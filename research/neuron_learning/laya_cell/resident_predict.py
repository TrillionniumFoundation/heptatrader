"""One fixed future slot: candidate, frozen predecessor, and frozen bootstrap.

No optimizer is imported or used in prediction. A missed slot is kept as missed;
its anchor is never moved to manufacture an outcome. No additional remote spend.
"""
from __future__ import annotations
import argparse,json,os,time
from pathlib import Path
import numpy as np
import torch
from .integrity import ContractError,read_json,sha_file,digest
from .resident_store import write_once,atomic
from .resident_data import forecast_input
from .resident_source_policy import declared_profile,read_for_future_plan
from .dataset import encode_rows
from .model import load_verified_laya,worker_resource_usage
from .resident_model import ResidentDecisionCell
from .native import NativePorts
from .integrity import validate_candidate_artifact as validate_parent
from . import resident_training_v2 as training_v2
from . import resident_training_v3 as training_v3
from . import resident_training_v4 as training_v4
from . import resident_training_v5 as training_v5
from . import resident_training_v6 as training_v6
from . import resident_training_v7 as training_v7
from . import resident_training_v8 as training_v8
from . import resident_price_input as price_input
from . import resident_price_calibration as price_calibration
from . import resident_price_baselines as price_refs
from . import resident_parent_calibration as parent_calibration


def _prediction_batches(tokenizer,row,input_binding):
 """Encode each declared role; ACT always retains the raw quote input."""
 raw={k:v.to('cuda') for k,v in encode_rows(tokenizer,[row]).items()}
 if input_binding is None:return raw,raw,raw
 price_input.check_bindings(input_binding)
 if (input_binding['price_input_profile']==price_input.RAW and
     input_binding['parent_price_input_profile']==price_input.RAW):return raw,raw,raw
 compact={k:v.to('cuda') for k,v in encode_rows(tokenizer,[row],price_input_profile=price_input.COMPACT).items()}
 parent=compact if input_binding['parent_price_input_profile']==price_input.COMPACT else raw
 child=compact if input_binding['price_input_profile']==price_input.COMPACT else raw
 return raw,parent,child


