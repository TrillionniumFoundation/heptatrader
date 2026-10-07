"""Bounded real Laya scorer-adapter job. No trading or automatic model selection."""
from __future__ import annotations
import argparse,copy,json,os,subprocess,time
from pathlib import Path
import torch
from torch.nn import functional as F
from .dataset import rows_for_training,verify_source_prefixes,encode_rows
from .integrity import ContractError,canonical,digest,read_json,sha_file
from .model import PROFILE,TradingDecisionCell,load_verified_laya,require_pocket4_gpu,tensor_hash
from .native import NativePorts
OBJECTIVE={'schema':'heptatrader.laya-objective.v1','primary':'OBSERVED_FUTURE_MID_INTERVAL_CROSS_ENTROPY','auxiliary_teacher_weight':.1,'ndu_observation':'NEGATIVE_OBSERVED_BRIER','pnl_weight':0,'optimizer':'AdamW','learning_rate':.0001,'weight_decay':0.,'gradient_norm_cap':1.,'max_steps':16,'max_rows':64,'minimum_rows':16,'automatic_selection':False}
def verify_old_learner_inactive():
 p=subprocess.run(['systemctl','--user','show','heptatrader-neuron-learning.service','-p','ActiveState','-p','MainPID','-p','UnitFileState'],capture_output=True,text=True,timeout=5)
 if p.returncode:raise ContractError('CANNOT_VERIFY_MLP_FREEZE')
 v=dict(x.split('=',1) for x in p.stdout.splitlines() if '=' in x)
 if v.get('ActiveState')!='inactive' or v.get('MainPID')!='0' or v.get('UnitFileState') not in {'disabled','masked'}:raise ContractError('MLP_NOT_FROZEN_NO_PARALLEL_RETRAINING')
def verify_baseline(root:Path):
 manifest=read_json(root/'freeze-manifest.json',16*1024**2)
 if manifest.get('name')!='heptatrader-bootstrap-mlp-v1' or manifest.get('learner_disabled') is not True or not manifest.get('inventory_sha256'):raise ContractError('FROZEN_BOOTSTRAP_MANIFEST_REQUIRED')
 from .integrity import safe_child
 for name,expected in manifest['inventory_sha256'].items():
  if sha_file(safe_child(root,name))!=expected:raise ContractError('FROZEN_BASELINE_CHANGED:'+name)
 model=read_json(root/'model.json')
 if model['model_id']!=manifest['selected_model_id']:raise ContractError('BASELINE_SELECTION_MISMATCH')
 return digest(manifest)
def supervised_loss(logits,labels,teachers=None,teacher_mask=None):
 if logits.ndim!=2 or logits.shape[1]!=7 or labels.shape!=logits.shape[:1]:raise ContractError('TYPED_LOSS_SHAPE')
 if not torch.isfinite(logits).all() or not ((labels>=0)&(labels<7)).all():raise ContractError('LOSS_INPUT_INVALID')
 primary=F.cross_entropy(logits,labels);auxiliary=logits.sum()*0
 if teachers is not None:
  if teachers.shape!=logits.shape or teacher_mask is None or teacher_mask.shape!=labels.shape:raise ContractError('TEACHER_ALIGNMENT')
  active=teachers[teacher_mask]
  if active.numel():
   if not torch.isfinite(active).all() or (active<0).any() or not torch.allclose(active.sum(-1),torch.ones_like(active[:,0]),atol=1e-6):raise ContractError('TEACHER_PROBABILITIES_INVALID')
   auxiliary=-(active*F.log_softmax(logits[teacher_mask],-1)).sum(-1).mean()
 return primary+.1*auxiliary
