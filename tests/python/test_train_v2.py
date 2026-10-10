"""Train V2 contract, adversarial timestamp and read-only event orchestration."""
from __future__ import annotations

import copy
import sqlite3
import tempfile
import unittest
from pathlib import Path

from train_v2.adapters import make_spec
from train_v2.contracts import (
    CAUSAL, EXPLORATORY, LIVE, REVISED, OBS_SCHEMA,
    ContractError, compile_dataset, compile_example, digest,
    feature_spec_id, validate_observation, validate_spec,
)
from train_v2.orchestrator import (
    SOURCE_EVENT, OUTCOME_EVENT, CORRECTION_EVENT,
    database, evidence_event, ingest, register_plan,
)


BASE = 1800000000000
PERIOD = "a" * 64


def source(index, *, source_kind=LIVE, period=PERIOD):
    t = BASE + index * 10000
    return {
        "schema": OBS_SCHEMA,
        "instrument": "EUR.USD",
        "period_sha256": period,
        "source_kind": source_kind,
        "source_id": digest({"quote": index}),
        "source_sha256": digest({"receipt": index}),
        "event_at_ms": t,
        "available_at_ms": t,
        "valid_until_ms": t,
        "bid": str(1.1 + index / 100000),
        "ask": str(1.1001 + index / 100000),
    }


