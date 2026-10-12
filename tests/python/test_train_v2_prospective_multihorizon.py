"""New-future original PAPER price-only cohort: adversarial preregistration."""
from __future__ import annotations

import copy
from pathlib import Path
import tempfile
import json
import unittest
from unittest.mock import patch

from train_v2.contracts import ContractError, digest
from train_v2.original_multihorizon import HORIZONS_MIN, GRID_MS
from train_v2.prospective_multihorizon import (
    POLICY, SLOT_COUNT, SLOT_SPACING_MS, LAST_CAPTURE_DELAY_MS,
    DECISION_DELAY_MS, PINNED_COST_SHA256, _next_anchor,
    preregister, capture_slot, campaign_status, _load,
)

BASE = 1792000000000


class FutureHorizonTests(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.root = Path(td.name)
        self.feed = self.root/"source"
        self.feed.mkdir(mode=0o700)
        self.segment = self.feed/"segment-1792000000000000000-ab12cd34"
        self.segment.mkdir(mode=0o700)
        self.file = self.segment/"quotes.jsonl"
        self.file.write_text("test-only-original-parser-fixture\n")
        self.file.chmod(0o600)
        self.period = self.root/"period.json"
        self.period.write_text(json.dumps({"execution_domain":"PAPER"}))
        self.period.chmod(0o600)
        self.cost = self.root/"cost.json"
        self.cost.write_text("{}\n")
        self.cost.chmod(0o600)
        self.config = self.root/"source-config.json"
        import hashlib
        self.config.write_text(json.dumps({
            "schema":"heptatrader.laya-resident-config.v1",
            "automatic_trading":False,
            "period_path":str(self.period),
            "period_sha256":hashlib.sha256(self.period.read_bytes()).hexdigest(),
            "feed_directory":str(self.feed)},sort_keys=True))
        self.config.chmod(0o600)
        self.campaign = self.root/"campaign"
        self.patch_cost=patch("train_v2.prospective_multihorizon._cost_file",
                              return_value=PINNED_COST_SHA256)
        self.patch_cost.start()
        self.addCleanup(self.patch_cost.stop)
        with patch("train_v2.prospective_multihorizon._clock",
                   return_value=BASE), \
             patch("train_v2.registry.time.time_ns",
                   return_value=BASE*1000000):
            self.plan=preregister(self.campaign,self.feed,self.period,self.cost,self.config)
        self.anchor=self.plan["anchors_ms"][0]
        self.before=self.anchor+DECISION_DELAY_MS+1300
        self.after=self.before+240

    def original(self, when=None):
        at=self.after if when is None else when
        rows=[]
        for i in range(6):
            event=at-3500+i*500
            rows.append({
                "schema":"heptatrader.train-v2-observation.v1",
                "instrument":"EUR.USD",
                "period_sha256":self.plan["period_sha256"],
                "source_kind":"LIVE_CAUSAL_QUOTE",
                "source_id":digest({"event":i}),
                "source_sha256":digest({"event":i}),
                "event_at_ms":event,
                "available_at_ms":event+120,
                "valid_until_ms":event+5000,
                "bid":str(1.102 + i*0.00001),
                "ask":str(1.1021 + i*0.00001),
            })
        proof={"verified_prefix_sha256":"b"*64,
               "original_chain_head":"c"*64}
        return rows,proof

    def clock(self):
        it=iter((self.before,self.after))
        return lambda:next(it)

    def capture(self, **kwargs):
        return capture_slot(self.campaign,0,clock=self.clock(),
                            original_reader=lambda *_:self.original(**kwargs))

    def test_research_plan_fixed_before_first_input_and_no_trade(self):
        self.assertEqual(self.plan["horizons_min"],list(HORIZONS_MIN))
        self.assertEqual(self.plan["policy_id"],POLICY)
        self.assertEqual(self.plan["slot_count"],SLOT_COUNT)
        self.assertEqual(len(self.plan["anchors_ms"]),SLOT_COUNT)
        self.assertEqual(
            self.plan["anchors_ms"][1]-self.anchor,SLOT_SPACING_MS)
        self.assertGreaterEqual(self.anchor-BASE,90000)
        self.assertEqual(self.anchor%GRID_MS,0)
        self.assertFalse(self.plan["actual_model_predictor"])
        self.assertFalse(self.plan["trade_authority"])
        self.assertFalse(self.plan["current_canonical_agentd_owner_enrolled"])
        self.assertFalse(self.plan["actual_slippage_attested"])

    def test_future_anchor_rounding(self):
        self.assertEqual(_next_anchor(BASE+123),((BASE+123+GRID_MS-1)//GRID_MS)*GRID_MS)
        with self.assertRaisesRegex(ContractError,"PROSPECTIVE_CLOCK_INVALID"):
            _next_anchor(True)

    def test_local_preregistration_is_private_and_immutable(self):
        self.assertEqual(_load(self.campaign),self.plan)
        self.assertTrue((self.campaign/"registry"/"sealed-plans"/
                        (self.plan["local_plan_id"]+".json")).is_file())
        with patch("train_v2.prospective_multihorizon._clock",
                   return_value=BASE), \
             self.assertRaisesRegex(ContractError,"PROSPECTIVE_FRESH_ROOT_REQUIRED"):
            preregister(self.campaign,self.feed,self.period,self.cost,self.config)

    def test_asof_slot_first_new_source_is_sealed_without_label(self):
        receipt=self.capture()
        self.assertEqual(receipt["slot"],0)
        self.assertEqual(receipt["decision_at_ms"],self.after)
        self.assertTrue(receipt["all_future_labels_unobserved"])
        self.assertFalse(receipt["forecast_model_run"])
        self.assertEqual(receipt["selected_original_trading_action"],None)
        self.assertFalse(receipt["native_ledgerwriter_ack"])
        self.assertEqual(receipt["broker_orders_sent"],0)
        self.assertEqual(
            list(receipt["outcome_maturity_ms"]),
            [str(x) for x in HORIZONS_MIN])
        self.assertEqual(campaign_status(self.campaign,at_ms=self.after)["slots"][0]["state"],
                         "PREOUTCOME_ORIGINAL_INPUT_SEALED")
        with self.assertRaisesRegex(ContractError,"PROSPECTIVE_SLOT_ALREADY_SEALED"):
            self.capture()

    def test_missing_capture_not_imputed_as_no_quote(self):
        report=campaign_status(
            self.campaign,at_ms=self.anchor+LAST_CAPTURE_DELAY_MS+1)
        self.assertEqual(report["slots"][0]["state"],
                         "MISSED_NO_OPERATOR_CAPTURE_NOT_NO_QUOTE")
        self.assertFalse(report["independent_efficacy_sample"])
        self.assertTrue(report["missing_inputs_not_assumed_zero"])

    def test_future_slot_not_due(self):
        report=campaign_status(self.campaign,at_ms=self.anchor)
        self.assertEqual(report["slots"][0]["state"],"FUTURE_SLOT_NOT_DUE")
        self.assertEqual(report["slots"][1]["state"],"FUTURE_SLOT_NOT_DUE")

    def test_late_or_early_capture_refused_even_if_source_exists(self):
        for before in (self.anchor,self.anchor+LAST_CAPTURE_DELAY_MS+1):
            with self.subTest(before=before), \
                 self.assertRaisesRegex(ContractError,"PROSPECTIVE_INPUT_NOT_IN_FROZEN_WINDOW"):
                capture_slot(self.campaign,0,clock=lambda:before,
                             original_reader=lambda *_:self.original())

    def test_expired_latest_quote_not_replaced_by_older_good_quote(self):
        quote,proof=self.original()
        quote[-1]["valid_until_ms"]=self.after-1
        with self.assertRaisesRegex(ContractError,"FEATURE_QUOTE_EXPIRED"):
            capture_slot(self.campaign,0,clock=self.clock(),
                         original_reader=lambda *_:(quote,proof))

    def test_latest_quote_must_arrive_after_preregistration(self):
        quotes,proof=self.original()
        for i,row in enumerate(quotes):
            row["available_at_ms"]=BASE-5000+i*500
            row["event_at_ms"]=row["available_at_ms"]-100
            row["valid_until_ms"]=self.after+2000
        with self.assertRaisesRegex(
            ContractError,"PROSPECTIVE_NO_POSTREG_INPUT_SOURCE"):
            capture_slot(self.campaign,0,clock=self.clock(),
                         original_reader=lambda *_:(quotes,proof))

    def test_input_capture_cannot_be_reported_after_window(self):
        ticks=iter((self.before,self.anchor+LAST_CAPTURE_DELAY_MS+1))
        with self.assertRaisesRegex(ContractError,"PROSPECTIVE_INPUT_CAPTURE_LATE"):
            capture_slot(self.campaign,0,clock=lambda:next(ticks),
                         original_reader=lambda *_:self.original())

    def test_incorrect_code_hash_is_rejected_even_with_local_rehash(self):
        path=self.campaign/"campaign.json"
        plan=json.loads(path.read_text())
        plan["source_code_sha256"]["prospective_multihorizon.py"]="d"*64
        plan["campaign_id"]=digest({k:v for k,v in plan.items()
                                    if k!="campaign_id"})
        path.write_text(json.dumps(plan)+"\n")
        with self.assertRaisesRegex(ContractError,"PROSPECTIVE_MANIFEST_OR_CODE_CHANGED"):
            _load(self.campaign)

    def test_period_mismatch_rejected(self):
        self.period.write_text('{"execution_domain":"LIVE"}')
        with self.assertRaisesRegex(ContractError,"PROSPECTIVE_ACCOUNT_PERIOD_CHANGED"):
            campaign_status(self.campaign)

    def test_unknown_future_slot_not_retrospectively_repaired(self):
        self.assertEqual(campaign_status(self.campaign,at_ms=
              self.plan["anchors_ms"][1]+LAST_CAPTURE_DELAY_MS+1)["slots"][1]["state"],
             "MISSED_NO_OPERATOR_CAPTURE_NOT_NO_QUOTE")
        with self.assertRaisesRegex(ContractError,"PROSPECTIVE_INPUT_NOT_IN_FROZEN_WINDOW"):
            capture_slot(self.campaign,1,clock=lambda:self.plan["anchors_ms"][1]+LAST_CAPTURE_DELAY_MS+1,
                         original_reader=lambda *_:self.original())

    def test_zero_automatic_actions(self):
        self.assertEqual(self.plan["broker_orders_sent"],0)
        self.assertFalse(self.plan["automatic_model_promotion"])
        self.assertEqual(_load(self.campaign)["selection"],
                         "NO_TRADE_ONLY_PRICE_OBSERVATION")
