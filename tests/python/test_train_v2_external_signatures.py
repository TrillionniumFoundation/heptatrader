"""Adversarial external signer boundary. Keys are TEST-ONLY and never installed."""
import base64
import copy
import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from historical_training.common import canonical, sha
from train_v2.contracts import ContractError, digest
from train_v2.orchestrator import (
    SOURCE_EVENT, OUTCOME_EVENT, CORRECTION_EVENT, evidence_event, register_plan,
)
from train_v2.external_signatures import (
    TRUST_SCHEMA, WITNESS_SCHEMA, signed_bytes, validate_plan_witness,
    validate_event_witness, verify_trust,
)
from train_v2.signed_handoff import (
    register_externally_witnessed, verify_external_registration,
    ingest_signed_original, SIGNED_LEDGER,
)
from test_train_v2_phase2 import OriginalSourceFixture


class ExternalSignedResearchTests(OriginalSourceFixture):
    def setUp(self):
        super().setUp()
        self.registry = self.root / "new-external-registry"
        self.declared = self.decision - 40000
        self.plan = register_plan(
            spec=self.spec, registered_at_ms=self.declared,
            first_decision_ms=self.decision, last_decision_ms=self.decision + 60000,
            minimum_mature_rows=1)
        self.event = evidence_event(
            self.plan, self.example, kind=SOURCE_EVENT, at_ms=self.decision,
            evidence_sha256=self.example["features"]["input_receipts"][-1]["source_sha256"])
        self.keys = {name: Ed25519PrivateKey.generate()
                     for name in ("registrar", "producer", "observer")}
        pubs = {
            name: key.public_key().public_bytes(
                serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()
            for name, key in self.keys.items()
        }
        self.trust = {
            "schema": TRUST_SCHEMA,
            "period_sha256": self.spec["period_sha256"],
            "instrument": "EUR.USD",
            "policy_id": self.plan["behavior_policy_id"],
            "trust_generation": 3,
            "not_before_ms": self.declared - 2000,
            "expires_at_ms": self.declared + 86400000,
            "roles": pubs,
            "revoked_role_keys": [],
            "issuer_kind": "OPERATOR_PINNED_EXTERNAL_RESEARCH_ONLY",
            "canonical_agentd_owner_enrolled": False,
            "trade_authority": False,
        }
        self.trust_file = self.root / "trusted-pubs.json"
        self.save_trust()
        self.registrar = self.issue(
            "registrar", "PLAN", self.plan["plan_id"],
            digest({"plan": self.plan, "feature_spec": self.spec,
                    "original_source_root": str(self.root),
                    "registry_root": str(self.registry)}), self.declared)
        self.producer = self.issue(
            "producer", "EVENT", self.event["event_id"],
            digest({"event": self.event, "original_source_file": str(self.path),
                    "registry_root": str(self.registry)}), self.decision)

    def save_trust(self):
        self.trust_file.write_bytes(canonical(self.trust)+b"\n")
        self.trust_file.chmod(0o600)
        self.pin = sha(self.trust_file)

    def issue(self, role, kind, bound_id, binding, at):
        record = {
            "schema": WITNESS_SCHEMA, "kind": kind, "role": role,
            "plan_id": self.plan["plan_id"], "bound_id": bound_id,
            "binding_sha256": binding,
            "trust_sha256": self.pin,
            "period_sha256": self.plan["period_sha256"],
            "policy_id": self.plan["behavior_policy_id"],
            "authority_effect": "NONE",
            "nonce": digest({"role": role, "bound": bound_id, "issued": at}),
            "issued_at_ms": at,
            "expires_at_ms": at+100000,
        }
        record["signature"] = base64.b64encode(
            self.keys[role].sign(signed_bytes(record))).decode()
        return record

    def registered(self):
        with patch("train_v2.registry.time.time_ns",
                   return_value=self.declared * 1000000):
            return register_externally_witnessed(
                self.registry, self.plan, self.spec, self.root,
                self.trust_file, self.pin, self.registrar, min_generation=3)

    def test_distinct_real_crypto_roles_and_no_canonical_trading_authority(self):
        result = self.registered()
        self.assertTrue(result["signed_registration"])
        self.assertFalse(result["canonical_agentd_owner_enrolled"])
        self.assertEqual(result["broker_orders_sent"], 0)
        self.assertTrue((self.registry/"external-plan-witnesses"/
                         (self.plan["plan_id"]+".json")).is_file())
        verified = verify_external_registration(
            self.registry, self.plan, self.spec, self.path,
            self.trust_file, self.pin, min_generation=3)
        self.assertTrue(verified[2]["signature_verified"])
        first = ingest_signed_original(
            self.registry, self.plan, self.dataset, self.event, self.path,
            self.trust_file, self.pin, self.producer, min_generation=3)
        second = ingest_signed_original(
            self.registry, self.plan, self.dataset, self.event, self.path,
            self.trust_file, self.pin, self.producer, min_generation=3)
        self.assertEqual(first["state"], "INPUT_RECORDED_SHADOW_ONLY")
        self.assertEqual(second["state"], "DUPLICATE_NO_EFFECT")
        self.assertFalse(first["canonical_agentd_owner_enrolled"])
        self.assertFalse(first["automatic_training"])
        self.assertFalse(first["model_selected"])
        with sqlite3.connect(self.registry / SIGNED_LEDGER) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM events").fetchone()[0], 1)

    def test_unregistered_plan_rejected_with_valid_producer(self):
        with self.assertRaisesRegex(ContractError, "REGISTRY_UNREGISTERED_PLAN"):
            ingest_signed_original(
                self.registry, self.plan, self.dataset, self.event, self.path,
                self.trust_file, self.pin, self.producer, min_generation=3)

    def test_wrong_issuer_role_refused_for_source_event(self):
        self.registered()
        fake = self.issue("observer", "EVENT", self.event["event_id"],
                          self.producer["binding_sha256"], self.decision)
        with self.assertRaisesRegex(ContractError, "EXTERNAL_WITNESS_SCOPE_OR_REPLAY"):
            ingest_signed_original(
                self.registry, self.plan, self.dataset, self.event, self.path,
                self.trust_file, self.pin, fake, min_generation=3)

    def test_modified_or_fake_signature_refused(self):
        self.registered()
        fake = copy.deepcopy(self.producer)
        fake["nonce"] = digest({"different": 1})
        with self.assertRaisesRegex(ContractError, "EXTERNAL_SIGNATURE_INVALID"):
            ingest_signed_original(
                self.registry, self.plan, self.dataset, self.event, self.path,
                self.trust_file, self.pin, fake, min_generation=3)
        fake = copy.deepcopy(self.producer)
        fake["signature"] = "bad"
        with self.assertRaisesRegex(ContractError, "EXTERNAL_SIGNATURE_ENCODING"):
            ingest_signed_original(
                self.registry, self.plan, self.dataset, self.event, self.path,
                self.trust_file, self.pin, fake, min_generation=3)

    def test_wrong_registry_or_original_segment_binding_refused(self):
        self.registered()
        other = self.root/"other-quotes.jsonl"
        other.write_bytes(self.path.read_bytes())
        other.chmod(0o600)
        with self.assertRaisesRegex(ContractError, "EXTERNAL_WITNESS_SCOPE_OR_REPLAY"):
            ingest_signed_original(
                self.registry, self.plan, self.dataset, self.event, other,
                self.trust_file, self.pin, self.producer, min_generation=3)
        with self.assertRaisesRegex(ContractError, "EXTERNAL_WITNESS_SCOPE_OR_REPLAY"):
            validate_event_witness(
                self.trust, self.pin, self.plan, self.event, self.path,
                self.root/"different-registry", self.producer,
                now_ms=self.decision, min_generation=3)

    def test_key_identity_roles_must_be_distinct(self):
        compromised = copy.deepcopy(self.trust)
        compromised["roles"]["observer"] = compromised["roles"]["producer"]
        compromised_pin = hashlib.sha256(canonical(compromised)+b"\n").hexdigest()
        with self.assertRaisesRegex(ContractError, "EXTERNAL_TRUST_KEYS_NOT_INDEPENDENT"):
            verify_trust(compromised, compromised_pin, self.plan,
                         min_generation=3, now_ms=self.declared)

    def test_expired_wrong_generation_or_revoked_trust_fails_closed(self):
        self.registered()
        with self.assertRaisesRegex(ContractError, "EXTERNAL_TRUST_GENERATION_ROLLBACK"):
            ingest_signed_original(
                self.registry, self.plan, self.dataset, self.event, self.path,
                self.trust_file, self.pin, self.producer, min_generation=4)
        expired = copy.deepcopy(self.producer)
        expired["issued_at_ms"] = self.declared
        with self.assertRaises(ContractError):
            ingest_signed_original(
                self.registry, self.plan, self.dataset, self.event, self.path,
                self.trust_file, self.pin, expired, min_generation=3)
        revoked = copy.deepcopy(self.trust)
        revoked["revoked_role_keys"] = ["producer"]
        newpin = hashlib.sha256(canonical(revoked)+b"\n").hexdigest()
        with self.assertRaisesRegex(ContractError, "EXTERNAL_ROLE_KEY_REVOKED"):
            validate_event_witness(
                revoked, newpin, self.plan, self.event, self.path,
                self.registry, {**self.producer, "trust_sha256": newpin},
                now_ms=self.decision, min_generation=3)
        # Original anchor cannot silently accept newly supplied trust material.
        self.trust = revoked
        self.save_trust()
        with self.assertRaisesRegex(ContractError, "EXTERNAL_TRUST_FILE_CHANGED"):
            ingest_signed_original(
                self.registry, self.plan, self.dataset, self.event, self.path,
                self.trust_file, self.pin if False else
                self.registrar["trust_sha256"], self.producer, min_generation=3)

    def test_unsigned_or_late_plan_refused(self):
        fake = copy.deepcopy(self.registrar)
        fake.pop("signature")
        with patch("train_v2.registry.time.time_ns",
                   return_value=self.declared * 1000000):
            with self.assertRaisesRegex(ContractError, "EXTERNAL_WITNESS_SHAPE"):
                register_externally_witnessed(
                    self.registry, self.plan, self.spec, self.root,
                    self.trust_file, self.pin, fake, min_generation=3)
        with self.assertRaisesRegex(ContractError, "EXTERNAL_WITNESS_EXPIRED_REPLAY"):
            validate_plan_witness(
                self.trust, self.pin, self.plan, self.spec, self.root,
                self.registry, self.registrar, now_ms=self.decision,
                min_generation=3)

    def test_correction_cannot_use_producer_or_observer(self):
        self.registered()
        ev = evidence_event(self.plan, self.example, kind=CORRECTION_EVENT,
                            at_ms=self.decision,
                            evidence_sha256=digest({"correction": 1}))
        with self.assertRaisesRegex(ContractError, "EXTERNAL_CORRECTION_REQUIRES_CANONICAL_OWNER"):
            ingest_signed_original(
                self.registry, self.plan, self.dataset, ev, self.path,
                self.trust_file, self.pin, self.producer, min_generation=3)

    def test_external_registrar_seal_tampering_fails_final_use(self):
        self.registered()
        file = self.registry/"external-plan-witnesses"/(self.plan["plan_id"]+".json")
        saved = json.loads(file.read_text())
        saved["registrar_witness"]["nonce"] = digest({"malicious": 1})
        file.chmod(0o600)
        file.write_bytes(canonical(saved)+b"\n")
        with self.assertRaises(ContractError):
            verify_external_registration(
                self.registry, self.plan, self.spec, self.path,
                self.trust_file, self.pin, min_generation=3)

    def test_signed_cli_preserves_exact_witness_and_deduplicates(self):
        import os
        import subprocess
        import sys
        from contextlib import closing
        self.registered()
        for label, value in (("plan", self.plan), ("dataset", self.dataset),
                             ("event", self.event), ("witness", self.producer)):
            file = self.root / ("signed-cli-"+label+".json")
            file.write_bytes(canonical(value)+b"\n")
            file.chmod(0o600)
        cmd = [
            sys.executable, "-B", "-m", "train_v2.signed_research_cli", "event",
            "--registry-root", str(self.registry),
            "--plan", str(self.root/"signed-cli-plan.json"),
            "--dataset", str(self.root/"signed-cli-dataset.json"),
            "--event", str(self.root/"signed-cli-event.json"),
            "--witness", str(self.root/"signed-cli-witness.json"),
            "--original-segment", str(self.path),
            "--trust-snapshot", str(self.trust_file),
            "--pinned-trust-sha256", self.pin,
            "--min-trust-generation", "3",
        ]
        first = subprocess.run(cmd, capture_output=True, text=True,
                               env=dict(os.environ), timeout=10)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(json.loads(first.stdout)["state"],
                         "INPUT_RECORDED_SHADOW_ONLY")
        second = subprocess.run(cmd, capture_output=True, text=True,
                                env=dict(os.environ), timeout=10)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(json.loads(second.stdout)["state"],
                         "DUPLICATE_NO_EFFECT")
        with closing(sqlite3.connect(self.registry/SIGNED_LEDGER)) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM events").fetchone()[0], 1)

    def test_legacy_clis_cannot_write_signed_registry_ledger(self):
        import subprocess
        import sys
        for module in ("train_v2.event_ingest",
                       "train_v2.original_event_ingest"):
            cmd = [sys.executable, "-B", "-m", module,
                   "--plan", str(self.root/"absent-plan.json"),
                   "--dataset", str(self.root/"absent-dataset.json"),
                   "--event", str(self.root/"absent-event.json"),
                   *([] if module == "train_v2.event_ingest" else
                     ["--original-segment", str(self.path)]),
                   "--ledger", str(self.registry/SIGNED_LEDGER)]
            out = subprocess.run(cmd, capture_output=True,
                                 text=True, timeout=5)
            self.assertNotEqual(out.returncode, 0)
            self.assertIn("REGISTERED_LEDGER_REQUIRES", out.stderr)

    def test_registrar_source_root_signature_cannot_move_registry(self):
        other_root = self.root/"other-research-registry"
        with patch("train_v2.registry.time.time_ns",
                   return_value=self.declared*1000000):
            with self.assertRaisesRegex(ContractError,
                                        "EXTERNAL_WITNESS_SCOPE_OR_REPLAY"):
                register_externally_witnessed(
                    other_root, self.plan, self.spec, self.root,
                    self.trust_file, self.pin, self.registrar,
                    min_generation=3)

    def test_operator_pinned_hash_cannot_be_replaced_by_signer(self):
        with patch("train_v2.registry.time.time_ns",
                   return_value=self.declared*1000000):
            with self.assertRaisesRegex(ContractError,
                                        "EXTERNAL_TRUST_FILE_CHANGED"):
                register_externally_witnessed(
                    self.registry, self.plan, self.spec, self.root,
                    self.trust_file, digest({"not": "the operator pin"}),
                    self.registrar, min_generation=3)

    def test_signed_observer_cannot_invent_mature_market_outcome(self):
        self.registered()
        imaginary = evidence_event(
            self.plan, self.example, kind=OUTCOME_EVENT,
            at_ms=self.decision,
            evidence_sha256=self.event["evidence_sha256"])
        observer = self.issue(
            "observer", "EVENT", imaginary["event_id"],
            digest({"event": imaginary, "original_source_file": str(self.path),
                    "registry_root": str(self.registry)}), self.decision)
        ok = validate_event_witness(
            self.trust, self.pin, self.plan, imaginary, self.path,
            self.registry, observer, now_ms=self.decision, min_generation=3)
        self.assertTrue(ok["signature_verified"])
        with self.assertRaises(ContractError):
            ingest_signed_original(
                self.registry, self.plan, self.dataset, imaginary, self.path,
                self.trust_file, self.pin, observer, min_generation=3)
        self.assertFalse((self.registry/SIGNED_LEDGER).exists())

    def test_valid_producer_signature_after_original_deadline_rejected(self):
        self.registered()
        late = self.issue(
            "producer", "EVENT", self.event["event_id"],
            self.producer["binding_sha256"], self.decision+1)
        with self.assertRaisesRegex(ContractError, "EXTERNAL_PRODUCER_SIGNED_TOO_LATE"):
            validate_event_witness(
                self.trust, self.pin, self.plan, self.event, self.path,
                self.registry, late, now_ms=self.decision+1, min_generation=3)

    def test_missing_external_registration_seal_fails_final_use(self):
        self.registered()
        (self.registry/"external-plan-witnesses"/
         (self.plan["plan_id"]+".json")).unlink()
        with self.assertRaisesRegex(ContractError, "EXTERNAL_PRESEALED_WITNESS_MISSING"):
            ingest_signed_original(
                self.registry, self.plan, self.dataset, self.event, self.path,
                self.trust_file, self.pin, self.producer, min_generation=3)

    def test_owner_claim_can_never_be_smuggled_through_signed_record(self):
        changed = copy.deepcopy(self.registrar)
        changed["canonical_agentd_owner_enrolled"] = True
        with self.assertRaisesRegex(ContractError, "EXTERNAL_WITNESS_SHAPE"):
            validate_plan_witness(
                self.trust, self.pin, self.plan, self.spec,
                self.root, self.registry, changed,
                now_ms=self.declared, min_generation=3)

    def test_real_final_use_requires_current_external_public_trust(self):
        self.registered()
        self.trust_file.unlink()
        with self.assertRaisesRegex(ContractError, "EXTERNAL_TRUST_FILE_MISSING"):
            verify_external_registration(
                self.registry, self.plan, self.spec, self.path,
                self.trust_file, self.pin, min_generation=3)

    def test_current_role_signature_is_genuinely_ed25519(self):
        result = validate_event_witness(
            self.trust, self.pin, self.plan, self.event, self.path,
            self.registry, self.producer, now_ms=self.decision, min_generation=3)
        self.assertTrue(result["signature_verified"])
        self.assertFalse(result["canonical_agentd_admitted"])
