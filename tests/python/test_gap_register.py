from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import check_gap_register as gaps  # noqa: E402
import verify_source_gap_closures as source_gaps  # noqa: E402




class GapRegisterTests(unittest.TestCase):
    def value(self) -> dict:
        return json.loads(
            (ROOT / "docs/gap-register.json").read_text(encoding="utf-8")
        )


    def test_repository_register_passes(self) -> None:
        self.assertEqual(gaps.validate(ROOT), [])

    def test_all_supported_scope_gaps_are_closed(self) -> None:
        value = self.value()
        self.assertEqual(value["authorization"]["source_state"], "READY")
        self.assertTrue(value["gaps"])
        self.assertFalse(
            [
                item
                for item in value["gaps"]
                if item["domain"] == "EXTERNAL"
            ]
        )
        self.assertTrue(
            all(item["state"] == "CLOSED_SOURCE" for item in value["gaps"])
        )
        self.assertTrue(
            all(
                item["blocking_authorization"] is False
                for item in value["gaps"]
            )
        )
        self.assertEqual(gaps.REQUIRED_EXTERNAL_GAPS, {})
        self.assertEqual(source_gaps.EXPECTED_EXTERNAL_GAPS, set())

    def test_optional_broker_capability_does_not_self_authorize(self) -> None:
        value = self.value()
        self.assertIs(value["authorization"]["paper_authorized"], False)
        self.assertIs(value["authorization"]["live_authorized"], False)
        capabilities = json.loads(
            (ROOT / "docs/capabilities.json").read_text(encoding="utf-8")
        )
        by_id = {
            item["id"]: item
            for item in capabilities["capabilities"]
        }
        self.assertEqual(
            by_id["ib-paper"]["status"], "QUALIFICATION_REQUIRED"
        )
        self.assertEqual(by_id["live"]["status"], "UNAVAILABLE")

    def test_source_projection_passes(self) -> None:
        self.assertEqual(source_gaps.validate_register_projection(ROOT), [])








if __name__ == "__main__":
    unittest.main()
