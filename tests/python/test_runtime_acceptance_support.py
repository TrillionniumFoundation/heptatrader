from __future__ import annotations
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
import runtime_acceptance_support as support


class RuntimeAcceptanceSupportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def fixture(self, name):
        root = self.root / name; root.mkdir()
        (root / 'es').mkdir(); (root / 'gs').mkdir()
        (root / 'gs/key').write_bytes(b'K'*32); (root / 'gs/key').chmod(0o400)
        return SimpleNamespace(root=root, processes=[])

    def checkpoint(self):
        runtime = self.fixture('source')
        (runtime.root / 'es/oms-journal.jsonl').write_bytes(b'{"event":"fixture"}\n')
        (runtime.root / 'es/oms-journal.jsonl').chmod(0o600)
        path = self.root / 'checkpoint'
        support.checkpoint_stopped_fixture(runtime, path)
        return path

    def test_bounded_ascii_sample_count(self):
        self.assertEqual(support.bounded_integer('128',128,2000),128)
        for x in ['127','2001','-1','+128','1.28e2','１２８','', '1'*100]:
            with self.subTest(x=x), self.assertRaises(ValueError): support.bounded_integer(x,128,2000)

    def test_nearest_rank_quantiles_and_small_sample_disclosure(self):
        s=support.summarize_latency([1_000_000,3_000_000,2_000_000,4_000_000],12_000_000)
        self.assertEqual(s['latency_ms']['p50'],2)
        self.assertEqual(s['latency_ms']['p99'],4)
        self.assertIsNone(s['latency_ms']['p999'])
        self.assertEqual(support.summarize_latency([1]*1000,1000)['latency_ms']['p999'],1e-6)

    def test_quantiles_reject_invented_or_inconsistent_timings(self):
        for values, elapsed in [([],1),([0],1),([-1],1),([True],1),([1.5],2),([2,3],4)]:
            with self.subTest(values=values), self.assertRaises(ValueError): support.summarize_latency(values,elapsed)

    def test_read_measurement_rejects_stale_without_retry(self):
        callback=mock.Mock(side_effect=[{'authoritative':True},{'authoritative':True,'stale':True}])
        with self.assertRaises(AssertionError): support.measure_reads(callback,128)
        self.assertEqual(callback.call_count,2)

    def test_real_private_copy_and_restore(self):
        path=self.checkpoint(); target=self.fixture('target')
        report=support.restore_new_fixture(path,target)
        self.assertEqual(report['file_count'],2)
        self.assertEqual((target.root/'es/oms-journal.jsonl').read_bytes(),b'{"event":"fixture"}\n')
        self.assertEqual((target.root/'gs/key').read_bytes(),b'K'*32)
        self.assertEqual((target.root/'gs/key').stat().st_mode & 0o777,0o400)

    def test_running_checkpoint_and_restore_are_rejected(self):
        runtime=self.fixture('busy');runtime.processes=[object()]
        with self.assertRaises(ValueError):support.checkpoint_stopped_fixture(runtime,self.root/'not-created')
        self.assertFalse((self.root/'not-created').exists())
        with self.assertRaises(ValueError):support.restore_new_fixture(self.root/'missing',runtime)

    def test_corruption_rejected_before_restored_namespace_is_modified(self):
        path=self.checkpoint();target=self.fixture('target')
        (path/'es/oms-journal.jsonl').chmod(0o600) # fixture owner injects corruption
        (path/'es/oms-journal.jsonl').write_bytes(b'corrupt')
        with self.assertRaises(ValueError):support.restore_new_fixture(path,target)
        self.assertEqual(list((target.root/'es').iterdir()),[])
        self.assertEqual((target.root/'gs/key').read_bytes(),b'K'*32)

    def test_existing_state_is_never_adopted(self):
        path=self.checkpoint();target=self.fixture('target')
        (target.root/'es/existing').write_bytes(b'do not overwrite')
        with self.assertRaises(ValueError):support.restore_new_fixture(path,target)
        self.assertEqual((target.root/'es/existing').read_bytes(),b'do not overwrite')

    def test_leaf_links_and_fifo_rejected(self):
        for kind in ('symlink','hardlink','fifo'):
            with self.subTest(kind=kind):
                p=self.root/kind; original=self.root/(kind+'-target');original.write_bytes(b'x')
                if kind=='symlink':p.symlink_to(original)
                elif kind=='hardlink':os.link(original,p)
                else:os.mkfifo(p)
                with self.assertRaises((OSError,ValueError)):support.regular_bytes(p)

    def test_namespace_substitution_is_rejected(self):
        path=self.checkpoint(); (path/'es').rename(path/'old-es');(path/'es').symlink_to(path/'old-es')
        with self.assertRaises(ValueError):support.load_checkpoint(path)

    def test_duplicate_and_unsafe_manifest_paths_rejected(self):
        path=self.checkpoint();original=json.loads((path/'manifest.json').read_text())
        for name in ('../escape','es/../escape','/es/file','es//file'):
            with self.subTest(name=name):
                value=json.loads(json.dumps(original));value['files'][0]['path']=name
                (path/'manifest.json').write_text(json.dumps(value))
                with self.assertRaises(ValueError):support.load_checkpoint(path)
        original['files'].append(original['files'][0]);(path/'manifest.json').write_text(json.dumps(original))
        with self.assertRaises(ValueError):support.load_checkpoint(path)

    def test_wrong_service_identity_rejected_before_writes(self):
        path=self.checkpoint();target=self.fixture('target')
        manifest=json.loads((path/'manifest.json').read_text());manifest['files'][0]['uid']+=1
        (path/'manifest.json').write_text(json.dumps(manifest))
        with self.assertRaises(ValueError):support.restore_new_fixture(path,target)
        self.assertEqual(list((target.root/'es').iterdir()),[])
        self.assertTrue((target.root/'gs/key').exists())

    def test_oversized_file_rejected_by_descriptor(self):
        path=self.root/'large'
        with path.open('wb') as f:f.truncate(1025)
        with self.assertRaises(ValueError):support.regular_bytes(path,1024)

    def test_duplicate_json_field_is_rejected(self):
        path=self.checkpoint();(path/'manifest.json').write_text('{"schema":"x","schema":"y","files":[]}')
        with self.assertRaises(ValueError):support.load_checkpoint(path)


if __name__=='__main__':unittest.main()
