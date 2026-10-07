"""Read-only same-input full-checkpoint GPU parity probe; not market qualification."""
from pathlib import Path
from datetime import datetime,timezone
import gc,hashlib,json,os,time
os.environ.setdefault('HF_HUB_OFFLINE','0')
os.environ.setdefault('USE_TF','0')
import torch
from huggingface_hub import HfApi
from safetensors.torch import load_file
from transformers import AutoTokenizer
from laya.common import build_model

H=Path.home();E=H/'.local/state/heptatrader/operator-evidence/neuron256-roadmap-20261008';E.mkdir(mode=0o700,parents=True,exist_ok=True)
def sha(path):
 d=hashlib.sha256()
 with path.open('rb') as f:
  for b in iter(lambda:f.read(4*1024*1024),b''):d.update(b)
 return d.hexdigest()
def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
def save(name,value):
 p=E/name
 with p.open('x') as f:f.write(json.dumps(value,indent=2)+'\n')
 p.chmod(0o400)

def verify(repository,revision,path):
 info=HfApi(token=False).model_info(repository,revision=revision,files_metadata=True)
 if len(info.sha)!=40:raise ValueError('PIN_RESOLUTION')
 siblings={s.rfilename:s for s in info.siblings};local_files={}
 selected=[p for p in path.rglob('*') if p.is_file() and (p.name=='model.safetensors' or p.suffix=='.json') and p.name!='base-manifest.json']
 for p in sorted(selected):
  relative=str(p.relative_to(path));up=siblings.get(relative)
  if up is None:raise ValueError('UNLISTED_UPSTREAM_MODEL_ASSET:'+relative)
  if p.stat().st_size!=up.size:raise ValueError('SIZE:'+relative)
  actual=sha(p)
  if up.lfs:
   if actual!=up.lfs.sha256:raise ValueError('LFS_HASH:'+relative)
  else:
   raw=p.read_bytes();git=hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
   if git!=up.blob_id:raise ValueError('GIT_BLOB:'+relative)
  local_files[relative]={'size':p.stat().st_size,'sha256':actual,'upstream_blob':up.blob_id}
 if not {'model.safetensors','rl_agent_config.json','encoder/config.json','tokenizer/tokenizer.json'}<=set(local_files):raise ValueError('INCOMPLETE_FULL_CHECKPOINT')
 return {'repository':repository,'revision':info.sha,'files':local_files,'all_local_assets_verified':True}

