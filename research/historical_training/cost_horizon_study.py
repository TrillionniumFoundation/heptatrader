"""Historical FX development experiment: forward cost-aware 15/30/60 min.

Uses only completed 5m bid, ask and midpoint bars. Bar-close t features predict
a next-bar-open entry, held h bars, liquidated at future bar open. These opens
are simulated proxies, not guaranteed fills. Research only, never an order.
"""
from __future__ import annotations
import argparse,hashlib,json,math,os,time
from pathlib import Path
import numpy as np

HORIZONS=(3,6,12)
COSTS=(0.,0.5,2.)
COMMISSION_BPS=0.4
NOTIONAL=100000
EXTRA_THRESHOLDS=(0.,2.,4.,6.,10.,15.,20.,30.)
ALPHAS=(1.,100.,10000.)
FIELDS=('t','mid_close','mid_open','bid_open','ask_open','spread_close','bar_range')
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def parse(path):
    body=json.loads(Path(path).read_bytes())
    if body.get('actual_execution_rewards',0)!=0:raise ValueError('HISTORICAL_BAR_SCOPE')
    bars=body['bars'];n=len(bars)
    times=np.array([int(b['time']) for b in bars],dtype=np.int64)
    if n<1000 or not np.all(np.diff(times)>0):raise ValueError('CHRONOLOGY_OR_SIZE')
    def arr(k1,k2):return np.array([float(b[k1][k2]) for b in bars],dtype=float)
    bidopen=arr('bid','open');askopen=arr('ask','open')
    midopen=arr('midpoint','open');midclose=arr('midpoint','close')
    spreadclose=arr('ask','close')-arr('bid','close')
    high=arr('ask','high');low=arr('bid','low')
    if not (np.all(bidopen>0) and np.all(askopen>=bidopen) and np.all(midclose>0)
            and np.all(spreadclose>=0) and np.all(high>=low)):
        raise ValueError('CROSSED_OR_INVALID_SIDE_PRICES')
    return times,midclose,midopen,bidopen,askopen,spreadclose,high-low
def features(mid,spread,span,times,idx):
    ret=[10000*np.log(mid[idx]/mid[idx-k]) for k in (1,2,3,6,12,24)]
    sp=10000*spread[idx]/mid[idx]
    rg=10000*span[idx]/mid[idx]
    tod=(times[idx]%86400)/86400*2*np.pi
    X=np.column_stack(ret+[sp,rg,np.sin(tod),np.cos(tod)])
    return X,sp
def fold_bounds(n,fold):
    a=int(n*(0.50+0.10*fold));b=int(n*(0.60+0.10*fold));c=min(n,int(n*(0.70+0.10*fold)))
    return a,b,c
def split_indices(valid_i,n,h,fold):
    a,b,c=fold_bounds(n,fold)
    # Label horizon must finish before the first sample of the next segment.
    train=valid_i[valid_i+h+1<a-12]
    val=valid_i[(valid_i>=a) & (valid_i+h+1<b-12)]
    test=valid_i[(valid_i>=b) & (valid_i+h+1<c)]
    # No overlapping held inventory within validation and test.
    return train,val[::h+1],test[::h+1]
def predictions(x,y,train,val,test):
    mu=x[train].mean(axis=0);std=x[train].std(axis=0);std=np.where(std>1e-9,std,1)
    z=(x-mu)/std
    zz=z[train];mean=float(y[train].mean())
    best=None
    for alpha in ALPHAS:
        weights=np.linalg.solve(zz.T@zz+alpha*np.eye(zz.shape[1]),zz.T@(y[train]-mean))
        err=float(np.mean((mean+z[val]@weights-y[val])**2))
        if best is None or err<best[0]:best=(err,alpha,weights)
    return best[1],mean+z[val]@best[2],mean+z[test]@best[2]
def score(signal,sp,longbps,shortbps,threshold,friction):
    take=np.abs(signal)>sp+friction+threshold
    sides=np.where(signal>0,1,-1)
    raw=np.where(sides>0,longbps,shortbps)
    net=(raw-friction)[take]
    return {'trades':int(take.sum()),'mean_bps':float(net.mean()) if len(net) else None,
            'net_usd':float(net.sum()*NOTIONAL/10000),'wins':int((net>0).sum()),
            'max_loss_bps':float(net.min()) if len(net) else None}
def select(signal,sp,longbps,shortbps,friction):
    options=[(th,score(signal,sp,longbps,shortbps,th,friction)) for th in EXTRA_THRESHOLDS]
    viable=[(th,ob) for th,ob in options if ob['trades']>=20 and ob['net_usd']>0]
    if not viable:return None
    return max(viable,key=lambda x:x[1]['net_usd'])[0]
