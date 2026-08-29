#!/usr/bin/env python3
"""Append read-only stability checkpoints for the local EURUSD PAPER run."""

from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import pwd
import stat
import subprocess
import sys
import time
from typing import Any


SCHEMA = "hepta.local-paper-supervision.v1"
ACTIVE_POLICY_SCHEMA = "hepta.ib-paper-campaign-policy.v5"
ACTIVE_POLICY_MAX_CYCLES = 720
ACTIVE_POLICY_MAX_DURATION_MS = 24 * 60 * 60 * 1000
POLICY_FILE = Path("/etc/heptatrader/paper-campaigns/alpha.json")
STATE_FILE = Path("/var/lib/hepta-local-ai-paper-agent/state.json")
ACCEPTANCE_FILE = Path(
    "/var/lib/hepta-local-ai-paper-agent/strategy-acceptance-state.json")
CHECKPOINT_FILE = Path(
    "/var/lib/hepta-local-ai-paper-agent/supervision.jsonl")
TOKEN_FILE = Path("/run/hepta-agent-alpha/sessions/local-paper.token")
TOOL_SOCKET = "/run/hepta-agent-alpha/tools.sock"
AGENT_USER = "hepta-agent-alpha"
CORE_UNITS = (
    "hepta-local-ai-paper-agent.service",
    "hepta-execution-ib-paper@alpha.service",
    "hepta-tool-gateway@alpha.service",
    "hepta-broker-egress-policy.service",
    "hepta-local-paper-safe-recover.timer",
    "hepta-local-paper-session-renew.timer",
    "hepta-local-paper-supervisor.timer",
    "hepta-local-ai-paper-24h-stop.timer",
    "hepta-local-ai-paper-end-flat-retry.timer",
)
MODEL_ATTEMPT_MAX_IN_FLIGHT_MS = 135_000
MODEL_ATTEMPT_TERMINAL_UNCERTAIN = "MODEL_ATTEMPT_TERMINAL_UNCERTAIN"
MODEL_TELEMETRY_FIELDS = (
    "model_attempt_count",
    "model_timeout_count",
    "model_contract_failure_count",
    "model_transport_failure_count",
    "model_consecutive_failures",
    "last_model_failure_at_ms",
    "last_model_failure_code",
    "next_model_attempt_after_ms",
    "model_attempt_in_flight",
    "model_attempt_started_at_ms",
    "model_attempt_position",
    "model_attempt_sample_observed_at_ms",
)


def canonical(value: object) -> bytes:
    return (json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
        allow_nan=False) + "\n").encode("ascii")


def read_json(path: Path) -> dict[str, Any]:
    metadata = os.lstat(path)
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise RuntimeError(f"SUPERVISION_INPUT_UNSAFE:{path.name}")
    value = json.loads(path.read_text(encoding="ascii"))
    if not isinstance(value, dict):
        raise RuntimeError(f"SUPERVISION_INPUT_INVALID:{path.name}")
    return value


def unit_active(unit: str) -> bool:
    return subprocess.run(
        ["/usr/bin/systemctl", "--quiet", "is-active", unit],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        timeout=10, check=False).returncode == 0


def unit_restarts(unit: str) -> int | None:
    completed = subprocess.run([
        "/usr/bin/systemctl", "show", unit, "--property=NRestarts",
        "--value", "--no-pager",
    ], text=True, capture_output=True, timeout=10, check=False)
    try:
        return int(completed.stdout.strip()) if completed.returncode == 0 else None
    except ValueError:
        return None


def tool(name: str) -> dict[str, Any]:
    identity = subprocess.run(
        ["/usr/bin/id", "-u", AGENT_USER], text=True, capture_output=True,
        timeout=5, check=True)
    group = subprocess.run(
        ["/usr/bin/id", "-g", AGENT_USER], text=True, capture_output=True,
        timeout=5, check=True)
    completed = subprocess.run([
        "/usr/bin/setpriv", "--reuid=" + identity.stdout.strip(),
        "--regid=" + group.stdout.strip(), "--init-groups",
        "/usr/bin/heptactl",
        "--socket", TOOL_SOCKET, "--token-file", str(TOKEN_FILE),
        "call", name,
    ], text=True, capture_output=True, timeout=15, check=False)
    try:
        envelope = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(
            f"SUPERVISION_TOOL_INVALID:{name}:rc={completed.returncode}:"
            f"stdout={completed.stdout.strip()[:200]}:"
            f"stderr={completed.stderr.strip()[:200]}") from error
    payload = envelope.get("payload") if isinstance(envelope, dict) else None
    if (completed.returncode != 0 or envelope.get("status") != "ok" or
            not isinstance(payload, dict)):
        raise RuntimeError(f"SUPERVISION_TOOL_UNREADY:{name}")
    return payload


