"""Native ORIGINAL-Cargo linked escrow gate: explicitly local synthetic fixtures.

These 8 tests never access a broker or independent host. The test runner
deliberately simulates 'X230' metadata using an in-memory key on Pocket4,
exposing WHY signer host strings and self-selected public keys do not prove
remote physical custody or a product-authorized RecoveryAuthority.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat

from train_v2.real_source_native_remote_escrow import (
    DOMAIN,SCHEMA,RECEIPT_SCHEMA,canonical,validate
)

BINARY=Path.home()/(
  ".local/state/heptatrader/operator-evidence/"
  "train-v2-full-canonical-20261011/native-test-target/"
  "debug/examples/real_source_native_escrow_contract")


def pubhex(k:Ed25519PrivateKey)->str:
    return k.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw).hex()


class NativeOriginalRustEscrowContractTests(unittest.TestCase):
    def setUp(self):
        if not BINARY.is_file():
            self.skipTest("native source-linked contract example binary not compiled")
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.root.chmod(0o700)
        self.k=Ed25519PrivateKey.generate()

    def run_case(self,stage="decision",*,fault=None):
        directory=self.root/("case-"+str(len(list(self.root.iterdir()))))
        directory.mkdir(mode=0o700)
        process=subprocess.Popen(
            [str(BINARY),stage,pubhex(self.k),str(directory)],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
            text=True,bufsize=1)
        try:
            line=process.stdout.readline().strip()
            self.assertTrue(line.startswith("ESCROW_REQUEST "),line)
            payload=bytes.fromhex(line.split(" ",1)[1])
            body=json.loads(payload)
            self.assertEqual(canonical(body),payload)
            self.assertEqual(body["schema"],SCHEMA)
            self.assertEqual(body["stage"],stage)
            self.assertEqual(body["product_trading_authority"],False)
            self.assertEqual(body["source_policy"],
                             "SHADOW_RESEARCH_PREACTION_TWO_BIT_NO_ORDER_V1")
            self.assertEqual(body["expected_previous_witness_sequence"],
                             0 if stage=="decision" else 1)
            self.assertEqual(body["next_ledger_sequence"],
                             1 if stage=="decision" else 2)
            # A test on this local machine can verify native expected
            # fields but is NOT a physical independent escrow signer.
            when=int(time.time_ns()//1000000)
            sig=self.k.sign(DOMAIN+payload+when.to_bytes(8,"big"))
            record={
                "schema":RECEIPT_SCHEMA,
                "body":body,
                "body_sha256":hashlib.sha256(payload).hexdigest(),
                "intent_held_at_ms":when,
                "signing_host":"qian-ThinkPad-X230" if stage=="decision"
                               else "MacBook-Pro.local",
                "escrow_test_role":"escrow_"+stage,
                "public_key_hex":pubhex(self.k),
                "signature_hex":sig.hex(),
                "message_domain_hex":DOMAIN.hex(),
                "test_only":True,
                "production_root_authority":False,
                "durably_persisted_before_reply":True,
            }
            if fault=="missing":
                process.stdin.close()
            else:
                if fault=="signature":
                    sign=bytearray(sig);sign[0]^=1
                    record["signature_hex"]=bytes(sign).hex()
                elif fault=="source":
                    record["body"]=dict(body,source_quote_receipt="b"*64)
                elif fault=="host":
                    record["signing_host"]="claimed-rog-host"
                elif fault=="role":
                    record["escrow_test_role"]="escrow_outcome"
                elif fault=="pub":
                    record["public_key_hex"]="f"*64
                elif fault=="flag":
                    record["durably_persisted_before_reply"]=False
                elif fault=="late":
                    record["intent_held_at_ms"]=when+15*60*1000
                elif fault is not None:
                    raise ValueError("UNTESTED_FAULT")
                payload_hex=(canonical(record)+b"\n").hex()
                process.stdin.write("ESCROW_ACK "+payload_hex+"\n")
                process.stdin.flush()
                process.stdin.close()
            stdout=process.stdout.read()
            stderr=process.stderr.read()
            return process.wait(timeout=10),line+"\n"+stdout,stderr,directory,record
        finally:
            if process.poll() is None:
                process.kill();process.communicate(timeout=4)

    def test_native_correct_synthetic_decision_receipt_permits_only_escrow_file(self):
        code,out,err,folder,doc=self.run_case()
        self.assertEqual(code,0,err[-1200:])
        self.assertIn("SYNTHETIC_TEST_ONLY_ESCROW_PUBLIC_SIGNATURE_ACCEPTED",out)
        file=folder/"original-native-decision-independent-escrow.json"
        self.assertTrue(file.is_file())
        self.assertEqual(file.stat().st_mode&0o777,0o600)
        self.assertEqual(json.loads(file.read_bytes()),doc)
        self.assertFalse((folder/"ledger").exists())
        self.assertFalse((folder/"witness").exists())

    def test_native_correct_synthetic_outcome_receipt_no_product_ledger(self):
        code,_,err,folder,_=self.run_case(stage="outcome")
        self.assertEqual(code,0,err[-1200:])
        self.assertTrue((folder/"original-native-outcome-independent-escrow.json").is_file())
        self.assertFalse((folder/"ledger").exists())

    def test_native_absent_independent_receipt_holds_without_write(self):
        code,_,_,folder,_=self.run_case(fault="missing")
        self.assertNotEqual(code,0)
        self.assertEqual(list(folder.iterdir()),[])

    def test_native_bitflipped_independent_signature_denied(self):
        code,_,_,folder,_=self.run_case(fault="signature")
        self.assertNotEqual(code,0)
        self.assertEqual(list(folder.iterdir()),[])

    def test_native_payload_or_host_or_role_substitution_denied(self):
        for problem in ("source","host","role"):
            with self.subTest(problem=problem):
                code,_,_,folder,_=self.run_case(fault=problem)
                self.assertNotEqual(code,0)
                self.assertEqual(list(folder.iterdir()),[])

    def test_native_pubkey_and_escrow_persistence_claim_change_denied(self):
        for problem in ("pub","flag"):
            with self.subTest(problem=problem):
                code,_,_,folder,_=self.run_case(fault=problem)
                self.assertNotEqual(code,0)
                self.assertEqual(list(folder.iterdir()),[])

    def test_native_fake_future_escrow_time_denied(self):
        code,_,_,folder,_=self.run_case(fault="late")
        self.assertNotEqual(code,0)
        self.assertEqual(list(folder.iterdir()),[])

    def test_synthetic_signature_cannot_prove_independent_physical_host(self):
        code,_,err,folder,record=self.run_case()
        self.assertEqual(code,0,err[-1200:])
        self.assertEqual(record["signing_host"],"qian-ThinkPad-X230")
        # The actual TEST-ONLY signer's key was generated locally in
        # Pocket4's test process. A displayed hostname is not hardware
        # evidence of where a private key was controlled.
        self.assertTrue((folder/"original-native-decision-independent-escrow.json").is_file())
        self.assertFalse((folder/"ledger").exists())


if __name__=="__main__":
    unittest.main()
