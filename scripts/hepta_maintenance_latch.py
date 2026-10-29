#!/usr/bin/env python3
"""Owner-local PAPER maintenance latch; no service-stop or Broker authority.

All callers on Pocket4 share one flock before inspecting/changing the
transient native kill-switch. A durable operator hold makes resident
`paper-on` fail without deleting native protection. This is a strategy-side
admission fence, NOT a native owner fence or maintenance-stop permit.
"""
from __future__ import annotations
from contextlib import contextmanager
import fcntl
import hashlib
import json
import math
import os
import pwd
from pathlib import Path
import stat
import subprocess
import sys
import time
import uuid

SCHEMA = "heptatrader.operator-maintenance-hold.v1"
SWITCH = "kill-switch"
HOLD = "maintenance-hold.json"
LOCK = "operator-maintenance.lock"
ACTIONS = {"pause", "paper-on", "maintenance-hold", "maintenance-release",
           "maintenance-status", "native-startup-hold"}
# Only reviewed, exact CLI machine codes may leave captured stderr. Never echo
# a general uppercase token, arbitrary line, argv, token path or native stdout.
NATIVE_READ_STDERR_CODES = frozenset({"DISCOVERY_LIST_REJECTED"})
OPERATOR_CLIENT_HELPER_SHA256 = "9b96f650b581dc82a2668eb9e67743a2826d6728731853a96800ffb653850889"
OPERATOR_CLIENTS_REQUIRED = False  # A prepared operator copy fixes this to True.


class HoldError(Exception):
    pass


def _directory(path: Path, mode: int, *, make=False, exact_links=None) -> int:
    created = False
    if make:
        try:
            path.mkdir(mode=0o700)
            created = True
        except FileExistsError:
            pass
    if created:
        created_fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            meta = os.fstat(created_fd)
            if meta.st_uid != os.geteuid() or meta.st_gid != os.getegid():
                raise HoldError("UNSAFE_CREATED_DIRECTORY:" + path.name)
            os.fchmod(created_fd, mode)
            os.fsync(created_fd)
        finally:
            os.close(created_fd)
    before = path.lstat()
    if (not stat.S_ISDIR(before.st_mode) or before.st_uid != os.geteuid()
            or before.st_gid != os.getegid()
            or stat.S_IMODE(before.st_mode) != mode or before.st_nlink < 2
            or (exact_links is not None and before.st_nlink != exact_links)):
        raise HoldError("UNSAFE_DIRECTORY:" + path.name)
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    after = os.fstat(fd)
    if ((before.st_dev, before.st_ino) != (after.st_dev, after.st_ino)
            or after.st_uid != os.geteuid() or stat.S_IMODE(after.st_mode) != mode):
        os.close(fd)
        raise HoldError("DIRECTORY_CHANGED:" + path.name)
    return fd


def _identity(st) -> tuple:
    return (st.st_dev, st.st_ino, st.st_mode, st.st_uid, st.st_gid,
            st.st_nlink, st.st_size, st.st_mtime_ns, st.st_ctime_ns)


@contextmanager
def _opened_file(fd: int, name: str, mode: int):
    """Keep the original object open; startup need not interpret its payload."""
    try:
        st = os.stat(name, dir_fd=fd, follow_symlinks=False)
    except FileNotFoundError:
        yield None
        return
    if (not stat.S_ISREG(st.st_mode) or st.st_nlink != 1
            or st.st_uid != os.geteuid() or st.st_gid != os.getegid()
            or stat.S_IMODE(st.st_mode) != mode or st.st_dev != os.fstat(fd).st_dev):
        raise HoldError("UNSAFE_MARKER:" + name)
    item = os.open(name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC,
                   dir_fd=fd)
    try:
        if _identity(st) != _identity(os.fstat(item)):
            raise HoldError("MARKER_CHANGED:" + name)
        yield item
    finally:
        os.close(item)


def _file(fd: int, name: str, mode: int) -> bytes | None:
    with _opened_file(fd, name, mode) as item:
        if item is None:
            return None
        before = os.fstat(item)
        if before.st_size > 4096:
            raise HoldError("UNSAFE_MARKER:" + name)
        data = os.read(item, before.st_size + 1)
        if len(data) != before.st_size or _identity(before) != _identity(os.fstat(item)):
            raise HoldError("MARKER_CHANGED_DURING_READ:" + name)
        return data


def _directory_identity(st) -> tuple:
    return (st.st_dev, st.st_ino, st.st_mode, st.st_uid, st.st_gid)


