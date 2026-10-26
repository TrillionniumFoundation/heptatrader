"""Read-only verification of canonical selections after independent evaluation.

A local signed PASS only prepares a request. Canonical CURRENT plus distinct
canonical selector signatures and exact payload must verify before byte loading.
This bridge does not invoke a product consumer or acknowledge its deployment.
No effect capability, actor change or trade permission exists.
"""
from __future__ import annotations
import argparse,fcntl,json,os,signal,subprocess,time
from pathlib import Path
from .integrity import read_json,digest,sha_file,ContractError
_original_sha_file=sha_file
def sha_file(path):return _original_sha_file(Path(path))
from .resident_store import write_once,atomic
from .financial_forward_witness import verify_record,sign_record
from .financial_forward_evaluate import validate_registration


def native(config,request):
 p=Path(config['canonical_verifier_binary'])
 if sha_file(p)!=config['canonical_verifier_sha256']:raise ContractError('CANONICAL_VERIFIER_CHANGED')
 process=subprocess.run([str(p)],input=json.dumps(request,sort_keys=True,separators=(',',':')).encode(),capture_output=True,timeout=10)
 out=json.loads(process.stdout)
 if (type(out) is not dict or process.returncode
     or out.get('native_source_sha')!='108bc534d33c7f9e48caa569c254253f6c356271'
     or out.get('effect_authority') is not False):
  raise ContractError('CANONICAL_VERIFIER_REJECTED:'+str(out.get('error') if isinstance(out,dict) else 'RESPONSE_SHAPE'))
 operation=request.get('operation')
 contracts={
  'validate_candidate_manifest':('CANONICAL_MANIFEST_VALID_NOT_OWNER_ADMITTED',False,('manifest_digest',)),
  'verify_current_selection':('CANONICAL_SIGNED_SELECTION_VERIFIED_READ_ONLY',True,('selection_digest','content_digest','registry_head_digest'))}
 if operation not in contracts:raise ContractError('CANONICAL_VERIFIER_OPERATION')
 status,selected,digests=contracts[operation]
 # These are the pinned Rust adapter's typed read-only results. Neither a
 # generic PASS nor an owner-admitted boolean is an equivalent receipt.
 if (out.get('status')!=status or out.get('selection_verified') is not selected
     or out.get('canonical_agentd_admitted') is not False):
  raise ContractError('CANONICAL_VERIFIER_RECEIPT_CONTRACT')
 if any(type(out.get(key)) is not str or len(out[key])!=64
        or any(char not in '0123456789abcdef' for char in out[key]) for key in digests):
  raise ContractError('CANONICAL_VERIFIER_RECEIPT_DIGEST')
 if selected and out['content_digest']!=request.get('expected_content_digest'):
  raise ContractError('NO_CANONICAL_SIGNED_SELECTION')
 return out


def manifest_for(plan,candidate):
 return {'artifact_id':'financial.'+plan['candidate_id'],'generation':plan['trial'],
  'source_dataset_digests':[plan['training_dataset_sha256']],
  'lineage_digests':sorted({candidate['base_id'],plan['plan_id'],digest(candidate['reward_ids'])}),
  'bytes_digest':plan['head_tensor_sha256'],'encoded_size_bytes':(Path(plan['candidate_directory'])/'value-head.safetensors').stat().st_size,
  'training_code_digest':digest(candidate['source_python_sha256']),
  'runtime_tuple_digest':digest({'torch':candidate['torch_version'],'rocm':candidate['rocm'],'base':candidate['base_id']}),
  'device_profile_digest':digest({'gpu_arch':candidate['gpu_arch']}),'objective_class_digest':digest(candidate['financial_profile']),
  'compatibility_digest':digest(plan['behavior_scope']),'schema_profile_digest':digest({'readout':'float32.linear.768+1.usd'}),
  'normalization_digest':digest({'pooling':'MASKED_MEAN_L2_NORMALIZED','target_scale_usd':100.}),
  'producer_id':'heptatrader.financial-value.candidate-generator','created_at':candidate['created_at_ms'],
  'expires_at':plan['outcome_end_ms']+86400000}


