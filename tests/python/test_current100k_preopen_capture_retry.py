"""New source-race regressions, using synthetic journals and clocks only."""
import sys,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'research'),str(ROOT/'research/neuron_learning'),str(Path(__file__).parent)]
import test_decision_attribution as provenance
import test_current100k_preopen as fixtures
from laya_cell import current100k_preopen as M
from laya_cell.financial_forward_source import _ingress

class CaptureRetryTests(unittest.TestCase):
 def source(self,**kw):
  self.clock=fixtures.Clock()
  return M.ResidentIngress({'manifest':'/unused','sha256':'3'*64},'2'*64,
   clock=self.clock.wall,monotonic_ns=self.clock.ns,sleep=self.clock.advance,**kw)
 def test_real_append_between_read_and_validation_gets_complete_retry(self):
  f=provenance.ProvenanceTests('runTest');f.setUp();self.addCleanup(f.doCleanups)
  f.trade();f.journal.q.join();clock=fixtures.Clock()
  source=M.ResidentIngress(f.reg,'1'*64,clock=clock.wall,monotonic_ns=clock.ns,sleep=clock.advance)
  calls=[]
  def append_after_read(*args,**kw):
   value=_ingress(*args,**kw);calls.append(1)
   if len(calls)==1:f.decision();f.journal.q.join()
   return value
  with patch.object(M,'_ingress',side_effect=append_after_read):value=source.read()
  self.assertEqual(len(calls),2);self.assertEqual(len(value[1]),2)
  self.assertEqual(value,_ingress(f.reg,'1'*64))
  self.assertEqual(source.statistics()['source_capture_conflicts'],1)
  self.assertEqual(source.statistics()['source_capture_retries'],1)
 def test_exhaustion_never_returns_old_or_unverified_snapshot(self):
  source=self.source(max_capture_attempts=3)
  source.identity={'last':'verified'};source.value=({}, {'old':'value'}, [], [])
  with patch.object(source,'_capture_once',side_effect=ValueError('ATTRIBUTION_SOURCE_CHANGED_DURING_READ')) as call:
   with self.assertRaises(M.SourceSnapshotBusy):source.read()
  self.assertEqual(call.call_count,3);self.assertEqual(self.clock.seconds(),.02)
  self.assertEqual(source.identity,{'last':'verified'});self.assertEqual(source.value[1],{'old':'value'})
 def test_retry_budget_bounds_additional_attempts(self):
  source=self.source(max_capture_attempts=5,retry_budget_ms=15,retry_delay_ms=10)
  with patch.object(source,'_capture_once',side_effect=M.ContractError('PREOPEN_SOURCE_CHANGED_DURING_CAPTURE')) as call:
   with self.assertRaises(M.SourceSnapshotBusy):source.read()
  self.assertEqual(call.call_count,2);self.assertLess(self.clock.seconds(),.015)
 def test_original_deadline_prevents_an_additional_retry(self):
  source=self.source()
  def conflict():
   self.clock.advance(.004);raise ValueError('ATTRIBUTION_SOURCE_CHANGED_DURING_READ')
  with patch.object(source,'_capture_once',side_effect=conflict) as call:
   with self.assertRaises(M.SourceSnapshotBusy):source.read(retry_deadline_ms=10005)
  self.assertEqual(call.call_count,1);self.assertEqual(self.clock.now,10004)
 def test_backward_wall_clock_cannot_renew_budget(self):
  source=self.source()
  def conflict():
   self.clock.tick+=20000000;self.clock.now-=100
   raise ValueError('ATTRIBUTION_SOURCE_CHANGED_DURING_READ')
  with patch.object(source,'_capture_once',side_effect=conflict) as call:
   with self.assertRaises(M.SourceSnapshotBusy):source.read(retry_deadline_ms=10005)
  self.assertEqual(call.call_count,1)
 def test_hash_and_producer_errors_are_not_transient(self):
  for reason in ('ATTRIBUTION_HASH','ATTRIBUTION_CACHED_PREFIX_CHANGED','REGISTERED_PRODUCER_CHANGED'):
   with self.subTest(reason=reason):
    source=self.source()
    with patch.object(source,'_capture_once',side_effect=ValueError(reason)) as call:
     with self.assertRaisesRegex(ValueError,reason):source.read()
    self.assertEqual(call.call_count,1);self.assertEqual(self.clock.tick,0)