def position_quantity(payload: dict[str, Any]) -> float:
    if payload.get("authoritative") is not True:
        raise RuntimeError("SUPERVISION_POSITION_NOT_AUTHORITATIVE")
    for item in payload.get("positions", []):
        if isinstance(item, dict) and item.get("instrument") == "EUR.USD":
            return float(item.get("quantity", 0.0))
    return 0.0


def runtime_binding(
        policy: dict[str, Any], health: dict[str, Any]) -> dict[str, Any]:
    identity = pwd.getpwnam(AGENT_USER)
    metadata = os.lstat(TOKEN_FILE)
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
            metadata.st_uid != identity.pw_uid or
            metadata.st_gid != identity.pw_gid or
            stat.S_IMODE(metadata.st_mode) != 0o600 or
            metadata.st_size != 65):
        raise RuntimeError("SUPERVISION_RUNTIME_TOKEN_INVALID")
    return {
        "campaign_id": policy.get("campaign_id"),
        "execution_service_epoch": health.get("execution_service_epoch"),
        "execution_service_fencing_generation": health.get(
            "execution_service_fencing_generation"),
        "tool_gateway_epoch": health.get("tool_gateway_epoch"),
        "tool_session_token_sha256": (
            "sha256:" + hashlib.sha256(TOKEN_FILE.read_bytes()).hexdigest()),
    }


def acceptance_proven(
        value: dict[str, Any], policy: dict[str, Any],
        current_runtime_binding: dict[str, Any], observed_at_ms: int,
) -> bool:
    trigger = value.get("last_exit_trigger")
    completed_at_ms = value.get("strategy_acceptance_completed_at_ms")
    return (
        value.get("schema") == "hepta.local-ai-paper-agent-state.v3" and
        value.get("entries") == 1 and value.get("exits") == 1 and
        value.get("last_order_result") == "ECONOMIC_FLATTEN_CONFIRMED" and
        value.get("recovery_required") is False and
        value.get("trading_suspended") is False and
        value.get("pending_order_id") is None and
        isinstance(trigger, dict) and trigger.get("trigger") == "MODEL_REVERSAL" and
        trigger.get("result") == "ECONOMIC_FLATTEN_CONFIRMED" and
        float(trigger.get("position_after", -1.0)) == 0.0 and
        value.get("strategy_acceptance_campaign_id") ==
            policy.get("campaign_id") and
        value.get("strategy_acceptance_runtime_binding") ==
            current_runtime_binding and
        isinstance(completed_at_ms, int) and
        not isinstance(completed_at_ms, bool) and
        isinstance(policy.get("valid_after_ms"), int) and
        not isinstance(policy.get("valid_after_ms"), bool) and
        policy.get("valid_after_ms") <= completed_at_ms <= observed_at_ms and
        isinstance(value.get("strategy_acceptance_position_generation"), int) and
        not isinstance(
            value.get("strategy_acceptance_position_generation"), bool) and
        isinstance(value.get("strategy_acceptance_fx_cash_generation"), int) and
        not isinstance(
            value.get("strategy_acceptance_fx_cash_generation"), bool) and
        value.get("strategy_acceptance_gross_absolute_position") == 0 and
        value.get("strategy_acceptance_paper_only") is True and
        value.get("strategy_acceptance_live_authorized") is False)


def model_telemetry(state: dict[str, Any]) -> dict[str, Any]:
    """Project durable model-attempt evidence into every checkpoint."""
    return {key: state.get(key) for key in MODEL_TELEMETRY_FIELDS}


