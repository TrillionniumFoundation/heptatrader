"""Pure read-only stratification checks; no broker, runtime mutation or model selection."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "research"))
from pnl_cost_decomposition import duration_side_breakdown


def episode(side, seconds, gross, fee):
    from decimal import Decimal
    return {
        "opening_side": side, "send_to_send_seconds": seconds,
        "gross_usd": str(Decimal(gross)), "commissions_usd": str(Decimal(fee)),
        "net_usd": str(Decimal(gross) - Decimal(fee)),
    }


class DurationSideTests(unittest.TestCase):
    def test_all_buckets_and_sides_with_exact_money(self):
        cases = [
            episode("BUY", 299.999, "5", "4"),
            episode("SELL", 300, "3", "4"),
            episode("BUY", 899.999, "4", "4"),
            episode("SELL", 900, "-1", "4"),
            episode("BUY", 3599.999, "8", "4"),
            episode("SELL", 3600, "10", "4"),
            episode("BUY", None, "2", "1"),
        ]
        result = duration_side_breakdown(cases)
        self.assertEqual(result["classification"], "EX_POST_ONLY_NO_CAUSAL_POLICY_OR_PROSPECTIVE_ALPHA")
        self.assertTrue(result["reconciled_to_closed_episodes"])
        groups = result["groups"]
        self.assertEqual(set(groups), {
            "BUY:LT_5MIN", "SELL:5_TO_15MIN", "BUY:5_TO_15MIN",
            "SELL:15_TO_60MIN", "BUY:15_TO_60MIN",
            "SELL:GE_60MIN", "BUY:UNKNOWN",
        })
        self.assertEqual(groups["BUY:LT_5MIN"]["net_usd"], "1")
        self.assertEqual(groups["SELL:5_TO_15MIN"]["gross_positive_but_net_nonpositive"], 1)
        self.assertEqual(groups["SELL:GE_60MIN"]["net_positive_roundtrips"], 1)
        from decimal import Decimal
        self.assertEqual(sum(Decimal(v["gross_usd"]) for v in groups.values()), Decimal("31"))
        self.assertEqual(sum(Decimal(v["reported_commissions_usd"]) for v in groups.values()), Decimal("25"))
        self.assertEqual(sum(Decimal(v["net_usd"]) for v in groups.values()), Decimal("6"))

    def test_empty_episodes_is_empty_not_profit(self):
        result = duration_side_breakdown([])
        self.assertEqual(result["groups"], {})
        self.assertEqual(result["classification"], "EX_POST_ONLY_NO_CAUSAL_POLICY_OR_PROSPECTIVE_ALPHA")

    def test_gross_fee_net_inconsistency_rejected(self):
        item = episode("BUY", 30, "2", "1")
        item["net_usd"] = "2"
        with self.assertRaisesRegex(ValueError, "EPISODE_UNRECONCILED_NET"):
            duration_side_breakdown([item])

    def test_missing_valid_duration_is_unknown(self):
        self.assertIn("SELL:UNKNOWN", duration_side_breakdown([episode("SELL", None, "0", "2")])["groups"])

    def test_negative_or_nonfinite_duration_rejected(self):
        for bad in (-0.001, float("nan"), float("inf"), True, "300"):
            with self.subTest(bad=bad):
                with self.assertRaisesRegex(ValueError, "EPISODE_INVALID_ORIGINAL_SEND_DURATION"):
                    duration_side_breakdown([episode("BUY", bad, "1", "0.2")])

    def test_unknown_side_rejected(self):
        with self.assertRaisesRegex(ValueError, "EPISODE_UNKNOWN_OPENING_SIDE"):
            duration_side_breakdown([episode("SHORT", 100, "1", "1")])

    def test_nonfinite_money_rejected(self):
        item = episode("BUY", 10, "2", "1")
        item["gross_usd"] = "NaN"
        with self.assertRaisesRegex(ValueError, "EPISODE_UNRECONCILED_NET"):
            duration_side_breakdown([item])


if __name__ == "__main__":
    unittest.main()
