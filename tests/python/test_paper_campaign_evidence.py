from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import run_ib_paper_campaign as campaign


class PaperCampaignEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="hepta-campaign-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.artifact = self.root / "artifact"
        self.artifact.mkdir(mode=0o700)
        self.binary = self.artifact / "hepta-ib-executiond"
        self.binary.write_text("#!/bin/sh\nexit 0\n")
        self.binary.chmod(0o700)
        self.manifest = {
            "schema": "heptatrader.ib-candidate-artifact.v2", "candidate_sha": "a" * 40,
            "binary": {"name": self.binary.name,
                       "sha256": hashlib.sha256(self.binary.read_bytes()).hexdigest(),
                       "size": self.binary.stat().st_size, "format": "ELF64"},
            "isolation": dict(campaign.ISOLATION),
        }
        (self.artifact / "manifest.json").write_text(json.dumps(self.manifest))
        (self.artifact / "manifest.json").chmod(0o600)
        self.attempt = self.root / "attempt"
        self.harness = self.root / "harness"
        self.env = {"PATH": "/usr/bin:/bin", "LC_ALL": "C", "SHOULD_NOT_REACH_CHILD": "secret",
                    "HEPTA_IB_PAPER_BROKER_HOST": "127.0.0.1", "HEPTA_IB_PAPER_BROKER_PORT": "4002"}
        self.set_harness('printf "{}\\n" > "$HEPTA_QUALIFICATION_RESULT_PATH"\n')

    def set_harness(self, body: str) -> None:
        self.harness.write_text("#!/bin/sh\nset -eu\numask 077\n" + body)
        self.harness.chmod(0o700)
        self.env.update(HEPTA_IB_PAPER_QUALIFIER=str(self.harness),
                        HEPTA_IB_PAPER_QUALIFIER_SHA256=hashlib.sha256(self.harness.read_bytes()).hexdigest(),
                        HEPTA_QUALIFICATION_MUTATIONS="1")

    def command(self, short_timeout: bool = False) -> list[str]:
        if not short_timeout:
            return ["/bin/bash", str(ROOT / "scripts/run_ib_paper_artifact_qualification.sh"),
                    str(self.artifact), "a" * 40, str(self.attempt)]
        code = ("import sys;from pathlib import Path;sys.path.insert(0,sys.argv[1]);"
                "from run_ib_paper_campaign import run_campaign;"
                "raise SystemExit(run_campaign(Path(sys.argv[2]),sys.argv[3],Path(sys.argv[4]),"
                "timeout_seconds=.2,termination_grace=.05))")
        return [sys.executable, "-I", "-c", code, str(ROOT / "scripts"),
                str(self.artifact), "a" * 40, str(self.attempt)]

    def run_wrapper(self, short_timeout: bool = False) -> subprocess.CompletedProcess:
        return subprocess.run(self.command(short_timeout), env=self.env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)

    def record(self) -> dict:
        return json.loads((self.attempt / "attempt.json").read_text())

    def assert_private_cleanup(self) -> None:
        self.assertEqual(list(self.root.glob(".hepta-paper-private-*")), [])

    def test_workflow_endpoint_reaches_real_controller_and_harness(self) -> None:
        import check_qualification_trust_boundary as boundary
        workflow = boundary.load_workflow(ROOT / boundary.WORKFLOW)
        phase = next(s for s in workflow["jobs"]["qualify"]["steps"]
                     if s.get("id") == "run-campaign")
        for key in ("HEPTA_IB_PAPER_BROKER_HOST", "HEPTA_IB_PAPER_BROKER_PORT"):
            self.env[key] = phase["env"][key]
        self.set_harness('test "$HEPTA_QUALIFICATION_EXPECTED_BROKER_HOST" = 127.0.0.1\n'
                         'test "$HEPTA_QUALIFICATION_EXPECTED_BROKER_PORT" = 4002\n'
                         'test -z "${SHOULD_NOT_REACH_CHILD:-}"\n'
                         'printf "%s\\n" "$@" > "$(dirname "$HEPTA_QUALIFICATION_RESULT_PATH")/argv"\n'
                         'printf "{}\\n" > "$HEPTA_QUALIFICATION_RESULT_PATH"\n')
        result = self.run_wrapper()
        self.assertEqual(result.returncode, 0, result.stderr)
        argv = (self.attempt / "evidence/argv").read_text().splitlines()
        self.assertEqual(argv.count("--broker-host"), 1)
        self.assertEqual(argv.count("--broker-port"), 1)
        self.assertEqual(argv[argv.index("--broker-host") + 1], "127.0.0.1")
        self.assertEqual(argv[argv.index("--broker-port") + 1], "4002")
        self.assertFalse(self.record()["paper_authorized"])

    def test_invalid_or_omitted_endpoint_never_reserves_or_spawns(self) -> None:
        for key, values in (("HEPTA_IB_PAPER_BROKER_HOST", (None, "", "localhost", "192.0.2.1", "::1")),
                            ("HEPTA_IB_PAPER_BROKER_PORT", (None, "", "4001", "7496", "7497", "04002", "4002 "))):
            for value in values:
                with self.subTest(key=key, value=value):
                    env = dict(self.env)
                    if value is None:
                        env.pop(key)
                    else:
                        env[key] = value
                    with mock.patch.object(campaign.subprocess, "Popen") as spawn:
                        with self.assertRaises(campaign.QualificationError):
                            campaign.run_campaign(self.artifact, "a" * 40, self.attempt, environ=env)
                        spawn.assert_not_called()
                    self.assertFalse(self.attempt.exists())

    def test_failure_preserves_evidence_without_authorization(self) -> None:
        self.set_harness('printf "event\\n" > "$(dirname "$HEPTA_QUALIFICATION_RESULT_PATH")/events.jsonl"\nexit 42\n')
        result = self.run_wrapper()
        self.assertEqual(result.returncode, 42, result.stderr)
        self.assertEqual((self.attempt / "evidence/events.jsonl").read_text(), "event\n")
        self.assertEqual(self.record()["state"], "HARNESS_FAILED")
        self.assertFalse(self.record()["paper_authorized"])
        self.assertFalse(self.record()["live_authorized"])
        self.assertFalse((self.attempt / "verified-evidence.tar").exists())
        self.assert_private_cleanup()

    def test_failure_attempt_is_not_reused(self) -> None:
        self.set_harness(f'echo called >> "{self.root}/calls"\nexit 42\n')
        self.assertEqual(self.run_wrapper().returncode, 42)
        self.assertEqual(self.run_wrapper().returncode, 73)
        self.assertEqual((self.root / "calls").read_text().splitlines(), ["called"])

    def test_success_is_not_qualification_and_private_home_is_separate(self) -> None:
        self.set_harness('test -z "${SHOULD_NOT_REACH_CHILD:-}"\n'
                         'printf "PRIVATE_SENTINEL\\n" > "$HOME/private.txt"\n'
                         'printf "PRIVATE_STDOUT\\n"\nprintf "PRIVATE_STDERR\\n" >&2\n'
                         'printf "{}\\n" > "$HEPTA_QUALIFICATION_RESULT_PATH"\n')
        result = self.run_wrapper()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.record()["state"], "HARNESS_SUCCEEDED_AWAITING_VERIFICATION")
        self.assertFalse(self.record()["paper_authorized"])
        self.assertNotIn(b"PRIVATE", result.stdout + result.stderr)
        self.assertEqual(sorted(p.name for p in (self.attempt / "evidence").iterdir()),
                         ["qualification-result.json"])
        self.assert_private_cleanup()

    def test_result_missing_is_not_success(self) -> None:
        self.set_harness("exit 0\n")
        self.assertEqual(self.run_wrapper().returncode, 70)
        self.assertEqual(self.record()["state"], "CONTROLLER_FAILED")

    def test_result_symlink_is_not_success(self) -> None:
        self.set_harness('printf "{}\\n" > "$HOME/result"\nln -s "$HOME/result" "$HEPTA_QUALIFICATION_RESULT_PATH"\n')
        self.assertEqual(self.run_wrapper().returncode, 70)
        self.assert_private_cleanup()

    def test_result_fifo_is_rejected_without_blocking(self) -> None:
        self.set_harness('mkfifo "$HEPTA_QUALIFICATION_RESULT_PATH"\n')
        self.assertEqual(self.run_wrapper().returncode, 70)

    def test_nonzero_even_with_result_remains_failure(self) -> None:
        self.set_harness('printf "{}\\n" > "$HEPTA_QUALIFICATION_RESULT_PATH"\nexit 23\n')
        self.assertEqual(self.run_wrapper().returncode, 23)
        self.assertEqual(self.record()["state"], "HARNESS_FAILED")

    def test_timeout_preserves_evidence_and_kills_ignoring_child(self) -> None:
        self.set_harness('trap "" TERM\nprintf "partial\\n" > "$(dirname "$HEPTA_QUALIFICATION_RESULT_PATH")/partial.jsonl"\nsleep 60\n')
        result = self.run_wrapper(short_timeout=True)
        self.assertEqual(result.returncode, 124, result.stderr)
        self.assertEqual(self.record()["state"], "TIMEOUT")
        self.assertTrue((self.attempt / "evidence/partial.jsonl").is_file())
        self.assert_private_cleanup()

    def test_interrupts_preserve_attempt_and_partial_evidence(self) -> None:
        for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            with self.subTest(signum=signum):
                self.attempt = self.root / f"attempt-{signum}"
                self.set_harness('printf "partial\\n" > "$(dirname "$HEPTA_QUALIFICATION_RESULT_PATH")/partial.jsonl"\nsleep 60\n')
                process = subprocess.Popen(self.command(), env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                try:
                    deadline = time.monotonic() + 5
                    while not (self.attempt / "evidence/partial.jsonl").exists() and time.monotonic() < deadline:
                        time.sleep(.01)
                    self.assertTrue((self.attempt / "evidence/partial.jsonl").exists())
                    process.send_signal(signum)
                    out, err = process.communicate(timeout=5)
                    self.assertEqual(process.returncode, 128 + signum, err)
                    self.assertEqual(self.record()["state"], "INTERRUPTED")
                    self.assertEqual(self.record()["signal"], signum)
                    self.assert_private_cleanup()
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.communicate()

    def test_identity_is_durable_before_child_starts(self) -> None:
        self.set_harness('python3 - <<\'CHECK\'\n'
                         'import json,os,pathlib\n'
                         'p=pathlib.Path(os.environ["HEPTA_QUALIFICATION_RESULT_PATH"])\n'
                         'r=json.loads((p.parent.parent/"attempt.json").read_text())\n'
                         'assert r["state"]=="RUNNING" and not r["paper_authorized"]\n'
                         'p.write_text("{}")\nCHECK\n')
        self.assertEqual(self.run_wrapper().returncode, 0)

    def test_digest_mismatch_prevents_any_attempt(self) -> None:
        self.env["HEPTA_IB_PAPER_QUALIFIER_SHA256"] = "b" * 64
        self.assertEqual(self.run_wrapper().returncode, 78)
        self.assertFalse(self.attempt.exists())

    def test_explicit_mutation_opt_in_cannot_be_omitted(self) -> None:
        self.env.pop("HEPTA_QUALIFICATION_MUTATIONS")
        self.assertEqual(self.run_wrapper().returncode, 78)
        self.assertFalse(self.attempt.exists())

    def test_source_manifest_mismatch_prevents_spawn(self) -> None:
        self.manifest["candidate_sha"] = "b" * 40
        (self.artifact / "manifest.json").write_text(json.dumps(self.manifest))
        self.assertEqual(self.run_wrapper().returncode, 78)
        self.assertFalse(self.attempt.exists())

    def test_existing_symlink_attempt_is_not_followed(self) -> None:
        self.attempt.symlink_to(self.root / "missing")
        self.assertEqual(self.run_wrapper().returncode, 73)
        self.assertFalse((self.root / "missing").exists())

    def test_failed_durable_reservation_never_spawns_harness(self) -> None:
        self.set_harness(f'echo called > "{self.root}/calls"\n')
        with mock.patch.object(campaign, "atomic_private_json", side_effect=OSError("disk fault")):
            with self.assertRaises(OSError):
                campaign.run_campaign(self.artifact, "a" * 40, self.attempt, environ=self.env)
        self.assertFalse((self.root / "calls").exists())
        self.assertEqual(self.run_wrapper().returncode, 73)

    def test_attempt_parent_is_synced_before_harness_spawn(self) -> None:
        parent_inode = self.root.stat().st_ino
        synced = []
        fsync = os.fsync
        popen = subprocess.Popen
        def observe_sync(fd):
            synced.append(os.fstat(fd).st_ino)
            return fsync(fd)
        def checked_spawn(*args, **kwargs):
            self.assertIn(parent_inode, synced)
            self.assertIn(self.attempt.stat().st_ino, synced)
            return popen(*args, **kwargs)
        with mock.patch.object(campaign.os, "fsync", side_effect=observe_sync), \
             mock.patch.object(campaign.subprocess, "Popen", side_effect=checked_spawn):
            self.assertEqual(campaign.run_campaign(self.artifact, "a"*40, self.attempt, environ=self.env), 0)

    def test_parent_fsync_failure_preserves_reservation_without_spawn(self) -> None:
        with mock.patch.object(campaign.os, "fsync", side_effect=OSError("parent fsync")), \
             mock.patch.object(campaign.subprocess, "Popen") as spawn:
            with self.assertRaises(OSError):
                campaign.run_campaign(self.artifact, "a"*40, self.attempt, environ=self.env)
            spawn.assert_not_called()
        self.assertTrue(self.attempt.is_dir())
        self.assertEqual(self.run_wrapper().returncode, 73)

    def test_missing_attempt_parent_is_not_created(self) -> None:
        self.attempt = self.root / "not-provisioned" / "attempt"
        self.assertEqual(self.run_wrapper().returncode, 78)
        self.assertFalse(self.attempt.parent.exists())

    def test_timeout_cannot_be_enlarged(self) -> None:
        with self.assertRaises(campaign.QualificationError):
            campaign.run_campaign(self.artifact, "a" * 40, self.attempt,
                                  environ=self.env, timeout_seconds=901)


if __name__ == "__main__":
    unittest.main()
