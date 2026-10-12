"""Adversarial tests for the non-trading Ridge -> original-quote research lane."""
from __future__ import annotations
import hashlib, json, math, os, stat, tempfile, unittest
from decimal import Decimal
from pathlib import Path
import numpy as np
from historical_training.eurusd_walkforward_cost import features as historical_features
from train_v2.fx_ridge_original import (
    HORIZON_MS, bar_close_features, cost_hurdle, ResearchHold, SCHEMA, HIST_REF,
)
from train_v2.fx_ridge_outcome import verify_price_maturity, verify_original_prefixes

def source_rows(n=13):
    anchor=(1791795600*1000//300000)*300000
    rows=[]
    for k in range(n):
        end=anchor-(12-k)*300000
        mid=1.12+0.00003*math.sin(k/2)+0.000003*k
        rows.append({"source_id":hashlib.sha256(str(k).encode()).hexdigest(),
                     "event_at_ms":end-700,"available_at_ms":end-550,
                     "valid_until_ms":end+4000,
                     "bid":str(mid),"ask":str(mid)})
    return rows,anchor

class FeatureTests(unittest.TestCase):
    def test_historical_feature_math_parity(self):
        rows,anchor=source_rows()
        ts0=anchor//1000-13*300
        bars=[]
        for j in range(1002):
            if j in range(13):
                mid=float(rows[j]["bid"])
            else:mid=1.125+0.000001*j
            bars.append({"time":ts0+j*300,
                         "midpoint":{"open":str(mid),"close":str(mid)}})
        _,x=bar_close_features(rows,anchor)
        original=historical_features(bars)["x"][0]
        np.testing.assert_allclose(x,original,rtol=0,atol=1e-9)

    def test_missing_causal_bucket_fails(self):
        rows,anchor=source_rows()
        with self.assertRaisesRegex(ResearchHold,"MISSING_CAUSAL"):
            bar_close_features(rows[:-1],anchor)

    def test_duplicate_source_receipt_fails(self):
        rows,anchor=source_rows()
        rows[-1]["source_id"]=rows[-2]["source_id"]
        with self.assertRaisesRegex(ResearchHold,"DUPLICATED_INPUT_QUOTE"):
            bar_close_features(rows,anchor)

    def test_late_observation_cannot_be_past_close(self):
        rows,anchor=source_rows()
        rows[4]["available_at_ms"]+=800
        with self.assertRaisesRegex(ResearchHold,"MISSING_CAUSAL"):
            bar_close_features(rows,anchor)

    def test_current_spread_counted_once(self):
        cost={"quote_spread_full_bps":{"p99":3.5},
              "fx_commission_one_way_bps":{"p90":0.7}}
        r=cost_hurdle(1.12,1.12001,cost)
        self.assertAlmostEqual(r["required_absolute_prediction_bps"],10.9,places=7)

    def test_nonfinite_cost_rejected(self):
        with self.assertRaisesRegex(ResearchHold,"HISTORICAL_COST_RECEIPT_INVALID"):
            cost_hurdle(1.12,1.12001,{"quote_spread_full_bps":{"p99":float("nan")},
                                      "fx_commission_one_way_bps":{"p90":0.7}})

    def test_crossed_original_quote_rejected(self):
        with self.assertRaisesRegex(ResearchHold,"ORIGINAL_SPREAD_INVALID"):
            cost_hurdle(1.13,1.12,{"quote_spread_full_bps":{"p99":1.},
                                  "fx_commission_one_way_bps":{"p90":1.}})

class MaturityTests(unittest.TestCase):
    def frozen(self):
        rows,anchor=source_rows()
        return {"anchor_ms":anchor,"outcome_mature_not_before_ms":anchor+HORIZON_MS,
                "history_quote_samples":rows,"forecast_midpoint_bps":0.4,
                "selected_trading_action":"NO_TRADE","actual_execution_reward":False},rows,anchor

    def test_requires_future_time(self):
        d,rows,anchor=self.frozen()
        with self.assertRaisesRegex(ResearchHold,"ORIGINAL_OUTCOME_NOT_YET_MATURE"):
            verify_price_maturity(d,anchor+HORIZON_MS,rows)

    def test_missing_future_quote_fails_closed(self):
        d,rows,anchor=self.frozen()
        with self.assertRaisesRegex(ResearchHold,"NO_ORIGINAL_MATURE_SOURCE"):
            verify_price_maturity(d,anchor+HORIZON_MS+7000,rows)

    def test_first_original_mature_quote_only(self):
        d,rows,anchor=self.frozen()
        due=anchor+HORIZON_MS
        early=dict(rows[-1],source_id="f"*64,event_at_ms=due+200,
                   available_at_ms=due+300,bid="1.121",ask="1.121")
        later=dict(rows[-1],source_id="e"*64,event_at_ms=due+1200,
                   available_at_ms=due+1250,bid="1.14",ask="1.14")
        result=verify_price_maturity(d,due+7000,rows+[later,early])
        self.assertEqual(result["outcome_quote_sha256"],"f"*64)
        self.assertEqual(result["selected_trading_action"],"NO_TRADE")
        self.assertFalse(result["model_efficacy_proven"])

    def test_chosen_trade_mutation_rejected(self):
        d,rows,anchor=self.frozen()
        d["selected_trading_action"]="BUY"
        with self.assertRaisesRegex(ResearchHold,"RESEARCH_ACTION_OR_REWARD_CHANGED"):
            verify_price_maturity(d,anchor+HORIZON_MS+7000,rows)

    def test_original_prefix_append_survives_but_mutation_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp)/"quotes.jsonl"
            raw=b'{"ok":1}\n'
            p.write_bytes(raw)
            p.chmod(0o600)
            receipt=[{"path":str(p),"complete_prefix_bytes":len(raw),
                      "prefix_sha256":hashlib.sha256(raw).hexdigest()}]
            with p.open("ab") as fh:fh.write(b'{"next":2}\n')
            verify_original_prefixes(receipt)
            with p.open("r+b") as fh:fh.write(b'{"ok":2}\n')
            with self.assertRaisesRegex(ResearchHold,"ORIGINAL_PREFIX_CONTENT_CHANGED"):
                verify_original_prefixes(receipt)

if __name__=="__main__":unittest.main()
