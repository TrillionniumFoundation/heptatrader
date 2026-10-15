"""Finite nonlinear/ridge exploratory FX study; historical bars only.

No synthetic rewards, no broker orders, no candidate selection for trading.
Chronological non-overlapping test positions and 48-bar input/label gap
checks. Threshold/regularization are always fixed on preceding validation.
"""
from __future__ import annotations
import argparse,hashlib,json,os,time
from pathlib import Path
import numpy as np
from .cost_horizon_study import parse,split_indices,score,HORIZONS,COSTS,NOTIONAL,COMMISSION_BPS

LAGS=(1,2,3,6,12,24,48)
ALPHAS=(1.,100.,10000.)
THRESHOLDS=(0.,2.,4.,6.,8.,12.,20.,30.)
NOISE_COST=2.  # 2 roundtrip bps EXTRA slippage, atop executable sides
MAX_FEATURE=500.

def matrices(times,mid,midopen,bidopen,askopen,spread,span,h):
    n=len(times);idx=np.arange(48,n-h-1,dtype=np.int64)
    cont=(times[idx]-times[idx-48]==48*300)&(times[idx+h+1]-times[idx+1]==h*300)&(times[idx+1]-times[idx]==300)
    idx=idx[cont]
    price=np.log(mid)
    rets=np.column_stack([10000*(price[idx]-price[idx-k]) for k in LAGS])
    unit=np.diff(price,prepend=price[0])
    vol6=np.sqrt(np.mean(np.column_stack([unit[idx-j]**2 for j in range(6)]),axis=1))*10000
    vol24=np.sqrt(np.mean(np.column_stack([unit[idx-j]**2 for j in range(24)]),axis=1))*10000
    spreadbps=10000*spread[idx]/mid[idx]
    ranges=10000*span[idx]/mid[idx]
    phase=2*np.pi*(times[idx]%86400)/86400
    basic=np.column_stack([rets,vol6,vol24,spreadbps,ranges,np.sin(phase),np.cos(phase)])
    # A small predeclared nonlinear family: oscillation, volatility interaction
    # and a smooth bounded trend transform. No fitted stochastic embeddings.
    nonlinear=np.column_stack([basic,
      np.tanh(rets[:,3]/4),np.tanh(rets[:,5]/8),
      np.sign(rets[:,3]),np.sign(rets[:,5]),
      rets[:,3]*vol24/20,rets[:,5]*vol24/20,
      rets[:,3]*rets[:,5]/50,
      np.abs(rets[:,3]),np.abs(rets[:,5])])
    target=10000*np.log(midopen[idx+h+1]/midopen[idx+1])
    longbps=10000*np.log(bidopen[idx+h+1]/askopen[idx+1])
    shortbps=10000*np.log(bidopen[idx+1]/askopen[idx+h+1])
    if not all(np.isfinite(v).all() for v in (basic,nonlinear,target,longbps,shortbps)):
        raise ValueError('NONFINITE_RESEARCH_BARS')
    return idx,np.clip(basic,-MAX_FEATURE,MAX_FEATURE),np.clip(nonlinear,-MAX_FEATURE,MAX_FEATURE),target,longbps,shortbps,spreadbps

def trained_predictions(X,y,tr,va,te):
    m=X[tr].mean(axis=0);scale=X[tr].std(axis=0)
    scale=np.where(scale>=1e-7,scale,1.)
    z=(X-m)/scale;train=z[tr];mean=float(y[tr].mean())
    gram=train.T@train;rhs=train.T@(y[tr]-mean)
    best=None
    for alpha in ALPHAS:
        weight=np.linalg.solve(gram+alpha*np.eye(gram.shape[0]),rhs)
        value=mean+z[va]@weight
        loss=float(np.mean((value-y[va])**2))
        if best is None or loss<best[0]:best=(loss,alpha,weight)
    return best[1],mean+z[va]@best[2],mean+z[te]@best[2]

def threshold_on_validation(pred,spread,longbps,shortbps):
    best=None
    for th in THRESHOLDS:
        take=np.abs(pred)>spread+COMMISSION_BPS+NOISE_COST+th
        if int(take.sum())<25:continue
        chosen=np.where(pred[take]>0,longbps[take],shortbps[take])
        net=float(np.sum(chosen-COMMISSION_BPS-NOISE_COST))
        if net>0 and (best is None or net>best[0]):best=(net,th)
    return None if best is None else best[1]