def classify_model_telemetry(
        state: dict[str, Any], observed_at_ms: int) -> list[str]:
    reasons: list[str] = []
    count_fields = (
        "model_attempt_count",
        "model_timeout_count",
        "model_contract_failure_count",
        "model_transport_failure_count",
        "model_consecutive_failures",
        "next_model_attempt_after_ms",
    )
    if any(
            not isinstance(state.get(key), int) or
            isinstance(state.get(key), bool) or state.get(key, -1) < 0
            for key in count_fields):
        reasons.append("MODEL_TELEMETRY_INVALID")

    last_failure_at_ms = state.get("last_model_failure_at_ms")
    last_failure_code = state.get("last_model_failure_code")
    failure_time_valid = (
        last_failure_at_ms is None or
        (isinstance(last_failure_at_ms, int) and
         not isinstance(last_failure_at_ms, bool) and
         0 < last_failure_at_ms <= observed_at_ms))
    failure_code_valid = (
        last_failure_code is None or
        (isinstance(last_failure_code, str) and
         0 < len(last_failure_code) <= 128))
    if (not failure_time_valid or not failure_code_valid or
            ((last_failure_at_ms is None) != (last_failure_code is None)) or
            (isinstance(state.get("model_consecutive_failures"), int) and
             not isinstance(state.get("model_consecutive_failures"), bool) and
             state.get("model_consecutive_failures", 0) > 0 and
             (last_failure_at_ms is None or last_failure_code is None))):
        if "MODEL_TELEMETRY_INVALID" not in reasons:
            reasons.append("MODEL_TELEMETRY_INVALID")

    in_flight = state.get("model_attempt_in_flight")
    started_at_ms = state.get("model_attempt_started_at_ms")
    if not isinstance(in_flight, bool):
        if "MODEL_TELEMETRY_INVALID" not in reasons:
            reasons.append("MODEL_TELEMETRY_INVALID")
    elif in_flight:
        if (not isinstance(started_at_ms, int) or
                isinstance(started_at_ms, bool) or started_at_ms <= 0 or
                started_at_ms > observed_at_ms):
            if "MODEL_TELEMETRY_INVALID" not in reasons:
                reasons.append("MODEL_TELEMETRY_INVALID")
        elif observed_at_ms - started_at_ms > MODEL_ATTEMPT_MAX_IN_FLIGHT_MS:
            reasons.append("MODEL_ATTEMPT_STALE")
    elif started_at_ms is not None:
        if "MODEL_TELEMETRY_INVALID" not in reasons:
            reasons.append("MODEL_TELEMETRY_INVALID")

    if (last_failure_code == MODEL_ATTEMPT_TERMINAL_UNCERTAIN or
            state.get("suspension_code") ==
            MODEL_ATTEMPT_TERMINAL_UNCERTAIN):
        reasons.append("MODEL_ATTEMPT_TERMINAL_UNCERTAIN")
    return reasons


