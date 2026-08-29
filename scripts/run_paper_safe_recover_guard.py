#!/usr/bin/env python3
"""Route a latched PAPER incident to risk-only recovery before normal restart."""

from __future__ import annotations

import json
import hashlib
import fcntl
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time


STATE = Path("/var/lib/hepta-local-ai-paper-agent/state.json")
POLICY_FILE = Path("/etc/heptatrader/paper-campaigns/alpha.json")
LOCAL_PAPER_CONTROL = "/usr/libexec/hepta-local-paper-control"
EXTERNAL_CANARY_ROOT = Path("/var/lib/hepta/p1-paper-canary")
EXTERNAL_RECOVERY_AUTHORITY = Path(
    "/var/lib/hepta-local-ai-paper-agent/"
    "local-paper-control-recovery-authority.json")
SAFETY_LATCH = STATE.parent / "safety-stop.pending.json"
# This lock only suppresses overlapping timer invocations.  Campaign mutation
# serialization is owned by run_paper_repair's lifecycle -> risk -> end-flat
# lock chain; reusing that first lock here would deadlock the child recovery
# transaction while the guard waits for it.
SAFE_RECOVERY_GUARD_LOCK = STATE.parent / "safe-recovery-invocation.lock"
AUTOMATIC_RISK_ATTEMPT = (
    STATE.parent / "safe-recovery-automatic-risk-attempt.json")
SESSION_AUTHORITY_ROOT = STATE.parent / "session-authority"
START_PERMIT_PENDING = STATE.parent / "start-permit.pending.json"
START_PERMIT_CLAIMED = STATE.parent / "start-permit.claimed.json"
START_PERMIT_CONSUMED = STATE.parent / "start-permit.consumed.json"
AGENT_SERVICE = "hepta-local-ai-paper-agent.service"
CAMPAIGN_BACKGROUND_TIMERS = (
    "hepta-local-paper-supervisor.timer",
    "hepta-local-paper-session-renew.timer",
    "hepta-local-paper-safe-recover.timer",
)
RISK_RECOVER = ["/usr/libexec/hepta-local-paper-repair", "risk-recover"]
NORMAL_RECOVER = ["/usr/libexec/hepta-local-paper-safe-recover"]
REQUEST_END_FLAT = [
    "/usr/libexec/hepta-local-paper-repair", "request-end-flat"]
REQUEST_ORPHAN_END_FLAT = [
    "/usr/libexec/hepta-local-paper-repair",
    "request-end-flat-if-orphan-start"]
END_FLAT_SERVICE = "hepta-local-ai-paper-24h-stop.service"
RISK_RECOVER_TIMEOUT_SECONDS = 240
NORMAL_RECOVER_TIMEOUT_SECONDS = 270
# The agent persists a pending order before entering its bounded authoritative
# settlement loop.  The recurring guard must not turn that normal, live
# in-flight state into a recovery incident.  This exceeds the agent's 12s
# settlement plus 2s cancel windows while remaining below the 60s guard tick.
PENDING_SETTLEMENT_GRACE_MS = 30_000
CAMPAIGN_ID_PATTERN = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9._:-]{2,255}")


def _root_json_document(path: Path, failure: str) -> dict[str, object]:
    """Read one root-owned, immutable JSON binding document.

    The recovery guard must not infer a campaign edge from an untrusted or
    torn file.  This deliberately uses the same single-link/root/mode fence as
    the state and receipt readers below; callers treat any failure as an
    unknown binding and remain fail-closed.
    """
    metadata = os.lstat(path)
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
            metadata.st_uid != 0 or metadata.st_gid != 0 or
            stat.S_IMODE(metadata.st_mode) != 0o600):
        raise RuntimeError(failure + "_PATH_UNSAFE")
    try:
        value = json.loads(path.read_text(encoding="ascii"))
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
        raise RuntimeError(failure + "_INVALID") from error
    if not isinstance(value, dict):
        raise RuntimeError(failure + "_INVALID")
    return value


