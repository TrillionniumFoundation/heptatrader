"""Synthetic admission/lifecycle regressions; no actual model, source or orders."""
import copy,json,sys,tempfile,types,unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch,Mock
from historical_training.resident_resource_guard import Gate
from historical_training.common import EvidenceError
from laya_cell import current100k_forward_worker as w
from laya_cell.current100k_forward import PLAN_SCHEMA

NOW=1791523660000

class PredictorGateTests(unittest.TestCase):
 def test_explicit_predictor_shares_existing_heavy_lease(self):
  with tempfile.TemporaryDirectory() as d:
   cfg={'state_root':d};trainer=Gate(cfg,'trainer');predictor=Gate(cfg,'value-predictor')
   self.assertEqual(trainer.lease.path,predictor.lease.path)
   report=lambda:{'admit_new_worker':True,'reasons':[]}
   with patch.object(trainer,'check',side_effect=report),patch.object(predictor,'check',side_effect=report):
    try:
     self.assertTrue(trainer.acquire({'synthetic':True}));self.assertFalse(predictor.acquire({'synthetic':True}))
     trainer.release();self.assertTrue(predictor.acquire({'synthetic':True}))
    finally:trainer.release();predictor.release()
 def test_nonresearch_roles_still_rejected(self):
  for role in ('trader','selector','registrar','../predictor',''):
   with self.assertRaisesRegex(EvidenceError,'RESEARCH_ROLE_REQUIRED'):Gate({},role)

class WorkerLifecycleTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
  self.c={'forward_root':str(self.root),'worker_runtime_inventory_digest':'fixed-inventory','guard_config':str(self.root/'guard.json'),
   'period_path':'period','training_dataset_path':'train','oms_path':'oms','attribution_directory':'attribution',
   'worker_read_only_paths':[],'worker_source_root':'source','role_key_path':'key','witness_trust_path':'trust','period_sha256':'scope'}
  self.plan={'schema':PLAN_SCHEMA,'plan_id':'fixed-plan','candidate_id':'fixed-head','worker_runtime_inventory_digest':'fixed-inventory',
   'input_start_ms':NOW-100000,'input_end_ms':NOW+1000000,'producer_registration':{},'training_dataset_path':'train'}
  self.plan_path=self.root/'plans/fixed-plan/plan.json';self.plan_path.parent.mkdir(parents=True);self.write_plan()
  (self.root/'guard.json').write_text('{}')
  self.request={'request':{'started_at_ms':NOW-1000,'position_at_request':0},'request_sha256':'exact-input',
   'source_prefix':{'path':'synthetic-private-journal','bytes':10,'sha256':'exact-prefix'}}
  self.requests={'original-request':self.request};self.events=[];self.gates=[];self.hold=False
  test=self
  class FakeGate:
   def __init__(self,c,role):
    test.assertEqual(role,'value-predictor');test.gates.append(self);self.released=False;test.events.append('gate-init')
   def acquire(self,subject):test.events.append('acquire');return not test.hold
   def release(self):self.released=True;test.events.append('release')
  self.gate_class=FakeGate
  class FakePredictor:
   def __init__(self,c):test.events.append('model-init')
   def step(self):test.events.append('predict-step');return {'new_predictions':0,'broker_orders_sent':0}
   def __del__(self):test.events.append('model-destroy')
  self.predictor_class=FakePredictor
  self.torch=types.SimpleNamespace(set_num_threads=lambda n:None,cuda=types.SimpleNamespace(
   get_device_properties=lambda n:types.SimpleNamespace(total_memory=10*1024**3),
   set_per_process_memory_fraction=lambda f:None,synchronize=lambda:test.events.append('cuda-sync'),
   empty_cache=lambda:test.events.append('cuda-cleanup')))
 def write_plan(self):self.plan_path.write_text(json.dumps(self.plan))
 def turn(self,now=NOW):
  with ExitStack() as s:
   s.enter_context(patch.object(w,'validate_runtime'))
   s.enter_context(patch('laya_cell.financial_forward_evaluate.validate_registration'))
   s.enter_context(patch('laya_cell.financial_forward_witness.verify_trust'))
   s.enter_context(patch('laya_cell.financial_forward_source.ingress',return_value=({},self.requests,[],[])))
   s.enter_context(patch('historical_training.resident_resource_guard.verify_config'))
   s.enter_context(patch('historical_training.resident_resource_guard.Gate',self.gate_class))
   s.enter_context(patch('sandbox.enter'))
   s.enter_context(patch('laya_cell.financial_forward_predict.Predictor',self.predictor_class))
   s.enter_context(patch.dict(sys.modules,{'torch':self.torch}))
   s.enter_context(patch.object(w.time,'time',return_value=now/1000))
   return w.run(self.c,'predict')
 def files(self,pattern):return list((self.plan_path.parent/'predictions').glob(pattern))
 def test_no_fresh_request_does_not_initialize_gate_or_model(self):
  self.requests={};result=self.turn();self.assertEqual(result['state'],'WAIT_FRESH_ORIGINAL_REQUEST');self.assertFalse(self.events)
 def test_nonflat_request_does_not_initialize_or_mark_failure(self):
  self.request['request']['position_at_request']=89055;self.turn();self.assertFalse(self.events);self.assertFalse(self.files('*/failure.json'))
 def test_fresh_request_uses_real_explicit_gate_role_and_cleanup_before_release(self):
  self.turn();self.assertEqual(self.events,['gate-init','acquire','model-init','predict-step','model-destroy','cuda-sync','cuda-cleanup','release'])
 def test_expired_request_is_permanent_and_never_backdated_or_loaded(self):
  self.request['request']['started_at_ms']=NOW-60001;result=self.turn();paths=self.files('*/failure.json');self.assertEqual(len(paths),1)
  first=paths[0].read_bytes();record=json.loads(first);self.assertEqual(record['observed_at_ms'],NOW)
  self.assertEqual(record['reason'],'REQUEST_ALREADY_OLD_NO_BACKDATING');self.assertFalse(record['prediction_backdated'])
  self.assertEqual(record['request_sha256'],'exact-input');self.assertFalse(self.events);self.assertEqual(result['failed_or_missed_requests'],1)
  self.turn(now=NOW+10000);self.assertEqual(paths[0].read_bytes(),first);self.assertFalse(self.files('*/commit.json'))
 def test_closed_window_records_missed_request_without_model_load(self):
  self.plan['input_end_ms']=NOW;self.write_plan();self.turn();self.assertEqual(len(self.files('*/failure.json')),1);self.assertFalse(self.events)
 def test_outside_plan_request_is_not_added_to_new_plan(self):
  self.request['request']['started_at_ms']=self.plan['input_start_ms']-1;self.turn();self.assertFalse(self.files('*/failure.json'));self.assertFalse(self.events)
 def test_gate_init_error_retained_and_same_fresh_request_can_retry(self):
  accepted_gate=self.gate_class
  self.gate_class=Mock(side_effect=EvidenceError('RESEARCH_ROLE_REQUIRED'))
  with self.assertRaisesRegex(EvidenceError,'RESEARCH_ROLE_REQUIRED'):self.turn()
  attempts=self.files('*/attempts/*.json');self.assertEqual(len(attempts),1);record=json.loads(attempts[0].read_bytes())
  self.assertEqual(record['observed_at_ms'],NOW);self.assertEqual(record['reason'],'RESEARCH_ROLE_REQUIRED');self.assertFalse(self.files('*/failure.json'))
  first=attempts[0].read_bytes();self.gate_class=accepted_gate;self.turn(now=NOW+10000)
  self.assertIn('predict-step',self.events);self.assertEqual(attempts[0].read_bytes(),first)
  self.run_expired=NOW+60000
  result=self.turn(now=self.run_expired);self.assertEqual(result['failed_or_missed_requests'],1);self.assertEqual(len(self.files('*/attempts/*.json')),1)
 def test_resource_hold_retained_and_retried_before_expiration(self):
  self.hold=True;result=self.turn();self.assertEqual(result['state'],'HOLD_SHARED_RESOURCE_OR_SOURCE');self.assertTrue(self.gates[-1].released)
  self.assertEqual(len(self.files('*/attempts/*.json')),1);self.assertFalse(self.files('*/failure.json'));self.assertNotIn('model-init',self.events)
  self.hold=False;self.turn(now=NOW+10000);self.assertIn('predict-step',self.events);self.assertEqual(len(self.files('*/attempts/*.json')),1)
 def test_admission_exception_releases_created_gate(self):
  base=self.gate_class
  class BrokenGate(base):
   def acquire(self,subject):raise ValueError('synthetic acquire failure')
  self.gate_class=BrokenGate
  with self.assertRaisesRegex(ValueError,'acquire failure'):self.turn()
  self.assertTrue(self.gates[-1].released);self.assertEqual(len(self.files('*/attempts/*.json')),1)
 def test_model_constructor_exception_releases_lease(self):
  self.predictor_class=Mock(side_effect=ValueError('synthetic constructor failure'))
  with self.assertRaisesRegex(ValueError,'constructor failure'):self.turn()
  self.assertTrue(self.gates[-1].released);self.assertEqual(len(self.files('*/attempts/*.json')),1)
 def test_real_constructor_traceback_does_not_keep_model_alive_after_release(self):
  test=self;base=self.predictor_class
  class BrokenConstructor(base):
   def __init__(self,c):super().__init__(c);self.synthetic_tensor=object();raise ValueError('synthetic constructor self reference')
  self.predictor_class=BrokenConstructor
  with self.assertRaisesRegex(ValueError,'constructor self'):self.turn()
  self.assertLess(self.events.index('model-destroy'),self.events.index('release'))
 def test_step_exception_destroys_model_and_cuda_before_lease_release(self):
  base=self.predictor_class
  class BrokenPredictor(base):
   def step(self):raise ValueError('synthetic inference failure')
  self.predictor_class=BrokenPredictor
  with self.assertRaisesRegex(ValueError,'inference failure'):self.turn()
  self.assertTrue(self.gates[-1].released);self.assertEqual(len(self.files('*/attempts/*.json')),1)
  self.assertLess(self.events.index('model-destroy'),self.events.index('release'));self.assertLess(self.events.index('cuda-cleanup'),self.events.index('release'))
 def test_chained_inference_tracebacks_are_cleared_before_release(self):
  base=self.predictor_class
  class ChainedPredictor(base):
   def nested(self):self.synthetic_tensor=object();raise ValueError('synthetic cause')
   def step(self):
    try:self.nested()
    except ValueError as exc:raise RuntimeError('synthetic chained inference') from exc
  self.predictor_class=ChainedPredictor
  with self.assertRaisesRegex(RuntimeError,'chained inference'):self.turn()
  self.assertLess(self.events.index('model-destroy'),self.events.index('release'))
 def test_attempt_write_failure_still_clears_model_before_release(self):
  base=self.predictor_class
  class BrokenPredictor(base):
   def step(self):raise ValueError('synthetic inference failure')
  self.predictor_class=BrokenPredictor
  with patch.object(w,'request_record',side_effect=OSError('synthetic full disk')):
   with self.assertRaisesRegex(OSError,'full disk'):self.turn()
  self.assertLess(self.events.index('model-destroy'),self.events.index('release'));self.assertTrue(self.gates[-1].released)
 def test_keyboard_interrupt_releases_after_model_destruction(self):
  base=self.predictor_class
  class InterruptedPredictor(base):
   def step(self):raise KeyboardInterrupt()
  self.predictor_class=InterruptedPredictor
  with self.assertRaises(KeyboardInterrupt):self.turn()
  self.assertLess(self.events.index('model-destroy'),self.events.index('release'));self.assertEqual(len(self.files('*/attempts/*.json')),1)
 def test_cuda_cleanup_exception_still_releases_lease(self):
  self.torch.cuda.synchronize=Mock(side_effect=ValueError('synthetic cleanup failure'))
  with self.assertRaisesRegex(ValueError,'cleanup failure'):self.turn()
  self.assertTrue(self.gates[-1].released);self.assertIn('model-destroy',self.events);self.assertIn('cuda-cleanup',self.events)

if __name__=='__main__':unittest.main()
