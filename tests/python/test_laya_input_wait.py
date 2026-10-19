from pathlib import Path
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'research' / 'neuron_learning'))
from laya_cell.integrity import ContractError
from laya_cell.resident_input_wait import InputWait, MAX_RECHECK_MS, input_signature, next_timestamp_ms
from laya_cell import resident_data
import test_laya_training_cadence as cadence


class InputWaitTests(unittest.TestCase):
    def setUp(self):
        self.version = 'v1'
        self.tick = 100.0
        self.wait = InputWait(lambda config: self.version, monotonic=lambda: self.tick)
        self.config = {}
        self.reserved = {'row-old'}
        self.quality = {'rows': 15, 'source': {'observed_at_ms': 800}}

    def remember(self, *, new=15, next_input=None, complete=True, before=None):
        if before is None:
            before = self.wait.begin(self.config, self.reserved, 1000)
        return self.wait.remember(
            self.config, self.reserved, 1000, before,
            quality=self.quality, mature_rows=20, new_mature_rows=new,
            clock_complete=complete, next_input_ms=next_input)

    def test_unchanged_negative_keeps_original_asof_and_does_not_refresh(self):
        self.assertTrue(self.remember())
        self.quality['source']['observed_at_ms'] = 999
        hit = self.wait.lookup(self.config, self.reserved, 2000)
        self.assertFalse(hit['training_data_refresh_performed'])
        self.assertEqual(hit['training_data_verified_at_ms'], 1000)
        self.assertEqual(hit['new_mature_rows'], 15)
        self.assertEqual(hit['feed']['source']['observed_at_ms'], 800)
        hit['feed']['rows'] = -1
        self.assertEqual(self.wait.lookup(self.config, self.reserved, 2001)['feed']['rows'], 15)

    def test_exact_temporal_boundary_requires_full_recheck(self):
        self.assertTrue(self.remember(next_input=1500))
        self.assertIsNotNone(self.wait.lookup(self.config, self.reserved, 1499))
        self.assertIsNone(self.wait.lookup(self.config, self.reserved, 1500))

    def test_full_recheck_is_bounded_even_without_known_future_input(self):
        self.assertTrue(self.remember())
        self.assertIsNotNone(self.wait.lookup(self.config, self.reserved, 1000 + MAX_RECHECK_MS - 1))
        self.assertIsNone(self.wait.lookup(self.config, self.reserved, 1000 + MAX_RECHECK_MS))

    def test_input_change_invalidates(self):
        self.assertTrue(self.remember())
        self.version = 'v2'
        self.assertIsNone(self.wait.lookup(self.config, self.reserved, 1001))

    def test_consumed_rows_change_invalidates(self):
        self.assertTrue(self.remember())
        self.assertIsNone(self.wait.lookup(self.config, self.reserved | {'row-new'}, 1001))

    def test_input_changed_during_collection_cannot_be_reused(self):
        before = self.wait.begin(self.config, self.reserved, 1000)
        self.version = 'v2'
        self.assertFalse(self.remember(before=before))
        self.assertIsNone(self.wait.lookup(self.config, self.reserved, 1001))

    def test_positive_or_incomplete_clock_scan_cannot_be_cached(self):
        self.assertFalse(self.remember(new=16))
        self.assertFalse(self.remember(complete=False))
        self.assertFalse(self.remember(next_input=1000))
        self.assertIsNone(self.wait.lookup(self.config, self.reserved, 1001))

    def test_clock_regression_invalidates_and_rejects(self):
        self.assertTrue(self.remember())
        with self.assertRaisesRegex(ContractError, 'TRAINING_CLOCK_REGRESSION'):
            self.wait.lookup(self.config, self.reserved, 999)
        self.assertIsNone(self.wait.snapshot)

    def test_monotonic_deadline_cannot_be_extended_by_nearly_fixed_wall_clock(self):
        self.assertTrue(self.remember())
        self.tick = 399.999
        self.assertIsNotNone(self.wait.lookup(self.config, self.reserved, 1001))
        self.tick = 400.0
        self.assertIsNone(self.wait.lookup(self.config, self.reserved, 1002))

    def test_wall_regression_above_original_verification_time_invalidates(self):
        self.assertTrue(self.remember())
        self.assertIsNotNone(self.wait.lookup(self.config, self.reserved, 2000))
        with self.assertRaisesRegex(ContractError, 'TRAINING_CLOCK_REGRESSION'):
            self.wait.lookup(self.config, self.reserved, 1999)
        self.assertIsNone(self.wait.snapshot)

    def test_collection_time_counts_toward_full_recheck_deadline(self):
        before = self.wait.begin(self.config, self.reserved, 1000)
        self.tick = 400.0
        self.assertFalse(self.remember(before=before))
        self.assertIsNone(self.wait.snapshot)

    def test_restart_or_unreadable_signature_falls_back(self):
        self.assertIsNone(InputWait(lambda config: 'v1').lookup({}, set(), 1000))
        self.version = None
        self.assertIsNone(self.wait.begin(self.config, self.reserved, 1000))


class InputSignatureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.feed = self.root / 'feed'
        self.quote = self.feed / 'segment-0001' / 'quotes.jsonl'
        self.quote.parent.mkdir(parents=True)
        self.period = self.root / 'period.json'
        self.seed = self.root / 'seed.json'
        for path in (self.quote, self.period, self.seed):
            path.write_text('one\n')
            path.chmod(0o600)
        self.config = {'feed_directory': str(self.feed), 'period_path': str(self.period),
                       'period_sha256': 'a'*64, 'seed_dataset': str(self.seed)}
        self.inventory = patch('laya_cell.source_archive.inventory_index', return_value=([], []))
        self.inventory.start()

    def tearDown(self):
        self.inventory.stop()
        self.tmp.cleanup()

    def test_unchanged_inputs_do_not_read_raw_bytes(self):
        with patch.object(Path, 'read_bytes', side_effect=AssertionError('raw read')):
            first = input_signature(self.config)
            self.assertIsNotNone(first)
            self.assertEqual(first, input_signature(self.config))

    def test_same_size_rewrite_with_restored_mtime_and_new_segment_invalidate(self):
        first = input_signature(self.config)
        previous = self.quote.stat()
        self.quote.write_text('two\n')
        os.utime(self.quote, ns=(previous.st_atime_ns, previous.st_mtime_ns))
        second = input_signature(self.config)
        self.assertIsNotNone(second)
        self.assertNotEqual(first, second)
        more = self.feed / 'segment-0002' / 'quotes.jsonl'
        more.parent.mkdir()
        more.write_text('new\n')
        more.chmod(0o600)
        self.assertNotEqual(second, input_signature(self.config))

    def test_missing_or_nonprivate_source_disables_reuse(self):
        self.quote.chmod(0o644)
        self.assertIsNone(input_signature(self.config))
        self.quote.unlink()
        self.seed.unlink()
        self.assertIsNone(input_signature(self.config))

    def test_fixed_raw_bytes_recheck_at_first_future_receipt(self):
        now = 1_000_000
        records = [
            {'t': 699999, 'known_at_ms': 999990, 'bid': '1.0', 'ask': '1.2', 'mid': '1.1'},
            {'t': 700010, 'known_at_ms': 999995, 'bid': '1.0', 'ask': '1.2', 'mid': '1.1'},
            {'t': 700020, 'known_at_ms': 1000005, 'bid': '1.0', 'ask': '1.2', 'mid': '1.1'},
        ]
        header = {'period_sha256': self.config['period_sha256']}
        pinned = {'path': str(self.quote), 'bytes': 4, 'sha256': 'b'*64}
        with patch('laya_cell.source_archive.inventory', return_value=([], [])), \
             patch.object(resident_data, 'sha_file', return_value=self.config['period_sha256']), \
             patch.object(resident_data, 'read_source', return_value=(header, records, pinned)):
            plain = resident_data.read_quotes(self.config, now)
            hints = {}
            hinted = resident_data.read_quotes(self.config, now, read_hints=hints)
            self.assertEqual(plain, hinted)
            self.assertEqual(len(hinted[0]), 2)
            self.assertTrue(hints['complete'])
            self.assertEqual(hints['next_input_ms'], 1000005)
            after, _ = resident_data.read_quotes(self.config, 1000005)
            self.assertEqual(len(after), 3)

    def test_seed_available_time_is_not_hidden_by_unchanged_file(self):
        sample = {'anchor_ms': 10, 'known_at_ms': 20,
                  'outcome_receipts': [{'observed_at_ms': 310010, 'available_at_ms': 310015}]}
        self.assertEqual(next_timestamp_ms([sample], 310000), 310010)
        self.assertEqual(next_timestamp_ms([sample], 310010), 310015)
        self.assertIsNone(next_timestamp_ms([sample], 310015))


