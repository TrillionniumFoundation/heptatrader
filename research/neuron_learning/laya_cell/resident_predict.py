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
from .resident_store import write_once
from .resident_data import collect,forecast_input
from .dataset import encode_rows
from .model import load_verified_laya
from .resident_model import ResidentDecisionCell
from .native import NativePorts
from .resident_train import validate_parent


def predict(config,plan_path,slot):
 from safetensors.torch import load_file
 plan=read_json(plan_path);candidate_dir=Path(plan['candidate_directory']);parent_dir=Path(plan['parent_directory'])
 candidate=validate_parent(candidate_dir);parent=validate_parent(parent_dir)
 if candidate['candidate_id']!=plan['candidate_id'] or parent['candidate_id']!=candidate['parent_id']:
  raise ContractError('PREDICT_CANDIDATE_PARENT_CHANGED')
 if not 0<=slot<plan['rule']['slots']:raise ContractError('SLOT_BOUND')
 due_start=plan['start_ms']+slot*plan['rule']['slot_spacing_ms'];late=due_start+plan['rule']['input_grace_ms']
 if int(time.time()*1000)>late:raise ContractError('SLOT_ALREADY_MISSED')
 raw,tok,base_id=load_verified_laya(Path(config['base_directory']),read_json(Path(config['base_directory'])/'base-manifest.json'))
 if candidate['base_id']!=base_id:raise ContractError('BASE_CHANGED')
 cell=ResidentDecisionCell(raw,base_id)
 cell.restore_parent(load_file(str(parent_dir/'adapter.safetensors')),allow_scorer_only=True);parent_tensors=cell.trainable_state()
 candidate_tensors=load_file(str(candidate_dir/'adapter.safetensors'))
 while True:
  now=int(time.time()*1000)
  if now>late:raise ContractError('NO_FRESH_INPUT_IN_RESERVED_SLOT')
  _,_,_,quotes=collect(config,now)
  try:
   sample,row=forecast_input(config,quotes,now)
   if sample['anchor_ms']<due_start:raise ContractError('QUOTE_PRECEDES_SLOT')
   break
  except ContractError:time.sleep(1)
 anchor=sample['anchor_ms']
 if candidate['created_at_ms']>=anchor or candidate['trained_until_ms']>anchor:
  raise ContractError('CANDIDATE_NOT_FROZEN_BEFORE_INPUT')
 bootstrap_root=Path(config['baseline_directory']);freeze=read_json(bootstrap_root/'freeze-manifest.json',16*1024**2)
 if sha_file(bootstrap_root/'model.json')!=freeze['inventory_sha256']['model.json']:raise ContractError('BOOTSTRAP_CHANGED')
 bootstrap=read_json(bootstrap_root/'model.json')
 if bootstrap['trained_until_ms']>anchor:raise ContractError('BOOTSTRAP_LOOKAHEAD')
 batch={k:v.to('cuda') for k,v in encode_rows(tok,[row]).items()}
 with torch.no_grad():
  cell.restore_trainable(parent_tensors);parent_probs=cell(**batch)[0].softmax(-1)[0].cpu().tolist()
  cell.restore_trainable(candidate_tensors);p,a=cell(**batch)
  probs=p.softmax(-1)[0].cpu().tolist();action=a.softmax(-1)[0].cpu().tolist()
 cell.assert_frozen()
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
 destination=plan_path.parent/f'prediction-{slot:03}.json';write_once(destination,result)
 print(json.dumps({'prediction':str(destination),'candidate_id':candidate['candidate_id'],'slot':slot,'anchor_ms':anchor,'broker_orders_sent':0}),flush=True)
 return result

def main():
 p=argparse.ArgumentParser();p.add_argument('--config',type=Path,required=True);p.add_argument('--plan',type=Path,required=True);p.add_argument('--slot',type=int,required=True)
 a=p.parse_args();os.umask(0o077);torch.set_num_threads(2);predict(read_json(a.config),a.plan,a.slot)
if __name__=='__main__':main()
