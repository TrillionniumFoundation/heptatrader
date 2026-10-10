"""Adversarial local prospective registration; not canonical Agentd trust."""
import copy
import json
import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from train_v2.adapters import make_spec
from train_v2.contracts import ContractError, digest
from train_v2.orchestrator import SOURCE_EVENT, evidence_event, register_plan
from train_v2.registry import (
    MIN_LEAD_MS, ingest_registered_original_event,
    register_before_window, verify_registration,
)
from test_train_v2_phase2 import OriginalSourceFixture


class ProspectiveRegistryTests(OriginalSourceFixture):
    def setUp(self):
        super().setUp()
        self.registry = self.root / "prospective-registry"
        self.declared = self.decision - 40000
        self.plan = register_plan(
            spec=self.spec, registered_at_ms=self.declared,
            first_decision_ms=self.decision,
            last_decision_ms=self.decision + 60000,
            minimum_mature_rows=1)
        self.event = evidence_event(
            self.plan, self.example, kind=SOURCE_EVENT,
            at_ms=self.decision,
            evidence_sha256=self.example["features"]["input_receipts"][-1]["source_sha256"])

    def register_fixture(self, root=None):
        if root is None:
            root = self.registry
        with patch("train_v2.registry.time.time_ns", return_value=self.declared * 1000000):
            return register_before_window(root, self.plan, self.spec, self.root)

    def test_unregistered_plan_cannot_ingest_even_with_true_source(self):
        with self.assertRaisesRegex(ContractError, "REGISTRY_UNREGISTERED_PLAN"):
            ingest_registered_original_event(
                self.registry, self.registry / "registered-events.sqlite",
                self.plan, self.dataset, self.event, self.path)

    def test_sealed_plan_and_real_source_one_shot_ingest_is_idempotent(self):
        receipt = self.register_fixture()
        self.assertFalse(receipt["independent_owner_signature"])
        self.assertFalse(receipt["canonical_agentd_owner_enrolled"])
        self.assertEqual(receipt["sealed_at_ms"], self.declared)
        self.assertEqual(receipt["plan"], self.plan)
        proof = verify_registration(self.registry, self.plan, self.spec, self.path)
        self.assertEqual(receipt, proof)
        ledger = self.registry / "registered-events.sqlite"
        once = ingest_registered_original_event(
            self.registry, ledger, self.plan, self.dataset, self.event, self.path)
        again = ingest_registered_original_event(
            self.registry, ledger, self.plan, self.dataset, self.event, self.path)
        self.assertEqual(once["status"], "INPUT_RECORDED_SHADOW_ONLY")
        self.assertEqual(again["status"], "DUPLICATE_NO_EFFECT")
        self.assertFalse(once["automatic_training"])
        self.assertFalse(once["canonical_agentd_accepted"])
        from contextlib import closing
        with closing(sqlite3.connect(ledger)) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM events").fetchone()[0], 1)

    def test_plan_claim_cannot_be_backdated(self):
        with self.assertRaisesRegex(ContractError, "REGISTRY_NOT_PROSPECTIVELY_DECLARED"):
            register_before_window(self.registry, self.plan, self.spec, self.root)

    def test_plan_must_precede_first_decision_by_at_least_30_seconds(self):
        altered = register_plan(
            spec=self.spec, registered_at_ms=self.declared,
            first_decision_ms=self.declared + MIN_LEAD_MS - 1,
            last_decision_ms=self.declared + MIN_LEAD_MS + 30000,
            minimum_mature_rows=1)
        with patch("train_v2.registry.time.time_ns", return_value=self.declared * 1000000):
            with self.assertRaisesRegex(ContractError, "REGISTRY_NOT_PROSPECTIVELY_DECLARED"):
                register_before_window(self.registry, altered, self.spec, self.root)

    def test_existing_plan_refuses_duplicate_registration(self):
        self.register_fixture()
        with patch("train_v2.registry.time.time_ns", return_value=self.declared * 1000000):
            with self.assertRaisesRegex(ContractError, "REGISTRY_PLAN_ALREADY_REGISTERED"):
                register_before_window(self.registry, self.plan, self.spec, self.root)

    def test_cross_policy_overlapping_trial_refused(self):
        self.register_fixture()
        competing_spec = make_spec(self.spec["period_sha256"], "OTHER_POLICY_DOES_NOT_RESET_OOS")
        competing = register_plan(
            spec=competing_spec, registered_at_ms=self.declared,
            first_decision_ms=self.decision + 10000,
            last_decision_ms=self.decision + 70000, minimum_mature_rows=1)
        with patch("train_v2.registry.time.time_ns", return_value=self.declared * 1000000):
            with self.assertRaisesRegex(ContractError, "REGISTRY_TRIAL_WINDOW_ALREADY_USED"):
                register_before_window(self.registry, competing, competing_spec, self.root)

    def test_independent_nonoverlapping_future_window_allowed(self):
        self.register_fixture()
        second = register_plan(
            spec=self.spec, registered_at_ms=self.declared,
            first_decision_ms=self.decision + 60001,
            last_decision_ms=self.decision + 120000, minimum_mature_rows=1)
        with patch("train_v2.registry.time.time_ns", return_value=self.declared * 1000000):
            receipt = register_before_window(self.registry, second, self.spec, self.root)
        self.assertNotEqual(receipt["plan_id"], self.plan["plan_id"])

    def test_sealed_receipt_mutation_fails_even_with_matching_json_fields(self):
        self.register_fixture()
        receipt_path = (self.registry / "sealed-plans" / (self.plan["plan_id"] + ".json"))
        modified = json.loads(receipt_path.read_text())
        modified["first_decision_ms"] += 1
        receipt_path.write_text(json.dumps(modified, sort_keys=True))
        with self.assertRaisesRegex(ContractError, "REGISTRY_PLAN_RECEIPT_OR_SCOPE_CHANGED"):
            verify_registration(self.registry, self.plan, self.spec, self.path)

    def test_missing_seal_is_not_deemed_registered(self):
        self.register_fixture()
        path = self.registry / "sealed-plans" / (self.plan["plan_id"] + ".json")
        path.unlink()
        with self.assertRaisesRegex(ContractError, "REGISTRY_SEAL_MISSING"):
            verify_registration(self.registry, self.plan, self.spec, self.path)

    def test_orphan_seal_prevents_later_retroactive_registration(self):
        self.registry.mkdir(mode=0o700)
        directory = self.registry / "sealed-plans"
        directory.mkdir(mode=0o700)
        (directory / (self.plan["plan_id"] + ".json")).write_text('{"interrupted":true}')
        with patch("train_v2.registry.time.time_ns", return_value=self.declared * 1000000):
            with self.assertRaisesRegex(ContractError, "REGISTRY_UNKNOWN_ORPHAN_SEAL"):
                register_before_window(self.registry, self.plan, self.spec, self.root)

    def test_original_file_must_be_under_pinned_source_root(self):
        self.register_fixture()
        with tempfile.TemporaryDirectory() as td:
            foreign = Path(td) / "other.jsonl"
            foreign.write_bytes(self.path.read_bytes())
            foreign.chmod(0o600)
            with self.assertRaisesRegex(ContractError, "REGISTRY_ORIGINAL_ROOT_MISMATCH"):
                verify_registration(self.registry, self.plan, self.spec, foreign)

    def test_registered_ingest_cannot_write_arbitrary_ledger(self):
        self.register_fixture()
        with self.assertRaisesRegex(ContractError, "REGISTRY_EVENT_LEDGER_NOT_BOUND"):
            ingest_registered_original_event(
                self.registry, self.root / "arbitrary.sqlite",
                self.plan, self.dataset, self.event, self.path)

    def test_outside_plan_start_is_unavailable(self):
        self.register_fixture()
        altered = copy.deepcopy(self.plan)
        altered["first_decision_ms"] += 1
        with self.assertRaisesRegex(ContractError, "TRAIN_PLAN_NOT_RESEARCH_ONLY"):
            verify_registration(self.registry, altered, self.spec, self.path)

    def test_previous_ingest_clis_cannot_bypass_registered_ledger(self):
        import subprocess
        import sys
        for module in ("train_v2.event_ingest", "train_v2.original_event_ingest"):
            child = subprocess.run(
                [sys.executable, "-B", "-m", module,
                 "--plan", str(self.root / "absent-plan.json"),
                 "--dataset", str(self.root / "absent-dataset.json"),
                 "--event", str(self.root / "absent-event.json"),
                 *([] if module == "train_v2.event_ingest" else
                   ["--original-segment", str(self.path)]),
                 "--ledger", str(self.registry / "registered-events.sqlite")],
                capture_output=True, text=True, timeout=5, check=False)
            self.assertNotEqual(child.returncode, 0)
            self.assertIn(
                "REGISTERED_LEDGER_REQUIRES_PRESEALED_PLAN_AND_ORIGINAL_SOURCE",
                child.stderr)

    def test_registration_cli_uses_actual_clock_without_caller_backdating(self):
        import subprocess
        import sys
        spec_file = self.root / "registration-spec.json"
        spec_file.write_text(json.dumps(self.spec, sort_keys=True))
        spec_file.chmod(0o600)
        now = int(time.time() * 1000)
        cli = subprocess.run(
            [sys.executable, "-B", "-m", "train_v2.register_trial",
             "--feature-spec", str(spec_file),
             "--registry-root", str(self.registry),
             "--original-source-root", str(self.root),
             "--first-decision-ms", str(now+120000),
             "--last-decision-ms", str(now+180000)],
            capture_output=True, text=True, timeout=6, check=False)
        self.assertEqual(cli.returncode, 0, cli.stderr)
        receipt = json.loads(cli.stdout)
        self.assertTrue(receipt["sealed_at_ms"] >= now)
        self.assertGreaterEqual(receipt["first_decision_ms"] -
                                receipt["sealed_at_ms"], 30000)
        self.assertFalse(receipt["independent_owner_signature"])
        self.assertEqual(receipt["broker_orders_sent"], 0)

    def test_unbounded_future_window_refused(self):
        from train_v2.registry import MAX_WINDOW_MS
        self.plan = register_plan(
            spec=self.spec, registered_at_ms=self.declared,
            first_decision_ms=self.decision, last_decision_ms=self.decision+MAX_WINDOW_MS+1,
            minimum_mature_rows=1)
        with patch("train_v2.registry.time.time_ns", return_value=self.declared*1000000):
            with self.assertRaisesRegex(ContractError,
                                        "REGISTRY_WINDOW_TOO_WIDE_OR_DISTANT"):
                register_before_window(self.registry, self.plan, self.spec, self.root)

    def test_concurrent_registrar_processes_cannot_reserve_same_window(self):
        import subprocess
        import sys
        spec_file = self.root / "parallel-spec.json"
        spec_file.write_text(json.dumps(self.spec, sort_keys=True))
        spec_file.chmod(0o600)
        now = int(time.time()*1000)
        args = [sys.executable, "-B", "-m", "train_v2.register_trial",
                "--feature-spec", str(spec_file),
                "--registry-root", str(self.registry),
                "--original-source-root", str(self.root),
                "--first-decision-ms", str(now+120000),
                "--last-decision-ms", str(now+180000)]
        children = [subprocess.Popen(args, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, text=True)
                    for _ in range(2)]
        outputs = [child.communicate(timeout=10) for child in children]
        codes = [child.returncode for child in children]
        self.assertEqual(sum(code==0 for code in codes), 1, outputs)
        # Two child processes may share the same millisecond and therefore
        # generate an identical immutable plan_id; both denial reasons are safe.
        self.assertTrue(any(
            "REGISTRY_TRIAL_WINDOW_ALREADY_USED" in err
            or "REGISTRY_PLAN_ALREADY_REGISTERED" in err
            for _, err in outputs), outputs)
        from contextlib import closing
        with closing(sqlite3.connect(self.registry/"registrations.sqlite")) as db:
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM registrations").fetchone()[0],1)
        self.assertEqual(len(list((self.registry/"sealed-plans").glob("*.json"))),1)

    def test_original_old_timestamp_cannot_be_replayed_as_new(self):
        self.register_fixture()
        event = evidence_event(
            self.plan, self.example, kind=SOURCE_EVENT,
            at_ms=self.decision - 4000,
            evidence_sha256=self.event["evidence_sha256"])
        with self.assertRaises(ContractError):
            ingest_registered_original_event(
                self.registry, self.registry / "registered-events.sqlite",
                self.plan, self.dataset, event, self.path)
