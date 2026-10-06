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
        self.assertLessEqual(len(self.driver.calls),3);self.assertEqual(sum(self.clock.sleeps),3)
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
