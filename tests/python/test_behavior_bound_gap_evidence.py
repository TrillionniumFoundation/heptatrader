from __future__ import annotations

import copy
from pathlib import Path
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import check_gap_register as gaps  # noqa: E402


class BehaviorBoundGapEvidenceTests(unittest.TestCase):
    def test_repository_behavior_bound_register_passes(self) -> None:
        self.assertEqual(gaps.validate(ROOT), [])

    def test_required_behavior_evidence_cannot_be_removed(self) -> None:
        original_load = gaps.load_json
        register = copy.deepcopy(
            original_load(ROOT / "docs/gap-register.json")
        )
        target_gap = next(
            item
            for item in register["gaps"]
            if item["id"] == "PENDING-EXPOSURE-001"
        )
        target_gap["evidence"].remove(
            "tests/ib_paper_execution_profile_tests.cpp"
        )

        def load(path: Path):
            if path.name == "gap-register.json":
                return copy.deepcopy(register)
            return original_load(path)

        with mock.patch.object(gaps, "load_json", side_effect=load):
            errors = gaps.validate(ROOT)
        self.assertTrue(
            any(
                "PENDING-EXPOSURE-001" in error
                and "missing required behavior evidence" in error
                for error in errors
            ),
            errors,
        )

    def test_v5_order_gross_equality_token_is_required(self) -> None:
        original_read = gaps.read_text

        def read(path: Path) -> str:
            text = original_read(path)
            if path.name == "ib_paper_execution_profile.cpp":
                return text.replace(
                    "maxOrderQuantity != maxGrossPosition",
                    "maxOrderQuantity > maxGrossPosition",
                )
            return text

        with mock.patch.object(gaps, "read_text", side_effect=read):
            errors = gaps.validate(ROOT)
        self.assertTrue(
            any(
                "ib_paper_execution_profile.cpp" in error
                and "maxOrderQuantity != maxGrossPosition" in error
                for error in errors
            ),
            errors,
        )

    def test_v5_test_target_must_exist_in_core_inventory(self) -> None:
        original_load = gaps.load_json
        inventory = copy.deepcopy(
            original_load(ROOT / "docs/build-targets.json")
        )
        inventory["profiles"]["core"]["targets"] = [
            item
            for item in inventory["profiles"]["core"]["targets"]
            if item["name"] != "hepta_ib_paper_execution_profile_tests"
        ]

        def load(path: Path):
            if path.name == "build-targets.json":
                return copy.deepcopy(inventory)
            return original_load(path)

        with mock.patch.object(gaps, "load_json", side_effect=load):
            errors = gaps.validate(ROOT)
        self.assertTrue(
            any(
                "required executable target is missing" in error
                for error in errors
            ),
            errors,
        )

    def test_required_network_policy_evidence_cannot_be_removed(self) -> None:
        original_load = gaps.load_json
        register = copy.deepcopy(
            original_load(ROOT / "docs/gap-register.json")
        )
        target = next(
            item for item in register["gaps"]
            if item["id"] == "PENDING-EXPOSURE-001"
        )
        target["evidence"].remove(
            "tests/python/test_hepta_broker_egress_policy.py"
        )

        def load(path: Path):
            if path.name == "gap-register.json":
                return copy.deepcopy(register)
            return original_load(path)

        with mock.patch.object(gaps, "load_json", side_effect=load):
            errors = gaps.validate(ROOT)
        self.assertTrue(
            any("missing required behavior evidence" in item for item in errors),
            errors,
        )

    def test_canonical_kill_switch_behavior_is_required(self) -> None:
        original_read = gaps.read_text

        def read(path: Path) -> str:
            text = original_read(path)
            if path.name == "hepta_preflight_core.py":
                return text.replace(
                    "/run/hepta/ib-paper-control/kill-switch",
                    "/tmp/arbitrary-marker",
                )
            return text

        with mock.patch.object(gaps, "read_text", side_effect=read):
            errors = gaps.validate(ROOT)
        self.assertTrue(
            any(
                "CANONICAL_IB_PAPER_KILL_SWITCH" in item
                or "/run/hepta/ib-paper-control/kill-switch" in item
                for item in errors
            ),
            errors,
        )

    def test_canonical_network_policy_digest_is_required(self) -> None:
        original_read = gaps.read_text

        def read(path: Path) -> str:
            text = original_read(path)
            if path.name == "hepta_broker_egress_policy.py":
                return text.replace(
                    "5eddd44a588ac3269804cb62adb19c3879febce8569df30ab86886028e969e6b",
                    "0" * 64,
                )
            return text

        with mock.patch.object(gaps, "read_text", side_effect=read):
            errors = gaps.validate(ROOT)
        self.assertTrue(
            any("hepta_broker_egress_policy.py" in item for item in errors),
            errors,
        )

    def test_full_canonical_denominator_is_required(self) -> None:
        original_read = gaps.read_text

        def read(path: Path) -> str:
            text = original_read(path)
            if path.name == "canonical-full-suite.yml":
                return text.replace(
                    "cmake --build build/reliability-clang --target hepta_core_test_binaries",
                    "cmake --build build/reliability-clang --target hepta_execution_coordinator_tests",
                )
            return text

        with mock.patch.object(gaps, "read_text", side_effect=read):
            errors = gaps.validate(ROOT)
        self.assertTrue(
            any("canonical-full-suite.yml" in item for item in errors),
            errors,
        )

    def test_atomic_network_policy_evidence_cannot_be_removed(self) -> None:
        original_load = gaps.load_json
        register = copy.deepcopy(
            original_load(ROOT / "docs/gap-register.json")
        )
        target = next(
            item
            for item in register["gaps"]
            if item["id"] == "PENDING-EXPOSURE-001"
        )
        target["evidence"].remove(
            "tests/python/test_hepta_broker_egress_policy_atomic.py"
        )

        def load(path: Path):
            if path.name == "gap-register.json":
                return copy.deepcopy(register)
            return original_load(path)

        with mock.patch.object(gaps, "load_json", side_effect=load):
            errors = gaps.validate(ROOT)
        self.assertTrue(
            any("missing required behavior evidence" in item for item in errors),
            errors,
        )

    def test_nft_machine_state_behavior_tokens_are_required(self) -> None:
        original_read = gaps.read_text

        def read(path: Path) -> str:
            text = original_read(path)
            if path.name == "hepta_broker_egress_policy.py":
                return text.replace(
                    "def _verify_table(",
                    "def _verify_table_removed(",
                )
            return text

        with mock.patch.object(gaps, "read_text", side_effect=read):
            errors = gaps.validate(ROOT)
        self.assertTrue(
            any("def _verify_table(" in item for item in errors),
            errors,
        )

    def test_localized_nft_diagnostic_protocol_is_forbidden(self) -> None:
        original_read = gaps.read_text

        def read(path: Path) -> str:
            text = original_read(path)
            if path.name == "hepta_broker_egress_policy.py":
                return text + "\n# File exists must not be a protocol token\n"
            return text

        with mock.patch.object(gaps, "read_text", side_effect=read):
            errors = gaps.validate(ROOT)
        self.assertTrue(
            any("forbidden diagnostic protocol tokens" in item for item in errors),
            errors,
        )

    def test_atomic_nft_module_catalog_binding_is_required(self) -> None:
        original_load = gaps.load_json
        catalog = copy.deepcopy(
            original_load(ROOT / "docs/module-catalog.json")
        )
        module = next(
            item
            for item in catalog["modules"]
            if item["id"] == "deployment"
        )
        module["tests"].remove(
            "tests/python/test_hepta_broker_egress_policy_atomic.py"
        )

        def load(path: Path):
            if path.name == "module-catalog.json":
                return copy.deepcopy(catalog)
            return original_load(path)

        with mock.patch.object(gaps, "load_json", side_effect=load):
            errors = gaps.validate(ROOT)
        self.assertTrue(
            any("atomic nftables regression" in item for item in errors),
            errors,
        )

    def test_all_behavior_bound_cases_are_discoverable(self) -> None:
        discovered = {
            case._testMethodName
            for case in unittest.defaultTestLoader.loadTestsFromTestCase(
                BehaviorBoundGapEvidenceTests
            )
        }
        required = {
            "test_required_network_policy_evidence_cannot_be_removed",
            "test_canonical_kill_switch_behavior_is_required",
            "test_canonical_network_policy_digest_is_required",
            "test_full_canonical_denominator_is_required",
            "test_atomic_network_policy_evidence_cannot_be_removed",
            "test_nft_machine_state_behavior_tokens_are_required",
            "test_localized_nft_diagnostic_protocol_is_forbidden",
            "test_atomic_nft_module_catalog_binding_is_required",
        }
        self.assertTrue(required.issubset(discovered), sorted(discovered))


if __name__ == "__main__":
    unittest.main()
