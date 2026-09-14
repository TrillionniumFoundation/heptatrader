"""Execute real CMake entry points for retired and supported build requests."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
RETIRED = ('HEPTA_BUILD_LEGACY_MONOLITH', 'HEPTA_BUILD_LEGACY_SIMULATOR', 'HEPTA_ENABLE_LEGACY_0DTE_BRIDGE')


class LegacyRetirementTests(unittest.TestCase):
    def reject(self, source, option):
        with tempfile.TemporaryDirectory(prefix='hepta-retired-') as directory:
            result = subprocess.run(['cmake', '-S', str(source), '-B', directory, '-D' + option + '=ON'],
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=30)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('HEPTA_LEGACY_RUNTIME_RETIRED', result.stdout)
            self.assertFalse((Path(directory) / 'build.ninja').exists())
            self.assertFalse((Path(directory) / 'Makefile').exists())

    def test_root_rejects_retired_monolith(self):
        self.reject(ROOT, RETIRED[0])

    def test_root_rejects_retired_simulator(self):
        self.reject(ROOT, RETIRED[1])

    def test_root_rejects_retired_bridge(self):
        self.reject(ROOT, RETIRED[2])

    def test_subproject_cannot_reenable_monolith(self):
        self.reject(ROOT / 'HeptaTrade', RETIRED[0])

    def test_subproject_cannot_reenable_simulator(self):
        self.reject(ROOT / 'HeptaTrade', RETIRED[1])

    def test_subproject_cannot_reenable_bridge(self):
        self.reject(ROOT / 'HeptaTrade', RETIRED[2])

    def test_old_include_entry_is_explicitly_retired(self):
        result = subprocess.run(['cmake', '-P', str(ROOT / 'cmake/HeptaLegacy.cmake')],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('HEPTA_LEGACY_RUNTIME_RETIRED', result.stdout)

    def test_canonical_configuration_with_explicit_off_retains_real_targets(self):
        with tempfile.TemporaryDirectory(prefix='hepta-supported-') as directory:
            build = Path(directory)
            query = build / '.cmake/api/v1/query'; query.mkdir(parents=True)
            (query / 'codemodel-v2').touch()
            result = subprocess.run(['cmake', '-S', str(ROOT), '-B', directory,
                                     '-DBUILD_TESTING=ON', '-DHEPTA_ENABLE_IBAPI=OFF',
                                     *('-D' + option + '=OFF' for option in RETIRED)],
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout)
            replies = list((build / '.cmake/api/v1/reply').glob('codemodel-v2-*.json'))
            self.assertEqual(len(replies), 1)
            model = json.loads(replies[0].read_text())
            targets = {t['name'] for c in model['configurations'] for t in c['targets']}
            self.assertTrue({'hepta_executiond', 'hepta_tool_gatewayd', 'heptactl'}.issubset(targets))
            self.assertFalse({'HeptaTrader', 'HeptaStrategy', 'HeptaSimulator'} & targets)
            # Inspect the generated build model, not source spelling: removed
            # overlays must not survive as inherited include requirements.
            retired_roots = [(ROOT / name).resolve() for name in ('Interface', 'Tools')]
            inspected = 0
            for configuration in model['configurations']:
                for target in configuration['targets']:
                    detail = json.loads((replies[0].parent / target['jsonFile']).read_text())
                    for group in detail.get('compileGroups', []):
                        for include in group.get('includes', []):
                            path = Path(include['path']).resolve()
                            self.assertFalse(any(path == old or old in path.parents
                                                 for old in retired_roots),
                                             f"retired include in {target['name']}: {path}")
                            inspected += 1
            self.assertGreater(inspected, 0, 'positive include-inventory control')


if __name__ == '__main__':
    unittest.main()
