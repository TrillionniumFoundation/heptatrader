"""Synthetic fixtures exercise real source binding, sealing and monetary replay."""
import copy
import hashlib
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from historical_training.tests.test_current100k_forward_scope import ForwardScopeFixture
from laya_cell import current100k_weight_shadow as W
from laya_cell import current100k_weight_shadow_eval as E
from paired_net_100k_preflight.core import feature_text


class ShadowTests(ForwardScopeFixture, unittest.TestCase):
    def setUp(self):
        ForwardScopeFixture.setUp(self)
        self.study = self.root / 'study'
        self.study.mkdir()
        self.p = {
            'schema': W.PLAN_SCHEMA, 'rule': copy.deepcopy(W.RULE),
            'claim_scope': 'PROSPECTIVE_VALUE_RESEARCH_ONLY', 'trade_authority': False,
            'automatic_selection': False, 'actor_update': False,
            'feature_profile': W.FEATURE_PROFILE, 'base_id': 'b' * 64,
            'period_sha256': self.period, 'behavior_model_id': 'test-model',
            'producer_registration': {'sha256': self.manifest},
            'training_dataset_path': str(self.dataset),
            'training_dataset_sha256': W.sha(self.dataset), 'training_mean_usd': -1.,
            'reward_config_path': str(self.root / 'reward.json'), 'reward_config_sha256': 'c' * 64,
            'training_producer_manifest_sha256': 'd' * 64,
            'cross_producer_transfer': True,
            'training_input_ids': ['e' * 64], 'training_reward_ids': ['f' * 64],
            'training_row_ids': ['a' * 64], 'training_cutoff_ms': 1,
            'labels_observed_at_ms': 2, 'registered_at_ms': 3, 'input_start_ms': 10,
            'input_end_ms': 10 + W.RULE['window_ms'],
            'outcome_end_ms': 10 + W.RULE['window_ms'] + W.RULE['closure_grace_ms'],
            'heads': {a: {'path': str(self.root / (a + '.safetensors')),
                         'sha256': W.digest(a), 'construction_kind': 'TEST_ONLY'}
                      for a in ('parent', 'patch')},
            'parent_arm': 'parent', 'cluster_policy': 'UTC_DAYS_MERGED_BY_OVERLAPPING_EPISODES',
            'bootstrap_repetitions': 2000, 'bootstrap_seed': 20261103,
            'numeric_profile': 'TORCH_CPU_FP32_SHARED_LINEAR_USD100_CLIP15',
            'encoder_backend': 'cpu_fp32', 'encoder_threads': 2, 'encoder_batch_size': 1,
        }
        self.p['plan_id'] = W.digest(self.p)
        W.durable_once(self.study / 'plan.json', self.p)
        W.durable_once(self.study / 'registration.json', {
            'plan_id': self.p['plan_id'], 'plan_sha256': W.sha(self.study / 'plan.json'), 'durable_at_ms': 4})
        self.store = W.ShadowStore(self.study)
        self.item = copy.deepcopy(self.rows[0]['source_request'])
        self.binding = copy.deepcopy(self.rows[0]['source_binding'])
        self.binding.update(request_started_at_ms=1000, producer_manifest_sha256=self.manifest)
        self.binding['command']['created_at_ms'] = 2000
        self.reward = self.rows[0]['reward']
        self.f = {'schema': W.FEATURE_SCHEMA, 'feature_profile': W.FEATURE_PROFILE,
                  'request_id': self.item['request']['request_id'],
                  'request_sha256': self.item['request_sha256'], 'producer_manifest_sha256': self.manifest,
                  'period_sha256': self.period, 'base_id': self.p['base_id'],
                  'input_started_at_ms': 1000, 'computed_at_ms': 1002,
                  'backend': 'cpu_fp32', 'encoder_threads': 2, 'encoder_batch_size': 1,
                  'features': {s: [1.] + [0.] * 767 for s in W.SIDES},
                  'feature_text_sha256': {s: hashlib.sha256(feature_text(self.item['request'], s).encode()).hexdigest()
                                          for s in W.SIDES}}
        self.predictions = {}
        for arm, scaled in (('parent', .1), ('patch', -.01)):
            self.predictions[arm] = {'head_sha256': self.p['heads'][arm]['sha256'],
                                    'raw_scaled': {s: scaled for s in W.SIDES},
                                    'raw_usd': {s: 100 * scaled for s in W.SIDES},
                                    'values_usd': {s: 100 * scaled for s in W.SIDES}}

    def seal(self):
        with patch.object(W, 'clock_ms', side_effect=[1003, 1004, 1005]):
            return self.store.seal(self.item, self.f, self.predictions)

    def score(self, pred=None, **kw):
        args = dict(first_fill_ms=2001, closed_at_ms=3000, now_ms=3001)
        args.update(kw)
        return W.score_case(self.p, self.binding, pred, self.item, self.reward, **args)

    def evaluator_fixture(self, input_start_ms=1000):
        root = self.root / 'evaluation'
        config = self.root / 'fixture-replay.json'
        W.atomic(config, {'fixture': 'already replayed executions supplied to evaluate_snapshot'})
        self.p = copy.deepcopy(self.p)
        self.p.update(input_start_ms=input_start_ms,
                      input_end_ms=input_start_ms + W.RULE['window_ms'],
                      outcome_end_ms=input_start_ms + W.RULE['window_ms'] + W.RULE['closure_grace_ms'],
                      reward_config_path=str(config), reward_config_sha256=W.sha(config))
        self.p['plan_id'] = W.digest({k: v for k, v in self.p.items() if k != 'plan_id'})
        W.durable_once(root / 'plan.json', self.p)
        W.durable_once(root / 'registration.json', {
            'plan_id': self.p['plan_id'], 'plan_sha256': W.sha(root / 'plan.json'), 'durable_at_ms': 4})
        evaluator = E.Evaluator(root)
        self.store = evaluator.store
        return evaluator

    def execution_replay_fixture(self, *, complete, attributed=True):
        from laya_cell.resident_rewards import financial_episodes
        opening, closing = copy.deepcopy(self.reward['executions'])
        first_close = {**closing, 'execution_id': closing['execution_id'] + '-part1',
                       'quantity': '40000', 'commission': '0.4'}
        second_close = {**closing, 'execution_id': closing['execution_id'] + '-part2',
                        'quantity': '60000', 'commission': '0.6'}
        executions = [opening, first_close] + ([second_close] if complete else [])
        exact = {**self.reward['input_attribution'],
                 'input_id': self.item['request_sha256'], 'forecast_id': self.binding['decision_sha256'],
                 'model_id': self.p['behavior_model_id'], 'instrument': 'EUR.USD', 'side': 'BUY'}
        native = {'executions': executions,
                  'settlement_pending_command_ids': [] if complete else [closing['command_id']]}
        observed, blocked = financial_episodes(
            native, {opening['command_id']: exact} if attributed else {}, self.period, 'EUR.USD')
        report = {'execution_rewards': observed, 'rewards': [r for r in observed if r['learning_eligible']],
                  'blocked': blocked}
        times = dict(zip([e['execution_id'] for e in executions], (2001, 2500, 3000)))
        commands = {e['execution_id']: e['command_id'] for e in executions}
        fills = {opening['command_id']: 2001, closing['command_id']: 2500}
        return report, times, fills, commands

    def test_request_before_window_with_fill_inside_is_excluded_in_both_replay_states(self):
        evaluator = self.evaluator_fixture(input_start_ms=1001)
        for complete in (False, True):
            with self.subTest(complete=complete):
                report, times, fills, commands = self.execution_replay_fixture(complete=complete)
                result = evaluator.evaluate_snapshot(
                    {self.binding['request_id']: self.item}, [self.binding], report,
                    times, fills, commands, 3001)
                self.assertEqual(result['original_command_count'], 0)
                self.assertEqual(result['status_counts'], {})
                self.assertEqual(list((evaluator.root / 'seen-unbound-commands').glob('*.json')), [])

    def test_partial_close_never_adds_an_open_and_real_settlement_scores_once(self):
        evaluator = self.evaluator_fixture()
        self.seal()
        request = {self.binding['request_id']: self.item}
        report, times, fills, commands = self.execution_replay_fixture(complete=False)
        pending = evaluator.evaluate_snapshot(request, [self.binding], report, times, fills, commands, 2501)
        self.assertEqual(pending['original_command_count'], 1)
        self.assertEqual(pending['status_counts'], {'PENDING': 1})
        self.assertEqual(list((evaluator.root / 'seen-unbound-commands').glob('*.json')), [])
        report, times, fills, commands = self.execution_replay_fixture(complete=True)
        final = evaluator.evaluate_snapshot(
            request, [self.binding], report, times, fills, commands, self.p['outcome_end_ms'])
        self.assertEqual(final['original_command_count'], 1)
        self.assertEqual(final['status_counts'], {'OBSERVED': 1})
        self.assertEqual(final['arm_metrics']['patch']['mae_usd'], 0.)
        self.assertNotIn('UNRESOLVED_OR_CENSORED_ORIGINAL_COMMANDS', final['reasons'])
        self.assertEqual(list((evaluator.root / 'seen-unbound-commands').glob('*.json')), [])

    def test_genuinely_unbound_episode_keeps_one_open_through_close_and_source_loss(self):
        evaluator = self.evaluator_fixture()
        opening = self.binding['command']['command_id']
        for complete in (False, True):
            report, times, fills, commands = self.execution_replay_fixture(complete=complete, attributed=False)
            result = evaluator.evaluate_snapshot({}, [], report, times, fills, commands, 3001)
            self.assertEqual(result['original_command_count'], 1)
            self.assertEqual(result['status_counts'], {'CENSORED': 1})
            self.assertEqual({p.stem for p in (evaluator.root / 'seen-unbound-commands').glob('*.json')}, {opening})
        lost = evaluator.evaluate_snapshot({}, [], {}, {}, {}, {}, self.p['outcome_end_ms'])
        self.assertEqual(lost['original_command_count'], 1)
        self.assertEqual(lost['status_counts'], {'CENSORED': 1})
        self.assertIn('UNRESOLVED_OR_CENSORED_ORIGINAL_COMMANDS', lost['reasons'])

    def test_real_source_reward_recomputed_and_same_population(self):
        pred = self.seal()
        result = self.score(pred)
        self.assertEqual(result['status'], 'OBSERVED')
        self.assertEqual(result['target_usd'], -1.)
        self.assertEqual(result['predictions'], {'parent': 10., 'patch': -1.})
        report = E.assessment(self.p, [result], 4000)
        self.assertEqual(report['state'], 'PENDING')
        self.assertEqual(report['arm_metrics']['parent']['mae_usd'], 11.)
        self.assertEqual(report['arm_metrics']['patch']['rmse_usd'], 0.)
        self.assertFalse(report['financial_candidate_selected'])
        self.assertIsNone(report['paired_comparisons']['patch vs parent']['final_cluster_interval'])

    def test_encoder_and_head_devices_have_distinct_frozen_numeric_profiles(self):
        backend = 'LAYA_HIP_FP32_SINGLE_ROW_MAX2048_NO_TRUNCATION_V1'
        mixed = 'LAYA_HIP_FP32_ENCODER_TORCH_CPU_FP32_SHARED_LINEAR_USD100_CLIP15'
        self.assertEqual(W.numeric_profile(backend), mixed)
        self.assertEqual(W.numeric_profile(backend, 'TORCH_HIP_FP32'),
                         'TORCH_HIP_FP32_SHARED_LINEAR_USD100_CLIP15')
        with self.assertRaises(W.EvidenceError):
            W.numeric_profile('cpu_fp32', 'TORCH_HIP_FP32')
        with self.assertRaises(W.EvidenceError):
            W.numeric_profile('UNKNOWN_BACKEND')
        plan = copy.deepcopy(self.p)
        plan['encoder_backend'] = backend
        plan['hip_runtime_manifest_sha256'] = 'a' * 64
        plan['plan_id'] = W.digest({k: v for k, v in plan.items() if k != 'plan_id'})
        with self.assertRaises(W.EvidenceError):
            W.validate_plan(plan)
        plan['numeric_profile'] = mixed
        plan['plan_id'] = W.digest({k: v for k, v in plan.items() if k != 'plan_id'})
        W.validate_plan(plan)

    def test_cpu_cannot_carry_a_hip_publication_proof(self):
        self.f['hip_runtime_proof'] = {'schema': 'UNSELECTED_TEST_PROOF'}
        with self.assertRaises(ValueError):
            self.seal()

    def test_hip_manifest_and_before_publication_proof_are_required(self):
        self.p.update(encoder_backend='LAYA_HIP_FP32_SINGLE_ROW_MAX2048_NO_TRUNCATION_V1',
                      hip_runtime_manifest_sha256='a' * 64)
        self.p['numeric_profile'] = W.numeric_profile(self.p['encoder_backend'])
        self.p['plan_id'] = W.digest({k: v for k, v in self.p.items() if k != 'plan_id'})
        hip_root = self.root / 'hip-study'
        W.durable_once(hip_root / 'plan.json', self.p)
        W.durable_once(hip_root / 'registration.json', {
            'plan_id': self.p['plan_id'], 'plan_sha256': W.sha(hip_root / 'plan.json'), 'durable_at_ms': 4})
        self.store = W.ShadowStore(hip_root)
        self.f.update(backend=self.p['encoder_backend'], hip_runtime_manifest_sha256='b' * 64)
        with self.assertRaises(ValueError):
            self.seal()
        self.f['hip_runtime_manifest_sha256'] = 'a' * 64
        with self.assertRaises(ValueError):
            self.seal()
        from laya_cell.resident_hip_runtime import LIBRARIES
        self.f['hip_runtime_proof'] = {
            'schema': 'heptatrader.resident-hip-runtime-proof.v1', 'phase': 'BEFORE_PUBLISH',
            'target': 'predict', 'manifest_sha256': 'a' * 64, 'candidate_sha256': 'c' * 64,
            'pid': 123, 'worker_admission_granted': False, 'extra_gpu_probe_performed': False,
            'libraries': {name: {'loaded': True, 'sha256': 'c' * 64, 'device_major': 1,
                                 'device_minor': 1, 'inode': 123} for name in LIBRARIES}}
        pred = self.seal()
        self.assertEqual(self.score(pred)['status'], 'OBSERVED')

    def test_publication_attests_after_commit_fsync_and_is_idempotent(self):
        calls = []
        original = W.durable_once
        def record(path, value):
            original(path, value)
            calls.append(Path(path).name)
        def clock():
            if not calls:
                return 1003
            if len(calls) == 3:
                self.assertEqual(calls, ['features.json', 'prediction.json', 'commit.json'])
                return 1004
            self.assertEqual(calls, ['features.json', 'prediction.json', 'commit.json', 'publication.json'])
            return 1005
        with patch.object(W, 'durable_once', side_effect=record), patch.object(W, 'clock_ms', side_effect=clock):
            pred = self.store.seal(self.item, self.f, self.predictions)
        self.assertEqual(pred['seal_durable_completed_at_ms'], 1005)
        self.assertEqual(W.sha(pred['publication_path']), pred['publication_sha256'])
        with patch.object(W, 'clock_ms', side_effect=AssertionError('must not re-stamp')):
            self.assertEqual(self.store.seal(self.item, self.f, self.predictions), pred)

    def test_orphan_commit_cannot_be_turned_into_historical_prediction(self):
        pred = self.seal()
        Path(pred['publication_path']).unlink()
        self.assertIsNone(self.store.prediction(pred['request_id']))
        with self.assertRaisesRegex(ValueError, 'INTERRUPTED_SEAL'):
            self.store.seal(self.item, self.f, self.predictions)

    def test_tamper_and_mixed_feature_or_head_refused(self):
        for mutator in (lambda f: f.update(base_id='0' * 64),
                        lambda f: f['feature_text_sha256'].update(BUY='0' * 64),
                        lambda f: f['features']['BUY'].__setitem__(0, float('nan'))):
            f = copy.deepcopy(self.f)
            mutator(f)
            with patch.object(W, 'clock_ms', return_value=1003), self.assertRaises(ValueError):
                self.store.seal(self.item, f, self.predictions)
        pred = self.seal()
        path = Path(pred['publication_path']).parent / 'prediction.json'
        changed = W.read(path)
        changed['predictions']['patch']['values_usd']['BUY'] = 9.
        path.chmod(0o600)
        path.write_text(json.dumps(changed))
        with self.assertRaisesRegex(ValueError, 'COMMIT_CHANGED'):
            self.store.prediction(pred['request_id'])

    def test_seal_after_prepared_or_fill_censored_not_scored(self):
        pred = self.seal()
        for first in (1004, 1003):
            self.assertEqual(self.score(pred, first_fill_ms=first)['status'], 'CENSORED')
        self.binding['command']['created_at_ms'] = 1005
        self.assertEqual(self.score(pred)['reason'], 'NOT_COMMITTED_BEFORE_ORIGINAL_PREPARED')

    def test_pending_and_censored_never_supply_zero_targets(self):
        pred = self.seal()
        result = W.score_case(self.p, self.binding, pred, self.item, None, 2001, None, 3001)
        self.assertEqual(result['status'], 'PENDING')
        report = E.assessment(self.p, [result], 4000)
        self.assertEqual(report['observed_trades'], 0)
        self.assertIsNone(report['arm_metrics']['patch']['mae_usd'])
        final = W.score_case(self.p, self.binding, pred, self.item, None, 2001, None, self.p['outcome_end_ms'])
        self.assertEqual(final['status'], 'CENSORED')

    def test_development_identity_is_not_future_evidence(self):
        pred = self.seal()
        plan = copy.deepcopy(self.p)
        plan['training_input_ids'] = [self.item['request_sha256']]
        self.assertEqual(W.score_case(plan, self.binding, pred, self.item, self.reward, 2001, 3000, 3001)['reason'],
                         'DEVELOPMENT_INPUT_OVERLAP')

    def test_missing_fees_and_economic_tampering_never_score(self):
        pred = self.seal()
        for change in ({'fees_complete': False}, {'realized_net_usd': '500'}):
            reward = {**self.reward, **change}
            reward['reward_id'] = W.digest({k: v for k, v in reward.items() if k != 'reward_id'})
            result = W.score_case(self.p, self.binding, pred, self.item, reward, 2001, 3000, 3001)
            self.assertEqual(result['status'], 'CENSORED')

    def test_time_blocks_merge_cross_day_episodes(self):
        rows = [{'command_id': 'a', 'opened_at_ms': W.DAY_MS, 'closed_at_ms': 3 * W.DAY_MS},
                {'command_id': 'b', 'opened_at_ms': 2 * W.DAY_MS, 'closed_at_ms': 2 * W.DAY_MS + 1},
                {'command_id': 'c', 'opened_at_ms': 4 * W.DAY_MS, 'closed_at_ms': 4 * W.DAY_MS + 1}]
        self.assertEqual([len(x) for x in E.clusters(rows)], [2, 1])
        self.assertIsNone(E.paired_interval(E.clusters(rows), 'patch', 'parent', .05, 2000, 1))

    def test_repeated_same_day_samples_do_not_create_temporal_support(self):
        row = self.score(self.seal())
        rows = [{**row, 'command_id': 'sample-' + str(i)} for i in range(100)]
        report = E.assessment(self.p, rows, self.p['outcome_end_ms'])
        self.assertEqual(report['state'], 'FINAL_INCOMPLETE')
        self.assertEqual(report['time_blocks'], 1)
        self.assertIn('INSUFFICIENT_TIME_BLOCK_SUPPORT', report['reasons'])
        self.assertTrue(all(not c['comparative_conditions_met'] for c in report['paired_comparisons'].values()))

    def test_original_rule_and_transfer_lineage_cannot_be_changed(self):
        for field, value in (('cross_producer_transfer', False), ('rule', {**W.RULE, 'window_ms': 60000})):
            plan = {**self.p, field: value}
            plan['plan_id'] = W.digest({k: v for k, v in plan.items() if k != 'plan_id'})
            with self.assertRaises(ValueError):
                W.validate_plan(plan)

    def test_losing_or_missing_command_is_retained_when_binding_disappears(self):
        self.store.remember_commands([self.binding])
        self.assertEqual(set(self.store.remember_commands([])), {self.binding['command']['command_id']})

    def test_partial_arm_results_refused(self):
        with self.assertRaisesRegex(ValueError, 'ARM_COVERAGE'):
            W.validate_predictions(self.p, {'parent': self.predictions['parent']})

    def test_fixed_final_has_paired_multiple_comparison_intervals(self):
        row = self.score(self.seal())
        rows = []
        for day in (1, 4, 8, 11):
            for i in range(6):
                rows.append({**row, 'command_id': 'day-%d-%d' % (day, i),
                             'opened_at_ms': day * W.DAY_MS + i * 10000,
                             'closed_at_ms': day * W.DAY_MS + i * 10000 + 100,
                             'target_usd': -1. if i % 2 else 1.})
        report = E.assessment(self.p, rows, self.p['outcome_end_ms'])
        self.assertEqual(report['state'], 'FINAL_RESEARCH_COMPARISON')
        self.assertEqual(report['multiple_comparison_count'], 6)
        self.assertAlmostEqual(report['pair_alpha'], .05 / 6)
        interval = report['paired_comparisons']['patch vs parent']['final_cluster_interval']
        self.assertGreater(interval['mae_gain_usd'][0], 0)
        self.assertTrue(interval['exploratory_only'])
        comparison = report['paired_comparisons']['patch vs parent']
        self.assertFalse(comparison['huber_mixture_evidence']['threshold_met'])
        self.assertFalse(comparison['comparative_conditions_met'])
        self.assertFalse(report['financial_candidate_selected'])
        rows.append({'command_id': 'missing', 'status': 'CENSORED'})
        censored = E.assessment(self.p, rows, self.p['outcome_end_ms'])
        self.assertEqual(censored['state'], 'FINAL_INCOMPLETE')
        self.assertEqual(censored['observed_trades'], 24)
        self.assertTrue(all(not c['comparative_conditions_met'] for c in censored['paired_comparisons'].values()))

    def test_invalid_training_labels_revoke_all_comparative_conditions(self):
        report = E.assessment(self.p, [self.score(self.seal())], self.p['outcome_end_ms'],
                              training_labels_current=False)
        self.assertIn('TRAINING_LABEL_REVOKED_OR_CHANGED', report['reasons'])
        self.assertTrue(all(not c['comparative_conditions_met'] for c in report['paired_comparisons'].values()))

    def test_freeze_uses_current_clock_and_preserves_cross_producer_history(self):
        from historical_training.current100k import BEHAVIOR
        period = self.root / 'period.json'
        period.write_text(json.dumps({'execution_domain': 'PAPER'}))
        manifest = self.root / 'active-producer.json'
        manifest.write_text(json.dumps({'behavior_profile': BEHAVIOR, 'instrument': 'EUR.USD',
                                         'execution_domain': 'PAPER', 'period_sha256': W.sha(period)}))
        reward_config = self.root / 'reward-config.json'
        reward_config.write_text(json.dumps({'period_path': str(period), 'period_sha256': W.sha(period)}))
        heads = copy.deepcopy(self.p['heads'])
        for arm, head in heads.items():
            Path(head['path']).write_bytes(('synthetic-test-only-' + arm).encode())
            head['sha256'] = W.sha(head['path'])
        config = {**self.p, 'schema': 'heptatrader.current100k-weight-shadow-config.v1',
                  'period_sha256': W.sha(period),
                  'producer_registration': {'manifest': str(manifest), 'sha256': W.sha(manifest)},
                  'training_producer_manifest_sha256': self.manifest,
                  'reward_config_path': str(reward_config), 'reward_config_sha256': W.sha(reward_config),
                  'heads': heads, 'training_cutoff_ms': 1001, 'labels_observed_at_ms': 1002,
                  'training_input_ids': [r['input_id'] for r in self.rows],
                  'training_reward_ids': [r['reward_id'] for r in self.rows],
                  'training_row_ids': [r['row_id'] for r in self.rows]}
        destination = self.root / 'future'
        with patch.object(W, 'clock_ms', side_effect=[10000, 10001]):
            plan = W.freeze(config, destination)
        self.assertEqual(plan['input_start_ms'], 310000)
        self.assertEqual(plan['input_end_ms'] - plan['input_start_ms'], 14 * W.DAY_MS)
        self.assertEqual(plan['outcome_end_ms'] - plan['input_end_ms'], W.DAY_MS)
        self.assertTrue(plan['cross_producer_transfer'])
        self.assertFalse(plan['historical_trials_modified'])
        self.assertEqual(W.ShadowStore(destination).plan, plan)

    def strict_fixture(self, *, status='SEALED_AWAITING_ORIGINAL_COMMAND', observed=1006, elapsed=3.):
        evaluator = self.evaluator_fixture()
        prediction = self.seal()
        request = {self.binding['request_id']: self.item}
        report, times, fills, commands = self.execution_replay_fixture(complete=True)
        baseline = evaluator.evaluate_snapshot(request, [self.binding], report, times, fills, commands, 3001)
        self.assertEqual(baseline['observed_trades'], 1)
        source = evaluator.root
        preopen = source / 'preopen'
        config = {'study_plan_path': str(source / 'plan.json'),
                  'study_plan_sha256': W.sha(source / 'plan.json'),
                  'source_registration': self.p['producer_registration'],
                  'period_sha256': self.p['period_sha256'], 'state_root': str(preopen),
                  'max_request_age_ms': 60000, 'trade_authority': False,
                  'automatic_selection': False, 'actor_update': False}
        W.durable_once(source / 'predictor-config.json', config)
        W.durable_once(source / 'activation.json', {
            'plan_id': self.p['plan_id'],
            'predictor_config_sha256': W.sha(source / 'predictor-config.json')})
        protocol = {k: self.p[k] for k in
                    ('plan_id', 'input_start_ms', 'input_end_ms', 'period_sha256', 'producer_registration')}
        protocol.update(max_request_age_ms=60000, study_plan_sha256=config['study_plan_sha256'])
        W.durable_once(preopen / 'protocol.json', protocol)
        rid = self.binding['request_id'];cid = self.binding['command']['command_id']
        folder = preopen / 'requests' / rid
        W.durable_once(folder / 'input.json', {'plan_id': self.p['plan_id'], 'request_item': self.item})
        identity = {'plan_id': self.p['plan_id'], 'request_id': rid,
                    'request_sha256': self.binding['request_sha256']}
        W.durable_once(folder / 'attempt.json', {
            **identity, 'started_at_ms': 1003, 'deadline_ms': 61000, 'started_monotonic_ns': 0})
        W.durable_once(folder / 'terminal.json', {
            **identity, 'producer_manifest_sha256': self.manifest, 'status': status,
            'publication': prediction, 'publication_durability_observed_at_ms': observed,
            'elapsed_ms': elapsed, 'observed_at_ms': observed})
        W.durable_once(folder / 'original-command.json', {
            'request_id': rid, 'request_sha256': self.binding['request_sha256'], 'command_id': cid})
        W.durable_once(preopen / 'bindings' / (cid + '.json'), {
            **identity, 'binding': self.binding, 'publication': prediction,
            'prediction_terminal': status, 'status': 'PREOPEN_BOUND_RESEARCH',
            'before_original_command': True})
        strict = E.Evaluator(source, output_root=self.root / 'strict-review', strict_preopen=True)
        return strict, (request, [self.binding], report, times, fills, commands, 3001), folder

    def test_strict_review_rejects_late_failed_or_unknown_attempt_despite_durable_prediction(self):
        for status in ('PREDICTION_FAILED_OR_LATE', 'INTERRUPTED_UNKNOWN_NO_REPLAY'):
            with self.subTest(status=status):
                # Each receipt graph is independently immutable.
                if status != 'PREDICTION_FAILED_OR_LATE':
                    self.setUp()
                strict, args, _ = self.strict_fixture(status=status)
                report = strict.evaluate_snapshot(*args)
                self.assertEqual(report['observed_trades'], 0)
                self.assertEqual(report['original_command_count'], 1)
                self.assertEqual(report['status_counts'], {'CENSORED': 1})
                self.assertIn('STRICT_PREOPEN_TERMINAL:' + status, report['source_issues'][0]['reason'])

    def test_strict_review_preserves_original_evidence_and_survives_restart(self):
        strict, args, _ = self.strict_fixture()
        source = strict.source_root
        before = {str(p.relative_to(source)): W.sha(p) for p in source.rglob('*') if p.is_file()}
        first = strict.evaluate_snapshot(*args)
        self.assertEqual(first['observed_trades'], 1)
        self.assertTrue(first['strict_preopen_terminal_required'])
        restarted = E.Evaluator(source, output_root=strict.root, strict_preopen=True)
        self.assertEqual(restarted.evaluate_snapshot(*args)['outcome_set_digest'], first['outcome_set_digest'])
        after = {str(p.relative_to(source)): W.sha(p) for p in source.rglob('*') if p.is_file()}
        self.assertEqual(before, after)
        self.assertEqual(W.read(source / 'assessment.json')['observed_trades'], 1)
        self.assertEqual(W.read(strict.root / 'strict-review-provenance.json')['original_plan_sha256'],
                         W.sha(source / 'plan.json'))

    def test_strict_review_uses_monotonic_deadline_not_just_early_publication(self):
        strict, args, _ = self.strict_fixture(elapsed=60000.)
        report = strict.evaluate_snapshot(*args)
        self.assertEqual(report['observed_trades'], 0)
        self.assertIn('STRICT_PREOPEN_MONOTONIC_DEADLINE', report['source_issues'][0]['reason'])

    def test_strict_review_requires_completion_before_prepared(self):
        strict, args, _ = self.strict_fixture(observed=2000)
        report = strict.evaluate_snapshot(*args)
        self.assertEqual(report['observed_trades'], 0)
        self.assertIn('STRICT_PREOPEN_LATE_OR_DEADLINE', report['source_issues'][0]['reason'])

    def test_strict_review_missing_terminal_remains_visible_and_cache_identity_changes(self):
        strict, args, folder = self.strict_fixture()
        before = strict.preopen.identity()
        (folder / 'terminal.json').unlink()
        self.assertNotEqual(before, strict.preopen.identity())
        report = strict.evaluate_snapshot(*args)
        self.assertEqual(report['observed_trades'], 0)
        self.assertEqual(report['original_command_count'], 1)

    def test_strict_review_refuses_disagreeing_command_binding(self):
        strict, args, _ = self.strict_fixture()
        path = strict.preopen.root / 'bindings' / (self.binding['command']['command_id'] + '.json')
        data = W.read(path)
        data['status'] = 'MISSING_OR_LATE_NO_CREDIT'
        W.atomic(path, data)
        report = strict.evaluate_snapshot(*args)
        self.assertEqual(report['observed_trades'], 0)
        self.assertIn('STRICT_PREOPEN_BINDING_NO_CREDIT', report['source_issues'][0]['reason'])

    def test_strict_review_config_mutation_never_grants_credit(self):
        strict, args, _ = self.strict_fixture()
        config = W.read(strict.preopen.config_path)
        config['max_request_age_ms'] = 60001
        W.atomic(strict.preopen.config_path, config)
        report = strict.evaluate_snapshot(*args)
        self.assertEqual(report['observed_trades'], 0)
        self.assertIn('STRICT_PREOPEN_PINS_CHANGED', report['source_issues'][0]['reason'])

    def changed_receipt_invalidates_cached_observation(self, filename, change):
        strict, args, folder = self.strict_fixture()
        strict.config['oms_path'] = str(self.source)
        strict.config['period_path'] = str(self.dataset)
        from laya_cell import financial_forward_source
        with patch.object(financial_forward_source, 'ingress_identity', return_value={'fixture': 'unchanged'}), \
                patch.object(W, 'clock_ms', return_value=3001), \
                patch.object(strict, 'snapshot', side_effect=lambda: strict.evaluate_snapshot(*args)) as replay:
            self.assertEqual(strict.step()['observed_trades'], 1)
            self.assertTrue(strict.step()['replay_skipped'])
            self.assertEqual(replay.call_count, 1)
            stable = (W.sha(folder / 'terminal.json'),
                      W.sha(strict.preopen.root / 'bindings' / (self.binding['command']['command_id'] + '.json')))
            path = folder / filename
            value = W.read(path);value.update(change);W.atomic(path, value)
            result = strict.step()
            self.assertEqual(replay.call_count, 2)
            self.assertEqual(result['observed_trades'], 0)
            self.assertEqual(result['original_command_count'], 1)
            self.assertEqual(stable, (W.sha(folder / 'terminal.json'),
                             W.sha(strict.preopen.root / 'bindings' / (self.binding['command']['command_id'] + '.json'))))

    def test_changed_attempt_invalidates_cached_observed_without_terminal_change(self):
        self.changed_receipt_invalidates_cached_observation('attempt.json', {'deadline_ms': 999999})

    def test_changed_original_command_invalidates_cached_observed_without_terminal_change(self):
        self.changed_receipt_invalidates_cached_observation('original-command.json', {'command_id': 'different'})

    def test_strict_live_loop_exits_after_fixed_final_assessment(self):
        import sys
        strict, args, _ = self.strict_fixture()
        final_args = (*args[:-1], self.p['outcome_end_ms'])
        final = strict.evaluate_snapshot(*final_args)
        self.assertEqual(final['state'], 'FINAL_INCOMPLETE')
        argv = ['evaluator', '--root', str(strict.source_root), '--strict-preopen',
                '--output-root', str(strict.root)]
        with patch.object(sys, 'argv', argv), patch.object(E, 'Evaluator', return_value=strict), \
                patch.object(strict, 'step', return_value=final) as step, \
                patch.object(E.time, 'sleep', side_effect=AssertionError('final review must exit')):
            E.main()
            self.assertEqual(step.call_count, 1)


if __name__ == '__main__':
    unittest.main()