def predict(config,plan_path,slot):
 plan=read_json(plan_path)
 from .resident_hip_runtime import check_plan,prove_before_publish
 check_plan(config,plan)
 from .promotion import validate_plan_rule
 from .resident_evaluate import PUBLICATION_PROFILE,PREDICTION_PREWARM_MS
 rule=validate_plan_rule(plan)
 if (plan.get('plan_id')!=digest({k:v for k,v in plan.items() if k!='plan_id'})
     or type(slot) is not int or not 0<=slot<rule['slots']):raise ContractError('PREDICT_PLAN_OR_SLOT_CHANGED')
 publication_profile=plan.get('prediction_publication_profile')
 if publication_profile not in (None,PUBLICATION_PROFILE):raise ContractError('PREDICTION_PUBLICATION_PROFILE_CHANGED')
 due_start=plan['start_ms']+slot*rule['slot_spacing_ms'];late=due_start+rule['input_grace_ms']
 # Preflight the sealed NEW source profile before heavyweight model loading:
 # a mismatched/corrupt future plan must never consume a GPU admission.
 expected_source_profile=declared_profile(config,plan)
 from safetensors.torch import load_file
 candidate_dir=Path(plan['candidate_directory']);parent_dir=Path(plan['parent_directory'])
 timing={'process_started_at_ms':int(time.time()*1000),'phase':'VALIDATE_AND_LOAD',
  'slot_start_ms':due_start,'fixed_input_deadline_ms':late,
  'prewarm_start_ms':due_start-PREDICTION_PREWARM_MS}
 stage=plan_path.parent/f'phase-{slot:03}.json'
 atomic(stage,timing)
 candidate=validate_parent(candidate_dir);parent=validate_parent(parent_dir)
 if candidate['candidate_id']!=plan['candidate_id'] or parent['candidate_id']!=candidate['parent_id']:
  raise ContractError('PREDICT_CANDIDATE_PARENT_CHANGED')
 objective=candidate.get('objective')
 if (not isinstance(objective,dict) or objective.get('schema') not in
     ('heptatrader.laya-resident-objective.v1',training_v2.SCHEMA,training_v3.SCHEMA,training_v4.SCHEMA,training_v5.SCHEMA,training_v6.SCHEMA,training_v7.SCHEMA,training_v8.SCHEMA)):
  raise ContractError('UNKNOWN_RESIDENT_OBJECTIVE')
 if training_v4.enabled(objective):training_v4.check_objective(objective)
 v8=training_v8.enabled(objective)
 v7=training_v7.enabled(objective) or v8
 v6=training_v6.enabled(objective) or v7
 v5=training_v5.enabled(objective) or v6
 if v5:(training_v8 if v8 else training_v7 if v7 else training_v6 if v6 else training_v5).check_objective(objective)
 training_policy=training_v8 if v8 else training_v7 if v7 else training_v6 if v6 else training_v5 if v5 else training_v4 if training_v4.enabled(objective) else training_v3 if training_v3.enabled(objective) else training_v2
 v2=training_policy.enabled(candidate.get('objective'))
 calibration=candidate.get('action_training_profile') if v2 else None
 price_reference_profile=price_refs.validate(candidate.get('price_baselines')) if v2 else None
 if v2 and (plan.get('price_baselines')!=price_reference_profile or price_reference_profile['training_cutoff_ms']!=candidate['trained_until_ms']):raise ContractError('PRICE_BASELINE_CANDIDATE_SCOPE')
 expected_output=training_v2.ACTION_OUTPUT if v2 else None
 if plan.get('action_output_profile')!=expected_output:raise ContractError('ACTION_OUTPUT_PROFILE_CHANGED')
 if v2 and (candidate.get('objective')!=training_policy.OBJECTIVE or candidate.get('action_output_profile')!=expected_output or plan.get('action_training_profile_id')!=(calibration['profile_id'] if calibration else None)):
  raise ContractError('ACTION_TRAINING_PROFILE_CHANGED')
 if v2 and (type(plan.get('action_optimizer_admitted')) is not bool or plan['action_optimizer_admitted']!=bool(calibration and calibration['action_optimizer_admitted'])):raise ContractError('ACTION_TRAINING_SUPPORT_CHANGED')
 if training_policy in (training_v3,training_v4,training_v5,training_v6,training_v7,training_v8):
  if plan.get('price_value_rule')!=price_refs.VALUE_RULE:raise ContractError('PRICE_VALUE_RULE_REQUIRED_FOR_V3')
  price_refs.validate_value_rule(plan)
  if calibration is not None and calibration.get('batch_balance_strategy')!=training_v3.ACTION_BALANCE:raise ContractError('ACTION_BATCH_BALANCE_PROFILE_CHANGED')
 if v5:
  price_profile=price_calibration.validate(candidate.get('price_calibration'))
  if plan.get('action_distribution_scope')!=calibration.get('action_distribution_scope'):
   raise ContractError('PREDICT_ACTION_DISTRIBUTION_SCOPE_CHANGED')
  if (price_profile['output_profile']!=training_policy.PRICE_OUTPUT or
      candidate.get('price_output_profile')!=training_policy.PRICE_OUTPUT or
      plan.get('price_output_profile')!=training_policy.PRICE_OUTPUT or
      plan.get('price_calibration')!=price_profile or
      plan.get('price_calibration_profile_id')!=price_profile['profile_id'] or
      price_profile['parent_id']!=parent['candidate_id'] or
      price_profile['adapter_sha256']!=candidate['adapter_sha256'] or
      price_profile['training_cutoff_ms']!=candidate['trained_until_ms'] or
      price_profile['training_row_ids_digest']!=price_reference_profile['training_row_ids_digest'] or
      price_profile['price_baseline_profile_id']!=price_reference_profile['profile_id']):
   raise ContractError('PREDICT_PRICE_CALIBRATION_CHANGED')
 input_binding=None
 if v7:
  input_binding=price_input.check_bindings(candidate)
  if (input_binding!=price_input.bindings(parent,objective_schema=training_policy.SCHEMA) or price_input.check_bindings(plan)!=input_binding):
   raise ContractError('V7_PREDICT_INPUT_PROFILE_CHANGED')
 parent_calibration.verify_plan(plan,candidate,parent,sha_file(parent_dir/'candidate.json'))
 if int(time.time()*1000)>late:raise ContractError('SLOT_ALREADY_MISSED')
 raw,tok,base_id=load_verified_laya(Path(config['base_directory']),read_json(Path(config['base_directory'])/'base-manifest.json'))
 if candidate['base_id']!=base_id:raise ContractError('BASE_CHANGED')
 cell=ResidentDecisionCell(raw,base_id)
 cell.restore_parent(load_file(str(parent_dir/'adapter.safetensors')),allow_scorer_only=True);parent_tensors=cell.trainable_state()
 candidate_tensors=load_file(str(candidate_dir/'adapter.safetensors'))
 cell.eval()
 ready=int(time.time()*1000)
 timing.update(model_ready_at_ms=ready,phase='WAIT_FOR_FIXED_SLOT_INPUT',
  model_preparation_ms=ready-timing['process_started_at_ms'],
  input_budget_remaining_after_model_ms=late-ready)
 atomic(stage,timing)
 last_input_error=None
 while True:
  now=int(time.time()*1000)
  if now>late:raise ContractError('INPUT_SLOT_EXPIRED:'+str(last_input_error or 'MODEL_LOAD_OR_SCHEDULING_LATE'))
  if now<due_start:time.sleep(min(1,(due_start-now)/1000));continue
  read_begin=time.monotonic()
  quotes,prefixes,source_profile=read_for_future_plan(config,plan,now)
  if source_profile!=expected_source_profile:
   raise ContractError('FUTURE_SOURCE_PROFILE_CHANGED_DURING_READ')
  now=int(time.time()*1000)
  timing['last_quote_read_ms']=round((time.monotonic()-read_begin)*1000,3)
  if now>late:raise ContractError('INPUT_SLOT_EXPIRED:DURING_QUOTE_READ')
  try:
   sample,row=forecast_input(config,quotes,now)
   if sample['anchor_ms']<due_start:raise ContractError('QUOTE_PRECEDES_SLOT')
   break
  except ContractError as exc:
   last_input_error=str(exc);time.sleep(.5)
 anchor=sample['anchor_ms'];selected_at=int(time.time()*1000)
 if selected_at>late:raise ContractError('INPUT_SLOT_EXPIRED:DURING_INPUT_SELECTION')
 timing.update(input_selected_at_ms=selected_at,anchor_ms=anchor,phase='INFERENCE',
  fixed_prediction_deadline_ms=anchor+rule['horizon_ms'],
  input_quote_age_ms=int(time.time()*1000)-anchor,contiguous_history_reset=sample.get('contiguous_history_reset_at_gap',False))
 atomic(stage,timing)
 input_record={'schema':'heptatrader.laya-committed-input.v1','plan_id':plan['plan_id'],'slot':slot,
  'candidate_id':candidate['candidate_id'],'input_id':digest(row['state']),'sample':sample,'state':row['state'],
  'source_prefixes':prefixes,'committed_at_ms':int(time.time()*1000)}
 if plan.get('future_source_read_profile') is not None:
  input_record['source_read_profile']=source_profile
 if v7:
  input_record.update(price_input.receipt(row['state'],input_binding))
  price_input.verify_committed_input(plan,input_record)
 input_path=plan_path.parent/f'input-{slot:03}.json'
 write_once(input_path,input_record)
 if candidate['created_at_ms']>=anchor or candidate['trained_until_ms']>anchor:
  raise ContractError('CANDIDATE_NOT_FROZEN_BEFORE_INPUT')
 bootstrap_root=Path(config['baseline_directory']);freeze=read_json(bootstrap_root/'freeze-manifest.json',16*1024**2)
 if sha_file(bootstrap_root/'model.json')!=freeze['inventory_sha256']['model.json']:raise ContractError('BOOTSTRAP_CHANGED')
 bootstrap=read_json(bootstrap_root/'model.json')
 if bootstrap['trained_until_ms']>anchor:raise ContractError('BOOTSTRAP_LOOKAHEAD')
 batch,parent_batch,price_batch=_prediction_batches(tok,row,input_binding)
 with torch.no_grad():
  cell.restore_trainable(parent_tensors);parent_probs=cell(**parent_batch)[0].softmax(-1)[0].cpu().tolist()
  cell.restore_trainable(candidate_tensors);p,a=cell(**price_batch)
  if v7 and price_batch is not batch:
   # The ACT adapter was calibrated/trained on original raw quote inputs.
   # A separate ordinary raw-input forward preserves that exact contract.
   a=cell(**batch)[1]
  probs=p.softmax(-1)[0].cpu().tolist();action=a.softmax(-1)[0].cpu().tolist()
  if v5:
   raw_price_probs=list(probs);probs=price_calibration.apply(price_profile,raw_price_probs)
  if v2:
   action_report=training_v2.action_inference(a,calibration)
   correct=action_report['action_correct_probs']
   value=correct[0] if correct is not None else None
   action=[value,1-value] if value is not None else None
 cell.assert_frozen()
 if v2:price_reference_forecast=price_refs.forecast(price_reference_profile,row['state'])
 native=NativePorts(Path(config['native_binary']),config['native_binary_sha256'])
 features=native.call({'op':'neuron','episodes':[sample['native_episode']]})
 x=np.asarray(sample['raw_features']+features['episodes'][0]['activation'],dtype=np.float64)
 w=np.asarray(bootstrap['weights'],dtype=np.float64);z=np.tanh(x@w[:512].reshape(32,16)+w[512:528]);point=float((z@w[528:560].reshape(16,2)+w[560:562])[0]*100)
 available=int(time.time()*1000)
 if available>=anchor+300000:raise ContractError('PREDICTION_AFTER_OUTCOME')
 result={'schema':'heptatrader.resident-forward-prediction.v1','candidate_id':candidate['candidate_id'],
  'parent_id':parent['candidate_id'],'plan_id':plan['plan_id'],'slot':slot,
  'created_at_ms':available,'anchor_ms':anchor,'trained_until_ms':candidate['trained_until_ms'],
  'input_id':digest(row['state']),'input':row['state'],'sample_id':sample['sample_id'],'reference_mid':sample['reference_mid'],
  'candidate_probs':probs,'candidate_act_escalate_probs':action,'parent_probs':parent_probs,
  'bootstrap_model_id':bootstrap['model_id'],'bootstrap_point_bps':point,'horizon_sec':300,
  'action_head_is_trade_permission':False,'remote_comparison':'NO_IDENTICAL_EXPORTED_INPUT_NO_EXTRA_MODEL_CALL',
  'gpu_arch':str(torch.cuda.get_device_properties(0).gcnArchName),'broker_orders_sent':0}
 if plan.get('future_source_read_profile') is not None:
  result['source_read_profile']=source_profile
 if v2:result['action_output_profile']=training_v2.ACTION_OUTPUT;result['action_inference']=action_report
 if v2:result['price_baseline_forecast']=price_reference_forecast
 if v5:
  result.update(candidate_raw_probs=raw_price_probs,price_output_profile=training_policy.PRICE_OUTPUT,
   price_calibration_profile_id=price_profile['profile_id'])
  price_calibration.verify_prediction(plan,result)
 if v7:
  result.update(price_input.receipt(row['state'],input_binding))
  price_input.verify_prediction(plan,result)
  price_input.verify_committed_input(plan,input_record,result)
 result.update(parent_calibration.prediction_fields(plan,parent_probs))
 result['input_receipt_sha256']=sha_file(input_path)
 result['resource_usage']=worker_resource_usage()
 # Keep the original final deadline check after the runtime proof. A present
 # prediction is authoritative for CPU closeout regardless of worker exit code.
 hip_proof=prove_before_publish(config)
 if hip_proof is not None:result['hip_runtime_proof']=hip_proof
 available=int(time.time()*1000)
 if available>=anchor+rule['horizon_ms']:raise ContractError('PREDICTION_AFTER_OUTCOME')
 result['created_at_ms']=available
 if publication_profile is not None:result['prediction_publication_profile']=publication_profile
 timing.update(prediction_write_started_at_ms=available,phase='PUBLISH_PREDICTION',
  inference_and_validation_ms=available-timing['input_selected_at_ms'])
 result['timing']=dict(timing)
 destination=plan_path.parent/f'prediction-{slot:03}.json';write_once(destination,result)
 published=int(time.time()*1000)
 timing.update(prediction_committed_at_ms=published,phase='COMPLETE',
  prediction_write_ms=published-available)
 if publication_profile is not None:
  timing['publication']={'profile':publication_profile,'plan_id':plan['plan_id'],'slot':slot,
   'prediction_digest':digest(result),'durably_published_at_ms':published}
 atomic(stage,timing)
 print(json.dumps({'prediction':str(destination),'candidate_id':candidate['candidate_id'],'slot':slot,'anchor_ms':anchor,'broker_orders_sent':0}),flush=True)
 return result

def main():
 p=argparse.ArgumentParser();p.add_argument('--config',type=Path,required=True);p.add_argument('--plan',type=Path,required=True);p.add_argument('--slot',type=int,required=True)
 a=p.parse_args();os.umask(0o077);torch.set_num_threads(2);predict(read_json(a.config),a.plan,a.slot)
if __name__=='__main__':main()
