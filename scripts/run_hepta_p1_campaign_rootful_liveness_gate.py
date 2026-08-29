#!/usr/bin/env python3
"""Run the disposable P1 coordinator/worker effective-systemd liveness gate.

The gate has no broker, credential, PAPER, LIVE, mutation or order surface.
It reuses the reviewed rootful build/container environment contract from the
dual-domain gate, but runs a distinct systemd fixture that proves effective
watchdog timeout/restart, durable FAILED_CLOSED behavior, no catch-up, and
target-owned cleanup.  Without the externally signed environment review and
all four reviewed provenance inputs the result is permanently rehearsal-only.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Iterator, Optional


sys.path.insert(0, str(Path(__file__).resolve(strict=True).parent))
import hepta_rootful_review_closure_consumer as ROOT_REVIEW
import run_hepta_p1_dual_domain_rootful_gate as BASE


SCHEMA = "hepta.p1-safety-soak-campaign-rootful-liveness-gate.v1"
INNER_SCHEMA = "hepta.p1-safety-soak-campaign-rootful-liveness-inner.v1"
INNER_MARKER = "HEPTA_P1_CAMPAIGN_ROOTFUL_LIVENESS_RESULT="
INNER_EXECUTABLE = "/usr/local/libexec/hepta_p1_liveness_inner_gate.py"
BASE_INNER_EXECUTABLE = (
    "/usr/local/libexec/hepta_p1_dual_domain_inner_gate.py")
PURPOSE = "p1-campaign-rootful-liveness-gate"
PURPOSE_LABEL = f"io.hepta.purpose={PURPOSE}"
RUN_LABEL_KEY = "io.hepta.run-id"
SCOPE = "p1-campaign-coordinator-rootful-liveness-prerequisite-only"
PRODUCTION_MODE = "PRODUCTION_REVIEWED_ROOTFUL_CERTIFICATION"
REHEARSAL_MODE = "REHEARSAL_ROOTFUL_NON_CERTIFYING"
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
SERVICE_EFFECTIVE_FIELDS = frozenset({
    "LoadState", "ActiveState", "SubState", "MainPID", "InvocationID",
    "NRestarts", "Result", "ExecMainStatus", "WatchdogUSec", "Restart",
    "RestartUSec", "Type", "NotifyAccess", "NoNewPrivileges",
    "PrivateDevices", "ProtectSystem", "ProtectHome", "ProtectClock",
    "RestrictNamespaces", "MemoryDenyWriteExecute",
    "CapabilityBoundingSet", "AmbientCapabilities",
    "RestrictAddressFamilies", "IPAddressDeny", "FragmentPath",
})
TARGET_EFFECTIVE_FIELDS = frozenset({
    "LoadState", "ActiveState", "SubState", "FragmentPath",
    "StopWhenUnneeded",
})
FIXTURE_REFERENCE_FIELDS = frozenset({
    "path", "file_sha256", "body_sha256", "device", "inode", "mode",
    "uid", "gid",
})
CERTIFICATION_BLOCKERS = BASE.CERTIFICATION_BLOCKERS
SOURCE_FILES = {
    "scripts/run_hepta_p1_dual_domain_rootful_gate.py":
        ("gate-inputs/rootful_base_runner.py", 0o644),
    "scripts/run_hepta_p1_campaign_rootful_liveness_gate.py":
        ("gate-inputs/runner.py", 0o644),
    "scripts/hepta_rootful_review_closure_consumer.py":
        ("gate-inputs/hepta_rootful_review_closure_consumer.py", 0o644),
    "systemd/hepta-p1-safety-soak-campaign@.service":
        ("production-unit-inputs/hepta-p1-safety-soak-campaign@.service",
         0o644),
    "systemd/hepta-p1-safety-soak-observer-worker@.service":
        ("production-unit-inputs/"
         "hepta-p1-safety-soak-observer-worker@.service", 0o644),
    "systemd/hepta-p1-safety-soak-recorder-worker@.service":
        ("production-unit-inputs/"
         "hepta-p1-safety-soak-recorder-worker@.service", 0o644),
    "systemd/hepta-p1-safety-soak@.target":
        ("production-unit-inputs/hepta-p1-safety-soak@.target", 0o644),
    "tests/p1_campaign_rootful_liveness_systemd/Dockerfile":
        ("tests/p1_campaign_rootful_liveness_systemd/Dockerfile", 0o644),
    "tests/p1_campaign_rootful_liveness_systemd/"
    "hepta-p1-liveness-systemd-entrypoint":
        ("tests/p1_campaign_rootful_liveness_systemd/"
         "hepta-p1-liveness-systemd-entrypoint", 0o755),
    "tests/p1_campaign_rootful_liveness_systemd/"
    "hepta_p1_liveness_inner_gate.py":
        ("tests/p1_campaign_rootful_liveness_systemd/"
         "hepta_p1_liveness_inner_gate.py", 0o755),
    "tests/p1_campaign_rootful_liveness_systemd/"
    "hepta_p1_liveness_daemon.py":
        ("install-root/usr/libexec/hepta-p1-liveness-daemon", 0o755),
    "tests/p1_campaign_rootful_liveness_systemd/"
    "hepta-p1-liveness-watchdog.service":
        ("install-root/usr/lib/systemd/system/"
         "hepta-p1-liveness-watchdog.service", 0o644),
    "tests/p1_campaign_rootful_liveness_systemd/"
    "hepta-p1-liveness-worker.service":
        ("install-root/usr/lib/systemd/system/"
         "hepta-p1-liveness-worker.service", 0o644),
    "tests/p1_campaign_rootful_liveness_systemd/"
    "hepta-p1-liveness-coordinator.service":
        ("install-root/usr/lib/systemd/system/"
         "hepta-p1-liveness-coordinator.service", 0o644),
    "tests/p1_campaign_rootful_liveness_systemd/"
    "hepta-p1-campaign-rootful-liveness.target":
        ("install-root/usr/lib/systemd/system/"
         "hepta-p1-campaign-rootful-liveness.target", 0o644),
}


GateError = BASE.GateError
CertificationRequest = BASE.CertificationRequest


def fail(message: str) -> None:
    raise GateError(message)


def canonical(value: object) -> bytes:
    return (json.dumps(
        value, ensure_ascii=True, allow_nan=False, sort_keys=True,
        separators=(",", ":")) + "\n").encode("ascii")


def body_sha(value: object) -> str:
    return "sha256:" + hashlib.sha256(canonical(value)).hexdigest()


def stage_context(
    root: Path, context: Path,
) -> tuple[dict[str, dict[str, object]], dict[str, str]]:
    records: dict[str, dict[str, object]] = {}
    for relative, (destination, mode) in SOURCE_FILES.items():
        raw, record = BASE.read_stable(root / relative)
        BASE.write_exact(context / destination, raw, mode)
        records[relative] = record
    return records, {}


def build_arguments(
    base: str, tag: str, context: Path, iidfile: Path, run_id: str, *,
    builder_name: Optional[str] = None,
) -> list[str]:
    arguments = (
        ["buildx", "build", "--builder", builder_name, "--load",
         "--platform", "linux/amd64", "--provenance=false"]
        if builder_name is not None else ["build"])
    arguments.extend([
        "--pull=false", "--network=none", "--no-cache",
        "--label", PURPOSE_LABEL,
        "--label", f"{RUN_LABEL_KEY}={run_id}",
        "--build-arg", f"BASE_IMAGE={base}", "--file",
        str(context / "tests/p1_campaign_rootful_liveness_systemd/"
            "Dockerfile"),
        "--iidfile", str(iidfile), "--tag", tag, str(context),
    ])
    return arguments


def create_arguments(
    image_id: str, name: str, run_id: str,
) -> list[str]:
    arguments = [
        "create", "--name", name, "--label", PURPOSE_LABEL,
        "--label", f"{RUN_LABEL_KEY}={run_id}",
        "--hostname", "hepta-p1-liveness-systemd", "--network", "none",
        "--cgroupns", "private", "--ipc", "private", "--read-only",
    ]
    for path, options in BASE.RUNTIME_TMPFS.items():
        arguments.extend(("--tmpfs", f"{path}:{options}"))
    arguments.extend(("--cap-drop", "ALL"))
    for capability in BASE.RUNTIME_CAPABILITIES:
        arguments.extend(("--cap-add", capability))
    arguments.extend((
        "--security-opt", "no-new-privileges", "--security-opt",
        f"apparmor={BASE.APPARMOR_PROFILE}", "--pids-limit", "256",
        "--memory", "768m", "--cpus", "2", "--stop-signal", "SIGRTMIN+3",
        "--stop-timeout", "20", "--env", "HEPTA_P1_LIVENESS_DISPOSABLE=1",
        "--env", f"HEPTA_P1_LIVENESS_RUN_ID={run_id}", image_id,
    ))
    return arguments


def validate_container_inspect_record(
    value: object, *, container_id: str, image_id: str, name: str,
    run_id: str,
) -> None:
    if not isinstance(value, dict):
        fail("container inspect is not an object")
    host = value.get("HostConfig")
    config = value.get("Config")
    mounts = value.get("Mounts")
    if not isinstance(host, dict) or not isinstance(config, dict) or \
            not isinstance(mounts, list):
        fail("container inspect sections are malformed")
    tmpfs = host.get("Tmpfs") or {}
    environment = config.get("Env")
    if not isinstance(environment, list):
        fail("container environment malformed")
    parsed: dict[str, str] = {}
    for raw in environment:
        if not isinstance(raw, str) or "=" not in raw:
            fail("container environment entry malformed")
        key, content = raw.split("=", 1)
        if key in parsed:
            fail("duplicate container environment")
        parsed[key] = content
    if (
        value.get("Id") != container_id or value.get("Name") != "/" + name or
        value.get("Image") != image_id or
        value.get("AppArmorProfile") != BASE.APPARMOR_PROFILE or
        config.get("Image") != image_id or
        config.get("Hostname") != "hepta-p1-liveness-systemd" or
        config.get("User") != "0:0" or config.get("WorkingDir") != "/" or
        config.get("Entrypoint") != [
            "/usr/local/libexec/hepta-p1-liveness-systemd-entrypoint"] or
        config.get("Cmd") not in (None, []) or
        config.get("ExposedPorts") not in (None, {}) or
        config.get("Volumes") not in (None, {}) or
        config.get("StopSignal") != "SIGRTMIN+3" or
        not BASE.object_owned(value, run_id) or
        host.get("Privileged") is not False or
        host.get("ReadonlyRootfs") is not True or
        host.get("NetworkMode") != "none" or
        host.get("CgroupnsMode") != "private" or
        host.get("IpcMode") != "private" or
        set(host.get("SecurityOpt") or []) != {
            "no-new-privileges", f"apparmor={BASE.APPARMOR_PROFILE}"} or
        host.get("PidsLimit") != 256 or
        host.get("Memory") != 768 * 1024 * 1024 or
        host.get("NanoCpus") != 2_000_000_000 or
        host.get("PublishAllPorts") is not False or
        host.get("PortBindings") not in (None, {}) or
        host.get("Binds") not in (None, []) or
        host.get("Devices") not in (None, []) or
        host.get("DeviceRequests") not in (None, []) or
        host.get("Links") not in (None, []) or
        (host.get("RestartPolicy") or {}) != {
            "Name": "no", "MaximumRetryCount": 0} or
        set(tmpfs) != set(BASE.RUNTIME_TMPFS) or
        any(tmpfs.get(path) != options
            for path, options in BASE.RUNTIME_TMPFS.items()) or
        any(item.get("Type") != "tmpfs" or
            item.get("Destination") not in BASE.RUNTIME_TMPFS
            for item in mounts) or
        set(host.get("CapDrop") or []) != {"ALL"} or
        set(host.get("CapAdd") or []) != {
            "CAP_" + item for item in BASE.RUNTIME_CAPABILITIES} or
        parsed.get("HEPTA_P1_LIVENESS_DISPOSABLE") != "1" or
        parsed.get("HEPTA_P1_LIVENESS_RUN_ID") != run_id or
        any(re.search(
            r"SECRET|TOKEN|PASSWORD|CREDENTIAL|AUTHORIZATION|BROKER", key,
            re.IGNORECASE) for key in parsed)
    ):
        fail("container isolation inspect mismatch")


def validate_inner(output: str, *, expected_run_id: str) -> dict[str, object]:
    lines = output.splitlines()
    if len(lines) != 1 or not lines[0].startswith(INNER_MARKER):
        fail("inner result marker/cardinality mismatch")
    raw = lines[0][len(INNER_MARKER):]
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise GateError("inner result JSON invalid") from error
    if not isinstance(value, dict) or set(value) != {
        "schema", "passed", "run_id", "checks", "inner_executable", "boot",
        "production_unit_inputs", "watchdog", "durable_failure",
        "effective_units_before_fault", "effective_units_after_fault",
        "cleanup", "boundary",
    }:
        fail("inner exact-field contract mismatch")
    checks = value.get("checks")
    if (
        value.get("schema") != INNER_SCHEMA or value.get("passed") is not True or
        value.get("run_id") != expected_run_id or
        not isinstance(checks, dict) or set(checks) != EXPECTED_CHECKS or
        not all(item is True for item in checks.values()) or
        value.get("boundary") != BOUNDARY or
        json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True,
                   separators=(",", ":")) != raw
    ):
        fail("inner result semantic contract mismatch")
    boot = value.get("boot")
    watchdog = value.get("watchdog")
    failure = value.get("durable_failure")
    cleanup = value.get("cleanup")
    inputs = value.get("production_unit_inputs")
    executable = value.get("inner_executable")
    before = value.get("effective_units_before_fault")
    after = value.get("effective_units_after_fault")
    if (
        not isinstance(executable, dict) or set(executable) != {
            "path", "file_sha256", "mode", "uid", "gid"} or
        executable.get("path") != INNER_EXECUTABLE or
        re.fullmatch(r"sha256:[0-9a-f]{64}", str(
            executable.get("file_sha256", ""))) is None or
        executable.get("mode") != "0755" or
        executable.get("uid") != 0 or executable.get("gid") != 0 or
        not isinstance(boot, dict) or set(boot) != {
            "boot_id", "pid1", "pid1_comm", "pid1_cgroup", "systemd"} or
        boot.get("pid1") != 1 or boot.get("pid1_comm") != "systemd" or
        boot.get("pid1_cgroup") != "0::/" or
        not isinstance(watchdog, dict) or watchdog.get("n_restarts", 0) < 1 or
        set(watchdog) != {
            "first", "recovered", "first_pid", "recovered_pid",
            "first_invocation_id", "recovered_invocation_id", "n_restarts",
            "effective_watchdog_usec"} or
        watchdog.get("first_pid") == watchdog.get("recovered_pid") or
        watchdog.get("first_invocation_id") ==
            watchdog.get("recovered_invocation_id") or
        watchdog.get("effective_watchdog_usec") != "2s" or
        not isinstance(failure, dict) or set(failure) != {
            "worker_terminal", "coordinator_terminal", "worker_status",
            "coordinator_status", "catch_up",
            "post_restart_journal_entry_count", "worker_active_at_publish",
            "terminal_observation_acknowledged",
            "worker_initial_invocation_id", "worker_failed_invocation_id",
            "worker_n_restarts"} or
        failure.get("worker_status") != "FAILED_CLOSED" or
        failure.get("coordinator_status") != "FAILED_CLOSED" or
        failure.get("catch_up") is not False or
        failure.get("post_restart_journal_entry_count") != 1 or
        failure.get("worker_active_at_publish") is not True or
        failure.get("terminal_observation_acknowledged") is not True or
        type(failure.get("worker_n_restarts")) is not int or
        failure.get("worker_n_restarts", 0) < 1 or
        not isinstance(failure.get("worker_initial_invocation_id"), str) or
        not isinstance(failure.get("worker_failed_invocation_id"), str) or
        failure.get("worker_initial_invocation_id") ==
            failure.get("worker_failed_invocation_id") or
        not isinstance(cleanup, dict) or set(cleanup) != {
            "target", "units", "all_inactive", "process_residue_absent"} or
        cleanup.get("target") !=
            "hepta-p1-campaign-rootful-liveness.target" or
        cleanup.get("all_inactive") is not True or
        cleanup.get("process_residue_absent") is not True or
        not isinstance(before, dict) or not isinstance(after, dict) or
        set(before) != {
            "hepta-p1-liveness-watchdog.service",
            "hepta-p1-liveness-worker.service",
            "hepta-p1-liveness-coordinator.service"} or
        set(after) != set(before) or
        not isinstance(cleanup.get("units"), dict) or
        set(cleanup["units"]) != set(before) or
        not isinstance(inputs, dict) or set(inputs) != {
            "systemd_analyze_verify", "units"} or
        not isinstance(inputs.get("systemd_analyze_verify"), dict) or
        set(inputs["systemd_analyze_verify"]) != {
            "argv", "returncode", "stdout_sha256"} or
        inputs["systemd_analyze_verify"].get("returncode") != 0 or
        re.fullmatch(r"sha256:[0-9a-f]{64}", str(
            inputs["systemd_analyze_verify"].get(
                "stdout_sha256", ""))) is None or
        not isinstance(inputs.get("units"), list) or
        len(inputs["units"]) != 4 or
        sorted(item.get("name") for item in inputs["units"]
               if isinstance(item, dict)) != [
            "hepta-p1-safety-soak-campaign@.service",
            "hepta-p1-safety-soak-observer-worker@.service",
            "hepta-p1-safety-soak-recorder-worker@.service",
            "hepta-p1-safety-soak@.target",
        ]
    ):
        fail("inner liveness evidence mismatch")
    for name in sorted(before):
        initial_state = before[name]
        fault_state = after[name]
        final_state = cleanup["units"][name]
        if (
            not isinstance(initial_state, dict) or
            set(initial_state) != SERVICE_EFFECTIVE_FIELDS or
            not isinstance(fault_state, dict) or
            set(fault_state) != SERVICE_EFFECTIVE_FIELDS or
            not isinstance(final_state, dict) or
            set(final_state) != SERVICE_EFFECTIVE_FIELDS or
            initial_state.get("ActiveState") != "active" or
            initial_state.get("SubState") != "running" or
            final_state.get("ActiveState") != "inactive" or
            final_state.get("SubState") != "dead" or
            final_state.get("MainPID") != "0"
        ):
            fail("inner fixture unit lifecycle evidence mismatch")
    worker_after = after["hepta-p1-liveness-worker.service"]
    if (
        worker_after.get("ActiveState") != "failed" or
        re.fullmatch(r"[1-9][0-9]*", str(
            worker_after.get("NRestarts", ""))) is None or
        worker_after.get("InvocationID") !=
            failure.get("worker_failed_invocation_id") or
        before["hepta-p1-liveness-worker.service"].get("InvocationID") !=
            failure.get("worker_initial_invocation_id")
    ):
        fail("inner worker restart evidence mismatch")
    reference_paths = {
        "first": "/var/lib/hepta-p1-liveness/watchdog-first.json",
        "recovered": "/var/lib/hepta-p1-liveness/watchdog-recovered.json",
        "worker_terminal": (
            "/var/lib/hepta-p1-liveness/worker-journal/00000000.json"),
        "coordinator_terminal": (
            "/var/lib/hepta-p1-liveness/coordinator-journal/00000000.json"),
    }
    references = {
        "first": watchdog.get("first"),
        "recovered": watchdog.get("recovered"),
        "worker_terminal": failure.get("worker_terminal"),
        "coordinator_terminal": failure.get("coordinator_terminal"),
    }
    for role, reference in references.items():
        if (
            not isinstance(reference, dict) or
            set(reference) != FIXTURE_REFERENCE_FIELDS or
            reference.get("path") != reference_paths[role] or
            re.fullmatch(r"sha256:[0-9a-f]{64}", str(
                reference.get("file_sha256", ""))) is None or
            re.fullmatch(r"sha256:[0-9a-f]{64}", str(
                reference.get("body_sha256", ""))) is None or
            reference.get("mode") != "0600" or
            reference.get("uid") != 0 or reference.get("gid") != 0 or
            type(reference.get("device")) is not int or
            type(reference.get("inode")) is not int
        ):
            fail("inner durable reference evidence mismatch")
    for item in inputs["units"]:
        name = item.get("name") if isinstance(item, dict) else None
        if (
            not isinstance(item, dict) or set(item) != {
                "name", "source_path", "loaded_path", "file_sha256",
                "effective"} or
            item.get("source_path") !=
                "/opt/hepta-inputs/systemd/" + str(item.get("name")) or
            item.get("loaded_path") !=
                "/run/systemd/system/" + str(item.get("name")) or
            re.fullmatch(r"sha256:[0-9a-f]{64}", str(
                item.get("file_sha256", ""))) is None or
            not isinstance(item.get("effective"), dict) or
            item["effective"].get("LoadState") != "loaded" or
            item["effective"].get("ActiveState") != "inactive" or
            item["effective"].get("SubState") != "dead" or
            item["effective"].get("FragmentPath") !=
                item.get("loaded_path")
        ):
            fail("inner effective production unit evidence mismatch")
        effective = item["effective"]
        if str(name).endswith(".service"):
            expected_caps = {
                "hepta-p1-safety-soak-campaign@.service": set(),
                "hepta-p1-safety-soak-observer-worker@.service": {
                    "cap_dac_read_search", "cap_sys_ptrace", "cap_net_admin"},
                "hepta-p1-safety-soak-recorder-worker@.service": {
                    "cap_dac_read_search"},
            }[str(name)]
            expected_families = {
                "hepta-p1-safety-soak-campaign@.service": {"AF_UNIX"},
                "hepta-p1-safety-soak-observer-worker@.service": {
                    "AF_UNIX", "AF_NETLINK"},
                "hepta-p1-safety-soak-recorder-worker@.service": {"AF_UNIX"},
            }[str(name)]
            expected_watchdog = (
                "45s" if name ==
                "hepta-p1-safety-soak-campaign@.service" else "30s")
            if (
                set(effective) != SERVICE_EFFECTIVE_FIELDS or
                effective.get("MainPID") != "0" or
                effective.get("Type") != "notify" or
                effective.get("NotifyAccess") != "main" or
                effective.get("Restart") != "on-failure" or
                effective.get("WatchdogUSec") != expected_watchdog or
                effective.get("NoNewPrivileges") != "yes" or
                effective.get("PrivateDevices") != "yes" or
                effective.get("ProtectSystem") != "strict" or
                effective.get("ProtectHome") != "yes" or
                effective.get("ProtectClock") != "yes" or
                effective.get("RestrictNamespaces") != "yes" or
                effective.get("MemoryDenyWriteExecute") != "yes" or
                set(effective.get("CapabilityBoundingSet", "").split()) !=
                    expected_caps or
                effective.get("AmbientCapabilities") != "" or
                set(effective.get("RestrictAddressFamilies", "").split()) !=
                    expected_families or
                set(effective.get("IPAddressDeny", "").split()) !=
                    {"0.0.0.0/0", "::/0"}
            ):
                fail("inner effective production service contract drift")
        elif set(effective) != TARGET_EFFECTIVE_FIELDS or \
                effective.get("StopWhenUnneeded") != "yes":
            fail("inner effective production target contract drift")
    expected_verify_argv = [
        "/usr/bin/systemd-analyze", "verify", *(
            "/run/systemd/system/" + name for name in (
                "hepta-p1-safety-soak-campaign@.service",
                "hepta-p1-safety-soak-observer-worker@.service",
                "hepta-p1-safety-soak-recorder-worker@.service",
                "hepta-p1-safety-soak@.target",
            ))]
    if inputs["systemd_analyze_verify"].get("argv") != expected_verify_argv:
        fail("inner systemd-analyze argv drift")
    return value


@contextmanager
def patched_base() -> Iterator[None]:
    original_command = BASE.command

    def liveness_command(
        arguments: list[str], *, timeout: int = 120, check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        rewritten = [
            INNER_EXECUTABLE if item == BASE_INNER_EXECUTABLE else item
            for item in arguments
        ]
        return original_command(rewritten, timeout=timeout, check=check)

    replacements = {
        "SCHEMA": SCHEMA, "INNER_SCHEMA": INNER_SCHEMA,
        "INNER_MARKER": INNER_MARKER, "PURPOSE": PURPOSE,
        "PURPOSE_LABEL": PURPOSE_LABEL, "RUN_LABEL_KEY": RUN_LABEL_KEY,
        "CERTIFICATION_BLOCKERS": CERTIFICATION_BLOCKERS,
        "SOURCE_FILES": SOURCE_FILES, "EXPECTED_CHECKS": EXPECTED_CHECKS,
        "EXPECTED_BOUNDARY": BOUNDARY, "stage_context": stage_context,
        "build_arguments": build_arguments, "create_arguments": create_arguments,
        "validate_container_inspect_record": validate_container_inspect_record,
        "validate_inner": validate_inner,
        "command": liveness_command,
        # The base execution function performs all evidence collection and
        # closure checks.  Its final dual-domain-shaped validator is replaced
        # only until this runner transforms that raw evidence into its own
        # exact report contract below.
        "validate_report": lambda report: report,
    }
    original = {name: getattr(BASE, name) for name in replacements}
    try:
        for name, value in replacements.items():
            setattr(BASE, name, value)
        yield
    finally:
        for name, value in original.items():
            setattr(BASE, name, value)


def validate_report(report: object) -> dict[str, object]:
    fields = {
        "schema", "run_id", "decision", "passed", "rehearsal_passed",
        "certification_ready", "certification_blockers", "scope",
        "started_at_ms", "completed_at_ms", "expires_at_ms", "body_sha256",
        "producer", "production_mode", "paper_test_admission_candidate",
        "paper_admission_authorized", "paper_authorized", "live_authorized",
        "mutation_authorized", "direct_broker_access",
        "order_submission_authorized", "duration_ms", "lineage", "inputs",
        "generated_input_sha256", "platform", "container",
        "disposable_cleanup", "certification", "environment_review_closure",
        "inner", "boundary",
    }
    if not isinstance(report, dict) or set(report) != fields:
        fail("outer report exact-field contract mismatch")
    body = dict(report)
    claimed = body.pop("body_sha256", None)
    certifying = report.get("decision") == "GO"
    started = report.get("started_at_ms")
    completed = report.get("completed_at_ms")
    expires = report.get("expires_at_ms")
    if (
        report.get("schema") != SCHEMA or
        re.fullmatch(r"[0-9a-f]{32}", str(report.get("run_id", ""))) is None or
        report.get("decision") not in {"GO", "REHEARSAL_ONLY"} or
        report.get("passed") is not certifying or
        report.get("rehearsal_passed") is not True or
        report.get("certification_ready") is not certifying or
        report.get("certification_blockers") !=
            ([] if certifying else list(CERTIFICATION_BLOCKERS)) or
        report.get("scope") != SCOPE or
        type(started) is not int or type(completed) is not int or
        type(expires) is not int or not (0 <= started <= completed < expires) or
        report.get("duration_ms") != completed - started or
        claimed != body_sha(body) or
        report.get("production_mode") !=
            (PRODUCTION_MODE if certifying else REHEARSAL_MODE) or
        any(report.get(field) is not False for field in (
            "paper_test_admission_candidate", "paper_admission_authorized",
            "paper_authorized", "live_authorized", "mutation_authorized",
            "direct_broker_access", "order_submission_authorized"))):
        fail("outer report decision/authority contract mismatch")
    inputs = report.get("inputs")
    lineage = report.get("lineage")
    producer = report.get("producer")
    inner_report = report.get("inner")
    if (
        not isinstance(inputs, dict) or set(inputs) != set(SOURCE_FILES) or
        not isinstance(lineage, dict) or set(lineage) != {
            "source_commit", "expected_source_commit", "source_tree_clean",
            "all_inputs_versioned", "inputs_stable", "final_lineage",
            "input_manifest_sha256", "runner_sha256"} or
        not isinstance(producer, dict) or set(producer) != {
            "path", "file_sha256"} or
        producer.get("path") != str(Path(__file__).resolve(strict=True)) or
        producer.get("file_sha256") != lineage.get("runner_sha256") or
        lineage.get("source_commit") != lineage.get("expected_source_commit") or
        lineage.get("inputs_stable") is not True or
        lineage.get("final_lineage") is not
            (bool(lineage.get("source_tree_clean")) and
             bool(lineage.get("all_inputs_versioned"))) or
        (certifying and lineage.get("final_lineage") is not True) or
        lineage.get("input_manifest_sha256") != body_sha(inputs) or
        lineage.get("runner_sha256") != "sha256:" + inputs[
            "scripts/run_hepta_p1_campaign_rootful_liveness_gate.py"][
                "sha256"] or
        not isinstance(inner_report, dict) or
        not isinstance(inner_report.get("inner_executable"), dict) or
        inner_report["inner_executable"].get("file_sha256") != "sha256:" + inputs[
                "tests/p1_campaign_rootful_liveness_systemd/"
                "hepta_p1_liveness_inner_gate.py"]["sha256"]
    ):
        fail("outer source lineage mismatch")
    for source, record in inputs.items():
        if (
            not isinstance(record, dict) or set(record) != {
                "sha256", "size", "mode"} or
            re.fullmatch(r"[0-9a-f]{64}", str(
                record.get("sha256", ""))) is None or
            type(record.get("size")) is not int or record["size"] <= 0 or
            record.get("mode") != format(SOURCE_FILES[source][1], "04o")
        ):
            fail("outer source input record mismatch")
    if report.get("generated_input_sha256") != {}:
        fail("outer generated input contract mismatch")
    inner_value = validate_inner(
        INNER_MARKER + json.dumps(
            report["inner"], ensure_ascii=True, allow_nan=False,
            sort_keys=True, separators=(",", ":")),
        expected_run_id=str(report["run_id"]))
    unit_hashes = {
        item["name"]: item["file_sha256"]
        for item in inner_value["production_unit_inputs"]["units"]
    }
    for source in (
        "systemd/hepta-p1-safety-soak-campaign@.service",
        "systemd/hepta-p1-safety-soak-observer-worker@.service",
        "systemd/hepta-p1-safety-soak-recorder-worker@.service",
        "systemd/hepta-p1-safety-soak@.target",
    ):
        if unit_hashes.get(Path(source).name) != \
                "sha256:" + inputs[source]["sha256"]:
            fail("inner production unit/source hash mismatch")
    if report.get("boundary") != BOUNDARY:
        fail("outer boundary mismatch")
    platform = report.get("platform")
    if not isinstance(platform, dict) or set(platform) != {
        "host_kernel", "host_architecture", "docker_client",
        "docker_server_version", "docker_server_api_version",
        "docker_server_os", "docker_server_architecture",
        "docker_cgroup_driver", "docker_cgroup_version",
        "docker_default_runtime", "docker_security_options",
        "base_image_reference", "base_image_id", "base_image_os",
        "base_image_architecture", "systemd", "container_boot_id",
        "container_pid1_cgroup",
    }:
        fail("outer platform exact-field mismatch")
    try:
        base_reference = BASE.require_pinned_image(str(
            platform.get("base_image_reference", "")))
    except BASE.GateError as error:
        raise GateError(str(error)) from error
    if (
        base_reference != platform.get("base_image_reference") or
        re.fullmatch(r"sha256:[0-9a-f]{64}", str(
            platform.get("base_image_id", ""))) is None or
        re.fullmatch(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
            r"[89ab][0-9a-f]{3}-[0-9a-f]{12}", str(
                platform.get("container_boot_id", ""))) is None or
        platform.get("container_pid1_cgroup") != "0::/" or
        platform.get("docker_cgroup_version") != "2" or
        not isinstance(platform.get("docker_security_options"), list)
    ):
        fail("outer platform value mismatch")
    cleanup = report.get("disposable_cleanup")
    container = report.get("container")
    certification = report.get("certification")
    if (
        cleanup != {"container_absent": True, "image_tag_absent": True,
                    "image_id_absent": True} or
        not isinstance(container, dict) or set(container) != {
            "image_id", "network_mode", "read_only_rootfs",
            "private_cgroup_namespace", "privileged", "bind_mounts",
            "published_ports", "devices", "device_requests", "links",
            "tmpfs_allowlist", "capabilities", "apparmor_profile"} or
        re.fullmatch(r"sha256:[0-9a-f]{64}", str(
            container.get("image_id", ""))) is None or
        container.get("network_mode") != "none" or
        container.get("read_only_rootfs") is not True or
        container.get("private_cgroup_namespace") is not True or
        container.get("privileged") is not False or
        container.get("bind_mounts") != 0 or
        container.get("published_ports") != 0 or
        container.get("devices") != 0 or
        container.get("device_requests") != 0 or
        container.get("links") != 0 or
        container.get("tmpfs_allowlist") != BASE.RUNTIME_TMPFS or
        container.get("capabilities") != list(BASE.RUNTIME_CAPABILITIES) or
        container.get("apparmor_profile") != BASE.APPARMOR_PROFILE or
        not isinstance(certification, dict) or set(certification) != {
            "requested", "eligible", "provenance",
            "provenance_reopened_equal", "reviewed_base",
            "reviewed_buildkit", "buildx_toolchain", "isolated_builder",
            "isolated_builder_cleanup", "docker_socket_before",
            "docker_socket_after", "docker_socket_records_equal",
            "apparmor_before", "apparmor_after", "apparmor_records_equal",
            "docker_namespace_before", "docker_namespace_after",
            "docker_namespace_records_equal"} or
        certification.get("requested") is not certifying or
        certification.get("eligible") is not certifying
    ):
        fail("outer isolation/certification evidence mismatch")
    review = report.get("environment_review_closure")
    evidence_fields = (
        "provenance", "reviewed_base", "reviewed_buildkit",
        "buildx_toolchain", "isolated_builder", "isolated_builder_cleanup",
        "docker_socket_before", "docker_socket_after", "apparmor_before",
        "apparmor_after", "docker_namespace_before", "docker_namespace_after",
    )
    equality_fields = (
        "provenance_reopened_equal", "docker_socket_records_equal",
        "apparmor_records_equal", "docker_namespace_records_equal",
    )
    if certifying:
        if not isinstance(review, dict):
            fail("certifying signed environment review missing")
        try:
            ROOT_REVIEW.validate_verification_record(
                review, now_ms=int(completed))
        except ROOT_REVIEW.ReviewClosureError as error:
            raise GateError(str(error)) from error
        if (
            any(not isinstance(certification.get(field), dict)
                for field in evidence_fields) or
            any(certification.get(field) is not True
                for field in equality_fields) or
            certification["docker_socket_before"] !=
                certification["docker_socket_after"] or
            certification["apparmor_before"] !=
                certification["apparmor_after"] or
            certification["docker_namespace_before"] !=
                certification["docker_namespace_after"]
        ):
            fail("certifying environment evidence closure mismatch")
        provenance = certification["provenance"]
        if not isinstance(provenance, dict) or set(provenance) != {
                "base", "builder", "apparmor", "docker_namespace"}:
            fail("certifying provenance inventory mismatch")
        with patched_base():
            BASE.validate_certifying_report_evidence(
                certification, run_id=str(report["run_id"]),
                platform=report["platform"], started_at_ms=int(started),
                completed_at_ms=int(completed))
            bodies = BASE.validate_report_provenance_inventory(
                provenance, started_at_ms=int(started),
                completed_at_ms=int(completed))
        if (
            review.get("source_commit") != lineage["source_commit"] or
            review.get("base_image_reference") !=
                report["platform"].get("base_image_reference") or
            review.get("buildkit_image_reference") !=
                bodies["builder"]["repo_digest"] or
            any(review["outputs"][kind]["file_sha256"] !=
                provenance[kind]["document_sha256"] for kind in bodies)
        ):
            fail("certifying evidence not bound to signed review")
        if report["expires_at_ms"] != min(
                min(int(item["expires_at_ms"]) for item in bodies.values()),
                int(review["expires_at_ms"])):
            fail("certifying expiry does not bind reviewed evidence")
    elif review is not None:
        fail("rehearsal cannot contain certifying review evidence")
    elif (
        any(certification.get(field) is not None for field in evidence_fields) or
        any(certification.get(field) is not False for field in equality_fields) or
        report["expires_at_ms"] != report["completed_at_ms"] +
            BASE.REHEARSAL_REPORT_LIFETIME_MS
    ):
        fail("rehearsal contains certifying environment evidence")
    return report


def execute(
    base: str, expected_source_commit: str, *,
    allow_dirty_rehearsal: bool = False,
    certification_request: Optional[CertificationRequest] = None,
) -> dict[str, object]:
    with patched_base():
        raw = BASE.execute(
            base, expected_source_commit,
            allow_dirty_rehearsal=allow_dirty_rehearsal,
            certification_request=certification_request)
    inputs = copy.deepcopy(raw["inputs"])
    runner_sha = "sha256:" + inputs[
        "scripts/run_hepta_p1_campaign_rootful_liveness_gate.py"]["sha256"]
    lineage = copy.deepcopy(raw["lineage"])
    lineage["runner_sha256"] = runner_sha
    lineage["input_manifest_sha256"] = body_sha(inputs)
    report = {
        **{key: copy.deepcopy(value) for key, value in raw.items()
           if key not in {"scope", "lineage", "body_sha256"}},
        "scope": SCOPE,
        "producer": {
            "path": str(Path(__file__).resolve(strict=True)),
            "file_sha256": runner_sha,
        },
        "production_mode": (
            PRODUCTION_MODE if raw["decision"] == "GO" else REHEARSAL_MODE),
        "lineage": lineage,
        "body_sha256": "",
    }
    body = dict(report)
    body.pop("body_sha256")
    report["body_sha256"] = body_sha(body)
    return validate_report(report)


def atomic_report(path: Path, report: dict[str, object]) -> None:
    validate_report(report)
    original = BASE.validate_report
    try:
        BASE.validate_report = validate_report
        BASE.atomic_report(path, report)
    finally:
        BASE.validate_report = original


def main() -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--allow-dirty-rehearsal", action="store_true")
    parser.add_argument("--certify", action="store_true")
    parser.add_argument("--base-image", required=True)
    parser.add_argument("--buildkit-image")
    parser.add_argument("--buildx-binary-sha256")
    parser.add_argument("--reviewed-base-provenance", type=Path)
    parser.add_argument("--reviewed-base-provenance-sha256")
    parser.add_argument("--reviewed-builder-provenance", type=Path)
    parser.add_argument("--reviewed-builder-provenance-sha256")
    parser.add_argument("--apparmor-provenance", type=Path)
    parser.add_argument("--apparmor-provenance-sha256")
    parser.add_argument("--docker-apparmor-namespace-provenance", type=Path)
    parser.add_argument("--docker-apparmor-namespace-provenance-sha256")
    ROOT_REVIEW.add_arguments(parser)
    parser.add_argument("--expected-source-commit", required=True)
    parser.add_argument("--report", type=Path, required=True)
    arguments = parser.parse_args()
    try:
        report_path = BASE.safe_report_path(arguments.report)
        if not arguments.run:
            print("P1 rootful liveness gate disabled; pass --run", file=sys.stderr)
            return 78
        if arguments.certify and arguments.allow_dirty_rehearsal:
            fail("--certify cannot combine with dirty rehearsal")
        try:
            environment_review = ROOT_REVIEW.inputs_from_arguments(
                arguments, certify=arguments.certify)
        except ROOT_REVIEW.ReviewClosureError as error:
            raise GateError(str(error)) from error
        request = BASE.certification_request_from_values(
            certify=arguments.certify,
            buildkit_image=arguments.buildkit_image,
            buildx_binary_sha256=arguments.buildx_binary_sha256,
            reviewed_base_path=arguments.reviewed_base_provenance,
            reviewed_base_sha256=arguments.reviewed_base_provenance_sha256,
            reviewed_builder_path=arguments.reviewed_builder_provenance,
            reviewed_builder_sha256=
                arguments.reviewed_builder_provenance_sha256,
            reviewed_apparmor_path=arguments.apparmor_provenance,
            reviewed_apparmor_sha256=arguments.apparmor_provenance_sha256,
            reviewed_docker_namespace_path=
                arguments.docker_apparmor_namespace_provenance,
            reviewed_docker_namespace_sha256=
                arguments.docker_apparmor_namespace_provenance_sha256,
            environment_review=environment_review)
        report = execute(
            arguments.base_image, arguments.expected_source_commit,
            allow_dirty_rehearsal=arguments.allow_dirty_rehearsal,
            certification_request=request)
        atomic_report(report_path, report)
    except (GateError, OSError, ValueError,
            subprocess.SubprocessError) as error:
        print("hepta P1 rootful liveness gate: FAIL: " +
              (str(error) or type(error).__name__)[:2048], file=sys.stderr)
        return 1
    print("hepta P1 rootful liveness gate: " + str(report["decision"]) +
          " watchdog_restart=1 durable_failed_closed=1 orders=0 authority=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
