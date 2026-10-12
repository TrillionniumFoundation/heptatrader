"""Phase4U: fail closed on first-input segment rotation; preserve Phase4T."""
from __future__ import annotations
import base64
import copy
import hashlib
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat

from historical_training.common import canonical,sha
from train_v2.contracts import ContractError,digest
from train_v2.first_source_rotation import (
    NAME,POLICY,candidate_segment,numeric_segments,pin_first_segment,
    recheck_presealed_cohort,
)
from train_v2.prewarmed_source_worker_v2 import (
    candidate as candidate_v2, SCHEMA as V2_SCHEMA,
    POLICY as V2_BEHAVIOR, DOMAIN as V2_DOMAIN,
)
from train_v2.prewarmed_source_worker import SCHEMA as V1_SCHEMA,DOMAIN as V1_DOMAIN
from train_v2.prewarmed_source_verify_v2 import (
    SIGNED_FIELDS,verify_signed_packet,
)
from train_v2.prewarmed_source_verify import verify_signed_packet as verify_v1_packet
from train_v2.prewarmed_source_orchestrate_v2 import SCHEMA as V2_ORCH_SCHEMA


class RotationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/"private-original"
        self.root.mkdir(mode=0o700)
        self.root.chmod(0o700)
        self.now_ms=int(time.time()*1000)
        self.start_ms=self.now_ms-20000
        self.before_ns=(self.start_ms-60000)*1000000
        self.prev_ns=(self.start_ms-180000)*1000000
        self.active=self.segment(self.before_ns,"a"*8,
                                 mtime_ns=self.before_ns+5000000)
        self.prev=self.segment(self.prev_ns,"b"*8,
                               mtime_ns=self.before_ns-1000000000)
    def segment(self,ns,suffix,*,mtime_ns=None):
        p=self.root/("segment-%019d-%s"%(ns,suffix))/"quotes.jsonl"
        p.parent.mkdir(mode=0o700)
        p.write_bytes(b"test-only-partial-prefix\n")
        p.chmod(0o600)
        stamp=ns if mtime_ns is None else mtime_ns
        os.utime(p,ns=(stamp,stamp))
        return p

    def test_single_pinned_writer_selected_not_lexical_recovery(self):
        recovery=self.root/"segment-recovery-live"/"quotes.jsonl"
        recovery.parent.mkdir()
        recovery.write_bytes(b"legacy")
        result=pin_first_segment(self.root,self.start_ms,
                                  asof_ms=self.now_ms)
        self.assertEqual(result["selected_segment_path"],str(self.active))
        self.assertEqual(result["selection_policy"],POLICY)
        self.assertEqual(result["prior_numeric_segment_count"],1)
        self.assertFalse(result["legacy_recovery_segment_selected"])
        other,proof=candidate_segment(self.root,self.start_ms,at_ms=self.now_ms)
        self.assertEqual(other,self.active)
        self.assertEqual(proof,result)

    def test_rotation_after_registered_start_refused_without_resample(self):
        frozen=pin_first_segment(self.root,self.start_ms,asof_ms=self.now_ms)
        original={"first_decision_ms":self.start_ms,
                  "source_anchor_at_ms":self.start_ms,
                  "cohort_id":frozen["cohort_id"],
                  "selected_segment_path":frozen["selected_segment_path"],
                  "selected_segment_start_ns":frozen["selected_segment_start_ns"],
                  "selection_policy":POLICY}
        new=self.segment((self.start_ms+1000)*1000000,"c"*8)
        self.assertTrue(new.exists())
        with self.assertRaisesRegex(ContractError,
                                   "COHORT_ROTATED_AFTER_DECLARED"):
            pin_first_segment(self.root,self.start_ms,asof_ms=self.now_ms)
        # No fallback to the newly created segment or late source price.
        with self.assertRaisesRegex(ContractError,
                                   "COHORT_ROTATED_AFTER_DECLARED"):
            candidate_v2({},self.root,self.start_ms,original)

    def test_new_numeric_writer_directory_without_quote_file_fails_closed(self):
        # The successor creates its directory before quotes.jsonl. The old
        # glob("segment-*/quotes.jsonl") incorrectly ignored this rollover.
        next_ns=(self.start_ms+200)*1000000
        incomplete=self.root/("segment-%019d-%s"%(next_ns,"f"*8))
        incomplete.mkdir(mode=0o700)
        with self.assertRaisesRegex(ContractError,"COHORT_NUMERIC_WRITER_WITHOUT_SOURCE"):
            pin_first_segment(self.root,self.start_ms,asof_ms=self.now_ms)

    def test_rotation_during_lead_before_first_decision_fails_closed(self):
        anchor=self.start_ms
        first_future_decision=self.now_ms+60000
        frozen=pin_first_segment(self.root,anchor,asof_ms=self.now_ms)
        original={"first_decision_ms":first_future_decision,
                  "source_anchor_at_ms":anchor,
                  "cohort_id":frozen["cohort_id"],
                  "selected_segment_path":frozen["selected_segment_path"],
                  "selected_segment_start_ns":frozen["selected_segment_start_ns"],
                  "selection_policy":POLICY}
        # A new segment created after registration but BEFORE first decision
        # may not silently replace the pre-registered original source.
        self.segment((self.start_ms+1000)*1000000,"d"*8)
        with self.assertRaisesRegex(ContractError,
                                   "COHORT_ROTATED_AFTER_DECLARED"):
            candidate_v2({},self.root,first_future_decision,original)

    def test_predecessor_modified_during_window_refused(self):
        os.utime(self.prev,ns=(self.start_ms*1000000,
                                self.start_ms*1000000))
        with self.assertRaisesRegex(ContractError,"COHORT_PREDECESSOR_MODIFIED"):
            pin_first_segment(self.root,self.start_ms,asof_ms=self.now_ms)

    def test_two_segment_starts_equal_are_ambiguous(self):
        self.segment(self.before_ns,"d"*8)
        with self.assertRaisesRegex(ContractError,"COHORT_DUPLICATE_NUMERIC_START"):
            pin_first_segment(self.root,self.start_ms,asof_ms=self.now_ms)

    def test_numeric_future_beyond_live_clock_refused(self):
        self.segment((self.now_ms+50000)*1000000,"d"*8)
        with self.assertRaisesRegex(ContractError,"COHORT_SEGMENT_START_IN_FUTURE"):
            pin_first_segment(self.root,self.start_ms,asof_ms=self.now_ms)
        # Readback at a historical decision cutoff must not reinterpret
        # a later new segment as already present at the earlier cutoff.
        result=pin_first_segment(self.root,self.start_ms,
                                 asof_ms=self.now_ms,historical_asof=True)
        self.assertEqual(result["selected_segment_path"],str(self.active))

    def test_symlinked_original_numeric_segment_refused(self):
        p=self.root/("segment-%019d-%s"%(self.before_ns+1,"e"*8))
        p.symlink_to(self.active.parent,target_is_directory=True)
        with self.assertRaisesRegex(ContractError,"COHORT_SEGMENT_SYMLINK"):
            pin_first_segment(self.root,self.start_ms,asof_ms=self.now_ms)

    def test_malformed_numeric_segment_cannot_be_silently_ignored(self):
        p=self.root/("segment-%019d-%s"%(self.before_ns+1,"NOTHEX"))
        p.mkdir()
        (p/"quotes.jsonl").write_text("wrong")
        with self.assertRaisesRegex(ContractError,"COHORT_AMBIGUOUS_NUMERIC"):
            pin_first_segment(self.root,self.start_ms,asof_ms=self.now_ms)

    def test_no_numeric_source_ever_before_first_window(self):
        new_start=self.start_ms-200000
        with self.assertRaisesRegex(ContractError,"COHORT_NO_PREWINDOW"):
            pin_first_segment(self.root,new_start,asof_ms=self.now_ms)

    def test_private_source_required(self):
        self.root.chmod(0o755)
        with self.assertRaisesRegex(ContractError,"COHORT_ORIGINAL_ROOT_NOT_PRIVATE"):
            pin_first_segment(self.root,self.start_ms,asof_ms=self.now_ms)

    def test_source_rotation_after_cryptographic_signing_still_fails_closed(self):
        frozen=pin_first_segment(self.root,self.start_ms,asof_ms=self.now_ms)
        record={
            "source_anchor_at_ms":self.start_ms,
            "cohort_id":frozen["cohort_id"],
            "selected_segment_path":frozen["selected_segment_path"],
            "selected_segment_start_ns":frozen["selected_segment_start_ns"],
            "selection_policy":POLICY,
        }
        self.assertEqual(recheck_presealed_cohort(self.root,record,
                          at_ms=self.now_ms)["cohort_id"],frozen["cohort_id"])
        self.segment((self.start_ms+250)*1000000,"e"*8)
        # Treat this as a signer/WAL fsync race: a perfect valid signature
        # does NOT mean the first-original source cohort survived.
        with self.assertRaisesRegex(ContractError,
                                    "COHORT_ROTATED_AFTER_DECLARED"):
            recheck_presealed_cohort(self.root,record,at_ms=self.now_ms+1000)

    def test_forged_cohort_identity_denied_even_without_file_rotation(self):
        frozen=pin_first_segment(self.root,self.start_ms,asof_ms=self.now_ms)
        record={
            "source_anchor_at_ms":self.start_ms,
            "cohort_id":"0"*64,
            "selected_segment_path":frozen["selected_segment_path"],
            "selected_segment_start_ns":frozen["selected_segment_start_ns"],
            "selection_policy":POLICY,
        }
        with self.assertRaisesRegex(ContractError,"COHORT_PRESEALED_IDENTITY_CHANGED"):
            recheck_presealed_cohort(self.root,record,at_ms=self.now_ms)
        record["cohort_id"]=frozen["cohort_id"]
        record["selection_policy"]="ANY_NEW_LATEST_SEGMENT_ALLOWED"
        with self.assertRaisesRegex(ContractError,"COHORT_PRESEALED_RECORD_INVALID"):
            recheck_presealed_cohort(self.root,record,at_ms=self.now_ms)

    def test_monotonic_identity_across_repeated_checks(self):
        r=pin_first_segment(self.root,self.start_ms,asof_ms=self.now_ms)
        self.assertEqual(
            pin_first_segment(self.root,self.start_ms,
                              asof_ms=self.now_ms+100)["cohort_id"],
            r["cohort_id"])
        new=self.segment((self.start_ms+500)*1000000,"d"*8)
        self.assertTrue(new.exists())
        with self.assertRaisesRegex(ContractError,"COHORT_ROTATED_AFTER_DECLARED"):
            pin_first_segment(self.root,self.start_ms,asof_ms=self.now_ms+1000)


