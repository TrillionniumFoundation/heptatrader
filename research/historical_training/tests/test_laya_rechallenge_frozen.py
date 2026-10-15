"""Adversarial local fixtures for the all-censored-only prospective retry.

No broker, GPU, live chart or original test instance is touched.
"""
import copy
import fcntl
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from laya_cell.integrity import ContractError, digest, sha_file
from laya_cell.promotion import rule_for_trial
from laya_cell.promotion_power import PROTOCOL
from laya_cell.rechallenge_frozen import register, validate_closed
from laya_cell.resident_store import database, get_meta, put_meta, write_once


class RechallengeTests(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.addCleanup(self.t.cleanup)
        self.root = Path(self.t.name)
        self.eval = self.root / "evaluation"
        self.control = self.root / "control"
        self.evidence = self.root / "evidence"
        self.eval.mkdir(mode=0o700)
        self.control.mkdir(mode=0o700)
        self.evidence.mkdir(mode=0o700)
        (self.eval / "evaluator.lock").write_bytes(b"")
        (self.control / "scheduler.lock").write_bytes(b"")
        now = int(time.time() * 1000)
        self.period = "a" * 64
        self.parent_dir = self.root / "parent"
        self.candidate_dir = self.root / "candidate"
        self.parent_dir.mkdir(mode=0o700)
        self.candidate_dir.mkdir(mode=0o700)
        self.parent = self._candidate({
            "schema": "parent",
            "adapter_sha256": self._adapter(self.parent_dir, b"parent"),
        }, self.parent_dir)
        self.candidate = self._candidate({
            "schema": "heptatrader.laya-resident-candidate.v1",
            "parent_id": self.parent["candidate_id"],
            "adapter_sha256": self._adapter(self.candidate_dir, b"frozen adapter"),
            "status": "REQUIRES_INDEPENDENT_FUTURE_EVALUATION",
            "automatic_cutover": False,
            "pnl_reward_used": False,
            "broker_orders_sent": 0,
            "created_at_ms": now - 10000000,
            "trained_until_ms": now - 10100000,
        }, self.candidate_dir)
        self.job = "c" * 64
        self.original_dir = self.eval / self.job
        self.original_dir.mkdir(mode=0o700)
        self.old_rule = rule_for_trial(36, PROTOCOL)
        self.original = {
            "schema": "heptatrader.laya-resident-future-plan.v1",
            "job_id": self.job,
            "trial": 36,
            "rule": self.old_rule,
            "candidate_id": self.candidate["candidate_id"],
            "parent_id": self.parent["candidate_id"],
            "candidate_directory": str(self.candidate_dir),
            "parent_directory": str(self.parent_dir),
            "candidate_manifest_sha256": sha_file(
                self.candidate_dir / "candidate.json"),
            "scope": self.period,
            "start_ms": now - 9000000,
            "outcome_end_ms": now - 1000,
        }
        self.original["plan_id"] = digest(self.original)
        write_once(self.original_dir / "plan.json", self.original)
        self.original_hash = sha_file(self.original_dir / "plan.json")
        self.final = {
            "plan_id": self.original["plan_id"],
            "candidate_id": self.candidate["candidate_id"],
            "state": "REJECTED", "observed": 0,
            "censored": self.old_rule["slots"],
            "promotion_performed": False,
            "canonical_owner_admitted": False,
            "statistical_qualification": False,
            "reasons": ["EXCESS_CENSORED_WINDOWS"],
        }
        write_once(self.original_dir / "qualification.json", self.final)
        self.final_hash = sha_file(self.original_dir / "qualification.json")
        for i in range(self.old_rule["slots"]):
            write_once(self.original_dir / f"outcome-{i:03}.json", {
                "candidate_id": self.candidate["candidate_id"],
                "plan_id": self.original["plan_id"], "slot": i,
                "status": "CENSORED", "reason": "NO_PREDICTION_IN_FIXED_SLOT",
            })
        for trial in range(1, 36):
            folder = self.eval / ("prior-%03d" % trial)
            folder.mkdir(mode=0o700)
            write_once(folder / "plan.json", {"trial": trial})
            write_once(folder / "qualification.json", {"state": "REJECTED"})
        db = database(self.control / "scheduler.sqlite")
        with db:
            put_meta(db, "evaluation_trial", 36)
            db.execute(
                "INSERT INTO jobs VALUES (?,?,?,?,?,?)",
                (self.job, "fp", "COMPLETED", now - 10000000,
                 self.candidate["candidate_id"], __import__("json").dumps({
                     "output_directory": str(self.candidate_dir),
                     "parent_directory": str(self.parent_dir),
                     "parent_id": self.parent["candidate_id"],
                     "automatic_trading": False,
                 })),
            )
        db.close()
        self.config_path = self.root / "config.json"
        self.config = {
            "schema": "heptatrader.laya-resident-config.v1",
            "automatic_trading": False, "period_sha256": self.period,
            "future_source_read_profile": "ARCHIVE_VERIFIED_RECENT_ORIGINAL_V2",
            "promotion_protocol": PROTOCOL,
            "evaluation_directory": str(self.eval),
            "control_directory": str(self.control),
            "runtime_sha256": {
                k: "0" * 64 for k in (
                    "learn/laya_cell/resident_evaluate.py",
                    "learn/laya_cell/resident_predict.py",
                    "learn/laya_cell/promotion.py",
                    "learn/laya_cell/promotion_power.py",
                    "learn/laya_cell/resident_data.py",
                    "learn/laya_cell/shadow_source.py",
                    "learn/laya_cell/resident_recent_source.py",
                    "learn/laya_cell/resident_source_policy.py",
                )
            },
        }
        write_once(self.config_path, self.config)

    def _adapter(self, root, data):
        path = root / "adapter.safetensors"
        path.write_bytes(data)
        path.chmod(0o400)
        return sha_file(path)

    def _candidate(self, doc, root):
        doc = dict(doc)
        doc["candidate_id"] = digest(doc)
        write_once(root / "candidate.json", doc)
        return doc

    def do_register(self, status_ok=True, dry_run=False):
        with patch("laya_cell.rechallenge_frozen.config_check",
                   return_value=self.config), patch(
            "laya_cell.rechallenge_frozen.read_current_source",
            side_effect=None if status_ok else ContractError(
                "RECHALLENGE_SOURCE_NOT_CONTINUOUS"),
            return_value={"readiness_sha256": "f" * 64,
                          "recent_quote_count": 24,
                          "prefixes_digest": "e" * 64,
                          "source_asof_ms": int(time.time()*1000)},
        ):
            return register(
                self.config_path, self.original_dir / "plan.json",
                self.original_hash, self.final_hash,
                self.root / "source-status.json", self.evidence,
                dry_run=dry_run,
            )

    def test_exact_closed_zero_observation_retry_consumes_next_trial_once(self):
        before = [
            (x, x.read_bytes()) for x in self.original_dir.iterdir()
            if x.is_file()
        ]
        result = self.do_register()
        self.assertEqual(result["trial"], 37)
        self.assertTrue(result["same_frozen_candidate"])
        self.assertEqual(result["broker_orders_sent"], 0)
        self.assertTrue(Path(result["receipt"]).is_file())
        receipt = __import__("json").loads(Path(result["receipt"]).read_text())
        self.assertEqual(receipt["future_plan_sha256"], sha_file(Path(result["plan"])))
        self.assertEqual(receipt["alpha"], .05/(37*38))
        plan = __import__("json").loads(Path(result["plan"]).read_text())
        self.assertEqual(plan["trial"], 37)
        self.assertEqual(plan["rechallenge_of_plan_id"], self.original["plan_id"])
        self.assertEqual(plan["candidate_id"], self.candidate["candidate_id"])
        self.assertTrue(plan["same_frozen_candidate_no_training"])
        self.assertTrue(plan["no_refit"])
        self.assertFalse(plan["rule"]["trading_cutover_allowed"])
        self.assertEqual(plan["future_source_read_profile"],
                         "ARCHIVE_VERIFIED_RECENT_ORIGINAL_V2")
        self.assertEqual(plan["plan_id"], digest({
            k: v for k, v in plan.items() if k != "plan_id"}))
        self.assertEqual(self.original_hash, sha_file(self.original_dir/"plan.json"))
        for path, data in before:
            self.assertEqual(path.read_bytes(), data)
        db = database(self.control / "scheduler.sqlite")
        self.assertEqual(get_meta(db, "evaluation_trial"), 37)
        self.assertEqual(db.execute(
            "SELECT COUNT(*) FROM jobs").fetchone()[0], 1)
        db.close()
        with self.assertRaises(ContractError):
            self.do_register()

    def test_readonly_preflight_does_not_create_trial_or_consume_alpha(self):
        result = self.do_register(dry_run=True)
        self.assertEqual(result["state"], "RECHALLENGE_PREFLIGHT_ONLY_NO_WRITE")
        self.assertEqual(result["next_trial_proposed"], 37)
        self.assertFalse(result["new_plan_written"])
        self.assertFalse(result["model_loaded"])
        db = database(self.control / "scheduler.sqlite")
        self.assertEqual(get_meta(db, "evaluation_trial"), 36)
        db.close()
        self.assertEqual(len(list(self.eval.glob("*/plan.json"))), 36)
        self.assertEqual(list(self.evidence.iterdir()), [])

    def test_original_final_hash_cannot_be_substituted(self):
        with patch("laya_cell.rechallenge_frozen.config_check",
                   return_value=self.config):
            with self.assertRaisesRegex(ContractError, "FROZEN_SHA_MISMATCH"):
                register(self.config_path, self.original_dir / "plan.json",
                         self.original_hash, "0" * 64,
                         self.root / "source-status.json", self.evidence)

    def test_wrong_or_missing_one_of_ninety_original_slots_is_refused(self):
        target = self.original_dir / "outcome-008.json"
        original_bytes = target.read_bytes()
        target.unlink()
        with self.assertRaisesRegex(ContractError, "ROSTER_INCOMPLETE"):
            validate_closed(self.original_dir / "plan.json", self.original_hash,
                            self.final_hash, self.eval)
        target.write_bytes(original_bytes)
        target.chmod(0o600)

    def test_nonzero_original_observed_is_refused(self):
        final = copy.deepcopy(self.final)
        final["observed"] = 1
        final["censored"] = 89
        (self.original_dir / "qualification.json").write_bytes(
            __import__("json").dumps(final).encode())
        with self.assertRaisesRegex(ContractError, "FULLY_CENSORED_TERMINAL"):
            validate_closed(self.original_dir / "plan.json", self.original_hash,
                            sha_file(self.original_dir/"qualification.json"),
                            self.eval)

    def test_missing_source_health_fails_without_mutation(self):
        with self.assertRaisesRegex(ContractError, "SOURCE_NOT_CONTINUOUS"):
            self.do_register(status_ok=False)
        db = database(self.control / "scheduler.sqlite")
        self.assertEqual(get_meta(db, "evaluation_trial"), 36)
        db.close()

    def test_existing_scheduler_lease_prevents_race(self):
        with (self.control / "scheduler.lock").open("rb") as fd:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(BlockingIOError):
                self.do_register()


if __name__ == "__main__":
    unittest.main()
