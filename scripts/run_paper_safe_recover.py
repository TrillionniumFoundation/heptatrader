#!/usr/bin/env python3
"""Fail-closed recovery for the finite local EURUSD PAPER campaign."""

from __future__ import annotations

import json
import hashlib
import fcntl
import os
from pathlib import Path
import pwd
import secrets
import stat
import subprocess
import sys
import time


DOMAIN = "alpha"
AGENT_USER = "hepta-agent-alpha"
STOP_TIMER = "hepta-local-ai-paper-24h-stop.timer"
AGENT_SERVICE = "hepta-local-ai-paper-agent.service"
EXECUTION_SERVICE = "hepta-execution-ib-paper@alpha.service"
GATEWAY_SERVICE = "hepta-tool-gateway@alpha.service"
CAMPAIGN_SOCKET = "hepta-ib-paper-campaign-operator@alpha.socket"
CONTROL = "/usr/libexec/hepta-local-paper-control"
SESSIONCTL = "/usr/bin/hepta-sessionctl"
HEPTACTL = "/usr/bin/heptactl"
SUPERVISOR_SOCKET = "/run/hepta-tool-gateway-alpha/session-supervisor.sock"
TOOL_SOCKET = "/run/hepta-agent-alpha/tools.sock"
TOKEN_FILE = Path("/run/hepta-agent-alpha/sessions/local-paper.token")
SESSION_LEASE_FILE = TOKEN_FILE.with_name(TOKEN_FILE.name + ".lease.json")
EXECUTION_ENV = Path("/etc/heptatrader/trust-domains/alpha.ib-paper.env")
GATEWAY_ENV = Path("/etc/heptatrader/trust-domains/alpha.env")
AGENT_ENV = Path("/etc/heptatrader/local-ai-paper-agent.env")
AGENT_STATE = Path("/var/lib/hepta-local-ai-paper-agent/state.json")
SAFE_RECOVERY_LOCK = AGENT_STATE.parent / "safe-recover.lock"
RUNTIME_INCIDENT_ROOT = AGENT_STATE.parent
POLICY_FILE = Path("/etc/heptatrader/paper-campaigns/alpha.json")
STRATEGY_FILE = Path(
    "/usr/share/heptatrader/hepta-local-ai-paper-strategy-v3.json")
ACTIVE_POLICY_SCHEMA = "hepta.ib-paper-campaign-policy.v5"
ACTIVE_POLICY_MAX_CYCLES = 720
ACTIVE_POLICY_MAX_DURATION_MS = 24 * 60 * 60 * 1000
# The execution daemon's bounded broker reconnect is 180 seconds.  Recovery
# must not declare the exact same runtime dead while that state machine still
# owns the fail-closed mutation gate.
RUNTIME_RECONNECT_GRACE_SECONDS = 210.0
RUNTIME_RECONNECT_RETRY_SECONDS = 0.5


class Deferred(RuntimeError):
    """A safe retryable recovery outcome."""


class RuntimeBindingChanged(Deferred):
    """The explicitly rearmed runtime tuple no longer names this stack."""

    def __init__(
            self, reason: str, *,
            expected: dict[str, object] | None = None,
            observed: dict[str, object] | None = None) -> None:
        super().__init__(reason)
        self.expected = expected
        self.observed = observed


def run(command: list[str], *, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command, text=True, capture_output=True, timeout=timeout, check=False)


def active(unit: str) -> bool:
    return run(["/usr/bin/systemctl", "--quiet", "is-active", unit], timeout=10).returncode == 0


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value
    return values


def external_p1_finalized() -> bool:
    """Keep the legacy automatic restarter completely out of external P1."""
    try:
        metadata = os.lstat(POLICY_FILE)
    except FileNotFoundError:
        return False
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
            metadata.st_uid != 0 or metadata.st_gid != 0 or
            stat.S_IMODE(metadata.st_mode) & 0o022):
        raise Deferred("EXTERNAL_P1_POLICY_PATH_UNSAFE")
    try:
        value = json.loads(POLICY_FILE.read_text(encoding="ascii"))
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
        raise Deferred("EXTERNAL_P1_POLICY_INVALID") from error
    if not isinstance(value, dict) or value.get(
            "admission_mode") != "external-p1-finalized":
        return False
    if (value.get("schema") != ACTIVE_POLICY_SCHEMA or
            value.get("version") != 5 or value.get("domain_id") != DOMAIN or
            value.get("paper_only") is not True or
            value.get("live_authorized") is not False or
            value.get("order_type") != "LMT" or value.get("tif") != "DAY" or
            value.get("max_cycles") != 1 or
            value.get("max_quantity") != 1 or
            value.get("max_active_orders") != 1):
        raise Deferred("EXTERNAL_P1_POLICY_INVALID")
    return True


