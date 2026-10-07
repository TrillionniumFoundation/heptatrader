"""Confirmed closed PAPER execution rewards, with exact original-command joins.

Uses the existing accounting replay for fills/commission/epoch identity. A remote
strategy reward is NOT an on-policy Laya reward, nor an action-head label. Missing
fees, input joins, incomplete round trips and account NAV never become zero reward.
"""
from __future__ import annotations
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
from .integrity import ContractError,digest,read_json,sha_file


def assemble(report,bindings,period_id,instrument='EUR.USD'):
 if report['fees_complete'] is not True or report['settlement_pending_command_ids']:
  return [],[{'reason':'INCOMPLETE_FEES_OR_UNSETTLED_COMMANDS'}]
 position=Decimal(0);cash=Decimal(0);fees=Decimal(0);cycle=[];rewards=[];blocked=[];seen=set()
 for execution in report['executions']:
  eid=execution['execution_id']
  if eid in seen:raise ContractError('DUPLICATE_REWARD_EXECUTION')
  seen.add(eid)
  if execution.get('commission') is None or execution.get('commission_currency')!='USD':
   raise ContractError('REWARD_MISSING_ACTUAL_FEE')
  qty=Decimal(execution['quantity']);price=Decimal(execution['price']);fee=Decimal(execution['commission'])
  if not qty.is_finite() or not price.is_finite() or not fee.is_finite() or qty<=0 or price<=0:
   raise ContractError('REWARD_NUMERIC')
  side=execution['side']
  if side not in ('BUY','SELL'):raise ContractError('REWARD_SIDE')
  signed=qty if side=='BUY' else -qty
  if position and position*(position+signed)<0:
   raise ContractError('CROSS_ZERO_EXECUTION_NEEDS_EXACT_LOT_SPLIT')
  cash-=signed*price;fees+=fee;position+=signed;cycle.append(execution)
  if position!=0:continue
  commands=list(dict.fromkeys(x['command_id'] for x in cycle))
  opening=commands[0];binding=bindings.get(opening)
  if len(commands)<2 or binding is None:
   blocked.append({'command_ids':commands,'execution_ids':[x['execution_id'] for x in cycle],
                   'reason':'ORIGINAL_OPEN_COMMAND_INPUT_UNBOUND'})
  else:
   payload={'schema':'heptatrader.confirmed-paper-roundtrip-reward.v1','period_id':period_id,
      'instrument':instrument,'currency':'USD','open_command_id':opening,'command_ids':commands,
      'execution_ids':[x['execution_id'] for x in cycle],'executions':cycle,
      'input_id':binding['input_id'],'forecast_id':binding['forecast_id'],'behavior_model_id':binding['model_id'],
      'realized_gross_usd':str(cash),'actual_commissions_usd':str(fees),'realized_net_usd':str(cash-fees),
      'reward_scope':'CLOSED_STRATEGY_EXECUTION_AFTER_ACTUAL_COMMISSIONS',
      'excluded_costs':['financing','tax','unreported_non_execution_charges'],
      'training_class':'BEHAVIOR_BOUND_VALUE_TARGET_ONLY','laya_on_policy':False,
      'policy_gradient_allowed':False,'effect_authority':False}
   payload['reward_id']=digest(payload);rewards.append(payload)
  cash=Decimal(0);fees=Decimal(0);cycle=[]
 if cycle:blocked.append({'reason':'OPEN_RESIDUAL_POSITION','quantity':str(position),'execution_ids':[x['execution_id'] for x in cycle]})
 return rewards,blocked


def current(config,now_ms):
 from data import prefix,strict,exports
 import hepta_pnl_accounting as accounting
 result={'schema':'heptatrader.laya-reward-status.v1','at_ms':now_ms,'rewards':[],'blocked':[],
         'pnl_reward_ready':False,'action_policy_gradient_used':False,'broker_orders_sent':0}
 try:
  if sha_file(Path(config['period_path']))!=config['period_sha256']:raise ContractError('REWARD_PERIOD_CHANGED')
  period=read_json(Path(config['period_path']));accounting.validate_period(period)
  if Decimal(str(period['instruments']['EUR.USD']['opening_quantity']))!=0:raise ContractError('NONZERO_OPENING_POSITION_UNALLOCATED')
  raw,source=prefix(config['oms_path']);result['source_prefix']=source
  if source['trailing_uncommitted_bytes']:raise ContractError('INCOMPLETE_OMS_TAIL')
  events=[strict(x) for x in raw.splitlines()]
  report=accounting.replay(events,period,'EUR.USD',now_ms)
  result.update(fees_complete=report['fees_complete'],execution_count=report['execution_count'],command_count=report['command_count'])
  records,_,exclusions=exports(config['export_directory'],config['period_sha256'],now_ms)
  forecasts={};inputs={};bindings={}
  for record in records:
   d=record['data']
   if record['kind']=='MODEL_INPUT' and d.get('input_id')==digest(d.get('input')):inputs[d['input_id']]=d['input']
   if record['kind']=='FORECAST' and d.get('candidate_id')==digest(d.get('forecast')):forecasts[d['candidate_id']]=d
  for record in records:
   if record['kind']!='DECISION':continue
   d=record['data'];pending=d.get('pending') or {};command=pending.get('command_id');f=forecasts.get(d.get('candidate_id'))
   if not command or pending.get('kind')!='OPEN' or not f or f['input_id'] not in inputs:continue
   binding={'input_id':f['input_id'],'forecast_id':f['candidate_id'],'model_id':f['forecast']['model_id']}
   if command in bindings and bindings[command]!=binding:raise ContractError('COMMAND_FORECAST_CONFLICT')
   bindings[command]=binding
  result['rewards'],result['blocked']=assemble(report,bindings,config['period_sha256'])
  result['blocked'].extend(exclusions)
  result['pnl_reward_ready']=bool(result['rewards'])
  result['reason']='CONFIRMED_CLOSED_REWARDS' if result['rewards'] else 'NO_COMPLETE_BOUND_ROUNDTRIP_REWARD'
 except (ContractError,accounting.EvidenceError,ValueError,KeyError,TypeError,OSError) as exc:
  result['reason']=str(exc);result['fees_complete']=False
 return result
