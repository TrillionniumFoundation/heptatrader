"""Independent Numeric/Laya experts then a chronologically separate frozen gate."""
from __future__ import annotations
import argparse,hashlib,json,os,time
from pathlib import Path
import torch
from torch import nn
from torch.nn import functional as F
from safetensors.torch import load_file,save_file
from laya_cell.model import load_verified_laya,require_pocket4_gpu
from laya_cell.temporal_fusion_model import TemporalLayaFusion,padded_sequences
from laya_cell.integrity import digest,read_json,sha_file,ContractError,canonical
from .common import PROFILE,require,immutable_json,utc_ms
from .data import build,model_state_text
from .model import PairModel

def features(raw,tokenizer,entries,batch_size=8):
 rows=[];tokens=[]
 raw.eval()
 for offset in range(0,len(entries),batch_size):
  texts=[model_state_text(x['row']['state']) for x in entries[offset:offset+batch_size]]
  batch=tokenizer(texts,padding=True,truncation=False,return_tensors='pt')
  counts=batch['attention_mask'].sum(-1)
  if int(counts.max())>PROFILE['encoder_max_tokens']:raise ContractError('NO_SILENT_ENCODER_TRUNCATION')
  tokens+=counts.tolist()
  inp={k:v.to('cuda') for k,v in batch.items() if k in ('input_ids','attention_mask')}
  with torch.inference_mode():
   hidden=raw.encoder(**inp).last_hidden_state
   mask=inp['attention_mask'].unsqueeze(-1).to(hidden.dtype)
   pooled=F.normalize(((hidden*mask).sum(1)/mask.sum(1)).float(),dim=-1)
  if pooled.shape[1]!=768:raise ContractError('FROZEN_ENCODER_SHAPE_CHANGED')
  rows.append(pooled.detach().cpu())
 return torch.cat(rows).to('cuda'),tokens

def batch_inputs(rows,device):
 seq,length=padded_sequences([x['sample'] for x in rows],device)
 labels=torch.tensor([x['row']['target_class'] for x in rows],device=device,dtype=torch.long)
 return seq,length,labels

