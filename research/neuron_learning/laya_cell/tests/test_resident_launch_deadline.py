"""Exercise launcher expiry ordering with temporary inputs and no GPU imports."""
import builtins
from contextlib import ExitStack
import ctypes
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

from laya_cell import resident_launch as launch
from laya_cell import resident_hip_runtime as hip
from laya_cell.integrity import ContractError, digest
from laya_cell.promotion import RULE


class ActivationReached(Exception):
    pass


class PredictionLaunchDeadlineTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.plan_path = self.root / 'plan.json'
        self.config_path = self.root / 'config.json'
        self.plan = {'start_ms': 1_000_000, 'rule': dict(RULE),
                     'candidate_directory': str(self.root / 'candidate'),
                     'parent_directory': str(self.root / 'parent')}
        self.plan['plan_id'] = digest(self.plan)
        self.plan_path.write_text(json.dumps(self.plan))
        self.deadline_ms = self.plan['start_ms'] + RULE['input_grace_ms']
        config = {key: str(self.root / key) for key in (
            'base_directory', 'baseline_directory', 'feed_directory',
            'initial_parent_directory', 'native_binary', 'seed_dataset',
            'runtime_directory', 'period_path', 'oms_path', 'export_directory',
            'dataset', 'parent_directory')}
        config.update(python=sys.executable, require_worker_sandbox=True,
                      output_directory=str(self.root / 'train' / 'output'))
        self.config_path.write_text(json.dumps(config))
        previous_umask = os.umask(0o077)
        self.addCleanup(os.umask, previous_umask)

    def run_launcher(self, *, now_ms, after_setup_ms=None, target='predict'):
        clock = [now_ms]
        sandbox = types.ModuleType('sandbox')

        def enter(_config):
            if after_setup_ms is not None:
                clock[0] = after_setup_ms
            return {'temporary_fixture': True}

        sandbox.enter = enter
        activate = Mock(side_effect=ActivationReached())
        real_import = builtins.__import__

        def cpu_import(name, *args, **kwargs):
            if name.split('.')[0] in {'torch', '_rocm_sdk_core', 'transformers'}:
                raise AssertionError('No real Torch, HIP or model import is allowed')
            return real_import(name, *args, **kwargs)

        argv = ['resident_launch', target, '--config', str(self.config_path)]
        if target == 'predict':
            argv += ['--plan', str(self.plan_path), '--slot', '0']
        state = {}
        with ExitStack() as stack:
            stack.enter_context(patch.object(sys, 'argv', argv))
            stack.enter_context(patch.dict(sys.modules, {'sandbox': sandbox}))
            stack.enter_context(patch('builtins.__import__', cpu_import))
            stack.enter_context(patch('time.time', side_effect=lambda: clock[0] / 1000))
            stack.enter_context(patch.object(launch, 'config_check'))
            stack.enter_context(patch.object(launch, 'bootstrap_still_off'))
            stack.enter_context(patch.object(launch.faulthandler, 'enable'))
            stack.enter_context(patch.object(ctypes, 'CDLL', side_effect=AssertionError('No native activation')))
            stack.enter_context(patch.object(hip, 'prepare_worker', return_value=None))
            stack.enter_context(patch.object(hip, 'activate', activate))
            try:
                launch._main(state)
            except BaseException as error:
                return error, state, activate
        self.fail('The fixture must stop before any real Torch import')

    def test_expired_window_refuses_before_hip_or_torch(self):
        error, state, activate = self.run_launcher(now_ms=self.deadline_ms + 1)
        self.assertIsInstance(error, ContractError)
        self.assertEqual(str(error), 'SLOT_ALREADY_MISSED')
        self.assertEqual(state['phase'], 'CHECK_FIXED_SLOT_DEADLINE')
        activate.assert_not_called()

    def test_setup_crossing_deadline_uses_fresh_time(self):
        error, _, activate = self.run_launcher(
            now_ms=self.deadline_ms - 1, after_setup_ms=self.deadline_ms + 1)
        self.assertIsInstance(error, ContractError)
        self.assertEqual(str(error), 'SLOT_ALREADY_MISSED')
        activate.assert_not_called()

    def test_exact_frozen_deadline_keeps_existing_strict_greater_than_boundary(self):
        error, state, activate = self.run_launcher(now_ms=self.deadline_ms)
        self.assertIsInstance(error, ActivationReached)
        self.assertEqual(state['phase'], 'ACTIVATE_HIP_RUNTIME')
        activate.assert_called_once_with(None)

    def test_frozen_rule_and_plan_identity_are_still_validated(self):
        for change, expected in (({'plan_id': '0' * 64}, 'PREDICT_PLAN_OR_SLOT_CHANGED'),
                                 ({'rule': {**RULE, 'input_grace_ms': RULE['input_grace_ms'] + 1}},
                                  'PROMOTION_RULE_CHANGED')):
            with self.subTest(expected=expected):
                self.plan_path.write_text(json.dumps({**self.plan, **change}))
                error, _, activate = self.run_launcher(now_ms=self.deadline_ms - 1)
                self.assertIsInstance(error, ContractError)
                self.assertEqual(str(error), expected)
                activate.assert_not_called()

    def test_training_has_no_prediction_deadline(self):
        error, state, activate = self.run_launcher(now_ms=self.deadline_ms + 1, target='train')
        self.assertIsInstance(error, ActivationReached)
        self.assertEqual(state['phase'], 'ACTIVATE_HIP_RUNTIME')
        activate.assert_called_once_with(None)


if __name__ == '__main__':
    unittest.main()
