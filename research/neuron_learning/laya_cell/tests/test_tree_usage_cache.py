from __future__ import annotations

import os
from pathlib import Path
import stat
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

from laya_cell import tree_usage_cache
from laya_cell.tree_usage_cache import TreeUsageCache
from laya_cell.integrity import ContractError


@unittest.skipUnless(sys.platform == "linux", "Linux notification accounting")
class TreeUsageCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "state"
        self.root.mkdir()
        self.file = self.root / "state.json"
        self.file.write_bytes(b"12345")
        self.cache = TreeUsageCache(self.root)
        self.addCleanup(self.cache.close)

    def assert_usage(self):
        expected = []
        for path in self.root.rglob("*"):
            value = path.lstat()
            if stat.S_ISREG(value.st_mode):
                expected.append(value.st_size)
        self.assertEqual(self.cache.usage(), (sum(expected), len(expected)))

    def test_unchanged_tree_has_no_walk_or_file_stat(self):
        self.assertEqual(self.cache.usage(), (5, 1))
        with patch("os.scandir", side_effect=AssertionError("unexpected walk")), \
                patch.object(Path, "lstat", side_effect=AssertionError("unexpected file stat")):
            for _ in range(10):
                self.assertEqual(self.cache.usage(), (5, 1))
        self.assertEqual(self.cache.stats()["full_scans"], 1)

    def test_rewrite_append_create_delete_and_atomic_replace(self):
        self.cache.usage()
        self.file.write_bytes(b"12")
        self.assert_usage()
        with self.file.open("ab") as handle:
            handle.write(b"new-data")
        self.assert_usage()
        new = self.root / "new"
        new.write_bytes(b"abc")
        self.assert_usage()
        new.replace(self.file)
        self.assert_usage()
        self.file.unlink()
        self.assert_usage()
        self.assertEqual(self.cache.stats()["full_scans"], 1)

    def test_new_nested_directory_and_directory_rename(self):
        self.cache.usage()
        child = self.root / "child" / "nested"
        child.mkdir(parents=True)
        (child / "one").write_bytes(b"abcd")
        self.assert_usage()
        (self.root / "child").rename(self.root / "renamed")
        self.assert_usage()
        (self.root / "renamed" / "nested" / "one").write_bytes(b"1")
        self.assert_usage()

    def test_root_atomic_replacement_invalidates(self):
        self.cache.usage()
        alternate = self.root.parent / "alternate"
        alternate.mkdir()
        (alternate / "new").write_bytes(b"new-root-data")
        self.root.rename(self.root.parent / "old")
        alternate.rename(self.root)
        self.assert_usage()

    def test_new_symbolic_links_preserve_fail_closed_policy(self):
        self.cache.usage()
        outside = self.root.parent / "outside"
        outside.mkdir()
        (outside / "large").write_bytes(b"0" * 1000)
        for name, target in (("linked-dir", outside), ("linked-file", outside / "large")):
            link = self.root / name
            link.symlink_to(target)
            with self.assertRaisesRegex(ContractError, 'STATE_TREE_SYMLINK'):
                self.cache.usage()
            link.unlink()
            self.assertEqual(self.cache.usage(), (5, 1))
        self.file.unlink()
        self.file.symlink_to(outside / "large")
        with self.assertRaisesRegex(ContractError, 'STATE_TREE_SYMLINK'):
            self.cache.usage()

    def test_existing_nested_symlink_is_rejected(self):
        child = self.root / 'child'
        child.mkdir()
        (child / 'link').symlink_to(self.file)
        with self.assertRaisesRegex(ContractError, 'STATE_TREE_SYMLINK'):
            self.cache.usage()

    def test_queue_overflow_rebuilds_snapshot(self):
        self.cache.usage()
        self.file.write_bytes(b"new content longer")
        read = os.read
        injected = False

        def overflow_once(fd, size):
            nonlocal injected
            if not injected:
                injected = True
                return struct.pack("iIII", -1, 0x00004000, 0, 0)
            return read(fd, size)

        with patch.object(tree_usage_cache.os, "read", side_effect=overflow_once):
            self.assert_usage()
        self.assertGreaterEqual(self.cache.stats()["full_scans"], 2)

    def test_watch_limit_falls_back_to_exact_walk_each_time(self):
        (self.root / "child").mkdir()
        cache = TreeUsageCache(self.root, max_watches=1)
        self.addCleanup(cache.close)
        self.assertEqual(cache.usage(), (5, 1))
        self.assertFalse(cache.stats()["watching"])
        (self.root / "child" / "new").write_bytes(b"abc")
        self.assertEqual(cache.usage(), (8, 2))
        self.assertGreaterEqual(cache.stats()["watch_failures"], 1)

    def test_deleted_and_recreated_root(self):
        self.cache.usage()
        self.file.unlink()
        self.root.rmdir()
        self.assertEqual(self.cache.usage(), (0, 0))
        self.root.mkdir()
        self.file.write_bytes(b"new")
        self.assertEqual(self.cache.usage(), (3, 1))

    def test_existing_outside_hardlink_preserves_full_walk_semantics(self):
        outside = self.root.parent / 'outside-link'
        os.link(self.file, outside)
        self.assertEqual(self.cache.usage(), (5, 1))
        self.assertFalse(self.cache.stats()['watching'])
        outside.write_bytes(b'longer outside write')
        self.assert_usage()
        outside.unlink()
        self.assert_usage()
        self.assertTrue(self.cache.stats()['watching'])

    def test_outside_hardlink_created_after_snapshot_is_detected(self):
        self.assertEqual(self.cache.usage(), (5, 1))
        outside = self.root.parent / 'outside-link'
        os.link(self.file, outside)
        outside.write_bytes(b'longer outside write')
        self.assert_usage()
        self.assertFalse(self.cache.stats()['watching'])
        self.assertGreater(self.cache.stats()['hardlink_fallbacks'], 0)

    def test_transient_outside_hardlink_cannot_hide_size_change(self):
        self.assertEqual(self.cache.usage(), (5, 1))
        outside = self.root.parent / 'outside-link'
        os.link(self.file, outside)
        outside.write_bytes(b'longer outside write')
        outside.unlink()
        self.assert_usage()


if __name__ == "__main__":
    unittest.main()