class BusyResidentTests(unittest.TestCase):
 def fixture(self):
  f=fixtures.LedgerTests('runTest');f.setUp();self.addCleanup(f.doCleanups);return f
 def test_busy_recovery_keeps_one_warm_model_without_stale_prediction(self):
  f=self.fixture();item=f.item()
  class Source:
   calls=0
   def read(self):
    self.calls+=1
    if self.calls==3:
     assert not f.ledger.known
     raise M.SourceSnapshotBusy('ATTRIBUTION_SOURCE_CHANGED_DURING_READ',3)
    return {},({item['request']['request_id']:item} if self.calls>=4 else {}),[],[]
   def statistics(self):return {}
  gate=fixtures.Gate();result=f.runner(Source(),gate)
  self.assertEqual(result['model_loads'],1);self.assertEqual(result['request_attempts'],1)
  self.assertEqual(result['source_busy_observations'],1);self.assertEqual(result['source_error_observations'],0)
  self.assertEqual(gate.events,['acquire','warm','close','release'])
 def test_busy_does_not_starve_original_guard_checks(self):
  f=self.fixture()
  class Source:
   calls=0
   def read(self):
    self.calls+=1
    if self.calls>2:raise M.SourceSnapshotBusy('PREOPEN_SOURCE_CHANGED_DURING_CAPTURE',3)
    return {},{},[],[]
   def statistics(self):return {}
  gate=fixtures.Gate(continuation=False)
  result=f.runner(Source(),gate,max_seconds=3,guard_check_seconds=1)
  self.assertEqual(result['reason'],'GUARD_WITHDREW:MANUAL_HOLD')
  self.assertLess(result['resident_seconds'],1.5);self.assertEqual(gate.events[-2:],['close','release'])
 def test_persistent_busy_still_respects_fixed_input_end(self):
  f=self.fixture();f.ledger.protocol["input_end_ms"]=f.clock.now+750
  class Source:
   calls=0
   def read(self):
    self.calls+=1
    if self.calls>2:raise M.SourceSnapshotBusy("PREOPEN_SOURCE_CHANGED_DURING_CAPTURE",3)
    return {},{},[],[]
   def statistics(self):return {}
  gate=fixtures.Gate();result=f.runner(Source(),gate,max_seconds=3)
  self.assertEqual(result["reason"],"FIXED_INPUT_WINDOW_CLOSED")
  self.assertEqual(result["source_error_observations"],0)
  self.assertLess(result["resident_seconds"],1);self.assertEqual(gate.events[-2:],["close","release"])
 def test_known_deadline_propagates_and_expires_during_busy_wait(self):
  f=self.fixture();item=f.item();seen=[]
  class Source:
   calls=0
   def read_with_deadline(self,deadline):
    self.calls+=1;seen.append(deadline)
    if self.calls==1:return {},{item['request']['request_id']:item},[],[]
    if self.calls==2:f.clock.advance(59.5)
    raise M.SourceSnapshotBusy('PREOPEN_SOURCE_CHANGED_DURING_CAPTURE',3)
   def statistics(self):return {}
  result=f.runner(Source(),fixtures.Gate(admit=False),max_seconds=61)
  self.assertIn(70000,seen);self.assertTrue(all(x in (None,70000) for x in seen))
  self.assertEqual(f.ledger.terminal(item['request']['request_id'])['status'],'REQUEST_EXPIRED_NO_BACKDATING')
  self.assertEqual(result['request_attempts'],0);self.assertLess(len(seen),12)
 def test_true_hash_or_producer_failure_still_closes_resident(self):
  for reason in ('ATTRIBUTION_HASH','REGISTERED_PRODUCER_CHANGED'):
   with self.subTest(reason=reason):
    f=self.fixture()
    class Source:
     calls=0
     def read(self):
      self.calls+=1
      if self.calls>2:raise ValueError(reason)
      return {},{},[],[]
     def statistics(self):return {}
    gate=fixtures.Gate();result=f.runner(Source(),gate,max_seconds=3)
    self.assertEqual(result['reason'],'SOURCE_OR_RESIDENT_FAILURE_HANDOFF')
    self.assertEqual(result['source_busy_observations'],0);self.assertEqual(gate.events[-2:],['close','release'])
 def test_actual_health_violation_still_hands_off(self):
  f=self.fixture()
  class Source:
   calls=0
   def read(self):
    self.calls+=1;return {},{},[],([{'reason':'PRODUCER_DROPPED'}] if self.calls>2 else [])
   def statistics(self):return {}
  gate=fixtures.Gate();result=f.runner(Source(),gate,max_seconds=3)
  self.assertEqual(result['reason'],'SOURCE_CHANGED_HANDOFF')
  self.assertEqual(result['source_busy_observations'],0);self.assertEqual(gate.events[-2:],['close','release'])
if __name__=='__main__':unittest.main()
