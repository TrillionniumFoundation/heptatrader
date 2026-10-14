"""Pure native wire-format regression: no separately built Rust process needed."""
import hashlib
import json
import unittest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from test_train_v2_physical_native_roundtrip import flow, rx, fields_from_original

class OriginalNativeOrderTests(unittest.TestCase):
    def test_both_generated_roles_exactly_match_native_field_order(self):
        for stage in ("decision","outcome"):
            with self.subTest(stage=stage):
                src=flow.new_signed_original(stage)
                req,msg,pos,ds,tag,names=fields_from_original(src)
                self.assertEqual(ds,[hashlib.sha256(x).digest() for x in (
                    b"TEST_TRUST",b"TEST_SCOPE",b"TEST_OBJECTIVE")])
                self.assertEqual(tag,0 if stage=="decision" else 1)
                self.assertEqual(names[-1],"lab.generator" if stage=="decision"
                                 else "lab.observer")
                self.assertTrue(req["test_only"])
                self.assertFalse(req["trade_authority"])

    def test_prior_wrong_order_is_not_native_semantic_identity(self):
        request,raw,start,ds,_,_=fields_from_original(
            flow.new_signed_original("decision"))
        wrong=(hashlib.sha256(b"TEST_SCOPE").digest()
               +hashlib.sha256(b"TEST_OBJECTIVE").digest()
               +hashlib.sha256(b"TEST_TRUST").digest())
        self.assertNotEqual(raw[start:start+96],wrong)
        self.assertNotEqual(ds[0],hashlib.sha256(b"TEST_SCOPE").digest())

    def test_old_digest_order_cannot_be_recast_as_native_by_rehash(self):
        request,original,start,_,_,_=fields_from_original(
            flow.new_signed_original("decision"))
        old=(hashlib.sha256(b"TEST_SCOPE").digest()
             +hashlib.sha256(b"TEST_OBJECTIVE").digest()
             +hashlib.sha256(b"TEST_TRUST").digest())
        replaced=original[:start]+old+original[start+96:]
        test_key=Ed25519PrivateKey.generate()
        request["signed_evidence_message_hex"]=replaced.hex()
        request["original_signer_public_hex"]=test_key.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw).hex()
        request["detached_signature_hex"]=test_key.sign(replaced).hex()
        request["original_intent"]["original_native_evidence_message_sha256"]=rx.digest(replaced)
        request["original_intent"]["original_detached_signature_sha256"]=rx.digest(
            bytes.fromhex(request["detached_signature_hex"]))
        reread,raw,_,ds,_,_=fields_from_original(flow.canonical(request)+b"\n")
        self.assertEqual(raw,replaced)
        self.assertNotEqual(ds,[hashlib.sha256(x).digest() for x in (
                    b"TEST_TRUST",b"TEST_SCOPE",b"TEST_OBJECTIVE")])
        self.assertTrue(reread["test_only"])
        self.assertFalse(reread["trade_authority"])

    def test_native_payload_remains_synthetic_not_real_policy(self):
        req=json.loads(flow.new_signed_original("decision"))
        raw=bytes.fromhex(req["signed_payload_hex"])
        self.assertTrue(raw.startswith(b"SYNTHETIC-PHASE4Q-NATIVE-PAYLOAD-NO-IB"))
        self.assertNotIn(b"EUR.USD",raw)
        self.assertNotIn(b"ProductionDecisionV2",raw)
