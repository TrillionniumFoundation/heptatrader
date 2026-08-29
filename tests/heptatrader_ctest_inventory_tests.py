#!/usr/bin/env python3

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import check_heptatrader_ctest_inventory as inventory  # noqa: E402


EXPECTED = (
    ROOT / "tests/heptatrader-agent-os-ctest-inventory-v1.json")
REPOSITORY_EXPECTED = (
    ROOT / "tests/heptatrader-repository-ctest-inventory-v1.json")


def observed(names: list[str]) -> dict:
    return {
        "kind": "ctestInfo",
        "version": {"major": 1, "minor": 0},
        "tests": [
            {"name": name, "properties": []}
            for name in names
        ],
    }


class CTestInventoryTests(unittest.TestCase):
    def expected(self) -> dict:
        return json.loads(EXPECTED.read_text(encoding="utf-8"))

    def test_current_inventory_contract_is_exact(self) -> None:
        expected = self.expected()
        result = inventory.validate_documents(
            observed(expected["test_names"]),
            expected,
            "agent-os-no-git")
        self.assertTrue(result["passed"])
        self.assertEqual(result["test_count"], 121)
        self.assertIn(
            "hepta_ib_order_lifecycle_tests",
            expected["test_names"])
        self.assertIn(
            "hepta_p1_paper_terminal_witness_verifier_tests",
            expected["test_names"])
        self.assertIn(
            "hepta_paper_terminal_external_latch_tests",
            expected["test_names"])

    def test_repository_inventory_contract_is_exact(self) -> None:
        expected = json.loads(
            REPOSITORY_EXPECTED.read_text(encoding="utf-8"))
        result = inventory.validate_documents(
            observed(expected["test_names"]),
            expected,
            "repository")
        self.assertTrue(result["passed"])
        self.assertEqual(result["test_count"], 146)
        self.assertIn(
            "hepta_versioned_source_baseline_gate",
            expected["test_names"])

    def test_missing_test_fails_closed(self) -> None:
        expected = self.expected()
        with self.assertRaisesRegex(
                inventory.CTestInventoryError, "inventory drifted"):
            inventory.validate_documents(
                observed(expected["test_names"][:-1]),
                expected,
                "agent-os-no-git")

    def test_unexpected_test_fails_closed(self) -> None:
        expected = self.expected()
        names = list(expected["test_names"])
        names.append("unreviewed_test")
        with self.assertRaisesRegex(
                inventory.CTestInventoryError, "inventory drifted"):
            inventory.validate_documents(
                observed(names),
                expected,
                "agent-os-no-git")

    def test_duplicate_expected_name_fails_closed(self) -> None:
        expected = self.expected()
        expected["test_names"][-1] = expected["test_names"][0]
        with self.assertRaisesRegex(
                inventory.CTestInventoryError,
                "duplicate test names|digest mismatch"):
            inventory.expected_inventory(
                expected, "agent-os-no-git")

    def test_profile_drift_fails_closed(self) -> None:
        expected = self.expected()
        with self.assertRaisesRegex(
                inventory.CTestInventoryError, "identity drifted"):
            inventory.expected_inventory(
                expected, "repository")

    def test_repository_profile_is_derived_from_configured_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "repository"
            build = root / "build"
            (source / "tests").mkdir(parents=True)
            (source / ".git").mkdir()
            build.mkdir()
            (build / "CMakeCache.txt").write_text(
                "CMAKE_HOME_DIRECTORY:INTERNAL=" + str(source) + "\n",
                encoding="utf-8")
            configured = inventory._configured_source_root(build)
            self.assertEqual(configured, source.resolve())
            self.assertEqual(
                inventory.detect_source_profile(configured), "repository")
            self.assertEqual(
                inventory.expected_path_for_profile(
                    configured, "repository"),
                configured / "tests/heptatrader-repository-ctest-inventory-v1.json")

    def test_no_git_profile_requires_safe_source_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            marker = source / ".hepta/agent-os-source-manifest.json"
            marker.parent.mkdir()
            marker.write_text(json.dumps({
                "schema": "hepta.agent-os-source-bundle.v1",
                "bundle_class": "agent-os-source-only",
                "paper_authorized": False,
                "live_authorized": False,
            }) + "\n", encoding="utf-8")
            marker.chmod(0o644)
            self.assertEqual(
                inventory.detect_source_profile(source), "agent-os-no-git")
            self.assertEqual(
                inventory.expected_path_for_profile(
                    source, "agent-os-no-git"),
                source / "tests/heptatrader-agent-os-ctest-inventory-v1.json")

    def test_profile_markers_cannot_be_ambiguous(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"
            source.mkdir()
            (source / ".git").mkdir()
            marker = source / ".hepta/agent-os-source-manifest.json"
            marker.parent.mkdir()
            marker.write_text(json.dumps({
                "schema": "hepta.agent-os-source-bundle.v1",
                "bundle_class": "agent-os-source-only",
                "paper_authorized": False,
                "live_authorized": False,
            }) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(
                    inventory.CTestInventoryError, "ambiguous"):
                inventory.detect_source_profile(source)

    def test_unmarked_source_profile_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"
            source.mkdir()
            with self.assertRaisesRegex(
                    inventory.CTestInventoryError,
                    "cannot determine source profile"):
                inventory.detect_source_profile(source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