def evaluate_gate(config,plan,signed,now):
 verify_record(config,'evaluator',signed)
 if signed.get('plan_id')!=plan['plan_id'] or signed.get('candidate_id')!=plan['candidate_id']:raise ContractError('EVALUATION_SELECTION_SCOPE')
 if not 0<=now-signed['role_witness']['signed_at_ms']<=60000:raise ContractError('CURRENT_EVALUATION_REQUIRED')
 if signed.get('state')!='STATISTICALLY_QUALIFIED_REQUIRES_NATIVE_OWNER':return False
 if signed.get('reasons') or now<plan['outcome_end_ms'] or signed.get('selection_performed') is not False:
  raise ContractError('EVALUATION_NOT_COMPLETE_AND_INDEPENDENT')
 return True


def canonical_selection(config,plan,signed):
 candidate=read_json(Path(plan['candidate_directory'])/'candidate.json');evaluation_support=digest({'plan_id':plan['plan_id'],
  'candidate_id':plan['candidate_id'],'outcome_set_digest':signed['outcome_set_digest'],'training_dataset_sha256':plan['training_dataset_sha256']})
 m=manifest_for(plan,candidate)
 manifest_receipt=native(config,{'operation':'validate_candidate_manifest','manifest':m,'payload_path':str(Path(plan['candidate_directory'])/'value-head.safetensors')})
 # Canonical V2 publication uses the validated manifest digest as the V1
 # registry support_digest (LearningArtifactOwnerHost), not evaluation support.
 # Keep the independent outcome binding explicitly separate.
 support=manifest_receipt['manifest_digest']
 base={'plan_id':plan['plan_id'],'candidate_id':plan['candidate_id'],'support_digest':support,
       'evaluation_support_digest':evaluation_support,'structural_receipt':manifest_receipt,
       'promotion_performed':False,'automatic_trading':False,'actor_updated':False,'canonical_agentd_accepted':False,
       'canonical_selection_verified':False,'product_consumed':False,'product_consume_receipt':None}
 trust_spec=config.get('canonical_owner_registration')
 if not trust_spec:return {**base,'state':'AWAITING_CANONICAL_AGENTD_OWNER_ENROLLMENT'}
 # The operator pins a real host trust root; a supplied model bundle cannot
 # choose its own trusted signing keys or anti-rollback requirements.
 if sha_file(trust_spec['path'])!=trust_spec['sha256']:raise ContractError('CANONICAL_OWNER_TRUST_CHANGED')
 host=read_json(Path(trust_spec['path']))
 if host.get('route')!='AGENTD_FINANCIAL_VALUE_READONLY_V1':raise ContractError('WRONG_CANONICAL_PRODUCT_ROUTE')
 bundle=read_json(Path(host['current_bundle_path']))
 request={'operation':'verify_current_selection','owner_trust':host['owner_trust'],'selection_trust':host['selection_trust'],
  'head_requirement':host['head_requirement'],'signed_selection':bundle['signed_selection'],
  'signed_current_head':bundle['signed_current_head'],'registry_receipt':bundle['registry_receipt'],
  'registry_snapshot_path':bundle['registry_snapshot_path'],'payload_path':bundle['payload_path'],
  'expected_content_digest':plan['head_tensor_sha256'],'expected_support_digest':support,'expected_compatibility_digest':digest(plan['behavior_scope'])}
 out=native(config,request)
 if out.get('selection_verified') is not True or out.get('content_digest')!=plan['head_tensor_sha256']:raise ContractError('NO_CANONICAL_SIGNED_SELECTION')
 # load_selected_candidate/with_current verified bytes only. This adapter
 # neither admits a product deployment nor calls a financial-value consumer.
 return {**base,'state':'NATIVE_SELECTED_READONLY_CANDIDATE','canonical_selection_verified':True,
   'canonical_selection_verification':out,'verified_host_route':host['route'],
   'payload_path':bundle['payload_path'],'artifact_generation':bundle['signed_selection']['artifact_generation']}