def bounded_step(cell,optimizer,batch,labels,teachers=None,teacher_mask=None):
 before=cell.trainable_state();optimizer_before=copy.deepcopy(optimizer.state_dict());optimizer.zero_grad(set_to_none=True)
 logits,_=cell(**batch);loss=supervised_loss(logits,labels,teachers,teacher_mask);loss.backward()
 for name,p in cell.named_parameters():
  if not p.requires_grad and p.grad is not None:raise ContractError('FROZEN_TENSOR_RECEIVED_GRADIENT:'+name)
 parameters=[p for p in cell.parameters() if p.requires_grad]
 if any(p.grad is not None and not torch.isfinite(p.grad).all() for p in parameters):raise ContractError('NONFINITE_ADAPTER_GRADIENT')
 torch.nn.utils.clip_grad_norm_(parameters,1.0,error_if_nonfinite=True)
 gd=digest({n:tensor_hash(p.grad) for n,p in cell.named_parameters() if p.grad is not None});optimizer.step();report=cell.norm_report()
 if not report['within_profile']:
  cell.restore_trainable(before);optimizer.load_state_dict(optimizer_before)
  return {'applied':False,'reason':'NATIVE_PROFILE_PRECHECK_REJECTED','loss':float(loss.detach()),'gradient_digest':gd,'norm':report}
 return {'applied':True,'loss':float(loss.detach()),'gradient_digest':gd,'norm':report}
def write_once(path:Path,value):
 with path.open('xb') as s:s.write(canonical(value)+b'\n');s.flush();os.fsync(s.fileno())
 path.chmod(0o400)
