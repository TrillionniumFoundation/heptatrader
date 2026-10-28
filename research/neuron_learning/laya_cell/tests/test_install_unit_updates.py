"""Only private ordinary unit files and mocked service commands."""
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import stat
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from laya_cell import immutable_files
from laya_cell.integrity import ContractError
from laya_cell.tests import test_install_resident_staging as resident_cases
from laya_cell.tests import test_install_special_artifacts as special_cases

KINDS = ('resident', 'install_financial_forward', 'install_temporal_fusion')


def version(path):
    s = path.stat()
    return s.st_dev, s.st_ino, s.st_mode, s.st_mtime_ns, s.st_ctime_ns, path.read_bytes()


class InstallerUnitUpdateTests(unittest.TestCase):
    @contextmanager
    def fixture(self, kind):
        if kind == 'resident':
            case = resident_cases.ResidentStagingTests('test_stage_creates_only_immutable_package_and_config')
            case.setUp()
            fixture = SimpleNamespace(module=resident_cases.installer, home=case.home,
                install=lambda update_owned=False: resident_cases.installer.install(
                    case.config_path, False, update_owned, 'evaluator'))
            name = 'heptatrader-laya-evaluator.service'
        else:
            case = special_cases.SpecialInstallerArtifactTests()
            case.setUp(); fixture = case.fixture(kind)
            name = ('heptatrader-financial-forward-registrar.service' if kind.endswith('forward')
                    else 'heptatrader-temporal-fusion-predictor.service')
        try:
            fixture.install()
            fixture.unit = fixture.home / '.config/systemd/user' / name
            fixture.original_text = fixture.unit.read_bytes()
            fixture.unit.write_bytes(fixture.original_text + b'# previous owned configuration\n')
            fixture.next = (fixture.unit.with_name(name + '.next') if kind == 'resident'
                            else fixture.unit.with_suffix('.next'))
            fixture.next.write_bytes(b'UNKNOWN NEXT FILE MUST SURVIVE\n'); fixture.next.chmod(0o600)
            with fixture.next.open('rb') as next_fd:
                fixture.next_fd = next_fd
                yield fixture
        finally:
            case.doCleanups()

    def test_owned_updates_preserve_unknown_fixed_next_files(self):
        for kind in KINDS:
            with self.subTest(installer=kind), self.fixture(kind) as f:
                before = version(f.next)
                f.install(update_owned=True)
                self.assertTrue(f.next.exists(), 'Unknown fixed .next must remain at its original name')
                self.assertEqual(version(f.next), before)
                self.assertEqual(f.unit.read_bytes(), f.original_text)
                self.assertEqual(stat.S_IMODE(f.unit.stat().st_mode), 0o600)
                self.assertNotEqual(f.unit.stat().st_ino, before[1])

    def test_unknown_unit_replaced_during_stop_is_preserved_and_refused(self):
        for kind in KINDS:
            for change in ('different_bytes', 'same_bytes_new_inode', 'same_inode_new_mode'):
                with self.subTest(installer=kind, change=change), self.fixture(kind) as f:
                    original_run = f.module.subprocess.run; changed = []; calls = []
                    def run(argv, **kwargs):
                        calls.append(argv)
                        result = original_run(argv, **kwargs)
                        if argv[:3] == ['systemctl', '--user', 'stop'] and argv[-1] == f.unit.name:
                            if change == 'same_inode_new_mode':
                                f.unit.chmod(0o400)
                            else:
                                replacement = f.unit.with_name('foreign-unit')
                                replacement.write_bytes(f.unit.read_bytes() if change == 'same_bytes_new_inode'
                                                        else b'UNRELATED CONCURRENT UNIT\n')
                                replacement.chmod(0o600); os.replace(replacement, f.unit)
                            changed.append(version(f.unit))
                        return result
                    next_before = version(f.next)
                    with patch.object(f.module.subprocess, 'run', side_effect=run), self.assertRaises(ContractError):
                        f.install(update_owned=True)
                    self.assertEqual(len(changed), 1)
                    self.assertEqual(version(f.unit), changed[0])
                    self.assertEqual(version(f.next), next_before)
                    self.assertEqual(calls, [['systemctl', '--user', 'stop', f.unit.name]])
                    self.assertEqual(list(f.unit.parent.glob('.' + f.unit.name + '.*')), [])

    def test_unit_temp_write_interruptions_preserve_old_unit_and_close_owned_files(self):
        for error in (KeyboardInterrupt('stop'), SystemExit(7), OSError('write failed')):
            with self.subTest(error=type(error).__name__), self.fixture('resident') as f:
                actual_write = os.write; before = version(f.unit); next_before = version(f.next)
                descriptors = len(os.listdir('/proc/self/fd')); injected = []
                def write(fd, raw):
                    temporary = Path(os.readlink('/proc/self/fd/' + str(fd)))
                    if temporary.parent == f.unit.parent and temporary.name.startswith('.' + f.unit.name + '.'):
                        injected.append(fd); raise error
                    return actual_write(fd, raw)
                with patch.object(immutable_files.os, 'write', side_effect=write), self.assertRaises(type(error)) as caught:
                    f.install(update_owned=True)
                self.assertIs(caught.exception, error); self.assertTrue(injected)
                self.assertEqual(version(f.unit), before); self.assertEqual(version(f.next), next_before)
                self.assertEqual(len(os.listdir('/proc/self/fd')), descriptors)
                for fd in injected:
                    with self.assertRaises(OSError): os.fstat(fd)
                self.assertEqual(list(f.unit.parent.glob('.' + f.unit.name + '.*')), [])

    def test_unit_temp_short_writes_complete_and_zero_write_fails_closed(self):
        for zero in (False, True):
            with self.subTest(zero=zero), self.fixture('resident') as f:
                actual_write = os.write; old = version(f.unit); next_before = version(f.next); injected = []
                def write(fd, raw):
                    temporary = Path(os.readlink('/proc/self/fd/' + str(fd)))
                    if temporary.parent == f.unit.parent and temporary.name.startswith('.' + f.unit.name + '.'):
                        injected.append(True)
                        return 0 if zero else actual_write(fd, raw[:7])
                    return actual_write(fd, raw)
                with patch.object(immutable_files.os, 'write', side_effect=write):
                    if zero:
                        with self.assertRaises(OSError): f.install(update_owned=True)
                        self.assertEqual(version(f.unit), old)
                    else:
                        f.install(update_owned=True); self.assertEqual(f.unit.read_bytes(), f.original_text)
                self.assertTrue(injected)
                self.assertTrue(f.next.exists())
                self.assertEqual(version(f.next), next_before)
                self.assertEqual(list(f.unit.parent.glob('.' + f.unit.name + '.*')), [])

    def test_foreign_temporary_replacement_is_neither_removed_nor_published(self):
        for interrupt in (False, True):
            with self.subTest(interrupt=interrupt), self.fixture('resident') as f:
                actual_write = os.write; old = version(f.unit); observed = []
                def write(fd, raw):
                    temporary = Path(os.readlink('/proc/self/fd/' + str(fd)))
                    if temporary.parent == f.unit.parent and temporary.name.startswith('.' + f.unit.name + '.'):
                        if not observed:
                            foreign = temporary.with_name('foreign-temp')
                            foreign.write_bytes(b'UNKNOWN TEMPORARY\n'); foreign.chmod(0o600)
                            os.replace(foreign, temporary)
                            observed.append((temporary, version(temporary), fd))
                        if interrupt: raise KeyboardInterrupt('interrupted after replacement')
                    return actual_write(fd, raw)
                with patch.object(immutable_files.os, 'write', side_effect=write):
                    with self.assertRaises(KeyboardInterrupt if interrupt else ContractError):
                        f.install(update_owned=True)
                self.assertEqual(len(observed), 1)
                temporary, expected, fd = observed[0]
                self.assertEqual(version(temporary), expected)
                self.assertEqual(version(f.unit), old)
                with self.assertRaises(OSError): os.fstat(fd)

    def test_secondary_cleanup_termination_is_raised_after_owned_descriptor_closes(self):
        for secondary in (OSError('cleanup failed'), KeyboardInterrupt('second stop'), SystemExit(9)):
            with self.subTest(secondary=type(secondary).__name__), self.fixture('resident') as f:
                original_write, original_unlink = os.write, Path.unlink
                primary = OSError('primary write failure'); observed = []; caught = None
                old, next_before = version(f.unit), version(f.next)
                def write(fd, raw):
                    temporary = Path(os.readlink('/proc/self/fd/' + str(fd)))
                    if temporary.parent == f.unit.parent and temporary.name.startswith('.' + f.unit.name + '.'):
                        observed.append((temporary, fd)); raise primary
                    return original_write(fd, raw)
                def unlink(path, *args, **kwargs):
                    result = original_unlink(path, *args, **kwargs)
                    if observed and path == observed[0][0]: raise secondary
                    return result
                with patch.object(immutable_files.os, 'write', side_effect=write), patch.object(Path, 'unlink', unlink):
                    try: f.install(update_owned=True)
                    except BaseException as error: caught = error
                self.assertEqual(len(observed), 1)
                self.assertIs(caught, primary if isinstance(secondary, Exception) else secondary)
                self.assertEqual(version(f.unit), old); self.assertEqual(version(f.next), next_before)
                self.assertFalse(observed[0][0].exists())
                with self.assertRaises(OSError): os.fstat(observed[0][1])

    def test_directory_close_termination_is_not_hidden_by_fsync_failure(self):
        with self.fixture('resident') as f:
            original_sync, original_close, original_write = os.fsync, os.close, os.write
            directory_identity = (f.unit.parent.stat().st_dev, f.unit.parent.stat().st_ino)
            primary, secondary = OSError('directory sync failure'), KeyboardInterrupt('second stop')
            directory_fds, temporary_fds = [], []
            def sync(fd):
                found = os.fstat(fd)
                if (found.st_dev, found.st_ino) == directory_identity:
                    directory_fds.append(fd); raise primary
                return original_sync(fd)
            def close(fd):
                result = original_close(fd)
                if directory_fds and fd == directory_fds[0]: raise secondary
                return result
            def write(fd, raw):
                temporary = Path(os.readlink('/proc/self/fd/' + str(fd)))
                if temporary.parent == f.unit.parent and temporary.name.startswith('.' + f.unit.name + '.'):
                    temporary_fds.append(fd)
                return original_write(fd, raw)
            with (patch.object(immutable_files.os, 'fsync', side_effect=sync),
                  patch.object(immutable_files.os, 'close', side_effect=close),
                  patch.object(immutable_files.os, 'write', side_effect=write)):
                caught = None
                try: f.install(update_owned=True)
                except BaseException as error: caught = error
            self.assertIs(caught, secondary)
            self.assertTrue(directory_fds); self.assertTrue(temporary_fds)
            for fd in set(directory_fds + temporary_fds):
                with self.assertRaises(OSError): os.fstat(fd)
            self.assertEqual(f.unit.read_bytes(), f.original_text)
            self.assertEqual(list(f.unit.parent.glob('.' + f.unit.name + '.*')), [])

    def test_backups_retain_exact_original_crlf_bytes_and_digest(self):
        for kind in KINDS:
            with self.subTest(installer=kind), self.fixture(kind) as f:
                raw = f.unit.read_bytes().replace(b'\n', b'\r\n'); f.unit.write_bytes(raw)
                next_before = version(f.next)
                f.install(update_owned=True)
                backups = list(f.home.rglob(f.unit.name + '.' + hashlib.sha256(raw).hexdigest() + '.before'))
                self.assertEqual(len(backups), 1)
                self.assertEqual(backups[0].read_bytes(), raw)
                self.assertEqual(version(f.next), next_before)
                self.assertEqual(f.unit.read_bytes(), f.original_text)

    def test_observed_unit_disappearing_before_snapshot_read_is_not_recreated(self):
        for kind in KINDS:
            for phase in ('stat', 'open'):
                with self.subTest(installer=kind, phase=phase), self.fixture(kind) as f:
                    original_stat, original_open = Path.stat, os.open
                    removed, calls = [], []; original_run = f.module.subprocess.run
                    def remove():
                        f.unit.unlink(); removed.append(True)
                        raise FileNotFoundError('concurrent unit removal')
                    def stat_path(path, *args, **kwargs):
                        if phase == 'stat' and path == f.unit and kwargs.get('follow_symlinks', True) and not removed:
                            remove()
                        return original_stat(path, *args, **kwargs)
                    def open_path(path, flags, *args, **kwargs):
                        if phase == 'open' and Path(path) == f.unit and not removed: remove()
                        return original_open(path, flags, *args, **kwargs)
                    def run(argv, **kwargs):
                        calls.append(argv); return original_run(argv, **kwargs)
                    caught = None
                    with (patch.object(Path, 'stat', stat_path), patch.object(immutable_files.os, 'open', side_effect=open_path),
                          patch.object(f.module.subprocess, 'run', side_effect=run)):
                        try: f.install(update_owned=True)
                        except BaseException as error: caught = error
                    self.assertIsInstance(caught, ContractError)
                    self.assertEqual(removed, [True]); self.assertFalse(f.unit.exists())
                    self.assertEqual(calls, [])

    def test_stage_secondary_cleanup_termination_closes_owned_descriptor(self):
        for secondary in (OSError('cleanup failed'), KeyboardInterrupt('second stop'), SystemExit(9)):
            with self.subTest(secondary=type(secondary).__name__):
                case = resident_cases.ResidentStagingTests('test_stage_creates_only_immutable_package_and_config')
                case.setUp()
                original_write, original_unlink = os.write, Path.unlink
                primary = OSError('primary stage write failure'); observed = []; caught = None
                def write(fd, raw):
                    temporary = Path(os.readlink('/proc/self/fd/' + str(fd)))
                    if temporary.is_relative_to(case.home) and temporary.name.startswith('.example.py.'):
                        found = os.fstat(fd)
                        observed.append((temporary, fd, found.st_dev, found.st_ino)); raise primary
                    return original_write(fd, raw)
                def unlink(path, *args, **kwargs):
                    result = original_unlink(path, *args, **kwargs)
                    if observed and path == observed[0][0]: raise secondary
                    return result
                try:
                    with patch.object(immutable_files.os, 'write', side_effect=write), patch.object(Path, 'unlink', unlink):
                        try: case.stage()
                        except BaseException as error: caught = error
                    self.assertEqual(len(observed), 1)
                    temporary, fd, device, inode = observed[0]
                    self.assertIs(caught, primary if isinstance(secondary, Exception) else secondary)
                    self.assertFalse(temporary.exists()); case.run.assert_not_called()
                    try: found = os.fstat(fd)
                    except OSError: closed = True
                    else: closed = False
                    try: self.assertTrue(closed, 'Stage must close its descriptor before secondary termination propagates')
                    finally:
                        if not closed:
                            self.assertEqual((found.st_dev, found.st_ino), (device, inode))
                            os.close(fd)  # Only this test-owned leaked inode, after recording failure.
                finally: case.doCleanups()

    def test_stage_directory_close_preserves_primary_but_propagates_termination(self):
        for secondary in (OSError('directory close failure'), KeyboardInterrupt('second stop')):
            with self.subTest(secondary=type(secondary).__name__):
                case = resident_cases.ResidentStagingTests('test_stage_creates_only_immutable_package_and_config')
                case.setUp()
                original_sync, original_close = os.fsync, os.close
                primary = OSError('primary directory sync failure'); directories = []; caught = None
                def sync(fd):
                    found = os.fstat(fd)
                    directory = Path(os.readlink('/proc/self/fd/' + str(fd)))
                    if stat.S_ISDIR(found.st_mode) and directory.is_relative_to(case.home):
                        directories.append((fd, found.st_dev, found.st_ino)); raise primary
                    return original_sync(fd)
                def close(fd):
                    result = original_close(fd)
                    if directories and fd == directories[0][0]: raise secondary
                    return result
                try:
                    with patch.object(immutable_files.os, 'fsync', side_effect=sync), patch.object(immutable_files.os, 'close', side_effect=close):
                        try: case.stage()
                        except BaseException as error: caught = error
                    self.assertEqual(len(directories), 1)
                    self.assertIs(caught, primary if isinstance(secondary, Exception) else secondary)
                    with self.assertRaises(OSError): os.fstat(directories[0][0])
                    case.run.assert_not_called()
                    self.assertFalse([p for p in case.home.rglob('*') if p.name.startswith('.') and p.is_file()])
                finally: case.doCleanups()

    def test_resident_first_unit_creation_never_overwrites_a_competitor(self):
        for matching in (False, True):
            with self.subTest(matching=matching), self.fixture('resident') as f:
                f.unit.unlink(); content = f.original_text if matching else b'UNKNOWN NEW UNIT\n'
                actual_exists, actual_link = Path.exists, os.link; actual_open, actual_write = os.open, os.write
                observed = []; exists_checks = []
                def competitor():
                    if not observed:
                        fd = actual_open(f.unit, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o600)
                        try: self.assertEqual(actual_write(fd, content), len(content))
                        finally: os.close(fd)
                        observed.append(version(f.unit))
                def exists(path):
                    result = actual_exists(path)
                    if path == f.unit and not result:
                        exists_checks.append(True)
                        if len(exists_checks) == 2: competitor()
                    return result
                def link(source, target, *args, **kwargs):
                    if Path(target) == f.unit: competitor()
                    return actual_link(source, target, *args, **kwargs)
                with patch.object(Path, 'exists', exists), patch.object(immutable_files.os, 'link', side_effect=link):
                    if matching: f.install()
                    else:
                        with self.assertRaises(ContractError): f.install()
                self.assertEqual(len(observed), 1)
                self.assertEqual(version(f.unit), observed[0])


if __name__ == '__main__': unittest.main()
