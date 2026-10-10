"""Non-deployable price-only walk-forward with IB live-cost stress evidence.

Historical BAR midpoint is an outcome/feature, never an executable quote.
The fill price is hypothetical next-bar midpoint OPEN, not a broker fill.
"""
import argparse,json,math,os
from datetime import datetime,timezone
from pathlib import Path
import numpy as np
from laya_cell.integrity import read_json,sha_file
DAY=86400
EMBARGO=900
TRAIN_DAYS=120
VALID_DAYS=30
TEST_DAYS=15
THRESHOLDS=(0,.5,1,2,3,4,6,8,12)
SLIPPAGE=(.5,1.,2.)

def features(bars):
    ts=np.asarray([b['time'] for b in bars],dtype=np.int64)
    op=np.asarray([float(b['midpoint']['open']) for b in bars])
    cl=np.asarray([float(b['midpoint']['close']) for b in bars])
    if (len(bars)<1000 or not (np.diff(ts)>0).all() or
        not np.isfinite(op).all() or not np.isfinite(cl).all() or
        not (op>0).all() or not (cl>0).all()):
        raise ValueError('INVALID_PRICE_SERIES')
    x=[];p=[];mature=[];gross=[]
    for i in range(12,len(bars)-3):
        if not (np.diff(ts[i-12:i+4])==300).all():continue
        lags=cl[i]/cl[i-np.array([1,3,6,12])]-1
        ret=np.diff(cl[i-12:i+1])/cl[i-12:i]
        hour=(int(ts[i])//3600)%24
        x.append(list(lags*10000)+[float(ret.std()*10000),
            float(np.abs(ret).mean()*10000),
            math.sin(2*math.pi*hour/24),math.cos(2*math.pi*hour/24)])
        p.append(int(ts[i])+300)
        mature.append(int(ts[i+3])+300)
        gross.append((cl[i+3]/op[i+1]-1)*10000)
    return {k:np.asarray(v,dtype=dt) for k,v,dt in [
        ('x',x,float),('at',p,np.int64),('mature',mature,np.int64),('returns',gross,float)]}

def regression(x,y):
    if len(x)<1000:raise ValueError('INSUFFICIENT_TRAIN_ROWS')
    mean=x.mean(0);sd=np.maximum(x.std(0),1e-8)
    v=np.concatenate([np.ones((len(x),1)),np.clip((x-mean)/sd,-5,5)],axis=1)
    ridge=np.eye(v.shape[1])*50;ridge[0,0]=0
    return mean,sd,np.linalg.solve(v.T@v+ridge,v.T@y)

def predict(model,x):
    mu,sd,w=model
    v=np.concatenate([np.ones((len(x),1)),np.clip((x-mu)/sd,-5,5)],axis=1)
    return v@w

def simulate(at,decision,actual,threshold,cost):
    if threshold is None:return {'trades':0,'net_usd':0.,'max_drawdown_usd':0.}
    pnl=[];last=-10**19
    for t,d,r in zip(at,decision,actual):
        if t<last+900 or abs(d)<=threshold:continue
        p=(1 if d>0 else -1)*r-cost
        if not math.isfinite(float(p)):raise ValueError('NONFINITE_PNL')
        pnl.append(float(p));last=int(t)
    cumul=np.cumsum(np.asarray(pnl)*10)
    if len(cumul):
        maxima=np.maximum.accumulate(np.r_[0,cumul])
        maxdd=float((maxima[1:]-cumul).max())
    else:maxdd=0.
    return {'trades':len(pnl),'net_usd':round(float(cumul[-1]),2) if len(cumul) else 0.,
            'max_drawdown_usd':round(maxdd,2)}

def verify_inputs(bars,coverage,cost):
    if bars.get('schema')!='heptatrader.historical-paired-bars.v1':
        raise ValueError('WRONG_HISTORICAL_DATASET')
    if (coverage.get('full_requested_coverage_proven') is not True
        or coverage.get('calendar_response_interval_complete') is not True
        or coverage.get('scheduled_missing_count') != 0
        or coverage.get('revised_historical_data_not_contemporaneous') is not True
        or coverage.get('bar_count') != len(bars['bars'])
        or any(coverage.get('counts_by_kind',{}).get(s)!=len(bars['bars'])
               for s in ('BID','ASK','MIDPOINT'))):
        raise ValueError('HISTORICAL_COVERAGE_NOT_VERIFIED')
    if (cost.get('schema')!='heptatrader.ib-cost-evidence.offline.v1'
        or cost.get('fx_fee_missing_or_unproved_executions')!=0
        or cost.get('fx_fee_complete_unique_executions',0)<50
        or cost.get('actual_slippage_verified') is not False
        or cost.get('trade_authority') is not False
        or cost.get('orders_sent')!=0):
        raise ValueError('COST_EVIDENCE_INVALID')
    spread=cost['quote_spread_full_bps']['p90']
    stress_spread=cost['quote_spread_full_bps']['p99']
    commission=cost['fx_commission_one_way_bps']['p90']
    if not all(type(v) in (float,int) and math.isfinite(v) and v>0 for v in
               (spread,stress_spread,commission)):
        raise ValueError('COST_QUANTILES_INVALID')
    return spread,stress_spread,commission

def run(bars,coverage,cost):
    spread,stress_spread,commission=verify_inputs(bars,coverage,cost)
    f=features(bars['bars'])
    X,at,mature,y=(f[k] for k in ('x','at','mature','returns'))
    first=int(at.min())//DAY*DAY
    last=int(mature.max())
    seen=set();folds=[]
    for index in range(40):
        start=first+index*TEST_DAYS*DAY
        tr_end=start+TRAIN_DAYS*DAY
        val_start=tr_end+EMBARGO
        val_end=val_start+VALID_DAYS*DAY
        test_start=val_end+EMBARGO
        test_end=test_start+TEST_DAYS*DAY
        if test_end>last:break
        tr=(at>=start)&(mature<tr_end-EMBARGO)
        va=(at>=val_start)&(mature<val_end-EMBARGO)
        te=(at>=test_start)&(mature<test_end)
        if sum(tr)<5000 or sum(va)<500 or sum(te)<300:
            raise ValueError('WALKFORWARD_ROW_BUDGET')
        test_ids=set(at[te].tolist())
        if seen.intersection(test_ids):raise ValueError('REUSED_TEST_OBSERVATIONS')
        seen.update(test_ids)
        model=regression(X[tr],y[tr])
        validation_predictions=predict(model,X[va])
        test_predictions=predict(model,X[te])
        validation_cost=spread+2*commission+2.0
        choices=[]
        for threshold in THRESHOLDS:
            metric=simulate(at[va],validation_predictions,y[va],threshold,validation_cost)
            if metric['trades']>=30 and metric['net_usd']>0:
                choices.append((metric['net_usd'],-threshold,threshold))
        choice=max(choices)[2] if choices else None
        scenarios=[]
        for slip in SLIPPAGE:
            applied_spread=stress_spread if slip==max(SLIPPAGE) else spread
            total=applied_spread+2*commission+2*slip
            trained=simulate(at[te],test_predictions,y[te],choice,total)
            # Deterministic, pre-registered 3-bar momentum baseline.
            naive=np.sign(X[te,1])*10.
            naive_result=simulate(at[te],naive,y[te],3.,total)
            scenarios.append({
                'slippage_assumed_bps_per_side':slip,
                'spread_full_bps':applied_spread,
                'reported_commission_roundtrip_bps':round(2*commission,7),
                'cost_roundtrip_bps':round(total,7),
                'trained_price_model':trained,
                'naive_3bar_momentum':naive_result,
                'no_trade_net_usd':0.})
        folds.append({
            'fold':index,'train_start_utc':datetime.fromtimestamp(start,timezone.utc).isoformat(),
            'train_end_utc':datetime.fromtimestamp(tr_end,timezone.utc).isoformat(),
            'val_start_utc':datetime.fromtimestamp(val_start,timezone.utc).isoformat(),
            'val_end_utc':datetime.fromtimestamp(val_end,timezone.utc).isoformat(),
            'test_start_utc':datetime.fromtimestamp(test_start,timezone.utc).isoformat(),
            'test_end_utc':datetime.fromtimestamp(test_end,timezone.utc).isoformat(),
            'training_rows':int(sum(tr)),'validation_rows':int(sum(va)),
            'test_rows':int(sum(te)),'selected_validation_threshold_bps':choice,
            'test_price_direction_accuracy':round(float(((test_predictions>0)==(y[te]>0)).mean()),6),
            'train_label_matured_before_cutoff':bool((mature[tr]<tr_end-EMBARGO).all()),
            'test_rows_never_reused':True,'scenarios':scenarios})
    if len(folds)<2:raise ValueError('INSUFFICIENT_OOS_FOLDS')
    return {'schema':'heptatrader.eurusd-walkforward-price.v1',
            'state':'OFFLINE_EXPLORATORY_NO_PROMOTION',
            'folds':folds,'tested_folds':len(folds),
            'eligible_price_rows':len(at),
            'training':'DETERMINISTIC_RIDGE_HISTORICAL_PRICE_ONLY',
            'selection':'120D_TRAIN_30D_VALIDATION_15D_TEST_15D_STEP_900SEC_EMBARGO',
            'entry_price':'NEXT_BAR_MID_OPEN_HYPOTHETICAL_NOT_BROKER_FILL',
            'data_revised_not_contemporaneous':True,
            'spread_from':'ORIGINAL_LIVE_IB_QUOTE_RECEIPTS',
            'commissions_from':'DEDUPLICATED_IB_PAPER_COMMISSION_CALLBACKS',
            'slippage_from':'HYPOTHETICAL_STRESS_WITH_CALLBACK_TIME_DIAGNOSTIC_ONLY',
            'actual_execution_slippage_proven':False,
            'no_trading_alpha_claim':True,
            'promotion_allowed':False,'trade_authority':False,'orders_sent':0}

def main():
    parser=argparse.ArgumentParser()
    for name in ('bars','coverage','cost','output'):
        parser.add_argument('--'+name,required=True,type=Path)
    args=parser.parse_args()
    b=read_json(args.bars,64*1024**2)
    v=read_json(args.coverage,1024**2)
    c=read_json(args.cost,1024**2)
    result=run(b,v,c)
    result['input_sha256']={'bars':sha_file(args.bars),'coverage':sha_file(args.coverage),
                            'cost':sha_file(args.cost)}
    args.output.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    with args.output.open('x') as fp:
        fp.write(json.dumps(result,sort_keys=True,indent=2)+'\n')
        fp.flush();os.fsync(fp.fileno())
    args.output.chmod(0o600)
    print(json.dumps({'state':result['state'],'tested_folds':result['tested_folds'],
        'eligible_price_rows':result['eligible_price_rows'],
        'thresholds':[f['selected_validation_threshold_bps'] for f in result['folds']],
        'promotion_allowed':False,'orders_sent':0}))

if __name__=='__main__':
    main()
