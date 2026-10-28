"""Negative-only registration stability through the existing transaction path."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from historical_training import resident_resource_guard as guard
from historical_training.tests import test_forward_registration_preflight as preflight
from laya_cell import resident_plan_admission as admission
from laya_cell.resident import Supervisor
from laya_cell.resident_store import get_meta
from laya_cell.tests import test_resident_plan_admission as fixtures


class StabilityObservationsTests(unittest.TestCase):
    def setUp(self):
        box=tempfile.TemporaryDirectory();self.addCleanup(box.cleanup)
        self.gate=guard.Gate({'state_root':box.name,'original_config':'/synthetic-config',
            'original_config_sha256':'a'*64,'period_path':'/synthetic-period',
            'period_sha256':'a'*64,'manual_hold_path':'/synthetic-hold'},'trainer')
        self.subject={'job_id':'job','candidate_id':'candidate'}
        self.original={'runtime_sha256':{'learn/fixed.py':'b'*64}}

    def observe(self,tick_ms,*,allowed=True,subject=None,original=None,at_ms=None):
        report={'admit_new_worker':allowed,'at_ms':fixtures.NOW+tick_ms if at_ms is None else at_ms,
                'reasons':[] if allowed else ['HOST_FULL_MEMORY_PSI_PRESSURE']}
        with patch.object(guard.time,'monotonic',return_value=tick_ms/1000):
            return self.gate._stable_registration(report,subject or self.subject,original or self.original)

    def test_fast_duplicate_checks_cannot_replace_three_spaced_samples(self):
        for tick in (0,1,1000,29999):
            result=self.observe(tick)
            self.assertFalse(result['admit_new_worker'])
            self.assertEqual(result['registration_stability']['healthy_samples'],1)
        self.assertEqual(self.observe(30000)['registration_stability']['healthy_samples'],2)
        self.assertFalse(self.observe(59999)['admit_new_worker'])
        ready=self.observe(60000)
        self.assertTrue(ready['admit_new_worker'])
        self.assertEqual(ready['registration_stability']['healthy_samples'],3)
        self.assertEqual(ready['registration_stability']['observed_span_ms'],60000)
        self.assertFalse(ready['registration_stability']['future_resource_capacity_reserved'])
        self.assertIsNone(self.gate.lease.fd)

    def test_original_denial_clears_all_accumulated_health(self):
        self.observe(0);self.observe(30000)
        denied=self.observe(31000,allowed=False)
        self.assertIsNone(self.gate._registration_stability)
        self.assertEqual(denied['reasons'],['HOST_FULL_MEMORY_PSI_PRESSURE'])
        self.assertEqual(denied['registration_stability']['healthy_samples'],0)
        self.assertFalse(self.observe(60000)['admit_new_worker'])
        self.assertFalse(self.observe(90000)['admit_new_worker'])
        self.assertTrue(self.observe(120000)['admit_new_worker'])

    def test_candidate_runtime_or_configuration_change_restarts_observation(self):
        variants=({'subject':{'job_id':'job','candidate_id':'different'}},
                  {'subject':{'job_id':'different-job','candidate_id':'candidate'}},
                  {'original':{'runtime_sha256':{'learn/fixed.py':'c'*64}}},
                  {'config_sha':'d'*64})
        for changed in variants:
            with self.subTest(changed=changed):
                self.gate._registration_stability=None
                self.gate.c['original_config_sha256']='a'*64
                self.observe(0);self.observe(30000)
                options=dict(changed)
                if 'config_sha' in options:
                    self.gate.c['original_config_sha256']=options.pop('config_sha')
                result=self.observe(60000,**options)
                self.assertFalse(result['admit_new_worker'])
                self.assertEqual(result['registration_stability']['healthy_samples'],1)

    def test_observation_gap_and_each_clock_regression_restart(self):
        for tick,wall in ((120001,fixtures.NOW+120001),
                          (29000,fixtures.NOW+31000), (60000,fixtures.NOW+29999)):
            with self.subTest(tick=tick,wall=wall):
                self.gate._registration_stability=None
                self.observe(0);self.observe(30000)
                result=self.observe(tick,at_ms=wall)
                self.assertFalse(result['admit_new_worker'])
                self.assertEqual(result['registration_stability']['healthy_samples'],1)

    def test_restart_does_not_reuse_a_previously_ready_observation(self):
        self.observe(0);self.observe(30000);self.assertTrue(self.observe(60000)['admit_new_worker'])
        self.gate=guard.Gate(dict(self.gate.c),'trainer')
        result=self.observe(60001)
        self.assertFalse(result['admit_new_worker'])
        self.assertEqual(result['registration_stability']['healthy_samples'],1)

    def test_actual_nonregistration_resource_refusal_breaks_the_sequence(self):
        self.observe(0);self.observe(30000)
        memory={'MemTotal':24*guard.GIB,'MemAvailable':10*guard.GIB,
                'SwapTotal':8*guard.GIB,'SwapFree':8*guard.GIB}
        with patch.object(guard,'sha',return_value='a'*64), \
                patch.object(guard,'read',return_value=self.original), \
                patch.object(Path,'read_text',return_value='synthetic telemetry'), \
                patch.object(guard,'parse_meminfo',return_value=memory), \
                patch.object(guard,'parse_full_psi',return_value={'avg10':0.,'avg60':.11}), \
                patch.object(guard.os.path,'lexists',return_value=False), \
                patch.object(self.gate,'attach_resource_attribution',side_effect=lambda report:report), \
                patch.object(guard,'atomic'):
            report=self.gate.check()
        self.assertIn('HOST_FULL_MEMORY_PSI_PRESSURE',report['reasons'])
        self.assertIsNone(self.gate._registration_stability)
        self.assertEqual(self.observe(60000)['registration_stability']['healthy_samples'],1)


class StableRegistrationGate(preflight.RegistrationGate):
    """Synthetic original health, with the real negative stability calculation."""
    def check(self,*,forward_registration,prediction_ready=False):
        report=super().check(forward_registration=forward_registration,prediction_ready=prediction_ready)
        report['at_ms']=int(guard.time.time()*1000)
        return guard.Gate._stable_registration(self,report,forward_registration,self.original)


class StabilityTransactionTests(unittest.TestCase):
    # Reuse fixture helpers, without inheriting/rerunning the older test methods.
    make_service=preflight.ForwardRegistrationTests.make_service
    register=preflight.ForwardRegistrationTests.register
    plan_path=preflight.ForwardRegistrationTests.plan_path
    assert_unallocated=preflight.ForwardRegistrationTests.assert_unallocated

    def setUp(self):
        preflight.ForwardRegistrationTests.setUp(self)
        clock=patch.object(guard.time,'monotonic',return_value=0.)
        self.monotonic=clock.start();self.addCleanup(clock.stop)

    def advance(self,milliseconds):
        self.clock.return_value=(fixtures.NOW+milliseconds)/1000
        self.monotonic.return_value=milliseconds/1000
        self.capture.return_value=fixtures.fresh_proof(fixtures.NOW+milliseconds)

    def wrap(self,decisions):
        gate=StableRegistrationGate(self,decisions)
        gate.service=self.service
        gate.c={'original_config_sha256':'f'*64}
        gate.original={'runtime_sha256':copy.deepcopy(self.service.c['runtime_sha256'])}
        self.service.__class__=guard.guarded_trainer_class(Supervisor,gate)
        return gate

    def test_waits_without_alpha_then_publishes_same_candidate_once(self):
        gate=self.wrap([True,True,True,True])
        candidate_bytes=(Path(self.job['output_directory'])/'candidate.json').read_bytes()
        consumed=self.service.db.execute('SELECT * FROM consumed_rows').fetchall()
        for ms in (0,30000):
            self.advance(ms)
            result=self.register()
            self.assertEqual(result['state'],'FORWARD_REGISTRATION_GUARD_HOLD')
            self.assert_unallocated()
            self.assertIsNone(get_meta(self.service.db,admission.KEY_PREFIX+'job'))
        self.capture.assert_not_called()
        self.advance(60000)
        self.assertEqual(self.register()['state'],'PUBLISHED')
        original_plan=self.plan_path().read_bytes()
        self.assertEqual(get_meta(self.service.db,'evaluation_trial'),1)
        self.register()
        self.assertEqual(self.plan_path().read_bytes(),original_plan)
        self.assertEqual((Path(self.job['output_directory'])/'candidate.json').read_bytes(),candidate_bytes)
        self.assertEqual(self.service.db.execute('SELECT * FROM consumed_rows').fetchall(),consumed)
        self.assertEqual(len(gate.calls),4)
        self.capture.assert_called_once()
        gate.acquire.assert_not_called();gate.release.assert_not_called()

    def test_failure_after_source_read_rolls_back_and_requires_new_samples(self):
        gate=self.wrap([True,True,True,False,True,True,True,True])
        for ms in (0,30000,60000):
            self.advance(ms)
            self.assertEqual(self.register()['state'],'FORWARD_REGISTRATION_GUARD_HOLD')
            self.assert_unallocated()
        self.assertIsNone(gate._registration_stability)
        self.assertIsNone(get_meta(self.service.db,admission.KEY_PREFIX+'job'))
        self.capture.assert_called_once()
        for ms in (90000,120000):
            self.advance(ms)
            self.assertEqual(self.register()['state'],'FORWARD_REGISTRATION_GUARD_HOLD')
            self.assert_unallocated()
        self.advance(150000)
        self.assertEqual(self.register()['state'],'PUBLISHED')
        self.assertEqual(get_meta(self.service.db,'evaluation_trial'),1)
        self.assertEqual(self.capture.call_count,2)

    def test_reserved_recovery_after_restart_skips_new_samples_and_preserves_original_time(self):
        self.wrap([True,True,True,True])
        for ms in (0,30000):
            self.advance(ms);self.register();self.assert_unallocated()
        self.advance(60000)
        with patch.object(admission,'_publish',side_effect=OSError('synthetic publication loss')):
            with self.assertRaises(OSError):self.register()
        reserved=get_meta(self.service.db,admission.KEY_PREFIX+'job')
        self.assertEqual(reserved['state'],'RESERVED')
        self.assertEqual(get_meta(self.service.db,'evaluation_trial'),1)
        fresh_gate=self.wrap([])
        self.capture.side_effect=AssertionError('ORIGINAL_RESERVATION_NEEDS_NO_NEW_SOURCE')
        self.clock.return_value=fixtures.SATURDAY/1000
        self.monotonic.return_value=100000
        self.assertEqual(self.register()['state'],'PUBLISHED')
        from laya_cell.integrity import read_json
        self.assertEqual(read_json(self.plan_path()),reserved['plan'])
        self.assertEqual(get_meta(self.service.db,'evaluation_trial'),1)
        self.assertEqual(fresh_gate.calls,[])

    def test_calendar_closed_or_queue_blocked_does_not_create_healthy_samples(self):
        gate=self.wrap([])
        self.inspect.return_value={'pending_original_trials':1}
        self.assertEqual(self.register()['state'],'DEFERRED')
        self.assertEqual(gate.calls,[])
        self.assertIsNone(getattr(gate,'_registration_stability',None))
        self.assert_unallocated()
        self.inspect.return_value={'pending_original_trials':0}
        self.clock.return_value=fixtures.SATURDAY/1000
        self.assertEqual(self.register()['state'],'NO_FORWARD_TRIAL')
        self.assertEqual(gate.calls,[])
        self.assert_unallocated()


if __name__=='__main__':
    unittest.main()
