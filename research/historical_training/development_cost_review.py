"""CPU-only, fixed-policy cost stress and explicit previously-seen data review.

No torch/model download, broker/socket client, deployment or prospective claims.
"""
from __future__ import annotations
import argparse, hashlib, json, os, time
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
from .eurusd_walkforward_cost import features, verify_inputs, THRESHOLDS, DAY, EMBARGO
from .offline_compare import input_text, input_vector


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    with Path(path).open('x') as f:
        json.dump(value, f, sort_keys=True, indent=2, allow_nan=False)
        f.write('\n'); f.flush(); os.fsync(f.fileno())


def time_digest(values):
    return hashlib.sha256(json.dumps([int(t) for t in values], separators=(',', ':')).encode()).hexdigest()


def ridge(x, y):
    if len(x) < 32 or not np.isfinite(x).all() or not np.isfinite(y).all():
        raise ValueError('INVALID_TRAINING_ROWS')
    mu=x.mean(0); sd=np.maximum(x.std(0), 1e-8)
    z=np.c_[np.ones(len(x)), np.clip((x-mu)/sd, -5, 5)]
    penalty=np.eye(z.shape[1])*50.; penalty[0,0]=0
    return mu, sd, np.linalg.solve(z.T@z+penalty, z.T@y)


def infer(model, x):
    mu, sd, weights=model
    return np.c_[np.ones(len(x)), np.clip((x-mu)/sd, -5, 5)] @ weights


def chosen_indices(at, mature, forecast, threshold):
    if threshold is None: return np.array([], dtype=int)
    result=[]; previous_end=-1
    for i,(t,end,p) in enumerate(zip(at,mature,forecast)):
        if not np.isfinite(p) or end <= t: raise ValueError('INVALID_FORECAST_OR_MATURITY')
        if t < previous_end or abs(p) <= threshold: continue
        result.append(i); previous_end=int(end)
    return np.array(result, dtype=int)


def metrics(at, forecast, returns, indices, cost):
    if not np.isfinite(cost) or cost < 0: raise ValueError('INVALID_COST')
    pnl=(np.sign(forecast[indices])*returns[indices]-cost)*10.
    curve=np.r_[0., np.cumsum(pnl)]
    counts=len(indices)
    return {'trades':counts, 'net_usd':float(pnl.sum()),
            'mean_net_usd_per_trade':float(pnl.mean()) if counts else None,
            'max_drawdown_usd':float((np.maximum.accumulate(curve)-curve).max()),
            'win_rate':float((pnl>0).mean()) if counts else None,
            'two_way_reference_turnover_usd':counts*200000,
            'trade_set_sha256':time_digest(at[indices]),
            'cost_roundtrip_bps':float(cost), 'reference_notional_usd':100000,
            'actual_broker_fills':0}


def evaluate_model(at, mature, x, y, tr, va, te, spread, stress_spread, commission, out, tag):
    if np.intersect1d(tr,va).size or np.intersect1d(tr,te).size or np.intersect1d(va,te).size:
        raise ValueError('OVERLAPPING_FOLD')
    if mature[tr].max()+EMBARGO >= at[va].min() or mature[va].max()+EMBARGO >= at[te].min():
        raise ValueError('CAUSAL_EMBARGO_FAILED')
    model=ridge(x[tr], y[tr]); pv=infer(model,x[va]); pt=infer(model,x[te])
    choices=[]; validation_cost=spread+2*commission+2.
    for threshold in THRESHOLDS:
        ids=chosen_indices(at[va],mature[va],pv,threshold)
        m=metrics(at[va],pv,y[va],ids,validation_cost)
        if m['trades']>=30 and m['net_usd']>0:
            choices.append((m['net_usd'],-threshold,threshold))
    threshold=max(choices)[2] if choices else None
    policy_ids=chosen_indices(at[te],mature[te],pt,threshold)
    raw_ids=chosen_indices(at[te],mature[te],pt,0.)
    scenarios=[]
    for slip in (.5,1.,2.):
        cost=(stress_spread if slip==2. else spread)+2*commission+2*slip
        scenarios.append({'slippage_assumed_bps_per_side':slip,
            'validation_selected_policy':metrics(at[te],pt,y[te],policy_ids,cost),
            'unfiltered_sign_diagnostic_not_selected_policy':metrics(at[te],pt,y[te],raw_ids,cost),
            'no_trade':metrics(at[te],pt,y[te],np.array([],dtype=int),cost)})
    # Freeze the same trade identities across cost stress, no cost-based retuning.
    for key in ('validation_selected_policy','unfiltered_sign_diagnostic_not_selected_policy'):
        assert len({s[key]['trade_set_sha256'] for s in scenarios})==1
        assert scenarios[2][key]['net_usd'] <= scenarios[0][key]['net_usd']+1e-8
    path=out/(tag+'-readout.npz')
    with path.open('xb') as f:
        np.savez_compressed(f,mu=model[0],sd=model[1],weights=model[2],
                            train_times=at[tr],validation_times=at[va],test_times=at[te],test_predictions=pt)
        f.flush();os.fsync(f.fileno())
    return {'selected_threshold_bps':threshold,'selection':'VALIDATION_NET_POSITIVE_MIN_30_TRADES_ELSE_ABSTAIN',
            'readout_sha256':sha(path),'test_direction_accuracy':float(((pt>0)==(y[te]>0)).mean()),
            'training_rows':len(tr),'validation_rows':len(va),'test_rows':len(te),
            'training_times_sha256':time_digest(at[tr]),'validation_times_sha256':time_digest(at[va]),
            'test_times_sha256':time_digest(at[te]),'scenarios':scenarios,
            'causal_embargo_seconds':EMBARGO,'new_oos_claim':False}


