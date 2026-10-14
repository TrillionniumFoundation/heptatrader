"""Original-request numeric Value baseline; derived labels, CPU, development only.

No broker/OMS reads, model downloads, signing, deployment or order interfaces.
Missing label-availability times explicitly prevent causal/OOS qualification.
"""
from __future__ import annotations
import argparse, hashlib, json, math, os
from pathlib import Path
import numpy as np
from .value_export_reconcile import read, reconcile, require

EMBARGO_MS=30*60*1000
RIDGE_ALPHA=50.
FOLDS=((80,110,140),(110,140,170),(140,170,200))
FEATURES=('side','ret_1_bps','ret_3_bps','ret_6_bps','ret_12_bps','ret_23_bps',
          'step_vol_bps','range_bps','last_spread_bps','mean_spread_bps',
          'source_age_s','history_span_s','utc_sin','utc_cos',
          'side_ret_1','side_ret_3','side_ret_6','side_ret_12','side_ret_23')

def file_sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(p,obj):
    with Path(p).open('x') as f:
        json.dump(obj,f,sort_keys=True,indent=2,allow_nan=False);f.write('\n');f.flush();os.fsync(f.fileno())

def vector(request,side):
    require(side in ('BUY','SELL'),'CONDITIONAL_SIDE')
    t=request['started_at_ms'];h=request['history_snapshot']
    require(type(t) is int and t>0 and len(h)==24,'EXACT_24_ORIGINAL_QUOTES')
    times=[v['observed_at_ms'] for v in h]
    require(all(type(v) is int and 0<v<=t for v in times) and all(a<b for a,b in zip(times,times[1:])),'QUOTE_TIME_ORDER')
    require(all(type(v[k]) in (int,float) for v in h for k in ('bid','ask','mid')),'QUOTE_NUMBER_TYPE')
    b=np.asarray([v['bid'] for v in h],dtype=float);a=np.asarray([v['ask'] for v in h],dtype=float);m=np.asarray([v['mid'] for v in h],dtype=float)
    require(np.isfinite(np.r_[a,b,m]).all() and (b>0).all() and (a>=b).all(),'QUOTE_FINITE_UNCROSSED')
    require(np.allclose(m,(a+b)/2,rtol=0,atol=1e-12),'QUOTE_MID_MISMATCH')
    r=np.asarray([(m[-1]/m[-1-k]-1)*10000 for k in (1,3,6,12,23)])
    step=np.diff(m)/m[:-1]*10000;spread=(a-b)/m*10000;s=1. if side=='BUY' else -1.
    angle=2*math.pi*((t//1000)%86400)/86400
    result=np.r_[s,r,np.std(step),(max(m)-min(m))/m[-1]*10000,spread[-1],spread.mean(),(t-times[-1])/1000,(times[-1]-times[0])/1000,math.sin(angle),math.cos(angle),s*r]
    require(result.shape==(len(FEATURES),) and np.isfinite(result).all(),'FEATURE_SHAPE')
    return result

def fit(x,y):
    require(len(x)>=40 and x.ndim==2 and len(x)==len(y) and np.isfinite(x).all() and np.isfinite(y).all(),'TRAINING_INPUT')
    mu=x.mean(0);sd=np.maximum(x.std(0),1e-8);z=np.c_[np.ones(len(x)),np.clip((x-mu)/sd,-5,5)]
    penalty=np.eye(z.shape[1])*RIDGE_ALPHA;penalty[0,0]=0
    w=np.linalg.solve(z.T@z+penalty,z.T@y)
    return mu,sd,w

def predict(model,x):
    mu,sd,w=model
    return np.c_[np.ones(len(x)),np.clip((x-mu)/sd,-5,5)]@w

def score(y,p):
    require(len(y)>0 and len(y)==len(p) and np.isfinite(np.r_[y,p]).all(),'SCORE_INPUT')
    d=p-y
    return {'rows':len(y),'mae_usd':float(np.abs(d).mean()),'rmse_usd':float(np.sqrt(np.mean(d*d))),
            'bias_usd':float(d.mean()),'mean_forecast_usd':float(p.mean()),'mean_actual_net_usd':float(y.mean())}

def run(config):
    out=Path(config['output']);out.mkdir(mode=0o700,parents=True,exist_ok=False)
    data,dsha=read(config['dataset']);require(dsha==config['dataset_sha256'],'FROZEN_DATASET_HASH')
    verification=reconcile(data);rows=sorted(data['rows'],key=lambda r:r['input_started_at_ms'])
    require(len(rows)==200,'FROZEN_200_ROW_SCOPE')
    at=np.asarray([r['input_started_at_ms'] for r in rows],dtype=np.int64)
    require((np.diff(at)>0).all(),'UNIQUE_REQUEST_ORDER')
    missing=sum('label_available_at_ms' not in r for r in rows)
    # Publish recipe before deriving targets, choosing models or fitting.
    plan={'schema':'heptatrader.value-net-cpu-development-plan.v1','dataset_sha256':dsha,'source_sha256':file_sha(__file__),
          'verifier_sha256':file_sha(Path(__file__).with_name('value_export_reconcile.py')),
          'features':list(FEATURES),'ridge_alpha':RIDGE_ALPHA,'fold_index_boundaries':FOLDS,'input_embargo_ms':EMBARGO_MS,
          'input_embargo_is_not_label_maturity_proof':True,'missing_label_available_at_ms':missing,
          'forecast_target':'ACTUAL_ORIGINAL_CHOSEN_SIDE_NET_USD_AFTER_REPORTED_EXECUTION_COMMISSIONS',
          'selection':'VALIDATION_MAE_ONLY_TIES_PREFER_BASELINES','automatic_deployment':False,
          'data_reuse':'PREVIOUSLY_EXPOSED_DEVELOPMENT_SET','causal_oos_allowed':False,'broker_requests':0}
    save(out/'plan.json',plan)
    x=np.asarray([vector(r['source_request']['request'],r['action']) for r in rows]);y=np.asarray([float(r['net_usd']) for r in rows])
    history_first=np.asarray([r['source_request']['request']['history_snapshot'][0]['observed_at_ms'] for r in rows])
    sides=np.asarray([r['action'] for r in rows]);outputs=[];all_predictions=[];seen=set();consumed=set()
    for i,(train_end,val_end,test_end) in enumerate(FOLDS):
        tr=np.arange(train_end);va=np.arange(train_end,val_end);te=np.arange(val_end,test_end)
        tr=tr[at[tr]+EMBARGO_MS<history_first[va].min()];va=va[at[va]+EMBARGO_MS<history_first[te].min()]
        require(len(tr)>=40 and len(va)>=10 and len(te)==30,'INPUT_PURGED_SPLIT_SIZE')
        require(not seen.intersection(te.tolist()),'TEST_ROW_REUSE');seen.update(te.tolist());consumed.update(np.r_[tr,va,te].tolist())
        model=fit(x[tr],y[tr]);mean=float(y[tr].mean());side_mean={s:float(y[tr][sides[tr]==s].mean()) if (sides[tr]==s).any() else mean for s in ('BUY','SELL')}
        def forecasts(ix):return {'zero':np.zeros(len(ix)),'training_mean':np.full(len(ix),mean),'training_side_mean':np.asarray([side_mean[s] for s in sides[ix]]),'numeric_ridge':predict(model,x[ix])}
        val=forecasts(va);test=forecasts(te);validation={name:score(y[va],p) for name,p in val.items()}
        selected=min(validation,key=lambda name:validation[name]['mae_usd'])
        metrics={name:score(y[te],p) for name,p in test.items()}
        record={'fold':i,'train_rows':len(tr),'validation_rows':len(va),'test_rows':len(te),'selected':selected,
                'train_first_ms':int(at[tr[0]]),'train_last_ms':int(at[tr[-1]]),'test_first_ms':int(at[te[0]]),'test_last_ms':int(at[te[-1]]),
                'validation':validation,'test':metrics,'training_mean_usd':mean,'training_side_mean_usd':side_mean}
        path=out/f'fold-{i}-readout.npz'
        with path.open('xb') as f:
            np.savez_compressed(f,mu=model[0],sd=model[1],weights=model[2],train_indices=tr,validation_indices=va,test_indices=te,train_times_ms=at[tr],validation_times_ms=at[va],test_times_ms=at[te],test_numeric_predictions=test['numeric_ridge'])
            f.flush();os.fsync(f.fileno())
        record['readout_sha256']=file_sha(path);outputs.append(record)
        for j,k in enumerate(te):all_predictions.append({'fold':i,'row_id':rows[k]['row_id'],'request_id':rows[k]['request_id'],'original_side':rows[k]['action'],'actual_net_usd':str(rows[k]['net_usd']),'forecast_usd':{name:float(p[j]) for name,p in test.items()},'selected_forecast_usd':float(test[selected][j])})
    yy=np.asarray([float(r['actual_net_usd']) for r in all_predictions]);aggregate={name:score(yy,np.asarray([r['forecast_usd'][name] for r in all_predictions])) for name in ('zero','training_mean','training_side_mean','numeric_ridge')}
    aggregate['validation_selected']=score(yy,np.asarray([r['selected_forecast_usd'] for r in all_predictions]))
    require(file_sha(config['dataset'])==dsha,'DATASET_CHANGED_DURING_TRAINING')
    result={'schema':'heptatrader.value-net-cpu-development-result.v1','state':'DEVELOPMENT_DIAGNOSTIC_NO_PROMOTION','plan_sha256':file_sha(out/'plan.json'),
            'verified_rows':verification['rows_checked'],'unique_executions':verification['unique_executions'],'unique_consumed_rows':len(consumed),'distinct_test_rows':len(seen),
            'readouts_fitted':len(outputs),'missing_label_availability_times':missing,'data_first_ms':int(at[0]),'data_last_ms':int(at[-1]),
            'folds':outputs,'aggregate_forecast_errors':aggregate,'prediction_rows':all_predictions,
            'not_scored':'UNCHOSEN_ACTION_COUNTERFACTUAL_PNL','causal_oos_qualified':False,'broker_orders_sent':0,'new_historical_rows':0,'new_encoder_training':False,
            'actual_trading_strategy_rerun':False,'new_prospective_windows':0,'trade_authority':False,'deployed':False}
    save(out/'result.json',result)
    print(json.dumps({k:v for k,v in result.items() if k not in ('folds','prediction_rows')},sort_keys=True),flush=True)
    return result

def main():
    p=argparse.ArgumentParser();p.add_argument('--config',required=True);a=p.parse_args();os.umask(0o077);run(json.loads(Path(a.config).read_text()))
if __name__=='__main__':main()
