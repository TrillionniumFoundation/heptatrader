from __future__ import annotations
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'scripts'))
from hepta_runtime_diagnostics import inspect_journal
from hepta_evidence_io import EvidenceError


class RuntimeDiagnosticsTests(unittest.TestCase):
    def test_read_only_bounded_scan_and_no_secret_export(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'journal';body=b'{"schema_version":4,"event_type":"order_intent","token":"SECRET"}\n'*100
            path.write_bytes(body);before=path.stat()
            result=inspect_journal(path,max_bytes=100,byte_budget=1000)
            self.assertFalse(result['complete_scan']);self.assertLessEqual(result['scanned_bytes'],100)
            self.assertIn('JOURNAL_BYTE_BUDGET_REACHED',result['warnings']);self.assertNotIn('SECRET',json.dumps(result))
            self.assertEqual(body,path.read_bytes());self.assertEqual(before.st_mtime_ns,path.stat().st_mtime_ns)
            self.assertEqual(result['reconciliation_claim'],'NONE')
    def test_corruption_and_partial_tail_are_not_healthy(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'journal';path.write_bytes(b'not-json\n{"unfinished":')
            result=inspect_journal(path)
            self.assertEqual(result['invalid_records'],1);self.assertFalse(result['complete_scan'])
            self.assertFalse(result['paper_authorized'])
    def test_safe_complete_schema_inventory(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'journal';path.write_bytes(b'{"schema_version":3}\n{"schema_version":4}\n')
            result=inspect_journal(path)
            self.assertTrue(result['complete_scan']);self.assertEqual(result['schema_counts'],{'3':1,'4':1})
    def test_special_files_and_links_reject_without_blocking(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);os.mkfifo(root/'fifo')
            with self.assertRaises((OSError,EvidenceError)):inspect_journal(root/'fifo')
            (root/'real').write_text('{}\n');(root/'link').symlink_to('real')
            with self.assertRaises((OSError,EvidenceError)):inspect_journal(root/'link')

if __name__=='__main__':unittest.main()
