"""Warm, read-only financial predictions for real requests before original OPEN.

Both action conditions are sealed regardless of the eventual choice. No model
output is sent to the trader. No optimizer, future reward or execution client is
used by this worker. Missed deadlines remain missed, never backdated.
"""
from __future__ import annotations
import argparse,fcntl,hashlib,json,os,signal,time
from pathlib import Path
from .integrity import ContractError,read_json,sha_file,digest
_original_sha_file=sha_file
def sha_file(path):return _original_sha_file(Path(path))
from .resident_store import write_once,atomic
from .financial_forward_protocol import RULE,feature_text,validate_plan,validate_candidate
from .financial_forward_source import ingress


def pin(config,plan):
 validate_plan(plan,int(time.time()*1000))
 if plan['period_sha256']!=config['period_sha256']:raise ContractError('PREDICT_PERIOD_SCOPE')
 for name,expected in plan['runtime_sha256'].items():
  if sha_file(Path(__file__).parent/name)!=expected:raise ContractError('FROZEN_FORWARD_RUNTIME_CHANGED')
 directory=Path(plan['candidate_directory'])
 c=validate_candidate(directory)
 if c['candidate_id']!=plan['candidate_id'] or sha_file(directory/'candidate.json')!=plan['candidate_manifest_sha256']:
  raise ContractError('CANDIDATE_SUBSTITUTION')
 return c