def run(bars_path,coverage_path):
    times,mid,mo,bo,ao,spread,span=parse(bars_path)
    coverage=json.loads(Path(coverage_path).read_bytes())
    if coverage.get('bar_count')!=len(times) or coverage.get('full_requested_coverage_proven') is not True:
        raise ValueError('DATASET_COVERAGE_DIFFERENCE')
    n=len(times);folds=[]
    for h in HORIZONS:
        i=np.arange(24,n-h-1,dtype=np.int64)
        eligible=(times[i]-times[i-24]==24*300)&(times[i+1]-times[i]==300)&(times[i+h+1]-times[i+1]==h*300)
        idx=i[eligible]
        X=np.zeros((n,10));X[idx],allsp=features(mid,spread,span,times,idx)
        sp=np.zeros(n);sp[idx]=allsp
        y=np.zeros(n);y[idx]=10000*np.log(mo[idx+h+1]/mo[idx+1])
        lb=np.zeros(n);sb=np.zeros(n)
        lb[idx]=10000*np.log(bo[idx+h+1]/ao[idx+1])
        sb[idx]=10000*np.log(bo[idx+1]/ao[idx+h+1])
        for fold in range(4):
            tr,va,te=split_indices(idx,n,h,fold)
            if len(tr)<200 or len(va)<30 or len(te)<30:continue
            alpha,prva,prte=predictions(X,y,tr,va,te)
            mom_va=X[va,2];mom_te=X[te,2]
            results={}
            for name,sv,st in [('ridge',prva,prte),('momentum',mom_va,mom_te),('mean_revert',-mom_va,-mom_te)]:
                th=select(sv,sp[va],lb[va],sb[va],COMMISSION_BPS+0.5)
                variants=[]
                for slip in COSTS:
                    fee=COMMISSION_BPS+slip
                    if th is None:result={'trades':0,'mean_bps':None,'net_usd':0.,'wins':0,'max_loss_bps':None}
                    else:result=score(st,sp[te],lb[te],sb[te],th,fee)
                    variants.append({'slippage_bps':slip,'commission_bps':COMMISSION_BPS,
                                     'spread_from_bid_ask':True,**result})
                results[name]={'selected_validation_threshold_bps':th,'cost_scenarios':variants}
            folds.append({'horizon_minutes':5*h,'fold':fold,'alpha_from_validation':alpha,
                          'train_rows':len(tr),'validation_nonoverlap_rows':len(va),
                          'test_nonoverlap_rows':len(te),
                          'train_label_end_before_validation':bool(max(tr)+h+1<min(va)),
                          'validation_label_end_before_test':bool(max(va)+h+1<min(te)),
                          'test_utc_start':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime(times[te[0]])),
                          'test_utc_end':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime(times[te[-1]])),
                          'baseline_no_trade_usd':0,
                          'policies':results})
    return {'schema':'heptatrader.cost-horizon-study.v1','asof_ms':int(time.time()*1000),
            'data_bars':n,'paired_bar_sha256':sha(bars_path),'coverage_sha256':sha(coverage_path),
            'horizons_minutes':[15,30,60],'notional_usd':NOTIONAL,
            'target':'NEXT_BAR_OPEN_TO_FUTURE_BAR_OPEN_PROXY','no_guaranteed_fills':True,
            'actual_broker_orders_sent':0,'unseen_oos':False,
            'spread_is_executable_side_proxy':True,
            'commission_roundtrip_bps':COMMISSION_BPS,'slippage_stress_bps':list(COSTS),
            'overlapping_test_positions':False,'folds':folds}
def main():
    a=argparse.ArgumentParser()
    a.add_argument('--bars',required=True);a.add_argument('--coverage',required=True)
    a.add_argument('--output',required=True)
    opt=a.parse_args()
    v=run(opt.bars,opt.coverage);out=Path(opt.output)
    out.parent.mkdir(parents=True,mode=0o700,exist_ok=True)
    if out.exists():raise ValueError('NO_OVERWRITE')
    os.umask(0o077);out.write_text(json.dumps(v,sort_keys=True,indent=2)+'\n')
    for h in HORIZONS:
        these=[f for f in v['folds'] if f['horizon_minutes']==5*h]
        print(json.dumps({'horizon_minutes':5*h,'folds':len(these),
              'policy_ridge_usd_at_0_5bps':round(sum(f['policies']['ridge']['cost_scenarios'][1]['net_usd'] for f in these),2),
              'ridge_trades':sum(f['policies']['ridge']['cost_scenarios'][1]['trades'] for f in these),
              'no_trade_total_usd':0}),flush=True)
if __name__=='__main__':main()
