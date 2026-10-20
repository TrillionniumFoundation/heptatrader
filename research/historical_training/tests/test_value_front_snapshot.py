"""Synthetic bounded-turn cache controls; these are not trading evidence."""
import hashlib,json,os,sys,tempfile,time,types,unittest
from pathlib import Path
from unittest.mock import Mock,patch
from laya_cell import financial_forward_source as source
from laya_cell.financial_forward_predict import Predictor,RegisteredPlanSnapshot
from laya_cell.integrity import ContractError,digest,sha_file


def put(path,value):
 path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
 path.write_bytes(value if isinstance(value,bytes) else json.dumps(value,sort_keys=True).encode())
 path.chmod(0o600);return path


class SourceFixture(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
  self.period=put(self.root/'period',b'period');self.period_sha=sha_file(self.period)
  self.journal=self.root/'journal';self.journal.mkdir();self.segment=self.journal/'producer-0.jsonl'
  manifest={'schema':'heptatrader.attribution-producer.v1','instrument':'EUR.USD','execution_domain':'PAPER','period_sha256':self.period_sha,'journal_directory':str(self.journal)}
  manifest_path=put(self.root/'manifest.json',manifest)
  self.registration={'manifest':str(manifest_path),'sha256':sha_file(manifest_path)}
  self.records=[];self.health=self.journal/'producer.health.json'
  put(self.health,{'at_ms':1,'dropped':0,'error':None})
  self.append('HEADER',{'manifest_sha256':self.registration['sha256'],'period_sha256':self.period_sha,'instrument':'EUR.USD','execution_domain':'PAPER','source_kind':'PINNED_DRIVER_CALLSITE','trading_authority':False})
  self.request('fresh',int(time.time()*1000)-100)
 def append(self,kind,data):
  record={'schema':'heptatrader.exact-decision-attribution.v1','producer_id':'producer','sequence':len(self.records),'previous':self.records[-1]['sha256'] if self.records else '0'*64,'kind':kind,'data':data,'dropped_total':0,'captured_at_ms':1,'written_at_ms':1}
  record['sha256']=digest(record);self.records.append(record)
  put(self.segment,b''.join(json.dumps(x,sort_keys=True,separators=(',',':')).encode()+b'\n' for x in self.records))
 def request(self,rid,started):
  self.append('REQUEST',{'request_id':rid,'period_sha256':self.period_sha,'instrument':'EUR.USD','prompt':'quote','prompt_sha256':hashlib.sha256(b'quote').hexdigest(),'model_id':'model-A','position_at_request':0,'started_at_ms':started})
 def snapshot(self):return source.VerifiedIngressSnapshot(self.registration,self.period_sha)
 def worker(self,snapshot):
  root=self.root/'forward';plan={'plan_id':'plan','input_end_ms':2**62,'producer_registration':self.registration}
  put(root/'plans/plan/plan.json',plan)
  return Predictor({'forward_root':str(root),'period_path':str(self.period),'period_sha256':self.period_sha},ingress_snapshots={'plan':snapshot})
 def test_unchanged_predictor_handoff_uses_one_ingress(self):
  with patch.object(source,'ingress',wraps=source.ingress) as scan:
   snap=self.snapshot();worker=self.worker(snap)
   with patch('laya_cell.financial_forward_predict.ingress',side_effect=AssertionError('DUPLICATE_INGRESS')),patch.object(worker,'predict',return_value=None):worker.step()
   self.assertEqual(scan.call_count,1);self.assertEqual(snap.full_scans,1)
 def test_returned_request_cannot_mutate_snapshot(self):
  snap=self.snapshot();first=snap.read(self.registration,self.period_sha)
  first[1]['fresh']['request']['model_id']='altered'
  self.assertEqual(snap.read(self.registration,self.period_sha)[1]['fresh']['request']['model_id'],'model-A')
 def test_append_revalidates_and_sees_new_request(self):
  snap=self.snapshot();self.request('next',int(time.time()*1000)-10)
  self.assertIn('next',snap.read(self.registration,self.period_sha)[1]);self.assertEqual(snap.full_scans,2)
 def test_same_size_rewrite_with_restored_mtime_is_rejected(self):
  snap=self.snapshot();st=self.segment.stat();self.segment.write_bytes(self.segment.read_bytes().replace(b'model-A',b'model-B'));os.utime(self.segment,ns=(st.st_atime_ns,st.st_mtime_ns))
  with self.assertRaises(ContractError):snap.read(self.registration,self.period_sha)
 def test_heartbeat_does_not_repeat_ingress(self):
  snap=self.snapshot();put(self.health,{'at_ms':999,'dropped':0,'error':None})
  snap.read(self.registration,self.period_sha);self.assertEqual(snap.full_scans,1)
 def test_health_error_blocks_prediction_this_turn(self):
  snap=self.snapshot();worker=self.worker(snap);put(self.health,{'at_ms':999,'dropped':0,'error':'writer failed'})
  with patch.object(worker,'predict') as predict:status=worker.step()
  predict.assert_not_called();self.assertEqual(status['state'],'HOLD_SOURCE_PROVENANCE');self.assertEqual(snap.full_scans,2)
 def test_mode_revocation_not_cached(self):
  snap=self.snapshot();self.segment.chmod(0o644)
  with self.assertRaises(ValueError):snap.read(self.registration,self.period_sha)
 def test_fresh_request_precedes_old_failure_bookkeeping(self):
  self.request('old',1);snap=self.snapshot();worker=self.worker(snap);visited=[]
  with patch.object(worker,'predict',side_effect=lambda plan,item:visited.append(item['request']['request_id'])):worker.step()
  self.assertEqual(visited,['fresh','old'])
 def test_other_registration_cannot_borrow_snapshot(self):
  snap=self.snapshot()
  with self.assertRaisesRegex(ContractError,'SCOPE'):snap.read({**self.registration,'sha256':'a'*64},self.period_sha)


class RegisteredTurnTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);root=Path(self.tmp.name)
  self.source=put(root/'source.jsonl',b'original\n');period=put(root/'period',b'period');trust=put(root/'trust.json',b'trust')
  self.head=put(root/'candidate/value-head.safetensors',b'weights-A');manifest=put(root/'candidate/candidate.json',{})
  data=put(root/'dataset.json',{'rows':[{'reward':{'input_attribution':{'source_prefixes':[{'path':str(self.source)}]}}}]})
  runtime=put(root/'runtime/worker.py',b'runtime')
  self.c={'period_path':str(period),'period_sha256':sha_file(period),'witness_trust_path':str(trust),'witness_trust_sha256':sha_file(trust),'worker_source_root':str(runtime.parent),'worker_runtime_sha256':{'worker.py':sha_file(runtime)}}
  self.plan={'schema':'heptatrader.current100k-value-forward-plan.v1','plan_id':'plan','period_sha256':self.c['period_sha256'],'candidate_directory':str(self.head.parent),'candidate_manifest_sha256':sha_file(manifest),'head_tensor_sha256':sha_file(self.head),'training_dataset_path':str(data),'training_dataset_sha256':sha_file(data),'runtime_sha256':{}}
  self.path=put(root/'plan/plan.json',self.plan);put(self.path.parent/'registration.json',{})
  self.verifier=patch('laya_cell.financial_forward_evaluate.validate_registration',return_value={'candidate_id':'candidate','base_id':'base'}).start();self.addCleanup(patch.stopall)
  patch('laya_cell.financial_forward_predict.validate_plan',return_value=self.plan).start()
  self.snap=RegisteredPlanSnapshot(self.c,self.path,self.plan)
 def test_full_bound_validation_once_for_same_turn(self):
  self.snap.candidate(self.c,self.plan);self.snap.candidate(self.c,self.plan)
  self.assertEqual(self.verifier.call_count,1);self.assertEqual(self.snap.reuses,2)
 def test_frozen_head_hash_checked_even_if_mtime_restored(self):
  st=self.head.stat();self.head.write_bytes(b'weights-B');os.utime(self.head,ns=(st.st_atime_ns,st.st_mtime_ns))
  with self.assertRaisesRegex(ContractError,'BYTES_CHANGED'):self.snap.candidate(self.c,self.plan)
 def test_changed_training_source_revalidates(self):
  self.source.write_bytes(b'original\nnew suffix\n');self.snap.candidate(self.c,self.plan)
  self.assertEqual(self.verifier.call_count,2)
 def test_revoked_training_source_does_not_return_cached_candidate(self):
  self.source.write_bytes(b'corrupt\n');self.verifier.side_effect=ContractError('PREFIX_REVOKED')
  with self.assertRaisesRegex(ContractError,'PREFIX_REVOKED'):self.snap.candidate(self.c,self.plan)
 def test_config_change_is_not_same_turn(self):
  with self.assertRaisesRegex(ContractError,'SCOPE_CHANGED'):self.snap.candidate({**self.c,'actor_update':True},self.plan)
 def test_returned_candidate_cannot_mutate_verified_candidate(self):
  value=self.snap.candidate(self.c,self.plan);value['base_id']='altered'
  self.assertEqual(self.snap.candidate(self.c,self.plan)['base_id'],'base')


