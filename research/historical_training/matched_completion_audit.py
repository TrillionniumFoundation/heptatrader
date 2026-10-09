"""Audit completed matched work without re-encoding, selecting or promoting.
Reconstruct ALL declared cost-policy decisions, including omissions, not only
reported trades. Verify projection from retained raw encoder vectors separately.
"""
from __future__ import annotations
import argparse,math,json,time
from pathlib import Path
from historical_training.common import EvidenceError,read,sha,digest,write_once


def close(actual,expected,path='root'):
    if type(expected) in (int,float) and type(expected) is not bool:
        if type(actual) not in (int,float) or not math.isfinite(actual) or abs(actual-expected)>1e-8:
            raise EvidenceError('POLICY_OR_VALUE_MISMATCH:'+path)
    elif isinstance(expected,dict):
        if not isinstance(actual,dict):raise EvidenceError('POLICY_SHAPE:'+path)
        for k,v in expected.items():
            if k not in actual:raise EvidenceError('POLICY_FIELD_MISSING:'+path+'.'+k)
            close(actual[k],v,path+'.'+k)
    elif isinstance(expected,list):
        if not isinstance(actual,list) or len(actual)!=len(expected):raise EvidenceError('POLICY_LENGTH:'+path)
        for i,(a,e) in enumerate(zip(actual,expected)):close(a,e,path+str(i))
    elif actual!=expected or type(actual)!=type(expected):raise EvidenceError('POLICY_IDENTITY:'+path)


def trace(rows,forecasts,buffer):
    if len(rows)!=len(forecasts) or not math.isfinite(buffer) or buffer<0:raise EvidenceError('POLICY_INPUT')
    details=[];end=-1;cash=peak=drawdown=0.
    for row,pair in zip(rows,forecasts):
        if len(pair)!=2 or not all(math.isfinite(v) for v in pair):raise EvidenceError('FORECAST_SHAPE_FINITE')
        # Independent Python argmax preserves the documented BUY tie rule.
        side=0 if pair[0]>=pair[1] else 1;edge=float(pair[side])
        if row['time']<end or edge<=buffer:continue
        result=(row['long_after_spread_bps' if side==0 else 'short_after_spread_bps']-.9)*10
        if not math.isfinite(result):raise EvidenceError('NONFINITE_COST_TARGET')
        end=row['outcome_end'];cash+=result;peak=max(peak,cash);drawdown=max(drawdown,peak-cash)
        details.append({'time':row['time'],'entry_at':row['entry_at'],'exit_at':row['exit_at'],
            'side':'BUY' if side==0 else 'SELL','forecast_net_bps':edge,'simulated_net_usd':result})
    return {'trades':len(details),'net_usd':cash,'max_drawdown_usd':drawdown,'buffer_bps':buffer,
        'commission_bps':.4,'slippage_bps':.5,'actual_execution_pnl':False,'details':details}


def audit_policies(record,calibration_rows,calibration_forecasts,check_rows,check_forecasts,grid):
    if grid!=[0.,.5,1.,2.]:raise EvidenceError('DECLARED_POLICY_GRID_CHANGED')
    options=[trace(calibration_rows,calibration_forecasts,b) for b in grid]
    if len(record.get('calibration_policies',[]))!=len(options):raise EvidenceError('CALIBRATION_POLICY_OMITTED')
    for a,b in zip(record['calibration_policies'],options):close(a,b,'calibration')
    eligible=[p for p in options if p['trades']>=30 and p['net_usd']>0]
    best=max(eligible,key=lambda p:p['net_usd']) if eligible else None
    close(record.get('admitted_policy'),best is not None,'admitted_policy')
    close(record.get('selected_buffer'),best['buffer_bps'] if best else None,'selected_buffer')
    expected=trace(check_rows,check_forecasts,best['buffer_bps'] if best else 0.)
    close(record['check_selected_or_diagnostic_zero_buffer'],expected,'check')
    close(record.get('economic_qualification'),False,'economic_qualification')
    close(record.get('no_trade_net_usd'),0.,'no_trade')
    identity=digest([(r['time'],r['side']) for r in expected['details']])
    stress=[{'extra_bps':e,'trades':expected['trades'],'net_usd':expected['net_usd']-10*e*expected['trades'],
             'same_time_side_sha256':identity} for e in (0.,.5,1.,2.)]
    close(record['fixed_trade_stress'],stress,'fixed_trade_stress')
    return {'calibration_options_reconstructed':4,'admitted_policy':best is not None,
        'recorded_and_omitted_trades_verified':True,'buffer_selection_calibration_only':True,
        'check_trades':expected['trades'],'check_net_usd':expected['net_usd'],'fixed_trade_stress_verified':True}