def evaluate(path,coverage_path):
    t,mid,mo,bo,ao,spread,span=parse(path)
    coverage=json.loads(Path(coverage_path).read_bytes())
    if not coverage.get('full_requested_coverage_proven') or coverage.get('bar_count')!=len(t):
        raise ValueError('UNPROVEN_DATASET')
    folds=[]
    for h in HORIZONS:
        idx,basic,nonlinear,y,lb,sb,sp=matrices(t,mid,mo,bo,ao,spread,span,h)
        rows=np.arange(len(idx),dtype=np.int64);n=len(t)
        for fold in range(4):
            a=int(n*(.5+fold*.1));b=int(n*(.6+fold*.1));c=min(n,int(n*(.7+fold*.1)))
            tr=rows[idx+h+1<a-48]
            va=rows[(idx>=a)&(idx+h+1<b-48)][::h+1]
            te=rows[(idx>=b)&(idx+h+1<c)][::h+1]
            if min(len(tr),len(va),len(te))<35:raise ValueError('NONLINEAR_STUDY_INSUFFICIENT_ROWS')
            assert max(idx[tr])+h+1<min(idx[va]) and max(idx[va])+h+1<min(idx[te])
            entries={}
            for name,X in (('engineered_linear',basic),('nonlinear_ridge',nonlinear)):
                alpha,pva,pte=trained_predictions(X,y,tr,va,te)
                cutoff=threshold_on_validation(pva,sp[va],lb[va],sb[va])
                scenario=[]
                for extra in COSTS:
                    result=({'trades':0,'net_usd':0.,'wins':0,'mean_bps':None,'max_loss_bps':None}
                            if cutoff is None
                            else score(pte,sp[te],lb[te],sb[te],cutoff,COMMISSION_BPS+extra))
                    scenario.append({'extra_slippage_bps':extra,**result})
                entries[name]={'alpha_from_validation':alpha,
                    'validation_threshold_bps':cutoff,'test_scenarios':scenario}
            folds.append({'horizon_minutes':5*h,'fold':fold,
              'training_rows':len(tr),'validation_nonoverlap_rows':len(va),
              'test_nonoverlap_rows':len(te),
              'test_first_time_epoch':int(t[idx[te[0]]]),
              'test_last_time_epoch':int(t[idx[te[-1]]]),
              'notrade_baseline_net_usd':0.,'arms':entries})
    qualifications={}
    for h in HORIZONS:
        for model in ('engineered_linear','nonlinear_ridge'):
            x=[f for f in folds if f['horizon_minutes']==5*h]
            stressed=[f['arms'][model]['test_scenarios'][2] for f in x]
            qualified=(all(s['net_usd']>0 for s in stressed)
                  and sum(s['trades'] for s in stressed)>=100
                  and sum(s['net_usd'] for s in stressed)>=200)
            qualifications[f'{5*h}:{model}']={'research_cost_robust_screen':qualified,
              'stress_net_usd':sum(s['net_usd'] for s in stressed),
              'stress_trades':sum(s['trades'] for s in stressed)}
    return {'schema':'heptatrader.nonlinear-fx-cost-study.v1',
            'at_ms':int(time.time()*1000),'source_sha256':hashlib.sha256(Path(path).read_bytes()).hexdigest(),
            'dataset_timestamp_count':len(t),'historical_only':True,
            'no_prospective_unseen_claim':True,
            'trade_authority':False,'broker_orders_sent':0,'automatic_promotion':False,
            'entry_exit':'NEXT_BAR_OPEN_SIMULATED_NOT_GUARANTEED_EXECUTION',
            'reference_notional_usd':NOTIONAL,
            'commission_assumed_bps':COMMISSION_BPS,
            'extra_slippage_bps':list(COSTS),
            'validation_selection_cost_bps':NOISE_COST,
            'folds':folds,'candidate_screens':qualifications}

def main():
    a=argparse.ArgumentParser();a.add_argument('--bars',required=True)
    a.add_argument('--coverage',required=True);a.add_argument('--output',required=True)
    args=a.parse_args();os.umask(0o077)
    out=Path(args.output)
    if out.exists():raise ValueError('NO_OVERWRITE')
    result=evaluate(args.bars,args.coverage)
    from .common import write_once
    write_once(out,result)
    print(json.dumps(result['candidate_screens']),flush=True)
if __name__=='__main__':main()
