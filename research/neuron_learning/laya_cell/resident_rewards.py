"""Observed execution rewards are distinct from attributed model-learning credit.

Each financial outcome is computed from the existing native-economics replay,
not NAV, unrealized marks, confidence, a synthetic fee model or guessed input.
Missing original input never hides a real economic loss, nor grants model credit.
"""
from __future__ import annotations
from decimal import Decimal
from pathlib import Path
from .integrity import ContractError,digest,read_json,sha_file


def financial_episodes(report,bindings,period_id,instrument):
 position=Decimal(0);cash=Decimal(0);fees=Decimal(0);cycle=[];seen=set();out=[];blocked=[]
 pending=set(report['settlement_pending_command_ids']);fee_missing=False
 for execution in report['executions']:
  eid=execution['execution_id']
  if eid in seen:raise ContractError('DUPLICATE_REWARD_EXECUTION')
  seen.add(eid);qty=Decimal(execution['quantity']);price=Decimal(execution['price'])
  if not qty.is_finite() or not price.is_finite() or qty<=0 or price<=0:raise ContractError('REWARD_NUMERIC')
  side=execution['side']
  if side not in ('BUY','SELL'):raise ContractError('REWARD_SIDE')
  signed=qty if side=='BUY' else -qty
  if position and position*(position+signed)<0:raise ContractError('CROSS_ZERO_EXECUTION_NEEDS_EXACT_LOT_SPLIT')
  fee=execution.get('commission')
  if fee is None or execution.get('commission_currency')!='USD':fee_missing=True
  else:
   value=Decimal(fee)
   if not value.is_finite():raise ContractError('REWARD_NONFINITE_FEE')
   fees+=value
  position+=signed;cash-=signed*price;cycle.append(execution)
  if position:continue
  commands=list(dict.fromkeys(x['command_id'] for x in cycle));opening=commands[0];binding=bindings.get(opening)
  reasons=[]
  if fee_missing:reasons.append('MISSING_ACTUAL_EXECUTION_COMMISSION')
  if len(commands)<2:reasons.append('NO_DISTINCT_CLOSE_COMMAND')
  unresolved=sorted(set(commands)&pending)
  if unresolved:reasons.append('ORIGINAL_COMMAND_SETTLEMENT_UNCONFIRMED')
  if binding is None:reasons.append('ORIGINAL_OPEN_COMMAND_INPUT_UNBOUND')
  payload={'schema':'heptatrader.observed-execution-reward.v2','period_id':period_id,'instrument':instrument,'currency':'USD',
   'open_command_id':opening,'command_ids':commands,'execution_ids':[x['execution_id'] for x in cycle],
   'executions':cycle,'flat_to_flat_executed_quantity':True,'realized_gross_usd':str(cash),
   'actual_commissions_usd':None if fee_missing else str(fees),'realized_net_usd':None if fee_missing else str(cash-fees),
   'fees_complete':not fee_missing,'settlement_complete':not unresolved,'unsettled_commands':unresolved,
   'input_id':binding['input_id'] if binding else None,'forecast_id':binding['forecast_id'] if binding else None,
   'behavior_model_id':binding['model_id'] if binding else None,'exact_input_bound':binding is not None,
   'learning_eligible':not reasons,'learning_exclusion_reasons':reasons,
   'training_class':'BEHAVIOR_BOUND_VALUE_TARGET_ONLY' if not reasons else 'ACCOUNTING_OBSERVATION_NO_MODEL_CREDIT',
   'reward_scope':'CLOSED_EXECUTED_QUANTITY_AFTER_REPORTED_USD_COMMISSIONS',
   'excluded_costs':['financing','tax','unreported_non_execution_charges'],
   'laya_on_policy':False,'policy_gradient_allowed':False,'effect_authority':False}
  payload['episode_id']=digest({'period':period_id,'instrument':instrument,'commands':commands})
  payload['reward_id']=digest(payload);out.append(payload)
  cash=Decimal(0);fees=Decimal(0);cycle=[];fee_missing=False
 if cycle:blocked.append({'reason':'OPEN_RESIDUAL_POSITION','instrument':instrument,'quantity':str(position),'execution_ids':[x['execution_id'] for x in cycle]})
 return out,blocked


def assemble(report,bindings,period_id,instrument='EUR.USD'):
 """Compatibility API: returns ONLY fully bound, settled learning rewards."""
 if report['fees_complete'] is not True or report['settlement_pending_command_ids']:
  return [],[{'reason':'INCOMPLETE_FEES_OR_UNSETTLED_COMMANDS'}]
 for e in report['executions']:
  if e.get('commission') is None or e.get('commission_currency')!='USD':raise ContractError('REWARD_MISSING_ACTUAL_FEE')
 out,blocked=financial_episodes(report,bindings,period_id,instrument)
 return [x for x in out if x['learning_eligible']],blocked+[
  {'reason':r,'instrument':instrument,'episode_id':x['episode_id']} for x in out for r in x['learning_exclusion_reasons']]


