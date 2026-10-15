"""Read-only regressions for Laya future-work suppression, never trading."""
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from laya_cell.integrity import ContractError, digest, sha_file
from laya_cell.promotion import assess, rule_for_trial
from laya_cell.promotion_power import PROTOCOL
from laya_cell.resident_evaluate import (
    Evaluator, suppress_new_work_for_frozen_failure,
)


class FrozenFutilityTests(unittest.TestCase):
    def plan(self):
        return {
            "candidate_id": "candidate-frozen",
            "plan_id": "original-plan",
            "trial": 29,
            "rule": rule_for_trial(29, PROTOCOL),
            "outcome_end_ms": 10**16,
        }

    def outcomes(self, plan, count=3):
        return [
            {
                "candidate_id": plan["candidate_id"],
                "plan_id": plan["plan_id"],
                "slot": slot,
                "status": "CENSORED",
                "reason": "PREDICTION_PROCESS_FAILED",
            }
            for slot in range(count)
        ]

    def test_irreversible_censors_suppress_work_without_early_verdict(self):
        plan = self.plan()
        self.assertFalse(suppress_new_work_for_frozen_failure(
            plan, self.outcomes(plan, 2)))
        self.assertTrue(suppress_new_work_for_frozen_failure(
            plan, self.outcomes(plan, 3)))
        original = assess(plan, self.outcomes(plan, 3), 1000)
        self.assertEqual(original["state"], "PENDING")
        self.assertFalse(original["promotion_performed"])

    def test_wrong_identity_or_duplicate_cannot_force_futility(self):
        plan = self.plan()
        for bad in [
            [{**self.outcomes(plan, 1)[0], "candidate_id": "injected"}],
            [{**self.outcomes(plan, 1)[0], "plan_id": "wrong"}],
            self.outcomes(plan, 1) * 2,
            [{**self.outcomes(plan, 1)[0], "status": "UNKNOWN"}],
        ]:
            with self.subTest(bad=bad), self.assertRaises(ContractError):
                suppress_new_work_for_frozen_failure(plan, bad)

    def test_futile_live_trial_never_launches_more_gpu_work(self):
        with tempfile.TemporaryDirectory() as dirname:
            root = Path(dirname)
            period = root / "period.json"
            period.write_text('{"execution_domain":"PAPER"}')
            runtime = root / "runtime"
            runtime.mkdir()
            code = runtime / "pinned.py"
            code.write_text("# fixture\n")
            plan = self.plan()
            rule = plan["rule"]
            plan.update(
                scope=sha_file(period),
                start_ms=int(time.time() * 1000)
                - 3 * rule["slot_spacing_ms"] - 1000,
                evaluation_runtime_sha256={"pinned.py": sha_file(code)},
            )
            # Registered plan identity is SHA-bound to every committed field.
            plan.pop("plan_id")
            plan["plan_id"] = digest(plan)
            folder = root / "evaluation" / "job"
            folder.mkdir(parents=True)
            (folder / "plan.json").write_text(json.dumps(plan))
            originals = []
            for result in self.outcomes(plan):
                path = folder / f"outcome-{result['slot']:03}.json"
                path.write_text(json.dumps(result))
                originals.append((path, path.read_bytes()))
            config = root / "config.json"
            config.write_text(json.dumps({
                "evaluation_directory": str(root / "evaluation"),
                "period_path": str(period),
                "period_sha256": sha_file(period),
                "runtime_directory": str(runtime),
                "promotion_protocol": PROTOCOL,
            }))
            evaluator = Evaluator(config)
            with patch.object(evaluator, "launch") as launch, patch(
                "laya_cell.resident_evaluate.read_quotes",
                side_effect=AssertionError("idle archive scan"),
            ) as read:
                result = evaluator.step()
            launch.assert_not_called()
            read.assert_not_called()
            self.assertEqual(result["futile_pending_plans"], 1)
            self.assertGreaterEqual(
                result["new_prediction_launches_suppressed"], 1)
            self.assertFalse(result["source_scan_performed"])
            self.assertEqual(result["counts"], {"PENDING": 1})
            self.assertEqual(json.loads(
                (folder / "progress.json").read_text())["state"], "PENDING")
            for path, before in originals:
                self.assertEqual(path.read_bytes(), before)
            self.assertFalse((folder / "qualification.json").exists())


if __name__ == "__main__":
    unittest.main()
