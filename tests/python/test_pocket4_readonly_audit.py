import json
import tempfile
import unittest
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "research"))
from historical_training import pocket4_readonly_audit as audit
from historical_training.pocket4_readonly_audit import audit_bars, audit_pnl


class Pocket4AuditTests(unittest.TestCase):
    def example_pnl(self):
        return {
            "scope": "STRATEGY_PERIOD_NOT_ACCOUNT_NAV",
            "period_id": "reset-test", "observed_at_ms": 1, "currency": "USD",
            "all_in_account_net_pnl": None, "complete": True,
            "valid_until_ms": 1,
            "totals": {"gross_total_pnl": "5", "net_total_pnl": "2",
                       "fees_paid": "3", "fees_complete": True,
                       "realized_gross_pnl": "5", "unrealized_gross_pnl": "0"},
            "components": [{"instrument": "EUR.USD", "position": "0",
                            "gross_total_pnl": "5", "net_total_pnl": "2",
                            "fees_paid": "3", "fees_complete": True,
                            "ledger_complete": True, "valuation_complete": True,
                            "realized_gross_pnl": "5", "unrealized_gross_pnl": "0",
                            "execution_count": 2}]
        }

    def test_net_is_not_gross(self):
        self.assertEqual(audit_pnl(self.example_pnl())["net_total_pnl"], "2")

    def test_report_coverage_and_expired_observation_are_separate(self):
        report = self.example_pnl()
        result = audit_pnl(report)
        self.assertTrue(result["reported_execution_fee_coverage"])
        self.assertTrue(result["complete_strategy_ledger"])
        self.assertFalse(result["mark_current_at_audit"])
        row = result["strategy_components"][0]
        self.assertEqual(row["fee_per_execution_not_per_roundtrip"], "1.5")
        self.assertEqual(row["additional_net_pnl_required_to_break_even"], "0")
        report["components"][0]["fees_complete"] = False
        self.assertFalse(audit_pnl(report)["reported_execution_fee_coverage"])

    def test_reject_mismatched_fees(self):
        report = self.example_pnl()
        report["components"][0]["fees_paid"] = "0"
        with self.assertRaisesRegex(ValueError, "TOTALS_MISMATCH"):
            audit_pnl(report)

    def test_reject_gross_split_mismatch(self):
        report = self.example_pnl()
        report["totals"]["unrealized_gross_pnl"] = "1"
        report["components"][0]["unrealized_gross_pnl"] = "1"
        with self.assertRaisesRegex(ValueError, "TOTALS_REALIZED_UNREALIZED_MISMATCH"):
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

    def test_reject_crossed_bid_ask(self):
        data = {"research_only_revised_not_contemporaneous": True, "bars": [
            {"time": 300, "bid": {"close": "1.201"},
             "ask": {"close": "1.200"}}]}
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "bars.json"
            p.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, "CROSSED_CLOSE"):
                audit_bars(p, None)

    def test_reject_duplicate_key_and_nonfinite_value(self):
        with self.assertRaisesRegex(ValueError, "DUPLICATE_JSON_KEY"):
            audit.decode_strict(b'{"bars": 1, "bars": 2}')
        with self.assertRaisesRegex(ValueError, "NONFINITE_JSON"):
            audit.decode_strict(b'{"x": NaN}')

    def test_reject_symlinked_file(self):
        with tempfile.TemporaryDirectory() as td:
            real = Path(td) / "real.json"
            real.write_text("{}")
            alias = Path(td) / "alias.json"
            alias.symlink_to(real)
            with self.assertRaisesRegex(ValueError, "SOURCE_OPEN_FAILED"):
                audit.load(alias)

    def test_dataset_decoded_from_same_read_as_digest(self):
        obj = {"research_only_revised_not_contemporaneous": True,
               "bars": [{"time": 300, "bid": {"close": "1.2"},
                         "ask": {"close": "1.2"}}]}
        raw = json.dumps(obj).encode("utf-8")
        with patch.object(audit, "read_stable", return_value=raw) as read_once:
            self.assertEqual(audit_bars(Path("/unused/mock"), None)["bar_count"], 1)
            read_once.assert_called_once()

    def test_reject_wrong_hash(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "paired-bars.json"
            p.write_text('{"bars":[]}')
            with self.assertRaisesRegex(ValueError, "DATASET_SHA256_MISMATCH"):
                audit_bars(p, "0" * 64)


if __name__ == "__main__":
    unittest.main()
