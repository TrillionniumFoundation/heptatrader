#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import os
import pathlib
import tempfile
import time


ROOT = pathlib.Path(__file__).resolve().parents[1]


def _load(name: str, filename: str):
    path = ROOT / "scripts" / filename
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


release_gate = _load("release_gate", "openclaw_hepta_ai_native_os_release_gate.py")
data_plane = _load("data_plane", "openclaw_agent_trader_data_plane_contract.py")
paper_contract = _load("paper_contract", "openclaw_agent_trader_ai_native_paper_request_contract.py")
shadow_resilience = _load("shadow_resilience", "openclaw_agent_trader_ai_native_shadow_resilience_gate.py")
supervisor = _load("supervisor", "openclaw_agent_trader_ai_native_supervisor.py")
signal_writer = _load("signal_writer", "openclaw_agent_trader_ai_native_signal_writer.py")
idempotency_guard = _load("idempotency_guard", "openclaw_agent_trader_idempotency_guard.py")
data_plane_refresh = _load("data_plane_refresh", "openclaw_agent_trader_data_plane_refresh.py")


def _intent(ts_ms: int) -> dict:
    return {
        "schema": "openclaw.hepta.agent_trader_intent.v1",
        "intent_id": "fixture-intent",
        "ts_ms": ts_ms,
        "agent": "fixture.agent",
        "assetClass": "FX",
        "venue": "IB",
        "instrument": "EUR.USD",
        "lifecycle_stage": "dry_run",
        "side": "BUY",
        "horizon": {"decision_window_sec": 60, "expected_holding_sec": 300},
        "thesis": {"summary": "fixture thesis"},
        "invalidation": {"summary": "fixture invalidation"},
        "entry_model": {"summary": "fixture entry"},
        "exit_plan": {"time_stop_sec": 300},
        "risk": {"risk_budget_id": "paper_micro_fixture"},
        "evidence_refs": ["fixture"],
        "execution_request": {"requested_action": "shadow_record", "effect": "none", "allow_broker": False, "allow_oms": False},
        "observeOnly": True,
        "paperOnly": True,
        "nonConsumable": True,
    }


def _paper_request(ts_ms: int) -> dict:
    return {
        "schema": paper_contract.SCHEMA,
        "request_id": "fixture-request",
        "idempotency_key": "fixture-idempotency",
        "ts_ms": ts_ms,
        "ttl_sec": 60,
        "dryRun": True,
        "intent": _intent(ts_ms),
        "risk_envelope": {"max_notional_usd": 500, "max_loss_usd": 2},
        "execution_request": {"requested_action": "paper_request_review", "effect": "none", "allow_broker": False, "allow_oms": False},
    }


def _closed_order() -> dict:
    return {
        "order_submission_allowed": False,
        "execution_allowed": False,
        "paper_consumer_clearance_allowed": False,
        "summary": {"order_intent_count": 0, "place_sent_count": 0, "route_leaks": []},
    }


def _write(path: pathlib.Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) + "\n", encoding="utf-8")


def _strict_oos_protocol() -> dict:
    return {
        "schema": "openclaw.hepta.agent_trader_ai_native_oos_protocol.v1",
        "protocol_id": "fixture-oos-v3",
        "cutover_utc": "2026-07-11T00:00:00Z",
        "post_cutover_partition": "forward_oos",
        "observation_sampling": {
            "enabled": True,
            "cadence_sec": 900,
            "cycles_per_sample": 1,
            "require_reviewable_bucket": False,
            "natural_abstention_allowed": True,
            "failure_retry_limit": 1,
            "failure_retry_delay_sec": 300,
            "execution_effect": "none",
        },
        "policy_epoch": {
            "epoch_id": "fixture-epoch-1",
            "model_version": "fixture-model-v1",
            "prompt_version": "fixture-prompt-v1",
            "expected_policy_state_hash": "fixture-policy-hash",
            "expected_critique_snapshot_hash": "fixture-critique-hash",
        },
        "evaluation_gate": {
            "sequential_checkpoints": [100, 150, 200],
            "hac_lag": 4,
            "minimum_effective_samples": 60,
            "minimum_qualified_sessions": 2,
            "minimum_qualified_regimes": 2,
            "minimum_samples_per_session": 20,
            "minimum_samples_per_regime": 15,
            "minimum_samples_per_side": 20,
            "required_horizons_sec": [600, 900],
            "minimum_samples_per_horizon": 15,
            "minimum_samples_per_instrument_side": 10,
            "minimum_qualified_instrument_side_buckets": 4,
            "require_nonnegative_qualified_stratum_mean": True,
            "max_instrument_side_share": 0.50,
        },
    }


