from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "schemas/allocation-plan-v1.json"


def digest(value: str) -> str:
    return "sha256:" + value * 64


def valid_plan() -> dict[str, object]:
    return {
        "schema": "hepta.allocation-plan.v1",
        "plan_id": "plan-0123456789abcdef",
        "allocator_epoch": 7,
        "capital_pool": "pool-a",
        "account_book": "book-a",
        "policy_revision": "policy-v1",
        "proposal_set_digest": digest("a"),
        "snapshot_digest": digest("b"),
        "proposal_captured_at_ms": 1000,
        "proposal_valid_until_ms": 1500,
        "snapshot_valid_until_ms": 1600,
        "solver": {
            "schema": "hepta.solver-result.v1",
            "status": "optimal",
            "objective_raw": 10,
            "primal_bound_raw": 10,
            "upper_bound_raw": 10,
            "absolute_gap_raw": 0,
            "combinations_explored": 4,
            "exact": True,
            "digest": digest("c"),
        },
        "targets": [
            {"instrument": "EUR.USD", "target_position_raw": 1000000}
        ],
        "accepted_candidates": ["proposal-alpha:candidate-a"],
        "rejected_proposals": ["proposal-beta"],
        "created_at_ms": 1100,
        "valid_until_ms": 1500,
        "numeric_policy_version": "hepta.numeric.fixed-v1",
        "plan_digest": digest("d"),
    }


class DecisionContractSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(cls.schema)
        cls.validator = Draft202012Validator(cls.schema)

    def assert_valid(self, value: dict[str, object]) -> None:
        errors = sorted(self.validator.iter_errors(value), key=lambda item: list(item.path))
        self.assertEqual([], errors, "\n".join(error.message for error in errors))

    def assert_invalid(self, value: dict[str, object]) -> None:
        self.assertTrue(list(self.validator.iter_errors(value)))

    def test_complete_reviewed_plan_is_valid(self) -> None:
        self.assert_valid(valid_plan())

    def test_identity_and_lifetime_fields_are_closed_world_required(self) -> None:
        required = {
            "policy_revision",
            "proposal_captured_at_ms",
            "proposal_valid_until_ms",
            "snapshot_valid_until_ms",
        }
        self.assertTrue(required.issubset(set(self.schema["required"])))
        for field in sorted(required):
            plan = valid_plan()
            del plan[field]
            with self.subTest(field=field):
                self.assert_invalid(plan)

        unknown = valid_plan()
        unknown["caller_selected_valid_until_ms"] = 999999
        self.assert_invalid(unknown)

    def test_numeric_and_collection_boundaries_fail_closed(self) -> None:
        out_of_range = valid_plan()
        out_of_range["targets"] = [
            {
                "instrument": "EUR.USD",
                "target_position_raw": 9000000000000001,
            }
        ]
        self.assert_invalid(out_of_range)

        duplicate_target = valid_plan()
        duplicate_target["targets"] = [
            {"instrument": "EUR.USD", "target_position_raw": 1000000},
            {"instrument": "EUR.USD", "target_position_raw": 1000000},
        ]
        self.assert_invalid(duplicate_target)

        duplicate_lineage = valid_plan()
        duplicate_lineage["accepted_candidates"] = [
            "proposal-alpha:candidate-a",
            "proposal-alpha:candidate-a",
        ]
        self.assert_invalid(duplicate_lineage)

    def test_solver_exactness_contract_is_enforced(self) -> None:
        invalid = valid_plan()
        solver = deepcopy(invalid["solver"])
        assert isinstance(solver, dict)
        solver["exact"] = True
        solver["status"] = "feasible_not_proven"
        invalid["solver"] = solver
        self.assert_invalid(invalid)

        invalid = valid_plan()
        solver = deepcopy(invalid["solver"])
        assert isinstance(solver, dict)
        solver["absolute_gap_raw"] = 1
        invalid["solver"] = solver
        self.assert_invalid(invalid)

    def test_runtime_digest_and_execution_context_cover_schema_fields(self) -> None:
        header = (ROOT / "HeptaTrade/allocation/global_allocator.h").read_text(
            encoding="utf-8"
        )
        source = (ROOT / "HeptaTrade/allocation/global_allocator.cpp").read_text(
            encoding="utf-8"
        )
        revalidator = (
            ROOT / "HeptaTrade/execution/allocation_plan_revalidator.h"
        ).read_text(encoding="utf-8")

        runtime_fields = {
            "policy_revision": "policyRevision",
            "proposal_captured_at_ms": "proposalCapturedAtMs",
            "proposal_valid_until_ms": "proposalValidUntilMs",
            "snapshot_valid_until_ms": "snapshotValidUntilMs",
        }
        for wire_name, cpp_name in runtime_fields.items():
            with self.subTest(field=wire_name):
                self.assertIn(cpp_name, header)
                self.assertIn(f'"{wire_name}"', source)

        for context_field in (
            "allocatorEpoch",
            "capitalPool",
            "accountBook",
            "policyRevision",
            "proposalSetDigest",
            "authoritativeSnapshotDigest",
            "authoritativeSnapshotValidUntilMs",
        ):
            with self.subTest(context=context_field):
                self.assertIn(context_field, revalidator)

        self.assertIn("GlobalDecisionReceipt", header)
        self.assertIn("friend class GlobalAllocator", header)

    def test_normative_text_does_not_claim_total_double_injectivity(self) -> None:
        numeric = (ROOT / "docs/architecture/NUMERIC-POLICY.md").read_text(
            encoding="utf-8"
        )
        allocation = (
            ROOT / "docs/contracts/ALLOCATION-PLAN-CONTRACT.md"
        ).read_text(encoding="utf-8")
        self.assertIn("ToDoubleExact", numeric)
        self.assertIn("NUMERIC_DOUBLE_PROJECTION_LOSS", numeric)
        self.assertNotIn(
            "使兼容 double 在规范化后仍保留单 microunit 整数身份", numeric
        )
        self.assertIn("construction-restricted", allocation)
        self.assertIn("authenticated authority envelope", allocation)


if __name__ == "__main__":
    unittest.main()
