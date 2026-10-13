"""Two truly different preregistered Numeric tick PRICE decisions; no trade."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from historical_training.common import canonical
from historical_training import native_price_ticks_two_window as cohort
from historical_training.native_score_ticks import PROFILE
from laya_cell.integrity import ContractError

START = 1900000000000


class TwoNativeTickWindows(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.config = self.root / "config.json"
        self.config.write_text("{}")
        self.slots = []
        self.documents = {}
        for n, shift in enumerate((0, 360000), start=1):
            r = self.root / str(n)
            r.mkdir(mode=0o700)
            code = "a" if n == 1 else "b"
            plan = {
                "plan_id": code*64,
                "registered_at_ms": START + shift - 120000,
                "slot_start_ms": START+shift,
                "window_end_ms": START+shift+16000,
                "forecast_profile": PROFILE,
                "target_horizon_sec": 300,
                "baseline_model_id": "m"*64,
                "baseline_model_sha256": "d"*64,
                "original_native_binary_sha256": "e"*64,
                "no_trade_policy": "NO_ACTION_NO_BROKER_ORDER",
            }
            inp = {
                "plan_id": plan["plan_id"],
                "quote_receipt": code*64,
                "quote_event_at_ms": START+shift+1000,
                "quote_known_at_ms": START+shift+1100,
                "source_asof_ms": START+shift+1300,
                "feature_decision_ms": START+shift+1400,
                "sample_id": code*64,
                "broker_orders_sent": 0,
            }
            score = {"scaled_bps_ticks": 73047167+n,
                     "score_id": code*64}
            pred = {
                "plan_id": plan["plan_id"],
                "anchor_ms": inp["quote_event_at_ms"],
                "source_decision_at_ms": inp["feature_decision_ms"],
                "prediction_ready_at_ms": START+shift+1500,
                "future_fixed_score": score,
                "broker_orders_sent": 0,
            }
            ack = {
                "plan_id": plan["plan_id"],
                "post_fsync_at_ms": START+shift+1600,
                "future_fixed_score_profile": PROFILE,
                "broker_orders_sent": 0,
            }
            for name, obj in (("plan",plan),("input",inp),
                              ("prediction",pred),("commit",ack)):
                (r/(name+".json")).write_bytes(canonical(obj)+b"\n")
            # Refresh immutable linked receipt values after writing input and
            # prediction; production verifier checks all hashes independently.
            from historical_training.common import sha
            pred["source_input_sha256"] = sha(r/"input.json")
            (r/"prediction.json").write_bytes(canonical(pred)+b"\n")
            ack["input_sha256"] = sha(r/"input.json")
            ack["prediction_sha256"] = sha(r/"prediction.json")
            (r/"commit.json").write_bytes(canonical(ack)+b"\n")
            self.slots.append(r)
            self.documents[str(r)] = {"plan":plan,"input":inp,
                                     "prediction":pred,"commit":ack}

    def run_audit(self):
        def plan(root):
            return self.documents[str(root)]["plan"]
        def original_verify(root, config):
            return {
                "new_profile_score_reproduced": True,
                "original_source_rechecked": True,
                "original_f64_bitwise_equal_attested": False,
                "broker_orders_sent": 0}
        with patch.object(cohort, "_plan", side_effect=plan), \
             patch.object(cohort, "verify", side_effect=original_verify):
            return cohort.two_original_windows(
                self.slots[0], self.slots[1], self.config)

    def test_two_source_horizons_disjoint_no_trade_or_alpha(self):
        out = self.run_audit()
        self.assertEqual(out["native_future_tick_score_replays"],2)
        self.assertTrue(out["source_price_horizons_disjoint"])
        self.assertTrue(out["both_forecast_magnitudes_below_hypothetical_cost"])
        self.assertFalse(out["after_cost_alpha_qualified"])
        self.assertEqual(out["actual_broker_execution_net_pnl_samples"],0)
        self.assertEqual(out["broker_orders_sent"],0)

    def test_same_original_source_quote_denied(self):
        self.documents[str(self.slots[1])]["input"]["quote_receipt"]="a"*64
        self.write_documents(1)
        with self.assertRaisesRegex(ContractError,"TICKS_TWO_WINDOW_NOT_DISJOINT"):
            self.run_audit()

    def test_same_model_candidate_score_receipt_denied(self):
        self.documents[str(self.slots[1])]["prediction"]["future_fixed_score"]["score_id"]="a"*64
        self.write_documents(1)
        with self.assertRaisesRegex(ContractError,"TICKS_TWO_WINDOW_NOT_DISJOINT"):
            self.run_audit()

    def test_future_horizon_overlap_denied(self):
        self.documents[str(self.slots[1])]["input"]["quote_event_at_ms"]=START+295000
        self.documents[str(self.slots[1])]["prediction"]["anchor_ms"]=START+295000
        self.write_documents(1)
        with self.assertRaisesRegex(ContractError,"TICKS_TWO_WINDOW_NOT_DISJOINT"):
            self.run_audit()

    def test_mismatched_original_model_denied(self):
        self.documents[str(self.slots[1])]["plan"]["baseline_model_sha256"]="f"*64
        self.write_documents(1)
        with self.assertRaisesRegex(ContractError,"TICKS_TWO_WINDOW_NOT_DISJOINT"):
            self.run_audit()

    def test_any_reported_broker_orders_fails(self):
        self.documents[str(self.slots[1])]["commit"]["broker_orders_sent"]=1
        self.write_documents(1)
        with self.assertRaisesRegex(ContractError,"TICKS_TWO_WINDOW_TIMING_OR_AUTHORITY"):
            self.run_audit()

    def test_model_recompute_must_be_true(self):
        with patch.object(cohort,"verify",
            return_value={"new_profile_score_reproduced":False}):
            with self.assertRaisesRegex(ContractError,
                        "TICKS_TWO_WINDOW_NO_EXACT_NEW_PROFILE_REPLAY"):
                cohort.two_original_windows(self.slots[0],self.slots[1],self.config)

    def test_high_hypothetical_forecast_still_no_trades(self):
        self.documents[str(self.slots[1])]["prediction"]["future_fixed_score"]["scaled_bps_ticks"]=1200000000
        self.write_documents(1)
        out=self.run_audit()
        self.assertFalse(out["both_forecast_magnitudes_below_hypothetical_cost"])
        self.assertFalse(out["automatic_trading"])
        self.assertFalse(out["after_cost_alpha_qualified"])

    def write_documents(self, slot):
        from historical_training.common import sha
        root=self.slots[slot]
        d=self.documents[str(root)]
        for name in ("plan","input"):
            (root/(name+".json")).write_bytes(canonical(d[name])+b"\n")
        d["prediction"]["source_input_sha256"]=sha(root/"input.json")
        (root/"prediction.json").write_bytes(canonical(d["prediction"])+b"\n")
        d["commit"]["input_sha256"]=sha(root/"input.json")
        d["commit"]["prediction_sha256"]=sha(root/"prediction.json")
        (root/"commit.json").write_bytes(canonical(d["commit"])+b"\n")
