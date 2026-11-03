#!/usr/bin/env python3
"""One Gateway handoff under the existing native PAPER hold.

Preflight is read-only. Activation keeps both native protections, uses normal
systemd stop with forced killing disabled, and reads the final authenticated
catalog after all Gateway workers exit. No order, permit or account fence is
created. A private checkpoint permits observation/recovery after interruption.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import shlex
import stat
import subprocess
import sys
import tempfile
import time
import uuid

import activate_prepared_driver as driver
import native_execution_maintenance as native_maintenance
from hepta_decision_attribution import read_private, strict

GATEWAY = "heptatrader-gateway.service"
EXECUTION = driver.EXECUTION_UNIT
POLICY = "zzzz-gateway-maintenance.conf"
RELEASE = "zzz-gateway-release.conf"
CHECKPOINT = "gateway-upgrade.json"
SAFE_STOP = b"[Unit]\nRefuseManualStop=no\n[Service]\nSendSIGKILL=no\nTimeoutStopSec=infinity\nRestart=no\n"
HELD_STOP = SAFE_STOP.replace(b"RefuseManualStop=no", b"RefuseManualStop=yes")
# The existing resident checks renewal every four hours. A shortened Resume
# must still live beyond that check; this transaction grants no extra TTL.
MIN_TTL_MS = 4 * 60 * 60 * 1000 + 120000
MAX_AUDIT_AGE_MS = 5000
AUDIT_MAXIMUM_BYTES = 1024 * 1024 * 1024
AUDIT_OFFLINE_TIMEOUT_SECONDS = 300
LANES = {"EUR.USD": ("paper-driver", "paper.token"), "BTC.USD": ("crypto-driver", "crypto.token")}


def fail(reason):
    raise RuntimeError(reason)


def digest(value):
    return hashlib.sha256(value.encode() if isinstance(value, str) else value).hexdigest()


def now_ms():
    return int(time.time() * 1000)


def atomic_json(path, value):
    path = Path(path)
    raw = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
    fd, temporary = tempfile.mkstemp(prefix="." + path.name + "-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)


def pinned(item):
    if not isinstance(item, dict) or re.fullmatch(r"[0-9a-f]{64}", item.get("sha256", "")) is None:
        fail("ARTIFACT_PIN_REQUIRED")
    driver.verify_binary(Path(item["path"]), item["sha256"])
    return Path(item["path"])


def release(item, fixes):
    raw = read_private(item["manifest"], 65536)
    if digest(raw) != item["sha256"]:
        fail("GATEWAY_RELEASE_MANIFEST_CHANGED")
    value = strict(raw)
    if (value.get("schema") != "heptatrader.gateway-maintenance-release.v1"
            or value.get("recovery_fixes_sha256") != fixes
            or value.get("preserves_existing_recovery_deadline") is not True
            or re.fullmatch(r"[0-9a-f]{40}", value.get("source_sha", "")) is None):
        fail("GATEWAY_RECOVERY_TTL_FIX_REQUIRED")
    pinned(value["binary"])
    return value


def unit_files(unit):
    props = driver.unit_properties(unit, ("FragmentPath", "DropInPaths"), "GATEWAY_CONFIG")
    # Some systemctl versions omit this empty array even with --all. Read
    # this known optional property alone; required properties stay strict.
    environment_files = subprocess.check_output(["systemctl", "--user", "show", unit,
        "--no-pager", "--all", "--value", "-p", "EnvironmentFiles"], text=True, timeout=15)
    if environment_files.endswith("\n"):
        environment_files = environment_files[:-1]
    if any(char in environment_files for char in "\n\r\0"):
        fail("INVALID_GATEWAY_ENVIRONMENT_FILES_READBACK")
    paths = [props["FragmentPath"], *props["DropInPaths"].split()]
    paths += re.findall(r"(/[^\s]+) \(ignore_errors=(?:yes|no)\)", environment_files)
    files = {}
    for name in paths:
        if (Path(name).name in (POLICY, RELEASE)
                or (unit in native_maintenance.UNITS and Path(name).name == native_maintenance.POLICY)
                or (unit == EXECUTION and Path(name).name == native_maintenance.RELEASE)):
            continue
        path = Path(name)
        if not path.is_absolute() or re.search(r"\s", name):
            fail("UNIT_CONFIG_PATH_INVALID")
        resolved = path.resolve(strict=True)
        with resolved.open("rb") as stream:
            before = os.fstat(stream.fileno())
            if (not stat.S_ISREG(before.st_mode) or before.st_uid not in (0, os.geteuid())
                    or before.st_mode & 0o022 or before.st_size > 262144):
                fail("UNIT_CONFIG_FILE_UNSAFE")
            raw = stream.read(262145)
            identity = lambda st: (st.st_dev, st.st_ino, st.st_mode, st.st_uid, st.st_gid,
                st.st_size, st.st_mtime_ns, st.st_ctime_ns)
            if identity(os.fstat(stream.fileno())) != identity(before) or path.resolve(strict=True) != resolved:
                fail("UNIT_CONFIG_FILE_CHANGED")
        files[name] = {"resolved": str(resolved), "sha256": digest(raw)}
    return files


def gateway(binary=None):
    status = driver.unit_properties(GATEWAY, (*driver.fence.SHOW_PROPS, "ExecStart", "TriggeredBy",
        "NRestarts", "SendSIGKILL", "TimeoutStopUSec"), "GATEWAY")
    if (status["ActiveState"] != "active" or status["SubState"] != "running"
            or not status["MainPID"].isdigit() or int(status["MainPID"]) <= 0
            or not status["InvocationID"] or status["TriggeredBy"]):
        fail("GATEWAY_NOT_RUNNING_WITHOUT_ACTIVATOR")
    pid = int(status["MainPID"])
    argv = driver.proc_bytes(pid, "cmdline").rstrip(b"\0").split(b"\0")
    if len(argv) != 1:
        fail("GATEWAY_ARGV_NOT_EXACT_BINARY")
    path = Path(argv[0].decode())
    process = driver.interactive_process(pid)
    if process["exe"] != str(path):
        fail("GATEWAY_PROCESS_EXE_MISMATCH")
    if binary is not None and str(path) != binary["path"]:
        fail("GATEWAY_BINARY_MISMATCH")
    if binary is None:
        binary = {"path": str(path), "sha256": driver.attribution.sha(path)}
    pinned(binary)
    if process["exe_sha256"] != binary["sha256"]:
        fail("GATEWAY_RUNNING_IMAGE_MISMATCH")
    if "argv[]=" + str(path) + " ;" not in status["ExecStart"]:
        fail("GATEWAY_EXECSTART_MISMATCH")
    final = driver.fence.show_unit(GATEWAY)
    if any(final[k] != status[k] for k in ("MainPID", "InvocationID", "ActiveState")):
        fail("GATEWAY_CHANGED_DURING_INSPECTION")
    return {"status": status, "pid_key": process["pid_key"], "binary": binary}


def same_gateway(expected, actual):
    for key in ("MainPID", "InvocationID", "NRestarts"):
        if actual["status"][key] != expected["status"][key]:
            fail("GATEWAY_PROCESS_CHANGED")
    if tuple(actual["pid_key"]) != tuple(expected["pid_key"]) or actual["binary"] != expected["binary"]:
        fail("GATEWAY_PROCESS_CHANGED")


def gateway_environment(pid):
    # The full policy stays in the 0600 checkpoint, never the public result.
    # Native control children still receive only driver.AUDIT_ENV below.
    environment = {}
    for raw in driver.proc_bytes(pid, "environ", 262144).split(b"\0"):
        if not raw:
            continue
        name, separator, value = raw.partition(b"=")
        try:
            name = name.decode()
            if not bound_environment_name(name):
                continue
            if not separator or name in environment:
                fail("GATEWAY_ENVIRONMENT_INVALID_OR_DUPLICATE")
            environment[name] = value.decode()
        except UnicodeDecodeError:
            fail("GATEWAY_ENVIRONMENT_ENCODING_INVALID")
    required = {"HEPTA_TOOL_SUPERVISOR_SOCKET", "HEPTA_TOOL_SOCKET", "HEPTA_TOOL_AGENT_ID", "HEPTA_TOOL_ACCOUNT",
        "HEPTA_TOOL_SUPERVISOR_LEASE_STORE", "HEPTA_IB_PAPER_USER_SCOPE", "HEPTA_EXECUTION_REMOTE_MODE"}
    if (not required <= environment.keys() or environment["HEPTA_IB_PAPER_USER_SCOPE"] != "1"
            or environment["HEPTA_EXECUTION_REMOTE_MODE"] != "PAPER"):
        fail("GATEWAY_NATIVE_PAPER_SCOPE_REQUIRED")
    return environment


def bound_environment_name(name):
    return (name.startswith("HEPTA_") or name.startswith("LD_") or name in driver.AUDIT_ENV
        or name == "CREDENTIALS_DIRECTORY")


def configured_environment(expected):
    """Bind what the NEXT service process will receive, before any stop."""
    props = driver.unit_properties(GATEWAY, ("Environment", "EnvironmentFiles", "UnsetEnvironment"), "GATEWAY_ENVIRONMENT")
    values = {}
    # User services inherit the user manager's environment. Its relevant
    # additions must not slip past a comparison of EnvironmentFile alone.
    manager = subprocess.check_output(["systemctl", "--user", "show-environment"], text=True, timeout=10)
    for raw in manager.splitlines():
        if not bound_environment_name(raw.partition("=")[0]):
            continue
        fields = shlex.split(raw)
        if len(fields) != 1 or "=" not in fields[0]:
            fail("UNSUPPORTED_GATEWAY_MANAGER_ENVIRONMENT_SYNTAX")
        name, value = fields[0].split("=", 1)
        values[name] = value
    for field in shlex.split(props["Environment"]):
        name, separator, value = field.partition("=")
        if not separator:
            fail("GATEWAY_CONFIGURED_ENVIRONMENT_INVALID")
        values[name] = value
    paths = re.findall(r"(/[^\s]+) \(ignore_errors=(?:yes|no)\)", props["EnvironmentFiles"])
    if not paths:
        fail("GATEWAY_ENVIRONMENT_FILE_REQUIRED")
    for path in paths:
        for raw in read_private(path, 262144).decode().split('\n'):
            line = raw.strip(' \t\r')
            if not line or line.startswith(("#", ";")):
                continue
            name, separator, value = line.partition("=")
            name, value = name.strip(' \t\r'), value.strip(' \t\r')
            if (not separator or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) is None
                    or "\\" in value or "\0" in value):
                fail("UNSUPPORTED_GATEWAY_ENVIRONMENT_FILE_SYNTAX")
            if value.startswith(("'", '"')):
                if len(value) < 2 or value[-1] != value[0] or any(char in value[1:-1] for char in "'\""):
                    fail("UNSUPPORTED_GATEWAY_ENVIRONMENT_FILE_SYNTAX")
                value = value[1:-1]
            elif any(char in value for char in "'\""):
                fail("UNSUPPORTED_GATEWAY_ENVIRONMENT_FILE_SYNTAX")
            # Unlike shell comments, # and ; inside an unquoted systemd
            # EnvironmentFile value are literal. Complex quoting/escapes
            # above are refused instead of being interpreted as shell code.
            values[name] = value
    for field in shlex.split(props["UnsetEnvironment"]):
        name, separator, value = field.partition("=")
        if not separator or values.get(name) == value:
            values.pop(name, None)
    actual = {name: value for name, value in values.items() if bound_environment_name(name)}
    if actual != expected:
        fail("RUNNING_GATEWAY_DIFFERS_FROM_NEXT_CONFIGURED_ENVIRONMENT")
    return actual


def catalog(plan, environment):
    binary = pinned(plan["catalog_probe"])
    key = environment.get("HEPTA_TOOL_SUPERVISOR_LEASE_KEY_FILE")
    if not key:
        key = str(Path(environment["CREDENTIALS_DIRECTORY"]) / "hepta-supervisor-lease-key")
    argv = [str(binary), "--store", environment["HEPTA_TOOL_SUPERVISOR_LEASE_STORE"], "--key", key,
        "--cleanup-lock", f"/run/user/{os.geteuid()}/heptatrader/interlock/session-cleanup.lock"]
    result = subprocess.run(argv, capture_output=True, timeout=10)
    if result.returncode or len(result.stdout) > 1048576:
        fail("AUTHENTICATED_CATALOG_READ_FAILED")
    pinned(plan["catalog_probe"])
    value = strict(result.stdout)
    if value.get("schema") != "heptatrader.gateway-catalog.v1" or not isinstance(value.get("records"), list):
        fail("CATALOG_PROJECTION_INVALID")
    records = value["records"]
    tokens = [r.get("token_sha256") for r in records]
    if len(tokens) != len(set(tokens)) or any(re.fullmatch(r"[0-9a-f]{64}", t or "") is None for t in tokens):
        fail("CATALOG_TOKEN_SET_INVALID")
    return records


def controller(instrument, home):
    lane, token_name = LANES[instrument]
    state_path = home / ".local/state/heptatrader" / lane / "state.json"
    raw, correlation = driver.read_flat_state(state_path, instrument)
    state = strict(raw)
    if (state.get("lease_uncertain") is not False or state.get("lease_renewal_state") != "IDLE"
            or state.get("owner_fence_recovery_required") not in (None, False)
            or state.get("owner_recovery_phase") not in (None, "VERIFIED")):
        fail("CONTROLLER_CONTROL_STATE_UNRESOLVED")
    if type(state.get("lease_generation")) is not int or not 0 < state["lease_generation"] < 2**64:
        fail("CONTROLLER_GENERATION_INVALID")
    command = (state.get("last_reconciliation") or {}).get("command_id")
    if not isinstance(command, str) or not 0 < len(command) <= 128 or "\n" in command:
        fail("ORIGINAL_CONTROLLER_COMMAND_REQUIRED")
    token_path = home / ".config/heptatrader" / token_name
    token = read_private(token_path, 4096).decode().strip()
    return {"instrument": instrument, "unit": driver.attribution.unit_for(instrument),
        "state_path": str(state_path), "lock_path": str(state_path.parent / "driver.lock"),
        "token_path": str(token_path), "token_sha256": digest(token), "command": command,
        "state_generation": state.get("lease_generation"), "correlation": correlation,
        "state_sha256": digest(raw)}


def bind_owners(records, owners, environment, *, deadline=None):
    for row in records:
        if (type(row.get("paper")) is not bool or type(row.get("watch")) is not bool
                or row["paper"] == row["watch"]):
            fail("UNKNOWN_CATALOG_TEMPLATE")
        if row["watch"] and (row.get("bootstrap_issuer") is not True
                or row.get("peer_uid") != os.geteuid() or row.get("recovery_only") is not False
                or row.get("fence_pending") is not False or row.get("fence_complete") is not False
                or row.get("finalization_required") is not False or row.get("finalization_state") != 0
                or row.get("predecessor_sha256") or row.get("recovery_command_sha256")):
            fail("WATCH_CATALOG_POLICY_UNRESOLVED")
        if row["watch"] and (type(row.get("expires_at_ms")) is not int or row["expires_at_ms"] <= 0):
            fail("WATCH_CATALOG_EXPIRY_INVALID")
    paper = [row for row in records if row.get("paper") is True]
    known = {owner["token_sha256"]: owner for owner in owners}
    if len(known) != len(owners):
        fail("DUPLICATE_CONTROLLER_TOKEN")
    if not paper or {r["token_sha256"] for r in paper} != set(known):
        fail("UNBOUND_WRITING_OWNER")
    sessions = set()
    for row in paper:
        owner = known[row["token_sha256"]]
        original = owner.get("record")
        if original and any(row.get(key) != original[key] for key in
                ("token_sha256", "agent_sha256", "session_sha256", "account_sha256", "domain_sha256")):
            fail("ORIGINAL_OWNER_SCOPE_CHANGED")
        if (row.get("bootstrap_issuer") is not True or row.get("peer_uid") != os.geteuid()
                or row.get("agent_sha256") != digest(environment["HEPTA_TOOL_AGENT_ID"])
                or row.get("account_sha256") != digest(environment["HEPTA_TOOL_ACCOUNT"])
                or row.get("domain_sha256") != digest(environment.get("HEPTA_EXECUTION_DOMAIN_ID", "PAPER"))
                or row.get("predecessor_sha256") or row.get("fence_pending") is not False
                or row.get("fence_complete") is not False or row.get("finalization_required") is not False
                or row.get("finalization_state") != 0 or row.get("session_sha256") in sessions
                or type(row.get("generation")) is not int or row["generation"] <= 0
                or type(row.get("expires_at_ms")) is not int
                or row["expires_at_ms"] < now_ms() + MIN_TTL_MS):
            fail("OWNER_SCOPE_OR_TTL_NOT_SAFE")
        sessions.add(row["session_sha256"])
        if deadline is not None and now_ms() + MIN_TTL_MS >= deadline:
            fail("MAINTENANCE_TTL_BUDGET_EXHAUSTED")
        if row["generation"] != owner["state_generation"]:
            fail("CONTROLLER_CATALOG_GENERATION_MISMATCH")
        owner["record"] = row
    return min(row["expires_at_ms"] for row in paper)


def native_hold_runtime(plan, statuses):
    item = plan["execution_receipt"]
    raw = read_private(item["path"], 1048576)
    if digest(raw) != item["sha256"]:
        fail("EXECUTION_RELEASE_RECEIPT_CHANGED")
    value = strict(raw)
    binary = {"path": value["binary"], "sha256": value["binary_sha256"]}
    pinned(binary)
    if value.get("source_sha") != plan["reviewed_execution_hold_source_sha"]:
        fail("EXECUTION_NATIVE_HOLD_SOURCE_NOT_REVIEWED")
    pid = int(statuses[EXECUTION]["MainPID"])
    actual = driver.interactive_process(pid)
    source = plan["reviewed_execution_hold_source_sha"]
    if actual["exe"] != binary["path"] or actual["exe_sha256"] != binary["sha256"]:
        replacement = plan.get("native_execution") or {}
        candidate = replacement.get("binary") or {}
        if actual["exe"] != candidate.get("path") or actual["exe_sha256"] != candidate.get("sha256"):
            fail("EXECUTION_NATIVE_HOLD_BINARY_MISMATCH")
        pinned(candidate)
        source = replacement["source_sha"]
    profile = {}
    for raw in driver.proc_bytes(pid, "environ", 262144).split(b"\0"):
        key, _, value = raw.partition(b"=")
        if key in (b"HEPTA_IB_PAPER_USER_SCOPE", b"HEPTA_IB_PAPER_CONTROL_DIRECTORY", b"HEPTA_IB_EXECUTION_MODE"):
            profile[key.decode()] = value.decode()
    expected = f"/run/user/{os.geteuid()}/heptatrader/control"
    if profile != {"HEPTA_IB_PAPER_USER_SCOPE": "1", "HEPTA_IB_PAPER_CONTROL_DIRECTORY": expected,
            "HEPTA_IB_EXECUTION_MODE": "PAPER"}:
        fail("EXECUTION_NATIVE_HOLD_PROFILE_MISMATCH")
    return {"process": actual, "source_sha": source, "profile": profile}


def controller_stop_edges(unit):
    edges = driver.unit_properties(unit, (*driver.STOP_EDGES, "Conflicts", "ConflictedBy"), "CONTROLLER_STOP_EDGES")
    if any(edges[name] for name in driver.STOP_EDGES):
        fail("CONTROLLER_STOP_PROPAGATION")
    conflicts = set((edges["Conflicts"] + " " + edges["ConflictedBy"]).split())
    if conflicts - {"shutdown.target"}:
        fail("CONTROLLER_START_CONFLICT")
    shutdown = driver.unit_properties("shutdown.target", ("LoadState", "ActiveState"), "CONTROLLER_SHUTDOWN") if conflicts else None
    if shutdown is not None and shutdown != {"LoadState": "loaded", "ActiveState": "inactive"}:
        fail("CONTROLLER_SHUTDOWN_ACTIVE")
    return {"edges": edges, "shutdown": shutdown}


def inspect(plan, home):
    if plan.get("schema") != "heptatrader.gateway-maintenance-plan.v1":
        fail("GATEWAY_PLAN_SCHEMA_INVALID")
    fixes = plan.get("recovery_fixes_sha256", "")
    if re.fullmatch(r"[0-9a-f]{64}", fixes) is None:
        fail("REVIEWED_RECOVERY_FIX_PIN_REQUIRED")
    packages = {name: release(plan[name], fixes) for name in ("candidate", "fallback")}
    for name in ("sessionctl", "reader", "catalog_probe"):
        pinned(plan[name])
    running = gateway()
    for unit in (GATEWAY, *driver.DRIVER_UNITS):
        if (home / ".config/systemd/user" / (unit + ".d") / POLICY).exists():
            fail("EXISTING_MAINTENANCE_POLICY_REQUIRES_RESUME")
    environment = gateway_environment(int(running["status"]["MainPID"]))
    configured_environment(environment)
    statuses = {name: driver.fence.show_unit(name) for name in driver.fence.RISK_UNITS}
    owners = []
    frozen = {}
    for instrument in LANES:
        unit = driver.attribution.unit_for(instrument)
        if statuses[unit]["ActiveState"] == "active":
            owner = controller(instrument, home)
            owner["resident"] = driver.running_driver(unit, instrument)
            owners.append(owner)
        elif statuses[unit]["ActiveState"] == "inactive":
            value = driver.frozen_driver_snapshot(unit)
            if value["UnitFileState"] != "disabled" or value["RefuseManualStop"] != "yes" or value["MainPID"] != "0":
                fail("INACTIVE_CONTROLLER_NOT_FROZEN")
            frozen[unit] = value
        else:
            fail("CONTROLLER_TRANSITION_UNRESOLVED")
    records = catalog(plan, environment)
    # An inactive lane with a retained PAPER record still belongs to the
    # complete owner inventory, but must never be automatically started.
    tokens = {row["token_sha256"] for row in records if row.get("paper") is True}
    for instrument in LANES:
        unit = driver.attribution.unit_for(instrument)
        if unit in frozen:
            token_path = home / ".config/heptatrader" / LANES[instrument][1]
            if token_path.exists() and digest(read_private(token_path, 4096).decode().strip()) in tokens:
                owner = controller(instrument, home); owner["resident"] = None; owners.append(owner)
    deadline = bind_owners(records, owners, environment)
    interactive = driver.interactive_broker_snapshot(plan["interactive_broker_pid"], GATEWAY, statuses)
    protected = {name: value for name, value in statuses.items() if name not in frozen and name != driver.BROKER_UNIT}
    driver.fence.check_state(protected, statuses, required=True)
    hold_runtime = native_hold_runtime(plan, statuses)
    release_path = home / ".config/systemd/user" / (GATEWAY + ".d") / RELEASE
    old_release = read_private(release_path, 65536).decode() if release_path.exists() else None
    if old_release is not None and old_release != "[Service]\nExecStart=\nExecStart=" + running["binary"]["path"] + "\n":
        fail("EXISTING_GATEWAY_RELEASE_OVERRIDE_CHANGED")
    snapshot = {"packages": packages, "gateway": running, "environment": environment, "statuses": statuses,
        "owners": owners, "frozen": frozen, "records": records, "deadline": deadline,
        "unit_files": unit_files(GATEWAY), "interactive": interactive, "hold_runtime": hold_runtime,
        "original_release_bytes": old_release,
        "controller_files": {owner["unit"]: unit_files(owner["unit"]) for owner in owners},
        "controller_topology": {owner["unit"]: controller_stop_edges(owner["unit"])
            for owner in owners if owner["resident"] is not None},
        "service_policy": {unit: driver.unit_properties(unit,
            ("RefuseManualStop", "SendSIGKILL", "TimeoutStopUSec", "Restart"), "ORIGINAL_SERVICE_POLICY")
            for unit in {GATEWAY, *(owner["unit"] for owner in owners)}}}
    snapshot["native_execution"] = native_maintenance.prepare(sys.modules[__name__], plan, home, snapshot)
    if "audit_seal" in plan:
        snapshot["audit_seal"] = audit_seal_configuration(plan, environment, packages)
    return snapshot


def rpc(plan, environment, owner, operation, *arguments):
    binary = pinned(plan["sessionctl"])
    if operation == "owner-audit":
        argv = [str(binary), operation, "--token-file", owner["token_path"]]
    elif operation in ("recovery-query", "paper-resume"):
        argv = [str(binary), "--socket", environment["HEPTA_TOOL_SUPERVISOR_SOCKET"],
            "--io-timeout-ms", "4000", operation, "--token-file", owner["token_path"],
            "--generation", str(owner["record"]["generation"]), "--token-owner-uid", str(os.geteuid()), *arguments]
    else:
        fail("UNSUPPORTED_MAINTENANCE_OPERATION")
    audit_environment = {name: value for name, value in environment.items() if name in driver.AUDIT_ENV}
    result = subprocess.run(argv, env=audit_environment, capture_output=True, timeout=12)
    pinned(plan["sessionctl"])
    if result.returncode or len(result.stdout) > 1048576:
        fail("NATIVE_CONTROL_NOT_CONFIRMED:" + operation)
    return strict(result.stdout)


def fresh_audit(plan, environment, owner):
    result = rpc(plan, environment, owner, "owner-audit")
    completed_at_ms = now_ms()
    driver.check_owner_zero(result)
    for field, key in (("agent_id", "agent_sha256"), ("session_id", "session_sha256"),
            ("owner_account", "account_sha256"), ("owner_execution_domain", "domain_sha256")):
        if digest(result[field]) != owner["record"][key]:
            fail("OWNER_AUDIT_CATALOG_SCOPE_MISMATCH")
    health = read_tool(plan, environment, owner, "system.get_health")
    native = health.get("execution_authoritative_health") or {}
    if (native.get("source") != "IB" or native.get("authoritative") is not True
            or native.get("native_new_risk_guard_blocked") is not True or native.get("mutation_ready") is not False
            or native.get("owner_fenced") is not False
            or native.get("owner_recovery_only") is not owner["record"]["recovery_only"]
            or health.get("execution_service_epoch") != result["service_epoch"]
            or health.get("execution_service_fencing_generation") != result["service_fencing_generation"]):
        fail("NATIVE_RISK_INCREASE_HOLD_NOT_OBSERVED")
    return {"reply": result, "at_ms": completed_at_ms,
        "native_health": {name: native[name] for name in
            ("source", "authoritative", "owner_fenced", "owner_recovery_only", "native_new_risk_guard_blocked", "mutation_ready")}}


def read_tool(plan, environment, owner, tool, *fields):
    binary = pinned(plan["reader"])
    try:
        result = subprocess.run([str(binary), "--socket", environment["HEPTA_TOOL_SOCKET"],
            "--token-file", owner["token_path"], "--io-timeout-ms", "4000", "--call-id", "gateway-status-" + uuid.uuid4().hex,
            "call", tool, *fields],
            capture_output=True, timeout=12)
    except subprocess.TimeoutExpired as error:
        read_tool_failure(tool, "TIMEOUT", stdout=error.output, stderr=error.stderr, timeout=error.timeout)
    except OSError as error:
        read_tool_failure(tool, "OS_ERROR", os_errno=error.errno, error_type=type(error))
    pinned(plan["reader"])
    try:
        value = strict(result.stdout) if result.stdout else {}
    except (ValueError, UnicodeError, RecursionError):
        read_tool_failure(tool, "INVALID_JSON", result)
    if not isinstance(value, dict):
        read_tool_failure(tool, "INVALID_ENVELOPE", result)
    payload = value.get("payload") or {}
    if result.returncode or value.get("status") != "ok" or value.get("tool") != tool or not isinstance(payload, dict):
        read_tool_failure(tool, "REPLY_NOT_CONFIRMED", result, envelope=value)
    return payload


def read_tool_failure(tool, kind, result=None, *, stdout=None, stderr=None, os_errno=None,
        error_type=RuntimeError, timeout=None, envelope=None):
    """Retain bounded diagnostics in the existing error receipt, never a permit.

    No command, token path, raw output or arbitrary exception text is retained.
    This function does not retry a read or any control RPC. Existing typed
    startup waits remain with their callers; audit freshness is unchanged.
    """
    rc = result.returncode if result is not None else None
    if result is not None:
        stdout, stderr = result.stdout, result.stderr
    details = {"kind": kind, "returncode": rc if type(rc) is int and -255 <= rc <= 255 else None,
        "stdout_bytes": len(stdout or b""), "stderr_bytes": len(stderr or b"")}
    if type(os_errno) is int and 0 <= os_errno <= 255:
        details["os_errno"] = os_errno
    # Reuse the maintenance latch's strict, <=4 KiB native error projection.
    # Additional stderr codes are literal reasons from the pinned native CLI.
    codes = driver.latch.NATIVE_READ_STDERR_CODES | {
        "SOCKET_CONNECT_FAILED", "FRAME_WRITE_TIMEOUT", "FRAME_HEADER_TIMEOUT", "FRAME_BODY_TIMEOUT",
        "INVALID_RESULT_ENVELOPE", "UNKNOWN_RESULT_STATUS", "RESULT_TOOL_MISMATCH"}
    if type(stderr) is bytes and len(stderr) <= 128:
        code = stderr.decode("ascii", errors="replace").strip()
        if code in codes:
            details["client_reason"] = code
    if rc == 9:
        code = driver.latch._native_error_reason(stdout, tool)
        # Canonical syntax alone is not a secret filter. Retain only literal
        # read-registry / remote-Execution reasons proven by native source.
        if code in {"READ_TOOL_FAILED", "TOOL_HANDLER_UNAVAILABLE", "REMOTE_EXECUTION_DISABLED",
                "EXECUTION_SERVICE_EPOCH_MISMATCH", "EXECUTION_SERVICE_EPOCH_CHANGED",
                "EXECUTION_GATEWAY_DAEMON_IDENTITY_UNAVAILABLE", "EXECUTION_GATEWAY_DAEMON_IDENTITY_MISMATCH",
                "EXECUTION_GATEWAY_CONTEXT_MISMATCH"}:
            details["reason_code"] = code
    if envelope is not None:
        status = envelope.get("status")
        details["status"] = status if status in (
            "ok", "error", "permission_denied", "invalid_tool", "rejected", "duplicate", "uncertain") else "UNKNOWN"
        details["tool_matches"] = envelope.get("tool") == tool
        details["payload_object"] = isinstance(envelope.get("payload"), dict)
    message = "NATIVE_READ_NOT_CONFIRMED:" + tool + ":" + json.dumps(details, sort_keys=True, separators=(",", ":"))
    # Preserve OS exception types/errno: existing native maintenance explicitly
    # treats FileNotFoundError and PermissionError as bounded startup waits.
    # Suppress contexts that could retain argv, paths, or arbitrary output.
    if issubclass(error_type, OSError):
        raise error_type(os_errno, message) from None
    if kind == "TIMEOUT":
        # Keep the original timeout type/budget without exposing its argv or
        # retaining partial output. Existing callers still see a timeout.
        raise subprocess.TimeoutExpired(message, timeout) from None
    raise RuntimeError(message) from None


def original_command(plan, environment, owner, audit):
    payload = read_tool(plan, environment, owner, "execution.get_command_status", "command_id=" + owner["command"])
    if (payload.get("authoritative") is not True or payload.get("command_id") != owner["command"]
            or payload.get("command_status") not in ("accepted", "rejected")
            or payload.get("execution_service_epoch") != audit["reply"]["service_epoch"]
            or payload.get("execution_service_fencing_generation") != audit["reply"]["service_fencing_generation"]):
        fail("ORIGINAL_COMMAND_NOT_AUTHORITATIVELY_RECONCILED")


def sync_confirmed_generation(owner, record, confirmed_at_ms):
    """Idempotent local bookkeeping after a confirmed native Resume, no rearm."""
    fd = driver.open_driver_lock(owner["lock_path"])
    try:
        raw, correlation = driver.read_flat_state(owner["state_path"], owner["instrument"])
        value = strict(raw)
        if correlation != owner["correlation"]:
            fail("CONTROLLER_BUSINESS_CORRELATION_CHANGED")
        if (value.get("lease_uncertain") is not False or value.get("lease_renewal_state") != "IDLE"
                or value.get("owner_fence_recovery_required") not in (None, False)
                or value.get("owner_recovery_phase") not in (None, "VERIFIED")):
            fail("CONTROLLER_CONTROL_STATE_UNRESOLVED")
        old = owner["state_generation"]
        if (record["generation"] != old + 1 or record["recovery_only"] is not False
                or any(record[key] != owner["record"][key] for key in
                    ("token_sha256", "agent_sha256", "session_sha256", "account_sha256", "domain_sha256"))):
            fail("CONFIRMED_RESUME_IDENTITY_MISMATCH")
        if value.get("lease_generation") not in (old, old + 1):
            fail("CONTROLLER_GENERATION_DRIFT")
        if value["lease_generation"] == old + 1:
            return
        value["lease_generation"] = old + 1
        value["lease_last_renew_ms"] = confirmed_at_ms
        atomic_json(owner["state_path"], value)
    finally:
        os.close(fd)


def equivalent(left, right):
    return json.dumps(left, sort_keys=True) == json.dumps(right, sort_keys=True)


def interactive_process_identity(proof):
    """Only the connected TCP pair is ephemeral; the listener is identity."""
    value = json.loads(json.dumps(proof))
    try:
        for name, endpoint in (("accepted", "remote"), ("execution", "local")):
            row = value["sockets"][name]
            for field in ("inode", "fds"):
                if field not in row:
                    return None
                row[field] = None
            address = row[endpoint]
            if not isinstance(address, list) or len(address) != 2:
                return None
            row[endpoint] = [address[0], None]  # Only the ephemeral port changes.
    except (KeyError, TypeError):
        return None
    return value


def prepared_reconnect_pristine(state, resume):
    allowed = {"schema", "plan_sha256", "status", "phase", "snapshot", "markers",
        "stopped_controllers", "error", "rollback", "prepared_reconnects",
        "prepared_lease_successors", "effective_snapshot"}
    return (resume and state.get("phase") == "PREPARED" and not set(state) - allowed
        and state.get("stopped_controllers") == []
        and state.get("rollback") in (None, "NO_PROCESS_FORCED_OR_REPLACED")
        and state["snapshot"].get("native_execution") is None)


def prepared_reconnect_effects(state, resume):
    if not prepared_reconnect_pristine(state, resume):
        fail("PREPARED_RECONNECT_CONTROL_EFFECTS_NOT_EMPTY")


def lease_successor_snapshot(snapshot, rows):
    """Current authenticated successor, not a claim about the historical RPC."""
    if equivalent(rows, snapshot["records"]):
        return snapshot, []
    old_rows = {row["token_sha256"]: row for row in snapshot["records"]}
    new_rows = {row["token_sha256"]: row for row in rows}
    if len(new_rows) != len(rows) or list(old_rows) != list(new_rows):
        fail("PREPARED_LEASE_SUCCESSOR_CATALOG_CHANGED")
    candidate = json.loads(json.dumps(snapshot))
    owners = {owner["token_sha256"]: owner for owner in candidate["owners"]}
    changes = []
    for token, old in old_rows.items():
        row = new_rows[token]
        if equivalent(row, old):
            continue
        if (token not in owners or owners[token]["resident"] is None
                or old.get("paper") is not True or old.get("watch") is not False
                or old.get("recovery_only") is not False or set(row) != set(old)
                or any(not equivalent(row[key], old[key]) for key in old if key not in ("generation", "expires_at_ms"))
                or type(row["generation"]) is not int or not 0 < row["generation"] < 2**64
                or row["generation"] != old["generation"] + 1
                or type(row["expires_at_ms"]) is not int or not 0 < row["expires_at_ms"] < 2**64
                or row["expires_at_ms"] <= old["expires_at_ms"]):
            fail("PREPARED_LEASE_SUCCESSOR_CATALOG_CHANGED")
        owner = owners[token]
        if not equivalent(owner["record"], old) or owner["state_generation"] != old["generation"]:
            fail("PREPARED_LEASE_SUCCESSOR_ORIGINAL_BINDING_CHANGED")
        owner["record"] = json.loads(json.dumps(row))
        owner["state_generation"] = row["generation"]
        changes.append({"token_sha256": token, "old_generation": old["generation"],
            "new_generation": row["generation"], "old_expires_at_ms": old["expires_at_ms"],
            "new_expires_at_ms": row["expires_at_ms"]})
    if not changes:
        fail("PREPARED_LEASE_SUCCESSOR_CATALOG_CHANGED")
    candidate["records"] = json.loads(json.dumps(rows))
    # The transaction deadline is deliberately not the new lease expiry.
    return candidate, changes


def effective_snapshot(state):
    original = state["snapshot"]
    if "effective_snapshot" not in state and "prepared_lease_successors" not in state:
        return original
    records = state.get("prepared_lease_successors")
    actual = state.get("effective_snapshot")
    if not isinstance(records, list) or not records or not isinstance(actual, dict):
        fail("PREPARED_LEASE_SUCCESSOR_RECEIPT_INVALID")
    previous = original
    witnesses = {digest(json.dumps(record, sort_keys=True)): record for record in state.get("prepared_reconnects", [])}
    for record in records:
        if (not isinstance(record, dict) or record.get("schema") != "heptatrader.gateway-lease-successor.v1"
                or record.get("plan_sha256") != state["plan_sha256"]
                or not equivalent(record.get("markers"), state["markers"])
                or record.get("deadline_ms") != original["deadline"]
                or not equivalent(record.get("before_catalog"), previous["records"])
                or record.get("reconnect_receipt_sha256") not in witnesses):
            fail("PREPARED_LEASE_SUCCESSOR_RECEIPT_INVALID")
        witness = witnesses[record["reconnect_receipt_sha256"]]
        if (witness.get("plan_sha256") != state["plan_sha256"]
                or not equivalent(witness.get("markers"), state["markers"])
                or witness.get("deadline_ms") != original["deadline"]
                or not equivalent(witness.get("original_snapshot"), previous)):
            fail("PREPARED_LEASE_SUCCESSOR_RECEIPT_INVALID")
        previous, changes = lease_successor_snapshot(previous, record["after_catalog"])
        if not changes or not equivalent(changes, record.get("successors")):
            fail("PREPARED_LEASE_SUCCESSOR_RECEIPT_INVALID")
        audits = {audit["token_sha256"]: audit for audit in witness.get("audits", [])}
        if (len(audits) != len(previous["owners"]) or any(
                owner["token_sha256"] not in audits
                or audits[owner["token_sha256"]].get("generation") != owner["state_generation"]
                or audits[owner["token_sha256"]].get("original_command_sha256") != digest(owner["command"])
                for owner in previous["owners"])):
            fail("PREPARED_LEASE_SUCCESSOR_RECEIPT_INVALID")
    if prepared_reconnect_pristine(state, True):
        if not equivalent(actual, previous):
            fail("PREPARED_LEASE_SUCCESSOR_EFFECTIVE_SNAPSHOT_CHANGED")
    else:
        # Later phases mutate only the effective catalog, owner recovery
        # records, TCP witness and (downwards) the recovery deadline.
        fixed = set(previous) - {"owners", "records", "interactive", "deadline"}
        if (set(actual) != set(previous) or any(not equivalent(actual[key], previous[key]) for key in fixed)
                or type(actual["deadline"]) is not int or actual["deadline"] > original["deadline"]
                or len(actual["owners"]) != len(previous["owners"])):
            fail("PREPARED_LEASE_SUCCESSOR_EFFECTIVE_SNAPSHOT_CHANGED")
        for old, owner in zip(previous["owners"], actual["owners"]):
            if set(owner) != set(old) or any(not equivalent(owner[key], old[key]) for key in old if key != "record"):
                fail("PREPARED_LEASE_SUCCESSOR_EFFECTIVE_OWNER_CHANGED")
    return actual


def prepared_processes_unchanged(home, snapshot):
    for unit in (GATEWAY, *driver.DRIVER_UNITS):
        if os.path.lexists(home / ".config/systemd/user" / (unit + ".d") / POLICY):
            fail("PREPARED_RECONNECT_EXISTING_STOP_POLICY")
    for unit, expected in snapshot["service_policy"].items():
        if driver.unit_properties(unit, tuple(expected), "PREPARED_ORIGINAL_POLICY") != expected:
            fail("PREPARED_RECONNECT_SERVICE_POLICY_CHANGED")
    for owner in snapshot["owners"]:
        observed = controller(owner["instrument"], home)
        for key in ("instrument", "unit", "state_path", "lock_path", "token_path", "token_sha256",
                    "command", "state_generation", "correlation"):
            if not equivalent(observed[key], owner[key]):
                fail("PREPARED_RECONNECT_CONTROLLER_STATE_CHANGED:" + key)
        if owner["resident"] is not None:
            original = dict(owner["resident"], pid_key=tuple(owner["resident"]["pid_key"]))
            driver.same_driver(original, driver.running_driver(owner["unit"], owner["instrument"]))
    observed = gateway(snapshot["gateway"]["binary"])
    same_gateway(snapshot["gateway"], observed)
    return observed


def held_native_authority(plan, snapshot, owner):
    health = read_tool(plan, snapshot["environment"], owner, "system.get_health")
    native = health.get("execution_authoritative_health") or {}
    epoch = health.get("execution_service_epoch")
    generation = health.get("execution_service_fencing_generation")
    if (native.get("source") != "IB" or native.get("authoritative") is not True
            or native.get("native_new_risk_guard_blocked") is not True or native.get("mutation_ready") is not False
            or native.get("owner_fenced") is not False
            or native.get("owner_recovery_only") is not owner["record"]["recovery_only"]
            or type(epoch) is not str or not 0 < len(epoch) <= 128
            or type(generation) is not int or not 0 < generation < 2**64):
        fail("PREPARED_RECONNECT_NATIVE_AUTHORITY_INVALID")
    return epoch, generation


def effective_interactive(snapshot, state):
    if "interactive_refresh" in state or "prepared_reconnects" not in state:
        return snapshot["interactive"]
    previous = snapshot["interactive"]
    records = state["prepared_reconnects"]
    if not isinstance(records, list) or not records:
        fail("PREPARED_RECONNECT_RECEIPT_INVALID")
    for record in records:
        if (not isinstance(record, dict) or record.get("plan_sha256") != state["plan_sha256"]
                or not equivalent(record.get("markers"), state["markers"])
                or not equivalent(record.get("previous_witness"), previous)
                or not isinstance(record.get("witness"), dict)
                or interactive_process_identity(previous) is None
                or not equivalent(interactive_process_identity(previous),
                                  interactive_process_identity(record["witness"]))):
            fail("PREPARED_RECONNECT_RECEIPT_INVALID")
        previous = record["witness"]
    return previous


def put_owned(path, raw, expected=None):
    path = Path(path)
    directory = driver.latch._directory(path.parent, 0o700)
    try:
        if expected is None:
            if not driver.latch._write_once(directory, path.name, raw, 0o600):
                fail("MAINTENANCE_DROPIN_ALREADY_EXISTS")
        else:
            driver.replace_exact(path, expected, raw)
    finally:
        os.close(directory)


def remove_owned(path, expected):
    path = Path(path)
    directory = driver.latch._directory(path.parent, 0o700)
    try:
        if driver.latch._file(directory, path.name, 0o600) != expected:
            fail("MAINTENANCE_DROPIN_CHANGED")
        driver.latch._unlink(directory, path.name, 0o600)
    finally:
        os.close(directory)


def archive_completed(path, raw):
    """Keep one completed receipt, without making the next job need rm."""
    path = Path(path)
    archive = path.with_name("gateway-upgrade-" + digest(raw) + ".json")
    if read_private(path, 1048576) != raw:
        fail("COMPLETED_CHECKPOINT_CHANGED")
    if archive.exists():
        if read_private(archive, 1048576) != raw:
            fail("COMPLETED_CHECKPOINT_ARCHIVE_CHANGED")
        path.unlink()
    else:
        os.rename(path, archive)
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def observe_stopped(unit, original, timeout=45):
    deadline = time.monotonic() + timeout
    while True:
        value = driver.fence.show_unit(unit)
        if value["ActiveState"] == "inactive" and value["MainPID"] in ("", "0"):
            if driver.pid_key(original["status"]["MainPID"]) == tuple(original["pid_key"]):
                fail("STOPPED_PROCESS_STILL_PRESENT")
            return value
        if value["MainPID"] not in ("", "0", original["status"]["MainPID"]):
            fail("STOPPING_SERVICE_REPLACED")
        if time.monotonic() >= deadline:
            fail("NORMAL_STOP_STILL_PENDING")
        time.sleep(0.2)


def start_gateway(binary):
    driver.ctl("start", GATEWAY)
    deadline = time.monotonic() + 35
    first = None
    stable_at = None
    while time.monotonic() < deadline:
        try:
            actual = gateway(binary)
            if first is None:
                first = actual; stable_at = time.monotonic()
            else:
                same_gateway(first, actual)
            if time.monotonic() - stable_at >= 3:
                return actual
        except (FileNotFoundError, PermissionError, RuntimeError) as error:
            # /proc may briefly be unavailable while exec establishes the
            # process. Retry only BEFORE any successful image observation.
            transient_read = isinstance(error, (FileNotFoundError, PermissionError))
            if first is not None or (not transient_read and str(error) != "GATEWAY_NOT_RUNNING_WITHOUT_ACTIVATOR"):
                raise
        time.sleep(0.2)
    fail("GATEWAY_STARTUP_NOT_STABLE")


def observe_controller_start(owner, first=None):
    """Wait for the first strict image of this transaction's original driver."""
    deadline = time.monotonic() + 30
    while True:
        try:
            actual = driver.running_driver(owner["unit"], owner["instrument"])
        except (RuntimeError, FileNotFoundError) as error:
            transient = isinstance(error, FileNotFoundError) or str(error) in {
                "DRIVER_NOT_ACTIVE", "RUNNING_DRIVER_ARGV_MISMATCH", "DRIVER_CHANGED_DURING_INSPECTION"}
            if first is not None or not transient:
                raise
            status = driver.fence.show_unit(owner["unit"])
            if status["ActiveState"] == "failed" and status["MainPID"] in ("", "0"):
                fail("CONTROLLER_START_FAILED")
            if time.monotonic() >= deadline:
                fail("CONTROLLER_STARTUP_NOT_OBSERVED")
            time.sleep(0.2)
            continue
        if (actual["manifest"] != owner["resident"]["manifest"]
                or actual["manifest_sha256"] != owner["resident"]["manifest_sha256"]):
            fail("ORIGINAL_CONTROLLER_RELEASE_CHANGED")
        if first is not None:
            driver.same_driver(first, actual)
        return actual


