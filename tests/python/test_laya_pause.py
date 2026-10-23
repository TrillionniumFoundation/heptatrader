"""Mocked lifecycle checks; never control a real service or send a signal."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import hepta_laya_pause as pause

TIMER_OFF = {'ActiveState': 'inactive', 'UnitFileState': 'disabled'}
RUNNING = {'ActiveState': 'active', 'MainPID': '42', 'InvocationID': 'a' * 32,
           'UnitFileState': 'disabled', 'Restart': 'no'}
IDLE = {'ActiveState': 'inactive', 'MainPID': '0', 'UnitFileState': 'disabled'}
ARGV = ['/runtime/bin/python', '-B', '-m', 'historical_training.resident_resource_guard',
        '--config', '/configuration/trainer.json', '--role', 'trainer']


class TrainerPauseTests(unittest.TestCase):
    def test_idle_pause_disables_timer_across_reboot_and_is_idempotent(self):
        with patch.object(pause, 'control') as command, patch.object(pause, 'show',
                side_effect=[TIMER_OFF, IDLE] * 2), \
                patch.object(pause, 'suppress_restart') as restart, \
                patch.object(pause, 'request_drain') as drain:
            for _ in range(2):
                result = pause.pause_trainer()
                self.assertTrue(result['trainer_paused'])
            drain.assert_not_called()
            restart.assert_not_called()
            self.assertEqual(command.call_args_list,
                             [unittest.mock.call('disable', '--now', pause.TIMER),
                              unittest.mock.call('disable', pause.TRAINER)] * 2)

    def test_running_worker_gets_only_pidfd_bound_supervisor_usr1(self):
        with patch.object(pause, 'control') as command, patch.object(pause, 'show',
                side_effect=[TIMER_OFF, RUNNING, RUNNING]), \
                patch.object(pause, 'suppress_restart') as restart, \
                patch.object(pause, 'read_argv', return_value=ARGV), \
                patch.object(pause.os, 'pidfd_open', return_value=19) as opened, \
                patch.object(pause.os, 'close') as closed, \
                patch.object(pause.signal, 'pidfd_send_signal') as sent:
            result = pause.pause_trainer()
            self.assertFalse(result['trainer_paused'])
            self.assertTrue(result['natural_drain_requested'])
            self.assertEqual(command.call_args_list,
                             [unittest.mock.call('disable', '--now', pause.TIMER),
                              unittest.mock.call('disable', pause.TRAINER)])
            opened.assert_called_once_with(42)
            sent.assert_called_once_with(19, signal.SIGUSR1, None, 0)
            closed.assert_called_once_with(19)
            restart.assert_called_once_with()

    def test_restart_reload_failure_never_signals_current_supervisor(self):
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(pause, 'runtime_dropin', return_value=Path(folder) / pause.DROPIN_NAME), \
                patch.object(pause, 'control', side_effect=[None, None, pause.PauseError('reload failed')]), \
                patch.object(pause, 'show', side_effect=[TIMER_OFF, RUNNING]), \
                patch.object(pause, 'request_drain') as drain:
            with self.assertRaisesRegex(pause.PauseError, 'reload failed'):
                pause.pause_trainer()
            drain.assert_not_called()

    def test_restart_readback_failure_never_signals_current_supervisor(self):
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(pause, 'runtime_dropin', return_value=Path(folder) / pause.DROPIN_NAME), \
                patch.object(pause, 'control') as command, patch.object(pause, 'show',
                side_effect=[TIMER_OFF, RUNNING, {**RUNNING, 'Restart': 'on-failure'}]), \
                patch.object(pause, 'request_drain') as drain:
            with self.assertRaisesRegex(pause.PauseError, 'RESTART_NOT_SUPPRESSED'):
                pause.pause_trainer()
            drain.assert_not_called()
            self.assertEqual(command.call_args_list,
                             [unittest.mock.call('disable', '--now', pause.TIMER),
                              unittest.mock.call('disable', pause.TRAINER),
                              unittest.mock.call('daemon-reload')])

    def test_owned_restart_dropin_is_exact_and_repeatable(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'user' / (pause.TRAINER + '.d') / pause.DROPIN_NAME
            with patch.object(pause, 'runtime_dropin', return_value=path), \
                    patch.object(pause, 'control') as command, \
                    patch.object(pause, 'show', return_value=RUNNING):
                pause.suppress_restart()
                original = path.stat()
                pause.suppress_restart()
                self.assertEqual(path.read_bytes(), b'[Service]\nRestart=no\n')
                self.assertEqual(path.stat().st_ino, original.st_ino)
                self.assertEqual(command.call_args_list,
                                 [unittest.mock.call('daemon-reload')] * 2)

    def test_unknown_dropin_is_not_overwritten_or_reloaded(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / pause.DROPIN_NAME
            path.write_bytes(b'[Service]\nRestart=always\n')
            path.chmod(0o600)
            with patch.object(pause, 'runtime_dropin', return_value=path), \
                    patch.object(pause, 'control') as command:
                with self.assertRaisesRegex(pause.PauseError, 'DROPIN_CONFLICT'):
                    pause.suppress_restart()
                self.assertEqual(path.read_bytes(), b'[Service]\nRestart=always\n')
                command.assert_not_called()

    def test_matching_dropin_with_unknown_permissions_is_preserved_and_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / pause.DROPIN_NAME
            path.write_bytes(pause.DROPIN_CONTENT)
            path.chmod(0o644)
            with patch.object(pause, 'runtime_dropin', return_value=path), \
                    patch.object(pause, 'control') as command:
                with self.assertRaisesRegex(pause.PauseError, 'DROPIN_CONFLICT'):
                    pause.suppress_restart()
                self.assertEqual(path.read_bytes(), pause.DROPIN_CONTENT)
                self.assertEqual(path.stat().st_mode & 0o777, 0o644)
                command.assert_not_called()

    def test_unknown_fifo_dropin_refuses_without_waiting_for_a_writer(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / pause.DROPIN_NAME
            os.mkfifo(path, 0o600)
            probe = '''import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import hepta_laya_pause as pause
pause.runtime_dropin = lambda: Path(sys.argv[2])
def forbid_control(*args):
    raise AssertionError("service control forbidden in FIFO fixture")
pause.control = forbid_control
try:
    pause.suppress_restart()
except pause.PauseError as error:
    assert str(error) == "TRAINER_PAUSE_DROPIN_CONFLICT", error
else:
    raise AssertionError("FIFO accepted")
'''
            result = subprocess.run([sys.executable, '-B', '-c', probe,
                                     str(Path(pause.__file__).resolve().parent), str(path)],
                                    capture_output=True, text=True, timeout=3)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(path.is_fifo())

    def test_inactive_but_enabled_timer_is_not_a_persistent_pause(self):
        with patch.object(pause, 'control'), patch.object(pause, 'show',
                return_value={'ActiveState': 'inactive', 'UnitFileState': 'enabled'}), \
                patch.object(pause, 'request_drain') as drain:
            with self.assertRaisesRegex(pause.PauseError, 'PERSISTENTLY_DISABLED'):
                pause.pause_trainer()
            drain.assert_not_called()

    def test_enabled_service_cannot_be_reported_as_a_persistent_pause(self):
        with patch.object(pause, 'control'), patch.object(pause, 'show',
                side_effect=[TIMER_OFF, {**IDLE, 'UnitFileState': 'enabled'}]), \
                patch.object(pause, 'request_drain') as drain:
            with self.assertRaisesRegex(pause.PauseError, 'SERVICE_NOT_PERSISTENTLY_DISABLED'):
                pause.pause_trainer()
            drain.assert_not_called()

    def test_changed_invocation_never_receives_a_signal(self):
        with patch.object(pause, 'show', return_value={**RUNNING, 'InvocationID': 'b' * 32}), \
                patch.object(pause, 'read_argv', return_value=ARGV), \
                patch.object(pause.os, 'pidfd_open', return_value=19), \
                patch.object(pause.os, 'close') as closed, \
                patch.object(pause.signal, 'pidfd_send_signal') as sent:
            with self.assertRaisesRegex(pause.PauseError, 'IDENTITY_CHANGED'):
                pause.request_drain(RUNNING)
            sent.assert_not_called()
            closed.assert_called_once_with(19)

    def test_child_or_other_process_cannot_be_used_as_supervisor(self):
        with patch.object(pause, 'read_argv', return_value=['python', '-m', 'laya_cell.resident_launch']), \
                patch.object(pause.os, 'pidfd_open', return_value=19), \
                patch.object(pause.os, 'close') as closed, \
                patch.object(pause.signal, 'pidfd_send_signal') as sent:
            with self.assertRaisesRegex(pause.PauseError, 'ARGV_CHANGED'):
                pause.request_drain(RUNNING)
            sent.assert_not_called()
            closed.assert_called_once_with(19)

    def test_disable_failure_never_signals_the_running_trainer(self):
        with patch.object(pause, 'control', side_effect=pause.PauseError('failed')), \
                patch.object(pause, 'request_drain') as drain:
            with self.assertRaises(pause.PauseError):
                pause.pause_trainer()
            drain.assert_not_called()

    def test_public_cli_resolves_its_own_pause_helper(self):
        with tempfile.TemporaryDirectory() as folder:
            release = Path(folder)
            wrapper = release / 'heptatrader'
            wrapper.write_bytes((Path(__file__).resolve().parents[2] /
                                 'scripts/heptatrader-user').read_bytes())
            (release / 'hepta_laya_pause.py').write_text(
                'import json; print(json.dumps({"fixture_pause_called": True}))\n')
            result = subprocess.run(['bash', str(wrapper), 'training-pause'],
                                    capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), {'fixture_pause_called': True})

    def test_help_is_read_only_and_unknown_arguments_are_rejected(self):
        helper = Path(pause.__file__).resolve()
        with tempfile.TemporaryDirectory() as empty_path:
            for arguments, expected in ((['--help'], 0), (['--resume'], 2)):
                with self.subTest(arguments=arguments):
                    result = subprocess.run(['/usr/bin/python3', '-B', str(helper), *arguments],
                                            capture_output=True, text=True, timeout=5,
                                            env={**os.environ, 'PATH': empty_path})
                    self.assertEqual(result.returncode, expected, result.stderr)
                    self.assertNotIn('SYSTEMCTL_FAILED', result.stderr)


if __name__ == '__main__': unittest.main()
