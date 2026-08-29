#!/usr/bin/env python3
"""Rootless contracts for the disposable P1 campaign liveness gate."""

from __future__ import annotations

import copy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve(strict=True).parents[1]
MODULE = ROOT / "scripts/run_hepta_p1_campaign_rootful_liveness_gate.py"
SPEC = importlib.util.spec_from_file_location(
    "run_hepta_p1_campaign_rootful_liveness_gate_under_test", MODULE)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("cannot import P1 campaign rootful liveness gate")
G = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = G
SPEC.loader.exec_module(G)


RUN_ID = "d" * 32
COMMIT = "c" * 40
BOOT_ID = "01234567-89ab-4def-8123-456789abcdef"
IMAGE_ID = "sha256:" + "e" * 64
INNER_SHA = "sha256:" + "a" * 64
UNIT_NAMES = (
    "hepta-p1-safety-soak-campaign@.service",
    "hepta-p1-safety-soak-observer-worker@.service",
    "hepta-p1-safety-soak-recorder-worker@.service",
    "hepta-p1-safety-soak@.target",
)
FIXTURE_UNITS = (
    "hepta-p1-liveness-watchdog.service",
    "hepta-p1-liveness-worker.service",
    "hepta-p1-liveness-coordinator.service",
)


def reference(path: str) -> dict[str, object]:
    return {
        "path": path, "file_sha256": "sha256:" + "1" * 64,
        "body_sha256": "sha256:" + "2" * 64, "device": 1, "inode": 2,
        "mode": "0600", "uid": 0, "gid": 0,
    }


def service_state(
    *, active: str = "active", sub: str = "running", pid: str = "100",
    invocation: str = "fixture-invocation", restarts: str = "0",
    watchdog: str = "2s", capabilities: str = "",
    families: str = "AF_UNIX", fragment: str = "/fixture.service",
) -> dict[str, str]:
    return {
        "LoadState": "loaded", "ActiveState": active, "SubState": sub,
        "MainPID": pid, "InvocationID": invocation, "NRestarts": restarts,
        "Result": "", "ExecMainStatus": "0", "WatchdogUSec": watchdog,
        "Restart": "on-failure", "RestartUSec": "200ms", "Type": "notify",
        "NotifyAccess": "main", "NoNewPrivileges": "yes",
        "PrivateDevices": "yes", "ProtectSystem": "strict",
        "ProtectHome": "yes", "ProtectClock": "yes",
        "RestrictNamespaces": "yes", "MemoryDenyWriteExecute": "yes",
        "CapabilityBoundingSet": capabilities, "AmbientCapabilities": "",
        "RestrictAddressFamilies": families,
        "IPAddressDeny": "0.0.0.0/0 ::/0", "FragmentPath": fragment,
    }


def production_effective(name: str) -> dict[str, str]:
    capabilities = {
        UNIT_NAMES[0]: "",
        UNIT_NAMES[1]: "cap_dac_read_search cap_net_admin cap_sys_ptrace",
        UNIT_NAMES[2]: "cap_dac_read_search",
    }
    families = {
        UNIT_NAMES[0]: "AF_UNIX", UNIT_NAMES[1]: "AF_NETLINK AF_UNIX",
        UNIT_NAMES[2]: "AF_UNIX",
    }
    if name.endswith(".target"):
        return {
            "LoadState": "loaded", "ActiveState": "inactive",
            "SubState": "dead",
            "FragmentPath": "/run/systemd/system/" + name,
            "StopWhenUnneeded": "yes",
        }
    return service_state(
        active="inactive", sub="dead", pid="0", invocation="",
        watchdog="45s" if name == UNIT_NAMES[0] else "30s",
        capabilities=capabilities[name], families=families[name],
        fragment="/run/systemd/system/" + name)


