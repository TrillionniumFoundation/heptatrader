"""Only the consumer's installed dependency closure defines its release."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from historical_training import install_consumer as installer
from historical_training.common import EvidenceError, digest

ROOT = Path(__file__).resolve().parents[2]


class ConsumerRuntimeInventoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        for name in installer.CONSUMER_RUNTIME_FILES:
            target = self.repo / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('# fixed fixture source\n')

    def identity(self):
        return digest(installer.runtime_inventory(self.repo))

    def test_unrelated_research_does_not_change_release_identity(self):
        before = self.identity()
        for filename in ('resident_train.py', 'resident_store.py', 'resident_row_accounting.py'):
            unrelated = self.repo / 'research/neuron_learning/laya_cell' / filename
            unrelated.write_text('# first unrelated experiment\n')
            self.assertEqual(self.identity(), before)
            unrelated.write_text('# changed unrelated experiment\n')
            self.assertEqual(self.identity(), before)
            unrelated.unlink()
            self.assertEqual(self.identity(), before)

    def test_real_dependency_change_changes_release_identity(self):
        before = self.identity()
        dependency = self.repo / 'scripts/hepta_decision_attribution.py'
        dependency.write_text('# corrected attribution behavior\n')
        self.assertNotEqual(self.identity(), before)

    def test_missing_runtime_dependency_is_rejected(self):
        (self.repo / 'research/neuron_learning/laya_cell/resident_rewards.py').unlink()
        with self.assertRaisesRegex(EvidenceError, 'RUNTIME_SOURCE_MISSING:.*resident_rewards'):
            installer.runtime_inventory(self.repo)

    def test_file_and_parent_symlinks_are_rejected(self):
        source = self.repo / 'scripts/hepta_decision_attribution.py'
        target = self.repo / 'copy.py'
        source.rename(target)
        source.symlink_to(target)
        with self.assertRaisesRegex(EvidenceError, 'SOURCE_SYMLINK:'):
            installer.runtime_inventory(self.repo)
        source.unlink()
        target.rename(source)
        scripts = self.repo / 'scripts'
        actual = self.repo / 'actual-scripts'
        scripts.rename(actual)
        scripts.symlink_to(actual, target_is_directory=True)
        with self.assertRaisesRegex(EvidenceError, 'SOURCE_SYMLINK:'):
            installer.runtime_inventory(self.repo)

    def test_only_packaged_sources_import_without_checkout_or_site_packages(self):
        for name in installer.CONSUMER_RUNTIME_FILES:
            shutil.copyfile(ROOT / name, self.repo / name)
        probe = '''import importlib, pathlib, sys
root = pathlib.Path(sys.argv[1])
sys.path[:0] = [str(root / suffix) for suffix in
               ('research', 'research/neuron_learning', 'scripts')]
for module in sys.argv[2:]:
    loaded = importlib.import_module(module)
    assert pathlib.Path(loaded.__file__).resolve().is_relative_to(root.resolve()), module
'''
        modules = []
        for name in installer.CONSUMER_RUNTIME_FILES:
            parts = list(Path(name).with_suffix('').parts)
            if parts[0] == 'scripts':
                parts.pop(0)
            elif parts[:2] == ['research', 'neuron_learning']:
                parts = parts[2:]
            else:
                parts = parts[1:]
            if parts[-1] == '__init__':
                parts.pop()
            modules.append('.'.join(parts))
        result = subprocess.run([sys.executable, '-I', '-S', '-B', '-c', probe,
                                 str(self.repo), *modules], cwd=self.repo,
                                text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
