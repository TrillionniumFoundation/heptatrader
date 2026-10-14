"""One-shot historical-incremental holdout relative to the PREVIOUS dataset.

All fits, regularization and abstention thresholds use only bars whose
outcomes finish before the old data cutoff. Newly backfilled history is read
only for later evaluation. Historical revision != prospective trial.
"""
from __future__ import annotations
import argparse,json,os,time
from pathlib import Path
import numpy as np
from .common import read,sha,write_once
from .cost_horizon_study import (
    parse,features,predictions,score,select,HORIZONS,COSTS,COMMISSION_BPS,NOTIONAL
)

def evaluate(old_path,joined_path,proof_path):
    old=read(old_path);joined=read(joined_path);proof=read(proof_path)
    oldlast=old['bars'][-1]['time']
    newtimes={x['time'] for x in joined['bars'] if x['time']>oldlast}
    if (proof['overlap_bars_identical']<1 or proof['revision_conflicts']!=0
         or proof['new_bars_from_increment']!=len(newtimes)
         or proof['new_total_bars']!=len(joined['bars'])):
        raise ValueError('NEW_HISTORY_PROOF_OR_TIME_CONFLICT')
    t,mid,mo,bo,ao,spread,span=parse(joined_path)
    n=len(t);cutoff=np.searchsorted(t,oldlast)
    if cutoff>=n or t[cutoff]!=oldlast:raise ValueError('OLD_TRAINING_CUTOFF_CHANGED')
    outcomes=[]
    for h in HORIZONS:
        i=np.arange(24,n-h-1,dtype=np.int64)
        mask=(t[i]-t[i-24]==24*300)&(t[i+1]-t[i]==300)&(t[i+h+1]-t[i+1]==h*300)
        idx=i[mask]
        X=np.zeros((n,10));X[idx],sp=features(mid,spread,span,t,idx)
        side_sp=np.zeros(n);side_sp[idx]=sp
        y=np.zeros(n);y[idx]=10000*np.log(mo[idx+h+1]/mo[idx+1])
        lb=np.zeros(n);sb=np.zeros(n)
        lb[idx]=10000*np.log(bo[idx+h+1]/ao[idx+1])
        sb[idx]=10000*np.log(bo[idx+1]/ao[idx+h+1])
        val_start=int(cutoff*.8);train=idx[idx+h+1<val_start-24]
        val=idx[(idx>=val_start)&(idx+h+1<cutoff-1)][::h+1]
        test=idx[idx>cutoff][::h+1]
        if len(train)<100 or len(val)<20 or len(test)<20:
            raise ValueError('NEW_HOLDOUT_INSUFFICIENT:'+str(h))
        assert max(train)+h+1<min(val)
        assert max(val)+h+1<min(test)
        alpha,pv,pt=predictions(X,y,train,val,test)
        comps={}
        for name,vpred,tpred in [('ridge',pv,pt),('momentum',X[val,2],X[test,2]),
                                 ('mean_revert',-X[val,2],-X[test,2])]:
            threshold=select(vpred,side_sp[val],lb[val],sb[val],COMMISSION_BPS+0.5)
            scenarios=[]
            for slip in COSTS:
                risk=COMMISSION_BPS+slip
                if threshold is None:
                    stat={'trades':0,'net_usd':0.0,'wins':0,'mean_bps':None,'max_loss_bps':None}
                else:
                    stat=score(tpred,side_sp[test],lb[test],sb[test],threshold,risk)
                scenarios.append({'slippage_bps':slip,**stat})
            comps[name]={'validation_threshold_bps':threshold,'test_cost_scenarios':scenarios}
        outcomes.append({
            'horizon_minutes':h*5,'train_rows':len(train),
            'validation_nonoverlap_rows':len(val),'test_nonoverlap_rows':len(test),
            'frozen_old_cutoff_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime(oldlast)),
            'first_new_test_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime(int(t[test[0]]))),
            'last_new_test_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime(int(t[test[-1]]))),
            'ridge_alpha_selected_without_new_data':alpha,
            'no_trade_net_usd':0,'comparisons':comps})
    return {'schema':'heptatrader.incremental-historical-holdout-cost.v1',
            'at_ms':int(time.time()*1000),'old_dataset_sha256':sha(old_path),
            'new_dataset_sha256':sha(joined_path),'merge_proof_sha256':sha(proof_path),
            'new_bars_count':len(newtimes),'old_data_cutoff_epoch':int(oldlast),
            'head_of_sample_forward_oos':False,
            'new_to_prior_training_but_historically_revised':True,
            'live_fill_evidence':False,'automatic_promotion':False,
            'trade_authority':False,'broker_orders_sent':0,
            'roundtrip_commission_bps_assumed':COMMISSION_BPS,
            'roundtrip_slippage_stress_bps':list(COSTS),
            'entry_exit':'NEXT_BAR_OPEN_SIMULATION_NOT_GUARANTEED_FILL',
            'net_usd_reference_notional':NOTIONAL,
            'horizons':outcomes}
def main():
    p=argparse.ArgumentParser()
    p.add_argument('--old',type=Path,required=True);p.add_argument('--joined',type=Path,required=True)
    p.add_argument('--proof',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();res=evaluate(a.old,a.joined,a.proof);a.output.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    if a.output.exists():raise ValueError('NEW_RESULT_ONLY')
    os.umask(0o077);write_once(a.output,res)
    print(json.dumps([{'horizon':x['horizon_minutes'],'test_rows':x['test_nonoverlap_rows'],
        'ridge':x['comparisons']['ridge']['test_cost_scenarios'][1]['net_usd'],
        'momentum':x['comparisons']['momentum']['test_cost_scenarios'][1]['net_usd'],
        'mean_revert':x['comparisons']['mean_revert']['test_cost_scenarios'][1]['net_usd']}
        for x in res['horizons']]))
if __name__=='__main__':main()