def classify(record: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    policy = record.get("policy", {})
    state = record.get("agent_state", {})
    broker = record.get("broker", {})
    units = record.get("units", {})
    runtime = record.get("runtime_binding", {})
    now_ms = int(record.get("observed_at_ms", 0))
    if not all(units.get(unit) is True for unit in CORE_UNITS):
        reasons.append("CORE_UNIT_INACTIVE")
    if (policy.get("schema") != ACTIVE_POLICY_SCHEMA or
            policy.get("version") != 5 or
            policy.get("paper_only") is not True or
            policy.get("live_authorized") is not False or
            policy.get("admission_mode") != "local-only" or
            policy.get("order_type") != "MKT" or
            policy.get("tif") != "DAY" or
            policy.get("max_quantity") != 25_000 or
            policy.get("max_active_orders") != 1 or
            policy.get("end_flat_required") is not True or
            policy.get("enabled") is not True or
            policy.get("mutations_authorized") is not True or
            not isinstance(policy.get("max_cycles"), int) or
            isinstance(policy.get("max_cycles"), bool) or
            not 2 <= policy.get("max_cycles", 0) <=
                ACTIVE_POLICY_MAX_CYCLES or
            not isinstance(policy.get("valid_after_ms"), int) or
            isinstance(policy.get("valid_after_ms"), bool) or
            policy.get("valid_after_ms", now_ms + 1) > now_ms or
            not isinstance(policy.get("expires_at_ms"), int) or
            isinstance(policy.get("expires_at_ms"), bool) or
            policy.get("expires_at_ms", 0) <= now_ms or
            policy.get("expires_at_ms", 0) -
                policy.get("valid_after_ms", 0) >
                ACTIVE_POLICY_MAX_DURATION_MS):
        reasons.append("POLICY_BOUNDARY_INVALID")
    if (state.get("recovery_required") is not False or
            state.get("trading_suspended") is not False):
        reasons.append("AGENT_SAFETY_LATCHED")
    if state.get("last_error") not in {None, ""}:
        reasons.append("AGENT_LAST_ERROR")
    reasons.extend(classify_model_telemetry(state, now_ms))
    if (not isinstance(state.get("runtime_binding"), dict) or
            state.get("runtime_binding") != runtime):
        reasons.append("RUNTIME_BINDING_INVALID")
    pending_order_id = state.get("pending_order_id")
    pending_since_ms = state.get("pending_order_since_ms")
    if pending_order_id is not None and (
            not isinstance(pending_since_ms, int) or
            now_ms - pending_since_ms > 30_000):
        reasons.append("ORDER_SETTLEMENT_STALE")
    active_order_ids = broker.get("active_order_ids")
    if not isinstance(active_order_ids, list) or len(active_order_ids) > 1:
        reasons.append("ACTIVE_ORDER_BOUNDARY_INVALID")
    try:
        position = float(broker.get("position"))
        gross = float(broker.get("gross_absolute_position"))
    except (TypeError, ValueError):
        reasons.append("BROKER_RISK_INVALID")
    else:
        if (not math.isfinite(position) or not math.isfinite(gross) or
                abs(position) > 25_000 or gross < 0 or gross > 25_000):
            reasons.append("BROKER_RISK_BOUNDARY_EXCEEDED")
    if record.get("strategy_acceptance_proven") is not True:
        reasons.append("STRATEGY_ACCEPTANCE_MISSING")
    return reasons


def append_checkpoint(record: dict[str, Any]) -> None:
    descriptor = os.open(
        CHECKPOINT_FILE,
        os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_CLOEXEC |
        getattr(os, "O_NOFOLLOW", 0),
        0o600)
    try:
        metadata = os.fstat(descriptor)
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
                metadata.st_uid != 0 or metadata.st_gid != 0 or
                stat.S_IMODE(metadata.st_mode) != 0o600):
            raise RuntimeError("SUPERVISION_OUTPUT_UNSAFE")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        os.write(descriptor, canonical(record))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def main() -> int:
    if os.geteuid() != 0:
        raise RuntimeError("SUPERVISION_ROOT_REQUIRED")
    observed_at_ms = time.time_ns() // 1_000_000
    policy = read_json(POLICY_FILE)
    state = read_json(STATE_FILE)
    acceptance = read_json(ACCEPTANCE_FILE)
    positions = tool("portfolio.list_positions")
    orders = tool("orders.list")
    risk = tool("risk.get_limits")
    health = tool("system.get_health")
    recent_fills = [
        item for item in orders.get("recent_orders", [])
        if isinstance(item, dict) and item.get("terminal") is True and
        item.get("status") == "Filled" and item.get("economic_fill") is True
    ]
    observed_runtime_binding = runtime_binding(policy, health)
    record: dict[str, Any] = {
        "schema": SCHEMA,
        "observed_at_ms": observed_at_ms,
        "policy": policy,
        "agent_state": {
            key: state.get(key) for key in (
                "position", "entries", "exits", "last_decision", "last_error",
                "last_order_result", "pending_order_id",
                "pending_order_since_ms", "recovery_required",
                "trading_suspended", "suspension_code", "runtime_binding",
                "updated_at", *MODEL_TELEMETRY_FIELDS)
        },
        "units": {unit: unit_active(unit) for unit in CORE_UNITS},
        "agent_restarts": unit_restarts(CORE_UNITS[0]),
        "broker": {
            "source": orders.get("source"),
            "authoritative": orders.get("authoritative"),
            "position": position_quantity(positions),
            "position_generation": positions.get("position_generation"),
            "fx_cash_generation": positions.get("fx_cash_generation"),
            "active_order_ids": orders.get("active_order_ids"),
            "recent_fills": recent_fills,
            "gross_absolute_position": risk.get("gross_absolute_position"),
        },
        "runtime_binding": observed_runtime_binding,
        "strategy_acceptance_proven": acceptance_proven(
            acceptance, policy, observed_runtime_binding, observed_at_ms),
    }
    reasons = classify(record)
    record["healthy"] = not reasons
    record["reasons"] = reasons
    append_checkpoint(record)
    print(
        "PAPER_SUPERVISION_CHECKPOINT "
        f"healthy={str(not reasons).lower()} reasons={','.join(reasons) or 'none'} "
        f"position={record['broker']['position']} "
        f"active_orders={len(record['broker']['active_order_ids'] or [])} "
        f"recent_fills={len(recent_fills)} "
        f"model_attempts={record['agent_state']['model_attempt_count']} "
        f"model_failures="
        f"{record['agent_state']['model_consecutive_failures']}",
        flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, subprocess.CalledProcessError,
            subprocess.TimeoutExpired,
            json.JSONDecodeError, ValueError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