def resume_owner(plan, snapshot, owner, state, save, context):
    """Lost Resume replies are reconciled from the authenticated catalog.

    Once a Resume intent is durable this function never resends it. Generation
    +1, original scope/command and a fresh complete audit must be observed;
    otherwise the native hold remains for explicit recovery.
    """
    token = owner["token_sha256"]
    progress = state.setdefault("owner_progress", {}).setdefault(token, {})
    context()
    rows = catalog(plan, snapshot["environment"])
    row = next((r for r in rows if r["token_sha256"] == token), None)
    if row is None:
        fail("ORIGINAL_OWNER_DISAPPEARED")
    old = owner["record"]
    if any(row[key] != old[key] for key in ("agent_sha256", "session_sha256", "account_sha256", "domain_sha256")):
        fail("ORIGINAL_OWNER_SCOPE_CHANGED")
    if not progress.get("resume_sent"):
        if row != old or row["recovery_only"] is not True:
            fail("OWNER_CHANGED_BEFORE_RESUME")
        audit = fresh_audit(plan, snapshot["environment"], owner)
        original_command(plan, snapshot["environment"], owner, audit)
        context()
        ttl = (snapshot["deadline"] - now_ms()) // 1000 - 1
        if ttl < MIN_TTL_MS // 1000:
            fail("MAINTENANCE_TTL_BUDGET_EXHAUSTED")
        progress.update(resume_sent=True, resume_sent_at_ms=now_ms())
        state["phase"] = "RESUME_SENT"; save()
        reply = rpc(plan, snapshot["environment"], owner, "paper-resume", "--ttl-sec", str(ttl))
        if (reply.get("accepted") is not True or reply.get("reason_code") != "SESSION_OWNER_RESUMED"
                or reply.get("lease_generation") != old["generation"] + 1):
            fail("ORIGINAL_OWNER_RESUME_NOT_CONFIRMED")
        context()
        rows = catalog(plan, snapshot["environment"])
        row = next((r for r in rows if r["token_sha256"] == token), None)
    if (row is None or row.get("generation") != old["generation"] + 1
            or row.get("recovery_only") is not False or row.get("recovery_command_sha256")
            or row.get("expires_at_ms", 0) > snapshot["deadline"]
            or any(row.get(key) != old[key] for key in ("token_sha256", "agent_sha256", "session_sha256", "account_sha256", "domain_sha256"))
            or row.get("fence_pending") is not False or row.get("fence_complete") is not False
            or row.get("finalization_required") is not False or row.get("finalization_state") != 0):
        fail("RESUME_RESULT_UNCERTAIN_HELD")
    audit = fresh_audit(plan, snapshot["environment"], dict(owner, record=row))
    original_command(plan, snapshot["environment"], owner, audit)
    context()
    if progress.get("state_synced"):
        raw, correlation = driver.read_flat_state(owner["state_path"], owner["instrument"])
        if correlation != owner["correlation"] or strict(raw).get("lease_generation") != row["generation"]:
            fail("SYNCED_CONTROLLER_STATE_CHANGED")
    else:
        sync_confirmed_generation(owner, row, progress["resume_sent_at_ms"])
    progress["state_synced"] = True
    state["phase"] = "OWNER_STATE_SYNCED"; save()


