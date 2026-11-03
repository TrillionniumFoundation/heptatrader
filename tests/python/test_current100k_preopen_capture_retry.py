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
  self.assertEqual(gate.events,['acquire','warm','check','close','release'])
 def test_busy_does_not_starve_original_guard_checks(self):
  f=self.fixture()
  class Source:
   calls=0
   def read(self):
    self.calls+=1
    if self.calls>2:raise M.SourceSnapshotBusy('PREOPEN_SOURCE_CHANGED_DURING_CAPTURE',3)
    return {},{},[],[]
   def statistics(self):return {}
  gate=fixtures.Gate();original_check=gate.check
  def check():
   report=original_check();report['admit_new_worker']=f.clock.seconds()<1
   report['reasons']=[] if f.clock.seconds()<1 else ['MANUAL_HOLD'];return report
  gate.check=check
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

class StaleQuoteHoldTests(unittest.TestCase):
 """Existing warm lease only; real ledger files with fake source/gate/clocks."""
 def fixture(self):
  f=fixtures.LedgerTests('runTest');f.setUp();self.addCleanup(f.doCleanups)
  f.items=[];f.bindings=[];f.source_script=None;f.forward_times=[];f.forward_ids=[]
  f.events=[];f.origin=f.clock.seconds();f.gate_script=None;f.warm_seconds=0
  return f
 def report(self,f,mode='fresh',*,age=None):
  now=f.clock.now;age=(6000 if mode=='stale' else 1000) if age is None else age
  return {'schema':'heptatrader.resident-resource-admission.v1','role':'value-predictor',
   'admit_new_worker':mode=='fresh','reasons':[] if mode=='fresh' else ['LATEST_SOURCE_QUOTE_NOT_FRESH'],
   'existing_workers_interrupted':False,'at_ms':now,
   'original_live_source_evidence':{'schema':'heptatrader.original-live-freshness.v1',
    'continuity_at_ms':now-500,'continuity_prefix_digest':'a'*64,
    'pinned_prefix':{'path':'/synthetic/original/quotes.jsonl','bytes':100,'sha256':'b'*64},
    'verified_live_prefix':{'path':'/synthetic/original/quotes.jsonl','bytes':200,'sha256':'c'*64},
    'quote_observed_at_ms':now-age,'quote_known_at_ms':now-100,
    'quote_receipt':{'synthetic_original_quote':True},'quote_timestamp_rewritten':False,
    'source_ttl_ms':5000,'cached_continuity_not_efficacy':True,'broker_queries_sent':0}}
 def run_case(self,f,*,max_seconds=6,admit=True,stop=lambda:False,yield_requested=lambda:False):
  owner=self;f.origin=f.clock.seconds()
  class Guard:
   checks=0;held=False
   def acquire(inner,subject):
    f.events.append('acquire');inner.held=admit;return admit
   def check(inner):
    inner.checks+=1;f.events.append('check')
    value=f.gate_script(inner.checks,f.clock.seconds()-f.origin) if f.gate_script else 'fresh'
    if isinstance(value,BaseException):raise value
    return owner.report(f,value) if isinstance(value,str) else value
   def release(inner):
    owner.assertTrue(inner.held);f.events.append('release');inner.held=False
  class Source:
   calls=0
   def read(inner):
    inner.calls+=1
    if f.source_script is not None:return f.source_script(inner.calls,f.clock.seconds()-f.origin)
    return {},{x['request']['request_id']:x for x in f.items},f.bindings,[]
   def statistics(inner):return {'captures':inner.calls}
  f.gate=Guard();f.source=Source()
  def warm():f.events.append('warm');f.clock.advance(f.warm_seconds)
  def predict(item):
   self.assertTrue(f.gate.held)
   entries=[p for p in f.ledger.root.glob('quote-hold-*.json') if not p.name.startswith('quote-hold-end-')]
   self.assertEqual(len(entries),len(list(f.ledger.root.glob('quote-hold-end-*.json'))))
   f.forward_times.append(f.clock.seconds()-f.origin);f.forward_ids.append(item['request']['request_id'])
   return f.publish(item)
  def close():f.events.append('close');return {'synthetic_cleanup_verified':True}
  return M.run_resident(f.source,f.ledger,warm,predict,close,f.gate,
   max_seconds=max_seconds,poll_seconds=.25,guard_check_seconds=5,
   sleep=f.clock.advance,monotonic=f.clock.seconds,stop=stop,yield_requested=yield_requested)
 def holds(self,f):
  entries=[M.load(p) for p in f.ledger.root.glob('quote-hold-*.json') if not p.name.startswith('quote-hold-end-')]
  ends=[M.load(p) for p in f.ledger.root.glob('quote-hold-end-*.json')]
  return sorted(entries,key=lambda x:x['started_at_ms']),sorted(ends,key=lambda x:x['ended_at_ms'])
 def assert_clean(self,f):
  self.assertEqual(f.events.count('acquire'),1);self.assertEqual(f.events.count('warm'),1)
  self.assertEqual(f.events[-2:],['close','release']);self.assertFalse(f.gate.held)
  reservations=[p for p in f.ledger.root.glob('lease-*.json') if not p.name.startswith('lease-close-')]
  self.assertEqual(len(reservations),1);self.assertEqual(len(list(f.ledger.root.glob('lease-close-*.json'))),1)
  lease=M.load(reservations[0])['lease_id'];entries,ends=self.holds(f)
  self.assertEqual(len(entries),len(ends))
  for entry,end in zip(entries,ends):
   self.assertEqual(entry['lease_id'],lease);self.assertEqual(end['lease_id'],lease)
   self.assertEqual(entry['hold_id'],end['hold_id']);self.assertFalse(entry['inference_ready'])
   self.assertFalse(end['new_lease_acquired']);self.assertFalse(end['request_deadlines_renewed'])
   self.assertEqual(end['request_attempts_during_hold'],0)
 def test_stale_hold_never_predicts_and_fresh_resume_uses_same_lease(self):
  f=self.fixture();item=f.item();f.items=[item]
  original=M.read_private(f.ledger.root/'protocol.json')
  f.gate_script=lambda n,t:'stale' if t<5 else 'fresh'
  result=self.run_case(f)
  self.assertEqual(result['request_attempts'],1);self.assertGreaterEqual(f.forward_times[0],5)
  self.assertEqual(result['quote_hold_count'],1);self.assertEqual(result['quote_hold_resumptions'],1)
  self.assertEqual(result['quote_hold_seconds'],5)
  self.assertEqual(f.gate.checks,2)
  self.assertEqual(M.read_private(f.ledger.root/'protocol.json'),original)
  attempt=M.load(f.ledger.folder(item['request']['request_id'])/'attempt.json')
  self.assertEqual(attempt['deadline_ms'],item['request']['started_at_ms']+60000)
  self.assert_clean(f)
 def test_persistent_stale_hold_exits_at_fifteen_seconds_without_forward(self):
  f=self.fixture();f.items=[f.item()];f.gate_script=lambda n,t:'stale'
  result=self.run_case(f,max_seconds=60)
  self.assertEqual(result['reason'],'STALE_QUOTE_WARM_HOLD_BOUND')
  self.assertEqual(result['resident_seconds'],15);self.assertEqual(result['quote_hold_seconds'],15)
  self.assertEqual(f.gate.checks,3);self.assertEqual(f.forward_times,[])
  self.assertEqual(list(f.ledger.requests.glob('*/attempt.json')),[]);self.assert_clean(f)
 def test_fresh_intervals_do_not_reset_cumulative_hold_budget(self):
  f=self.fixture();f.gate_script=lambda n,t:'fresh' if 5<=t<10 or 15<=t<20 else 'stale'
  result=self.run_case(f,max_seconds=60)
  self.assertEqual(result['reason'],'STALE_QUOTE_WARM_HOLD_BOUND')
  self.assertGreater(result['resident_seconds'],15);self.assertLess(result['resident_seconds'],60)
  self.assertEqual(result['quote_hold_seconds'],15)
  self.assertEqual(result['quote_hold_count'],3);self.assertEqual(result['quote_hold_resumptions'],2)
  self.assert_clean(f)
 def test_future_quote_same_reason_never_receives_hold_exception(self):
  f=self.fixture();f.items=[f.item()]
  def check(n,t):
   report=self.report(f,'stale');p=report['original_live_source_evidence']
   p['quote_observed_at_ms']=f.clock.now+1;p['quote_known_at_ms']=f.clock.now+1;return report
  f.gate_script=check;result=self.run_case(f)
  self.assertEqual(result['quote_hold_count'],0);self.assertEqual(f.forward_times,[])
  self.assertEqual(result['reason'],'GUARD_WITHDREW:LATEST_SOURCE_QUOTE_NOT_FRESH');self.assert_clean(f)
 def test_missing_or_unbound_causal_proof_is_not_a_pause(self):
  variants=('missing','future_known','future_report','old_report','bad_prefix','rewritten','bad_continuity')
  for variant in variants:
   with self.subTest(variant=variant):
    f=self.fixture();f.items=[f.item()]
    def check(n,t):
     report=self.report(f,'stale');p=report['original_live_source_evidence']
     if variant=='missing':del report['original_live_source_evidence']
     elif variant=='future_known':p['quote_known_at_ms']=f.clock.now+1
     elif variant=='future_report':report['at_ms']=f.clock.now+1
     elif variant=='old_report':report['at_ms']=f.clock.now-1
     elif variant=='bad_prefix':p['verified_live_prefix']['path']='/other/quotes.jsonl'
     elif variant=='rewritten':p['quote_timestamp_rewritten']=True
     else:p['continuity_at_ms']=f.clock.now+1
     return report
    f.gate_script=check;result=self.run_case(f)
    self.assertEqual(result['quote_hold_count'],0);self.assertEqual(f.forward_times,[]);self.assert_clean(f)
 def test_any_mixed_resource_or_source_reason_hands_off_immediately(self):
  for extra in ('HOST_FULL_MEMORY_PSI_PRESSURE','HOST_AVAILABLE_MEMORY_BELOW_8GIB',
                'OPERATOR_RESOURCE_HOLD','SOURCE_CONTINUITY_NOT_READY','SOURCE_PERIOD_OR_SCHEMA_UNBOUND',
                'OTHER_RESEARCH_WORKER_ACTIVE','SUPERVISOR_DRAIN_REQUESTED'):
   with self.subTest(extra=extra):
    f=self.fixture();f.items=[f.item()]
    def check(n,t):
     report=self.report(f,'stale');report['reasons'].append(extra);return report
    f.gate_script=check;result=self.run_case(f)
    self.assertEqual(result['quote_hold_count'],0);self.assertIn(extra,result['reason'])
    self.assertEqual(f.forward_times,[]);self.assert_clean(f)
 def test_open_during_hold_is_recorded_and_never_backfilled(self):
  f=self.fixture();item=f.item();f.items=[item];binding=f.binding(item,created=f.clock.now+1000)
  def source(n,t):return {},{item['request']['request_id']:item},[binding] if t>=1 else [],[]
  f.source_script=source;f.gate_script=lambda n,t:'stale' if t<5 else 'fresh'
  result=self.run_case(f)
  self.assertEqual(f.forward_times,[]);self.assertEqual(result['request_attempts'],0)
  self.assertEqual(f.ledger.terminal(item['request']['request_id'])['status'],'NO_PREDICTION_BEFORE_ORIGINAL_COMMAND')
  self.assertEqual(M.load(f.ledger.bindings/'original-1.json')['status'],'MISSING_OR_LATE_NO_CREDIT')
  self.assertGreater(f.source.calls,10);self.assert_clean(f)
 def test_request_expiry_continues_during_hold_without_renewal(self):
  f=self.fixture();f.clock.advance(60);item=f.item(started=f.clock.now-59000);f.items=[item]
  f.gate_script=lambda n,t:'stale' if t<5 else 'fresh';result=self.run_case(f)
  terminal=f.ledger.terminal(item['request']['request_id'])
  self.assertEqual(terminal['status'],'REQUEST_EXPIRED_NO_BACKDATING')
  self.assertEqual(terminal['deadline_ms'],item['request']['started_at_ms']+60000)
  self.assertEqual(result['request_attempts'],0);self.assertEqual(f.forward_times,[]);self.assert_clean(f)
 def test_busy_snapshot_keeps_hold_but_fresh_guard_needs_current_capture(self):
  f=self.fixture();item=f.item();f.items=[item]
  def source(n,t):
   if 5<=t<5.5:raise M.SourceSnapshotBusy('ATTRIBUTION_SOURCE_CHANGED_DURING_READ',3)
   return {},{item['request']['request_id']:item},[],[]
  f.source_script=source;f.gate_script=lambda n,t:'stale' if t<5 else 'fresh';result=self.run_case(f)
  self.assertEqual(result['source_busy_observations'],2);self.assertEqual(result['quote_hold_count'],1)
  self.assertEqual(f.gate.checks,3);self.assertGreaterEqual(f.forward_times[0],5.5)
  self.assertEqual(result['quote_hold_seconds'],5.5);self.assert_clean(f)
 def test_source_corruption_or_provenance_denial_during_hold_closes_normally(self):
  for mode in ('hash','issues'):
   with self.subTest(mode=mode):
    f=self.fixture();f.items=[f.item()];f.gate_script=lambda n,t:'stale'
    def source(n,t):
     if t>=.25:
      if mode=='hash':raise ValueError('ATTRIBUTION_HASH')
      return {},{},[],[{'reason':'REGISTERED_PRODUCER_CHANGED'}]
     return {},{x['request']['request_id']:x for x in f.items},[],[]
    f.source_script=source;result=self.run_case(f)
    self.assertEqual(result['reason'],'SOURCE_OR_RESIDENT_FAILURE_HANDOFF' if mode=='hash' else 'SOURCE_CHANGED_HANDOFF')
    self.assertEqual(f.forward_times,[]);self.assert_clean(f)
 def test_warm_longer_than_original_ttl_requires_new_guard_before_first_forward(self):
  f=self.fixture();f.items=[f.item()];f.warm_seconds=9
  f.gate_script=lambda n,t:'stale' if t<14 else 'fresh';result=self.run_case(f,max_seconds=15)
  self.assertGreaterEqual(f.forward_times[0],14);self.assertEqual(result['quote_hold_seconds'],5)
  self.assertEqual(f.events[:3],['acquire','warm','check']);self.assert_clean(f)
 def test_long_capture_or_queue_invalidates_previous_positive_quote_proof(self):
  f=self.fixture();f.items=[f.item(1),f.item(2)]
  def source(n,t):
   if n==3:f.clock.advance(4.1)
   return {},{x['request']['request_id']:x for x in f.items},[],[]
  f.source_script=source;f.gate_script=lambda n,t:'stale' if n==2 else 'fresh'
  result=self.run_case(f,max_seconds=10)
  self.assertEqual(len(f.forward_times),2);self.assertLess(f.forward_times[0],.1)
  self.assertGreaterEqual(f.forward_times[1],9.1);self.assertEqual(result['quote_hold_count'],1)
  self.assert_clean(f)
 def test_fresh_check_crossing_hold_deadline_never_allows_one_last_forward(self):
  f=self.fixture();f.items=[f.item()]
  def check(n,t):
   if n==1:return 'stale'
   f.clock.advance(11);return 'fresh'
  f.gate_script=check;result=self.run_case(f,max_seconds=60)
  self.assertEqual(result['reason'],'STALE_QUOTE_WARM_HOLD_BOUND')
  self.assertEqual(result['quote_hold_seconds'],16);self.assertEqual(result['quote_hold_resumptions'],0)
  self.assertEqual(f.forward_times,[]);self.assert_clean(f)
 def test_original_stop_yield_session_and_input_bounds_win_during_hold(self):
  for mode in ('stop','yield','session','input'):
   with self.subTest(mode=mode):
    f=self.fixture();f.items=[f.item()];f.gate_script=lambda n,t:'stale'
    if mode=='input':f.ledger.protocol['input_end_ms']=f.clock.now+750
    result=self.run_case(f,max_seconds=1 if mode=='session' else 6,
     stop=(lambda:f.clock.seconds()-f.origin>=.25) if mode=='stop' else lambda:False,
     yield_requested=(lambda:f.clock.seconds()-f.origin>=.25) if mode=='yield' else lambda:False)
    expected={'stop':'SUPERVISOR_STOP','yield':'OTHER_REGISTERED_WORKER_WAITING',
              'session':'BOUNDED_SESSION_COMPLETE','input':'FIXED_INPUT_WINDOW_CLOSED'}[mode]
    self.assertEqual(result['reason'],expected);self.assertLessEqual(result['resident_seconds'],1)
    self.assertEqual(f.forward_times,[]);self.assert_clean(f)
 def test_new_resource_denial_after_hold_is_never_treated_as_recovery(self):
  f=self.fixture();f.items=[f.item()]
  def check(n,t):
   report=self.report(f,'stale')
   if n>1:report['reasons'].append('HOST_FULL_MEMORY_PSI_PRESSURE')
   return report
  f.gate_script=check;result=self.run_case(f)
  self.assertIn('HOST_FULL_MEMORY_PSI_PRESSURE',result['reason'])
  self.assertEqual(result['quote_hold_resumptions'],0);self.assertEqual(f.forward_times,[]);self.assert_clean(f)
 def test_recovery_check_exception_is_visible_and_releases_the_original_lease(self):
  f=self.fixture();f.items=[f.item()]
  f.gate_script=lambda n,t:'stale' if n==1 else OSError('fresh guard unreadable')
  result=self.run_case(f)
  self.assertEqual(result['reason'],'SOURCE_OR_RESIDENT_FAILURE_HANDOFF')
  self.assertEqual(result['source_error_observations'],1);self.assertEqual(f.forward_times,[])
  self.assertTrue(any('fresh guard unreadable' in M.load(p)['reason'] for p in f.ledger.incidents.glob('*.json')))
  self.assert_clean(f)
 def test_guard_exception_while_source_busy_cleans_up_with_visible_failure(self):
  f=self.fixture();f.items=[f.item()]
  def source(n,t):
   if t>=5:raise M.SourceSnapshotBusy('ATTRIBUTION_SOURCE_CHANGED_DURING_READ',3)
   return {},{x['request']['request_id']:x for x in f.items},[],[]
  f.source_script=source
  f.gate_script=lambda n,t:'stale' if n==1 else OSError('guard failed during busy capture')
  result=self.run_case(f)
  self.assertEqual(result['reason'],'SOURCE_OR_RESIDENT_FAILURE_HANDOFF')
  self.assertEqual(result['source_error_observations'],1);self.assertEqual(f.forward_times,[])
  self.assertTrue(any('guard failed during busy capture' in M.load(p)['reason'] for p in f.ledger.incidents.glob('*.json')))
  self.assert_clean(f)
 def test_positive_decision_without_complete_proof_cannot_authorize_forward(self):
  f=self.fixture();f.items=[f.item()]
  f.gate_script=lambda n,t:{'admit_new_worker':True,'reasons':[]}
  result=self.run_case(f)
  self.assertEqual(result['reason'],'GUARD_WITHDREW:CURRENT_QUOTE_PROOF_INVALID_OR_EXPIRED')
  self.assertEqual(result['quote_hold_count'],0);self.assertEqual(f.forward_times,[]);self.assert_clean(f)
 def test_pause_never_creates_a_new_admission(self):
  f=self.fixture();f.items=[f.item()];f.gate_script=lambda n,t:'stale'
  result=self.run_case(f,admit=False)
  self.assertEqual(result['quote_hold_count'],0);self.assertEqual(result['model_loads'],0)
  self.assertEqual(f.forward_times,[]);self.assertNotIn('warm',f.events);self.assertNotIn('release',f.events)
 def test_three_existing_failed_terminals_and_command_bindings_are_unchanged(self):
  f=self.fixture()
  for n in (1,2,3):
   item=f.item(n);f.items.append(item);f.ledger.observe(item);f.clock.advance(.02)
   binding=f.binding(item,command='old-'+str(n));f.bindings.append(binding);f.ledger.reconcile(binding)
  saved={p:p.read_bytes() for p in f.ledger.requests.glob('*/terminal.json')}
  saved.update({p:p.read_bytes() for p in f.ledger.bindings.glob('*.json')})
  f.items.append(f.item(4));f.gate_script=lambda n,t:'stale' if t<5 else 'fresh'
  self.run_case(f)
  self.assertEqual(f.forward_ids,[f'{4:032x}'])
  for path,raw in saved.items():self.assertEqual(path.read_bytes(),raw)
  self.assertEqual(len(list(f.ledger.bindings.glob('*.json'))),3);self.assert_clean(f)

if __name__=='__main__':unittest.main()
