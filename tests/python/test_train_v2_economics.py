"""Research cost frontier is NOT a realized trade or strategy selection."""
import copy
import unittest

from train_v2.contracts import ContractError, digest
from train_v2.economics import modeled_net_edge, verify_cost_receipt

PERIOD = "c"*64


def fixture():
    return {
        "schema": "heptatrader.ib-cost-evidence.offline.v1",
        "instrument": "EUR.USD",
        "period_source_sha256": PERIOD,
        "exact_paper_account_scoped": True,
        "source": "ORIGINAL_CHAIN_VERIFIED_PAPER_LIVE_QUOTES_AND_OMS_COMMISSION_CALLBACKS",
        "source_prefix_digest": digest({"quotes": 1}),
        "source_prefix_count": 3,
        "quote_count": 270,
        "oms_sha256": digest({"oms": 1}),
        "fx_fee_missing_or_unproved_executions": 0,
        "fx_fee_complete_unique_executions": 190,
        "actual_slippage_verified": False,
        "actual_fill_timestamp_attested": False,
        "historical_bar_bid_ask_used_as_executable_spread": False,
        "commission_callbacks_are_reported_not_broker_flex_statements": True,
        "quote_spread_full_bps": {"p50": 0.178, "p90": 0.2675, "p99": 3.4838},
        "fx_commission_one_way_bps": {"p50": 0.2, "p90": 0.7148, "p99": 0.716},
        "trade_authority": False,
        "orders_sent": 0,
        "broker_requests_sent": 0,
    }


class ResearchEconomicTests(unittest.TestCase):
    def test_small_gross_edge_is_no_trade(self):
        r = modeled_net_edge("0.3", fixture(), PERIOD)
        self.assertEqual(r["assessment"], "RESEARCH_ABSTAIN_COST_GATE")
        self.assertTrue(any(not s["beats_no_trade_hypothetically"] for s in r["scenarios"]))
        self.assertFalse(r["actual_executed_profit_attested"])
        self.assertFalse(r["trade_authority"])

    def test_positive_modeled_edge_never_promotes(self):
        r = modeled_net_edge("20", fixture(), PERIOD)
        self.assertEqual(r["assessment"], "UNQUALIFIED_POSITIVE_MODELED_EDGE")
        self.assertTrue(all(s["beats_no_trade_hypothetically"] for s in r["scenarios"]))
        self.assertFalse(r["automatic_selection"])
        self.assertFalse(r["actual_slippage_attested"])
        self.assertFalse(r["financing_and_cashflow_complete"])
        self.assertEqual(r["broker_orders_sent"], 0)

    def test_stress_uses_p99_spread_and_does_not_change_trade_set(self):
        r = modeled_net_edge("4", fixture(), PERIOD)
        self.assertEqual([s["assumed_slippage_bps_per_side"] for s in r["scenarios"]],
                         ["0.5", "1", "2"])
        self.assertEqual(r["scenarios"][-1]["observed_spread_full_bps"], "3.4838")
        self.assertEqual(len(r["scenarios"]), 3)
        self.assertEqual(r["assessment"], "RESEARCH_ABSTAIN_COST_GATE")

    def test_early_2030s_historical_source_cannot_be_live_scope(self):
        c = fixture()
        c["source"] = "BROKER_HISTORICAL_BARS"
        with self.assertRaisesRegex(ContractError, "COST_EVIDENCE_NOT_FULLY_BOUND"):
            verify_cost_receipt(c, PERIOD)

    def test_missing_commission_cannot_equal_free(self):
        c = fixture()
        c["fx_fee_missing_or_unproved_executions"] = 1
        with self.assertRaises(ContractError):
            modeled_net_edge("100", c, PERIOD)

    def test_wrong_accounting_period_denied(self):
        with self.assertRaises(ContractError):
            modeled_net_edge(10, fixture(), "e"*64)

    def test_forged_execution_slippage_marked_verified_denied(self):
        c = fixture()
        c["actual_slippage_verified"] = True
        with self.assertRaises(ContractError):
            modeled_net_edge(10, c, PERIOD)

    def test_invalid_broker_only_spread_rejected(self):
        c = fixture()
        c["historical_bar_bid_ask_used_as_executable_spread"] = True
        with self.assertRaises(ContractError):
            modeled_net_edge(10, c, PERIOD)

    def test_nonfinite_and_boolean_edges_rejected(self):
        for bad in (False, None, "NaN", "Infinity", "-Infinity"):
            with self.assertRaises(ContractError):
                modeled_net_edge(bad, fixture(), PERIOD)

    def test_no_original_quote_inventory_refused(self):
        c = fixture()
        c["quote_count"] = 0
        with self.assertRaisesRegex(ContractError, "COST_EVIDENCE_NOT_FULLY_BOUND"):
            modeled_net_edge("100", c, PERIOD)

    def test_nonmonotonic_costs_rejected(self):
        c = fixture()
        c["quote_spread_full_bps"]["p99"] = 0.1
        with self.assertRaisesRegex(ContractError, "COST_QUANTILE_ORDER"):
            modeled_net_edge(10, c, PERIOD)

    def test_model_cost_receipt_is_deterministic(self):
        x = modeled_net_edge("-0.5", fixture(), PERIOD)
        y = modeled_net_edge("-0.5", fixture(), PERIOD)
        self.assertEqual(x, y)
        self.assertEqual(x["receipt_id"], digest({k: v for k, v in x.items()
                                                 if k != "receipt_id"}))
