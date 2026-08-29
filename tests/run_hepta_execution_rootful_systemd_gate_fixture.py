#!/usr/bin/env python3

"""Offline contract tests for the explicit rootful-systemd outer runner."""

from pathlib import Path
import json
import stat
import sys
import tempfile
from types import SimpleNamespace
import unittest


REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY / "scripts"))
import run_hepta_execution_rootful_systemd_gate as gate  # noqa: E402


PINNED = "ubuntu@sha256:" + "a" * 64


def inner_result(mode: str = "real") -> dict[str, object]:
    return {
        "schema": gate.INNER_SCHEMA,
        "mode": mode,
        "passed": True,
        "checks": {
            "disposable_sentinel": True,
            "provisioned_host_preflight": True,
            "effective_units_static": True,
            "effective_units_no_dropins": True,
            "effective_units_no_generators": True,
            "journal_available": True,
            "nss_numeric_uid_isolation": True,
            "sensitive_host_bind_mounts_absent": True,
            "tmpfiles_default_engaged": True,
            "tmpfiles_idempotent": True,
            "source_credentials_root_0400": True,
            "source_credentials_service_unreadable": True,
            "kill_switch_stable_engaged": True,
            "binary_inputs_stable": True,
            "credential_content_recorded": False,
            "credential_hash_recorded": False,
            "raw_environment_recorded": False,
            "raw_journal_recorded": False,
            "mode_evidence": {
                "simulator_socket_activation": True,
                "dual_socket_shared_identity": True,
                "service_epoch_changed_on_restart": True,
                "fencing_generation_stable_on_restart": True,
                "credential_generation_consumed": True,
                "manager_socket_inode_stable_until_socket_stop": True,
                "socket_inode_recreated_after_socket_restart": True,
                "peer_uid_rejection": True,
                "credential_mount_read_only": True,
                "credential_copy_matches_source": True,
                "private_network_loopback_only": True,
                "killmode_control_group_cleanup": True,
                "real_ibapi_elf_executed": False,
            },
        },
        "platform": {
            "scope": gate.CONTAINER_SCOPE,
            "platform_image_sha256": "a" * 64,
            "systemd_pid1": True,
            "pid1_cgroup_v2_root": True,
        },
        "metrics": {
            "simulator_sha256": "b" * 64,
            "client_probe_sha256": "d" * 64,
            "formal_ibapi_sha256": "c" * 64,
            "executed_ib_path_sha256": "e" * 64,
            "executed_kind": "real_simulator_only_ibapi_not_staged",
        },
        "boundary": {
            "real_ibapi_elf_executed": False,
            "real_broker_connections": 0,
            "paper_orders": 0,
            "live_enabled": False,
            "real_ibapi_broker_unreachable":
                "not_run_requires_separate_authorization",
        },
    }


