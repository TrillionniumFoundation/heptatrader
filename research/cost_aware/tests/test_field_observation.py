import unittest
from pathlib import Path
from fixtures import *
from hepta_cost_observer import Sampler
from hepta_cost_replay import candidate_outcomes
from test_observer import Fake, Writer
from test_capture_replay import small_tape

class FieldObservationTests(unittest.TestCase):
    def test_matured_missing_quote_is_not_marked_unmatured(self):
        tape=small_tape();tape['rows']=[x for x in tape['rows'] if x['at_ms']!=NOW+300000]
        self.assertEqual(candidate_outcomes(tape)[0]['reason'],'OUTCOME_WINDOW_MISSING_QUOTE')
    def test_future_cutoff_does_not_make_a_young_window_mature(self):
        tape=small_tape();tape['rows']=tape['rows'][:2]
        self.assertEqual(candidate_outcomes(tape,cutoff_ms=NOW+86400000)[0]['reason'],'OUTCOME_NOT_YET_OBSERVED')
    def test_sampling_reason_retains_only_a_safe_native_code(self):
        f=Fake();w=Writer()
        def rejected(*_):raise RuntimeError('native rejected: AUTHORITATIVE_QUOTE_STALE')
        f.native=rejected;sampler=Sampler(f,'cli',Path('/unused'),w);sampler.poll(NOW)
        self.assertEqual(w.events[0][1]['reason_code'],'AUTHORITATIVE_QUOTE_STALE')
    def test_arbitrary_exception_detail_never_enters_capture(self):
        f=Fake();w=Writer()
        def rejected(*_):raise OSError('credential-like detail must not be retained')
        f.native=rejected;Sampler(f,'cli',Path('/unused'),w).poll(NOW)
        self.assertEqual(w.events[0][1]['reason_code'],'OSError')
        self.assertNotIn('credential-like',str(w.events))

from hepta_cost_replay import capture_to_tape

class CausalQuoteAlignmentTests(unittest.TestCase):
    def capture(self,next_quote_at=NOW+10000,candidate_at=NOW+6000):
        events=[{'event':'HEADER','at_ms':1,'data':{'instrument':'BTC.USD'}},
                {'event':'QUOTE','at_ms':NOW,'data':quote()},
                {'event':'CANDIDATE','at_ms':candidate_at,'data':{'forecast':forecast()}}]
        if next_quote_at is not None:
            events.append({'event':'QUOTE','at_ms':next_quote_at,'data':quote(next_quote_at,mid='100.1')})
        return {'events':events,'source_kind':'SYNTHETIC','input_sha256':'test-only'}
    def candidate(self,tape):return next(x for x in tape['rows'] if x.get('forecast'))
    def test_stale_cached_quote_waits_for_next_fresh_observation(self):
        row=self.candidate(capture_to_tape(self.capture()))
        self.assertEqual(row['at_ms'],NOW+10000)
        self.assertEqual(row['quote']['observed_at_ms'],NOW+10000)
    def test_original_model_availability_is_not_replaced(self):
        row=self.candidate(capture_to_tape(self.capture()))
        self.assertEqual(row['forecast_available_at_ms'],NOW+6000)
        self.assertEqual(row['forecast']['issued_at_ms'],NOW)
        self.assertEqual(row['forecast']['reference_mid'],'100')
    def test_fresh_quote_is_never_backdated_to_model_completion(self):
        row=self.candidate(capture_to_tape(self.capture(NOW+70000)))
        self.assertEqual(row['at_ms'],NOW+70000)
        result=entry_admission(row['forecast'],row['quote'],25000,row['at_ms'],artifact(),synthetic_ok=True)
        self.assertIn('FORECAST_STALE',result['reasons'])
    def test_no_later_quote_preserves_rejected_candidate(self):
        row=self.candidate(capture_to_tape(self.capture(None)))
        self.assertEqual(row['forecast'],forecast())
        self.assertFalse(entry_admission(row['forecast'],row['quote'],25000,row['at_ms'])['allowed'])
    def test_still_fresh_cached_quote_can_be_used_immediately(self):
        row=self.candidate(capture_to_tape(self.capture(NOW+10000,NOW+3000)))
        self.assertEqual(row['at_ms'],NOW+3000)
    def test_label_uses_true_forecast_knowledge_time(self):
        tape=capture_to_tape(self.capture())
        self.assertEqual(candidate_outcomes(tape)[0]['forecast_available_at_ms'],NOW+6000)