class ModelLoadTest(unittest.TestCase):
 def test_verified_model_not_hashed_again_into_unused_field(self):
  with tempfile.TemporaryDirectory() as root:
   raw=Mock();raw.requires_grad_.return_value=raw;raw.eval.return_value=raw
   loader=Mock(return_value=(raw,object(),'base'))
   fake_torch=types.SimpleNamespace(set_num_threads=lambda n:None,cuda=types.SimpleNamespace(get_device_properties=lambda n:types.SimpleNamespace(gcnArchName='synthetic')))
   fake_model=types.SimpleNamespace(load_verified_laya=loader)
   worker=Predictor({'forward_root':root,'base_directory':root})
   with patch.dict(sys.modules,{'torch':fake_torch,'laya_cell.model':fake_model}),patch('laya_cell.financial_forward_predict.read_json',return_value={}),patch('laya_cell.financial_forward_predict.sha_file',side_effect=AssertionError('DUPLICATE_MODEL_HASH')):
    worker.load();worker.load()
   loader.assert_called_once();self.assertFalse(hasattr(worker,'base_bytes'))



class ValueGateImportPathTests(unittest.TestCase):
 def test_success_and_denial_restore_worker_import_path(self):
  import sys
  from laya_cell.current100k_forward_worker import acquire_value_resources
  before=list(sys.path);identity=id(sys.path)
  for admitted in (True,False):
   class Gate:
    def acquire(self,subject):
     self.subject=subject;sys.path[:0]=['/other/resident/learn','/other/resident/scripts']
     return admitted
   gate=Gate();subject={'plan_id':'fixed','requests':['request']}
   self.assertIs(acquire_value_resources(gate,subject),admitted)
   self.assertEqual(gate.subject,subject);self.assertEqual(sys.path,before);self.assertEqual(id(sys.path),identity)
 def test_guard_error_restores_worker_import_path(self):
  import sys
  from laya_cell.current100k_forward_worker import acquire_value_resources
  before=list(sys.path)
  class Gate:
   def acquire(self,subject):
    sys.path.insert(0,'/other/resident/learn');raise RuntimeError('SOURCE_REJECTED')
  with self.assertRaisesRegex(RuntimeError,'SOURCE_REJECTED'):acquire_value_resources(Gate(),{'request':'r'})
  self.assertEqual(sys.path,before)

if __name__=='__main__':unittest.main()
