from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import check_gap_register as gap_register  # noqa: E402


class GapRegisterTests(unittest.TestCase):
    def test_repository_register_passes(self) -> None:
        self.assertEqual(gap_register.validate(ROOT), [])

    def fixture(self, directory: str) -> Path:
        root = Path(directory)
        (root / "docs").mkdir(parents=True)
        for path in (
            "docs/gap-register.json",
            "docs/capabilities.json",
            "docs/module-catalog.json",
        ):
            destination = root / path
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / path, destination)
        register = json.loads((root / "docs/gap-register.json").read_text(encoding="utf-8"))
        for gap in register["gaps"]:
            for evidence in gap["evidence"]:
                target = root / evidence
                target.parent.mkdir(parents=True, exist_ok=True)
                if not target.exists():
                    if (ROOT / evidence).is_dir():
                        target.mkdir(parents=True, exist_ok=True)
                    else:
                        target.write_text("fixture\n", encoding="utf-8")
        return root

    def mutate(self, root: Path, callback) -> None:
        path = root / "docs/gap-register.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        callback(value)
        path.write_text(json.dumps(value), encoding="utf-8")

    def test_source_cannot_close_external_control(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.fixture(directory)
            self.mutate(
                root,
                lambda value: next(
                    gap for gap in value["gaps"] if gap["id"] == "G-TEAM-001"
                ).update({"state": "CLOSED_SOURCE"}),
            )
            errors = gap_register.validate(root)
            self.assertTrue(any("cannot mark an external control closed" in error for error in errors), errors)

    def test_repository_gap_cannot_be_left_open(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.fixture(directory)
            self.mutate(
                root,
                lambda value: next(
                    gap for gap in value["gaps"] if gap["id"] == "RISK-001"
                ).update({"state": "OPEN_EXTERNAL"}),
            )
            errors = gap_register.validate(root)
            self.assertTrue(any("repository-controlled gap must be closed" in error for error in errors), errors)

    def test_paper_or_live_source_authorization_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.fixture(directory)
            self.mutate(
                root,
                lambda value: value["authorization"].update(
                    {"paper_authorized": True, "live_authorized": True}
                ),
            )
            errors = gap_register.validate(root)
            self.assertTrue(any("PAPER cannot be source-authorized" in error for error in errors), errors)

    def test_external_issue_binding_is_immutable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.fixture(directory)
            self.mutate(
                root,
                lambda value: next(
                    gap for gap in value["gaps"] if gap["id"] == "G-IB-001"
                ).update({"issue": "https://example.invalid/closed"}),
            )
            errors = gap_register.validate(root)
            self.assertTrue(any("external issue binding is invalid" in error for error in errors), errors)


if __name__ == "__main__":
    unittest.main()