def recovery_campaign_binding() -> str:
    """Classify the recovered state against the currently prepared policy.

    A completed recovery is bound to the campaign that was interrupted.  A
    newly prepared campaign must be admitted by the operator's explicit
    rearm/auth path; the recurring guard must never turn its old recovery
    receipt into an automatic end-flat request.  Return ``unknown`` whenever
    either immutable document cannot prove the edge, so callers retain the
    fail-closed behavior.
    """
    try:
        state = _root_json_document(
            STATE, "SAFE_RECOVERY_STATE_BINDING")
        policy = _root_json_document(
            POLICY_FILE, "SAFE_RECOVERY_POLICY_BINDING")
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError):
        return "unknown"
    suspended = state.get("campaign_id_at_suspend")
    current = policy.get("campaign_id")
    if (not isinstance(suspended, str) or
            CAMPAIGN_ID_PATTERN.fullmatch(suspended) is None or
            not isinstance(current, str) or
            CAMPAIGN_ID_PATTERN.fullmatch(current) is None or
            policy.get("schema") != "hepta.ib-paper-campaign-policy.v5" or
            policy.get("domain_id") != "alpha"):
        return "unknown"
    return "same" if suspended == current else "fresh"


class _SingleFlight:
    def __init__(self) -> None:
        self.descriptor: int | None = None

    def __enter__(self) -> bool:
        descriptor = os.open(
            SAFE_RECOVERY_GUARD_LOCK,
            os.O_RDWR | os.O_CREAT | os.O_CLOEXEC |
            getattr(os, "O_NOFOLLOW", 0),
            0o600)
        metadata = os.fstat(descriptor)
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
                metadata.st_uid != 0 or metadata.st_gid != 0 or
                stat.S_IMODE(metadata.st_mode) != 0o600):
            os.close(descriptor)
            raise RuntimeError("SAFE_RECOVERY_GUARD_LOCK_UNSAFE")
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


def run(command: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command, text=True, capture_output=True, timeout=timeout, check=False)


def now_ms() -> int:
    return time.time_ns() // 1_000_000


def safety_latch_exists() -> bool:
    try:
        metadata = os.lstat(SAFETY_LATCH)
    except FileNotFoundError:
        return False
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
            metadata.st_uid != 0 or metadata.st_gid != 0 or
            stat.S_IMODE(metadata.st_mode) != 0o600):
        raise RuntimeError("SAFE_RECOVERY_SAFETY_LATCH_PATH_UNSAFE")
    return True


def automatic_risk_recovery_consumed() -> bool:
    """Return whether this incident may have spent its automatic attempt.

    Metadata-safe malformed legacy markers can result only from the old
    direct-to-final publisher tearing before fsync.  Treat them as consumed:
    retrying a broker mutation would be less safe than terminal end-flat.
    """
    try:
        metadata = os.lstat(AUTOMATIC_RISK_ATTEMPT)
    except FileNotFoundError:
        return False
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
            metadata.st_uid != 0 or metadata.st_gid != 0 or
            stat.S_IMODE(metadata.st_mode) != 0o600):
        raise RuntimeError("SAFE_RECOVERY_ATTEMPT_PATH_UNSAFE")
    try:
        value = json.loads(AUTOMATIC_RISK_ATTEMPT.read_text(encoding="ascii"))
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        return True
    if (not isinstance(value, dict) or
            value.get("schema") !=
                "hepta.local-paper-automatic-risk-attempt.v1" or
            value.get("automatic_attempt_consumed") is not True or
            value.get("paper_only") is not True or
            value.get("live_authorized") is not False or
            not isinstance(value.get("attempted_at_ms"), int) or
            isinstance(value.get("attempted_at_ms"), bool) or
            value.get("attempted_at_ms", 0) <= 0 or
            not isinstance(value.get("state_sha256"), str) or
            not value.get("state_sha256", "").startswith("sha256:")):
        return True
    return True


