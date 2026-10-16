#!/usr/bin/env python3
"""Bounded Pocket4-only prospective source coverage for a read-only price prior.

This is NOT a formal Laya promotion trial and has NO connection to execution,
OMS, brokers, trade authority, or live model selection. The fixed 90-slot
schedule and frozen prior are registered before the first slot opens.
"""
from __future__ import annotations

import argparse
from bisect import bisect_left, bisect_right
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import sys
import time
from datetime import datetime, timezone

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"research/neuron_learning"))
from laya_cell.integrity import ContractError, digest, read_json, sha_file
from laya_cell.price_prequential_research import asof_prior, collect_available
from laya_cell.action_quality_research import probabilities
from laya_cell.price_quality_research import CLASSES
from laya_cell.dataset import BIN_EDGES_BPS
from laya_cell.resident_recent_source import read_recent_original_quotes, MIN_OUTCOME_SPAN_MS
from laya_cell.resident_data import forecast_input

SLOTS=90
SPACING_MS=360000
HORIZON_MS=300000
GRACE_MS=60000
SOURCE_TIMEOUT_MS=15000
STOP=False

def stop(*_):
    global STOP
    STOP=True

def persist_new(path, data):
    path=Path(path)
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as f:
            json.dump(data,f,sort_keys=True,separators=(',',':'),allow_nan=False)
            f.write('\n');f.flush();os.fsync(f.fileno())
    finally:
        if fd>=0:
            pass

def persist_status(path,data):
    path=Path(path)
    tmp=path.with_name('.'+path.name+'.'+str(os.getpid()))
    with tmp.open('x') as f:
        json.dump(data,f,sort_keys=True,separators=(',',':'),allow_nan=False)
        f.write('\n');f.flush();os.fsync(f.fileno())
    os.replace(tmp,path)

def verified_model(report,config):
    if (report.get('schema') != 'heptatrader.price-prequential-research.v1'
            or report.get('report_id') != digest({k:v for k,v in report.items() if k!='report_id'})
            or report.get('claims',{}).get('no_trade_authority') is not True
            or report.get('selection_parameters') is None):
        raise ContractError('SHADOW_PRICE_RESEARCH_REPORT_NOT_FROZEN')
    p=report['selection_parameters']
    if set(p)!= {'backoff_strength','half_life_hours'}:
        raise ContractError('SHADOW_PRICE_MODEL_PARAMETER_SHAPE')
    weights=report['global_training_prior']['weights']
    probabilities(weights,CLASSES)
    if config.get('automatic_trading') is not False or config.get('canonical_owner_endpoint') is not None:
        raise ContractError('SHADOW_RESEARCH_CONFIG_HAS_ORDER_AUTHORITY')
    if sha_file(Path(config['period_path']))!=config['period_sha256']:
        raise ContractError('SHADOW_PERIOD_NOT_PINNED')
    return weights,p

def grade(prediction,quotes,now_ms):
    if (prediction.get('schema')!='heptatrader.price-shadow-prediction.v1'
            or type(prediction.get('anchor_ms')) is not int
            or type(prediction.get('created_at_ms')) is not int
            or prediction['created_at_ms'] >= prediction['anchor_ms']+HORIZON_MS):
        raise ContractError('SHADOW_PREDICTION_NONCAUSAL_OR_UNBOUND')
    ordered=[q['t'] for q in quotes]
    anchor=prediction['anchor_ms'];due=anchor+HORIZON_MS
    a=bisect_left(ordered,anchor);b=bisect_left(ordered,due)
    if b==len(ordered) and now_ms<due+SOURCE_TIMEOUT_MS:
        return None
    scope={'plan_id':prediction['plan_id'],'slot':prediction['slot'],
           'anchor_ms':anchor,'evaluated_at_ms':now_ms,
           'metric_scope':'ORIGINAL_QUOTE_SHADOW_PROBABILITY_ONLY',
           'trading_authority':False,'broker_orders_sent':0}
    def censor(reason):return {**scope,'status':'CENSORED','reason':reason}
    if a>=len(quotes) or ordered[a]!=anchor or abs(float(quotes[a]['mid'])-prediction['reference_mid'])>1e-10:
        return censor('ANCHOR_MISSING_OR_CHANGED')
    if b>=len(quotes):return censor('OUTCOME_MISSING')
    if ordered[b]>due+5000:return censor('ENDPOINT_GAP')
    if any(y-x>30000 for x,y in zip(ordered[a:b],ordered[a+1:b+1])):
        return censor('PATH_GAP')
    bp=(float(quotes[b]['mid'])/float(quotes[a]['mid'])-1)*10000
    label=bisect_right(BIN_EDGES_BPS,bp)
    def metrics(p):
        probabilities(p,CLASSES)
        return {'brier':sum((x-(i==label))**2 for i,x in enumerate(p)),
                'nll':-math.log(max(p[label],1e-12)),
                'hit':max(range(CLASSES),key=lambda i:p[i])==label}
    return {**scope,'status':'OBSERVED','reason':None,'outcome_ms':ordered[b],
            'outcome_quote_receipt':quotes[b]['receipt'],'return_bps':bp,
            'target_class':label,'online_asof':metrics(prediction['online_prior']),
            'frozen_train_prior':metrics(prediction['frozen_train_prior'])}

