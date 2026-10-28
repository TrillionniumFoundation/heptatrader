"""Optional native phase of the existing Gateway maintenance transaction.

This module has no standalone CLI and creates no stop permit. Its caller owns
the existing operator lock, authenticated owner inventory and durable Gateway
checkpoint. Both entry producers and the Gateway must already be stopped under
that transaction. The original native marker stays engaged throughout.
"""
from __future__ import annotations

import copy
from pathlib import Path
import re


SERVICE = "heptatrader-execution.service"
SOCKETS = ("heptatrader-events.socket", "heptatrader-execution.socket")
UNITS = (SERVICE, *SOCKETS)
POLICY = "zzzz-execution-maintenance.conf"
RELEASE = "zzz-execution-release.conf"
SOCKET_STOP = b"[Unit]\nRefuseManualStop=no\n"
SOCKET_HELD = b"[Unit]\nRefuseManualStop=yes\n"


class RecoveryPending(RuntimeError):
    def __init__(self, reply, completed_at_ms):
        super().__init__(reply.get("reason_code", "NATIVE_RECOVERY_PENDING"))
        self.reply = reply
        self.completed_at_ms = completed_at_ms


def image(api, statuses, binary, source):
    status = statuses[SERVICE]
    if (status["ActiveState"] != "active" or status["SubState"] != "running"
            or not status["MainPID"].isdigit() or int(status["MainPID"]) <= 0
            or not status["InvocationID"]):
        api.fail("NATIVE_EXECUTION_NOT_RUNNING")
    process = api.driver.interactive_process(int(status["MainPID"]))
    argv = api.driver.proc_bytes(int(status["MainPID"]), "cmdline").rstrip(b"\0").split(b"\0")
    if argv != [binary["path"].encode()]:
        api.fail("NATIVE_EXECUTION_ARGV_NOT_EXACT_BINARY")
    api.pinned(binary)
    if process["exe"] != binary["path"] or process["exe_sha256"] != binary["sha256"]:
        api.fail("NATIVE_EXECUTION_RUNNING_IMAGE_MISMATCH")
    profile = {}
    for raw in api.driver.proc_bytes(int(status["MainPID"]), "environ", 262144).split(b"\0"):
        key, _, value = raw.partition(b"=")
        if key in (b"HEPTA_IB_PAPER_USER_SCOPE", b"HEPTA_IB_PAPER_CONTROL_DIRECTORY", b"HEPTA_IB_EXECUTION_MODE"):
            profile[key.decode()] = value.decode()
    expected = {"HEPTA_IB_PAPER_USER_SCOPE": "1", "HEPTA_IB_EXECUTION_MODE": "PAPER",
        "HEPTA_IB_PAPER_CONTROL_DIRECTORY": f"/run/user/{api.os.geteuid()}/heptatrader/control"}
    if profile != expected:
        api.fail("NATIVE_EXECUTION_HOLD_PROFILE_MISMATCH")
    final = api.driver.fence.show_unit(SERVICE)
    if any(final[key] != status[key] for key in ("MainPID", "InvocationID", "ActiveState")):
        api.fail("NATIVE_EXECUTION_CHANGED_DURING_INSPECTION")
    return {"status": status, "process": process, "binary": binary, "source_sha": source,
        "profile": profile, "pid_key": process["pid_key"]}


def topology(api, unit):
    names = (*api.driver.STOP_EDGES, "Triggers", "TriggeredBy", "Conflicts", "ConflictedBy")
    value = api.driver.unit_properties(unit, names, "NATIVE_EXECUTION_TOPOLOGY")
    for key in api.driver.STOP_EDGES:
        if set(value[key].split()) - set(UNITS):
            api.fail("NATIVE_EXECUTION_EXTERNAL_STOP_EDGE:" + key)
    if set((value["Conflicts"] + " " + value["ConflictedBy"]).split()) - {"shutdown.target"}:
        api.fail("NATIVE_EXECUTION_START_CONFLICT")
    if unit == SERVICE:
        if set(value["TriggeredBy"].split()) != set(SOCKETS) or value["Triggers"]:
            api.fail("NATIVE_EXECUTION_ACTIVATOR_SET_CHANGED")
    elif set(value["Triggers"].split()) != {SERVICE} or value["TriggeredBy"]:
        api.fail("NATIVE_EXECUTION_SOCKET_TARGET_CHANGED")
    return value


