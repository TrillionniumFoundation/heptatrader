"""Offline FX/NAV cohort and persistent IB session acceptance tests.

These fixtures cannot certify an actual Broker valuation timestamp or live quotes.
"""
from __future__ import annotations

import copy
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests/python"))
import hepta_account_observer as observer
import hepta_account_pnl as account
from hepta_pnl_accounting import EvidenceError
from test_account_pnl import period, raw_snapshot


def v3_snapshot(at=1791244800100, first_id=1):
    raw = raw_snapshot(at, at + 20)
    raw["schema"] = "heptatrader.account-download.v3"
    shift = first_id - 1
    raw["request_ids"] = {"ledger": first_id, "account": first_id + 1,
                          "positions": first_id + 2}
    raw["ends"] = [first_id, first_id + 1, first_id + 2]
    for row in raw["rows"]:
        row["request_id"] += shift
    for p in raw["positions"]:
        p["request_id"] += shift
        p["observed_at_ms"] = at + 5
    raw["capture_cohort"] = {
        "schema": "heptatrader.account-capture-cohort.v1",
        "request_end_at_ms": {"ledger": at + 15, "account": at + 16,
                              "positions": at + 17},
        "first_row_callback_at_ms": at + 1,
        "last_row_callback_at_ms": at + 1,
        "broker_valuation_tick_attested": False,
        "time_basis": "LOCAL_CALLBACK_ARRIVAL_NOT_BROKER_VALUATION_TICK",
    }
    return raw


class CompleteCohortTests(unittest.TestCase):
    def test_v3_complete_reconciles_original_dollar_bound(self):
        nav = account.normalize_nav(v3_snapshot(), period())
        self.assertTrue(nav["authoritative"])
        self.assertEqual(nav["fx_reconciliation_bound_usd"], "1.00")
        self.assertFalse(nav["capture_cohort"]["broker_valuation_tick_attested"])

    def test_positions_end_is_mandatory_only_for_new_source(self):
        bad = v3_snapshot()
        bad["ends"].remove(bad["request_ids"]["positions"])
        with self.assertRaisesRegex(EvidenceError, "ALL_THREE_ENDS_REQUIRED"):
            account.normalize_nav(bad, period())
        old = raw_snapshot()
        old["ends"].remove(old["request_ids"]["positions"])
        # Historical v1 positions-complete bit stays false; no retroactive schema migration.
        self.assertFalse(account.normalize_nav(old, period())["positions_complete"])

    def test_position_after_end_or_missing_timestamp_rejected(self):
        for observed in (None, 1791244800118, True):
            raw = v3_snapshot()
            if observed is None:
                del raw["positions"][0]["observed_at_ms"]
            else:
                raw["positions"][0]["observed_at_ms"] = observed
            with self.subTest(observed=observed), self.assertRaisesRegex(
                EvidenceError, "ACCOUNT_POSITION_IDENTITY_INVALID"
            ):
                account.normalize_nav(raw, period())

    def test_unverifiable_broker_tick_stays_unattested(self):
        raw = v3_snapshot()
        raw["capture_cohort"]["broker_valuation_tick_attested"] = True
        with self.assertRaisesRegex(EvidenceError, "COHORT_TIME_OR_IDENTITY"):
            account.normalize_nav(raw, period())

    def test_two_matching_completed_cohorts_are_not_same_tick_proof(self):
        prior = v3_snapshot()
        later = v3_snapshot(prior["observed_at_ms"] + 200, 4)
        verdict = account.compare_rejected_fx_cohorts(later, prior)
        self.assertEqual(verdict["status"], "STABLE_VALUES_BUT_BROKER_TIME_UNATTESTED")
        self.assertFalse(verdict["same_broker_valuation_time_proven"])
        self.assertEqual(verdict["field_changes"], [])
        self.assertEqual(verdict["field_change_count"], 0)

    def test_base_changes_with_constant_fx_is_sequence_diagnostic(self):
        prior = v3_snapshot()
        later = v3_snapshot(prior["observed_at_ms"] + 200, 4)
        for r in later["rows"]:
            if r["request_id"] == 4 and r["currency"] == "BASE" and r["key"] == "NetLiquidationByCurrency":
                r["value"] = "1230.00"
        verdict = account.compare_rejected_fx_cohorts(later, prior)
        self.assertEqual(verdict["status"], "VALUES_CHANGED_BETWEEN_COHORTS")
        self.assertIn("ledger.BASE.NetLiquidationByCurrency", verdict["field_changes"])
        self.assertTrue(verdict["base_values_changed"])
        self.assertEqual(verdict["currency_rates_changed"], [])
        self.assertEqual(verdict["timing_hypothesis"],
                         "BROKER_UPDATE_ORDERING_POSSIBLE_NO_COMMON_MARK")
        self.assertFalse(verdict["same_broker_valuation_time_proven"])

    def test_missing_currency_or_wrong_scope_cannot_claim_stability(self):
        earlier = v3_snapshot()
        later = v3_snapshot(earlier["observed_at_ms"] + 100, 4)
        later["rows"] = [r for r in later["rows"]
                         if not (r["currency"] == "EUR" and r["key"] == "CashBalance")]
        self.assertEqual(account.compare_rejected_fx_cohorts(later, earlier)["status"],
                         "NOT_COMPARABLE")
        later = v3_snapshot(earlier["observed_at_ms"] + 100, 4)
        later["account"] = "DU_OTHER"
        self.assertEqual(account.compare_rejected_fx_cohorts(later, earlier)["status"],
                         "NOT_COMPARABLE")
        later = v3_snapshot(earlier["observed_at_ms"] + 100, 1)
        self.assertEqual(account.compare_rejected_fx_cohorts(later, earlier)["status"],
                         "NOT_COMPARABLE")  # Reused IDs cannot separate generations.

    def test_all_differences_retained(self):
        earlier = v3_snapshot()
        later = v3_snapshot(earlier["observed_at_ms"] + 100, 4)
        for row in later["rows"]:
            if row["request_id"] == 4 and row["key"] == "ExchangeRate" and row["currency"] == "EUR":
                row["value"] = "1.01"
            if row["request_id"] == 4 and row["currency"] == "BASE" and row["key"] == "CashBalance":
                row["value"] = "1090"
        verdict = account.compare_rejected_fx_cohorts(later, earlier)
        self.assertEqual(verdict["field_change_count"], 2)
        self.assertEqual(set(verdict["field_changes"]), {
            "ledger.BASE.CashBalance", "ledger.EUR.ExchangeRate"
        })
        self.assertEqual(verdict["currency_rates_changed"], ["EUR"])
        self.assertEqual(verdict["timing_hypothesis"],
                         "COMMON_BROKER_MARK_NEEDED_TO_CLASSIFY_DIFFERENCE")


