"""The report borrows verified accounting and copies only its display evidence."""
import copy
import json
import unittest
from unittest import mock
import hepta_pnl_accounting as accounting
import hepta_pnl_report as report
from test_pnl_accounting import basic, observation, period, NOW


class ExcludedFromDisplay:
    def __deepcopy__(self, memo):
        raise AssertionError("full execution history copied before display bound")


class BoundedLedgerViewTests(unittest.TestCase):
    def setUp(self):
        self.events = basic()
        self.period = period()
        self.ledger = accounting.replay(self.events, self.period, "BTC.USD", NOW)

    def view(self, ledger=None, native=None):
        supplied = self.ledger if ledger is None else ledger
        return accounting.instrument_report(self.events, self.period, "BTC.USD",
            observation() if native is None else native, NOW,
            ledger_provider=lambda *args: supplied)

    def test_reusable_supplier_is_not_consumed_or_exposed(self):
        original = copy.deepcopy(self.ledger)
        expected = accounting.instrument_report(self.events, self.period, "BTC.USD", observation(), NOW)
        first = self.view()
        self.assertEqual(first, expected)
        self.assertEqual(self.view(), expected)
        self.assertEqual(self.ledger, original)
        first["executions"][0]["quantity"] = "999"
        first["fees_known_by_currency"]["USD"] = "999"
        first["fee_missing_execution_ids"].append("invented")
        self.assertEqual(self.ledger, original)
        self.assertEqual(self.view(), expected)

    def test_display_limits_apply_before_nested_copy(self):
        count = 4096
        self.ledger["executions"] = ([ExcludedFromDisplay()] * (count - 128) +
                                    [dict(self.ledger["executions"][0], execution_id=str(i)) for i in range(128)])
        for key in ("fee_missing_execution_ids", "fee_conflicting_execution_ids", "settlement_pending_command_ids"):
            self.ledger[key] = [str(i) for i in range(count)]
        actual = self.view()
        self.assertTrue(actual["ledger_complete"])
        self.assertEqual(actual["execution_details_total"], count)
        self.assertTrue(actual["execution_details_truncated"])
        self.assertEqual(len(actual["executions"]), 128)
        self.assertEqual(actual["executions"][0]["execution_id"], "0")
        for key in ("fee_missing_execution_ids", "fee_conflicting_execution_ids", "settlement_pending_command_ids"):
            self.assertEqual(actual[key + "_total"], count)
            self.assertEqual(actual[key], [str(i) for i in range(128)])
        self.assertEqual(len(self.ledger["executions"]), count)

    def test_cached_report_matches_full_replay_after_valuation_and_output_mutation(self):
        parser = report.IncrementalOMSParser()
        self.addCleanup(parser.close)
        raw = b"".join(json.dumps(row, separators=(",", ":")).encode() + b"\n" for row in self.events)
        events = parser.parse(raw)
        with mock.patch.object(report, "replay_ledger", wraps=report.replay_ledger) as replay:
            for price in (90, 95, 90):
                native = observation(bid=price, ask=price + 2)
                actual = accounting.instrument_report(events, self.period, "BTC.USD", native, NOW,
                                                       ledger_provider=parser._cached_ledger)
                expected = accounting.instrument_report(events, self.period, "BTC.USD", native, NOW)
                self.assertEqual(actual, expected)
                actual["executions"][0]["quantity"] = "999"
                actual["fees_known_by_currency"]["USD"] = "999"
            self.assertEqual(replay.call_count, 1)
        self.assertEqual(parser.replay_ledger(events, self.period, "BTC.USD", NOW), self.ledger)


if __name__ == "__main__":
    unittest.main()