def run_job(config):
 began=time.monotonic();torch.manual_seed(20261007);verify_old_learner_inactive();device=require_pocket4_gpu();baseline_id=verify_baseline(Path(config['baseline_directory']));window=config['window']
 if config.get('objective')!=OBJECTIVE or config.get('profile')!=PROFILE:raise ContractError('UNREVIEWED_TRAINING_PROFILE')
 output=Path(config['output_directory'])
 if output.exists():raise ContractError('CANDIDATE_OUTPUT_MUST_BE_NEW')
 dataset_path=Path(config['dataset']);dataset=read_json(dataset_path,64*1024**2)
 if sha_file(dataset_path)!=config['dataset_sha256']:raise ContractError('FROZEN_DATASET_CHANGED')
 verify_source_prefixes(dataset);rows,exclusions=rows_for_training(dataset,window)
 if len(rows)<OBJECTIVE['minimum_rows']:raise ContractError('INSUFFICIENT_MATURE_LABELS_NO_UPDATE')
 rows=rows[-OBJECTIVE['max_rows']:];directory=Path(config['base_directory']);manifest=read_json(directory/'base-manifest.json',16*1024**2)
 print('LOADING_FULL_PRETRAINED_LAYA',flush=True);raw,tokenizer,base_id=load_verified_laya(directory,manifest)
 probe={k:v.to(device) for k,v in encode_rows(tokenizer,rows[:1]).items()}
 with torch.no_grad():base_logits=raw(**probe)[0].clone()
 total_parameters=sum(p.numel() for p in raw.parameters());cell=TradingDecisionCell(raw,base_id)
 with torch.no_grad():zero_logits=cell(**probe)[0]
 torch.testing.assert_close(zero_logits,base_logits,atol=1e-7,rtol=1e-6)
 parent_id=digest({'base':base_id,'profile':PROFILE,'adapter':'ZERO_DELTA'});initial=cell.trainable_state();native=NativePorts(Path(config['native_binary']),config['native_binary_sha256']);objective_id=digest(OBJECTIVE);dataset_id=digest(dataset)
 steps=[];cell.train(True);optimizer=torch.optim.AdamW([p for p in cell.parameters() if p.requires_grad],lr=OBJECTIVE['learning_rate'],weight_decay=0.)
 for offset in range(min(OBJECTIVE['max_steps'],len(rows))):
  verify_old_learner_inactive();chosen=rows[offset:offset+2];batch={k:v.to(device) for k,v in encode_rows(tokenizer,chosen).items()};labels=torch.tensor([r['target_class'] for r in chosen],device=device);tm=torch.tensor([r['teacher_probs'] is not None for r in chosen],device=device);teachers=torch.tensor([r['teacher_probs'] or [0.]*7 for r in chosen],device=device)
  step=bounded_step(cell,optimizer,batch,labels,teachers,tm);steps.append(step);print(json.dumps({'step':offset+1,'applied':step['applied'],'loss':step['loss']}),flush=True)
 cell.eval();cell.assert_frozen();losses=[]
 with torch.no_grad():
  for row in rows:
   batch={k:v.to(device) for k,v in encode_rows(tokenizer,[row]).items()};probs=cell(**batch)[0].softmax(-1)[0];truth=F.one_hot(torch.tensor(row['target_class'],device=device),num_classes=7);losses.append(float((probs-truth).square().sum()))
 utility=native.predictive_utility(losses,rows,dataset_id,objective_id)
 measurement={'training_diagnostic_only':True,'steps':steps,'brier_on_training_rows':sum(losses)/len(losses),'selected_model_unchanged':True,'scored_rows':len(rows),'optimizer_rows_unique':min(len(rows),len(steps)+1)}
 proposal=native.proposal(cell=cell,selected_id=parent_id,dataset_id=dataset_id,utility_id=utility['receipt_digest'],evaluation_id=digest(measurement),gradient_receipt=digest([s['gradient_digest'] for s in steps]),generation=1)
 output.mkdir(mode=0o700,parents=True)
 from safetensors.torch import save_file
 tensors={n:v.detach().cpu().contiguous() for n,v in cell.trainable_state().items()};save_file(tensors,str(output/'adapter.safetensors'));(output/'adapter.safetensors').chmod(0o400)
 record={'schema':'heptatrader.laya-cell-candidate.v1','created_at_ms':int(time.time()*1000),'base_id':base_id,'base_revision':manifest['revision'],'parent_id':parent_id,'frozen_bootstrap_id':baseline_id,'dataset_id':dataset_id,'window':window,'profile':PROFILE,'objective':OBJECTIVE,'tensor_mask':sorted(tensors),'trainable_parameters':sum(t.numel() for t in tensors.values()),'total_pretrained_parameters':total_parameters,'changed_parameters':sum(int((t.cpu()!=initial[n].cpu()).sum()) for n,t in tensors.items()),'adapter_sha256':sha_file(output/'adapter.safetensors'),'zero_delta_equivalence':True,'pretrained_base_unchanged':True,'gpu_arch':str(torch.cuda.get_device_properties(0).gcnArchName),'rocm_version':torch.version.hip,'torch_version':torch.__version__,'utility':utility,'plasticity':proposal,'exclusions':exclusions,'measurement':measurement,'status':'REQUIRES_INDEPENDENT_FUTURE_SHADOW','action_head_trained':False,'broker_orders_sent':0,'automatic_cutover':False,'canonical_agentd_owner_activation_claimed':False,'wall_seconds':time.monotonic()-began,'max_gpu_allocated_bytes':torch.cuda.max_memory_allocated()}
 record['native_binary_sha256']=config['native_binary_sha256']
 record['job_config_sha256']=digest(config)
 record['source_sha256']={p.name:sha_file(p) for p in sorted(Path(__file__).parent.glob('*.py'))}
 record['effective_changed_coordinates']=sum(len(x['deltas']) for x in cell.native_parameter_rows())
 record['trainable_parameter_names']=sorted(tensors)
 record['precision_policy']=PROFILE['precision_policy']
 record['future_window_not_yet_observed_at_completion']=record['created_at_ms']<window['validation_start_ms']
 record['job_window_eligible']=record['future_window_not_yet_observed_at_completion']
 record['independent_validation_plan_required']=True
 record['candidate_id']=digest(record);write_once(output/'candidate.json',record);return record
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',required=True,type=Path);args=p.parse_args();os.umask(0o077);torch.set_num_threads(2)
 print(json.dumps(run_job(read_json(args.config)),indent=2))
if __name__=='__main__':main()