def main():
 if not torch.cuda.is_available() or not torch.version.hip:raise ValueError('REAL_POCKET4_ROCM_REQUIRED')
 arch=str(torch.cuda.get_device_properties(0).gcnArchName)
 if not arch.startswith('gfx1150'):raise ValueError('EXPECTED_POCKET4_890M')
 torch.set_num_threads(2)
 cfg=json.loads((H/'.config/heptatrader-temporal-fusion/2db40232fa5c05c03d1c64e7ab06942520c3a7cd2a58f82c18d46861c848aba6.json').read_bytes())
 folder=Path(cfg['shadow_root']);p=sorted(folder.glob('prediction-*.json'))[0];source=json.loads(p.read_bytes())
 if digest(source['input'])!=source['input_id']:raise ValueError('SOURCE_INPUT_HASH')
 text=json.dumps(source['input'],sort_keys=True,separators=(',',':'),allow_nan=False)
 manifest={'schema':'heptatrader.frozen-encoder-same-input-probe.v1','registered_at_utc':datetime.now(timezone.utc).isoformat(),
  'input_id':source['input_id'],'input_file_sha256':sha(p),'same_utf8_prompt_sha256':hashlib.sha256(text.encode()).hexdigest(),
  'source_is_preexisting_real_WATCH_input':True,'not_prospective_efficacy':True,'input_limit_tokens':2048,
  'arm_order':['Laya-mmBERT','Laya-English'],'primary_measurement':'STRICT_LOAD_FINITE_FROZEN_ENCODER_REPRESENTATION',
  'training_performed':False,'orders_sent':0,'services_changed':False}
 save('encoder-probe-registration.json',manifest)
 arms=[('Laya-mmBERT','convaiinnovations/laya-multilingual','e4e9ddf21a7b1903b7acffd8814ad4307bf63a67',H/'.local/share/heptatrader/laya-runtime/base/e4e9ddf21a7b1903b7acffd8814ad4307bf63a67'),
       ('Laya-English','convaiinnovations/laya','fe2b7719c095b82cdb2bfeedefc1fee30e506c21',H/'.local/share/heptatrader/laya-challengers/english-fe2b7719')]
 results=[]
 for name,repo,rev,base in arms:
  verified=verify(repo,rev,base);save(name+'-verified.json',verified)
  configuration=json.loads((base/'rl_agent_config.json').read_bytes())
  start=time.monotonic();model=build_model(configuration,encoder_dir=str(base/'encoder'),pretrained=False)
  state=load_file(str(base/'model.safetensors'),device='cpu');status=model.load_state_dict(state,strict=True);del state
  if status.missing_keys or status.unexpected_keys:raise ValueError('STRICT_LOAD_MISMATCH')
  model.requires_grad_(False).eval();model=model.float().to('cuda');tokenizer=AutoTokenizer.from_pretrained(base/'tokenizer',local_files_only=True,trust_remote_code=False)
  batch=tokenizer([text],return_tensors='pt',padding=True,truncation=False)
  tokens=int(batch['attention_mask'].sum())
  if tokens>2048:raise ValueError('UNPAIRED_INPUT_OVER_BUDGET_NO_TRUNCATION:'+name)
  inputs={k:v.to('cuda') for k,v in batch.items() if k in ('input_ids','attention_mask')}
  torch.cuda.reset_peak_memory_stats();torch.cuda.synchronize();inference_start=time.monotonic()
  with torch.inference_mode():
   hidden=model.encoder(**inputs).last_hidden_state
   mask=inputs['attention_mask'].unsqueeze(-1).to(hidden.dtype);pooled=(hidden*mask).sum(1)/mask.sum(1)
   pooled=torch.nn.functional.normalize(pooled.float(),dim=-1)
  torch.cuda.synchronize();elapsed=time.monotonic()-inference_start
  if not torch.isfinite(pooled).all() or any(p.requires_grad or p.grad is not None for p in model.parameters()):raise ValueError('FROZEN_REPRESENTATION_FAILED')
  output={'arm':name,'upstream_revision':rev,'base_asset_digest':digest(verified),'total_parameters':sum(p.numel() for p in model.parameters()),
    'hidden_size':pooled.shape[-1],'tokens':tokens,'prompt_sha256':manifest['same_utf8_prompt_sha256'],
    'strict_full_checkpoint_loaded':True,'missing_keys':[],'unexpected_keys':[],
    'all_original_parameters_frozen':True,'float32_encoder_output_sha256':hashlib.sha256(pooled.detach().cpu().numpy().tobytes()).hexdigest(),
    'load_and_forward_seconds':time.monotonic()-start,'encoder_seconds':elapsed,'max_gpu_allocated_bytes':torch.cuda.max_memory_allocated(),
    'gpu_arch':arch,'torch_version':torch.__version__,'hip_version':torch.version.hip,
    'score_or_pnl_advantage_measured':False,'full_backbone_no_random_replacement':True}
  save(name+'-GPU-probe.json',output);print(json.dumps(output),flush=True);results.append(output)
  del model,pooled,hidden,mask,inputs,batch;gc.collect();torch.cuda.empty_cache()
 save('encoder-paired-probe.json',{'registration':manifest,'arms':results,'engineering_pair_pass':len(results)==2,
      'future_PnL_AB_complete':False,'representation_shape_is_not_performance':True,'main_model_replaced':False})
if __name__=='__main__':main()
