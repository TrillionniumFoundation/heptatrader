"""CPU-only real installer entry points with temporary HOME and fake commands.

Temporal metadata uses the real pure module; installer model imports are
forbidden. No model/native executable or real service is started.
"""
import builtins
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch
from laya_cell import immutable_files
from laya_cell.integrity import ContractError, canonical, digest, sha_file
from laya_cell.temporal_fusion_metadata import PROFILE, RULE

CELL = Path(__file__).resolve().parents[1]
INSTALLERS = ('install_financial_forward', 'install_financial_value', 'install_temporal_fusion')



class SpecialInstallerArtifactTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def fixture(self, name, *, source_change=False, writable_existing=False, native_alias=False):
        base = self.root / name
        src = base / 'repo/research/neuron_learning/laya_cell'
        src.mkdir(mode=0o700, parents=True)
        source = (CELL / (name + '.py')).read_bytes()
        module_path = src / (name + '.py')
        module_path.write_bytes(source)
        for filename in ('integrity.py', 'immutable_files.py', 'temporal_fusion_metadata.py'):
            (src / filename).write_bytes((CELL / filename).read_bytes())
        payload = src / 'zz_source_fixture.py'
        payload.write_bytes(b'ORIGINAL_SOURCE_FIXTURE\n')
        for filename in ('data.py', 'sandbox.py'):
            (src.parent / filename).write_bytes(b'# ordinary fixture source\n')
        scripts = base / 'repo/scripts'
        scripts.mkdir(mode=0o700)
        for filename in ('hepta_pnl_accounting.py', 'hepta_decision_attribution.py'):
            (scripts / filename).write_bytes(b'# ordinary fixture script\n')
        home = base / 'home'
        home.mkdir(mode=0o700)
        period = base / 'period.json'
        period.write_bytes(b'{"execution_domain":"PAPER"}\n')
        native = base / 'native-never-executed'
        native.write_bytes(b'ORDINARY_BINARY_FIXTURE_NOT_EXECUTED\n')
        if native_alias:
            alias = base / 'native-alias'
            alias.symlink_to(native)
            native = alias
        behavior = base / 'behavior.json'
        behavior.write_bytes(b'{"fixture":true}\n')
        config = {'period_path': str(period), 'period_sha256': sha_file(period), 'automatic_trading': False,
                  'native_binary': str(native), 'python': '/fixture/python-not-executed',
                  'decision_attribution_registrations': {'EUR.USD': [{'manifest': str(behavior), 'sha256': sha_file(behavior)}]}}
        fusion = base / 'fusion'
        fusion.mkdir(mode=0o700)
        tensor = fusion / 'fusion.safetensors'
        tensor.write_bytes(b'ORDINARY_TENSOR_BYTES_NOT_LOADED\n')
        (fusion / 'candidate.json').write_bytes(canonical({'profile': PROFILE, 'fusion_sha256': sha_file(tensor),
            'period_sha256': config['period_sha256'], 'candidate_id': 'fixture-fusion', 'laya_candidate_id': 'fixture-laya',
            'train_row_ids': [], 'internal_holdout_row_ids': []}) + b'\n')
        candidates = base / 'candidates'
        artifact = candidates / 'one/artifact'
        artifact.mkdir(mode=0o700, parents=True)
        (artifact / 'candidate.json').write_bytes(b'{"candidate_id":"fixture-laya"}\n')
        config['candidate_directory'] = str(candidates)
        config_path = base / 'config.json'
        config_path.write_bytes(canonical(config) + b'\n')
        module = ModuleType('laya_cell.private_' + name)
        module.__package__ = 'laya_cell'
        module.__file__ = str(module_path)
        original_import = builtins.__import__
        def light_import(module_name, *args, **kwargs):
            if module_name.split('.')[0] in ('torch', 'transformers', 'safetensors') or module_name.rsplit('.', 1)[-1] in ('temporal_fusion_model', 'temporal_fusion_shadow'):
                raise AssertionError('Installer must not import models: ' + module_name)
            return original_import(module_name, *args, **kwargs)
        with patch('builtins.__import__', side_effect=light_import):
            exec(compile(source, module.__file__, 'exec'), module.__dict__)
        files = {'learn/laya_cell/' + path.name: path for path in sorted(src.glob('*.py'))}
        files.update({'learn/' + filename: src.parent / filename for filename in ('data.py', 'sandbox.py')})
        if name != 'install_temporal_fusion':
            files.update({'scripts/' + filename: scripts / filename for filename in ('hepta_pnl_accounting.py', 'hepta_decision_attribution.py')})
            files['bin/native' if name == 'install_financial_value' else 'bin/hepta-financial-owner-verify'] = native
        inventory = {filename: sha_file(path) for filename, path in files.items()}
        group = {'install_financial_forward': 'financial-forward', 'install_financial_value': 'financial-value',
                 'install_temporal_fusion': 'temporal-fusion'}[name]
        release = home / '.local/share/heptatrader' / group / 'releases' / digest(inventory)
        destination = release / 'learn/laya_cell/zz_source_fixture.py'
        if writable_existing:
            destination.parent.mkdir(mode=0o700, parents=True)
            destination.write_bytes(payload.read_bytes())
            destination.chmod(0o600)
        calls = []
        def check_output(argv, **kwargs):
            calls.append(tuple(str(value) for value in argv[:3]))
            if argv[0] == 'systemctl':
                return ('/fixture/driver --manifest ' + str(behavior) + '\n') if name == 'install_financial_forward' else 'MainPID=0\nActiveState=inactive\nNRestarts=0\n'
            if argv[0] == '/usr/bin/python3':
                return json.dumps({role: str(index + 1) * 64 for index, role in enumerate(('registrar', 'predictor', 'evaluator', 'selector'))})
            if argv[0] == 'git':
                return 'fixed-fixture-source\n'
            raise AssertionError('Unexpected command')
        def run(argv, **kwargs):
            calls.append(tuple(str(value) for value in argv))
            return SimpleNamespace(returncode=0)
        module.subprocess = SimpleNamespace(check_output=check_output, run=run)
        original_sha = module.sha_file
        changed = []
        def hash_then_change(path):
            value = original_sha(path)
            if source_change and Path(path) == payload and not changed:
                payload.write_bytes(b'CHANGED_AFTER_INVENTORY\n')
                changed.append(True)
            return value
        module.sha_file = hash_then_change
        def install(update_owned=False):
            with (patch.object(Path, 'home', return_value=home),
                  patch('subprocess.Popen', side_effect=AssertionError('No real subprocess')),
                  redirect_stdout(io.StringIO())):
                if name == 'install_financial_forward':
                    return module.install(config_path, native, start=False, update_owned=update_owned)
                if name == 'install_financial_value':
                    return module.install(config_path, start=False)
                return module.install(config_path, fusion, start=False, update_owned=update_owned)
        return SimpleNamespace(module=module, install=install, inventory=inventory, release=release, home=home,
                               destination=destination, calls=calls, changed=changed, native=native)

    def reason(self, name):
        return 'IMMUTABLE_FINANCIAL_RELEASE_CONFLICT' if name == 'install_financial_value' else 'IMMUTABLE_INSTALL_CONFLICT:'

    def test_real_entries_publish_idempotent_inventory_and_original_modes(self):
        for name in INSTALLERS:
            with self.subTest(installer=name):
                fixture = self.fixture(name)
                first = fixture.install()
                second = fixture.install()
                self.assertEqual(first['runtime_sha256'], fixture.inventory)
                self.assertEqual(second['runtime_sha256'], fixture.inventory)
                self.assertIn('learn/laya_cell/immutable_files.py', first['runtime_sha256'])
                self.assertIn('learn/laya_cell/temporal_fusion_metadata.py', first['runtime_sha256'])
                if name == 'install_temporal_fusion':
                    self.assertEqual(first['plan']['rule'], RULE)
                    self.assertIs(fixture.module.PROFILE, PROFILE)
                    self.assertIs(fixture.module.RULE, RULE)
                for relative, expected in first['runtime_sha256'].items():
                    path = fixture.release / relative
                    self.assertEqual(sha_file(path), expected)
                    self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o500 if relative.startswith('bin/') else 0o400)
                    self.assertEqual(path.stat().st_nlink, 1)

    def test_real_entries_reject_source_changed_after_inventory(self):
        for name in INSTALLERS:
            with self.subTest(installer=name):
                fixture = self.fixture(name, source_change=True)
                with self.assertRaisesRegex(ContractError, re.escape(self.reason(name))):
                    fixture.install()
                self.assertTrue(fixture.changed)
                self.assertFalse(fixture.destination.exists())
                self.assertFalse(any('daemon-reload' in call for call in fixture.calls))

    def test_real_entries_preserve_and_reject_existing_writable_canonical_file(self):
        for name in INSTALLERS:
            with self.subTest(installer=name):
                fixture = self.fixture(name, writable_existing=True)
                before = fixture.destination.stat()
                raw = fixture.destination.read_bytes()
                with self.assertRaisesRegex(ContractError, re.escape(self.reason(name))):
                    fixture.install()
                after = fixture.destination.stat()
                self.assertEqual((before.st_dev, before.st_ino, before.st_mode), (after.st_dev, after.st_ino, after.st_mode))
                self.assertEqual(fixture.destination.read_bytes(), raw)
                self.assertFalse(any('daemon-reload' in call for call in fixture.calls))

    def test_bytes_wrappers_keep_default_mode_and_original_conflict_reason(self):
        for name in INSTALLERS:
            with self.subTest(installer=name):
                fixture = self.fixture(name)
                writer = fixture.module.put if name == 'install_financial_value' else fixture.module.write_once
                path = self.root / name / 'bytes-config.json'
                writer(path, b'{"fixture":true}\n')
                writer(path, b'{"fixture":true}\n')
                with self.assertRaisesRegex(ContractError, re.escape(self.reason(name))):
                    writer(path, b'{"fixture":false}\n')
                self.assertEqual(path.read_bytes(), b'{"fixture":true}\n')
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o400)

    def test_native_source_alias_policy_is_preserved(self):
        for name in INSTALLERS[:2]:
            with self.subTest(installer=name):
                fixture = self.fixture(name, native_alias=True)
                receipt = fixture.install()
                relative = 'bin/native' if name == 'install_financial_value' else 'bin/hepta-financial-owner-verify'
                self.assertTrue(fixture.native.is_symlink())
                self.assertEqual(sha_file(fixture.release / relative), receipt['runtime_sha256'][relative])
                self.assertFalse((fixture.release / relative).is_symlink())


if __name__ == '__main__':
    unittest.main()
