"""Integration guard: primary final path MUST enter owner-private evidence() intact."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from train_v2.fx_fourwindow_primary_terminal_reconcile import verify_terminal
from train_v2.fx_first_three_original_crosshost import CrosshostHold

MODULE="train_v2.fx_fourwindow_primary_terminal_reconcile"

class PrimaryFullPathTests(unittest.TestCase):
    def fixture(self,root):
        p=Path(root)/"actual-original-primary-final.json"
        raw=b'{"schema":"heptatrader.fx-original-future-price-cost-calendar.v1.final.v1","trade_authority":false}\n'
        p.write_bytes(raw)
        p.chmod(0o600)
        return p,hashlib.sha256(raw).hexdigest()

    def exercise(self,p):
        with patch(MODULE+".verify_4",return_value={"result":"COMPLETE_4X4_CENSORED_RESEARCH_PROVEN_NO_TRADE"}) as physical,\
             patch(MODULE+".first_three_verify",return_value={"original_decisions_censored":3}) as first,\
             patch(MODULE+".validate_manifest",return_value={"trade_authority":False}) as validation:
            got=verify_terminal(
                firstthree_root=Path("/unused/firstthree"),
                cache1005_root=Path("/unused/cache1005"),
                fourth_root=Path("/unused/fourth"),
                primary_final_file=p,
            )
            physical.assert_called_once()
            first.assert_called_once()
            validation.assert_called_once()
            return got

    def test_actual_private_full_file_path_verified(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d).chmod(0o700)
            p,h=self.fixture(d)
            result=self.exercise(p)
            self.assertEqual(result["original_primary_final_sha256"],h)
            self.assertTrue(result["crosshost_allfour_verified"])
            self.assertFalse(result["trade_authority"])

    def test_public_original_worker_receipt_refused(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d).chmod(0o700)
            p,h=self.fixture(d)
            p.chmod(0o644)
            with self.assertRaisesRegex(CrosshostHold,"UNSAFE_OR_OVERSIZE_SOURCE"):
                self.exercise(p)

    def test_symlink_lookalike_final_refused(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d).chmod(0o700)
            p,h=self.fixture(d)
            link=Path(d)/"spoof.json"
            link.symlink_to(p)
            with self.assertRaisesRegex(CrosshostHold,"RELATIVE_OR_SYMLINK_SOURCE"):
                self.exercise(link)

if __name__=="__main__":unittest.main()