def valid_inner() -> dict[str, object]:
    before = {
        FIXTURE_UNITS[0]: service_state(invocation="watchdog-1", pid="101"),
        FIXTURE_UNITS[1]: service_state(invocation="worker-1", pid="102"),
        FIXTURE_UNITS[2]: service_state(invocation="coordinator-1", pid="103"),
    }
    after = copy.deepcopy(before)
    after[FIXTURE_UNITS[1]] = service_state(
        active="failed", sub="failed", pid="0", invocation="worker-2",
        restarts="1")
    cleanup = {
        name: service_state(
            active="inactive", sub="dead", pid="0", invocation="",
            restarts="1" if name == FIXTURE_UNITS[1] else "0")
        for name in FIXTURE_UNITS
    }
    units = [{
        "name": name,
        "source_path": "/opt/hepta-inputs/systemd/" + name,
        "loaded_path": "/run/systemd/system/" + name,
        "file_sha256": "sha256:" + str(index + 3) * 64,
        "effective": production_effective(name),
    } for index, name in enumerate(UNIT_NAMES)]
    verify_argv = [
        "/usr/bin/systemd-analyze", "verify",
        *("/run/systemd/system/" + name for name in UNIT_NAMES),
    ]
    return {
        "schema": G.INNER_SCHEMA, "passed": True, "run_id": RUN_ID,
        "checks": {name: True for name in G.EXPECTED_CHECKS},
        "inner_executable": {
            "path": G.INNER_EXECUTABLE, "file_sha256": INNER_SHA,
            "mode": "0755", "uid": 0, "gid": 0,
        },
        "boot": {
            "boot_id": BOOT_ID, "pid1": 1, "pid1_comm": "systemd",
            "pid1_cgroup": "0::/", "systemd": "systemd 255 (255.4-1)",
        },
        "production_unit_inputs": {
            "systemd_analyze_verify": {
                "argv": verify_argv, "returncode": 0,
                "stdout_sha256": "sha256:" + "9" * 64,
            },
            "units": units,
        },
        "watchdog": {
            "first": reference(
                "/var/lib/hepta-p1-liveness/watchdog-first.json"),
            "recovered": reference(
                "/var/lib/hepta-p1-liveness/watchdog-recovered.json"),
            "first_pid": 101, "recovered_pid": 201,
            "first_invocation_id": "watchdog-1",
            "recovered_invocation_id": "watchdog-2",
            "n_restarts": 1, "effective_watchdog_usec": "2s",
        },
        "durable_failure": {
            "worker_terminal": reference(
                "/var/lib/hepta-p1-liveness/worker-journal/00000000.json"),
            "coordinator_terminal": reference(
                "/var/lib/hepta-p1-liveness/"
                "coordinator-journal/00000000.json"),
            "worker_status": "FAILED_CLOSED",
            "coordinator_status": "FAILED_CLOSED", "catch_up": False,
            "post_restart_journal_entry_count": 1,
            "worker_active_at_publish": True,
            "terminal_observation_acknowledged": True,
            "worker_initial_invocation_id": "worker-1",
            "worker_failed_invocation_id": "worker-2",
            "worker_n_restarts": 1,
        },
        "effective_units_before_fault": before,
        "effective_units_after_fault": after,
        "cleanup": {
            "target": "hepta-p1-campaign-rootful-liveness.target",
            "units": cleanup, "all_inactive": True,
            "process_residue_absent": True,
        },
        "boundary": copy.deepcopy(G.BOUNDARY),
    }


def marker(value: dict[str, object]) -> str:
    return G.INNER_MARKER + json.dumps(
        value, ensure_ascii=True, allow_nan=False, sort_keys=True,
        separators=(",", ":"))


def rehearsal_certification() -> dict[str, object]:
    return {
        "requested": False, "eligible": False, "provenance": None,
        "provenance_reopened_equal": False, "reviewed_base": None,
        "reviewed_buildkit": None, "buildx_toolchain": None,
        "isolated_builder": None, "isolated_builder_cleanup": None,
        "docker_socket_before": None, "docker_socket_after": None,
        "docker_socket_records_equal": False, "apparmor_before": None,
        "apparmor_after": None, "apparmor_records_equal": False,
        "docker_namespace_before": None, "docker_namespace_after": None,
        "docker_namespace_records_equal": False,
    }


