"""Adversarial original-quote-native feature/label research contract tests."""
from __future__ import annotations
from decimal import Decimal
import json
from pathlib import Path
import stat
import tempfile
import unittest

from train_v2.fx_original_native_dataset import (
    BAR_MS, BLOCK_LENGTH_MS, DECISION_OFFSETS_MS, HORIZON_MS, LOOKBACK,
    MIN_UNSEEN_NATIVE_BLOCKS_FOR_RESEARCH_TRAIN, NativeHold,
    causal_price_rows, decision_status, delay_grid, first_mature_original,
    frozen_nonoverlap_subsample, source_closes, write_once_private
)

GRID = 1791799800000

def quote(t, name, *, available=None, until=None, price="1.12000"):
    return {
        "event_at_ms":t, "available_at_ms":t+50 if available is None else available,
        "valid_until_ms":t+5000 if until is None else until,
        "bid":str(Decimal(price)), "ask":str(Decimal(price)+Decimal("0.00001")),
        "source_id":(name*64)[:64],
    }

def full_13(anchor=GRID):
    rows=[]
    for i in range(LOOKBACK):
        end=anchor-(LOOKBACK-1-i)*BAR_MS
        rows.append(quote(end-500, format(i,"x"),price=str(
            Decimal("1.12000")+Decimal(i)*Decimal("0.00001"))))
    return rows

