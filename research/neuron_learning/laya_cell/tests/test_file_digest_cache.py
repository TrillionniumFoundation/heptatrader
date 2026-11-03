from __future__ import annotations

import hashlib
from contextlib import contextmanager
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from laya_cell import file_digest_cache
from laya_cell.file_digest_cache import FileDigestCache
from laya_cell.integrity import ContractError


class FileDigestCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "artifact.bin"
        self.path.write_bytes(b"original")
        self.cache = FileDigestCache()

    def test_unchanged_file_avoids_content_reads(self):
        expected = hashlib.sha256(b"original").hexdigest()
        self.assertEqual(self.cache.sha256(self.path), expected)
        with patch.object(file_digest_cache, "_regular_stream", side_effect=AssertionError("unexpected read")):
            for _ in range(20):
                self.assertEqual(self.cache.sha256(self.path), expected)
        self.assertEqual(self.cache.stats()["bytes_read"], 8)

    def test_same_size_rewrite_with_restored_mtime_invalidates(self):
        original = self.cache.sha256(self.path)
        before = self.path.stat()
        self.path.write_bytes(b"replaced")
        os.utime(self.path, ns=(before.st_atime_ns, before.st_mtime_ns))
        actual = self.cache.sha256(self.path)
        self.assertNotEqual(actual, original)
        self.assertEqual(actual, hashlib.sha256(b"replaced").hexdigest())

    def test_atomic_replacement_with_same_size_and_mtime_invalidates(self):
        original = self.cache.sha256(self.path)
        before = self.path.stat()
        replacement = self.root / "replacement"
        replacement.write_bytes(b"replaced")
        os.utime(replacement, ns=(before.st_atime_ns, before.st_mtime_ns))
        replacement.replace(self.path)
        self.assertNotEqual(self.cache.sha256(self.path), original)

    def test_symlink_retarget_invalidates(self):
        target = self.root / "target"
        target.write_bytes(b"replaced")
        link = self.root / "link"
        link.symlink_to(self.path)
        original = self.cache.sha256(link)
        link.unlink()
        link.symlink_to(target)
        self.assertNotEqual(self.cache.sha256(link), original)

    def test_missing_file_never_returns_cached_digest(self):
        self.cache.sha256(self.path)
        self.path.unlink()
        with self.assertRaises(FileNotFoundError):
            self.cache.sha256(self.path)
        self.assertEqual(self.cache.stats()["entries"], 0)

    def test_directory_replacement_is_rejected(self):
        self.cache.sha256(self.path)
        self.path.unlink()
        self.path.mkdir()
        with self.assertRaises(ValueError):
            self.cache.sha256(self.path)

    def test_replacement_during_hash_is_rejected_and_not_cached(self):
        original_stream = file_digest_cache._regular_stream
        replacement = self.root / "replacement"
        replacement.write_bytes(b"replaced")

        class ReplacingReader:
            def __init__(self, wrapped):
                self.wrapped = wrapped
                self.first_read = True

            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.wrapped.close()

            def fileno(self):
                return self.wrapped.fileno()

            def read(self, size):
                value = self.wrapped.read(size)
                if self.first_read:
                    self.first_read = False
                    replacement.replace(path)
                return value

        path = self.path
        @contextmanager
        def replacing_stream(*args):
            with original_stream(*args) as (handle, metadata):
                yield ReplacingReader(handle), metadata
        with patch.object(file_digest_cache, "_regular_stream", replacing_stream):
            with self.assertRaisesRegex(ContractError, "CHANGED_DURING_READ"):
                self.cache.sha256(path)
        self.assertEqual(self.cache.stats()["entries"], 0)
        self.assertEqual(self.cache.sha256(path), hashlib.sha256(b"replaced").hexdigest())

    def test_cache_bound_evicts_old_paths(self):
        cache = FileDigestCache(max_entries=2)
        for i in range(3):
            path = self.root / str(i)
            path.write_bytes(bytes([i]))
            cache.sha256(path)
        self.assertEqual(cache.stats()["entries"], 2)
        cache.sha256(self.root / "0")
        self.assertEqual(cache.stats()["misses"], 4)


if __name__ == "__main__":
    unittest.main()
