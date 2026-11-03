"""python -m research.model_replacement {dataset,split,run}."""
import argparse
from dataclasses import fields
import json
from pathlib import Path
import sys
from .data import build_dataset
from .splits import build_split
from .experiment import ExperimentConfig, run_experiment, write_new
from research.neuron_learning.laya_cell.integrity import read_json_snapshot

def parser():
    p=argparse.ArgumentParser(description='Isolated historical PRICE research; no trade authority.')
    sub=p.add_subparsers(dest='command',required=True)
    data=sub.add_parser('dataset'); data.add_argument('--jobs-manifest',required=True); data.add_argument('--output',required=True)
    data.add_argument('--horizon-sec',type=int,choices=(300,900),default=300)
    split=sub.add_parser('split'); split.add_argument('--dataset',required=True); split.add_argument('--output',required=True)
    split.add_argument('--fractions',type=float,nargs=3,default=(.55,.15,.15))
    split.add_argument('--embargo-ms',type=int,default=0); split.add_argument('--minimum-rows',type=int,default=8)
    run=sub.add_parser('run'); run.add_argument('--dataset',required=True); run.add_argument('--split',required=True)
    run.add_argument('--output',required=True)
    for field in fields(ExperimentConfig):
        run.add_argument('--'+field.name.replace('_','-'),default=field.default,
                         type=int if type(field.default) is int else str)
    return p

def main(argv=None):
    args=parser().parse_args(argv); out=Path(args.output).absolute()
    if args.command=='dataset':
        result=build_dataset(Path(args.jobs_manifest),args.horizon_sec)
    elif args.command=='split':
        dataset,_=read_json_snapshot(Path(args.dataset),256*1024**2)
        result=build_split(dataset,tuple(args.fractions),args.embargo_ms,args.minimum_rows)
    else:
        from threadpoolctl import threadpool_limits
        config=ExperimentConfig(**{f.name:getattr(args,f.name) for f in fields(ExperimentConfig)})
        with threadpool_limits(limits=config.threads):
            result=run_experiment(args.dataset,args.split,out,config)
        print(json.dumps({'status':result['status'],'report':str(out/'report.json'),
                          'test':result['metrics']['test'],'model_state':result['model_state']},sort_keys=True))
        return 0
    out.parent.mkdir(parents=True,exist_ok=True); pin=write_new(out,result)
    print(json.dumps({'status':'WRITTEN',**pin,'accounting':result.get('accounting'),
                      'coverage':result.get('coverage')},sort_keys=True))
    return 0

if __name__=='__main__':
    try: raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({'status':'FAILED','error_type':type(exc).__name__,'error':str(exc)}),file=sys.stderr)
        raise SystemExit(2)