def read_agent_state() -> dict[str, object]:
    """Read the root-owned state used by every automatic recovery gate."""
    try:
        metadata = os.lstat(AGENT_STATE)
    except FileNotFoundError:
        raise Deferred("AGENT_STATE_MISSING")
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
            metadata.st_uid != 0 or metadata.st_gid != 0):
        raise Deferred("AGENT_STATE_PATH_UNSAFE")
    try:
        state = json.loads(AGENT_STATE.read_text(encoding="ascii"))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise Deferred("AGENT_STATE_UNREADABLE") from error
    if not isinstance(state, dict):
        raise Deferred("AGENT_STATE_INVALID")
    return state


def _canonical_json(value: object) -> bytes:
    return (json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
        allow_nan=False) + "\n").encode("ascii")


def _atomic_root_json(path: Path, value: object, mode: int = 0o600) -> bytes:
    """Publish one root-owned regular JSON file without following links."""
    payload = _canonical_json(value)
    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC |
        getattr(os, "O_NOFOLLOW", 0),
        mode)
    try:
        os.fchmod(descriptor, mode)
        os.fchown(descriptor, 0, 0)
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise Deferred("RUNTIME_INCIDENT_WRITE_FAILED")
            offset += written
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    try:
        os.replace(temporary, path)
        directory = os.open(
            path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
    return payload


def _validated_existing_state() -> tuple[dict[str, object], int]:
    try:
        metadata = os.lstat(AGENT_STATE)
    except FileNotFoundError:
        return {"schema": "hepta.local-ai-paper-agent-state.v3"}, 0o600
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
            metadata.st_uid != 0 or metadata.st_gid != 0 or
            stat.S_IMODE(metadata.st_mode) != 0o600):
        raise Deferred("AGENT_STATE_PATH_UNSAFE")
    try:
        state = json.loads(AGENT_STATE.read_text(encoding="ascii"))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise Deferred("AGENT_STATE_UNREADABLE") from error
    if not isinstance(state, dict):
        raise Deferred("AGENT_STATE_INVALID")
    return state, stat.S_IMODE(metadata.st_mode)


