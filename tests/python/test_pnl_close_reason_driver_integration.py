"""Real merged FX driver CLOSE intent adapter ↔ exact OMS attribution; NO API calls."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "research"))
from pnl_cost_decomposition import close_reason_coverage

spec = importlib.util.spec_from_file_location(
    "heptatrader_close_driver_integration", ROOT / "scripts/heptatrader_paper_driver.py")
driver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(driver)


class RealDriverCloseBinding(unittest.TestCase):
    def test_actual_driver_original_reason_survives_mutable_state_changes(self):
        state = {"close_command_attribution": [], "position": -25000.0,
                 "last_exit_reason": "STALE_UNTRUSTED_STATE"}
        with patch.object(driver, "now_ms", side_effect=[2500, 5000]):
            trigger = driver.freeze_exit_trigger(
                "close-original-1", "MODEL_REVERSAL", -25000.0,
                {"bid": 1.1, "ask": 1.1002, "observed_at_ms": 2400})
            pending = {"command_id": "close-original-1", "broker_order_id": 77,
                       "position_before_send": -25000.0, "exit_trigger": trigger}
            fill = {"broker_execution_id": "exec-real-1",
                    "broker_execution_ids": ["exec-real-1"],
                    "venue_correlation_id": "hepta-v1-sha256:" + "1" * 64,
                    "quantity": 25000.0, "price": 1.1002, "terminal": True}
            driver.record_correlated_close(state, pending, fill, 0.0)
        state["last_exit_reason"] = "MAX_ADVERSE_MOVE"  # Cannot rewrite actual intent.
        episodes = [{"end_command": "close-original-1", "last_original_send_ms": 3000,
                     "net_usd": "-6.50"}]
        executions = [{"command_id": "close-original-1",
                       "execution_id": "exec-real-1", "order_id": 77,
                       "quantity": "25000", "price": "1.1002"}]
        result = close_reason_coverage(episodes, executions,
                                       state["close_command_attribution"], "EUR.USD")
        self.assertEqual(result["exact_original_reason_bound"], 1)
        self.assertEqual(result["reason_buckets"]["MODEL_REVERSAL"]["net_usd"], "-6.50")
        self.assertNotIn("MAX_ADVERSE_MOVE", result["reason_buckets"])
        self.assertEqual(len(state["close_command_attribution"]), 1)

    def test_real_driver_legacy_close_is_unknown(self):
        with patch.object(driver, "now_ms", return_value=5000):
            state = {}
            pending = {"command_id": "old-close", "broker_order_id": 78}
            fill = {"broker_execution_id": "exec-2",
                    "venue_correlation_id": "hepta-v1-sha256:" + "2" * 64,
                    "quantity": 1.0, "price": 1.1, "terminal": True}
            driver.record_correlated_close(state, pending, fill, 0)
        result = close_reason_coverage(
            [{"end_command": "old-close", "last_original_send_ms": 3000,
              "net_usd": "-1"}],
            [{"command_id": "old-close", "execution_id": "exec-2", "order_id": 78,
             "quantity": "1", "price": "1.1"}],
            state["close_command_attribution"], "EUR.USD")
        self.assertEqual(result["exact_original_reason_bound"], 0)
        self.assertEqual(result["unknown_or_unverified"], 1)

    def test_driver_diagnostic_corruption_does_not_change_confirmed_exit(self):
        state = {"close_command_attribution": "CORRUPT"}
        with patch.object(driver, "now_ms", return_value=5000):
            driver.record_close_diagnostic(
                state, {"command_id": "close", "broker_order_id": 11},
                {"broker_execution_id": "exec",
                 "venue_correlation_id": "hepta-v1-sha256:" + "3" * 64,
                 "quantity": 1., "price": 1.1, "terminal": True}, 0.0)
        self.assertEqual(state["close_command_attribution"], "CORRUPT")
        self.assertEqual(state["last_close_attribution_error"]["exception_type"],
                         "DriverError")
        self.assertNotIn("close_command_attribution_receipt", state)


if __name__ == "__main__":
    unittest.main()
