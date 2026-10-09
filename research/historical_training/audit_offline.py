"""Independent reconstruction of an EXISTING offline result, not a new trial.
No encoder invocation, broker traffic, tuning on test, or promotion authority.
"""
from __future__ import annotations
import argparse,hashlib,json,math,time
from pathlib import Path
from .common import EvidenceError,read,sha,write_once,digest

def check_probability(p):
    if len(p)!=3 or any(isinstance(x,bool) or not isinstance(x,(int,float)) or not math.isfinite(x) or x<0 for x in p):
        raise EvidenceError('INVALID_PROBABILITY')
    if abs(sum(p)-1)>1e-8:raise EvidenceError('PROBABILITY_SUM')

def observed_brier(p,label):
    check_probability(p)
    if type(label) is not int or label not in (0,1,2):raise EvidenceError('INVALID_CLASS')
    return sum((x-(int(label==i)))**2 for i,x in enumerate(p))

def training_prior(rows):
    if not rows:raise EvidenceError('EMPTY_TRAINING')
    counts=[0,0,0]
    for row in rows:
        k=row['class']
        if type(k) is not int or k not in (0,1,2):raise EvidenceError('INVALID_CLASS')
        counts[k]+=1
    return [n/len(rows) for n in counts]

def fixed_trade_stress(trades,extra_bps):
    if not math.isfinite(extra_bps) or extra_bps<0:raise EvidenceError('INVALID_ADVERSE_COST')
    identities=[(r['time'],r['side']) for r in trades]
    if len(set(identities))!=len(identities):raise EvidenceError('DUPLICATE_TRADE_IDENTITY')
    values=[]
    for row in trades:
        value=row['simulated_net_usd']
        if not math.isfinite(value) or row['side'] not in ('BUY','SELL'):raise EvidenceError('INVALID_TRADE')
        values.append(value-extra_bps*10)
    return {'trades':len(trades),'trade_identity_sha256':digest(identities),
        'extra_roundtrip_slippage_bps':extra_bps,'net_usd':sum(values),
        'trade_set_fixed':True,'gate_recomputed':False,'actual_execution_reward':False}

def input_text(row):
    v=[round(float(x),6) for x in row['features']]
    if len(v)!=25 or not all(math.isfinite(x) for x in v):raise EvidenceError('INVALID_FEATURE_VECTOR')
    known={'relative_completed_closes_bps':v[:11],'completed_spreads_bps':v[11:23],'utc_hour_sine_cosine':v[23:]}
    return 'EUR/USD five-minute historical price inputs, oldest first. Forecast the next 15-minute midpoint return class (below -1 bp, within +/-1 bp, above +1 bp). '+json.dumps(known,sort_keys=True,separators=(',',':'))