def run(c):
    out=Path(c['output']);out.mkdir(mode=0o700,parents=True,exist_ok=False)
    # Bind all inputs BEFORE fitting, and reverify them before publishing results.
    before={name:sha(path) for name,path in c['inputs'].items()}
    plan={'schema':'heptatrader.development-cost-review-plan.v1','created_ms':int(time.time()*1000),
          'inputs':c['inputs'],'input_sha256':before,'source_sha256':sha(__file__),
          'dense_windows_days':[120,30,15,15],'paired_index_windows':[[0,256,320,384],[64,320,384,448],[128,384,448,512]],
          'ridge_alpha':50,'threshold_grid':list(THRESHOLDS),'embargo_seconds':EMBARGO,
          'mode':'PREVIOUSLY_SEEN_DEVELOPMENT_REEVALUATION_NOT_NEW_OOS','source_cutoff_unchanged':True,
          'cost_stress_keeps_trade_set_fixed':True,'broker_requests':0,'automatic_promotion':False}
    save(out/'plan.json',plan)
    load=lambda name:json.loads(Path(c['inputs'][name]).read_text())
    bars=load('bars');coverage=load('coverage');cost=load('cost')
    spread,stress_spread,commission=verify_inputs(bars,coverage,cost)
    f=features(bars['bars']);x,at,mature,y=(f[k] for k in ('x','at','mature','returns'))
    first=int(at.min())//DAY*DAY;last=int(mature.max());dense=[];seen=set();consumed=set()
    for i in range(40):
        start=first+i*15*DAY;tr_end=start+120*DAY
        vs=tr_end+EMBARGO;ve=vs+30*DAY;ts=ve+EMBARGO;end=ts+15*DAY
        if end>last:break
        tr=np.flatnonzero((at>=start)&(mature<tr_end-EMBARGO))
        va=np.flatnonzero((at>=vs)&(mature<ve-EMBARGO))
        te=np.flatnonzero((at>=ts)&(mature<end))
        if len(tr)<5000 or len(va)<500 or len(te)<300:raise ValueError('DENSE_FOLD_ROWS')
        ids=set(at[te].tolist())
        if seen & ids:raise ValueError('DUPLICATE_TEST_WINDOWS')
        seen |= ids;consumed |= set(at[np.r_[tr,va,te]].tolist())
        result=evaluate_model(at,mature,x,y,tr,va,te,spread,stress_spread,commission,out,f'dense-{i}')
        dense.append({'fold':i,'train_start_utc':datetime.fromtimestamp(start,timezone.utc).isoformat(),
          'train_end_utc':datetime.fromtimestamp(tr_end,timezone.utc).isoformat(),
          'test_start_utc':datetime.fromtimestamp(ts,timezone.utc).isoformat(),
          'test_end_utc':datetime.fromtimestamp(end,timezone.utc).isoformat(),'numeric':result})
    if len(dense)<2:raise ValueError('INSUFFICIENT_FOLDS')
    oldplan=load('paired_plan');oldresult=load('paired_result');examples=load('paired_examples')
    if (oldplan['dataset_sha256']!=before['paired_examples'] or oldresult['feature_file_sha256']!=before['paired_features']
        or oldresult['plan_sha256']!=before['paired_plan'] or oldplan['dataset_id']!=examples['dataset_id']):
        raise ValueError('FROZEN_PAIRED_PROVENANCE')
    times=sum((oldplan['sample_times'][k] for k in ('train','validation','test')),[])
    all_examples=sum((examples['groups'][k] for k in ('train','validation','test')),[])
    lookup={r['time']:r for r in all_examples}
    if len(lookup)!=len(all_examples) or len(set(times))!=512:raise ValueError('DUPLICATE_PAIRED_SAMPLE')
    rows=[lookup[t] for t in times]
    if [hashlib.sha256(input_text(r).encode()).hexdigest() for r in rows]!=oldplan['text_hashes']:
        raise ValueError('FROZEN_FEATURE_INPUT_MISMATCH')
    from safetensors.numpy import load_file
    arrays=load_file(c['inputs']['paired_features'])
    if set(arrays)!={'english','mmbert'} or any(a.shape!=(512,256) or not np.isfinite(a).all() for a in arrays.values()):
        raise ValueError('FROZEN_FEATURE_SHAPE')
    arrays['numeric']=np.asarray([input_vector(r) for r in rows],dtype=float)
    at=np.asarray(times);mature=np.asarray([r['outcome_end'] for r in rows]);y=np.asarray([r['target_bps'] for r in rows])
    if not (np.diff(at)>0).all():raise ValueError('PAIRED_TIME_ORDER')
    paired=[]
    for i,(a,b,d,e) in enumerate(plan['paired_index_windows']):
        tr=np.arange(a,b);va=np.arange(b,d);te=np.arange(d,e)
        tr=tr[mature[tr]+EMBARGO<at[va].min()]
        va=va[mature[va]+EMBARGO<at[te].min()]
        arms={arm:evaluate_model(at,mature,xx,y,tr,va,te,spread,stress_spread,commission,out,f'paired-{i}-{arm}') for arm,xx in arrays.items()}
        paired.append({'fold':i,'first_train_utc':datetime.fromtimestamp(int(at[tr[0]]),timezone.utc).isoformat(),
           'first_test_utc':datetime.fromtimestamp(int(at[te[0]]),timezone.utc).isoformat(),
           'last_test_utc':datetime.fromtimestamp(int(at[te[-1]]),timezone.utc).isoformat(),'arms':arms})
    if before!={name:sha(path) for name,path in c['inputs'].items()}:raise ValueError('INPUT_CHANGED_DURING_REVIEW')
    report={'schema':'heptatrader.development-cost-review.v1','plan_sha256':sha(out/'plan.json'),
       'state':'DEVELOPMENT_ONLY_NO_PROMOTION','new_unseen_oos':False,'data_revised_not_contemporaneous':True,
       'data_bar_count':len(bars['bars']),'eligible_dense_rows':len(f['at']),'unique_consumed_dense_timestamps':len(consumed),
       'dense_test_timestamps':len(seen),'dense_folds':dense,'paired_cached_rows':512,'paired_folds':paired,
       'new_encoder_training':False,'training':'CPU_RIDGE_READOUT_REFIT','broker_queries':0,'broker_orders_sent':0,
       'actual_executed_net_pnl_samples':0,'actual_slippage_verified':False,'promotion_allowed':False,
       'historical_fill_assumption':'MID_RETURN_PROXY_NOT_GUARANTEED_EXECUTION',
       'newly_requested_incremental_data_used':False,'input_sha256':before}
    save(out/'result.json',report)
    print(json.dumps({'state':report['state'],'dense_folds':len(dense),'eligible_rows':report['eligible_dense_rows'],
       'paired_folds':len(paired),'dense_thresholds':[d['numeric']['selected_threshold_bps'] for d in dense],
       'paired_thresholds':[{k:v['selected_threshold_bps'] for k,v in p['arms'].items()} for p in paired]}),flush=True)
    return report


def main():
    p=argparse.ArgumentParser();p.add_argument('--config',required=True);a=p.parse_args()
    os.umask(0o077);run(json.loads(Path(a.config).read_text()))
if __name__=='__main__':main()
