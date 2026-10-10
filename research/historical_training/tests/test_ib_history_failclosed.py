"""Historical downloader must not issue another broker request after any failed callback."""
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from historical_training.common import EvidenceError, digest, sha, write_once
from historical_training.ib_history import (
    CONTRACT, completion_state, global_history_lock, plans, preflight_existing, sync_dir, verified_receipt,
)


class HistoricalFailClosedTests(unittest.TestCase):
    def test_error_162_stops_even_when_ib_signals_completion(self):
        self.assertEqual(
            completion_state(True, [{"code": 162, "message": "Request Timed Out"}], False),
            ("ERROR", "REQUEST_ERROR_NO_AUTOMATIC_RETRY"),
        )
        self.assertEqual(
            completion_state(True, [{"code": 162, "message": "Request Timed Out"}], True),
            ("ERROR", "REQUEST_ERROR_NO_AUTOMATIC_RETRY"),
        )

    def test_no_end_callback_is_not_success(self):
        self.assertEqual(
            completion_state(True, [], False),
            ("TIMEOUT", "REQUEST_TIMEOUT_NO_AUTOMATIC_RETRY"),
        )
        self.assertEqual(
            completion_state(False, [], True),
            ("TIMEOUT", "REQUEST_TIMEOUT_NO_AUTOMATIC_RETRY"),
        )

    def test_pacing_error_takes_priority(self):
        self.assertEqual(
            completion_state(True, [{"code": 162, "message": "Historical pacing violation"}], False),
            ("ERROR", "PACING_STOP"),
        )
        self.assertEqual(completion_state(True, [], True), ("RECEIVED", None))

    @staticmethod
    def fixture(root, plan, state):
        key = digest({"contract": CONTRACT, **plan})
        chunk = root / "chunks" / key
        response = {
            "bars": [], "errors": [] if state == "RECEIVED" else [
                {"code": 162, "message": "Historical service timeout"}],
            "head": "20260311 00:00:00" if plan["kind"] == "head" else None,
            "schedule": None,
            "end_callback": None,
        }
        write_once(chunk / "request.json", plan)
        write_once(chunk / "response.json", response)
        write_once(chunk / "receipt.json", {
            "request": plan, "state": state,
            "response_sha256": sha(chunk / "response.json"),
            "rows": 0,
        })
        return chunk

    def test_successful_receipt_is_reusable_without_reissue(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = {"chunks": 1}
            end = datetime(2026, 10, 8, tzinfo=timezone.utc)
            plan = plans(config, end)[0]
            chunk = self.fixture(root, plan, "RECEIVED")
            self.assertEqual(verified_receipt(chunk, plan)["state"], "RECEIVED")
            preflight_existing(root, config, end)

    def test_failed_receipt_requires_distinct_root_no_overwrite(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = {"chunks": 1}
            end = datetime(2026, 10, 8, tzinfo=timezone.utc)
            plan = plans(config, end)[0]
            chunk = self.fixture(root, plan, "ERROR")
            original = (chunk / "receipt.json").read_bytes()
            with self.assertRaisesRegex(EvidenceError, "FAILED_CHUNK_IMMUTABLE"):
                preflight_existing(root, config, end)
            self.assertEqual(original, (chunk / "receipt.json").read_bytes())

    def test_late_failed_chunk_rejected_before_resume_network(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = {"chunks": 1}
            end = datetime(2026, 10, 8, tzinfo=timezone.utc)
            requests = plans(config, end)
            self.fixture(root, requests[0], "RECEIVED")
            self.fixture(root, requests[-1], "TIMEOUT")
            with self.assertRaisesRegex(EvidenceError, "FAILED_CHUNK_IMMUTABLE"):
                preflight_existing(root, config, end)

    def test_orphan_request_unknown_dispatch_refused(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = {"chunks": 1}
            end = datetime(2026, 10, 8, tzinfo=timezone.utc)
            plan = plans(config, end)[0]
            key = digest({"contract": CONTRACT, **plan})
            chunk = root / "chunks" / key
            write_once(chunk / "request.json", plan)
            original = (chunk / "request.json").read_bytes()
            with self.assertRaisesRegex(EvidenceError, "UNFINALIZED_CHUNK_UNKNOWN_OUTCOME"):
                preflight_existing(root, config, end)
            self.assertEqual(original, (chunk / "request.json").read_bytes())

    def test_tampered_request_refused_even_with_valid_response(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            end = datetime(2026, 10, 8, tzinfo=timezone.utc)
            plan = plans({"chunks": 1}, end)[0]
            chunk = self.fixture(root, plan, "RECEIVED")
            (chunk / "request.json").chmod(0o600)
            (chunk / "request.json").write_text('{"kind":"wrong"}\n')
            with self.assertRaisesRegex(EvidenceError, "RESUME_CHUNK_CHANGED"):
                verified_receipt(chunk, plan)

    def test_pinned_history_client_globally_fenced_across_roots(self):
        with tempfile.TemporaryDirectory() as td:
            left = Path(td) / "first"
            right = Path(td) / "second"
            with global_history_lock(left):
                with self.assertRaises(BlockingIOError):
                    with global_history_lock(right):
                        self.fail("second history session acquired the same pinned client")
            with global_history_lock(right):
                self.assertTrue((Path(td) / "ib-history-client-8984.lock").exists())

    def test_synced_orphan_is_still_unknown_outcome(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "history"
            plan = plans({"chunks": 1}, datetime(2026, 10, 8, tzinfo=timezone.utc))[0]
            chunk = root / "chunks" / digest({"contract": CONTRACT, **plan})
            write_once(chunk / "request.json", plan)
            for path in (chunk, chunk.parent, root, root.parent):
                sync_dir(path)
            with self.assertRaisesRegex(EvidenceError, "UNFINALIZED_CHUNK_UNKNOWN_OUTCOME"):
                verified_receipt(chunk, plan)

    def test_mismatched_receipt_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            end = datetime(2026, 10, 8, tzinfo=timezone.utc)
            plan = plans({"chunks": 1}, end)[0]
            chunk = self.fixture(root, plan, "RECEIVED")
            (chunk / "response.json").chmod(0o600)
            (chunk / "response.json").write_text('{"changed":true}\n')
            with self.assertRaisesRegex(EvidenceError, "RESUME_CHUNK_CHANGED"):
                verified_receipt(chunk, plan)


if __name__ == "__main__":
    unittest.main()
