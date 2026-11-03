"""Exact source-order export through real evaluator and source-loss paths.

These synthetic fixtures perform no model inference, ledger replay or orders.
The existing strict-preopen tests remain responsible for full terminal custody.
"""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from historical_training.common import canonical
from laya_cell import current100k_outcome_export as X
from laya_cell import current100k_preopen_evidence as P
from laya_cell import current100k_weight_shadow as W
from laya_cell import current100k_weight_shadow_eval as E


def plan_for(root):
    plan = {
        'schema': W.PLAN_SCHEMA, 'rule': copy.deepcopy(W.RULE),
        'claim_scope': 'PROSPECTIVE_VALUE_RESEARCH_ONLY', 'trade_authority': False,
        'automatic_selection': False, 'actor_update': False,
        'feature_profile': W.FEATURE_PROFILE, 'base_id': 'b' * 64,
        'period_sha256': '1' * 64, 'behavior_model_id': 'test-model',
        'producer_registration': {'sha256': '2' * 64},
        'training_dataset_path': str(root / 'synthetic-dataset.json'),
        'training_dataset_sha256': '3' * 64, 'training_mean_usd': -1.,
        'reward_config_path': str(root / 'synthetic-reward.json'),
        'reward_config_sha256': 'c' * 64, 'training_producer_manifest_sha256': 'd' * 64,
        'cross_producer_transfer': True, 'training_input_ids': ['e' * 64],
        'training_reward_ids': ['f' * 64], 'training_row_ids': ['a' * 64],
        'training_cutoff_ms': 1, 'labels_observed_at_ms': 2, 'registered_at_ms': 3,
        'input_start_ms': 10, 'input_end_ms': 10 + W.RULE['window_ms'],
        'outcome_end_ms': 10 + W.RULE['window_ms'] + W.RULE['closure_grace_ms'],
        'heads': {name: {'path': str(root / (name + '.safetensors')),
                        'sha256': W.digest(name), 'construction_kind': 'TEST_ONLY'}
                  for name in ('parent', 'patch')},
        'parent_arm': 'parent', 'cluster_policy': 'UTC_DAYS_MERGED_BY_OVERLAPPING_EPISODES',
        'bootstrap_repetitions': 2000, 'bootstrap_seed': 20261103,
        'numeric_profile': 'TORCH_CPU_FP32_SHARED_LINEAR_USD100_CLIP15',
        'encoder_backend': 'cpu_fp32', 'encoder_threads': 2, 'encoder_batch_size': 1,
        'description': '原始顺序，不重排',
    }
    plan['plan_id'] = W.digest(plan)
    W.validate_plan(plan)
    return plan


class OutcomeExportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / 'source'
        self.review = self.root / 'review'
        self.source.mkdir(mode=0o700)
        self.review.mkdir(mode=0o700)
        self.plan = plan_for(self.source)
        W.durable_once(self.source / 'plan.json', self.plan)
        W.durable_once(self.source / 'registration.json', {
            'plan_id': self.plan['plan_id'], 'plan_sha256': W.sha(self.source / 'plan.json'),
            'durable_at_ms': 4, 'local_receipt_only': True})
        # Construction is bypassed only to supply a synthetic replay directly.
        # The store, score_case, assessment, row publication and export are real.
        self.worker = object.__new__(E.Evaluator)
        self.worker.store = W.ShadowStore(self.source)
        self.worker.plan = self.plan
        self.worker.source_root = self.source
        self.worker.root = self.review
        self.worker.strict_review = True
        self.worker.assessment_name = 'assessment-strict.json'
        preopen = object.__new__(P.PreopenEvidence)
        preopen.study_root = self.source
        preopen.plan = self.plan
        preopen.activation_sha256 = '4' * 64
        preopen.config_sha256 = '5' * 64
        preopen.root = self.source / 'preopen'
        self.worker.preopen = preopen
        W.durable_once(self.review / 'strict-review-provenance.json', preopen.provenance())
        E.retained_review_plan(self.source, self.review)
        self.binding = {
            'request_id': 'original-request', 'request_started_at_ms': 1000,
            'request_sha256': '6' * 64,
            'command': {'command_id': 'middle-bound', 'created_at_ms': 2000},
        }
        # The two unbound commands occur in ledger order, not directory order.
        self.report = {'rewards': [], 'execution_rewards': [
            {'instrument': 'EUR.USD', 'open_command_id': 'z-unbound'},
            {'instrument': 'EUR.USD', 'open_command_id': 'a-unbound'},
        ]}
        self.original = {str(p.relative_to(self.source)): p.read_bytes()
                         for p in self.source.rglob('*') if p.is_file()}

    def evaluate(self, now=3000):
        requests = {'original-request': {'request': {'started_at_ms': 1000, 'position_at_request': 0}}}
        return self.worker.evaluate_snapshot(
            requests, [self.binding], self.report, {},
            {'z-unbound': 2001, 'a-unbound': 2002}, {}, now)

    def snapshot(self, report):
        pointer = report['outcome_set_snapshot']
        path = self.review / pointer['relative_path']
        raw = path.read_bytes()
        value = json.loads(raw)
        self.assertEqual(pointer['sha256'], hashlib.sha256(raw).hexdigest())
        self.assertEqual(pointer['outcome_set_digest'], W.digest(value['rows']))
        self.assertEqual(pointer['row_count'], len(value['rows']))
        self.assertEqual(value['as_of_ms'], report['as_of_ms'])
        self.assertEqual(value['plan_id'], self.plan['plan_id'])
        self.assertEqual(path.name, W.digest(value) + '.json')
        self.assertEqual(raw, canonical(value) + b'\n')
        return value

    def negative_terminal_fixture(self):
        preopen = self.worker.preopen
        preopen.activation_path = self.source / 'activation.json'
        preopen.config_path = self.source / 'predictor-config.json'
        preopen.plan_sha256 = W.sha(self.source / 'plan.json')
        preopen.registration_sha256 = W.sha(self.source / 'registration.json')
        preopen.config = {'max_request_age_ms': 60000}
        W.durable_once(preopen.config_path, preopen.config)
        preopen.config_sha256 = W.sha(preopen.config_path)
        W.durable_once(preopen.activation_path, {'plan_id': self.plan['plan_id'],
                                               'predictor_config_sha256': preopen.config_sha256})
        preopen.activation_sha256 = W.sha(preopen.activation_path)
        protocol = {key: self.plan[key] for key in
                    ('plan_id', 'input_start_ms', 'input_end_ms', 'period_sha256', 'producer_registration')}
        protocol['study_plan_sha256'] = preopen.plan_sha256
        W.durable_once(preopen.root / 'protocol.json', protocol)
        request = {'request_id': 'original-request', 'started_at_ms': 1000,
                   'instrument': 'EUR.USD', 'period_sha256': self.plan['period_sha256'],
                   'position_at_request': 0, 'prompt': 'synthetic private input, never exported'}
        self.binding.update(request_sha256=W.digest(request),
                            producer_manifest_sha256=self.plan['producer_registration']['sha256'])
        self.binding['command'].update(kind='OPEN', instrument='EUR.USD', side='BUY')
        folder = preopen.root / 'requests' / request['request_id']
        W.durable_once(folder / 'input.json', {'plan_id': self.plan['plan_id'], 'request_item': {
            'request': request, 'request_sha256': W.digest(request),
            'producer_manifest_sha256': self.plan['producer_registration']['sha256']}})
        terminal = {'schema': 'heptatrader.preopen-request-terminal.v1', 'plan_id': self.plan['plan_id'],
                    'request_id': request['request_id'], 'request_sha256': W.digest(request),
                    'producer_manifest_sha256': self.plan['producer_registration']['sha256'],
                    'status': 'NO_PREDICTION_BEFORE_ORIGINAL_COMMAND', 'observed_at_ms': 2100,
                    'broker_orders_sent': 0, 'old_trial_modified': False}
        W.durable_once(folder / 'terminal.json', terminal)
        original = {'request_id': request['request_id'], 'request_sha256': W.digest(request),
                    'command_id': self.binding['command']['command_id']}
        W.durable_once(folder / 'original-command.json', original)
        linked = {'schema': 'heptatrader.preopen-research-command-binding.v1',
                  'plan_id': self.plan['plan_id'], **original, 'binding': self.binding,
                  'prediction_terminal': terminal['status'], 'publication': None,
                  'observed_at_ms': 2101, 'status': 'MISSING_OR_LATE_NO_CREDIT',
                  'before_original_command': False, 'production_eligible': False, 'broker_orders_sent': 0}
        W.durable_once(preopen.root / 'bindings' / (original['command_id'] + '.json'), linked)
        W.atomic(self.review / 'strict-review-provenance.json', preopen.provenance())
        return folder, terminal

    def test_real_evaluator_exports_original_order_and_unchanged_assessment(self):
        report = self.evaluate()
        snapshot = self.snapshot(report)
        self.assertEqual([r['command_id'] for r in snapshot['rows']],
                         ['middle-bound', 'z-unbound', 'a-unbound'])
        self.assertEqual(report['original_command_count'], 3)
        expected = E.assessment(self.plan, snapshot['rows'], 3000, training_labels_current=False)
        self.assertEqual({k: report[k] for k in expected}, expected)
        self.assertEqual(W.read(self.review / 'assessment-strict.json'), report)
        self.assertEqual(report['comparison_eligibility']['status'], 'UNKNOWN')
        self.assertIsNone(report['comparison_eligibility']['formal_success_possible_under_frozen_rule'])
        self.assertEqual(self.original, {str(p.relative_to(self.source)): p.read_bytes()
                         for p in self.source.rglob('*') if p.is_file()})

    def test_non_strict_evaluator_does_not_add_research_export(self):
        self.worker.strict_review = False
        report = self.evaluate()
        self.assertNotIn('outcome_set_snapshot', report)
        self.assertFalse((self.review / 'outcome-sets').exists())

    def test_observed_pending_and_censored_rows_are_exported_without_changes(self):
        rows = [
            {'command_id': 'observed', 'status': 'OBSERVED', 'opened_at_ms': 2000,
             'closed_at_ms': 2500, 'target_usd': 3., 'fee_usd': .2,
             'target_exceeds_training_support': False,
             'predictions': {'parent': 1., 'patch': 2.}},
            {'command_id': 'pending', 'status': 'PENDING', 'reason': 'awaiting'},
            {'command_id': 'censored', 'status': 'CENSORED', 'reason': '原始缺失'},
        ]
        report = E.assessment(self.plan, rows, 3000)
        report.update(outcome_set_digest=W.digest(rows), strict_preopen_terminal_required=True)
        before = copy.deepcopy((report, rows))
        pointer = X.write_outcome_snapshot(self.review, report, rows)
        value = self.snapshot({**report, 'outcome_set_snapshot': pointer})
        self.assertEqual(value['rows'], rows)
        self.assertEqual((report, rows), before)
        self.assertEqual(report['arm_metrics']['patch']['mae_usd'], 1.)
        self.assertEqual(report['original_command_count'], 3)
        self.assertEqual(report['observed_trades'], 1)
        self.assertEqual(report['prediction_coverage'], 1 / 3)
        raw = (self.review / pointer['relative_path']).read_bytes()
        self.assertIn(b'\\u539f', raw)
        self.assertNotIn('原始缺失'.encode(), raw)
        self.assertEqual(X.write_outcome_snapshot(self.review, report, rows), pointer)

    def test_reordered_list_or_changed_digest_is_rejected_without_publication(self):
        rows = [{'command_id': 'z'}, {'command_id': 'a'}]
        report = {'schema': 'heptatrader.current100k-weight-shadow-assessment.v1',
                  'plan_id': self.plan['plan_id'], 'as_of_ms': 3000,
                  'strict_preopen_terminal_required': True, 'outcome_set_digest': W.digest(rows)}
        with self.assertRaisesRegex(W.EvidenceError, 'ORDERED_DIGEST_MISMATCH'):
            X.write_outcome_snapshot(self.review, report, list(reversed(rows)))
        self.assertFalse((self.review / 'outcome-sets').exists())

    def test_failed_snapshot_publication_does_not_advance_assessment(self):
        first = self.evaluate()
        before = (self.review / 'assessment-strict.json').read_bytes()
        with patch.object(X.os, 'link', side_effect=OSError('synthetic link failure')):
            with self.assertRaisesRegex(OSError, 'synthetic link failure'):
                self.evaluate(3001)
        self.assertEqual((self.review / 'assessment-strict.json').read_bytes(), before)
        self.assertEqual(len(list((self.review / 'outcome-sets').iterdir())), 1)
        self.snapshot(first)
        self.snapshot(self.evaluate(3001))

    def test_source_loss_keeps_lower_bound_and_exports_no_observed_credit(self):
        first = self.evaluate()
        W.atomic(self.review / 'assessment-strict.json', {**first, 'original_command_count': 8})
        result = E.strict_source_unavailable(self.review, self.plan, ValueError('source lost'), 3001)
        snapshot = self.snapshot(result)
        self.assertEqual(result['original_command_count'], 8)
        self.assertEqual(result['retained_unenumerated_command_count'], 5)
        self.assertFalse(result['current_replay_complete'])
        self.assertEqual(result['observed_trades'], 0)
        self.assertEqual(len(snapshot['rows']), 3)
        self.assertTrue(all(r['status'] == 'CENSORED' for r in snapshot['rows']))
        self.assertTrue(all(v is None for m in result['arm_metrics'].values() for v in m.values()))

    def test_terminal_source_failure_retains_same_snapshot_across_restart(self):
        self.evaluate()
        end = self.plan['outcome_end_ms'] + 1
        result = E.strict_source_unavailable(self.review, self.plan, ValueError('source lost'), end)
        terminal = self.review / 'strict-terminal-unavailable.json'
        original_terminal = terminal.read_bytes()
        self.assertEqual(W.read(terminal)['assessment']['outcome_set_snapshot'], result['outcome_set_snapshot'])
        with patch.object(E, 'write_outcome_snapshot', side_effect=AssertionError('must not re-export final')):
            restarted = E.strict_source_unavailable(self.review, self.plan, ValueError('later failure'), end + 5000)
        self.assertEqual(restarted, result)
        self.assertEqual(terminal.read_bytes(), original_terminal)
        self.snapshot(restarted)

    def test_old_terminal_without_snapshot_is_retained_without_backfill(self):
        self.evaluate()
        end = self.plan['outcome_end_ms'] + 1
        E.strict_source_unavailable(self.review, self.plan, ValueError('source lost'), end)
        terminal = self.review / 'strict-terminal-unavailable.json'
        value = W.read(terminal)
        del value['assessment']['outcome_set_snapshot']
        W.atomic(terminal, value)
        original_terminal = terminal.read_bytes()
        with patch.object(E, 'write_outcome_snapshot', side_effect=AssertionError('no retrofit')):
            restarted = E.strict_source_unavailable(self.review, self.plan, ValueError('later'), end + 5000)
        self.assertNotIn('outcome_set_snapshot', restarted)
        self.assertEqual(terminal.read_bytes(), original_terminal)
        self.assertEqual(restarted['as_of_ms'], end)

    def test_helper_is_pinned_in_original_strict_review_provenance(self):
        provenance = self.worker.preopen.provenance()
        self.assertEqual(provenance['review_implementation_sha256']['current100k_outcome_export.py'],
                         W.sha(Path(X.__file__)))
        self.assertFalse(provenance['original_window_modified'])
        self.assertFalse(provenance['request_replayed'])

    def test_existing_snapshot_conflict_is_not_overwritten(self):
        report = self.evaluate()
        pointer = report['outcome_set_snapshot']
        snapshot = self.snapshot(report)
        path = self.review / pointer['relative_path']
        path.chmod(0o600)
        path.write_bytes(b'changed')
        with self.assertRaisesRegex(W.EvidenceError, 'IMMUTABLE_CONFLICT'):
            X.write_outcome_snapshot(self.review, report, snapshot['rows'])
        self.assertEqual(path.read_bytes(), b'changed')

    def test_symlink_or_invalid_contract_does_not_publish(self):
        elsewhere = self.root / 'elsewhere'
        elsewhere.mkdir()
        (self.review / 'outcome-sets').symlink_to(elsewhere, target_is_directory=True)
        report = {'schema': 'heptatrader.current100k-weight-shadow-assessment.v1',
                  'plan_id': self.plan['plan_id'], 'as_of_ms': 3000,
                  'strict_preopen_terminal_required': True, 'outcome_set_digest': W.digest([])}
        with self.assertRaisesRegex(W.EvidenceError, 'DIRECTORY_SYMLINK'):
            X.write_outcome_snapshot(self.review, report, [])
        self.assertEqual(list(elsewhere.iterdir()), [])
        for key, value in [('as_of_ms', True), ('plan_id', 'bad'), ('strict_preopen_terminal_required', False)]:
            with self.subTest(key=key), self.assertRaises(W.EvidenceError):
                X.write_outcome_snapshot(self.review, {**report, key: value}, [])

    def test_verified_negative_terminal_blocks_family_without_finishing_window(self):
        folder, _ = self.negative_terminal_fixture()
        report = self.evaluate()
        eligibility = report['comparison_eligibility']
        self.assertEqual(report['state'], 'PENDING')
        self.assertEqual(report['as_of_ms'], 3000)
        self.assertEqual(eligibility['status'], 'IRRECOVERABLY_INELIGIBLE')
        self.assertIs(eligibility['formal_success_possible_under_frozen_rule'], False)
        self.assertEqual(eligibility['permanent_unresolved_count_lower_bound'], 1)
        proof = eligibility['failure_proofs'][0]
        self.assertEqual(proof['terminal_sha256'], W.sha(folder / 'terminal.json'))
        self.assertEqual(proof['original_command_sha256'], W.sha(folder / 'original-command.json'))
        self.assertEqual(proof['proof_digest'], W.digest({k: v for k, v in proof.items() if k != 'proof_digest'}))
        self.assertNotIn('synthetic private input', json.dumps(proof))
        self.assertEqual(proof['command_id'], self.binding['command']['command_id'])
        self.assertFalse(eligibility['is_final_protocol_report'])
        snapshot = self.snapshot(report)
        original = E.assessment(self.plan, snapshot['rows'], 3000, training_labels_current=False)
        self.assertEqual({k: report[k] for k in original}, original)

    def test_unsupported_or_wrong_terminal_is_unknown_without_changing_diagnostics(self):
        folder, terminal = self.negative_terminal_fixture()
        for update in ({'status': 'REQUEST_EXPIRED_NO_BACKDATING'}, {'plan_id': '0' * 64}):
            with self.subTest(update=update):
                W.atomic(folder / 'terminal.json', {**terminal, **update})
                report = self.evaluate()
                self.assertEqual(report['state'], 'PENDING')
                self.assertEqual(report['status_counts'], {'CENSORED': 3})
                self.assertEqual(report['comparison_eligibility']['status'], 'UNKNOWN')
                self.assertIsNone(report['comparison_eligibility']['formal_success_possible_under_frozen_rule'])

    def test_negative_terminal_requires_exact_original_command_binding(self):
        folder, _ = self.negative_terminal_fixture()
        W.atomic(folder / 'original-command.json', {'request_id': 'other', 'request_sha256': '0' * 64,
                                                  'command_id': self.binding['command']['command_id']})
        report = self.evaluate()
        self.assertEqual(report['comparison_eligibility']['status'], 'UNKNOWN')
        self.assertEqual(report['comparison_eligibility']['evidence_errors'][0]['reason'],
                         'PERMANENT_FAILURE_EVIDENCE_UNAVAILABLE')
        self.assertEqual(report['original_command_count'], 3)

    def test_blocked_family_keeps_observed_diagnostic_metrics(self):
        self.negative_terminal_fixture()
        proof = self.worker.preopen.permanent_prediction_failure(self.binding, 3000)
        rows = [{'command_id': 'covered', 'status': 'OBSERVED', 'opened_at_ms': 2000,
                 'closed_at_ms': 2500, 'target_usd': 3., 'fee_usd': .2,
                 'target_exceeds_training_support': False, 'predictions': {'parent': 1., 'patch': 2.}},
                {'command_id': self.binding['command']['command_id'], 'status': 'CENSORED',
                 'reason': 'NO_DURABLE_PREDICTION_FOR_ORIGINAL_REQUEST'}]
        report = E.assessment(self.plan, rows, 3000)
        report.update(outcome_set_digest=W.digest(rows), current_replay_complete=True)
        before = copy.deepcopy(report)
        metadata = X.comparison_eligibility(self.plan, report, [proof])
        self.assertEqual(report, before)
        self.assertEqual(report['observed_trades'], 1)
        self.assertEqual(report['arm_metrics']['patch']['mae_usd'], 1.)
        self.assertEqual(report['paired_comparisons']['patch vs parent']['mae_gain_usd'], 1.)
        self.assertEqual(metadata['status'], 'IRRECOVERABLY_INELIGIBLE')

    def test_source_loss_is_unknown_and_preserves_prior_negative_diagnostic(self):
        self.negative_terminal_fixture()
        first = self.evaluate()
        self.assertEqual(first['comparison_eligibility']['status'], 'IRRECOVERABLY_INELIGIBLE')
        result = E.strict_source_unavailable(self.review, self.plan, ValueError('source lost'), 3001)
        self.assertEqual(result['comparison_eligibility']['status'], 'UNKNOWN')
        self.assertIsNone(result['comparison_eligibility']['formal_success_possible_under_frozen_rule'])
        prior = W.read(result['last_completed_assessment']['path'])
        self.assertEqual(prior['comparison_eligibility'], first['comparison_eligibility'])


if __name__ == '__main__':
    unittest.main()