def _formal_oos_row(index: int, *, protocol: dict | None = None, adjusted_bps: float = 1.70) -> dict:
    protocol = protocol or _strict_oos_protocol()
    epoch = protocol["policy_epoch"]
    instruments = ["EUR.USD", "GBP.USD", "USD.CNH", "USD.JPY"]
    return {
        "entry_ts_ms": 1_000_000 + index * 900_000,
        "directional": True,
        "signed_bps": adjusted_bps + 0.30,
        "estimated_round_trip_cost_bps": 0.30,
        "cost_adjusted_signed_bps": adjusted_bps,
        "entry_spread_bps": 0.10,
        "exit_spread_bps": 0.14,
        "cost_model": {"used_spread_observation_count": 2},
        "instrument": instruments[index % len(instruments)],
        "decision": "BUY" if index % 2 == 0 else "SELL",
        "horizon_sec": 600 if index % 2 == 0 else 900,
        "evaluation_context": {
            "partition": "forward_oos",
            "partition_assigned_before_outcome": True,
            "protocol_id": protocol["protocol_id"],
            "policy_epoch_id": epoch["epoch_id"],
            "policy_state_hash": epoch["expected_policy_state_hash"],
            "critique_snapshot_hash": epoch["expected_critique_snapshot_hash"],
            "model_version": epoch["model_version"],
            "prompt_version": epoch["prompt_version"],
            "observation_eligible": True,
            "data_plane_contract_hash": "fixture-data-contract-hash",
            "sampling_slot_id": f"fixture-slot-{index:03d}",
            "market_session": "london" if index % 2 == 0 else "new_york",
            "market_regime": "trend/stable" if index % 3 else "range/stable",
        },
    }


def test_paper_request_contract_is_valid_but_never_enabled():
    now_ms = int(time.time() * 1000)
    issues = paper_contract.validate_request(
        _paper_request(now_ms),
        whitelist=["EUR.USD"],
        max_notional_usd=1000,
        max_loss_usd=5,
        now_ms=now_ms,
    )
    assert issues == []
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        _write(root / "request.json", _paper_request(now_ms))
        _write(root / "order.json", _closed_order())
        args = argparse.Namespace(
            input=root / "request.json",
            order_sentinel=root / "order.json",
            instrument_whitelist=["EUR.USD"],
            max_notional_usd=1000.0,
            max_loss_usd=5.0,
            now_ms=now_ms,
            write_review=False,
        )
        report = paper_contract.build_report(args)
        assert report["contract_valid"] is True
        assert report["review_record_allowed"] is True
        assert report["paper_request_enabled"] is False
        assert report["paper_request_allowed"] is False
        assert report["guard"]["writes_paper_queue"] is False
        assert report["guard"]["calls_broker"] is False


def test_paper_request_rejects_broker_effect_and_limit_breach():
    now_ms = int(time.time() * 1000)
    request = _paper_request(now_ms)
    request["execution_request"]["allow_broker"] = True
    request["risk_envelope"]["max_notional_usd"] = 5000
    issues = paper_contract.validate_request(
        request,
        whitelist=["EUR.USD"],
        max_notional_usd=1000,
        max_loss_usd=5,
        now_ms=now_ms,
    )
    assert "paper_request_allow_broker_must_be_false" in issues
    assert "paper_request_max_notional_exceeds_limit" in issues


def test_idempotency_guard_survives_reopen_and_rejects_duplicate():
    with tempfile.TemporaryDirectory() as td:
        ledger = pathlib.Path(td) / "ledger.json"
        first = idempotency_guard.check_and_reserve(ledger, "fixture-key", ttl_sec=60, now_ms=1_000_000)
        second = idempotency_guard.check_and_reserve(ledger, "fixture-key", ttl_sec=60, now_ms=1_000_001)
        assert first["reserved"] is True
        assert second["duplicate"] is True
        assert second["guard"]["writes_order_intent"] is False


