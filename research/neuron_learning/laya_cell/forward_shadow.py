"""Frozen three-way forward-price probe. No optimizer, no submit, no model promotion."""
from __future__ import annotations
import argparse,hashlib,json,math,os,subprocess,time
from bisect import bisect_left,bisect_right
from decimal import Decimal
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import numpy as np
import torch
from .integrity import ContractError,canonical,digest,read_json,sha_file
from .dataset import causal_state,question,encode_rows,BIN_EDGES_BPS
from .model import TradingDecisionCell,load_verified_laya
from .native import NativePorts

def source(path):
 from .shadow_source import read_source
 return read_source(path)

def exclusive(path,value):
 path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
 with path.open('xb') as f:f.write(canonical(value)+b'\n');f.flush();os.fsync(f.fileno())
 path.chmod(0o400)

def remote_prediction(state,horizon):
 # Same observable input and a fixed horizon, with a separately recorded prompt.
 # This is a shadow forecast, not a resident strategy decision or vendor weight pin.
 prompt=('Computation-only future-price forecast. No tools, trading or actions. Given the complete historical quote input below, return JSON with exactly one key probabilities containing 7 nonnegative numbers summing to 1. Predict the underlying MID return at the fixed horizon, not execution profit. Bins in order are (-infinity,-10), [-10,-3), [-3,-1), [-1,1), [1,3), [3,10), [10,infinity), in basis points. Do not use future data. Input='+canonical(state).decode())
 model='openai/gpt-5.6-luna';exe=Path.home()/'.npm-global/bin/openclaw';start=int(time.time()*1000)
 p=subprocess.run([str(exe),'infer','model','run','--local','--model',model,'--thinking','low','--prompt',prompt,'--json'],capture_output=True,text=True,timeout=150)
 if p.returncode:raise ContractError('REMOTE_SHADOW_REQUEST_FAILED')
 envelope=json.loads(p.stdout)
 if envelope.get('ok') is not True or len(envelope.get('outputs',[]))!=1:raise ContractError('REMOTE_SHADOW_ENVELOPE')
 out=json.loads(envelope['outputs'][0]['text']);probs=out.get('probabilities')
 if set(out)!={'probabilities'} or not isinstance(probs,list) or len(probs)!=7 or any(type(x) not in (int,float) or not math.isfinite(x) or not 0<=x<=1 for x in probs) or abs(sum(probs)-1)>1e-6:raise ContractError('REMOTE_DISTRIBUTION_INVALID')
 return {'kind':'remote','model_id':model+':seven-bin-shadow-v1','probabilities':probs,'available_at_ms':int(time.time()*1000),'request_started_ms':start,'prompt_sha256':hashlib.sha256(prompt.encode()).hexdigest(),'no_local_updates_during_probe':True,'vendor_weights_frozen_claimed':False}

def validation_window(config,candidate,baseline):
 path=Path(config['validation_plan']);m=read_json(path)
 if sha_file(path)!=config['validation_plan_sha256']:raise ContractError('VALIDATION_PLAN_FILE_CHANGED')
 if m.get('manifest_sha256')!=digest({k:v for k,v in m.items() if k!='manifest_sha256'}):raise ContractError('VALIDATION_PLAN_DIGEST')
 if (m.get('schema')!='heptatrader.laya-independent-validation.v1' or
     m.get('candidate_id')!=candidate['candidate_id'] or m.get('bootstrap_model_id')!=baseline['model_id'] or
     m.get('horizon_sec')!=300 or m.get('automatic_selection') is not False or
     m.get('source_file')!=str(Path(config['shadow_directory'])/'quotes.jsonl')):
  raise ContractError('VALIDATION_PLAN_SCOPE')
 if not candidate['created_at_ms']<=m['registered_at_ms']<m['validation_start_ms']<m['validation_end_ms']:
  raise ContractError('VALIDATION_PLAN_NOT_PROSPECTIVE')
 if sha_file(Path(__file__))!=m['evaluator_source_sha256']:raise ContractError('EVALUATOR_CHANGED_AFTER_REGISTRATION')
 return m