class OriginalQuoteContractTests(unittest.TestCase):
    def test_original_stale_1810_is_not_replayed(self):
        origin=quote(GRID-3744,"a",available=GRID-3609,until=GRID+1256)
        self.assertEqual(decision_status(origin,GRID,1000),"QUOTE_TOO_OLD")
        self.assertEqual(decision_status(origin,GRID,250),"ELIGIBLE_PRICE_ONLY_SOURCE")
        self.assertNotEqual(1000,250)
        self.assertEqual(DECISION_OFFSETS_MS,(250,500,1000))

    def test_future_after_grid_is_not_pregrid_close(self):
        future=quote(GRID+100,"a",available=GRID+120)
        self.assertEqual(decision_status(future,GRID,250),"POSTGRID_QUOTE_NOT_CAUSAL")

    def test_broker_reported_quote_expired(self):
        old=quote(GRID-3500,"a",available=GRID-3300,until=GRID+200)
        self.assertEqual(decision_status(old,GRID,250),
                         "BROKER_REPORTED_QUOTE_EXPIRED")

    def test_invalid_offset_refused(self):
        q=quote(GRID-500,"a")
        with self.assertRaisesRegex(NativeHold,"INVALID_DECISION_OFFSET"):
            decision_status(q,GRID,4501)

    def test_expired_input_at_grid_is_excluded(self):
        rows=full_13()
        rows[-1]["valid_until_ms"]=GRID-1
        rows.append(quote(GRID+100,"f"))
        closes,counts=source_closes(rows,GRID+100)
        self.assertNotIn(GRID,closes)
        self.assertEqual(counts["expired_before_grid"],1)
        self.assertEqual(counts["new_contract_valid_closes"],12)

    def test_strict_last_grid_is_not_unobserved_future_endpoint(self):
        rows=full_13()
        _,counts=source_closes(rows,GRID+2*BAR_MS)
        self.assertEqual(counts["all_endpoints"],LOOKBACK-1)
        self.assertNotIn(GRID, source_closes(rows,GRID+2*BAR_MS)[0])

    def test_no_synthetic_close_at_missing_bucket(self):
        rows=full_13()
        del rows[5]
        closes,_=source_closes(rows,GRID)
        self.assertEqual(len(closes),11)
        rows,stats=causal_price_rows(rows,closes,GRID+HORIZON_MS+5000)
        self.assertEqual(rows,[])
        self.assertGreater(stats["missing_13_contiguous_endpoints"],0)

    def test_offset_counts_are_disjoint(self):
        first=quote(GRID-3600,"a",available=GRID-3500)
        second=quote(GRID+BAR_MS-450,"b")
        report=delay_grid({GRID:first,GRID+BAR_MS:second},
                          [GRID,GRID+BAR_MS,GRID+2*BAR_MS])
        for r in report.values():
            self.assertEqual(r["anchors_considered"],3)
            self.assertEqual(sum(v for k,v in r.items() if k!="anchors_considered"),3)
            self.assertEqual(r["missing_close"],1)

    def test_first_mature_quote_never_cherry_picks_later_favorable(self):
        due=GRID+HORIZON_MS
        earliest=quote(due+100,"a",available=due+5100)
        later=quote(due+800,"b",available=due+900)
        picked,why=first_mature_original([earliest,later],
                                         [due+100,due+800],due)
        self.assertIsNone(picked)
        self.assertEqual(why,"FIRST_FUTURE_QUOTE_LATE_OR_UNAVAILABLE")

    def test_only_first_after_horizon_is_selected(self):
        due=GRID+HORIZON_MS
        first=quote(due+200,"a",available=due+250)
        later=quote(due+700,"b",available=due+750)
        selected,state=first_mature_original([first,later],
                                             [due+200,due+700],due)
        self.assertEqual(state,"MATURE_PRICE_ONLY")
        self.assertEqual(selected["source_id"],"a"*64)

    def test_no_quote_within_5s_is_censored(self):
        due=GRID+HORIZON_MS
        x,reason=first_mature_original([quote(due+7000,"a")],[due+7000],due)
        self.assertIsNone(x)
        self.assertEqual(reason,"NO_ORIGINAL_FUTURE_MARKET_EVENT")

    def test_future_event_cannot_be_mature_before_due(self):
        due=GRID+HORIZON_MS
        q=quote(due+100,"a",available=due-100)
        x,why=first_mature_original([q],[due+100],due)
        self.assertIsNone(x)
        self.assertEqual(why,"FIRST_FUTURE_QUOTE_LATE_OR_UNAVAILABLE")

    def test_same_native_causal_features_and_price_label(self):
        original=full_13()
        end=GRID+HORIZON_MS
        original.append(quote(end+300,"f",available=end+400,price="1.12025"))
        closes,stats=source_closes(original,end+5000)
        rows,counts=causal_price_rows(original,closes,end+5000)
        target=next(r for r in rows if r["anchor_ms"]==GRID)
        self.assertEqual(len(target["features"]),8)
        self.assertEqual(len(target["original_input_quote_ids"]),13)
        self.assertEqual(target["mature_due_ms"],end)
        self.assertEqual(target["mature_original_quote_id"],"f"*64)
        self.assertTrue(target["same_original_source_domain"])
        self.assertFalse(target["actual_executable_fill"])
        self.assertFalse(target["independent_prospective_prediction"])
        self.assertEqual(counts["mature_price_only_rows"],len(rows))
        self.assertFalse(any(r.get("historical_ohlc_backfill",False) for r in rows))

    def test_last_quote_old_at_original_time_causes_no_label(self):
        original=full_13()
        original[-1]["available_at_ms"]=GRID-4600
        original.append(quote(GRID+HORIZON_MS+100,"f"))
        closes,_=source_closes(original,GRID+HORIZON_MS+5000)
        # last receipt age at +1s 5600 -> newer model not admitted
        rows,_=causal_price_rows(original,closes,GRID+HORIZON_MS+5000)
        self.assertFalse(any(r["anchor_ms"]==GRID for r in rows))

    def test_80_min_purge_does_not_reuse_overlapping_horizons(self):
        rows=[{"anchor_ms":GRID+i*BAR_MS} for i in range(24)]
        anchors=frozen_nonoverlap_subsample(rows)
        self.assertEqual(anchors,[GRID,GRID+BLOCK_LENGTH_MS])
        self.assertEqual(BLOCK_LENGTH_MS,80*60000)

    def test_insufficient_same_native_blocks_cannot_fit_model(self):
        self.assertEqual(MIN_UNSEEN_NATIVE_BLOCKS_FOR_RESEARCH_TRAIN,1024)
        self.assertLess(len(frozen_nonoverlap_subsample(
            [{"anchor_ms":GRID+i*BAR_MS} for i in range(300)])),
            MIN_UNSEEN_NATIVE_BLOCKS_FOR_RESEARCH_TRAIN)

    def test_write_once_rejects_duplicate_report(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/"private"
            root.mkdir(mode=0o700)
            p=root/"report.json"
            write_once_private(p,{"trade_authority":False})
            with self.assertRaisesRegex(NativeHold,"OUTPUT_MUST_BE_NEW"):
                write_once_private(p,{"trade_authority":True})
            self.assertEqual(stat.S_IMODE(p.stat().st_mode),0o600)

    def test_public_output_directory_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/"world"
            root.mkdir(mode=0o755)
            with self.assertRaisesRegex(NativeHold,"PRIVATE_OUTPUT_DIR_REQUIRED"):
                write_once_private(root/"bad.json",{"a":1})

    def test_no_unseen_statistical_claim_in_original_labels(self):
        rows=full_13()
        due=GRID+HORIZON_MS
        rows.append(quote(due+100,"f",available=due+250))
        closes,_=source_closes(rows,due+5000)
        out,_=causal_price_rows(rows,closes,due+5000)
        self.assertTrue(out)
        self.assertTrue(all(not v["independent_prospective_prediction"] for v in out))
        self.assertTrue(all(not v["monetary_reward"] for v in out))

if __name__=="__main__":
    unittest.main()