def start_attempt_uncertain() -> bool:
    """Recognize any orphaned one-shot start artifact after agent loss.

    Publishing pending is itself the fresh-campaign boundary: a launcher may
    crash immediately afterward, or the artifact may survive boot/expiry.
    Metadata-safe torn legacy files are also terminal evidence.  Their content
    is never trusted here; end-flat may delete them only after re-proving every
    policy/session/egress/runtime fence.
    """
    observed = False
    for path in (
            START_PERMIT_PENDING, START_PERMIT_CLAIMED,
            START_PERMIT_CONSUMED):
        try:
            metadata = os.lstat(path)
        except FileNotFoundError:
            continue
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
                metadata.st_uid != 0 or metadata.st_gid != 0 or
                stat.S_IMODE(metadata.st_mode) != 0o600):
            raise RuntimeError("SAFE_RECOVERY_START_PERMIT_PATH_UNSAFE")
        try:
            value = json.loads(path.read_text(encoding="ascii"))
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
            observed = True
            continue
        if (not isinstance(value, dict) or
                value.get("schema") !=
                    "hepta.local-ai-paper-start-permit.v1" or
                value.get("paper_only") is not True or
                value.get("live_authorized") is not False or
                not isinstance(value.get("campaign_id"), str) or
                not value.get("campaign_id") or
                not isinstance(value.get("permit_id"), str) or
                not value.get("permit_id")):
            observed = True
            continue
        observed = True
    return observed


def managed_session_authority_present() -> bool:
    """Treat any non-revoked or malformed managed record as live authority."""
    try:
        children = list(SESSION_AUTHORITY_ROOT.iterdir())
    except FileNotFoundError:
        return False
    managed = []
    for path in children:
        if not path.name.endswith(".authority.json"):
            continue
        token_name = path.name.removesuffix(".authority.json")
        if (token_name == "local-paper.token" or
                (token_name.startswith("risk-recovery-") or
                 token_name.startswith("end-flat-")) and
                token_name.endswith(".token")):
            managed.append(path)
    for path in managed:
        try:
            metadata = os.lstat(path)
            value = json.loads(path.read_text(encoding="ascii"))
        except (OSError, ValueError, json.JSONDecodeError):
            return True
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
                metadata.st_uid != 0 or metadata.st_gid != 0 or
                stat.S_IMODE(metadata.st_mode) != 0o600 or
                not isinstance(value, dict) or
                value.get("phase") != "REVOKED"):
            return True
    return False


def persist_safety_exit_latch() -> None:
    """Publish a root-owned latch before systemd's failed status is cleared."""
    if safety_latch_exists():
        return
    payload = (json.dumps({
        "schema": "hepta.local-ai-paper-safety-exit-latch.v1",
        "created_at_ms": time.time_ns() // 1_000_000,
        "source": "agent_exit_75",
    }, ensure_ascii=True, sort_keys=True, separators=(",", ":")) +
        "\n").encode("ascii")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(SAFETY_LATCH, flags, 0o600)
    except FileExistsError:
        # A concurrent timer invocation may win the O_EXCL race after the
        # initial lstat. Accept only the same validated root-owned latch.
        if not safety_latch_exists():
            raise
        return
    try:
        metadata = os.fstat(descriptor)
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
                metadata.st_uid != 0 or metadata.st_gid != 0 or
                stat.S_IMODE(metadata.st_mode) != 0o600):
            raise RuntimeError("SAFE_RECOVERY_SAFETY_LATCH_CREATE_UNSAFE")
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise RuntimeError("SAFE_RECOVERY_SAFETY_LATCH_WRITE_FAILED")
            offset += written
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    parent = os.open(
        SAFETY_LATCH.parent,
        os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC |
        getattr(os, "O_NOFOLLOW", 0))
    try:
        os.fsync(parent)
    finally:
        os.close(parent)


def _verify_recovery_receipt(value: dict[str, object]) -> None:
    suspension_id = value.get("suspension_id")
    expected_hash = value.get("recovery_receipt_sha256")
    if (not isinstance(suspension_id, str) or not suspension_id or
            not isinstance(expected_hash, str) or
            not expected_hash.startswith("sha256:") or
            len(expected_hash) != 71):
        raise RuntimeError("SAFE_RECOVERY_RECEIPT_REFERENCE_INVALID")
    digest = hashlib.sha256(
        suspension_id.encode("utf-8")).hexdigest()[:24]
    path = STATE.parent / (
        "risk-recovery-" + digest + ".receipt.json")
    metadata = os.lstat(path)
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
            metadata.st_uid != 0 or metadata.st_gid != 0 or
            stat.S_IMODE(metadata.st_mode) != 0o600):
        raise RuntimeError("SAFE_RECOVERY_RECEIPT_PATH_UNSAFE")
    raw = path.read_bytes()
    if expected_hash != "sha256:" + hashlib.sha256(raw).hexdigest():
        raise RuntimeError("SAFE_RECOVERY_RECEIPT_HASH_MISMATCH")
    receipt = json.loads(raw)
    if (not isinstance(receipt, dict) or
            receipt.get("schema") !=
                "hepta.local-ai-paper-risk-recovery-receipt.v1" or
            receipt.get("suspension_id") != suspension_id or
            receipt.get("position") != 0 or
            receipt.get("active_orders") != 0 or
            receipt.get("gross_absolute_position") != 0 or
            receipt.get("trading_resumed") is not False):
        raise RuntimeError("SAFE_RECOVERY_RECEIPT_BOUNDARY_INVALID")


