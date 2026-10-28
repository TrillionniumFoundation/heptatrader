"""CPU metadata diagnostics: no real process, lease, model or GPU execution."""
from contextlib import ExitStack
import io
import json
from pathlib import Path
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from historical_training import resident_resource_guard as guard
from laya_cell import resident, resident_evaluate as evaluate
from laya_cell import resident_launch as launch
from laya_cell import resident_hip_runtime as hip
from laya_cell import resident_worker_failure_context as timing
from laya_cell.integrity import ContractError, digest
from laya_cell.promotion import RULE
from laya_cell.tests import test_resident_launch_deadline as deadline_fixture
from laya_cell.tests import test_resident_worker_failure_diagnostics as failure_fixture


def events(text):
    return [json.loads(line[len(timing.STARTUP_PREFIX):])
            for line in text.splitlines() if line.startswith(timing.STARTUP_PREFIX)]


class StartupEventTests(unittest.TestCase):
    def test_output_is_bounded_allowlisted_and_not_authority(self):
        output = io.StringIO()
        with patch.object(timing.sys, 'stderr', output):
            timing.emit_startup('x' * 10000, {
                'plan_id': 'a' * 64, 'slot': 21,
                'admission': {'started_at_ms': 12, 'elapsed_ns': 34, 'raw_env': 'secret'},
                'environment': {'TOKEN': 'secret'}, 'broker_message': 'private',
                'account': 'private', 'result': 'private'})
        raw = output.getvalue()
        value, = events(raw)
        self.assertLessEqual(len(raw.encode()), timing.MAX_STARTUP_BYTES)
        self.assertEqual(len(value['phase']), 64)
        self.assertEqual(value['admission'], {'started_at_ms': 12, 'elapsed_ns': 34})
        self.assertNotIn('secret', raw); self.assertNotIn('private', raw)
        self.assertTrue(value['observational_only'])
        self.assertEqual(value['authorization_effect'], 'NONE')

    def test_elapsed_uses_monotonic_clock_even_when_wall_clock_moves_back(self):
        result = timing.startup_interval(
            {'at_ms': 200, 'monotonic_ns': 1000},
            {'at_ms': 100, 'monotonic_ns': 5000})
        self.assertEqual(result, {'started_at_ms': 200, 'completed_at_ms': 100, 'elapsed_ns': 4000})
        self.assertNotIn('elapsed_ns', timing.startup_interval(
            {'monotonic_ns': 1000}, {'monotonic_ns': 500}))
        with patch.object(timing.time, 'time_ns', side_effect=MemoryError):
            self.assertEqual(timing.startup_stamp(), {})

    def test_kernel_birth_handles_command_parentheses_and_missing_process(self):
        fields = ['S', *['0'] * 18, '98765', '0']
        with patch.object(timing, '_read', return_value={
                'text': '123 (name with ) and spaces) ' + ' '.join(fields), 'truncated': False}) as read:
            self.assertEqual(timing.process_start_ticks(123), 98765)
        read.assert_called_once_with('/proc/123/stat', 4096)
        for reply in ({'text': '123 (bad) S', 'truncated': False},
                      {'text': '124 (other) ' + ' '.join(fields), 'truncated': False},
                      {'text': '', 'truncated': True}):
            with self.subTest(reply=reply), patch.object(timing, '_read', return_value=reply):
                self.assertIsNone(timing.process_start_ticks(123))
        with patch.object(timing, '_read', side_effect=PermissionError):
            self.assertIsNone(timing.process_start_ticks(123))

    def test_diagnostic_failure_cannot_replace_the_phase_or_escape(self):
        state = {}
        with patch.object(timing, 'emit_startup', side_effect=MemoryError):
            launch._phase(state, 'VALIDATE_CONFIG')
        self.assertEqual(state, {'phase': 'VALIDATE_CONFIG'})
        with patch.object(timing.sys, 'stderr') as output:
            output.write.side_effect = BrokenPipeError
            self.assertIsNone(timing.emit_startup('VALIDATE_CONFIG'))

    def test_normal_path_helpers_preserve_keyboard_interrupt_and_system_exit(self):
        for kind in (KeyboardInterrupt, SystemExit):
            error = kind('operator termination')
            with self.subTest(kind=kind, boundary='clock'), \
                    patch.object(timing.time, 'time_ns', side_effect=error):
                with self.assertRaises(kind) as caught:
                    timing.startup_stamp()
                self.assertIs(caught.exception, error)
            with self.subTest(kind=kind, boundary='proc'), \
                    patch.object(timing, '_read', side_effect=error):
                with self.assertRaises(kind) as caught:
                    timing.process_start_ticks(123)
                self.assertIs(caught.exception, error)
            with self.subTest(kind=kind, boundary='output'), \
                    patch.object(timing.sys, 'stderr') as output:
                output.write.side_effect = error
                with self.assertRaises(kind) as caught:
                    timing.emit_startup('VALIDATE_CONFIG')
                self.assertIs(caught.exception, error)

    def test_real_launcher_entry_does_not_turn_termination_into_diagnostics(self):
        for kind in (KeyboardInterrupt, SystemExit):
            error = kind('operator termination')
            with self.subTest(kind=kind), \
                    patch.object(timing, 'emit_startup', side_effect=error), \
                    patch.object(timing, 'emit') as failure:
                # Execute the real main/_main/_phase call chain. It exits at
                # the first diagnostic boundary before arguments or model IO.
                with self.assertRaises(kind) as caught:
                    launch.main()
                self.assertIs(caught.exception, error)
                failure.assert_not_called()


class PredictionDispatchTimingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / 'plan.json'
        self.plan = {'candidate_id': 'a' * 64, 'start_ms': 1_000_000, 'rule': dict(RULE)}
        self.plan['plan_id'] = digest(self.plan)
        self.path.write_text(json.dumps(self.plan))
        self.gate = SimpleNamespace(acquire=Mock(return_value=True), release=Mock(),
                                    draining=False, lease=SimpleNamespace(fd=71))
        kind = guard.guarded_evaluator_class(evaluate.Evaluator, self.gate)
        self.engine = kind.__new__(kind)
        self.engine.c = {'python': '/synthetic/python'}
        self.engine.path = self.root / 'config.json'
        self.engine.tasks = {}
        self.process = SimpleNamespace(pid=123)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(guard.time, 'time', return_value=1000.0))
        self.stack.enter_context(patch.object(guard.time, 'monotonic', return_value=1.0))
        self.stack.enter_context(patch.object(guard.time, 'monotonic_ns', return_value=1_000_000))
        self.stack.enter_context(patch.object(resident, 'inherited_worker_locks', return_value=(71, 72)))
        self.environment = self.stack.enter_context(patch.object(hip, 'worker_environment',
                                                                return_value={'ONLY': 'synthetic'}))
        self.popen = self.stack.enter_context(patch.object(evaluate.subprocess, 'Popen',
                                                          return_value=self.process))
        self.stack.enter_context(patch.object(timing, 'process_start_ticks', return_value=98765))
        self.addCleanup(self.close_streams)

    def close_streams(self):
        for _, stream, _ in self.engine.tasks.values():
            stream.close()

    def log_events(self):
        return events((self.root / 'prediction-000.log').read_text())

    def private_stream(self, output):
        original_open = Path.open

        def private_open(path, *args, **kwargs):
            if path.name == 'prediction-000.log':
                return output
            return original_open(path, *args, **kwargs)

        return patch.object(Path, 'open', private_open)

    def test_success_links_admission_environment_popen_and_kernel_identity(self):
        stamps = [{'at_ms': ms, 'monotonic_ns': tick} for ms, tick in (
            (1000100, 1100000), (1000200, 1200000), (1000300, 1600000),
            (1000400, 1900000), (1000500, 2000000))]
        with patch.object(timing, 'startup_stamp', side_effect=stamps):
            self.engine.launch(self.path, 0)
        value, = self.log_events()
        self.assertEqual(value['phase'], 'PREDICTION_DISPATCHED')
        self.assertEqual((value['plan_id'], value['candidate_id'], value['slot']),
                         (self.plan['plan_id'], self.plan['candidate_id'], 0))
        self.assertEqual((value['child_pid'], value['child_start_ticks']), (123, 98765))
        self.assertEqual(value['admission']['elapsed_ns'], 100000)
        self.assertEqual(value['environment_preparation']['elapsed_ns'], 400000)
        self.assertEqual(value['popen']['elapsed_ns'], 300000)
        self.assertIsNone(self.engine._worker_admission_timing)
        self.assertIs(self.engine.tasks[(str(self.path), 0)][0], self.process)
        self.assertEqual(self.engine.tasks[(str(self.path), 0)][2], 1.0)
        self.assertEqual(self.popen.call_args.args[0], [
            '/synthetic/python', '-B', '-m', 'laya_cell.resident_launch', 'predict',
            '--config', str(self.engine.path), '--plan', str(self.path), '--slot', '0',
            '--held-lock-fd', '71', '--held-lock-fd', '72'])
        self.assertEqual(self.popen.call_args.kwargs['pass_fds'], (71, 72))
        self.assertEqual(self.popen.call_args.kwargs['env'], {'ONLY': 'synthetic'})
        self.gate.release.assert_not_called()

    def test_denial_never_dispatches_or_creates_a_success_event(self):
        self.gate.acquire.return_value = False
        self.engine.launch(self.path, 0)
        self.popen.assert_not_called(); self.environment.assert_not_called()
        self.assertFalse(self.engine.tasks)
        self.assertFalse((self.root / 'prediction-000.log').exists())
        self.assertTrue((self.root / 'admission-denial-000.json').exists())

    def test_popen_failure_keeps_original_error_and_no_child_identity(self):
        original = OSError('synthetic Popen failure')
        self.popen.side_effect = original
        with self.assertRaises(OSError) as caught:
            self.engine.launch(self.path, 0)
        self.assertIs(caught.exception, original)
        self.assertFalse(self.engine.tasks)
        self.assertEqual(self.log_events(), [])
        self.gate.release.assert_called_once()
        self.assertIsNone(self.engine._worker_admission_timing)

    def test_environment_error_closes_stream_and_never_creates_child(self):
        original = OSError('synthetic verified environment failure')
        self.environment.side_effect = original
        output = io.StringIO()
        with self.private_stream(output), self.assertRaises(OSError) as caught:
            self.engine.launch(self.path, 0)
        self.assertIs(caught.exception, original)
        self.assertTrue(output.closed)
        self.popen.assert_not_called()
        self.assertFalse(self.engine.tasks)
        self.gate.release.assert_called_once()

    def test_termination_before_gate_is_not_swallowed_or_admitted(self):
        for kind in (KeyboardInterrupt, SystemExit):
            error = kind('operator termination')
            with self.subTest(kind=kind), \
                    patch.object(guard.time, 'monotonic_ns', side_effect=error):
                with self.assertRaises(kind) as caught:
                    self.engine.launch(self.path, 0)
                self.assertIs(caught.exception, error)
        self.gate.acquire.assert_not_called()
        self.popen.assert_not_called()
        self.assertFalse(self.engine.tasks)

    def test_termination_before_child_keeps_original_fd_cleanup_and_no_identity(self):
        for kind in (KeyboardInterrupt, SystemExit):
            for boundary, stamps in (('admission_complete', None),
                                     ('environment_start', [{}]),
                                     ('popen_start', [{}, {}]),
                                     ('popen', [{}, {}, {}])):
                error = kind('operator termination')
                side_effect = ([*stamps, error] if stamps is not None else error)
                self.popen.side_effect = None
                if boundary == 'popen':
                    side_effect = stamps
                    self.popen.side_effect = error
                output = io.StringIO()
                with self.subTest(kind=kind, boundary=boundary), self.private_stream(output), \
                        patch.object(timing, 'startup_stamp', side_effect=side_effect):
                    with self.assertRaises(kind) as caught:
                        self.engine.launch(self.path, 0)
                    self.assertIs(caught.exception, error)
                self.assertFalse(self.engine.tasks)
                self.assertIsNone(self.engine._worker_admission_timing)
                if boundary != 'admission_complete':
                    self.assertTrue(output.closed)
        # Keep the original BaseException contract: the controller's finally
        # owns lease cleanup; no child was dispatched or invented here.
        self.gate.release.assert_not_called()

    def test_termination_after_popen_preserves_tracked_child_until_original_cleanup(self):
        for kind in (KeyboardInterrupt, SystemExit):
            for boundary in ('process_start_ticks', 'emit_startup'):
                error = kind('operator termination')
                with self.subTest(kind=kind, boundary=boundary), \
                        patch.object(timing, boundary, side_effect=error):
                    with self.assertRaises(kind) as caught:
                        self.engine.launch(self.path, 0)
                    self.assertIs(caught.exception, error)
                task = self.engine.tasks[(str(self.path), 0)]
                self.assertIs(task[0], self.process)
                self.assertFalse(task[1].closed)
                self.assertIsNone(self.engine._worker_admission_timing)
                task[1].close(); self.engine.tasks.clear()
        self.gate.release.assert_not_called()

    def test_proc_or_output_failure_preserves_success_and_lease(self):
        for failure in ('process_start_ticks', 'emit_startup'):
            with self.subTest(failure=failure), patch.object(timing, failure, side_effect=MemoryError):
                self.engine.launch(self.path, 0)
            self.assertIn((str(self.path), 0), self.engine.tasks)
            self.engine.tasks[(str(self.path), 0)][1].close()
            self.engine.tasks.clear()
        self.assertEqual(self.popen.call_count, 2)
        self.gate.release.assert_not_called()

    def test_exited_or_confined_child_keeps_pid_without_inventing_birth(self):
        with patch.object(timing, 'process_start_ticks', return_value=None):
            self.engine.launch(self.path, 0)
        value, = self.log_events()
        self.assertEqual(value['child_pid'], 123)
        self.assertNotIn('child_start_ticks', value)


