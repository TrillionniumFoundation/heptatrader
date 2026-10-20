"""One isolated resident Laya update. Generation, parent and dataset are immutable.

The scheduler repeats jobs; this child has no ability to promote models or send
orders. Financial rewards are not fabricated from price-label correctness.
"""
from __future__ import annotations
import argparse,copy,json,os,time
from pathlib import Path
import torch
from torch.nn import functional as F
from .integrity import ContractError,read_json,digest,sha_file,canonical
from .integrity import validate_candidate_artifact as validate_parent
from .model import load_verified_laya,require_pocket4_gpu,tensor_hash
from .resident_model import ResidentDecisionCell,PROFILE,answer_targets,action_loss
from .dataset import rows_for_training,verify_source_prefixes,encode_rows
from .native import NativePorts
from .train import supervised_loss,verify_old_learner_inactive,verify_baseline
from .resident_row_accounting import SCHEMA as ROW_ACCOUNTING_SCHEMA,row_id,batches,receipt,checked_ids
from . import resident_training_v2 as training_v2
from . import resident_training_v3 as training_v3
from . import resident_training_v4 as training_v4
from . import resident_price_baselines as price_refs

OBJECTIVE={'schema':'heptatrader.laya-resident-objective.v1','primary':'OBSERVED_FUTURE_MID_INTERVAL_CROSS_ENTROPY',
 'auxiliary_teacher_weight':.1,'action_loss_weight':.01,
 'action_target':'FROZEN_PARENT_ANSWER_CORRECTNESS_ACT_VS_ESCALATE',
 'financial_reward_policy':'EXACT_BEHAVIOR_BOUND_ROUNDTRIP_ONLY_NO_OFFPOLICY_POLICY_GRADIENT',
 'optimizer':'AdamW','optimizer_reset_each_job':True,'learning_rate':.0001,'weight_decay':0.,
 'gradient_norm_cap':1.,'max_steps':16,'max_rows':64,'minimum_new_rows':16,
 'automatic_execution_selection':False}

class ResidentNative(NativePorts):
 def propose(self,cell,parent,dataset,utility,measurement,gradient,generation):
  out=self.call({'op':'laya_resident_proposal','base_id':cell.base_id,'selected_artifact_digest':parent,
   'generation':generation,'dataset_digest':dataset,'update_rule_digest':digest({'profile':PROFILE,'objective':getattr(self,'objective',OBJECTIVE)}),
   'modulator_digest':utility,'eligibility_digest':gradient,'evaluation_digest':digest(measurement),
   'layers':cell.native_parameter_rows()})
  if out.get('status')!='RequiresIndependentAcceptance' or out.get('selection_performed') is not False:
   raise ContractError('NATIVE_CANNOT_SELF_PROMOTE')
  return out

def immutable(path,value):
 with path.open('xb') as f:f.write(canonical(value)+b'\n');f.flush();os.fsync(f.fileno())
 path.chmod(0o400)

