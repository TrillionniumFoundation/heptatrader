#!/usr/bin/env python3

from __future__ import annotations

import base64
import copy
from contextlib import contextmanager
import errno
import fcntl
import importlib.util
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


MODULE_PATH = (
    Path(__file__).resolve().parents[1] / "scripts" /
    "hepta_p1_paper_zero_exposure_snapshot_producer.py")
HANDOFF_MODULE_PATH = (
    Path(__file__).resolve().parents[1] / "scripts" /
    "hepta_p1_watch_to_paper_handoff.py")
SPEC = importlib.util.spec_from_file_location(
    "hepta_p1_paper_zero_exposure_snapshot_producer", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
PRODUCER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = PRODUCER
SPEC.loader.exec_module(PRODUCER)
HANDOFF_SPEC = importlib.util.spec_from_file_location(
    "hepta_p1_watch_to_paper_handoff_contract", HANDOFF_MODULE_PATH)
assert HANDOFF_SPEC is not None and HANDOFF_SPEC.loader is not None
HANDOFF = importlib.util.module_from_spec(HANDOFF_SPEC)
sys.modules[HANDOFF_SPEC.name] = HANDOFF
HANDOFF_SPEC.loader.exec_module(HANDOFF)


class ReferenceBinding:
    def __init__(self, path: Path, payload: bytes):
        self.path = path
        self.payload = payload

    @property
    def reference(self) -> dict[str, str]:
        return {
            "path": str(self.path),
            "file_sha256": PRODUCER.digest_bytes(self.payload),
        }

    def reopen(self) -> None:
        return None


class EvidenceFixture:
    def __init__(self, root: Path, now_ms: int):
        self.root = root
        self.now_ms = now_ms
        self.uid = os.geteuid()
        self.gid = os.getegid()
        self.source = PRODUCER.digest_bytes(b"frozen-round114-source")
        self.campaign = "p1-round114-campaign-a"
        self.account = PRODUCER.digest_bytes(b"paper-account-a")
        self.paths = {
            "intent": root / "operator-intent.json",
            "handoff": root / "watch-handoff.json",
            "challenge": root / "challenge.json",
            "signed_evidence": root / "signed-evidence.json",
            "broker_output": root / "broker-snapshot.json",
            "account_output": root / "account-snapshot.json",
        }
        self.context = PRODUCER.ProductionContext.__new__(
            PRODUCER.ProductionContext)
        self.context.expected_uid = self.uid
        self.context.expected_gid = self.gid
        self.context.producer = ReferenceBinding(
            root / "installed-producer", b"installed producer")
        self.context.broker_helper = ReferenceBinding(
            root / "broker-helper", b"broker helper")
        self.context.signature_verifier = ReferenceBinding(
            Path("/usr/bin/openssl"), Path("/usr/bin/openssl").read_bytes())
        self.context.verification_key = ReferenceBinding(
            root / "reviewed-public-key", b"reviewed public key")
        self.context._certification_secret = object()
        self.context._lease_certification_secret = object()
        self.authority_directory = root / "ib-paper-host-authority"
        self.authority_directory.mkdir(mode=0o700, exist_ok=True)
        self.authority_directory.chmod(0o700)
        self.authority_lease = self.authority_directory / "lease.lock"
        self.authority_lease.write_bytes(b"")
        self.authority_lease.chmod(0o600)
        self.authority_owner = self.authority_directory / "owner.v1"
        self.boot_id_path = root / "boot_id"
        self.boot_id_path.write_text(
            "11111111-2222-4333-8444-555555555555\n", encoding="ascii")
        self.boot_id_path.chmod(0o444)
        self.documents: dict[str, dict] = {}
        self._install_profile_fixture()
        self._build()

    @staticmethod
    def boundary() -> dict[str, bool]:
        return {
            "paper_authorized": False, "live_authorized": False,
            "mutation_authorized": False, "direct_broker_access": False,
            "order_submission_authorized": False,
        }

    def lineage(self) -> dict:
        return {
            "round": 114, "domain": "alpha", "campaign_id": self.campaign,
            "source_baseline_sha256": self.source,
        }

    def generic_reference(self, name: str) -> dict[str, str]:
        return {
            "path": str(self.root / (name + ".json")),
            "file_sha256": PRODUCER.digest_bytes((name + "-file").encode()),
            "body_sha256": PRODUCER.digest_bytes((name + "-body").encode()),
        }

    def _profile_record(self, path: Path, *, sealed: bool = False) -> dict:
        metadata = path.stat()
        payload = path.read_bytes()
        value = {
            "path": str(path), "file_sha256": PRODUCER.digest_bytes(payload),
            "bytes": len(payload), "mode": metadata.st_mode,
            "uid": metadata.st_uid, "gid": metadata.st_gid,
            "nlink": metadata.st_nlink, "device": metadata.st_dev,
            "inode": metadata.st_ino, "mtime_ns": metadata.st_mtime_ns,
            "ctime_ns": metadata.st_ctime_ns,
        }
        if sealed:
            value["body_sha256"] = PRODUCER.strict_object(
                payload, "FIXTURE_INVALID")["body_sha256"]
        return value

    def _legacy_profile_record(self, path: Path) -> dict:
        record = self._profile_record(path)
        record["sha256"] = record.pop("file_sha256")
        return record

    def _write_profile_document(self, path: Path, document: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.parent.chmod(0o700)
        path.write_bytes(PRODUCER.canonical_bytes(PRODUCER.seal(document)))
        path.chmod(0o600)

    def _install_profile_fixture(self) -> None:
        dormant = b"D" * PRODUCER.PAPER_PROFILE_DORMANT_BYTES
        watch = b"W" * PRODUCER.PAPER_PROFILE_WATCH_BYTES
        hardened_runtime = b"P" * PRODUCER.PAPER_RUNTIME_PROFILE_HARDENED_BYTES
        legacy_runtime = b"L" * PRODUCER.PAPER_RUNTIME_PROFILE_LEGACY_BYTES
        identity = b'{"identities":[]}\n'
        PRODUCER.PAPER_PROFILE_PATH = self.root / "trust" / "alpha.env"
        PRODUCER.PAPER_PROFILE_DORMANT_BACKUP_PATH = (
            self.root / "profile-backup" / "alpha.env")
        PRODUCER.PAPER_PROFILE_FORWARD_RETAINED_PATH = (
            self.root / "trust" / ".alpha.retained")
        PRODUCER.PAPER_PROFILE_FORWARD_PREIMAGE_PATH = (
            self.root / "profile-backup" / "preimage-evidence.json")
        PRODUCER.PAPER_PROFILE_FORWARD_TRANSITION_RECEIPT_PATH = (
            self.root / "profile-receipts" / "transition.json")
        PRODUCER.PAPER_PROFILE_DEPLOYMENT_RECEIPT_PATH = (
            self.root / "profile-receipts" / "deployment.json")
        PRODUCER.PAPER_PROFILE_CANDIDATE_PATH = (
            self.root / "trust" / ".alpha.candidate")
        PRODUCER.PAPER_PROFILE_RETIRED_WATCH_PATH = (
            self.root / "handoff-state" / "retired-watch.env")
        PRODUCER.PAPER_RUNTIME_PROFILE_PATH = (
            self.root / "trust" / "alpha.ib-paper.env")
        PRODUCER.PAPER_RUNTIME_PROFILE_CANDIDATE_PATH = (
            self.root / "trust" / ".alpha.ib-paper.candidate")
        PRODUCER.PAPER_RUNTIME_PROFILE_BACKUP_PATH = (
            self.root / "handoff-state" / "legacy-runtime-backup.env")
        PRODUCER.PAPER_RUNTIME_PROFILE_RETAINED_PATH = (
            self.root / "handoff-state" / "retained-legacy-runtime.env")
        PRODUCER.IDENTITY_MANIFEST_PATH = self.root / "identity.json"
        PRODUCER.KILL_SWITCH_PATH = self.root / "paper-kill-switch"
        PRODUCER.GLOBAL_KILL_SWITCH_PATH = self.root / "global-kill-switch"
        PRODUCER.PAPER_CONTROL_GID = self.gid
        PRODUCER.GLOBAL_PAPER_CONTROL_GID = self.gid
        PRODUCER.PAPER_PROFILE_DORMANT_SHA256 = PRODUCER.digest_bytes(dormant)
        PRODUCER.PAPER_PROFILE_WATCH_SHA256 = PRODUCER.digest_bytes(watch)
        PRODUCER.PAPER_RUNTIME_PROFILE_HARDENED_SHA256 = (
            PRODUCER.digest_bytes(hardened_runtime))
        PRODUCER.PAPER_RUNTIME_PROFILE_LEGACY_SHA256 = (
            PRODUCER.digest_bytes(legacy_runtime))
        PRODUCER.DISABLED_IDENTITY_MANIFEST_SHA256 = PRODUCER.digest_bytes(
            identity)
        for path, payload, mode in (
            (PRODUCER.PAPER_PROFILE_PATH, dormant, 0o644),
            (PRODUCER.PAPER_PROFILE_DORMANT_BACKUP_PATH, dormant, 0o600),
            (PRODUCER.PAPER_PROFILE_FORWARD_RETAINED_PATH, dormant, 0o600),
            (PRODUCER.PAPER_PROFILE_RETIRED_WATCH_PATH, watch, 0o600),
            (PRODUCER.PAPER_RUNTIME_PROFILE_PATH, hardened_runtime, 0o644),
            (PRODUCER.PAPER_RUNTIME_PROFILE_BACKUP_PATH, legacy_runtime,
             0o600),
            (PRODUCER.PAPER_RUNTIME_PROFILE_RETAINED_PATH, legacy_runtime,
             0o600),
            (PRODUCER.IDENTITY_MANIFEST_PATH, identity, 0o600),
            (PRODUCER.KILL_SWITCH_PATH, b"engaged", 0o440),
            (PRODUCER.GLOBAL_KILL_SWITCH_PATH, b"engaged", 0o440),
        ):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.parent.chmod(0o700)
            path.write_bytes(payload)
            path.chmod(mode)
        preimage = {
            field: None for field in PRODUCER.PROFILE_PREIMAGE_FIELDS
            if field != "body_sha256"
        }
        preimage.update({
            "schema": PRODUCER.PROFILE_PREIMAGE_SCHEMA, "version": 1,
            "status": PRODUCER.PROFILE_PREIMAGE_STATUS, "round": 114,
            "domain": "alpha", "transition_token": "round114-transition",
            "created_at_ms": self.now_ms - 50_000,
            "backup": self._legacy_profile_record(
                PRODUCER.PAPER_PROFILE_DORMANT_BACKUP_PATH),
            "paper_authorized": False, "live_authorized": False,
            "mutation_attempted": False, "direct_broker_access": False,
        })
        self._write_profile_document(
            PRODUCER.PAPER_PROFILE_FORWARD_PREIMAGE_PATH, preimage)
        transition = {
            field: None for field in PRODUCER.PROFILE_TRANSITION_FIELDS
            if field != "body_sha256"
        }
        transition.update({
            "schema": PRODUCER.PROFILE_TRANSITION_SCHEMA, "version": 2,
            "status": PRODUCER.PROFILE_TRANSITION_STATUS, "round": 114,
            "domain": "alpha", "transition_token": "round114-transition",
            "started_at_ms": self.now_ms - 45_000,
            "finished_at_ms": self.now_ms - 44_000,
            "target_path": str(PRODUCER.PAPER_PROFILE_PATH),
            "backup_path": str(PRODUCER.PAPER_PROFILE_DORMANT_BACKUP_PATH),
            "retained_target_path": str(
                PRODUCER.PAPER_PROFILE_FORWARD_RETAINED_PATH),
            "profile_content_changed": True, "target_written": True,
            "target_replaced": True, "services_started": False,
            "services_stopped": False, "services_restarted": False,
            "campaign_launched": False, "paper_authorized": False,
            "live_authorized": False, "mutation_attempted": False,
            "direct_broker_access": False,
            "backup": self._legacy_profile_record(
                PRODUCER.PAPER_PROFILE_DORMANT_BACKUP_PATH),
            "retained_target": self._legacy_profile_record(
                PRODUCER.PAPER_PROFILE_FORWARD_RETAINED_PATH),
        })
        self._write_profile_document(
            PRODUCER.PAPER_PROFILE_FORWARD_TRANSITION_RECEIPT_PATH,
            transition)
        transition_evidence = self._profile_record(
            PRODUCER.PAPER_PROFILE_FORWARD_TRANSITION_RECEIPT_PATH,
            sealed=True)
        deployment = {
            field: None for field in PRODUCER.PROFILE_DEPLOYMENT_FIELDS
            if field != "body_sha256"
        }
        deployment.update({
            "schema": PRODUCER.PROFILE_DEPLOYMENT_SCHEMA, "version": 8,
            "status": PRODUCER.PROFILE_DEPLOYMENT_STATUS, "round": 114,
            "domain": "alpha", "target_path": str(PRODUCER.PAPER_PROFILE_PATH),
            "dormant_paper_to_watch_transition_receipt": {
                **transition_evidence,
                "sha256": transition_evidence["file_sha256"],
            },
        })
        deployment["dormant_paper_to_watch_transition_receipt"].pop(
            "file_sha256")
        self._write_profile_document(
            PRODUCER.PAPER_PROFILE_DEPLOYMENT_RECEIPT_PATH, deployment)
        self.profile_restoration = {
            "schema": PRODUCER.PROFILE_RESTORATION_SCHEMA, "version": 1,
            "status": PRODUCER.PROFILE_RESTORATION_STATUS,
            "target": self._profile_record(PRODUCER.PAPER_PROFILE_PATH),
            "dormant_backup": self._profile_record(
                PRODUCER.PAPER_PROFILE_DORMANT_BACKUP_PATH),
            "forward_retained_dormant": self._profile_record(
                PRODUCER.PAPER_PROFILE_FORWARD_RETAINED_PATH),
            "retired_watch": self._profile_record(
                PRODUCER.PAPER_PROFILE_RETIRED_WATCH_PATH),
            "forward_transition_receipt": transition_evidence,
            "profile_deployment_receipt": self._profile_record(
                PRODUCER.PAPER_PROFILE_DEPLOYMENT_RECEIPT_PATH, sealed=True),
            "forward_preimage_evidence": self._profile_record(
                PRODUCER.PAPER_PROFILE_FORWARD_PREIMAGE_PATH, sealed=True),
            "candidate_path": str(PRODUCER.PAPER_PROFILE_CANDIDATE_PATH),
            "retired_watch_path": str(
                PRODUCER.PAPER_PROFILE_RETIRED_WATCH_PATH),
            "exchange_method": "RENAME_EXCHANGE",
            "forward_only_after_exchange": True,
            "restore_intent_record_sha256": PRODUCER.digest_bytes(b"intent"),
            "restore_exchange_record_sha256": PRODUCER.digest_bytes(
                b"exchange"),
        }
        self.runtime_profile_hardening = {
            "schema": PRODUCER.PAPER_RUNTIME_PROFILE_HARDENING_SCHEMA,
            "version": 1,
            "status": PRODUCER.PAPER_RUNTIME_PROFILE_HARDENING_STATUS,
            "target": self._profile_record(
                PRODUCER.PAPER_RUNTIME_PROFILE_PATH),
            "legacy_backup": self._profile_record(
                PRODUCER.PAPER_RUNTIME_PROFILE_BACKUP_PATH),
            "retained_legacy": self._profile_record(
                PRODUCER.PAPER_RUNTIME_PROFILE_RETAINED_PATH),
            "candidate_path": str(
                PRODUCER.PAPER_RUNTIME_PROFILE_CANDIDATE_PATH),
            "retained_legacy_path": str(
                PRODUCER.PAPER_RUNTIME_PROFILE_RETAINED_PATH),
            "exchange_method": "RENAME_EXCHANGE",
            "forward_only_after_exchange": True,
            "harden_intent_record_sha256": PRODUCER.digest_bytes(
                b"runtime-intent"),
            "harden_exchange_record_sha256": PRODUCER.digest_bytes(
                b"runtime-exchange"),
        }

    def write(self, name: str, document: dict, *, sealed: bool = True) -> None:
        value = PRODUCER.seal(document) if sealed else document
        self.paths[name].write_bytes(PRODUCER.canonical_bytes(value))
        self.paths[name].chmod(0o600)
        self.documents[name] = value

    def bind(self, name: str, fields, schema):
        return PRODUCER._bind_document(
            self.paths[name], fields, schema, "FIXTURE_INVALID",
            expected_uid=self.uid, expected_gid=self.gid)

    def rewrite_evidence(self) -> None:
        payload = self.documents["signed_evidence"]["payload"]
        payload["snapshot_sha256"] = PRODUCER.account_state_sha256(payload)
        self.write("signed_evidence", self.documents["signed_evidence"],
                   sealed=False)

    @contextmanager
    def authority_paths(self):
        with mock.patch.object(
                PRODUCER, "HOST_AUTHORITY_DIRECTORY",
                self.authority_directory), mock.patch.object(
                PRODUCER, "HOST_AUTHORITY_LEASE_PATH",
                self.authority_lease), mock.patch.object(
                PRODUCER, "HOST_AUTHORITY_OWNER_PATH",
                self.authority_owner), mock.patch.object(
                PRODUCER, "BOOT_ID_PATH", self.boot_id_path):
            yield

    def reservation_binding(self):
        return PRODUCER._bind_document(
            self.authority_owner, PRODUCER.RESERVATION_FIELDS,
            PRODUCER.RESERVATION_SCHEMA, "FIXTURE_INVALID",
            expected_uid=self.uid, expected_gid=self.gid)

    def _create_reservation(
        self, intent_binding, handoff_binding, *, nonce: str = "a" * 64,
        reservation_id: str | None = None,
    ):
        paths = PRODUCER._intent_paths(
            intent_binding.document, "FIXTURE_INVALID")
        with self.authority_paths():
            lease = self.context.acquire_host_authority_lease()
            reservation = None
            try:
                document = PRODUCER.build_reservation(
                    intent=intent_binding, handoff=handoff_binding,
                    context=self.context, lease=lease, paths=paths,
                    now_ms=self.now_ms, nonce=nonce,
                    reservation_id=reservation_id or
                        "zero-exposure-" + "1" * 48)
                PRODUCER._publish_one(
                    self.authority_owner, document, expected_uid=self.uid,
                    expected_gid=self.gid)
                reservation = self.reservation_binding()
                PRODUCER.validate_reservation(
                    reservation, self.now_ms, intent=intent_binding,
                    handoff=handoff_binding, context=self.context, lease=lease,
                    paths=paths)
                return reservation
            finally:
                self.context.release_host_authority_lease(lease, reservation)

    def _build(self) -> None:
        handoff = {
            "schema": PRODUCER.HANDOFF_SCHEMA,
            "version": PRODUCER.HANDOFF_VERSION,
            "status": "WATCH_RETIRED_HANDOFF_COMPLETE",
            "issued_at_ms": self.now_ms - 5_000,
            "expires_at_ms": self.now_ms + 180_000, **self.lineage(),
            "producer": self.context.producer.reference,
            "production_mode": "PRODUCTION_ROOT_SYSTEMD",
            "activation_receipt": self.generic_reference("activation"),
            "p1_audit_receipt": self.generic_reference("p1-audit"),
            "freeze_bundle": self.generic_reference("freeze-bundle"),
            "watch_units_inactive": True, "watch_authority_count": 0,
            "watch_socket_count": 0, "watch_timer_count": 0,
            "paper_units_inactive": True, "broker_deny_all": True,
            "kill_switch_engaged": True,
            "global_kill_switch_engaged": True, "identity_count": 0,
            "identity_manifest_sha256":
                PRODUCER.DISABLED_IDENTITY_MANIFEST_SHA256,
            "paper_profile_restored": True,
            "paper_profile_restoration": self.profile_restoration,
            "profile_candidate_absent": True,
            "paper_runtime_profile_hardened": True,
            "paper_runtime_profile_hardening": self.runtime_profile_hardening,
            "paper_runtime_profile_candidate_absent": True,
            "crash_recovery_verified": True,
            "cleanup_residue_count": 0, **self.boundary(),
        }
        self.write("handoff", handoff)
        intent = {
            "schema": PRODUCER.INTENT_SCHEMA, "version": 1,
            "status": "APPROVED", "issued_at_ms": self.now_ms - 2_000,
            "expires_at_ms": self.now_ms + 180_000, **self.lineage(),
            "intent_id": "zero-exposure-signed-account-a",
            "account_id_sha256": self.account,
            "production_mode": PRODUCER.PRODUCTION_MODE,
            "producer": self.context.producer.reference,
            "broker_policy_helper": self.context.broker_helper.reference,
            "signature_verifier": self.context.signature_verifier.reference,
            "verification_key": self.context.verification_key.reference,
            "watch_handoff_receipt_path": str(self.paths["handoff"]),
            "challenge_output_path": str(self.paths["challenge"]),
            "signed_account_evidence_path":
                str(self.paths["signed_evidence"]),
            "broker_snapshot_output_path": str(self.paths["broker_output"]),
            "account_snapshot_output_path": str(
                self.paths["account_output"]),
            "allow_fixed_read_only_host_observation": True,
            "allow_offline_signed_account_adaptation": True,
            **self.boundary(),
        }
        self.write("intent", intent)
        intent_binding = self.bind(
            "intent", PRODUCER.INTENT_FIELDS, PRODUCER.INTENT_SCHEMA)
        handoff_binding = self.bind(
            "handoff", PRODUCER.HANDOFF_FIELDS, PRODUCER.HANDOFF_SCHEMA)
        reservation = self._create_reservation(
            intent_binding, handoff_binding)
        with self.authority_paths():
            challenge = PRODUCER.build_challenge(
                intent=intent_binding, handoff=handoff_binding,
                reservation=reservation,
                context=self.context, now_ms=self.now_ms,
                nonce="a" * 64)
        self.write("challenge", challenge, sealed=False)
        payload = {
            "schema": PRODUCER.SIGNED_EVIDENCE_PAYLOAD_SCHEMA,
            "version": 1, "status": "COMPLETE",
            "observed_at_ms": self.now_ms,
            "expires_at_ms": self.now_ms + 120_000, **self.lineage(),
            "nonce": challenge["nonce"],
            "challenge_body_sha256": challenge["body_sha256"],
            "account_id_sha256": self.account,
            "provider_id": "reviewed-remote-account-authority-a",
            "provider_request_id_sha256":
                PRODUCER.digest_bytes(b"remote-request-a"),
            "provider_response_sha256":
                PRODUCER.digest_bytes(b"remote-response-a"),
            "observation_authority":
                PRODUCER.REMOTE_OBSERVATION_AUTHORITY,
            "query_effect": PRODUCER.REMOTE_QUERY_EFFECT,
            "query_epoch": "remote-query-epoch-a",
            "query_fencing_generation": 3,
            "query_invocation_id": "remote-query-invocation-a",
            "read_only_authority": True, "authoritative": True,
            "account_complete": True, "snapshot_sha256": "",
            "active_order_id_sha256s": [], "positions": [],
            "gross_absolute_position": 0,
            "authorized_connector_count": 0, "end_flat": True,
            **self.boundary(),
        }
        payload["snapshot_sha256"] = PRODUCER.account_state_sha256(payload)
        envelope = {
            "schema": PRODUCER.SIGNED_EVIDENCE_ENVELOPE_SCHEMA,
            "version": 1, "payload": payload,
            "signature_base64": base64.b64encode(b"s" * 64).decode("ascii"),
        }
        self.write("signed_evidence", envelope, sealed=False)

    def terminal_bundle(self) -> dict:
        """Build an in-memory/file-backed post-cutoff witness fixture."""

        def write_path(path: Path, document: dict) -> None:
            path.write_bytes(PRODUCER.canonical_bytes(document))
            path.chmod(0o600)

        if self.authority_owner.exists():
            self.authority_owner.unlink()
        owner_id = PRODUCER.digest_bytes(b"owner-a")
        owner_account = b"DU123"
        owner_domain = b"PAPER:alpha"
        owner_canonical = (
            owner_id + "\t1\t" + owner_account.hex() + "\t" +
            owner_domain.hex() + "\n").encode("ascii")
        cutoff_body = {
            "schema": PRODUCER.TRANSPORT_CUTOFF_SCHEMA, "version": 1,
            "status": PRODUCER.TERMINAL_CUTOFF_STATUS,
            "completed_at_ms": self.now_ms - 5_000,
            "completed_monotonic_ns": 1_000_000, "round": 114,
            "domain": "alpha", "campaign_id": self.campaign,
            "source_baseline_sha256": self.source, "cycle_id": "cycle-a",
            "recovery_id": "recovery-a", "finalization_id": "finalization-a",
            "boot_id": "11111111-2222-4333-8444-555555555555",
            "service_pid": 1234, "service_start_ticks": 5678,
            "broker_socket_identity_sha256": PRODUCER.digest_bytes(b"socket"),
            "account_id_sha256": PRODUCER.digest_bytes(owner_account),
            "owner_ids": [owner_id],
            "owner_set_sha256": PRODUCER.digest_bytes(owner_canonical),
            "owner_set_canonical_hex": owner_canonical.hex(), "owner_count": 1,
            "execution_service_epoch": "execution-epoch-a",
            "execution_service_fencing_generation": 17,
            "mutation_fence_generation": 19,
            "known_mutation_command_set_sha256":
                PRODUCER.digest_bytes(b"mutations"),
            "known_mutation_command_count": 1,
            "known_correlation_set_sha256":
                PRODUCER.digest_bytes(b"correlations"),
            "known_correlation_count": 1, "egress_policy_generation": 23,
            "egress_policy_sha256": PRODUCER.digest_bytes(b"deny-all"),
            "authorized_connectors": 0, "authorized_uids": [],
            "broker_socket_count": 0, "broker_process_count": 0,
            "credential_exposure_count": 0,
            "process_inventory_complete": True,
            "socket_inventory_complete": True,
            "credential_inventory_complete": True,
            "mutation_gate_closed": True, "reconnect_permitted": False,
            **self.boundary(),
        }
        trust_body = {
            "schema": PRODUCER.TERMINAL_PROVIDER_TRUST_POLICY_SCHEMA,
            "version": 1, "status": "ACTIVE",
            "provider_id": PRODUCER.TERMINAL_PROVIDER_ID,
            "provider_key_sha256":
                self.context.verification_key.reference["file_sha256"],
            "provider_capability": PRODUCER.TERMINAL_PROVIDER_CAPABILITY,
            "atomic_account_supported": True,
            "causal_watermark_supported": True,
            "challenge_bound_query_supported": True,
            "read_only_authority_required": True,
            "mutation_attempted": False, **self.boundary(),
        }
        paths = {
            "cutoff": self.root / "terminal-cutoff.json",
            "trust": self.root / "terminal-trust-policy.json",
            "challenge": self.root / "terminal-challenge.json",
            "evidence": self.root / "terminal-evidence.json",
            "request": self.root / "terminal-provider-request.bin",
            "response": self.root / "terminal-provider-response.bin",
        }
        for name, body, fields, schema in (
            ("cutoff", cutoff_body, PRODUCER.TRANSPORT_CUTOFF_FIELDS,
             PRODUCER.TRANSPORT_CUTOFF_SCHEMA),
            ("trust", trust_body, PRODUCER.TERMINAL_PROVIDER_TRUST_POLICY_FIELDS,
             PRODUCER.TERMINAL_PROVIDER_TRUST_POLICY_SCHEMA),
        ):
            document = PRODUCER.seal(body)
            write_path(paths[name], document)
            self.documents[name] = document
            PRODUCER._sealed(document, fields, schema, "FIXTURE_INVALID")
        PRODUCER.TERMINAL_PROVIDER_TRUST_POLICY_BODY_SHA256 = \
            self.documents["trust"]["body_sha256"]
        cutoff = PRODUCER._bind_document(
            paths["cutoff"], PRODUCER.TRANSPORT_CUTOFF_FIELDS,
            PRODUCER.TRANSPORT_CUTOFF_SCHEMA, "FIXTURE_INVALID",
            expected_uid=self.uid, expected_gid=self.gid)
        trust = PRODUCER._bind_document(
            paths["trust"], PRODUCER.TERMINAL_PROVIDER_TRUST_POLICY_FIELDS,
            PRODUCER.TERMINAL_PROVIDER_TRUST_POLICY_SCHEMA, "FIXTURE_INVALID",
            expected_uid=self.uid, expected_gid=self.gid)
        with self.authority_paths():
            lease = self.context.acquire_host_authority_lease()
            try:
                challenge_document = PRODUCER.build_terminal_challenge(
                    cutoff=cutoff, trust_policy=trust, context=self.context,
                    lease=lease, now_ms=self.now_ms - 4_000,
                    now_monotonic_ns=2_000_000, nonce="b" * 64)
            finally:
                self.context.release_host_authority_lease(lease)
        write_path(paths["challenge"], challenge_document)
        challenge = PRODUCER._bind_document(
            paths["challenge"], PRODUCER.TERMINAL_CHALLENGE_FIELDS,
            PRODUCER.TERMINAL_CHALLENGE_SCHEMA, "FIXTURE_INVALID",
            expected_uid=self.uid, expected_gid=self.gid)
        paths["request"].write_bytes(b"canonical provider request\n")
        paths["response"].write_bytes(b"canonical provider response\n")
        for path in (paths["request"], paths["response"]):
            path.chmod(0o600)
        request = PRODUCER._bind_terminal_provider_artifact(
            paths["request"], "FIXTURE_INVALID", expected_uid=self.uid,
            expected_gid=self.gid)
        response = PRODUCER._bind_terminal_provider_artifact(
            paths["response"], "FIXTURE_INVALID", expected_uid=self.uid,
            expected_gid=self.gid)
        payload = {
            "schema": PRODUCER.TERMINAL_SIGNED_EVIDENCE_SCHEMA, "version": 1,
            "status": PRODUCER.TERMINAL_SIGNED_EVIDENCE_STATUS,
            "query_started_at_ms": self.now_ms - 3_000,
            "query_started_monotonic_ns": 3_000_000,
            "observed_at_ms": self.now_ms - 2_000,
            "observed_monotonic_ns": 4_000_000,
            "query_completed_at_ms": self.now_ms - 1_000,
            "query_completed_monotonic_ns": 5_000_000,
            "expires_at_ms": self.now_ms + 60_000,
            "round": 114, "domain": "alpha", "campaign_id": self.campaign,
            "source_baseline_sha256": self.source, "cycle_id": "cycle-a",
            "recovery_id": "recovery-a", "finalization_id": "finalization-a",
            "nonce": challenge_document["nonce"],
            "challenge_body_sha256": challenge_document["body_sha256"],
            "transport_cutoff_body_sha256": cutoff.document["body_sha256"],
            "boot_id": cutoff.document["boot_id"],
            "service_pid": cutoff.document["service_pid"],
            "service_start_ticks": cutoff.document["service_start_ticks"],
            "broker_socket_identity_sha256":
                cutoff.document["broker_socket_identity_sha256"],
            "account_id_sha256": cutoff.document["account_id_sha256"],
            "owner_set_sha256": cutoff.document["owner_set_sha256"],
            "owner_set_canonical_hex":
                cutoff.document["owner_set_canonical_hex"],
            "owner_count": 1,
            "execution_service_epoch": "execution-epoch-a",
            "execution_service_fencing_generation": 17,
            "mutation_fence_generation": 19,
            "known_mutation_command_set_sha256":
                cutoff.document["known_mutation_command_set_sha256"],
            "known_mutation_command_count": 1,
            "known_correlation_set_sha256":
                cutoff.document["known_correlation_set_sha256"],
            "known_correlation_count": 1, "egress_policy_generation": 23,
            "egress_policy_sha256": cutoff.document["egress_policy_sha256"],
            "provider_id": PRODUCER.TERMINAL_PROVIDER_ID,
            "provider_trust_policy_sha256": trust.document["body_sha256"],
            "provider_key_sha256": trust.document["provider_key_sha256"],
            "provider_capability": PRODUCER.TERMINAL_PROVIDER_CAPABILITY,
            "provider_request_sha256": request.reference["file_sha256"],
            "provider_response_sha256": response.reference["file_sha256"],
            "observation_authority": PRODUCER.REMOTE_OBSERVATION_AUTHORITY,
            "query_effect": PRODUCER.REMOTE_QUERY_EFFECT,
            "query_epoch": "provider-query-epoch-a",
            "query_fencing_generation": 29,
            "query_invocation_id": "provider-query-a",
            "provider_clock_id": "provider-clock-a",
            "provider_boot_id": "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
            "query_started_after_challenge": True,
            "snapshot_consistency": "ATOMIC_ACCOUNT",
            "consistency_token_sha256": PRODUCER.digest_bytes(b"atomic-token"),
            "consistency_cutoff_body_sha256": cutoff.document["body_sha256"],
            "consistency_known_mutation_command_set_sha256":
                cutoff.document["known_mutation_command_set_sha256"],
            "consistency_known_correlation_set_sha256":
                cutoff.document["known_correlation_set_sha256"],
            "consistency_dominates_cutoff": True,
            "consistency_dominates_all_mutations": True,
            "active_orders_complete": True,
            "active_orders_end_marker_observed": True,
            "completed_orders_complete": True,
            "completed_orders_end_marker_observed": True,
            "executions_complete": True,
            "executions_end_marker_observed": True,
            "positions_complete": True,
            "positions_end_marker_observed": True,
            "cash_fx_complete": True, "cash_fx_end_marker_observed": True,
            "risk_complete": True, "risk_end_marker_observed": True,
            "active_order_id_sha256s": [],
            "completed_order_id_sha256s": [
                PRODUCER.digest_bytes(b"completed-order")],
            "execution_id_sha256s": [PRODUCER.digest_bytes(b"execution")],
            "positions": [], "cash_fx_exposures": [],
            "gross_absolute_position": "0", "gross_fx_exposure": "0",
            "gross_risk": "0", "settled_mutation_command_count": 1,
            "unknown_mutation_command_count": 0,
            "unresolved_mutation_command_count": 0,
            "read_only_authority": True, "authoritative": True,
            "account_complete": True, "mutation_attempted": False,
            **self.boundary(),
        }
        envelope = {
            "schema": PRODUCER.SIGNED_EVIDENCE_ENVELOPE_SCHEMA, "version": 1,
            "payload": payload,
            "signature_base64": base64.b64encode(b"s" * 64).decode("ascii"),
        }
        write_path(paths["evidence"], envelope)
        evidence_binding = PRODUCER._bind_unsealed_document(
            paths["evidence"], "FIXTURE_INVALID", expected_uid=self.uid,
            expected_gid=self.gid)
        evidence = PRODUCER.parse_terminal_signed_evidence(evidence_binding)
        certification = PRODUCER.SignatureCertification(
            evidence.payload_sha256, evidence.signature_sha256,
            self.context._certification_secret)
        observation = lambda when: PRODUCER.HostObservation(
            when, cutoff.document["egress_policy_sha256"], 0, tuple(), 0, 0,
            0, True, True, True, True, True,
            cutoff.document["egress_policy_generation"])
        witness = PRODUCER.assemble_terminal_witness(
            cutoff=cutoff, challenge=challenge, trust_policy=trust,
            evidence=evidence, provider_request=request,
            provider_response=response, context=self.context,
            certification=certification,
            first_observation=observation(self.now_ms - 500),
            second_observation=observation(self.now_ms - 200),
            received_at_ms=self.now_ms - 800,
            received_monotonic_ns=5_500_000,
            verified_at_ms=self.now_ms, verified_monotonic_ns=6_000_000)
        return {
            "cutoff": cutoff, "trust": trust, "challenge": challenge,
            "evidence": evidence, "request": request, "response": response,
            "certification": certification, "witness": witness,
        }

    def bindings(self):
        return (
            self.bind("intent", PRODUCER.INTENT_FIELDS,
                      PRODUCER.INTENT_SCHEMA),
            self.bind("handoff", PRODUCER.HANDOFF_FIELDS,
                      PRODUCER.HANDOFF_SCHEMA),
            self.reservation_binding(),
            self.bind("challenge", PRODUCER.CHALLENGE_FIELDS,
                      PRODUCER.CHALLENGE_SCHEMA),
        )

    def evidence(self):
        binding = PRODUCER._bind_unsealed_document(
            self.paths["signed_evidence"], "FIXTURE_INVALID",
            expected_uid=self.uid, expected_gid=self.gid)
        return PRODUCER.parse_signed_evidence(binding)

    def observation(self, observed_at_ms=None, **changes):
        values = {
            "observed_at_ms": self.now_ms if observed_at_ms is None
                else observed_at_ms,
            "policy_sha256": PRODUCER.digest_bytes(b"deny-all-policy"),
            "authorized_connectors": 0, "authorized_uids": tuple(),
            "broker_socket_count": 0, "broker_process_count": 0,
            "credential_exposure_count": 0,
            "paper_units_inactive": True, "kill_switch_engaged": True,
            "process_inventory_complete": True,
            "socket_inventory_complete": True,
            "credential_inventory_complete": True,
        }
        values.update(changes)
        return PRODUCER.HostObservation(**values)

    @contextmanager
    def host_authority_lease(self):
        with self.authority_paths():
            reservation = self.reservation_binding()
            lease = self.context.acquire_host_authority_lease(
                allow_reservation_owner=True)
            try:
                self.context.validate_host_authority_lease(
                    lease, reservation)
                yield lease, reservation
            finally:
                self.context.release_host_authority_lease(lease, reservation)

    def assemble(self, *, first=None, second=None, certification=None):
        intent, handoff, reservation, challenge = self.bindings()
        evidence = self.evidence()
        PRODUCER.validate_signed_payload(evidence, self.now_ms, challenge)
        with self.host_authority_lease() as (lease, reservation):
            return PRODUCER.assemble_snapshots(
                intent=intent, handoff=handoff, challenge=challenge,
                reservation=reservation,
                evidence=evidence, context=self.context,
                host_authority_lease=lease,
                first_observation=first or self.observation(
                    self.now_ms - 100),
                second_observation=second or self.observation(self.now_ms),
                now_ms=self.now_ms, certification=certification)


class OfflineSignedSnapshotProducerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.now_ms = 2_000_000_000_000
        self.fixture = EvidenceFixture(self.root, self.now_ms)

    def tearDown(self):
        self.temporary.cleanup()

    def assertReason(self, reason: str, callback) -> None:
        with self.assertRaises(PRODUCER.ProducerError) as caught:
            callback()
        self.assertEqual(caught.exception.reason, reason)

    def assertAuthorityLeaseHeld(self) -> None:
        descriptor = os.open(self.fixture.authority_lease, os.O_RDONLY)
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                self.assertIn(error.errno, (errno.EACCES, errno.EAGAIN))
            else:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
                self.fail("host authority lease was not held")
        finally:
            os.close(descriptor)

    def assertAuthorityLeaseAvailable(self) -> None:
        descriptor = os.open(self.fixture.authority_lease, os.O_RDONLY)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)

    def test_handoff_v2_contract_matches_producer_exactly(self):
        self.assertEqual(PRODUCER.HANDOFF_SCHEMA, HANDOFF.RECEIPT_SCHEMA)
        self.assertEqual(PRODUCER.HANDOFF_VERSION, 2)
        self.assertEqual(PRODUCER.HANDOFF_FIELDS, HANDOFF.RECEIPT_FIELDS)
        self.assertEqual(
            PRODUCER.PROFILE_RESTORATION_FIELDS,
            HANDOFF.PROFILE_RESTORATION_FIELDS)
        self.assertEqual(
            PRODUCER.PROFILE_FILE_EVIDENCE_FIELDS,
            HANDOFF.PROFILE_FILE_EVIDENCE_FIELDS)
        self.assertEqual(
            PRODUCER.PROFILE_SEALED_EVIDENCE_FIELDS,
            HANDOFF.PROFILE_SEALED_EVIDENCE_FIELDS)
        self.assertEqual(
            PRODUCER.PAPER_RUNTIME_PROFILE_HARDENING_FIELDS,
            HANDOFF.PAPER_RUNTIME_PROFILE_HARDENING_FIELDS)

    def test_handoff_v1_is_rejected(self):
        document = copy.deepcopy(self.fixture.documents["handoff"])
        document.pop("body_sha256")
        document["schema"] = "hepta.p1-watch-to-paper-handoff-receipt.v1"
        document["version"] = 1
        legacy = PRODUCER.seal(document)
        self.assertReason(
            "ZERO_SNAPSHOT_HANDOFF_INVALID",
            lambda: PRODUCER.validate_handoff(
                legacy, self.now_ms, expected_uid=self.fixture.uid,
                expected_gid=self.fixture.gid))

    def test_handoff_v2_profile_restoration_is_exact_and_live_bound(self):
        base = copy.deepcopy(self.fixture.documents["handoff"])
        mutations = (
            ("missing-field", lambda value: value[
                "paper_profile_restoration"].pop("retired_watch")),
            ("wrong-path", lambda value: value[
                "paper_profile_restoration"]["target"].__setitem__(
                    "path", "/tmp/not-alpha.env")),
            ("wrong-hash", lambda value: value[
                "paper_profile_restoration"]["dormant_backup"].__setitem__(
                    "file_sha256", PRODUCER.digest_bytes(b"tampered"))),
            ("wrong-mode", lambda value: value[
                "paper_profile_restoration"][
                    "forward_retained_dormant"].__setitem__("mode", 0o600)),
            ("wrong-uid", lambda value: value[
                "paper_profile_restoration"]["target"].__setitem__(
                    "uid", self.fixture.uid + 1)),
            ("wrong-gid", lambda value: value[
                "paper_profile_restoration"]["target"].__setitem__(
                    "gid", self.fixture.gid + 1)),
            ("wrong-nlink", lambda value: value[
                "paper_profile_restoration"]["target"].__setitem__(
                    "nlink", 2)),
            ("wrong-body", lambda value: value[
                "paper_profile_restoration"][
                    "forward_preimage_evidence"].__setitem__(
                        "body_sha256", PRODUCER.digest_bytes(b"tampered"))),
            ("candidate-claim", lambda value: value.__setitem__(
                "profile_candidate_absent", False)),
            ("runtime-missing-field", lambda value: value[
                "paper_runtime_profile_hardening"].pop("retained_legacy")),
            ("runtime-schema", lambda value: value[
                "paper_runtime_profile_hardening"].__setitem__(
                    "schema", "legacy.runtime-profile.v0")),
            ("runtime-path", lambda value: value[
                "paper_runtime_profile_hardening"]["target"].__setitem__(
                    "path", "/tmp/not-paper-runtime.env")),
            ("runtime-hash", lambda value: value[
                "paper_runtime_profile_hardening"]["target"].__setitem__(
                    "file_sha256", PRODUCER.digest_bytes(b"tampered"))),
            ("runtime-mode", lambda value: value[
                "paper_runtime_profile_hardening"]["target"].__setitem__(
                    "mode", 0o644)),
            ("runtime-hardened-claim", lambda value: value.__setitem__(
                "paper_runtime_profile_hardened", False)),
            ("runtime-candidate-claim", lambda value: value.__setitem__(
                "paper_runtime_profile_candidate_absent", False)),
        )
        for label, mutate in mutations:
            with self.subTest(label=label):
                value = copy.deepcopy(base)
                value.pop("body_sha256")
                mutate(value)
                value = PRODUCER.seal(value)
                self.assertReason(
                    "ZERO_SNAPSHOT_HANDOFF_INVALID",
                    lambda value=value: PRODUCER.validate_handoff(
                        value, self.now_ms, expected_uid=self.fixture.uid,
                        expected_gid=self.fixture.gid))
        self.fixture.paths["handoff"].write_bytes(
            PRODUCER.canonical_bytes(base))
        PRODUCER.PAPER_PROFILE_PATH.write_bytes(
            b"X" * PRODUCER.PAPER_PROFILE_DORMANT_BYTES)
        PRODUCER.PAPER_PROFILE_PATH.chmod(0o644)
        self.assertReason(
            "ZERO_SNAPSHOT_HANDOFF_INVALID",
            lambda: PRODUCER.validate_handoff(
                base, self.now_ms, expected_uid=self.fixture.uid,
                expected_gid=self.fixture.gid))

    def test_handoff_v2_runtime_profile_files_are_reopened_and_candidate_absent(
            self):
        mutations = (
            ("current", lambda: PRODUCER.PAPER_RUNTIME_PROFILE_PATH.write_bytes(
                b"X" * PRODUCER.PAPER_RUNTIME_PROFILE_HARDENED_BYTES)),
            ("backup", lambda: PRODUCER.PAPER_RUNTIME_PROFILE_BACKUP_PATH.write_bytes(
                b"X" * PRODUCER.PAPER_RUNTIME_PROFILE_LEGACY_BYTES)),
            ("retained", lambda:
                PRODUCER.PAPER_RUNTIME_PROFILE_RETAINED_PATH.write_bytes(
                    b"X" * PRODUCER.PAPER_RUNTIME_PROFILE_LEGACY_BYTES)),
            ("candidate", lambda:
                PRODUCER.PAPER_RUNTIME_PROFILE_CANDIDATE_PATH.write_bytes(
                    b"residue")),
        )
        for index, (label, mutate) in enumerate(mutations):
            with self.subTest(label=label):
                root = self.root / f"runtime-profile-{index:02d}"
                root.mkdir(mode=0o700)
                fixture = EvidenceFixture(root, self.now_ms)
                mutate()
                self.assertReason(
                    "ZERO_SNAPSHOT_HANDOFF_INVALID",
                    lambda fixture=fixture: PRODUCER.validate_handoff(
                        fixture.documents["handoff"], self.now_ms,
                        expected_uid=fixture.uid, expected_gid=fixture.gid))

    @contextmanager
    def recovery_runtime(self):
        fixture = self.fixture

        def fake_context_init(
            context, *, expected_uid=PRODUCER.ROOT_UID,
            expected_gid=PRODUCER.ROOT_GID,
        ):
            context.expected_uid = expected_uid
            context.expected_gid = expected_gid
            context.producer = fixture.context.producer
            context.broker_helper = fixture.context.broker_helper
            context.signature_verifier = fixture.context.signature_verifier
            context.verification_key = fixture.context.verification_key
            context._certification_secret = object()
            context._lease_certification_secret = object()

        def fake_observe(_observer, *, now_ms=None):
            self.assertIsNone(now_ms)
            return fixture.observation(self.now_ms)

        with fixture.authority_paths(), mock.patch.object(
                PRODUCER.ProductionContext, "__init__",
                new=fake_context_init), mock.patch.object(
                PRODUCER.ProductionReadOnlyObserver, "observe",
                new=fake_observe), mock.patch.object(
                PRODUCER, "_wall_clock_ms", return_value=self.now_ms):
            yield

    def admission_artifacts(self):
        fixture = self.fixture
        reservation = fixture.reservation_binding()
        active = reservation.document
        with fixture.authority_paths():
            active_reference = PRODUCER.reservation_reference(reservation)
        zero_path = self.root / "zero-exposure-receipt.json"
        candidate_path = self.root / "paper-admission-candidate.json"
        zero = PRODUCER.seal({
            "schema": PRODUCER.ZERO_EXPOSURE_RECEIPT_SCHEMA,
            "version": 1, "status": "PASS", "round": PRODUCER.ROUND,
            "domain": PRODUCER.DOMAIN_ID, "campaign_id": fixture.campaign,
            "source_baseline_sha256": fixture.source,
            "host_authority_reservation": active_reference,
            "reservation_id": active["reservation_id"],
            "reservation_generation": active["reservation_generation"],
            "reservation_predecessor_finalization_body_sha256":
                active["predecessor_finalization_body_sha256"],
            "reservation_prior_finalization_pointer_reference":
                active["prior_finalization_pointer_reference"],
            "reservation_finalization_tombstone_path":
                active["finalization_tombstone_path"],
            "reservation_finalization_current_pointer_path":
                active["finalization_current_pointer_path"],
            "reservation_boot_id": active["boot_id"],
            "reservation_lease_device":
                active["host_authority_lease"]["lease_device"],
            "reservation_lease_inode":
                active["host_authority_lease"]["lease_inode"],
            "host_authority_lease": active["host_authority_lease"],
            "reservation_continuity_verified": True,
            "reservation_finalization_tombstone_absent": True,
            **fixture.boundary(),
        })
        zero_reference = {
            "path": str(zero_path),
            "file_sha256": PRODUCER.digest_bytes(
                PRODUCER.canonical_bytes(zero)),
            "body_sha256": zero["body_sha256"],
            "schema": PRODUCER.ZERO_EXPOSURE_RECEIPT_SCHEMA,
            "version": 1, "status": "PASS",
        }
        candidate = PRODUCER.seal({
            "schema": PRODUCER.PAPER_ADMISSION_CANDIDATE_SCHEMA,
            "version": 1, "status": "GO", "round": PRODUCER.ROUND,
            "domain": PRODUCER.DOMAIN_ID, "campaign_id": fixture.campaign,
            "source_baseline_sha256": fixture.source,
            "strategy_sha256": PRODUCER.digest_bytes(b"strategy-a"),
            "evaluated_at_ms": self.now_ms - 1_000,
            "expires_at_ms": self.now_ms + 60_000,
            "findings": [],
            "paper_test_admission_candidate": True,
            "input_bindings": {"zero_exposure_receipt": zero_reference},
            "authorization_effect": "NONE_READ_ONLY_CANDIDATE_ONLY",
            **fixture.boundary(),
        })
        return candidate_path, candidate, zero_path, zero

    def publish_admission_artifacts(self):
        candidate_path, candidate, zero_path, zero = \
            self.admission_artifacts()
        PRODUCER._publish_one(
            candidate_path, candidate, expected_uid=self.fixture.uid,
            expected_gid=self.fixture.gid)
        PRODUCER._publish_one(
            zero_path, zero, expected_uid=self.fixture.uid,
            expected_gid=self.fixture.gid)
        return candidate_path, candidate, zero_path, zero

    def finalize_session(
        self, session, *, candidate_path, candidate, zero_path, zero,
        status="GO", now_ms=None,
    ):
        return session.finalize(
            candidate_path=candidate_path,
            zero_exposure_receipt_path=zero_path,
            expected_candidate_reference={
                "path": str(candidate_path),
                "file_sha256": PRODUCER.digest_bytes(
                    PRODUCER.canonical_bytes(candidate)),
                "body_sha256": candidate["body_sha256"],
            },
            expected_zero_exposure_receipt_reference={
                "path": str(zero_path),
                "file_sha256": PRODUCER.digest_bytes(
                    PRODUCER.canonical_bytes(zero)),
                "body_sha256": zero["body_sha256"],
            },
            status=status, now_ms=now_ms)

    def finalize_admission_once(self):
        fixture = self.fixture
        candidate_path, candidate, zero_path, zero = \
            self.publish_admission_artifacts()
        with self.recovery_runtime():
            session = PRODUCER.open_admission_reservation_session(
                expected_source=fixture.source,
                expected_campaign=fixture.campaign,
                candidate_path=candidate_path,
                zero_exposure_receipt_path=zero_path,
                production_mode=PRODUCER.PRODUCTION_MODE,
                expected_uid=fixture.uid, expected_gid=fixture.gid,
                now_ms=self.now_ms, _run_token=PRODUCER.CLI_RUN_TOKEN)
            tombstone = self.finalize_session(
                session, candidate_path=candidate_path, candidate=candidate,
                zero_path=zero_path, zero=zero, now_ms=self.now_ms)
        return candidate_path, candidate, zero_path, zero, tombstone

    def test_challenge_is_fresh_exact_and_non_authorizing(self):
        intent, handoff, reservation, challenge = self.fixture.bindings()
        with self.fixture.authority_paths():
            PRODUCER.validate_challenge(
                challenge.document, self.now_ms, intent=intent,
                handoff=handoff, reservation=reservation,
                context=self.fixture.context)
        self.assertEqual(challenge.document["nonce"], "a" * 64)
        self.assertEqual(challenge.document["signature_algorithm"], "ED25519")
        self.assertLessEqual(
            challenge.document["expires_at_ms"] -
            challenge.document["issued_at_ms"],
            PRODUCER.MAXIMUM_CHALLENGE_LIFETIME_MS)
        for field in PRODUCER.BOUNDARY_FIELDS:
            self.assertIs(challenge.document[field], False)

    def test_signed_payload_binds_challenge_and_complete_state(self):
        _, _, _, challenge = self.fixture.bindings()
        evidence = self.fixture.evidence()
        PRODUCER.validate_signed_payload(evidence, self.now_ms, challenge)

    def _terminal_evidence_with(self, bundle: dict, **changes):
        payload = copy.deepcopy(bundle["evidence"].payload)
        payload.update(changes)
        raw = PRODUCER.canonical_bytes(payload)
        return PRODUCER.SignedEvidence(
            bundle["evidence"].binding, payload, raw,
            PRODUCER.digest_bytes(raw), bundle["evidence"].signature,
            bundle["evidence"].signature_sha256)

    def _validate_terminal_evidence(self, bundle: dict, evidence=None):
        PRODUCER.validate_terminal_signed_payload(
            bundle["evidence"] if evidence is None else evidence,
            now_ms=self.now_ms, now_monotonic_ns=6_000_000,
            challenge=bundle["challenge"], cutoff=bundle["cutoff"],
            trust_policy=bundle["trust"])

    def test_post_cutoff_terminal_witness_is_exact_non_authorizing(self):
        bundle = self.fixture.terminal_bundle()
        self._validate_terminal_evidence(bundle)
        PRODUCER.validate_terminal_witness(
            bundle["witness"], cutoff=bundle["cutoff"],
            challenge=bundle["challenge"], trust_policy=bundle["trust"],
            evidence=bundle["evidence"], provider_request=bundle["request"],
            provider_response=bundle["response"], context=self.fixture.context)
        self.assertEqual(set(bundle["witness"]), PRODUCER.TERMINAL_WITNESS_FIELDS)
        self.assertEqual(
            bundle["witness"]["terminal_proof_kind"],
            PRODUCER.TERMINAL_PROOF_KIND)
        self.assertFalse(bundle["witness"]["paper_authorized"])
        self.assertFalse(bundle["witness"]["order_submission_authorized"])

    def test_terminal_evidence_separates_host_and_provider_clocks(self):
        bundle = self.fixture.terminal_bundle()
        skewed = self._terminal_evidence_with(
            bundle, query_started_at_ms=10_000,
            observed_at_ms=11_000, query_completed_at_ms=12_000,
            expires_at_ms=72_000)
        self._validate_terminal_evidence(bundle, skewed)
        cases = (
            {"query_started_after_challenge": False},
            {"query_started_monotonic_ns": 4_000_001},
            {"observed_at_ms": bundle["evidence"].payload[
                "query_started_at_ms"] - 1},
            {"observed_monotonic_ns": 5_000_001},
        )
        for changes in cases:
            with self.subTest(changes=changes), self.assertRaises(
                    PRODUCER.ProducerError):
                self._validate_terminal_evidence(
                    bundle, self._terminal_evidence_with(bundle, **changes))

    def test_terminal_evidence_rejects_cache_nonce_and_identity_splice(self):
        bundle = self.fixture.terminal_bundle()
        cases = (
            {"nonce": "c" * 64},
            {"challenge_body_sha256": PRODUCER.digest_bytes(b"other")},
            {"transport_cutoff_body_sha256": PRODUCER.digest_bytes(b"old")},
            {"execution_service_epoch": "other-epoch"},
            {"owner_set_sha256": PRODUCER.digest_bytes(b"other-owner")},
            {"known_correlation_set_sha256":
                PRODUCER.digest_bytes(b"other-correlations")},
        )
        for changes in cases:
            with self.subTest(changes=changes), self.assertRaises(
                    PRODUCER.ProducerError):
                self._validate_terminal_evidence(
                    bundle, self._terminal_evidence_with(bundle, **changes))

    def test_terminal_evidence_requires_real_atomic_or_causal_dominance(self):
        bundle = self.fixture.terminal_bundle()
        cases = (
            {"snapshot_consistency": "EVENTUAL"},
            {"consistency_dominates_cutoff": False},
            {"consistency_dominates_all_mutations": False},
            {"consistency_known_mutation_command_set_sha256":
                PRODUCER.digest_bytes(b"partial")},
            {"unknown_mutation_command_count": 1},
            {"unresolved_mutation_command_count": 1},
            {"gross_risk": "0.0"},
        )
        for changes in cases:
            with self.subTest(changes=changes), self.assertRaises(
                    PRODUCER.ProducerError):
                self._validate_terminal_evidence(
                    bundle, self._terminal_evidence_with(bundle, **changes))

    def test_terminal_provider_policy_is_exactly_pinned(self):
        bundle = self.fixture.terminal_bundle()
        document = copy.deepcopy(bundle["trust"].document)
        for field, value in (
            ("provider_id", "self-asserted-provider"),
            ("provider_key_sha256", PRODUCER.digest_bytes(b"other-key")),
            ("provider_capability", "SIGNED_BOOLEAN_ONLY"),
            ("challenge_bound_query_supported", False),
        ):
            mutated = copy.deepcopy(document)
            mutated[field] = value
            mutated = PRODUCER.seal({
                key: item for key, item in mutated.items()
                if key != "body_sha256"})
            with self.subTest(field=field), self.assertRaises(
                    PRODUCER.ProducerError):
                PRODUCER.validate_terminal_provider_trust_policy(
                    mutated,
                    verification_key_sha256=self.fixture.context.
                        verification_key.reference["file_sha256"])

    def test_terminal_provider_dependency_is_explicitly_unprovisioned(self):
        bundle = self.fixture.terminal_bundle()
        test_policy_pin = PRODUCER.TERMINAL_PROVIDER_TRUST_POLICY_BODY_SHA256
        unprovisioned_pin = PRODUCER.digest_bytes(
            b"UNPROVISIONED_TERMINAL_PROVIDER_TRUST_POLICY_V1")
        try:
            PRODUCER.TERMINAL_PROVIDER_TRUST_POLICY_BODY_SHA256 = \
                unprovisioned_pin
            with self.assertRaises(PRODUCER.ProducerError):
                PRODUCER.validate_terminal_provider_trust_policy(
                    bundle["trust"].document,
                    verification_key_sha256=self.fixture.context.
                        verification_key.reference["file_sha256"])
        finally:
            PRODUCER.TERMINAL_PROVIDER_TRUST_POLICY_BODY_SHA256 = \
                test_policy_pin

    def test_terminal_challenge_never_reconstructs_an_absent_cutoff_owner(
            self):
        bundle = self.fixture.terminal_bundle()
        self.assertFalse(self.fixture.authority_owner.exists())
        output = self.root / "fresh-terminal-challenge.json"
        with self.fixture.authority_paths(), mock.patch.object(
                PRODUCER, "ProductionContext",
                return_value=self.fixture.context):
            self.assertReason(
                "TERMINAL_WITNESS_CUTOFF_OWNER_REQUIRED",
                lambda: PRODUCER.issue_terminal_challenge_and_publish(
                    transport_cutoff_path=bundle["cutoff"].path,
                    provider_trust_policy_path=bundle["trust"].path,
                    challenge_output_path=output,
                    expected_source=self.fixture.source,
                    expected_campaign=self.fixture.campaign,
                    expected_cycle="cycle-a",
                    expected_recovery="recovery-a",
                    expected_finalization="finalization-a",
                    production_mode=PRODUCER.TERMINAL_PRODUCTION_MODE,
                    expected_uid=self.fixture.uid,
                    expected_gid=self.fixture.gid,
                    now_ms=self.now_ms,
                    now_monotonic_ns=6_000_000,
                    _run_token=PRODUCER.CLI_RUN_TOKEN))
        self.assertFalse(output.exists())

    def test_terminal_owner_set_is_exact_hsl8_canonical_scope(self):
        bundle = self.fixture.terminal_bundle()
        cutoff = bundle["cutoff"].document
        canonical = bytes.fromhex(cutoff["owner_set_canonical_hex"])
        token = cutoff["owner_ids"][0]
        expected = (
            token + "\t1\t" + b"DU123".hex() + "\t" +
            b"PAPER:alpha".hex() + "\n").encode("ascii")
        self.assertEqual(canonical, expected)
        cases = (
            (expected.replace(b"\t1\t", b"\t01\t"), cutoff["owner_ids"]),
            (expected.replace(b"4455313233", b"4455393939"),
             cutoff["owner_ids"]),
            (expected.replace(b"50415045523a616c706861",
                              b"4c4956453a616c706861"),
             cutoff["owner_ids"]),
            (expected, [PRODUCER.digest_bytes(b"different-owner")]),
        )
        for owner_bytes, owner_ids in cases:
            mutated = dict(cutoff)
            mutated["owner_set_canonical_hex"] = owner_bytes.hex()
            mutated["owner_set_sha256"] = PRODUCER.digest_bytes(owner_bytes)
            mutated["owner_ids"] = owner_ids
            with self.subTest(owner_bytes=owner_bytes), self.assertRaises(
                    PRODUCER.ProducerError):
                PRODUCER._terminal_owner_binding(
                    mutated, "TERMINAL_OWNER_FIXTURE_INVALID")

    def test_terminal_witness_rejects_provider_artifact_and_host_drift(self):
        bundle = self.fixture.terminal_bundle()
        bundle["request"].path.write_bytes(b"replaced request\n")
        with self.assertRaises(PRODUCER.ProducerError):
            bundle["request"].reopen()
        observation = PRODUCER.HostObservation(
            self.now_ms - 200, PRODUCER.digest_bytes(b"changed-policy"),
            0, tuple(), 0, 0, 0, True, True, True, True, True,
            bundle["cutoff"].document["egress_policy_generation"])
        with self.assertRaises(PRODUCER.ProducerError):
            PRODUCER.assemble_terminal_witness(
                cutoff=bundle["cutoff"], challenge=bundle["challenge"],
                trust_policy=bundle["trust"], evidence=bundle["evidence"],
                provider_request=bundle["request"],
                provider_response=bundle["response"],
                context=self.fixture.context,
                certification=bundle["certification"],
                first_observation=observation, second_observation=observation,
                received_at_ms=self.now_ms - 800,
                received_monotonic_ns=5_500_000,
                verified_at_ms=self.now_ms, verified_monotonic_ns=6_000_000)

    def test_terminal_witness_requires_current_egress_generation_observation(
            self):
        bundle = self.fixture.terminal_bundle()
        expected = bundle["cutoff"].document["egress_policy_generation"]
        for generation in (None, expected + 1):
            observation = PRODUCER.HostObservation(
                self.now_ms - 200,
                bundle["cutoff"].document["egress_policy_sha256"], 0,
                tuple(), 0, 0, 0, True, True, True, True, True, generation)
            with self.subTest(generation=generation), self.assertRaises(
                    PRODUCER.ProducerError):
                PRODUCER.assemble_terminal_witness(
                    cutoff=bundle["cutoff"], challenge=bundle["challenge"],
                    trust_policy=bundle["trust"],
                    evidence=bundle["evidence"],
                    provider_request=bundle["request"],
                    provider_response=bundle["response"],
                    context=self.fixture.context,
                    certification=bundle["certification"],
                    first_observation=observation,
                    second_observation=observation,
                    received_at_ms=self.now_ms - 800,
                    received_monotonic_ns=5_500_000,
                    verified_at_ms=self.now_ms,
                    verified_monotonic_ns=6_000_000)

    def test_terminal_response_loss_replays_exact_witness_bytes(self):
        bundle = self.fixture.terminal_bundle()
        owner = self.fixture.authority_owner
        owner.write_bytes(PRODUCER.canonical_bytes(
            bundle["challenge"].document))
        owner.chmod(0o600)
        output = self.root / "post-cutoff-terminal-witness.json"
        output.write_bytes(PRODUCER.canonical_bytes(bundle["witness"]))
        output.chmod(0o600)
        before = output.read_bytes()
        observations = iter((
            PRODUCER.HostObservation(
                self.now_ms + 150,
                bundle["cutoff"].document["egress_policy_sha256"], 0,
                tuple(), 0, 0, 0, True, True, True, True, True,
                bundle["cutoff"].document["egress_policy_generation"]),
            PRODUCER.HostObservation(
                self.now_ms + 250,
                bundle["cutoff"].document["egress_policy_sha256"], 0,
                tuple(), 0, 0, 0, True, True, True, True, True,
                bundle["cutoff"].document["egress_policy_generation"]),
        ))

        class ReplayObserver:
            def __init__(self, _context):
                pass

            def observe(self):
                return next(observations)

        wall = iter((self.now_ms + 100, self.now_ms + 200,
                     self.now_ms + 300))
        monotonic = iter((6_500_000, 6_600_000, 6_700_000))
        with self.fixture.authority_paths(), mock.patch.object(
                PRODUCER, "ProductionContext",
                return_value=self.fixture.context), mock.patch.object(
                type(self.fixture.context), "verify_signature",
                return_value=bundle["certification"]), mock.patch.object(
                PRODUCER, "ProductionReadOnlyObserver", ReplayObserver), \
                mock.patch.object(
                    PRODUCER, "_wall_clock_ms",
                    side_effect=lambda: next(wall)), mock.patch.object(
                    PRODUCER.time, "monotonic_ns",
                    side_effect=lambda: next(monotonic)):
            replay = PRODUCER.consume_terminal_response_and_publish(
                transport_cutoff_path=bundle["cutoff"].path,
                provider_trust_policy_path=bundle["trust"].path,
                challenge_path=bundle["challenge"].path,
                signed_evidence_path=bundle["evidence"].binding.path,
                provider_request_path=bundle["request"].path,
                provider_response_path=bundle["response"].path,
                witness_output_path=output,
                expected_source=self.fixture.source,
                expected_campaign=self.fixture.campaign,
                expected_cycle="cycle-a", expected_recovery="recovery-a",
                expected_finalization="finalization-a",
                production_mode=PRODUCER.TERMINAL_PRODUCTION_MODE,
                expected_uid=self.fixture.uid, expected_gid=self.fixture.gid,
                _run_token=PRODUCER.CLI_RUN_TOKEN)
        self.assertEqual(replay, bundle["witness"])
        self.assertEqual(output.read_bytes(), before)

    def test_legacy_evidence_observed_before_challenge_is_rejected(self):
        challenge = self.fixture.bind(
            "challenge", PRODUCER.CHALLENGE_FIELDS, PRODUCER.CHALLENGE_SCHEMA)
        binding = PRODUCER._bind_unsealed_document(
            self.fixture.paths["signed_evidence"], "FIXTURE_INVALID",
            expected_uid=self.fixture.uid, expected_gid=self.fixture.gid)
        evidence = PRODUCER.parse_signed_evidence(binding)
        stale = copy.deepcopy(evidence.payload)
        stale["observed_at_ms"] = challenge.document["issued_at_ms"] - 1
        raw = PRODUCER.canonical_bytes(stale)
        stale_evidence = PRODUCER.SignedEvidence(
            binding, stale, raw, PRODUCER.digest_bytes(raw), evidence.signature,
            evidence.signature_sha256)
        with self.assertRaises(PRODUCER.ProducerError):
            PRODUCER.validate_signed_payload(
                stale_evidence, self.now_ms, challenge)
        self.assertEqual(evidence.payload["nonce"], challenge.document["nonce"])
        self.assertEqual(
            evidence.payload["challenge_body_sha256"],
            challenge.document["body_sha256"])
        self.assertEqual(
            evidence.payload["snapshot_sha256"],
            PRODUCER.account_state_sha256(evidence.payload))

    def test_nonce_replay_source_and_state_tamper_fail(self):
        mutations = {
            "nonce": lambda value: value.__setitem__("nonce", "b" * 64),
            "source": lambda value: value.__setitem__(
                "source_baseline_sha256", PRODUCER.digest_bytes(b"other")),
            "incomplete": lambda value: value.__setitem__(
                "account_complete", False),
            "state": lambda value: value.__setitem__(
                "query_epoch", "forged-query-epoch"),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                case_root = self.root / ("tamper-" + name)
                case_root.mkdir()
                case_root.chmod(0o700)
                fixture = EvidenceFixture(case_root, self.now_ms)
                mutate(fixture.documents["signed_evidence"]["payload"])
                fixture.write(
                    "signed_evidence", fixture.documents["signed_evidence"],
                    sealed=False)
                _, _, _, challenge = fixture.bindings()
                evidence = fixture.evidence()
                self.assertReason(
                    "ZERO_SNAPSHOT_SIGNED_ACCOUNT_EVIDENCE_INVALID",
                    lambda: PRODUCER.validate_signed_payload(
                        evidence, self.now_ms, challenge))

    def test_unverified_or_forged_certification_cannot_pass(self):
        broker, account = self.fixture.assemble()
        self.assertEqual(broker["status"], "NO_GO")
        self.assertEqual(account["status"], "UNVERIFIED")
        self.assertFalse(account["authoritative"])
        forged = PRODUCER.SignatureCertification(
            self.fixture.evidence().payload_sha256,
            self.fixture.evidence().signature_sha256, object())
        broker, account = self.fixture.assemble(certification=forged)
        self.assertEqual(broker["status"], "NO_GO")
        self.assertFalse(account["account_complete"])

    def test_real_ed25519_verification_certifies_exact_payload(self):
        private_key = self.root / "private.pem"
        public_key = self.root / "public.pem"
        payload_path = self.root / "payload.json"
        signature_path = self.root / "signature.bin"
        subprocess.run(
            ("/usr/bin/openssl", "genpkey", "-algorithm", "ED25519",
             "-out", str(private_key)), check=True,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL)
        subprocess.run(
            ("/usr/bin/openssl", "pkey", "-in", str(private_key),
             "-pubout", "-out", str(public_key)), check=True,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL)
        self.fixture.context.verification_key = ReferenceBinding(
            public_key, public_key.read_bytes())
        intent_body = dict(self.fixture.documents["intent"])
        intent_body.pop("body_sha256")
        intent_body["verification_key"] = \
            self.fixture.context.verification_key.reference
        self.fixture.write("intent", intent_body)
        intent = self.fixture.bind(
            "intent", PRODUCER.INTENT_FIELDS, PRODUCER.INTENT_SCHEMA)
        handoff = self.fixture.bind(
            "handoff", PRODUCER.HANDOFF_FIELDS, PRODUCER.HANDOFF_SCHEMA)
        self.fixture.authority_owner.unlink()
        reservation = self.fixture._create_reservation(intent, handoff)
        with self.fixture.authority_paths():
            challenge = PRODUCER.build_challenge(
                intent=intent, handoff=handoff, reservation=reservation,
                context=self.fixture.context, now_ms=self.now_ms,
                nonce="a" * 64)
        self.fixture.write("challenge", challenge, sealed=False)
        payload = self.fixture.documents["signed_evidence"]["payload"]
        payload["challenge_body_sha256"] = challenge["body_sha256"]
        self.fixture.rewrite_evidence()
        evidence = self.fixture.evidence()
        payload_path.write_bytes(evidence.payload_bytes)
        subprocess.run(
            ("/usr/bin/openssl", "pkeyutl", "-sign", "-inkey",
             str(private_key), "-rawin", "-in", str(payload_path), "-out",
             str(signature_path)), check=True, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        signature = signature_path.read_bytes()
        self.assertEqual(len(signature), 64)
        self.fixture.documents["signed_evidence"]["signature_base64"] = \
            base64.b64encode(signature).decode("ascii")
        self.fixture.write(
            "signed_evidence", self.fixture.documents["signed_evidence"],
            sealed=False)
        evidence = self.fixture.evidence()
        original_key = PRODUCER.VERIFICATION_KEY
        try:
            PRODUCER.VERIFICATION_KEY = public_key
            certification = self.fixture.context.verify_signature(evidence)
        finally:
            PRODUCER.VERIFICATION_KEY = original_key
        self.assertTrue(self.fixture.context.certifies(
            evidence, certification))
        broker, account = self.fixture.assemble(certification=certification)
        self.assertEqual(broker["status"], "PASS")
        self.assertEqual(account["status"], "COMPLETE")

    def test_wrong_signature_is_rejected_by_fixed_verifier(self):
        evidence = self.fixture.evidence()
        with mock.patch.object(
                PRODUCER.subprocess, "run",
                return_value=subprocess.CompletedProcess(
                    args=(), returncode=1, stdout=b"",
                    stderr=b"Signature Verification Failure\n")):
            self.assertReason(
                "ZERO_SNAPSHOT_SIGNATURE_VERIFY_FAILED",
                lambda: self.fixture.context.verify_signature(evidence))

    def test_nonzero_remote_state_is_preserved_never_flattened(self):
        payload = self.fixture.documents["signed_evidence"]["payload"]
        payload["active_order_id_sha256s"] = [
            PRODUCER.digest_bytes(b"order-a")]
        payload["positions"] = [{"instrument": "EUR.USD", "quantity": -5}]
        payload["gross_absolute_position"] = 5
        payload["authorized_connector_count"] = 1
        payload["end_flat"] = False
        self.fixture.rewrite_evidence()
        _, account = self.fixture.assemble()
        self.assertEqual(account["active_order_id_sha256s"],
                         payload["active_order_id_sha256s"])
        self.assertEqual(account["positions"], payload["positions"])
        self.assertEqual(account["gross_absolute_position"], 5)
        self.assertFalse(account["end_flat"])

    def test_second_inventory_exposure_halts_and_incomplete_is_no_go(self):
        second = self.fixture.observation(
            self.now_ms, broker_socket_count=1)
        broker, _ = self.fixture.assemble(second=second)
        self.assertEqual(broker["status"], "HALT")
        self.assertEqual(broker["broker_socket_count"], 1)

        second = self.fixture.observation(
            self.now_ms, socket_inventory_complete=False)
        broker, _ = self.fixture.assemble(second=second)
        self.assertEqual(broker["status"], "NO_GO")
        self.assertFalse(broker["observation_complete"])

    def test_host_authority_lease_rejects_owner_and_binds_exact_identity(self):
        with self.fixture.host_authority_lease() as (lease, reservation):
            self.assertAuthorityLeaseHeld()
            self.assertReason(
                "ZERO_SNAPSHOT_HOST_AUTHORITY_LEASE_BUSY",
                self.fixture.context.acquire_host_authority_lease)
            reference = self.fixture.context.validate_host_authority_lease(
                lease, reservation)
            self.assertEqual(set(reference),
                             PRODUCER.HOST_AUTHORITY_LEASE_FIELDS)
            self.assertEqual(reference["directory_path"],
                             str(self.fixture.authority_directory))
            self.assertEqual(reference["lease_path"],
                             str(self.fixture.authority_lease))
            self.assertEqual(reference["owner_path"],
                             str(self.fixture.authority_owner))
            self.assertEqual(reference["directory_mode"], 0o700)
            self.assertEqual(reference["lease_mode"], 0o600)
            self.assertEqual(reference["lease_size"], 0)
            self.assertTrue(reference["held_exclusive"])
            self.assertEqual(
                reservation.document["reservation_lifecycle"],
                PRODUCER.RESERVATION_LIFECYCLE)
        self.assertAuthorityLeaseAvailable()

        with self.fixture.authority_paths():
            self.assertReason(
                "ZERO_SNAPSHOT_HOST_AUTHORITY_LEASE_INVALID",
                self.fixture.context.acquire_host_authority_lease)

    def test_recovery_closes_unpublished_challenge_and_advances_gap_free(self):
        fixture = self.fixture
        fixture.paths["challenge"].unlink()
        with self.recovery_runtime():
            tombstone = PRODUCER.recover_reservation_and_publish(
                expected_source=fixture.source,
                expected_campaign=fixture.campaign,
                production_mode=PRODUCER.PRODUCTION_MODE,
                expected_uid=fixture.uid, expected_gid=fixture.gid,
                now_ms=self.now_ms, _run_token=PRODUCER.CLI_RUN_TOKEN)
        self.assertEqual(tombstone["status"], "ABORTED")
        self.assertEqual(tombstone["reservation_generation"], 1)
        self.assertEqual(
            tombstone["recovery_reason"], "CHALLENGE_NOT_PUBLISHED")
        self.assertFalse(fixture.authority_owner.exists())
        pointer_path = (
            fixture.authority_directory / "finalization-current.v1.json")
        pointer = PRODUCER.strict_object(pointer_path.read_bytes(), "TEST")
        self.assertEqual(pointer["reservation_generation"], 1)
        self.assertEqual(
            pointer["finalization_tombstone_reference"]["body_sha256"],
            tombstone["body_sha256"])

        intent = fixture.bind(
            "intent", PRODUCER.INTENT_FIELDS, PRODUCER.INTENT_SCHEMA)
        handoff = fixture.bind(
            "handoff", PRODUCER.HANDOFF_FIELDS, PRODUCER.HANDOFF_SCHEMA)
        with fixture.authority_paths():
            second = fixture._create_reservation(
                intent, handoff, nonce="b" * 64,
                reservation_id="zero-exposure-" + "2" * 48)
        self.assertEqual(second.document["reservation_generation"], 2)
        self.assertEqual(
            second.document["predecessor_finalization_body_sha256"],
            tombstone["body_sha256"])
        self.assertEqual(
            second.document["prior_finalization_pointer_reference"]
                ["body_sha256"],
            pointer["body_sha256"])

    def test_recovery_rejects_live_challenge_but_accepts_expiry(self):
        fixture = self.fixture
        with self.recovery_runtime():
            self.assertReason(
                "ZERO_SNAPSHOT_RESERVATION_RECOVERY_NOT_PERMITTED",
                lambda: PRODUCER.recover_reservation_and_publish(
                    expected_source=fixture.source,
                    expected_campaign=fixture.campaign,
                    production_mode=PRODUCER.PRODUCTION_MODE,
                    expected_uid=fixture.uid, expected_gid=fixture.gid,
                    now_ms=self.now_ms,
                    _run_token=PRODUCER.CLI_RUN_TOKEN))
            tombstone = PRODUCER.recover_reservation_and_publish(
                expected_source=fixture.source,
                expected_campaign=fixture.campaign,
                production_mode=PRODUCER.PRODUCTION_MODE,
                expected_uid=fixture.uid, expected_gid=fixture.gid,
                now_ms=self.now_ms + PRODUCER.MAXIMUM_CHALLENGE_LIFETIME_MS,
                _run_token=PRODUCER.CLI_RUN_TOKEN)
        self.assertEqual(tombstone["recovery_reason"], "RESERVATION_EXPIRED")
        self.assertFalse(fixture.authority_owner.exists())

    def test_recovery_resumes_tombstone_pointer_owner_crash_seam(self):
        fixture = self.fixture
        fixture.paths["challenge"].unlink()
        original = PRODUCER._commit_finalization_pointer
        with self.recovery_runtime(), mock.patch.object(
                PRODUCER, "_commit_finalization_pointer",
                side_effect=PRODUCER.ProducerError("INJECTED_CRASH")):
            self.assertReason(
                "INJECTED_CRASH",
                lambda: PRODUCER.recover_reservation_and_publish(
                    expected_source=fixture.source,
                    expected_campaign=fixture.campaign,
                    production_mode=PRODUCER.PRODUCTION_MODE,
                    expected_uid=fixture.uid, expected_gid=fixture.gid,
                    now_ms=self.now_ms,
                    _run_token=PRODUCER.CLI_RUN_TOKEN))
        self.assertTrue(fixture.authority_owner.exists())
        tombstones = list(fixture.authority_directory.glob("finalized.*.json"))
        self.assertEqual(len(tombstones), 1)
        self.assertFalse((fixture.authority_directory /
                          "finalization-current.v1.json").exists())
        with self.recovery_runtime(), mock.patch.object(
                PRODUCER, "_commit_finalization_pointer", new=original):
            result = PRODUCER.recover_reservation_and_publish(
                expected_source=fixture.source,
                expected_campaign=fixture.campaign,
                production_mode=PRODUCER.PRODUCTION_MODE,
                expected_uid=fixture.uid, expected_gid=fixture.gid,
                now_ms=self.now_ms + 1,
                _run_token=PRODUCER.CLI_RUN_TOKEN)
        self.assertEqual(result["status"], "ABORTED")
        self.assertFalse(fixture.authority_owner.exists())
        self.assertTrue((fixture.authority_directory /
                         "finalization-current.v1.json").exists())

    def test_recovery_resumes_pointer_committed_owner_present_seam(self):
        fixture = self.fixture
        fixture.paths["challenge"].unlink()
        original = PRODUCER._remove_reservation_after_finalization
        with self.recovery_runtime(), mock.patch.object(
                PRODUCER, "_remove_reservation_after_finalization",
                side_effect=PRODUCER.ProducerError("INJECTED_CRASH")):
            self.assertReason(
                "INJECTED_CRASH",
                lambda: PRODUCER.recover_reservation_and_publish(
                    expected_source=fixture.source,
                    expected_campaign=fixture.campaign,
                    production_mode=PRODUCER.PRODUCTION_MODE,
                    expected_uid=fixture.uid, expected_gid=fixture.gid,
                    now_ms=self.now_ms,
                    _run_token=PRODUCER.CLI_RUN_TOKEN))
        self.assertTrue(fixture.authority_owner.exists())
        self.assertTrue((fixture.authority_directory /
                         "finalization-current.v1.json").exists())
        with self.recovery_runtime(), mock.patch.object(
                PRODUCER, "_remove_reservation_after_finalization",
                new=original):
            result = PRODUCER.recover_reservation_and_publish(
                expected_source=fixture.source,
                expected_campaign=fixture.campaign,
                production_mode=PRODUCER.PRODUCTION_MODE,
                expected_uid=fixture.uid, expected_gid=fixture.gid,
                now_ms=self.now_ms + 1,
                _run_token=PRODUCER.CLI_RUN_TOKEN)
        self.assertEqual(result["status"], "ABORTED")
        self.assertFalse(fixture.authority_owner.exists())

    def test_admission_session_holds_lock_and_resumes_finalization(self):
        fixture = self.fixture
        candidate_path, candidate, zero_path, zero = \
            self.admission_artifacts()
        with self.recovery_runtime():
            session = PRODUCER.open_admission_reservation_session(
                expected_source=fixture.source,
                expected_campaign=fixture.campaign,
                production_mode=PRODUCER.PRODUCTION_MODE,
                expected_uid=fixture.uid, expected_gid=fixture.gid,
                now_ms=self.now_ms, _run_token=PRODUCER.CLI_RUN_TOKEN)
            self.assertAuthorityLeaseHeld()
            PRODUCER._publish_one(
                candidate_path, candidate, expected_uid=fixture.uid,
                expected_gid=fixture.gid)
            PRODUCER._publish_one(
                zero_path, zero, expected_uid=fixture.uid,
                expected_gid=fixture.gid)
            with mock.patch.object(
                    PRODUCER, "_commit_finalization_pointer",
                    side_effect=PRODUCER.ProducerError("INJECTED_CRASH")):
                self.assertReason(
                    "INJECTED_CRASH",
                    lambda: self.finalize_session(
                        session, candidate_path=candidate_path,
                        candidate=candidate, zero_path=zero_path, zero=zero,
                        now_ms=self.now_ms))
        self.assertTrue(fixture.authority_owner.exists())
        self.assertEqual(
            len(list(fixture.authority_directory.glob("finalized.*.json"))),
            1)
        with self.recovery_runtime():
            resumed = PRODUCER.open_admission_reservation_session(
                expected_source=fixture.source,
                expected_campaign=fixture.campaign,
                production_mode=PRODUCER.PRODUCTION_MODE,
                expected_uid=fixture.uid, expected_gid=fixture.gid,
                now_ms=self.now_ms + 1,
                _run_token=PRODUCER.CLI_RUN_TOKEN)
            tombstone = self.finalize_session(
                resumed, candidate_path=candidate_path, candidate=candidate,
                zero_path=zero_path, zero=zero, now_ms=self.now_ms + 1)
        self.assertEqual(tombstone["status"], "ADMISSION_GO")
        self.assertFalse(fixture.authority_owner.exists())
        self.assertAuthorityLeaseAvailable()
        with self.recovery_runtime():
            terminal = PRODUCER.open_admission_reservation_session(
                expected_source=fixture.source,
                expected_campaign=fixture.campaign,
                candidate_path=candidate_path,
                zero_exposure_receipt_path=zero_path,
                production_mode=PRODUCER.PRODUCTION_MODE,
                expected_uid=fixture.uid, expected_gid=fixture.gid,
                now_ms=self.now_ms + 2,
                _run_token=PRODUCER.CLI_RUN_TOKEN)
            self.assertTrue(terminal.finalized)
            repeated = self.finalize_session(
                terminal, candidate_path=candidate_path, candidate=candidate,
                zero_path=zero_path, zero=zero, now_ms=self.now_ms + 2)
        self.assertEqual(repeated, tombstone)
        self.assertFalse(fixture.authority_owner.exists())
        self.assertAuthorityLeaseAvailable()

    def test_admission_finalization_rechecks_expiry_before_tombstone(self):
        fixture = self.fixture
        candidate_path, candidate, zero_path, zero = \
            self.publish_admission_artifacts()
        expires = candidate["expires_at_ms"]
        with self.recovery_runtime():
            session = PRODUCER.open_admission_reservation_session(
                expected_source=fixture.source,
                expected_campaign=fixture.campaign,
                production_mode=PRODUCER.PRODUCTION_MODE,
                expected_uid=fixture.uid, expected_gid=fixture.gid,
                now_ms=self.now_ms, _run_token=PRODUCER.CLI_RUN_TOKEN)
            with mock.patch.object(
                    PRODUCER, "_wall_clock_ms",
                    side_effect=[self.now_ms, expires]):
                self.assertReason(
                    "ZERO_SNAPSHOT_ADMISSION_FINALIZATION_INVALID",
                    lambda: self.finalize_session(
                        session, candidate_path=candidate_path,
                        candidate=candidate, zero_path=zero_path, zero=zero,
                        now_ms=self.now_ms))
        self.assertTrue(fixture.authority_owner.exists())
        self.assertEqual(
            list(fixture.authority_directory.glob("finalized.*.json")), [])
        self.assertAuthorityLeaseAvailable()

    def test_owner_absent_retry_rejects_expired_go_candidate(self):
        candidate_path, candidate, zero_path, _zero, _tombstone = \
            self.finalize_admission_once()
        with self.recovery_runtime(), mock.patch.object(
                PRODUCER, "_wall_clock_ms",
                return_value=candidate["expires_at_ms"]):
            self.assertReason(
                "ZERO_SNAPSHOT_FINALIZED_ADMISSION_SESSION_INVALID",
                lambda: PRODUCER.open_admission_reservation_session(
                    expected_source=self.fixture.source,
                    expected_campaign=self.fixture.campaign,
                    candidate_path=candidate_path,
                    zero_exposure_receipt_path=zero_path,
                    production_mode=PRODUCER.PRODUCTION_MODE,
                    expected_uid=self.fixture.uid,
                    expected_gid=self.fixture.gid,
                    now_ms=self.now_ms,
                    _run_token=PRODUCER.CLI_RUN_TOKEN))
        self.assertAuthorityLeaseAvailable()

    def test_active_finalization_rejects_candidate_zero_cross_binding_drift(self):
        for field, replacement in (
            ("path", "/tmp/not-the-zero-receipt.json"),
            ("file_sha256", PRODUCER.digest_bytes(b"other-zero-file")),
            ("body_sha256", PRODUCER.digest_bytes(b"other-zero-body")),
        ):
            with self.subTest(field=field):
                case_root = self.root / ("candidate-zero-" + field)
                case_root.mkdir(mode=0o700)
                self.fixture = EvidenceFixture(case_root, self.now_ms)
                candidate_path, candidate, zero_path, zero = \
                    self.admission_artifacts()
                candidate_path.unlink(missing_ok=True)
                zero_path.unlink(missing_ok=True)
                candidate_body = dict(candidate)
                candidate_body.pop("body_sha256")
                candidate_inputs = dict(candidate_body["input_bindings"])
                zero_input = dict(candidate_inputs["zero_exposure_receipt"])
                zero_input[field] = replacement
                candidate_inputs["zero_exposure_receipt"] = zero_input
                candidate_body["input_bindings"] = candidate_inputs
                candidate = PRODUCER.seal(candidate_body)
                PRODUCER._publish_one(
                    candidate_path, candidate, expected_uid=self.fixture.uid,
                    expected_gid=self.fixture.gid)
                PRODUCER._publish_one(
                    zero_path, zero, expected_uid=self.fixture.uid,
                    expected_gid=self.fixture.gid)
                with self.recovery_runtime():
                    session = PRODUCER.open_admission_reservation_session(
                        expected_source=self.fixture.source,
                        expected_campaign=self.fixture.campaign,
                        production_mode=PRODUCER.PRODUCTION_MODE,
                        expected_uid=self.fixture.uid,
                        expected_gid=self.fixture.gid, now_ms=self.now_ms,
                        _run_token=PRODUCER.CLI_RUN_TOKEN)
                    self.assertReason(
                        "ZERO_SNAPSHOT_ADMISSION_FINALIZATION_INVALID",
                        lambda: self.finalize_session(
                            session, candidate_path=candidate_path,
                            candidate=candidate, zero_path=zero_path,
                            zero=zero, now_ms=self.now_ms))

    def test_active_finalization_rejects_replaced_zero_receipt(self):
        candidate_path, candidate, zero_path, zero = \
            self.admission_artifacts()
        replacement_body = dict(zero)
        replacement_body.pop("body_sha256")
        replacement_body["replacement_marker"] = "different-valid-json-body"
        replacement = PRODUCER.seal(replacement_body)
        PRODUCER._publish_one(
            candidate_path, candidate, expected_uid=self.fixture.uid,
            expected_gid=self.fixture.gid)
        PRODUCER._publish_one(
            zero_path, replacement, expected_uid=self.fixture.uid,
            expected_gid=self.fixture.gid)
        with self.recovery_runtime():
            session = PRODUCER.open_admission_reservation_session(
                expected_source=self.fixture.source,
                expected_campaign=self.fixture.campaign,
                production_mode=PRODUCER.PRODUCTION_MODE,
                expected_uid=self.fixture.uid, expected_gid=self.fixture.gid,
                now_ms=self.now_ms, _run_token=PRODUCER.CLI_RUN_TOKEN)
            self.assertReason(
                "ZERO_SNAPSHOT_ADMISSION_FINALIZATION_INVALID",
                lambda: self.finalize_session(
                    session, candidate_path=candidate_path,
                    candidate=candidate, zero_path=zero_path, zero=zero,
                    now_ms=self.now_ms))

    def test_active_finalization_rejects_replaced_candidate(self):
        candidate_path, candidate, zero_path, zero = \
            self.admission_artifacts()
        replacement_body = dict(candidate)
        replacement_body.pop("body_sha256")
        replacement_body["replacement_marker"] = "different-valid-json-body"
        replacement = PRODUCER.seal(replacement_body)
        PRODUCER._publish_one(
            candidate_path, replacement, expected_uid=self.fixture.uid,
            expected_gid=self.fixture.gid)
        PRODUCER._publish_one(
            zero_path, zero, expected_uid=self.fixture.uid,
            expected_gid=self.fixture.gid)
        with self.recovery_runtime():
            session = PRODUCER.open_admission_reservation_session(
                expected_source=self.fixture.source,
                expected_campaign=self.fixture.campaign,
                production_mode=PRODUCER.PRODUCTION_MODE,
                expected_uid=self.fixture.uid, expected_gid=self.fixture.gid,
                now_ms=self.now_ms, _run_token=PRODUCER.CLI_RUN_TOKEN)
            self.assertReason(
                "ZERO_SNAPSHOT_ADMISSION_FINALIZATION_INVALID",
                lambda: self.finalize_session(
                    session, candidate_path=candidate_path,
                    candidate=candidate, zero_path=zero_path, zero=zero,
                    now_ms=self.now_ms))

    def test_owner_removed_crash_resumes_exact_terminal_idempotently(self):
        fixture = self.fixture
        candidate_path, candidate, zero_path, zero = \
            self.publish_admission_artifacts()
        with self.recovery_runtime():
            session = PRODUCER.open_admission_reservation_session(
                expected_source=fixture.source,
                expected_campaign=fixture.campaign,
                candidate_path=candidate_path,
                zero_exposure_receipt_path=zero_path,
                production_mode=PRODUCER.PRODUCTION_MODE,
                expected_uid=fixture.uid, expected_gid=fixture.gid,
                now_ms=self.now_ms, _run_token=PRODUCER.CLI_RUN_TOKEN)
            with mock.patch.object(
                    PRODUCER, "_validate_finalized_reservation_state",
                    side_effect=PRODUCER.ProducerError(
                        "INJECTED_OWNER_REMOVED_CRASH")):
                self.assertReason(
                    "INJECTED_OWNER_REMOVED_CRASH",
                    lambda: self.finalize_session(
                        session, candidate_path=candidate_path,
                        candidate=candidate, zero_path=zero_path, zero=zero,
                        now_ms=self.now_ms))
        self.assertFalse(fixture.authority_owner.exists())
        self.assertTrue((fixture.authority_directory /
                         "finalization-current.v1.json").exists())
        with self.recovery_runtime():
            terminal = PRODUCER.open_admission_reservation_session(
                expected_source=fixture.source,
                expected_campaign=fixture.campaign,
                candidate_path=candidate_path,
                zero_exposure_receipt_path=zero_path,
                production_mode=PRODUCER.PRODUCTION_MODE,
                expected_uid=fixture.uid, expected_gid=fixture.gid,
                now_ms=self.now_ms + 1,
                _run_token=PRODUCER.CLI_RUN_TOKEN)
            result = self.finalize_session(
                terminal, candidate_path=candidate_path, candidate=candidate,
                zero_path=zero_path, zero=zero, now_ms=self.now_ms + 1)
        self.assertEqual(result["status"], "ADMISSION_GO")
        self.assertFalse(fixture.authority_owner.exists())
        self.assertAuthorityLeaseAvailable()

    def test_owner_absent_retry_rejects_pointer_tamper(self):
        candidate_path, _candidate, zero_path, _zero, _tombstone = \
            self.finalize_admission_once()
        pointer_path = self.fixture.authority_directory / \
            "finalization-current.v1.json"
        pointer = PRODUCER.strict_object(
            pointer_path.read_bytes(), "FIXTURE_INVALID")
        pointer = dict(pointer)
        pointer.pop("body_sha256")
        pointer["reservation_generation"] = 7
        pointer_path.write_bytes(PRODUCER.canonical_bytes(
            PRODUCER.seal(pointer)))
        pointer_path.chmod(0o600)
        with self.recovery_runtime():
            self.assertReason(
                "ZERO_SNAPSHOT_RESERVATION_LINEAGE_INVALID",
                lambda: PRODUCER.open_admission_reservation_session(
                    expected_source=self.fixture.source,
                    expected_campaign=self.fixture.campaign,
                    candidate_path=candidate_path,
                    zero_exposure_receipt_path=zero_path,
                    production_mode=PRODUCER.PRODUCTION_MODE,
                    expected_uid=self.fixture.uid,
                    expected_gid=self.fixture.gid,
                    now_ms=self.now_ms + 1,
                    _run_token=PRODUCER.CLI_RUN_TOKEN))

    def test_owner_absent_retry_rejects_candidate_tamper(self):
        candidate_path, candidate, zero_path, _zero, _tombstone = \
            self.finalize_admission_once()
        body = dict(candidate)
        body.pop("body_sha256")
        body["status"] = "NO_GO"
        body["paper_test_admission_candidate"] = False
        candidate_path.write_bytes(PRODUCER.canonical_bytes(
            PRODUCER.seal(body)))
        candidate_path.chmod(0o600)
        with self.recovery_runtime():
            self.assertReason(
                "ZERO_SNAPSHOT_FINALIZED_ADMISSION_SESSION_INVALID",
                lambda: PRODUCER.open_admission_reservation_session(
                    expected_source=self.fixture.source,
                    expected_campaign=self.fixture.campaign,
                    candidate_path=candidate_path,
                    zero_exposure_receipt_path=zero_path,
                    production_mode=PRODUCER.PRODUCTION_MODE,
                    expected_uid=self.fixture.uid,
                    expected_gid=self.fixture.gid,
                    now_ms=self.now_ms + 1,
                    _run_token=PRODUCER.CLI_RUN_TOKEN))

    def test_owner_absent_retry_rejects_zero_receipt_tamper(self):
        candidate_path, _candidate, zero_path, zero, _tombstone = \
            self.finalize_admission_once()
        body = dict(zero)
        body.pop("body_sha256")
        body["status"] = "NO_GO"
        zero_path.write_bytes(PRODUCER.canonical_bytes(
            PRODUCER.seal(body)))
        zero_path.chmod(0o600)
        with self.recovery_runtime():
            self.assertReason(
                "ZERO_SNAPSHOT_FINALIZED_ADMISSION_SESSION_INVALID",
                lambda: PRODUCER.open_admission_reservation_session(
                    expected_source=self.fixture.source,
                    expected_campaign=self.fixture.campaign,
                    candidate_path=candidate_path,
                    zero_exposure_receipt_path=zero_path,
                    production_mode=PRODUCER.PRODUCTION_MODE,
                    expected_uid=self.fixture.uid,
                    expected_gid=self.fixture.gid,
                    now_ms=self.now_ms + 1,
                    _run_token=PRODUCER.CLI_RUN_TOKEN))

    def test_owner_absent_retry_rejects_mismatched_candidate_path(self):
        candidate_path, candidate, zero_path, _zero, _tombstone = \
            self.finalize_admission_once()
        other = self.root / "other-paper-admission-candidate.json"
        PRODUCER._publish_one(
            other, candidate, expected_uid=self.fixture.uid,
            expected_gid=self.fixture.gid)
        self.assertNotEqual(other, candidate_path)
        with self.recovery_runtime():
            self.assertReason(
                "ZERO_SNAPSHOT_FINALIZED_ADMISSION_SESSION_INVALID",
                lambda: PRODUCER.open_admission_reservation_session(
                    expected_source=self.fixture.source,
                    expected_campaign=self.fixture.campaign,
                    candidate_path=other,
                    zero_exposure_receipt_path=zero_path,
                    production_mode=PRODUCER.PRODUCTION_MODE,
                    expected_uid=self.fixture.uid,
                    expected_gid=self.fixture.gid,
                    now_ms=self.now_ms + 1,
                    _run_token=PRODUCER.CLI_RUN_TOKEN))

    def test_owner_absent_retry_rejects_mismatched_zero_path(self):
        candidate_path, _candidate, zero_path, zero, _tombstone = \
            self.finalize_admission_once()
        other = self.root / "other-zero-exposure-receipt.json"
        PRODUCER._publish_one(
            other, zero, expected_uid=self.fixture.uid,
            expected_gid=self.fixture.gid)
        self.assertNotEqual(other, zero_path)
        with self.recovery_runtime():
            self.assertReason(
                "ZERO_SNAPSHOT_FINALIZED_ADMISSION_SESSION_INVALID",
                lambda: PRODUCER.open_admission_reservation_session(
                    expected_source=self.fixture.source,
                    expected_campaign=self.fixture.campaign,
                    candidate_path=candidate_path,
                    zero_exposure_receipt_path=other,
                    production_mode=PRODUCER.PRODUCTION_MODE,
                    expected_uid=self.fixture.uid,
                    expected_gid=self.fixture.gid,
                    now_ms=self.now_ms + 1,
                    _run_token=PRODUCER.CLI_RUN_TOKEN))

    def test_owner_absent_retry_rejects_status_mismatch(self):
        candidate_path, candidate, zero_path, zero, _tombstone = \
            self.finalize_admission_once()
        with self.recovery_runtime():
            terminal = PRODUCER.open_admission_reservation_session(
                expected_source=self.fixture.source,
                expected_campaign=self.fixture.campaign,
                candidate_path=candidate_path,
                zero_exposure_receipt_path=zero_path,
                production_mode=PRODUCER.PRODUCTION_MODE,
                expected_uid=self.fixture.uid,
                expected_gid=self.fixture.gid,
                now_ms=self.now_ms + 1,
                _run_token=PRODUCER.CLI_RUN_TOKEN)
            self.assertReason(
                "ZERO_SNAPSHOT_FINALIZED_ADMISSION_SESSION_INVALID",
                lambda: self.finalize_session(
                    terminal, candidate_path=candidate_path,
                    candidate=candidate, zero_path=zero_path, zero=zero,
                    status="NO_GO", now_ms=self.now_ms + 1))
        self.assertAuthorityLeaseAvailable()

    def test_owner_absent_retry_rejects_source_and_campaign_mismatch(self):
        candidate_path, _candidate, zero_path, _zero, _tombstone = \
            self.finalize_admission_once()
        mismatches = (
            (PRODUCER.digest_bytes(b"other-source"), self.fixture.campaign),
            (self.fixture.source, "other-campaign"),
        )
        for source, campaign in mismatches:
            with self.subTest(source=source, campaign=campaign), \
                    self.recovery_runtime():
                self.assertReason(
                    "ZERO_SNAPSHOT_FINALIZED_ADMISSION_SESSION_INVALID",
                    lambda: PRODUCER.open_admission_reservation_session(
                        expected_source=source, expected_campaign=campaign,
                        candidate_path=candidate_path,
                        zero_exposure_receipt_path=zero_path,
                        production_mode=PRODUCER.PRODUCTION_MODE,
                        expected_uid=self.fixture.uid,
                        expected_gid=self.fixture.gid,
                        now_ms=self.now_ms + 1,
                        _run_token=PRODUCER.CLI_RUN_TOKEN))
            self.assertAuthorityLeaseAvailable()

    def test_owner_absent_retry_rejects_tombstone_tamper(self):
        candidate_path, _candidate, zero_path, _zero, tombstone = \
            self.finalize_admission_once()
        tombstone_path = self.fixture.authority_directory / (
            "finalized." + tombstone["reservation_id"] + ".v1.json")
        body = dict(tombstone)
        body.pop("body_sha256")
        body["campaign_id"] = "tampered-campaign"
        tombstone_path.write_bytes(PRODUCER.canonical_bytes(
            PRODUCER.seal(body)))
        tombstone_path.chmod(0o600)
        with self.recovery_runtime():
            self.assertReason(
                "ZERO_SNAPSHOT_RESERVATION_LINEAGE_INVALID",
                lambda: PRODUCER.open_admission_reservation_session(
                    expected_source=self.fixture.source,
                    expected_campaign=self.fixture.campaign,
                    candidate_path=candidate_path,
                    zero_exposure_receipt_path=zero_path,
                    production_mode=PRODUCER.PRODUCTION_MODE,
                    expected_uid=self.fixture.uid,
                    expected_gid=self.fixture.gid,
                    now_ms=self.now_ms + 1,
                    _run_token=PRODUCER.CLI_RUN_TOKEN))

    def test_consume_holds_host_lease_across_observations_and_commit(self):
        fixture = self.fixture
        stages: list[str] = []
        observations = iter((
            fixture.observation(self.now_ms - 100),
            fixture.observation(self.now_ms),
        ))

        def held(stage: str) -> None:
            self.assertAuthorityLeaseHeld()
            stages.append(stage)

        def fake_context_init(
            context, *, expected_uid=PRODUCER.ROOT_UID,
            expected_gid=PRODUCER.ROOT_GID,
        ):
            context.expected_uid = expected_uid
            context.expected_gid = expected_gid
            context.producer = fixture.context.producer
            context.broker_helper = fixture.context.broker_helper
            context.signature_verifier = fixture.context.signature_verifier
            context.verification_key = fixture.context.verification_key
            context._certification_secret = object()
            context._lease_certification_secret = object()

        def fake_verify(context, evidence):
            held("signature")
            return PRODUCER.SignatureCertification(
                evidence.payload_sha256, evidence.signature_sha256,
                context._certification_secret)

        def fake_observe(_observer, *, now_ms=None):
            self.assertIsNone(now_ms)
            held("observe")
            return next(observations)

        original_validate = PRODUCER.validate_signed_payload
        original_assemble = PRODUCER.assemble_snapshots
        original_publish = PRODUCER._publish_one
        original_reopen_output = PRODUCER._reopen_published_output
        original_context_reopen = PRODUCER.ProductionContext.reopen

        def checked_validate(*args, **kwargs):
            held("validate-signed")
            return original_validate(*args, **kwargs)

        def checked_assemble(*args, **kwargs):
            held("assemble")
            return original_assemble(*args, **kwargs)

        def checked_publish(*args, **kwargs):
            held("publish")
            return original_publish(*args, **kwargs)

        def checked_reopen_output(*args, **kwargs):
            held("reopen-output")
            return original_reopen_output(*args, **kwargs)

        def checked_context_reopen(context):
            held("reopen-context")
            return original_context_reopen(context)

        with mock.patch.object(
                PRODUCER, "HOST_AUTHORITY_DIRECTORY",
                fixture.authority_directory), mock.patch.object(
                PRODUCER, "HOST_AUTHORITY_LEASE_PATH",
                fixture.authority_lease), mock.patch.object(
                PRODUCER, "HOST_AUTHORITY_OWNER_PATH",
                fixture.authority_owner), mock.patch.object(
                PRODUCER, "BOOT_ID_PATH", fixture.boot_id_path), \
                mock.patch.object(
                PRODUCER.ProductionContext, "__init__",
                new=fake_context_init), mock.patch.object(
                PRODUCER.ProductionContext, "verify_signature",
                new=fake_verify), mock.patch.object(
                PRODUCER.ProductionContext, "reopen",
                new=checked_context_reopen), mock.patch.object(
                PRODUCER.ProductionReadOnlyObserver, "observe",
                new=fake_observe), mock.patch.object(
                PRODUCER, "validate_signed_payload",
                new=checked_validate), mock.patch.object(
                PRODUCER, "assemble_snapshots",
                new=checked_assemble), mock.patch.object(
                PRODUCER, "_publish_one",
                new=checked_publish), mock.patch.object(
                PRODUCER, "_reopen_published_output",
                new=checked_reopen_output), mock.patch.object(
                PRODUCER.time, "time_ns",
                return_value=self.now_ms * 1_000_000):
            pair = PRODUCER.consume_response_and_publish(
                operator_intent_path=fixture.paths["intent"],
                handoff_path=fixture.paths["handoff"],
                challenge_path=fixture.paths["challenge"],
                signed_evidence_path=fixture.paths["signed_evidence"],
                broker_output_path=fixture.paths["broker_output"],
                account_output_path=fixture.paths["account_output"],
                expected_source=fixture.source,
                expected_campaign=fixture.campaign,
                production_mode=PRODUCER.PRODUCTION_MODE,
                expected_uid=fixture.uid, expected_gid=fixture.gid,
                _run_token=PRODUCER.CLI_RUN_TOKEN)

        self.assertAuthorityLeaseAvailable()
        self.assertEqual(pair.broker_snapshot["status"], "PASS")
        self.assertEqual(pair.account_snapshot["status"], "COMPLETE")
        self.assertEqual(
            set(pair.broker_snapshot["host_authority_lease"]),
            PRODUCER.HOST_AUTHORITY_LEASE_FIELDS)
        self.assertEqual(stages.count("observe"), 2)
        self.assertEqual(stages.count("publish"), 2)
        self.assertEqual(stages.count("reopen-output"), 2)
        self.assertIn("validate-signed", stages)
        self.assertIn("signature", stages)
        self.assertIn("assemble", stages)
        self.assertEqual(stages[-1], "reopen-context")

    def test_cli_token_is_required_before_any_production_access(self):
        self.assertReason(
            "ZERO_SNAPSHOT_CLI_RUN_REQUIRED",
            lambda: PRODUCER.issue_challenge_and_publish(
                operator_intent_path=self.fixture.paths["intent"],
                handoff_path=self.fixture.paths["handoff"],
                challenge_output_path=self.fixture.paths["challenge"],
                expected_source=self.fixture.source,
                expected_campaign=self.fixture.campaign,
                production_mode=PRODUCER.PRODUCTION_MODE))

    def test_recovery_cli_is_explicit_and_mutually_exclusive(self):
        arguments = [
            "--run", "--recover-reservation",
            "--expected-source-baseline-sha256", self.fixture.source,
            "--expected-campaign-id", self.fixture.campaign,
            "--production-mode", PRODUCER.PRODUCTION_MODE,
        ]
        parsed = PRODUCER._parser().parse_args(arguments)
        self.assertTrue(parsed.run)
        self.assertTrue(parsed.recover_reservation)
        self.assertFalse(parsed.issue_challenge)
        self.assertFalse(parsed.consume_response)
        with self.assertRaises(SystemExit):
            PRODUCER._parser().parse_args(
                arguments + ["--issue-challenge"])

    def test_imported_source_copy_cannot_construct_production_context(self):
        self.assertNotEqual(MODULE_PATH, PRODUCER.INSTALLED_EXECUTABLE)
        self.assertReason(
            "ZERO_SNAPSHOT_EXECUTING_IMAGE_INVALID",
            lambda: PRODUCER.ProductionContext(
                expected_uid=os.geteuid(), expected_gid=os.getegid()))
        self.assertReason(
            "ZERO_SNAPSHOT_EXECUTING_IMAGE_INVALID",
            lambda: PRODUCER.open_admission_reservation_session(
                expected_source=self.fixture.source,
                expected_campaign=self.fixture.campaign,
                production_mode=PRODUCER.PRODUCTION_MODE,
                expected_uid=os.geteuid(), expected_gid=os.getegid(),
                now_ms=self.now_ms,
                _run_token=PRODUCER.CLI_RUN_TOKEN))

    def test_missing_evidence_is_failure_not_zero(self):
        self.fixture.paths["signed_evidence"].unlink()
        self.assertReason(
            "FIXTURE_INVALID", self.fixture.evidence)

    def test_atomic_publish_is_0600_canonical_and_no_replace(self):
        document = self.fixture.documents["challenge"]
        output = self.root / "published-challenge.json"
        PRODUCER._publish_one(
            output, document, expected_uid=self.fixture.uid,
            expected_gid=self.fixture.gid)
        self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
        self.assertEqual(output.read_bytes(), PRODUCER.canonical_bytes(document))
        self.assertReason(
            "ZERO_SNAPSHOT_OUTPUT_ALREADY_EXISTS",
            lambda: PRODUCER._publish_one(
                output, document, expected_uid=self.fixture.uid,
                expected_gid=self.fixture.gid))

    @staticmethod
    def _proc_stat(pid: int, start_ticks: int) -> bytes:
        suffix = ["S"] + ["0"] * 18 + [str(start_ticks)]
        return f"{pid} (custom) ".encode() + " ".join(suffix).encode() + b"\n"

    @staticmethod
    def _tcp(protected_inode: int | None = None) -> bytes:
        header = (
            b"  sl  local_address rem_address   st tx_queue rx_queue tr "
            b"tm->when retrnsmt   uid  timeout inode\n")
        if protected_inode is None:
            return header
        row = (
            "0: 0100007F:1D48 0100007F:C350 01 00000000:00000000 "
            f"00:00000000 00000000 1000 0 {protected_inode}\n")
        return header + row.encode("ascii")

    def _fake_process(
        self, proc: Path, pid: int, netns_file: Path, *, inode: int | None,
    ) -> None:
        process = proc / str(pid)
        (process / "net").mkdir(parents=True)
        (process / "ns").mkdir()
        (process / "fd").mkdir()
        (process / "stat").write_bytes(self._proc_stat(pid, pid * 100))
        os.link(netns_file, process / "ns" / "net")
        (process / "net" / "tcp").write_bytes(self._tcp(inode))
        (process / "net" / "tcp6").write_bytes(self._tcp())
        (process / "cmdline").write_bytes(b"custom-process\0")
        (process / "exe").symlink_to("/opt/custom/process")
        if inode is not None:
            (process / "fd" / "7").symlink_to(f"socket:[{inode}]")

    def test_all_network_namespaces_are_deduped_and_custom_owner_counted(self):
        proc = self.root / "proc"
        proc.mkdir()
        shared_namespace = self.root / "shared-netns"
        custom_namespace = self.root / "custom-netns"
        shared_namespace.write_bytes(b"ns-a")
        custom_namespace.write_bytes(b"ns-b")
        self._fake_process(proc, 101, shared_namespace, inode=None)
        self._fake_process(proc, 102, shared_namespace, inode=None)
        self._fake_process(proc, 202, custom_namespace, inode=777)
        original = PRODUCER.PROC_ROOT
        try:
            PRODUCER.PROC_ROOT = proc
            processes, sockets = PRODUCER.ProductionReadOnlyObserver.\
                _process_and_socket_inventory()
        finally:
            PRODUCER.PROC_ROOT = original
        self.assertEqual((processes, sockets), (1, 1))

    def test_inventory_instability_never_returns_zero(self):
        with mock.patch.object(
                PRODUCER.ProductionReadOnlyObserver, "_inventory_once",
                side_effect=PRODUCER.InventoryRetry()):
            self.assertReason(
                "ZERO_SNAPSHOT_PROCESS_INVENTORY_UNSTABLE",
                PRODUCER.ProductionReadOnlyObserver.
                    _process_and_socket_inventory)


if __name__ == "__main__":
    unittest.main()