def valid_report() -> dict[str, object]:
    inner = valid_inner()
    input_hashes = {
        name: inner["production_unit_inputs"]["units"][index]["file_sha256"][
            len("sha256:"):]
        for index, name in enumerate(UNIT_NAMES)
    }
    inputs: dict[str, dict[str, object]] = {}
    for index, (source, (_destination, mode)) in enumerate(
            G.SOURCE_FILES.items()):
        digest = ("a" * 64 if source.endswith(
            "hepta_p1_liveness_inner_gate.py") else
            input_hashes.get(Path(source).name, format(index + 1, "x") * 64))
        digest = digest[:64]
        inputs[source] = {
            "sha256": digest, "size": index + 1, "mode": format(mode, "04o"),
        }
    runner_hash = inputs[
        "scripts/run_hepta_p1_campaign_rootful_liveness_gate.py"]["sha256"]
    platform = {
        "host_kernel": "fixture", "host_architecture": "amd64",
        "docker_client": "fixture", "docker_server_version": "29.1.3",
        "docker_server_api_version": "1.52",
        "docker_server_os": "fixture", "docker_server_architecture": "amd64",
        "docker_cgroup_driver": "systemd", "docker_cgroup_version": "2",
        "docker_default_runtime": "runc", "docker_security_options": [],
        "base_image_reference": (
            "registry.example/hepta/systemd@sha256:" + "b" * 64),
        "base_image_id": "sha256:" + "b" * 64,
        "base_image_os": "linux", "base_image_architecture": "amd64",
        "systemd": "systemd 255", "container_boot_id": BOOT_ID,
        "container_pid1_cgroup": "0::/",
    }
    report = {
        "schema": G.SCHEMA, "run_id": RUN_ID, "decision": "REHEARSAL_ONLY",
        "passed": False, "rehearsal_passed": True,
        "certification_ready": False,
        "certification_blockers": list(G.CERTIFICATION_BLOCKERS),
        "scope": G.SCOPE, "started_at_ms": 1000, "completed_at_ms": 2000,
        "expires_at_ms": 2000 + G.BASE.REHEARSAL_REPORT_LIFETIME_MS,
        "body_sha256": "",
        "producer": {
            "path": str(MODULE), "file_sha256": "sha256:" + runner_hash,
        },
        "production_mode": G.REHEARSAL_MODE,
        "paper_test_admission_candidate": False,
        "paper_admission_authorized": False, "paper_authorized": False,
        "live_authorized": False, "mutation_authorized": False,
        "direct_broker_access": False, "order_submission_authorized": False,
        "duration_ms": 1000,
        "lineage": {
            "source_commit": COMMIT, "expected_source_commit": COMMIT,
            "source_tree_clean": True, "all_inputs_versioned": True,
            "inputs_stable": True, "final_lineage": True,
            "input_manifest_sha256": G.body_sha(inputs),
            "runner_sha256": "sha256:" + runner_hash,
        },
        "inputs": inputs, "generated_input_sha256": {}, "platform": platform,
        "container": {
            "image_id": IMAGE_ID, "network_mode": "none",
            "read_only_rootfs": True, "private_cgroup_namespace": True,
            "privileged": False, "bind_mounts": 0, "published_ports": 0,
            "devices": 0, "device_requests": 0, "links": 0,
            "tmpfs_allowlist": copy.deepcopy(G.BASE.RUNTIME_TMPFS),
            "capabilities": list(G.BASE.RUNTIME_CAPABILITIES),
            "apparmor_profile": G.BASE.APPARMOR_PROFILE,
        },
        "disposable_cleanup": {
            "container_absent": True, "image_tag_absent": True,
            "image_id_absent": True,
        },
        "certification": rehearsal_certification(),
        "environment_review_closure": None, "inner": inner,
        "boundary": copy.deepcopy(G.BOUNDARY),
    }
    body = dict(report)
    body.pop("body_sha256")
    report["body_sha256"] = G.body_sha(body)
    return report


def reseal(report: dict[str, object]) -> None:
    body = dict(report)
    body.pop("body_sha256", None)
    report["body_sha256"] = G.body_sha(body)