def comparable_catalog(expected, actual):
    """Only a known read-only WATCH may disappear at its own expiry."""
    present = {row["token_sha256"] for row in actual}
    return [row for row in expected if not (row["watch"] is True
        and row["expires_at_ms"] <= now_ms() and row["token_sha256"] not in present)]


def check_transition_catalog(plan, snapshot, state):
    rows = catalog(plan, snapshot["environment"])
    original = {row["token_sha256"]: row for row in comparable_catalog(snapshot["records"], rows)}
    if {row["token_sha256"] for row in rows} != set(original):
        fail("WRITING_OWNER_SET_CHANGED_HELD")
    for row in rows:
        old = original[row["token_sha256"]]
        if (type(row.get("paper")) is not bool or type(row.get("watch")) is not bool
                or row["paper"] == row["watch"]):
            fail("UNKNOWN_CATALOG_TEMPLATE")
        if row["watch"]:
            if row != old:
                fail("WATCH_CATALOG_CHANGED_HELD")
            continue
        progress = state.get("owner_progress", {}).get(row["token_sha256"], {})
        if not progress.get("resume_sent"):
            if row != old:
                fail("RECOVERY_OWNER_CHANGED_HELD")
        elif (row["generation"] != old["generation"] + 1 or row["recovery_only"] is not False
                or row["expires_at_ms"] > snapshot["deadline"] or row["recovery_command_sha256"]
                or any(row[key] != old[key] for key in ("agent_sha256", "session_sha256", "account_sha256", "domain_sha256"))
                or row["fence_pending"] is not False or row["fence_complete"] is not False
                or row["finalization_required"] is not False or row["finalization_state"] != 0):
            fail("RESUMED_OWNER_CHANGED_HELD")