def statuses(root,plan):
    good=bad=0
    for i in range(plan['slots']):
        f=root/('outcome-%03d.json'%i)
        if not f.exists():continue
        o=read_json(f)
        if o['plan_id']!=plan['plan_id'] or o['slot']!=i or o['status'] not in ('OBSERVED','CENSORED'):
            raise ContractError('SHADOW_OUTCOME_CHANGED')
        good+=o['status']=='OBSERVED'
        bad+=o['status']=='CENSORED'
    return good,bad

def prior_asof(history,root,current_slot,anchor,weights,param):
    rows=list(history)
    for i in range(current_slot):
        outcome=root/('outcome-%03d.json'%i)
        if not outcome.exists():continue
        o=read_json(outcome)
        if o['status']=='OBSERVED':
            rows.append({'anchor_ms':o['anchor_ms'],'label_available_at_ms':o['evaluated_at_ms'],
                         'target_class':o['target_class']})
    q,n=asof_prior(rows,anchor,weights,param['half_life_hours'],param['backoff_strength'])
    return q,n

def poll_fresh(config,start,deadline):
    last='NO_QUOTE_RECEIVED'
    while int(time.time()*1000)<=deadline and not STOP:
        now=int(time.time()*1000)
        if now<start:
            time.sleep(min(1,max(.05,(start-now)/1000)));continue
        try:
            quotes,prefixes=read_recent_original_quotes(config,now)
            sample,_=forecast_input(config,quotes,int(time.time()*1000))
            if sample['anchor_ms']<start:
                raise ContractError('PRE_SLOT_QUOTE_NOT_CAUSAL')
            if int(time.time()*1000)>deadline:
                return None,None,None,'INPUT_SLOT_EXPIRED_DURING_READ'
            return sample,prefixes,now,None
        except (ContractError,OSError,KeyError,ValueError) as exc:
            last=type(exc).__name__+':'+str(exc)[:100]
            time.sleep(.75)
    return None,None,None,last

