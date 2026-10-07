"""Existing native NDU and Plasticity ports; no Python surrogate acceptance."""
import json,math,subprocess
from pathlib import Path
from .integrity import ContractError,canonical,digest,sha_file
SOURCE_SHA='78fdb0cf8537e3a84fc6e0a849707559c80881e8'
class NativePorts:
 def __init__(self,path:Path,expected_sha256:str):
  self.path=path
  if sha_file(path)!=expected_sha256:raise ContractError('NATIVE_BINARY_IDENTITY_MISMATCH')
 def call(self,request):
  p=subprocess.run([str(self.path)],input=canonical(request),capture_output=True,timeout=30)
  if p.returncode:raise ContractError('NATIVE_REJECTED:'+p.stdout.decode(errors='replace')[:400])
  out=json.loads(p.stdout)
  if out.get('native_source_sha')!=SOURCE_SHA or out.get('authorization_effect')!='NONE':raise ContractError('NATIVE_SOURCE_OR_AUTHORITY_MISMATCH')
  return out
 def predictive_utility(self,losses,rows,dataset_id,objective_id):
  if not losses or len(losses)!=len(rows) or len(rows)>512:raise ContractError('NDU_OBSERVATION_BOUND')
  events=[]
  for i,(loss,row) in enumerate(zip(losses,rows)):
   if not math.isfinite(loss) or not 0<=loss<=2.00001:raise ContractError('NDU_BRIER_UNITS_INVALID')
   dt=0 if i+1==len(rows) else max(0,rows[i+1]['anchor_ms']-row['anchor_ms'])
   events.append({'instant':-loss/len(rows),'discount':math.exp(-dt/3600000),'event_digest':row['outcome_receipt_digest']})
  return self.call({'op':'utility','events':events,'dataset_digest':dataset_id,'objective_digest':objective_id,'outcome_digest':digest([r['outcome_receipt_digest'] for r in rows])})
 def proposal(self,*,cell,selected_id,dataset_id,utility_id,evaluation_id,gradient_receipt,generation):
  request={'op':'laya_proposal','base_id':cell.base_id,'selected_artifact_digest':selected_id,'generation':generation,'dataset_digest':dataset_id,'update_rule_digest':digest({'profile':cell.profile,'estimator':'SUPERVISED_CE_PLUS_AUX_TEACHER_KL'}),'modulator_digest':utility_id,'eligibility_digest':gradient_receipt,'evaluation_digest':evaluation_id,'layers':cell.native_parameter_rows()}
  out=self.call(request)
  if out.get('status')!='RequiresIndependentAcceptance' or out.get('selection_performed') is not False:raise ContractError('NATIVE_CANDIDATE_STATUS_MISMATCH')
  return out