def audit_qualification(value, plan, packages, uid):
    """Validate a pinned receipt from the isolated real-reader fixture."""
    if (not isinstance(value, dict)
            or value.get("schema") != "heptatrader.gateway-audit-reader-qualification.v1"
            or value.get("result") != "PASS" or type(value.get("uid")) is not int
            or value["uid"] != uid or uid <= 0 or value.get("execution_mode") != "SIMULATOR"
            or value.get("watch_only") is not True or value.get("persistent_lease_store") is not False
            or value.get("processes_reaped") is not True or value.get("production_services_changed") is not False):
        fail("AUDIT_SEAL_READER_QUALIFICATION_REQUIRED")
    inputs = value.get("source_inputs", {})
    if not isinstance(inputs, dict):
        fail("AUDIT_SEAL_QUALIFIED_INPUTS_REQUIRED")
    if any(not equivalent(inputs.get(name), plan[name]) for name in ("candidate", "fallback", "sessionctl")):
        fail("AUDIT_SEAL_QUALIFIED_ARTIFACT_CHANGED")
    evidence = inputs.get("recovery_evidence", {})
    if evidence.get("sha256") != plan["recovery_fixes_sha256"]:
        fail("AUDIT_SEAL_QUALIFIED_SOURCE_EVIDENCE_CHANGED")
    for pin in (evidence, inputs.get("simulator", {}), value.get("input_artifact", {}),
            value.get("qualification_tool", {})):
        if (not isinstance(pin, dict) or not isinstance(pin.get("path"), str)
                or not Path(pin["path"]).is_absolute()
                or re.fullmatch(r"[0-9a-f]{64}", pin.get("sha256", "")) is None):
            fail("AUDIT_SEAL_QUALIFICATION_PROVENANCE_INVALID")
    cases = value.get("cases")
    if not isinstance(cases, list) or not 1 <= len(cases) <= 2:
        fail("AUDIT_SEAL_READER_CASES_REQUIRED")
    covered = set()
    for case in cases:
        if not isinstance(case, dict):
            fail("AUDIT_SEAL_READER_CASES_REQUIRED")
        roles = case.get("roles")
        if (not isinstance(roles, list) or not roles or any(not isinstance(role, str) for role in roles)
                or len(set(roles)) != len(roles)
                or any(role not in ("candidate", "fallback") or role in covered for role in roles)):
            fail("AUDIT_SEAL_READER_ROLES_INVALID")
        binary = case.get("binary")
        for role in roles:
            if (not equivalent(binary, packages[role]["binary"])
                    or case.get("source_sha") != packages[role]["source_sha"]):
                fail("AUDIT_SEAL_QUALIFIED_BINARY_CHANGED")
        covered.update(roles)
        for name in ("valid", "missing", "corrupt"):
            result = case.get(name, {})
            if not isinstance(result, dict):
                fail("AUDIT_SEAL_READER_CASE_NOT_CONFIRMED")
            rejected = name != "valid"
            expected = {"seal_returncode": 0, "verify_before_returncode": 0,
                "verify_after_returncode": 1 if rejected else 0,
                "gateway_returncode": 7 if rejected else 0}
            if (any(type(result.get(key)) is not int or result[key] != number for key, number in expected.items())
                    or result.get("ready") is not (not rejected)
                    or result.get("verify_after_rejection") != ("SUPERVISOR_AUDIT_SEGMENT_INVALID" if rejected else "")
                    or result.get("rejection") != (
                        "SUPERVISOR_AUDIT_INIT_FAILED:SUPERVISOR_AUDIT_SEGMENT_INVALID" if rejected else "")):
                fail("AUDIT_SEAL_READER_CASE_NOT_CONFIRMED")
    if covered != {"candidate", "fallback"}:
        fail("AUDIT_SEAL_BOTH_READERS_REQUIRED")
    return inputs