def _bound_directory(path: Path, fd: int, expected=None) -> None:
    expected = _directory_identity(os.fstat(fd)) if expected is None else expected
    if (_directory_identity(os.fstat(fd)) != expected
            or _directory_identity(path.lstat()) != expected):
        raise HoldError("DIRECTORY_CHANGED:" + path.name)


def _write_once(fd: int, name: str, data: bytes, mode: int) -> bool:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
    try:
        item = os.open(name, flags, mode, dir_fd=fd)
    except FileExistsError:
        return False
    try:
        os.fchmod(item, mode)
        os.write(item, data)
        os.fsync(item)
    finally:
        os.close(item)
    os.fsync(fd)
    return True


def _unlink(fd: int, name: str, mode: int) -> None:
    if _file(fd, name, mode) is None:
        raise HoldError("MARKER_MISSING:" + name)
    os.unlink(name, dir_fd=fd)
    os.fsync(fd)


def _hold_record(data: bytes | None) -> dict | None:
    if data is None:
        return None
    def pairs(items):
        obj = {}
        for key, value in items:
            if key in obj:
                raise HoldError("DUPLICATE_HOLD_FIELD")
            obj[key] = value
        return obj
    try:
        record = json.loads(data, object_pairs_hook=pairs)
    except (ValueError, UnicodeDecodeError) as exc:
        raise HoldError("BAD_HOLD_JSON") from exc
    if (not isinstance(record, dict)
            or record.get("schema") != SCHEMA
            or record.get("effect") != "NEW_PAPER_RISK_HOLD_ONLY"
            or record.get("uid") != os.geteuid()
            or type(record.get("created_at_ms")) is not int
            or record["created_at_ms"] <= 0):
        raise HoldError("BAD_HOLD_SCOPE")
    return record


@contextmanager
def operator_lock(state_root: Path):
    """Share the existing owner lock across latch and unit-policy transactions."""
    if os.geteuid() == 0:
        raise HoldError("ROOT_NOT_ALLOWED")
    root_fd = _directory(state_root, 0o700)
    lock_fd = None
    try:
        lock_fd = os.open(
            LOCK, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600, dir_fd=root_fd)
        meta = os.fstat(lock_fd)
        if (not stat.S_ISREG(meta.st_mode) or meta.st_nlink != 1
                or meta.st_uid != os.geteuid() or meta.st_gid != os.getegid()
                or stat.S_IMODE(meta.st_mode) != 0o600):
            raise HoldError("UNSAFE_OPERATOR_LOCK")
        root_identity = _directory_identity(os.fstat(root_fd))
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        def check_binding():
            _bound_directory(state_root, root_fd, root_identity)
            actual = os.stat(LOCK, dir_fd=root_fd, follow_symlinks=False)
            if (_identity(actual) != _identity(meta)
                    or _identity(os.fstat(lock_fd)) != _identity(meta)):
                raise HoldError("OPERATOR_LOCK_CHANGED")
        check_binding()
        yield check_binding
    finally:
        if lock_fd is not None:
            os.close(lock_fd)
        os.close(root_fd)


def _command(argv: list[str]) -> str:
    try:
        result = subprocess.run(argv, check=True, capture_output=True, text=True, timeout=12)
    except (OSError, subprocess.SubprocessError) as exc:
        raise HoldError("NATIVE_RESUME_READ_FAILED:" + type(exc).__name__) from exc
    return result.stdout


def _native_read_failure(owner: str, method: str, cause: BaseException | None) -> str:
    detail = "READ_FAILED"
    if isinstance(cause, subprocess.TimeoutExpired):
        detail = "TIMEOUT"
    elif isinstance(cause, subprocess.CalledProcessError):
        code = "UNCLASSIFIED"
        raw = cause.stderr
        if type(raw) is bytes and len(raw) <= 128:
            try:
                raw = raw.decode("ascii")
            except UnicodeError:
                raw = None
        if type(raw) is str and len(raw) <= 128 and raw.strip() in NATIVE_READ_STDERR_CODES:
            code = raw.strip()
        rc = cause.returncode
        bounded_rc = str(rc) if type(rc) is int and -255 <= rc <= 255 else "UNKNOWN"
        detail = "rc=" + bounded_rc + ":" + code
    elif isinstance(cause, OSError):
        detail = "OS_ERROR"
    # Both fields come from the fixed local resume sequence, not CLI overrides.
    return "NATIVE_RESUME_READ_FAILED:" + owner + ":" + method + ":" + detail


