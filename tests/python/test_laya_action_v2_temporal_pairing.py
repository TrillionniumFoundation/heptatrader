"""Causal UTC-day matched genuine Action V2 rows, CPU/source-only tests."""
from __future__ import annotations

import unittest

from laya_cell.action_v2_temporal_pairing import (
    source_time_matched_action_pairs,
)
from laya_cell.integrity import ContractError, digest
from laya_cell.resident_action_v2_closed_source import SCHEMA as SOURCE_SCHEMA

CUTOFF=1900000000000
DAY=(CUTOFF//86400000-4)*86400000
PARENT="a"*64


def artifact(*,acts=4,wrong_next_day=False,wrong_close=False):
    data=[]
    for i in range(acts):
        anchor=DAY+(i+2)*3600000
        data.append({"parent_id":PARENT,"row_id":f"{i+1:064x}",
                     "anchor_ms":anchor,
                     "future_outcome_available_ms":anchor+310000,
                     "parent_correct":True})
    for i in range(16-acts):
        anchor=(DAY+(i%4+2)*3600000+
                (120000 if wrong_close else 420000)+i//4*660000)
        if wrong_next_day:
            anchor+=86400000
        data.append({"parent_id":PARENT,"row_id":f"{100+i:064x}",
                     "anchor_ms":anchor,
                     "future_outcome_available_ms":anchor+310000,
                     "parent_correct":False})
    m={"schema":SOURCE_SCHEMA,"frozen_parent_id":PARENT,
       "training_cutoff_ms":CUTOFF,
       "training_rows":len(data),"rows_digest":digest(data),
       "action_training_two_class_supported":acts>=4,
       "no_pending_trial_outcomes_included":True,
       "not_independent_future_evaluation":True,
       "model_or_trading_authority":False}
    m["manifest_id"]=digest(m)
    return {"manifest":m,"rows":data}


class TemporalActionPairsTests(unittest.TestCase):
    def test_same_day_nearest_genuine_pairs_stay_disjoint(self):
        rows=artifact()
        x=source_time_matched_action_pairs(rows)
        self.assertEqual(len(x["pairs"]),4)
        self.assertTrue(x["eligible"])
        self.assertTrue(x["training_only"])
        self.assertFalse(x["model_or_trade_permission"])
        selected=[]
        for i,j in x["pairs"]:
            a,b=rows["rows"][i],rows["rows"][j]
            self.assertTrue(a["parent_correct"])
            self.assertFalse(b["parent_correct"])
            self.assertEqual(a["anchor_ms"]//86400000,
                             b["anchor_ms"]//86400000)
            self.assertGreaterEqual(abs(a["anchor_ms"]-b["anchor_ms"]),300000)
            selected.extend([a["anchor_ms"],b["anchor_ms"]])
        self.assertTrue(all(abs(v-w)>=300000
                            for k,v in enumerate(selected)
                            for w in selected[k+1:]))

    def test_cross_day_majority_cannot_be_forced_into_pairs(self):
        x=source_time_matched_action_pairs(artifact(wrong_next_day=True))
        self.assertFalse(x["eligible"])
        self.assertEqual(x["pairs"],[])
        self.assertEqual(x["skipped_act_rows"],4)

    def test_single_day_without_four_genuine_act_refused(self):
        x=source_time_matched_action_pairs(artifact(acts=3))
        self.assertFalse(x["eligible"])
        self.assertEqual(x["reason"],"NO_GENUINE_TWO_CLASS_PROFILE")

    def test_invalid_spacing_cannot_relax_original_five_minute_horizon(self):
        with self.assertRaisesRegex(ContractError,"PAIR_WINDOW"):
            source_time_matched_action_pairs(artifact(),min_spacing_ms=1000)

    def test_only_overlapping_negatives_refused_if_max_lag_is_short(self):
        a=artifact(wrong_close=True)
        # Exactly 300 seconds of spacing is required, never two minutes.
        x=source_time_matched_action_pairs(a,max_lag_ms=300000)
        self.assertFalse(x["eligible"])
        self.assertEqual(x["pairs"],[])

    def test_tampered_original_row_without_digest_change_is_refused(self):
        a=artifact()
        a["rows"][0]["parent_correct"]=False
        with self.assertRaisesRegex(ContractError,"SOURCE_CHANGED"):
            source_time_matched_action_pairs(a)

    def test_wrong_parent_id_refused_despite_rehashed_source(self):
        a=artifact()
        a["rows"][0]["parent_id"]="b"*64
        a["manifest"]["rows_digest"]=digest(a["rows"])
        a["manifest"]["manifest_id"]=digest(
            {k:v for k,v in a["manifest"].items() if k!="manifest_id"})
        with self.assertRaisesRegex(ContractError,"ROW_TAMPERED"):
            source_time_matched_action_pairs(a)


if __name__=="__main__":
    unittest.main()
