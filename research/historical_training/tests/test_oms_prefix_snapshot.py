"""Exact historical OMS append-only prefix proof tests; no Broker."""
import hashlib
import tempfile
import unittest
from pathlib import Path
from historical_training.oms_prefix_snapshot import seal_prefix
from historical_training.common import EvidenceError

class OMSSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.j=self.root/"oms.jsonl"
        self.original=b'{"event":"a"}\n{"event":"b"}\n'
        self.j.write_bytes(self.original)
        self.expected=hashlib.sha256(self.original).hexdigest()
        self.output=self.root/"archive"/"oms-prefix.jsonl"
    def test_later_journal_appends_are_not_backdated(self):
        self.j.write_bytes(self.original+b'{"event":"future"}\n')
        before=self.j.read_bytes()
        result=seal_prefix(self.j,self.expected,self.output)
        self.assertEqual(result["prefix_records"],2)
        self.assertEqual(result["prefix_bytes"],len(self.original))
        self.assertEqual(self.output.read_bytes(),self.original)
        self.assertEqual(self.j.read_bytes(),before)
        self.assertEqual(self.output.stat().st_mode & 0o777,0o400)
        self.assertEqual(result["orders_sent"],0)
    def test_original_altered_prefix_cannot_be_silently_accepted(self):
        self.j.write_bytes(self.original.replace(b'"event":"a"',b'"event":"x"')+b'{"event":"future"}\n')
        with self.assertRaisesRegex(EvidenceError,"HASH_NOT_FOUND"):
            seal_prefix(self.j,self.expected,self.output)
        self.assertFalse(self.output.exists())
    def test_unknown_source_hash_refused(self):
        with self.assertRaisesRegex(EvidenceError,"HASH_NOT_FOUND"):
            seal_prefix(self.j,"0"*64,self.output)
    def test_prefix_must_end_at_complete_record(self):
        partial=b'{"event":"a"}\n{"partial":'
        self.j.write_bytes(partial)
        sha=hashlib.sha256(partial).hexdigest()
        with self.assertRaisesRegex(EvidenceError,"RECORD_BOUND"):
            seal_prefix(self.j,sha,self.output)
    def test_no_replace_of_existing_snapshot(self):
        seal_prefix(self.j,self.expected,self.output)
        before=self.output.read_bytes()
        with self.assertRaisesRegex(EvidenceError,"DESTINATION"):
            seal_prefix(self.j,self.expected,self.output)
        self.assertEqual(self.output.read_bytes(),before)
    def test_invalid_hash_refused(self):
        with self.assertRaisesRegex(EvidenceError,"SHA_INVALID"):
            seal_prefix(self.j,"invalid",self.output)
    def test_source_symlink_is_not_followed(self):
        link=self.root/"link.jsonl";link.symlink_to(self.j)
        with self.assertRaisesRegex(EvidenceError,"PATH_OR_DESTINATION"):
            seal_prefix(link,self.expected,self.output)

if __name__=="__main__":
    unittest.main()