class TrainingInputPreflightTests(unittest.TestCase):
    service = cadence.TrainingCadenceTests.service
    environment = cadence.TrainingCadenceTests.environment

    def test_real_negative_collection_is_reused_before_lease_and_scan(self):
        service, gate = self.service(guarded=True)
        now = 3 * cadence.MIN_CADENCE_MS
        rewards, collect = self.environment(service, now)
        collect.side_effect = None
        collect.return_value = ({}, [], {'training_input_clock_complete': True,
                                         'next_training_input_ms': now + 50}, [])
        with patch('laya_cell.resident.training_v3.enabled', return_value=True), \
             patch('laya_cell.resident_input_wait.input_signature', return_value='v1'):
            first = service.step()
            self.assertEqual(first['state'], 'WAITING_FOR_NEW_MATURE_LABELS')
            gate.acquire.assert_called_once()
            gate.reset_mock(); collect.reset_mock(); rewards.reset_mock()
            with patch('laya_cell.resident.time.time', return_value=(now + 1) / 1000):
                second = service.step()
            self.assertEqual(second['reason_codes'], ['UNCHANGED_TRAINING_INPUTS'])
            self.assertFalse(second['training_data_refresh_performed'])
            rewards.assert_called_once()
            gate.acquire.assert_not_called()
            collect.assert_not_called()

    def test_due_input_reenters_original_lease_and_collection(self):
        service, gate = self.service(guarded=True)
        now = 3 * cadence.MIN_CADENCE_MS
        _, collect = self.environment(service, now)
        service._training_input_wait = wait = InputWait(lambda config: 'v1')
        before = wait.begin(service.c, set(), now - 10)
        self.assertTrue(wait.remember(service.c, set(), now - 10, before, quality={},
                                      mature_rows=0, new_mature_rows=0,
                                      clock_complete=True, next_input_ms=now))
        with self.assertRaisesRegex(RuntimeError, 'FULL_COLLECT_REACHED'):
            service.step()
        gate.acquire.assert_called_once()
        gate.release.assert_called_once()
        collect.assert_called_once()

    def test_changed_input_cannot_skip_original_lease_and_collection(self):
        service, gate = self.service(guarded=True)
        now = 3 * cadence.MIN_CADENCE_MS
        _, collect = self.environment(service, now)
        version = ['v1']
        service._training_input_wait = wait = InputWait(lambda config: version[0])
        before = wait.begin(service.c, set(), now - 10)
        self.assertTrue(wait.remember(service.c, set(), now - 10, before, quality={},
                                      mature_rows=0, new_mature_rows=0,
                                      clock_complete=True, next_input_ms=now + 10))
        version[0] = 'v2'
        with self.assertRaisesRegex(RuntimeError, 'FULL_COLLECT_REACHED'):
            service.step()
        gate.acquire.assert_called_once()
        collect.assert_called_once()


if __name__ == '__main__':
    unittest.main()
