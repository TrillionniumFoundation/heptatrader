"""Independent recovery and timeline refusal tests; no broker/native writes."""
from __future__ import annotations
import os
from pathlib import Path
import tempfile
import unittest

from train_v2.native_fresh_readback import (
    verify_timeline, read_private, create_private, write_once, digest
)


def fixture():
    start = 1_900_000_000_000
    event = start + 30_000
    available = event + 400
    root = {
        "status": "TEST_ONLY_ACTUAL_SOURCE_SIGNED_AND_LOCALLY_INDEXED",
        "source_event_id": "a"*64,
        "original_first_source_cohort_id": "b"*64,
        "signed_proof_sha256": "c"*64,
        "real_original_quote_at_ms": event,
        "real_original_quote_available_ms": available,
        "real_original_quote_expiry_ms": event + 5000,
        "true_new_source_decision_cutoff_ms": available + 600,
        "signed_proof_durable_at_ms": available + 50,
    }
    native = {
        "source_event_id": "a"*64,
        "first_source_cohort_id": "b"*64,
        "original_signed_source_receipt_sha256": "c"*64,
        "original_independent_verifier_report_id": "d"*64,
        "source_available_at_ms": available,
        "quote_expiry_ms": event + 5000,
        "selected_action": "NO_TRADE",
        "candidate_universe": ["NO_TRADE"],
        "selected_logged_q32_propensity": 4294967296,
        "native_witness_sequence": 1,
        "root_and_role_test_only": True,
        "no_model_inference": True,
        "original_canonical_writer_executed": True,
        "production_authority": False,
        "economic_reward_observed": False,
        "original_opaque_llm_propensity_used": False,
        "cross_host_wallclock_order_used_for_admission": False,
        "broker_orders_sent": 0,
        "native_precommitted_deadline_ms": available + 3500,
        "native_issuer_request_at_ms": available + 100,
        "pocket4_received_signature_at_ms": available + 110,
        "native_witness_ack_at_ms": available + 120,
    }
    plan = {
        "precommitted_at_ms": start,
        "fixed_candidate_set": ["NO_TRADE"],
        "selected_q32_propensity": 4294967296,
        "fixed_decision_delay_ms": 3500,
        "broker_order_authority": False,
        "model_inference": False,
    }
    independently_verified = {
        "original_source_original_chain_verified": True,
        "public_mac_ed25519_signature_reverified": True,
        "precisely_one_causal_research_source_event": True,
        "report_id": "d"*64,
    }
    return root,native,plan,independently_verified