def run_job(c,*,old_learner_verified=False):
 started=time.monotonic();torch.manual_seed(20261007)
 if not old_learner_verified:verify_old_learner_inactive()
 if c.get('objective') not in (OBJECTIVE,training_v2.OBJECTIVE,training_v3.OBJECTIVE,training_v4.OBJECTIVE) or c.get('profile')!=PROFILE:raise ContractError('RESIDENT_PROFILE_CHANGED')
 episode=training_v4.enabled(c['objective'])
 training_policy=training_v4 if episode else training_v3 if c['objective']==training_v3.OBJECTIVE else training_v2
 v2=c['objective']!=OBJECTIVE
 base=Path(c['base_directory']);out=Path(c['output_directory'])
 if out.exists():raise ContractError('NEW_CANDIDATE_OUTPUT_REQUIRED')
 baseline_id=verify_baseline(Path(c['baseline_directory']))
 parent_dir=Path(c['parent_directory']);parent=validate_parent(parent_dir)
 if parent['candidate_id']!=c['parent_id']:raise ContractError('PARENT_SUBSTITUTED')
 parent_manifest_sha256=sha_file(parent_dir/'candidate.json')
 if c.get('parent_manifest_sha256',parent_manifest_sha256)!=parent_manifest_sha256:
  raise ContractError('PARENT_MANIFEST_CHANGED')
 dataset_path=Path(c['dataset'])
 if sha_file(dataset_path)!=c['dataset_sha256']:raise ContractError('DATASET_CHANGED')
 dataset=read_json(dataset_path,64*1024**2);verify_source_prefixes(dataset)
 rows,exclusions=rows_for_training(dataset,c['window'])
 if v2:rows=training_policy.timed_rows(rows,dataset)
 if training_policy in (training_v3,training_v4):rows=training_v3.primary_rows(rows)
 eligible_rows=list(rows)
 expected_accounting=training_v4.ROW_ACCOUNTING_SCHEMA if episode else ROW_ACCOUNTING_SCHEMA
 if c.get('training_row_accounting_schema')!=expected_accounting:raise ContractError('ROW_ACCOUNTING_SCHEMA_REQUIRED')
 if episode:training_v4.check_job(c)
 allowed=set(checked_ids(c['training_row_ids'],16));rows=[r for r in rows if row_id(r) in allowed]
 if len(rows)!=len(allowed) or {row_id(r) for r in rows}!=allowed:raise ContractError('TRAINING_ROW_SET_CHANGED')
 training_batches=batches(rows)
 frozen_price_baselines=price_refs.freeze(rows,c['window']['training_end_ms']) if v2 else None
 device=require_pocket4_gpu();torch.cuda.reset_peak_memory_stats()
 raw,tok,base_id=load_verified_laya(base,read_json(base/'base-manifest.json'))
 if base_id!=parent['base_id']:raise ContractError('PARENT_BASE_MISMATCH')
 from safetensors.torch import load_file,save_file
 cell=ResidentDecisionCell(raw,base_id);cell.restore_parent(load_file(str(parent_dir/'adapter.safetensors')),allow_scorer_only=True)
 initial=cell.trainable_state();native=ResidentNative(Path(c['native_binary']),c['native_binary_sha256'])
 native.objective=c['objective']
 parent_cutoff=parent.get('trained_until_ms',parent['window']['training_end_ms'])
 # Only the new episode reuses tokenization. Values remain CPU tensors and
 # model activations are recomputed after every optimizer update.
 encoding_cache={};encoding_calls=0
 def encoded(selected):
  nonlocal encoding_calls
  if not episode:return encode_rows(tok,selected)
  key=tuple(row_id(r) for r in selected)
  if key not in encoding_cache:
   encoding_cache[key]={k:v.detach().cpu() for k,v in encode_rows(tok,selected).items()}
   encoding_calls+=1
  return encoding_cache[key]
 def measure_training_rows():
  scores=[];briers=[]
  cell.eval()
  with torch.no_grad():
   for row in rows:
    batch={k:v.to(device) for k,v in encoded([row]).items()}
    p,_=cell(**batch);truth=torch.tensor([row['target_class']],device=device)
    scores.append(float(F.cross_entropy(p,truth)))
    briers.append(float((p.softmax(-1)[0]-F.one_hot(truth[0],7)).square().sum()))
  return scores,briers
 loaded_at=time.monotonic()
 # Derive targets once from the frozen predecessor, never from the improving student.
 targets={};parent_scores=[];parent_brier=[];parent_action_logits={}
 with torch.no_grad():
  for row in rows:
   batch={k:v.to(device) for k,v in encoded([row]).items()};p,a=cell(**batch)
   key=digest([row['sample_id'],row['horizon_sec']]);truth=torch.tensor([row['target_class']],device=device)
   targets[key]=int(answer_targets(p,truth)[0]) if parent_cutoff<=row['anchor_ms'] else None
   parent_scores.append(float(F.cross_entropy(p,truth)))
   if episode:parent_brier.append(float((p.softmax(-1)[0]-F.one_hot(truth[0],7)).square().sum()))
   if v2:parent_action_logits[key]=a[0].detach().cpu()
 action_training_profile=None
 if v2:action_training_profile,training_batches=training_policy.prepare_action(rows,targets,parent_action_logits,c['window']['training_end_ms'])
 frozen_episode=(training_v4.freeze(c,training_batches,targets,action_training_profile,frozen_price_baselines) if episode else None)
 schedule=(training_v4.schedule(training_batches) if episode else [(1,i+1,selected) for i,selected in enumerate(training_batches)])
 curve_points=([training_v4.point(0,parent_scores,parent_brier,[],time.monotonic()-loaded_at)] if episode else [])
 optimizer=torch.optim.AdamW([p for p in cell.parameters() if p.requires_grad],lr=.0001,weight_decay=0.)
 steps=[];cell.train(True);seen_action=set();applied_action=0
 # Each epoch covers the same disjoint two-row batches; one optimizer survives
 # all passes. Unique examples and repeated presentations have separate receipts.
 for epoch,batch_index,selected in schedule:
  batch={k:v.to(device) for k,v in encoded(selected).items()}
  truth=torch.tensor([r['target_class'] for r in selected],device=device)
  tm=torch.tensor([r['teacher_probs'] is not None for r in selected],device=device)
  teacher=torch.tensor([r['teacher_probs'] or [0.]*7 for r in selected],device=device)
  action_values=[targets[digest([r['sample_id'],r['horizon_sec']])] for r in selected]
  am=torch.tensor([v is not None for v in action_values],device=device)
  ay=torch.tensor([v if v is not None else 0 for v in action_values],device=device)
  saved=cell.trainable_state();opt=copy.deepcopy(optimizer.state_dict());optimizer.zero_grad(set_to_none=True)
  p,a=cell(**batch);price_loss=supervised_loss(p,truth,teacher,tm)
  action_step_eligible=bool(am.any())
  if v2:
   from .resident_action_v2_research import balanced_action_loss
   action_step_eligible=bool(action_training_profile and action_training_profile['action_optimizer_admitted'] and len(set(v for v in action_values if v is not None))==2)
   act_loss=(balanced_action_loss(a[am],ay[am]==0,price_loss,action_training_profile)
             if action_training_profile is not None and am.any() else a.sum()*0)
   loss=price_loss+act_loss
  else:
   act_loss=action_loss(a[am],ay[am]) if am.any() else a.sum()*0
   loss=price_loss+.01*act_loss
  loss.backward()
  if v2 and not action_step_eligible:
   for name,param in cell.named_parameters():
    if param.requires_grad and '.act_head.' in name:param.grad=None
  for name,param in cell.named_parameters():
   if not param.requires_grad and param.grad is not None:raise ContractError('FROZEN_GRADIENT')
  gradient_norm_before_clip=torch.nn.utils.clip_grad_norm_([p for p in cell.parameters() if p.requires_grad],1.,error_if_nonfinite=True)
  gradient=digest({n:tensor_hash(p.grad) for n,p in cell.named_parameters() if p.grad is not None})
  optimizer.step();norm=cell.norm_report();applied=norm['within_profile']
  if not applied:cell.restore_trainable(saved);optimizer.load_state_dict(opt)
  elif action_step_eligible:applied_action+=1
  seen_action.update(digest([r['sample_id'],r['horizon_sec']]) for r,v in zip(selected,action_values) if v is not None)
  step={'applied':applied,'training_row_ids':[row_id(r) for r in selected],
   'training_examples':len(selected),'teacher_rows':sum(r['teacher_probs'] is not None for r in selected),
   'action_optimizer_update':bool(applied and action_step_eligible),
   'gradient_norm_before_clip':float(gradient_norm_before_clip.detach()),
   'loss':float(loss.detach()),'price_loss':float(price_loss.detach()),
   'action_loss':float(act_loss.detach()),'action_labels':int(am.sum()),'gradient_digest':gradient,'norm':norm}
  if episode:step.update(epoch=epoch,batch_in_epoch=batch_index)
  steps.append(step);print(json.dumps({'step':len(steps),**{k:step[k] for k in ('applied','training_examples','teacher_rows','gradient_norm_before_clip','price_loss','action_loss','action_labels','action_optimizer_update')}}),flush=True)
  if episode and batch_index==len(training_batches) and epoch in training_v4.EPISODE['checkpoint_epochs']:
   after_scores,brier=measure_training_rows()
   curve_points.append(training_v4.point(epoch,after_scores,brier,steps,time.monotonic()-loaded_at))
   print(json.dumps({'training_curve_checkpoint':curve_points[-1],'training_only':True}),flush=True)
   cell.train(True)
 cell.eval();cell.assert_frozen()
 # The final fixed-budget checkpoint is already the final measurement.
 if not episode:after_scores,brier=measure_training_rows()
 utility=native.predictive_utility(brier,rows,digest(dataset),digest(c['objective']))
 measurement={'training_only':True,'rows':len(rows),'unique_action_labels':len(seen_action),
  'parent_ce':sum(parent_scores)/len(rows),'candidate_ce':sum(after_scores)/len(rows),
  'candidate_brier':sum(brier)/len(rows),'action_target_counts':{str(k):list(targets.values()).count(k) for k in (0,1,None)}}
 proposal=native.propose(cell,parent['candidate_id'],digest(dataset),utility['receipt_digest'],measurement,
    digest([s['gradient_digest'] for s in steps]),int(c['generation']))
 out.mkdir(mode=0o700,parents=True)
 tensors={n:t.cpu().contiguous() for n,t in cell.trainable_state().items()}
 save_file(tensors,str(out/'adapter.safetensors'));(out/'adapter.safetensors').chmod(0o400)
 changes={n:int((v.cpu()!=initial[n].cpu()).sum()) for n,v in tensors.items()}
 record={'schema':'heptatrader.laya-resident-candidate.v1','created_at_ms':int(time.time()*1000),
  'base_id':base_id,'base_revision':parent['base_revision'],'parent_id':parent['candidate_id'],'training_lineage':{'mode':'INDEPENDENT_CANDIDATES_FROM_CONFIGURED_PARENT','parent_directory':str(parent_dir),'parent_candidate_id':parent['candidate_id'],'parent_manifest_sha256':parent_manifest_sha256,'optimizer_reset_each_job':True,'optimizer_state_restored':False},
  'generation':c['generation']+1,'frozen_bootstrap_id':baseline_id,'dataset_id':digest(dataset),
  'dataset_file_sha256':c['dataset_sha256'],'data_fingerprint':c['data_fingerprint'],
  'trained_until_ms':c['window']['training_end_ms'],'window':c['window'],'profile':PROFILE,'objective':c['objective'],
  'tensor_mask':sorted(tensors),'adapter_sha256':sha_file(out/'adapter.safetensors'),
  'trainable_parameters':sum(t.numel() for t in tensors.values()),'changed_parameters':sum(changes.values()),
  'changed_by_tensor':changes,'action_head_trained':any('.act_head.' in n and v for n,v in changes.items()),
  'action_update_steps':applied_action,'effective_changed_coordinates':sum(len(x['deltas']) for x in cell.native_parameter_rows()),
  'pretrained_base_unchanged':True,'gpu_arch':str(torch.cuda.get_device_properties(0).gcnArchName),
  'torch_version':torch.__version__,'rocm_version':torch.version.hip,'wall_seconds':time.monotonic()-started,
  'max_gpu_allocated_bytes':torch.cuda.max_memory_allocated(),'native_binary_sha256':c['native_binary_sha256'],
  'utility':utility,'plasticity':proposal,'measurement':measurement,'steps':steps,'exclusions':exclusions,
  'row_accounting':(training_v4.receipt if episode else receipt)(c['training_row_ids'],steps,[row_id(r) for r in rows]),
  'status':'REQUIRES_INDEPENDENT_FUTURE_EVALUATION','pnl_reward_used':False,
  'pnl_note':'REMOTE_BEHAVIOR_REWARDS_CANNOT_BE_LAYA_POLICY_GRADIENT',
  'automatic_cutover':False,'broker_orders_sent':0,'canonical_owner_activation_claimed':False}
 if v2:
  record.update(selection_policy=training_policy.SELECTION,price_baselines=frozen_price_baselines,
   row_statistics=training_v2.coverage(rows,c['window']['training_end_ms']),
   eligible_pool_statistics=training_v2.coverage(eligible_rows,c['window']['training_end_ms']),
   action_training_profile=action_training_profile,
   action_output_profile=training_v2.ACTION_OUTPUT,
   action_optimizer_admitted=bool(action_training_profile and action_training_profile['action_optimizer_admitted']),
   action_unsupported_reason=(None if action_training_profile and action_training_profile['action_optimizer_admitted'] else 'NO_BALANCED_CAUSAL_PARENT_CORRECTNESS_TRAINING'))
 if 'selection_context' in c:record['selection_context']=copy.deepcopy(c['selection_context'])
 record['training_diagnostics']=price_refs.candidate_training_diagnostics(record)
 if episode:
  record['training_lineage'].update(mode='BOUNDED_CONTINUOUS_EPISODE_FROM_CONFIGURED_PARENT',
   optimizer_reset_between_epochs=False,completed_jobs_are_cumulative_updates=False)
  record['training_episode']=frozen_episode
  record['learning_curve']=training_v4.curve(frozen_episode,curve_points,
   record['training_diagnostics']['same_training_rows_baselines'])
  record['training_diagnostics']['continuous_episode']={
   'episode_id':frozen_episode['episode_id'],'epochs':training_v4.EPISODE['epochs'],
   'unique_training_rows':len(rows),'gradient_presentations':len(rows)*training_v4.EPISODE['epochs'],
   'optimizer_reset_between_epochs':False,'tokenization_batches':encoding_calls,
   'load_and_validation_seconds':loaded_at-started,
   'training_and_measurement_seconds':time.monotonic()-loaded_at,
   'checkpoint_epochs':training_v4.EPISODE['checkpoint_epochs'],
   'future_value_proven':False}
  training_v4.verify_candidate(c,record)
 record['candidate_id']=digest(record);immutable(out/'candidate.json',record)
 parent_fd=os.open(out,os.O_RDONLY|os.O_DIRECTORY)
 try:os.fsync(parent_fd)
 finally:os.close(parent_fd)
 return record

def main():
 p=argparse.ArgumentParser();p.add_argument('--config',type=Path,required=True);a=p.parse_args();os.umask(0o077);torch.set_num_threads(2)
 print(json.dumps(run_job(read_json(a.config)),indent=2))
if __name__=='__main__':main()
