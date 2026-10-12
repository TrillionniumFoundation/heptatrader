"""Full exact original native bytes escrow, deliberately TEST-ONLY."""
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from train_v2.real_source_native_remote_escrow import SCHEMA as INTENT_SCHEMA,canonical
from train_v2.full_native_escrow import (
    SCHEMA, sha, validate_complete, seal_local_test_only, verify_saved,
)

class CompleteNativeEscrowTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/"archives"
        key=Ed25519PrivateKey.generate()
        from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat
        pub=key.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)
        payload=b"heptatrader TEST typed native V2 decision payload"
        message=b"hepta.learning-ledger.signed-evidence.v1"+b"x"*64+bytes.fromhex(sha(payload))
        sig=key.sign(message)
        self.intent={
           "schema":INTENT_SCHEMA,"stage":"decision","native_signer":"generator",
           "source_policy":"SHADOW_RESEARCH_PREACTION_TWO_BIT_NO_ORDER_V1",
           "test_only":True,"product_trading_authority":False,
           "source_time_ms":1000,"outcome_due_ms":301000,
           "expected_previous_witness_sequence":0,"next_ledger_sequence":1,
           "source_quote_receipt":"a"*64,"test_source_packet_sha256":"b"*64,
           "expected_predecessor_chain_digest":"0"*64,
           "original_native_payload_sha256":sha(payload),
           "original_native_evidence_message_sha256":sha(message),
           "original_detached_signature_sha256":sha(sig),
        }
        self.request={
            "schema":SCHEMA,"original_intent":self.intent,
            "signed_payload_hex":payload.hex(),
            "signed_evidence_message_hex":message.hex(),
            "detached_signature_hex":sig.hex(),
            "original_signer_public_hex":pub.hex(),
            "test_only":True,"trade_authority":False,
        }

    def test_exact_original_signed_bytes_recovered_without_private_key(self):
        initial=seal_local_test_only(self.root,self.request)
        resumed=verify_saved(self.root,"decision",initial["original_intent_digest"])
        self.assertEqual(initial,resumed)
        self.assertTrue(resumed["recoverable_complete_original_test_bytes"])
        self.assertFalse(resumed["native_witness_advance_authorized"])
        self.assertFalse(resumed["native_root_recovery_authority_enrolled"])
        self.assertEqual(len(list(self.root.iterdir())),1)

    def test_missing_bytes_cannot_be_restored_from_sha_only(self):
        for field in ("signed_payload_hex","signed_evidence_message_hex",
                      "detached_signature_hex","original_signer_public_hex"):
            with self.subTest(field=field):
                request=copy.deepcopy(self.request)
                del request[field]
                with self.assertRaises(ValueError):
                    validate_complete(request)

    def test_rehashed_but_wrong_ed25519_signature_refused(self):
        r=copy.deepcopy(self.request)
        sig=bytes.fromhex(r["detached_signature_hex"])
        changed=bytes([sig[0]^1])+sig[1:]
        r["detached_signature_hex"]=changed.hex()
        r["original_intent"]["original_detached_signature_sha256"]=sha(changed)
        with self.assertRaisesRegex(ValueError,"NATIVE_ESCROW_DETACHED_SIGNATURE_INVALID"):
            validate_complete(r)

    def test_valid_signature_of_another_native_payload_refused(self):
        r=copy.deepcopy(self.request)
        key=Ed25519PrivateKey.generate()
        other=b"other original native source"
        msg=b"hepta.learning-ledger.signed-evidence.v1"+b"x"*64+bytes.fromhex(sha(other))
        from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat
        r["original_signer_public_hex"]=key.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw).hex()
        signature=key.sign(msg)
        r["signed_evidence_message_hex"]=msg.hex()
        r["detached_signature_hex"]=signature.hex()
        r["original_intent"]["original_native_evidence_message_sha256"]=sha(msg)
        r["original_intent"]["original_detached_signature_sha256"]=sha(signature)
        with self.assertRaisesRegex(ValueError,"NATIVE_ESCROW_SIGNED_PAYLOAD_BINDING"):
            validate_complete(r)

    def test_changed_original_payload_rejected(self):
        r=copy.deepcopy(self.request)
        r["signed_payload_hex"]=b"changed payload".hex()
        with self.assertRaisesRegex(ValueError,"NATIVE_ESCROW_ORIGINAL_BYTES_DIGEST"):
            validate_complete(r)

    def test_role_switch_and_false_product_claim_refused(self):
        r=copy.deepcopy(self.request)
        r["original_intent"]["native_signer"]="observer"
        with self.assertRaisesRegex(ValueError,"NATIVE_ESCROW_NATIVE_SIGNER_ROLE"):
            validate_complete(r)
        r=copy.deepcopy(self.request)
        r["trade_authority"]=True
        with self.assertRaisesRegex(ValueError,"NATIVE_ESCROW_TEST_ONLY_SCOPE"):
            validate_complete(r)

    def test_domain_substitution_refused_even_with_valid_signature(self):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        r=copy.deepcopy(self.request)
        r["signed_evidence_message_hex"]=b"unrelated evidence".hex()
        r["original_intent"]["original_native_evidence_message_sha256"]=sha(
            b"unrelated evidence")
        with self.assertRaisesRegex(ValueError,"NATIVE_ESCROW_WRONG_NATIVE_DOMAIN"):
            validate_complete(r)

    def test_no_lost_ack_overwrite_or_reissue(self):
        first=seal_local_test_only(self.root,self.request)
        with self.assertRaises(FileExistsError):
            seal_local_test_only(self.root,self.request)
        self.assertEqual(verify_saved(self.root,"decision",first["original_intent_digest"]),
                         first)

    def test_tamper_or_truncate_sealed_archive_fails(self):
        receipt=seal_local_test_only(self.root,self.request)
        target=self.root/receipt["archive_basename"]
        target.write_bytes(target.read_bytes()[:80])
        with self.assertRaises(ValueError):
            verify_saved(self.root,"decision",receipt["original_intent_digest"])

    def test_public_archive_permission_refused(self):
        receipt=seal_local_test_only(self.root,self.request)
        (self.root/receipt["archive_basename"]).chmod(0o644)
        with self.assertRaisesRegex(ValueError,"NATIVE_ESCROW_ARCHIVE_NOT_PRIVATE"):
            verify_saved(self.root,"decision",receipt["original_intent_digest"])

    def test_private_symlink_archive_refused(self):
        receipt=seal_local_test_only(self.root,self.request)
        target=self.root/receipt["archive_basename"]
        target.rename(self.root/"actual")
        target.symlink_to(self.root/"actual")
        with self.assertRaises(OSError):
            verify_saved(self.root,"decision",receipt["original_intent_digest"])

    def test_original_intent_digest_changes_new_identity(self):
        result=validate_complete(self.request)
        r=copy.deepcopy(self.request)
        r["original_intent"]["source_quote_receipt"]="d"*64
        changed=validate_complete(r)
        self.assertNotEqual(changed["original_intent_digest"],result["original_intent_digest"])

    def test_stage_outcome_uses_distinct_native_role(self):
        r=copy.deepcopy(self.request)
        r["original_intent"]["stage"]="outcome"
        r["original_intent"]["native_signer"]="observer"
        r["original_intent"]["expected_previous_witness_sequence"]=1
        r["original_intent"]["next_ledger_sequence"]=2
        r["original_intent"]["expected_predecessor_chain_digest"]="c"*64
        r["original_intent"]["source_time_ms"]=301000
        outcome=seal_local_test_only(self.root,r)
        self.assertEqual(outcome["stage"],"outcome")
        self.assertFalse(outcome["product_trading_authority"])
