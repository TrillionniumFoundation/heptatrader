"""Prepare identical one-bar-lead development rows for three model arms."""
from __future__ import annotations
import argparse,hashlib,json,math,time
from pathlib import Path
from .common import EvidenceError,read,sha,digest,write_once
from .calibration_development import validate_development,split_purged
SIZES={'fit':512,'tune':128,'calibration':128,'check':256}
SEED=2026100818


def select(data):
    train,val=validate_development(data)
    f,t,a=split_purged(train,.8);c,h,b=split_purged(val,.5)
    roles={'fit':f,'tune':t,'calibration':c,'check':h};chosen={}
    for name,count in SIZES.items():
        rows=roles[name]
        if len(rows)<count:raise EvidenceError('INSUFFICIENT_MATCHED_ROLE')
        chosen[name]=[rows[i*(len(rows)-1)//(count-1)] for i in range(count)]
    return chosen,{'original_sizes':{k:len(v) for k,v in roles.items()},'purges':[a,b]}


def text(row):
    values=[round(float(x),6) for x in row['features']]
    if len(values)!=25 or not all(math.isfinite(x) for x in values):raise EvidenceError('MATCHED_INPUT_SHAPE')
    body={'relative_completed_closes_bps':values[:11],'completed_spreads_bps':values[11:23],'utc_hour_sine_cosine':values[23:]}
    return 'EUR/USD five-minute bars, oldest first. After a five-minute decision delay, forecast the following 15-minute midpoint return: down below -1 bp, flat within +/-1 bp, up above +1 bp. '+json.dumps(body,sort_keys=True,separators=(',',':'))


def prepare(config):
    inp=Path(config['input']);out=Path(config['output'])
    if sha(inp)!=config['input_sha256']:raise EvidenceError('DEVELOPMENT_INPUT_CHANGED')
    if out.exists():raise EvidenceError('MATCHED_EXPERIMENT_EXISTS')
    data=read(inp,128*1024*1024);groups,split=select(data);rows=sum((groups[k] for k in SIZES),[])
    from paired_net_100k_preflight.run import asset_manifest
    assets={arm:asset_manifest(Path(config[arm+'_directory']),Path(config[arm+'_receipt']),arm) for arm in ('mmbert','english')}
    if sha(config['frozen_candidate'])!='538dd502589cf4ddad56c0dc2c13b7f75f5da402f2d53f08983b09fc6a377466':raise EvidenceError('FROZEN_REFERENCE_CHANGED')
    out.mkdir(mode=0o700)
    payload={'schema':'heptatrader.matched-lead1-input.v1','profile':data['profile'],'groups':groups,
        'source_development_sha256':sha(inp),'old_test_rows_exported':0,'trade_authority':False}
    write_once(out/'input.json',payload)
    plan={'schema':'heptatrader.matched-lead1-plan.v1','at_ms':int(time.time()*1000),'profile':data['profile'],
        'input_sha256':sha(out/'input.json'),'role_sizes':SIZES,'source_roles':split,
        'times':{k:[r['time'] for r in v] for k,v in groups.items()},
        'text_sha256':[hashlib.sha256(text(r).encode()).hexdigest() for r in rows],
        'assets':assets,'projection_width':64,'projection_seed':SEED,'device':'cpu','cpu_threads':2,
        'classifier':'REGULARIZED_SOFTMAX','penalties':[.01,.1,1.],
        'selection':'TUNE_BRIER_THEN_REFIT_FIT_PLUS_TUNE_ONLY','calibration':'CALIBRATION_LOGLOSS_ONLY',
        'fixed_numeric_mix_weight':.5,'same_model_family_training_rows':640,
        'frozen_reference_training_rows':24175,'frozen_reference_is_unequal_budget':True,
        'value_penalties':[.01,.1,1.],'value_selection':'TUNE_EXECUTABLE_NET_MSE',
        'buffer_grid_bps':[0.,.5,1.,2.],'min_calibration_trades':30,
        'commission_bps':.4,'slippage_bps':.5,'notional_usd':100000,
        'old_test_rows':0,'trade_authority':False,'automatic_promotion':False,
        'source_files_sha256':{p.name:sha(p) for p in Path(__file__).parent.glob('matched_*.py')}}
    write_once(out/'plan.json',plan)
    print(json.dumps({'output':str(out),'input_sha256':sha(out/'input.json'),'plan_sha256':sha(out/'plan.json'),'role_sizes':SIZES},indent=2))


def main():
    p=argparse.ArgumentParser();p.add_argument('--config',type=Path,required=True);a=p.parse_args();prepare(read(a.config))
if __name__=='__main__':main()