def audit_seal_configuration(plan, environment, packages):
    if "audit_seal" not in plan:
        return None
    value = plan["audit_seal"]
    if (not isinstance(value, dict) or set(value) != {"path", "qualification"}
            or not isinstance(value.get("path"), str) or "\0" in value["path"]
            or plan.get("native_execution") is not None):
        fail("AUDIT_SEAL_GATEWAY_ONLY_PLAN_REQUIRED")
    path = Path(value["path"])
    if (not path.is_absolute() or str(path) != value["path"] or path.resolve(strict=True) != path
            or environment.get("HEPTA_TOOL_SUPERVISOR_AUDIT_JOURNAL") != str(path)):
        fail("AUDIT_SEAL_CONFIGURED_PATH_REQUIRED")
    # No content scan or native verify of the online journal during preflight.
    audit_private_file(path.lstat())
    item = value["qualification"]
    if (not isinstance(item, dict) or set(item) != {"path", "sha256"}
            or re.fullmatch(r"[0-9a-f]{64}", item.get("sha256", "")) is None):
        fail("AUDIT_SEAL_QUALIFICATION_PIN_REQUIRED")
    raw = read_private(item["path"], 262144)
    if digest(raw) != item["sha256"]:
        fail("AUDIT_SEAL_QUALIFICATION_CHANGED")
    inputs = audit_qualification(strict(raw), plan, packages, os.geteuid())
    evidence_pin = inputs["recovery_evidence"]
    evidence_raw = read_private(evidence_pin["path"], 262144)
    if digest(evidence_raw) != evidence_pin["sha256"]:
        fail("AUDIT_SEAL_SOURCE_EVIDENCE_CHANGED")
    evidence = strict(evidence_raw)
    if evidence.get("schema") != "heptatrader.gateway-recovery-source-evidence.v2":
        fail("AUDIT_SEAL_SOURCE_EVIDENCE_REQUIRED")
    builds = [evidence.get(name, {}) for name in ("candidate", "fallback")]
    for package in packages.values():
        if not any(equivalent(row.get("binary"), package["binary"])
                and row.get("source_sha") == package["source_sha"] for row in builds):
            fail("AUDIT_SEAL_READER_BUILD_NOT_BOUND")
    return {"path": str(path), "qualification": item, "sessionctl": plan["sessionctl"],
        "readers": {name: {"source_sha": package["source_sha"], "binary": package["binary"]}
            for name, package in packages.items()}}


def audit_private_file(info):
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.geteuid()
            or info.st_mode & 0o077 or not 0 <= info.st_size <= AUDIT_MAXIMUM_BYTES):
        fail("AUDIT_SEAL_PRIVATE_FILE_REQUIRED")


