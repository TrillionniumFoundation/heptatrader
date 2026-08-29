#!/usr/bin/env python3
"""Run a continuous local-only AI PAPER agent through canonical Hepta tools."""

from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import pwd
import re
import signal
import stat
import subprocess
import threading
import time
import uuid
from typing import Any

SCHEMA = "hepta.local-ai-paper-agent-state.v3"
PREVIOUS_SCHEMA = "hepta.local-ai-paper-agent-state.v2"
LEGACY_SCHEMA = "hepta.local-ai-paper-agent-state.v1"
INSTRUMENT = "EUR.USD"
HISTORY_LIMIT = 120
ORDER_QUANTITY = 25_000
MAX_ADVERSE_MOVE = 0.002
# The campaign operator grants a 20-second mutation window. Leave margin for
# authoritative resolution of the marketable DAY limit entry order.
ORDER_SETTLEMENT_WINDOW_SEC = 12.0
ORDER_CANCEL_WINDOW_SEC = 2.0
CYCLE_FINALIZATION_MARGIN_SEC = 5.0
CAMPAIGN_OPEN_TIMEOUT_SEC = 5.0
CAMPAIGN_FINALIZE_TIMEOUT_SEC = 2.0
IN_CYCLE_TOOL_TIMEOUT_SEC = 4.0
HEPTACTL_DEFAULT_IO_TIMEOUT_MS = 5_000
HEPTACTL_STABLE_IO_TIMEOUT_MS = 15_000
HEPTACTL_STABLE_CALL_MIN_TIMEOUT_SEC = 20.0
AUTH_PROFILE_CHECK_TIMEOUT_SEC = 60
MODEL_ATTEMPT_TIMEOUT_SEC = 120
# A killed CLI is not proof that its already-accepted Gateway turn is gone.
# Reconcile the unique decision session until OpenClaw's durable session store
# reports a terminal state; only then may the next model attempt be scheduled.
MODEL_ATTEMPT_TERMINAL_PROOF_TIMEOUT_SEC = 180
MODEL_ATTEMPT_TERMINAL_PROOF_POLL_SEC = 2.0
MODEL_SESSION_STATUS_TIMEOUT_SEC = 20
MODEL_RESULT_MAX_AGE_MS = 60_000
RECOVERY_HALT_REASON = "LOCAL_AI_ORDER_SETTLEMENT_RECOVERY_REQUIRED"
SAFETY_STOP_EXIT_STATUS = 75
TRADE_TOOL_BUDGET_EXHAUSTED = "TRADE_TOOL_BUDGET_EXHAUSTED"
MODEL_AUTH_RATE_LIMIT = "MODEL_AUTH_RATE_LIMIT"
MODEL_AUTH_UNUSABLE = "MODEL_AUTH_UNUSABLE"
MODEL_AUTH_BILLING = "MODEL_AUTH_BILLING"
MODEL_REQUEST_RATE_LIMIT = "MODEL_REQUEST_RATE_LIMIT"
MODEL_ATTEMPT_TIMEOUT = "MODEL_ATTEMPT_TIMEOUT"
MODEL_ATTEMPT_TRANSPORT = "MODEL_ATTEMPT_TRANSPORT"
MODEL_ATTEMPT_CONTRACT_INVALID = "MODEL_ATTEMPT_CONTRACT_INVALID"
MODEL_ATTEMPT_TERMINAL_UNCERTAIN = "MODEL_ATTEMPT_TERMINAL_UNCERTAIN"
ORDER_STATE_UNCERTAIN = "ORDER_STATE_UNCERTAIN"
RUNTIME_EPOCH_CHANGED = "RUNTIME_EPOCH_CHANGED"
# The execution daemon owns a 180-second bounded in-process broker reconnect.
# Keep the agent blocked (and therefore incapable of market/order calls) for
# slightly longer so the daemon can either restore the exact identity or fail
# closed first.
RUNTIME_BINDING_UNAVAILABLE_GRACE_SECONDS = 210.0
RUNTIME_BINDING_RETRY_SECONDS = 0.25
AUTH_SAFETY_STOP_CODES = frozenset({
    MODEL_AUTH_RATE_LIMIT, MODEL_AUTH_UNUSABLE, MODEL_AUTH_BILLING,
})
TERMINAL_NON_FILL_STATUSES = frozenset({
    "rejected", "inactive", "cancelled", "apicancelled",
})
START_PERMIT_CONSUMED = Path(
    "/var/lib/hepta-local-ai-paper-agent/start-permit.consumed.json")
BROKER_MUTATION_LOCK = Path(
    "/var/lib/hepta-local-ai-paper-agent/broker-mutation.lock")


class _BrokerMutationLock:
    """Serialize one broker mutation through authoritative settlement."""

    def __init__(self) -> None:
        self.descriptor: int | None = None

    def __enter__(self) -> "_BrokerMutationLock":
        descriptor = os.open(
            BROKER_MUTATION_LOCK,
            os.O_RDWR | os.O_CREAT | os.O_CLOEXEC |
            getattr(os, "O_NOFOLLOW", 0),
            0o600)
        try:
            metadata = os.fstat(descriptor)
            if (not stat.S_ISREG(metadata.st_mode) or
                    metadata.st_nlink != 1 or metadata.st_uid != 0 or
                    metadata.st_gid != 0 or
                    stat.S_IMODE(metadata.st_mode) != 0o600):
                raise RuntimeError("BROKER_MUTATION_LOCK_UNSAFE")
            fcntl.flock(descriptor, fcntl.LOCK_EX)
        except BaseException:
            os.close(descriptor)
            raise
        self.descriptor = descriptor
        return self

    def __exit__(self, *_unused: object) -> None:
        descriptor = self.descriptor
        self.descriptor = None
        if descriptor is None:
            return
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _broker_mutation_lock() -> _BrokerMutationLock:
    return _BrokerMutationLock()


class RecoveryRequiredError(RuntimeError):
    """The order result is not safe to resolve without reconciliation."""


class ToolRejectedError(RuntimeError):
    """A typed tool or control command returned a structured rejection."""

    def __init__(self, response: dict[str, Any]) -> None:
        self.response = dict(response)
        self.tool = str(response.get("tool") or "")
        self.reason_code = str(response.get("reason_code") or "")
        self.detail = str(response.get("detail") or "")
        super().__init__(
            f"tool {self.tool or 'unknown'} rejected: "
            f"reason_code={self.reason_code or 'UNKNOWN'} "
            f"detail={self.detail or 'none'}")


class SafetyStopError(RecoveryRequiredError):
    """A fail-closed condition that must terminate the trading loop."""

    def __init__(self, suspension_code: str, detail: str) -> None:
        self.suspension_code = suspension_code
        super().__init__(detail)


class ModelAttemptFailure(RuntimeError):
    """A typed transient failure of one completed model attempt."""

    def __init__(self, failure_code: str, detail: str) -> None:
        self.failure_code = failure_code
        super().__init__(detail)


class ModelAttemptTerminalUncertainError(SafetyStopError):
    """A model subprocess timed out without terminal ownership proof."""

    def __init__(self, detail: str) -> None:
        super().__init__(MODEL_ATTEMPT_TERMINAL_UNCERTAIN, detail)


class ModelRequestRateLimitError(ModelAttemptFailure):
    """A request-scoped model 429 that must back off without liquidating."""

    def __init__(self, detail: str) -> None:
        super().__init__(MODEL_REQUEST_RATE_LIMIT, detail)


class RuntimeBindingUnavailableError(RuntimeError):
    """The Tool Gateway cannot currently prove a complete ready identity."""

    def __init__(
            self, detail: str, *, observed: dict[str, Any] | None = None,
            execution_mode: Any = None) -> None:
        super().__init__(detail)
        self.observed = dict(observed or {})
        self.execution_mode = execution_mode


class ModelAttemptWorker:
    """Own exactly one model attempt while the risk loop keeps sampling."""

    def __init__(
            self, arguments: argparse.Namespace,
            history: list[dict[str, Any]], position: float,
            pnl: dict[str, float]) -> None:
        self.started_at_ms = now_ms()
        self.position = float(position)
        self.history_observed_at_ms = int(history[-1]["observed_at_ms"])
        self._result: dict[str, Any] | None = None
        self._error: BaseException | None = None

        def run() -> None:
            try:
                self._result = model_decision(
                    arguments, list(history), position, dict(pnl))
            except BaseException as error:
                self._error = error

        self._thread = threading.Thread(
            target=run, name="hepta-model-attempt", daemon=True)
        self._thread.start()

    def done(self) -> bool:
        return not self._thread.is_alive()

    def result(self) -> dict[str, Any]:
        self._thread.join()
        if self._error is not None:
            raise self._error
        if self._result is None:
            raise ModelAttemptFailure(
                MODEL_ATTEMPT_TRANSPORT,
                "MODEL_ATTEMPT_TRANSPORT: model worker returned no result")
        return self._result


def now_ms() -> int:
    return int(time.time() * 1000)


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=True, sort_keys=True,
                       separators=(",", ":"), allow_nan=False) + "\n").encode("ascii")


def auth_profile_sha256(profile_id: str) -> str:
    """Return the legacy single-profile provenance binding."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:@+-]{2,255}", profile_id):
        raise ValueError("local AI PAPER auth profile id is invalid")
    return "sha256:" + hashlib.sha256(profile_id.encode("utf-8")).hexdigest()


def auth_profile_allowlist_sha256(profile_ids: list[str]) -> str:
    """Return an order-independent, non-secret binding for an auth allowlist."""
    if (not isinstance(profile_ids, list) or not profile_ids or
            not all(isinstance(item, str) for item in profile_ids) or
            len(profile_ids) != len(set(profile_ids))):
        raise ValueError("local AI PAPER auth profile allowlist is invalid")
    if not all(re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._:@+-]{2,255}", item)
            for item in profile_ids):
        raise ValueError("local AI PAPER auth profile allowlist is invalid")
    normalized = sorted(profile_ids)
    return "sha256:" + hashlib.sha256(canonical(normalized)).hexdigest()


def _verify_effective_auth_profile(arguments: argparse.Namespace) -> None:
    """Require the effective OpenAI routes to remain the rearmed allowlist."""
    try:
        completed = subprocess.run([
            "runuser", "-u", arguments.model_user, "--",
            "openclaw", "models", "auth", "order", "get",
            "--agent", arguments.openclaw_agent,
            "--provider", "openai", "--json",
        ], text=True, capture_output=True,
            timeout=AUTH_PROFILE_CHECK_TIMEOUT_SEC, check=False)
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeError(
            "effective auth profile check temporarily unavailable") from error
    if completed.returncode != 0:
        raise RuntimeError(
            "effective auth profile check temporarily unavailable")
    try:
        document = json.loads(completed.stdout)
        order = document.get("order") if isinstance(document, dict) else None
        structurally_valid = (
            isinstance(document, dict) and
            document.get("agentId") == arguments.openclaw_agent and
            document.get("provider") == "openai" and
            isinstance(order, list))
        if not structurally_valid:
            raise ValueError("effective auth route metadata is invalid")
        effective_allowlist_sha256 = auth_profile_allowlist_sha256(order)
    except (json.JSONDecodeError, TypeError, ValueError) as error:
        raise SafetyStopError(
            MODEL_AUTH_UNUSABLE,
            "MODEL_AUTH_UNUSABLE: effective auth profile response invalid") \
            from error
    if (effective_allowlist_sha256 !=
            arguments.auth_profile_allowlist_sha256):
        raise SafetyStopError(
            MODEL_AUTH_UNUSABLE,
            "MODEL_AUTH_UNUSABLE: effective auth profile allowlist drifted")


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(
        "." + path.name + "." + str(os.getpid()) + "." +
        uuid.uuid4().hex + ".tmp")
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC |
        getattr(os, "O_NOFOLLOW", 0),
        0o600)
    try:
        try:
            os.fchmod(descriptor, 0o600)
            payload = canonical(value)
            offset = 0
            while offset < len(payload):
                written = os.write(descriptor, payload[offset:])
                if written <= 0:
                    raise RuntimeError("AGENT_STATE_WRITE_FAILED")
                offset += written
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temporary, path)
        parent = os.open(
            path.parent,
            os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC |
            getattr(os, "O_NOFOLLOW", 0))
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def write_agent_json(path: Path, value: Any, agent_user: str) -> None:
    identity = pwd.getpwnam(agent_user)
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chown(path.parent, identity.pw_uid, identity.pw_gid)
    os.chmod(path.parent, 0o700)
    write_json(path, value)
    os.chown(path, identity.pw_uid, identity.pw_gid)
    os.chmod(path, 0o400)
    descriptor = os.open(
        path, os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    parent = os.open(
        path.parent,
        os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC |
        getattr(os, "O_NOFOLLOW", 0))
    try:
        os.fsync(parent)
    finally:
        os.close(parent)


def _json_object(value: str) -> dict[str, Any] | None:
    if not value.strip():
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _structured_command_error(
        completed: subprocess.CompletedProcess[str]) -> dict[str, Any] | None:
    for rendered in (completed.stdout, completed.stderr):
        parsed = _json_object(rendered)
        if (parsed is not None and
                isinstance(parsed.get("reason_code"), str) and
                parsed.get("status") in {"rejected", "uncertain", "error"}):
            return parsed
    return None


def _mutation_response(
        error: BaseException, command_id: str) -> dict[str, Any] | None:
    """Recover the exact durable mutation result from an idempotent replay."""
    if not isinstance(error, ToolRejectedError):
        return None
    response = error.response
    if (response.get("status") not in {"duplicate", "ok"} or
            response.get("command_id") not in {None, command_id}):
        return None
    order_id = response.get("order_id")
    if (not isinstance(order_id, int) or isinstance(order_id, bool) or
            order_id < 0):
        return None
    return response


def run_json(command: list[str], timeout: float = 30) -> dict[str, Any]:
    completed = subprocess.run(command, text=True, capture_output=True,
                               timeout=timeout, check=False)
    if completed.returncode != 0:
        structured = _structured_command_error(completed)
        if structured is not None:
            raise ToolRejectedError(structured)
        raise RuntimeError(
            f"command failed rc={completed.returncode}: "
            f"{completed.stderr.strip() or completed.stdout.strip()}")
    value = json.loads(completed.stdout)
    if not isinstance(value, dict):
        raise RuntimeError("command output is not a JSON object")
    return value


def tool_response(arguments: argparse.Namespace, name: str,
                  values: dict[str, Any] | None = None,
                  call_id: str | None = None,
                  timeout: float = 30) -> dict[str, Any]:
    io_timeout_ms = (
        HEPTACTL_STABLE_IO_TIMEOUT_MS
        if timeout >= HEPTACTL_STABLE_CALL_MIN_TIMEOUT_SEC
        else HEPTACTL_DEFAULT_IO_TIMEOUT_MS)
    command = [
        "runuser", "-u", arguments.agent_user, "--",
        arguments.heptactl, "--socket", arguments.tool_socket,
        "--token-file", arguments.token_file,
        "--io-timeout-ms", str(io_timeout_ms),
    ]
    if call_id:
        command += ["--call-id", call_id]
    command += ["call", name]
    for key, value in (values or {}).items():
        if isinstance(value, bool):
            rendered = "true" if value else "false"
        else:
            rendered = str(value)
        command.append(f"{key}={rendered}")
    response = run_json(command, timeout=timeout)
    if response.get("status") != "ok":
        raise ToolRejectedError(response)
    return response


def tool(arguments: argparse.Namespace, name: str,
         values: dict[str, Any] | None = None,
         call_id: str | None = None,
         timeout: float = 30) -> dict[str, Any]:
    response = tool_response(arguments, name, values, call_id, timeout)
    payload = response.get("payload")
    if not isinstance(payload, dict):
        raise RuntimeError(f"tool {name} returned invalid payload: {response}")
    return payload


def _tool_session_token_sha256(arguments: argparse.Namespace) -> str:
    """Return a metadata-validated binding for the current tool session."""
    path = Path(arguments.token_file)
    identity = pwd.getpwnam(arguments.agent_user)
    metadata = os.lstat(path)
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
            metadata.st_uid != identity.pw_uid or
            metadata.st_gid != identity.pw_gid or
            stat.S_IMODE(metadata.st_mode) != 0o600 or
            metadata.st_size != 65):
        raise RuntimeError("tool session token metadata is invalid")
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def current_runtime_binding(
        arguments: argparse.Namespace,
        health: dict[str, Any] | None = None) -> dict[str, Any]:
    """Read the immutable runtime identity tuple for one PAPER campaign."""
    observed = health if health is not None else tool(
        arguments, "system.get_health")
    execution_epoch = observed.get("execution_service_epoch")
    execution_fence = observed.get("execution_service_fencing_generation")
    gateway_epoch = observed.get("tool_gateway_epoch")
    try:
        token_sha256: str | None = _tool_session_token_sha256(arguments)
    except (OSError, KeyError, RuntimeError):
        token_sha256 = None
    binding = {
        "campaign_id": str(arguments.campaign_id),
        "execution_service_epoch": execution_epoch,
        "execution_service_fencing_generation": execution_fence,
        "tool_gateway_epoch": gateway_epoch,
        "tool_session_token_sha256": token_sha256,
    }
    identity_complete = (
        isinstance(execution_epoch, str) and
        re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{7,127}",
                     execution_epoch) is not None and
        isinstance(execution_fence, int) and
        not isinstance(execution_fence, bool) and execution_fence >= 1 and
        isinstance(gateway_epoch, str) and
        re.fullmatch(r"htgw-v1-[0-9a-f]{32}", gateway_epoch) is not None and
        isinstance(token_sha256, str) and
        re.fullmatch(r"sha256:[0-9a-f]{64}", token_sha256) is not None)
    if (observed.get("gateway_ready") is not True or
            observed.get("remote_execution_ready") is not True or
            observed.get("execution_mode") != "PAPER" or
            not identity_complete):
        raise RuntimeBindingUnavailableError(
            "PAPER runtime identity is unavailable",
            observed=binding,
            execution_mode=observed.get("execution_mode"))
    return binding


def _runtime_identity_value_available(key: str, value: Any) -> bool:
    """Distinguish an explicit identity from the gateway's empty sentinel."""
    if key == "execution_service_fencing_generation":
        return (isinstance(value, int) and not isinstance(value, bool) and
                value > 0)
    return isinstance(value, str) and bool(value)