def source_bindings(config,instrument,now_ms):
 from data import exports
 directory=config.get('export_directories',{}).get(instrument)
 if directory is None:
  directory=config.get('export_directory') if instrument=='EUR.USD' else ''
 if not directory or not Path(directory).exists():return {},[{'reason':'NO_EXACT_INPUT_EXPORT','instrument':instrument}]
 records,_,excluded=exports(directory,config['period_sha256'],now_ms)
 inputs={};forecasts={};bindings={}
 for r in records:
  d=r['data']
  if r['kind']=='MODEL_INPUT' and d.get('input_id')==digest(d.get('input')):
   if d['input'].get('instrument')==instrument:inputs[d['input_id']]=d['input']
  if r['kind']=='FORECAST' and d.get('candidate_id')==digest(d.get('forecast')):
   if d['forecast'].get('instrument')==instrument:forecasts[d['candidate_id']]=d
 for r in records:
  if r['kind']!='DECISION':continue
  d=r['data'];p=d.get('pending') or {};cmd=p.get('command_id');f=forecasts.get(d.get('candidate_id'))
  if not cmd or p.get('kind')!='OPEN' or not f or f['input_id'] not in inputs:continue
  b={'input_id':f['input_id'],'forecast_id':f['candidate_id'],'model_id':f['forecast']['model_id']}
  if cmd in bindings and bindings[cmd]!=b:raise ContractError('COMMAND_FORECAST_CONFLICT')
  bindings[cmd]=b
 return bindings,excluded


def current(config,now_ms):
 from data import prefix,strict
 import hepta_pnl_accounting as accounting
 result={'schema':'heptatrader.laya-reward-status.v2','at_ms':now_ms,'execution_rewards':[],'rewards':[],
  'blocked':[],'components':{},'pnl_reward_ready':False,'financial_outcomes_available':False,
  'action_policy_gradient_used':False,'broker_orders_sent':0}
 try:
  if sha_file(Path(config['period_path']))!=config['period_sha256']:raise ContractError('REWARD_PERIOD_CHANGED')
  period=read_json(Path(config['period_path']));accounting.validate_period(period)
  raw,source=prefix(config['oms_path']);result['source_prefix']=source
  if source['trailing_uncommitted_bytes']:raise ContractError('INCOMPLETE_OMS_TAIL')
  events=[strict(x) for x in raw.splitlines()]
  instruments=config.get('reward_instruments',['EUR.USD','BTC.USD'])
  if not instruments or any(i not in ('EUR.USD','BTC.USD') for i in instruments):raise ContractError('REWARD_INSTRUMENT_SCOPE')
  for instrument in instruments:
   try:
    if Decimal(str(period['instruments'][instrument]['opening_quantity']))!=0:raise ContractError('NONZERO_OPENING_POSITION_UNALLOCATED')
    report=accounting.replay(events,period,instrument,now_ms)
    bindings,exclusions=source_bindings(config,instrument,now_ms)
    observed,blocked=financial_episodes(report,bindings,period['period_id'],instrument)
    eligible=[r for r in observed if r['learning_eligible']]
    result['execution_rewards'].extend(observed);result['rewards'].extend(eligible)
    result['blocked'].extend(blocked+exclusions)
    complete=[r for r in observed if r['fees_complete']]
    result['components'][instrument]={'ledger_complete':True,'fees_complete':report['fees_complete'],
     'execution_count':report['execution_count'],'command_count':report['command_count'],'closed_execution_episodes':len(observed),
     'fee_complete_episodes':len(complete),'settled_episodes':sum(r['settlement_complete'] for r in observed),
     'attributed_learning_rewards':len(eligible),'unbound_input_episodes':sum(not r['exact_input_bound'] for r in observed),
     'closed_gross_usd':str(sum((Decimal(r['realized_gross_usd']) for r in observed),Decimal(0))),
     'closed_reported_fees_usd':str(sum((Decimal(r['actual_commissions_usd']) for r in complete),Decimal(0))),
     'closed_net_usd':str(sum((Decimal(r['realized_net_usd']) for r in complete),Decimal(0))) if len(complete)==len(observed) else None,
     'settlement_pending_command_ids':report['settlement_pending_command_ids'],'blocked':blocked+exclusions}
   except (ContractError,accounting.EvidenceError,ValueError,KeyError,TypeError,OSError) as exc:
    result['components'][instrument]={'ledger_complete':False,'reason':str(exc)}
  result['fees_complete']=all(x.get('fees_complete') for x in result['components'].values())
  result['pnl_reward_ready']=bool(result['rewards']);result['financial_outcomes_available']=bool(result['execution_rewards'])
  result['reason']='ATTRIBUTED_SETTLED_REWARDS_READY' if result['rewards'] else ('OBSERVED_ECONOMICS_AWAITING_INPUT_OR_SETTLEMENT' if result['execution_rewards'] else 'NO_COMPLETE_EXECUTION_EPISODE')
 except (ContractError,accounting.EvidenceError,ValueError,KeyError,TypeError,OSError) as exc:
  result['reason']=str(exc);result['fees_complete']=False
 return result
