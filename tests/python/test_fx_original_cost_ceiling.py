"""Adversarial tests for original-quote retrospective cost ceiling; no broker I/O."""
from __future__ import annotations
import copy
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

from train_v2.fx_original_cost_ceiling import (
    analyze, frozen_predict, original_rows, private_json, CeilingError,
    DATA_SCHEMA, CONTRACT_SCHEMA, MODEL_SCHEMA,
    MIN_NONOVERLAP_MS, SCHEMA, run,
)

def model_fixture():
    return {"schema":MODEL_SCHEMA,"mean":[0.]*8,"stddev":[1.]*8,
            "weights":[.1]+[.01]*8,
            "candidate_has_positive_validation_edge":False,
            "historical_validation_selected_action":"NO_TRADE",
            "trade_authority":False,"promotion_allowed":False}

def original_fixture():
    anchor=1791366000000
    rows=[]
    for i in range(3):
        t=anchor+i*MIN_NONOVERLAP_MS
        rows.append({
            "schema":CONTRACT_SCHEMA+".example.v1",
            "anchor_ms":t,"decision_delay_ms":1000,
            "mature_due_ms":t+900000,
            "input_last_quote_available_at_ms":t-1500,
            "input_last_quote_valid_until_ms":t+3000,
            "mature_original_quote_event_ms":t+900500,
            "mature_original_quote_available_ms":t+900600,
            "mature_original_quote_id":hashlib.sha256(str(i).encode()).hexdigest(),
            "original_input_quote_ids":[hashlib.sha256((str(i)+str(j)).encode()).hexdigest()
                                        for j in range(13)],
            "features":[.1]*8,"price_only_midpoint_return_bps":("12.0","5.0","9.0")[i],
            "same_original_source_domain":True,
            "retrospectively_observed":True,
            "actual_executable_fill":False,"monetary_reward":False,
            "independent_prospective_prediction":False,
        })
    samples={
        "schema":DATA_SCHEMA,"examples":rows,
        "original_preseal_source_prefixes_sha256":"f"*64,
        "model_fit_authorized":False,"research_only":True,
        "retrospective_not_an_oos_test":True,
    }
    contract={
        "schema":CONTRACT_SCHEMA,
        "original_segment_prefixes_sha256":"f"*64,
        "native_mature_price_development_counts":{"mature_price_only_rows":3},
        "preseal_cutoff_ms":anchor+4*MIN_NONOVERLAP_MS,
        "required_nonoverlap_for_any_new_research_model_fit":1024,
        "research_model_fit_admission":False,
        "trade_authority":False,
        "model_promotion_allowed":False,
    }
    return samples,contract,model_fixture()

