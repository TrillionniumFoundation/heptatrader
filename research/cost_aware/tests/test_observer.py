import unittest
from pathlib import Path
from fixtures import *
from hepta_cost_observer import Sampler, forbidden

class Writer:
    def __init__(self):self.events=[]
    def append(self,event,data,**kwargs):self.events.append((event,data))
class DoneWorker:
    def done(self):return True
    def result(self):return forecast()
class Fake:
    INSTRUMENT='BTC.USD'
    def __init__(self):self.now=NOW;self.calls=[];self.model_calls=[];self.fail=False
    def now_ms(self):return self.now
    def native(self,cli,tool,*args):
        self.calls.append(tool)
        if self.fail:raise OSError('quote unavailable')
        return quote(self.now)
    def ModelWorker(self,model,state_dir,history,position):
        self.model_calls.append((history,position));return DoneWorker()
class ObserverTests(unittest.TestCase):
    def test_sampler_uses_only_quote_reads(self):
        f=Fake();w=Writer();s=Sampler(f,'read-only',Path('/unused'),w)
        for i in range(25):f.now=NOW+i*10000;s.poll(f.now)
        self.assertEqual(set(f.calls),{'market.get_quote'})
        self.assertEqual(s.forecasts_started,2)
        self.assertEqual(len(f.calls),25)
    def test_forecast_schedule_has_no_position_or_pending_input(self):
        f=Fake();s=Sampler(f,'read-only',Path('/unused'),Writer())
        for i in range(13):f.now=NOW+i*10000;s.poll(f.now)
        self.assertEqual(len(f.model_calls),1)
        self.assertEqual(f.model_calls[0][1],0)
    def test_bad_quote_is_explicitly_missing_not_hold(self):
        f=Fake();f.fail=True;w=Writer();s=Sampler(f,'read-only',Path('/unused'),w);s.poll(NOW)
        self.assertEqual(s.failures,1)
        self.assertEqual(w.events[0][0],'SAMPLING_FAILURE')
        self.assertEqual(s.forecasts_started,0)
    def test_dense_quotes_preserve_ten_second_model_feature_grid(self):
        f=Fake();s=Sampler(f,'read-only',Path('/unused'),Writer())
        for i in range(61):f.now=NOW+i*2000;s.poll(f.now)
        self.assertEqual(len(f.calls),61)
        self.assertEqual(len(s.history),13)
        self.assertEqual([x['observed_at_ms'] for x in s.history],[NOW+i*10000 for i in range(13)])
        self.assertEqual(s.forecasts_started,1)
    def test_failed_feature_slot_does_not_fill_with_future_quote(self):
        f=Fake();s=Sampler(f,'read-only',Path('/unused'),Writer())
        s.poll(NOW);f.now=NOW+10000;f.fail=True;s.poll(f.now)
        f.now=NOW+12000;f.fail=False;s.poll(f.now)
        self.assertEqual(len(s.history),1)
        f.now=NOW+20000;s.poll(f.now)
        self.assertEqual([x['observed_at_ms'] for x in s.history],[NOW,NOW+20000])
    def test_mutation_entry_points_refused(self):
        with self.assertRaisesRegex(CostError,'MUTATION_FORBIDDEN'):forbidden()
    def test_busy_forecast_slot_is_not_catchup_trade(self):
        f=Fake();w=Writer();s=Sampler(f,'read-only',Path('/unused'),w)
        for i in range(13):f.now=NOW+i*10000;s.poll(f.now)
        class Busy:
            def done(self):return False
        s.worker=Busy()
        f.now=NOW+240000;s.poll(f.now)
        self.assertEqual(s.forecasts_started,1)
        self.assertEqual(w.events[-1][0],'MODEL_SLOT_SKIPPED')
        self.assertTrue(w.events[-1][1]['not_a_strategy_hold'])

if __name__=='__main__':unittest.main(verbosity=2)