def prepare(api, plan, home, snapshot):
    requested = plan.get("native_execution")
    if requested is None:
        return None
    if not isinstance(requested, dict) or re.fullmatch(r"[0-9a-f]{40}", requested.get("source_sha", "")) is None:
        api.fail("NATIVE_EXECUTION_SOURCE_PIN_REQUIRED")
    binary = requested["binary"]
    release_bytes(binary)
    api.pinned(binary)
    receipt = requested["validation_receipt"]
    raw = api.read_private(receipt["path"], 1048576)
    if api.digest(raw) != receipt["sha256"]:
        api.fail("NATIVE_EXECUTION_VALIDATION_RECEIPT_CHANGED")
    validation = api.strict(raw)
    if (validation.get("status") != "ACTUAL_SDK_BUILD_AND_OFFLINE_EXITS_ACCEPTED"
            or validation.get("actual_sdk") is not True
            or validation.get("source_sha") != requested["source_sha"]
            or validation.get("binary_sha256") != binary["sha256"]
            or not isinstance(validation.get("results"), list) or len(validation["results"]) < 4
            or any(type(step.get("exit")) is not int or step["exit"] != 0 for step in validation["results"])):
        api.fail("NATIVE_EXECUTION_SDK_ACCEPTANCE_NOT_BOUND")
    held = snapshot["hold_runtime"]
    old = {"path": held["process"]["exe"], "sha256": held["process"]["exe_sha256"]}
    release_bytes(old)
    original = image(api, snapshot["statuses"], old, held["source_sha"])
    root = home / ".config/systemd/user"
    for unit in UNITS:
        if (root / (unit + ".d") / POLICY).exists():
            api.fail("EXISTING_NATIVE_MAINTENANCE_REQUIRES_RESUME")
        status = snapshot["statuses"][unit]
        if status["ActiveState"] != "active" or status["RefuseManualStop"] != "yes":
            api.fail("NATIVE_EXECUTION_GROUP_NOT_ACTIVE_AND_PROTECTED")
    path = root / (SERVICE + ".d") / RELEASE
    old_release = api.read_private(path, 65536).decode() if path.exists() else None
    if old_release is not None and old_release != release_bytes(old).decode():
        api.fail("EXISTING_NATIVE_RELEASE_OVERRIDE_CHANGED")
    return {"original": original, "candidate": copy.deepcopy(requested),
        "unit_files": {unit: api.unit_files(unit) for unit in UNITS},
        "topology": {unit: topology(api, unit) for unit in UNITS},
        "service_policy": {unit: api.driver.unit_properties(unit,
            ("RefuseManualStop", "SendSIGKILL", "TimeoutStopUSec", "Restart") if unit == SERVICE
            else ("RefuseManualStop",), "NATIVE_ORIGINAL_POLICY") for unit in UNITS},
        "original_release_bytes": old_release}


def release_bytes(binary):
    value = binary.get("path") if isinstance(binary, dict) else None
    if (not isinstance(value, str) or not Path(value).is_absolute()
            or re.search(r"""[\s%\\$'"]""", value)):
        raise RuntimeError("NATIVE_EXECUTION_UNIT_PATH_INVALID")
    return ("[Service]\nExecStart=\nExecStart=" + binary["path"] + "\n").encode()


def broker_unchanged(api, plan, snapshot):
    """Keep the original Broker process and listening socket across reconnect."""
    expected = snapshot["interactive"]
    pid = plan["interactive_broker_pid"]
    actual = api.driver.interactive_process(pid)
    if not api.equivalent(actual, expected["broker_process"]):
        api.fail("NATIVE_HANDOFF_BROKER_PROCESS_CHANGED")
    scope, shell = api.driver.interactive_broker_container(actual)
    if not api.equivalent(scope, expected["scope"]) or not api.equivalent(shell, expected.get("gnome_shell_process")):
        api.fail("NATIVE_HANDOFF_BROKER_CONTAINER_CHANGED")
    listener = expected["sockets"]["listener"]
    for _ in range(2):
        fds = api.driver.process_socket_fds(pid)
        rows = api.driver.process_tcp_sockets(pid)
        if (fds.get(listener["inode"]) != listener["fds"]
                or not any(api.equivalent(row, {k: v for k, v in listener.items() if k != "fds"}) for row in rows)):
            api.fail("NATIVE_HANDOFF_BROKER_LISTENER_CHANGED")