class NativeIndependentTimelineTests(unittest.TestCase):
    def test_authentic_test_only_deadlines_have_positive_margin(self):
        s,a,p,i=fixture()
        result=verify_timeline(s,a,p,i)
        self.assertGreater(result["ack_before_native_deadline_ms"],0)
        self.assertGreater(result["ack_before_original_ib_expiry_ms"],0)
        self.assertTrue(result["crosshost_wallclock_order_not_used"])

    def test_wrong_original_source_event_not_tolerated(self):
        s,a,p,i=fixture();a["source_event_id"]="e"*64
        with self.assertRaisesRegex(ValueError,"SOURCE_NATIVE_ACK_IDENTITY"):
            verify_timeline(s,a,p,i)

    def test_outcome_without_independent_original_signature_refused(self):
        s,a,p,i=fixture();i["public_mac_ed25519_signature_reverified"]=False
        with self.assertRaisesRegex(ValueError,"INDEPENDENT_SOURCE_REVIEW"):
            verify_timeline(s,a,p,i)

    def test_changed_report_id_refused(self):
        s,a,p,i=fixture();a["original_independent_verifier_report_id"]="f"*64
        with self.assertRaisesRegex(ValueError,"SOURCE_NATIVE_ACK_IDENTITY"):
            verify_timeline(s,a,p,i)

    def test_false_product_authority_and_reward_refused(self):
        for field in ("production_authority","economic_reward_observed",
                      "original_opaque_llm_propensity_used",
                      "cross_host_wallclock_order_used_for_admission"):
            s,a,p,i=fixture();a[field]=True
            with self.subTest(field=field),self.assertRaisesRegex(
                ValueError,"FALSE_PRODUCT_AUTHORITY"):
                verify_timeline(s,a,p,i)

    def test_fake_trading_action_or_probability_refused(self):
        for field,bad in (("selected_action","BUY"),
                          ("candidate_universe",["BUY","SELL"]),
                          ("selected_logged_q32_propensity",.6)):
            s,a,p,i=fixture();a[field]=bad
            with self.subTest(field=field),self.assertRaisesRegex(
                ValueError,"SOURCE_NATIVE_ACK_IDENTITY"):
                verify_timeline(s,a,p,i)

    def test_native_ack_after_original_source_cutoff_but_before_later_deadline_refused(self):
        s,a,p,i=fixture()
        cutoff=s["true_new_source_decision_cutoff_ms"]
        self.assertLess(cutoff+1,a["native_precommitted_deadline_ms"])
        self.assertLess(cutoff+1,s["real_original_quote_expiry_ms"])
        a["native_witness_ack_at_ms"]=cutoff+1
        with self.assertRaisesRegex(ValueError,"TIMELINE_NOT_CAUSAL"):
            verify_timeline(s,a,p,i)

    def test_native_ack_at_exact_source_cutoff_is_not_backdated(self):
        s,a,p,i=fixture()
        a["native_witness_ack_at_ms"]=s["true_new_source_decision_cutoff_ms"]
        verified=verify_timeline(s,a,p,i)
        self.assertEqual(verified["ack_before_source_decision_cutoff_ms"],0)

    def test_witness_acked_after_original_expiry_refused(self):
        s,a,p,i=fixture();a["native_witness_ack_at_ms"]=s["real_original_quote_expiry_ms"]+1
        with self.assertRaisesRegex(ValueError,"TIMELINE_NOT_CAUSAL"):
            verify_timeline(s,a,p,i)

    def test_native_response_arriving_after_ack_is_refused(self):
        s,a,p,i=fixture();a["pocket4_received_signature_at_ms"]=a["native_witness_ack_at_ms"]+1
        with self.assertRaisesRegex(ValueError,"TIMELINE_NOT_CAUSAL"):
            verify_timeline(s,a,p,i)

    def test_bool_cannot_masquerade_as_quote_time(self):
        s,a,p,i=fixture();a["native_issuer_request_at_ms"]=True
        with self.assertRaisesRegex(ValueError,"TIMELINE_NOT_CAUSAL"):
            verify_timeline(s,a,p,i)

    def test_test_root_cannot_claim_full_original_model(self):
        s,a,p,i=fixture();a["no_model_inference"]=False
        with self.assertRaisesRegex(ValueError,"TEST_ONLY_ORIGINAL_NATIVE_SCOPE"):
            verify_timeline(s,a,p,i)


class NativeIndependentFileCopyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.root.chmod(0o700)

    def test_birth_private_copy_and_immutable_duplicate_denial(self):
        folder=create_private(self.root,"only-once")
        source=write_once(folder,"native-ledger.bin",b"HEAD"+b"\x00"*20)
        self.assertEqual(source.stat().st_mode & 0o777,0o600)
        self.assertEqual(folder.stat().st_mode & 0o777,0o700)
        self.assertEqual(read_private(source),b"HEAD"+b"\x00"*20)
        with self.assertRaises(FileExistsError):
            write_once(folder,"native-ledger.bin",b"changed")

    def test_original_private_source_with_public_mode_refused(self):
        p=write_once(self.root,"ack",b"data")
        p.chmod(0o644)
        with self.assertRaisesRegex(ValueError,"PRIVATE_SOURCE_OWNER_MODE"):
            read_private(p)

    def test_symlink_to_another_file_is_never_followed(self):
        real=write_once(self.root,"real",b"source")
        link=self.root/"linked"
        link.symlink_to(real)
        with self.assertRaisesRegex(ValueError,"PRIVATE_SOURCE_SYMLINK"):
            read_private(link)

    def test_preexisting_copy_directory_refuses_rewrite(self):
        create_private(self.root,"same")
        with self.assertRaisesRegex(ValueError,"COPY_ALREADY_EXISTS_NO_RETRY"):
            create_private(self.root,"same")

    def test_file_size_bound_enforced_before_materialization(self):
        p=write_once(self.root,"oversized",b"x"*64)
        with self.assertRaisesRegex(ValueError,"PRIVATE_SOURCE_OWNER_MODE_OR_SIZE"):
            read_private(p,max_bytes=63)

    def test_checksum_detects_one_bit_modified_copy(self):
        signed=write_once(self.root,"signed",b"a"*160)
        change=bytearray(read_private(signed))
        change[100]^=1
        attacked=write_once(self.root,"copy",bytes(change))
        self.assertNotEqual(digest(read_private(attacked)),digest(read_private(signed)))
        self.assertEqual(read_private(signed),b"a"*160)