def _runtime_binding_drifted(
        expected: dict[str, Any], observed: dict[str, Any]) -> bool:
    """Return true as soon as any non-empty identity is explicitly different."""
    for key in (
            "campaign_id", "execution_service_epoch",
            "execution_service_fencing_generation", "tool_gateway_epoch",
            "tool_session_token_sha256"):
        value = observed.get(key)
        if (_runtime_identity_value_available(key, value) and
                value != expected.get(key)):
            return True
    return False


def require_runtime_binding(
        arguments: argparse.Namespace, state: dict[str, Any]) -> None:
    """Fail closed unless the rearmed runtime tuple is still exact."""
    expected = state.get("runtime_binding")
    if not isinstance(expected, dict):
        raise SafetyStopError(
            RUNTIME_EPOCH_CHANGED,
            "RUNTIME_EPOCH_CHANGED: rearmed runtime binding is missing")
    if expected.get("campaign_id") != str(arguments.campaign_id):
        raise SafetyStopError(
            RUNTIME_EPOCH_CHANGED,
            "RUNTIME_EPOCH_CHANGED: campaign runtime binding drifted")
    # During the execution daemon's bounded in-process broker reconnect the
    # Tool Gateway deliberately reports remote_execution_ready=false while the
    # mutation gate is closed.  Wait only for that bounded window; do not read
    # market state or issue a mutation meanwhile.  An available-but-different
    # identity is still rejected immediately below, and persistent
    # unavailability remains a durable safety stop.
    deadline = (
        time.monotonic() + RUNTIME_BINDING_UNAVAILABLE_GRACE_SECONDS)
    last_error: Exception | None = None
    while True:
        try:
            observed = current_runtime_binding(arguments)
        except RuntimeBindingUnavailableError as error:
            if (_runtime_binding_drifted(expected, error.observed) or
                    (isinstance(error.execution_mode, str) and
                     bool(error.execution_mode) and
                     error.execution_mode != "PAPER")):
                raise SafetyStopError(
                    RUNTIME_EPOCH_CHANGED,
                    "RUNTIME_EPOCH_CHANGED: an available runtime identity "
                    "drifted during reconnect") from error
            last_error = error
        except Exception as error:
            last_error = error
        else:
            if observed != expected:
                raise SafetyStopError(
                    RUNTIME_EPOCH_CHANGED,
                    "RUNTIME_EPOCH_CHANGED: rearmed runtime identity drifted")
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise SafetyStopError(
                RUNTIME_EPOCH_CHANGED,
                "RUNTIME_EPOCH_CHANGED: current runtime identity is "
                "unavailable after bounded reconnect grace") from last_error
        time.sleep(min(RUNTIME_BINDING_RETRY_SECONDS, remaining))


def campaign(arguments: argparse.Namespace, action: str, request_id: str,
             extra: list[str] | None = None,
             timeout: float = 30) -> dict[str, Any]:
    command = [
        "runuser", "-u", arguments.agent_user, "--",
        arguments.campaignctl, "--domain", arguments.domain,
        "--campaign-id", arguments.campaign_id,
        "--request-id", request_id, action,
    ] + (extra or [])
    return run_json(command, timeout=timeout)


def _normalized_failure_category(value: Any) -> str | None:
    if isinstance(value, int) and not isinstance(value, bool):
        if value == 401:
            return MODEL_AUTH_UNUSABLE
        if value == 402:
            return MODEL_AUTH_BILLING
        if value == 429:
            # A provider may expose exhausted profile/account allowance only as
            # HTTP 429.  Ambiguous current 429s therefore fail closed; only an
            # explicitly request-scoped throttle may use the bounded HOLD path.
            return MODEL_AUTH_RATE_LIMIT
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
    if normalized in {"401", "http_401", "status_401"}:
        return MODEL_AUTH_UNUSABLE
    if normalized in {"402", "http_402", "status_402"}:
        return MODEL_AUTH_BILLING
    if normalized in {"429", "http_429", "status_429"}:
        return MODEL_AUTH_RATE_LIMIT
    if normalized in {
            "request_rate_limit", "request_rate_limited",
            "request_throttled", "transient_request_rate_limit"}:
        return MODEL_REQUEST_RATE_LIMIT
    if normalized in {
            "rate_limit", "rate_limited", "rate_limit_exceeded",
            "too_many_requests"}:
        return MODEL_AUTH_RATE_LIMIT
    if normalized in {
            "account_rate_limit", "auth_rate_limit", "profile_rate_limit",
            "subscription_rate_limit", "subscription_usage_limit",
            "usage_limit", "usage_limit_reached"}:
        return MODEL_AUTH_RATE_LIMIT
    if normalized in {
            "auth", "authentication", "auth_permanent", "unauthorized",
            "invalid_api_key", "invalid_auth", "authentication_failed",
            "invalid_grant", "refresh_token_reused", "token_expired",
            "expired_token", "token_revoked", "revoked_token",
            "oauth_refresh_failed", "oauth_token_refresh_failed",
            "token_refresh_failed"}:
        return MODEL_AUTH_UNUSABLE
    if normalized in {
            "billing", "insufficient_quota", "payment_required",
            "quota_exceeded"}:
        return MODEL_AUTH_BILLING
    return None


def _preferred_failure_category(categories: set[str]) -> str | None:
    for category in (
            MODEL_AUTH_UNUSABLE, MODEL_AUTH_BILLING,
            MODEL_AUTH_RATE_LIMIT, MODEL_REQUEST_RATE_LIMIT):
        if category in categories:
            return category
    return None


def _prompt_error_failure_category(
        value: Any, *, auth_failure_scoped: bool = False) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = " ".join(value.lower().split())
    # Codex app-server emits this as a structured promptError when the
    # selected subscription/profile has exhausted its multi-day allowance.
    # This is deliberately narrower than matching arbitrary 429 text.
    categories: set[str] = set()
    exact_category = _normalized_failure_category(value)
    if exact_category is not None:
        categories.add(exact_category)
    if (
            "codex subscription usage limit" in normalized or
            ("you've reached your codex" in normalized and
             "usage limit" in normalized)):
        categories.add(MODEL_AUTH_RATE_LIMIT)
    if any(marker in normalized for marker in (
            "oauth token refresh failed", "authentication failed",
            "invalid api key", "token has been revoked")):
        categories.add(MODEL_AUTH_UNUSABLE)
    if (
            "too many requests" in normalized or
            "rate limit exceeded" in normalized or
            ("429" in normalized and "rate limit" in normalized)):
        categories.add(MODEL_AUTH_RATE_LIMIT)
    opening = value.find("{")
    closing = value.rfind("}")
    if opening >= 0 and closing > opening:
        try:
            embedded = json.loads(value[opening:closing + 1])
        except json.JSONDecodeError:
            embedded = None
        if embedded is not None:
            category = _model_failure_category(
                embedded, auth_scoped=auth_failure_scoped)
            if category is not None:
                categories.add(category)
    return _preferred_failure_category(categories)


def _failure_scope_for_key(key: str) -> bool:
    normalized = "".join(
        character for character in key.lower() if character.isalnum())
    return normalized in {
        "authfailure", "authenticationfailure", "credentialfailure",
        "profilefailure", "subscriptionfailure", "billingfailure",
    }


def _historical_failure_key(key: str) -> bool:
    normalized = "".join(
        character for character in key.lower() if character.isalnum())
    return (
        normalized in {
            "executiontrace", "failoverhistory", "fallbackhistory",
            "traceattempts", "traces", "attempts", "history",
        } or "attempt" in normalized or "history" in normalized)


def _collect_model_failure_categories(
        value: Any, *, auth_failure_scoped: bool = False,
        root: bool = False) -> set[str]:
    """Collect current failure categories without traversing attempt history."""
    if isinstance(value, list):
        categories: set[str] = set()
        for item in value:
            categories.update(_collect_model_failure_categories(
                item, auth_failure_scoped=auth_failure_scoped, root=root))
        return categories
    if not isinstance(value, dict):
        return set()
    categories: set[str] = set()
    profile_failure_fields = {
        "profileFailureReason", "profile_failure_reason",
        "cooldownReason", "cooldown_reason",
        "blockedReason", "blocked_reason",
    }
    local_auth_failure_scoped = auth_failure_scoped or any(
        key in value and value.get(key) not in (None, "")
        for key in profile_failure_fields)
    category_fields = {
        "category", "code", "error_type", "errorType",
        "failover_reason", "failoverReason", "raw_reason", "rawReason",
        "reason", "reason_code", "status", "type",
    }
    http_status_fields = {
        "httpStatus", "http_status", "httpStatusCode", "http_status_code",
        "statusCode", "status_code",
    }
    message_fields = {
        "promptError", "provider_error", "providerError", "message",
        "errorMessage", "error_message", "detail", "error",
        "failureSignal",
    }

    for key in profile_failure_fields:
        if key not in value:
            continue
        category = _normalized_failure_category(value.get(key))
        if category == MODEL_REQUEST_RATE_LIMIT:
            categories.add(MODEL_AUTH_RATE_LIMIT)
        elif category is not None:
            categories.add(category)
    for key in category_fields:
        if key not in value:
            continue
        category = _normalized_failure_category(value.get(key))
        if category is not None:
            categories.add(
                MODEL_AUTH_RATE_LIMIT
                if (category == MODEL_REQUEST_RATE_LIMIT and
                    local_auth_failure_scoped)
                else category)
    for key in http_status_fields:
        if key not in value:
            continue
        category = _normalized_failure_category(value.get(key))
        if category is not None:
            categories.add(
                MODEL_AUTH_RATE_LIMIT
                if (category == MODEL_REQUEST_RATE_LIMIT and
                    local_auth_failure_scoped)
                else category)
    # Some providers put the HTTP status directly in `status`.
    status_category = _normalized_failure_category(value.get("status"))
    if status_category == MODEL_REQUEST_RATE_LIMIT:
        categories.add(
            MODEL_AUTH_RATE_LIMIT if local_auth_failure_scoped else
            MODEL_REQUEST_RATE_LIMIT)
    for key in message_fields:
        category = _prompt_error_failure_category(
            value.get(key), auth_failure_scoped=local_auth_failure_scoped)
        if category is not None:
            categories.add(category)
    # Gateway RPC success is not model success. When every auth/fallback
    # attempt fails, OpenClaw resolves the RPC with status=ok and carries the
    # current provider failure in an isError payload. Only error-marked payload
    # text is trusted for classification; ordinary assistant text must never be
    # able to manufacture a safety stop by mentioning rate limits.
    if value.get("isError") is True:
        category = _prompt_error_failure_category(
            value.get("text"),
            auth_failure_scoped=local_auth_failure_scoped)
        if category is not None:
            categories.add(category)

    for key, item in value.items():
        if not isinstance(item, (dict, list)) or _historical_failure_key(key):
            continue
        if key in {"meta", "metadata"}:
            if not isinstance(item, dict):
                continue
            # Only explicit current-error fields below metadata are relevant;
            # tracing/failover records in the same object remain historical.
            for current_key in (
                    "error", "failureSignal", "promptError",
                    "providerError", "provider_error"):
                if current_key in item:
                    categories.update(_collect_model_failure_categories(
                        {current_key: item[current_key]},
                        auth_failure_scoped=local_auth_failure_scoped))
            continue
        categories.update(_collect_model_failure_categories(
            item,
            auth_failure_scoped=(
                local_auth_failure_scoped or _failure_scope_for_key(key))))
    return categories


def _model_failure_category(
        value: Any, *, auth_scoped: bool = False) -> str | None:
    """Classify explicit current provider/auth failures deterministically."""
    return _preferred_failure_category(_collect_model_failure_categories(
        value, auth_failure_scoped=auth_scoped, root=True))


def _classified_model_error(category: str) -> Exception:
    if category == MODEL_REQUEST_RATE_LIMIT:
        return ModelRequestRateLimitError(
            "MODEL_REQUEST_RATE_LIMIT: transient model request throttled")
    return SafetyStopError(
        category, f"{category}: model auth/profile is not safely usable")


def _model_failure_documents(
        completed: subprocess.CompletedProcess[str]) -> list[Any]:
    documents: list[Any] = []
    for rendered in (completed.stdout, completed.stderr):
        try:
            value = json.loads(rendered)
        except (json.JSONDecodeError, TypeError):
            value = None
        if isinstance(value, (dict, list)):
            documents.append(value)
            continue
        if isinstance(rendered, str):
            for line in reversed(rendered.splitlines()):
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, (dict, list)):
                    documents.append(value)
    return documents


def _terminate_model_process_group(
        process: subprocess.Popen[str],
        grace_seconds: float = 1.0) -> bool:
    """Terminate every descendant from a timed-out start_new_session run."""
    process_id = process.pid
    if (not isinstance(process_id, int) or isinstance(process_id, bool) or
            process_id <= 1):
        return False
    for requested_signal in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(process_id, requested_signal)
        except ProcessLookupError:
            try:
                process.wait(timeout=grace_seconds)
            except subprocess.TimeoutExpired:
                return False
            return process.poll() is not None
        except OSError:
            return False
        try:
            process.communicate(timeout=max(0.0, grace_seconds))
        except subprocess.TimeoutExpired:
            continue
        deadline = time.monotonic() + max(0.0, grace_seconds)
        while True:
            try:
                os.killpg(process_id, 0)
            except ProcessLookupError:
                return process.poll() is not None
            except OSError:
                return False
            if time.monotonic() >= deadline:
                break
            time.sleep(0.01)
    try:
        os.killpg(process_id, 0)
    except ProcessLookupError:
        return True
    except OSError:
        return False
    return False