def audit_file(path, *, contents=True):
    """Read an exact private inode; never load the whole journal into memory."""
    path = Path(path)
    if not path.is_absolute() or path.resolve(strict=True) != path:
        fail("AUDIT_SEAL_CANONICAL_PATH_REQUIRED")
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    descriptor = None
    try:
        fcntl.flock(directory, fcntl.LOCK_SH | fcntl.LOCK_NB)
        parent = os.fstat(directory)
        if parent.st_uid != os.geteuid() or parent.st_mode & 0o022:
            fail("AUDIT_SEAL_PARENT_DIRECTORY_UNSAFE")
        descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
            dir_fd=directory)
        fcntl.flock(descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
        before = os.fstat(descriptor)
        audit_private_file(before)
        identity = lambda item: {key: getattr(item, "st_" + key) for key in
            ("dev", "ino", "mode", "uid", "gid", "nlink", "size", "mtime_ns", "ctime_ns")}
        parent_identity = lambda item: {key: getattr(item, "st_" + key) for key in
            ("dev", "ino", "mode", "uid", "gid")}
        result = {"file": identity(before), "directory": parent_identity(parent)}
        head = b""
        if contents:
            hasher = hashlib.sha256()
            remaining = before.st_size
            deadline = time.monotonic() + AUDIT_OFFLINE_TIMEOUT_SECONDS
            while remaining:
                if time.monotonic() >= deadline:
                    fail("AUDIT_SEAL_HASH_TIMEOUT")
                chunk = os.read(descriptor, min(1024 * 1024, remaining))
                if not chunk:
                    fail("AUDIT_SEAL_SOURCE_SHORT_READ")
                head = (head + chunk[:192])[:192]
                hasher.update(chunk); remaining -= len(chunk)
            result["sha256"] = hasher.hexdigest()
        named = os.stat(path.name, dir_fd=directory, follow_symlinks=False)
        if (identity(os.fstat(descriptor)) != identity(before) or identity(named) != identity(before)
                or parent_identity(path.parent.lstat()) != parent_identity(parent)
                or path.resolve(strict=True) != path):
            fail("AUDIT_SEAL_FILE_CHANGED")
        return result, head
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(directory)


def audit_file_unchanged(path, expected):
    actual, _ = audit_file(path, contents=False)
    if actual != {key: expected[key] for key in ("file", "directory")}:
        fail("AUDIT_SEAL_FILE_CHANGED")


def audit_offline_cli(plan, snapshot, operation):
    """The exact three-argument offline CLI; never a supervisor RPC."""
    flag = {"seal": "--audit-seal-stopped", "verify": "--audit-verify"}[operation]
    binary = pinned(plan["sessionctl"])
    available = (snapshot["deadline"] - now_ms() - MIN_TTL_MS) // 1000
    if available <= 0:
        fail("MAINTENANCE_TTL_BUDGET_EXHAUSTED")
    result = subprocess.run([str(binary), flag, snapshot["audit_seal"]["path"]], env={},
        capture_output=True, timeout=min(AUDIT_OFFLINE_TIMEOUT_SECONDS, available))
    # run() reaps on return, and kills/reaps on timeout. After a controller
    # crash the native directory lock serializes same-plan reconciliation.
    pinned(plan["sessionctl"])
    if result.returncode != 0 or result.stderr:
        fail("AUDIT_SEAL_" + operation.upper() + "_NOT_CONFIRMED:" + str(result.returncode))
    if operation == "seal":
        if result.stdout != b"audit sealed\n":
            fail("AUDIT_SEAL_RESULT_INVALID")
        return None
    match = re.fullmatch(rb"audit verified records=(0|[1-9][0-9]{0,19})\n", result.stdout)
    if match is None or int(match[1]) >= 2 ** 64:
        fail("AUDIT_SEAL_VERIFY_RESULT_INVALID")
    return int(match[1])


def audit_binding(snapshot, state):
    return json.loads(json.dumps({"plan_sha256": state["plan_sha256"], "markers": state["markers"],
        "deadline_ms": snapshot["deadline"], "gateway": snapshot["gateway"],
        "configuration": snapshot["audit_seal"]}))


def audit_anchor(raw):
    match = re.fullmatch(rb"HJA3\t([0-9a-f]{64})\t(0|[1-9][0-9]{0,19})\n", raw)
    if match is None or int(match[2]) >= 2 ** 64:
        fail("AUDIT_SEAL_ANCHOR_INVALID")
    return match[1].decode(), int(match[2])


def audit_intent(snapshot, state, progress):
    intent = progress.get("intent", {})
    if (intent.get("schema") != "heptatrader.gateway-audit-seal-intent.v1"
            or not equivalent(intent.get("binding"), audit_binding(snapshot, state))
            or intent.get("kind") not in ("EMPTY", "ANCHOR", "ROTATABLE")):
        fail("AUDIT_SEAL_INTENT_BINDING_CHANGED")
    source = intent.get("source", {})
    if (set(source) != {"file", "directory", "sha256"}
            or re.fullmatch(r"[0-9a-f]{64}", source.get("sha256", "")) is None
            or type(source.get("file", {}).get("size")) is not int
            or not 0 <= source["file"]["size"] <= AUDIT_MAXIMUM_BYTES):
        fail("AUDIT_SEAL_SOURCE_BINDING_INVALID")
    anchor = intent.get("anchor")
    if intent["kind"] == "ROTATABLE":
        if source["file"]["size"] == 0 or intent.get("records") is not None or anchor is not None:
            fail("AUDIT_SEAL_SEGMENT_BINDING_INVALID")
        return intent
    if type(intent.get("records")) is not int or not 0 <= intent["records"] < 2 ** 64:
        fail("AUDIT_SEAL_COUNT_BINDING_INVALID")
    if intent["kind"] == "EMPTY":
        if source["file"]["size"] != 0 or source["sha256"] != digest(b"") or intent["records"] != 0 or anchor is not None:
            fail("AUDIT_SEAL_EMPTY_BINDING_INVALID")
    else:
        if not isinstance(anchor, str):
            fail("AUDIT_SEAL_ANCHOR_BINDING_INVALID")
        predecessor, count = audit_anchor(anchor.encode())
        if count != intent["records"]:
            fail("AUDIT_SEAL_ANCHOR_COUNT_CHANGED")
        if source["file"]["size"] != len(anchor.encode()) or source["sha256"] != digest(anchor):
            fail("AUDIT_SEAL_ANCHOR_BINDING_INVALID")
    return intent


def audit_layout(snapshot, intent):
    path = Path(snapshot["audit_seal"]["path"])
    current, head = audit_file(path)
    source = intent["source"]
    if current["directory"] != source["directory"]:
        fail("AUDIT_SEAL_PARENT_DIRECTORY_CHANGED")
    segment = None
    if intent["kind"] == "ROTATABLE" and equivalent(current, source):
        return "NO_ACTIVE_REPLACEMENT", current, segment, None
    anchor = intent["anchor"]
    if intent["kind"] in ("EMPTY", "ANCHOR"):
        if not equivalent(current, source):
            fail("AUDIT_SEAL_NOOP_SOURCE_CHANGED")
        classification = "NOOP_DURABILITY_UNKNOWN"
    else:
        try:
            predecessor, _ = audit_anchor(head)
        except RuntimeError:
            fail("AUDIT_SEAL_PUBLICATION_UNKNOWN")
        if (predecessor != source["sha256"] or current["file"]["size"] != len(head)
                or current["sha256"] != digest(head)
                or current["file"]["ino"] == source["file"]["ino"]):
            fail("AUDIT_SEAL_PUBLICATION_UNKNOWN")
        anchor = head.decode()
        classification = "PUBLISHED_DURABILITY_UNKNOWN"
    if anchor is not None:
        predecessor, _ = audit_anchor(anchor.encode())
        segment_path = Path(str(path) + ".segments") / (predecessor + ".hja2")
        segment, _ = audit_file(segment_path, contents=False)
        if segment["directory"]["mode"] & 0o077:
            fail("AUDIT_SEAL_SEGMENT_DIRECTORY_UNSAFE")
        if intent["kind"] == "ROTATABLE" and segment["file"]["size"] != source["file"]["size"]:
            fail("AUDIT_SEAL_SEGMENT_SIZE_CHANGED")
    return classification, current, segment, anchor


def audit_writer_stopped(snapshot, state, context):
    context()
    if (state.get("gateway_stopped") is not True or state.get("owners_fenced") is not True
            or state.get("gateway_started") or state.get("phase") != "GATEWAY_STOPPED"
            or snapshot.get("native_execution") is not None
            or any(owner["resident"] is not None and owner["unit"] not in state["stopped_controllers"]
                for owner in snapshot["owners"])):
        fail("AUDIT_SEAL_STOPPED_TRANSACTION_REQUIRED")
    current = driver.fence.show_unit(GATEWAY)
    job = driver.unit_properties(GATEWAY, ("Job",), "AUDIT_SEAL_STOPPED_JOB")["Job"]
    if (current["ActiveState"] != "inactive" or current["MainPID"] not in ("", "0") or job
            or driver.pid_key(snapshot["gateway"]["status"]["MainPID"]) == tuple(snapshot["gateway"]["pid_key"])):
        fail("AUDIT_SEAL_WRITER_NOT_STOPPED")


def audit_receipt(snapshot, state):
    progress = state.get("audit_seal", {})
    intent = audit_intent(snapshot, state, progress)
    receipt = progress.get("receipt", {})
    if (progress.get("status") != "VERIFIED"
            or receipt.get("schema") != "heptatrader.gateway-audit-seal-receipt.v1"
            or receipt.get("intent_sha256") != digest(json.dumps(intent, sort_keys=True))
            or type(receipt.get("records")) is not int or not 0 <= receipt["records"] < 2 ** 64
            or (intent["records"] is not None and receipt["records"] != intent["records"])
            or type(receipt.get("seal_exit_code")) is not int or receipt["seal_exit_code"] != 0
            or type(receipt.get("verify_exit_code")) is not int or receipt["verify_exit_code"] != 0):
        fail("AUDIT_SEAL_VERIFIED_RECEIPT_REQUIRED")
    if intent["kind"] == "EMPTY":
        if receipt.get("anchor") is not None or receipt["records"] != 0:
            fail("AUDIT_SEAL_RECEIPT_ANCHOR_CHANGED")
    else:
        anchor = receipt.get("anchor")
        if not isinstance(anchor, str):
            fail("AUDIT_SEAL_RECEIPT_ANCHOR_CHANGED")
        predecessor, count = audit_anchor(anchor.encode())
        expected_predecessor = (intent["source"]["sha256"] if intent["kind"] == "ROTATABLE"
            else audit_anchor(intent["anchor"].encode())[0])
        if count != receipt["records"] or predecessor != expected_predecessor:
            fail("AUDIT_SEAL_RECEIPT_ANCHOR_CHANGED")
    return intent, receipt


def prepare_audit_intent(plan, snapshot, state, progress, save, context):
    original_progress = progress
    progress = json.loads(json.dumps(progress))
    path = Path(snapshot["audit_seal"]["path"])
    first = "intent" not in progress
    if first:
        source, head = audit_file(path)
        kind = "EMPTY" if source["file"]["size"] == 0 else "ROTATABLE"
        records = 0 if kind == "EMPTY" else None
        anchor = None
        if kind != "EMPTY":
            if source["file"]["size"] == len(head):
                try:
                    _, count = audit_anchor(head)
                    kind, anchor, records = "ANCHOR", head.decode(), count
                except RuntimeError:
                    pass  # Nonempty legacy with zero HJA2 records still rotates.
        audit_writer_stopped(snapshot, state, context)
        audit_file_unchanged(path, source)
        progress["intent"] = {"schema": "heptatrader.gateway-audit-seal-intent.v1",
            "binding": audit_binding(snapshot, state), "source": source, "records": records,
            "kind": kind, "anchor": anchor, "created_at_ms": now_ms()}
        progress["status"] = "INTENT_RECORDED"
    intent = audit_intent(snapshot, state, progress)
    if first and intent["kind"] == "ROTATABLE":
        # H binds the exact original bytes, including any prior anchor.
        # Native seal and final verify establish N; no extra pre-count scan.
        audit_file_unchanged(path, intent["source"])
        classification = "NO_ACTIVE_REPLACEMENT"
    else:
        classification, _, _, _ = audit_layout(snapshot, intent)
    if classification == "NO_ACTIVE_REPLACEMENT":
        archive = Path(str(path) + ".segments")
        space = os.statvfs(archive if archive.exists() else path.parent)
        parent_space = os.statvfs(path.parent)
        if (space.f_bavail * space.f_frsize < intent["source"]["file"]["size"] + 4096
                or parent_space.f_bavail * parent_space.f_frsize < 4096):
            fail("AUDIT_SEAL_COPY_SPACE_REQUIRED")
    attempt = {"at_ms": now_ms(), "observed": classification, "status": "REQUESTED"}
    progress.setdefault("attempts", []).append(attempt)
    progress["status"] = "UNKNOWN"
    # Reserve room for the receipt and HELD diagnostics before mutating state.
    if len(json.dumps(dict(state, audit_seal=progress), sort_keys=True).encode()) + 8192 >= 1048576:
        fail("AUDIT_SEAL_CHECKPOINT_BOUND")
    original_progress.clear(); original_progress.update(progress)
    save()  # Intent and unknown outcome are durable before the filesystem effect.
    return intent, attempt


def run_audit_seal(plan, snapshot, state, save, context):
    if snapshot.get("audit_seal") is None:
        return
    progress = state.setdefault("audit_seal", {})
    if progress.get("status") == "VERIFIED":
        audit_receipt(snapshot, state)
        return  # A started Gateway may legitimately append after the anchor.
    if progress.get("writer_start_requested"):
        fail("AUDIT_SEAL_UNKNOWN_AFTER_WRITER_START")
    audit_writer_stopped(snapshot, state, context)
    intent, attempt = prepare_audit_intent(plan, snapshot, state, progress, save, context)
    path = Path(snapshot["audit_seal"]["path"])
    try:
        audit_writer_stopped(snapshot, state, context)
        audit_offline_cli(plan, snapshot, "seal")
        classification, active, segment, anchor = audit_layout(snapshot, intent)
        if classification == "NO_ACTIVE_REPLACEMENT":
            fail("AUDIT_SEAL_SUCCESS_WITHOUT_PUBLICATION")
        records = audit_anchor(anchor.encode())[1] if anchor is not None else 0
        if (intent["records"] is not None and records != intent["records"]
                or audit_offline_cli(plan, snapshot, "verify") != records):
            fail("AUDIT_SEAL_VERIFIED_COUNT_CHANGED")
        audit_writer_stopped(snapshot, state, context)
        audit_file_unchanged(path, active)
        if segment is not None:
            predecessor, _ = audit_anchor(anchor.encode())
            audit_file_unchanged(Path(str(path) + ".segments") / (predecessor + ".hja2"), segment)
        # The pinned verifier checks every retained segment's actual SHA and
        # count. Avoid a duplicate Python scan of the same large segment.
        receipt = {"schema": "heptatrader.gateway-audit-seal-receipt.v1",
            "intent_sha256": digest(json.dumps(intent, sort_keys=True)), "records": records, "anchor": anchor,
            "active": active, "segment": segment, "completed_at_ms": now_ms(),
            "seal_exit_code": 0, "verify_exit_code": 0}
        progress["receipt"] = receipt; progress["status"] = "VERIFIED"
        attempt["status"] = "VERIFIED"
        save()
    except BaseException as error:
        if "receipt" in progress:
            progress["unconfirmed_receipt"] = progress.pop("receipt")
        progress["status"] = "UNKNOWN"
        attempt.update(status="UNKNOWN", error_type=type(error).__name__)
        raise


def audit_before_gateway_start(snapshot, state, binary, save, context, *, request_start=True):
    if snapshot.get("audit_seal") is None:
        return
    if binary not in [item["binary"] for item in snapshot["packages"].values()]:
        fail("AUDIT_SEAL_UNREVIEWED_WRITER")
    intent, receipt = audit_receipt(snapshot, state)
    progress = state["audit_seal"]
    requested = progress.get("writer_start_requested")
    receipt_sha = digest(json.dumps(receipt, sort_keys=True))
    if requested is not None:
        if (requested.get("receipt_sha256") != receipt_sha
                or requested.get("binary") not in [item["binary"] for item in snapshot["packages"].values()]):
            fail("AUDIT_SEAL_WRITER_START_BINDING_CHANGED")
        return
    audit_writer_stopped(snapshot, state, context)
    _, active, segment, anchor = audit_layout(snapshot, intent)
    if (anchor != receipt.get("anchor") or not equivalent(active, receipt.get("active"))
            or not equivalent(segment, receipt.get("segment"))):
        fail("AUDIT_SEAL_VERIFIED_FILES_CHANGED")
    if not request_start:
        return
    progress["writer_start_requested"] = {"receipt_sha256": receipt_sha, "binary": binary, "at_ms": now_ms()}
    try:
        save()  # Only after this may a new writer legitimately append.
    except BaseException:
        progress.pop("writer_start_requested", None)
        raise


def maintenance_upgrade(plan, plan_sha, *, home=None, resume=False, native_fallback=False):
    home = Path(home or pwd.getpwuid(os.geteuid()).pw_dir)
    root = home / ".local/state/heptatrader"
    checkpoint = root / "operator-maintenance" / CHECKPOINT
    policy = home / ".config/systemd/user" / (GATEWAY + ".d") / POLICY
    execution_dropin = policy.with_name(RELEASE)
    with driver.latch.operator_lock(root) as check_lock_binding:
        if checkpoint.exists():
            previous_raw = read_private(checkpoint, 1048576)
            previous = strict(previous_raw)
            if previous.get("status") == "COMPLETE":
                if previous.get("plan_sha256") == plan_sha:
                    current = inspect(plan, home)
                    selected = previous.get("selected_release", "candidate")
                    if current["gateway"]["binary"] != current["packages"][selected]["binary"]:
                        fail("COMPLETED_GATEWAY_RELEASE_CHANGED")
                    return {"status": "ALREADY_COMPLETE", "phase": previous["phase"],
                        "selected_release": selected, "gateway_binary_sha256": current["gateway"]["binary"]["sha256"]}
                if resume:
                    fail("COMPLETED_OTHER_PLAN_CANNOT_BE_RESUMED")
                archive_completed(checkpoint, previous_raw)
        if resume:
            state = strict(read_private(checkpoint, 1048576))
            if state.get("plan_sha256") != plan_sha or state.get("status") == "COMPLETE":
                fail("RESUMABLE_MAINTENANCE_CHECKPOINT_REQUIRED")
            snapshot = effective_snapshot(state)
            native_maintenance.reconcile_release(sys.modules[__name__], home, snapshot, state)
            state.update(status="RUNNING")
            state.pop("error", None)
            if state.get("gateway_stopped") and execution_dropin.exists():
                raw = read_private(execution_dropin, 65536).decode()
                for name, package in snapshot["packages"].items():
                    if raw == "[Service]\nExecStart=\nExecStart=" + package["binary"]["path"] + "\n":
                        state["release_bytes"] = raw; state["selected_release"] = name
                        break
                else:
                    if raw != snapshot.get("original_release_bytes"):
                        fail("UNKNOWN_GATEWAY_RELEASE_OVERRIDE")
        else:
            if checkpoint.exists():
                fail("EXISTING_MAINTENANCE_CHECKPOINT_REQUIRES_REVIEW")
            snapshot = inspect(plan, home)
            state = {"schema": "heptatrader.gateway-upgrade.v1", "plan_sha256": plan_sha,
                "status": "RUNNING", "phase": "PREPARED", "snapshot": snapshot,
                "markers": driver.marker_identity(home), "stopped_controllers": []}
        if "audit_seal" in plan or snapshot.get("audit_seal") is not None:
            if not equivalent(snapshot.get("audit_seal"),
                    audit_seal_configuration(plan, snapshot["environment"], snapshot["packages"])):
                fail("AUDIT_SEAL_CONFIGURATION_CHANGED")
        def save():
            atomic_json(checkpoint, state)
        controller_observations = {}
        def observe_controller(owner):
            unit = owner["unit"]
            actual = observe_controller_start(owner, controller_observations.get(unit))
            controller_observations[unit] = actual
            return actual
        def context(*, expected_interactive=None, allow_reconnect_refresh=True, allow_lease_successor=False):
            check_lock_binding()
            if not equivalent(driver.marker_identity(home), state["markers"]):
                fail("MAINTENANCE_MARKERS_CHANGED")
            if unit_files(GATEWAY) != snapshot["unit_files"]:
                fail("ORIGINAL_GATEWAY_CONFIG_CHANGED")
            for unit, files in snapshot.get("controller_files", {}).items():
                if unit_files(unit) != files:
                    fail("ORIGINAL_CONTROLLER_CONFIG_CHANGED")
            configured_environment(snapshot["environment"])
            expected_release = state.get("release_bytes", snapshot.get("original_release_bytes"))
            actual_release = read_private(execution_dropin, 65536).decode() if execution_dropin.exists() else None
            if actual_release != expected_release:
                fail("GATEWAY_RELEASE_OVERRIDE_CHANGED")
            current = {name: driver.fence.show_unit(name) for name in driver.fence.RISK_UNITS}
            exempt = {GATEWAY, driver.BROKER_UNIT, *snapshot["frozen"], *state["stopped_controllers"]}
            if state.get("native_execution") is not None:
                exempt.update(native_maintenance.UNITS)
            stopping = state.get("stopping")
            if stopping is not None:
                unit = stopping["unit"]; exempt.add(unit)
                status = current[unit]; original = stopping["original"]
                own = home / ".config/systemd/user" / (unit + ".d") / POLICY
                raw = read_private(own, 65536) if own.exists() else None
                if raw not in (None, SAFE_STOP, HELD_STOP):
                    fail("STOPPING_POLICY_CHANGED")
                if status["ActiveState"] == "active":
                    if (status["MainPID"] != original["status"]["MainPID"]
                            or status["InvocationID"] != original["status"]["InvocationID"]
                            or driver.pid_key(status["MainPID"]) != tuple(original["pid_key"])):
                        fail("STOPPING_PROCESS_CHANGED")
                elif status["ActiveState"] in ("deactivating", "inactive"):
                    if stopping.get("requested") is not True:
                        fail("UNREQUESTED_CONTROLLER_STOP")
                    if status["MainPID"] not in ("", "0", original["status"]["MainPID"]):
                        fail("STOPPING_PROCESS_CHANGED")
                else:
                    fail("STOPPING_SERVICE_STATE_UNRESOLVED")
            protected = {name: value for name, value in snapshot["statuses"].items() if name not in exempt}
            driver.fence.check_state(protected, current, required=True)
            for unit in state["stopped_controllers"]:
                started = state.get("controllers_started", {}).get(unit)
                if started:
                    instrument = next(owner["instrument"] for owner in snapshot["owners"] if owner["unit"] == unit)
                    actual = driver.running_driver(unit, instrument)
                    if actual["manifest"] != started["manifest"] or actual["manifest_sha256"] != started["manifest_sha256"]:
                        fail("RESTORED_CONTROLLER_RELEASE_CHANGED")
                    if tuple(actual["pid_key"]) != tuple(started["pid_key"]):
                        fail("RESTORED_CONTROLLER_PROCESS_CHANGED")
                elif unit == state.get("starting_controller"):
                    owner = next(owner for owner in snapshot["owners"] if owner["unit"] == unit)
                    if unit in controller_observations or current[unit]["ActiveState"] in ("active", "activating"):
                        observe_controller(owner)
                    elif current[unit]["ActiveState"] != "inactive" or current[unit]["MainPID"] not in ("", "0"):
                        fail("STARTING_CONTROLLER_STATE_UNRESOLVED")
                elif current[unit]["ActiveState"] != "inactive" or current[unit]["MainPID"] not in ("", "0"):
                    fail("STOPPED_CONTROLLER_RESTARTED_EXTERNALLY")
            changing_native = native_maintenance.verify(sys.modules[__name__], plan, home, snapshot, state, current)
            if not changing_native:
                if not equivalent(native_hold_runtime(plan, current), snapshot["hold_runtime"]):
                    fail("NATIVE_EXECUTION_HOLD_RUNTIME_CHANGED")
                interactive = driver.interactive_broker_snapshot(plan["interactive_broker_pid"], GATEWAY, current)
            elif state["native_execution"].get("accepted"):
                interactive = driver.interactive_broker_snapshot(plan["interactive_broker_pid"], GATEWAY, current)
                if not equivalent(interactive, state["native_execution"]["interactive"]):
                    fail("NATIVE_HANDOFF_ACCEPTED_CONNECTION_CHANGED")
            else:
                interactive = None
            for unit, expected in snapshot.get("controller_topology", {}).items():
                actual = controller_stop_edges(unit)
                if not equivalent(actual, expected):
                    fail("CONTROLLER_STOP_TOPOLOGY_CHANGED")
            for unit, expected in snapshot["frozen"].items():
                if driver.frozen_driver_snapshot(unit) != expected:
                    fail("FROZEN_CONTROLLER_CHANGED")
            if now_ms() + MIN_TTL_MS >= snapshot["deadline"]:
                fail("MAINTENANCE_TTL_BUDGET_EXHAUSTED")
            for item in snapshot["packages"].values():
                pinned(item["binary"])
            expected = effective_interactive(snapshot, state) if expected_interactive is None else expected_interactive
            if (not changing_native and resume and state.get("phase") == "PREPARED"
                    and allow_reconnect_refresh and expected_interactive is None):
                # A lease can advance without a TCP reconnect. Reconcile it
                # before any stop/fence intent, through the same fresh proof.
                rows = catalog(plan, snapshot["environment"])
                pristine = prepared_reconnect_pristine(state, resume)
                if not equivalent(rows, snapshot["records"]):
                    if pristine:
                        if not allow_lease_successor:
                            fail("PREPARED_LEASE_SUCCESSOR_REQUIRES_RESUME_BOUNDARY")
                        refresh_interactive_witness(interactive, allow_lease_successor=True)
                        return
                    # Existing all-frozen flows can fence successive owners
                    # while phase is still PREPARED. Keep that original flow,
                    # but never reconcile another lease after control intent.
                    generations = {row["token_sha256"]: row["generation"] for row in rows}
                    if any(generations.get(owner["token_sha256"]) != owner["state_generation"]
                            for owner in snapshot["owners"]):
                        fail("PREPARED_RECONNECT_CONTROL_EFFECTS_NOT_EMPTY")
                if pristine and allow_lease_successor and state.get("prepared_lease_successors"):
                    prepared_reconnect_effects(state, resume)
                    prepared_processes_unchanged(home, snapshot)
                    authority = held_native_authority(plan, snapshot, snapshot["owners"][0])
                    if any(not equivalent(previous.get("native_authority"), authority)
                            for previous in state["prepared_reconnects"]):
                        fail("PREPARED_RECONNECT_NATIVE_AUTHORITY_CHANGED")
            if not changing_native and not equivalent(interactive, expected):
                if not allow_reconnect_refresh or expected_interactive is not None:
                    fail("INTERACTIVE_BROKER_CHANGED")
                refresh_interactive_witness(interactive, allow_lease_successor=allow_lease_successor)

        def refresh_interactive_witness(interactive, *, allow_lease_successor=False):
            nonlocal snapshot
            before_snapshot = snapshot
            proof_snapshot = snapshot
            successors = []
            # PREPARED may reconcile only before any upgrade control intent.
            # The existing post-stop recovery keeps its original restrictions.
            prepared = resume and state.get("phase") == "PREPARED"
            if prepared:
                prepared_reconnect_effects(state, resume)
            if not prepared and (not resume or state.get("gateway_stopped") is not True or state.get("owners_fenced") is not True
                    or state.get("phase") not in ("GATEWAY_STOPPED", "GATEWAY_STARTED")
                    or state.get("stopping") or state.get("starting_controller") or state.get("controllers_started")
                    or any(any(name in progress for name in ("resume_sent", "resume_sent_at_ms", "state_synced"))
                        for progress in state.get("owner_progress", {}).values())):
                fail("INTERACTIVE_RECONNECT_REFRESH_NOT_ALLOWED")
            original_identity = interactive_process_identity(snapshot["interactive"])
            current_identity = interactive_process_identity(interactive)
            if original_identity is None or current_identity is None or not equivalent(original_identity, current_identity):
                fail("INTERACTIVE_BROKER_IDENTITY_CHANGED")
            native_before = None
            if prepared:
                binary = snapshot["gateway"]["binary"]
                native_before = held_native_authority(plan, snapshot, snapshot["owners"][0])
                if any(not equivalent(previous.get("native_authority"), native_before)
                        for previous in state.get("prepared_reconnects", [])):
                    fail("PREPARED_RECONNECT_NATIVE_AUTHORITY_CHANGED")
            else:
                for owner in snapshot["owners"]:
                    resident = owner["resident"]
                    if resident is not None:
                        status = driver.fence.show_unit(owner["unit"])
                        if (owner["unit"] not in state["stopped_controllers"]
                                or status["ActiveState"] != "inactive" or status["MainPID"] not in ("", "0")
                                or driver.pid_key(resident["status"]["MainPID"]) == tuple(resident["pid_key"])):
                            fail("RECONNECT_CONTROLLERS_NOT_NATURALLY_STOPPED")
                selected = state.get("selected_release")
                if selected not in snapshot["packages"]:
                    fail("RECONNECT_SELECTED_GATEWAY_UNKNOWN")
                binary = snapshot["packages"][selected]["binary"]
                observed_gateway = gateway(binary)
                if state.get("gateway_started"):
                    same_gateway(state["gateway_started"], observed_gateway)
            rows = catalog(plan, snapshot["environment"])
            if prepared:
                proof_snapshot, successors = lease_successor_snapshot(snapshot, rows)
                if successors and not allow_lease_successor:
                    fail("PREPARED_LEASE_SUCCESSOR_REQUIRES_RESUME_BOUNDARY")
                observed_gateway = prepared_processes_unchanged(home, proof_snapshot)
                if successors:
                    bind_owners(rows, proof_snapshot["owners"], proof_snapshot["environment"], deadline=snapshot["deadline"])
            elif not equivalent(rows, snapshot["records"]):
                fail("RECONNECT_ORIGINAL_CATALOG_CHANGED")
            paper = {row["token_sha256"]: row for row in rows if row["paper"] is True}
            if set(paper) != {owner["token_sha256"] for owner in proof_snapshot["owners"]}:
                fail("RECONNECT_WRITING_OWNER_SET_CHANGED")
            observations = []
            authority = None
            for owner in proof_snapshot["owners"]:
                row = paper[owner["token_sha256"]]
                if (not equivalent(row, owner["record"]) or row["generation"] != owner["state_generation"]
                        or (not prepared and (row["recovery_only"] is not True
                            or row["recovery_command_sha256"] != digest(owner["command"])))):
                    fail("RECONNECT_ORIGINAL_RECOVERY_SCOPE_CHANGED")
                audit = fresh_audit(plan, snapshot["environment"], owner)
                original_command(plan, snapshot["environment"], owner, audit)
                reply = audit["reply"]
                current_authority = (reply["service_epoch"], reply["service_fencing_generation"], reply["broker_connection_epoch"])
                if prepared and current_authority[:2] != native_before:
                    fail("PREPARED_RECONNECT_NATIVE_AUTHORITY_CHANGED")
                if authority is not None and current_authority != authority:
                    fail("RECONNECT_NATIVE_AUTHORITIES_CHANGED")
                authority = current_authority
                observations.append({"token_sha256": owner["token_sha256"], "generation": row["generation"],
                    "original_command_sha256": digest(owner["command"]), "audit_command_sha256": digest(reply["command_id"]),
                    "at_ms": audit["at_ms"], "service_epoch": reply["service_epoch"],
                    "service_fencing_generation": reply["service_fencing_generation"],
                    "broker_connection_epoch": reply["broker_connection_epoch"],
                    "broker_recovery_audit_barrier_complete": reply["broker_recovery_audit_barrier_complete"],
                    "broker_recovery_audit_new_connection_epoch_required": reply["broker_recovery_audit_new_connection_epoch_required"],
                    "owner_active_order_count": reply["owner_active_order_count"],
                    "owner_uncertain_command_count": reply["owner_uncertain_command_count"],
                    "broker_global_active_order_count": reply["broker_global_active_order_count"],
                    "broker_position_quantity": reply["broker_position_quantity"],
                    "broker_gross_absolute_position": reply["broker_gross_absolute_position"],
                    "native_health": audit["native_health"]})
            if not observations or any(not 0 <= now_ms() - item["at_ms"] <= MAX_AUDIT_AGE_MS for item in observations):
                fail("RECONNECT_FRESH_AUDIT_EXPIRED")
            # Re-check all frozen policy/markers/processes and the actual TCP
            # pair after the native observations. This call cannot refresh.
            context(expected_interactive=interactive, allow_reconnect_refresh=False)
            same_gateway(observed_gateway, gateway(binary))
            if not equivalent(catalog(plan, snapshot["environment"]), rows):
                fail("RECONNECT_CATALOG_CHANGED_DURING_AUDIT")
            check_lock_binding()
            if not equivalent(driver.marker_identity(home), state["markers"]):
                fail("MAINTENANCE_MARKERS_CHANGED")
            if any(not 0 <= now_ms() - item["at_ms"] <= MAX_AUDIT_AGE_MS for item in observations):
                fail("RECONNECT_FRESH_AUDIT_EXPIRED")
            for owner in snapshot["owners"]:
                resident = owner["resident"]
                if not prepared and resident and driver.pid_key(resident["status"]["MainPID"]) == tuple(resident["pid_key"]):
                    fail("RECONNECT_CONTROLLERS_NOT_NATURALLY_STOPPED")
            receipt = {"at_ms": now_ms(), "gateway_pid_key": observed_gateway["pid_key"],
                "gateway_binary_sha256": binary["sha256"], "witness_sha256": digest(json.dumps(interactive, sort_keys=True)),
                "audits": observations}
            if prepared:
                prepared_reconnect_effects(state, resume)
                prepared_processes_unchanged(home, proof_snapshot)
                for owner in proof_snapshot["owners"]:
                    if held_native_authority(plan, proof_snapshot, owner) != native_before:
                        fail("PREPARED_RECONNECT_NATIVE_AUTHORITY_CHANGED")
                context(expected_interactive=interactive, allow_reconnect_refresh=False)
                if not equivalent(catalog(plan, snapshot["environment"]), rows):
                    fail("RECONNECT_CATALOG_CHANGED_DURING_AUDIT")
                check_lock_binding()
                if not equivalent(driver.marker_identity(home), state["markers"]):
                    fail("MAINTENANCE_MARKERS_CHANGED")
                if any(not 0 <= now_ms() - item["at_ms"] <= MAX_AUDIT_AGE_MS for item in observations):
                    fail("RECONNECT_FRESH_AUDIT_EXPIRED")
                receipt.update(schema="heptatrader.gateway-prepared-reconnect.v1", plan_sha256=state["plan_sha256"],
                    markers=state["markers"], deadline_ms=snapshot["deadline"], native_authority=native_before,
                    previous_witness=effective_interactive(snapshot, state), witness=interactive,
                    original_snapshot=json.loads(json.dumps(before_snapshot)))
                history = [*state.get("prepared_reconnects", []), receipt]
                updates = {"prepared_reconnects": history}
                if successors:
                    successor_receipt = {"schema": "heptatrader.gateway-lease-successor.v1",
                        "at_ms": now_ms(), "plan_sha256": state["plan_sha256"], "markers": state["markers"],
                        "deadline_ms": state["snapshot"]["deadline"],
                        "before_catalog": json.loads(json.dumps(before_snapshot["records"])),
                        "after_catalog": json.loads(json.dumps(rows)), "successors": successors,
                        "reconnect_receipt_sha256": digest(json.dumps(receipt, sort_keys=True)),
                        "historical_control_operation": "NOT_INFERRED"}
                    updates.update(effective_snapshot=proof_snapshot,
                        prepared_lease_successors=[*state.get("prepared_lease_successors", []), successor_receipt])
                if len(json.dumps(dict(state, **updates), sort_keys=True).encode()) >= 1048576:
                    fail("PREPARED_RECONNECT_RECEIPT_BOUND")
                effective_snapshot(dict(state, **updates))
                state.update(updates)
                snapshot = proof_snapshot
            else:
                snapshot["interactive"] = interactive
                state["interactive_refresh"] = receipt
            save()  # Operational witness only; the authenticated catalog is never written.

        def controlled_stop(unit, original, audit, *, selected_policy):
            audits = audit if isinstance(audit, list) else [audit]
            context()
            if any(now_ms() - item["at_ms"] > MAX_AUDIT_AGE_MS for item in audits):
                fail("FRESH_STOP_AUDIT_EXPIRED")
            old_policy = read_private(selected_policy, 65536) if selected_policy.exists() else None
            if old_policy is not None and (old_policy != HELD_STOP or str(selected_policy) not in state.get("owned_policies", [])):
                fail("EXISTING_STOP_POLICY_OVERRIDE")
            state["stopping"] = {"unit": unit, "original": original, "requested": False}
            state.setdefault("owned_policies", []).append(str(selected_policy))
            state["phase"] = "STOP_POLICY_PREPARING"; save()
            put_owned(selected_policy, SAFE_STOP, old_policy)
            driver.ctl("daemon-reload")
            props = driver.unit_properties(unit, ("RefuseManualStop", "SendSIGKILL", "TimeoutStopUSec", "Restart"), "NORMAL_STOP")
            if props != {"RefuseManualStop": "no", "SendSIGKILL": "no", "TimeoutStopUSec": "infinity", "Restart": "no"}:
                fail("AUDITED_NORMAL_STOP_POLICY_NOT_EFFECTIVE")
            context()
            if any(now_ms() - item["at_ms"] > MAX_AUDIT_AGE_MS for item in audits):
                fail("FRESH_STOP_AUDIT_EXPIRED")
            state["phase"] = "STOP_REQUESTED"; state["stopping"]["requested"] = True
            save()
            driver.ctl("stop", "--no-block", unit)
            try:
                return observe_stopped(unit, original)
            finally:
                # A pending normal stop must retain its no-SIGKILL policy.
                # Restore manual-stop protection without cancelling the job.
                driver.replace_exact(selected_policy, SAFE_STOP, HELD_STOP)
                driver.ctl("daemon-reload")
        save()
        try:
            # Freeze the effective lease before taking a fresh stop audit.
            # Later drift must retain the hold for another same-plan resume.
            context(allow_lease_successor=True)
            if state.get("starting_controller"):
                unit = state["starting_controller"]
                owner = next(owner for owner in snapshot["owners"] if owner["unit"] == unit)
                progress = state.get("owner_progress", {}).get(owner["token_sha256"], {})
                if progress.get("state_synced") is not True:
                    fail("CONTROLLER_START_INTENT_WITHOUT_RESUME_PROOF")
                check_transition_catalog(plan, snapshot, state)
                resume_owner(plan, snapshot, owner, state, save, context)
                if unit in controller_observations or driver.fence.show_unit(unit)["ActiveState"] in ("active", "activating"):
                    actual = observe_controller(owner)
                    state.setdefault("controllers_started", {})[unit] = actual
                state.pop("starting_controller"); save()
            if state.get("stopping"):
                stopping = state["stopping"]
                unit = stopping["unit"]
                current = driver.fence.show_unit(unit)
                job = driver.unit_properties(unit, ("Job",), "STOP_JOB")["Job"]
                if current["ActiveState"] == "active" and not job:
                    # No stop job exists. Restore the original shield, then
                    # the normal path consumes a NEW native audit before stop.
                    own = home / ".config/systemd/user" / (unit + ".d") / POLICY
                    if own.exists():
                        raw = read_private(own, 65536)
                        if raw not in (SAFE_STOP, HELD_STOP):
                            fail("STOPPING_POLICY_CHANGED")
                        remove_owned(own, raw); driver.ctl("daemon-reload")
                else:
                    observe_stopped(unit, stopping["original"])
                    if unit == GATEWAY:
                        state["gateway_stopped"] = True
                    elif unit not in state["stopped_controllers"]:
                        state["stopped_controllers"].append(unit)
                state.pop("stopping"); save()
            for owner in snapshot["owners"]:
                if owner["resident"] is None or owner["unit"] in state["stopped_controllers"]:
                    continue
                original = dict(owner["resident"], pid_key=tuple(owner["resident"]["pid_key"]))
                driver.same_driver(original, driver.running_driver(owner["unit"], owner["instrument"]))
                audit = fresh_audit(plan, snapshot["environment"], owner)
                stop_policy = home / ".config/systemd/user" / (owner["unit"] + ".d") / POLICY
                controlled_stop(owner["unit"], owner["resident"], audit, selected_policy=stop_policy)
                state["stopped_controllers"].append(owner["unit"])
                state.pop("stopping", None); save()
            if not policy.exists():
                # Before any reversible owner fence, prevent an old binary
                # from automatically restarting into its known restore bug.
                context()
                for owner in snapshot["owners"]:
                    fresh_audit(plan, snapshot["environment"], owner)
                state.setdefault("owned_policies", []).append(str(policy)); save()
                put_owned(policy, HELD_STOP); driver.ctl("daemon-reload")
            if not state.get("owners_fenced"):
                rows = catalog(plan, snapshot["environment"])
                bind_owners(rows, snapshot["owners"], snapshot["environment"], deadline=snapshot["deadline"])
                for owner in snapshot["owners"]:
                    context()
                    if owner["record"]["recovery_only"] is True:
                        # The old running Gateway renews TTL on repeated
                        # RecoveryQuery. Reconcile an already committed exact
                        # fence instead of replaying that mutating operation.
                        if owner["record"]["recovery_command_sha256"] != digest(owner["command"]):
                            fail("ORIGINAL_RECOVERY_COMMAND_MISMATCH")
                        audit = fresh_audit(plan, snapshot["environment"], owner)
                        original_command(plan, snapshot["environment"], owner, audit)
                        continue
                    reply = rpc(plan, snapshot["environment"], owner, "recovery-query", "--command-id", owner["command"])
                    if (reply.get("accepted") is not True or reply.get("recovery_only") is not True
                            or reply.get("authoritative_command_status") is not True
                            or reply.get("command_id") != owner["command"]
                            or reply.get("lease_generation") != owner["record"]["generation"]
                            or reply.get("command_status") not in ("accepted", "rejected")
                            or reply.get("owner_audit_authoritative") is not True or reply.get("owner_audit_complete") is not True
                            or reply.get("owner_active_order_count") != 0 or reply.get("owner_uncertain_command_count") != 0
                            or reply.get("paper_finalization_required") is not False):
                        fail("ORIGINAL_OWNER_RECOVERY_NOT_CONFIRMED")
                rows = catalog(plan, snapshot["environment"])
                snapshot["deadline"] = min(snapshot["deadline"], bind_owners(rows, snapshot["owners"], snapshot["environment"]))
                for owner in snapshot["owners"]:
                    if (owner["record"]["recovery_only"] is not True or
                            owner["record"]["recovery_command_sha256"] != digest(owner["command"])):
                        fail("DURABLE_ORIGINAL_OWNER_FENCE_REQUIRED")
                snapshot["records"] = rows
                state["owners_fenced"] = True; state["phase"] = "OWNERS_FENCED"; save()
            if not state.get("gateway_stopped"):
                current = gateway(snapshot["gateway"]["binary"])
                same_gateway(snapshot["gateway"], current)
                audits = [fresh_audit(plan, snapshot["environment"], owner) for owner in snapshot["owners"]]
                if any(now_ms() - audit["at_ms"] > MAX_AUDIT_AGE_MS for audit in audits):
                    fail("ALL_OWNER_STOP_AUDITS_NOT_FRESH")
                controlled_stop(GATEWAY, current, audits, selected_policy=policy)
                state.pop("stopping", None)
                state["gateway_stopped"] = True; state["phase"] = "GATEWAY_STOPPED"; save()
            if (snapshot.get("audit_seal") is not None and not state.get("gateway_started")
                    and not state.get("audit_seal", {}).get("writer_start_requested")
                    and state["phase"] != "GATEWAY_STOPPED"):
                # A lost normal-stop reply can leave the old phase recorded.
                state["phase"] = "GATEWAY_STOPPED"; save()
            context()
            run_audit_seal(plan, snapshot, state, save, context)
            native_maintenance.run(sys.modules[__name__], plan, home, snapshot, state, save, context,
                fallback=native_fallback)
            audit_before_gateway_start(snapshot, state,
                snapshot["packages"][state.get("selected_release", "candidate")]["binary"], save, context,
                request_start=False)
            if not state.get("gateway_started"):
                # Old 0004 is never an automatic rollback after RecoveryQuery.
                selected = state.get("selected_release", "candidate")
                item = snapshot["packages"][selected]["binary"]
                current = driver.fence.show_unit(GATEWAY)
                if current["ActiveState"] == "active":
                    actual = gateway(item)
                else:
                    if (resume and selected == "candidate" and current["ActiveState"] == "failed"
                            and current["MainPID"] in ("", "0")):
                        fail("CANDIDATE_START_FAILED_AFTER_INTERRUPT")
                    final = catalog(plan, snapshot["environment"])
                    if final != comparable_catalog(snapshot["records"], final):
                        fail("FINAL_CATALOG_DRIFT_HELD")
                    new_exec = ("[Service]\nExecStart=\nExecStart=" + item["path"] + "\n").encode()
                    old = state.get("release_bytes", snapshot.get("original_release_bytes"))
                    if not execution_dropin.exists() or read_private(execution_dropin, 65536) != new_exec:
                        put_owned(execution_dropin, new_exec, old.encode() if old is not None else None)
                    state["release_bytes"] = new_exec.decode(); state["selected_release"] = selected; save()
                    driver.ctl("daemon-reload")
                    audit_before_gateway_start(snapshot, state, item, save, context)
                    actual = start_gateway(item)
                state["gateway_started"] = actual; state["phase"] = "GATEWAY_STARTED"; save()
            same_gateway(state["gateway_started"], gateway(snapshot["packages"][state.get("selected_release", "candidate")]["binary"]))
            try:
                actual_environment = gateway_environment(int(state["gateway_started"]["status"]["MainPID"]))
            except PermissionError:
                # The original process was compared to the exact loaded
                # EnvironmentFile before stop; those bytes and unit policy
                # remain frozen. A non-dumpable native process may hide env.
                actual_environment = configured_environment(snapshot["environment"])
            if actual_environment != snapshot["environment"]:
                fail("NEW_GATEWAY_ENVIRONMENT_CHANGED")
            for owner in snapshot["owners"]:
                check_transition_catalog(plan, snapshot, state)
                resume_owner(plan, snapshot, owner, state, save, context)
            check_transition_catalog(plan, snapshot, state)
            for owner in snapshot["owners"]:
                if owner["resident"] is None:
                    continue
                if owner["unit"] in state.get("controllers_started", {}):
                    continue
                context()
                stop_policy = home / ".config/systemd/user" / (owner["unit"] + ".d") / POLICY
                state["starting_controller"] = owner["unit"]; state["phase"] = "CONTROLLER_START_REQUESTED"; save()
                driver.ctl("start", owner["unit"])
                actual = observe_controller(owner)
                state.setdefault("controllers_started", {})[owner["unit"]] = actual
                state.pop("starting_controller", None)
                state["phase"] = "CONTROLLER_STARTED"; save()
                started = time.monotonic()
                while time.monotonic() - started < 3:
                    driver.same_driver(actual, driver.running_driver(owner["unit"], owner["instrument"]))
                    context(); time.sleep(0.2)
            context()
            native_maintenance.restore(sys.modules[__name__], home, snapshot, state)
            for name in dict.fromkeys(state.get("owned_policies", [])):
                path = Path(name)
                if path.exists():
                    remove_owned(path, HELD_STOP)
            driver.ctl("daemon-reload")
            for unit in {GATEWAY, *state["stopped_controllers"]}:
                policy_now = driver.unit_properties(unit,
                    ("RefuseManualStop", "SendSIGKILL", "TimeoutStopUSec", "Restart"), "RESTORED_SERVICE_POLICY")
                if policy_now != snapshot["service_policy"][unit]:
                    fail("ORIGINAL_STOP_PROTECTION_NOT_RESTORED")
            state.update(status="COMPLETE", phase="CONTROLLERS_RESTORED_NATIVE_HOLD_RETAINED")
            save()
        except native_maintenance.StartPending as pending:
            # Native ExecStartPre needs this same operator lock. The retained
            # start is not acceptance and must not enter owner recovery or the
            # failure fallback below. Return from this transaction to release
            # the lock; the next invocation observes the original checkpoint.
            state.update(status="HELD", error=type(pending).__name__ + ":" + str(pending),
                rollback="NOT_ATTEMPTED_NATIVE_START_PENDING")
            state.pop("rollback_error", None)
            save()
        except BaseException as error:
            state.update(status="HELD", error=type(error).__name__ + ":" + str(error))
            if state.get("native_execution") is not None:
                try:
                    native_maintenance.protect(sys.modules[__name__], home, state["native_execution"])
                except BaseException as protection_error:
                    state["native_protection_error"] = type(protection_error).__name__ + ":" + str(protection_error)
            for name in state.get("owned_policies", []):
                path = Path(name)
                if path.exists() and read_private(path, 65536) == SAFE_STOP:
                    driver.replace_exact(path, SAFE_STOP, HELD_STOP)
                    driver.ctl("daemon-reload")
            # Recovery does not stop a newly running/unknown Gateway. If it is
            # naturally down, start only the reviewed compatible fallback.
            try:
                current = driver.fence.show_unit(GATEWAY)
                job = driver.unit_properties(GATEWAY, ("Job",), "ROLLBACK_JOB")["Job"]
                native_ready = state.get("native_execution") is None or state["native_execution"].get("accepted") is True
                if native_ready and current["ActiveState"] in ("inactive", "failed") and current["MainPID"] in ("", "0") and not job:
                    context()
                    item = snapshot["packages"]["fallback"]["binary"]
                    audit_before_gateway_start(snapshot, state, item, save, context, request_start=False)
                    raw = ("[Service]\nExecStart=\nExecStart=" + item["path"] + "\n").encode()
                    old = state.get("release_bytes", snapshot.get("original_release_bytes"))
                    put_owned(execution_dropin, raw, old.encode() if old else None)
                    state["release_bytes"] = raw.decode(); save()
                    if not policy.exists():
                        state.setdefault("owned_policies", []).append(str(policy)); save()
                        put_owned(policy, HELD_STOP)
                    driver.ctl("daemon-reload")
                    if current["ActiveState"] == "failed":
                        driver.ctl("reset-failed", GATEWAY)
                    state["selected_release"] = "fallback"
                    audit_before_gateway_start(snapshot, state, item, save, context)
                    state["gateway_started"] = start_gateway(item)
                    state["rollback"] = "COMPATIBLE_GATEWAY_RUNNING_HOLD_RETAINED"
                else:
                    state["rollback"] = "NO_PROCESS_FORCED_OR_REPLACED"
            except BaseException as rollback_error:
                state["rollback"] = "INCOMPLETE_HELD"
                state["rollback_error"] = type(rollback_error).__name__ + ":" + str(rollback_error)
            save()
        result = {key: state[key] for key in ("status", "phase", "error", "rollback", "rollback_error") if key in state}
        result["selected_release"] = state.get("selected_release")
        result["gateway_binary_sha256"] = (state.get("gateway_started") or {}).get("binary", {}).get("sha256")
        if snapshot.get("audit_seal") is not None:
            progress = state.get("audit_seal", {})
            result["audit_seal"] = {"status": progress.get("status", "NOT_STARTED"),
                "records": (progress.get("receipt") or {}).get("records"),
                "active_source_sha256": ((progress.get("intent") or {}).get("source") or {}).get("sha256")}
        if state.get("native_execution") is not None:
            native = state["native_execution"]
            result["native_execution"] = {
                "phase": native.get("phase"), "accepted": native.get("accepted") is True,
                "fallback_selected": native.get("fallback_selected") is True,
                "binary_sha256": (native.get("running") or {}).get("binary", {}).get("sha256"),
                "service_epoch": ((native.get("after_audits") or [{}])[0].get("reply") or {}).get("service_epoch"),
            }
        return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--plan-sha256", required=True)
    parser.add_argument("--activate", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--native-fallback", action="store_true",
        help="on resume only, restore the former native binary after the candidate has no process or job")
    args = parser.parse_args(argv)
    if args.resume and not args.activate:
        parser.error("--resume requires --activate")
    if args.native_fallback and not (args.activate and args.resume):
        parser.error("--native-fallback requires --activate --resume")
    raw = read_private(args.plan, 1048576)
    if digest(raw) != args.plan_sha256:
        fail("GATEWAY_PLAN_CHANGED")
    plan = strict(raw)
    if args.activate:
        result = maintenance_upgrade(plan, args.plan_sha256, resume=args.resume, native_fallback=args.native_fallback)
    else:
        value = inspect(plan, Path(pwd.getpwuid(os.geteuid()).pw_dir))
        result = {"status": "PREFLIGHT_PASSED_NO_CONTROL_ACTION", "owner_count": len(value["owners"]),
            "deadline_ms": value["deadline"], "catalog_record_count": len(value["records"]),
            "candidate_sha256": value["packages"]["candidate"]["binary"]["sha256"],
            "fallback_sha256": value["packages"]["fallback"]["binary"]["sha256"],
            "native_gate_rpc_observed": False, "fresh_audit_consumed": False}
        if value.get("native_execution") is not None:
            result["native_candidate_sha256"] = value["native_execution"]["candidate"]["binary"]["sha256"]
        if value.get("audit_seal") is not None:
            result["audit_seal"] = {"path": value["audit_seal"]["path"],
                "qualification_sha256": value["audit_seal"]["qualification"]["sha256"],
                "journal_contents_read": False, "requires_stopped_gateway": True}
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] in ("COMPLETE", "ALREADY_COMPLETE", "PREFLIGHT_PASSED_NO_CONTROL_ACTION") else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(json.dumps({"status": "REFUSED", "reason": str(error)}))
        raise SystemExit(2)