def test_data_plane_contract_fails_closed_on_stale_or_missing_assets():
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        old = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=2)).isoformat()
        _write(root / "worklist.json", {"generated_at_utc": old, "summary": {"source_contract_states": {"FX": "read_only_mark_source_stale"}}})
        _write(root / "coverage.json", {"generated_at_utc": old, "summary": {"fresh_by_asset": {}, "stale_by_asset": {"FX": 4}}})
        args = argparse.Namespace(
            asset_worklist=root / "worklist.json",
            mark_coverage=root / "coverage.json",
            required_assets=["FX", "FOP"],
            artifact_max_age_sec=900.0,
        )
        report = data_plane.build_report(args)
        assert report["data_plane_ready"] is False
        assert report["assets"]["FX"]["live_decision_input_allowed"] is False
        assert "FOP:source_missing" in report["violations"]
        assert report["guard"]["stale_or_missing_data_policy"] == "fail_closed_no_model_guess"


def test_quant_gate_excludes_legacy_and_wrong_policy_epoch_rows():
    protocol = _strict_oos_protocol()
    scored = [_formal_oos_row(index, protocol=protocol) for index in range(100)]
    for index in range(22):
        legacy = _formal_oos_row(100 + index, protocol=protocol, adjusted_bps=-100.0)
        legacy["evaluation_context"]["partition"] = "legacy_pre_oos"
        scored.append(legacy)
    wrong_epoch = _formal_oos_row(200, protocol=protocol, adjusted_bps=-100.0)
    wrong_epoch["evaluation_context"]["policy_epoch_id"] = "fixture-epoch-old"
    scored.append(wrong_epoch)
    report = release_gate.quant_evidence_report(
        {"scored": scored},
        cost_bps=0.20,
        min_samples=100,
        max_concentration=0.50,
        protocol=protocol,
    )
    assert report["release_quant_edge_ready"] is True
    assert report["formal_oos_directional_sample_count"] == 100
    assert report["checkpoint_evaluation_sample_count"] == 100
    assert report["legacy_or_non_active_directional_rows_excluded"] == 23
    assert report["cost_adjusted_avg_signed_bps"] == 1.70
    assert report["confidence_method"] == "newey_west_hac"
    assert report["confidence_interval_bps"]["lower"] > 0


def test_forward_oos_partition_is_prospective_and_versioned():
    with tempfile.TemporaryDirectory() as td:
        protocol = pathlib.Path(td) / "protocol.json"
        _write(protocol, {
            "protocol_id": "fixture-oos-v1",
            "cutover_utc": "1970-01-01T00:00:00Z",
            "pre_cutover_partition": "legacy_pre_oos",
            "post_cutover_partition": "forward_oos",
        })
        context = signal_writer.build_evaluation_context(
            {"instrument": "EUR.USD"},
            packet={
                "generated_at_utc": "2026-07-10T00:00:00Z",
                "market_states": {
                    "EURUSD": {
                        "session": "london",
                        "market_micro_context": {
                            "tick_path_compression": {"follow_through_hint": "up_follow_through"},
                            "spread_stability": {"classification": "stable"},
                        },
                    }
                },
            },
            ts_ms=int(time.time() * 1000),
            protocol_path=protocol,
            model_version="fixture-model-v1",
            prompt_version="fixture-prompt-v1",
        )
        assert context["partition"] == "forward_oos"
        assert context["partition_assigned_before_outcome"] is True
        assert context["market_session"] == "london"
        assert context["market_regime"] == "up_follow_through/stable"


