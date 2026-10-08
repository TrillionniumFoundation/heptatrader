from __future__ import annotations
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
import hepta_fx_source_evidence as replay


def rows(extra=()):
    records = [
        ('HEADER', 1000000, {}),
        ('SUBSCRIBED', 1000100, {'ids': [3101, 3102]}),
        ('MARKET_TYPE', 1000150, {'request_id': 3101, 'market_type': 1}),
        *extra,
        ('END', 1010000, {'broker_orders_sent': 0, 'account_requests_sent': 0,
             'wire_counts': {'mutating_codes_sent': 0, 'active_requests': [],
                             'counts': {'71': 1, '59': 1, '1': 1, '2': 1, '97': 1, '98': 1}},
             'state': 'DURATION_ENDED'}),
    ]
    out = []
    previous = '0' * 64
    for seq, (kind, at, payload) in enumerate(records):
        if kind not in ('HEADER', 'END', 'SUBSCRIBED'):
            payload = dict(payload, received_ms=at)
        row = dict(schema='heptatrader.ib-direct-bidask-probe.v1', seq=seq,
                   prev=previous, at_ms=at, kind=kind, payload=payload)
        row['sha256'] = hashlib.sha256(replay.canonical(row)).hexdigest()
        previous = row['sha256']
        out.append(row)
    return out


class SourceReplayTests(unittest.TestCase):
    def test_top_coverage_never_fills_tbt_gap(self):
        source = rows([
            ('TOP_BID', 1000200, {'request_id': 3101, 'price': 1.1}),
            ('TOP_ASK', 1000300, {'request_id': 3101, 'price': 1.2}),
        ])
        self.assertGreater(replay.replay_stream(source, 'TOP')['full_window_fresh_cells'], 0)
        self.assertEqual(replay.replay_stream(source, 'TBT')['full_window_fresh_cells'], 0)
        with self.assertRaises(ValueError):
            replay.replay_stream(source, 'MIXED')

    def test_tbt_never_fills_top_and_never_uses_future_callbacks(self):
        source = rows([('TBT_BIDASK', 1006400, {
            'request_id': 3102, 'broker_second': 1006, 'bid': 1.1, 'ask': 1.2})])
        result = replay.replay_stream(source, 'TBT')
        self.assertEqual(result['first_usable_pair_at_ms'], 1006400)
        self.assertEqual(result['full_window_fresh_cells'], 4)
        self.assertEqual(replay.replay_stream(source, 'TOP')['source_callbacks'], 0)
        self.assertFalse(result['source_replacement_eligible'])

    def test_future_regressions_and_duplicates_keep_source_time(self):
        data = [('TBT_BIDASK', 1000300, {'request_id': 3102, 'broker_second': 1000, 'bid': 1.1, 'ask': 1.2}),
                ('TBT_BIDASK', 1000700, {'request_id': 3102, 'broker_second': 1000, 'bid': 1.1, 'ask': 1.2}),
                ('TBT_BIDASK', 1000900, {'request_id': 3102, 'broker_second': 999, 'bid': 1.1, 'ask': 1.2}),
                ('TBT_BIDASK', 1000950, {'request_id': 3102, 'broker_second': 998, 'bid': 1.1, 'ask': 1.2})]
        result = replay.replay_stream(rows(data), 'TBT')
        self.assertEqual(result['independent_tbt_pairs'], 1)
        self.assertEqual(result['duplicate_tbt_callbacks'], 1)
        self.assertEqual(result['tbt_source_regressions'], 2)
        self.assertEqual(result['full_window_fresh_cells'], 0)
        future = rows([('TBT_BIDASK', 1000300, {'request_id': 3102, 'broker_second': 1001, 'bid': 1.1, 'ask': 1.2})])
        self.assertEqual(replay.replay_stream(future, 'TBT')['full_window_fresh_cells'], 0)

    def test_data_lost_requires_new_subscription_not_old_ticks(self):
        data = [
            ('TOP_BID', 1000200, {'request_id': 3101, 'price': 1.1}),
            ('TOP_ASK', 1000300, {'request_id': 3101, 'price': 1.2}),
            ('CONTROL', 1000900, {'code': 1101}),
            ('TOP_BID', 1001100, {'request_id': 3101, 'price': 1.1}),
            ('TOP_ASK', 1001200, {'request_id': 3101, 'price': 1.2}),
            ('RESUBSCRIBED_READ_ONLY', 1001300, {'ids': [3103, 3104]}),
            ('TOP_BID', 1001400, {'request_id': 3101, 'price': 1.1}),
            ('TOP_ASK', 1001500, {'request_id': 3101, 'price': 1.2}),
        ]
        result = replay.replay_stream(rows(data), 'TOP')
        self.assertEqual(result['full_window_fresh_cells'], 0)
        self.assertEqual(result['ignored_foreign_requests'], 2)
        self.assertEqual(result['observed_resubscriptions'], 1)

    def test_chain_and_readonly_boundary_verified(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'events.jsonl'
            source = rows()
            path.write_bytes(b''.join(replay.canonical(r) + b'\n' for r in source))
            evidence = replay.audit(path, hashlib.sha256(path.read_bytes()).hexdigest())
            self.assertTrue(evidence['chain_verified'])
            self.assertEqual(set(evidence['streams']), {'TOP', 'TBT'})
            self.assertFalse(evidence['strict_nav_eligible'])
            altered = copy.deepcopy(source)
            altered[1]['payload']['ids'] = [9, 10]
            path.write_bytes(b''.join(replay.canonical(r) + b'\n' for r in altered))
            with self.assertRaisesRegex(ValueError, 'CHAIN_INVALID'):
                replay.load_chain(path)
            with self.assertRaisesRegex(ValueError, 'DIGEST_MISMATCH'):
                replay.load_chain(path, 'f' * 64)


if __name__ == '__main__':
    unittest.main()