def _model_session_terminal(
        arguments: argparse.Namespace, decision_session_key: str,
        attempt_started_at_ms: int,
        status_timeout_sec: float = MODEL_SESSION_STATUS_TIMEOUT_SEC) -> bool:
    """Return true only for the exact unique decision session in done state."""
    completed = subprocess.run([
        "runuser", "-u", arguments.model_user, "--",
        "openclaw", "sessions", "--agent", arguments.openclaw_agent,
        "--json", "--active", "10", "--limit", "100",
    ], text=True, capture_output=True,
        timeout=max(0.001, min(
            MODEL_SESSION_STATUS_TIMEOUT_SEC, status_timeout_sec)),
        check=False)
    if completed.returncode != 0:
        return False
    try:
        document = json.loads(completed.stdout)
    except (json.JSONDecodeError, TypeError):
        return False
    sessions = document.get("sessions") if isinstance(document, dict) else None
    if not isinstance(sessions, list):
        return False
    agent_prefix = f"agent:{arguments.openclaw_agent}:"
    # openclaw accepts either a fully qualified key or an agent-local suffix.
    # The production default is already fully qualified; never prepend the
    # agent namespace twice when reconciling that exact durable session.
    expected_key = (
        decision_session_key
        if decision_session_key.startswith(agent_prefix)
        else agent_prefix + decision_session_key)
    matches = [
        item for item in sessions
        if isinstance(item, dict) and item.get("key") == expected_key
    ]
    if len(matches) != 1:
        return False
    session = matches[0]
    started_at_ms = session.get("sessionStartedAt")
    last_interaction_at_ms = session.get("lastInteractionAt")
    return (
        session.get("status") == "done" and
        isinstance(started_at_ms, int) and
        not isinstance(started_at_ms, bool) and
        started_at_ms >= attempt_started_at_ms - 5_000 and
        isinstance(last_interaction_at_ms, int) and
        not isinstance(last_interaction_at_ms, bool) and
        last_interaction_at_ms >= attempt_started_at_ms)


def _wait_for_model_session_terminal(
        arguments: argparse.Namespace, decision_session_key: str,
        attempt_started_at_ms: int) -> bool:
    deadline = (
        time.monotonic() + MODEL_ATTEMPT_TERMINAL_PROOF_TIMEOUT_SEC)
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        try:
            if _model_session_terminal(
                    arguments, decision_session_key,
                    attempt_started_at_ms, remaining):
                return True
        except (OSError, subprocess.SubprocessError):
            pass
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(MODEL_ATTEMPT_TERMINAL_PROOF_POLL_SEC, remaining))


def _run_model_command(
        command: list[str], *, arguments: argparse.Namespace | None = None,
        decision_session_key: str | None = None,
        attempt_started_at_ms: int | None = None,
        ) -> subprocess.CompletedProcess[str]:
    """Run one owned model process group and prove terminal status on timeout."""
    try:
        process = subprocess.Popen(
            command, text=True, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, start_new_session=True)
    except (OSError, subprocess.SubprocessError) as error:
        raise ModelAttemptFailure(
            MODEL_ATTEMPT_TRANSPORT,
            "MODEL_ATTEMPT_TRANSPORT: model runtime unavailable") from error
    try:
        stdout, stderr = process.communicate(timeout=MODEL_ATTEMPT_TIMEOUT_SEC)
    except subprocess.TimeoutExpired as error:
        terminal = _terminate_model_process_group(process)
        local_detail = (
            "local process group reaped" if terminal and process.poll() is not None
            else "local process group terminal status unproven")
        if (terminal and process.poll() is not None and
                arguments is not None and decision_session_key is not None and
                attempt_started_at_ms is not None and
                _wait_for_model_session_terminal(
                    arguments, decision_session_key,
                    attempt_started_at_ms)):
            raise ModelAttemptFailure(
                MODEL_ATTEMPT_TIMEOUT,
                "MODEL_ATTEMPT_TIMEOUT: model attempt exceeded its local "
                "deadline; exact Gateway session reached durable terminal "
                "state and its late result was discarded") from error
        # Local process ownership is insufficient evidence that a Gateway RPC
        # accepted before disconnect is terminal. Preserve the permanent latch
        # whenever the unique durable session cannot itself prove completion.
        raise ModelAttemptTerminalUncertainError(
            "MODEL_ATTEMPT_TERMINAL_UNCERTAIN: model attempt timed out; "
            f"{local_detail}; Gateway terminal status is unproven") from error
    return subprocess.CompletedProcess(
        command, process.returncode, stdout, stderr)


def model_decision(arguments: argparse.Namespace, history: list[dict[str, Any]],
                   position: float, pnl: dict[str, float]) -> dict[str, Any]:
    try:
        _verify_effective_auth_profile(arguments)
    except SafetyStopError:
        raise
    except Exception as error:
        raise ModelAttemptFailure(
            MODEL_ATTEMPT_TRANSPORT,
            "MODEL_ATTEMPT_TRANSPORT: effective auth route check "
            "temporarily unavailable") from error
    mids = [item["mid"] for item in history[-30:]]
    returns = [mids[index] - mids[index - 1] for index in range(1, len(mids))]
    prompt = {
        "role": "local IB PAPER-only EURUSD trading decision engine",
        "hard_rules": {
            "tools_forbidden": True,
            "output": "one compact JSON object only",
            "actions": ["BUY", "SELL", "HOLD"],
            "quantity": ORDER_QUANTITY,
            "live_forbidden": True,
            "do_not_force_trade": True,
        },
        "market": {
            "samples": history[-30:],
            "sample_count": len(history),
            "short_change": (mids[-1] - mids[-4]) if len(mids) >= 4 else 0,
            "long_change": (mids[-1] - mids[0]) if len(mids) >= 2 else 0,
            "mean_step": sum(returns) / len(returns) if returns else 0,
        },
        "portfolio": {"position": position, **pnl},
        "instruction": (
            "Return keys action, confidence, rationale. confidence is 0..1. "
            "Use HOLD unless the sampled direction is coherent. If a position "
            "exists, choose the opposite action only to exit/reverse conviction; "
            "otherwise HOLD. Never mention or request LIVE."),
    }
    # Every decision is intentionally isolated. Reusing one long OpenClaw
    # session eventually triggers transcript compaction. The effective auth
    # allowlist was revalidated immediately above and may reorder only within
    # the complete rearmed profile set; embedded --local turns can stall while
    # bootstrapping the Codex harness.
    decision_session_key = (
        f"{arguments.model_session_key}-{uuid.uuid4().hex}")
    attempt_started_at_ms = now_ms()
    completed = _run_model_command([
            "runuser", "-u", arguments.model_user, "--",
            "openclaw", "agent", "--agent", arguments.openclaw_agent,
            "--session-key", decision_session_key,
            "--model", arguments.model,
            "--message", json.dumps(prompt, separators=(",", ":")),
            "--thinking", "minimal", "--json", "--timeout", "105",
        ], arguments=arguments, decision_session_key=decision_session_key,
        attempt_started_at_ms=attempt_started_at_ms)
    failure_documents = _model_failure_documents(completed)
    category = _model_failure_category(failure_documents)
    if completed.returncode != 0:
        if category is not None:
            raise _classified_model_error(category)
        raise ModelAttemptFailure(
            MODEL_ATTEMPT_TRANSPORT,
            "MODEL_ATTEMPT_TRANSPORT: model runtime failed: " +
            (completed.stderr.strip() or completed.stdout.strip() or
             f"exit {completed.returncode}"))
    try:
        envelope = json.loads(completed.stdout)
    except (json.JSONDecodeError, TypeError) as error:
        if category is not None:
            raise _classified_model_error(category) from error
        raise ModelAttemptFailure(
            MODEL_ATTEMPT_CONTRACT_INVALID,
            "MODEL_ATTEMPT_CONTRACT_INVALID: model runtime response is not "
            "valid JSON") from error
    result = envelope.get("result") if isinstance(envelope, dict) else None
    text = None
    if isinstance(result, dict):
        text = result.get("finalAssistantVisibleText")
        if not text:
            payloads = result.get("payloads")
            if isinstance(payloads, list):
                text = next((
                    item.get("text") for item in payloads
                    if isinstance(item, dict) and
                    item.get("isError") is not True and
                    item.get("isReasoning") is not True and
                    isinstance(item.get("text"), str) and item.get("text")),
                    None)
    if not text and isinstance(envelope, dict):
        # Embedded/local runs return payloads at the top level; Gateway runs
        # wrap the same visible text below result.
        payloads = envelope.get("payloads")
        if isinstance(payloads, list):
            text = next((
                item.get("text") for item in payloads
                if isinstance(item, dict) and
                item.get("isError") is not True and
                item.get("isReasoning") is not True and
                isinstance(item.get("text"), str) and item.get("text")),
                None)
    try:
        decision = json.loads(text or "")
    except (json.JSONDecodeError, TypeError) as error:
        envelope_category = _model_failure_category(envelope)
        failure_category = _preferred_failure_category({
            candidate for candidate in (category, envelope_category)
            if candidate is not None
        })
        if failure_category is not None:
            raise _classified_model_error(failure_category) from error
        raise ModelAttemptFailure(
            MODEL_ATTEMPT_CONTRACT_INVALID,
            "MODEL_ATTEMPT_CONTRACT_INVALID: model decision response "
            "unavailable") from error
    if (
            not isinstance(decision, dict) or
            set(decision) != {"action", "confidence", "rationale"} or
            decision["action"] not in {"BUY", "SELL", "HOLD"} or
            not isinstance(decision["confidence"], (int, float)) or
            isinstance(decision["confidence"], bool) or
            not 0 <= float(decision["confidence"]) <= 1 or
            not isinstance(decision["rationale"], str)):
        envelope_category = _model_failure_category(envelope)
        failure_category = _preferred_failure_category({
            candidate for candidate in (category, envelope_category)
            if candidate is not None
        })
        if failure_category is not None:
            raise _classified_model_error(failure_category)
        raise ModelAttemptFailure(
            MODEL_ATTEMPT_CONTRACT_INVALID,
            "MODEL_ATTEMPT_CONTRACT_INVALID: model decision contract invalid")
    return decision


def position_snapshot(
        arguments: argparse.Namespace, timeout: float = 30,
        *, require_generation: bool = False) -> tuple[float, int, int]:
    payload = tool(
        arguments, "portfolio.list_positions", timeout=timeout)
    if payload.get("authoritative") is not True:
        raise RuntimeError("positions are not authoritative")
    position_generation = payload.get("position_generation", 0)
    fx_cash_generation = payload.get("fx_cash_generation", 0)
    if require_generation and (
            not isinstance(position_generation, int) or
            isinstance(position_generation, bool) or
            position_generation <= 0 or
            not isinstance(fx_cash_generation, int) or
            isinstance(fx_cash_generation, bool) or
            fx_cash_generation <= 0):
        raise RuntimeError("position generations are not authoritative")
    for item in payload.get("positions", []):
        if isinstance(item, dict) and item.get("instrument") == INSTRUMENT:
            return (float(item.get("quantity", 0)),
                    int(position_generation), int(fx_cash_generation))
    return 0.0, int(position_generation), int(fx_cash_generation)


def position_quantity(
        arguments: argparse.Namespace, timeout: float = 30) -> float:
    return position_snapshot(arguments, timeout)[0]


def orders_snapshot(
        arguments: argparse.Namespace, timeout: float = 30) -> dict[str, Any]:
    payload = tool(arguments, "orders.list", timeout=timeout)
    validate_order_projection(payload)
    return payload


