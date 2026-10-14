"""Independent arithmetic on an existing derived Value export, never raw OMS I/O."""
from __future__ import annotations
import argparse, hashlib, json, math, os, time
from decimal import Decimal, localcontext
from pathlib import Path


def canonical(x): return json.dumps(x,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def digest(x): return hashlib.sha256(canonical(x)).hexdigest()
def require(ok, reason):
    if not ok: raise ValueError(reason)

def read(path):
    p=Path(path);require(not p.is_symlink() and p.is_file() and p.stat().st_size<=16*1024**2,'FILE_SCOPE')
    def unique(items):
        out={}
        for k,v in items:
            require(k not in out,'DUPLICATE_JSON_KEY');out[k]=v
        return out
    b=p.read_bytes();x=json.loads(b,object_pairs_hook=unique,parse_constant=lambda _:(_ for _ in ()).throw(ValueError('NONFINITE_JSON')))
    return x, hashlib.sha256(b).hexdigest()

def number(x):
    require(not isinstance(x,bool) and isinstance(x,(str,int,float,Decimal)),'NUMBER_TYPE')
    d=Decimal(str(x));require(d.is_finite(),'NONFINITE_AMOUNT');return d

def verify_row(row, period, producer):
    require(row.get('row_id')==digest({k:v for k,v in row.items() if k!='row_id'}),'ROW_HASH')
    require(row.get('period_sha256')==period and row.get('automatic_training') is False and row.get('automatic_selection') is False,'ROW_SCOPE')
    require(row.get('schema')=='heptatrader.current100k-value-row.v1' and row.get('label_class')=='REAL_EXECUTION_BEHAVIOR_BOUND_VALUE_NOT_ON_POLICY','ROW_SCHEMA')
    require(row['behavior_scope'].get('reference_notional_usd')==100000 and row['behavior_scope'].get('instrument')=='EUR.USD','BEHAVIOR_SCOPE')
    reward=row['reward'];source=row['source_request'];request=source['request'];binding=row['source_binding'];cmd=binding['command']
    require(reward.get('reward_id')==digest({k:v for k,v in reward.items() if k!='reward_id'}),'REWARD_HASH')
    require(all(reward.get(k) is True for k in ('learning_eligible','exact_input_bound','fees_complete','settlement_complete','flat_to_flat_executed_quantity')),'INCOMPLETE_REWARD')
    require(reward.get('policy_gradient_allowed') is False and reward.get('laya_on_policy') is False and reward.get('effect_authority') is False and not reward.get('learning_exclusion_reasons') and not reward.get('unsettled_commands'),'NO_MODEL_AUTHORITY')
    require(reward['instrument']=='EUR.USD' and reward['currency']=='USD','REWARD_INSTRUMENT')
    require(request['period_sha256']==period and request['instrument']=='EUR.USD' and request['position_at_request']==0,'REQUEST_SCOPE')
    require(source['producer_manifest_sha256']==producer==binding['producer_manifest_sha256']==row['behavior_scope']['producer_manifest_sha256']==reward['input_attribution']['producer_manifest_sha256'],'PRODUCER_IDENTITY')
    require(digest(request)==source['request_sha256']==binding['request_sha256']==row['input_id']==reward['input_id'],'REQUEST_HASH')
    require(request['request_id']==row['request_id']==binding['request_id']==reward['input_attribution']['request_id'],'REQUEST_IDENTITY')
    require(request['model_id']==row['behavior_scope']['model_id']==reward['behavior_model_id'],'BEHAVIOR_IDENTITY')
    require(hashlib.sha256(request['prompt'].encode()).hexdigest()==request['prompt_sha256']==reward['input_attribution']['prompt_sha256'],'PROMPT_HASH')
    require(binding['decision_sha256']==reward['forecast_id'],'DECISION_BINDING')
    require(cmd['kind']=='OPEN' and cmd['instrument']=='EUR.USD' and cmd['position_before_send']==0 and cmd['quantity_mode']=='BASE_NOTIONAL_TARGET' and number(cmd['target_notional_usd'])==100000,'ORIGINAL_100K_COMMAND')
    require(cmd['command_id']==row['open_command_id']==reward['open_command_id'] and cmd['side']==row['action'] and row['action'] in ('BUY','SELL'),'OPEN_BINDING')
    require(row['reward_id']==reward['reward_id'] and row['episode_id']==reward['episode_id'],'EPISODE_BINDING')
    require(type(request['started_at_ms']) is int and request['started_at_ms']==row['input_started_at_ms']==binding['request_started_at_ms'],'INPUT_TIME')
    expected=(request['prompt']+'\nFinancial value context: EUR.USD; requested reference notional budget=100000 USD; selected action='+row['action']+'; behavior model='+request['model_id']+'. Predict eventual roundtrip net USD after reported execution commissions.')
    require(row['feature_text']==expected and hashlib.sha256(expected.encode()).hexdigest()==row['feature_text_sha256'],'FEATURE_INPUT_ONLY')
    position=cash=fees=opened=Decimal(0);seen=set();commands=set()
    for e in reward['executions']:
        require(e['execution_id'] not in seen,'DUPLICATE_EXECUTION');seen.add(e['execution_id']);commands.add(e['command_id'])
        q,p,f=(number(e[k]) for k in ('quantity','price','commission'))
        require(q>0 and p>0 and e['side'] in ('BUY','SELL') and e['commission_currency']=='USD','EXECUTION_SCOPE')
        signed=q if e['side']=='BUY' else -q
        require(not position or position*(position+signed)>=0,'CROSS_ZERO')
        position+=signed;cash-=signed*p;fees+=f
        if e['command_id']==cmd['command_id']:
            require(e['side']==row['action'],'OPEN_SIDE');opened+=q
    require(set(reward['execution_ids'])==seen and len(reward['execution_ids'])==len(seen) and set(reward['command_ids'])==commands,'EXECUTION_INVENTORY')
    require(position==0 and opened==number(cmd['order_quantity']),'QUANTITY_RECONCILIATION')
    require(cash==number(reward['realized_gross_usd']) and fees==number(reward['actual_commissions_usd'])==number(row['actual_commissions_usd']) and cash-fees==number(reward['realized_net_usd'])==number(row['net_usd']),'CASH_FEE_NET_RECONCILIATION')
    return {'row_id':row['row_id'],'request_id':row['request_id'],'open_command_id':row['open_command_id'],
            'reward_id':row['reward_id'],'execution_ids':sorted(seen),'gross_usd':str(cash),'fees_usd':str(fees),'net_usd':str(cash-fees),'input_started_at_ms':row['input_started_at_ms']}

def reconcile(data):
    require(data.get('schema')=='heptatrader.current100k-value-dataset.v1' and data.get('synthetic_rewards')==0 and data.get('old25k_rewards')==0,'DATASET_SCOPE')
    require(data.get('behavior_profile')=='EURUSD_ORIGINAL_ENTRY_CONF_0.62_USD100K_REFERENCE_NOTIONAL','BEHAVIOR_PROFILE')
    require(all(data.get(k) is False for k in ('automatic_selection','automatic_training','trade_authority')),'DATASET_AUTHORITY')
    require(isinstance(data.get('rows'),list) and 0<len(data['rows'])<=10000,'ROW_BUDGET')
    rows=[];executions=set();commands=set();rowids=set();rewardids=set();requests=set()
    with localcontext() as ctx:
        ctx.prec=50
        for row in data['rows']:
            r=verify_row(row,data['period_sha256'],data['producer_manifest_sha256'])
            for field,seen in [('row_id',rowids),('open_command_id',commands),('reward_id',rewardids),('request_id',requests)]:
                require(r[field] not in seen,'CROSS_ROW_DUPLICATE_'+field);seen.add(r[field])
            require(not executions.intersection(r['execution_ids']),'CROSS_ROW_EXECUTION_REUSE');executions.update(r['execution_ids']);rows.append(r)
        require(data['fingerprint']==digest({k:v for k,v in data.items() if k!='fingerprint'}),'DATASET_FINGERPRINT')
        totals={field:str(sum((number(r[field]) for r in rows),Decimal(0))) for field in ('gross_usd','fees_usd','net_usd')}
    return {'rows_checked':len(rows),'unique_executions':len(executions),'unique_original_opens':len(commands),'totals_exported_subset':totals,'rows':rows,
            'all_embedded_identity_and_arithmetic_checks':True,'raw_oms_replayed':False,'raw_attribution_prefixes_replayed':False,'full_account_nav_reconciled':False,'model_credit':False}

def main():
    p=argparse.ArgumentParser();p.add_argument('--dataset',required=True);p.add_argument('--expected-sha256',required=True);p.add_argument('--output',required=True);a=p.parse_args()
    os.umask(0o077);data,sha=read(a.dataset);require(sha==a.expected_sha256,'CAPTURED_DATASET_HASH');result=reconcile(data)
    result.update(schema='heptatrader.derived-value-reconciliation.v1',at_ms=int(time.time()*1000),dataset_sha256=sha,verifier_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),orders_sent=0,trade_authority=False)
    with Path(a.output).open('x') as f:json.dump(result,f,sort_keys=True,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
    print(json.dumps({k:v for k,v in result.items() if k!='rows'},sort_keys=True))
if __name__=='__main__':main()
