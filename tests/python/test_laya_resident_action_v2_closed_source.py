"""Genuine closed-future source custody for future-only Laya v2 training."""
from __future__ import annotations
from bisect import bisect_right
import json,tempfile,unittest
from pathlib import Path

from laya_cell.dataset import BIN_EDGES_BPS
from laya_cell.integrity import ContractError,digest,sha_file
from laya_cell.resident_action_v2_closed_source import (
    collect_parent_closed_train,check_train_source)

CUTOFF=1800004000000
PARENT="a"*64
CANDIDATE="b"*64
PROBS=[.05,.05,.05,.7,.05,.05,.05]
ACTION=[.9,.1]


def save(path,data):
    path.write_text(json.dumps(data,sort_keys=True,separators=(",",":"))+"\n")


class ClosedOriginalParentSourceTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.root=Path(t.name)
        self.dir=self.root/"plan1"
        self.dir.mkdir()
        self.start=CUTOFF-7200000
        self.plan={"schema":"heptatrader.laya-resident-future-plan.v1",
                   "candidate_id":CANDIDATE,"trial":1,
                   "start_ms":self.start,"outcome_end_ms":CUTOFF-1000,
                   "rule":{"slots":16,"slot_spacing_ms":360000,
                           "input_grace_ms":60000,"horizon_ms":300000}}
        self.plan["plan_id"]=digest(self.plan)
        self._plan()
        self._create_rows()
    def _plan(self):
        save(self.dir/"plan.json",self.plan)
        save(self.dir/"qualification.json",
             {"plan_id":self.plan["plan_id"],"candidate_id":CANDIDATE,
              "state":"REJECTED","observed":16})
    def _create_rows(self):
        for i in range(16):
            anchor=self.start+i*360000+3000
            st={"instrument":"EUR.USD","anchor_ms":anchor,
                "reference_mid":"1.00001","horizon_sec":300,
                "columns":["offset_ms","bid","ask"],
                "quotes":[[j*10000,"1.00000","1.00002"] for j in (-5,-4,-3,-2,-1,0)],
                "quantity":"underlying MID return; not execution profit"}
            target=bisect_right(BIN_EDGES_BPS,0 if i<4 else 100)
            correct=target==3
            self.assertEqual(correct,i<4)
            prediction={"parent_id":PARENT,"candidate_id":CANDIDATE,
                        "plan_id":self.plan["plan_id"],"slot":i,
                        "anchor_ms":anchor,"created_at_ms":anchor+1000,
                        "trained_until_ms":anchor-3000,
                        "input":st,"input_id":digest(st),
                        "reference_mid":1.00001,
                        "parent_probs":PROBS,"candidate_act_escalate_probs":ACTION}
            pp=self.dir/("prediction-%03d.json"%i)
            save(pp,prediction)
            y={"candidate_id":CANDIDATE,"plan_id":self.plan["plan_id"],
                "slot":i,"status":"OBSERVED",
                "evaluated_at_ms":anchor+302000,
                "outcome_ms":anchor+301000,"target_class":target,
                "return_bps":0. if i<4 else 100.,
                "prediction_file_sha256":sha_file(pp),
                "outcome_receipt":"c"*64,
                "parent_brier":sum((p-float(j==target))**2 for j,p in enumerate(PROBS)),
                "action_head_brier":2*(ACTION[0]-float(correct))**2}
            save(self.dir/("outcome-%03d.json"%i),y)
    def test_nullable_action_keeps_all_original_parent_correctness_labels(self):
        from laya_cell.resident_training_v2 import ACTION_OUTPUT
        self.plan.update(action_output_profile=ACTION_OUTPUT,action_training_profile_id=None,
                         action_optimizer_admitted=False)
        self.plan.pop("plan_id");self.plan["plan_id"]=digest(self.plan);self._plan()
        for i in range(16):
            pp=self.dir/("prediction-%03d.json"%i);op=self.dir/("outcome-%03d.json"%i)
            prediction=json.loads(pp.read_text())
            prediction.update(plan_id=self.plan["plan_id"],action_output_profile=ACTION_OUTPUT,
                candidate_act_escalate_probs=None,action_inference={"frozen_profile_id":None,
                "action_correct_probs":None,"model_or_trade_permission":False})
            save(pp,prediction)
            outcome=json.loads(op.read_text())
            outcome.update(plan_id=self.plan["plan_id"],prediction_file_sha256=sha_file(pp),
                           action_head_brier=None)
            save(op,outcome)
        result=self.collect();manifest=check_train_source(result)
        self.assertEqual(manifest["training_rows"],16)
        self.assertEqual(manifest["frozen_parent_correct_ACT"],4)
        self.assertEqual(manifest["frozen_parent_wrong_ESCALATE"],12)
        self.assertEqual(manifest["action_abstention_counts"],{"NO_BALANCED_ACTION_TRAINING":16})

    def collect(self):
        return collect_parent_closed_train(self.root,PARENT,CUTOFF)
    def test_real_signed_closed_rows_make_balanced_v2_population(self):
        x=self.collect();m=check_train_source(x)
        self.assertEqual(m["training_rows"],16)
        self.assertEqual(m["frozen_parent_correct_ACT"],4)
        self.assertEqual(m["frozen_parent_wrong_ESCALATE"],12)
        self.assertTrue(m["action_training_two_class_supported"])
        self.assertTrue(m["no_pending_trial_outcomes_included"])
        self.assertFalse(m["model_or_trading_authority"])
    def test_all_original_inputs_bounded_and_full_laya_question(self):
        rows=self.collect()["rows"]
        self.assertEqual(len(set(r["row_id"] for r in rows)),16)
        self.assertEqual(rows[0]["state"]["quotes"][-1][0],0)
        self.assertEqual(rows[0]["question"]["t"],"choice")
    def test_pending_future_plan_never_contributes_training_label(self):
        second=self.root/"pending";second.mkdir()
        save(second/"plan.json",self.plan)
        x=self.collect()
        self.assertEqual(x["manifest"]["pending_plans_excluded"],1)
        self.assertEqual(x["manifest"]["training_rows"],16)
    def test_late_closed_plan_cannot_be_backfilled_under_earlier_cutoff(self):
        self.plan["outcome_end_ms"]=CUTOFF+1
        self.plan.pop("plan_id")
        self.plan["plan_id"]=digest(self.plan)
        self._plan()
        x=self.collect()
        self.assertEqual(x["manifest"]["training_rows"],0)
    def test_corrupt_saved_prediction_hash_rejected(self):
        p=self.dir/"prediction-000.json"
        item=json.loads(p.read_text());item["reference_mid"]=9
        save(p,item)
        with self.assertRaisesRegex(ContractError,"PREDICTION_CHANGED"):
            self.collect()
    def test_a_different_parent_must_not_enter_frozen_training(self):
        p=self.dir/"prediction-000.json";o=self.dir/"outcome-000.json"
        v=json.loads(p.read_text());v["parent_id"]="d"*64;save(p,v)
        result=json.loads(o.read_text());result["prediction_file_sha256"]=sha_file(p);save(o,result)
        with self.assertRaisesRegex(ContractError,"PREDICTION_NONCAUSAL"):
            self.collect()
    def test_fake_observed_price_class_is_detected(self):
        o=self.dir/"outcome-000.json"
        v=json.loads(o.read_text());v["target_class"]=0;save(o,v)
        with self.assertRaisesRegex(ContractError,"PRICE_LABEL"):
            self.collect()
    def test_invalid_quote_midpoint_fails_even_with_fresh_input_hash(self):
        p=self.dir/"prediction-000.json";o=self.dir/"outcome-000.json"
        v=json.loads(p.read_text())
        v["input"]["quotes"][-1][2]="0.9999"
        v["input_id"]=digest(v["input"]);save(p,v)
        item=json.loads(o.read_text());item["prediction_file_sha256"]=sha_file(p);save(o,item)
        with self.assertRaisesRegex(ContractError,"QUOTE_PRICE"):
            self.collect()
    def test_malicious_artifact_class_mutation_rejected(self):
        data=self.collect()
        data["rows"][0]["parent_correct"]=not data["rows"][0]["parent_correct"]
        with self.assertRaisesRegex(ContractError,"SOURCE_CHANGED"):
            check_train_source(data)
    def test_invalid_outcome_or_missing_future_clock_rejected(self):
        o=self.dir/"outcome-000.json"
        x=json.loads(o.read_text());x["evaluated_at_ms"]=CUTOFF+1;save(o,x)
        with self.assertRaisesRegex(ContractError,"PREDICTION_NONCAUSAL"):
            self.collect()
    def test_unexpected_future_or_duplicate_training_anchors_detected(self):
        new=self.root/"second";new.mkdir()
        save(new/"plan.json",self.plan)
        save(new/"qualification.json",{"plan_id":self.plan["plan_id"],
             "candidate_id":CANDIDATE,"state":"REJECTED"})
        for f in self.dir.glob("*.json"):
            if f.name.startswith(("prediction-","outcome-")):
                (new/f.name).write_bytes(f.read_bytes())
        with self.assertRaisesRegex(ContractError,"DUPLICATE_TRAINING_ANCHOR"):
            self.collect()


if __name__=="__main__":unittest.main()