class LauncherStartupTimingTests(unittest.TestCase):
    def fixture(self):
        case = deadline_fixture.PredictionLaunchDeadlineTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        return case

    def test_expired_launcher_flushes_setup_boundaries_without_activation(self):
        case = self.fixture(); output = io.StringIO()
        with patch.object(timing.sys, 'stderr', output):
            error, _, activate = case.run_launcher(now_ms=case.deadline_ms + 1)
        self.assertIsInstance(error, ContractError)
        self.assertEqual(str(error), 'SLOT_ALREADY_MISSED')
        self.assertEqual([value['phase'] for value in events(output.getvalue())], [
            'PARSE_ARGUMENTS', 'VALIDATE_CONFIG', 'PREPARE_HIP_RUNTIME',
            'ENTER_SANDBOX', 'CHECK_FIXED_SLOT_DEADLINE'])
        activate.assert_not_called()

    def test_activation_and_torch_import_are_distinct_observed_boundaries(self):
        case = self.fixture(); output = io.StringIO()
        with patch.object(timing.sys, 'stderr', output):
            error, state, activate = case.run_launcher(now_ms=case.deadline_ms)
        self.assertIsInstance(error, deadline_fixture.ActivationReached)
        self.assertEqual(state['phase'], 'ACTIVATE_HIP_RUNTIME')
        phases = [value['phase'] for value in events(output.getvalue())]
        self.assertEqual(phases[-1], 'ACTIVATE_HIP_RUNTIME')
        self.assertNotIn('IMPORT_TORCH', phases)
        activate.assert_called_once_with(None)

    def test_complete_stub_worker_emits_all_phases_without_real_import_or_model(self):
        import builtins
        import laya_cell
        import sys
        case = self.fixture(); output = io.StringIO()
        sandbox = ModuleType('sandbox'); sandbox.enter = Mock(return_value={'synthetic': True})
        torch = ModuleType('torch'); torch.set_num_threads = Mock()
        predictor = ModuleType('laya_cell.resident_predict'); predictor.predict = Mock()
        original_import = builtins.__import__

        def no_real_model(name, *args, **kwargs):
            if name == 'torch':
                self.assertIs(sys.modules['torch'], torch)
            elif name.split('.')[0] in ('transformers', '_rocm_sdk_core', 'numpy'):
                self.fail('no real model dependency may be imported')
            return original_import(name, *args, **kwargs)

        with ExitStack() as stack:
            stack.enter_context(patch.dict(sys.modules, {
                'sandbox': sandbox, 'torch': torch, 'laya_cell.resident_predict': predictor}))
            stack.enter_context(patch.object(laya_cell, 'resident_predict', predictor, create=True))
            stack.enter_context(patch('builtins.__import__', no_real_model))
            stack.enter_context(patch.object(sys, 'argv', ['launch', 'predict', '--config',
                str(case.config_path), '--plan', str(case.plan_path), '--slot', '0']))
            stack.enter_context(patch.object(launch, 'config_check'))
            stack.enter_context(patch.object(launch, 'bootstrap_still_off'))
            stack.enter_context(patch.object(launch.faulthandler, 'enable'))
            stack.enter_context(patch.object(hip, 'prepare_worker', return_value=None))
            activate = stack.enter_context(patch.object(hip, 'activate'))
            prove = stack.enter_context(patch.object(hip, 'prove'))
            stack.enter_context(patch.object(launch.time, 'time', return_value=case.deadline_ms / 1000))
            stack.enter_context(patch.object(timing.sys, 'stderr', output))
            state = {}
            launch._main(state)
        self.assertEqual([value['phase'] for value in events(output.getvalue())], [
            'PARSE_ARGUMENTS', 'VALIDATE_CONFIG', 'PREPARE_HIP_RUNTIME', 'ENTER_SANDBOX',
            'CHECK_FIXED_SLOT_DEADLINE', 'ACTIVATE_HIP_RUNTIME', 'IMPORT_TORCH',
            'PROVE_TORCH_RUNTIME', 'RUN_PREDICT', 'WORKER_COMPLETED'])
        activate.assert_called_once_with(None)
        prove.assert_called_once_with(None, 'AFTER_TORCH_BEFORE_MODEL')
        torch.set_num_threads.assert_called_once_with(2)
        predictor.predict.assert_called_once()


