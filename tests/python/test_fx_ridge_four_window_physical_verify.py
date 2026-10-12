"""Offline four-window physical replay: adversarial and exact-source tests.

With env HT_FX_PHYSICAL_BUNDLE/EPISODES/VERIFIER, replay the real immutable
pocket4 research fixture plus corrupt private COPIES. Without that opt-in
only dependency-free contract tests run; no network or broker path exists.
"""
from __future__ import annotations
import hashlib
import os
from pathlib import Path
import shutil
import tempfile
import unittest

from train_v2 import fx_ridge_four_window_physical_verify as api

BUNDLE=os.getenv("HT_FX_PHYSICAL_BUNDLE")
EPISODES=os.getenv("HT_FX_PHYSICAL_EPISODES")
VERIFIER=os.getenv("HT_FX_PHYSICAL_VERIFIER")
HAS_ACTUAL=bool(BUNDLE and EPISODES and VERIFIER)

class SourceBoundaryTests(unittest.TestCase):
    def test_exactly_four_original_windows_and_no_censored_fills(self):
        paths=api.evidence_paths()
        self.assertEqual(len(paths),42)
        self.assertEqual(len(set(paths)),42)
        self.assertIn("window-00/decision-attempt.json",paths)
        self.assertIn("window-00/maturity-attempt.json",paths)
        self.assertNotIn("window-00/mature-outcome.json",paths)
        for n in ("01","02","03"):
            self.assertIn("window-"+n+"/mature-outcome.json",paths)
    def test_original_frozen_digest_pins_present(self):
        for item in (api.EXPECTED_SEAL_SHA256,api.EXPECTED_FINAL_SHA256,
                     api.EXPECTED_INDEPENDENT_AUDIT_SHA256,
                     api.EXPECTED_OFFLINE_VERIFIER_SHA256,
                     api.EXPECTED_METADATA_BUNDLE_SHA256):
            self.assertEqual(len(item),64)
            self.assertEqual(bytes.fromhex(item).hex(),item)
    def test_duplicate_json_key_rejected(self):
        with self.assertRaisesRegex(api.PhysicalAuditHold,"DUPLICATE_JSON_KEY"):
            api.strict(b'{"x":1,"x":2}')
    def test_nonfinite_json_rejected(self):
        with self.assertRaisesRegex(api.PhysicalAuditHold,"NONFINITE_JSON"):
            api.strict(b'{"x":NaN}')
    def test_zero_length_input_is_not_private_evidence(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"zero"
            p.touch(mode=0o600)
            with self.assertRaisesRegex(api.PhysicalAuditHold,"PRIVATE_INPUT_BOUNDS"):
                api.private_read(p,1024)
    def test_public_input_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"source.json"
            p.write_text("test")
            p.chmod(0o644)
            with self.assertRaisesRegex(api.PhysicalAuditHold,"PRIVATE_INPUT_BOUNDS"):
                api.private_read(p,1024)
    def test_symlink_not_trusted(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"source.json"
            p.write_text("test")
            p.chmod(0o600)
            other=Path(d)/"symlink"
            other.symlink_to(p)
            with self.assertRaisesRegex(api.PhysicalAuditHold,"INPUT_PATH_SCOPE"):
                api.private_read(other,1024)
    def test_report_write_once_permissions(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"output"
            os.chmod(d,0o700)
            digest=api.write_new(p,{"sample":"research"})
            self.assertEqual(digest,hashlib.sha256(p.read_bytes()).hexdigest())
            self.assertEqual(p.stat().st_mode & 0o777,0o600)
            with self.assertRaises(FileExistsError):
                api.write_new(p,{"sample":"mutated"})
    def test_zero_promotion_and_live_trade_contracts(self):
        self.assertEqual(api.FIRST_ANCHOR_MS % 300000,0)
        self.assertGreater(api.STEP_MS,api.HORIZON_MS)
        self.assertGreater(api.REQUIRED_BPS,0)

@unittest.skipUnless(HAS_ACTUAL,"actual owner-private frozen pocket4 artifacts not supplied")
class ActualFrozenEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.actual=Path(BUNDLE)
        self.episodes=Path(EPISODES)
        self.verifier=Path(VERIFIER)
    def fixture(self):
        tmp=tempfile.TemporaryDirectory()
        root=Path(tmp.name)
        root.chmod(0o700)
        episodes=root/"episodes"
        episodes.mkdir(mode=0o700)
        for n in ("01","02","03"):
            target=episodes/("window-"+n+"-original.zip")
            shutil.copy2(self.episodes/target.name,target)
            target.chmod(0o600)
        bundle=root/"metadata.zip"
        shutil.copy2(self.actual,bundle)
        bundle.chmod(0o600)
        code=root/"verifier.py"
        shutil.copy2(self.verifier,code)
        code.chmod(0o600)
        return tmp,root,bundle,episodes,code
    def test_actual_four_window_replay(self):
        r=api.check(self.actual,self.episodes,self.verifier)
        self.assertTrue(r["all_four_original_windows_checked"])
        self.assertTrue(r["initial_censored_window_preserved"])
        self.assertEqual((r["eligible_price_only_predictions"],
                          r["censored_windows"],r["direction_correct_count"]),
                         (3,1,1))
        self.assertEqual(r["broker_queries_or_orders_sent"],0)
        self.assertFalse(r["model_trading_alpha_proven"])
    def test_changed_campaign_container_refused(self):
        t,root,b,e,v=self.fixture()
        try:
            with b.open("ab") as f: f.write(b"x")
            with self.assertRaisesRegex(api.PhysicalAuditHold,
                                        "PINNED_CAMPAIGN_METADATA_BUNDLE_CHANGED"):
                api.check(b,e,v)
        finally:t.cleanup()
    def test_missing_original_episode_not_relabelled(self):
        t,root,b,e,v=self.fixture()
        try:
            (e/"window-02-original.zip").unlink()
            with self.assertRaises(FileNotFoundError):
                api.check(b,e,v)
        finally:t.cleanup()
    def test_mutated_original_quote_zip_refused(self):
        t,root,b,e,v=self.fixture()
        try:
            f=e/"window-03-original.zip"
            with f.open("ab") as stream:stream.write(b"garbage")
            with self.assertRaisesRegex(api.PhysicalAuditHold,
                                        "PORTABLE_SOURCE_ZIP_CHANGED"):
                api.check(b,e,v)
        finally:t.cleanup()
    def test_swapped_episode_indices_refused(self):
        t,root,b,e,v=self.fixture()
        try:
            left=e/"window-01-original.zip"
            right=e/"window-02-original.zip"
            a=left.read_bytes()
            left.write_bytes(right.read_bytes())
            right.write_bytes(a)
            with self.assertRaisesRegex(api.PhysicalAuditHold,
                                        "PORTABLE_SOURCE_ZIP_CHANGED"):
                api.check(b,e,v)
        finally:t.cleanup()
    def test_self_reissued_verifier_code_refused(self):
        t,root,b,e,v=self.fixture()
        try:
            with v.open("ab") as stream:stream.write(b"\n# forged verifier\n")
            with self.assertRaisesRegex(api.PhysicalAuditHold,
                                        "OFFLINE_VERIFIER_CODE_CHANGED"):
                api.check(b,e,v)
        finally:t.cleanup()
    def test_untrusted_broker_signed_label_not_promoted(self):
        r=api.check(self.actual,self.episodes,self.verifier)
        self.assertFalse(r["broker_signed_market_source"])
        self.assertFalse(r["independent_product_owner_or_witness_enrolled"])
        self.assertFalse(r["native_product_dataset_qualified"])
        self.assertFalse(r["actual_after_cost_execution_pnl_observed"])
        self.assertFalse(r["automatic_training_or_promotion"])
        self.assertFalse(r["trade_authority"])
if __name__=="__main__":unittest.main()
