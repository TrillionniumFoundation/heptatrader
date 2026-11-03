"""Same-plan native role/calendar construction, without a service manager."""
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import activate_current100k_warm_stage as activation
from laya_cell.financial_forward_protocol import RULE
from laya_cell.integrity import digest


class FiniteActivationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(); self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        start = 1794558297469
        self.plan = {'rule': copy.deepcopy(RULE), 'trial': 5, 'synthetic': False,
            'candidate_created_at_ms': 1, 'registered_at_ms': start - 900000,
            'input_start_ms': start, 'input_end_ms': start + RULE['window_ms'],
            'outcome_end_ms': start + RULE['window_ms'] + RULE['closure_grace_ms'],
            'training_mean_usd': 0., 'registry_root': str(self.root / 'forward'),
            'worker_runtime_inventory_digest': 'a' * 64}
        self.plan['plan_id'] = digest(self.plan)
        self.stage = {'expected_trial': 5, 'forward_root': self.plan['registry_root'],
                      'release': str(self.root / 'release')}
        self.configs = {role: {'signing_role': role, 'role_key_path': '/existing/' + role + '.key',
            'guard_config': '/old/guard.json', 'guard_config_sha256': 'b' * 64,
            'python': '/selected/python', 'worker_runtime_inventory_digest': 'a' * 64}
            for role in ('registrar', 'predictor', 'evaluator')}
        self.priority = {'wrapper': '/new/immutable-guard/wrapper.json', 'wrapper_sha256': 'c' * 64}

    def test_same_plan_has_same_units_and_only_new_role_guard_fields_change(self):
        original = copy.deepcopy(self.configs)
        result = activation.role_candidates(self.stage, self.configs, self.plan, self.priority)
        self.assertEqual(activation.role_candidates(self.stage, self.configs, self.plan, self.priority), result)
        self.assertEqual(self.configs, original)
        for role in ('predictor', 'evaluator'):
            selected = result['roles'][role]['config']
            changed = {key for key in set(selected) | set(original[role]) if selected.get(key) != original[role].get(key)}
            self.assertEqual(changed, {'guard_config', 'guard_config_sha256'})
            path = result['roles'][role]['path']; raw = result['documents'][path]
            self.assertEqual(hashlib.sha256(raw).hexdigest(), result['roles'][role]['sha256'])
            self.assertEqual(json.loads(raw), selected)
        self.assertNotIn('registrar', result['roles'])
        self.assertFalse((self.root / 'forward').exists())

    def test_new_units_keep_finite_worker_bounds_and_never_name_original_trial4_units(self):
        result = activation.role_candidates(self.stage, self.configs, self.plan, self.priority)
        self.assertEqual(len(result['units']), 4)
        for name, data in result['units'].items():
            self.assertTrue(name.startswith('heptatrader-current100k-warm-trial5-'))
            text = data.decode()
            self.assertNotIn('heptatrader-current100k-forward-', text)
            if name.endswith('.service'):
                for value in ('Type=oneshot', 'Restart=no', 'TimeoutStartSec=240', 'TimeoutStopSec=20', 'KillMode=control-group'):
                    self.assertIn(value, text)
                self.assertNotIn('RemainAfterExit=yes', text)
            else:
                self.assertIn('Persistent=false', text)
                self.assertNotIn('OnBootSec=', text)
                self.assertNotIn('OnUnitActiveSec=', text)
                self.assertTrue(all('2026-' in line and line.endswith(' UTC') for line in text.splitlines() if line.startswith('OnCalendar=')))

    def test_terminal_evaluation_uses_actual_finite_worker_shutdown_margin(self):
        timer = activation.plan_timer('new-evaluator.service', self.plan, 'evaluate')
        expected = datetime.fromtimestamp((self.plan['outcome_end_ms'] + 261000 + 999) // 1000,
            timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
        self.assertIn('OnCalendar=' + expected, timer)
        self.assertEqual(activation.TERMINAL_EVALUATION_DELAY_MS, 240000 + 20000 + 1000)
        self.assertNotIn(expected, activation.plan_timer('new-predictor.service', self.plan, 'predict'))

    def test_different_root_trial_or_runtime_refuses_before_any_role_output(self):
        for key, value in (('trial', 6), ('registry_root', '/different/root'),
                           ('worker_runtime_inventory_digest', 'd' * 64)):
            changed = {**self.plan, key: value}
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'SIGNED_PLAN_SCOPE_CHANGED'):
                activation.role_candidates(self.stage, self.configs, changed, self.priority)
        self.assertFalse((self.root / 'forward').exists())




