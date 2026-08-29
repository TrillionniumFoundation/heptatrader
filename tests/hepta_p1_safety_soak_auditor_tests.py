#!/usr/bin/env python3

"""Independent contract and fail-closed tests for the P1 soak auditor."""

from __future__ import annotations

import copy
from datetime import date, datetime, timedelta, timezone
import io
import importlib.util
import os
from pathlib import Path
import stat
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/hepta_p1_safety_soak_auditor.py"
SPEC = importlib.util.spec_from_file_location("hepta_p1_safety_soak_auditor", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
AUDITOR = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = AUDITOR
SPEC.loader.exec_module(AUDITOR)

COORDINATOR_SPEC = importlib.util.spec_from_file_location(
    "p1_campaign_coordinator_for_auditor_contract_test",
    ROOT / "scripts" / "hepta_p1_safety_soak_campaign_coordinator.py")
assert COORDINATOR_SPEC is not None and COORDINATOR_SPEC.loader is not None
COORDINATOR = importlib.util.module_from_spec(COORDINATOR_SPEC)
sys.modules[COORDINATOR_SPEC.name] = COORDINATOR
COORDINATOR_SPEC.loader.exec_module(COORDINATOR)


CAMPAIGN_ID = "p1-safety-soak-round95"
FORMAL_ID = "hepta-p1-shadow-soak-round95"
DOMAIN_ID = "alpha"
BOOT_ID = "00000000-0000-0000-0000-000000000001"
SOURCE_SHA = AUDITOR.digest_bytes(b"frozen-source")
POLICY_SHA = AUDITOR.digest_bytes(b"frozen-cumulative-policy")
STRATEGY_SHA = AUDITOR.digest_bytes(b"frozen-strategy")
FORMAL_CAMPAIGN_SHA = AUDITOR.digest_bytes(b"formal-campaign")
FORMAL_POLICY_BODY_SHA = AUDITOR.digest_bytes(b"formal-policy-body")
FORMAL_POLICY_FILE_SHA = AUDITOR.digest_bytes(b"formal-policy-file")
GAP_NS = 15 * 60 * 1_000_000_000
START_BOOTTIME_NS = 100_000_000_000_000
FORMAL_SCHEDULED_MS = int(datetime(
    2026, 8, 3, 13, 0, tzinfo=timezone.utc).timestamp() * 1000)
FROZEN_WALL_MS = (
    FORMAL_SCHEDULED_MS - AUDITOR.LAUNCHER_WARMUP_MS -
    AUDITOR.LAUNCHER_EARLY_START_LEAD_MS - 20_000)
FROZEN_BOOTTIME_NS = (
    START_BOOTTIME_NS -
    (FORMAL_SCHEDULED_MS - FROZEN_WALL_MS) * 1_000_000)
EVIDENCE_ROOT = Path("/evidence")
FREEZE_ID = "a" * 32
AUDITOR_SHA = AUDITOR.digest_bytes(b"installed-auditor")
OBSERVER_SHA = AUDITOR.digest_bytes(b"installed-observer")
INJECTOR_SHA = AUDITOR.digest_bytes(b"installed-injector")
LAUNCHER_SHA = AUDITOR.digest_bytes(b"installed-launcher")
FORMAL_SEGMENTS = 22


def digest(name: str) -> str:
    return AUDITOR.digest_bytes(name.encode("ascii"))


def predecessor_activation_success(module=AUDITOR) -> dict[str, object]:
    return {
        "receipt_path": module.PREDECESSOR_ACTIVATION_SUCCESS_PATH,
        "receipt_file_sha256":
            module.PREDECESSOR_ACTIVATION_SUCCESS_FILE_SHA256,
        "receipt_body_sha256":
            module.PREDECESSOR_ACTIVATION_SUCCESS_BODY_SHA256,
        "receipt_schema": "hepta.p1-watch-activation-receipt.v3",
        "receipt_version": 3, "receipt_status": "WATCH_GATEWAY_ACTIVATED",
        "receipt_round": 95, "receipt_domain": "alpha",
        "receipt_device": 8, "receipt_inode": 95,
        "receipt_mode": stat.S_IFREG | 0o600, "receipt_nlink": 1,
        "receipt_uid": 0, "receipt_gid": 0, "receipt_bytes": 4096,
        "receipt_mtime_ns": 95_000, "receipt_ctime_ns": 95_001,
    }


def predecessor_activation_failure(module=AUDITOR) -> dict[str, object]:
    return {
        "receipt_path": module.PREDECESSOR_ACTIVATION_FAILURE_PATH,
        "receipt_file_sha256":
            module.PREDECESSOR_ACTIVATION_FAILURE_FILE_SHA256,
        "receipt_body_sha256":
            module.PREDECESSOR_ACTIVATION_FAILURE_BODY_SHA256,
        "receipt_schema": "hepta.p1-watch-activation-failed-receipt.v2",
        "receipt_version": 2, "receipt_revision": 1,
        "receipt_status": "FAILED_CLOSED", "receipt_round": 95,
        "receipt_domain": "alpha", "receipt_reason": "FAILED_TEST_FIXTURE",
        "receipt_device": 8, "receipt_inode": 96,
        "receipt_mode": stat.S_IFREG | 0o600, "receipt_nlink": 1,
        "receipt_uid": 0, "receipt_gid": 0, "receipt_bytes": 4096,
        "receipt_mtime_ns": 96_000, "receipt_ctime_ns": 96_001,
        "journal_path": module.PREDECESSOR_ACTIVATION_FAILURE_JOURNAL_PATH,
        "journal_sha256":
            module.PREDECESSOR_ACTIVATION_FAILURE_JOURNAL_SHA256,
        "journal_record_count": 21, "journal_terminal_phase": "FAILED_CLOSED",
    }


def shadow_install_evidence(source_sha: str = SOURCE_SHA) -> dict:
    return {
        "schema": "hepta.shadow-runtime-install-consumption-evidence.v3",
        "version": 3,
        "receipt_path": AUDITOR.SHADOW_INSTALL_RECEIPT_PATH,
        "receipt_file_sha256": digest("install-receipt-file"),
        "receipt_body_sha256": digest("install-receipt-body"),
        "manifest_path": AUDITOR.SHADOW_INSTALL_MANIFEST_PATH,
        "manifest_file_sha256": digest("install-manifest-file"),
        "archive_sha256": digest("install-archive"),
        "source_baseline_sha256": source_sha,
        "installer_sha256": digest("installer"),
        "installed_file_count": 128,
        "installed_paths_sha256": digest("installed-paths"),
        "closure_sha256": digest("install-closure"),
        "transaction_lock": {
            "path": AUDITOR.SHADOW_INSTALL_LOCK_PATH, "device": 8,
            "inode": 100, "nlink": 1, "uid": 0, "gid": 0,
            "mode": "0600", "size": 0, "mtime_ns": 1, "ctime_ns": 2,
            "created_during_transaction": False, "persistent": True,
            "held_during_transaction": True,
        },
        "default_deny_identity_sha256":
            AUDITOR.SHADOW_DEFAULT_DENY_IDENTITY_SHA256,
        "lock_mode": "exclusive", "verified_under_lock": True,
        "domain": "alpha", "backup_root": AUDITOR.SHADOW_INSTALL_BACKUP_ROOT,
        "paper_authorized": False, "live_authorized": False,
        "mutation_attempted": False, "direct_broker_access": False,
        "current_install_pointer_path":
            AUDITOR.SHADOW_CURRENT_INSTALL_POINTER_PATH,
        "current_install_pointer_file_sha256": digest("install-pointer"),
        "install_generation": 22, "predecessor_install_generation": 21,
        "predecessor_current_install_pointer_file_sha256":
            AUDITOR.SHADOW_PREDECESSOR_POINTER_SHA256,
    }


def artifact(role: str, index: int, document: dict) -> AUDITOR.Artifact:
    return AUDITOR.Artifact.from_document(
        role, str(EVIDENCE_ROOT / f"{role}-{index:04d}.json"), document)


def observer_artifact(
    kind: str, index: int, document: dict,
) -> AUDITOR.Artifact:
    return AUDITOR.Artifact.from_document(
        "observer_receipt",
        str(EVIDENCE_ROOT / f"raw-{kind}-observer-{index:04d}.json"),
        document,
    )


def observer_reference(value: AUDITOR.Artifact) -> dict[str, str]:
    return {
        "path": value.path,
        "file_sha256": value.file_sha256,
        "body_sha256": value.body_sha256,
        "schema": value.document["schema"],
    }


def state_seal(body: dict) -> dict:
    return {
        **body,
        "state_sha256": AUDITOR.digest_bytes(AUDITOR.canonical_bytes(body)),
    }


def identity_lists(observed_boottime_ns: int) -> dict:
    unit = state_seal({
        "unit": "hepta-test-observer.service", "load_state": "loaded",
        "active_state": "active", "sub_state": "running",
        "unit_file_state": "transient", "main_pid": 1000,
        "invocation_id": "0" * 32,
        "exec_main_start_timestamp_monotonic_us":
            observed_boottime_ns // 1000,
        "n_restarts": 0,
    })
    process = state_seal({
        "pid": 1000, "uid": 0, "gid": 0, "starttime_ticks": 100,
        "exe_device": 1, "exe_inode": 2,
        "cgroup_sha256": digest("observer-cgroup"),
    })
    path = state_seal({
        "path": "/run/hepta-test-observer-state", "present": False,
        "parent_device": 1, "parent_inode": 2, "parent_uid": 0,
        "parent_gid": 0, "parent_mode": 0o700, "parent_nlink": 2,
        "file_type": None, "device": None, "inode": None,
        "uid": None, "gid": None, "mode": None, "nlink": None,
        "size": None, "mtime_ns": None, "ctime_ns": None,
        "content_file_sha256": None, "content_body_sha256": None,
    })
    broker = state_seal({
        "helper_path": "/usr/libexec/hepta-broker-egress-policy",
        "helper_file_sha256": digest("broker-helper"),
        "policy_sha256": digest("broker-policy"),
        "authorized_connector_count": 0, "authorized_uids": [],
        "protected_port_count": 2, "deny_all": True,
        "checked_boottime_ns": observed_boottime_ns,
    })
    return {
        "systemd_units": [unit], "processes": [process], "paths": [path],
        "broker_deny_all": broker,
    }


def observation_evidence(
    kind: str, observed_boottime_ns: int,
    fault_injection_receipt: dict | None = None,
) -> dict:
    return AUDITOR.seal({
        "schema": AUDITOR.OBSERVATION_EVIDENCE_SCHEMA, "version": 1,
        "kind": kind, "boot_id": BOOT_ID,
        "observed_boottime_ns": observed_boottime_ns,
        **identity_lists(observed_boottime_ns),
        "fault_injection_receipt": fault_injection_receipt,
    })


def fault_target_identity(
    phase: str, target_id: str, fault_type: str, observed_boottime_ns: int,
) -> dict:
    post = phase == "POST"
    identities = identity_lists(observed_boottime_ns)
    if fault_type in {"SERVICE_RESTART", "NETWORK_DENY_RELOAD"}:
        unit = dict(identities["systemd_units"][0])
        unit.pop("state_sha256")
        unit["unit"] = (
            "hepta-tool-gateway@alpha.service"
            if fault_type == "SERVICE_RESTART" else
            "hepta-broker-egress-policy.service")
        identities["systemd_units"] = [state_seal(unit)]
    if fault_type in {"PROCESS_KILL", "EVIDENCE_WRITER_CRASH"} and post:
        process = dict(identities["processes"][0])
        process.pop("state_sha256")
        process.update({"pid": 1001, "starttime_ticks": 101})
        identities["processes"] = [state_seal(process)]
    if fault_type in {"SERVICE_RESTART", "NETWORK_DENY_RELOAD"} and post:
        unit = dict(identities["systemd_units"][0])
        unit.pop("state_sha256")
        unit.update({
            "main_pid": 1001, "invocation_id": "1" * 32,
            "exec_main_start_timestamp_monotonic_us":
                observed_boottime_ns // 1000,
        })
        identities["systemd_units"] = [state_seal(unit)]
    fixture_generation = None
    fixture_expiry = None
    fixture_valid = None
    if fault_type in AUDITOR.FAULT_FIXTURE_PATHS:
        fixture_path = AUDITOR.FAULT_FIXTURE_PATHS[fault_type]
        fixture = state_seal({
            "path": fixture_path, "present": True,
            "parent_device": 1, "parent_inode": 2, "parent_uid": 0,
            "parent_gid": 0, "parent_mode": 0o700, "parent_nlink": 2,
            "file_type": "regular", "device": 1,
            "inode": 11 if post else 10, "uid": 0, "gid": 0,
            "mode": 0o100600, "nlink": 1, "size": 100,
            "mtime_ns": observed_boottime_ns,
            "ctime_ns": observed_boottime_ns,
            "content_file_sha256": digest(
                f"{fault_type}-fixture-file-{phase}"),
            "content_body_sha256": digest(
                f"{fault_type}-fixture-body-{phase}"),
        })
        identities["paths"] = [fixture]
        fixture_generation = 2 if post else 1
        fixture_expiry = observed_boottime_ns + (
            60 * 1_000_000_000 if post or fault_type == "TOKEN_LOSS" else 1)
        fixture_valid = True
    if fault_type == "CLOCK_STEP":
        unit = dict(identities["systemd_units"][0])
        unit.pop("state_sha256")
        unit["exec_main_start_timestamp_monotonic_us"] = \
            START_BOOTTIME_NS // 1000
        identities["systemd_units"] = [state_seal(unit)]
    return AUDITOR.seal({
        "schema": AUDITOR.FAULT_TARGET_IDENTITY_SCHEMA, "version": 1,
        "phase": phase, "target_id": target_id, "boot_id": BOOT_ID,
        "observed_boottime_ns": observed_boottime_ns,
        "service_epoch": (
            "epoch-2" if fault_type == "SERVICE_RESTART" and post else
            "epoch-1"),
        "fencing_generation": 7, "lease_generation": 11,
        **identities,
        "residue_count": 0,
        "wall_clock_delta_ms": (
            100 if fault_type == "CLOCK_STEP" and post else
            0 if fault_type == "CLOCK_STEP" else None),
        "fixture_generation": fixture_generation,
        "fixture_expires_boottime_ns": fixture_expiry,
        "fixture_valid": fixture_valid,
    })


def reseal_artifact(
    value: AUDITOR.Artifact, body: dict,
) -> AUDITOR.Artifact:
    body.pop("body_sha256", None)
    return AUDITOR.Artifact.from_document(
        value.role, value.path, AUDITOR.seal(body))


def observer_base(
    schema: str, observer_id: str, observed_boottime_ns: int,
    observed_at_offset: int,
) -> dict:
    del observed_at_offset
    observed_at_ms = FORMAL_SCHEDULED_MS + \
        (observed_boottime_ns - START_BOOTTIME_NS) // 1_000_000
    return {
        "schema": schema,
        "version": 1,
        "status": "COMPLETE",
        "observed_at_ms": observed_at_ms,
        "expires_at_ms": observed_at_ms + 60_000,
        "campaign_id": CAMPAIGN_ID,
        "observer_id": observer_id,
        "observation_complete": True,
        "clock_id": "CLOCK_BOOTTIME",
        "boot_id": BOOT_ID,
        "observed_boottime_ns": observed_boottime_ns,
        "source_manifest_sha256": SOURCE_SHA,
        "policy_sha256": POLICY_SHA,
        "strategy_sha256": STRATEGY_SHA,
        "producer": {
            "path": str(AUDITOR.OBSERVER_EXECUTABLE),
            "file_sha256": OBSERVER_SHA,
        },
        "production_mode": AUDITOR.OBSERVER_PRODUCTION_MODE,
        "paper_authorized": False,
        "live_authorized": False,
        "mutation_authorized": False,
        "direct_broker_access": False,
    }


def service_observer_artifact(index: int) -> AUDITOR.Artifact:
    observed_boottime = START_BOOTTIME_NS + index * GAP_NS
    service_observation = observer_base(
        AUDITOR.SERVICE_OBSERVATION_SCHEMA,
        f"service-observer-{index:04d}", observed_boottime, index)
    service_observation.update({
        "service_epoch": "epoch-1", "fencing_generation": 7,
        "lease_generation": 11, "transition_fault_id": None,
        "continuity_ok": True, "audit_ok": True, "cleanup_ok": True,
        "observation_evidence": observation_evidence(
            "SERVICE", observed_boottime),
    })
    return observer_artifact(
        "service", index, AUDITOR.seal(service_observation))


def continuity_unit(
    name: str, *, substate: str, pid: int = 0, invocation: str = "",
) -> dict:
    return state_seal({
        "unit": name, "load_state": "loaded", "active_state": "active",
        "sub_state": substate, "unit_file_state": "static",
        "main_pid": pid, "invocation_id": invocation,
        "exec_main_start_timestamp_monotonic_us": 100 if pid else 0,
        "n_restarts": 0,
    })


def continuity_path(
    path: str, *, file_type: str = "socket", inode: int = 10,
    file_sha256: str | None = None, body_sha256: str | None = None,
    uid: int = 0, gid: int = 0, mode: int = 0o660,
    parent_uid: int = 0, parent_gid: int = 0,
    parent_mode: int = 0o700,
) -> dict:
    return state_seal({
        "path": path, "present": True,
        "parent_device": 8, "parent_inode": 9,
        "parent_uid": parent_uid, "parent_gid": parent_gid,
        "parent_mode": parent_mode, "parent_nlink": 2,
        "file_type": file_type, "device": 8, "inode": inode,
        "uid": uid, "gid": gid, "mode": mode, "nlink": 1,
        "size": 0, "mtime_ns": 1, "ctime_ns": 1,
        "content_file_sha256": file_sha256,
        "content_body_sha256": body_sha256,
    })


def document_reference(path: str, document: dict) -> dict[str, str]:
    return {
        "path": path,
        "file_sha256": AUDITOR.digest_bytes(AUDITOR.canonical_bytes(document)),
        "body_sha256": document["body_sha256"],
        "schema": document["schema"],
    }


def activation_receipt_document(
    source_sha: str = SOURCE_SHA,
) -> dict:
    gateway_after = {
        "unit": "hepta-tool-gateway@alpha.service",
        "active_state": "active", "sub_state": "running",
        "gateway_main_pid": 2101, "gateway_invocation_id": "a" * 32,
        "gateway_exec_main_start_timestamp_monotonic_us": 100,
        "process_starttime_ticks": 210100,
        "gateway_executable_path": str(AUDITOR.GATEWAY_EXECUTABLE),
        "gateway_executable_sha256": digest("gateway-executable"),
        "domain_config_sha256": digest("gateway-domain"),
        "gateway_profile_path": str(AUDITOR.GATEWAY_PROFILE),
        "gateway_profile_sha256": digest("gateway-profile"),
        "gateway_process_profile_sha256": digest("process-profile"),
        "execution_remote_mode": "SIMULATOR", "tool_account": "SIM",
        "execution_domain_id": "SIM:alpha", "tool_allow_trade": "0",
        "session_templates": "watch",
        "contract_bindings": "EUR.USD|EUR|CASH|IDEALPRO|USD",
        "gateway_socket_path": str(AUDITOR.GATEWAY_TOOL_SOCKET),
        "gateway_socket_device": 8, "gateway_socket_inode": 23,
        "supervisor_socket_path": str(AUDITOR.GATEWAY_SUPERVISOR_SOCKET),
        "supervisor_socket_device": 8, "supervisor_socket_inode": 24,
        "unit_contract_sha256": digest("gateway-unit-contract"),
    }
    body = {
        field: None for field in AUDITOR.ACTIVATION_RECEIPT_FIELDS
        if field != "body_sha256"
    }
    body.update({
        "schema": "hepta.p1-watch-activation-receipt.v4", "version": 4,
        "status": "WATCH_GATEWAY_ACTIVATED", "round": 114,
        "domain": "alpha", "boot_id": BOOT_ID,
        "gateway_activated": True,
        "broker_deny_all_continuity_attested": True,
        "kill_switch_engaged": True, "watch_authority_provisioned": False,
        "gateway_after": gateway_after,
        "paper_authorized": False, "live_authorized": False,
        "mutation_attempted": False, "direct_broker_access": False,
        "shadow_install_evidence": shadow_install_evidence(source_sha),
        "predecessor_activation_success": predecessor_activation_success(),
        "predecessor_activation_failure": predecessor_activation_failure(),
    })
    return AUDITOR.seal(body)


def campaign_continuity_observer_artifact(
    index: int, freeze: AUDITOR.Artifact, runtime: AUDITOR.Artifact,
    *, origin_ms: int, end_ms: int, cadence_ms: int,
    activation_source_sha: str = SOURCE_SHA,
) -> AUDITOR.Artifact:
    final_slot = (end_ms - origin_ms + cadence_ms - 1) // cadence_ms
    scheduled = min(origin_ms + index * cadence_ms, end_ms)
    observed = FROZEN_BOOTTIME_NS + \
        (scheduled - FROZEN_WALL_MS) * 1_000_000
    lease = AUDITOR.seal({
        "schema": "hepta.shadow-watch-lease-receipt.v1", "version": 1,
        "domain_id": "alpha", "agent_id": "alpha", "agent_uid": 2104,
        "boundary": "WATCH", "operation": "ROTATE",
        "lease_generation": 11, "previous_lease_generation": 10,
        "previous_receipt_body_sha256": digest("lease-generation-10"),
        "accepted": True, "reason_code": "OK",
        "accepted_at_ms": FROZEN_WALL_MS, "ttl_seconds": 3_000_000,
        "expires_at_ms": FROZEN_WALL_MS + 30 * 24 * 60 * 60 * 1000,
        "paper_authorized": False, "live_authorized": False,
        "mutation_authorized": False,
    })
    export_root = Path("/run/hepta-shadow-watch-export-alpha")
    commit_sequence = index + 1
    generation = (
        f"generation-{commit_sequence:020d}-fixture{commit_sequence:08d}")
    generation_root = export_root / "generations" / generation
    snapshot = AUDITOR.seal({
        "schema": "hepta.shadow-watch-snapshot.v1", "version": 1,
        "domain_id": "alpha", "agent_uid": 2104,
        "generated_at_ms": scheduled - 2_000,
        "paper_authorized": False, "live_authorized": False,
        "mutation_attempted": False, "direct_broker_access": False,
    })
    snapshot_reference = document_reference(
        str(generation_root / "snapshot.json"), snapshot)
    lease_reference = document_reference(
        str(generation_root / "shadow-watch-lease-receipt.json"), lease)
    export_receipt_document = AUDITOR.seal({
        "schema": "hepta.shadow-watch-export-receipt.v1", "version": 1,
        "domain_id": "alpha", "agent_uid": 2104,
        "reader_uid": 1000, "reader_gid": 1000,
        "lease_generation": lease["lease_generation"],
        "snapshot_body_sha256": snapshot["body_sha256"],
        "lease_receipt_body_sha256": lease["body_sha256"],
        "paper_authorized": False, "live_authorized": False,
        "mutation_attempted": False, "direct_broker_access": False,
    })
    export_receipt = document_reference(
        str(generation_root / "shadow-watch-export-receipt.json"),
        export_receipt_document)
    committed_at_ms = scheduled - 500
    export_commit_document = AUDITOR.seal({
        "schema": "hepta.shadow-watch-export-commit.v1", "version": 1,
        "authority_status": "ACTIVE",
        "authority_changed_at_ms": committed_at_ms,
        "close_reason": None, "commit_sequence": commit_sequence,
        "generation": generation, "domain_id": "alpha",
        "agent_uid": 2104, "reader_uid": 1000, "reader_gid": 1000,
        "lease_generation": lease["lease_generation"],
        "snapshot_body_sha256": snapshot_reference["body_sha256"],
        "snapshot_file_sha256": snapshot_reference["file_sha256"],
        "lease_receipt_body_sha256": lease_reference["body_sha256"],
        "lease_receipt_file_sha256": lease_reference["file_sha256"],
        "export_receipt_body_sha256": export_receipt["body_sha256"],
        "export_receipt_file_sha256": export_receipt["file_sha256"],
        "committed_at_ms": committed_at_ms,
        "paper_authorized": False, "live_authorized": False,
        "mutation_attempted": False, "direct_broker_access": False,
    })
    export_commit = document_reference(
        str(export_root / "current.json"), export_commit_document)
    export_references = (
        export_commit, snapshot_reference, lease_reference, export_receipt)
    export_identities = [
        continuity_path(
            reference["path"], file_type="regular", inode=30 + offset,
            file_sha256=reference["file_sha256"],
            body_sha256=reference["body_sha256"], uid=0, gid=1000,
            mode=0o440, parent_uid=0, parent_gid=1000,
            parent_mode=0o750)
        for offset, reference in enumerate(export_references)
    ]
    gateway = continuity_unit(
        "hepta-tool-gateway@alpha.service", substate="running", pid=2101,
        invocation="a" * 32)
    custodian = continuity_unit(
        "hepta-shadow-watch-custodian@alpha.service", substate="running",
        pid=2102, invocation="b" * 32)
    collector = continuity_unit(
        "hepta-shadow-watch-collector@alpha.timer", substate="waiting")
    reconcile = continuity_unit(
        "hepta-p1-watch-activation-reconcile.timer", substate="waiting")
    gateway_process = state_seal({
        "pid": 2101, "uid": 1000, "gid": 1000,
        "starttime_ticks": 210100, "exe_device": 8, "exe_inode": 20,
        "cgroup_sha256": digest("gateway-cgroup"),
    })
    gateway_executable = continuity_path(
        str(AUDITOR.GATEWAY_EXECUTABLE), file_type="regular", inode=20,
        file_sha256=digest("gateway-executable"))
    gateway_profile = continuity_path(
        str(AUDITOR.GATEWAY_PROFILE), file_type="regular", inode=21,
        file_sha256=digest("gateway-profile"))
    gateway_domain = continuity_path(
        str(AUDITOR.GATEWAY_DOMAIN_CONFIG), file_type="regular", inode=22,
        file_sha256=digest("gateway-domain"))
    tool_socket = continuity_path(
        str(AUDITOR.GATEWAY_TOOL_SOCKET), inode=23)
    supervisor_socket = continuity_path(
        str(AUDITOR.GATEWAY_SUPERVISOR_SOCKET), inode=24)
    activation = activation_receipt_document(activation_source_sha)
    broker = state_seal({
        "helper_path": "/usr/libexec/hepta-broker-egress-policy",
        "helper_file_sha256": digest("broker-helper"),
        "policy_sha256": digest("broker-policy"),
        "authorized_connector_count": 0, "authorized_uids": [],
        "protected_port_count": 4, "deny_all": True,
        "checked_boottime_ns": observed,
    })
    evidence = AUDITOR.seal({
        "schema": AUDITOR.OBSERVATION_EVIDENCE_SCHEMA, "version": 1,
        "kind": "CAMPAIGN_CONTINUITY", "boot_id": BOOT_ID,
        "observed_boottime_ns": observed,
        "systemd_units": sorted(
            [gateway, custodian, collector, reconcile],
            key=lambda item: item["unit"]),
        "processes": [gateway_process],
        "paths": sorted([
            gateway_executable, gateway_profile, gateway_domain,
            tool_socket, supervisor_socket, *export_identities,
        ], key=lambda item: item["path"]),
        "broker_deny_all": broker, "fault_injection_receipt": None,
    })
    value = observer_base(
        AUDITOR.CAMPAIGN_CONTINUITY_OBSERVATION_SCHEMA,
        f"campaign-continuity-observer-{index:04d}", observed, index)
    value.update({
        "freeze_bundle": AUDITOR._reference(freeze),
        "campaign_runtime": observer_reference(runtime),
        "continuity_slot_index": index,
        "continuity_scheduled_at_ms": scheduled,
        "continuity_origin_ms": origin_ms,
        "continuity_end_ms": end_ms,
        "continuity_cadence_ms": cadence_ms,
        "continuity_final_slot": final_slot,
        "continuity_is_final": index == final_slot,
        "catch_up": False,
        "activation_receipt": document_reference(
            "/evidence/activation-receipt.json", activation),
        "activation_receipt_document": activation,
        "export_commit": export_commit,
        "export_commit_document": export_commit_document,
        "export_snapshot": snapshot_reference,
        "lease_receipt": lease_reference,
        "lease_receipt_document": lease,
        "export_receipt": export_receipt,
        "lease_generation": 11, "previous_lease_generation": 10,
        "previous_lease_receipt_body_sha256":
            lease["previous_receipt_body_sha256"],
        "gateway_identity": gateway,
        "gateway_process_identity": gateway_process,
        "gateway_executable_identity": gateway_executable,
        "gateway_profile_identity": gateway_profile,
        "gateway_domain_config_identity": gateway_domain,
        "supervisor_socket_identity": supervisor_socket,
        "custodian_identity": custodian,
        "collector_timer_identity": collector,
        "activation_reconcile_timer_identity": reconcile,
        "tool_socket_identity": tool_socket, "transition_fault_id": None,
        "persistent_stack_ok": True, "lease_chain_ok": True,
        "connector_count": 0, "authorized_uids": [],
        "paper_unit_active_count": 0, "campaign_socket_present": False,
        "kill_switch_engaged": True, "zero_exposure": True,
        "observation_evidence": evidence,
    })
    return observer_artifact(
        "campaign-continuity", index, AUDITOR.seal(value))


def formal_id(day_index: int) -> str:
    return f"hepta-p1-shadow-soak-round{95 + day_index}"


def formal_lineage(identifier: str) -> dict[str, str]:
    return {
        "campaign_id": identifier,
        "campaign_sha256": digest(identifier + "-campaign"),
        "policy_body_sha256": digest(identifier + "-policy-body"),
        "policy_file_sha256": digest(identifier + "-policy-file"),
    }


def production_formal_schedules(
    *, extra_gap_ms: int = 0,
) -> list[tuple[dict, list[int]]]:
    """Return the real multi-day two-minute production grid."""

    result: list[tuple[dict, list[int]]] = []
    previous_teardown: int | None = None
    interval = AUDITOR.POLICY_SLOT_INTERVAL_MS
    maximum = AUDITOR.POLICY_MAXIMUM_ITERATIONS
    for formal_index in range(FORMAL_SEGMENTS):
        if previous_teardown is None:
            valid_after = FORMAL_SCHEDULED_MS
        else:
            valid_after = (
                (previous_teardown + AUDITOR.LAUNCHER_WARMUP_MS +
                 AUDITOR.LAUNCHER_EARLY_START_LEAD_MS) // interval + 1
            ) * interval
            if formal_index == 1:
                valid_after += extra_gap_ms
        identifier = formal_id(formal_index)
        lineage = formal_lineage(identifier)
        expiry = valid_after + maximum * interval
        launcher_start = valid_after - AUDITOR.LAUNCHER_WARMUP_MS
        record = {
            "campaign_id": identifier,
            "path": str(
                EVIDENCE_ROOT / f"formal-policy-{formal_index}.json"),
            "file_sha256": lineage["policy_file_sha256"],
            "body_sha256": lineage["policy_body_sha256"],
            "launcher_start_ms": launcher_start,
            "launcher_dispatch_at_ms": launcher_start -
                AUDITOR.LAUNCHER_EARLY_START_LEAD_MS,
            "valid_after_ms": valid_after,
            "expires_at_ms": expiry,
            "slot_interval_ms": interval,
            "maximum_iterations": maximum,
            "launcher_completion_deadline_ms":
                expiry + AUDITOR.MAXIMUM_LAUNCH_LATENESS_MS,
            "projection_deadline_ms":
                expiry + AUDITOR.POST_FORMAL_PROJECTION_GUARD_MS,
            "teardown_deadline_ms":
                expiry + AUDITOR.POST_FORMAL_TEARDOWN_GUARD_MS,
        }
        slots = [valid_after + index * interval for index in range(maximum)]
        result.append((record, slots))
        previous_teardown = record["teardown_deadline_ms"]
    return result


def custodian_closure(identifier: str) -> dict:
    return AUDITOR.seal({
        "schema": "hepta.shadow-watch-custodian-closure.v1",
        "version": 1,
        "domain_id": DOMAIN_ID,
        "campaign_id": identifier,
        "lease_generation": 2,
        "authoritative_revoke_outcome": "ACCEPTED",
        "local_authority_removed": True,
        "export_evidence_removed": True,
        "paper_authorized": False,
        "live_authorized": False,
        "mutation_authorized": False,
        "direct_broker_access": False,
    })


def verified_closure(
    formal_index: int, slots: list[int], *, catch_up: bool = False,
) -> dict:
    identifier = formal_id(formal_index)
    lineage = formal_lineage(identifier)
    iterations: list[dict] = []
    for sequence, scheduled in enumerate(slots, start=1):
        global_sequence = (
            formal_index * AUDITOR.POLICY_MAXIMUM_ITERATIONS + sequence)
        iteration = {
            field: digest(f"iteration-{global_sequence}-{field}")
            for field in AUDITOR.VERIFIED_ITERATION_FIELDS
        }
        iteration.update({
            "iteration": sequence,
            "segment_index": 1,
            "scheduled_at_ms": scheduled,
            "evaluated_at_ms": scheduled + (
                60_001 if catch_up and global_sequence == 1 else 1000),
            "source_first_sequence": sequence,
            "source_last_sequence": sequence,
            "source_record_count": 1,
            "source_total_record_count": sequence,
            "source_window_truncated": sequence > 1,
            "source_predecessor_record_sha256": (
                None if sequence == 1 else
                digest(f"source-predecessor-{global_sequence}")),
            "materialization_window_ms": 60_000,
            "materialization_maximum_records": 100,
            "source_attestation": {
                "receipt_body_sha256":
                    digest(f"source-receipt-body-{global_sequence}"),
                "receipt_file_sha256":
                    digest(f"source-receipt-file-{global_sequence}"),
                "extractor_code_sha256": digest("extractor"),
                "semantic_output_sha256":
                    digest(f"semantic-{global_sequence}"),
                "completeness_sha256":
                    digest(f"source-completeness-{global_sequence}"),
                "raw_payloads_verified": True,
            },
            "decision_receipt_file_sha256":
                digest(f"decision-artifact-{global_sequence}"),
            "final_outcome": "NO_TRADE",
            "residual_evidence": ["retained-evidence"],
        })
        iterations.append(iteration)
    return AUDITOR.seal({
        "schema": "hepta.bounded-shadow-campaign-closure.v1",
        "version": 1,
        **lineage,
        "strategy_id": "eurusd-confirmed-momentum",
        "strategy_version": "v2",
        "strategy_sha256": STRATEGY_SHA,
        "strategy_file_sha256": digest("strategy-file"),
        "observer_state_body_sha256": digest("observer-body"),
        "observer_state_file_sha256": digest("observer-file"),
        "strategy_state_file_sha256": digest("strategy-state"),
        "final_audit_body_sha256": digest("final-audit-body"),
        "final_audit_file_sha256": digest("final-audit-file"),
        "verified_at_ms": slots[-1] + 2000,
        "completed_iterations": len(slots),
        "maximum_iterations": len(slots),
        "segment_count": 1,
        "segments": [{
            "segment_index": 1,
            "record_count": len(slots),
            "history_head_sha256": digest("history-head"),
            "source_sha256": digest("history-source"),
            "history_record_bytes": 10,
            "history_index_bytes": 5,
            "history_storage_bytes": 15,
            "audit_sha256": digest("history-audit"),
        }],
        "iteration_count": len(slots),
        "iterations": iterations,
        "residual_evidence": ["retained-evidence"],
        "complete_revalidation": False,
        "closure_status": "VERIFIED_WITH_RETAINED_EVIDENCE_RESIDUALS",
        "paper_authorized": False,
        "live_authorized": False,
        "mutation_attempted": False,
        "direct_broker_access": False,
    })


def launcher_receipt(
    closure_artifact: AUDITOR.Artifact, formal_index: int,
    formal_record: dict,
    status: str = "FORMAL_COMPLETE",
) -> dict:
    identifier = formal_id(formal_index)
    scheduled = formal_record["valid_after_ms"]
    final_scheduled = (
        formal_record["expires_at_ms"] - formal_record["slot_interval_ms"])
    lineage = formal_lineage(identifier)
    activation = activation_receipt_document()
    probe_id = f"hepta-p1-shadow-load-probe-round{195 + formal_index}"
    body = {
        field: None
        for field in AUDITOR.LAUNCHER_RECEIPT_FIELDS - {"body_sha256"}
    }
    body.update({
        "schema": "hepta.p1-shadow-admission-launcher-receipt.v1",
        "version": 1,
        "status": status,
        "reason": None if status == "FORMAL_COMPLETE" else "FAILED_CLOSED",
        "domain_id": DOMAIN_ID,
        "probe_campaign_id": probe_id,
        "formal_campaign_id": identifier,
        "formal_start_ms": formal_record["launcher_start_ms"],
        "completed_at_ms": final_scheduled + 3000,
        "launcher_identity": {
            "unit": f"hepta-p1-shadow-admission-round{95 + formal_index}.service",
            "invocation_id": f"{formal_index + 1:032x}",
            "main_pid": 1000 + formal_index,
            "type": "exec", "restart": "no", "remain_after_exit": "no",
            "user": "root", "group": "root",
            "exec_start": [
                str(AUDITOR.LAUNCHER_EXECUTABLE), "--probe-campaign-id",
                probe_id, "--formal-campaign-id", identifier,
                "--formal-start-ms", str(formal_record["launcher_start_ms"]),
            ],
            "environment": {}, "launcher_sha256": LAUNCHER_SHA,
            "conflicts": [],
        },
        "helper_sha256": {"launcher_sha256": LAUNCHER_SHA},
        "activation_receipt_path": "/evidence/activation-receipt.json",
        "activation_receipt_file_sha256": AUDITOR.digest_bytes(
            AUDITOR.canonical_bytes(activation)),
        "activation_receipt_body_sha256": activation["body_sha256"],
        "activation_profile_receipt_file_sha256": digest("profile-file"),
        "activation_profile_receipt_body_sha256": digest("profile-body"),
        "activation_broker_epoch": {},
        "activation_gateway_epoch": {},
        "activation_reconcile_timer": {},
        "activation_predecessor_success": {},
        "activation_predecessor_failure": {},
        "gateway_identity": {},
        "formal_policy_file_sha256": lineage["policy_file_sha256"],
        "formal_valid_after_ms": scheduled,
        "formal_expected_iterations": formal_record["maximum_iterations"],
        "formal_completed_iterations": formal_record["maximum_iterations"],
        "formal_final_generation": 2,
        "formal_controller_status_file_sha256": digest("controller-file"),
        "formal_observer_state_file_sha256": digest("state-file"),
        "formal_verified_closure_file_sha256": closure_artifact.file_sha256,
        "formal_verified_closure_body_sha256": closure_artifact.body_sha256,
        "formal_host_result_sha256": digest("host-result"),
        "formal_reader_completion": {},
        "formal_post_verifier_reader_evidence": {},
        "execution_service_epoch": "epoch-1",
        "execution_service_fencing_generation": 7,
        "formal_reader_pid": 1000,
        "formal_generation": 1,
        "formal_closure": custodian_closure(identifier),
        "cleanup_errors": [],
        "authority_residue": False,
        "export_residue": False,
        "load_probe_admission_receipt_activation_binding_attested": False,
        "paper_authorized": False,
        "live_authorized": False,
        "mutation_authorized": False,
        "direct_broker_access": False,
    })
    return AUDITOR.seal(body)


def trading_days() -> list[str]:
    result: list[str] = []
    cursor = date(2026, 8, 3)
    while len(result) < AUDITOR.MINIMUM_TRADING_DAYS:
        if (cursor.weekday() < 5 and cursor.isoformat() not in
                AUDITOR.CALENDAR_EXCLUDED_DAYS_2026):
            result.append(cursor.isoformat())
        cursor += timedelta(days=1)
    return result


def make_bundle(
    *, duration_hours: int | None = None, incomplete_count: int = 0,
    eligible_day_count: int = AUDITOR.MINIMUM_TRADING_DAYS,
    catch_up: bool = False,
    authority_exposure: bool = False, cleanup_failure: bool = False,
    broken_decision_chain: bool = False, source_drift: bool = False,
    omit_fault_result: bool = False, launcher_status: str = "FORMAL_COMPLETE",
    decision_binding_drift: str | None = None,
    decision_set_drift: str | None = None,
    fault_type_coverage: str | None = None,
    fault_target_drift: bool = False,
    fault_timing_drift: str | None = None,
    activation_source_drift: bool = False,
    formal_gap_ms: int = 0,
) -> dict:
    schedules = production_formal_schedules(extra_gap_ms=formal_gap_ms)
    formal_records = [copy.deepcopy(item[0]) for item in schedules]
    formal_campaigns = [
        formal_lineage(item["campaign_id"]) for item in formal_records]
    all_slots = [slot for _record, slots in schedules for slot in slots]
    fault_types = [
        "EVIDENCE_WRITER_CRASH", "PROCESS_KILL", "SERVICE_RESTART",
        "TOKEN_LOSS", "LEASE_EXPIRY", "NETWORK_DENY_RELOAD",
        "CLOCK_STEP",
    ]
    if fault_type_coverage == "missing":
        fault_types.pop()
    elif fault_type_coverage == "duplicate-type":
        fault_types[-1] = fault_types[0]
    per_formal_count: dict[int, int] = {}
    planned_faults = []
    for index, fault_type in enumerate(fault_types, start=1):
        formal_index = min(
            (index - 1) * len(schedules) // len(fault_types),
            len(schedules) - 1)
        within_formal = per_formal_count.get(formal_index, 0)
        per_formal_count[formal_index] = within_formal + 1
        formal_record = formal_records[formal_index]
        formal_start_boottime = FROZEN_BOOTTIME_NS + (
            formal_record["valid_after_ms"] - FROZEN_WALL_MS) * 1_000_000
        planned_faults.append({
            "fault_id": f"fault-{index}",
            "fault_type": fault_type,
            "target_id": AUDITOR.FAULT_TARGET_IDS[fault_type],
            "formal_campaign_id": formal_record["campaign_id"],
            "inject_at_boottime_ns": formal_start_boottime +
                (within_formal + 1) * 2 * GAP_NS,
            "maximum_injection_lateness_ns": 5 * 1_000_000_000,
            "maximum_recovery_ns": 60 * 1_000_000_000,
        })
    if fault_target_drift:
        planned_faults[0]["target_id"] = "unrelated-process"
    if fault_timing_drift == "excessive-lateness":
        planned_faults[0]["maximum_injection_lateness_ns"] = \
            AUDITOR.MAXIMUM_FAULT_INJECTION_LATENESS_NS + 1
    elif fault_timing_drift == "overlap":
        planned_faults[1]["inject_at_boottime_ns"] = \
            planned_faults[0]["inject_at_boottime_ns"] + \
            60 * 1_000_000_000
    elif fault_timing_drift == "wrong-formal":
        planned_faults[-1]["formal_campaign_id"] = "unknown-formal"
    elif fault_timing_drift == "outside-formal":
        window = formal_records[-1]
        window_end = FROZEN_BOOTTIME_NS + (
            window["expires_at_ms"] - FROZEN_WALL_MS) * 1_000_000
        planned_faults[-1]["inject_at_boottime_ns"] = (
            window_end -
            planned_faults[-1]["maximum_injection_lateness_ns"] -
            planned_faults[-1]["maximum_recovery_ns"])
    plan_document = AUDITOR.seal({
        "schema": "hepta.p1-safety-soak-fault-plan.v1",
        "version": 1,
        "campaign_id": CAMPAIGN_ID,
        "source_manifest_sha256": SOURCE_SHA,
        "policy_sha256": POLICY_SHA,
        "strategy_sha256": STRATEGY_SHA,
        "planned_faults": planned_faults,
        "paper_authorized": False,
        "live_authorized": False,
        "mutation_authorized": False,
        "direct_broker_access": False,
    })
    plan_artifact = artifact("fault_plan", 0, plan_document)
    freezer_sha = digest("installed-freezer")
    freezer_producer = {
        "path": str(AUDITOR.FREEZER_EXECUTABLE),
        "file_sha256": freezer_sha,
    }
    calendar_sessions = AUDITOR._expected_calendar_sessions(
        all_slots, "TEST_CALENDAR")
    days = [item["trading_day"] for item in calendar_sessions]
    eligible_schedule = [
        slot for slot in all_slots
        if any(
            session["opens_at_ms"] <= slot < session["closes_at_ms"] and
            not any(window["opens_at_ms"] <= slot < window["closes_at_ms"]
                    for window in session["maintenance_windows"])
            for session in calendar_sessions)
    ]
    calendar_document = AUDITOR.seal({
        "schema": AUDITOR.CALENDAR_SCHEMA, "version": 1,
        "status": "FROZEN", "freeze_id": FREEZE_ID,
        "producer": freezer_producer,
        "production_mode": AUDITOR.FREEZER_PRODUCTION_MODE,
        "calendar_id": AUDITOR.CALENDAR_ID,
        "calendar_version": AUDITOR.CALENDAR_VERSION,
        "calendar_source_sha256": AUDITOR._calendar_source_sha256(),
        "trading_timezone": AUDITOR.CALENDAR_TIMEZONE,
        "sessions": calendar_sessions,
        "issued_at_ms": FROZEN_WALL_MS,
        "expires_at_ms": all_slots[-1] + 86_400_000,
        "paper_authorized": False, "live_authorized": False,
        "mutation_authorized": False, "direct_broker_access": False,
    })
    calendar_artifact = artifact(
        "trading_calendar", 0, calendar_document)
    source_pins = []
    for role, (source_path, installed_path) in sorted(
            AUDITOR.SOURCE_PRODUCER_PATHS.items()):
        pin_sha = {
            "campaign_freezer": freezer_sha,
            "auditor": AUDITOR_SHA,
            "independent_observer": OBSERVER_SHA,
            "root_fault_injector": INJECTOR_SHA,
            "shadow_admission_launcher": LAUNCHER_SHA,
        }.get(role, digest("installed-" + role))
        source_pins.append({
            "role": role, "source_path": source_path,
            "installed_path": installed_path, "file_sha256": pin_sha,
        })
    reference = lambda name: {
        "path": str(EVIDENCE_ROOT / f"{name}.json"),
        "file_sha256": digest(name + "-file"),
        "body_sha256": digest(name + "-body"),
    }
    bundle_document = AUDITOR.seal({
        "schema": AUDITOR.FREEZE_BUNDLE_SCHEMA, "version": 1,
        "status": "FROZEN", "round": 114, "freeze_id": FREEZE_ID,
        "issued_at_ms": FROZEN_WALL_MS,
        "expires_at_ms": all_slots[-1] + 86_400_000,
        "campaign_id": CAMPAIGN_ID, "domain_id": DOMAIN_ID,
        "producer": freezer_producer,
        "production_mode": AUDITOR.FREEZER_PRODUCTION_MODE,
        "boot_id": BOOT_ID, "frozen_boottime_ns": FROZEN_BOOTTIME_NS,
        "source_baseline": reference("source-baseline"),
        "source_manifest_sha256": SOURCE_SHA,
        "source_producer_pins": source_pins,
        "policy_sha256": POLICY_SHA,
        "formal_policies": formal_records,
        "strategy_id": "eurusd-confirmed-momentum",
        "strategy_version": "v2", "strategy_sha256": STRATEGY_SHA,
        "strategy_files": [
            {"role": role, **reference("strategy-" + role)}
            for role in (
                "config", "evaluator", "context_builder", "normalizer",
                "contracts")
        ],
        "trading_calendar": AUDITOR._reference(calendar_artifact),
        "calendar_id": AUDITOR.CALENDAR_ID,
        "calendar_version": AUDITOR.CALENDAR_VERSION,
        "calendar_source_sha256": AUDITOR._calendar_source_sha256(),
        "declared_trading_days": days,
        "trading_timezone": AUDITOR.CALENDAR_TIMEZONE,
        "trading_calendar_sha256": calendar_artifact.body_sha256,
        "eligible_scheduled_at_ms": eligible_schedule,
        "scheduled_decision_count": len(all_slots),
        "planned_faults": planned_faults,
        "anchors": {
            role: reference(role) for role in (
                "source_anchor", "policy_anchor", "strategy_anchor",
                "frozen_schedule", "frozen_fault_schedule")
        },
        "paper_authorized": False, "live_authorized": False,
        "mutation_authorized": False, "direct_broker_access": False,
    })
    freeze_artifact = artifact("freeze_bundle", 0, bundle_document)
    spec_document = AUDITOR.seal({
        "schema": "hepta.p1-safety-soak-campaign-spec.v1",
        "version": 1,
        "campaign_id": CAMPAIGN_ID,
        "domain_id": DOMAIN_ID,
        "source_manifest_sha256": SOURCE_SHA,
        "policy_sha256": POLICY_SHA,
        "strategy_id": "eurusd-confirmed-momentum",
        "strategy_version": "v2",
        "strategy_sha256": STRATEGY_SHA,
        "formal_campaigns": formal_campaigns,
        "declared_trading_days": days,
        "trading_timezone": AUDITOR.CALENDAR_TIMEZONE,
        "trading_calendar_sha256": calendar_artifact.body_sha256,
        "eligible_scheduled_at_ms": eligible_schedule,
        "scheduled_decision_count": len(all_slots),
        "minimum_eligible_decisions": 200,
        "minimum_complete_ppm": 990001,
        "minimum_boottime_duration_ns":
            AUDITOR.MINIMUM_BOOTTIME_DURATION_NS,
        "maximum_checkpoint_gap_ns": GAP_NS,
        "maximum_decision_lateness_ms": 60_000,
        "fault_plan_body_sha256": plan_artifact.body_sha256,
        "independent_auditor_id": "independent-p1-auditor",
        "frozen_at_ms": FORMAL_SCHEDULED_MS - 10_000,
        "freeze_bundle": AUDITOR._reference(freeze_artifact),
        "paper_authorized": False,
        "live_authorized": False,
        "mutation_authorized": False,
        "direct_broker_access": False,
    })
    spec_artifact = artifact("campaign_spec", 0, spec_document)
    runtime_formals = [{
        "formal_campaign_id": item["campaign_id"],
        "probe_campaign_id": f"probe-{index + 1:02d}",
        "launcher_start_ms": item["launcher_start_ms"],
        "launcher_dispatch_at_ms": item["launcher_dispatch_at_ms"],
        "valid_after_ms": item["valid_after_ms"],
        "slot_interval_ms": item["slot_interval_ms"],
        "maximum_iterations": item["maximum_iterations"],
        "expires_at_ms": item["expires_at_ms"],
        "launcher_completion_deadline_ms":
            item["launcher_completion_deadline_ms"],
        "projection_deadline_ms": item["projection_deadline_ms"],
        "teardown_deadline_ms": item["teardown_deadline_ms"],
        "policy": {
            "path": item["path"], "file_sha256": item["file_sha256"],
            "body_sha256": item["body_sha256"],
        },
        "launcher_receipt_path": str(
            EVIDENCE_ROOT / f"launcher-{index + 1:02d}.json"),
        "verified_closure_path": str(
            EVIDENCE_ROOT / f"closure-{index + 1:02d}.json"),
        "artifact_root": str(EVIDENCE_ROOT / f"formal-{index + 1:02d}"),
    } for index, item in enumerate(formal_records)]
    runtime_document = AUDITOR.seal({
        "schema": AUDITOR.CAMPAIGN_RUNTIME_SCHEMA, "version": 1,
        "status": "FROZEN", "campaign_id": CAMPAIGN_ID, "round": 114,
        "boot_id": BOOT_ID, "issued_at_ms": FROZEN_WALL_MS,
        "expires_at_ms": bundle_document["expires_at_ms"],
        "freeze_bundle": AUDITOR._reference(freeze_artifact),
        "campaign_spec": AUDITOR._reference(spec_artifact),
        "fault_plan": AUDITOR._reference(plan_artifact),
        "pin_formal_campaign_id": runtime_formals[0]["formal_campaign_id"],
        "formal_campaigns": runtime_formals,
        "observer_cadence_ms": GAP_NS // 1_000_000,
        "maximum_slot_lateness_ms": 60_000,
        "state_root": "/var/lib/hepta/p1-runtime",
        "raw_observation_directory": "/var/lib/hepta/p1-runtime/raw",
        "recorder_root": "/var/lib/hepta/p1-runtime/recorder",
        "injector_journal_directory": "/var/lib/hepta/p1-runtime/journal",
        "injector_output_directory": "/var/lib/hepta/p1-runtime/injector",
        "control_directory": "/var/lib/hepta/p1-runtime/control",
        "executables": {
            item["role"]: {
                "path": item["installed_path"],
                "file_sha256": item["file_sha256"],
            }
            for item in source_pins
        },
        "paper_authorized": False, "live_authorized": False,
        "mutation_authorized": False, "direct_broker_access": False,
    })
    runtime_artifact = artifact("campaign_runtime", 0, runtime_document)
    closure_artifacts = [artifact(
        "verified_closure", index,
        verified_closure(index, slots, catch_up=catch_up))
        for index, (_record, slots) in enumerate(schedules)]
    launcher_artifacts = [artifact(
        "launcher_receipt", index,
        launcher_receipt(
            closure, index, formal_records[index], launcher_status))
        for index, closure in enumerate(closure_artifacts)]

    decisions: list[AUDITOR.Artifact] = []
    service_anchor_indexes: set[int] = set()
    previous: str | None = None
    sequence = 0
    eligible_set = set(eligible_schedule)
    allowed_eligible_days = set(days[:eligible_day_count])
    for formal_index, (_record, slots) in enumerate(schedules):
        closure_artifact = closure_artifacts[formal_index]
        anchor_wall = closure_artifact.document["iterations"][-1][
            "evaluated_at_ms"] + 1
        anchor_index = (
            (anchor_wall - FORMAL_SCHEDULED_MS) * 1_000_000 + GAP_NS - 1
        ) // GAP_NS
        service_anchor_indexes.add(anchor_index)
        clock_reference = observer_reference(
            service_observer_artifact(anchor_index))
        for within_formal, scheduled in enumerate(slots):
            sequence += 1
            complete = sequence > incomplete_count
            trading_day = datetime.fromtimestamp(
                scheduled / 1000,
                ZoneInfo(AUDITOR.CALENDAR_TIMEZONE)).date().isoformat()
            eligible = (
                scheduled in eligible_set and
                trading_day in allowed_eligible_days)
            wrapper_scheduled = (
                scheduled + 1
                if decision_binding_drift == "scheduled" and sequence == 1
                else scheduled)
            wrapper_evaluated = (
                scheduled + 2000
                if decision_binding_drift == "evaluated" and sequence == 1
                else wrapper_scheduled +
                    (60_001 if catch_up and sequence == 1 else 1000))
            scheduled_boot = FROZEN_BOOTTIME_NS + \
                (wrapper_scheduled - FROZEN_WALL_MS) * 1_000_000
            evaluated_boot = FROZEN_BOOTTIME_NS + \
                (wrapper_evaluated - FROZEN_WALL_MS) * 1_000_000
            decision_file_sha = (
                digest("decision-artifact-drift")
                if decision_binding_drift == "artifact" and sequence == 1
                else digest(f"decision-artifact-{sequence}"))
            evidence_sha = AUDITOR.digest_bytes(AUDITOR.canonical_bytes({
                "verified_closure_body_sha256": (
                    digest("closure-drift")
                    if decision_binding_drift == "closure" and sequence == 1
                    else closure_artifact.body_sha256),
                "closure_iteration": within_formal + 1,
                "decision_artifact_file_sha256": decision_file_sha,
                "scheduled_at_ms": wrapper_scheduled,
                "evaluated_at_ms": wrapper_evaluated,
                "clock_id": "CLOCK_BOOTTIME", "boot_id": BOOT_ID,
                "scheduled_boottime_ns": scheduled_boot,
                "evaluated_boottime_ns": evaluated_boot,
                "clock_observer_receipt": clock_reference,
                "final_outcome": "NO_TRADE",
            }))
            decision_document = AUDITOR.seal({
                "schema": "hepta.p1-safety-soak-decision-receipt.v1",
                "version": 1,
                "campaign_id": CAMPAIGN_ID,
                "sequence": sequence,
                "decision_id": f"decision-{sequence:04d}",
                "formal_campaign_id": formal_id(formal_index),
                "verified_closure_body_sha256":
                    (digest("closure-drift")
                     if decision_binding_drift == "closure" and sequence == 1
                     else closure_artifact.body_sha256),
                "closure_iteration": within_formal + 1,
                "trading_day": trading_day,
                "scheduled_at_ms": wrapper_scheduled,
                "evaluated_at_ms": wrapper_evaluated,
                "clock_id": "CLOCK_BOOTTIME", "boot_id": BOOT_ID,
                "scheduled_boottime_ns": scheduled_boot,
                "evaluated_boottime_ns": evaluated_boot,
                "clock_observer_receipt": clock_reference,
                "eligible": eligible,
                "complete": complete,
                "catch_up": catch_up and sequence == 1,
                "outcome": (
                    "TRADE_CANDIDATE"
                    if decision_binding_drift == "outcome" and sequence == 1
                    else "NO_TRADE"),
                "source_manifest_sha256": (
                    digest("drift") if source_drift and sequence == 1 else
                    SOURCE_SHA),
                "policy_sha256": POLICY_SHA,
                "strategy_sha256": STRATEGY_SHA,
                "decision_artifact_file_sha256": decision_file_sha,
                "evidence_sha256": evidence_sha,
                "previous_receipt_body_sha256": (
                    digest("broken-predecessor")
                    if broken_decision_chain and sequence == 2 else previous),
                "audit_failure": False,
                "cleanup_failure": False,
                "paper_authorized": False,
                "live_authorized": False,
                "mutation_authorized": False,
                "direct_broker_access": False,
            })
            current = artifact("decision_receipt", sequence, decision_document)
            decisions.append(current)
            previous = current.body_sha256

    if decision_set_drift == "missing":
        decisions.pop()
    elif decision_set_drift in {"duplicate", "extra"}:
        body = dict(decisions[-1].document)
        body.pop("body_sha256")
        next_sequence = len(decisions) + 1
        extra_delta = AUDITOR.POLICY_SLOT_INTERVAL_MS
        body.update({
            "sequence": next_sequence,
            "decision_id": f"decision-{next_sequence:04d}",
            "closure_iteration": (
                AUDITOR.POLICY_MAXIMUM_ITERATIONS
                if decision_set_drift == "duplicate" else
                AUDITOR.POLICY_MAXIMUM_ITERATIONS + 1),
            "scheduled_at_ms": body["scheduled_at_ms"] + extra_delta,
            "evaluated_at_ms": body["evaluated_at_ms"] + extra_delta,
            "scheduled_boottime_ns":
                body["scheduled_boottime_ns"] + extra_delta * 1_000_000,
            "evaluated_boottime_ns":
                body["evaluated_boottime_ns"] + extra_delta * 1_000_000,
            "decision_artifact_file_sha256": (
                body["decision_artifact_file_sha256"]
                if decision_set_drift == "duplicate" else
                digest(f"decision-artifact-{next_sequence}")),
            "previous_receipt_body_sha256": decisions[-1].body_sha256,
        })
        bound_closure = closure_artifacts[-1].document
        iteration_index = body["closure_iteration"]
        final_outcome = (
            bound_closure["iterations"][iteration_index - 1]["final_outcome"]
            if 1 <= iteration_index <= len(bound_closure["iterations"])
            else "NO_TRADE")
        body["evidence_sha256"] = AUDITOR.digest_bytes(
            AUDITOR.canonical_bytes({
                "verified_closure_body_sha256":
                    body["verified_closure_body_sha256"],
                "closure_iteration": iteration_index,
                "decision_artifact_file_sha256":
                    body["decision_artifact_file_sha256"],
                "scheduled_at_ms": body["scheduled_at_ms"],
                "evaluated_at_ms": body["evaluated_at_ms"],
                "clock_id": body["clock_id"], "boot_id": body["boot_id"],
                "scheduled_boottime_ns": body["scheduled_boottime_ns"],
                "evaluated_boottime_ns": body["evaluated_boottime_ns"],
                "clock_observer_receipt": body["clock_observer_receipt"],
                "final_outcome": final_outcome,
            }))
        decisions.append(artifact(
            "decision_receipt", next_sequence, AUDITOR.seal(body)))

    grid_origin = runtime_formals[0]["launcher_dispatch_at_ms"]
    grid_end = runtime_formals[-1]["teardown_deadline_ms"]
    grid_cadence = runtime_document["observer_cadence_ms"]
    grid_final = (grid_end - grid_origin + grid_cadence - 1) // grid_cadence
    if duration_hours is None:
        checkpoint_count = grid_final + 1
    else:
        checkpoint_count = min(
            grid_final,
            duration_hours * 60 * 60 * 1000 // grid_cadence,
        ) + 1
    checkpoints: list[AUDITOR.Artifact] = []
    observers: list[AUDITOR.Artifact] = [
        service_observer_artifact(index)
        for index in sorted(service_anchor_indexes)
    ]
    previous = None
    for index in range(checkpoint_count):
        continuity_artifact = campaign_continuity_observer_artifact(
            index, freeze_artifact, runtime_artifact,
            origin_ms=grid_origin, end_ms=grid_end,
            cadence_ms=grid_cadence,
            activation_source_sha=(
                digest("wrong-activation-source")
                if activation_source_drift else SOURCE_SHA))
        observers.append(continuity_artifact)
        continuity = continuity_artifact.document
        observed_boottime = continuity["observed_boottime_ns"]
        checkpoint_document = AUDITOR.seal({
            "schema": "hepta.p1-safety-soak-continuity-checkpoint.v1",
            "version": 1,
            "campaign_id": CAMPAIGN_ID,
            "sequence": index,
            "clock_id": "CLOCK_BOOTTIME",
            "boot_id": BOOT_ID,
            "observed_boottime_ns": observed_boottime,
            "freeze_bundle": continuity["freeze_bundle"],
            "campaign_runtime": continuity["campaign_runtime"],
            "continuity_slot_index": continuity["continuity_slot_index"],
            "continuity_scheduled_at_ms":
                continuity["continuity_scheduled_at_ms"],
            "continuity_origin_ms": continuity["continuity_origin_ms"],
            "continuity_end_ms": continuity["continuity_end_ms"],
            "continuity_cadence_ms": continuity["continuity_cadence_ms"],
            "continuity_final_slot": continuity["continuity_final_slot"],
            "continuity_is_final": continuity["continuity_is_final"],
            "catch_up": continuity["catch_up"],
            "activation_receipt": continuity["activation_receipt"],
            "activation_receipt_document":
                continuity["activation_receipt_document"],
            "export_commit": continuity["export_commit"],
            "export_commit_document": continuity["export_commit_document"],
            "export_snapshot": continuity["export_snapshot"],
            "lease_receipt": continuity["lease_receipt"],
            "lease_receipt_document": continuity["lease_receipt_document"],
            "export_receipt": continuity["export_receipt"],
            "lease_generation": continuity["lease_generation"],
            "previous_lease_generation":
                continuity["previous_lease_generation"],
            "previous_lease_receipt_body_sha256":
                continuity["previous_lease_receipt_body_sha256"],
            "gateway_identity": continuity["gateway_identity"],
            "gateway_process_identity":
                continuity["gateway_process_identity"],
            "gateway_executable_identity":
                continuity["gateway_executable_identity"],
            "gateway_profile_identity":
                continuity["gateway_profile_identity"],
            "gateway_domain_config_identity":
                continuity["gateway_domain_config_identity"],
            "supervisor_socket_identity":
                continuity["supervisor_socket_identity"],
            "custodian_identity": continuity["custodian_identity"],
            "collector_timer_identity":
                continuity["collector_timer_identity"],
            "activation_reconcile_timer_identity":
                continuity["activation_reconcile_timer_identity"],
            "tool_socket_identity": continuity["tool_socket_identity"],
            "transition_fault_id": continuity["transition_fault_id"],
            "persistent_stack_ok": continuity["persistent_stack_ok"],
            "lease_chain_ok": continuity["lease_chain_ok"],
            "connector_count": continuity["connector_count"],
            "authorized_uids": continuity["authorized_uids"],
            "paper_unit_active_count":
                continuity["paper_unit_active_count"],
            "campaign_socket_present":
                continuity["campaign_socket_present"],
            "kill_switch_engaged": continuity["kill_switch_engaged"],
            "zero_exposure": continuity["zero_exposure"],
            "source_manifest_sha256": SOURCE_SHA,
            "policy_sha256": POLICY_SHA,
            "strategy_sha256": STRATEGY_SHA,
            "previous_checkpoint_body_sha256": previous,
            "observer_receipt": observer_reference(continuity_artifact),
            "paper_authorized": False,
            "live_authorized": False,
            "mutation_authorized": False,
            "direct_broker_access": False,
        })
        current = artifact("continuity_checkpoint", index, checkpoint_document)
        checkpoints.append(current)
        previous = current.body_sha256

    fault_results: list[AUDITOR.Artifact] = []
    injection_receipts: list[AUDITOR.Artifact] = []
    previous_fault: str | None = None
    included_faults = planned_faults[:-1] if omit_fault_result else planned_faults
    for index, planned_fault in enumerate(included_faults, start=1):
        injection = planned_fault["inject_at_boottime_ns"]
        recovered = injection + 1_000_000_000
        issued_at = FROZEN_WALL_MS + (
            injection - FROZEN_BOOTTIME_NS) // 1_000_000
        injection_document = AUDITOR.seal({
            "schema": AUDITOR.FAULT_INJECTION_SCHEMA, "version": 1,
            "status": "COMPLETE", "issued_at_ms": issued_at,
            "expires_at_ms": issued_at + 60_000,
            "campaign_id": CAMPAIGN_ID,
            "source_manifest_sha256": SOURCE_SHA,
            "policy_sha256": POLICY_SHA, "strategy_sha256": STRATEGY_SHA,
            "fault_id": planned_fault["fault_id"],
            "fault_type": planned_fault["fault_type"],
            "target_id": planned_fault["target_id"],
            "clock_id": "CLOCK_BOOTTIME", "boot_id": BOOT_ID,
            "planned_injection_boottime_ns": injection,
            "actual_injection_boottime_ns": injection,
            "recovered_boottime_ns": recovered,
            "maximum_recovery_ns": planned_fault["maximum_recovery_ns"],
            "injector_id": "root-p1-fault-injector",
            "injector_uid": 0, "injector_gid": 0,
            "injection_scope": "P1_DECLARED_FAULT_ONLY",
            "action_receipt_sha256": digest(f"fault-action-{index}"),
            "pre_identity": fault_target_identity(
                "PRE", planned_fault["target_id"],
                planned_fault["fault_type"], injection - 1),
            "post_identity": fault_target_identity(
                "POST", planned_fault["target_id"],
                planned_fault["fault_type"], recovered),
            "injection_performed": True, "recovery_complete": True,
            "cleanup_complete": True, "authority_failure": False,
            "audit_failure": False, "cleanup_failure": False,
            "producer": {
                "path": str(AUDITOR.FAULT_INJECTOR_EXECUTABLE),
                "file_sha256": INJECTOR_SHA,
            },
            "production_mode": AUDITOR.FAULT_INJECTOR_PRODUCTION_MODE,
            "pins_reference": reference("fault-injector-pins"),
            "journal_predecessor_sequence": index * 8,
            "journal_predecessor_body_sha256":
                digest(f"fault-journal-predecessor-{index}"),
            "paper_authorized": False, "live_authorized": False,
            "mutation_authorized": False, "direct_broker_access": False,
        })
        injection_artifact = artifact(
            "fault_injection_receipt", index, injection_document)
        injection_receipts.append(injection_artifact)
        injection_reference = observer_reference(injection_artifact)
        fault_observation = observer_base(
            AUDITOR.FAULT_OBSERVATION_SCHEMA,
            f"fault-observer-{index:04d}", injection, 10_000 + index)
        fault_observation.pop("observed_boottime_ns")
        fault_observation.update({
            "fault_id": planned_fault["fault_id"],
            "fault_type": planned_fault["fault_type"],
            "target_id": planned_fault["target_id"],
            "injection_boottime_ns": injection,
            "recovered_boottime_ns": recovered,
            "recovery_verified": True,
            "cleanup_verified": True,
            "authority_failure": False,
            "audit_failure": False,
            "cleanup_failure": False,
            "observation_evidence": observation_evidence(
                "FAULT", recovered, injection_reference),
        })
        fault_observer = observer_artifact(
            "fault", index, AUDITOR.seal(fault_observation))
        fault_reference = observer_reference(fault_observer)
        fault_evidence_reference = {
            "role": "fault_observation",
            **fault_reference,
            "sealed": True,
        }
        fault_result_document = AUDITOR.seal({
            "schema": "hepta.p1-safety-soak-fault-result.v1",
            "version": 1,
            "campaign_id": CAMPAIGN_ID,
            "sequence": index,
            "fault_id": planned_fault["fault_id"],
            "fault_type": planned_fault["fault_type"],
            "target_id": planned_fault["target_id"],
            "injection_boottime_ns": injection,
            "recovered_boottime_ns": recovered,
            "recovery_verified": True,
            "cleanup_verified": True,
            "evidence_sha256": AUDITOR.digest_bytes(
                AUDITOR.canonical_bytes(fault_evidence_reference)),
            "observer_receipt": fault_reference,
            "previous_result_body_sha256": previous_fault,
            "authority_failure": False,
            "audit_failure": False,
            "cleanup_failure": False,
            "paper_authorized": False,
            "live_authorized": False,
            "mutation_authorized": False,
            "direct_broker_access": False,
        })
        current_fault = artifact(
            "fault_result", index, fault_result_document)
        fault_results.append(current_fault)
        observers.append(fault_observer)
        previous_fault = current_fault.body_sha256

    authority: list[AUDITOR.Artifact] = []
    previous = None
    for index in range(checkpoint_count):
        observed_boottime = checkpoints[index].document[
            "observed_boottime_ns"]
        connector_count = 1 if authority_exposure and index == 1 else 0
        authority_observation = observer_base(
            AUDITOR.AUTHORITY_OBSERVATION_SCHEMA,
            f"authority-observer-{index:04d}", observed_boottime,
            20_000 + index)
        authority_observation.update({
            "connector_count": connector_count,
            "authorized_uids": [],
            "paper_unit_active_count": 0,
            "campaign_socket_present": False,
            "kill_switch_engaged": True,
            "local_boundary_safe": connector_count == 0,
            "local_boundary_uncertain": False,
            "observation_scope": "LOCAL_HOST_BOUNDARY_ONLY",
            "authoritative_account_state_observed": False,
            "observation_evidence": observation_evidence(
                "AUTHORITY", observed_boottime),
        })
        authority_observer = observer_artifact(
            "authority", index, AUDITOR.seal(authority_observation))
        observers.append(authority_observer)
        snapshot_document = AUDITOR.seal({
            "schema": "hepta.p1-safety-soak-authority-snapshot.v1",
            "version": 1,
            "campaign_id": CAMPAIGN_ID,
            "sequence": index,
            "clock_id": "CLOCK_BOOTTIME",
            "boot_id": BOOT_ID,
            "observed_boottime_ns": observed_boottime,
            "source_manifest_sha256": SOURCE_SHA,
            "policy_sha256": POLICY_SHA,
            "strategy_sha256": STRATEGY_SHA,
            "connector_count": connector_count,
            "authorized_uids": [],
            "paper_unit_active_count": 0,
            "campaign_socket_present": False,
            "kill_switch_engaged": True,
            "local_boundary_safe": connector_count == 0,
            "local_boundary_uncertain": False,
            "observation_scope": "LOCAL_HOST_BOUNDARY_ONLY",
            "authoritative_account_state_observed": False,
            "observer_receipt": observer_reference(authority_observer),
            "previous_snapshot_body_sha256": previous,
            "paper_authorized": False,
            "live_authorized": False,
            "mutation_authorized": False,
            "direct_broker_access": False,
        })
        current = artifact("authority_snapshot", index, snapshot_document)
        authority.append(current)
        previous = current.body_sha256

    end_boottime = checkpoints[-1].document["observed_boottime_ns"]
    cleanup_subjects = [
        *(("FAULT", item["fault_id"],
           item["inject_at_boottime_ns"] + 1_000_000_000)
          for item in planned_faults),
        *(("LAUNCHER", formal_id(index), end_boottime)
          for index in range(len(formal_records))),
        ("FINAL", CAMPAIGN_ID, end_boottime + 1),
    ]
    cleanups: list[AUDITOR.Artifact] = []
    previous = None
    for index, (subject_type, subject_id, observed) in enumerate(cleanup_subjects):
        failed = cleanup_failure and subject_id == "fault-1"
        cleanup_observation = observer_base(
            AUDITOR.CLEANUP_OBSERVATION_SCHEMA,
            f"cleanup-observer-{index:04d}", observed, 30_000 + index)
        cleanup_observation.update({
            "subject_type": subject_type,
            "subject_id": subject_id,
            "watch_authority_count": 0,
            "export_residue_count": 0,
            "session_authority_count": 0,
            "paper_unit_active_count": 0,
            "campaign_socket_present": False,
            "cleanup_complete": not failed,
            "cleanup_uncertain": failed,
            "errors": ["FAULT_CLEANUP_FAILED"] if failed else [],
            "observation_evidence": observation_evidence(
                "CLEANUP", observed),
        })
        cleanup_observer = observer_artifact(
            "cleanup", index, AUDITOR.seal(cleanup_observation))
        observers.append(cleanup_observer)
        cleanup_document = AUDITOR.seal({
            "schema": "hepta.p1-safety-soak-cleanup-snapshot.v1",
            "version": 1,
            "campaign_id": CAMPAIGN_ID,
            "sequence": index,
            "clock_id": "CLOCK_BOOTTIME",
            "boot_id": BOOT_ID,
            "observed_boottime_ns": observed,
            "subject_type": subject_type,
            "subject_id": subject_id,
            "watch_authority_count": 0,
            "export_residue_count": 0,
            "session_authority_count": 0,
            "paper_unit_active_count": 0,
            "campaign_socket_present": False,
            "cleanup_complete": not failed,
            "cleanup_uncertain": failed,
            "errors": ["FAULT_CLEANUP_FAILED"] if failed else [],
            "observer_receipt": observer_reference(cleanup_observer),
            "previous_snapshot_body_sha256": previous,
            "paper_authorized": False,
            "live_authorized": False,
            "mutation_authorized": False,
            "direct_broker_access": False,
        })
        current = artifact("cleanup_snapshot", index, cleanup_document)
        cleanups.append(current)
        previous = current.body_sha256

    return {
        "campaign_spec": spec_artifact,
        "launcher_receipts": launcher_artifacts,
        "verified_closures": closure_artifacts,
        "decision_receipts": decisions,
        "continuity_checkpoints": checkpoints,
        "fault_plan": plan_artifact,
        "fault_results": fault_results,
        "authority_snapshots": authority,
        "cleanup_snapshots": cleanups,
        "observer_receipts": observers,
        "fault_injection_receipts": injection_receipts,
        "freeze_bundle": freeze_artifact,
        "campaign_runtime": runtime_artifact,
        "trading_calendar": calendar_artifact,
        "producer": {
            "path": str(AUDITOR.INSTALLED_EXECUTABLE),
            "file_sha256": AUDITOR_SHA,
        },
        "audited_at_ms": 1_800_000_000_000,
    }


def mutate_fault_companion(
    bundle: dict, fault_id: str, callback,
) -> None:
    companion_index = next(
        index for index, item in enumerate(bundle["fault_injection_receipts"])
        if item.document["fault_id"] == fault_id)
    companion = bundle["fault_injection_receipts"][companion_index]
    companion_body = copy.deepcopy(companion.document)
    callback(companion_body)
    companion = reseal_artifact(companion, companion_body)
    bundle["fault_injection_receipts"][companion_index] = companion
    companion_ref = observer_reference(companion)

    raw_index = next(
        index for index, item in enumerate(bundle["observer_receipts"])
        if item.document.get("schema") == AUDITOR.FAULT_OBSERVATION_SCHEMA and
        item.document.get("fault_id") == fault_id)
    raw = bundle["observer_receipts"][raw_index]
    raw_body = copy.deepcopy(raw.document)
    evidence = copy.deepcopy(raw_body["observation_evidence"])
    evidence.pop("body_sha256")
    evidence["fault_injection_receipt"] = companion_ref
    raw_body["observation_evidence"] = AUDITOR.seal(evidence)
    if "recovered_boottime_ns" in companion.document:
        raw_body["recovered_boottime_ns"] = \
            companion.document["recovered_boottime_ns"]
    raw = reseal_artifact(raw, raw_body)
    bundle["observer_receipts"][raw_index] = raw
    raw_ref = observer_reference(raw)

    result_index = next(
        index for index, item in enumerate(bundle["fault_results"])
        if item.document["fault_id"] == fault_id)
    result = bundle["fault_results"][result_index]
    result_body = copy.deepcopy(result.document)
    result_body["observer_receipt"] = raw_ref
    result_body["recovered_boottime_ns"] = raw.document[
        "recovered_boottime_ns"]
    result_body["evidence_sha256"] = AUDITOR.digest_bytes(
        AUDITOR.canonical_bytes({
            "role": "fault_observation", **raw_ref, "sealed": True,
        }))
    bundle["fault_results"][result_index] = reseal_artifact(
        result, result_body)


def reseal_decision_chain(bundle: dict) -> None:
    closures = {
        item.document["campaign_id"]: item.document
        for item in bundle["verified_closures"]
    }
    previous: str | None = None
    rebuilt = []
    for item in bundle["decision_receipts"]:
        body = copy.deepcopy(item.document)
        body.pop("body_sha256", None)
        body["previous_receipt_body_sha256"] = previous
        closure = closures[body["formal_campaign_id"]]
        outcome = closure["iterations"][
            body["closure_iteration"] - 1]["final_outcome"]
        body["evidence_sha256"] = AUDITOR.digest_bytes(
            AUDITOR.canonical_bytes({
                "verified_closure_body_sha256":
                    body["verified_closure_body_sha256"],
                "closure_iteration": body["closure_iteration"],
                "decision_artifact_file_sha256":
                    body["decision_artifact_file_sha256"],
                "scheduled_at_ms": body["scheduled_at_ms"],
                "evaluated_at_ms": body["evaluated_at_ms"],
                "clock_id": body["clock_id"], "boot_id": body["boot_id"],
                "scheduled_boottime_ns": body["scheduled_boottime_ns"],
                "evaluated_boottime_ns": body["evaluated_boottime_ns"],
                "clock_observer_receipt": body["clock_observer_receipt"],
                "final_outcome": outcome,
            }))
        rebuilt_item = AUDITOR.Artifact.from_document(
            item.role, item.path, AUDITOR.seal(body))
        rebuilt.append(rebuilt_item)
        previous = rebuilt_item.body_sha256
    bundle["decision_receipts"] = rebuilt


class P1SafetySoakAuditorTests(unittest.TestCase):
    def audit(self, **changes) -> dict:
        return AUDITOR.audit_evidence(**make_bundle(**changes))

    def test_activation_predecessor_lineage_is_exact(self) -> None:
        success = predecessor_activation_success()
        failure = predecessor_activation_failure()
        AUDITOR._validate_activation_predecessor_lineage(
            success, failure, "TEST_PREDECESSOR_INVALID")
        mutations = (
            ("success-file", success, "receipt_file_sha256", digest("bad")),
            ("success-schema", success, "receipt_schema", "tampered.v3"),
            ("failure-journal", failure, "journal_sha256", digest("bad")),
            ("round86-ancestor-binding", failure, "receipt_body_sha256",
             digest("bad")),
        )
        for label, original, field, value in mutations:
            with self.subTest(label=label):
                changed = copy.deepcopy(original)
                changed[field] = value
                with self.assertRaises(AUDITOR.EvidenceError):
                    AUDITOR._validate_activation_predecessor_lineage(
                        changed if original is success else success,
                        changed if original is failure else failure,
                        "TEST_PREDECESSOR_INVALID")

    def test_directory_anchor_ignores_unrelated_child_link_count_churn(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary) / "anchor-parent"
            parent.mkdir()
            before = parent.stat()
            (parent / "unrelated-child").mkdir()
            after = parent.stat()
        self.assertEqual(
            AUDITOR._directory_identity(before),
            AUDITOR._directory_identity(after))

    def test_complete_evidence_is_go_but_never_admission_authority(self) -> None:
        bundle = make_bundle()
        receipt = AUDITOR.audit_evidence(**bundle)
        self.assertEqual(receipt["verdict"], "GO")
        self.assertTrue(receipt["p1_safety_soak_gate_satisfied"])
        self.assertFalse(receipt["paper_test_admission_candidate"])
        self.assertEqual(
            receipt["safest_allowed_next_action"],
            "CONTINUE_REMAINING_PAPER_ADMISSION_GATES")
        for field in (
                "paper_authorized", "live_authorized",
                "mutation_authorized", "direct_broker_access"):
            self.assertIs(receipt[field], False)
        self.assertEqual(
            receipt["counts"]["eligible_decisions"],
            len(bundle["campaign_spec"].document[
                "eligible_scheduled_at_ms"]))
        self.assertEqual(
            sum(item["role"] == "observer_receipt"
                for item in receipt["checked_artifacts"]),
            len(bundle["observer_receipts"]))
        self.assertEqual(
            sum(item["role"] == "fault_injection_receipt"
                for item in receipt["checked_artifacts"]),
            len(bundle["fault_injection_receipts"]))

    def test_real_auditor_receipt_is_consumed_by_coordinator_contract(
            self) -> None:
        bundle = make_bundle()
        receipt = AUDITOR.audit_evidence(**bundle)

        def snapshot_from_artifact(value: AUDITOR.Artifact):
            payload = COORDINATOR.canonical_bytes(value.document)
            return COORDINATOR.Snapshot(
                path=Path(value.path), payload=payload,
                document=value.document, file_sha256=value.file_sha256,
                body_sha256=value.body_sha256,
                metadata=os.stat_result((
                    stat.S_IFREG | 0o600, 1, 1, 1, os.getuid(), os.getgid(),
                    len(payload), 1, 1, 1)))

        receipt_payload = COORDINATOR.canonical_bytes(receipt)
        receipt_snapshot = COORDINATOR.Snapshot(
            path=Path("/evidence/final-audit.json"),
            payload=receipt_payload, document=receipt,
            file_sha256=COORDINATOR.digest_bytes(receipt_payload),
            body_sha256=receipt["body_sha256"],
            metadata=os.stat_result((
                stat.S_IFREG | 0o600, 1, 1, 1, os.getuid(), os.getgid(),
                len(receipt_payload), 1, 1, 1)))
        restored = COORDINATOR.validate_final_audit(
            receipt_snapshot,
            snapshot_from_artifact(bundle["campaign_runtime"]),
            snapshot_from_artifact(bundle["freeze_bundle"]),
            snapshot_from_artifact(bundle["campaign_spec"]))
        self.assertEqual(restored, receipt)
        self.assertEqual(
            receipt["evaluated_interval"]["duration_ns"],
            (receipt["evaluated_interval"]["continuity_end_ms"] -
             receipt["evaluated_interval"]["continuity_origin_ms"]) *
                1_000_000)
        self.assertEqual(
            AUDITOR.decode_canonical_document(
                AUDITOR.canonical_bytes(receipt), "TEST_OUTPUT"), receipt)
        forged = copy.deepcopy(receipt)
        forged.pop("body_sha256")
        forged["paper_test_admission_candidate"] = True
        forged = AUDITOR.seal(forged)
        with self.assertRaisesRegex(
                AUDITOR.EvidenceError, "OUTPUT_BOUNDARY_INVALID"):
            AUDITOR.validate_audit_receipt(forged)

    def test_missing_freeze_lineage_returns_deterministic_halt(self) -> None:
        bundle = make_bundle()
        bundle["freeze_bundle"] = None
        bundle["trading_calendar"] = None
        receipt = AUDITOR.audit_evidence(**bundle)
        self.assertEqual(receipt["verdict"], "HALT")
        self.assertIn(
            "P1_AUDIT_FREEZE_LINEAGE_MISSING",
            receipt["failed_invariants"])

    def test_missing_runtime_is_no_go_and_tamper_is_halt(self) -> None:
        bundle = make_bundle()
        bundle["campaign_runtime"] = None
        receipt = AUDITOR.audit_evidence(**bundle)
        self.assertEqual(receipt["verdict"], "NO_GO")
        self.assertIn(
            "P1_AUDIT_CAMPAIGN_RUNTIME_MISSING",
            receipt["failed_invariants"])
        self.assertIsNone(receipt["campaign_runtime"])

        bundle = make_bundle()
        runtime = bundle["campaign_runtime"]
        body = copy.deepcopy(runtime.document)
        body.pop("body_sha256")
        body["observer_cadence_ms"] += 1
        bundle["campaign_runtime"] = AUDITOR.Artifact.from_document(
            runtime.role, runtime.path, AUDITOR.seal(body))
        receipt = AUDITOR.audit_evidence(**bundle)
        self.assertEqual(receipt["verdict"], "HALT")
        self.assertIn(
            "P1_AUDIT_CAMPAIGN_RUNTIME_INVALID",
            receipt["failed_invariants"])

    def test_invalid_freeze_lineage_returns_deterministic_halt(self) -> None:
        bundle = make_bundle()
        frozen = bundle["freeze_bundle"]
        body = copy.deepcopy(frozen.document)
        body.pop("body_sha256")
        body["boot_id"] = "invalid-boot-id"
        bundle["freeze_bundle"] = AUDITOR.Artifact.from_document(
            frozen.role, frozen.path, AUDITOR.seal(body))
        receipt = AUDITOR.audit_evidence(**bundle)
        self.assertEqual(receipt["verdict"], "HALT")
        self.assertIn(
            "P1_AUDIT_FREEZE_LINEAGE_INVALID",
            receipt["failed_invariants"])

    def test_every_projection_requires_exact_raw_observer_receipt(self) -> None:
        bundle = make_bundle()
        projected_reference = bundle["cleanup_snapshots"][-1].document[
            "observer_receipt"]
        raw_index = next(
            index for index, item in enumerate(bundle["observer_receipts"])
            if observer_reference(item) == projected_reference)
        bundle["observer_receipts"].pop(raw_index)
        receipt = AUDITOR.audit_evidence(**bundle)
        self.assertEqual(receipt["verdict"], "HALT")
        self.assertIn("P1_AUDIT_OBSERVER_RECEIPT_BINDING_INCOMPLETE",
                      receipt["failed_invariants"])

        for drift in ("digest", "schema"):
            with self.subTest(drift=drift):
                bundle = make_bundle()
                projected = bundle["cleanup_snapshots"][-1]
                body = copy.deepcopy(projected.document)
                if drift == "digest":
                    body["observer_receipt"]["file_sha256"] = \
                        digest("raw-observer-digest-drift")
                else:
                    body["observer_receipt"]["schema"] = \
                        AUDITOR.SERVICE_OBSERVATION_SCHEMA
                bundle["cleanup_snapshots"][-1] = reseal_artifact(
                    projected, body)
                receipt = AUDITOR.audit_evidence(**bundle)
                self.assertEqual(receipt["verdict"], "HALT")
                self.assertIn(
                    "P1_AUDIT_OBSERVER_RECEIPT_BINDING_INCOMPLETE",
                    receipt["failed_invariants"])

    def test_raw_observer_projection_state_cannot_drift(self) -> None:
        bundle = make_bundle()
        projected = bundle["authority_snapshots"][-1]
        body = copy.deepcopy(projected.document)
        body["observed_boottime_ns"] += 1
        bundle["authority_snapshots"][-1] = reseal_artifact(projected, body)
        receipt = AUDITOR.audit_evidence(**bundle)
        self.assertEqual(receipt["verdict"], "HALT")
        self.assertIn("P1_AUDIT_OBSERVER_PROJECTION_DRIFT",
                      receipt["failed_invariants"])

    def test_campaign_export_projection_is_raw_and_observed(self) -> None:
        freeze = artifact(
            "projection-freeze", 0,
            AUDITOR.seal({"schema": "test.projection-freeze.v1"}))
        runtime = artifact(
            "projection-runtime", 0,
            AUDITOR.seal({"schema": AUDITOR.CAMPAIGN_RUNTIME_SCHEMA}))
        raw = campaign_continuity_observer_artifact(
            0, freeze, runtime,
            origin_ms=FORMAL_SCHEDULED_MS,
            end_ms=FORMAL_SCHEDULED_MS + AUDITOR.POLICY_SLOT_INTERVAL_MS,
            cadence_ms=AUDITOR.POLICY_SLOT_INTERVAL_MS)
        spec = SimpleNamespace(
            campaign_id=CAMPAIGN_ID,
            source_manifest_sha256=SOURCE_SHA,
            policy_sha256=POLICY_SHA,
            strategy_sha256=STRATEGY_SHA,
            freeze_bundle=AUDITOR._reference(freeze))
        AUDITOR.validate_observer_artifact(raw, spec)

        projected = copy.deepcopy(raw.document)
        for field in (
                "export_commit", "export_commit_document",
                "export_snapshot", "export_receipt"):
            with self.subTest(projection_field=field):
                drift = copy.deepcopy(projected)
                drift[field] = {"unobserved": field}
                self.assertFalse(AUDITOR._observer_projection_matches(
                    drift, raw.document))

        body = copy.deepcopy(raw.document)
        body["export_snapshot"]["path"] = (
            "/run/hepta-shadow-watch-export-alpha/generations/"
            "generation-00000000000000000001-unobserved/snapshot.json")
        with self.assertRaisesRegex(
                AUDITOR.EvidenceError,
                "P1_AUDIT_RAW_CAMPAIGN_CONTINUITY_INVALID"):
            AUDITOR.validate_observer_artifact(
                reseal_artifact(raw, body), spec)

        body = copy.deepcopy(raw.document)
        evidence = dict(body["observation_evidence"])
        evidence.pop("body_sha256")
        paths = copy.deepcopy(evidence["paths"])
        commit_path = body["export_commit"]["path"]
        identity = next(item for item in paths
                        if item["path"] == commit_path)
        identity_body = dict(identity)
        identity_body.pop("state_sha256")
        identity_body["mode"] = 0o600
        paths[paths.index(identity)] = state_seal(identity_body)
        evidence["paths"] = paths
        body["observation_evidence"] = AUDITOR.seal(evidence)
        with self.assertRaisesRegex(
                AUDITOR.EvidenceError,
                "P1_AUDIT_RAW_CAMPAIGN_CONTINUITY_INVALID"):
            AUDITOR.validate_observer_artifact(
                reseal_artifact(raw, body), spec)

        body = copy.deepcopy(raw.document)
        evidence = dict(body["observation_evidence"])
        evidence.pop("body_sha256")
        evidence["paths"] = [
            item for item in evidence["paths"]
            if item["path"] != body["export_commit"]["path"]
        ]
        body["observation_evidence"] = AUDITOR.seal(evidence)
        with self.assertRaisesRegex(
                AUDITOR.EvidenceError,
                "P1_AUDIT_RAW_CAMPAIGN_CONTINUITY_INVALID"):
            AUDITOR.validate_observer_artifact(
                reseal_artifact(raw, body), spec)

    def test_unused_or_replayed_raw_observer_is_halt(self) -> None:
        bundle = make_bundle()
        unused = observer_base(
            AUDITOR.SERVICE_OBSERVATION_SCHEMA, "unused-observer",
            START_BOOTTIME_NS, 50_000)
        unused.update({
            "service_epoch": "epoch-1", "fencing_generation": 7,
            "lease_generation": 11, "transition_fault_id": None,
            "continuity_ok": True, "audit_ok": True, "cleanup_ok": True,
            "observation_evidence": observation_evidence(
                "SERVICE", START_BOOTTIME_NS),
        })
        bundle["observer_receipts"].append(observer_artifact(
            "unused", 9999, AUDITOR.seal(unused)))
        receipt = AUDITOR.audit_evidence(**bundle)
        self.assertEqual(receipt["verdict"], "HALT")
        self.assertIn("P1_AUDIT_OBSERVER_RECEIPT_BINDING_INCOMPLETE",
                      receipt["failed_invariants"])

        bundle = make_bundle()
        bundle["observer_receipts"].append(bundle["observer_receipts"][0])
        receipt = AUDITOR.audit_evidence(**bundle)
        self.assertEqual(receipt["verdict"], "HALT")
        self.assertIn("P1_AUDIT_OBSERVER_RECEIPT_REPLAY",
                      receipt["failed_invariants"])

        bundle = make_bundle()
        projected = bundle["cleanup_snapshots"][-1]
        body = copy.deepcopy(projected.document)
        body["observer_receipt"] = copy.deepcopy(
            bundle["cleanup_snapshots"][0].document["observer_receipt"])
        bundle["cleanup_snapshots"][-1] = reseal_artifact(projected, body)
        receipt = AUDITOR.audit_evidence(**bundle)
        self.assertEqual(receipt["verdict"], "HALT")
        self.assertIn("P1_AUDIT_OBSERVER_RECEIPT_REPLAY",
                      receipt["failed_invariants"])

    def test_fault_injection_companion_is_reopened_one_to_one(self) -> None:
        bundle = make_bundle()
        bundle["fault_injection_receipts"].pop()
        receipt = AUDITOR.audit_evidence(**bundle)
        self.assertEqual(receipt["verdict"], "HALT")
        self.assertIn(
            "P1_AUDIT_FAULT_INJECTION_RECEIPT_BINDING_INCOMPLETE",
            receipt["failed_invariants"])

        bundle = make_bundle()
        bundle["fault_injection_receipts"].append(
            bundle["fault_injection_receipts"][0])
        receipt = AUDITOR.audit_evidence(**bundle)
        self.assertEqual(receipt["verdict"], "HALT")
        self.assertIn("P1_AUDIT_FAULT_INJECTION_RECEIPT_REPLAY",
                      receipt["failed_invariants"])

    def test_every_declared_fault_requires_non_noop_target_transition(
            self) -> None:
        fault_ids = [f"fault-{index}" for index in range(1, 8)]
        for fault_id in fault_ids:
            with self.subTest(fault_id=fault_id):
                bundle = make_bundle()

                def no_op(document: dict) -> None:
                    pre = copy.deepcopy(document["pre_identity"])
                    original_post = document["post_identity"]
                    pre.pop("body_sha256")
                    pre["phase"] = "POST"
                    pre["observed_boottime_ns"] = original_post[
                        "observed_boottime_ns"]
                    document["post_identity"] = AUDITOR.seal(pre)

                mutate_fault_companion(bundle, fault_id, no_op)
                receipt = AUDITOR.audit_evidence(**bundle)
                self.assertEqual(receipt["verdict"], "HALT")
                self.assertIn(
                    "P1_AUDIT_FAULT_INJECTION_RECEIPT_INVALID",
                    receipt["failed_invariants"])

    def test_less_than_seventy_two_real_boottime_hours_is_no_go(self) -> None:
        receipt = self.audit(duration_hours=71)
        self.assertNotEqual(receipt["verdict"], "GO")
        self.assertIn("P1_AUDIT_BOOTTIME_DURATION_BELOW_MINIMUM",
                      receipt["failed_invariants"])

    def test_seventy_two_hours_cannot_cover_unobserved_trading_days(self) \
            -> None:
        receipt = self.audit(duration_hours=72, eligible_day_count=0)
        self.assertNotEqual(receipt["verdict"], "GO")
        self.assertIn(
            "P1_AUDIT_TRADING_DAY_COVERAGE_INVALID",
            receipt["failed_invariants"])
        self.assertIn(
            "P1_AUDIT_CONTINUITY_GRID_INCOMPLETE",
            receipt["failed_invariants"])

    def test_cross_boot_splice_is_halt(self) -> None:
        bundle = make_bundle()
        first = bundle["decision_receipts"][0]
        body = copy.deepcopy(first.document)
        body.pop("body_sha256")
        body["boot_id"] = "00000000-0000-0000-0000-000000000002"
        bundle["decision_receipts"][0] = AUDITOR.Artifact.from_document(
            first.role, first.path, AUDITOR.seal(body))
        reseal_decision_chain(bundle)
        receipt = AUDITOR.audit_evidence(**bundle)
        self.assertEqual(receipt["verdict"], "HALT")
        self.assertIn(
            "P1_AUDIT_FREEZE_BOOT_SPLICE", receipt["failed_invariants"])

    def test_wall_clock_jump_in_campaign_chain_is_halt(self) -> None:
        bundle = make_bundle()
        continuity_indexes = [
            offset for offset, item in enumerate(bundle["observer_receipts"])
            if item.document.get("schema") ==
                AUDITOR.CAMPAIGN_CONTINUITY_OBSERVATION_SCHEMA
        ]
        self.assertGreater(len(continuity_indexes), 2)
        index = continuity_indexes[1]
        raw = bundle["observer_receipts"][index]
        body = copy.deepcopy(raw.document)
        body.pop("body_sha256")
        body["observed_at_ms"] += 60_000
        body["expires_at_ms"] += 60_000
        bundle["observer_receipts"][index] = AUDITOR.Artifact.from_document(
            raw.role, raw.path, AUDITOR.seal(body))
        receipt = AUDITOR.audit_evidence(**bundle)
        self.assertEqual(receipt["verdict"], "HALT")
        self.assertIn(
            "P1_AUDIT_CAMPAIGN_WALL_BOOTTIME_CHAIN_INVALID",
            receipt["failed_invariants"])

    def test_decision_clock_anchor_drift_is_halt(self) -> None:
        bundle = make_bundle()
        item = bundle["decision_receipts"][0]
        body = copy.deepcopy(item.document)
        body.pop("body_sha256")
        body["clock_observer_receipt"] = {
            "path": "/evidence/unknown-clock.json",
            "file_sha256": digest("unknown-clock-file"),
            "body_sha256": digest("unknown-clock-body"),
        }
        bundle["decision_receipts"][0] = AUDITOR.Artifact.from_document(
            item.role, item.path, AUDITOR.seal(body))
        reseal_decision_chain(bundle)
        receipt = AUDITOR.audit_evidence(**bundle)
        self.assertEqual(receipt["verdict"], "HALT")
        self.assertIn(
            "P1_AUDIT_DECISION_CLOCK_OBSERVER_REFERENCE_INVALID",
            receipt["failed_invariants"])

    def test_receipt_cannot_self_declare_ineligible_schedule(self) -> None:
        receipt = self.audit(eligible_day_count=0)
        self.assertEqual(receipt["verdict"], "HALT")
        self.assertIn("P1_AUDIT_DECISION_ELIGIBILITY_BINDING_INVALID",
                      receipt["failed_invariants"])

    def test_at_or_below_99_percent_complete_is_no_go(self) -> None:
        receipt = self.audit(incomplete_count=25)
        self.assertEqual(
            receipt["completeness"]["ppm"],
            receipt["counts"]["complete_eligible_decisions"] * 1_000_000 //
            receipt["counts"]["eligible_decisions"])
        self.assertFalse(
            receipt["completeness"]["strictly_greater_than_99_percent"])
        self.assertEqual(receipt["verdict"], "NO_GO")

    def test_greater_than_99_percent_complete_can_go(self) -> None:
        receipt = self.audit(incomplete_count=2)
        self.assertEqual(
            receipt["completeness"]["ppm"],
            receipt["counts"]["complete_eligible_decisions"] * 1_000_000 //
            receipt["counts"]["eligible_decisions"])
        self.assertEqual(receipt["verdict"], "GO")

    def test_catch_up_decision_is_no_go(self) -> None:
        receipt = self.audit(catch_up=True)
        self.assertEqual(receipt["verdict"], "NO_GO")
        self.assertEqual(receipt["counts"]["catch_up_decisions"], 1)

    def test_authority_exposure_is_halt(self) -> None:
        receipt = self.audit(authority_exposure=True)
        self.assertEqual(receipt["verdict"], "HALT")
        self.assertIn("P1_AUDIT_AUTHORITY_EXPOSURE_OR_UNCERTAINTY",
                      receipt["failed_invariants"])
        self.assertEqual(
            receipt["exposure_summary"]["maximum_connector_count"], 1)

    def test_cleanup_failure_is_halt(self) -> None:
        receipt = self.audit(cleanup_failure=True)
        self.assertEqual(receipt["verdict"], "HALT")
        self.assertIn("P1_AUDIT_CLEANUP_FAILURE_OR_UNCERTAINTY",
                      receipt["failed_invariants"])
        self.assertFalse(receipt["cleanup_status"]["complete"])

    def test_hash_chain_gap_is_halt(self) -> None:
        receipt = self.audit(broken_decision_chain=True)
        self.assertEqual(receipt["verdict"], "HALT")
        self.assertIn("P1_AUDIT_DECISION_HASH_CHAIN_GAP",
                      receipt["failed_invariants"])

    def test_frozen_lineage_drift_is_halt(self) -> None:
        receipt = self.audit(source_drift=True)
        self.assertEqual(receipt["verdict"], "HALT")
        self.assertIn("P1_AUDIT_DECISION_FROZEN_LINEAGE_DRIFT",
                      receipt["failed_invariants"])

    def test_activation_install_source_lineage_mismatch_is_halt(self) -> None:
        receipt = self.audit(activation_source_drift=True)
        self.assertEqual(receipt["verdict"], "HALT")
        self.assertIn(
            "P1_AUDIT_CHECKPOINT_ACTIVATION_INSTALL_LINEAGE_INVALID",
            receipt["failed_invariants"])

    def test_decision_wrappers_must_match_verified_closure_one_to_one(
            self) -> None:
        for drift in ("closure", "artifact", "scheduled", "evaluated",
                      "outcome"):
            with self.subTest(drift=drift):
                receipt = self.audit(decision_binding_drift=drift)
                self.assertEqual(receipt["verdict"], "HALT")
                self.assertIn("P1_AUDIT_DECISION_CLOSURE_BINDING_DRIFT",
                              receipt["failed_invariants"])
        expected = {
            "missing": "P1_AUDIT_DECISION_CLOSURE_SET_INCOMPLETE",
            "duplicate": "P1_AUDIT_DUPLICATE_DECISION_CLOSURE_BINDING",
            "extra": "P1_AUDIT_DECISION_CLOSURE_SET_INCOMPLETE",
        }
        for drift, reason in expected.items():
            with self.subTest(set_drift=drift):
                receipt = self.audit(decision_set_drift=drift)
                self.assertNotEqual(receipt["verdict"], "GO")
                self.assertIn(reason, receipt["failed_invariants"])

    def test_frozen_trading_calendar_digest_is_machine_verified(self) -> None:
        bundle = make_bundle()
        body = copy.deepcopy(bundle["campaign_spec"].document)
        body.pop("body_sha256")
        body["trading_calendar_sha256"] = digest("calendar-drift")
        bundle["campaign_spec"] = artifact(
            "campaign_spec", 0, AUDITOR.seal(body))
        receipt = AUDITOR.audit_evidence(**bundle)
        self.assertEqual(receipt["verdict"], "HALT")
        self.assertIn(
            "P1_AUDIT_FREEZE_LINEAGE_INVALID",
            receipt["failed_invariants"])

    def test_frozen_eligible_schedule_is_exact_and_calendar_bounded(self) -> None:
        for drift in ("duplicate", "outside-calendar"):
            with self.subTest(drift=drift):
                bundle = make_bundle()
                body = copy.deepcopy(bundle["campaign_spec"].document)
                body.pop("body_sha256")
                schedule = body["eligible_scheduled_at_ms"]
                if drift == "duplicate":
                    schedule[-1] = schedule[-2]
                else:
                    schedule[-1] += 30 * 24 * 60 * 60 * 1000
                bundle["campaign_spec"] = artifact(
                    "campaign_spec", 0, AUDITOR.seal(body))
                with self.assertRaisesRegex(
                        AUDITOR.EvidenceError,
                        "SPEC_ELIGIBLE_SCHEDULE_INVALID"):
                    AUDITOR.audit_evidence(**bundle)

    def test_missing_fault_result_is_no_go(self) -> None:
        receipt = self.audit(omit_fault_result=True)
        self.assertEqual(receipt["verdict"], "NO_GO")
        self.assertIn("P1_AUDIT_FAULT_RESULT_SET_INCOMPLETE",
                      receipt["failed_invariants"])

    def test_fault_plan_requires_full_type_coverage_and_exact_targets(
            self) -> None:
        for drift in ("missing", "duplicate-type"):
            with self.subTest(drift=drift):
                receipt = self.audit(fault_type_coverage=drift)
                self.assertEqual(receipt["verdict"], "HALT")
                self.assertIn(
                    "P1_AUDIT_FAULT_TYPE_COVERAGE_INCOMPLETE",
                    receipt["failed_invariants"])
        receipt = self.audit(fault_target_drift=True)
        self.assertEqual(receipt["verdict"], "HALT")
        self.assertIn("P1_AUDIT_PLANNED_FAULT_INVALID",
                      receipt["failed_invariants"])
        expected = {
            "excessive-lateness": "P1_AUDIT_PLANNED_FAULT_INVALID",
            "overlap": "P1_AUDIT_FAULT_PLAN_OVERLAP",
            "wrong-formal": "P1_AUDIT_FREEZE_LINEAGE_INVALID",
            "outside-formal": "P1_AUDIT_FREEZE_LINEAGE_INVALID",
        }
        for drift, reason in expected.items():
            with self.subTest(timing_drift=drift):
                receipt = self.audit(fault_timing_drift=drift)
                self.assertEqual(receipt["verdict"], "HALT")
                self.assertIn(reason, receipt["failed_invariants"])

    def test_cross_formal_gap_is_rejected(self) -> None:
        receipt = self.audit(formal_gap_ms=AUDITOR.POLICY_SLOT_INTERVAL_MS)
        self.assertEqual(receipt["verdict"], "HALT")
        self.assertTrue(any(
            item in receipt["failed_invariants"] for item in (
                "P1_AUDIT_FREEZE_LINEAGE_INVALID",
                "P1_AUDIT_CAMPAIGN_RUNTIME_INVALID")))

    def test_safe_failed_launcher_is_no_go(self) -> None:
        receipt = self.audit(launcher_status="FAILED_CLOSED")
        self.assertEqual(receipt["verdict"], "NO_GO")
        self.assertIn("P1_AUDIT_LAUNCHER_NOT_FORMAL_COMPLETE",
                      receipt["failed_invariants"])

    def test_duplicate_keys_noncanonical_and_invalid_utf8_are_rejected(self) -> None:
        duplicate = b'{"a":1,"a":2,"body_sha256":"sha256:' + b"0" * 64 + b'"}\n'
        with self.assertRaisesRegex(AUDITOR.AuditError, "DUPLICATE_JSON_KEY"):
            AUDITOR.decode_canonical_document(duplicate, "TEST")
        valid = AUDITOR.canonical_bytes(AUDITOR.seal({"schema": "test.v1"}))
        with self.assertRaisesRegex(AUDITOR.AuditError, "NOT_CANONICAL"):
            AUDITOR.decode_canonical_document(b" " + valid, "TEST")
        with self.assertRaisesRegex(AUDITOR.AuditError, "JSON_INVALID"):
            AUDITOR.decode_canonical_document(b"\xff", "TEST")

    def test_secure_read_rejects_symlink_and_mid_read_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            root.chmod(0o700)
            target = root / "target.json"
            target.write_bytes(AUDITOR.canonical_bytes(AUDITOR.seal({
                "schema": "test.v1", "padding": "x" * 70000})))
            target.chmod(0o600)
            link = root / "link.json"
            link.symlink_to(target)
            with self.assertRaises(AUDITOR.AuditError):
                AUDITOR.secure_read(link, "TEST_LINK")

            replacement = root / "replacement.json"
            replacement.write_bytes(AUDITOR.canonical_bytes(AUDITOR.seal({
                "schema": "test.v1", "padding": "y" * 70000})))
            replacement.chmod(0o600)
            real_read = AUDITOR.os.read
            replaced = False

            def swapping_read(descriptor: int, count: int) -> bytes:
                nonlocal replaced
                data = real_read(descriptor, count)
                if not replaced:
                    replaced = True
                    os.replace(replacement, target)
                return data

            with mock.patch.object(AUDITOR.os, "read", side_effect=swapping_read):
                with self.assertRaisesRegex(
                        AUDITOR.AuditError, "CANONICAL_REOPEN_FAILED"):
                    AUDITOR.secure_read(target, "TEST_SWAP")

    def test_secure_read_allows_unrelated_parent_directory_activity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            root.chmod(0o700)
            target = root / "target.json"
            target.write_bytes(AUDITOR.canonical_bytes(AUDITOR.seal({
                "schema": "test.v1", "padding": "x" * 70000})))
            target.chmod(0o600)
            real_read = AUDITOR.os.read
            touched = False

            def sibling_write(descriptor: int, count: int) -> bytes:
                nonlocal touched
                data = real_read(descriptor, count)
                if not touched:
                    touched = True
                    sibling = root / "unrelated"
                    sibling.write_bytes(b"unrelated")
                    sibling.unlink()
                return data

            with mock.patch.object(
                    AUDITOR.os, "read", side_effect=sibling_write):
                payload = AUDITOR.secure_read(target, "TEST_SIBLING")
            self.assertEqual(payload, target.read_bytes())

    def test_atomic_noreplace_0600_publish_and_post_reopen(self) -> None:
        receipt = self.audit()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            root.chmod(0o700)
            output = root / "audit.json"
            published = AUDITOR.publish_receipt(receipt, output, [])
            self.assertEqual(published, AUDITOR.digest_bytes(output.read_bytes()))
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
            self.assertEqual(output.read_bytes(), AUDITOR.canonical_bytes(receipt))
            with self.assertRaisesRegex(
                    AUDITOR.AuditError, "OUTPUT_ALREADY_EXISTS"):
                AUDITOR.publish_receipt(receipt, output, [])

    def test_publish_fsync_and_post_reopen_fail_closed(self) -> None:
        receipt = self.audit()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            root.chmod(0o700)
            output = root / "fsync-failure.json"
            with mock.patch.object(
                    AUDITOR.os, "fsync", side_effect=OSError("fsync")):
                with self.assertRaisesRegex(
                        AUDITOR.AuditError, "OUTPUT_PUBLISH_FAILED"):
                    AUDITOR.publish_receipt(receipt, output, [])
            self.assertFalse(output.exists())

            post_output = root / "post-reopen-failure.json"
            with mock.patch.object(
                    AUDITOR, "secure_read", return_value=b"drift"):
                with self.assertRaisesRegex(
                        AUDITOR.AuditError, "OUTPUT_POST_VERIFY_FAILED"):
                    AUDITOR.publish_receipt(receipt, post_output, [])

    def test_cli_requires_output_and_publishes_canonical_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            root.chmod(0o700)
            with mock.patch.object(
                    sys.modules[__name__], "EVIDENCE_ROOT", root):
                bundle = make_bundle()
            arguments: list[str] = ["--run"]

            def add(flag: str, item: AUDITOR.Artifact, index: int) -> None:
                del index
                path = Path(item.path)
                path.write_bytes(AUDITOR.canonical_bytes(item.document))
                path.chmod(0o600)
                arguments.extend((flag, str(path)))

            add("--campaign-spec", bundle["campaign_spec"], 0)
            add("--campaign-runtime", bundle["campaign_runtime"], 0)
            add("--fault-plan", bundle["fault_plan"], 0)
            for item in (bundle["freeze_bundle"],
                         bundle["trading_calendar"]):
                path = Path(item.path)
                path.write_bytes(AUDITOR.canonical_bytes(item.document))
                path.chmod(0o600)
            for key, flag in (
                    ("launcher_receipts", "--launcher-receipt"),
                    ("verified_closures", "--verified-closure"),
                    ("decision_receipts", "--decision-receipt"),
                    ("continuity_checkpoints", "--continuity-checkpoint"),
                    ("fault_results", "--fault-result"),
                    ("authority_snapshots", "--authority-snapshot"),
                    ("cleanup_snapshots", "--cleanup-snapshot"),
                    ("observer_receipts", "--observer-receipt"),
                    ("fault_injection_receipts",
                     "--fault-injection-receipt")):
                for index, item in enumerate(bundle[key]):
                    add(flag, item, index)
            output = root / "p1-audit.json"
            arguments.extend(("--output", str(output)))
            stdout = SimpleNamespace(buffer=io.BytesIO())
            producer = SimpleNamespace(
                reference=bundle["producer"], reopen=lambda: None)
            with mock.patch.object(AUDITOR.sys, "stdout", stdout), \
                    mock.patch.object(
                        AUDITOR, "bind_executing_image",
                        return_value=producer), \
                    mock.patch.object(
                        AUDITOR, "assert_installed_source_pins"), \
                    mock.patch.object(
                        AUDITOR, "ROOT_UID", AUDITOR.os.geteuid()), \
                    mock.patch.object(
                        AUDITOR, "ROOT_GID", AUDITOR.os.getegid()):
                self.assertEqual(AUDITOR.main(arguments), 0)
            self.assertEqual(stdout.buffer.getvalue(), output.read_bytes())
            restored = AUDITOR.decode_canonical_document(
                output.read_bytes(), "TEST_CLI_OUTPUT")
            self.assertEqual(restored["verdict"], "GO")
            self.assertFalse(restored["paper_test_admission_candidate"])
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)

    def test_publish_reopens_inputs_and_rejects_drift(self) -> None:
        receipt = self.audit()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            root.chmod(0o700)
            input_path = root / "input.json"
            original = AUDITOR.seal({"schema": "test-input.v1"})
            input_path.write_bytes(AUDITOR.canonical_bytes(original))
            input_path.chmod(0o600)
            snapshot = AUDITOR.load_artifact(input_path, "test_input")
            drifted = AUDITOR.seal({"schema": "test-input.v1", "drift": True})
            input_path.write_bytes(AUDITOR.canonical_bytes(drifted))
            input_path.chmod(0o600)
            with self.assertRaisesRegex(AUDITOR.AuditError, "DRIFT"):
                AUDITOR.publish_receipt(
                    receipt, root / "audit.json", [snapshot])
            self.assertFalse((root / "audit.json").exists())


if __name__ == "__main__":
    unittest.main()
