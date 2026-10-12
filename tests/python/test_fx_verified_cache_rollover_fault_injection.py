"""End-to-end original-cache synthetic roll/cold/recovery/corruption tests.

This suite ONLY changes a TemporaryDirectory and never touches original IB
PAPER quote segments, active services or a real account.
"""
import json,hashlib,tempfile,unittest
from pathlib import Path
from contextlib import redirect_stdout
from io import StringIO
from train_v2.fx_verified_cache_rollover_fault_injection import main

class RolloverTests(unittest.TestCase):
    def test_eight_phases_and_no_product_authority(self):
        with tempfile.TemporaryDirectory() as t:
            path=Path(t)/"synthetic-canonical-cache-v1.json"
            with redirect_stdout(StringIO()):
                main(path)
            raw=path.read_bytes()
            report=json.loads(raw)
            self.assertEqual(
                report["schema"],
                "heptatrader.fx-verified-cache-isolated-archive-roller-adversarial.v1")
            self.assertEqual(report["phases_count"],8)
            self.assertEqual([x["phase"] for x in report["phases"]],[
                "initial_cold","warm_before_rollover",
                "rollover_promotion","warm_after_rollover",
                "cold_restart_after_rollover",
                "synthetic_uncheckpointed_page",
                "writer_only_checkpoint_recovery",
                "corrupt_same_size_archived_source"])
            self.assertTrue(report["source_archive_rollover_32_original_segments_checked"])
            self.assertTrue(report["cold_restart_exact_orig_quote_parity_checked"])
            self.assertTrue(report["two_checkpoints_and_power_loss_recovery_checked"])
            self.assertTrue(report["tampered_source_fails_closed"])
            self.assertFalse(report["real_ib_source_changed"])
            self.assertFalse(report["real_product_monitor_changed"])
            self.assertFalse(report["product_rollout_qualified"])
            self.assertFalse(report["trade_authority"])
            self.assertEqual(report["broker_orders_sent"],0)
            self.assertEqual(path.stat().st_mode & 0o777,0o600)
            self.assertEqual(len(hashlib.sha256(raw).hexdigest()),64)

    def test_write_once_never_relabels_prior_original_report(self):
        with tempfile.TemporaryDirectory() as t:
            path=Path(t)/"immutable.json"
            path.write_text("OLD_FAILED_EVIDENCE")
            with redirect_stdout(StringIO()),self.assertRaisesRegex(
                    AssertionError,"NEW_OUTPUT_PATH_REQUIRED"):
                main(path)
            self.assertEqual(path.read_text(),"OLD_FAILED_EVIDENCE")

if __name__=="__main__":
    unittest.main()
