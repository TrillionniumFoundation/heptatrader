#!/usr/bin/env python3
"""BTC-only resident entry discipline over the unchanged pinned PAPER base driver.

0.62 and all base exits are retained. Only new BUYs require a bounded forecast,
fee-aware edge and anti-churn admission. FX has no path through this executable.
"""
from __future__ import annotations
import argparse,copy,hashlib,json,os,stat,sys
from pathlib import Path
from hepta_verified_loader import SourceHashMismatch,load_verified_module
from hepta_btc_entry_policy import PROFILE,HORIZONS,PolicyError,canonical,digest,number,validate_forecast,validate_control,reserve,evaluate
BASE_SHA256='73cfe8f036acf80dde8b42bd235c40f2cd79f2a92fc896b6d068216a60187d42'
SCHEMA={'type':'object','required':['action','confidence','rationale','expected_move_bps','horizon_sec'],
 'additionalProperties':False,'properties':{'action':{'enum':['BUY','SELL','HOLD']},
 'confidence':{'type':'number','minimum':0,'maximum':1},'rationale':{'type':'string','minLength':1,'maxLength':2000},
 'expected_move_bps':{'type':'number','minimum':-1000,'maximum':1000},'horizon_sec':{'enum':list(HORIZONS)}}}

def load_base(path):
 path=Path(path)
 if path.is_symlink():raise PolicyError('BTC_BASE_DRIVER_IDENTITY')
 try:return load_verified_module('hepta_btc_pinned_base',path,BASE_SHA256)
 except SourceHashMismatch as exc:raise PolicyError('BTC_BASE_DRIVER_IDENTITY') from exc

def load_profile(path):
 path=Path(path);st=path.lstat()
 if not stat.S_ISREG(st.st_mode) or st.st_uid!=os.geteuid() or st.st_mode&0o077 or st.st_size>65536:raise PolicyError('BTC_PROFILE_PERMISSIONS')
 c=json.loads(path.read_text())
 if c.get('profile')!=PROFILE or c.get('schema')!='heptatrader.btc-entry-runtime.v1':raise PolicyError('BTC_PROFILE_CHANGED')
 if not isinstance(c.get('period_sha256'),str) or len(c['period_sha256'])!=64:raise PolicyError('BTC_PROFILE_PERIOD')
 validate_control(c['initial_control'],int(__import__('time').time()*1000))
 return c