def _operator_clients():
    root = Path(__file__).resolve().parent
    path = root / "MANIFEST.json"
    if not os.path.lexists(path):
        if OPERATOR_CLIENTS_REQUIRED:
            raise HoldError("OPERATOR_CLIENT_DECLARATION_REQUIRED")
        return None
    from hepta_verified_loader import load_verified_module
    helper = load_verified_module("hepta_latch_pinned_operator_clients",
                                  root / "hepta_operator_clients.py", OPERATOR_CLIENT_HELPER_SHA256)
    config = helper.load_config(root)
    if config is None and OPERATOR_CLIENTS_REQUIRED:
        raise HoldError("OPERATOR_CLIENT_DECLARATION_REQUIRED")
    return config


def _service_snapshot() -> dict:
    """Read the manager's loaded policy; disk drop-ins alone are insufficient."""
    clients = _operator_clients()
    startup_guard = (Path(clients["native_startup_guard"]["path"]) if clients is not None
                     else Path(__file__).resolve())
    result = {}
    for unit in ("heptatrader-execution.service", "heptatrader-gateway.service"):
        props = ["ActiveState", "SubState", "MainPID", "InvocationID"]
        if unit == "heptatrader-execution.service":
            props += ["Type", "KillMode", "ExecStartPre"]
        raw = _command(["systemctl", "--user", "show", unit, "--no-pager",
                        "--property=" + ",".join(props)])
        values = {}
        for line in raw.splitlines():
            key, separator, value = line.partition("=")
            if not separator or key not in props or key in values:
                raise HoldError("BAD_SYSTEMD_READBACK:" + unit)
            values[key] = value
        if (set(values) != set(props) or values["ActiveState"] != "active"
                or values["SubState"] != "running" or not values["MainPID"].isdigit()
                or int(values["MainPID"]) <= 0 or not values["InvocationID"]):
            raise HoldError("SERVICE_NOT_RUNNING:" + unit)
        if unit == "heptatrader-execution.service":
            guard = values["ExecStartPre"]
            expected = ("{ path=/usr/bin/python3 ; argv[]=/usr/bin/python3 -B "
                        + str(startup_guard)
                        + " native-startup-hold ; ignore_errors=no ;")
            if (values["Type"] != "simple" or values["KillMode"] != "control-group"
                    or not guard.startswith(expected) or guard.count("{") != 1
                    or guard.count("}") != 1 or not guard.endswith("}")):
                raise HoldError("NATIVE_STARTUP_GUARD_NOT_COORDINATED")
        result[unit] = {key: values[key] for key in ("MainPID", "InvocationID")}
    if clients is not None and _operator_clients() != clients:
        raise HoldError("OPERATOR_CLIENT_DECLARATION_CHANGED")
    return result


def _native_payload(control: Path, owner: str, method: str) -> dict:
    # No token, endpoint, path, or method overrides are exposed to the CLI.
    home = Path(pwd.getpwuid(os.geteuid()).pw_dir)
    binary = [str(home / ".local/share/heptatrader/current/bin/heptactl")]
    if _operator_clients() is not None:
        binary = ["/usr/bin/python3", "-B", str(Path(__file__).resolve().with_name(
            "hepta_operator_clients.py")), "heptactl"]
    try:
        raw = _command([*binary,
            "--socket", str(control.parent / "tools.sock"), "--token-file",
            str(home / (".config/heptatrader/" + owner + ".token")),
            "--io-timeout-ms", "4000", "--call-id", "hold-resume-" + uuid.uuid4().hex,
            "call", method])
    except HoldError as exc:
        raise HoldError(_native_read_failure(owner, method, exc.__cause__)) from exc
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise HoldError("BAD_NATIVE_JSON:" + owner + ":" + method) from exc
    if (not isinstance(value, dict) or value.get("status") != "ok"
            or value.get("tool") != method or not isinstance(value.get("payload"), dict)):
        raise HoldError("NATIVE_READ_REFUSED:" + owner + ":" + method)
    return value["payload"]


