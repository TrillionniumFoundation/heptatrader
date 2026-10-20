"""Read-only current100k dataset exporter. No order, network, or training API."""
from __future__ import annotations
import argparse
import hashlib
import json
import time
from decimal import Decimal
from pathlib import Path
from .common import EvidenceError,read,sha,digest,atomic,write_once

BEHAVIOR='EURUSD_ORIGINAL_ENTRY_CONF_0.62_USD100K_REFERENCE_NOTIONAL'

def validate_config(c):
    if c.get('automatic_trading') is not False or c.get('automatic_training') is not False:
        raise EvidenceError('READ_ONLY_CONSUMER_REQUIRED')
    if sha(c['period_path'])!=c['period_sha256']:
        raise EvidenceError('PERIOD_CHANGED')
    if read(c['period_path']).get('execution_domain')!='PAPER':
        raise EvidenceError('PAPER_ONLY')
    r=c['registration']
    if sha(r['manifest'])!=r['sha256']:
        raise EvidenceError('REGISTERED_PRODUCER_CHANGED')
    m=read(r['manifest'])
    if (m.get('behavior_profile')!=BEHAVIOR or m.get('instrument')!='EUR.USD'
        or m.get('execution_domain')!='PAPER' or m.get('period_sha256')!=c['period_sha256']
        or m.get('fx_churn_profile_sha256')):
        raise EvidenceError('CURRENT100K_SCOPE_REQUIRED')
    for rel,wanted in c.get('runtime_sha256',{}).items():
        p=Path(rel)
        if p.is_absolute() or '..' in p.parts or sha(Path(c['runtime_root'])/p)!=wanted:
            raise EvidenceError('RUNTIME_CHANGED:'+rel)
    return m

def to_row(reward,item,binding,manifest_sha,period_sha,*,source_index=None):
    from laya_cell.financial_value_data import exact_request
    from paired_net_100k_preflight.core import check_request,feature_text
    if digest({k:v for k,v in reward.items() if k!='reward_id'})!=reward.get('reward_id'):
        raise EvidenceError('REWARD_HASH')
    if any(reward.get(k) is not True for k in ('learning_eligible','exact_input_bound','fees_complete','settlement_complete')):
        raise EvidenceError('INCOMPLETE_REWARD')
    if (reward.get('instrument')!='EUR.USD' or reward.get('currency')!='USD'
        or reward.get('policy_gradient_allowed') is not False or reward.get('laya_on_policy') is not False
        or reward.get('learning_exclusion_reasons')
        or reward['input_attribution']['producer_manifest_sha256']!=manifest_sha):
        raise EvidenceError('REWARD_SCOPE')
    request=check_request(item,manifest_sha,period_sha)
    if exact_request(reward,source_index=source_index)!=request or request.get('model_id')!=reward.get('behavior_model_id'):
        raise EvidenceError('ORIGINAL_INPUT_CONFLICT')
    cmd=binding['command'];side=cmd.get('side')
    if (cmd.get('command_id')!=reward.get('open_command_id') or cmd.get('kind')!='OPEN'
        or cmd.get('instrument')!='EUR.USD' or cmd.get('position_before_send')!=0
        or cmd.get('quantity_mode')!='BASE_NOTIONAL_TARGET'
        or Decimal(str(cmd.get('target_notional_usd',0)))!=Decimal(100000)
        or side not in ('BUY','SELL') or binding.get('request_id')!=request['request_id']
        or binding.get('request_sha256')!=reward['input_id'] or binding.get('decision_sha256')!=reward['forecast_id']):
        raise EvidenceError('CURRENT100K_ORIGINAL_BINDING_REQUIRED')
    position=cash=fees=opened=Decimal(0);seen=set()
    for e in reward['executions']:
        if e['execution_id'] in seen or e.get('commission_currency')!='USD' or e.get('commission') is None:
            raise EvidenceError('DUPLICATE_OR_MISSING_FEE')
        seen.add(e['execution_id'])
        q,p,f=(Decimal(str(e[k])) for k in ('quantity','price','commission'))
        if not all(v.is_finite() for v in (q,p,f)) or q<=0 or p<=0 or e['side'] not in ('BUY','SELL'):
            raise EvidenceError('INVALID_EXECUTION_NUMERIC')
        signed=q if e['side']=='BUY' else -q
        if position and position*(position+signed)<0:
            raise EvidenceError('CROSS_ZERO_EXECUTION')
        position+=signed;cash-=signed*p;fees+=f
        if e['command_id']==cmd['command_id']:
            if e['side']!=side:raise EvidenceError('OPEN_SIDE_CONFLICT')
            opened+=q
    if (position!=0 or opened!=Decimal(str(cmd['order_quantity']))
        or cash!=Decimal(reward['realized_gross_usd']) or fees!=Decimal(reward['actual_commissions_usd'])
        or cash-fees!=Decimal(reward['realized_net_usd'])):
        raise EvidenceError('ECONOMIC_RECOMPUTATION_FAILED')
    text=feature_text(request,side)
    row={'schema':'heptatrader.current100k-value-row.v1','period_sha256':period_sha,
        'behavior_scope':{'behavior_profile':BEHAVIOR,'producer_manifest_sha256':manifest_sha,
            'model_id':request['model_id'],'reference_notional_usd':100000,'instrument':'EUR.USD'},
        'input_id':reward['input_id'],'request_id':request['request_id'],
        'input_started_at_ms':request['started_at_ms'],'open_command_id':cmd['command_id'],
        'feature_text':text,'feature_text_sha256':hashlib.sha256(text.encode()).hexdigest(),
        'action':side,'net_usd':str(cash-fees),'actual_commissions_usd':str(fees),
        'reward_id':reward['reward_id'],'episode_id':reward['episode_id'],
        'source_request':item,'source_binding':binding,'reward':reward,
        'label_class':'REAL_EXECUTION_BEHAVIOR_BOUND_VALUE_NOT_ON_POLICY',
        'automatic_training':False,'automatic_selection':False}
    row['row_id']=digest(row)
    return row
