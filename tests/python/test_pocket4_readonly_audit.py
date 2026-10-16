import json
import tempfile
import unittest
from pathlib import Path

from research.historical_training.pocket4_readonly_audit import audit_bars, audit_pnl


class Pocket4AuditTests(unittest.TestCase):
    def example_pnl(self):
        return {
            "scope": "STRATEGY_PERIOD_NOT_ACCOUNT_NAV",
            "period_id": "reset-test", "observed_at_ms": 1, "currency": "USD",
            "all_in_account_net_pnl": None,
            "totals": {"gross_total_pnl": "5", "net_total_pnl": "2",
                       "fees_paid": "3", "realized_gross_pnl": "5",
                       "unrealized_gross_pnl": "0"},
            "components": [{"instrument": "EUR.USD", "position": "0",
                            "gross_total_pnl": "5", "net_total_pnl": "2",
                            "fees_paid": "3", "realized_gross_pnl": "5",
                            "unrealized_gross_pnl": "0", "execution_count": 2,
                            "fees_complete": True}]
        }

    def test_net_is_not_gross(self):
        self.assertEqual(audit_pnl(self.example_pnl())["net_total_pnl"], "2")

    def test_reject_mismatched_fees(self):
        report = self.example_pnl()
        report["components"][0]["fees_paid"] = "0"
        with self.assertRaisesRegex(ValueError, "TOTALS_MISMATCH"):
            audit_pnl(report)

    def test_historical_spread_zero_is_not_free_execution(self):
        data = {"research_only_revised_not_contemporaneous": True, "bars": [
            {"time": 100, "bid": {"close": "1.20"}, "ask": {"close": "1.20"}},
            {"time": 400, "bid": {"close": "1.21"}, "ask": {"close": "1.22"}}]}
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "paired-bars.json"
            p.write_text(json.dumps(data))
            result = audit_bars(p, None)
            self.assertEqual(result["zero_close_spread_count"], 1)
            self.assertTrue(result["history_not_executable_spread"])

    def test_reject_reordered_bar(self):
        data = {"research_only_revised_not_contemporaneous": True, "bars": [
            {"time": 400, "bid": {"close": "1.20"}, "ask": {"close": "1.20"}},
            {"time": 100, "bid": {"close": "1.20"}, "ask": {"close": "1.20"}}]}
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "paired-bars.json"
            p.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, "BAR_TIMESTAMP"):
                audit_bars(p, None)

    def test_reject_wrong_hash(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "paired-bars.json"
            p.write_text('{"bars":[]}')
            with self.assertRaisesRegex(ValueError, "DATASET_SHA256_MISMATCH"):
                audit_bars(p, "0" * 64)


if __name__ == "__main__":
    unittest.main()
