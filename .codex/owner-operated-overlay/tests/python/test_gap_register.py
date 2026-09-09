from __future__ import annotations

import json
from pathlib import Path
import sys
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
        self.assertEqual(
            gaps.REQUIRED_EXTERNAL_GAPS,
            {"G-IB-001": "https://github.com/TrillionniumFoundation/heptatrader/issues/9"},
        )

    def test_paper_and_live_remain_unauthorized(self) -> None:
        value = json.loads((ROOT / "docs/gap-register.json").read_text())
        self.assertIs(value["authorization"]["paper_authorized"], False)
        self.assertIs(value["authorization"]["live_authorized"], False)


if __name__ == "__main__":
    unittest.main()