class RootfulSystemdGateFixtureTests(unittest.TestCase):
    def test_authorization_credential_matches_frozen_profile(self) -> None:
        self.assertEqual(
            gate.paper_authorization_credential(),
            "PAPER-V3:sha256:"
            "49105131b344b86b84e6392ee23a984a9848f84b2243320f9581304bd47afe80")

    def test_base_image_requires_digest(self) -> None:
        self.assertEqual(gate.require_pinned_image(PINNED), PINNED)
        for unsafe in (
                "ubuntu:24.04", "ubuntu@sha256:short",
                "ubuntu@sha256:" + "A" * 64, ""):
            with self.assertRaises(gate.GateError):
                gate.require_pinned_image(unsafe)

    def test_run_contract_has_no_host_or_privileged_escape(self) -> None:
        arguments = gate.docker_run_arguments(
            PINNED, "hepta-rootful-gate-fixture", "real",
            config=Path("/tmp/hepta-empty-docker-config"))
        joined = "\0".join(arguments)
        self.assertIn("--network=none", arguments)
        self.assertIn("--cgroupns=private", arguments)
        self.assertIn("--read-only", arguments)
        self.assertIn("--security-opt=no-new-privileges=true", arguments)
        self.assertIn("--security-opt=apparmor=hepta-systemd-gate", arguments)
        self.assertIn("--cap-drop=ALL", arguments)
        self.assertNotIn("--pid=private", arguments)
        self.assertNotIn("--uts=private", arguments)
        for forbidden in (
                "--privileged", "--network=host", "--pid=host",
                "--cgroupns=host", "/var/run/docker.sock",
                "/sys/fs/cgroup:/sys/fs/cgroup", "--volume", "--mount",
                "apparmor=unconfined"):
            self.assertNotIn(forbidden, joined)

    def test_gate_image_build_is_networkless(self) -> None:
        dockerfile = (REPOSITORY / "tests/rootful_systemd/Dockerfile").read_text(
            encoding="utf-8")
        for forbidden in (
                "apt-get", "apk add", "dnf install", "curl ", "wget ",
                "ldd "):
            self.assertNotIn(forbidden, dockerfile)
        runner = (REPOSITORY /
                  "scripts/run_hepta_execution_rootful_systemd_gate.py").read_text(
                      encoding="utf-8")
        self.assertIn('docker_cli(\n                    "build", "--pull=false", "--network=none"',
                      runner)
        self.assertNotIn("# syntax=", dockerfile)
        self.assertNotIn("--allow-pull", runner)

    def test_disposable_host_sentinel_contract(self) -> None:
        metadata = SimpleNamespace(
            st_uid=0, st_gid=0, st_mode=stat.S_IFREG | 0o400,
            st_nlink=1, st_dev=7, st_ino=11)
        machine_id = "a" * 32
        boot_id = "11111111-2222-3333-4444-555555555555"
        daemon_id = "fcdcfe7d-93a9-435f-ab58-41239e5d1e8c"
        content = gate.disposable_host_sentinel_content(
            machine_id, boot_id, daemon_id)
        result = gate.disposable_host_sentinel_record(
            metadata, content, Path("/fixture"), machine_id, boot_id)
        self.assertTrue(result["root_owned"])
        self.assertEqual((result["device"], result["inode"]), (7, 11))
        with self.assertRaisesRegex(gate.GateError, "sentinel contract"):
            gate.disposable_host_sentinel_record(
                metadata, b"unsafe\n", Path("/fixture"), machine_id, boot_id)
        linked = SimpleNamespace(**vars(metadata))
        linked.st_nlink = 2
        with self.assertRaisesRegex(gate.GateError, "sentinel contract"):
            gate.disposable_host_sentinel_record(
                linked, content, Path("/fixture"), machine_id, boot_id)
        with self.assertRaisesRegex(gate.GateError, "sentinel contract"):
            gate.disposable_host_sentinel_record(
                metadata, content, Path("/fixture"), "b" * 32, boot_id)

    def test_apparmor_profile_must_be_unique_and_enforcing(self) -> None:
        result = gate.apparmor_enforcing_record(
            b"unrelated (complain)\nhepta-systemd-gate (enforce)\n")
        self.assertEqual(result, {
            "profile": "hepta-systemd-gate", "enforcing": True})
        for unsafe in (
                b"hepta-systemd-gate (complain)\n",
                b"hepta-systemd-gate (enforce)\n"
                b"hepta-systemd-gate (enforce)\n",
                b"\xff"):
            with self.assertRaises(gate.GateError):
                gate.apparmor_enforcing_record(unsafe)

    def test_unknown_run_variant_fails_closed(self) -> None:
        with self.assertRaisesRegex(gate.GateError, "unknown variant"):
            gate.docker_run_arguments(
                PINNED, "hepta-rootful-gate-fixture", "paper",
                config=Path("/tmp/hepta-empty-docker-config"))

    def test_inner_result_exact_contract_passes(self) -> None:
        payload = json.dumps(inner_result(), separators=(",", ":"))
        parsed = gate.parse_inner_result(
            "HEPTA_ROOTFUL_SYSTEMD_GATE_RESULT=" + payload + "\n", "real")
        self.assertTrue(parsed["passed"])

    def test_inner_result_duplicate_marker_fails_closed(self) -> None:
        marker = "HEPTA_ROOTFUL_SYSTEMD_GATE_RESULT=" + json.dumps(
            inner_result()) + "\n"
        with self.assertRaisesRegex(gate.GateError, "exactly one"):
            gate.parse_inner_result(marker + marker, "real")

    def test_inner_result_extra_stdout_fails_closed(self) -> None:
        marker = "HEPTA_ROOTFUL_SYSTEMD_GATE_RESULT=" + json.dumps(
            inner_result()) + "\n"
        with self.assertRaisesRegex(gate.GateError, "exactly one"):
            gate.parse_inner_result("unexpected\n" + marker, "real")

    def test_inner_result_boundary_drift_fails_closed(self) -> None:
        result = inner_result()
        result["boundary"]["paper_orders"] = 1  # type: ignore[index]
        marker = "HEPTA_ROOTFUL_SYSTEMD_GATE_RESULT=" + json.dumps(result)
        with self.assertRaisesRegex(gate.GateError, "no-order"):
            gate.parse_inner_result(marker, "real")

    def test_inner_result_extra_field_fails_closed(self) -> None:
        result = inner_result()
        result["unexpected"] = True
        marker = "HEPTA_ROOTFUL_SYSTEMD_GATE_RESULT=" + json.dumps(result)
        with self.assertRaisesRegex(gate.GateError, "field set"):
            gate.parse_inner_result(marker, "real")

    def test_inner_result_nested_drift_fails_closed(self) -> None:
        result = inner_result()
        result["checks"]["binary_inputs_stable"] = False  # type: ignore[index]
        marker = "HEPTA_ROOTFUL_SYSTEMD_GATE_RESULT=" + json.dumps(result)
        with self.assertRaisesRegex(gate.GateError, "check values"):
            gate.parse_inner_result(marker, "real")

    def test_inner_result_missing_field_fails_closed(self) -> None:
        result = inner_result()
        del result["platform"]
        marker = "HEPTA_ROOTFUL_SYSTEMD_GATE_RESULT=" + json.dumps(result)
        with self.assertRaisesRegex(gate.GateError, "field set"):
            gate.parse_inner_result(marker, "real")

    def test_atomic_report_is_0600_and_round_trips(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-rootful-report-fixture-") as temporary:
            report = Path(temporary) / "report.json"
            payload = {"schema": gate.SCHEMA, "passed": False}
            gate.atomic_report(report, payload)
            self.assertEqual(stat.S_IMODE(report.stat().st_mode), 0o600)
            self.assertEqual(
                json.loads(report.read_text(encoding="utf-8")), payload)
            self.assertFalse(any(
                path.name.startswith(".report.json.tmp-")
                for path in report.parent.iterdir()))

    def test_pre_container_failure_reports_exact_zero_boundary(self) -> None:
        progress = gate.GateProgress(phase="disposable_host_validation")
        report = gate.failure_report(gate.GateError("sentinel missing"), progress)
        self.assertEqual(report["failure_stage"], {
            "phase": "disposable_host_validation",
            "docker_api_touched": False,
            "image_build_started": False,
            "container_start_attempted": False,
            "completed_variants": [],
        })
        self.assertEqual(report["boundary"]["real_ibapi_elf_executed"], False)
        self.assertEqual(report["boundary"]["real_broker_connections"], 0)
        self.assertEqual(report["boundary"]["paper_orders"], 0)
        self.assertEqual(report["boundary"]["live_enabled"], False)

    def test_post_container_failure_keeps_runtime_boundary_unknown(self) -> None:
        progress = gate.GateProgress(
            phase="container_start_sandbox",
            docker_api_touched=True,
            image_build_started=True,
            container_start_attempted=True,
            completed_variants=["real"],
        )
        report = gate.failure_report(gate.GateError("container failed"), progress)
        self.assertEqual(report["failure_stage"]["completed_variants"], ["real"])
        self.assertEqual(
            report["boundary"]["real_ibapi_elf_executed"], "unknown")
        self.assertEqual(
            report["boundary"]["real_broker_connections"], "unknown")
        self.assertEqual(report["boundary"]["paper_orders"], "unknown")
        self.assertEqual(report["boundary"]["live_enabled"], "unknown")


if __name__ == "__main__":
    unittest.main(verbosity=2)