def persist_runtime_incident(
        reason: str, *, expected: dict[str, object] | None = None,
        observed: dict[str, object] | None = None) -> str:
    """Latch one epoch/dependency incident and bind its exact safe evidence.

    This never grants authority or resumes the old campaign.  The old Tool
    session remains intact until risk recovery has reconciled any owned order
    and exposure; only then may the repair path revoke it exactly.
    """
    state, mode = _validated_existing_state()
    values = read_env(AGENT_ENV)
    campaign_id = values.get("HEPTA_LOCAL_AI_CAMPAIGN_ID", "")
    auth_generation = values.get("HEPTA_LOCAL_AI_AUTH_GENERATION", "")
    if not campaign_id or not auth_generation:
        raise Deferred("RUNTIME_INCIDENT_BINDING_UNAVAILABLE")
    already_latched = (
        state.get("trading_suspended") is True or
        state.get("recovery_required") is True)
    suspension_id = state.get("suspension_id")
    if not isinstance(suspension_id, str) or not suspension_id:
        suspension_id = "suspension-" + secrets.token_hex(16)
    expected_binding = expected
    if expected_binding is None and isinstance(state.get("runtime_binding"), dict):
        expected_binding = dict(state["runtime_binding"])
    evidence = {
        "schema": "hepta.local-ai-paper-runtime-incident.v1",
        "suspension_id": suspension_id,
        "campaign_id": campaign_id,
        "reason_code": reason,
        "recorded_at_ms": time.time_ns() // 1_000_000,
        "expected_runtime_binding": expected_binding,
        "observed_runtime_binding": observed,
        "automatic_resume": False,
        "fresh_campaign_required": True,
        "paper_only": True,
        "live_authorized": False,
    }
    digest = hashlib.sha256(suspension_id.encode("utf-8")).hexdigest()[:24]
    incident_path = RUNTIME_INCIDENT_ROOT / (
        "runtime-incident-" + digest + ".json")
    if incident_path.exists() or incident_path.is_symlink():
        metadata = os.lstat(incident_path)
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
                metadata.st_uid != 0 or metadata.st_gid != 0 or
                stat.S_IMODE(metadata.st_mode) != 0o600):
            raise Deferred("RUNTIME_INCIDENT_PATH_UNSAFE")
        raw = incident_path.read_bytes()
        try:
            prior = json.loads(raw)
        except json.JSONDecodeError as error:
            raise Deferred("RUNTIME_INCIDENT_INVALID") from error
        immutable_fields = (
            "schema", "suspension_id", "campaign_id", "reason_code",
            "expected_runtime_binding", "observed_runtime_binding",
            "automatic_resume", "fresh_campaign_required", "paper_only",
            "live_authorized")
        if any(prior.get(key) != evidence.get(key) for key in immutable_fields):
            raise Deferred("RUNTIME_INCIDENT_CONFLICT")
    else:
        raw = _atomic_root_json(incident_path, evidence)
    evidence_sha256 = "sha256:" + hashlib.sha256(raw).hexdigest()
    if not already_latched:
        state["trading_suspended"] = True
        state["recovery_required"] = True
        state["recovery_complete"] = False
        state["recovery_phase"] = "REQUESTED"
        state["recovery_halt_confirmed"] = False
        state["suspension_code"] = "ORDER_STATE_UNCERTAIN"
        state["suspension_id"] = suspension_id
        state["suspended_at_ms"] = time.time_ns() // 1_000_000
        state["auth_generation_at_suspend"] = auth_generation
        state["campaign_id_at_suspend"] = campaign_id
        if state.get("pending_order_id") is not None:
            state["incident_pending_order_id"] = state.get("pending_order_id")
    state["recovery_reason"] = (
        "RECOVERY_REQUIRED: " + reason +
        "; automatic same-campaign resume forbidden; evidence_sha256=" +
        evidence_sha256)
    _atomic_root_json(AGENT_STATE, state, mode)
    return evidence_sha256


class _SingleFlight:
    def __init__(self) -> None:
        self.descriptor: int | None = None

    def __enter__(self) -> bool:
        descriptor = os.open(
            SAFE_RECOVERY_LOCK,
            os.O_RDWR | os.O_CREAT | os.O_CLOEXEC |
            getattr(os, "O_NOFOLLOW", 0),
            0o600)
        metadata = os.fstat(descriptor)
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
                metadata.st_uid != 0 or metadata.st_gid != 0 or
                stat.S_IMODE(metadata.st_mode) != 0o600):
            os.close(descriptor)
            raise Deferred("SAFE_RECOVERY_LOCK_UNSAFE")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(descriptor)
            return False
        self.descriptor = descriptor
        return True

    def __exit__(self, *_unused: object) -> None:
        if self.descriptor is None:
            return
        try:
            fcntl.flock(self.descriptor, fcntl.LOCK_UN)
        finally:
            os.close(self.descriptor)
            self.descriptor = None


def single_flight() -> _SingleFlight:
    return _SingleFlight()


def require_no_manual_rearm_start() -> None:
    """Recurring recovery must not consume explicit post-rearm start auth."""
    try:
        state = read_agent_state()
    except Deferred as error:
        if str(error) == "AGENT_STATE_MISSING":
            return
        raise
    if state.get("manual_start_required") is True:
        raise Deferred("AUTH_REARM_MANUAL_START_REQUIRED")