def verify(api, plan, home, snapshot, state, current):
    prepared = snapshot.get("native_execution")
    if prepared is None:
        return False
    native = state.get("native_execution")
    for unit in UNITS:
        if api.unit_files(unit) != prepared["unit_files"][unit] or topology(api, unit) != prepared["topology"][unit]:
            api.fail("NATIVE_EXECUTION_CONFIG_CHANGED")
    if native is None:
        actual = image(api, current, prepared["original"]["binary"], prepared["original"]["source_sha"])
        if not api.equivalent(actual["process"], prepared["original"]["process"]):
            api.fail("NATIVE_EXECUTION_ORIGINAL_PROCESS_CHANGED")
        return False
    if state.get("gateway_stopped") is not True or state.get("owners_fenced") is not True:
        api.fail("NATIVE_HANDOFF_REQUIRES_STOPPED_GATEWAY_AND_ORIGINAL_OWNER_FENCES")
    if not native.get("accepted") and (current[api.GATEWAY]["ActiveState"] != "inactive"
            or current[api.GATEWAY]["MainPID"] not in ("", "0")):
        api.fail("NATIVE_HANDOFF_GATEWAY_RESTARTED_EXTERNALLY")
    if any(owner["resident"] is not None and owner["unit"] not in state["stopped_controllers"] for owner in snapshot["owners"]):
        api.fail("NATIVE_HANDOFF_CONTROLLERS_NOT_STOPPED")
    if native.get("stop_requested") and not native.get("accepted"):
        for unit in UNITS:
            policy = home / ".config/systemd/user" / (unit + ".d") / POLICY
            opened, held = (api.SAFE_STOP, api.HELD_STOP) if unit == SERVICE else (SOCKET_STOP, SOCKET_HELD)
            raw = api.read_private(policy, 65536) if policy.exists() else None
            if str(policy) not in native["owned_policies"] or raw not in (opened, held):
                api.fail("NATIVE_PENDING_LIFECYCLE_POLICY_CHANGED")
            expected = {"RefuseManualStop": "no" if raw == opened else "yes"}
            if unit == SERVICE:
                expected.update(SendSIGKILL="no", TimeoutStopUSec="infinity", Restart="no")
            if api.driver.unit_properties(unit, tuple(expected), "NATIVE_PENDING_POLICY") != expected:
                api.fail("NATIVE_PENDING_LIFECYCLE_POLICY_NOT_EFFECTIVE")
    broker_unchanged(api, plan, snapshot)
    release = home / ".config/systemd/user" / (SERVICE + ".d") / RELEASE
    expected = native.get("release_bytes", prepared["original_release_bytes"])
    actual = api.read_private(release, 65536).decode() if release.exists() else None
    if actual != expected:
        api.fail("NATIVE_EXECUTION_RELEASE_OVERRIDE_CHANGED")
    if native.get("accepted"):
        actual = image(api, current, native["running"]["binary"], native["running"]["source_sha"])
        if not api.equivalent(actual["process"], native["running"]["process"]):
            api.fail("NATIVE_EXECUTION_REPLACED_AFTER_ACCEPTANCE")
        for unit in SOCKETS:
            if any(current[unit][key] != native["unit_statuses"][unit][key]
                    for key in ("ActiveState", "InvocationID", "PartOf")):
                api.fail("NATIVE_EXECUTION_SOCKET_CHANGED_AFTER_ACCEPTANCE")
    elif native.get("start_requested"):
        for unit, expected_socket in native.get("started_socket_statuses", {}).items():
            if current[unit]["ActiveState"] == "active" and current[unit]["InvocationID"] != expected_socket["InvocationID"]:
                api.fail("NATIVE_EXECUTION_START_SOCKET_CHANGED")
        status = current[SERVICE]
        if status["MainPID"] not in ("", "0"):
            actual = image(api, current, native["selected_binary"], native["selected_source_sha"])
            first = native.get("first_process")
            if first is not None and not api.equivalent(first, actual["process"]):
                api.fail("NATIVE_EXECUTION_START_PROCESS_CHANGED")
    else:
        old = prepared["original"]
        status = current[SERVICE]
        if status["MainPID"] not in ("", "0", old["status"]["MainPID"]):
            api.fail("NATIVE_EXECUTION_STOP_PROCESS_CHANGED")
        if status["MainPID"] == old["status"]["MainPID"] and api.driver.pid_key(status["MainPID"]) != tuple(old["pid_key"]):
            api.fail("NATIVE_EXECUTION_STOP_PID_REUSED")
        if native.get("stopped") and any(current[unit]["ActiveState"] != "inactive" for unit in UNITS):
            api.fail("NATIVE_EXECUTION_GROUP_REACTIVATED")
    return True