def state_latch() -> tuple[bool, bool, str, bool, bool]:
    safety_latched = safety_latch_exists()
    try:
        metadata = os.lstat(STATE)
    except FileNotFoundError:
        return (
            safety_latched, False,
            "SAFETY_EXIT_UNPERSISTED" if safety_latched else "", False,
            False)
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
            metadata.st_uid != 0 or metadata.st_gid != 0):
        raise RuntimeError("SAFE_RECOVERY_STATE_PATH_UNSAFE")
    try:
        value = json.loads(STATE.read_text(encoding="ascii"))
    except (OSError, ValueError, json.JSONDecodeError):
        if safety_latched:
            return True, False, "SAFETY_EXIT_STATE_UNREADABLE", False, False
        raise
    if not isinstance(value, dict):
        if safety_latched:
            return True, False, "SAFETY_EXIT_STATE_UNREADABLE", False, False
        raise RuntimeError("SAFE_RECOVERY_STATE_INVALID")
    explicitly_latched = (
        safety_latched or
        value.get("trading_suspended") is True or
        value.get("recovery_required") is True)
    pending = value.get("pending_order_id") is not None
    pending_since_ms = value.get("pending_order_since_ms")
    pending_age_ms = (
        now_ms() - pending_since_ms
        if (pending and isinstance(pending_since_ms, int) and
            not isinstance(pending_since_ms, bool)) else None)
    fresh_pending = bool(
        pending and not explicitly_latched and
        pending_age_ms is not None and
        0 <= pending_age_ms <= PENDING_SETTLEMENT_GRACE_MS)
    # Missing, future-dated, or stale pending metadata is uncertain and must
    # remain fail-closed.  Only a fresh pending owned by a live agent may use
    # the settlement grace path in main().
    latched = explicitly_latched or (pending and not fresh_pending)
    recovered = (
        value.get("recovery_complete") is True and
        value.get("recovery_phase") == "FLAT_CONFIRMED")
    if recovered:
        _verify_recovery_receipt(value)
    return (
        latched, recovered, str(value.get("suspension_code") or ""),
        value.get("manual_start_required") is True, fresh_pending)


def agent_runtime_status() -> tuple[bool, bool]:
    completed = run([
        "/usr/bin/systemctl", "show", AGENT_SERVICE,
        "--property", "ActiveState", "--property", "Result",
        "--property", "ExecMainStatus"], timeout=10)
    if completed.returncode != 0:
        return False, False
    properties: dict[str, str] = {}
    for line in completed.stdout.splitlines():
        key, separator, value = line.partition("=")
        if separator and key not in properties:
            properties[key] = value
    active = properties.get("ActiveState") == "active"
    safety_exit = (
        properties.get("ActiveState") == "failed" and
        properties.get("Result") == "exit-code" and
        properties.get("ExecMainStatus") == "75")
    return active, safety_exit


def stop_agent() -> None:
    stopped = run(
        ["/usr/bin/systemctl", "stop", AGENT_SERVICE], timeout=30)
    if stopped.returncode != 0:
        raise RuntimeError("SAFE_RECOVERY_AGENT_STOP_FAILED")
    active = run(
        ["/usr/bin/systemctl", "is-active", AGENT_SERVICE], timeout=10)
    if active.returncode == 0:
        raise RuntimeError("SAFE_RECOVERY_AGENT_STILL_ACTIVE")


