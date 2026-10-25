"""Real disposable inference children: expiry/hold cancels only the original job."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]


class InferenceDeadlineTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location(
            "deadline_driver", ROOT / "scripts/heptatrader_paper_driver.py")
        self.d = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.d)
        self.temp = tempfile.TemporaryDirectory(prefix="hepta-inference-deadline-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def history(self, observed=None):
        return [{"observed_at_ms": self.d.now_ms() if observed is None else observed,
                 "bid": 1.0, "ask": 1.1, "mid": 1.05}]

    def start_child(self, *, observed=None, ignore_term=False):
        marker = self.root / "child-ready"
        source = ("import os,signal,sys,time\nfrom pathlib import Path\n" +
                  ("signal.signal(signal.SIGTERM,signal.SIG_IGN)\n" if ignore_term else "") +
                  "Path(sys.argv[1]).write_text(str(os.getpid()))\ntime.sleep(30)\n")
        def model(*_args):
            self.d.run([sys.executable, "-c", source, str(marker)], 150)
            return {"action": "BUY", "confidence": 1.0, "rationale": "late"}
        self.d.model_decision = model
        worker = self.d.ModelWorker("unused", self.root, self.history(observed), 0.0)
        self.addCleanup(worker.close)
        return worker, marker

    def wait_marker(self, marker):
        deadline = time.monotonic() + 3
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(marker.exists(), "isolated child did not initialize")
        return int(marker.read_text())

    def assert_reaped(self, worker):
        self.assertTrue(worker._done.wait(4), "original inference did not stop")
        self.assertTrue(worker.done())
        self.assertIsNone(worker._process)
        with self.assertRaises(self.d.DriverError):
            worker.result()
        if worker.process_pid is not None:
            with self.assertRaises(ProcessLookupError):
                os.kill(worker.process_pid, 0)

    def test_hold_cancels_and_reaps_term_ignoring_original_child(self):
        worker, marker = self.start_child(ignore_term=True)
        pid = self.wait_marker(marker)
        state = {}
        retained, discarded = self.d.sync_model_inference(
            state, worker, False, "ACTIVE_BROKER_ORDER")
        self.assertIs(retained, worker)
        self.assertTrue(discarded)
        self.assertEqual(worker.process_pid, pid)
        self.assert_reaped(worker)
        self.assertEqual(worker.cancel_reason, "ACTIVE_BROKER_ORDER")
        retained, discarded = self.d.sync_model_inference(
            state, worker, discarded, "ACTIVE_BROKER_ORDER")
        self.assertIsNone(retained)
        self.assertTrue(discarded)

    def test_old_input_gets_only_its_remaining_ttl_even_with_150_second_caller(self):
        # The mock clock intentionally does not advance. The monotonic deadline
        # still expires; provider timeout cannot extend the input's remaining life.
        fixed = self.d.now_ms()
        with patch.object(self.d, "MODEL_SIGNAL_TTL_MS", 1200), \
                patch.object(self.d, "now_ms", return_value=fixed):
            start = time.monotonic()
            worker, marker = self.start_child(observed=fixed - 400)
            self.wait_marker(marker)
            self.assert_reaped(worker)
            self.assertLess(time.monotonic() - start, 3)
            self.assertEqual(worker.cancel_reason, "MARKET_SNAPSHOT_EXPIRED")

    def test_expired_or_future_input_never_spawns_provider(self):
        for observed in (self.d.now_ms() - 60001, self.d.now_ms() + 60000):
            with self.subTest(observed=observed), patch.object(self.d.subprocess, "Popen") as start:
                worker, _ = self.start_child(observed=observed)
                self.assert_reaped(worker)
                start.assert_not_called()

    def test_cleanup_pending_retains_original_worker_until_reap(self):
        release = threading.Event()
        class Process:
            pid = 991234567
            returncode = None
            def poll(self):
                return self.returncode
            def communicate(self, timeout):
                if release.is_set():
                    self.returncode = -9
                    return "", ""
                raise subprocess.TimeoutExpired("fixture-owned-child", timeout)
        child = Process()
        # Cancel after spawning, rather than testing the no-spawn expiry branch.
        def start(*_args, **_kwargs):
            self.d._MODEL_RUN_CONTEXT.worker.cancel("TEST_HOLD")
            return child
        self.d.model_decision = lambda *_: self.d.run(["unused-fixture"], 10)
        with patch.object(self.d.subprocess, "Popen", side_effect=start), \
                patch.object(self.d.os, "killpg") as signal_group:
            worker = self.d.ModelWorker("unused", self.root, self.history(), 0.0)
            self.assertTrue(worker._done.wait(2))
            self.assertFalse(worker.done())
            retained, discarded = self.d.sync_model_inference({}, worker, False, "TEST_HOLD")
            self.assertIs(retained, worker)
            self.assertTrue(discarded)
            self.assertEqual({call.args[0] for call in signal_group.call_args_list}, {child.pid})
            release.set()
            self.assertTrue(worker.done())

    def test_non_inference_subprocess_keeps_original_execution_path(self):
        expected = subprocess.CompletedProcess(["original"], 0, "ok", "")
        with patch.object(self.d.subprocess, "run", return_value=expected) as run, \
                patch.object(self.d.subprocess, "Popen") as start:
            self.assertIs(self.d.run(["original"], 4), expected)
            run.assert_called_once()
            self.assertEqual(run.call_args.kwargs["timeout"], 4)
            start.assert_not_called()

    def test_hold_after_completion_still_invalidates_the_original_answer(self):
        self.d.model_decision = lambda *_: {"action": "BUY", "confidence": 1.0}
        worker = self.d.ModelWorker("unused", self.root, self.history(), 0.0)
        self.addCleanup(worker.close)
        self.assertTrue(worker._done.wait(1))
        worker.cancel("AUTHORITATIVE_POSITION_CHANGED")
        with self.assertRaisesRegex(self.d.DriverError, "AUTHORITATIVE_POSITION_CHANGED"):
            worker.result()


if __name__ == "__main__":
    unittest.main()