class ReconnectTests(unittest.TestCase):
    def test_backoff_caps_and_resets_only_after_recovery(self):
        b = observer.ObserverReconnectBudget()
        for n, seconds in enumerate((10, 20, 40, 80, 120, 120)):
            b.failed(100.0)
            self.assertTrue(b.blocked(100.0 + seconds - 0.001))
            self.assertFalse(b.blocked(100.0 + seconds))
        b.recovered()
        self.assertFalse(b.blocked(100.0))
        b.failed(100.0)
        self.assertEqual(b.next_connect_at, 110.0)

    def test_one_handshake_for_multiple_downloads_and_timeout_forces_new_socket(self):
        class FakeWrapper:
            pass
        class FakeClient:
            connections = 0
            skip_end = False
            cancel_fail = False
            def __init__(self, wrapper):
                self._connected = False
                self._stop = threading.Event()
            def connect(self, host, port, client_id):
                self._connected = True
                FakeClient.connections += 1
                self.nextValidId(123)
                self.managedAccounts("DU_TEST,")
            def run(self):
                self._stop.wait(4)
            def disconnect(self):
                self._connected = False
                self._stop.set()
            def isConnected(self):
                return self._connected
            def sendMsg(self, msg):
                return True
            def reqAccountUpdatesMulti(self, req, account, model, ledger):
                self.boundary.subscribed.add((76, req))
                if not type(self).skip_end:
                    self.accountUpdateMultiEnd(req)
            def reqPositionsMulti(self, req, account, model):
                self.boundary.subscribed.add((74, req))
                if not type(self).skip_end:
                    self.positionMultiEnd(req)
            def cancelAccountUpdatesMulti(self, req):
                if FakeClient.cancel_fail:
                    raise OSError("simulated IB account cancellation failure")
            def cancelPositionsMulti(self, req):
                if FakeClient.cancel_fail:
                    raise OSError("simulated IB position cancellation failure")
            def serverVersion(self):
                return 123

        session = {}
        with patch.object(observer, "load_sdk", return_value=(FakeClient, FakeWrapper)):
            def collect(timeout=1):
                return observer._acquire_account(
                    "DU_TEST", Path("."), 8950, timeout,
                    "period_id", "fixture-period", "heptatrader.account-download.v3",
                    session=session,
                )
            first, second = collect(), collect()
            self.assertEqual(FakeClient.connections, 1)
            self.assertEqual(first["connection_id"], second["connection_id"])
            self.assertEqual(first["ends"], [1, 2, 3])
            self.assertEqual(second["ends"], [4, 5, 6])
            self.assertFalse(first["connection_lost_during_download"])
            self.assertEqual(first["allowed_send_counts"], {})
            FakeClient.cancel_fail = True
            with self.assertRaisesRegex(EvidenceError, "READ_SUBSCRIPTION_CANCEL_FAILED"):
                collect()
            self.assertEqual(session, {})  # Poisoned cancellation closes the socket.
            FakeClient.cancel_fail = False
            third = collect()
            self.assertEqual(FakeClient.connections, 2)
            self.assertEqual(third["ends"], [1, 2, 3])
            FakeClient.skip_end = True
            with self.assertRaisesRegex(EvidenceError, "ACCOUNT_DOWNLOAD_END_TIMEOUT"):
                collect(0.02)
            self.assertEqual(session, {})  # Do not reuse a hung callback stream.
            FakeClient.skip_end = False
            fourth = collect()
            self.assertEqual(FakeClient.connections, 3)
            self.assertEqual(fourth["ends"], [1, 2, 3])
            observer.close_account_session(session)


if __name__ == "__main__":
    unittest.main()