class CeilingTests(unittest.TestCase):
    def test_perfect_direction_ceiling_counts_only_price_not_fill(self):
        samples,contract,model=original_fixture()
        rows=original_rows(samples,contract,model)
        r=analyze(rows,"0.7147899","3.4837759",min_rows=1024,pinned_nonoverlap=3)
        all_=r["all_overlapping_original_research_price_examples"]
        self.assertEqual(all_["count"],3)
        self.assertEqual(all_["oracle_perfect_direction_price_move_clears_assumed_cost_count"],2)
        self.assertEqual(all_["oracle_perfect_direction_price_move_clears_assumed_cost_plus_margin_count"],1)
        self.assertEqual(r["frozen_cost_assumptions_bps"]["assumed_broker_cost_roundtrip_bps"],"8.9133557")
        self.assertFalse(r["oracle_is_predictable_or_executable"])
        self.assertFalse(r["model_fit_admission"])
    def test_nonoverlap_selects_anchors_not_labels(self):
        samples,contract,model=original_fixture()
        rows=original_rows(samples,contract,model)
        before=analyze(rows,"0.5","1.0",min_rows=1024,pinned_nonoverlap=3)
        for row in rows:
            row["price_move"]=-row["price_move"]*2
        after=analyze(rows,"0.5","1.0",min_rows=1024,pinned_nonoverlap=3)
        self.assertEqual(before["independent_window_population_80min_label_blind_greedy"]["count"],
                         after["independent_window_population_80min_label_blind_greedy"]["count"])
    def test_zero_cost_or_fee_refused(self):
        samples,contract,model=original_fixture()
        rows=original_rows(samples,contract,model)
        with self.assertRaisesRegex(CeilingError,"COST_NOT_POSITIVE"):
            analyze(rows,"0","1",min_rows=1024,pinned_nonoverlap=3)
    def test_nonoverlap_count_mismatch_rejected(self):
        samples,contract,model=original_fixture()
        rows=original_rows(samples,contract,model)
        with self.assertRaisesRegex(CeilingError,"NONOVERLAP_POPULATION_CHANGED"):
            analyze(rows,"0.5","1",min_rows=1024,pinned_nonoverlap=4)
    def test_fitting_not_allowed_even_with_many_rows(self):
        samples,contract,model=original_fixture()
        contract["research_model_fit_admission"]=True
        with self.assertRaisesRegex(CeilingError,"ORIGINAL_CONTRACT_NOT_HELD"):
            original_rows(samples,contract,model)
    def test_outcome_timestamp_before_maturity_refused(self):
        samples,contract,model=original_fixture()
        samples["examples"][0]["mature_original_quote_event_ms"]-=2000
        with self.assertRaisesRegex(CeilingError,"MATURE_PRICE_WAS_NOT_PRESEAL"):
            original_rows(samples,contract,model)
    def test_outcome_after_original_preseal_refused(self):
        samples,contract,model=original_fixture()
        samples["examples"][0]["mature_original_quote_available_ms"]=contract["preseal_cutoff_ms"]+1
        with self.assertRaisesRegex(CeilingError,"MATURE_PRICE_WAS_NOT_PRESEAL"):
            original_rows(samples,contract,model)
    def test_future_quote_as_feature_refused(self):
        samples,contract,model=original_fixture()
        samples["examples"][0]["input_last_quote_available_at_ms"]+=10000
        with self.assertRaisesRegex(CeilingError,"FROZEN_ORIGINAL_INPUT_QUOTE_EXPIRED"):
            original_rows(samples,contract,model)
    def test_expired_input_quote_refused(self):
        samples,contract,model=original_fixture()
        samples["examples"][0]["input_last_quote_valid_until_ms"]-=10000
        with self.assertRaisesRegex(CeilingError,"FROZEN_ORIGINAL_INPUT_QUOTE_EXPIRED"):
            original_rows(samples,contract,model)
    def test_conflicting_duplicate_anchor_refused(self):
        samples,contract,model=original_fixture()
        samples["examples"][1]["anchor_ms"]=samples["examples"][0]["anchor_ms"]
        with self.assertRaisesRegex(CeilingError,"FROZEN_ORIGINAL_PRICE_ANCHOR_CHANGED"):
            original_rows(samples,contract,model)
    def test_monetary_reward_disguised_as_label_refused(self):
        samples,contract,model=original_fixture()
        samples["examples"][0]["monetary_reward"]=True
        with self.assertRaisesRegex(CeilingError,"EXECUTION_OR_PROSPECTIVE_LABEL"):
            original_rows(samples,contract,model)
    def test_nonfinite_future_label_refused(self):
        samples,contract,model=original_fixture()
        samples["examples"][0]["price_only_midpoint_return_bps"]="NaN"
        with self.assertRaisesRegex(CeilingError,"DECIMAL_RANGE"):
            original_rows(samples,contract,model)
    def test_nonfinite_model_refused(self):
        m=model_fixture();m["weights"][2]=float("inf")
        with self.assertRaisesRegex(CeilingError,"NONFINITE_OR_ZERO_FROZEN_MODEL"):
            frozen_predict(m,[.1]*8)
    def test_model_bias_first_and_normalization(self):
        m=model_fixture()
        self.assertAlmostEqual(frozen_predict(m,[.1]*8),.108,places=12)
    def test_preexisting_output_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/"receipt.json"
            p.write_text("ORIGINAL")
            # Run fails before entering the writer on wrong source path.
            with self.assertRaises((CeilingError,FileNotFoundError)):
                run(examples_path=p,contract_path=p,model_path=p,cost_path=p,
                    campaign_seal_path=p,drift_path=p,output_path=p)
            self.assertEqual(p.read_text(),"ORIGINAL")
    def test_public_input_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/"public.json"
            p.write_text("{}")
            p.chmod(0o644)
            with self.assertRaisesRegex(CeilingError,"PRIVATE_INPUT_NOT_ATTESTED"):
                private_json(p)
    def test_duplicate_json_key_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/"file.json"
            p.write_text('{"a":1,"a":2}')
            p.chmod(0o600)
            with self.assertRaisesRegex(CeilingError,"DUPLICATE_INPUT_KEY"):
                private_json(p)
    def test_json_nan_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/"file.json"
            p.write_text('{"a":NaN}')
            p.chmod(0o600)
            with self.assertRaisesRegex(CeilingError,"NONFINITE_JSON"):
                private_json(p)

if __name__=="__main__":
    unittest.main()
