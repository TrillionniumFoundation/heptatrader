#!/usr/bin/env python3

"""Offline failure-seam tests for profile deployment and round95 rebind."""

from __future__ import annotations

import contextlib
import copy
import fcntl
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts/hepta_p1_watch_profile_deployer.py"
SPEC = importlib.util.spec_from_file_location(
    "hepta_p1_watch_profile_deployer", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
DEPLOYER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = DEPLOYER
SPEC.loader.exec_module(DEPLOYER)
REAL_GUARDED_BROKER_EGRESS_CHECK = DEPLOYER.guarded_broker_egress_check
FROZEN_GATEWAY_UNIT_CLOSURE = copy.deepcopy(DEPLOYER.GATEWAY_UNIT_CLOSURE)


def host_path(root: Path, absolute: Path) -> Path:
    return root.joinpath(*absolute.parts[1:])


def completed(
    arguments: list[str],
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        arguments, returncode, stdout=stdout, stderr=stderr)


def synthetic_dormant_paper_payload() -> bytes:
    values = {
        "HEPTA_EXECUTION_REMOTE_MODE": "PAPER",
        "HEPTA_EXECUTION_SOCKET": "/run/hepta-execution-alpha/execution.sock",
        "HEPTA_EXECUTION_EVENT_SOCKET":
            "/run/hepta-execution-alpha/events.sock",
        "HEPTA_EXECUTION_SERVICE_UID": "2121",
        "HEPTA_EXECUTION_IO_TIMEOUT_MS": "2500",
        "HEPTA_EXECUTION_MAX_RESPONSE_BYTES": "32768",
        "HEPTA_TOOL_ACCOUNT": "TEST12345",
        "HEPTA_TOOL_AGENT_ID": "alpha",
        "HEPTA_EXECUTION_DOMAIN_ID": "PAPER:alpha",
        "HEPTA_TOOL_ALLOW_TRADE": "1",
        "HEPTA_TOOL_SESSION_TEMPLATES": "watch,paper",
        "HEPTA_TOOL_CONTRACT_BINDINGS": "EUR.USD|EUR|CASH|IDEALPRO|USD",
        "HEPTA_TOOL_MAX_ORDER_QTY": "25000",
        "HEPTA_TOOL_MAX_TRADE_CALLS_PER_MIN": "1",
        "HEPTA_TOOL_DECISION_LEASE_TTL_MS": "5000",
        "HEPTA_TOOL_AGENT_UID": "2104",
        "HEPTA_TOOL_SUPERVISOR_UID": "0",
        "HEPTA_TOOL_SUPERVISOR_MAX_TTL_SEC": "86400",
        "HEPTA_TOOL_SERVER_WORKERS": "4",
        "HEPTA_TOOL_SERVER_MAX_PENDING": "32",
        "HEPTA_TOOL_SERVER_MAX_CONCURRENT_PER_OWNER": "1",
        "HEPTA_TOOL_SERVER_MAX_PENDING_PER_OWNER": "8",
        "HEPTA_TOOL_SERVER_INGRESS_WORKERS": "2",
    }
    return "".join(f"{key}={value}\n" for key, value in values.items()).encode()


class Fixture:
    def __init__(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="hepta-p1-watch-profile-deployer-tests.")
        self.root = Path(self.temporary.name)
        self.uid = os.getuid()
        self.gid = os.getgid()
        os.chmod(self.root, 0o700)
        self.commands: list[list[str]] = []
        self.historical_exec_main_pid_checks: list[int] = []
        self.historical_exec_main_pid_allowed = True
        self.broker_returncode = 0
        self.broker_stdout = (
            "hepta_broker_egress_policy: PASS "
            + "policy_sha256="
            + DEPLOYER.BROKER_EGRESS_DENY_ALL_SOURCE_SHA256
            + " authorized_connectors=0 authorized_uids= protected_ports=4\n")
        self.broker_stderr = ""
        self.local_control_document = {
            "identity_count": 0,
            "identity_manifest_sha256":
                DEPLOYER.SHADOW_DEFAULT_DENY_IDENTITY_SHA256,
            "live_authorized": False,
            "mode": "DENY_ALL",
            "paper_authorized": False,
        }
        self.systemctl_stderr = ""
        self.manager_stderr = ""
        self.shadow_install_acquire_arguments: list[
            tuple[str | None, str | None]] = []
        self.shadow_install_validation_count = 0
        self.shadow_install_release_count = 0
        self.shadow_install_binding = object()
        digest_a = "sha256:" + "1" * 64
        digest_b = "sha256:" + "2" * 64
        self.shadow_install_evidence = {
            "schema":
                "hepta.shadow-runtime-install-consumption-evidence.v3",
            "version": 3,
            "receipt_path": str(DEPLOYER.SHADOW_INSTALL_RECEIPT_PATH),
            "receipt_file_sha256": digest_a,
            "receipt_body_sha256": digest_b,
            "manifest_path": str(DEPLOYER.SHADOW_INSTALL_MANIFEST_PATH),
            "manifest_file_sha256": digest_a,
            "current_install_pointer_path":
                str(DEPLOYER.SHADOW_CURRENT_INSTALL_POINTER_PATH),
            "current_install_pointer_file_sha256": digest_b,
            "install_generation": DEPLOYER.CURRENT_SHADOW_INSTALL_GENERATION,
            "predecessor_install_generation":
                DEPLOYER.CURRENT_SHADOW_PREDECESSOR_INSTALL_GENERATION,
            "predecessor_current_install_pointer_file_sha256":
                DEPLOYER.CURRENT_SHADOW_PREDECESSOR_POINTER_SHA256,
            "archive_sha256": digest_b,
            "source_baseline_sha256": digest_a,
            "installer_sha256": digest_b,
            "installed_file_count": DEPLOYER.SHADOW_INSTALL_FILE_COUNT,
            "installed_paths_sha256": digest_a,
            "closure_sha256": digest_b,
            "transaction_lock": {
                "path": str(DEPLOYER.SHADOW_INSTALL_LOCK_PATH),
                "device": 1,
                "inode": 2,
                "nlink": 1,
                "uid": self.uid,
                "gid": self.gid,
                "mode": "0600",
                "size": 0,
                "mtime_ns": 3,
                "ctime_ns": 4,
                "created_during_transaction": False,
                "persistent": True,
                "held_during_transaction": True,
            },
            "default_deny_identity_sha256":
                DEPLOYER.SHADOW_DEFAULT_DENY_IDENTITY_SHA256,
            "lock_mode": "exclusive",
            "verified_under_lock": True,
            "domain": "alpha",
            "backup_root": str(DEPLOYER.SHADOW_INSTALL_BACKUP_ROOT),
            "paper_authorized": False,
            "live_authorized": False,
            "mutation_attempted": False,
            "direct_broker_access": False,
        }
        self.active_unit: str | None = None
        self.failed_units: set[str] = set()
        self.unit_jobs = {
            unit: ""
            for unit in (
                *DEPLOYER.GATEWAY_BOUNDARY_UNITS,
                *DEPLOYER.PAPER_UNITS,
                *DEPLOYER.WATCH_BOUNDARY_UNITS,
            )
        }
        self.manager_version = DEPLOYER.EXPECTED_SYSTEMD_VERSION
        self.manager_features = DEPLOYER.EXPECTED_SYSTEMD_FEATURES
        self.manager_unit_path = DEPLOYER.EXPECTED_SYSTEMD_UNIT_PATH
        self.manager_environment = (
            DEPLOYER.EXPECTED_SYSTEMD_MANAGER_ENVIRONMENT)
        self.gateway_load_state = "masked"
        self.gateway_unit_file_state = "masked"
        self.gateway_fragment_path: str | None = None
        self.gateway_source_paths = {
            unit: "" for unit in DEPLOYER.GATEWAY_BOUNDARY_UNITS
        }
        self.gateway_drop_in_paths = {
            unit: (
                str(DEPLOYER.GATEWAY_SERVICE_DROPIN_PATH)
                if unit == DEPLOYER.GATEWAY_SERVICE_UNIT else ""
            )
            for unit in DEPLOYER.GATEWAY_BOUNDARY_UNITS
        }
        self.gateway_binds_to = {
            unit: (
                "hepta-broker-egress-policy.service"
                if unit == DEPLOYER.GATEWAY_SERVICE_UNIT else ""
            )
            for unit in DEPLOYER.GATEWAY_BOUNDARY_UNITS
        }
        self.gateway_after = dict(self.gateway_binds_to)
        self.gateway_names = {
            unit: unit for unit in DEPLOYER.GATEWAY_BOUNDARY_UNITS
        }
        self.gateway_wants = {
            unit: "" for unit in DEPLOYER.GATEWAY_BOUNDARY_UNITS
        }
        self.gateway_requires = dict(self.gateway_wants)
        self.gateway_upholds = dict(self.gateway_wants)
        self.gateway_need_daemon_reload = {
            unit: (
                "yes" if unit == DEPLOYER.GATEWAY_SERVICE_UNIT else "no"
            )
            for unit in DEPLOYER.GATEWAY_BOUNDARY_UNITS
        }
        self.broker_unit_fields = {
            "Id": DEPLOYER.BROKER_EGRESS_UNIT,
            "Names": DEPLOYER.BROKER_EGRESS_UNIT,
            "LoadState": "loaded",
            "ActiveState": "active",
            "SubState": "running",
            "UnitFileState": "enabled",
            "FragmentPath": str(DEPLOYER.BROKER_EGRESS_UNIT_PATH),
            "SourcePath": "",
            "DropInPaths": "",
            "NeedDaemonReload": "no",
            "Job": "",
            "Type": "notify",
            "NotifyAccess": "main",
            "Restart": "no",
            "WatchdogUSec": "15s",
            "Environment": "",
            "PassEnvironment": "",
            "UnsetEnvironment": "",
            "ExecSearchPath": "",
            "WorkingDirectory": "",
            "RootDirectory": "",
            "DynamicUser": "no",
            "User": "root",
            "Group": "root",
            "CapabilityBoundingSet": "cap_net_admin",
            "AmbientCapabilities": "",
            "RestrictAddressFamilies": "AF_NETLINK AF_UNIX",
            "NoNewPrivileges": "yes",
            "ExecStart": "",
            "ExecStopPost": (
                "{ path=/usr/libexec/hepta-broker-egress-policy ; "
                "argv[]=/usr/libexec/hepta-broker-egress-policy "
                "--tighten-deny-all ; ignore_errors=no ; "
                "start_time=[n/a] ; stop_time=[n/a] ; pid=0 ; "
                "code=(null) ; status=0/0 }"),
            "MainPID": "",
            "ExecMainPID": "",
            "InvocationID": "",
            "ExecMainStartTimestampMonotonic":
                str(DEPLOYER.EXPECTED_BROKER_EXEC_MAIN_START_MONOTONIC_US),
            "ControlGroup": DEPLOYER.EXPECTED_BROKER_CONTROL_GROUP,
            "ControlGroupId":
                str(DEPLOYER.EXPECTED_BROKER_CONTROL_GROUP_ID),
            "ControlPID": "0",
            "NRestarts": "0",
            "ConditionResult": "yes",
            "AssertResult": "yes",
            "FreezerState": "running",
            "UID": "0",
            "GID": "0",
            "ExecMainCode": "0",
            "ExecMainStatus": "0",
        }
        self.set_broker_process_identity(
            1108253, DEPLOYER.EXPECTED_BROKER_INVOCATION_ID)
        self.broker_offline_fields = {
            "Id": DEPLOYER.BROKER_EGRESS_UNIT,
            "Names": DEPLOYER.BROKER_EGRESS_UNIT,
            "LoadState": "loaded",
            "ActiveState": "failed",
            "SubState": "failed",
            "UnitFileState": "enabled",
            "FragmentPath": str(DEPLOYER.BROKER_EGRESS_UNIT_PATH),
            "SourcePath": "",
            "DropInPaths": "",
            "NeedDaemonReload": "yes",
            "Job": "",
            "MainPID": "0",
            "ExecMainPID": "1108253",
            "ControlPID": "0",
        }
        unprintable = {
            "NFTSet": "[unprintable]",
            "RootImageOptions": "[unprintable]",
            "TTYRows": "[unprintable]",
            "TTYColumns": "[unprintable]",
            "SetCredential": "[unprintable]",
            "SetCredentialEncrypted": "[unprintable]",
            "LoadCredential": "[unprintable]",
            "LoadCredentialEncrypted": "[unprintable]",
            "Conditions": "[unprintable]",
            "Asserts": "[unprintable]",
            "ActivationDetails": "[unprintable]",
        }
        self.manager_unit_all_fields = {
            unit: {
                "Id": unit,
                "Description": f"fixture {unit}",
                **unprintable,
            }
            for unit in DEPLOYER.GATEWAY_BOUNDARY_UNITS
        }
        for unit in DEPLOYER.GATEWAY_BOUNDARY_UNITS:
            dynamic = (
                DEPLOYER.GATEWAY_SERVICE_MANAGER_DYNAMIC_PROPERTIES
                if unit == "hepta-tool-gateway@alpha.service" else
                DEPLOYER.GATEWAY_SOCKET_MANAGER_DYNAMIC_PROPERTIES)
            for field in dynamic:
                self.manager_unit_all_fields[unit].setdefault(field, "0")
        self.manager_unit_all_fields[DEPLOYER.BROKER_EGRESS_UNIT] = {
            **self.broker_unit_fields,
            **unprintable,
            "CPUUsageNSec": "123",
            "MemoryAvailable": "456",
            "MemoryCurrent": "789",
            "StatusText": "HeptaTrader broker boundary exact",
            "TasksCurrent": "1",
            "WatchdogTimestamp": "Sat 2026-08-01 20:38:46 CST",
            "WatchdogTimestampMonotonic": "214503000000",
        }
        self.dbus_interface_properties = {}
        for unit in DEPLOYER.MANAGER_UNIT_CONTRACT_UNITS:
            execution_interface = (
                DEPLOYER.SYSTEMD_DBUS_EXECUTION_INTERFACES[unit])
            unit_properties = {
                "Id": {"type": "s", "data": unit},
                "LoadState": {
                    "type": "s",
                    "data": (
                        "loaded" if unit == DEPLOYER.BROKER_EGRESS_UNIT
                        else "masked"),
                },
                "ActiveState": {
                    "type": "s",
                    "data": (
                        "active" if unit == DEPLOYER.BROKER_EGRESS_UNIT
                        else "inactive"),
                },
                "OnFailure": {"type": "as", "data": []},
                "Conditions": {"type": "a(sbbsi)", "data": []},
                "Asserts": {"type": "a(sbbsi)", "data": []},
                "ActivationDetails": {"type": "a(ss)", "data": []},
                "Job": {"type": "(uo)", "data": [0, "/"]},
            }
            execution_properties = {
                "ExecStart": {
                    "type": "a(sasbttttuii)",
                    "data": [[
                        "/usr/bin/fixture", ["/usr/bin/fixture"], False,
                        1, 2, 0, 0, 123, 0, 0,
                    ]],
                },
                "ExecStartPre": {
                    "type": "a(sasbttttuii)", "data": []},
                "ExecReload": {
                    "type": "a(sasbttttuii)", "data": []},
                "ExecStop": {
                    "type": "a(sasbttttuii)", "data": []},
                "LoadCredential": {"type": "a(ss)", "data": []},
                "SetCredential": {"type": "a(say)", "data": []},
                "TTYRows": {"type": "q", "data": 65535},
                "TTYColumns": {"type": "q", "data": 65535},
                "CPUUsageNSec": {"type": "t", "data": 123},
            }
            if execution_interface.endswith(".Socket"):
                execution_properties.update({
                    "NAccepted": {"type": "u", "data": 0},
                    "NConnections": {"type": "u", "data": 0},
                    "NRefused": {"type": "u", "data": 0},
                })
            self.dbus_interface_properties[unit] = {
                DEPLOYER.SYSTEMD_DBUS_UNIT_INTERFACE: unit_properties,
                execution_interface: execution_properties,
            }
        self.expected_manager_unit_contracts = {
            unit: self.expected_manager_unit_contract(unit)
            for unit in DEPLOYER.MANAGER_UNIT_CONTRACT_UNITS
        }
        self.broker_process_evidence = {
            "MainPID": DEPLOYER.EXPECTED_BROKER_MAIN_PID,
            "InvocationID": DEPLOYER.EXPECTED_BROKER_INVOCATION_ID,
            "boot_id": DEPLOYER.EXPECTED_BOOT_ID,
            "parent_pid": 1,
            "starttime_ticks": DEPLOYER.EXPECTED_BROKER_PROC_STARTTIME_TICKS,
            "process_directory_device": 101,
            "process_directory_inode": 202,
            "cgroup": DEPLOYER.EXPECTED_BROKER_CONTROL_GROUP,
            "cmdline": [
                entry.decode("ascii")
                for entry in DEPLOYER.BROKER_CMDLINE[:-1].split(b"\0")
            ],
            "cmdline_sha256": DEPLOYER.digest_bytes(
                DEPLOYER.BROKER_CMDLINE),
            "environment_bytes": DEPLOYER.BROKER_ENVIRONMENT_BYTES,
            "environment_sha256":
                "sha256:" + DEPLOYER.BROKER_ENVIRONMENT_SHA256,
            "status": DEPLOYER.expected_broker_process_status(
                DEPLOYER.EXPECTED_BROKER_MAIN_PID),
            "interpreter": {
                "path": str(DEPLOYER.BROKER_INTERPRETER_PATH),
                "sha256": "sha256:" + DEPLOYER.BROKER_INTERPRETER_SHA256,
                "bytes": DEPLOYER.BROKER_INTERPRETER_BYTES,
                "device": 303,
                "inode": 404,
                "mode": stat.S_IFREG | 0o755,
                "nlink": 1,
                "uid": self.uid,
                "gid": self.gid,
            },
        }

        self.make_directory(DEPLOYER.TARGET_PATH.parent)
        self.make_directory(DEPLOYER.KILL_SWITCH_PATH.parent)
        self.make_directory(DEPLOYER.GLOBAL_KILL_SWITCH_PATH.parent)
        self.make_directory(DEPLOYER.PAPER_POLICY_ROOT)
        sessions = self.make_directory(DEPLOYER.WATCH_SESSIONS_PATH)
        os.chmod(sessions, 0o711)
        bootstrap_lock = sessions / DEPLOYER.SESSION_BOOTSTRAP_LOCK
        bootstrap_lock.write_bytes(b"")
        os.chmod(bootstrap_lock, 0o600)
        private = self.make_directory(DEPLOYER.WATCH_PRIVATE_PATH)
        os.chmod(private.parent, 0o700)
        os.chmod(private, 0o700)
        self.make_directory(DEPLOYER.CUSTODIAN_TRANSACTION_PATH.parent)
        self.make_directory(DEPLOYER.LOCK_PATH.parent)
        self.make_directory(DEPLOYER.PERSISTENT_MASK_ROOT)
        self.make_directory(DEPLOYER.RUNTIME_MASK_ROOT)
        for unit in DEPLOYER.GATEWAY_BOUNDARY_UNITS:
            self.local(DEPLOYER.PERSISTENT_MASK_ROOT / unit).symlink_to(
                DEPLOYER.MASK_TARGET)
            self.local(DEPLOYER.RUNTIME_MASK_ROOT / unit).symlink_to(
                DEPLOYER.MASK_TARGET)
        paper_identities = (ROOT / "systemd/"
                            "hepta-agent-trust-domain-paper-identities-v1."
                            "json.example").read_bytes()
        closure_payloads = {
            "gateway_service_template":
                (ROOT / "systemd/hepta-tool-gateway@.service").read_bytes(),
            "gateway_socket_template":
                (ROOT / "systemd/hepta-tool-gateway@.socket").read_bytes(),
            "supervisor_socket_template":
                (ROOT / "systemd/hepta-tool-session-supervisor@.socket").read_bytes(),
            "gateway_service_broker_dropin": (ROOT / (
                "systemd/hepta-tool-gateway@.service.d/"
                "10-hepta-broker-egress-policy.conf")).read_bytes(),
            "broker_egress_service":
                (ROOT / "systemd/hepta-broker-egress-policy.service").read_bytes(),
            "broker_egress_helper":
                (ROOT / "scripts/hepta_broker_egress_policy.py").read_bytes(),
            "broker_network_policy":
                (ROOT / "systemd/hepta-broker-network-policy-v1.json").read_bytes(),
            "broker_service_identities":
                (ROOT / "systemd/hepta-service-identities-v1.json").read_bytes(),
            "broker_paper_identities": paper_identities,
        }
        assert set(closure_payloads) == set(DEPLOYER.GATEWAY_UNIT_CLOSURE)
        self.gateway_unit_closure = copy.deepcopy(
            DEPLOYER.GATEWAY_UNIT_CLOSURE)
        for label, payload in closure_payloads.items():
            self.gateway_unit_closure[label]["bytes"] = len(payload)
            self.gateway_unit_closure[label]["sha256"] = (
                hashlib.sha256(payload).hexdigest())
        for label, payload in closure_payloads.items():
            self.write_file(
                self.gateway_unit_closure[label]["path"],
                payload,
                self.gateway_unit_closure[label]["mode"])
        self.write_file(
            DEPLOYER.BROKER_INTERPRETER_PATH,
            b"fixture reviewed Python interpreter\n",
            0o755,
        )
        self.write_file(
            DEPLOYER.LOCAL_PAPER_CONTROL_PATH,
            (ROOT / "scripts/hepta_local_paper_control.py").read_bytes(),
            0o755,
        )
        self.write_file(
            DEPLOYER.TARGET_PATH, DEPLOYER.OLD_PAYLOAD, 0o644)
        self.write_file(DEPLOYER.KILL_SWITCH_PATH, b"engaged", 0o440)
        self.write_file(DEPLOYER.GLOBAL_KILL_SWITCH_PATH, b"engaged", 0o440)
        os.chmod(
            self.local(DEPLOYER.KILL_SWITCH_PATH.parent),
            DEPLOYER.KILL_SWITCH_PARENT_MODE,
        )
        os.chmod(
            self.local(DEPLOYER.GLOBAL_KILL_SWITCH_PATH.parent),
            DEPLOYER.KILL_SWITCH_PARENT_MODE,
        )

        self.patchers = [
            mock.patch.object(DEPLOYER, "FILESYSTEM_ROOT", self.root),
            mock.patch.object(DEPLOYER, "ROOT_UID", self.uid),
            mock.patch.object(DEPLOYER, "ROOT_GID", self.gid),
            mock.patch.object(DEPLOYER, "PAPER_CONTROL_GID", self.gid),
            mock.patch.object(
                DEPLOYER, "GLOBAL_PAPER_CONTROL_GID", self.gid),
            mock.patch.object(DEPLOYER, "WATCH_UID", self.uid),
            mock.patch.object(DEPLOYER, "WATCH_GID", self.gid),
            mock.patch.object(
                DEPLOYER, "EXPECTED_MANAGER_UNIT_CONTRACTS",
                self.expected_manager_unit_contracts),
            mock.patch.object(
                DEPLOYER, "GATEWAY_UNIT_CLOSURE",
                self.gateway_unit_closure),
            mock.patch.object(DEPLOYER.os, "geteuid", return_value=0),
            mock.patch.object(
                DEPLOYER, "open_verified_broker_interpreter",
                side_effect=self.open_verified_broker_interpreter),
            mock.patch.object(
                DEPLOYER, "rebind_verified_broker_interpreter",
                side_effect=self.rebind_verified_broker_interpreter),
            mock.patch.object(DEPLOYER, "command", side_effect=self.command),
            mock.patch.object(
                DEPLOYER, "validate_historical_exec_main_pid",
                side_effect=self.validate_historical_exec_main_pid),
            mock.patch.object(
                DEPLOYER, "guarded_broker_egress_check",
                side_effect=self.guarded_broker_egress_check),
            mock.patch.object(
                DEPLOYER, "acquire_shadow_install_binding",
                side_effect=self.acquire_shadow_install_binding),
            mock.patch.object(
                DEPLOYER, "validate_shadow_install_binding",
                side_effect=self.validate_shadow_install_binding),
            mock.patch.object(
                DEPLOYER, "release_shadow_install_binding",
                side_effect=self.release_shadow_install_binding),
            mock.patch.object(DEPLOYER, "SEAM_HOOK", lambda _name: None),
        ]
        for patcher in self.patchers:
            patcher.start()

    def close(self) -> None:
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temporary.cleanup()

    def local(self, path: Path) -> Path:
        return host_path(self.root, path)

    def acquire_shadow_install_binding(
        self,
        expected_manifest_sha256: str | None,
        expected_receipt_sha256: str | None,
    ) -> object:
        self.shadow_install_acquire_arguments.append((
            expected_manifest_sha256, expected_receipt_sha256))
        return self.shadow_install_binding

    def validate_shadow_install_binding(
        self,
        binding: object,
    ) -> dict[str, object]:
        if binding is not self.shadow_install_binding:
            raise DEPLOYER.DeployError("PROFILE_SHADOW_INSTALL_REBOUND")
        self.shadow_install_validation_count += 1
        return copy.deepcopy(self.shadow_install_evidence)

    def release_shadow_install_binding(self, binding: object) -> None:
        if binding is not self.shadow_install_binding:
            raise DEPLOYER.DeployError("PROFILE_SHADOW_INSTALL_RELEASE_FAILED")
        self.shadow_install_release_count += 1

    def make_directory(self, path: Path) -> Path:
        current = self.root
        for part in path.parts[1:]:
            current /= part
            current.mkdir(exist_ok=True)
            os.chmod(current, 0o755)
        return current

    def write_file(self, path: Path, payload: bytes, mode: int) -> Path:
        parent = self.make_directory(path.parent)
        local = parent / path.name
        local.write_bytes(payload)
        os.chmod(local, mode)
        return local

    def set_broker_process_identity(
        self,
        pid: int,
        invocation_id: str,
    ) -> None:
        self.broker_unit_fields["MainPID"] = str(pid)
        self.broker_unit_fields["ExecMainPID"] = str(pid)
        self.broker_unit_fields["InvocationID"] = invocation_id
        self.broker_unit_fields["ExecStart"] = (
            "{ path=/usr/libexec/hepta-broker-egress-policy ; "
            "argv[]=/usr/libexec/hepta-broker-egress-policy --supervise "
            "--paper-identities /etc/heptatrader/"
            "hepta-agent-trust-domain-paper-identities-v1.json ; "
            "ignore_errors=no ; start_time=[Sat 2026-08-01 20:38:45 CST] ; "
            f"stop_time=[n/a] ; pid={pid} ; code=(null) ; status=0/0 }}")

    def validate_historical_exec_main_pid(self, pid: int) -> None:
        self.historical_exec_main_pid_checks.append(pid)
        if not self.historical_exec_main_pid_allowed:
            raise DEPLOYER.DeployError(
                "PROFILE_BROKER_EGRESS_UNIT_NOT_OFFLINE")

    def guarded_broker_egress_check(
        self,
        broker_before: dict[str, object],
    ) -> tuple[
        subprocess.CompletedProcess[str], dict[str, object],
        dict[str, object],
    ]:
        egress = DEPLOYER.execute_verified_broker_egress_check()
        evidence = json.loads(json.dumps(self.broker_process_evidence))
        return egress, evidence, dict(broker_before)

    def expected_manager_unit_contract(self, unit: str) -> dict[str, object]:
        fields = self.manager_unit_all_fields[unit]
        if unit == DEPLOYER.BROKER_EGRESS_UNIT:
            dynamic = list(DEPLOYER.BROKER_MANAGER_DYNAMIC_PROPERTIES)
        elif unit == "hepta-tool-gateway@alpha.service":
            dynamic = list(
                DEPLOYER.GATEWAY_SERVICE_MANAGER_DYNAMIC_PROPERTIES)
        else:
            dynamic = list(
                DEPLOYER.GATEWAY_SOCKET_MANAGER_DYNAMIC_PROPERTIES)
        semantic = {
            key: value for key, value in fields.items()
            if key not in set(dynamic)
        }
        object_path = DEPLOYER.SYSTEMD_DBUS_OBJECT_PATHS[unit]
        interfaces = self.dbus_interface_properties[unit]
        interface_evidence = {}
        frozen_interfaces = {}
        for interface, properties in interfaces.items():
            schema = sorted([
                [name, value["type"]]
                for name, value in properties.items()
            ])
            interface_evidence[interface] = {
                "property_count": len(properties),
                "schema_sha256": DEPLOYER.manager_cache_json_digest(schema),
            }
            frozen_interfaces[interface] = (
                DEPLOYER.project_systemd_dbus_interface(
                    properties,
                    socket_interface=interface.endswith(".Socket"),
                ))
        frozen_document = {
            "schema": DEPLOYER.SYSTEMD_MANAGER_CACHE_SCHEMA,
            "unit": unit,
            "interfaces": frozen_interfaces,
        }
        return {
            "property_count": len(fields),
            "semantic_property_count": len(semantic),
            "semantic_sha256": DEPLOYER.digest_bytes(
                DEPLOYER.canonical_bytes(semantic)),
            "dynamic_properties": dynamic,
            "object_loaded": DEPLOYER.SYSTEMD_DBUS_EXPECTED_LOADED[unit],
            "object_path": object_path,
            "dbus_interfaces": interface_evidence,
            "frozen_property_count": sum(
                len(value) for value in frozen_interfaces.values()),
            "frozen_semantic_sha256":
                DEPLOYER.manager_cache_json_digest(frozen_document),
        }

    def command(
        self,
        arguments: list[str],
        *,
        pass_fds: tuple[int, ...] = (),
    ) -> subprocess.CompletedProcess[str]:
        self.commands.append(list(arguments))
        if (
            len(arguments) == 8
            and re.fullmatch(r"/proc/self/fd/[0-9]+", arguments[0])
            and arguments[1:4] == ["-I", "-S", "-B"]
            and re.fullmatch(r"/proc/self/fd/[0-9]+", arguments[4])
            and arguments[5:7] == ["status", "--identities"]
            and arguments[7] == str(DEPLOYER.BROKER_PAPER_IDENTITIES_PATH)
            and len(pass_fds) == 2
            and arguments[0] == f"/proc/self/fd/{pass_fds[0]}"
            and arguments[4] == f"/proc/self/fd/{pass_fds[1]}"
        ):
            return completed(
                arguments,
                stdout=json.dumps(
                    self.local_control_document, sort_keys=True) + "\n")
        if (
            len(arguments) == 6
            and re.fullmatch(r"/proc/self/fd/[0-9]+", arguments[0])
            and arguments[1:4] == ["-I", "-S", "-B"]
            and re.fullmatch(r"/proc/self/fd/[0-9]+", arguments[4])
            and arguments[5] == "--check-deny-all"
            and len(pass_fds) == 2
            and arguments[0] == f"/proc/self/fd/{pass_fds[0]}"
            and arguments[4] == f"/proc/self/fd/{pass_fds[1]}"
        ):
            return completed(
                arguments, self.broker_returncode,
                stdout=self.broker_stdout, stderr=self.broker_stderr)
        if (
            arguments[:4] == [
                DEPLOYER.SYSTEMCTL, "show", "--all", "--no-pager"]
            and len(arguments) == 5
            and arguments[4] in DEPLOYER.MANAGER_UNIT_CONTRACT_UNITS
        ):
            fields = self.manager_unit_all_fields[arguments[4]]
            return completed(
                arguments,
                stderr=self.systemctl_stderr,
                stdout="".join(
                    f"{field}={value}\n" for field, value in fields.items()),
            )
        if (
            arguments[:8] == [
                DEPLOYER.BUSCTL, "--system", "--json=short", "call",
                DEPLOYER.SYSTEMD_DBUS_DESTINATION,
                DEPLOYER.SYSTEMD_DBUS_MANAGER_PATH,
                DEPLOYER.SYSTEMD_DBUS_MANAGER_INTERFACE,
                "GetUnit",
            ]
            and len(arguments) == 10
            and arguments[8] == "s"
            and arguments[9] in DEPLOYER.MANAGER_UNIT_CONTRACT_UNITS
        ):
            unit = arguments[9]
            object_path = DEPLOYER.SYSTEMD_DBUS_OBJECT_PATHS[unit]
            if not DEPLOYER.SYSTEMD_DBUS_EXPECTED_LOADED[unit]:
                return completed(
                    arguments, 1,
                    stderr=f"Call failed: Unit {unit} not loaded.\n")
            return completed(
                arguments,
                stdout=json.dumps(
                    {"type": "o", "data": [object_path]},
                    separators=(",", ":")) + "\n",
            )
        if (
            arguments[:4] == [
                DEPLOYER.BUSCTL, "--system", "--json=short", "call"]
            and len(arguments) == 10
            and arguments[4] == DEPLOYER.SYSTEMD_DBUS_DESTINATION
            and arguments[6] == DEPLOYER.SYSTEMD_DBUS_PROPERTIES_INTERFACE
            and arguments[7:9] == ["GetAll", "s"]
        ):
            object_path = arguments[5]
            interface = arguments[9]
            unit = next((
                candidate
                for candidate, candidate_path
                in DEPLOYER.SYSTEMD_DBUS_OBJECT_PATHS.items()
                if candidate_path == object_path
            ), None)
            properties = (
                self.dbus_interface_properties.get(unit, {}).get(interface)
            )
            if properties is None:
                return completed(arguments, 127, stderr="unexpected command")
            return completed(
                arguments,
                stdout=json.dumps({
                    "type": "a{sv}", "data": [properties],
                }, separators=(",", ":")) + "\n",
            )
        if arguments == [
            DEPLOYER.SYSTEMCTL, "show", "--no-pager",
            "--property=Version", "--property=Features",
            "--property=UnitPath",
            "--property=Environment",
        ]:
            return completed(
                arguments, stderr=self.manager_stderr,
                stdout=(
                    f"Version={self.manager_version}\n"
                    f"Features={self.manager_features}\n"
                    f"UnitPath={self.manager_unit_path}\n"
                    f"Environment={self.manager_environment}\n"))
        if (
            arguments[:3] == [
                DEPLOYER.SYSTEMCTL, "show", "--no-pager"]
            and arguments[-1] == DEPLOYER.BROKER_EGRESS_UNIT
        ):
            requested = {
                argument.removeprefix("--property=")
                for argument in arguments[3:-1]
                if argument.startswith("--property=")
            }
            if requested == set(self.broker_offline_fields):
                fields = self.broker_offline_fields
            elif requested == set(self.broker_unit_fields):
                fields = self.broker_unit_fields
            else:
                return completed(arguments, 127, stderr="unexpected command")
            return completed(
                arguments,
                stderr=self.systemctl_stderr,
                stdout="".join(
                    f"{field}={fields[field]}\n"
                    for field in fields),
            )
        if arguments[:2] == [DEPLOYER.SYSTEMCTL, "show"]:
            unit = arguments[-1]
            active = unit == self.active_unit
            failed = unit in self.failed_units
            masked = (
                unit in DEPLOYER.GATEWAY_BOUNDARY_UNITS
                and "--property=Id" in arguments)
            fragment_path = self.gateway_fragment_path or str(
                DEPLOYER.PERSISTENT_MASK_ROOT / unit)
            extra = (
                f"Id={unit}\n"
                f"UnitFileState={self.gateway_unit_file_state}\n"
                f"FragmentPath={fragment_path}\n"
                f"SourcePath={self.gateway_source_paths[unit]}\n"
                f"DropInPaths={self.gateway_drop_in_paths[unit]}\n"
                f"Names={self.gateway_names[unit]}\n"
                f"Wants={self.gateway_wants[unit]}\n"
                f"Requires={self.gateway_requires[unit]}\n"
                f"Upholds={self.gateway_upholds[unit]}\n"
                f"BindsTo={self.gateway_binds_to[unit]}\n"
                f"After={self.gateway_after[unit]}\n"
                f"NeedDaemonReload={self.gateway_need_daemon_reload[unit]}\n"
                if masked else "")
            active_state = (
                "active" if active else "failed" if failed else "inactive")
            sub_state = (
                "running" if active else "failed" if failed else "dead")
            return completed(
                arguments,
                stderr=self.systemctl_stderr,
                stdout=(
                    f"LoadState={self.gateway_load_state if masked else 'loaded'}\n"
                    f"ActiveState={active_state}\n"
                    f"SubState={sub_state}\n"
                    f"Job={self.unit_jobs[unit]}\n"
                    f"{extra}"))
        return completed(arguments, 127, stderr="unexpected command")

    def open_verified_broker_interpreter(
        self,
        reason: str,
    ) -> tuple[int, os.stat_result]:
        del reason
        descriptor = os.open(
            self.local(DEPLOYER.BROKER_INTERPRETER_PATH), os.O_RDONLY)
        return descriptor, os.fstat(descriptor)

    def rebind_verified_broker_interpreter(
        self,
        descriptor: int,
        expected: os.stat_result,
        reason: str,
    ) -> os.stat_result:
        opened = os.fstat(descriptor)
        entry = os.stat(
            self.local(DEPLOYER.BROKER_INTERPRETER_PATH),
            follow_symlinks=False,
        )
        if (
            DEPLOYER.stable_identity(expected)
            != DEPLOYER.stable_identity(opened)
            or DEPLOYER.stable_identity(opened)
            != DEPLOYER.stable_identity(entry)
        ):
            raise DEPLOYER.DeployError(reason)
        return opened


class WatchProfileDeployerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = Fixture()

    def tearDown(self) -> None:
        self.fixture.close()

    def assert_reason(self, expected: str) -> None:
        with self.assertRaises(DEPLOYER.DeployError) as raised:
            DEPLOYER.deploy()
        self.assertEqual(raised.exception.reason, expected)

    def test_reviewed_executable_capture_has_a_separate_bounded_limit(
            self) -> None:
        payload = b"x" * (DEPLOYER.MAXIMUM_FILE_BYTES + 1)
        self.fixture.write_file(
            DEPLOYER.SHADOW_INSTALLER_PATH, payload, 0o755)

        with self.assertRaises(DEPLOYER.DeployError) as ordinary:
            DEPLOYER.read_anchored_file(
                DEPLOYER.SHADOW_INSTALLER_PATH,
                "PROFILE_TEST_ORDINARY_FILE_TOO_LARGE")
        self.assertEqual(
            ordinary.exception.reason,
            "PROFILE_TEST_ORDINARY_FILE_TOO_LARGE")

        captured = DEPLOYER.read_anchored_file(
            DEPLOYER.SHADOW_INSTALLER_PATH,
            "PROFILE_TEST_REVIEWED_EXECUTABLE_INVALID",
            maximum_bytes=DEPLOYER.MAXIMUM_REVIEWED_EXECUTABLE_BYTES)
        self.assertEqual(captured.payload, payload)

        oversized = b"y" * (
            DEPLOYER.MAXIMUM_REVIEWED_EXECUTABLE_BYTES + 1)
        self.fixture.local(DEPLOYER.SHADOW_INSTALLER_PATH).write_bytes(
            oversized)
        with self.assertRaises(DEPLOYER.DeployError) as reviewed:
            DEPLOYER.read_anchored_file(
                DEPLOYER.SHADOW_INSTALLER_PATH,
                "PROFILE_TEST_REVIEWED_EXECUTABLE_INVALID",
                maximum_bytes=DEPLOYER.MAXIMUM_REVIEWED_EXECUTABLE_BYTES)
        self.assertEqual(
            reviewed.exception.reason,
            "PROFILE_TEST_REVIEWED_EXECUTABLE_INVALID")

    def test_reviewed_executable_capture_limit_is_strictly_validated(
            self) -> None:
        self.fixture.write_file(
            DEPLOYER.SHADOW_INSTALLER_PATH, b"reviewed\n", 0o755)
        for invalid in (
            True, 0, -1, DEPLOYER.MAXIMUM_REVIEWED_EXECUTABLE_BYTES + 1,
        ):
            with self.subTest(invalid=invalid), self.assertRaises(
                    DEPLOYER.DeployError) as raised:
                DEPLOYER.read_anchored_file(
                    DEPLOYER.SHADOW_INSTALLER_PATH,
                    "PROFILE_TEST_REVIEWED_EXECUTABLE_INVALID",
                    maximum_bytes=invalid)
            self.assertEqual(
                raised.exception.reason,
                "PROFILE_INTERNAL_READ_LIMIT_INVALID")

    def test_frozen_shadow_helpers_fit_reviewed_executable_limit(self) -> None:
        self.assertEqual(
            DEPLOYER.MAXIMUM_REVIEWED_EXECUTABLE_BYTES, 384 * 1024)
        for path in (
            ROOT / "scripts/hepta_shadow_host_installer.py",
            MODULE_PATH,
        ):
            with self.subTest(path=path.name):
                size = path.stat().st_size
                self.assertGreater(size, DEPLOYER.MAXIMUM_FILE_BYTES)
                self.assertLessEqual(
                    size, DEPLOYER.MAXIMUM_REVIEWED_EXECUTABLE_BYTES)
                self.assertGreater(
                    DEPLOYER.MAXIMUM_REVIEWED_EXECUTABLE_BYTES - size,
                    32 * 1024)

    def test_shadow_helper_bootstrap_call_sites_capture_real_frozen_bytes(
            self) -> None:
        installer_payload = (
            ROOT / "scripts/hepta_shadow_host_installer.py").read_bytes()
        installer_sha256 = DEPLOYER.digest_bytes(installer_payload)
        self.fixture.write_file(
            DEPLOYER.SHADOW_INSTALLER_PATH, installer_payload, 0o755)
        manifest_payload = DEPLOYER.canonical_bytes({
            "schema": "hepta.shadow-runtime-install-manifest.v2",
            "version": 2,
            "archive_sha256": "sha256:" + "1" * 64,
            "source_baseline_sha256": "sha256:" + "2" * 64,
            "installer_sha256": installer_sha256,
            "files": [{
                "path": DEPLOYER.SHADOW_INSTALLER_MEMBER,
                "mode": "0755",
                "size": len(installer_payload),
                "sha256": installer_sha256,
            }],
            "paper_authorized": False,
            "live_authorized": False,
            "mutation_attempted": False,
            "direct_broker_access": False,
        })
        self.fixture.write_file(
            DEPLOYER.SHADOW_INSTALL_MANIFEST_PATH,
            manifest_payload, 0o600)

        _consumer, captured_installer = (
            DEPLOYER._load_shadow_install_consumer(
                DEPLOYER.digest_bytes(manifest_payload)))
        self.assertEqual(captured_installer, installer_payload)

        profile_payload = MODULE_PATH.read_bytes()
        installed_profile = Path(
            "/usr/libexec/hepta-p1-watch-profile-deployer")
        self.fixture.write_file(installed_profile, profile_payload, 0o755)
        with mock.patch.object(
                DEPLOYER, "__file__", str(installed_profile)):
            self.assertEqual(
                DEPLOYER._profile_caller_payload(), profile_payload)

    def hard_crash_at(self, seam: str) -> None:
        fired = False

        def crash(name: str) -> None:
            nonlocal fired
            if name == seam and not fired:
                fired = True
                raise SystemExit(f"hard crash at {seam}")

        with mock.patch.object(DEPLOYER, "SEAM_HOOK", crash):
            with self.assertRaises(SystemExit):
                DEPLOYER.deploy()
        self.assertTrue(fired)

    def process_crash_at(self, seam: str) -> None:
        """Kill the worker without Python cleanup and wait for lock release."""

        child = os.fork()
        if child == 0:
            def crash(name: str) -> None:
                if name == seam:
                    os._exit(97)

            DEPLOYER.SEAM_HOOK = crash
            try:
                DEPLOYER.deploy()
            except BaseException:
                os._exit(99)
            os._exit(98)
        waited, status = os.waitpid(child, 0)
        self.assertEqual(waited, child)
        self.assertTrue(os.WIFEXITED(status), status)
        self.assertEqual(os.WEXITSTATUS(status), 97)

    def crash_during_fsync_after_seam(self, seam: str) -> None:
        real_fsync = os.fsync
        armed = False
        fired = False

        def arm(name: str) -> None:
            nonlocal armed
            if name == seam:
                armed = True

        def crash(descriptor: int) -> None:
            nonlocal armed, fired
            if armed and not fired:
                armed = False
                fired = True
                raise SystemExit(f"hard crash inside fsync after {seam}")
            real_fsync(descriptor)

        with (
            mock.patch.object(DEPLOYER, "SEAM_HOOK", arm),
            mock.patch.object(DEPLOYER.os, "fsync", side_effect=crash),
        ):
            with self.assertRaises(SystemExit):
                DEPLOYER.deploy()
        self.assertTrue(fired)

    def assert_fsync_failure_at(self, seam: str, expected: str) -> None:
        real_fsync = os.fsync
        armed = False
        fired = False

        def arm(name: str) -> None:
            nonlocal armed
            if name == seam:
                armed = True

        def fail(descriptor: int) -> None:
            nonlocal armed, fired
            if armed and not fired:
                armed = False
                fired = True
                raise OSError(f"injected fsync failure after {seam}")
            real_fsync(descriptor)

        with (
            mock.patch.object(DEPLOYER, "SEAM_HOOK", arm),
            mock.patch.object(DEPLOYER.os, "fsync", side_effect=fail),
        ):
            self.assert_reason(expected)
        self.assertTrue(fired)

    def exercise_lock_rebound_with_second_helper(
        self,
        fixture: Fixture,
        seam: str,
        *,
        induce_rollback: bool = False,
    ) -> list[tuple[str, str]]:
        lock = fixture.local(DEPLOYER.LOCK_PATH)
        swapped = False
        rollback_armed = induce_rollback
        second: list[tuple[str, str]] = []

        def hook(name: str) -> None:
            nonlocal swapped, rollback_armed
            if rollback_armed and name == "after_postflight":
                rollback_armed = False
                raise DEPLOYER.DeployError("PROFILE_TEST_TRIGGER_ROLLBACK")
            if name != seam or swapped:
                return
            swapped = True
            saved = lock.with_name("attacker-preserved-old.lock")
            lock.rename(saved)
            replacement = lock.with_name("attacker-new.lock")
            replacement.write_bytes(b"")
            os.chmod(replacement, 0o600)
            replacement.rename(lock)
            with mock.patch.object(DEPLOYER, "SEAM_HOOK", lambda _name: None):
                try:
                    second.append(("PASS", DEPLOYER.deploy()))
                except DEPLOYER.DeployError as error:
                    second.append(("ERROR", error.reason))

        with mock.patch.object(DEPLOYER, "SEAM_HOOK", hook):
            with self.assertRaises(DEPLOYER.DeployError) as raised:
                DEPLOYER.deploy()
        self.assertEqual(raised.exception.reason, "PROFILE_LOCK_REBOUND")
        self.assertTrue(swapped)
        self.assertEqual(len(second), 1)
        self.assertTrue(lock.with_name("attacker-preserved-old.lock").exists())
        return second

    def assert_p3_state(self, fixture: Fixture) -> None:
        self.assertEqual(
            fixture.local(DEPLOYER.TARGET_PATH).read_bytes(),
            DEPLOYER.OLD_PAYLOAD)
        self.assertEqual(
            fixture.local(DEPLOYER.BACKUP_PATH).read_bytes(),
            DEPLOYER.OLD_PAYLOAD)
        self.assertEqual(
            fixture.local(DEPLOYER.TARGET_TEMP_PATH).read_bytes(),
            DEPLOYER.NEW_PAYLOAD)
        for path in (
            DEPLOYER.RECEIPT_PATH,
            DEPLOYER.BACKUP_TEMP_PATH,
            DEPLOYER.RECEIPT_TEMP_PATH,
        ):
            self.assertFalse(fixture.local(path).exists(), str(path))

    def assert_success_state(self, fixture: Fixture) -> str:
        self.assertEqual(
            fixture.local(DEPLOYER.TARGET_PATH).read_bytes(),
            DEPLOYER.NEW_PAYLOAD)
        self.assertEqual(
            fixture.local(DEPLOYER.TARGET_TEMP_PATH).read_bytes(),
            DEPLOYER.OLD_PAYLOAD)
        self.assertEqual(
            fixture.local(DEPLOYER.BACKUP_PATH).read_bytes(),
            DEPLOYER.OLD_PAYLOAD)
        receipt = fixture.local(DEPLOYER.RECEIPT_PATH).read_bytes()
        self.assertFalse(fixture.local(DEPLOYER.BACKUP_TEMP_PATH).exists())
        self.assertFalse(fixture.local(DEPLOYER.RECEIPT_TEMP_PATH).exists())
        return DEPLOYER.digest_bytes(receipt)

    def install_disabled_campaign_policy(self) -> None:
        digest = "sha256:" + "a" * 64
        document = {
            "schema": "hepta.ib-paper-campaign-policy.v5",
            "version": 5,
            "campaign_id": "disabled-test-seed",
            "domain_id": "alpha",
            "enabled": False,
            "mutations_authorized": False,
            "paper_only": True,
            "live_authorized": False,
            "strategy_id": "test",
            "strategy_version": "1",
            "strategy_sha256": digest,
            "valid_after_ms": 0,
            "expires_at_ms": 0,
            "allowed_instruments": ["EUR.USD"],
            "max_cycles": 0,
            "max_quantity": 25000,
            "min_cycle_interval_ms": 0,
            "operator_ttl_seconds": 0,
            "max_intent_horizon_ms": 0,
            "max_holding_ms": 0,
            "max_active_orders": 1,
            "order_type": "MKT",
            "tif": "DAY",
            "end_flat_required": True,
            "source_baseline_sha256": digest,
            "admission_mode": "local-only",
            "deployment_evidence_file_sha256": digest,
            "deployment_evidence_body_sha256": digest,
            "deployment_install_transaction_id": "install-test-114",
        }
        self.fixture.write_file(
            DEPLOYER.PAPER_POLICY_PATH,
            DEPLOYER.canonical_bytes(document), 0o600)

    def prepare_round114_rebind_layout(
        self, *, transition: bool = True,
    ) -> dict[str, object]:
        """Create exact synthetic Round86 and Round95 predecessor receipts."""

        DEPLOYER.deploy()
        receipt_path = self.fixture.local(DEPLOYER.RECEIPT_PATH)
        document = json.loads(receipt_path.read_bytes())
        evidence = document["shadow_install_evidence"]
        evidence["receipt_path"] = str(
            DEPLOYER.LEGACY_SHADOW_INSTALL_RECEIPT_PATH)
        evidence["manifest_path"] = str(
            DEPLOYER.LEGACY_SHADOW_INSTALL_MANIFEST_PATH)
        evidence["backup_root"] = str(
            DEPLOYER.LEGACY_SHADOW_INSTALL_BACKUP_ROOT)
        evidence["install_generation"] = (
            DEPLOYER.LEGACY_SHADOW_INSTALL_GENERATION)
        evidence["installed_file_count"] = (
            DEPLOYER.LEGACY_SHADOW_INSTALL_FILE_COUNT)
        evidence["schema"] = (
            "hepta.shadow-runtime-install-consumption-evidence.v2")
        evidence["version"] = 2
        evidence.pop("predecessor_install_generation")
        evidence.pop(
            "predecessor_current_install_pointer_file_sha256")
        for preflight_name in ("preflight_before", "preflight_after"):
            contracts = document[preflight_name]["manager_unit_contracts"]
            for unit, (
                    semantic_count, semantic_sha256,
                    frozen_semantic_sha256) in (
                    DEPLOYER.LEGACY_GATEWAY_MANAGER_SEMANTICS.items()):
                contracts[unit]["semantic_property_count"] = semantic_count
                contracts[unit]["semantic_sha256"] = semantic_sha256
                contracts[unit]["dynamic_properties"] = []
                contracts[unit]["frozen_semantic_sha256"] = (
                    frozen_semantic_sha256)
        body = dict(document)
        body.pop("body_sha256")
        body_sha256 = DEPLOYER.digest_bytes(DEPLOYER.canonical_bytes(body))
        document["body_sha256"] = body_sha256
        payload = DEPLOYER.canonical_bytes(document)
        receipt_path.write_bytes(payload)
        os.chmod(receipt_path, 0o600)
        pins: dict[str, object] = {
            "LEGACY_RECEIPT_FILE_SHA256": DEPLOYER.digest_bytes(payload),
            "LEGACY_RECEIPT_BODY_SHA256": body_sha256,
            "LEGACY_RECEIPT_BYTES": len(payload),
        }
        with mock.patch.multiple(DEPLOYER, **pins):
            legacy = DEPLOYER.optional_secure_file(
                DEPLOYER.RECEIPT_PATH, 0o600,
                "PROFILE_TEST_LEGACY_RECEIPT_INVALID")
            assert legacy is not None
            target = DEPLOYER.require_exact_file(
                DEPLOYER.TARGET_PATH, DEPLOYER.NEW_PAYLOAD, 0o644,
                self.fixture.uid, self.fixture.gid,
                "PROFILE_TEST_TARGET_INVALID")
            backup = DEPLOYER.require_exact_file(
                DEPLOYER.BACKUP_PATH, DEPLOYER.OLD_PAYLOAD, 0o600,
                self.fixture.uid, self.fixture.gid,
                "PROFILE_TEST_BACKUP_INVALID")
            retained = DEPLOYER.require_exact_file(
                DEPLOYER.TARGET_TEMP_PATH, DEPLOYER.OLD_PAYLOAD, 0o644,
                self.fixture.uid, self.fixture.gid,
                "PROFILE_TEST_RETAINED_INVALID")
            round95_evidence = copy.deepcopy(self.fixture.shadow_install_evidence)
            round95_evidence["receipt_path"] = str(
                DEPLOYER.ROUND95_SHADOW_INSTALL_RECEIPT_PATH)
            round95_evidence["manifest_path"] = str(
                DEPLOYER.ROUND95_SHADOW_INSTALL_MANIFEST_PATH)
            round95_evidence["backup_root"] = str(
                DEPLOYER.ROUND95_SHADOW_INSTALL_BACKUP_ROOT)
            round95_evidence["install_generation"] = (
                DEPLOYER.ROUND95_SHADOW_INSTALL_GENERATION)
            round95_evidence["predecessor_install_generation"] = (
                DEPLOYER.ROUND95_SHADOW_PREDECESSOR_INSTALL_GENERATION)
            round95_evidence[
                "predecessor_current_install_pointer_file_sha256"] = (
                    DEPLOYER.ROUND95_SHADOW_PREDECESSOR_POINTER_SHA256)
            round95_evidence["installed_file_count"] = (
                DEPLOYER.ROUND95_SHADOW_INSTALL_FILE_COUNT)
            preflight = DEPLOYER.safety_preflight()
            body = {
                "schema": DEPLOYER.ROUND95_RECEIPT_SCHEMA,
                "version": DEPLOYER.ROUND95_RECEIPT_VERSION,
                "status": DEPLOYER.ROUND95_RECEIPT_STATUS,
                "round": 95,
                "domain": "alpha",
                "started_at_ms": 1,
                "finished_at_ms": 2,
                "target_path": str(DEPLOYER.TARGET_PATH),
                "receipt_staging_path": str(
                    DEPLOYER.ROUND95_RECEIPT_TEMP_PATH),
                "target_before": DEPLOYER.profile_file_evidence(
                    DEPLOYER.TARGET_PATH, target),
                "target_after": DEPLOYER.profile_file_evidence(
                    DEPLOYER.TARGET_PATH, target),
                "target_final": DEPLOYER.profile_file_evidence(
                    DEPLOYER.TARGET_PATH, target),
                "legacy_receipt": (
                    DEPLOYER.historical_round86_receipt_evidence(legacy)),
                "legacy_backup": DEPLOYER.profile_file_evidence(
                    DEPLOYER.BACKUP_PATH, backup),
                "legacy_retained_target": DEPLOYER.profile_file_evidence(
                    DEPLOYER.TARGET_TEMP_PATH, retained),
                "preflight_before": preflight,
                "preflight_after": preflight,
                "preflight_final": preflight,
                "profile_content_changed": False,
                "target_written": False,
                "target_replaced": False,
                "services_started": False,
                "services_stopped": False,
                "services_restarted": False,
                "campaign_launched": False,
                "paper_authorized": False,
                "live_authorized": False,
                "mutation_attempted": False,
                "direct_broker_access": False,
                "activation_receipt_eligible": False,
                "preflight_reusable_for_activation": False,
                "broker_loaded_source_attested": False,
                "broker_deny_all_continuity_attested": False,
                "fresh_activation_transaction_required": True,
                "shadow_install_evidence": round95_evidence,
            }
            predecessor = dict(body)
            predecessor["body_sha256"] = DEPLOYER.digest_bytes(
                DEPLOYER.canonical_bytes(body))
            predecessor_payload = DEPLOYER.canonical_bytes(predecessor)
            self.fixture.write_file(
                DEPLOYER.ROUND95_RECEIPT_PATH, predecessor_payload, 0o600)
        pins.update({
            "ROUND95_RECEIPT_FILE_SHA256":
                DEPLOYER.digest_bytes(predecessor_payload),
            "ROUND95_RECEIPT_BODY_SHA256": predecessor["body_sha256"],
            "ROUND95_RECEIPT_BYTES": len(predecessor_payload),
        })
        dormant = synthetic_dormant_paper_payload()
        pins.update({
            "DORMANT_PAPER_BYTES": len(dormant),
            "DORMANT_PAPER_SHA256": hashlib.sha256(dormant).hexdigest(),
        })
        self.fixture.local(DEPLOYER.TARGET_PATH).write_bytes(dormant)
        os.chmod(self.fixture.local(DEPLOYER.TARGET_PATH), 0o644)
        self.install_disabled_campaign_policy()
        if transition:
            with mock.patch.multiple(DEPLOYER, **pins):
                DEPLOYER.deploy(
                    expected_prior_profile_receipt_sha256=
                        DEPLOYER.ROUND95_RECEIPT_FILE_SHA256,
                    transition_token=DEPLOYER.ROUND114_TRANSITION_TOKEN)
        return pins

    def deploy_round114(self) -> str:
        return DEPLOYER.deploy(
            expected_prior_profile_receipt_sha256=
                DEPLOYER.ROUND95_RECEIPT_FILE_SHA256)

    def transition_round114(self) -> str:
        return DEPLOYER.deploy(
            expected_prior_profile_receipt_sha256=
                DEPLOYER.ROUND95_RECEIPT_FILE_SHA256,
            transition_token=DEPLOYER.ROUND114_TRANSITION_TOKEN)

    def test_embedded_profiles_are_exact(self) -> None:
        self.assertEqual(len(DEPLOYER.OLD_PAYLOAD), 677)
        self.assertEqual(
            hashlib.sha256(DEPLOYER.OLD_PAYLOAD).hexdigest(),
            "2397f4c86156adaa9dca0e929e727b827080312fd57ede3ffd1597d1bdc37ea1")
        self.assertEqual(len(DEPLOYER.NEW_PAYLOAD), 736)
        self.assertEqual(
            hashlib.sha256(DEPLOYER.NEW_PAYLOAD).hexdigest(),
            "ffcde4c46237ecacb3c32603f3aca0ba1a51c5b353b4fd2e5ab2f42ca1470e3f")
        old_lines = DEPLOYER.OLD_PAYLOAD.splitlines()
        new_lines = DEPLOYER.NEW_PAYLOAD.splitlines()
        self.assertEqual(
            new_lines,
            old_lines[:10] + [
                b"HEPTA_TOOL_CONTRACT_BINDINGS=EUR.USD|EUR|CASH|IDEALPRO|USD"
            ] + old_lines[10:])

    def test_round114_rebind_and_round95_predecessor_constants_are_frozen(
            self) -> None:
        self.assertEqual(
            DEPLOYER.ROUND95_RECEIPT_PATH,
            Path("/var/lib/heptatrader/p1-watch-profile-receipts/"
                 "round95-generation20.json"))
        self.assertEqual(
            DEPLOYER.ROUND114_RECEIPT_PATH,
            Path("/var/lib/heptatrader/p1-watch-profile-receipts/"
                 "round114-generation22.json"))
        self.assertNotEqual(
            DEPLOYER.ROUND114_RECEIPT_TEMP_PATH, DEPLOYER.RECEIPT_TEMP_PATH)
        self.assertEqual(
            DEPLOYER.ROUND95_RECEIPT_SCHEMA,
            "hepta.p1-watch-profile-deployment-receipt.v7")
        self.assertEqual(DEPLOYER.ROUND95_RECEIPT_VERSION, 7)
        self.assertEqual(
            DEPLOYER.ROUND114_RECEIPT_SCHEMA,
            "hepta.p1-watch-profile-deployment-receipt.v8")
        self.assertEqual(DEPLOYER.ROUND114_RECEIPT_VERSION, 8)
        self.assertEqual(DEPLOYER.ROUND114_TRANSITION_RECEIPT_VERSION, 2)
        self.assertEqual(DEPLOYER.ROUND114_TRANSITION_PREIMAGE_VERSION, 1)
        self.assertEqual(
            DEPLOYER.ROUND114_TRANSITION_PREIMAGE_PATH,
            Path("/var/lib/heptatrader/p1-watch-profile-backups/"
                 "round114-dormant-paper-to-watch/preimage-evidence.json"))
        self.assertEqual(DEPLOYER.CURRENT_SHADOW_INSTALL_GENERATION, 22)
        self.assertEqual(
            DEPLOYER.CURRENT_SHADOW_PREDECESSOR_INSTALL_GENERATION, 21)
        self.assertEqual(DEPLOYER.SHADOW_INSTALL_FILE_COUNT, 128)
        self.assertEqual(DEPLOYER.LEGACY_SHADOW_INSTALL_GENERATION, 3)
        self.assertEqual(DEPLOYER.LEGACY_SHADOW_INSTALL_FILE_COUNT, 73)
        self.assertIn("round114", str(DEPLOYER.SHADOW_INSTALL_RECEIPT_PATH))
        self.assertIn(
            "round94", str(DEPLOYER.LEGACY_SHADOW_INSTALL_RECEIPT_PATH))
        self.assertEqual(
            DEPLOYER.LEGACY_RECEIPT_FILE_SHA256,
            "sha256:3904f17a444fb7a6a482b187c081c9a8eba854d39dd476ff948477eb7b9376aa")
        self.assertEqual(
            DEPLOYER.LEGACY_RECEIPT_BODY_SHA256,
            "sha256:17fcaee75ce5a3bc67f944b3d0fc5bc63512a39f4d85dc6e2b04f71af81da4ff")
        self.assertEqual(
            DEPLOYER.ROUND95_RECEIPT_FILE_SHA256,
            "sha256:c1557c1fe0bbab68bfc0c85148f2dcb3b32a2c8b75da7b229296d1b99daebd67")
        self.assertEqual(
            DEPLOYER.ROUND95_RECEIPT_BODY_SHA256,
            "sha256:e09712acbfed117a47ad5e86c63bbfe638ec38d89d7579e85b47409b57728fb2")

    def test_round114_explicit_dormant_transition_is_forward_only_and_bound(
            self) -> None:
        pins = self.prepare_round114_rebind_layout(transition=False)
        dormant = synthetic_dormant_paper_payload()
        with mock.patch.multiple(DEPLOYER, **pins):
            receipt_sha256 = self.transition_round114()
            self.assertEqual(self.transition_round114(), receipt_sha256)
        target = self.fixture.local(DEPLOYER.TARGET_PATH)
        backup = self.fixture.local(DEPLOYER.ROUND114_TRANSITION_BACKUP_PATH)
        retained = self.fixture.local(
            DEPLOYER.ROUND114_TRANSITION_TARGET_TEMP_PATH)
        preimage_path = self.fixture.local(
            DEPLOYER.ROUND114_TRANSITION_PREIMAGE_PATH)
        receipt_path = self.fixture.local(
            DEPLOYER.ROUND114_TRANSITION_RECEIPT_PATH)
        self.assertEqual(target.read_bytes(), DEPLOYER.NEW_PAYLOAD)
        self.assertEqual(backup.read_bytes(), dormant)
        self.assertEqual(retained.read_bytes(), dormant)
        self.assertEqual(stat.S_IMODE(backup.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(retained.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(preimage_path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(receipt_path.stat().st_mode), 0o600)
        receipt = json.loads(receipt_path.read_bytes())
        preimage = json.loads(preimage_path.read_bytes())
        self.assertEqual(receipt["version"], 2)
        self.assertEqual(
            receipt["preimage_evidence"]["body_sha256"],
            preimage["body_sha256"])
        self.assertEqual(
            receipt["target_before"], preimage["target_before"])
        self.assertEqual(
            receipt["target_before"]["mode"], stat.S_IFREG | 0o644)
        self.assertEqual(
            receipt["retained_target"]["mode"], stat.S_IFREG | 0o600)
        self.assertEqual(
            set(receipt["preflight_final"]["kill_switches"]), {
                str(DEPLOYER.GLOBAL_KILL_SWITCH_PATH),
                str(DEPLOYER.KILL_SWITCH_PATH),
            })
        self.assertNotIn(b"TEST12345", preimage_path.read_bytes())
        self.assertNotIn(b"TEST12345", receipt_path.read_bytes())
        for arguments in self.fixture.commands:
            self.assertFalse(any(
                member in {"start", "stop", "restart", "enable", "unmask"}
                for member in arguments), arguments)

    def test_round114_transition_idempotent_recheck_rejects_bound_artifact_race(
            self) -> None:
        pins = self.prepare_round114_rebind_layout(transition=False)
        fired = False

        def replace_backup(name: str) -> None:
            nonlocal fired
            if name != "after_transition_existing_receipt_parent_fsync" or fired:
                return
            fired = True
            self.fixture.local(
                DEPLOYER.ROUND114_TRANSITION_BACKUP_PATH).write_bytes(
                    b"tampered\n")

        with mock.patch.multiple(DEPLOYER, **pins):
            self.transition_round114()
            with mock.patch.object(
                    DEPLOYER, "SEAM_HOOK", side_effect=replace_backup), \
                    self.assertRaises(DEPLOYER.DeployError) as raised:
                self.transition_round114()
        self.assertTrue(fired)
        self.assertEqual(
            raised.exception.reason, "PROFILE_TRANSITION_BACKUP_REBOUND")
        self.assertEqual(
            self.fixture.local(DEPLOYER.TARGET_PATH).read_bytes(),
            DEPLOYER.NEW_PAYLOAD)

    def test_round114_transition_requires_explicit_exact_token(self) -> None:
        pins = self.prepare_round114_rebind_layout(transition=False)
        target = self.fixture.local(DEPLOYER.TARGET_PATH)
        before = target.read_bytes()
        with mock.patch.multiple(DEPLOYER, **pins):
            with self.assertRaises(DEPLOYER.DeployError) as missing:
                self.deploy_round114()
            self.assertEqual(missing.exception.reason, "PROFILE_REBIND_REQUIRED")
            with self.assertRaises(DEPLOYER.DeployError) as wrong:
                DEPLOYER.deploy(
                    expected_prior_profile_receipt_sha256=
                        DEPLOYER.ROUND95_RECEIPT_FILE_SHA256,
                    transition_token="wrong")
            self.assertEqual(
                wrong.exception.reason, "PROFILE_TRANSITION_TOKEN_INVALID")
        self.assertEqual(target.read_bytes(), before)
        for path in (
            DEPLOYER.ROUND114_TRANSITION_BACKUP_PATH,
            DEPLOYER.ROUND114_TRANSITION_PREIMAGE_PATH,
            DEPLOYER.ROUND114_TRANSITION_RECEIPT_PATH,
        ):
            self.assertFalse(self.fixture.local(path).exists())

    def test_round114_transition_requires_both_exact_kill_switches(self) -> None:
        pins = self.prepare_round114_rebind_layout(transition=False)
        for path in (
            DEPLOYER.GLOBAL_KILL_SWITCH_PATH, DEPLOYER.KILL_SWITCH_PATH):
            marker = self.fixture.local(path)
            os.chmod(marker, 0o600)
            marker.write_bytes(b"released")
            with self.subTest(path=path), mock.patch.multiple(
                    DEPLOYER, **pins), self.assertRaises(
                        DEPLOYER.DeployError) as raised:
                self.transition_round114()
            self.assertEqual(
                raised.exception.reason, "PROFILE_KILL_SWITCH_INVALID")
            marker.write_bytes(b"engaged")
            os.chmod(marker, 0o440)

    def test_round114_transition_rejects_both_kill_switch_rebounds(self) -> None:
        cases = (
            (DEPLOYER.GLOBAL_KILL_SWITCH_PATH,
             "after_transition_global_kill_switch_open"),
            (DEPLOYER.KILL_SWITCH_PATH,
             "after_transition_kill_switch_open"),
        )
        for index, (path, seam) in enumerate(cases):
            if index:
                self.fixture.close()
                self.fixture = Fixture()
            pins = self.prepare_round114_rebind_layout(transition=False)
            fired = False

            def replace(name: str) -> None:
                nonlocal fired
                if name != seam or fired:
                    return
                fired = True
                marker = self.fixture.local(path)
                replacement = marker.with_name(marker.name + ".replacement")
                replacement.write_bytes(b"engaged")
                os.chmod(replacement, 0o440)
                os.replace(replacement, marker)

            with self.subTest(path=path), mock.patch.multiple(
                    DEPLOYER, **pins), mock.patch.object(
                        DEPLOYER, "SEAM_HOOK", side_effect=replace), \
                    self.assertRaises(DEPLOYER.DeployError) as raised:
                self.transition_round114()
            self.assertTrue(fired)
            self.assertEqual(
                raised.exception.reason, "PROFILE_KILL_SWITCH_INVALID")
            self.assertEqual(
                self.fixture.local(DEPLOYER.TARGET_PATH).read_bytes(),
                synthetic_dormant_paper_payload())

    def test_round114_transition_preflight_rejects_authority_and_runtime(
            self) -> None:
        pins = self.prepare_round114_rebind_layout(transition=False)
        cases = ("control", "identity", "policy", "runtime", "residue")
        for case in cases:
            with self.subTest(case=case):
                if case == "control":
                    self.fixture.local_control_document[
                        "paper_authorized"] = True
                    expected = "PROFILE_TRANSITION_LOCAL_CONTROL_NOT_DENY_ALL"
                elif case == "identity":
                    self.fixture.local(
                        DEPLOYER.BROKER_PAPER_IDENTITIES_PATH).write_bytes(
                            b"{}\n")
                    expected = "PROFILE_TRANSITION_IDENTITY_MANIFEST_INVALID"
                elif case == "policy":
                    policy = json.loads(self.fixture.local(
                        DEPLOYER.PAPER_POLICY_PATH).read_bytes())
                    policy["enabled"] = True
                    self.fixture.local(DEPLOYER.PAPER_POLICY_PATH).write_bytes(
                        DEPLOYER.canonical_bytes(policy))
                    expected = "PROFILE_TRANSITION_CAMPAIGN_POLICY_INVALID"
                elif case == "runtime":
                    self.fixture.active_unit = DEPLOYER.PAPER_UNITS[0]
                    expected = "PROFILE_TRANSITION_RUNTIME_NOT_INACTIVE"
                else:
                    self.fixture.write_file(
                        DEPLOYER.START_PERMIT_PATHS[0], b"{}\n", 0o600)
                    expected = "PROFILE_TRANSITION_AUTHORITY_RESIDUE"
                with mock.patch.multiple(DEPLOYER, **pins), self.assertRaises(
                        DEPLOYER.DeployError) as raised:
                    self.transition_round114()
                self.assertEqual(raised.exception.reason, expected)
                if case == "control":
                    self.fixture.local_control_document[
                        "paper_authorized"] = False
                elif case == "identity":
                    self.fixture.local(
                        DEPLOYER.BROKER_PAPER_IDENTITIES_PATH).write_bytes(
                            DEPLOYER.DISABLED_PAPER_IDENTITIES_PAYLOAD)
                elif case == "policy":
                    self.install_disabled_campaign_policy()
                elif case == "runtime":
                    self.fixture.active_unit = None
                else:
                    self.fixture.local(DEPLOYER.START_PERMIT_PATHS[0]).unlink()

    def test_round114_transition_crash_seams_resume_only_forward(self) -> None:
        seams = (
            "after_transition_backup_ready",
            "after_transition_preimage_temp_fsync",
            "after_transition_preimage_publish_rename",
            "after_transition_target_exchange",
            "before_transition_retained_quarantine",
            "after_transition_retained_quarantine",
            "after_transition_receipt_temp_fsync",
            "after_transition_receipt_publish_rename",
        )
        for index, seam in enumerate(seams):
            if index:
                self.fixture.close()
                self.fixture = Fixture()
            pins = self.prepare_round114_rebind_layout(transition=False)
            fired = False

            def crash(name: str) -> None:
                nonlocal fired
                if name == seam and not fired:
                    fired = True
                    raise SystemExit(seam)

            with self.subTest(seam=seam), mock.patch.multiple(
                    DEPLOYER, **pins):
                with mock.patch.object(
                        DEPLOYER, "SEAM_HOOK", side_effect=crash), \
                        self.assertRaises(SystemExit):
                    self.transition_round114()
                self.assertTrue(fired)
                if seam in {
                    "after_transition_target_exchange",
                    "before_transition_retained_quarantine",
                    "after_transition_retained_quarantine",
                    "after_transition_receipt_temp_fsync",
                    "after_transition_receipt_publish_rename",
                }:
                    self.assertEqual(
                        self.fixture.local(DEPLOYER.TARGET_PATH).read_bytes(),
                        DEPLOYER.NEW_PAYLOAD)
                receipt = self.transition_round114()
                self.assertEqual(
                    receipt, DEPLOYER.digest_bytes(self.fixture.local(
                        DEPLOYER.ROUND114_TRANSITION_RECEIPT_PATH).read_bytes()))
                self.assertEqual(
                    self.fixture.local(DEPLOYER.TARGET_PATH).read_bytes(),
                    DEPLOYER.NEW_PAYLOAD)
                retained = self.fixture.local(
                    DEPLOYER.ROUND114_TRANSITION_TARGET_TEMP_PATH)
                self.assertEqual(stat.S_IMODE(retained.stat().st_mode), 0o600)

    def test_round114_transition_preimage_rebound_is_preserved_and_rejected(
            self) -> None:
        pins = self.prepare_round114_rebind_layout(transition=False)

        def crash(name: str) -> None:
            if name == "after_transition_preimage_ready":
                raise SystemExit(name)

        with mock.patch.multiple(DEPLOYER, **pins):
            with mock.patch.object(
                    DEPLOYER, "SEAM_HOOK", side_effect=crash), \
                    self.assertRaises(SystemExit):
                self.transition_round114()
            preimage = self.fixture.local(
                DEPLOYER.ROUND114_TRANSITION_PREIMAGE_PATH)
            preimage.write_bytes(b"{}\n")
            with self.assertRaises(DEPLOYER.DeployError) as raised:
                self.transition_round114()
        self.assertEqual(
            raised.exception.reason, "PROFILE_TRANSITION_PREIMAGE_INVALID")
        self.assertEqual(preimage.read_bytes(), b"{}\n")
        self.assertEqual(
            self.fixture.local(DEPLOYER.TARGET_PATH).read_bytes(),
            synthetic_dormant_paper_payload())

    def test_round114_rebind_is_read_only_and_atomically_publishes_receipt(
            self) -> None:
        pins = self.prepare_round114_rebind_layout()
        protected_paths = (
            DEPLOYER.TARGET_PATH,
            DEPLOYER.RECEIPT_PATH,
            DEPLOYER.ROUND95_RECEIPT_PATH,
            DEPLOYER.BACKUP_PATH,
            DEPLOYER.TARGET_TEMP_PATH,
            DEPLOYER.ROUND114_TRANSITION_BACKUP_PATH,
            DEPLOYER.ROUND114_TRANSITION_TARGET_TEMP_PATH,
            DEPLOYER.ROUND114_TRANSITION_PREIMAGE_PATH,
            DEPLOYER.ROUND114_TRANSITION_RECEIPT_PATH,
        )
        before = {
            path: (
                self.fixture.local(path).read_bytes(),
                DEPLOYER.stable_identity(self.fixture.local(path).stat()),
            )
            for path in protected_paths
        }
        real_renameat2 = DEPLOYER.renameat2
        rename_flags: list[int] = []

        def tracked_renameat2(
            source_parent: int,
            source_name: str,
            target_parent: int,
            target_name: str,
            flags: int,
            reason: str,
        ) -> None:
            rename_flags.append(flags)
            real_renameat2(
                source_parent, source_name, target_parent, target_name,
                flags, reason)

        with mock.patch.multiple(DEPLOYER, **pins), mock.patch.object(
                DEPLOYER, "renameat2", side_effect=tracked_renameat2):
            receipt_sha256 = self.deploy_round114()
            self.assertEqual(self.deploy_round114(), receipt_sha256)

        for path in protected_paths:
            local = self.fixture.local(path)
            self.assertEqual(local.read_bytes(), before[path][0])
            self.assertEqual(
                DEPLOYER.stable_identity(local.stat()), before[path][1])
        self.assertEqual(rename_flags, [DEPLOYER.RENAME_NOREPLACE])
        self.assertFalse(
            self.fixture.local(DEPLOYER.ROUND114_RECEIPT_TEMP_PATH).exists())
        receipt_path = self.fixture.local(DEPLOYER.ROUND114_RECEIPT_PATH)
        receipt_payload = receipt_path.read_bytes()
        self.assertEqual(receipt_sha256, DEPLOYER.digest_bytes(receipt_payload))
        receipt = json.loads(receipt_payload)
        self.assertEqual(receipt["schema"], DEPLOYER.ROUND114_RECEIPT_SCHEMA)
        self.assertEqual(receipt["version"], 8)
        self.assertEqual(receipt["round"], 114)
        self.assertEqual(receipt["status"], DEPLOYER.ROUND114_RECEIPT_STATUS)
        self.assertEqual(
            receipt["shadow_install_evidence"],
            self.fixture.shadow_install_evidence)
        self.assertEqual(
            receipt["target_before"], receipt["target_after"])
        self.assertEqual(receipt["target_after"], receipt["target_final"])
        self.assertEqual(
            receipt["legacy_receipt"]["sha256"],
            pins["LEGACY_RECEIPT_FILE_SHA256"])
        self.assertEqual(
            receipt["legacy_receipt"]["body_sha256"],
            pins["LEGACY_RECEIPT_BODY_SHA256"])
        self.assertEqual(
            receipt["predecessor_profile_receipt"]["sha256"],
            pins["ROUND95_RECEIPT_FILE_SHA256"])
        self.assertEqual(
            receipt["predecessor_profile_receipt"]["body_sha256"],
            pins["ROUND95_RECEIPT_BODY_SHA256"])
        self.assertEqual(
            receipt["preflight_before"], receipt["preflight_after"])
        self.assertEqual(
            receipt["preflight_after"], receipt["preflight_final"])
        for field in (
            "profile_content_changed", "target_written", "target_replaced",
            "services_started", "services_stopped", "services_restarted",
            "campaign_launched", "paper_authorized", "live_authorized",
            "mutation_attempted", "direct_broker_access",
        ):
            self.assertFalse(receipt[field], field)

    def test_round114_rebind_rejects_legacy_artifact_drift_without_receipt(
            self) -> None:
        pins = self.prepare_round114_rebind_layout()
        target = self.fixture.local(DEPLOYER.TARGET_PATH)
        target_before = (
            target.read_bytes(), DEPLOYER.stable_identity(target.stat()))
        fired = False

        def drift(name: str) -> None:
            nonlocal fired
            if name == "after_round114_preflight_before" and not fired:
                fired = True
                self.fixture.local(DEPLOYER.BACKUP_PATH).write_bytes(
                    b"x" * len(DEPLOYER.OLD_PAYLOAD))

        with mock.patch.multiple(DEPLOYER, **pins), mock.patch.object(
                DEPLOYER, "SEAM_HOOK", side_effect=drift), self.assertRaises(
                    DEPLOYER.DeployError) as raised:
            self.deploy_round114()
        self.assertTrue(fired)
        self.assertEqual(
            raised.exception.reason, "PROFILE_LEGACY_BACKUP_REBOUND")
        self.assertFalse(
            self.fixture.local(DEPLOYER.ROUND114_RECEIPT_PATH).exists())
        self.assertFalse(
            self.fixture.local(DEPLOYER.ROUND114_RECEIPT_TEMP_PATH).exists())
        self.assertEqual(target.read_bytes(), target_before[0])
        self.assertEqual(
            DEPLOYER.stable_identity(target.stat()), target_before[1])

    def test_round114_rebind_requires_current_generation_evidence(
            self) -> None:
        pins = self.prepare_round114_rebind_layout()
        self.fixture.shadow_install_evidence["install_generation"] = 3
        with mock.patch.multiple(DEPLOYER, **pins), self.assertRaises(
                DEPLOYER.DeployError) as raised:
            self.deploy_round114()
        self.assertEqual(
            raised.exception.reason,
            "PROFILE_SHADOW_INSTALL_EVIDENCE_INVALID")
        self.assertFalse(
            self.fixture.local(DEPLOYER.ROUND114_RECEIPT_PATH).exists())

    def test_round114_rebind_rechecks_target_after_receipt_prepare(
            self) -> None:
        pins = self.prepare_round114_rebind_layout()
        fired = False

        def drift(name: str) -> None:
            nonlocal fired
            if name == "after_round114_receipt_temp_fsync" and not fired:
                fired = True
                self.fixture.local(DEPLOYER.TARGET_PATH).write_bytes(
                    b"x" * len(DEPLOYER.NEW_PAYLOAD))

        with mock.patch.multiple(DEPLOYER, **pins), mock.patch.object(
                DEPLOYER, "SEAM_HOOK", side_effect=drift), self.assertRaises(
                    DEPLOYER.DeployError) as raised:
            self.deploy_round114()
        self.assertTrue(fired)
        self.assertEqual(
            raised.exception.reason, "PROFILE_REBIND_TARGET_REBOUND")
        self.assertFalse(
            self.fixture.local(DEPLOYER.ROUND114_RECEIPT_PATH).exists())
        self.assertTrue(
            self.fixture.local(DEPLOYER.ROUND114_RECEIPT_TEMP_PATH).exists())

    def test_round114_temp_fsync_crash_recovers_exact_receipt(self) -> None:
        pins = self.prepare_round114_rebind_layout()
        fired = False

        def crash(name: str) -> None:
            nonlocal fired
            if name == "after_round114_receipt_temp_fsync" and not fired:
                fired = True
                raise DEPLOYER.DeployError("PROFILE_TEST_CRASH")

        with mock.patch.multiple(DEPLOYER, **pins):
            with mock.patch.object(
                    DEPLOYER, "SEAM_HOOK", side_effect=crash), \
                    self.assertRaises(DEPLOYER.DeployError) as raised:
                self.deploy_round114()
            self.assertEqual(raised.exception.reason, "PROFILE_TEST_CRASH")
            self.assertTrue(fired)
            temporary = self.fixture.local(
                DEPLOYER.ROUND114_RECEIPT_TEMP_PATH)
            self.assertTrue(temporary.exists())
            self.assertFalse(self.fixture.local(
                DEPLOYER.ROUND114_RECEIPT_PATH).exists())
            receipt_sha256 = self.deploy_round114()
        final = self.fixture.local(DEPLOYER.ROUND114_RECEIPT_PATH)
        self.assertEqual(
            receipt_sha256, DEPLOYER.digest_bytes(final.read_bytes()))
        self.assertFalse(temporary.exists())

    def test_round114_tampered_temp_is_preserved_and_rejected(self) -> None:
        pins = self.prepare_round114_rebind_layout()

        def crash(name: str) -> None:
            if name == "after_round114_receipt_temp_fsync":
                raise DEPLOYER.DeployError("PROFILE_TEST_CRASH")

        with mock.patch.multiple(DEPLOYER, **pins):
            with mock.patch.object(
                    DEPLOYER, "SEAM_HOOK", side_effect=crash), \
                    self.assertRaises(DEPLOYER.DeployError):
                self.deploy_round114()
            temporary = self.fixture.local(
                DEPLOYER.ROUND114_RECEIPT_TEMP_PATH)
            temporary.write_bytes(b"{}\n")
            with self.assertRaises(DEPLOYER.DeployError) as raised:
                self.deploy_round114()
        self.assertEqual(
            raised.exception.reason,
            "PROFILE_ROUND114_RECEIPT_TEMP_INVALID")
        self.assertEqual(temporary.read_bytes(), b"{}\n")
        self.assertFalse(self.fixture.local(
            DEPLOYER.ROUND114_RECEIPT_PATH).exists())

    def test_round114_post_rename_crash_retry_fsyncs_file_and_parent(
            self) -> None:
        pins = self.prepare_round114_rebind_layout()
        fired = False

        def crash(name: str) -> None:
            nonlocal fired
            if name == "after_round114_receipt_publish_rename" and not fired:
                fired = True
                raise DEPLOYER.DeployError("PROFILE_TEST_CRASH")

        with mock.patch.multiple(DEPLOYER, **pins):
            with mock.patch.object(
                    DEPLOYER, "SEAM_HOOK", side_effect=crash), \
                    self.assertRaises(DEPLOYER.DeployError):
                self.deploy_round114()
            self.assertTrue(fired)
            self.assertTrue(self.fixture.local(
                DEPLOYER.ROUND114_RECEIPT_PATH).exists())
            self.assertFalse(self.fixture.local(
                DEPLOYER.ROUND114_RECEIPT_TEMP_PATH).exists())
            fsync_seams: list[str] = []

            def count_fsync_seams(name: str) -> None:
                if name in {
                        "before_round114_existing_receipt_file_fsync",
                        "before_round114_existing_receipt_parent_fsync"}:
                    fsync_seams.append(name)

            with mock.patch.object(
                    DEPLOYER, "SEAM_HOOK", side_effect=count_fsync_seams):
                receipt_sha256 = self.deploy_round114()
        self.assertEqual(
            fsync_seams.count(
                "before_round114_existing_receipt_file_fsync"), 1)
        self.assertEqual(
            fsync_seams.count(
                "before_round114_existing_receipt_parent_fsync"), 1)
        self.assertEqual(
            receipt_sha256,
            DEPLOYER.digest_bytes(self.fixture.local(
                DEPLOYER.ROUND114_RECEIPT_PATH).read_bytes()))

    def test_round114_lineage_rejects_pointer_and_lock_identity_drift(
            self) -> None:
        pins = self.prepare_round114_rebind_layout()
        with mock.patch.multiple(DEPLOYER, **pins):
            predecessor_field = (
                "predecessor_current_install_pointer_file_sha256")
            original_predecessor = self.fixture.shadow_install_evidence[
                predecessor_field]
            self.fixture.shadow_install_evidence[predecessor_field] = (
                "sha256:" + "9" * 64)
            with self.assertRaises(DEPLOYER.DeployError) as pointer_error:
                self.deploy_round114()
            self.assertEqual(
                pointer_error.exception.reason,
                "PROFILE_SHADOW_INSTALL_LINEAGE_INVALID")
            self.fixture.shadow_install_evidence[
                predecessor_field] = original_predecessor
            lock = self.fixture.shadow_install_evidence["transaction_lock"]
            assert isinstance(lock, dict)
            for field in ("device", "inode"):
                original = lock[field]
                lock[field] = original + 1
                with self.subTest(field=field), self.assertRaises(
                        DEPLOYER.DeployError) as lock_error:
                    self.deploy_round114()
                self.assertEqual(
                    lock_error.exception.reason,
                    "PROFILE_SHADOW_INSTALL_LINEAGE_INVALID")
                lock[field] = original
        self.assertFalse(self.fixture.local(
            DEPLOYER.ROUND114_RECEIPT_PATH).exists())

    def test_round114_requires_exact_secure_round95_direct_predecessor(
            self) -> None:
        pins = self.prepare_round114_rebind_layout()
        predecessor = self.fixture.local(DEPLOYER.ROUND95_RECEIPT_PATH)
        predecessor.write_bytes(b"{}\n")
        with mock.patch.multiple(DEPLOYER, **pins), self.assertRaises(
                DEPLOYER.DeployError) as raised:
            self.deploy_round114()
        self.assertEqual(raised.exception.reason, "PROFILE_REBIND_REQUIRED")
        self.assertFalse(
            self.fixture.local(DEPLOYER.ROUND114_RECEIPT_PATH).exists())
        self.assertFalse(
            self.fixture.local(DEPLOYER.ROUND114_RECEIPT_TEMP_PATH).exists())

    def test_round114_rejects_round95_predecessor_rebound_mid_transaction(
            self) -> None:
        pins = self.prepare_round114_rebind_layout()
        predecessor = self.fixture.local(DEPLOYER.ROUND95_RECEIPT_PATH)
        fired = False

        def rebound(name: str) -> None:
            nonlocal fired
            if name != "after_round114_preflight_before" or fired:
                return
            fired = True
            preserved = predecessor.with_name("preserved-round95.json")
            predecessor.rename(preserved)
            replacement = predecessor.with_name("replacement-round95.json")
            replacement.write_bytes(preserved.read_bytes())
            os.chmod(replacement, 0o600)
            replacement.rename(predecessor)

        with mock.patch.multiple(DEPLOYER, **pins), mock.patch.object(
                DEPLOYER, "SEAM_HOOK", side_effect=rebound), self.assertRaises(
                    DEPLOYER.DeployError) as raised:
            self.deploy_round114()
        self.assertTrue(fired)
        self.assertEqual(
            raised.exception.reason,
            "PROFILE_ROUND95_PREDECESSOR_RECEIPT_REBOUND")
        self.assertFalse(
            self.fixture.local(DEPLOYER.ROUND114_RECEIPT_PATH).exists())

    def test_round114_receipt_rejects_forged_predecessor_evidence(
            self) -> None:
        pins = self.prepare_round114_rebind_layout()
        with mock.patch.multiple(DEPLOYER, **pins):
            self.deploy_round114()
            receipt_path = self.fixture.local(DEPLOYER.ROUND114_RECEIPT_PATH)
            original_document = json.loads(receipt_path.read_bytes())
        for field, value in (
                ("path", "/var/lib/heptatrader/attacker.json"),
                ("sha256", "sha256:" + "9" * 64),
                ("body_sha256", "sha256:" + "8" * 64),
                ("bytes", 1),
                ("mode", stat.S_IFREG | 0o640),
                ("nlink", 2)):
            with mock.patch.multiple(DEPLOYER, **pins):
                document = copy.deepcopy(original_document)
                document["predecessor_profile_receipt"][field] = value
                body = dict(document)
                body.pop("body_sha256")
                document["body_sha256"] = DEPLOYER.digest_bytes(
                    DEPLOYER.canonical_bytes(body))
                payload = DEPLOYER.canonical_bytes(document)
                snapshot = DEPLOYER.FileSnapshot(
                    payload, receipt_path.stat())
                with self.subTest(field=field), self.assertRaises(
                        DEPLOYER.DeployError) as raised:
                    DEPLOYER.validate_round114_receipt(
                        snapshot, self.fixture.shadow_install_evidence)
                self.assertEqual(
                    raised.exception.reason,
                    "PROFILE_ROUND114_RECEIPT_INVALID")

    def test_frozen_broker_closure_matches_current_sources(self) -> None:
        sources = {
            "broker_egress_service": (
                ROOT / "systemd/hepta-broker-egress-policy.service", 0o644),
            "broker_egress_helper": (
                ROOT / "scripts/hepta_broker_egress_policy.py", 0o755),
        }
        for label, (path, mode) in sources.items():
            payload = path.read_bytes()
            specification = FROZEN_GATEWAY_UNIT_CLOSURE[label]
            with self.subTest(label=label):
                self.assertEqual(specification["bytes"], len(payload))
                self.assertEqual(
                    specification["sha256"],
                    hashlib.sha256(payload).hexdigest(),
                )
                self.assertEqual(specification["mode"], mode)

    def test_installed_helper_has_no_namespace_deletion_surface(self) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertIsNone(re.search(
            r"\b(?:unlink|unlinkat|remove|rmdir|rmtree)\b", source))
        self.assertEqual(
            re.findall(r'getattr\(LIBC,\s*"([^"]+)"', source),
            ["fstatfs", "renameat2"])

    def test_happy_path_is_canonical_and_never_controls_services(self) -> None:
        receipt_digest = DEPLOYER.deploy()

        target = self.fixture.local(DEPLOYER.TARGET_PATH)
        retained_target = self.fixture.local(DEPLOYER.TARGET_TEMP_PATH)
        backup = self.fixture.local(DEPLOYER.BACKUP_PATH)
        receipt_path = self.fixture.local(DEPLOYER.RECEIPT_PATH)
        self.assertEqual(target.read_bytes(), DEPLOYER.NEW_PAYLOAD)
        self.assertEqual(retained_target.read_bytes(), DEPLOYER.OLD_PAYLOAD)
        self.assertEqual(backup.read_bytes(), DEPLOYER.OLD_PAYLOAD)
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o644)
        self.assertEqual(stat.S_IMODE(backup.stat().st_mode), 0o600)
        self.assertEqual(target.stat().st_nlink, 1)
        self.assertEqual(backup.stat().st_nlink, 1)

        receipt_payload = receipt_path.read_bytes()
        self.assertEqual(
            receipt_digest,
            "sha256:" + hashlib.sha256(receipt_payload).hexdigest())
        receipt = json.loads(receipt_payload)
        self.assertEqual(
            receipt["schema"],
            "hepta.p1-watch-profile-deployment-receipt.v6")
        self.assertEqual(receipt["version"], 6)
        self.assertEqual(
            receipt["shadow_install_evidence"],
            self.fixture.shadow_install_evidence)
        self.assertEqual(receipt["round"], 86)
        self.assertEqual(
            receipt["status"], "OFFLINE_PASSIVE_WATCH_PROFILE_DEPLOYED")
        self.assertEqual(
            receipt["retained_target_path"], str(DEPLOYER.TARGET_TEMP_PATH))
        self.assertEqual(
            receipt["receipt_staging_path"], str(DEPLOYER.RECEIPT_TEMP_PATH))
        self.assertEqual(
            receipt["retained_target_inode"], retained_target.stat().st_ino)
        self.assertFalse(receipt["services_started"])
        self.assertFalse(receipt["services_stopped"])
        self.assertFalse(receipt["services_restarted"])
        self.assertFalse(receipt["campaign_launched"])
        self.assertFalse(receipt["activation_receipt_eligible"])
        self.assertFalse(receipt["preflight_reusable_for_activation"])
        self.assertFalse(receipt["broker_loaded_source_attested"])
        self.assertFalse(receipt["broker_deny_all_continuity_attested"])
        self.assertTrue(receipt["fresh_activation_transaction_required"])
        for field in (
            "paper_authorized", "live_authorized", "mutation_attempted",
            "direct_broker_access",
        ):
            self.assertFalse(receipt[field])
        expected_gateway_masks = {
            unit: {
                "persistent": {
                    "path": str(DEPLOYER.PERSISTENT_MASK_ROOT / unit),
                    "target": DEPLOYER.MASK_TARGET,
                },
                "runtime": {
                    "path": str(DEPLOYER.RUNTIME_MASK_ROOT / unit),
                    "target": DEPLOYER.MASK_TARGET,
                },
            }
            for unit in DEPLOYER.GATEWAY_BOUNDARY_UNITS
        }
        self.assertEqual(
            receipt["preflight_before"]["gateway_masks"],
            expected_gateway_masks)
        self.assertEqual(
            receipt["preflight_after"]["gateway_masks"],
            expected_gateway_masks)
        self.assertEqual(
            set(receipt["preflight_before"]["gateway_unit_closure"]["files"]),
            set(DEPLOYER.GATEWAY_UNIT_CLOSURE))
        self.assertEqual(
            receipt["preflight_before"]["gateway_unit_closure"],
            receipt["preflight_after"]["gateway_unit_closure"])
        self.assertEqual(
            receipt["preflight_before"]["systemd_manager"],
            {
                "Version": DEPLOYER.EXPECTED_SYSTEMD_VERSION,
                "Features": DEPLOYER.EXPECTED_SYSTEMD_FEATURES,
                "UnitPath": DEPLOYER.EXPECTED_SYSTEMD_UNIT_PATH,
                "Environment":
                    DEPLOYER.EXPECTED_SYSTEMD_MANAGER_ENVIRONMENT,
            })
        broker_before = receipt["preflight_before"]["broker_egress_unit"]
        broker_after = receipt["preflight_after"]["broker_egress_unit"]
        self.assertEqual(broker_before, broker_after)
        self.assertEqual(broker_before, {
            **self.fixture.broker_offline_fields,
            "MainPID": 0,
            "ExecMainPID": 1108253,
            "ControlPID": 0,
        })
        broker_check_before = receipt["preflight_before"][
            "broker_egress_check"]
        broker_check_after = receipt["preflight_after"][
            "broker_egress_check"]
        self.assertEqual(broker_check_before, broker_check_after)
        self.assertEqual(broker_check_before["argv"], ["--check-deny-all"])
        self.assertEqual(broker_check_before["authorized_connectors"], 0)
        self.assertEqual(broker_check_before["authorized_uids"], [])
        self.assertEqual(
            set(receipt["preflight_before"]["manager_unit_contracts"]),
            set(DEPLOYER.GATEWAY_BOUNDARY_UNITS))
        watch = receipt["preflight_before"]["watch_boundary"]
        self.assertEqual(
            set(watch["units"]), set(DEPLOYER.WATCH_BOUNDARY_UNITS))
        self.assertEqual(
            watch["sessions"]["entries"],
            [DEPLOYER.SESSION_BOOTSTRAP_LOCK])
        self.assertTrue(
            watch["sessions"]["bootstrap_lock"]["idle_lock_observed"])
        self.assertEqual(watch["private"]["entries"], [])
        self.assertFalse(watch["export"]["present"])
        self.assertFalse(watch["custodian_transaction"]["present"])
        expected_body_sha256 = receipt.pop("body_sha256")
        self.assertEqual(
            expected_body_sha256,
            DEPLOYER.digest_bytes(DEPLOYER.canonical_bytes(receipt)))
        restored = dict(receipt)
        restored["body_sha256"] = expected_body_sha256
        self.assertEqual(receipt_payload, DEPLOYER.canonical_bytes(restored))
        self.assertEqual(stat.S_IMODE(receipt_path.stat().st_mode), 0o600)
        self.assertEqual(receipt_path.stat().st_nlink, 1)

        for arguments in self.fixture.commands:
            if arguments[0] == DEPLOYER.SYSTEMCTL:
                self.assertEqual(arguments[1], "show")
            elif arguments[0] == DEPLOYER.BUSCTL:
                self.assertEqual(arguments[1:3], ["--system", "--json=short"])
            else:
                self.assertRegex(arguments[0], r"^/proc/self/fd/[0-9]+$")
                self.assertEqual(arguments[1:4], ["-I", "-S", "-B"])
                self.assertRegex(arguments[4], r"^/proc/self/fd/[0-9]+$")
                self.assertEqual(arguments[5:], ["--check-deny-all"])

    def test_receipt_rejects_boolean_campaign_policy_count(self) -> None:
        DEPLOYER.deploy()
        receipt_path = self.fixture.local(DEPLOYER.RECEIPT_PATH)
        document = json.loads(receipt_path.read_bytes())
        document["preflight_before"]["campaign_policy_count"] = False
        document["preflight_after"]["campaign_policy_count"] = False
        body = dict(document)
        body.pop("body_sha256")
        document["body_sha256"] = DEPLOYER.digest_bytes(
            DEPLOYER.canonical_bytes(body))
        receipt_path.write_bytes(DEPLOYER.canonical_bytes(document))
        os.chmod(receipt_path, 0o600)
        self.assert_reason("PROFILE_RECEIPT_INVALID")

    def test_receipt_rejects_missing_or_forged_gateway_masks(self) -> None:
        for mutation in ("missing", "forged"):
            fixture = Fixture()
            try:
                DEPLOYER.deploy()
                receipt_path = fixture.local(DEPLOYER.RECEIPT_PATH)
                document = json.loads(receipt_path.read_bytes())
                for key in ("preflight_before", "preflight_after"):
                    if mutation == "missing":
                        document[key].pop("gateway_masks")
                    else:
                        unit = DEPLOYER.GATEWAY_BOUNDARY_UNITS[0]
                        document[key]["gateway_masks"][unit]["runtime"][
                            "target"] = "/dev/zero"
                body = dict(document)
                body.pop("body_sha256")
                document["body_sha256"] = DEPLOYER.digest_bytes(
                    DEPLOYER.canonical_bytes(body))
                receipt_path.write_bytes(DEPLOYER.canonical_bytes(document))
                os.chmod(receipt_path, 0o600)
                with self.subTest(mutation=mutation), self.assertRaises(
                        DEPLOYER.DeployError) as raised:
                    DEPLOYER.deploy()
                self.assertEqual(
                    raised.exception.reason, "PROFILE_RECEIPT_INVALID")
            finally:
                fixture.close()

    def test_receipt_rejects_missing_or_forged_gateway_closure(self) -> None:
        for mutation in ("missing", "forged", "helper-mode"):
            fixture = Fixture()
            try:
                DEPLOYER.deploy()
                receipt_path = fixture.local(DEPLOYER.RECEIPT_PATH)
                document = json.loads(receipt_path.read_bytes())
                for key in ("preflight_before", "preflight_after"):
                    closure = document[key]["gateway_unit_closure"]
                    if mutation == "missing":
                        closure.pop("dropin_inventory")
                    elif mutation == "forged":
                        closure["files"]["gateway_service_broker_dropin"][
                            "sha256"] = "sha256:" + "0" * 64
                    else:
                        closure["files"]["broker_egress_helper"]["mode"] = (
                            stat.S_IFREG | 0o644)
                body = dict(document)
                body.pop("body_sha256")
                document["body_sha256"] = DEPLOYER.digest_bytes(
                    DEPLOYER.canonical_bytes(body))
                receipt_path.write_bytes(DEPLOYER.canonical_bytes(document))
                os.chmod(receipt_path, 0o600)
                with self.subTest(mutation=mutation), self.assertRaises(
                        DEPLOYER.DeployError) as raised:
                    DEPLOYER.deploy()
                self.assertEqual(
                    raised.exception.reason, "PROFILE_RECEIPT_INVALID")
            finally:
                fixture.close()

    def test_receipt_rejects_forged_manager_and_inventory_evidence(self) -> None:
        for mutation in ("manager", "entries", "metadata", "aliases"):
            fixture = Fixture()
            try:
                DEPLOYER.deploy()
                receipt_path = fixture.local(DEPLOYER.RECEIPT_PATH)
                document = json.loads(receipt_path.read_bytes())
                for key in ("preflight_before", "preflight_after"):
                    preflight = document[key]
                    inventory = preflight["gateway_unit_closure"][
                        "dropin_inventory"]
                    if mutation == "manager":
                        preflight["systemd_manager"]["Version"] = "257"
                    elif mutation == "entries":
                        inventory["search_roots"][
                            str(DEPLOYER.PERSISTENT_MASK_ROOT)][
                                "matching_unit_entries"].append(
                                    "gateway-alias.service")
                    elif mutation == "metadata":
                        inventory["expected_directory"]["mode"] = (
                            stat.S_IFDIR | 0o777)
                    else:
                        inventory["relevant_unit_aliases"] = [
                            "gateway-alias.service"]
                body = dict(document)
                body.pop("body_sha256")
                document["body_sha256"] = DEPLOYER.digest_bytes(
                    DEPLOYER.canonical_bytes(body))
                receipt_path.write_bytes(DEPLOYER.canonical_bytes(document))
                os.chmod(receipt_path, 0o600)
                with self.subTest(mutation=mutation), self.assertRaises(
                        DEPLOYER.DeployError) as raised:
                    DEPLOYER.deploy()
                self.assertEqual(
                    raised.exception.reason, "PROFILE_RECEIPT_INVALID")
            finally:
                fixture.close()

    def test_receipt_rejects_missing_or_forged_broker_unit_evidence(
            self) -> None:
        for mutation in (
            "missing", "fragment", "active", "pid", "job",
        ):
            fixture = Fixture()
            try:
                DEPLOYER.deploy()
                receipt_path = fixture.local(DEPLOYER.RECEIPT_PATH)
                document = json.loads(receipt_path.read_bytes())
                for key in ("preflight_before", "preflight_after"):
                    preflight = document[key]
                    if mutation == "missing":
                        preflight.pop("broker_egress_unit")
                        continue
                    broker = preflight["broker_egress_unit"]
                    if mutation == "fragment":
                        broker["FragmentPath"] = (
                            "/etc/systemd/system/"
                            "hepta-broker-egress-policy.service")
                    elif mutation == "active":
                        broker["ActiveState"] = "active"
                    elif mutation == "pid":
                        broker["MainPID"] = 1
                    else:
                        broker["Job"] = "123 start"
                body = dict(document)
                body.pop("body_sha256")
                document["body_sha256"] = DEPLOYER.digest_bytes(
                    DEPLOYER.canonical_bytes(body))
                receipt_path.write_bytes(DEPLOYER.canonical_bytes(document))
                os.chmod(receipt_path, 0o600)
                with self.subTest(mutation=mutation), self.assertRaises(
                        DEPLOYER.DeployError) as raised:
                    DEPLOYER.deploy()
                self.assertEqual(
                    raised.exception.reason, "PROFILE_RECEIPT_INVALID")
            finally:
                fixture.close()

    def test_receipt_rejects_forged_offline_deny_all_evidence(self) -> None:
        for mutation in (
            "missing", "argv", "helper", "connectors", "uids",
        ):
            fixture = Fixture()
            try:
                DEPLOYER.deploy()
                receipt_path = fixture.local(DEPLOYER.RECEIPT_PATH)
                document = json.loads(receipt_path.read_bytes())
                for key in ("preflight_before", "preflight_after"):
                    preflight = document[key]
                    if mutation == "missing":
                        preflight.pop("broker_egress_check")
                        continue
                    evidence = preflight["broker_egress_check"]
                    if mutation == "argv":
                        evidence["argv"] = ["--check-active"]
                    elif mutation == "helper":
                        evidence["helper_sha256"] = "sha256:" + "0" * 64
                    elif mutation == "connectors":
                        evidence["authorized_connectors"] = 1
                    else:
                        evidence["authorized_uids"] = [2121]
                body = dict(document)
                body.pop("body_sha256")
                document["body_sha256"] = DEPLOYER.digest_bytes(
                    DEPLOYER.canonical_bytes(body))
                receipt_path.write_bytes(DEPLOYER.canonical_bytes(document))
                os.chmod(receipt_path, 0o600)
                with self.subTest(mutation=mutation), self.assertRaises(
                        DEPLOYER.DeployError) as raised:
                    DEPLOYER.deploy()
                self.assertEqual(
                    raised.exception.reason, "PROFILE_RECEIPT_INVALID")
            finally:
                fixture.close()

    def test_receipt_can_never_be_promoted_to_activation_evidence(self) -> None:
        for field, value in {
            "activation_receipt_eligible": True,
            "preflight_reusable_for_activation": True,
            "broker_loaded_source_attested": True,
            "broker_deny_all_continuity_attested": True,
            "fresh_activation_transaction_required": False,
        }.items():
            fixture = Fixture()
            try:
                DEPLOYER.deploy()
                receipt_path = fixture.local(DEPLOYER.RECEIPT_PATH)
                document = json.loads(receipt_path.read_bytes())
                document[field] = value
                body = dict(document)
                body.pop("body_sha256")
                document["body_sha256"] = DEPLOYER.digest_bytes(
                    DEPLOYER.canonical_bytes(body))
                receipt_path.write_bytes(DEPLOYER.canonical_bytes(document))
                os.chmod(receipt_path, 0o600)
                with self.subTest(field=field), self.assertRaises(
                        DEPLOYER.DeployError) as raised:
                    DEPLOYER.deploy()
                self.assertEqual(
                    raised.exception.reason, "PROFILE_RECEIPT_INVALID")
            finally:
                fixture.close()

    def test_success_recovery_ignores_unrelated_unit_root_metadata(self) -> None:
        receipt = DEPLOYER.deploy()
        self.fixture.write_file(
            Path("/etc/systemd/system/unrelated-safe.service"),
            b"[Unit]\n", 0o644)
        self.assertEqual(DEPLOYER.deploy(), receipt)
        self.assertEqual(receipt, self.assert_success_state(self.fixture))

    def test_commit_intent_recovery_ignores_unrelated_root_metadata(
            self) -> None:
        self.hard_crash_at("after_receipt_temp_fsync")
        self.fixture.write_file(
            Path("/etc/systemd/system/unrelated-safe.service"),
            b"[Unit]\n", 0o644)
        receipt = DEPLOYER.deploy()
        self.assertEqual(receipt, self.assert_success_state(self.fixture))

    def test_success_recovery_rejects_relevant_unit_entry(self) -> None:
        receipt = DEPLOYER.deploy()
        self.fixture.make_directory(Path(
            "/etc/systemd/system/hepta-tool-@alpha.service.wants"))
        self.assert_reason("PROFILE_GATEWAY_UNIT_CLOSURE_INVALID")
        self.assertEqual(receipt, self.assert_success_state(self.fixture))

    def test_success_recovery_accepts_exact_closure_inode_replacement(
            self) -> None:
        receipt = DEPLOYER.deploy()
        path = self.fixture.local(
            DEPLOYER.GATEWAY_UNIT_CLOSURE[
                "gateway_service_template"]["path"])
        original_inode = path.stat().st_ino
        replacement = path.with_name(path.name + ".replacement")
        replacement.write_bytes(path.read_bytes())
        os.chmod(replacement, 0o644)
        os.replace(replacement, path)
        self.assertNotEqual(path.stat().st_ino, original_inode)
        self.assertEqual(DEPLOYER.deploy(), receipt)
        self.assertEqual(receipt, self.assert_success_state(self.fixture))

    def test_success_recovery_accepts_exact_broker_helper_replacement(
            self) -> None:
        receipt = DEPLOYER.deploy()
        path = self.fixture.local(DEPLOYER.BROKER_EGRESS_POLICY_PATH)
        original_inode = path.stat().st_ino
        replacement = path.with_name(path.name + ".replacement")
        replacement.write_bytes(path.read_bytes())
        os.chmod(replacement, 0o755)
        os.replace(replacement, path)
        self.assertNotEqual(path.stat().st_ino, original_inode)
        self.assertEqual(DEPLOYER.deploy(), receipt)
        self.assertEqual(receipt, self.assert_success_state(self.fixture))

    def test_success_recovery_rejects_closure_hash_drift(self) -> None:
        receipt = DEPLOYER.deploy()
        path = self.fixture.local(
            DEPLOYER.GATEWAY_UNIT_CLOSURE[
                "gateway_service_template"]["path"])
        path.write_bytes(b"[Unit]\nDescription=drift\n")
        self.assert_reason("PROFILE_GATEWAY_UNIT_CLOSURE_INVALID")
        self.assertEqual(receipt, self.assert_success_state(self.fixture))

    def test_success_recovery_rejects_broker_helper_hash_drift(self) -> None:
        receipt = DEPLOYER.deploy()
        path = self.fixture.local(DEPLOYER.BROKER_EGRESS_POLICY_PATH)
        path.write_bytes(b"#!/bin/sh\nexit 0\n")
        os.chmod(path, 0o755)
        self.assert_reason("PROFILE_GATEWAY_UNIT_CLOSURE_INVALID")
        self.assertEqual(receipt, self.assert_success_state(self.fixture))

    def test_success_recovery_rejects_broker_becoming_active(self) -> None:
        receipt = DEPLOYER.deploy()
        self.fixture.broker_offline_fields.update({
            "ActiveState": "active", "SubState": "running",
            "MainPID": "1109001", "ExecMainPID": "1109001",
        })
        self.assert_reason("PROFILE_BROKER_EGRESS_UNIT_NOT_OFFLINE")
        self.assertEqual(receipt, self.assert_success_state(self.fixture))

    def test_commit_intent_recovery_rejects_broker_becoming_active(
            self) -> None:
        self.hard_crash_at("after_receipt_temp_fsync")
        original = dict(self.fixture.broker_offline_fields)
        self.fixture.broker_offline_fields.update({
            "ActiveState": "active", "SubState": "running",
            "MainPID": "1109002", "ExecMainPID": "1109002",
        })
        self.assert_reason("PROFILE_BROKER_EGRESS_UNIT_NOT_OFFLINE")
        self.assertFalse(self.fixture.local(DEPLOYER.RECEIPT_PATH).exists())
        self.fixture.broker_offline_fields = original
        receipt = DEPLOYER.deploy()
        self.assertEqual(receipt, self.assert_success_state(self.fixture))

    def test_success_recovery_rejects_offline_broker_pid(self) -> None:
        receipt = DEPLOYER.deploy()
        self.fixture.broker_offline_fields["MainPID"] = "1109004"
        self.assert_reason("PROFILE_BROKER_EGRESS_UNIT_NOT_OFFLINE")
        self.assertEqual(receipt, self.assert_success_state(self.fixture))

    def test_broker_start_during_transaction_is_rejected(
            self) -> None:
        observations = 0

        def restart_after_observation(name: str) -> None:
            nonlocal observations
            if name != "after_gateway_manager_before_masks":
                return
            observations += 1
            if observations == 1:
                self.fixture.broker_offline_fields.update({
                    "ActiveState": "active", "SubState": "running",
                    "MainPID": "1109003", "ExecMainPID": "1109003",
                })

        with mock.patch.object(
                DEPLOYER, "SEAM_HOOK", restart_after_observation):
            self.assert_reason("PROFILE_BROKER_EGRESS_UNIT_NOT_OFFLINE")
        self.assertEqual(observations, 1)
        self.assertFalse(self.fixture.local(DEPLOYER.BACKUP_PATH).exists())

    def test_root_is_mandatory(self) -> None:
        with mock.patch.object(DEPLOYER.os, "geteuid", return_value=1000):
            self.assert_reason("PROFILE_ROOT_REQUIRED")

    def test_wrong_old_payload_is_rejected_without_backup(self) -> None:
        self.fixture.local(DEPLOYER.TARGET_PATH).write_bytes(b"wrong\n")
        self.assert_reason("PROFILE_TRANSACTION_STATE_INVALID")
        self.assertFalse(self.fixture.local(DEPLOYER.BACKUP_PATH).exists())

    def test_target_mode_and_hard_link_are_rejected(self) -> None:
        target = self.fixture.local(DEPLOYER.TARGET_PATH)
        os.chmod(target, 0o640)
        self.assert_reason("PROFILE_TRANSACTION_STATE_INVALID")

    def test_target_hard_link_is_rejected(self) -> None:
        target = self.fixture.local(DEPLOYER.TARGET_PATH)
        os.link(target, target.with_name("alpha.env.alias"))
        self.assert_reason("PROFILE_TRANSACTION_STATE_INVALID")

    def test_target_symlink_is_rejected(self) -> None:
        target = self.fixture.local(DEPLOYER.TARGET_PATH)
        other = target.with_name("other.env")
        other.write_bytes(DEPLOYER.OLD_PAYLOAD)
        os.chmod(other, 0o644)
        target.unlink()
        target.symlink_to(other.name)
        self.assert_reason("PROFILE_TRANSACTION_STATE_INVALID")

    def test_target_fifo_is_rejected_without_blocking(self) -> None:
        target = self.fixture.local(DEPLOYER.TARGET_PATH)
        target.unlink()
        os.mkfifo(target, 0o644)
        self.assert_reason("PROFILE_TRANSACTION_STATE_INVALID")

    def test_symlinked_parent_is_rejected(self) -> None:
        parent = self.fixture.local(DEPLOYER.TARGET_PATH.parent)
        moved = parent.with_name("trust-domains-real")
        parent.rename(moved)
        parent.symlink_to(moved.name, target_is_directory=True)
        self.assert_reason("PROFILE_ANCHORED_DIRECTORY_INVALID")

    def test_gateway_or_paper_activity_is_rejected(self) -> None:
        self.fixture.active_unit = DEPLOYER.GATEWAY_BOUNDARY_UNITS[0]
        self.assert_reason("PROFILE_GATEWAY_BOUNDARY_NOT_STOPPED")

    def test_paper_activity_is_rejected(self) -> None:
        self.fixture.active_unit = DEPLOYER.PAPER_UNITS[-1]
        self.assert_reason("PROFILE_PAPER_BOUNDARY_NOT_STOPPED")

    def test_kill_switch_and_broker_deny_all_are_mandatory(self) -> None:
        marker = self.fixture.local(DEPLOYER.KILL_SWITCH_PATH)
        os.chmod(marker, 0o600)
        marker.write_bytes(b"released")
        os.chmod(marker, 0o440)
        self.assert_reason("PROFILE_KILL_SWITCH_INVALID")

    def test_kill_switch_parent_is_exact_control_boundary(self) -> None:
        parent = self.fixture.local(DEPLOYER.KILL_SWITCH_PATH.parent)
        for mode in (0o700, 0o755, 0o770):
            with self.subTest(mode=oct(mode)):
                os.chmod(parent, mode)
                self.assert_reason("PROFILE_ANCHORED_DIRECTORY_INVALID")
                os.chmod(parent, DEPLOYER.KILL_SWITCH_PARENT_MODE)

        with mock.patch.object(
                DEPLOYER, "PAPER_CONTROL_GID", self.fixture.gid + 1):
            self.assert_reason("PROFILE_ANCHORED_DIRECTORY_INVALID")

    def test_kill_switch_parent_symlink_is_rejected(self) -> None:
        parent = self.fixture.local(DEPLOYER.KILL_SWITCH_PATH.parent)
        preserved = parent.with_name(parent.name + "-preserved")
        parent.rename(preserved)
        parent.symlink_to(preserved.name, target_is_directory=True)
        self.assert_reason("PROFILE_ANCHORED_DIRECTORY_INVALID")
        self.assertFalse(self.fixture.local(DEPLOYER.BACKUP_PATH).exists())

    def test_kill_switch_parent_replacement_races_are_rejected(self) -> None:
        for stage in ("before_open", "open", "read", "final_stat"):
            fixture = Fixture()
            try:
                parent = fixture.local(DEPLOYER.KILL_SWITCH_PATH.parent)
                preserved = parent.with_name(parent.name + "-preserved")
                raced = False
                seam = f"after_kill_switch_{stage}"

                def replace_parent(name: str) -> None:
                    nonlocal raced
                    if name != seam or raced:
                        return
                    raced = True
                    parent.rename(preserved)
                    parent.mkdir()
                    os.chmod(parent, DEPLOYER.KILL_SWITCH_PARENT_MODE)
                    marker = parent / DEPLOYER.KILL_SWITCH_PATH.name
                    marker.write_bytes(b"engaged")
                    os.chmod(marker, 0o440)

                with self.subTest(stage=stage), mock.patch.object(
                        DEPLOYER, "SEAM_HOOK", replace_parent), \
                        self.assertRaises(DEPLOYER.DeployError) as raised:
                    DEPLOYER.deploy()
                self.assertEqual(
                    raised.exception.reason,
                    "PROFILE_ANCHORED_DIRECTORY_REBOUND",
                )
                self.assertTrue(raced)
                self.assertFalse(fixture.local(DEPLOYER.BACKUP_PATH).exists())
            finally:
                fixture.close()

    def test_broker_deny_all_failure_is_rejected(self) -> None:
        self.fixture.broker_returncode = 1
        self.assert_reason("PROFILE_BROKER_EGRESS_NOT_DENY_ALL")

    def test_broker_false_success_output_is_rejected(self) -> None:
        self.fixture.broker_stdout = (
            "hepta_broker_egress_policy: PASS "
            + "policy_sha256="
            + DEPLOYER.BROKER_EGRESS_DENY_ALL_SOURCE_SHA256
            + " authorized_connectors=1 authorized_uids=0 protected_ports=4\n")
        self.assert_reason("PROFILE_BROKER_EGRESS_NOT_DENY_ALL")

    def test_broker_wrong_deny_all_source_hash_is_rejected(self) -> None:
        self.fixture.broker_stdout = (
            "hepta_broker_egress_policy: PASS "
            + "policy_sha256=" + "0" * 64
            + " authorized_connectors=0 authorized_uids= protected_ports=4\n")
        self.assert_reason("PROFILE_BROKER_EGRESS_NOT_DENY_ALL")

    def test_broker_nonempty_stderr_is_rejected(self) -> None:
        self.fixture.broker_stderr = "warning\n"
        self.assert_reason("PROFILE_BROKER_EGRESS_NOT_DENY_ALL")

    def test_watch_custodian_units_must_all_be_fail_closed(self) -> None:
        for unit in DEPLOYER.WATCH_BOUNDARY_UNITS:
            fixture = Fixture()
            try:
                fixture.active_unit = unit
                with self.subTest(unit=unit), self.assertRaises(
                        DEPLOYER.DeployError) as raised:
                    DEPLOYER.deploy()
                self.assertEqual(
                    raised.exception.reason,
                    "PROFILE_WATCH_CUSTODIAN_ACTIVE")
                self.assertFalse(fixture.local(DEPLOYER.BACKUP_PATH).exists())
            finally:
                fixture.close()

    def test_exact_failed_watch_units_are_accepted_as_fail_closed(self) -> None:
        failed_units = {
            "hepta-shadow-watch-custodian-reconcile@alpha.timer",
            "hepta-shadow-watch-collector@alpha.service",
        }
        self.fixture.failed_units.update(failed_units)
        DEPLOYER.deploy()
        receipt = json.loads(
            self.fixture.local(DEPLOYER.RECEIPT_PATH).read_bytes())
        for unit in DEPLOYER.WATCH_BOUNDARY_UNITS:
            self.assertEqual(
                receipt["preflight_before"]["watch_boundary"]["units"][unit],
                ({"LoadState": "loaded", "ActiveState": "failed",
                  "SubState": "failed", "Job": ""}
                 if unit in failed_units else
                 {"LoadState": "loaded", "ActiveState": "inactive",
                  "SubState": "dead", "Job": ""}))
        DEPLOYER.validate_receipt(
            DEPLOYER.FileSnapshot(
                self.fixture.local(DEPLOYER.RECEIPT_PATH).read_bytes(),
                self.fixture.local(DEPLOYER.RECEIPT_PATH).stat()),
            self.fixture.shadow_install_evidence)

    def test_failed_watch_state_with_job_or_mixed_pair_is_rejected(
            self) -> None:
        for value in (
                {"LoadState": "loaded", "ActiveState": "failed",
                 "SubState": "failed", "Job": "queued.service"},
                {"LoadState": "loaded", "ActiveState": "failed",
                 "SubState": "dead", "Job": ""},
                {"LoadState": "loaded", "ActiveState": "inactive",
                 "SubState": "failed", "Job": ""},
                {"LoadState": "loaded", "ActiveState": "activating",
                 "SubState": "start", "Job": ""},
        ):
            with self.subTest(value=value):
                self.assertFalse(
                    DEPLOYER.fail_closed_watch_unit_state(value))

    def test_watch_sessions_require_unique_idle_bootstrap_lock(self) -> None:
        for mutation in ("missing", "extra", "mode", "busy"):
            fixture = Fixture()
            held = -1
            try:
                sessions = fixture.local(DEPLOYER.WATCH_SESSIONS_PATH)
                lock = sessions / DEPLOYER.SESSION_BOOTSTRAP_LOCK
                if mutation == "missing":
                    lock.unlink()
                elif mutation == "extra":
                    (sessions / "authority-token").write_bytes(b"token")
                elif mutation == "mode":
                    os.chmod(lock, 0o644)
                else:
                    held = os.open(lock, os.O_RDWR)
                    fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with self.subTest(mutation=mutation), self.assertRaises(
                        DEPLOYER.DeployError) as raised:
                    DEPLOYER.deploy()
                self.assertEqual(
                    raised.exception.reason,
                    "PROFILE_WATCH_AUTHORITY_RESIDUE")
                self.assertFalse(fixture.local(DEPLOYER.BACKUP_PATH).exists())
            finally:
                if held >= 0:
                    os.close(held)
                fixture.close()

    def test_watch_private_must_be_exact_and_empty(self) -> None:
        for mutation in (
            "snapshot", "mode", "parent_symlink", "leaf_symlink",
        ):
            fixture = Fixture()
            try:
                private = fixture.local(DEPLOYER.WATCH_PRIVATE_PATH)
                if mutation == "snapshot":
                    (private / "snapshot.json").write_text("{}\n")
                elif mutation == "mode":
                    os.chmod(private, 0o750)
                elif mutation == "parent_symlink":
                    parent = private.parent
                    moved = parent.with_name(parent.name + "-real")
                    parent.rename(moved)
                    parent.symlink_to(moved.name, target_is_directory=True)
                else:
                    moved = private.with_name("private-real")
                    private.rename(moved)
                    private.symlink_to(moved.name)
                with self.subTest(mutation=mutation), self.assertRaises(
                        DEPLOYER.DeployError) as raised:
                    DEPLOYER.deploy()
                self.assertEqual(
                    raised.exception.reason,
                    "PROFILE_WATCH_AUTHORITY_RESIDUE")
                self.assertFalse(fixture.local(DEPLOYER.BACKUP_PATH).exists())
            finally:
                fixture.close()

    def test_watch_private_uses_distinct_root_and_watch_policies(self) -> None:
        root_metadata = mock.Mock(
            st_mode=stat.S_IFDIR | 0o755, st_uid=0, st_gid=0)
        watch_metadata = mock.Mock(
            st_mode=stat.S_IFDIR | 0o700, st_uid=2104, st_gid=2104)
        with mock.patch.multiple(
            DEPLOYER,
            ROOT_UID=0,
            ROOT_GID=0,
            WATCH_UID=2104,
            WATCH_GID=2104,
        ):
            DEPLOYER.validate_directory(root_metadata)
            with self.assertRaises(DEPLOYER.DeployError):
                DEPLOYER.validate_directory(watch_metadata)
            DEPLOYER.validate_exact_leaf_directory(
                watch_metadata, (DEPLOYER.WATCH_UID, DEPLOYER.WATCH_GID, 0o700))

        real_open = DEPLOYER.open_anchored_directory
        opened: list[Path] = []

        def tracked_open(
            path: Path,
            *,
            create: bool = False,
            leaf_policy: tuple[int, int, int] | None = None,
            procfs: bool = False,
        ) -> int:
            opened.append(path)
            return real_open(
                path,
                create=create,
                leaf_policy=leaf_policy,
                procfs=procfs,
            )

        with mock.patch.object(
                DEPLOYER, "open_anchored_directory", side_effect=tracked_open):
            evidence = DEPLOYER.watch_private_without_authority()
        self.assertEqual(opened, [Path("/var/lib"), Path("/var/lib")])
        self.assertEqual(evidence["path"], str(DEPLOYER.WATCH_PRIVATE_PATH))
        self.assertEqual(evidence["entries"], [])
        self.assertIsNone(evidence["bootstrap_lock"])
        self.assertFalse(any("continuity" in key for key in evidence))
        DEPLOYER.validate_receipt_watch_directory(
            evidence,
            path=DEPLOYER.WATCH_PRIVATE_PATH,
            uid=DEPLOYER.WATCH_UID,
            gid=DEPLOYER.WATCH_GID,
            mode=0o700,
            bootstrap_lock=False,
        )

    def test_watch_private_parent_must_be_exact(self) -> None:
        for mutation in ("uid", "gid", "mode"):
            fixture = Fixture()
            try:
                parent = fixture.local(DEPLOYER.WATCH_PRIVATE_PATH).parent
                if mutation == "uid":
                    policy = mock.patch.object(
                        DEPLOYER, "WATCH_UID", fixture.uid + 1)
                elif mutation == "gid":
                    policy = mock.patch.object(
                        DEPLOYER, "WATCH_GID", fixture.gid + 1)
                else:
                    os.chmod(parent, 0o750)
                    policy = contextlib.nullcontext()
                seams: list[str] = []
                with self.subTest(mutation=mutation), policy, \
                        mock.patch.object(
                            DEPLOYER, "SEAM_HOOK", side_effect=seams.append), \
                        self.assertRaises(DEPLOYER.DeployError) as raised:
                    DEPLOYER.watch_private_without_authority()
                self.assertEqual(
                    raised.exception.reason,
                    "PROFILE_WATCH_AUTHORITY_RESIDUE",
                )
                self.assertEqual(seams, [])
            finally:
                fixture.close()

    def test_watch_private_leaf_uid_and_gid_must_be_exact(self) -> None:
        stable_fields = (
            "st_dev", "st_ino", "st_mode", "st_nlink", "st_uid", "st_gid",
            "st_size", "st_mtime_ns", "st_ctime_ns",
        )
        for field in ("st_uid", "st_gid"):
            fixture = Fixture()
            try:
                private = fixture.local(DEPLOYER.WATCH_PRIVATE_PATH)
                real_stat = os.stat
                real_fstat = os.fstat
                leaf_metadata = real_stat(private, follow_symlinks=False)
                leaf_inode = (
                    leaf_metadata.st_dev,
                    leaf_metadata.st_ino,
                )

                def mutate(metadata: os.stat_result) -> os.stat_result:
                    if (metadata.st_dev, metadata.st_ino) != leaf_inode:
                        return metadata
                    values = {
                        name: getattr(metadata, name)
                        for name in stable_fields
                    }
                    values[field] += 1
                    return mock.Mock(**values)

                def stat_with_wrong_leaf_owner(
                    path: object,
                    *,
                    dir_fd: int | None = None,
                    follow_symlinks: bool = True,
                ) -> os.stat_result:
                    return mutate(real_stat(
                        path,
                        dir_fd=dir_fd,
                        follow_symlinks=follow_symlinks,
                    ))

                def fstat_with_wrong_leaf_owner(
                    descriptor: int,
                ) -> os.stat_result:
                    return mutate(real_fstat(descriptor))

                seams: list[str] = []
                with self.subTest(field=field), \
                        mock.patch.object(
                            DEPLOYER.os,
                            "stat",
                            side_effect=stat_with_wrong_leaf_owner,
                        ), mock.patch.object(
                            DEPLOYER.os,
                            "fstat",
                            side_effect=fstat_with_wrong_leaf_owner,
                        ), mock.patch.object(
                            DEPLOYER, "SEAM_HOOK", side_effect=seams.append,
                        ), self.assertRaises(DEPLOYER.DeployError) as raised:
                    DEPLOYER.watch_private_without_authority()
                self.assertEqual(
                    raised.exception.reason,
                    "PROFILE_WATCH_AUTHORITY_RESIDUE",
                )
                self.assertEqual(seams, [
                    "after_watch_private_parent_open",
                    "after_watch_private_parent_inventory",
                ])
            finally:
                fixture.close()

    def test_watch_private_parent_and_leaf_inventories_are_exact(self) -> None:
        for mutation in ("parent_sibling", "leaf_entry"):
            fixture = Fixture()
            try:
                private = fixture.local(DEPLOYER.WATCH_PRIVATE_PATH)
                target = (
                    private.parent / "authority-token"
                    if mutation == "parent_sibling"
                    else private / "snapshot.json"
                )
                target.write_bytes(b"residue\n")
                with self.subTest(mutation=mutation), self.assertRaises(
                        DEPLOYER.DeployError) as raised:
                    DEPLOYER.watch_private_without_authority()
                self.assertEqual(
                    raised.exception.reason,
                    "PROFILE_WATCH_AUTHORITY_RESIDUE",
                )
            finally:
                fixture.close()

    def test_watch_private_parent_and_leaf_swaps_are_rejected(self) -> None:
        for target_name, seam in (
            ("parent", "after_watch_private_parent_open"),
            ("leaf", "after_watch_private_leaf_open"),
        ):
            fixture = Fixture()
            try:
                private = fixture.local(DEPLOYER.WATCH_PRIVATE_PATH)
                target = private.parent if target_name == "parent" else private
                preserved = target.with_name(target.name + "-preserved")
                swapped = False

                def swap(name: str) -> None:
                    nonlocal swapped
                    if name != seam or swapped:
                        return
                    swapped = True
                    target.rename(preserved)
                    target.mkdir()
                    os.chmod(target, 0o700)
                    if target_name == "parent":
                        replacement_leaf = target / private.name
                        replacement_leaf.mkdir()
                        os.chmod(replacement_leaf, 0o700)

                with self.subTest(target=target_name), mock.patch.object(
                        DEPLOYER, "SEAM_HOOK", side_effect=swap), \
                        self.assertRaises(DEPLOYER.DeployError) as raised:
                    DEPLOYER.watch_private_without_authority()
                self.assertEqual(
                    raised.exception.reason,
                    "PROFILE_WATCH_AUTHORITY_RESIDUE",
                )
                self.assertTrue(swapped)
            finally:
                fixture.close()

    def test_watch_private_parent_and_leaf_aba_are_rejected(self) -> None:
        for target_name, first_seam in (
            ("parent", "after_watch_private_parent_inventory"),
            ("leaf", "after_watch_private_leaf_inventory"),
        ):
            fixture = Fixture()
            try:
                private = fixture.local(DEPLOYER.WATCH_PRIVATE_PATH)
                target = private.parent if target_name == "parent" else private
                preserved = target.with_name(target.name + "-preserved")
                swapped = False
                restored = False

                def exchange_and_restore(name: str) -> None:
                    nonlocal swapped, restored
                    if name == first_seam and not swapped:
                        target.rename(preserved)
                        target.mkdir()
                        os.chmod(target, 0o700)
                        if target_name == "parent":
                            replacement_leaf = target / private.name
                            replacement_leaf.mkdir()
                            os.chmod(replacement_leaf, 0o700)
                        swapped = True
                    elif (
                        name == "before_watch_private_final_rebind"
                        and swapped
                        and not restored
                    ):
                        if target_name == "parent":
                            (target / private.name).rmdir()
                        target.rmdir()
                        preserved.rename(target)
                        restored = True

                with self.subTest(target=target_name), mock.patch.object(
                        DEPLOYER,
                        "SEAM_HOOK",
                        side_effect=exchange_and_restore,
                    ), self.assertRaises(DEPLOYER.DeployError) as raised:
                    DEPLOYER.watch_private_without_authority()
                self.assertEqual(
                    raised.exception.reason,
                    "PROFILE_WATCH_AUTHORITY_RESIDUE",
                )
                self.assertTrue(swapped)
                self.assertTrue(restored)
            finally:
                fixture.close()

    def test_watch_private_final_inventory_leaf_race_is_rejected(self) -> None:
        private = self.fixture.local(DEPLOYER.WATCH_PRIVATE_PATH)
        preserved = private.with_name("private-preserved")
        raced = False

        def replace_after_final_inventories(name: str) -> None:
            nonlocal raced
            if name != "after_watch_private_final_inventories" or raced:
                return
            raced = True
            private.rename(preserved)
            private.mkdir()
            os.chmod(private, 0o700)
            (private / "authority-token").write_bytes(b"residue\n")

        with mock.patch.object(
                DEPLOYER,
                "SEAM_HOOK",
                side_effect=replace_after_final_inventories,
            ), self.assertRaises(DEPLOYER.DeployError) as raised:
            DEPLOYER.watch_private_without_authority()
        self.assertEqual(
            raised.exception.reason,
            "PROFILE_WATCH_AUTHORITY_RESIDUE",
        )
        self.assertTrue(raced)
        self.assertEqual(
            (private / "authority-token").read_bytes(), b"residue\n")

    def test_watch_export_and_custodian_transaction_must_be_absent(
            self) -> None:
        for path, reason in (
            (DEPLOYER.WATCH_EXPORT_PATH, "PROFILE_WATCH_EXPORT_RESIDUE"),
            (
                DEPLOYER.CUSTODIAN_TRANSACTION_PATH,
                "PROFILE_WATCH_CUSTODIAN_TRANSACTION_PRESENT",
            ),
        ):
            fixture = Fixture()
            try:
                fixture.write_file(path, b"residue\n", 0o600)
                with self.subTest(path=str(path)), self.assertRaises(
                        DEPLOYER.DeployError) as raised:
                    DEPLOYER.deploy()
                self.assertEqual(raised.exception.reason, reason)
                self.assertFalse(fixture.local(DEPLOYER.BACKUP_PATH).exists())
            finally:
                fixture.close()

    def test_receipt_rejects_forged_watch_boundary(self) -> None:
        for mutation in ("unit", "lock", "private", "export", "transaction"):
            fixture = Fixture()
            try:
                DEPLOYER.deploy()
                receipt_path = fixture.local(DEPLOYER.RECEIPT_PATH)
                document = json.loads(receipt_path.read_bytes())
                for key in ("preflight_before", "preflight_after"):
                    watch = document[key]["watch_boundary"]
                    if mutation == "unit":
                        watch["units"][DEPLOYER.WATCH_BOUNDARY_UNITS[0]][
                            "ActiveState"] = "active"
                    elif mutation == "lock":
                        watch["sessions"]["bootstrap_lock"][
                            "idle_lock_observed"] = False
                    elif mutation == "private":
                        watch["private"]["entries"] = ["snapshot.json"]
                    elif mutation == "export":
                        watch["export"]["present"] = True
                    else:
                        watch["custodian_transaction"]["present"] = True
                body = dict(document)
                body.pop("body_sha256")
                document["body_sha256"] = DEPLOYER.digest_bytes(
                    DEPLOYER.canonical_bytes(body))
                receipt_path.write_bytes(DEPLOYER.canonical_bytes(document))
                os.chmod(receipt_path, 0o600)
                with self.subTest(mutation=mutation), self.assertRaises(
                        DEPLOYER.DeployError) as raised:
                    DEPLOYER.deploy()
                self.assertEqual(
                    raised.exception.reason, "PROFILE_RECEIPT_INVALID")
            finally:
                fixture.close()

    def test_systemctl_nonempty_stderr_is_rejected(self) -> None:
        self.fixture.systemctl_stderr = "warning\n"
        self.assert_reason("PROFILE_SYSTEMD_STATE_INVALID")

    def test_systemd_manager_nonempty_stderr_is_rejected(self) -> None:
        self.fixture.manager_stderr = "warning\n"
        self.assert_reason("PROFILE_SYSTEMD_MANAGER_INVALID")

    def test_campaign_policy_is_rejected(self) -> None:
        policy = self.fixture.local(DEPLOYER.PAPER_POLICY_ROOT) / "active.json"
        policy.write_bytes(b"{}\n")
        self.assert_reason("PROFILE_CAMPAIGN_POLICY_PRESENT")

    def test_campaign_policy_parent_replacement_race_is_rejected(self) -> None:
        policy_root = self.fixture.local(DEPLOYER.PAPER_POLICY_ROOT)
        saved = policy_root.with_name("paper-campaigns-preserved")
        raced = False

        def replace_after_empty_listing(name: str) -> None:
            nonlocal raced
            if name != "after_campaign_policy_first_empty_listing" or raced:
                return
            raced = True
            policy_root.rename(saved)
            policy_root.mkdir()
            os.chmod(policy_root, 0o755)

        with mock.patch.object(
                DEPLOYER, "SEAM_HOOK", replace_after_empty_listing):
            self.assert_reason("PROFILE_CAMPAIGN_POLICY_INVALID")
        self.assertTrue(raced)
        self.assertTrue(saved.is_dir())
        self.assertTrue(policy_root.is_dir())
        self.assertFalse(self.fixture.local(DEPLOYER.BACKUP_PATH).exists())

    def test_campaign_policy_entry_addition_race_is_rejected(self) -> None:
        policy_root = self.fixture.local(DEPLOYER.PAPER_POLICY_ROOT)
        raced = False

        def add_after_empty_listing(name: str) -> None:
            nonlocal raced
            if name != "after_campaign_policy_first_empty_listing" or raced:
                return
            raced = True
            (policy_root / "raced-policy.json").write_bytes(b"{}\n")

        with mock.patch.object(DEPLOYER, "SEAM_HOOK", add_after_empty_listing):
            self.assert_reason("PROFILE_CAMPAIGN_POLICY_INVALID")
        self.assertTrue(raced)
        self.assertEqual(
            (policy_root / "raced-policy.json").read_bytes(), b"{}\n")
        self.assertFalse(self.fixture.local(DEPLOYER.BACKUP_PATH).exists())

    def test_existing_backup_or_receipt_is_rejected(self) -> None:
        self.fixture.write_file(DEPLOYER.BACKUP_PATH, b"occupied", 0o600)
        self.assert_reason("PROFILE_BACKUP_INVALID")

    def test_existing_receipt_is_rejected(self) -> None:
        self.fixture.write_file(DEPLOYER.RECEIPT_PATH, b"occupied", 0o600)
        self.assert_reason("PROFILE_RECEIPT_INVALID")

    def test_backup_and_backup_stage_double_occupancy_is_preserved(self) -> None:
        backup = self.fixture.write_file(
            DEPLOYER.BACKUP_PATH, DEPLOYER.OLD_PAYLOAD, 0o600)
        staged = self.fixture.write_file(
            DEPLOYER.BACKUP_TEMP_PATH, DEPLOYER.OLD_PAYLOAD, 0o600)
        identities = (backup.stat().st_ino, staged.stat().st_ino)
        self.assert_reason("PROFILE_TRANSACTION_STATE_INVALID")
        self.assertEqual(
            identities, (backup.stat().st_ino, staged.stat().st_ino))
        self.assertEqual(backup.read_bytes(), DEPLOYER.OLD_PAYLOAD)
        self.assertEqual(staged.read_bytes(), DEPLOYER.OLD_PAYLOAD)

    def test_receipt_and_receipt_stage_double_occupancy_is_preserved(self) -> None:
        DEPLOYER.deploy()
        receipt = self.fixture.local(DEPLOYER.RECEIPT_PATH)
        staged = self.fixture.write_file(
            DEPLOYER.RECEIPT_TEMP_PATH, receipt.read_bytes(), 0o600)
        identities = (receipt.stat().st_ino, staged.stat().st_ino)
        self.assert_reason("PROFILE_TRANSACTION_STATE_INVALID")
        self.assertEqual(
            identities, (receipt.stat().st_ino, staged.stat().st_ino))

    def test_unknown_target_candidate_is_preserved_without_mutation(self) -> None:
        candidate = self.fixture.write_file(
            DEPLOYER.TARGET_TEMP_PATH, b"unknown-candidate\n", 0o644)
        identity = candidate.stat().st_ino
        self.assert_reason("PROFILE_TARGET_TEMP_INVALID")
        self.assertEqual(candidate.stat().st_ino, identity)
        self.assertEqual(candidate.read_bytes(), b"unknown-candidate\n")
        self.assertFalse(self.fixture.local(DEPLOYER.BACKUP_PATH).exists())

    def test_success_receipt_rejects_rebound_retained_old_inode(self) -> None:
        DEPLOYER.deploy()
        retained = self.fixture.local(DEPLOYER.TARGET_TEMP_PATH)
        saved = retained.with_name("attacker-preserved-retained-old")
        retained.rename(saved)
        replacement = retained.with_name("attacker-replacement-retained-old")
        replacement.write_bytes(DEPLOYER.OLD_PAYLOAD)
        os.chmod(replacement, 0o644)
        replacement.rename(retained)
        self.assert_reason("PROFILE_TRANSACTION_STATE_INVALID")
        self.assertEqual(saved.read_bytes(), DEPLOYER.OLD_PAYLOAD)
        self.assertEqual(retained.read_bytes(), DEPLOYER.OLD_PAYLOAD)

    def test_target_exchange_race_restores_writer_without_inode_loss(self) -> None:
        target = self.fixture.local(DEPLOYER.TARGET_PATH)
        candidate = self.fixture.local(DEPLOYER.TARGET_TEMP_PATH)
        saved = target.with_name("attacker-preserved-original.env")
        real_renameat2 = DEPLOYER.renameat2
        raced = False
        writer_inode: int | None = None

        def race(
            old_parent: int,
            old_name: str,
            new_parent: int,
            new_name: str,
            flags: int,
            reason: str,
        ) -> None:
            nonlocal raced, writer_inode
            if (
                not raced
                and old_name == DEPLOYER.TARGET_TEMP_PATH.name
                and new_name == DEPLOYER.TARGET_PATH.name
                and flags == DEPLOYER.RENAME_EXCHANGE
            ):
                raced = True
                target.rename(saved)
                writer = target.with_name("attacker-writer.env")
                writer.write_bytes(DEPLOYER.OLD_PAYLOAD)
                os.chmod(writer, 0o644)
                writer_inode = writer.stat().st_ino
                writer.rename(target)
            real_renameat2(
                old_parent, old_name, new_parent, new_name, flags, reason)

        with mock.patch.object(DEPLOYER, "renameat2", side_effect=race):
            self.assert_reason("PROFILE_TARGET_REBOUND")
        self.assertTrue(raced)
        self.assertIsNotNone(writer_inode)
        self.assertEqual(target.stat().st_ino, writer_inode)
        self.assertEqual(target.read_bytes(), DEPLOYER.OLD_PAYLOAD)
        self.assertEqual(saved.read_bytes(), DEPLOYER.OLD_PAYLOAD)
        self.assertEqual(candidate.read_bytes(), DEPLOYER.NEW_PAYLOAD)
        receipt = DEPLOYER.deploy()
        self.assertEqual(receipt, self.assert_success_state(self.fixture))

    def test_fresh_candidate_in_place_drift_is_rejected_before_exchange(self) -> None:
        candidate = self.fixture.local(DEPLOYER.TARGET_TEMP_PATH)
        target = self.fixture.local(DEPLOYER.TARGET_PATH)
        unknown = b"Q" * len(DEPLOYER.NEW_PAYLOAD)
        original_inode: int | None = None
        raced = False

        def overwrite_candidate(name: str) -> None:
            nonlocal original_inode, raced
            if name != "before_target_exchange" or raced:
                return
            raced = True
            original_inode = candidate.stat().st_ino
            candidate.write_bytes(unknown)
            os.chmod(candidate, 0o644)

        with mock.patch.object(DEPLOYER, "SEAM_HOOK", overwrite_candidate):
            self.assert_reason("PROFILE_TARGET_TEMP_INVALID")
        self.assertTrue(raced)
        self.assertEqual(candidate.stat().st_ino, original_inode)
        self.assertEqual(candidate.read_bytes(), unknown)
        self.assertEqual(target.read_bytes(), DEPLOYER.OLD_PAYLOAD)
        self.assertFalse(self.fixture.local(DEPLOYER.RECEIPT_PATH).exists())

    def test_last_window_candidate_drift_is_exchanged_back_safely(self) -> None:
        candidate = self.fixture.local(DEPLOYER.TARGET_TEMP_PATH)
        target = self.fixture.local(DEPLOYER.TARGET_PATH)
        unknown = b"Q" * len(DEPLOYER.NEW_PAYLOAD)
        original_inode: int | None = None
        raced = False

        def overwrite_candidate(name: str) -> None:
            nonlocal original_inode, raced
            if (
                name != "after_target_final_precheck_before_exchange"
                or raced
            ):
                return
            raced = True
            original_inode = candidate.stat().st_ino
            candidate.write_bytes(unknown)
            os.chmod(candidate, 0o644)

        with mock.patch.object(DEPLOYER, "SEAM_HOOK", overwrite_candidate):
            self.assert_reason("PROFILE_TARGET_REBOUND")
        self.assertTrue(raced)
        self.assertEqual(target.read_bytes(), DEPLOYER.OLD_PAYLOAD)
        self.assertEqual(candidate.stat().st_ino, original_inode)
        self.assertEqual(candidate.read_bytes(), unknown)
        self.assertFalse(self.fixture.local(DEPLOYER.RECEIPT_PATH).exists())

    def test_backup_recovery_move_race_preserves_both_inodes(self) -> None:
        self.hard_crash_at("after_backup_temp_fsync")
        staged = self.fixture.local(DEPLOYER.BACKUP_TEMP_PATH)
        backup = self.fixture.local(DEPLOYER.BACKUP_PATH)
        saved = staged.with_name("attacker-preserved-backup-stage")
        original_inode = staged.stat().st_ino
        real_renameat2 = DEPLOYER.renameat2
        raced = False
        writer_inode: int | None = None

        def race(
            old_parent: int,
            old_name: str,
            new_parent: int,
            new_name: str,
            flags: int,
            reason: str,
        ) -> None:
            nonlocal raced, writer_inode
            if (
                not raced
                and old_name == DEPLOYER.BACKUP_TEMP_PATH.name
                and new_name == DEPLOYER.BACKUP_PATH.name
                and flags == DEPLOYER.RENAME_NOREPLACE
            ):
                raced = True
                staged.rename(saved)
                staged.write_bytes(b"attacker-backup-stage\n")
                os.chmod(staged, 0o600)
                writer_inode = staged.stat().st_ino
            real_renameat2(
                old_parent, old_name, new_parent, new_name, flags, reason)

        with mock.patch.object(DEPLOYER, "renameat2", side_effect=race):
            self.assert_reason("PROFILE_RECOVERY_BACKUP_TEMP_DRIFT")
        self.assertTrue(raced)
        self.assertFalse(backup.exists())
        self.assertEqual(saved.stat().st_ino, original_inode)
        self.assertEqual(saved.read_bytes(), DEPLOYER.OLD_PAYLOAD)
        self.assertEqual(staged.stat().st_ino, writer_inode)
        self.assertEqual(staged.read_bytes(), b"attacker-backup-stage\n")
        self.assert_reason("PROFILE_BACKUP_TEMP_INVALID")

    def test_receipt_commit_move_race_preserves_both_inodes(self) -> None:
        self.hard_crash_at("after_receipt_temp_fsync")
        staged = self.fixture.local(DEPLOYER.RECEIPT_TEMP_PATH)
        receipt = self.fixture.local(DEPLOYER.RECEIPT_PATH)
        saved = staged.with_name("attacker-preserved-receipt-stage")
        original_inode = staged.stat().st_ino
        original_payload = staged.read_bytes()
        real_renameat2 = DEPLOYER.renameat2
        raced = False
        writer_inode: int | None = None

        def race(
            old_parent: int,
            old_name: str,
            new_parent: int,
            new_name: str,
            flags: int,
            reason: str,
        ) -> None:
            nonlocal raced, writer_inode
            if (
                not raced
                and old_name == DEPLOYER.RECEIPT_TEMP_PATH.name
                and new_name == DEPLOYER.RECEIPT_PATH.name
                and flags == DEPLOYER.RENAME_NOREPLACE
            ):
                raced = True
                staged.rename(saved)
                staged.write_bytes(b"attacker-receipt-stage\n")
                os.chmod(staged, 0o600)
                writer_inode = staged.stat().st_ino
            real_renameat2(
                old_parent, old_name, new_parent, new_name, flags, reason)

        with mock.patch.object(DEPLOYER, "renameat2", side_effect=race):
            self.assert_reason("PROFILE_RECEIPT_PUBLISH_FAILED")
        self.assertTrue(raced)
        self.assertFalse(receipt.exists())
        self.assertEqual(saved.stat().st_ino, original_inode)
        self.assertEqual(saved.read_bytes(), original_payload)
        self.assertEqual(staged.stat().st_ino, writer_inode)
        self.assertEqual(staged.read_bytes(), b"attacker-receipt-stage\n")
        self.assert_reason("PROFILE_RECEIPT_TEMP_INVALID")

    def test_target_inode_replacement_seam_fails_before_replace(self) -> None:
        target = self.fixture.local(DEPLOYER.TARGET_PATH)

        def replace_at_seam(name: str) -> None:
            if name != "before_target_replace":
                return
            replacement = target.with_name("replacement.env")
            replacement.write_bytes(DEPLOYER.OLD_PAYLOAD)
            os.chmod(replacement, 0o644)
            os.replace(replacement, target)

        with mock.patch.object(DEPLOYER, "SEAM_HOOK", replace_at_seam):
            self.assert_reason("PROFILE_TARGET_REBOUND")
        self.assertEqual(target.read_bytes(), DEPLOYER.OLD_PAYLOAD)
        self.assertFalse(self.fixture.local(DEPLOYER.RECEIPT_PATH).exists())

    def test_directory_replacement_seam_fails_before_replace(self) -> None:
        parent = self.fixture.local(DEPLOYER.TARGET_PATH.parent)

        def replace_at_seam(name: str) -> None:
            if name != "before_target_replace":
                return
            moved = parent.with_name("trust-domains-original")
            parent.rename(moved)
            parent.mkdir()
            os.chmod(parent, 0o755)
            replacement = parent / DEPLOYER.TARGET_PATH.name
            replacement.write_bytes(DEPLOYER.OLD_PAYLOAD)
            os.chmod(replacement, 0o644)

        with mock.patch.object(DEPLOYER, "SEAM_HOOK", replace_at_seam):
            self.assert_reason("PROFILE_ANCHORED_DIRECTORY_REBOUND")
        self.assertEqual(
            (parent / DEPLOYER.TARGET_PATH.name).read_bytes(),
            DEPLOYER.OLD_PAYLOAD)

    def test_failure_after_atomic_replace_rolls_back_exact_old_profile(self) -> None:
        def fail_at_seam(name: str) -> None:
            if name == "after_target_replace":
                raise DEPLOYER.DeployError("PROFILE_TEST_SEAM_FAILURE")

        with mock.patch.object(DEPLOYER, "SEAM_HOOK", fail_at_seam):
            self.assert_reason("PROFILE_TEST_SEAM_FAILURE")
        target = self.fixture.local(DEPLOYER.TARGET_PATH)
        self.assertEqual(target.read_bytes(), DEPLOYER.OLD_PAYLOAD)
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o644)
        self.assertEqual(
            self.fixture.local(DEPLOYER.BACKUP_PATH).read_bytes(),
            DEPLOYER.OLD_PAYLOAD)
        self.assertFalse(self.fixture.local(DEPLOYER.RECEIPT_PATH).exists())

    def test_parent_fsync_failure_after_replace_rolls_back(self) -> None:
        real_fsync = os.fsync
        fail_next_fsync = False

        def arm_fsync_failure(name: str) -> None:
            nonlocal fail_next_fsync
            if name == "after_target_replace_before_parent_fsync":
                fail_next_fsync = True

        def injected_fsync(descriptor: int) -> None:
            nonlocal fail_next_fsync
            if fail_next_fsync:
                fail_next_fsync = False
                raise OSError("injected parent fsync failure")
            real_fsync(descriptor)

        with (
            mock.patch.object(DEPLOYER, "SEAM_HOOK", arm_fsync_failure),
            mock.patch.object(DEPLOYER.os, "fsync", side_effect=injected_fsync),
        ):
            self.assert_reason("PROFILE_ATOMIC_EXCHANGE_FAILED")
        self.assertEqual(
            self.fixture.local(DEPLOYER.TARGET_PATH).read_bytes(),
            DEPLOYER.OLD_PAYLOAD)
        self.assertFalse(self.fixture.local(DEPLOYER.RECEIPT_PATH).exists())

    def assert_receipt_commit_seam_preserves_commit_intent(
        self, seam: str,
    ) -> None:
        def fail_at_seam(name: str) -> None:
            if name == seam:
                raise DEPLOYER.DeployError("PROFILE_TEST_RECEIPT_SEAM_FAILURE")

        with mock.patch.object(DEPLOYER, "SEAM_HOOK", fail_at_seam):
            self.assert_reason("PROFILE_TEST_RECEIPT_SEAM_FAILURE")
        expected = DEPLOYER.deploy()
        self.assertEqual(expected, self.assert_success_state(self.fixture))

    def test_receipt_commit_post_rename_failure_is_resumable(self) -> None:
        self.assert_receipt_commit_seam_preserves_commit_intent(
            "after_receipt_commit_rename")

    def test_receipt_commit_post_fsync_failure_is_resumable(self) -> None:
        self.assert_receipt_commit_seam_preserves_commit_intent(
            "after_receipt_commit_fsync")

    def test_receipt_commit_post_verify_failure_is_idempotent(self) -> None:
        self.assert_receipt_commit_seam_preserves_commit_intent(
            "after_receipt_commit_post_verify")

    def test_failure_after_receipt_publish_preserves_success_state(self) -> None:
        def fail_at_seam(name: str) -> None:
            if name == "after_receipt_publish":
                raise DEPLOYER.DeployError("PROFILE_TEST_FINAL_SEAM_FAILURE")

        with mock.patch.object(DEPLOYER, "SEAM_HOOK", fail_at_seam):
            self.assert_reason("PROFILE_TEST_FINAL_SEAM_FAILURE")
        expected = DEPLOYER.deploy()
        self.assertEqual(expected, self.assert_success_state(self.fixture))

    def test_main_pin_is_reattest_only_and_old_target_is_zero_write(self) -> None:
        output = io.StringIO()
        errors = io.StringIO()
        target = self.fixture.local(DEPLOYER.TARGET_PATH)
        target_before = (
            target.read_bytes(), DEPLOYER.stable_identity(target.stat()))
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            result = DEPLOYER.main([
                "--expected-install-manifest-sha256", "sha256:" + "1" * 64,
                "--expected-install-receipt-sha256", "sha256:" + "2" * 64,
                "--expected-prior-profile-receipt-sha256",
                DEPLOYER.ROUND95_RECEIPT_FILE_SHA256,
            ])
        self.assertEqual(result, 1)
        self.assertEqual(output.getvalue(), "")
        self.assertEqual(
            errors.getvalue(),
            "hepta-p1-watch-profile-deployer: ERROR PROFILE_REBIND_REQUIRED\n")
        self.assertEqual(
            (target.read_bytes(), DEPLOYER.stable_identity(target.stat())),
            target_before)
        for path in (
                DEPLOYER.BACKUP_PATH, DEPLOYER.TARGET_TEMP_PATH,
                DEPLOYER.RECEIPT_PATH, DEPLOYER.RECEIPT_TEMP_PATH,
                DEPLOYER.ROUND95_RECEIPT_PATH,
                DEPLOYER.ROUND95_RECEIPT_TEMP_PATH,
                DEPLOYER.ROUND114_RECEIPT_PATH,
                DEPLOYER.ROUND114_RECEIPT_TEMP_PATH):
            self.assertFalse(self.fixture.local(path).exists(), str(path))

    def test_shadow_install_guard_precedes_profile_lock_and_receives_pins(
            self) -> None:
        manifest_sha256 = "sha256:" + "3" * 64
        receipt_sha256 = "sha256:" + "4" * 64
        real_acquire = DEPLOYER.acquire_transaction_lock

        def acquire_inner() -> int:
            self.assertEqual(
                self.fixture.shadow_install_acquire_arguments,
                [(manifest_sha256, receipt_sha256)])
            self.assertGreater(
                self.fixture.shadow_install_validation_count, 0)
            return real_acquire()

        with mock.patch.object(
                DEPLOYER, "acquire_transaction_lock",
                side_effect=acquire_inner):
            DEPLOYER.deploy(manifest_sha256, receipt_sha256)
        self.assertEqual(self.fixture.shadow_install_release_count, 1)

    def test_shadow_install_failure_precedes_all_profile_mutation(self) -> None:
        with mock.patch.object(
                DEPLOYER, "acquire_shadow_install_binding",
                side_effect=DEPLOYER.DeployError(
                    "PROFILE_SHADOW_INSTALL_INVALID")), \
                mock.patch.object(
                    DEPLOYER, "acquire_transaction_lock") as inner:
            self.assert_reason("PROFILE_SHADOW_INSTALL_INVALID")
        inner.assert_not_called()
        self.assertEqual(
            self.fixture.local(DEPLOYER.TARGET_PATH).read_bytes(),
            DEPLOYER.OLD_PAYLOAD)
        self.assertFalse(self.fixture.local(DEPLOYER.BACKUP_PATH).exists())

    def test_shadow_install_guard_is_held_through_receipt_publication(
            self) -> None:
        observed = False

        def hook(name: str) -> None:
            nonlocal observed
            if name == "after_receipt_publish":
                observed = True
                self.assertEqual(
                    self.fixture.shadow_install_release_count, 0)

        with mock.patch.object(DEPLOYER, "SEAM_HOOK", hook):
            DEPLOYER.deploy()
        self.assertTrue(observed)
        self.assertEqual(self.fixture.shadow_install_release_count, 1)

    def test_shadow_install_rebound_before_commit_fails_closed(self) -> None:
        def hook(name: str) -> None:
            if name == "after_receipt_temp_fsync":
                self.fixture.shadow_install_evidence["closure_sha256"] = (
                    "sha256:" + "9" * 64)

        with mock.patch.object(DEPLOYER, "SEAM_HOOK", hook):
            self.assert_reason("PROFILE_SHADOW_INSTALL_REBOUND")
        self.assertFalse(self.fixture.local(DEPLOYER.RECEIPT_PATH).exists())

    def test_cli_requires_all_external_digest_pins(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                DEPLOYER.parse_arguments([])
            with self.assertRaises(SystemExit):
                DEPLOYER.parse_arguments([
                    "--expected-install-manifest-sha256",
                    "sha256:" + "1" * 64,
                    "--expected-install-receipt-sha256",
                    "sha256:" + "2" * 64,
                ])
        parsed = DEPLOYER.parse_arguments([
            "--expected-install-manifest-sha256", "sha256:" + "1" * 64,
            "--expected-install-receipt-sha256", "sha256:" + "2" * 64,
            "--expected-prior-profile-receipt-sha256",
            DEPLOYER.ROUND95_RECEIPT_FILE_SHA256,
            "--transition-dormant-paper-to-watch",
            DEPLOYER.ROUND114_TRANSITION_TOKEN,
        ])
        self.assertEqual(
            parsed.transition_dormant_paper_to_watch,
            DEPLOYER.ROUND114_TRANSITION_TOKEN)

    def test_gateway_requires_persistent_not_runtime_mask(self) -> None:
        self.fixture.gateway_unit_file_state = "masked-runtime"
        self.assert_reason("PROFILE_GATEWAY_BOUNDARY_NOT_STOPPED")
        self.assertFalse(self.fixture.local(DEPLOYER.BACKUP_PATH).exists())

    def test_gateway_fragment_path_must_be_exact_persistent_mask(self) -> None:
        unit = DEPLOYER.GATEWAY_BOUNDARY_UNITS[0]
        invalid_paths = (
            DEPLOYER.MASK_TARGET,
            str(DEPLOYER.RUNTIME_MASK_ROOT / unit),
            str(
                DEPLOYER.PERSISTENT_MASK_ROOT
                / DEPLOYER.GATEWAY_BOUNDARY_UNITS[1]),
        )
        for path in invalid_paths:
            with self.subTest(path=path):
                self.fixture.gateway_fragment_path = path
                self.assert_reason("PROFILE_GATEWAY_BOUNDARY_NOT_STOPPED")
                self.assertFalse(
                    self.fixture.local(DEPLOYER.BACKUP_PATH).exists())

    def test_gateway_service_need_daemon_reload_no_is_rejected(self) -> None:
        self.fixture.gateway_need_daemon_reload[
            DEPLOYER.GATEWAY_SERVICE_UNIT] = "no"
        self.assert_reason("PROFILE_GATEWAY_BOUNDARY_NOT_STOPPED")
        self.assertFalse(self.fixture.local(DEPLOYER.BACKUP_PATH).exists())

    def test_gateway_socket_need_daemon_reload_yes_is_rejected(self) -> None:
        for unit in DEPLOYER.GATEWAY_BOUNDARY_UNITS[1:]:
            fixture = Fixture()
            try:
                fixture.gateway_need_daemon_reload[unit] = "yes"
                with self.subTest(unit=unit), self.assertRaises(
                        DEPLOYER.DeployError) as raised:
                    DEPLOYER.deploy()
                self.assertEqual(
                    raised.exception.reason,
                    "PROFILE_GATEWAY_BOUNDARY_NOT_STOPPED")
                self.assertFalse(fixture.local(DEPLOYER.BACKUP_PATH).exists())
            finally:
                fixture.close()

    def test_gateway_manager_closure_fields_are_exact(self) -> None:
        mutations = {
            "source": (
                "gateway_source_paths", "/run/systemd/generator/source"),
            "dropin-empty": ("gateway_drop_in_paths", ""),
            "dropin-extra": (
                "gateway_drop_in_paths",
                str(DEPLOYER.GATEWAY_SERVICE_DROPIN_PATH)
                + " /etc/systemd/system/service.d/extra.conf"),
            "binds-to": ("gateway_binds_to", ""),
            "after": ("gateway_after", "network.target"),
            "names": (
                "gateway_names",
                DEPLOYER.GATEWAY_SERVICE_UNIT + " gateway-alias.service"),
            "wants": ("gateway_wants", "unexpected.service"),
            "requires": ("gateway_requires", "unexpected.service"),
            "upholds": ("gateway_upholds", "unexpected.service"),
        }
        for name, (attribute, value) in mutations.items():
            fixture = Fixture()
            try:
                getattr(fixture, attribute)[
                    DEPLOYER.GATEWAY_SERVICE_UNIT] = value
                with self.subTest(name=name), self.assertRaises(
                        DEPLOYER.DeployError) as raised:
                    DEPLOYER.deploy()
                self.assertEqual(
                    raised.exception.reason,
                    "PROFILE_GATEWAY_BOUNDARY_NOT_STOPPED")
                self.assertFalse(fixture.local(DEPLOYER.BACKUP_PATH).exists())
            finally:
                fixture.close()

    def test_broker_manager_closure_fields_are_exact(self) -> None:
        mutations = {
            "id": ("Id", "other.service"),
            "names": (
                "Names",
                DEPLOYER.BROKER_EGRESS_UNIT + " broker-alias.service"),
            "load": ("LoadState", "not-found"),
            "active": ("ActiveState", "active"),
            "sub": ("SubState", "running"),
            "unit-file": ("UnitFileState", "disabled"),
            "fragment": (
                "FragmentPath",
                "/etc/systemd/system/hepta-broker-egress-policy.service"),
            "source": ("SourcePath", "/run/systemd/generator/source"),
            "dropin": (
                "DropInPaths",
                "/etc/systemd/system/"
                "hepta-broker-egress-policy.service.d/90-extra.conf"),
            "reload": ("NeedDaemonReload", "no"),
            "job": ("Job", "123 start"),
            "main-pid": ("MainPID", "1108254"),
            "exec-main-pid": ("ExecMainPID", "01"),
            "control-pid": ("ControlPID", "1108254"),
        }
        for name, (field, value) in mutations.items():
            fixture = Fixture()
            try:
                fixture.broker_offline_fields[field] = value
                with self.subTest(name=name), self.assertRaises(
                        DEPLOYER.DeployError) as raised:
                    DEPLOYER.deploy()
                self.assertEqual(
                    raised.exception.reason,
                    "PROFILE_BROKER_EGRESS_UNIT_NOT_OFFLINE")
                self.assertFalse(fixture.local(DEPLOYER.BACKUP_PATH).exists())
            finally:
                fixture.close()

        # Both systemd terminal forms are explicitly accepted, but neither
        # may carry a live MainPID/ControlPID or pending job.
        self.fixture.broker_offline_fields.update({
            "ActiveState": "inactive", "SubState": "dead",
        })
        receipt = DEPLOYER.deploy()
        self.assertEqual(receipt, self.assert_success_state(self.fixture))

    def test_historical_exec_main_pid_is_not_frozen_and_must_be_offline(
            self) -> None:
        self.fixture.broker_offline_fields["ExecMainPID"] = "1108254"
        receipt = DEPLOYER.deploy()
        self.assertEqual(receipt, self.assert_success_state(self.fixture))
        self.assertGreaterEqual(
            len(self.fixture.historical_exec_main_pid_checks), 2)
        self.assertEqual(
            set(self.fixture.historical_exec_main_pid_checks), {1108254})

        fixture = Fixture()
        try:
            fixture.historical_exec_main_pid_allowed = False
            with self.assertRaises(DEPLOYER.DeployError) as raised:
                DEPLOYER.deploy()
            self.assertEqual(
                raised.exception.reason,
                "PROFILE_BROKER_EGRESS_UNIT_NOT_OFFLINE",
            )
            self.assertEqual(
                fixture.historical_exec_main_pid_checks, [1108253])
            self.assertFalse(fixture.local(DEPLOYER.BACKUP_PATH).exists())
        finally:
            fixture.close()

    def test_gateway_and_paper_pending_jobs_are_rejected(self) -> None:
        cases = (
            (DEPLOYER.GATEWAY_BOUNDARY_UNITS[0],
             "PROFILE_GATEWAY_BOUNDARY_NOT_STOPPED"),
            (DEPLOYER.PAPER_UNITS[0],
             "PROFILE_PAPER_BOUNDARY_NOT_STOPPED"),
        )
        for unit, reason in cases:
            fixture = Fixture()
            try:
                fixture.unit_jobs[unit] = "123 start"
                with self.subTest(unit=unit), self.assertRaises(
                        DEPLOYER.DeployError) as raised:
                    DEPLOYER.deploy()
                self.assertEqual(raised.exception.reason, reason)
                self.assertFalse(fixture.local(DEPLOYER.BACKUP_PATH).exists())
            finally:
                fixture.close()

    def test_full_typed_manager_cache_rejects_hidden_semantics(self) -> None:
        service = DEPLOYER.GATEWAY_SERVICE_UNIT
        service_interface = DEPLOYER.SYSTEMD_DBUS_EXECUTION_INTERFACES[
            service]
        cases = {
            "on-failure": (
                DEPLOYER.SYSTEMD_DBUS_UNIT_INTERFACE,
                "OnFailure", {"type": "as", "data": ["attack.service"]}),
            "condition": (
                DEPLOYER.SYSTEMD_DBUS_UNIT_INTERFACE,
                "Conditions", {
                    "type": "a(sbbsi)",
                    "data": [[
                        "ConditionPathExists", False, False,
                        "/tmp/attack", 0,
                    ]],
                }),
            "exec-reload": (
                service_interface, "ExecReload", {
                    "type": "a(sasbttttuii)",
                    "data": [[
                        "/usr/bin/true", ["/usr/bin/true"], False,
                        0, 0, 0, 0, 0, 0, 0,
                    ]],
                }),
            "exec-stop": (
                service_interface, "ExecStop", {
                    "type": "a(sasbttttuii)",
                    "data": [[
                        "/usr/bin/true", ["/usr/bin/true"], False,
                        0, 0, 0, 0, 0, 0, 0,
                    ]],
                }),
            "exec-start-pre": (
                service_interface, "ExecStartPre", {
                    "type": "a(sasbttttuii)",
                    "data": [[
                        "/usr/bin/true", ["/usr/bin/true"], False,
                        0, 0, 0, 0, 0, 0, 0,
                    ]],
                }),
            "load-credential": (
                service_interface, "LoadCredential", {
                    "type": "a(ss)",
                    "data": [["attack", "/tmp/attack"]],
                }),
            "open-file": (
                service_interface, "OpenFile", {
                    "type": "a(sst)",
                    "data": [["/tmp/attack", "attack", 0]],
                }),
        }
        for name, (interface, property_name, value) in cases.items():
            fixture = Fixture()
            try:
                fixture.dbus_interface_properties[service][interface][
                    property_name] = value
                with self.subTest(name=name), self.assertRaises(
                        DEPLOYER.DeployError) as raised:
                    DEPLOYER.deploy()
                self.assertEqual(
                    raised.exception.reason,
                    "PROFILE_SYSTEMD_MANAGER_UNIT_CONTRACT_INVALID",
                )
                self.assertFalse(fixture.local(DEPLOYER.BACKUP_PATH).exists())
            finally:
                fixture.close()

    def test_full_typed_manager_cache_rejects_schema_drift(self) -> None:
        unit = DEPLOYER.GATEWAY_SERVICE_UNIT
        interface = DEPLOYER.SYSTEMD_DBUS_EXECUTION_INTERFACES[unit]
        for mutation in ("missing", "signature", "extra"):
            fixture = Fixture()
            try:
                properties = fixture.dbus_interface_properties[unit][interface]
                if mutation == "missing":
                    properties.pop("LoadCredential")
                elif mutation == "signature":
                    properties["LoadCredential"]["type"] = "as"
                else:
                    properties["UnknownFuturePolicy"] = {
                        "type": "s", "data": "unsafe"}
                with self.subTest(mutation=mutation), self.assertRaises(
                        DEPLOYER.DeployError) as raised:
                    DEPLOYER.deploy()
                self.assertEqual(
                    raised.exception.reason,
                    "PROFILE_SYSTEMD_MANAGER_UNIT_CONTRACT_INVALID",
                )
            finally:
                fixture.close()

    def test_manager_cache_runtime_counters_are_excluded(self) -> None:
        broker = DEPLOYER.BROKER_EGRESS_UNIT
        broker_interface = DEPLOYER.SYSTEMD_DBUS_EXECUTION_INTERFACES[broker]
        self.fixture.dbus_interface_properties[broker][broker_interface][
            "CPUUsageNSec"]["data"] = 987654321
        for field, value in {
            "CPUUsageNSec": "987654321",
            "MemoryAvailable": "654321",
            "MemoryCurrent": "123456",
            "StatusText": "HeptaTrader broker boundary validating",
            "TasksCurrent": "1",
            "WatchdogTimestamp": "Sun 2026-08-02 01:00:00 CST",
            "WatchdogTimestampMonotonic": "999999999",
        }.items():
            self.fixture.manager_unit_all_fields[broker][field] = value
        for unit in DEPLOYER.GATEWAY_BOUNDARY_UNITS[1:]:
            interface = DEPLOYER.SYSTEMD_DBUS_EXECUTION_INTERFACES[unit]
            properties = self.fixture.dbus_interface_properties[unit][interface]
            properties["NAccepted"]["data"] = 9
            properties["NConnections"]["data"] = 4
            properties["NRefused"]["data"] = 5
        receipt = DEPLOYER.deploy()
        self.assertEqual(receipt, self.assert_success_state(self.fixture))

    def test_offline_broker_rejects_pid_or_pending_job(self) -> None:
        for field, value in (
            ("MainPID", "1"),
            ("ExecMainPID", "1"),
            ("ControlPID", "1"),
            ("Job", "123 start"),
        ):
            fixture = Fixture()
            try:
                fixture.broker_offline_fields[field] = value
                with self.subTest(field=field), self.assertRaises(
                        DEPLOYER.DeployError) as raised:
                    DEPLOYER.deploy()
                self.assertEqual(
                    raised.exception.reason,
                    "PROFILE_BROKER_EGRESS_UNIT_NOT_OFFLINE",
                )
                self.assertFalse(fixture.local(DEPLOYER.BACKUP_PATH).exists())
            finally:
                fixture.close()

    def test_offline_broker_inactive_dead_is_accepted(self) -> None:
        self.fixture.broker_offline_fields.update({
            "ActiveState": "inactive", "SubState": "dead",
        })
        receipt = DEPLOYER.deploy()
        self.assertEqual(receipt, self.assert_success_state(self.fixture))

    def test_manager_cache_drift_between_observations_is_rejected(self) -> None:
        changed = False

        def inject_after_first_contract(name: str) -> None:
            nonlocal changed
            if name != "after_gateway_manager_before_masks" or changed:
                return
            changed = True
            unit = DEPLOYER.GATEWAY_SERVICE_UNIT
            properties = self.fixture.dbus_interface_properties[unit][
                DEPLOYER.SYSTEMD_DBUS_UNIT_INTERFACE]
            properties["OnFailure"]["data"] = ["attack.service"]

        with mock.patch.object(
                DEPLOYER, "SEAM_HOOK", inject_after_first_contract):
            self.assert_reason(
                "PROFILE_SYSTEMD_MANAGER_UNIT_CONTRACT_INVALID")
        self.assertTrue(changed)

    def test_proc_stat_and_status_parsers_are_fail_closed(self) -> None:
        pid = DEPLOYER.EXPECTED_BROKER_MAIN_PID
        tail = [
            "S", "1", "1", "1", "0", "-1", "0", "0", "0", "0",
            "0", "1", "2", "3", "4", "20", "0", "1", "0",
            str(DEPLOYER.EXPECTED_BROKER_PROC_STARTTIME_TICKS),
        ]
        valid_stat = (
            f"{pid} (python3) " + " ".join(tail) + "\n").encode("ascii")
        self.assertEqual(
            DEPLOYER.parse_proc_stat(valid_stat, pid),
            (1, DEPLOYER.EXPECTED_BROKER_PROC_STARTTIME_TICKS),
        )
        for mutation in ("parent", "start", "truncated", "malformed"):
            fields = list(tail)
            if mutation == "parent":
                fields[1] = "2"
                payload = f"{pid} (python3) {' '.join(fields)}\n".encode()
            elif mutation == "start":
                fields[19] = str(int(fields[19]) + 1)
                payload = f"{pid} (python3) {' '.join(fields)}\n".encode()
            elif mutation == "truncated":
                payload = f"{pid} (python3) S 1\n".encode()
            else:
                payload = f"{pid} python3 S 1\n".encode()
            with self.subTest(mutation=mutation), self.assertRaises(
                    DEPLOYER.DeployError):
                DEPLOYER.parse_proc_stat(payload, pid)

        status = DEPLOYER.expected_broker_process_status(pid)
        valid_status = "".join(
            f"{key}:\t{value}\n" for key, value in status.items()).encode()
        self.assertEqual(
            DEPLOYER.parse_proc_status(valid_status, pid), status)
        for mutation in ("uid", "caps", "duplicate"):
            payload = valid_status
            if mutation == "uid":
                payload = payload.replace(b"Uid:\t0\t0\t0\t0", b"Uid:\t1\t1\t1\t1")
            elif mutation == "caps":
                payload = payload.replace(
                    b"CapEff:\t0000000000001000",
                    b"CapEff:\t0000000000000000")
            else:
                payload += b"Pid:\t1108253\n"
            with self.subTest(mutation=mutation), self.assertRaises(
                    DEPLOYER.DeployError):
                DEPLOYER.parse_proc_status(payload, pid)

    def test_procfs_directory_identity_ignores_only_live_nlink(self) -> None:
        values = {
            "st_dev": 26,
            "st_ino": 1,
            "st_mode": stat.S_IFDIR | 0o555,
            "st_nlink": 880,
            "st_uid": 0,
            "st_gid": 0,
            "st_size": 0,
            "st_mtime_ns": 123,
            "st_ctime_ns": 456,
        }
        before = mock.Mock(**values)
        after = mock.Mock(**{**values, "st_nlink": 881})
        self.assertEqual(
            DEPLOYER.procfs_directory_identity(before),
            DEPLOYER.procfs_directory_identity(after),
        )
        for field in (
            "st_dev", "st_ino", "st_mode", "st_uid", "st_gid",
            "st_size", "st_mtime_ns", "st_ctime_ns",
        ):
            changed = dict(values)
            changed[field] += 1
            with self.subTest(field=field):
                self.assertNotEqual(
                    DEPLOYER.procfs_directory_identity(before),
                    DEPLOYER.procfs_directory_identity(mock.Mock(**changed)),
                )

        with self.assertRaises(DEPLOYER.DeployError) as raised:
            DEPLOYER.open_anchored_directory(Path("/tmp"), procfs=True)
        self.assertEqual(
            raised.exception.reason, "PROFILE_INTERNAL_PATH_INVALID")

    def test_fabricated_broker_pid_is_rejected_before_proc_open(self) -> None:
        with self.assertRaises(DEPLOYER.DeployError) as raised:
            DEPLOYER.open_broker_process(2147483646)
        self.assertEqual(
            raised.exception.reason,
            "PROFILE_BROKER_EGRESS_PROCESS_INVALID")

    def test_broker_process_double_snapshot_drift_is_rejected(self) -> None:
        broker = DEPLOYER.broker_unit_state()
        before = json.loads(json.dumps(self.fixture.broker_process_evidence))
        after = json.loads(json.dumps(before))
        after["interpreter"]["sha256"] = "sha256:" + "0" * 64
        pidfd, writer = os.pipe()
        process_descriptor = os.open(self.fixture.root, os.O_RDONLY)
        try:
            with (
                mock.patch.object(
                    DEPLOYER, "open_broker_process",
                    return_value=(pidfd, process_descriptor)),
                mock.patch.object(
                    DEPLOYER, "broker_process_snapshot",
                    side_effect=[before, after]),
                mock.patch.object(
                    DEPLOYER, "execute_verified_broker_egress_check",
                    return_value=completed([], stdout=self.fixture.broker_stdout)),
                mock.patch.object(
                    DEPLOYER, "broker_unit_state", return_value=broker),
                self.assertRaises(DEPLOYER.DeployError) as raised,
            ):
                REAL_GUARDED_BROKER_EGRESS_CHECK(broker)
            self.assertEqual(
                raised.exception.reason,
                "PROFILE_BROKER_EGRESS_PROCESS_INVALID")
            pidfd = -1
            process_descriptor = -1
        finally:
            if process_descriptor >= 0:
                os.close(process_descriptor)
            if pidfd >= 0:
                os.close(pidfd)
            os.close(writer)

    def test_broker_final_systemd_identity_drift_is_rejected(self) -> None:
        broker = DEPLOYER.broker_unit_state()
        restarted = dict(broker)
        restarted["InvocationID"] = "0" * 32
        evidence = json.loads(json.dumps(self.fixture.broker_process_evidence))
        pidfd, writer = os.pipe()
        process_descriptor = os.open(self.fixture.root, os.O_RDONLY)
        try:
            with (
                mock.patch.object(
                    DEPLOYER, "open_broker_process",
                    return_value=(pidfd, process_descriptor)),
                mock.patch.object(
                    DEPLOYER, "broker_process_snapshot",
                    side_effect=[evidence, evidence]),
                mock.patch.object(
                    DEPLOYER, "execute_verified_broker_egress_check",
                    return_value=completed([], stdout=self.fixture.broker_stdout)),
                mock.patch.object(
                    DEPLOYER, "broker_unit_state", return_value=restarted),
                self.assertRaises(DEPLOYER.DeployError) as raised,
            ):
                REAL_GUARDED_BROKER_EGRESS_CHECK(broker)
            self.assertEqual(
                raised.exception.reason,
                "PROFILE_BROKER_EGRESS_PROCESS_INVALID")
            pidfd = -1
            process_descriptor = -1
        finally:
            if process_descriptor >= 0:
                os.close(process_descriptor)
            if pidfd >= 0:
                os.close(pidfd)
            os.close(writer)

    def test_systemd_manager_version_and_unit_path_are_exact(self) -> None:
        mutations = {
            "version": (
                "manager_version", "257.1-unreviewed"),
            "features": (
                "manager_features", DEPLOYER.EXPECTED_SYSTEMD_FEATURES
                + " +UNREVIEWED"),
            "extra-root": (
                "manager_unit_path",
                DEPLOYER.EXPECTED_SYSTEMD_UNIT_PATH
                + " /opt/unreviewed/systemd/system"),
            "missing-root": (
                "manager_unit_path",
                " ".join(str(path) for path in
                         DEPLOYER.SYSTEMD_UNIT_SEARCH_ROOTS[:-1])),
            "reordered-roots": (
                "manager_unit_path",
                " ".join(str(path) for path in reversed(
                    DEPLOYER.SYSTEMD_UNIT_SEARCH_ROOTS))),
            "environment": (
                "manager_environment",
                DEPLOYER.EXPECTED_SYSTEMD_MANAGER_ENVIRONMENT
                + " PYTHONPATH=/tmp/attack"),
        }
        for name, (attribute, value) in mutations.items():
            fixture = Fixture()
            try:
                setattr(fixture, attribute, value)
                with self.subTest(name=name), self.assertRaises(
                        DEPLOYER.DeployError) as raised:
                    DEPLOYER.deploy()
                self.assertEqual(
                    raised.exception.reason,
                    "PROFILE_SYSTEMD_MANAGER_INVALID")
                self.assertFalse(fixture.local(DEPLOYER.BACKUP_PATH).exists())
            finally:
                fixture.close()

    def test_gateway_unit_closure_files_are_exact(self) -> None:
        for label in DEPLOYER.GATEWAY_UNIT_CLOSURE:
            for mutation in ("payload", "mode", "hardlink", "symlink"):
                fixture = Fixture()
                try:
                    path = fixture.local(
                        DEPLOYER.GATEWAY_UNIT_CLOSURE[label]["path"])
                    if mutation == "payload":
                        path.write_bytes(path.read_bytes() + b"drift\n")
                    elif mutation == "mode":
                        expected_mode = DEPLOYER.GATEWAY_UNIT_CLOSURE[label][
                            "mode"]
                        os.chmod(
                            path, 0o644 if expected_mode != 0o644 else 0o600)
                    elif mutation == "hardlink":
                        os.link(path, path.with_name(path.name + ".alias"))
                    else:
                        preserved = path.with_name(path.name + ".preserved")
                        path.rename(preserved)
                        path.symlink_to(preserved.name)
                    with self.subTest(label=label, mutation=mutation), \
                            self.assertRaises(DEPLOYER.DeployError) as raised:
                        DEPLOYER.deploy()
                    self.assertEqual(
                        raised.exception.reason,
                        "PROFILE_GATEWAY_UNIT_CLOSURE_INVALID")
                    self.assertFalse(
                        fixture.local(DEPLOYER.BACKUP_PATH).exists())
                finally:
                    fixture.close()

    def test_gateway_dropin_inventory_rejects_extras(self) -> None:
        for location in ("vendor", "type-wide", "instance-prefix"):
            fixture = Fixture()
            try:
                if location == "vendor":
                    extra = (
                        DEPLOYER.GATEWAY_SERVICE_DROPIN_DIRECTORY
                        / "20-extra.conf")
                elif location == "type-wide":
                    extra = Path(
                        "/etc/systemd/system/service.d/20-extra.conf")
                else:
                    extra = Path(
                        "/etc/systemd/system/"
                        "hepta-tool-@alpha.service.d/20-extra.conf")
                fixture.write_file(extra, b"[Service]\n", 0o644)
                with self.subTest(location=location), self.assertRaises(
                        DEPLOYER.DeployError) as raised:
                    DEPLOYER.deploy()
                self.assertEqual(
                    raised.exception.reason,
                    "PROFILE_GATEWAY_UNIT_CLOSURE_INVALID")
                self.assertFalse(fixture.local(DEPLOYER.BACKUP_PATH).exists())
            finally:
                fixture.close()

    def test_gateway_dependency_directories_are_rejected(self) -> None:
        directories = (
            Path(
                "/etc/systemd/system/"
                "hepta-tool-@alpha.service.d"),
            Path(
                "/etc/systemd/system/"
                "hepta-tool-@alpha.service.wants"),
            Path(
                "/run/systemd/system/"
                "hepta-tool-gateway@.service.requires"),
            Path(
                "/usr/lib/systemd/system/"
                "hepta-tool-session-@alpha.socket.upholds"),
            Path(
                "/etc/systemd/system/"
                "hepta-broker-egress-policy.service.d"),
            Path(
                "/run/systemd/system/"
                "hepta-broker-egress-.service.wants"),
            Path(
                "/usr/lib/systemd/system/"
                "hepta-broker-.service.requires"),
        )
        for directory in directories:
            fixture = Fixture()
            try:
                fixture.make_directory(directory)
                with self.subTest(directory=directory), self.assertRaises(
                        DEPLOYER.DeployError) as raised:
                    DEPLOYER.deploy()
                self.assertEqual(
                    raised.exception.reason,
                    "PROFILE_GATEWAY_UNIT_CLOSURE_INVALID")
                self.assertFalse(fixture.local(DEPLOYER.BACKUP_PATH).exists())
            finally:
                fixture.close()

    def test_gateway_alternative_unit_fragments_are_rejected(self) -> None:
        fragments = (
            Path(
                "/run/systemd/transient/"
                "hepta-tool-gateway@alpha.service"),
            Path(
                "/etc/systemd/system/hepta-tool-gateway@.service"),
            Path(
                "/usr/lib/systemd/system/"
                "hepta-tool-gateway@alpha.service"),
            Path(
                "/etc/systemd/system/"
                "hepta-broker-egress-policy.service"),
            Path(
                "/run/systemd/transient/"
                "hepta-broker-egress-policy.service"),
        )
        for fragment in fragments:
            fixture = Fixture()
            try:
                fixture.write_file(fragment, b"[Unit]\n", 0o644)
                with self.subTest(fragment=fragment), self.assertRaises(
                        DEPLOYER.DeployError) as raised:
                    DEPLOYER.deploy()
                self.assertEqual(
                    raised.exception.reason,
                    "PROFILE_GATEWAY_UNIT_CLOSURE_INVALID")
                self.assertFalse(fixture.local(DEPLOYER.BACKUP_PATH).exists())
            finally:
                fixture.close()

    def test_gateway_direct_and_chained_unit_aliases_are_rejected(self) -> None:
        cases = (
            (
                "template",
                (("gateway-alias@.service",
                  "hepta-tool-gateway@.service"),),
            ),
            (
                "instance",
                (("gateway-alias@alpha.service",
                  "hepta-tool-gateway@alpha.service"),),
            ),
            (
                "chain",
                (("gateway-alias-a.service", "gateway-alias-b.service"),
                 ("gateway-alias-b.service",
                  "hepta-tool-gateway@alpha.service")),
            ),
            (
                "broker-direct",
                (("broker-alias.service",
                  "hepta-broker-egress-policy.service"),),
            ),
            (
                "broker-chain",
                (("broker-alias-a.service", "broker-alias-b.service"),
                 ("broker-alias-b.service",
                  "hepta-broker-egress-policy.service")),
            ),
        )
        for name, aliases in cases:
            fixture = Fixture()
            try:
                root = fixture.local(Path("/etc/systemd/system"))
                for alias, target in aliases:
                    (root / alias).symlink_to(target)
                with self.subTest(name=name), self.assertRaises(
                        DEPLOYER.DeployError) as raised:
                    DEPLOYER.deploy()
                self.assertEqual(
                    raised.exception.reason,
                    "PROFILE_GATEWAY_UNIT_CLOSURE_INVALID")
                self.assertFalse(fixture.local(DEPLOYER.BACKUP_PATH).exists())
            finally:
                fixture.close()

    def test_gateway_dropin_symlinked_parent_is_rejected(self) -> None:
        parent = self.fixture.local(
            DEPLOYER.GATEWAY_SERVICE_DROPIN_DIRECTORY)
        moved = parent.with_name(parent.name + ".preserved")
        parent.rename(moved)
        parent.symlink_to(moved.name, target_is_directory=True)
        self.assert_reason("PROFILE_GATEWAY_UNIT_CLOSURE_INVALID")
        self.assertFalse(self.fixture.local(DEPLOYER.BACKUP_PATH).exists())

    def test_gateway_unit_root_listing_race_is_rejected(self) -> None:
        raced = False
        seam = (
            "after_gateway_dropin_root_first_listing:"
            "/etc/systemd/system")

        def add_directory(name: str) -> None:
            nonlocal raced
            if name != seam or raced:
                return
            raced = True
            self.fixture.make_directory(Path(
                "/etc/systemd/system/"
                "hepta-tool-@alpha.service.requires"))

        with mock.patch.object(DEPLOYER, "SEAM_HOOK", add_directory):
            self.assert_reason("PROFILE_GATEWAY_UNIT_CLOSURE_INVALID")
        self.assertTrue(raced)
        self.assertFalse(self.fixture.local(DEPLOYER.BACKUP_PATH).exists())

    def test_gateway_expected_dropin_listing_race_is_rejected(self) -> None:
        raced = False

        def add_dropin(name: str) -> None:
            nonlocal raced
            if name != "after_gateway_dropin_directory_first_listing" or raced:
                return
            raced = True
            self.fixture.write_file(
                DEPLOYER.GATEWAY_SERVICE_DROPIN_DIRECTORY / "20-raced.conf",
                b"[Unit]\n", 0o644)

        with mock.patch.object(DEPLOYER, "SEAM_HOOK", add_dropin):
            self.assert_reason("PROFILE_GATEWAY_UNIT_CLOSURE_INVALID")
        self.assertTrue(raced)
        self.assertFalse(self.fixture.local(DEPLOYER.BACKUP_PATH).exists())

    def test_gateway_systemd_search_root_rebind_is_rejected(self) -> None:
        root = self.fixture.local(Path("/etc/systemd/system"))
        preserved = root.with_name("system-preserved")
        raced = False
        seam = (
            "after_gateway_dropin_root_first_listing:"
            "/etc/systemd/system")

        def replace_root(name: str) -> None:
            nonlocal raced
            if name != seam or raced:
                return
            raced = True
            root.rename(preserved)
            root.mkdir(mode=0o755)

        with mock.patch.object(DEPLOYER, "SEAM_HOOK", replace_root):
            self.assert_reason("PROFILE_GATEWAY_UNIT_CLOSURE_INVALID")
        self.assertTrue(raced)
        self.assertFalse(self.fixture.local(DEPLOYER.BACKUP_PATH).exists())

    def test_gateway_expected_dropin_directory_rebind_is_rejected(self) -> None:
        directory = self.fixture.local(
            DEPLOYER.GATEWAY_SERVICE_DROPIN_DIRECTORY)
        preserved = directory.with_name(directory.name + ".preserved")
        payload = self.fixture.local(
            DEPLOYER.GATEWAY_SERVICE_DROPIN_PATH).read_bytes()
        raced = False

        def replace_directory(name: str) -> None:
            nonlocal raced
            if name != "after_gateway_dropin_directory_first_listing" or raced:
                return
            raced = True
            directory.rename(preserved)
            directory.mkdir(mode=0o755)
            replacement = directory / DEPLOYER.GATEWAY_SERVICE_DROPIN_PATH.name
            replacement.write_bytes(payload)
            os.chmod(replacement, 0o644)

        with mock.patch.object(DEPLOYER, "SEAM_HOOK", replace_directory):
            self.assert_reason("PROFILE_GATEWAY_UNIT_CLOSURE_INVALID")
        self.assertTrue(raced)
        self.assertFalse(self.fixture.local(DEPLOYER.BACKUP_PATH).exists())

    def test_gateway_closure_replacement_around_manager_query_is_rejected(
            self) -> None:
        for seam in (
            "after_gateway_masks_before_manager",
            "after_gateway_manager_before_masks",
        ):
            fixture = Fixture()
            try:
                specification = DEPLOYER.GATEWAY_UNIT_CLOSURE[
                    "gateway_service_broker_dropin"]
                path = fixture.local(specification["path"])
                payload = path.read_bytes()
                preserved = path.with_name(path.name + ".preserved")
                raced = False

                def replace_closure(name: str) -> None:
                    nonlocal raced
                    if name != seam or raced:
                        return
                    raced = True
                    path.rename(preserved)
                    path.write_bytes(payload)
                    os.chmod(path, 0o644)

                with self.subTest(seam=seam), mock.patch.object(
                        DEPLOYER, "SEAM_HOOK", replace_closure), \
                        self.assertRaises(DEPLOYER.DeployError) as raised:
                    DEPLOYER.deploy()
                self.assertEqual(
                    raised.exception.reason,
                    "PROFILE_GATEWAY_UNIT_CLOSURE_INVALID")
                self.assertTrue(raced)
                self.assertFalse(fixture.local(DEPLOYER.BACKUP_PATH).exists())
            finally:
                fixture.close()

    def test_gateway_extra_unit_entry_races_around_manager_are_rejected(
            self) -> None:
        entries = (
            Path(
                "/etc/systemd/system/"
                "hepta-tool-@alpha.service.d/20-raced.conf"),
            Path(
                "/run/systemd/transient/"
                "hepta-tool-gateway@alpha.service"),
            Path(
                "/etc/systemd/system/"
                "hepta-tool-@alpha.service.wants/unexpected.service"),
            Path(
                "/etc/systemd/system/"
                "hepta-broker-egress-policy.service"),
            Path(
                "/etc/systemd/system/"
                "hepta-broker-egress-policy.service.d/90-raced.conf"),
        )
        for entry in entries:
            fixture = Fixture()
            try:
                raced = False

                def add_entry(name: str) -> None:
                    nonlocal raced
                    if (
                        name != "after_gateway_manager_before_masks"
                        or raced
                    ):
                        return
                    raced = True
                    fixture.write_file(entry, b"[Unit]\n", 0o644)

                with self.subTest(entry=entry), mock.patch.object(
                        DEPLOYER, "SEAM_HOOK", add_entry), \
                        self.assertRaises(DEPLOYER.DeployError) as raised:
                    DEPLOYER.deploy()
                self.assertEqual(
                    raised.exception.reason,
                    "PROFILE_GATEWAY_UNIT_CLOSURE_INVALID")
                self.assertTrue(raced)
                self.assertFalse(fixture.local(DEPLOYER.BACKUP_PATH).exists())
            finally:
                fixture.close()

    def test_gateway_alias_and_alias_dropin_race_is_rejected(self) -> None:
        fixture = Fixture()
        raced = False

        def add_alias(name: str) -> None:
            nonlocal raced
            if name != "after_gateway_manager_before_masks" or raced:
                return
            raced = True
            root = fixture.local(Path("/etc/systemd/system"))
            (root / "gateway-alias@.service").symlink_to(
                "hepta-tool-gateway@.service")
            fixture.write_file(
                Path(
                    "/etc/systemd/system/"
                    "gateway-alias@.service.d/20-raced.conf"),
                b"[Unit]\nWants=unexpected.service\n", 0o644)

        try:
            with mock.patch.object(
                    DEPLOYER, "SEAM_HOOK", add_alias), self.assertRaises(
                        DEPLOYER.DeployError) as raised:
                DEPLOYER.deploy()
            self.assertEqual(
                raised.exception.reason,
                "PROFILE_GATEWAY_UNIT_CLOSURE_INVALID")
            self.assertTrue(raced)
            self.assertFalse(fixture.local(DEPLOYER.BACKUP_PATH).exists())
        finally:
            fixture.close()

    def test_gateway_manager_drift_between_full_observations_is_rejected(
            self) -> None:
        observations = 0

        def drift_after_first_manager(name: str) -> None:
            nonlocal observations
            if name != "after_gateway_manager_before_masks":
                return
            observations += 1
            if observations == 1:
                self.fixture.gateway_after[
                    DEPLOYER.GATEWAY_SERVICE_UNIT] = "network.target"

        with mock.patch.object(
                DEPLOYER, "SEAM_HOOK", drift_after_first_manager):
            self.assert_reason("PROFILE_GATEWAY_BOUNDARY_NOT_STOPPED")
        self.assertGreaterEqual(observations, 1)
        self.assertFalse(self.fixture.local(DEPLOYER.BACKUP_PATH).exists())

    def test_gateway_closure_in_read_inode_races_are_rejected(self) -> None:
        for label in ("gateway_service_template", "broker_egress_helper"):
            for stage in ("before_open", "open", "read", "final_stat"):
                fixture = Fixture()
                try:
                    specification = DEPLOYER.GATEWAY_UNIT_CLOSURE[label]
                    path = fixture.local(specification["path"])
                    payload = path.read_bytes()
                    preserved = path.with_name(path.name + ".preserved")
                    seam = f"after_gateway_unit_closure:{label}_{stage}"
                    raced = False

                    def replace_closure(name: str) -> None:
                        nonlocal raced
                        if name != seam or raced:
                            return
                        raced = True
                        path.rename(preserved)
                        path.write_bytes(payload)
                        os.chmod(path, specification["mode"])

                    with self.subTest(label=label, stage=stage), \
                            mock.patch.object(
                                DEPLOYER, "SEAM_HOOK", replace_closure), \
                            self.assertRaises(DEPLOYER.DeployError) as raised:
                        DEPLOYER.deploy()
                    self.assertEqual(
                        raised.exception.reason,
                        "PROFILE_GATEWAY_UNIT_CLOSURE_INVALID")
                    self.assertTrue(raced)
                    self.assertFalse(
                        fixture.local(DEPLOYER.BACKUP_PATH).exists())
                finally:
                    fixture.close()

    def test_broker_helper_execution_is_bound_to_verified_inode(self) -> None:
        path = self.fixture.local(DEPLOYER.BROKER_EGRESS_POLICY_PATH)
        preserved = path.with_name(path.name + ".preserved")
        raced = False

        def replace_helper(name: str) -> None:
            nonlocal raced
            if name != "after_broker_egress_exec_verified" or raced:
                return
            raced = True
            path.rename(preserved)
            path.write_bytes(b"#!/bin/sh\nexit 0\n")
            os.chmod(path, 0o755)

        with mock.patch.object(
                DEPLOYER, "SEAM_HOOK", replace_helper), self.assertRaises(
                    DEPLOYER.DeployError) as raised:
            DEPLOYER.deploy()
        self.assertEqual(
            raised.exception.reason,
            "PROFILE_GATEWAY_UNIT_CLOSURE_INVALID")
        self.assertTrue(raced)
        self.assertFalse(self.fixture.local(DEPLOYER.BACKUP_PATH).exists())

    def test_broker_helper_interpreter_path_race_is_rejected(self) -> None:
        path = self.fixture.local(DEPLOYER.BROKER_INTERPRETER_PATH)
        preserved = path.with_name(path.name + ".preserved")
        raced = False

        def replace_interpreter(name: str) -> None:
            nonlocal raced
            if name != "after_broker_egress_interpreter_verified" or raced:
                return
            raced = True
            path.rename(preserved)
            path.write_bytes(b"replacement interpreter\n")
            os.chmod(path, 0o755)

        with mock.patch.object(
                DEPLOYER, "SEAM_HOOK", replace_interpreter), self.assertRaises(
                    DEPLOYER.DeployError) as raised:
            DEPLOYER.deploy()
        self.assertEqual(
            raised.exception.reason,
            "PROFILE_GATEWAY_UNIT_CLOSURE_INVALID",
        )
        self.assertTrue(raced)
        self.assertFalse(self.fixture.local(DEPLOYER.BACKUP_PATH).exists())

    def test_gateway_mask_missing_is_rejected(self) -> None:
        unit = DEPLOYER.GATEWAY_BOUNDARY_UNITS[0]
        self.fixture.local(DEPLOYER.RUNTIME_MASK_ROOT / unit).unlink()
        self.assert_reason("PROFILE_GATEWAY_MASK_INVALID")
        self.assertFalse(self.fixture.local(DEPLOYER.BACKUP_PATH).exists())

    def test_gateway_mask_wrong_target_is_rejected(self) -> None:
        unit = DEPLOYER.GATEWAY_BOUNDARY_UNITS[0]
        mask = self.fixture.local(DEPLOYER.PERSISTENT_MASK_ROOT / unit)
        mask.unlink()
        mask.symlink_to("/dev/zero")
        self.assert_reason("PROFILE_GATEWAY_MASK_INVALID")
        self.assertFalse(self.fixture.local(DEPLOYER.BACKUP_PATH).exists())

    def test_gateway_mask_target_is_read_from_open_descriptor(self) -> None:
        real_readlink = os.readlink
        calls: list[tuple[object, object]] = []

        def tracked_readlink(
            path: object,
            *,
            dir_fd: int | None = None,
        ) -> str:
            calls.append((path, dir_fd))
            return real_readlink(path, dir_fd=dir_fd)

        with mock.patch.object(
                DEPLOYER.os, "readlink", side_effect=tracked_readlink):
            DEPLOYER.deploy()
        self.assertTrue(calls)
        self.assertTrue(all(
            path == "" and isinstance(descriptor, int) and descriptor >= 0
            for path, descriptor in calls))

    def test_gateway_mask_wrong_target_aba_is_rejected(self) -> None:
        unit = DEPLOYER.GATEWAY_BOUNDARY_UNITS[0]
        mask = self.fixture.local(DEPLOYER.PERSISTENT_MASK_ROOT / unit)
        preserved = mask.with_name(mask.name + ".preserved")
        mask.unlink()
        mask.symlink_to("/dev/zero")
        swapped = False
        restored = False

        def exchange_around_readlink(name: str) -> None:
            nonlocal swapped, restored
            if name == f"after_gateway_mask_open:persistent:{unit}":
                mask.rename(preserved)
                mask.symlink_to(DEPLOYER.MASK_TARGET)
                swapped = True
            elif (
                name == f"after_gateway_mask_readlink:persistent:{unit}"
                and swapped
                and not restored
            ):
                mask.unlink()
                preserved.rename(mask)
                restored = True

        with mock.patch.object(
                DEPLOYER, "SEAM_HOOK", exchange_around_readlink):
            self.assert_reason("PROFILE_GATEWAY_MASK_INVALID")
        self.assertTrue(swapped)
        self.assertTrue(restored)
        self.assertEqual(os.readlink(mask), "/dev/zero")
        self.assertFalse(self.fixture.local(DEPLOYER.BACKUP_PATH).exists())

    def test_gateway_mask_regular_entry_is_rejected(self) -> None:
        unit = DEPLOYER.GATEWAY_BOUNDARY_UNITS[0]
        mask = self.fixture.local(DEPLOYER.RUNTIME_MASK_ROOT / unit)
        mask.unlink()
        mask.write_bytes(DEPLOYER.MASK_TARGET.encode("ascii"))
        os.chmod(mask, 0o644)
        self.assert_reason("PROFILE_GATEWAY_MASK_INVALID")
        self.assertFalse(self.fixture.local(DEPLOYER.BACKUP_PATH).exists())

    def test_gateway_mask_relative_target_and_hardlink_are_rejected(self) -> None:
        for mutation in ("relative", "hardlink"):
            fixture = Fixture()
            try:
                unit = DEPLOYER.GATEWAY_BOUNDARY_UNITS[0]
                mask = fixture.local(DEPLOYER.PERSISTENT_MASK_ROOT / unit)
                if mutation == "relative":
                    mask.unlink()
                    mask.symlink_to("../../../../dev/null")
                else:
                    os.link(
                        mask, mask.with_name(mask.name + ".alias"),
                        follow_symlinks=False)
                with self.subTest(mutation=mutation), self.assertRaises(
                        DEPLOYER.DeployError) as raised:
                    DEPLOYER.deploy()
                self.assertEqual(
                    raised.exception.reason, "PROFILE_GATEWAY_MASK_INVALID")
                self.assertFalse(fixture.local(DEPLOYER.BACKUP_PATH).exists())
            finally:
                fixture.close()

    def test_gateway_mask_symlinked_parent_is_rejected(self) -> None:
        parent = self.fixture.local(DEPLOYER.PERSISTENT_MASK_ROOT)
        moved = parent.with_name("system-preserved")
        parent.rename(moved)
        parent.symlink_to(moved.name, target_is_directory=True)
        self.assert_reason("PROFILE_GATEWAY_MASK_INVALID")
        self.assertFalse(self.fixture.local(DEPLOYER.BACKUP_PATH).exists())

    def test_gateway_mask_inode_replacement_races_are_rejected(self) -> None:
        stages = ("before_open", "open", "readlink", "final_stat")
        for stage in stages:
            fixture = Fixture()
            try:
                unit = DEPLOYER.GATEWAY_BOUNDARY_UNITS[0]
                mask = fixture.local(DEPLOYER.PERSISTENT_MASK_ROOT / unit)
                preserved = mask.with_name(mask.name + ".preserved")
                raced = False
                seam = f"after_gateway_mask_{stage}:persistent:{unit}"

                def replace_mask(name: str) -> None:
                    nonlocal raced
                    if name != seam or raced:
                        return
                    raced = True
                    mask.rename(preserved)
                    mask.symlink_to(DEPLOYER.MASK_TARGET)

                with self.subTest(stage=stage), mock.patch.object(
                        DEPLOYER, "SEAM_HOOK", replace_mask), \
                        self.assertRaises(DEPLOYER.DeployError) as raised:
                    DEPLOYER.deploy()
                self.assertEqual(
                    raised.exception.reason, "PROFILE_GATEWAY_MASK_INVALID")
                self.assertTrue(raced)
                self.assertTrue(preserved.is_symlink())
                self.assertEqual(os.readlink(preserved), DEPLOYER.MASK_TARGET)
                self.assertTrue(mask.is_symlink())
                self.assertEqual(os.readlink(mask), DEPLOYER.MASK_TARGET)
                self.assertFalse(fixture.local(DEPLOYER.BACKUP_PATH).exists())
            finally:
                fixture.close()

    def test_gateway_mask_replacement_around_manager_query_is_rejected(
            self) -> None:
        for seam in (
            "after_gateway_masks_before_manager",
            "after_gateway_manager_before_masks",
        ):
            fixture = Fixture()
            try:
                unit = DEPLOYER.GATEWAY_BOUNDARY_UNITS[0]
                mask = fixture.local(DEPLOYER.RUNTIME_MASK_ROOT / unit)
                preserved = mask.with_name(mask.name + ".preserved")
                raced = False

                def replace_mask(name: str) -> None:
                    nonlocal raced
                    if name != seam or raced:
                        return
                    raced = True
                    mask.rename(preserved)
                    mask.symlink_to(DEPLOYER.MASK_TARGET)

                with self.subTest(seam=seam), mock.patch.object(
                        DEPLOYER, "SEAM_HOOK", replace_mask), \
                        self.assertRaises(DEPLOYER.DeployError) as raised:
                    DEPLOYER.deploy()
                self.assertEqual(
                    raised.exception.reason, "PROFILE_GATEWAY_MASK_INVALID")
                self.assertTrue(raced)
                self.assertFalse(fixture.local(DEPLOYER.BACKUP_PATH).exists())
            finally:
                fixture.close()

    def test_gateway_unmasked_inactive_state_is_rejected(self) -> None:
        self.fixture.gateway_load_state = "loaded"
        self.fixture.gateway_unit_file_state = "disabled"
        self.fixture.gateway_fragment_path = (
            "/usr/lib/systemd/system/hepta-tool-gateway@.service")
        self.assert_reason("PROFILE_GATEWAY_BOUNDARY_NOT_STOPPED")
        self.assertFalse(self.fixture.local(DEPLOYER.BACKUP_PATH).exists())

    def test_lock_metadata_and_aliases_are_rejected(self) -> None:
        lock = self.fixture.write_file(DEPLOYER.LOCK_PATH, b"", 0o644)
        self.assert_reason("PROFILE_LOCK_INVALID")
        os.chmod(lock, 0o600)
        os.link(lock, lock.with_name("lock.alias"))
        self.assert_reason("PROFILE_LOCK_INVALID")
        self.assertFalse(self.fixture.local(DEPLOYER.BACKUP_PATH).exists())

    def test_lock_symlink_is_rejected(self) -> None:
        lock = self.fixture.local(DEPLOYER.LOCK_PATH)
        other = lock.with_name("other.lock")
        other.write_bytes(b"")
        os.chmod(other, 0o600)
        lock.symlink_to(other.name)
        self.assert_reason("PROFILE_LOCK_INVALID")
        self.assertFalse(self.fixture.local(DEPLOYER.BACKUP_PATH).exists())

    def test_busy_lock_is_rejected_without_mutation(self) -> None:
        lock = self.fixture.write_file(DEPLOYER.LOCK_PATH, b"", 0o600)
        descriptor = os.open(lock, os.O_RDWR)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assert_reason("PROFILE_LOCK_BUSY")
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)
        self.assertFalse(self.fixture.local(DEPLOYER.BACKUP_PATH).exists())

    def test_lock_inode_replacement_is_rejected_before_mutation(self) -> None:
        lock = self.fixture.local(DEPLOYER.LOCK_PATH)

        def replace_lock(name: str) -> None:
            if name != "after_lock_acquired":
                return
            replacement = lock.with_name("replacement.lock")
            replacement.write_bytes(b"")
            os.chmod(replacement, 0o600)
            os.replace(replacement, lock)

        with mock.patch.object(DEPLOYER, "SEAM_HOOK", replace_lock):
            self.assert_reason("PROFILE_LOCK_REBOUND")
        self.assertFalse(self.fixture.local(DEPLOYER.BACKUP_PATH).exists())

    def test_lock_rebound_at_backup_publish_allows_only_second_helper(self) -> None:
        second = self.exercise_lock_rebound_with_second_helper(
            self.fixture, "after_backup_publish_rename")
        self.assertEqual(second[0][0], "PASS")
        self.assertEqual(second[0][1], self.assert_success_state(self.fixture))

    def test_lock_rebound_at_target_exchange_preserves_retry_state(self) -> None:
        second = self.exercise_lock_rebound_with_second_helper(
            self.fixture, "after_target_exchange")
        self.assertEqual(
            second, [("ERROR", "PROFILE_RECOVERED_POST_REPLACE_CRASH")])
        self.assert_p3_state(self.fixture)
        receipt = DEPLOYER.deploy()
        self.assertEqual(receipt, self.assert_success_state(self.fixture))

    def test_lock_rebound_at_rollback_exchange_preserves_one_winner(self) -> None:
        second = self.exercise_lock_rebound_with_second_helper(
            self.fixture, "after_rollback_exchange", induce_rollback=True)
        self.assertEqual(second[0][0], "PASS")
        self.assertEqual(second[0][1], self.assert_success_state(self.fixture))

    def test_lock_rebound_at_receipt_stage_preserves_commit_intent(self) -> None:
        second = self.exercise_lock_rebound_with_second_helper(
            self.fixture, "after_receipt_temp_fsync")
        self.assertEqual(second[0][0], "PASS")
        self.assertEqual(second[0][1], self.assert_success_state(self.fixture))

    def test_lock_rebound_at_receipt_commit_preserves_success(self) -> None:
        second = self.exercise_lock_rebound_with_second_helper(
            self.fixture, "after_receipt_commit_rename")
        self.assertEqual(second[0][0], "PASS")
        self.assertEqual(second[0][1], self.assert_success_state(self.fixture))

    def test_hard_crash_before_backup_publication_is_recovered(self) -> None:
        self.hard_crash_at("after_backup_temp_fsync")
        self.assert_reason("PROFILE_RECOVERED_PRE_PUBLISH_CRASH")
        self.assertEqual(
            self.fixture.local(DEPLOYER.BACKUP_PATH).read_bytes(),
            DEPLOYER.OLD_PAYLOAD)
        receipt = DEPLOYER.deploy()
        self.assertEqual(receipt, self.assert_success_state(self.fixture))

    def test_exact_backup_stage_is_refsynced_after_in_fsync_crash(self) -> None:
        self.crash_during_fsync_after_seam(
            "before_backup_temp_file_fsync")
        staged = self.fixture.local(DEPLOYER.BACKUP_TEMP_PATH)
        self.assertEqual(staged.read_bytes(), DEPLOYER.OLD_PAYLOAD)
        self.assertFalse(self.fixture.local(DEPLOYER.BACKUP_PATH).exists())
        self.assert_fsync_failure_at(
            "before_backup_recovery_publish_source_file_fsync",
            "PROFILE_RECOVERY_BACKUP_TEMP_DRIFT")
        self.assertEqual(staged.read_bytes(), DEPLOYER.OLD_PAYLOAD)
        self.assertFalse(self.fixture.local(DEPLOYER.BACKUP_PATH).exists())
        self.assert_fsync_failure_at(
            "before_backup_recovery_publish_source_parent_fsync",
            "PROFILE_RECOVERY_BACKUP_TEMP_DRIFT")
        self.assertEqual(staged.read_bytes(), DEPLOYER.OLD_PAYLOAD)
        self.assertFalse(self.fixture.local(DEPLOYER.BACKUP_PATH).exists())
        self.assert_reason("PROFILE_RECOVERED_PRE_PUBLISH_CRASH")
        receipt = DEPLOYER.deploy()
        self.assertEqual(receipt, self.assert_success_state(self.fixture))

    def test_exact_target_stage_is_refsynced_after_in_fsync_crash(self) -> None:
        self.crash_during_fsync_after_seam(
            "before_target_temp_file_fsync")
        staged = self.fixture.local(DEPLOYER.TARGET_TEMP_PATH)
        self.assertEqual(staged.read_bytes(), DEPLOYER.NEW_PAYLOAD)
        self.assertEqual(
            self.fixture.local(DEPLOYER.TARGET_PATH).read_bytes(),
            DEPLOYER.OLD_PAYLOAD)
        self.assert_fsync_failure_at(
            "before_target_candidate_file_fsync",
            "PROFILE_TARGET_TEMP_INVALID")
        self.assertEqual(staged.read_bytes(), DEPLOYER.NEW_PAYLOAD)
        self.assertEqual(
            self.fixture.local(DEPLOYER.TARGET_PATH).read_bytes(),
            DEPLOYER.OLD_PAYLOAD)
        self.assert_fsync_failure_at(
            "before_target_candidate_parent_fsync",
            "PROFILE_TARGET_TEMP_INVALID")
        self.assertEqual(staged.read_bytes(), DEPLOYER.NEW_PAYLOAD)
        self.assertEqual(
            self.fixture.local(DEPLOYER.TARGET_PATH).read_bytes(),
            DEPLOYER.OLD_PAYLOAD)
        receipt = DEPLOYER.deploy()
        self.assertEqual(receipt, self.assert_success_state(self.fixture))

    def test_exact_receipt_stage_is_refsynced_after_in_fsync_crash(self) -> None:
        self.crash_during_fsync_after_seam(
            "before_receipt_temp_file_fsync")
        staged = self.fixture.local(DEPLOYER.RECEIPT_TEMP_PATH)
        DEPLOYER.validate_receipt(DEPLOYER.read_anchored_file(
            DEPLOYER.RECEIPT_TEMP_PATH, "test-invalid"))
        self.assertEqual(
            self.fixture.local(DEPLOYER.TARGET_PATH).read_bytes(),
            DEPLOYER.NEW_PAYLOAD)
        self.assert_fsync_failure_at(
            "before_receipt_commit_source_file_fsync",
            "PROFILE_RECEIPT_PUBLISH_FAILED")
        self.assertTrue(staged.exists())
        self.assertFalse(self.fixture.local(DEPLOYER.RECEIPT_PATH).exists())
        self.assert_fsync_failure_at(
            "before_receipt_commit_source_parent_fsync",
            "PROFILE_RECEIPT_PUBLISH_FAILED")
        self.assertTrue(staged.exists())
        self.assertFalse(self.fixture.local(DEPLOYER.RECEIPT_PATH).exists())
        receipt = DEPLOYER.deploy()
        self.assertEqual(receipt, self.assert_success_state(self.fixture))

    def test_hard_crash_during_backup_recovery_promotion_is_resumable(self) -> None:
        for seam in (
            "after_backup_recovery_publish_rename",
            "after_backup_recovery_publish_fsync",
            "after_backup_recovery_publish_post_verify",
        ):
            fixture = Fixture()
            try:
                fired = False

                def first_crash(name: str) -> None:
                    nonlocal fired
                    if name == "after_backup_temp_fsync" and not fired:
                        fired = True
                        raise SystemExit(name)

                with mock.patch.object(DEPLOYER, "SEAM_HOOK", first_crash):
                    with self.assertRaises(SystemExit):
                        DEPLOYER.deploy()

                recovery_fired = False

                def recovery_crash(name: str) -> None:
                    nonlocal recovery_fired
                    if name == seam and not recovery_fired:
                        recovery_fired = True
                        raise SystemExit(name)

                with self.subTest(seam=seam):
                    with mock.patch.object(
                            DEPLOYER, "SEAM_HOOK", recovery_crash):
                        with self.assertRaises(SystemExit):
                            DEPLOYER.deploy()
                    self.assertTrue(recovery_fired)
                    receipt = DEPLOYER.deploy()
                    self.assertEqual(
                        receipt, self.assert_success_state(fixture))
            finally:
                fixture.close()

    def test_process_death_after_target_exchange_releases_kernel_lock(self) -> None:
        self.process_crash_at("after_target_exchange")
        self.assert_reason("PROFILE_RECOVERED_POST_REPLACE_CRASH")
        self.assert_p3_state(self.fixture)
        receipt = DEPLOYER.deploy()
        self.assertEqual(receipt, self.assert_success_state(self.fixture))

    def test_process_death_after_receipt_fsync_releases_kernel_lock(self) -> None:
        self.process_crash_at("after_receipt_temp_fsync")
        self.assertTrue(
            self.fixture.local(DEPLOYER.RECEIPT_TEMP_PATH).exists())
        receipt = DEPLOYER.deploy()
        self.assertEqual(receipt, self.assert_success_state(self.fixture))

    def test_hard_crash_after_backup_publication_retries_forward(self) -> None:
        for seam in (
            "after_backup_publish_rename",
            "after_backup_publish_fsync",
            "after_backup_publish",
        ):
            fixture = Fixture()
            try:
                fired = False

                def crash(name: str) -> None:
                    nonlocal fired
                    if name == seam and not fired:
                        fired = True
                        raise SystemExit(seam)

                with self.subTest(seam=seam):
                    with mock.patch.object(DEPLOYER, "SEAM_HOOK", crash):
                        with self.assertRaises(SystemExit):
                            DEPLOYER.deploy()
                    receipt = DEPLOYER.deploy()
                    self.assertEqual(receipt, self.assert_success_state(fixture))
            finally:
                fixture.close()

    def test_hard_crash_with_preexchange_candidate_retries_forward(self) -> None:
        self.hard_crash_at("after_target_temp_fsync")
        self.assert_p3_state(self.fixture)
        receipt = DEPLOYER.deploy()
        self.assertEqual(receipt, self.assert_success_state(self.fixture))

    def test_hard_crash_after_target_exchange_rolls_back_once_then_retries(self) -> None:
        for seam in (
            "after_target_exchange",
            "after_target_replace_before_parent_fsync",
            "after_target_replace_parent_fsync",
            "after_target_replace",
            "after_postflight",
        ):
            fixture = Fixture()
            try:
                fired = False

                def crash(name: str) -> None:
                    nonlocal fired
                    if name == seam and not fired:
                        fired = True
                        raise SystemExit(seam)

                with self.subTest(seam=seam):
                    with mock.patch.object(DEPLOYER, "SEAM_HOOK", crash):
                        with self.assertRaises(SystemExit):
                            DEPLOYER.deploy()
                    with self.assertRaises(DEPLOYER.DeployError) as raised:
                        DEPLOYER.deploy()
                    self.assertEqual(
                        raised.exception.reason,
                        "PROFILE_RECOVERED_POST_REPLACE_CRASH")
                    self.assert_p3_state(fixture)
                    receipt = DEPLOYER.deploy()
                    self.assertEqual(receipt, self.assert_success_state(fixture))
            finally:
                fixture.close()

    def test_hard_crash_during_recovery_rollback_is_resumable(self) -> None:
        for seam in (
            "after_rollback_exchange",
            "after_rollback_exchange_fsync",
        ):
            fixture = Fixture()
            try:
                fired = False

                def first_crash(name: str) -> None:
                    nonlocal fired
                    if name == "after_target_exchange" and not fired:
                        fired = True
                        raise SystemExit(name)

                with mock.patch.object(DEPLOYER, "SEAM_HOOK", first_crash):
                    with self.assertRaises(SystemExit):
                        DEPLOYER.deploy()

                rollback_fired = False

                def rollback_crash(name: str) -> None:
                    nonlocal rollback_fired
                    if name == seam and not rollback_fired:
                        rollback_fired = True
                        raise SystemExit(name)

                with self.subTest(seam=seam):
                    with mock.patch.object(
                            DEPLOYER, "SEAM_HOOK", rollback_crash):
                        with self.assertRaises(SystemExit):
                            DEPLOYER.deploy()
                    self.assertTrue(rollback_fired)
                    self.assert_p3_state(fixture)
                    receipt = DEPLOYER.deploy()
                    self.assertEqual(
                        receipt, self.assert_success_state(fixture))
            finally:
                fixture.close()

    def test_hard_crash_at_receipt_stage_resumes_commit_without_rollback(self) -> None:
        self.hard_crash_at("after_receipt_temp_fsync")
        self.assertEqual(
            self.fixture.local(DEPLOYER.TARGET_PATH).read_bytes(),
            DEPLOYER.NEW_PAYLOAD)
        self.assertTrue(self.fixture.local(DEPLOYER.RECEIPT_TEMP_PATH).exists())
        receipt = DEPLOYER.deploy()
        self.assertEqual(receipt, self.assert_success_state(self.fixture))

    def test_commit_prepared_boundary_drift_preserves_commit_intent(self) -> None:
        self.hard_crash_at("after_receipt_temp_fsync")
        tracked_paths = (
            DEPLOYER.TARGET_PATH,
            DEPLOYER.TARGET_TEMP_PATH,
            DEPLOYER.BACKUP_PATH,
            DEPLOYER.RECEIPT_TEMP_PATH,
        )
        before = {
            path: (
                self.fixture.local(path).stat().st_ino,
                self.fixture.local(path).read_bytes(),
            )
            for path in tracked_paths
        }
        self.fixture.active_unit = DEPLOYER.GATEWAY_BOUNDARY_UNITS[0]
        self.assert_reason("PROFILE_GATEWAY_BOUNDARY_NOT_STOPPED")
        after = {
            path: (
                self.fixture.local(path).stat().st_ino,
                self.fixture.local(path).read_bytes(),
            )
            for path in tracked_paths
        }
        self.assertEqual(after, before)
        self.assertFalse(self.fixture.local(DEPLOYER.RECEIPT_PATH).exists())
        self.fixture.active_unit = None
        receipt = DEPLOYER.deploy()
        self.assertEqual(receipt, self.assert_success_state(self.fixture))

    def test_success_boundary_drift_preserves_exact_success_state(self) -> None:
        expected = DEPLOYER.deploy()
        tracked_paths = (
            DEPLOYER.TARGET_PATH,
            DEPLOYER.TARGET_TEMP_PATH,
            DEPLOYER.BACKUP_PATH,
            DEPLOYER.RECEIPT_PATH,
        )
        before = {
            path: (
                self.fixture.local(path).stat().st_ino,
                self.fixture.local(path).read_bytes(),
            )
            for path in tracked_paths
        }
        self.fixture.gateway_unit_file_state = "masked-runtime"
        self.assert_reason("PROFILE_GATEWAY_BOUNDARY_NOT_STOPPED")
        after = {
            path: (
                self.fixture.local(path).stat().st_ino,
                self.fixture.local(path).read_bytes(),
            )
            for path in tracked_paths
        }
        self.assertEqual(after, before)
        self.fixture.gateway_unit_file_state = "masked"
        self.assertEqual(DEPLOYER.deploy(), expected)

    def test_hard_crash_after_receipt_publication_is_idempotent(self) -> None:
        for seam in (
            "after_receipt_commit_rename",
            "after_receipt_commit_fsync",
            "after_receipt_commit_post_verify",
            "after_receipt_publish",
        ):
            fixture = Fixture()
            try:
                fired = False

                def crash(name: str) -> None:
                    nonlocal fired
                    if name == seam and not fired:
                        fired = True
                        raise SystemExit(f"hard crash at {seam}")

                with self.subTest(seam=seam):
                    with mock.patch.object(DEPLOYER, "SEAM_HOOK", crash):
                        with self.assertRaises(SystemExit):
                            DEPLOYER.deploy()
                    expected = DEPLOYER.deploy()
                    self.assertEqual(expected, self.assert_success_state(fixture))
            finally:
                fixture.close()


if __name__ == "__main__":
    unittest.main()
