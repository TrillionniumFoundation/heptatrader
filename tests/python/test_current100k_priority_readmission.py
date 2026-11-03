"""New-profile readmission inhibition, exact process identity and bounded cleanup."""
import copy
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

import activate_current100k_warm_stage as warm_activation
import activate_prepared_driver as driver
import current100k_priority_activation as activation
import test_current100k_background_guards as backgrounds
import test_current100k_priority_activation as original

TRAINER_TIMER = activation.READMISSION_TIMERS['trainer']
EVALUATOR_TIMER = activation.READMISSION_TIMERS['evaluator']


class ReadmissionTests(unittest.TestCase):
    setUp = backgrounds.BackgroundGuardTests.setUp
    publish = backgrounds.BackgroundGuardTests.publish
    load = backgrounds.BackgroundGuardTests.load
    prepare = backgrounds.BackgroundGuardTests.prepare
    shared_fixture = backgrounds.BackgroundGuardTests.shared_fixture
    selection_fixture = backgrounds.BackgroundGuardTests.selection_fixture
    select = backgrounds.BackgroundGuardTests.select

    def activation_output(self):
        return Path(self.config['forward_root']) / 'activation' / self.plan['plan_id']

    def leave_selected_before_timer_restore(self, request, candidate):
        run = activation.subprocess.run
        def unavailable(argv, **kwargs):
            if argv == ['systemctl', '--user', 'start', TRAINER_TIMER]:
                raise OSError('timer restore not dispatched')
            return run(argv, **kwargs)
        with patch.object(activation.subprocess, 'run', side_effect=unavailable):
            with self.assertRaisesRegex(OSError, 'timer restore not dispatched'):
                self.select(request, candidate)

    def restore(self, request, candidate, remaining=lambda: 180.):
        return activation.restore_shared_readmission(self.stage, self.config, self.plan_path,
            self.plan, candidate, request, remaining_seconds=remaining)

    def test_stop_precedes_each_drain_and_restore_preserves_enabled_state(self):
        request, candidate, properties, _, calls, signals, _ = self.selection_fixture()
        signal_call = activation.signal.pidfd_send_signal
        def natural_exit(fd, signum):
            for role, unit in activation.READMISSION_TIMERS.items():
                self.assertEqual(properties[unit]['ActiveState'], 'inactive')
                self.assertTrue((self.activation_output() / ('READMISSION_' + role.upper() + '_PAUSED.json')).is_file())
            return signal_call(fd, signum)
        with patch.object(activation.signal, 'pidfd_send_signal', side_effect=natural_exit):
            result = self.select(request, candidate)
        self.assertEqual(signals, [101, 102])
        self.assertEqual(calls[0], ['systemctl', '--user', 'stop', TRAINER_TIMER])
        self.assertEqual(calls[1], ['systemctl', '--user', 'stop', EVALUATOR_TIMER])
        self.assertEqual(calls[-1], ['systemctl', '--user', 'start', EVALUATOR_TIMER])
        self.assertEqual(result['readmission']['trainer']['readback']['UnitFileState'], 'enabled')
        self.assertEqual(result['readmission']['trainer']['readback']['ActiveState'], 'active')
        self.assertFalse(any('disable' in argv or 'enable' in argv or 'restart' in argv for argv in calls))
        before = copy.deepcopy(calls); self.select(request, candidate)
        self.assertEqual(calls, before); self.assertEqual(signals, [101, 102])

    def test_timer_stop_unknown_result_resumes_without_a_second_stop(self):
        request, candidate, properties, _, calls, signals, _ = self.selection_fixture()
        run = activation.subprocess.run
        def lost(argv, **kwargs):
            result = run(argv, **kwargs)
            if argv == ['systemctl', '--user', 'stop', TRAINER_TIMER]:
                raise subprocess.TimeoutExpired(argv, 30)
            return result
        with patch.object(activation.subprocess, 'run', side_effect=lost):
            with self.assertRaises(subprocess.TimeoutExpired): self.select(request, candidate)
        self.assertEqual(properties[TRAINER_TIMER]['ActiveState'], 'inactive')
        self.assertEqual(signals, [])
        self.assertFalse((self.activation_output() / 'READMISSION_TRAINER_PAUSED.json').exists())
        self.select(request, candidate)
        self.assertEqual(sum(argv == ['systemctl', '--user', 'stop', TRAINER_TIMER] for argv in calls), 1)
        self.assertEqual(signals, [101, 102])

    def test_timer_start_unknown_result_recovers_by_readback_without_resignalling(self):
        request, candidate, properties, _, calls, signals, _ = self.selection_fixture()
        run = activation.subprocess.run
        def lost(argv, **kwargs):
            result = run(argv, **kwargs)
            if argv == ['systemctl', '--user', 'start', TRAINER_TIMER]:
                raise OSError('restore result lost')
            return result
        with patch.object(activation.subprocess, 'run', side_effect=lost):
            with self.assertRaisesRegex(OSError, 'restore result lost'): self.select(request, candidate)
        self.assertEqual(properties[TRAINER_TIMER]['ActiveState'], 'active')
        before = copy.deepcopy(calls)
        self.assertFalse((self.activation_output() / 'READMISSION_TRAINER_RESTORED.json').exists())
        self.select(request, candidate)
        self.assertEqual(calls[len(before):], [['systemctl', '--user', 'start', EVALUATOR_TIMER]])
        self.assertEqual(signals, [101, 102])

    def test_new_process_environment_eacces_stays_pending_then_verifies_same_selected_roles(self):
        request, candidate, properties, _, calls, signals, _ = self.selection_fixture()
        proc = driver.proc_bytes; denied = []
        def transient(pid, name):
            if pid == 201 and name == 'environ' and not denied:
                denied.append(pid); raise PermissionError('transient environ EACCES')
            return proc(pid, name)
        with patch.object(driver, 'proc_bytes', side_effect=transient):
            with self.assertRaisesRegex(PermissionError, 'transient environ EACCES'):
                self.select(request, candidate)
            self.assertEqual(properties[TRAINER_TIMER]['ActiveState'], 'inactive')
            before_starts = sum(argv[:3] == ['systemctl', '--user', 'start'] for argv in calls)
            result = self.select(request, candidate)
        self.assertEqual(result['roles']['trainer']['pid_key'][0], '201')
        self.assertEqual(signals, [101, 102])
        self.assertEqual(sum(argv[:3] == ['systemctl', '--user', 'start'] for argv in calls), before_starts + 2)
        self.assertEqual(calls[-1][-1], EVALUATOR_TIMER)

    def test_exact_original_controller_restart_remains_a_failure(self):
        request, candidate, properties, processes, calls, signals, _ = self.selection_fixture()
        with patch.object(activation, '_select_observed', side_effect=OSError('paused only')):
            with self.assertRaisesRegex(OSError, 'paused only'): self.select(request, candidate)
        properties[activation.SHARED_UNITS['trainer']].update(MainPID='301', InvocationID='outside-restart')
        processes[301] = processes.pop(101)
        processes[301]['environ'] = processes[301]['environ'].replace(b'invocation-trainer', b'outside-restart')
        before = copy.deepcopy(calls)
        with self.assertRaisesRegex(ValueError, 'ORIGINAL_CONTROLLER_RESTARTED_DURING_SELECTION'):
            self.select(request, candidate)
        self.assertEqual(calls, before); self.assertEqual(signals, [])

    def test_queued_trainer_start_job_blocks_drain_until_it_is_gone(self):
        request, candidate, properties, _, _, signals, _ = self.selection_fixture()
        properties[activation.SHARED_UNITS['trainer']]['Job'] = '42 start'
        with self.assertRaisesRegex(ValueError, 'ROLE_PENDING_JOB_AFTER_READMISSION_PAUSE'):
            self.select(request, candidate)
        self.assertEqual(signals, [])
        properties[activation.SHARED_UNITS['trainer']]['Job'] = ''
        self.select(request, candidate); self.assertEqual(signals, [101, 102])

    def test_original_pid_gone_deactivating_waits_until_inactive_and_empty(self):
        request, candidate, properties, _, _, signals, _ = self.selection_fixture()
        send = activation.signal.pidfd_send_signal; waited = []
        def deactivating(fd, signum):
            send(fd, signum)
            for name in activation.SHARED_UNITS.values():
                if properties[name]['MainPID'] == '0':
                    properties[name].update(ActiveState='deactivating', SubState='stop-sigterm')
        def settle(seconds):
            waited.append(seconds)
            for name in activation.SHARED_UNITS.values():
                properties[name].update(ActiveState='inactive', SubState='dead')
        with patch.object(activation.signal, 'pidfd_send_signal', side_effect=deactivating), \
                patch.object(activation.time, 'sleep', side_effect=settle):
            self.select(request, candidate)
        self.assertEqual(signals, [101, 102]); self.assertEqual(len(waited), 1)

    def test_evaluator_timer_unknown_stop_resumes_without_any_early_drain(self):
        request, candidate, properties, _, calls, signals, _ = self.selection_fixture()
        run = activation.subprocess.run
        def lost(argv, **kwargs):
            result = run(argv, **kwargs)
            if argv == ['systemctl', '--user', 'stop', EVALUATOR_TIMER]:
                raise OSError('evaluator timer stop result lost')
            return result
        with patch.object(activation.subprocess, 'run', side_effect=lost):
            with self.assertRaisesRegex(OSError, 'evaluator timer stop result lost'):
                self.select(request, candidate)
        self.assertEqual(signals, [])
        self.assertTrue(all(properties[unit]['ActiveState'] == 'inactive' for unit in (TRAINER_TIMER, EVALUATOR_TIMER)))
        self.select(request, candidate)
        self.assertEqual(sum(argv[:3] == ['systemctl', '--user', 'stop'] for argv in calls), 2)
        self.assertEqual(signals, [101, 102])

    def test_loaded_timer_propagation_or_target_drift_refuses_before_effects(self):
        request, candidate, properties, _, calls, signals, _ = self.selection_fixture()
        p = properties[TRAINER_TIMER]
        for key, value in (('PropagatesStopTo', activation.SHARED_UNITS['trainer']),
                           ('BoundBy', activation.SHARED_UNITS['trainer']),
                           ('Triggers', activation.SHARED_UNITS['evaluator']),
                           ('UnitFileState', 'disabled')):
            old = p[key]; p[key] = value
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'READMISSION_'):
                self.select(request, candidate)
            p[key] = old
        self.assertEqual(calls, []); self.assertEqual(signals, [])

    def test_timer_bytes_and_missing_new_profile_pin_refuse(self):
        request, candidate, _, _, calls, signals, _ = self.selection_fixture()
        original.write(Path(request['readmission_timers']['trainer']['fragment_path']), b'changed timer\n')
        with self.assertRaisesRegex(ValueError, 'READMISSION_UNIT_BYTES_CHANGED'):
            self.select(request, candidate)
        request.pop('readmission_timers')
        with self.assertRaisesRegex(ValueError, 'BOTH_READMISSION_TIMER_PINS_REQUIRED'):
            self.select(request, candidate)
        self.assertEqual(calls, []); self.assertEqual(signals, [])

    def test_original_disabled_inactive_timer_is_never_started_or_enabled(self):
        request, candidate, properties, _, calls, _, _ = self.selection_fixture()
        timer = request['readmission_timers']['trainer']
        timer.update(original_unit_file_state='disabled', original_active_state='inactive', original_sub_state='dead')
        properties[TRAINER_TIMER].update(UnitFileState='disabled', ActiveState='inactive', SubState='dead')
        result = self.select(request, candidate)
        self.assertEqual(result['readmission']['trainer']['readback']['ActiveState'], 'inactive')
        self.assertFalse(any(TRAINER_TIMER in argv for argv in calls))

    def test_original_inactive_timer_external_activation_after_intent_is_not_silently_stopped(self):
        request, candidate, properties, _, calls, signals, _ = self.selection_fixture()
        request['readmission_timers']['trainer'].update(original_active_state='inactive', original_sub_state='dead')
        properties[TRAINER_TIMER].update(ActiveState='inactive', SubState='dead')
        with patch.object(activation, '_select_observed', side_effect=OSError('paused only')):
            with self.assertRaisesRegex(OSError, 'paused only'): self.select(request, candidate)
        properties[TRAINER_TIMER].update(ActiveState='active', SubState='waiting')
        before = copy.deepcopy(calls)
        with self.assertRaisesRegex(ValueError, 'READMISSION_OUTSIDE_ACTIVITY_CHANGED'):
            self.select(request, candidate)
        self.assertEqual(calls, before); self.assertEqual(signals, [])

    def test_insufficient_budget_does_not_pause_timer(self):
        request, candidate, _, _, calls, signals, _ = self.selection_fixture()
        with self.assertRaisesRegex(ValueError, 'INSUFFICIENT_BUDGET_BEFORE_READMISSION_PAUSE'):
            self.select(request, candidate, remaining_seconds=lambda: 149.)
        self.assertEqual(calls, []); self.assertEqual(signals, [])

    def test_intent_sync_failure_never_advances_timer_controls(self):
        request, candidate, _, _, calls, signals, _ = self.selection_fixture()
        with patch.object(activation, '_sync_selection_intent', side_effect=OSError('durability unavailable')):
            for _ in range(2):
                with self.assertRaisesRegex(OSError, 'durability unavailable'): self.select(request, candidate)
        self.assertEqual(calls, []); self.assertEqual(signals, [])
        self.select(request, candidate)

    def test_expired_activation_all_selected_has_separate_timer_only_cleanup(self):
        request, candidate, properties, _, calls, signals, _ = self.selection_fixture()
        self.leave_selected_before_timer_restore(request, candidate)
        before = copy.deepcopy(calls); original_deadline = request['activation_deadline_ms']
        with patch.object(activation.time, 'time', return_value=3000.):
            with self.assertRaisesRegex(ValueError, 'ACTIVATION_WINDOW_EXPIRED'): self.select(request, candidate)
            result = self.restore(request, candidate)
        self.assertEqual(request['activation_deadline_ms'], original_deadline)
        self.assertEqual(calls[len(before):], [['systemctl', '--user', 'start', unit] for unit in (TRAINER_TIMER, EVALUATOR_TIMER)])
        self.assertEqual(signals, [101, 102]); self.assertFalse(result['activation_deadline_renewed'])
        self.assertFalse(result['registration_performed']); self.assertFalse(result['cooperative_drain_performed'])
        self.assertEqual(properties[TRAINER_TIMER]['ActiveState'], 'active')

    def test_expired_cleanup_refuses_original_or_drained_roles(self):
        request, candidate, properties, _, calls, signals, _ = self.selection_fixture()
        with patch.object(activation, '_select_observed', side_effect=OSError('paused only')):
            with self.assertRaisesRegex(OSError, 'paused only'): self.select(request, candidate)
        before = copy.deepcopy(calls)
        with patch.object(activation.time, 'time', return_value=3000.):
            with self.assertRaises((ValueError, FileNotFoundError)):
                self.restore(request, candidate)
        self.assertEqual(calls, before); self.assertEqual(signals, [])
        self.assertEqual(properties[TRAINER_TIMER]['ActiveState'], 'inactive')

    def test_timer_only_cleanup_requires_exact_original_intent_and_finite_budget(self):
        request, candidate, _, _, calls, _, _ = self.selection_fixture()
        self.leave_selected_before_timer_restore(request, candidate); before = copy.deepcopy(calls)
        with self.assertRaisesRegex(ValueError, 'READMISSION_RESTORE_FINITE_BUDGET_REQUIRED'):
            self.restore(request, candidate, remaining=None)
        changed = copy.deepcopy(request); changed['activation_deadline_ms'] += 1
        with self.assertRaisesRegex(ValueError, 'SELECTION_INTENT_CHANGED'): self.restore(changed, candidate)
        self.assertEqual(calls, before)

    def test_cleanup_entry_does_not_create_lock_candidate_or_call_registrar(self):
        request, candidate, _, _, calls, signals, _ = self.selection_fixture()
        self.leave_selected_before_timer_restore(request, candidate)
        selection = original.write(self.root / 'selection.json', request)
        lock = original.write(Path(self.config['forward_root']) / 'activation.lock', b''); lock.chmod(0o600)
        before = copy.deepcopy(calls)
        with patch.object(warm_activation, 'stage_inputs', return_value=(self.stage, {'registrar':self.config}, {})), \
                patch.object(warm_activation, 'registration_state', return_value={'state':'PUBLISHED','plan':self.plan}), \
                patch.object(warm_activation, 'child') as registrar, \
                patch.object(activation.time, 'time', return_value=3000.):
            result = warm_activation.restore_readmission_only(Path('/stage'), 'a'*64,
                selection, original.sha(selection), 3060000)
            registrar.assert_not_called()
        self.assertEqual(result['state'], 'SAME_PLAN_READMISSION_TIMER_RESTORED_ONLY')
        self.assertEqual(calls[len(before):], [['systemctl', '--user', 'start', unit] for unit in (TRAINER_TIMER, EVALUATOR_TIMER)])
        self.assertEqual(signals, [101, 102])


    def test_cleanup_deadline_is_explicit_bounded_and_does_not_initialize_anything(self):
        with patch.object(warm_activation, 'stage_inputs') as read_stage, \
                patch.object(warm_activation.time, 'time', return_value=1.):
            for deadline in (None, 1000, 181001):
                with self.subTest(deadline=deadline), self.assertRaisesRegex(ValueError, 'EXPLICIT_FINITE_DEADLINE'):
                    warm_activation.restore_readmission_only(Path('/stage'), 's', Path('/selection'), 'p', deadline)
            read_stage.assert_not_called()

    def test_cleanup_wall_rollback_cannot_extend_its_initial_monotonic_budget(self):
        clocks = {'wall': 1., 'mono': 100.}; budgets = []
        original_budget = warm_activation.WakeBudget
        def capture(deadline):
            value = original_budget(deadline); budgets.append(value); return value
        def rollback(*args):
            self.assertEqual(budgets[0].stop, 160.)
            clocks.update(wall=1., mono=161.)
            budgets[0].remaining()
            self.fail('cleanup exceeded its initial 60-second monotonic budget')
        with patch.object(warm_activation.time, 'time', side_effect=lambda: clocks['wall']), \
                patch.object(warm_activation.time, 'monotonic', side_effect=lambda: clocks['mono']), \
                patch.object(warm_activation, 'WakeBudget', side_effect=capture), \
                patch.object(warm_activation, 'stage_inputs', side_effect=rollback):
            with self.assertRaisesRegex(ValueError, 'WARM_ACTIVATION_TOTAL_BUDGET_EXPIRED'):
                warm_activation.restore_readmission_only(Path('/stage'), 's', Path('/selection'), 'p', 61000)


if __name__ == '__main__':
    unittest.main()
