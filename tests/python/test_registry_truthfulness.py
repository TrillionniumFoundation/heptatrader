from __future__ import annotations

import json
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[2]


class RegistryTruthfulnessTests(unittest.TestCase):
    def test_unsupported_venue_stubs_are_described_as_negative_tests_only(self) -> None:
        document = json.loads(
            (
                ROOT / "docs/product/capability-registry-v2.json"
            ).read_text(encoding="utf-8")
        )
        capabilities = {item["id"]: item for item in document["capabilities"]}
        cmake = (ROOT / "HeptaTrade/CMakeLists.txt").read_text(
            encoding="utf-8-sig"
        )
        tests = (ROOT / "tests/CMakeLists.txt").read_text(encoding="utf-8-sig")
        install = (ROOT / "cmake/RuntimeInstall.cmake").read_text(
            encoding="utf-8-sig"
        )

        for capability_id, target in (
            ("hepta.venue.ctp", "hepta_venue_ctp"),
            ("hepta.venue.xt", "hepta_venue_xt"),
        ):
            item = capabilities[capability_id]
            self.assertEqual("unsupported", item["declared_state"])
            self.assertEqual("unsupported-scaffold", item["implementation"])
            self.assertEqual("negative-test-stub-only", item["build"])
            self.assertEqual("excluded", item["release"])
            self.assertEqual("forbidden", item["integration"]["paper"])
            self.assertEqual("forbidden", item["integration"]["live"])
            self.assertRegex(
                cmake,
                rf"add_library\s*\(\s*{re.escape(target)}\b",
                capability_id,
            )
            self.assertIn(target, tests)
            self.assertNotRegex(
                install,
                rf"install\s*\([^)]*\b{re.escape(target)}\b",
                capability_id,
            )

        self.assertIn(
            "negative-test-stub-only",
            document["policy"]["unsupported_venue_build"],
        )

    def test_live_capability_is_absent_and_forbidden(self) -> None:
        document = json.loads(
            (
                ROOT / "docs/product/capability-registry-v2.json"
            ).read_text(encoding="utf-8")
        )
        capabilities = {item["id"]: item for item in document["capabilities"]}
        live = capabilities["hepta.venue.live"]
        self.assertEqual("unsupported", live["declared_state"])
        self.assertEqual("unsupported", live["implementation"])
        self.assertEqual("absent", live["build"])
        self.assertEqual("forbidden", live["integration"]["live"])
        self.assertEqual("excluded", live["release"])
        self.assertEqual([], live["modules"])

    def test_milestone_closed_state_has_repository_only_scope(self) -> None:
        document = json.loads(
            (
                ROOT / "docs/program/milestone-registry-v1.json"
            ).read_text(encoding="utf-8")
        )
        policy = document["policy"]
        self.assertEqual(
            "repository-tree implementation only", policy["state_scope"]
        )
        required_non_implications = {
            "merged-to-main",
            "released",
            "externally-qualified",
            "deployed",
            "paper-authorized",
            "live-authorized",
        }
        self.assertEqual(
            required_non_implications, set(policy["closed_does_not_imply"])
        )
        self.assertTrue(policy["external_gates_fail_closed"])

        milestone_ids: set[str] = set()
        for milestone in document["milestones"]:
            self.assertNotIn(milestone["id"], milestone_ids)
            milestone_ids.add(milestone["id"])
            self.assertIn(milestone["state"], {"planned", "in-progress", "closed"})
            self.assertTrue(milestone["integration_gate"].strip())
            self.assertTrue(milestone["exit"])

        self.assertEqual("in-progress", next(
            item["state"] for item in document["milestones"]
            if item["id"] == "M6"
        ))
        self.assertEqual("in-progress", next(
            item["state"] for item in document["milestones"]
            if item["id"] == "M7"
        ))

    def test_traceability_document_contains_the_same_evidence_ladder(self) -> None:
        text = (
            ROOT / "docs/program/TRACEABILITY-MODEL.md"
        ).read_text(encoding="utf-8")
        for stage in (
            "repository-implemented",
            "exact-head-verified",
            "independently-reviewed",
            "merge-group-verified",
            "merged-main",
            "artifact-reproducible",
            "externally-qualified",
            "deployed-observed",
        ):
            self.assertIn(stage, text)
        self.assertIn("不自动表示", text)
        self.assertIn("PAPER receipt 永不推导 LIVE", text)


if __name__ == "__main__":
    unittest.main()
