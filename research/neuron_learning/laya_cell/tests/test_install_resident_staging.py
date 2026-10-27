"""Immutable staging must not install units or alter a running research spool."""
from contextlib import ExitStack, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from laya_cell import install_resident as installer
from laya_cell.integrity import ContractError, sha_file


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


if __name__ == '__main__':
    unittest.main()
