#!/usr/bin/env python3

"""Offline seams for the round114 WATCH-to-PAPER handoff transaction."""

from __future__ import annotations

from contextlib import ExitStack
import copy
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_SOURCE = ROOT / "scripts/hepta_p1_watch_to_paper_handoff.py"
MODULE_DIRECTORY = tempfile.TemporaryDirectory(dir=ROOT)
SOURCE = Path(MODULE_DIRECTORY.name) / "hepta-p1-watch-to-paper-handoff.py"
shutil.copyfile(REPOSITORY_SOURCE, SOURCE)
SOURCE.chmod(0o755)
unittest.addModuleCleanup(MODULE_DIRECTORY.cleanup)
SPEC = importlib.util.spec_from_file_location("hepta_watch_handoff", SOURCE)
assert SPEC is not None and SPEC.loader is not None
HANDOFF = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = HANDOFF
SPEC.loader.exec_module(HANDOFF)

VERIFIER_SOURCE = ROOT / "scripts/hepta_p1_paper_admission_verifier.py"
VERIFIER_SPEC = importlib.util.spec_from_file_location(
    "hepta_paper_admission_for_handoff_tests", VERIFIER_SOURCE)
assert VERIFIER_SPEC is not None and VERIFIER_SPEC.loader is not None
VERIFIER = importlib.util.module_from_spec(VERIFIER_SPEC)
sys.modules[VERIFIER_SPEC.name] = VERIFIER
VERIFIER_SPEC.loader.exec_module(VERIFIER)


SHA = "sha256:" + "1" * 64
SOURCE_SHA = "sha256:" + "a" * 64
CAMPAIGN = "p1-shadow-20260803-alpha"
AUDITOR_TEST_PAYLOAD = b"#!/bin/sh\nexit 3\n"
FREEZE_REFERENCE = {
    "path": "/evidence/freeze-bundle.json",
    "file_sha256": "sha256:" + "b" * 64,
    "body_sha256": "sha256:" + "c" * 64,
}


def profile_file_evidence(
    path: Path, sha256: str, size: int, mode: int, inode: int,
) -> dict[str, object]:
    return {
        "path": str(path), "file_sha256": sha256, "bytes": size,
        "mode": stat.S_IFREG | mode, "uid": HANDOFF.ROOT_UID,
        "gid": HANDOFF.ROOT_GID, "nlink": 1, "device": 8,
        "inode": inode, "mtime_ns": inode * 1000,
        "ctime_ns": inode * 1000 + 1,
    }


def profile_sealed_evidence(path: Path, inode: int) -> dict[str, object]:
    return {
        **profile_file_evidence(path, SHA, 4096, 0o600, inode),
        "body_sha256": SHA,
    }


def paper_runtime_profile_payload(*, hardened: bool) -> bytes:
    values = {
        **HANDOFF.PAPER_RUNTIME_PROFILE_FIXED_VALUES,
        **(HANDOFF.PAPER_RUNTIME_PROFILE_HARDENED_LIMITS if hardened else
           HANDOFF.PAPER_RUNTIME_PROFILE_LEGACY_LIMITS),
        "HEPTA_IB_PAPER_ACCOUNT": "A12345678",
    }
    return "".join(
        f"{key}={values[key]}\n" for key in
        HANDOFF.PAPER_RUNTIME_PROFILE_KEYS).encode("ascii")


def predecessor_activation_success(module=HANDOFF) -> dict[str, object]:
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


def predecessor_activation_failure(module=HANDOFF) -> dict[str, object]:
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


def activation_document() -> dict[str, object]:
    evidence: dict[str, object] = {
        field: None for field in HANDOFF.SHADOW_INSTALL_EVIDENCE_FIELDS
        if field != "body_sha256"
    }
    evidence.update({
        "schema": "hepta.shadow-runtime-install-consumption-evidence.v3",
        "version": 3, "receipt_path":
            "/var/lib/hepta/shadow-runtime-install-receipts/"
            "hepta-p1-round114-generation22-passive.json",
        "receipt_file_sha256": SHA, "receipt_body_sha256": SHA,
        "manifest_path":
            "/var/lib/hepta/shadow-runtime-install-artifacts/"
            "hepta-p1-round114-generation22-shadow-runtime.manifest.json",
        "manifest_file_sha256": SHA, "archive_sha256": SHA,
        "source_baseline_sha256": SOURCE_SHA, "installer_sha256": SHA,
        "installed_file_count": 128, "installed_paths_sha256": SHA,
        "closure_sha256": SHA, "transaction_lock": {},
        "default_deny_identity_sha256": SHA, "lock_mode": "exclusive",
        "verified_under_lock": True, "domain": "alpha",
        "backup_root": "/var/lib/hepta/shadow-runtime-backups/"
            "hepta-p1-round114-generation22-passive",
        "paper_authorized": False,
        "live_authorized": False, "mutation_attempted": False,
        "direct_broker_access": False,
        "current_install_pointer_path": "/evidence/current.json",
        "current_install_pointer_file_sha256": SHA,
        "install_generation": 22, "predecessor_install_generation": 21,
        "predecessor_current_install_pointer_file_sha256":
            "sha256:2beeb507fcafbbfc2c93d2e4756fddf0b27e9872733ff97d28af47006461d406",
    })
    body: dict[str, object] = {
        field: None for field in HANDOFF.ACTIVATION_FIELDS
        if field != "body_sha256"
    }
    body.update({
        "schema": "hepta.p1-watch-activation-receipt.v4", "version": 4,
        "status": "WATCH_GATEWAY_ACTIVATED", "round": 114,
        "domain": "alpha", "started_at_ms": 1, "completed_at_ms": 2,
        "boot_id": "00000000-0000-0000-0000-000000000001",
        "shadow_install_evidence": evidence,
        "broker_after": {
            "authorized_connectors": 0, "authorized_uids": []},
        "fresh_activation_transaction": True, "gateway_activated": True,
        "gateway_profile_loaded": True,
        "gateway_contract_binding_loaded": True,
        "broker_loaded_source_attested": True,
        "broker_deny_all_continuity_attested": True,
        "watch_authority_provisioned": False, "campaign_launched": False,
        "admission_prerequisite_satisfied": True,
        "paper_prerequisite_satisfied": False,
        "kill_switch_engaged": True, "paper_authorized": False,
        "live_authorized": False, "mutation_attempted": False,
        "direct_broker_access": False,
        "predecessor_activation_success": predecessor_activation_success(),
        "predecessor_activation_failure": predecessor_activation_failure(),
    })
    return HANDOFF.seal(body)


def audit_document() -> dict[str, object]:
    counts = {
        "launcher_receipts": 10, "verified_closures": 10,
        "continuity_checkpoints": 1000,
        "declared_trading_days": HANDOFF.MINIMUM_TRADING_DAYS,
        "observed_trading_days": HANDOFF.MINIMUM_TRADING_DAYS,
        "scheduled_decisions": 201,
        "decision_receipts": 201, "eligible_decisions": 200,
        "complete_eligible_decisions": 200,
        "incomplete_eligible_decisions": 0, "catch_up_decisions": 0,
        "planned_faults": 7, "fault_results": 7,
        "authority_snapshots": 1000, "cleanup_snapshots": 10,
    }
    body: dict[str, object] = {
        field: None for field in HANDOFF.P1_AUDIT_FIELDS
        if field != "body_sha256"
    }
    body.update({
        "schema": "hepta.p1-safety-soak-audit-receipt.v1", "version": 1,
        "phase": "P1_SHADOW", "verdict": "GO", "campaign_id": CAMPAIGN,
        "domain_id": "alpha", "independent_auditor_id": "root-auditor",
        "audited_at_ms": 1, "campaign_spec_file_sha256": SHA,
        "campaign_spec_body_sha256": SHA,
        "freeze_bundle": dict(FREEZE_REFERENCE),
        "campaign_runtime": {
            "schema": HANDOFF.P1_CAMPAIGN_RUNTIME_SCHEMA,
            "path": "/evidence/campaign-runtime.json",
            "file_sha256": SHA, "body_sha256": SHA,
        },
        "producer": {
            "path": str(HANDOFF.P1_AUDITOR_EXECUTABLE),
            "file_sha256": HANDOFF.digest_bytes(AUDITOR_TEST_PAYLOAD),
        },
        "production_mode": HANDOFF.P1_AUDITOR_PRODUCTION_MODE,
        "source_manifest_sha256": SOURCE_SHA, "policy_sha256": SHA,
        "strategy_sha256": SHA,
        "evaluated_interval": {
            "clock_id": "CLOCK_BOOTTIME",
            "boot_id": "00000000-0000-0000-0000-000000000001",
            "start_boottime_ns": 1,
            "end_boottime_ns": HANDOFF.MINIMUM_BOOTTIME_DURATION_NS + 1,
            "duration_ns": HANDOFF.MINIMUM_BOOTTIME_DURATION_NS,
            "maximum_checkpoint_gap_ns": 15 * 60 * 1_000_000_000,
            "continuity_origin_ms": 1_000_000,
            "continuity_end_ms": 1_000_000 +
                HANDOFF.MINIMUM_BOOTTIME_DURATION_NS // 1_000_000,
            "continuity_final_slot": counts["continuity_checkpoints"] - 1,
            "consecutive": True,
        },
        "counts": counts,
        "completeness": {
            "numerator": 200, "denominator": 200, "ppm": 1_000_000,
            "strictly_greater_than_99_percent": True,
        },
        "checked_artifacts": [{
            "role": "launcher_receipt", "path": "/evidence/launcher.json",
            "file_sha256": SHA, "body_sha256": SHA,
        }],
        "failed_invariants": [],
        "exposure_summary": {
            "evidence_present": True, "maximum_connector_count": 0,
            "maximum_authorized_uid_count": 0,
            "maximum_paper_unit_active_count": 0,
            "campaign_socket_ever_present": False,
            "kill_switch_continuously_engaged": True,
            "local_boundary_uncertain": False,
            "scope": "LOCAL_HOST_BOUNDARY_ONLY",
            "authoritative_account_state_observed": False,
        },
        "cleanup_status": {
            "required_subject_count": 10, "verified_subject_count": 10,
            "complete": True,
        },
        "p1_safety_soak_gate_satisfied": True,
        "paper_test_admission_candidate": False,
        "safest_allowed_next_action":
            "CONTINUE_REMAINING_PAPER_ADMISSION_GATES",
        "paper_authorized": False, "live_authorized": False,
        "mutation_authorized": False, "direct_broker_access": False,
    })
    return HANDOFF.seal(body)


