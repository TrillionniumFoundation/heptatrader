"""SYNTHETIC-only native retry/expiry guard tests; no real root or broker."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat

from train_v2 import real_source_native_recovery_gate as gate


def native_id(name:str)->bytes:
    b=name.encode("ascii")
    return len(b).to_bytes(8,"big")+b


def signed_example(role:str,private:Ed25519PrivateKey,*,issued:int,expires:int,
                   tag:int|None=None,epoch:int=19)->dict:
    roles={"generator":0,"observer":1}
    r=roles[role] if tag is None else tag
    domain=gate.DOMAIN
    body=(domain+native_id("lab.originalmarket."+role+".signed")
          +native_id("lab."+role)+bytes([r])
          +bytes.fromhex("aa"*32)+bytes.fromhex("bb"*32)
          +bytes.fromhex("cc"*32)+epoch.to_bytes(8,"big")
          +issued.to_bytes(8,"big")+expires.to_bytes(8,"big")
          +bytes.fromhex("dd"*32))
    return {"role":role,"public_message_hex":body.hex(),
            "signature_hex":private.sign(body).hex(),
            "signing_host":"MacBook-Pro.local" if role=="generator" else "qian-ThinkPad-X230"}


def key_hex(key:Ed25519PrivateKey)->str:
    return key.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw).hex()


def write_private(path:Path,raw:bytes):
    with open(path,"xb") as f:f.write(raw);f.flush();os.fsync(f.fileno())
    path.chmod(0o600)


class StrictReplayClassifierTests(unittest.TestCase):
    def test_already_acknowledged_even_if_original_signer_expired(self):
        self.assertEqual(gate.classify(2,1001,200),gate.ACK)

    def test_one_lagged_witness_with_current_expired_sig_is_hold(self):
        self.assertEqual(gate.classify(1,1001,1000),gate.HOLD)

    def test_one_lagged_witness_with_still_valid_sig_needs_manual_review(self):
        self.assertEqual(gate.classify(1,1000,1100),gate.REVIEW)

    def test_expiry_boundary_is_inclusive_then_expired_strictly_after(self):
        self.assertEqual(gate.classify(1,1000,1000),gate.REVIEW)
        self.assertEqual(gate.classify(1,1001,1000),gate.HOLD)

    def test_invalid_witness_sequences_and_boolean_clock_fail(self):
        for seq,now,expiry in ((0,100,100),(3,100,100),
                                (1,True,100),(1,100,False),(1,-1,100)):
            with self.subTest(case=(seq,now,expiry)):
                with self.assertRaisesRegex(ValueError,"REPLAY_CLOCK_AND_FRONTIER_TYPE"):
                    gate.classify(seq,now,expiry)


class NativePublicSignatureTests(unittest.TestCase):
    def setUp(self):
        self.key=Ed25519PrivateKey.generate()
        self.issued=int(time.time_ns()//1000000)-100000
        self.expiry=self.issued+900000

    def test_original_native_signed_evidence_exact_epoch_and_time(self):
        doc=signed_example("generator",self.key,issued=self.issued,
                           expires=self.expiry)
        d=gate.signed_native_times(doc,key_hex(self.key),"generator")
        self.assertEqual(d["native_issued_at_ms"],self.issued)
        self.assertEqual(d["native_expires_at_ms"],self.expiry)
        self.assertEqual(d["native_authority_epoch"],19)
        self.assertTrue(d["independent_ed25519_signature_verified"])

    def test_observer_signed_time_parses_without_display_clock(self):
        doc=signed_example("observer",self.key,issued=self.issued,
                           expires=self.expiry)
        r=gate.signed_native_times(doc,key_hex(self.key),"observer")
        self.assertEqual(r["original_signed_payload_digest"],"dd"*32)

    def test_generator_tries_using_observer_tag_fail(self):
        doc=signed_example("generator",self.key,issued=self.issued,
                           expires=self.expiry,tag=1)
        with self.assertRaisesRegex(ValueError,"NATIVE_SIGNER_IDENTITY_CHANGED"):
            gate.signed_native_times(doc,key_hex(self.key),"generator")

    def test_signature_bitflip_rejected(self):
        doc=signed_example("observer",self.key,issued=self.issued,
                           expires=self.expiry)
        sig=bytearray.fromhex(doc["signature_hex"]);sig[0]^=1
        doc["signature_hex"]=bytes(sig).hex()
        with self.assertRaisesRegex(ValueError,"SIGNED_ORIGINAL_PUBLIC_SIGNATURE_INVALID"):
            gate.signed_native_times(doc,key_hex(self.key),"observer")

    def test_old_authority_epoch_cannot_be_promoted(self):
        doc=signed_example("generator",self.key,issued=self.issued,
                           expires=self.expiry,epoch=20)
        with self.assertRaisesRegex(ValueError,"SIGNING_EPOCH_OR_TIME_INVALID"):
            gate.signed_native_times(doc,key_hex(self.key),"generator")

    def test_invalid_native_time_interval_rejected(self):
        doc=signed_example("generator",self.key,issued=self.issued,
                           expires=self.issued-1)
        with self.assertRaisesRegex(ValueError,"SIGNING_EPOCH_OR_TIME_INVALID"):
            gate.signed_native_times(doc,key_hex(self.key),"generator")


class ActualShapeButSyntheticCopiedLedgerTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.base=Path(t.name)
        self.original=self.base/"train-v2-real-source-native-20261012"
        self.replay=self.base/"train-v2-native-replay-20261012"
        self.source=self.original/"native-lab-v1"
        self.acked=self.replay/"acked"
        self.lagged=self.replay/"lagged"
        for x in (self.original,self.replay,self.source,self.acked,self.lagged):
            x.mkdir(mode=0o700)
            x.chmod(0o700)
        self.ledger=b"\xfd"*1224
        self.witness=b"\xfe"*248
        write_private(self.source/"ledger",self.ledger)
        write_private(self.source/"witness",self.witness)
        for p in (self.acked,self.lagged):
            write_private(p/"ledger",self.ledger)
            write_private(p/"witness",self.witness if p==self.acked
                          else self.witness[:160])
        self.keys={"generator":Ed25519PrivateKey.generate(),
                   "observer":Ed25519PrivateKey.generate()}
        self.issued=int(time.time_ns()//1000000)-90*60*1000
        gen=signed_example("generator",self.keys["generator"],
                           issued=self.issued,expires=self.issued+900000)
        obs=signed_example("observer",self.keys["observer"],
                           issued=self.issued+360000,
                           expires=self.issued+360000+900000)
        roles={k:{"public_key_hex":key_hex(v)} for k,v in self.keys.items()}
        self._document("public_roles.json",{"roles":roles})
        self._document("native-init-crosshost-public-signatures.json",
                       {"signatures":[gen]})
        self._document("native-finish-crosshost-public-signatures.json",
                       {"signatures":[obs]})

    def _document(self,name,obj):
        write_private(self.original/name,(json.dumps(obj)+"\n").encode())

    def run_admission(self,mode):
        with patch.object(gate,"verify_public",
                          return_value={"public_only_ed25519_messages_verified":4}):
            return gate.assess(self.original,mode)

    def test_stale_signature_already_acked_no_reappend_needed(self):
        doc=self.run_admission(self.acked)
        self.assertEqual(doc["recovery_admission"],gate.ACK)
        self.assertFalse(doc["automatic_write_or_witness_ack_performed"])
        self.assertEqual(doc["native_witness_count"],2)
        self.assertFalse(doc["product_controller_root_and_signature_enrolled"])

    def test_stale_one_late_witness_holds_instead_of_backdating(self):
        doc=self.run_admission(self.lagged)
        self.assertEqual(doc["native_witness_count"],1)
        self.assertEqual(doc["recovery_admission"],gate.HOLD)
        self.assertTrue(doc["production_history_repair_escalation_required"])
        self.assertFalse(doc["historical_backdating_as_production_authority_allowed"])

    def test_changed_event_bytes_denied_before_any_recovery(self):
        p=self.lagged/"ledger"
        x=bytearray(p.read_bytes());x[90]^=0x3a
        p.write_bytes(x)
        with self.assertRaisesRegex(ValueError,"TEST_REPLAY_COPY_OR_ORIGINAL_WITNESS_NOT_SAME_SOURCE"):
            self.run_admission(self.lagged)

    def test_wrong_witness_prefix_denied(self):
        p=self.lagged/"witness"
        x=bytearray(p.read_bytes());x[-1]^=0x01
        p.write_bytes(x)
        with self.assertRaisesRegex(ValueError,"TEST_REPLAY_COPY_OR_ORIGINAL_WITNESS_NOT_SAME_SOURCE"):
            self.run_admission(self.lagged)

    def test_unknown_crosshost_signed_observer_pubkey_denied(self):
        p=self.original/"public_roles.json"
        d=json.loads(p.read_text());d["roles"]["observer"]["public_key_hex"]="00"*32
        p.write_bytes((json.dumps(d)+"\n").encode())
        with self.assertRaisesRegex(ValueError,"SIGNED_ORIGINAL_PUBLIC_SIGNATURE_INVALID"):
            self.run_admission(self.lagged)

    def test_test_copy_escape_refused(self):
        with self.assertRaisesRegex(ValueError,"ORIGINAL_TEST_ONLY_REPLAY_SCOPE_INVALID"):
            self.run_admission(self.source)


if __name__=="__main__":
    unittest.main()
