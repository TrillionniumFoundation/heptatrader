#!/usr/bin/env python3
"""Runner boundary tests, synthetic logs only; no broker or order API."""
import importlib.util
import json
import subprocess
import sys
import os
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
    def test_storage_with_room(self):
        budget = shadow.storage_budget(self.directory, 64 * 1024 * 1024, 0)
        self.assertTrue(budget["allowed"])
    def test_storage_owned_budget_blocks(self):
        (self.directory / "history").write_bytes(b"x" * 1000)
        budget = shadow.storage_budget(self.directory, 100, 0, reserve_bytes=0)
        self.assertIn("OPTIONS_SHADOW_STATE_BUDGET_EXHAUSTED", budget["reasons"])
    def test_storage_free_space_floor_blocks(self):
        budget = shadow.storage_budget(self.directory, 64 * 1024 * 1024, 1 << 63)
        self.assertIn("OPTIONS_SHADOW_FILESYSTEM_RESERVE_REQUIRED", budget["reasons"])
    def test_storage_attempt_reserve_blocks(self):
        budget = shadow.storage_budget(self.directory, 1024, 0, reserve_bytes=2048)
        self.assertFalse(budget["allowed"])
    def test_snowball_default_has_no_ib_fallback(self):
        status = shadow.snowball_source_status()
        self.assertEqual(status["market_data_source"], "SNOWBALL")
        self.assertFalse(status["ib_market_data_required"])
        self.assertFalse(status["stage1_proven"])
        self.assertFalse(status["snowball_transport_connected"])
        self.assertFalse(status["broker_order_submission_enabled"])
    def test_missing_official_feed_never_launches_observer(self):
        if os.geteuid() == 0:
            self.skipTest("runner intentionally rejects root")
        marker = self.directory / "observer-was-run"
        binary = self.directory / "fake-observer"
        binary.write_text("#!/bin/sh\ntouch '" + str(marker) + "'\nexit 1\n")
        binary.chmod(0o700)
        state = self.directory / "state"
        process = subprocess.run([sys.executable, str(ROOT / "scripts/heptatrader_options_shadow.py"),
                                  "--observer", str(binary), "--account", "DU123", "--state-dir", str(state)],
                                 capture_output=True, text=True, timeout=5)
        self.assertEqual(process.returncode, 78, process.stdout + process.stderr)
        self.assertFalse(marker.exists())
        actual = json.loads((state / "status.json").read_text())
        self.assertEqual(actual["state"], "BLOCKED_SNOWBALL_OFFICIAL_FEED_UNCONFIGURED")
        self.assertFalse(actual["stage1_proven"])
        self.assertEqual(actual["records"], 0)
    def test_unsupported_source_is_rejected(self):
        process = subprocess.run([sys.executable, str(ROOT / "scripts/heptatrader_options_shadow.py"),
                                  "--observer", "/nonexistent", "--account", "DU123", "--state-dir", str(self.directory),
                                  "--market-data-source", "AUTO_FALLBACK"], capture_output=True, timeout=5)
        self.assertEqual(process.returncode, 2)
    def test_atomic_status(self):
        path = self.directory / "status.json"
        shadow.atomic_status(path, {"phase": 3, "trade_enabled": False})
        self.assertEqual(json.loads(path.read_text())["trade_enabled"], False)
        self.assertEqual(path.stat().st_mode & 0o077, 0)

if __name__ == "__main__":
    unittest.main(verbosity=2)