def seal_recovered_campaign_timers() -> None:
    """Persistently stop stale campaign jobs after verified end-flat recovery."""
    completed = run([
        "/usr/bin/systemctl", "disable", "--now",
        *CAMPAIGN_BACKGROUND_TIMERS,
    ], timeout=30)
    if completed.returncode != 0:
        raise RuntimeError("SAFE_RECOVERY_TIMER_SEAL_FAILED")


def schedule_terminal_end_flat() -> None:
    """Queue terminal closure after this lifecycle lock is released."""
    requested = run(REQUEST_END_FLAT, timeout=30)
    if requested.returncode != 0:
        raise RuntimeError("SAFE_RECOVERY_END_FLAT_REQUEST_FAILED")
    completed = run([
        "/usr/bin/systemctl", "start", "--no-block", END_FLAT_SERVICE,
    ], timeout=30)
    if completed.returncode != 0:
        raise RuntimeError("SAFE_RECOVERY_END_FLAT_SCHEDULE_FAILED")


def schedule_orphan_start_end_flat() -> bool:
    """Let the lifecycle owner recheck the permit before terminal closure."""
    requested = run(REQUEST_ORPHAN_END_FLAT, timeout=60)
    if requested.returncode == 3:
        return False
    if requested.returncode != 0:
        raise RuntimeError("SAFE_RECOVERY_ORPHAN_RECHECK_FAILED")
    completed = run([
        "/usr/bin/systemctl", "start", "--no-block", END_FLAT_SERVICE,
    ], timeout=30)
    if completed.returncode != 0:
        raise RuntimeError("SAFE_RECOVERY_END_FLAT_SCHEDULE_FAILED")
    return True


def forward(completed: subprocess.CompletedProcess[str]) -> None:
    if completed.stdout:
        print(completed.stdout, end="", flush=True)
    if completed.stderr:
        print(completed.stderr, end="", file=sys.stderr, flush=True)


def external_p1_policy() -> dict[str, object] | None:
    """Recognize the finalized external profile without applying expiry."""
    try:
        metadata = os.lstat(POLICY_FILE)
    except FileNotFoundError:
        return None
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
            metadata.st_uid != 0 or metadata.st_gid != 0 or
            stat.S_IMODE(metadata.st_mode) & 0o022):
        raise RuntimeError("SAFE_RECOVERY_EXTERNAL_POLICY_UNSAFE")
    try:
        value = json.loads(POLICY_FILE.read_text(encoding="ascii"))
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
        raise RuntimeError("SAFE_RECOVERY_EXTERNAL_POLICY_INVALID") from error
    if not isinstance(value, dict) or value.get(
            "admission_mode") != "external-p1-finalized":
        return None
    if (value.get("schema") != "hepta.ib-paper-campaign-policy.v5" or
            value.get("version") != 5 or value.get("domain_id") != "alpha" or
            value.get("paper_only") is not True or
            value.get("live_authorized") is not False or
            value.get("order_type") != "LMT" or value.get("tif") != "DAY" or
            value.get("max_cycles") != 1 or
            value.get("max_quantity") != 1 or
            value.get("max_active_orders") != 1 or
            not isinstance(value.get("campaign_id"), str) or
            not value["campaign_id"]):
        raise RuntimeError("SAFE_RECOVERY_EXTERNAL_POLICY_INVALID")
    return value


def reconcile_external_control() -> dict[str, object]:
    completed = run([
        LOCAL_PAPER_CONTROL, "reconcile", "--domain", "alpha",
    ], timeout=120)
    if completed.returncode != 0:
        raise RuntimeError("SAFE_RECOVERY_EXTERNAL_RECONCILE_FAILED")
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(
            "SAFE_RECOVERY_EXTERNAL_RECONCILE_INVALID") from error
    if not isinstance(value, dict):
        raise RuntimeError("SAFE_RECOVERY_EXTERNAL_RECONCILE_INVALID")
    return value


