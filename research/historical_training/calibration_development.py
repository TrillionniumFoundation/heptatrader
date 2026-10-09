"""Bounded train/tune/calibrate/check experiment. Old test is not accessible.
Development evidence is never reported as independent future efficacy.
"""
from __future__ import annotations
import argparse,math,os,sys,time
from datetime import datetime,timezone
from pathlib import Path
from .common import EvidenceError,read,sha,digest,write_once

PROFILE='EURUSD_5MIN_LOOKBACK12_LEAD1_HOLD3_V1'


def split_purged(rows,fraction):
    if not .1<=fraction<=.9 or len(rows)<100:raise EvidenceError('DEVELOPMENT_SPLIT_SIZE')
    if any(b['time']<=a['time'] for a,b in zip(rows,rows[1:])):raise EvidenceError('DEVELOPMENT_TIME_ORDER')
    boundary=rows[int(len(rows)*fraction)]['time']
    left=[r for r in rows if r['outcome_end']<boundary]
    right=[r for r in rows if r['history_start']>=boundary]
    if not left or not right or max(r['outcome_end'] for r in left)>=min(r['history_start'] for r in right):
        raise EvidenceError('DEVELOPMENT_FULL_SPAN_OVERLAP')
    return left,right,{'boundary':boundary,'purged':len(rows)-len(left)-len(right)}


def validate_development(data):
    if (data.get('profile')!=PROFILE or set(data.get('groups',{}))!={'train','validation'}
        or data.get('source_test_rows_exported')!=0 or data.get('actual_execution_rewards')!=0
        or data.get('trade_authority') is not False):raise EvidenceError('DEVELOPMENT_ONLY_SCOPE')
    if digest({k:v for k,v in data.items() if k!='fingerprint'})!=data['fingerprint']:raise EvidenceError('DEVELOPMENT_DATA_CHANGED')
    train=data['groups']['train'];val=data['groups']['validation']
    if max(r['outcome_end'] for r in train)>=min(r['history_start'] for r in val):raise EvidenceError('TRAIN_VALIDATION_OVERLAP')
    for row in train+val:
        if any(type(row[k]) is not int for k in ('time','history_start','entry_at','exit_at','outcome_end')):
            raise EvidenceError('NONINTEGER_EXAMPLE_TIME')
        if (row['time']%300 or row['time']-row['history_start']!=3600 or row['entry_at']-row['time']!=300
            or row['exit_at']-row['entry_at']!=900 or row['outcome_end']-row['exit_at']!=300
            or row['real_execution_reward'] is not False):raise EvidenceError('DECISION_LEAD_OR_HORIZON')
    return train,val


def bootstrap_days(rows,values):
    import numpy as np
    values=np.asarray(values,float);groups={}
    if len(rows)!=len(values) or not np.isfinite(values).all():raise EvidenceError('BOOTSTRAP_VALUES')
    for i,r in enumerate(rows):
        day=datetime.fromtimestamp(r['time'],timezone.utc).date().isoformat()
        groups.setdefault(day,[]).append(i)
    totals=np.array([values[idx].sum() for idx in groups.values()]);counts=np.array([len(idx) for idx in groups.values()])
    if not len(totals):return {'rows':0,'utc_days':0,'mean':None,'interval_95':None}
    rng=np.random.default_rng(2026100809);picks=rng.integers(0,len(totals),(2000,len(totals)))
    samples=totals[picks].sum(1)/counts[picks].sum(1)
    return {'rows':len(rows),'utc_days':len(totals),'mean':float(values.mean()),
            'interval_95':[float(x) for x in np.quantile(samples,[.025,.975])],
            'scope':'DEVELOPMENT_DIAGNOSTIC_NOT_INDEPENDENT_OR_MULTIPLICITY_ADJUSTED'}


