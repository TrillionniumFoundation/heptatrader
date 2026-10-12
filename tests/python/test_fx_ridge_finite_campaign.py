"""Research-only finite Ridge campaign: no-backfill and schedule guard tests."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from train_v2.fx_ridge_finite_campaign import (
    HORIZON_MS, INTERVAL_MS, MATURE_WAIT_MS, N_WINDOWS,
    ResearchHold, grid, note_once, perform_event
)

def scenario(temp: Path):
    root = temp / "campaign"
    root.mkdir(mode=0o700)
    cell = root / "window-00"
    cell.mkdir(mode=0o700)
    anchor = 900000000000
    x = {
        "index": 0, "anchor_ms": anchor,
        "first_decision_ms": anchor + 1000,
        "last_decision_ms": anchor + 4500,
        "outcome_due_ms": anchor + HORIZON_MS,
        "plan_id": "a" * 64,
    }
    plan = {"config_path":str(temp/"config"),"cost_path":str(temp/"cost")}
    return root, cell, plan, x

class CampaignScheduleTests(unittest.TestCase):
    def test_exactly_four_nonoverlapping_windows(self):
        anchors = grid(1791797000000)
        self.assertEqual(len(anchors), N_WINDOWS)
        self.assertEqual(len(set(anchors)), N_WINDOWS)
        self.assertTrue(all(a % 300000 == 0 for a in anchors))
        self.assertTrue(all(b-a == INTERVAL_MS for a,b in zip(anchors,anchors[1:])))
        self.assertTrue(all(b-a > HORIZON_MS + MATURE_WAIT_MS
                            for a,b in zip(anchors,anchors[1:])))
    def test_future_only_and_no_backdating(self):
        now = 1791797000000
        self.assertGreater(grid(now)[0], now + 600000 - 300001)
    def test_clock_bool_is_not_valid_time(self):
        with self.assertRaisesRegex(ResearchHold,"CAMPAIGN_CLOCK_INVALID"):grid(True)
    def test_decision_missed_is_censored_without_callback(self):
        with tempfile.TemporaryDirectory() as d:
            root, cell, intent, x = scenario(Path(d))
            mock=Mock()
            r=perform_event(root,intent,x,"decision",at=x["last_decision_ms"]+1,
                            decision_fn=mock)
            self.assertEqual(r["state"],"CENSORED")
            self.assertEqual(r["reason_code"],"MISSED_FIXED_DECISION_WINDOW")
            mock.assert_not_called()
            self.assertFalse(r["trade_authority"])
    def test_never_retry_censored_window(self):
        with tempfile.TemporaryDirectory() as d:
            root, cell, intent, x = scenario(Path(d))
            perform_event(root,intent,x,"decision",at=x["last_decision_ms"]+1)
            with self.assertRaisesRegex(ResearchHold,"EVENT_ALREADY_ATTEMPTED"):
                perform_event(root,intent,x,"decision",at=x["first_decision_ms"])
    def test_uncertified_future_maturity_is_not_backfilled(self):
        with tempfile.TemporaryDirectory() as d:
            root, cell, intent, x = scenario(Path(d))
            mock=Mock()
            r=perform_event(root,intent,x,"maturity",
                            at=x["outcome_due_ms"]+MATURE_WAIT_MS,
                            maturity_fn=mock)
            self.assertEqual(r["state"],"CENSORED")
            self.assertEqual(r["reason_code"],
                             "NO_PREMATURE_VALID_FROZEN_DECISION")
            mock.assert_not_called()
    def test_early_future_outcome_is_censored(self):
        with tempfile.TemporaryDirectory() as d:
            root, cell, intent, x = scenario(Path(d))
            r=perform_event(root,intent,x,"maturity",at=x["outcome_due_ms"])
            self.assertEqual(r["reason_code"],"OUTCOME_TOO_EARLY_NO_BACKFILL")
    def test_tampered_predecision_status_not_admitted(self):
        with tempfile.TemporaryDirectory() as d:
            root, cell, intent, x = scenario(Path(d))
            p=cell/"qualification.json"
            p.write_text(json.dumps({"state":"HOLD"}))
            p.chmod(0o600)
            m=Mock()
            r=perform_event(root,intent,x,"maturity",
                            at=x["outcome_due_ms"]+MATURE_WAIT_MS,maturity_fn=m)
            self.assertEqual(r["reason_code"],"PREDECISION_NOT_QUALIFIED")
            m.assert_not_called()
    def test_failed_original_decision_refused_and_retained(self):
        with tempfile.TemporaryDirectory() as d:
            root, cell, intent, x = scenario(Path(d))
            m=Mock(side_effect=ResearchHold("MISSING_CAUSAL_ORIGINAL_5MIN_CLOSE"))
            r=perform_event(root,intent,x,"decision",
                            at=x["first_decision_ms"],decision_fn=m)
            self.assertEqual(r["state"],"CENSORED")
            self.assertIn("MISSING_CAUSAL",r["reason_code"])
            self.assertTrue((cell/"decision-attempt.json").exists())
            self.assertFalse(r["model_promotion_allowed"])
    def test_positive_price_shadow_not_a_trade(self):
        with tempfile.TemporaryDirectory() as d:
            root, cell, intent, x = scenario(Path(d))
            success=Mock(return_value={"state":"PRICE_ONLY_PREDICTION_FROZEN_NO_TRADE",
                                        "model_promotion_allowed":False})
            r=perform_event(root,intent,x,"decision",
                            at=x["first_decision_ms"],decision_fn=success)
            self.assertEqual(r["state"],"FROZEN_PRICE_ONLY")
            self.assertFalse(r["trade_authority"])
            self.assertEqual(r["broker_orders_sent"],0)
    def test_unqualified_candidate_rejected_even_if_callback_returns(self):
        with tempfile.TemporaryDirectory() as d:
            root, cell, intent, x = scenario(Path(d))
            bad=Mock(return_value={"state":"TRADE","model_promotion_allowed":True})
            r=perform_event(root,intent,x,"decision",
                            at=x["first_decision_ms"],decision_fn=bad)
            self.assertEqual(r["state"],"CENSORED")
            self.assertIn("DECISION_NOT_IN_FROZEN_NO_TRADE",r["reason_code"])
    def test_maturity_cannot_claim_native_efficacy(self):
        with tempfile.TemporaryDirectory() as d:
            root, cell, intent, x = scenario(Path(d))
            p=cell/"qualification.json"
            p.write_text(json.dumps({"state":"PRICE_ONLY_PREDICTION_FROZEN_NO_TRADE"}))
            p.chmod(0o600)
            ok=Mock(return_value={
                "state":"PRICE_ONLY_REAL_ORIGINAL_OUTCOME_MATURED_NOT_MODEL_EFFICACY"})
            r=perform_event(root,intent,x,"maturity",
                            at=x["outcome_due_ms"]+MATURE_WAIT_MS,maturity_fn=ok)
            self.assertEqual(r["state"],"MATURE_PRICE_ONLY")
            self.assertFalse(r["model_promotion_allowed"])

if __name__ == "__main__":
    unittest.main()
