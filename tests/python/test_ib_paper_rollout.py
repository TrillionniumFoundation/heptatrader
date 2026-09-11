from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "scripts/verify_ib_paper_rollout.py"
SPEC = importlib.util.spec_from_file_location("verify_ib_paper_rollout", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
rollout = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(rollout)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class IbPaperRolloutTests(unittest.TestCase):
    def make_fixture(self, root: Path, stage: str = "canary", cycles: int = 1) -> tuple[Path, Path, Path, Path]:
        binary = root / "hepta-ib-executiond"
        harness = root / "qualifier"
        binary.write_bytes(b"candidate-binary\n")
        harness.write_bytes(b"trusted-harness\n")
        os.chmod(binary, 0o500)
        os.chmod(harness, 0o500)

        evidence_root = root / "evidence"
        evidence_root.mkdir()
        entries = []
        for kind, name, payload in (
            ("authoritative-snapshot", "snapshot.json", b"{}\n"),
            ("broker-callbacks", "callbacks.jsonl", b"{\"event\":\"flat\"}\n"),
            ("oms-journal", "journal.jsonl", b"{\"status\":\"terminal\"}\n"),
        ):
            path = evidence_root / name
            path.write_bytes(payload)
            entries.append(
                {
                    "kind": kind,
                    "path": name,
                    "sha256": digest(path),
                    "size": path.stat().st_size,
                }
            )

        result = {
            "schema": "heptatrader.ib-paper-rollout-result.v1",
            "candidate_sha": "a" * 40,
            "binary_sha256": digest(binary),
            "harness_sha256": digest(harness),
            "stage": stage,
            "account_mode": "PAPER",
            "profile_order_mode": "EXTERNAL_P1_CANARY_LMT_DAY",
            "mutation_cycles": cycles,
            "successful_round_trips": cycles,
            "max_order_quantity": 1.0,
            "max_order_notional": 5000.0,
            "max_active_orders": 1,
            "max_gross_position": 1.0,
            "final_active_orders": 0,
            "final_uncertain_commands": 0,
            "final_position_quantity": 0.0,
            "authoritative_reconciliation_complete": True,
            "live_authorized": False,
            "evidence": entries,
        }
        result_path = root / "rollout-result.json"
        result_path.write_text(json.dumps(result), encoding="utf-8")
        return result_path, evidence_root, binary, harness

    def verify_fixture(self, root: Path, stage: str = "canary", cycles: int = 1) -> dict[str, object]:
        result, evidence, binary, harness = self.make_fixture(root, stage, cycles)
        return rollout.verify(
            result,
            evidence,
            "a" * 40,
            binary,
            harness,
            stage,
        )

    def test_valid_canary_has_no_authorization_effect(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            receipt = self.verify_fixture(Path(directory))
            self.assertEqual(receipt["stage"], "canary")
            self.assertEqual(receipt["mutation_cycles"], 1)
            self.assertEqual(receipt["authorization_effect"], "NONE")
            self.assertIs(receipt["paper_authorized"], False)
            self.assertIs(receipt["live_authorized"], False)

    def test_stage_cycle_limits_are_progressive_without_widening_exposure(self) -> None:
        expected = {"canary": 1, "pilot": 3, "extended": 10}
        policy = rollout._load_json(ROOT / "docs/ib-paper-rollout-policy-v1.json")
        for stage, cycles in expected.items():
            with self.subTest(stage=stage):
                item = rollout._stage_policy(policy, stage)
                self.assertEqual(item["max_mutation_cycles"], cycles)
                self.assertEqual(item["max_order_quantity"], 1.0)
                self.assertEqual(item["max_order_notional"], 5000.0)
                self.assertEqual(item["max_active_orders"], 1)
                self.assertEqual(item["max_gross_position"], 1.0)
                self.assertIs(item["require_flat_between_cycles"], True)

    def test_cycle_limit_is_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result, evidence, binary, harness = self.make_fixture(root, "canary", 2)
            with self.assertRaisesRegex(rollout.VerificationError, "cycles exceed policy"):
                rollout.verify(result, evidence, "a" * 40, binary, harness, "canary")

    def test_nonflat_terminal_state_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result, evidence, binary, harness = self.make_fixture(root)
            value = json.loads(result.read_text(encoding="utf-8"))
            value["final_position_quantity"] = 1.0
            result.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(rollout.VerificationError, "final position is not flat"):
                rollout.verify(result, evidence, "a" * 40, binary, harness, "canary")

    def test_uncertain_command_blocks_promotion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result, evidence, binary, harness = self.make_fixture(root)
            value = json.loads(result.read_text(encoding="utf-8"))
            value["final_uncertain_commands"] = 1
            result.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(rollout.VerificationError, "retained uncertain commands"):
                rollout.verify(result, evidence, "a" * 40, binary, harness, "canary")

    def test_evidence_tampering_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result, evidence, binary, harness = self.make_fixture(root)
            (evidence / "journal.jsonl").write_text("tampered\n", encoding="utf-8")
            with self.assertRaisesRegex(rollout.VerificationError, "size mismatch|digest mismatch"):
                rollout.verify(result, evidence, "a" * 40, binary, harness, "canary")

    def test_result_cannot_widen_p1_limits(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result, evidence, binary, harness = self.make_fixture(root)
            value = json.loads(result.read_text(encoding="utf-8"))
            value["max_order_quantity"] = 2.0
            result.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(rollout.VerificationError, "max_order_quantity exceeds rollout policy"):
                rollout.verify(result, evidence, "a" * 40, binary, harness, "canary")


if __name__ == "__main__":
    unittest.main()