def _health_identity(value: dict, owner: str) -> tuple:
    health = value.get("execution_authoritative_health")
    if not isinstance(health, dict):
        raise HoldError("NATIVE_HEALTH_MISSING:" + owner)
    expected_true = ("authoritative", "terminal_download_complete",
                     "account_broker_sample_connected",
                     "account_broker_sample_event_stream_authoritative",
                     "account_broker_sample_coherent", "account_local_drain_observation_present",
                     "native_new_risk_guard_blocked")
    expected_false = ("startup_recovery_read_only", "owner_fenced", "owner_recovery_only",
                      "account_new_entry_fenced", "mutation_blocked",
                      "risk_mutation_dispatch_in_flight", "mutation_ready")
    for key in expected_true:
        if health.get(key) is not True:
            raise HoldError("NATIVE_RESUME_BLOCKED:" + owner + ":" + key)
    for key in expected_false:
        if health.get(key) is not False:
            raise HoldError("NATIVE_RESUME_BLOCKED:" + owner + ":" + key)
    for key in ("account_local_uncertain_commands", "account_local_venue_dispatches_in_flight",
                "account_local_unattributed_owned_orders",
                "account_local_unattributed_uncertain_commands"):
        if type(health.get(key)) is not int or health[key] != 0:
            raise HoldError("NATIVE_RESUME_BLOCKED:" + owner + ":" + key)
    if (value.get("execution_mode") != "PAPER" or value.get("gateway_ready") is not True
            or value.get("remote_execution_ready") is not True or health.get("source") != "IB"
            or type(health.get("authorized_connector_count")) is not int
            or health["authorized_connector_count"] != 1
            or health.get("mutation_reason") != "IB_PAPER_KILL_SWITCH_ENGAGED"):
        raise HoldError("NATIVE_PAPER_HOLD_NOT_SOLE_OBSERVED_BLOCKER:" + owner)
    epoch = value.get("execution_service_epoch")
    generation = value.get("execution_service_fencing_generation")
    if not isinstance(epoch, str) or not epoch or type(generation) is not int or generation <= 0:
        raise HoldError("NATIVE_IDENTITY_MISSING:" + owner)
    return epoch, generation


def _inactive_crypto_proof(control: Path, services: dict, *, previous=None):
    """Use the explicit native proof only for a reviewed, disabled crypto lane."""
    clients = _operator_clients()
    if clients is None or "inactive_owner_runtime" not in clients:
        if previous is not None:
            raise HoldError("INACTIVE_OWNER_RUNTIME_BINDING_DISAPPEARED")
        return None
    from hepta_verified_loader import load_verified_module
    root = Path(__file__).resolve().parent
    helper = load_verified_module("hepta_latch_inactive_owner", root / "hepta_inactive_owner.py",
                                  clients["files"]["hepta_inactive_owner.py"])
    try:
        policy = helper.crypto_policy(_command)
        if policy is None and previous is None:
            return None
        proof = helper.prove(control, services, clients, "held" if previous is None else "released",
                             _command, previous=previous)
    except (OSError, ValueError, RuntimeError) as error:
        raise HoldError(str(error)) from error
    if _operator_clients() != clients:
        raise HoldError("OPERATOR_CLIENT_DECLARATION_CHANGED")
    return proof


def _resume_observation(control: Path) -> dict:
    before = _service_snapshot()
    identity = None
    observations = {}
    for owner in ("paper", "crypto"):
        if owner == "crypto":
            proof = _inactive_crypto_proof(control, before)
            if proof is not None:
                current = proof["native"]["execution"]
                first = (current["service_epoch"], current["service_fencing_generation"])
                if identity is None or first != identity:
                    raise HoldError("NATIVE_INSTANCE_CHANGED:" + owner)
                observations[owner] = proof
                continue
        first = _health_identity(_native_payload(control, owner, "system.get_health"), owner)
        if identity is not None and first != identity:
            raise HoldError("NATIVE_INSTANCE_CHANGED:" + owner)
        identity = first
        orders = _native_payload(control, owner, "orders.list")
        if (orders.get("source") != "IB"
                or any(orders.get(key) is not True for key in
                       ("authoritative", "global_active_orders_complete", "owner_projection_complete"))
                or not isinstance(orders.get("active_order_ids"), list)
                or not isinstance(orders.get("owned_active_order_ids"), list)):
            raise HoldError("NATIVE_ORDERS_NOT_AUTHORITATIVE:" + owner)
        portfolio = _native_payload(control, owner, "portfolio.list_positions")
        positions = portfolio.get("positions")
        if (portfolio.get("source") != "IB" or portfolio.get("authoritative") is not True
                or not isinstance(positions, list)
                or any(not isinstance(row, dict) or type(row.get("quantity")) not in (int, float)
                       or not math.isfinite(row["quantity"]) for row in positions)):
            raise HoldError("NATIVE_POSITIONS_NOT_AUTHORITATIVE:" + owner)
        if _health_identity(_native_payload(control, owner, "system.get_health"), owner) != first:
            raise HoldError("NATIVE_INSTANCE_CHANGED:" + owner)
        observations[owner] = {"active_orders": len(orders["active_order_ids"]),
                               "owned_active_orders": len(orders["owned_active_order_ids"]),
                               "positions": len(positions)}
    if _service_snapshot() != before:
        raise HoldError("NATIVE_SERVICE_INSTANCE_CHANGED")
    return {"execution_service_epoch": identity[0],
            "execution_service_fencing_generation": identity[1],
            "services": before, "owners": observations}


