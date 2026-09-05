from __future__ import annotations

import contextlib
import errno
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import test_gap_closure as fixtures

BOUNDARY = fixtures.BOUNDARY
CHECKER = fixtures.CHECKER
PAYLOAD = b'{"fixture_only":true}'


class ReceiptFileBoundaryTests(unittest.TestCase):
    """Temporary local fixtures only; no real governance/Broker evidence."""
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.outer = Path(self.temp.name)
        self.root = self.outer / 'evidence'
        self.root.mkdir(mode=0o700)
        self.path = self.root / 'receipt.json'
        self.path.write_bytes(PAYLOAD)
        self.path.chmod(0o600)

    def fd_set(self) -> set[int]:
        # The listing descriptor closes before the explicit fstat probes.
        candidates = [int(name) for name in os.listdir('/proc/self/fd')]
        result = set()
        for fd in candidates:
            try:
                os.fstat(fd)
                result.add(fd)
            except OSError:
                pass
        return result

    def patch_os(self, name: str, replacement: object) -> contextlib.ExitStack:
        stack = contextlib.ExitStack()
        old = getattr(os, name)
        stack.enter_context(mock.patch.object(BOUNDARY.os, name, replacement))
        # Preserve real platform capability declarations when instrumenting the
        # very function whose identity appears in Python's capability sets.
        for attribute in ('supports_dir_fd', 'supports_follow_symlinks'):
            values = getattr(os, attribute)
            if old in values:
                stack.enter_context(mock.patch.object(BOUNDARY.os, attribute,
                                                       (values - {old}) | {replacement}))
        return stack

    def read(self, root: Path | None = None, relative: object = 'receipt.json') -> dict:
        return BOUNDARY.read_receipt(self.root if root is None else root, relative)

    def test_valid_and_relative_roots_are_read_only(self) -> None:
        before = self.fd_set()
        metadata = self.path.stat()
        self.assertEqual({'fixture_only': True}, self.read())
        cwd = Path.cwd()
        try:
            os.chdir(self.outer)
            self.assertEqual({'fixture_only': True}, self.read(Path('evidence')))
            os.chdir(self.root)
            self.assertEqual({'fixture_only': True}, self.read(Path('.')))
        finally:
            os.chdir(cwd)
        after = self.path.stat()
        self.assertEqual((metadata.st_ino, metadata.st_mode, metadata.st_size,
                          metadata.st_mtime_ns, metadata.st_ctime_ns),
                         (after.st_ino, after.st_mode, after.st_size,
                          after.st_mtime_ns, after.st_ctime_ns))
        self.assertEqual(PAYLOAD, self.path.read_bytes())
        self.assertEqual(before, self.fd_set())

    def test_selected_root_symlink_is_rejected(self) -> None:
        alias = self.outer / 'alias'
        alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.read(alias)

    def test_ancestor_symlink_is_rejected(self) -> None:
        alias = self.outer / 'alias'
        alias.symlink_to(self.outer, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.read(alias / 'evidence')

    def test_world_writable_root_including_sticky_root_is_rejected(self) -> None:
        for mode in (0o777, 0o1777):
            with self.subTest(mode=mode):
                self.root.chmod(mode)
                with self.assertRaisesRegex(ValueError, 'world-writable'):
                    self.read()
        self.root.chmod(0o700)

    def test_only_sticky_shared_ancestors_outside_root_are_permitted(self) -> None:
        self.outer.chmod(0o777)
        with self.assertRaisesRegex(ValueError, 'world-writable'):
            self.read()
        self.outer.chmod(0o1777)
        self.assertEqual({'fixture_only': True}, self.read())
        self.outer.chmod(0o700)

    def test_world_writable_descendant_remains_rejected(self) -> None:
        nested = self.root / 'nested'
        nested.mkdir(mode=0o700)
        (nested / 'receipt.json').write_bytes(PAYLOAD)
        nested.chmod(0o1777)
        with self.assertRaisesRegex(ValueError, 'world-writable'):
            self.read(relative='nested/receipt.json')

    def test_root_parent_nul_and_double_anchor_are_rejected(self) -> None:
        for root in (self.root / '..' / 'evidence', Path('//tmp/example'), Path('/tmp/nul\0x')):
            with self.subTest(root=str(root)):
                with self.assertRaisesRegex(ValueError, 'root path is unsafe'):
                    self.read(root)

    def test_unsafe_relative_paths_fail_before_open(self) -> None:
        for relative in ('..', '.', '../x', 'a/../b', 'a//b', 'a/./b', '/tmp/x', 'a\\b', 'x\0', '', None):
            with self.subTest(relative=relative):
                with mock.patch.object(BOUNDARY.os, 'open') as opened:
                    with self.assertRaises(ValueError):
                        self.read(relative=relative)
                    opened.assert_not_called()

    def test_exact_directory_depth_and_one_over(self) -> None:
        existing = len(self.root.parts) - 1
        depth = BOUNDARY.MAX_RECEIPT_DIRECTORIES - existing
        sub = self.root
        for _ in range(depth):
            sub = sub / 'd'
            sub.mkdir(mode=0o700)
        (sub / 'receipt.json').write_bytes(PAYLOAD)
        relative = '/'.join(['d'] * depth + ['receipt.json'])
        before = self.fd_set()
        opened_count = 0
        original = os.open
        def opened(*args: object, **kwargs: object) -> int:
            nonlocal opened_count
            fd = original(*args, **kwargs)
            opened_count += 1
            return fd
        with self.patch_os('open', opened):
            self.assertEqual({'fixture_only': True}, self.read(relative=relative))
        self.assertEqual(BOUNDARY.MAX_RECEIPT_DIRECTORIES + 2, opened_count)
        self.assertEqual(before, self.fd_set())
        with mock.patch.object(BOUNDARY.os, 'open') as opened:
            with self.assertRaisesRegex(ValueError, 'depth exceeds'):
                self.read(relative='d/' + relative)
            opened.assert_not_called()

    def test_full_path_and_component_byte_limits_precede_open(self) -> None:
        # Build an exactly admitted 4,096-byte lexical path with bounded leaves.
        root = Path('/safe')
        remaining = BOUNDARY.MAX_RECEIPT_PATH_BYTES - len(os.fsencode(root)) - 1
        pieces = []
        while remaining > 255:
            pieces.append('a' * 255)
            remaining -= 256
        pieces.append('z' * remaining)
        relative = '/'.join(pieces)
        self.assertEqual(4096, len(os.fsencode(root / relative)))
        self.assertEqual(pieces[-1], BOUNDARY._checked_path(root, relative)[2])
        for name in (relative + 'z', 'x' * 256, '\u754c' * 86):
            with self.subTest(name=name[:20]):
                with mock.patch.object(BOUNDARY.os, 'open') as opened:
                    with self.assertRaisesRegex(ValueError, 'byte limit'):
                        self.read(root, name)
                    opened.assert_not_called()

    def test_exact_payload_ceiling_and_sparse_oversize_before_read(self) -> None:
        self.path.write_bytes(PAYLOAD + b' ' * (BOUNDARY.MAX_RECEIPT_BYTES - len(PAYLOAD)))
        self.assertEqual({'fixture_only': True}, self.read())
        with self.path.open('wb') as stream:
            stream.truncate(BOUNDARY.MAX_RECEIPT_BYTES + 1)
        with mock.patch.object(BOUNDARY.os, 'read') as read:
            with self.assertRaisesRegex(ValueError, 'size is invalid'):
                self.read()
            read.assert_not_called()

    def test_short_and_interrupted_reads_preserve_bytes_and_cleanup(self) -> None:
        before = self.fd_set()
        original = os.read
        calls = 0
        def read(fd: int, size: int) -> bytes:
            nonlocal calls
            calls += 1
            if calls in (1, 4):
                raise InterruptedError(errno.EINTR, 'test-only interrupted read')
            return original(fd, min(size, 2))
        with mock.patch.object(BOUNDARY.os, 'read', read):
            self.assertEqual({'fixture_only': True}, self.read())
        self.assertGreater(calls, len(PAYLOAD) // 2)
        self.assertEqual(before, self.fd_set())

    def test_growing_and_truncated_leaf_never_decodes(self) -> None:
        for growth in (False, True):
            with self.subTest(growth=growth):
                self.path.write_bytes(PAYLOAD)
                original = os.read
                mutated = False
                def read(fd: int, size: int) -> bytes:
                    nonlocal mutated
                    data = original(fd, min(size, 1))
                    if not mutated:
                        mutated = True
                        if growth:
                            with self.path.open('ab') as stream:
                                stream.write(b' ')
                        else:
                            with self.path.open('wb'):
                                pass
                    return data
                with mock.patch.object(BOUNDARY.os, 'read', read), mock.patch.object(BOUNDARY, 'decode_object') as decode:
                    with self.assertRaisesRegex(ValueError, 'length changed'):
                        self.read()
                    decode.assert_not_called()

    def test_identical_leaf_replacement_during_decode_is_rejected(self) -> None:
        original = BOUNDARY.decode_object
        def decode(data: bytes) -> dict:
            value = original(data)
            replacement = self.root / 'replacement.json'
            replacement.write_bytes(data)
            replacement.chmod(0o600)
            replacement.replace(self.path)
            return value
        with mock.patch.object(BOUNDARY, 'decode_object', decode):
            with self.assertRaisesRegex(ValueError, 'identity changed'):
                self.read()

    def test_in_place_mutation_during_decode_is_rejected(self) -> None:
        original = BOUNDARY.decode_object
        def decode(data: bytes) -> dict:
            value = original(data)
            self.path.write_bytes(data + b' ')
            return value
        with mock.patch.object(BOUNDARY, 'decode_object', decode):
            with self.assertRaisesRegex(ValueError, 'identity changed'):
                self.read()

    def test_root_rebinding_during_decode_is_rejected(self) -> None:
        original = BOUNDARY.decode_object
        def decode(data: bytes) -> dict:
            value = original(data)
            self.root.rename(self.outer / 'old-evidence')
            self.root.mkdir(mode=0o700)
            self.path.write_bytes(data)
            return value
        with mock.patch.object(BOUNDARY, 'decode_object', decode):
            with self.assertRaisesRegex(ValueError, 'directory binding changed'):
                self.read()

    def test_root_ancestor_rebinding_during_decode_is_rejected(self) -> None:
        top = self.root / 'top'
        selected = top / 'selected'
        selected.mkdir(parents=True, mode=0o700)
        (selected / 'receipt.json').write_bytes(PAYLOAD)
        original = BOUNDARY.decode_object
        def decode(data: bytes) -> dict:
            value = original(data)
            top.rename(self.root / 'old-top')
            selected.mkdir(parents=True, mode=0o700)
            (selected / 'receipt.json').write_bytes(data)
            return value
        with mock.patch.object(BOUNDARY, 'decode_object', decode):
            with self.assertRaisesRegex(ValueError, 'directory binding changed'):
                self.read(selected)

    def test_permission_change_during_decode_is_rejected_even_when_not_world_writable(self) -> None:
        original = BOUNDARY.decode_object
        def decode(data: bytes) -> dict:
            value = original(data)
            self.root.chmod(0o750)
            return value
        with mock.patch.object(BOUNDARY, 'decode_object', decode):
            with self.assertRaisesRegex(ValueError, 'directory binding changed'):
                self.read()

    def test_unrelated_sibling_creation_does_not_change_selected_binding(self) -> None:
        original = BOUNDARY.decode_object
        def decode(data: bytes) -> dict:
            value = original(data)
            (self.root / 'unrelated').mkdir(mode=0o700)
            return value
        with mock.patch.object(BOUNDARY, 'decode_object', decode):
            self.assertEqual({'fixture_only': True}, self.read())

    def test_relative_root_is_anchored_once_not_reinterpreted_after_chdir(self) -> None:
        cwd = Path.cwd()
        original = BOUNDARY.decode_object
        def decode(data: bytes) -> dict:
            value = original(data)
            os.chdir(self.root)
            return value
        try:
            os.chdir(self.outer)
            with mock.patch.object(BOUNDARY, 'decode_object', decode):
                self.assertEqual({'fixture_only': True}, self.read(Path('evidence')))
        finally:
            os.chdir(cwd)

    def test_missing_secure_primitives_have_no_fallback(self) -> None:
        for name in ('O_NOFOLLOW', 'O_DIRECTORY', 'O_NONBLOCK'):
            with self.subTest(name=name), mock.patch.object(BOUNDARY.os, name):
                delattr(BOUNDARY.os, name)
                with self.assertRaisesRegex(ValueError, 'secure receipt reads require'):
                    self.read()
        with mock.patch.object(BOUNDARY.os, 'supports_dir_fd', set()):
            with self.assertRaisesRegex(ValueError, 'directory-relative'):
                self.read()
        with mock.patch.object(BOUNDARY.os, 'supports_follow_symlinks', set()):
            with self.assertRaisesRegex(ValueError, 'no-follow metadata'):
                self.read()

    def test_each_open_failure_releases_all_previously_owned_descriptors(self) -> None:
        before = self.fd_set()
        total = len(self.root.parts) + 1
        for ordinal in range(1, total + 1):
            calls = 0
            original = os.open
            def opened(*args: object, **kwargs: object) -> int:
                nonlocal calls
                calls += 1
                if calls == ordinal:
                    raise OSError(errno.EMFILE, 'test-only acquisition failure')
                return original(*args, **kwargs)
            with self.subTest(ordinal=ordinal), self.patch_os('open', opened):
                with self.assertRaises(ValueError):
                    self.read()
            self.assertEqual(ordinal, calls)
            self.assertEqual(before, self.fd_set())

    def test_each_metadata_failure_releases_all_owned_descriptors(self) -> None:
        before = self.fd_set()
        reached_success = False
        for ordinal in range(1, 80):
            calls = 0
            original = os.fstat
            def fstat(fd: int) -> os.stat_result:
                nonlocal calls
                calls += 1
                if calls == ordinal:
                    raise OSError(errno.EIO, 'test-only metadata failure')
                return original(fd)
            with mock.patch.object(BOUNDARY.os, 'fstat', fstat):
                if ordinal <= 3 * len(self.root.parts) + 3:
                    with self.assertRaises(ValueError):
                        self.read()
                else:
                    self.assertEqual({'fixture_only': True}, self.read())
                    reached_success = True
            self.assertEqual(before, self.fd_set())
            if reached_success:
                break
        self.assertTrue(reached_success)

    def test_decode_exceptions_preserve_failure_and_release_descriptors(self) -> None:
        before = self.fd_set()
        for error in (ValueError('test parse failure'), MemoryError('test allocation failure'), RecursionError('test depth')):
            with self.subTest(error=type(error).__name__), mock.patch.object(BOUNDARY, 'decode_object', side_effect=error):
                with self.assertRaises(type(error)):
                    self.read()
            self.assertEqual(before, self.fd_set())

    def test_each_close_failure_cleans_remaining_descriptors_and_suppresses_success(self) -> None:
        before = self.fd_set()
        total = len(self.root.parts) + 1
        for ordinal in range(1, total + 1):
            calls = 0
            original = os.close
            def close(fd: int) -> None:
                nonlocal calls
                calls += 1
                original(fd)
                if calls == ordinal:
                    raise OSError(errno.EIO, 'test-only close failure after actual close')
            with self.subTest(ordinal=ordinal), mock.patch.object(BOUNDARY.os, 'close', close):
                with self.assertRaisesRegex(ValueError, 'secure receipt close failed'):
                    self.read()
            self.assertEqual(total, calls)
            self.assertEqual(before, self.fd_set())

    def test_multiple_close_errors_do_not_stop_cleanup_or_retry_fds(self) -> None:
        before = self.fd_set()
        closed = []
        original = os.close
        def close(fd: int) -> None:
            self.assertNotIn(fd, closed)
            closed.append(fd)
            original(fd)
            raise OSError(errno.EIO, 'test-only close failure after actual close')
        with mock.patch.object(BOUNDARY.os, 'close', close):
            with self.assertRaisesRegex(ValueError, 'secure receipt close failed'):
                self.read()
        self.assertEqual(len(self.root.parts) + 1, len(closed))
        self.assertEqual(before, self.fd_set())

    def test_opened_descriptors_are_not_inheritable(self) -> None:
        original = os.open
        seen = []
        def opened(*args: object, **kwargs: object) -> int:
            fd = original(*args, **kwargs)
            seen.append(os.get_inheritable(fd))
            return fd
        with self.patch_os('open', opened):
            self.assertEqual({'fixture_only': True}, self.read())
        self.assertTrue(seen)
        self.assertEqual({False}, set(seen))

    def test_closed_gate_evaluation_rejects_aliased_receipt_root_without_a_report(self) -> None:
        for kind in ('ib', 'governance'):
            fixture = fixtures.GapClosureTests()
            fixture.setUp()
            self.addCleanup(fixture.doCleanups)
            if kind == 'ib':
                fixture.install_ib()
            else:
                fixture.install_governance()
            fixture.validate()
            alias = fixture.root / 'alias'
            alias.symlink_to(fixture.root, target_is_directory=True)
            errors, report = CHECKER.evaluate(fixture.gap_path, fixture.module_path,
                repository_root=fixture.root, receipt_root=alias,
                expected_source_sha=fixtures.SOURCE,
                expected_merge_group_sha=fixtures.MERGE, expected_pull_number=17)
            self.assertTrue(errors)
            self.assertIsNone(report)


if __name__ == '__main__':
    unittest.main()
