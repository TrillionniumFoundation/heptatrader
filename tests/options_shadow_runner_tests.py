#!/usr/bin/env python3
"""Runner boundary tests, synthetic logs only; no broker or order API."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("shadow", ROOT / "scripts/heptatrader_options_shadow.py")
shadow = importlib.util.module_from_spec(spec)
spec.loader.exec_module(shadow)

class RunnerBoundaries(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.log = self.directory / "observer.jsonl"
        self.events = [
            {"event": "paper_account_check", "verified": True},
            {"event": "options_readonly_started", "exchange_date": "20261005", "order_send_capability": False},
            {"event": "options_readonly_complete", "records": 2, "realtime_ready_records": 0,
             "broker_order_send_count": 0, "trading_permission": "UNVERIFIED", "premium_budget": "UNSET"},
        ]
    def tearDown(self):
        self.temp.cleanup()
    def read(self, code=0):
        self.log.write_text("\n".join(json.dumps(e) for e in self.events)+"\n")
        return shadow.observer_result(self.log, code)
    def test_normal_completion(self):
        self.assertEqual(self.read()["records"], 2)
    def test_exit_failure_is_not_success(self):
        with self.assertRaisesRegex(RuntimeError, "OBSERVER_FAILED"):
            self.read(1)
    def test_missing_completion(self):
        self.events.pop()
        with self.assertRaisesRegex(RuntimeError, "COMPLETION_UNPROVEN"):
            self.read()
    def test_duplicate_completion(self):
        self.events.append(dict(self.events[-1]))
        with self.assertRaisesRegex(RuntimeError, "COMPLETION_UNPROVEN"):
            self.read()
    def test_mutation_claim(self):
        self.events[-1]["broker_order_send_count"] = 1
        with self.assertRaisesRegex(RuntimeError, "MUTATION_BOUNDARY"):
            self.read()
    def test_unverified_account(self):
        self.events[0]["verified"] = False
        with self.assertRaisesRegex(RuntimeError, "COMPLETION_UNPROVEN"):
            self.read()
    def test_bad_counts(self):
        self.events[-1]["realtime_ready_records"] = 3
        with self.assertRaisesRegex(RuntimeError, "COUNTS_INVALID"):
            self.read()
    def test_reported_error_with_zero_exit(self):
        self.events.append({"event": "options_readonly_failed"})
        with self.assertRaisesRegex(RuntimeError, "REPORTED_FAILURE"):
            self.read()
    def test_no_candidate(self):
        self.events[-1]["records"] = 0
        self.assertEqual(self.read(2)["records"], 0)
        with self.assertRaisesRegex(RuntimeError, "COUNTS_INVALID"):
            self.read(0)
    def test_replay_must_match(self):
        status = self.read()
        replay = {"replay_pass": True, "records": 2, "market_data_ready_records": 0, "broker_authorized": False}
        shadow.validate_replay(replay, 0, status)
        replay["records"] = 1
        with self.assertRaisesRegex(RuntimeError, "COUNT_MISMATCH"):
            shadow.validate_replay(replay, 0, status)
    def test_replay_cannot_grant_authority(self):
        with self.assertRaisesRegex(RuntimeError, "REPLAY_FAILED"):
            shadow.validate_replay({"replay_pass": True, "broker_authorized": True}, 0, self.read())
    def test_single_writer(self):
        with shadow.single_writer(self.directory):
            with self.assertRaisesRegex(RuntimeError, "ALREADY_RUNNING"):
                with shadow.single_writer(self.directory):
                    self.fail("second writer entered")
        with shadow.single_writer(self.directory):
            pass
    def test_lock_symlink_rejected(self):
        (self.directory / "observer.lock").symlink_to(self.directory / "other")
        with self.assertRaises(OSError):
            with shadow.single_writer(self.directory):
                self.fail("symlink lock accepted")
    def test_atomic_status(self):
        path = self.directory / "status.json"
        shadow.atomic_status(path, {"phase": 3, "trade_enabled": False})
        self.assertEqual(json.loads(path.read_text())["trade_enabled"], False)
        self.assertEqual(path.stat().st_mode & 0o077, 0)

if __name__ == "__main__":
    unittest.main(verbosity=2)