def evaluate(config,pred):
    due=pred['anchor_ms']+HORIZON_MS
    while int(time.time()*1000)<due+1000 and not STOP:
        time.sleep(min(2,max(.05,(due+1000-int(time.time()*1000))/1000)))
    last_error='OUTCOME_NOT_READY'
    for attempt in range(16):
        if STOP:break
        now=int(time.time()*1000)
        try:
            quotes,prefixes=read_recent_original_quotes(config,now,min_span_ms=MIN_OUTCOME_SPAN_MS)
            result=grade(pred,quotes,now)
            if result is not None:
                result['outcome_source_pin_digest']=digest(prefixes)
                return result
        except (ContractError,OSError,KeyError,ValueError) as exc:
            last_error=type(exc).__name__+':'+str(exc)[:100]
        time.sleep(1)
    return {'plan_id':pred['plan_id'],'slot':pred['slot'],
            'anchor_ms':pred['anchor_ms'],'evaluated_at_ms':int(time.time()*1000),
            'status':'CENSORED','reason':'SOURCE_UNAVAILABLE_AFTER_DUE:'+last_error,
            'trading_authority':False,'broker_orders_sent':0}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--report',type=Path,required=True)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--slots',type=int,default=SLOTS)
    parser.add_argument('--initial-delay-sec',type=int,default=60)
    parser.add_argument('--resume',action='store_true')
    args=parser.parse_args()
    os.umask(0o077)
    if args.slots!=SLOTS or not 30<=args.initial_delay_sec<=600:
        parser.error('fixed 90 slots and bounded initial delay are required')
    if not args.root.is_absolute() or args.root.is_symlink():
        parser.error('root must be an absolute non-symlink path')
    if args.resume:
        if not args.root.is_dir():parser.error('missing prior snapshot')
    else:
        args.root.mkdir(mode=0o700,parents=True,exist_ok=False)
    fd=os.open(args.root/'run.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
    fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    config=read_json(args.config)
    report=read_json(args.report)
    weights,params=verified_model(report,config)
    plan_path=args.root/'plan.json'
    history_path=args.root/'historical-labels.json'
    if args.resume:
        plan=read_json(plan_path)
        if plan['plan_id']!=digest({k:v for k,v in plan.items() if k!='plan_id'}):
            raise ContractError('SHADOW_PLAN_MODIFIED')
        history_record=read_json(history_path)
        if sha_file(history_path)!=plan['historical_snapshot_sha256']:
            raise ContractError('SHADOW_HISTORY_MODIFIED')
        history=history_record['rows']
    else:
        history,provenance=collect_available(config['evaluation_directory'])
        snapshot={'schema':'heptatrader.price-shadow-historical-snapshot.v1',
                  'source':provenance,'rows':history}
        persist_new(history_path,snapshot)
        created=int(time.time()*1000)
        start=created+args.initial_delay_sec*1000
        plan={'schema':'heptatrader.price-shadow-fixed-90.v1',
              'created_at_ms':created,'start_ms':start,'slot_spacing_ms':SPACING_MS,
              'horizon_ms':HORIZON_MS,'input_grace_ms':GRACE_MS,
              'slots':SLOTS,'last_outcome_end_ms':start+(SLOTS-1)*SPACING_MS+HORIZON_MS+15000,
              'source_period_sha256':config['period_sha256'],
              'config_file_sha256':sha_file(args.config),
              'model_report_sha256':sha_file(args.report),'model_report_id':report['report_id'],
              'historical_snapshot_sha256':sha_file(history_path),
              'selected_params':params,'training_prior':weights,
              'runner_source_sha256':sha_file(Path(__file__)),
              'prior_source_sha256':sha_file(ROOT/'research/neuron_learning/laya_cell/price_prequential_research.py'),
              'quote_reader_sha256':sha_file(ROOT/'research/neuron_learning/laya_cell/resident_recent_source.py'),
              'state':'RESEARCH_PRICE_SHADOW_ONLY_NO_LAYA_PROMOTION',
              'original_trial_unchanged':True,'trading_authority':False,
              'broker_orders_sent':0}
        plan['plan_id']=digest(plan)
        persist_new(plan_path,plan)
    if (plan['runner_source_sha256'] != sha_file(Path(__file__)) or
            plan['prior_source_sha256'] != sha_file(ROOT/'research/neuron_learning/laya_cell/price_prequential_research.py') or
            plan['quote_reader_sha256'] != sha_file(ROOT/'research/neuron_learning/laya_cell/resident_recent_source.py')):
        raise ContractError('SHADOW_PINNED_RESEARCH_SOURCE_CHANGED')
    if (plan['config_file_sha256']!=sha_file(args.config) or
            plan['model_report_sha256']!=sha_file(args.report) or
            plan['source_period_sha256']!=config['period_sha256'] or
            plan['selected_params']!=params or plan['training_prior']!=weights):
        raise ContractError('SHADOW_DEPLOYMENT_IDENTITY_CHANGED')
    print('SHADOW_REGISTERED',json.dumps({'plan_id':plan['plan_id'],
        'start_ms':plan['start_ms'],'slots':plan['slots'],
        'end_ms':plan['last_outcome_end_ms'],'no_trade_authority':True}),flush=True)
    for i in range(plan['slots']):
        if STOP:break
        start=plan['start_ms']+i*SPACING_MS;grace=start+GRACE_MS
        outfile=args.root/('outcome-%03d.json'%i)
        predfile=args.root/('prediction-%03d.json'%i)
        if outfile.exists():continue
        if predfile.exists():
            pred=read_json(predfile)
            if pred['plan_id']!=plan['plan_id'] or pred['slot']!=i:
                raise ContractError('SHADOW_RESTART_PREDICTION_IDENTITY')
        else:
            sample,prefixes,source_clock,error=poll_fresh(config,start,grace)
            if STOP:break  # A supervised drain is not a missing market observation.
            if sample is None:
                outcome={'plan_id':plan['plan_id'],'slot':i,
                    'evaluated_at_ms':int(time.time()*1000),
                    'status':'CENSORED','reason':'NO_CAUSAL_INPUT_IN_FIXED_SLOT:'+str(error)[:140],
                    'trading_authority':False,'broker_orders_sent':0}
                persist_new(outfile,outcome)
                good,bad=statuses(args.root,plan)
                persist_status(args.root/'status.json',{'state':'RUNNING','at_ms':int(time.time()*1000),
                    'observed':good,'censored':bad,'recorded':good+bad,'last_slot':i,
                    'minimum_88_of_90_still_possible':bad<=2})
                print('SLOT_CENSORED',i,outcome['reason'],flush=True)
                continue
            anchor=sample['anchor_ms']
            if not start<=anchor<=grace:
                raise ContractError('SHADOW_ANCHOR_OUTSIDE_REGISTERED_SLOT')
            q,prior_count=prior_asof(history,args.root,i,anchor,weights,params)
            created=int(time.time()*1000)
            if created>=anchor+HORIZON_MS:raise ContractError('SHADOW_PREDICTION_LATE')
            pred={'schema':'heptatrader.price-shadow-prediction.v1',
                  'plan_id':plan['plan_id'],'slot':i,'anchor_ms':anchor,'created_at_ms':created,
                  'source_clock_ms':source_clock,'reference_mid':float(sample['reference_mid']),
                  'input_sample_id':sample['sample_id'],
                  'input_age_ms':created-anchor,
                  'input_quote_prefix_digest':digest(prefixes),
                  'historically_available_labels':prior_count,
                  'online_prior':q,'frozen_train_prior':weights,
                  'model_report_id':report['report_id'],'trading_authority':False,
                  'broker_orders_sent':0}
            persist_new(predfile,pred)
            print('PREDICTION_SEALED',json.dumps({'slot':i,'anchor_ms':anchor,'created_at_ms':created,
                   'model_id':report['report_id'],'asof_rows':prior_count}),flush=True)
        outcome=evaluate(config,pred)
        if STOP:break
        outcome['prediction_file_sha256']=sha_file(predfile)
        persist_new(outfile,outcome)
        good,bad=statuses(args.root,plan)
        persist_status(args.root/'status.json',{'state':'RUNNING','at_ms':int(time.time()*1000),
            'observed':good,'censored':bad,'recorded':good+bad,'last_slot':i,
            'minimum_88_of_90_still_possible':bad<=2,
            'trading_authority':False,'broker_orders_sent':0})
        print('SLOT_RESULT',json.dumps({'slot':i,'status':outcome['status'],
                   'observed':good,'censored':bad,'reason':outcome.get('reason')}),flush=True)
    good,bad=statuses(args.root,plan)
    state=('STOPPED_RECOVERABLE' if STOP else
           'SHADOW_90_INPUT_COVERAGE_PASSED_ONLY' if good>=88 and bad<=2 else
           'SHADOW_90_INPUT_COVERAGE_FAILED')
    persist_status(args.root/'status.json',{'state':state,'at_ms':int(time.time()*1000),
        'observed':good,'censored':bad,'recorded':good+bad,'slots':SLOTS,
        'formal_laya_qualification':False,'trading_authority':False,
        'broker_orders_sent':0})
    print('SHADOW_END',json.dumps({'state':state,'observed':good,'censored':bad,
                                   'no_authority':True}),flush=True)


if __name__ == '__main__':
    main()
