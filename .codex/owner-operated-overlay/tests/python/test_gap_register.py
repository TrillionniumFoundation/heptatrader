from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import check_gap_register as gaps  # noqa: E402


class GapRegisterTests(unittest.TestCase):
    def test_repository_register_passes(self) -> None:
        self.assertEqual(gaps.validate(ROOT), [])

    def test_only_broker_qualification_is_external(self) -> None:
        value = json.loads((ROOT / "docs/gap-register.json").read_text())
        external = [item for item in value["gaps"] if item["domain"] == "EXTERNAL"]
        self.assertEqual([item["id"] for item in external], ["G-IB-001"])
        self.assertNotIn("G-TEAM-001", {item["id"] for item in value["gaps"]})

    def test_paper_and_live_remain_unauthorized(self) -> None:
        value = json.loads((ROOT / "docs/gap-register.json").read_text())
        self.assertIs(value["authorization"]["paper_authorized"], False)
        self.assertIs(value["authorization"]["live_authorized"], False)

    def test_missing_broker_gap_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "docs").mkdir()
            for name in (
                "gap-register.json",
                "capabilities.json",
                "module-catalog.json",
            ):
                (root / "docs" / name).write_bytes((ROOT / "docs" / name).read_bytes())
            value = json.loads((root / "docs/gap-register.json").read_text())
            value["gaps"] = [
                item for item in value["gaps"] if item["id"] != "G-IB-001"
            ]
            (root / "docs/gap-register.json").write_text(json.dumps(value))
            errors = gaps.validate(root)
            self.assertTrue(any("missing external gaps" in item for item in errors), errors)


if __name__ == "__main__":
    unittest.main()
