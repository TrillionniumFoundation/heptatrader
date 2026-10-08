"""Fixed-window causal predictor; original trader never calls this process."""
from __future__ import annotations
import argparse,fcntl,os,signal,time
from pathlib import Path
import torch
from torch.nn import functional as F
from safetensors.torch import load_file
from laya_cell.integrity import ContractError,read_json,digest,sha_file
from laya_cell.resident_store import write_once,atomic
from laya_cell.resident_data import read_quotes,forecast_input
from laya_cell.model import load_verified_laya,require_pocket4_gpu
from laya_cell.temporal_fusion_model import TemporalLayaFusion,padded_sequences
from .common import PROFILE,RULE,ARMS,require,check_candidate,check_plan,utc_ms
from .data import model_state_text
from .model import PairModel

class Predictor:
 def __init__(self,c):
  self.c=c;self.root=Path(c['shadow_root']);self.plan=check_plan(read_json(self.root/'plan.json'),utc_ms())
  self.candidate=check_candidate(c['candidate_directory']);require(self.candidate['candidate_id']==self.plan['candidate_id'],'CANDIDATE_PLAN_MISMATCH')
  for name,expected in self.plan['runtime_sha256'].items():
   require(sha_file(Path(c['runtime_directory'])/name)==expected,'PREDICTOR_RUNTIME_CHANGED')
  base=Path(c['base_directory']);self.device=require_pocket4_gpu()
  raw,tok,base_id=load_verified_laya(base,read_json(base/'base-manifest.json'))
  require(base_id==self.candidate['base_id'] and sha_file(base/'model.safetensors')==self.candidate['base_checkpoint_sha256'],'FROZEN_LAYA_BASE_CHANGED')
  raw.eval().requires_grad_(False);self.raw=raw;self.tokenizer=tok
  self.pair=PairModel().to(self.device);self.pair.load_state_dict(load_file(str(Path(c['candidate_directory'])/'pair.safetensors')),strict=True)
  self.pair.eval().requires_grad_(False)
  old=Path(c['fusion_candidate_directory'])
  require(sha_file(old/'fusion.safetensors')==self.candidate['legacy_numeric_sha256'],'LEGACY_BASELINE_CHANGED')
  self.legacy=TemporalLayaFusion().to(self.device)
  self.legacy.load_state_dict(load_file(str(old/'fusion.safetensors')),strict=True);self.legacy.eval().requires_grad_(False)
  self.stop=False
 def step(self):
  now=utc_ms();require(sha_file(Path(self.c['period_path']))==self.c['period_sha256'],'PREDICTION_PERIOD_CHANGED')
  new=0;reasons=[]
  for i in range(RULE['slots']):
   start=self.plan['start_ms']+i*RULE['slot_spacing_ms'];late=start+RULE['input_grace_ms']
   path=self.root/f'prediction-{i:03}.json';failure=self.root/f'failure-{i:03}.json'
   if path.exists() or failure.exists() or now<start:continue
   if now>late:
    write_once(failure,{'slot':i,'reason':'NO_VALID_INPUT_IN_REGISTERED_SLOT','evaluated_at_ms':now});continue
   try:
    quotes,prefixes=read_quotes(self.c,now)
    sample,row=forecast_input(self.c,quotes,now)
    if sample['anchor_ms']<start:continue
    require(self.candidate['trained_until_ms']<sample['anchor_ms'],'TRAINING_LEAKAGE')
    require(self.candidate['created_at_ms']<sample['anchor_ms'],'CANDIDATE_NOT_FROZEN_BEFORE_INPUT')
    text=model_state_text(row['state'])
    batch=self.tokenizer([text],padding=True,truncation=False,return_tensors='pt')
    tokens=int(batch['attention_mask'].sum())
    require(tokens<=PROFILE['encoder_max_tokens'],'SILENT_ENCODER_TRUNCATION')
    batch={k:v.to(self.device) for k,v in batch.items() if k in ('input_ids','attention_mask')}
    x,length=padded_sequences([sample],self.device)
    with torch.inference_mode():
     hidden=self.raw.encoder(**batch).last_hidden_state
     mask=batch['attention_mask'].unsqueeze(-1).to(hidden.dtype)
     features=F.normalize(((hidden*mask).sum(1)/mask.sum(1)).float(),dim=-1)
     legacy=self.legacy.numeric(x,length)[1].softmax(-1)
     pred=self.pair.predict(x,length,features,legacy)
    made=utc_ms()
    require(made<=late and made<sample['anchor_ms']+RULE['horizon_ms'],'PREDICTION_DEADLINE')
    arms={name:[float(v) for v in pred[name][0].cpu()] for name in ARMS}
    if set(arms)!=set(ARMS):raise ContractError('PREDICTION_ARM_DENOMINATOR')
    result={'schema':'heptatrader.paired-price-prediction.v1','plan_id':self.plan['plan_id'],
      'candidate_id':self.candidate['candidate_id'],'slot':i,'anchor_ms':sample['anchor_ms'],
      'created_at_ms':made,'input_id':digest(row['state']),'input_state':row['state'],
      'sample_id':sample['sample_id'],'reference_mid':sample['reference_mid'],
      'arms':arms,'numeric_weight':float(pred['numeric_weight'][0][0]),
      'source_prefixes':prefixes,'encoder_tokens':tokens,'horizon_sec':300,
      'train_label_cutoff_ms':self.candidate['trained_until_ms'],
      'instrument':'EUR.USD','effect_authority':False,'broker_orders_sent':0}
    write_once(path,result);new+=1
   except ContractError as exc:
    # A transient quote gap can recover within the ORIGINAL 60-second grace.
    reasons.append(str(exc))
    if utc_ms()>late:write_once(failure,{'slot':i,'reason':str(exc),'evaluated_at_ms':utc_ms()})
  status={'schema':'heptatrader.paired-price-predictor-status.v1','at_ms':utc_ms(),
    'new_predictions':new,'total_predictions':len(list(self.root.glob('prediction-*.json'))),
    'censored_or_missing_slots':len(list(self.root.glob('failure-*.json'))),
    'recent_input_reasons':reasons[:5],'candidate_id':self.candidate['candidate_id'],
    'actor_updated':False,'broker_orders_sent':0}
  atomic(self.root/'predictor-status.json',status)
  return status

def main():
 p=argparse.ArgumentParser();p.add_argument('--config',type=Path,required=True);p.add_argument('--once',action='store_true');args=p.parse_args()
 os.umask(0o077);c=read_json(args.config);root=Path(c['shadow_root']);root.mkdir(mode=0o700,parents=True,exist_ok=True)
 fd=os.open(root/'predictor.lock',os.O_WRONLY|os.O_CREAT|os.O_CLOEXEC|os.O_NOFOLLOW,0o600);fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
 worker=Predictor(c)
 def stop(*_):worker.stop=True
 signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
 while not worker.stop:
  try:worker.step()
  except Exception as exc:atomic(root/'predictor-status.json',{'state':'UNAVAILABLE_NO_TRADING','reason':str(exc)[:160],'at_ms':utc_ms()})
  if args.once:break
  time.sleep(1)
 os.close(fd)
if __name__=='__main__':main()
