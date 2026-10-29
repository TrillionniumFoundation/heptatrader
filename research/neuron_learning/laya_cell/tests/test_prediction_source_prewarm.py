"""Optional early preparation never replaces the fixed-slot source decision."""
from contextlib import contextmanager
import time
import unittest
from unittest.mock import patch

from laya_cell import resident_recent_source, resident_store, shadow_source
from laya_cell.integrity import read_json
from laya_cell.source_read_budget import SourceReadDeadline
from laya_cell.tests.test_prediction_source_read_phase import functions, SOURCE
from laya_cell.tests import test_recent_source as recent_fixture


class PredictionSourcePrewarmTests(unittest.TestCase):
    def setUp(self):
        self.fixture = recent_fixture.RecentSourceStrictParityTests('test_full_original_quote_and_prefix_parity_at_6min')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.config, self.now = self.fixture.config, self.fixture.now
        self.stage = self.fixture.source.root / 'phase-000.json'
        self.deadline = int(time.time() * 1000) + 10000
        self.timing = {'fixed_input_deadline_ms': self.deadline}
        self.cache = shadow_source.SourceDecodeCache(max_entries=32)
        self.namespace = {'__package__': 'laya_cell', 'time': time,
                          'atomic': resident_store.atomic, 'contextmanager': contextmanager,
                          'read_for_future_plan': self.fixed_source}
        functions(SOURCE, {'_timed_phase', '_prewarm_fixed_slot_source', '_read_fixed_slot_source'}, self.namespace)

    def fixed_source(self, config, plan, now, *, deadline_ms, decode_cache):
        self.assertEqual(deadline_ms, self.deadline)
        from laya_cell.source_read_budget import source_read_budget
        with source_read_budget(deadline_ms):
            return (*resident_recent_source.read_recent_original_quotes(config, now, decode_cache=decode_cache),
                    'ARCHIVE_VERIFIED_RECENT_ORIGINAL_V2')

    def prewarm(self):
        return self.namespace['_prewarm_fixed_slot_source'](
            self.config, self.cache, self.deadline, self.stage, self.timing)

    def fixed_read(self, cache):
        return self.namespace['_read_fixed_slot_source'](
            self.config, {}, self.now, self.stage, self.timing, cache)

    def empty_rotating_segment(self):
        path = self.fixture.source.feed / 'segment-rotation-in-progress' / 'quotes.jsonl'
        path.parent.mkdir(mode=0o700)
        path.touch(mode=0o600)
        return path

    def test_uncommitted_header_before_slot_can_become_valid_input_at_slot(self):
        path = self.empty_rotating_segment()
        cache = self.prewarm()
        self.assertIsNot(cache, self.cache)
        self.assertEqual(cache.max_entries, 32)
        self.assertEqual(cache.counters()['entries'], 0)
        self.assertEqual(self.timing['source_prewarm_status'], 'UNAVAILABLE_RECHECK_AT_FIXED_SLOT')
        self.assertEqual(self.timing['source_prewarm_error'], 'SHADOW_HEADER_REQUIRED')
        path.write_bytes((self.fixture.source.feed / 'segment-00000190' / 'quotes.jsonl').read_bytes())
        quotes, pins, profile = self.fixed_read(cache)
        expected, expected_pins = resident_recent_source.read_recent_original_quotes(self.config, self.now)
        self.assertEqual((quotes, pins), (expected, expected_pins))
        self.assertEqual(profile, 'ARCHIVE_VERIFIED_RECENT_ORIGINAL_V2')
        self.assertNotIn('anchor_ms', self.timing)

    def test_still_empty_header_at_slot_remains_fatal(self):
        self.empty_rotating_segment()
        cache = self.prewarm()
        with self.assertRaisesRegex(ValueError, 'SHADOW_HEADER_REQUIRED'):
            self.fixed_read(cache)
        self.assertEqual(self.timing['source_read_error_class'], 'ValueError')

    def test_failed_prewarm_does_not_hide_corrupt_original_at_slot(self):
        path = self.empty_rotating_segment()
        cache = self.prewarm()
        path.write_bytes((self.fixture.source.feed / 'segment-00000190' / 'quotes.jsonl').read_bytes())
        archived = self.fixture.source.feed / 'segment-00000000' / 'quotes.jsonl'
        archived.write_bytes(archived.read_bytes().replace(b'1.125', b'1.124'))
        with self.assertRaisesRegex(ValueError, 'SOURCE_PREFIX_HASH_CHANGED'):
            self.fixed_read(cache)

    def test_known_file_error_is_bounded_and_clears_prepared_state(self):
        self.prewarm()
        self.assertGreater(self.cache.counters()['entries'], 0)
        with patch.object(resident_recent_source, 'prewarm_recent_decoding',
                          side_effect=FileNotFoundError('rotating segment ' + 'x' * 1000)):
            cache = self.prewarm()
        self.assertIsNot(cache, self.cache)
        self.assertEqual(cache.counters()['entries'], 0)
        self.assertEqual(len(self.timing['source_prewarm_error']), 256)
        persisted = read_json(self.stage)
        self.assertEqual(persisted['source_prewarm_error_class'], 'FileNotFoundError')
        self.assertEqual(persisted['source_prewarm_cache_policy'], 'DISCARDED_AFTER_SOURCE_ERROR')

    def test_deadline_retains_only_verified_decoder_entries_and_slot_rehashes(self):
        self.prewarm()
        entries = self.cache.counters()['entries']
        before = self.cache.rehashed_bytes
        with patch.object(resident_recent_source, 'prewarm_recent_decoding',
                          side_effect=SourceReadDeadline('INPUT_SLOT_EXPIRED:DURING_QUOTE_READ')):
            cache = self.prewarm()
        self.assertIs(cache, self.cache)
        self.assertEqual(cache.counters()['entries'], entries)
        self.assertEqual(self.timing['source_prewarm_status'], 'PRE_SLOT_BUDGET_EXHAUSTED_NO_INPUT_SELECTED')
        self.fixed_read(cache)
        self.assertGreater(cache.rehashed_bytes, before)

    def test_unexpected_programming_failure_is_not_hidden(self):
        with patch.object(resident_recent_source, 'prewarm_recent_decoding', side_effect=RuntimeError('bug')):
            with self.assertRaisesRegex(RuntimeError, 'bug'):
                self.prewarm()


if __name__ == '__main__':
    unittest.main()
