"""Strip old test populations and declare a latency-safe development profile.
This trusted exporter reads source bytes to verify their hashes. Only its
train/validation output may be passed into a no-network learning sandbox.
"""
from __future__ import annotations
import argparse
from pathlib import Path
from .common import EvidenceError,read,sha,digest,write_once

PROFILE='EURUSD_5MIN_LOOKBACK12_LEAD1_HOLD3_V1'


def derive(source,barfile,source_sha,bar_sha):
    if (source.get('actual_execution_rewards')!=0 or source.get('trade_authority') is not False
        or source.get('lookback_bars')!=12 or source.get('horizon_bars')!=3
        or source.get('paired_bars_sha256')!=bar_sha):raise EvidenceError('SOURCE_BAR_PROFILE_OR_BINDING')
    bars={r['time']:r for r in barfile['bars']}
    if len(bars)!=len(barfile['bars']):raise EvidenceError('DUPLICATE_SOURCE_BAR')
    groups={}
    for name in ('train','validation'):
        original=source['groups'][name]
        if not original:raise EvidenceError('EMPTY_DEVELOPMENT_POPULATION')
        end=max(r['outcome_end'] for r in original);converted=[]
        for row in original:
            anchor=row['time'];entry=anchor+300;exit_time=entry+900;outcome_end=exit_time+300
            if outcome_end>end or any(t not in bars for t in range(anchor,exit_time+300,300)):continue
            entrybar=bars[entry];exitbar=bars[exit_time];reference=float(entrybar['midpoint']['open'])
            target=(float(exitbar['midpoint']['open'])/reference-1)*10000
            converted.append({'time':anchor,'history_start':row['history_start'],'outcome_end':outcome_end,
                'entry_at':entry,'exit_at':exit_time,'features':row['features'],
                'class':0 if target< -1 else 2 if target>1 else 1,'target_bps':target,
                'long_after_spread_bps':(float(exitbar['bid']['open'])-float(entrybar['ask']['open']))/reference*10000,
                'short_after_spread_bps':(float(entrybar['bid']['open'])-float(exitbar['ask']['open']))/reference*10000,
                'real_execution_reward':False})
        groups[name]=converted
    result={'schema':'heptatrader.development-only-price-data.v1','source_sha256':source_sha,'source_bars_sha256':bar_sha,
        'profile':PROFILE,'groups':groups,'source_groups_allowed':['train','validation'],'source_test_rows_exported':0,
        'trade_authority':False,'actual_execution_rewards':0,'old_test_is_not_independent':True,
        'decision_lead_seconds':300,'return_horizon_seconds':900,'label_availability_delay_seconds':300,
        'cost_assumptions':{'roundtrip_commission_bps':.4,'roundtrip_slippage_bps':.5,'reference_notional_usd':100000}}
    result['fingerprint']=digest(result)
    return result


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--source',type=Path,required=True);parser.add_argument('--source-sha256',required=True)
    parser.add_argument('--bars',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);a=parser.parse_args()
    if sha(a.source)!=a.source_sha256:raise EvidenceError('APPROVED_SOURCE_CHANGED')
    source=read(a.source,256*1024*1024);bar_sha=sha(a.bars)
    if digest({k:v for k,v in source.items() if k!='dataset_id'})!=source['dataset_id']:raise EvidenceError('SOURCE_DATASET_ID')
    bars=read(a.bars,256*1024*1024);result=derive(source,bars,a.source_sha256,bar_sha)
    from .calibration_development import validate_development
    validate_development(result);write_once(a.output,result)
    print(__import__('json').dumps({'output_sha256':sha(a.output),'fingerprint':result['fingerprint'],'group_rows':{k:len(v) for k,v in result['groups'].items()},'old_test_rows_exported':0}))
if __name__=='__main__':main()
