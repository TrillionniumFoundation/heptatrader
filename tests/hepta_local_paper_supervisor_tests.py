#!/usr/bin/env python3

from __future__ import annotations

from importlib.machinery import SourceFileLoader
import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "scripts/run_paper_supervisor.py"
loader = SourceFileLoader("hepta_paper_supervisor", str(SOURCE))
spec = importlib.util.spec_from_loader(loader.name, loader)
assert spec is not None and spec.loader is not None
supervisor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(supervisor)


def healthy_record() -> dict[str, object]:
    runtime_binding = {
        "campaign_id": "campaign-1",
        "execution_service_epoch": "hexec-v6-" + "1" * 32,
        "execution_service_fencing_generation": 1,
        "tool_gateway_epoch": "htgw-v1-" + "2" * 32,
        "tool_session_token_sha256": "sha256:" + "3" * 64,
    }
    return {
        "observed_at_ms": 1_000_000,
        "policy": {
            "schema": "hepta.ib-paper-campaign-policy.v5",
            "version": 5,
            "paper_only": True,
            "live_authorized": False,
            "admission_mode": "local-only",
            "order_type": "MKT",
            "tif": "DAY",
            "max_quantity": 25_000,
            "max_active_orders": 1,
            "end_flat_required": True,
            "max_cycles": 720,
            "enabled": True,
            "mutations_authorized": True,
            "campaign_id": "campaign-1",
            "valid_after_ms": 900_000,
            "expires_at_ms": 2_000_000,
        },
        "agent_state": {
            "recovery_required": False,
            "trading_suspended": False,
            "suspension_code": None,
            "last_error": None,
            "pending_order_id": None,
            "pending_order_since_ms": None,
            "runtime_binding": runtime_binding,
            "model_attempt_count": 0,
            "model_timeout_count": 0,
            "model_contract_failure_count": 0,
            "model_transport_failure_count": 0,
            "model_consecutive_failures": 0,
            "last_model_failure_at_ms": None,
            "last_model_failure_code": None,
            "next_model_attempt_after_ms": 0,
            "model_attempt_in_flight": False,
            "model_attempt_started_at_ms": None,
            "model_attempt_position": None,
            "model_attempt_sample_observed_at_ms": None,
        },
        "units": {unit: True for unit in supervisor.CORE_UNITS},
        "broker": {
            "active_order_ids": [],
            "position": 0.0,
            "gross_absolute_position": 0.0,
        },
        "runtime_binding": runtime_binding,
        "strategy_acceptance_proven": True,
    }