class ActivationSequenceTests(unittest.TestCase):
    """Faults use private plan bytes and mocked service/process boundaries."""
    def setUp(self):
        import os
        from unittest.mock import patch
        import current100k_priority_activation as priority
        self.patching = patch
        previous = os.umask(0o077); self.addCleanup(os.umask, previous)
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve(); self.forward = self.root / 'forward'
        self.plan = {'plan_id': 'a' * 64, 'trial': 5, 'input_start_ms': 901000,
            'input_end_ms': 2000000, 'outcome_end_ms': 2100000}
        self.plan_path = self.forward / 'plans' / self.plan['plan_id'] / 'plan.json'
        self.stage = {'registration_request': '/pinned/request.json', 'registration_request_sha256': 'b' * 64,
            'explicit_argv': {'registrar': ['/original/registrar', '--mode', 'register']}}
        self.config = {'forward_root': str(self.forward)}; self.request = {'frozen': True}
        self.selection = {'activation_deadline_ms': 2000000, 'drain_timeout_seconds': 660}
        self.eligibility = {'state': 'FRESH_REGISTRATION_ELIGIBLE_NOW_NO_WRITE', 'target_registration_state': 'EMPTY'}
        self.clock = [1.0]; self.effects = {}
        for target, name, replacement in ((activation.time, 'time', lambda: self.clock[0]),
            (activation.time, 'monotonic', lambda: self.clock[0])):
            item = patch.object(target, name, side_effect=replacement); item.start(); self.addCleanup(item.stop)
        def registrar(*args, **kwargs):
            self.plan_path.parent.mkdir(parents=True, exist_ok=True)
            self.plan_path.write_bytes(json.dumps(self.plan, sort_keys=True).encode())
            return {'state': 'REGISTERED_SAME_PRODUCER_WARM_PROFILE', 'plan_id': self.plan['plan_id'],
                'plan_sha256': hashlib.sha256(self.plan_path.read_bytes()).hexdigest(), 'registration_performed': True}
        fixtures = ((activation, 'stage_inputs', {'return_value': (self.stage, {'registrar': self.config}, self.request)}),
            (activation, 'pinned_json', {'return_value': self.selection}),
            (activation, 'cpu_checks', {}), (activation, 'empty_role_destinations', {}),
            (activation, 'precheck_registration', {'side_effect': lambda *a, **k: (self.request, self.eligibility)}),
            (activation, 'child', {'side_effect': registrar}),
            (activation, 'registration_state', {'side_effect': lambda *a: {'state': 'PUBLISHED', 'plan': self.plan}}),
            (activation, 'role_candidates', {'return_value': {'roles': {}, 'units': {}}}),
            (activation, 'install_roles', {}), (activation, 'enable_roles', {'return_value': {'verified': True}}),
            (priority, 'precheck_shared_priority', {}), (priority, 'prepare_priority', {'return_value': {'candidate': True}}),
            (priority, 'select_shared_priority', {'return_value': {'selected': True}}))
        for target, name, options in fixtures:
            item = patch.object(target, name, **options); self.effects[name] = item.start(); self.addCleanup(item.stop)

    def activate(self):
        return activation.activate(self.root / 'stage.json', 'c' * 64, self.root / 'selection.json', 'd' * 64, 2000000)

    def test_fresh_gate_failure_never_calls_registrar_or_creates_trial_root(self):
        self.effects['precheck_registration'].side_effect = ValueError('no current original producer')
        with self.assertRaisesRegex(ValueError, 'no current original producer'): self.activate()
        self.effects['child'].assert_not_called(); self.assertFalse(self.forward.exists())

    def test_slow_cpu_checks_spend_same_budget_before_any_registration(self):
        self.effects['cpu_checks'].side_effect = lambda *args: self.clock.__setitem__(0, 500.)
        with self.assertRaisesRegex(ValueError, 'INSUFFICIENT_REMAINING_BUDGET_NO_REGISTRATION'): self.activate()
        self.effects['child'].assert_not_called(); self.assertFalse(self.forward.exists())

    def test_unknown_registrar_reply_is_not_reissued_or_activated(self):
        self.effects['child'].side_effect = TimeoutError('original result unknown')
        with self.assertRaisesRegex(TimeoutError, 'original result unknown'): self.activate()
        self.effects['child'].assert_called_once()
        for name in ('registration_state', 'prepare_priority', 'install_roles', 'select_shared_priority', 'enable_roles'):
            self.effects[name].assert_not_called()

    def test_first_registration_returning_after_original_start_cannot_start_activation(self):
        original = self.effects['child'].side_effect
        def delayed(*args, **kwargs):
            value = original(*args, **kwargs); self.clock[0] = 902.; return value
        self.effects['child'].side_effect = delayed
        with self.assertRaisesRegex(ValueError, 'WAKE_DEADLINE_EXPIRED'): self.activate()
        self.assertEqual(json.loads(self.plan_path.read_bytes()), self.plan)
        for name in ('prepare_priority', 'install_roles', 'select_shared_priority', 'enable_roles'):
            self.effects[name].assert_not_called()

    def test_first_activation_shares_original_start_deadline_with_file_and_unit_work(self):
        self.activate()
        budget = self.effects['install_roles'].call_args.args[-1]
        self.assertEqual(budget.deadline_ms, self.plan['input_start_ms'] - 1)
        self.assertIs(self.effects['prepare_priority'].call_args.kwargs['remaining_seconds'].__self__, budget)
        self.assertIs(self.effects['select_shared_priority'].call_args.kwargs['remaining_seconds'].__self__, budget)
        self.assertIs(self.effects['enable_roles'].call_args.args[-1], budget)
        self.effects['cpu_checks'].assert_called_once()
        self.assertEqual(self.effects['child'].call_count, 1)

    def test_published_late_resume_calls_formal_sync_then_keeps_the_original_window(self):
        self.eligibility = {'state': 'REGISTRATION_ALREADY_COMPLETE_VERIFIED_NO_WRITE'}
        self.clock[0] = 950.
        original = self.effects['child'].side_effect
        def published(*args, **kwargs):
            return {**original(*args, **kwargs), 'state': 'REGISTRATION_ALREADY_COMPLETE_DURABLE_VERIFIED', 'registration_performed': False}
        self.effects['child'].side_effect = published
        before = copy.deepcopy(self.plan); result = self.activate()
        self.effects['child'].assert_called_once()
        self.effects['empty_role_destinations'].assert_not_called(); self.effects['precheck_shared_priority'].assert_not_called()
        self.assertFalse(result['activation_before_input_start']); self.assertFalse(result['registration_performed'])
        self.assertFalse(result['future_validation_proven']); self.assertEqual(self.plan, before)
        self.assertEqual(json.loads(self.plan_path.read_bytes()), before)

    def test_activation_failure_leaves_same_plan_pending_for_later_inspection(self):
        self.effects['select_shared_priority'].side_effect = ValueError('cooperative drain timed out')
        with self.assertRaisesRegex(ValueError, 'cooperative drain timed out'): self.activate()
        status = json.loads((self.forward / 'activation-status.json').read_bytes())
        self.assertEqual(status['state'], 'REGISTERED_ACTIVATION_PENDING')
        self.assertEqual(status['input_start_ms'], self.plan['input_start_ms'])
        self.effects['enable_roles'].assert_not_called(); self.effects['child'].assert_called_once()