def audit(config):
    import numpy as np
    from safetensors.numpy import load_file
    from historical_training.matched_audit import audit as original_audit
    from historical_training.matched_data import SIZES
    from historical_training.matched_fit import probability,value,fit_value
    from historical_training.calibration_model import fit_logistic,brier
    base_audit=original_audit(config);root=Path(config['output']);plan=read(root/'plan.json');r=read(root/'result.json')
    if sha(config['frozen_candidate'])!=r['frozen_reference_sha256']:raise EvidenceError('FROZEN_REFERENCE_HASH')
    if (plan['role_sizes']!=SIZES or plan['min_calibration_trades']!=30 or plan['commission_bps']!=.4
        or plan['slippage_bps']!=.5 or plan['notional_usd']!=100000):raise EvidenceError('PLAN_ECONOMIC_CONTRACT_CHANGED')
    inputs=read(root/'input.json')
    if inputs['old_test_rows_exported']!=0 or inputs['trade_authority'] is not False:raise EvidenceError('INPUT_SCOPE')
    for name,count in SIZES.items():
        if len(inputs['groups'][name])!=count:raise EvidenceError('ROLE_SIZE_CHANGED')
    rows=sum((inputs['groups'][k] for k in SIZES),[]);y=np.array([row['class'] for row in rows],int)
    n=SIZES['fit'];nt=n+SIZES['tune'];nc=nt+SIZES['calibration']
    features={k:v.astype(float) for k,v in load_file(root/'features.safetensors').items()}
    if set(features)!={'english','mmbert'}:raise EvidenceError('SEMANTIC_ARM_SET')
    features['numeric']=np.asarray([[round(float(v),6) for v in row['features']] for row in rows])
    heads=read(root/'readouts.json')['readouts'];checks={}
    for arm,x in features.items():
        h=heads[arm];v=h['value_model'];m=h['model']
        mean=x[:nt].mean(0);std=x[:nt].std(0);std[std<1e-6]=1
        if not np.array_equal(mean,v['mean']) or not np.array_equal(std,v['std']):raise EvidenceError('VALUE_NORMALIZER_NOT_TRAIN_ONLY')
        # Recreate the PREDECLARED tune-only choices, not a new model search.
        trials=[];vtrials=[];target=np.array([[row['long_after_spread_bps']-.9,row['short_after_spread_bps']-.9] for row in rows[n:nt]])
        for penalty in plan['penalties']:
            model=fit_logistic(x[:n],y[:n],penalty)
            trials.append({'penalty':penalty,'tune_brier':float(brier(probability(model,x[n:nt]),y[n:nt]).mean())})
        for penalty in plan['value_penalties']:
            model=fit_value(x[:n],rows[:n],penalty)
            vtrials.append({'penalty':penalty,'tune_mse':float(np.mean((value(model,x[n:nt])-target)**2))})
        close(h['tune_trials'],trials,'tune_trials');close(h['value_trials'],vtrials,'value_trials')
        close(m['penalty'],min(trials,key=lambda z:z['tune_brier'])['penalty'],'classifier_selected_penalty')
        close(v['penalty'],min(vtrials,key=lambda z:z['tune_mse'])['penalty'],'value_selected_penalty')
        checks[arm]=audit_policies(r['costs'][arm],rows[nt:nc],value(v,x[nt:nc]),rows[nc:],value(v,x[nc:]),plan['buffer_grid_bps'])
        checks[arm].update(tune_choice_reproduced=True,value_normalizer_training_only=True)
    # Actual retained raw features -> exact originally declared fixed64 projection.
    import torch
    from safetensors.torch import load_file as load_tensors
    from historical_training.matched_projection import project_fixed64
    torch.set_num_threads(2);encoded=load_tensors(str(root/'features.safetensors'));projection={}
    for arm in ('english','mmbert'):
        rr=read(root/(arm+'-raw-receipt.json'));path=root/(arm+'-raw.safetensors')
        if (rr['raw_sha256']!=sha(path) or rr['input_sha256']!=plan['input_sha256']
            or rr['plan_sha256']!=sha(root/'plan.json') or rr['text_hashes_sha256']!=__import__('hashlib').sha256(json.dumps(plan['text_sha256']).encode()).hexdigest()):
            raise EvidenceError('RAW_ENCODER_FEATURE_BINDING')
        if rr.get('asset')!=plan['assets'][arm] or rr.get('trained') is not False:
            raise EvidenceError('RAW_ENCODER_MODEL_IDENTITY')
        raw=load_tensors(str(path))
        if len(raw)!=1:raise EvidenceError('RAW_ENCODER_KEYS')
        raw=next(iter(raw.values()))
        if raw.shape!=(len(rows),rr['width']) or rr['rows']!=len(rows):raise EvidenceError('RAW_ENCODER_SHAPE')
        p=project_fixed64(raw,plan['projection_seed'],plan['projection_width'])
        difference=float((p-encoded[arm]).abs().max())
        if difference>1e-6:raise EvidenceError('RAW_TO_PROJECTED_FEATURE_MISMATCH')
        projection[arm]={'raw_sha256':sha(path),'maximum_projection_difference':difference,'rows':len(rows),'backbone_rerun':False}
    return {'schema':'heptatrader.matched-completion-audit.v1','at_ms':int(time.time()*1000),
        'original_result_sha256':sha(root/'result.json'),'plan_sha256':sha(root/'plan.json'),
        'full_original_audit':base_audit,'policy_checks':checks,'raw_projection_checks':projection,
        'new_model_selected':False,'new_candidate_created':False,'original_result_modified':False,
        'frozen_candidate_changed':False,'old_test_rows_used':0,'encoder_forward_passes':0,'broker_orders_sent':0,
        'uncertainty_interpretation':'DEVELOPMENT_ONLY_ALL_SEMANTIC_OR_MIXED_GAIN_INTERVALS_VS_MATCHED_NUMERIC_INCLUDE_ZERO'}


def main():
    p=argparse.ArgumentParser();p.add_argument('--config',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    if a.output.exists():raise EvidenceError('AUDIT_RECEIPT_EXISTS')
    result=audit(read(a.config));write_once(a.output,result)
    print(json.dumps({k:v for k,v in result.items() if k!='full_original_audit'},indent=2))
if __name__=='__main__':main()
