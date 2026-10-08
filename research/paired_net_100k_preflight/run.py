"""Freeze same-original-request paired encoder features. No rewards or orders."""
from __future__ import annotations
import argparse,hashlib,json,os,time,sys
from pathlib import Path
from .core import PROFILE,PreflightError,paired_items,project_fixed_cpu,canonical,digest

ARM_META={
 'mmbert':('convaiinnovations/laya-multilingual','e4e9ddf21a7b1903b7acffd8814ad4307bf63a67',768),
 'english':('convaiinnovations/laya','fe2b7719c095b82cdb2bfeedefc1fee30e506c21',1024)
}

def sha(path):
 f=hashlib.sha256()
 with Path(path).open('rb') as stream:
  for b in iter(lambda:stream.read(4*1024*1024),b''):f.update(b)
 return f.hexdigest()
def save(path,obj):
 raw=canonical(obj)+b'\n'
 with Path(path).open('xb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
 Path(path).chmod(0o400)
def asset_manifest(path,receipt,arm):
 v=json.loads(Path(receipt).read_bytes());repo,revision,dim=ARM_META[arm]
 if v.get('repository')!=repo or v.get('revision')!=revision:raise PreflightError('UPSTREAM_REVISION_MISMATCH')
 fs=v.get('files') or {}
 if set(fs)!={'model.safetensors','rl_agent_config.json','encoder/config.json',
              'tokenizer/tokenizer.json','tokenizer/tokenizer_config.json'}:raise PreflightError('UPSTREAM_FILE_INVENTORY')
 for relative,meta in fs.items():
  p=Path(path)/relative
  if not p.is_file() or p.stat().st_size!=meta['size'] or sha(p)!=meta['sha256']:
   raise PreflightError('PINNED_PRETRAINED_ASSET_CHANGED:'+relative)
 cfg=json.loads((Path(path)/'encoder/config.json').read_bytes())
 if cfg.get('hidden_size')!=dim:raise PreflightError('ENCODER_WIDTH_INCORRECT')
 return {'model_repo':repo,'revision':revision,'hidden_size':dim,
         'inventory_sha256':digest(fs),'model_file_sha256':fs['model.safetensors']['sha256'],
         'tokenizer_file_sha256':fs['tokenizer/tokenizer.json']['sha256'],
         'frozen_complete_checkpoint':True}

def main():
 p=argparse.ArgumentParser();p.add_argument('--period',type=Path,required=True)
 p.add_argument('--producer',type=Path,required=True)
 p.add_argument('--mmbert-dir',type=Path,required=True);p.add_argument('--english-dir',type=Path,required=True)
 p.add_argument('--mmbert-receipt',type=Path,required=True);p.add_argument('--english-receipt',type=Path,required=True)
 p.add_argument('--output',type=Path,required=True);a=p.parse_args()
 os.umask(0o077);os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',USE_TF='0')
 os.environ['TOKENIZERS_PARALLELISM']='false'
 from laya_cell.financial_forward_source import ingress
 from laya_cell.integrity import read_json
 period_hash=sha(a.period);period=read_json(a.period)
 if period.get('execution_domain')!='PAPER':raise PreflightError('PAPER_ONLY')
 m=read_json(a.producer)
 if (m.get('behavior_profile')!=PROFILE['behavior_profile'] or
     m.get('period_sha256')!=period_hash or
     m.get('execution_domain')!='PAPER' or
     m.get('fx_churn_profile_sha256')):
  raise PreflightError('CURRENT_100K_PRODUCER_REQUIRED')
 manifest_sha=sha(a.producer)
 _,requests,bindings,issues=ingress({'manifest':str(a.producer),'sha256':manifest_sha},period_hash)
 if issues:raise PreflightError('ATTRIBUTION_ISSUES')
 eligible=[v for v in requests.values() if v['request'].get('position_at_request')==0]
 sample=paired_items(eligible,manifest_sha,period_hash)
 if any(x['request_id'] not in requests for x in sample):raise PreflightError('REQUEST_SET')
 assets={}
 for arm,base,receipt in (('mmbert',a.mmbert_dir,a.mmbert_receipt),
                          ('english',a.english_dir,a.english_receipt)):
  assets[arm]=asset_manifest(base,receipt,arm)
 out=a.output
 if out.exists():raise PreflightError('EXISTING_FEATURE_OUTPUT_REFUSE_REPLACE')
 out.mkdir(mode=0o700,parents=True)
 publicly_safe=[{k:v for k,v in s.items() if k!='text'} for s in sample]
 input_registration={'schema':PROFILE['schema'],'at_ms':int(time.time()*1000),'profile':PROFILE,
   'period_sha256':period_hash,'producer_manifest_sha256':manifest_sha,
   'source_request_count':len(requests),'eligible_flat_original_requests':len(eligible),
   'original_bindings':len(bindings),'input_rows':publicly_safe,
   'input_digest':digest(publicly_safe),'assets':assets,
   'no_value_labels_read':True,'no_outcome_training':True,'trade_authority':False}
 save(out/'input-registration.json',input_registration)
 # Enforce inherited Landlock and no new sockets before loading any ML code.
 from sandbox import enter
 sandbox=enter({'output_dir':str(out),'period_path':str(a.period),'legacy_capture':str(a.period),
  'oms_path':str(a.period),'export_dir':str(a.producer),
  'read_only_paths':[str(Path(__file__).resolve().parents[2]),str(a.mmbert_dir),str(a.english_dir),
    str(a.mmbert_receipt),str(a.english_receipt),str(a.producer),str(m['journal_directory']),
    str(Path(sys.prefix))]})
 save(out/'sandbox.json',sandbox)
 import torch,gc
 from torch.nn import functional as F
 from safetensors.torch import load_file,save_file
 from laya.common import build_model
 from transformers import AutoTokenizer
 if not torch.cuda.is_available() or not torch.version.hip or not str(torch.cuda.get_device_properties(0).gcnArchName).startswith('gfx1150'):
  raise PreflightError('POCKET4_ROCM_REQUIRED')
 torch.set_num_threads(2)
 projections={};metadata={}
 texts=[s['text'] for s in sample]
 for arm,base in (('mmbert',a.mmbert_dir),('english',a.english_dir)):
  t0=time.monotonic();cfg=json.loads((base/'rl_agent_config.json').read_bytes())
  model=build_model(cfg,encoder_dir=str(base/'encoder'),pretrained=False)
  weights=load_file(str(base/'model.safetensors'),device='cpu')
  loaded=model.load_state_dict(weights,strict=True)
  if loaded.missing_keys or loaded.unexpected_keys:raise PreflightError('FULL_CHECKPOINT_NOT_STRICT')
  del weights
  model.requires_grad_(False).float().eval().to('cuda')
  tokenizer=AutoTokenizer.from_pretrained(base/'tokenizer',local_files_only=True,trust_remote_code=False)
  vectors=[];lengths=[]
  torch.cuda.reset_peak_memory_stats()
  for i in range(0,len(texts),2):
   batch=tokenizer(texts[i:i+2],return_tensors='pt',padding=True,truncation=False)
   counts=batch['attention_mask'].sum(-1).tolist()
   if any(n>PROFILE['max_tokens_each_arm'] for n in counts):
    raise PreflightError('UNPAIRED_OR_TRUNCATED_MODEL_INPUT')
   lengths.extend(counts)
   inputs={k:v.to('cuda') for k,v in batch.items() if k in ('input_ids','attention_mask')}
   with torch.inference_mode():
    h=model.encoder(**inputs).last_hidden_state
    mask=inputs['attention_mask'].unsqueeze(-1).to(h.dtype)
    x=F.normalize(((h*mask).sum(1)/mask.sum(1)).float(),dim=-1)
   vectors.append(x.cpu())
  tensor=torch.cat(vectors,0).contiguous()
  if tensor.shape!=(len(sample),ARM_META[arm][2]) or not torch.isfinite(tensor).all():
   raise PreflightError('BAD_ACTUAL_FROZEN_ENCODER')
  projected=project_fixed_cpu(tensor,PROFILE['feature_projection_seed'],256)
  if not torch.isfinite(projected).all():raise PreflightError('BAD_PROJECTION')
  projections[arm+'_raw']=tensor
  projections[arm+'_fixed256']=projected
  metadata[arm]={'tokens':lengths,'width':tensor.shape[1],
    'raw_sha256':hashlib.sha256(tensor.numpy().tobytes()).hexdigest(),
    'fixed256_sha256':hashlib.sha256(projected.numpy().tobytes()).hexdigest(),
    'strict_full_checkpoint':True,'trainable_model_parameters':0,
    'fixed_projection_not_trained':True,'encoder_wall_seconds':time.monotonic()-t0,
    'peak_gpu_allocated_bytes':torch.cuda.max_memory_allocated()}
  print('PAIRED_ARM',arm,'ROWS',len(lengths),'TOKENS_MINMAX',min(lengths),max(lengths),flush=True)
  del model,tokenizer,tensor,projected,h,mask,x
  gc.collect();torch.cuda.empty_cache()
 assert sorted(projections)==['english_fixed256','english_raw','mmbert_fixed256','mmbert_raw']
 save_file(projections,str(out/'frozen-paired-features.safetensors'))
 (out/'frozen-paired-features.safetensors').chmod(0o400)
 now=int(time.time()*1000)
 final={'schema':'heptatrader.current100k-english-paired-feature-proof.v1',
   'at_ms':now,'input_registration_sha256':sha(out/'input-registration.json'),
   'feature_file_sha256':sha(out/'frozen-paired-features.safetensors'),
   'period_sha256':period_hash,'producer_manifest_sha256':manifest_sha,
   'request_count':len(eligible),'paired_action_rows':len(sample),
   'rows_digest':digest(publicly_safe),'models':metadata,'assets':assets,
   'same_original_text_sha256_both_bases':True,'no_truncation':True,
   'target_net_usd_rows':0,'monetary_head_trained':False,
   'prospective_prediction_backdated':False,'financial_AB_started':False,
   'winner_selected':False,'actor_update':False,'order_send_count':0,'effect_authority':False}
 save(out/'feature-proof.json',final)
 print(json.dumps({k:v for k,v in final.items() if k not in ('assets','models')},indent=2))
if __name__=='__main__':main()
