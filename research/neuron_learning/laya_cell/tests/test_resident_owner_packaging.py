"""Pure package/config checks; no installer, service, native or model execution."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from laya_cell import install_resident, resident, resident_evaluate, resident_owner, resident_owner_consume
from laya_cell import resident_training_v8 as policy
from laya_cell.integrity import ContractError, canonical, sha_file


class OwnerPackagingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def runtime(self):
        root = self.root / 'runtime'; pins = {}
        for rel in (*resident_owner.RUNTIME_PINS, *resident_owner_consume.RUNTIME_PINS):
            path = root / rel; path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('# Synthetic package fixture, never imported\n')
            pins[rel] = sha_file(path)
        config = {'objective': deepcopy(policy.OBJECTIVE), 'runtime_directory': str(root),
                  'runtime_sha256': pins, 'evaluation_directory': str(self.root / 'evaluation')}
        return root, config

    def test_normal_package_collects_new_owner_modules_and_rejects_an_incomplete_source(self):
        source = self.root / 'repo/research/neuron_learning/laya_cell'; source.mkdir(parents=True)
        for name in ('resident_owner.py', 'resident_owner_predict.py', 'resident_owner_consume.py',
                     'resident_launch.py', 'resident_evaluate.py'):
            (source / name).write_text('# Synthetic package fixture\n')
        native = self.root / 'synthetic-native'; native.write_bytes(b'not an executable')
        with patch.object(install_resident.subprocess, 'run', side_effect=AssertionError('no service commands')):
            files = install_resident.runtime_files(source, native)
            for rel in (*resident_owner.RUNTIME_PINS, *resident_owner_consume.RUNTIME_PINS):
                self.assertEqual(files[rel], source / Path(rel).name)
            self.assertEqual(files['bin/hepta-trader-neuron-native'], native)
            (source / 'resident_owner_predict.py').unlink()
            with self.assertRaisesRegex(ContractError, 'RESIDENT_OWNER_MODULE_MISSING'):
                install_resident.runtime_files(source, native)

    def test_v8_release_builder_adds_handoff_pins_missing_from_the_old_inventory(self):
        root, config = self.runtime()
        for rel in policy.RUNTIME_PINS:
            path = root / rel; path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('# Existing forecast source fixture\n')
        config['runtime_sha256'] = {}
        with patch.object(policy.previous, 'configuration_for_release', return_value=deepcopy(config)), \
             patch.object(policy, 'check_config'):
            result = policy.configuration_for_release(config, root, closed_action_source='synthetic-source')
        self.assertEqual(config['runtime_sha256'], {})
        for rel in (*resident_owner.RUNTIME_PINS, *resident_owner_consume.RUNTIME_PINS):
            self.assertEqual(result['runtime_sha256'][rel], sha_file(root / rel))
            if rel != 'learn/laya_cell/resident_launch.py':
                self.assertNotIn(rel, policy.RUNTIME_PINS)

    def test_v8_evaluator_rejects_missing_pin_file_or_changed_bytes_before_making_state(self):
        root, config = self.runtime(); path = self.root / 'config.json'
        rel = resident_owner.RUNTIME_PINS[0]; file = root / rel; original = file.read_bytes()
        with patch.object(resident_owner, 'native', side_effect=AssertionError('no native execution')):
            for mutation in ('missing_pin', 'missing_file', 'changed_bytes'):
                current = deepcopy(config); file.write_bytes(original)
                if mutation == 'missing_pin':
                    current['runtime_sha256'].pop(rel)
                elif mutation == 'missing_file':
                    file.unlink()
                else:
                    file.write_text('# changed bytes\n')
                path.write_bytes(canonical(current))
                with self.subTest(mutation=mutation), self.assertRaisesRegex(ContractError, 'LAYA_OWNER_RUNTIME_'):
                    resident_evaluate.Evaluator(path)
                self.assertFalse(Path(config['evaluation_directory']).exists())
            file.write_bytes(original); path.write_bytes(canonical(config))
            self.assertTrue(resident.check_owner_handoff_runtime(config))
            resident_evaluate.Evaluator(path)

    def test_configured_owner_native_pin_is_verified_at_evaluator_start(self):
        root, config = self.runtime(); path = self.root / 'config.json'
        host = {'schema': resident_owner.ENROLLMENT_SCHEMA, 'route': resident_owner.ROUTE,
                'native_source_sha': resident_owner.PIN, 'effect_authority': False,
                'owner': {'required_current_head': {'synthetic_public_metadata_only': True}}}
        host_path = self.root / 'host.json'; host_path.write_bytes(canonical(host))
        binary = self.root / 'native-fixture'; binary.write_bytes(b'not an executable')
        config.update(canonical_owner_registration={'path': str(host_path), 'sha256': sha_file(host_path)},
                      canonical_verifier_binary=str(binary), canonical_verifier_sha256='0' * 64)
        path.write_bytes(canonical(config))
        with patch.object(resident_owner, 'native', side_effect=AssertionError('no native execution')):
            with self.assertRaisesRegex(ContractError, 'CANONICAL_VERIFIER_CHANGED'):
                resident_evaluate.Evaluator(path)
        self.assertFalse(Path(config['evaluation_directory']).exists())


if __name__ == '__main__':
    unittest.main()
