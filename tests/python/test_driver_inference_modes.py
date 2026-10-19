"""Operator recovery and maintenance must not depend on entry inference."""
from contextlib import ExitStack
import importlib.util
import os
from pathlib import Path
import signal
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]


class DriverInferenceModesTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location(
            "isolated_driver_modes", ROOT / "scripts/heptatrader_paper_driver.py")
        self.d = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.d)
        self.temp = tempfile.TemporaryDirectory(prefix="heptatrader-driver-modes-")
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        for relative in (
            ".local/bin/heptatrader",
            ".local/share/heptatrader/current/bin/hepta-paper-command",
            ".local/share/heptatrader/current/bin/hepta-sessionctl",
            ".config/heptatrader/paper.token",
            ".config/heptatrader/crypto.token",
        ):
            p = self.home / relative
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("test-only")
            p.chmod(0o600)
        self.clock = [1792326000000]
        self.state = self.d.load_state(self.home / "absent-state.json")
        self.state["history"] = [
            {"observed_at_ms": self.clock[0] - 10000 * (6 - i),
             "bid": 1.1, "ask": 1.1001, "mid": 1.10005} for i in range(6)
        ]
        self.snap = {"position": 0.0, "active": [],
                     "owner_health": {"mutation_ready": True}}
        self.hold = self.home / ".local/state/heptatrader/operator-maintenance/maintenance-hold.json"

    def make_hold(self):
        self.hold.parent.mkdir(parents=True, exist_ok=True)
        self.hold.write_text('{"test_only":true}')
        self.hold.chmod(0o600)

    def make_model_runtime(self):
        p = self.home / ".npm-global/bin/openclaw"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("test-only")

    def harness(self, args):
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.object(self.d.Path, "home", return_value=self.home))
        stack.enter_context(patch.object(sys, "argv", ["driver", *args]))
        stack.enter_context(patch.object(self.d.signal, "signal"))
        stack.enter_context(patch.object(self.d, "now_ms", side_effect=lambda: self.clock[0]))
        stack.enter_context(patch.object(self.d, "load_state", return_value=self.state))
        stack.enter_context(patch.object(self.d, "snapshot", return_value=self.snap))
        self.renew = stack.enter_context(patch.object(self.d, "maybe_renew"))
        self.submit = stack.enter_context(patch.object(self.d, "prepare_send"))
        self.worker = stack.enter_context(patch.object(self.d, "ModelWorker"))
        self.quote = stack.enter_context(patch.object(
            self.d, "native", side_effect=lambda *_a, **_k: {
                "source": "IB", "authoritative": True, "stale": False,
                "bid": 1.1, "ask": 1.1001, "observed_at_ms": self.clock[0]}))
        return stack

    def test_recovery_modes_run_without_model_cli(self):
        cases = [
            (["--reconcile-only"], "snapshot"),
            (["--recover-owner"], "recover_owner"),
            (["--rearm-session-generation", "7"], "rearm_session"),
            (["--canary-roundtrip", "--instrument", "BTC.USD"], "canary_roundtrip"),
        ]
        for args, selected in cases:
            with self.subTest(args=args):
                with self.harness(args) as stack:
                    callbacks = {}
                    for name, result in (("recover_owner", True), ("rearm_session", None),
                                         ("canary_roundtrip", 0), ("latch_owner_fence", None)):
                        callbacks[name] = stack.enter_context(
                            patch.object(self.d, name, return_value=result))
                    self.assertEqual(self.d.main(), 0)
                    if selected != "snapshot":
                        callbacks[selected].assert_called_once()
                    self.worker.assert_not_called()
                    self.submit.assert_not_called()

    def test_flat_maintenance_preserves_lease_without_quote_or_model(self):
        self.make_hold()
        before = len(self.state["history"])
        with self.harness(["--once"]):
            self.assertEqual(self.d.main(), 0)
            self.worker.assert_not_called()
            self.submit.assert_not_called()
            self.renew.assert_called()
            self.quote.assert_not_called()
        self.assertEqual(len(self.state["history"]), before)
        self.assertEqual(self.state["entry_inference_pause"]["reason_code"],
                         "PERSISTENT_MAINTENANCE_HOLD")
        self.assertTrue(self.hold.exists())

    def test_flat_hold_is_recorded_even_when_mutation_gate_is_not_ready(self):
        self.make_hold()
        self.snap["owner_health"]["mutation_ready"] = False
        with self.harness(["--once"]):
            self.assertEqual(self.d.main(), 0)
            self.quote.assert_not_called()
            self.worker.assert_not_called()
            self.submit.assert_not_called()
        self.assertEqual(self.state["entry_inference_pause"]["reason_code"],
                         "PERSISTENT_MAINTENANCE_HOLD")

    def test_loop_progress_belongs_to_current_process_even_under_hold(self):
        self.make_hold()
        identity = self.d.resident_process_identity()
        with self.harness(["--once"]):
            self.assertEqual(self.d.main(), 0)
        progress = self.state["resident_loop_progress"]
        self.assertEqual(progress, {
            "schema": self.d.RESIDENT_LOOP_PROGRESS_SCHEMA,
            **identity, "iteration": 1, "at_ms": self.clock[0],
        })
        state_path = self.home / ".local/state/heptatrader/paper-driver/state.json"
        # The same existing atomic state writer persists this observation.
        self.assertEqual(__import__("json").loads(state_path.read_text())["resident_loop_progress"], progress)

    def test_driver_entry_halt_also_pauses_only_flat_inference(self):
        self.state["halt_new_entries"] = True
        with self.harness(["--once"]):
            self.assertEqual(self.d.main(), 0)
            self.worker.assert_not_called()
            self.submit.assert_not_called()
        self.assertEqual(self.state["entry_inference_pause"]["reason_code"],
                         "DRIVER_NEW_ENTRY_HALT")

    def test_maintenance_does_not_pause_open_position_exit_decisions(self):
        self.make_hold()
        self.make_model_runtime()
        self.state["position"] = self.snap["position"] = 100000.0
        with self.harness(["--once"]) as stack:
            close = stack.enter_context(patch.object(self.d, "should_close", return_value=(False, "")))
            self.assertEqual(self.d.main(), 0)
            close.assert_called_once()
            self.worker.assert_called_once()
            self.submit.assert_not_called()
        self.assertNotIn("entry_inference_pause", self.state)

    def test_pending_command_reconciliation_precedes_maintenance_pause(self):
        self.make_hold()
        pending = {"command_id": "original-uncertain-command", "kind": "OPEN"}
        self.state["pending"] = pending
        with self.harness(["--once"]) as stack:
            inspect = stack.enter_context(patch.object(self.d, "inspect_pending"))
            self.assertEqual(self.d.main(), 0)
            inspect.assert_called_once()
            self.assertIs(self.state["pending"], pending)
            self.worker.assert_not_called()
            self.quote.assert_not_called()
            self.submit.assert_not_called()

    def test_missing_model_keeps_quote_and_reconciliation_loop_alive(self):
        with self.harness(["--once"]) as stack:
            stack.enter_context(patch.object(self.d, "entry_inference_hold_reason", return_value=None))
            self.assertEqual(self.d.main(), 0)
            self.renew.assert_called()
            self.quote.assert_called_once()
            self.worker.assert_not_called()
            self.submit.assert_not_called()
        self.assertIn("model runtime missing:", self.state["last_error"])

    def test_inflight_answer_is_discarded_after_hold_without_parallel_worker(self):
        self.make_model_runtime()
        answer = Mock()
        answer.done.side_effect = lambda: sleeps[0] >= 2
        answer.observed_at_ms = self.clock[0]
        answer.position = 0.0
        answer.result.return_value = {"action": "BUY", "confidence": 1.0, "rationale": "old"}
        handlers = {}
        sleeps = [0]
        def sleep(_seconds):
            sleeps[0] += 1
            self.clock[0] += 10000
            if sleeps[0] == 3:
                handlers[signal.SIGTERM](signal.SIGTERM, None)
        with self.harness([]) as stack:
            stack.enter_context(patch.object(
                self.d.signal, "signal", side_effect=lambda sig, fn: handlers.__setitem__(sig, fn)))
            stack.enter_context(patch.object(self.d.time, "sleep", side_effect=sleep))
            stack.enter_context(patch.object(self.d, "entry_inference_hold_reason",
                                            side_effect=lambda *_args: "PERSISTENT_MAINTENANCE_HOLD" if sleeps[0] == 1 else None))
            self.worker.return_value = answer
            self.assertEqual(self.d.main(), 0)
            self.worker.assert_called_once()
            answer.result.assert_not_called()
            self.submit.assert_not_called()
        self.assertEqual(self.state["decisions"], 0)
        self.assertIn("discarded after inference hold", self.state["last_error"])

    def test_unreadable_hold_state_conservatively_pauses_inference(self):
        with patch.object(self.d.Path, "lstat", side_effect=PermissionError("unreadable")):
            self.assertEqual(self.d.entry_inference_hold_reason({}, self.home, os.geteuid()),
                             "ENTRY_HOLD_STATE_UNAVAILABLE")


if __name__ == "__main__":
    unittest.main()
