"""Equal-budget classifier/calibration and cost-sensitive paired development audit."""
from __future__ import annotations
import argparse,json,sys,time
from pathlib import Path
from .common import EvidenceError,read,sha,digest,write_once
from .matched_data import SIZES


def probability(model,x):
    import numpy as np
    from .calibration_model import design,softmax
    a=design(x,model['mean'],model['std'])
    return softmax(np.c_[a@np.asarray(model['weights']),np.zeros(len(a))])


def fit_value(x,rows,penalty):
    import numpy as np
    from .calibration_model import normalize_fit,design
    mean,std=normalize_fit(x);a=design(x,mean,std)
    y=np.asarray([[r['long_after_spread_bps']-.9,r['short_after_spread_bps']-.9] for r in rows],float)
    if not np.isfinite(y).all():raise EvidenceError('NONFINITE_SIMULATED_TARGET')
    reg=np.eye(a.shape[1])*penalty;reg[-1,-1]=0
    w=np.linalg.solve(a.T@a/len(a)+reg,a.T@y/len(a))
    return {'mean':mean.tolist(),'std':std.tolist(),'weights':w.tolist(),'penalty':penalty,'actual_execution_rewards_used':0}


def value(model,x):
    import numpy as np
    from .calibration_model import design
    return design(x,model['mean'],model['std'])@np.asarray(model['weights'])