def runtime_binding(
        state: dict[str, object], *, reconnect_grace: bool = False
        ) -> dict[str, object]:
    """Require the current stack to match the explicitly rearmed tuple."""
    expected = state.get("runtime_binding")
    if not isinstance(expected, dict):
        raise RuntimeBindingChanged("RUNTIME_BINDING_REQUIRED")
    expected_campaign_id = expected.get("campaign_id")
    expected_execution_epoch = expected.get("execution_service_epoch")
    expected_execution_fence = expected.get(
        "execution_service_fencing_generation")
    expected_gateway_epoch = expected.get("tool_gateway_epoch")
    expected_token_sha256 = expected.get("tool_session_token_sha256")
    if (not isinstance(expected_campaign_id, str) or
            not expected_campaign_id or
            not isinstance(expected_execution_epoch, str) or
            not expected_execution_epoch or
            not isinstance(expected_execution_fence, int) or
            isinstance(expected_execution_fence, bool) or
            expected_execution_fence < 1 or
            not isinstance(expected_gateway_epoch, str) or
            not expected_gateway_epoch or
            not isinstance(expected_token_sha256, str) or
            not expected_token_sha256):
        raise RuntimeBindingChanged(
            "RUNTIME_BINDING_REQUIRED", expected=expected)
    deadline = time.monotonic() + (
        RUNTIME_RECONNECT_GRACE_SECONDS if reconnect_grace else 0.0)
    last_observed: dict[str, object] | None = None
    while True:
        try:
            agent = read_env(AGENT_ENV)
            policy = json.loads(POLICY_FILE.read_text(encoding="ascii"))
            identity = pwd.getpwnam(AGENT_USER)
            token_safe = token_metadata_safe(identity.pw_uid, identity.pw_gid)
        except (Deferred, FileNotFoundError, KeyError, OSError, ValueError,
                json.JSONDecodeError) as error:
            raise RuntimeBindingChanged(
                "RUNTIME_BINDING_IDENTITY_UNAVAILABLE", expected=expected,
                observed={
                    "runtime_identity_unavailable": True,
                    "detail": str(error),
                }) from error
        if not isinstance(policy, dict):
            raise RuntimeBindingChanged(
                "RUNTIME_BINDING_IDENTITY_UNAVAILABLE", expected=expected,
                observed={"campaign_policy_invalid": True})
        campaign_id = agent.get("HEPTA_LOCAL_AI_CAMPAIGN_ID")
        policy_campaign_id = policy.get("campaign_id")
        pre_health_observed = {
            "campaign_id": campaign_id,
            "policy_campaign_id": policy_campaign_id,
            "tool_session_token_sha256": None,
            "tool_session_token_metadata_safe": token_safe,
        }
        if (not isinstance(campaign_id, str) or not campaign_id or
                not isinstance(policy_campaign_id, str) or
                not policy_campaign_id):
            raise RuntimeBindingChanged(
                "RUNTIME_BINDING_IDENTITY_UNAVAILABLE", expected=expected,
                observed=pre_health_observed)
        if (campaign_id != expected_campaign_id or
                policy_campaign_id != expected_campaign_id):
            raise RuntimeBindingChanged(
                "RUNTIME_BINDING_CHANGED", expected=expected,
                observed=pre_health_observed)
        if not token_safe:
            raise RuntimeBindingChanged(
                "RUNTIME_BINDING_SESSION_UNAVAILABLE", expected=expected,
                observed=pre_health_observed)
        try:
            token_sha256 = (
                "sha256:" + hashlib.sha256(
                    TOKEN_FILE.read_bytes()).hexdigest())
        except OSError as error:
            raise RuntimeBindingChanged(
                "RUNTIME_BINDING_SESSION_UNAVAILABLE", expected=expected,
                observed=pre_health_observed) from error
        pre_health_observed["tool_session_token_sha256"] = token_sha256
        if token_sha256 != expected_token_sha256:
            raise RuntimeBindingChanged(
                "RUNTIME_BINDING_CHANGED", expected=expected,
                observed=pre_health_observed)
        try:
            health = call_tool("system.get_health")
        except (Deferred, OSError, subprocess.TimeoutExpired, ValueError,
                json.JSONDecodeError) as error:
            last_observed = {
                "runtime_readiness_unavailable": True,
                "detail": str(error),
            }
        else:
            execution_epoch = health.get("execution_service_epoch")
            execution_fence = health.get(
                "execution_service_fencing_generation")
            gateway_epoch = health.get("tool_gateway_epoch")
            observed = {
                "campaign_id": campaign_id,
                "policy_campaign_id": policy_campaign_id,
                "execution_service_epoch": execution_epoch,
                "execution_service_fencing_generation": execution_fence,
                "tool_gateway_epoch": gateway_epoch,
                "tool_session_token_sha256": token_sha256,
                "tool_session_token_metadata_safe": token_safe,
                "execution_mode": health.get("execution_mode"),
                "gateway_ready": health.get("gateway_ready"),
                "remote_execution_ready": health.get(
                    "remote_execution_ready"),
            }
            last_observed = observed
            # A populated identity component is authoritative evidence.  A
            # different value must halt immediately even while readiness is
            # false; only empty sentinels ("", 0, null) are unavailable.
            explicit_values = {
                "campaign_id": campaign_id,
                "execution_service_epoch": execution_epoch,
                "execution_service_fencing_generation": execution_fence,
                "tool_gateway_epoch": gateway_epoch,
                "tool_session_token_sha256": token_sha256,
            }
            drifted = False
            for key, value in explicit_values.items():
                available = (
                    isinstance(value, int) and
                    not isinstance(value, bool) and value > 0
                    if key == "execution_service_fencing_generation"
                    else isinstance(value, str) and bool(value))
                if available and value != expected.get(key):
                    drifted = True
                    break
            mode = health.get("execution_mode")
            if (isinstance(policy_campaign_id, str) and
                    bool(policy_campaign_id) and
                    policy_campaign_id != expected.get("campaign_id")):
                drifted = True
            if isinstance(mode, str) and bool(mode) and mode != "PAPER":
                drifted = True
            if drifted:
                raise RuntimeBindingChanged(
                    "RUNTIME_BINDING_CHANGED", expected=expected,
                    observed=observed)
            identity_complete = (
                campaign_id == expected.get("campaign_id") and
                policy_campaign_id == expected.get("campaign_id") and
                execution_epoch == expected.get("execution_service_epoch") and
                execution_fence == expected.get(
                    "execution_service_fencing_generation") and
                gateway_epoch == expected.get("tool_gateway_epoch") and
                token_sha256 == expected.get("tool_session_token_sha256") and
                health.get("gateway_ready") is True and mode == "PAPER")
            if (identity_complete and
                    health.get("remote_execution_ready") is True):
                return {
                    key: explicit_values[key]
                    for key in (
                        "campaign_id", "execution_service_epoch",
                        "execution_service_fencing_generation",
                        "tool_gateway_epoch", "tool_session_token_sha256")
                }
        if not reconnect_grace or time.monotonic() >= deadline:
            raise RuntimeBindingChanged(
                "RUNTIME_BINDING_RECONNECT_TIMEOUT", expected=expected,
                observed=last_observed)
        remaining = deadline - time.monotonic()
        time.sleep(min(RUNTIME_RECONNECT_RETRY_SECONDS, max(0.0, remaining)))


