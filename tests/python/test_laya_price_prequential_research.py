"""As-of outcome availability is the sole basis for online recalibration."""
import copy
import json
import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from laya_cell.integrity import ContractError
from laya_cell.price_prequential_research import (
    asof_prior, score_asof, collect_available, experiment)
from laya_cell.price_quality_research import prior


def row(i, day="2026-10-07", target=3):
    anchor=1791360000000+i*360000
    # Normal outcome + evaluation follows the five-minute horizon.
    return {"anchor_ms":anchor,"label_available_at_ms":anchor+303000,
            "utc_day":day,"target_class":target,
            "parent_probabilities":[.33,.06,.08,.24,.12,.10,.07],
            "candidate_probabilities":[.34,.06,.08,.23,.12,.10,.07],
            "plan_id":"synthetic"}


def dataset():
    rows=[]
    for idx,day in enumerate(["2026-10-07","2026-10-08","2026-10-09"]):
        # UTC days fixed; keep unique anchor across days.
        from datetime import datetime,timezone
        start=int(datetime.fromisoformat(day+"T07:00:00+00:00").timestamp()*1000)
        for j in range(25):
            r=row(idx*25+j,day,target=(j+idx)%7)
            r["anchor_ms"]=start+j*360000
            r["label_available_at_ms"]=r["anchor_ms"]+303000
            rows.append(r)
    return rows


class AsOfTests(unittest.TestCase):
    def setUp(self):
        self.history=dataset()
        self.weights=prior(self.history[:25],.5)["weights"]
    def test_exact_future_outcome_is_excluded_before_real_availability(self):
        past=self.history[0]
        now=past["anchor_ms"]+302999
        q,count=asof_prior([past],now,self.weights,0,1.0)
        self.assertEqual(count,0)
        self.assertEqual(q,self.weights)
        after=past["label_available_at_ms"]
        q2,count2=asof_prior([past],after,self.weights,0,1.0)
        self.assertEqual(count2,1)
        self.assertNotEqual(q2,self.weights)
    def test_late_evaluation_is_not_assumed_available_after_event(self):
        r=copy.deepcopy(self.history[0]);now=r["anchor_ms"]+305000
        r["label_available_at_ms"]=r["anchor_ms"]+600000
        _,admitted=asof_prior([r],now,self.weights,24,1.0)
        self.assertEqual(admitted,0)
    def test_later_test_label_changes_cannot_change_earlier_prediction(self):
        history=copy.deepcopy(self.history)
        asof=history[55]["anchor_ms"]
        p,_=asof_prior(history,asof,self.weights,24,1.0)
        for r in history:
            if r["anchor_ms"]>=asof:r["target_class"]=6
        new,_=asof_prior(history,asof,self.weights,24,1.0)
        self.assertEqual(p,new)
    def test_update_after_outcome_does_change_later_prequential_predictions(self):
        history=copy.deepcopy(self.history)
        asof=history[-1]["anchor_ms"]
        original,_=asof_prior(history,asof,self.weights,0,1.0)
        history[-2]["target_class"]=0
        changed,_=asof_prior(history,asof,self.weights,0,1.0)
        self.assertNotEqual(original,changed)
    def test_invalid_smoothing_and_noncausal_ledger_rejected(self):
        with self.assertRaises(ContractError):
            asof_prior(self.history,self.history[-1]["anchor_ms"],self.weights,24,3.0)
        malformed=copy.deepcopy(self.history[0])
        malformed["label_available_at_ms"]=malformed["anchor_ms"]+1000
        with self.assertRaisesRegex(ContractError,"HISTORY_RECEIPT_INVALID"):
            asof_prior([malformed],self.history[-1]["anchor_ms"],self.weights,0,1.0)
    def test_model_selection_not_a_function_of_future_test_labels(self):
        rows=dataset()
        # Synthetic availability already populated; exercise only experiment logic.
        with patch("laya_cell.price_prequential_research.collect_available",return_value=(rows,{"source":"synthetic"})):
            first=experiment("unused")
        later=copy.deepcopy(rows)
        for r in later[50:]:r["target_class"]=(r["target_class"]+2)%7
        with patch("laya_cell.price_prequential_research.collect_available",return_value=(later,{"source":"synthetic"})):
            second=experiment("unused")
        self.assertEqual(first["validation_selected"],second["validation_selected"])
        self.assertEqual(first["selection_parameters"],second["selection_parameters"])
        self.assertNotEqual(first["test_scores"],second["test_scores"])
    def test_prequential_score_preserves_positive_probability(self):
        v=score_asof(self.history[50:],self.history,self.weights,12,4.0)
        self.assertEqual(v["rows"],25)
        self.assertTrue(math.isfinite(v["brier"]))
        self.assertTrue(v["prequential_updates_from_later_labels_allowed_only_when_past"])
    def test_read_only_availability_checks_actual_event_and_evaluation_time(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            d=root/"closed";d.mkdir()
            r=copy.deepcopy(self.history[0])
            obs={"status":"OBSERVED","plan_id":"synthetic","anchor_ms":r["anchor_ms"],
                 "outcome_ms":r["anchor_ms"]+300000,
                 "evaluated_at_ms":r["anchor_ms"]+303000}
            path=d/"outcome-000.json";path.write_text(json.dumps(obs))
            with patch("laya_cell.price_prequential_research.collect_closed",
                       return_value=([r],{"source":"synthetic"})):
                got,_=collect_available(root)
                self.assertEqual(got[0]["label_available_at_ms"],obs["evaluated_at_ms"])
                obs["evaluated_at_ms"]=r["anchor_ms"]+200000
                path.write_text(json.dumps(obs))
                with self.assertRaises(ContractError):collect_available(root)
    def test_report_is_never_automatic_promotion(self):
        rows=dataset()
        with patch("laya_cell.price_prequential_research.collect_available",return_value=(rows,{"source":"synthetic"})):
            r=experiment("unused")
        self.assertFalse(r["claims"]["no_trade_authority"] is False)
        self.assertFalse(r["claims"]["no_model_promotion"] is False)
        self.assertTrue(r["claims"]["retrospective_prequential_only"])


if __name__=="__main__":
    unittest.main()
