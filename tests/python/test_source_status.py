#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "derive_source_status", ROOT / "scripts/derive_source_status.py"
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class SourceStatusTests(unittest.TestCase):
    def _repository(self) -> Path:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "docs").mkdir()
        register = {
            "schema": "heptatrader.gap-register.v1",
            "authorization": {
                "source_state": "CANDIDATE",
                "paper_authorized": False,
                "live_authorized": False,
            },
            "gaps": [
                {
                    "id": "SOURCE-001",
                    "domain": "REPOSITORY",
                    "state": "CLOSED_SOURCE",
                    "blocking_authorization": False,
                    "summary": "fixture",
                    "evidence": ["docs/gap-register.json"],
                    "issue": None,
                },
                {
                    "id": "EXTERNAL-001",
                    "domain": "EXTERNAL",
                    "state": "OPEN_EXTERNAL",
                    "blocking_authorization": True,
                    "summary": "fixture",
                    "evidence": ["docs/gap-register.json"],
                    "issue": "https://example.invalid/1",
                },
            ],
        }
        (root / "docs/gap-register.json").write_text(
            json.dumps(register), encoding="utf-8"
        )
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
        subprocess.run(["git", "config", "user.name", "Source Status Test"], cwd=root, check=True)
        subprocess.run(["git", "config", "user.email", "source-status@example.invalid"], cwd=root, check=True)
        subprocess.run(["git", "add", "."], cwd=root, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "fixture"], cwd=root, check=True)
        return root

    def test_pass_derives_repository_closed_but_external_open(self) -> None:
        root = self._repository()
        checks = (("fixture", (sys.executable, "-c", "print('PASS')")),)
        with mock.patch.object(MODULE, "CHECKS", checks):
            receipt, passed = MODULE.derive(root)
        self.assertTrue(passed)
        self.assertTrue(receipt["source_correct"])
        self.assertEqual("VERIFIED_CLOSED", receipt["gaps"][0]["derived_state"])
        self.assertFalse(receipt["gaps"][0]["blocking_authorization"])
        self.assertEqual("OPEN_EXTERNAL", receipt["gaps"][1]["derived_state"])
        self.assertTrue(receipt["gaps"][1]["blocking_authorization"])
        self.assertFalse(receipt["authorization"]["paper_authorized"])
        self.assertTrue(receipt["receipt_sha256"].startswith("sha256:"))

    def test_failed_predicate_reopens_repository_gap(self) -> None:
        root = self._repository()
        checks = (("fixture", (sys.executable, "-c", "raise SystemExit(7)")),)
        with mock.patch.object(MODULE, "CHECKS", checks):
            receipt, passed = MODULE.derive(root)
        self.assertFalse(passed)
        self.assertFalse(receipt["source_correct"])
        self.assertEqual("OPEN_SOURCE", receipt["gaps"][0]["derived_state"])
        self.assertTrue(receipt["gaps"][0]["blocking_authorization"])
        self.assertEqual(7, receipt["checks"][0]["exit_code"])

    def test_dirty_worktree_is_rejected(self) -> None:
        root = self._repository()
        (root / "dirty.txt").write_text("dirty", encoding="utf-8")
        checks = (("fixture", (sys.executable, "-c", "print('PASS')")),)
        with mock.patch.object(MODULE, "CHECKS", checks):
            with self.assertRaises(MODULE.SourceStatusError):
                MODULE.derive(root)


if __name__ == "__main__":
    unittest.main()
