"""Exercise tag rejection and package/acceptance binding, not source tokens."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import sys
import unittest

from workflow_test_support import shell, step, workflow

SHA = "a" * 40


class ReleaseWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.workflow = workflow("release.yml")
        self.job = self.workflow["jobs"]["release"]

    def test_structural_custody_and_shared_artifact_acceptance(self):
        self.assertEqual(self.workflow["on"], {"push": {"tags": ["v*.*.*"]}})
        self.assertEqual(self.workflow["permissions"], {"contents": "read"})
        self.assertEqual(self.job["runs-on"], "ubuntu-24.04")
        checkouts = [s for s in self.job["steps"] if s.get("uses", "").startswith("actions/checkout@")]
        self.assertEqual(len(checkouts), 1)
        self.assertEqual(checkouts[0]["with"]["persist-credentials"], "false")
        self.assertEqual(checkouts[0]["with"]["ref"], "${{ github.sha }}")
        core = workflow("core-ci.yml")["jobs"]["core"]
        acceptance = step(self.job, "accept-core-artifact")
        self.assertEqual(acceptance["run"], step(core, "accept-core-artifact")["run"])
        self.assertEqual(acceptance["env"]["EXPECTED_SHA"], "${{ github.sha }}")
        positions = {s.get("id"): i for i, s in enumerate(self.job["steps"]) if "id" in s}
        self.assertLess(positions["check-release-tag"], positions["accept-core-artifact"])
        self.assertLess(positions["accept-core-artifact"], positions["bind-release-manifest"])
        self.assertNotIn("continue-on-error", acceptance)

    def assert_acceptance_invocation(self, body):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / "python3"
            trace = root / "invoked.json"
            executable.write_text(f"#!{sys.executable}\nimport json, os, sys\n"
                                  "with open(os.environ['TRACE'], 'w') as f: json.dump(sys.argv[1:], f)\n"
                                  "raise SystemExit(int(os.environ['FIXTURE_EXIT']))\n")
            executable.chmod(0o755)
            for code in (0, 23):
                if trace.exists(): trace.unlink()
                result = shell(body, root, {"PATH": str(root), "EXPECTED_SHA": SHA,
                                            "TRACE": str(trace), "FIXTURE_EXIT": str(code)})
                self.assertTrue(trace.exists(), "workflow did not execute the acceptance driver")
                self.assertEqual(json.loads(trace.read_text()), ["scripts/accept_core_release.py",
                    "--build-dir", "build/core", "--output-dir", "dist", "--source-sha", SHA])
                self.assertEqual(result.returncode == 0, code == 0, "acceptance failure was swallowed")

    def test_each_real_workflow_invokes_driver_and_propagates_failure(self):
        for name, job in (("core-ci.yml", "core"), ("release.yml", "release")):
            with self.subTest(workflow=name):
                self.assert_acceptance_invocation(step(workflow(name)["jobs"][job], "accept-core-artifact")["run"])

    def test_acceptance_invocation_kills_noop_and_failure_swallowing_mutants(self):
        body = step(self.job, "accept-core-artifact")["run"]
        for mutant in (": # " + body, body + " || true"):
            with self.assertRaises(AssertionError):
                self.assert_acceptance_invocation(mutant)

    def assert_tag_boundary(self, body):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "VERSION").write_text("0.3.0\n")
            self.assertEqual(shell(body, root, {"GITHUB_REF_NAME": "v0.3.0"}).returncode, 0)
            for tag in ("v0.3.1", "0.3.0", "v0.3.0; touch injected"):
                result = shell(body, root, {"GITHUB_REF_NAME": tag})
                self.assertNotEqual(result.returncode, 0, tag)
            self.assertFalse((root / "injected").exists())

    def test_tag_version_boundary_executes(self):
        self.assert_tag_boundary(step(self.job, "check-release-tag")["run"])

    def test_tag_boundary_kills_comment_and_failure_swallowing_mutants(self):
        body = step(self.job, "check-release-tag")["run"]
        for mutant in (body.replace('test "v${version}"', ': # test "v${version}"'),
                       body.replace('= "${GITHUB_REF_NAME}"', '= "${GITHUB_REF_NAME}" || true')):
            with self.assertRaises(AssertionError):
                self.assert_tag_boundary(mutant)

    def manifest(self, change=None, *, missing=False, tampered=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "VERSION").write_text("0.3.0\n")
            dist = root / "dist"
            dist.mkdir()
            package = dist / f"heptatrader-0.3.0-core-{SHA}.tar.gz"
            package.write_bytes(b"the accepted immutable package")
            common = {"source_sha": SHA, "version": "0.3.0", "profile": "core",
                      "package_sha256": hashlib.sha256(package.read_bytes()).hexdigest(),
                      "authorization_effect": "NONE", "paper_authorized": False, "live_authorized": False}
            receipt = {"schema": "heptatrader.release-package-receipt.v1", **common}
            acceptance = {"schema": "heptatrader.core-artifact-acceptance.v1", "result": "PASS", **common}
            if change:
                acceptance.update(change)
            Path(str(package) + ".receipt.json").write_text(json.dumps(receipt))
            if not missing:
                (dist / "core-acceptance.json").write_text(json.dumps(acceptance))
            if tampered:
                package.write_bytes(b"different untested rebuild")
            result = shell(step(self.job, "bind-release-manifest")["run"], root,
                           {"EXPECTED_SHA": SHA, "GITHUB_REF_NAME": "v0.3.0"})
            manifest = dist / "release-manifest.json"
            return result, json.loads(manifest.read_text()) if manifest.exists() else None

    def test_release_manifest_requires_successful_exact_artifact_acceptance(self):
        result, manifest = self.manifest()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(manifest["artifact"]["package_sha256"], manifest["acceptance"]["package_sha256"])
        for fields in ({"source_sha": "b" * 40}, {"package_sha256": "0" * 64}, {"profile": "ib-paper"},
                       {"version": "0.2.0"}, {"result": "FAIL"}, {"paper_authorized": True},
                       {"live_authorized": True}, {"authorization_effect": "ALLOW"}, {"schema": "unknown"}):
            with self.subTest(fields=fields):
                result, manifest = self.manifest(fields)
                self.assertNotEqual(result.returncode, 0)
                self.assertIsNone(manifest)
        for kwargs in ({"missing": True}, {"tampered": True}):
            result, manifest = self.manifest(**kwargs)
            self.assertNotEqual(result.returncode, 0)
            self.assertIsNone(manifest)


if __name__ == "__main__":
    unittest.main()