def fit(config,config_path):
    out=Path(config['output']);plan=read(out/'plan.json');encoded=read(out/'encoder-receipt.json')
    if (out/'result.json').exists():raise EvidenceError('MATCHED_RESULT_EXISTS')
    from sandbox import enter
    restriction=enter({'output_dir':str(out),'period_path':str(out/'input.json'),'legacy_capture':str(out/'input.json'),
        'oms_path':str(out/'input.json'),'export_dir':str(out/'input.json'),
        'read_only_paths':[config['source_root'],str(config_path),config['frozen_candidate'],sys.prefix]})
    write_once(out/'fit-sandbox.json',restriction)
    if sha(out/'input.json')!=plan['input_sha256'] or sha(out/'features.safetensors')!=encoded['features_sha256']:
        raise EvidenceError('MATCHED_FEATURE_OR_INPUT_CHANGED')
    if sha(out/'plan.json')!=encoded['plan_sha256']:raise EvidenceError('MATCHED_PLAN_CHANGED')
    import numpy as np
    from safetensors.numpy import load_file
    from .calibration_model import fit_logistic,prior,brier,fit_calibration,calibrate_apply,reliability,predict
    from .calibration_development import bootstrap_days,trade_summary
    data=read(out/'input.json');rows=sum((data['groups'][k] for k in SIZES),[])
    arrays={k:v.astype(np.float64) for k,v in load_file(out/'features.safetensors').items()}
    arrays['numeric']=np.asarray([[round(float(x),6) for x in r['features']] for r in rows],float)
    n=SIZES['fit'];nt=n+SIZES['tune'];nc=nt+SIZES['calibration'];y=np.asarray([r['class'] for r in rows],int)
    base=prior(y[:nt]);probs={};heads={};costs={}
    target=lambda rs:np.asarray([[r['long_after_spread_bps']-.9,r['short_after_spread_bps']-.9] for r in rs])
    for arm,x in arrays.items():
        if x.shape[0]!=len(rows) or not np.isfinite(x).all():raise EvidenceError('MATCHED_ARRAY_SHAPE')
        trials=[]
        for penalty in plan['penalties']:
            m=fit_logistic(x[:n],y[:n],penalty)
            trials.append({'penalty':penalty,'tune_brier':float(brier(probability(m,x[n:nt]),y[n:nt]).mean())})
        selected=min(trials,key=lambda t:t['tune_brier']);m=fit_logistic(x[:nt],y[:nt],selected['penalty'])
        c=fit_calibration(probability(m,x[nt:nc]),y[nt:nc],base)
        probs[arm]=calibrate_apply(probability(m,x[nc:]),c);probs[arm+'_raw']=probability(m,x[nc:])
        vtrials=[]
        for penalty in plan['value_penalties']:
            v=fit_value(x[:n],rows[:n],penalty)
            vtrials.append({'penalty':penalty,'tune_mse':float(np.mean((value(v,x[n:nt])-target(rows[n:nt]))**2))})
        selectedv=min(vtrials,key=lambda t:t['tune_mse']);v=fit_value(x[:nt],rows[:nt],selectedv['penalty'])
        policies=[trade_summary(rows[nt:nc],value(v,x[nt:nc]),b) for b in plan['buffer_grid_bps']]
        eligible=[p for p in policies if p['trades']>=30 and p['net_usd']>0]
        policy=max(eligible,key=lambda p:p['net_usd']) if eligible else None
        check=trade_summary(rows[nc:],value(v,x[nc:]),policy['buffer_bps'] if policy else 0.)
        costs[arm]={'calibration_policies':policies,'admitted_policy':policy is not None,'selected_buffer':policy['buffer_bps'] if policy else None,
            'check_selected_or_diagnostic_zero_buffer':check,'no_trade_net_usd':0.,'economic_qualification':False,
            'fixed_trade_stress':[{'extra_bps':e,'trades':check['trades'],'net_usd':check['net_usd']-check['trades']*10*e,
                'same_time_side_sha256':digest([(t['time'],t['side']) for t in check['details']])} for e in (0.,.5,1.,2.)]}
        heads[arm]={'model':m,'calibration':c,'tune_trials':trials,'value_model':v,'value_trials':vtrials}
    for arm in ('mmbert','english'):probs[arm+'_fixed_mix']=.5*probs['numeric']+.5*probs[arm]
    probs['train_prior']=np.tile(base,(SIZES['check'],1));probs['uniform']=np.full((SIZES['check'],3),1/3)
    reference=read(config['frozen_candidate'])
    if sha(config['frozen_candidate'])!='538dd502589cf4ddad56c0dc2c13b7f75f5da402f2d53f08983b09fc6a377466':raise EvidenceError('FROZEN_REFERENCE_CHANGED')
    probs['frozen_full_data_reference']=calibrate_apply(predict(reference['model'],rows[nc:]),reference['calibration'])
    scores={}
    for arm,p in probs.items():
        scores[arm]=reliability(p,y[nc:]);scores[arm]['paired_vs_matched_numeric']=bootstrap_days(rows[nc:],brier(probs['numeric'],y[nc:])-brier(p,y[nc:]))
    write_once(out/'readouts.json',{'profile':data['profile'],'readouts':heads,'trade_authority':False})
    result={'schema':'heptatrader.matched-lead1-result.v1','at_ms':int(time.time()*1000),'plan_sha256':sha(out/'plan.json'),
        'features_sha256':sha(out/'features.safetensors'),'readouts_sha256':sha(out/'readouts.json'),'profile':data['profile'],
        'role_sizes':SIZES,'scores':scores,'costs':costs,'encoders':encoded['encoders'],
        'probabilities':{k:v.tolist() for k,v in probs.items()},'old_test_rows_evaluated':0,
        'frozen_reference_sha256':sha(config['frozen_candidate']),'frozen_candidate_changed':False,'cpu_only':True,
        'state':'MATCHED_DEVELOPMENT_COMPARISON_NOT_FUTURE_EFFICACY','new_prospective_registration':False,'broker_orders_sent':0,'promotion_performed':False}
    write_once(out/'result.json',result)
    print(json.dumps({'brier':{k:v['multiclass_brier'] for k,v in scores.items()},'role_sizes':SIZES,
        'costs':{k:{'admitted':v['admitted_policy'],'trades':v['check_selected_or_diagnostic_zero_buffer']['trades'],
            'net':v['check_selected_or_diagnostic_zero_buffer']['net_usd']} for k,v in costs.items()},'state':result['state']},indent=2),flush=True)


def main():
    p=argparse.ArgumentParser();p.add_argument('--config',type=Path,required=True);a=p.parse_args();fit(read(a.config),a.config)
if __name__=='__main__':main()
