"""Pure metadata and the real installer help must not need a model stack."""
import json
from pathlib import Path
import subprocess
import sys
import unittest


HELP_PROBE = r'''
import importlib.abc, json, os, runpy, sys
sys.path.insert(0, sys.argv[1])
sys.dont_write_bytecode = True
blocked = ('torch', 'transformers', 'safetensors', 'tensorflow', 'jax', 'numpy',
           'laya_cell.model', 'laya_cell.resident_model', 'laya_cell.temporal_fusion_model',
           'laya_cell.temporal_fusion_shadow', 'laya_cell.temporal_fusion_train')
class Guard(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == name or fullname.startswith(name + '.') for name in blocked):
            raise AssertionError('Unexpected model import: ' + fullname)
sys.meta_path.insert(0, Guard())
def audit(event, args):
    if event in ('subprocess.Popen', 'os.system', 'socket.connect', 'socket.bind', 'ctypes.dlopen'):
        raise AssertionError('Unexpected operation: ' + event)
    if event == 'open':
        _, mode, flags = args
        if ((isinstance(mode, str) and any(char in mode for char in 'wax+')) or
                isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)):
            raise AssertionError('Unexpected file write')
sys.addaudithook(audit)
sys.argv = ['laya_cell.install_temporal_fusion', '--help']
try:
    runpy.run_module('laya_cell.install_temporal_fusion', run_name='__main__')
except SystemExit as exc:
    if exc.code not in (0, None):
        raise
loaded = [name for name in sys.modules if any(name == item or name.startswith(item + '.') for item in blocked)]
assert not loaded, loaded
print('METADATA_HELP_MODEL_MODULES=' + json.dumps(loaded))
'''


class TemporalFusionMetadataTests(unittest.TestCase):
    def test_original_profile_and_rule_digests(self):
        from laya_cell.integrity import digest
        from laya_cell.temporal_fusion_metadata import PROFILE, RULE
        self.assertEqual(digest(PROFILE), '38b71fc640b4800feb496464fd27ab4093102bc6a54d186ff39420c58b5ce895')
        self.assertEqual(digest(RULE), '4688957a8d440d2f0849438c8371a73ce0d5f782e6d89cfef855d20dfcdc2953')

    def test_real_help_never_imports_or_uses_models(self):
        source = str(Path(__file__).resolve().parents[2])
        result = subprocess.run([sys.executable, '-I', '-B', '-c', HELP_PROBE, source],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--laya-config', result.stdout)
        self.assertIn('--fusion-candidate', result.stdout)
        self.assertIn('METADATA_HELP_MODEL_MODULES=[]', result.stdout)
        self.assertEqual(result.stderr, '')


if __name__ == '__main__':
    unittest.main()
