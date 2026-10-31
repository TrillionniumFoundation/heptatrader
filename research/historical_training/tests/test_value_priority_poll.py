import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from historical_training.common import digest
from historical_training.resident_resource_guard import evaluator_idle_poll_seconds,_prediction_retry_window,guarded_evaluator_class

class Gate:
 def __init__(self,role='evaluator',enabled=True,draining=False):
  self.role=role;self.draining=draining
  self.c={'value_priority':{'schema':'PINNED'}} if enabled else {}

class PollTests(unittest.TestCase):
 def test_real_reservation_expiry_shortens_poll_without_admission(self):
  for remaining,expected in ((0,1),(599,1),(2000,3),(9999,10),(60000,10)):
   with self.subTest(remaining_ms=remaining):
    with patch('historical_training.resident_resource_guard.time.time',return_value=1000), \
         patch('historical_training.value_priority_admission.decision',
          return_value=(True,'VALUE_PRIORITY_FRESH_FLAT_ORIGINAL_REQUEST',{'reservation_expires_ms':1000000+remaining})):
     self.assertEqual(evaluator_idle_poll_seconds(Gate(),{}),expected)
 def test_missing_expiry_preserves_existing_bounded_poll(self):
  with patch('historical_training.value_priority_admission.decision',
    return_value=(True,'VALUE_PRIORITY_FRESH_FLAT_ORIGINAL_REQUEST',{})):
   self.assertEqual(evaluator_idle_poll_seconds(Gate(),{}),10)
 def test_reserved_idle_evaluator_uses_bounded_ten_seconds(self):
  g=Gate()
  with patch('historical_training.value_priority_admission.decision',
              return_value=(True,'VALUE_PRIORITY_DEFER_NEW_LONG_EVALUATION',{})) as check:
   self.assertEqual(evaluator_idle_poll_seconds(g,{}),10)
   self.assertEqual(check.call_count,1)
 def test_live_child_keeps_two_second_reap(self):
  g=Gate()
  with patch('historical_training.value_priority_admission.decision') as check:
   self.assertEqual(evaluator_idle_poll_seconds(g,{'child':object()}),2)
   check.assert_not_called()
 def test_unreserved_or_unverified_reverts_to_two_seconds(self):
  g=Gate()
  with patch('historical_training.value_priority_admission.decision',
              return_value=(False,'VALUE_PRIORITY_NO_ACTIVE_RESERVATION',{})):
   self.assertEqual(evaluator_idle_poll_seconds(g,{}),2)
  with patch('historical_training.value_priority_admission.decision',
              side_effect=ValueError('UNVERIFIED_SOURCE')):
   self.assertEqual(evaluator_idle_poll_seconds(g,{}),2)
 def test_no_priority_and_draining_keep_old_cadence(self):
  self.assertEqual(evaluator_idle_poll_seconds(Gate(enabled=False),{}),2)
  self.assertEqual(evaluator_idle_poll_seconds(Gate(draining=True),{}),2)


class ReservationRetryTests(unittest.TestCase):
 def plan(self,root):
  plan={'start_ms':1090000,'rule':{'slots':2,'slot_spacing_ms':360000}}
  plan['plan_id']=digest(plan);path=root/'plan.json';path.write_text(json.dumps(plan));path.chmod(0o600)
  return path
 def test_known_expiry_bounds_only_negative_cache_not_fixed_slot(self):
  with tempfile.TemporaryDirectory() as folder:
   path=self.plan(Path(folder));original=path.read_bytes()
   normal=_prediction_retry_window(path,0,4,1000000)
   early=_prediction_retry_window(path,0,4,1000000,reservation_expires_ms=1000500)
   self.assertEqual(normal['next_probe_at_ms'],1010000)
   self.assertEqual(early['next_probe_at_ms'],1000501)
   crossed=_prediction_retry_window(path,0,4,1000000,reservation_expires_ms=999999)
   self.assertEqual(crossed['next_probe_at_ms'],1000001)
   self.assertEqual(early['slot_start_ms'],normal['slot_start_ms'])
   self.assertEqual(path.read_bytes(),original)
   self.assertIsNone(_prediction_retry_window(path,0,4,1090000,reservation_expires_ms=1090500))
 def test_expiry_reenters_original_gate_and_does_not_launch_on_a_new_denial(self):
  class DenyingGate:
   draining=False
   def __init__(self):self.acquisitions=0
   def acquire(self,subject):
    self.acquisitions+=1
    self.last_admission={'reasons':['VALUE_PRIORITY_FRESH_FLAT_ORIGINAL_REQUEST'],
     'value_priority_evidence':{'reservation_expires_ms':1000500}}
    return False
  class Base:
   def __init__(self):self.tasks={}
   def launch(self,path,slot):raise AssertionError('EXPIRY_IS_NOT_ADMISSION')
  with tempfile.TemporaryDirectory() as folder:
   path=self.plan(Path(folder));gate=DenyingGate();service=guarded_evaluator_class(Base,gate)()
   with patch('historical_training.resident_resource_guard.time.time',return_value=1000), \
        patch('historical_training.resident_resource_guard.time.monotonic',return_value=100):
    service.launch(path,0)
   self.assertEqual(service._launch_retry['next_probe_at_ms'],1000501)
   with patch('historical_training.resident_resource_guard.time.time',return_value=1000.4), \
        patch('historical_training.resident_resource_guard.time.monotonic',return_value=100.4):
    service.launch(path,0)
   self.assertEqual(gate.acquisitions,1)
   with patch('historical_training.resident_resource_guard.time.time',return_value=1000.501), \
        patch('historical_training.resident_resource_guard.time.monotonic',return_value=100.501):
    service.launch(path,0)
   self.assertEqual(gate.acquisitions,2)
   self.assertFalse(service.tasks)