def validate_order_projection(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate the global/owner active-order projection as one snapshot.

    `active_order_ids` is deliberately global.  It must never be interpreted
    as the calling session's cancellation set.  The execution runtime supplies
    a second, exact owner-scoped set plus the epoch/generation and completeness
    evidence tying both sets to the same IB snapshot.
    """
    required = {
        "source", "authoritative", "active_orders_source",
        "active_orders_connection_epoch", "active_orders_generation",
        "global_active_orders_complete", "owner_projection_source",
        "owner_projection_connection_epoch", "owner_projection_generation",
        "owner_projection_complete", "owned_active_order_ids_authoritative",
        "owner_scope", "reason_code", "active_order_ids",
        "owned_active_order_ids", "unmapped_active_order_ids",
        "recent_orders",
    }
    if not isinstance(payload, dict) or not required.issubset(payload):
        raise RuntimeError("orders projection contract invalid")
    epoch = payload.get("active_orders_connection_epoch")
    generation = payload.get("active_orders_generation")
    owner_epoch = payload.get("owner_projection_connection_epoch")
    owner_generation = payload.get("owner_projection_generation")
    scope = payload.get("owner_scope")
    if (payload.get("source") != "IB" or
            payload.get("active_orders_source") != "IB_OPEN_ORDERS" or
            payload.get("owner_projection_source") !=
                "EXECUTION_COORDINATOR_ORDER_OWNERS" or
            not isinstance(epoch, int) or isinstance(epoch, bool) or epoch <= 0 or
            not isinstance(generation, int) or
            isinstance(generation, bool) or generation <= 0 or
            owner_epoch != epoch or owner_generation != generation or
            not isinstance(scope, dict) or
            set(scope) != {
                "agent_id", "session_id", "execution_domain", "account"} or
            any(not isinstance(scope.get(name), str) or not scope.get(name)
                for name in scope) or
            not isinstance(payload.get("reason_code"), str) or
            not isinstance(payload.get("recent_orders"), list)):
        raise RuntimeError("orders projection contract invalid")

    parsed: dict[str, tuple[int, ...]] = {}
    for name in (
            "active_order_ids", "owned_active_order_ids",
            "unmapped_active_order_ids"):
        raw = payload.get(name)
        if (not isinstance(raw, list) or
                any(not isinstance(value, int) or isinstance(value, bool) or
                    value < 0 for value in raw) or
                raw != sorted(set(raw))):
            raise RuntimeError("orders projection contract invalid")
        parsed[name] = tuple(raw)
    global_ids = set(parsed["active_order_ids"])
    owned_ids = set(parsed["owned_active_order_ids"])
    unmapped_ids = set(parsed["unmapped_active_order_ids"])
    if (not owned_ids.issubset(global_ids) or
            not unmapped_ids.issubset(global_ids) or
            owned_ids.intersection(unmapped_ids)):
        raise RuntimeError("orders projection contract invalid")
    if (payload.get("authoritative") is not True or
            payload.get("global_active_orders_complete") is not True or
            payload.get("owner_projection_complete") is not True or
            payload.get("owned_active_order_ids_authoritative") is not True or
            payload.get("reason_code") != "" or unmapped_ids):
        raise RuntimeError("orders projection is not authoritative")
    return {
        "connection_epoch": epoch,
        "generation": generation,
        "owner_scope": dict(scope),
        "global_active_order_ids": parsed["active_order_ids"],
        "owned_active_order_ids": parsed["owned_active_order_ids"],
    }


def active_orders(
        arguments: argparse.Namespace, timeout: float = 30) -> list[int]:
    payload = orders_snapshot(arguments, timeout)
    projection = validate_order_projection(payload)
    return list(projection["global_active_order_ids"])


def owned_active_orders(
        arguments: argparse.Namespace, timeout: float = 30) -> list[int]:
    payload = orders_snapshot(arguments, timeout)
    projection = validate_order_projection(payload)
    return list(projection["owned_active_order_ids"])


def authoritative_broker_fill(
        arguments: argparse.Namespace, order_id: int, expected_side: str,
        expected_quantity: float, timeout: float = 5.0,
) -> dict[str, Any]:
    """Return one exact owner-bound IB execution, never an order/quote proxy."""
    snapshot = orders_snapshot(arguments, timeout=timeout)
    projection = validate_order_projection(snapshot)
    matches = [
        raw for raw in snapshot.get("recent_orders", [])
        if isinstance(raw, dict) and raw.get("order_id") == order_id
    ]
    if len(matches) != 1:
        raise RecoveryRequiredError(
            "RECOVERY_REQUIRED: exact broker fill evidence unavailable")
    raw = matches[0]
    owner = projection["owner_scope"]
    execution_id = raw.get("broker_execution_id")
    account = raw.get("account")
    execution_domain = raw.get("execution_domain")
    side = _normalized_side(raw.get("side"))
    status = "".join(
        character for character in str(raw.get("status", "")).lower()
        if character.isalnum())
    try:
        quantity = float(raw.get("filled_quantity"))
        remaining = float(raw.get("remaining_quantity"))
        price = float(raw.get("average_fill_price"))
        execution_quantity = float(raw.get("broker_execution_quantity"))
        execution_price = float(raw.get("broker_execution_price"))
    except (TypeError, ValueError) as error:
        raise RecoveryRequiredError(
            "RECOVERY_REQUIRED: broker fill values invalid") from error
    if (raw.get("terminal") is not True or
            raw.get("economic_fill") is not True or status != "filled" or
            not isinstance(execution_id, str) or
            re.fullmatch(r"[A-Za-z0-9._:-]{1,256}", execution_id) is None or
            raw.get("broker_execution_ambiguous") is not False or
            account != owner.get("account") or
            execution_domain != owner.get("execution_domain") or
            raw.get("instrument") != INSTRUMENT or side != expected_side or
            not all(math.isfinite(value) for value in (
                quantity, remaining, price,
                execution_quantity, execution_price)) or
            not _quantity_equal(quantity, expected_quantity) or
            not _quantity_equal(execution_quantity, quantity) or
            not _quantity_equal(remaining, 0.0) or
            not _quantity_equal(execution_price, price) or price <= 0.0):
        raise RecoveryRequiredError(
            "RECOVERY_REQUIRED: broker fill identity/economics invalid")
    return {
        "broker_execution_id": execution_id,
        "account": account,
        "execution_domain": execution_domain,
        "instrument": INSTRUMENT,
        "order_id": order_id,
        "side": side,
        "filled_quantity": quantity,
        "average_fill_price": price,
    }


def _broker_fill_key(fill: dict[str, Any]) -> str:
    return "|".join(str(fill[name]) for name in (
        "broker_execution_id", "account", "execution_domain", "instrument"))


def _accounting_fill(fill: dict[str, Any]) -> dict[str, Any]:
    try:
        normalized = {
            "broker_execution_id": str(fill["broker_execution_id"]),
            "account": str(fill["account"]),
            "execution_domain": str(fill["execution_domain"]),
            "instrument": str(fill["instrument"]),
            "order_id": int(fill["order_id"]),
            "side": _normalized_side(fill["side"]),
            "filled_quantity": float(fill["filled_quantity"]),
            "average_fill_price": float(fill["average_fill_price"]),
        }
    except (KeyError, TypeError, ValueError) as error:
        raise RecoveryRequiredError(
            "RECOVERY_REQUIRED: broker fill accounting record invalid") \
            from error
    if (not normalized["broker_execution_id"] or
            not normalized["account"] or
            not normalized["execution_domain"] or
            normalized["instrument"] != INSTRUMENT or
            normalized["order_id"] < 0 or
            normalized["side"] not in {"BUY", "SELL"} or
            not _positive_number(normalized["filled_quantity"]) or
            not _positive_number(normalized["average_fill_price"])):
        raise RecoveryRequiredError(
            "RECOVERY_REQUIRED: broker fill accounting record invalid")
    return normalized


def _record_broker_fill(
        state: dict[str, Any], fill: dict[str, Any]) -> bool:
    """Persist an idempotent fill ledger; conflicting reconnect replay stops."""
    fill = _accounting_fill(fill)
    records = state.setdefault("processed_broker_fills", [])
    if not isinstance(records, list) or len(records) > 512:
        raise RecoveryRequiredError(
            "RECOVERY_REQUIRED: broker fill ledger invalid")
    key = _broker_fill_key(fill)
    for existing in records:
        if not isinstance(existing, dict):
            raise RecoveryRequiredError(
                "RECOVERY_REQUIRED: broker fill ledger invalid")
        normalized_existing = _accounting_fill(existing)
        if _broker_fill_key(normalized_existing) == key:
            if normalized_existing != fill:
                raise RecoveryRequiredError(
                    "RECOVERY_REQUIRED: broker execution replay conflicted")
            return False
    records.append(dict(fill))
    return True


def record_broker_entry(
        state: dict[str, Any], fill: dict[str, Any], position: float) -> None:
    fill = _accounting_fill(fill)
    if _normalized_side(fill.get("side")) != (
            "BUY" if position > 0 else "SELL") or not _quantity_equal(
            abs(position), float(fill.get("filled_quantity", 0.0))):
        raise RecoveryRequiredError(
            "RECOVERY_REQUIRED: entry fill/position mismatch")
    current = state.get("open_broker_fill")
    if isinstance(current, dict):
        if current != fill:
            raise RecoveryRequiredError(
                "RECOVERY_REQUIRED: open broker fill already exists")
        return
    if not _record_broker_fill(state, fill):
        raise RecoveryRequiredError(
            "RECOVERY_REQUIRED: replayed entry has no open trade")
    state["open_broker_fill"] = dict(fill)
    state["broker_fill_entries"] = int(
        state.get("broker_fill_entries", 0)) + 1
    state["entries"] = int(state.get("entries", 0)) + 1
    state["entry_fill_price"] = float(fill["average_fill_price"])
    state["entry_price_basis"] = "broker_average_fill_price"
    state["entry_mid"] = float(fill["average_fill_price"])


def record_broker_close(
        state: dict[str, Any], close_fill: dict[str, Any], *,
        recovery: bool = False,
        entry_fill: dict[str, Any] | None = None) -> bool:
    close_fill = _accounting_fill(close_fill)
    opened = entry_fill if entry_fill is not None else state.get(
        "open_broker_fill")
    if not isinstance(opened, dict):
        raise RecoveryRequiredError(
            "RECOVERY_REQUIRED: close fill has no authoritative entry fill")
    opened = _accounting_fill(opened)
    quantity = float(opened.get("filled_quantity", 0.0))
    signed_quantity = quantity if _normalized_side(
        opened.get("side")) == "BUY" else -quantity
    if (_normalized_side(close_fill.get("side")) !=
            ("SELL" if signed_quantity > 0 else "BUY") or
            not _quantity_equal(
                float(close_fill.get("filled_quantity", 0.0)), quantity)):
        raise RecoveryRequiredError(
            "RECOVERY_REQUIRED: close fill does not offset entry fill")
    entry_added = _record_broker_fill(state, opened)
    close_added = _record_broker_fill(state, close_fill)
    if not close_added:
        return False
    gross = ((float(close_fill["average_fill_price"]) -
              float(opened["average_fill_price"])) * signed_quantity)
    if not math.isfinite(gross):
        raise RecoveryRequiredError(
            "RECOVERY_REQUIRED: realized gross PnL invalid")
    if entry_added:
        state["broker_fill_entries"] = int(
            state.get("broker_fill_entries", 0)) + 1
    state["broker_fill_exits"] = int(
        state.get("broker_fill_exits", 0)) + 1
    state["closed_trades"] = int(state.get("closed_trades", 0)) + 1
    if recovery:
        state["recovery_broker_fill_exits"] = int(
            state.get("recovery_broker_fill_exits", 0)) + 1
        state["recovery_closed_trades"] = int(
            state.get("recovery_closed_trades", 0)) + 1
    else:
        state["strategy_closed_trades"] = int(
            state.get("strategy_closed_trades", 0)) + 1
    state["realized_gross_pnl"] = round(
        float(state.get("realized_gross_pnl", 0.0)) + gross, 10)
    state["realized_gross_pnl_quote_currency"] = "USD"
    state["fees_known"] = False
    state["realized_fees"] = None
    state["realized_net_pnl"] = None
    # Retain the legacy display field, but bind it to exact gross broker fills.
    state["realized_pnl_estimate"] = state["realized_gross_pnl"]
    state["open_broker_fill"] = None
    return True


def next_execution_event(
        arguments: argparse.Namespace, after_sequence: int,
        timeout_ms: int, command_timeout: float = 30) -> dict[str, Any] | None:
    """Read one owner-routed execution event; timeout is not an error."""
    try:
        response = tool_response(arguments, "events.wait", {
            "after_sequence": after_sequence,
            "timeout_ms": max(0, min(int(timeout_ms), 30_000)),
        }, timeout=command_timeout)
    except RuntimeError as error:
        rendered = str(error)
        if any(marker in rendered for marker in (
                "EXECUTION_EVENT_TIMEOUT", "EVENT_WAIT_TIMEOUT",
                "event wait timed out")):
            return None
        raise RecoveryRequiredError(
            f"RECOVERY_REQUIRED: execution evidence unavailable: {rendered}") \
            from error
    payload = response.get("payload")
    if not isinstance(payload, dict):
        raise RecoveryRequiredError(
            "RECOVERY_REQUIRED: events.wait returned invalid payload")
    return payload


def _event_sequence(
        event: dict[str, Any], cursor: int,
        stream_epoch: str | None) -> tuple[int, str]:
    sequence = event.get("sequence")
    epoch = event.get("stream_epoch")
    if (not isinstance(sequence, int) or isinstance(sequence, bool) or
            sequence <= cursor or not isinstance(epoch, str) or not epoch):
        raise RecoveryRequiredError(
            "RECOVERY_REQUIRED: execution event cursor invalid")
    if stream_epoch is not None and epoch != stream_epoch:
        raise RecoveryRequiredError(
            "RECOVERY_REQUIRED: execution event stream epoch changed")
    return sequence, epoch


def _positive_number(value: Any) -> bool:
    return (isinstance(value, (int, float)) and
            not isinstance(value, bool) and math.isfinite(float(value)) and
            float(value) > 0)


def _economic_fill_event(event: dict[str, Any]) -> bool:
    event_type = str(event.get("type", "")).lower()
    fill_shaped = event_type in {"order.fill", "order.status"}
    return (fill_shaped and _positive_number(event.get("filled_quantity")) and
            _positive_number(event.get("average_fill_price")))


def _terminal_non_fill_event(event: dict[str, Any]) -> bool:
    if str(event.get("type", "")).lower() not in {
            "order.status", "order.completed", "order.reject"}:
        return False
    normalized = "".join(
        character for character in str(event.get("status", "")).lower()
        if character.isalnum())
    return normalized in TERMINAL_NON_FILL_STATUSES


def _normalized_side(value: Any) -> str:
    rendered = str(value or "").upper()
    if rendered in {"BUY", "BOT"}:
        return "BUY"
    if rendered in {"SELL", "SLD"}:
        return "SELL"
    return ""


def _nonnegative_number(value: Any, field: str) -> float:
    if (not isinstance(value, (int, float)) or isinstance(value, bool) or
            not math.isfinite(float(value)) or float(value) < 0):
        raise RecoveryRequiredError(
            f"RECOVERY_REQUIRED: invalid {field} in order evidence")
    return float(value)


def _quantity_equal(left: float, right: float) -> bool:
    return math.isclose(left, right, rel_tol=0.0, abs_tol=1e-6)


def _expected_position(
        baseline_position: float, expected_side: str,
        expected_quantity: float) -> float:
    signed = expected_quantity if expected_side == "BUY" else -expected_quantity
    return baseline_position + signed


def _position_matches(
        position: float, baseline_position: float, expected_side: str,
        expected_quantity: float) -> bool:
    return _quantity_equal(
        position, _expected_position(
            baseline_position, expected_side, expected_quantity))


def _remaining_call_timeout(deadline: float, cap: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise RecoveryRequiredError(
            "RECOVERY_REQUIRED: cycle evidence budget exhausted")
    return min(cap, max(0.1, remaining))


def _apply_order_evidence(
        record: dict[str, Any], order_id: int, expected_side: str,
        expected_quantity: float, baseline_position: float,
        current_position: float | None, evidence: dict[str, Any], *,
        recent_projection: bool = False) -> str | None:
    if record.get("order_id") != order_id:
        return None
    instrument = record.get("instrument")
    if isinstance(instrument, str) and instrument and instrument != INSTRUMENT:
        raise RecoveryRequiredError(
            "RECOVERY_REQUIRED: order evidence instrument mismatch")
    status = str(record.get("status", ""))
    normalized_status = "".join(
        character for character in status.lower() if character.isalnum())
    economic = (record.get("economic_fill") is True if recent_projection
                else _economic_fill_event(record))
    terminal_non_fill = (
        record.get("terminal") is True and
        normalized_status in TERMINAL_NON_FILL_STATUSES
        if recent_projection else _terminal_non_fill_event(record))
    if economic or terminal_non_fill:
        if _normalized_side(record.get("side")) != expected_side:
            raise RecoveryRequiredError(
                "RECOVERY_REQUIRED: order evidence side mismatch")
    if economic:
        filled = _nonnegative_number(
            record.get("filled_quantity"), "filled_quantity")
        remaining = _nonnegative_number(
            record.get("remaining_quantity"), "remaining_quantity")
        average_price = _nonnegative_number(
            record.get("average_fill_price"), "average_fill_price")
        if (filled <= 0 or average_price <= 0 or
                filled > expected_quantity + 1e-6 or
                not _quantity_equal(filled + remaining, expected_quantity)):
            raise RecoveryRequiredError(
                "RECOVERY_REQUIRED: order economic evidence quantity invalid")
        if filled >= float(evidence["filled_quantity"]):
            evidence["filled_quantity"] = filled
            evidence["remaining_quantity"] = remaining
            evidence["average_fill_price"] = average_price
    elif normalized_status == "filled":
        raise RecoveryRequiredError(
            "RECOVERY_REQUIRED: Filled lacked positive economic evidence")
    reason = record.get("reason_code")
    if isinstance(reason, str) and reason:
        evidence["reason_code"] = reason[:256]
    fully_filled = (
        _quantity_equal(float(evidence["filled_quantity"]), expected_quantity) and
        _quantity_equal(float(evidence["remaining_quantity"]), 0.0))
    if (fully_filled and
            evidence.get("position_generation_advanced") is True and
            current_position is not None and _position_matches(
            current_position, baseline_position, expected_side,
            expected_quantity)):
        return "FILLED"
    if terminal_non_fill:
        evidence["terminal_non_fill"] = normalized_status
        position_unchanged = (current_position is not None and
            _quantity_equal(current_position, baseline_position))
        if (_quantity_equal(float(evidence["filled_quantity"]), 0.0) and
                position_unchanged):
            return "REJECTED"
    return None


def _apply_recent_order_evidence(
        snapshot: dict[str, Any], order_id: int, expected_side: str,
        expected_quantity: float, baseline_position: float,
        current_position: float | None,
        evidence: dict[str, Any]) -> str | None:
    recent_orders = snapshot.get("recent_orders", [])
    if not isinstance(recent_orders, list):
        raise RecoveryRequiredError(
            "RECOVERY_REQUIRED: orders.list recent_orders invalid")
    for record in recent_orders:
        if not isinstance(record, dict):
            raise RecoveryRequiredError(
                "RECOVERY_REQUIRED: orders.list recent order invalid")
        resolved = _apply_order_evidence(
            record, order_id, expected_side, expected_quantity,
            baseline_position, current_position, evidence,
            recent_projection=True)
        if resolved is not None:
            return resolved
    return None


def _persist_state(arguments: argparse.Namespace, state: dict[str, Any]) -> None:
    state_file = getattr(arguments, "state_file", None)
    if state_file is not None:
        write_json(Path(state_file), state)


def _suspension_code(error: BaseException) -> str | None:
    current: BaseException | None = error
    visited: set[int] = set()
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        if isinstance(current, SafetyStopError):
            return current.suspension_code
        if (isinstance(current, ToolRejectedError) and
                current.reason_code in {
                    "AGENT_TRADE_RATE_LIMIT",
                    "AGENT_RISK_REDUCTION_RATE_LIMIT"}):
            return TRADE_TOOL_BUDGET_EXHAUSTED
        current = current.__cause__ or current.__context__
    return None


def _mark_recovery_required(
        arguments: argparse.Namespace, state: dict[str, Any],
        reason: str) -> None:
    rendered = reason if reason.startswith("RECOVERY_REQUIRED") else (
        "RECOVERY_REQUIRED: " + reason)
    state["recovery_required"] = True
    state["recovery_reason"] = rendered[:1000]
    state["recovery_halt_confirmed"] = False
    state["last_error"] = rendered[:1000]
    state["last_order_result"] = "RECOVERY_REQUIRED"
    state["updated_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    _persist_state(arguments, state)


def _mark_trading_suspended(
        arguments: argparse.Namespace, state: dict[str, Any],
        suspension_code: str, reason: str) -> None:
    preserve_completed_recovery = (
        state.get("trading_suspended") is True and
        state.get("recovery_complete") is True and
        state.get("recovery_phase") == "FLAT_CONFIRMED")
    completed_receipt = state.get("recovery_receipt_sha256")
    completed_halt = state.get("recovery_halt_confirmed")
    _mark_recovery_required(arguments, state, reason)
    state["trading_suspended"] = True
    if not isinstance(state.get("suspension_code"), str):
        state["suspension_code"] = suspension_code
    if not isinstance(state.get("suspension_id"), str):
        state["suspension_id"] = "suspension-" + uuid.uuid4().hex
    if not isinstance(state.get("suspended_at_ms"), int):
        state["suspended_at_ms"] = now_ms()
    if not isinstance(state.get("auth_generation_at_suspend"), str):
        state["auth_generation_at_suspend"] = str(
            getattr(arguments, "auth_generation", "unversioned"))[:128]
    if not isinstance(state.get("campaign_id_at_suspend"), str):
        state["campaign_id_at_suspend"] = str(
            getattr(arguments, "campaign_id", "unknown"))[:256]
    if state.get("incident_pending_order_id") is None:
        state["incident_pending_order_id"] = state.get("pending_order_id")
    if preserve_completed_recovery:
        state["recovery_phase"] = "FLAT_CONFIRMED"
        state["recovery_complete"] = True
        state["recovery_receipt_sha256"] = completed_receipt
        state["recovery_halt_confirmed"] = completed_halt
    else:
        state["recovery_phase"] = "REQUESTED"
        state["recovery_complete"] = False
        state["recovery_receipt_sha256"] = None
    _persist_state(arguments, state)


def _clear_pending_order(state: dict[str, Any], result: str) -> None:
    state["pending_order_id"] = None
    state["pending_order_since_ms"] = None
    state["last_order_result"] = result


def _record_pending_mutation(
        arguments: argparse.Namespace, state: dict[str, Any],
        kind: str, command_id: str) -> None:
    """Durably name a broker mutation before crossing its dispatch boundary."""
    token_name = Path(str(getattr(
        arguments, "token_file", "local-paper.token"))).name
    token_sha256 = _tool_session_token_sha256(arguments)
    if (kind not in {"PLACE_ORDER", "FLATTEN_POSITION"} or
            re.fullmatch(r"[A-Za-z0-9._:-]{8,128}", command_id) is None or
            re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{2,159}",
                         token_name) is None or
            re.fullmatch(r"sha256:[0-9a-f]{64}", token_sha256) is None):
        raise RecoveryRequiredError(
            "RECOVERY_REQUIRED: mutation command identity invalid")
    state["pending_mutation_kind"] = kind
    state["pending_mutation_command_id"] = command_id
    state["pending_mutation_recorded_at_ms"] = now_ms()
    state["pending_mutation_token_name"] = token_name
    state["pending_mutation_token_sha256"] = token_sha256
    _persist_state(arguments, state)


def _clear_pending_mutation(state: dict[str, Any]) -> None:
    state["pending_mutation_kind"] = None
    state["pending_mutation_command_id"] = None
    state["pending_mutation_recorded_at_ms"] = None
    state["pending_mutation_token_name"] = None
    state["pending_mutation_token_sha256"] = None


def _ensure_recovery_halt(
        arguments: argparse.Namespace, state: dict[str, Any],
        timeout: float = CAMPAIGN_FINALIZE_TIMEOUT_SEC) -> bool:
    response = campaign(
        arguments, "halt", "halt-" + uuid.uuid4().hex,
        ["--reason-code", RECOVERY_HALT_REASON], timeout=timeout)
    if response.get("status") != "ok":
        raise RuntimeError(f"campaign halt rejected: {response}")
    state["recovery_halt_confirmed"] = True
    _persist_state(arguments, state)
    return True


def _settle_order(
        arguments: argparse.Namespace, order_id: int, expected_side: str,
        expected_quantity: float, baseline_position: float,
        resolution_deadline: float, baseline_position_generation: int = 0,
        baseline_fx_cash_generation: int = 0) -> str:
    """Return FILLED or REJECTED; uncertain results always fail closed."""
    cursor = 0
    stream_epoch: str | None = None
    broker_reason = ""
    event_path_error = ""
    evidence: dict[str, Any] = {
        "filled_quantity": 0.0,
        "remaining_quantity": expected_quantity,
        "average_fill_price": 0.0,
        "terminal_non_fill": "",
        "reason_code": "",
        "position_generation_advanced": False,
    }

    def settlement_position() -> float | None:
        try:
            if (baseline_position_generation <= 0 or
                    baseline_fx_cash_generation <= 0):
                evidence["position_generation_advanced"] = True
                return position_quantity(
                    arguments,
                    _remaining_call_timeout(resolution_deadline, 2.0))
            quantity, position_generation, fx_cash_generation = position_snapshot(
                arguments,
                _remaining_call_timeout(resolution_deadline, 2.0),
                require_generation=True)
            advanced = (
                position_generation > baseline_position_generation and
                fx_cash_generation > baseline_fx_cash_generation)
            evidence["position_generation_advanced"] = advanced
            return quantity
        except RuntimeError as error:
            # A broker fill starts a new account-specific CashBalance snapshot.
            # The old generation is deliberately non-authoritative until the
            # matching end callback. Keep consuming exact order evidence while
            # that bounded refresh is pending; never infer a position from it.
            if "positions are not authoritative" in str(error):
                return None
            raise

    def observe(timeout_ms: int) -> str | None:
        nonlocal cursor, stream_epoch, broker_reason, event_path_error
        current_position = settlement_position()
        event: dict[str, Any] | None = None
        if not event_path_error:
            try:
                event = next_execution_event(
                    arguments, cursor, timeout_ms,
                    _remaining_call_timeout(resolution_deadline, 2.0))
            except RecoveryRequiredError as error:
                # A relay gap is not terminal. The additive, owner-bound
                # recent_orders projection can still provide authoritative
                # durable resolution for this exact order id.
                event_path_error = str(error)[:512]
        if event is None:
            snapshot = orders_snapshot(
                arguments, _remaining_call_timeout(resolution_deadline, 2.0))
            resolved = _apply_recent_order_evidence(
                snapshot, order_id, expected_side, expected_quantity,
                baseline_position, current_position, evidence)
            if (resolved is None and event_path_error and timeout_ms > 0):
                time.sleep(min(timeout_ms, 1000) / 1000)
            return resolved
        cursor, stream_epoch = _event_sequence(event, cursor, stream_epoch)
        raw_event_order_id = event.get("order_id")
        if (not isinstance(raw_event_order_id, int) or
                isinstance(raw_event_order_id, bool) or
                raw_event_order_id != order_id):
            snapshot = orders_snapshot(
                arguments, _remaining_call_timeout(resolution_deadline, 2.0))
            return _apply_recent_order_evidence(
                snapshot, order_id, expected_side, expected_quantity,
                baseline_position, current_position, evidence)
        reason = event.get("reason_code")
        if isinstance(reason, str) and reason:
            broker_reason = reason[:256]
        return _apply_order_evidence(
            event, order_id, expected_side, expected_quantity,
            baseline_position, current_position, evidence)

    settlement_deadline = min(
        time.monotonic() + min(
            max(0, arguments.fill_timeout_sec), ORDER_SETTLEMENT_WINDOW_SEC),
        resolution_deadline - ORDER_CANCEL_WINDOW_SEC)
    while time.monotonic() < settlement_deadline:
        remaining_ms = max(
            0, int((settlement_deadline - time.monotonic()) * 1000))
        resolved = observe(min(1000, remaining_ms))
        if resolved is not None:
            return resolved

    current_position = settlement_position()
    snapshot = orders_snapshot(
        arguments, _remaining_call_timeout(resolution_deadline, 2.0))
    resolved = _apply_recent_order_evidence(
        snapshot, order_id, expected_side, expected_quantity,
        baseline_position, current_position, evidence)
    if resolved is not None:
        return resolved
    orders = [int(value) for value in snapshot.get("active_order_ids", [])]
    if order_id in orders:
        tool_response(
            arguments, "trade.cancel_order", {"order_id": order_id},
            "cancel-" + uuid.uuid4().hex,
            timeout=_remaining_call_timeout(resolution_deadline, 2.0))
        cancel_deadline = min(
            resolution_deadline,
            time.monotonic() + ORDER_CANCEL_WINDOW_SEC)
        while time.monotonic() < cancel_deadline:
            remaining_ms = max(
                0, int((cancel_deadline - time.monotonic()) * 1000))
            resolved = observe(min(1000, remaining_ms))
            if resolved is not None:
                return resolved

    current_position = settlement_position()
    final_snapshot = orders_snapshot(
        arguments, _remaining_call_timeout(resolution_deadline, 2.0))
    resolved = _apply_recent_order_evidence(
        final_snapshot, order_id, expected_side, expected_quantity,
        baseline_position, current_position, evidence)
    if resolved is not None:
        return resolved
    final_orders = [
        int(value) for value in final_snapshot.get("active_order_ids", [])]
    detail = (
        "active order remained unresolved without economic or terminal evidence"
        if order_id in final_orders else
        "active order disappeared without economic or terminal evidence")
    if broker_reason:
        detail += f" (last broker reason {broker_reason})"
    if event_path_error:
        detail += f" ({event_path_error})"
    detail += (
        f" (filled={evidence['filled_quantity']},"
        f" remaining={evidence['remaining_quantity']},"
        f" baseline_position={baseline_position},"
        f" current_position={current_position})")
    raise RecoveryRequiredError("RECOVERY_REQUIRED: " + detail)


def quote(arguments: argparse.Namespace) -> dict[str, Any]:
    payload = tool(arguments, "market.get_quote", {"instrument": INSTRUMENT})
    bid, ask = float(payload["bid"]), float(payload["ask"])
    if not math.isfinite(bid) or not math.isfinite(ask) or bid <= 0 or ask < bid:
        raise RuntimeError("invalid quote")
    return {"observed_at_ms": int(payload["observed_at_ms"]), "bid": bid,
            "ask": ask, "mid": (bid + ask) / 2}


def fresh_quote(arguments: argparse.Namespace, attempts: int = 8) -> dict[str, Any]:
    last_error: Exception | None = None
    for _attempt in range(attempts):
        try:
            return quote(arguments)
        except Exception as error:
            last_error = error
            time.sleep(1)
    raise RuntimeError(f"fresh quote unavailable: {last_error}")


def _validated_flatten_preview(
        preview: dict[str, Any], baseline_position: float,
        baseline_position_generation: int) -> tuple[str, float]:
    authoritative = preview.get("authoritative_preview")
    if not isinstance(authoritative, dict):
        raise RecoveryRequiredError(
            "RECOVERY_REQUIRED: flatten preview lacked authoritative detail")
    side = _normalized_side(authoritative.get("side"))
    quantity_value = authoritative.get("quantity")
    position_value = authoritative.get("position_quantity")
    try:
        quantity = float(quantity_value)
        preview_position = float(position_value)
    except (TypeError, ValueError) as error:
        raise RecoveryRequiredError(
            "RECOVERY_REQUIRED: flatten preview contained invalid quantity") \
            from error
    expected_quantity = min(abs(baseline_position), float(ORDER_QUANTITY))
    preview_generation = authoritative.get("position_generation")
    if (authoritative.get("source") != "IB" or
            authoritative.get("authoritative") is not True or
            ("instrument" in authoritative and
             authoritative["instrument"] != INSTRUMENT) or
            authoritative.get("reduce_only") is not True or
            authoritative.get("risk_approved") is not True or
            authoritative.get("order_type") != "MKT" or
            not _positive_number(authoritative.get("reference_price")) or
            not _positive_number(quantity_value) or
            not _quantity_equal(quantity, expected_quantity) or
            not _quantity_equal(preview_position, baseline_position) or
            not isinstance(preview_generation, int) or
            isinstance(preview_generation, bool) or
            preview_generation != baseline_position_generation or
            side not in {"BUY", "SELL"} or
            (baseline_position > 0 and side != "SELL") or
            (baseline_position < 0 and side != "BUY")):
        raise RecoveryRequiredError(
            "RECOVERY_REQUIRED: flatten preview is not strict reduce-only")
    return side, quantity


def flatten(
        arguments: argparse.Namespace, state: dict[str, Any],
        accounting_close_kind: str | None = None) -> float:
    # The root renewal custodian uses this same lock non-blockingly.  Keep the
    # credential generation stable from the first broker-facing preview until
    # the exact order reaches authoritative terminal settlement.
    with _broker_mutation_lock():
        return _flatten_locked(arguments, state, accounting_close_kind)


def _flatten_locked(
        arguments: argparse.Namespace, state: dict[str, Any],
        accounting_close_kind: str | None = None) -> float:
    (baseline_position, baseline_position_generation,
     baseline_fx_cash_generation) = position_snapshot(
        arguments, require_generation=True)
    if _quantity_equal(baseline_position, 0.0):
        return 0.0
    preview_id = "flatten-preview-" + uuid.uuid4().hex
    preview = tool(arguments, "risk.preview_flatten", {"instrument": INSTRUMENT}, preview_id)
    command_id = str(preview["command_id"])
    side, quantity = _validated_flatten_preview(
        preview, baseline_position, baseline_position_generation)
    _record_pending_mutation(
        arguments, state, "FLATTEN_POSITION", command_id)
    try:
        placed = tool_response(arguments, "trade.flatten_position", {
            "instrument": INSTRUMENT,
            "preview_permit": preview["preview_permit"],
        }, command_id)
    except BaseException as error:
        replayed = _mutation_response(error, command_id)
        if replayed is None:
            reason = (str(error) if isinstance(error, RecoveryRequiredError) else
                      f"RECOVERY_REQUIRED: flatten outcome uncertain: {error}")
            _mark_recovery_required(arguments, state, reason)
            try:
                _ensure_recovery_halt(arguments, state)
            except Exception as halt_error:
                raise RecoveryRequiredError(
                    f"{reason}; campaign halt failed: {halt_error}") from halt_error
            raise RecoveryRequiredError(reason) from error
        placed = replayed
    try:
        raw_order_id = placed.get("order_id")
        if (not isinstance(raw_order_id, int) or
                isinstance(raw_order_id, bool) or raw_order_id < 0):
            raise RecoveryRequiredError(
                "RECOVERY_REQUIRED: flatten accepted without order_id")
    except BaseException as error:
        reason = (str(error) if isinstance(error, RecoveryRequiredError) else
                  f"RECOVERY_REQUIRED: flatten outcome uncertain: {error}")
        _mark_recovery_required(arguments, state, reason)
        try:
            _ensure_recovery_halt(arguments, state)
        except Exception as halt_error:
            raise RecoveryRequiredError(
                f"{reason}; campaign halt failed: {halt_error}") from halt_error
        raise RecoveryRequiredError(reason) from error
    state["pending_order_id"] = raw_order_id
    state["pending_order_since_ms"] = now_ms()
    state["last_order_result"] = "PENDING_FLATTEN_SETTLEMENT"
    _persist_state(arguments, state)
    resolution_deadline = time.monotonic() + min(
        max(1.0, float(arguments.fill_timeout_sec)),
        ORDER_SETTLEMENT_WINDOW_SEC + ORDER_CANCEL_WINDOW_SEC + 2.0)
    try:
        settlement = _settle_order(
            arguments, raw_order_id, side, quantity, baseline_position,
            resolution_deadline,
            baseline_position_generation=baseline_position_generation,
            baseline_fx_cash_generation=baseline_fx_cash_generation)
    except Exception as error:
        reason = (str(error) if isinstance(error, RecoveryRequiredError) else
                  f"RECOVERY_REQUIRED: flatten settlement failed: {error}")
        _mark_recovery_required(arguments, state, reason)
        _ensure_recovery_halt(arguments, state)
        raise RecoveryRequiredError(reason) from error
    if settlement != "FILLED":
        reason = "RECOVERY_REQUIRED: flatten reached non-fill terminal"
        _mark_recovery_required(arguments, state, reason)
        _ensure_recovery_halt(arguments, state)
        raise RecoveryRequiredError(reason)
    confirmed, _position_generation, _fx_cash_generation = position_snapshot(
        arguments, require_generation=True)
    expected = _expected_position(baseline_position, side, quantity)
    if not _quantity_equal(confirmed, expected) or (
            abs(confirmed) >= abs(baseline_position) and
            not _quantity_equal(confirmed, 0.0)):
        reason = "RECOVERY_REQUIRED: flatten did not strictly reduce exposure"
        _mark_recovery_required(arguments, state, reason)
        _ensure_recovery_halt(arguments, state)
        raise RecoveryRequiredError(reason)
    # Accounting-only identity, persisted only after the reduce-only fill and
    # authoritative position delta have both been confirmed.
    state["last_flatten_order_id"] = raw_order_id
    if accounting_close_kind is not None:
        try:
            close_fill = authoritative_broker_fill(
                arguments, raw_order_id, side, quantity)
            record_broker_close(
                state, close_fill,
                recovery=accounting_close_kind == "recovery")
        except Exception as error:
            reason = (
                "RECOVERY_REQUIRED: authoritative close accounting failed: "
                f"{error}")
            _mark_recovery_required(arguments, state, reason)
            _ensure_recovery_halt(arguments, state)
            raise RecoveryRequiredError(reason) from error
    _clear_pending_order(state, "ECONOMIC_FLATTEN_CONFIRMED")
    _clear_pending_mutation(state)
    _persist_state(arguments, state)
    return confirmed


def order_arguments(side: str, current_quote: dict[str, Any],
                    expires_at_ms: int) -> dict[str, Any]:
    reference_price = (
        current_quote["ask"] if side == "BUY" else current_quote["bid"])
    return {
        "instrument": INSTRUMENT, "side": side, "quantity": ORDER_QUANTITY,
        "order_type": "MKT", "tif": "DAY",
        "reference_price": reference_price,
        "expires_at_ms": expires_at_ms,
        "symbol": "EUR", "currency": "USD", "sec_type": "CASH",
        "exchange": "IDEALPRO",
    }


def enter(arguments: argparse.Namespace, state: dict[str, Any],
          decision: dict[str, Any], current_quote: dict[str, Any]) -> float | None:
    # This covers campaign open, place, terminal reconciliation, and cycle
    # close.  Renewal must never rotate/revoke this owner token mid-flight.
    with _broker_mutation_lock():
        return _enter_locked(arguments, state, decision, current_quote)


def _enter_locked(
        arguments: argparse.Namespace, state: dict[str, Any],
        decision: dict[str, Any], current_quote: dict[str, Any],
) -> float | None:
    cycle_id = "cycle-" + uuid.uuid4().hex
    intent_id = "intent-" + uuid.uuid4().hex
    expires = now_ms() + 45_000
    side = decision["action"]
    order = order_arguments(side, current_quote, expires)
    exit_plan = (
        "AI reversal, max adverse move safety, or campaign end-flat"
        if arguments.max_holding_sec == 0 else
        "AI reversal, explicit max holding timeout, max adverse move safety, "
        "or campaign end-flat")
    intent = {
        "currency": "USD", "entry_thesis": decision["rationale"][:2048],
        "exchange": "IDEALPRO", "exit_plan": exit_plan,
        "expected_slippage": max(0.0, current_quote["ask"] - current_quote["bid"]),
        "expires_at_ms": expires, "instrument": INSTRUMENT,
        "intent_id": intent_id, "invalidation_condition": "AI reversal or risk boundary",
        "reference_price": order["reference_price"],
        "max_adverse_move": 0.002, "max_holding_ms": arguments.max_holding_sec * 1000,
        "observed_ask": current_quote["ask"],
        "observed_at_ms": current_quote["observed_at_ms"],
        "observed_bid": current_quote["bid"], "order_type": "MKT",
        "paper_only": True, "quantity": ORDER_QUANTITY,
        "schema": "hepta.trade-intent.v2",
        "sec_type": "CASH", "side": side, "strategy_id": arguments.strategy_id,
        "strategy_sha256": arguments.strategy_sha256,
        "strategy_version": arguments.strategy_version, "symbol": "EUR", "tif": "DAY",
    }
    runtime = Path(arguments.runtime_dir)
    intent_path = runtime / f"{cycle_id}.intent.json"
    preflight_path = runtime / f"{cycle_id}.preflight.json"
    (baseline_position, baseline_position_generation,
     baseline_fx_cash_generation) = position_snapshot(
        arguments, require_generation=True)
    preflight_orders = active_orders(arguments)
    if not _quantity_equal(baseline_position, 0.0) or preflight_orders:
        raise RuntimeError("entry preflight is not flat and order-free")
    write_agent_json(intent_path, intent, arguments.agent_user)
    write_agent_json(preflight_path, {
        "account": tool(arguments, "account.get_summary"),
        "orders": preflight_orders,
        "position": baseline_position,
        "quote": current_quote,
    }, arguments.agent_user)
    intent_digest = "sha256:" + hashlib.sha256(canonical(intent)).hexdigest()
    outcome = "OPERATOR_ABORT"
    opened = False
    recovery_reason: str | None = None
    try:
        try:
            response = campaign(
                arguments, "open_cycle", "open-" + uuid.uuid4().hex, [
                    "--cycle-id", cycle_id, "--intent-file", str(intent_path),
                    "--preflight-file", str(preflight_path),
                ], timeout=CAMPAIGN_OPEN_TIMEOUT_SEC)
        except Exception as error:
            recovery_reason = (
                f"RECOVERY_REQUIRED: campaign open outcome uncertain: {error}")
            _mark_recovery_required(arguments, state, recovery_reason)
            try:
                _ensure_recovery_halt(arguments, state)
            except Exception as halt_error:
                raise RecoveryRequiredError(
                    f"{recovery_reason}; campaign halt failed: {halt_error}") \
                    from halt_error
            raise RecoveryRequiredError(recovery_reason) from error
        if response.get("status") != "ok":
            raise RuntimeError(f"campaign open rejected: {response}")
        opened = True
        response_state = response.get("state")
        active_deadline_at_ms = (
            response_state.get("active_deadline_at_ms")
            if isinstance(response_state, dict) else None)
        if (not isinstance(active_deadline_at_ms, int) or
                isinstance(active_deadline_at_ms, bool)):
            recovery_reason = (
                "RECOVERY_REQUIRED: campaign active deadline missing")
            _mark_recovery_required(arguments, state, recovery_reason)
            raise RecoveryRequiredError(recovery_reason)
        cycle_deadline = time.monotonic() + max(
            0.0, (active_deadline_at_ms - now_ms()) / 1000.0)
        resolution_deadline = cycle_deadline - CYCLE_FINALIZATION_MARGIN_SEC
        _remaining_call_timeout(resolution_deadline, 0.1)
        preview_id = "preview-" + uuid.uuid4().hex
        preview = tool(
            arguments, "risk.preview_order", order, preview_id,
            timeout=_remaining_call_timeout(
                resolution_deadline, IN_CYCLE_TOOL_TIMEOUT_SEC))
        command_id = str(preview["command_id"])
        _record_pending_mutation(
            arguments, state, "PLACE_ORDER", command_id)
        try:
            placed = tool_response(arguments, "trade.place_order", {
                **order, "preview_permit": preview["preview_permit"],
            }, command_id, timeout=_remaining_call_timeout(
                resolution_deadline, IN_CYCLE_TOOL_TIMEOUT_SEC))
        except BaseException as error:
            replayed = _mutation_response(error, command_id)
            if replayed is None:
                recovery_reason = (
                    f"RECOVERY_REQUIRED: place outcome uncertain: {error}")
                _mark_recovery_required(arguments, state, recovery_reason)
                raise RecoveryRequiredError(recovery_reason) from error
            placed = replayed
        # Local/API acceptance is not a final outcome. Keep the cycle uncertain
        # until authoritative position or economic execution evidence arrives.
        outcome = "PLACE_UNCERTAIN"
        raw_order_id = placed.get("order_id")
        if (not isinstance(raw_order_id, int) or
                isinstance(raw_order_id, bool) or raw_order_id < 0):
            recovery_reason = (
                "RECOVERY_REQUIRED: trade.place_order accepted without order_id")
            _mark_recovery_required(arguments, state, recovery_reason)
            raise RecoveryRequiredError(recovery_reason)
        order_id = raw_order_id
        state["pending_order_id"] = order_id
        state["pending_order_since_ms"] = now_ms()
        state["last_order_result"] = "PENDING_ECONOMIC_SETTLEMENT"
        _persist_state(arguments, state)
        try:
            settlement = _settle_order(
                arguments, order_id, side, ORDER_QUANTITY,
                baseline_position, resolution_deadline,
                baseline_position_generation=baseline_position_generation,
                baseline_fx_cash_generation=baseline_fx_cash_generation)
        except Exception as error:
            recovery_reason = (str(error) if isinstance(
                error, RecoveryRequiredError) else
                f"RECOVERY_REQUIRED: order settlement failed: {error}")
            _mark_recovery_required(arguments, state, recovery_reason)
            raise RecoveryRequiredError(recovery_reason) from error
        if settlement == "FILLED":
            confirmed_position = position_quantity(
                arguments, _remaining_call_timeout(resolution_deadline, 2.0))
            if not _position_matches(
                    confirmed_position, baseline_position, side,
                    ORDER_QUANTITY):
                recovery_reason = (
                    "RECOVERY_REQUIRED: filled order position confirmation lost")
                _mark_recovery_required(arguments, state, recovery_reason)
                raise RecoveryRequiredError(recovery_reason)
            outcome = "PLACE_ACCEPTED"
            # Accounting-only identity, persisted only after both economic
            # fill and authoritative position confirmation.
            state["entry_order_id"] = order_id
            try:
                entry_fill = authoritative_broker_fill(
                    arguments, order_id, side, ORDER_QUANTITY)
                record_broker_entry(state, entry_fill, confirmed_position)
            except Exception as error:
                recovery_reason = (
                    "RECOVERY_REQUIRED: authoritative entry accounting "
                    f"failed: {error}")
                _mark_recovery_required(
                    arguments, state, recovery_reason)
                raise RecoveryRequiredError(recovery_reason) from error
            _clear_pending_order(state, "ECONOMIC_FILL_CONFIRMED")
            _clear_pending_mutation(state)
            _persist_state(arguments, state)
            return confirmed_position
        outcome = "PLACE_REJECTED"
        _clear_pending_order(state, "BROKER_TERMINAL_NON_FILL")
        _clear_pending_mutation(state)
        _persist_state(arguments, state)
        return None
    finally:
        if opened:
            close_error: Exception | None = None
            try:
                closed = campaign(arguments, "close_cycle", "close-" + uuid.uuid4().hex, [
                    "--cycle-id", cycle_id, "--intent-sha256", intent_digest,
                    "--outcome", outcome,
                ], timeout=CAMPAIGN_FINALIZE_TIMEOUT_SEC)
                if closed.get("status") != "ok":
                    raise RuntimeError(f"campaign close rejected: {closed}")
            except Exception as error:
                close_error = error
                close_reason = (
                    f"RECOVERY_REQUIRED: campaign close failed: {error}")
                recovery_reason = (
                    f"{recovery_reason}; {close_reason}"
                    if recovery_reason else close_reason)
                _mark_recovery_required(arguments, state, recovery_reason)
            if recovery_reason is not None:
                try:
                    _ensure_recovery_halt(arguments, state)
                except Exception as error:
                    raise RecoveryRequiredError(
                        f"{recovery_reason}; campaign halt failed: {error}") \
                        from error
            if close_error is not None:
                raise RecoveryRequiredError(recovery_reason or str(close_error)) \
                    from close_error


def empty_state() -> dict[str, Any]:
    return {"schema": SCHEMA, "decisions": 0, "entries": 0, "exits": 0,
            "realized_pnl_estimate": 0.0, "entry_mid": None,
            "entry_quantity": 0.0, "entry_at_ms": None,
            "entry_order_id": None, "last_flatten_order_id": None,
            "entry_fill_price": None,
            "entry_price_basis": None,
            "open_broker_fill": None,
            "processed_broker_fills": [],
            "broker_fill_entries": 0, "broker_fill_exits": 0,
            "closed_trades": 0, "strategy_closed_trades": 0,
            "recovery_broker_fill_exits": 0,
            "recovery_closed_trades": 0,
            "realized_gross_pnl": 0.0,
            "realized_gross_pnl_quote_currency": "USD",
            "unrealized_gross_pnl_estimate": 0.0,
            "fees_known": False, "realized_fees": None,
            "realized_net_pnl": None,
            # This is deliberately separate from realized_pnl_estimate.  The
            # latter is a strategy mid-mark estimate; recovery accounting is
            # broker fill-to-fill and never implies commission-adjusted PnL.
            "recovery_raw_price_pnl": None,
            "recovery_raw_price_pnl_quote_currency": None,
            "recovery_raw_price_pnl_commission_included": False,
            "recovery_raw_price_pnl_evidence": None,
            "history": [], "last_decision": None, "last_error": None,
            "last_exit_trigger": None,
            "recovery_required": False, "recovery_reason": None,
            "recovery_halt_confirmed": False, "pending_order_id": None,
            "pending_order_since_ms": None, "last_order_result": None,
            "pending_mutation_kind": None,
            "pending_mutation_command_id": None,
            "pending_mutation_recorded_at_ms": None,
            "pending_mutation_token_name": None,
            "pending_mutation_token_sha256": None,
            "pending_mutation_state_unproven": False,
            "trading_suspended": False, "suspension_code": None,
            "suspension_id": None, "suspended_at_ms": None,
            "auth_generation_at_suspend": None,
            "campaign_id_at_suspend": None,
            "auth_generation_rearmed": None,
            "auth_profile_sha256_rearmed": None,
            "auth_profile_allowlist_sha256_rearmed": None,
            "auth_rearm_receipt_sha256": None,
            "runtime_binding": None,
            "manual_start_required": False,
            "manual_started_at_ms": None,
            "model_request_rate_limit_count": 0,
            "last_model_request_rate_limit_at_ms": None,
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
            "immediate_flat_observed_at_ms": None,
            "immediate_flat_position_generation": None,
            "immediate_flat_fx_cash_generation": None,
            "immediate_flat_gross_absolute_position": None,
            "incident_pending_order_id": None,
            "recovery_phase": None, "recovery_complete": False,
            "recovery_receipt_sha256": None}


def normalize_history(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    by_observation: dict[int, dict[str, Any]] = {}
    for item in value:
        if not isinstance(item, dict):
            continue
        observed_at_ms = item.get("observed_at_ms")
        if (not isinstance(observed_at_ms, int) or
                isinstance(observed_at_ms, bool) or observed_at_ms <= 0):
            continue
        try:
            bid = float(item["bid"])
            ask = float(item["ask"])
            mid = float(item["mid"])
        except (KeyError, TypeError, ValueError):
            continue
        if (not all(math.isfinite(number) for number in (bid, ask, mid)) or
                bid <= 0 or ask < bid):
            continue
        by_observation[observed_at_ms] = {
            "observed_at_ms": observed_at_ms,
            "bid": bid, "ask": ask, "mid": mid,
        }
    return [by_observation[key] for key in sorted(by_observation)][-HISTORY_LIMIT:]


def append_quote_sample(history: Any,
                        current: dict[str, Any]) -> list[dict[str, Any]]:
    samples = list(history) if isinstance(history, list) else []
    samples.append(current)
    return normalize_history(samples)


def load_state(path: Path) -> dict[str, Any]:
    state = empty_state()
    if not path.exists():
        return state
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise RuntimeError("local AI PAPER state is not an object")
    preserved = (
        "decisions", "entries", "exits", "realized_pnl_estimate", "entry_mid",
        "entry_quantity", "entry_at_ms", "entry_order_id",
        "last_flatten_order_id", "position", "quote", "updated_at",
        "entry_fill_price", "entry_price_basis", "open_broker_fill",
        "processed_broker_fills", "broker_fill_entries",
        "broker_fill_exits", "closed_trades", "strategy_closed_trades",
        "recovery_broker_fill_exits", "recovery_closed_trades",
        "realized_gross_pnl", "realized_gross_pnl_quote_currency",
        "unrealized_gross_pnl_estimate",
        "fees_known", "realized_fees", "realized_net_pnl",
        "recovery_raw_price_pnl",
        "recovery_raw_price_pnl_quote_currency",
        "recovery_raw_price_pnl_commission_included",
        "recovery_raw_price_pnl_evidence",
        "recovery_required", "recovery_reason", "recovery_halt_confirmed",
        "pending_order_id", "pending_order_since_ms", "last_order_result",
        "pending_mutation_kind", "pending_mutation_command_id",
        "pending_mutation_recorded_at_ms",
        "pending_mutation_token_name", "pending_mutation_token_sha256",
        "pending_mutation_state_unproven",
        "last_exit_trigger", "trading_suspended", "suspension_code",
        "suspension_id", "suspended_at_ms", "auth_generation_at_suspend",
        "campaign_id_at_suspend", "auth_generation_rearmed",
        "auth_profile_sha256_rearmed",
        "auth_profile_allowlist_sha256_rearmed",
        "auth_rearm_receipt_sha256",
        "runtime_binding",
        "manual_start_required", "manual_started_at_ms",
        "manual_start_permit_id", "manual_start_invocation_id",
        "model_request_rate_limit_count",
        "last_model_request_rate_limit_at_ms",
        "model_attempt_count", "model_timeout_count",
        "model_contract_failure_count", "model_transport_failure_count",
        "model_consecutive_failures", "last_model_failure_at_ms",
        "last_model_failure_code", "next_model_attempt_after_ms",
        "model_attempt_in_flight", "model_attempt_started_at_ms",
        "model_attempt_position", "model_attempt_sample_observed_at_ms",
        "immediate_flat_observed_at_ms",
        "immediate_flat_position_generation",
        "immediate_flat_fx_cash_generation",
        "immediate_flat_gross_absolute_position",
        "incident_pending_order_id",
        "recovery_phase", "recovery_complete", "recovery_receipt_sha256",
    )
    if value.get("schema") == LEGACY_SCHEMA:
        for key in preserved:
            if key in value:
                state[key] = value[key]
    else:
        if value.get("schema") not in {PREVIOUS_SCHEMA, SCHEMA}:
            raise RuntimeError("local AI PAPER state schema unsupported")
        for key in (*preserved, "last_decision", "last_error"):
            if key in value:
                state[key] = value[key]
    state["history"] = normalize_history(value.get("history"))
    state["schema"] = SCHEMA
    # A worker thread cannot survive a process restart. Treat the persisted
    # marker as terminal-uncertain rather than creating a second model session.
    if (state.get("model_attempt_in_flight") is True or
            state.get("last_model_failure_code") ==
            MODEL_ATTEMPT_TERMINAL_UNCERTAIN):
        state["model_attempt_in_flight"] = False
        state["trading_suspended"] = True
        state["suspension_code"] = MODEL_ATTEMPT_TERMINAL_UNCERTAIN
        state["recovery_required"] = True
        state["recovery_reason"] = (
            "RECOVERY_REQUIRED: model attempt has no durable Gateway terminal "
            "proof after agent restart")
        state["last_error"] = state["recovery_reason"]
    pending_order_id = state.get("pending_order_id")
    if (isinstance(pending_order_id, int) and
            not isinstance(pending_order_id, bool) and pending_order_id >= 0 and
            state.get("recovery_required") is not True):
        state["recovery_required"] = True
        state["recovery_reason"] = (
            "RECOVERY_REQUIRED: pending order survived agent restart")
        state["recovery_halt_confirmed"] = False
        state["last_error"] = state["recovery_reason"]
    return state


def verified_manual_start_permit(
        arguments: argparse.Namespace, state: dict[str, Any],
) -> tuple[str, str]:
    metadata = os.lstat(START_PERMIT_CONSUMED)
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
            metadata.st_uid != 0 or metadata.st_gid != 0 or
            stat.S_IMODE(metadata.st_mode) != 0o600 or
            not 256 <= metadata.st_size <= 16_384):
        raise RuntimeError("manual start permit metadata is invalid")
    raw = START_PERMIT_CONSUMED.read_bytes()
    value = json.loads(raw)
    invocation_id = os.environ.get("INVOCATION_ID", "")
    now = now_ms()
    expected_keys = {
        "schema", "permit_id", "unit", "boot_id", "issued_at_ms",
        "not_after_ms", "campaign_id", "policy_sha256",
        "agent_env_sha256", "state_sha256", "deadline_timer_sha256",
        "strategy_acceptance_sha256", "auth_rearm_receipt_sha256",
        "prelaunch_zero_receipt_sha256", "runtime_binding",
        "policy_expires_at_ms", "manual_start_required", "paper_only",
        "live_authorized", "phase", "invocation_id", "consumed_at_ms",
    }
    digest_fields = (
        "policy_sha256", "agent_env_sha256", "state_sha256",
        "deadline_timer_sha256", "strategy_acceptance_sha256",
        "auth_rearm_receipt_sha256", "prelaunch_zero_receipt_sha256",
    )
    if (not isinstance(value, dict) or
            set(value) != expected_keys or
            (json.dumps(
                value, ensure_ascii=True, sort_keys=True,
                separators=(",", ":"), allow_nan=False) + "\n").encode(
                    "ascii") != raw or
            value.get("schema") != "hepta.local-ai-paper-start-permit.v1" or
            value.get("phase") != "CONSUMED" or
            value.get("unit") != "hepta-local-ai-paper-agent.service" or
            value.get("campaign_id") != str(arguments.campaign_id) or
            value.get("runtime_binding") != state.get("runtime_binding") or
            value.get("invocation_id") != invocation_id or
            re.fullmatch(r"[0-9a-f]{32}", invocation_id) is None or
            not isinstance(value.get("permit_id"), str) or
            re.fullmatch(r"[0-9a-f]{64}", value.get("permit_id", "")) is None or
            any(re.fullmatch(
                r"sha256:[0-9a-f]{64}", str(value.get(key, ""))) is None
                for key in digest_fields) or
            not isinstance(value.get("issued_at_ms"), int) or
            isinstance(value.get("issued_at_ms"), bool) or
            not isinstance(value.get("not_after_ms"), int) or
            isinstance(value.get("not_after_ms"), bool) or
            not value.get("issued_at_ms", now + 1) <= now <=
                value.get("not_after_ms", now - 1) or
            value.get("manual_start_required") is not True or
            value.get("paper_only") is not True or
            value.get("live_authorized") is not False):
        raise RuntimeError("manual start permit is invalid")
    return str(value["permit_id"]), invocation_id


def adverse_move_reached(
        quantity: float, entry_mid: Any, current_mid: Any,
        maximum_move: float = MAX_ADVERSE_MOVE) -> bool:
    try:
        entry = float(entry_mid)
        current = float(current_mid)
        boundary = float(maximum_move)
    except (TypeError, ValueError):
        return False
    if (not all(math.isfinite(value) for value in (entry, current, boundary)) or
            entry <= 0 or current <= 0 or boundary <= 0):
        return False
    return ((quantity > 0 and current <= entry - boundary) or
            (quantity < 0 and current >= entry + boundary))


def _strategy_exit_record(
        arguments: argparse.Namespace, state: dict[str, Any], quantity: float,
        trigger: str, execution_quote: dict[str, Any],
        decision: dict[str, Any] | None) -> dict[str, Any]:
    record: dict[str, Any] = {
        "schema": "hepta.local-ai-paper-exit-trigger.v1",
        "trigger": trigger,
        "triggered_at_ms": now_ms(),
        "strategy_id": str(getattr(arguments, "strategy_id", "")),
        "strategy_version": str(getattr(arguments, "strategy_version", "")),
        "strategy_sha256": str(getattr(arguments, "strategy_sha256", "")),
        "position_before": quantity,
        "entry_mid": state.get("entry_mid"),
        "entry_at_ms": state.get("entry_at_ms"),
        "observed_at_ms": execution_quote["observed_at_ms"],
        "observed_bid": execution_quote["bid"],
        "observed_ask": execution_quote["ask"],
        "decision": None if decision is None else {
            "action": decision["action"],
            "confidence": float(decision["confidence"]),
            "rationale": str(decision["rationale"])[:2048],
        },
        "result": "PENDING_FLATTEN_SETTLEMENT",
    }
    record["trigger_sha256"] = (
        "sha256:" + hashlib.sha256(canonical(record)).hexdigest())
    return record


def _close_strategy_position(
        arguments: argparse.Namespace, state: dict[str, Any], quantity: float,
        trigger: str, decision: dict[str, Any] | None = None) -> float:
    execution_quote = fresh_quote(arguments)
    state["last_exit_trigger"] = _strategy_exit_record(
        arguments, state, quantity, trigger, execution_quote, decision)
    _persist_state(arguments, state)
    confirmed_position = flatten(arguments, state, "strategy")
    if _quantity_equal(confirmed_position, 0.0):
        # Persist the economically confirmed flat snapshot in the same write
        # as the exit receipt.  Otherwise observers can briefly see a stale
        # non-zero state["position"] until the next sampling loop even though
        # IB and the receipt already agree that the position is closed.
        state["position"] = confirmed_position
        state["unrealized_pnl_estimate"] = 0.0
        state["entry_mid"] = None
        state["entry_fill_price"] = None
        state["entry_price_basis"] = None
        state["entry_quantity"] = 0.0
        state["entry_at_ms"] = None
        state["entry_order_id"] = None
        state["exits"] = int(state.get("exits", 0)) + 1
        exit_record = state.get("last_exit_trigger")
        if isinstance(exit_record, dict):
            exit_record["result"] = "ECONOMIC_FLATTEN_CONFIRMED"
            exit_record["confirmed_at_ms"] = now_ms()
            exit_record["position_after"] = confirmed_position
        _persist_state(arguments, state)
    return confirmed_position


def apply_decision(arguments: argparse.Namespace, state: dict[str, Any],
                   quantity: float, decision: dict[str, Any],
                   holding_expired: bool) -> None:
    if state.get("recovery_required") is True:
        return
    explicit_timeout = bool(
        holding_expired and int(getattr(arguments, "max_holding_sec", 0)) > 0)
    opposite = ((quantity > 0 and decision["action"] == "SELL") or
                (quantity < 0 and decision["action"] == "BUY"))
    confident_reversal = (
        opposite and float(decision["confidence"]) >= arguments.confidence)
    if quantity and (explicit_timeout or confident_reversal):
        trigger = (
            "MAX_HOLDING_TIMEOUT" if explicit_timeout else "MODEL_REVERSAL")
        _close_strategy_position(
            arguments, state, quantity, trigger,
            None if explicit_timeout else decision)
        return
    if (quantity or decision["action"] not in {"BUY", "SELL"} or
            float(decision["confidence"]) < arguments.confidence or
            active_orders(arguments)):
        return
    execution_quote = fresh_quote(arguments)
    confirmed_position = enter(arguments, state, decision, execution_quote)
    if confirmed_position is None:
        return
    expected_position = (
        ORDER_QUANTITY if decision["action"] == "BUY" else -ORDER_QUANTITY)
    if not _quantity_equal(confirmed_position, expected_position):
        reason = "RECOVERY_REQUIRED: entry position confirmation mismatch"
        _mark_recovery_required(arguments, state, reason)
        raise RecoveryRequiredError(reason)
    # Tests/legacy execution seams may not project the broker fill themselves.
    # Production enter() always records it durably before returning.
    if not isinstance(state.get("open_broker_fill"), dict):
        state["entries"] = int(state.get("entries", 0)) + 1
        state["entry_mid"] = execution_quote["mid"]
    state["entry_quantity"] = confirmed_position
    state["entry_at_ms"] = now_ms()


def _model_request_backoff_active(
        state: dict[str, Any], decision_seconds: int,
        current_ms: int | None = None) -> bool:
    observed_ms = now_ms() if current_ms is None else current_ms
    next_attempt_after_ms = state.get("next_model_attempt_after_ms")
    if (isinstance(next_attempt_after_ms, int) and
            not isinstance(next_attempt_after_ms, bool) and
            next_attempt_after_ms > 0 and observed_ms < next_attempt_after_ms):
        return True
    limited_at_ms = state.get("last_model_request_rate_limit_at_ms")
    if (not isinstance(limited_at_ms, int) or isinstance(limited_at_ms, bool) or
            limited_at_ms <= 0 or decision_seconds <= 0):
        return False
    elapsed_ms = observed_ms - limited_at_ms
    # A backward wall-clock step must not bypass the persisted throttle.
    return elapsed_ms < 0 or elapsed_ms < decision_seconds * 1000


def _begin_model_attempt(
        arguments: argparse.Namespace, state: dict[str, Any],
        history: list[dict[str, Any]], quantity: float,
        pnl: dict[str, float]) -> ModelAttemptWorker:
    state["model_attempt_count"] = int(
        state.get("model_attempt_count", 0)) + 1
    state["model_attempt_in_flight"] = True
    state["model_attempt_started_at_ms"] = now_ms()
    state["model_attempt_position"] = float(quantity)
    state["model_attempt_sample_observed_at_ms"] = int(
        history[-1]["observed_at_ms"])
    _persist_state(arguments, state)
    return ModelAttemptWorker(arguments, history, quantity, pnl)


def _complete_model_attempt(
        arguments: argparse.Namespace, state: dict[str, Any],
        decision_seconds: int, failure: BaseException | None) -> None:
    completed_at_ms = now_ms()
    state["model_attempt_in_flight"] = False
    state["model_attempt_started_at_ms"] = None
    state["model_attempt_position"] = None
    state["model_attempt_sample_observed_at_ms"] = None
    state["next_model_attempt_after_ms"] = (
        completed_at_ms + max(0, int(decision_seconds)) * 1000)
    if failure is None:
        state["model_consecutive_failures"] = 0
        state["last_error"] = None
    else:
        if isinstance(failure, ModelAttemptFailure):
            code = failure.failure_code
        elif isinstance(failure, SafetyStopError):
            code = failure.suspension_code
        else:
            code = MODEL_ATTEMPT_TRANSPORT
        state["model_consecutive_failures"] = int(
            state.get("model_consecutive_failures", 0)) + 1
        state["last_model_failure_at_ms"] = completed_at_ms
        state["last_model_failure_code"] = code
        if code in {MODEL_ATTEMPT_TIMEOUT, MODEL_ATTEMPT_TERMINAL_UNCERTAIN}:
            state["model_timeout_count"] = int(
                state.get("model_timeout_count", 0)) + 1
        elif code == MODEL_ATTEMPT_CONTRACT_INVALID:
            state["model_contract_failure_count"] = int(
                state.get("model_contract_failure_count", 0)) + 1
        elif code == MODEL_REQUEST_RATE_LIMIT:
            state["model_request_rate_limit_count"] = int(
                state.get("model_request_rate_limit_count", 0)) + 1
            state["last_model_request_rate_limit_at_ms"] = completed_at_ms
        else:
            state["model_transport_failure_count"] = int(
                state.get("model_transport_failure_count", 0)) + 1
        state["last_error"] = str(failure)[:1000]
    state["updated_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    _persist_state(arguments, state)


def _attempt_immediate_auth_safety_flatten(
        arguments: argparse.Namespace, state: dict[str, Any],
        suspension_code: str) -> bool:
    """Immediately reduce a model-auth incident before the process exits.

    This is deliberately only the first, in-process risk-reduction attempt.
    The root-owned recovery custodian still reconciles orders and produces the
    double authoritative 0/0/0 receipt after exit.  An active order makes an
    opposite market order unsafe, so that case remains latched for the
    cancel/reconcile path instead of guessing at exposure.
    """
    if suspension_code not in AUTH_SAFETY_STOP_CODES:
        return False
    state["recovery_phase"] = "IMMEDIATE_RISK_REDUCTION"
    _persist_state(arguments, state)
    active = active_orders(arguments, timeout=5)
    if active:
        raise RecoveryRequiredError(
            "RECOVERY_REQUIRED: auth safety stop has active orders requiring "
            f"cancel/reconcile: {sorted(active)}")
    flattened = flatten(arguments, state)
    remaining_orders = active_orders(arguments, timeout=5)
    (position, position_generation,
     fx_cash_generation) = position_snapshot(
        arguments, timeout=5, require_generation=True)
    risk = tool(arguments, "risk.get_limits", timeout=5)
    try:
        gross = float(risk.get("gross_absolute_position"))
    except (TypeError, ValueError) as error:
        raise RecoveryRequiredError(
            "RECOVERY_REQUIRED: auth safety flatten gross proof invalid") \
            from error
    if (remaining_orders or not _quantity_equal(flattened, 0.0) or
            not _quantity_equal(position, 0.0) or
            not math.isfinite(gross) or not _quantity_equal(gross, 0.0)):
        raise RecoveryRequiredError(
            "RECOVERY_REQUIRED: immediate auth safety flatten did not prove "
            "position=0 active_orders=0 gross=0")
    state["position"] = 0.0
    state["active_order_ids"] = []
    state["gross_absolute_position"] = 0.0
    state["immediate_flat_observed_at_ms"] = now_ms()
    state["immediate_flat_position_generation"] = position_generation
    state["immediate_flat_fx_cash_generation"] = fx_cash_generation
    state["immediate_flat_gross_absolute_position"] = 0.0
    state["recovery_phase"] = "IMMEDIATE_FLAT_OBSERVED"
    state["recovery_complete"] = False
    state["last_order_result"] = "IMMEDIATE_AUTH_SAFETY_FLAT_OBSERVED"
    _persist_state(arguments, state)
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--domain", default="alpha")
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--strategy-id", required=True)
    parser.add_argument("--strategy-version", default="1")
    parser.add_argument("--strategy-sha256", required=True)
    parser.add_argument("--agent-user", default="hepta-agent-alpha")
    parser.add_argument("--model-user", default="qian-qi")
    parser.add_argument("--openclaw-agent", default="telegram-bot-8681289317")
    parser.add_argument("--model", default="codex/gpt-5.3-codex-spark")
    parser.add_argument("--model-session-key", default="agent:telegram-bot-8681289317:hepta-local-paper-decision")
    parser.add_argument("--auth-generation", default="unversioned")
    parser.add_argument("--auth-profile-id", required=True)
    parser.add_argument("--auth-profile-allowlist-sha256", required=True)
    parser.add_argument("--heptactl", default="/usr/bin/heptactl")
    parser.add_argument("--campaignctl", default="/usr/bin/hepta-campaignctl")
    parser.add_argument("--tool-socket", default="/run/hepta-agent-alpha/tools.sock")
    parser.add_argument("--token-file", default="/run/hepta-agent-alpha/sessions/local-paper.token")
    parser.add_argument("--runtime-dir", default="/run/hepta-local-ai-paper-agent")
    parser.add_argument("--state-file", type=Path, default=Path("/var/lib/hepta-local-ai-paper-agent/state.json"))
    parser.add_argument("--sample-sec", type=int, default=10)
    parser.add_argument("--decision-sec", type=int, default=120)
    parser.add_argument("--confidence", type=float, default=0.62)
    parser.add_argument(
        "--max-holding-sec", type=int, default=0,
        help=("fixed holding timeout; 0 disables time-based exits so the "
              "model controls normal strategy exits"))
    parser.add_argument("--fill-timeout-sec", type=int, default=30)
    arguments = parser.parse_args()
    if arguments.max_holding_sec < 0:
        parser.error("--max-holding-sec must be non-negative")
    try:
        configured_profile_sha256 = auth_profile_sha256(
            arguments.auth_profile_id)
    except ValueError as error:
        parser.error(str(error))
    if not re.fullmatch(
            r"sha256:[0-9a-f]{64}",
            arguments.auth_profile_allowlist_sha256):
        parser.error(
            "--auth-profile-allowlist-sha256 must be sha256:<64 lowercase hex>")
    state = load_state(arguments.state_file)
    rearmed_generation = state.get("auth_generation_rearmed")
    if (rearmed_generation != arguments.auth_generation and
            state.get("trading_suspended") is not True and
            state.get("recovery_required") is not True):
        try:
            _mark_trading_suspended(
                arguments, state, MODEL_AUTH_UNUSABLE,
                "MODEL_AUTH_UNUSABLE: configured auth generation was not rearmed")
        except Exception as persist_error:
            print(
                "local AI PAPER safety stop: auth generation latch "
                f"persistence failed: {persist_error}", flush=True)
            return SAFETY_STOP_EXIT_STATUS
    rearmed_profile_sha256 = state.get("auth_profile_sha256_rearmed")
    if (rearmed_profile_sha256 != configured_profile_sha256 and
            state.get("trading_suspended") is not True and
            state.get("recovery_required") is not True):
        try:
            _mark_trading_suspended(
                arguments, state, MODEL_AUTH_UNUSABLE,
                "MODEL_AUTH_UNUSABLE: configured auth profile was not rearmed")
        except Exception as persist_error:
            print(
                "local AI PAPER safety stop: auth profile latch "
                f"persistence failed: {persist_error}", flush=True)
            return SAFETY_STOP_EXIT_STATUS
    rearmed_profile_allowlist_sha256 = state.get(
        "auth_profile_allowlist_sha256_rearmed")
    if (rearmed_profile_allowlist_sha256 !=
            arguments.auth_profile_allowlist_sha256 and
            state.get("trading_suspended") is not True and
            state.get("recovery_required") is not True):
        try:
            _mark_trading_suspended(
                arguments, state, MODEL_AUTH_UNUSABLE,
                "MODEL_AUTH_UNUSABLE: configured auth profile allowlist was "
                "not rearmed")
        except Exception as persist_error:
            print(
                "local AI PAPER safety stop: auth profile allowlist latch "
                f"persistence failed: {persist_error}", flush=True)
            return SAFETY_STOP_EXIT_STATUS
    if (state.get("trading_suspended") is not True and
            state.get("recovery_required") is not True):
        try:
            require_runtime_binding(arguments, state)
        except SafetyStopError as error:
            try:
                _mark_trading_suspended(
                    arguments, state, error.suspension_code, str(error))
            except Exception as persist_error:
                print(
                    "local AI PAPER safety stop: runtime binding latch "
                    f"persistence failed: {persist_error}", flush=True)
                return SAFETY_STOP_EXIT_STATUS
    if (state.get("manual_start_required") is True and
            state.get("trading_suspended") is not True and
            state.get("recovery_required") is not True):
        try:
            permit_id, invocation_id = verified_manual_start_permit(
                arguments, state)
        except Exception as permit_error:
            try:
                _mark_trading_suspended(
                    arguments, state, ORDER_STATE_UNCERTAIN,
                    "RECOVERY_REQUIRED: one-shot manual start permit was "
                    f"missing, expired, or invalid: {permit_error}")
            except Exception as persist_error:
                print(
                    "local AI PAPER safety stop: invalid start permit latch "
                    f"persistence failed: {persist_error}", flush=True)
            return SAFETY_STOP_EXIT_STATUS
        state["manual_start_required"] = False
        state["manual_started_at_ms"] = now_ms()
        state["manual_start_permit_id"] = permit_id
        state["manual_start_invocation_id"] = invocation_id
        state["last_order_result"] = "AUTH_REARM_MANUAL_START_CONSUMED"
        try:
            _persist_state(arguments, state)
        except Exception as persist_error:
            print(
                "local AI PAPER safety stop: manual-start receipt "
                f"persistence failed: {persist_error}", flush=True)
            return SAFETY_STOP_EXIT_STATUS
    model_attempt: ModelAttemptWorker | None = None
    while True:
        if (state.get("trading_suspended") is True or
                state.get("recovery_required") is True):
            reason = str(
                state.get("recovery_reason") or
                "RECOVERY_REQUIRED: persisted safety stop")
            code = str(
                state.get("suspension_code") or ORDER_STATE_UNCERTAIN)
            if state.get("trading_suspended") is not True:
                try:
                    _mark_trading_suspended(arguments, state, code, reason)
                except Exception as persist_error:
                    print(
                        "local AI PAPER safety stop: persisted incident latch "
                        f"failed: {persist_error}", flush=True)
                    return SAFETY_STOP_EXIT_STATUS
            if state.get("recovery_halt_confirmed") is not True:
                try:
                    _ensure_recovery_halt(arguments, state)
                except Exception as halt_error:
                    state["last_error"] = (
                        f"{reason}; campaign halt failed: {halt_error}")[:1000]
                    _persist_state(arguments, state)
            print(
                f"local AI PAPER safety stop: {code}; operator recovery required",
                flush=True)
            return SAFETY_STOP_EXIT_STATUS
        try:
            require_runtime_binding(arguments, state)
            current = quote(arguments)
            history = append_quote_sample(state.get("history"), current)
            state["history"] = history
            quantity = position_quantity(arguments)
            if quantity and state.get("entry_mid") is None:
                state["entry_mid"] = current["mid"]
                state["entry_quantity"] = quantity
                state["entry_at_ms"] = now_ms()
            unrealized = 0.0
            if quantity and state.get("entry_mid") is not None:
                # Gross liquidation estimate: use the executable side of the
                # quote against the authoritative broker entry fill.  Fees are
                # still unknown and no net value is published.
                liquidation_price = (
                    current["bid"] if quantity > 0 else current["ask"])
                unrealized = (
                    liquidation_price - float(state["entry_mid"])) * quantity
            pnl = {"realized_pnl_estimate": float(state.get("realized_pnl_estimate", 0.0)),
                   "unrealized_pnl_estimate": unrealized,
                   "unrealized_gross_pnl_estimate": unrealized,
                   "fees_known": False, "realized_fees": None,
                   "realized_net_pnl": None}
            state.update({"position": quantity, "quote": current, **pnl,
                          "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                          })
            write_json(arguments.state_file, state)
            holding_expired = bool(
                arguments.max_holding_sec > 0 and quantity and
                state.get("entry_at_ms") and
                now_ms() - int(state["entry_at_ms"]) >= arguments.max_holding_sec * 1000)
            adverse_exit = bool(quantity and adverse_move_reached(
                quantity, state.get("entry_mid"), current["mid"]))
            if holding_expired or adverse_exit:
                _close_strategy_position(
                    arguments, state, quantity,
                    "MAX_HOLDING_TIMEOUT" if holding_expired else
                    "MAX_ADVERSE_MOVE")
                write_json(arguments.state_file, state)
                continue
            if model_attempt is not None and model_attempt.done():
                failure: BaseException | None = None
                decision: dict[str, Any] | None = None
                try:
                    decision = model_attempt.result()
                except BaseException as error:
                    failure = error
                if (failure is None and not _quantity_equal(
                        quantity, model_attempt.position)):
                    failure = ModelAttemptFailure(
                        MODEL_ATTEMPT_CONTRACT_INVALID,
                        "MODEL_ATTEMPT_CONTRACT_INVALID: authoritative "
                        "position changed while decision was in flight")
                result_age_ms = now_ms() - model_attempt.history_observed_at_ms
                if (failure is None and (
                        result_age_ms < 0 or
                        result_age_ms > MODEL_RESULT_MAX_AGE_MS)):
                    failure = ModelAttemptFailure(
                        MODEL_ATTEMPT_CONTRACT_INVALID,
                        "MODEL_ATTEMPT_CONTRACT_INVALID: decision market "
                        "snapshot expired while model was in flight")
                _complete_model_attempt(
                    arguments, state, arguments.decision_sec, failure)
                model_attempt = None
                if failure is not None:
                    if not isinstance(failure, Exception):
                        raise failure
                    if isinstance(failure, SafetyStopError):
                        raise failure
                    print(
                        f"local AI PAPER model attempt HOLD: {failure}",
                        flush=True)
                elif decision is not None:
                    state["decisions"] = int(state.get("decisions", 0)) + 1
                    state["last_decision"] = {
                        **decision, "at_ms": now_ms()}
                    apply_decision(
                        arguments, state, quantity, decision, holding_expired)
                    _persist_state(arguments, state)
            due = not _model_request_backoff_active(
                state, arguments.decision_sec)
            if model_attempt is None and due and len(history) >= 6:
                model_attempt = _begin_model_attempt(
                    arguments, state, history, quantity, pnl)
        except Exception as error:
            suspension_code = _suspension_code(error)
            if isinstance(error, RecoveryRequiredError) or suspension_code:
                code = suspension_code or ORDER_STATE_UNCERTAIN
                try:
                    _mark_trading_suspended(
                        arguments, state, code, str(error)[:1000])
                except Exception as persist_error:
                    print(
                        "local AI PAPER safety stop: incident persistence "
                        f"failed: {persist_error}", flush=True)
                    return SAFETY_STOP_EXIT_STATUS
                halt_error: Exception | None = None
                try:
                    if state.get("recovery_halt_confirmed") is not True:
                        _ensure_recovery_halt(arguments, state)
                except Exception as caught_halt_error:
                    halt_error = caught_halt_error
                immediate_error: Exception | None = None
                if code in AUTH_SAFETY_STOP_CODES:
                    try:
                        _attempt_immediate_auth_safety_flatten(
                            arguments, state, code)
                    except Exception as caught_immediate_error:
                        immediate_error = caught_immediate_error
                details = [str(error)]
                if halt_error is not None:
                    details.append(f"campaign halt failed: {halt_error}")
                if immediate_error is not None:
                    details.append(
                        f"immediate risk reduction deferred: {immediate_error}")
                state["last_error"] = "; ".join(details)[:1000]
                _persist_state(arguments, state)
                print(
                    f"local AI PAPER safety stop: {code}; {error}",
                    flush=True)
                return SAFETY_STOP_EXIT_STATUS
            if isinstance(error, ModelRequestRateLimitError):
                state["model_request_rate_limit_count"] = int(
                    state.get("model_request_rate_limit_count", 0)) + 1
                state["last_model_request_rate_limit_at_ms"] = now_ms()
            state["last_error"] = str(error)[:1000]
            state["updated_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
            write_json(arguments.state_file, state)
            print(f"local AI PAPER cycle error: {error}", flush=True)
        time.sleep(arguments.sample_sec)


if __name__ == "__main__":
    raise SystemExit(main())