def predict(config):
 from data import make_sample
 from safetensors.torch import load_file
 root=Path(config['shadow_directory']);candidate_dir=Path(config['candidate_directory']);candidate=read_json(candidate_dir/'candidate.json')
 if candidate.get('candidate_id')!=digest({k:v for k,v in candidate.items() if k!='candidate_id'}):raise ContractError('CANDIDATE_MANIFEST_HASH')
 if candidate.get('status')!='REQUIRES_INDEPENDENT_FUTURE_SHADOW' or candidate.get('automatic_cutover') is not False:raise ContractError('CANDIDATE_ROLE')
 if sha_file(candidate_dir/'adapter.safetensors')!=candidate['adapter_sha256']:raise ContractError('ADAPTER_DIGEST')
 from .train import verify_baseline,verify_old_learner_inactive
 verify_old_learner_inactive()
 bootstrap_root=Path(config['baseline_directory']);verify_baseline(bootstrap_root);baseline=read_json(bootstrap_root/'model.json');freeze=read_json(bootstrap_root/'freeze-manifest.json')
 if sha_file(bootstrap_root/'model.json')!=freeze['inventory_sha256']['model.json']:raise ContractError('BASELINE_CHANGED')
 window=validation_window(config,candidate,baseline)
 raw,tok,base_id=load_verified_laya(Path(config['base_directory']),read_json(Path(config['base_directory'])/'base-manifest.json'))
 if candidate['base_id']!=base_id:raise ContractError('CANDIDATE_BASE_MISMATCH')
 cell=TradingDecisionCell(raw,base_id);cell.restore_trainable(load_file(str(candidate_dir/'adapter.safetensors')));cell.eval()
 header,quotes,prefix=source(root/'quotes.jsonl');sampled=[]
 for q in reversed(quotes):
  if not sampled or sampled[-1]['t']-q['t']>=10000:sampled.append(q)
 history=list(reversed(sampled[:24]))
 if len(history)<6 or history[-1]['t']<int(time.time()*1000)-5000:raise ContractError('FRESH_SHADOW_HISTORY_REQUIRED')
 period=Path.home()/'.config/heptatrader/pnl-period.json'
 if sha_file(period)!=header['period_sha256']:raise ContractError('CURRENT_PERIOD_MISMATCH')
 anchor=history[-1]['t'];scope=header['period_sha256']+':EUR.USD';known=int(time.time()*1000)
 if max(b['t']-a['t'] for a,b in zip(history,history[1:]))>30000:raise ContractError('SHADOW_INPUT_GAP')
 if candidate['created_at_ms']>=anchor or baseline['trained_until_ms']>anchor:raise ContractError('MODEL_MUST_PRECEDE_SHADOW_ANCHOR')
 if not window['validation_start_ms']<=anchor<window['validation_end_ms']:raise ContractError('ANCHOR_OUTSIDE_RESERVED_WINDOW')
 sample=make_sample(history,known,scope,'CAUSAL_QUOTE_WINDOW');state=causal_state(sample,'EUR.USD',300);input_id=digest(state)
 plan={'schema':'heptatrader.laya-forward-probe.v1','created_at_ms':known,'input_id':input_id,'scope':scope,'horizon_sec':300,'anchor_ms':anchor,'state':state,'source_prefix':prefix,'candidate_id':candidate['candidate_id'],'bootstrap_model_id':baseline['model_id'],'effect_authority':False,'no_training_after_probe':True,'outcome_end_ms':anchor+305000}
 plan['validation_manifest_sha256']=window['manifest_sha256']
 exclusive(root/'probe-plan.json',plan)
 records=[]
 with ThreadPoolExecutor(max_workers=1) as pool:
  future=pool.submit(remote_prediction,state,300)
  native=NativePorts(Path(config['native_binary']),config['native_binary_sha256'])
  receipt=native.call({'op':'neuron','episodes':[sample['native_episode']]})
  x=np.asarray(sample['raw_features']+receipt['episodes'][0]['activation'],dtype=np.float64);w=np.asarray(baseline['weights'],dtype=np.float64)
  z=np.tanh(x@w[:512].reshape(32,16)+w[512:528]);p=z@w[528:560].reshape(16,2)+w[560:562]
  records.append({'kind':'bootstrap','model_id':baseline['model_id'],'training_end_ms':baseline['trained_until_ms'],'point_return_bps':float(p[0]*100),'available_at_ms':int(time.time()*1000),'inference_backend':'CPU_NUMERIC_REFERENCE_NO_TRAINING'})
  with torch.no_grad():
   batch={k:v.to('cuda') for k,v in encode_rows(tok,[{'state':state,'question':question(300)}]).items()};probs=cell(**batch)[0].softmax(-1)[0].cpu().tolist()
  records.append({'kind':'laya','model_id':candidate['candidate_id'],'training_end_ms':candidate['window']['training_end_ms'],'probabilities':probs,'available_at_ms':int(time.time()*1000),'gpu_arch':str(torch.cuda.get_device_properties(0).gcnArchName)})
  try:records.append(future.result())
  except Exception as exc:records.append({'kind':'remote','status':'UNAVAILABLE','reason':type(exc).__name__+':'+str(exc)[:150]})
 for r in records:r.update({k:plan[k] for k in ('input_id','scope','horizon_sec','anchor_ms')})
 receipt={'schema':'heptatrader.laya-frozen-predictions.v1','plan_sha256':sha_file(root/'probe-plan.json'),'records':records,'created_at_ms':int(time.time()*1000),'broker_orders_sent':0,'model_replacement_performed':False}
 exclusive(root/'predictions.json',receipt);cell.assert_frozen();print(json.dumps(receipt,indent=2))