def test_signal_context_records_frozen_epoch_slot_eligibility_and_cost_model():
    with tempfile.TemporaryDirectory() as td:
        protocol_path = pathlib.Path(td) / "protocol.json"
        protocol = _strict_oos_protocol()
        protocol["cutover_utc"] = "1970-01-01T00:00:00Z"
        protocol["cost_model"] = {"fallback_round_trip_cost_bps": 0.5}
        _write(protocol_path, protocol)
        values = {
            "OPENCLAW_AI_NATIVE_POLICY_EPOCH_ID": "fixture-epoch-1",
            "OPENCLAW_AI_NATIVE_POLICY_STATE_HASH": "fixture-policy-hash",
            "OPENCLAW_AI_NATIVE_CRITIQUE_SNAPSHOT_HASH": "fixture-critique-hash",
            "OPENCLAW_AI_NATIVE_SAMPLING_SLOT_ID": "fixture-slot-1",
            "OPENCLAW_AI_NATIVE_OBSERVATION_ELIGIBLE": "1",
            "OPENCLAW_AI_NATIVE_DATA_PLANE_CONTRACT_HASH": "fixture-contract-hash",
        }
        previous = {key: os.environ.get(key) for key in values}
        os.environ.update(values)
        try:
            context = signal_writer.build_evaluation_context(
                {"instrument": "EUR.USD"},
                packet={
                    "market_states": {
                        "EURUSD": {
                            "session": "london",
                            "spread_bps": 0.25,
                            "market_micro_context": {
                                "tick_path_compression": {"follow_through_hint": "up_follow_through"},
                                "spread_stability": {"classification": "stable"},
                            },
                        }
                    }
                },
                ts_ms=int(time.time() * 1000),
                protocol_path=protocol_path,
                model_version="fixture-model-v1",
                prompt_version="fixture-prompt-v1",
            )
        finally:
            for key, old in previous.items():
                if old is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = old
        assert context["partition"] == "forward_oos"
        assert context["policy_epoch_id"] == "fixture-epoch-1"
        assert context["policy_state_hash"] == "fixture-policy-hash"
        assert context["critique_snapshot_hash"] == "fixture-critique-hash"
        assert context["sampling_slot_id"] == "fixture-slot-1"
        assert context["observation_eligible"] is True
        assert context["data_plane_contract_hash"] == "fixture-contract-hash"
        assert context["entry_spread_bps"] == 0.25
        assert context["cost_model"]["fallback_round_trip_cost_bps"] == 0.5


def test_active_oos_protocol_v3_is_declared_before_cutover_and_strict():
    protocol = json.loads((ROOT / "configs" / "ai-native-oos-protocol-v3.json").read_text(encoding="utf-8"))
    declared = dt.datetime.fromisoformat(protocol["declared_at_utc"].replace("Z", "+00:00"))
    cutover = dt.datetime.fromisoformat(protocol["cutover_utc"].replace("Z", "+00:00"))
    assert protocol["protocol_id"] == "ai-native-forward-oos-v3"
    assert protocol["supersedes_protocol_id"] == "ai-native-forward-oos-v2"
    assert protocol["supersedes_before_first_accepted_formal_sample"] is True
    assert declared < cutover
    assert protocol["observation_sampling"]["cycles_per_sample"] == 1
    assert protocol["observation_sampling"]["cadence_sec"] == 900
    assert protocol["observation_sampling"]["failure_retry_limit"] == 1
    assert protocol["observation_sampling"]["execution_effect"] == "none"
    assert protocol["evaluation_gate"]["sequential_checkpoints"] == [100, 150, 200]
    assert protocol["evaluation_gate"]["confidence_method"] == "newey_west_hac"


def test_quant_gate_uses_only_predeclared_checkpoint_and_rejects_duplicate_slot():
    protocol = _strict_oos_protocol()
    scored = [_formal_oos_row(index, protocol=protocol) for index in range(101)]
    scored[-1]["cost_adjusted_signed_bps"] = -100.0
    report = release_gate.quant_evidence_report(
        {"scored": scored}, 0.20, 100, 0.50, protocol=protocol
    )
    assert report["formal_oos_directional_sample_count"] == 101
    assert report["reached_checkpoint"] == 100
    assert report["checkpoint_evaluation_sample_count"] == 100
    assert report["cost_adjusted_avg_signed_bps"] == 1.70
    assert report["release_quant_edge_ready"] is True

    duplicate = _formal_oos_row(101, protocol=protocol)
    duplicate["evaluation_context"]["sampling_slot_id"] = "fixture-slot-000"
    report = release_gate.quant_evidence_report(
        {"scored": scored[:100] + [duplicate]}, 0.20, 100, 0.50, protocol=protocol
    )
    assert report["duplicate_sampling_slot_row_count"] == 1
    assert "duplicate_sampling_slot_rows_present" in report["blockers"]
    assert report["release_quant_edge_ready"] is False


