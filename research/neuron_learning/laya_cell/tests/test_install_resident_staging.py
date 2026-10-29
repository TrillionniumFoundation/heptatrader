"""Immutable staging must not install units or alter a running research spool."""
from contextlib import ExitStack, redirect_stdout
import io
import json
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from laya_cell import install_resident as installer
from laya_cell.integrity import ContractError, sha_file

PRIVATE_PYTHON_RUN = subprocess.run
REAL_RUNTIME_FILES = installer.runtime_files


class ResidentStagingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / 'home'
        self.home.mkdir()
        self.source = self.root / 'module.py'
        self.source.write_text('VALUE = 1\n')
        self.binary = self.root / 'native'
        self.binary.write_bytes(b'fixed native fixture\n')
        self.config = {
            'automatic_trading': False,
            'native_binary': str(self.binary),
            'native_binary_sha256': sha_file(self.binary),
            'python': '/usr/bin/python3',
            **{key: str(self.root / key) for key in
               ('control_directory', 'feed_directory', 'evaluation_directory', 'candidate_directory')},
        }
        self.config_path = self.root / 'config.json'
        self.config_path.write_text(json.dumps(self.config))
        self.files = {'learn/laya_cell/example.py': self.source,
                      'bin/hepta-trader-neuron-native': self.binary}
        self.context = ExitStack()
        self.addCleanup(self.context.close)
        self.context.enter_context(patch.object(installer.Path, 'home', return_value=self.home))
        self.context.enter_context(patch.object(installer.os, 'geteuid', return_value=1000))
        self.context.enter_context(patch.object(installer, 'runtime_files', return_value=self.files))
        self.run = self.context.enter_context(patch.object(installer.subprocess, 'run'))
        self.context.enter_context(redirect_stdout(io.StringIO()))

    def stage(self):
        return installer.install(self.config_path, False, stage_only=True)

    def test_stage_creates_only_immutable_package_and_config(self):
        result = self.stage()
        self.run.assert_not_called()
        self.assertEqual(result['schema'], 'heptatrader.laya-resident-stage.v1')
        self.assertFalse(result['unit_files_changed'])
        self.assertFalse(result['runtime_state_changed'])
        self.assertFalse(result['configuration_execution_validated'])
        self.assertFalse((self.home / '.config/systemd').exists())
        for key in ('control_directory', 'feed_directory', 'evaluation_directory', 'candidate_directory'):
            self.assertFalse(Path(self.config[key]).exists())
        runtime = Path(result['release'])
        for relative, expected in result['runtime_sha256'].items():
            self.assertEqual(sha_file(runtime / relative), expected)
        config = Path(result['configuration'])
        self.assertEqual(sha_file(config), result['configuration_sha256'])
        self.assertEqual(json.loads(config.read_text())['runtime_directory'], str(runtime))

    def hip_fixture(self):
        from laya_cell.resident_hip_runtime import MANIFEST,PROFILE
        runtime=self.root/'original-runtime';manifest=runtime/MANIFEST
        manifest.parent.mkdir(parents=True)
        manifest.write_text(json.dumps({'schema':'heptatrader.resident-hip-runtime.v1',
            'overlay':{'original_core':'/unchanged/core','candidate_sha256':'a'*64}})+'\n')
        manifest.chmod(0o400)
        self.config.update(runtime_directory=str(runtime),runtime_sha256={MANIFEST:sha_file(manifest)},
            hip_runtime={'profile':PROFILE,'manifest':MANIFEST,'sha256':sha_file(manifest)})
        self.config_path.write_text(json.dumps(self.config))
        return manifest

    def test_code_upgrade_retains_exact_selected_hip_manifest_and_config(self):
        original=self.hip_fixture();before=original.read_bytes();selection=dict(self.config['hip_runtime'])
        result=self.stage();new=Path(result['release'])/selection['manifest']
        self.assertEqual(new.read_bytes(),before);self.assertEqual(original.read_bytes(),before)
        self.assertEqual(result['runtime_sha256'][selection['manifest']],selection['sha256'])
        self.assertEqual(json.loads(Path(result['configuration']).read_text())['hip_runtime'],selection)
        self.assertEqual(self.stage(),result);self.run.assert_not_called()

    def test_changed_selected_hip_bytes_fail_before_staging(self):
        original=self.hip_fixture();original.chmod(0o600);original.write_text('{}\n')
        with self.assertRaisesRegex(ContractError,'RETAINED_HIP_MANIFEST_CHANGED'):self.stage()
        self.assertEqual(list(self.home.iterdir()),[]);self.run.assert_not_called()

    def test_unbound_or_redirected_hip_manifest_cannot_be_carried_forward(self):
        original=self.hip_fixture();self.config['runtime_sha256']['gpu/hip-runtime.json']='b'*64
        self.config_path.write_text(json.dumps(self.config))
        with self.assertRaisesRegex(ContractError,'RETAINED_HIP_MANIFEST_SELECTION_CHANGED'):self.stage()
        self.config['runtime_sha256']['gpu/hip-runtime.json']=self.config['hip_runtime']['sha256']
        self.config_path.write_text(json.dumps(self.config))
        target=self.root/'redirected.json';original.rename(target);original.symlink_to(target)
        with self.assertRaisesRegex(ContractError,'RETAINED_HIP_MANIFEST_SOURCE_UNSAFE'):self.stage()
        self.assertEqual(list(self.home.iterdir()),[]);self.run.assert_not_called()

    def test_hip_change_between_inventory_and_copy_cannot_publish_configuration(self):
        original=self.hip_fixture();actual=installer.sha_file;mutated=False
        def changing(path):
            nonlocal mutated
            result=actual(path)
            if Path(path)==original and not mutated:
                original.chmod(0o600);original.write_bytes(b'changed after inventory\n');mutated=True
            return result
        with patch.object(installer,'sha_file',side_effect=changing):
            with self.assertRaisesRegex(ContractError,'IMMUTABLE_RESIDENT_RELEASE_CHANGED'):self.stage()
        self.assertTrue(mutated);self.assertFalse(list((self.home/'.config').rglob('*.json')))
        self.run.assert_not_called()

    def test_stage_preserves_existing_units_and_spool_bytes(self):
        unit = self.home / '.config/systemd/user/heptatrader-laya-evaluator.service'
        unit.parent.mkdir(parents=True)
        unit.write_text('[Service]\nExecStart=/original/evaluator\n')
        control = Path(self.config['control_directory'])
        control.mkdir()
        sentinel = control / 'existing.sqlite'
        sentinel.write_bytes(b'existing scheduling state')
        before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in (unit, sentinel)}
        self.stage()
        for path, (content, modified) in before.items():
            self.assertEqual(path.read_bytes(), content)
            self.assertEqual(path.stat().st_mtime_ns, modified)
        self.run.assert_not_called()

    def test_repeated_stage_reuses_exact_content_and_config(self):
        first = self.stage()
        config = Path(first['configuration'])
        before = config.stat().st_mtime_ns
        self.assertEqual(self.stage(), first)
        self.assertEqual(config.stat().st_mtime_ns, before)
        self.run.assert_not_called()

    def test_staging_rejects_activation_arguments_before_writing(self):
        for options in ({'start': True}, {'start': False, 'update_owned': True},
                        {'start': False, 'only': 'evaluator'}):
            with self.subTest(options=options), self.assertRaisesRegex(
                    ContractError, 'STAGING_CANNOT_REQUEST_SERVICE_CHANGES'):
                installer.install(self.config_path, stage_only=True, **options)
        self.assertEqual(list(self.home.iterdir()), [])
        self.run.assert_not_called()

    def test_changed_immutable_package_is_rejected_without_repairing_it(self):
        result = self.stage()
        path = Path(result['release']) / 'learn/laya_cell/example.py'
        path.chmod(0o600)
        path.write_text('changed\n')
        with self.assertRaisesRegex(ContractError, 'IMMUTABLE_RESIDENT_RELEASE_CHANGED'):
            self.stage()
        self.assertEqual(path.read_text(), 'changed\n')
        self.run.assert_not_called()

    def test_existing_install_mode_still_installs_and_reloads_without_start(self):
        result = installer.install(self.config_path, False)
        self.assertEqual(result['schema'], 'heptatrader.laya-resident-install.v1')
        self.assertEqual(len(result['units']), 3)
        self.run.assert_called_once_with(['systemctl', '--user', 'daemon-reload'], check=True)
        for entry in result['units'].values():
            self.assertTrue(Path(entry['path']).is_file())

    def test_source_changed_after_inventory_cannot_publish_wrong_immutable_bytes(self):
        actual = installer.sha_file
        mutated = False
        def changing(path):
            nonlocal mutated
            value = actual(path)
            if Path(path) == self.source and not mutated:
                self.source.write_text('VALUE = 2\n')
                mutated = True
            return value
        with patch.object(installer, 'sha_file', changing):
            with self.assertRaisesRegex(ContractError, 'IMMUTABLE_RESIDENT_RELEASE_CHANGED'):
                self.stage()
        self.assertTrue(mutated)
        self.assertFalse(list(self.home.rglob('example.py')))
        self.assertFalse(list(self.home.rglob('*.json')))
        self.assertFalse([p for p in self.home.rglob('*') if p.is_file()])
        self.run.assert_not_called()

    def test_actual_written_bytes_are_verified_before_canonical_publication(self):
        actual = os.fsync
        corrupted = False
        def corrupt(fd):
            nonlocal corrupted
            actual(fd)
            path = Path(os.readlink('/proc/self/fd/' + str(fd)))
            if path.is_relative_to(self.home) and '/releases/' in str(path) and not corrupted:
                os.pwrite(fd, b'X', 0)
                corrupted = True
        with patch.object(installer.os, 'fsync', corrupt):
            with self.assertRaisesRegex(ContractError, 'IMMUTABLE_RESIDENT_RELEASE_CHANGED'):
                self.stage()
        self.assertTrue(corrupted)
        self.assertFalse([p for p in self.home.rglob('*') if p.is_file()])
        self.run.assert_not_called()

    def competing_write(self, action, target, content, mode):
        """Inject a real competing file at either old or no-clobber publication."""
        actual_open, actual_link = Path.open, os.link
        actual_fd_open, actual_write = os.open, os.write
        winner = []
        def compete():
            if winner:
                return
            fd = actual_fd_open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o600)
            try:
                self.assertEqual(actual_write(fd, content), len(content))
            finally:
                os.close(fd)
            target.chmod(mode)
            winner.append(target.stat())
        def open_path(path, mode='r', *args, **kwargs):
            if path == target and any(flag in mode for flag in ('w', 'x', 'a')):
                compete()
            return actual_open(path, mode, *args, **kwargs)
        def link(source, destination, *args, **kwargs):
            if Path(destination) == target:
                compete()
            return actual_link(source, destination, *args, **kwargs)
        with patch.object(Path, 'open', open_path), patch.object(installer.os, 'link', link):
            try:
                return action()
            finally:
                self.assertEqual(len(winner), 1)
                self.assertEqual(target.read_bytes(), content)
                self.assertEqual(target.stat().st_ino, winner[0].st_ino)
                self.assertEqual(target.stat().st_mtime_ns, winner[0].st_mtime_ns)
                self.assertEqual(target.stat().st_mode, winner[0].st_mode)

    def receipt_target(self, kind):
        action = self.stage if kind != 'install' else lambda: installer.install(self.config_path, False)
        result = action()
        config = Path(result['configuration'])
        target = config if kind == 'configuration' else config.with_suffix('.' + kind + '.json')
        raw = target.read_bytes()
        target.unlink()
        self.run.reset_mock()
        return action, target, raw

    def test_config_and_both_receipts_do_not_overwrite_concurrent_files(self):
        reasons = {'configuration':'IMMUTABLE_CONFIGURATION_CHANGED',
                   'stage':'STAGE_RECEIPT_CHANGED', 'install':'INSTALL_RECEIPT_CHANGED'}
        for kind, reason in reasons.items():
            with self.subTest(kind=kind):
                action, target, valid = self.receipt_target(kind)
                try:
                    with self.assertRaisesRegex(ContractError, reason):
                        self.competing_write(action, target, b'competing evidence\n', 0o600)
                finally:
                    target.unlink()
                    target.write_bytes(valid)
                    target.chmod(0o400)
                self.assertEqual(len(self.run.call_args_list), int(kind == 'install'))

    def test_matching_concurrent_config_and_receipts_reuse_without_touching(self):
        for kind in ('configuration', 'stage', 'install'):
            with self.subTest(kind=kind):
                action, target, valid = self.receipt_target(kind)
                self.competing_write(action, target, valid, 0o400)
                self.assertEqual(len(self.run.call_args_list), int(kind == 'install'))

    def test_existing_service_backup_collision_is_preserved_before_stop(self):
        result = self.stage()
        unit = self.home / '.config/systemd/user/heptatrader-laya-evaluator.service'
        unit.parent.mkdir(parents=True)
        unit.write_text('Description=HeptaTrader original\nExecStart=/laya-resident/releases/original/bin\n')
        before = unit.read_bytes()
        backup = Path(result['configuration']).parent / (unit.name + '.' + sha_file(unit) + '.before')
        action = lambda: installer.install(self.config_path, False, True, 'evaluator')
        with self.assertRaisesRegex(ContractError, 'IMMUTABLE_SERVICE_BACKUP_CHANGED'):
            self.competing_write(action, backup, b'competing backup evidence\n', 0o600)
        self.assertEqual(unit.read_bytes(), before)
        self.run.assert_not_called()

    def test_write_termination_closes_owned_descriptors_and_unpublished_temporary(self):
        for error in (KeyboardInterrupt('operator stop'), SystemExit(7), OSError('short device')):
            with self.subTest(error=type(error).__name__):
                before = len(os.listdir('/proc/self/fd'))
                with patch.object(installer.os, 'write', side_effect=error):
                    with self.assertRaises(type(error)) as caught:
                        self.stage()
                self.assertIs(caught.exception, error)
                self.assertEqual(len(os.listdir('/proc/self/fd')), before)
                self.assertFalse([p for p in self.home.rglob('*') if p.is_file()])
        self.run.assert_not_called()

    def test_source_alias_remains_supported_but_package_is_regular_copy(self):
        alias = self.root / 'source-alias.py'
        alias.symlink_to(self.source)
        self.files['learn/laya_cell/example.py'] = alias
        result = self.stage()
        copied = Path(result['release']) / 'learn/laya_cell/example.py'
        self.assertFalse(copied.is_symlink())
        self.assertEqual(copied.read_bytes(), self.source.read_bytes())
        self.assertEqual(sha_file(copied), result['runtime_sha256']['learn/laya_cell/example.py'])

    def test_actual_package_collector_includes_shared_publication_dependency(self):
        from laya_cell import immutable_files
        source = Path(installer.__file__).resolve().parent
        # Leave the real collector unmocked: the future installed importer
        # must receive both its helper and that helper's integrity dependency.
        files = REAL_RUNTIME_FILES(source, self.binary)
        self.assertEqual(files['learn/laya_cell/immutable_files.py'], Path(immutable_files.__file__))
        self.assertEqual(files['learn/laya_cell/integrity.py'], source / 'integrity.py')
        self.assertEqual(files['learn/laya_cell/install_resident.py'], Path(installer.__file__))
        self.run.assert_not_called()

    def test_source_changed_to_fifo_after_hash_is_rejected_without_blocking(self):
        code = r'''
import json, os, signal
from pathlib import Path
from unittest.mock import patch
from laya_cell.tests.test_install_resident_staging import ResidentStagingTests
from laya_cell import install_resident as installer
from laya_cell.integrity import ContractError
t=ResidentStagingTests('test_stage_creates_only_immutable_package_and_config');t.setUp()
actual=installer.sha_file;changed=False
def changing(path):
 global changed
 value=actual(path)
 if Path(path)==t.source and not changed:
  t.source.unlink();os.mkfifo(t.source,0o600);changed=True
 return value
def timeout(*_):raise TimeoutError('SOURCE_FIFO_BLOCKED')
signal.signal(signal.SIGALRM,timeout);signal.setitimer(signal.ITIMER_REAL,1)
try:
 with patch.object(installer,'sha_file',changing):t.stage()
 result={'state':'unexpected_success'}
except ContractError as error:result={'state':'rejected','reason':str(error)}
except TimeoutError:result={'state':'blocked'}
finally:
 signal.setitimer(signal.ITIMER_REAL,0);t.doCleanups()
assert changed
print(json.dumps(result))
'''
        package = Path(__file__).resolve().parents[2]
        result = PRIVATE_PYTHON_RUN([sys.executable, '-B', '-c', code],
            env={'PATH':'/usr/bin:/bin','PYTHONPATH':str(package),'PYTHONDONTWRITEBYTECODE':'1',
                 'HOME':str(self.root),'TMPDIR':'/dev/shm'},
            cwd=self.root, capture_output=True, text=True, timeout=3)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout),
                         {'state':'rejected','reason':'IMMUTABLE_RESIDENT_RELEASE_CHANGED'})


if __name__ == '__main__':
    unittest.main()
