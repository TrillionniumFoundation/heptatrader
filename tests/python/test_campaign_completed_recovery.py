"""Recovery exercises retained economic evidence, never a Broker or driver."""
from __future__ import annotations

import copy
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import hepta_paper_campaign as campaign
from hepta_evidence_io import EvidenceError, load_json, write_json
from paper_rollout_fixtures import RolloutFixture


class CompletedRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.fixture = RolloutFixture(self.root / 'fixture')
        self.store = campaign.CampaignStore(self.root / 'store', self.fixture.campaign,
                                             self.fixture.binary, self.fixture.harness)

    def tearDown(self):
        self.temporary.cleanup()

    def operation(self, stage):
        def execute(evidence):
            fixture = RolloutFixture(self.root / ('source-' + stage), stage)
            shutil.copytree(fixture.evidence, evidence)
        return execute

    def interrupt_completion(self, stage='canary'):
        real_write = write_json
        def interrupted(path, value, **kwargs):
            if path == self.store.state_path and value.get('active') is None:
                raise OSError('injected state commit failure')
            return real_write(path, value, **kwargs)
        with patch.object(campaign, 'write_json', side_effect=interrupted):
            with self.assertRaisesRegex(OSError, 'state commit failure'):
                self.store.run(stage, self.operation(stage))
        active = self.store.state()['active']
        self.assertEqual(active['status'], 'failed_or_interrupted')
        return self.store.root / 'attempts' / active['attempt']

    def recover_without_processes(self, stage):
        # Any subprocess call, including the wrapper/harness, is a regression.
        with patch.object(campaign.subprocess, 'run', side_effect=AssertionError('recovery must not execute a process')):
            return self.store.recover_completed(stage)

    def test_recover_after_receipt_before_state_commit(self):
        evidence = self.interrupt_completion()
        before = load_json(evidence / 'rollout-verification.json')
        receipt = self.recover_without_processes('canary')
        self.assertEqual(receipt, before)
        self.assertIsNone(self.store.status()['active'])
        self.assertFalse(receipt['paper_authorized'])
        self.assertFalse(receipt['live_authorized'])
        self.assertEqual(receipt['authorization_effect'], 'NONE')
        self.assertEqual(receipt, self.store.run('canary', lambda _: self.fail('must not resend')))

    def test_recover_complete_result_before_receipt_publication(self):
        evidence = self.interrupt_completion()
        (evidence / 'rollout-verification.json').unlink()
        receipt = self.recover_without_processes('canary')
        self.assertEqual(load_json(evidence / 'rollout-verification.json'), receipt)
        self.assertIsNone(self.store.state()['active'])

    def test_recovery_is_idempotent(self):
        self.interrupt_completion()
        first = self.recover_without_processes('canary')
        state = self.store.state_path.read_bytes()
        second = self.recover_without_processes('canary')
        self.assertEqual(first, second)
        self.assertEqual(state, self.store.state_path.read_bytes())

    def test_recovered_canary_can_continue_same_campaign(self):
        self.interrupt_completion()
        self.recover_without_processes('canary')
        self.store.run('pilot', self.operation('pilot'))
        self.store.run('extended', self.operation('extended'))
        self.assertEqual(set(self.store.status()['completed']), set(campaign.STAGES))

    def test_recovery_write_failure_keeps_fence_and_can_be_retried(self):
        self.interrupt_completion()
        state = self.store.state_path.read_bytes()
        real_write = write_json
        def interrupted(path, value, **kwargs):
            if path == self.store.state_path:
                raise OSError('injected recovery fsync failure')
            return real_write(path, value, **kwargs)
        with patch.object(campaign, 'write_json', side_effect=interrupted):
            with self.assertRaisesRegex(OSError, 'recovery fsync'):
                self.recover_without_processes('canary')
        self.assertEqual(state, self.store.state_path.read_bytes())
        with self.assertRaises(EvidenceError):
            self.store.run('canary', lambda _: self.fail('must not resend'))
        self.recover_without_processes('canary')
        self.assertIsNone(self.store.state()['active'])

    def test_incomplete_or_tampered_evidence_leaves_fence_unchanged(self):
        evidence = self.interrupt_completion()
        (evidence / 'snapshot.json').write_text('{}', encoding='utf-8')
        state = self.store.state_path.read_bytes()
        with self.assertRaises(EvidenceError):
            self.recover_without_processes('canary')
        self.assertEqual(state, self.store.state_path.read_bytes())

    def test_conflicting_receipt_is_not_overwritten(self):
        evidence = self.interrupt_completion()
        receipt_path = evidence / 'rollout-verification.json'
        wrong = load_json(receipt_path)
        wrong['mutation_cycles'] = 999
        write_json(receipt_path, wrong, replace=True)
        before = receipt_path.read_bytes()
        with self.assertRaisesRegex(EvidenceError, 'retained receipt disagrees'):
            self.recover_without_processes('canary')
        self.assertEqual(before, receipt_path.read_bytes())
        self.assertIsNotNone(self.store.state()['active'])

    def test_no_attempt_cannot_be_manufactured(self):
        with self.assertRaisesRegex(EvidenceError, 'no retained attempt'):
            self.recover_without_processes('canary')
        self.assertFalse(self.store.state_path.exists())

    def test_mismatched_active_stage_is_not_cleared(self):
        self.store.run('canary', self.operation('canary'))
        self.interrupt_completion('pilot')
        before = self.store.state_path.read_bytes()
        with self.assertRaisesRegex(EvidenceError, 'active attempt does not match'):
            self.recover_without_processes('canary')
        self.assertEqual(before, self.store.state_path.read_bytes())

    def test_changed_binary_cannot_recover(self):
        self.interrupt_completion()
        self.fixture.binary.chmod(0o600)
        self.fixture.binary.write_bytes(b'not the admitted executable')
        with self.assertRaises(EvidenceError):
            self.recover_without_processes('canary')
        self.assertIsNotNone(self.store.state()['active'])

    def test_concurrent_recovery_is_rejected(self):
        self.interrupt_completion()
        with self.store.locked():
            with self.assertRaisesRegex(EvidenceError, 'another process'):
                self.recover_without_processes('canary')

    def test_corrupt_preceding_stage_blocks_recovery(self):
        self.store.run('canary', self.operation('canary'))
        self.interrupt_completion('pilot')
        record = self.store.state()['completed']['canary']
        (self.store.root / 'attempts' / record['attempt'] / 'callbacks.jsonl').write_text('{}\n')
        with self.assertRaises(EvidenceError):
            self.recover_without_processes('pilot')
        self.assertEqual(self.store.state()['active']['stage'], 'pilot')

    def test_invalid_attempt_path_is_rejected_without_state_change(self):
        self.interrupt_completion()
        state = copy.deepcopy(self.store.state())
        state['active']['attempt'] = 'canary-../../outside'
        write_json(self.store.state_path, state, replace=True)
        with self.assertRaisesRegex(EvidenceError, 'invalid attempt path'):
            self.recover_without_processes('canary')
        self.assertEqual(state, self.store.state())


if __name__ == '__main__':
    unittest.main()