def fit(config):
 torch.set_num_threads(2);torch.manual_seed(PROFILE['seed']);device=require_pocket4_gpu()
 start=time.monotonic();cutoff=utc_ms();dataset=build(config,cutoff)
 base=Path(config['base_directory']);raw,tok,base_id=load_verified_laya(base,read_json(base/'base-manifest.json'))
 raw.requires_grad_(False).eval();weights_before=sha_file(base/'model.safetensors')
 experts=dataset['experts'];gate=dataset['gate'];entries=experts+gate
 feats,tokens=features(raw,tok,entries)
 del raw;torch.cuda.empty_cache()
 n=len(experts)
 x,length,labels=batch_inputs(entries,device);model=PairModel().to(device)
 legacy_dir=Path(config['fusion_candidate_directory'])
 old=read_json(legacy_dir/'candidate.json')
 require(old['period_sha256']==config['period_sha256'] and sha_file(legacy_dir/'fusion.safetensors')==old['fusion_sha256'],'LEGACY_NUMERIC_CHANGED')
 legacy=TemporalLayaFusion().to(device)
 legacy.load_state_dict(load_file(str(legacy_dir/'fusion.safetensors')),strict=True);legacy.eval().requires_grad_(False)
 with torch.no_grad():
  legacy_n=legacy.numeric(x,length)[1].softmax(-1).detach()
 criterion=nn.CrossEntropyLoss()
 num_optim=torch.optim.AdamW(model.numeric.parameters(),lr=PROFILE['learning_rate'],weight_decay=PROFILE['weight_decay'])
 sem_optim=torch.optim.AdamW(model.semantic.parameters(),lr=PROFILE['learning_rate'],weight_decay=PROFILE['weight_decay'])
 details=[]
 for epoch in range(max(PROFILE['num_epochs'],PROFILE['semantic_epochs'])):
  generator=torch.Generator(device='cpu').manual_seed(PROFILE['seed']+epoch)
  order=torch.randperm(n,generator=generator)
  total_num=total_sem=0.
  for pos in range(0,n,PROFILE['batch_size']):
   ix=order[pos:pos+PROFILE['batch_size']].to(device)
   if epoch<PROFILE['num_epochs']:
    num_optim.zero_grad(set_to_none=True)
    num_logits=model.numeric(x[ix],length[ix])[1]
    loss=criterion(num_logits,labels[ix]);loss.backward()
    torch.nn.utils.clip_grad_norm_(model.numeric.parameters(),1.,error_if_nonfinite=True);num_optim.step()
    total_num+=float(loss.detach())*len(ix)
   if epoch<PROFILE['semantic_epochs']:
    sem_optim.zero_grad(set_to_none=True)
    loss_sem=criterion(model.semantic(feats[ix]),labels[ix]);loss_sem.backward()
    torch.nn.utils.clip_grad_norm_(model.semantic.parameters(),1.,error_if_nonfinite=True);sem_optim.step()
    total_sem+=float(loss_sem.detach())*len(ix)
  if epoch in (0,7,15,27,35):details.append({'epoch':epoch+1,'numeric_loss':total_num/n if epoch<PROFILE['num_epochs'] else None,'semantic_loss':total_sem/n if epoch<PROFILE['semantic_epochs'] else None})
 model.freeze_experts()
 frozen={key:v.detach().cpu().clone() for key,v in model.state_dict().items() if key.startswith(('numeric.','semantic.'))}
 gate_optim=torch.optim.AdamW(model.gate.parameters(),lr=PROFILE['learning_rate'],weight_decay=PROFILE['weight_decay'])
 for _ in range(PROFILE['gate_epochs']):
  gate_optim.zero_grad(set_to_none=True)
  result=model.predict(x[n:],length[n:],feats[n:],legacy_n[n:])
  loss=F.nll_loss(result['gate'].clamp_min(1e-8).log(),labels[n:]);loss.backward()
  torch.nn.utils.clip_grad_norm_(model.gate.parameters(),1.,error_if_nonfinite=True);gate_optim.step()
 for key,value in frozen.items():
  if not torch.equal(model.state_dict()[key].detach().cpu(),value):raise ContractError('GATE_CHANGED_FROZEN_EXPERT')
 model.assert_parameters()
 with torch.no_grad():
  gate_pred=model.predict(x[n:],length[n:],feats[n:],legacy_n[n:])
  one=F.one_hot(labels[n:],7).float()
  diag={name:{'gate_train_in_sample_brier':float((gate_pred[name]-one).square().sum(-1).mean()),
   'hits':int((gate_pred[name].argmax(-1)==labels[n:]).sum())} for name in ('numeric','semantic','fixed_mix','gate','legacy_numeric')}
  diag['gate_numeric_mean']=float(gate_pred['numeric_weight'].mean())
 # NO train-time qualification of the gate via its own fitting sample.
 out=Path(config['output_directory']);out.mkdir(parents=True,mode=0o700,exist_ok=False)
 tensors={key:value.detach().cpu().contiguous() for key,value in model.state_dict().items()}
 save_file(tensors,str(out/'pair.safetensors'));(out/'pair.safetensors').chmod(0o400)
 training_ids=[x['label_identity'] for x in entries]
 dataset_digest=digest({'period_sha256':dataset['period_sha256'],'training_row_ids':training_ids,
  'prefixes':dataset['source_prefixes'],'cutoff_ms':cutoff})
 record={'schema':'heptatrader.paired-price-candidate.v1','created_at_ms':utc_ms(),'trained_until_ms':max(x['last_outcome_known_ms'] for x in gate),
  'period_sha256':config['period_sha256'],'profile':PROFILE,'base_id':base_id,
  'base_checkpoint_sha256':weights_before,'legacy_numeric_id':old['candidate_id'],'legacy_numeric_sha256':old['fusion_sha256'],
  'laya_representation':'FROZEN_FULL_PRETRAINED_MM_BERT_768_MASKED_MEAN',
  'num_expert_rows':n,'num_gate_rows':len(gate),'fit_dataset_digest':dataset_digest,
  'expert_row_ids':training_ids[:n],'gate_row_ids':training_ids[n:],'gated_label_disjoint_from_expert':True,
  'last_expert_outcome_ms':max(x['last_outcome_known_ms'] for x in experts),
  'first_gate_input_ms':gate[0]['row']['anchor_ms'],'token_min':min(tokens),'token_max':max(tokens),
  'trained_model_sha256':sha_file(out/'pair.safetensors'),
  'artifacts':{'pair.safetensors':sha_file(out/'pair.safetensors')},
  'source_sha256':{name:sha_file(Path(__file__).parent/name) for name in ('common.py','data.py','model.py','train.py')},
  'numeric_semantic_frozen_before_gate':True,'optimization_receipt':details,
  'in_sample_gate_diagnostic_not_qualification':diag,'gpu_arch':str(torch.cuda.get_device_properties(0).gcnArchName),
  'wall_seconds':time.monotonic()-start,'torch':torch.__version__,'hip':torch.version.hip,
  'base_parameters_updated':False,'broker_orders_sent':0,'effect_authority':False}
 require(record['last_expert_outcome_ms']<record['first_gate_input_ms'] and record['trained_until_ms']<=record['created_at_ms'],'FUTURE_LEAKAGE')
 record['candidate_id']=digest(record);immutable_json(out/'candidate.json',record)
 print(json.dumps({k:v for k,v in record.items() if k not in ('expert_row_ids','gate_row_ids')},indent=2),flush=True)
 return record

def main():
 p=argparse.ArgumentParser();p.add_argument('--config',type=Path,required=True);a=p.parse_args()
 os.umask(0o077);fit(read_json(a.config))
if __name__=='__main__':main()
