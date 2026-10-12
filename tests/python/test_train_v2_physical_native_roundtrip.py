"""Phase 4R: original Rust trust/scope/objective signing semantics (TEST ONLY)."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat

from train_v2 import physical_full_escrow_orchestrate as flow
from train_v2 import physical_full_escrow_receiver as rx
from train_v2.physical_native_roundtrip_qualification import (
    BIN_SCHEMA, rust_verify, mutation_checks,
)

def fields_from_original(data):
    request=json.loads(data)
    msg=bytes.fromhex(request["signed_evidence_message_hex"])
    position=len(flow.DOMAIN)
    assert msg[:position]==flow.DOMAIN
    names=[]
    for _ in range(2):
        count=int.from_bytes(msg[position:position+8],"big")
        position+=8
        names.append(msg[position:position+count].decode("ascii"))
        position+=count
    tag=msg[position];position+=1
    digests=[msg[position+i*32:position+(i+1)*32] for i in range(3)]
    return request, msg,position,digests,tag,names

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

class ActualNativeProcessTests(unittest.TestCase):
    def setUp(self):
        env=os.environ.get("HEPTA_PHASE4R_NATIVE_BINARY")
        if not env or not Path(env).is_file():
            self.skipTest("original pinned native bridge must be built separately")
        self.binary=Path(env)
        self.td=tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.root=Path(self.td.name)/"escrow"
        self.root_patch=patch.object(rx,"ROOT",self.root)
        self.root_patch.start();self.addCleanup(self.root_patch.stop)
        self.host_patch=patch.object(rx.socket,"gethostname",
                                    return_value="qian-ThinkPad-X230")
        self.host_patch.start();self.addCleanup(self.host_patch.stop)

    def original(self,raw):
        ack=rx.seal("decision",raw)
        self.assertTrue(ack["remote_fsync"])
        return rx.exact_read("decision",ack["original_sha256"])[:2]

    def test_true_original_native_rust_roundtrip_in_clean_new_test(self):
        original,receipt=self.original(flow.new_signed_original("decision"))
        proof=rust_verify(self.binary,"decision","qian-ThinkPad-X230",
                          original,receipt,want_success=True)
        self.assertTrue(proof["original_signed_evidence_rust_roundtrip_exact"])
        self.assertTrue(proof["original_actor_ed25519_verified"])
        self.assertFalse(proof["native_ledger_writer_append_performed"])
        negatives=mutation_checks(self.binary,"decision","qian-ThinkPad-X230",
                                  original,receipt)
        self.assertEqual(len(negatives),8)

    def test_cryptographically_valid_prior_wrong_order_rejected_in_rust(self):
        correct=flow.new_signed_original("decision")
        request,original,start,_,_,_=fields_from_original(correct)
        wrong=b"".join(hashlib.sha256(x).digest() for x in (
            b"TEST_SCOPE",b"TEST_OBJECTIVE",b"TEST_TRUST"))
        overwritten=original[:start]+wrong+original[start+96:]
        key=Ed25519PrivateKey.generate()
        request["signed_evidence_message_hex"]=overwritten.hex()
        request["original_signer_public_hex"]=key.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw).hex()
        request["detached_signature_hex"]=key.sign(overwritten).hex()
        request["original_intent"]["original_native_evidence_message_sha256"]=rx.digest(overwritten)
        request["original_intent"]["original_detached_signature_sha256"]=rx.digest(
            bytes.fromhex(request["detached_signature_hex"]))
        former=flow.canonical(request)+b"\n"
        original,receipt=self.original(former)
        error=rust_verify(self.binary,"decision","qian-ThinkPad-X230",
                          original,receipt,want_success=False)
        self.assertIn("ORIGINAL_TEST_DIGEST_SEMANTICS_DRIFT",error)
