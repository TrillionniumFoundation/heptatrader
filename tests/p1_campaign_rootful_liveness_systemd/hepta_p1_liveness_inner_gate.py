#!/usr/bin/env python3
"""Verify real systemd watchdog/restart/fail-closed cleanup in one container."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any, Callable


SCHEMA = "hepta.p1-safety-soak-campaign-rootful-liveness-inner.v1"
MARKER = "HEPTA_P1_CAMPAIGN_ROOTFUL_LIVENESS_RESULT="
RUN_ID = re.compile(r"[0-9a-f]{32}")
UNITS = (
    "hepta-p1-liveness-watchdog.service",
    "hepta-p1-liveness-worker.service",
    "hepta-p1-liveness-coordinator.service",
)
TARGET = "hepta-p1-campaign-rootful-liveness.target"
INNER_EXECUTABLE = Path("/usr/local/libexec/hepta_p1_liveness_inner_gate.py")
STATE = Path("/var/lib/hepta-p1-liveness")
CONTROL = Path("/run/hepta-p1-liveness")
BOUNDARY = {
    "broker_connectors": 0,
    "broker_connections": 0,
    "broker_protocol_messages": 0,
    "paper_orders": 0,
    "paper_test_admission_candidate": False,
    "paper_authorized": False,
    "live_authorized": False,
    "mutation_authorized": False,
    "direct_broker_access": False,
    "order_submission_authorized": False,
    "host_bind_mounts": 0,
    "host_systemd_units_touched": 0,
    "host_network_rules_touched": 0,
    "real_credentials": 0,
}
EXPECTED_CHECKS = frozenset({
    "real_systemd_pid1_private_cgroup",
    "production_unit_inputs_present_and_hardened",
    "effective_watchdog_timeout_observed",
    "watchdog_restart_changed_process_identity",
    "watchdog_recovered_and_remained_healthy",
    "worker_failure_durable_before_exit",
    "worker_restart_refused_terminal_replay",
    "coordinator_observed_worker_terminal",
    "failed_closed_chain_forbids_catch_up",
    "effective_unit_hardening_exact",
    "target_stop_cleaned_all_owned_units",
    "owned_process_residue_absent",
    "container_tcp_udp_surface_empty",
    "all_authority_and_order_flags_false",
})
FALSE_AUTHORITY_FIELDS = (
    "paper_test_admission_candidate", "paper_authorized",
    "live_authorized", "mutation_authorized", "direct_broker_access",
    "order_submission_authorized",
)
ZERO_EXPOSURE_FIELDS = (
    "broker_connectors", "broker_connections", "broker_protocol_messages",
    "paper_orders", "host_bind_mounts", "host_systemd_units_touched",
    "host_network_rules_touched", "real_credentials",
)


class GateError(RuntimeError):
    pass


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise GateError(reason)


def canonical(value: object) -> bytes:
    return (json.dumps(
        value, ensure_ascii=True, allow_nan=False, sort_keys=True,
        separators=(",", ":")) + "\n").encode("ascii")


def digest(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def command(arguments: list[str], timeout: int = 30,
            check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        arguments, check=False, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        encoding="utf-8", errors="replace", timeout=timeout,
        env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C",
             "LC_ALL": "C"}, close_fds=True)
    require(len(result.stdout.encode("utf-8")) <= 1024 * 1024,
            "command output too large")
    if check:
        require(result.returncode == 0,
                "command failed: " + result.stdout[-1000:])
    return result


def wait_for(predicate: Callable[[], bool], reason: str,
             seconds: float = 20.0) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.1)
    raise GateError(reason)


def unit(unit_name: str) -> dict[str, str]:
    names = (
        "LoadState", "ActiveState", "SubState", "MainPID", "InvocationID",
        "NRestarts", "Result", "ExecMainStatus", "WatchdogUSec", "Restart",
        "RestartUSec", "Type", "NotifyAccess", "NoNewPrivileges",
        "PrivateDevices", "ProtectSystem", "ProtectHome", "ProtectClock",
        "RestrictNamespaces", "MemoryDenyWriteExecute",
        "CapabilityBoundingSet", "AmbientCapabilities",
        "RestrictAddressFamilies", "IPAddressDeny", "FragmentPath",
    )
    raw = command([
        "/usr/bin/systemctl", "show", "--no-pager",
        *(f"--property={name}" for name in names), unit_name,
    ]).stdout
    result: dict[str, str] = {}
    for line in raw.splitlines():
        key, separator, value = line.partition("=")
        require(separator == "=" and key in names and key not in result,
                "systemd show malformed")
        result[key] = value
    require(set(result) == set(names), "systemd show fields incomplete")
    return result


def read_document(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    descriptor = os.open(
        path, os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0))
    try:
        before = os.fstat(descriptor)
        raw = os.read(descriptor, 1024 * 1024 + 1)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    require(
        before.st_uid == 0 and before.st_gid == 0 and
        before.st_nlink == 1 and (before.st_mode & 0o777) == 0o600 and
        0 < len(raw) <= 1024 * 1024 and
        (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns,
         before.st_ctime_ns) ==
        (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns,
         after.st_ctime_ns), "durable fixture document metadata drift")
    try:
        value = json.loads(raw.decode("ascii"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise GateError("durable fixture JSON invalid") from error
    require(isinstance(value, dict) and canonical(value) == raw,
            "durable fixture JSON not canonical")
    body = dict(value)
    claimed = body.pop("body_sha256", None)
    require(claimed == digest(canonical(body)), "fixture body digest invalid")
    return value, {
        "path": str(path), "file_sha256": digest(raw),
        "body_sha256": claimed, "device": before.st_dev,
        "inode": before.st_ino, "mode": "0600", "uid": 0, "gid": 0,
    }


def inner_executable() -> dict[str, Any]:
    path = Path(__file__).resolve(strict=True)
    require(path == INNER_EXECUTABLE, "inner executable path drift")
    descriptor = os.open(
        path, os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0))
    try:
        before = os.fstat(descriptor)
        raw = os.read(descriptor, 1024 * 1024 + 1)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    require(
        before.st_uid == 0 and before.st_gid == 0 and before.st_nlink == 1 and
        (before.st_mode & 0o777) == 0o755 and 0 < len(raw) <= 1024 * 1024 and
        (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns,
         before.st_ctime_ns) ==
        (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns,
         after.st_ctime_ns), "inner executable metadata drift")
    return {
        "path": str(path), "file_sha256": digest(raw), "mode": "0755",
        "uid": 0, "gid": 0,
    }


def publish_trigger(path: Path) -> None:
    descriptor = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC |
        getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        os.write(descriptor, b"DISPOSABLE_ROOTFUL_INJECTION\n")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(parent)
    finally:
        os.close(parent)


def network_empty() -> bool:
    for path in ("tcp", "tcp6", "udp", "udp6"):
        lines = Path("/proc/net/" + path).read_text(
            encoding="ascii").splitlines()
        if len(lines) != 1:
            return False
    return True


def process_residue_absent() -> bool:
    needle = b"hepta-p1-liveness-daemon"
    for child in Path("/proc").iterdir():
        if not child.name.isdigit() or int(child.name) == os.getpid():
            continue
        try:
            if needle in (child / "cmdline").read_bytes():
                return False
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
    return True


def publish_raw(path: Path, raw: bytes) -> None:
    descriptor = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC |
        getattr(os, "O_NOFOLLOW", 0), 0o644)
    try:
        os.fchmod(descriptor, 0o644)
        view = memoryview(raw)
        while view:
            written = os.write(descriptor, view)
            require(written > 0, "short production unit write")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def stable_unit_input(path: Path) -> bytes:
    descriptor = os.open(
        path, os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0))
    try:
        before = os.fstat(descriptor)
        raw = os.read(descriptor, 1024 * 1024 + 1)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    require(
        before.st_uid == 0 and before.st_gid == 0 and before.st_nlink == 1 and
        (before.st_mode & 0o777) == 0o644 and 0 < len(raw) <= 1024 * 1024 and
        (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns,
         before.st_ctime_ns) ==
        (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns,
         after.st_ctime_ns), "production unit input metadata drift")
    return raw


def production_unit_inventory() -> dict[str, Any]:
    names = (
        "hepta-p1-safety-soak-campaign@.service",
        "hepta-p1-safety-soak-observer-worker@.service",
        "hepta-p1-safety-soak-recorder-worker@.service",
        "hepta-p1-safety-soak@.target",
    )
    source_root = Path("/opt/hepta-inputs/systemd")
    loaded_root = Path("/run/systemd/system")
    records: list[dict[str, Any]] = []
    for name in names:
        source = source_root / name
        loaded = loaded_root / name
        raw = stable_unit_input(source)
        publish_raw(loaded, raw)
        records.append({
            "name": name, "source_path": str(source),
            "loaded_path": str(loaded), "file_sha256": digest(raw),
        })
    parent = os.open(loaded_root, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(parent)
    finally:
        os.close(parent)
    verify_argv = ["/usr/bin/systemd-analyze", "verify", *(
        str(loaded_root / name) for name in names)]
    verified = command(verify_argv, timeout=30)
    command(["/usr/bin/systemctl", "daemon-reload"], timeout=20)
    instances = {
        names[0]: "hepta-p1-safety-soak-campaign@liveness.service",
        names[1]: "hepta-p1-safety-soak-observer-worker@liveness.service",
        names[2]: "hepta-p1-safety-soak-recorder-worker@liveness.service",
        names[3]: "hepta-p1-safety-soak@liveness.target",
    }
    expected_caps = {
        names[0]: set(),
        names[1]: {
            "cap_dac_read_search", "cap_sys_ptrace", "cap_net_admin"},
        names[2]: {"cap_dac_read_search"},
    }
    expected_families = {
        names[0]: "AF_UNIX", names[1]: "AF_UNIX AF_NETLINK",
        names[2]: "AF_UNIX",
    }
    expected_watchdog = {names[0]: "45s", names[1]: "30s", names[2]: "30s"}
    for record in records:
        name = record["name"]
        instance = instances[name]
        if name.endswith(".service"):
            effective = unit(instance)
            require(
                effective["LoadState"] == "loaded" and
                effective["ActiveState"] == "inactive" and
                effective["SubState"] == "dead" and
                effective["MainPID"] == "0" and
                effective["FragmentPath"] == record["loaded_path"] and
                effective["Type"] == "notify" and
                effective["NotifyAccess"] == "main" and
                effective["Restart"] == "on-failure" and
                effective["WatchdogUSec"] == expected_watchdog[name] and
                effective["NoNewPrivileges"] == "yes" and
                effective["ProtectSystem"] == "strict" and
                effective["ProtectHome"] == "yes" and
                effective["ProtectClock"] == "yes" and
                effective["RestrictNamespaces"] == "yes" and
                effective["MemoryDenyWriteExecute"] == "yes" and
                set(effective["CapabilityBoundingSet"].split()) ==
                    expected_caps[name] and
                effective["AmbientCapabilities"] == "" and
                effective["RestrictAddressFamilies"] ==
                    expected_families[name] and
                set(effective["IPAddressDeny"].split()) ==
                    {"0.0.0.0/0", "::/0"},
                "effective production service contract mismatch")
            record["effective"] = effective
        else:
            raw = command([
                "/usr/bin/systemctl", "show", "--no-pager",
                "--property=LoadState", "--property=ActiveState",
                "--property=SubState", "--property=FragmentPath",
                "--property=StopWhenUnneeded", instance,
            ]).stdout
            effective: dict[str, str] = {}
            for line in raw.splitlines():
                key, separator, content = line.partition("=")
                require(separator == "=" and key not in effective,
                        "effective target output malformed")
                effective[key] = content
            require(
                effective == {
                    "LoadState": "loaded", "ActiveState": "inactive",
                    "SubState": "dead", "FragmentPath": record["loaded_path"],
                    "StopWhenUnneeded": "yes",
                }, "effective production target contract mismatch")
            record["effective"] = effective
    return {
        "systemd_analyze_verify": {
            "argv": verify_argv, "returncode": verified.returncode,
            "stdout_sha256": digest(verified.stdout.encode("utf-8")),
        },
        "units": records,
    }


def effective_contract(value: dict[str, str]) -> bool:
    return (
        value["LoadState"] == "loaded" and value["Type"] == "notify" and
        value["NotifyAccess"] == "main" and value["WatchdogUSec"] == "2s" and
        value["NoNewPrivileges"] == "yes" and
        value["PrivateDevices"] == "yes" and
        value["ProtectSystem"] == "strict" and
        value["ProtectHome"] == "yes" and
        value["ProtectClock"] == "yes" and
        value["RestrictNamespaces"] == "yes" and
        value["MemoryDenyWriteExecute"] == "yes" and
        value["CapabilityBoundingSet"] == "" and
        value["AmbientCapabilities"] == "" and
        value["RestrictAddressFamilies"] == "AF_UNIX" and
        set(value["IPAddressDeny"].split()) == {"0.0.0.0/0", "::/0"})


def run() -> dict[str, Any]:
    run_id = os.environ.get("HEPTA_P1_LIVENESS_RUN_ID", "")
    require(RUN_ID.fullmatch(run_id) is not None, "run id invalid")
    require(os.geteuid() == 0 and os.getpid() != 1, "inner identity invalid")
    require(Path("/proc/1/comm").read_text(encoding="ascii").strip() ==
            "systemd", "PID 1 is not systemd")
    boot_id = Path("/proc/sys/kernel/random/boot_id").read_text(
        encoding="ascii").strip()
    pid1_cgroup = Path("/proc/1/cgroup").read_text(
        encoding="ascii").strip()
    require(pid1_cgroup == "0::/", "private cgroup namespace absent")
    units = production_unit_inventory()

    command(["/usr/bin/systemctl", "start", *UNITS], timeout=20)
    initial = {name: unit(name) for name in UNITS}
    require(all(effective_contract(value) for value in initial.values()),
            "effective fixture hardening mismatch")
    first_path = STATE / "watchdog-first.json"
    recovered_path = STATE / "watchdog-recovered.json"
    wait_for(lambda: first_path.exists(), "watchdog first identity missing")
    wait_for(lambda: recovered_path.exists(),
             "effective watchdog did not restart fixture", 15)
    first, first_ref = read_document(first_path)
    recovered, recovered_ref = read_document(recovered_path)
    watchdog_state = unit(UNITS[0])
    require(
        watchdog_state["ActiveState"] == "active" and
        watchdog_state["SubState"] == "running" and
        int(watchdog_state["NRestarts"]) >= 1 and
        first["pid"] != recovered["pid"] and
        first["invocation_id"] != recovered["invocation_id"] and
        watchdog_state["InvocationID"] == recovered["invocation_id"],
        "watchdog restart identity evidence invalid")

    trigger = CONTROL / "trigger-worker-failure"
    publish_trigger(trigger)
    worker_journal = STATE / "worker-journal" / "00000000.json"
    coordinator_journal = STATE / "coordinator-journal" / "00000000.json"
    wait_for(lambda: worker_journal.exists(), "worker terminal missing")
    worker_at_publish = unit(UNITS[1])
    require(
        worker_at_publish["ActiveState"] == "active" and
        worker_at_publish["SubState"] == "running" and
        worker_at_publish["InvocationID"] == initial[UNITS[1]]["InvocationID"],
        "worker exited before durable terminal observation")
    publish_trigger(CONTROL / "ack-worker-terminal-observed")
    wait_for(lambda: coordinator_journal.exists(), "coordinator terminal missing")
    worker_terminal, worker_ref = read_document(worker_journal)
    coordinator_terminal, coordinator_ref = read_document(coordinator_journal)
    worker_raw_before = worker_journal.read_bytes()
    wait_for(lambda: unit(UNITS[1])["ActiveState"] == "failed",
             "worker restart did not refuse durable terminal", 15)
    worker_failed = unit(UNITS[1])
    time.sleep(0.5)
    require(worker_journal.read_bytes() == worker_raw_before and
            list((STATE / "worker-journal").iterdir()) == [worker_journal],
            "durable worker terminal was replayed or extended")
    require(
        worker_terminal["status"] == "FAILED_CLOSED" and
        worker_terminal["catch_up"] is False and
        coordinator_terminal["status"] == "FAILED_CLOSED" and
        coordinator_terminal["catch_up"] is False and
        int(worker_failed["NRestarts"]) >= 1 and
        worker_failed["InvocationID"] !=
            initial[UNITS[1]]["InvocationID"],
        "failed-closed journal semantics invalid")
    failed = {name: unit(name) for name in UNITS}

    command(["/usr/bin/systemctl", "stop", TARGET], check=False)
    command(["/usr/bin/systemctl", "stop", *UNITS], check=False)
    command(["/usr/bin/systemctl", "reset-failed", *UNITS], check=False)
    wait_for(lambda: all(
        unit(name)["ActiveState"] == "inactive" and
        unit(name)["SubState"] == "dead" and
        unit(name)["MainPID"] == "0" for name in UNITS),
        "owned unit cleanup incomplete")
    final = {name: unit(name) for name in UNITS}
    wait_for(process_residue_absent, "owned fixture process residue")

    checks = {
        "real_systemd_pid1_private_cgroup": True,
        "production_unit_inputs_present_and_hardened":
            len(units["units"]) == 4 and
            units["systemd_analyze_verify"]["returncode"] == 0,
        "effective_watchdog_timeout_observed":
            int(watchdog_state["NRestarts"]) >= 1,
        "watchdog_restart_changed_process_identity":
            first["pid"] != recovered["pid"],
        "watchdog_recovered_and_remained_healthy":
            watchdog_state["ActiveState"] == "active",
        "worker_failure_durable_before_exit": worker_ref["body_sha256"] ==
            worker_terminal["body_sha256"] and
            worker_at_publish["ActiveState"] == "active",
        "worker_restart_refused_terminal_replay":
            failed[UNITS[1]]["ActiveState"] == "failed" and
            int(failed[UNITS[1]]["NRestarts"]) >= 1 and
            failed[UNITS[1]]["InvocationID"] !=
                initial[UNITS[1]]["InvocationID"],
        "coordinator_observed_worker_terminal":
            coordinator_terminal["reason"] == "PINNED_WORKER_FAILED_CLOSED",
        "failed_closed_chain_forbids_catch_up":
            not worker_terminal["catch_up"] and
            not coordinator_terminal["catch_up"],
        "effective_unit_hardening_exact":
            all(effective_contract(value) for value in initial.values()),
        "target_stop_cleaned_all_owned_units": all(
            value["ActiveState"] == "inactive" and
            value["MainPID"] == "0" for value in final.values()),
        "owned_process_residue_absent": process_residue_absent(),
        "container_tcp_udp_surface_empty": network_empty(),
        "all_authority_and_order_flags_false": all(
            BOUNDARY[field] is False for field in FALSE_AUTHORITY_FIELDS) and
            all(BOUNDARY[field] == 0 for field in ZERO_EXPOSURE_FIELDS),
    }
    require(set(checks) == EXPECTED_CHECKS and all(checks.values()),
            "one or more liveness checks failed")
    return {
        "schema": SCHEMA, "passed": True, "run_id": run_id,
        "checks": checks,
        "inner_executable": inner_executable(),
        "boot": {
            "boot_id": boot_id, "pid1": 1, "pid1_comm": "systemd",
            "pid1_cgroup": pid1_cgroup,
            "systemd": command([
                "/usr/bin/systemctl", "--version"]).stdout.splitlines()[0],
        },
        "production_unit_inputs": units,
        "watchdog": {
            "first": first_ref, "recovered": recovered_ref,
            "first_pid": first["pid"], "recovered_pid": recovered["pid"],
            "first_invocation_id": first["invocation_id"],
            "recovered_invocation_id": recovered["invocation_id"],
            "n_restarts": int(watchdog_state["NRestarts"]),
            "effective_watchdog_usec": watchdog_state["WatchdogUSec"],
        },
        "durable_failure": {
            "worker_terminal": worker_ref,
            "coordinator_terminal": coordinator_ref,
            "worker_status": worker_terminal["status"],
            "coordinator_status": coordinator_terminal["status"],
            "catch_up": False, "post_restart_journal_entry_count": 1,
            "worker_active_at_publish": True,
            "terminal_observation_acknowledged": True,
            "worker_initial_invocation_id":
                initial[UNITS[1]]["InvocationID"],
            "worker_failed_invocation_id": worker_failed["InvocationID"],
            "worker_n_restarts": int(worker_failed["NRestarts"]),
        },
        "effective_units_before_fault": initial,
        "effective_units_after_fault": failed,
        "cleanup": {"target": TARGET, "units": final,
                    "all_inactive": True, "process_residue_absent": True},
        "boundary": BOUNDARY,
    }


def main() -> int:
    try:
        value = run()
        print(MARKER + json.dumps(
            value, ensure_ascii=True, allow_nan=False, sort_keys=True,
            separators=(",", ":")))
        return 0
    except Exception as error:
        print("hepta P1 liveness inner gate: FAIL " + type(error).__name__,
              file=sys.stderr)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