def verify_static_boundary(now_ms: int) -> int:
    execution = read_env(EXECUTION_ENV)
    gateway = read_env(GATEWAY_ENV)
    agent = read_env(AGENT_ENV)
    policy = json.loads(POLICY_FILE.read_text(encoding="ascii"))
    strategy_sha256 = "sha256:" + hashlib.sha256(
        STRATEGY_FILE.read_bytes()).hexdigest()
    account = execution.get("HEPTA_IB_PAPER_ACCOUNT", "")
    required = (
        execution.get("HEPTA_IB_EXECUTION_MODE") == "PAPER",
        execution.get("HEPTA_IB_PAPER_PORT") == "4002",
        account.startswith("DU"),
        gateway.get("HEPTA_TOOL_ACCOUNT") == account,
        execution.get("HEPTA_IB_PAPER_MAX_ORDER_QTY") == "25000",
        execution.get("HEPTA_IB_PAPER_MAX_ORDER_NOTIONAL") == "35000",
        execution.get("HEPTA_IB_PAPER_MAX_GROSS_POSITION") == "25000",
        gateway.get("HEPTA_TOOL_MAX_ORDER_QTY") == "25000",
        policy.get("paper_only") is True,
        policy.get("live_authorized") is False,
        policy.get("schema") == ACTIVE_POLICY_SCHEMA,
        policy.get("version") == 5,
        policy.get("admission_mode") == "local-only",
        policy.get("order_type") == "MKT",
        policy.get("tif") == "DAY",
        policy.get("enabled") is True,
        policy.get("mutations_authorized") is True,
        policy.get("max_quantity") == 25000,
        policy.get("max_active_orders") == 1,
        policy.get("end_flat_required") is True,
        type(policy.get("max_cycles")) is int and
            2 <= policy["max_cycles"] <= ACTIVE_POLICY_MAX_CYCLES,
        policy.get("campaign_id") == agent.get("HEPTA_LOCAL_AI_CAMPAIGN_ID"),
        policy.get("strategy_id") == agent.get("HEPTA_LOCAL_AI_STRATEGY_ID"),
        policy.get("strategy_version") ==
            agent.get("HEPTA_LOCAL_AI_STRATEGY_VERSION") == "3",
        policy.get("strategy_sha256") == agent.get("HEPTA_LOCAL_AI_STRATEGY_SHA256"),
        policy.get("strategy_sha256") == strategy_sha256,
    )
    if not all(required):
        raise Deferred("STATIC_PAPER_BOUNDARY_MISMATCH")
    valid_after_ms = policy.get("valid_after_ms")
    expires_at_ms = policy.get("expires_at_ms")
    if (not isinstance(valid_after_ms, int) or
            isinstance(valid_after_ms, bool) or
            valid_after_ms > now_ms or
            not isinstance(expires_at_ms, int) or
            isinstance(expires_at_ms, bool) or
            now_ms >= expires_at_ms or
            expires_at_ms - valid_after_ms >
                ACTIVE_POLICY_MAX_DURATION_MS):
        raise Deferred("CAMPAIGN_EXPIRED")
    return expires_at_ms