def external_recovery_incident_present(policy: dict[str, object]) -> bool:
    """Detect sealed recovery input; contents are validated by repair."""
    paths: list[tuple[Path, int | None]] = [
        (EXTERNAL_RECOVERY_AUTHORITY, 0)]
    root = EXTERNAL_CANARY_ROOT / str(policy["campaign_id"])
    try:
        for pattern, expected_uid in (
                ("*/recovery-record-v1.json", None),
                ("*/recovery-record.v1.json", None),
                ("*/root-emergency-cleanup-receipt.v1.json", 0)):
            paths.extend((path, expected_uid) for path in root.glob(pattern))
    except OSError as error:
        raise RuntimeError("SAFE_RECOVERY_EXTERNAL_SCAN_FAILED") from error
    observed = False
    for path, expected_uid in paths:
        try:
            metadata = os.lstat(path)
        except FileNotFoundError:
            continue
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
                stat.S_IMODE(metadata.st_mode) != 0o600 or
                (expected_uid is not None and
                 (metadata.st_uid != expected_uid or metadata.st_gid != 0))):
            raise RuntimeError("SAFE_RECOVERY_EXTERNAL_ARTIFACT_UNSAFE")
        observed = True
    return observed


def recover_external_once(policy: dict[str, object]) -> int:
    # A retained ENABLE_RECOVERY WAL is a cross-boot/operator boundary.  The
    # reconcile step leaves it DENY_ALL and intact; this timer must not call
    # risk recovery or re-enable the minimal stack.
    reconciled = reconcile_external_control()
    if (reconciled.get("recovery_retained") is True or
            reconciled.get("wal_operation") == "ENABLE_RECOVERY"):
        print(
            "RECOVERY_PENDING_OPERATOR mode=DENY_ALL "
            "wal_operation=ENABLE_RECOVERY recovery_required=true",
            flush=True)
        return 0
    if not external_recovery_incident_present(policy):
        print(
            "SAFE_RECOVERY_NOOP external_p1=true incident=false",
            flush=True)
        return 0
    stop_agent()
    completed = run(
        [*RISK_RECOVER, "--automatic"],
        timeout=RISK_RECOVER_TIMEOUT_SECONDS)
    forward(completed)
    if completed.returncode != 0:
        print(
            "SAFE_RECOVERY_DEFERRED external_p1_recovery_incomplete=true "
            "operator_recovery_required=true",
            flush=True)
        return completed.returncode or 1
    print(
        "SAFE_RECOVERY_BLOCKED external_p1=true auth_rearm_required=true "
        "fresh_campaign_required=true",
        flush=True)
    return 0


