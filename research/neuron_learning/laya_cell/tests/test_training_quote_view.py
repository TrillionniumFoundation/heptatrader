"""Strict-source training projection preserves the complete collection result.

Fixtures are local hash-chained PAPER quotes. No model, GPU or broker runs.
"""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from laya_cell import resident_data as source
from laya_cell import resident_training_v5 as v5
from laya_cell import resident_training_v6 as v6
from laya_cell import resident_training_v7 as v7
from laya_cell import resident_training_v8 as v8
from laya_cell.integrity import ContractError, sha_file
from laya_cell.resident_row_accounting import row_id
from laya_cell.shadow_source import SCHEMA, canonical, digest
from laya_cell.source_archive import seal

NOW = 1_700_000_000_000
FIRST = NOW-v6.MAX_ROW_AGE_MS


class TrainingQuoteViewTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.feed = self.root/'feed'
        self.feed.mkdir(mode=0o700)
        self.period = self.root/'period.json'
        self.period.write_text('{}')
        self.seed = self.root/'seed.json'
        period_sha = sha_file(self.period)
        self.seed.write_text(json.dumps({'scope': period_sha+':EUR.USD',
            'source_kind': 'REAL_PAPER_RUNTIME', 'samples': [], 'source_prefixes': []}))
        self.config = {'period_path': str(self.period), 'period_sha256': period_sha,
            'feed_directory': str(self.feed), 'completed_history_sources': [],
            'seed_dataset': str(self.seed), 'seed_dataset_sha256': sha_file(self.seed),
            'objective': deepcopy(v6.OBJECTIVE)}

    def segment(self, name, quotes, *, period_sha=None):
        folder = self.feed/name
        folder.mkdir(mode=0o700)
        path = folder/'quotes.jsonl'
        previous = '0'*64
        rows = []

        def append(kind, data, at):
            nonlocal previous
            body = {'schema': SCHEMA, 'sequence': len(rows), 'previous': previous,
                    'at_ms': at, 'kind': kind, 'data': data}
            previous = digest(body)
            rows.append(canonical({**body, 'sha256': previous})+b'\n')

        append('HEADER', {'source': 'IB_TOOL_GATEWAY', 'execution_domain': 'PAPER',
            'instrument': 'EUR.USD', 'write_operations_allowed': [], 'broker_orders_sent': 0,
            'period_sha256': period_sha or self.config['period_sha256']},
            min((q[0] for q in quotes), default=FIRST)-1)
        for stamp, bid, ask, delay in sorted(quotes, key=lambda q: q[0]+q[3]):
            append('QUOTE', {'instrument': 'EUR.USD', 'source': 'IB',
                'authoritative': True, 'stale': False, 'bid': bid, 'ask': ask,
                'observed_at_ms': stamp, 'stale_after_ms': stamp+5000,
                'subscription_id': 'fixture', 'subscription_state': 'active'}, stamp+delay)
        append('END', {}, max((q[0]+q[3] for q in quotes), default=FIRST)+1)
        path.write_bytes(b''.join(rows))
        path.chmod(0o600)
        return path

    def population(self):
        self.segment('segment-00000000', [
            (FIRST-3*86400000+i*10000, '1.08', '1.08002', 1) for i in range(300)])
        # The full-history 10s thinning selects FIRST+6000, while restarting
        # at the recent boundary would select FIRST and change sample IDs.
        self.segment('segment-00000001', [
            (FIRST-906000+i*6000, '1.08', '1.08002', 1) for i in range(300)])
        self.segment('segment-00000002', [
            (NOW-3600000+i*6000, '1.08', '1.08002', 1) for i in range(300)])

    def compare(self, *, reserved=()):
        baseline = source.collect(self.config, NOW, reserved_row_ids=reserved)
        projected = source.collect(self.config, NOW, reserved_row_ids=reserved,
                                   include_source_quotes=False)
        self.assertEqual(projected[:3], baseline[:3])
        self.assertIsNone(projected[3])
        return baseline

    def test_complete_dataset_rows_quality_and_global_thinning_match(self):
        self.population()
        for index, objective in enumerate((v6.OBJECTIVE, v7.OBJECTIVE, v8.OBJECTIVE), 6):
            with self.subTest(version=index):
                self.config['objective'] = deepcopy(objective)
                baseline = self.compare()
                self.assertTrue(baseline[1])
                pool = baseline[2]['training_pool']
                self.assertEqual(pool['first_eligible_anchor_ms'], FIRST+6000)
                self.assertGreater(pool['exclusion_counts']['ANCHOR_OLDER_THAN_RECENCY_PROFILE'], 0)

    def test_reserved_rows_and_seed_merge_keep_identical_results(self):
        self.population()
        baseline = self.compare()
        seed = json.loads(self.seed.read_text())
        seed['samples'] = baseline[0]['samples'][:12]
        self.seed.write_text(json.dumps(seed))
        self.config['seed_dataset_sha256'] = sha_file(self.seed)
        self.compare(reserved={row_id(row) for row in baseline[1][::7]})

    def test_maximum_history_span_and_exact_age_boundary_match(self):
        self.segment('segment-00000000', [
            (FIRST-900000+i*30000, '1.08', '1.08002', 4999) for i in range(90)])
        baseline = self.compare()
        sample = next(s for s in baseline[0]['samples'] if s['anchor_ms'] == FIRST)
        self.assertEqual(len(sample['input_snapshot']['quotes']), 24)
        self.assertFalse(sample['contiguous_history_reset_at_gap'])

    def test_old_boundary_preserves_gap_reset_and_missing_history(self):
        self.segment('segment-00000000', [(FIRST-900000, '1.08', '1.08002', 1)])
        self.segment('segment-00000001', [
            (FIRST+i*10000, '1.08', '1.08002', 1) for i in range(70)])
        baseline = self.compare()
        self.assertTrue(baseline[0]['samples'][0]['contiguous_history_reset_at_gap'])
        self.assertGreater(baseline[2]['training_pool']['exclusion_counts'][
            'INSUFFICIENT_CONTIGUOUS_INPUT_HISTORY'], 0)

    def test_old_and_recent_cross_segment_conflicts_still_fail(self):
        for stamp in (FIRST-900000, FIRST+60000):
            with self.subTest(stamp=stamp):
                for path in self.feed.glob('segment-*/quotes.jsonl'):
                    path.unlink();path.parent.rmdir()
                self.segment('segment-00000000', [(stamp, '1.08', '1.08002', 1)])
                self.segment('segment-00000001', [(stamp, '1.07', '1.08002', 2)])
                for include in (True, False):
                    with self.assertRaisesRegex(ContractError, 'CROSS_SEGMENT_PRICE_CONFLICT'):
                        source.collect(self.config, NOW, include_source_quotes=include)

    def test_completed_probe_conflict_remains_visible(self):
        stamp = FIRST-900000
        self.segment('segment-00000000', [(stamp, '1.08', '1.08002', 1)])
        extra = self.segment('probe', [(stamp, '1.07', '1.08002', 2)])
        self.config['completed_history_sources'] = [str(extra)]
        for include in (True, False):
            with self.assertRaisesRegex(ContractError, 'CROSS_SEGMENT_PRICE_CONFLICT'):
                source.collect(self.config, NOW, include_source_quotes=include)

    def test_earliest_availability_and_stable_duplicate_ties_match(self):
        self.population()
        original = FIRST+6000
        self.segment('segment-00000003', [(original, '1.080', '1.080020', 0),
            (FIRST-900000, '1.08', '1.08002', 0)])
        self.segment('segment-00000004', [(original, 1.08, 1.08002, 0)])
        baseline = self.compare()
        chosen = next(q for q in baseline[3] if q['t'] == original)
        self.assertEqual(chosen['known_at_ms'], original)

    def test_future_input_clock_and_empty_or_old_only_populations_match(self):
        self.compare()
        self.segment('segment-00000000', [
            (FIRST-900000+i*10000, '1.08', '1.08002', 1) for i in range(20)])
        old = self.compare()
        self.assertEqual(old[2]['new_source_quotes'], 20)
        self.segment('segment-00000001', [(NOW+1000, '1.08', '1.08002', 2)])
        future = self.compare()
        self.assertEqual(future[2]['next_training_input_ms'], NOW+1002)
        self.assertTrue(future[2]['training_input_clock_complete'])

    def test_historical_sealed_bytes_still_checked(self):
        for i in range(33):
            self.segment(f'segment-{i:08d}', [(FIRST-2000000+i*10000, '1.08', '1.08002', 1)])
        seal(self.config)
        self.compare()
        path = self.feed/'segment-00000000'/'quotes.jsonl'
        path.write_bytes(path.read_bytes().replace(b'1.08', b'1.07'))
        with self.assertRaisesRegex(ContractError, 'SOURCE_PREFIX_HASH_CHANGED'):
            source.collect(self.config, NOW, include_source_quotes=False)

    def sparse_population(self):
        self.population()
        for i in range(3, 34):
            self.segment(f'segment-{i:08d}',
                [(FIRST-4*86400000+i*10000, '1.08', '1.08002', 1)])
        gap = self.feed/'segment-00000000'/'quotes.jsonl'
        rows = gap.read_bytes().splitlines()
        self.assertEqual(json.loads(rows[-1])['kind'], 'END')
        gap.write_bytes(b'\n'.join(rows[:-1])+b'\n')
        receipt = seal(self.config, 1)
        self.assertEqual(receipt['sealed_segments'], 32)
        self.assertEqual(receipt['skipped_unsealed_segments'], 1)
        return gap

    def test_sparse_gap_and_late_end_keep_complete_training_output_and_raw_proof(self):
        gap = self.sparse_population()
        self.compare()
        rows = gap.read_bytes().splitlines(); last = json.loads(rows[-1])
        body = {'schema': SCHEMA, 'sequence': len(rows), 'previous': last['sha256'],
            'at_ms': last['at_ms']+1, 'kind': 'END', 'data': {}}
        with gap.open('ab') as stream:
            stream.write(canonical({**body, 'sha256': digest(body)})+b'\n')
        result = self.compare()
        raw = next(p for p in result[0]['source_prefixes'] if p['path'] == str(gap))
        self.assertTrue(raw['ended'])
        gap.unlink()
        for include in (True, False):
            with self.assertRaisesRegex(ContractError, 'SOURCE_ARCHIVE_REORDERED_OR_MISSING'):
                source.collect(self.config, NOW, include_source_quotes=include)

    def test_compact_old_keys_do_not_hide_conflicts_from_unsealed_sparse_gap(self):
        gap = self.sparse_population()
        rows = gap.read_bytes().splitlines(); last = json.loads(rows[-1])
        stamp = FIRST-906000
        body = {'schema': SCHEMA, 'sequence': len(rows), 'previous': last['sha256'],
            'at_ms': stamp+1, 'kind': 'QUOTE', 'data': {'instrument': 'EUR.USD',
                'source': 'IB', 'authoritative': True, 'stale': False,
                'bid': '1.07', 'ask': '1.08002', 'observed_at_ms': stamp,
                'stale_after_ms': stamp+5000, 'subscription_id': 'fixture',
                'subscription_state': 'active'}}
        with gap.open('ab') as stream:
            stream.write(canonical({**body, 'sha256': digest(body)})+b'\n')
        for include in (True, False):
            with self.assertRaisesRegex(ContractError, 'CROSS_SEGMENT_PRICE_CONFLICT'):
                source.collect(self.config, NOW, include_source_quotes=include)

    def test_old_cross_period_source_still_rejected(self):
        self.segment('segment-00000000', [(FIRST-900000, '1.08', '1.08002', 1)],
                     period_sha='f'*64)
        for include in (True, False):
            with self.assertRaisesRegex(ContractError, 'CROSS_PERIOD_FEED'):
                source.collect(self.config, NOW, include_source_quotes=include)

    def test_full_rows_are_bounded_but_original_count_is_preserved(self):
        self.population()
        view = source._TrainingQuoteView(FIRST)
        projected, pins = source.read_quotes(self.config, NOW, training_view=view)
        full, full_pins = source.read_quotes(self.config, NOW)
        self.assertEqual(pins, full_pins)
        self.assertEqual(view.source_count, len(full))
        older = [q for q in full if q['t'] < view.full_after_ms]
        self.assertEqual(projected, older[-1:]+[q for q in full if q['t'] >= view.full_after_ms])
        self.assertLess(len(projected), len(full))
        self.assertEqual(view.old_prices, {})

    def test_legacy_objective_keeps_complete_read_path(self):
        self.population()
        self.config['objective'] = deepcopy(v5.OBJECTIVE)
        with patch.object(source, '_TrainingQuoteView', side_effect=AssertionError('LEGACY_VIEW')):
            self.compare()


if __name__ == '__main__':
    unittest.main()
