#!/usr/bin/env python3
"""Activate one prepared PAPER driver under the existing maintenance hold.

The native audit is consumed in this transaction. Readiness observations only
schedule another fresh audit; they never authorize a service stop.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import pwd
import re
import stat
import subprocess
import tempfile
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path

import hepta_maintenance_latch as latch
import install_decision_attribution as attribution
import install_risk_stop_fence as fence
from hepta_decision_attribution import read_private, strict

GUARD_YES = fence.GUARD_TEXT.encode()
GUARD_NO = b"[Unit]\nRefuseManualStop=no\n"
EXEC_DROPIN = "70-exact-attribution.conf"
AUDIT_ENV = frozenset({
    "HEPTA_IB_PAPER_USER_SCOPE", "HEPTA_EXECUTION_REMOTE_MODE",
    "HEPTA_EXECUTION_SOCKET", "HEPTA_EXECUTION_EVENT_SOCKET",
    "HEPTA_EXECUTION_SERVICE_UID", "HEPTA_EXECUTION_IO_TIMEOUT_MS",
    "HEPTA_EXECUTION_RESPONSE_TIMEOUT_MS", "HEPTA_EXECUTION_MAX_RESPONSE_BYTES",
    "HEPTA_TOOL_SUPERVISOR_LEASE_STORE",
    "HEPTA_TOOL_SUPERVISOR_LEASE_KEY_FILE", "CREDENTIALS_DIRECTORY",
    "HEPTA_TOOL_SUPERVISOR_UID", "HEPTA_TOOL_AGENT_UID",
    "HEPTA_TOOL_AGENT_ID", "HEPTA_TOOL_ACCOUNT", "HEPTA_EXECUTION_DOMAIN_ID",
})
RETRY_REASONS = frozenset({
    "IB_RECOVERY_AUDIT_NEW_CONNECTION_EPOCH_REQUIRED",
    "IB_RECOVERY_AUDIT_BARRIER_NOT_COMPLETE",
})
BUSINESS_FIELDS = frozenset({
    "entries", "exits", "gross_pnl", "net_pnl", "closed_trades",
    "btc_entry_control", "daily_opening_position", "day_one_started_at_ms",
    "fees_complete", "pnl_basis", "pnl_currency", "opening_purchase_fill_price",
    "partial_exit_fills", "crypto_roundtrip_proof", "crypto_exit_evidence",
})


def fail(reason):
    raise RuntimeError(reason)


def sha_bytes(raw):
    return hashlib.sha256(raw).hexdigest()


def ctl(*args):
    subprocess.run(
        ["systemctl", "--user", *args],
        check=True, timeout=45, capture_output=True, text=True,
    )


def replace_exact(path, expected, replacement):
    """Replace an existing private drop-in while operator_lock is held."""
    path = Path(path)
    before = path.lstat()
    if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
            or before.st_uid != os.geteuid() or before.st_mode & 0o077):
        fail("UNSAFE_EXISTING_DROPIN:" + path.name)
    if read_private(path, 65536) != expected:
        fail("DROPIN_CHANGED:" + path.name)
    fd, temporary = tempfile.mkstemp(prefix="." + path.name + "-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), stat.S_IMODE(before.st_mode))
            stream.write(replacement)
            stream.flush()
            os.fsync(stream.fileno())
        current = path.lstat()
        if ((current.st_dev, current.st_ino) != (before.st_dev, before.st_ino)
                or read_private(path, 65536) != expected):
            fail("DROPIN_CHANGED:" + path.name)
        os.replace(temporary, path)
        directory = os.open(
            path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
        )
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)


def restore_exact(path, original, ours):
    current = read_private(path, 65536)
    if current == original:
        return False
    if current != ours:
        fail("DROPIN_REPLACED_BY_ANOTHER_WRITER:" + Path(path).name)
    replace_exact(path, ours, original)
    return True


def marker_identity(home):
    """Read existing markers without reacquiring operator_lock."""
    state_root = home / ".local/state/heptatrader"
    control = Path("/run/user") / str(os.geteuid()) / "heptatrader/control"
    parent_fd = latch._directory(control.parent, 0o700)
    control_fd = maintenance_fd = None
    try:
        control_fd = latch._directory(control, 0o750, exact_links=2)
        maintenance_fd = latch._directory(state_root / "operator-maintenance", 0o700)
        hold = latch._file(maintenance_fd, latch.HOLD, 0o600)
        if latch._hold_record(hold) is None:
            fail("DURABLE_MAINTENANCE_HOLD_REQUIRED")
        kill = latch._file(control_fd, latch.SWITCH, 0o440)
        if kill not in (b"", b"engaged\n"):
            fail("EXISTING_NATIVE_KILL_REQUIRED")
        result = []
        for fd, name, raw in (
            (maintenance_fd, latch.HOLD, hold), (control_fd, latch.SWITCH, kill),
        ):
            item = os.stat(name, dir_fd=fd, follow_symlinks=False)
            result.append((
                item.st_dev, item.st_ino, item.st_ctime_ns, sha_bytes(raw),
            ))
        return tuple(result)
    finally:
        for fd in (maintenance_fd, control_fd, parent_fd):
            if fd is not None:
                os.close(fd)


def proc_bytes(pid, name, limit=65536):
    path = Path("/proc") / str(pid) / name
    if path.stat().st_uid != os.geteuid():
        fail("PROCESS_OWNER_CHANGED")
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        fail("PROCESS_FIELD_TOO_LARGE:" + name)
    return raw


def pid_key(pid):
    try:
        raw = proc_bytes(pid, "stat").decode()
    except FileNotFoundError:
        return None
    fields = raw.rsplit(")", 1)[1].split()
    if len(fields) <= 19:
        fail("PROCESS_IDENTITY_INVALID")
    return str(pid), fields[19]


def argv_for(manifest):
    manifest = Path(manifest)
    if (not manifest.is_absolute() or manifest.name != "manifest.json"
            or re.search(r"\s|;", str(manifest))):
        fail("MANIFEST_PATH_INVALID")
    return [
        "/usr/bin/python3", "-B",
        str(manifest.parent / "heptatrader_attributed_driver.py"),
        "--manifest", str(manifest),
    ]


def inspect_manifest(manifest, instrument):
    argv = argv_for(manifest)
    # Reuse release-byte validation, without claiming this is a running process.
    observed = attribution.inspect_resident(
        "argv[]=" + " ".join(argv) + ";", instrument,
    )
    if observed["status"] != "INSTALLED_VERIFIED":
        fail("RELEASE_NOT_VERIFIED")
    return observed


def running_driver(unit, instrument, *, require_configured_match=True):
    status = fence.show_unit(unit)
    if (status["ActiveState"] != "active"
            or not status["MainPID"].isdigit()
            or int(status["MainPID"]) <= 0 or not status["InvocationID"]):
        fail("DRIVER_NOT_ACTIVE")
    pid = int(status["MainPID"])
    key = pid_key(pid)
    service = attribution.show(unit)
    if (service["MainPID"] != status["MainPID"]
            or service["ActiveState"] != status["ActiveState"]):
        fail("DRIVER_CHANGED_DURING_INSPECTION")
    argv = proc_bytes(pid, "cmdline").rstrip(b"\0").split(b"\0")
    if require_configured_match:
        observed = attribution.inspect_resident(service["ExecStart"], instrument)
    else:
        if len(argv) != 5:
            fail("RUNNING_DRIVER_ARGV_MISMATCH")
        try:
            actual_manifest = Path(argv[4].decode("utf-8"))
        except UnicodeDecodeError:
            fail("RUNNING_DRIVER_ARGV_MISMATCH")
        # argv_for validates the exact permitted invocation and path syntax;
        # inspect_resident remains the one validator of the manifest and bytes.
        command = "ExecStart={ argv[]=" + " ".join(argv_for(actual_manifest)) + " ; }"
        observed = attribution.inspect_resident(command, instrument)
    if observed["status"] != "INSTALLED_VERIFIED":
        fail("EXISTING_ATTRIBUTION_REQUIRED")
    manifest = Path(observed["release"]) / "manifest.json"
    if argv != [item.encode() for item in argv_for(manifest)]:
        fail("RUNNING_DRIVER_ARGV_MISMATCH")
    if key is None or pid_key(pid) != key:
        fail("DRIVER_CHANGED_DURING_INSPECTION")
    final = fence.show_unit(unit)
    for name in ("MainPID", "InvocationID", "ActiveState"):
        if final[name] != status[name]:
            fail("DRIVER_CHANGED_DURING_INSPECTION")
    return {
        "status": final, "manifest": str(manifest),
        "manifest_sha256": observed["manifest_sha256"],
        "runtime_sha256": observed["runtime_sha256"], "pid_key": key,
        "source_sha": observed.get("source_sha"), "release": observed["release"],
    }


def same_driver(expected, actual):
    for name in ("MainPID", "InvocationID", "ActiveState"):
        if expected["status"][name] != actual["status"][name]:
            fail("DRIVER_PROCESS_CHANGED")
    for name in ("manifest", "manifest_sha256", "pid_key"):
        if expected[name] != actual[name]:
            fail("DRIVER_PROCESS_CHANGED")


def read_flat_state(path, instrument):
    raw = read_private(path, 1048576)
    value = strict(raw)
    if (not isinstance(value, dict) or value.get("instrument") != instrument
            or "position" not in value or "pending" not in value
            or type(value["position"]) not in (int, float)
            or value["pending"] is not None):
        fail("DRIVER_STATE_NOT_EXPLICITLY_FLAT")
    position = Decimal(str(value["position"]))
    if not position.is_finite() or position != 0:
        fail("DRIVER_STATE_NOT_EXPLICITLY_FLAT")
    for name in ("entries", "exits"):
        if type(value.get(name)) is not int or value[name] < 0:
            fail("DRIVER_ACCOUNTING_INVALID:" + name)
    # Old entry correlations can remain populated at zero position. Keep them.
    # This one entry-prefixed field is live loop telemetry, not trade identity:
    # it can appear, change its timestamp/reason, or disappear under the hold.
    # Unknown entry_* fields remain protected.
    correlation = {
        name: item for name, item in value.items()
        if name in BUSINESS_FIELDS or (
            name.startswith("entry_") and name != "entry_inference_pause"
        )
    }
    return raw, correlation


def resident_loop_progress(raw, driver, started_ms, checked_ms):
    """Recognize progress emitted after driver startup for this exact process.

    The field is diagnostic only. Release identity, holds, owner audit and
    unchanged business correlations are still checked independently.
    """
    value = strict(raw).get("resident_loop_progress")
    if (not isinstance(value, dict)
            or value.get("schema") != "heptatrader.driver-loop-progress.v1"
            or type(value.get("pid")) is not int
            or type(value.get("start_ticks")) is not str
            or type(value.get("iteration")) is not int or value["iteration"] < 1
            or type(value.get("at_ms")) is not int
            or not started_ms <= value["at_ms"] <= checked_ms
            or (str(value["pid"]), value["start_ticks"]) != tuple(driver["pid_key"])):
        return None
    return {name: value[name] for name in ("schema", "pid", "start_ticks", "iteration", "at_ms")}


def state_version(path):
    item = Path(path).lstat()
    return item.st_dev, item.st_ino, item.st_mtime_ns, item.st_ctime_ns


def open_driver_lock(path):
    fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        item = os.fstat(fd)
        if (not stat.S_ISREG(item.st_mode) or item.st_nlink != 1
                or item.st_uid != os.geteuid() or item.st_mode & 0o077):
            fail("UNSAFE_DRIVER_LOCK")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except BaseException:
        os.close(fd)
        raise


def check_owner_zero(reply):
    if not isinstance(reply, dict):
        fail("OWNER_AUDIT_NOT_OBJECT")
    if (reply.get("schema") != "hepta_session_owner_audit_v1"
            or reply.get("operation") != "owner-audit"
            or reply.get("accepted") is not True
            or type(reply.get("native_status")) is not int
            or reply["native_status"] != 0 or reply.get("validation_reason") != ""):
        fail("OWNER_AUDIT_NOT_ACCEPTED")
    for name in (
        "command_id", "agent_id", "session_id", "service_epoch",
        "expected_owner_account", "expected_owner_execution_domain",
    ):
        if type(reply.get(name)) is not str or not reply[name]:
            fail("OWNER_AUDIT_IDENTITY_MISSING:" + name)
    if len(reply["service_epoch"]) > 128:
        fail("OWNER_AUDIT_EPOCH_INVALID")
    generation = reply.get("service_fencing_generation")
    if type(generation) is not int or not 0 < generation < 2**64:
        fail("OWNER_AUDIT_FENCING_INVALID")
    if (reply.get("owner_account") != reply["expected_owner_account"]
            or reply.get("owner_execution_domain") !=
            reply["expected_owner_execution_domain"]):
        fail("OWNER_AUDIT_SCOPE_MISMATCH")
    for name in (
        "owner_audit_authoritative", "owner_audit_complete",
        "broker_recovery_audit_barrier_complete",
    ):
        if reply.get(name) is not True:
            fail("OWNER_AUDIT_INCOMPLETE:" + name)
    for name in (
        "broker_post_fill_risk_reconciliation_pending",
        "broker_recovery_audit_new_connection_epoch_required",
    ):
        if reply.get(name) is not False:
            fail("OWNER_AUDIT_RECOVERY_PENDING:" + name)
    for name in (
        "owner_active_order_count", "owner_uncertain_command_count",
        "affected_count", "broker_global_active_order_count",
    ):
        if type(reply.get(name)) is not int or reply[name] != 0:
            fail("OWNER_AUDIT_NONZERO:" + name)
    for name in ("broker_position_quantity", "broker_gross_absolute_position"):
        if type(reply.get(name)) is not str:
            fail("OWNER_AUDIT_POSITION_INVALID:" + name)
        try:
            value = Decimal(reply[name])
        except InvalidOperation as error:
            raise RuntimeError("OWNER_AUDIT_POSITION_INVALID:" + name) from error
        if not value.is_finite() or value != 0:
            fail("OWNER_AUDIT_POSITION_NONZERO:" + name)
    # This field is not the external kill marker, and is not an Accepted veto.
    if type(reply.get("mutation_blocked")) is not bool:
        fail("OWNER_AUDIT_MUTATION_FIELD_INVALID")
    positive = (
        "broker_connection_epoch", "broker_active_generation",
        "broker_terminal_generation", "broker_risk_generation",
        "broker_account_generation", "broker_position_generation",
        "broker_fx_cash_generation",
    )
    for name in positive:
        if type(reply.get(name)) is not int or not 0 < reply[name] < 2**64:
            fail("OWNER_AUDIT_GENERATION_INVALID:" + name)
    for name in (
        "broker_exposure_generation", "broker_terminal_exposure_generation",
        "broker_risk_absorbed_exposure_generation",
    ):
        if type(reply.get(name)) is not int or not 0 <= reply[name] < 2**64:
            fail("OWNER_AUDIT_GENERATION_INVALID:" + name)
    if not (
        reply["broker_terminal_exposure_generation"]
        <= reply["broker_risk_absorbed_exposure_generation"]
        == reply["broker_exposure_generation"]
    ):
        fail("OWNER_AUDIT_EXPOSURE_NOT_ABSORBED")
    return reply


def audit_environment(gateway_pid):
    values = {}
    for item in proc_bytes(gateway_pid, "environ", 262144).split(b"\0"):
        if not item:
            continue
        key, separator, value = item.partition(b"=")
        if not separator:
            fail("GATEWAY_ENVIRONMENT_INVALID")
        key = key.decode()
        if key not in AUDIT_ENV:
            continue
        if key in values:
            fail("GATEWAY_ENVIRONMENT_DUPLICATE:" + key)
        values[key] = value.decode()
    return values


def verify_binary(path, expected_sha):
    path = Path(path)
    item = path.lstat()
    if (not path.is_absolute() or not stat.S_ISREG(item.st_mode)
            or item.st_nlink != 1 or item.st_uid != os.geteuid()
            or item.st_mode & 0o022 or not item.st_mode & 0o111
            or attribution.sha(path) != expected_sha):
        fail("AUDIT_BINARY_CHANGED")


def verify_prepared(candidate, candidate_sha, instrument, installed):
    candidate = Path(candidate)
    target = inspect_manifest(candidate, instrument)
    if target["manifest_sha256"] != candidate_sha:
        fail("CANDIDATE_MANIFEST_CHANGED")
    installed_raw = read_private(installed["manifest"], 65536)
    if sha_bytes(installed_raw) != installed["manifest_sha256"]:
        fail("INSTALLED_MANIFEST_CHANGED")
    old = strict(installed_raw)
    new = strict(read_private(candidate, 65536))
    if (type(new.get("source_sha")) is not str
            or re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})",
                            new["source_sha"]) is None):
        fail("CANDIDATE_SOURCE_IDENTITY_INVALID")
    changed = new["runtime_sha256"] != old["runtime_sha256"]
    read_transport_changed = (
        new.get("native_read_stream", False) != old.get("native_read_stream", False)
        or new.get("native_read_binary") != old.get("native_read_binary")
    )
    expected = dict(old)
    expected.pop("created_at_ms", None)
    expected.update(
        source_sha=new["source_sha"], runtime_sha256=new["runtime_sha256"],
        prepared_from_manifest_sha256=installed["manifest_sha256"],
        runtime_bytes_changed=changed,
        trading_behavior_changed=(
            old.get("trading_behavior_changed") is not False or changed or read_transport_changed
        ),
        behavior_equivalence_assessed=False,
    )
    # inspect_manifest has already accepted exactly a current or immutable
    # legacy runtime layout. Keep that candidate's layout when validating the
    # preparation, including pre-existing legacy candidates used for rollback.
    expected.pop("runtime_loader", None)
    if "runtime_loader" in new:
        expected["runtime_loader"] = new["runtime_loader"]
    expected.pop("native_read_stream", None)
    if "native_read_stream" in new:
        expected["native_read_stream"] = new["native_read_stream"]
    expected.pop("native_read_binary", None)
    if "native_read_binary" in new:
        expected["native_read_binary"] = new["native_read_binary"]
    names = set(target["runtime_sha256"])
    if instrument == "BTC.USD":
        names = names | {"profile.json"}
    files = {
        name: read_private(candidate.parent / name, 1048576) for name in names
    }
    verified = attribution._verify_prepared(candidate.parent, expected, files)
    if verified != candidate_sha:
        fail("CANDIDATE_MANIFEST_CHANGED")
    if "native_read_binary" in new:
        with attribution.verified_binary(new["native_read_binary"]):
            pass
    return target


def wait_for_native_recovery(reply, completed_at_ms, deadline):
    """Wait for a newer passive observation before a fresh audit."""
    expected_epoch = reply.get("service_epoch")
    if (type(expected_epoch) is not str or not expected_epoch
            or type(completed_at_ms) is not int):
        return False
    command = [
        "journalctl", "--user", "-u", "heptatrader-execution.service",
        "-n", "100", "-o", "cat", "--no-pager",
    ]
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        result = None
        try:
            result = subprocess.run(
                command, stdin=subprocess.DEVNULL, capture_output=True,
                timeout=min(5, remaining), check=False,
            )
        except subprocess.TimeoutExpired:
            pass
        except OSError:
            return False
        if result is not None:
            if result.returncode != 0:
                return False
            latest = None
            for line in reversed(result.stdout.split(b"\n")[:-1]):
                try:
                    record = strict(line)
                except (ValueError, TypeError, UnicodeDecodeError):
                    continue
                if (isinstance(record, dict) and record.get("schema")
                        == "heptatrader.ib-runtime-observation.v1"):
                    latest = record
                    break
            if latest is not None:
                if latest.get("service_epoch") != expected_epoch:
                    return False
                observed = latest.get("observed_at_ms")
                now_ms = int(time.time() * 1000)
                if (type(observed) is int and observed >= completed_at_ms
                        and 0 <= now_ms - observed <= 15000
                        and latest.get("recovery_barrier_complete") is True
                        and latest.get("new_connection_epoch_required") is False):
                    return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(1, remaining))


def maintenance_upgrade(
    instrument, candidate, candidate_sha, sessionctl, sessionctl_sha, *,
    readiness_hook=None, home=None,
):
    """Activate one lane; each stop consumes a fresh audit under the same hold.

    readiness_hook(reply, completed_at_ms, monotonic_deadline) may request
    another fresh audit. It cannot provide an audit result itself.
    """
    if instrument not in ("EUR.USD", "BTC.USD"):
        fail("UNSUPPORTED_DRIVER_LANE")
    if os.geteuid() == 0:
        fail("USER_SERVICE_ONLY")
    home = Path(home or pwd.getpwuid(os.geteuid()).pw_dir)
    candidate = Path(candidate)
    state_root = home / ".local/state/heptatrader"
    unit = attribution.unit_for(instrument)
    lane = "paper-driver" if instrument == "EUR.USD" else "crypto-driver"
    state_path = state_root / lane / "state.json"
    lock_path = state_root / lane / "driver.lock"
    unit_root = home / ".config/systemd/user"
    exec_path = unit_root / (unit + ".d") / EXEC_DROPIN
    guard_path = unit_root / (unit + ".d") / fence.GUARD_NAME
    token_path = home / ".config/heptatrader" / (
        "paper.token" if instrument == "EUR.USD" else "crypto.token"
    )
    receipt = {
        "schema": "heptatrader.driver-maintenance-upgrade.v1",
        "instrument": instrument, "unit": unit,
        "candidate_manifest": str(candidate),
        "candidate_manifest_sha256": candidate_sha,
        "trade_authority": False, "forced_orders": 0,
        "audit_attempts": [], "operations": [], "status": "PREPARING",
    }
    with latch.operator_lock(state_root):
        original = running_driver(unit, instrument)
        target = inspect_manifest(candidate, instrument)
        if target["manifest_sha256"] != candidate_sha:
            fail("CANDIDATE_MANIFEST_CHANGED")
        if (original["manifest"] == str(candidate)
                and original["manifest_sha256"] == candidate_sha):
            receipt.update(
                status="ALREADY_ACTIVE", service_changed=False, actual=original,
            )
            return receipt
        verify_prepared(candidate, candidate_sha, instrument, original)
        markers = marker_identity(home)
        before = {name: fence.show_unit(name) for name in fence.RISK_UNITS}
        fence.check_state(before, before, required=True)
        same_driver(original, running_driver(unit, instrument))
        peer_before = {
            name: status for name, status in before.items() if name != unit
        }
        guard_paths = {
            name: unit_root / (name + ".d") / fence.GUARD_NAME
            for name in fence.RISK_UNITS
        }
        for path in guard_paths.values():
            if read_private(path, 65536) != GUARD_YES:
                fail("EXISTING_STOP_GUARD_CHANGED")
        old_exec = read_private(exec_path, 65536)
        expected_old_exec = (
            "[Service]\nExecStart=\nExecStart="
            + " ".join(argv_for(original["manifest"])) + "\n"
        ).encode()
        if old_exec != expected_old_exec:
            fail("EXISTING_EXECSTART_DROPIN_CHANGED")
        new_exec = (
            "[Service]\nExecStart=\nExecStart="
            + " ".join(argv_for(candidate)) + "\n"
        ).encode()
        _, original_correlation = read_flat_state(state_path, instrument)
        receipt["original_business_correlation"] = original_correlation
        verify_binary(sessionctl, sessionctl_sha)
        environment = audit_environment(
            int(before["heptatrader-gateway.service"]["MainPID"]),
        )
        receipt["original"] = original
        receipt["original_execstart_sha256"] = sha_bytes(old_exec)
        receipt["candidate_execstart_sha256"] = sha_bytes(new_exec)
        receipt["original_guard_sha256"] = sha_bytes(GUARD_YES)

        def context(expected_driver=None, guard=GUARD_YES):
            if marker_identity(home) != markers:
                fail("MAINTENANCE_MARKERS_CHANGED")
            current = {name: fence.show_unit(name) for name in fence.RISK_UNITS}
            fence.check_state(peer_before, current, required=True)
            for name, path in guard_paths.items():
                expected = guard if name == unit else GUARD_YES
                if read_private(path, 65536) != expected:
                    fail("STOP_GUARD_CHANGED:" + name)
            wanted = "yes" if guard == GUARD_YES else "no"
            if current[unit]["RefuseManualStop"] != wanted:
                fail("SELECTED_STOP_GUARD_NOT_EFFECTIVE")
            for field in ("LoadState", "PartOf"):
                if current[unit][field] != before[unit][field]:
                    fail("SELECTED_UNIT_DEPENDENCY_CHANGED")
            if expected_driver is not None:
                same_driver(expected_driver, running_driver(unit, instrument))

        def ensure_guard():
            changed = restore_exact(guard_path, GUARD_YES, GUARD_NO)
            # A preceding daemon-reload can have failed after the file was
            # restored; readback, not the local phase, decides whether to reload.
            if changed or fence.show_unit(unit)["RefuseManualStop"] != "yes":
                ctl("daemon-reload")
                receipt["operations"].append("RESTORE_SELECTED_STOP_GUARD")
            if fence.show_unit(unit)["RefuseManualStop"] != "yes":
                fail("SELECTED_STOP_GUARD_NOT_RESTORED")

        def state_unchanged():
            raw, correlation = read_flat_state(state_path, instrument)
            if correlation != original_correlation:
                receipt["business_correlation_changed_fields"] = sorted(
                    name for name in correlation.keys() | original_correlation.keys()
                    if name not in correlation or name not in original_correlation
                    or correlation[name] != original_correlation[name]
                )
                fail("DRIVER_BUSINESS_CORRELATION_CHANGED")
            return raw

        def stopped(expected_driver):
            status = fence.show_unit(unit)
            if (status["ActiveState"] != "inactive"
                    or status["MainPID"] not in ("", "0")):
                fail("DRIVER_STOP_NOT_COMPLETE")
            old_pid = expected_driver["status"]["MainPID"]
            if pid_key(old_pid) == expected_driver["pid_key"]:
                fail("OLD_DRIVER_PROCESS_STILL_PRESENT")

        def fresh_audit(expected_driver):
            deadline = time.monotonic() + 60
            for attempt in range(3):
                context(expected_driver)
                verify_binary(sessionctl, sessionctl_sha)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    fail("OWNER_AUDIT_WAIT_EXPIRED")
                result = subprocess.run(
                    [str(sessionctl), "owner-audit", "--token-file", str(token_path)],
                    env=environment, capture_output=True, timeout=min(15, remaining),
                )
                completed_ms = int(time.time() * 1000)
                verify_binary(sessionctl, sessionctl_sha)
                context(expected_driver)
                if len(result.stdout) > 1048576:
                    fail("OWNER_AUDIT_RESPONSE_TOO_LARGE")
                if not result.stdout:
                    fail("OWNER_AUDIT_NO_RESPONSE:" +
                         result.stderr[:4096].decode(errors="replace").strip())
                try:
                    reply = strict(result.stdout)
                except (ValueError, TypeError, UnicodeDecodeError) as error:
                    raise RuntimeError("OWNER_AUDIT_RESPONSE_INVALID") from error
                receipt["audit_attempts"].append({
                    "returncode": result.returncode,
                    "completed_at_ms": completed_ms, "reply": reply,
                })
                if result.returncode == 0:
                    check_owner_zero(reply)
                    context(expected_driver)
                    return reply
                retry = (
                    readiness_hook is not None and result.returncode == 4
                    and isinstance(reply, dict)
                    and reply.get("schema") == "hepta_session_owner_audit_v1"
                    and reply.get("operation") == "owner-audit"
                    and reply.get("validation_reason") == ""
                    and reply.get("reason_code") in RETRY_REASONS
                    and type(reply.get("service_epoch")) is str
                    and 0 < len(reply["service_epoch"]) <= 128
                    and type(reply.get("service_fencing_generation")) is int
                    and reply["service_fencing_generation"] > 0 and attempt < 2
                )
                if not retry:
                    reason = (reply.get("reason_code", "NO_NATIVE_REASON")
                              if isinstance(reply, dict) else "INVALID_REPLY")
                    fail("OWNER_AUDIT_FAILED:" + reason)
                if readiness_hook(reply, completed_ms, deadline) is not True:
                    fail("OWNER_AUDIT_RECOVERY_NOT_READY")
                if time.monotonic() >= deadline:
                    fail("OWNER_AUDIT_WAIT_EXPIRED")
            fail("OWNER_AUDIT_ATTEMPTS_EXHAUSTED")

        def start_verified(manifest, manifest_sha):
            # operator_lock stays held, but driver.lock must be released.
            context()
            baseline = state_version(state_path)
            started_ms = int(time.time() * 1000)
            ctl("start", unit)
            receipt["operations"].append("START_SELECTED_DRIVER")
            deadline = time.monotonic() + 30
            first = None
            writes = set()
            while True:
                try:
                    actual = running_driver(unit, instrument)
                    if (actual["manifest"] != str(manifest)
                            or actual["manifest_sha256"] != manifest_sha):
                        fail("STARTED_DRIVER_RELEASE_MISMATCH")
                    if first is None:
                        first = actual
                    else:
                        same_driver(first, actual)
                    raw = state_unchanged()
                    progress = resident_loop_progress(raw, actual, started_ms, int(time.time() * 1000))
                    version = state_version(state_path)
                    if version != baseline:
                        writes.add(version)
                    # New drivers report entry into the loop for this PID/start
                    # identity. Retain the previous observation for rollback to
                    # releases predating this field; it needs three state writes.
                    if progress is not None or len(writes) >= 3:
                        context(actual)
                        state_unchanged()
                        receipt["startup_observed_state_versions"] = len(writes)
                        receipt["startup_progress_basis"] = (
                            "PID_BOUND_RESIDENT_LOOP" if progress is not None else "LEGACY_STATE_WRITES"
                        )
                        if progress is not None:
                            receipt["startup_resident_loop_progress"] = progress
                        return actual
                except (RuntimeError, FileNotFoundError) as error:
                    transient = isinstance(error, FileNotFoundError) or str(error) in {
                        "DRIVER_NOT_ACTIVE", "RUNNING_DRIVER_ARGV_MISMATCH",
                        "DRIVER_CHANGED_DURING_INSPECTION",
                    }
                    if not transient:
                        raise
                    status = fence.show_unit(unit)
                    if status["ActiveState"] == "failed" and status["MainPID"] in ("", "0"):
                        fail("DRIVER_START_FAILED")
                if time.monotonic() >= deadline:
                    fail("DRIVER_STARTUP_LOOP_NOT_OBSERVED")
                time.sleep(0.2)

        def switch(expected_driver, expected_exec, replacement,
                   manifest, manifest_sha):
            """Activation and rollback use the same audited stop sequence."""
            context(expected_driver)
            state_unchanged()
            if read_private(exec_path, 65536) != expected_exec:
                fail("EXECSTART_CHANGED_BEFORE_AUDIT")
            audit = fresh_audit(expected_driver)
            receipt["last_consumed_audit_command_id"] = audit["command_id"]
            try:
                replace_exact(guard_path, GUARD_YES, GUARD_NO)
                ctl("daemon-reload")
                receipt["operations"].append("ALLOW_SELECTED_NORMAL_STOP")
                context(expected_driver, GUARD_NO)
                ctl("stop", unit)
                receipt["operations"].append("STOP_SELECTED_DRIVER")
                stopped(expected_driver)
                context(guard=GUARD_NO)
                driver_fd = open_driver_lock(lock_path)
                try:
                    stopped(expected_driver)
                    raw = state_unchanged()
                    replace_exact(exec_path, expected_exec, replacement)
                    receipt["operations"].append("REPLACE_SELECTED_EXECSTART")
                    replace_exact(guard_path, GUARD_NO, GUARD_YES)
                    ctl("daemon-reload")
                    receipt["operations"].append("RESTORE_SELECTED_STOP_GUARD")
                    context()
                    stopped(expected_driver)
                    if read_private(state_path, 1048576) != raw:
                        fail("STOPPED_DRIVER_STATE_CHANGED")
                    receipt["stopped_state_sha256"] = sha_bytes(raw)
                finally:
                    os.close(driver_fd)
                actual = start_verified(manifest, manifest_sha)
                if actual["pid_key"] == expected_driver["pid_key"]:
                    fail("DRIVER_PROCESS_NOT_REPLACED")
                return actual
            finally:
                ensure_guard()

        try:
            receipt["status"] = "ACTIVATING"
            actual = switch(
                original, old_exec, new_exec, candidate, candidate_sha,
            )
            receipt.update(
                status="ACTIVATED", service_changed=True, actual=actual,
                maintenance_markers_preserved=True, other_risk_units_preserved=True,
            )
            return receipt
        except BaseException as error:
            receipt["error"] = type(error).__name__ + ":" + str(error)
            receipt["status"] = "FAILED"
            try:
                ensure_guard()
                context()
                live = fence.show_unit(unit)
                if live["ActiveState"] == "active":
                    actual = running_driver(unit, instrument)
                    if (actual["manifest"] == original["manifest"]
                            and actual["manifest_sha256"] ==
                            original["manifest_sha256"]):
                        restore_exact(exec_path, old_exec, new_exec)
                        ctl("daemon-reload")
                        context(actual)
                        state_unchanged()
                        receipt["rollback"] = "ORIGINAL_DRIVER_RUNNING"
                        receipt["actual"] = actual
                    elif (actual["manifest"] == str(candidate)
                          and actual["manifest_sha256"] == candidate_sha):
                        receipt["actual"] = switch(
                            actual, new_exec, old_exec, Path(original["manifest"]),
                            original["manifest_sha256"],
                        )
                        receipt["rollback"] = "ORIGINAL_DRIVER_RESTORED"
                    else:
                        fail("UNKNOWN_ACTIVE_DRIVER_NOT_STOPPED")
                elif (live["ActiveState"] in ("inactive", "failed")
                      and live["MainPID"] in ("", "0")):
                    if pid_key(original["status"]["MainPID"]) == original["pid_key"]:
                        fail("OLD_DRIVER_PROCESS_STILL_PRESENT")
                    driver_fd = open_driver_lock(lock_path)
                    try:
                        raw = state_unchanged()
                        restore_exact(exec_path, old_exec, new_exec)
                        ensure_guard()
                        ctl("daemon-reload")
                        context()
                        if read_private(state_path, 1048576) != raw:
                            fail("STOPPED_DRIVER_STATE_CHANGED")
                    finally:
                        os.close(driver_fd)
                    receipt["actual"] = start_verified(
                        Path(original["manifest"]), original["manifest_sha256"],
                    )
                    receipt["rollback"] = "ORIGINAL_DRIVER_RESTORED"
                else:
                    fail("DRIVER_TRANSITION_UNRESOLVED")
            except BaseException as rollback_error:
                receipt["rollback"] = "INCOMPLETE_HELD"
                receipt["rollback_error"] = (
                    type(rollback_error).__name__ + ":" + str(rollback_error)
                )
            finally:
                try:
                    ensure_guard()
                    receipt["selected_stop_guard_restored"] = True
                except BaseException as guard_error:
                    receipt["selected_stop_guard_restored"] = False
                    receipt["guard_error"] = (
                        type(guard_error).__name__ + ":" + str(guard_error)
                    )
                try:
                    receipt["maintenance_markers_preserved"] = (
                        marker_identity(home) == markers
                    )
                except BaseException:
                    receipt["maintenance_markers_preserved"] = False
            return receipt


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--instrument", choices=("EUR.USD", "BTC.USD"), required=True)
    parser.add_argument("--candidate-manifest", type=Path, required=True)
    parser.add_argument("--candidate-sha256", required=True)
    parser.add_argument("--sessionctl", type=Path, required=True)
    parser.add_argument("--sessionctl-sha256", required=True)
    args = parser.parse_args()
    try:
        result = maintenance_upgrade(
            args.instrument, args.candidate_manifest, args.candidate_sha256,
            args.sessionctl, args.sessionctl_sha256,
            readiness_hook=wait_for_native_recovery,
        )
    except BaseException as error:
        result = {
            "schema": "heptatrader.driver-maintenance-upgrade.v1",
            "status": "FAILED_BEFORE_SWITCH",
            "error": type(error).__name__ + ":" + str(error),
            "trade_authority": False, "forced_orders": 0,
        }
    print(json.dumps(result, sort_keys=True), flush=True)
    return 0 if result["status"] in ("ACTIVATED", "ALREADY_ACTIVE") else 1


if __name__ == "__main__":
    raise SystemExit(main())
