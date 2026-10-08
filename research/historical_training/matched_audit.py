"""Reconstruct same-input readouts and costs; no new fit selection or encoder run."""
from __future__ import annotations
import argparse,hashlib,json,sys
from pathlib import Path
from .common import EvidenceError,read,sha,write_once


def verify_role_order(groups):
    from .matched_data import SIZES
    if set(groups)!=set(SIZES) or any(not groups[k] for k in SIZES):
        raise EvidenceError('MATCHED_ROLE_SET')
    ordered=[groups[k] for k in SIZES]
    if any(max(r['outcome_end'] for r in a)>=min(r['history_start'] for r in b)
           for a,b in zip(ordered,ordered[1:])):
        raise EvidenceError('MATCHED_ROLE_OVERLAP')


def audit(config):
    import numpy as np
    from safetensors.numpy import load_file
    from .matched_data import SIZES,text
    from .calibration_model import brier,fit_calibration,calibrate_apply,predict
    root=Path(config['output']);plan=read(root/'plan.json');report=read(root/'result.json');inputs=read(root/'input.json')
    if sha(root/'plan.json')!=report['plan_sha256'] or sha(root/'input.json')!=plan['input_sha256']:raise EvidenceError('PLAN_OR_INPUT_CHANGED')
    if sha(root/'features.safetensors')!=report['features_sha256'] or sha(root/'readouts.json')!=report['readouts_sha256']:raise EvidenceError('FROZEN_OUTPUT_CHANGED')
    rows=sum((inputs['groups'][k] for k in SIZES),[])
    if {k:[r['time'] for r in v] for k,v in inputs['groups'].items()}!=plan['times']:raise EvidenceError('TIME_SELECTION_CHANGED')
    if [hashlib.sha256(text(r).encode()).hexdigest() for r in rows]!=plan['text_sha256']:raise EvidenceError('TEXT_CHANGED')
    verify_role_order(inputs['groups'])
    n=SIZES['fit'];nt=n+SIZES['tune'];nc=nt+SIZES['calibration'];labels=np.asarray([r['class'] for r in rows],int)
    arrays={k:v.astype(float) for k,v in load_file(root/'features.safetensors').items()}
    arrays['numeric']=np.asarray([[round(float(x),6) for x in r['features']] for r in rows])
    heads=read(root/'readouts.json')['readouts'];reconstructed={};checks={}
    def design(x,m):return np.c_[np.clip((x-np.asarray(m['mean']))/np.asarray(m['std']),-8,8),np.ones(len(x))]
    def probs(a,w):
        z=np.c_[a@np.asarray(w),np.zeros(len(a))];z-=z.max(1,keepdims=True);p=np.exp(z);return p/p.sum(1,keepdims=True)
    base=np.bincount(labels[:nt],minlength=3)/nt
    for arm,x in arrays.items():
        h=heads[arm];m=h['model'];mean=x[:nt].mean(0);std=x[:nt].std(0);std[std<1e-6]=1
        if not np.array_equal(mean,np.asarray(m['mean'])) or not np.array_equal(std,np.asarray(m['std'])):raise EvidenceError('NON_TRAIN_NORMALIZATION')
        a=design(x,m);p=probs(a,m['weights']);target=np.eye(3)[labels[:nt],:2];w=np.asarray(m['weights'])
        g=a[:nt].T@(p[:nt,:2]-target)/nt;g[:-1]+=m['penalty']*w[:-1]
        if abs(g).max()>1e-6:raise EvidenceError('NONOPTIMAL_CLASSIFIER')
        c=fit_calibration(p[nt:nc],labels[nt:nc],base)
        if c!=h['calibration']:raise EvidenceError('CALIBRATION_REPRODUCTION_FAILED')
        reconstructed[arm]=calibrate_apply(p[nc:],c);reconstructed[arm+'_raw']=p[nc:]
        v=h['value_model'];a=design(x,v)
        target=np.asarray([[r['long_after_spread_bps']-.9,r['short_after_spread_bps']-.9] for r in rows[:nt]])
        weights=np.asarray(v['weights']);vg=a[:nt].T@(a[:nt]@weights-target)/nt;vg[:-1]+=v['penalty']*weights[:-1]
        if abs(vg).max()>1e-7:raise EvidenceError('VALUE_STATIONARITY_FAILED')
        check=report['costs'][arm]['check_selected_or_diagnostic_zero_buffer'];times={r['time']:r for r in rows[nc:]};total=0.;prior_end=-1
        for trade in check['details']:
            row=times[trade['time']]
            if trade['time']<prior_end:raise EvidenceError('OVERLAPPING_SIMULATED_TRADES')
            prior_end=row['outcome_end'];side='long' if trade['side']=='BUY' else 'short'
            net=(row[side+'_after_spread_bps']-.9)*10
            if abs(net-trade['simulated_net_usd'])>1e-9:raise EvidenceError('TRADE_COST_MISMATCH')
            total+=net
        if abs(total-check['net_usd'])>1e-9 or len(check['details'])!=check['trades']:raise EvidenceError('TOTAL_COST_MISMATCH')
        checks[arm]={'normalizer_training_only':True,'gradient_inf':float(abs(g).max()),'value_gradient_inf':float(abs(vg).max()),
            'calibration_reproduced':True,'simulated_costs_reproduced':True,'maximum_probability_difference':float(abs(reconstructed[arm]-np.asarray(report['probabilities'][arm])).max())}
    for arm in ('mmbert','english'):reconstructed[arm+'_fixed_mix']=.5*reconstructed['numeric']+.5*reconstructed[arm]
    reconstructed['train_prior']=np.tile(base,(SIZES['check'],1));reconstructed['uniform']=np.full((SIZES['check'],3),1/3)
    ref=read(config['frozen_candidate']);reconstructed['frozen_full_data_reference']=calibrate_apply(predict(ref['model'],rows[nc:]),ref['calibration'])
    scores={}
    for arm,p in reconstructed.items():
        diff=float(abs(p-np.asarray(report['probabilities'][arm])).max())
        score=sum(sum((float(q[k])-float(y==k))**2 for k in range(3)) for q,y in zip(p,labels[nc:]))/SIZES['check']
        if diff>1e-10 or abs(score-report['scores'][arm]['multiclass_brier'])>1e-10:raise EvidenceError('PREDICTION_OR_BRlER_MISMATCH')
        scores[arm]={'brier':score,'max_probability_difference':diff}
    return {'schema':'heptatrader.matched-result-reconstruction.v1','plan_sha256':sha(root/'plan.json'),'result_sha256':sha(root/'result.json'),
        'checks':checks,'scores':scores,'frozen_features_hash_verified':True,'encoders_recomputed':False,
        'old_test_rows_used':0,'new_model_selected':False,'frozen_reference_changed':False,'broker_orders_sent':0,
        'scope':'MATCHED_DEVELOPMENT_RESULT_AUDIT_NOT_INDEPENDENT_FUTURE_EFFICACY'}


def main():
    p=argparse.ArgumentParser();p.add_argument('--config',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    r=audit(read(a.config));write_once(a.output,r);print(json.dumps(r,indent=2))
if __name__=='__main__':main()