class VersionedSignedSourceTests(unittest.TestCase):
    def setUp(self):
        self.key=Ed25519PrivateKey.generate()
        self.pub=self.key.public_key().public_bytes(
            Encoding.Raw,PublicFormat.Raw)
        self.packet={
            "schema":V2_SCHEMA,"plan_id":"a"*64,"event_id":"b"*64,
            "feature_id":"c"*64,"dataset_id":"d"*64,
            "original_source_path":"/private/input/segment-123/quotes.jsonl",
            "original_source_prefix_sha256":"e"*64,
            "first_source_registry_anchor_ms":1791789277221,
            "first_source_selection_policy":POLICY,
            "first_source_selected_segment_start_ns":1791788845839928307,
            "first_source_cohort_id":"f"*64,
            "original_quote_receipt":"0"*64,
            "original_event_at_ms":1791789327675,
            "original_available_at_ms":1791789328138,
            "original_expires_at_ms":1791789332675,
            "decision_at_ms":1791789328688,
            "source_event_at_ms":1791789328138,
            "source_worker_source_sha256":"1"*64,
            "no_action_or_model_inference":True,
            "not_original_llm_policy":True,
            "test_signature_only":True,
            "broker_orders_sent":0,
        }
        assert set(self.packet)==set(SIGNED_FIELDS)
        signed=V2_DOMAIN+hashlib.sha256(canonical(self.packet)).digest()
        sig=self.key.sign(signed)
        self.proof={**self.packet,
            "test_only_mac_public_key_hex":self.pub.hex(),
            "test_only_signature_hex":sig.hex(),
            "signed_message_hex":signed.hex(),
            "signature_sha256":hashlib.sha256(sig).hexdigest(),
            "public_key_sha256":hashlib.sha256(self.pub).hexdigest(),
            "test_signature_independently_replayable_without_private_key":True,
            "trade_authority":False,"native_ledger_writer_ack":False}

    def test_versioned_signed_source_fenced_from_original_v1(self):
        self.assertNotEqual(V2_SCHEMA,V1_SCHEMA)
        self.assertEqual(V2_DOMAIN,V1_DOMAIN)
        self.assertIn("v2",V2_ORCH_SCHEMA)
        self.assertEqual(verify_signed_packet(self.proof),self.packet)
        with self.assertRaisesRegex(ContractError,"SIGNED_SOURCE_PROOF_SCHEMA"):
            verify_v1_packet(self.proof)

    def test_signed_cohort_id_tamper_is_not_valid_even_if_rehashed_locally(self):
        forged=copy.deepcopy(self.proof)
        forged["first_source_cohort_id"]="3"*64
        with self.assertRaisesRegex(ContractError,"SIGNED_SOURCE_MESSAGE_DIGEST_CHANGED"):
            verify_signed_packet(forged)

    def test_signed_cohort_start_tamper_is_not_valid(self):
        forged=copy.deepcopy(self.proof)
        forged["first_source_selected_segment_start_ns"]+=1
        with self.assertRaisesRegex(ContractError,"SIGNED_SOURCE_MESSAGE_DIGEST_CHANGED"):
            verify_signed_packet(forged)

    def test_source_worker_v1_bytes_untouched_and_different_from_v2(self):
        from train_v2 import prewarmed_source_worker as old
        from train_v2 import prewarmed_source_worker_v2 as new
        pinned_sha="4f008a983f953c265706d0272a8e1cbb143855b8da1113848f4e29aaf12cea95"
        self.assertEqual(sha(Path(old.__file__)),pinned_sha)
        self.assertNotEqual(sha(Path(new.__file__)),pinned_sha)
        self.assertNotIn("latest_original_segment",new.__dict__)
