"""Fail-closed adversarial tests for prospective ORIGINAL IB PAPER price-only study."""
from __future__ import annotations

from decimal import Decimal
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from train_v2.fx_original_forward_price import (
    COUNT, DECISION_START_MS, HORIZONS_MINUTES,HORIZONS_MS, Hold,
    STEP_MS, frozen_anchors, attempt_record, mature_horizon, prepare_entry,
    readiness_observation, write_record, private_root, now_ms, SCHEMA,
)

ANCHOR=1791822600000
def base():
    p={"period_sha256":"f"*64}
    w={"index":0,"anchor_ms":ANCHOR,"entry_first_ms":ANCHOR+1000,
       "entry_last_ms":ANCHOR+4500,
       "mature_at_ms":[ANCHOR+h for h in HORIZONS_MS]}
    cost={"quote_spread_full_bps":{"p99":3.4837759},
          "fx_commission_one_way_bps":{"p90":0.7147899}}
    return p,w,cost

def sample(t=ANCHOR):
    def q(k):
        return {"source_id":str(k)*64,"bid":"1.10000","ask":"1.10002",
                "event_at_ms":t-(12-k)*300000-800,
                "available_at_ms":t-(12-k)*300000-700,
                "valid_until_ms":t-(12-k)*300000+4000}
    return [q(i) for i in range(13)]

def create():
    d=tempfile.TemporaryDirectory()
    root=Path(d.name)/"state";root.mkdir(mode=0o700)
    (root/"window-00").mkdir(mode=0o700)
    return d,root

def success(root, *,at=ANCHOR+1000,latest=None,health=None):
    p,w,cost=base()
    rows=sample()
    if latest:rows[-1].update(latest)
    with patch("train_v2.fx_original_forward_price.now_ms",return_value=at+250):
        return prepare_entry(root,p,w,{},cost,{},at_ms=at,
                readiness_fn=health or (lambda p,t:{"state":"READY_FOR_REVIEW_NOT_AUTHORIZATION"}),
                source_fn=lambda c,s,t:(rows,[{"prefix_sha256":"a"*64}]),
                features_fn=lambda rs,anchor:(rs,[.0]*8))

def future_row(minutes=15,price="1.10050",late=False):
    due=ANCHOR+minutes*60000
    return {"source_id":"b"*64, "bid":price,"ask":str(Decimal(price)+Decimal("0.00002")),
            "event_at_ms":due+200,
            "available_at_ms":due+(5200 if late else 600),
            "valid_until_ms":due+5000}

