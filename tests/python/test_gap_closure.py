from __future__ import annotations

import copy
import hashlib
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

    def validate(
        self,
        gaps: dict,
        modules: dict,
        receipts: dict[str, dict] | None = None,
    ) -> list[str]:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            gap_path = root / "gaps.json"
            module_path = root / "modules.json"
            gap_path.write_text(json.dumps(gaps), encoding="utf-8")
            module_path.write_text(json.dumps(modules), encoding="utf-8")
            for relative, payload in (receipts or {}).items():
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(payload), encoding="utf-8")
            return CHECKER.validate(
                gap_path, module_path, repository_root=root
            )

    @staticmethod
    def ib_receipt() -> dict:
        scenarios = [
            {
                "id": scenario_id,
                "status": "PASS",
                "evidence": [
                    {
                        "path": f"{scenario_id}/evidence.json",
                        "kind": "oms-journal",
                        "sha256": "a" * 64,
                        "size": 1,
                    }
                ],
            }
            for scenario_id in sorted(CHECKER.IB_SCENARIOS)
        ]
        return {
            "schema": "hepta.ib-paper-qualification-verification.v1",
            "verified": True,
            "qualified": True,
            "git_sha": "1" * 40,
            "binary": {"name": "hepta-ib-executiond", "sha256": "2" * 64},
            "harness": {"name": "ib-paper-harness", "sha256": "3" * 64},
            "result_sha256": "4" * 64,
            "broker": {
                "venue": "IB",
                "environment": "PAPER",
                "session_id": "paper-session",
                "account_fingerprint": "sha256:" + "5" * 64,
                "host_fingerprint": "sha256:" + "6" * 64,
            },
            "scenarios": scenarios,
        }

    @staticmethod
    def governance_receipt() -> dict:
        body = {
            "schema": "heptatrader.github-governance-receipt.v1",
            "verified_at": "2026-09-02T00:00:00Z",
            "repository": "TrillionniumFoundation/heptatrader",
            "default_branch": "main",
            "pull_number": 17,
            "head_sha": "7" * 40,
            "merge_group_sha": "8" * 40,
            "ruleset_id": 123,
            "team_slugs": ["architecture", "execution", "risk", "reliability"],
            "required_pull_request_contexts": ["core-runtime-exact-head"],
            "required_merge_group_contexts": ["exact-merge-candidate"],
            "api_response_digests": {
                "/repos/TrillionniumFoundation/heptatrader": "sha256:"
                + "9" * 64
            },
        }
        canonical = json.dumps(
            body, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        return {
            "body": body,
            "receipt_sha256": "sha256:" + hashlib.sha256(canonical).hexdigest(),
        }

    def test_current_repository_has_zero_open_internal_gaps(self) -> None:
        self.assertEqual([], self.validate(self.gaps, self.modules))
        result = CHECKER.summary()
        self.assertEqual([], result["repository_executable_open"])
        self.assertEqual(
            ["G-IB-001", "G-TEAM-001"], result["external_open"]
        )
        self.assertEqual([], result["external_closed_with_receipt"])
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
        self.assertTrue(
            any("no receipt verifier" in item for item in errors), errors
        )

    def test_external_gate_cannot_be_closed_without_receipt(self) -> None:
        gaps = copy.deepcopy(self.gaps)
        gate = next(item for item in gaps["gaps"] if item["id"] == "G-IB-001")
        gate["state"] = "closed"
        errors = self.validate(gaps, self.modules)
        self.assertTrue(
            any("requires qualification_receipt" in item for item in errors),
            errors,
        )

    def test_ib_external_gate_closes_with_verifier_receipt(self) -> None:
        gaps = copy.deepcopy(self.gaps)
        gate = next(item for item in gaps["gaps"] if item["id"] == "G-IB-001")
        gate["state"] = "closed"
        gate["qualification_receipt"] = "receipts/ib.json"
        errors = self.validate(
            gaps,
            self.modules,
            {"receipts/ib.json": self.ib_receipt()},
        )
        self.assertEqual([], errors)

    def test_ib_receipt_capability_inflation_is_rejected(self) -> None:
        gaps = copy.deepcopy(self.gaps)
        gate = next(item for item in gaps["gaps"] if item["id"] == "G-IB-001")
        gate["state"] = "closed"
        gate["qualification_receipt"] = "receipts/ib.json"
        receipt = self.ib_receipt()
        receipt["broker"]["environment"] = "LIVE"
        errors = self.validate(
            gaps, self.modules, {"receipts/ib.json": receipt}
        )
        self.assertTrue(any("must bind IB PAPER" in item for item in errors), errors)

    def test_governance_external_gate_closes_with_digest_bound_receipt(self) -> None:
        gaps = copy.deepcopy(self.gaps)
        gate = next(
            item for item in gaps["gaps"] if item["id"] == "G-TEAM-001"
        )
        gate["state"] = "closed"
        gate["qualification_receipt"] = "receipts/governance.json"
        errors = self.validate(
            gaps,
            self.modules,
            {"receipts/governance.json": self.governance_receipt()},
        )
        self.assertEqual([], errors)

    def test_governance_receipt_tampering_is_rejected(self) -> None:
        gaps = copy.deepcopy(self.gaps)
        gate = next(
            item for item in gaps["gaps"] if item["id"] == "G-TEAM-001"
        )
        gate["state"] = "closed"
        gate["qualification_receipt"] = "receipts/governance.json"
        receipt = self.governance_receipt()
        receipt["body"]["team_slugs"].pop()
        errors = self.validate(
            gaps,
            self.modules,
            {"receipts/governance.json": receipt},
        )
        self.assertTrue(
            any("four distinct teams" in item for item in errors), errors
        )
        self.assertTrue(any("digest mismatch" in item for item in errors), errors)

    def test_external_receipt_path_escape_is_rejected(self) -> None:
        gaps = copy.deepcopy(self.gaps)
        gate = next(item for item in gaps["gaps"] if item["id"] == "G-IB-001")
        gate["state"] = "closed"
        gate["qualification_receipt"] = "../receipt.json"
        errors = self.validate(gaps, self.modules)
        self.assertTrue(any("path is unsafe" in item for item in errors), errors)

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