def audit_all(api, plan, snapshot):
    audits = []
    for owner in snapshot["owners"]:
        reply = api.rpc(plan, snapshot["environment"], owner, "owner-audit")
        if reply.get("accepted") is not True and reply.get("reason_code") in {
                "IB_RECOVERY_AUDIT_NEW_CONNECTION_EPOCH_REQUIRED",
                "IB_RECOVERY_AUDIT_BARRIER_NOT_COMPLETE", "RECOVERY_OWNER_BROKER_BARRIER_INCOMPLETE"}:
            raise RecoveryPending(reply, api.now_ms())
        api.driver.check_owner_zero(reply)
        for field, key in (("agent_id", "agent_sha256"), ("session_id", "session_sha256"),
                ("owner_account", "account_sha256"), ("owner_execution_domain", "domain_sha256")):
            if api.digest(reply[field]) != owner["record"][key]:
                api.fail("NATIVE_HANDOFF_AUDIT_OWNER_MISMATCH")
        audits.append({"reply": reply, "at_ms": api.now_ms()})
    if not audits or len({(item["reply"]["service_epoch"], item["reply"]["service_fencing_generation"],
            item["reply"]["broker_connection_epoch"]) for item in audits}) != 1:
        api.fail("NATIVE_HANDOFF_AUDITS_NOT_ONE_EPOCH")
    if any(api.now_ms() - item["at_ms"] > api.MAX_AUDIT_AGE_MS for item in audits):
        api.fail("NATIVE_HANDOFF_AUDIT_EXPIRED")
    return audits


def reconcile_release(api, home, snapshot, state):
    native = state.get("native_execution")
    prepared = snapshot.get("native_execution")
    if native is None or prepared is None or not native.get("stopped") or native.get("start_requested"):
        return
    path = home / ".config/systemd/user" / (SERVICE + ".d") / RELEASE
    actual = api.read_private(path, 65536).decode() if path.exists() else None
    expected = native.get("release_bytes", prepared["original_release_bytes"])
    candidate = native.get("selected_binary", prepared["candidate"]["binary"])
    if actual != expected and actual == release_bytes(candidate).decode():
        # Adopt only this transaction's reviewed bytes after a successful
        # rename whose subsequent fsync/reload reply may have been lost.
        native["release_bytes"] = actual


def open_stop_policies(api, home, native, save):
    root = home / ".config/systemd/user"
    for unit in UNITS:
        path = root / (unit + ".d") / POLICY
        opened, held = (api.SAFE_STOP, api.HELD_STOP) if unit == SERVICE else (SOCKET_STOP, SOCKET_HELD)
        if str(path) not in native["owned_policies"]:
            native["owned_policies"].append(str(path)); save()
        existing = api.read_private(path, 65536) if path.exists() else None
        if existing not in (None, opened, held):
            api.fail("NATIVE_EXECUTION_STOP_POLICY_CHANGED")
        api.put_owned(path, opened, existing)
    api.driver.ctl("daemon-reload")
    expected = {"RefuseManualStop": "no", "SendSIGKILL": "no", "TimeoutStopUSec": "infinity", "Restart": "no"}
    if api.driver.unit_properties(SERVICE, tuple(expected), "NATIVE_STOP_POLICY") != expected:
        api.fail("NATIVE_EXECUTION_NORMAL_STOP_POLICY_NOT_EFFECTIVE")
    for unit in SOCKETS:
        if api.driver.unit_properties(unit, ("RefuseManualStop",), "NATIVE_SOCKET_STOP_POLICY") != {"RefuseManualStop": "no"}:
            api.fail("NATIVE_SOCKET_NORMAL_STOP_POLICY_NOT_EFFECTIVE")