def trade_summary(rows,forecasts,buffer_bps,extra_cost_bps=0.):
    import numpy as np
    f=np.asarray(forecasts,float)
    if f.shape!=(len(rows),2) or not np.isfinite(f).all() or not math.isfinite(buffer_bps) or buffer_bps<0 or extra_cost_bps<0:
        raise EvidenceError('VALUE_FORECAST_OR_BUFFER')
    occupied=-1;trades=[];allprofits=[]
    for row,pair in zip(rows,f):
        side=int(np.argmax(pair));value=float(pair[side])
        if row['time']<occupied or value<=buffer_bps:continue
        gross=row['long_after_spread_bps'] if side==0 else row['short_after_spread_bps']
        profit=(gross-.4-.5-extra_cost_bps)*10
        # Reserve the input->available-label span, conservatively avoiding overlaps.
        occupied=row['outcome_end'];allprofits.append(profit)
        trades.append({'time':row['time'],'entry_at':row['entry_at'],'exit_at':row['exit_at'],
            'side':'BUY' if side==0 else 'SELL','forecast_net_bps':value,'simulated_net_usd':profit})
    cumul=np.cumsum([0.]+allprofits);dd=float(np.max(np.maximum.accumulate(cumul)-cumul))
    interval=bootstrap_days(trades,allprofits) if trades else {'rows':0,'utc_days':0,'mean':None,'interval_95':None}
    return {'trades':len(trades),'net_usd':float(sum(allprofits)),'max_drawdown_usd':dd,'day_block_per_trade':interval,
            'buffer_bps':buffer_bps,'commission_bps':.4,'slippage_bps':.5+extra_cost_bps,
            'actual_execution_pnl':False,'details':trades}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--config',type=Path,required=True);args=parser.parse_args()
    config=read(args.config);inp=Path(config['input']);out=Path(config['output'])
    if sha(inp)!=config['input_sha256']:raise EvidenceError('DEVELOPMENT_INPUT_CHANGED')
    if out.exists():raise EvidenceError('EXPERIMENT_ALREADY_EXISTS_NO_OVERWRITE')
    out.mkdir(mode=0o700)
    from sandbox import enter
    sandbox=enter({'output_dir':str(out),'period_path':str(inp),'legacy_capture':str(inp),'oms_path':str(inp),'export_dir':str(inp),
        'read_only_paths':[config['source_root'],str(args.config),sys.prefix]})
    try:
        with open(config['forbidden_original'],'rb') as f:f.read(1)
    except PermissionError:sandbox['original_train_validation_test_file_read_denied']=True
    else:raise EvidenceError('ORIGINAL_TEST_FILE_NOT_DENIED')
    write_once(out/'sandbox.json',sandbox)
    data=read(inp,128*1024*1024);train,validation=validate_development(data)
    fit,tune,train_split=split_purged(train,.8);cal,check,val_split=split_purged(validation,.5)
    roles={'fit':fit,'tune':tune,'calibration':cal,'check':check}
    inventory={p.name:sha(p) for p in Path(__file__).parent.glob('*.py')}
    plan={'schema':'heptatrader.calibration-development-plan.v1','created_at_ms':int(time.time()*1000),
        'profile':PROFILE,'development_sha256':config['input_sha256'],'fingerprint':data['fingerprint'],
        'role_sizes':{k:len(v) for k,v in roles.items()},'role_ranges':{k:{'first_history':v[0]['history_start'],'last_outcome':v[-1]['outcome_end']} for k,v in roles.items()},
        'full_span_purge':{'train_split':train_split,'validation_split':val_split},'source_sha256':inventory,
        'learning_curve_sizes':[256,2048,len(fit)],'logistic_penalties':[.001,.01,.1],
        'feature_profiles':['raw25','compact'],'volatility_bins':[5],
        'model_selection':'FULL_SIZE_TUNE_BRIER_THEN_REFIT_ON_TRAIN_ONLY',
        'calibration':'TEMPERATURE_AND_PRIOR_SHRINKAGE_CALIBRATION_LOGLOSS_ONLY',
        'value_penalties':[.001,.01,.1,1.],'value_selection':'TUNE_MSE_ON_EXECUTABLE_SIDE_NET_BPS',
        'policy_buffer_grid_bps':[0.,.5,1.,2.],'policy_selection':'CALIBRATION_NET_WITH_AT_LEAST_30_TRADES',
        'price_review_gate':{'minimum_brier_gain':.001,'paired_day_interval_lower_positive':True,'minimum_days':5},
        'economic_review_gate':{'minimum_trades':30,'minimum_days':5,'net_positive':True,'day_block_interval_lower_positive':True},
        'cost_assumptions':data['cost_assumptions'],'old_test_evaluated':False,
        'prospective_window_registered':False,'orders_sent':0,'max_grid_expansion':0}
    write_once(out/'plan.json',plan)
    import numpy as np
    from .calibration_model import features,fit_logistic,fit_volatility,predict,prior,brier,fit_calibration,calibrate_apply,fit_value,predict_value,reliability
    y=lambda rs:np.array([r['class'] for r in rs],dtype=int)
    learning=[];eligible=[]
    for profile in plan['feature_profiles']:
        for count in plan['learning_curve_sizes']:
            subset=[fit[i*(len(fit)-1)//(count-1)] for i in range(count)]
            for penalty in plan['logistic_penalties']:
                model=fit_logistic(features(subset,profile),y(subset),penalty);model['profile']=profile
                score=float(brier(predict(model,tune),y(tune)).mean())
                trial={'family':'softmax','profile':profile,'rows':count,'penalty':penalty,'tune_brier':score,
                       'iterations':model['iterations'],'gradient_inf':model['gradient_inf']}
                learning.append(trial)
                if count==len(fit):eligible.append((score,trial))
                print(__import__('json').dumps({'fit':trial}),flush=True)
    for bins in plan['volatility_bins']:
        model=fit_volatility(fit,y(fit),bins);score=float(brier(predict(model,tune),y(tune)).mean())
        trial={'family':'volatility_prior','bins':bins,'rows':len(fit),'tune_brier':score};eligible.append((score,trial));learning.append(trial)
    constant={'family':'constant_prior','fit_prior':prior(y(fit)).tolist(),'fit_rows':len(fit)}
    trial={'family':'constant_prior','rows':len(fit),'tune_brier':float(brier(predict(constant,tune),y(tune)).mean())}
    eligible.append((trial['tune_brier'],trial));learning.append(trial)
    _,selected=min(eligible,key=lambda t:(t[0],t[1]['family']))
    base=prior(y(train))
    if selected['family']=='softmax':
        model=fit_logistic(features(train,selected['profile']),y(train),selected['penalty']);model['profile']=selected['profile']
    elif selected['family']=='volatility_prior':model=fit_volatility(train,y(train),selected['bins'])
    else:model={'family':'constant_prior','fit_prior':base.tolist(),'fit_rows':len(train)}
    calibration=fit_calibration(predict(model,cal),y(cal),base)
    calibrated=calibrate_apply(predict(model,check),calibration)
    prior_predictions=np.tile(base,(len(check),1));uniform=np.full_like(prior_predictions,1/3)
    reports={'selected_raw':reliability(predict(model,check),y(check)),'selected_calibrated':reliability(calibrated,y(check)),
             'train_prior':reliability(prior_predictions,y(check)),'uniform':reliability(uniform,y(check))}
    gains={name:bootstrap_days(check,brier(p,y(check))-brier(calibrated,y(check))) for name,p in (('train_prior',prior_predictions),('uniform',uniform))}
    price_ok=all(g['utc_days']>=5 and g['mean']>.001 and g['interval_95'][0]>0 for g in gains.values())
    value_trials=[]
    target=lambda rows:np.asarray([[r['long_after_spread_bps']-.9,r['short_after_spread_bps']-.9] for r in rows])
    for penalty in plan['value_penalties']:
        value=fit_value(fit,penalty);mse=float(np.mean((predict_value(value,tune)-target(tune))**2))
        value_trials.append({'penalty':penalty,'tune_mse':mse})
    value_selected=min(value_trials,key=lambda r:r['tune_mse']);value=fit_value(train,value_selected['penalty'])
    calibration_forecasts=predict_value(value,cal)
    policies=[trade_summary(cal,calibration_forecasts,b) for b in plan['policy_buffer_grid_bps']]
    admissible=[p for p in policies if p['trades']>=30 and p['net_usd']>0]
    proposal=max(admissible,key=lambda p:p['net_usd']) if admissible else None
    cost_check=trade_summary(check,predict_value(value,check),proposal['buffer_bps'] if proposal else 0.)
    ci=cost_check['day_block_per_trade']
    economic_ok=bool(proposal and cost_check['trades']>=30 and cost_check['net_usd']>0 and ci['utc_days']>=5 and ci['interval_95'][0]>0)
    costs={'calibration_policies':policies,'buffer_selected_on_calibration':proposal['buffer_bps'] if proposal else None,
        'check_selected_or_diagnostic_zero_buffer':cost_check,'economic_gate_passed':economic_ok,
        'zero_buffer_check_is_diagnostic_only':proposal is None,'no_trade_net_usd':0.0,
        'fixed_check_trade_extra_costs':[{'extra_bps':e,'trades':cost_check['trades'],
            'same_trade_identity_sha256':digest([(t['time'],t['side']) for t in cost_check['details']]),
            'net_usd':cost_check['net_usd']-cost_check['trades']*10*e} for e in (0.,.5,1.,2.)]}
    bundle={'schema':'heptatrader.frozen-bar-price-candidate.v1','profile':PROFILE,'created_at_ms':int(time.time()*1000),
        'plan_sha256':sha(out/'plan.json'),'development_sha256':sha(inp),'source_sha256':inventory,
        'model':model,'calibration':calibration,'value_model':value,
        'price_shadow_eligible':price_ok,'simulated_economic_review_passed':economic_ok,
        'action_policy':'SIMULATED_SHADOW_ONLY' if economic_ok else 'ABSTAIN',
        'buffer_bps':proposal['buffer_bps'] if proposal else None,
        'fit_max_outcome_seconds':max(r['outcome_end'] for r in train),'check_max_outcome_seconds':max(r['outcome_end'] for r in check),
        'trade_authority':False,'automatic_promotion':False,'old_live_numeric_parity':False,
        'prospective_efficacy_proven':False,'expected_input_bar_seconds':300,'lookback_bars':12,
        'decision_lead_seconds':300,'return_horizon_seconds':900,'label_availability_delay_seconds':300}
    bundle['candidate_id']=digest(bundle);write_once(out/'frozen-candidate.json',bundle)
    result={'schema':'heptatrader.calibration-development-result.v1','created_at_ms':int(time.time()*1000),
        'plan_sha256':sha(out/'plan.json'),'candidate_sha256':sha(out/'frozen-candidate.json'),'candidate_id':bundle['candidate_id'],
        'numpy_version':np.__version__,'role_sizes':plan['role_sizes'],'learning_curve':learning,'chosen_by_tune':selected,
        'calibration':calibration,'check_scores':reports,'paired_development_gains':gains,
        'value_tune_trials':value_trials,'costs':costs,'price_shadow_eligible':price_ok,'economic_shadow_eligible':economic_ok,
        'old_test_rows_evaluated':0,'original_test_file_read_denied':True,'actual_execution_rewards_used':0,
        'state':'FROZEN_RESEARCH_CANDIDATE_NOT_PROMOTED','prospective_outcome_count':0,'orders_sent':0}
    write_once(out/'result.json',result)
    print(__import__('json').dumps({'chosen':selected,'calibration':calibration,'check_brier':{k:v['multiclass_brier'] for k,v in reports.items()},
        'paired_gains':gains,'price_shadow_eligible':price_ok,'economic_shadow_eligible':economic_ok,
        'check_cost':{k:v for k,v in cost_check.items() if k!='details'},'candidate_id':bundle['candidate_id']},indent=2),flush=True)

if __name__=='__main__':main()
