"""Phase 4V: prevent Python/native V1 source shape drift before live dispatch.

No model, broker, signer private key or native GPU required.
"""
import copy
import re
import unittest
from pathlib import Path

from train_v2.prewarmed_native_abstain_orchestrate import (
    NATIVE_INPUT_KEYS, validate_native_source_packet,
)


def packet():
    at = 1_900_000_000_000
    result = {
        "schema": "heptatrader.train-v2-prewarmed-native-abstain-input.v1",
        "test_only": True,
        "no_model": True,
        "no_orders": True,
        "original_source_independently_verified": True,
        "original_signed_source_fsynced": True,
        "production_root_enrolled": False,
        "model_prediction_emitted": False,
        "actual_broker_prepared": False,
        "economic_return_attested": False,
        "source_event_id": "a" * 64,
        "original_quote_receipt": "b" * 64,
        "first_source_cohort_id": "c" * 64,
        "independent_verifier_report_id": "d" * 64,
        "original_signed_source_proof_sha256": "e" * 64,
        "original_source_prefix_sha256": "f" * 64,
        "registered_at_ms": at - 40_000,
        "original_quote_event_ms": at - 350,
        "original_quote_available_ms": at,
        "original_quote_expiry_ms": at + 4500,
        "original_signed_source_durable_at_ms": at + 100,
        "original_source_decision_cutoff_ms": at + 650,
        "fixed_native_delay_ms": 3500,
        "candidate_universe": ["NO_TRADE"],
        "actual_selected_action": "NO_TRADE",
        "selected_logged_behavior_q32": 4_294_967_296,
        "trading_role": "NONE",
    }
    return result


class NativeAbstainInputTests(unittest.TestCase):
    def test_exact_expected_rust_json_field_set(self):
        source = Path(__file__).resolve().parents[2] / "research/train_v2/prewarmed_native_abstain.rs"
        self.assertTrue(source.is_file())
        rust = source.read_text()
        part = rust.split("let allowed = [", 1)[1].split("];", 1)[0]
        rust_fields = re.findall(r'"([a-z][a-z0-9_]*)"', part)
        self.assertEqual(len(rust_fields), len(set(rust_fields)))
        self.assertEqual(len(rust_fields), 27)
        self.assertEqual(set(rust_fields), NATIVE_INPUT_KEYS)

    def test_complete_single_no_trade_model_free_source_is_allowed(self):
        good = packet()
        self.assertEqual(set(good), NATIVE_INPUT_KEYS)
        self.assertIs(validate_native_source_packet(good), good)

    def test_old_actual_failure_missing_original_prefix_stays_failure(self):
        old = packet()
        del old["original_source_prefix_sha256"]
        with self.assertRaisesRegex(ValueError, "NATIVE_27_FIELD_SCHEMA_MISMATCH"):
            validate_native_source_packet(old)

    def test_new_unreviewed_field_cannot_be_used_as_authority(self):
        bad = packet()
        bad["product_root_authorized"] = True
        with self.assertRaisesRegex(ValueError, "NATIVE_27_FIELD_SCHEMA_MISMATCH"):
            validate_native_source_packet(bad)

    def test_no_trade_action_and_propensity_are_test_only(self):
        for key, value in (
            ("candidate_universe", ["BUY", "NO_TRADE"]),
            ("actual_selected_action", "BUY"),
            ("selected_logged_behavior_q32", 1),
            ("trading_role", "TRADER"),
            ("fixed_native_delay_ms", 4000),
        ):
            with self.subTest(key=key):
                bad = packet()
                bad[key] = value
                with self.assertRaisesRegex(ValueError, "NATIVE_TEST_POLICY_SCOPE"):
                    validate_native_source_packet(bad)

    def test_no_product_or_model_claim_is_ever_accepted(self):
        for key in ("production_root_enrolled", "model_prediction_emitted",
                    "actual_broker_prepared", "economic_return_attested"):
            with self.subTest(key=key):
                bad = packet()
                bad[key] = True
                with self.assertRaisesRegex(ValueError, "NATIVE_TEST_PRIVILEGE_CLAIM_FORBIDDEN"):
                    validate_native_source_packet(bad)

    def test_source_requires_original_hash_and_no_forged_ack(self):
        for key, value in (
            ("source_event_id", "z" * 64),
            ("original_quote_receipt", "deadbeef"),
            ("original_source_prefix_sha256", True),
        ):
            with self.subTest(key=key):
                bad = packet()
                bad[key] = value
                with self.assertRaisesRegex(ValueError, "NATIVE_ORIGINAL_SHA256_INVALID"):
                    validate_native_source_packet(bad)

    def test_original_expiry_and_predeclared_clock_fail_closed(self):
        for key, value in (
            ("original_quote_expiry_ms", packet()["original_quote_available_ms"] + 2000),
            ("registered_at_ms", packet()["original_quote_available_ms"] - 1000),
            ("original_signed_source_durable_at_ms", packet()["original_quote_available_ms"] + 1100),
            ("original_source_decision_cutoff_ms", packet()["original_quote_available_ms"] + 3550),
        ):
            with self.subTest(key=key):
                bad = packet()
                bad[key] = value
                with self.assertRaisesRegex(ValueError, "NATIVE_ORIGINAL_SOURCE_TIMELINE_MISMATCH"):
                    validate_native_source_packet(bad)

    def test_original_timestamp_bool_never_acts_as_integer(self):
        bad = packet()
        bad["registered_at_ms"] = True
        with self.assertRaisesRegex(ValueError, "NATIVE_SOURCE_TIMES_NOT_INTEGERS"):
            validate_native_source_packet(bad)

class NativeBuildIdentityTests(unittest.TestCase):
    def test_source_and_binary_pinned_before_dispatch(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from train_v2.prewarmed_native_abstain_orchestrate import verify_pinned_native_identity
        with patch("train_v2.prewarmed_native_abstain_orchestrate.verified_ssh",
                   return_value=SimpleNamespace(
                       stdout="a"*64+"  /lab/bin\n"+"b"*64+"  /lab/src\n")):
            r = verify_pinned_native_identity(
                "pocket4", Path("/lab/bin"), Path("/lab/src"),
                "a"*64, "b"*64)
            self.assertEqual(r["binary_sha256"], "a"*64)
            self.assertEqual(r["source_sha256"], "b"*64)
            self.assertEqual(r["broker_orders_sent"], 0)

    def test_wrong_native_binary_or_source_hash_refused(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from train_v2.prewarmed_native_abstain_orchestrate import verify_pinned_native_identity
        with patch("train_v2.prewarmed_native_abstain_orchestrate.verified_ssh",
                   return_value=SimpleNamespace(
                       stdout="f"*64+"  /lab/bin\n"+"b"*64+"  /lab/src\n")):
            with self.assertRaisesRegex(RuntimeError,
                                        "NATIVE_BINARY_OR_SOURCE_HASH_CHANGED"):
                verify_pinned_native_identity(
                    "pocket4", Path("/lab/bin"), Path("/lab/src"),
                    "a"*64, "b"*64)

    def test_native_hash_pin_missing_rejected_without_ssh(self):
        from unittest.mock import patch
        from train_v2.prewarmed_native_abstain_orchestrate import verify_pinned_native_identity
        with patch("train_v2.prewarmed_native_abstain_orchestrate.verified_ssh") as ssh:
            with self.assertRaisesRegex(ValueError,"NATIVE_BUILD_PIN_MISSING"):
                verify_pinned_native_identity(
                    "pocket4", Path("/lab/bin"), Path("/lab/src"),
                    "not-a-hash", "b"*64)
            ssh.assert_not_called()
