"""Protocol tests with synthetic clocks/quotes; no model or broker is contacted."""
from __future__ import annotations

import copy
import errno
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from research.model_replacement import prospective as p


class FakeClock:
    def __init__(self, value=1700000000000):
        self.value = value

    def __call__(self):
        return self.value

    def advance(self, delta):
        self.value += delta


class ProcessCrash(BaseException):
    pass


def pin_json(path, body):
    raw = p.canonical(body) + b"\n"
    p.write_new(path, raw)
    return {"path": str(path), "sha256": p.sha_bytes(raw)}


class FakeSource:
    def __init__(self, root, plan):
        self.path = root / "source-proof.fixture"
        p.write_new(self.path, b"synthetic quote backend for protocol tests\n")
        raw = self.path.read_bytes()
        self.prefix = {"path": str(self.path), "bytes": len(raw), "sha256": p.sha_bytes(raw),
                       "chain_head": "d" * 64, "ended": False, "uncommitted_tail_bytes": 0}
        anchor = p.slot_time(plan, 0) - 1000
        self.quotes = []
        for index, t in enumerate(range(anchor - 240000, anchor + 1300000, 1000)):
            bid = 1.1 + index * .00000001
            q = {"t": t, "known_at_ms": t + 50, "bid": bid, "ask": bid + .00002,
                 "mid": float((p.Decimal(str(bid)) + p.Decimal(str(bid + .00002))) / 2)}
            q["receipt"] = p.digest(q)
            self.quotes.append(q)
        self.calls = 0

    def poll(self, now_ms, since_ms, deadline_ms=None):
        self.calls += 1
        return {"quotes": [copy.deepcopy(q) for q in self.quotes if q["t"] >= since_ms and q["known_at_ms"] <= now_ms],
                "prefixes": {str(self.path): copy.deepcopy(self.prefix)},
                "receipt_paths": {q["receipt"]: str(self.path) for q in self.quotes}, "read_at_ms": now_ms}


class FakePredictor:
    def __init__(self, arm, clock, calls, behavior=None):
        self.arm, self.clock, self.calls, self.behavior = arm, clock, calls, behavior

    def predict(self, rows):
        self.calls.append((self.arm["name"], copy.deepcopy(rows)))
        if self.behavior:
            self.behavior()
        return {"model_state_id": self.arm["model_state_id"], "created_at_ms": self.clock(),
                "sample_ids": [r["sample_id"] for r in rows], "raw_probabilities": [[1/7] * 7],
                "probabilities": [[1/7] * 7], "prediction_seconds": .001, "trade_permission": False}


class ProtocolTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.clock = FakeClock()
        self.identity = lambda: {"runtime_fixture": "fixed"}
        dataset = {"instrument": "EUR.USD", "horizon_sec": 300, "rows": []}
        dataset["dataset_id"] = p.digest(dataset)
        split = {"dataset_id": dataset["dataset_id"], "partitions": {}}
        split["split_id"] = p.digest(split)
        self.dataset = pin_json(self.root / "dataset.json", dataset)
        self.split = pin_json(self.root / "split.json", split)
        self.model_pins = []
        for i, name in enumerate(("prior", "chronos_frozen")):
            state = {"schema": "heptatrader.model-replacement-state.v1", "instrument": "EUR.USD",
                     "horizon_sec": 300, "class_order": list(range(7)), "bin_edges_bps": list(p.BIN_EDGES_BPS),
                     "dataset_id": dataset["dataset_id"], "split_id": split["split_id"], "backbone": None,
                     "training_cutoff_ms": self.clock() - 4000, "validation_cutoff_ms": self.clock() - 3000,
                     "calibration_cutoff_ms": self.clock() - 2000, "usable_after_ms": self.clock() - 1999,
                     "created_at_ms": self.clock() - 1000 + i, "trade_permission": False}
            state["model_state_id"] = p.digest(state)
            self.model_pins.append({"name": name, "model_state": pin_json(self.root / (name + ".json"), state)})
        self.feed = self.root / "feed"
        self.feed.mkdir(mode=0o700)
        self.config = {"feed_root": str(self.feed), "period_sha256": "a" * 64,
                       "dataset": self.dataset, "split": self.split, "arms": self.model_pins,
                       "start_at_ms": self.clock() + 60000, "slot_count": 1, "max_censored": 0}
        self.observers = []

    def tearDown(self):
        for observer in self.observers:
            observer.journal.close()
        self.temp.cleanup()

    def plan(self, **overrides):
        config = {**self.config, **overrides}
        self.plan_path = self.root / "plan.json"
        self.receipt = p.seal_plan(config, self.plan_path, now=self.clock, identity=self.identity)
        return p.load_plan(self.plan_path, self.receipt["sha256"], identity=self.identity)

    def observer(self, plan, source=None, behaviors=None):
        source = source or FakeSource(self.root, plan)
        loads, calls = [], []
        by_path = {a["model_state"]["path"]: a for a in plan["arms"]}
        def factory(path, sha):
            arm = by_path[path]
            loads.append(arm["name"])
            self.assertEqual(sha, arm["model_state"]["sha256"])
            return FakePredictor(arm, self.clock, calls, (behaviors or {}).get(arm["name"]))
        observer = p.Observer(plan, self.receipt["sha256"], self.root / "state", clock=self.clock,
                              source=source, predictor_factory=factory,
                              row_factory=lambda sample, **_: sample)
        self.observers.append(observer)
        return observer, source, loads, calls

    def test_seal_is_future_fixed_and_binds_all_model_data_runtime_pins(self):
        plan = self.plan(slot_count=101, max_censored=2)
        self.assertEqual(plan["coverage_rule"], {"min_observed": 99, "max_censored": 2,
                                               "denominator": "ALL_FIXED_SLOTS_PER_ARM"})
        self.assertEqual(plan["first_slot_at_ms"], plan["start_at_ms"] + 5000)
        self.assertFalse(plan["trade_permission"])
        self.assertFalse(plan["better_than_laya_claim_allowed"])
        with self.assertRaisesRegex(ValueError, "FROZEN_RUNTIME_CHANGED"):
            p.load_plan(self.plan_path, self.receipt["sha256"], identity=lambda: {"runtime_fixture": "changed"})
        with self.assertRaisesRegex(ValueError, "PINNED_FILE_CHANGED"):
            p.load_plan(self.plan_path, "b" * 64, identity=self.identity)

    def test_historical_start_and_unknown_config_fail_before_seal(self):
        for config, error in (({**self.config, "start_at_ms": self.clock()}, "FUTURE_START"),
                              ({**self.config, "silent_relaxation": True}, "UNKNOWN_PLAN_CONFIG")):
            with self.assertRaisesRegex(ValueError, error):
                p.seal_plan(config, self.root / "bad-plan.json", now=self.clock, identity=self.identity)
            self.assertFalse((self.root / "bad-plan.json").exists())

    def test_retry_policy_is_sealed_only_into_new_plans(self):
        plan = self.plan()
        self.assertEqual(plan["outcome_policy"]["source_error_retry_interval_ms"], 5000)
        self.assertEqual(plan["outcome_policy"]["source_error_grace_ms"], 60000)
        legacy = copy.deepcopy(plan)
        legacy["outcome_policy"].pop("source_error_retry_interval_ms")
        legacy["outcome_policy"].pop("source_error_grace_ms")
        legacy["plan_id"] = p.digest({k: v for k, v in legacy.items() if k != "plan_id"})
        legacy_path = self.root / "legacy-plan.json"
        pin = pin_json(legacy_path, legacy)
        pin_json(Path(str(legacy_path) + ".seal.json"), {"plan_id": legacy["plan_id"],
            "plan_sha256": pin["sha256"], "plan_durable_at_ms": legacy["created_at_ms"]})
        with self.assertRaisesRegex(ValueError, "PLAN_POLICY"):
            p.load_plan(legacy_path, pin["sha256"], identity=self.identity)

    def test_complete_plan_shared_original_input_real_label_and_resident_models(self):
        plan = self.plan()
        observer, source, loads, calls = self.observer(plan)
        self.assertEqual(loads, ["prior", "chronos_frozen"])
        self.assertFalse(observer.tick())
        self.assertEqual(source.calls, 0)
        self.clock.value = p.slot_time(plan, 0)
        self.assertFalse(observer.tick())
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][1], calls[1][1])
        sample = calls[0][1][0]
        self.assertEqual(sample["anchor_ms"], p.slot_time(plan, 0) - 1000)
        self.assertEqual(sample["known_at_ms"], p.slot_time(plan, 0))
        self.assertEqual(len(sample["input_snapshot"]["quotes"]), 24)
        self.assertEqual(sample["targets"], [None, None])
        self.assertIsNone(observer.slots[0]["outcome"])
        summary = p.summarize(plan, observer.journal.events)
        self.assertFalse(summary["arms"]["prior"]["coverage_passed"])
        self.clock.value = sample["anchor_ms"] + 310000
        self.assertTrue(observer.tick())
        summary = p.summarize(plan, observer.journal.events)
        self.assertEqual(summary["identical_all_arm_observed_slots"], 1)
        self.assertTrue(summary["arms"]["prior"]["coverage_passed"])
        self.assertEqual(summary["arms"]["prior"]["common_cohort_comparisons_vs_raw_references"]["prior"]
                         ["raw_brier_improvement_vs_raw_reference"], 0)
        self.assertEqual(len(loads), 2)
        self.assertEqual(len(calls), 2)
        events = observer.journal.events
        outcome_seq = next(e["sequence"] for e in events if e["kind"] == "OUTCOME")
        self.assertTrue(all(e["sequence"] < outcome_seq for e in events if e["kind"] == "ARM_FINAL"))

    def test_late_quote_availability_and_post_slot_prices_cannot_enter_input(self):
        plan = self.plan()
        observer, source, _, calls = self.observer(plan)
        scheduled = p.slot_time(plan, 0)
        late = copy.deepcopy(source.quotes[0])
        late.update(t=scheduled - 100, known_at_ms=scheduled + 1000,
                    bid=9.0, ask=9.1, mid=9.05, receipt="f" * 64)
        source.quotes.append(late)
        source.quotes.sort(key=lambda q: q["t"])
        self.clock.value = scheduled + 2000
        observer.tick()
        for _, rows in calls:
            quotes = rows[0]["input_snapshot"]["quotes"]
            self.assertTrue(all(q["t"] <= scheduled and q["known_at_ms"] <= scheduled for q in quotes))
            self.assertNotIn(late, quotes)

    def test_late_prediction_remains_in_fixed_denominator_and_other_arm_is_not_started(self):
        plan = self.plan()
        observer, _, _, calls = self.observer(plan, behaviors={"prior": lambda: self.clock.advance(6000)})
        self.clock.value = p.slot_time(plan, 0)
        observer.tick()
        self.assertEqual([name for name, _ in calls], ["prior"])
        reasons = [e["data"]["reason"] for e in observer.journal.events if e["kind"] == "ARM_FINAL"]
        self.assertEqual(reasons, ["PREDICTION_DURABILITY_DEADLINE_MISSED", "DEADLINE_BEFORE_ARM_ATTEMPT"])
        self.clock.value = p.slot_time(plan, 0) + 310000
        observer.tick()
        report = p.summarize(plan, observer.journal.events)
        for arm in report["arms"].values():
            self.assertEqual((arm["fixed_denominator"], arm["observed"], arm["censored"]), (1, 0, 1))
            self.assertFalse(arm["coverage_passed"])

    def test_fsync_finishing_late_cannot_create_eligible_seal(self):
        plan = self.plan()
        observer, _, _, _ = self.observer(plan)
        append, actual_fsync = observer.journal.append, os.fsync
        def slow_append(kind, data):
            if kind == "SEAL":
                def fsync(fd):
                    actual_fsync(fd)
                    self.clock.advance(6000)
                with patch.object(p.os, "fsync", side_effect=fsync):
                    return append(kind, data)
            return append(kind, data)
        observer.journal.append = slow_append
        self.clock.value = p.slot_time(plan, 0)
        observer.tick()
        finals = p.replay(observer.journal.events, plan)[0]["finals"]
        self.assertEqual(finals["prior"]["data"]["reason"], "SEAL_DURABILITY_DEADLINE_MISSED")
        self.assertTrue(all(e["data"]["status"] == "MISSING" for e in finals.values()))

    def test_crash_after_attempt_never_retries_any_old_arm(self):
        plan = self.plan()
        def crash():
            raise ProcessCrash()
        observer, source, _, _ = self.observer(plan, behaviors={"prior": crash})
        self.clock.value = p.slot_time(plan, 0)
        with self.assertRaises(ProcessCrash):
            observer.tick()
        observer.journal.close()
        resumed, _, _, calls = self.observer(plan, source)
        resumed.tick()
        self.assertEqual(calls, [])
        finals = resumed.slots[0]["finals"]
        self.assertEqual(finals["prior"]["data"]["reason"], "PROCESS_RESTART_NO_RETRY")
        self.assertTrue(finals["prior"]["data"]["attempted"])
        self.assertFalse(finals["chronos_frozen"]["data"]["attempted"])
        p.summarize(plan, resumed.journal.events)

    def test_crash_after_seal_does_not_manufacture_final_receipt(self):
        plan = self.plan()
        observer, source, _, _ = self.observer(plan)
        append = observer.journal.append
        def crash_at_final(kind, data):
            if kind == "ARM_FINAL" and data["status"] == "SEALED":
                raise ProcessCrash()
            return append(kind, data)
        observer.journal.append = crash_at_final
        self.clock.value = p.slot_time(plan, 0)
        with self.assertRaises(ProcessCrash):
            observer.tick()
        observer.journal.close()
        resumed, _, _, calls = self.observer(plan, source)
        self.assertEqual(calls, [])
        report = p.summarize(plan, resumed.journal.events)
        self.assertEqual(report["arms"]["prior"]["sealed_predictions"], 0)

    def test_torn_tail_preserved_and_recovered_without_duplicate_prediction(self):
        plan = self.plan()
        observer, source, _, calls = self.observer(plan)
        self.clock.value = p.slot_time(plan, 0)
        observer.tick()
        observer.journal.close()
        with (self.root / "state" / "events.jsonl").open("ab") as out:
            out.write(b'{"incomplete":')
        resumed, _, _, new_calls = self.observer(plan, source)
        resumed.tick()
        self.assertEqual(len(calls), 2)
        self.assertEqual(new_calls, [])
        saved = list((self.root / "state").glob("uncommitted-tail-*.bin"))
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0].read_bytes(), b'{"incomplete":')
        self.assertTrue(any(e["kind"] == "RECOVER_UNCOMMITTED_TAIL" for e in resumed.journal.events))
        p.summarize(plan, resumed.journal.events)

    def test_complete_corrupt_record_fails_closed(self):
        plan = self.plan()
        observer, _, _, _ = self.observer(plan)
        observer.journal.close()
        path = self.root / "state" / "events.jsonl"
        raw = path.read_bytes().replace(b'"HEADER"', b'"HAADER"', 1)
        path.write_bytes(raw)
        with self.assertRaisesRegex(ValueError, "EVENT_CHAIN"):
            p.read_events(path, plan["plan_id"])

    def test_restart_after_elapsed_slot_does_not_backfill_even_inside_deadline(self):
        plan = self.plan()
        self.clock.value = p.slot_time(plan, 0) + 1
        observer, _, _, calls = self.observer(plan)
        observer.tick()
        self.assertEqual(calls, [])
        self.assertEqual(observer.slots[0]["finals"]["prior"]["data"]["reason"], "SLOT_ELAPSED_BEFORE_PROCESS_START")

    def test_original_prefix_replacement_censors_outcome(self):
        plan = self.plan()
        observer, source, _, _ = self.observer(plan)
        self.clock.value = p.slot_time(plan, 0)
        observer.tick()
        source.path.unlink()
        p.write_new(source.path, b"X" * source.prefix["bytes"])
        self.clock.value = p.slot_time(plan, 0) + 310000
        observer.tick()
        outcome = observer.slots[0]["outcome"]["data"]
        self.assertEqual(outcome["status"], "CENSORED")
        self.assertIn("ORIGINAL_INPUT_PREFIX_CHANGED", outcome["reason"])
        self.assertEqual(p.summarize(plan, observer.journal.events)["outcome_resource_retry_records"], 0)

    def test_path_gap_cannot_be_filled_by_endpoint(self):
        plan = self.plan()
        observer, source, _, calls = self.observer(plan)
        self.clock.value = p.slot_time(plan, 0)
        observer.tick()
        anchor = calls[0][1][0]["anchor_ms"]
        source.quotes = [q for q in source.quotes if not anchor + 20000 <= q["t"] <= anchor + 70000]
        self.clock.value = anchor + 310000
        observer.tick()
        outcome = observer.slots[0]["outcome"]["data"]
        self.assertEqual(outcome["status"], "CENSORED")
        self.assertIn("OUTCOME_PATH_GAP", outcome["reason"])

    def test_invalid_output_is_missing_and_does_not_corrupt_other_arm(self):
        plan = self.plan()
        observer, _, _, calls = self.observer(plan)
        predictor = observer.predictors["prior"]
        original = predictor.predict
        def invalid(rows):
            result = original(rows)
            result["probabilities"][0][0] = float("nan")
            return result
        predictor.predict = invalid
        self.clock.value = p.slot_time(plan, 0)
        observer.tick()
        self.assertEqual(len(calls), 2)
        self.assertEqual(observer.slots[0]["finals"]["prior"]["data"]["status"], "MISSING")
        self.assertEqual(observer.slots[0]["finals"]["chronos_frozen"]["data"]["status"], "SEALED")
        p.summarize(plan, observer.journal.events)

    def test_rotation_is_fixed_and_models_load_only_once(self):
        plan = self.plan(slot_count=2, max_censored=1)
        observer, _, loads, calls = self.observer(plan)
        for slot in range(2):
            self.clock.value = p.slot_time(plan, slot)
            observer.tick()
        self.assertEqual([name for name, _ in calls], ["prior", "chronos_frozen", "chronos_frozen", "prior"])
        self.assertEqual(loads, ["prior", "chronos_frozen"])

    def test_exclusive_writer_lock(self):
        plan = self.plan()
        observer, source, _, _ = self.observer(plan)
        with self.assertRaises(BlockingIOError):
            self.observer(plan, source)
        self.assertIsNotNone(observer.journal.fd)

    def test_reference_is_raw_prior_even_when_calibration_makes_prior_weaker(self):
        plan = self.plan()
        observer, _, _, _ = self.observer(plan)
        original = observer.predictors["prior"].predict
        def prior(rows):
            result = original(rows)
            result["raw_probabilities"] = [[0, 0, 0, 1, 0, 0, 0]]
            return result
        observer.predictors["prior"].predict = prior
        self.clock.value = p.slot_time(plan, 0)
        observer.tick()
        self.clock.advance(310000)
        observer.tick()
        report = p.summarize(plan, observer.journal.events)
        self.assertEqual(report["reference_output"], "raw_probabilities")
        prior_metric = report["arms"]["prior"]["identical_all_arm_cohort_metrics"]
        self.assertEqual(prior_metric["raw"]["mean_brier"], 0)
        self.assertGreater(prior_metric["calibrated"]["mean_brier"], 0)
        comparison = report["arms"]["chronos_frozen"]["common_cohort_comparisons_vs_raw_references"]["prior"]
        self.assertLess(comparison["raw_brier_improvement_vs_raw_reference"], 0)
        self.assertLess(comparison["calibrated_brier_improvement_vs_raw_reference"], 0)

    def test_numerical_warmup_has_no_sample_labels_or_scored_prediction(self):
        calls = []
        class Component:
            def predict(self, contexts, horizon_steps):
                calls.append(("backbone", len(contexts[0]), horizon_steps))
            def predict_proba(self, features):
                calls.append(("head", tuple(features.shape)))
        class Predictor:
            state = {"feature_names": ["a", "b"]}
            adapter = Component()
            model = Component()
        receipt = p.numerical_warmup(Predictor())
        self.assertEqual(calls, [("backbone", 24, 30), ("head", (1, 2))])
        self.assertEqual(receipt["scope"], p.WARMUP_POLICY)
        self.assertFalse(receipt["scored"])

    def test_replay_rejects_broken_semantic_seal_reference(self):
        plan = self.plan()
        observer, _, _, _ = self.observer(plan)
        self.clock.value = p.slot_time(plan, 0)
        observer.tick()
        events = copy.deepcopy(observer.journal.events)
        final = next(e for e in events if e["kind"] == "ARM_FINAL")
        final["data"]["seal_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "ARM_FINAL_SEAL_PROOF"):
            p.replay(events, plan)

    def test_transient_settlement_resource_refusal_recovers_without_repredicting(self):
        plan = self.plan()
        observer, source, _, calls = self.observer(plan)
        self.clock.value = p.slot_time(plan, 0)
        observer.tick()
        poll = source.poll
        def refused(*args, **kwargs):
            raise BlockingIOError(errno.EAGAIN, "temporary resource refusal")
        source.poll = refused
        self.clock.value = calls[0][1][0]["anchor_ms"] + 310000
        self.assertFalse(observer.tick())
        self.assertIsNone(observer.slots[0]["outcome"])
        report = p.summarize(plan, observer.journal.events)
        self.assertEqual(report["pending_outcome_resource_deferrals"], 1)
        self.assertEqual(report["arms"]["prior"]["censored"], 0)
        self.assertEqual(report["arms"]["prior"]["unresolved_or_future"], 1)
        self.assertFalse(report["arms"]["prior"]["coverage_passed"])
        source.poll = poll
        self.clock.advance(1000)
        self.assertFalse(observer.tick())
        self.assertEqual(source.calls, 1)
        self.clock.advance(4000)
        self.assertTrue(observer.tick())
        self.assertEqual(len(calls), 2)
        self.assertEqual(source.calls, 2)
        self.assertEqual(observer.slots[0]["outcome"]["data"]["status"], "OBSERVED")
        self.assertTrue(p.summarize(plan, observer.journal.events)["identical_all_arm_coverage_passed"])

    def test_settlement_resource_retry_is_bounded_and_does_not_reopen_final_censor(self):
        plan = self.plan()
        observer, source, _, calls = self.observer(plan)
        self.clock.value = p.slot_time(plan, 0)
        observer.tick()
        poll = source.poll
        def refused(*args, **kwargs):
            raise OSError(errno.EMFILE, "temporary descriptor exhaustion")
        source.poll = refused
        mature = calls[0][1][0]["anchor_ms"] + 310000
        for step in range(13):
            self.clock.value = mature + step * 5000
            observer.tick()
        report = p.summarize(plan, observer.journal.events)
        self.assertEqual(report["outcome_resource_retry_records"], 12)
        self.assertEqual(report["pending_outcome_resource_deferrals"], 0)
        self.assertIn("OUTCOME_RESOURCE_RETRY_EXHAUSTED", observer.slots[0]["outcome"]["data"]["reason"])
        self.assertEqual(report["arms"]["prior"]["censored"], 1)
        source.poll = poll
        self.clock.advance(5000)
        observer.tick()
        self.assertEqual(len(calls), 2)
        self.assertEqual(source.calls, 1)
        self.assertEqual(observer.slots[0]["outcome"]["data"]["status"], "CENSORED")

    def test_restart_preserves_settlement_retry_cooldown_and_original_prediction(self):
        plan = self.plan()
        observer, source, _, calls = self.observer(plan)
        self.clock.value = p.slot_time(plan, 0)
        observer.tick()
        poll = source.poll
        def refused(*args, **kwargs):
            raise OSError(errno.EBUSY, "temporary resource refusal")
        source.poll = refused
        self.clock.value = calls[0][1][0]["anchor_ms"] + 310000
        observer.tick()
        observer.journal.close()
        source.poll = poll
        self.clock.advance(1000)
        resumed, _, _, new_calls = self.observer(plan, source)
        resumed.tick()
        self.assertEqual(source.calls, 1)
        self.clock.advance(4000)
        self.assertTrue(resumed.tick())
        self.assertEqual(source.calls, 2)
        self.assertEqual(new_calls, [])
        self.assertEqual(p.summarize(plan, resumed.journal.events)["arms"]["prior"]["observed"], 1)

    def test_retry_logging_delay_does_not_extend_grace_or_corrupt_replay(self):
        plan = self.plan()
        observer, source, _, calls = self.observer(plan)
        self.clock.value = p.slot_time(plan, 0)
        observer.tick()
        def refused(*args, **kwargs):
            raise OSError(errno.EAGAIN, "temporary resource refusal")
        source.poll = refused
        append = observer.journal.append
        def delayed(kind, data):
            if kind == "OUTCOME_DEFERRED":
                self.clock.advance(60000)
            return append(kind, data)
        observer.journal.append = delayed
        self.clock.value = calls[0][1][0]["anchor_ms"] + 310000
        observer.tick()
        p.replay(observer.journal.events, plan)
        observer.tick()
        report = p.summarize(plan, observer.journal.events)
        self.assertEqual(report["outcome_resource_retry_records"], 1)
        self.assertEqual(report["arms"]["prior"]["censored"], 1)

    def test_preexisting_final_censored_receipt_is_not_rejudged_on_restart(self):
        plan = self.plan()
        observer, source, _, _ = self.observer(plan)
        self.clock.value = p.slot_time(plan, 0)
        observer.tick()
        observer.outcome(0, {"status": "CENSORED", "reason": "PREEXISTING_FINAL_READ_REFUSAL"})
        observer.journal.close()
        self.clock.advance(400000)
        resumed, _, _, calls = self.observer(plan, source)
        self.assertTrue(resumed.tick())
        self.assertEqual(calls, [])
        self.assertEqual(source.calls, 1)
        self.assertEqual(resumed.slots[0]["outcome"]["data"]["reason"], "PREEXISTING_FINAL_READ_REFUSAL")

    def test_separate_arm_coverage_cannot_pass_an_insufficient_common_cohort(self):
        plan = self.plan(slot_count=3, max_censored=1)
        def failure_at(slot):
            def fail():
                if self.clock() == p.slot_time(plan, slot):
                    raise RuntimeError("fixed synthetic prediction refusal")
            return fail
        observer, _, _, _ = self.observer(plan, behaviors={"prior": failure_at(0), "chronos_frozen": failure_at(1)})
        for slot in range(3):
            self.clock.value = p.slot_time(plan, slot)
            observer.tick()
            self.clock.advance(10000)
            observer.tick()
        self.clock.value = p.slot_time(plan, 2) + 310000
        self.assertTrue(observer.tick())
        report = p.summarize(plan, observer.journal.events)
        self.assertTrue(all(arm["coverage_passed"] for arm in report["arms"].values()))
        self.assertEqual(report["identical_all_arm_observed_slots"], 1)
        self.assertEqual(report["identical_all_arm_censored_slots"], 2)
        self.assertFalse(report["identical_all_arm_coverage_passed"])


class ActualSourceFormatTest(unittest.TestCase):
    def test_real_format_chain_partial_tail_period_and_modified_prefix(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            now = int(time.time() * 1000)
            start = now - 100000
            segment = root / ("segment-" + str(start * 1000000) + "-abcd")
            segment.mkdir(mode=0o700)
            path = segment / "quotes.jsonl"
            previous, records = "0" * 64, []
            header = {"source": "IB_TOOL_GATEWAY", "execution_domain": "PAPER", "instrument": "EUR.USD",
                      "period_sha256": "a" * 64, "write_operations_allowed": [], "broker_orders_sent": 0,
                      "collector_sha256": "b" * 64}
            values = [(start, "HEADER", header)]
            for t in range(start + 1000, now - 1000, 1000):
                values.append((t + 50, "QUOTE", {"instrument": "EUR.USD", "source": "IB", "authoritative": True,
                    "stale": False, "subscription_id": "TEST", "subscription_state": "active", "bid": 1.1,
                    "ask": 1.10002, "observed_at_ms": t, "stale_after_ms": t + 5000}))
            for sequence, (at, kind, data) in enumerate(values):
                body = {"schema": "heptatrader.laya-shadow-quotes.v1", "sequence": sequence,
                        "previous": previous, "at_ms": at, "kind": kind, "data": data}
                previous = p.digest(body)
                records.append({**body, "sha256": previous})
            raw = b"".join(p.canonical(r) + b"\n" for r in records)
            path.write_bytes(raw + b'{"not_complete":')
            path.chmod(0o600)
            pin_json(root / "status.json", {"schema": "heptatrader.laya-feed-status.v1", "period_sha256": "a" * 64,
                "broker_orders_sent": 0, "active_segment": str(segment)})
            source = p.FeedSource({"feed_root": str(root), "period_sha256": "a" * 64})
            first = source.poll(now, start)
            self.assertEqual(len(first["quotes"]), len(values) - 1)
            self.assertEqual(first["prefixes"][str(path)]["bytes"], len(raw))
            second = source.poll(now, start)
            self.assertEqual(first["quotes"], second["quotes"])
            self.assertEqual(source.cache.hits, 1)
            self.assertEqual(source.cache.rehashed_bytes, len(raw))
            changed = raw.replace(b'"bid":1.1,', b'"bid":1.2,', 1)
            self.assertNotEqual(changed, raw)
            path.write_bytes(changed + b'{"not_complete":')
            with self.assertRaisesRegex(ValueError, "PREFIX_CHANGED"):
                source.poll(now, start)


if __name__ == "__main__":
    unittest.main()
