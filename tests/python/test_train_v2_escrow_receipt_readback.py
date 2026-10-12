"""Synthetic-only offline source-intent escrow readback adversarial tests.

No outside key, live broker, production root, external host or remote fsync
is created or invoked. A fake host path and locally generated public key
are not physical-machine custody evidence.
"""
from __future__ import annotations
import hashlib
import json
import time
import unittest

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat

from train_v2 import escrow_receipt_readback as checker
from train_v2.real_source_native_remote_escrow import canonical,DOMAIN,OWNERS,SCHEMA,RECEIPT_SCHEMA


class SyntheticOfflineRemoteReadbackTests(unittest.TestCase):
    def setUp(self):
        self.key=Ed25519PrivateKey.generate()
        self.pub=self.key.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw).hex()
        self.now=time.time_ns()//1000000
        self.stage="decision"
        self.intent={
          "schema":SCHEMA,"stage":"decision","native_signer":"generator",
          "source_policy":"SHADOW_RESEARCH_PREACTION_TWO_BIT_NO_ORDER_V1",
          "source_quote_receipt":"a"*64,
          "test_source_packet_sha256":"b"*64,
          "original_native_payload_sha256":"c"*64,
          "original_native_evidence_message_sha256":"d"*64,
          "original_detached_signature_sha256":"e"*64,
          "expected_predecessor_chain_digest":"0"*64,
          "expected_previous_witness_sequence":0,
          "next_ledger_sequence":1,
          "source_time_ms":self.now-1300,
          "outcome_due_ms":self.now+300000,
          "test_only":True,"product_trading_authority":False,
        }
        self.sign()

    def sign(self):
        when=self.now
        data=canonical(self.intent)
        signature=self.key.sign(DOMAIN+data+when.to_bytes(8,"big"))
        self.receipt={
          "schema":RECEIPT_SCHEMA,"body":self.intent,
          "body_sha256":hashlib.sha256(data).hexdigest(),
          "intent_held_at_ms":when,
          "signing_host":"qian-ThinkPad-X230",
          "escrow_test_role":"escrow_decision",
          "public_key_hex":self.pub,
          "signature_hex":signature.hex(),
          "message_domain_hex":DOMAIN.hex(),
          "test_only":True,
          "production_root_authority":False,
          "durably_persisted_before_reply":True,
        }
        self.raw=canonical(self.receipt)+b"\n"
        self.ack={
          "role":"escrow_decision","host":"qian-ThinkPad-X230",
          "test_only":True,
          "durable_receipt_path":"/home/fake/.local/state/hepta-native-escrow-phase4n-20261012/receipt-decision-"+hashlib.sha256(data).hexdigest()+".json",
          "receipt_hex":self.raw.hex(),"receipt_sha256":hashlib.sha256(self.raw).hexdigest()
        }

    def verify(self,origin=None):
        return checker.verify_returned_bytes(
            stage="decision",claimed_ack=self.ack,
            expected_intent=self.intent,pinned_test_public_hex=self.pub,
            independent_origin_file=origin)

    def test_valid_matching_origin_copy_remains_test_only(self):
        result=self.verify(self.raw)
        self.assertEqual(result["state"],"TEST_REMOTE_BYTES_MATCH_ACROSS_TWO_READS")
        self.assertTrue(result["exact_remote_file_bytes_matched"])
        self.assertFalse(result["production_authority"])
        self.assertFalse(result["remote_fsync_survived_power_loss"])
        self.assertFalse(result["signed_original_host_cryptographically_attested"])

    def test_only_returned_ack_not_remote_origin_is_hold(self):
        result=self.verify(None)
        self.assertEqual(result["state"],"HOLD_REMOTE_ORIGIN_BYTES_NOT_OBSERVED")
        self.assertFalse(result["production_authority"])

    def test_changed_remote_file_bytes_rejected(self):
        b=bytearray(self.raw)
        b[30]^=1
        with self.assertRaisesRegex(ValueError,"TEST_ESCROW_RETURNED_AND_REMOTE_ORIGIN_BYTES_DIFFER"):
            self.verify(bytes(b))

    def test_returned_ack_file_path_escape_rejected(self):
        self.ack["durable_receipt_path"]="/tmp/../tmp/receipt-decision-"+self.receipt["body_sha256"]+".json"
        with self.assertRaisesRegex(ValueError,"TEST_ESCROW_REMOTE_PATH_NOT_PINNED"):
            self.verify(self.raw)

    def test_wrong_ack_source_role_rejected(self):
        self.ack["role"]="escrow_outcome"
        with self.assertRaisesRegex(ValueError,"TEST_ESCROW_ACK_ROLE_INVALID"):
            self.verify(self.raw)

    def test_claimed_durable_receipt_but_fake_file_path_hash_rejected(self):
        self.ack["durable_receipt_path"]=self.ack["durable_receipt_path"].replace(
            self.receipt["body_sha256"],"f"*64)
        with self.assertRaisesRegex(ValueError,"TEST_ESCROW_REMOTE_NAME_DOES_NOT_BIND_INTENT"):
            self.verify(self.raw)

    def test_original_source_quote_change_refused(self):
        self.intent=dict(self.intent,source_quote_receipt="1"*64)
        with self.assertRaisesRegex(ValueError,"TEST_ESCROW_RECEIPT_WRONG_SCOPE"):
            self.verify(self.raw)

    def test_pinned_test_public_key_replacement_refused(self):
        other=Ed25519PrivateKey.generate()
        self.pub=other.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw).hex()
        with self.assertRaisesRegex(ValueError,"TEST_ESCROW_RECEIPT_WRONG_SCOPE"):
            self.verify(self.raw)

    def test_fake_persistence_claim_refused_even_with_matching_signature(self):
        self.receipt["durably_persisted_before_reply"]=False
        self.raw=canonical(self.receipt)+b"\n"
        self.ack["receipt_hex"]=self.raw.hex()
        self.ack["receipt_sha256"]=hashlib.sha256(self.raw).hexdigest()
        with self.assertRaisesRegex(ValueError,"TEST_ESCROW_RECEIPT_WRONG_SCOPE"):
            self.verify(self.raw)

    def test_remote_origin_bytes_not_allowed_to_exceed_input_bound(self):
        with self.assertRaisesRegex(ValueError,"TEST_ESCROW_REPLAY_UNBOUNDED_BYTES"):
            self.verify(self.raw + b"x"*16385)

    def test_unbounded_returned_hex_refused_before_allocation(self):
        self.ack["receipt_hex"]="a"*32770
        with self.assertRaisesRegex(ValueError,"TEST_ESCROW_REPLAY_UNBOUNDED_BYTES"):
            self.verify(self.raw)

    def test_signer_signature_bitflip_refused_even_when_remote_copy_matches(self):
        sig=bytearray.fromhex(self.receipt["signature_hex"])
        sig[0]^=1
        self.receipt["signature_hex"]=bytes(sig).hex()
        self.raw=canonical(self.receipt)+b"\n"
        self.ack["receipt_hex"]=self.raw.hex()
        self.ack["receipt_sha256"]=hashlib.sha256(self.raw).hexdigest()
        with self.assertRaisesRegex(ValueError,"TEST_ESCROW_INDEPENDENT_ED25519_INVALID"):
            self.verify(self.raw)

    def test_malicious_signed_future_time_is_rejected(self):
        self.receipt["intent_held_at_ms"]=self.intent["outcome_due_ms"]
        self.raw=canonical(self.receipt)+b"\n"
        self.ack["receipt_hex"]=self.raw.hex()
        self.ack["receipt_sha256"]=hashlib.sha256(self.raw).hexdigest()
        with self.assertRaisesRegex(ValueError,"TEST_ESCROW_RECEIPT_CLOCK_INVALID"):
            self.verify(self.raw)

    def test_bool_frontier_rejected_not_an_integer(self):
        self.intent["next_ledger_sequence"]=True
        with self.assertRaisesRegex(ValueError,"TEST_ESCROW_EXPECTED_FRONTIER_NOT_INTEGER"):
            self.verify(self.raw)

    def test_changed_source_policy_rejected_even_if_returned_ack_absent(self):
        self.intent["source_policy"]="original_opaque_llm_with_confidence"
        with self.assertRaisesRegex(ValueError,"TEST_ESCROW_ORIGINAL_INTENT_NOT_IN_SCOPE"):
            self.verify(None)

    def test_noncanonical_predecessor_for_source_decision_rejected(self):
        self.intent["expected_predecessor_chain_digest"]="f"*64
        with self.assertRaisesRegex(ValueError,"TEST_ESCROW_EXPECTED_PREDECESSOR_CHANGED"):
            self.verify(None)

    def test_invalid_native_event_payload_digest_rejected(self):
        self.intent["original_native_payload_sha256"]="INVALID"
        with self.assertRaisesRegex(ValueError,"TEST_ESCROW_EXPECTED_SOURCE_DIGEST_INVALID"):
            self.verify(None)

    def test_wrong_domain_and_stage_reported_by_escrow_refused(self):
        for field,value in (("message_domain_hex","00"),("escrow_test_role","escrow_outcome")):
            with self.subTest(field=field):
                old=self.receipt[field]
                self.receipt[field]=value
                blob=canonical(self.receipt)+b"\n"
                ack=dict(self.ack,receipt_hex=blob.hex(),
                         receipt_sha256=hashlib.sha256(blob).hexdigest())
                with self.assertRaisesRegex(ValueError,"TEST_ESCROW_RECEIPT_WRONG_SCOPE"):
                    checker.verify_returned_bytes(stage="decision",claimed_ack=ack,
                          expected_intent=self.intent,pinned_test_public_hex=self.pub,
                          independent_origin_file=blob)
                self.receipt[field]=old


if __name__=="__main__":
    unittest.main()
