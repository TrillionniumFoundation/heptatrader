"""SYNTHETIC test-only original native independent escrow contract fixtures.

No actual Mac/X230 signer is claimed by these mock-host unit tests.
No broker, user credentials, original trading model or product ledger.
"""
from __future__ import annotations

from contextlib import redirect_stdout
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PublicKey,
)
from train_v2 import real_source_native_remote_escrow as escrow


def fixture(stage="decision", now_ms=None):
    now_ms=int(time.time_ns()//1000000) if now_ms is None else now_ms
    first=stage=="decision"
    return {
        "schema":escrow.SCHEMA,
        "stage":stage,
        "native_signer":"generator" if first else "observer",
        "source_policy":"SHADOW_RESEARCH_PREACTION_TWO_BIT_NO_ORDER_V1",
        "source_quote_receipt":"a"*64,
        "test_source_packet_sha256":"b"*64,
        "original_native_payload_sha256":"c"*64,
        "original_native_evidence_message_sha256":"d"*64,
        "original_detached_signature_sha256":"e"*64,
        "expected_predecessor_chain_digest":"0"*64 if first else "f"*64,
        "expected_previous_witness_sequence":0 if first else 1,
        "next_ledger_sequence":1 if first else 2,
        "source_time_ms":now_ms-900,
        "outcome_due_ms":now_ms+300000 if first else now_ms-1800,
        "test_only":True,
        "product_trading_authority":False,
    }


class EscrowStructureTests(unittest.TestCase):
    def test_before_decision_test_only_intent_shape(self):
        escrow.validate(fixture("decision"),"decision")

    def test_before_observer_test_only_intent_shape(self):
        escrow.validate(fixture("outcome"),"outcome")

    def test_wrong_stage_identity_refused(self):
        x=fixture("decision")
        with self.assertRaisesRegex(ValueError,
                                     "ESCROW_ORIGINAL_NATIVE_INTENT_SCHEMA_SCOPE"):
            escrow.validate(x,"outcome")

    def test_nonzero_expected_decision_predecessor_refused(self):
        x=fixture()
        x["expected_predecessor_chain_digest"]="f"*64
        with self.assertRaisesRegex(ValueError,"ESCROW_WRONG_PREDECESSOR_SCOPE"):
            escrow.validate(x,"decision")

    def test_observer_must_have_one_predecessor(self):
        x=fixture("outcome")
        x["expected_previous_witness_sequence"]=0
        with self.assertRaisesRegex(ValueError,"ESCROW_SOURCE_WITNESS_FRONTIER_INVALID"):
            escrow.validate(x,"outcome")

    def test_unqualified_product_authority_refused(self):
        x=fixture()
        x["product_trading_authority"]=True
        with self.assertRaisesRegex(ValueError,"ESCROW_IS_NOT_PRODUCT_AUTHORITY"):
            escrow.validate(x,"decision")

    def test_substitute_old_llm_strategy_rejected(self):
        x=fixture()
        x["source_policy"]="OPAQUE_OLD_LLM_ACTION_CONFIDENCE"
        with self.assertRaisesRegex(ValueError,"ESCROW_ORIGINAL_LLM_POLICY_NOT_ACCEPTABLE"):
            escrow.validate(x,"decision")

    def test_source_quote_receipt_nonsha_refused(self):
        x=fixture()
        x["source_quote_receipt"]="f"
        with self.assertRaisesRegex(ValueError,"ESCROW_NATIVE_BINDING_HASH_INVALID"):
            escrow.validate(x,"decision")

    def test_generator_event_cannot_be_sealed_too_late(self):
        x=fixture(now_ms=int(time.time_ns()//1000000)-300000)
        with self.assertRaisesRegex(ValueError,"ESCROW_REAL_NATIVE_GENERATOR_SIGNED_TOO_LATE"):
            escrow.validate(x,"decision")

    def test_observer_source_time_cannot_be_in_future(self):
        x=fixture("outcome",now_ms=int(time.time_ns()//1000000)+300000)
        with self.assertRaisesRegex(ValueError,"ESCROW_OBSERVER_BEFORE_HORIZON"):
            escrow.validate(x,"outcome")

    def test_boolean_witness_sequence_rejected(self):
        x=fixture()
        x["next_ledger_sequence"]=True
        with self.assertRaisesRegex(ValueError,"ESCROW_SOURCE_WITNESS_FRONTIER_INVALID"):
            escrow.validate(x,"decision")

    def test_noncanonical_json_signature_message_disallowed(self):
        x=fixture()
        canonical=escrow.canonical(x)
        raw=(json.dumps(x,indent=2)+"\n").encode()
        self.assertNotEqual(canonical,raw)
        self.assertEqual(json.loads(raw),json.loads(canonical))


class EscrowDurableLocalFixtureTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.temp=Path(tmp.name)
        self.private_root=self.temp/"escrow"
        self.original_root=escrow.ROOT
        escrow.ROOT=self.private_root
        self.addCleanup(setattr,escrow,"ROOT",self.original_root)
        self.host_patch=patch.object(escrow.socket,"gethostname",
                                     return_value="qian-ThinkPad-X230")
        self.host_patch.start()
        self.addCleanup(self.host_patch.stop)

    def call(self,mode,stage="decision",doc=None):
        out=io.StringIO()
        if doc is None:
            with redirect_stdout(out):
                escrow.action(mode,stage)
        else:
            raw=escrow.canonical(doc).hex()+"\n"
            stdin=io.TextIOWrapper(io.BytesIO(raw.encode()),encoding="ascii")
            with patch.object(escrow.sys,"stdin",stdin),redirect_stdout(out):
                escrow.action(mode,stage)
        return json.loads(out.getvalue())

    def test_real_fsync_labeled_local_fixture_and_signature(self):
        init=self.call("init")
        self.assertEqual(init["test_only"],True)
        assert self.private_root.stat().st_mode&0o777==0o700
        d=fixture()
        reply=self.call("seal",doc=d)
        blob=bytes.fromhex(reply["receipt_hex"])
        self.assertEqual(hashlib.sha256(blob).hexdigest(),reply["receipt_sha256"])
        record=json.loads(blob)
        self.assertEqual(record["body"],d)
        self.assertTrue(record["durably_persisted_before_reply"])
        self.assertFalse(record["production_root_authority"])
        self.assertEqual(record["body_sha256"],hashlib.sha256(escrow.canonical(d)).hexdigest())
        msg=(escrow.DOMAIN+escrow.canonical(d)+record["intent_held_at_ms"].to_bytes(8,"big"))
        Ed25519PublicKey.from_public_bytes(
            bytes.fromhex(init["public_key_hex"])).verify(
                bytes.fromhex(record["signature_hex"]),msg)
        stored=Path(reply["durable_receipt_path"])
        self.assertEqual(stored.read_bytes(),blob)
        self.assertEqual(stored.stat().st_mode&0o777,0o600)
        self.assertEqual((self.private_root/"decision.escrow-test-only.key").stat().st_mode&0o777,0o600)

    def test_duplicate_identical_intent_returns_exact_durable_receipt(self):
        self.call("init")
        doc=fixture()
        first=self.call("seal",doc=doc)
        original=Path(first["durable_receipt_path"]).read_bytes()
        self.assertFalse(first["idempotent_prior_receipt_readback"])
        second=self.call("seal",doc=doc)
        self.assertTrue(second["idempotent_prior_receipt_readback"])
        self.assertEqual(second["receipt_hex"],first["receipt_hex"])
        self.assertEqual(second["receipt_sha256"],first["receipt_sha256"])
        self.assertEqual(Path(first["durable_receipt_path"]).read_bytes(),original)

    def test_lost_ack_exact_replay_after_window_expiry_is_only_readback(self):
        self.call("init")
        doc=fixture()
        original=self.call("seal",doc=doc)
        prior=Path(original["durable_receipt_path"]).read_bytes()
        future=doc["source_time_ms"]+4*60*60*1000
        with patch.object(escrow.time,"time_ns",
                          return_value=future*1000000):
            second=self.call("seal",doc=doc)
        self.assertTrue(second["idempotent_prior_receipt_readback"])
        self.assertEqual(second["receipt_hex"],original["receipt_hex"])
        self.assertEqual(prior,Path(original["durable_receipt_path"]).read_bytes())

    def test_no_prior_archive_cannot_create_new_decision_after_window(self):
        self.call("init")
        doc=fixture()
        future=doc["source_time_ms"]+4*60*60*1000
        with patch.object(escrow.time,"time_ns",
                          return_value=future*1000000):
            with self.assertRaisesRegex(
                  ValueError,"ESCROW_REAL_NATIVE_GENERATOR_SIGNED_TOO_LATE"):
                self.call("seal",doc=doc)
        receipts=list(self.private_root.glob("receipt-*.json"))
        self.assertEqual(receipts,[])

    def test_changed_existing_signed_receipt_cannot_be_replayed(self):
        self.call("init")
        doc=fixture()
        original=self.call("seal",doc=doc)
        file=Path(original["durable_receipt_path"])
        envelope=json.loads(file.read_bytes())
        envelope["signature_hex"]="00"*64
        file.write_bytes(escrow.canonical(envelope)+b"\n")
        with self.assertRaisesRegex(ValueError,
                                    "ESCROW_PRIOR_RECEIPT_BAD_ED25519_SIGNATURE"):
            self.call("seal",doc=doc)

    def test_incomplete_existing_archive_cannot_be_reissued_as_ack(self):
        self.call("init")
        doc=fixture()
        original=self.call("seal",doc=doc)
        path=Path(original["durable_receipt_path"])
        path.write_bytes(b"{")
        with self.assertRaisesRegex(ValueError,
                                    "ESCROW_PRIOR_ARCHIVE_NOT_OWNER_PRIVATE"):
            self.call("seal",doc=doc)

    def test_destroyed_original_test_key_does_not_invalidate_old_public_receipt(self):
        initial=self.call("init")
        record=json.loads(bytes.fromhex(self.call("seal",doc=fixture())["receipt_hex"]))
        cleanup=self.call("destroy")
        self.assertTrue(cleanup["only_test_key_unlinked"])
        self.assertFalse((self.private_root/"decision.escrow-test-only.key").exists())
        msg=escrow.DOMAIN+escrow.canonical(record["body"])+record["intent_held_at_ms"].to_bytes(8,"big")
        Ed25519PublicKey.from_public_bytes(
            bytes.fromhex(initial["public_key_hex"])).verify(
                bytes.fromhex(record["signature_hex"]),msg)

    def test_cannot_sign_with_other_host_role_key(self):
        self.call("init")
        with self.assertRaisesRegex(ValueError,"ESCROW_STAGE_INDEPENDENT_MACHINE_MISMATCH"):
            self.call("pub","outcome")

    def test_symlink_to_private_escrow_receipt_not_reused(self):
        self.call("init")
        x=fixture()
        original_name=self.private_root/("receipt-decision-"+escrow.digest(escrow.canonical(x))+".json")
        original_name.symlink_to(self.temp/"other")
        with self.assertRaises((OSError,ValueError)):
            self.call("seal",doc=x)
        self.assertTrue(original_name.is_symlink())


if __name__=="__main__":
    unittest.main()
