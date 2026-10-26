"""Original-prefix behavior with bounded synthetic files; no models or broker."""
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from historical_training import resident_live_source as live_source
from historical_training.common import EvidenceError, digest


NOW = 1793000000000


class LiveSourceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.feed = Path(self.temporary.name).resolve() / 'feed'
        self.folder = self.feed / 'segment-001'
        self.folder.mkdir(mode=0o700, parents=True)
        self.path = self.folder / 'quotes.jsonl'
        self.original = {'feed_directory': str(self.feed), 'period_sha256': 'a' * 64}
        self.quotes = [{'t': NOW-1000, 'known_at_ms': NOW-900, 'receipt': 'q1'},
                       {'t': NOW-500, 'known_at_ms': NOW-400, 'receipt': 'q2'},
                       {'t': NOW+1, 'known_at_ms': NOW+2, 'receipt': 'future'}]

    def fixture(self, raw, prefix_bytes):
        self.path.write_bytes(raw)
        self.path.chmod(0o600)
        self.pin = {'path': str(self.path), 'bytes': prefix_bytes,
                    'sha256': hashlib.sha256(raw[:prefix_bytes]).hexdigest(), 'ended': False}
        self.live = {**self.pin, 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
        self.source = {'at_ms': NOW-2000, 'source_prefixes': [self.pin],
                       'source_prefix_digest': digest([self.pin]), 'latest_source_quote_ms': NOW-3000}

    def reader(self, path):
        self.assertEqual(path, self.path)
        return {'period_sha256': self.original['period_sha256']}, self.quotes, self.live

    def refreshed(self, reader=None):
        return live_source.refresh(self.original, self.source, NOW, reader or self.reader)

    def assert_original_proof(self, result):
        updated, proof = result
        self.assertEqual(updated, {**self.source, 'latest_source_quote_ms': NOW-500,
                                   'latest_quote_age_ms': 500})
        self.assertEqual(proof, {
            'schema': 'heptatrader.original-live-freshness.v1',
            'continuity_at_ms': self.source['at_ms'],
            'continuity_prefix_digest': self.source['source_prefix_digest'],
            'pinned_prefix': self.pin, 'verified_live_prefix': self.live,
            'quote_observed_at_ms': NOW-500, 'quote_known_at_ms': NOW-400,
            'quote_receipt': 'q2', 'quote_timestamp_rewritten': False,
            'source_ttl_ms': 5000, 'cached_continuity_not_efficacy': True,
            'broker_queries_sent': 0})

    def test_original_prefix_can_end_before_at_or_after_a_read_boundary(self):
        raw = bytes(range(256)) * 8193
        for size in (1, 1024*1024-1, 1024*1024, 1024*1024+17, len(raw)):
            with self.subTest(prefix_bytes=size):
                self.fixture(raw, size)
                self.assert_original_proof(self.refreshed())

    def test_partial_kernel_reads_preserve_the_same_prefix_proof(self):
        self.fixture(bytes(range(256)) * 5000, 700003)
        actual_read = os.read
        with patch.object(live_source.os, 'read',
                          side_effect=lambda fd, size: actual_read(fd, min(size, 65537))):
            self.assert_original_proof(self.refreshed())

    def test_truncated_file_after_reader_is_a_short_read(self):
        raw = b'a' * (1024*1024+17)
        self.fixture(raw, 31)
        def reader(path):
            result = self.reader(path)
            path.write_bytes(raw[:-1])
            return result
        with self.assertRaisesRegex(EvidenceError, '^LIVE_SOURCE_SHORT_READ$'):
            self.refreshed(reader)

    def test_live_bytes_changed_after_reader_are_rejected(self):
        raw = b'original-prefix-new-live-tail'
        self.fixture(raw, len(b'original-prefix'))
        def reader(path):
            result = self.reader(path)
            path.write_bytes(raw[:-1] + b'!')
            return result
        with self.assertRaisesRegex(EvidenceError, '^LIVE_SOURCE_CHANGED_DURING_VERIFICATION$'):
            self.refreshed(reader)

    def test_rewritten_original_prefix_is_rejected_even_with_current_live_hash(self):
        raw = b'original-prefix-new-live-tail'
        self.fixture(raw, len(b'original-prefix'))
        changed = b'!' + raw[1:]
        self.path.write_bytes(changed)
        self.live['sha256'] = hashlib.sha256(changed).hexdigest()
        with self.assertRaisesRegex(EvidenceError, '^ORIGINAL_CONTINUITY_PREFIX_REWRITTEN$'):
            self.refreshed()

    def test_concurrent_append_beyond_verified_live_prefix_is_still_allowed(self):
        raw = b'original-prefix-new-live-tail'
        self.fixture(raw, len(b'original-prefix'))
        def reader(path):
            result = self.reader(path)
            with path.open('ab') as stream:
                stream.write(b'-later-append')
            return result
        self.assert_original_proof(self.refreshed(reader))

    def test_original_byte_bounds_are_unchanged(self):
        for prefix_bytes, live_bytes in ((0, 16), (17, 16), (1, live_source.MAX_BYTES+1)):
            with self.subTest(prefix_bytes=prefix_bytes, live_bytes=live_bytes):
                self.fixture(b'x' * 16, 1)
                self.pin['bytes'] = prefix_bytes
                self.live['bytes'] = live_bytes
                self.source['source_prefix_digest'] = digest([self.pin])
                with self.assertRaisesRegex(EvidenceError, '^LIVE_SOURCE_PREFIX_TRUNCATED_OR_BOUND$'):
                    self.refreshed()

    def test_source_private_file_requirement_is_unchanged(self):
        self.fixture(b'original-and-live', 8)
        self.path.chmod(0o644)
        with self.assertRaisesRegex(EvidenceError, '^LIVE_SOURCE_FILE_OWNER_OR_MODE$'):
            self.refreshed()


if __name__ == '__main__':
    unittest.main()
