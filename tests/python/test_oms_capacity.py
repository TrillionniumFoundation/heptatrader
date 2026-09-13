from __future__ import annotations
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/verify_oms_journal_replay.py"
spec = importlib.util.spec_from_file_location("oms_capacity_subject", SCRIPT)
subject = importlib.util.module_from_spec(spec)
spec.loader.exec_module(subject)

class OmsCapacityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "journal"
        self.record = b'{"schema_version":4,"event":"place_send_attempt","ts_ms":1,"req_id":"not-for-output"}'
        self.path.write_bytes(self.record + b"\n")

    def test_inclusive_limits_and_one_over(self):
        budgets = dict(max_bytes=len(self.record)+1, max_records=1, max_record_bytes=len(self.record))
        self.assertEqual(len(subject.load_events(self.path, **budgets)), 1)
        for name in ("max_bytes", "max_record_bytes"):
            bad = dict(budgets); bad[name] -= 1
            with self.subTest(name=name), self.assertRaises(subject.JournalError):
                subject.load_events(self.path, **bad)
        self.path.write_bytes((self.record+b"\n")*2)
        with self.assertRaisesRegex(subject.JournalError, "COUNT_LIMIT"):
            subject.load_events(self.path, max_records=1)

    def test_large_record_and_torn_tail(self):
        for data in (self.record+b"\n"+b"x"*20000+b"\n", self.record+b"\n"+b"{"):
            self.path.write_bytes(data)
            with self.assertRaises(subject.JournalError):
                subject.load_events(self.path, max_record_bytes=16384)

    def test_special_files_and_links_return_without_blocking(self):
        for kind in ("fifo", "link", "directory"):
            self.path.unlink(missing_ok=True)
            if kind == "fifo": os.mkfifo(self.path)
            elif kind == "link": self.path.symlink_to("missing")
            else: self.path.mkdir()
            result = subprocess.run([sys.executable, str(SCRIPT), "--journal", str(self.path), "--capacity-json"], capture_output=True, timeout=3)
            self.assertEqual(result.returncode, 2, result.stderr)

    def test_malformed_json_and_blank_records_rejected(self):
        for data in (b"\n", b'{"event":"a","event":"b","ts_ms":1}\n', b'{"event":"a","ts_ms":1,"qty":NaN}\n'):
            self.path.write_bytes(data)
            with self.assertRaises(subject.JournalError): subject.load_events(self.path)

    def test_growth_during_scan_is_not_a_successful_snapshot(self):
        iterator = subject.read_records(self.path)
        next(iterator)
        with self.path.open("ab") as file: file.write(self.record+b"\n")
        with self.assertRaisesRegex(subject.JournalError, "SNAPSHOT_CHANGED"):
            list(iterator)

    def test_summary_is_read_only_and_has_no_command_ids(self):
        before = self.path.read_bytes()
        result = subprocess.run([sys.executable, str(SCRIPT), "--journal", str(self.path), "--capacity-json", "--max-records", "1"], capture_output=True, timeout=3)
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual(value["records"], 1)
        self.assertTrue(value["warning_at_80_percent"])
        self.assertFalse(value["paper_authorized"])
        self.assertNotIn(b"not-for-output", result.stdout)
        self.assertEqual(self.path.read_bytes(), before)

    def test_bad_budgets_reject(self):
        for value in (0, -1, True, "12", 10**30):
            for name in ("max_bytes", "max_records", "max_record_bytes"):
                with self.subTest(name=name, value=value), self.assertRaises(subject.JournalError):
                    subject.load_events(self.path, **{name:value})

    def test_budget_failure_does_not_rewrite_history(self):
        before = hashlib.sha256(self.path.read_bytes()).digest()
        with self.assertRaises(subject.JournalError): subject.load_events(self.path, max_bytes=1)
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).digest(), before)
        self.assertEqual(subject.load_events(self.path)[0]["req_id"], "not-for-output")

if __name__ == "__main__": unittest.main()