def audit(config):
    import numpy as np
    from safetensors.numpy import load_file
    root=Path(config['output_root']);plan=read(root/'plan.json');result=read(root/'result.json')
    dataset=Path(config['dataset']);data=read(dataset,256*1024*1024)
    if sha(dataset)!=config['dataset_sha256'] or sha(dataset)!=plan['dataset_sha256']:
        raise EvidenceError('DATASET_CHANGED')
    if digest({k:v for k,v in data.items() if k!='dataset_id'})!=data['dataset_id'] or data['dataset_id']!=plan['dataset_id']:
        raise EvidenceError('DATASET_ID')
    if sha(root/'plan.json')!=result['plan_sha256']:raise EvidenceError('PLAN_CHANGED')
    if data.get('trade_authority') is not False or result.get('actual_execution_rewards_used')!=0:
        raise EvidenceError('OFFLINE_SCOPE_REQUIRED')
    for file,key in [('paired-frozen-features.safetensors','feature_file_sha256'),('fitted-readouts.npz','readout_file_sha256')]:
        if sha(root/file)!=result[key]:raise EvidenceError('ARTIFACT_CHANGED:'+file)
    groups={};all_rows=[]
    for name in ('train','validation','test'):
        source=data['groups'][name];count=plan['sample_sizes'][name]
        if count<2 or count>len(source):raise EvidenceError('SAMPLE_SIZE')
        chosen=[source[i*(len(source)-1)//(count-1)] for i in range(count)]
        if [r['time'] for r in chosen]!=plan['sample_times'][name]:raise EvidenceError('SAMPLE_SELECTION_CHANGED')
        if any(b['time']<=a['time'] for a,b in zip(source,source[1:])):raise EvidenceError('NONINCREASING_DATASET')
        groups[name]=chosen;all_rows.extend(chosen)
    for left,right in [('train','validation'),('validation','test')]:
        if max(r['outcome_end'] for r in data['groups'][left])>=min(r['history_start'] for r in data['groups'][right]):
            raise EvidenceError('FULL_SPAN_TEMPORAL_OVERLAP')
    if [hashlib.sha256(input_text(r).encode()).hexdigest() for r in all_rows]!=plan['text_hashes']:
        raise EvidenceError('INPUT_TEXT_CHANGED')
    encoded=load_file(root/'paired-frozen-features.safetensors')
    arrays={'numeric':np.asarray([[round(float(x),6) for x in r['features']] for r in all_rows])}
    for arm in ('mmbert','english'):
        v=encoded[arm]
        if v.shape!=(len(all_rows),plan['projection_width']) or not np.isfinite(v).all():raise EvidenceError('FEATURE_SHAPE_OR_FINITE')
        arrays[arm]=v.astype(np.float64)
    n=len(groups['train']);v=len(groups['validation']);labels=np.array([r['class'] for r in all_rows]);eye=np.eye(3)
    models=np.load(root/'fitted-readouts.npz',allow_pickle=False)
    reproduced={};refits={}
    def simplex(raw):
        raw=np.clip(raw,1e-6,None)
        return raw/np.sum(raw,axis=1,keepdims=True)
    for arm,x in arrays.items():
        mean=x[:n].mean(axis=0);std=x[:n].std(axis=0);std[std<1e-6]=1
        design=np.column_stack(((x-mean)/std,np.ones(len(x))))
        train=design[:n];validation=design[n:n+v];target=eye[labels[:n]]
        candidates=[]
        for alpha in plan['alpha_grid']:
            penalty=np.eye(train.shape[1])*alpha;penalty[-1,-1]=0
            weights=np.linalg.solve(train.T@train+penalty,train.T@target)
            score=float(np.sum((simplex(validation@weights)-eye[labels[n:n+v]])**2,axis=1).mean())
            candidates.append((score,alpha,weights))
        score,alpha,w=min(candidates,key=lambda item:item[0])
        if alpha!=result['heads'][arm]['alpha']:raise EvidenceError('VALIDATION_SELECTION_MISMATCH')
        for name,value in [('weights',w),('mean',mean),('std',std)]:
            if not np.allclose(value,models[arm+'_'+name],rtol=1e-8,atol=1e-9):raise EvidenceError('TRAIN_ONLY_REFIT_MISMATCH:'+arm+':'+name)
        reproduced[arm]=simplex(design[n+v:]@w)
        refits[arm]={'train_only_normalization_and_fit_reproduced':True,'selected_alpha':alpha,'validation_brier':score}
    for arm in ('mmbert','english'):
        reproduced[arm+'_fixed_mix']=(reproduced['numeric']+reproduced[arm])/2
    checks={};stress={};test=groups['test'];time_index={r['time']:r for r in test}
    for arm,pred in reproduced.items():
        if not np.allclose(pred,result['test_predictions'][arm],rtol=1e-8,atol=1e-9):raise EvidenceError('PREDICTION_RECONSTRUCTION_MISMATCH:'+arm)
        value=sum(observed_brier(p.tolist(),int(row['class'])) for p,row in zip(pred,test))/len(test)
        if abs(value-result['scores'][arm]['test_brier'])>1e-8:raise EvidenceError('BRIER_MISMATCH')
        checks[arm]={'test_brier':value,'prediction_max_abs_difference':float(np.max(np.abs(pred-np.array(result['test_predictions'][arm]))))}
        for case in result['scores'][arm]['simulated_cost_scenarios']:
            total=0.;previous=-1
            for trade in case['trade_details']:
                row=time_index[trade['time']]
                if row['time']<previous:raise EvidenceError('OVERLAPPING_SIMULATED_TRADES')
                previous=row['outcome_end']
                gross=row['long_after_spread_bps'] if trade['side']=='BUY' else row['short_after_spread_bps']
                net=(gross-case['roundtrip_commission_bps']-case['roundtrip_slippage_bps'])*10
                if abs(net-trade['simulated_net_usd'])>1e-8:raise EvidenceError('COST_DETAIL_MISMATCH')
                total+=net
            if len(case['trade_details'])!=case['trades'] or abs(total-case['net_usd'])>1e-8:raise EvidenceError('COST_TOTAL_MISMATCH')
        base=next(c for c in result['scores'][arm]['simulated_cost_scenarios'] if c['roundtrip_slippage_bps']==0)
        stress[arm]=[fixed_trade_stress(base['trade_details'],extra) for extra in (0.,.5,1.,2.)]
    prior=training_prior(groups['train']);baselines={}
    for name,p in [('uniform',[1/3]*3),('training_class_prior',prior)]:
        baselines[name]={'probabilities':p,'test_brier':sum(observed_brier(p,r['class']) for r in test)/len(test),
            'defined_after_original_test_seen':True,'tuned_on_test':False,'purpose':'DIAGNOSTIC_BASELINE_NOT_NEW_EFFICACY_TRIAL'}
    return {'schema':'heptatrader.offline-independent-reconstruction.v1','at_ms':int(time.time()*1000),
        'original_result_sha256':sha(root/'result.json'),'original_plan_sha256':sha(root/'plan.json'),
        'dataset_sha256':sha(dataset),'refits':refits,'verified_scores':checks,'additional_baselines':baselines,
        'fixed_trade_cost_stress':stress,'encoder_features_recomputed':False,'encoder_features_hash_verified':True,
        'test_already_observed':True,'new_independent_test_claimed':False,'original_test_result_modified':False,
        'numeric_baseline_beats_both_simple_baselines':all(checks['numeric']['test_brier']<r['test_brier'] for r in baselines.values()),
        'broker_orders_sent':0,'promotion_authority':False,'new_candidate_trained':False,
        'state':'RECONSTRUCTION_PASSED_EXISTING_RESULT_NOT_NEW_HOLDOUT'}

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--config',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();r=audit(read(args.config));write_once(args.output,r)
    print(json.dumps({k:v for k,v in r.items() if k!='fixed_trade_cost_stress'},indent=2))
if __name__=='__main__':main()
