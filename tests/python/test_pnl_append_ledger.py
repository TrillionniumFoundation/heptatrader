"""Verified operational appends retain accounting; economic evidence never does."""
import hashlib
import json
import unittest
from unittest import mock
import hepta_pnl_accounting as accounting
import hepta_pnl_report as report
from test_pnl_accounting import basic, commission, fill, observation, period, send, START, NOW
from test_pnl_broker_202_cancel import order_cancel_error
from test_pnl_completed_owner_terminal import completed, reconciled

def oms_bytes(rows):
    return b"".join(json.dumps(row, separators=(",", ":")).encode()+b"\n" for row in rows)

class AppendLedgerRegressionTests(unittest.TestCase):
    def setUp(self):
        self.period = period()
        self.reset_parser(basic())
    def reset_parser(self, rows):
        self.parser = report.IncrementalOMSParser()
        self.addCleanup(self.parser.close)
        self.raw = oms_bytes(rows)
        self.events = self.parser.parse(self.raw)
    def append(self, rows):
        self.raw += oms_bytes(rows)
        self.events = self.parser.parse(self.raw)
    def ledgers(self, at_ms=NOW):
        return {instrument:self.parser.replay_ledger(self.events, self.period, instrument, at_ms)
                for instrument in accounting.INSTRUMENTS}
    def assert_full_ledgers(self, actual, at_ms=NOW):
        self.assertEqual(actual, {instrument:accounting.replay(self.events, self.period, instrument, at_ms)
                                  for instrument in accounting.INSTRUMENTS})
    def test_known_operational_append_reuses_both_ledgers_with_fresh_valuation(self):
        old_sha = self.parser.journal_sha256
        with mock.patch.object(report, "replay_ledger", wraps=report.replay_ledger) as raw:
            first = self.ledgers()
            old_view = accounting.instrument_report(self.events, self.period, "BTC.USD",
                observation(bid=90, ask=92), NOW, ledger_provider=self.parser.replay_ledger)
            self.append([
                {"event":"broker_completed_orders_end", "ts_ms":NOW-30},
                {"event":"broker_execution_details_end", "ts_ms":NOW-20, "broker_execution_id":""},
                {"event":"broker_error", "risk_code":"IB_ERROR_162", "ts_ms":NOW-10},
            ])
            actual = self.ledgers()
            self.assertEqual(actual, first)
            self.assert_full_ledgers(actual)
            for instrument in accounting.INSTRUMENTS:
                native = observation(quantity=1 if instrument=="BTC.USD" else 0,
                                     bid=95, ask=97, instrument=instrument)
                cached = accounting.instrument_report(self.events, self.period, instrument, native, NOW,
                                                       ledger_provider=self.parser.replay_ledger)
                expected = accounting.instrument_report(self.events, self.period, instrument, native, NOW)
                self.assertEqual(cached, expected)
                if instrument == "BTC.USD":
                    self.assertNotEqual(cached["net_total_pnl"], old_view["net_total_pnl"])
            self.assertEqual(raw.call_count, 2)
        self.assertNotEqual(old_sha, self.parser.journal_sha256)
        self.assertEqual(self.parser.journal_sha256, hashlib.sha256(self.raw).hexdigest())
        self.assertEqual(self.parser.ledger_preserving_appends, 1)
    def test_subjectless_commission_in_mixed_append_rebuilds_both_ledgers(self):
        rows = basic(); fee = rows.pop()
        self.assertNotIn("instrument", fee)
        self.reset_parser(rows)
        with mock.patch.object(report, "replay_ledger", wraps=report.replay_ledger) as raw:
            before = self.ledgers()
            self.assertFalse(before["BTC.USD"]["fees_complete"])
            self.append([{"event":"broker_execution_details_end", "ts_ms":NOW-1}, fee,
                         {"event":"broker_completed_orders_end", "ts_ms":NOW-1}])
            after = self.ledgers()
            self.assert_full_ledgers(after)
            self.assertTrue(after["BTC.USD"]["fees_complete"])
            self.assertEqual(after["BTC.USD"]["book"].paid_fees, accounting.Decimal(1))
            self.assertEqual(raw.call_count, 4)
        self.assertEqual(self.parser.ledger_preserving_appends, 0)
    def test_broker_202_append_settles_original_partial_fill(self):
        sent = send(); execution = fill(sent, eid="partial-original", qty=.4)
        self.reset_parser([sent, execution, commission(execution)])
        with mock.patch.object(report, "replay_ledger", wraps=report.replay_ledger) as raw:
            before = self.ledgers()
            self.assertEqual(before["BTC.USD"]["settlement_pending_command_ids"], [sent["req_id"]])
            self.append([order_cancel_error(execution)])
            after = self.ledgers()
            self.assert_full_ledgers(after)
            self.assertEqual(after["BTC.USD"]["settlement_pending_command_ids"], [])
            self.assertEqual(after["BTC.USD"]["broker_cancel_witnesses_total"], 1)
            self.assertEqual(raw.call_count, 4)
    def test_completed_order_without_instrument_rebuilds_and_settles(self):
        sent = send(); execution = fill(sent, qty=.4)
        self.reset_parser([sent, execution, commission(execution), reconciled(sent, execution)])
        completion = completed(execution); completion.pop("instrument")
        with mock.patch.object(report, "replay_ledger", wraps=report.replay_ledger) as raw:
            before = self.ledgers()
            self.assertEqual(before["BTC.USD"]["settlement_pending_command_ids"], [sent["req_id"]])
            self.append([completion])
            after = self.ledgers()
            self.assert_full_ledgers(after)
            self.assertEqual(after["BTC.USD"]["settlement_pending_command_ids"], [])
            self.assertEqual(after["BTC.USD"]["owner_terminal_witnesses_total"], 1)
            self.assertEqual(raw.call_count, 4)
    def test_unknown_kind_and_untyped_error_risk_rebuild_both_ledgers(self):
        cases = (
            {"event":"future_accounting_kind"}, {}, {"event":"broker_error"},
            {"event":"broker_error", "risk_code":None},
            {"event":"broker_error", "risk_code":202},
            {"event":"broker_execution_details_end", "broker_execution_id":"e1.1"},
            {"event":"broker_completed_orders_end", "broker_execution_id":"unbound"},
        )
        for row in cases:
            with self.subTest(row=row):
                self.reset_parser(basic())
                with mock.patch.object(report, "replay_ledger", wraps=report.replay_ledger) as raw:
                    self.ledgers()
                    self.append([dict(row, ts_ms=NOW-1)])
                    self.assert_full_ledgers(self.ledgers())
                    self.assertEqual(raw.call_count, 4)
                self.assertEqual(self.parser.ledger_preserving_appends, 0)
    def test_malformed_execution_id_on_operational_record_is_not_hidden(self):
        for malformed in ([], {}):
            with self.subTest(execution_id=malformed):
                self.reset_parser(basic())
                with mock.patch.object(report, "replay_ledger", wraps=report.replay_ledger) as raw:
                    self.ledgers()
                    self.append([{"event":"broker_execution_details_end", "ts_ms":NOW-1,
                                  "broker_execution_id":malformed}])
                    actual = accounting.instrument_report(self.events, self.period, "BTC.USD", observation(), NOW,
                                                           ledger_provider=self.parser.replay_ledger)
                    expected = accounting.instrument_report(self.events, self.period, "BTC.USD", observation(), NOW)
                    self.assertEqual(actual, expected)
                    self.assertFalse(actual["ledger_complete"])
                    self.assertIn("MALFORMED_PNL_EVIDENCE", actual["reason_codes"])
                    self.assertEqual(raw.call_count, 3)
    def test_rewritten_old_economics_plus_operational_suffix_cannot_reuse(self):
        changed = basic(); changed[-1]["broker_econ_commission"] = 2
        changed_prefix = oms_bytes(changed)
        self.assertEqual(len(changed_prefix), len(self.raw))
        with mock.patch.object(report, "replay_ledger", wraps=report.replay_ledger) as raw:
            self.ledgers()
            self.raw = changed_prefix + oms_bytes([{"event":"broker_execution_details_end", "ts_ms":NOW-1}])
            self.events = self.parser.parse(self.raw)
            after = self.ledgers()
            self.assert_full_ledgers(after)
            self.assertEqual(after["BTC.USD"]["book"].paid_fees, accounting.Decimal(2))
            self.assertEqual(raw.call_count, 4)
        self.assertEqual(self.parser.rebuilds, 2)
        self.assertEqual(self.parser.appends, 0)
        self.assertEqual(self.parser.ledger_preserving_appends, 0)
        self.assertEqual(self.parser.journal_sha256, hashlib.sha256(self.raw).hexdigest())
    def test_future_append_and_clock_rollback_keep_existing_revalidation(self):
        with mock.patch.object(report, "replay_ledger", wraps=report.replay_ledger) as raw:
            self.parser.replay_ledger(self.events, self.period, "BTC.USD", NOW)
            self.append([{"event":"broker_execution_details_end", "ts_ms":NOW+100}])
            self.append([{"event":"broker_completed_orders_end", "ts_ms":NOW-1}])
            for at_ms in (NOW, NOW+50, NOW+100):
                actual = self.parser.replay_ledger(self.events, self.period, "BTC.USD", at_ms)
                self.assertEqual(actual, accounting.replay(self.events, self.period, "BTC.USD", at_ms))
            self.assertEqual(raw.call_count, 4)
            self.parser.replay_ledger(self.events, self.period, "BTC.USD", NOW+101)
            self.assertEqual(raw.call_count, 4)
            self.parser.replay_ledger(self.events, self.period, "BTC.USD", NOW+100)
            self.assertEqual(raw.call_count, 5)

    def test_removed_future_timestamp_does_not_keep_ledger_uncacheable(self):
        future = {"event": "broker_execution_details_end", "ts_ms": NOW + 100}
        for replacement in ([], [dict(future, ts_ms=NOW - 100)]):
            with self.subTest(replacement=replacement):
                self.reset_parser(basic() + [future])
                with mock.patch.object(report, "replay_ledger", wraps=report.replay_ledger) as raw:
                    self.ledgers(NOW)
                    self.raw = oms_bytes(basic() + replacement)
                    self.events = self.parser.parse(self.raw)
                    first = self.ledgers(NOW)
                    self.assert_full_ledgers(first, NOW)
                    second = self.ledgers(NOW + 1)
                    self.assert_full_ledgers(second, NOW + 1)
                    self.assertEqual(raw.call_count, 4)
                self.assertEqual(self.parser.rebuilds, 2)
                self.assertEqual(self.parser.appends, 0)
                self.assertEqual(self.parser.journal_sha256, hashlib.sha256(self.raw).hexdigest())

if __name__ == "__main__":
    unittest.main()
