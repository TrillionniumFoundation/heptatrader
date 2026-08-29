#!/usr/bin/env python3

from __future__ import annotations

import copy
from contextlib import contextmanager
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest import mock


MODULE_PATH = (
    Path(__file__).resolve().parents[1] / "scripts" /
    "hepta_p1_paper_admission_verifier.py")
REPOSITORY = MODULE_PATH.parents[1]
sys.path.insert(0, str(REPOSITORY / "scripts"))
import build_heptatrader_clean_source_bundle as SOURCE_BUILDER  # noqa: E402
import build_heptatrader_release_validation_closure as RELEASE_BUILDER  # noqa: E402
import aggregate_hepta_execution_native_systemd_gate as NATIVE_AGGREGATE  # noqa: E402
import verify_heptatrader_clean_source_bundle as SOURCE_VERIFIER  # noqa: E402
import verify_heptatrader_runtime_package as RUNTIME_VERIFIER  # noqa: E402
import hepta_p1_watch_to_paper_handoff as HANDOFF_CONTRACT  # noqa: E402

SPEC = importlib.util.spec_from_file_location(
    "hepta_p1_paper_admission_verifier", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
VERIFIER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = VERIFIER
SPEC.loader.exec_module(VERIFIER)

ROUND = 114
INSTALL_GENERATION = 22
PREDECESSOR_INSTALL_GENERATION = 21
INSTALLED_FILE_COUNT = 128
PREDECESSOR_INSTALL_POINTER_SHA256 = (
    "sha256:2beeb507fcafbbfc2c93d2e4756fddf0b27e9872733ff97d28af47006461d406")
PREDECESSOR_PROFILE_RECEIPT_PATH = (
    "/var/lib/heptatrader/p1-watch-profile-receipts/round95-generation20.json")
PREDECESSOR_PROFILE_RECEIPT_FILE_SHA256 = (
    "sha256:c1557c1fe0bbab68bfc0c85148f2dcb3b32a2c8b75da7b229296d1b99daebd67")
PREDECESSOR_PROFILE_RECEIPT_BODY_SHA256 = (
    "sha256:e09712acbfed117a47ad5e86c63bbfe638ec38d89d7579e85b47409b57728fb2")
PREDECESSOR_PROFILE_RECEIPT_BYTES = 58196


def predecessor_activation_success(module=VERIFIER) -> dict[str, object]:
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


def predecessor_activation_failure(module=VERIFIER) -> dict[str, object]:
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


def noncertifying_gate_evidence() -> dict:
    return {
        "requested": False, "eligible": False, "provenance": None,
        "provenance_reopened_equal": False, "reviewed_base": None,
        "reviewed_buildkit": None, "buildx_toolchain": None,
        "isolated_builder": None, "isolated_builder_cleanup": None,
        "docker_socket_before": None, "docker_socket_after": None,
        "docker_socket_records_equal": False,
        "apparmor_before": None, "apparmor_after": None,
        "apparmor_records_equal": False,
        "docker_namespace_before": None, "docker_namespace_after": None,
        "docker_namespace_records_equal": False,
    }


class EvidenceFixture:
    def __init__(self, root: Path, now_ms: int):
        self.root = root
        self.now_ms = now_ms
        self.uid = os.geteuid()
        self.domain = "alpha"
        self.campaign = "p1-round114-campaign-a"
        self.paths = {
            name: root / f"{name}.json" for name in VERIFIER.INPUT_NAMES}
        self.paths["install_receipt"] = (
            root / "hepta-p1-round114-generation22-passive.json")
        self.paths["install_manifest"] = (
            root /
            "hepta-p1-round114-generation22-shadow-runtime.manifest.json")
        self.install_backup_root = str(
            root / "hepta-p1-round114-generation22-passive.backup")
        self.documents: dict[str, dict] = {}
        self._install_handoff_profile_fixture()
        self._build()

    @staticmethod
    def _body(fields):
        return {field: None for field in fields if field != "body_sha256"}

    def _write(self, name: str, document: dict) -> None:
        payload = (
            VERIFIER.pretty_baseline_bytes(document)
            if name in {
                "source_baseline", "network_gate_receipt",
                "native_gate_receipt", "agent_os_rootful_gate_receipt"} else
            VERIFIER.canonical_bytes(document))
        path = self.paths[name]
        path.write_bytes(payload)
        path.chmod(0o600)
        self.documents[name] = document

    def _rewrite(self, name: str, *, sealed: bool = True) -> None:
        document = self.documents[name]
        if sealed and "body_sha256" in document:
            body = dict(document)
            body.pop("body_sha256", None)
            document = VERIFIER.seal(body)
        self._write(name, document)

    def _handoff_profile_record(
        self, path: Path, *, sealed: bool = False,
    ) -> dict:
        metadata = path.stat()
        payload = path.read_bytes()
        value = {
            "path": str(path), "file_sha256": VERIFIER.digest_bytes(payload),
            "bytes": len(payload), "mode": metadata.st_mode,
            "uid": metadata.st_uid, "gid": metadata.st_gid,
            "nlink": metadata.st_nlink, "device": metadata.st_dev,
            "inode": metadata.st_ino, "mtime_ns": metadata.st_mtime_ns,
            "ctime_ns": metadata.st_ctime_ns,
        }
        if sealed:
            value["body_sha256"] = VERIFIER.strict_object(
                payload, "FIXTURE_INVALID")["body_sha256"]
        return value

    def _handoff_legacy_record(self, path: Path) -> dict:
        value = self._handoff_profile_record(path)
        value["sha256"] = value.pop("file_sha256")
        return value

    def _write_handoff_profile_document(
        self, path: Path, body: dict,
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.parent.chmod(0o700)
        path.write_bytes(VERIFIER.canonical_bytes(VERIFIER.seal(body)))
        path.chmod(0o600)

    def _install_handoff_profile_fixture(self) -> None:
        dormant = b"D" * VERIFIER.PAPER_PROFILE_DORMANT_BYTES
        watch = b"W" * VERIFIER.PAPER_PROFILE_WATCH_BYTES
        hardened_runtime = b"P" * VERIFIER.PAPER_RUNTIME_PROFILE_HARDENED_BYTES
        legacy_runtime = b"L" * VERIFIER.PAPER_RUNTIME_PROFILE_LEGACY_BYTES
        identity = b'{"identities":[]}\n'
        VERIFIER.PAPER_PROFILE_PATH = self.root / "handoff-trust" / "alpha.env"
        VERIFIER.PAPER_PROFILE_DORMANT_BACKUP_PATH = (
            self.root / "handoff-profile-backup" / "alpha.env")
        VERIFIER.PAPER_PROFILE_FORWARD_RETAINED_PATH = (
            self.root / "handoff-trust" / ".alpha.retained")
        VERIFIER.PAPER_PROFILE_FORWARD_PREIMAGE_PATH = (
            self.root / "handoff-profile-backup" / "preimage-evidence.json")
        VERIFIER.PAPER_PROFILE_FORWARD_TRANSITION_RECEIPT_PATH = (
            self.root / "handoff-profile-receipts" / "transition.json")
        VERIFIER.PAPER_PROFILE_DEPLOYMENT_RECEIPT_PATH = (
            self.root / "handoff-profile-receipts" / "deployment.json")
        VERIFIER.PAPER_PROFILE_CANDIDATE_PATH = (
            self.root / "handoff-trust" / ".alpha.candidate")
        VERIFIER.PAPER_PROFILE_RETIRED_WATCH_PATH = (
            self.root / "handoff-state" / "retired-watch.env")
        VERIFIER.PAPER_RUNTIME_PROFILE_PATH = (
            self.root / "handoff-trust" / "alpha.ib-paper.env")
        VERIFIER.PAPER_RUNTIME_PROFILE_CANDIDATE_PATH = (
            self.root / "handoff-trust" / ".alpha.ib-paper.candidate")
        VERIFIER.PAPER_RUNTIME_PROFILE_BACKUP_PATH = (
            self.root / "handoff-state" / "legacy-runtime-backup.env")
        VERIFIER.PAPER_RUNTIME_PROFILE_RETAINED_PATH = (
            self.root / "handoff-state" / "retained-legacy-runtime.env")
        VERIFIER.IDENTITY_MANIFEST_PATH = self.root / "handoff-identity.json"
        VERIFIER.KILL_SWITCH_PATH = self.root / "handoff-paper-kill-switch"
        VERIFIER.GLOBAL_KILL_SWITCH_PATH = (
            self.root / "handoff-global-kill-switch")
        VERIFIER.PAPER_CONTROL_GID = self.uid if self.uid == os.getegid() else (
            os.getegid())
        VERIFIER.GLOBAL_PAPER_CONTROL_GID = os.getegid()
        VERIFIER.PAPER_PROFILE_DORMANT_SHA256 = VERIFIER.digest_bytes(dormant)
        VERIFIER.PAPER_PROFILE_WATCH_SHA256 = VERIFIER.digest_bytes(watch)
        VERIFIER.PAPER_RUNTIME_PROFILE_HARDENED_SHA256 = (
            VERIFIER.digest_bytes(hardened_runtime))
        VERIFIER.PAPER_RUNTIME_PROFILE_LEGACY_SHA256 = (
            VERIFIER.digest_bytes(legacy_runtime))
        VERIFIER.DISABLED_IDENTITY_MANIFEST_SHA256 = VERIFIER.digest_bytes(
            identity)
        for path, payload, mode in (
            (VERIFIER.PAPER_PROFILE_PATH, dormant, 0o644),
            (VERIFIER.PAPER_PROFILE_DORMANT_BACKUP_PATH, dormant, 0o600),
            (VERIFIER.PAPER_PROFILE_FORWARD_RETAINED_PATH, dormant, 0o600),
            (VERIFIER.PAPER_PROFILE_RETIRED_WATCH_PATH, watch, 0o600),
            (VERIFIER.PAPER_RUNTIME_PROFILE_PATH, hardened_runtime, 0o644),
            (VERIFIER.PAPER_RUNTIME_PROFILE_BACKUP_PATH, legacy_runtime,
             0o600),
            (VERIFIER.PAPER_RUNTIME_PROFILE_RETAINED_PATH, legacy_runtime,
             0o600),
            (VERIFIER.IDENTITY_MANIFEST_PATH, identity, 0o600),
            (VERIFIER.KILL_SWITCH_PATH, b"engaged", 0o440),
            (VERIFIER.GLOBAL_KILL_SWITCH_PATH, b"engaged", 0o440),
        ):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.parent.chmod(0o700)
            if path.exists():
                path.chmod(0o600)
            path.write_bytes(payload)
            path.chmod(mode)
        preimage = {
            field: None for field in VERIFIER.HANDOFF_PROFILE_PREIMAGE_FIELDS
            if field != "body_sha256"
        }
        preimage.update({
            "schema": VERIFIER.PROFILE_PREIMAGE_SCHEMA, "version": 1,
            "status": VERIFIER.PROFILE_PREIMAGE_STATUS, "round": 114,
            "domain": "alpha", "transition_token": "round114-transition",
            "created_at_ms": self.now_ms - 50_000,
            "backup": self._handoff_legacy_record(
                VERIFIER.PAPER_PROFILE_DORMANT_BACKUP_PATH),
            "paper_authorized": False, "live_authorized": False,
            "mutation_attempted": False, "direct_broker_access": False,
        })
        self._write_handoff_profile_document(
            VERIFIER.PAPER_PROFILE_FORWARD_PREIMAGE_PATH, preimage)
        transition = {
            field: None for field in VERIFIER.HANDOFF_PROFILE_TRANSITION_FIELDS
            if field != "body_sha256"
        }
        transition.update({
            "schema": VERIFIER.PROFILE_TRANSITION_SCHEMA, "version": 2,
            "status": VERIFIER.PROFILE_TRANSITION_STATUS, "round": 114,
            "domain": "alpha", "transition_token": "round114-transition",
            "started_at_ms": self.now_ms - 45_000,
            "finished_at_ms": self.now_ms - 44_000,
            "target_path": str(VERIFIER.PAPER_PROFILE_PATH),
            "backup_path": str(VERIFIER.PAPER_PROFILE_DORMANT_BACKUP_PATH),
            "retained_target_path": str(
                VERIFIER.PAPER_PROFILE_FORWARD_RETAINED_PATH),
            "profile_content_changed": True, "target_written": True,
            "target_replaced": True, "services_started": False,
            "services_stopped": False, "services_restarted": False,
            "campaign_launched": False, "paper_authorized": False,
            "live_authorized": False, "mutation_attempted": False,
            "direct_broker_access": False,
            "backup": self._handoff_legacy_record(
                VERIFIER.PAPER_PROFILE_DORMANT_BACKUP_PATH),
            "retained_target": self._handoff_legacy_record(
                VERIFIER.PAPER_PROFILE_FORWARD_RETAINED_PATH),
        })
        self._write_handoff_profile_document(
            VERIFIER.PAPER_PROFILE_FORWARD_TRANSITION_RECEIPT_PATH, transition)
        transition_evidence = self._handoff_profile_record(
            VERIFIER.PAPER_PROFILE_FORWARD_TRANSITION_RECEIPT_PATH,
            sealed=True)
        deployment = {
            field: None for field in VERIFIER.PROFILE_RECEIPT_FIELDS
            if field != "body_sha256"
        }
        deployment.update({
            "schema": VERIFIER.PROFILE_DEPLOYMENT_SCHEMA, "version": 8,
            "status": VERIFIER.PROFILE_DEPLOYMENT_STATUS, "round": 114,
            "domain": "alpha", "target_path": str(VERIFIER.PAPER_PROFILE_PATH),
            "dormant_paper_to_watch_transition_receipt": {
                **transition_evidence,
                "sha256": transition_evidence["file_sha256"],
            },
        })
        deployment["dormant_paper_to_watch_transition_receipt"].pop(
            "file_sha256")
        self._write_handoff_profile_document(
            VERIFIER.PAPER_PROFILE_DEPLOYMENT_RECEIPT_PATH, deployment)
        self.handoff_profile_restoration = {
            "schema": VERIFIER.PROFILE_RESTORATION_SCHEMA, "version": 1,
            "status": VERIFIER.PROFILE_RESTORATION_STATUS,
            "target": self._handoff_profile_record(
                VERIFIER.PAPER_PROFILE_PATH),
            "dormant_backup": self._handoff_profile_record(
                VERIFIER.PAPER_PROFILE_DORMANT_BACKUP_PATH),
            "forward_retained_dormant": self._handoff_profile_record(
                VERIFIER.PAPER_PROFILE_FORWARD_RETAINED_PATH),
            "retired_watch": self._handoff_profile_record(
                VERIFIER.PAPER_PROFILE_RETIRED_WATCH_PATH),
            "forward_transition_receipt": transition_evidence,
            "profile_deployment_receipt": self._handoff_profile_record(
                VERIFIER.PAPER_PROFILE_DEPLOYMENT_RECEIPT_PATH, sealed=True),
            "forward_preimage_evidence": self._handoff_profile_record(
                VERIFIER.PAPER_PROFILE_FORWARD_PREIMAGE_PATH, sealed=True),
            "candidate_path": str(VERIFIER.PAPER_PROFILE_CANDIDATE_PATH),
            "retired_watch_path": str(
                VERIFIER.PAPER_PROFILE_RETIRED_WATCH_PATH),
            "exchange_method": "RENAME_EXCHANGE",
            "forward_only_after_exchange": True,
            "restore_intent_record_sha256": VERIFIER.digest_bytes(b"intent"),
            "restore_exchange_record_sha256": VERIFIER.digest_bytes(
                b"exchange"),
        }
        self.handoff_runtime_profile_hardening = {
            "schema": VERIFIER.PAPER_RUNTIME_PROFILE_HARDENING_SCHEMA,
            "version": 1,
            "status": VERIFIER.PAPER_RUNTIME_PROFILE_HARDENING_STATUS,
            "target": self._handoff_profile_record(
                VERIFIER.PAPER_RUNTIME_PROFILE_PATH),
            "legacy_backup": self._handoff_profile_record(
                VERIFIER.PAPER_RUNTIME_PROFILE_BACKUP_PATH),
            "retained_legacy": self._handoff_profile_record(
                VERIFIER.PAPER_RUNTIME_PROFILE_RETAINED_PATH),
            "candidate_path": str(
                VERIFIER.PAPER_RUNTIME_PROFILE_CANDIDATE_PATH),
            "retained_legacy_path": str(
                VERIFIER.PAPER_RUNTIME_PROFILE_RETAINED_PATH),
            "exchange_method": "RENAME_EXCHANGE",
            "forward_only_after_exchange": True,
            "harden_intent_record_sha256": VERIFIER.digest_bytes(
                b"runtime-intent"),
            "harden_exchange_record_sha256": VERIFIER.digest_bytes(
                b"runtime-exchange"),
        }

    def reference(self, name: str) -> dict[str, str]:
        document = self.documents[name]
        payload = self.paths[name].read_bytes()
        return {
            "path": str(self.paths[name]),
            "file_sha256": VERIFIER.digest_bytes(payload),
            "body_sha256": document.get(
                "body_sha256", VERIFIER.digest_bytes(
                    VERIFIER.canonical_bytes(document))),
        }

    def _build(self) -> None:
        source_payloads = {
            path: path.encode("utf-8")
            for path in (
                set(VERIFIER.DUAL_GATE_SOURCE_MODES) |
                set(VERIFIER.PAPER_GATE_SOURCE_MODES) |
                set(VERIFIER.P1_LIVENESS_SOURCE_MODES) |
                set(VERIFIER.AGENT_OS_SOURCE_MODES) |
                set(VERIFIER.NETWORK_GATE_SOURCE_MODES))
        }
        source_payloads.update({
            "scripts/frozen.py": b"x",
            "scripts/run_hepta_broker_network_rootful_gate.py": b"n",
            "scripts/run_hepta_broker_network_hard_isolation_gate.py": b"h",
            "scripts/run_hepta_p1_dual_domain_rootful_gate.py": b"d",
            "scripts/run_hepta_paper_domain_rootful_systemd_gate.py": b"r",
            "scripts/hepta_p1_watch_to_paper_handoff.py": b"w",
            "scripts/hepta_p1_safety_soak_auditor.py": b"auditor",
            "scripts/hepta_p1_paper_zero_exposure_attestor.py": b"attestor",
            "scripts/hepta_p1_paper_zero_exposure_snapshot_producer.py":
                b"snapshot",
            "scripts/hepta_rootful_systemd_environment_provenance.py":
                b"review-verifier",
            "scripts/build_heptatrader_release_validation_closure.py":
                b"release-builder",
            "scripts/verify_heptatrader_release_validation_closure.py":
                b"release-verifier",
            "scripts/run_hepta_agent_os_rootful_systemd_e2e_gate.py":
                b"agent-os-gate",
        })
        source_records = [
            {
                "path": path,
                "mode": (
                    VERIFIER.NETWORK_GATE_SOURCE_MODES.get(path) or
                    VERIFIER.AGENT_OS_SOURCE_MODES.get(path) or
                    VERIFIER.DUAL_GATE_SOURCE_MODES.get(path) or
                    VERIFIER.PAPER_GATE_SOURCE_MODES.get(path) or
                    VERIFIER.P1_LIVENESS_SOURCE_MODES.get(path) or
                    "0755"),
                "size": len(source_payloads[path]),
                "sha256": VERIFIER.digest_bytes(source_payloads[path]),
            }
            for path in sorted(source_payloads)
        ]
        source_sha = VERIFIER.digest_bytes(json.dumps(
            source_records, ensure_ascii=True, allow_nan=False,
            sort_keys=True, separators=(",", ":")).encode("utf-8"))
        strict_source_files_sha = VERIFIER.digest_bytes(
            b"strict-source-full-file-closure").removeprefix("sha256:")
        self.source_payloads = copy.deepcopy(source_payloads)
        self.source_records = copy.deepcopy(source_records)
        self.source_sha = source_sha
        baseline = {
            "schema": "hepta.versioned-source-baseline.v1",
            "version": "1.0.0-round114",
            "generated_at": datetime.fromtimestamp(
                (self.now_ms - 60_000) / 1000,
                tz=timezone.utc).isoformat(),
            "git_head": "a" * 40,
            "source_manifest": {
                "file_count": len(source_records), "sha256": source_sha,
                "files": source_records,
            },
            "source_baseline_frozen": True,
            "clean_checkout_certified": True,
            "release_authorized": False,
            "paper_authorized": False,
            "live_authorized": False,
            "worktree_status_entry_count": 0,
            "blocked_reason": None,
            "excluded_unsafe_tree": "compat/unsafe-direct-broker",
        }
        self._write("source_baseline", baseline)

        self.agent_binary_records = {}
        for binary, runtime_path in (
                VERIFIER.AGENT_OS_RUNTIME_BINARY_PATHS.items()):
            payload = ("agent-binary:" + binary + "\n").encode("ascii")
            self.agent_binary_records[binary] = {
                "path": runtime_path, "mode": "0755", "size": len(payload),
                "sha256": VERIFIER.digest_bytes(payload),
            }
        install_files = [
            {
                "path": "usr/bin/hepta", "mode": "0755", "size": 1,
                "sha256": VERIFIER.digest_bytes(b"a"),
            },
            {
                "path": "usr/libexec/hepta-shadow-host-installer",
                "mode": "0755", "size": 1,
                "sha256": VERIFIER.digest_bytes(b"i"),
            },
        ]
        for source_path, installed_path in VERIFIER.PRODUCTION_PRODUCER_PATHS.values():
            payload = source_payloads[source_path]
            install_files.append({
                "path": installed_path, "mode": "0755", "size": len(payload),
                "sha256": VERIFIER.digest_bytes(payload),
            })
        install_files.extend(
            copy.deepcopy(record)
            for record in self.agent_binary_records.values())
        while len(install_files) < INSTALLED_FILE_COUNT:
            index = len(install_files)
            payload = f"round114-shadow-runtime-fixture:{index}\n".encode(
                "ascii")
            install_files.append({
                "path": f"usr/share/hepta-shadow-runtime/{index:03d}.fixture",
                "mode": "0644", "size": len(payload),
                "sha256": VERIFIER.digest_bytes(payload),
            })
        assert len(install_files) == INSTALLED_FILE_COUNT
        install_files.sort(key=lambda item: item["path"])
        archive_sha = VERIFIER.digest_bytes(b"archive")
        installer_sha = VERIFIER.digest_bytes(b"i")
        manifest = {
            "schema": "hepta.shadow-runtime-install-manifest.v2",
            "version": 2,
            "archive_sha256": archive_sha,
            "source_baseline_sha256": source_sha,
            "installer_sha256": installer_sha,
            "files": install_files,
            "paper_authorized": False,
            "live_authorized": False,
            "mutation_attempted": False,
            "direct_broker_access": False,
        }
        self._write("install_manifest", manifest)

        transaction_lock = {
            "path": "/var/lib/hepta/.shadow-runtime-install.lock",
            "device": 1, "inode": 2, "nlink": 1, "uid": 0, "gid": 0,
            "mode": "0600", "size": 0, "mtime_ns": 10, "ctime_ns": 11,
            "created_during_transaction": False, "persistent": True,
            "held_during_transaction": True,
        }
        install_preflight = {
            "domain": self.domain,
            "paper_units": {
                unit: "inactive" for unit in VERIFIER.INSTALL_PAPER_UNITS},
            "installation_blocking_units": {
                unit: "inactive" for unit in VERIFIER.INSTALL_BLOCKING_UNITS},
            "campaign_policy_count": 0,
            "kill_switch_engaged": True,
            "broker_egress_deny_all": True,
        }

        receipt = self._body(VERIFIER.INSTALL_RECEIPT_FIELDS)
        receipt.update({
            "schema": "hepta.shadow-runtime-install-receipt.v4",
            "version": 4,
            "finished_at_ms": self.now_ms - 50_000,
            "domain": self.domain,
            "archive_sha256": archive_sha,
            "source_baseline_sha256": source_sha,
            "installer_sha256": installer_sha,
            "installed_file_count": len(install_files),
            "installed_paths_sha256": VERIFIER.digest_bytes(
                VERIFIER.canonical_bytes([item["path"] for item in install_files])),
            "backup_root": self.install_backup_root,
            "replaced_file_count": 1,
            "new_file_count": len(install_files) - 1,
            "default_deny_identity_manifest": {
                "destination": "/etc/heptatrader/deny.json",
                "archive_path": "etc/heptatrader/deny.json",
                "uid": 0, "gid": 0, "mode": "0600", "size": 10,
                "sha256": VERIFIER.digest_bytes(b"deny"), "installed": True,
                "preexisting_backed_up": False, "new_file": True,
            },
            "reader_gid": 1000,
            "install_generation": INSTALL_GENERATION,
            "predecessor_install_generation":
                PREDECESSOR_INSTALL_GENERATION,
            "predecessor_current_install_pointer_file_sha256":
                PREDECESSOR_INSTALL_POINTER_SHA256,
            "transaction_lock": transaction_lock,
            "preflight_before": install_preflight,
            "preflight_after": install_preflight,
            "preflight_continuity_claimed": False,
            "paper_authorized": False,
            "live_authorized": False,
            "mutation_attempted": False,
            "direct_broker_access": False,
            "services_started": False,
            "services_enabled": False,
            "status": "PASSIVE_INSTALL_COMPLETE",
        })
        self._write("install_receipt", VERIFIER.seal(receipt))

        pointer = self._body(VERIFIER.INSTALL_POINTER_FIELDS)
        pointer.update({
            "schema": "hepta.shadow-runtime-current-install.v1",
            "version": 1,
            "generation": INSTALL_GENERATION,
            "domain": self.domain,
            "backup_root": receipt["backup_root"],
            "manifest_path": str(self.paths["install_manifest"]),
            "manifest_file_sha256": VERIFIER.digest_bytes(
                self.paths["install_manifest"].read_bytes()),
            "receipt_path": str(self.paths["install_receipt"]),
            "receipt_file_sha256": VERIFIER.digest_bytes(
                self.paths["install_receipt"].read_bytes()),
            "archive_sha256": archive_sha,
            "source_baseline_sha256": source_sha,
            "installer_sha256": installer_sha,
            "installed_file_count": len(install_files),
            "installed_paths_sha256": receipt["installed_paths_sha256"],
            "transaction_lock_path": "/var/lib/hepta/.shadow-runtime-install.lock",
            "paper_authorized": False,
            "live_authorized": False,
            "mutation_attempted": False,
            "direct_broker_access": False,
        })
        self._write("install_pointer", VERIFIER.seal(pointer))

        evidence = {
            "schema": "hepta.shadow-runtime-install-consumption-evidence.v3",
            "version": 3,
            "receipt_path": str(self.paths["install_receipt"]),
            "receipt_file_sha256": VERIFIER.digest_bytes(
                self.paths["install_receipt"].read_bytes()),
            "receipt_body_sha256": self.documents[
                "install_receipt"]["body_sha256"],
            "manifest_path": str(self.paths["install_manifest"]),
            "manifest_file_sha256": VERIFIER.digest_bytes(
                self.paths["install_manifest"].read_bytes()),
            "archive_sha256": archive_sha,
            "source_baseline_sha256": source_sha,
            "installer_sha256": installer_sha,
            "installed_file_count": len(install_files),
            "installed_paths_sha256": receipt["installed_paths_sha256"],
            "closure_sha256": VERIFIER.digest_bytes(b"closure"),
            "transaction_lock": transaction_lock,
            "default_deny_identity_sha256": VERIFIER.digest_bytes(b"deny"),
            "lock_mode": "exclusive",
            "verified_under_lock": True,
            "domain": self.domain,
            "backup_root": receipt["backup_root"],
            "paper_authorized": False,
            "live_authorized": False,
            "mutation_attempted": False,
            "direct_broker_access": False,
            "current_install_pointer_path": str(self.paths["install_pointer"]),
            "current_install_pointer_file_sha256": VERIFIER.digest_bytes(
                self.paths["install_pointer"].read_bytes()),
            "install_generation": INSTALL_GENERATION,
            "predecessor_install_generation":
                PREDECESSOR_INSTALL_GENERATION,
            "predecessor_current_install_pointer_file_sha256":
                receipt["predecessor_current_install_pointer_file_sha256"],
        }
        def file_evidence(path: str, marker: bytes, mode: int, *,
                          legacy: bool = False) -> dict:
            value = {
                "path": path, "sha256": VERIFIER.digest_bytes(marker),
                "bytes": len(marker), "device": 1, "inode": 100 + len(marker),
                "mode": stat.S_IFREG | mode, "nlink": 1, "uid": 0, "gid": 0,
                "mtime_ns": 1000, "ctime_ns": 1001,
            }
            if legacy:
                value["body_sha256"] = VERIFIER.digest_bytes(b"legacy-body")
            return value

        paper_state = {
            "LoadState": "loaded", "ActiveState": "inactive",
            "SubState": "dead", "Job": "",
        }
        profile_preflight = {
            "gateway_units": {"hepta-tool-gateway@alpha.service": {
                "ActiveState": "inactive"}},
            "gateway_masks": {"hepta-tool-gateway@alpha.service": {
                "persistent": "/dev/null"}},
            "gateway_unit_closure": {"verified": True},
            "systemd_manager": {"Version": "255"},
            "manager_unit_contracts": {"verified": True},
            "broker_egress_unit": {"ActiveState": "inactive"},
            "broker_egress_check": {"status": "PASS"},
            "paper_units": {"hepta-execution-ib-paper.service": paper_state},
            "campaign_policy_count": 0, "kill_switch_engaged": True,
            "watch_boundary": {"authority_count": 0},
            "broker_egress_deny_all_observed": True,
        }
        target_evidence = file_evidence(
            "/etc/heptatrader/trust-domains/alpha.env", b"profile", 0o644)
        profile = self._body(VERIFIER.PROFILE_RECEIPT_FIELDS)
        profile.update({
            "schema": "hepta.p1-watch-profile-deployment-receipt.v8",
            "version": 8,
            "status": "OFFLINE_PASSIVE_WATCH_PROFILE_REATTESTED",
            "round": 114,
            "domain": self.domain,
            "started_at_ms": self.now_ms - 45_000,
            "finished_at_ms": self.now_ms - 44_000,
            "target_path": "/etc/heptatrader/trust-domains/alpha.env",
            "receipt_staging_path": "/var/lib/hepta/.profile.tmp",
            "target_before": target_evidence, "target_after": target_evidence,
            "target_final": target_evidence,
            "legacy_receipt": file_evidence(
                "/var/lib/hepta/legacy-receipt.json", b"legacy", 0o600,
                legacy=True),
            "legacy_backup": file_evidence(
                "/var/lib/hepta/legacy-backup.env", b"old", 0o600),
            "legacy_retained_target": file_evidence(
                "/etc/heptatrader/.legacy-retained.env", b"old", 0o644),
            "preflight_before": profile_preflight,
            "preflight_after": profile_preflight,
            "preflight_final": profile_preflight,
            "profile_content_changed": False,
            "target_written": False, "target_replaced": False,
            "services_started": False, "services_stopped": False,
            "services_restarted": False, "campaign_launched": False,
            "paper_authorized": False, "live_authorized": False,
            "mutation_attempted": False, "direct_broker_access": False,
            "activation_receipt_eligible": False,
            "preflight_reusable_for_activation": False,
            "broker_loaded_source_attested": False,
            "broker_deny_all_continuity_attested": False,
            "fresh_activation_transaction_required": True,
            "shadow_install_evidence": evidence,
            "predecessor_profile_receipt": {
                "path": PREDECESSOR_PROFILE_RECEIPT_PATH,
                "sha256": PREDECESSOR_PROFILE_RECEIPT_FILE_SHA256,
                "body_sha256":
                    PREDECESSOR_PROFILE_RECEIPT_BODY_SHA256,
                "bytes": PREDECESSOR_PROFILE_RECEIPT_BYTES,
                "device": 1, "inode": 222, "mode": stat.S_IFREG | 0o600,
                "nlink": 1, "uid": 0, "gid": 0,
                "mtime_ns": 2000, "ctime_ns": 2001,
            },
            "dormant_paper_to_watch_transition_receipt": file_evidence(
                VERIFIER.DORMANT_PAPER_TO_WATCH_TRANSITION_RECEIPT_PATH,
                b"sealed-transition-receipt", 0o600, legacy=True),
        })
        self._write("profile_receipt", VERIFIER.seal(profile))

        deny_sha = VERIFIER.digest_bytes(b"deny-policy")
        broker_after = {field: "value" for field in VERIFIER.BROKER_AFTER_FIELDS}
        broker_after.update({
            "unit": "hepta-broker-egress-policy.service",
            "active_state": "active", "sub_state": "running", "main_pid": 20,
            "exec_main_start_timestamp_monotonic_us": 100,
            "process_starttime_ticks": 200, "tasks_current": 1,
            "authorized_connectors": 0, "authorized_uids": [],
            "protected_ports": 4, "deny_all_policy_sha256": deny_sha,
        })
        for field in (
            "interpreter_sha256", "credential_source_sha256",
            "installed_source_sha256", "cmdline_sha256", "unit_contract_sha256",
        ):
            broker_after[field] = VERIFIER.digest_bytes(field.encode())
        gateway_after = {
            field: "value" for field in VERIFIER.GATEWAY_AFTER_FIELDS}
        gateway_after.update({
            "unit": "hepta-tool-gateway@alpha.service",
            "active_state": "active", "sub_state": "running",
            "gateway_main_pid": 30,
            "gateway_exec_main_start_timestamp_monotonic_us": 100,
            "process_starttime_ticks": 200,
            "execution_remote_mode": "SIMULATOR", "tool_account": "SIM",
            "execution_domain_id": "SIM:alpha", "tool_allow_trade": "0",
            "session_templates": "watch", "contract_bindings": "EUR.USD",
            "gateway_socket_device": 1, "gateway_socket_inode": 2,
            "supervisor_socket_device": 1, "supervisor_socket_inode": 3,
        })
        for field in (
            "gateway_executable_sha256", "domain_config_sha256",
            "gateway_profile_sha256", "gateway_process_profile_sha256",
            "unit_contract_sha256",
        ):
            gateway_after[field] = VERIFIER.digest_bytes(field.encode())
        reconcile_timer = {
            "unit": "hepta-p1-watch-activation-reconcile.timer",
            "load_state": "loaded", "active_state": "active",
            "sub_state": "waiting", "job": "", "unit_file_state": "enabled",
            "unit_contract_sha256": VERIFIER.digest_bytes(b"timer"),
        }
        watch_boundary = {
            "export_absent": True, "sessions_authority_count": 0,
            "private_authority_count": 0,
            "custodian_transaction_absent": True,
            "session_bootstrap_idle_lock_observed": True,
        }
        activation = self._body(VERIFIER.ACTIVATION_RECEIPT_FIELDS)
        activation.update({
            "schema": "hepta.p1-watch-activation-receipt.v4",
            "version": 4, "status": "WATCH_GATEWAY_ACTIVATED",
            "round": 114, "domain": self.domain,
            "started_at_ms": self.now_ms - 40_000,
            "completed_at_ms": self.now_ms - 39_000,
            "boot_id": "00000000-0000-4000-8000-000000000001",
            "profile_deployment_receipt_path": str(self.paths["profile_receipt"]),
            "profile_deployment_receipt_file_sha256": VERIFIER.digest_bytes(
                self.paths["profile_receipt"].read_bytes()),
            "profile_deployment_receipt_body_sha256": self.documents[
                "profile_receipt"]["body_sha256"],
            "profile_sha256": VERIFIER.digest_bytes(b"profile"),
            "profile_bytes": 10, "journal_sha256": VERIFIER.digest_bytes(b"j"),
            "broker_before": {
                "policy_sha256": deny_sha, "authorized_connectors": 0,
                "authorized_uids": [], "protected_ports": 4,
            },
            "broker_after": broker_after, "gateway_after": gateway_after,
            "reconcile_timer": reconcile_timer,
            "paper_units": {"hepta-execution-ib-paper.service": {
                "ActiveState": "inactive", "SubState": "dead", "Job": ""}},
            "kill_switch_engaged": True, "watch_boundary": watch_boundary,
            "stale_bundles": [],
            "systemctl_mutations": [["/usr/bin/systemctl", "daemon-reload"]],
            "fresh_activation_transaction": True,
            "gateway_activated": True, "gateway_profile_loaded": True,
            "gateway_contract_binding_loaded": True,
            "broker_loaded_source_attested": True,
            "broker_deny_all_continuity_attested": True,
            "watch_authority_provisioned": False,
            "campaign_launched": False,
            "paper_authorized": False, "live_authorized": False,
            "mutation_attempted": False, "direct_broker_access": False,
            "admission_prerequisite_satisfied": True,
            "paper_prerequisite_satisfied": False,
            "shadow_install_evidence": evidence,
            "predecessor_activation_success": predecessor_activation_success(),
            "predecessor_activation_failure": predecessor_activation_failure(),
        })
        self._write("activation_receipt", VERIFIER.seal(activation))

        audit = self._body(VERIFIER.P1_AUDIT_FIELDS)
        audit.update({
            "schema": "hepta.p1-safety-soak-audit-receipt.v1",
            "version": 1, "phase": "P1_SHADOW", "verdict": "GO",
            "campaign_id": self.campaign, "domain_id": self.domain,
            "independent_auditor_id": "auditor-a",
            "audited_at_ms": self.now_ms - 30_000,
            "campaign_spec_file_sha256": VERIFIER.digest_bytes(b"spec-file"),
            "campaign_spec_body_sha256": VERIFIER.digest_bytes(b"spec-body"),
            "freeze_bundle": {
                "path": str(self.root / "freeze-bundle.json"),
                "file_sha256": VERIFIER.digest_bytes(b"freeze-file"),
                "body_sha256": VERIFIER.digest_bytes(b"freeze-body"),
            },
            "campaign_runtime": {
                "schema": VERIFIER.P1_CAMPAIGN_RUNTIME_SCHEMA,
                "path": str(self.root / "campaign-runtime.json"),
                "file_sha256": VERIFIER.digest_bytes(b"runtime-file"),
                "body_sha256": VERIFIER.digest_bytes(b"runtime-body"),
            },
            "producer": {
                "path": "/usr/libexec/hepta-p1-safety-soak-auditor",
                "file_sha256": VERIFIER.digest_bytes(
                    source_payloads["scripts/hepta_p1_safety_soak_auditor.py"]),
            },
            "production_mode": "PRODUCTION_ROOT_AUDIT",
            "source_manifest_sha256": source_sha,
            "policy_sha256": VERIFIER.digest_bytes(b"policy"),
            "strategy_sha256": VERIFIER.digest_bytes(b"strategy"),
            "evaluated_interval": {
                "clock_id": "CLOCK_BOOTTIME",
                "boot_id": "00000000-0000-4000-8000-000000000001",
                "start_boottime_ns": 1_000_000_000,
                "end_boottime_ns": 1_000_000_000 +
                    VERIFIER.MINIMUM_BOOTTIME_DURATION_NS,
                "duration_ns": VERIFIER.MINIMUM_BOOTTIME_DURATION_NS,
                "maximum_checkpoint_gap_ns": 60 * 1_000_000_000,
                "continuity_origin_ms": self.now_ms - 2 * 60 * 60 * 1000,
                "continuity_end_ms": self.now_ms - 30_000,
                "continuity_final_slot": 999,
                "consecutive": True,
            },
            "counts": {
                "launcher_receipts": 10, "verified_closures": 10,
                "continuity_checkpoints": 1000,
                "declared_trading_days": VERIFIER.MINIMUM_TRADING_DAYS,
                "observed_trading_days": VERIFIER.MINIMUM_TRADING_DAYS,
                "scheduled_decisions": 210, "decision_receipts": 210,
                "eligible_decisions": 200, "complete_eligible_decisions": 200,
                "incomplete_eligible_decisions": 0, "catch_up_decisions": 0,
                "planned_faults": 7, "fault_results": 7,
                "authority_snapshots": 100, "cleanup_snapshots": 18,
            },
            "completeness": {
                "numerator": 200, "denominator": 200, "ppm": 1_000_000,
                "strictly_greater_than_99_percent": True,
            },
            "checked_artifacts": [{
                "role": "launcher_receipt",
                "path": str(self.root / "audited-launcher.json"),
                "file_sha256": VERIFIER.digest_bytes(b"launcher-file"),
                "body_sha256": VERIFIER.digest_bytes(b"launcher-body"),
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
                "required_subject_count": 18,
                "verified_subject_count": 18, "complete": True,
            },
            "p1_safety_soak_gate_satisfied": True,
            "paper_test_admission_candidate": False,
            "safest_allowed_next_action":
                "CONTINUE_REMAINING_PAPER_ADMISSION_GATES",
            "paper_authorized": False, "live_authorized": False,
            "mutation_authorized": False, "direct_broker_access": False,
        })
        self._write("p1_audit_receipt", VERIFIER.seal(audit))

        def bare(payload: bytes) -> str:
            return VERIFIER.digest_bytes(payload).removeprefix("sha256:")

        def source_input(path: str) -> dict:
            record = next(
                item for item in source_records if item["path"] == path)
            return {
                "sha256": record["sha256"].removeprefix("sha256:"),
                "size": record["size"], "mode": record["mode"],
            }

        gate_started = self.now_ms - 50_000
        gate_completed = self.now_ms - 40_000
        gate_expires = self.now_ms + 120_000
        boot_id = "01234567-89ab-cdef-0123-456789abcdef"
        base_reference = "registry.example/hepta/systemd@sha256:" + "1" * 64
        base_image_id = "sha256:" + "2" * 64
        buildkit_reference = (
            "registry.example/hepta/buildkit@sha256:" + "3" * 64)
        buildkit_image_id = "sha256:" + "4" * 64

        def reviewed_body(kind: str) -> dict:
            common = {
                "schema": VERIFIER.REVIEWED_PROVENANCE_SCHEMAS[kind],
                "decision": "GO", "issued_at_ms": self.now_ms - 100_000,
                "expires_at_ms": gate_expires,
            }
            if kind == "base":
                return {
                    **common, "image_id": base_image_id,
                    "repo_digest": base_reference,
                    "labels_sha256": VERIFIER.digest_bytes(json.dumps({
                            "io.hepta.rootful-systemd-base.offline-ready":
                                "true",
                            "io.hepta.rootful-systemd-base.version": "1",
                        }, sort_keys=True, separators=(",", ":")).encode()),
                }
            if kind == "builder":
                return {
                    **common, "image_id": buildkit_image_id,
                    "repo_digest": buildkit_reference,
                    "config_sha256": VERIFIER.digest_bytes(b"config"),
                    "buildkit_version": "v0.13.0",
                    "buildx_version": "0.14.0",
                    "buildx_binary_sha256": VERIFIER.digest_bytes(b"buildx"),
                    "docker_server_version": "29.0.0",
                    "docker_server_api_version": "1.50",
                    "docker_server_git_commit": "docker-commit",
                }
            if kind == "apparmor":
                return {
                    **common, "profile": "hepta-systemd-gate",
                    "policy_source_sha256": VERIFIER.digest_bytes(b"policy"),
                    "profile_sha256": VERIFIER.digest_bytes(b"profile"),
                    "raw_sha256": VERIFIER.digest_bytes(b"raw"),
                    "raw_abi": "v8",
                }
            return {
                **common, "docker_daemon_id": "daemon-a",
                "docker_daemon_pid": 42,
                "docker_daemon_start_time_ticks": 100,
                "host_boot_id": boot_id,
                "host_namespace_name": "root", "host_namespace_level": 0,
                "host_namespace_stacked": False,
                "daemon_namespace_name": "root",
                "daemon_namespace_level": 0,
                "daemon_namespace_stacked": False,
            }

        def reviewed_record(kind: str, *, paper: bool, index: int) -> dict:
            body = reviewed_body(kind)
            result = {
                **body,
                "document_sha256": VERIFIER.digest_bytes(
                    VERIFIER.canonical_bytes(body)),
                "root_owned": True, "canonical_json": True,
                "mode": "0400",
                "identity_sha256": VERIFIER.digest_bytes(
                    f"identity-{kind}".encode()),
            }
            if paper:
                result.update({
                    "path": f"/var/lib/hepta/provenance/{kind}.json",
                    "device": 1, "inode": 100 + index, "nlink": 1,
                    "uid": 0, "gid": 0,
                })
            return result

        def certification(*, paper: bool, run_id: str) -> dict:
            provenance = {
                kind: reviewed_record(kind, paper=paper, index=index)
                for index, kind in enumerate(
                    ("base", "builder", "apparmor", "docker_namespace"), 1)
            }
            reviewed_base = {
                "reference": base_reference, "id": base_image_id,
                "repo_digests": [base_reference], "os": "linux",
                "architecture": "amd64", "declared_volumes": 0,
                "onbuild_instructions": 0,
                "labels_sha256": provenance["base"]["labels_sha256"],
                "production_approved": True,
                "reviewed_provenance": provenance["base"],
            }
            reviewed_buildkit = {
                "reference": buildkit_reference, "id": buildkit_image_id,
                "bare_id": buildkit_image_id.removeprefix("sha256:"),
                "repo_digests": [buildkit_reference], "os": "linux",
                "architecture": "amd64",
                "config_sha256": provenance["builder"]["config_sha256"],
                "config_labels": {}, "entrypoint": ["buildkitd"],
                "production_approved": True,
                "reviewed_provenance": provenance["builder"],
            }
            builder_name = (
                "hepta-paper-domain-isolated-" if paper else
                "hepta-p1-dual-isolated-") + run_id
            node_name = builder_name + "0"
            container_name = "buildx_buildkit_" + node_name
            names = {
                "builder": builder_name, "node": node_name,
                "container": container_name,
                "volume": container_name + "_state",
            }
            purpose = (
                "paper-domain-rootful-systemd-gate" if paper else
                "p1-dual-domain-rootful-gate")
            common_labels = {
                "io.hepta.purpose": purpose,
                "io.hepta.run-id": run_id,
                "io.hepta.buildkit-image-id": buildkit_image_id,
                "io.hepta.buildx-builder": builder_name,
            }
            state_labels = {
                **common_labels,
                "io.hepta.role": "isolated-buildkit-state",
            }
            daemon_labels = {
                **common_labels,
                "io.hepta.role": "isolated-buildkit-daemon",
            }
            container_id = "5" * 64
            container_common = {
                "container_id": container_id,
                "name": names["container"], "network_mode": "none",
                "privileged": True, "bind_mounts": 0, "devices": 0,
                "published_ports": 0, "labels": daemon_labels,
            }
            isolated_builder = {
                "names": names, "container_id": container_id,
                "volume": {
                    "name": names["volume"], "driver": "local",
                    "scope": "local", "labels": state_labels,
                    "mountpoint_sha256": VERIFIER.digest_bytes(b"mount"),
                },
                "container_before_start": {
                    **container_common, "running": False},
                "container_running": {**container_common, "running": True},
                "runtime": {
                    "builder": names["builder"], "node": names["node"],
                    "driver": "docker-container", "status": "running",
                    "buildkit_version":
                        provenance["builder"]["buildkit_version"],
                },
            }
            socket = {
                "device": 1, "inode": 80, "mode": "0660", "uid": 0,
                "gid": 999, "owner_root": True, "world_writable": False,
            }
            apparmor = {
                "profile": "hepta-systemd-gate", "mode": "enforce",
                "attach": "hepta-systemd-gate", "learning_count": 0,
                "profile_sha256": provenance["apparmor"]["profile_sha256"],
                "raw_sha256": provenance["apparmor"]["raw_sha256"],
                "raw_abi": provenance["apparmor"]["raw_abi"],
                "raw_data_id": "1", "profile_inventory_count": 1,
                "profile_inventory_sha256": VERIFIER.digest_bytes(b"inventory"),
                "namespace": {"name": "root", "level": 0, "stacked": False},
                "reviewed_provenance": provenance["apparmor"],
            }
            namespace = {
                "docker_daemon_id": "daemon-a", "docker_daemon_pid": 42,
                "docker_daemon_start_time_ticks": 100,
                "docker_daemon_comm": "dockerd",
                "docker_daemon_process_inode": 900, "host_boot_id": boot_id,
                "host_namespace": {
                    "name": "root", "level": 0, "stacked": False},
                "daemon_namespace": {
                    "name": "root", "level": 0, "stacked": False},
                "same_apparmor_namespace_attested": True,
                "reviewed_provenance": provenance["docker_namespace"],
            }
            return {
                "requested": True, "eligible": True,
                "provenance": provenance, "provenance_reopened_equal": True,
                "reviewed_base": reviewed_base,
                "reviewed_buildkit": reviewed_buildkit,
                "buildx_toolchain": {
                    "buildx_path_sha256": VERIFIER.digest_bytes(b"path"),
                    "buildx_version": provenance["builder"]["buildx_version"],
                    "buildx_binary_sha256":
                        provenance["builder"]["buildx_binary_sha256"],
                    "docker_server_version": "29.0.0",
                    "docker_server_api_version": "1.50",
                    "docker_server_git_commit": "docker-commit",
                    "reviewed": True,
                },
                "isolated_builder": isolated_builder,
                "isolated_builder_cleanup": {
                    "buildx_rm": "completed", "container_absent": True,
                    "state_volume_absent": True,
                    "cache_cleanup": "state-volume-removed",
                    "private_builder_metadata_absent": True,
                    "buildkit_image_retained": True,
                },
                "docker_socket_before": socket,
                "docker_socket_after": copy.deepcopy(socket),
                "docker_socket_records_equal": True,
                "apparmor_before": apparmor,
                "apparmor_after": copy.deepcopy(apparmor),
                "apparmor_records_equal": True,
                "docker_namespace_before": namespace,
                "docker_namespace_after": copy.deepcopy(namespace),
                "docker_namespace_records_equal": True,
            }

        def environment_review(
            provenance: dict[str, dict], *, ceremony: str,
        ) -> dict:
            output_directory = f"/var/lib/hepta/rootful-reviews/{ceremony}"
            review_authority = VERIFIER.ENVIRONMENT_REVIEW_AUTHORITY
            reviewer_id = "independent-rootful-reviewer-a"

            def file_record(path: str, marker: bytes, mode: str) -> dict:
                return {
                    "path": path,
                    "file_sha256": VERIFIER.digest_bytes(marker),
                    "mode": mode, "uid": 0, "gid": 0,
                    "identity_sha256": VERIFIER.digest_bytes(
                        b"identity:" + marker),
                }

            verifier_record = {
                **file_record(
                    "/usr/libexec/hepta-rootful-systemd-environment-provenance",
                    source_payloads[
                        "scripts/hepta_rootful_systemd_environment_provenance.py"],
                    "0755"),
                "source_path": (
                    "/opt/hepta/source/scripts/"
                    "hepta_rootful_systemd_environment_provenance.py"),
                "source_file_sha256": VERIFIER.digest_bytes(
                    source_payloads[
                        "scripts/hepta_rootful_systemd_environment_provenance.py"]),
                "source_commit": baseline["git_head"],
            }
            buildx_path = "/usr/libexec/docker/cli-plugins/docker-buildx"
            observations = {
                "base_image": {
                    "image_id": base_image_id,
                    "repo_digest": base_reference,
                    "repo_digests": [base_reference],
                    "labels_sha256": VERIFIER._canonical_object_digest(
                        VERIFIER.ENVIRONMENT_BASE_LABELS),
                    "os": "linux", "architecture": "amd64",
                    "declared_volumes": 0, "onbuild_instructions": 0,
                },
                "isolated_builder": {
                    "image_id": buildkit_image_id,
                    "repo_digest": buildkit_reference,
                    "repo_digests": [buildkit_reference],
                    "config_sha256": provenance["builder"]["config_sha256"],
                    "os": "linux", "architecture": "amd64",
                    "entrypoint": ["/usr/bin/buildkitd"],
                    "buildkit_binary_path": "/usr/bin/buildkitd",
                    "buildkit_binary_sha256":
                        VERIFIER.digest_bytes(b"buildkitd"),
                    "buildkit_version":
                        provenance["builder"]["buildkit_version"],
                    "buildx_path": buildx_path,
                    "buildx_path_sha256": VERIFIER.digest_bytes(
                        buildx_path.encode("utf-8")),
                    "buildx_binary_sha256":
                        provenance["builder"]["buildx_binary_sha256"],
                    "buildx_version": provenance["builder"]["buildx_version"],
                    "docker_server_version":
                        provenance["builder"]["docker_server_version"],
                    "docker_server_api_version":
                        provenance["builder"]["docker_server_api_version"],
                    "docker_server_git_commit":
                        provenance["builder"]["docker_server_git_commit"],
                },
                "apparmor": {
                    "profile": "hepta-systemd-gate", "mode": "enforce",
                    "attach": "hepta-systemd-gate", "learning_count": 0,
                    "policy_source_sha256":
                        provenance["apparmor"]["policy_source_sha256"],
                    "profile_sha256":
                        provenance["apparmor"]["profile_sha256"],
                    "raw_sha256": provenance["apparmor"]["raw_sha256"],
                    "raw_abi": provenance["apparmor"]["raw_abi"],
                    "raw_data_id": "1", "namespace_name": "root",
                    "namespace_level": 0, "namespace_stacked": False,
                    "profile_inventory_sha256":
                        VERIFIER.digest_bytes(b"apparmor-inventory"),
                },
                "docker_namespace": {
                    "docker_daemon_id": "daemon-a",
                    "docker_daemon_pid": 42,
                    "docker_daemon_start_time_ticks": 100,
                    "docker_daemon_exe_sha256":
                        VERIFIER.digest_bytes(b"dockerd"),
                    "host_boot_id": boot_id,
                    "host_namespace_name": "root",
                    "host_namespace_level": 0,
                    "host_namespace_stacked": False,
                    "daemon_namespace_name": "root",
                    "daemon_namespace_level": 0,
                    "daemon_namespace_stacked": False,
                    "daemon_apparmor_current": "unconfined",
                    "self_user_namespace_inode": 4_026_531_837,
                    "daemon_user_namespace_inode": 4_026_531_837,
                },
            }
            trust_bindings = {
                key: {
                    "path": path,
                    "sha256": (
                        verifier_record["file_sha256"] if key == "producer"
                        else VERIFIER.digest_bytes(key.encode("utf-8"))),
                }
                for key, path in VERIFIER.ENVIRONMENT_TRUST_PATHS.items()
            }
            fingerprint_body = {
                "schema": VERIFIER.ENVIRONMENT_FINGERPRINT_SCHEMA,
                "source_commit": baseline["git_head"],
                "verifier_file_sha256": verifier_record["file_sha256"],
                "verifier_source_file_sha256":
                    verifier_record["source_file_sha256"],
                "review_authority": review_authority,
                "reviewer_id": reviewer_id,
                "observations": observations,
                "trust_bindings": trust_bindings,
            }
            environment_fingerprint = {
                **fingerprint_body,
                "body_sha256": VERIFIER.digest_bytes(
                    VERIFIER.canonical_bytes(fingerprint_body)),
            }
            closure = {
                **file_record(
                    output_directory + "/review-closure.v1.json",
                    (ceremony + ":closure").encode(), "0400"),
                "closure_sha256": VERIFIER.digest_bytes(
                    (ceremony + ":closure-body").encode()),
                "review_authority": review_authority,
                "reviewer_id": reviewer_id,
            }
            request = {
                **file_record(
                    output_directory + "/review-request.v1.json",
                    (ceremony + ":request").encode(), "0600"),
                "request_sha256": VERIFIER.digest_bytes(
                    (ceremony + ":request-body").encode()),
                "nonce": ("1" if ceremony != "hard" else "2") * 64,
            }
            authorization = {
                **file_record(
                    output_directory + "/review-authorization.v1.json",
                    (ceremony + ":authorization").encode(), "0600"),
                "signed_payload_sha256": VERIFIER.digest_bytes(
                    (ceremony + ":signed-payload").encode()),
                "signature_sha256": VERIFIER.digest_bytes(
                    (ceremony + ":signature").encode()),
                "review_authority": review_authority,
                "reviewer_id": reviewer_id,
            }
            outputs = {}
            for kind, filename in VERIFIER.REVIEW_OUTPUT_FILENAMES.items():
                outputs[kind] = {
                    **file_record(
                        output_directory + "/" + filename,
                        (ceremony + ":" + kind).encode(), "0400"),
                    "file_sha256": provenance[kind]["document_sha256"],
                    "schema": VERIFIER.REVIEW_OUTPUT_SCHEMAS[kind],
                }
            return {
                "schema":
                    "hepta.rootful-systemd-review-closure-verification.v1",
                "status": "VERIFIED_EXTERNALLY_SIGNED_REVIEW_CLOSURE",
                "verified_at_ms": gate_completed,
                "expires_at_ms": gate_expires,
                "source_commit": baseline["git_head"],
                "base_image_reference": base_reference,
                "buildkit_image_reference": buildkit_reference,
                "output_directory": output_directory,
                "verifier": verifier_record,
                "closure": closure, "request": request,
                "authorization": authorization, "outputs": outputs,
                "invocation": {
                    "argv_sha256": VERIFIER.digest_bytes(
                        (ceremony + ":argv").encode()),
                    "stdout_sha256": VERIFIER.digest_bytes(b"VERIFIED\n"),
                    "returncode": 0, "duration_ms": 10,
                    "exact_success_output": True, "no_shell": True,
                },
                "environment_fingerprint": environment_fingerprint,
                "reopened_after_invocation": True,
                "reopened_at_gate_end": True,
                "paper_authorized": False, "live_authorized": False,
                "mutation_authorized": False,
                "direct_broker_access": False,
                "order_submission_authorized": False,
            }

        dual_inputs = {
            path: source_input(path) for path in VERIFIER.DUAL_GATE_SOURCE_MODES}
        dual_manifest_sha = VERIFIER.digest_bytes(
            VERIFIER.canonical_bytes(dual_inputs)).removeprefix("sha256:")
        dual_run_id = "c" * 32
        dual_faults = {}
        for index, (name, (plane, domain)) in enumerate(
                VERIFIER.DUAL_EXPECTED_FAULTS.items(), 1):
            dual_faults[name] = {
                "plane": plane, "domain_id": domain,
                "before_pid": 100 + index, "after_pid": 200 + index,
                "before_generation": index,
                "after_generation": index + 1,
                "tombstone_generation": index,
                "restart_observed": True, "stale_generation_rejected": True,
            }
        dual_platform = {
            "host_kernel": "6.8", "host_architecture": "x86_64",
            "docker_client": "Docker 29", "docker_server_version": "29.0.0",
            "docker_server_api_version": "1.50", "docker_server_os": "Linux",
            "docker_server_architecture": "amd64",
            "docker_cgroup_driver": "systemd", "docker_cgroup_version": "2",
            "docker_default_runtime": "runc",
            "docker_security_options": ["name=apparmor"],
            "base_image_reference": base_reference,
            "base_image_id": base_image_id, "base_image_os": "linux",
            "base_image_architecture": "amd64", "systemd": "systemd 252",
            "container_boot_id": boot_id, "container_pid1_cgroup": "0::/",
        }
        dual_certification = certification(paper=False, run_id=dual_run_id)
        dual = {
            "schema": "hepta.p1-dual-domain-rootful-gate.v1",
            "run_id": dual_run_id, "decision": "GO", "passed": True,
            "rehearsal_passed": True, "certification_ready": True,
            "certification_blockers": [],
            "scope": "broker-free-p1-dual-domain-rootful-prerequisite-only",
            "started_at_ms": gate_started, "completed_at_ms": gate_completed,
            "expires_at_ms": gate_expires,
            "paper_test_admission_candidate": False,
            "paper_admission_authorized": False,
            "paper_authorized": False, "live_authorized": False,
            "mutation_authorized": False, "direct_broker_access": False,
            "order_submission_authorized": False,
            "duration_ms": gate_completed - gate_started,
            "lineage": {
                "source_commit": baseline["git_head"],
                "expected_source_commit": baseline["git_head"],
                "source_tree_clean": True, "all_inputs_versioned": True,
                "inputs_stable": True, "final_lineage": True,
                "input_manifest_sha256": dual_manifest_sha,
                "runner_sha256": dual_inputs[
                    "scripts/run_hepta_p1_dual_domain_rootful_gate.py"]["sha256"],
            },
            "inputs": dual_inputs,
            "generated_input_sha256": {
                name: bare(name.encode()) for name in (
                    "identities.json", "boundary.json",
                    "watch-codex-a.credential", "watch-openclaw-b.credential",
                    "paper-codex-a.credential", "paper-openclaw-b.credential")},
            "platform": dual_platform,
            "container": {
                "image_id": "sha256:" + "6" * 64,
                "network_mode": "none", "read_only_rootfs": True,
                "private_cgroup_namespace": True, "privileged": False,
                "bind_mounts": 0, "published_ports": 0, "devices": 0,
                "device_requests": 0, "links": 0,
                "tmpfs_allowlist": copy.deepcopy(VERIFIER.DUAL_RUNTIME_TMPFS),
                "capabilities": list(VERIFIER.DUAL_RUNTIME_CAPABILITIES),
                "apparmor_profile": "hepta-systemd-gate",
            },
            "disposable_cleanup": {
                "container_absent": True, "image_tag_absent": True,
                "image_id_absent": True},
            "certification": dual_certification,
            "environment_review_closure": environment_review(
                dual_certification["provenance"], ceremony="dual"),
            "inner": {
                "schema": "hepta.p1-dual-domain-rootful-inner.v1",
                "passed": True, "run_id": dual_run_id,
                "checks": {key: True for key in
                           VERIFIER.DUAL_DOMAIN_EXPECTED_CHECKS},
                "boot": {"boot_id": boot_id, "pid1_cgroup": "0::/",
                         "systemd": "systemd 252"},
                "identities": copy.deepcopy(VERIFIER.DUAL_EXPECTED_IDENTITIES),
                "faults": dual_faults,
                "inventory": {
                    "immutable_file_count": 100,
                    "immutable_file_inventory_sha256": bare(b"dual-files"),
                    "inert_daemon_sha256": bare(b"dual-daemon"),
                    "forbidden_ib_api_payloads": 0,
                    "protected_broker_sockets": 0,
                    "network_interfaces": ["lo"],
                },
                "boundary": copy.deepcopy(VERIFIER.DUAL_EXPECTED_BOUNDARY),
            },
            "boundary": copy.deepcopy(VERIFIER.DUAL_EXPECTED_BOUNDARY),
        }
        self._write("dual_domain_gate_receipt", VERIFIER.seal(dual))

        paper_inputs = {
            path: source_input(path) for path in VERIFIER.PAPER_GATE_SOURCE_MODES}
        paper_manifest_sha = VERIFIER.digest_bytes(
            VERIFIER.canonical_bytes(paper_inputs))
        paper_run_id = "d" * 32
        paper_platform = {
            **dual_platform,
            "nft": "nftables 1.0", "container_kernel": "6.8",
            "container_architecture": "x86_64",
            "container_cgroup": "v2-private",
            "immutable_file_count": "100",
            "immutable_file_inventory_sha256": bare(b"paper-files"),
            "package_count": "10",
            "package_inventory_sha256": bare(b"paper-packages"),
        }
        paper_certification = certification(paper=True, run_id=paper_run_id)
        rootful = {
            "schema": "hepta.paper-domain-rootful-systemd-gate.v2",
            "run_id": paper_run_id, "decision": "GO", "passed": True,
            "rehearsal_passed": True, "certification_ready": True,
            "certification_blockers": [],
            "scope": "broker-free-paper-domain-rootful-prerequisite-only",
            "started_at_ms": gate_started, "completed_at_ms": gate_completed,
            "expires_at_ms": gate_expires,
            "duration_ms": gate_completed - gate_started,
            "paper_test_admission_candidate": False,
            "paper_authorized": False, "live_authorized": False,
            "mutation_authorized": False, "direct_broker_access": False,
            "order_submission_authorized": False,
            "lineage": {
                "source_commit": baseline["git_head"],
                "expected_source_commit": baseline["git_head"],
                "source_tree_clean": True, "all_inputs_versioned": True,
                "inputs_stable": True, "final_lineage": True,
                "input_manifest_sha256": paper_manifest_sha,
                "expected_input_manifest_sha256": paper_manifest_sha,
                "runner_sha256": "sha256:" + paper_inputs[
                    "scripts/run_hepta_paper_domain_rootful_systemd_gate.py"
                ]["sha256"],
                "expected_runner_sha256": "sha256:" + paper_inputs[
                    "scripts/run_hepta_paper_domain_rootful_systemd_gate.py"
                ]["sha256"],
            },
            "inputs": paper_inputs,
            "generated_input_sha256": {"fixture": bare(b"fixture")},
            "platform": paper_platform,
            "container": {
                "image_id": "sha256:" + "7" * 64,
                "network_mode": "none", "read_only_rootfs": True,
                "private_cgroup_namespace": True, "privileged": False,
                "bind_mounts": 0, "published_ports": 0, "devices": 0,
                "device_requests": 0, "links": 0,
                "tmpfs_allowlist": copy.deepcopy(VERIFIER.PAPER_RUNTIME_TMPFS),
                "apparmor_profile": "hepta-systemd-gate",
                "capabilities": list(VERIFIER.PAPER_RUNTIME_CAPABILITIES),
            },
            "disposable_cleanup": {
                "container_absent": True, "image_tag_absent": True,
                "image_id_absent": True},
            "certification": paper_certification,
            "environment_review_closure": environment_review(
                paper_certification["provenance"], ceremony="paper"),
            "inner": {
                "schema": "hepta.paper-domain-rootful-systemd-inner.v2",
                "passed": True, "run_id": paper_run_id,
                "checks": {key: True for key in
                           VERIFIER.ROOTFUL_EXPECTED_CHECKS},
                "versions": {
                    "systemd": "systemd 252", "nft": "nftables 1.0",
                    "kernel": "6.8", "architecture": "x86_64",
                    "cgroup": "v2-private", "immutable_file_count": "100",
                    "immutable_file_inventory_sha256": bare(b"paper-files"),
                    "package_count": "10",
                    "package_inventory_sha256": bare(b"paper-packages"),
                },
                "boot": {"boot_id": boot_id, "pid1_cgroup": "0::/"},
                "boundary": copy.deepcopy(
                    VERIFIER.PAPER_EXPECTED_INNER_BOUNDARY),
            },
            "boundary": copy.deepcopy(VERIFIER.PAPER_EXPECTED_OUTER_BOUNDARY),
        }
        self._write("rootful_gate_receipt", VERIFIER.seal(rootful))

        network_boundary = {
            "network_only": True, "inert_loopback_sentinels": True,
            "loopback_families": ["ipv4", "ipv6"],
            "real_broker_connections": 0, "broker_protocol_messages": 0,
            "ib_binaries": 0, "paper_units": 0, "credentials": 0,
            "default_engaged_kill_switch_fixtures": 2, "paper_orders": 0,
            "live_authorized": False,
        }
        network = {
            "schema": "hepta.broker-network-rootful-gate.v3",
            "passed": True, "run_id": "network-run",
            "base_image": "hepta/base@sha256:" + "1" * 64,
            "image_id": "sha256:" + "2" * 64, "container_id": "3" * 64,
            "staged_inputs": {
                path: source_input(path)
                for path in VERIFIER.NETWORK_GATE_SOURCE_MODES},
            "inner": {
                "schema": "hepta.broker-network-opt-in-rootful.v3",
                "passed": True,
                "checks": {key: True for key in
                           VERIFIER.NETWORK_EXPECTED_CHECKS},
                "identities": copy.deepcopy(
                    VERIFIER.NETWORK_EXPECTED_IDENTITIES),
                "boundary": network_boundary,
            },
            "actual_rootful_container_run": True,
            "host_policy_applied": False, "host_services_started": False,
            "real_broker_connections": 0, "paper_orders": 0,
            "live_authorized": False,
        }
        self._write("network_gate_receipt", network)

        hard_run_id = "e" * 32
        parent_identity = {
            "st_dev": 1, "st_ino": 2, "st_uid": 0, "st_gid": 0,
            "st_mode": 0o40700, "st_nlink": 2,
        }
        hard_provenance = {}
        for index, kind in enumerate(("host", "source", "base", "tooling"), 1):
            hard_provenance[kind] = {
                "path": f"/var/lib/hepta/hard/{kind}.json",
                "file_sha256": bare(f"hard-file-{kind}".encode()),
                "body_sha256": VERIFIER.digest_bytes(
                    f"hard-body-{kind}".encode()),
                "size": 100, "issued_at_ms": self.now_ms - 100_000,
                "expires_at_ms": gate_expires,
                "device": 1, "inode": 300 + index, "mode": "0600",
                "nlink": 1, "uid": 0, "gid": 0,
                "mtime_ns": 1000 + index, "ctime_ns": 2000 + index,
                "parent_identity": copy.deepcopy(parent_identity),
            }
        hard_checks = {
            key: True for key in VERIFIER.HARD_NETWORK_EXPECTED_CHECKS}
        hard_review_provenance = certification(
            paper=False, run_id=hard_run_id)["provenance"]
        hard = {
            "schema": "hepta.broker-network-hard-isolation-gate.v1",
            "run_id": hard_run_id, "decision": "GO", "passed": True,
            "certification_ready": True, "rehearsal_passed": True,
            "execution_mode": "NATIVE_PRODUCTION",
            "scope": "DEDICATED_BROKER_NETNS_HARD_CERTIFICATION_GATE",
            "started_at_ms": gate_started, "completed_at_ms": gate_completed,
            "expires_at_ms": gate_expires,
            "duration_ms": gate_completed - gate_started,
            "lineage": {
                "host_id": "disposable-vm-a", "boot_id": boot_id,
                "source_commit": baseline["git_head"],
                "source_manifest_sha256": source_sha.removeprefix("sha256:"),
                "runner_sha256": bare(b"h"),
            },
            "provenance": hard_provenance,
            "environment": {
                "boot_id": boot_id, "cgroup_filesystem": "cgroup2",
                "source_commit": baseline["git_head"],
                "virtualization": "kvm",
                "source_manifest_sha256": source_sha.removeprefix("sha256:"),
                "initial_listener_inventory_sha256": bare(b"listeners"),
                "initial_netns_inventory_sha256": bare(b"netns"),
                "initial_firewall_semantic_sha256": bare(b"firewall"),
            },
            "topology": VERIFIER._hard_network_topology(hard_run_id),
            "phases": [
                {"sequence": index, "name": name, "detail": {},
                 "kill_switch_state": state}
                for index, (name, state) in enumerate((
                    ("preflight", "not-created"), ("setup", "engaged"),
                    ("fault-and-revocation-drills", "engaged"),
                    ("final-deny-all", "engaged"),
                    ("cleanup", "engaged-finally-then-removed")), 1)
            ],
            "checks": hard_checks,
            "exposure": {
                "host_listener_allowlist_count": 0,
                "reachable_forwarders": 0, "ib_binaries": 0,
                "broker_credentials": 0, "broker_protocol_messages": 0,
                "orders": 0, "command_transcript_sha256": bare(b"commands"),
                "command_count": 100,
                "kill_switch": {
                    "state": "engaged", "sha256": bare(b"kill-switch"),
                    "device": 1, "inode": 400, "mode": "0400",
                    "parent_identity": copy.deepcopy(parent_identity),
                },
            },
            "cleanup": {
                "attempted": True, "complete": True,
                "firewall_reload_attempted": True,
                "firewall_restored": True, "residue": [],
            },
            "environment_review_closure": environment_review(
                hard_review_provenance, ceremony="hard"),
            "boundary": {
                "native_disposable_host": True,
                "dedicated_network_namespaces": 5,
                "dedicated_cgroup_v2_slices": 5,
                "protected_ports": [4001, 4002, 7496, 7497],
                "inert_ipv4_sentinel_listeners": 16,
                "inert_ipv6_sentinel_listeners": 16,
                "controlled_positive_target": "inert-sentinel-only",
                "kill_switch_state": "engaged",
                "host_firewall_flush_reload_drill": True,
                "forwarder_inventory": "exact-zero-or-reviewed-allowlist",
                "real_ib_binaries": 0, "real_broker_credentials": 0,
                "broker_protocol_messages": 0, "orders": 0,
                "paper_authorized": False, "live_authorized": False,
                "mutation_authorized": False, "direct_broker_access": False,
            },
            "failure": None,
            "paper_test_admission_authorized": False,
            "paper_authorized": False, "live_authorized": False,
            "mutation_authorized": False, "direct_broker_access": False,
            "order_submission_authorized": False,
        }
        self._write("hard_network_gate_receipt", VERIFIER.seal(hard))

        variant_fields = {
            "vm_type", "kernel_release", "vm_image_manifest_sha256",
            "provisioning_manifest_sha256", "machine_id_sha256",
            "boot_id_sha256", "run_id_sha256",
            "instance_uuid", "instance_challenge_sha256",
            "instance_provisioner_id", "instance_hypervisor_id",
            "instance_receipt_file_sha256", "instance_receipt_body_sha256",
            "instance_receipt_issued_at_ms", "instance_receipt_expires_at_ms",
            "agent_os_installation_manifest_sha256",
            "agent_os_runtime_input_manifest_sha256",
            "agent_os_runtime_input_records_sha256",
            "agent_os_runtime_result_sha256",
            "agent_os_runtime_lifecycle_sha256",
            "agent_os_runtime_watch_generation",
            "agent_os_runtime_preflight_executed",
            "agent_os_watch_session_revoked",
            "agent_os_runtime_cleanup_complete", "executed_kind",
            "executed_ib_path_sha256",
        }
        variants = {}
        for index, variant in enumerate(("real", "sandbox", "stub"), 1):
            record = {field: bare((variant + field).encode())
                      for field in variant_fields if field.endswith("sha256")}
            record.update({
                "vm_type": "kvm", "kernel_release": "6.8.0",
                "instance_uuid":
                    f"00000000-0000-4000-8000-{index:012x}",
                "instance_provisioner_id": "test-provisioner-a",
                "instance_hypervisor_id": f"test-hypervisor-{index}",
                "instance_receipt_issued_at_ms": self.now_ms - 60_000,
                "instance_receipt_expires_at_ms": self.now_ms + 60_000,
                "agent_os_runtime_watch_generation": 1,
                "agent_os_runtime_preflight_executed": True,
                "agent_os_watch_session_revoked": True,
                "agent_os_runtime_cleanup_complete": True,
                "executed_kind": variant + "-kind",
            })
            variants[variant] = record
        common_digest_fields = {
            "platform_policy_sha256", "clean_source_bundle_sha256",
            "clean_source_manifest_sha256", "clean_source_files_sha256",
            "simulator_sha256", "client_probe_sha256", "formal_ibapi_sha256",
            "agent_os_installation_manifest_sha256", "agent_os_gateway_sha256",
            "agent_os_sessionctl_sha256", "agent_os_mcp_server_sha256",
            "agent_os_runtime_input_manifest_sha256",
            "agent_os_runtime_input_content_sha256",
            "agent_os_runtime_inner_gate_sha256",
        }
        common = {field: bare(field.encode()) for field in common_digest_fields}
        common["clean_source_files_sha256"] = strict_source_files_sha
        common["simulator_sha256"] = self.agent_binary_records[
            "hepta-executiond"]["sha256"].removeprefix("sha256:")
        common["agent_os_gateway_sha256"] = self.agent_binary_records[
            "hepta-tool-gatewayd"]["sha256"].removeprefix("sha256:")
        common["agent_os_sessionctl_sha256"] = self.agent_binary_records[
            "hepta-sessionctl"]["sha256"].removeprefix("sha256:")
        common.update({
            "agent_os_installation_file_count": 73,
            "agent_os_runtime_input_file_count": 10,
            "agent_os_fixed_identities": {"agent_uid": 2004},
            "agent_os_watch_tools": ["system.get_health"],
            "agent_os_read_probes": ["system.get_health"],
            "all_agent_os_runtime_preflights_executed": True,
            "all_agent_os_watch_sessions_revoked": True,
            "all_agent_os_runtime_cleanup_complete": True,
            "distinct_native_vms": 3,
            "distinct_provisioner_attested_instances": 3,
            "external_instance_receipts_verified": True,
            "instance_receipt_validity_windows_overlap": True,
            "all_networks_loopback_only": True,
            "all_inputs_stable": True,
        })
        native = {
            "schema": "hepta.execution-native-systemd-aggregate.v6",
            "passed": True,
            "certification_level":
                "native-disposable-vm-agent-os-watch-runtime-rootful-systemd",
            "variants": variants, "common_closure": common,
            "aggregation_inputs": [
                {
                    "variant": variant,
                    "path": str(self.root /
                                f"execution-native-systemd-{variant}.json"),
                    "sha256": bare((variant + "-report").encode()),
                    "size": 100, "mode": "0600",
                }
                for variant in ("real", "sandbox", "stub")
            ],
            "boundary": {
                "real_ibapi_elf_executed": False,
                "real_broker_connections": 0, "paper_orders": 0,
                "live_enabled": False, "paper_authorized": False,
                "native_agent_os_installation_gate_satisfied": True,
                "native_agent_os_runtime_gate_satisfied": True,
                "agent_os_runtime_preflight_executed": True,
                "agent_os_runtime_preflight_required": True,
                "agent_os_runtime_evidence_fabricated": False,
                "agent_os_runtime_source":
                    "three-distinct-externally-attested-native-vms",
                "ib_adapter_visible_during_agent_os_runtime": False,
                "paper_certification": "requires_separate_explicit_authorization",
            },
        }
        self._write("native_gate_receipt", native)

        agent_run_id = "a" * 32
        agent_certification = certification(
            paper=False, run_id=agent_run_id)
        agent_review = environment_review(
            agent_certification["provenance"], ceremony="agent-os")
        agent_observations = agent_review[
            "environment_fingerprint"]["observations"]

        def agent_reviewed_record(kind: str) -> dict:
            provenance = agent_certification["provenance"][kind]
            return {
                field: provenance[field]
                for field in (
                    VERIFIER.REVIEWED_PROVENANCE_FIELDS[kind] |
                    {"document_sha256"})
            }

        agent_build_path = "build/agent-os-release"
        agent_inputs = [
            {
                "path": path, **source_input(path),
                "device": 1, "inode": 700 + index,
            }
            for index, path in enumerate(
                sorted(VERIFIER.AGENT_OS_SOURCE_MODES))
        ]
        for index, binary in enumerate(
                sorted(VERIFIER.AGENT_OS_BUILD_BINARIES), 1):
            runtime_record = self.agent_binary_records[binary]
            agent_inputs.append({
                "path": f"{agent_build_path}/bin/{binary}",
                "sha256": runtime_record["sha256"].removeprefix("sha256:"),
                "size": runtime_record["size"], "mode": "0755", "device": 2,
                "inode": 900 + index,
            })
        agent_inputs.sort(key=lambda item: item["path"])
        phase_fields = (
            "gateway_pid", "simulator_pid", "tool_socket_inode",
            "supervisor_socket_inode", "execution_socket_inode",
            "events_socket_inode")
        agent_initial = {
            field: 1000 + index for index, field in enumerate(phase_fields)}
        agent_services = copy.deepcopy(agent_initial)
        agent_services.update({"gateway_pid": 1100, "simulator_pid": 1101})
        agent_sockets = {
            field: 1200 + index for index, field in enumerate(phase_fields)}
        domain_fields = (
            "watch_generation", "gateway_pid", "simulator_pid",
            "custodian_pid", "reader_owner_pid",
            "custodian_crash_generation", "custodian_restart_count",
            "closure_receipt_count", "tool_socket_inode",
            "supervisor_socket_inode", "execution_socket_inode",
            "events_socket_inode")
        agent_domains = {
            domain: {
                field: offset + index + 1
                for index, field in enumerate(domain_fields)
            }
            for domain, offset in (("codex-a", 2000), ("openclaw-b", 3000))
        }
        agent_inner = {
            "schema": "hepta.agent-os-rootful-systemd-e2e-inner.v2",
            "profile": "two-domain-agent-gateway-execution-watch",
            "passed": True,
            "identities": copy.deepcopy(VERIFIER.AGENT_OS_INNER_IDENTITIES),
            "checks": {
                key: True for key in VERIFIER.AGENT_OS_INNER_CHECKS},
            "lifecycle": {
                "watch_generation": 1, "initial": agent_initial,
                "service_reactivation": agent_services,
                "socket_reactivation": agent_sockets,
                "trust_domains": agent_domains,
            },
            "boundary": copy.deepcopy(VERIFIER.AGENT_OS_INNER_BOUNDARY),
        }
        base_observation = agent_observations["base_image"]
        builder_observation = agent_observations["isolated_builder"]
        apparmor_observation = agent_observations["apparmor"]
        namespace_observation = agent_observations["docker_namespace"]
        agent_base = {
            "reference": base_observation["repo_digest"],
            "id": base_observation["image_id"],
            "repo_digests": base_observation["repo_digests"],
            "os": "linux", "architecture": base_observation["architecture"],
            "declared_volumes": 0, "base_class": "reviewed-offline-ready",
            "production_approved": True,
            "production_status": "external-reviewed-go",
            "reviewed_provenance": agent_reviewed_record("base"),
        }
        agent_buildkit = {
            "reference": builder_observation["repo_digest"],
            "id": builder_observation["image_id"],
            "bare_id": builder_observation["image_id"].removeprefix(
                "sha256:"),
            "repo_digests": builder_observation["repo_digests"],
            "config_sha256": builder_observation["config_sha256"],
            "config_labels": {},
            "entrypoint": builder_observation["entrypoint"],
            "production_status": "external-reviewed-go",
            "production_approved": True,
        }
        agent_builder_name = f"hepta-isolated-{agent_run_id}"
        agent_builder_node = agent_builder_name + "0"
        agent_builder_container = "buildx_buildkit_" + agent_builder_node
        agent_builder_volume = agent_builder_container + "_state"
        agent_builder_names = {
            "builder": agent_builder_name, "node": agent_builder_node,
            "container": agent_builder_container,
            "volume": agent_builder_volume,
        }
        agent_builder_common_labels = {
            "io.hepta.purpose": "agent-os-rootful-systemd-e2e-gate",
            "io.hepta.run-id": agent_run_id,
            "io.hepta.buildkit-image-id": buildkit_image_id,
            "io.hepta.buildx-builder": agent_builder_name,
        }
        agent_builder_container_id = "5" * 64
        agent_builder_container_common = {
            "container_id": agent_builder_container_id,
            "name": agent_builder_container, "image_id": buildkit_image_id,
            "builder": agent_builder_name, "node": agent_builder_node,
            "state_volume": agent_builder_volume, "network_mode": "none",
            "privileged": True, "bind_mounts": 0, "published_ports": 0,
            "labels": {
                **agent_builder_common_labels,
                "io.hepta.role": "isolated-buildkit-daemon",
            },
        }
        agent_builder_objects = {
            "names": agent_builder_names,
            "container_id": agent_builder_container_id,
            "volume": {
                "name": agent_builder_volume, "driver": "local",
                "scope": "local",
                "labels": {
                    **agent_builder_common_labels,
                    "io.hepta.role": "isolated-buildkit-state",
                },
                "mountpoint_sha256": VERIFIER.digest_bytes(
                    b"agent-builder-mountpoint"),
            },
            "container_before_start": {
                **agent_builder_container_common, "running": False},
            "container_running": {
                **agent_builder_container_common, "running": True},
            "runtime": {
                "builder": agent_builder_name, "node": agent_builder_node,
                "driver": "docker-container", "status": "running",
                "buildkit_version":
                    agent_certification["provenance"]["builder"][
                        "buildkit_version"],
            },
        }
        agent_builder = {
            "mode": "reviewed-isolated-buildx", "isolated": True,
            "cache_reuse": "disabled",
            "builder_cache_cleanup": "state-volume-removed",
            "preloaded_image_only": True,
            "reviewed_provenance": agent_reviewed_record("builder"),
            "production_eligible": True,
            "toolchain": {
                "buildx_path": builder_observation["buildx_path"],
                "buildx_version": builder_observation["buildx_version"],
                "buildx_binary_sha256":
                    builder_observation["buildx_binary_sha256"],
                "docker_server_version":
                    builder_observation["docker_server_version"],
                "docker_server_api_version":
                    builder_observation["docker_server_api_version"],
                "docker_server_git_commit":
                    builder_observation["docker_server_git_commit"],
                "reviewed": True,
            },
            "buildkit_image": agent_buildkit,
            "objects": agent_builder_objects,
            "builder_cache_before_cleanup": {
                "record_count": 1,
                "inventory_sha256": VERIFIER.digest_bytes(
                    b"agent-builder-cache")},
            "builder_stopped": {
                **agent_builder_container_common, "running": False},
            "cleanup": {
                "buildx_rm": "completed", "container_absent": True,
                "state_volume_absent": True,
                "private_builder_metadata_absent": True,
                "exact_container_fallback": False,
                "exact_volume_fallback": False,
                "cache_cleanup": "state-volume-removed",
            },
            "cleanup_complete": True,
        }
        agent_apparmor = {
            "profile": apparmor_observation["profile"],
            "mode": "enforce", "attach": apparmor_observation["attach"],
            "learning_count": 0,
            "profile_sha256": apparmor_observation["profile_sha256"],
            "raw_sha256": apparmor_observation["raw_sha256"],
            "raw_abi": apparmor_observation["raw_abi"],
            "raw_data_id": apparmor_observation["raw_data_id"],
            "raw_data_size": 100,
            "policy_entry": "hepta-systemd-gate.1",
            "profile_inventory_count": 1,
            "profile_inventory_sha256":
                apparmor_observation["profile_inventory_sha256"],
            "policy_content_attested": True,
            "reviewed_provenance": agent_reviewed_record("apparmor"),
            "kernel_anchor": {
                "namespace": {
                    "name": apparmor_observation["namespace_name"],
                    "level": apparmor_observation["namespace_level"],
                    "stacked": apparmor_observation["namespace_stacked"],
                    "field_metadata_sha256":
                        VERIFIER.digest_bytes(b"aa-field-metadata"),
                }},
            "kernel_aafs_attested": True,
        }
        agent_namespace = {
            "docker_daemon_id": namespace_observation["docker_daemon_id"],
            "docker_daemon_pid": namespace_observation["docker_daemon_pid"],
            "docker_daemon_start_time_ticks":
                namespace_observation["docker_daemon_start_time_ticks"],
            "docker_daemon_comm": "dockerd",
            "docker_daemon_process_inode": 900,
            "docker_daemon_process_metadata_sha256":
                VERIFIER.digest_bytes(b"dockerd-process-metadata"),
            "host_boot_id": namespace_observation["host_boot_id"],
            "host_namespace": {
                "name": namespace_observation["host_namespace_name"],
                "level": namespace_observation["host_namespace_level"],
                "stacked": namespace_observation["host_namespace_stacked"],
            },
            "daemon_namespace": {
                "name": namespace_observation["daemon_namespace_name"],
                "level": namespace_observation["daemon_namespace_level"],
                "stacked":
                    namespace_observation["daemon_namespace_stacked"],
            },
            "same_apparmor_namespace_attested": True,
            "reviewed_provenance":
                agent_reviewed_record("docker_namespace"),
        }
        agent_image_id = "sha256:" + "8" * 64
        agent_rootfs_sha = "sha256:" + bare(b"base-rootfs")
        agent_labels = {
            "io.hepta.purpose": "agent-os-rootful-systemd-e2e-gate",
            "io.hepta.role": "offline-rootful-systemd-runtime",
            "io.hepta.run-id": agent_run_id,
            "io.hepta.base-image-id": base_image_id,
            "io.hepta.base-rootfs-sha256": agent_rootfs_sha,
            "io.hepta.base-construction": "docker-export-scratch-add-v1",
        }
        agent_os = {
            "schema": "hepta.agent-os-rootful-systemd-e2e-gate.v1",
            "passed": True, "decision": "GO", "certification_ready": True,
            "certification_blockers": [],
            "certification_level":
                "externally-reviewed-rootful-systemd-certification",
            "production_eligible": True,
            "environment_review_closure": agent_review,
            "duration_ms": 1000,
            "build": {
                "path": agent_build_path,
                "cmake_cache_sha256": bare(b"agent-cmake-cache"),
                "compile_commands_sha256": bare(b"agent-compile-commands"),
                "build_type": "Release", "ibapi_enabled": False,
                "legacy_bridge_enabled": False,
            },
            "builder": agent_builder, "base_image": agent_base,
            "docker_host": {
                "socket_owner_root": True, "socket_world_writable": False,
                "client": "Docker version 29.0.0"},
            "apparmor": agent_apparmor,
            "docker_apparmor_namespace": agent_namespace,
            "image": {
                "id": agent_image_id,
                "purpose": "agent-os-rootful-systemd-e2e-gate",
                "role": "offline-rootful-systemd-runtime",
                "run_id": agent_run_id, "build_network": "none",
                "cache_reuse": "disabled",
                "builder_cache_cleanup": "state-volume-removed",
                "source_image_id": base_image_id,
                "base_rootfs_sha256": agent_rootfs_sha,
                "base_rootfs_size": 100,
                "base_construction_version":
                    "docker-export-scratch-add-v1",
                "labels": agent_labels,
                "repo_tags": [f"hepta/agent-os-rootful-e2e:{agent_run_id}"],
                "repo_digests": [],
            },
            "base_holder": {
                "container_id": "6" * 64,
                "name": f"hepta-agent-os-base-rootfs-{agent_run_id}",
                "image_id": base_image_id,
                "purpose": "agent-os-rootful-systemd-e2e-gate",
                "role": "base-rootfs-snapshot-holder",
                "run_id": agent_run_id, "network_mode": "none",
                "read_only_rootfs": True, "mounts": 0, "volumes": 0,
            },
            "container": {
                "container_id": "7" * 64, "image_id": agent_image_id,
                "network_mode": "none", "read_only_rootfs": True,
                "bind_mounts": 0, "published_ports": 0,
                "privileged": False,
                "apparmor_profile": "hepta-systemd-gate",
                "private_cgroup_namespace": True,
            },
            "inner": agent_inner, "inputs": agent_inputs,
            "input_stability": True,
            "owned_docker_objects_cleanup_complete": True,
            "owned_docker_objects_cleanup": {
                "runtime_container": {"absent": True},
                "built_image": {
                    "tag_absent": True, "exact_image_id_absent": True},
                "base_holder": {"absent": True},
            },
            "boundary": copy.deepcopy(VERIFIER.AGENT_OS_BOUNDARY),
            "apparmor_post_cleanup": copy.deepcopy(agent_apparmor),
            "apparmor_revalidated": True,
            "apparmor_records_equal": True,
            "docker_apparmor_namespace_post_cleanup":
                copy.deepcopy(agent_namespace),
            "docker_apparmor_namespace_revalidated": True,
            "docker_apparmor_namespace_records_equal": True,
            "completed_checks": sorted({
                "isolated_builder_contract", "local_inputs",
                "apparmor_policy_attested",
                "docker_apparmor_namespace_attested",
                "buildx_toolchain_attested", "pinned_local_buildkit",
                "pinned_local_base", "local_base_rootfs_snapshot",
                "isolated_builder_started", "isolated_builder_stopped",
                "offline_image_build", "container_isolation",
                "systemd_pid1", "four_uid_watch_runtime",
                "isolated_builder_cache_removed", "apparmor_revalidated",
                "docker_apparmor_namespace_revalidated",
                "environment_review_closure_reopened",
            }),
        }
        self._write("agent_os_rootful_gate_receipt", agent_os)

        liveness_run_id = "b" * 32
        liveness_inputs = {
            path: source_input(path)
            for path in VERIFIER.P1_LIVENESS_SOURCE_MODES}
        liveness_runner = (
            "scripts/run_hepta_p1_campaign_rootful_liveness_gate.py")
        liveness_certification = certification(
            paper=False, run_id=liveness_run_id)
        liveness = self._body(VERIFIER.P1_LIVENESS_GATE_FIELDS)
        liveness.update({
            "schema":
                "hepta.p1-safety-soak-campaign-rootful-liveness-gate.v1",
            "run_id": liveness_run_id, "decision": "GO", "passed": True,
            "rehearsal_passed": True, "certification_ready": True,
            "certification_blockers": [],
            "scope":
                "p1-campaign-coordinator-rootful-liveness-prerequisite-only",
            "started_at_ms": gate_started, "completed_at_ms": gate_completed,
            "expires_at_ms": gate_expires,
            "producer": {
                "path": "/usr/libexec/hepta-p1-campaign-rootful-liveness-gate",
                "file_sha256": "sha256:" +
                    liveness_inputs[liveness_runner]["sha256"],
            },
            "production_mode": "PRODUCTION_REVIEWED_ROOTFUL_CERTIFICATION",
            "paper_test_admission_candidate": False,
            "paper_admission_authorized": False,
            "paper_authorized": False, "live_authorized": False,
            "mutation_authorized": False, "direct_broker_access": False,
            "order_submission_authorized": False,
            "duration_ms": gate_completed - gate_started,
            "lineage": {
                "source_commit": baseline["git_head"],
                "expected_source_commit": baseline["git_head"],
                "source_tree_clean": True, "all_inputs_versioned": True,
                "inputs_stable": True, "final_lineage": True,
                "input_manifest_sha256": VERIFIER.digest_bytes(
                    VERIFIER.canonical_bytes(liveness_inputs)),
                "runner_sha256": "sha256:" +
                    liveness_inputs[liveness_runner]["sha256"],
            },
            "inputs": liveness_inputs, "generated_input_sha256": {},
            "platform": copy.deepcopy(dual_platform),
            "container": {
                "image_id": "sha256:" + "9" * 64,
                "network_mode": "none", "read_only_rootfs": True,
                "private_cgroup_namespace": True, "privileged": False,
                "bind_mounts": 0, "published_ports": 0, "devices": 0,
                "device_requests": 0, "links": 0,
                "tmpfs_allowlist": copy.deepcopy(VERIFIER.DUAL_RUNTIME_TMPFS),
                "capabilities": list(VERIFIER.DUAL_RUNTIME_CAPABILITIES),
                "apparmor_profile": "hepta-systemd-gate",
            },
            "disposable_cleanup": {
                "container_absent": True, "image_tag_absent": True,
                "image_id_absent": True},
            "certification": liveness_certification,
            "environment_review_closure": environment_review(
                liveness_certification["provenance"], ceremony="liveness"),
            "inner": {
                "schema":
                    "hepta.p1-safety-soak-campaign-rootful-liveness-inner.v1",
                "passed": True, "run_id": liveness_run_id,
                "checks": {key: True for key in
                           VERIFIER.P1_LIVENESS_EXPECTED_CHECKS},
                "inner_executable": {
                    "path": "/usr/libexec/hepta-p1-liveness-inner-gate",
                    "file_sha256": VERIFIER.digest_bytes(b"liveness-inner"),
                    "mode": "0755", "uid": 0, "gid": 0,
                },
                "boot": {
                    "boot_id": boot_id, "pid1": 1, "pid1_comm": "systemd",
                    "pid1_cgroup": "0::/", "systemd": "systemd 252",
                },
                "production_unit_inputs": {"verified": True},
                "watchdog": {
                    "first": {"state": "healthy"},
                    "recovered": {"state": "healthy"},
                    "first_pid": 100, "recovered_pid": 101,
                    "first_invocation_id": "watchdog-a",
                    "recovered_invocation_id": "watchdog-b",
                    "n_restarts": 1, "effective_watchdog_usec": "2s",
                },
                "durable_failure": {
                    "worker_status": "FAILED_CLOSED",
                    "coordinator_status": "FAILED_CLOSED",
                    "catch_up": False,
                    "post_restart_journal_entry_count": 1,
                    "terminal_observation_acknowledged": True,
                    "worker_n_restarts": 1,
                },
                "effective_units_before_fault": {"verified": True},
                "effective_units_after_fault": {"verified": True},
                "cleanup": {
                    "target": "hepta-p1-campaign-rootful-liveness.target",
                    "units": [], "all_inactive": True,
                    "process_residue_absent": True,
                },
                "boundary": copy.deepcopy(VERIFIER.P1_LIVENESS_BOUNDARY),
            },
            "boundary": copy.deepcopy(VERIFIER.P1_LIVENESS_BOUNDARY),
        })
        self._write("p1_liveness_gate_receipt", VERIFIER.seal(liveness))

        def rfc3339(timestamp_ms: int) -> str:
            return datetime.fromtimestamp(
                timestamp_ms / 1000, tz=timezone.utc
            ).isoformat().replace("+00:00", "Z")

        evaluated_at = self.now_ms - 20_000
        release_expires_at = gate_expires
        source_baseline_binding = {
            "path": "evidence/source-baseline.json",
            "sha256": VERIFIER.digest_bytes(
                self.paths["source_baseline"].read_bytes()).removeprefix(
                    "sha256:"),
            "size": self.paths["source_baseline"].stat().st_size,
            "mode": "0600",
        }
        native_binding = {
            "role": "native-runtime-aggregate",
            "path": "evidence/native-runtime-aggregate.json",
            "sha256": VERIFIER.digest_bytes(
                self.paths["native_gate_receipt"].read_bytes()).removeprefix(
                    "sha256:"),
            "size": self.paths["native_gate_receipt"].stat().st_size,
            "mode": "0600",
        }
        critical_roles = [
            "release-input-manifest", "round-closure",
            "strict-source-bundle", "strict-source-bundle-manifest",
            "agent-os-source-bundle", "agent-os-source-manifest",
            "agent-os-source-policy", "runtime-package",
            "runtime-package-manifest", "test-matrix-report",
            "runner-identity-report", "native-variant-report-real",
            "native-variant-report-sandbox", "native-variant-report-stub",
            "native-instance-receipt-real",
            "native-instance-receipt-sandbox",
            "native-instance-receipt-stub",
        ]
        critical_files = [{
            "role": "source-baseline-manifest", **source_baseline_binding,
        }, native_binding]
        critical_files.extend({
            "role": role, "path": f"evidence/{index:02d}-{role}.json",
            "sha256": bare(("release:" + role).encode()),
            "size": 100 + index, "mode": "0600",
        } for index, role in enumerate(critical_roles, 1))
        while len(critical_files) < 24:
            index = len(critical_files)
            role = f"supporting-evidence-{index:02d}"
            critical_files.append({
                "role": role, "path": f"evidence/{role}.json",
                "sha256": bare(role.encode()), "size": 200 + index,
                "mode": "0600",
            })
        retention_inputs = {
            role: {
                "path": f"/var/lib/hepta/retention/{role}.json",
                "sha256": bare(("retention:" + role).encode()),
                "size": 100, "mode": "0400",
            }
            for role in VERIFIER.RELEASE_RETENTION_INPUTS
        }
        release = {
            "schema": "heptatrader.release-validation-closure.v1",
            "version": 1, "project_id": "heptatrader-agent-os",
            "round": 114, "release_version": "1.0.0-round114",
            "evaluated_at": rfc3339(evaluated_at),
            "expires_at": rfc3339(release_expires_at),
            "decision": "GO", "passed": True,
            "candidate_scope": "paper-testing-admission-candidate-only",
            "local_evidence": {
                "profile": "release-validation-p0-v1", "round": 114,
                "release_version": "1.0.0-round114",
                "artifact_directory":
                    "heptatrader-round114-engineering-artifacts-v1",
                "input_manifest_sha256": bare(b"release-input-manifest"),
                "source_baseline": source_baseline_binding,
                "source_lineage": {
                    "git_head": baseline["git_head"],
                    "strict_source_bundle_sha256": bare(b"strict-bundle"),
                    "strict_source_manifest_sha256": bare(b"strict-manifest"),
                    "strict_source_security_manifest_sha256":
                        source_sha.removeprefix("sha256:"),
                    "strict_source_files_sha256": strict_source_files_sha,
                    "agent_source_bundle_sha256": bare(b"agent-bundle"),
                    "runtime_package_sha256": bare(b"runtime-package"),
                    "runtime_package_manifest_sha256":
                        bare(b"runtime-package-manifest"),
                },
                "verification": {
                    "matrix_generated_at": rfc3339(evaluated_at - 1000),
                    "runner_generated_at": rfc3339(evaluated_at - 500),
                    "fresh_until": rfc3339(release_expires_at),
                    "maximum_age_seconds": 24 * 60 * 60,
                    "lanes": [
                        {
                            "name": name, "build_type": "Release",
                            "build_testing": True,
                            "ibapi_enabled": ibapi, "expected_tests": 116,
                            "observed_tests": 116, "selection": [],
                            "passed": True,
                        }
                        for name, ibapi in (
                            ("repo-ibapi-off", False),
                            ("repo-ibapi-on", True),
                            ("no-git-ibapi-off", False),
                            ("no-git-ibapi-on", True))
                    ],
                },
                "delivery": {
                    "closure_sha256": bare(b"delivery-closure"),
                    "artifact_roles": ["soak-a", "soak-b", "soak-c", "soak-d"],
                    "four_soaks_eight_rounds_verified": True,
                },
                "native": {
                    "schema": "hepta.execution-native-systemd-aggregate.v6",
                    "certification_level":
                        "native-disposable-vm-agent-os-watch-runtime-rootful-systemd",
                    "distinct_native_vms": 3,
                    "distinct_provisioner_attested_instances": 3,
                    "external_instance_receipts_verified": True,
                    "runtime_contract_verified": True,
                },
                "critical_files": critical_files,
                "safety_boundaries": copy.deepcopy(
                    VERIFIER.RELEASE_SAFETY_BOUNDARIES),
            },
            "retention_evidence": {
                "inputs": retention_inputs,
                "evidence_root": "/var/lib/hepta/retention",
                "verification": {
                    "schema":
                        "heptatrader.evidence-ingestion-receipt-verification.v2",
                    "trust_scope": "system-production",
                    "signature_status": "verified",
                    "retention_status": "current-policy-satisfied",
                    "current_policy_satisfied_object_count": len(critical_files),
                    "statement_sha256": bare(b"statement"),
                    "request_sha256": bare(b"request"),
                    "index_sha256": bare(b"index"),
                    "evidence_set_manifest_sha256": bare(b"evidence-set"),
                    "trust_policy_sha256": bare(b"trust-policy"),
                    "evidence_set_id": "round114-evidence-set-a",
                    "profile": "release-validation-p0-v1",
                    "role_count": len(critical_files),
                    "production_contract_verified": True,
                },
            },
            "safety_boundaries": copy.deepcopy(
                VERIFIER.RELEASE_SAFETY_BOUNDARIES),
        }
        self._write("release_validation_receipt", release)

        handoff = self._body(VERIFIER.WATCH_HANDOFF_FIELDS)
        handoff.update({
            "schema": VERIFIER.WATCH_HANDOFF_SCHEMA,
            "version": VERIFIER.WATCH_HANDOFF_VERSION,
            "status": "WATCH_RETIRED_HANDOFF_COMPLETE",
            "issued_at_ms": self.now_ms - 10_000,
            "expires_at_ms": self.now_ms + 3_600_000,
            "round": 114, "domain": self.domain,
            "campaign_id": self.campaign,
            "source_baseline_sha256": source_sha,
            "producer": {
                "path": "/usr/libexec/hepta-p1-watch-to-paper-handoff",
                "file_sha256": VERIFIER.digest_bytes(
                    source_payloads[
                        "scripts/hepta_p1_watch_to_paper_handoff.py"]),
            },
            "production_mode": "PRODUCTION_ROOT_SYSTEMD",
            "activation_receipt": self.reference("activation_receipt"),
            "p1_audit_receipt": self.reference("p1_audit_receipt"),
            "freeze_bundle": copy.deepcopy(audit["freeze_bundle"]),
            "watch_units_inactive": True, "watch_authority_count": 0,
            "watch_socket_count": 0, "watch_timer_count": 0,
            "paper_units_inactive": True, "broker_deny_all": True,
            "kill_switch_engaged": True,
            "global_kill_switch_engaged": True, "identity_count": 0,
            "identity_manifest_sha256":
                VERIFIER.DISABLED_IDENTITY_MANIFEST_SHA256,
            "paper_profile_restored": True,
            "paper_profile_restoration":
                copy.deepcopy(self.handoff_profile_restoration),
            "profile_candidate_absent": True,
            "paper_runtime_profile_hardened": True,
            "paper_runtime_profile_hardening": copy.deepcopy(
                self.handoff_runtime_profile_hardening),
            "paper_runtime_profile_candidate_absent": True,
            "crash_recovery_verified": True,
            "cleanup_residue_count": 0,
            "paper_authorized": False, "live_authorized": False,
            "mutation_authorized": False, "direct_broker_access": False,
            "order_submission_authorized": False,
        })
        self._write("watch_handoff_receipt", VERIFIER.seal(handoff))

        exposure = self._body(VERIFIER.ZERO_EXPOSURE_FIELDS)
        reservation_id = "zero-exposure-" + "1" * 48
        reservation_boot_id = "11111111-2222-4333-8444-555555555555"
        host_authority_lease = {
            "directory_path": "/run/hepta/ib-paper-host-authority",
            "lease_path": "/run/hepta/ib-paper-host-authority/lease.lock",
            "owner_path": "/run/hepta/ib-paper-host-authority/owner.v1",
            "directory_device": 1, "directory_inode": 10,
            "directory_uid": 0, "directory_gid": 0,
            "directory_mode": 0o700,
            "lease_device": 1, "lease_inode": 11,
            "lease_uid": 0, "lease_gid": 0, "lease_mode": 0o600,
            "lease_size": 0, "held_exclusive": True,
            "boot_id": reservation_boot_id,
        }
        executable = lambda path, marker: {
            "path": path, "file_sha256": VERIFIER.digest_bytes(marker)}
        exposure.update({
            "schema": "hepta.p1-paper-deny-all-zero-exposure-receipt.v1",
            "version": 1, "status": "PASS",
            "observed_at_ms": self.now_ms - 1_000,
            "expires_at_ms": self.now_ms + 60_000,
            "round": 114, "domain": self.domain,
            "campaign_id": self.campaign,
            "source_baseline_sha256": source_sha,
            "producer": executable(
                "/usr/libexec/hepta-p1-paper-zero-exposure-attestor",
                source_payloads[
                    "scripts/hepta_p1_paper_zero_exposure_attestor.py"]),
            "production_mode":
                "PRODUCTION_ROOT_OFFLINE_SIGNED_ACCOUNT_ATTESTOR",
            "snapshot_producer": executable(
                "/usr/libexec/hepta-p1-paper-zero-exposure-snapshot-producer",
                source_payloads[
                    "scripts/hepta_p1_paper_zero_exposure_snapshot_producer.py"]),
            "snapshot_production_mode":
                VERIFIER.ZERO_SNAPSHOT_PRODUCTION_MODE,
            "intent_id": "zero-exposure-production-intent-a",
            "operator_intent_reference": {
                "path": str(self.root / "operator-intent.json"),
                "file_sha256": VERIFIER.digest_bytes(b"intent-file"),
                "body_sha256": VERIFIER.digest_bytes(b"intent-body"),
            },
            "watch_handoff_receipt": self.reference("watch_handoff_receipt"),
            "challenge_reference": {
                "path": str(self.root / "challenge.json"),
                "file_sha256": VERIFIER.digest_bytes(b"challenge-file"),
                "body_sha256": VERIFIER.digest_bytes(b"challenge-body"),
            },
            "host_authority_reservation": {
                "path": "/run/hepta/ib-paper-host-authority/owner.v1",
                "file_sha256": VERIFIER.digest_bytes(b"reservation-file"),
                "body_sha256": VERIFIER.digest_bytes(b"reservation-body"),
                "device": 1, "inode": 12, "uid": 0, "gid": 0,
                "mode": 0o600, "size": 100, "mtime_ns": 1000,
                "ctime_ns": 1001,
            },
            "reservation_id": reservation_id,
            "reservation_generation": 1,
            "reservation_lifecycle": VERIFIER.RESERVATION_LIFECYCLE,
            "reservation_predecessor_finalization_body_sha256": None,
            "reservation_prior_finalization_pointer_reference": None,
            "reservation_next_consumer": VERIFIER.RESERVATION_NEXT_CONSUMER,
            "reservation_continuity_verified": True,
            "reservation_finalization_tombstone_path": (
                "/run/hepta/ib-paper-host-authority/finalized." +
                reservation_id + ".v1.json"),
            "reservation_finalization_current_pointer_path":
                "/run/hepta/ib-paper-host-authority/"
                "finalization-current.v1.json",
            "reservation_finalization_tombstone_absent": True,
            "reservation_finalization_schema":
                VERIFIER.RESERVATION_FINALIZATION_SCHEMA,
            "reservation_finalization_order":
                VERIFIER.RESERVATION_FINALIZATION_ORDER,
            "reservation_boot_id": reservation_boot_id,
            "reservation_lease_device": 1, "reservation_lease_inode": 11,
            "signed_evidence_reference": {
                "path": str(self.root / "signed-evidence.json"),
                "file_sha256": VERIFIER.digest_bytes(b"signed-file"),
                "signed_payload_sha256":
                    VERIFIER.digest_bytes(b"signed-payload"),
            },
            "broker_boundary_reference": {
                "path": str(self.root / "broker-boundary.json"),
                "file_sha256": VERIFIER.digest_bytes(b"broker-file"),
                "body_sha256": VERIFIER.digest_bytes(b"broker-body"),
            },
            "authoritative_state_reference": {
                "path": str(self.root / "account-snapshot.json"),
                "file_sha256": VERIFIER.digest_bytes(b"account-file"),
                "body_sha256": VERIFIER.digest_bytes(b"account-body"),
            },
            "signature_verification": {
                "algorithm": "ED25519",
                "public_key": executable(
                    "/etc/heptatrader/paper-account-authority.pub",
                    b"public-key"),
                "verifier": executable("/usr/bin/openssl", b"openssl"),
                "signature_sha256": VERIFIER.digest_bytes(b"signature"),
                "signed_payload_sha256":
                    VERIFIER.digest_bytes(b"signed-payload"),
                "return_code": 0,
                "stdout": "Signature Verified Successfully\n",
                "stderr": "",
                "stdout_sha256": VERIFIER.digest_bytes(
                    b"Signature Verified Successfully\n"),
                "stderr_sha256": VERIFIER.digest_bytes(b""),
            },
            "request_nonce": "a" * 64,
            "account_id_sha256": VERIFIER.digest_bytes(b"paper-account"),
            "provider_id": "reviewed-remote-account-authority-a",
            "provider_request_id_sha256": VERIFIER.digest_bytes(b"request"),
            "provider_response_sha256": VERIFIER.digest_bytes(b"response"),
            "observation_method":
                "FIXED_LOCAL_READ_ONLY_SYSTEMD_PROC_BROKER_POLICY",
            "broker_policy_helper": executable(
                "/usr/libexec/hepta-broker-egress-policy", b"broker-helper"),
            "broker_observer_id":
                "hepta-p1-zero-exposure-local-boundary-v2",
            "account_observer_id":
                "hepta-p1-zero-exposure-signed-adapter-v2",
            "observation_authority": "INDEPENDENT_REMOTE_READ_ONLY_ACCOUNT",
            "query_effect": "READ_ONLY", "query_epoch": "query-epoch-a",
            "query_fencing_generation": 7,
            "query_invocation_id": "query-invocation-a",
            "read_only_authority": True,
            "authoritative": True, "account_complete": True,
            "snapshot_sha256": VERIFIER.digest_bytes(b"account-snapshot"),
            "observation_complete": True, "broker_deny_all": True,
            "policy_sha256": VERIFIER.digest_bytes(b"deny-all-policy"),
            "authorized_connectors": 0, "authorized_uids": [],
            "broker_socket_count": 0, "broker_process_count": 0,
            "credential_exposure_count": 0, "order_count": 0,
            "position_count": 0, "gross_absolute_position": 0,
            "end_flat": True,
            "paper_units_inactive": True, "kill_switch_engaged": True,
            "protected_broker_ports": [4001, 4002, 7496, 7497],
            "process_inventory_complete": True,
            "socket_inventory_complete": True,
            "credential_inventory_complete": True,
            "host_authority_lease": host_authority_lease,
            "host_authority_lease_reacquired": True,
            "paper_authorized": False, "live_authorized": False,
            "mutation_authorized": False, "direct_broker_access": False,
            "order_submission_authorized": False,
        })
        self._write("zero_exposure_receipt", VERIFIER.seal(exposure))

    def evaluate(self):
        # The native aggregate's raw reports and externally signed provisioner
        # receipts are covered by dedicated file-backed tests.  This broad
        # admission fixture keeps those inputs synthetic and patches only that
        # causal reopen boundary.
        with self.verifier_runtime_layout(), mock.patch.object(
                VERIFIER, "_reverify_native_gate_evidence", return_value=None):
            return VERIFIER.evaluate_candidate(
                self.paths, expected_domain=self.domain,
                expected_campaign=self.campaign, expected_uid=self.uid,
                now_ms=self.now_ms)

    @contextmanager
    def verifier_runtime_layout(self):
        with (
            mock.patch.object(
                VERIFIER, "INSTALL_RECEIPT_PATH",
                str(self.paths["install_receipt"])),
            mock.patch.object(
                VERIFIER, "INSTALL_MANIFEST_PATH",
                str(self.paths["install_manifest"])),
            mock.patch.object(
                VERIFIER, "INSTALL_BACKUP_ROOT", self.install_backup_root),
        ):
            yield


class PaperAdmissionVerifierTests(unittest.TestCase):
    def test_activation_predecessor_lineage_is_exact(self):
        success = predecessor_activation_success()
        failure = predecessor_activation_failure()
        VERIFIER._validate_activation_predecessor_lineage(
            success, failure, "TEST_PREDECESSOR_INVALID")
        mutations = (
            ("success-file", success, "receipt_file_sha256",
             VERIFIER.digest_bytes(b"tampered-success")),
            ("success-schema", success, "receipt_schema", "tampered.v3"),
            ("failure-journal", failure, "journal_sha256",
             VERIFIER.digest_bytes(b"tampered-journal")),
            ("round86-ancestor-binding", failure, "receipt_body_sha256",
             VERIFIER.digest_bytes(b"tampered-ancestor")),
        )
        for label, original, field, value in mutations:
            with self.subTest(label=label):
                changed = copy.deepcopy(original)
                changed[field] = value
                with self.assertRaises(VERIFIER.AdmissionError):
                    VERIFIER._validate_activation_predecessor_lineage(
                        changed if original is success else success,
                        changed if original is failure else failure,
                        "TEST_PREDECESSOR_INVALID")

    def test_profile_transition_receipt_reference_is_strict(self):
        evidence = {
            "path": VERIFIER.DORMANT_PAPER_TO_WATCH_TRANSITION_RECEIPT_PATH,
            "sha256": VERIFIER.digest_bytes(b"sealed-transition-receipt"),
            "body_sha256": VERIFIER.digest_bytes(b"transition-body"),
            "bytes": 4096, "device": 1, "inode": 222,
            "mode": stat.S_IFREG | 0o600, "nlink": 1,
            "uid": 0, "gid": 0, "mtime_ns": 2000, "ctime_ns": 2001,
        }
        VERIFIER._validate_dormant_paper_to_watch_transition_receipt(
            evidence, "TEST_TRANSITION_INVALID")
        for field, value in (
            ("path", "/tmp/transition.json"),
            ("body_sha256", "tampered-body"),
            ("mode", stat.S_IFREG | 0o644),
        ):
            with self.subTest(field=field):
                changed = copy.deepcopy(evidence)
                changed[field] = value
                with self.assertRaises(VERIFIER.AdmissionError):
                    VERIFIER._validate_dormant_paper_to_watch_transition_receipt(
                        changed, "TEST_TRANSITION_INVALID")

    def test_watch_handoff_v2_restoration_and_host_files_are_strict(self):
        legacy = copy.deepcopy(
            self.fixture.documents["watch_handoff_receipt"])
        legacy.pop("body_sha256")
        legacy["schema"] = "hepta.p1-watch-to-paper-handoff-receipt.v1"
        legacy["version"] = 1
        with self.assertRaises(VERIFIER.AdmissionError):
            VERIFIER.validate_watch_handoff(VERIFIER.seal(legacy))

        mutations = (
            ("missing", lambda fixture: fixture.documents[
                "watch_handoff_receipt"][
                    "paper_profile_restoration"].pop("retired_watch")),
            ("path", lambda fixture: fixture.documents[
                "watch_handoff_receipt"]["paper_profile_restoration"][
                    "target"].__setitem__("path", "/tmp/not-alpha.env")),
            ("hash", lambda fixture: fixture.documents[
                "watch_handoff_receipt"]["paper_profile_restoration"][
                    "target"].__setitem__(
                        "file_sha256", VERIFIER.digest_bytes(b"tampered"))),
            ("mode", lambda fixture: fixture.documents[
                "watch_handoff_receipt"]["paper_profile_restoration"][
                    "target"].__setitem__("mode", 0o644)),
            ("uid", lambda fixture: fixture.documents[
                "watch_handoff_receipt"]["paper_profile_restoration"][
                    "target"].__setitem__("uid", fixture.uid + 1)),
            ("gid", lambda fixture: fixture.documents[
                "watch_handoff_receipt"]["paper_profile_restoration"][
                    "target"].__setitem__("gid", os.getegid() + 1)),
            ("nlink", lambda fixture: fixture.documents[
                "watch_handoff_receipt"]["paper_profile_restoration"][
                    "target"].__setitem__("nlink", 2)),
            ("body", lambda fixture: fixture.documents[
                "watch_handoff_receipt"]["paper_profile_restoration"][
                    "forward_transition_receipt"].__setitem__(
                        "body_sha256", VERIFIER.digest_bytes(b"tampered"))),
            ("current-profile", lambda fixture:
                VERIFIER.PAPER_PROFILE_PATH.write_bytes(
                    b"X" * VERIFIER.PAPER_PROFILE_DORMANT_BYTES)),
            ("missing-preimage", lambda fixture:
                VERIFIER.PAPER_PROFILE_FORWARD_PREIMAGE_PATH.unlink()),
            ("candidate-residue", lambda fixture:
                VERIFIER.PAPER_PROFILE_CANDIDATE_PATH.write_bytes(b"residue")),
            ("runtime-missing", lambda fixture: fixture.documents[
                "watch_handoff_receipt"][
                    "paper_runtime_profile_hardening"].pop("retained_legacy")),
            ("runtime-schema", lambda fixture: fixture.documents[
                "watch_handoff_receipt"][
                    "paper_runtime_profile_hardening"].__setitem__(
                        "schema", "legacy.runtime-profile.v0")),
            ("runtime-path", lambda fixture: fixture.documents[
                "watch_handoff_receipt"][
                    "paper_runtime_profile_hardening"]["target"].__setitem__(
                        "path", "/tmp/not-paper-runtime.env")),
            ("runtime-hash", lambda fixture: fixture.documents[
                "watch_handoff_receipt"][
                    "paper_runtime_profile_hardening"]["target"].__setitem__(
                        "file_sha256", VERIFIER.digest_bytes(b"tampered"))),
            ("runtime-mode", lambda fixture: fixture.documents[
                "watch_handoff_receipt"][
                    "paper_runtime_profile_hardening"]["target"].__setitem__(
                        "mode", 0o644)),
            ("runtime-not-hardened", lambda fixture: fixture.documents[
                "watch_handoff_receipt"].__setitem__(
                    "paper_runtime_profile_hardened", False)),
            ("runtime-current", lambda fixture:
                VERIFIER.PAPER_RUNTIME_PROFILE_PATH.write_bytes(
                    b"X" * VERIFIER.PAPER_RUNTIME_PROFILE_HARDENED_BYTES)),
            ("runtime-backup", lambda fixture:
                VERIFIER.PAPER_RUNTIME_PROFILE_BACKUP_PATH.write_bytes(
                    b"X" * VERIFIER.PAPER_RUNTIME_PROFILE_LEGACY_BYTES)),
            ("runtime-retained", lambda fixture:
                VERIFIER.PAPER_RUNTIME_PROFILE_RETAINED_PATH.write_bytes(
                    b"X" * VERIFIER.PAPER_RUNTIME_PROFILE_LEGACY_BYTES)),
            ("runtime-candidate-residue", lambda fixture:
                VERIFIER.PAPER_RUNTIME_PROFILE_CANDIDATE_PATH.write_bytes(
                    b"residue")),
        )
        for index, (label, mutate) in enumerate(mutations):
            with self.subTest(label=label):
                case_root = self.root / f"handoff-v2-{index:02d}"
                case_root.mkdir(mode=0o700)
                fixture = EvidenceFixture(case_root, self.now_ms)
                mutate(fixture)
                if label not in {
                    "current-profile", "missing-preimage", "candidate-residue",
                    "runtime-current", "runtime-backup", "runtime-retained",
                    "runtime-candidate-residue",
                }:
                    fixture._rewrite("watch_handoff_receipt")
                evaluation = fixture.evaluate()
                self.assertEqual(evaluation.receipt["status"], "HALT")
                self.assertTrue(any(
                    "WATCH_HANDOFF" in finding
                    for finding in evaluation.receipt["findings"]))

    def test_round114_install_and_predecessor_pins_are_exact(self):
        self.assertEqual(
            VERIFIER.WATCH_HANDOFF_SCHEMA, HANDOFF_CONTRACT.RECEIPT_SCHEMA)
        self.assertEqual(
            VERIFIER.WATCH_HANDOFF_FIELDS, HANDOFF_CONTRACT.RECEIPT_FIELDS)
        self.assertEqual(
            VERIFIER.HANDOFF_PROFILE_RESTORATION_FIELDS,
            HANDOFF_CONTRACT.PROFILE_RESTORATION_FIELDS)
        self.assertEqual(
            VERIFIER.HANDOFF_PROFILE_FILE_FIELDS,
            HANDOFF_CONTRACT.PROFILE_FILE_EVIDENCE_FIELDS)
        self.assertEqual(
            VERIFIER.HANDOFF_PROFILE_SEALED_FILE_FIELDS,
            HANDOFF_CONTRACT.PROFILE_SEALED_EVIDENCE_FIELDS)
        self.assertEqual(
            VERIFIER.HANDOFF_RUNTIME_PROFILE_HARDENING_FIELDS,
            HANDOFF_CONTRACT.PAPER_RUNTIME_PROFILE_HARDENING_FIELDS)
        self.assertEqual(VERIFIER.ROUND, ROUND)
        self.assertEqual(VERIFIER.INSTALL_GENERATION, INSTALL_GENERATION)
        self.assertEqual(
            VERIFIER.PREDECESSOR_INSTALL_GENERATION,
            PREDECESSOR_INSTALL_GENERATION)
        self.assertEqual(VERIFIER.INSTALLED_FILE_COUNT, INSTALLED_FILE_COUNT)
        self.assertEqual(
            VERIFIER.PREDECESSOR_INSTALL_POINTER_SHA256,
            PREDECESSOR_INSTALL_POINTER_SHA256)
        self.assertEqual(
            VERIFIER.INSTALL_RECEIPT_PATH,
            "/var/lib/hepta/shadow-runtime-install-receipts/"
            "hepta-p1-round114-generation22-passive.json")
        self.assertEqual(
            VERIFIER.INSTALL_MANIFEST_PATH,
            "/var/lib/hepta/shadow-runtime-install-artifacts/"
            "hepta-p1-round114-generation22-shadow-runtime.manifest.json")
        self.assertEqual(
            VERIFIER.INSTALL_BACKUP_ROOT,
            "/var/lib/hepta/shadow-runtime-backups/"
            "hepta-p1-round114-generation22-passive")
        self.assertEqual(
            VERIFIER.PREDECESSOR_PROFILE_RECEIPT_PATH,
            PREDECESSOR_PROFILE_RECEIPT_PATH)
        self.assertEqual(
            VERIFIER.PREDECESSOR_PROFILE_RECEIPT_FILE_SHA256,
            PREDECESSOR_PROFILE_RECEIPT_FILE_SHA256)
        self.assertEqual(
            VERIFIER.PREDECESSOR_PROFILE_RECEIPT_BODY_SHA256,
            PREDECESSOR_PROFILE_RECEIPT_BODY_SHA256)
        self.assertEqual(
            VERIFIER.PREDECESSOR_PROFILE_RECEIPT_BYTES,
            PREDECESSOR_PROFILE_RECEIPT_BYTES)

    def test_directory_identity_ignores_legitimate_child_churn(self):
        before = mock.Mock(
            st_dev=1, st_ino=2, st_mode=stat.S_IFDIR | 0o700,
            st_nlink=2, st_uid=self.fixture.uid, st_gid=os.getegid())
        after = mock.Mock(
            st_dev=1, st_ino=2, st_mode=stat.S_IFDIR | 0o700,
            st_nlink=99, st_uid=self.fixture.uid, st_gid=os.getegid())
        self.assertEqual(
            VERIFIER._directory_identity(before),
            VERIFIER._directory_identity(after))

    def test_native_causal_reverify_rebuilds_raw_reports_and_receipts(self):
        document = copy.deepcopy(
            self.fixture.documents["native_gate_receipt"])
        with mock.patch.object(
                NATIVE_AGGREGATE, "verify_runtime_aggregate",
                return_value=document) as verify:
            VERIFIER._reverify_native_gate_evidence(document)
        verify.assert_called_once_with(document)

        drifted = copy.deepcopy(document)
        drifted["variants"]["real"]["instance_uuid"] = (
            "00000000-0000-4000-8000-000000000099")
        with (mock.patch.object(
                NATIVE_AGGREGATE, "verify_runtime_aggregate",
                return_value=drifted),
              self.assertRaises(VERIFIER.AdmissionError) as mismatch):
            VERIFIER._reverify_native_gate_evidence(document)
        self.assertEqual(
            mismatch.exception.reason,
            "NATIVE_GATE_CAUSAL_EVIDENCE_INVALID")

        with (mock.patch.object(
                NATIVE_AGGREGATE, "verify_runtime_aggregate",
                side_effect=NATIVE_AGGREGATE.AggregateError("tampered")),
              self.assertRaises(VERIFIER.AdmissionError) as invalid):
            VERIFIER._reverify_native_gate_evidence(document)
        self.assertEqual(
            invalid.exception.reason,
            "NATIVE_GATE_CAUSAL_EVIDENCE_INVALID")

    def test_native_external_trust_or_raw_reopen_failure_is_halt(self):
        with self.fixture.verifier_runtime_layout(), mock.patch.object(
                VERIFIER, "_reverify_native_gate_evidence",
                side_effect=VERIFIER.AdmissionError(
                    "NATIVE_GATE_CAUSAL_EVIDENCE_INVALID")):
            evaluation = VERIFIER.evaluate_candidate(
                self.fixture.paths,
                expected_domain=self.fixture.domain,
                expected_campaign=self.fixture.campaign,
                expected_uid=self.fixture.uid,
                now_ms=self.fixture.now_ms)
        self.assertEqual(evaluation.receipt["status"], "HALT")
        self.assertIn(
            "NATIVE_GATE_CAUSAL_EVIDENCE_INVALID",
            evaluation.receipt["findings"])

    def test_release_causal_runtime_mapping_matches_installed_contract(self):
        executable_sources = {
            "scripts/aggregate_hepta_execution_native_systemd_gate.py",
            "scripts/build_hepta_execution_native_vm_bundle.py",
            "scripts/build_heptatrader_evidence_ingestion_request.py",
            "scripts/converge_ctp_vendor_headers.py",
            "scripts/run_hepta_execution_native_systemd_gate.py",
            "scripts/verify_hepta_execution_native_vm_bundle.py",
            "scripts/verify_heptatrader_evidence_ingestion_receipt.py",
            "scripts/verify_heptatrader_evidence_set.py",
        }
        expected = {
            "scripts/verify_heptatrader_release_validation_closure.py": (
                "usr/libexec/hepta-release-validation-closure-verifier",
                "0644", "0755"),
        }
        expected.update({
            f"scripts/{name}.py": (
                f"usr/libexec/{name}.py",
                "0755" if f"scripts/{name}.py" in executable_sources
                else "0644", "0644")
            for name in RUNTIME_VERIFIER.RELEASE_VALIDATION_COMPANION_NAMES
            if name != "verify_heptatrader_release_validation_closure"
        })
        expected.update({
            path.removeprefix("usr/libexec/"): (path, "0644", "0644")
            for path in RUNTIME_VERIFIER.RELEASE_VALIDATION_PACKAGE_FILES
        })
        self.assertEqual(
            VERIFIER.RELEASE_CAUSAL_SOURCE_INSTALL_PATHS, expected)
        self.assertNotIn(
            "scripts/hepta_ops.py",
            VERIFIER.RELEASE_CAUSAL_SOURCE_INSTALL_PATHS)
        self.assertEqual(
            VERIFIER.RELEASE_CAUSAL_OPENSSL, Path("/usr/bin/openssl"))
        self.assertEqual(
            VERIFIER.RELEASE_CAUSAL_CHROOT, Path("/usr/sbin/chroot"))
        source_root = Path(__file__).resolve().parents[1]
        for source, (_target, source_mode, _target_mode) in expected.items():
            self.assertEqual(
                bool(stat.S_IMODE((source_root / source).stat().st_mode) &
                     0o111),
                source_mode == "0755", source)

    def test_release_causal_dependencies_bind_declared_trust_keys(self):
        trust_root = self.root / "causal-trust"
        trust_root.mkdir(mode=0o700)
        key_path = trust_root / "review.pub"
        key_path.write_bytes(b"reviewed-ed25519-public-key\n")
        key_path.chmod(0o444)
        policy_path = trust_root / "trust-policy.json"
        policy_payload = VERIFIER.canonical_bytes({
            "keys": [{"public_key_path": key_path.name}],
        })
        policy_path.write_bytes(policy_payload)
        policy_path.chmod(0o400)
        closure_path = trust_root / "release-closure.json"
        closure_payload = b"{}"
        closure_path.write_bytes(closure_payload)
        closure_path.chmod(0o400)
        document = {
            "local_evidence": {"critical_files": []},
            "retention_evidence": {
                "evidence_root": str(trust_root),
                "inputs": {
                    "trust_policy": {
                        "path": str(policy_path),
                        "sha256": VERIFIER.digest_bytes(
                            policy_payload).removeprefix("sha256:"),
                        "size": len(policy_payload), "mode": "0400",
                    },
                },
            },
        }
        closure = VERIFIER.InputSnapshot(
            "release_validation_receipt", closure_path, closure_payload,
            closure_path.stat(), document,
            VERIFIER.digest_bytes(closure_payload),
            VERIFIER.digest_bytes(closure_payload),
        )
        with mock.patch.object(VERIFIER, "ROOT_UID", self.fixture.uid):
            bindings = VERIFIER._bind_release_causal_dependencies(closure)
            self.assertEqual(
                {binding.path for binding in bindings},
                {policy_path, key_path})
            key_path.chmod(0o644)
            key_path.write_bytes(b"replaced-ed25519-public-key\n")
            key_path.chmod(0o444)
            with self.assertRaises(VERIFIER.AdmissionError) as caught:
                for binding in bindings:
                    binding.reopen()
        self.assertEqual(
            caught.exception.reason,
            "ADMISSION_RELEASE_CAUSAL_TRUST_KEY_REBOUND")

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.now_ms = 2_000_000_000_000
        self.fixture = EvidenceFixture(self.root, self.now_ms)

    @contextmanager
    def _release_causal_stage_fixture(self):
        install_root = self.root / "causal-installed"
        mapping = {}
        for source, (
                installed, source_mode, installed_mode,
        ) in VERIFIER.RELEASE_CAUSAL_SOURCE_INSTALL_PATHS.items():
            relative = Path(installed).relative_to("usr/libexec")
            target = install_root / "usr/libexec" / relative
            target.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
            target.write_bytes((source + "\n").encode("utf-8"))
            target.chmod(int(installed_mode, 8))
            mapping[source] = (str(target), source_mode, installed_mode)
        interpreter_path = install_root / "usr/bin/python3.12"
        openssl_path = install_root / "usr/bin/openssl"
        chroot_path = install_root / "usr/sbin/chroot"
        interpreter_path.parent.mkdir(parents=True, mode=0o700)
        chroot_path.parent.mkdir(parents=True, mode=0o700)
        interpreter_path.write_bytes(b"bound-python\n")
        interpreter_path.chmod(0o755)
        openssl_path.write_bytes(b"bound-openssl\n")
        openssl_path.chmod(0o755)
        chroot_path.write_bytes(b"bound-chroot\n")
        chroot_path.chmod(0o755)
        verifier_path = Path(mapping[
            "scripts/verify_heptatrader_release_validation_closure.py"
        ][0])
        stage_parent = self.root / "run/hepta"
        stage_parent.mkdir(parents=True, mode=0o700)
        stage_parent.chmod(0o700)
        stage_path = stage_parent / ".hepta-release-causal-stage"
        with (
            mock.patch.object(
                VERIFIER, "RELEASE_CAUSAL_SOURCE_INSTALL_PATHS", mapping),
            mock.patch.object(
                VERIFIER, "RELEASE_CAUSAL_PYTHON", interpreter_path),
            mock.patch.object(
                VERIFIER, "RELEASE_CAUSAL_OPENSSL", openssl_path),
            mock.patch.object(
                VERIFIER, "RELEASE_CAUSAL_CHROOT", chroot_path),
            mock.patch.object(
                VERIFIER, "RELEASE_CAUSAL_VERIFIER", verifier_path),
            mock.patch.object(VERIFIER, "RELEASE_CAUSAL_STAGE", stage_path),
            mock.patch.object(
                VERIFIER, "RELEASE_CAUSAL_ABI_LOGICAL_PATHS", ()),
            mock.patch.object(
                VERIFIER, "_bind_release_causal_python_tree",
                return_value=((), ())),
            mock.patch.object(VERIFIER, "ROOT_UID", self.fixture.uid),
        ):
            runtime = VERIFIER._bind_release_causal_runtime()
            stage = VERIFIER._create_release_causal_stage(
                rootfs_files=runtime.rootfs_files,
                verifier_path=runtime.verifier.path,
                owner_uid=self.fixture.uid)
            yield stage, runtime

    def tearDown(self):
        self.temporary.cleanup()

    def test_release_causal_stage_extra_file_is_rejected(self):
        with self._release_causal_stage_fixture() as (stage, _runtime):
            extra = stage.path / "extra.py"
            extra.write_bytes(b"unbound-extra\n")
            extra.chmod(0o400)
            with self.assertRaisesRegex(
                    VERIFIER.AdmissionError,
                    "ADMISSION_RELEASE_CAUSAL_STAGE_REBOUND"):
                stage.reopen()

    def test_release_causal_stage_replaced_file_is_rejected(self):
        with self._release_causal_stage_fixture() as (stage, _runtime):
            target = next(
                stage.path.joinpath(*entry.relative_path.parts)
                for entry in stage.files)
            replacement = stage.path / ".replacement"
            replacement.write_bytes(target.read_bytes())
            replacement.chmod(0o400)
            os.replace(replacement, target)
            with self.assertRaisesRegex(
                    VERIFIER.AdmissionError,
                    "ADMISSION_RELEASE_CAUSAL_STAGE_REBOUND"):
                stage.reopen()

    def test_release_causal_stage_pycache_is_rejected(self):
        with self._release_causal_stage_fixture() as (stage, _runtime):
            pycache = stage.path / "__pycache__"
            pycache.mkdir(mode=0o700)
            cached = pycache / "shadow.cpython-312.pyc"
            cached.write_bytes(b"unbound-bytecode\n")
            cached.chmod(0o400)
            with self.assertRaisesRegex(
                    VERIFIER.AdmissionError,
                    "ADMISSION_RELEASE_CAUSAL_STAGE_REBOUND"):
                stage.reopen()

    def test_release_causal_stage_residue_fails_closed_then_exact_cleanup(self):
        with self._release_causal_stage_fixture() as (stage, runtime):
            with self.assertRaisesRegex(
                    VERIFIER.AdmissionError,
                    "ADMISSION_RELEASE_CAUSAL_STAGE_RESIDUE"):
                VERIFIER._create_release_causal_stage(
                    rootfs_files=runtime.rootfs_files,
                    verifier_path=runtime.verifier.path,
                    owner_uid=self.fixture.uid)
            stage.reopen()
            stage.cleanup()
            self.assertFalse(stage.path.exists())

    def test_release_causal_runner_uses_private_stage_and_production_flags(self):
        with self._release_causal_stage_fixture() as (stage, runtime):
            rebound_runtime = VERIFIER._bind_release_causal_runtime()
            self.assertEqual(
                rebound_runtime.verifier.path, runtime.verifier.path)
            self.assertEqual(
                tuple(item.path for item in rebound_runtime.runtime_modules),
                tuple(item.path for item in runtime.runtime_modules))
            closure_path = self.root / "causal-closure.json"
            closure_payload = b"{}\n"
            closure_path.write_bytes(closure_payload)
            closure_path.chmod(0o600)
            closure = VERIFIER.InputSnapshot(
                "release_validation_receipt", closure_path, closure_payload,
                closure_path.stat(), {},
                VERIFIER.digest_bytes(closure_payload),
                VERIFIER.digest_bytes(closure_payload),
            )
            evaluation = VERIFIER.Evaluation(
                {}, {"release_validation_receipt": closure})
            completed = mock.Mock(
                returncode=0,
                stdout=VERIFIER.RELEASE_CAUSAL_EXPECTED_STDOUT.encode("ascii"),
                stderr=b"",
            )
            with (
                mock.patch.object(
                    VERIFIER, "_bind_release_causal_dependencies",
                    return_value=()),
                mock.patch.object(VERIFIER.os, "geteuid", return_value=0),
                mock.patch.object(VERIFIER.os, "getegid", return_value=0),
                mock.patch.object(
                    VERIFIER, "_verify_release_causal_mapped_libc"),
                mock.patch.object(
                    VERIFIER, "_enter_release_causal_private_mount_namespace"),
                mock.patch.object(
                    VERIFIER, "_create_release_causal_stage",
                    return_value=stage) as create_stage,
                mock.patch.object(
                    VERIFIER, "_run_release_causal_pinned_child",
                    return_value=completed) as run,
            ):
                result = VERIFIER._run_release_causal_verifier(
                    evaluation, runtime=rebound_runtime)
            self.assertIs(result.closure, closure)
            self.assertFalse(stage.path.exists())
            self.assertTrue(
                create_stage.call_args.kwargs["private_mount_namespace"])
            argv = run.call_args.kwargs["arguments"]
            self.assertEqual(
                argv[:4],
                (str(VERIFIER.RELEASE_CAUSAL_PYTHON), "-I", "-S", "-B"))
            self.assertEqual(Path(argv[4]), stage.child_verifier_path)
            self.assertEqual(argv[5:7], ("--closure", str(closure_path)))
            self.assertEqual(
                run.call_args.kwargs["environment"],
                VERIFIER.RELEASE_CAUSAL_ENVIRONMENT)
            self.assertNotIn("preexec_fn", run.call_args.kwargs)

    def test_sudo_private_noexec_stage_pins_executed_root_across_path_swap(
            self):
        """Exercise the production mount/chroot/exec path under real sudo."""

        sudo = Path("/usr/bin/sudo")
        if sys.platform != "linux" or not sudo.is_file():
            self.skipTest("Linux sudo is required for the causal mount probe")
        capability = subprocess.run(
            [str(sudo), "-n", "true"], check=False,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
        if capability.returncode != 0:
            self.skipTest("passwordless sudo is unavailable")
        inner_probe = textwrap.dedent("""\
            import hashlib
            import json
            import os
            from pathlib import Path
            import subprocess
            import tempfile
            assert Path(json.__file__).is_relative_to(
                Path("/usr/lib/python3.12")), json.__file__
            with tempfile.TemporaryDirectory() as directory:
                assert Path(directory).parent == Path("/tmp"), directory
            completed = subprocess.run(
                ["/usr/bin/openssl", "pkey", "-pubin", "-in",
                 "/etc/heptatrader/release-causal-test-ed25519.pub",
                 "-outform", "DER"],
                check=False, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env={
                    "PATH": "/usr/bin", "LANG": "C", "LC_ALL": "C",
                    "OPENSSL_CONF":
                        "/etc/heptatrader/release-causal-openssl.cnf",
                    "OPENSSL_MODULES":
                        "/nonexistent-release-causal-provider-directory",
                })
            assert completed.returncode == 0, (
                completed.returncode, completed.stderr)
            root = os.stat("/")
            script = os.stat(__file__, follow_symlinks=False)
            print(
                f"PINNED_ROOT={root.st_dev}:{root.st_ino} "
                f"SCRIPT={script.st_dev}:{script.st_ino} "
                f"DER={hashlib.sha256(completed.stdout).hexdigest()}")
        """).encode("utf-8")
        public_key = (
            b"-----BEGIN PUBLIC KEY-----\n"
            b"MCowBQYDK2VwAyEAnHlgqOSGWTqPi6Xl5/0o1Vp+xJCUNedG41Xu4DfrDWI=\n"
            b"-----END PUBLIC KEY-----\n")
        root_probe = textwrap.dedent(r"""
            import errno
            import hashlib
            import importlib.util
            import os
            from pathlib import Path
            import sys

            module_path = Path(sys.argv[1])
            sys.path.insert(0, str(module_path.parent))
            spec = importlib.util.spec_from_file_location(
                "release_causal_sudo_probe", module_path)
            assert spec is not None and spec.loader is not None
            verifier = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = verifier
            spec.loader.exec_module(verifier)

            run_mount = verifier._release_causal_mount_record(
                Path("/run"), "TEST_RELEASE_CAUSAL_RUN_MOUNT")
            run_options = (
                run_mount["mount_options"] | run_mount["super_options"])
            if "noexec" not in run_options:
                raise SystemExit(77)
            assert not verifier.RELEASE_CAUSAL_STAGE.exists()

            manifests, python_entries = (
                verifier._bind_release_causal_python_tree())
            assert len(manifests) == 4
            entries = [
                verifier._bind_logical_runtime_file(
                    verifier.RELEASE_CAUSAL_PYTHON,
                    reason="TEST_RELEASE_CAUSAL_PYTHON"),
                verifier._bind_logical_runtime_file(
                    verifier.RELEASE_CAUSAL_OPENSSL,
                    reason="TEST_RELEASE_CAUSAL_OPENSSL"),
                *python_entries,
            ]
            entries.extend(
                verifier._bind_logical_runtime_file(
                    path, reason="TEST_RELEASE_CAUSAL_ABI")
                for path in verifier.RELEASE_CAUSAL_ABI_LOGICAL_PATHS)
            probe_path = Path(
                "/usr/libexec/hepta-release-causal-private-probe.py")
            replacement_path = Path(
                "/usr/libexec/hepta-release-causal-private-replacement.py")
            public_key_path = Path(
                "/etc/heptatrader/release-causal-test-ed25519.pub")
            probe_payload = __INNER_PROBE_PAYLOAD__
            public_key = __PUBLIC_KEY_PAYLOAD__
            entries.extend((
                verifier.RootfsRuntimeFile(
                    Path(
                        "/etc/heptatrader/release-causal-openssl.cnf"),
                    verifier.RELEASE_CAUSAL_OPENSSL_CONFIGURATION,
                    0o400, None),
                verifier.RootfsRuntimeFile(
                    public_key_path, public_key, 0o400, None),
                verifier.RootfsRuntimeFile(
                    probe_path, probe_payload, 0o400, None),
                verifier.RootfsRuntimeFile(
                    replacement_path,
                    b'raise SystemExit("HOSTILE_REPLACEMENT_EXECUTED")\n',
                    0o400, None),
            ))
            rootfs_files = tuple(sorted(
                entries, key=lambda entry: str(entry.logical_path)))
            verifier._verify_release_causal_mapped_libc(rootfs_files)

            request_read, request_write = os.pipe()
            ready_read, ready_write = os.pipe()
            restore_read, restore_write = os.pipe()
            attacker_pid = os.fork()
            if attacker_pid == 0:
                displaced = verifier.RELEASE_CAUSAL_STAGE.with_name(
                    verifier.RELEASE_CAUSAL_STAGE.name +
                    ".displaced-sudo-probe")
                swapped = False
                try:
                    os.close(request_write)
                    os.close(ready_read)
                    os.close(restore_write)
                    assert os.read(request_read, 1) == b"X"
                    assert not displaced.exists()
                    os.rename(verifier.RELEASE_CAUSAL_STAGE, displaced)
                    swapped = True
                    os.mkdir(verifier.RELEASE_CAUSAL_STAGE, 0o700)
                    hostile = (
                        verifier.RELEASE_CAUSAL_STAGE /
                        "HOSTILE_PATH_REPLACEMENT")
                    hostile.write_bytes(b"hostile-path-bytes\n")
                    os.write(ready_write, b"R")
                    assert os.read(restore_read, 1) == b"Y"
                    os.unlink(hostile)
                    os.rmdir(verifier.RELEASE_CAUSAL_STAGE)
                    os.rename(displaced, verifier.RELEASE_CAUSAL_STAGE)
                    swapped = False
                    os._exit(0)
                except BaseException:
                    if swapped:
                        try:
                            hostile = (
                                verifier.RELEASE_CAUSAL_STAGE /
                                "HOSTILE_PATH_REPLACEMENT")
                            if hostile.exists():
                                os.unlink(hostile)
                            if verifier.RELEASE_CAUSAL_STAGE.exists():
                                os.rmdir(verifier.RELEASE_CAUSAL_STAGE)
                            if displaced.exists():
                                os.rename(
                                    displaced,
                                    verifier.RELEASE_CAUSAL_STAGE)
                        except OSError:
                            pass
                    os._exit(94)

            os.close(request_read)
            os.close(ready_write)
            os.close(restore_read)
            stage = None
            attacker_requested = [False]
            attacker_waited = False
            original_reopen = verifier._reopen_release_causal_stage
            try:
                verifier._enter_release_causal_private_mount_namespace()
                stage = verifier._create_release_causal_stage(
                    rootfs_files=rootfs_files,
                    verifier_path=probe_path,
                    private_mount_namespace=True)
                verifier._verify_release_causal_mounts(
                    stage.path, read_only=True,
                    reason="TEST_RELEASE_CAUSAL_MOUNTS")

                source = stage.path.joinpath(
                    *replacement_path.parts[1:])
                target = stage.path.joinpath(*probe_path.parts[1:])
                try:
                    os.replace(source, target)
                except OSError as error:
                    assert error.errno == errno.EROFS, error
                else:
                    raise AssertionError(
                        "read-only stage replacement succeeded")

                expected_root = (
                    f"{stage.root_metadata.st_dev}:"
                    f"{stage.root_metadata.st_ino}")
                probe_entry = next(
                    entry for entry in stage.files
                    if entry.relative_path == Path(*probe_path.parts[1:]))
                expected_script = (
                    f"{probe_entry.metadata.st_dev}:"
                    f"{probe_entry.metadata.st_ino}")

                def reopen_then_swap_path(value):
                    original_reopen(value)
                    if not attacker_requested[0]:
                        attacker_requested[0] = True
                        os.write(request_write, b"X")
                        assert os.read(ready_read, 1) == b"R"
                        assert (
                            value.path / "HOSTILE_PATH_REPLACEMENT"
                        ).read_bytes() == b"hostile-path-bytes\n"
                        pinned = os.fstat(value.pinned_root_descriptor)
                        assert pinned.st_dev == value.root_metadata.st_dev
                        assert pinned.st_ino == value.root_metadata.st_ino

                verifier._reopen_release_causal_stage = (
                    reopen_then_swap_path)
                completed = verifier._run_release_causal_pinned_child(
                    stage,
                    arguments=(
                        str(verifier.RELEASE_CAUSAL_PYTHON),
                        "-I", "-S", "-B",
                        str(stage.child_verifier_path)),
                    environment=verifier.RELEASE_CAUSAL_ENVIRONMENT,
                    timeout=120)
                verifier._reopen_release_causal_stage = original_reopen
                assert attacker_requested[0]
                assert completed.returncode == 0, (
                    completed.returncode, completed.stdout,
                    completed.stderr)
                expected = (
                    f"PINNED_ROOT={expected_root} "
                    f"SCRIPT={expected_script} "
                    "DER=fff5621a266893c5c7f8af13115f32f5625c9f27195cd892"
                    "7206e99bbc2183f7\n").encode("ascii")
                assert completed.stdout == expected, (
                    completed.stdout, expected)
                assert completed.stderr == b"", completed.stderr

                os.write(restore_write, b"Y")
                _pid, status = os.waitpid(attacker_pid, 0)
                attacker_waited = True
                assert os.waitstatus_to_exitcode(status) == 0, status
                stage.reopen()
            finally:
                verifier._reopen_release_causal_stage = original_reopen
                if not attacker_waited:
                    if not attacker_requested[0]:
                        try:
                            os.write(request_write, b"X")
                        except OSError:
                            pass
                    try:
                        os.write(restore_write, b"Y")
                    except OSError:
                        pass
                    try:
                        os.waitpid(attacker_pid, 0)
                    except ChildProcessError:
                        pass
                if stage is not None:
                    stage.cleanup()
                for descriptor in (
                        request_write, ready_read, restore_write):
                    try:
                        os.close(descriptor)
                    except OSError:
                        pass
            assert not verifier.RELEASE_CAUSAL_STAGE.exists()
            print("PRIVATE_CAUSAL_PRODUCTION_PATH_OK")
        """).replace(
            "__INNER_PROBE_PAYLOAD__", repr(inner_probe)).replace(
                "__PUBLIC_KEY_PAYLOAD__", repr(public_key))
        completed = subprocess.run(
            [
                str(sudo), "-n", "env", "PYTHONDONTWRITEBYTECODE=1",
                str(VERIFIER.RELEASE_CAUSAL_PYTHON), "-B", "-",
                str(MODULE_PATH),
            ],
            input=root_probe.encode("utf-8"), check=False,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180)
        if completed.returncode == 77:
            self.skipTest("host /run is not mounted noexec")
        self.assertEqual(
            completed.returncode, 0,
            (completed.stdout, completed.stderr))
        self.assertEqual(
            completed.stdout, b"PRIVATE_CAUSAL_PRODUCTION_PATH_OK\n")
        self.assertEqual(completed.stderr, b"")

    def test_real_child_uses_only_staged_python_abi_and_openssl(self):
        """A hostile PYTHONPATH/PATH cannot reach the causal child."""

        stage_parent = self.root / "real-child/run/hepta"
        stage_parent.mkdir(parents=True, mode=0o700)
        stage_parent.chmod(0o700)
        manifests, python_files = (
            VERIFIER._bind_release_causal_python_tree())
        self.assertEqual(len(manifests), 4)
        self.assertEqual(
            {entry.logical_path for entry in python_files if not entry.payload},
            {
                Path("/usr/lib/python3.12/email/mime/__init__.py"),
                Path("/usr/lib/python3.12/pydoc_data/__init__.py"),
                Path("/usr/lib/python3.12/test/libregrtest/__init__.py"),
                Path("/usr/lib/python3.12/test/typinganndata/__init__.py"),
                Path("/usr/lib/python3.12/urllib/__init__.py"),
            })
        self.assertEqual(
            {entry.logical_path for entry in python_files
             if entry.logical_path.is_symlink()},
            {
                Path("/usr/lib/python3.12/sitecustomize.py"),
                Path("/usr/lib/python3.12/"
                     "_sysconfigdata__linux_x86_64-linux-gnu.py"),
            })
        entries = [
            VERIFIER._bind_logical_runtime_file(
                VERIFIER.RELEASE_CAUSAL_PYTHON,
                reason="TEST_RELEASE_CAUSAL_PYTHON"),
            VERIFIER._bind_logical_runtime_file(
                VERIFIER.RELEASE_CAUSAL_OPENSSL,
                reason="TEST_RELEASE_CAUSAL_OPENSSL"),
            VERIFIER._bind_logical_runtime_file(
                VERIFIER.RELEASE_CAUSAL_CHROOT,
                reason="TEST_RELEASE_CAUSAL_CHROOT"),
            *python_files,
        ]
        entries.extend(
            VERIFIER._bind_logical_runtime_file(
                path, reason="TEST_RELEASE_CAUSAL_ABI")
            for path in VERIFIER.RELEASE_CAUSAL_ABI_LOGICAL_PATHS)
        entries.append(VERIFIER.RootfsRuntimeFile(
            Path("/etc/heptatrader/release-causal-openssl.cnf"),
            VERIFIER.RELEASE_CAUSAL_OPENSSL_CONFIGURATION, 0o400, None))
        shadow = self.root / "host-shadow"
        shadow.mkdir(mode=0o700)
        (shadow / "json.py").write_text(
            "raise SystemExit('HOST_SHADOW_JSON_EXECUTED')\n",
            encoding="utf-8")
        shadow_openssl = shadow / "openssl"
        shadow_openssl.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
        shadow_openssl.chmod(0o755)
        with mock.patch.object(
                VERIFIER, "RELEASE_CAUSAL_STAGE", stage_parent / ".stage"):
            stage = VERIFIER._create_release_causal_stage(
                rootfs_files=tuple(entries),
                verifier_path=VERIFIER.RELEASE_CAUSAL_PYTHON,
                owner_uid=os.getuid())
        try:
            loader = stage.path / "lib64/ld-linux-x86-64.so.2"
            library_root = stage.path / "lib/x86_64-linux-gnu"
            python = stage.path / "usr/bin/python3.12"
            child = """\
import json
import pathlib
import subprocess
import sys
root = pathlib.Path(sys.argv[1])
loader = root / "lib64/ld-linux-x86-64.so.2"
library_root = root / "lib/x86_64-linux-gnu"
openssl = root / "usr/bin/openssl"
configuration = root / "etc/heptatrader/release-causal-openssl.cnf"
assert pathlib.Path(json.__file__).is_relative_to(
    root / "usr/lib/python3.12"), json.__file__
completed = subprocess.run(
    [str(loader), "--inhibit-cache", "--library-path", str(library_root),
     str(openssl), "version"],
    check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    env={"PATH": sys.argv[2], "LANG": "C", "LC_ALL": "C",
         "OPENSSL_CONF": str(configuration),
         "OPENSSL_MODULES": "/nonexistent"})
assert completed.returncode == 0, (
    completed.returncode, completed.stdout, completed.stderr)
print(json.__file__)
print(completed.stdout.decode("ascii").strip())
"""
            completed = subprocess.run(
                [str(loader), "--inhibit-cache", "--library-path",
                 str(library_root), str(python), "-I", "-S", "-B", "-c",
                 child, str(stage.path), str(shadow)],
                check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                env={"PYTHONPATH": str(shadow), "PATH": str(shadow),
                     "LANG": "C", "LC_ALL": "C",
                     "PYTHONDONTWRITEBYTECODE": "1"})
            self.assertEqual(completed.returncode, 0, completed.stderr)
            output = completed.stdout.decode("utf-8").splitlines()
            self.assertTrue(output[0].startswith(
                str(stage.path / "usr/lib/python3.12")))
            self.assertTrue(output[1].startswith("OpenSSL 3.0."))
            self.assertFalse((shadow / "__pycache__").exists())
            stage.reopen()
            for manifest in manifests:
                manifest.reopen()
        finally:
            stage.cleanup()

    def test_exact_complete_chain_is_non_authorizing_go_candidate(self):
        evaluation = self.fixture.evaluate()
        receipt = evaluation.receipt
        self.assertEqual(receipt["status"], "GO")
        self.assertTrue(receipt["paper_test_admission_candidate"])
        self.assertEqual(receipt["findings"], [])
        self.assertEqual(
            receipt["strategy_sha256"],
            self.fixture.documents["p1_audit_receipt"]["strategy_sha256"])
        for field in (
            "paper_authorized", "live_authorized", "mutation_authorized",
            "direct_broker_access", "order_submission_authorized",
        ):
            self.assertIs(receipt[field], False)
        self.assertEqual(
            set(receipt["input_bindings"]), set(VERIFIER.INPUT_NAMES))
        self.assertTrue(all(
            binding["file_sha256"] and binding["body_sha256"]
            for binding in receipt["input_bindings"].values()))
        VERIFIER.validate_output_receipt(receipt)

    def test_terminal_p1_consumer_rechecks_duration_and_day_boundaries(self):
        original = self.fixture.documents["p1_audit_receipt"]
        VERIFIER.validate_p1_audit(original)

        too_short = copy.deepcopy(original)
        too_short.pop("body_sha256")
        interval = too_short["evaluated_interval"]
        interval["duration_ns"] = VERIFIER.MINIMUM_BOOTTIME_DURATION_NS - 1
        interval["end_boottime_ns"] = (
            interval["start_boottime_ns"] + interval["duration_ns"])
        with self.assertRaisesRegex(
                VERIFIER.AdmissionError, "P1_AUDIT_RECEIPT_INVALID"):
            VERIFIER.validate_p1_audit(VERIFIER.seal(too_short))

        for day_count, accepted in ((9, False), (10, True), (20, True),
                                    (21, False)):
            with self.subTest(day_count=day_count):
                candidate = copy.deepcopy(original)
                candidate.pop("body_sha256")
                candidate["counts"]["declared_trading_days"] = day_count
                candidate["counts"]["observed_trading_days"] = day_count
                candidate = VERIFIER.seal(candidate)
                facts = VERIFIER.validate_p1_audit(candidate)
                if accepted:
                    self.assertEqual(facts.readiness, ())
                else:
                    self.assertEqual(facts.readiness, ("P1_AUDIT_NOT_GO",))

    def test_liveness_gate_requires_the_exact_frozen_input_set(self):
        document = self.fixture.documents["p1_liveness_gate_receipt"]
        runner = "scripts/run_hepta_p1_campaign_rootful_liveness_gate.py"
        missing = next(path for path in document["inputs"] if path != runner)
        del document["inputs"][missing]
        document["lineage"]["input_manifest_sha256"] = VERIFIER.digest_bytes(
            VERIFIER.canonical_bytes(document["inputs"]))
        self.fixture._rewrite("p1_liveness_gate_receipt")

        receipt = self.fixture.evaluate().receipt
        self.assertEqual(receipt["status"], "HALT")
        self.assertEqual(receipt["findings"], [
            "P1_LIVENESS_GATE_RECEIPT_P1_LIVENESS_GATE_INVALID",
        ])

    def test_agent_os_gate_rejects_non_exact_inner_inputs_and_maps(self):
        original = self.fixture.documents["agent_os_rootful_gate_receipt"]
        for mutation in ("missing_input", "extra_input", "identity", "map"):
            with self.subTest(mutation=mutation):
                document = copy.deepcopy(original)
                if mutation == "missing_input":
                    document["inputs"] = document["inputs"][1:]
                elif mutation == "extra_input":
                    document["inputs"].append({
                        "path": "scripts/not-frozen.py",
                        "sha256": "0" * 64, "size": 1, "mode": "0644",
                        "device": 1, "inode": 9999,
                    })
                    document["inputs"].sort(key=lambda item: item["path"])
                elif mutation == "identity":
                    document["inner"]["identities"]["agent_uid"] = 9999
                else:
                    document["builder"]["objects"] = {"attested": True}
                with self.assertRaisesRegex(
                        VERIFIER.AdmissionError,
                        "AGENT_OS_ROOTFUL_GATE_INVALID"):
                    VERIFIER.validate_agent_os_rootful_gate(document)

    def test_agent_os_binary_hash_drift_across_gates_is_halt(self):
        document = self.fixture.documents["agent_os_rootful_gate_receipt"]
        record = next(
            item for item in document["inputs"]
            if PurePosixPath(item["path"]).name == "hepta-executiond")
        record["sha256"] = "f" * 64
        self.fixture._write("agent_os_rootful_gate_receipt", document)
        receipt = self.fixture.evaluate().receipt
        self.assertEqual(receipt["status"], "HALT")
        self.assertIn(
            "AGENT_OS_ROOTFUL_GATE_BINARY_BINDING_MISMATCH",
            receipt["findings"])

    def test_agent_os_binaries_bind_to_causally_verified_runtime_manifest(self):
        evaluation = self.fixture.evaluate()
        evidence_root = self.root / "release-causal-evidence"
        evidence_root.mkdir(mode=0o700)
        manifest_path = evidence_root / "runtime-package-manifest.json"
        records = []
        for binary, runtime_path in sorted(
                VERIFIER.AGENT_OS_RUNTIME_BINARY_PATHS.items()):
            installed = self.fixture.agent_binary_records[binary]
            records.append({
                "path": runtime_path, "mode": installed["mode"],
                "size": installed["size"], "sha256": installed["sha256"],
                "payload": {"kind": "elf"},
            })
        runtime_manifest = {
            "schema": "hepta.runtime-package.v1",
            "package_class": "production-runtime",
            "release_version": "1.0.0-round114",
            "root": "heptatrader-runtime-1.0.0-round114-linux-x86_64",
            "source_ref": {}, "vendor_ref": {}, "target": {},
            "boundary": {}, "file_count": len(records),
            "files_sha256": VERIFIER.digest_bytes(b"runtime-files"),
            "files": sorted(records, key=lambda item: item["path"]),
        }
        payload = VERIFIER.canonical_bytes(runtime_manifest)
        manifest_path.write_bytes(payload)
        manifest_path.chmod(0o600)
        release_snapshot = evaluation.snapshots[
            "release_validation_receipt"]
        release = release_snapshot.document
        release["retention_evidence"]["evidence_root"] = str(evidence_root)
        critical = next(
            item for item in release["local_evidence"]["critical_files"]
            if item["role"] == "runtime-package-manifest")
        critical.update({
            "path": manifest_path.name,
            "sha256": VERIFIER.digest_bytes(payload).removeprefix("sha256:"),
            "size": len(payload), "mode": "0600",
        })
        with mock.patch.object(VERIFIER, "ROOT_UID", self.fixture.uid):
            binding = VERIFIER._bind_runtime_file(
                manifest_path, modes=frozenset({0o600}),
                maximum=VERIFIER.MAXIMUM_INPUT_BYTES,
                reason="TEST_RUNTIME_MANIFEST_REBOUND")
            verification = VERIFIER.ReleaseCausalVerification(
                release_snapshot, mock.Mock(), (binding,))
            VERIFIER._validate_agent_os_binary_causal_binding(
                evaluation, verification)
            agent = next(
                item for item in evaluation.snapshots[
                    "agent_os_rootful_gate_receipt"].document["inputs"]
                if PurePosixPath(item["path"]).name == "heptactl")
            agent["sha256"] = "e" * 64
            with self.assertRaises(VERIFIER.AdmissionError) as caught:
                VERIFIER._validate_agent_os_binary_causal_binding(
                    evaluation, verification)
        self.assertEqual(
            caught.exception.reason,
            "ADMISSION_AGENT_OS_BINARY_CAUSAL_BINDING_INVALID")

    def test_network_gate_rejects_non_exact_inputs_and_identities(self):
        original = self.fixture.documents["network_gate_receipt"]
        for mutation in ("missing", "extra", "identity"):
            with self.subTest(mutation=mutation):
                document = copy.deepcopy(original)
                if mutation == "missing":
                    document["staged_inputs"].pop(next(iter(
                        document["staged_inputs"])))
                elif mutation == "extra":
                    document["staged_inputs"]["scripts/not-frozen.py"] = {
                        "sha256": "0" * 64, "size": 1, "mode": "0644"}
                else:
                    document["inner"]["identities"]["fixed_ib_uid"] = 9999
                with self.assertRaisesRegex(
                        VERIFIER.AdmissionError, "NETWORK_GATE_INVALID"):
                    VERIFIER.validate_network_gate(document)

    def test_real_strict_bundle_digest_domains_reach_admission(self):
        source_root = self.root / "strict-source-root"
        source_root.mkdir(mode=0o700)
        record_modes = {
            record["path"]: int(record["mode"], 8)
            for record in self.fixture.source_records
        }
        payloads = {
            path: (payload, record_modes[path])
            for path, payload in self.fixture.source_payloads.items()
        }
        payloads["README.md"] = (b"# strict source fixture\n", 0o644)
        baseline_relative = (
            "release-manifests/heptatrader-agent-os-v1.0.0-round114/"
            "manifest.json")
        payloads[baseline_relative] = (
            VERIFIER.pretty_baseline_bytes(
                self.fixture.documents["source_baseline"]),
            0o644,
        )
        for relative in SOURCE_VERIFIER.REDISTRIBUTABLE_VENDOR_METADATA:
            payloads.setdefault(
                relative, ((REPOSITORY / relative).read_bytes(), 0o644))
        for relative, (payload, mode) in payloads.items():
            destination = source_root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(payload)
            destination.chmod(mode)

        paths = [source_root / relative for relative in sorted(payloads)]
        captures = SOURCE_BUILDER.capture_sources(source_root, paths)
        security_manifest = copy.deepcopy(
            self.fixture.documents["source_baseline"]["source_manifest"])
        SOURCE_BUILDER.validate_security_manifest(
            security_manifest, captures)
        manifest = SOURCE_BUILDER.build_manifest(
            "1.0.0-round114", captures, security_manifest)
        output_root = self.root / "strict-source-output"
        output_root.mkdir(mode=0o700)
        bundle_path = output_root / "strict-source.tar"
        manifest_path = output_root / "strict-source.manifest.json"
        SOURCE_BUILDER.publish_bundle(
            bundle_path, manifest_path, manifest, captures, source_root)
        source_result = SOURCE_VERIFIER.verify_bundle(
            bundle_path, manifest_path)

        security_sha = source_result[
            "security_manifest_sha256"].removeprefix("sha256:")
        self.assertEqual(security_sha, self.fixture.source_sha.removeprefix(
            "sha256:"))
        self.assertNotEqual(
            security_sha, source_result["files_sha256"])

        release = self.fixture.documents["release_validation_receipt"]
        old_lineage = release["local_evidence"]["source_lineage"]
        release["local_evidence"]["source_lineage"] = (
            RELEASE_BUILDER._release_source_lineage(
                git_head=self.fixture.documents["source_baseline"]["git_head"],
                source_result=source_result,
                agent_result={
                    "bundle_sha256":
                        old_lineage["agent_source_bundle_sha256"]},
                runtime_result={
                    "package_sha256":
                        old_lineage["runtime_package_sha256"],
                    "manifest_sha256":
                        old_lineage["runtime_package_manifest_sha256"],
                }))

        native = self.fixture.documents["native_gate_receipt"]
        native_common = native["common_closure"]
        native_common["clean_source_bundle_sha256"] = source_result[
            "bundle_sha256"]
        native_common["clean_source_manifest_sha256"] = source_result[
            "manifest_sha256"]
        native_common["clean_source_files_sha256"] = source_result[
            "files_sha256"]
        self.fixture._rewrite("native_gate_receipt")
        native_binding = next(
            record for record in release["local_evidence"]["critical_files"]
            if record["role"] == "native-runtime-aggregate")
        native_binding["sha256"] = VERIFIER.digest_bytes(
            self.fixture.paths["native_gate_receipt"].read_bytes()).removeprefix(
                "sha256:")
        native_binding["size"] = self.fixture.paths[
            "native_gate_receipt"].stat().st_size
        self.fixture._rewrite("release_validation_receipt")

        receipt = self.fixture.evaluate().receipt
        self.assertEqual(receipt["status"], "GO")
        self.assertEqual(receipt["findings"], [])
        self.assertEqual(receipt["source_baseline_sha256"],
                         self.fixture.source_sha)

        release_lineage = release["local_evidence"]["source_lineage"]
        release_lineage["strict_source_security_manifest_sha256"] = (
            source_result["files_sha256"])
        self.fixture._rewrite("release_validation_receipt")
        cross_wired = self.fixture.evaluate().receipt
        self.assertEqual(cross_wired["status"], "HALT")
        self.assertEqual(cross_wired["findings"], [
            "RELEASE_VALIDATION_SOURCE_BINDING_MISMATCH",
            "SOURCE_LINEAGE_MISMATCH",
        ])

        release_lineage["strict_source_security_manifest_sha256"] = (
            security_sha)
        native_common["clean_source_files_sha256"] = VERIFIER.digest_bytes(
            b"drifted-native-full-file-closure").removeprefix("sha256:")
        self.fixture._rewrite("native_gate_receipt")
        native_binding["sha256"] = VERIFIER.digest_bytes(
            self.fixture.paths["native_gate_receipt"].read_bytes()).removeprefix(
                "sha256:")
        native_binding["size"] = self.fixture.paths[
            "native_gate_receipt"].stat().st_size
        self.fixture._rewrite("release_validation_receipt")
        native_drift = self.fixture.evaluate().receipt
        self.assertEqual(native_drift["status"], "HALT")
        self.assertEqual(native_drift["findings"], [
            "RELEASE_VALIDATION_SOURCE_BINDING_MISMATCH",
        ])

    def test_atomic_noreplace_publish_and_post_verify(self):
        evaluation = self.fixture.evaluate()
        output = self.root / "candidate.json"
        file_sha = VERIFIER.publish_candidate(
            evaluation, output, expected_uid=self.fixture.uid)
        self.assertEqual(file_sha, VERIFIER.digest_bytes(output.read_bytes()))
        self.assertEqual(output.stat().st_mode & 0o777, 0o600)
        parsed = VERIFIER.strict_object(
            output.read_bytes(), "TEST_OUTPUT_INVALID")
        self.assertEqual(parsed, evaluation.receipt)
        with self.assertRaisesRegex(
                VERIFIER.AdmissionError, "ADMISSION_OUTPUT_ALREADY_EXISTS"):
            VERIFIER.publish_candidate(
                evaluation, output, expected_uid=self.fixture.uid)

    def test_publish_or_resume_rejects_expired_candidate_before_publish(self):
        evaluation = self.fixture.evaluate()
        output = self.root / "expired-candidate.json"
        with (
            mock.patch.object(VERIFIER, "ROOT_UID", self.fixture.uid),
            mock.patch.object(
                VERIFIER, "_wall_clock_ms",
                return_value=evaluation.receipt["expires_at_ms"]),
            self.assertRaises(VERIFIER.AdmissionError) as caught,
        ):
            VERIFIER._publish_or_resume_candidate(
                evaluation, output,
                now_ms=evaluation.receipt["expires_at_ms"])
        self.assertEqual(
            caught.exception.reason, "ADMISSION_CANDIDATE_NOT_CURRENT")
        self.assertFalse(output.exists())

    def test_publish_or_resume_reopens_the_exact_current_candidate(self):
        evaluation = self.fixture.evaluate()
        output = self.root / "resume-candidate.json"
        VERIFIER.publish_candidate(
            evaluation, output, expected_uid=self.fixture.uid)
        with (
            mock.patch.object(VERIFIER, "ROOT_UID", self.fixture.uid),
            mock.patch.object(
                VERIFIER, "_wall_clock_ms",
                return_value=evaluation.receipt["evaluated_at_ms"] + 1),
        ):
            resumed = VERIFIER._publish_or_resume_candidate(
                evaluation, output,
                now_ms=evaluation.receipt["evaluated_at_ms"] + 1)
        self.assertEqual(resumed.receipt, evaluation.receipt)
        self.assertEqual(
            output.read_bytes(), VERIFIER.canonical_bytes(evaluation.receipt))

    def test_owner_absent_terminal_must_bind_exact_candidate_generation(self):
        evaluation = self.fixture.evaluate()
        zero = self.fixture.documents["zero_exposure_receipt"]
        terminal = {
            "status": "ADMISSION_GO",
            "reservation_reference": zero["host_authority_reservation"],
            "reservation_id": zero["reservation_id"],
            "reservation_generation": zero["reservation_generation"],
            "predecessor_finalization_body_sha256":
                zero["reservation_predecessor_finalization_body_sha256"],
            "prior_finalization_pointer_reference":
                zero["reservation_prior_finalization_pointer_reference"],
            "host_authority_lease": zero["host_authority_lease"],
        }
        session = mock.Mock()
        session.finalized = True
        session.tombstone.document = terminal
        session.tombstone.path = Path(
            zero["reservation_finalization_tombstone_path"])
        session.pointer.path = Path(
            zero["reservation_finalization_current_pointer_path"])
        session.lease.reference = zero["host_authority_lease"]

        VERIFIER._validate_active_reservation_binding(
            evaluation, mock.Mock(), session)
        session.reopen.assert_called_once_with()

        terminal["reservation_generation"] += 1
        with self.assertRaises(VERIFIER.AdmissionError) as caught:
            VERIFIER._validate_active_reservation_binding(
                evaluation, mock.Mock(), session)
        self.assertEqual(
            caught.exception.reason,
            "ADMISSION_ZERO_RESERVATION_BINDING_INVALID")

    def test_world_writable_evidence_parent_is_halt(self):
        path = self.fixture.paths["zero_exposure_receipt"]
        original_mode = stat.S_IMODE(path.parent.stat().st_mode)
        path.parent.chmod(0o777)
        try:
            receipt = self.fixture.evaluate().receipt
        finally:
            path.parent.chmod(original_mode)
        self.assertEqual(receipt["status"], "HALT")
        self.assertTrue(any(
            finding.endswith("_ADMISSION_INPUT_PARENT_UNTRUSTED")
            for finding in receipt["findings"]))

    def test_duplicate_key_is_halt(self):
        name = "rootful_gate_receipt"
        raw = self.fixture.paths[name].read_bytes()
        self.fixture.paths[name].write_bytes(b'{"schema":"duplicate",' + raw[1:])
        evaluation = self.fixture.evaluate()
        self.assertEqual(evaluation.receipt["status"], "HALT")
        self.assertTrue(any("JSON_INVALID" in item
                            for item in evaluation.receipt["findings"]))
        self.assertFalse(evaluation.receipt["paper_test_admission_candidate"])

    def test_noncanonical_json_is_halt(self):
        path = self.fixture.paths["native_gate_receipt"]
        path.write_bytes(b" " + path.read_bytes())
        evaluation = self.fixture.evaluate()
        self.assertEqual(evaluation.receipt["status"], "HALT")
        self.assertTrue(any("NOT_CANONICAL" in item
                            for item in evaluation.receipt["findings"]))

    def test_missing_input_is_no_go(self):
        self.fixture.paths["network_gate_receipt"].unlink()
        receipt = self.fixture.evaluate().receipt
        self.assertEqual(receipt["status"], "NO_GO")
        binding = receipt["input_bindings"]["network_gate_receipt"]
        self.assertIsNone(binding["file_sha256"])
        self.assertIsNone(binding["body_sha256"])

    def test_missing_hard_network_gate_is_no_go(self):
        name = "hard_network_gate_receipt"
        self.fixture.paths[name].unlink()
        receipt = self.fixture.evaluate().receipt
        self.assertEqual(receipt["status"], "NO_GO")
        self.assertIn(
            "HARD_NETWORK_GATE_RECEIPT_ADMISSION_INPUT_MISSING",
            receipt["findings"])
        self.assertIsNone(receipt["input_bindings"][name]["file_sha256"])

    def test_actual_hard_runner_rehearsal_receipt_cannot_promote(self):
        name = "hard_network_gate_receipt"
        document = self.fixture.documents[name]
        document.update({
            "decision": "REHEARSAL_ONLY", "passed": False,
            "certification_ready": False,
            "execution_mode": "INJECTED_REHEARSAL",
        })
        document["environment_review_closure"] = None
        self.fixture._rewrite(name)
        receipt = self.fixture.evaluate().receipt
        self.assertEqual(receipt["status"], "NO_GO")
        self.assertIn("HARD_NETWORK_GATE_NOT_PASS", receipt["findings"])

    def test_actual_dual_runner_rehearsal_receipt_cannot_promote(self):
        name = "dual_domain_gate_receipt"
        document = self.fixture.documents[name]
        document.update({
            "decision": "REHEARSAL_ONLY", "passed": False,
            "certification_ready": False,
            "certification_blockers": list(VERIFIER.CERTIFICATION_BLOCKERS),
            "expires_at_ms": document["completed_at_ms"] + 5 * 60 * 1000,
        })
        document["certification"] = noncertifying_gate_evidence()
        document["environment_review_closure"] = None
        self.fixture._rewrite(name)
        receipt = self.fixture.evaluate().receipt
        self.assertEqual(receipt["status"], "NO_GO")
        self.assertIn("DUAL_DOMAIN_GATE_NOT_PASS", receipt["findings"])

    def test_actual_paper_runner_rehearsal_receipt_cannot_promote(self):
        name = "rootful_gate_receipt"
        document = self.fixture.documents[name]
        document.update({
            "decision": "REHEARSAL_ONLY", "passed": False,
            "certification_ready": False,
            "certification_blockers": list(VERIFIER.CERTIFICATION_BLOCKERS),
            "expires_at_ms": document["completed_at_ms"] + 60 * 60 * 1000,
        })
        document["lineage"]["expected_input_manifest_sha256"] = None
        document["lineage"]["expected_runner_sha256"] = None
        document["certification"] = noncertifying_gate_evidence()
        document["environment_review_closure"] = None
        self.fixture._rewrite(name)
        receipt = self.fixture.evaluate().receipt
        self.assertEqual(receipt["status"], "NO_GO")
        self.assertIn("ROOTFUL_GATE_NOT_PASS", receipt["findings"])

    def test_expired_evidence_is_no_go(self):
        name = "zero_exposure_receipt"
        self.fixture.documents[name]["expires_at_ms"] = self.now_ms
        self.fixture._rewrite(name)
        receipt = self.fixture.evaluate().receipt
        self.assertEqual(receipt["status"], "NO_GO")
        self.assertIn("ZERO_EXPOSURE_RECEIPT_EXPIRED", receipt["findings"])

    def test_expired_hard_network_certification_is_no_go(self):
        name = "hard_network_gate_receipt"
        document = self.fixture.documents[name]
        document["expires_at_ms"] = self.now_ms
        for record in document["provenance"].values():
            record["expires_at_ms"] = self.now_ms
        self.fixture._rewrite(name)
        receipt = self.fixture.evaluate().receipt
        self.assertEqual(receipt["status"], "NO_GO")
        self.assertIn("HARD_NETWORK_GATE_RECEIPT_EXPIRED", receipt["findings"])

    def test_hard_gate_body_tamper_is_halt(self):
        name = "hard_network_gate_receipt"
        self.fixture.documents[name]["body_sha256"] = "sha256:" + "0" * 64
        self.fixture._write(name, self.fixture.documents[name])
        receipt = self.fixture.evaluate().receipt
        self.assertEqual(receipt["status"], "HALT")
        self.assertTrue(any(
            "HARD_NETWORK_GATE_INVALID" in finding
            for finding in receipt["findings"]))

    def test_gate_extra_field_tamper_is_halt(self):
        name = "rootful_gate_receipt"
        self.fixture.documents[name]["reviewed"] = True
        self.fixture._rewrite(name)
        receipt = self.fixture.evaluate().receipt
        self.assertEqual(receipt["status"], "HALT")
        self.assertTrue(any(
            "ROOTFUL_GATE_INVALID" in finding
            for finding in receipt["findings"]))

    def test_gate_provenance_tamper_is_halt(self):
        name = "dual_domain_gate_receipt"
        self.fixture.documents[name]["certification"]["provenance"][
            "builder"]["buildx_binary_sha256"] = VERIFIER.digest_bytes(
                b"tampered-buildx")
        self.fixture._rewrite(name)
        receipt = self.fixture.evaluate().receipt
        self.assertEqual(receipt["status"], "HALT")
        self.assertTrue(any(
            "DUAL_DOMAIN_GATE_INVALID" in finding
            for finding in receipt["findings"]))

    def test_hard_gate_source_binding_tamper_is_halt(self):
        name = "hard_network_gate_receipt"
        self.fixture.documents[name]["lineage"]["runner_sha256"] = "f" * 64
        self.fixture._rewrite(name)
        receipt = self.fixture.evaluate().receipt
        self.assertEqual(receipt["status"], "HALT")
        self.assertIn(
            "HARD_NETWORK_GATE_SOURCE_BINDING_MISMATCH",
            receipt["findings"])

    def test_hard_gate_authority_tamper_is_halt(self):
        name = "hard_network_gate_receipt"
        self.fixture.documents[name]["paper_authorized"] = True
        self.fixture._rewrite(name)
        receipt = self.fixture.evaluate().receipt
        self.assertEqual(receipt["status"], "HALT")
        self.assertTrue(any(
            "HARD_NETWORK_GATE_INVALID" in finding
            for finding in receipt["findings"]))

    def test_all_timed_gate_duration_drift_is_halt(self):
        names = (
            "dual_domain_gate_receipt", "rootful_gate_receipt",
            "hard_network_gate_receipt",
        )
        for name in names:
            with self.subTest(name=name), tempfile.TemporaryDirectory(
                    prefix="hepta-admission-duration-") as directory:
                fixture = EvidenceFixture(Path(directory), self.now_ms)
                fixture.documents[name]["duration_ms"] += 1
                fixture._rewrite(name)
                receipt = fixture.evaluate().receipt
                self.assertEqual(receipt["status"], "HALT")
                self.assertTrue(any(
                    f"{name.upper()}_" in finding and
                    "INVALID" in finding
                    for finding in receipt["findings"]))

    def test_legacy_or_nonproduction_watch_handoff_never_goes(self):
        name = "watch_handoff_receipt"
        for field in ("producer", "production_mode"):
            fixture = EvidenceFixture(self.root, self.now_ms)
            fixture.documents[name].pop(field)
            fixture._rewrite(name)
            receipt = fixture.evaluate().receipt
            self.assertNotEqual(receipt["status"], "GO")
            self.assertTrue(any(
                "WATCH_HANDOFF_RECEIPT_INVALID" in finding
                for finding in receipt["findings"]))

    def test_watch_handoff_producer_must_match_frozen_source(self):
        handoff_name = "watch_handoff_receipt"
        self.fixture.documents[handoff_name]["producer"][
            "file_sha256"] = VERIFIER.digest_bytes(b"other-producer")
        self.fixture._rewrite(handoff_name)
        exposure_name = "zero_exposure_receipt"
        self.fixture.documents[exposure_name]["watch_handoff_receipt"] = (
            self.fixture.reference(handoff_name))
        self.fixture._rewrite(exposure_name)
        receipt = self.fixture.evaluate().receipt
        self.assertEqual(receipt["status"], "HALT")
        self.assertIn(
            "WATCH_HANDOFF_SOURCE_BINDING_MISMATCH", receipt["findings"])

    def test_old_paper_v1_and_legacy_dual_shapes_never_go(self):
        paper = self.fixture.documents["rootful_gate_receipt"]
        paper["schema"] = "hepta.paper-domain-rootful-systemd-gate.v1"
        self.fixture._rewrite("rootful_gate_receipt")
        receipt = self.fixture.evaluate().receipt
        self.assertNotEqual(receipt["status"], "GO")

        fixture = EvidenceFixture(self.root, self.now_ms)
        dual = fixture.documents["dual_domain_gate_receipt"]
        for field in (
                "run_id", "started_at_ms", "completed_at_ms", "expires_at_ms",
                "body_sha256", "certification", "certification_ready",
                "certification_blockers", "disposable_cleanup",
                "paper_test_admission_candidate", "paper_authorized",
                "order_submission_authorized"):
            dual.pop(field, None)
        fixture._write("dual_domain_gate_receipt", dual)
        receipt = fixture.evaluate().receipt
        self.assertNotEqual(receipt["status"], "GO")

    def test_stale_zero_exposure_is_no_go(self):
        name = "zero_exposure_receipt"
        self.fixture.documents[name]["observed_at_ms"] = (
            self.now_ms - VERIFIER.MAXIMUM_EXPOSURE_AGE_MS - 1)
        self.fixture._rewrite(name)
        receipt = self.fixture.evaluate().receipt
        self.assertEqual(receipt["status"], "NO_GO")
        self.assertIn("ZERO_EXPOSURE_RECEIPT_STALE", receipt["findings"])

    def test_authority_signal_is_halt(self):
        name = "zero_exposure_receipt"
        self.fixture.documents[name]["paper_authorized"] = True
        self.fixture._rewrite(name)
        receipt = self.fixture.evaluate().receipt
        self.assertEqual(receipt["status"], "HALT")
        self.assertIn(
            "ZERO_EXPOSURE_RECEIPT_PAPER_AUTHORIZED_DANGEROUS",
            receipt["findings"])
        self.assertIs(receipt["paper_authorized"], False)

    def test_broker_or_credential_exposure_is_halt(self):
        name = "zero_exposure_receipt"
        self.fixture.documents[name]["credential_exposure_count"] = 1
        self.fixture._rewrite(name)
        receipt = self.fixture.evaluate().receipt
        self.assertEqual(receipt["status"], "HALT")
        self.assertIn("ZERO_EXPOSURE_DANGEROUS_SIGNAL", receipt["findings"])

    def test_upstream_zero_exposure_halt_is_preserved(self):
        name = "zero_exposure_receipt"
        self.fixture.documents[name]["status"] = "HALT"
        self.fixture._rewrite(name)
        receipt = self.fixture.evaluate().receipt
        self.assertEqual(receipt["status"], "HALT")
        self.assertIn("ZERO_EXPOSURE_UPSTREAM_HALT", receipt["findings"])

    def test_upstream_p1_audit_halt_is_preserved(self):
        name = "p1_audit_receipt"
        self.fixture.documents[name]["verdict"] = "HALT"
        self.fixture._rewrite(name)
        handoff_name = "watch_handoff_receipt"
        self.fixture.documents[handoff_name]["p1_audit_receipt"] = (
            self.fixture.reference(name))
        self.fixture._rewrite(handoff_name)
        exposure_name = "zero_exposure_receipt"
        self.fixture.documents[exposure_name]["watch_handoff_receipt"] = (
            self.fixture.reference(handoff_name))
        self.fixture._rewrite(exposure_name)
        receipt = self.fixture.evaluate().receipt
        self.assertEqual(receipt["status"], "HALT")
        self.assertIn("P1_AUDIT_UPSTREAM_HALT", receipt["findings"])

    def test_non_authoritative_or_incomplete_account_is_no_go(self):
        name = "zero_exposure_receipt"
        for field in ("authoritative", "account_complete"):
            with self.subTest(field=field):
                fixture = EvidenceFixture(self.root, self.now_ms)
                fixture.documents[name][field] = False
                fixture._rewrite(name)
                receipt = fixture.evaluate().receipt
                self.assertEqual(receipt["status"], "NO_GO")
                self.assertIn("ZERO_EXPOSURE_NOT_PASS", receipt["findings"])

    def test_position_or_gross_exposure_is_halt(self):
        name = "zero_exposure_receipt"
        self.fixture.documents[name]["position_count"] = 1
        self.fixture.documents[name]["gross_absolute_position"] = 7
        self.fixture.documents[name]["end_flat"] = False
        self.fixture._rewrite(name)
        receipt = self.fixture.evaluate().receipt
        self.assertEqual(receipt["status"], "HALT")
        self.assertIn("ZERO_EXPOSURE_DANGEROUS_SIGNAL", receipt["findings"])

    def test_campaign_lineage_mismatch_is_halt(self):
        name = "watch_handoff_receipt"
        self.fixture.documents[name]["campaign_id"] = "other-campaign"
        self.fixture._rewrite(name)
        receipt = self.fixture.evaluate().receipt
        self.assertEqual(receipt["status"], "HALT")
        self.assertIn("CAMPAIGN_LINEAGE_MISMATCH", receipt["findings"])

    def test_reference_digest_mismatch_is_halt(self):
        name = "watch_handoff_receipt"
        self.fixture.documents[name]["p1_audit_receipt"]["file_sha256"] = (
            "sha256:" + "f" * 64)
        self.fixture._rewrite(name)
        receipt = self.fixture.evaluate().receipt
        self.assertEqual(receipt["status"], "HALT")
        self.assertIn(
            "WATCH_HANDOFF_BINDING_MISMATCH", receipt["findings"])

    def test_invalid_body_digest_is_halt(self):
        name = "p1_audit_receipt"
        self.fixture.documents[name]["body_sha256"] = "sha256:" + "0" * 64
        self.fixture._write(name, self.fixture.documents[name])
        receipt = self.fixture.evaluate().receipt
        self.assertEqual(receipt["status"], "HALT")
        self.assertTrue(any("P1_AUDIT_RECEIPT_INVALID" in item
                            for item in receipt["findings"]))

    def test_secure_reopen_rejects_same_bytes_on_new_inode(self):
        evaluation = self.fixture.evaluate()
        target = self.fixture.paths["native_gate_receipt"]
        replacement = self.root / "replacement.json"
        replacement.write_bytes(target.read_bytes())
        replacement.chmod(0o600)
        os.replace(replacement, target)
        with self.assertRaisesRegex(
                VERIFIER.AdmissionError,
                "NATIVE_GATE_RECEIPT_SECURE_REOPEN_MISMATCH"):
            VERIFIER.assert_inputs_unchanged(
                evaluation.snapshots, expected_uid=self.fixture.uid)

    def test_dirty_source_baseline_is_no_go(self):
        name = "source_baseline"
        self.fixture.documents[name]["clean_checkout_certified"] = False
        self.fixture.documents[name]["worktree_status_entry_count"] = 1
        self.fixture.documents[name]["blocked_reason"] = (
            "VERSION_CONTROL_COMMIT_REQUIRED")
        self.fixture._rewrite(name, sealed=False)
        release_name = "release_validation_receipt"
        release = self.fixture.documents[release_name]
        binding = release["local_evidence"]["source_baseline"]
        binding["sha256"] = VERIFIER.digest_bytes(
            self.fixture.paths[name].read_bytes()).removeprefix("sha256:")
        binding["size"] = self.fixture.paths[name].stat().st_size
        critical = release["local_evidence"]["critical_files"]
        source_record = next(
            item for item in critical
            if item["role"] == "source-baseline-manifest")
        source_record.update(binding)
        self.fixture._rewrite(release_name, sealed=False)
        receipt = self.fixture.evaluate().receipt
        self.assertEqual(receipt["status"], "NO_GO")
        self.assertIn("SOURCE_BASELINE_NOT_CLEAN_FROZEN", receipt["findings"])

    def test_candidate_expiry_closes_exactly_at_issued_age_boundary(self):
        name = "source_baseline"
        issued_at = self.now_ms - VERIFIER.MAXIMUM_STATIC_AGE_MS + 1
        self.fixture.documents[name]["generated_at"] = datetime.fromtimestamp(
            issued_at / 1000, tz=timezone.utc).isoformat()
        self.fixture._rewrite(name, sealed=False)
        release_name = "release_validation_receipt"
        release = self.fixture.documents[release_name]
        binding = release["local_evidence"]["source_baseline"]
        binding["sha256"] = VERIFIER.digest_bytes(
            self.fixture.paths[name].read_bytes()).removeprefix("sha256:")
        binding["size"] = self.fixture.paths[name].stat().st_size
        next(item for item in release["local_evidence"]["critical_files"]
             if item["role"] == "source-baseline-manifest").update(binding)
        self.fixture._rewrite(release_name, sealed=False)

        current = self.fixture.evaluate().receipt
        self.assertEqual(current["status"], "GO")
        self.assertEqual(
            current["expires_at_ms"],
            issued_at + VERIFIER.MAXIMUM_STATIC_AGE_MS)
        with self.fixture.verifier_runtime_layout(), mock.patch.object(
                VERIFIER, "_reverify_native_gate_evidence",
                return_value=None):
            expired = VERIFIER.evaluate_candidate(
                self.fixture.paths, expected_domain=self.fixture.domain,
                expected_campaign=self.fixture.campaign,
                expected_uid=self.fixture.uid,
                now_ms=issued_at + VERIFIER.MAXIMUM_STATIC_AGE_MS).receipt
        self.assertEqual(expired["status"], "NO_GO")
        self.assertIn("SOURCE_BASELINE_STALE", expired["findings"])

    def test_strictly_greater_than_99_percent_p1_is_accepted(self):
        audit_name = "p1_audit_receipt"
        audit = self.fixture.documents[audit_name]
        audit["counts"]["complete_eligible_decisions"] = 199
        audit["counts"]["incomplete_eligible_decisions"] = 1
        audit["completeness"].update({
            "numerator": 199, "denominator": 200, "ppm": 995_000,
            "strictly_greater_than_99_percent": True,
        })
        self.fixture._rewrite(audit_name)
        handoff_name = "watch_handoff_receipt"
        self.fixture.documents[handoff_name]["p1_audit_receipt"] = (
            self.fixture.reference(audit_name))
        self.fixture._rewrite(handoff_name)
        exposure_name = "zero_exposure_receipt"
        self.fixture.documents[exposure_name]["watch_handoff_receipt"] = (
            self.fixture.reference(handoff_name))
        self.fixture._rewrite(exposure_name)
        receipt = self.fixture.evaluate().receipt
        self.assertEqual(receipt["status"], "GO")
        self.assertTrue(receipt["paper_test_admission_candidate"])

    def test_incomplete_p1_audit_cannot_become_go(self):
        name = "p1_audit_receipt"
        self.fixture.documents[name]["counts"]["eligible_decisions"] = 199
        self.fixture._rewrite(name)
        # Gate references now point at the previous audit file identity.  Even
        # if an operator forgot to regenerate them, lineage closes with HALT.
        receipt = self.fixture.evaluate().receipt
        self.assertEqual(receipt["status"], "HALT")
        self.assertIn("P1_AUDIT_NOT_GO", receipt["findings"])
        self.assertIn("WATCH_HANDOFF_BINDING_MISMATCH", receipt["findings"])

    def test_output_tamper_is_rejected(self):
        receipt = self.fixture.evaluate().receipt
        receipt["paper_authorized"] = True
        with self.assertRaises(VERIFIER.AdmissionError):
            VERIFIER.validate_output_receipt(receipt)

    def test_go_output_requires_a_nonzero_audited_strategy_digest(self):
        receipt = self.fixture.evaluate().receipt
        body = dict(receipt)
        body.pop("body_sha256")
        body.pop("strategy_sha256")
        with self.assertRaisesRegex(
                VERIFIER.AdmissionError,
                "ADMISSION_OUTPUT_RECEIPT_INVALID"):
            VERIFIER.validate_output_receipt(VERIFIER.seal(body))

        body = dict(receipt)
        body.pop("body_sha256")
        body["strategy_sha256"] = "sha256:" + "0" * 64
        with self.assertRaisesRegex(
                VERIFIER.AdmissionError,
                "ADMISSION_OUTPUT_RECEIPT_INVALID"):
            VERIFIER.validate_output_receipt(VERIFIER.seal(body))


if __name__ == "__main__":
    unittest.main()
