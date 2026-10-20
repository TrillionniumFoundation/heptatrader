"""Behavioral checks for skipped replays and exact per-refresh source reuse."""
import copy
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from historical_training import consumer_checkpoint as checkpoint
from historical_training import export_current100k as exporter
from historical_training.common import atomic, canonical, digest, read, sha
from laya_cell.financial_value_data import ExactRequestIndex, exact_request
from laya_cell.integrity import ContractError


class ConsumerCheckpointTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.journal = self.root / 'journal'
        self.journal.mkdir(mode=0o700)
        self.segment = self.journal / 'producer-0000.jsonl'
        self.segment.write_bytes(b'{}\n')
        self.segment.chmod(0o600)
        self.health = self.journal / 'producer.health.json'
        atomic(self.health, {'at_ms': 1, 'written_records': 1, 'dropped': 0, 'error': None})
        self.oms = self.root / 'oms.jsonl'
        self.oms.write_bytes(b'{}\n')
        self.period = self.root / 'period.json'
        atomic(self.period, {'execution_domain': 'PAPER'})
        self.manifest = {'journal_directory': str(self.journal)}
        self.manifest_path = self.root / 'manifest.json'
        atomic(self.manifest_path, self.manifest)
        self.config = {'output_root': str(self.root / 'output'),
                       'period_path': str(self.period), 'period_sha256': sha(self.period),
                       'runtime_root': '/fixture', 'runtime_sha256': {'one.py': 'a' * 64},
                       'registration': {'manifest': str(self.manifest_path), 'sha256': sha(self.manifest_path)},
                       'reward_config': {'oms_path': str(self.oms)}}
        self.now = 1000
        self.future = 0
        self.calls = 0

    def evaluate(self, config, now_ms, *, source_observer):
        self.calls += 1
        source_observer(self.future)
        return {'components': {'EUR.USD': {'ledger_complete': True}},
                'rewards': [], 'source_prefix': {'bytes': self.oms.stat().st_size}}

    def run_export(self, issues=()):
        with patch.object(exporter, 'validate_config', return_value=self.manifest) as validate, \
             patch('laya_cell.resident_rewards.current', side_effect=self.evaluate), \
             patch('laya_cell.financial_forward_source.ingress',
                   return_value=(self.manifest, {}, [], list(issues))), \
             patch.object(exporter.time, 'time', return_value=self.now / 1000):
            result = exporter.export(self.config)
        validate.assert_called_once_with(self.config)
        return result

    def test_identical_input_skips_replay_without_rewriting_dataset(self):
        first = self.run_export()
        view = Path(first['dataset_path'])
        stamp = view.stat().st_mtime_ns
        self.now += 60_000
        second = self.run_export()
        self.assertEqual(self.calls, 1)
        self.assertTrue(second['evaluation_skipped'])
        self.assertEqual(second['incremental']['new_episodes'], 0)
        self.assertEqual(second['last_full_evaluation_at_ms'], first['last_full_evaluation_at_ms'])
        self.assertEqual(second['input_checked_at_ms'], self.now)
        self.assertEqual(view.stat().st_mtime_ns, stamp)
        self.assertEqual(second['dataset_sha256'], first['dataset_sha256'])

    def test_heartbeat_only_skips_but_business_health_error_is_current(self):
        self.run_export()
        atomic(self.health, {'at_ms': 99999, 'written_records': 1, 'dropped': 0, 'error': None})
        self.assertTrue(self.run_export()['evaluation_skipped'])
        atomic(self.health, {'at_ms': 99999, 'written_records': 1, 'dropped': 1, 'error': 'failed'})
        with self.assertRaisesRegex(ValueError, 'ATTRIBUTION_ISSUES'):
            self.run_export([{'reason': 'FAILED_PRODUCER'}])
        self.assertFalse((Path(self.config['output_root']) / 'input-checkpoint.json').exists())

    def test_binding_without_oms_change_and_fee_append_both_refresh(self):
        self.run_export()
        oms = self.oms.read_bytes()
        with self.segment.open('ab') as stream:
            stream.write(b'{"kind":"COMMAND_BINDING"}\n')
        self.assertFalse(self.run_export()['evaluation_skipped'])
        self.assertEqual(self.oms.read_bytes(), oms)
        with self.oms.open('ab') as stream:
            stream.write(b'{"kind":"COMMISSION"}\n')
        self.assertFalse(self.run_export()['evaluation_skipped'])
        self.assertEqual(self.calls, 3)

    def test_new_segment_even_without_size_change_is_observed(self):
        self.run_export()
        new = self.journal / 'producer-0001.jsonl'
        new.write_bytes(b'')
        new.chmod(0o600)
        self.assertFalse(self.run_export()['evaluation_skipped'])

    def test_future_fee_replayed_when_time_advances_without_new_bytes(self):
        self.future = self.now + 60_000
        first = self.run_export()
        self.assertFalse(first['input_checkpoint_reusable'])
        self.now += 60_000
        second = self.run_export()
        self.assertFalse(second['evaluation_skipped'])
        self.assertTrue(second['input_checkpoint_reusable'])
        self.now += 1000
        self.assertTrue(self.run_export()['evaluation_skipped'])
        self.assertEqual(self.calls, 2)

    def test_period_runtime_changes_clock_regression_and_expiry_refresh(self):
        self.run_export()
        for mutate in (
                lambda: self.config.update(runtime_sha256={'one.py': 'b' * 64}),
                lambda: self.config.update(period_sha256='c' * 64)):
            mutate()
            self.assertFalse(self.run_export()['evaluation_skipped'])
        self.now -= 1
        self.assertFalse(self.run_export()['evaluation_skipped'])
        self.now += checkpoint.REVERIFY_MS
        self.assertFalse(self.run_export()['evaluation_skipped'])
        self.assertEqual(self.calls, 5)

    def test_output_tamper_cannot_hit_checkpoint(self):
        first = self.run_export()
        Path(first['dataset_path']).write_text('{}\n')
        self.assertFalse(self.run_export()['evaluation_skipped'])
        self.assertEqual(self.calls, 2)

    def test_symlink_hardlink_and_permission_changes_rejected(self):
        self.run_export()
        self.segment.chmod(0o644)
        with self.assertRaisesRegex(ValueError, 'FILE_SCOPE'):
            self.run_export()
        self.segment.chmod(0o600)
        duplicate = self.root / 'hardlink'
        os.link(self.segment, duplicate)
        with self.assertRaisesRegex(ValueError, 'FILE_SCOPE'):
            self.run_export()
        duplicate.unlink()
        other = self.root / 'segment'
        self.segment.rename(other)
        self.segment.symlink_to(other)
        with self.assertRaisesRegex(ValueError, 'FILE_SCOPE'):
            self.run_export()

    def test_source_changed_mid_refresh_publishes_no_new_checkpoint(self):
        self.run_export()
        with self.oms.open('ab') as stream:
            stream.write(b'{}\n')
        original = self.evaluate
        def changing(*args, **kwargs):
            result = original(*args, **kwargs)
            with self.segment.open('ab') as stream:
                stream.write(b'{}\n')
            return result
        with patch.object(self, 'evaluate', side_effect=changing):
            with self.assertRaisesRegex(ValueError, 'INPUT_CHANGED_DURING_REFRESH'):
                self.run_export()
        self.assertFalse((Path(self.config['output_root']) / 'input-checkpoint.json').exists())

    def test_incremental_existing_receipt_cannot_change_or_disappear(self):
        root = self.root / 'incremental-output'
        row = {'episode_id': 'episode', 'row_id': 'old'}
        payload = {'rows': [row]}
        exporter.publish_incremental(root, payload, 16 * 1024**2)
        for rows in ([], [{'episode_id': 'episode', 'row_id': 'corrected'}]):
            with self.assertRaisesRegex(ValueError, 'REWRITTEN_OR_WITHDRAWN'):
                exporter.publish_incremental(root, {'rows': rows}, 16 * 1024**2)


class ExactRequestIndexTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'source.jsonl'
        self.requests = [
            {'request_id': str(i), 'prompt': 'prompt-' + str(i)}
            for i in range(3)]
        self.lines = [
            canonical({'kind': 'REQUEST', 'data': request}) + b'\n'
            for request in self.requests]
        self.path.write_bytes(b''.join(self.lines))
        self.path.chmod(0o600)

    def reward(self, index, prefix_count=None):
        count = index + 1 if prefix_count is None else prefix_count
        prefix = b''.join(self.lines[:count])
        request = self.requests[index]
        return {'input_id': digest(request), 'input_attribution': {
            'request_id': request['request_id'],
            'prompt_sha256': hashlib.sha256(request['prompt'].encode()).hexdigest(),
            'source_prefixes': [{'path': str(self.path), 'bytes': len(prefix),
                                 'sha256': hashlib.sha256(prefix).hexdigest()}]}}

    def test_all_different_prefixes_share_one_bounded_private_read(self):
        import hepta_decision_attribution as attribution
        rewards = [self.reward(i) for i in range(3)]
        with patch.object(attribution, 'read_private', wraps=attribution.read_private) as reader:
            index = ExactRequestIndex(rewards)
            actual = [exact_request(r, source_index=index) for r in rewards]
        self.assertEqual(actual, self.requests)
        self.assertEqual(reader.call_count, 1)
        self.assertEqual(index.prefix_reads, 1)
        self.assertEqual(reader.call_args.kwargs['prefix_bytes'], self.path.stat().st_size)

    def test_later_request_is_not_available_through_earlier_prefix(self):
        bad = self.reward(2, prefix_count=1)
        index = ExactRequestIndex([self.reward(2), bad])
        with self.assertRaisesRegex(ContractError, 'EXACT_SINGLE_REQUEST'):
            exact_request(bad, source_index=index)

    def test_wrong_prefix_hash_and_duplicate_request_rejected(self):
        bad = self.reward(0)
        bad['input_attribution']['source_prefixes'][0]['sha256'] = '0' * 64
        with self.assertRaisesRegex(ContractError, 'PREFIX_CHANGED'):
            ExactRequestIndex([bad])
        self.lines.append(self.lines[0])
        self.path.write_bytes(b''.join(self.lines))
        duplicate = self.reward(0, prefix_count=4)
        index = ExactRequestIndex([duplicate])
        with self.assertRaisesRegex(ContractError, 'EXACT_SINGLE_REQUEST'):
            exact_request(duplicate, source_index=index)

    def test_mutation_during_read_is_not_cached(self):
        import hepta_decision_attribution as attribution
        read_private = attribution.read_private
        def mutate(*args, **kwargs):
            value = read_private(*args, **kwargs)
            with self.path.open('ab') as stream:
                stream.write(b'{}\n')
            return value
        with patch.object(attribution, 'read_private', side_effect=mutate):
            with self.assertRaisesRegex(ContractError, 'CHANGED_DURING_READ'):
                ExactRequestIndex([self.reward(0)])


if __name__ == '__main__':
    unittest.main()
