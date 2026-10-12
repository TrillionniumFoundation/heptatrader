"""Phase 4S: Native PRODUCT preaction chronology vs PRICE-only laboratory.

Never uses real trader credentials, model weights or product signatures.
Original Phase4L is inspected separately on pocket4, never imported into CI.
"""
import copy
from datetime import datetime, timezone
import hashlib
import unittest

from train_v2.native_preaction_timeline import (
    NATIVE_DOMAIN, BLOCKED, evaluate_chronology, native_generator_time,
)
from train_v2.contracts import ContractError


BASE = {
    "quote_at_ms": 100000,
    "quote_known_ms": 100400,
    "quote_expires_ms": 105000,
    "decision_at_ms": 102000,
    "signed_native_issued_ms": 102300,
    "signed_host_at_ms": 102523,
    "native_ack_at_ms": 102780,
    "outcome_due_ms": 402000,
    "prepared_at_ms": 103000,
    "original_llm_propensity": True,
}


class SignedChronologyTests(unittest.TestCase):
    def eval(self, **changes):
        data = {**BASE, **changes}
        return evaluate_chronology(**data)

    def test_only_hypothetical_complete_timeline_meets_local_constraints(self):
        result = self.eval()
        self.assertEqual(result["reasons"], [])
        self.assertTrue(result["research_preaction_timeline_constraints_met"])
        self.assertTrue(result["native_ack_before_original_prepared"])
        self.assertTrue(result["original_quote_valid_at_shadow_selection"])
        self.assertFalse(result["canonical_owner_or_trading_authority"])

    def test_actual_immutable_original_episode_misses_signature_and_ack_ttl(self):
        result = self.eval(
            quote_at_ms=1791767819984,
            quote_known_ms=1791767820389,
            quote_expires_ms=1791767824984,
            decision_at_ms=1791767821699,
            signed_native_issued_ms=1791767834437,
            signed_host_at_ms=1791767834660,
            native_ack_at_ms=1791767834922,
            outcome_due_ms=1791768121699,
            prepared_at_ms=None,
            original_llm_propensity=False,
        )
        self.assertTrue(result["original_quote_valid_at_shadow_selection"])
        self.assertTrue(result["native_preoutcome_only"])
        self.assertFalse(result["native_ack_before_quote_expiration"])
        self.assertEqual(result["remote_signed_after_quote_expiry_ms"], 9676)
        self.assertEqual(result["native_ack_after_quote_expiry_ms"], 9938)
        for issue in (
            "NATIVE_GENERATOR_ISSUED_AFTER_ORIGINAL_QUOTE_EXPIRED",
            "MAC_GENERATOR_SIGNED_AFTER_ORIGINAL_QUOTE_EXPIRED",
            "NATIVE_WITNESS_ACKED_AFTER_ORIGINAL_QUOTE_EXPIRED",
            "ORIGINAL_BROKER_PREPARED_IDENTITY_NOT_ATTESTED",
            "ORIGINAL_OPAQUE_LLM_BEHAVIOR_PROPENSITY_MISSING",
        ):
            self.assertIn(issue, result["reasons"])
        self.assertFalse(result["research_preaction_timeline_constraints_met"])

    def test_valid_price_at_shadow_selection_does_not_backdate_later_signatures(self):
        result = self.eval(
            signed_native_issued_ms=105001, signed_host_at_ms=105100,
            native_ack_at_ms=105300, prepared_at_ms=106000,
        )
        self.assertTrue(result["original_quote_valid_at_shadow_selection"])
        self.assertFalse(result["native_issued_before_quote_expiration"])
        self.assertFalse(result["native_ack_before_quote_expiration"])
        self.assertFalse(result["research_preaction_timeline_constraints_met"])

    def test_signature_in_time_but_witness_ack_late_does_not_qualify(self):
        result = self.eval(native_ack_at_ms=105001, prepared_at_ms=106000)
        self.assertTrue(result["remote_signed_before_quote_expiration"])
        self.assertIn("NATIVE_WITNESS_ACKED_AFTER_ORIGINAL_QUOTE_EXPIRED",
                      result["reasons"])

    def test_signature_early_but_persisted_after_original_prepared_refused(self):
        result = self.eval(prepared_at_ms=102600)
        self.assertIn("NATIVE_DECISION_OR_WITNESS_AFTER_ORIGINAL_PREPARED",
                      result["reasons"])

    def test_product_original_prepared_missing_is_never_assumed(self):
        result = self.eval(prepared_at_ms=None)
        self.assertFalse(result["original_prepared_identity_attested"])
        self.assertIn("ORIGINAL_BROKER_PREPARED_IDENTITY_NOT_ATTESTED",
                      result["reasons"])

    def test_model_confidence_cannot_replace_original_propensity(self):
        result = self.eval(original_llm_propensity=False)
        self.assertIn("ORIGINAL_OPAQUE_LLM_BEHAVIOR_PROPENSITY_MISSING",
                      result["reasons"])

    def test_very_late_signature_still_signed_does_not_count_preoutcome(self):
        result = self.eval(
            signed_native_issued_ms=402001,
            signed_host_at_ms=402004,
            native_ack_at_ms=402050,
            prepared_at_ms=403000,
        )
        self.assertIn("NATIVE_RESEARCH_DECISION_NOT_PREOUTCOME",
                      result["reasons"])

    def test_original_quote_already_expired_when_shadow_selected(self):
        result = self.eval(decision_at_ms=105001, outcome_due_ms=405001)
        self.assertIn("ORIGINAL_DECISION_OUTSIDE_KNOWN_QUOTE_TTL",
                      result["reasons"])

    def test_future_or_not_yet_received_quote_not_original_pit(self):
        result = self.eval(decision_at_ms=100200, outcome_due_ms=400200)
        self.assertIn("ORIGINAL_DECISION_OUTSIDE_KNOWN_QUOTE_TTL",
                      result["reasons"])

    def test_expiry_can_never_exceed_original_quote_plus_five_seconds(self):
        with self.assertRaisesRegex(
                ContractError, "NATIVE_PREACTION_ORIGINAL_QUOTE_CLOCK"):
            self.eval(quote_expires_ms=106000)

    def test_expiry_prior_to_original_local_arrival_refused(self):
        with self.assertRaisesRegex(
                ContractError, "NATIVE_PREACTION_ORIGINAL_QUOTE_CLOCK"):
            self.eval(quote_expires_ms=100399)

    def test_signing_or_witness_temporal_inversion_refused(self):
        for values in (
            {"signed_native_issued_ms":102600},
            {"native_ack_at_ms":102522},
            {"signed_host_at_ms":103100},
        ):
            with self.subTest(values=values), self.assertRaisesRegex(
                    ContractError, "NATIVE_PREACTION_SIGNER_OR_WITNESS_ORDER"):
                self.eval(**values)

    def test_original_outcome_horizon_cannot_be_extended(self):
        with self.assertRaisesRegex(
                ContractError, "NATIVE_PREACTION_OUTCOME_HORIZON_CHANGED"):
            self.eval(outcome_due_ms=402001)

    def test_timestamp_fields_must_be_real_integers_not_python_bool(self):
        for key in (
            "quote_at_ms", "quote_known_ms", "quote_expires_ms",
            "decision_at_ms", "signed_native_issued_ms",
            "signed_host_at_ms", "native_ack_at_ms", "outcome_due_ms",
            "prepared_at_ms",
        ):
            with self.subTest(key=key), self.assertRaises(ContractError):
                self.eval(**{key: True})

    def test_bogus_model_propensity_truth_claim_requires_explicit_bool(self):
        for value in (1, "True", None):
            with self.subTest(value=value), self.assertRaisesRegex(
                    ContractError, "NATIVE_PREACTION_PROPENSITY_ATTESTATION_TYPE"):
                self.eval(original_llm_propensity=value)