class StageIdentityTests(unittest.TestCase):
    def test_every_role_and_command_is_bound_to_the_same_new_stage(self):
        from unittest.mock import patch
        from historical_training.common import canonical, digest
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve(); release = root / 'release'; forward = root / 'forward'
            stage = {'schema': 'heptatrader.current100k-warm-flat-stage.v1',
                'state': 'STAGED_CPU_VERIFIED_NOT_REGISTERED', 'trial_registered': False,
                'registration_activation_profile': activation.ACTIVATION_PROFILE,
                'release': str(release), 'forward_root': str(forward), 'configs': {}, 'explicit_argv': {},
                'pythonpath': ':'.join(str(release / part) for part in ('research', 'learn', 'scripts'))}
            request = root / 'request.json'; request.write_bytes(b'{}\n')
            stage.update(registration_request=str(request), registration_request_sha256=hashlib.sha256(request.read_bytes()).hexdigest())
            configs = {}
            for role, mode in (('registrar', 'register'), ('predictor', 'predict'), ('evaluator', 'evaluate')):
                c = {'signing_role': role, 'role_key_path': '/key/' + role, 'worker_source_root': str(release),
                    'forward_root': str(forward), 'python': '/selected/python'}
                path = root / (role + '-' + digest(c) + '.json'); path.write_bytes(canonical(c) + b'\n')
                configs[role] = c; checksum = hashlib.sha256(path.read_bytes()).hexdigest()
                stage['configs'][role] = {'path': str(path), 'sha256': checksum}
                argv = [c['python'] if role == 'predictor' else '/usr/bin/python3', '-B',
                    str(release / 'bin/current100k-warm-launch.py'), '--config', str(path), '--sha256', checksum, '--mode', mode]
                if role == 'registrar': argv += ['--request', str(request), '--request-sha256', stage['registration_request_sha256']]
                stage['explicit_argv'][role] = argv
            path = root / 'WARM_STAGE.json'
            def inspect():
                path.write_bytes(canonical(stage) + b'\n')
                return activation.stage_inputs(path, hashlib.sha256(path.read_bytes()).hexdigest())
            with patch.object(activation, 'check_precheck_source') as source:
                observed, parsed, _ = inspect(); self.assertEqual(parsed, configs); self.assertEqual(observed, stage)
                source.assert_called_once_with(configs['registrar'])
                stage['explicit_argv']['predictor'][-1] = 'register'
                with self.assertRaisesRegex(ValueError, 'ARGV_CHANGED'): inspect()
                stage['explicit_argv']['predictor'][-1] = 'predict'
                stage['configs']['predictor'] = stage['configs']['registrar']
                with self.assertRaisesRegex(ValueError, 'ROLE_CONFIG_CHANGED'): inspect()


if __name__ == '__main__': unittest.main()