def _verify_native_release(control: Path, observation: dict) -> dict:
    """Observe the actual native guard after unlink, under the original lock."""
    expected = (observation["execution_service_epoch"],
                observation["execution_service_fencing_generation"])
    owners = {}
    for owner in ("paper", "crypto"):
        previous = observation["owners"].get(owner, {})
        if owner == "crypto" and previous.get("state") == "INACTIVE_FENCED_ZERO_NO_LEASE":
            proof = _inactive_crypto_proof(control, observation["services"], previous=previous)
            current = proof["native"]["execution"]
            if (current["service_epoch"], current["service_fencing_generation"]) != expected:
                raise HoldError("NATIVE_INSTANCE_CHANGED:" + owner)
            owners[owner] = proof
            continue
        value = _native_payload(control, owner, "system.get_health")
        health = value.get("execution_authoritative_health")
        if (value.get("execution_mode") != "PAPER" or value.get("gateway_ready") is not True
                or value.get("remote_execution_ready") is not True
                or not isinstance(health, dict) or health.get("source") != "IB"
                or health.get("authoritative") is not True):
            raise HoldError("NATIVE_RELEASE_NOT_AUTHORITATIVE:" + owner)
        identity = (value.get("execution_service_epoch"),
                    value.get("execution_service_fencing_generation"))
        if type(identity[1]) is not int or identity != expected:
            raise HoldError("NATIVE_INSTANCE_CHANGED:" + owner)
        if health.get("native_new_risk_guard_blocked") is not False:
            raise HoldError("NATIVE_GUARD_NOT_RELEASED:" + owner + ":"
                            + str(health.get("mutation_reason")))
        if (type(health.get("mutation_ready")) is not bool
                or not isinstance(health.get("mutation_reason"), str)):
            raise HoldError("NATIVE_RELEASE_HEALTH_MISSING:" + owner)
        # A resident may legitimately start a request as soon as the guard lifts.
        # Record current readiness; do not confuse in-flight work with a stuck hold.
        owners[owner] = {key: health[key] for key in
                         ("native_new_risk_guard_blocked", "mutation_ready", "mutation_reason")}
    if _service_snapshot() != observation["services"]:
        raise HoldError("NATIVE_SERVICE_INSTANCE_CHANGED")
    return owners


def native_marker_origin(marker: bytes | None) -> str:
    """Classify existing bytes; empty payloads do not identify their writer."""
    return ("ABSENT" if marker is None else
            "INDEPENDENT_EMPTY_NATIVE" if marker == b"" else
            "CLI_TAGGED_PAUSE" if marker == b"engaged\n" else
            "UNRECOGNIZED_INDEPENDENT_NATIVE")


def resume_guidance(origin: str, persistent_hold: bool) -> dict:
    """Explain the existing owner workflow; this is never resume permission."""
    if persistent_hold:
        action = "REVIEW_AND_RELEASE_EXACT_PERSISTENT_MAINTENANCE_HOLD"
        conditions = ["ORIGINAL_HOLD_RESUME_CONDITIONS", "EXACT_HOLD_SHA256",
                      "NATIVE_SERVICES_AND_DRIVER_STATE_READY"]
        command = None
    elif origin == "INDEPENDENT_EMPTY_NATIVE":
        action = "EXPLICIT_NATIVE_RESUME_PRECHECK"
        conditions = ["NO_PERSISTENT_MAINTENANCE_HOLD", "NATIVE_OWNER_EXPOSURE_RECONCILED",
                      "NATIVE_SERVICE_IDENTITIES_UNCHANGED", "POST_RELEASE_NATIVE_GUARD_VERIFIED"]
        command = "heptatrader paper-on --resume-native-hold --check"
    elif origin == "CLI_TAGGED_PAUSE":
        action = "PAPER_ON_AFTER_NATIVE_READINESS"
        conditions = ["NO_PERSISTENT_MAINTENANCE_HOLD", "NATIVE_READINESS"]
        command = "heptatrader paper-on"
    elif origin == "ABSENT":
        action = "CONTINUE_NATIVE_ADMISSION_CHECKS"
        conditions = ["NATIVE_READINESS"]
        command = None
    else:
        action = "RECONCILE_UNRECOGNIZED_NATIVE_MARKER"
        conditions = ["MARKER_IDENTITY_AND_OWNER_RESOLVED"]
        command = None
    return {"next_action": action, "conditions_to_recheck": conditions,
            "check_command": command, "automatic_resume": False,
            "resume_authority_granted": False}


