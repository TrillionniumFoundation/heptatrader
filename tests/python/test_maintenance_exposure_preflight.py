import unittest
import json
import os
import tempfile
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from hepta_maintenance_exposure_preflight import _read_private, _strict_json, classify


def snapshot(now, fx="0", btc="0"):
    return {"schema":"heptatrader.pnl.v1","scope":"STRATEGY_PERIOD_NOT_ACCOUNT_NAV",
        "observed_at_ms":now-100,"valid_until_ms":now+1000,"complete":True,
        "components":[{"instrument":name,"position":q,"broker_position":q,
          "ledger_complete":True,"valuation_complete":True,
          "settlement_pending_command_ids_total":0}
          for name,q in (("EUR.USD",fx),("BTC.USD",btc))]}


class MaintenancePreflight(unittest.TestCase):
    def test_flat_is_observation_only(self):
        self.assertEqual(classify(snapshot(10000),10000),[])
    def test_fx_short_blocks(self):
        self.assertIn("EUR.USD:EXPOSED",classify(snapshot(10000,fx="-89201"),10000))
    def test_btc_exposure_blocks(self):
        self.assertIn("BTC.USD:EXPOSED",classify(snapshot(10000,btc=".0001"),10000))
    def test_snapshot_stale_blocks(self):
        self.assertIn("PNL_STALE_UNKNOWN",classify(snapshot(10000),20000))
    def test_unknown_broker_qty_blocks(self):
        s=snapshot(10000);s["components"][0]["broker_position"]=None
        self.assertIn("EUR.USD:POSITION_UNKNOWN",classify(s,10000))
    def test_mismatch_blocks(self):
        s=snapshot(10000);s["components"][0]["broker_position"]="1"
        self.assertIn("EUR.USD:POSITION_MISMATCH",classify(s,10000))
    def test_pending_blocks(self):
        s=snapshot(10000);s["components"][0]["settlement_pending_command_ids_total"]=1
        self.assertIn("EUR.USD:SETTLEMENT_PENDING",classify(s,10000))
    def test_missing_scope_blocks(self):
        s=snapshot(10000);s["scope"]="ACCOUNT_NAV"
        self.assertIn("PNL_SCOPE_INVALID",classify(s,10000))
    def test_duplicate_key_cannot_overwrite_private_snapshot(self):
        with self.assertRaisesRegex(ValueError,'DUPLICATE_SNAPSHOT_FIELD'):
            _strict_json('{"position":"-89000","position":"0"}')

    def test_nonfinite_and_forged_expiry_refused(self):
        with self.assertRaisesRegex(ValueError,'NONFINITE_SNAPSHOT'):
            _strict_json('{"position":NaN}')
        self.assertIn('PNL_STALE_UNKNOWN',classify(snapshot(10000),15000))

    def test_symlink_and_exposed_read_permissions_refused(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);p=root/'snapshot.json'
            p.write_text(json.dumps(snapshot(10000)));p.chmod(0o600)
            self.assertEqual(_read_private(p)['schema'],'heptatrader.pnl.v1')
            alias=root/'alias.json';alias.symlink_to(p)
            with self.assertRaises(OSError): _read_private(alias)
            p.chmod(0o644)
            with self.assertRaisesRegex(ValueError,'SNAPSHOT_PRIVATE_SCOPE'):
                _read_private(p)

    def test_malformed_instrument_field_reports_unknown_without_crashing(self):
        state=snapshot(10000)
        state['components'][0]['instrument']={'fake':'EUR.USD'}
        self.assertIn('INSTRUMENT_SET_CHANGED',classify(state,10000))
        state['components'][0]['instrument']=None
        self.assertIn('INSTRUMENT_SET_CHANGED',classify(state,10000))
        state['components'][0]=None
        self.assertIn('INSTRUMENT_SET_CHANGED',classify(state,10000))

    def test_unknown_position_never_infers_zero(self):
        s=snapshot(10000);s['components'][0]['position']='NaN'
        self.assertIn('EUR.USD:POSITION_UNKNOWN',classify(s,10000))

    def test_no_missing_instrument(self):
        s=snapshot(10000);s["components"].pop()
        self.assertIn("INSTRUMENT_SET_UNKNOWN",classify(s,10000))


if __name__=="__main__":unittest.main()