def test_health_tiering_separates_active_critical_from_historical_stale():
    health = {
        "healthy": False,
        "unhealthy_count": 2,
        "components": [
            {"name": "0dte_bridge_paper", "kind": "heartbeat", "healthy": False, "alerts": ["not_connected"], "age_ms": 1000},
            {"name": "old_diagnostic", "kind": "ledger", "healthy": False, "alerts": ["ledger_stale"], "age_ms": 1000},
        ],
    }
    report = release_gate.health_report(health, retire_after_sec=86400)
    assert report["unhealthy_by_tier"]["active-critical"] == 1
    assert report["unhealthy_by_tier"]["historical"] == 1
    assert report["release_health_ready"] is False


def test_shadow_resilience_requires_all_fresh_receipts():
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        for drill in shadow_resilience.DRILLS:
            _write(root / "receipts" / f"{drill}.json", {
                "schema": shadow_resilience.RECEIPT_SCHEMA,
                "generated_at_utc": now,
                "drill": drill,
                "passed": True,
                "non_consumable": True,
                "evidence": ["fixture"],
                "guard": {"calls_broker": False, "writes_order_intent": False, "paper_live_enabled": False, "route_leaks": []},
            })
        _write(root / "order.json", _closed_order())
        args = argparse.Namespace(receipt_dir=root / "receipts", order_sentinel=root / "order.json", max_receipt_age_sec=3600.0)
        report = shadow_resilience.build_report(args)
        assert report["shadow_resilience_ready"] is True
        (root / "receipts" / "clock_drift.json").unlink()
        report = shadow_resilience.build_report(args)
        assert report["shadow_resilience_ready"] is False
        assert report["drills"]["clock_drift"] is False


def test_supervisor_order_path_check_is_fail_closed():
    assert supervisor._order_path_safe(_closed_order()) is True
    unsafe = _closed_order()
    unsafe["summary"]["place_sent_count"] = 1
    assert supervisor._order_path_safe(unsafe) is False


def test_supervisor_command_spawn_error_is_structured_fail_closed():
    result = supervisor._run_command(
        ["./fixture-command-that-does-not-exist"],
        cwd=ROOT,
        timeout_sec=1,
        retries=0,
    )
    assert result["ok"] is False
    assert result["attempts"][0]["spawn_failed"] is True
    assert "FileNotFoundError" in result["attempts"][0]["stderr_tail"]


def test_supervisor_fx_eligibility_rechecks_contract_age_now():
    with tempfile.TemporaryDirectory() as td:
        path = pathlib.Path(td) / "contract.json"
        _write(path, {
            "generated_at_utc": (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=10)).isoformat(),
            "artifact_freshness": {"ready": True},
            "assets": {"FX": {"live_decision_input_allowed": True, "source_contract_state": "read_only_mark_source_fresh"}},
        })
        report = supervisor._data_plane_eligibility(
            json.loads(path.read_text(encoding="utf-8")),
            asset="FX",
            contract_path=path,
            max_age_sec=180,
        )
        assert report["eligible"] is False
        assert report["contract_fresh_now"] is False


