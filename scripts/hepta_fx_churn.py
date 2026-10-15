"""PAPER FX opening hysteresis only. Original risk and model exits are unchanged.

Confirmation means two distinct completed model decisions, not calibrated win
probability. Original command reservations survive restart. No monetary forecast
is invented from confidence and no 'hold until profitable' rule exists.
"""
from __future__ import annotations
import copy,hashlib,json,math,threading
from pathlib import Path

PROFILE={'schema':'heptatrader.fx-opening-hysteresis.v1','instrument':'EUR.USD',
 'confidence_threshold':.62,'required_confirmations':2,'confirmation_min_spacing_ms':60000,
 'confirmation_max_span_ms':300000,'minimum_open_spacing_ms':900000,
 'after_flat_cooldown_ms':600000,'rolling_hour_max_open_attempts':2,'quantity':25000,
 'exit_rule_changed':False,'cost_forecast_required':False}
class ChurnError(RuntimeError):pass

def digest(v):return hashlib.sha256(json.dumps(v,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
def valid_direction(d):
 return isinstance(d,dict) and d.get('action') in ('BUY','SELL') and type(d.get('confidence')) in (int,float) and math.isfinite(d['confidence']) and .62<=d['confidence']<=1

def fresh_control(seed):
 return {**copy.deepcopy(seed),'schema':PROFILE['schema'],'profile_sha256':digest(PROFILE),
  'signal_side':None,'signal_first_ms':0,'signal_latest_ms':0,'confirmations':0,
  'last_seen_decisions':-1,'last_seen_decision_ms':0}

def observe(control,state,now):
 c=copy.deepcopy(control);seq=state.get('decisions',0);at=state.get('last_decision_ms',0)
 if type(seq)is not int or type(at)is not int or type(now)is not int:raise ChurnError('FX_SIGNAL_CLOCK_TYPE')
 if seq<c['last_seen_decisions'] or at<c['last_seen_decision_ms'] or at>now:raise ChurnError('FX_SIGNAL_CLOCK_REGRESSION')
 if seq==c['last_seen_decisions']:return c
 c.update(last_seen_decisions=seq,last_seen_decision_ms=at)
 d=state.get('last_decision');flat=abs(float(state.get('position',0)))<=1e-9
 if not flat or not valid_direction(d) or at<=0 or at<c.get('activation_ms',0) or now-at>60000:
  c.update(signal_side=None,confirmations=0,signal_first_ms=0,signal_latest_ms=0);return c
 side=d['action'];delta=at-c['signal_latest_ms']
 if side!=c['signal_side'] or at-c['signal_first_ms']>PROFILE['confirmation_max_span_ms']:
  c.update(signal_side=side,signal_first_ms=at,signal_latest_ms=at,confirmations=1)
 elif delta>=PROFILE['confirmation_min_spacing_ms']:
  c['confirmations']+=1;c['signal_latest_ms']=at
 return c

def admission(control,decision,now):
 if control.get('profile_sha256')!=digest(PROFILE):raise ChurnError('FX_PROFILE_CHANGED')
 if type(now)is not int or now<control['last_exit_ms']:raise ChurnError('FX_CONTROL_CLOCK_REGRESSION')
 reasons=[]
 if not valid_direction(decision):reasons.append('LEGACY_DIRECTION_CONFIDENCE')
 if control['signal_side']!=(decision or {}).get('action') or control['confirmations']<2:reasons.append('WAIT_SECOND_DIRECTIONAL_CONFIRMATION')
 if not 0<=now-control['signal_latest_ms']<=60000:reasons.append('FRESH_COMPLETED_DECISION_REQUIRED')
 attempts=control['open_attempts']
 if any(type(x.get('at_ms'))is not int or x['at_ms']>now for x in attempts):raise ChurnError('FX_ATTEMPT_CLOCK')
 if attempts and now-max(x['at_ms'] for x in attempts)<PROFILE['minimum_open_spacing_ms']:reasons.append('MINIMUM_OPEN_SPACING')
 if now-control['last_exit_ms']<PROFILE['after_flat_cooldown_ms']:reasons.append('AFTER_FLAT_COOLDOWN')
 if sum(now-3600000<x['at_ms']<=now for x in attempts)>=PROFILE['rolling_hour_max_open_attempts']:reasons.append('ROLLING_HOUR_OPEN_LIMIT')
 return {'allowed':not reasons,'reasons':reasons,'checked_at_ms':now,'profile_sha256':digest(PROFILE),
  'confirmation_count':control['confirmations'],'commission_budget_note':'UNCHANGED_ACTUAL_FEES_NOT_CONFIDENCE_DERIVED_EDGE'}

def resume_activation_control(existing, seed, period):
    """A new observed OMS seed conservatively replaces old activation signals.

    The prior controller may contain real PREPARED-but-rejected attempts not
    represented by Broker sends; carry them forward without replaying commands.
    Restarting the identical activation preserves its durable counters.
    """
    if existing is None:
        return fresh_control(seed)
    if (not isinstance(existing, dict) or
            existing.get('period_sha256') != period or
            existing.get('profile_sha256') != digest(PROFILE)):
        raise ChurnError('FX_CONTROL_SCOPE')
    older = existing.get('activation_ms')
    newer = seed.get('activation_ms')
    if newer is None:
        return copy.deepcopy(existing)
    if type(older) is not int or type(newer) is not int or newer < older:
        raise ChurnError('FX_ACTIVATION_CLOCK_REGRESSION')
    if newer == older:
        return copy.deepcopy(existing)
    if (type(existing.get('last_exit_ms')) is not int or
            type(seed.get('last_exit_ms')) is not int or
            type(existing.get('open_attempts')) is not list or
            type(seed.get('open_attempts')) is not list):
        raise ChurnError('FX_PREVIOUS_CONTROL_INVALID')
    attempts = {}
    for row in existing['open_attempts'] + seed['open_attempts']:
        if (not isinstance(row, dict) or
                not isinstance(row.get('command_id'), str) or
                not row['command_id'] or type(row.get('at_ms')) is not int or
                row['at_ms'] > newer):
            raise ChurnError('FX_ATTEMPT_CLOCK')
        if row['at_ms'] >= newer - 86400000:
            key = row['command_id']
            # Later observed send/attempt must retain the more restrictive
            # rolling-window clock; an earlier PREPARED receipt cannot age it out.
            attempts[key] = max(row['at_ms'], attempts.get(key, row['at_ms']))
    updated = copy.deepcopy(seed)
    updated['open_attempts'] = [
        {'command_id': key, 'at_ms': value}
        for key, value in sorted(attempts.items(), key=lambda item: (item[1], item[0]))
    ]
    updated['last_exit_ms'] = max(existing['last_exit_ms'], seed['last_exit_ms'])
    return fresh_control(updated)


def attach(driver,configuration):
 if driver.INSTRUMENT!='EUR.USD' or configuration['profile']!=PROFILE:raise ChurnError('FX_ONLY_PROFILE')
 seed=configuration['seed'];period=configuration['period_sha256'];local=threading.local()
 original={n:getattr(driver,n) for n in ('atomic_json','prepare_send','inspect_pending')}
 def control(state):
  return resume_activation_control(state.get('fx_open_control'),seed,period)
 def atomic(path,state):
  # Only the OPEN callsite requires these extra fields. Exit persistence never
  # becomes unavailable merely because optional opening controls are malformed.
  try:
   c=observe(control(state),state,driver.now_ms())
   p=state.get('pending') or {}
   if p.get('kind')=='OPEN' and p.get('send_state')=='PREPARED':
    if not any(x['command_id']==p['command_id'] for x in c['open_attempts']):
     c['open_attempts'].append({'command_id':p['command_id'],'at_ms':p['created_at_ms']})
     c['open_attempts']=[x for x in c['open_attempts'] if x['at_ms']>=driver.now_ms()-86400000]
    c.update(confirmations=0,signal_side=None,signal_first_ms=0,signal_latest_ms=0)
   before=getattr(local,'settlement_pending',None)
   if (before and before.get('kind')=='CLOSE' and state.get('pending') is None
       and abs(float(state.get('position',0)))<=1e-9 and (state.get('last_reconciliation') or {}).get('command_id')==before['command_id']):
    c.update(last_exit_ms=driver.now_ms(),last_exit_command=before['command_id'],signal_side=None,
             confirmations=0,signal_first_ms=0,signal_latest_ms=0)
   state['fx_open_control']=c
  except Exception as exc:
   state['fx_open_control_error']=str(exc)[:160]
   if getattr(local,'opening',False):raise
  return original['atomic_json'](path,state)
 def prepare(state,path,*args,**kwargs):
  # Original positional arguments are binary/socket/token/outbox/kind/quote/side.
  kind=kwargs.get('kind',args[4] if len(args)>4 else None)
  if kind!='OPEN':return original['prepare_send'](state,path,*args,**kwargs)
  if state.get('pending') is not None:raise ChurnError('ORIGINAL_PENDING_NO_REPLACEMENT')
  if state.get('halt_new_entries') or state.get('owner_fence_recovery_required') or state.get('lease_uncertain') or state.get('risk_halt_reason') or abs(float(state.get('position',0)))>1e-9:
   raise ChurnError('FX_ENTRY_SAFETY_STATE')
  if hashlib.sha256(Path(configuration['period_path']).read_bytes()).hexdigest()!=period:raise ChurnError('FX_PERIOD_CHANGED')
  if (Path(configuration['period_path']).parent.parent.parent/'.local/state/heptatrader/RESET-STOPPED.json').exists():raise ChurnError('FX_RESET_HOLD')
  c=observe(control(state),state,driver.now_ms());state['fx_open_control']=c
  r=admission(c,state.get('last_decision'),driver.now_ms());state['fx_last_open_admission']=r
  original['atomic_json'](path,state)
  if not r['allowed']:raise ChurnError('FX_OPEN_DEFERRED:'+','.join(r['reasons']))
  local.opening=True
  try:return original['prepare_send'](state,path,*args,**kwargs)
  finally:local.opening=False
 def inspect(state,path,*args,**kwargs):
  local.settlement_pending=copy.deepcopy(state.get('pending'))
  try:return original['inspect_pending'](state,path,*args,**kwargs)
  finally:local.settlement_pending=None
 driver.atomic_json=atomic;driver.prepare_send=prepare;driver.inspect_pending=inspect
 def close():
  for n,f in original.items():setattr(driver,n,f)
 return close
