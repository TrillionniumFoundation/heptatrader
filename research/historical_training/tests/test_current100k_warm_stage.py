"""CPU-only stage-boundary checks. Every output/plan path is temporary."""
import contextlib, copy, hashlib, io, json, os, sys, tempfile, types, unittest
from pathlib import Path
from unittest.mock import Mock, patch
import time
import precheck_current100k_registration as registration_precheck

from laya_cell import stage_current100k_warm_flat as stage
def raw(value): return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
def digest(value): return hashlib.sha256(raw(value)).hexdigest()
class StopBeforeEffect(BaseException): pass


class StageBoundaryTests(unittest.TestCase):
    def setUp(self):
        # The CLI owns its process umask; these in-process fixtures must restore ours.
        previous_umask = os.umask(0o077)
        os.umask(previous_umask)
        self.addCleanup(os.umask, previous_umask)
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve(); self.stack = contextlib.ExitStack(); self.addCleanup(self.stack.close)
        self.data = {'producer_manifest_sha256': 'p'*64, 'period_sha256': 'q'*64,
                     'rows': [{'behavior_scope': {'model_id': 'behavior'}}]}
        self.prior = self.plan(1)
    def plan(self, trial):
        from laya_cell.financial_forward_protocol import RULE
        p = {'rule': copy.deepcopy(RULE), 'trial': trial, 'synthetic': False,
             'candidate_created_at_ms': 1, 'registered_at_ms': 2, 'input_start_ms': 3,
             'input_end_ms': 3+RULE['window_ms'], 'outcome_end_ms': 3+RULE['window_ms']+RULE['closure_grace_ms'],
             'training_mean_usd': 0., 'producer_registration': {'manifest': '/original/manifest.json', 'sha256': 'p'*64},
             'period_sha256': 'q'*64, 'behavior_scope': {'behavior_model_id': 'behavior'},
             'training_input_ids': ['old-input'], 'training_reward_ids': ['old-reward'],
             'candidate_directory': str(self.root/'original-candidate')}
        p['plan_id'] = digest(p); path = self.root/'old-plans'/p['plan_id']/'plan.json'
        path.parent.mkdir(parents=True); path.write_bytes(raw(p)+b'\n'); return path
    def fixture(self, mode, check_only=False):
        import laya_cell
        from laya_cell.current100k_warm_flat import PROFILE
        role = {'predict': 'predictor', 'evaluate': 'evaluator', 'register': 'registrar'}[mode]
        guard = self.root/'guard.json'; guard.write_text('{}\n')
        c = {'signing_role': role, 'guard_config': str(guard), 'guard_config_sha256': stage.sha(guard),
             'warm_flat_profile': dict(PROFILE), 'producer_registration': json.loads(self.prior.read_text())['producer_registration'],
             'python': '/selected/python', 'forward_root': str(self.root/'new-forward'), 'period_path': '/period',
             'oms_path': '/oms', 'period_sha256': 'q'*64, 'attribution_directory': '/original-attribution', 'worker_source_root': '/immutable-runtime',
             'worker_read_only_paths': [], 'role_key_path': '/this-role.key', 'witness_trust_path': '/trust.json'}
        config = self.root/(role+'-'+digest(c)+'.json'); config.write_bytes(raw(c)+b'\n')
        request = {'schema': 'heptatrader.current100k-warm-registration-request.v1', 'expected_trial': 2,
                   'registration_scope': 'SAME_PRODUCER_REFIT', 'input_start_delay_ms': 900000,
                   'registration_not_before_ms': json.loads(self.prior.read_text())['outcome_end_ms'],
                   'input_sha256': {str(self.prior): stage.sha(self.prior)}, 'legacy_plan_paths': [str(self.prior)],
                   'legacy_plans_roots': [str(self.prior.parent.parent)], 'prior_plan_path': str(self.prior),
                   'prior_candidate_path': str(self.root/'original-candidate/candidate.json'),
                   'candidate_directory': '/unchanged-centered-candidate', 'dataset_path': '/frozen54.json',
                   'prior_training_read_only_paths': ['/original-frozen-prefix'], 'existing_role_key_paths': {}}
        request_path = self.root/'request.json'; request_path.write_bytes(raw(request)+b'\n')
        effects = {'runtime': Mock(), 'guard': Mock(return_value={}), 'hip': Mock(), 'trust': Mock(), 'producer': Mock(),
                   'sandbox': Mock(side_effect=StopBeforeEffect()), 'exec': Mock(side_effect=StopBeforeEffect()),
                   'write': Mock(side_effect=AssertionError('unexpected publication')),
                   'admission': Mock(return_value=contextlib.nullcontext(types.SimpleNamespace(read_only_paths=[])))}
        common = types.ModuleType('historical_training.common')
        common.read = lambda path: json.loads(Path(path).read_text())
        common.sha = stage.sha; common.digest = digest; common.write_once = effects['write']
        guard_module = types.ModuleType('historical_training.resident_resource_guard'); guard_module.verify_config = effects['guard']
        worker = types.ModuleType('laya_cell.current100k_forward_worker'); worker.validate_runtime = effects['runtime']
        hip = types.ModuleType('laya_cell.resident_hip_runtime'); hip.__file__ = '/immutable-runtime/learn/laya_cell/resident_hip_runtime.py'
        hip.check_guard_selection = effects['hip']; hip.worker_environment = lambda _: {'PIN': 'unchanged'}
        witness = types.ModuleType('laya_cell.financial_forward_witness'); witness.verify_trust = effects['trust']
        consumer = types.ModuleType('historical_training.current100k'); consumer.validate_config = effects['producer']
        sandbox = types.ModuleType('sandbox'); sandbox.enter = effects['sandbox']
        self.stack.enter_context(patch.dict(sys.modules, {m.__name__: m for m in (common, guard_module, worker, hip, witness, consumer, sandbox)}))
        self.stack.enter_context(patch.object(laya_cell, 'resident_hip_runtime', hip, create=True))
        self.stack.enter_context(patch.object(registration_precheck, 'check_precheck_source'))
        self.stack.enter_context(patch.object(registration_precheck, 'OriginalProducerAdmission', effects['admission']))
        self.stack.enter_context(patch.object(registration_precheck, 'registration_state', return_value={'state': 'EMPTY', 'plan': None}))
        self.stack.enter_context(patch.object(registration_precheck, 'running_producer',
            side_effect=lambda *_: {'request_activity': {'started_at_ms': int(time.time()*1000)}}))
        argv = ['launcher', '--config', str(config), '--sha256', stage.sha(config), '--mode', mode]
        if check_only: argv.append('--check-only')
        if mode == 'register': argv += ['--request', str(request_path), '--request-sha256', stage.sha(request_path)]
        self.stack.enter_context(patch.object(sys, 'argv', argv)); self.stack.enter_context(patch.object(os, 'execvpe', effects['exec']))
        module = types.ModuleType('warm_launcher_fixture')
        exec(compile(stage.LAUNCHER, '<warm-launcher-fixture>', 'exec'), module.__dict__)
        return module, effects, config
    def test_cpu_check_returns_without_sandbox_exec_or_output_root(self):
        module, effects, _ = self.fixture('predict', True); out = io.StringIO()
        with contextlib.redirect_stdout(out): module.main()
        proof = json.loads(out.getvalue()); self.assertFalse(proof['gpu_loaded']); self.assertFalse(proof['worker_admission_granted'])
        for key in ('sandbox', 'exec', 'write'): effects[key].assert_not_called()
        self.assertFalse((self.root/'new-forward').exists())
    def test_full_registration_precheck_checks_inputs_without_output_or_sandbox(self):
        module, effects, _ = self.fixture('register'); sys.argv.append('--registration-precheck')
        out = io.StringIO()
        with contextlib.redirect_stdout(out): module.main()
        proof = json.loads(out.getvalue())
        self.assertEqual(proof['state'], 'FRESH_REGISTRATION_ELIGIBLE_NOW_NO_WRITE')
        self.assertFalse(proof['registration_performed']); effects['producer'].assert_called_once()
        for key in ('sandbox', 'exec', 'write'): effects[key].assert_not_called()
        self.assertFalse((self.root/'new-forward').exists())
    def test_cpu_check_does_not_claim_registration_eligibility(self):
        module, effects, _ = self.fixture('register', True); out = io.StringIO()
        with contextlib.redirect_stdout(out): module.main()
        self.assertEqual(json.loads(out.getvalue())['state'], 'CPU_FORWARD_CLOSURE_VERIFIED')
        effects['producer'].assert_not_called()
    def test_registration_precheck_rejects_mixed_cpu_mode(self):
        module, effects, _ = self.fixture('register', True); sys.argv.append('--registration-precheck')
        with self.assertRaisesRegex(ValueError, 'MODE_CONFLICT'): module.main()
        for key in ('sandbox', 'exec', 'write'): effects[key].assert_not_called()
    def test_expired_wake_refuses_before_root_or_producer_check(self):
        module, effects, _ = self.fixture('register'); sys.argv += ['--wake-deadline-ms', '1']
        with self.assertRaisesRegex(ValueError, 'WAKE_DEADLINE_EXPIRED'): module.main()
        for key in ('sandbox', 'producer', 'write'): effects[key].assert_not_called()
        self.assertFalse((self.root/'new-forward').exists())
    def test_parent_sync_failure_cannot_enter_sandbox_or_register(self):
        module, effects, _ = self.fixture('register')
        parent = self.root.stat(); fsync = os.fsync; observed = []
        def sync(fd):
            item = os.fstat(fd)
            if (item.st_dev, item.st_ino) == (parent.st_dev, parent.st_ino):
                observed.append(True); raise OSError('fixture parent fsync failure')
            return fsync(fd)
        with patch.object(os, 'fsync', side_effect=sync):
            with self.assertRaisesRegex(OSError, 'parent fsync failure'): module.main()
        self.assertEqual(observed, [True])
        effects['sandbox'].assert_not_called(); effects['write'].assert_not_called()
        self.assertEqual(list((self.root/'new-forward').glob('plans/*/plan.json')), [])
    def test_formal_published_reentry_does_not_skip_original_durability_path(self):
        module, effects, _ = self.fixture('register')
        with patch.object(registration_precheck, 'registration_state',
                return_value={'state': 'PUBLISHED', 'plan': {'plan_id': 'fixed', 'trial': 2}}):
            with self.assertRaises(StopBeforeEffect): module.main()
        effects['admission'].assert_not_called()
        effects['sandbox'].assert_called_once()
    def test_predict_keeps_original_worker_entry_and_hip_environment(self):
        module, effects, config = self.fixture('predict')
        with self.assertRaises(StopBeforeEffect): module.main()
        effects['exec'].assert_called_once_with('/selected/python', ['/selected/python', '-B', '-m',
            'laya_cell.current100k_forward_worker', '--config', str(config), '--mode', 'predict'], {'PIN': 'unchanged'})
        effects['sandbox'].assert_not_called(); effects['write'].assert_not_called()
    def test_same_producer_registration_keeps_original_sandbox(self):
        module, effects, _ = self.fixture('register')
        with self.assertRaises(StopBeforeEffect): module.main()
        effects['sandbox'].assert_called_once()
        effects['producer'].assert_called_once()
        self.assertEqual(effects['sandbox'].call_args.args[0]['legacy_capture'], '/frozen54.json')
        effects['exec'].assert_not_called(); effects['write'].assert_not_called()
    def test_appended_trial_refuses_before_any_registration_write(self):
        module, effects, _ = self.fixture('register'); self.plan(2)
        with self.assertRaisesRegex(ValueError, 'HISTORY_CHANGED_BEFORE_WRITE'): module.main()
        effects['sandbox'].assert_not_called(); effects['write'].assert_not_called()
        self.assertFalse((self.root/'new-forward').exists())
    def test_prepare_history_preserves_old_bytes_and_does_not_allocate_trial(self):
        before = self.prior.read_bytes(); prior, trial = stage.check_history([self.prior], self.prior, self.data)
        self.assertEqual(trial, 2); self.assertEqual(self.prior.read_bytes(), before)
        with self.assertRaisesRegex(ValueError, 'COMPLETE_ORIGINAL_TRIAL_HISTORY'):
            stage.check_history([self.prior, self.prior], self.prior, self.data)
        with self.assertRaisesRegex(ValueError, 'SAME_PRODUCER'):
            stage.check_history([self.prior], self.prior, {**self.data, 'producer_manifest_sha256': 'changed'})
    def test_registration_waits_for_exact_prior_outcome_end_before_any_output(self):
        module, effects, _ = self.fixture('register')
        end = json.loads(self.prior.read_text())['outcome_end_ms']
        with patch.object(module.time, 'time', return_value=(end-1)/1000):
            with self.assertRaisesRegex(ValueError, 'WAIT_PRIOR_OUTCOME_END'): module.main()
        self.assertFalse((self.root/'new-forward').exists())
        for key in ('sandbox', 'producer', 'write'): effects[key].assert_not_called()
        with patch.object(module.time, 'time', return_value=end/1000):
            with self.assertRaises(StopBeforeEffect): module.main()
        effects['producer'].assert_called_once(); effects['sandbox'].assert_called_once()

    def test_new_producer_history_keeps_budget_and_rejects_historical_overlap(self):
        data = {**self.data, 'producer_manifest_sha256': 'n'*64,
                'rows': [{'behavior_scope': {'model_id': 'behavior'}, 'input_id': 'new-input', 'reward_id': 'new-reward'}]}
        before = self.prior.read_bytes()
        prior, trial = stage.check_history([self.prior], self.prior, data, 'NEW_PRODUCER_SCOPE')
        self.assertEqual(trial, 2); self.assertEqual(self.prior.read_bytes(), before)
        with self.assertRaisesRegex(ValueError, 'CHANGED_REGISTERED_PRODUCER'):
            stage.check_history([self.prior], self.prior, self.data, 'NEW_PRODUCER_SCOPE')
        for field, old in [('input_id', 'old-input'), ('reward_id', 'old-reward')]:
            changed = copy.deepcopy(data); changed['rows'][0][field] = old
            with self.assertRaisesRegex(ValueError, 'HISTORICAL_TRAINING_OVERLAP'):
                stage.check_history([self.prior], self.prior, changed, 'NEW_PRODUCER_SCOPE')

    def test_new_scope_precheck_uses_completed_fit_and_existing_lineage_contract(self):
        module, effects, config_path = self.fixture('register')
        c = json.loads(config_path.read_text())
        c['producer_registration'] = {'manifest': '/new/manifest.json', 'sha256': 'n'*64}
        request_path = self.root / 'request.json'; request = json.loads(request_path.read_text())
        dataset = self.root / 'new-dataset.json'
        dataset.write_bytes(raw({'rows': [{'row_id': str(i)} for i in range(32)]}))
        request.update(registration_scope='NEW_PRODUCER_SCOPE', registration_not_before_ms=10,
                       dataset_path=str(dataset))
        request_path.write_bytes(raw(request))
        candidate = {'rows': 32, 'dataset_sha256': stage.sha(dataset), 'row_ids': [str(i) for i in range(32)], 'created_at_ms': 10}
        trial = types.ModuleType('historical_training.current100k_value_trial')
        trial.selected_rows = Mock()
        with patch('laya_cell.current100k_forward.validate_value_candidate', return_value=candidate), \
                patch.dict(sys.modules, {trial.__name__: trial}), \
                patch('laya_cell.current100k_forward._new_producer_lineage') as lineage:
            result, prior = registration_precheck.registration_inputs(c, request_path, stage.sha(request_path), now_ms=20)
            self.assertEqual(result['registration_scope'], 'NEW_PRODUCER_SCOPE')
            self.assertGreater(prior['outcome_end_ms'], 20)
            lineage.assert_called_once()
            self.assertEqual(lineage.call_args.args[0]['producer_registration'], c['producer_registration'])
            self.assertFalse((self.root/'new-forward').exists())
            with patch('laya_cell.current100k_forward.validate_value_candidate', return_value={**candidate, 'rows': 31}):
                with self.assertRaisesRegex(ValueError, 'COMPLETED_FIT_REQUIRED'):
                    registration_precheck.registration_inputs(c, request_path, stage.sha(request_path), now_ms=20)
    def test_outer_stage_parser_reaches_original_uid_guard(self):
        arguments = ['stage']
        for name in ('repo', 'fit-stage', 'prior-forward-stage', 'guard-config', 'forward-root', 'evidence-root'):
            arguments += ['--' + name, '/temporary/' + name]
        for name in ('expected-head', 'fit-stage-sha256', 'prior-forward-stage-sha256',
                     'guard-config-sha256', 'candidate-id', 'dataset-sha256'):
            arguments += ['--' + name, 'a' * 64]
        arguments += ['--legacy-plan', '/temporary/plan.json']
        with patch.object(sys, 'argv', arguments), patch.object(stage, 'need', side_effect=StopBeforeEffect()) as gate:
            with self.assertRaises(StopBeforeEffect): stage.main()
        self.assertEqual(gate.call_args.args[1], 'ORIGINAL_UNPRIVILEGED_USER_REQUIRED')
    def test_unit_has_hard_cgroup_bound_without_install_target_or_restart(self):
        text = stage.finite_unit('predict', '/new/config.json', '1'*64, '/new/runtime', '/selected/python', 5)
        for item in ('Type=oneshot', 'Restart=no', 'StartLimitIntervalSec=10s', 'StartLimitBurst=12',
                     'TimeoutStartSec=240', 'TimeoutStopSec=20', 'KillMode=control-group',
                     'SendSIGKILL=yes', '--mode predict', 'RestrictAddressFamilies=AF_UNIX',
                     'CPUQuota=400%', 'CPUWeight=500', 'MemoryLow=768M'): self.assertIn(item, text)
        self.assertNotIn('[Install]', text)
        evaluator = stage.finite_unit('evaluate', '/new/config.json', '1'*64, '/new/runtime', '/selected/python', 5)
        for item in ('CPUQuota=50%', 'CPUWeight=10', 'MemoryLow=0'): self.assertIn(item, evaluator)


if __name__ == '__main__': unittest.main()