def _status(action: str, marker: bytes | None, record: dict | None, hold_raw: bytes | None = None) -> dict:
    origin = native_marker_origin(marker)
    blockers = (["PERSISTENT_OPERATOR_HOLD"] if record is not None else [])
    if marker is not None:
        blockers.append(origin)
    details = None if record is None else {
        "uid": record["uid"], "created_at_ms": record["created_at_ms"],
        "age_seconds": max(0, (int(time.time() * 1000) - record["created_at_ms"]) // 1000),
        "created_by": record.get("created_by"), "reason": record.get("reason"),
        "task": record.get("task"), "resume_when": record.get("resume_when")}
    return {"schema": "heptatrader.maintenance-latch-status.v1", "action": action,
            "persistent_hold": record is not None, "native_kill_switch_engaged": marker is not None,
            "native_marker_origin": origin, "hold_details": details,
            "native_marker_origin_evidence": "PAYLOAD_SHAPE_ONLY", "native_marker_creator_verified": False,
            "hold_sha256": None if hold_raw is None else hashlib.sha256(hold_raw).hexdigest(),
            "new_paper_risk_blocked": bool(blockers), "blockers": blockers,
            "resume": resume_guidance(origin, record is not None),
            "may_stop_driver": False, "broker_orders_sent": 0}


class ResumeFailure(HoldError):
    def __init__(self, reason: str, reengaged: bool):
        super().__init__(reason)
        self.status = "NATIVE_RESUME_FAILED_REENGAGED" if reengaged else "NATIVE_RESUME_STATE_UNCERTAIN"


def _resume_native_hold(control: Path, state_root: Path, parent_fd: int, ctrl_fd: int,
                        maint_fd: int, check_lock, *, check_only: bool) -> dict:
    # All normal writers, including ExecStartPre, hold this same lock. Metadata
    # checks detect replaced objects; stat followed by unlink is not itself CAS.
    with _opened_file(ctrl_fd, SWITCH, 0o440) as item:
        if item is None or os.fstat(item).st_size != 0:
            raise HoldError("RESUME_REQUIRES_INDEPENDENT_EMPTY_NATIVE_HOLD")
        identity = _identity(os.fstat(item))
        directories = [(path, fd, _directory_identity(os.fstat(fd))) for path, fd in
                       ((control.parent, parent_fd), (control, ctrl_fd),
                        (state_root / "operator-maintenance", maint_fd))]
        observation = _resume_observation(control)
        def check_bindings():
            check_lock()
            for path, fd, expected in directories:
                _bound_directory(path, fd, expected)
            if _file(maint_fd, HOLD, 0o600) is not None:
                raise HoldError("BLOCKED_BY_PERSISTENT_MAINTENANCE_HOLD")
        check_bindings()
        if (_identity(os.fstat(item)) != identity
                or _identity(os.stat(SWITCH, dir_fd=ctrl_fd, follow_symlinks=False)) != identity):
            raise HoldError("NATIVE_HOLD_CHANGED_DURING_CHECK")
        if check_only:
            return {**_status("paper-on", b"", None),
                    "native_hold_resume": {"state": "PRECHECK_ONLY_NATIVE_RELEASE_UNVERIFIED", **observation}}
        try:
            os.unlink(SWITCH, dir_fd=ctrl_fd)
            os.fsync(ctrl_fd)
            check_bindings()
            if _file(ctrl_fd, SWITCH, 0o440) is not None:
                raise HoldError("NATIVE_HOLD_REAPPEARED")
            released = _verify_native_release(control, observation)
            check_bindings()
            if _file(ctrl_fd, SWITCH, 0o440) is not None:
                raise HoldError("NATIVE_HOLD_REAPPEARED")
        except Exception as exc:
            # Never overwrite another writer's marker. Report uncertainty if the
            # original directory cannot be confirmed or durable rearm fails.
            reengaged = False
            try:
                for path, fd, expected in directories[:2]:
                    _bound_directory(path, fd, expected)
                _write_once(ctrl_fd, SWITCH, b"", 0o440)
                with _opened_file(ctrl_fd, SWITCH, 0o440) as restored:
                    reengaged = restored is not None
            except (OSError, HoldError):
                pass
            raise ResumeFailure(str(exc), reengaged) from exc
        return {**_status("paper-on", None, None),
                "native_hold_resume": {"state": "RELEASED_NATIVE_GUARD_VERIFIED",
                                       **observation, "post_release": released}}


def perform(action: str, control: Path, state_root: Path, *,
            reason: str | None = None, task: str | None = None,
            resume_when: str | None = None, resume_native_hold: bool = False,
            check_only: bool = False, expected_hold_sha256: str | None = None) -> dict:
    """Testable policy core. CLI main separately pins the real runtime paths."""
    if action not in ACTIONS:
        raise HoldError("UNKNOWN_ACTION")
    if (resume_native_hold and action != "paper-on") or (check_only and not resume_native_hold):
        raise HoldError("NATIVE_RESUME_OPTIONS_REQUIRE_PAPER_ON")
    if expected_hold_sha256 is not None:
        if action != "maintenance-release":
            raise HoldError("EXPECTED_HOLD_HASH_REQUIRES_MAINTENANCE_RELEASE")
        if (not isinstance(expected_hold_sha256, str) or len(expected_hold_sha256) != 64
                or any(char not in "0123456789abcdef" for char in expected_hold_sha256)):
            raise HoldError("INVALID_EXPECTED_HOLD_SHA256")
    metadata = {"reason": reason, "task": task, "resume_when": resume_when}
    if any(value is not None and (not isinstance(value, str) or len(value) > 512
           or any(ord(char) < 32 for char in value)) for value in metadata.values()):
        raise HoldError("BAD_HOLD_DESCRIPTION")
    # An unprivileged owner alone is allowed; never silently act as root.
    if os.geteuid() == 0:
        raise HoldError("ROOT_NOT_ALLOWED")
    # The existing PAPER runtime parent uses 0700, native control uses 0750.
    startup = action == "native-startup-hold"
    if startup:
        # /run/user/<uid> is manager-owned; create only its checked private child.
        runtime_fd = _directory(control.parent.parent, 0o700)
        try:
            parent_fd = _directory(control.parent, 0o700, make=True)
            _bound_directory(control.parent.parent, runtime_fd)
            os.fsync(runtime_fd)
        finally:
            os.close(runtime_fd)
    else:
        parent_fd = _directory(control.parent, 0o700)
    try:
        with operator_lock(state_root) as check_lock:
            ctrl_fd = _directory(control, 0o750, make=startup, exact_links=2)
            try:
                if startup:
                    # Startup preserves existing native payloads and does not
                    # parse or change the unrelated durable maintenance record.
                    with _opened_file(ctrl_fd, SWITCH, 0o440) as item:
                        created = item is None
                    if created and not _write_once(ctrl_fd, SWITCH, b"", 0o440):
                        raise HoldError("RACING_KILLSWITCH_CREATION")
                    with _opened_file(ctrl_fd, SWITCH, 0o440) as item:
                        if item is None:
                            raise HoldError("STARTUP_MARKER_MISSING")
                        _bound_directory(control.parent, parent_fd)
                        _bound_directory(control, ctrl_fd)
                        check_lock()
                        if _identity(os.fstat(item)) != _identity(
                                os.stat(SWITCH, dir_fd=ctrl_fd, follow_symlinks=False)):
                            raise HoldError("STARTUP_MARKER_CHANGED")
                    os.fsync(parent_fd)
                    return {"action": action, "state": "ENGAGED", "created_marker": created,
                            "authorization_effect": "BLOCK_RISK_INCREASE_ONLY",
                            "may_stop_driver": False, "broker_orders_sent": 0}
                maint = state_root / "operator-maintenance"
                maint_fd = _directory(maint, 0o700, make=True)
                try:
                    hold_raw = _file(maint_fd, HOLD, 0o600)
                    record = _hold_record(hold_raw)
                    hold = record is not None
                    marker = _file(ctrl_fd, SWITCH, 0o440)
                    # Native Execution accepts a 0440 single-link marker even
                    # when the payload is empty. It creates empty markers on
                    # startup/recovery; that is an INDEPENDENT safety hold, not
                    # the ordinary CLI's exactly tagged pause.
                    independent_empty = marker == b""
                    other_native = marker is not None and marker not in (b"engaged\n", b"")
                    if other_native and action != "maintenance-status":
                        raise HoldError("UNKNOWN_EXISTING_NATIVE_KILLSWITCH")
                    if action == "paper-on":
                        if hold:
                            raise HoldError("BLOCKED_BY_PERSISTENT_MAINTENANCE_HOLD")
                        if resume_native_hold:
                            if not independent_empty:
                                raise HoldError("RESUME_REQUIRES_INDEPENDENT_EMPTY_NATIVE_HOLD")
                            return _resume_native_hold(
                                control, state_root, parent_fd, ctrl_fd, maint_fd, check_lock,
                                check_only=check_only)
                        if marker is not None:
                            if independent_empty:
                                raise HoldError("INDEPENDENT_NATIVE_HOLD_NOT_REARMABLE")
                            _unlink(ctrl_fd, SWITCH, 0o440)
                    elif action in ("pause", "maintenance-hold"):
                        if action == "maintenance-hold" and not hold:
                            payload = {"schema": SCHEMA, "uid": os.geteuid(),
                                       "effect": "NEW_PAPER_RISK_HOLD_ONLY",
                                       "created_at_ms": int(time.time() * 1000),
                                       "created_by": "heptatrader-cli", "created_by_pid": os.getpid(),
                                       **{key: value for key, value in metadata.items() if value is not None}}
                            raw = (json.dumps(payload, sort_keys=True, separators=(",", ":"))
                                   + "\n").encode()
                            if len(raw) > 4096:
                                raise HoldError("HOLD_DESCRIPTION_TOO_LARGE")
                            if not _write_once(maint_fd, HOLD, raw, 0o600):
                                raise HoldError("RACING_HOLD_CREATION")
                            hold = True
                        if marker is None:
                            if not _write_once(ctrl_fd, SWITCH, b"engaged\n", 0o440):
                                raise HoldError("RACING_KILLSWITCH_CREATION")
                    elif action == "maintenance-release":
                        if not hold or marker is None:
                            raise HoldError("RELEASE_REQUIRES_BOTH_VALID_MARKERS")
                        # Match the caller's original hold under the same lock
                        # used to read and unlink it; --task is descriptive only.
                        if (expected_hold_sha256 is not None
                                and hashlib.sha256(hold_raw).hexdigest() != expected_hold_sha256):
                            raise HoldError("MAINTENANCE_HOLD_IDENTITY_MISMATCH")
                        # Under the same exclusive lock. Keep native entry block
                        # installed until the explicit persistent latch is retired.
                        _unlink(maint_fd, HOLD, 0o600)
                        hold = False
                        try:
                            # Release only the operator's own tagged CLI marker.
                            # Execution's zero-byte recovery marker is a separate
                            # owner and must survive maintenance-release intact.
                            if not independent_empty:
                                _unlink(ctrl_fd, SWITCH, 0o440)
                        except Exception:
                            # Restore the original owner's reason and creation time.
                            _write_once(maint_fd, HOLD, hold_raw, 0o600)
                            raise
                    # Report the post-action state, without granting service-stop authority.
                    marker = _file(ctrl_fd, SWITCH, 0o440)
                    hold_raw = _file(maint_fd, HOLD, 0o600)
                    record = _hold_record(hold_raw)
                    return _status(action, marker, record, hold_raw)
                finally:
                    os.close(maint_fd)
            finally:
                os.close(ctrl_fd)
    finally:
        os.close(parent_fd)


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=sorted(ACTIONS))
    ap.add_argument("--reason", help="optional reason for a newly created maintenance hold")
    ap.add_argument("--task", help="optional task associated with a new hold")
    ap.add_argument("--resume-when", help="optional resume condition; does not auto-release a hold")
    ap.add_argument("--expected-hold-sha256", help="release only the exact original maintenance hold bytes")
    ap.add_argument("--resume-native-hold", action="store_true",
                    help="explicitly release an independently engaged empty native PAPER hold")
    ap.add_argument("--check", action="store_true",
                    help="read-only precheck; actual native release is verified only after explicit resume")
    args = ap.parse_args(argv)
    uid = os.geteuid()
    expected_control = Path(f"/run/user/{uid}/heptatrader/control")
    expected_state = Path(pwd.getpwuid(uid).pw_dir) / ".local/state/heptatrader"
    try:
        status = perform(args.action, expected_control, expected_state,
                         reason=args.reason, task=args.task, resume_when=args.resume_when,
                         resume_native_hold=args.resume_native_hold, check_only=args.check,
                         expected_hold_sha256=args.expected_hold_sha256)
    except (HoldError, OSError, ValueError) as exc:
        print(json.dumps({"status": exc.status if isinstance(exc, ResumeFailure) else "HOLD_BLOCKED",
                          "reason": str(exc)[:200],
                          "may_stop_driver": False, "broker_orders_sent": 0}))
        return 75
    print(json.dumps(status, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
