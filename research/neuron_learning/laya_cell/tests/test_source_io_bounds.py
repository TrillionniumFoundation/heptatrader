"""Source type/byte bounds apply before a bad source can consume resources."""
from contextlib import contextmanager
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from laya_cell import source_archive as archive
from laya_cell.integrity import ContractError, sha_file
from laya_cell.shadow_source import read_source
from laya_cell.tests import test_source_archive as archive_fixture


class SourceIOBoundsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.feed = self.root/'input'
        self.feed.mkdir(mode=0o700)
        period = self.root/'period.json'
        period.write_bytes(b'{}\n')
        period.chmod(0o600)
        self.config = {'feed_directory':str(self.feed), 'period_path':str(period),
                       'period_sha256':sha_file(period), 'completed_history_sources':[]}

    def _fifo_rejection(self, mode):
        if mode == 'page':
            fifo, _ = self._page()
        else:
            fifo = self.feed/'segment-00000001'/'quotes.jsonl'
            fifo.parent.mkdir(mode=0o700)
            os.mkfifo(fifo, 0o600)
        # The alarm belongs only to this private Python child. It makes the
        # old blocking implementation fail without hanging the whole suite.
        script = r'''
import json, os, signal, sys, time
from pathlib import Path
from unittest.mock import patch
def alarm(*_): raise TimeoutError('FIFO_OPEN_TIMEOUT')
def forbid(event,args):
 if event in ('subprocess.Popen','os.system','os.posix_spawn','socket.connect','socket.bind'):
  raise AssertionError('EXTERNAL_OPERATION_FORBIDDEN')
 if event=='import' and str(args[0]).split('.')[0] in ('torch','transformers','tensorflow','jax','ibapi'):
  raise AssertionError('MODEL_OR_BROKER_IMPORT_FORBIDDEN')
sys.addaudithook(forbid)
from laya_cell.resident_data import read_quotes
from laya_cell.resident_recent_source import _sealed_end_timestamp
from laya_cell import source_archive as archive
config=json.loads(sys.argv[1]);path=sys.argv[2];mode=sys.argv[3]
private=archive._private
def replace_page_after_stat(candidate,*args,**kwargs):
 result=private(candidate,*args,**kwargs)
 if str(candidate)==path:
  candidate.unlink();os.mkfifo(candidate,0o600)
 return result
signal.signal(signal.SIGALRM,alarm);signal.setitimer(signal.ITIMER_REAL,1)
try:
 if mode=='unsealed':read_quotes(config,int(time.time()*1000))
 elif mode=='page':
  with patch.object(archive,'_private',replace_page_after_stat):
   archive.inventory_index(config,sorted(Path(config['feed_directory']).glob('segment-*/quotes.jsonl')))
 else:_sealed_end_timestamp({'path':path,'bytes':1,'ended':True,'uncommitted_tail_bytes':0,'chain_head':'a'*64})
 result={'state':'unexpected_success'}
except TimeoutError as exc:result={'state':'blocked','reason':str(exc)}
except ValueError as exc:result={'state':'rejected','reason':str(exc)}
finally:signal.setitimer(signal.ITIMER_REAL,0)
print(json.dumps(result))
'''
        package = Path(__file__).resolve().parents[2]
        env = {'PYTHONPATH':str(package), 'PYTHONDONTWRITEBYTECODE':'1',
               'HOME':str(self.root), 'TMPDIR':str(self.root), 'PATH':'/usr/bin:/bin',
               'ROCR_VISIBLE_DEVICES':'', 'HIP_VISIBLE_DEVICES':'', 'CUDA_VISIBLE_DEVICES':''}
        child = subprocess.run([sys.executable, '-B', '-c', script, json.dumps(self.config),
                                str(fifo), mode], env=env, cwd=self.root,
                               capture_output=True, text=True, timeout=3)
        self.assertEqual(child.returncode, 0, child.stderr)
        result = json.loads(child.stdout)
        self.assertEqual(result['state'], 'rejected', result)
        expected = {'unsealed':'SHADOW_SOURCE_FILE_BOUND', 'sealed':'RECENT_SEALED_SOURCE_CHANGED',
                    'page':'SOURCE_ARCHIVE_FILE_INVALID'}[mode]
        self.assertEqual(result['reason'], expected)

    def test_unsealed_public_quote_reader_rejects_fifo_without_waiting_for_writer(self):
        self._fifo_rejection('unsealed')

    def test_sealed_footer_rejects_fifo_without_waiting_for_writer(self):
        self._fifo_rejection('sealed')

    def test_archive_page_fifo_replacement_is_rejected_before_read(self):
        self._fifo_rejection('page')

    def _page(self):
        fixture = SimpleNamespace(feed=self.feed, T=int(time.time()*1000)-12_000_000,
                                  period_sha=self.config['period_sha256'])
        for i in range(32):
            archive_fixture.SourceArchiveTests.make_segment(fixture, i)
        self.assertEqual(archive.seal(self.config, 1)['pages_added'], 1)
        _, pins = archive.inventory_index(self.config, sorted(self.feed.glob('segment-*/quotes.jsonl')))
        return Path(pins[0]['path']), pins[0]

    @contextmanager
    def _measure_page_reads(self, page):
        actual_read_bytes, actual_read = Path.read_bytes, os.read
        counts = []
        def read_bytes(path):
            result = actual_read_bytes(path)
            if path == page:counts.append(len(result))
            return result
        def read(fd, maximum):
            result = actual_read(fd, maximum)
            if os.readlink('/proc/self/fd/'+str(fd)) == str(page):counts.append(len(result))
            return result
        with patch.object(Path, 'read_bytes', read_bytes), patch.object(archive.os, 'read', read):
            yield counts

    def test_oversized_page_is_rejected_before_unbounded_raw_allocation(self):
        page, pin = self._page()
        page.write_bytes(b'X'*(2*1024**2)+b'\n')
        with self._measure_page_reads(page) as counts:
            with self.assertRaisesRegex(ContractError, 'SOURCE_ARCHIVE_PIN_HASH'):
                archive.verify_page_pin(pin)
        self.assertLessEqual(sum(counts), 256*1024+1)

    def test_page_growth_after_path_stat_cannot_bypass_read_bound(self):
        page, _ = self._page()
        private = archive._private
        changed = False
        def growing(path, *args, **kwargs):
            nonlocal changed
            result = private(path, *args, **kwargs)
            if path == page and not changed:
                page.write_bytes(b'X'*(2*1024**2)+b'\n')
                changed = True
            return result
        with self._measure_page_reads(page) as counts, patch.object(archive, '_private', growing):
            with self.assertRaisesRegex(ContractError, 'SOURCE_ARCHIVE_PAGE_SIZE|JSON_SIZE_BOUND'):
                archive.inventory_index(self.config, sorted(self.feed.glob('segment-*/quotes.jsonl')))
        self.assertTrue(changed)
        self.assertLessEqual(sum(counts), 256*1024+1)

    def test_page_growth_after_descriptor_stat_stops_at_limit_plus_one(self):
        page, _ = self._page()
        actual_read = os.read
        changed = False
        counts = []
        def growing_read(fd, maximum):
            nonlocal changed
            target = os.readlink('/proc/self/fd/'+str(fd)) == str(page)
            if target and not changed:
                with page.open('ab') as output:output.write(b'X'*(2*1024**2))
                changed = True
            result = actual_read(fd, maximum)
            if target:counts.append(len(result))
            return result
        with patch.object(archive.os, 'read', growing_read):
            with self.assertRaisesRegex(ContractError, 'JSON_SIZE_BOUND'):
                archive.inventory_index(self.config, sorted(self.feed.glob('segment-*/quotes.jsonl')))
        self.assertTrue(changed)
        self.assertEqual(sum(counts), 256*1024+1)

    def test_grown_pinned_page_keeps_hash_rejection_ahead_of_json_bound(self):
        page, pin = self._page()
        actual_read = os.read
        changed = False
        counts = []
        def growing_read(fd, maximum):
            nonlocal changed
            target = os.readlink('/proc/self/fd/'+str(fd)) == str(page)
            if target and not changed:
                with page.open('ab') as output:output.write(b'X'*(2*1024**2))
                changed = True
            result = actual_read(fd, maximum)
            if target:counts.append(len(result))
            return result
        with patch.object(archive.os, 'read', growing_read):
            with self.assertRaisesRegex(ContractError, 'SOURCE_ARCHIVE_PIN_HASH'):
                archive.verify_page_pin(pin)
        self.assertTrue(changed)
        self.assertEqual(sum(counts), 256*1024+1)

    def test_valid_archive_page_hash_and_json_share_one_open(self):
        page, expected = self._page()
        actual_os_open, actual_io_open = os.open, io.open
        opens = []
        def os_open(path, *args, **kwargs):
            result = actual_os_open(path, *args, **kwargs)
            if str(path) == str(page):opens.append('os')
            return result
        def io_open(path, *args, **kwargs):
            result = actual_io_open(path, *args, **kwargs)
            if str(path) == str(page):opens.append('io')
            return result
        with patch.object(archive.os, 'open', os_open), patch.object(io, 'open', io_open):
            originals, pins = archive.inventory_index(self.config, sorted(self.feed.glob('segment-*/quotes.jsonl')))
        self.assertEqual(len(originals), 32)
        self.assertEqual(pins, [expected])
        self.assertEqual(len(opens), 1, opens)

    def _bad_page(self, substitution):
        page, pin = self._page()
        raw = page.read_bytes().replace(b'"index":0', substitution, 1)
        page.write_bytes(raw)
        return {**pin, 'bytes':len(raw), 'sha256':hashlib.sha256(raw).hexdigest()}

    def test_duplicate_page_field_keeps_original_json_rejection(self):
        pin = self._bad_page(b'"index":0,"index":0')
        with self.assertRaisesRegex(ContractError, 'DUPLICATE_JSON_KEY'):
            archive.verify_page_pin(pin)

    def test_nonfinite_page_field_keeps_original_json_rejection(self):
        pin = self._bad_page(b'"index":NaN')
        with self.assertRaisesRegex(ContractError, 'NONFINITE_JSON'):
            archive.verify_page_pin(pin)

    def test_wrong_page_hash_still_precedes_malformed_json(self):
        page, pin = self._page()
        raw = bytearray(page.read_bytes())
        raw[0] = ord('X')
        page.write_bytes(raw)
        with self.assertRaisesRegex(ContractError, 'SOURCE_ARCHIVE_PIN_HASH'):
            archive.verify_page_pin(pin)

    def test_regular_source_keeps_exact_chain_hash_and_quote_rows(self):
        fixture = SimpleNamespace(feed=self.feed, T=int(time.time()*1000)-12_000_000,
                                  period_sha=self.config['period_sha256'])
        path = archive_fixture.SourceArchiveTests.make_segment(fixture, 0)
        header, rows, prefix = read_source(path)
        self.assertEqual(header['period_sha256'], self.config['period_sha256'])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['bid'], '1.125')
        self.assertEqual(prefix['sha256'], sha_file(path))
        self.assertEqual(prefix['bytes'], path.stat().st_size)
        self.assertTrue(prefix['ended'])
