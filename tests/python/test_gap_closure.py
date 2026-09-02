from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "scripts/check_gap_closure.py"
SPEC = importlib.util.spec_from_file_location("check_gap_closure", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
CHECKER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECKER)


class GapClosureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.gaps = json.loads(
            (ROOT / "docs/program/gap-registry-v2.json").read_text(
                encoding="utf-8"
            )
        )
        self.modules = json.loads(
            (ROOT / "docs/modules/module-registry-v2.json").read_text(
                encoding="utf-8"
            )
        )

    def validate(self, gaps: dict, modules: dict) -> list[str]:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            gap_path = root / "gaps.json"
            module_path = root / "modules.json"
            gap_path.write_text(json.dumps(gaps), encoding="utf-8")
            module_path.write_text(json.dumps(modules), encoding="utf-8")
            return CHECKER.validate(gap_path, module_path)

    def test_current_repository_has_zero_open_internal_gaps(self) -> None:
        self.assertEqual([], self.validate(self.gaps, self.modules))
        result = CHECKER.summary()
        self.assertEqual([], result["repository_executable_open"])
        self.assertEqual(
            ["G-IB-001", "G-TEAM-001"], result["external_open"]
        )
        self.assertFalse(result["external_evidence_synthesized"])

    def test_open_repository_gap_is_rejected(self) -> None:
        gaps = copy.deepcopy(self.gaps)
        internal = next(
            item for item in gaps["gaps"] if item["id"] == "G-DOC-003"
        )
        internal["state"] = "in-progress"
        errors = self.validate(gaps, self.modules)
        self.assertTrue(
            any("repository-executable gap must be closed" in item for item in errors),
            errors,
        )

    def test_open_registered_external_gate_is_allowed(self) -> None:
        errors = self.validate(self.gaps, self.modules)
        self.assertFalse(
            any("G-IB-001: repository-executable" in item for item in errors),
            errors,
        )
        self.assertFalse(
            any("G-TEAM-001: repository-executable" in item for item in errors),
            errors,
        )

    def test_unknown_external_gate_is_rejected(self) -> None:
        modules = copy.deepcopy(self.modules)
        modules["implementation_evidence_policy"]["external_gate_ids"].append(
            "G-EXTERNAL-UNKNOWN"
        )
        errors = self.validate(self.gaps, modules)
        self.assertTrue(
            any("external gate is absent" in item for item in errors), errors
        )

    def test_external_gate_cannot_be_closed_without_receipt(self) -> None:
        gaps = copy.deepcopy(self.gaps)
        gate = next(item for item in gaps["gaps"] if item["id"] == "G-IB-001")
        gate["state"] = "closed"
        errors = self.validate(gaps, self.modules)
        self.assertTrue(
            any("qualification receipt" in item for item in errors), errors
        )

    def test_external_fail_closed_policy_is_mandatory(self) -> None:
        modules = copy.deepcopy(self.modules)
        modules["implementation_evidence_policy"][
            "external_gates_fail_closed"
        ] = False
        errors = self.validate(self.gaps, modules)
        self.assertIn(
            "module registry: external_gates_fail_closed must be true", errors
        )


if __name__ == "__main__":
    unittest.main()
