import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from historical_training.common import digest
from historical_training.resident_resource_guard import guarded_evaluator_class, _prediction_retry_window


class PredictionBackoffTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.path=Path(temporary.name)/'plan.json'
        plan={'start_ms':100000,'rule':{'slots':2,'slot_spacing_ms':360000}}
        plan['plan_id']=digest(plan)
        self.path.write_text(json.dumps(plan))

    def test_exponential_wait_is_bounded_by_ten_seconds_and_fixed_start(self):
        before=self.path.read_bytes()
        for attempt,delay in ((1,2000),(2,4000),(3,8000),(4,10000),(100,10000)):
            value=_prediction_retry_window(self.path,0,attempt,80000)
            self.assertEqual(value['next_probe_at_ms'],80000+delay)
        value=_prediction_retry_window(self.path,0,10,99700)
        self.assertEqual(value['next_probe_at_ms'],100000)
        self.assertIsNone(_prediction_retry_window(self.path,0,10,100000))
        self.assertEqual(self.path.read_bytes(),before)

    def wrapper(self):
        class Gate:
            draining=False
            def __init__(self):
                self.calls=0;self.allow=False
                self.last_admission={'reasons':['HOST_SWAP_RESERVE_BELOW_1GIB']}
            def acquire(self,subject):self.calls+=1;return self.allow
            def release(self):pass
        class Base:
            def __init__(self):self.tasks={};self.launched=0
            def launch(self,path,slot):self.launched+=1
            def reap(self):pass
        gate=Gate();service=guarded_evaluator_class(Base,gate)()
        def at(wall,tick,slot=0):
            with patch('historical_training.resident_resource_guard.time.time',return_value=wall/1000),patch('historical_training.resident_resource_guard.time.monotonic',return_value=tick):
                service.launch(self.path,slot)
        return gate,service,at

    def test_wrapper_skips_repeat_probe_but_rechecks_at_original_slot_start(self):
        gate,service,at=self.wrapper()
        at(80000,80);self.assertEqual(gate.calls,1)
        at(81000,81);self.assertEqual(gate.calls,1)
        at(82000,82);self.assertEqual(gate.calls,2)
        at(84000,84);self.assertEqual(gate.calls,2)
        gate.allow=True
        at(100000,84.1)
        self.assertEqual(gate.calls,3);self.assertEqual(service.launched,1)
        self.assertIsNone(service._launch_retry)

    def test_new_slot_clock_regression_and_drain_do_not_reuse_old_wait(self):
        gate,service,at=self.wrapper()
        at(80000,80)
        at(81000,81,1);self.assertEqual(gate.calls,2)
        at(80000,80,1);self.assertEqual(gate.calls,3)
        self.assertEqual(service._launch_retry['attempt'],1)
        gate.draining=True
        at(80001,80.001,1);self.assertEqual(gate.calls,4)
        self.assertEqual(service._launch_retry['attempt'],1)

    def test_corrupt_plan_never_provides_cached_wait(self):
        plan=json.loads(self.path.read_text());plan['start_ms']+=1
        self.path.write_text(json.dumps(plan))
        self.assertIsNone(_prediction_retry_window(self.path,0,2,80000))


if __name__=='__main__':unittest.main()
