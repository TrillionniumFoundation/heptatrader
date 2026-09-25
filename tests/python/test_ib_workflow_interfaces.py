from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import check_qualification_trust_boundary as boundary


class IbWorkflowInterfaceTests(unittest.TestCase):
    """Execute extracted fail-fast shell phases against inert local programs.

    No Actions expression, candidate executable, broker harness, credential or
    network is executed. The fake programs only record argv and return a code.
    """
    def setUp(self) -> None:
        self.workflow = boundary.load_workflow(ROOT / boundary.WORKFLOW)
        self.temporary = tempfile.TemporaryDirectory(prefix="hepta-workflow-shell-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.record = self.root / "calls"
        self.bin = self.root / "bin"
        self.bin.mkdir()
        stub = self.bin / "python3"
        stub.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$RECORD"\nexit "$STUB_STATUS"\n')
        stub.chmod(0o700)
        wrapper = self.root / "trusted/scripts/run_ib_paper_artifact_qualification.sh"
        wrapper.parent.mkdir(parents=True)
        wrapper.write_text(stub.read_text())
        wrapper.chmod(0o700)
        self.env = {"PATH": str(self.bin) + ":/usr/bin:/bin", "RECORD": str(self.record),
                    "GITHUB_SHA": "a" * 40, "ARTIFACT_DIR": "/private/artifact",
                    "EVIDENCE_DIR": "/private/attempt-1", "CANDIDATE_ARCHIVE": "/private/candidate.tar",
                    "HEPTA_IB_PAPER_QUALIFIER": "/private/pinned-harness", "HEPTA_IB_BUILDER_IMAGE": "image@sha256:" + "b" * 64}

    def run_step(self, step: dict, status: int) -> subprocess.CompletedProcess:
        return subprocess.run(["/bin/bash", "-e", "-o", "pipefail", "-c", step["run"]],
                              cwd=self.root, env={**self.env, "STUB_STATUS": str(status)},
                              capture_output=True, timeout=5)

    def test_real_owner_phase_rejects_wrong_desktop_or_role(self) -> None:
        for job_name, runner in (("build-candidate", "desktop-ib-builder"),
                                 ("qualify", "desktop-ib-paper")):
            phase = next(s for s in self.workflow["jobs"][job_name]["steps"]
                         if s.get("id") == "bind-owner")
            env = {**self.env, "DISPATCH_ACTOR": "ProfHepta",
                   "DISPATCH_ACTOR_ID": "102159240", "TRIGGERING_ACTOR": "ProfHepta",
                   "RUNNER_NAME": runner, "RUNNER_OS": "Linux", "RUNNER_ARCH": "X64"}
            for name in (runner, "x230-ib-paper", "desktop", "wrong-role"):
                with self.subTest(job=job_name, runner=name):
                    result = subprocess.run(["/bin/bash", "-e", "-o", "pipefail", "-c", phase["run"]],
                                            cwd=self.root, env={**env, "RUNNER_NAME": name},
                                            capture_output=True, timeout=5)
                    self.assertEqual(result.returncode == 0, name == runner, result.stderr)

    def test_all_verification_phases_execute_and_propagate_failure(self) -> None:
        for job in self.workflow["jobs"].values():
            for step in job["steps"]:
                if not step.get("id", "").startswith("verify-"):
                    continue
                with self.subTest(phase=step["id"]):
                    self.record.unlink(missing_ok=True)
                    result = self.run_step(step, 29)
                    self.assertEqual(result.returncode, 29, result.stderr)
                    calls = self.record.read_text().splitlines()
                    self.assertEqual(len(calls), 1, "fail-fast shell must stop after the first rejected verification")
                    self.assertTrue(calls[0].startswith("trusted/scripts/verify_"))

    def test_success_executes_both_prebuild_checks(self) -> None:
        step = next(s for s in self.workflow["jobs"]["build-candidate"]["steps"] if s.get("id") == "verify-source-before-build")
        result = self.run_step(step, 0)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.record.read_text().splitlines(), [
            "trusted/scripts/verify_exact_git_index.py --root trusted",
            "trusted/scripts/verify_exact_git_index.py --root candidate"])

    def test_campaign_failure_is_not_hidden_and_paths_bind_one_attempt(self) -> None:
        step = next(s for s in self.workflow["jobs"]["qualify"]["steps"] if s.get("id") == "run-campaign")
        result = self.run_step(step, 42)
        self.assertEqual(result.returncode, 42, result.stderr)
        self.assertEqual(self.record.read_text().strip(), "/private/artifact " + "a" * 40 + " /private/attempt-1")

    def test_verifier_binds_raw_evidence_attempt_and_publication(self) -> None:
        step = next(s for s in self.workflow["jobs"]["qualify"]["steps"] if s.get("id") == "verify-qualification")
        result = self.run_step(step, 0)
        self.assertEqual(result.returncode, 0, result.stderr)
        words = self.record.read_text().split()
        flags = dict(zip(words[1::2], words[2::2]))
        self.assertEqual(flags["--attempt"], "/private/attempt-1/attempt.json")
        self.assertEqual(flags["--evidence-root"], "/private/attempt-1/evidence")
        self.assertEqual(flags["--publication-archive"], "/private/attempt-1/verified-evidence.tar")
        self.assertEqual(flags["--expected-git-sha"], "a" * 40)


if __name__ == "__main__":
    unittest.main()
