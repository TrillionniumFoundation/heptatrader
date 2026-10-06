import unittest
from fixtures import *
from hepta_cost_observer import first_authoritative_quote,QuoteReadinessUnavailable
from hepta_cost_capture import ResetHold

class Clock:
    def __init__(self):self.t=0.;self.sleeps=[]
    def read(self):return self.t
    def sleep(self,n):self.sleeps.append(n);self.t+=n
class ReadOnly:
    def __init__(self,clock,steps):self.clock=clock;self.steps=list(steps);self.calls=[]
    def now_ms(self):return NOW+int(self.clock.t*1000)
    def native(self,cli,tool,*fields):
        self.calls.append((tool,fields));value=self.steps.pop(0) if self.steps else 'STALE'
        if isinstance(value,Exception):raise value
        if callable(value):return value(self)
        if isinstance(value,dict):return value
        if value=='STALE':raise RuntimeError('native rejected: AUTHORITATIVE_QUOTE_STALE')
        if value=='AUTH':raise RuntimeError('native rejected: SESSION_TOKEN_INVALID')
        if value=='BAD':return {**quote(self.now_ms()),'authoritative':False}
        return quote(self.now_ms())

class QuoteWarmupTests(unittest.TestCase):
    def get(self,steps,**kwargs):
        self.clock=Clock();self.driver=ReadOnly(self.clock,steps);self.guards=0
        def guard():self.guards+=1
        self.guard=guard
        return first_authoritative_quote(self.driver,'unused','BTC.USD',guard,
            monotonic=self.clock.read,sleep=self.clock.sleep,**kwargs)
    def test_first_success_has_no_delay(self):
        q,report=self.get(['OK'])
        self.assertEqual(report['attempts'],1);self.assertEqual(self.clock.sleeps,[])
        self.assertEqual(q['observed_at_ms'],NOW);self.assertEqual(self.guards,2)
    def test_stale_is_not_authorized_but_next_real_quote_is(self):
        q,report=self.get(['STALE','STALE','OK'])
        self.assertEqual(len(self.driver.calls),3);self.assertEqual(self.clock.sleeps,[2,2])
        self.assertEqual(q['observed_at_ms'],NOW+4000)
        self.assertEqual(len(report['stale_rejections']),2)
        self.assertFalse(report['source_clock_rewritten']);self.assertEqual(report['quote_ttl_ms'],5000)
    def test_permanent_identity_error_not_retried(self):
        with self.assertRaisesRegex(CostError,'FIRST_AUTHORITATIVE_QUOTE'):self.get(['AUTH','OK'])
        self.assertEqual(len(self.driver.calls),1);self.assertFalse(self.clock.sleeps)
    def test_false_authority_not_retried(self):
        with self.assertRaisesRegex(CostError,'FIRST_AUTHORITATIVE_QUOTE'):self.get(['BAD','OK'])
        self.assertEqual(len(self.driver.calls),1)
    def test_attempts_are_bounded_and_no_synthetic_fallback(self):
        with self.assertRaises(QuoteReadinessUnavailable):self.get(['STALE']*20,max_attempts=3)
        self.assertEqual(len(self.driver.calls),3);self.assertEqual(self.clock.sleeps,[2,2])
    def test_elapsed_budget_prevents_further_retry(self):
        with self.assertRaises(QuoteReadinessUnavailable):self.get(['STALE']*20,budget_sec=3,max_attempts=20)
        self.assertEqual(len(self.driver.calls),2);self.assertEqual(sum(self.clock.sleeps),3)
    def test_reset_during_warmup_precedes_next_io(self):
        clock=Clock();driver=ReadOnly(clock,['STALE','OK'])
        def guard():
            if clock.t>0:raise ResetHold('COST_RESET_HOLD_ACTIVE')
        with self.assertRaises(ResetHold):first_authoritative_quote(driver,'unused','BTC.USD',guard,monotonic=clock.read,sleep=clock.sleep)
        self.assertEqual(len(driver.calls),1)
    def test_reset_after_quote_prevents_capture(self):
        clock=Clock();driver=ReadOnly(clock,['OK']);checks=[]
        def guard():
            checks.append(1)
            if len(checks)>1:raise ResetHold('COST_RESET_HOLD_ACTIVE')
        with self.assertRaises(ResetHold):first_authoritative_quote(driver,'unused','BTC.USD',guard,monotonic=clock.read,sleep=clock.sleep)
        self.assertEqual(len(driver.calls),1)
    def test_only_quote_operation_ever_called(self):
        self.get(['STALE','OK'])
        self.assertTrue(all(tool=='market.get_quote' and fields==('instrument=BTC.USD',) for tool,fields in self.driver.calls))

    def test_event_service_not_ready_is_temporary_not_authority(self):
        q,report=self.get([RuntimeError('native rejected: EXECUTION_EVENT_SERVICE_NOT_READY'),'OK'])
        self.assertEqual(report['attempts'],2)
        self.assertEqual(self.clock.sleeps,[2])
        self.assertEqual(report['stale_rejections'],[])
        self.assertEqual(report['readiness_rejections'],[
            {'reason_code':'EXECUTION_EVENT_SERVICE_NOT_READY','observed_at_ms':NOW}])
        self.assertTrue(q['authoritative']);self.assertEqual(q['observed_at_ms'],NOW+2000)
    def test_execution_service_not_ready_is_temporary(self):
        _,report=self.get([RuntimeError('native rejected: EXECUTION_SERVICE_NOT_READY'),'OK'])
        self.assertEqual(report['attempts'],2)
        self.assertEqual(report['readiness_rejections'][0]['reason_code'],'EXECUTION_SERVICE_NOT_READY')
    def test_stale_and_readiness_diagnostics_are_not_conflated(self):
        _,report=self.get(['STALE',RuntimeError('native rejected: EXECUTION_EVENT_SERVICE_NOT_READY'),'OK'])
        self.assertEqual(report['attempts'],3)
        self.assertEqual([r['reason_code'] for r in report['stale_rejections']],['AUTHORITATIVE_QUOTE_STALE'])
        self.assertEqual([r['reason_code'] for r in report['readiness_rejections']],['EXECUTION_EVENT_SERVICE_NOT_READY'])
        self.assertEqual(self.clock.sleeps,[2,2])
    def test_readiness_exhaustion_preserves_temporary_failure_class(self):
        errors=[RuntimeError('native rejected: EXECUTION_EVENT_SERVICE_NOT_READY')]*5
        with self.assertRaisesRegex(QuoteReadinessUnavailable,'RETRY_EXHAUSTED'):
            self.get(errors,max_attempts=3)
        self.assertEqual(len(self.driver.calls),3)
        self.assertEqual(self.clock.sleeps,[2,2])
    def test_no_new_read_at_exact_deadline_even_when_next_step_is_good(self):
        with self.assertRaises(QuoteReadinessUnavailable):
            self.get(['STALE','OK'],retry_sec=2,budget_sec=2)
        self.assertEqual(len(self.driver.calls),1)
        self.assertEqual(self.clock.sleeps,[2])
    def test_slow_failed_read_cannot_start_another_after_budget(self):
        def slow(driver):
            driver.clock.t+=2.5
            raise RuntimeError('native rejected: AUTHORITATIVE_QUOTE_STALE')
        with self.assertRaises(QuoteReadinessUnavailable):self.get([slow,'OK'],budget_sec=3)
        self.assertEqual(len(self.driver.calls),1)
        self.assertEqual(self.clock.sleeps,[.5])
    def test_inflight_success_may_finish_after_budget_but_must_be_fresh(self):
        def slow(driver):
            driver.clock.t+=4
            return quote(driver.now_ms())
        q,report=self.get([slow],budget_sec=3)
        self.assertEqual(report['attempts'],1);self.assertFalse(self.clock.sleeps)
        self.assertEqual(q['observed_at_ms'],NOW+4000)
    def test_readiness_reason_without_native_rejection_prefix_is_not_retryable(self):
        with self.assertRaises(CostError) as caught:
            self.get([RuntimeError('EXECUTION_EVENT_SERVICE_NOT_READY'),'OK'])
        self.assertNotIsInstance(caught.exception,QuoteReadinessUnavailable)
        self.assertEqual(len(self.driver.calls),1);self.assertFalse(self.clock.sleeps)
    def test_identity_fence_path_and_unknown_failures_still_stop_immediately(self):
        for reason in ('SESSION_NOT_FOUND','SESSION_TOKEN_INVALID','SESSION_OWNER_FENCED',
                       'OBSERVATION_CYCLE_PATH_MISMATCH','OBSERVATION_RESET_CYCLE_CHANGED',
                       'EXECUTION_SERVICE_EPOCH_CHANGED','EXECUTION_EVENT_GATEWAY_CONTEXT_BINDING_MISMATCH',
                       'DISCOVERY_LIST_REJECTED','EXECUTION_EVENT_RESPONSE_READ_FAILED','UNKNOWN'):
            with self.subTest(reason=reason):
                with self.assertRaises(CostError) as caught:
                    self.get([RuntimeError('native rejected: '+reason),'OK'])
                self.assertNotIsInstance(caught.exception,QuoteReadinessUnavailable)
                self.assertEqual(len(self.driver.calls),1);self.assertFalse(self.clock.sleeps)
    def test_readiness_reason_prefix_or_suffix_match_is_not_enough(self):
        for reason in ('native rejected: EXECUTION_EVENT_SERVICE_NOT_READY:other',
                       'detail native rejected: EXECUTION_EVENT_SERVICE_NOT_READY',
                       'native rejected: EXECUTION_EVENT_SERVICE_NOT_READY\n'):
            with self.subTest(reason=reason),self.assertRaises(CostError) as caught:
                self.get([RuntimeError(reason),'OK'])
            self.assertNotIsInstance(caught.exception,QuoteReadinessUnavailable)
            self.assertEqual(len(self.driver.calls),1);self.assertFalse(self.clock.sleeps)
    def test_readiness_recovery_does_not_accept_non_authoritative_quote(self):
        with self.assertRaises(CostError) as caught:
            self.get([RuntimeError('native rejected: EXECUTION_SERVICE_NOT_READY'),'BAD','OK'])
        self.assertNotIsInstance(caught.exception,QuoteReadinessUnavailable)
        self.assertEqual(len(self.driver.calls),2);self.assertEqual(self.clock.sleeps,[2])
    def test_reset_during_readiness_wait_precedes_next_native_read(self):
        clock=Clock();driver=ReadOnly(clock,[RuntimeError('native rejected: EXECUTION_SERVICE_NOT_READY'),'OK'])
        def guard():
            if clock.t:raise ResetHold('COST_RESET_HOLD_ACTIVE')
        with self.assertRaises(ResetHold):
            first_authoritative_quote(driver,'unused','BTC.USD',guard,monotonic=clock.read,sleep=clock.sleep)
        self.assertEqual(len(driver.calls),1)
    def test_guard_consuming_budget_cannot_dispatch_a_read(self):
        clock=Clock();driver=ReadOnly(clock,['OK'])
        def guard():clock.t+=3
        with self.assertRaises(QuoteReadinessUnavailable):
            first_authoritative_quote(driver,'unused','BTC.USD',guard,monotonic=clock.read,sleep=clock.sleep,budget_sec=2)
        self.assertEqual(driver.calls,[])
    def test_retry_still_calls_only_the_original_instrument_quote(self):
        self.get([RuntimeError('native rejected: EXECUTION_EVENT_SERVICE_NOT_READY'),'STALE','OK'])
        self.assertEqual(self.driver.calls,[('market.get_quote',('instrument=BTC.USD',))]*3)
