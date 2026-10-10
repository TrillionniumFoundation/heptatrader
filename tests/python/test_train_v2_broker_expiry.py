"""Phase 4B opt-in IB-reported TTL and delayed independent signing.

All keys and timestamps here are deterministic TEST fixtures; no live producer
enrollment, previously missed forward input repair or broker orders.
"""
import base64
import copy
import hashlib
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from historical_training.common import canonical, sha
from train_v2.adapters import make_spec, sealed_live_segment
from train_v2.causal_ttl import sealed_original_broker_expiry
from train_v2.contracts import (
    BROKER_EXPIRY_PROFILE, BROKER_EXPIRY_POLICY, ContractError,
    compile_dataset, compile_example, digest, validate_spec,
)
from train_v2.external_signatures import (
    TRUST_SCHEMA, WITNESS_SCHEMA, signed_bytes, validate_event_witness,
)
from train_v2.handoff import verify_original_paper_handoff
from train_v2.orchestrator import SOURCE_EVENT, evidence_event, register_plan
from train_v2.parity import compare_numeric_laya_price_inputs
from train_v2.signed_handoff import register_externally_witnessed, ingest_signed_original
from test_train_v2_phase2 import OriginalSourceFixture


class OriginalBrokerExpiryTests(OriginalSourceFixture):
    def setUp(self):
        super().setUp()
        self.old_spec = self.spec
        self.original_available = self.decision
        self.spec = make_spec(
            self.old_spec["period_sha256"], "PAPER_FIXTURE_BEHAVIOR",
            broker_reported_expiry=True)
        self.records, self.provenance = sealed_original_broker_expiry(self.path, self.spec)
        self.decision = self.original_available + 1000
        self.plan = register_plan(
            spec=self.spec, registered_at_ms=self.decision - 40000,
            first_decision_ms=self.decision,
            last_decision_ms=self.decision + 60000,
            minimum_mature_rows=1)
        self.dataset = compile_dataset(self.spec, self.records, [self.decision], self.decision)
        self.example = self.dataset["examples"][0]
        self.event = evidence_event(
            self.plan, self.example, kind=SOURCE_EVENT,
            at_ms=self.original_available,
            evidence_sha256=self.example["features"]["input_receipts"][-1]["source_sha256"])
        self.registry = self.root / "new-ttl-external-registry"
        self.trust_keys = {role: Ed25519PrivateKey.generate()
                           for role in ("registrar", "producer", "observer")}
        pubs = {
            role: key.public_key().public_bytes(
                serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()
            for role, key in self.trust_keys.items()
        }
        self.trust = {
            "schema": TRUST_SCHEMA,
            "period_sha256": self.spec["period_sha256"],
            "instrument": self.spec["instrument"],
            "policy_id": self.plan["behavior_policy_id"],
            "trust_generation": 8,
            "not_before_ms": self.plan["registered_at_ms"] - 2000,
            "expires_at_ms": self.plan["registered_at_ms"] + 86400000,
            "roles": pubs,
            "revoked_role_keys": [],
            "issuer_kind": "OPERATOR_PINNED_EXTERNAL_RESEARCH_ONLY",
            "canonical_agentd_owner_enrolled": False,
            "trade_authority": False,
        }
        self.trust_path = self.root / "public-trust.json"
        self.trust_path.write_bytes(canonical(self.trust)+b"\n")
        self.trust_path.chmod(0o600)
        self.pin = sha(self.trust_path)
        self.registrar = self.issue(
            "registrar", "PLAN", self.plan["plan_id"],
            digest({
                "plan": self.plan, "feature_spec": self.spec,
                "original_source_root": str(self.root),
                "registry_root": str(self.registry)}),
            self.plan["registered_at_ms"])
        self.producer = self.issue(
            "producer", "EVENT", self.event["event_id"],
            digest({"event": self.event, "original_source_file": str(self.path),
                    "registry_root": str(self.registry)}),
            self.original_available + 350)

    def issue(self, role, kind, bound, binding, signed_at):
        witness = {
            "schema": WITNESS_SCHEMA, "kind": kind, "role": role,
            "plan_id": self.plan["plan_id"], "bound_id": bound,
            "binding_sha256": binding, "trust_sha256": self.pin,
            "period_sha256": self.plan["period_sha256"],
            "policy_id": self.plan["behavior_policy_id"],
            "authority_effect": "NONE",
            "nonce": digest({"signed": signed_at, "role": role, "bound": bound}),
            "issued_at_ms": signed_at, "expires_at_ms": signed_at + 90000,
        }
        witness["signature"] = base64.b64encode(
            self.trust_keys[role].sign(signed_bytes(witness))).decode()
        return witness

    def register_synthetic(self):
        with patch("train_v2.registry.time.time_ns",
                   return_value=self.plan["registered_at_ms"]*1000000):
            return register_externally_witnessed(
                self.registry, self.plan, self.spec, self.root,
                self.trust_path, self.pin, self.registrar,
                min_generation=8)

    def test_original_broker_ttl_extracted_without_changing_v1(self):
        self.assertEqual(self.spec["feature_profile"], BROKER_EXPIRY_PROFILE)
        self.assertEqual(self.spec["quote_expiry_policy"], BROKER_EXPIRY_POLICY)
        self.assertEqual(len(self.records), 7)
        self.assertFalse(self.provenance["canonical_agentd_owner_admitted"])
        self.assertFalse(self.provenance["broker_orders_sent"])
        q = self.records[-1]
        self.assertEqual(q["available_at_ms"], self.original_available)
        self.assertEqual(q["valid_until_ms"], q["event_at_ms"]+5000)
        self.assertGreater(q["valid_until_ms"], self.decision)
        after = compile_example(self.spec, self.records, self.decision, self.decision)
        self.assertTrue(after["locally_point_in_time"])
        self.assertEqual(after["label_status"], "PENDING_OUTCOME")
        with self.assertRaisesRegex(ContractError, "LIVE_ADAPTER_SCOPE"):
            sealed_live_segment(self.path, self.spec)
        older, _ = sealed_live_segment(self.path, self.old_spec)
        with self.assertRaisesRegex(ContractError, "FEATURE_QUOTE_EXPIRED"):
            compile_example(self.old_spec, older, self.decision, self.decision)

    def test_actual_original_numeric_and_laya_semantics_at_delayed_decision(self):
        result = compare_numeric_laya_price_inputs(
            self.spec, self.records, self.decision, horizon_sec=300)
        self.assertEqual(len(result["numeric16"]), 16)
        self.assertEqual(len(result["laya_price_question"]["crit"]), 7)
        self.assertEqual(result["available_at_ms"], self.original_available)
        self.assertEqual(result["decision_at_ms"], self.decision)
        self.assertGreater(result["decision_at_ms"], result["available_at_ms"])
        self.assertFalse(result["laya_action_is_trade_action"])

    def test_delayed_original_source_signed_before_decision_is_admitted_once(self):
        reg = self.register_synthetic()
        self.assertFalse(reg["canonical_agentd_owner_enrolled"])
        now = self.original_available + 700
        check = validate_event_witness(
            self.trust, self.pin, self.plan, self.event, self.path,
            self.registry, self.producer, now_ms=now,
            min_generation=8, decision_at_ms=self.decision)
        self.assertTrue(check["signature_verified"])
        proof = verify_original_paper_handoff(
            self.plan, self.dataset, self.event, self.path, now_ms=now)
        self.assertTrue(proof["original_source_chain_locally_verified"])
        self.assertEqual(proof["source_event_at_ms"], self.original_available)
        self.assertEqual(proof["feature_decision_at_ms"], self.decision)
        self.assertLess(proof["source_event_at_ms"], proof["feature_decision_at_ms"])
        first = ingest_signed_original(
            self.registry, self.plan, self.dataset, self.event, self.path,
            self.trust_path, self.pin, self.producer,
            min_generation=8, now_ms=now)
        repeated = ingest_signed_original(
            self.registry, self.plan, self.dataset, self.event, self.path,
            self.trust_path, self.pin, self.producer,
            min_generation=8, now_ms=now)
        self.assertEqual(first["state"], "INPUT_RECORDED_SHADOW_ONLY")
        self.assertEqual(repeated["state"], "DUPLICATE_NO_EFFECT")
        self.assertFalse(first["canonical_agentd_owner_enrolled"])
        self.assertFalse(first["automatic_training"])

    def test_producer_signing_after_decision_is_refused(self):
        self.register_synthetic()
        late = self.issue(
            "producer", "EVENT", self.event["event_id"],
            self.producer["binding_sha256"], self.decision + 1)
        with self.assertRaisesRegex(ContractError, "EXTERNAL_PRODUCER_SIGNED_TOO_LATE"):
            ingest_signed_original(
                self.registry, self.plan, self.dataset, self.event, self.path,
                self.trust_path, self.pin, late, min_generation=8,
                now_ms=self.decision+3)

    def test_signature_early_but_source_admitted_after_decision_is_denied(self):
        self.register_synthetic()
        # Original local quote was witnessed at original_available, producer
        # signed 350ms later (before decision), but actual ingest arrives late.
        signed_at = self.producer["issued_at_ms"]
        self.assertLess(signed_at, self.decision)
        late_now = self.decision + 1
        self.assertLess(late_now-self.event["at_ms"], 3000)
        with self.assertRaisesRegex(
                ContractError, "ORIGINAL_SOURCE_ADMITTED_AFTER_DECISION"):
            ingest_signed_original(
                self.registry, self.plan, self.dataset, self.event, self.path,
                self.trust_path, self.pin, self.producer,
                min_generation=8, now_ms=late_now)

    def test_broker_expiration_absolutely_bounds_decision(self):
        from laya_cell.shadow_source import canonical as original_canonical
        from laya_cell.shadow_source import digest as original_digest, strict
        # Create a NEW independently consistent local chain whose original
        # broker-reported expiry is much shorter than the general 5s cap.
        # This is a unit fixture, NOT a broker-signed fact or a live trial.
        rows = [strict(line) for line in self.path.read_bytes().splitlines()]
        original_last = rows[-1]["data"]
        original_last["stale_after_ms"] = original_last["observed_at_ms"] + 800
        previous = "0" * 64
        for record in rows:
            record["previous"] = previous
            record["sha256"] = original_digest(
                {key: val for key, val in record.items() if key != "sha256"})
            previous = record["sha256"]
        short_path = self.root / "original-short-broker-ttl.jsonl"
        short_path.write_bytes(
            b"\n".join(original_canonical(row) for row in rows) + b"\n")
        short_path.chmod(0o600)
        actual, _ = sealed_original_broker_expiry(short_path, self.spec)
        last = actual[-1]
        decision = last["available_at_ms"] + 1000
        self.assertLess(last["valid_until_ms"], decision)
        self.assertLess(decision-last["event_at_ms"], 5000)
        with self.assertRaisesRegex(ContractError, "FEATURE_QUOTE_EXPIRED"):
            compile_example(self.spec, actual, decision, decision)
        # A caller can rehash locally forged longer expiry, but cannot pass
        # a re-read of the original chain containing the shorter expiry.
        forged = copy.deepcopy(actual)
        forged[-1]["valid_until_ms"] = forged[-1]["event_at_ms"] + 5000
        forged_plan = register_plan(
            spec=self.spec, registered_at_ms=decision-40000,
            first_decision_ms=decision, last_decision_ms=decision+60000,
            minimum_mature_rows=1)
        forged_dataset = compile_dataset(
            self.spec, forged, [decision], decision)
        candidate = forged_dataset["examples"][0]
        forged_event = evidence_event(
            forged_plan, candidate, kind=SOURCE_EVENT,
            at_ms=last["available_at_ms"],
            evidence_sha256=candidate["features"]["input_receipts"][-1]["source_sha256"])
        self.assertEqual(candidate["label_status"], "PENDING_OUTCOME")
        with self.assertRaisesRegex(ContractError, "FEATURE_QUOTE_EXPIRED"):
            verify_original_paper_handoff(
                forged_plan, forged_dataset, forged_event,
                short_path, now_ms=last["available_at_ms"]+600)

    def test_original_ttl_changed_in_private_file_is_not_silent(self):
        raw = self.path.read_bytes()
        anchor = b'"stale_after_ms":'
        self.assertIn(anchor, raw)
        changed = raw.replace(anchor, b'"stale_after_ms":', 1)
        # Change one original quote expiry numeric value but not its checksum.
        import re
        changed = re.sub(rb'("stale_after_ms":)(\d+)',
                         lambda m: m.group(1)+str(int(m.group(2))+100).encode(),
                         changed, count=1)
        self.assertNotEqual(raw, changed)
        self.path.write_bytes(changed)
        with self.assertRaises(ValueError):
            sealed_original_broker_expiry(self.path, self.spec)

    def test_new_expiry_profile_must_be_explicit_and_live(self):
        changed = dict(self.old_spec)
        changed["quote_expiry_policy"] = BROKER_EXPIRY_POLICY
        with self.assertRaisesRegex(ContractError, "FEATURE_EXPIRY_POLICY_UNREGISTERED"):
            validate_spec(changed)
        with self.assertRaisesRegex(ContractError, "BROKER_EXPIRY_NOT_FOR_REVISED_HISTORY"):
            make_spec(self.spec["period_sha256"], "RESEARCH_POLICY",
                      historical=True, broker_reported_expiry=True)

    def test_mutation_between_two_original_reads_is_detected(self):
        from laya_cell.shadow_source import read_source
        true_reader = read_source

        def mutate(path):
            result = true_reader(path)
            with self.path.open("ab") as file:
                file.write(b'{"not":"an original record"}\n')
            return result

        with patch("laya_cell.shadow_source.read_source", side_effect=mutate):
            with self.assertRaisesRegex(
                    ContractError, "EXPIRY_SOURCE_CHANGED_BETWEEN_READS"):
                sealed_original_broker_expiry(self.path, self.spec)

    def test_source_timestamp_without_original_early_signature_not_backfilled(self):
        self.register_synthetic()
        new_at = self.decision + 2
        forged_event = evidence_event(
            self.plan, self.example, kind=SOURCE_EVENT,
            at_ms=new_at, evidence_sha256=self.event["evidence_sha256"])
        with self.assertRaisesRegex(ContractError, "ORIGINAL_EVENT_NOT_FRESH"):
            verify_original_paper_handoff(
                self.plan, self.dataset, forged_event, self.path,
                now_ms=new_at+4000)
