"""Check frozen fit stationarity and diagnose directional vs volatility gains.
Reads the stripped development set only. Does not refit/select/promote a model.
"""
from __future__ import annotations
import argparse,math,sys
from pathlib import Path
from .common import EvidenceError,read,sha,digest,write_once


def main():
    p=argparse.ArgumentParser();p.add_argument('--config',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    cfg=read(a.config);out=a.output
    if out.exists():raise EvidenceError('AUDIT_EXISTS')
    out.mkdir(mode=0o700)
    fitroot=Path(cfg['output']);data_path=Path(cfg['input']);candidate_path=fitroot/'frozen-candidate.json'
    from sandbox import enter
    sandbox=enter({'output_dir':str(out),'period_path':str(data_path),'legacy_capture':str(data_path),'oms_path':str(data_path),'export_dir':str(data_path),
        'read_only_paths':[cfg['source_root'],str(a.config),str(fitroot),sys.prefix]})
    try:
        with open(cfg['forbidden_original'],'rb') as f:f.read(1)
    except PermissionError:sandbox['original_test_container_denied']=True
    else:raise EvidenceError('ORIGINAL_TEST_ACCESS_NOT_DENIED')
    import numpy as np
    from .calibration_model import features,predict,calibrate_apply,fit_calibration
    from .calibration_development import validate_development,split_purged
    data=read(data_path,128*1024*1024);train,val=validate_development(data)
    cal,check,_=split_purged(val,.5);c=read(candidate_path);r=read(fitroot/'result.json')
    if sha(candidate_path)!=r['candidate_sha256'] or c['candidate_id']!=digest({k:v for k,v in c.items() if k!='candidate_id'}):raise EvidenceError('FROZEN_MODEL_TAMPER')
    if sha(data_path)!=c['development_sha256'] or sha(data_path)!=cfg['input_sha256']:raise EvidenceError('DEVELOPMENT_TAMPER')
    for name,wanted in c['source_sha256'].items():
        if sha(Path(__file__).parent/name)!=wanted:raise EvidenceError('ORIGINAL_FIT_SOURCE_CHANGED:'+name)
    m=c['model'];x=features(train,m['profile']);mean=x.mean(0);std=x.std(0);std[std<1e-6]=1
    if not np.array_equal(mean,m['mean']) or not np.array_equal(std,m['std']):raise EvidenceError('NORMALIZER_NOT_TRAIN_ONLY')
    design=np.c_[np.clip((x-mean)/std,-8,8),np.ones(len(x))];w=np.asarray(m['weights']);logit=design@w
    z=np.c_[logit,np.zeros(len(x))];z-=z.max(1,keepdims=True);q=np.exp(z);q/=q.sum(1,keepdims=True)
    y=np.array([row['class'] for row in train]);target=np.eye(3)[y,:2]
    gradient=design.T@(q[:,:2]-target)/len(y);gradient[:-1]+=m['penalty']*w[:-1]
    residual=float(np.max(np.abs(gradient)))
    if residual>1e-6:raise EvidenceError('FROZEN_SOFTMAX_NOT_STATIONARY')
    yc=np.array([row['class'] for row in cal]);recal=fit_calibration(predict(m,cal),yc,m['fit_prior'])
    if recal!=c['calibration']:raise EvidenceError('CALIBRATION_NOT_REPRODUCIBLE')
    pcheck=calibrate_apply(predict(m,check),c['calibration']);ycheck=np.array([row['class'] for row in check])
    # Scalar score calculation independent of the vectorized fit/report function.
    scalar=sum(sum((float(p[j])-float(label==j))**2 for j in range(3)) for p,label in zip(pcheck,ycheck))/len(check)
    if abs(scalar-r['check_scores']['selected_calibrated']['multiclass_brier'])>1e-10:raise EvidenceError('CHECK_SCORE_MISMATCH')
    mask=ycheck!=1;up=(ycheck[mask]==2).astype(float);prob=pcheck[mask,2]/(pcheck[mask,0]+pcheck[mask,2])
    train_direction=y[y!=1];prior_up=float(np.mean(train_direction==2))
    order=np.argsort(prob,kind='mergesort');ranks=np.empty(len(prob),float);i=0
    while i<len(order):
        j=i+1
        while j<len(order) and prob[order[j]]==prob[order[i]]:j+=1
        ranks[order[i:j]]=(i+j+1)/2;i=j
    n1=int(up.sum());n0=len(up)-n1
    auc=float((ranks[up==1].sum()-n1*(n1+1)/2)/(n1*n0)) if n1 and n0 else None
    from .calibration_model import predict_value
    values=predict_value(c['value_model'],check)
    result={'schema':'heptatrader.frozen-development-diagnostic.v1','candidate_sha256':sha(candidate_path),'candidate_id':c['candidate_id'],
        'sandbox':sandbox,'fit_normalizer_recomputed_exactly':True,'softmax_gradient_inf_independent':residual,
        'calibration_recomputed_exactly':True,'scalar_brier_recomputed':scalar,'original_check_brier':r['check_scores']['selected_calibrated']['multiclass_brier'],
        'directional_diagnostic':{'scope':'CONDITIONAL_ON_REALIZED_NONFLAT_LABEL_NOT_AN_ENTRY_RULE','rows':int(mask.sum()),
            'conditional_up_probability_brier':float(np.mean((prob-up)**2)),
            'train_direction_prior_brier':float(np.mean((prior_up-up)**2)),'train_prior_up':prior_up,
            'direction_auc':auc,'direction_accuracy':float(np.mean((prob>.5)==(up==1)))},
        'flat_class_brier':float(np.mean((pcheck[:,1]-(ycheck==1))**2)),
        'net_forecast_check':{'minimum_bps':float(values.min()),'maximum_bps':float(values.max()),
            'positive_max_side_rows':int((values.max(1)>0).sum()),'total_rows':len(check),
            'actual_execution_costs_used':False,'declared_roundtrip_commission_bps':.4,'declared_roundtrip_slippage_bps':.5},
        'model_refit_performed':False,'parameters_changed':False,'old_test_rows_evaluated':0,
        'future_labels_used':False,'broker_orders_sent':0,'promotion_performed':False}
    write_once(out/'audit.json',result);print(__import__('json').dumps(result,indent=2))
if __name__=='__main__':main()