class P1CampaignRootfulLivenessGateTests(unittest.TestCase):
    def test_valid_inner_and_rehearsal_report(self) -> None:
        self.assertEqual(G.validate_inner(marker(valid_inner()),
                                          expected_run_id=RUN_ID)["passed"],
                         True)
        report = valid_report()
        self.assertIs(G.validate_report(report), report)
        self.assertFalse(report["passed"])
        self.assertFalse(report["paper_test_admission_candidate"])

    def test_execute_transforms_base_evidence_without_promoting_rehearsal(
            self) -> None:
        raw = valid_report()
        with mock.patch.object(G.BASE, "execute", return_value=raw) as execute:
            report = G.execute(
                "registry.example/hepta/systemd@sha256:" + "b" * 64,
                COMMIT)
        execute.assert_called_once()
        self.assertEqual(report["decision"], "REHEARSAL_ONLY")
        self.assertFalse(report["passed"])
        self.assertIsNone(report["environment_review_closure"])

    def test_inner_adversarial_liveness_and_authority_drift(self) -> None:
        mutations = []
        value = valid_inner()
        value["durable_failure"]["worker_active_at_publish"] = False
        mutations.append(value)
        value = valid_inner()
        value["durable_failure"]["worker_n_restarts"] = 0
        mutations.append(value)
        value = valid_inner()
        value["effective_units_after_fault"][FIXTURE_UNITS[1]][
            "InvocationID"] = "worker-1"
        mutations.append(value)
        value = valid_inner()
        value["boundary"]["direct_broker_access"] = True
        mutations.append(value)
        value = valid_inner()
        value["production_unit_inputs"]["units"][1]["effective"][
            "RestrictAddressFamilies"] = "AF_UNIX AF_NETLINK AF_INET"
        mutations.append(value)
        value = valid_inner()
        value["production_unit_inputs"]["systemd_analyze_verify"]["argv"][-1] = \
            "/run/systemd/system/wrong.target"
        mutations.append(value)
        for index, candidate in enumerate(mutations):
            with self.subTest(case=index), self.assertRaises(G.GateError):
                G.validate_inner(marker(candidate), expected_run_id=RUN_ID)

    def test_outer_rejects_tamper_promotion_and_source_drift(self) -> None:
        cases: list[dict[str, object]] = []
        report = valid_report()
        report["passed"] = True
        reseal(report)
        cases.append(report)
        report = valid_report()
        report["decision"] = "GO"
        report["certification_ready"] = True
        report["certification_blockers"] = []
        report["certification"]["requested"] = True
        report["certification"]["eligible"] = True
        report["production_mode"] = G.PRODUCTION_MODE
        reseal(report)
        cases.append(report)
        report = valid_report()
        report["inner"]["inner_executable"]["file_sha256"] = \
            "sha256:" + "f" * 64
        reseal(report)
        cases.append(report)
        report = valid_report()
        report["container"]["bind_mounts"] = 1
        reseal(report)
        cases.append(report)
        report = valid_report()
        report["body_sha256"] = "sha256:" + "0" * 64
        cases.append(report)
        for index, candidate in enumerate(cases):
            with self.subTest(case=index), self.assertRaises(G.GateError):
                G.validate_report(candidate)

    def test_base_exec_is_rewritten_to_liveness_specific_inner_path(self) -> None:
        calls: list[list[str]] = []

        def fake(arguments: list[str], *, timeout: int = 120,
                 check: bool = True) -> subprocess.CompletedProcess[str]:
            del timeout, check
            calls.append(list(arguments))
            return subprocess.CompletedProcess(arguments, 0, "")

        with mock.patch.object(G.BASE, "command", fake):
            with G.patched_base():
                G.BASE.command([
                    "docker", "exec", "fixture", "python3",
                    G.BASE_INNER_EXECUTABLE])
                self.assertIsNot(G.BASE.command, fake)
            self.assertIs(G.BASE.command, fake)
        self.assertEqual(calls[0][-1], G.INNER_EXECUTABLE)

    def test_create_surface_has_no_network_bind_port_or_device(self) -> None:
        argv = G.create_arguments(IMAGE_ID, "fixture", RUN_ID)
        self.assertIn("none", argv)
        self.assertIn(f"apparmor={G.BASE.APPARMOR_PROFILE}", argv)
        for forbidden in ("--privileged", "--mount", "--volume", "--publish",
                          "--device", "--network=host"):
            self.assertNotIn(forbidden, argv)
        self.assertEqual(argv.count("HEPTA_P1_LIVENESS_DISPOSABLE=1"), 1)

    def test_installed_python_layout_imports_exact_base_module_copy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            for name in (
                "run_hepta_p1_campaign_rootful_liveness_gate.py",
                "run_hepta_p1_dual_domain_rootful_gate.py",
                "hepta_rootful_review_closure_consumer.py",
            ):
                shutil.copy2(ROOT / "scripts" / name, target / name)
            result = subprocess.run(
                [sys.executable,
                 str(target / "run_hepta_p1_campaign_rootful_liveness_gate.py"),
                 "--help"], stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
                timeout=30, env={"PATH": os.environ.get("PATH", "")})
            self.assertEqual(
                result.returncode, 0,
                result.stderr.decode("utf-8", errors="replace"))

    @unittest.skipUnless(shutil.which("systemd-analyze"),
                         "systemd-analyze unavailable")
    def test_systemd_analyze_accepts_fixture_and_production_inputs(self) -> None:
        names = (*UNIT_NAMES,
                 "hepta-p1-liveness-watchdog.service",
                 "hepta-p1-liveness-worker.service",
                 "hepta-p1-liveness-coordinator.service",
                 "hepta-p1-campaign-rootful-liveness.target")
        with tempfile.TemporaryDirectory() as temporary:
            staged: list[str] = []
            for name in names:
                source = (ROOT / "systemd" / name
                          if name in UNIT_NAMES else
                          ROOT / "tests/p1_campaign_rootful_liveness_systemd" /
                          name)
                payload = source.read_bytes()
                for executable in (
                    b"/usr/libexec/hepta-p1-safety-soak-campaign-coordinator",
                    b"/usr/libexec/hepta-p1-safety-soak-observer-worker",
                    b"/usr/libexec/hepta-p1-safety-soak-recorder-worker",
                    b"/usr/libexec/hepta-p1-liveness-daemon",
                ):
                    payload = payload.replace(executable, b"/bin/true")
                destination = Path(temporary) / name
                destination.write_bytes(payload)
                staged.append(str(destination))
            result = subprocess.run(
                [str(shutil.which("systemd-analyze")), "verify", *staged],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, check=False, timeout=30,
                env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C"})
            self.assertEqual(
                result.returncode, 0,
                result.stderr.decode("utf-8", errors="replace"))


if __name__ == "__main__":
    unittest.main()