class PaperSupervisorTests(unittest.TestCase):
    def acceptance_state(self) -> tuple[
            dict[str, object], dict[str, object], dict[str, object]]:
        record = healthy_record()
        policy = record["policy"]
        runtime = record["runtime_binding"]
        value: dict[str, object] = {
            "schema": "hepta.local-ai-paper-agent-state.v3",
            "entries": 1,
            "exits": 1,
            "last_order_result": "ECONOMIC_FLATTEN_CONFIRMED",
            "recovery_required": False,
            "trading_suspended": False,
            "pending_order_id": None,
            "last_exit_trigger": {
                "trigger": "MODEL_REVERSAL",
                "result": "ECONOMIC_FLATTEN_CONFIRMED",
                "position_after": 0,
            },
            "strategy_acceptance_campaign_id": "campaign-1",
            "strategy_acceptance_runtime_binding": runtime,
            "strategy_acceptance_completed_at_ms": 950_000,
            "strategy_acceptance_position_generation": 10,
            "strategy_acceptance_fx_cash_generation": 11,
            "strategy_acceptance_gross_absolute_position": 0,
            "strategy_acceptance_paper_only": True,
            "strategy_acceptance_live_authorized": False,
        }
        return value, policy, runtime

    def test_healthy_v5_market_paper_checkpoint_passes(self) -> None:
        self.assertEqual(supervisor.classify(healthy_record()), [])

    def test_live_or_non_market_policy_is_rejected(self) -> None:
        for update in (
                {"live_authorized": True}, {"order_type": "LMT"},
                {"admission_mode": "external-p1-finalized"},
                {"schema": "hepta.ib-paper-campaign-policy.v4"},
                {"version": 4}):
            record = healthy_record()
            record["policy"].update(update)
            self.assertIn(
                "POLICY_BOUNDARY_INVALID", supervisor.classify(record))

    def test_future_or_invalid_policy_start_is_rejected(self) -> None:
        for valid_after_ms in (1_000_001, None, True):
            with self.subTest(valid_after_ms=valid_after_ms):
                record = healthy_record()
                record["policy"]["valid_after_ms"] = valid_after_ms
                self.assertIn(
                    "POLICY_BOUNDARY_INVALID", supervisor.classify(record))

    def test_policy_start_equal_to_observation_time_is_allowed(self) -> None:
        record = healthy_record()
        record["policy"]["valid_after_ms"] = record["observed_at_ms"]
        self.assertNotIn(
            "POLICY_BOUNDARY_INVALID", supervisor.classify(record))

    def test_session_renew_and_supervisor_timers_are_core_units(self) -> None:
        required = (
            "hepta-local-paper-session-renew.timer",
            "hepta-local-paper-supervisor.timer",
            "hepta-local-ai-paper-end-flat-retry.timer",
        )
        for unit in required:
            with self.subTest(unit=unit):
                self.assertIn(unit, supervisor.CORE_UNITS)
                record = healthy_record()
                record["units"][unit] = False
                self.assertIn(
                    "CORE_UNIT_INACTIVE", supervisor.classify(record))

    def test_strategy_acceptance_is_bound_to_campaign_and_runtime(self) -> None:
        value, policy, runtime = self.acceptance_state()
        self.assertTrue(supervisor.acceptance_proven(
            value, policy, runtime, 1_000_000))
        for field, changed in (
                ("strategy_acceptance_campaign_id", "other-campaign"),
                ("strategy_acceptance_runtime_binding", {
                    **runtime, "tool_gateway_epoch": "changed"}),
                ("strategy_acceptance_completed_at_ms", 899_999),
                ("strategy_acceptance_live_authorized", True)):
            with self.subTest(field=field):
                candidate = dict(value)
                candidate[field] = changed
                self.assertFalse(supervisor.acceptance_proven(
                    candidate, policy, runtime, 1_000_000))

    def test_stale_pending_order_is_rejected(self) -> None:
        record = healthy_record()
        record["agent_state"].update({
            "pending_order_id": 49,
            "pending_order_since_ms": 900_000,
        })
        self.assertIn("ORDER_SETTLEMENT_STALE", supervisor.classify(record))

    def test_runtime_epoch_drift_is_rejected(self) -> None:
        record = healthy_record()
        record["runtime_binding"] = {
            **record["runtime_binding"],
            "execution_service_epoch": "hexec-v6-" + "4" * 32,
        }
        self.assertIn("RUNTIME_BINDING_INVALID", supervisor.classify(record))

    def test_bounded_position_and_single_order_are_allowed(self) -> None:
        record = healthy_record()
        record["broker"].update({
            "active_order_ids": [51],
            "position": -25_000.0,
            "gross_absolute_position": 25_000.0,
        })
        self.assertEqual(supervisor.classify(record), [])

    def test_durable_model_failure_counters_are_projected(self) -> None:
        record = healthy_record()
        record["agent_state"].update({
            "model_attempt_count": 4,
            "model_timeout_count": 1,
            "model_contract_failure_count": 1,
            "model_transport_failure_count": 1,
            "model_consecutive_failures": 1,
            "last_model_failure_at_ms": 999_000,
            "last_model_failure_code": "MODEL_ATTEMPT_TRANSPORT",
            "next_model_attempt_after_ms": 1_119_000,
        })
        projected = supervisor.model_telemetry(record["agent_state"])
        self.assertEqual(
            set(projected), set(supervisor.MODEL_TELEMETRY_FIELDS))
        self.assertEqual(projected["model_attempt_count"], 4)
        self.assertEqual(projected["model_timeout_count"], 1)
        self.assertEqual(projected["model_contract_failure_count"], 1)
        self.assertEqual(projected["model_transport_failure_count"], 1)
        self.assertEqual(projected["model_consecutive_failures"], 1)
        self.assertEqual(
            projected["last_model_failure_code"],
            "MODEL_ATTEMPT_TRANSPORT")
        self.assertEqual(supervisor.classify(record), [])

    def test_fresh_inflight_model_attempt_is_healthy(self) -> None:
        record = healthy_record()
        record["agent_state"].update({
            "model_attempt_count": 1,
            "model_attempt_in_flight": True,
            "model_attempt_started_at_ms": 900_000,
            "model_attempt_position": 0.0,
            "model_attempt_sample_observed_at_ms": 899_000,
        })
        self.assertEqual(supervisor.classify(record), [])

    def test_stale_inflight_model_attempt_is_unhealthy(self) -> None:
        record = healthy_record()
        record["agent_state"].update({
            "model_attempt_count": 1,
            "model_attempt_in_flight": True,
            "model_attempt_started_at_ms": (
                record["observed_at_ms"] -
                supervisor.MODEL_ATTEMPT_MAX_IN_FLIGHT_MS - 1),
            "model_attempt_position": 0.0,
            "model_attempt_sample_observed_at_ms": 800_000,
        })
        self.assertIn(
            "MODEL_ATTEMPT_STALE", supervisor.classify(record))

    def test_terminal_uncertain_is_unhealthy_without_last_error(self) -> None:
        record = healthy_record()
        record["agent_state"].update({
            "model_attempt_count": 1,
            "model_timeout_count": 1,
            "model_consecutive_failures": 1,
            "last_model_failure_at_ms": 999_000,
            "last_model_failure_code":
                supervisor.MODEL_ATTEMPT_TERMINAL_UNCERTAIN,
        })
        reasons = supervisor.classify(record)
        self.assertIn("MODEL_ATTEMPT_TERMINAL_UNCERTAIN", reasons)
        self.assertNotIn("AGENT_LAST_ERROR", reasons)

    def test_recovery_latch_is_unhealthy_without_last_error(self) -> None:
        record = healthy_record()
        record["agent_state"].update({
            "recovery_required": True,
            "last_error": None,
        })
        reasons = supervisor.classify(record)
        self.assertIn("AGENT_SAFETY_LATCHED", reasons)
        self.assertNotIn("AGENT_LAST_ERROR", reasons)

    def test_invalid_model_telemetry_is_unhealthy(self) -> None:
        updates = (
            {"model_attempt_count": -1},
            {"model_attempt_in_flight": "false"},
            {"model_attempt_in_flight": False,
             "model_attempt_started_at_ms": 900_000},
            {"model_consecutive_failures": 1,
             "last_model_failure_at_ms": None,
             "last_model_failure_code": None},
        )
        for update in updates:
            with self.subTest(update=update):
                record = healthy_record()
                record["agent_state"].update(update)
                self.assertIn(
                    "MODEL_TELEMETRY_INVALID",
                    supervisor.classify(record))


if __name__ == "__main__":
    unittest.main(verbosity=2)
