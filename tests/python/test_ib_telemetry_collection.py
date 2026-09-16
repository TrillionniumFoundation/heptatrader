"""IB journald collection/publication uses only synthetic envelopes; no Broker."""
from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import hepta_telemetry_collect as collect
import hepta_oms_report as metrics
import hepta_ib_runtime_report as ib_metrics


def oms_sample(now: int) -> dict:
    return {
        "schema": metrics.SCHEMA, "authorization_effect": "NONE",
        "service_epoch": "ib-fixture-epoch", "observed_at_ms": now,
        "monotonic_ms": 1000, "known": True, "write_poisoned": False,
        "max_bytes": 100000, "max_records": 1000, "bytes": 100,
        "records": 4, "byte_headroom": 99900, "record_headroom": 996,
        "pending_records": 0, "queue_depth": 0, "buffered_depth": 0,
        "status": "OK",
    }


def ib_sample(now: int) -> dict:
    return {
        "schema": ib_metrics.SCHEMA, "authorization_effect": "NONE",
        "paper_authorized": False, "live_authorized": False,
        "observed_at_ms": now, "monotonic_ms": 1000,
        "service_epoch": "ib-fixture-epoch", "connected": True,
        "event_stream_authoritative": True, "event_overflow_generation": 0,
        "connection_epoch": 7, "active_generation": 8, "active_complete": True,
        "active_orders": 0, "active_correlations": 0, "active_reason": "",
        "terminal_generation": 9, "terminal_complete": True,
        "terminal_orders": 0, "terminal_executions": 0,
        "terminal_exposure_generation": 1, "terminal_reason": "",
        "risk_generation": 10, "account_generation": 10,
        "positions_generation": 10, "fx_cash_generation": 10,
        "risk_complete": True, "coherent_risk_complete": True,
        "account_complete": True, "positions_complete": True,
        "fx_cash_complete": True, "risk_absorbed_exposure_generation": 1,
        "gross_absolute_position": 0.0, "risk_reason": "", "positions": 0,
        "post_fill_risk_reconciliation_pending": False,
        "exposure_generation": 1, "recovery_barrier_complete": True,
        "new_connection_epoch_required": False, "recovery_reason": "",
        "terminal_transport_halted": False,
        "terminal_transport_drain_verified": False,
        "terminal_callbacks_in_flight": 0,
        "callback_lag_metrics_present": False,
        "callback_conflict_metrics_present": False,
        "network_policy_metrics_present": False,
    }


def gateway_sample(now: int) -> dict:
    value = {
        "schema": metrics.GATEWAY_SCHEMA, "authorization_effect": "NONE",
        "service_epoch": "gateway-fixture", "observed_at_ms": now,
        "monotonic_ms": 1000,
        "metrics_saturated": False, "results": [0] * 7,
    }
    value.update({key: 0 for key in metrics.GATEWAY_COUNTERS + metrics.GATEWAY_GAUGES})
    # The queue bound is a required non-zero gauge. Set it after populating the
    # generic zero-valued fixture fields so the GATEWAY_GAUGES inventory cannot
    # accidentally overwrite the deliberate bound.
    value["max_pending_connections"] = 128
    for name in metrics.GATEWAY_LATENCIES:
        value[name] = {"samples": 0, "total_ns": 0, "max_ns": 0,
                       "last_ns": 0, "saturated": False}
    return value


def envelope(value: dict, unit: str, invocation: str = "1" * 32) -> bytes:
    return (json.dumps({"_SYSTEMD_UNIT": unit, "_BOOT_ID": "a" * 32,
                        "_SYSTEMD_INVOCATION_ID": invocation,
                        "MESSAGE": json.dumps(value)}) + "\n").encode()


class IbTelemetryCollectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="hepta-ib-collect-")
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.profile = collect.PROFILES["ib-paper"]

    def reader(self, unit: str) -> bytes:
        now = time.time_ns() // 1000000
        if unit == self.profile["gateway"]:
            return envelope(gateway_sample(now), unit)
        # One journald invocation contains both schemas. Each collector selects
        # only its exact supported schema and requires the same service epoch.
        return envelope(oms_sample(now), unit) + envelope(ib_sample(now), unit)

    def test_ib_profile_publishes_three_independent_textfiles(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(collect.collect_profile(
                self.directory, "ib-paper", self.reader), 0)
        for kind in ("oms", "ib", "gateway"):
            path = self.directory / f"hepta_{kind}.prom"
            self.assertTrue(path.is_file(), kind)
            self.assertIn(f"hepta_{kind}_collector_success 1", path.read_text())
        ib_text = (self.directory / "hepta_ib.prom").read_text()
        self.assertIn("hepta_ib_connection_epoch 7", ib_text)
        self.assertIn("hepta_ib_callback_lag_metrics_present 0", ib_text)
        self.assertNotIn("active_reason", ib_text)
        self.assertIn("HEPTA_COLLECTION_IB_OK", output.getvalue())

    def test_missing_ib_schema_replaces_only_ib_health_with_failure(self) -> None:
        self.assertEqual(collect.collect_profile(
            self.directory, "ib-paper", self.reader), 0)
        def without_ib(unit: str) -> bytes:
            now = time.time_ns() // 1000000
            if unit == self.profile["gateway"]:
                return envelope(gateway_sample(now), unit)
            return envelope(oms_sample(now), unit)
        with redirect_stdout(io.StringIO()):
            self.assertEqual(collect.collect_profile(
                self.directory, "ib-paper", without_ib), 2)
        ib_text = (self.directory / "hepta_ib.prom").read_text()
        self.assertIn("hepta_ib_collector_success 0", ib_text)
        self.assertIn("hepta_ib_runtime_telemetry_fresh 0", ib_text)
        self.assertNotIn("hepta_ib_connection_epoch", ib_text)
        self.assertIn("hepta_oms_collector_success 1",
                      (self.directory / "hepta_oms.prom").read_text())
        self.assertIn("hepta_gateway_collector_success 1",
                      (self.directory / "hepta_gateway.prom").read_text())

    def test_ib_kind_cannot_be_collected_from_gateway_unit(self) -> None:
        with self.assertRaises(ValueError):
            collect.collect_kind(self.directory, "ib", self.profile["gateway"], self.reader)
        self.assertEqual(list(self.directory.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
