"""Finite one-shot systemd-invoked research slot runner: adversarial tests."""
from __future__ import annotations

import copy
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from train_v2.contracts import ContractError, digest
from train_v2.scheduled_horizon import (
    SCHEMA, RUN_SCHEMA, DELAY_MS, MIN_SETUP_MS,
    create_roster, load_roster, run_slot, _now,
)
from train_v2.prospective_multihorizon import LAST_CAPTURE_DELAY_MS
import test_train_v2_prospective_multihorizon as fixture_module
BASE = fixture_module.BASE


class FiniteScheduledCaptureTests(unittest.TestCase):
    def setUp(self):
        original=fixture_module.FutureHorizonTests(
            "test_research_plan_fixed_before_first_input_and_no_trade")
        original.setUp()
        self.addCleanup(original.doCleanups)
        self.fx=original
        self.root=original.campaign
        self.file=self.root/"timers-roster.json"
        self.start=BASE

    def frozen_roster(self):
        with patch("train_v2.scheduled_horizon._now",return_value=self.start), \
             patch("train_v2.scheduled_horizon.campaign_status",
                   return_value=self.fx.campaign_status(
                       self.root, at_ms=self.start) if hasattr(self.fx,"campaign_status")
                       else {"slots":[{"state":"FUTURE_SLOT_NOT_DUE"}]*3}):
            return create_roster(self.root,self.file)

    def test_roster_binds_frozen_future_three_slots(self):
        roster=self.frozen_roster()
        self.assertEqual(roster["schema"],SCHEMA)
        self.assertEqual(len(roster["slots"]),3)
        self.assertEqual(roster["slots"][0]["launch_at_ms"],
                         self.fx.anchor+DELAY_MS)
        self.assertEqual(roster["slots"][0]["last_original_input_ms"],
                         self.fx.anchor+LAST_CAPTURE_DELAY_MS)
        self.assertEqual(load_roster(self.file),roster)
        self.assertFalse(roster["automatic_trading"])
        self.assertFalse(roster["model_inference"])

    def test_same_schedule_never_replaces_original_bytes(self):
        self.frozen_roster()
        before=self.file.read_bytes()
        with self.assertRaisesRegex(ContractError,"SCHEDULE_ALREADY_PRESENT"):
            self.frozen_roster()
        self.assertEqual(before,self.file.read_bytes())

    def test_early_late_and_wrong_slot_never_capture(self):
        roster=self.frozen_roster()
        for tick,error in (
            (self.fx.anchor-10000,"SCHEDULE_TIMER_EARLY"),
            (self.fx.anchor+LAST_CAPTURE_DELAY_MS+1,"SCHEDULE_TIMER_LATE")):
            with self.subTest(tick=tick),patch(
                    "train_v2.scheduled_horizon._now",return_value=tick),\
                    self.assertRaisesRegex(ContractError,error):
                run_slot(self.file,0)
        with self.assertRaisesRegex(ContractError,"SCHEDULE_SLOT_OUT_OF_BOUNDS"):
            run_slot(self.file,-1)
        self.assertFalse((self.root/"slot-00.input.json").exists())

    def test_finite_success_only_preoutcome_and_duplicate_fenced(self):
        self.frozen_roster()
        launch=self.fx.anchor+DELAY_MS
        def fake_capture(root,slot):
            self.assertEqual(slot,0)
            path=Path(root)/"slot-00.input.json"
            path.write_text('{"sealed_fixture":true}\n')
            path.chmod(0o600)
            return {"all_future_labels_unobserved":True,
                    "forecast_model_run":False,"broker_orders_sent":0}
        with patch("train_v2.scheduled_horizon._now",
                   side_effect=[launch,launch+44]),\
             patch("train_v2.scheduled_horizon.capture_slot",
                   side_effect=fake_capture) as cap:
            result=run_slot(self.file,0)
        cap.assert_called_once_with(self.root,0)
        self.assertEqual(result["state"],"SOURCE_INPUT_SEALED_NO_LABEL")
        self.assertFalse(result["original_label_assigned"])
        self.assertEqual(result["broker_orders_sent"],0)
        self.assertIsNotNone(result["source_input_sha256"])
        sealed=json.loads((self.root/"slot-00.timer-result.json").read_text())
        self.assertEqual(sealed["result_id"],digest({
            k:v for k,v in sealed.items() if k!="result_id"}))
        with patch("train_v2.scheduled_horizon._now",
                   return_value=launch+100),self.assertRaisesRegex(
                       ContractError,"SCHEDULE_RESULT_ALREADY_SEALED"):
            run_slot(self.file,0)

    def test_capture_failure_sealed_not_false_market_or_price_label(self):
        self.frozen_roster()
        launch=self.fx.anchor+DELAY_MS
        with patch("train_v2.scheduled_horizon._now",
                   side_effect=[launch,launch+90]),\
             patch("train_v2.scheduled_horizon.capture_slot",
                   side_effect=ContractError("FEATURE_QUOTE_EXPIRED")):
            result=run_slot(self.file,0)
        self.assertEqual(result["state"],"FAILED_RESEARCH_ONLY")
        self.assertEqual(result["reason"],"ContractError:FEATURE_QUOTE_EXPIRED")
        self.assertIsNone(result["source_input_sha256"])
        self.assertFalse(result["original_label_assigned"])
        self.assertFalse((self.root/"slot-00.input.json").exists())

    def test_forged_roster_even_rehashed_is_denied(self):
        self.frozen_roster()
        v=json.loads(self.file.read_text())
        v["slots"][0]["launch_at_ms"]+=1500
        v["roster_id"]=digest({k:x for k,x in v.items() if k!="roster_id"})
        self.file.write_text(json.dumps(v,sort_keys=True)+"\n")
        with self.assertRaisesRegex(ContractError,"SCHEDULE_SLOT_ROSTER_CHANGED"):
            load_roster(self.file)

    def test_code_hash_drift_denied(self):
        self.frozen_roster()
        with patch("train_v2.scheduled_horizon._source_sha",
                   return_value="d"*64),self.assertRaisesRegex(
                       ContractError,"SCHEDULE_IMMUTABLE_ROSTER_CHANGED"):
            load_roster(self.file)

    def test_old_or_missed_campaign_refused_to_schedule(self):
        with patch("train_v2.scheduled_horizon._now",return_value=BASE),\
             patch("train_v2.scheduled_horizon.campaign_status",
                   return_value={"slots":[
                       {"state":"MISSED_NO_OPERATOR_CAPTURE_NOT_NO_QUOTE"},
                       {"state":"FUTURE_SLOT_NOT_DUE"},
                       {"state":"FUTURE_SLOT_NOT_DUE"}]}),\
             self.assertRaisesRegex(
                 ContractError,"SCHEDULE_OLD_OR_MISSED_SLOT_CANNOT_BE_RESTARTED"):
            create_roster(self.root,self.file)

    def test_insufficient_time_before_first_slot_is_refused(self):
        with patch("train_v2.scheduled_horizon._now",
                   return_value=self.fx.anchor+DELAY_MS-MIN_SETUP_MS+1),\
             patch("train_v2.scheduled_horizon.campaign_status",
                   return_value={"slots":[{"state":"FUTURE_SLOT_NOT_DUE"}]*3}),\
             self.assertRaisesRegex(
                 ContractError,"SCHEDULE_NOT_FROZEN_EARLY_ENOUGH"):
            create_roster(self.root,self.file)

    def test_schedule_symlink_rejected_without_original_mutation(self):
        with tempfile.TemporaryDirectory() as td:
            target=Path(td)/"untrusted.json"
            target.write_text("{}")
            self.file.symlink_to(target)
            with self.assertRaises(ContractError):
                self.frozen_roster()
            self.assertEqual(target.read_text(),"{}")