class Predictor:
 def __init__(self,config):
  self.c=config;self.root=Path(config['forward_root']);self.root.mkdir(mode=0o700,parents=True,exist_ok=True)
  self.stop=False;self.raw=None;self.tokenizer=None;self.heads={};self.started=int(time.time()*1000);self.log_ms=0
 def load(self):
  if self.raw is not None:return
  import torch
  from .model import load_verified_laya
  torch.set_num_threads(2)
  self.raw,self.tokenizer,self.base_id=load_verified_laya(Path(self.c['base_directory']),read_json(Path(self.c['base_directory'])/'base-manifest.json'))
  self.raw.requires_grad_(False).eval();self.base_bytes=sha_file(Path(self.c['base_directory'])/'model.safetensors')
  atomic(self.root/'predictor-start.json',{'at_ms':int(time.time()*1000),'base_id':self.base_id,
   'gpu_arch':str(torch.cuda.get_device_properties(0).gcnArchName),'source_scope':'ACTUAL_CLI_REQUESTS_ONLY','optimizer_used':False,'broker_orders_sent':0})
 def predict(self,plan,item):
  r=item['request'];key=plan['candidate_id'];now=int(time.time()*1000)
  if r['model_id']!=plan['behavior_scope']['behavior_model_id'] or item['producer_manifest_sha256']!=plan['behavior_scope']['producer_manifest_sha256']:
   raise ContractError('BEHAVIOR_SCOPE_CHANGED')
  if not plan['input_start_ms']<=r['started_at_ms']<plan['input_end_ms'] or r['position_at_request']!=0:return None
  folder=self.root/'plans'/plan['plan_id']/'predictions'/r['request_id']
  if (folder/'commit.json').exists() or (folder/'failure.json').exists():return None
  folder.mkdir(mode=0o700,parents=True,exist_ok=True)
  if now-r['started_at_ms']>60000:
   write_once(folder/'failure.json',{'reason':'REQUEST_ALREADY_OLD_NO_BACKDATING','observed_at_ms':now,'request_id':r['request_id']});return None
  try:
   c=pin(self.c,plan)
   self.load()
   import torch
   from safetensors.torch import load_file
   from .financial_value_train import FinancialHead,features_from_laya
   if c['base_id']!=self.base_id:raise ContractError('FINANCIAL_BASE_MISMATCH')
   if key not in self.heads:
    head=FinancialHead(torch.device('cuda'));tensors=load_file(str(Path(plan['candidate_directory'])/'value-head.safetensors'))
    if set(tensors)!=set(head.state_dict()) or any(not torch.isfinite(t).all() for t in tensors.values()):raise ContractError('FINANCIAL_TENSOR_SCOPE')
    head.load_state_dict(tensors,strict=True);head.requires_grad_(False).eval()
    if float(torch.linalg.vector_norm(torch.cat([t.float().flatten() for t in tensors.values()])))>.100001:raise ContractError('READOUT_NORM_BOUND')
    self.heads[key]=head
   head=self.heads[key]
   rows=[{'feature_text':feature_text(r,side)} for side in ('BUY','SELL')]
   begin=time.monotonic();features,info=features_from_laya(self.raw,self.tokenizer,rows)
   with torch.no_grad():values=(head(features)*100).cpu().tolist()
   if any(not torch.isfinite(t).all() for t in head.parameters()):raise ContractError('VALUE_NONFINITE')
   computed=int(time.time()*1000)
   payload={'schema':'heptatrader.financial-forward-prediction.v1','plan_id':plan['plan_id'],'candidate_id':key,
    'request_id':r['request_id'],'request_sha256':item['request_sha256'],'producer_manifest_sha256':item['producer_manifest_sha256'],
    'prompt_sha256':r['prompt_sha256'],'request_started_at_ms':r['started_at_ms'],
    'request_source_written_at_ms':item['source_written_at_ms'],'request_prefix':item['source_prefix'],
    'values_usd':dict(zip(('BUY','SELL'),values)),'computed_at_ms':computed,
    'feature_text_sha256':{side:hashlib.sha256(row['feature_text'].encode()).hexdigest() for side,row in zip(('BUY','SELL'),rows)},
    'feature_receipt':info,'inference_seconds':time.monotonic()-begin,'candidate_refit':False,
    'side_choice_provided_to_trader':False,'output_scope':'CONDITIONAL_ROUNDTRIP_NET_USD_NOT_TRADE_SIGNAL',
    'both_action_conditions_committed_without_response_input':True,'broker_orders_sent':0}
   write_once(folder/'prediction.json',payload)
   # Separate commit witness is made AFTER fsync of the actual output payload.
   # Committing after PREPARED is never rescued by changing timestamps.
   commit={'schema':'heptatrader.financial-forward-commit.v1','prediction_sha256':sha_file(folder/'prediction.json'),
    'committed_at_ms':int(time.time()*1000),'plan_id':plan['plan_id'],'request_id':r['request_id'],'candidate_id':key}
   from .financial_forward_witness import sign_record
   commit=sign_record(self.c,'predictor',commit)
   write_once(folder/'commit.json',commit)
   return commit
  except Exception as exc:
   write_once(folder/'failure.json',{'reason':type(exc).__name__+':'+str(exc)[:220],'observed_at_ms':int(time.time()*1000),'request_id':r['request_id']})
   return None
 def step(self):
  if sha_file(Path(self.c['period_path']))!=self.c['period_sha256']:raise ContractError('FINANCIAL_PREDICT_PERIOD_CHANGED')
  plans=[read_json(p) for p in sorted((self.root/'plans').glob('*/plan.json'))];now=int(time.time()*1000);new=0;issues=[]
  if len(plans)>32:raise ContractError('MAX_FORWARD_PLAN_BUDGET')
  for plan in plans:
   if now>=plan['input_end_ms']:continue
   try:
    _,requests,_,problems=ingress(plan['producer_registration'],self.c['period_sha256']);issues+=problems
    for item in sorted(requests.values(),key=lambda x:x['request']['started_at_ms']):
     if self.predict(plan,item) is not None:new+=1
   except Exception as exc:issues.append({'plan_id':plan['plan_id'],'reason':str(exc)[:220]})
  status={'schema':'heptatrader.financial-forward-predictor-status.v1','at_ms':int(time.time()*1000),
   'plans':len(plans),'new_predictions':new,'sealed_predictions':len(list((self.root/'plans').glob('*/predictions/*/commit.json'))),
   'failed_or_missed_requests':len(list((self.root/'plans').glob('*/predictions/*/failure.json'))),
   'issues':issues,'loaded_heads':len(self.heads),'actor_updated':False,'broker_orders_sent':0}
  atomic(self.root/'predictor-status.json',status)
  if new or now-self.log_ms>30000:print(json.dumps(status),flush=True);self.log_ms=now
  return status

def main():
 p=argparse.ArgumentParser();p.add_argument('--config',type=Path,required=True);p.add_argument('--once',action='store_true');a=p.parse_args();os.umask(0o077)
 c=read_json(a.config);root=Path(c['forward_root']);root.mkdir(mode=0o700,parents=True,exist_ok=True)
 fd=os.open(root/'predictor.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW|os.O_CLOEXEC,0o600);fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
 worker=Predictor(c)
 def stop(*_):worker.stop=True
 signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
 worker.load()
 while not worker.stop:
  try:worker.step()
  except Exception as exc:atomic(root/'predictor-status.json',{'at_ms':int(time.time()*1000),'state':'UNAVAILABLE_NO_TRADE_EFFECT','reason':str(exc)[:220]})
  if a.once:break
  time.sleep(.25)
 os.close(fd)
if __name__=='__main__':main()