def broker_port_ready() -> bool:
    # The broker egress policy intentionally denies root a TCP connection to
    # the PAPER API port; only the dedicated execution identity may connect.
    # Observe the local listener without weakening that identity boundary.
    observed = run([
        "/usr/bin/ss", "-H", "-ltn", "sport", "=", ":4002",
    ], timeout=5)
    return observed.returncode == 0 and ":4002" in observed.stdout


def heptactl(arguments: list[str], *, timeout: int = 15) -> dict[str, object]:
    completed = run([
        "/usr/sbin/runuser", "-u", AGENT_USER, "--", HEPTACTL,
        "--socket", TOOL_SOCKET, "--token-file", str(TOKEN_FILE),
        *arguments,
    ], timeout=timeout)
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise Deferred("TOOL_RESPONSE_INVALID") from error
    if not isinstance(value, dict):
        raise Deferred("TOOL_RESPONSE_INVALID")
    return value


def session_usable() -> bool:
    try:
        response = heptactl(["tools", "list"])
    except (Deferred, subprocess.TimeoutExpired):
        return False
    return response.get("status") == "ok"


def token_metadata_safe(uid: int, gid: int) -> bool:
    try:
        metadata = os.lstat(TOKEN_FILE)
    except FileNotFoundError:
        return False
    return (
        stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1 and
        metadata.st_uid == uid and metadata.st_gid == gid and
        stat.S_IMODE(metadata.st_mode) == 0o600 and metadata.st_size == 65)


def write_session_lease(lease_generation: int, ttl_seconds: int) -> None:
    if (not isinstance(lease_generation, int) or
            isinstance(lease_generation, bool) or lease_generation < 1):
        raise Deferred("SESSION_GENERATION_INVALID")
    observed_at_ms = time.time_ns() // 1_000_000
    payload = {
        "schema": "hepta.local-paper-session-lease.v1",
        "session_name": "local-paper-recovery",
        "lease_generation": lease_generation,
        "ttl_seconds": ttl_seconds,
        "observed_at_ms": observed_at_ms,
        "expires_at_ms": observed_at_ms + ttl_seconds * 1000,
        "token_sha256": (
            "sha256:" + hashlib.sha256(TOKEN_FILE.read_bytes()).hexdigest()),
    }
    temporary = SESSION_LEASE_FILE.with_name(
        f".{SESSION_LEASE_FILE.name}.{os.getpid()}.tmp")
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC |
        getattr(os, "O_NOFOLLOW", 0),
        0o600)
    try:
        os.fchmod(descriptor, 0o600)
        os.fchown(descriptor, 0, 0)
        rendered = (json.dumps(
            payload, ensure_ascii=True, sort_keys=True,
            separators=(",", ":"), allow_nan=False) + "\n").encode("ascii")
        os.write(descriptor, rendered)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(temporary, SESSION_LEASE_FILE)