def evaluate(config,wait_seconds):
 root=Path(config['shadow_directory']);plan=read_json(root/'probe-plan.json');pred=read_json(root/'predictions.json');deadline=time.monotonic()+wait_seconds
 candidate=read_json(Path(config['candidate_directory'])/'candidate.json');baseline=read_json(Path(config['baseline_directory'])/'model.json')
 window=validation_window(config,candidate,baseline)
 if plan.get('validation_manifest_sha256')!=window['manifest_sha256'] or not window['validation_start_ms']<=plan['anchor_ms']<window['validation_end_ms']:raise ContractError('PROBE_OUTSIDE_VALIDATION_PLAN')
 if pred.get('plan_sha256')!=sha_file(root/'probe-plan.json'):raise ContractError('PREDICTION_PLAN_HASH')
 if plan['input_id']!=digest(plan['state']):raise ContractError('PROBE_INPUT_IDENTITY')
 if plan['created_at_ms']>=plan['anchor_ms']+300000 or pred['created_at_ms']>=plan['anchor_ms']+300000:raise ContractError('PROBE_NOT_COMMITTED_BEFORE_OUTCOME')
 kinds=[r.get('kind') for r in pred['records']]
 if sorted(kinds)!=['bootstrap','laya','remote']:raise ContractError('PROBE_KIND_SET')
 for r in pred['records']:
  if any(r.get(k)!=plan[k] for k in ('input_id','scope','horizon_sec','anchor_ms')):raise ContractError('CROSS_INPUT_PREDICTION')
  if r['kind']=='laya' and r.get('model_id')!=plan['candidate_id']:raise ContractError('CANDIDATE_REPLACED_IN_PROBE')
  if r['kind']=='bootstrap' and r.get('model_id')!=plan['bootstrap_model_id']:raise ContractError('BASELINE_REPLACED_IN_PROBE')
 while True:
  header,qs,prefix=source(root/'quotes.jsonl');times=[q['t'] for q in qs];due=plan['anchor_ms']+300000;end=bisect_left(times,due);now=int(time.time()*1000)
  if end<len(times) or now>due+15000 or time.monotonic()>=deadline:break
  time.sleep(2)
 reason=None;start=bisect_left(times,plan['anchor_ms'])
 if header['period_sha256']+':EUR.USD'!=plan['scope']:reason='PERIOD_CHANGED'
 elif start>=len(times) or times[start]!=plan['anchor_ms']:reason='ANCHOR_MISSING'
 elif end>=len(times):reason='OUTCOME_MISSING' if now>due+5000 else 'PENDING'
 elif times[end]>due+5000:reason='ENDPOINT_GAP'
 elif any(b-a>30000 for a,b in zip(times[start:end],times[start+1:end+1])):reason='PATH_GAP'
 if reason=='PENDING':
  print(json.dumps({'status':'PENDING','input_id':plan['input_id'],'strategy_accepted':False}));return
 result={'schema':'heptatrader.laya-three-way-forward-result.v1','input_id':plan['input_id'],'observed_at_ms':now,'source_prefix':prefix,'status':'CENSORED' if reason else 'OBSERVED','reason':reason,'results':{},'strategy_accepted':False,'model_replacement_performed':False,'broker_orders_sent':0,'metric_scope':'FUTURE_PRICE_PREDICTION_NOT_EXECUTION_PNL','independent_windows':0 if reason else 1}
 if not reason:
  value=(qs[end]['mid']/qs[start]['mid']-1)*10000;truth=bisect_right(BIN_EDGES_BPS,value);result.update(return_bps=value,true_interval=truth,outcome_source_ms=times[end])
  for r in pred['records']:
   if r.get('status')=='UNAVAILABLE':result['results'][r['kind']]=r;continue
   if r['available_at_ms']>=due or r.get('training_end_ms',0)>plan['anchor_ms']:result['results'][r['kind']]={'status':'INVALID_LOOKAHEAD'};continue
   if r['kind']=='bootstrap':guess=bisect_right(BIN_EDGES_BPS,r['point_return_bps']);score=None
   else:
    probs=r['probabilities'];guess=max(range(7),key=lambda i:probs[i]);score=sum((p-(i==truth))**2 for i,p in enumerate(probs))
   result['results'][r['kind']]={'status':'OBSERVED','model_id':r['model_id'],'predicted_interval':guess,'correct_interval':guess==truth,'brier_score':score}
 result['predictions_sha256']=sha_file(root/'predictions.json')
 result['plan_sha256']=sha_file(root/'probe-plan.json')
 result['evaluated_by']='SEPARATE_NO_OPTIMIZER_PROCESS'
 exclusive(root/'result.json',result);print(json.dumps(result,indent=2))

def main():
 p=argparse.ArgumentParser();p.add_argument('operation',choices=['predict','evaluate']);p.add_argument('--config',type=Path,required=True);p.add_argument('--wait-seconds',type=int,default=360);a=p.parse_args();os.umask(0o077);torch.set_num_threads(2);config=read_json(a.config)
 if a.operation=='predict':predict(config)
 else:evaluate(config,a.wait_seconds)
if __name__=='__main__':main()
