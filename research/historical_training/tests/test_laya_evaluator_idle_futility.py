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


    def test_same_tick_third_censor_blocks_later_worker(self):
        """A newly terminalized missed slot must block the next GPU launch."""
        with tempfile.TemporaryDirectory() as dirname:
            root = Path(dirname)
            period = root / "period.json"
            period.write_text('{"execution_domain":"PAPER"}')
            runtime = root / "runtime"
            runtime.mkdir()
            code = runtime / "pinned.py"
            code.write_text("# fixture\\n")
            plan = self.plan()
            rule = plan["rule"]
            # Slot 2 is expired, while slot 3 is inside its 90 s prewarm.
            plan.update(
                scope=sha_file(period),
                start_ms=int(time.time() * 1000) + 30000
                - 3 * rule["slot_spacing_ms"],
                evaluation_runtime_sha256={"pinned.py": sha_file(code)},
            )
            plan.pop("plan_id")
            plan["plan_id"] = digest(plan)
            folder = root / "evaluation" / "job"
            folder.mkdir(parents=True)
            (folder / "plan.json").write_text(json.dumps(plan))
            for result in self.outcomes(plan, 2):
                (folder / f"outcome-{result['slot']:03}.json").write_text(
                    json.dumps(result))
            config = root / "config.json"
            config.write_text(json.dumps({
                "evaluation_directory": str(root / "evaluation"),
                "period_path": str(period),
                "period_sha256": sha_file(period),
                "runtime_directory": str(runtime),
                "promotion_protocol": PROTOCOL,
            }))
            evaluator = Evaluator(config)
            with patch.object(evaluator, "launch") as launch:
                status = evaluator.step()
            launch.assert_not_called()
            self.assertEqual(status["counts"], {"PENDING": 1})
            self.assertEqual(status["futile_pending_plans"], 1)
            self.assertEqual(status["censored"], 3)
            self.assertGreaterEqual(status["new_prediction_launches_suppressed"], 1)
            self.assertFalse((folder / "qualification.json").exists())

    def test_one_verified_snapshot_clock_for_all_mature_outcomes(self):
        """Measured as-of may not advance beyond the quotes already read."""
        with tempfile.TemporaryDirectory() as dirname:
            root = Path(dirname)
            period = root / "period.json"
            period.write_text('{"execution_domain":"PAPER"}')
            runtime = root / "runtime"
            runtime.mkdir()
            code = runtime / "pinned.py"
            code.write_text("# fixture\\n")
            plan = self.plan()
            rule = plan["rule"]
            now_ms = int(time.time() * 1000)
            plan.update(
                scope=sha_file(period),
                start_ms=now_ms + rule["slot_spacing_ms"] * 2,
                evaluation_runtime_sha256={"pinned.py": sha_file(code)},
            )
            plan.pop("plan_id")
            plan["plan_id"] = digest(plan)
            folder = root / "evaluation" / "job"
            folder.mkdir(parents=True)
            (folder / "plan.json").write_text(json.dumps(plan))
            for slot in range(2):
                (folder / f"prediction-{slot:03}.json").write_text(
                    json.dumps({"anchor_ms": now_ms - 600000, "slot": slot}))
            config = root / "config.json"
            config.write_text(json.dumps({
                "evaluation_directory": str(root / "evaluation"),
                "period_path": str(period),
                "period_sha256": sha_file(period),
                "runtime_directory": str(runtime),
                "promotion_protocol": PROTOCOL,
            }))
            evaluator = Evaluator(config)
            reads = []
            measurements = []
            def read_quotes(config, asof_ms):
                reads.append(asof_ms)
                time.sleep(.02)  # force wall-clock advance after evidence cut
                return [], []
            def measure(plan, slot, prediction, quotes, asof_ms):
                measurements.append((slot, asof_ms))
                self.assertEqual(asof_ms, reads[0])
                return None
            with patch("laya_cell.resident_evaluate.read_quotes",
                       side_effect=read_quotes), patch(
                "laya_cell.resident_evaluate.measure", side_effect=measure
            ):
                status = evaluator.step()
            self.assertEqual(len(reads), 1)
            self.assertEqual(len(measurements), 2)
            self.assertTrue(status["source_scan_performed"])
            self.assertEqual(status["counts"], {"PENDING": 1})
            self.assertEqual(status["observed"], 0)
            self.assertFalse((folder / "qualification.json").exists())


if __name__ == "__main__":
    unittest.main()