class StartupFailureReasonTests(unittest.TestCase):
    def reaper(self):
        case = failure_fixture.WorkerFailureDiagnosticTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        return case

    def startup(self, context=None):
        output = io.StringIO()
        timing.emit_startup('PREDICTION_DISPATCHED', context, stream=output)
        self.assertTrue(output.getvalue().startswith(timing.STARTUP_PREFIX))
        return output.getvalue()

    def test_only_startup_keeps_original_missing_diagnostic_and_phase(self):
        log = self.startup().encode()
        record, _, _, _ = self.reaper().run_reap(2, log)
        self.assertEqual(record['reason'], 'NO_PROCESS_DIAGNOSTIC')
        self.assertEqual(record['stderr_tail'], log.decode())
        self.assertEqual(record['phase'], {'phase': 'VALIDATE_AND_LOAD'})

    def test_startup_does_not_hide_a_real_exception_before_or_after_it(self):
        title = 'laya_cell.integrity.ContractError: original worker error'
        for log in (self.startup() + title + '\n',
                    'Traceback (most recent call last):\n' + title + '\n' + self.startup(),
                    title + '\nRuntime advice.\n' + self.startup()):
            with self.subTest(log=log):
                record, _, _, _ = self.reaper().run_reap(1, log.encode())
                self.assertEqual(record['reason'], title)
                self.assertEqual(record['stderr_tail'], log)

    def test_mixed_legacy_messages_keep_last_nonstartup_fallback(self):
        log = ('INFO RuntimeError: ordinary mention\n'
               '  RuntimeError: indented source example\n' + self.startup() +
               'legacy process warning\n' + self.startup())
        record, _, _, _ = self.reaper().run_reap(2, log.encode())
        self.assertEqual(record['reason'], 'legacy process warning')
        title = 'RuntimeError: mentions ' + timing.STARTUP_PREFIX + 'as text'
        self.assertEqual(evaluate.worker_failure_diagnostic(title + '\n' + self.startup()), title)

    def test_bounded_tail_of_many_startup_rows_is_not_a_failure(self):
        log = (self.startup() * 10).encode()
        self.assertGreater(len(log), 1500)
        record, _, _, _ = self.reaper().run_reap(2, log)
        self.assertEqual(record['reason'], 'NO_PROCESS_DIAGNOSTIC')
        self.assertEqual(record['stderr_tail'], log[-1500:].decode())

    def test_single_observation_larger_than_tail_cannot_become_a_fragment_reason(self):
        # Within the emitter's fixed2KiB output bound, but larger than the
        # reaper's original1500-byte tail. No whole-log or extra byte read.
        interval = {name: 10**130 for name in ('started_at_ms', 'completed_at_ms', 'elapsed_ns')}
        log = self.startup({name: interval for name in
            ('admission', 'environment_preparation', 'popen')}).encode()
        self.assertGreater(len(log), 1500)
        self.assertLessEqual(len(log), timing.MAX_STARTUP_BYTES)
        record, _, _, _ = self.reaper().run_reap(2, log)
        self.assertEqual(record['reason'], 'NO_PROCESS_DIAGNOSTIC')
        self.assertEqual(record['stderr_tail'], log[-1500:].decode())

    def test_truncated_mixed_tail_retains_complete_error_and_termination_titles(self):
        for title in ('RuntimeError: real failure', 'KeyboardInterrupt: operator stop', 'SystemExit: 2'):
            log = (self.startup() * 10 + title + '\n' + self.startup()).encode()
            with self.subTest(title=title):
                record, _, _, _ = self.reaper().run_reap(1, log)
                self.assertEqual(record['reason'], title)
                self.assertEqual(record['stderr_tail'], log[-1500:].decode())
                # Also retain an exception title if the bounded tail happens
                # to begin at the exact start of a complete error line.
                self.assertEqual(evaluate.worker_failure_diagnostic(
                    title + '\n' + self.startup()), title)

    def test_long_real_single_line_keeps_original_available_tail_detail(self):
        log = ('RuntimeError: ' + 'real-error-coordinate-' * 1000 + '\n').encode()
        record, _, _, _ = self.reaper().run_reap(1, log)
        self.assertEqual(record['stderr_tail'], log[-1500:].decode())
        self.assertEqual(record['reason'], log[-1500:].decode().strip()[:240])

    def test_no_startup_never_expands_exception_search_beyond_original_tail(self):
        log = ('ValueError: earlier exception must stay outside reason scope\n' +
               'loader.CustomFailure: ' + 'x' * 2400 + '\n').encode()
        record, _, _, _ = self.reaper().run_reap(1, log)
        expected = log[-1500:].decode().strip()[:240]
        self.assertEqual(record['reason'], expected)
        self.assertNotIn('ValueError', record['reason'])
        self.assertEqual(record['stderr_tail'], log[-1500:].decode())

    def test_startup_keeps_the_same_nonstartup_tail_not_an_older_exception(self):
        earlier = 'ValueError: earlier exception must stay outside reason scope\n'
        last = 'loader.CustomFailure: ' + 'x' * 2400 + '\n'
        plain = (earlier + last).encode()
        log = (earlier + self.startup() * 3 + last + self.startup() * 10).encode()
        record, _, _, _ = self.reaper().run_reap(1, log)
        self.assertEqual(record['reason'], plain[-1500:].decode().strip()[:240])
        self.assertNotIn('ValueError', record['reason'])
        self.assertEqual(record['stderr_tail'], log[-1500:].decode())

    def test_virtual_nonstartup_tail_keeps_byte_cutoff_and_utf8_replacement(self):
        plain = (b'ValueError: older than byte cutoff\n' + b'failure-' * 300 +
                 b'\xff' + '\u20ac'.encode() * 300 + b'\xfe\n')
        log = plain + (self.startup() * 10).encode()
        record, _, _, _ = self.reaper().run_reap(1, log)
        self.assertEqual(record['reason'], plain[-1500:].decode('utf-8',errors='replace').strip()[:240])
        self.assertEqual(record['stderr_tail'], log[-1500:].decode('utf-8',errors='replace'))

    def test_unidentified_long_real_fragment_before_startup_is_not_discarded(self):
        log = ('RuntimeError: ' + 'E' * 20000 + '\n' + self.startup() * 10).encode()
        record, _, _, _ = self.reaper().run_reap(1, log)
        self.assertEqual(record['reason'], 'E' * 240)
        self.assertEqual(record['stderr_tail'], log[-1500:].decode())

    def test_late_parent_startup_rows_do_not_displace_real_error(self):
        title = 'RuntimeError: child failed before parent finished dispatch observation'
        for suffix in (self.startup() * 10, self.startup() * 20):
            log = (self.startup() * 5 + title + '\n' + suffix).encode()
            with self.subTest(suffix_bytes=len(suffix)):
                self.assertNotIn(title.encode(), log[-1500:])
                record, _, _, _ = self.reaper().run_reap(1, log)
                self.assertEqual(record['reason'], title)
                self.assertEqual(record['stderr_tail'], log[-1500:].decode())

    def test_reason_context_boundary_recovers_partial_startup_identity(self):
        self.assertEqual(evaluate._WORKER_STARTUP_LINE_BYTES, timing.MAX_STARTUP_BYTES)
        log = (self.startup() * 100).encode()
        self.assertGreater(len(log), evaluate._WORKER_DIAGNOSTIC_BYTES + timing.MAX_STARTUP_BYTES)
        record, _, _, _ = self.reaper().run_reap(2, log)
        self.assertEqual(record['reason'], 'NO_PROCESS_DIAGNOSTIC')
        self.assertEqual(record['stderr_tail'], log[-1500:].decode())


if __name__ == '__main__':
    unittest.main()
