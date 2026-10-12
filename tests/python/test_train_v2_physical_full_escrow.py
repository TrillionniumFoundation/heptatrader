"""Phase4Q physical escrow receiver tests, all synthetic and isolated."""
from __future__ import annotations
import copy
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from train_v2 import physical_full_escrow_receiver as rx
from train_v2 import physical_full_escrow_orchestrate as flow


class PhysicalFullBytesReceiverTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/"escrow"
        self.root_patch=patch.object(rx,"ROOT",self.root)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)
        self.host_patch=patch.object(rx.socket,"gethostname",
                                     return_value="qian-ThinkPad-X230")
        self.host_patch.start()
        self.addCleanup(self.host_patch.stop)
        self.stage="decision"
        self.raw=flow.new_signed_original(self.stage)
        self.key=rx.digest(self.raw)

    def test_real_ed25519_native_shaped_bytes_and_fsynced_create_once(self):
        initial=rx.seal(self.stage,self.raw)
        self.assertEqual(initial["status"],"NEW_COMPLETE_BYTES_SEALED_AND_READBACK")
        source,receipt,parsed=rx.exact_read(self.stage,self.key)
        self.assertEqual(source,self.raw)
        self.assertEqual(initial["receipt_sha256"],rx.digest(receipt))
        self.assertEqual(parsed["principal"],"lab.generator")
        self.assertEqual(parsed["role"],"generator")
        self.assertEqual(self.root.stat().st_mode&0o777,0o700)
        self.assertEqual(len(list(self.root.glob("original-*.json"))),1)
        self.assertEqual(len(list(self.root.glob("receipt-*.json"))),1)
        for f in self.root.glob("*.json"):
            self.assertEqual(f.stat().st_mode&0o777,0o600)
        self.assertEqual(len(list(self.root.glob("*key*"))),0)
        self.assertTrue(json.loads(receipt)["remote_directory_fsynced_before_reply"])
        self.assertFalse(json.loads(receipt)["product_root_enrolled"])

    def test_exact_lost_ack_retry_recovers_without_resigning(self):
        one=rx.seal(self.stage,self.raw)
        old=rx.exact_read(self.stage,self.key)[1]
        second=rx.seal(self.stage,self.raw)
        self.assertEqual(second["status"],"IDEMPOTENT_EXACT_REMOTE_READBACK")
        self.assertEqual(one["receipt_sha256"],second["receipt_sha256"])
        self.assertEqual(rx.exact_read(self.stage,self.key)[1],old)

    def test_archived_signature_readback_after_original_expiry_not_a_new_permission(self):
        rx.seal(self.stage,self.raw)
        saved=rx.exact_read(self.stage,self.key)
        unseen_prior_request=flow.new_signed_original(self.stage)
        future=time.time_ns()+720000*1000000
        with patch.object(rx.time,"time_ns",return_value=future):
            self.assertEqual(rx.exact_read(self.stage,self.key),saved)
            replay=rx.seal(self.stage,self.raw)
            self.assertEqual(replay["status"],"IDEMPOTENT_EXACT_REMOTE_READBACK")
            with self.assertRaisesRegex(ValueError,"PHYSICAL_TEST_SOURCE_WINDOW"):
                rx.seal(self.stage,unseen_prior_request)
        self.assertEqual(len(list(self.root.glob("original-*.json"))),1)

    def test_outcome_cannot_be_sealed_on_x230(self):
        outcome=flow.new_signed_original("outcome")
        with self.assertRaisesRegex(ValueError,"PHYSICAL_HOST_STAGE_MISMATCH"):
            rx.seal("outcome",outcome)

    def test_native_signature_bitflip_refused(self):
        broken=json.loads(self.raw)
        sig=bytearray.fromhex(broken["detached_signature_hex"])
        sig[5]^=1
        broken["detached_signature_hex"]=sig.hex()
        broken["original_intent"]["original_detached_signature_sha256"]=rx.digest(sig)
        with self.assertRaises(Exception):
            rx.seal(self.stage,rx.canonical(broken)+b"\n")
        self.assertFalse(list(self.root.glob("original-*.json")))
        self.assertFalse(list(self.root.glob("receipt-*.json")))

    def test_native_role_cannot_be_exchanged_by_rehash(self):
        changed=json.loads(self.raw)
        changed["original_intent"]["native_signer"]="observer"
        with self.assertRaisesRegex(ValueError,"PHYSICAL_ORIGINAL_INTENT"):
            rx.seal(self.stage,rx.canonical(changed)+b"\n")

    def test_source_time_and_witness_sequences_are_not_boolean(self):
        changed=json.loads(self.raw)
        changed["original_intent"]["next_ledger_sequence"]=True
        with self.assertRaisesRegex(ValueError,"PHYSICAL_FRONTIER"):
            rx.seal(self.stage,rx.canonical(changed)+b"\n")

    def test_original_native_message_payload_digest_must_be_exact(self):
        changed=json.loads(self.raw)
        changed["signed_payload_hex"]=b"OTHER_SYNTHETIC_PAYLOAD".hex()
        with self.assertRaisesRegex(ValueError,"PHYSICAL_DIGEST_MISMATCH"):
            rx.seal(self.stage,rx.canonical(changed)+b"\n")

    def test_archived_request_tamper_detected(self):
        rx.seal(self.stage,self.raw)
        target=self.root/rx.filename(self.stage,self.key)
        original=target.read_bytes()
        target.write_bytes(original.replace(b"test_only",b"test_only2",1))
        with self.assertRaisesRegex(ValueError,"PHYSICAL_ARCHIVE_IDENTITY_CHANGED"):
            rx.exact_read(self.stage,self.key)

    def test_archived_receipt_signature_tamper_detected(self):
        rx.seal(self.stage,self.raw)
        target=self.root/("receipt-"+self.stage+"-"+self.key+".json")
        body=json.loads(target.read_bytes())
        bad=bytearray.fromhex(body["receipt_signature_hex"])
        bad[4]^=1
        body["receipt_signature_hex"]=bad.hex()
        target.write_bytes(rx.canonical(body)+b"\n")
        with self.assertRaises(Exception):
            rx.exact_read(self.stage,self.key)

    def test_archive_permissions_fail_closed(self):
        rx.seal(self.stage,self.raw)
        target=self.root/rx.filename(self.stage,self.key)
        target.chmod(0o644)
        with self.assertRaisesRegex(ValueError,"PHYSICAL_ARCHIVE_MODE_OR_SIZE"):
            rx.exact_read(self.stage,self.key)

    def test_symlinked_root_and_original_are_rejected(self):
        outside=self.root.with_name("real")
        outside.mkdir(mode=0o700)
        self.root.symlink_to(outside,target_is_directory=True)
        with self.assertRaisesRegex(ValueError,"PHYSICAL_ROOT_SYMLINK"):
            rx.seal(self.stage,self.raw)
        self.root.unlink()
        rx.seal(self.stage,self.raw)
        target=self.root/rx.filename(self.stage,self.key)
        target.unlink()
        target.symlink_to(Path(self.tmp.name)/"nonexistent")
        with self.assertRaises(OSError):
            rx.exact_read(self.stage,self.key)

    def test_noncanonical_or_duplicate_json_rejected(self):
        raw=self.raw.rstrip(b"\n")+b' ,"extra": true}\n'
        with self.assertRaises(ValueError):
            rx.seal(self.stage,raw)
        v=self.raw.replace(b'"test_only":true',b'"test_only":false,"test_only":true',1)
        with self.assertRaisesRegex(ValueError,"DUPLICATE_JSON_KEY"):
            rx.seal(self.stage,v)

    def test_complete_remote_receipt_can_be_verified_without_private_key(self):
        rx.seal(self.stage,self.raw)
        original,receipt,_=rx.exact_read(self.stage,self.key)
        r=flow.verify_host_readback(self.stage,self.raw,{
            "host":"qian-ThinkPad-X230","stage":self.stage,
            "model_or_trade_authority":False,"private_keys_returned":False,
            "original_hex":original.hex(),"receipt_hex":receipt.hex(),
            "original_sha256":rx.digest(original),
            "receipt_sha256":rx.digest(receipt),
        },"qian-ThinkPad-X230")
        self.assertTrue(r["remote_new_key_signature_verified"])
        self.assertFalse(r["product_root_authorized"])

    def test_cross_host_claim_cannot_reuse_x230_receipt_for_mac(self):
        rx.seal(self.stage,self.raw)
        original,receipt,_=rx.exact_read(self.stage,self.key)
        doc={"host":"qian-ThinkPad-X230","stage":self.stage,
             "model_or_trade_authority":False,"private_keys_returned":False,
             "original_hex":original.hex(),"receipt_hex":receipt.hex(),
             "original_sha256":rx.digest(original),
             "receipt_sha256":rx.digest(receipt)}
        with self.assertRaisesRegex(RuntimeError,"PHYSICAL_ESCROW_HOST_OR_EFFECT"):
            flow.verify_host_readback(self.stage,self.raw,doc,"MacBook-Pro.local")
