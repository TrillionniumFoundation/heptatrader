"""Execute the real wrapper with local, non-trading harness fixtures."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
WRAPPER = ROOT / "scripts/run_ib_paper_artifact_qualification.sh"
SHA = "d" * 40


class CampaignRetentionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="hepta-campaign-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.artifact = self.root / "artifact"
        self.artifact.mkdir()
        binary = self.artifact / "hepta-ib-executiond"
        binary.write_text("#!/bin/sh\nexit 99 # never executed by these fixtures\n")
        binary.chmod(0o700)
        (self.artifact / "manifest.json").write_text(json.dumps({
            "schema": "heptatrader.ib-candidate-artifact.v2", "candidate_sha": SHA,
            "binary": {"name": binary.name, "sha256": hashlib.sha256(binary.read_bytes()).hexdigest()},
            "isolation": {
                "network": "none", "environment": "cleared",
                "rootfs": "read-only-digest-pinned-oci",
                "source_mount": "read-only-git-archive",
                "sdk_mount": "read-only-stable-snapshot",
                "writable_filesystem": "dedicated-size-bounded-mount",
                "resource_control": "oci-cgroup-memory-cpu-pids-plus-tmpfs-limits",
                "candidate_output": "captured-not-replayed",
            },
        }))
        self.harness = self.root / "harness"
        self.evidence = self.root / "evidence"
        self.calls = self.root / "calls"

    def configure(self, body: str) -> dict[str, str]:
        self.harness.write_text(
            "#!/usr/bin/python3\nimport json, os, pathlib, time\n"
            "result = pathlib.Path(os.environ['HEPTA_QUALIFICATION_RESULT_PATH'])\n"
            "root = result.parent\n"
            f"with open({str(self.calls)!r}, 'a') as calls: calls.write('called\\n')\n"
            "(root / 'raw-events.log').write_text('fixture-only; no Broker I/O\\n')\n"
            "pathlib.Path(os.environ['HOME'], 'private-scratch').write_text('not-for-upload')\n"
            + body + "\n"
        )
        self.harness.chmod(0o700)
        return {
            "PATH": "/usr/bin:/bin", "LC_ALL": "C",
            "HEPTA_IB_PAPER_QUALIFIER": str(self.harness),
            "HEPTA_IB_PAPER_QUALIFIER_SHA256": hashlib.sha256(self.harness.read_bytes()).hexdigest(),
            "HEPTA_QUALIFICATION_MUTATIONS": "1",
        }

    def command(self) -> list[str]:
        return ["/bin/bash", str(WRAPPER), str(self.artifact), SHA, str(self.evidence)]

    def run_wrapper(self, env: dict[str, str]) -> subprocess.CompletedProcess:
        return subprocess.run(self.command(), env=env, capture_output=True, text=True, timeout=10)

    def assert_retained(self, result: subprocess.CompletedProcess, code: int) -> dict:
        self.assertEqual(result.returncode, code, result.stderr)
        self.assertTrue((self.evidence / "raw-events.log").is_file())
        status = json.loads((self.evidence / "campaign-status.json").read_text())
        self.assertEqual(status["exit_code"], code)
        self.assertEqual(status["state"], "COMPLETED" if code == 0 else "INCOMPLETE")
        self.assertIs(status["paper_authorized"], False)
        self.assertIs(status["live_authorized"], False)
        self.assertEqual(status["authorization_effect"], "NONE")
        self.assertEqual(self.evidence.stat().st_mode & 0o777, 0o700)
        self.assertEqual((self.evidence / "campaign-status.json").stat().st_mode & 0o777, 0o600)
        self.assertEqual(list(self.root.glob(".hepta-ib-harness-home.*")), [])
        self.assertFalse(any(p.name == "private-scratch" for p in self.evidence.rglob("*")))
        return status

    def test_success_retains_evidence_but_does_not_authorize(self) -> None:
        env = self.configure("result.write_text('{}')")
        self.assert_retained(self.run_wrapper(env), 0)

    def test_failure_after_possible_send_retains_evidence_and_exit_code(self) -> None:
        env = self.configure("result.write_text('{}')\nraise SystemExit(42)")
        self.assert_retained(self.run_wrapper(env), 42)

    def test_missing_final_result_retains_raw_evidence(self) -> None:
        env = self.configure("pass")
        self.assert_retained(self.run_wrapper(env), 70)

    def test_failed_attempt_cannot_be_reexecuted_with_same_evidence_identity(self) -> None:
        env = self.configure("raise SystemExit(42)")
        self.assert_retained(self.run_wrapper(env), 42)
        before = (self.evidence / "campaign-status.json").read_bytes()
        self.assertEqual(self.run_wrapper(env).returncode, 73)
        self.assertEqual(self.calls.read_text(), "called\n")
        self.assertEqual((self.evidence / "campaign-status.json").read_bytes(), before)

    def test_completed_attempt_cannot_be_reexecuted(self) -> None:
        env = self.configure("result.write_text('{}')")
        self.assert_retained(self.run_wrapper(env), 0)
        self.assertEqual(self.run_wrapper(env).returncode, 73)
        self.assertEqual(self.calls.read_text(), "called\n")

    def test_timeout_retains_evidence(self) -> None:
        env = self.configure("time.sleep(30)")
        tools = self.root / "tools"
        tools.mkdir()
        timeout = tools / "timeout"
        # Exercise GNU timeout; shorten only the test launcher, not production policy.
        timeout.write_text("#!/bin/sh\nshift 4\nexec /usr/bin/timeout --foreground --signal=TERM --kill-after=1s 0.3s \"$@\"\n")
        timeout.chmod(0o700)
        env["PATH"] = str(tools) + ":/usr/bin:/bin"
        self.assert_retained(self.run_wrapper(env), 124)

    def interrupt(self, signum: int) -> None:
        env = self.configure("time.sleep(30)")
        with subprocess.Popen(self.command(), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True) as process:
            try:
                deadline = time.monotonic() + 5
                while not (self.evidence / "raw-events.log").exists():
                    if process.poll() is not None or time.monotonic() > deadline:
                        self.fail("harness did not start")
                    time.sleep(0.01)
                # Signal the wrapper alone: it must forward termination to the harness.
                process.send_signal(signum)
                stdout, stderr = process.communicate(timeout=5)
            finally:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.communicate()
        self.assert_retained(subprocess.CompletedProcess(self.command(), process.returncode, stdout, stderr), 128 + signum)

    def test_term_retains_evidence_and_stops_child(self) -> None:
        self.interrupt(signal.SIGTERM)

    def test_hup_retains_evidence_and_stops_child(self) -> None:
        self.interrupt(signal.SIGHUP)

    def test_symlink_destination_is_rejected_before_harness(self) -> None:
        env = self.configure("result.write_text('{}')")
        self.evidence.symlink_to(self.root / "missing-target")
        self.assertEqual(self.run_wrapper(env).returncode, 73)
        self.assertFalse(self.calls.exists())

    def test_symlink_result_does_not_count_as_completion(self) -> None:
        env = self.configure("result.symlink_to(root / 'raw-events.log')")
        self.assert_retained(self.run_wrapper(env), 70)

    def test_harness_cannot_precreate_wrapper_completion_receipt(self) -> None:
        env = self.configure("result.write_text('{}')\n(root / 'campaign-status.json').write_text('untrusted')")
        run = self.run_wrapper(env)
        self.assertNotEqual(run.returncode, 0)
        self.assertEqual((self.evidence / "campaign-status.json").read_text(), "untrusted")
        self.assertTrue((self.evidence / "raw-events.log").is_file())


if __name__ == "__main__":
    unittest.main()