def attach(driver,config,*,period_check=None):
 original={name:getattr(driver,name) for name in ('model_decision','prepare_send','paper_cmd','atomic_json')}
 context={'state':None,'path':None}
 cli=str(Path.home()/'.local/bin/heptatrader')
 def check_period():
  if period_check is not None:period_check();return
  if (Path.home()/'.local/state/heptatrader/RESET-STOPPED.json').exists():raise PolicyError('BTC_OPERATOR_RESET_HOLD')
  p=Path.home()/'.config/heptatrader/pnl-period.json';raw=p.read_bytes();period=json.loads(raw)
  if hashlib.sha256(raw).hexdigest()!=config['period_sha256'] or period.get('execution_domain')!='PAPER':raise PolicyError('BTC_ACCOUNTING_PERIOD_CHANGED')
 def control(state):
  if 'btc_entry_control' not in state:state['btc_entry_control']=copy.deepcopy(config['initial_control'])
  return validate_control(state['btc_entry_control'],driver.now_ms())
 def atomic_json(path,state):
  if driver.INSTRUMENT=='BTC.USD':
   try:
    c=control(state);p=state.get('pending') or {}
    if p.get('kind')=='OPEN' and p.get('send_state')=='PREPARED' and context['state'] is state:
     reserve(c,p['command_id'],int(p['created_at_ms']))
     p['btc_entry_profile_sha256']=digest(PROFILE)
     p['btc_entry_forecast']=copy.deepcopy(state.get('last_decision'))
    r=state.get('last_reconciliation') or {}
    # Only a newly completed full CLOSE changes cooldown; partial exits do not.
    if (state.get('pending') is None and number(state.get('position',0))==0 and r.get('kind')=='CLOSE'
        and type(r.get('reconciled_at_ms'))is int and 0<r['reconciled_at_ms']<=driver.now_ms()):
     c['last_flat_at_ms']=max(c['last_flat_at_ms'],r['reconciled_at_ms'])
   except (PolicyError,KeyError,TypeError,ValueError) as exc:
    if context['state'] is state and (state.get('pending') or {}).get('kind')=='OPEN':raise
    # Broken opening-only bookkeeping must not obstruct existing risk exits.
    state['btc_entry_control_error']=type(exc).__name__
  return original['atomic_json'](path,state)
 def model_decision(openclaw,state_dir,history,position):
  if driver.INSTRUMENT!='BTC.USD' or number(position)!=0:
   # While exposed, use the exact legacy position-aware prompt and decoder.
   # Missing opening-edge forecasts must never suppress a model/risk exit.
   return original['model_decision'](openclaw,state_dir,history,position)
  compact=[{'t':x['observed_at_ms'],'bid':x['bid'],'ask':x['ask'],'mid':x['mid']} for x in history[-24:]]
  if not compact:raise driver.DriverError('BTC_NO_MODEL_HISTORY')
  prompt=('Return exactly one JSON object matching '+canonical(SCHEMA)+'. This is a PAPER BTC/USD forecast, not permission to trade. '
    'BUY is bullish, SELL bearish, HOLD abstention. Estimate expected future MID move in basis points relative to the final input MID, '
    'at the stated horizon. Do not infer move size or win probability from confidence. Do not inflate a forecast to cause a trade. '
    'Do not call tools. When there is no coherent directional evidence, prefer HOLD. Input='+canonical(compact))
  response=driver.run([openclaw,'infer','model','run','--local','--model',driver.openclaw_model_ref(driver.MODEL),
                       '--thinking','low','--prompt',prompt,'--json'],150)
  if response.returncode:raise driver.DriverError('BTC_MODEL_UNAVAILABLE')
  try:
   envelope=driver.strict_json(response.stdout);outputs=envelope.get('outputs')
   if envelope.get('ok') is not True or not isinstance(outputs,list) or len(outputs)!=1:raise PolicyError('BTC_MODEL_ENVELOPE')
   f=driver.strict_json(outputs[0]['text'])
   if set(f)!=set(SCHEMA['required']):raise PolicyError('BTC_MODEL_FIELDS')
   reference=(number(compact[-1]['bid'])+number(compact[-1]['ask']))/2
   f.update(issued_at_ms=compact[-1]['t'],available_at_ms=driver.now_ms(),reference_mid=str(reference),
            input_sha256=digest(compact),model_id=driver.MODEL,forecast_schema='heptatrader.btc-return-forecast.v1')
   validate_forecast(f)
   return f
  except (PolicyError,ValueError,KeyError,TypeError,IndexError) as exc:raise driver.DriverError('BTC_FORECAST_INVALID:'+type(exc).__name__) from exc
 def entry_check(state,ignore=None,forecast=None):
  check_period()
  fresh=driver.native(cli,'market.get_quote','instrument=BTC.USD')
  health=driver.native(cli,'system.get_health')
  owner=health.get('execution_authoritative_health') or {}
  if (health.get('execution_mode')!='PAPER' or owner.get('source')!='IB' or owner.get('authoritative') is not True
      or owner.get('mutation_ready') is not True or owner.get('owner_fenced') is not False
      or owner.get('owner_recovery_only') is not False or owner.get('startup_recovery_read_only') is True):
   raise PolicyError('BTC_PAPER_MUTATION_NOT_READY')
  result=evaluate(state.get('last_decision') if forecast is None else forecast,fresh,control(state),driver.now_ms(),ignore_command=ignore)
  state['btc_last_entry_check']=result
  return result,fresh
 def prepare_send(state,path,binary,socket,token,outbox,kind,quote=None,side=None,**kwargs):
  if driver.INSTRUMENT!='BTC.USD' or kind!='OPEN':
   return original['prepare_send'](state,path,binary,socket,token,outbox,kind,quote,side,**kwargs)
  if kwargs:raise driver.DriverError('BTC_ECONOMICS_NO_UNQUALIFIED_CANARY_OVERRIDE')
  if state.get('pending') is not None:raise driver.DriverError('original command pending; replacement preparation forbidden')
  if number(state.get('position',0))!=0 or side!='BUY' or state.get('halt_new_entries'):
   raise driver.DriverError('BTC_NOT_FLAT_UNHALTED_LONG_ENTRY')
  result,fresh=entry_check(state)
  original['atomic_json'](path,state)
  if not result['allowed']:raise driver.DriverError('BTC_ENTRY_REJECTED:'+','.join(result['reasons']))
  context.update(state=state,path=path)
  try:return original['prepare_send'](state,path,binary,socket,token,outbox,kind,fresh,side)
  finally:context.update(state=None,path=None)
 def paper_cmd(binary,socket,token,outbox,operation,extra):
  state=context['state'];pending=(state.get('pending') or {}) if state is not None else {}
  if operation=='submit' and pending.get('kind')=='OPEN':
   try:
    if extra!=['--command-id',pending['command_id']]:raise PolicyError('BTC_ORIGINAL_SUBMIT_ID_CHANGED')
    captured=pending.get('btc_entry_forecast')
    if (pending.get('btc_entry_profile_sha256')!=digest(PROFILE) or not captured
        or digest(captured)!=digest(state.get('last_decision'))):raise PolicyError('BTC_PREPARED_FORECAST_CHANGED')
    if (pending.get('instrument')!='BTC.USD' or pending.get('side')!='BUY'
        or pending.get('quantity_mode')!='CASH' or number(pending.get('cash_quantity'))!=number(PROFILE['max_cash_usd'])
        or state.get('halt_new_entries') or number(state.get('position',0))!=0):raise PolicyError('BTC_PREPARED_STATE_CHANGED')
    result,_=entry_check(state,pending['command_id'],captured)
    state['btc_last_final_entry_check']=copy.deepcopy(result);original['atomic_json'](context['path'],state)
    if not result['allowed']:raise PolicyError('BTC_FINAL_ECONOMICS_REJECTED')
   except Exception as exc:
    # The actual submit was not called. Let unchanged base persist its definite
    # non-send and reconcile unchanged Broker state; never delete pending here.
    return {'ok':False,'transport_ok':True,'application_status':'error',
     'application_reason':'BTC_FINAL_NON_SEND:'+type(exc).__name__,
     'result':{'status':'error','reason_code':'BTC_FINAL_NON_SEND','order_id':-1}}
  return original['paper_cmd'](binary,socket,token,outbox,operation,extra)
 driver.model_decision=model_decision;driver.prepare_send=prepare_send;driver.atomic_json=atomic_json;driver.paper_cmd=paper_cmd
 def close():
  for name,value in original.items():setattr(driver,name,value)
 return close

def main():
 p=argparse.ArgumentParser();p.add_argument('--base-driver',type=Path,required=True);p.add_argument('--profile',type=Path)
 p.add_argument('--once',action='store_true');p.add_argument('--reconcile-only',action='store_true');p.add_argument('--recover-owner',action='store_true');a=p.parse_args()
 control_only=a.reconcile_only or a.recover_owner
 if not control_only and a.profile is None:p.error('--profile is required for entry-capable modes')
 os.umask(0o077);m=load_base(a.base_driver);close=None
 if not control_only:close=attach(m,load_profile(a.profile))
 # No LIVE selector, cash sizing override or canary permission is exposed.
 sys.argv=[str(a.base_driver),'--instrument','BTC.USD']+(['--once'] if a.once else [])+(['--reconcile-only'] if a.reconcile_only else [])+(['--recover-owner'] if a.recover_owner else [])
 try:return m.main()
 finally:
  if close is not None:close()
if __name__=='__main__':raise SystemExit(main())