def recover_once() -> int:
    try:
        policy = external_p1_policy()
        if policy is not None:
            return recover_external_once(policy)
        agent_active, safety_exit = agent_runtime_status()
        uncertain_start = bool(
            not agent_active and start_attempt_uncertain())
        if safety_exit:
            persist_safety_exit_latch()
        if uncertain_start:
            requested = schedule_orphan_start_end_flat()
            if requested:
                print(
                    "SAFE_RECOVERY_BLOCKED "
                    "suspension_code=START_PERMIT_ORPHANED "
                    "operator_recovery_required=true "
                    "fresh_campaign_required=true",
                    flush=True)
            else:
                print(
                    "SAFE_RECOVERY_DEFERRED "
                    "start_permit_transition_revalidated=true",
                    flush=True)
            return 0
        (latched, recovered, code, manual_start_required,
         fresh_pending) = state_latch()
        if manual_start_required and not latched and not safety_exit:
            stop_agent()
            print(
                "SAFE_RECOVERY_BLOCKED manual_start_required=true "
                "auth_rearm_verified=true",
                flush=True)
            return 0
        if fresh_pending and not safety_exit:
            if agent_active:
                print(
                    "SAFE_RECOVERY_DEFERRED pending_settlement_in_progress=true "
                    "agent_active=true",
                    flush=True)
                return 0
            # A pending order without its settling agent is uncertain even
            # when the timestamp is recent.  Route it to risk-only recovery.
            latched = True
        if latched or safety_exit:
            authority_present = managed_session_authority_present()
            binding: str | None = None
            if recovered and not authority_present:
                # Classify the immutable recovery/policy edge before doing
                # anything that can affect the currently prepared campaign.
                # A foreign (fresh) campaign must never be fenced by an old
                # receipt.  If an agent is already active, stop it only after
                # this classification: the latch is a fail-closed boundary,
                # so leaving a live executor running would be unsafe.  An
                # inactive agent needs no stop side effect at all.
                binding = recovery_campaign_binding()
                if binding == "fresh":
                    if agent_active:
                        stop_agent()
                    print(
                        "SAFE_RECOVERY_BLOCKED auth_rearm_required=true "
                        f"suspension_code={code or 'UNKNOWN'} "
                        "recovery_receipt_verified=true "
                        "fresh_campaign_required=true "
                        "recovery_campaign_binding=fresh",
                        flush=True)
                    return 0
                if binding != "same":
                    if agent_active:
                        stop_agent()
                    # Do not guess which campaign is safe to close when the
                    # binding documents are missing, malformed, or drifted.
                    # No authority is granted; the next guarded invocation
                    # may retry after the immutable inputs are repaired.
                    print(
                        "SAFE_RECOVERY_DEFERRED "
                        "recovery_campaign_binding=unknown",
                        flush=True)
                    return 1

            # Preserve the existing fail-closed stop for same-campaign
            # recovery and for unrecovered/authority-present latches.
            stop_agent()
            if recovered and not authority_present:
                # Same-campaign recovery retains the original fail-closed
                # behavior.  Queue terminal closure on every such resume;
                # end-flat is checkpointed/idempotent and its retry timer
                # remains armed until the terminal receipt is sealed.
                schedule_terminal_end_flat()
                seal_recovered_campaign_timers()
                print(
                    "SAFE_RECOVERY_BLOCKED auth_rearm_required=true "
                    f"suspension_code={code or 'UNKNOWN'} "
                    "recovery_receipt_verified=true "
                    "fresh_campaign_required=true",
                    flush=True)
                return 0
            if (automatic_risk_recovery_consumed() and
                    not (recovered and authority_present)):
                schedule_terminal_end_flat()
                print(
                    "SAFE_RECOVERY_BLOCKED "
                    "automatic_risk_recovery_attempt_consumed=true "
                    "operator_recovery_required=true "
                    "fresh_campaign_required=true",
                    flush=True)
                return 0
            command = [*RISK_RECOVER, "--automatic"]
            if safety_exit or code in {
                    "SAFETY_EXIT_UNPERSISTED",
                    "SAFETY_EXIT_STATE_UNREADABLE"}:
                command.append("--safety-exit")
            completed = run(
                command, timeout=RISK_RECOVER_TIMEOUT_SECONDS)
            forward(completed)
            if completed.returncode != 0:
                if automatic_risk_recovery_consumed():
                    schedule_terminal_end_flat()
                print(
                    "SAFE_RECOVERY_DEFERRED risk_recovery_incomplete",
                    flush=True)
                return completed.returncode or 1
            # A zero subprocess status is not itself end-flat evidence.  The
            # repair command must have durably published a latched,
            # FLAT_CONFIRMED state whose receipt state_latch() verifies before
            # this invocation persistently disables the stale campaign jobs.
            (recovered_latched, recovery_proven, recovered_code,
             _manual_start_required, _fresh_pending) = state_latch()
            if not recovered_latched or not recovery_proven:
                if automatic_risk_recovery_consumed():
                    schedule_terminal_end_flat()
                print(
                    "SAFE_RECOVERY_DEFERRED "
                    "risk_recovery_state_unproven=true",
                    flush=True)
                return 1
            schedule_terminal_end_flat()
            seal_recovered_campaign_timers()
            print(
                "SAFE_RECOVERY_BLOCKED auth_rearm_required=true "
                f"suspension_code={recovered_code or code or 'UNKNOWN'} "
                "recovery_receipt_verified=true "
                "fresh_campaign_required=true",
                flush=True)
            return 0
        completed = run(
            NORMAL_RECOVER, timeout=NORMAL_RECOVER_TIMEOUT_SECONDS)
        forward(completed)
        return completed.returncode
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError,
            subprocess.TimeoutExpired) as error:
        print(f"SAFE_RECOVERY_DEFERRED {error}", flush=True)
        return 1


def main() -> int:
    try:
        with single_flight() as acquired:
            if not acquired:
                print(
                    "SAFE_RECOVERY_NOOP single_flight_busy=true",
                    flush=True)
                return 0
            return recover_once()
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError,
            subprocess.TimeoutExpired) as error:
        print(f"SAFE_RECOVERY_DEFERRED {error}", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