def write_receipt(path: Path, document: dict[str, object]) -> None:
    path.write_bytes(HANDOFF.canonical_bytes(document))
    path.chmod(0o600)


class SimulatedCrash(BaseException):
    pass


class FakeExecutor:
    def __init__(
        self, *, fail_at: int | None = None, fail_persistently: bool = False,
        paper_active: bool = False, broker_open: bool = False,
        kill_disengaged: bool = False, residue: bool = False,
        global_kill_disengaged: bool = False, identity_count: int = 0,
        transient_active: bool = False, profile_state: str = "PRE",
        runtime_profile_state: str = "LEGACY",
    ):
        self.watch = {
            unit: {
                "load_state": "loaded", "active_state": "active",
                "sub_state": "running", "job": "",
                "unit_file_state": "enabled", "persistent_masked": False,
                "runtime_masked": False,
            }
            for unit in HANDOFF.WATCH_UNITS
        }
        self.paper_active = paper_active
        self.broker_open = broker_open
        self.kill_disengaged = kill_disengaged
        self.global_kill_disengaged = global_kill_disengaged
        self.identity_count = identity_count
        self.residue = residue
        self.transient_active = transient_active
        self.fail_at = fail_at
        self.fail_persistently = fail_persistently
        self.calls = 0
        self.mutations: list[tuple[str, str | None]] = []
        self.profile_state = profile_state
        self.runtime_profile_state = runtime_profile_state

    def _maybe_fail(self) -> None:
        self.calls += 1
        if self.fail_at == self.calls or (
                self.fail_persistently and self.fail_at is not None and
                self.calls >= self.fail_at):
            if not self.fail_persistently:
                self.fail_at = None
            raise HANDOFF.HandoffError("HANDOFF_FAKE_SYSTEMCTL_FAILED")

    def disable_and_stop(self, unit: str) -> None:
        self._maybe_fail()
        self.mutations.append(("disable", unit))
        self.watch[unit].update({
            "active_state": "inactive", "sub_state": "dead",
            "unit_file_state": "disabled",
        })

    def mask_persistent(self, unit: str) -> None:
        self._maybe_fail()
        self.mutations.append(("persistent-mask", unit))
        self.watch[unit]["persistent_masked"] = True
        self.watch[unit]["unit_file_state"] = "masked"

    def mask_runtime(self, unit: str) -> None:
        self._maybe_fail()
        self.mutations.append(("runtime-mask", unit))
        self.watch[unit]["runtime_masked"] = True
        self.watch[unit]["unit_file_state"] = "masked"

    def daemon_reload(self) -> None:
        self._maybe_fail()
        self.mutations.append(("daemon-reload", None))

    def profile_restoration_state(self) -> dict[str, object]:
        before = self.profile_state in {"PRE", "PRE_CANDIDATE"}
        target = profile_file_evidence(
            HANDOFF.PROFILE_TARGET_PATH,
            HANDOFF.WATCH_PROFILE_SHA256 if before else
                HANDOFF.DORMANT_PAPER_PROFILE_SHA256,
            HANDOFF.WATCH_PROFILE_BYTES if before else
                HANDOFF.DORMANT_PAPER_PROFILE_BYTES,
            0o644, 100 if before else 101)
        candidate = None
        retired = None
        if self.profile_state == "PRE_CANDIDATE":
            candidate = profile_file_evidence(
                HANDOFF.PROFILE_CANDIDATE_PATH,
                HANDOFF.DORMANT_PAPER_PROFILE_SHA256,
                HANDOFF.DORMANT_PAPER_PROFILE_BYTES, 0o644, 102)
        elif self.profile_state == "POST_CANDIDATE":
            candidate = profile_file_evidence(
                HANDOFF.PROFILE_CANDIDATE_PATH,
                HANDOFF.WATCH_PROFILE_SHA256,
                HANDOFF.WATCH_PROFILE_BYTES, 0o644, 100)
        elif self.profile_state == "RESTORED":
            retired = profile_file_evidence(
                HANDOFF.PROFILE_RETIRED_WATCH_PATH,
                HANDOFF.WATCH_PROFILE_SHA256,
                HANDOFF.WATCH_PROFILE_BYTES, 0o600, 100)
        return HANDOFF._validate_profile_state({
            "state": self.profile_state, "target": target,
            "dormant_backup": profile_file_evidence(
                HANDOFF.PROFILE_DORMANT_BACKUP_PATH,
                HANDOFF.DORMANT_PAPER_PROFILE_SHA256,
                HANDOFF.DORMANT_PAPER_PROFILE_BYTES, 0o600, 200),
            "forward_retained_dormant": profile_file_evidence(
                HANDOFF.PROFILE_FORWARD_RETAINED_PATH,
                HANDOFF.DORMANT_PAPER_PROFILE_SHA256,
                HANDOFF.DORMANT_PAPER_PROFILE_BYTES, 0o600, 201),
            "candidate": candidate, "retired_watch": retired,
            "forward_transition_receipt": profile_sealed_evidence(
                HANDOFF.PROFILE_FORWARD_TRANSITION_RECEIPT_PATH, 300),
            "profile_deployment_receipt": profile_sealed_evidence(
                HANDOFF.PROFILE_DEPLOYMENT_RECEIPT_PATH, 301),
            "forward_preimage_evidence": profile_sealed_evidence(
                HANDOFF.PROFILE_FORWARD_PREIMAGE_PATH, 302),
        })

    def prepare_profile_candidate(self) -> None:
        if self.profile_state != "PRE":
            raise HANDOFF.HandoffError("HANDOFF_FAKE_PROFILE_STATE_INVALID")
        self.mutations.append(("profile-candidate", None))
        self.profile_state = "PRE_CANDIDATE"

    def exchange_profile_candidate(self) -> None:
        if self.profile_state != "PRE_CANDIDATE":
            raise HANDOFF.HandoffError("HANDOFF_FAKE_PROFILE_STATE_INVALID")
        self.mutations.append(("profile-exchange", None))
        self.profile_state = "POST_CANDIDATE"

    def remove_preexchange_profile_candidate(self) -> None:
        if self.profile_state == "PRE":
            return
        if self.profile_state != "PRE_CANDIDATE":
            raise HANDOFF.HandoffError("HANDOFF_FAKE_PROFILE_STATE_INVALID")
        self.mutations.append(("profile-candidate-cleanup", None))
        self.profile_state = "PRE"

    def profile_candidate_absent(self) -> bool:
        return self.profile_state not in {"PRE_CANDIDATE", "POST_CANDIDATE"}

    def seal_retired_watch(self) -> None:
        if self.profile_state != "POST_CANDIDATE":
            raise HANDOFF.HandoffError("HANDOFF_FAKE_PROFILE_STATE_INVALID")
        self.mutations.append(("profile-retire-watch", None))
        self.profile_state = "RESTORED"

    def paper_runtime_profile_hardening_state(self) -> dict[str, object]:
        hardened = self.runtime_profile_state in {
            "HARDENED_CANDIDATE", "HARDENED"}
        target = profile_file_evidence(
            HANDOFF.PAPER_RUNTIME_PROFILE_PATH,
            (HANDOFF.HARDENED_PAPER_RUNTIME_PROFILE_SHA256 if hardened else
             HANDOFF.LEGACY_PAPER_RUNTIME_PROFILE_SHA256),
            (HANDOFF.HARDENED_PAPER_RUNTIME_PROFILE_BYTES if hardened else
             HANDOFF.LEGACY_PAPER_RUNTIME_PROFILE_BYTES),
            0o644, 401 if hardened else 400)
        backup = None
        if self.runtime_profile_state != "LEGACY":
            backup = profile_file_evidence(
                HANDOFF.PAPER_RUNTIME_PROFILE_BACKUP_PATH,
                HANDOFF.LEGACY_PAPER_RUNTIME_PROFILE_SHA256,
                HANDOFF.LEGACY_PAPER_RUNTIME_PROFILE_BYTES, 0o600, 500)
        candidate = None
        retained = None
        if self.runtime_profile_state == "LEGACY_CANDIDATE":
            candidate = profile_file_evidence(
                HANDOFF.PAPER_RUNTIME_PROFILE_CANDIDATE_PATH,
                HANDOFF.HARDENED_PAPER_RUNTIME_PROFILE_SHA256,
                HANDOFF.HARDENED_PAPER_RUNTIME_PROFILE_BYTES, 0o644, 401)
        elif self.runtime_profile_state == "HARDENED_CANDIDATE":
            candidate = profile_file_evidence(
                HANDOFF.PAPER_RUNTIME_PROFILE_CANDIDATE_PATH,
                HANDOFF.LEGACY_PAPER_RUNTIME_PROFILE_SHA256,
                HANDOFF.LEGACY_PAPER_RUNTIME_PROFILE_BYTES, 0o644, 400)
        elif self.runtime_profile_state == "HARDENED":
            retained = profile_file_evidence(
                HANDOFF.PAPER_RUNTIME_PROFILE_RETAINED_PATH,
                HANDOFF.LEGACY_PAPER_RUNTIME_PROFILE_SHA256,
                HANDOFF.LEGACY_PAPER_RUNTIME_PROFILE_BYTES, 0o600, 400)
        return HANDOFF._validate_paper_runtime_profile_state({
            "state": self.runtime_profile_state, "target": target,
            "legacy_backup": backup, "candidate": candidate,
            "retained_legacy": retained,
        })

    def backup_legacy_paper_runtime_profile(self) -> None:
        if self.runtime_profile_state != "LEGACY":
            raise HANDOFF.HandoffError("HANDOFF_FAKE_RUNTIME_PROFILE_INVALID")
        self.mutations.append(("runtime-profile-backup", None))
        self.runtime_profile_state = "LEGACY_BACKED_UP"

    def prepare_paper_runtime_profile_candidate(self) -> None:
        if self.runtime_profile_state != "LEGACY_BACKED_UP":
            raise HANDOFF.HandoffError("HANDOFF_FAKE_RUNTIME_PROFILE_INVALID")
        self.mutations.append(("runtime-profile-candidate", None))
        self.runtime_profile_state = "LEGACY_CANDIDATE"

    def exchange_paper_runtime_profile_candidate(self) -> None:
        if self.runtime_profile_state != "LEGACY_CANDIDATE":
            raise HANDOFF.HandoffError("HANDOFF_FAKE_RUNTIME_PROFILE_INVALID")
        self.mutations.append(("runtime-profile-exchange", None))
        self.runtime_profile_state = "HARDENED_CANDIDATE"

    def remove_preexchange_paper_runtime_profile_candidate(self) -> None:
        if self.runtime_profile_state in {"LEGACY", "LEGACY_BACKED_UP"}:
            return
        if self.runtime_profile_state != "LEGACY_CANDIDATE":
            raise HANDOFF.HandoffError("HANDOFF_FAKE_RUNTIME_PROFILE_INVALID")
        self.mutations.append(("runtime-profile-candidate-cleanup", None))
        self.runtime_profile_state = "LEGACY_BACKED_UP"

    def paper_runtime_profile_candidate_absent(self) -> bool:
        return self.runtime_profile_state not in {
            "LEGACY_CANDIDATE", "HARDENED_CANDIDATE"}

    def seal_retained_legacy_paper_runtime_profile(self) -> None:
        if self.runtime_profile_state != "HARDENED_CANDIDATE":
            raise HANDOFF.HandoffError("HANDOFF_FAKE_RUNTIME_PROFILE_INVALID")
        self.mutations.append(("runtime-profile-retain-legacy", None))
        self.runtime_profile_state = "HARDENED"

    def snapshot(self, *, tighten: bool = False) -> dict[str, object]:
        broker_open = self.broker_open and not tighten
        paper = {
            unit: {
                "load_state": "loaded",
                "active_state": "active" if self.paper_active else "inactive",
                "sub_state": "running" if self.paper_active else "dead",
                "job": "", "unit_file_state": "disabled",
            }
            for unit in HANDOFF.PAPER_UNITS
        }
        timers = sum(
            1 for unit in HANDOFF.WATCH_TIMER_UNITS
            if self.watch[unit]["active_state"] != "inactive")
        inactive = all(
            state["active_state"] == "inactive"
            for state in self.watch.values())
        residue = 1 if self.residue and inactive else 0
        return HANDOFF.validate_snapshot({
            "watch_units": copy.deepcopy(self.watch),
            "transient_units": ({
                "hepta-p1-shadow-host-round123.service": {
                    "load_state": "loaded", "active_state": "active",
                    "sub_state": "running", "job": "77",
                    "unit_file_state": "transient",
                }} if self.transient_active else {}),
            "paper_units": paper,
            "broker": {
                "policy_sha256": SHA,
                "authorized_connectors": 1 if broker_open else 0,
                "authorized_uids": [2101] if broker_open else [],
                "protected_ports": 4,
            },
            "kill_switch_engaged": not self.kill_disengaged,
            "global_kill_switch_engaged":
                not self.global_kill_disengaged,
            "identity_count": self.identity_count,
            "identity_manifest_sha256":
                (HANDOFF.DISABLED_IDENTITY_MANIFEST_SHA256
                 if self.identity_count == 0 else SHA),
            "watch_authority_count": residue,
            "watch_socket_count": residue, "watch_timer_count": timers,
            "cleanup_residue_count":
                residue + (1 if self.transient_active else 0),
        })


class HandoffFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir=ROOT)
        self.root = Path(self.temporary.name)
        self.state = self.root / "state"
        self.output = self.root / "handoff.json"
        self.activation = self.root / "activation.json"
        self.audit = self.root / "audit.json"
        self.auditor = self.root / "hepta-p1-safety-soak-auditor"
        self.auditor.write_bytes(AUDITOR_TEST_PAYLOAD)
        self.auditor.chmod(0o755)
        self.stack = ExitStack()
        self.backends: dict[int, FakeExecutor] = {}
        uid, gid = os.geteuid(), os.getegid()
        for name, value in {
            "ROOT_UID": uid, "ROOT_GID": gid,
            "INSTALLED_EXECUTABLE": SOURCE,
            "P1_AUDITOR_EXECUTABLE": self.auditor,
            "STATE_ROOT": self.state,
            "JOURNAL_ROOT": self.state / "journal",
            "LOCK_PATH": self.root / ".handoff.lock",
        }.items():
            self.stack.enter_context(mock.patch.object(HANDOFF, name, value))
        write_receipt(self.activation, activation_document())
        write_receipt(self.audit, audit_document())
        production = HANDOFF.ProductionExecutor
        self.stack.enter_context(mock.patch.object(
            production, "disable_and_stop",
            lambda selected, unit: self.backends[id(selected)].disable_and_stop(
                unit)))
        self.stack.enter_context(mock.patch.object(
            production, "mask_persistent",
            lambda selected, unit: self.backends[id(selected)].mask_persistent(
                unit)))
        self.stack.enter_context(mock.patch.object(
            production, "mask_runtime",
            lambda selected, unit: self.backends[id(selected)].mask_runtime(
                unit)))
        self.stack.enter_context(mock.patch.object(
            production, "daemon_reload",
            lambda selected: self.backends[id(selected)].daemon_reload()))
        self.stack.enter_context(mock.patch.object(
            production, "snapshot",
            lambda selected, *, tighten=False:
                self.backends[id(selected)].snapshot(tighten=tighten)))
        self.stack.enter_context(mock.patch.object(
            production, "profile_restoration_state",
            lambda selected:
                self.backends[id(selected)].profile_restoration_state()))
        self.stack.enter_context(mock.patch.object(
            production, "prepare_profile_candidate",
            lambda selected:
                self.backends[id(selected)].prepare_profile_candidate()))
        self.stack.enter_context(mock.patch.object(
            production, "exchange_profile_candidate",
            lambda selected:
                self.backends[id(selected)].exchange_profile_candidate()))
        self.stack.enter_context(mock.patch.object(
            production, "remove_preexchange_profile_candidate",
            lambda selected: self.backends[
                id(selected)].remove_preexchange_profile_candidate()))
        self.stack.enter_context(mock.patch.object(
            production, "profile_candidate_absent",
            lambda selected:
                self.backends[id(selected)].profile_candidate_absent()))
        self.stack.enter_context(mock.patch.object(
            production, "seal_retired_watch",
            lambda selected:
                self.backends[id(selected)].seal_retired_watch()))
        self.stack.enter_context(mock.patch.object(
            production, "paper_runtime_profile_hardening_state",
            lambda selected: self.backends[
                id(selected)].paper_runtime_profile_hardening_state()))
        self.stack.enter_context(mock.patch.object(
            production, "backup_legacy_paper_runtime_profile",
            lambda selected: self.backends[
                id(selected)].backup_legacy_paper_runtime_profile()))
        self.stack.enter_context(mock.patch.object(
            production, "prepare_paper_runtime_profile_candidate",
            lambda selected: self.backends[
                id(selected)].prepare_paper_runtime_profile_candidate()))
        self.stack.enter_context(mock.patch.object(
            production, "exchange_paper_runtime_profile_candidate",
            lambda selected: self.backends[
                id(selected)].exchange_paper_runtime_profile_candidate()))
        self.stack.enter_context(mock.patch.object(
            production, "remove_preexchange_paper_runtime_profile_candidate",
            lambda selected: getattr(
                self.backends[id(selected)],
                "remove_preexchange_paper_runtime_profile_candidate")()))
        self.stack.enter_context(mock.patch.object(
            production, "paper_runtime_profile_candidate_absent",
            lambda selected: self.backends[
                id(selected)].paper_runtime_profile_candidate_absent()))
        self.stack.enter_context(mock.patch.object(
            production, "seal_retained_legacy_paper_runtime_profile",
            lambda selected: getattr(
                self.backends[id(selected)],
                "seal_retained_legacy_paper_runtime_profile")()))

    def tearDown(self) -> None:
        self.stack.close()
        self.temporary.cleanup()

    def production_executor(
        self, backend: FakeExecutor,
    ) -> HANDOFF.ProductionExecutor:
        selected = HANDOFF.ProductionExecutor()
        self.backends[id(selected)] = backend
        return selected

    def run_handoff(self, executor: FakeExecutor) -> dict[str, object]:
        return HANDOFF.handoff(
            self.activation, self.audit, self.output,
            expected_source_baseline_sha256=SOURCE_SHA,
            expected_campaign_id=CAMPAIGN,
            production_mode=HANDOFF.PRODUCTION_MODE,
            executor=self.production_executor(executor))

    def run_reconcile(self, executor: FakeExecutor) -> str:
        return HANDOFF.reconcile(
            production_mode=HANDOFF.PRODUCTION_MODE,
            executor=self.production_executor(executor))

    def test_activation_predecessor_lineage_is_exact(self) -> None:
        success = predecessor_activation_success()
        failure = predecessor_activation_failure()
        HANDOFF._validate_activation_predecessor_lineage(
            success, failure, "TEST_PREDECESSOR_INVALID")
        mutations = (
            ("success-file", success, "receipt_file_sha256", SHA),
            ("success-schema", success, "receipt_schema", "tampered.v3"),
            ("failure-journal", failure, "journal_sha256", SHA),
            ("round86-ancestor-binding", failure, "receipt_body_sha256", SHA),
        )
        for label, original, field, value in mutations:
            with self.subTest(label=label):
                changed = copy.deepcopy(original)
                changed[field] = value
                with self.assertRaises(HANDOFF.HandoffError):
                    HANDOFF._validate_activation_predecessor_lineage(
                        changed if original is success else success,
                        changed if original is failure else failure,
                        "TEST_PREDECESSOR_INVALID")

    def test_complete_receipt_matches_admission_validator_exactly(self) -> None:
        executor = FakeExecutor()
        receipt = self.run_handoff(executor)
        self.assertEqual(
            receipt["status"], "WATCH_RETIRED_HANDOFF_COMPLETE")
        self.assertEqual(set(receipt), HANDOFF.RECEIPT_FIELDS)
        self.assertEqual(stat.S_IMODE(self.output.stat().st_mode), 0o600)
        self.assertEqual(receipt, HANDOFF.validate_receipt(
            self.output.read_bytes()))
        self.assertEqual(receipt["production_mode"], HANDOFF.PRODUCTION_MODE)
        self.assertEqual(receipt["producer"], {
            "path": str(SOURCE),
            "file_sha256": HANDOFF.digest_bytes(SOURCE.read_bytes()),
        })
        self.assertEqual(receipt["schema"], HANDOFF.RECEIPT_SCHEMA)
        self.assertEqual(receipt["version"], 2)
        self.assertTrue(receipt["global_kill_switch_engaged"])
        self.assertEqual(receipt["identity_count"], 0)
        self.assertEqual(
            receipt["identity_manifest_sha256"],
            HANDOFF.DISABLED_IDENTITY_MANIFEST_SHA256)
        self.assertTrue(receipt["paper_profile_restored"])
        self.assertTrue(receipt["profile_candidate_absent"])
        self.assertTrue(receipt["paper_runtime_profile_hardened"])
        self.assertTrue(receipt["paper_runtime_profile_candidate_absent"])
        restoration = receipt["paper_profile_restoration"]
        self.assertEqual(set(restoration), HANDOFF.PROFILE_RESTORATION_FIELDS)
        self.assertEqual(restoration["exchange_method"], "RENAME_EXCHANGE")
        self.assertTrue(restoration["forward_only_after_exchange"])
        hardening = receipt["paper_runtime_profile_hardening"]
        self.assertEqual(
            set(hardening), HANDOFF.PAPER_RUNTIME_PROFILE_HARDENING_FIELDS)
        self.assertEqual(hardening["exchange_method"], "RENAME_EXCHANGE")
        self.assertTrue(hardening["forward_only_after_exchange"])
        if VERIFIER.WATCH_HANDOFF_FIELDS != HANDOFF.RECEIPT_FIELDS:
            self.skipTest(
                "admission verifier handoff schema integration pending")
        with mock.patch.object(
                VERIFIER, "WATCH_HANDOFF_PRODUCER_PATH", SOURCE):
            facts = VERIFIER.validate_watch_handoff(receipt)
        self.assertEqual(facts.status, "WATCH_RETIRED_HANDOFF_COMPLETE")
        self.assertEqual(facts.readiness, ())
        self.assertEqual(facts.dangers, ())
        self.assertTrue(all(receipt[field] is False for field in (
            "paper_authorized", "live_authorized", "mutation_authorized",
            "direct_broker_access", "order_submission_authorized")))
        self.assertEqual(
            self.run_reconcile(executor),
            "WATCH_RETIRED_HANDOFF_COMPLETE")

    def test_real_auditor_receipt_flows_to_terminal_admission_consumer(
            self) -> None:
        module_name = "p1_auditor_contract_fixture_for_handoff"
        owned_module_names = (
            module_name, "hepta_p1_safety_soak_auditor",
            "p1_campaign_coordinator_for_auditor_contract_test",
        )
        previous_modules = {
            name: sys.modules.get(name) for name in owned_module_names}
        spec = importlib.util.spec_from_file_location(
            module_name, ROOT / "tests" /
                "hepta_p1_safety_soak_auditor_tests.py")
        assert spec is not None and spec.loader is not None
        fixture_module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = fixture_module
        try:
            spec.loader.exec_module(fixture_module)
            auditor = fixture_module.AUDITOR
            self.assertEqual(
                HANDOFF.P1_AUDIT_FIELDS, auditor.AUDIT_RECEIPT_FIELDS)
            self.assertEqual(
                HANDOFF.P1_INTERVAL_FIELDS,
                auditor.EVALUATED_INTERVAL_FIELDS)
            self.assertEqual(
                HANDOFF.P1_COUNTS_FIELDS, auditor.COUNTS_FIELDS)
            self.assertEqual(
                HANDOFF.P1_COMPLETENESS_FIELDS,
                auditor.COMPLETENESS_FIELDS)
            self.assertEqual(
                HANDOFF.P1_EXPOSURE_FIELDS,
                auditor.EXPOSURE_SUMMARY_FIELDS)
            self.assertEqual(
                HANDOFF.P1_CLEANUP_FIELDS,
                auditor.CLEANUP_STATUS_FIELDS)

            self.auditor.write_bytes(b"installed-auditor")
            self.auditor.chmod(0o755)
            with mock.patch.object(
                    auditor, "INSTALLED_EXECUTABLE", self.auditor):
                audit = auditor.audit_evidence(**fixture_module.make_bundle())
            self.assertEqual(audit["verdict"], "GO")
            HANDOFF.validate_p1_audit(audit)

            activation = activation_document()
            activation.pop("body_sha256")
            activation["shadow_install_evidence"][
                "source_baseline_sha256"] = \
                    audit["source_manifest_sha256"]
            write_receipt(self.activation, HANDOFF.seal(activation))
            write_receipt(self.audit, audit)
            receipt = HANDOFF.handoff(
                self.activation, self.audit, self.output,
                expected_source_baseline_sha256=
                    audit["source_manifest_sha256"],
                expected_campaign_id=audit["campaign_id"],
                production_mode=HANDOFF.PRODUCTION_MODE,
                executor=self.production_executor(FakeExecutor()))
            if VERIFIER.WATCH_HANDOFF_FIELDS != HANDOFF.RECEIPT_FIELDS:
                self.skipTest(
                    "admission verifier handoff schema integration pending")
            with mock.patch.object(
                    VERIFIER, "WATCH_HANDOFF_PRODUCER_PATH", SOURCE):
                facts = VERIFIER.validate_watch_handoff(receipt)
            self.assertEqual(facts.readiness, ())
            self.assertEqual(facts.source, audit["source_manifest_sha256"])
            self.assertEqual(facts.campaign, audit["campaign_id"])
        finally:
            for name, previous in previous_modules.items():
                if previous is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = previous

    def test_complete_mutation_inventory_is_fixed(self) -> None:
        executor = FakeExecutor()
        self.run_handoff(executor)
        expected = [
            item for unit in HANDOFF.WATCH_UNITS for item in (
                ("disable", unit), ("persistent-mask", unit),
                ("runtime-mask", unit))
        ] + [
            ("daemon-reload", None), ("profile-candidate", None),
            ("profile-exchange", None), ("profile-retire-watch", None),
            ("runtime-profile-backup", None),
            ("runtime-profile-candidate", None),
            ("runtime-profile-exchange", None),
            ("runtime-profile-retain-legacy", None),
        ]
        self.assertEqual(executor.mutations, expected)
        self.assertEqual(
            [record.phase for record in HANDOFF.Journal().load()],
            list(HANDOFF.SUCCESS_PHASES))

    def test_fake_executor_is_rejected_before_state_or_mutation(self) -> None:
        executor = FakeExecutor()
        with self.assertRaisesRegex(
                HANDOFF.HandoffError,
                "HANDOFF_PRODUCTION_EXECUTOR_REQUIRED"):
            HANDOFF.handoff(
                self.activation, self.audit, self.output,
                expected_source_baseline_sha256=SOURCE_SHA,
                expected_campaign_id=CAMPAIGN,
                production_mode=HANDOFF.PRODUCTION_MODE,
                executor=executor)
        self.assertFalse(self.state.exists())
        self.assertFalse(self.output.exists())
        self.assertEqual(executor.mutations, [])

    def test_explicit_production_intent_is_required_before_state(self) -> None:
        executor = self.production_executor(FakeExecutor())
        with self.assertRaisesRegex(
                HANDOFF.HandoffError,
                "HANDOFF_EXPLICIT_PRODUCTION_INTENT_REQUIRED"):
            HANDOFF.handoff(
                self.activation, self.audit, self.output,
                expected_source_baseline_sha256=SOURCE_SHA,
                expected_campaign_id=CAMPAIGN, executor=executor)
        self.assertFalse(self.state.exists())
        self.assertFalse(self.output.exists())

    def test_source_copy_cannot_self_attest_as_installed_producer(self) -> None:
        copied = self.root / "copied-handoff.py"
        shutil.copyfile(SOURCE, copied)
        copied.chmod(0o755)
        spec = importlib.util.spec_from_file_location(
            "hepta_watch_handoff_source_copy", copied)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        try:
            with mock.patch.object(module, "ROOT_UID", os.geteuid()), \
                    mock.patch.object(module, "ROOT_GID", os.getegid()):
                with self.assertRaisesRegex(
                        module.HandoffError,
                        "HANDOFF_EXECUTING_IMAGE_(?:NOT_INSTALLED|DRIFT)"):
                    module.ProductionExecutor()
        finally:
            sys.modules.pop(spec.name, None)

    def test_bound_producer_tamper_fails_before_journal(self) -> None:
        backend = FakeExecutor()
        executor = self.production_executor(backend)
        original = executor._producer_binding
        executor._producer_binding = HANDOFF.ProducerBinding(
            original.path, original.payload + b"tamper",
            original.metadata_identity, original.parent_identity)
        with self.assertRaisesRegex(
                HANDOFF.HandoffError, "HANDOFF_EXECUTING_IMAGE_DRIFT"):
            HANDOFF.handoff(
                self.activation, self.audit, self.output,
                expected_source_baseline_sha256=SOURCE_SHA,
                expected_campaign_id=CAMPAIGN,
                production_mode=HANDOFF.PRODUCTION_MODE,
                executor=executor)
        self.assertFalse(self.state.exists())
        self.assertEqual(backend.mutations, [])

    def test_legacy_or_malformed_producer_receipt_is_rejected(self) -> None:
        receipt = self.run_handoff(FakeExecutor())
        for mutation in ("legacy", "extra", "path", "digest"):
            with self.subTest(mutation=mutation):
                body = dict(receipt)
                body.pop("body_sha256")
                if mutation == "legacy":
                    body.pop("producer")
                elif mutation == "extra":
                    body["producer"] = {**body["producer"], "extra": False}
                elif mutation == "path":
                    body["producer"] = {
                        **body["producer"], "path": "/tmp/source-copy"}
                else:
                    body["producer"] = {
                        **body["producer"],
                        "file_sha256": "sha256:" + "0" * 64}
                malformed = HANDOFF.seal(body)
                with self.assertRaises(HANDOFF.HandoffError):
                    HANDOFF.validate_receipt(
                        HANDOFF.canonical_bytes(malformed))

    def test_source_mismatch_precedes_state_and_systemd_mutation(self) -> None:
        executor = FakeExecutor()
        with self.assertRaisesRegex(
                HANDOFF.HandoffError, "HANDOFF_EXPECTED_SOURCE_MISMATCH"):
            HANDOFF.handoff(
                self.activation, self.audit, self.output,
                expected_source_baseline_sha256="sha256:" + "b" * 64,
                expected_campaign_id=CAMPAIGN,
                production_mode=HANDOFF.PRODUCTION_MODE,
                executor=self.production_executor(executor))
        self.assertFalse(self.state.exists())
        self.assertEqual(executor.mutations, [])

    def test_campaign_mismatch_precedes_mutation(self) -> None:
        executor = FakeExecutor()
        with self.assertRaisesRegex(
                HANDOFF.HandoffError, "HANDOFF_EXPECTED_CAMPAIGN_MISMATCH"):
            HANDOFF.handoff(
                self.activation, self.audit, self.output,
                expected_source_baseline_sha256=SOURCE_SHA,
                expected_campaign_id="wrong-campaign",
                production_mode=HANDOFF.PRODUCTION_MODE,
                executor=self.production_executor(executor))
        self.assertFalse(self.state.exists())
        self.assertEqual(executor.mutations, [])

    def test_activation_and_audit_source_lineage_must_match(self) -> None:
        document = audit_document()
        document.pop("body_sha256")
        document["source_manifest_sha256"] = "sha256:" + "b" * 64
        write_receipt(self.audit, HANDOFF.seal(document))
        with self.assertRaisesRegex(
                HANDOFF.HandoffError, "HANDOFF_SOURCE_LINEAGE_MISMATCH"):
            self.run_handoff(FakeExecutor())
        self.assertFalse(self.state.exists())

    def test_noncanonical_and_extra_input_fields_are_rejected(self) -> None:
        document = activation_document()
        document["extra"] = False
        self.activation.write_bytes(HANDOFF.canonical_bytes(document))
        with self.assertRaisesRegex(
                HANDOFF.HandoffError, "HANDOFF_ACTIVATION_RECEIPT_INVALID"):
            self.run_handoff(FakeExecutor())
        self.assertFalse(self.state.exists())

    def test_p1_no_go_is_rejected_before_state(self) -> None:
        body = audit_document()
        body.pop("body_sha256")
        body["verdict"] = "NO_GO"
        write_receipt(self.audit, HANDOFF.seal(body))
        with self.assertRaisesRegex(
                HANDOFF.HandoffError, "HANDOFF_P1_AUDIT_RECEIPT_INVALID"):
            self.run_handoff(FakeExecutor())
        self.assertFalse(self.state.exists())

    def test_p1_duration_and_trading_day_boundaries_are_exact(self) -> None:
        HANDOFF.validate_p1_audit(audit_document())

        too_short = audit_document()
        too_short.pop("body_sha256")
        interval = too_short["evaluated_interval"]
        interval["duration_ns"] = HANDOFF.MINIMUM_BOOTTIME_DURATION_NS - 1
        interval["end_boottime_ns"] = (
            interval["start_boottime_ns"] + interval["duration_ns"])
        with self.assertRaisesRegex(
                HANDOFF.HandoffError, "HANDOFF_P1_AUDIT_RECEIPT_INVALID"):
            HANDOFF.validate_p1_audit(HANDOFF.seal(too_short))

        for day_count, accepted in ((9, False), (10, True), (20, True),
                                    (21, False)):
            with self.subTest(day_count=day_count):
                candidate = audit_document()
                candidate.pop("body_sha256")
                candidate["counts"]["declared_trading_days"] = day_count
                candidate["counts"]["observed_trading_days"] = day_count
                candidate = HANDOFF.seal(candidate)
                if accepted:
                    HANDOFF.validate_p1_audit(candidate)
                else:
                    with self.assertRaisesRegex(
                            HANDOFF.HandoffError,
                            "HANDOFF_P1_AUDIT_RECEIPT_INVALID"):
                        HANDOFF.validate_p1_audit(candidate)

    def test_p1_decision_and_completeness_boundaries_are_exact(self) -> None:
        below_count = audit_document()
        below_count.pop("body_sha256")
        below_count["counts"].update({
            "eligible_decisions": 199,
            "complete_eligible_decisions": 199,
            "incomplete_eligible_decisions": 0,
        })
        below_count["completeness"] = {
            "numerator": 199, "denominator": 199, "ppm": 1_000_000,
            "strictly_greater_than_99_percent": True,
        }
        with self.assertRaisesRegex(
                HANDOFF.HandoffError, "HANDOFF_P1_AUDIT_RECEIPT_INVALID"):
            HANDOFF.validate_p1_audit(HANDOFF.seal(below_count))

        exact_ninety_nine = audit_document()
        exact_ninety_nine.pop("body_sha256")
        exact_ninety_nine["counts"].update({
            "complete_eligible_decisions": 198,
            "incomplete_eligible_decisions": 2,
        })
        exact_ninety_nine["completeness"] = {
            "numerator": 198, "denominator": 200, "ppm": 990_000,
            "strictly_greater_than_99_percent": False,
        }
        with self.assertRaisesRegex(
                HANDOFF.HandoffError, "HANDOFF_P1_AUDIT_RECEIPT_INVALID"):
            HANDOFF.validate_p1_audit(HANDOFF.seal(exact_ninety_nine))

    def test_legitimate_strictly_over_99_percent_audit_is_accepted(self) -> None:
        body = audit_document()
        body.pop("body_sha256")
        body["counts"]["eligible_decisions"] = 201
        body["counts"]["complete_eligible_decisions"] = 200
        body["counts"]["incomplete_eligible_decisions"] = 1
        body["completeness"] = {
            "numerator": 200, "denominator": 201,
            "ppm": 200 * 1_000_000 // 201,
            "strictly_greater_than_99_percent": True,
        }
        write_receipt(self.audit, HANDOFF.seal(body))
        receipt = self.run_handoff(FakeExecutor())
        self.assertEqual(
            receipt["status"], "WATCH_RETIRED_HANDOFF_COMPLETE")

    def test_input_inode_rebound_after_first_mutation_fails_closed(self) -> None:
        fixture = self

        class ReboundExecutor(FakeExecutor):
            def disable_and_stop(inner_self, unit: str) -> None:
                super().disable_and_stop(unit)
                if inner_self.calls == 1:
                    replacement = fixture.root / "replacement.json"
                    write_receipt(replacement, activation_document())
                    replacement.replace(fixture.activation)

        receipt = self.run_handoff(ReboundExecutor())
        self.assertEqual(receipt["status"], "FAILED_CLOSED")
        self.assertFalse(receipt["paper_authorized"])
        self.assertIn(
            "FAILURE_INTENT",
            [record.phase for record in HANDOFF.Journal().load()])

    def test_audit_inode_rebound_after_first_mutation_fails_closed(self) -> None:
        fixture = self

        class ReboundExecutor(FakeExecutor):
            def disable_and_stop(inner_self, unit: str) -> None:
                super().disable_and_stop(unit)
                if inner_self.calls == 1:
                    replacement = fixture.root / "audit-replacement.json"
                    write_receipt(replacement, audit_document())
                    replacement.replace(fixture.audit)

        receipt = self.run_handoff(ReboundExecutor())
        self.assertEqual(receipt["status"], "FAILED_CLOSED")
        self.assertIn(
            "FAILURE_INTENT",
            [record.phase for record in HANDOFF.Journal().load()])

    def test_non_root_owned_audit_is_rejected_before_mutation(self) -> None:
        real_fstat = HANDOFF.os.fstat
        audit_path = str(self.audit)

        def adversarial_fstat(descriptor: int) -> object:
            metadata = real_fstat(descriptor)
            try:
                target = os.path.realpath(f"/proc/self/fd/{descriptor}")
            except OSError:
                return metadata
            if target != audit_path:
                return metadata
            values = {
                field: getattr(metadata, field) for field in (
                    "st_dev", "st_ino", "st_mode", "st_nlink", "st_uid",
                    "st_gid", "st_size", "st_mtime_ns", "st_ctime_ns")
            }
            values["st_uid"] = HANDOFF.ROOT_UID + 1
            return SimpleNamespace(**values)

        backend = FakeExecutor()
        with mock.patch.object(
                HANDOFF.os, "fstat", side_effect=adversarial_fstat):
            with self.assertRaisesRegex(
                    HANDOFF.HandoffError,
                    "HANDOFF_P1_AUDIT_RECEIPT_INVALID"):
                self.run_handoff(backend)
        self.assertEqual(backend.mutations, [])
        self.assertFalse(self.state.exists())

    def test_writable_input_ancestor_is_rejected_before_mutation(self) -> None:
        unsafe = self.root / "unsafe"
        unsafe.mkdir(mode=0o777)
        unsafe.chmod(0o777)
        activation = unsafe / "activation.json"
        audit = unsafe / "audit.json"
        write_receipt(activation, activation_document())
        write_receipt(audit, audit_document())
        backend = FakeExecutor()
        with self.assertRaisesRegex(
                HANDOFF.HandoffError,
                "HANDOFF_ACTIVATION_RECEIPT_INVALID"):
            HANDOFF.handoff(
                activation, audit, self.output,
                expected_source_baseline_sha256=SOURCE_SHA,
                expected_campaign_id=CAMPAIGN,
                production_mode=HANDOFF.PRODUCTION_MODE,
                executor=self.production_executor(backend))
        self.assertEqual(backend.mutations, [])
        self.assertFalse(self.state.exists())

    def test_systemctl_error_is_terminal_failed_closed(self) -> None:
        executor = FakeExecutor(fail_at=2)
        receipt = self.run_handoff(executor)
        self.assertEqual(receipt["status"], "FAILED_CLOSED")
        self.assertEqual(
            self.run_reconcile(FakeExecutor()), "FAILED_CLOSED")
        with self.assertRaisesRegex(
                HANDOFF.HandoffError, "HANDOFF_TRANSACTION_ALREADY_EXISTS"):
            self.run_handoff(FakeExecutor())

    def test_persistent_systemctl_failure_never_claims_complete(self) -> None:
        receipt = self.run_handoff(FakeExecutor(
            fail_at=1, fail_persistently=True))
        self.assertEqual(receipt["status"], "FAILED_CLOSED")
        self.assertFalse(receipt["watch_units_inactive"])
        self.assertFalse(receipt["crash_recovery_verified"])

    def test_paper_broker_and_kill_preflight_drift_fail_before_mutation(
            self) -> None:
        for kwargs in (
            {"paper_active": True}, {"broker_open": True},
            {"kill_disengaged": True}, {"global_kill_disengaged": True},
            {"identity_count": 1},
        ):
            with self.subTest(kwargs=kwargs):
                executor = FakeExecutor(**kwargs)
                with self.assertRaisesRegex(
                        HANDOFF.HandoffError, "HANDOFF_PREFLIGHT_FAILED"):
                    self.run_handoff(executor)
                self.assertEqual(executor.mutations, [])
                self.assertFalse(self.state.exists())

    def test_profile_preflight_requires_watch_and_no_candidate(self) -> None:
        for state in ("PRE_CANDIDATE", "POST_CANDIDATE", "RESTORED"):
            with self.subTest(state=state):
                self.tearDown()
                self.setUp()
                executor = FakeExecutor(profile_state=state)
                with self.assertRaisesRegex(
                        HANDOFF.HandoffError,
                        "HANDOFF_PROFILE_PREFLIGHT_FAILED"):
                    self.run_handoff(executor)
                self.assertEqual(executor.mutations, [])
                self.assertFalse(self.state.exists())

    def test_profile_crash_seams_resume_forward_to_complete(self) -> None:
        seams = (
            "BEFORE_PROFILE_CANDIDATE", "AFTER_PROFILE_CANDIDATE",
            "BEFORE_PROFILE_EXCHANGE", "AFTER_PROFILE_EXCHANGE",
            "BEFORE_PROFILE_RETIRE_WATCH", "AFTER_PROFILE_RETIRE_WATCH",
        )
        for selected in seams:
            with self.subTest(seam=selected):
                self.tearDown()
                self.setUp()
                triggered = False
                executor = FakeExecutor()

                def crash(name: str, _unit: str | None) -> None:
                    nonlocal triggered
                    if name == selected and not triggered:
                        triggered = True
                        raise SimulatedCrash()

                with mock.patch.object(HANDOFF, "MUTATION_SEAM_HOOK", crash):
                    with self.assertRaises(SimulatedCrash):
                        self.run_handoff(executor)
                self.assertTrue(triggered)
                self.assertEqual(
                    self.run_reconcile(executor),
                    "WATCH_RETIRED_HANDOFF_COMPLETE")
                receipt = HANDOFF.validate_receipt(self.output.read_bytes())
                self.assertTrue(receipt["paper_profile_restored"])
                self.assertTrue(receipt["profile_candidate_absent"])
                self.assertEqual(executor.profile_state, "RESTORED")

    def test_preexchange_failure_removes_dormant_candidate(self) -> None:
        class FailingCandidateExecutor(FakeExecutor):
            def prepare_profile_candidate(inner_self) -> None:
                super().prepare_profile_candidate()
                raise HANDOFF.HandoffError("HANDOFF_FAKE_AFTER_CANDIDATE")

        executor = FailingCandidateExecutor()
        receipt = self.run_handoff(executor)
        self.assertEqual(receipt["status"], "FAILED_CLOSED")
        self.assertFalse(receipt["paper_profile_restored"])
        self.assertIsNone(receipt["paper_profile_restoration"])
        self.assertTrue(receipt["profile_candidate_absent"])
        self.assertEqual(executor.profile_state, "PRE")
        self.assertIn(("profile-candidate-cleanup", None), executor.mutations)

    def test_postexchange_failure_is_forward_only_and_terminal(self) -> None:
        class FailingExchangeExecutor(FakeExecutor):
            def exchange_profile_candidate(inner_self) -> None:
                super().exchange_profile_candidate()
                raise HANDOFF.HandoffError("HANDOFF_FAKE_AFTER_EXCHANGE")

        executor = FailingExchangeExecutor()
        receipt = self.run_handoff(executor)
        self.assertEqual(receipt["status"], "FAILED_CLOSED")
        self.assertTrue(receipt["paper_profile_restored"])
        self.assertTrue(receipt["profile_candidate_absent"])
        self.assertEqual(executor.profile_state, "RESTORED")
        self.assertIn(("profile-retire-watch", None), executor.mutations)
        self.assertEqual(self.run_reconcile(executor), "FAILED_CLOSED")

    def test_runtime_profile_transform_is_exact_and_account_opaque(self) -> None:
        legacy = paper_runtime_profile_payload(hardened=False)
        hardened = paper_runtime_profile_payload(hardened=True)
        patches = (
            mock.patch.object(
                HANDOFF, "LEGACY_PAPER_RUNTIME_PROFILE_BYTES", len(legacy)),
            mock.patch.object(
                HANDOFF, "LEGACY_PAPER_RUNTIME_PROFILE_SHA256",
                HANDOFF.digest_bytes(legacy)),
            mock.patch.object(
                HANDOFF, "HARDENED_PAPER_RUNTIME_PROFILE_BYTES",
                len(hardened)),
            mock.patch.object(
                HANDOFF, "HARDENED_PAPER_RUNTIME_PROFILE_SHA256",
                HANDOFF.digest_bytes(hardened)),
        )
        with ExitStack() as stack:
            for patch in patches:
                stack.enter_context(patch)
            actual = HANDOFF._harden_paper_runtime_profile(legacy)
            self.assertEqual(actual, hardened)
            before = HANDOFF._parse_paper_runtime_profile(
                legacy, hardened=False, reason="TEST")
            after = HANDOFF._parse_paper_runtime_profile(
                actual, hardened=True, reason="TEST")
        self.assertEqual(
            before["HEPTA_IB_PAPER_ACCOUNT"],
            after["HEPTA_IB_PAPER_ACCOUNT"])
        self.assertEqual(
            {key for key in before if before[key] != after[key]},
            set(HANDOFF.PAPER_RUNTIME_PROFILE_HARDENED_LIMITS))
        self.assertNotIn(
            before["HEPTA_IB_PAPER_ACCOUNT"],
            REPOSITORY_SOURCE.read_text(encoding="utf-8"))

    def test_runtime_profile_preflight_requires_exact_legacy_state(self) -> None:
        for state in (
            "LEGACY_BACKED_UP", "LEGACY_CANDIDATE",
            "HARDENED_CANDIDATE", "HARDENED",
        ):
            with self.subTest(state=state):
                self.tearDown()
                self.setUp()
                executor = FakeExecutor(runtime_profile_state=state)
                with self.assertRaisesRegex(
                        HANDOFF.HandoffError,
                        "HANDOFF_RUNTIME_PROFILE_PREFLIGHT_FAILED"):
                    self.run_handoff(executor)
                self.assertEqual(executor.mutations, [])
                self.assertFalse(self.state.exists())

    def test_runtime_profile_crash_seams_resume_forward_to_complete(
            self) -> None:
        seams = (
            "BEFORE_RUNTIME_PROFILE_BACKUP",
            "AFTER_RUNTIME_PROFILE_BACKUP",
            "BEFORE_RUNTIME_PROFILE_CANDIDATE",
            "AFTER_RUNTIME_PROFILE_CANDIDATE",
            "BEFORE_RUNTIME_PROFILE_EXCHANGE",
            "AFTER_RUNTIME_PROFILE_EXCHANGE",
            "BEFORE_RUNTIME_PROFILE_RETAIN_LEGACY",
            "AFTER_RUNTIME_PROFILE_RETAIN_LEGACY",
        )
        for selected in seams:
            with self.subTest(seam=selected):
                self.tearDown()
                self.setUp()
                triggered = False
                executor = FakeExecutor()

                def crash(name: str, _unit: str | None) -> None:
                    nonlocal triggered
                    if name == selected and not triggered:
                        triggered = True
                        raise SimulatedCrash()

                with mock.patch.object(HANDOFF, "MUTATION_SEAM_HOOK", crash):
                    with self.assertRaises(SimulatedCrash):
                        self.run_handoff(executor)
                self.assertTrue(triggered)
                self.assertEqual(
                    self.run_reconcile(executor),
                    "WATCH_RETIRED_HANDOFF_COMPLETE")
                receipt = HANDOFF.validate_receipt(self.output.read_bytes())
                self.assertTrue(receipt["paper_runtime_profile_hardened"])
                self.assertTrue(
                    receipt["paper_runtime_profile_candidate_absent"])
                self.assertEqual(executor.runtime_profile_state, "HARDENED")

    def test_runtime_preexchange_failure_cleans_candidate_and_stays_legacy(
            self) -> None:
        class FailingCandidateExecutor(FakeExecutor):
            def prepare_paper_runtime_profile_candidate(inner_self) -> None:
                super().prepare_paper_runtime_profile_candidate()
                raise HANDOFF.HandoffError(
                    "HANDOFF_FAKE_AFTER_RUNTIME_CANDIDATE")

        executor = FailingCandidateExecutor()
        receipt = self.run_handoff(executor)
        self.assertEqual(receipt["status"], "FAILED_CLOSED")
        self.assertFalse(receipt["paper_runtime_profile_hardened"])
        self.assertIsNone(receipt["paper_runtime_profile_hardening"])
        self.assertTrue(receipt["paper_runtime_profile_candidate_absent"])
        self.assertEqual(executor.runtime_profile_state, "LEGACY_BACKED_UP")
        self.assertIn(
            ("runtime-profile-candidate-cleanup", None), executor.mutations)

    def test_runtime_postexchange_failure_forward_closes_hardened(
            self) -> None:
        class FailingExchangeExecutor(FakeExecutor):
            def exchange_paper_runtime_profile_candidate(inner_self) -> None:
                super().exchange_paper_runtime_profile_candidate()
                raise HANDOFF.HandoffError(
                    "HANDOFF_FAKE_AFTER_RUNTIME_EXCHANGE")

        executor = FailingExchangeExecutor()
        receipt = self.run_handoff(executor)
        self.assertEqual(receipt["status"], "FAILED_CLOSED")
        self.assertTrue(receipt["paper_runtime_profile_hardened"])
        self.assertTrue(receipt["paper_runtime_profile_candidate_absent"])
        self.assertEqual(executor.runtime_profile_state, "HARDENED")
        self.assertIn(
            ("runtime-profile-retain-legacy", None), executor.mutations)
        self.assertEqual(self.run_reconcile(executor), "FAILED_CLOSED")

    def test_runtime_hardening_evidence_tamper_is_rejected(self) -> None:
        receipt = self.run_handoff(FakeExecutor())
        mutations = (
            ("target-hash", "target", "file_sha256", SHA),
            ("backup-mode", "legacy_backup", "mode", stat.S_IFREG | 0o644),
            ("retained-nlink", "retained_legacy", "nlink", 2),
        )
        for label, member, field, value in mutations:
            with self.subTest(label=label):
                changed = copy.deepcopy(receipt)
                changed.pop("body_sha256")
                changed["paper_runtime_profile_hardening"][member][field] = (
                    value)
                changed = HANDOFF.seal(changed)
                with self.assertRaisesRegex(
                        HANDOFF.HandoffError, "HANDOFF_RECEIPT_INVALID|"
                        "HANDOFF_RUNTIME_PROFILE_HARDENING_EVIDENCE_INVALID"):
                    HANDOFF.validate_receipt(HANDOFF.canonical_bytes(changed))

    def test_runtime_candidate_absence_is_reattested_before_publish(
            self) -> None:
        class CandidateAbaExecutor(FakeExecutor):
            def paper_runtime_profile_candidate_absent(inner_self) -> bool:
                return False

        executor = CandidateAbaExecutor()
        with self.assertRaisesRegex(
                HANDOFF.HandoffError,
                "HANDOFF_RUNTIME_PROFILE_CANDIDATE_ABSENCE_INVALID"):
            self.run_handoff(executor)
        self.assertFalse(self.output.exists())
        self.assertEqual(executor.runtime_profile_state, "HARDENED")
        self.assertNotIn(
            "COMPLETED", [record.phase for record in HANDOFF.Journal().load()])

    def test_restoration_evidence_tamper_is_rejected(self) -> None:
        receipt = self.run_handoff(FakeExecutor())
        mutations = (
            ("target-hash", "target", "file_sha256", SHA),
            ("backup-mode", "dormant_backup", "mode", stat.S_IFREG | 0o644),
            ("retired-nlink", "retired_watch", "nlink", 2),
            ("transition-body", "forward_transition_receipt",
             "body_sha256", "invalid"),
        )
        for label, member, field, value in mutations:
            with self.subTest(label=label):
                changed = copy.deepcopy(receipt)
                changed.pop("body_sha256")
                changed["paper_profile_restoration"][member][field] = value
                changed = HANDOFF.seal(changed)
                with self.assertRaisesRegex(
                        HANDOFF.HandoffError, "HANDOFF_RECEIPT_INVALID|"
                        "HANDOFF_PROFILE_RESTORATION_EVIDENCE_INVALID"):
                    HANDOFF.validate_receipt(HANDOFF.canonical_bytes(changed))

    def test_failed_receipt_rejects_candidate_residue_claim(self) -> None:
        receipt = self.run_handoff(FakeExecutor(fail_at=1))
        changed = dict(receipt)
        changed.pop("body_sha256")
        changed["profile_candidate_absent"] = False
        with self.assertRaises(HANDOFF.HandoffError):
            HANDOFF.validate_receipt(
                HANDOFF.canonical_bytes(HANDOFF.seal(changed)))

    def test_candidate_absence_is_reattested_before_publish(self) -> None:
        class CandidateAbaExecutor(FakeExecutor):
            def profile_candidate_absent(inner_self) -> bool:
                return False

        executor = CandidateAbaExecutor()
        with self.assertRaisesRegex(
                HANDOFF.HandoffError,
                "HANDOFF_PROFILE_CANDIDATE_ABSENCE_INVALID"):
            self.run_handoff(executor)
        self.assertFalse(self.output.exists())
        self.assertEqual(executor.profile_state, "RESTORED")
        self.assertNotIn(
            "COMPLETED", [record.phase for record in HANDOFF.Journal().load()])

    def test_enabled_inactive_paper_activation_surface_blocks_handoff(
            self) -> None:
        fixture = self

        class EnabledPaperExecutor(FakeExecutor):
            def __init__(inner_self, unit: str):
                super().__init__()
                inner_self.unit = unit

            def snapshot(inner_self, *, tighten: bool = False):
                value = super().snapshot(tighten=tighten)
                value["paper_units"][inner_self.unit][
                    "unit_file_state"] = "enabled"
                return HANDOFF.validate_snapshot(value)

        for unit in (
            "hepta-execution-ib-paper.service",
            "hepta-execution-ib-paper.socket",
        ):
            with self.subTest(unit=unit):
                self.tearDown()
                self.setUp()
                executor = EnabledPaperExecutor(unit)
                with self.assertRaisesRegex(
                        HANDOFF.HandoffError, "HANDOFF_PREFLIGHT_FAILED"):
                    fixture.run_handoff(executor)
                self.assertEqual(executor.mutations, [])
                self.assertFalse(self.state.exists())

    def test_final_residue_can_only_publish_failed_closed(self) -> None:
        receipt = self.run_handoff(FakeExecutor(residue=True))
        self.assertEqual(receipt["status"], "FAILED_CLOSED")
        self.assertGreater(receipt["cleanup_residue_count"], 0)
        self.assertFalse(receipt["crash_recovery_verified"])
        self.assertFalse(receipt["paper_authorized"])

    def test_active_transient_p1_unit_blocks_handoff_complete(self) -> None:
        receipt = self.run_handoff(FakeExecutor(transient_active=True))
        self.assertEqual(receipt["status"], "FAILED_CLOSED")
        self.assertGreater(receipt["cleanup_residue_count"], 0)

    def test_success_mutation_crash_seams_reconcile_only_to_failed(self) -> None:
        seam_names = (
            "BEFORE_DISABLE", "AFTER_DISABLE",
            "BEFORE_PERSISTENT_MASK", "AFTER_PERSISTENT_MASK",
            "BEFORE_RUNTIME_MASK", "AFTER_RUNTIME_MASK",
            "BEFORE_DAEMON_RELOAD", "AFTER_DAEMON_RELOAD",
        )
        for seam in seam_names:
            with self.subTest(seam=seam):
                self.tearDown()
                self.setUp()
                triggered = False

                def crash(name: str, _unit: str | None) -> None:
                    nonlocal triggered
                    if name == seam and not triggered:
                        triggered = True
                        raise SimulatedCrash()

                with mock.patch.object(
                        HANDOFF, "MUTATION_SEAM_HOOK", crash):
                    with self.assertRaises(SimulatedCrash):
                        self.run_handoff(FakeExecutor())
                self.assertTrue(triggered)
                self.assertEqual(
                    self.run_reconcile(FakeExecutor()),
                    "FAILED_CLOSED")
                failed = HANDOFF.validate_receipt(self.output.read_bytes())
                self.assertEqual(failed["status"], "FAILED_CLOSED")

    def test_fail_close_crash_seams_are_reentrant_and_never_promote(self) -> None:
        seam_names = (
            "FAIL_CLOSE_BEFORE_DISABLE", "FAIL_CLOSE_AFTER_DISABLE",
            "FAIL_CLOSE_BEFORE_PERSISTENT_MASK",
            "FAIL_CLOSE_AFTER_PERSISTENT_MASK",
            "FAIL_CLOSE_BEFORE_RUNTIME_MASK", "FAIL_CLOSE_AFTER_RUNTIME_MASK",
            "FAIL_CLOSE_BEFORE_DAEMON_RELOAD",
            "FAIL_CLOSE_AFTER_DAEMON_RELOAD",
        )
        for seam in seam_names:
            with self.subTest(seam=seam):
                self.tearDown()
                self.setUp()
                triggered = False

                def crash(name: str, _unit: str | None) -> None:
                    nonlocal triggered
                    if name == seam and not triggered:
                        triggered = True
                        raise SimulatedCrash()

                executor = FakeExecutor(fail_at=1)
                with mock.patch.object(
                        HANDOFF, "MUTATION_SEAM_HOOK", crash):
                    with self.assertRaises(SimulatedCrash):
                        self.run_handoff(executor)
                self.assertTrue(triggered)
                self.assertEqual(
                    self.run_reconcile(FakeExecutor()),
                    "FAILED_CLOSED")

    def test_publish_crash_before_rename_reconciles_failed_closed(self) -> None:
        def crash(seam: str) -> None:
            if seam == "AFTER_TEMP_FSYNC":
                raise SimulatedCrash()

        with mock.patch.object(HANDOFF, "PUBLISH_SEAM_HOOK", crash):
            with self.assertRaises(SimulatedCrash):
                self.run_handoff(FakeExecutor())
        self.assertFalse(self.output.exists())
        self.assertEqual(
            self.run_reconcile(FakeExecutor()), "FAILED_CLOSED")

    def test_publish_crash_after_rename_recovers_complete_without_promotion(
            self) -> None:
        for selected in ("AFTER_RENAME", "AFTER_PARENT_FSYNC", "AFTER_REOPEN"):
            with self.subTest(seam=selected):
                self.tearDown()
                self.setUp()
                triggered = False

                def crash(seam: str) -> None:
                    nonlocal triggered
                    if seam == selected and not triggered:
                        triggered = True
                        raise SimulatedCrash()

                executor = FakeExecutor()
                with mock.patch.object(HANDOFF, "PUBLISH_SEAM_HOOK", crash):
                    with self.assertRaises(SimulatedCrash):
                        self.run_handoff(executor)
                self.assertTrue(self.output.exists())
                self.assertEqual(
                    self.run_reconcile(executor),
                    "WATCH_RETIRED_HANDOFF_COMPLETE")

    def test_failed_receipt_is_noreplace_and_cannot_be_promoted(self) -> None:
        failed = self.run_handoff(FakeExecutor(fail_at=1))
        payload = self.output.read_bytes()
        self.assertEqual(failed["status"], "FAILED_CLOSED")
        self.assertEqual(
            self.run_reconcile(FakeExecutor()), "FAILED_CLOSED")
        self.assertEqual(self.output.read_bytes(), payload)

    def test_corrupt_journal_fails_without_systemd_mutation(self) -> None:
        self.run_handoff(FakeExecutor())
        first = sorted((self.state / "journal").iterdir())[0]
        first.write_bytes(b"{}\n")
        executor = FakeExecutor()
        with self.assertRaisesRegex(
                HANDOFF.HandoffError, "HANDOFF_JOURNAL_INVALID"):
            self.run_reconcile(executor)
        self.assertEqual(executor.mutations, [])

    def test_output_body_tamper_is_rejected(self) -> None:
        self.run_handoff(FakeExecutor())
        document = json.loads(self.output.read_bytes())
        document["body_sha256"] = "sha256:" + "f" * 64
        self.output.write_bytes(HANDOFF.canonical_bytes(document))
        with self.assertRaisesRegex(
                HANDOFF.HandoffError, "HANDOFF_RECEIPT_INVALID"):
            self.run_reconcile(FakeExecutor())

    def test_production_allowlist_rejects_arbitrary_systemctl(self) -> None:
        executor = object.__new__(HANDOFF.ProductionExecutor)
        with mock.patch.object(
                HANDOFF.subprocess, "run",
                side_effect=AssertionError("subprocess must not run")):
            with self.assertRaisesRegex(
                    HANDOFF.HandoffError,
                    "HANDOFF_SYSTEMCTL_ARGUMENT_INVALID"):
                executor._run((HANDOFF.SYSTEMCTL, "start", "ssh.service"))

    def test_transient_inventory_is_bounded_and_pattern_strict(self) -> None:
        valid = mock.Mock(
            returncode=0, stderr=b"",
            stdout=(
                b"hepta-p1-shadow-host-round123.service loaded active running "
                b"fixture\n"
                b"hepta-p1-shadow-observer-round123.timer loaded active waiting "
                b"fixture\n"))
        with mock.patch.object(
                HANDOFF.subprocess, "run", return_value=valid) as runner:
            self.assertEqual(
                HANDOFF.ProductionExecutor._transient_units(),
                ("hepta-p1-shadow-host-round123.service",
                 "hepta-p1-shadow-observer-round123.timer"))
            arguments = runner.call_args.args[0]
            self.assertIn("hepta-p1-shadow-*.service", arguments)
            self.assertIn("hepta-p1-shadow-*.timer", arguments)
        foreign = mock.Mock(
            returncode=0, stderr=b"",
            stdout=b"ssh.service loaded active running foreign\n")
        with mock.patch.object(
                HANDOFF.subprocess, "run", return_value=foreign):
            with self.assertRaisesRegex(
                    HANDOFF.HandoffError,
                    "HANDOFF_TRANSIENT_INVENTORY_INVALID"):
                HANDOFF.ProductionExecutor._transient_units()

    def test_active_transient_observer_timer_blocks_handoff(self) -> None:
        snapshot = FakeExecutor().snapshot()
        snapshot["transient_units"] = {
            "hepta-p1-shadow-observer-round123.timer": {
                "load_state": "loaded", "active_state": "active",
                "sub_state": "waiting", "job": "",
                "unit_file_state": "transient",
            }}
        snapshot["watch_timer_count"] = 1
        snapshot["cleanup_residue_count"] = 1
        HANDOFF.validate_snapshot(snapshot)
        self.assertFalse(HANDOFF._watch_complete(snapshot))

    def test_session_bootstrap_lock_must_be_idle(self) -> None:
        sessions = self.root / "sessions"
        sessions.mkdir(mode=0o711)
        lock = sessions / ".session-bootstrap.lock"
        lock.write_bytes(b"")
        lock.chmod(0o600)
        with mock.patch.object(HANDOFF, "WATCH_SESSIONS", sessions):
            self.assertEqual(HANDOFF._session_authority_count(), 0)
            descriptor = os.open(lock, os.O_RDWR)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.assertEqual(HANDOFF._session_authority_count(), 1)
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)

    def test_directory_anchor_tolerates_unrelated_sibling_activity(
            self) -> None:
        anchor = self.root / "anchor"
        anchor.mkdir(mode=0o700)
        real_open = HANDOFF.os.open
        changed = False

        def racing_open(
            path: object, flags: int, mode: int = 0o777,
            *, dir_fd: int | None = None,
        ) -> int:
            nonlocal changed
            descriptor = real_open(path, flags, mode, dir_fd=dir_fd)
            if path == anchor.name and not changed:
                changed = True
                sibling = real_open(
                    anchor / "unrelated", os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600)
                os.close(sibling)
            return descriptor

        with mock.patch.object(HANDOFF.os, "open", side_effect=racing_open):
            descriptor = HANDOFF._open_anchored_directory(
                anchor, "HANDOFF_TEST_DIRECTORY_INVALID")
        try:
            self.assertTrue(changed)
            self.assertEqual(os.fstat(descriptor).st_ino, anchor.stat().st_ino)
        finally:
            os.close(descriptor)

    def test_reconcile_with_wrong_context_arguments_is_non_mutating(self) -> None:
        self.run_handoff(FakeExecutor(fail_at=1))
        executor = FakeExecutor()
        with self.assertRaisesRegex(
                HANDOFF.HandoffError, "HANDOFF_CONTEXT_ARGUMENT_MISMATCH"):
            HANDOFF.reconcile(
                output=self.root / "wrong.json",
                production_mode=HANDOFF.PRODUCTION_MODE,
                executor=self.production_executor(executor))
        self.assertEqual(executor.mutations, [])


if __name__ == "__main__":
    unittest.main()
