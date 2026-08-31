from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


def load_verifier():
    path = ROOT / "scripts/verify_ib_paper_qualification.py"
    specification = importlib.util.spec_from_file_location(
        "hepta_verify_ib_paper_qualification", path
    )
    if specification is None or specification.loader is None:
        raise RuntimeError(f"unable to load {path}")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


VERIFIER = load_verifier()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_private(path: Path, data: bytes, executable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    path.chmod(0o700 if executable else 0o600)


class QualificationFixture:
    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.root = workspace / "evidence"
        self.root.mkdir(mode=0o700)
        self.root.chmod(0o700)
        self.binary = workspace / "hepta-ib-executiond"
        self.harness = workspace / "controlled-ib-qualifier"
        write_private(self.binary, b"execution-binary\n", executable=True)
        write_private(self.harness, b"qualification-harness\n", executable=True)
        self.git_sha = "a" * 40
        self.payload = self._payload()
        self.result = self.root / "qualification-result.json"
        self.write_result()

    def _payload(self) -> dict:
        scenarios = []
        start = 1_800_000_000_000
        for index, scenario_id in enumerate(VERIFIER.REQUIRED_SCENARIOS):
            scenario_start = start + index * 1000
            evidence = []
            for kind_index, kind in enumerate(
                sorted(VERIFIER.REQUIRED_EVIDENCE_KINDS[scenario_id])
            ):
                relative = f"scenarios/{scenario_id}/{kind_index}-{kind}.jsonl"
                data = (
                    json.dumps(
                        {
                            "scenario": scenario_id,
                            "kind": kind,
                            "source": "controlled-fixture",
                            "sequence": kind_index,
                        },
                        sort_keys=True,
                    )
                    + "\n"
                ).encode("utf-8")
                write_private(self.root / relative, data)
                evidence.append(
                    {
                        "path": relative,
                        "kind": kind,
                        "sha256": sha256(data),
                        "size": len(data),
                    }
                )
            scenarios.append(
                {
                    "id": scenario_id,
                    "status": "PASS",
                    "started_at_ms": scenario_start,
                    "completed_at_ms": scenario_start + 500,
                    "assertions": sorted(
                        VERIFIER.REQUIRED_ASSERTIONS[scenario_id]
                    ),
                    "evidence": evidence,
                }
            )
        return {
            "schema": VERIFIER.SCHEMA,
            "qualified": True,
            "mode": "bounded-mutations",
            "git_sha": self.git_sha,
            "binary": {
                "name": self.binary.name,
                "sha256": sha256(self.binary.read_bytes()),
            },
            "harness": {
                "name": self.harness.name,
                "sha256": sha256(self.harness.read_bytes()),
            },
            "broker": {
                "venue": "IB",
                "environment": "PAPER",
                "transport": "TWS_API",
                "api_version": "10.30",
                "session_id": "paper-session-001",
                "account_fingerprint": "sha256:" + "b" * 64,
                "host_fingerprint": "sha256:" + "c" * 64,
                "origin": "broker-observed",
                "simulated": False,
                "test_double": False,
            },
            "started_at_ms": start,
            "completed_at_ms": start + len(VERIFIER.REQUIRED_SCENARIOS) * 1000,
            "scenarios": scenarios,
        }

    def write_result(self) -> None:
        write_private(
            self.result,
            (json.dumps(self.payload, indent=2, sort_keys=True) + "\n").encode(
                "utf-8"
            ),
        )

    def verify(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/verify_ib_paper_qualification.py"),
                "--result",
                str(self.result),
                "--evidence-root",
                str(self.root),
                "--expected-git-sha",
                self.git_sha,
                "--expected-binary",
                str(self.binary),
                "--expected-harness",
                str(self.harness),
                "--receipt",
                str(self.root / "qualification-verification.json"),
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )


class IbPaperQualificationTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "posix", "qualification metadata requires POSIX")
    def test_valid_complete_evidence_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = QualificationFixture(Path(directory))
            result = fixture.verify()
            self.assertEqual(result.returncode, 0, result.stderr)
            receipt_path = fixture.root / "qualification-verification.json"
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            self.assertTrue(receipt["verified"])
            self.assertTrue(receipt["qualified"])
            self.assertEqual(receipt["git_sha"], fixture.git_sha)
            self.assertEqual(
                len(receipt["scenarios"]), len(VERIFIER.REQUIRED_SCENARIOS)
            )
            self.assertEqual(oct(receipt_path.stat().st_mode & 0o777), "0o600")

    @unittest.skipUnless(os.name == "posix", "qualification metadata requires POSIX")
    def test_simulated_or_test_double_evidence_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = QualificationFixture(Path(directory))
            fixture.payload["broker"]["simulated"] = True
            fixture.write_result()
            result = fixture.verify()
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("simulator or test-double", result.stderr)

    @unittest.skipUnless(os.name == "posix", "qualification metadata requires POSIX")
    def test_missing_scenario_or_assertion_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = QualificationFixture(Path(directory))
            fixture.payload["scenarios"].pop()
            fixture.write_result()
            result = fixture.verify()
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("canonical ordered set", result.stderr)

        with tempfile.TemporaryDirectory() as directory:
            fixture = QualificationFixture(Path(directory))
            fixture.payload["scenarios"][0]["assertions"].pop()
            fixture.write_result()
            result = fixture.verify()
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("required invariant set", result.stderr)

    @unittest.skipUnless(os.name == "posix", "qualification metadata requires POSIX")
    def test_tampered_or_hardlinked_evidence_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = QualificationFixture(Path(directory))
            relative = fixture.payload["scenarios"][0]["evidence"][0]["path"]
            (fixture.root / relative).write_bytes(b"tampered\n")
            (fixture.root / relative).chmod(0o600)
            result = fixture.verify()
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("identity mismatch", result.stderr)

        with tempfile.TemporaryDirectory() as directory:
            fixture = QualificationFixture(Path(directory))
            relative = fixture.payload["scenarios"][0]["evidence"][0]["path"]
            os.link(fixture.root / relative, fixture.workspace / "extra-hardlink")
            result = fixture.verify()
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("exactly one hard link", result.stderr)

    @unittest.skipUnless(os.name == "posix", "qualification metadata requires POSIX")
    def test_path_escape_and_unreferenced_files_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = QualificationFixture(Path(directory))
            fixture.payload["scenarios"][0]["evidence"][0]["path"] = "../escape"
            fixture.write_result()
            result = fixture.verify()
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("relative POSIX", result.stderr)

        with tempfile.TemporaryDirectory() as directory:
            fixture = QualificationFixture(Path(directory))
            write_private(fixture.root / "unreferenced.log", b"unexpected\n")
            result = fixture.verify()
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("unreferenced evidence file", result.stderr)

    @unittest.skipUnless(os.name == "posix", "qualification metadata requires POSIX")
    def test_duplicate_json_keys_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = QualificationFixture(Path(directory))
            data = fixture.result.read_text(encoding="utf-8")
            data = data.replace(
                '"qualified": true,',
                '"qualified": true,\n  "qualified": true,',
                1,
            )
            write_private(fixture.result, data.encode("utf-8"))
            result = fixture.verify()
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("duplicate JSON key", result.stderr)


if __name__ == "__main__":
    unittest.main()
