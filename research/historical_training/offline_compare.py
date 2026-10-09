"""Bounded offline price comparison; frozen paired encoders, train-only heads.
No live trading, monetary execution rewards, or prospective promotion. The new
bar-profile numeric baseline is NOT mislabeled as the old live checkpoint.
"""
from __future__ import annotations
import argparse,gc,hashlib,json,os,sys,time
from datetime import datetime,timezone
from pathlib import Path
from .common import EvidenceError,read,sha,digest,write_once

SIZES={'train':256,'validation':128,'test':128}
SEED=2026100803

def select(data):
    selected={}
    for name,count in SIZES.items():
        group=data['groups'][name]
        if len(group)<count:raise EvidenceError('INSUFFICIENT_EXAMPLES:'+name)
        # Fixed time-stratified index sampling, never class/outcome selection.
        indices=[i*(len(group)-1)//(count-1) for i in range(count)]
        selected[name]=[group[i] for i in indices]
    return selected

def input_vector(row):return [round(float(x),6) for x in row['features']]

def input_text(row):
    values=input_vector(row)
    known={'relative_completed_closes_bps':values[:11],'completed_spreads_bps':values[11:23],
           'utc_hour_sine_cosine':values[23:]}
    return 'EUR/USD five-minute historical price inputs, oldest first. Forecast the next 15-minute midpoint return class (below -1 bp, within +/-1 bp, above +1 bp). '+json.dumps(known,sort_keys=True,separators=(',',':'))

def probabilities(np,x,w):
    raw=x@w;raw=np.maximum(raw,1e-6)
    return raw/raw.sum(axis=1,keepdims=True)

def brier(np,p,y):return ((p-np.eye(3)[y])**2).sum(axis=1)

def fit(np,x,y,xv,yv):
    mean=x.mean(0);std=x.std(0);std[std<1e-6]=1
    design=lambda z:np.column_stack(((z-mean)/std,np.ones(len(z))))
    a=design(x);v=design(xv);target=np.eye(3)[y];trials=[];best=None
    for alpha in (.1,1.,10.):
        penalty=np.eye(a.shape[1])*alpha;penalty[-1,-1]=0
        w=np.linalg.solve(a.T@a+penalty,a.T@target)
        score=float(brier(np,probabilities(np,v,w),yv).mean())
        trials.append({'alpha':alpha,'validation_brier':score})
        if best is None or score<best[0]:best=(score,w,alpha)
    return {'weights':best[1],'mean':mean,'std':std,'alpha':best[2],'trials':trials},design

def net_summary(np,rows,p,class_means,slippage):
    previous_end=-1;profits=[];trades=[]
    expected=p@class_means
    for i,(row,edge) in enumerate(zip(rows,expected)):
        # Gate uses only the forecast and already completed input spread.
        threshold=max(0.,input_vector(row)[22])+.4+slippage+.5
        if row['time']<previous_end or abs(edge)<=threshold:continue
        side='BUY' if edge>0 else 'SELL'
        gross=row['long_after_spread_bps'] if side=='BUY' else row['short_after_spread_bps']
        profit=(gross-.4-slippage)*10. # $100k reference notional, explicit simulated costs.
        profits.append(profit);previous_end=row['outcome_end']
        trades.append({'time':row['time'],'side':side,'simulated_net_usd':profit})
    cumulative=np.cumsum([0.]+profits)
    dd=float(np.max(np.maximum.accumulate(cumulative)-cumulative))
    return {'trades':len(profits),'net_usd':float(sum(profits)),
        'win_rate':float(np.mean(np.array(profits)>0)) if profits else None,'max_drawdown_usd':dd,
        'roundtrip_commission_bps':.4,'roundtrip_slippage_bps':slippage,
        'spread_in_executable_side_prices':True,'entry_model':'NEXT_BAR_OPEN_NOT_A_GUARANTEED_FILL',
        'actual_execution_reward':False,'trade_details':trades}

def paired_day_interval(np,rows,gain):
    days=[datetime.fromtimestamp(r['time'],timezone.utc).date().isoformat() for r in rows]
    unique=sorted(set(days));blocks=[np.where(np.array(days)==day)[0] for day in unique]
    rng=np.random.default_rng(SEED);means=[]
    for _ in range(1000):
        sample=np.concatenate([blocks[i] for i in rng.integers(0,len(blocks),len(blocks))])
        means.append(float(gain[sample].mean()))
    return {'paired_rows':len(rows),'utc_day_blocks':len(blocks),'mean_brier_gain':float(gain.mean()),
        'block_bootstrap_95_interval':[float(x) for x in np.quantile(means,[.025,.975])],
        'exploratory_not_multiple_testing_adjusted':True}

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--config',type=Path,required=True)
    args=parser.parse_args();c=read(args.config);data=read(c['dataset'],256*1024*1024)
    if sha(c['dataset'])!=c['dataset_sha256']:raise EvidenceError('DATASET_CHANGED')
    if data.get('actual_execution_rewards')!=0 or data.get('trade_authority') is not False:
        raise EvidenceError('HISTORICAL_PRICE_SCOPE_REQUIRED')
    out=Path(c['output_root'])
    if out.exists():raise EvidenceError('EXPERIMENT_OUTPUT_EXISTS_NO_TEST_REUSE')
    out.mkdir(parents=True,mode=0o700)
    chosen=select(data);all_rows=sum((chosen[k] for k in SIZES),[])
    texts=[input_text(r) for r in all_rows]
    from paired_net_100k_preflight.run import asset_manifest
    assets={arm:asset_manifest(Path(c[arm+'_directory']),Path(c[arm+'_receipt']),arm) for arm in ('mmbert','english')}
    plan={'schema':'heptatrader.offline-paired-price-plan.v1','created_ms':int(time.time()*1000),
        'dataset_sha256':c['dataset_sha256'],'dataset_id':data['dataset_id'],'sample_sizes':SIZES,
        'sample_times':{k:[r['time'] for r in v] for k,v in chosen.items()},
        'text_hashes':[hashlib.sha256(t.encode()).hexdigest() for t in texts],
        'text_does_not_contain_outcomes':True,'assets':assets,'projection_seed':SEED,
        'projection_width':256,'head':'TRAIN_ONLY_RIDGE_ONEHOT_POSITIVE_SIMPLEX',
        'alpha_grid':[.1,1.,10.],'selection':'VALIDATION_BRIER_ONLY','fixed_mix_numeric_weight':.5,
        'numeric_baseline':'NEW_CAUSAL_5MIN_BAR_PROFILE_NOT_OLD_LIVE_CHECKPOINT',
        'legacy_numeric_checkpoint_evaluated':False,'slippage_grid_bps':[0.,.5,1.,2.],
        'device':c['device'],'broker_orders_sent':0,'promotion_authority':False}
    write_once(out/'plan.json',plan)
    os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',USE_TF='0',TOKENIZERS_PARALLELISM='false')
    from sandbox import enter
    sandbox=enter({'output_dir':str(out),'period_path':c['dataset'],'legacy_capture':c['dataset'],
        'oms_path':c['dataset'],'export_dir':c['dataset'],
        'read_only_paths':[str(Path(__file__).resolve().parents[2]),sys.prefix,c['mmbert_directory'],
            c['english_directory'],c['mmbert_receipt'],c['english_receipt']]})
    write_once(out/'sandbox.json',sandbox)
    import numpy as np
    import torch
    from torch.nn import functional as F
    from safetensors.torch import load_file,save_file
    from laya.common import build_model
    from transformers import AutoTokenizer
    from paired_net_100k_preflight.core import project_fixed_cpu
    torch.set_num_threads(2);device=c['device']
    if device not in ('cpu','cuda'):raise EvidenceError('DEVICE_SCOPE')
    if device=='cuda':
        if not torch.cuda.is_available() or not torch.version.hip:raise EvidenceError('ROCM_REQUIRED')
        free,total=torch.cuda.mem_get_info()
        if free<6*1024**3:raise EvidenceError('INSUFFICIENT_FREE_GPU_MEMORY_NO_SERVICE_INTERRUPTION')
        torch.cuda.set_per_process_memory_fraction(min(.4,4*1024**3/total))
    arrays={'numeric':np.array([input_vector(r) for r in all_rows],dtype=np.float64)};tensors={};encoder_stats={}
    for arm in ('mmbert','english'):
        start=time.monotonic();base=Path(c[arm+'_directory'])
        cfg=read(base/'rl_agent_config.json')
        model=build_model(cfg,encoder_dir=str(base/'encoder'),pretrained=False)
        weights=load_file(str(base/'model.safetensors'),device='cpu');model.load_state_dict(weights,strict=True);del weights
        model.requires_grad_(False).float().eval().to(device)
        tokenizer=AutoTokenizer.from_pretrained(base/'tokenizer',local_files_only=True,trust_remote_code=False)
        vectors=[];token_counts=[]
        for index,text in enumerate(texts):
            tokenized=tokenizer(text,return_tensors='pt',truncation=False)
            count=int(tokenized['attention_mask'].sum());token_counts.append(count)
            if count>1024:raise EvidenceError('NO_SILENT_INPUT_TRUNCATION')
            batch={k:v.to(device) for k,v in tokenized.items() if k in ('input_ids','attention_mask')}
            with torch.inference_mode():
                h=model.encoder(**batch).last_hidden_state
                mask=batch['attention_mask'].unsqueeze(-1).to(h.dtype)
                vector=F.normalize((h*mask).sum(1)/mask.sum(1),dim=-1)
            vectors.append(vector.cpu())
            if index%128==0:print(json.dumps({'arm':arm,'encoded':index+1,'total':len(texts)}),flush=True)
        raw=torch.cat(vectors,0);projected=project_fixed_cpu(raw,SEED,256)
        tensors[arm]=projected;arrays[arm]=projected.numpy().astype(np.float64)
        encoder_stats[arm]={'rows':len(texts),'tokens_min':min(token_counts),'tokens_max':max(token_counts),
            'wall_seconds':time.monotonic()-start,'frozen':True,'strict_full_checkpoint':True,
            'feature_sha256':hashlib.sha256(projected.numpy().tobytes()).hexdigest()}
        del model,tokenizer,raw,projected,h,mask,vector,batch,vectors
        gc.collect()
        if device=='cuda':torch.cuda.empty_cache()
    save_file(tensors,str(out/'paired-frozen-features.safetensors'))
    n=SIZES['train'];v=SIZES['validation'];labels=np.array([r['class'] for r in all_rows])
    means=np.array([np.mean([r['target_bps'] for r in chosen['train'] if r['class']==k]) for k in range(3)])
    if not np.isfinite(means).all():raise EvidenceError('TRAIN_CLASS_COVERAGE')
    probs={};heads={};head_arrays={}
    for arm,x in arrays.items():
        head,transform=fit(np,x[:n],labels[:n],x[n:n+v],labels[n:n+v]);probs[arm]=probabilities(np,transform(x[n+v:]),head['weights'])
        heads[arm]={'alpha':head['alpha'],'validation_trials':head['trials']}
        for key in ('weights','mean','std'):head_arrays[arm+'_'+key]=head[key]
    for arm in ('mmbert','english'):probs[arm+'_fixed_mix']=.5*probs['numeric']+.5*probs[arm]
    np.savez_compressed(out/'fitted-readouts.npz',**head_arrays)
    y=labels[n+v:];baseline=brier(np,probs['numeric'],y);scores={}
    for arm,pred in probs.items():
        score=brier(np,pred,y)
        scores[arm]={'test_brier':float(score.mean()),'test_accuracy':float((pred.argmax(1)==y).mean()),
            'paired_vs_numeric':paired_day_interval(np,chosen['test'],baseline-score),
            'simulated_cost_scenarios':[net_summary(np,chosen['test'],pred,means,s) for s in (0.,.5,1.,2.)]}
    scores['no_trade']={'simulated_net_usd':0.,'trades':0,'classification_metric':None}
    report={'schema':'heptatrader.offline-paired-price-result.v1','plan_sha256':sha(out/'plan.json'),
        'at_ms':int(time.time()*1000),'encoders':encoder_stats,'heads':heads,'scores':scores,
        'feature_file_sha256':sha(out/'paired-frozen-features.safetensors'),
        'readout_file_sha256':sha(out/'fitted-readouts.npz'),'test_predictions':{k:v.tolist() for k,v in probs.items()},
        'source_data_rows':{k:len(v) for k,v in data['groups'].items()},'evaluated_rows':SIZES,
        'source_bar_profile_live_compatible':False,'legacy_numeric_checkpoint_evaluated':False,
        'actual_execution_rewards_used':0,'financial_value_head_trained':False,
        'state':'OFFLINE_EXPLORATORY_NO_PROMOTION','new_live_window_registered':False,'broker_orders_sent':0}
    write_once(out/'result.json',report)
    print(json.dumps({'state':report['state'],'test_brier':{k:r.get('test_brier') for k,r in scores.items()},
        'result_path':str(out/'result.json')},indent=2),flush=True)
if __name__=='__main__':main()
