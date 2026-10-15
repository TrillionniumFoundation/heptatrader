"""Original EUR/USD PAPER behavior, with optional opening hysteresis retired."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts'))
import heptatrader_attributed_driver as launcher
import heptatrader_paper_driver as original


class OriginalFxEntryTests(unittest.TestCase):
    def test_single_original_062_directional_decision_eligible(self):
        prior=original.INSTRUMENT
        try:
            original.INSTRUMENT='EUR.USD'
            self.assertEqual(original.CONF,0.62)
            self.assertTrue(original.entry_candidate_allowed(
                {'action':'BUY','confidence':0.62}))
            self.assertTrue(original.entry_candidate_allowed(
                {'action':'SELL','confidence':0.62}))
            self.assertFalse(original.entry_candidate_allowed(
                {'action':'BUY','confidence':0.6199}))
            self.assertFalse(original.entry_candidate_allowed(
                {'action':'HOLD','confidence':1.0}))
            self.assertEqual(original.fx_order_quantity(1.12),89285.0)
            self.assertEqual(original.FX_TARGET_NOTIONAL_USD,100000.0)
            self.assertEqual(original.FX_MAX_QUANTITY,100000.0)
        finally:
            original.INSTRUMENT=prior

    def test_retired_churn_manifest_cannot_reenable_entry_filter(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            for instrument in ('EUR.USD','BTC.USD'):
                with self.subTest(instrument=instrument):
                    f=root/(instrument+'.json')
                    f.write_text(json.dumps({
                        'schema':'heptatrader.attribution-producer.v1',
                        'instrument':instrument,'execution_domain':'PAPER',
                        'fx_churn_profile_sha256':'a'*64
                    }))
                    f.chmod(0o600)
                    with self.assertRaisesRegex(ValueError,'FX_CHURN_RETIRED'):
                        launcher.load_manifest(f)

    def test_fx_opening_hysteresis_source_and_installer_retired(self):
        self.assertFalse((ROOT/'scripts/hepta_fx_churn.py').exists())
        self.assertFalse((ROOT/'scripts/install_fx_churn.py').exists())
        self.assertFalse((ROOT/'docs/technical/fx-opening-hysteresis.md').exists())


if __name__=='__main__':
    unittest.main()