def resume_empty_stop(api, home, snapshot, native, save):
    """Complete a known partially submitted group stop without a live native."""
    current = {unit: api.driver.fence.show_unit(unit) for unit in UNITS}
    service = current[SERVICE]
    if service["ActiveState"] not in ("inactive", "failed") or service["MainPID"] not in ("", "0"):
        return
    former = native.get("first_process") if native.get("fallback_selected") else snapshot["native_execution"]["original"]["process"]
    if former is not None and api.driver.pid_key(former["pid_key"][0]) == tuple(former["pid_key"]):
        api.fail("NATIVE_GROUP_STOP_PROCESS_STILL_PRESENT")
    if any(api.driver.unit_properties(unit, ("Job",), "NATIVE_GROUP_STOP_JOB")["Job"] for unit in UNITS):
        return
    expected = native.get("started_socket_statuses", {}) if native.get("fallback_selected") else snapshot["statuses"]
    active = []
    for unit in SOCKETS:
        observed = current[unit]
        if observed["ActiveState"] == "inactive":
            continue
        if (observed["ActiveState"] != "active" or unit not in expected
                or observed["InvocationID"] != expected[unit]["InvocationID"]):
            api.fail("NATIVE_GROUP_STOP_SOCKET_IDENTITY_CHANGED")
        active.append(unit)
    if active or service["ActiveState"] == "failed":
        open_stop_policies(api, home, native, save)
        native["phase"] = "EMPTY_GROUP_STOP_RESUBMITTED"; save()
        api.driver.ctl("stop", "--no-block", *UNITS)


