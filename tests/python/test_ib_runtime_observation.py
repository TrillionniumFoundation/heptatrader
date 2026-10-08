from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import hepta_ib_runtime_report as report

CPP = r'''
#include "HeptaTrade/execution/ib_runtime_observation.h"
#include <iostream>
int main() {
    IbRuntimeObservationSnapshot value;
    value.observedAtMs = 10000;
    value.monotonicMs = 5000;
    value.serviceEpoch = "ib-fixture-epoch";
    value.connected = true;
    value.eventStreamAuthoritative = true;
    value.connectionEpoch = 7;
    value.activeGeneration = 11;
    value.activeComplete = true;
    value.activeOrders = 2;
    value.activeCorrelations = 2;
    value.terminalGeneration = 12;
    value.terminalComplete = true;
    value.terminalOrders = 4;
    value.terminalExecutions = 3;
    value.terminalExposureGeneration = 8;
    value.riskGeneration = 13;
    value.accountGeneration = 13;
    value.positionsGeneration = 13;
    value.fxCashGeneration = 13;
    value.riskComplete = true;
    value.coherentRiskComplete = true;
    value.accountComplete = true;
    value.positionsComplete = true;
    value.fxCashComplete = true;
    value.riskAbsorbedExposureGeneration = 8;
    value.grossAbsolutePosition = 25.5;
    value.positions = 1;
    value.exposureGeneration = 8;
    value.recoveryBarrierComplete = true;
    value.activeReason = "";
    value.terminalReason = "";
    value.riskReason = "";
    value.recoveryReason = "fixture \"safe\"\nreason";
    value.callbackQueueLag.Observe(2000000);
    value.callbackConflictCount = 3;
    value.quoteAgeMetricsPresent = true;
    value.primaryQuoteAgeValid = true;
    value.primaryQuoteAgeMs = 125;
    value.secondaryCashQuoteCount = 1;
    value.secondaryCashQuoteMetricsPresent = true;
    value.secondaryCashQuoteInstrument = "EUR.USD";
    value.secondaryCashQuoteRequestId = 1000003;
    value.secondaryCashQuoteDispatchAccepted = true;
    value.secondaryCashBidAgeValid = true;
    value.secondaryCashBidAgeMs = 850;
    value.secondaryCashAskAgeValid = true;
    value.secondaryCashAskAgeMs = 4750;
    value.snapshotAgeMetricsPresent = true;
    value.authoritativeSnapshotAgeValid = true;
    value.authoritativeSnapshotAgeMs = 250;
    value.brokerReconciliationDurationMetricsPresent = true;
    value.brokerReconciliationDuration.Observe(4000000);
    value.brokerReconnectDurationMetricsPresent = true;
    value.brokerReconnectDuration.Observe(8000000);
    value.brokerReconnectRefreshDurationMetricsPresent = true;
    value.brokerReconnectRefreshDuration.Observe(5000000);
    std::cout << SerializeIbRuntimeObservation(value) << "\n";
}
'''


class IbRuntimeObservationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.directory = tempfile.TemporaryDirectory(prefix="hepta-ib-runtime-observation-")
        cls.addClassCleanup(cls.directory.cleanup)
        root = Path(cls.directory.name)
        source, binary = root / "probe.cpp", root / "probe"
        source.write_text(CPP, encoding="utf-8")
        subprocess.run(
            ["g++", "-std=c++11", "-pthread", "-Wall", "-Wextra", "-Werror",
             "-I", str(ROOT), str(source), "-o", str(binary)],
            check=True, capture_output=True, text=True, timeout=60,
        )
        completed = subprocess.run([str(binary)], check=True, capture_output=True,
                                   text=True, timeout=5)
        cls.sample = json.loads(completed.stdout)

    def test_native_serializer_and_reporter_agree(self) -> None:
        sample = report.validate(copy.deepcopy(self.sample))
        self.assertEqual(sample["connection_epoch"], 7)
        self.assertEqual(sample["risk_generation"], 13)
        self.assertEqual(sample["recovery_reason"], 'fixture "safe"\nreason')
        summary = report.report([sample], 10000)
        self.assertTrue(summary["fresh"])
        self.assertEqual(summary["alerts"], [])
        text = report.prometheus(sample, summary)
        self.assertIn("hepta_ib_active_snapshot_generation 11", text)
        self.assertIn("hepta_ib_gross_absolute_position 25.5", text)
        self.assertIn("hepta_ib_callback_lag_metrics_present 1", text)
        self.assertIn("hepta_ib_callback_queue_lag_seconds_count 1", text)
        self.assertIn("hepta_ib_callback_conflicts_total 3", text)
        self.assertIn("hepta_ib_primary_quote_age_ms 125", text)
        self.assertEqual(sample["secondary_cash_quote_instrument"], "EUR.USD")
        self.assertIn("hepta_ib_secondary_cash_quote_request_id 1000003", text)
        self.assertIn("hepta_ib_secondary_cash_bid_age_ms 850", text)
        self.assertIn("hepta_ib_secondary_cash_ask_age_ms 4750", text)
        self.assertIn("hepta_ib_authoritative_snapshot_age_ms 250", text)
        self.assertIn("hepta_ib_broker_reconciliation_duration_seconds_count 1", text)
        self.assertIn("hepta_ib_broker_reconnect_duration_seconds_count 1", text)
        self.assertIn("hepta_ib_broker_reconnect_refresh_duration_seconds_count 1", text)
        self.assertIn("hepta_ib_post_fill_reconciliation_pending_observed_ms 0", text)
        self.assertNotIn("fixture", text)
        self.assertNotIn("agent", text)

    def test_legacy_absence_is_not_rewritten_as_zero_measurements(self) -> None:
        sample = copy.deepcopy(self.sample)
        sample["callback_lag_metrics_present"] = False
        sample["callback_conflict_metrics_present"] = False
        sample.pop("callback_queue_lag")
        sample.pop("callback_conflicts_total")
        sample.pop("callback_conflict_metrics_saturated")
        sample["quote_age_metrics_present"] = False
        for key in (
            "secondary_cash_quote_count", "secondary_cash_quote_metrics_present",
            "secondary_cash_quote_instrument", "secondary_cash_quote_request_id",
            "secondary_cash_quote_dispatch_accepted", "secondary_cash_bid_age_valid",
            "secondary_cash_bid_age_ms", "secondary_cash_ask_age_valid",
            "secondary_cash_ask_age_ms",
        ):
            sample.pop(key)
        sample["snapshot_age_metrics_present"] = False
        sample["broker_reconciliation_duration_metrics_present"] = False
        sample["broker_reconnect_duration_metrics_present"] = False
        sample["broker_reconnect_refresh_duration_metrics_present"] = False
        sample = report.validate(sample)
        self.assertFalse(sample["network_policy_metrics_present"])
        text = report.prometheus(sample, report.report([sample], 10000))
        self.assertNotIn("callback_queue_lag_seconds", text)
        self.assertNotIn("callback_conflicts_total", text)
        self.assertNotIn("network_policy_state", text)
        self.assertNotIn("broker_reconnect_duration_seconds", text)
        self.assertNotIn("broker_reconnect_refresh_duration_seconds", text)

    def test_secondary_cash_missing_leg_never_becomes_zero_age(self) -> None:
        sample = copy.deepcopy(self.sample)
        sample["secondary_cash_ask_age_valid"] = False
        sample["secondary_cash_ask_age_ms"] = 0
        accepted = report.validate(sample)
        self.assertIn("hepta_ib_secondary_cash_ask_age_valid 0",
                      report.prometheus(accepted, report.report([accepted], 10000)))
        self.assertNotIn("hepta_ib_secondary_cash_ask_age_ms ",
                         report.prometheus(accepted, report.report([accepted], 10000)))
        sample["secondary_cash_ask_age_ms"] = 1
        with self.assertRaisesRegex(ValueError, "unproven secondary CASH"):
            report.validate(sample)

    def test_secondary_cash_scope_and_dispatch_provenance_are_strict(self) -> None:
        for update in (
            {"secondary_cash_quote_count": 2},
            {"secondary_cash_quote_instrument": "DU_NOT_SYMBOL"},
            {"secondary_cash_quote_dispatch_accepted": False},
            {"secondary_cash_quote_request_id": 0},
        ):
            altered = copy.deepcopy(self.sample)
            altered.update(update)
            with self.subTest(update=update), self.assertRaises(ValueError):
                report.validate(altered)

    def test_secondary_span_breaks_on_gap_request_change_and_clock(self) -> None:
        earlier = copy.deepcopy(self.sample)
        earlier["secondary_cash_ask_age_ms"] = 7000
        earlier["monotonic_ms"] = 1000
        later = copy.deepcopy(earlier)
        later["monotonic_ms"] = 61000
        later["observed_at_ms"] += 60000
        summary = report.report([earlier, later], later["observed_at_ms"])
        self.assertEqual(summary["secondary_cash_quote_unfresh_sample_span_ms"], 0)
        self.assertFalse(summary["sample_span_is_continuous_proof"])
        for delta in (0, -1000, 6001):
            changed = copy.deepcopy(later)
            changed["monotonic_ms"] = earlier["monotonic_ms"] + delta
            self.assertEqual(report.report([earlier, changed], changed["observed_at_ms"])[
                "secondary_cash_quote_unfresh_observed_ms"], 0)
        later["monotonic_ms"] = 6000
        self.assertEqual(report.report([earlier, later], later["observed_at_ms"])[
            "secondary_cash_quote_unfresh_observed_ms"], 5000)
        later["secondary_cash_quote_request_id"] += 1
        self.assertEqual(report.report([earlier, later], later["observed_at_ms"])[
            "secondary_cash_quote_unfresh_observed_ms"], 0)

    def test_secondary_cash_stale_streak_resets_with_real_freshness_and_epoch(self) -> None:
        samples = []
        for observed, monotonic, ask_age in (
            (6000, 1000, 5200), (8000, 3000, 7200), (10000, 5000, 9200)
        ):
            sample = copy.deepcopy(self.sample)
            sample["observed_at_ms"] = observed
            sample["monotonic_ms"] = monotonic
            sample["secondary_cash_ask_age_ms"] = ask_age
            samples.append(sample)
        summary = report.report(samples, 10000)
        self.assertEqual(summary["secondary_cash_quote_unfresh_observed_ms"], 4000)
        self.assertIn(
            "hepta_ib_secondary_cash_quote_unfresh_observed_ms 4000",
            report.prometheus(samples[-1], summary))
        recovered = copy.deepcopy(samples[-1])
        recovered["monotonic_ms"] = 6000
        recovered["observed_at_ms"] = 11000
        recovered["secondary_cash_ask_age_ms"] = 100
        self.assertEqual(report.report(samples + [recovered], 11000)[
            "secondary_cash_quote_unfresh_observed_ms"], 0)
        new_epoch = copy.deepcopy(samples[-1])
        new_epoch["connection_epoch"] = 8
        new_epoch["observed_at_ms"] = 11000
        new_epoch["monotonic_ms"] = 6000
        self.assertEqual(report.report(samples + [new_epoch], 11000)[
            "secondary_cash_quote_unfresh_observed_ms"], 0)

    def test_continuous_stall_durations_are_bounded_by_retained_samples_and_epoch(self) -> None:
        samples = []
        for observed, monotonic in ((6000, 1000), (8000, 3000), (10000, 5000)):
            sample = copy.deepcopy(self.sample)
            sample["observed_at_ms"] = observed
            sample["monotonic_ms"] = monotonic
            sample["post_fill_risk_reconciliation_pending"] = True
            sample["risk_complete"] = False
            sample["coherent_risk_complete"] = False
            sample["terminal_transport_halted"] = True
            sample["terminal_callbacks_in_flight"] = 2
            samples.append(sample)
        summary = report.report(samples, 10000)
        self.assertEqual(summary["post_fill_reconciliation_pending_observed_ms"], 4000)
        self.assertEqual(summary["authoritative_snapshot_incomplete_observed_ms"], 4000)
        self.assertEqual(summary["terminal_callback_drain_pending_observed_ms"], 4000)
        text = report.prometheus(samples[-1], summary)
        self.assertIn("hepta_ib_post_fill_reconciliation_pending_observed_ms 4000", text)
        self.assertIn("hepta_ib_authoritative_snapshot_incomplete_observed_ms 4000", text)
        self.assertIn("hepta_ib_terminal_callback_drain_pending_observed_ms 4000", text)

        changed = copy.deepcopy(samples[-1])
        changed["connection_epoch"] = 8
        changed["monotonic_ms"] = 6000
        changed["observed_at_ms"] = 11000
        reset = report.report(samples + [changed], 11000)
        self.assertEqual(reset["post_fill_reconciliation_pending_observed_ms"], 0)
        self.assertEqual(reset["authoritative_snapshot_incomplete_observed_ms"], 0)
        self.assertEqual(reset["terminal_callback_drain_pending_observed_ms"], 0)

    def test_incomplete_or_inconsistent_samples_fail_closed(self) -> None:
        mutations = [
            lambda value: value.__setitem__("paper_authorized", True),
            lambda value: value.__setitem__("service_epoch", "bad epoch"),
            lambda value: value.__setitem__("active_correlations", 3),
            lambda value: value.__setitem__("gross_absolute_position", -1.0),
            lambda value: value.__setitem__("connected", 1),
            lambda value: value.__setitem__("terminal_transport_drain_verified", True),
            lambda value: value.__setitem__("coherent_risk_complete", True),
        ]
        for index, mutation in enumerate(mutations):
            value = copy.deepcopy(self.sample)
            if index == 6:
                value["account_complete"] = False
            mutation(value)
            with self.subTest(index=index), self.assertRaises(ValueError):
                report.validate(value)

    def test_reconnect_latency_presence_shape_and_saturation_fail_closed(self) -> None:
        wrong_presence = copy.deepcopy(self.sample)
        wrong_presence["broker_reconnect_duration_metrics_present"] = 1
        with self.assertRaises(ValueError):
            report.validate(wrong_presence)

        malformed = copy.deepcopy(self.sample)
        malformed["broker_reconnect_refresh_duration"]["bucket_counts"] = [1]
        with self.assertRaises(ValueError):
            report.validate(malformed)

        saturated = copy.deepcopy(self.sample)
        saturated["broker_reconnect_duration"]["saturated"] = True
        saturated = report.validate(saturated)
        text = report.prometheus(
            saturated, report.report([saturated], saturated["observed_at_ms"]))
        self.assertIn(
            "hepta_ib_broker_reconnect_duration_metrics_saturated 1", text)
        self.assertNotIn(
            "hepta_ib_broker_reconnect_duration_seconds_count", text)

    def test_alerts_expose_runtime_health_without_reason_labels(self) -> None:
        sample = copy.deepcopy(self.sample)
        sample["connected"] = False
        sample["event_stream_authoritative"] = False
        sample["post_fill_risk_reconciliation_pending"] = True
        sample["new_connection_epoch_required"] = True
        summary = report.report([sample], 10000)
        rules = {item["rule_id"] for item in summary["alerts"]}
        self.assertIn("IB_RUNTIME_DISCONNECTED", rules)
        self.assertIn("IB_EVENT_STREAM_INCOMPLETE", rules)
        self.assertIn("IB_POST_FILL_RECONCILIATION_PENDING", rules)
        self.assertIn("IB_NEW_CONNECTION_EPOCH_REQUIRED", rules)
        text = report.prometheus(sample, summary)
        self.assertNotIn(sample["recovery_reason"], text)

    def test_file_reader_rejects_duplicate_keys_and_special_input(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / "telemetry.jsonl"
            path.write_text(json.dumps(self.sample) + "\n", encoding="utf-8")
            self.assertEqual(len(report.read_samples(path)), 1)
            path.write_text('{"schema":"%s","schema":"%s"}\n' % (report.SCHEMA, report.SCHEMA))
            with self.assertRaises(ValueError):
                report.read_samples(path)


if __name__ == "__main__":
    unittest.main()
