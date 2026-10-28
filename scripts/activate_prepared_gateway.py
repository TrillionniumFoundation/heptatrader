#!/usr/bin/env python3
"""One Gateway handoff under the existing native PAPER hold.

Preflight is read-only. Activation keeps both native protections, uses normal
systemd stop with forced killing disabled, and reads the final authenticated
catalog after all Gateway workers exit. No order, permit or account fence is
created. A private checkpoint permits observation/recovery after interruption.
"""
from __future__ import annotations

import argparse
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
    result = subprocess.run([str(binary), "--socket", environment["HEPTA_TOOL_SOCKET"],
        "--token-file", owner["token_path"], "--io-timeout-ms", "4000", "--call-id", "gateway-status-" + uuid.uuid4().hex,
        "call", tool, *fields],
        capture_output=True, timeout=12)
    pinned(plan["reader"])
    value = strict(result.stdout) if result.stdout else {}
    payload = value.get("payload") or {}
    if result.returncode or value.get("status") != "ok" or value.get("tool") != tool or not isinstance(payload, dict):
        fail("NATIVE_READ_NOT_CONFIRMED:" + tool)
    return payload


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
            snapshot = state["snapshot"]
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
        def save():
            atomic_json(checkpoint, state)
        controller_observations = {}
        def observe_controller(owner):
            unit = owner["unit"]
            actual = observe_controller_start(owner, controller_observations.get(unit))
            controller_observations[unit] = actual
            return actual
        def context(*, expected_interactive=None, allow_reconnect_refresh=True):
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
            expected = snapshot["interactive"] if expected_interactive is None else expected_interactive
            if not changing_native and not equivalent(interactive, expected):
                if not allow_reconnect_refresh or expected_interactive is not None:
                    fail("INTERACTIVE_BROKER_CHANGED")
                refresh_interactive_witness(interactive)

        def refresh_interactive_witness(interactive):
            # This is a resume-only recovery boundary, never permission to
            # ignore changed sockets before the original Gateway is drained.
            if (not resume or state.get("gateway_stopped") is not True or state.get("owners_fenced") is not True
                    or state.get("phase") not in ("GATEWAY_STOPPED", "GATEWAY_STARTED")
                    or state.get("stopping") or state.get("starting_controller") or state.get("controllers_started")
                    or any(any(name in progress for name in ("resume_sent", "resume_sent_at_ms", "state_synced"))
                        for progress in state.get("owner_progress", {}).values())):
                fail("INTERACTIVE_RECONNECT_REFRESH_NOT_ALLOWED")
            original_identity = interactive_process_identity(snapshot["interactive"])
            current_identity = interactive_process_identity(interactive)
            if original_identity is None or current_identity is None or not equivalent(original_identity, current_identity):
                fail("INTERACTIVE_BROKER_IDENTITY_CHANGED")
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
            if not equivalent(rows, snapshot["records"]):
                fail("RECONNECT_ORIGINAL_CATALOG_CHANGED")
            paper = {row["token_sha256"]: row for row in rows if row["paper"] is True}
            if set(paper) != {owner["token_sha256"] for owner in snapshot["owners"]}:
                fail("RECONNECT_WRITING_OWNER_SET_CHANGED")
            observations = []
            authority = None
            for owner in snapshot["owners"]:
                row = paper[owner["token_sha256"]]
                if (not equivalent(row, owner["record"]) or row["recovery_only"] is not True
                        or row["generation"] != owner["state_generation"]
                        or row["recovery_command_sha256"] != digest(owner["command"])):
                    fail("RECONNECT_ORIGINAL_RECOVERY_SCOPE_CHANGED")
                audit = fresh_audit(plan, snapshot["environment"], owner)
                original_command(plan, snapshot["environment"], owner, audit)
                reply = audit["reply"]
                current_authority = (reply["service_epoch"], reply["service_fencing_generation"], reply["broker_connection_epoch"])
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
                if resident and driver.pid_key(resident["status"]["MainPID"]) == tuple(resident["pid_key"]):
                    fail("RECONNECT_CONTROLLERS_NOT_NATURALLY_STOPPED")
            snapshot["interactive"] = interactive
            state["interactive_refresh"] = {"at_ms": now_ms(), "gateway_pid_key": observed_gateway["pid_key"],
                "gateway_binary_sha256": binary["sha256"], "witness_sha256": digest(json.dumps(interactive, sort_keys=True)),
                "audits": observations}
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
            context()
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
            context()
            native_maintenance.run(sys.modules[__name__], plan, home, snapshot, state, save, context,
                fallback=native_fallback)
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
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] in ("COMPLETE", "ALREADY_COMPLETE", "PREFLIGHT_PASSED_NO_CONTROL_ACTION") else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(json.dumps({"status": "REFUSED", "reason": str(error)}))
        raise SystemExit(2)
