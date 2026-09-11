#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import verify_ib_paper_qualification as verifier  # noqa: E402


class IbPaperScenarioContractTests(unittest.TestCase):
    def test_reviewable_contract_matches_executable_verifier(self) -> None:
        path = ROOT / "docs/ib-paper-qualification-scenarios-v1.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(payload["schema"], "heptatrader.ib-paper-scenario-contract.v1")
        scenarios = payload["scenarios"]
        self.assertEqual([item["id"] for item in scenarios], list(verifier.REQUIRED_SCENARIOS))

        for item in scenarios:
            scenario_id = item["id"]
            self.assertIsInstance(item.get("objective"), str)
            self.assertGreaterEqual(len(item["objective"].strip()), 40)
            self.assertEqual(
                set(item["required_assertions"]),
                set(verifier.REQUIRED_ASSERTIONS[scenario_id]),
            )
            self.assertEqual(
                set(item["required_evidence_kinds"]),
                set(verifier.REQUIRED_EVIDENCE_KINDS[scenario_id]),
            )
            self.assertEqual(len(item["required_assertions"]), len(set(item["required_assertions"])))
            self.assertEqual(len(item["required_evidence_kinds"]), len(set(item["required_evidence_kinds"])))

    def test_contract_has_no_extra_or_missing_scenarios(self) -> None:
        payload = json.loads(
            (ROOT / "docs/ib-paper-qualification-scenarios-v1.json").read_text(encoding="utf-8")
        )
        ids = [item["id"] for item in payload["scenarios"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(set(ids), set(verifier.REQUIRED_SCENARIOS))


if __name__ == "__main__":
    unittest.main()
