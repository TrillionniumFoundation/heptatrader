"""Offline-only EURUSD action/no-trade net-target ridge research.

All labels use future executable-side proxy bars. Training/validation/test are
chronological with full lookback/outcome purge; trades selected on a given
test fold remain FIXED across cost stresses. No broker orders or promotion.
Historical BID/ASK often report zero spread; extra stress is not a guarantee.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import time
from pathlib import Path
import numpy as np
from .common import write_once
from .cost_horizon_study import parse,HORIZONS,COMMISSION_BPS,NOTIONAL
from .nonlinear_fx_cost_study import matrices,ALPHAS

STRESS_BPS=(0.,0.5,2.)
VALIDATION_STRESS_BPS=2.
SPREAD_FLOOR_BPS=0.5
THRESHOLDS=(0.,1.,2.,4.,8.)
ARMS=('engineered_linear','nonlinear_ridge')


def action_target(long_bps,short_bps,slippage_bps):
    """0 = no-trade, 1 = long, 2 = short, as an OFFLINE future label."""
    long_net=np.asarray(long_bps)-COMMISSION_BPS-SPREAD_FLOOR_BPS-slippage_bps
    short_net=np.asarray(short_bps)-COMMISSION_BPS-SPREAD_FLOOR_BPS-slippage_bps
    return np.where(np.maximum(long_net,short_net)<=0,0,
                    np.where(long_net>=short_net,1,2)).astype(np.int8)


def fixed_cohort(pred_long,pred_short,long_realized,short_realized,threshold,extra_costs=STRESS_BPS):
    """No reselecting trades when a stress cost changes."""
    buy=(pred_long>threshold)&(pred_long>=pred_short)
    sell=(pred_short>threshold)&(pred_short>pred_long)
    selected=buy|sell
    before_fee=np.where(buy,long_realized,short_realized)[selected]
    total=int(selected.sum())
    out=[]
    for slippage in extra_costs:
        net=before_fee-COMMISSION_BPS-SPREAD_FLOOR_BPS-slippage
        out.append({'extra_slippage_bps':float(slippage),
                    'trades':total, 'net_usd':float(net.sum()*NOTIONAL/10000),
                    'winning_trades':int((net>0).sum()),
                    'mean_net_bps':float(net.mean()) if total else None})
    return out


def predict_two_sides(features,long_realized,short_realized,train,validation,test):
    """Fit both executable-side outcomes; scale using TRAIN rows only."""
    x_train=features[train]
    mu=x_train.mean(axis=0)
    sigma=np.where(x_train.std(axis=0)>1e-6,x_train.std(axis=0),1.)
    ztr=(x_train-mu)/sigma
    zv=(features[validation]-mu)/sigma
    zt=(features[test]-mu)/sigma
    targets=np.column_stack([long_realized[train],short_realized[train]])
    ymean=targets.mean(axis=0)
    center=targets-ymean
    gram=ztr.T@ztr
    rhs=ztr.T@center
    candidates=[]
    for alpha in ALPHAS:
        w=np.linalg.solve(gram+alpha*np.eye(gram.shape[0]),rhs)
        valid=zv@w+ymean
        test_pred=zt@w+ymean
        for threshold in THRESHOLDS:
            stat=fixed_cohort(valid[:,0],valid[:,1],long_realized[validation],
                              short_realized[validation],threshold,(VALIDATION_STRESS_BPS,))[0]
            # No-trade wins ties and refuses non-positive validation net.
            if stat['trades']<20 or stat['net_usd']<=0:continue
            candidates.append((stat['net_usd'],float(alpha),float(threshold),test_pred,stat))
    if not candidates:
        return {'validation_passed':False,'alpha':None,'threshold_bps':None,
                'test_scenarios':fixed_cohort(
                    np.full(len(test),-1e6),np.full(len(test),-1e6),
                    long_realized[test],short_realized[test],0)}
    _,alpha,threshold,prediction,validation_stat=max(candidates,key=lambda x:(x[0],-x[1],-x[2]))
    return {'validation_passed':True,'alpha':alpha,'threshold_bps':threshold,
            'validation_net_usd':validation_stat['net_usd'],
            'test_scenarios':fixed_cohort(prediction[:,0],prediction[:,1],
                     long_realized[test],short_realized[test],threshold)}


def evaluate(bars_file,coverage_file):
    raw=Path(bars_file).read_bytes()
    coverage=json.loads(Path(coverage_file).read_text())
    t,mid,mo,bo,ao,spread,span=parse(bars_file)
    if (coverage.get('bar_count')!=len(t)
        or coverage.get('full_requested_coverage_proven') is not True):
        raise ValueError('UNPROVEN_HISTORICAL_COVERAGE')
    folds=[]
    n=len(t)
    for h in HORIZONS:
        idx,linear,nonlinear,y,lb,sb,sp=matrices(t,mid,mo,bo,ao,spread,span,h)
        rows=np.arange(len(idx),dtype=np.int64)
        for k in range(4):
            a=int(n*(.5+k*.1));b=int(n*(.6+k*.1));c=min(n,int(n*(.7+k*.1)))
            train=rows[idx+h+1<a-48]
            valid=rows[(idx>=a)&(idx+h+1<b-48)][::h+1]
            test=rows[(idx>=b)&(idx+h+1<c)][::h+1]
            if min(len(train),len(valid),len(test))<35:raise ValueError('INSUFFICIENT_PURGED_WINDOWS')
            if not (idx[train[-1]]+h+1<idx[valid[0]]
                    and idx[valid[-1]]+h+1<idx[test[0]]):
                raise ValueError('TRAIN_TEST_LABEL_LEAK')
            arms={}
            for name,x in (('engineered_linear',linear),('nonlinear_ridge',nonlinear)):
                arms[name]=predict_two_sides(x,lb,sb,train,valid,test)
            targets=action_target(lb[train],sb[train],VALIDATION_STRESS_BPS)
            folds.append({'fold':k,'horizon_minutes':5*h,
                          'train_count':len(train),'validation_count':len(valid),
                          'test_count':len(test),
                          'train_action_label_counts':{str(v):int((targets==v).sum()) for v in (0,1,2)},
                          'test_first_utc_epoch':int(t[idx[test[0]]]),
                          'test_last_utc_epoch':int(t[idx[test[-1]]]),
                          'purged_chronological':True,'arms':arms})
    summary={}
    for name in ARMS:
        for h in HORIZONS:
            fs=[f for f in folds if f['horizon_minutes']==5*h]
            summary[f'{5*h}:{name}']={
                'validated_folds':sum(f['arms'][name]['validation_passed'] for f in fs),
                'folds':len(fs),
                'stress':[
                    {'extra_slippage_bps':stress,'trades':sum(f['arms'][name]['test_scenarios'][i]['trades'] for f in fs),
                     'net_usd':sum(f['arms'][name]['test_scenarios'][i]['net_usd'] for f in fs)}
                    for i,stress in enumerate(STRESS_BPS)]}
    return {'schema':'heptatrader.cost-net-train-research.v1',
            'at_ms':int(time.time()*1000),'source_sha256':hashlib.sha256(raw).hexdigest(),
            'coverage_sha256':hashlib.sha256(Path(coverage_file).read_bytes()).hexdigest(),
            'historical_revised_not_prospective':True,
            'no_broker_execution_fills_assumed':True,'broker_orders_sent':0,
            'no_automatic_promotion':True,
            'action_target':'FUTURE_EXECUTABLE_SIDE_NET_BUY_SELL_OR_NO_TRADE',
            'transaction_costs':{'commission_roundtrip_bps':COMMISSION_BPS,
              'additional_spread_floor_bps':SPREAD_FLOOR_BPS,
              'validation_slippage_bps':VALIDATION_STRESS_BPS,
              'test_stresses_bps':list(STRESS_BPS)},
            'fixed_trades_across_stresses':True,'notrade_baseline_usd':0,
            'source_bars':len(t),'folds':folds,'summary':summary}


def main():
    cli=argparse.ArgumentParser()
    cli.add_argument('--bars',required=True)
    cli.add_argument('--coverage',required=True)
    cli.add_argument('--output',required=True)
    args=cli.parse_args()
    os.umask(0o077)
    output=Path(args.output)
    if output.exists():raise ValueError('NO_OVERWRITE')
    result=evaluate(args.bars,args.coverage)
    write_once(output,result)
    print(json.dumps(result['summary'],sort_keys=True),flush=True)

if __name__=='__main__':
    main()
