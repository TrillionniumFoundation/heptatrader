"""Read current native evidence for a disabled, fenced crypto owner.

This module neither owns a lease nor changes a native fence. Its caller holds
the existing operator-maintenance lock and repeats the proof after releasing
the account's startup hold. Supervisor operations remain independently owned;
the catalog and current native fence are observations, not a new stop permit.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import pwd
import re
import stat
import subprocess

CRYPTO_UNIT = "heptatrader-crypto-driver.service"
GATEWAY_UNIT = "heptatrader-gateway.service"
EXECUTION_UNIT = "heptatrader-execution.service"
PROC_ROOT = Path("/proc")
POLICY_FIELDS = ("LoadState", "ActiveState", "SubState", "UnitFileState", "MainPID",
                 "InvocationID", "RefuseManualStop", "ExecStart", "FragmentPath", "DropInPaths", "Job")
AUDIT_ENV = frozenset({
    "HEPTA_IB_PAPER_USER_SCOPE", "HEPTA_EXECUTION_REMOTE_MODE",
    "HEPTA_TOOL_SUPERVISOR_LEASE_STORE", "HEPTA_TOOL_SUPERVISOR_LEASE_KEY_FILE",
    "HEPTA_TOOL_SUPERVISOR_AUDIT_JOURNAL", "HEPTA_TOOL_SUPERVISOR_UID", "HEPTA_TOOL_AGENT_UID",
    "HEPTA_TOOL_AGENT_ID", "HEPTA_TOOL_CRYPTO_SESSION_ID", "HEPTA_TOOL_ACCOUNT",
    "HEPTA_TOOL_SESSION_TEMPLATES", "HEPTA_TOOL_CONTRACT_BINDINGS",
    "HEPTA_EXECUTION_DOMAIN_ID", "HEPTA_EXECUTION_SOCKET", "HEPTA_EXECUTION_EVENT_SOCKET",
    "HEPTA_EXECUTION_SERVICE_UID", "HEPTA_EXECUTION_IO_TIMEOUT_MS",
    "HEPTA_EXECUTION_RESPONSE_TIMEOUT_MS", "HEPTA_EXECUTION_MAX_RESPONSE_BYTES",
    "HEPTA_TOOL_ALLOW_TRADE", "HEPTA_EXECUTION_EXTERNAL_P1_CANARY_LMT_DAY", "CREDENTIALS_DIRECTORY",
})


class InactiveOwnerError(RuntimeError):
    pass


def require(condition, reason):
    if not condition:
        raise InactiveOwnerError(reason)


def digest(value):
    if isinstance(value, str):
        value = value.encode()
    return hashlib.sha256(value).hexdigest()


def strict(raw):
    def pairs(items):
        value = {}
        for key, item in items:
            require(key not in value, "INACTIVE_OWNER_DUPLICATE_JSON_FIELD")
            value[key] = item
        return value
    def bad_constant(_value):
        raise InactiveOwnerError("INACTIVE_OWNER_NONFINITE_JSON")
    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=bad_constant)
    except (ValueError, UnicodeError) as error:
        raise InactiveOwnerError("INACTIVE_OWNER_BAD_JSON") from error


def identity(value):
    return (value.st_dev, value.st_ino, value.st_mode, value.st_uid, value.st_gid,
            value.st_nlink, value.st_size, value.st_mtime_ns, value.st_ctime_ns)


def regular_file(path, *, content=False, limit=2 * 1024 * 1024, private=False, absent=False):
    path = Path(path)
    try:
        before = path.lstat()
    except FileNotFoundError:
        require(absent, "INACTIVE_OWNER_FILE_REQUIRED")
        return {"path": str(path), "absent": True}, None
    require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1
            and before.st_uid == os.geteuid() and not before.st_mode & 0o022
            and (not private or stat.S_IMODE(before.st_mode) in (0o400, 0o600))
            and before.st_size <= limit, "INACTIVE_OWNER_FILE_UNSAFE")
    result = {"path": str(path), "identity": list(identity(before))}
    raw = None
    if content:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC)
        with os.fdopen(fd, "rb") as stream:
            require(identity(os.fstat(stream.fileno())) == identity(before), "INACTIVE_OWNER_FILE_CHANGED")
            raw = stream.read(limit + 1)
            require(len(raw) == before.st_size and identity(os.fstat(stream.fileno())) == identity(before)
                    and identity(path.lstat()) == identity(before), "INACTIVE_OWNER_FILE_CHANGED")
        result["sha256"] = digest(raw)
    return result, raw


def crypto_policy(command):
    raw = command(["systemctl", "--user", "show", CRYPTO_UNIT, "--no-pager",
                   "--property=" + ",".join(POLICY_FIELDS)])
    values = {}
    for line in raw.splitlines():
        key, separator, value = line.partition("=")
        require(separator and key in POLICY_FIELDS and key not in values, "INACTIVE_OWNER_UNIT_READBACK_INVALID")
        values[key] = value
    require(set(values) == set(POLICY_FIELDS), "INACTIVE_OWNER_UNIT_READBACK_INCOMPLETE")
    if values["ActiveState"] == "active":
        # The ordinary owner-token path is unchanged for a running strategy.
        return None
    require(values["LoadState"] == "loaded" and values["ActiveState"] == "inactive"
            and values["SubState"] == "dead" and values["MainPID"] == "0"
            and values["UnitFileState"] == "disabled" and values["RefuseManualStop"] == "yes"
            and values["Job"] == "", "INACTIVE_OWNER_DRIVER_NOT_FROZEN_DISABLED")
    return values


def process(pid):
    require(isinstance(pid, str) and pid.isdecimal() and int(pid) > 0, "INACTIVE_OWNER_PROCESS_ID_INVALID")
    root = PROC_ROOT / pid
    require(root.stat().st_uid == os.geteuid(), "INACTIVE_OWNER_PROCESS_UID_MISMATCH")
    before = (root / "stat").read_text().rsplit(")", 1)[1].split()[19]
    require(before.isdecimal() and int(before) > 0, "INACTIVE_OWNER_PROCESS_START_INVALID")
    executable = (root / "exe").resolve(strict=True)
    fd = os.open(root / "environ", os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    with os.fdopen(fd, "rb") as stream:
        raw = stream.read(262145)
    require(len(raw) <= 262144, "INACTIVE_OWNER_PROCESS_ENVIRONMENT_BOUND")
    environment = {}
    for item in raw.split(b"\0"):
        if not item:
            continue
        key, separator, value = item.partition(b"=")
        try:
            name, text = key.decode(), value.decode()
        except UnicodeError as error:
            raise InactiveOwnerError("INACTIVE_OWNER_PROCESS_ENVIRONMENT_ENCODING") from error
        require(separator and name and name not in environment, "INACTIVE_OWNER_PROCESS_ENVIRONMENT_INVALID")
        environment[name] = text
    require((root / "stat").read_text().rsplit(")", 1)[1].split()[19] == before
            and (root / "exe").resolve(strict=True) == executable, "INACTIVE_OWNER_PROCESS_CHANGED")
    return {"pid": int(pid), "start_ticks": before, "exe": str(executable),
            "image_identity": list(identity((root / "exe").stat())), "environment_sha256": digest(raw)}, environment


def definitions(policy):
    paths = [policy["FragmentPath"], *policy["DropInPaths"].split()]
    require(paths[0] and len(paths) <= 32 and len(paths) == len(set(paths)), "INACTIVE_OWNER_UNIT_DEFINITION_INVALID")
    result = []
    for value in paths:
        path = Path(value)
        require(path.is_absolute(), "INACTIVE_OWNER_UNIT_DEFINITION_INVALID")
        original = path.lstat()
        resolved = path.resolve(strict=True)
        record, _ = regular_file(resolved, content=True, limit=262144)
        require(identity(path.lstat()) == identity(original), "INACTIVE_OWNER_UNIT_DEFINITION_CHANGED")
        result.append({"path": value, "path_identity": list(identity(original)), "resolved": record})
    return result


def context(control, services, clients, policy, command):
    require(policy is not None, "INACTIVE_OWNER_DRIVER_NO_LONGER_DISABLED")
    home = Path(pwd.getpwuid(os.geteuid()).pw_dir)
    gateway, ge = process(services[GATEWAY_UNIT]["MainPID"])
    execution, ee = process(services[EXECUTION_UNIT]["MainPID"])
    require(ge.get("HEPTA_IB_PAPER_USER_SCOPE") == "1" and ge.get("HEPTA_EXECUTION_REMOTE_MODE") == "PAPER"
            and ee.get("HEPTA_IB_PAPER_USER_SCOPE") == "1" and ee.get("HEPTA_IB_EXECUTION_MODE") == "PAPER",
            "INACTIVE_OWNER_USER_PAPER_SCOPE_REQUIRED")
    scope = {"agent_id": ge.get("HEPTA_TOOL_AGENT_ID"), "session_id": ge.get("HEPTA_TOOL_CRYPTO_SESSION_ID"),
             "account": ge.get("HEPTA_TOOL_ACCOUNT"), "execution_domain": ge.get("HEPTA_EXECUTION_DOMAIN_ID", "PAPER"),
             "instrument": "BTC.USD"}
    require(all(isinstance(value, str) and value for value in scope.values())
            and scope["execution_domain"] == "PAPER"
            and scope["agent_id"] == ee.get("HEPTA_IB_PAPER_CRYPTO_AGENT_ID")
            and scope["session_id"] == ee.get("HEPTA_IB_PAPER_CRYPTO_SESSION_ID")
            and scope["account"] == ee.get("HEPTA_IB_PAPER_ACCOUNT"), "INACTIVE_OWNER_CONFIGURED_SCOPE_MISMATCH")
    for name in ("HEPTA_TOOL_SUPERVISOR_UID", "HEPTA_TOOL_AGENT_UID", "HEPTA_EXECUTION_SERVICE_UID"):
        require(ge.get(name) == str(os.geteuid()), "INACTIVE_OWNER_UID_SCOPE_MISMATCH")
    require(ge.get("HEPTA_TOOL_SESSION_TEMPLATES") == "watch,paper"
            and ge.get("HEPTA_TOOL_CONTRACT_BINDINGS") ==
                "BTC.USD|BTC|CRYPTO|PAXOS|USD;EUR.USD|EUR|CASH|IDEALPRO|USD",
            "INACTIVE_OWNER_CONFIGURED_CONTRACT_MISMATCH")
    runtime = clients["inactive_owner_runtime"]
    require(execution["exe"] == runtime["execution_binary"]["path"], "INACTIVE_OWNER_EXECUTION_IMAGE_MISMATCH")
    receipt, _ = regular_file(runtime["build_receipt"]["path"], content=True)
    require(receipt["sha256"] == runtime["build_receipt"]["sha256"], "INACTIVE_OWNER_BUILD_RECEIPT_CHANGED")
    state_file, state_raw = regular_file(home / ".local/state/heptatrader/crypto-driver/state.json", content=True, private=True)
    state = strict(state_raw)
    require(isinstance(state, dict) and type(state.get("position")) in (int, float)
            and math.isfinite(state["position"]) and state["position"] == 0 and state.get("pending") is None
            and state.get("lease_uncertain") is False, "INACTIVE_OWNER_LOCAL_STATE_UNSETTLED")
    token_file, _ = regular_file(home / ".config/heptatrader/crypto.token", limit=1024, private=True, absent=True)
    binding = {"services": services, "gateway_process": gateway, "execution_process": execution,
               "unit_policy_sha256": digest(json.dumps(policy, sort_keys=True)), "unit_files": definitions(policy),
               "driver_state": state_file, "old_token_metadata": token_file, "build_receipt": receipt,
               "runtime_contract_sha256": digest(json.dumps(runtime, sort_keys=True)),
               "operator_clients_sha256": digest(json.dumps(clients, sort_keys=True)),
               "scope_sha256": {key: digest(value) for key, value in scope.items()}}
    # Never inherit a caller-supplied HEPTA_*, loader variable or arbitrary
    # credential. The CLI consumes the running Gateway's selected config.
    environment = {"PATH": os.defpath, "HOME": str(home), "XDG_RUNTIME_DIR": str(control.parent.parent),
                   "LANG": "C.UTF-8", "PYTHONNOUSERSITE": "1"}
    environment.update({key: ge[key] for key in AUDIT_ENV if key in ge})
    # The native profile uses the same PAPER default, but explicit forwarding
    # makes the checked effective domain independent of caller environment.
    environment["HEPTA_EXECUTION_DOMAIN_ID"] = scope["execution_domain"]
    require(crypto_policy(command) == policy, "INACTIVE_OWNER_UNIT_CHANGED")
    return binding, scope, environment


def run_native(arguments, environment):
    try:
        result = subprocess.run(arguments, capture_output=True, env=environment, timeout=45)
    except (OSError, subprocess.SubprocessError) as error:
        raise InactiveOwnerError("INACTIVE_OWNER_NATIVE_TRANSPORT_" + type(error).__name__) from error
    require(len(result.stdout) <= 8 * 1024 * 1024, "INACTIVE_OWNER_NATIVE_RESPONSE_BOUND")
    value = strict(result.stdout)
    if result.returncode != 0:
        code = value.get("reason_code") if isinstance(value, dict) else None
        suffix = code if isinstance(code, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{1,100}(?::[a-z][a-z0-9_]{0,80})?", code) else "UNCONFIRMED"
        raise InactiveOwnerError("INACTIVE_OWNER_NATIVE_PROOF_FAILED:" + suffix)
    return value


def prove(control, services, clients, phase, command, *, previous=None):
    require(phase in ("held", "released") and (previous is not None) == (phase == "released"),
            "INACTIVE_OWNER_PROOF_PHASE_INVALID")
    policy = crypto_policy(command)
    binding, scope, environment = context(control, services, clients, policy, command)
    if previous is not None:
        require(binding == previous["binding"], "INACTIVE_OWNER_BINDING_CHANGED_AFTER_RELEASE")
    root = Path(__file__).resolve().parent
    from hepta_verified_loader import load_verified_module
    reader = load_verified_module("hepta_inactive_owner_pinned_reader", root / "hepta_native_reader.py",
                                  clients["files"]["hepta_native_reader.py"])
    execution = binding["execution_process"]
    with reader.verified_binary(clients["inactive_owner_runtime"]["execution_binary"]) as fd:
        require(list(identity(os.fstat(fd))) == execution["image_identity"], "INACTIVE_OWNER_RUNNING_IMAGE_CHANGED")
        value = run_native(["/usr/bin/python3", "-B", str(root / "hepta_operator_clients.py"), "hepta-sessionctl",
                            "inactive-owner-audit", "--owner", "crypto", "--phase", phase,
                            "--execution-pid", str(execution["pid"]), "--execution-start-ticks", execution["start_ticks"]], environment)
        require(list(identity(os.fstat(fd))) == execution["image_identity"], "INACTIVE_OWNER_RUNNING_IMAGE_CHANGED")
    require(isinstance(value, dict) and value.get("schema") == "hepta_inactive_owner_audit_v1"
            and value.get("accepted") is True and value.get("state") == "INACTIVE_FENCED_ZERO_NO_LEASE"
            and value.get("phase") == phase and value.get("configured_owner") == scope
            and value.get("preserves_owner_fence") is True, "INACTIVE_OWNER_NATIVE_PROOF_INVALID")
    for key in ("control_mutations", "lease_mutations", "orders_sent"):
        require(type(value.get(key)) is int and value[key] == 0, "INACTIVE_OWNER_UNEXPECTED_MUTATION")
    catalog = value.get("catalog", {})
    require(isinstance(catalog, dict) and type(catalog.get("owner_records")) is int and catalog["owner_records"] == 0
            and isinstance(catalog.get("before_sha256"), str) and re.fullmatch(r"[0-9a-f]{64}", catalog["before_sha256"])
            and catalog.get("after_sha256") == catalog["before_sha256"], "INACTIVE_OWNER_CATALOG_NOT_STABLE_ABSENT")
    native_execution = value.get("execution", {})
    require(isinstance(native_execution, dict) and type(native_execution.get("pid")) is int and native_execution["pid"] == execution["pid"]
            and native_execution.get("start_ticks") == execution["start_ticks"]
            and isinstance(native_execution.get("service_epoch"), str) and native_execution["service_epoch"]
            and type(native_execution.get("service_fencing_generation")) is int
            and native_execution["service_fencing_generation"] > 0, "INACTIVE_OWNER_NATIVE_IDENTITY_INVALID")
    if previous is not None:
        require(native_execution == previous["native"]["execution"], "INACTIVE_OWNER_NATIVE_IDENTITY_CHANGED")
    require(value.get("audit_performed") is (phase == "held"), "INACTIVE_OWNER_AUDIT_PHASE_INVALID")
    reads = value.get("reads", {})
    require(isinstance(reads, dict), "INACTIVE_OWNER_NATIVE_READS_MISSING")
    for name in ("health_before", "health_after"):
        health = reads.get(name, {})
        require(isinstance(health, dict) and health.get("owner_fenced") is True and health.get("mutation_blocked") is False
                and health.get("native_new_risk_guard_blocked") is (phase == "held"), "INACTIVE_OWNER_NATIVE_FENCE_CHANGED")
    after, after_scope, _ = context(control, services, clients, crypto_policy(command), command)
    require(after == binding and after_scope == scope, "INACTIVE_OWNER_BINDING_CHANGED_DURING_PROOF")
    return {"state": "INACTIVE_FENCED_ZERO_NO_LEASE", "binding": binding, "native": value}