def run(api, plan, home, snapshot, state, save, context, *, fallback=False):
    prepared = snapshot.get("native_execution")
    if prepared is None:
        return
    context()
    native = state.setdefault("native_execution", {"phase": "PREPARED", "owned_policies": []})
    root = home / ".config/systemd/user"
    if native.get("accepted"):
        context(); return
    api.check_transition_catalog(plan, snapshot, state)
    if fallback:
        status = api.driver.fence.show_unit(SERVICE)
        if (not native.get("start_requested") or status["ActiveState"] not in ("failed", "inactive")
                or status["MainPID"] not in ("", "0")
                or api.driver.unit_properties(SERVICE, ("Job",), "NATIVE_FALLBACK_JOB")["Job"]):
            api.fail("NATIVE_FALLBACK_REQUIRES_FAILED_CANDIDATE_WITHOUT_PROCESS_OR_JOB")
        if native.get("first_process") is not None and api.driver.pid_key(native["first_process"]["pid_key"][0]) == tuple(native["first_process"]["pid_key"]):
            api.fail("NATIVE_FAILED_CANDIDATE_PROCESS_STILL_PRESENT")
        # Start may have completed before its reply was lost, leaving no
        # first-process/socket observation in the checkpoint. Under the
        # unchanged group start intent and config, bind those exact sockets
        # BEFORE persisting fallback selection, so a second lost stop reply
        # remains recoverable without accepting a later replacement.
        sockets = native.setdefault("started_socket_statuses", {})
        for unit in SOCKETS:
            observed = api.driver.fence.show_unit(unit)
            if observed["ActiveState"] == "inactive":
                continue
            if (observed["ActiveState"] != "active" or not observed["InvocationID"]
                    or observed["SubState"] not in ("listening", "running")
                    or unit in sockets and sockets[unit]["InvocationID"] != observed["InvocationID"]):
                api.fail("NATIVE_FALLBACK_SOCKET_IDENTITY_UNRESOLVED")
            sockets[unit] = observed
        save()
        native.update(selected_binary=prepared["original"]["binary"],
            selected_source_sha=prepared["original"]["source_sha"], fallback_selected=True,
            phase="FALLBACK_AFTER_FAILED_START", start_requested=False, stopped=False)
        native.pop("first_process", None); save()
        # No native process remains to protect. Stop only its original empty
        # listener group before restoring the reviewed former binary.
        open_stop_policies(api, home, native, save)
        api.driver.ctl("stop", "--no-block", *UNITS)
    if native.get("stop_requested") and not native.get("stopped") and not native.get("fallback_selected"):
        status = api.driver.fence.show_unit(SERVICE)
        job = api.driver.unit_properties(SERVICE, ("Job",), "NATIVE_STOP_JOB")["Job"]
        if status["ActiveState"] == "active" and not job and all(
                api.driver.fence.show_unit(unit)["ActiveState"] == "active" for unit in SOCKETS):
            # A durable stop intent without an actual job is not completion.
            # Re-observe the old image and consume a new audit before retrying.
            observed = image(api, {SERVICE: status}, prepared["original"]["binary"], prepared["original"]["source_sha"])
            if not api.equivalent(observed["process"], prepared["original"]["process"]):
                api.fail("NATIVE_STOP_INTENT_ORIGINAL_PROCESS_CHANGED")
            native["stop_requested"] = False; save()
    if native.get("stop_requested") and not native.get("stopped") and not native.get("start_requested"):
        resume_empty_stop(api, home, snapshot, native, save)
    if not native.get("stop_requested"):
        audits = audit_all(api, plan, snapshot)
        native["before_audits"] = audits; save()
        open_stop_policies(api, home, native, save)
        context()
        if any(api.now_ms() - item["at_ms"] > api.MAX_AUDIT_AGE_MS for item in audits):
            api.fail("NATIVE_HANDOFF_AUDIT_EXPIRED")
        native.update(stop_requested=True, phase="GROUP_STOP_REQUESTED"); save()
        api.driver.ctl("stop", "--no-block", *UNITS)
    if not native.get("stopped"):
        api.observe_stopped(SERVICE, prepared["original"])
        for unit in SOCKETS:
            status = api.driver.fence.show_unit(unit)
            if status["ActiveState"] != "inactive":
                api.fail("NATIVE_SOCKET_STOP_STILL_PENDING")
        native.update(stopped=True, phase="GROUP_STOPPED"); save()
        native.pop("started_socket_statuses", None)
        save()
    context()
    if not native.get("start_requested"):
        binary = native.get("selected_binary", prepared["candidate"]["binary"])
        api.pinned(binary)
        path = root / (SERVICE + ".d") / RELEASE
        expected = native.get("release_bytes", prepared["original_release_bytes"])
        raw = release_bytes(binary)
        api.put_owned(path, raw, expected.encode() if expected is not None else None)
        native.update(release_bytes=raw.decode(), selected_binary=binary,
            selected_source_sha=native.get("selected_source_sha", prepared["candidate"]["source_sha"]), phase="CANDIDATE_SELECTED"); save()
        protect(api, home, native)
        native.update(start_requested=True, phase="GROUP_START_REQUESTED"); save()
        api.driver.ctl("start", "--no-block", *SOCKETS, SERVICE)
    elif (api.driver.fence.show_unit(SERVICE)["ActiveState"] == "inactive"
            and api.driver.fence.show_unit(SERVICE)["MainPID"] in ("", "0")):
        # A normal Start is idempotent; only retry an observed absent job,
        # under the already selected exact image and retained no-kill policy.
        if not any(api.driver.unit_properties(unit, ("Job",), "NATIVE_START_JOB")["Job"] for unit in UNITS):
            for unit in SOCKETS:
                status = api.driver.fence.show_unit(unit)
                if status["ActiveState"] not in ("inactive", "active"):
                    api.fail("NATIVE_PARTIAL_START_SOCKET_STATE_UNRESOLVED")
                expected = native.get("started_socket_statuses", {}).get(unit)
                if expected is not None and status["ActiveState"] == "active" and status["InvocationID"] != expected["InvocationID"]:
                    api.fail("NATIVE_PARTIAL_START_SOCKET_IDENTITY_CHANGED")
            api.driver.ctl("start", "--no-block", *SOCKETS, SERVICE)
    deadline = api.time.monotonic() + 90
    last_error = None
    partial_start_retried = False
    while api.time.monotonic() < deadline:
        current = {unit: api.driver.fence.show_unit(unit) for unit in UNITS}
        sockets = native.setdefault("started_socket_statuses", {})
        for unit in SOCKETS:
            if current[unit]["ActiveState"] == "active" and unit not in sockets:
                sockets[unit] = current[unit]; save()
        if current[SERVICE]["ActiveState"] == "failed" and current[SERVICE]["MainPID"] in ("", "0"):
            api.fail("NATIVE_CANDIDATE_START_FAILED_HELD")
        try:
            observed = image(api, current, native["selected_binary"], native["selected_source_sha"])
            if native.get("first_process") is None:
                native["first_process"] = observed["process"]; save()
            context()
            if any(current[unit]["ActiveState"] != "active"
                    or current[unit]["SubState"] not in ("listening", "running")
                    or not current[unit]["InvocationID"] for unit in SOCKETS):
                if (not partial_start_retried
                        and all(current[unit]["ActiveState"] in ("inactive", "active") for unit in SOCKETS)
                        and not any(api.driver.unit_properties(unit, ("Job",), "NATIVE_PARTIAL_START_JOB")["Job"] for unit in UNITS)):
                    partial_start_retried = True
                    api.driver.ctl("start", "--no-block", *SOCKETS, SERVICE)
                last_error = "NATIVE_SOCKET_GROUP_NOT_READY"
                api.time.sleep(2)
                continue
            statuses = {name: api.driver.fence.show_unit(name) for name in api.driver.fence.RISK_UNITS}
            interactive = api.driver.interactive_broker_snapshot(plan["interactive_broker_pid"], api.GATEWAY, statuses)
            audits = audit_all(api, plan, snapshot)
            if audits[0]["reply"]["service_epoch"] == native["before_audits"][0]["reply"]["service_epoch"]:
                api.fail("NATIVE_CANDIDATE_SERVICE_EPOCH_NOT_NEW")
            native.update(accepted=True, phase="NEW_NATIVE_ZERO_CONFIRMED", running=observed,
                interactive=interactive, after_audits=audits, unit_statuses=current)
            save(); context(); return
        except RecoveryPending as pending:
            # A recovery audit may request a new broker epoch. Wait for the
            # original native journal's newer observations, never repeatedly
            # trigger the same recovery while it is already progressing.
            api.driver.wait_for_native_recovery(pending.reply, pending.completed_at_ms, deadline)
            last_error = str(pending)
        except (FileNotFoundError, PermissionError, RuntimeError) as error:
            reason = str(error)
            retry = (isinstance(error, (FileNotFoundError, PermissionError))
                or reason == "NATIVE_EXECUTION_NOT_RUNNING"
                or reason.startswith("INTERACTIVE_BROKER_EXECUTION_SOCKET_REQUIRED"))
            if not retry:
                raise
            last_error = reason
        api.time.sleep(2)
    api.fail("NATIVE_CANDIDATE_RECOVERY_NOT_CONFIRMED_HELD:" + str(last_error))


