"""Real ROCm training of a separate monetary readout on frozen pretrained Laya.

No scorer/action adapter update, no policy gradient, no production selection.
A tiny dataset is an executed learning-path check, not economic efficacy proof.
"""
from __future__ import annotations
import argparse,json,math,os,time
from pathlib import Path
import torch
from torch import nn
from torch.nn import functional as F
from .integrity import ContractError,read_json,digest,sha_file,canonical
from .model import load_verified_laya,require_pocket4_gpu,tensor_hash
from .native import NativePorts
from .financial_value_data import PROFILE,to_row,snapshot

class FinancialHead(nn.Module):
 def __init__(self,device):
  super().__init__();self.value=nn.Linear(768,1,device=device,dtype=torch.float32)
  with torch.no_grad():self.value.weight.zero_();self.value.bias.zero_()
 def forward(self,features):return self.value(features).squeeze(-1)

def train_head(features,targets):
 if features.ndim!=2 or features.shape[1]!=768 or targets.shape!=features.shape[:1]:raise ContractError('VALUE_TENSOR_SHAPE')
 if not torch.isfinite(features).all() or not torch.isfinite(targets).all():raise ContractError('VALUE_NONFINITE_INPUT')
 if (targets.abs()>8).any():raise ContractError('VALUE_TARGET_BOUND')
 features=features.detach();head=FinancialHead(features.device);optimizer=torch.optim.SGD(head.parameters(),lr=.05)
 before=head(features).detach();initial={k:v.detach().clone() for k,v in head.state_dict().items()};steps=[]
 for _ in range(16):
  optimizer.zero_grad(set_to_none=True);loss=F.smooth_l1_loss(head(features),targets);loss.backward()
  if any(p.grad is None or not torch.isfinite(p.grad).all() for p in head.parameters()):raise ContractError('VALUE_BAD_GRADIENT')
  grad=float(torch.nn.utils.clip_grad_norm_(head.parameters(),1.,error_if_nonfinite=True));optimizer.step()
  with torch.no_grad():
   vector=torch.cat([p.flatten() for p in head.parameters()]);norm=float(torch.linalg.vector_norm(vector))
   if norm>.1:
    for p in head.parameters():p.mul_(.1/norm)
  steps.append({'huber_scaled':float(loss.detach()),'gradient_norm':grad})
 after=head(features).detach();final_norm=float(torch.linalg.vector_norm(torch.cat([p.detach().flatten() for p in head.parameters()])))
 if final_norm>.100001:raise ContractError('VALUE_UPDATE_BOUND_EXCEEDED')
 changed=sum(int(torch.count_nonzero(v-initial[k])) for k,v in head.state_dict().items())
 return head,{'steps':steps,'before_scaled':before.cpu().tolist(),'after_scaled':after.cpu().tolist(),
  'huber_before':float(F.smooth_l1_loss(before,targets)),'huber_after':float(F.smooth_l1_loss(after,targets)),
  'changed_parameters':changed,'effective_update_l2':final_norm,'trainable_parameters':769}

def features_from_laya(raw,tokenizer,rows):
 batch=tokenizer([r['feature_text'] for r in rows],padding=True,truncation=False,return_tensors='pt')
 if batch['input_ids'].shape[1]>PROFILE['max_tokens']:raise ContractError('VALUE_PROMPT_EXCEEDS_FIXED_CONTEXT_NO_TRUNCATION')
 batch={k:v.to('cuda') for k,v in batch.items() if k in ('input_ids','attention_mask','token_type_ids')}
 raw.eval()
 with torch.no_grad():
  hidden=raw.encoder(**batch).last_hidden_state
  mask=batch['attention_mask'].unsqueeze(-1).to(hidden.dtype)
  mean=(hidden*mask).sum(1)/mask.sum(1).clamp_min(1)
  if mean.shape[1]!=768:raise ContractError('VALUE_LAYA_REPRESENTATION_CHANGED')
  features=F.normalize(mean.float(),p=2,dim=1).detach()
 return features,{'token_counts':batch['attention_mask'].sum(1).cpu().tolist(),'feature_sha256':tensor_hash(features),
                  'pooling':'MASKED_MEAN_L2_NORMALIZED','requires_grad':features.requires_grad}