def provision_session(expires_at_ms: int) -> None:
    identity = pwd.getpwnam(AGENT_USER)
    TOKEN_FILE.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    metadata = os.lstat(TOKEN_FILE) if TOKEN_FILE.exists() else None
    if metadata is not None and (
            not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1):
        raise Deferred("TOKEN_PATH_UNSAFE")
    descriptor = os.open(
        TOKEN_FILE,
        os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_CLOEXEC |
        getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        os.fchmod(descriptor, 0o600)
        os.fchown(descriptor, 0, 0)
        os.write(descriptor, (secrets.token_hex(32) + "\n").encode("ascii"))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    remaining = max(120, min(21600, (expires_at_ms - int(time.time() * 1000)) // 1000 + 120))
    session_id = f"local-paper-recovery-{int(time.time())}"
    completed = run([
        SESSIONCTL, "--socket", SUPERVISOR_SOCKET, "provision",
        "--template", "paper", "--token-file", str(TOKEN_FILE),
        "--agent-id", DOMAIN, "--session-id", session_id,
        "--peer-uid", str(identity.pw_uid), "--ttl-sec", str(remaining),
    ], timeout=20)
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise Deferred("SESSION_PROVISION_INVALID") from error
    if completed.returncode != 0 or result.get("accepted") is not True:
        raise Deferred("SESSION_PROVISION_REJECTED")
    os.chown(TOKEN_FILE, identity.pw_uid, identity.pw_gid)
    os.chmod(TOKEN_FILE, 0o600)
    write_session_lease(result.get("lease_generation"), remaining)
    if not token_metadata_safe(identity.pw_uid, identity.pw_gid) or not session_usable():
        raise Deferred("SESSION_PROVISION_UNCONFIRMED")


def call_tool(name: str, values: list[str] | None = None) -> dict[str, object]:
    response = heptactl(["call", name, *(values or [])])
    payload = response.get("payload")
    if response.get("status") != "ok" or not isinstance(payload, dict):
        raise Deferred(f"{name.upper().replace('.', '_')}_UNREADY")
    return payload


def reconcile_authoritative_state() -> None:
    health = call_tool("system.get_health")
    account = call_tool("account.get_summary")
    positions = call_tool("portfolio.list_positions")
    orders = call_tool("orders.list")
    risk = call_tool("risk.get_limits")
    quote = call_tool("market.get_quote", ["instrument=EUR.USD"])
    checks = (
        health.get("gateway_ready") is True,
        health.get("remote_execution_ready") is True,
        health.get("execution_mode") == "PAPER",
        health.get("paper_template_enabled") is True,
        account.get("source") == "IB",
        account.get("authoritative") is True,
        account.get("account_complete") is True,
        positions.get("source") == "IB",
        positions.get("authoritative") is True,
        positions.get("positions") == [],
        orders.get("source") == "IB",
        orders.get("authoritative") is True,
        orders.get("active_order_ids") == [],
        risk.get("source") == "IB",
        risk.get("authoritative") is True,
        risk.get("max_order_quantity") == 25000,
        risk.get("max_order_notional") == 35000,
        risk.get("max_active_orders") == 1,
        risk.get("max_gross_position") == 25000,
        risk.get("gross_absolute_position") == 0,
        quote.get("source") == "IB",
        quote.get("authoritative") is True,
        quote.get("instrument") == "EUR.USD",
        quote.get("subscription_state") == "active",
        quote.get("stale") is False,
    )
    if not all(checks):
        raise Deferred("AUTHORITATIVE_FLAT_RECONCILIATION_FAILED")


def wait_for_stack() -> None:
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        if active(EXECUTION_SERVICE) and active(GATEWAY_SERVICE) and Path(TOOL_SOCKET).exists():
            return
        time.sleep(2)
    raise Deferred("PAPER_STACK_NOT_READY")


def stop_agent() -> None:
    stopped = run(["/usr/bin/systemctl", "stop", AGENT_SERVICE], timeout=20)
    if stopped.returncode != 0 or active(AGENT_SERVICE):
        raise Deferred("AGENT_STOP_FAILED")


def recover_once() -> int:
    try:
        if external_p1_finalized():
            print(
                "SAFE_RECOVERY_NOOP external_p1=true "
                "legacy_restart_authorized=false",
                flush=True)
            return 0
        now_ms = int(time.time() * 1000)
        expires_at_ms = verify_static_boundary(now_ms)
        require_no_manual_rearm_start()
        if not active(STOP_TIMER):
            raise Deferred("STOP_TIMER_INACTIVE")
        dependencies_ready = active(EXECUTION_SERVICE) and active(GATEWAY_SERVICE)
        if active(AGENT_SERVICE) and dependencies_ready:
            try:
                runtime_binding(read_agent_state(), reconnect_grace=True)
            except RuntimeBindingChanged as error:
                stop_agent()
                persist_runtime_incident(
                    str(error), expected=error.expected,
                    observed=error.observed)
                raise
            print("SAFE_RECOVERY_NOOP agent_active", flush=True)
            return 0
        if active(AGENT_SERVICE):
            stop_agent()
        if not dependencies_ready:
            state = read_agent_state()
            expected = state.get("runtime_binding")
            persist_runtime_incident(
                "RUNTIME_BINDING_DEPENDENCY_LOST",
                expected=(expected if isinstance(expected, dict) else None),
                observed={
                    "campaign_id": read_env(AGENT_ENV).get(
                        "HEPTA_LOCAL_AI_CAMPAIGN_ID"),
                    "execution_service_active": active(EXECUTION_SERVICE),
                    "tool_gateway_active": active(GATEWAY_SERVICE),
                })
            raise Deferred("RUNTIME_BINDING_DEPENDENCY_LOST")
        if not broker_port_ready():
            state = read_agent_state()
            expected = state.get("runtime_binding")
            persist_runtime_incident(
                "IB_PAPER_API_PORT_UNREADY",
                expected=(expected if isinstance(expected, dict) else None),
                observed={
                    "campaign_id": read_env(AGENT_ENV).get(
                        "HEPTA_LOCAL_AI_CAMPAIGN_ID"),
                    "ib_paper_api_port_ready": False,
                })
            raise Deferred("IB_PAPER_API_PORT_UNREADY")
        wait_for_stack()
        identity = pwd.getpwnam(AGENT_USER)
        if not token_metadata_safe(identity.pw_uid, identity.pw_gid) or not session_usable():
            # A new token would be a new Tool Gateway session epoch. Automatic
            # recovery may only reuse the exact session bound at explicit
            # rearm; an operator must rearm after session loss.
            state = read_agent_state()
            expected = state.get("runtime_binding")
            persist_runtime_incident(
                "RUNTIME_BINDING_SESSION_UNAVAILABLE",
                expected=(expected if isinstance(expected, dict) else None),
                observed={
                    "campaign_id": read_env(AGENT_ENV).get(
                        "HEPTA_LOCAL_AI_CAMPAIGN_ID"),
                    "tool_session_usable": False,
                })
            raise Deferred("RUNTIME_BINDING_SESSION_UNAVAILABLE")
        state = read_agent_state()
        try:
            runtime_binding(state)
        except RuntimeBindingChanged as error:
            persist_runtime_incident(
                str(error), expected=error.expected,
                observed=error.observed)
            raise
        try:
            reconcile_authoritative_state()
        except Deferred as error:
            expected = state.get("runtime_binding")
            persist_runtime_incident(
                str(error),
                expected=(expected if isinstance(expected, dict) else None),
                observed={
                    "campaign_id": read_env(AGENT_ENV).get(
                        "HEPTA_LOCAL_AI_CAMPAIGN_ID"),
                    "authoritative_flat_reconciliation": False,
                })
            raise
        # An inactive agent breaks the campaign's continuity. Recurring
        # recovery may verify flatness, but it must never consume the explicit
        # manual-start boundary or silently resume an old campaign.
        expected = state.get("runtime_binding")
        persist_runtime_incident(
            "AGENT_INACTIVE_FRESH_CAMPAIGN_REQUIRED",
            expected=(expected if isinstance(expected, dict) else None),
            observed={
                "campaign_id": read_env(AGENT_ENV).get(
                    "HEPTA_LOCAL_AI_CAMPAIGN_ID"),
                "authoritative_flat_reconciliation": True,
                "agent_active": False,
            })
        raise Deferred("AGENT_INACTIVE_FRESH_CAMPAIGN_REQUIRED")
    except (Deferred, OSError, subprocess.TimeoutExpired, ValueError) as error:
        print(f"SAFE_RECOVERY_DEFERRED {error}", flush=True)
    return 0


def main() -> int:
    try:
        with single_flight() as acquired:
            if not acquired:
                print("SAFE_RECOVERY_NOOP single_flight_busy=true", flush=True)
                return 0
            return recover_once()
    except (Deferred, OSError, subprocess.TimeoutExpired, ValueError) as error:
        print(f"SAFE_RECOVERY_DEFERRED {error}", flush=True)
        return 0


if __name__ == "__main__":
    sys.exit(main())