class ProtocolTests(unittest.TestCase):
    def test_horizons_unchanged(self):
        self.assertEqual(HORIZONS_MINUTES,(15,30,60,120))
        self.assertEqual(HORIZONS_MS,(900000,1800000,3600000,7200000))
    def test_fixed_nonoverlap_four_anchors(self):
        t=1791820324567
        anchors=frozen_anchors(t)
        self.assertEqual(len(anchors),COUNT)
        self.assertTrue(all(a%300000==0 for a in anchors))
        self.assertTrue(all(b-a==STEP_MS for a,b in zip(anchors,anchors[1:])))
        self.assertGreaterEqual(anchors[0]-t,20*60000)
    def test_clock_type_refuses_bool(self):
        with self.assertRaisesRegex(Hold,"REGISTRATION_CLOCK_INVALID"):
            frozen_anchors(True)
    def test_private_outcome_write_once(self):
        d,root=create()
        with d:
            path=root/"sealed.json"
            write_record(path,{"scope":"NO_TRADE"})
            with self.assertRaises((FileExistsError,Hold)):
                write_record(path,{"scope":"TRADE"})
            self.assertEqual(json.loads(path.read_text())["scope"],"NO_TRADE")
    def test_public_evidence_root_rejected(self):
        d,root=create()
        with d:
            root.chmod(0o755)
            with self.assertRaisesRegex(Hold,"RESEARCH_DIRECTORY_NOT_PRIVATE"):
                private_root(root)
    def test_health_hold_state_not_ignored(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/"health.json"
            path.write_text(json.dumps({"state":"HOLD_NEW_FUSION_WINDOW",
                "counts":{"USABLE":16,"PATH_GAP":8},"at_ms":ANCHOR,
                "automatic_trial_registration":False}))
            plan={"readiness_file":str(path)}
            with self.assertRaisesRegex(Hold,"ORIGINAL_READINESS_HOLD_OR_STALE"):
                readiness_observation(plan,ANCHOR+1000)
    def test_health_positive_is_still_not_trading_authority(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/"health.json"
            path.write_text(json.dumps({"state":"READY_FOR_REVIEW_NOT_AUTHORIZATION",
                "counts":{"USABLE":20,"PATH_GAP":4},"at_ms":ANCHOR,
                "automatic_trial_registration":False}))
            out=readiness_observation({"readiness_file":str(path)},ANCHOR+1000)
            self.assertEqual(out["usable_probes"],20)
    def test_expired_health_refused(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/"health.json"
            path.write_text(json.dumps({"state":"READY_FOR_REVIEW_NOT_AUTHORIZATION",
                "counts":{"USABLE":24},"at_ms":ANCHOR,
                "automatic_trial_registration":False}))
            with self.assertRaisesRegex(Hold,"ORIGINAL_READINESS_HOLD_OR_STALE"):
                readiness_observation({"readiness_file":str(path)},ANCHOR+31000)
    def test_missed_preaction_is_permanently_censored(self):
        d,root=create()
        with d:
            p,w,cost=base()
            fn=Mock()
            r=prepare_entry(root,p,w,{},cost,{},at_ms=ANCHOR+4600,
                    readiness_fn=fn)
            self.assertEqual(r["state"],"CENSORED")
            fn.assert_not_called()
            self.assertFalse((root/"window-00"/"preaction.json").exists())
    def test_entry_fails_closed_on_hold_not_order(self):
        d,root=create()
        with d:
            fn=Mock(side_effect=Hold("ORIGINAL_READINESS_HOLD_OR_STALE"))
            r=success(root,health=fn)
            self.assertEqual(r["state"],"CENSORED")
            self.assertIn("ORIGINAL_READINESS_HOLD",r["reason_code"])
            self.assertFalse(r["trade_authority"])
            self.assertEqual(r["broker_orders_sent"],0)
    def test_entry_fails_closed_if_source_stale(self):
        d,root=create()
        with d:
            r=success(root,latest={"available_at_ms":ANCHOR-4500})
            self.assertEqual(r["state"],"CENSORED")
            self.assertIn("ORIGINAL_QUOTE_AGE_EXPIRY",r["reason_code"])
    def test_entry_fails_closed_if_quote_expired(self):
        d,root=create()
        with d:
            r=success(root,latest={"valid_until_ms":ANCHOR+900})
            self.assertEqual(r["state"],"CENSORED")
    def test_success_is_only_price_no_prediction(self):
        d,root=create()
        with d:
            r=success(root)
            self.assertEqual(r["state"],"FROZEN_ORIGINAL_PRICE_ONLY")
            self.assertEqual(r["broker_orders_sent"],0)
            frozen=json.loads((root/"window-00"/"preaction.json").read_text())
            self.assertTrue(frozen["no_model_prediction_or_propensity_derived"])
            self.assertEqual(frozen["intended_behavior"],"NO_TRADE_PRICE_ONLY")
            self.assertEqual(len(frozen["input_source_ids"]),13)
            self.assertEqual(frozen["cost_stress"]["required_absolute_prediction_bps"],10.9133557)
    def test_entry_never_retry(self):
        d,root=create()
        with d:
            success(root)
            p,w,cost=base()
            with self.assertRaisesRegex(Hold,"DECISION_ALREADY_ATTEMPTED"):
                prepare_entry(root,p,w,{},cost,{})
    def test_maturity_before_declared_time_is_censored(self):
        d,root=create()
        with d:
            p,w,cost=base()
            r=mature_horizon(root,p,w,15,{}, {},at_ms=ANCHOR+900000)
            self.assertEqual(r["state"],"CENSORED")
            self.assertEqual(r["reason_code"],"OUTSIDE_FROZEN_MATURITY_ATTEMPT_WINDOW")
    def test_no_original_entry_never_matures(self):
        d,root=create()
        with d:
            p,w,cost=base()
            r=mature_horizon(root,p,w,15,{},{},at_ms=ANCHOR+900000+6500)
            self.assertEqual(r["state"],"CENSORED")
            self.assertEqual(r["reason_code"],"ORIGINAL_ENTRY_NOT_FROZEN")
    def test_frozen_censored_entry_cannot_generate_price_reward(self):
        d,root=create()
        with d:
            p,w,cost=base()
            attempt_record(root,root/"window-00","decision","CENSORED","SOURCE_HOLD")
            r=mature_horizon(root,p,w,15,{},{},at_ms=ANCHOR+900000+6500)
            self.assertEqual(r["reason_code"],"NO_ELIGIBLE_ORIGINAL_PREACTION")
    def test_mature_original_quote_not_an_order(self):
        d,root=create()
        with d:
            success(root)
            p,w,cost=base()
            quote=future_row()
            r=mature_horizon(root,p,w,15,{},{},
                  at_ms=ANCHOR+900000+6500,
                  source_fn=lambda c,s,t:([quote],[{"prefix_sha256":"b"*64}]),
                  prefix_verify_fn=lambda receipts:None)
            self.assertEqual(r["state"],"MATURE_ORIGINAL_QUOTE_PRICE_ONLY")
            row=json.loads((root/"window-00"/"maturity-015.json").read_text())
            self.assertTrue(row["quote_side_prices_are_NOT_guaranteed_fills"])
            self.assertEqual(row["selected_trading_action"],"NO_TRADE")
            self.assertEqual(row["actual_broker_execution_pnl_samples"],0)
            self.assertLess(Decimal(row["hypothetical_long_entry_ask_exit_bid_move_bps"]),
                            Decimal(row["midpoint_move_bps"]))
    def test_late_first_original_quote_censored(self):
        d,root=create()
        with d:
            success(root)
            p,w,cost=base()
            r=mature_horizon(root,p,w,15,{},{},
                  at_ms=ANCHOR+900000+6500,
                  source_fn=lambda c,s,t:([future_row(late=True)],[]),
                  prefix_verify_fn=lambda receipts:None)
            self.assertEqual(r["state"],"CENSORED")
            self.assertIn("FIRST_FUTURE_QUOTE_LATE",r["reason_code"])
            self.assertFalse((root/"window-00"/"maturity-015.json").exists())
    def test_changed_input_prefix_invalidates_future(self):
        d,root=create()
        with d:
            success(root)
            p,w,cost=base()
            r=mature_horizon(root,p,w,15,{},{},
                  at_ms=ANCHOR+900000+6500,
                  prefix_verify_fn=Mock(side_effect=ValueError("SOURCE_CHANGED")))
            self.assertEqual(r["state"],"CENSORED")
            self.assertIn("SOURCE_CHANGED",r["reason_code"])
    def test_tampered_original_price_refused(self):
        d,root=create()
        with d:
            success(root)
            path=root/"window-00"/"preaction.json"
            content=json.loads(path.read_text())
            content["input_quote_ask"]="1.50000"
            path.write_text(json.dumps(content))
            p,w,cost=base()
            r=mature_horizon(root,p,w,15,{},{},
                  at_ms=ANCHOR+900000+6500)
            self.assertEqual(r["state"],"CENSORED")
            self.assertIn("FROZEN_ORIGINAL_PREACTION_CHANGED",r["reason_code"])
    def test_attempts_are_once_per_horizon(self):
        d,root=create()
        with d:
            p,w,cost=base()
            mature_horizon(root,p,w,120,{},{},at_ms=ANCHOR+7200000+6500)
            with self.assertRaisesRegex(Hold,"MATURITY_ALREADY_ATTEMPTED"):
                mature_horizon(root,p,w,120,{},{},at_ms=ANCHOR+7200000+6500)
    def test_unlisted_horizon_rejected(self):
        d,root=create()
        with d:
            p,w,cost=base()
            with self.assertRaisesRegex(Hold,"HORIZON_NOT_FROZEN"):
                mature_horizon(root,p,w,90,{},{},at_ms=ANCHOR+5400000+6500)

    def test_train_v2_120min_spec_remains_closed(self):
        # The new 120-minute price-only study MUST NOT silently widen
        # the product Train V2 <=3600000ms native FeatureSpec contract.
        from train_v2.adapters import make_spec
        from train_v2.contracts import ContractError
        with self.assertRaises(ContractError):
            make_spec("f"*64,"FX_FUTURE_PRICE_ONLY_RESEARCH",
                      horizon_ms=7200000,lookback=13,
                      broker_reported_expiry=True)

if __name__=="__main__":unittest.main()
