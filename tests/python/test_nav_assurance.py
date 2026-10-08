from __future__ import annotations
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import hepta_account_pnl as account
import hepta_nav_assurance as assurance
from test_account_cohort_lifecycle import v3_snapshot
from test_account_pnl import period


class NavAssuranceTests(unittest.TestCase):
    def test_complete_numeric_cohort_still_has_no_common_valuation_basis(self):
        raw = v3_snapshot()
        nav = account.normalize_nav(raw, period())
        facts = assurance.assess_nav_assurance(nav, raw, period(), raw['observed_at_ms'])
        self.assertTrue(facts['capture_complete'])
        self.assertTrue(facts['arithmetic_reconciled'])
        self.assertFalse(facts['valuation_basis_attested'])
        self.assertFalse(facts['strict_nav_eligible'])
        self.assertFalse(facts['authoritative'])
        self.assertEqual(facts['reason_codes'], ['BROKER_VALUATION_BASIS_UNATTESTED'])

    def test_forged_flags_and_quoted_prices_cannot_supply_missing_proof(self):
        raw = v3_snapshot()
        nav = account.normalize_nav(raw, period())
        for name in ('strict_nav_eligible', 'valuation_basis_attested', 'capture_complete'):
            forged = dict(nav, **{name: True})
            facts = assurance.assess_nav_assurance(forged, raw, period(), raw['observed_at_ms'])
            self.assertFalse(facts['strict_nav_eligible'])
        raw['fx_quote_witness'] = {'broker_time': raw['observed_at_ms'], 'bid': '1.1', 'ask': '1.2'}
        nav = account.normalize_nav(raw, period())
        self.assertFalse(assurance.assess_nav_assurance(nav, raw, period(), raw['observed_at_ms'])['valuation_basis_attested'])
        raw['capture_cohort']['broker_valuation_tick_attested'] = True
        self.assertFalse(assurance.assess_nav_assurance(nav, raw, period(), raw['observed_at_ms'])['strict_nav_eligible'])

    def test_public_cache_blocks_legacy_consumers_and_preserves_history(self):
        raw = v3_snapshot()
        nav = account.normalize_nav(raw, period())
        with tempfile.TemporaryDirectory() as root:
            out = Path(root)
            historical = out/'first-original.json'
            rejected = out/'rejected-raw.json'
            historical.write_text('immutable historical value')
            rejected.write_text('immutable rejected diagnostic')
            public = assurance.publish_arithmetic_observation(out, nav, raw, period(), raw['observed_at_ms'])
            self.assertEqual(public['schema'], 'heptatrader.account-nav.v2')
            self.assertFalse(public['authoritative'])
            self.assertIsNone(public['nav'])
            self.assertTrue(public['arithmetic_reconciled'])
            self.assertEqual(json.loads((out/'arithmetic-latest.json').read_text()), nav)
            self.assertEqual(historical.read_text(), 'immutable historical value')
            self.assertEqual(rejected.read_text(), 'immutable rejected diagnostic')
            self.assertEqual(len(list(out.glob('first-*'))), 1)
            with self.assertRaises(ValueError):
                account.validate_nav(public, period(), raw['observed_at_ms'])

    def test_stale_or_mismatched_cache_has_no_arithmetic_authority(self):
        raw = v3_snapshot()
        nav = account.normalize_nav(raw, period())
        for at in (raw['started_at_ms'] - 1, raw['started_at_ms'] + 30000):
            facts = assurance.assess_nav_assurance(nav, raw, period(), at)
            self.assertFalse(facts['arithmetic_reconciled'])
        altered = copy.deepcopy(raw)
        altered['connection_id'] += '-other'
        facts = assurance.assess_nav_assurance(nav, altered, period(), raw['observed_at_ms'])
        self.assertFalse(facts['arithmetic_reconciled'])


if __name__ == '__main__':
    unittest.main()