def test_supervisor_forward_oos_sampling_uses_fixed_slots_eligibility_and_retry():
    protocol = _strict_oos_protocol()
    before = supervisor._oos_sampling_plan(
        protocol,
        {},
        now=dt.datetime(2026, 7, 10, 23, 59, tzinfo=dt.timezone.utc),
        authorized=True,
    )
    assert before["due"] is False
    assert before["reason"] == "prospective_oos_cutover_not_reached"

    first = supervisor._oos_sampling_plan(
        protocol,
        {},
        now=dt.datetime(2026, 7, 11, 0, 0, tzinfo=dt.timezone.utc),
        authorized=True,
        observation_eligible=False,
    )
    assert first["due"] is False
    assert first["reason"] == "oos_observation_asset_not_eligible_fail_closed"

    first = supervisor._oos_sampling_plan(
        protocol,
        {},
        now=dt.datetime(2026, 7, 11, 0, 2, tzinfo=dt.timezone.utc),
        authorized=True,
        observation_eligible=True,
        journal_line_count=10,
    )
    assert first["due"] is True
    assert first["cycles_per_sample"] == 1
    assert first["fixed_slot"] is True
    assert first["slot_start_utc"] == "2026-07-11T00:00:00+00:00"
    assert first["require_reviewable_bucket"] is False
    assert first["execution_effect"] == "none"

    retry_wait = supervisor._oos_sampling_plan(
        protocol,
        {
            "protocol_id": protocol["protocol_id"],
            "slot_id": first["slot_id"],
            "attempt_count": 1,
            "last_attempt_status": "no_signal",
            "last_attempt_at_utc": "2026-07-11T00:02:00Z",
            "journal_line_count_before": 10,
        },
        now=dt.datetime(2026, 7, 11, 0, 6, tzinfo=dt.timezone.utc),
        authorized=True,
        observation_eligible=True,
        journal_line_count=10,
    )
    assert retry_wait["due"] is False
    assert retry_wait["reason"] == "fixed_sampling_slot_retry_delay_not_reached"

    retry = supervisor._oos_sampling_plan(
        protocol,
        {
            "protocol_id": protocol["protocol_id"],
            "slot_id": first["slot_id"],
            "attempt_count": 1,
            "last_attempt_status": "no_signal",
            "last_attempt_at_utc": "2026-07-11T00:02:00Z",
            "journal_line_count_before": 10,
        },
        now=dt.datetime(2026, 7, 11, 0, 7, tzinfo=dt.timezone.utc),
        authorized=True,
        observation_eligible=True,
        journal_line_count=10,
    )
    assert retry["due"] is True
    assert retry["retry_due"] is True

    next_sample = supervisor._oos_sampling_plan(
        protocol,
        {
            "protocol_id": protocol["protocol_id"],
            "slot_id": first["slot_id"],
            "attempt_count": 2,
            "last_attempt_status": "signal_recorded",
            "last_attempt_at_utc": "2026-07-11T00:07:00Z",
            "journal_line_count_before": 10,
        },
        now=dt.datetime(2026, 7, 11, 0, 16, tzinfo=dt.timezone.utc),
        authorized=True,
        observation_eligible=True,
        journal_line_count=11,
    )
    assert next_sample["due"] is True
    assert next_sample["slot_start_utc"] == "2026-07-11T00:15:00+00:00"


def test_supervisor_forward_oos_sampling_fails_closed_on_invalid_protocol():
    report = supervisor._oos_sampling_plan(
        {
            "schema": "openclaw.hepta.agent_trader_ai_native_oos_protocol.v1",
            "protocol_id": "fixture-oos-v1",
            "cutover_utc": "2026-07-11T00:00:00Z",
            "post_cutover_partition": "forward_oos",
            "observation_sampling": {
                "enabled": True,
                "cadence_sec": 600,
                "cycles_per_sample": 3,
                "require_reviewable_bucket": False,
                "failure_retry_limit": 0,
                "failure_retry_delay_sec": 0,
                "execution_effect": "none",
            },
        },
        {},
        now=dt.datetime(2026, 7, 11, 0, 0, tzinfo=dt.timezone.utc),
        authorized=True,
    )
    assert report["due"] is False
    assert report["protocol_valid"] is False
    assert report["reason"] == "oos_sampling_protocol_invalid_fail_closed"


def test_data_plane_refresh_uses_same_closed_order_boundary():
    assert data_plane_refresh._order_safe(_closed_order()) is True
    unsafe = _closed_order()
    unsafe["execution_allowed"] = True
    assert data_plane_refresh._order_safe(unsafe) is False


def test_harness_keeps_paper_request_disabled_with_review_command():
    text = (ROOT / "scripts" / "openclaw_hepta_agent_harness_adapter.py").read_text(encoding="utf-8")
    assert "openclaw_agent_trader_ai_native_paper_request_contract.py" in text
    assert 'disabled_by_default = tool_name == "hepta.request_paper_intent"' in text
    assert '"request_paper_intent_enabled": False' in text


if __name__ == "__main__":
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_") and callable(value)]
    for test in tests:
        test()
    print(f"{len(tests)} tests passed")
