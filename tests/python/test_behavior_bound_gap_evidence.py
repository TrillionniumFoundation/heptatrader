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


    def test_runtime_source_spelling_is_not_gap_closure_evidence(self):
        original=gaps.read_text
        def read(path):
            if path.suffix in {'.cpp','.h'}:
                raise AssertionError('runtime text cannot prove executable behavior')
            return original(path)
        with mock.patch.object(gaps,'read_text',side_effect=read):
            self.assertEqual(gaps.validate(ROOT),[])


if __name__ == "__main__":
    unittest.main()
