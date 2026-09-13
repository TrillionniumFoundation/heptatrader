from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest

from test_ib_paper_qualification import QualificationFixture, VERIFIER, write_private


class PaperEvidencePublicationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="hepta-publication-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.fixture = QualificationFixture(self.root)
        self.archive = self.root / "verified-evidence.tar"

    def verify(self, *extra: str) -> subprocess.CompletedProcess:
        return subprocess.run([
            sys.executable, str(Path(VERIFIER.__file__)),
            "--result", str(self.fixture.result), "--evidence-root", str(self.fixture.root),
            "--expected-git-sha", self.fixture.git_sha,
            "--expected-binary", str(self.fixture.binary),
            "--expected-harness", str(self.fixture.harness),
            "--publication-archive", str(self.archive), *extra,
        ], capture_output=True, timeout=10)

    def attempt(self, **changes) -> Path:
        _, binary = VERIFIER.verify_tool(self.fixture.binary, "binary")
        _, harness = VERIFIER.verify_tool(self.fixture.harness, "harness")
        value = dict(schema="hepta.ib-paper-attempt.v1",
                     state="HARNESS_SUCCEEDED_AWAITING_VERIFICATION", returncode=0,
                     source_sha=self.fixture.git_sha, binary_sha256=binary,
                     harness_sha256=harness, paper_authorized=False, live_authorized=False)
        value.update(changes)
        path = self.fixture.root.parent / "attempt.json"
        write_private(path, json.dumps(value).encode())
        return path

    def test_controller_attempt_binding_accepts_only_same_successful_attempt(self) -> None:
        result = self.verify("--attempt", str(self.attempt()))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_incomplete_failed_or_different_attempt_cannot_publish(self) -> None:
        cases = ({"state":"RESERVED"}, {"state":"RUNNING"}, {"state":"INTERRUPTED"},
                 {"returncode":42}, {"returncode":False}, {"private_cleanup_failed":True},
                 {"source_sha":"f"*40}, {"binary_sha256":"f"*64},
                 {"harness_sha256":"f"*64}, {"paper_authorized":True}, {"live_authorized":True})
        for changes in cases:
            with self.subTest(changes=changes):
                result = self.verify("--attempt", str(self.attempt(**changes)))
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.archive.exists())

    def test_unrelated_attempt_path_is_rejected(self) -> None:
        path = self.attempt()
        other = self.root / "elsewhere"
        other.mkdir(mode=0o700)
        moved = other / "attempt.json"
        path.rename(moved)
        self.assertNotEqual(self.verify("--attempt", str(moved)).returncode, 0)
        self.assertFalse(self.archive.exists())

    def test_publishes_exact_reference_allowlist(self) -> None:
        # Private HOME/scratch is a sibling, not under the verifier root.
        private = self.root / "private-home"
        private.mkdir(mode=0o700)
        write_private(private / "secret.txt", b"DO_NOT_PUBLISH")
        result = self.verify()
        self.assertEqual(result.returncode, 0, result.stderr)
        expected = {"qualification-result.json", "qualification-verification.json"}
        expected.update(e["path"] for s in self.fixture.payload["scenarios"] for e in s["evidence"])
        with tarfile.open(self.archive) as archive:
            self.assertEqual(set(archive.getnames()), expected)
            self.assertTrue(all(m.isfile() and m.mode == 0o600 for m in archive.getmembers()))
            self.assertNotIn(b"DO_NOT_PUBLISH", b"".join(archive.extractfile(m).read() for m in archive.getmembers()))
        self.assertEqual(self.archive.stat().st_nlink, 1)

    def test_publication_is_never_overwritten(self) -> None:
        self.archive.write_bytes(b"original")
        self.assertNotEqual(self.verify().returncode, 0)
        self.assertEqual(self.archive.read_bytes(), b"original")

    def test_extra_private_file_in_raw_evidence_rejects_publication(self) -> None:
        write_private(self.fixture.root / "unexpected-secret.txt", b"DO_NOT_PUBLISH")
        self.assertNotEqual(self.verify().returncode, 0)
        self.assertFalse(self.archive.exists())
        self.assertTrue((self.fixture.root / "unexpected-secret.txt").exists())

    def test_failed_result_cannot_publish_a_verified_archive(self) -> None:
        self.fixture.payload["qualified"] = False
        self.fixture.write_result()
        self.assertNotEqual(self.verify().returncode, 0)
        self.assertFalse(self.archive.exists())

    def test_changed_evidence_between_verification_and_publication_is_rejected(self) -> None:
        binary_name, binary_sha = VERIFIER.verify_tool(self.fixture.binary, "binary")
        harness_name, harness_sha = VERIFIER.verify_tool(self.fixture.harness, "harness")
        receipt = VERIFIER.validate_result(
            self.fixture.payload, root=self.fixture.root, result_path=self.fixture.result,
            expected_git_sha=self.fixture.git_sha, binary_name=binary_name, binary_sha256=binary_sha,
            harness_name=harness_name, harness_sha256=harness_sha)
        reference = self.fixture.payload["scenarios"][0]["evidence"][0]
        write_private(self.fixture.root / reference["path"], b"changed")
        with self.assertRaises(VERIFIER.QualificationError):
            VERIFIER.publish_verified_archive(self.fixture.root, self.fixture.result.read_bytes(), receipt, self.archive)
        self.assertFalse(self.archive.exists())
        self.assertEqual(list(self.root.glob(".verified-evidence-*")), [])

    def test_fifo_evidence_is_rejected_without_hanging(self) -> None:
        reference = self.fixture.payload["scenarios"][0]["evidence"][0]
        path = self.fixture.root / reference["path"]
        path.unlink()
        os.mkfifo(path, 0o600)
        self.assertNotEqual(self.verify().returncode, 0)
        self.assertFalse(self.archive.exists())

    def test_cannot_publish_inside_raw_evidence(self) -> None:
        self.archive = self.fixture.root / "archive.tar"
        self.assertNotEqual(self.verify().returncode, 0)
        self.assertFalse(self.archive.exists())


if __name__ == "__main__":
    unittest.main()