def protect(api, home, native):
    """A pending stop retains no-SIGKILL; never discard another writer's bytes."""
    changed = False
    for name in native.get("owned_policies", []):
        path = Path(name)
        unit = path.parent.name.removesuffix(".d")
        if unit not in UNITS or path.name != POLICY:
            api.fail("NATIVE_OWNED_POLICY_SCOPE_INVALID")
        if not path.exists():
            if native.get("stop_requested") and not native.get("accepted"):
                api.fail("NATIVE_PENDING_LIFECYCLE_POLICY_MISSING")
            continue
        raw = api.read_private(path, 65536)
        opened, held = (api.SAFE_STOP, api.HELD_STOP) if unit == SERVICE else (SOCKET_STOP, SOCKET_HELD)
        if raw == opened:
            api.driver.replace_exact(path, opened, held); changed = True
        elif raw != held:
            api.fail("NATIVE_EXECUTION_POLICY_CHANGED")
    if changed:
        api.driver.ctl("daemon-reload")


def restore(api, home, snapshot, state):
    native = state.get("native_execution")
    if native is None:
        return
    if native.get("accepted") is not True:
        api.fail("NATIVE_EXECUTION_UNACCEPTED_CANNOT_RESTORE_AUTOMATIC_RESTART")
    for name in native["owned_policies"]:
        path = Path(name)
        if path.exists():
            expected = api.HELD_STOP if path.parent.name == SERVICE + ".d" else SOCKET_HELD
            api.remove_owned(path, expected)
    api.driver.ctl("daemon-reload")
    for unit, expected in snapshot["native_execution"]["service_policy"].items():
        if api.driver.unit_properties(unit, tuple(expected), "NATIVE_POLICY_RESTORED") != expected:
            api.fail("NATIVE_ORIGINAL_STOP_POLICY_NOT_RESTORED")