class Selector:
 def __init__(self,c):self.c=c;self.root=Path(c['forward_root']);self.stop=False
 def step(self):
  if sha_file(self.c['period_path'])!=self.c['period_sha256']:raise ContractError('SELECTION_PERIOD_CHANGED')
  now=int(time.time()*1000);states=[];selected=[]
  for path in sorted((self.root/'plans').glob('*/plan.json')):
   plan=read_json(path);validate_registration(self.c,path,plan)
   candidate=read_json(Path(plan['candidate_directory'])/'candidate.json')
   structural=native(self.c,{'operation':'validate_candidate_manifest','manifest':manifest_for(plan,candidate),'payload_path':str(Path(plan['candidate_directory'])/'value-head.safetensors')})
   status={'plan_id':plan['plan_id'],'candidate_id':plan['candidate_id'],'as_of_ms':now,
    'state':'WAIT_INDEPENDENT_PROSPECTIVE_EVALUATION','manifest_structurally_verified':True,
    'native_manifest_digest':structural['manifest_digest'],'canonical_agentd_accepted':False,'promotion_performed':False,'trading_authority':False,
    'canonical_selection_verified':False,'product_consumed':False,'product_consume_receipt':None}
   assessment_path=path.parent/'assessment-signed.json'
   if assessment_path.exists():
    signed=read_json(assessment_path)
    if evaluate_gate(self.c,plan,signed,now):status.update(canonical_selection(self.c,plan,signed))
    else:status.update(evaluation_state=signed['state'],evaluation_reasons=signed['reasons'])
   state_path=path.parent/'selector-status.json';atomic(state_path,sign_record(self.c,'selector',status));states.append(status)
   if status.get('canonical_selection_verified') is True:selected.append(status)
  target=self.root/'selection-current.json'
  if selected:
   # Never silently roll back to a lower generation on missing/corrected evidence.
   chosen=max(selected,key=lambda x:x['artifact_generation']);old=read_json(target) if target.exists() else {}
   previous=old.get('high_water_generation',0)
   if chosen['artifact_generation']<previous:raise ContractError('READONLY_SELECTION_ROLLBACK')
   atomic(target,sign_record(self.c,'selector',{'state':'READONLY_AVAILABLE','candidate_id':chosen['candidate_id'],
      'high_water_generation':chosen['artifact_generation'],'native':chosen['canonical_selection_verification'],
      'support_digest':chosen['support_digest'],'evaluation_support_digest':chosen['evaluation_support_digest'],
      'canonical_selection_verified':True,'canonical_agentd_accepted':False,'promotion_performed':False,
      'product_consumed':False,'product_consume_receipt':None,
      'authority':'READ_ONLY_FINANCIAL_VALUE_NO_TRADES','at_ms':now}))
  elif target.exists():
   old=read_json(target);atomic(target,sign_record(self.c,'selector',{'state':'UNAVAILABLE_NO_FALLBACK',
    'canonical_selection_verified':False,'canonical_agentd_accepted':False,'promotion_performed':False,
    'product_consumed':False,'product_consume_receipt':None,'high_water_generation':old.get('high_water_generation',0),'previous_candidate_id':old.get('candidate_id',old.get('previous_candidate_id')),'at_ms':now}))
  result={'at_ms':now,'plans':len(states),'selected_count':len(selected),'canonical_owner_configured':bool(self.c.get('canonical_owner_registration')),
   'states':{s:sum(x['state']==s for x in states) for s in sorted({x['state'] for x in states})},
   'consumed_count':0,'canonical_agentd_accepted':False,'broker_orders_sent':0,'actor_updated':False}
  atomic(self.root/'selector-status.json',result);return result


def main():
 p=argparse.ArgumentParser();p.add_argument('--config',type=Path,required=True);p.add_argument('--once',action='store_true');a=p.parse_args();os.umask(0o077)
 c=read_json(a.config);root=Path(c['forward_root']);root.mkdir(mode=0o700,parents=True,exist_ok=True)
 lock=os.open(root/'selector.lock',os.O_WRONLY|os.O_CREAT|os.O_NOFOLLOW|os.O_CLOEXEC,0o600);fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);worker=Selector(c)
 def stop(*_):worker.stop=True
 signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
 while not worker.stop:
  try:worker.step()
  except Exception as exc:
   atomic(root/'selector-status.json',{'at_ms':int(time.time()*1000),'state':'UNAVAILABLE_NO_SELECTION','reason':str(exc)[:250],'broker_orders_sent':0})
   target=root/'selection-current.json'
   if target.exists():
    old=read_json(target);atomic(target,{'state':'UNAVAILABLE_NO_FALLBACK','high_water_generation':old.get('high_water_generation',0),
    'canonical_selection_verified':False,'canonical_agentd_accepted':False,'promotion_performed':False,
    'product_consumed':False,'product_consume_receipt':None,'at_ms':int(time.time()*1000)})
  if a.once:break
  for _ in range(10):
   if worker.stop:break
   time.sleep(1)
 os.close(lock)
if __name__=='__main__':main()
