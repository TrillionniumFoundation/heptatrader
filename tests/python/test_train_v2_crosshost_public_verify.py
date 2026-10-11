"""Adversarial *synthetic* public signature verification; no broker or keys on disk."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from train_v2.crosshost_public_verify import TEST_HOSTS,EXPECTED,verify
from train_v2.crosshost_test_signer import _scope,ROOT_DOMAIN,EVIDENCE_DOMAIN


def native_id(value: str) -> bytes:
    encoded=value.encode("ascii")
    return len(encoded).to_bytes(8,"big")+encoded


class PublicCrosshostSignatureTests(unittest.TestCase):
    def setUp(self):
        self.keys={role:Ed25519PrivateKey.generate() for role in TEST_HOSTS}
        pub={
            role:{
                "role":role,
                "actual_signing_host":TEST_HOSTS[role],
                "public_key_hex":key.public_key().public_bytes(
                    Encoding.Raw,PublicFormat.Raw).hex(),
                "test_only":True,
                "private_key_exported":False
            }
            for role,key in self.keys.items()
        }
        self.manifest={
            "schema":"hepta-train-v2-crosshost-native-signing-pubs.v1",
            "purpose":"test-only-no-production","roles":pub
        }
        self.manifest_raw=(json.dumps(self.manifest,sort_keys=True,
                                      indent=2)+"\n").encode()
        requests=[]
        roles=[("root",None),("generator",0),("generator",0),
               ("observer",1),("evaluator",2),("privacy",4)]
        for idx,(role,tag) in enumerate(roles):
            if tag is None:
                msg=ROOT_DOMAIN+native_id("lab.root")+b"\x00"*44
            else:
                msg=EVIDENCE_DOMAIN+native_id("lab."+role+".unit-"+str(idx))+native_id(
                    "lab."+role)+bytes([tag])+bytes([idx])*135
            signature=self.keys[role].sign(msg)
            requests.append({
                "role":role,"purpose":"synthetic_native_lab_only",
                "actual_signing_host":TEST_HOSTS[role],
                "signing_key_transferred":False,
                "signature_cryptographically_verified_against_host_public_key":True,
                "native_signing_message_hex":msg.hex(),
                "signature_hex":signature.hex(),
                "message_sha256":hashlib.sha256(msg).hexdigest(),
                "signature_sha256":hashlib.sha256(signature).hexdigest(),
            })
        self.proof={
            "schema":"heptatrader.train-v2-true-crosshost-test-only-native-signature-lab.v1",
            "public_manifest_sha256":hashlib.sha256(self.manifest_raw).hexdigest(),
            "all_test_only":True,"signer_private_keys_ever_exported":False,
            "signature_requests":requests,
            "native_result":{
                "result":"TEST_ONLY_NATIVE_PASSED",
                "native_durable_witness_frontier_records":3,
                "native_signed_decisions":1,
                "native_signed_terminal_outcomes":1,
                "native_v3_dataset_frozen":True,
                "native_v3_revalidation_rejected_after_withdrawal":True,
                "native_generator_evidence_corruption_rejected":True,
                "native_wrong_role_outcome_rejected":True,
                "product_root_authority_enrolled":False,
                "real_broker_evidence":False,
            }
        }

    def validated(self):
        return verify(self.manifest,self.manifest_raw,self.proof)

    def test_public_six_native_roles_verify_offline_without_private_key_file(self):
        result=self.validated()
        self.assertEqual(result["verified_public_ed25519_signatures"],6)
        self.assertTrue(result["independent_offline_signature_verification_passed"])
        self.assertFalse(result["machine_identity_cryptographically_attested"])
        self.assertFalse(result["production_controller_keys_enrolled"])
        self.assertFalse(result["real_quote_and_after_cost_pnl"])

    def test_signature_bit_flip_refuses(self):
        row=self.proof["signature_requests"][3]
        sig=bytearray.fromhex(row["signature_hex"])
        sig[0]^=1
        row["signature_hex"]=bytes(sig).hex()
        row["signature_sha256"]=hashlib.sha256(sig).hexdigest()
        with self.assertRaisesRegex(ValueError, "INDEPENDENT_OFFLINE_NATIVE_SIGNATURE_INVALID"):
            self.validated()

    def test_root_signing_payload_bit_flip_refuses(self):
        row=self.proof["signature_requests"][0]
        message=bytearray.fromhex(row["native_signing_message_hex"])
        message[-1]^=1
        row["native_signing_message_hex"]=bytes(message).hex()
        row["message_sha256"]=hashlib.sha256(message).hexdigest()
        with self.assertRaisesRegex(ValueError, "INDEPENDENT_OFFLINE_NATIVE_SIGNATURE_INVALID"):
            self.validated()

    def test_proof_cannot_change_pinned_public_manifest_hash(self):
        self.proof["public_manifest_sha256"]="f"*64
        with self.assertRaisesRegex(ValueError,"PUBLIC_SIGNED_TRUST_SOURCE_INVALID"):
            self.validated()

    def test_root_generator_observer_same_host_refuses(self):
        self.manifest["roles"]["observer"]["actual_signing_host"]="MacBook-Pro.local"
        with self.assertRaisesRegex(ValueError,"PUBLIC_ROLE_HOST_SCOPE_INVALID"):
            self.validated()

    def test_swapping_mac_and_x230_public_keys_refuses(self):
        old=self.manifest["roles"]["observer"]["public_key_hex"]
        self.manifest["roles"]["observer"]["public_key_hex"]=self.manifest["roles"]["generator"]["public_key_hex"]
        self.manifest["roles"]["generator"]["public_key_hex"]=old
        # Rehash now exposes actual key collision/cross-binding under same input.
        self.manifest_raw=(json.dumps(self.manifest,sort_keys=True,indent=2)+"\n").encode()
        self.proof["public_manifest_sha256"]=hashlib.sha256(self.manifest_raw).hexdigest()
        with self.assertRaisesRegex(ValueError,"INDEPENDENT_OFFLINE_NATIVE_SIGNATURE_INVALID"):
            self.validated()

    def test_untampered_but_swapped_role_order_refuses(self):
        reqs=self.proof["signature_requests"]
        reqs[1],reqs[3]=reqs[3],reqs[1]
        with self.assertRaisesRegex(ValueError, "NATIVE_ROLE_SEQUENCE_UNVERIFIED"):
            self.validated()

    def test_false_native_product_activation_refuses(self):
        self.proof["native_result"]["product_root_authority_enrolled"]=True
        with self.assertRaisesRegex(ValueError, "NATIVE_RESULTS_MISCLASSIFIED"):
            self.validated()

    def test_incorrect_native_evidence_role_byte_refuses(self):
        row=self.proof["signature_requests"][1]
        raw=bytes.fromhex(row["native_signing_message_hex"])
        pos=len(EVIDENCE_DOMAIN)
        n=int.from_bytes(raw[pos:pos+8],"big");pos+=8+n
        n=int.from_bytes(raw[pos:pos+8],"big");pos+=8+n
        changed=bytearray(raw)
        changed[pos]=1
        row["native_signing_message_hex"]=bytes(changed).hex()
        row["message_sha256"]=hashlib.sha256(changed).hexdigest()
        with self.assertRaisesRegex(ValueError, "TEST_SIGNER_ROLE_PRINCIPAL_MISMATCH"):
            self.validated()

    def test_bad_credential_and_trust_generation_not_autopromoted(self):
        r=self.validated()
        self.assertFalse(r["production_training_or_trading_authorized"])
        self.assertFalse(r["true_original_trading_behavior_policy_probabilities"])

    def test_roles_have_distinct_ed25519_public_key_bytes(self):
        result=self.validated()
        self.assertEqual(len({x["public_key_hex"] for x in self.manifest["roles"].values()}),5)
        self.assertEqual(result["verified_public_ed25519_signatures"],6)

    def test_native_domain_refuses_malformed_or_out_of_scope(self):
        for role,raw in [("root",b"invalid"),("generator",b"wrong"),
                         ("observer",ROOT_DOMAIN+native_id("lab.root"))]:
            with self.subTest(role=role):
                with self.assertRaises(ValueError):
                    _scope(role,raw)


if __name__=="__main__":
    unittest.main()
