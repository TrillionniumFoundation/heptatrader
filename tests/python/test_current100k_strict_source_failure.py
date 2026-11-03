"""Source-loss closeout preserves the original window and revokes current credit."""
import io
import shutil
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import test_current100k_weight_shadow as fixtures
from laya_cell import current100k_weight_shadow as W
from laya_cell import current100k_weight_shadow_eval as E


class StrictSourceFailureTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ShadowTests('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.worker, self.args, self.receipts = self.fixture.strict_fixture()
        self.plan = self.worker.plan
        self.output = self.worker.root
        self.source = self.worker.source_root
        self.first = self.worker.evaluate_snapshot(*self.args)
        self.assertEqual(self.first['observed_trades'], 1)
        self.original_hashes = self.source_hashes()

    def source_hashes(self):
        return {str(p.relative_to(self.source)): W.sha(p)
                for p in self.source.rglob('*') if p.is_file()}

    def cli(self, now, *, once=False, worker=None):
        argv = ['evaluator', '--root', str(self.source), '--strict-preopen',
                '--output-root', str(self.output)] + (['--once'] if once else [])
        with patch.object(sys, 'argv', argv), patch.object(W, 'clock_ms', return_value=now), \
                patch.object(E.time, 'sleep', side_effect=AssertionError('loop must exit')), \
                redirect_stdout(io.StringIO()):
            if worker is None:
                E.main()
            else:
                with patch.object(E, 'Evaluator', return_value=worker):
                    E.main()
        return W.read(self.output / 'assessment-strict.json')

    def assert_no_credit(self, result, *, final):
        self.assertEqual(result['state'], 'FINAL_INCOMPLETE' if final else 'PENDING')
        self.assertEqual(result['observed_trades'], 0)
        self.assertEqual(result['original_command_count'], 1)
        self.assertEqual(result['status_counts'], {'CENSORED': 1})
        self.assertEqual(result['prediction_coverage'], 0)
        self.assertFalse(result['current_replay_complete'])
        self.assertFalse(result['original_command_count_complete'])
        self.assertTrue(result['source_issues'])
        self.assertTrue(all(m['mae_usd'] is None and m['rmse_usd'] is None
                            for m in result['arm_metrics'].values()))
        self.assertTrue(all(not v['comparative_conditions_met']
                            for v in result['paired_comparisons'].values()))

    def test_source_failure_before_end_is_pending_and_recovery_requires_current_replay(self):
        result = E.strict_source_unavailable(self.output, self.plan, OSError('source gone'), 4000)
        self.assert_no_credit(result, final=False)
        self.assertFalse((self.output / 'strict-terminal-unavailable.json').exists())
        restored = self.worker.evaluate_snapshot(*self.args)
        self.assertEqual(restored['observed_trades'], 1)
        self.assertEqual(self.source_hashes(), self.original_hashes)

    def test_final_failure_retains_denominator_and_last_complete_snapshot(self):
        E.strict_source_unavailable(self.output, self.plan, OSError('gone'), 4000)
        result = E.strict_source_unavailable(self.output, self.plan, OSError('still gone'),
                                             self.plan['outcome_end_ms'])
        self.assert_no_credit(result, final=True)
        prior = result['last_completed_assessment']
        self.assertEqual(W.read(prior['path']), self.first)
        self.assertEqual(W.sha(prior['path']), prior['sha256'])
        self.assertEqual(prior['observed_trades'], 1)
        repeated = E.strict_source_unavailable(self.output, self.plan, OSError('later'),
                                               self.plan['outcome_end_ms'] + 999)
        self.assertEqual(repeated, result)
        self.assertEqual(self.source_hashes(), self.original_hashes)

    def test_prior_count_survives_missing_local_enumeration_without_fabricated_ids(self):
        for name in ('seen-commands', 'seen-unbound-commands', 'outcomes'):
            shutil.rmtree(self.output / name, ignore_errors=True)
        result = E.strict_source_unavailable(self.output, self.plan, OSError('gone'),
                                             self.plan['outcome_end_ms'])
        self.assert_no_credit(result, final=True)
        self.assertEqual(result['retained_known_command_count'], 0)
        self.assertEqual(result['retained_unenumerated_command_count'], 1)
        self.assertFalse((self.output / 'outcomes').exists())
        self.assertIn('UNRESOLVED_OR_CENSORED_ORIGINAL_COMMANDS', result['reasons'])

    def test_real_identity_failure_exits_at_original_end_and_revokes_old_credit(self):
        from laya_cell import financial_forward_source
        self.worker.config['oms_path'] = str(self.fixture.root / 'missing-oms.jsonl')
        self.worker.config['period_path'] = str(self.fixture.dataset)
        with patch.object(financial_forward_source, 'ingress_identity', return_value={'fixture': True}):
            result = self.cli(self.plan['outcome_end_ms'], worker=self.worker)
        self.assert_no_credit(result, final=True)
        self.assertEqual(result['source_failure']['type'], 'FileNotFoundError')
        self.assertIsNone(self.worker.last_result)
        self.assertEqual(self.source_hashes(), self.original_hashes)

    def test_source_directory_loss_restart_uses_retained_plan_without_registration(self):
        retained = W.read(self.output / 'review-plan-snapshot.json')
        shutil.rmtree(self.source)
        result = self.cli(self.plan['outcome_end_ms'])
        self.assert_no_credit(result, final=True)
        self.assertFalse(self.source.exists())
        self.assertEqual(W.read(self.output / 'review-plan-snapshot.json'), retained)
        self.assertFalse(retained['window_registered_by_review'])
        self.assertEqual(result['plan_id'], self.plan['plan_id'])

    def test_source_missing_before_end_does_not_publish_final_or_recreate_source(self):
        shutil.rmtree(self.source)
        result = self.cli(4000, once=True)
        self.assert_no_credit(result, final=False)
        self.assertFalse(self.source.exists())
        self.assertFalse((self.output / 'strict-terminal-unavailable.json').exists())
        self.assertEqual(W.read(self.output / 'evaluator-status.json')['state'], 'UNAVAILABLE_NO_SELECTION')

    def test_restart_restores_immutable_final_pointer_without_replay_or_retimestamp(self):
        final = E.strict_source_unavailable(self.output, self.plan, OSError('gone'),
                                            self.plan['outcome_end_ms'])
        (self.output / 'assessment-strict.json').unlink()
        with patch.object(E, 'Evaluator', side_effect=AssertionError('must not replay final')):
            result = self.cli(self.plan['outcome_end_ms'] + 500)
        self.assertEqual(result, final)
        self.assertEqual(self.source_hashes(), self.original_hashes)

    def test_nonthrowing_source_incomplete_snapshot_revokes_before_overwriting_success(self):
        source_issues = [{'reason': 'CURRENT_LEDGER_REPLAY_UNAVAILABLE'}]
        partial = self.worker.evaluate_snapshot(*self.args, issues=source_issues)
        self.assert_no_credit(partial, final=False)
        self.assertIn(source_issues[0], partial['source_issues'])
        self.assertEqual(W.read(partial['last_completed_assessment']['path']), self.first)
        final_args = (*self.args[:-1], self.plan['outcome_end_ms'])
        with patch.object(self.worker, 'step',
                          side_effect=lambda: self.worker.evaluate_snapshot(*final_args, issues=source_issues)):
            final = self.cli(self.plan['outcome_end_ms'], worker=self.worker)
        self.assert_no_credit(final, final=True)
        self.assertEqual(W.read(final['last_completed_assessment']['path']), self.first)
        self.assertEqual(self.source_hashes(), self.original_hashes)

    def test_local_prediction_rejection_is_censored_without_marking_global_replay_lost(self):
        (self.receipts / 'terminal.json').unlink()
        result = self.worker.evaluate_snapshot(*self.args)
        self.assertEqual(result['observed_trades'], 0)
        self.assertEqual(result['status_counts'], {'CENSORED': 1})
        self.assertTrue(result['current_replay_complete'])
        self.assertTrue(result['original_command_count_complete'])
        self.assertNotIn('STRICT_SOURCE_REPLAY_UNAVAILABLE',
                         [i['reason'] for i in result['source_issues']])
        self.assertFalse((self.output / 'strict-terminal-unavailable.json').exists())

    def test_first_launch_without_plan_cannot_register_or_invent_a_window(self):
        missing = self.fixture.root / 'never-registered'
        output = self.fixture.root / 'no-plan-review'
        with self.assertRaisesRegex(W.EvidenceError, 'INVALID_OR_OVERSIZED_FILE'):
            E.retained_review_plan(missing, output)
        self.assertFalse(missing.exists())
        self.assertFalse((output / 'review-plan-snapshot.json').exists())


if __name__ == '__main__':
    unittest.main()
