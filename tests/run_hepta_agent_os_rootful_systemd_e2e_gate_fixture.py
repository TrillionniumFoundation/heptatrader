#!/usr/bin/env python3

"""Rootless fixture tests for the offline four-UID Agent OS gate."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock


ROOT = Path(__file__).resolve(strict=True).parents[1]
MODULE_PATH = ROOT / "scripts/run_hepta_agent_os_rootful_systemd_e2e_gate.py"
SPEC = importlib.util.spec_from_file_location(
    "run_hepta_agent_os_rootful_systemd_e2e_gate_under_test", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("cannot load Agent OS E2E gate module")
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

BROKER_PROBE_PATH = (
    ROOT / "tests/agent_os_rootful_systemd/"
    "hepta_broker_network_rootful_probe.py")
BROKER_PROBE_SPEC = importlib.util.spec_from_file_location(
    "hepta_broker_network_rootful_probe_under_test", BROKER_PROBE_PATH)
if BROKER_PROBE_SPEC is None or BROKER_PROBE_SPEC.loader is None:
    raise RuntimeError("cannot load broker rootful probe module")
BROKER_PROBE = importlib.util.module_from_spec(BROKER_PROBE_SPEC)
sys.modules[BROKER_PROBE_SPEC.name] = BROKER_PROBE
BROKER_PROBE_SPEC.loader.exec_module(BROKER_PROBE)


def pinned_reference() -> str:
    return "registry.example/hepta/systemd@sha256:" + "a" * 64


def base_record(labels: dict[str, str]) -> dict[str, object]:
    return {
        "Id": "sha256:" + "b" * 64,
        "Os": "linux",
        "Architecture": "amd64",
        "RepoDigests": [
            "registry.example/hepta/systemd@sha256:" + "a" * 64],
        "Config": {"Labels": labels, "OnBuild": None},
    }


def reviewed_labels() -> dict[str, str]:
    return dict(MODULE.REVIEWED_BASE_LABELS)


def reviewed_provenance_document(
        record: dict[str, object],
        *,
        reference: str | None = None,
        decision: str = "GO",
) -> dict[str, object]:
    labels = record["Config"]["Labels"]
    return {
        "schema": MODULE.REVIEWED_BASE_PROVENANCE_SCHEMA,
        "decision": decision,
        "issued_at_ms": int(time.time() * 1000) - 1000,
        "expires_at_ms": int(time.time() * 1000) + 60_000,
        "image_id": record["Id"],
        "repo_digest": reference or pinned_reference(),
        "labels_sha256": MODULE.canonical_base_labels_sha256(labels),
    }


def write_reviewed_provenance(
        path: Path,
        document: dict[str, str],
) -> str:
    contents = (
        json.dumps(document, sort_keys=True, separators=(",", ":")) +
        "\n"
    ).encode("ascii")
    path.write_bytes(contents)
    return "sha256:" + hashlib.sha256(contents).hexdigest()


def buildkit_reference() -> str:
    return "registry.example/hepta/buildkit@sha256:" + "c" * 64


def buildkit_record() -> dict[str, object]:
    return {
        "Id": "sha256:" + "d" * 64,
        "Os": "linux",
        "Architecture": "amd64",
        "RepoDigests": [buildkit_reference()],
        "Config": {
            "Entrypoint": ["buildkitd"],
            "Cmd": None,
            "Labels": {"org.mobyproject.buildkit.fixture": "true"},
            "OnBuild": None,
            "Volumes": None,
            "ExposedPorts": None,
        },
    }


def builder_provenance_document(
        record: dict[str, object] | None = None,
        *,
        decision: str = "GO",
) -> dict[str, object]:
    inspected = record or buildkit_record()
    return {
        "schema": MODULE.REVIEWED_BUILDER_PROVENANCE_SCHEMA,
        "decision": decision,
        "issued_at_ms": int(time.time() * 1000) - 1000,
        "expires_at_ms": int(time.time() * 1000) + 60_000,
        "image_id": inspected["Id"],
        "repo_digest": buildkit_reference(),
        "config_sha256":
            MODULE.canonical_json_sha256(inspected["Config"]),
        "buildkit_version": "v0.26.2",
        "buildx_version": "0.30.1",
        "buildx_binary_sha256": "sha256:" + "e" * 64,
        "docker_server_version": "29.1.3",
        "docker_server_api_version": "1.52",
        "docker_server_git_commit": "29.1.3-0ubuntu3",
    }


def write_builder_provenance(
        path: Path,
        document: dict[str, str],
) -> str:
    contents = (
        json.dumps(document, sort_keys=True, separators=(",", ":")) +
        "\n"
    ).encode("ascii")
    path.write_bytes(contents)
    return "sha256:" + hashlib.sha256(contents).hexdigest()


def apparmor_provenance_document(
        *,
        profile_sha256: str = "sha256:" + "c" * 64,
        raw_sha256: str = "sha256:" + "d" * 64,
        raw_abi: str = "v5",
        decision: str = "GO",
) -> dict[str, object]:
    return {
        "schema": MODULE.REVIEWED_APPARMOR_PROVENANCE_SCHEMA,
        "decision": decision,
        "issued_at_ms": int(time.time() * 1000) - 1000,
        "expires_at_ms": int(time.time() * 1000) + 60_000,
        "profile": MODULE.APPARMOR_PROFILE,
        "policy_source_sha256": "sha256:" + "e" * 64,
        "profile_sha256": profile_sha256,
        "raw_sha256": raw_sha256,
        "raw_abi": raw_abi,
    }


def write_apparmor_provenance(
        path: Path,
        document: dict[str, object],
) -> str:
    contents = (
        json.dumps(document, sort_keys=True, separators=(",", ":")) +
        "\n"
    ).encode("ascii")
    path.write_bytes(contents)
    return "sha256:" + hashlib.sha256(contents).hexdigest()


def write_policy_scalar(path: Path, value: str) -> None:
    if path.exists():
        path.chmod(0o644)
    path.write_text(value + "\n", encoding="ascii")
    path.chmod(0o444)


def create_apparmor_policy_tree(
        root: Path,
        *,
        raw_id: str = "73",
        mode: str = "enforce",
        attach: str = MODULE.APPARMOR_ATTACH,
        learning_count: str = "0",
        profile_sha256: str = "c" * 64,
        raw_sha256: str = "d" * 64,
        raw_abi: str = "v5",
        entry_name: str | None = None,
) -> Path:
    profiles = root / "profiles"
    raw_root = root / "raw_data"
    profiles.mkdir(mode=0o755)
    raw_root.mkdir(mode=0o755)
    profiles.chmod(0o755)
    raw_root.chmod(0o755)
    entry = profiles / (
        entry_name or f"{MODULE.APPARMOR_PROFILE}.{raw_id}")
    entry.mkdir(mode=0o755)
    entry.chmod(0o755)
    for name, value in (
            ("name", MODULE.APPARMOR_PROFILE),
            ("mode", mode),
            ("attach", attach),
            ("learning_count", learning_count),
            ("sha256", profile_sha256)):
        write_policy_scalar(entry / name, value)
    (entry / "raw_data").symlink_to(
        f"../../raw_data/{raw_id}/raw_data")
    (entry / "raw_sha256").symlink_to(
        f"../../raw_data/{raw_id}/sha256")
    (entry / "raw_abi").symlink_to(
        f"../../raw_data/{raw_id}/abi")
    raw_directory = raw_root / raw_id
    raw_directory.mkdir(mode=0o755)
    raw_directory.chmod(0o755)
    (raw_directory / "raw_data").write_bytes(b"fixture-policy-binary")
    (raw_directory / "raw_data").chmod(0o444)
    write_policy_scalar(raw_directory / "sha256", raw_sha256)
    write_policy_scalar(raw_directory / "abi", raw_abi)
    return entry


def load_fixture_apparmor_provenance(
        temporary: Path,
        *,
        profile_sha256: str = "sha256:" + "c" * 64,
        raw_sha256: str = "sha256:" + "d" * 64,
        raw_abi: str = "v5",
) -> MODULE.ReviewedAppArmorProvenance:
    path = temporary / "apparmor-provenance.json"
    document = apparmor_provenance_document(
        profile_sha256=profile_sha256,
        raw_sha256=raw_sha256,
        raw_abi=raw_abi,
    )
    digest = write_apparmor_provenance(path, document)
    return MODULE.load_reviewed_apparmor_provenance(path, digest)


def docker_apparmor_namespace_provenance_document(
        *,
        daemon_id: str = "fixture:daemon:ID",
        daemon_pid: int = 4242,
        start_time_ticks: int = 123456,
        boot_id: str = "11111111-2222-3333-4444-555555555555",
) -> dict[str, object]:
    return {
        "schema":
            MODULE.REVIEWED_DOCKER_APPARMOR_NAMESPACE_PROVENANCE_SCHEMA,
        "decision": "GO",
        "issued_at_ms": int(time.time() * 1000) - 1000,
        "expires_at_ms": int(time.time() * 1000) + 60_000,
        "docker_daemon_id": daemon_id,
        "docker_daemon_pid": daemon_pid,
        "docker_daemon_start_time_ticks": start_time_ticks,
        "host_boot_id": boot_id,
        "host_namespace_name": "root",
        "host_namespace_level": 0,
        "host_namespace_stacked": False,
        "daemon_namespace_name": "root",
        "daemon_namespace_level": 0,
        "daemon_namespace_stacked": False,
    }


def load_fixture_docker_namespace_provenance(
        temporary: Path,
        document: dict[str, object] | None = None,
) -> MODULE.ReviewedDockerAppArmorNamespaceProvenance:
    path = temporary / "docker-apparmor-namespace.json"
    contents = (
        json.dumps(
            document or docker_apparmor_namespace_provenance_document(),
            sort_keys=True,
            separators=(",", ":"),
        ) + "\n"
    ).encode("ascii")
    path.write_bytes(contents)
    digest = "sha256:" + hashlib.sha256(contents).hexdigest()
    return MODULE.load_reviewed_docker_apparmor_namespace_provenance(
        path, digest)


def valid_inner_result() -> dict[str, object]:
    def phase(
            gateway: int, simulator: int, socket_base: int
            ) -> dict[str, int]:
        return {
            "gateway_pid": gateway,
            "simulator_pid": simulator,
            "tool_socket_inode": socket_base,
            "supervisor_socket_inode": socket_base + 1,
            "execution_socket_inode": socket_base + 2,
            "events_socket_inode": socket_base + 3,
        }

    return {
        "schema": MODULE.INNER_SCHEMA,
        "profile": "two-domain-agent-gateway-execution-watch",
        "passed": True,
        "identities": {
            "agent_uid": 2004,
            "gateway_uid": 2001,
            "simulator_execution_uid": 2002,
            "ib_execution_uid_reserved_not_started": 2003,
            "trust_domains": {
                "codex-a": {
                    "gateway_uid": 2101,
                    "agent_uid": 2104,
                    "execution_uid": 2111,
                    "reader_uid": 2121,
                },
                "openclaw-b": {
                    "gateway_uid": 2102,
                    "agent_uid": 2105,
                    "execution_uid": 2112,
                    "reader_uid": 2122,
                },
            },
        },
        "checks": {
            "systemd_pid1": True,
            "network_none_loopback_only": True,
            "no_host_mount_or_docker_socket": True,
            "fixed_identity_isolation": True,
            "two_domain_execution_identity_isolation": True,
            "two_domain_execution_socket_cross_access_denied": True,
            "two_domain_execution_authorities_started_and_stopped": True,
            "two_domain_runtime_configs_root_owned_regular": True,
            "two_domain_agent_host_dropins_isolated": True,
            "two_agent_gateway_execution_watch_chains": True,
            "two_domain_uid_config_cross_rejected": True,
            "two_domain_token_cross_rejected": True,
            "two_domain_account_binding_cross_rejected": True,
            "two_domain_execution_binding_cross_rejected": True,
            "two_domain_gateway_socket_cross_rejected": True,
            "two_domain_watch_restart_fails_closed": True,
            "two_domain_collector_typed_terminal": True,
            "two_domain_watch_sessions_revoked": True,
            "two_domain_custodian_reader_identity_isolation": True,
            "two_domain_watch_environments_root_owned_private": True,
            "two_domain_custodian_services_monitored": True,
            "two_domain_custodian_reconcile_timers_enabled": True,
            "two_domain_custodian_rotation_bound": True,
            "two_domain_custodian_sigkill_crash_closed": True,
            "two_domain_custodian_closure_receipts_exact": True,
            "two_domain_custodian_authority_residue_absent": True,
            "uid1000_observer_reads_uid2101_proc_stat": True,
            "broker_network_policy_active": True,
            "broker_watchdog_timeout_observed": True,
            "broker_watchdog_timeout_stop_contract": True,
            "broker_watchdog_gateway_binds_to_stop": True,
            "broker_watchdog_deny_all_persisted": True,
            "broker_watchdog_watch_terminalized": True,
            "broker_watchdog_clean_restart": True,
            "agent_ib_ports_denied": True,
            "gateway_ib_ports_denied": True,
            "ib_execution_ib_ports_denied": True,
            "agent_model_egress_preserved": True,
            "ib_paper_surface_absent": True,
            "installation_preflight": True,
            "simulator_dual_socket_activation": True,
            "gateway_dual_socket_activation": True,
            "root_watch_bootstrap": True,
            "uid_2004_mcp_initialize": True,
            "uid_2004_exact_watch_tool_list": True,
            "uid_2004_read_only_probes": True,
            "gateway_service_socket_reactivation": True,
            "simulator_service_socket_reactivation": True,
            "socket_stop_removes_paths": True,
            "socket_restart_recreates_paths": True,
            "watch_restart_fails_closed": True,
            "runtime_preflight_after_restart": True,
            "watch_session_revoked": True,
            "all_runtime_paths_removed": True,
        },
        "lifecycle": {
            "watch_generation": 1,
            "initial": phase(101, 102, 1001),
            "service_reactivation": phase(201, 202, 1001),
            "socket_reactivation": phase(301, 302, 2001),
            "trust_domains": {
                "codex-a": {
                    "watch_generation": 11,
                    "custodian_pid": 391,
                    "reader_owner_pid": 392,
                    "custodian_crash_generation": 2,
                    "custodian_restart_count": 1,
                    "closure_receipt_count": 3,
                    **phase(401, 402, 3001),
                },
                "openclaw-b": {
                    "watch_generation": 12,
                    "custodian_pid": 491,
                    "reader_owner_pid": 492,
                    "custodian_crash_generation": 2,
                    "custodian_restart_count": 1,
                    "closure_receipt_count": 1,
                    **phase(501, 502, 4001),
                },
            },
        },
        "boundary": {
            "container_network": "none",
            "real_broker_connections": 0,
            "paper_orders": 0,
            "paper_authorized": False,
            "live_authorized": False,
            "ib_adapter_staged": False,
            "host_hepta_units_started": False,
            "host_bind_mounts": 0,
            "raw_session_token_recorded": False,
        },
    }


def valid_inspect(
        container_id: str, name: str, image_id: str, run_id: str
        ) -> dict[str, object]:
    return {
        "Id": container_id,
        "Name": "/" + name,
        "Image": image_id,
        "AppArmorProfile": MODULE.APPARMOR_PROFILE,
        "Mounts": [],
        "HostConfig": {
            "Privileged": False,
            "ReadonlyRootfs": True,
            "NetworkMode": "none",
            "Binds": None,
            "PortBindings": {},
            "PublishAllPorts": False,
            "PidMode": "",
            "IpcMode": "private",
            "CgroupnsMode": "private",
            "Tmpfs": dict(MODULE.RUNTIME_TMPFS),
            "CapDrop": ["ALL"],
            "CapAdd": sorted(MODULE.RUNTIME_CAPABILITIES),
            "SecurityOpt": [
                "no-new-privileges=true",
                f"apparmor={MODULE.APPARMOR_PROFILE}",
            ],
            "Devices": [],
            "DeviceRequests": [],
            "VolumesFrom": None,
            "Links": None,
            "ExtraHosts": None,
            "Dns": [],
            "CgroupParent": "",
            "RestartPolicy": {"Name": "no"},
        },
        "Config": {
            "User": "0:0",
            "Labels": {"io.hepta.purpose": MODULE.PURPOSE},
            "Env": [
                "HEPTA_AGENT_OS_E2E_DISPOSABLE=1",
                f"HEPTA_AGENT_OS_E2E_RUN_ID={run_id}",
            ],
        },
    }


def valid_base_holder_inspect(
        container_id: str, name: str, image_id: str, run_id: str
        ) -> dict[str, object]:
    return {
        "Id": container_id,
        "Name": "/" + name,
        "Image": image_id,
        "Mounts": [],
        "HostConfig": {
            "NetworkMode": "none",
            "ReadonlyRootfs": True,
            "Privileged": False,
            "Binds": None,
            "Tmpfs": None,
            "VolumesFrom": None,
            "Devices": [],
            "DeviceRequests": None,
            "PortBindings": {},
            "PublishAllPorts": False,
        },
        "Config": {
            "Image": image_id,
            "Volumes": None,
            "Entrypoint": ["/bin/true"],
            "Labels": {
                **reviewed_labels(),
                **MODULE.base_holder_labels(run_id),
            },
        },
    }


def valid_builder_volume_inspect(
        names: dict[str, str], run_id: str, image_id: str
        ) -> dict[str, object]:
    return {
        "Name": names["volume"],
        "Driver": "local",
        "Scope": "local",
        "Mountpoint": "/var/lib/docker/volumes/" + names["volume"] + "/_data",
        "Options": None,
        "Labels": MODULE.isolated_builder_labels(
            run_id, names["builder"], image_id,
            role=MODULE.BUILDER_STATE_ROLE),
    }


def valid_builder_container_inspect(
        container_id: str,
        names: dict[str, str],
        run_id: str,
        image: dict[str, object],
        *,
        running: bool = False,
) -> dict[str, object]:
    ownership = MODULE.isolated_builder_labels(
        run_id, names["builder"], image["id"],
        role=MODULE.BUILDER_DAEMON_ROLE)
    return {
        "Id": container_id,
        "Name": "/" + names["container"],
        "Image": image["id"],
        "State": {"Running": running},
        "Mounts": [{
            "Type": "volume",
            "Name": names["volume"],
            "Destination": MODULE.BUILDKIT_STATE_DIRECTORY,
            "Driver": "local",
            "RW": True,
        }],
        "HostConfig": {
            "NetworkMode": "none",
            "Privileged": True,
            "AutoRemove": False,
            "Init": True,
            "ReadonlyRootfs": False,
            "RestartPolicy": {"Name": "no", "MaximumRetryCount": 0},
            "Binds": None,
            "Tmpfs": None,
            "VolumesFrom": None,
            "Devices": [],
            "DeviceRequests": None,
            "PortBindings": {},
            "PublishAllPorts": False,
        },
        "Config": {
            "Image": image["bare_id"],
            "Labels": {**image["config_labels"], **ownership},
        },
    }


class AgentOsRootfulE2EGateFixture(unittest.TestCase):
    def test_pinned_image_is_mandatory(self) -> None:
        self.assertEqual(
            MODULE.require_pinned_image(pinned_reference()),
            pinned_reference())
        for invalid in (
                "debian:bookworm", "debian@sha256:" + "a" * 63,
                "debian@sha256:" + "A" * 64, "sha256:" + "a" * 64,
                "Registry.example/base@sha256:" + "a" * 64,
                "registry.example/base@tag@sha256:" + "a" * 64):
            with self.assertRaises(MODULE.GateError):
                MODULE.require_pinned_image(invalid)

    def test_apparmor_policy_tree_is_content_attested_and_go_bound(
            self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-apparmor-policy-fixture-") as temporary:
            root = Path(temporary) / "policy"
            root.mkdir()
            create_apparmor_policy_tree(
                root,
                raw_id="73",
                entry_name=f"{MODULE.APPARMOR_PROFILE}.91",
            )
            provenance = load_fixture_apparmor_provenance(Path(temporary))
            record = MODULE._validate_apparmor_policy_tree(
                provenance,
                policy_root=root,
                expected_uid=os.geteuid(),
                expected_gid=os.getegid(),
                expected_symlink_mode=0o777,
            )
            self.assertEqual(record["profile"], MODULE.APPARMOR_PROFILE)
            self.assertEqual(record["mode"], "enforce")
            self.assertEqual(record["attach"], MODULE.APPARMOR_ATTACH)
            self.assertEqual(record["learning_count"], 0)
            self.assertEqual(record["profile_sha256"], "sha256:" + "c" * 64)
            self.assertEqual(record["raw_sha256"], "sha256:" + "d" * 64)
            self.assertEqual(record["raw_abi"], "v5")
            self.assertEqual(record["raw_data_id"], "73")
            self.assertEqual(
                record["policy_entry"],
                f"{MODULE.APPARMOR_PROFILE}.91",
            )
            self.assertTrue(record["policy_content_attested"])
            self.assertEqual(
                record["reviewed_provenance"]["decision"], "GO")

    def test_apparmor_policy_fields_and_provenance_fail_closed(
            self) -> None:
        cases = (
            ("mode", "complain"),
            ("attach", "other-profile"),
            ("learning_count", "1"),
            ("sha256", "f" * 63),
        )
        for field, value in cases:
            with self.subTest(field=field), tempfile.TemporaryDirectory(
                    prefix="hepta-apparmor-field-fixture-") as temporary:
                temporary_path = Path(temporary)
                root = temporary_path / "policy"
                root.mkdir()
                entry = create_apparmor_policy_tree(root)
                write_policy_scalar(entry / field, value)
                provenance = load_fixture_apparmor_provenance(temporary_path)
                with self.assertRaises(MODULE.GateError):
                    MODULE._validate_apparmor_policy_tree(
                        provenance,
                        policy_root=root,
                        expected_uid=os.geteuid(),
                        expected_gid=os.getegid(),
                        expected_symlink_mode=0o777,
                    )

        with tempfile.TemporaryDirectory(
                prefix="hepta-apparmor-binding-fixture-") as temporary:
            temporary_path = Path(temporary)
            root = temporary_path / "policy"
            root.mkdir()
            create_apparmor_policy_tree(root)
            for field, value in (
                    ("profile_sha256", "sha256:" + "1" * 64),
                    ("raw_sha256", "sha256:" + "2" * 64),
                    ("raw_abi", "v6")):
                with self.subTest(binding=field):
                    arguments = {
                        "profile_sha256": "sha256:" + "c" * 64,
                        "raw_sha256": "sha256:" + "d" * 64,
                        "raw_abi": "v5",
                    }
                    arguments[field] = value
                    provenance = load_fixture_apparmor_provenance(
                        temporary_path, **arguments)
                    with self.assertRaisesRegex(
                            MODULE.GateError, "does not bind"):
                        MODULE._validate_apparmor_policy_tree(
                            provenance,
                            policy_root=root,
                            expected_uid=os.geteuid(),
                            expected_gid=os.getegid(),
                            expected_symlink_mode=0o777,
                        )

    def test_apparmor_policy_rejects_duplicates_symlink_and_inventory_drift(
            self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-apparmor-duplicate-fixture-") as temporary:
            temporary_path = Path(temporary)
            root = temporary_path / "policy"
            root.mkdir()
            create_apparmor_policy_tree(root)
            duplicate = root / "profiles/duplicate.74"
            duplicate.mkdir(mode=0o755)
            duplicate.chmod(0o755)
            write_policy_scalar(duplicate / "name", MODULE.APPARMOR_PROFILE)
            provenance = load_fixture_apparmor_provenance(temporary_path)
            with self.assertRaisesRegex(
                    MODULE.GateError, "not uniquely present"):
                MODULE._validate_apparmor_policy_tree(
                    provenance,
                    policy_root=root,
                    expected_uid=os.geteuid(),
                    expected_gid=os.getegid(),
                    expected_symlink_mode=0o777,
                )

        for field, target in (
                ("raw_data", "../../raw_data/74/raw_data"),
                ("raw_sha256", "../../raw_data/73/other"),
                ("raw_abi", "../../raw_data/74/abi")):
            with self.subTest(field=field), tempfile.TemporaryDirectory(
                    prefix="hepta-apparmor-symlink-fixture-") as temporary:
                temporary_path = Path(temporary)
                root = temporary_path / "policy"
                root.mkdir()
                entry = create_apparmor_policy_tree(root)
                (entry / field).unlink()
                (entry / field).symlink_to(target)
                provenance = load_fixture_apparmor_provenance(temporary_path)
                with self.assertRaises(MODULE.GateError):
                    MODULE._validate_apparmor_policy_tree(
                        provenance,
                        policy_root=root,
                        expected_uid=os.geteuid(),
                        expected_gid=os.getegid(),
                        expected_symlink_mode=0o777,
                    )

        with tempfile.TemporaryDirectory(
                prefix="hepta-apparmor-drift-fixture-") as temporary:
            temporary_path = Path(temporary)
            root = temporary_path / "policy"
            root.mkdir()
            create_apparmor_policy_tree(root)
            provenance = load_fixture_apparmor_provenance(temporary_path)
            original = MODULE._policy_entry_inventory
            calls = 0

            def inventory_with_drift(*args: object, **kwargs: object):
                nonlocal calls
                result = original(*args, **kwargs)
                calls += 1
                if calls == 1:
                    drift = root / "profiles/drift.75"
                    drift.mkdir(mode=0o755)
                    drift.chmod(0o755)
                    write_policy_scalar(drift / "name", "drift")
                return result

            with mock.patch.object(
                    MODULE, "_policy_entry_inventory",
                    side_effect=inventory_with_drift), \
                    self.assertRaisesRegex(
                        MODULE.GateError, "inventory changed"):
                MODULE._validate_apparmor_policy_tree(
                    provenance,
                    policy_root=root,
                    expected_uid=os.geteuid(),
                    expected_gid=os.getegid(),
                    expected_symlink_mode=0o777,
                )

    def test_apparmor_provenance_document_is_strict_and_always_required(
            self) -> None:
        with self.assertRaisesRegex(MODULE.GateError, "GO is required"):
            MODULE.reviewed_apparmor_provenance_from_arguments(None, None)
        with self.assertRaisesRegex(MODULE.GateError, "supplied together"):
            MODULE.reviewed_apparmor_provenance_from_arguments(
                Path("/not-read"), None)
        with tempfile.TemporaryDirectory(
                prefix="hepta-apparmor-provenance-fixture-") as temporary:
            path = Path(temporary) / "apparmor.json"
            document: dict[str, object] = apparmor_provenance_document()
            digest = write_apparmor_provenance(path, document)
            accepted = MODULE.load_reviewed_apparmor_provenance(path, digest)
            self.assertEqual(accepted.profile, MODULE.APPARMOR_PROFILE)
            with self.assertRaisesRegex(MODULE.GateError, "digest mismatch"):
                MODULE.load_reviewed_apparmor_provenance(
                    path, "sha256:" + "0" * 64)

            for field, value in (
                    ("decision", "NO-GO"),
                    ("profile", "docker-default"),
                    ("policy_source_sha256", "e" * 64),
                    ("profile_sha256", ["sha256:" + "c" * 64]),
                    ("raw_sha256", "sha256:" + "D" * 64),
                    ("raw_abi", "5")):
                changed = dict(document)
                changed[field] = value
                changed_digest = write_apparmor_provenance(path, changed)
                with self.subTest(field=field), self.assertRaises(
                        MODULE.GateError):
                    MODULE.load_reviewed_apparmor_provenance(
                        path, changed_digest)

            changed = dict(document)
            changed["extra"] = "forbidden"
            changed_digest = write_apparmor_provenance(path, changed)
            with self.assertRaisesRegex(
                    MODULE.GateError, "field inventory"):
                MODULE.load_reviewed_apparmor_provenance(
                    path, changed_digest)

            serialized = json.dumps(
                document, sort_keys=True, separators=(",", ":"))
            duplicate = (
                '{"schema":"' + MODULE.REVIEWED_APPARMOR_PROVENANCE_SCHEMA +
                '",' + serialized[1:] + "\n"
            ).encode("ascii")
            path.write_bytes(duplicate)
            duplicate_digest = (
                "sha256:" + hashlib.sha256(duplicate).hexdigest())
            with self.assertRaisesRegex(MODULE.GateError, "duplicate field"):
                MODULE.load_reviewed_apparmor_provenance(
                    path, duplicate_digest)

    def test_kernel_aafs_anchor_rejects_ordinary_directory_and_fake_magic_link(
            self) -> None:
        for fake_link in (False, True):
            with self.subTest(fake_link=fake_link), tempfile.TemporaryDirectory(
                    prefix="hepta-fake-aafs-fixture-") as temporary:
                security_root = Path(temporary) / "security"
                control_root = security_root / "apparmor"
                security_root.mkdir()
                control_root.mkdir()
                policy_target = control_root / "fake-policy"
                policy_target.mkdir()
                policy_link = control_root / "policy"
                if fake_link:
                    policy_link.symlink_to("apparmorfs:[123]")
                else:
                    policy_link.mkdir()
                with mock.patch.object(
                        MODULE, "APPARMOR_SECURITY_ROOT", security_root), \
                        mock.patch.object(
                            MODULE, "APPARMOR_CONTROL_ROOT", control_root), \
                        mock.patch.object(
                            MODULE, "APPARMOR_POLICY_MAGIC_LINK", policy_link), \
                        self.assertRaises(MODULE.GateError):
                    MODULE._open_apparmor_kernel_anchor()

    def test_docker_daemon_namespace_provenance_is_strict_and_bound(
            self) -> None:
        with self.assertRaisesRegex(MODULE.GateError, "GO is required"):
            MODULE.reviewed_docker_apparmor_namespace_provenance_from_arguments(
                None, None)
        with tempfile.TemporaryDirectory(
                prefix="hepta-docker-aa-namespace-fixture-") as temporary:
            temporary_path = Path(temporary)
            provenance = load_fixture_docker_namespace_provenance(
                temporary_path)
            apparmor_record = {
                "kernel_anchor": {
                    "namespace": {
                        "name": "root",
                        "level": 0,
                        "stacked": False,
                        "field_metadata_sha256": "sha256:" + "a" * 64,
                    },
                },
            }
            process = {
                "pid": 4242,
                "start_time_ticks": 123456,
                "comm": "dockerd",
                "process_inode": 999,
                "stat_metadata_sha256": "sha256:" + "b" * 64,
            }
            with mock.patch.object(
                    MODULE, "_docker_daemon_process_record",
                    return_value=process), \
                    mock.patch.object(
                        MODULE, "_current_boot_id",
                        return_value=
                            "11111111-2222-3333-4444-555555555555"), \
                    mock.patch.object(
                        MODULE, "_docker_daemon_id",
                        return_value="fixture:daemon:ID"):
                record = MODULE.validate_docker_apparmor_namespace_binding(
                    provenance, apparmor_record)
            self.assertTrue(record["same_apparmor_namespace_attested"])
            self.assertEqual(record["host_namespace"],
                             record["daemon_namespace"])

            with mock.patch.object(
                    MODULE, "_docker_daemon_process_record",
                    return_value=process), \
                    mock.patch.object(
                        MODULE, "_current_boot_id",
                        return_value=
                            "11111111-2222-3333-4444-555555555555"), \
                    mock.patch.object(
                        MODULE, "_docker_daemon_id",
                        return_value="other:daemon:ID"), \
                    self.assertRaisesRegex(MODULE.GateError, "drifted"):
                MODULE.validate_docker_apparmor_namespace_binding(
                    provenance, apparmor_record)

            for field, value in (
                    ("decision", "NO-GO"),
                    ("docker_daemon_pid", True),
                    ("host_namespace_name", "child"),
                    ("daemon_namespace_level", 1),
                    ("daemon_namespace_stacked", True)):
                document = docker_apparmor_namespace_provenance_document()
                document[field] = value
                with self.subTest(field=field), self.assertRaises(
                        MODULE.GateError):
                    load_fixture_docker_namespace_provenance(
                        temporary_path, document)

            path = temporary_path / "docker-apparmor-namespace.json"
            valid_document = (
                docker_apparmor_namespace_provenance_document())
            valid_contents = (
                json.dumps(
                    valid_document,
                    sort_keys=True,
                    separators=(",", ":"),
                ) + "\n"
            ).encode("ascii")
            path.write_bytes(valid_contents)
            with self.assertRaisesRegex(MODULE.GateError, "digest mismatch"):
                MODULE.load_reviewed_docker_apparmor_namespace_provenance(
                    path, "sha256:" + "0" * 64)
            extra = dict(valid_document)
            extra["extra"] = "forbidden"
            with self.assertRaisesRegex(
                    MODULE.GateError, "field inventory"):
                load_fixture_docker_namespace_provenance(
                    temporary_path, extra)
            serialized = json.dumps(
                valid_document, sort_keys=True, separators=(",", ":"))
            duplicate = (
                '{"schema":"' +
                MODULE.REVIEWED_DOCKER_APPARMOR_NAMESPACE_PROVENANCE_SCHEMA +
                '",' + serialized[1:] + "\n"
            ).encode("ascii")
            path.write_bytes(duplicate)
            duplicate_digest = (
                "sha256:" + hashlib.sha256(duplicate).hexdigest())
            with self.assertRaisesRegex(MODULE.GateError, "duplicate field"):
                MODULE.load_reviewed_docker_apparmor_namespace_provenance(
                    path, duplicate_digest)

    def test_pass_report_persists_post_cleanup_attestation_or_fails(
            self) -> None:
        apparmor = {"profile": MODULE.APPARMOR_PROFILE}
        docker_namespace = {"same_apparmor_namespace_attested": True}
        progress = MODULE.Progress(
            owned_docker_objects_cleanup_complete=True,
            completed_checks=[
                "apparmor_revalidated",
                "docker_apparmor_namespace_revalidated",
            ],
            apparmor=apparmor,
            docker_apparmor_namespace=docker_namespace,
        )
        report: dict[str, object] = {
            "passed": True,
            "owned_docker_objects_cleanup_complete": True,
        }
        MODULE.persist_post_cleanup_attestations(
            report,
            progress,
            apparmor_after=dict(apparmor),
            docker_namespace_after=dict(docker_namespace),
        )
        self.assertTrue(report["apparmor_revalidated"])
        self.assertTrue(report["apparmor_records_equal"])
        self.assertEqual(report["apparmor_post_cleanup"], apparmor)
        self.assertIn("apparmor_revalidated", report["completed_checks"])

        rejected_report: dict[str, object] = {
            "passed": True,
            "owned_docker_objects_cleanup_complete": True,
        }
        with self.assertRaises(MODULE.GateError):
            MODULE.persist_post_cleanup_attestations(
                rejected_report,
                progress,
                apparmor_after={"profile": "changed"},
                docker_namespace_after=dict(docker_namespace),
            )
        self.assertNotIn("apparmor_revalidated", rejected_report)

    def test_docker_absence_requires_exact_not_found_proof(self) -> None:
        MODULE.initialize_docker_config()
        try:
            exact = subprocess.CompletedProcess(
                [], 1,
                "[]\nError response from daemon: No such container: "
                "fixture-container\n",
                None,
            )
            with mock.patch.object(MODULE, "command", return_value=exact):
                MODULE.require_docker_absent(
                    "container", "fixture-container")
            for output in (
                    "",
                    "daemon unavailable\n",
                    "[]\nError response from daemon: permission denied\n"):
                failed = subprocess.CompletedProcess([], 1, output, None)
                with self.subTest(output=output), mock.patch.object(
                        MODULE, "command", return_value=failed), \
                        self.assertRaises(MODULE.GateError):
                    MODULE.require_docker_absent(
                        "container", "fixture-container")
        finally:
            MODULE.cleanup_docker_config()

    def test_reviewed_base_requires_externally_pinned_go_binding(self) -> None:
        reviewed = base_record(reviewed_labels())
        with self.assertRaisesRegex(
                MODULE.GateError, "external provenance"):
            MODULE.validate_base_image_record(
                reviewed, pinned_reference(), allow_candidate=False)
        with tempfile.TemporaryDirectory(
                prefix="hepta-reviewed-base-fixture-") as temporary:
            provenance_path = Path(temporary) / "reviewed-base.json"
            document = reviewed_provenance_document(reviewed)
            provenance_sha256 = write_reviewed_provenance(
                provenance_path, document)
            provenance = MODULE.load_reviewed_base_provenance(
                provenance_path, provenance_sha256)
            accepted = MODULE.validate_base_image_record(
                reviewed,
                pinned_reference(),
                allow_candidate=False,
                reviewed_provenance=provenance,
            )
            self.assertEqual(
                accepted["base_class"], "reviewed-offline-ready")
            self.assertTrue(accepted["production_approved"])
            self.assertEqual(
                accepted["production_status"], "external-reviewed-go")
            self.assertEqual(
                accepted["reviewed_provenance"]["document_sha256"],
                provenance_sha256,
            )

            for field, value in (
                    ("image_id", "sha256:" + "d" * 64),
                    (
                        "repo_digest",
                        "registry.example/hepta/other@sha256:" + "a" * 64,
                    ),
                    ("labels_sha256", "sha256:" + "e" * 64)):
                changed = dict(document)
                changed[field] = value
                changed_sha256 = write_reviewed_provenance(
                    provenance_path, changed)
                wrong_binding = MODULE.load_reviewed_base_provenance(
                    provenance_path, changed_sha256)
                with self.subTest(binding=field), self.assertRaisesRegex(
                        MODULE.GateError, "does not bind"):
                    MODULE.validate_base_image_record(
                        reviewed,
                        pinned_reference(),
                        allow_candidate=False,
                        reviewed_provenance=wrong_binding,
                    )

            with self.assertRaisesRegex(MODULE.GateError, "digest mismatch"):
                MODULE.load_reviewed_base_provenance(
                    provenance_path, "sha256:" + "f" * 64)

            for field, value in (
                    ("decision", "NO-GO"),
                    ("image_id", "not-an-image-id"),
                    ("repo_digest", "not-a-repo-digest"),
                    ("labels_sha256", "a" * 64)):
                changed = dict(document)
                changed[field] = value
                changed_sha256 = write_reviewed_provenance(
                    provenance_path, changed)
                with self.subTest(field=field), self.assertRaises(
                        MODULE.GateError):
                    MODULE.load_reviewed_base_provenance(
                        provenance_path, changed_sha256)

            changed = dict(document)
            changed["unreviewed_extra"] = "forbidden"
            changed_sha256 = write_reviewed_provenance(
                provenance_path, changed)
            with self.assertRaisesRegex(
                    MODULE.GateError, "field inventory"):
                MODULE.load_reviewed_base_provenance(
                    provenance_path, changed_sha256)

            serialized = json.dumps(
                document, sort_keys=True, separators=(",", ":"))
            duplicate = (
                '{"schema":"' + MODULE.REVIEWED_BASE_PROVENANCE_SCHEMA +
                '",' + serialized[1:] + "\n"
            ).encode("ascii")
            provenance_path.write_bytes(duplicate)
            duplicate_sha256 = (
                "sha256:" + hashlib.sha256(duplicate).hexdigest())
            with self.assertRaisesRegex(MODULE.GateError, "duplicate field"):
                MODULE.load_reviewed_base_provenance(
                    provenance_path, duplicate_sha256)

    def test_reviewed_provenance_arguments_fail_closed_before_docker(
            self) -> None:
        with self.assertRaisesRegex(MODULE.GateError, "GO is required"):
            MODULE.reviewed_base_provenance_from_arguments(
                None, None, allow_candidate=False)
        self.assertIsNone(MODULE.reviewed_base_provenance_from_arguments(
            None, None, allow_candidate=True))
        with self.assertRaisesRegex(MODULE.GateError, "supplied together"):
            MODULE.reviewed_base_provenance_from_arguments(
                Path("/not-read"), None, allow_candidate=False)

    def test_isolated_builder_provenance_and_candidate_are_separate(
            self) -> None:
        buildx_sha256 = "sha256:" + "e" * 64
        with self.assertRaisesRegex(MODULE.GateError, "GO is required"):
            MODULE.reviewed_builder_provenance_from_arguments(
                None, None, buildx_sha256, allow_candidate=False)
        self.assertIsNone(
            MODULE.reviewed_builder_provenance_from_arguments(
                None, None, buildx_sha256, allow_candidate=True))
        with tempfile.TemporaryDirectory(
                prefix="hepta-builder-provenance-fixture-") as temporary:
            path = Path(temporary) / "builder.json"
            document = builder_provenance_document()
            digest = write_builder_provenance(path, document)
            provenance = MODULE.reviewed_builder_provenance_from_arguments(
                path, digest, buildx_sha256, allow_candidate=False)
            reviewed = MODULE.builder_execution_record(
                provenance, allow_candidate=False)
            self.assertEqual(reviewed["mode"], "reviewed-isolated-buildx")
            self.assertTrue(reviewed["isolated"])
            self.assertTrue(reviewed["production_eligible"])
            self.assertEqual(
                reviewed["builder_cache_cleanup"], "pending")

            changed = dict(document)
            changed["buildx_binary_sha256"] = "sha256:" + "f" * 64
            changed_digest = write_builder_provenance(path, changed)
            with self.assertRaisesRegex(MODULE.GateError, "does not bind"):
                MODULE.reviewed_builder_provenance_from_arguments(
                    path, changed_digest, buildx_sha256,
                    allow_candidate=False)

            with self.assertRaisesRegex(MODULE.GateError, "downgraded"):
                MODULE.builder_execution_record(
                    provenance, allow_candidate=True)

        candidate = MODULE.builder_execution_record(
            None, allow_candidate=True)
        self.assertEqual(
            candidate["mode"], "explicit-isolated-buildx-candidate")
        self.assertTrue(candidate["isolated"])
        self.assertEqual(candidate["cache_reuse"], "disabled")
        self.assertEqual(candidate["builder_cache_cleanup"], "pending")
        self.assertFalse(candidate["production_eligible"])

    def test_reviewed_builder_provenance_is_strict_and_duplicate_safe(
            self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-builder-provenance-strict-fixture-"
                ) as temporary:
            path = Path(temporary) / "builder.json"
            document = builder_provenance_document()
            digest = write_builder_provenance(path, document)
            accepted = MODULE.load_reviewed_builder_provenance(path, digest)
            self.assertEqual(accepted.repo_digest, buildkit_reference())
            self.assertEqual(accepted.decision if hasattr(
                accepted, "decision") else "GO", "GO")

            mutations = (
                ("decision", "NO-GO"),
                ("image_id", "sha256:" + "A" * 64),
                ("repo_digest", "buildkit:latest"),
                ("config_sha256", "f" * 64),
                ("buildkit_version", "latest"),
                ("buildx_version", "v0.30.1"),
                ("docker_server_api_version", "latest"),
                ("docker_server_git_commit", "../unsafe"),
            )
            for field, value in mutations:
                changed = dict(document)
                changed[field] = value
                changed_digest = write_builder_provenance(path, changed)
                with self.subTest(field=field), self.assertRaises(
                        MODULE.GateError):
                    MODULE.load_reviewed_builder_provenance(
                        path, changed_digest)

            extra = dict(document)
            extra["unreviewed_extra"] = "forbidden"
            extra_digest = write_builder_provenance(path, extra)
            with self.assertRaisesRegex(
                    MODULE.GateError, "field inventory"):
                MODULE.load_reviewed_builder_provenance(path, extra_digest)

            serialized = json.dumps(
                document, sort_keys=True, separators=(",", ":"))
            duplicate = (
                '{"schema":"' + MODULE.REVIEWED_BUILDER_PROVENANCE_SCHEMA +
                '",' + serialized[1:] + "\n"
            ).encode("ascii")
            path.write_bytes(duplicate)
            duplicate_digest = (
                "sha256:" + hashlib.sha256(duplicate).hexdigest())
            with self.assertRaisesRegex(MODULE.GateError, "duplicate field"):
                MODULE.load_reviewed_builder_provenance(
                    path, duplicate_digest)

    def test_buildkit_image_and_buildx_toolchain_are_exactly_go_bound(
            self) -> None:
        record = buildkit_record()
        with tempfile.TemporaryDirectory(
                prefix="hepta-builder-go-binding-fixture-") as temporary:
            path = Path(temporary) / "builder.json"
            document = builder_provenance_document(record)
            digest = write_builder_provenance(path, document)
            provenance = MODULE.load_reviewed_builder_provenance(path, digest)
            accepted = MODULE.validate_buildkit_image_record(
                record,
                buildkit_reference(),
                reviewed_provenance=provenance,
                allow_candidate=False,
            )
            self.assertTrue(accepted["production_approved"])
            self.assertEqual(
                accepted["production_status"], "external-reviewed-go")
            candidate = MODULE.validate_buildkit_image_record(
                record,
                buildkit_reference(),
                reviewed_provenance=None,
                allow_candidate=True,
            )
            self.assertFalse(candidate["production_approved"])
            self.assertEqual(
                candidate["production_status"], "non-production-candidate")

            for field, value in (
                    ("RepoDigests", [
                        "registry.example/attacker/buildkit@sha256:" +
                        "c" * 64]),
                    ("Os", "windows"),
                    ("Architecture", "arm64")):
                changed = copy.deepcopy(record)
                changed[field] = value
                with self.subTest(field=field), self.assertRaises(
                        MODULE.GateError):
                    MODULE.validate_buildkit_image_record(
                        changed,
                        buildkit_reference(),
                        reviewed_provenance=provenance,
                        allow_candidate=False,
                    )
            for field, value in (
                    ("OnBuild", ["RUN attacker"]),
                    ("Volumes", {"/host": {}}),
                    ("ExposedPorts", {"22/tcp": {}}),
                    ("Entrypoint", ["/bin/sh"])):
                changed = copy.deepcopy(record)
                changed["Config"][field] = value
                with self.subTest(config_field=field), self.assertRaises(
                        MODULE.GateError):
                    MODULE.validate_buildkit_image_record(
                        changed,
                        buildkit_reference(),
                        reviewed_provenance=provenance,
                        allow_candidate=False,
                    )
            changed = copy.deepcopy(record)
            changed["Config"]["Labels"][MODULE.RUN_ID_LABEL_KEY] = "attacker"
            with self.assertRaisesRegex(MODULE.GateError, "collide"):
                MODULE.validate_buildkit_image_record(
                    changed,
                    buildkit_reference(),
                    reviewed_provenance=provenance,
                    allow_candidate=False,
                )

            metadata = mock.Mock(
                st_mode=0o100755,
                st_nlink=1,
                st_uid=0,
                st_gid=0,
            )
            plugins = [{
                "Name": "buildx",
                "Path": "/usr/libexec/docker/cli-plugins/docker-buildx",
                "Version": document["buildx_version"],
            }]
            server = {
                "Version": document["docker_server_version"],
                "ApiVersion": document["docker_server_api_version"],
                "GitCommit": document["docker_server_git_commit"],
            }
            with mock.patch.object(
                    MODULE,
                    "read_regular_file",
                    return_value=(metadata, b"fixture", "e" * 64)):
                toolchain = MODULE.validate_buildx_toolchain(
                    plugins,
                    "github.com/docker/buildx 0.30.1 fixture\n",
                    server,
                    "sha256:" + "e" * 64,
                    provenance,
                )
                self.assertTrue(toolchain["reviewed"])
                with self.assertRaisesRegex(
                        MODULE.GateError, "exactly one"):
                    MODULE.validate_buildx_toolchain(
                        plugins + plugins,
                        "github.com/docker/buildx 0.30.1 fixture\n",
                        server,
                        "sha256:" + "e" * 64,
                        provenance,
                    )
                changed_server = dict(server)
                changed_server["ApiVersion"] = "1.51"
                with self.assertRaisesRegex(
                        MODULE.GateError, "does not bind"):
                    MODULE.validate_buildx_toolchain(
                        plugins,
                        "github.com/docker/buildx 0.30.1 fixture\n",
                        changed_server,
                        "sha256:" + "e" * 64,
                        provenance,
                    )

    def test_isolated_builder_argv_and_inspection_reject_drift(
            self) -> None:
        run_id = "a" * 32
        names = MODULE.isolated_builder_names(run_id)
        image = MODULE.validate_buildkit_image_record(
            buildkit_record(),
            buildkit_reference(),
            reviewed_provenance=None,
            allow_candidate=True,
        )
        container_id = "b" * 64
        MODULE.initialize_docker_config()
        try:
            volume_arguments = MODULE.docker_builder_volume_create_arguments(
                names, run_id, image["id"])
            container_arguments = (
                MODULE.docker_builder_container_create_arguments(
                    names, run_id, image))
            buildx_arguments = MODULE.docker_buildx_create_arguments(
                names, run_id, image["id"])
        finally:
            MODULE.cleanup_docker_config()

        self.assertEqual(volume_arguments[-1], names["volume"])
        self.assertIn("--driver=local", volume_arguments)
        self.assertEqual(container_arguments[-1], image["bare_id"])
        self.assertNotIn(buildkit_reference(), container_arguments)
        self.assertIn("--pull=never", container_arguments)
        self.assertIn("--network=none", container_arguments)
        self.assertIn("--privileged", container_arguments)
        self.assertIn("--init", container_arguments)
        self.assertIn("--restart=no", container_arguments)
        self.assertFalse(any(
            value in {"-v", "--volume", "-p", "--publish"}
            for value in container_arguments))
        self.assertIn("buildx", buildx_arguments)
        self.assertIn("create", buildx_arguments)
        self.assertNotIn("--use", buildx_arguments)
        self.assertNotIn("--bootstrap", buildx_arguments)
        driver_opt = buildx_arguments[
            buildx_arguments.index("--driver-opt") + 1]
        self.assertIn("image=" + image["bare_id"], driver_opt)
        self.assertIn("network=none", driver_opt)
        self.assertIn("restart-policy=no", driver_opt)

        volume = valid_builder_volume_inspect(
            names, run_id, image["id"])
        container = valid_builder_container_inspect(
            container_id, names, run_id, image)
        MODULE.validate_builder_volume_record(
            volume,
            names=names,
            run_id=run_id,
            buildkit_image_id=image["id"],
        )
        MODULE.validate_builder_container_record(
            container,
            container_id=container_id,
            names=names,
            run_id=run_id,
            buildkit_image=image,
            expected_running=False,
        )
        for field, value in (
                ("NetworkMode", "host"),
                ("Privileged", False),
                ("Binds", ["/:/host:rw"]),
                ("PortBindings", {"22/tcp": [{"HostPort": "22"}]})):
            changed = copy.deepcopy(container)
            changed["HostConfig"][field] = value
            with self.subTest(field=field), self.assertRaises(
                    MODULE.GateError):
                MODULE.validate_builder_container_record(
                    changed,
                    container_id=container_id,
                    names=names,
                    run_id=run_id,
                    buildkit_image=image,
                    expected_running=False,
                )
        changed_volume = copy.deepcopy(volume)
        changed_volume["Options"] = {"type": "none", "device": "/host"}
        with self.assertRaises(MODULE.GateError):
            MODULE.validate_builder_volume_record(
                changed_volume,
                names=names,
                run_id=run_id,
                buildkit_image_id=image["id"],
            )

    def test_owned_builder_cleanup_targets_no_global_objects(self) -> None:
        run_id = "a" * 32
        names = MODULE.isolated_builder_names(run_id)
        image = MODULE.validate_buildkit_image_record(
            buildkit_record(),
            buildkit_reference(),
            reviewed_provenance=None,
            allow_candidate=True,
        )
        container_id = "b" * 64
        container = valid_builder_container_inspect(
            container_id, names, run_id, image)
        volume = valid_builder_volume_inspect(
            names, run_id, image["id"])
        removed = False
        observed: list[list[str]] = []

        def fake_command(
                arguments: list[str],
                *,
                timeout: int = 120,
                check: bool = True,
        ) -> subprocess.CompletedProcess[str]:
            nonlocal removed
            del timeout, check
            observed.append(list(arguments))
            operation = arguments[4:]
            if operation == [
                    "container", "inspect", names["container"]]:
                if not removed:
                    output = json.dumps([container])
                    return subprocess.CompletedProcess(
                        arguments, 0, output, None)
                output = (
                    "[]\nError response from daemon: No such container: " +
                    names["container"] + "\n")
                return subprocess.CompletedProcess(
                    arguments, 1, output, None)
            if operation == ["volume", "inspect", names["volume"]]:
                if not removed:
                    return subprocess.CompletedProcess(
                        arguments, 0, json.dumps([volume]), None)
                output = (
                    "[]\nError response from daemon: get " +
                    names["volume"] + ": no such volume\n")
                return subprocess.CompletedProcess(
                    arguments, 1, output, None)
            if operation == [
                    "buildx", "rm", "--force", names["builder"]]:
                removed = True
                MODULE.builder_metadata_path(names["builder"]).unlink()
                return subprocess.CompletedProcess(
                    arguments, 0, names["builder"] + " removed\n", None)
            if operation == ["image", "inspect", image["id"]]:
                return subprocess.CompletedProcess(
                    arguments, 0,
                    json.dumps([{"Id": image["id"]}]), None)
            raise AssertionError(f"unexpected Docker command: {operation}")

        MODULE.initialize_docker_config()
        try:
            metadata = MODULE.builder_metadata_path(names["builder"])
            metadata.parent.mkdir(parents=True)
            metadata.write_text("{}\n", encoding="ascii")
            metadata.chmod(0o600)
            with mock.patch.object(
                    MODULE, "command", side_effect=fake_command):
                cleanup = MODULE.cleanup_isolated_builder(
                    names, run_id, image, container_id)
        finally:
            MODULE.cleanup_docker_config()
        self.assertTrue(cleanup["container_absent"])
        self.assertTrue(cleanup["state_volume_absent"])
        self.assertEqual(cleanup["cache_cleanup"], "state-volume-removed")
        operations = [arguments[4:] for arguments in observed]
        self.assertEqual(
            operations.count([
                "buildx", "rm", "--force", names["builder"]]),
            1,
        )
        self.assertFalse(any("prune" in operation for operation in operations))

    def test_formal_cmake_gate_binds_reviewed_provenance_only(self) -> None:
        root_cmake = (ROOT / "CMakeLists.txt").read_text(
            encoding="utf-8-sig", errors="strict")
        test_cmake = (ROOT / "tests/CMakeLists.txt").read_text(
            encoding="utf-8", errors="strict")
        provenance = (
            "HEPTA_AGENT_OS_ROOTFUL_SYSTEMD_BASE_PROVENANCE")
        provenance_sha256 = provenance + "_SHA256"
        self.assertIn(
            f"set({provenance} \"\" CACHE FILEPATH", root_cmake)
        self.assertIn(
            f"set({provenance_sha256} \"\" CACHE STRING", root_cmake)
        self.assertIn(
            "requires both reviewed base provenance inputs", root_cmake)
        self.assertIn(
            f'IS_ABSOLUTE\n       "${{{provenance}}}"', root_cmake)
        self.assertIn(
            f'IS_SYMLINK\n       "${{{provenance}}}"', root_cmake)
        self.assertIn(
            '"^sha256:[0-9a-f]+$"', root_cmake)
        self.assertIn(
            f'file(SHA256 "${{{provenance}}}"', root_cmake)
        self.assertIn(
            "base provenance digest does not match its file", root_cmake)
        registration_start = test_cmake.index(
            "add_test(NAME hepta_agent_os_rootful_systemd_e2e_gate")
        registration_end = test_cmake.index(
            "set_tests_properties(\n"
            "            hepta_agent_os_rootful_systemd_e2e_gate",
            registration_start,
        )
        registration = test_cmake[registration_start:registration_end]
        self.assertIn("\n                         --run\n", registration)
        self.assertEqual(
            registration.splitlines().count(
                "                         --run"),
            1,
        )
        self.assertIn("--reviewed-base-provenance", registration)
        self.assertIn(f'"${{{provenance}}}"', registration)
        self.assertIn("--reviewed-base-provenance-sha256", registration)
        self.assertIn(f'"${{{provenance_sha256}}}"', registration)
        buildkit = "HEPTA_AGENT_OS_ROOTFUL_SYSTEMD_BUILDKIT_IMAGE"
        builder = "HEPTA_AGENT_OS_ROOTFUL_SYSTEMD_BUILDER_PROVENANCE"
        builder_sha256 = builder + "_SHA256"
        buildx_sha256 = (
            "HEPTA_AGENT_OS_ROOTFUL_SYSTEMD_BUILDX_BINARY_SHA256")
        self.assertIn(f"set({buildkit} \"\" CACHE STRING", root_cmake)
        self.assertIn(f"set({builder} \"\" CACHE FILEPATH", root_cmake)
        self.assertIn(
            f"set({builder_sha256} \"\" CACHE STRING", root_cmake)
        self.assertIn(
            f"set({buildx_sha256} \"\" CACHE STRING", root_cmake)
        self.assertIn(
            "requires both isolated builder provenance inputs", root_cmake)
        self.assertIn(
            f'IS_ABSOLUTE\n       "${{{builder}}}"', root_cmake)
        self.assertIn(
            f'IS_SYMLINK\n       "${{{builder}}}"', root_cmake)
        self.assertIn(
            f'file(SIZE "${{{builder}}}"', root_cmake)
        self.assertIn(
            f'file(SHA256 "${{{builder}}}"', root_cmake)
        self.assertIn(
            "isolated builder provenance digest does not match its file",
            root_cmake,
        )
        self.assertIn("--buildkit-image", registration)
        self.assertIn(f'"${{{buildkit}}}"', registration)
        self.assertIn("--reviewed-builder-provenance", registration)
        self.assertIn(f'"${{{builder}}}"', registration)
        self.assertIn(
            "--reviewed-builder-provenance-sha256", registration)
        self.assertIn(f'"${{{builder_sha256}}}"', registration)
        self.assertIn("--buildx-binary-sha256", registration)
        self.assertIn(f'"${{{buildx_sha256}}}"', registration)
        apparmor = (
            "HEPTA_AGENT_OS_ROOTFUL_SYSTEMD_APPARMOR_PROVENANCE")
        apparmor_sha256 = apparmor + "_SHA256"
        self.assertIn(
            f"set({apparmor} \"\" CACHE FILEPATH", root_cmake)
        self.assertIn(
            f"set({apparmor_sha256} \"\" CACHE STRING", root_cmake)
        self.assertIn(
            "requires both AppArmor provenance inputs", root_cmake)
        self.assertIn(
            f'IS_ABSOLUTE\n       "${{{apparmor}}}"', root_cmake)
        self.assertIn(
            f'IS_SYMLINK\n       "${{{apparmor}}}"', root_cmake)
        self.assertIn(
            f'file(SHA256 "${{{apparmor}}}"', root_cmake)
        self.assertIn(
            "AppArmor provenance digest does not match its file", root_cmake)
        self.assertIn("--apparmor-provenance", registration)
        self.assertIn(f'"${{{apparmor}}}"', registration)
        self.assertIn("--apparmor-provenance-sha256", registration)
        self.assertIn(f'"${{{apparmor_sha256}}}"', registration)
        docker_namespace = (
            "HEPTA_AGENT_OS_ROOTFUL_SYSTEMD_DOCKER_AA_NAMESPACE_PROVENANCE")
        docker_namespace_sha256 = docker_namespace + "_SHA256"
        self.assertIn(
            f"set({docker_namespace} \"\"", root_cmake)
        self.assertIn(
            f"set({docker_namespace_sha256} \"\"", root_cmake)
        self.assertIn(
            "requires both Docker/AppArmor namespace provenance inputs",
            root_cmake,
        )
        self.assertIn(
            f'IS_ABSOLUTE\n       "${{{docker_namespace}}}"',
            root_cmake,
        )
        self.assertIn(
            f'IS_SYMLINK\n       "${{{docker_namespace}}}"',
            root_cmake,
        )
        self.assertIn(
            f'"${{{docker_namespace}}}"', registration)
        self.assertIn(
            f'"${{{docker_namespace_sha256}}}"', registration)
        self.assertIn(
            "--docker-apparmor-namespace-provenance", registration)
        self.assertIn(
            "--docker-apparmor-namespace-provenance-sha256", registration)
        self.assertNotIn("--allow-candidate-base", registration)
        self.assertNotIn("--allow-candidate-builder", registration)

    def test_candidate_base_requires_explicit_non_production_opt_in(
            self) -> None:
        candidate = base_record({
            "org.trillionnium.root-linux.builder-contract":
                "bookworm-content-addressed-candidate-v1",
            "org.trillionnium.root-linux.production-approved": "false",
            "org.trillionnium.root-linux.base-manifest":
                "sha256:" + "c" * 64,
        })
        with self.assertRaises(MODULE.GateError):
            MODULE.validate_base_image_record(
                candidate, pinned_reference(), allow_candidate=False)
        accepted_candidate = MODULE.validate_base_image_record(
            candidate, pinned_reference(), allow_candidate=True)
        self.assertEqual(
            accepted_candidate["base_class"],
            "explicit-development-candidate")
        self.assertFalse(accepted_candidate["production_approved"])
        self.assertEqual(
            accepted_candidate["production_status"],
            "non-production-candidate")
        self.assertIsNone(accepted_candidate["reviewed_provenance"])

        missing_manifest = copy.deepcopy(candidate)
        del missing_manifest["Config"]["Labels"][
            "org.trillionnium.root-linux.base-manifest"]
        with self.assertRaises(MODULE.GateError):
            MODULE.validate_base_image_record(
                missing_manifest, pinned_reference(), allow_candidate=True)

    def test_base_inspection_types_labels_and_repo_digest_are_exact(
            self) -> None:
        candidate = base_record({
            "org.trillionnium.root-linux.builder-contract":
                "bookworm-content-addressed-candidate-v1",
            "org.trillionnium.root-linux.production-approved": "false",
            "org.trillionnium.root-linux.base-manifest":
                "sha256:" + "c" * 64,
        })
        suffix_only = copy.deepcopy(candidate)
        suffix_only["RepoDigests"] = [
            "registry.example/attacker/systemd@sha256:" + "a" * 64]
        with self.assertRaisesRegex(MODULE.GateError, "exact requested"):
            MODULE.validate_base_image_record(
                suffix_only, pinned_reference(), allow_candidate=True)

        empty_volumes = copy.deepcopy(candidate)
        empty_volumes["Config"]["Volumes"] = {}
        accepted = MODULE.validate_base_image_record(
            empty_volumes, pinned_reference(), allow_candidate=True)
        self.assertEqual(accepted["declared_volumes"], 0)

        mutations: list[tuple[str, object]] = [
            ("record", []),
            ("config", []),
            ("labels", []),
            ("repo_digests", pinned_reference()),
            ("repo_digest_member", [7]),
            ("on_build_missing", None),
            ("volumes", {"/host-sensitive": {}}),
            ("volumes_type", []),
            ("extra_label", "forbidden"),
        ]
        for mutation, value in mutations:
            changed: object = copy.deepcopy(candidate)
            if mutation == "record":
                changed = value
            elif mutation == "config":
                changed["Config"] = value
            elif mutation == "labels":
                changed["Config"]["Labels"] = value
            elif mutation == "repo_digests":
                changed["RepoDigests"] = value
            elif mutation == "repo_digest_member":
                changed["RepoDigests"] = value
            elif mutation == "on_build_missing":
                del changed["Config"]["OnBuild"]
            elif mutation in {"volumes", "volumes_type"}:
                changed["Config"]["Volumes"] = value
            else:
                changed["Config"]["Labels"]["unreviewed.extra"] = value
            with self.subTest(mutation=mutation), self.assertRaises(
                    MODULE.GateError):
                MODULE.validate_base_image_record(
                    changed, pinned_reference(), allow_candidate=True)

    def test_pull_never_holder_uses_only_the_inspected_image_id(
            self) -> None:
        image_id = "sha256:" + "3" * 64
        run_id = "4" * 32
        name = f"hepta-agent-os-base-rootfs-{run_id}"
        MODULE.initialize_docker_config()
        try:
            arguments = MODULE.docker_base_holder_create_arguments(
                image_id, name, run_id)
        finally:
            MODULE.cleanup_docker_config()
        create_index = arguments.index("container")
        self.assertEqual(
            arguments[create_index:create_index + 4],
            ["container", "create", "--pull=never", "--network=none"],
        )
        self.assertEqual(arguments[-1], image_id)
        self.assertNotIn(pinned_reference(), arguments)
        self.assertIn("--read-only", arguments)
        self.assertIn("--entrypoint=/bin/true", arguments)
        self.assertEqual(arguments[arguments.index("--name") + 1], name)
        observed_labels = {
            arguments[index + 1]
            for index, value in enumerate(arguments)
            if value == "--label"
        }
        self.assertEqual(
            observed_labels,
            {
                f"io.hepta.purpose={MODULE.PURPOSE}",
                f"{MODULE.ROLE_LABEL_KEY}={MODULE.BASE_HOLDER_ROLE}",
                f"{MODULE.RUN_ID_LABEL_KEY}={run_id}",
            },
        )
        for forbidden in ("-v", "--volume", "--mount", "--tmpfs"):
            self.assertNotIn(forbidden, arguments)

    def test_base_holder_inspection_rejects_mounts_and_identity_drift(
            self) -> None:
        container_id = "5" * 64
        image_id = "sha256:" + "6" * 64
        run_id = "7" * 32
        name = f"hepta-agent-os-base-rootfs-{run_id}"
        record = valid_base_holder_inspect(
            container_id, name, image_id, run_id)
        accepted = MODULE.validate_base_holder_inspect_record(
            record,
            container_id=container_id,
            name=name,
            image_id=image_id,
            run_id=run_id,
        )
        self.assertEqual(accepted["mounts"], 0)
        self.assertEqual(accepted["volumes"], 0)

        mutations = (
            ("record", "Id", "8" * 64),
            ("record", "Name", "/attacker"),
            ("record", "Image", "sha256:" + "8" * 64),
            ("host", "NetworkMode", "bridge"),
            ("host", "ReadonlyRootfs", False),
            ("host", "Privileged", True),
            ("host", "Binds", ["/:/host:rw"]),
            ("host", "Tmpfs", {"/tmp": "rw"}),
            ("host", "VolumesFrom", ["attacker"]),
            ("host", "Devices", [{"PathOnHost": "/dev/kvm"}]),
            ("host", "DeviceRequests", [{"Count": -1}]),
            ("host", "PortBindings", {"80/tcp": [{}]}),
            ("host", "PublishAllPorts", True),
            ("config", "Image", "sha256:" + "8" * 64),
            ("config", "Volumes", {"/data": {}}),
            ("config", "Entrypoint", ["/bin/sh"]),
            (
                "label",
                MODULE.ROLE_LABEL_KEY,
                "attacker-owned-holder",
            ),
            ("label", MODULE.RUN_ID_LABEL_KEY, "8" * 32),
            ("mounts", "Mounts", [{"Type": "volume"}]),
        )
        for area, key, value in mutations:
            changed = copy.deepcopy(record)
            if area == "record" or area == "mounts":
                changed[key] = value
            elif area == "host":
                changed["HostConfig"][key] = value
            elif area == "config":
                changed["Config"][key] = value
            else:
                changed["Config"]["Labels"][key] = value
            with self.subTest(area=area, key=key), self.assertRaises(
                    MODULE.GateError):
                MODULE.validate_base_holder_inspect_record(
                    changed,
                    container_id=container_id,
                    name=name,
                    image_id=image_id,
                    run_id=run_id,
                )

    def test_missing_exact_image_fails_holder_create_without_fallback(
            self) -> None:
        image_id = "sha256:" + "9" * 64
        run_id = "a" * 32
        name = f"hepta-agent-os-base-rootfs-{run_id}"
        missing = subprocess.CompletedProcess(
            [], 1, "Error response from daemon: No such image\n", None)
        MODULE.initialize_docker_config()
        try:
            with mock.patch.object(
                    MODULE, "command", return_value=missing) as invoked, \
                    self.assertRaisesRegex(
                        MODULE.GateError, "disappeared"):
                MODULE.create_base_holder(image_id, name, run_id)
        finally:
            MODULE.cleanup_docker_config()
        self.assertEqual(invoked.call_count, 1)
        arguments = invoked.call_args.args[0]
        self.assertEqual(arguments[-1], image_id)
        self.assertNotIn(pinned_reference(), arguments)
        self.assertIn("--pull=never", arguments)

    def test_private_rootfs_tar_is_digest_bound_and_rechecked(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-rootfs-tar-fixture-") as temporary:
            directory = Path(temporary)
            os.chmod(directory, 0o700)
            tar_path = directory / "base-rootfs.tar"
            tar_path.write_bytes(b"A" * 1024)
            os.chmod(tar_path, 0o600)
            record = MODULE.stable_private_rootfs_tar(
                tar_path, maximum=2048)
            self.assertEqual(
                record["sha256"],
                "sha256:" + hashlib.sha256(b"A" * 1024).hexdigest(),
            )
            MODULE.verify_private_rootfs_tar_unchanged(
                tar_path, record, maximum=2048)

            tar_path.write_bytes(b"B" * 1024)
            with self.assertRaisesRegex(MODULE.GateError, "changed"):
                MODULE.verify_private_rootfs_tar_unchanged(
                    tar_path, record, maximum=2048)

        with tempfile.TemporaryDirectory(
                prefix="hepta-rootfs-tar-fixture-") as temporary:
            directory = Path(temporary)
            os.chmod(directory, 0o700)
            tar_path = directory / "base-rootfs.tar"
            tar_path.write_bytes(b"C" * 1024)
            os.chmod(tar_path, 0o644)
            with self.assertRaises(MODULE.GateError):
                MODULE.stable_private_rootfs_tar(
                    tar_path, maximum=2048)
            os.chmod(tar_path, 0o600)
            alias = directory / "alias.tar"
            os.link(tar_path, alias)
            with self.assertRaises(MODULE.GateError):
                MODULE.stable_private_rootfs_tar(
                    tar_path, maximum=2048)

        with tempfile.TemporaryDirectory(
                prefix="hepta-rootfs-tar-fixture-") as temporary:
            directory = Path(temporary)
            os.chmod(directory, 0o700)
            target = directory / "target.tar"
            target.write_bytes(b"D" * 1024)
            os.chmod(target, 0o600)
            symlink = directory / "base-rootfs.tar"
            symlink.symlink_to(target)
            with self.assertRaises(MODULE.GateError):
                MODULE.stable_private_rootfs_tar(
                    symlink, maximum=2048)

    def test_rootfs_export_stream_has_an_active_file_size_bound(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-rootfs-export-fixture-") as temporary:
            directory = Path(temporary)
            os.chmod(directory, 0o700)
            destination = directory / "base-rootfs.tar"
            record = MODULE.stream_docker_export(
                [
                    sys.executable,
                    "-c",
                    "import os; os.write(1, b'X' * 1024)",
                ],
                destination,
                maximum=1024,
                timeout=10,
            )
            self.assertEqual(record["size"], 1024)
            self.assertEqual(record["mode"], "0600")

        with tempfile.TemporaryDirectory(
                prefix="hepta-rootfs-export-fixture-") as temporary:
            directory = Path(temporary)
            os.chmod(directory, 0o700)
            destination = directory / "base-rootfs.tar"
            with self.assertRaises(MODULE.GateError):
                MODULE.stream_docker_export(
                    [
                        sys.executable,
                        "-c",
                        (
                            "import os; "
                            "os.write(1, b'X' * 512); "
                            "os.write(1, b'Y' * 512)"
                        ),
                    ],
                    destination,
                    maximum=512,
                    timeout=10,
                )

    def test_scratch_build_arguments_and_built_labels_are_exact(
            self) -> None:
        image_id = "sha256:" + "b" * 64
        rootfs_sha256 = "sha256:" + "c" * 64
        run_id = "d" * 32
        tag = f"hepta/agent-os-rootful-e2e:{run_id}"
        expected_labels = MODULE.built_image_labels(
            run_id, image_id, rootfs_sha256)
        MODULE.initialize_docker_config()
        try:
            arguments = MODULE.docker_build_arguments(
                Path("/private/context"),
                tag,
                pinned_reference(),
                image_id,
                rootfs_sha256,
                run_id,
                MODULE.isolated_builder_names(run_id)["builder"],
            )
        finally:
            MODULE.cleanup_docker_config()
        self.assertIn("--network=none", arguments)
        self.assertIn("--no-cache", arguments)
        self.assertEqual(arguments[-1], "/private/context")
        self.assertIn("buildx", arguments)
        self.assertIn("build", arguments)
        self.assertIn("--builder", arguments)
        self.assertIn("--load", arguments)
        self.assertFalse(any(value.startswith("--pull") for value in arguments))
        observed_labels = {
            arguments[index + 1]
            for index, value in enumerate(arguments)
            if value == "--label"
        }
        self.assertEqual(
            observed_labels,
            {f"{key}={value}" for key, value in expected_labels.items()},
        )
        self.assertIn(f"BASE_IMAGE={pinned_reference()}", arguments)

        built_id = "sha256:" + "e" * 64
        record = {
            "Id": built_id,
            "RepoTags": [tag],
            "RepoDigests": [],
            "Config": {"Labels": expected_labels},
        }
        accepted = MODULE.validate_built_image_record(
            record,
            tag=tag,
            base_image_id=image_id,
            rootfs_sha256=rootfs_sha256,
            run_id=run_id,
        )
        self.assertEqual(accepted["id"], record["Id"])
        for key in expected_labels:
            changed = copy.deepcopy(record)
            changed["Config"]["Labels"][key] = "attacker"
            with self.subTest(label=key), self.assertRaises(MODULE.GateError):
                MODULE.validate_built_image_record(
                    changed,
                    tag=tag,
                    base_image_id=image_id,
                    rootfs_sha256=rootfs_sha256,
                    run_id=run_id,
                )
        extra = copy.deepcopy(record)
        extra["Config"]["Labels"]["unreviewed.extra"] = "forbidden"
        with self.assertRaises(MODULE.GateError):
            MODULE.validate_built_image_record(
                extra,
                tag=tag,
                base_image_id=image_id,
                rootfs_sha256=rootfs_sha256,
                run_id=run_id,
            )
        for field, value in (
                ("RepoTags", []),
                ("RepoTags", [tag, "attacker/second:tag"]),
                ("RepoTags", tag),
                ("RepoDigests", [pinned_reference()]),
                ("RepoDigests", None)):
            changed = copy.deepcopy(record)
            changed[field] = value
            with self.subTest(
                    inventory=field, value=value), self.assertRaisesRegex(
                        MODULE.GateError, "residue"):
                MODULE.validate_built_image_record(
                    changed,
                    tag=tag,
                    base_image_id=image_id,
                    rootfs_sha256=rootfs_sha256,
                    run_id=run_id,
                )

    def test_holder_cleanup_never_removes_on_role_mismatch(self) -> None:
        container_id = "f" * 64
        image_id = "sha256:" + "1" * 64
        run_id = "2" * 32
        name = f"hepta-agent-os-base-rootfs-{run_id}"
        record = valid_base_holder_inspect(
            container_id, name, image_id, run_id)
        record["Config"]["Labels"][MODULE.ROLE_LABEL_KEY] = "attacker"
        inspected = subprocess.CompletedProcess(
            [], 0, json.dumps([record]), None)
        MODULE.initialize_docker_config()
        try:
            with mock.patch.object(
                    MODULE, "command", return_value=inspected) as invoked, \
                    self.assertRaisesRegex(
                        MODULE.GateError, "ownership mismatch"):
                MODULE.cleanup_container(
                    name,
                    container_id,
                    image_id,
                    expected_role=MODULE.BASE_HOLDER_ROLE,
                    expected_run_id=run_id,
                )
        finally:
            MODULE.cleanup_docker_config()
        self.assertEqual(invoked.call_count, 1)

    def test_built_image_cleanup_never_removes_on_binding_mismatch(
            self) -> None:
        image_id = "sha256:" + "3" * 64
        source_id = "sha256:" + "4" * 64
        rootfs_sha256 = "sha256:" + "5" * 64
        run_id = "6" * 32
        tag = f"hepta/agent-os-rootful-e2e:{run_id}"
        expected = MODULE.built_image_labels(
            run_id, source_id, rootfs_sha256)
        changed = dict(expected)
        changed[MODULE.BASE_ROOTFS_SHA256_LABEL_KEY] = (
            "sha256:" + "7" * 64)
        inspected = subprocess.CompletedProcess(
            [], 0,
            json.dumps([{
                "Id": image_id,
                "RepoTags": [tag],
                "RepoDigests": [],
                "Config": {"Labels": changed},
            }]),
            None,
        )
        MODULE.initialize_docker_config()
        try:
            with mock.patch.object(
                    MODULE, "command", return_value=inspected) as invoked, \
                    self.assertRaisesRegex(
                        MODULE.GateError, "binding mismatch"):
                MODULE.cleanup_image(tag, image_id, expected)
        finally:
            MODULE.cleanup_docker_config()
        self.assertEqual(invoked.call_count, 1)

    def test_built_image_cleanup_proves_tag_and_exact_id_absence(
            self) -> None:
        image_id = "sha256:" + "a" * 64
        source_id = "sha256:" + "b" * 64
        rootfs_sha256 = "sha256:" + "c" * 64
        run_id = "d" * 32
        tag = f"hepta/agent-os-rootful-e2e:{run_id}"
        labels = MODULE.built_image_labels(
            run_id, source_id, rootfs_sha256)
        record = {
            "Id": image_id,
            "RepoTags": [tag],
            "RepoDigests": [],
            "Config": {"Labels": labels},
        }

        def present() -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(
                [], 0, json.dumps([record]), None)

        def absent(reference: str) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(
                [],
                1,
                "[]\nError response from daemon: No such image: "
                f"{reference}\n",
                None,
            )

        responses = [
            present(),
            present(),
            subprocess.CompletedProcess([], 0, "", None),
            absent(tag),
            absent(image_id),
        ]
        MODULE.initialize_docker_config()
        try:
            with mock.patch.object(
                    MODULE, "command", side_effect=responses) as invoked:
                evidence = MODULE.cleanup_image(tag, image_id, labels)
        finally:
            MODULE.cleanup_docker_config()
        self.assertEqual(
            evidence,
            {"tag_absent": True, "exact_image_id_absent": True},
        )
        self.assertEqual(invoked.call_count, 5)
        calls = [call.args[0] for call in invoked.call_args_list]
        self.assertEqual(calls[0][-3:], ["image", "inspect", tag])
        self.assertEqual(calls[1][-3:], ["image", "inspect", image_id])
        self.assertEqual(calls[2][-3:], ["image", "rm", tag])
        self.assertEqual(calls[3][-3:], ["image", "inspect", tag])
        self.assertEqual(calls[4][-3:], ["image", "inspect", image_id])

    def test_built_image_cleanup_reports_lost_tag_and_second_tag_residue(
            self) -> None:
        image_id = "sha256:" + "e" * 64
        source_id = "sha256:" + "f" * 64
        rootfs_sha256 = "sha256:" + "1" * 64
        run_id = "2" * 32
        tag = f"hepta/agent-os-rootful-e2e:{run_id}"
        labels = MODULE.built_image_labels(
            run_id, source_id, rootfs_sha256)

        def absent(reference: str) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(
                [],
                1,
                "[]\nError response from daemon: No such image: "
                f"{reference}\n",
                None,
            )

        exact_residue = {
            "Id": image_id,
            "RepoTags": [],
            "RepoDigests": [],
            "Config": {"Labels": labels},
        }
        MODULE.initialize_docker_config()
        try:
            with mock.patch.object(
                    MODULE,
                    "command",
                    side_effect=[
                        absent(tag),
                        absent(tag),
                        subprocess.CompletedProcess(
                            [], 0, json.dumps([exact_residue]), None),
                    ],
            ) as invoked, self.assertRaisesRegex(
                    MODULE.GateError, "exact image residue"):
                MODULE.cleanup_image(tag, image_id, labels)
            self.assertEqual(invoked.call_count, 3)

            second_tag = copy.deepcopy(exact_residue)
            second_tag["RepoTags"] = [tag, "attacker/second:tag"]
            with mock.patch.object(
                    MODULE,
                    "command",
                    return_value=subprocess.CompletedProcess(
                        [], 0, json.dumps([second_tag]), None),
            ) as invoked, self.assertRaisesRegex(
                    MODULE.GateError, "residue"):
                MODULE.cleanup_image(tag, image_id, labels)
            self.assertEqual(invoked.call_count, 1)
        finally:
            MODULE.cleanup_docker_config()

    def test_cleanup_attempts_runtime_image_and_holder_after_failure(
            self) -> None:
        run_id = "8" * 32
        with mock.patch.object(
                MODULE,
                "cleanup_container",
                side_effect=[MODULE.GateError("runtime cleanup"), None],
        ) as containers, mock.patch.object(
                MODULE, "cleanup_image"
        ) as image, self.assertRaisesRegex(
                MODULE.GateError, "cleanup failed"):
            MODULE.cleanup_gate_docker_objects(
                runtime_name=f"hepta-agent-os-e2e-{run_id}",
                runtime_id=None,
                built_tag=f"hepta/agent-os-rootful-e2e:{run_id}",
                built_image_id=None,
                built_labels=None,
                holder_name=f"hepta-agent-os-base-rootfs-{run_id}",
                holder_id=None,
                base_image_id="sha256:" + "9" * 64,
                run_id=run_id,
            )
        self.assertEqual(containers.call_count, 2)
        self.assertEqual(image.call_count, 1)

    def test_runtime_command_has_no_host_or_network_escape(self) -> None:
        MODULE.initialize_docker_config()
        try:
            arguments = MODULE.docker_run_arguments(
                "sha256:" + "d" * 64,
                "hepta-agent-os-e2e-fixture",
                "e" * 32,
            )
        finally:
            MODULE.cleanup_docker_config()
        joined = "\n".join(arguments)
        self.assertIn("--network=none", joined)
        self.assertIn("--pull=never", joined)
        self.assertIn("--read-only", arguments)
        self.assertIn("--cgroupns=private", joined)
        self.assertIn("--cap-drop=ALL", joined)
        self.assertIn(
            f"--security-opt=apparmor={MODULE.APPARMOR_PROFILE}", joined)
        self.assertIn("--security-opt=no-new-privileges=true", joined)
        self.assertNotIn("--privileged", arguments)
        self.assertNotIn("--network=host", joined)
        self.assertNotIn("--pid=host", joined)
        self.assertNotIn("--ipc=host", joined)
        self.assertNotIn("--volume", arguments)
        self.assertNotIn("--mount", arguments)
        self.assertNotIn("-v", arguments)
        self.assertNotIn("--publish", arguments)

    def test_container_inspection_rejects_every_escape_axis(self) -> None:
        container_id = "f" * 64
        image_id = "sha256:" + "1" * 64
        name = "hepta-agent-os-e2e-fixture"
        run_id = "2" * 32
        record = valid_inspect(container_id, name, image_id, run_id)
        accepted = MODULE.validate_container_inspect_record(
            record,
            container_id=container_id,
            name=name,
            image_id=image_id,
            run_id=run_id,
        )
        self.assertEqual(accepted["network_mode"], "none")
        self.assertEqual(accepted["bind_mounts"], 0)

        mutations = (
            ("Privileged", True),
            ("ReadonlyRootfs", False),
            ("NetworkMode", "bridge"),
            ("Binds", ["/:/host:rw"]),
            ("PublishAllPorts", True),
            ("PidMode", "host"),
            ("IpcMode", "host"),
            ("CgroupnsMode", "host"),
            ("CapDrop", []),
            ("CapAdd", ["SYS_ADMIN"]),
            ("SecurityOpt", ["apparmor=unconfined"]),
        )
        for key, value in mutations:
            changed = copy.deepcopy(record)
            changed["HostConfig"][key] = value
            with self.subTest(key=key), self.assertRaises(MODULE.GateError):
                MODULE.validate_container_inspect_record(
                    changed,
                    container_id=container_id,
                    name=name,
                    image_id=image_id,
                    run_id=run_id,
                )
        changed = copy.deepcopy(record)
        changed["HostConfig"]["Tmpfs"]["/host"] = "rw"
        with self.assertRaises(MODULE.GateError):
            MODULE.validate_container_inspect_record(
                changed,
                container_id=container_id,
                name=name,
                image_id=image_id,
                run_id=run_id,
            )

    def test_inner_result_requires_full_watch_lifecycle(self) -> None:
        result = valid_inner_result()
        stdout = (
            "diagnostic line\nHEPTA_AGENT_OS_ROOTFUL_E2E_RESULT=" +
            json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n")
        self.assertEqual(MODULE.parse_inner_result(stdout), result)

        for mutation in (
                "paper", "live", "ib", "probe", "socket",
                "domain_identity"):
            changed = copy.deepcopy(result)
            if mutation == "paper":
                changed["boundary"]["paper_authorized"] = True
            elif mutation == "live":
                changed["boundary"]["live_authorized"] = True
            elif mutation == "ib":
                changed["boundary"]["ib_adapter_staged"] = True
            elif mutation == "probe":
                changed["checks"]["uid_2004_read_only_probes"] = False
            elif mutation == "socket":
                changed["lifecycle"]["socket_reactivation"][
                    "tool_socket_inode"] = 1001
            else:
                changed["lifecycle"]["trust_domains"]["openclaw-b"][
                    "gateway_pid"] = changed["lifecycle"]["trust_domains"][
                        "codex-a"]["gateway_pid"]
            changed_stdout = (
                "HEPTA_AGENT_OS_ROOTFUL_E2E_RESULT=" +
                json.dumps(changed, sort_keys=True, separators=(",", ":")))
            with self.subTest(mutation=mutation), self.assertRaises(
                    MODULE.GateError):
                MODULE.parse_inner_result(changed_stdout)

    def test_staging_allowlist_excludes_ib_and_paper_runtime(self) -> None:
        fake = {
            "heptactl": Path("/fixture/heptactl"),
            "hepta-sessionctl": Path("/fixture/hepta-sessionctl"),
            "hepta-executiond": Path("/fixture/hepta-executiond"),
            "hepta-tool-gatewayd": Path("/fixture/hepta-tool-gatewayd"),
        }
        staged = MODULE.staged_sources(fake)
        allowlist = set(staged)
        self.assertIn("usr/libexec/hepta-executiond", allowlist)
        self.assertIn("usr/libexec/hepta-tool-gatewayd", allowlist)
        self.assertIn("usr/libexec/hepta-agent-mcp-launcher", allowlist)
        self.assertIn(
            "usr/libexec/hepta-paper-receipt-contracts", allowlist)
        self.assertIn(
            "usr/libexec/hepta-shadow-watch-collector", allowlist)
        self.assertIn(
            "usr/libexec/hepta-shadow-watch-exporter", allowlist)
        self.assertIn(
            "usr/libexec/hepta-shadow-watch-custodian", allowlist)
        for helper in (
                "hepta-p1-shadow-host-controller",
                "hepta-p1-load-probe-validator",
                "build-hepta-p1-observation-policy",
                "hepta-p1-shadow-observer-controller",
                "hepta-p1-shadow-admission-launcher",
                "hepta-shadow-host-installer",
                "hepta-p1-watch-profile-deployer",
                "hepta-p1-watch-activation-transaction",
                "hepta-bounded-shadow-closure-verifier",
                "hepta-official-source-capture",
                "hepta_bounded_shadow_observer.py",
                "hepta_market_context_builder.py",
                "hepta_market_evidence_normalizer.py",
                "hepta_market_official_source_extractor.py",
                "hepta_eurusd_confirmed_momentum_strategy.py",
                "hepta_shadow_market_history.py",
                "hepta_strategy_shadow_runner.py",
                "hepta_strategy_contracts.py",
                "validate_hepta_strategy_decision_receipt.py"):
            self.assertIn("usr/libexec/" + helper, allowlist)
        self.assertIn(
            "usr/share/heptatrader/strategies/"
            "eurusd-confirmed-momentum-shadow-v2.json",
            allowlist)
        self.assertIn(
            "usr/lib/systemd/system/"
            "hepta-shadow-watch-custodian@.service", allowlist)
        self.assertIn(
            "usr/lib/systemd/system/"
            "hepta-shadow-watch-custodian-reconcile@.service", allowlist)
        self.assertIn(
            "usr/lib/systemd/system/"
            "hepta-shadow-watch-custodian-reconcile@.timer", allowlist)
        self.assertIn(
            "usr/lib/systemd/system/hepta-tool-session-supervisor.socket",
            allowlist)
        self.assertIn(
            "usr/lib/systemd/system/"
            "hepta-shadow-watch-collector@.service", allowlist)
        self.assertIn(
            "usr/lib/systemd/system/"
            "hepta-shadow-watch-collector@.timer", allowlist)
        self.assertIn(
            "usr/lib/systemd/system/"
            "hepta-shadow-watch-export@.service", allowlist)
        self.assertIn(
            "usr/share/doc/heptatrader/examples/"
            "hepta-shadow-watch-domain.env.example", allowlist)
        self.assertIn(
            "usr/share/doc/heptatrader/examples/"
            "hepta-tool-gateway-domain.env.example", allowlist)
        self.assertIn(
            "usr/lib/systemd/system/hepta-execution-simulator@.service",
            allowlist)
        self.assertIn(
            "usr/lib/systemd/system/"
            "hepta-execution-events-simulator@.socket",
            allowlist)
        self.assertIn(
            "usr/lib/systemd/system/hepta-tool-gateway@.service",
            allowlist)
        self.assertIn(
            "usr/lib/systemd/system/hepta-tool-gateway@.socket",
            allowlist)
        self.assertIn(
            "usr/lib/systemd/system/"
            "hepta-tool-session-supervisor@.socket", allowlist)
        for unit in (
                "hepta-broker-egress-policy.service",
                "hepta-p1-watch-activation.service",
                "hepta-p1-watch-activation-reconcile.service",
                "hepta-p1-watch-activation-reconcile.timer"):
            self.assertIn("usr/lib/systemd/system/" + unit, allowlist)
        self.assertIn(
            "usr/libexec/hepta_agent_trust_domain.py", allowlist)
        self.assertIn(
            "usr/libexec/hepta-broker-egress-policy", allowlist)
        self.assertIn(
            "usr/share/heptatrader/hepta-broker-network-policy-v1.json",
            allowlist)
        build_identity = (
            "etc/heptatrader/"
            "hepta-agent-trust-domain-paper-identities-v1.json")
        runtime_identity = (
            "usr/local/share/hepta-agent-os-e2e/provisioning/"
            "hepta-agent-trust-domain-paper-identities-v1.json")
        self.assertIn(build_identity, allowlist)
        self.assertEqual(staged[build_identity][1], 0o600)
        self.assertEqual(staged[build_identity][0], staged[runtime_identity][0])
        self.assertEqual(
            hashlib.sha256(staged[build_identity][0].read_bytes()).hexdigest(),
            "4a94d555cad61a9de67b809cfae301ead"
            "d6ebf2511714c93343f10decb34e435")
        self.assertFalse(any("ib-paper" in path for path in allowlist))
        self.assertNotIn("usr/libexec/hepta-ib-executiond", allowlist)

    def test_watch_broker_probe_denies_ib_uid_and_keeps_model_egress(
            self) -> None:
        class FakeSentinel:
            def __init__(self, port: int):
                self.port = port
                self.accepted = 0

            def start(self) -> None:
                pass

            def close(self) -> None:
                pass

        def connect_as(uid: int, gid: int, port: int) -> bool:
            if port in BROKER_PROBE.PROTECTED_PORTS:
                return False
            return (
                port == BROKER_PROBE.MODEL_EGRESS_SENTINEL_PORT and
                (uid, gid) in (
                    BROKER_PROBE.AGENT_IDENTITY,
                    *BROKER_PROBE.DOMAIN_AGENT_IDENTITIES))

        with mock.patch.object(
                BROKER_PROBE.os, "geteuid", return_value=0), \
                mock.patch.object(
                    BROKER_PROBE.os, "getegid", return_value=0), \
                mock.patch.object(
                    BROKER_PROBE.Path, "read_text",
                    return_value="systemd\n"), \
                mock.patch.object(
                    BROKER_PROBE.Path, "exists", return_value=False), \
                mock.patch.object(BROKER_PROBE, "Sentinel", FakeSentinel), \
                mock.patch.object(BROKER_PROBE, "require_policy_active"), \
                mock.patch.object(
                    BROKER_PROBE, "connect_as", side_effect=connect_as
                ) as connect_mock, \
                mock.patch.object(
                    BROKER_PROBE, "wait_accepts") as wait_mock:
            result = BROKER_PROBE.execute()

        self.assertEqual(
            result["schema"], "hepta.broker-network-rootful-probe.v2")
        self.assertTrue(
            result["checks"]["ib_execution_uid_all_ib_ports_denied"])
        self.assertNotIn(
            "ib_execution_uid_all_ib_ports_allowed", result["checks"])
        self.assertFalse(result["boundary"]["paper_authorized"])
        protected_calls = {
            call.args for call in connect_mock.call_args_list
            if call.args[2] in BROKER_PROBE.PROTECTED_PORTS}
        for port in BROKER_PROBE.PROTECTED_PORTS:
            self.assertIn((*BROKER_PROBE.IB_EXECUTION_IDENTITY, port),
                          protected_calls)
        self.assertEqual(
            [call.args[1] for call in wait_mock.call_args_list],
            [0, 0, 0, 0, 1 + len(BROKER_PROBE.DOMAIN_AGENT_IDENTITIES)])

    def test_shipped_broker_unit_is_credential_bound_deny_all(self) -> None:
        unit = (ROOT / "systemd/hepta-broker-egress-policy.service").read_text(
            encoding="utf-8", errors="strict")
        self.assertIn(
            "LoadCredential=hepta-broker-egress-policy.py:"
            "/usr/libexec/hepta-broker-egress-policy\n",
            unit)
        self.assertIn(
            "ExecStart=/usr/bin/python3.12 -I -S "
            "${CREDENTIALS_DIRECTORY}/hepta-broker-egress-policy.py "
            "--supervise-deny-all --paper-identities "
            "/etc/heptatrader/"
            "hepta-agent-trust-domain-paper-identities-v1.json\n",
            unit)
        self.assertIn(
            "ExecStopPost=/usr/bin/python3.12 -I -S "
            "${CREDENTIALS_DIRECTORY}/hepta-broker-egress-policy.py "
            "--tighten-deny-all\n",
            unit)
        self.assertNotIn(" --supervise --paper-identities ", unit)

    def test_gate_sources_encode_real_mcp_and_socket_lifecycle(self) -> None:
        dockerfile = (
            ROOT / "tests/agent_os_rootful_systemd/Dockerfile").read_text(
                encoding="utf-8", errors="strict")
        entrypoint = (
            ROOT / "tests/agent_os_rootful_systemd/"
            "hepta-agent-os-systemd-entrypoint").read_text(
                encoding="utf-8", errors="strict")
        inner = (
            ROOT / "tests/agent_os_rootful_systemd/"
            "hepta_agent_os_rootful_inner_gate.py").read_text(
                encoding="utf-8", errors="strict")
        runner = MODULE_PATH.read_text(encoding="utf-8", errors="strict")
        from_lines = [
            line.strip() for line in dockerfile.splitlines()
            if line.strip().startswith("FROM ")
        ]
        self.assertEqual(from_lines, ["FROM scratch"])
        self.assertEqual(
            [
                line.strip() for line in dockerfile.splitlines()
                if line.strip().startswith("ADD ")
            ],
            ["ADD base-rootfs.tar /"],
        )
        self.assertNotIn("FROM ${", dockerfile)
        self.assertNotIn("ADD http://", dockerfile)
        self.assertNotIn("ADD https://", dockerfile)
        self.assertNotIn("apt-get", dockerfile)
        self.assertNotIn("curl ", dockerfile)
        self.assertNotIn('"pull_performed"', runner)
        self.assertNotIn('"build", "--pull=false"', runner)
        self.assertNotIn('"build_cache"', runner)
        self.assertNotIn('"cleanup_complete": True', runner)
        self.assertIn('"cache_reuse": "disabled"', runner)
        self.assertIn(
            '"builder_cache_cleanup": "state-volume-removed"', runner)
        self.assertNotIn('"buildx", "prune"', runner)
        self.assertNotIn('"builder", "prune"', runner)
        self.assertNotIn('"system", "prune"', runner)
        self.assertIn(
            '"owned_docker_objects_cleanup_complete": True', runner)
        self.assertIn('"source_image_id": base_record["id"]', runner)
        self.assertIn(
            '"base_construction_version": BASE_CONSTRUCTION_VERSION',
            runner,
        )
        self.assertIn(
            "check-hepta-agent-os-provisioned-host --installation-only",
            dockerfile)
        self.assertIn("[ ! -e /usr/libexec/hepta-ib-executiond ]", dockerfile)
        self.assertIn("tmpfs /etc/heptatrader", entrypoint)
        self.assertIn(
            "/usr/local/share/hepta-agent-os-e2e/provisioning/"
            "hepta-agent-trust-domain-paper-identities-v1.json",
            entrypoint)
        self.assertIn(
            "/etc/heptatrader/"
            "hepta-agent-trust-domain-paper-identities-v1.json",
            entrypoint)
        self.assertIn("os.urandom(32)", entrypoint)
        self.assertIn('"provision-watch"', inner)
        self.assertIn("generation_holder[:] = [generation]", inner)
        self.assertIn("CUSTODIAN", inner)
        self.assertIn('"provision"', inner)
        self.assertIn('"rotate"', inner)
        self.assertIn('enable", "--runtime", "--now"', inner)
        self.assertIn(".shadow-watch.env", entrypoint)
        self.assertIn("-m 0600", entrypoint)
        self.assertIn("hepta-shadow-reader-codex-a", dockerfile)
        self.assertIn("hepta-shadow-reader-openclaw-b", dockerfile)
        self.assertIn("require_watch_restart_fenced()", inner)
        self.assertIn("require_domain_watch_restart_fenced(record)", inner)
        self.assertIn(
            "require_domain_collector_restart_terminal(record)", inner)
        self.assertIn(
            '"two_domain_watch_restart_fails_closed": True', inner)
        self.assertIn(
            '"two_domain_collector_typed_terminal": True', inner)
        self.assertIn(
            '"two_domain_custodian_sigkill_crash_closed": True', inner)
        self.assertIn(
            '"two_domain_custodian_closure_receipts_exact": True', inner)
        self.assertIn("WATCH_SESSION_AUTHORITY_NOT_FOUND", inner)
        self.assertIn(
            'result.get("reason_code") != "SESSION_NOT_FOUND"', inner)
        self.assertIn(
            "validate_uid1000_observer_reads_uid2101_proc_stat", inner)
        self.assertIn(
            '"read_alpha_gateway_process_identity", None', inner)
        self.assertIn(
            '"uid1000_observer_reads_uid2101_proc_stat": True', inner)
        self.assertIn("runtime_preflight(attempts=8)", inner)
        self.assertIn("negative_runtime_preflight()", inner)
        self.assertIn("socket restart did not create fresh socket inodes", inner)
        self.assertIn("cross-domain Gateway opened a foreign Execution socket",
                      inner)
        self.assertIn(
            "foreign domain token did not reach the expected rejection",
            inner)
        self.assertIn("foreign per-UID runtime config did not fail closed",
                      inner)
        self.assertIn("HEPTA_TOOL_ACCOUNT", inner)
        self.assertIn("validate_broker_network_policy()", inner)
        self.assertIn("os.kill(policy_pid, signal.SIGSTOP)", inner)
        self.assertIn('"WatchdogUSec": "15s"', inner)
        self.assertIn('"TimeoutStopUSec": "30s"', inner)
        self.assertIn('"Result": "watchdog"', inner)
        self.assertIn("_require_broker_deny_all()", inner)
        self.assertIn(
            '"broker_watchdog_clean_restart": True', inner)
        self.assertIn("hepta-execution-simulator@{domain_id}.service", inner)
        self.assertIn("codex-a openclaw-b", entrypoint)
        self.assertIn("uid-$agent_uid.json", entrypoint)
        self.assertIn("hepta-gw-openclaw-b", dockerfile)

    def test_pre_container_failure_report_claims_no_runtime(self) -> None:
        progress = MODULE.Progress()
        report = MODULE.failure_report(
            MODULE.GateError("fixture failure"), progress)
        self.assertFalse(report["passed"])
        self.assertEqual(report["boundary"]["real_broker_connections"], 0)
        self.assertEqual(report["boundary"]["paper_orders"], 0)
        self.assertFalse(report["boundary"]["paper_authorized"])
        self.assertFalse(report["boundary"]["live_authorized"])
        self.assertFalse(report["boundary"]["ib_adapter_staged"])
        self.assertFalse(report["boundary"]["host_hepta_units_started"])
        self.assertFalse(report["failure_stage"][
            "owned_docker_objects_cleanup_complete"])
        self.assertEqual(
            report["builder_cache_cleanup"], "incomplete-or-not-created")


if __name__ == "__main__":
    unittest.main()
