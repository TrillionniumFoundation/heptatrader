"""Train V2 phase 2: price/model semantic parity and bounded original-source handoff."""
import copy
import json
import tempfile
import time
import unittest
from pathlib import Path

from train_v2.adapters import make_spec, sealed_live_segment
from train_v2.contracts import ContractError, compile_dataset, digest
from train_v2.parity import (
    compare_numeric_laya_price_inputs, price_family_contracts,
    require_semantic_compatibility,
)
from train_v2.handoff import verify_original_paper_handoff, ingest_original_event
from train_v2.orchestrator import (
    SOURCE_EVENT, OUTCOME_EVENT, CORRECTION_EVENT,
    evidence_event, register_plan,
)


PERIOD = "b" * 64


class OriginalSourceFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / "original-quotes.jsonl"
        self.spec = make_spec(PERIOD, "PAPER_FIXTURE_BEHAVIOR")
        self.now = int(time.time_ns() // 1000000)
        self.first = self.now - 60200
        header = {
            "source": "IB_TOOL_GATEWAY", "instrument": "EUR.USD",
            "execution_domain": "PAPER", "period_sha256": PERIOD,
            "write_operations_allowed": [], "broker_orders_sent": 0,
        }
        events = [("HEADER", header, self.first - 10)]
        for i in range(7):
            t = self.first + i * 10000
            events.append(("QUOTE", {
                "instrument": "EUR.USD", "source": "IB",
                "authoritative": True, "stale": False,
                "bid": str(1.1 + 0.00001*i),
                "ask": str(1.10002 + 0.00001*i),
                "observed_at_ms": t, "stale_after_ms": t + 5000,
                "subscription_id": "LOCAL-UNIT-ONLY",
                "subscription_state": "active",
            }, t + 1))
        from laya_cell.shadow_source import canonical, digest as chain_digest
        previous = "0" * 64
        lines = []
        for i, (kind, data, at) in enumerate(events):
            row = {
                "schema": "heptatrader.laya-shadow-quotes.v1",
                "sequence": i, "previous": previous, "at_ms": at,
                "kind": kind, "data": data,
            }
            row["sha256"] = chain_digest(row)
            previous = row["sha256"]
            lines.append(canonical(row))
        self.path.write_bytes(b"\n".join(lines) + b"\n")
        self.path.chmod(0o600)
        self.records, _ = sealed_live_segment(self.path, self.spec)
        self.decision = self.records[-1]["available_at_ms"]
        self.plan = register_plan(
            spec=self.spec, registered_at_ms=self.first - 1000,
            first_decision_ms=self.decision,
            last_decision_ms=self.decision + 60000,
            minimum_mature_rows=1,
        )
        self.dataset = compile_dataset(
            self.spec, self.records, [self.decision], self.decision)
        self.example = self.dataset["examples"][0]
        self.event = evidence_event(
            self.plan, self.example, kind=SOURCE_EVENT,
            at_ms=self.decision,
            evidence_sha256=self.example["features"]["input_receipts"][-1]["source_sha256"],
        )


class ParityTests(OriginalSourceFixture):
    def test_exact_original_numeric16_and_laya_price_question(self):
        result = compare_numeric_laya_price_inputs(
            self.spec, self.records, self.decision, horizon_sec=300)
        self.assertEqual(len(result["numeric16"]), 16)
        self.assertEqual(len(result["laya_price_question"]["crit"]), 7)
        self.assertEqual(result["laya_causal_state"]["horizon_sec"], 300)
        self.assertFalse(result["direct_four_to_sixteen_tensor_cast_allowed"])
        self.assertFalse(result["laya_action_is_trade_action"])
        self.assertFalse(result["trade_authority"])

    def test_second_horizon_uses_original_semantics(self):
        result = compare_numeric_laya_price_inputs(
            self.spec, self.records, self.decision, horizon_sec=900)
        self.assertEqual(result["laya_causal_state"]["horizon_sec"], 900)

    def test_value_not_interchangeable_with_price_or_other_behavior(self):
        x = price_family_contracts()
        self.assertEqual(x["numeric"]["input_width"], 16)
        self.assertEqual(x["laya-price"]["auxiliary_action_vocabulary"],
                         ["ACT", "ESCALATE"])
        self.assertNotEqual(x["value-eur25k"]["feature_target"],
                            x["numeric"]["target"])
        self.assertNotEqual(x["value-usd100k"]["reference_notional_usd"],
                            x["value-eur25k"]["quantity_eur"])
        with self.assertRaisesRegex(ContractError, "MODEL_SEMANTICS_NOT_EQUIVALENT"):
            require_semantic_compatibility("numeric", "laya-price")
        with self.assertRaisesRegex(ContractError, "MODEL_SEMANTICS_NOT_EQUIVALENT"):
            require_semantic_compatibility("value-eur25k", "value-usd100k")
        result = require_semantic_compatibility("numeric", "numeric")
        self.assertFalse(result["can_auto_select"])
        with self.assertRaisesRegex(ContractError, "NO_AUTOMATIC_TRAIN"):
            require_semantic_compatibility("numeric", "numeric", proposed_repurpose=True)

    def test_seven_class_not_buy_sell_action(self):
        x = price_family_contracts()["laya-price"]
        self.assertFalse(x["trade_action_head"])
        self.assertEqual(len(x["bins_bps"]), 6)

    def test_modified_old_price_prediction_fails(self):
        bad = copy.deepcopy(self.records)
        bad[-1]["bid"] = "1.30001"
        with self.assertRaises(ContractError):
            compare_numeric_laya_price_inputs(self.spec, bad, self.decision)

    def test_incompatible_pit_source_denied(self):
        historical = make_spec(PERIOD, "PAPER_FIXTURE_BEHAVIOR", historical=True)
        with self.assertRaisesRegex(ContractError, "MODEL_PARITY_REQUIRES_REAL_CAUSAL_SOURCE"):
            compare_numeric_laya_price_inputs(historical, [], self.decision)


class HandoffTests(OriginalSourceFixture):
    def test_genuine_local_original_source_compiles(self):
        result = verify_original_paper_handoff(
            self.plan, self.dataset, self.event, self.path, now_ms=self.decision+500)
        self.assertTrue(result["original_source_chain_locally_verified"])
        self.assertFalse(result["canonical_agentd_owner_enrolled"])
        self.assertEqual(result["broker_orders_sent"], 0)

    def test_bounded_live_delivery_records_once(self):
        db = self.root / "research" / "events.sqlite"
        result = ingest_original_event(
            db, self.plan, self.dataset, self.event, self.path)
        self.assertEqual(result["result"], "INPUT_RECORDED_SHADOW_ONLY")
        duplicate = ingest_original_event(
            db, self.plan, self.dataset, self.event, self.path)
        self.assertEqual(duplicate["result"], "DUPLICATE_NO_EFFECT")
        self.assertFalse(result["automatic_training"])

    def test_fresh_original_one_shot_cli_then_duplicate(self):
        import os
        import subprocess
        import sys
        records = {}
        for label, document in (("plan", self.plan),
                                ("dataset", self.dataset),
                                ("event", self.event)):
            file = self.root / (label + ".json")
            file.write_text(json.dumps(document, sort_keys=True))
            file.chmod(0o600)
            records[label] = file
        args = [sys.executable, "-B", "-m", "train_v2.original_event_ingest",
                "--plan", str(records["plan"]),
                "--dataset", str(records["dataset"]),
                "--event", str(records["event"]),
                "--original-segment", str(self.path),
                "--ledger", str(self.root / "newresearch" / "events.sqlite")]
        first = subprocess.run(args, capture_output=True, text=True,
                               env=dict(os.environ), timeout=10, check=False)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertIn("INPUT_RECORDED_SHADOW_ONLY", first.stdout)
        again = subprocess.run(args, capture_output=True, text=True,
                               env=dict(os.environ), timeout=10, check=False)
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertIn("DUPLICATE_NO_EFFECT", again.stdout)

    def test_late_callback_fails_instead_of_backdating(self):
        with self.assertRaisesRegex(ContractError, "ORIGINAL_EVENT_NOT_FRESH"):
            verify_original_paper_handoff(
                self.plan, self.dataset, self.event, self.path,
                now_ms=self.decision + 3001)

    def test_changed_source_rejected_even_with_rehashed_dataset(self):
        forged = copy.deepcopy(self.dataset)
        target = forged["examples"][0]
        target["features"]["values"]["mid"] = "1.23456789"
        target["feature_id"] = digest(target["features"])
        forged["dataset_id"] = digest({k: v for k, v in forged.items()
                                        if k != "dataset_id"})
        ev = evidence_event(
            self.plan, target, kind=SOURCE_EVENT, at_ms=self.decision,
            evidence_sha256=target["features"]["input_receipts"][-1]["source_sha256"])
        with self.assertRaisesRegex(ContractError, "ORIGINAL_SOURCE_FEATURE_NOT_REPRODUCIBLE"):
            verify_original_paper_handoff(
                self.plan, forged, ev, self.path, now_ms=self.decision + 500)

    def test_source_event_cannot_smuggle_a_future_profit_label(self):
        forged = copy.deepcopy(self.dataset)
        row = forged["examples"][0]
        row["label_status"] = "OBSERVED"
        row["label_return_bps"] = "200"
        row["label_receipt"] = {
            "source_id": digest({"fake": 1}),
            "source_sha256": digest({"fake": 2}),
            "event_at_ms": self.decision + 300000,
            "available_at_ms": self.decision + 300000,
        }
        row["candidate_training_eligible"] = True
        forged["evaluated_at_ms"] = self.decision + 300000
        forged["dataset_id"] = digest({k: v for k, v in forged.items()
                                       if k != "dataset_id"})
        with self.assertRaisesRegex(
                ContractError, "ORIGINAL_PREDECISION_LABEL_MUST_BE_UNOBSERVED"):
            verify_original_paper_handoff(
                self.plan, forged, self.event, self.path,
                now_ms=self.decision + 500)

    def test_original_file_tamper_refused(self):
        self.path.chmod(0o600)
        raw = self.path.read_bytes().replace(b"1.10002", b"1.10102", 1)
        self.path.write_bytes(raw)
        with self.assertRaises(ValueError):
            verify_original_paper_handoff(
                self.plan, self.dataset, self.event, self.path,
                now_ms=self.decision + 500)

    def test_missing_mature_outcome_not_sealed(self):
        ev = evidence_event(
            self.plan, self.example, kind=OUTCOME_EVENT,
            at_ms=self.decision + 300000,
            evidence_sha256=self.event["evidence_sha256"])
        with self.assertRaises(ContractError):
            verify_original_paper_handoff(
                self.plan, self.dataset, ev, self.path,
                now_ms=ev["at_ms"])

    def test_correction_requires_independent_owner(self):
        ev = evidence_event(
            self.plan, self.example, kind=CORRECTION_EVENT,
            at_ms=self.decision, evidence_sha256=digest({"correction": 1}))
        with self.assertRaisesRegex(ContractError, "ORIGINAL_CORRECTION_REQUIRES_SEPARATE_OWNER"):
            verify_original_paper_handoff(
                self.plan, self.dataset, ev, self.path, now_ms=self.decision + 1)