def native_message(*, role="lab.generator", tag=0, issued=102300,
                   expiry=192300, authority_epoch=19):
    def native_id(text):
        data = text.encode("ascii")
        return len(data).to_bytes(8,"big") + data
    return (
        NATIVE_DOMAIN
        + native_id("lab.synthetic.test.evidence")
        + native_id(role) + bytes([tag])
        + b"".join(hashlib.sha256(n).digest() for n in
                   (b"TEST_TRUST",b"TEST_SCOPE",b"TEST_OBJECTIVE"))
        + authority_epoch.to_bytes(8,"big")
        + issued.to_bytes(8,"big")
        + expiry.to_bytes(8,"big")
        + hashlib.sha256(b"TEST_PAYLOAD").digest()
    )


class NativeTimeByteLayoutTests(unittest.TestCase):
    def record(self, raw=None, *, stamp=102523):
        dt=datetime.fromtimestamp(stamp/1000,tz=timezone.utc)
        return {
            "role":"generator",
            "public_message_hex": (native_message() if raw is None else raw).hex(),
            "signed_at_utc":dt.isoformat(timespec="milliseconds"),
        }

    def test_real_canonical_field_order_yields_issuance_after_decision(self):
        result = native_generator_time(self.record())
        self.assertEqual(result["native_authority_epoch"],19)
        self.assertEqual(result["native_issued_at_ms"],102300)
        self.assertEqual(result["remote_reported_signed_at_ms"],102523)
        self.assertTrue(result["native_timestamp_parser_does_not_verify_signature"])
        self.assertEqual(result["native_principal"],"lab.generator")

    def test_wrong_role_or_native_tag_refuses_without_signature_promotion(self):
        with self.assertRaisesRegex(ContractError,"NATIVE_TIMELINE_NOT_GENERATOR"):
            native_generator_time({**self.record(),"role":"observer"})
        with self.assertRaisesRegex(ContractError,"NATIVE_TIMELINE_WRONG_ROLE"):
            native_generator_time(self.record(native_message(tag=1)))

    def test_invalid_principal_refuses(self):
        with self.assertRaisesRegex(ContractError,"NATIVE_TIMELINE_WRONG_PRINCIPAL"):
            native_generator_time(self.record(native_message(role="lab.observer")))

    def test_native_signed_times_must_be_monotonic_and_current(self):
        with self.assertRaisesRegex(ContractError,"NATIVE_TIMELINE_ISSUER_PERIOD"):
            native_generator_time(self.record(native_message(expiry=102299)))
        with self.assertRaisesRegex(ContractError,"NATIVE_TIMELINE_ISSUER_CLOCK_INCONSISTENT"):
            native_generator_time(self.record(stamp=107301))

    def test_bad_evidence_domain_or_truncated_typed_fields_refused(self):
        with self.assertRaises(ContractError):
            native_generator_time(self.record(b"not-native"))
        with self.assertRaises(ContractError):
            native_generator_time(self.record(native_message()[:-8]))

    def test_double_checked_actual_native_times_never_grant_owner(self):
        result = native_generator_time(self.record())
        self.assertNotIn("canonical_product_owner",result)
        self.assertNotIn("broker_orders_sent",result)
        self.assertFalse(result.get("native_public_key_validated_by_earlier_verifier", False))