class TrainV2ContractTests(unittest.TestCase):
    def setUp(self):
        self.spec = make_spec(PERIOD, "PAPER_BEHAVIOR_MODEL_V1")
        self.recs = [source(i) for i in range(45)]
        self.when = self.recs[5]["event_at_ms"]
        self.due = self.when + self.spec["horizon_ms"]

    def test_pit_features_stable_after_label_matures(self):
        before = compile_example(self.spec, self.recs, self.when, self.when)
        after = compile_example(self.spec, self.recs, self.when, self.due)
        self.assertEqual(before["label_status"], "PENDING_OUTCOME")
        self.assertEqual(after["label_status"], "OBSERVED")
        self.assertEqual(before["feature_id"], after["feature_id"])
        self.assertEqual(before["features"], after["features"])
        self.assertFalse(before["candidate_training_eligible"])
        self.assertTrue(after["candidate_training_eligible"])
        self.assertFalse(after["economic_return_attested"])
        self.assertFalse(after["trade_authority"])

    def test_future_prices_cannot_enter_predecision_features(self):
        original = compile_example(self.spec, self.recs, self.when, self.due)
        manipulated = copy.deepcopy(self.recs)
        for i in range(6, len(manipulated)):
            manipulated[i]["bid"] = str(1.2 + i / 100000)
            manipulated[i]["ask"] = str(1.2001 + i / 100000)
        changed = compile_example(self.spec, manipulated, self.when, self.due)
        self.assertEqual(original["feature_id"], changed["feature_id"])
        self.assertNotEqual(original["label_return_bps"], changed["label_return_bps"])

    def test_delayed_predecision_event_is_excluded(self):
        baseline = compile_example(self.spec, self.recs, self.when, self.when)
        delayed = source(99)
        delayed["event_at_ms"] = self.when - 4000
        delayed["available_at_ms"] = self.when + 1
        delayed["valid_until_ms"] = self.when + 1
        assert delayed["event_at_ms"] < self.when
        observed = compile_example(self.spec, self.recs + [delayed], self.when, self.when)
        self.assertEqual(observed["feature_id"], baseline["feature_id"])

    def test_missing_input_is_not_zero_feature(self):
        with self.assertRaisesRegex(ContractError, "FEATURE_HISTORY_NOT_AVAILABLE"):
            compile_example(self.spec, self.recs[:5], self.when, self.due)

    def test_live_expired_quote_refused(self):
        with self.assertRaisesRegex(ContractError, "FEATURE_QUOTE_EXPIRED"):
            compile_example(self.spec, self.recs, self.when + 1, self.due)

    def test_cross_session_gap_refused(self):
        truncated = self.recs[:4] + self.recs[7:]
        with self.assertRaisesRegex(ContractError, "FEATURE_INPUT_PATH_GAP"):
            compile_example(self.spec, truncated, self.recs[8]["event_at_ms"],
                            self.recs[38]["event_at_ms"])

    def test_missed_outcome_grace_stays_pending_then_censors(self):
        subset = self.recs[:35]
        before = compile_example(self.spec, subset, self.when, self.due + 4999)
        after = compile_example(self.spec, subset, self.when, self.due + 5000)
        self.assertEqual(before["label_status"], "PENDING_OUTCOME")
        self.assertEqual(after["label_status"], "CENSORED_MISSING_OR_LATE_OUTCOME")
        self.assertIsNone(after["label_return_bps"])

    def test_no_synthetic_outcome_across_missing_path(self):
        truncated = self.recs[:12] + self.recs[30:]
        item = compile_example(self.spec, truncated, self.when, self.due)
        self.assertEqual(item["label_status"], "CENSORED_OUTCOME_PATH_GAP")
        self.assertIsNone(item["label_return_bps"])

    def test_revised_historical_cannot_claim_prospective_eligibility(self):
        history = make_spec(PERIOD, "PAPER_BEHAVIOR_MODEL_V1", historical=True)
        historical = [source(i, source_kind=REVISED) for i in range(45)]
        for row in historical:
            row["available_at_ms"] = BASE + 800000
            row["valid_until_ms"] = BASE + 800000
        result = compile_example(
            history, historical, self.when, BASE + 800000)
        self.assertEqual(result["label_status"], "REVISED_RESEARCH_LABEL")
        self.assertFalse(result["locally_point_in_time"])
        self.assertFalse(result["candidate_training_eligible"])
        self.assertTrue(result["revised_historical"])
        with self.assertRaisesRegex(ContractError, "OBSERVATION_CROSS_SCOPE"):
            compile_example(self.spec, historical, self.when, BASE + 800000)

    def test_future_availability_does_not_become_live_quote(self):
        changed = copy.deepcopy(self.recs)
        for row in changed:
            row["available_at_ms"] = row["event_at_ms"] + 4000
            row["valid_until_ms"] = row["available_at_ms"]
        with self.assertRaisesRegex(ContractError, "FEATURE_HISTORY_NOT_AVAILABLE"):
            compile_example(self.spec, changed[:6], self.when, self.when)

    def test_input_scope_and_identity_rejection(self):
        crossed = copy.deepcopy(self.recs)
        crossed[0]["bid"] = "1.4"
        with self.assertRaisesRegex(ContractError, "OBSERVATION_CROSSED_MARKET"):
            compile_example(self.spec, crossed, self.when, self.due)
        altered = copy.deepcopy(self.recs)
        altered[0]["source_id"] = self.recs[1]["source_id"]
        with self.assertRaisesRegex(ContractError, "OBSERVATION_ID_CONFLICT"):
            compile_example(self.spec, altered, self.when, self.due)
        different = copy.deepcopy(self.recs)
        different[0]["period_sha256"] = "b"*64
        with self.assertRaisesRegex(ContractError, "OBSERVATION_CROSS_SCOPE"):
            compile_example(self.spec, different, self.when, self.due)

    def test_duplicate_timestamp_conflicting_quote_refused(self):
        changed = copy.deepcopy(self.recs[0])
        changed["source_id"] = digest({"additional": 1})
        changed["bid"] = "1.099"
        with self.assertRaisesRegex(ContractError, "OBSERVATION_TIMESTAMP_CONFLICT"):
            compile_example(self.spec, self.recs + [changed], self.when, self.due)

    def test_invalid_numeric_boolean_and_future_clock_refused(self):
        for bad in ("NaN", "Infinity", False, 0, -1):
            changed = copy.deepcopy(self.recs[0])
            changed["bid"] = bad
            with self.assertRaises(ContractError):
                validate_observation(changed, self.spec)
        later = copy.deepcopy(self.recs[0])
        later["available_at_ms"] = later["event_at_ms"] - 1
        with self.assertRaisesRegex(ContractError, "OBSERVATION_CLOCK_OR_EXPIRY"):
            validate_observation(later, self.spec)

    def test_schema_and_effect_authority_refused(self):
        for key in ("trade_authority", "promotion_allowed", "actor_update"):
            bad = dict(self.spec)
            bad[key] = True
            with self.assertRaisesRegex(ContractError, "FEATURE_HAS_NO_EFFECT_AUTHORITY"):
                validate_spec(bad)
        mismatched = dict(self.spec)
        mismatched["mode"] = EXPLORATORY
        with self.assertRaisesRegex(ContractError, "FEATURE_SOURCE_MODE_MISMATCH"):
            validate_spec(mismatched)

    def test_dataset_order_and_determinism(self):
        decisions = [self.recs[5]["event_at_ms"], self.recs[6]["event_at_ms"]]
        first = compile_dataset(self.spec, self.recs, decisions, self.recs[36]["event_at_ms"])
        second = compile_dataset(self.spec, self.recs, decisions, self.recs[36]["event_at_ms"])
        self.assertEqual(first, second)
        self.assertEqual(first["feature_spec_id"], feature_spec_id(self.spec))
        self.assertFalse(first["promotion_allowed"])
        with self.assertRaisesRegex(ContractError, "DATASET_DECISION_ORDER"):
            compile_dataset(self.spec, self.recs, list(reversed(decisions)),
                            self.recs[36]["event_at_ms"])
        with self.assertRaisesRegex(ContractError, "DATASET_DECISION_ORDER"):
            compile_dataset(self.spec, self.recs, [decisions[0]]*2,
                            self.recs[36]["event_at_ms"])


class EventOrchestratorTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.db = Path(self.td.name) / "private" / "train.sqlite"
        self.spec = make_spec(PERIOD, "PAPER_BEHAVIOR_MODEL_V1")
        self.quotes = [source(i) for i in range(45)]
        self.d1 = self.quotes[5]["event_at_ms"]
        self.d2 = self.quotes[6]["event_at_ms"]
        self.plan = register_plan(spec=self.spec, registered_at_ms=BASE - 10000,
                                  first_decision_ms=self.d1, last_decision_ms=self.d2,
                                  minimum_mature_rows=2)

    def example(self, decision, evaluated_at_ms):
        ds = compile_dataset(self.spec, self.quotes, [decision], evaluated_at_ms)
        return ds, ds["examples"][0]

    def event(self, kind, ds, sample, at, witness):
        return evidence_event(self.plan, sample, kind=kind, at_ms=at,
                              evidence_sha256=witness)

    def submit_input(self, decision):
        ds, sample = self.example(decision, decision)
        event = self.event(SOURCE_EVENT, ds, sample, decision,
                           sample["features"]["input_receipts"][-1]["source_sha256"])
        return ingest(self.db, self.plan, ds, event), sample, event

    def submit_outcome(self, decision):
        t = decision + self.spec["horizon_ms"]
        ds, sample = self.example(decision, t)
        event = self.event(OUTCOME_EVENT, ds, sample, t,
                           sample["label_receipt"]["source_sha256"])
        return ingest(self.db, self.plan, ds, event), sample, event

    def test_source_without_outcome_never_trains(self):
        result, _, _ = self.submit_input(self.d1)
        self.assertEqual(result["state"], "INPUT_RECORDED_SHADOW_ONLY")
        self.assertFalse(result["automatic_training"])
        self.assertEqual(result["broker_orders_sent"], 0)
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM review_batches").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT state FROM samples").fetchone()[0],
                             "AWAITING_OUTCOME")

    def test_outcome_requires_original_early_feature(self):
        with self.assertRaisesRegex(ContractError, "TRAIN_ORIGINAL_CAUSAL_INPUT_NOT_SEALED"):
            self.submit_outcome(self.d1)

    def test_duplicate_event_is_idempotent(self):
        _, _, event = self.submit_input(self.d1)
        ds, _ = self.example(self.d1, self.d1)
        replay = ingest(self.db, self.plan, ds, event)
        self.assertEqual(replay["state"], "DUPLICATE_NO_EFFECT")
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM events").fetchone()[0], 1)

    def test_mature_outcomes_trigger_only_review_after_minimum(self):
        self.submit_input(self.d1)
        self.submit_input(self.d2)
        first, _, _ = self.submit_outcome(self.d1)
        second, _, _ = self.submit_outcome(self.d2)
        self.assertEqual(first["state"], "MATURED_BELOW_SAMPLE_FLOOR")
        self.assertEqual(second["state"], "CANDIDATE_REVIEW_ONLY")
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute(
                "SELECT state FROM review_batches").fetchone()[0],
                "REVIEW_ONLY_NO_MODEL_DISPATCH")
            self.assertEqual(db.execute("SELECT count(*) FROM events").fetchone()[0], 4)

    def test_correction_revokes_all_current_review_batches(self):
        self.test_mature_outcomes_trigger_only_review_after_minimum()
        ds, sample = self.example(self.d1, self.d1 + self.spec["horizon_ms"])
        event = self.event(CORRECTION_EVENT, ds, sample, self.d1+400000,
                           digest({"correction": "immutable-original-witness"}))
        result = ingest(self.db, self.plan, ds, event)
        self.assertEqual(result["state"], "CORRECTION_REVOKED_ALL_REVIEW_BATCHES")
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute(
                "SELECT state FROM review_batches").fetchone()[0],
                "REVOKED_PENDING_INDEPENDENT_REVIEW")
        # Original replay remains idempotent but can NEVER un-revoke the row.
        repeated, _, _ = self.submit_outcome(self.d1)
        self.assertEqual(repeated["state"], "DUPLICATE_NO_EFFECT")
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute(
                "SELECT state FROM samples WHERE feature_id=?", (sample["feature_id"],)
            ).fetchone()[0], "REVOKED")
        # A newly backdated/reworded outcome event is refused as well.
        new_event = self.event(OUTCOME_EVENT, ds, sample, self.d1 + 400001,
                               sample["label_receipt"]["source_sha256"])
        with self.assertRaisesRegex(ContractError, "TRAIN_ORIGINAL_CAUSAL_INPUT_NOT_SEALED"):
            ingest(self.db, self.plan, ds, new_event)

    def test_event_hash_tampering_refused(self):
        ds, sample = self.example(self.d1, self.d1)
        event = self.event(SOURCE_EVENT, ds, sample, self.d1,
                           sample["features"]["input_receipts"][-1]["source_sha256"])
        event["at_ms"] += 1
        with self.assertRaisesRegex(ContractError, "TRAIN_EVENT_IDENTITY"):
            ingest(self.db, self.plan, ds, event)

    def test_late_source_event_cannot_backdate(self):
        ds, sample = self.example(self.d1, self.d1)
        event = self.event(SOURCE_EVENT, ds, sample, self.d1 + 1,
                           sample["features"]["input_receipts"][-1]["source_sha256"])
        with self.assertRaisesRegex(ContractError, "TRAIN_SOURCE_NOT_EARLY_ENOUGH"):
            ingest(self.db, self.plan, ds, event)

    def test_wrong_receipts_and_plan_refused(self):
        ds, sample = self.example(self.d1, self.d1)
        event = self.event(SOURCE_EVENT, ds, sample, self.d1, digest({"foreign": 1}))
        with self.assertRaisesRegex(ContractError, "TRAIN_SOURCE_RECEIPT_MISMATCH"):
            ingest(self.db, self.plan, ds, event)
        amended = dict(self.plan)
        amended["registered_at_ms"] -= 1
        with self.assertRaisesRegex(ContractError, "TRAIN_PLAN_NOT_RESEARCH_ONLY"):
            ingest(self.db, amended, ds, event)

    def test_historical_dataset_cannot_create_research_job(self):
        hist_spec = make_spec(PERIOD, "PAPER_BEHAVIOR_MODEL_V1", historical=True)
        historic = [source(i, source_kind=REVISED) for i in range(45)]
        for item in historic:
            item["available_at_ms"] = BASE + 800000
            item["valid_until_ms"] = BASE + 800000
        ds = compile_dataset(hist_spec, historic, [self.d1], BASE + 800000)
        sample = ds["examples"][0]
        event = self.event(SOURCE_EVENT, ds, sample, self.d1,
                           sample["features"]["input_receipts"][-1]["source_sha256"])
        with self.assertRaisesRegex(ContractError, "TRAIN_DATASET_SCOPE"):
            ingest(self.db, self.plan, ds, event)

    def test_not_matured_outcome_cannot_be_admitted(self):
        self.submit_input(self.d1)
        ds, sample = self.example(self.d1, self.d1)
        event = self.event(OUTCOME_EVENT, ds, sample,
                           self.d1 + self.spec["horizon_ms"],
                           sample["features"]["input_receipts"][-1]["source_sha256"])
        with self.assertRaisesRegex(ContractError, "TRAIN_LABEL_NOT_MATURE_OR_VERIFIED"):
            ingest(self.db, self.plan, ds, event)

    def test_replay_state_persists_after_database_reopen(self):
        self.submit_input(self.d1)
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM samples").fetchone()[0], 1)
        output, _, _ = self.submit_outcome(self.d1)
        self.assertEqual(output["state"], "MATURED_BELOW_SAMPLE_FLOOR")

    def test_rehashed_future_input_still_fails_causal_admission(self):
        ds, sample = self.example(self.d1, self.d1)
        forged = copy.deepcopy(ds)
        target = forged["examples"][0]
        target["features"]["input_receipts"][-1]["available_at_ms"] = self.d1 + 1
        target["feature_id"] = digest(target["features"])
        forged["dataset_id"] = digest({k: v for k, v in forged.items()
                                        if k != "dataset_id"})
        event = self.event(
            SOURCE_EVENT, forged, target, self.d1,
            target["features"]["input_receipts"][-1]["source_sha256"])
        with self.assertRaisesRegex(ContractError, "TRAIN_INPUT_WITNESS_NOT_CAUSAL"):
            ingest(self.db, self.plan, forged, event)

    def test_rehashed_undelivered_label_still_fails_admission(self):
        ds, sample = self.example(self.d1, self.d1 + self.spec["horizon_ms"])
        forged = copy.deepcopy(ds)
        target = forged["examples"][0]
        target["label_receipt"]["available_at_ms"] = self.d1 + 900000
        forged["dataset_id"] = digest({k: v for k, v in forged.items()
                                        if k != "dataset_id"})
        event = self.event(
            OUTCOME_EVENT, forged, target, self.d1 + 900000,
            target["label_receipt"]["source_sha256"])
        with self.assertRaisesRegex(ContractError, "TRAIN_LABEL_WITNESS_NOT_MATURE"):
            ingest(self.db, self.plan, forged, event)

    def test_rehashed_earlier_label_deadline_is_not_accepted(self):
        ds, sample = self.example(self.d1, self.d1)
        forged = copy.deepcopy(ds)
        target = forged["examples"][0]
        target["label_due_ms"] -= 10000
        forged["dataset_id"] = digest({k: v for k, v in forged.items()
                                        if k != "dataset_id"})
        event = self.event(
            SOURCE_EVENT, forged, target, self.d1,
            target["features"]["input_receipts"][-1]["source_sha256"])
        with self.assertRaisesRegex(ContractError, "TRAIN_LABEL_DUE_CHANGED"):
            ingest(self.db, self.plan, forged, event)

    def test_cli_one_event_then_duplicate_without_polling(self):
        import json
        import os
        import subprocess
        import sys
        ds, sample = self.example(self.d1, self.d1)
        event = self.event(SOURCE_EVENT, ds, sample, self.d1,
                           sample["features"]["input_receipts"][-1]["source_sha256"])
        files = {}
        for kind, obj in (("plan", self.plan), ("dataset", ds), ("event", event)):
            f = Path(self.td.name) / (kind + ".json")
            f.write_text(json.dumps(obj, sort_keys=True))
            f.chmod(0o600)
            files[kind] = f
        args = [sys.executable, "-B", "-m", "train_v2.event_ingest",
                "--plan", str(files["plan"]), "--dataset", str(files["dataset"]),
                "--event", str(files["event"]), "--ledger", str(self.db)]
        first = subprocess.run(args, capture_output=True, text=True,
                               env=dict(os.environ), timeout=10, check=False)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertIn("INPUT_RECORDED_SHADOW_ONLY", first.stdout)
        duplicate = subprocess.run(args, capture_output=True, text=True,
                                   env=dict(os.environ), timeout=10, check=False)
        self.assertEqual(duplicate.returncode, 0, duplicate.stderr)
        self.assertIn("DUPLICATE_NO_EFFECT", duplicate.stdout)

    def test_two_independent_processes_cannot_duplicate_same_input(self):
        import json
        import os
        import subprocess
        import sys
        ds, sample = self.example(self.d1, self.d1)
        event = self.event(SOURCE_EVENT, ds, sample, self.d1,
                           sample["features"]["input_receipts"][-1]["source_sha256"])
        files = {}
        for kind, obj in (("plan", self.plan), ("dataset", ds), ("event", event)):
            f = Path(self.td.name) / (kind + ".json")
            f.write_text(json.dumps(obj, sort_keys=True))
            f.chmod(0o600)
            files[kind] = f
        args = [sys.executable, "-B", "-m", "train_v2.event_ingest",
                "--plan", str(files["plan"]), "--dataset", str(files["dataset"]),
                "--event", str(files["event"]), "--ledger", str(self.db)]
        children = [subprocess.Popen(args, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, text=True,
                                     env=dict(os.environ)) for _ in range(2)]
        results = [child.communicate(timeout=10) for child in children]
        self.assertTrue(all(child.returncode == 0 for child in children), results)
        states = [json.loads(out)["state"] for out, _ in results]
        self.assertCountEqual(states, ["INPUT_RECORDED_SHADOW_ONLY", "DUPLICATE_NO_EFFECT"])
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM events").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT count(*) FROM samples").fetchone()[0], 1)

    def test_nonprivate_storage_refused(self):
        bad = Path(self.td.name) / "public"
        bad.mkdir(mode=0o755)
        bad.chmod(0o755)
        with self.assertRaisesRegex(ContractError, "TRAIN_STORAGE_NOT_PRIVATE"):
            database(bad / "test.sqlite")
