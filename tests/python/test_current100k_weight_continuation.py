"""Pure file identity tests; no model, GPU or services."""
import copy,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from historical_training.common import atomic,sha,digest
from laya_cell import current100k_weight_shadow as W
import current100k_weight_continuation as C
import supervise_current100k_weight_shadow as S

class ContinuationTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
  self.root=Path(self.temp.name);self.state=self.root/'state';self.state.mkdir()
  self.oldroot=self.root/'old';self.newroot=self.root/'new'
  self.external=self.root/'encoder.py';self.external.write_text('PINNED=1\n')
  self.preopen='research/neuron_learning/laya_cell/current100k_preopen.py'
  self.math='research/neuron_learning/laya_cell/current100k_weight_shadow.py'
  self.eval=C.EVALUATOR
  stats='\n'.join('def '+n+'(): return 1' for n in C.PROTECTED_STATISTICS)+'\nclass Evaluator:\n def __init__(self): return 1\n def identity(self): return 1\n def evaluate_snapshot(self): return 1\n def snapshot(self): return 1\n def step(self): return 1\n'
  self.old_files={self.preopen:'CAPTURE=1\n',self.math:'HEAD=1\n',self.eval:stats+'def main(): return 0\n'}
  self.new_files=dict(self.old_files);self.new_files[self.preopen]='CAPTURE=2\n'
  self.new_files[self.eval]=stats+'def main(): return 2\n'
  self.old_manifest=self.release(self.oldroot,self.old_files)
  self.new_manifest=self.release(self.newroot,self.new_files)
  self.dataset=self.root/'dataset.json';atomic(self.dataset,{'rows':[]})
  p={'schema':W.PLAN_SCHEMA,'rule':copy.deepcopy(W.RULE),'claim_scope':'PROSPECTIVE_VALUE_RESEARCH_ONLY',
   'trade_authority':False,'automatic_selection':False,'actor_update':False,'feature_profile':W.FEATURE_PROFILE,
   'base_id':'b'*64,'period_sha256':'c'*64,'behavior_model_id':'test','producer_registration':{'sha256':'d'*64},
   'training_dataset_path':str(self.dataset),'training_dataset_sha256':sha(self.dataset),'training_mean_usd':-1.,
   'reward_config_path':str(self.root/'reward.json'),'reward_config_sha256':'e'*64,
   'training_producer_manifest_sha256':'f'*64,'cross_producer_transfer':True,
   'training_input_ids':['1'*64],'training_reward_ids':['2'*64],'training_row_ids':['3'*64],
   'training_cutoff_ms':1,'labels_observed_at_ms':2,'registered_at_ms':3,'input_start_ms':10,
   'input_end_ms':10+W.RULE['window_ms'],'outcome_end_ms':10+W.RULE['window_ms']+W.RULE['closure_grace_ms'],
   'heads':{a:{'path':str(self.root/(a+'.safetensors')),'sha256':digest(a),'construction_kind':'TEST'} for a in ('parent','patch')},
   'parent_arm':'parent','cluster_policy':'UTC_DAYS_MERGED_BY_OVERLAPPING_EPISODES','bootstrap_repetitions':2000,'bootstrap_seed':20261103,
   'encoder_backend':'cpu_fp32','encoder_threads':2,'encoder_batch_size':1,'numeric_profile':'TORCH_CPU_FP32_SHARED_LINEAR_USD100_CLIP15'}
  p['plan_id']=digest(p);self.plan=p
  atomic(self.state/'plan.json',p)
  atomic(self.state/'registration.json',{'plan_id':p['plan_id'],'plan_sha256':sha(self.state/'plan.json'),'durable_at_ms':4})
  atomic(self.state/'study.json',{'heads':p['heads']})
  self.template={'runtime_root':str(self.oldroot),'runtime_files_sha256':self.old_manifest['files'],'encoder_backend':'cpu_fp32','encoder_threads':2,'encoder_batch_size':1,'state_root':str(self.state/'preopen')}
  atomic(self.state/'predictor-template.json',self.template)
  self.old_config={'schema':'heptatrader.weight-shadow-supervisor.v1','state_root':str(self.state),
   'release_manifest':str(self.oldroot/'release-manifest.json'),'release_manifest_sha256':sha(self.oldroot/'release-manifest.json'),
   'predictor_template_path':str(self.state/'predictor-template.json'),'predictor_template_sha256':sha(self.state/'predictor-template.json'),
   'study_config_path':str(self.state/'study.json'),'study_config_sha256':sha(self.state/'study.json'),'python':'/pinned/python',
   'trade_authority':False,'automatic_selection':False,'actor_update':False}
  atomic(self.state/'supervisor-config.json',self.old_config)
  self.old_sha=sha(self.state/'supervisor-config.json')
  atomic(self.state/'predictor-config.json',dict(self.template,study_plan_path=str(self.state/'plan.json'),study_plan_sha256=sha(self.state/'plan.json')))
  atomic(self.state/'activation.json',{'plan_id':p['plan_id'],'activated_at_ms':5,'supervisor_config_sha256':self.old_sha,
   'study_config_sha256':self.old_config['study_config_sha256'],'predictor_config_sha256':sha(self.state/'predictor-config.json'),'broker_orders_sent':0})
  self.new_config=dict(self.old_config,release_manifest=str(self.newroot/'release-manifest.json'),release_manifest_sha256=sha(self.newroot/'release-manifest.json'))

 def release(self,root,files,external=None):
  for name,text in files.items():
   path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text)
  manifest={'schema':'heptatrader.weight-research-release.v1','files':{n:sha(root/n) for n in files},'external_files':external or {str(self.external):sha(self.external)}}
  atomic(root/'release-manifest.json',manifest);return manifest

 def prepare(self):
  return C.prepare_record(self.new_config,self.state/'supervisor-config.json',self.old_sha,recorded_at_ms=6)
 def pin_record(self,record):
  path=self.state/'continuation.json';atomic(path,record)
  return dict(self.new_config,continuation={'path':str(path),'sha256':sha(path)})
 def test_valid_maintenance_preserves_original_bytes_and_window(self):
  before={p:p.read_bytes() for p in self.state.glob('*.json')}
  record=self.prepare();path=Path(record['predictor_config']['path']);self.assertFalse(path.exists())
  config=self.pin_record(record);plan,derived=C.continue_activation(config,self.state)
  self.assertEqual(plan,self.plan);self.assertEqual(sha(derived),record['predictor_config']['sha256'])
  for p,raw in before.items():self.assertEqual(p.read_bytes(),raw)
  value=W.read(derived);old=W.read(self.state/'predictor-config.json')
  self.assertEqual({k for k in value if value[k]!=old[k]},{'runtime_root','runtime_files_sha256'})
  self.assertEqual(C.continue_activation(config,self.state),(plan,derived))

 def test_missing_or_wrong_record_cannot_authorize(self):
  with self.assertRaises(ValueError):C.continue_activation(self.new_config,self.state)
  record=self.prepare();config=self.pin_record(record);config['continuation']['sha256']='0'*64
  with self.assertRaises(ValueError):C.continue_activation(config,self.state)
  self.assertFalse(Path(record['predictor_config']['path']).exists())
 def test_non_runtime_supervisor_field_change_rejected(self):
  config=self.pin_record(self.prepare());config['python']='/another/python'
  with self.assertRaisesRegex(ValueError,'SUPERVISOR_SCOPE_CHANGED'):C.continue_activation(config,self.state)
 def test_derived_predictor_path_or_hash_cannot_be_substituted(self):
  record=self.prepare();record['predictor_config']['sha256']='0'*64
  with self.assertRaisesRegex(ValueError,'RECORD_SCOPE_CHANGED'):C.continue_activation(self.pin_record(record),self.state)
 def test_template_and_old_predictor_changes_rejected(self):
  config=self.pin_record(self.prepare());value=W.read(self.state/'predictor-template.json');value['encoder_threads']=4
  atomic(self.state/'predictor-template.json',value)
  with self.assertRaisesRegex(ValueError,'PIN_CHANGED'):C.continue_activation(config,self.state)

 def test_rebound_window_still_fails_original_record(self):
  config=self.pin_record(self.prepare());plan=copy.deepcopy(self.plan)
  for k in ('input_start_ms','input_end_ms','outcome_end_ms'):plan[k]+=1
  plan['plan_id']=digest({k:v for k,v in plan.items() if k!='plan_id'})
  atomic(self.state/'plan.json',plan)
  atomic(self.state/'registration.json',{'plan_id':plan['plan_id'],'plan_sha256':sha(self.state/'plan.json'),'durable_at_ms':4})
  activation=W.read(self.state/'activation.json');activation['plan_id']=plan['plan_id']
  old=W.read(self.state/'predictor-config.json');old['study_plan_sha256']=sha(self.state/'plan.json')
  atomic(self.state/'predictor-config.json',old);activation['predictor_config_sha256']=sha(self.state/'predictor-config.json')
  atomic(self.state/'activation.json',activation)
  with self.assertRaisesRegex(ValueError,'RECORD_SCOPE_CHANGED'):C.continue_activation(config,self.state)
 def test_changed_head_math_module_is_never_allowed(self):
  self.new_files[self.math]='HEAD=2\n';self.release(self.newroot,self.new_files)
  self.new_config['release_manifest_sha256']=sha(self.newroot/'release-manifest.json')
  with self.assertRaisesRegex(ValueError,'SHADOW_INTERFACE'):self.prepare()

 def test_reward_cutoff_path_does_not_allow_unreviewed_reward_code(self):
  self.old_files[C.REWARD_CUTOFF]='def current(): return 1\n'
  self.new_files[C.REWARD_CUTOFF]='def current(): return 2\n'
  before=self.release(self.oldroot,self.old_files)
  after=self.release(self.newroot,self.new_files)
  with self.assertRaisesRegex(ValueError,'REWARD_CUTOFF_PATCH_CHANGED'):
   C.release_changes(self.oldroot,before,self.newroot,after)

 def test_changed_statistics_rejected_despite_permitted_file(self):
  self.new_files[self.eval]=self.new_files[self.eval].replace('def metrics(): return 1','def metrics(): return 9')
  self.release(self.newroot,self.new_files);self.new_config['release_manifest_sha256']=sha(self.newroot/'release-manifest.json')
  with self.assertRaisesRegex(ValueError,'STATISTICS_CHANGED'):self.prepare()
 def test_external_encoder_scope_change_rejected(self):
  extra=self.root/'other.py';extra.write_text('OTHER=1\n')
  self.release(self.newroot,self.new_files,{str(extra):sha(extra)})
  self.new_config['release_manifest_sha256']=sha(self.newroot/'release-manifest.json')
  with self.assertRaisesRegex(ValueError,'EXTERNAL_PINS_CHANGED'):self.prepare()
 def test_changed_candidate_in_plan_rejected(self):
  config=self.pin_record(self.prepare());plan=W.read(self.state/'plan.json');plan['heads']['patch']['sha256']='9'*64
  atomic(self.state/'plan.json',plan)
  with self.assertRaises(ValueError):C.continue_activation(config,self.state)

 def test_snapshot_reader_may_change_but_other_methods_may_not(self):
  for name in C.EVALUATOR_RUNTIME_METHODS:
   self.new_files[self.eval]=self.new_files[self.eval].replace('def '+name+'(self): return 1','def '+name+'(self): return 2')
  self.release(self.newroot,self.new_files);self.new_config['release_manifest_sha256']=sha(self.newroot/'release-manifest.json')
  self.prepare()
  self.new_files[self.eval]=self.new_files[self.eval].replace('def step(self): return 1','def step(self): return 2')
  self.release(self.newroot,self.new_files);self.new_config['release_manifest_sha256']=sha(self.newroot/'release-manifest.json')
  with self.assertRaisesRegex(ValueError,'STATISTICS_CHANGED'):self.prepare()


 def test_only_exact_remember_commands_directory_interface_is_allowed(self):
  before="from pathlib import Path\nHEAD=1\nclass ShadowStore:\n def remember_commands(self,bindings):\n  folder=self.root/'seen-commands'\n  return folder,bindings\n"
  after=before.replace('self,bindings):','self,bindings,*,state_root=None):').replace("folder=self.root/'seen-commands'","folder=(self.root if state_root is None else Path(state_root))/'seen-commands'")
  (self.oldroot/self.math).write_text(before);(self.newroot/self.math).write_text(after)
  C.protect_shadow_math(self.oldroot,self.newroot)
  (self.newroot/self.math).write_text(after.replace('HEAD=1','HEAD=2'))
  with self.assertRaisesRegex(ValueError,'SHADOW_MATH_CHANGED'):C.protect_shadow_math(self.oldroot,self.newroot)


 def test_supervisor_explicit_hook_consumes_pinned_continuation(self):
  record=self.prepare();config=self.pin_record(record)
  before={p:p.read_bytes() for p in self.state.glob('*.json')}
  with patch.object(W,'freeze',side_effect=AssertionError('must not register again')):
   plan,path=S.ensure_activation(config,{}, {},self.state)
  self.assertEqual(plan,self.plan)
  self.assertEqual(str(path),record['predictor_config']['path'])
  self.assertEqual(sha(path),record['predictor_config']['sha256'])
  for p,raw in before.items():self.assertEqual(p.read_bytes(),raw)

 def test_supervisor_hook_rejects_invalid_receipt_without_derived_writes(self):
  record=self.prepare();config=self.pin_record(record)
  config['continuation']['sha256']='0'*64
  with self.assertRaisesRegex(ValueError,'CONTINUATION_PIN_CHANGED'):
   S.ensure_activation(config,{}, {},self.state)
  self.assertFalse(Path(record['predictor_config']['path']).exists())

 def runner_pair(self):
  old=(
   'ENCODER_THREADS=2\n'
   'def original_guard(config): return config["pinned_guard"]\n'
   'def run(config):\n'
   ' class Source:\n'
   '  def read(self):\n'
   '   value=ingress.read()\n'
   '   return value\n'
   ' source=Source()\n'
   ' return source\n')
  new=old.replace('def read(self):','def read(self,*,retry_deadline_ms=None):').replace(
   'value=ingress.read()','value=ingress.read(retry_deadline_ms=retry_deadline_ms)').replace(
   '   return value\n','   return value\n  def read_with_deadline(self,deadline_ms):return self.read(retry_deadline_ms=deadline_ms)\n')
  return old,new

 def check_runners(self,old,new):
  before=self.release(self.oldroot,{**self.old_files,C.RUNNER:old})
  after=self.release(self.newroot,{**self.new_files,C.RUNNER:new})
  return C.release_changes(self.oldroot,before,self.newroot,after)

 def test_only_reviewed_capture_deadline_adapter_is_accepted(self):
  old,new=self.runner_pair()
  changes=self.check_runners(old,new)
  self.assertIn(C.RUNNER,changes)
  self.assertNotEqual(changes[C.RUNNER]['old_sha256'],changes[C.RUNNER]['new_sha256'])

 def test_other_runner_semantics_remain_protected(self):
  old,new=self.runner_pair()
  alterations=[
   new.replace('ENCODER_THREADS=2','ENCODER_THREADS=4'),
   new.replace('return config["pinned_guard"]','return True'),
   new.replace('   return value','   return {}'),
   new.replace('retry_deadline_ms=deadline_ms','retry_deadline_ms=None'),
   new+'\ndef extra_runtime_entry(): return True\n',
  ]
  for value in alterations:
   with self.subTest(value=value):
    with self.assertRaisesRegex(ValueError,'OUTSIDE_CAPTURE_RETRY_CHANGED'):
     self.check_runners(old,value)

 def test_unknown_original_runner_cannot_use_adapter_allowance(self):
  old,new=self.runner_pair()
  with self.assertRaisesRegex(ValueError,'ORIGINAL_CAPTURE'):
   self.check_runners(old.replace('value=ingress.read()','value=other.read()'),new)

 def test_new_unreviewed_runtime_module_stays_forbidden(self):
  self.new_files['scripts/unreviewed_runtime.py']='ALLOWED=False\n'
  self.release(self.newroot,self.new_files)
  self.new_config['release_manifest_sha256']=sha(self.newroot/'release-manifest.json')
  with self.assertRaisesRegex(ValueError,'UNREVIEWED_RUNTIME_CHANGE'):self.prepare()

 def test_pinned_bytes_keep_strict_json_validation(self):
  path=self.root/'strict-pinned.json'
  for text,reason in (('{"same":1,"same":2}', 'DUPLICATE_JSON_KEY'),
                      ('{"value":NaN}', 'NONFINITE_JSON')):
   with self.subTest(reason=reason):
    path.write_text(text)
    with self.assertRaisesRegex(W.EvidenceError,reason):C.pinned(path,sha(path))
  with path.open('wb') as stream:stream.truncate(64*1024*1024+1)
  with self.assertRaisesRegex(W.EvidenceError,'INVALID_OR_OVERSIZED_FILE'):
   C.pinned(path,'0'*64)

if __name__=='__main__':unittest.main()
