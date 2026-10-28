"""Metadata and original-file checks must reject streams before blocking."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from laya_cell.integrity import ContractError, read_json, sha_file


ROOT = Path(__file__).resolve().parents[4]
LEAF = r'''
import os, sys
from pathlib import Path
from laya_cell.integrity import ContractError, read_json, sha_file
from laya_cell import source_archive, resident_plan_admission
root = Path(sys.argv[2])
path = root / 'pipe'
os.mkfifo(path, 0o600)
kind = sys.argv[1]
if kind == 'json':
    action = lambda: read_json(path)
    expected = 'JSON_FILE_TYPE'
elif kind == 'hash':
    action = lambda: sha_file(path)
    expected = 'HASH_FILE_TYPE'
elif kind == 'prefix':
    action = lambda: source_archive.verify_prefix(
        {'path': str(path), 'bytes': 1, 'sha256': '0' * 64})
    expected = 'SOURCE_PREFIX_FILE_CHANGED'
elif kind == 'publication':
    action = lambda: resident_plan_admission.publish_immutable(path, {'original': True})
    expected = 'IMMUTABLE_FORWARD_RECORD_CONFLICT'
elif kind == 'quote':
    feed = root / 'feed'
    segment = feed / 'segment-00000000'
    segment.mkdir(parents=True)
    path.rename(segment / 'quotes.jsonl')
    path = segment / 'quotes.jsonl'
    action = lambda: resident_plan_admission._original_quote(
        {'feed_directory': str(feed)},
        [{'path': str(path), 'bytes': 1, 'sha256': '0' * 64}], {})
    expected = 'FORWARD_RECEIPT_FILE_SCOPE'
else:
    raise AssertionError(kind)
before = len(list(Path('/proc/self/fd').iterdir())) if Path('/proc/self/fd').exists() else None
for _ in range(12):
    try:
        action()
    except ContractError as error:
        assert str(error) == expected, (kind, str(error))
    else:
        raise AssertionError('STREAM_ACCEPTED:' + kind)
assert path.is_fifo()
assert not list(root.rglob('.registration-*.pending'))
if before is not None:
    assert before == len(list(Path('/proc/self/fd').iterdir())), 'FD_LEAK'
print('REJECTED_WITHOUT_BLOCK_OR_FD_LEAK:' + kind)
'''


class RegularFileReadTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    @unittest.skipUnless(hasattr(os, 'mkfifo'), 'POSIX named pipes required')
    def test_metadata_and_receipt_readers_reject_fifo(self):
        # Each leaf touches only a private temporary path. A regression is
        # bounded by the parent; it cannot hang the test runner on open().
        for kind in ('json', 'hash', 'prefix', 'publication', 'quote'):
            with self.subTest(kind=kind):
                directory = self.root / kind
                directory.mkdir()
                environment = {
                    'PATH': os.defpath, 'HOME': str(directory), 'LANG': 'C.UTF-8',
                    'PYTHONPATH': os.pathsep.join((str(ROOT / 'research/neuron_learning'),
                                                 str(ROOT / 'scripts'))),
                }
                try:
                    result = subprocess.run(
                        [sys.executable, '-B', '-c', LEAF, kind, str(directory)],
                        capture_output=True, text=True, timeout=2, env=environment)
                except subprocess.TimeoutExpired:
                    self.fail('file reader blocked before type rejection: ' + kind)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn('REJECTED_WITHOUT_BLOCK_OR_FD_LEAK:' + kind, result.stdout)

    def test_json_and_hash_keep_regular_file_and_symlink_behavior(self):
        path = self.root / 'value.json'
        raw = b'{"unicode":"\xe6\x95\xb0\xe6\x8d\xae","value":[1,true,null]}'
        path.write_bytes(raw)
        link = self.root / 'alias.json'
        link.symlink_to(path.name)
        for source in (path, link):
            self.assertEqual(read_json(source), json.loads(raw))
            self.assertEqual(sha_file(source), hashlib.sha256(raw).hexdigest())

    def test_json_limits_and_parser_rejections_remain(self):
        path = self.root / 'value.json'
        for raw, maximum, error in (
                (b'{"x":1}', 6, 'JSON_SIZE_BOUND'),
                (b'{"x":1,"x":2}', 100, 'DUPLICATE_JSON_KEY'),
                (b'{"x":NaN}', 100, 'NONFINITE_JSON')):
            with self.subTest(error=error):
                path.write_bytes(raw)
                with self.assertRaisesRegex(ContractError, error):
                    read_json(path, maximum)

    def test_json_growth_after_size_observation_stays_bounded(self):
        path = self.root / 'growing.json'
        path.write_bytes(b'{}')
        original_stat, original_fstat = Path.stat, os.fstat
        grown = False

        def grow_once(metadata):
            nonlocal grown
            if not grown:
                grown = True
                path.write_text(json.dumps({'value': 'x' * 200}))
            return metadata

        def pathname_stat(source, *args, **kwargs):
            metadata = original_stat(source, *args, **kwargs)
            return grow_once(metadata) if source == path else metadata

        def descriptor_stat(fd):
            return grow_once(original_fstat(fd))

        # Exercise the real bytes after a stale size observation, for either
        # pathname- or descriptor-based validation; no parser substitution.
        with patch.object(Path, 'stat', pathname_stat), patch.object(os, 'fstat', descriptor_stat):
            with self.assertRaisesRegex(ContractError, 'JSON_SIZE_BOUND'):
                read_json(path, 32)
        self.assertTrue(grown)


if __name__ == '__main__':
    unittest.main()