def run(config):
 begin=time.monotonic();device=require_pocket4_gpu();torch.set_num_threads(2);torch.manual_seed(20261007)
 if config['financial_profile']!=PROFILE:raise ContractError('VALUE_PROFILE_CHANGED')
 data_path=Path(config['dataset_path']);data=read_json(data_path,32*1024**2)
 if sha_file(data_path)!=config['dataset_sha256'] or data['profile']!=PROFILE or not data['rows']:raise ContractError('VALUE_DATASET_CHANGED_OR_EMPTY')
 # Revalidate against CURRENT authoritative replay/attribution, not a caller's
 # profitable JSON. Preserve earlier labels only if still currently valid.
 current=snapshot(config,int(time.time()*1000));by_id={r['row_id']:r for r in current['rows']}
 rows=data['rows']
 scopes={(r['behavior_model_id'],r['reward']['input_attribution']['producer_manifest_sha256']) for r in rows}
 if len(scopes)!=1:raise ContractError('MIXED_BEHAVIOR_POLICIES_NEED_SEPARATE_VALUE_HEADS')
 expected_scope={'instrument':'EUR.USD','behavior_model_id':rows[0]['behavior_model_id'],
  'producer_manifest_sha256':rows[0]['reward']['input_attribution']['producer_manifest_sha256'],'quantity':25000,'currency':'USD'}
 if config.get('behavior_scope',expected_scope)!=expected_scope:raise ContractError('VALUE_BEHAVIOR_SCOPE_CHANGED')
 if any(r.get('row_id') not in by_id or r!=by_id[r['row_id']] for r in rows):raise ContractError('VALUE_REWARD_REVOKED_OR_CHANGED')
 for r in rows:
  if to_row(r['reward'])!=r:raise ContractError('VALUE_EXACT_ROW_CHANGED')
 out=Path(config['job_output']);out.mkdir(mode=0o700,parents=True,exist_ok=False)
 base=Path(config['base_directory']);torch.cuda.reset_peak_memory_stats()
 raw,tok,base_id=load_verified_laya(base,read_json(base/'base-manifest.json'));raw.requires_grad_(False).eval()
 before_hash={n:tensor_hash(p) for n,p in raw.named_parameters()}
 features,feature_receipt=features_from_laya(raw,tok,rows)
 target=torch.tensor([r['target_scaled'] for r in rows],device=device,dtype=torch.float32)
 head,diagnostic=train_head(features,target)
 if any(p.grad is not None or p.requires_grad for p in raw.parameters()):raise ContractError('FROZEN_LAYA_RECEIVED_GRADIENT')
 if before_hash!={n:tensor_hash(p) for n,p in raw.named_parameters()}:raise ContractError('PRETRAINED_LAYA_CHANGED')
 native=NativePorts(Path(config['native_binary']),config['native_binary_sha256'])
 utility=native.call({'op':'utility','dataset_digest':digest(data),'objective_digest':digest(PROFILE),
  'outcome_digest':digest([r['reward_id'] for r in rows]),
  'events':[{'instant':r['target_scaled']/len(rows),'discount':1.,'event_digest':r['reward_id']} for r in rows]})
 from safetensors.torch import save_file
 tensors={n:p.detach().cpu().contiguous() for n,p in head.state_dict().items()};save_file(tensors,str(out/'value-head.safetensors'))
 (out/'value-head.safetensors').chmod(0o400)
 record={'schema':'heptatrader.financial-value-candidate.v1','created_at_ms':int(time.time()*1000),'base_id':base_id,
  'base_revision':read_json(base/'base-manifest.json')['revision'],'pretrained_base_parameters':sum(p.numel() for p in raw.parameters()),
  'pretrained_laya_unchanged':True,'scorer_or_action_adapter_updated':False,'financial_profile':PROFILE,
  'new_value_head_initialized':True,'random_backbone_created':False,'trained_input_cutoff_ms':max(r['input_started_at_ms'] for r in rows),
  'labels_observed_at_ms':data['observed_at_ms'],'unique_closed_rewards':len(rows),'replayed_optimizer_steps':16,
  'reward_ids':[r['reward_id'] for r in rows],'row_ids':[r['row_id'] for r in rows],
  'target_net_usd':[r['net_usd'] for r in rows],'actual_commissions_usd':[r['commission_usd'] for r in rows],
  'diagnostic':diagnostic,'feature_receipt':feature_receipt,'native_net_reward_utility':utility,
  'head_tensor_sha256':sha_file(out/'value-head.safetensors'),'dataset_sha256':config['dataset_sha256'],
  'pnl_reward_used':True,'training_scope':'INDEPENDENT_BEHAVIOR_BOUND_VALUE_HEAD',
  'policy_gradient_used':False,'canonical_agentd_owner_accepted':False,'plasticity_qualification':'ABSOLUTE_READOUT_BOUND_ONLY_NO_NATIVE_SELECTION',
  'future_validation_observed':False,'status':'UNVALIDATED_FINANCIAL_VALUE_CANDIDATE',
  'sample_size_warning':'ENGINEERING_UPDATE_EVIDENCE_NOT_GENERALIZATION',
  'gpu_arch':str(torch.cuda.get_device_properties(0).gcnArchName),'torch_version':torch.__version__,'rocm':torch.version.hip,
  'wall_seconds':time.monotonic()-begin,'max_gpu_allocated_bytes':torch.cuda.max_memory_allocated(),
  'automatic_trading':False,'broker_orders_sent':0}
 record['behavior_scope']=config.get('behavior_scope',{'instrument':'EUR.USD','behavior_model_id':rows[0]['behavior_model_id'],
  'producer_manifest_sha256':rows[0]['reward']['input_attribution']['producer_manifest_sha256'],'quantity':25000,'currency':'USD'})
 record['source_python_sha256']={p.name:sha_file(p) for p in (Path(__file__),Path(__file__).with_name('financial_value_data.py'))}
 record['config_digest']=digest(config)
 record['candidate_id']=digest(record)
 with (out/'candidate.json').open('xb') as f:f.write(canonical(record)+b'\n');f.flush();os.fsync(f.fileno())
 (out/'candidate.json').chmod(0o400)
 print(json.dumps(record,indent=2));return record

def main():
 p=argparse.ArgumentParser();p.add_argument('--config',type=Path,required=True);a=p.parse_args();os.umask(0o077);run(read_json(a.config))
if __name__=='__main__':main()
