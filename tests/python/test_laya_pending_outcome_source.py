from contextlib import ExitStack, contextmanager
import copy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'research' / 'neuron_learning'))
from laya_cell.integrity import ContractError, digest
from laya_cell import resident_data, resident_evaluate as evaluation
from laya_cell import resident_recent_source as recent
from laya_cell import resident_source_policy as policy


class PendingOutcomeSourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.feed = self.root / 'feed'
        self.paths = [self.feed / f'segment-{index:04d}' / 'quotes.jsonl' for index in range(3)]
        for path in self.paths:
            path.parent.mkdir(parents=True)
            path.write_text('x')
            path.chmod(0o600)
        self.period_sha = 'a' * 64
        self.config = {'feed_directory': str(self.feed), 'period_path': str(self.root / 'period.json'),
                       'period_sha256': self.period_sha}
        self.pins = [{'path': str(path), 'bytes': 1, 'sha256': str(index + 1) * 64,
                      'chain_head': str(index + 1) * 64, 'uncommitted_tail_bytes': 0,
                      'ended': index < 2} for index, path in enumerate(self.paths)]
        self.archived = self.pins[:2]
        self.pages = [{'path': str(self.root / 'archive-page.jsonl'), 'bytes': 1, 'sha256': 'e' * 64}]
        self.anchor = 1_000_000
        self.horizon = evaluation.RULE['horizon_ms']
        self.assertEqual(self.horizon, 300000)
        self.plan = {'candidate_id': 'c' * 64, 'start_ms': self.anchor,
                     'rule': copy.deepcopy(evaluation.RULE)}
        self.plan['plan_id'] = digest(self.plan)
        self.prediction = self.prediction_at(0, self.anchor)
        self.set_rows(self.complete_rows())

    def tearDown(self):
        self.tmp.cleanup()

    def quote(self, stamp, *, known=None, mid=1.1002):
        return {'t': stamp, 'known_at_ms': stamp + 1 if known is None else known,
                'bid': str(mid - 0.0001), 'ask': str(mid + 0.0001), 'mid': mid,
                'receipt': format(stamp, '064x')}

    def prediction_at(self, slot, anchor):
        value = {'candidate_id': self.plan['candidate_id'], 'plan_id': self.plan['plan_id'],
                 'slot': slot, 'anchor_ms': anchor, 'created_at_ms': anchor + 1000,
                 'trained_until_ms': anchor - 1, 'reference_mid': 1.1002,
                 'candidate_probs': [1 / 7] * 7, 'parent_probs': [1 / 7] * 7,
                 'candidate_act_escalate_probs': [0.5, 0.5], 'bootstrap_point_bps': 0.0,
                 'input': {'fixture': 'same immutable source'}}
        value['input_id'] = digest(value['input'])
        return value

    def complete_rows(self):
        return [self.quote(stamp) for stamp in range(self.anchor, self.anchor + self.horizon + 1, 30000)]

    def set_rows(self, rows):
        self.records = {
            self.paths[0]: [self.quote(100000), self.quote(130000)],
            self.paths[1]: [self.quote(self.anchor - 30000)] + [q for q in rows if q['t'] <= self.anchor + 150000],
            self.paths[2]: [q for q in rows if q['t'] > self.anchor + 150000],
        }

    def read_source(self, path, *, expected_prefix=None):
        index = self.paths.index(Path(path))
        if expected_prefix is not None:
            self.assertEqual(expected_prefix, self.pins[index])
            self.assertIn(expected_prefix, self.archived)
        return {'period_sha256': self.period_sha}, self.records[Path(path)], self.pins[index]

    def end_timestamp(self, pin):
        return max(quote['known_at_ms'] for quote in self.records[Path(pin['path'])]) + 1

    @contextmanager
    def sources(self):
        with ExitStack() as stack:
            stack.enter_context(patch('laya_cell.source_archive.inventory_index', return_value=(self.archived, self.pages)))
            stack.enter_context(patch.object(recent, 'inventory', return_value=(self.archived, self.pages)))
            index = stack.enter_context(patch.object(recent, 'inventory_index', return_value=(self.archived, self.pages)))
            stack.enter_context(patch.object(resident_data, 'sha_file', return_value=self.period_sha))
            stack.enter_context(patch.object(recent, 'sha_file', return_value=self.period_sha))
            full_reader = stack.enter_context(patch.object(resident_data, 'read_source', side_effect=self.read_source))
            reader = stack.enter_context(patch.object(recent, 'read_source', side_effect=self.read_source))
            stack.enter_context(patch.object(recent, '_sealed_end_timestamp', side_effect=self.end_timestamp))
            verify = stack.enter_context(patch.object(recent, 'verify_prefix'))
            yield {'reader': reader, 'full_reader': full_reader, 'verify': verify, 'index': index}

    def compare_outcome(self, now):
        with self.sources() as probes:
            full, full_pins = resident_data.read_quotes(self.config, now)
            for index, path in enumerate(self.paths):
                if index < len(self.archived):
                    probes['full_reader'].assert_any_call(path, expected_prefix=self.archived[index])
                else:
                    probes['full_reader'].assert_any_call(path)
            self.assertEqual(probes['full_reader'].call_count, len(self.paths))
            first, last = policy.pending_outcome_bounds(self.plan, [self.prediction], now)
            bounded, bounded_pins = recent.read_original_quote_range(self.config, now, first, last)
            self.assertEqual(full_pins, bounded_pins)
            expected = evaluation.measure(self.plan, 0, self.prediction, full, now)
            actual = evaluation.measure(self.plan, 0, self.prediction, bounded, now)
            self.assertEqual(expected, actual)
            decoded = [Path(call.args[0]) for call in probes['reader'].call_args_list]
            self.assertNotIn(self.paths[0], decoded)
            self.assertIn(self.paths[1], decoded)
            probes['verify'].assert_any_call(self.pins[0], sealed=True)
            probes['verify'].assert_any_call(self.pins[1], sealed=True)
            return actual, bounded

    def test_exact_anchor_and_old_pending_outside_420_seconds(self):
        now = self.anchor + 1_000_000
        self.assertGreater(now - self.anchor, 420000)
        rows = self.complete_rows() + [self.quote(self.anchor + self.horizon + 6000),
                                       self.quote(now, known=now + 1)]
        self.set_rows(rows)
        result, bounded = self.compare_outcome(now)
        self.assertEqual(result['status'], 'OBSERVED')
        self.assertEqual(result['outcome_ms'], self.anchor + self.horizon)
        self.assertEqual(bounded[0]['t'], self.anchor)
        self.assertEqual(bounded[-1]['t'], self.anchor + self.horizon + 6000)
        self.assertNotIn(now, [quote['t'] for quote in bounded])

    def test_late_endpoint_successor_preserves_gap_before_grace_expires(self):
        due = self.anchor + self.horizon
        self.set_rows(self.complete_rows()[:-1] + [self.quote(due + 6000)])
        result, bounded = self.compare_outcome(due + 10000)
        self.assertEqual(result['status'], 'CENSORED')
        self.assertEqual(result['reason'], 'ENDPOINT_GAP')
        self.assertEqual(bounded[-1]['t'], due + 6000)

    def test_missing_endpoint_and_exact_grace_semantics(self):
        due = self.anchor + self.horizon
        self.set_rows(self.complete_rows()[:-1])
        during, _ = self.compare_outcome(due + 15000)
        self.assertIsNone(during)
        after, _ = self.compare_outcome(due + 15001)
        self.assertEqual(after['reason'], 'OUTCOME_MISSING')

    def test_anchor_missing_and_path_gap_reasons_are_unchanged(self):
        now = self.anchor + self.horizon + 20000
        self.set_rows(self.complete_rows()[1:])
        absent, _ = self.compare_outcome(now)
        self.assertEqual(absent['reason'], 'ANCHOR_MISSING_OR_CHANGED')
        rows = self.complete_rows()
        del rows[4]
        self.set_rows(rows)
        gap, _ = self.compare_outcome(now)
        self.assertEqual(gap['reason'], 'PATH_GAP')

    def test_archive_catalog_change_is_rejected(self):
        now = self.anchor + self.horizon + 20000
        with self.sources() as probes:
            changed = copy.deepcopy(self.archived)
            changed[0]['sha256'] = 'f' * 64
            probes['index'].return_value = (changed, self.pages)
            with self.assertRaisesRegex(ContractError, 'ARCHIVE_CHANGED_DURING_READ'):
                recent.read_original_quote_range(self.config, now, self.anchor, self.anchor + self.horizon + 5000)

    def test_skipped_original_is_still_verified_and_mutation_fails(self):
        now = self.anchor + self.horizon + 20000
        with self.sources() as probes:
            def verify(pin, *, sealed):
                if pin['path'] == str(self.paths[0]):
                    raise ContractError('SOURCE_ARCHIVE_PREFIX_CHANGED')
            probes['verify'].side_effect = verify
            with self.assertRaisesRegex(ContractError, 'SOURCE_ARCHIVE_PREFIX_CHANGED'):
                recent.read_original_quote_range(self.config, now, self.anchor, self.anchor + self.horizon + 5000)
            self.assertNotIn(self.paths[0], [Path(call.args[0]) for call in probes['reader'].call_args_list])

    def test_rotation_during_projection_is_rejected(self):
        now = self.anchor + self.horizon + 20000
        with self.sources() as probes:
            changed = False
            def read(path):
                nonlocal changed
                result = self.read_source(path)
                if not changed:
                    added = self.feed / 'segment-9999' / 'quotes.jsonl'
                    added.parent.mkdir()
                    added.write_text('x')
                    changed = True
                return result
            probes['reader'].side_effect = read
            with self.assertRaisesRegex(ContractError, 'ARCHIVE_CHANGED_DURING_READ'):
                recent.read_original_quote_range(self.config, now, self.anchor, self.anchor + self.horizon + 5000)

    def test_bounds_cover_all_mature_pending_anchors_and_no_future_slot(self):
        now = self.anchor + 1_000_000
        self.assertGreaterEqual(self.plan['rule']['slots'], 3)
        older = self.prediction_at(0, self.anchor)
        later = self.prediction_at(1, self.anchor + 360000)
        future = self.prediction_at(2, now - self.horizon + 1)
        self.assertEqual(policy.pending_outcome_bounds(self.plan, [future, later, older], now),
                         (self.anchor, later['anchor_ms'] + self.horizon + 5000))
        self.assertIsNone(policy.pending_outcome_bounds(self.plan, [future], now))

    def test_foreign_or_duplicate_predictions_cannot_broaden_bounds(self):
        now = self.anchor + 1_000_000
        for field, value in [('candidate_id', 'f'*64), ('plan_id', 'f'*64),
                             ('slot', -1), ('slot', True), ('slot', self.plan['rule']['slots'])]:
            with self.subTest(field=field, value=value):
                foreign = {**self.prediction, field: value, 'anchor_ms': 1}
                with self.assertRaisesRegex(ContractError, 'PREDICTION_IDENTITY'):
                    policy.pending_outcome_bounds(self.plan, [foreign], now)
        with self.assertRaisesRegex(ContractError, 'DUPLICATE_PENDING_OUTCOME_SLOT'):
            policy.pending_outcome_bounds(self.plan, [self.prediction, dict(self.prediction)], now)
        with self.assertRaisesRegex(ContractError, 'PREDICTION_IDENTITY'):
            policy.validate_pending_prediction(self.plan, self.prediction, expected_slot=1)

    def test_legacy_plan_uses_original_full_reader(self):
        with patch.object(resident_data, 'read_quotes', return_value=(['full'], ['pins'])) as full, \
             patch.object(recent, 'read_original_quote_range', side_effect=AssertionError('legacy bounded read')):
            result = policy.read_for_pending_outcomes(self.config, self.plan, self.anchor + 1_000_000, [self.prediction])
        self.assertEqual(result, (['full'], ['pins'], 'FULL_ORIGINAL_HISTORY'))
        full.assert_called_once_with(self.config, self.anchor + 1_000_000)

    def test_declared_pending_profile_passes_actual_union_bounds(self):
        plan = {key: value for key, value in self.plan.items() if key != 'plan_id'}
        plan['future_source_read_profile'] = policy.RECENT
        plan['future_outcome_read_profile'] = policy.PENDING_OUTCOMES
        plan['plan_id'] = digest(plan)
        first = {**self.prediction, 'plan_id': plan['plan_id']}
        later = {**self.prediction_at(1, self.anchor + 360000), 'plan_id': plan['plan_id']}
        now = self.anchor + 1_000_000
        with patch.object(policy, 'declared_profile', return_value=policy.RECENT), \
             patch.object(recent, 'read_original_quote_range', return_value=(['bounded'], ['pins'])) as bounded, \
             patch.object(resident_data, 'read_quotes', side_effect=AssertionError('new profile full decode')):
            result = policy.read_for_pending_outcomes(self.config, plan, now, [first, later])
        self.assertEqual(result, (['bounded'], ['pins'], policy.PENDING_OUTCOMES))
        bounded.assert_called_once_with(self.config, now, self.anchor, later['anchor_ms'] + self.horizon + 5000)

    def test_completed_sealed_history_can_settle_existing_prediction(self):
        self.pins[2]['ended'] = True
        self.archived = self.pins[:]
        result, _ = self.compare_outcome(self.anchor + self.horizon + 20000)
        self.assertEqual(result['status'], 'OBSERVED')


if __name__ == '__main__':
    unittest.main()
