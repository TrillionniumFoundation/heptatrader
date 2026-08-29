#!/usr/bin/env python3

from __future__ import annotations

import base64
import copy
import fcntl
import importlib.machinery
import importlib.util
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


REPOSITORY = Path(__file__).resolve().parents[1]
MODULE_PATH = REPOSITORY / "scripts" / \
    "hepta_p1_paper_zero_exposure_attestor.py"
PRODUCER_PATH = REPOSITORY / "scripts" / \
    "hepta_p1_paper_zero_exposure_snapshot_producer.py"
HANDOFF_PATH = REPOSITORY / "scripts" / \
    "hepta_p1_watch_to_paper_handoff.py"


def load_module(name: str, path: Path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    specification = importlib.util.spec_from_loader(name, loader)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules[name] = module
    specification.loader.exec_module(module)
    return module


ATTESTOR = load_module("hepta_zero_attestor_under_test", MODULE_PATH)
PRODUCER = load_module("hepta_zero_snapshot_producer_contract", PRODUCER_PATH)
HANDOFF = load_module("hepta_watch_to_paper_handoff_contract", HANDOFF_PATH)


class EvidenceFixture:
    def __init__(self, root: Path, now_ms: int):
        self.root = root
        self.root.chmod(0o700)
        self.now_ms = now_ms
        self.uid = os.geteuid()
        self.gid = os.getegid()
        self.source = ATTESTOR.digest_bytes(b"frozen-round114-source")
        self.campaign = "p1-round114-campaign-a"
        self.account_id = ATTESTOR.digest_bytes(b"paper-account-a")
        self.paths = {
            "intent": root / "operator-intent.json",
            "handoff": root / "watch-handoff.json",
            "challenge": root / "challenge.json",
            "evidence": root / "signed-evidence.json",
            "broker": root / "broker-snapshot.json",
            "account": root / "account-snapshot.json",
            "output": root / "zero-exposure-receipt.json",
        }
        self.attestor = Path(ATTESTOR.__file__).resolve()
        self.snapshot_producer = root / "installed-snapshot-producer"
        self.handoff_producer = root / "installed-handoff-producer"
        self.broker_helper = root / "installed-broker-helper"
        self.verifier = root / "openssl"
        self.private_key = root / "account-private.pem"
        self.public_key = root / "account-public.pem"
        for path, payload in (
            (self.snapshot_producer, b"fixed snapshot producer\n"),
            (self.handoff_producer, b"fixed handoff producer\n"),
            (self.broker_helper, b"fixed broker helper\n"),
        ):
            path.write_bytes(payload)
            path.chmod(0o755)
        shutil.copyfile("/usr/bin/openssl", self.verifier)
        self.verifier.chmod(0o755)
        subprocess.run(
            ("/usr/bin/openssl", "genpkey", "-algorithm", "ED25519",
             "-out", str(self.private_key)), check=True,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL)
        subprocess.run(
            ("/usr/bin/openssl", "pkey", "-in", str(self.private_key),
             "-pubout", "-out", str(self.public_key)), check=True,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL)
        self.private_key.chmod(0o600)
        self.public_key.chmod(0o600)
        self.authority_directory = root / "ib-paper-host-authority"
        self.authority_directory.mkdir(mode=0o700)
        self.authority_directory.chmod(0o700)
        self.authority_lease = self.authority_directory / "lease.lock"
        self.authority_lease.write_bytes(b"")
        self.authority_lease.chmod(0o600)
        self.authority_owner = self.authority_directory / "owner.v1"
        self.paths["reservation"] = self.authority_owner
        self.boot_id_path = root / "boot_id"
        self.boot_id = "11111111-2222-4333-8444-555555555555"
        self.boot_id_path.write_text(self.boot_id + "\n", encoding="ascii")
        self.boot_id_path.chmod(0o444)
        self.reservation_id = "zero-exposure-" + "1" * 48
        self.documents: dict[str, dict] = {}
        self._install_profile_fixture()

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

    @staticmethod
    def executable_reference(path: Path) -> dict[str, str]:
        return {
            "path": str(path),
            "file_sha256": ATTESTOR.digest_bytes(path.read_bytes()),
        }

    def generic_reference(self, name: str) -> dict[str, str]:
        return {
            "path": str(self.root / (name + ".json")),
            "file_sha256": ATTESTOR.digest_bytes((name + "-file").encode()),
            "body_sha256": ATTESTOR.digest_bytes((name + "-body").encode()),
        }

    def _profile_record(self, path: Path, *, sealed: bool = False) -> dict:
        metadata = path.stat()
        payload = path.read_bytes()
        value = {
            "path": str(path), "file_sha256": ATTESTOR.digest_bytes(payload),
            "bytes": len(payload), "mode": metadata.st_mode,
            "uid": metadata.st_uid, "gid": metadata.st_gid,
            "nlink": metadata.st_nlink, "device": metadata.st_dev,
            "inode": metadata.st_ino, "mtime_ns": metadata.st_mtime_ns,
            "ctime_ns": metadata.st_ctime_ns,
        }
        if sealed:
            value["body_sha256"] = ATTESTOR.strict_object(
                payload, "FIXTURE_INVALID")["body_sha256"]
        return value

    def _legacy_profile_record(self, path: Path) -> dict:
        value = self._profile_record(path)
        value["sha256"] = value.pop("file_sha256")
        return value

    def _write_profile_document(self, path: Path, body: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.parent.chmod(0o700)
        path.write_bytes(ATTESTOR.canonical_bytes(ATTESTOR.seal(body)))
        path.chmod(0o600)

    def _install_profile_fixture(self) -> None:
        dormant = b"D" * ATTESTOR.PAPER_PROFILE_DORMANT_BYTES
        watch = b"W" * ATTESTOR.PAPER_PROFILE_WATCH_BYTES
        hardened_runtime = b"P" * ATTESTOR.PAPER_RUNTIME_PROFILE_HARDENED_BYTES
        legacy_runtime = b"L" * ATTESTOR.PAPER_RUNTIME_PROFILE_LEGACY_BYTES
        identity = b'{"identities":[]}\n'
        values = {
            "PAPER_PROFILE_PATH": self.root / "trust" / "alpha.env",
            "PAPER_PROFILE_DORMANT_BACKUP_PATH":
                self.root / "profile-backup" / "alpha.env",
            "PAPER_PROFILE_FORWARD_RETAINED_PATH":
                self.root / "trust" / ".alpha.retained",
            "PAPER_PROFILE_FORWARD_PREIMAGE_PATH":
                self.root / "profile-backup" / "preimage-evidence.json",
            "PAPER_PROFILE_FORWARD_TRANSITION_RECEIPT_PATH":
                self.root / "profile-receipts" / "transition.json",
            "PAPER_PROFILE_DEPLOYMENT_RECEIPT_PATH":
                self.root / "profile-receipts" / "deployment.json",
            "PAPER_PROFILE_CANDIDATE_PATH":
                self.root / "trust" / ".alpha.candidate",
            "PAPER_PROFILE_RETIRED_WATCH_PATH":
                self.root / "handoff-state" / "retired-watch.env",
            "PAPER_RUNTIME_PROFILE_PATH":
                self.root / "trust" / "alpha.ib-paper.env",
            "PAPER_RUNTIME_PROFILE_CANDIDATE_PATH":
                self.root / "trust" / ".alpha.ib-paper.candidate",
            "PAPER_RUNTIME_PROFILE_BACKUP_PATH":
                self.root / "handoff-state" / "legacy-runtime-backup.env",
            "PAPER_RUNTIME_PROFILE_RETAINED_PATH":
                self.root / "handoff-state" / "retained-legacy-runtime.env",
            "IDENTITY_MANIFEST_PATH": self.root / "identity.json",
            "KILL_SWITCH_PATH": self.root / "paper-kill-switch",
            "GLOBAL_KILL_SWITCH_PATH": self.root / "global-kill-switch",
            "PAPER_CONTROL_GID": self.gid,
            "GLOBAL_PAPER_CONTROL_GID": self.gid,
            "PAPER_PROFILE_DORMANT_SHA256": ATTESTOR.digest_bytes(dormant),
            "PAPER_PROFILE_WATCH_SHA256": ATTESTOR.digest_bytes(watch),
            "PAPER_RUNTIME_PROFILE_HARDENED_SHA256":
                ATTESTOR.digest_bytes(hardened_runtime),
            "PAPER_RUNTIME_PROFILE_LEGACY_SHA256":
                ATTESTOR.digest_bytes(legacy_runtime),
            "DISABLED_IDENTITY_MANIFEST_SHA256":
                ATTESTOR.digest_bytes(identity),
        }
        for module in (ATTESTOR, PRODUCER):
            for name, value in values.items():
                setattr(module, name, value)
        for path, payload, mode in (
            (ATTESTOR.PAPER_PROFILE_PATH, dormant, 0o644),
            (ATTESTOR.PAPER_PROFILE_DORMANT_BACKUP_PATH, dormant, 0o600),
            (ATTESTOR.PAPER_PROFILE_FORWARD_RETAINED_PATH, dormant, 0o600),
            (ATTESTOR.PAPER_PROFILE_RETIRED_WATCH_PATH, watch, 0o600),
            (ATTESTOR.PAPER_RUNTIME_PROFILE_PATH, hardened_runtime, 0o644),
            (ATTESTOR.PAPER_RUNTIME_PROFILE_BACKUP_PATH, legacy_runtime,
             0o600),
            (ATTESTOR.PAPER_RUNTIME_PROFILE_RETAINED_PATH, legacy_runtime,
             0o600),
            (ATTESTOR.IDENTITY_MANIFEST_PATH, identity, 0o600),
            (ATTESTOR.KILL_SWITCH_PATH, b"engaged", 0o440),
            (ATTESTOR.GLOBAL_KILL_SWITCH_PATH, b"engaged", 0o440),
        ):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.parent.chmod(0o700)
            path.write_bytes(payload)
            path.chmod(mode)
        preimage = {
            field: None for field in ATTESTOR.PROFILE_PREIMAGE_FIELDS
            if field != "body_sha256"
        }
        preimage.update({
            "schema": ATTESTOR.PROFILE_PREIMAGE_SCHEMA, "version": 1,
            "status": ATTESTOR.PROFILE_PREIMAGE_STATUS, "round": 114,
            "domain": "alpha", "transition_token": "round114-transition",
            "created_at_ms": self.now_ms - 50_000,
            "backup": self._legacy_profile_record(
                ATTESTOR.PAPER_PROFILE_DORMANT_BACKUP_PATH),
            "paper_authorized": False, "live_authorized": False,
            "mutation_attempted": False, "direct_broker_access": False,
        })
        self._write_profile_document(
            ATTESTOR.PAPER_PROFILE_FORWARD_PREIMAGE_PATH, preimage)
        transition = {
            field: None for field in ATTESTOR.PROFILE_TRANSITION_FIELDS
            if field != "body_sha256"
        }
        transition.update({
            "schema": ATTESTOR.PROFILE_TRANSITION_SCHEMA, "version": 2,
            "status": ATTESTOR.PROFILE_TRANSITION_STATUS, "round": 114,
            "domain": "alpha", "transition_token": "round114-transition",
            "started_at_ms": self.now_ms - 45_000,
            "finished_at_ms": self.now_ms - 44_000,
            "target_path": str(ATTESTOR.PAPER_PROFILE_PATH),
            "backup_path": str(ATTESTOR.PAPER_PROFILE_DORMANT_BACKUP_PATH),
            "retained_target_path": str(
                ATTESTOR.PAPER_PROFILE_FORWARD_RETAINED_PATH),
            "profile_content_changed": True, "target_written": True,
            "target_replaced": True, "services_started": False,
            "services_stopped": False, "services_restarted": False,
            "campaign_launched": False, "paper_authorized": False,
            "live_authorized": False, "mutation_attempted": False,
            "direct_broker_access": False,
            "backup": self._legacy_profile_record(
                ATTESTOR.PAPER_PROFILE_DORMANT_BACKUP_PATH),
            "retained_target": self._legacy_profile_record(
                ATTESTOR.PAPER_PROFILE_FORWARD_RETAINED_PATH),
        })
        self._write_profile_document(
            ATTESTOR.PAPER_PROFILE_FORWARD_TRANSITION_RECEIPT_PATH, transition)
        transition_evidence = self._profile_record(
            ATTESTOR.PAPER_PROFILE_FORWARD_TRANSITION_RECEIPT_PATH,
            sealed=True)
        deployment = {
            field: None for field in ATTESTOR.PROFILE_DEPLOYMENT_FIELDS
            if field != "body_sha256"
        }
        deployment.update({
            "schema": ATTESTOR.PROFILE_DEPLOYMENT_SCHEMA, "version": 8,
            "status": ATTESTOR.PROFILE_DEPLOYMENT_STATUS, "round": 114,
            "domain": "alpha", "target_path": str(ATTESTOR.PAPER_PROFILE_PATH),
            "dormant_paper_to_watch_transition_receipt": {
                **transition_evidence,
                "sha256": transition_evidence["file_sha256"],
            },
        })
        deployment["dormant_paper_to_watch_transition_receipt"].pop(
            "file_sha256")
        self._write_profile_document(
            ATTESTOR.PAPER_PROFILE_DEPLOYMENT_RECEIPT_PATH, deployment)
        self.profile_restoration = {
            "schema": ATTESTOR.PROFILE_RESTORATION_SCHEMA, "version": 1,
            "status": ATTESTOR.PROFILE_RESTORATION_STATUS,
            "target": self._profile_record(ATTESTOR.PAPER_PROFILE_PATH),
            "dormant_backup": self._profile_record(
                ATTESTOR.PAPER_PROFILE_DORMANT_BACKUP_PATH),
            "forward_retained_dormant": self._profile_record(
                ATTESTOR.PAPER_PROFILE_FORWARD_RETAINED_PATH),
            "retired_watch": self._profile_record(
                ATTESTOR.PAPER_PROFILE_RETIRED_WATCH_PATH),
            "forward_transition_receipt": transition_evidence,
            "profile_deployment_receipt": self._profile_record(
                ATTESTOR.PAPER_PROFILE_DEPLOYMENT_RECEIPT_PATH, sealed=True),
            "forward_preimage_evidence": self._profile_record(
                ATTESTOR.PAPER_PROFILE_FORWARD_PREIMAGE_PATH, sealed=True),
            "candidate_path": str(ATTESTOR.PAPER_PROFILE_CANDIDATE_PATH),
            "retired_watch_path": str(
                ATTESTOR.PAPER_PROFILE_RETIRED_WATCH_PATH),
            "exchange_method": "RENAME_EXCHANGE",
            "forward_only_after_exchange": True,
            "restore_intent_record_sha256": ATTESTOR.digest_bytes(b"intent"),
            "restore_exchange_record_sha256": ATTESTOR.digest_bytes(
                b"exchange"),
        }
        self.runtime_profile_hardening = {
            "schema": ATTESTOR.PAPER_RUNTIME_PROFILE_HARDENING_SCHEMA,
            "version": 1,
            "status": ATTESTOR.PAPER_RUNTIME_PROFILE_HARDENING_STATUS,
            "target": self._profile_record(
                ATTESTOR.PAPER_RUNTIME_PROFILE_PATH),
            "legacy_backup": self._profile_record(
                ATTESTOR.PAPER_RUNTIME_PROFILE_BACKUP_PATH),
            "retained_legacy": self._profile_record(
                ATTESTOR.PAPER_RUNTIME_PROFILE_RETAINED_PATH),
            "candidate_path": str(
                ATTESTOR.PAPER_RUNTIME_PROFILE_CANDIDATE_PATH),
            "retained_legacy_path": str(
                ATTESTOR.PAPER_RUNTIME_PROFILE_RETAINED_PATH),
            "exchange_method": "RENAME_EXCHANGE",
            "forward_only_after_exchange": True,
            "harden_intent_record_sha256": ATTESTOR.digest_bytes(
                b"runtime-intent"),
            "harden_exchange_record_sha256": ATTESTOR.digest_bytes(
                b"runtime-exchange"),
        }

    def reference(self, name: str) -> dict[str, str]:
        document = self.documents[name]
        payload = self.paths[name].read_bytes()
        return {
            "path": str(self.paths[name]),
            "file_sha256": ATTESTOR.digest_bytes(payload),
            "body_sha256": document["body_sha256"],
        }

    def write(self, name: str, body: dict, *, sealed: bool = True) -> None:
        value = ATTESTOR.seal(body) if sealed else body
        self.paths[name].write_bytes(ATTESTOR.canonical_bytes(value))
        self.paths[name].chmod(0o600)
        self.documents[name] = value

    def rewrite(self, name: str) -> None:
        body = dict(self.documents[name])
        body.pop("body_sha256", None)
        self.write(name, body)

    def lease_reference(self) -> dict:
        directory = self.authority_directory.stat()
        lease = self.authority_lease.stat()
        return {
            "directory_path": str(self.authority_directory),
            "lease_path": str(self.authority_lease),
            "owner_path": str(self.authority_owner),
            "directory_device": directory.st_dev,
            "directory_inode": directory.st_ino,
            "directory_uid": directory.st_uid,
            "directory_gid": directory.st_gid,
            "directory_mode": stat.S_IMODE(directory.st_mode),
            "lease_device": lease.st_dev, "lease_inode": lease.st_ino,
            "lease_uid": lease.st_uid, "lease_gid": lease.st_gid,
            "lease_mode": stat.S_IMODE(lease.st_mode),
            "lease_size": lease.st_size,
            "held_exclusive": True, "boot_id": self.boot_id,
        }

    def reservation_reference(self) -> dict:
        document = self.documents["reservation"]
        metadata = self.authority_owner.stat()
        payload = self.authority_owner.read_bytes()
        return {
            "path": str(self.authority_owner),
            "file_sha256": ATTESTOR.digest_bytes(payload),
            "body_sha256": document["body_sha256"],
            "device": metadata.st_dev, "inode": metadata.st_ino,
            "uid": metadata.st_uid, "gid": metadata.st_gid,
            "mode": stat.S_IMODE(metadata.st_mode),
            "size": metadata.st_size, "mtime_ns": metadata.st_mtime_ns,
            "ctime_ns": metadata.st_ctime_ns,
        }

    def build(self) -> None:
        handoff = {
            "schema": ATTESTOR.HANDOFF_SCHEMA,
            "version": ATTESTOR.HANDOFF_VERSION,
            "status": "WATCH_RETIRED_HANDOFF_COMPLETE",
            "issued_at_ms": self.now_ms - 5_000,
            "expires_at_ms": self.now_ms + 120_000, **self.lineage(),
            "producer": self.executable_reference(self.handoff_producer),
            "production_mode": ATTESTOR.HANDOFF_PRODUCTION_MODE,
            "activation_receipt": self.generic_reference("activation"),
            "p1_audit_receipt": self.generic_reference("p1-audit"),
            "freeze_bundle": self.generic_reference("freeze-bundle"),
            "watch_units_inactive": True, "watch_authority_count": 0,
            "watch_socket_count": 0, "watch_timer_count": 0,
            "paper_units_inactive": True, "broker_deny_all": True,
            "kill_switch_engaged": True,
            "global_kill_switch_engaged": True, "identity_count": 0,
            "identity_manifest_sha256":
                ATTESTOR.DISABLED_IDENTITY_MANIFEST_SHA256,
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
            "schema": ATTESTOR.INTENT_SCHEMA, "version": 1,
            "status": "APPROVED", "issued_at_ms": self.now_ms - 4_000,
            "expires_at_ms": self.now_ms + 120_000, **self.lineage(),
            "intent_id": "zero-exposure-production-intent-a",
            "account_id_sha256": self.account_id,
            "production_mode": ATTESTOR.SNAPSHOT_PRODUCTION_MODE,
            "producer": self.executable_reference(self.snapshot_producer),
            "broker_policy_helper": self.executable_reference(
                self.broker_helper),
            "signature_verifier": self.executable_reference(self.verifier),
            "verification_key": self.executable_reference(self.public_key),
            "watch_handoff_receipt_path": str(self.paths["handoff"]),
            "challenge_output_path": str(self.paths["challenge"]),
            "signed_account_evidence_path": str(self.paths["evidence"]),
            "broker_snapshot_output_path": str(self.paths["broker"]),
            "account_snapshot_output_path": str(self.paths["account"]),
            "allow_fixed_read_only_host_observation": True,
            "allow_offline_signed_account_adaptation": True,
            **self.boundary(),
        }
        self.write("intent", intent)
        reservation = {
            "schema": ATTESTOR.RESERVATION_SCHEMA, "version": 1,
            "status": "ACTIVE", "issued_at_ms": self.now_ms - 3_000,
            "expires_at_ms": self.now_ms + 120_000, **self.lineage(),
            "reservation_id": self.reservation_id,
            "reservation_generation": 1,
            "predecessor_finalization_body_sha256": None,
            "prior_finalization_pointer_reference": None,
            "reservation_owner_kind": "ZERO_EXPOSURE_ADMISSION_EVIDENCE",
            "reservation_lifecycle": ATTESTOR.RESERVATION_LIFECYCLE,
            "next_consumer": ATTESTOR.RESERVATION_NEXT_CONSUMER,
            "boot_id": self.boot_id, "request_nonce": "a" * 64,
            "account_id_sha256": self.account_id,
            "producer": self.executable_reference(self.snapshot_producer),
            "production_mode": ATTESTOR.SNAPSHOT_PRODUCTION_MODE,
            "operator_intent_reference": self.reference("intent"),
            "watch_handoff_receipt": self.reference("handoff"),
            "challenge_output_path": str(self.paths["challenge"]),
            "signed_account_evidence_path": str(self.paths["evidence"]),
            "broker_snapshot_output_path": str(self.paths["broker"]),
            "account_snapshot_output_path": str(self.paths["account"]),
            "host_authority_lease": self.lease_reference(),
            "finalization_tombstone_path": str(
                self.authority_directory /
                ("finalized." + self.reservation_id + ".v1.json")),
            "finalization_current_pointer_path": str(
                self.authority_directory / "finalization-current.v1.json"),
            "finalization_tombstone_absent": True, **self.boundary(),
        }
        self.write("reservation", reservation)
        challenge = {
            "schema": ATTESTOR.CHALLENGE_SCHEMA, "version": 1,
            "status": "AWAITING_SIGNED_RESPONSE",
            "issued_at_ms": self.now_ms - 3_000,
            "expires_at_ms": self.now_ms + 120_000, **self.lineage(),
            "nonce": "a" * 64, "account_id_sha256": self.account_id,
            "producer": self.executable_reference(self.snapshot_producer),
            "production_mode": ATTESTOR.SNAPSHOT_PRODUCTION_MODE,
            "operator_intent_reference": self.reference("intent"),
            "watch_handoff_receipt": self.reference("handoff"),
            "host_authority_reservation": self.reservation_reference(),
            "signature_algorithm": ATTESTOR.SIGNATURE_ALGORITHM,
            "signature_verifier": self.executable_reference(self.verifier),
            "verification_key": self.executable_reference(self.public_key),
            "required_observation_authority":
                ATTESTOR.REMOTE_OBSERVATION_AUTHORITY,
            **self.boundary(),
        }
        self.write("challenge", challenge)
        payload = {
            "schema": ATTESTOR.SIGNED_EVIDENCE_PAYLOAD_SCHEMA, "version": 1,
            "status": "COMPLETE", "observed_at_ms": self.now_ms - 1_000,
            "expires_at_ms": self.now_ms + 120_000, **self.lineage(),
            "nonce": challenge["nonce"],
            "challenge_body_sha256":
                self.documents["challenge"]["body_sha256"],
            "account_id_sha256": self.account_id,
            "provider_id": "reviewed-remote-account-authority-a",
            "provider_request_id_sha256": ATTESTOR.digest_bytes(b"request"),
            "provider_response_sha256": ATTESTOR.digest_bytes(b"response"),
            "observation_authority":
                ATTESTOR.REMOTE_OBSERVATION_AUTHORITY,
            "query_effect": ATTESTOR.REMOTE_QUERY_EFFECT,
            "query_epoch": "remote-query-epoch-a",
            "query_fencing_generation": 7,
            "query_invocation_id": "remote-query-invocation-a",
            "read_only_authority": True, "authoritative": True,
            "account_complete": True, "snapshot_sha256": "",
            "active_order_id_sha256s": [], "positions": [],
            "gross_absolute_position": 0,
            "authorized_connector_count": 0, "end_flat": True,
            **self.boundary(),
        }
        payload["snapshot_sha256"] = ATTESTOR.account_state_sha256(payload)
        self._write_signed_evidence(payload)
        self._write_snapshots()

    def _sign(self, payload: dict) -> bytes:
        payload_path = self.root / "payload-to-sign.json"
        signature_path = self.root / "payload-signature.bin"
        payload_path.write_bytes(ATTESTOR.canonical_bytes(payload))
        payload_path.chmod(0o600)
        subprocess.run(
            ("/usr/bin/openssl", "pkeyutl", "-sign", "-inkey",
             str(self.private_key), "-rawin", "-in", str(payload_path),
             "-out", str(signature_path)), check=True,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL)
        signature = signature_path.read_bytes()
        self.assert_signature_size(signature)
        payload_path.unlink()
        signature_path.unlink()
        return signature

    @staticmethod
    def assert_signature_size(signature: bytes) -> None:
        if len(signature) != 64:
            raise AssertionError("invalid Ed25519 signature fixture")

    def _write_signed_evidence(
        self, payload: dict, *, signature: bytes | None = None,
    ) -> None:
        signature = self._sign(payload) if signature is None else signature
        envelope = {
            "schema": ATTESTOR.SIGNED_EVIDENCE_ENVELOPE_SCHEMA,
            "version": 1, "payload": payload,
            "signature_base64": base64.b64encode(signature).decode("ascii"),
        }
        self.write("evidence", envelope, sealed=False)

    def _write_snapshots(self) -> None:
        intent_ref = self.reference("intent")
        handoff_ref = self.reference("handoff")
        challenge_ref = self.reference("challenge")
        evidence_payload = self.documents["evidence"]["payload"]
        evidence_bytes = ATTESTOR.canonical_bytes(evidence_payload)
        evidence_file = self.paths["evidence"].read_bytes()
        signature = base64.b64decode(
            self.documents["evidence"]["signature_base64"])
        producer_ref = self.executable_reference(self.snapshot_producer)
        common = {
            "version": 1, "expires_at_ms": self.now_ms + 120_000,
            **self.lineage(), "producer": producer_ref,
            "production_mode": ATTESTOR.SNAPSHOT_PRODUCTION_MODE,
            "operator_intent_reference": intent_ref,
            "watch_handoff_receipt": handoff_ref,
            "challenge_reference": challenge_ref,
            "host_authority_reservation": self.reservation_reference(),
        }
        broker = {
            "schema": ATTESTOR.BROKER_SNAPSHOT_SCHEMA, "status": "PASS",
            "observed_at_ms": self.now_ms - 500, **common,
            "request_nonce": self.documents["challenge"]["nonce"],
            "account_id_sha256": self.account_id,
            "signed_account_payload_sha256":
                ATTESTOR.digest_bytes(evidence_bytes),
            "observation_method": ATTESTOR.BROKER_OBSERVATION_METHOD,
            "broker_policy_helper": self.executable_reference(
                self.broker_helper),
            "observer_id": ATTESTOR.BROKER_OBSERVER_ID,
            "observation_complete": True, "broker_deny_all": True,
            "policy_sha256": ATTESTOR.digest_bytes(b"deny-all-policy"),
            "authorized_connectors": 0, "authorized_uids": [],
            "broker_socket_count": 0, "broker_process_count": 0,
            "credential_exposure_count": 0, "paper_units_inactive": True,
            "kill_switch_engaged": True,
            "protected_broker_ports": list(ATTESTOR.PROTECTED_BROKER_PORTS),
            "process_inventory_complete": True,
            "socket_inventory_complete": True,
            "credential_inventory_complete": True,
            "host_authority_lease": self.lease_reference(),
            **self.boundary(),
        }
        self.write("broker", broker)
        account = {
            "schema": ATTESTOR.ACCOUNT_SNAPSHOT_SCHEMA, "status": "COMPLETE",
            "observed_at_ms": evidence_payload["observed_at_ms"], **common,
            "signed_evidence_reference": {
                "path": str(self.paths["evidence"]),
                "file_sha256": ATTESTOR.digest_bytes(evidence_file),
                "signed_payload_sha256":
                    ATTESTOR.digest_bytes(evidence_bytes),
            },
            "signature_verification": {
                "algorithm": ATTESTOR.SIGNATURE_ALGORITHM,
                "public_key": self.executable_reference(self.public_key),
                "verifier": self.executable_reference(self.verifier),
                "signature_sha256": ATTESTOR.digest_bytes(signature),
                "signed_payload_sha256":
                    ATTESTOR.digest_bytes(evidence_bytes),
            },
            "request_nonce": self.documents["challenge"]["nonce"],
            "provider_id": evidence_payload["provider_id"],
            "account_id_sha256": evidence_payload["account_id_sha256"],
            "provider_request_id_sha256":
                evidence_payload["provider_request_id_sha256"],
            "provider_response_sha256":
                evidence_payload["provider_response_sha256"],
            "observer_id": ATTESTOR.ACCOUNT_OBSERVER_ID,
            "observation_authority":
                ATTESTOR.REMOTE_OBSERVATION_AUTHORITY,
            "query_effect": ATTESTOR.REMOTE_QUERY_EFFECT,
            "query_epoch": evidence_payload["query_epoch"],
            "query_fencing_generation":
                evidence_payload["query_fencing_generation"],
            "query_invocation_id": evidence_payload["query_invocation_id"],
            "read_only_authority": True, "authoritative": True,
            "account_complete": True,
            "snapshot_sha256": evidence_payload["snapshot_sha256"],
            "active_order_id_sha256s": list(
                evidence_payload["active_order_id_sha256s"]),
            "positions": [dict(value)
                          for value in evidence_payload["positions"]],
            "gross_absolute_position":
                evidence_payload["gross_absolute_position"],
            "authorized_connector_count":
                evidence_payload["authorized_connector_count"],
            "end_flat": evidence_payload["end_flat"], **self.boundary(),
        }
        self.write("account", account)

    def update_remote_state(self, **changes) -> None:
        payload = dict(self.documents["evidence"]["payload"])
        payload.update(changes)
        payload["snapshot_sha256"] = ATTESTOR.account_state_sha256(payload)
        self._write_signed_evidence(payload)
        self._write_snapshots()

    def replace_with_wrong_signature(self) -> None:
        payload = dict(self.documents["evidence"]["payload"])
        good = self._sign(payload)
        wrong = bytes([good[0] ^ 1]) + good[1:]
        self._write_signed_evidence(payload, signature=wrong)
        self._write_snapshots()

    def refresh_reservation_chain(self, *, resign_evidence: bool = True) -> None:
        """Rebind all downstream artifacts after an intentional upstream edit."""

        reservation = dict(self.documents["reservation"])
        reservation.pop("body_sha256", None)
        reservation["producer"] = self.executable_reference(
            self.snapshot_producer)
        reservation["operator_intent_reference"] = self.reference("intent")
        reservation["watch_handoff_receipt"] = self.reference("handoff")
        reservation["request_nonce"] = self.documents["challenge"]["nonce"]
        self.write("reservation", reservation)

        challenge = dict(self.documents["challenge"])
        challenge.pop("body_sha256", None)
        challenge["producer"] = self.executable_reference(
            self.snapshot_producer)
        challenge["operator_intent_reference"] = self.reference("intent")
        challenge["watch_handoff_receipt"] = self.reference("handoff")
        challenge["host_authority_reservation"] = (
            self.reservation_reference())
        self.write("challenge", challenge)
        if resign_evidence:
            payload = dict(self.documents["evidence"]["payload"])
            payload["nonce"] = challenge["nonce"]
            payload["challenge_body_sha256"] = (
                self.documents["challenge"]["body_sha256"])
            self._write_signed_evidence(payload)
        self._write_snapshots()

    def advance_to_generation_two(self) -> Path:
        """Finalize gen1 and create an exact active gen2 lineage fixture."""

        first = dict(self.documents["reservation"])
        first_reference = self.reservation_reference()
        tombstone_path = self.authority_directory / (
            "finalized." + first["reservation_id"] + ".v1.json")
        recovery = {
            "first_observed_at_ms": self.now_ms - 100,
            "second_observed_at_ms": self.now_ms,
            "policy_sha256": ATTESTOR.digest_bytes(b"deny-all-policy"),
            "authorized_connectors": 0, "authorized_uids": [],
            "broker_socket_count": 0, "broker_process_count": 0,
            "credential_exposure_count": 0, "paper_units_inactive": True,
            "kill_switch_engaged": True,
            "process_inventory_complete": True,
            "socket_inventory_complete": True,
            "credential_inventory_complete": True,
        }
        tombstone = ATTESTOR.seal({
            "schema": ATTESTOR.RESERVATION_FINALIZATION_SCHEMA,
            "version": 1, "status": "ABORTED",
            "finalized_at_ms": self.now_ms, **self.lineage(),
            "reservation_id": first["reservation_id"],
            "reservation_generation": 1,
            "predecessor_finalization_body_sha256": None,
            "prior_finalization_pointer_reference": None,
            "boot_id": self.boot_id,
            "reservation_reference": first_reference,
            "candidate_reference": None,
            "zero_exposure_receipt_reference": None,
            "host_authority_lease": self.lease_reference(),
            "recovery_observation": recovery,
            "owner_present_at_tombstone_commit": True,
            "owner_removal_required_after_commit": True,
            "finalization_order": ATTESTOR.RESERVATION_FINALIZATION_ORDER,
            "recovery_reason": "RESERVATION_EXPIRED", **self.boundary(),
        })
        tombstone_path.write_bytes(ATTESTOR.canonical_bytes(tombstone))
        tombstone_path.chmod(0o600)
        tombstone_binding = ATTESTOR._bind_document(
            tombstone_path, ATTESTOR.RESERVATION_FINALIZATION_FIELDS,
            ATTESTOR.RESERVATION_FINALIZATION_SCHEMA, "FIXTURE_INVALID",
            expected_uid=self.uid, expected_gid=self.gid)
        pointer_path = (
            self.authority_directory / "finalization-current.v1.json")
        pointer = ATTESTOR._expected_pointer_document(tombstone_binding)
        pointer_path.write_bytes(ATTESTOR.canonical_bytes(pointer))
        pointer_path.chmod(0o600)

        second = dict(first)
        second.pop("body_sha256", None)
        second["reservation_id"] = "zero-exposure-" + "2" * 48
        second["reservation_generation"] = 2
        second["predecessor_finalization_body_sha256"] = (
            tombstone["body_sha256"])
        second["prior_finalization_pointer_reference"] = {
            "path": str(pointer_path),
            "file_sha256": ATTESTOR.digest_bytes(pointer_path.read_bytes()),
            "body_sha256": pointer["body_sha256"],
        }
        second["finalization_tombstone_path"] = str(
            self.authority_directory /
            ("finalized." + second["reservation_id"] + ".v1.json"))
        self.write("reservation", second)
        self.refresh_reservation_chain()
        return pointer_path

    def build_terminal_bundle(
        self, *, provider_wall: tuple[int, int, int, int] | None = None,
        query_started_after_challenge: bool = True,
    ) -> dict[str, Path]:
        class Reference:
            def __init__(self, path: Path):
                self.reference = {
                    "path": str(path),
                    "file_sha256": PRODUCER.digest_bytes(path.read_bytes()),
                }

        owner_id = ATTESTOR.digest_bytes(b"owner")
        owner_account = b"DU123"
        owner_domain = b"PAPER:alpha"
        owner_canonical = (
            owner_id + "\t1\t" + owner_account.hex() + "\t" +
            owner_domain.hex() + "\n").encode("ascii")
        paths = {
            name: self.root / (name + ".json") for name in (
                "terminal_cutoff", "terminal_trust", "terminal_challenge",
                "terminal_evidence", "terminal_witness")
        }
        paths["terminal_request"] = self.root / "terminal-request.bin"
        paths["terminal_response"] = self.root / "terminal-response.bin"

        def write(path: Path, document: dict) -> None:
            path.write_bytes(ATTESTOR.canonical_bytes(document))
            path.chmod(0o600)

        boundary = self.boundary()
        cutoff = ATTESTOR.seal({
            "schema": ATTESTOR.TRANSPORT_CUTOFF_SCHEMA, "version": 1,
            "status": ATTESTOR.TERMINAL_CUTOFF_STATUS,
            "completed_at_ms": self.now_ms - 5_000,
            "completed_monotonic_ns": 1_000_000, "round": 114,
            "domain": "alpha", "campaign_id": self.campaign,
            "source_baseline_sha256": self.source, "cycle_id": "cycle-a",
            "recovery_id": "recovery-a", "finalization_id": "finalization-a",
            "boot_id": self.boot_id, "service_pid": 1234,
            "service_start_ticks": 5678,
            "broker_socket_identity_sha256": ATTESTOR.digest_bytes(b"socket"),
            "account_id_sha256": ATTESTOR.digest_bytes(owner_account),
            "owner_ids": [owner_id],
            "owner_set_sha256": ATTESTOR.digest_bytes(owner_canonical),
            "owner_set_canonical_hex": owner_canonical.hex(), "owner_count": 1,
            "execution_service_epoch": "execution-epoch-a",
            "execution_service_fencing_generation": 17,
            "mutation_fence_generation": 19,
            "known_mutation_command_set_sha256":
                ATTESTOR.digest_bytes(b"mutations"),
            "known_mutation_command_count": 1,
            "known_correlation_set_sha256":
                ATTESTOR.digest_bytes(b"correlations"),
            "known_correlation_count": 1, "egress_policy_generation": 23,
            "egress_policy_sha256": ATTESTOR.digest_bytes(b"deny-all"),
            "authorized_connectors": 0, "authorized_uids": [],
            "broker_socket_count": 0, "broker_process_count": 0,
            "credential_exposure_count": 0,
            "process_inventory_complete": True,
            "socket_inventory_complete": True,
            "credential_inventory_complete": True,
            "mutation_gate_closed": True, "reconnect_permitted": False,
            **boundary,
        })
        trust = ATTESTOR.seal({
            "schema": ATTESTOR.TERMINAL_PROVIDER_TRUST_POLICY_SCHEMA,
            "version": 1, "status": "ACTIVE",
            "provider_id": ATTESTOR.TERMINAL_PROVIDER_ID,
            "provider_key_sha256":
                ATTESTOR.digest_bytes(self.public_key.read_bytes()),
            "provider_capability": ATTESTOR.TERMINAL_PROVIDER_CAPABILITY,
            "atomic_account_supported": True,
            "causal_watermark_supported": True,
            "challenge_bound_query_supported": True,
            "read_only_authority_required": True,
            "mutation_attempted": False, **boundary,
        })
        write(paths["terminal_cutoff"], cutoff)
        write(paths["terminal_trust"], trust)
        PRODUCER.TERMINAL_PROVIDER_TRUST_POLICY_BODY_SHA256 = \
            trust["body_sha256"]
        ATTESTOR.TERMINAL_PROVIDER_TRUST_POLICY_BODY_SHA256 = \
            trust["body_sha256"]
        cutoff_binding = PRODUCER._bind_document(
            paths["terminal_cutoff"], PRODUCER.TRANSPORT_CUTOFF_FIELDS,
            PRODUCER.TRANSPORT_CUTOFF_SCHEMA, "FIXTURE_INVALID",
            expected_uid=self.uid, expected_gid=self.gid)
        trust_binding = PRODUCER._bind_document(
            paths["terminal_trust"],
            PRODUCER.TERMINAL_PROVIDER_TRUST_POLICY_FIELDS,
            PRODUCER.TERMINAL_PROVIDER_TRUST_POLICY_SCHEMA, "FIXTURE_INVALID",
            expected_uid=self.uid, expected_gid=self.gid)
        context = PRODUCER.ProductionContext.__new__(PRODUCER.ProductionContext)
        context.expected_uid = self.uid
        context.expected_gid = self.gid
        context.producer = Reference(self.snapshot_producer)
        context.broker_helper = Reference(self.broker_helper)
        context.signature_verifier = Reference(self.verifier)
        context.verification_key = Reference(self.public_key)
        context._certification_secret = object()
        context._lease_certification_secret = object()
        lease = mock.Mock(boot_id=self.boot_id)
        challenge = PRODUCER.build_terminal_challenge(
            cutoff=cutoff_binding, trust_policy=trust_binding,
            context=context, lease=lease, now_ms=self.now_ms - 4_000,
            now_monotonic_ns=2_000_000, nonce="b" * 64)
        write(paths["terminal_challenge"], challenge)
        write(self.authority_owner, challenge)
        challenge_binding = PRODUCER._bind_document(
            paths["terminal_challenge"], PRODUCER.TERMINAL_CHALLENGE_FIELDS,
            PRODUCER.TERMINAL_CHALLENGE_SCHEMA, "FIXTURE_INVALID",
            expected_uid=self.uid, expected_gid=self.gid)
        paths["terminal_request"].write_bytes(b"provider request\n")
        paths["terminal_response"].write_bytes(b"provider response\n")
        paths["terminal_request"].chmod(0o600)
        paths["terminal_response"].chmod(0o600)
        request = PRODUCER._bind_terminal_provider_artifact(
            paths["terminal_request"], "FIXTURE_INVALID",
            expected_uid=self.uid, expected_gid=self.gid)
        response = PRODUCER._bind_terminal_provider_artifact(
            paths["terminal_response"], "FIXTURE_INVALID",
            expected_uid=self.uid, expected_gid=self.gid)
        provider_wall = provider_wall or (
            self.now_ms - 3_000, self.now_ms - 2_000,
            self.now_ms - 1_000, self.now_ms + 60_000)
        payload = {
            field: None for field in
            PRODUCER.TERMINAL_SIGNED_EVIDENCE_PAYLOAD_FIELDS
        }
        payload.update({
            "schema": PRODUCER.TERMINAL_SIGNED_EVIDENCE_SCHEMA, "version": 1,
            "status": PRODUCER.TERMINAL_SIGNED_EVIDENCE_STATUS,
            "query_started_at_ms": provider_wall[0],
            "query_started_monotonic_ns": 3_000_000,
            "observed_at_ms": provider_wall[1],
            "observed_monotonic_ns": 4_000_000,
            "query_completed_at_ms": provider_wall[2],
            "query_completed_monotonic_ns": 5_000_000,
            "expires_at_ms": provider_wall[3],
            "round": 114, "domain": "alpha", "campaign_id": self.campaign,
            "source_baseline_sha256": self.source, "cycle_id": "cycle-a",
            "recovery_id": "recovery-a", "finalization_id": "finalization-a",
            "nonce": challenge["nonce"],
            "challenge_body_sha256": challenge["body_sha256"],
            "transport_cutoff_body_sha256": cutoff["body_sha256"],
            "provider_id": PRODUCER.TERMINAL_PROVIDER_ID,
            "provider_trust_policy_sha256": trust["body_sha256"],
            "provider_key_sha256": trust["provider_key_sha256"],
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
            "query_started_after_challenge":
                query_started_after_challenge,
            "snapshot_consistency": "ATOMIC_ACCOUNT",
            "consistency_token_sha256": ATTESTOR.digest_bytes(b"token"),
            "consistency_cutoff_body_sha256": cutoff["body_sha256"],
            "consistency_known_mutation_command_set_sha256":
                cutoff["known_mutation_command_set_sha256"],
            "consistency_known_correlation_set_sha256":
                cutoff["known_correlation_set_sha256"],
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
            "completed_order_id_sha256s": [ATTESTOR.digest_bytes(b"order")],
            "execution_id_sha256s": [ATTESTOR.digest_bytes(b"execution")],
            "positions": [], "cash_fx_exposures": [],
            "gross_absolute_position": "0", "gross_fx_exposure": "0",
            "gross_risk": "0", "settled_mutation_command_count": 1,
            "unknown_mutation_command_count": 0,
            "unresolved_mutation_command_count": 0,
            "read_only_authority": True, "authoritative": True,
            "account_complete": True, "mutation_attempted": False,
            **{field: False for field in PRODUCER.BOUNDARY_FIELDS},
        })
        for field in (
            "domain", "campaign_id", "source_baseline_sha256", "cycle_id",
            "recovery_id", "finalization_id", "boot_id", "service_pid",
            "service_start_ticks", "broker_socket_identity_sha256",
            "account_id_sha256", "owner_set_sha256",
            "owner_set_canonical_hex", "owner_count", "execution_service_epoch",
            "execution_service_fencing_generation", "mutation_fence_generation",
            "known_mutation_command_set_sha256", "known_mutation_command_count",
            "known_correlation_set_sha256", "known_correlation_count",
            "egress_policy_generation", "egress_policy_sha256"):
            payload[field] = challenge[field]
        signature = self._sign(payload)
        envelope = {
            "schema": PRODUCER.SIGNED_EVIDENCE_ENVELOPE_SCHEMA, "version": 1,
            "payload": payload,
            "signature_base64": base64.b64encode(signature).decode("ascii"),
        }
        write(paths["terminal_evidence"], envelope)
        evidence_binding = PRODUCER._bind_unsealed_document(
            paths["terminal_evidence"], "FIXTURE_INVALID",
            expected_uid=self.uid, expected_gid=self.gid)
        evidence = PRODUCER.parse_terminal_signed_evidence(evidence_binding)
        certification = PRODUCER.SignatureCertification(
            evidence.payload_sha256, evidence.signature_sha256,
            context._certification_secret)
        observation = lambda when: PRODUCER.HostObservation(
            when, cutoff["egress_policy_sha256"], 0, tuple(), 0, 0, 0,
            True, True, True, True, True,
            cutoff["egress_policy_generation"])
        witness = PRODUCER.assemble_terminal_witness(
            cutoff=cutoff_binding, challenge=challenge_binding,
            trust_policy=trust_binding, evidence=evidence,
            provider_request=request, provider_response=response,
            context=context, certification=certification,
            first_observation=observation(self.now_ms - 500),
            second_observation=observation(self.now_ms - 200),
            received_at_ms=self.now_ms - 800,
            received_monotonic_ns=5_500_000,
            verified_at_ms=self.now_ms, verified_monotonic_ns=6_000_000)
        write(paths["terminal_witness"], witness)
        return paths

    def run(self, *, expected_campaign: str | None = None,
            output: Path | None = None) -> dict:
        with mock.patch.object(
                ATTESTOR, "HOST_AUTHORITY_DIRECTORY",
                self.authority_directory), mock.patch.object(
                ATTESTOR, "HOST_AUTHORITY_LEASE_PATH",
                self.authority_lease), mock.patch.object(
                ATTESTOR, "HOST_AUTHORITY_OWNER_PATH",
                self.authority_owner), mock.patch.object(
                ATTESTOR, "BOOT_ID_PATH", self.boot_id_path):
            return ATTESTOR.attest_and_publish(
                operator_intent_path=self.paths["intent"],
                handoff_path=self.paths["handoff"],
                challenge_path=self.paths["challenge"],
                signed_evidence_path=self.paths["evidence"],
                broker_snapshot_path=self.paths["broker"],
                account_snapshot_path=self.paths["account"],
                expected_source=self.source, expected_domain="alpha",
                expected_campaign=expected_campaign or self.campaign,
                output_path=output or self.paths["output"],
                production_mode=ATTESTOR.PRODUCTION_MODE,
                expected_uid=self.uid, expected_gid=self.gid,
                now_ms=self.now_ms, _run_token=ATTESTOR.CLI_RUN_TOKEN)


class PaperZeroExposureAttestorTests(unittest.TestCase):
    def setUp(self):
        global ATTESTOR

        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.installed_attestor = (
            self.root / "hepta-p1-paper-zero-exposure-attestor")
        shutil.copyfile(MODULE_PATH, self.installed_attestor)
        self.installed_attestor.chmod(0o755)
        self.source_attestor = ATTESTOR
        self.installed_module_name = (
            "hepta_zero_attestor_installed_" + str(id(self)))
        ATTESTOR = load_module(
            self.installed_module_name, self.installed_attestor)
        self.addCleanup(self.restore_attestor_module)
        self.now_ms = 2_000_000_000_000
        self.fixture = EvidenceFixture(self.root, self.now_ms)
        replacements = {
            "INSTALLED_EXECUTABLE": self.fixture.attestor,
            "SNAPSHOT_PRODUCER_EXECUTABLE": self.fixture.snapshot_producer,
            "HANDOFF_EXECUTABLE": self.fixture.handoff_producer,
            "BROKER_POLICY_HELPER": self.fixture.broker_helper,
            "SIGNATURE_VERIFIER": self.fixture.verifier,
            "VERIFICATION_KEY": self.fixture.public_key,
            "HOST_AUTHORITY_DIRECTORY": self.fixture.authority_directory,
            "HOST_AUTHORITY_LEASE_PATH": self.fixture.authority_lease,
            "HOST_AUTHORITY_OWNER_PATH": self.fixture.authority_owner,
            "BOOT_ID_PATH": self.fixture.boot_id_path,
            "CURRENT_BOUNDARY_VALIDATOR":
                lambda generation, digest: (
                    None if type(generation) is int and generation > 0 and
                    isinstance(digest, str) and digest.startswith("sha256:")
                    else (_ for _ in ()).throw(
                        ATTESTOR.AttestationError(
                            "TERMINAL_WITNESS_CURRENT_BOUNDARY_INVALID"))),
        }
        self.patchers = [mock.patch.object(ATTESTOR, name, value)
                         for name, value in replacements.items()]
        for patcher in self.patchers:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.fixture.build()

    def restore_attestor_module(self) -> None:
        global ATTESTOR

        ATTESTOR = self.source_attestor
        sys.modules.pop(self.installed_module_name, None)

    def assertReason(self, expected: str, callback) -> None:
        with self.assertRaises(ATTESTOR.AttestationError) as caught:
            callback()
        self.assertEqual(caught.exception.reason, expected)

    def test_directory_identity_ignores_legitimate_child_churn(self):
        before = mock.Mock(
            st_dev=1, st_ino=2, st_mode=stat.S_IFDIR | 0o700,
            st_nlink=2, st_uid=self.fixture.uid, st_gid=self.fixture.gid)
        after = mock.Mock(
            st_dev=1, st_ino=2, st_mode=stat.S_IFDIR | 0o700,
            st_nlink=99, st_uid=self.fixture.uid, st_gid=self.fixture.gid)
        self.assertEqual(
            ATTESTOR._directory_identity(before),
            ATTESTOR._directory_identity(after))

    def test_contract_fields_match_current_snapshot_producer_exactly(self):
        self.assertEqual(ATTESTOR.HANDOFF_SCHEMA, PRODUCER.HANDOFF_SCHEMA)
        for name in (
            "TRANSPORT_CUTOFF_FIELDS",
            "TERMINAL_PROVIDER_TRUST_POLICY_FIELDS",
            "TERMINAL_CHALLENGE_FIELDS",
            "TERMINAL_SIGNED_EVIDENCE_PAYLOAD_FIELDS",
            "TERMINAL_WITNESS_FIELDS",
        ):
            self.assertEqual(getattr(ATTESTOR, name), getattr(PRODUCER, name))
        self.assertEqual(ATTESTOR.HANDOFF_VERSION, PRODUCER.HANDOFF_VERSION)
        self.assertEqual(ATTESTOR.HANDOFF_FIELDS, PRODUCER.HANDOFF_FIELDS)
        self.assertEqual(ATTESTOR.HANDOFF_SCHEMA, HANDOFF.RECEIPT_SCHEMA)
        self.assertEqual(ATTESTOR.HANDOFF_FIELDS, HANDOFF.RECEIPT_FIELDS)
        self.assertEqual(
            ATTESTOR.PROFILE_RESTORATION_FIELDS,
            HANDOFF.PROFILE_RESTORATION_FIELDS)
        self.assertEqual(
            ATTESTOR.PROFILE_FILE_EVIDENCE_FIELDS,
            HANDOFF.PROFILE_FILE_EVIDENCE_FIELDS)
        self.assertEqual(
            ATTESTOR.PROFILE_SEALED_EVIDENCE_FIELDS,
            HANDOFF.PROFILE_SEALED_EVIDENCE_FIELDS)
        self.assertEqual(
            ATTESTOR.PAPER_RUNTIME_PROFILE_HARDENING_FIELDS,
            HANDOFF.PAPER_RUNTIME_PROFILE_HARDENING_FIELDS)
        self.assertEqual(ATTESTOR.BROKER_SNAPSHOT_FIELDS,
                         PRODUCER.BROKER_SNAPSHOT_FIELDS)
        self.assertEqual(ATTESTOR.ACCOUNT_SNAPSHOT_FIELDS,
                         PRODUCER.ACCOUNT_SNAPSHOT_FIELDS)
        self.assertEqual(ATTESTOR.SIGNED_EVIDENCE_PAYLOAD_FIELDS,
                         PRODUCER.SIGNED_EVIDENCE_PAYLOAD_FIELDS)
        self.assertEqual(ATTESTOR.RESERVATION_FIELDS,
                         PRODUCER.RESERVATION_FIELDS)
        self.assertEqual(ATTESTOR.RESERVATION_FINALIZATION_FIELDS,
                         PRODUCER.RESERVATION_FINALIZATION_FIELDS)
        self.assertEqual(ATTESTOR.RESERVATION_CURRENT_POINTER_FIELDS,
                         PRODUCER.RESERVATION_CURRENT_POINTER_FIELDS)
        self.assertEqual(ATTESTOR.HOST_AUTHORITY_LEASE_FIELDS,
                         PRODUCER.HOST_AUTHORITY_LEASE_FIELDS)

    def _validate_terminal_bundle(self, paths):
        return ATTESTOR.validate_terminal_witness_bundle(
            transport_cutoff_path=paths["terminal_cutoff"],
            provider_trust_policy_path=paths["terminal_trust"],
            challenge_path=paths["terminal_challenge"],
            signed_evidence_path=paths["terminal_evidence"],
            provider_request_path=paths["terminal_request"],
            provider_response_path=paths["terminal_response"],
            witness_path=paths["terminal_witness"],
            expected_source=self.fixture.source,
            expected_campaign=self.fixture.campaign, expected_cycle="cycle-a",
            expected_recovery="recovery-a",
            expected_finalization="finalization-a",
            expected_uid=self.fixture.uid, expected_gid=self.fixture.gid,
            now_ms=self.now_ms + 100, now_monotonic_ns=6_500_000)

    def test_terminal_witness_bundle_is_independently_verified(self):
        paths = self.fixture.build_terminal_bundle()
        witness = self._validate_terminal_bundle(paths)
        self.assertEqual(witness["status"], ATTESTOR.TERMINAL_WITNESS_STATUS)
        self.assertEqual(witness["terminal_proof_kind"],
                         ATTESTOR.TERMINAL_PROOF_KIND)
        self.assertFalse(witness["paper_authorized"])

    def test_terminal_witness_accepts_independent_skewed_provider_clock(self):
        paths = self.fixture.build_terminal_bundle(
            provider_wall=(10_000, 11_000, 12_000, 72_000))
        witness = self._validate_terminal_bundle(paths)
        self.assertEqual(witness["query_completed_at_ms"], 12_000)

    def test_terminal_witness_rejects_false_challenge_causal_attestation(self):
        paths = self.fixture.build_terminal_bundle(
            query_started_after_challenge=False)
        self.assertReason(
            "TERMINAL_WITNESS_SIGNED_ACCOUNT_EVIDENCE_INVALID",
            lambda: self._validate_terminal_bundle(paths))

    def test_terminal_witness_rejects_raw_provider_artifact_drift(self):
        paths = self.fixture.build_terminal_bundle()
        paths["terminal_response"].write_bytes(b"different response\n")
        paths["terminal_response"].chmod(0o600)
        self.assertReason(
            "TERMINAL_WITNESS_PROVIDER_ARTIFACT_MISMATCH",
            lambda: self._validate_terminal_bundle(paths))

    def test_terminal_witness_rejects_nonce_watermark_and_host_drift(self):
        for field, value in (
            ("nonce", "c" * 64),
            ("consistency_dominates_all_mutations", False),
            ("host_broker_socket_count", 1),
        ):
            with self.subTest(field=field):
                fixture = self._fresh_fixture()
                paths = fixture.build_terminal_bundle()
                if field in fixture.documents.get("terminal_evidence", {}):
                    raise AssertionError("unexpected fixture document alias")
                if field in {"nonce", "consistency_dominates_all_mutations"}:
                    envelope = ATTESTOR.strict_object(
                        paths["terminal_evidence"].read_bytes(),
                        "FIXTURE_INVALID")
                    payload = dict(envelope["payload"])
                    payload[field] = value
                    signature = fixture._sign(payload)
                    envelope["payload"] = payload
                    envelope["signature_base64"] = base64.b64encode(
                        signature).decode("ascii")
                    paths["terminal_evidence"].write_bytes(
                        ATTESTOR.canonical_bytes(envelope))
                else:
                    witness = ATTESTOR.strict_object(
                        paths["terminal_witness"].read_bytes(),
                        "FIXTURE_INVALID")
                    witness[field] = value
                    witness = ATTESTOR.seal({
                        key: item for key, item in witness.items()
                        if key != "body_sha256"})
                    paths["terminal_witness"].write_bytes(
                        ATTESTOR.canonical_bytes(witness))
                with mock.patch.object(
                        ATTESTOR, "HOST_AUTHORITY_DIRECTORY",
                        fixture.authority_directory), mock.patch.object(
                        ATTESTOR, "HOST_AUTHORITY_LEASE_PATH",
                        fixture.authority_lease), mock.patch.object(
                        ATTESTOR, "HOST_AUTHORITY_OWNER_PATH",
                        fixture.authority_owner), mock.patch.object(
                        ATTESTOR, "BOOT_ID_PATH", fixture.boot_id_path), \
                        self.assertRaises(ATTESTOR.AttestationError):
                    ATTESTOR.validate_terminal_witness_bundle(
                        transport_cutoff_path=paths["terminal_cutoff"],
                        provider_trust_policy_path=paths["terminal_trust"],
                        challenge_path=paths["terminal_challenge"],
                        signed_evidence_path=paths["terminal_evidence"],
                        provider_request_path=paths["terminal_request"],
                        provider_response_path=paths["terminal_response"],
                        witness_path=paths["terminal_witness"],
                        expected_source=fixture.source,
                        expected_campaign=fixture.campaign,
                        expected_cycle="cycle-a", expected_recovery="recovery-a",
                        expected_finalization="finalization-a",
                        expected_uid=fixture.uid, expected_gid=fixture.gid,
                        now_ms=self.now_ms + 100,
                        now_monotonic_ns=6_500_000)
    def test_handoff_v1_and_restoration_tamper_are_rejected(self):
        legacy = dict(self.fixture.documents["handoff"])
        legacy.pop("body_sha256")
        legacy["schema"] = "hepta.p1-watch-to-paper-handoff-receipt.v1"
        legacy["version"] = 1
        legacy = ATTESTOR.seal(legacy)
        self.assertReason(
            "ZERO_EXPOSURE_HANDOFF_INVALID",
            lambda: ATTESTOR._sealed(
                legacy, ATTESTOR.HANDOFF_FIELDS, ATTESTOR.HANDOFF_SCHEMA,
                "ZERO_EXPOSURE_HANDOFF_INVALID",
                version=ATTESTOR.HANDOFF_VERSION))

        context = ATTESTOR.ProductionContext.__new__(
            ATTESTOR.ProductionContext)
        context.expected_uid = self.fixture.uid
        context.expected_gid = self.fixture.gid
        base = self.fixture.documents["handoff"]
        mutations = (
            ("missing", lambda value: value[
                "paper_profile_restoration"].pop("dormant_backup")),
            ("path", lambda value: value[
                "paper_profile_restoration"].__setitem__(
                    "candidate_path", "/tmp/candidate")),
            ("hash", lambda value: value[
                "paper_profile_restoration"]["retired_watch"].__setitem__(
                    "file_sha256", ATTESTOR.digest_bytes(b"tampered"))),
            ("body", lambda value: value[
                "paper_profile_restoration"][
                    "profile_deployment_receipt"].__setitem__(
                        "body_sha256", ATTESTOR.digest_bytes(b"tampered"))),
            ("runtime-missing", lambda value: value[
                "paper_runtime_profile_hardening"].pop("retained_legacy")),
            ("runtime-schema", lambda value: value[
                "paper_runtime_profile_hardening"].__setitem__(
                    "schema", "legacy.runtime-profile.v0")),
            ("runtime-path", lambda value: value[
                "paper_runtime_profile_hardening"]["target"].__setitem__(
                    "path", "/tmp/not-paper-runtime.env")),
            ("runtime-hash", lambda value: value[
                "paper_runtime_profile_hardening"]["target"].__setitem__(
                    "file_sha256", ATTESTOR.digest_bytes(b"tampered"))),
            ("runtime-mode", lambda value: value[
                "paper_runtime_profile_hardening"]["target"].__setitem__(
                    "mode", 0o644)),
            ("runtime-not-hardened", lambda value: value.__setitem__(
                "paper_runtime_profile_hardened", False)),
            ("runtime-candidate-claim", lambda value: value.__setitem__(
                "paper_runtime_profile_candidate_absent", False)),
        )
        for label, mutate in mutations:
            with self.subTest(label=label):
                value = copy.deepcopy(base)
                mutate(value)
                self.assertReason(
                    "ZERO_EXPOSURE_HANDOFF_INVALID",
                    lambda value=value: ATTESTOR._validate_handoff_v2_host_binding(
                        value, context))

    def test_handoff_v2_runtime_profile_files_are_reopened_and_candidate_absent(
            self):
        context = ATTESTOR.ProductionContext.__new__(
            ATTESTOR.ProductionContext)
        context.expected_uid = self.fixture.uid
        context.expected_gid = self.fixture.gid
        mutations = (
            ("current", lambda: ATTESTOR.PAPER_RUNTIME_PROFILE_PATH.write_bytes(
                b"X" * ATTESTOR.PAPER_RUNTIME_PROFILE_HARDENED_BYTES)),
            ("backup", lambda: ATTESTOR.PAPER_RUNTIME_PROFILE_BACKUP_PATH.write_bytes(
                b"X" * ATTESTOR.PAPER_RUNTIME_PROFILE_LEGACY_BYTES)),
            ("retained", lambda:
                ATTESTOR.PAPER_RUNTIME_PROFILE_RETAINED_PATH.write_bytes(
                    b"X" * ATTESTOR.PAPER_RUNTIME_PROFILE_LEGACY_BYTES)),
            ("candidate", lambda:
                ATTESTOR.PAPER_RUNTIME_PROFILE_CANDIDATE_PATH.write_bytes(
                    b"residue")),
        )
        for index, (label, mutate) in enumerate(mutations):
            with self.subTest(label=label):
                root = self.root / f"runtime-profile-{index:02d}"
                root.mkdir(mode=0o700)
                fixture = EvidenceFixture(root, self.now_ms)
                fixture.build()
                context.expected_uid = fixture.uid
                context.expected_gid = fixture.gid
                mutate()
                self.assertReason(
                    "ZERO_EXPOSURE_HANDOFF_INVALID",
                    lambda fixture=fixture:
                        ATTESTOR._validate_handoff_v2_host_binding(
                            fixture.documents["handoff"], context))

    def test_handoff_v2_rechecks_kill_switch_and_identity_files(self):
        context = ATTESTOR.ProductionContext.__new__(
            ATTESTOR.ProductionContext)
        context.expected_uid = self.fixture.uid
        context.expected_gid = self.fixture.gid
        handoff = self.fixture.documents["handoff"]
        ATTESTOR._validate_handoff_v2_host_binding(handoff, context)
        ATTESTOR.GLOBAL_KILL_SWITCH_PATH.chmod(0o600)
        self.assertReason(
            "ZERO_EXPOSURE_HANDOFF_INVALID",
            lambda: ATTESTOR._validate_handoff_v2_host_binding(
                handoff, context))

    def test_real_ed25519_exact_producer_pair_passes_without_authority(self):
        receipt = self.fixture.run()
        self.assertEqual(receipt["status"], "PASS")
        self.assertTrue(receipt["host_authority_lease_reacquired"])
        self.assertEqual(receipt["query_epoch"], "remote-query-epoch-a")
        self.assertEqual(receipt["query_fencing_generation"], 7)
        self.assertEqual(receipt["query_invocation_id"],
                         "remote-query-invocation-a")
        self.assertEqual(receipt["signature_verification"]["return_code"], 0)
        self.assertEqual(receipt["signature_verification"]["stdout"],
                         "Signature Verified Successfully\n")
        for field in ATTESTOR.BOUNDARY_FIELDS:
            self.assertIs(receipt[field], False)

    def test_repeatable_gap_free_generation_two_lineage_passes(self):
        self.fixture.advance_to_generation_two()
        receipt = self.fixture.run()
        self.assertEqual(receipt["status"], "PASS")
        self.assertEqual(receipt["reservation_generation"], 2)
        self.assertIsNotNone(
            receipt["reservation_predecessor_finalization_body_sha256"])
        self.assertIsNotNone(
            receipt["reservation_prior_finalization_pointer_reference"])

    def test_gap_or_current_pointer_tamper_fails_closed(self):
        pointer_path = self.fixture.advance_to_generation_two()
        pointer = dict(ATTESTOR.strict_object(
            pointer_path.read_bytes(), "FIXTURE_INVALID"))
        pointer.pop("body_sha256", None)
        pointer["reservation_generation"] = 7
        pointer_path.write_bytes(
            ATTESTOR.canonical_bytes(ATTESTOR.seal(pointer)))
        pointer_path.chmod(0o600)
        self.assertReason(
            "ZERO_EXPOSURE_RESERVATION_LINEAGE_INVALID",
            self.fixture.run)

    def test_output_is_canonical_0600_no_replace_and_releases_lease(self):
        receipt = self.fixture.run()
        output = self.fixture.paths["output"]
        self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
        self.assertEqual(output.read_bytes(), ATTESTOR.canonical_bytes(receipt))
        descriptor = os.open(self.fixture.authority_lease, os.O_RDONLY)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)
        self.assertReason("ZERO_EXPOSURE_OUTPUT_ALREADY_EXISTS",
                          self.fixture.run)

    def test_wrong_ed25519_signature_is_hard_failure(self):
        self.fixture.replace_with_wrong_signature()
        self.assertReason("ZERO_EXPOSURE_SIGNATURE_VERIFY_FAILED",
                          self.fixture.run)
        self.assertFalse(self.fixture.paths["output"].exists())

    def test_snapshot_producer_path_or_digest_drift_is_rejected(self):
        for field, value in (
            ("path", str(self.root / "source-copy")),
            ("file_sha256", ATTESTOR.digest_bytes(b"forged")),
        ):
            with self.subTest(field=field):
                fixture = self._fresh_fixture()
                fixture.documents["broker"]["producer"][field] = value
                fixture.rewrite("broker")
                self.assertReason("ZERO_EXPOSURE_PRODUCER_BINDING_INVALID",
                                  fixture.run)

    def test_fixed_producer_binary_inode_or_content_drift_is_rejected(self):
        replacement = self.root / "replacement-producer"
        replacement.write_bytes(b"changed fixed producer\n")
        replacement.chmod(0o755)
        os.replace(replacement, self.fixture.snapshot_producer)
        self.fixture.refresh_reservation_chain()
        self.assertReason("ZERO_EXPOSURE_PRODUCER_BINDING_INVALID",
                          self.fixture.run)

    def test_cross_pair_challenge_reference_is_rejected(self):
        self.fixture.documents["account"]["challenge_reference"] = \
            self.fixture.generic_reference("other-challenge")
        self.fixture.rewrite("account")
        self.assertReason("ZERO_EXPOSURE_PRODUCER_BINDING_INVALID",
                          self.fixture.run)

    def test_signed_payload_replay_under_new_nonce_is_rejected(self):
        challenge = self.fixture.documents["challenge"]
        challenge["nonce"] = "b" * 64
        self.fixture.rewrite("challenge")
        self.fixture.refresh_reservation_chain(resign_evidence=False)
        self.assertReason("ZERO_EXPOSURE_SIGNED_EVIDENCE_INVALID",
                          self.fixture.run)

    def test_historical_lease_inode_drift_is_rejected(self):
        lease = self.fixture.documents["broker"]["host_authority_lease"]
        lease["lease_inode"] += 1
        self.fixture.rewrite("broker")
        self.assertReason("ZERO_EXPOSURE_HOST_AUTHORITY_LEASE_IDENTITY_MISMATCH",
                          self.fixture.run)

    def test_boot_id_splice_is_rejected(self):
        self.fixture.boot_id_path.chmod(0o600)
        self.fixture.boot_id_path.write_text(
            "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee\n", encoding="ascii")
        self.fixture.boot_id_path.chmod(0o444)
        self.assertReason(
            "ZERO_EXPOSURE_HOST_AUTHORITY_LEASE_IDENTITY_MISMATCH",
            self.fixture.run)

    def test_forged_or_replaced_authority_owner_is_rejected(self):
        self.fixture.authority_owner.write_bytes(b"active-owner\n")
        self.fixture.authority_owner.chmod(0o600)
        self.assertReason("ZERO_EXPOSURE_RESERVATION_INVALID",
                          self.fixture.run)

    def test_busy_authority_lease_is_rejected(self):
        descriptor = os.open(self.fixture.authority_lease, os.O_RDONLY)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertReason("ZERO_EXPOSURE_HOST_AUTHORITY_LEASE_BUSY",
                              self.fixture.run)
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def test_input_mode_and_hardlink_are_rejected(self):
        self.fixture.paths["account"].chmod(0o644)
        self.assertReason("ZERO_EXPOSURE_ACCOUNT_SNAPSHOT_INVALID",
                          self.fixture.run)
        fixture = self._fresh_fixture()
        os.link(fixture.paths["account"], self.root / "account-hardlink.json")
        self.assertReason("ZERO_EXPOSURE_ACCOUNT_SNAPSHOT_INVALID",
                          fixture.run)

    def test_secure_reopen_rejects_same_bytes_on_new_inode_after_signature(self):
        original = ATTESTOR.ProductionContext.verify_signature

        def drift(context, evidence):
            result = original(context, evidence)
            path = self.fixture.paths["account"]
            replacement = self.root / "account-replacement.json"
            replacement.write_bytes(path.read_bytes())
            replacement.chmod(0o600)
            os.replace(replacement, path)
            return result

        with mock.patch.object(
                ATTESTOR.ProductionContext, "verify_signature", drift):
            self.assertReason("ZERO_EXPOSURE_INPUT_SECURE_REOPEN_MISMATCH",
                              self.fixture.run)

    def test_noncanonical_or_duplicate_input_is_rejected(self):
        path = self.fixture.paths["account"]
        path.write_text(str(self.fixture.documents["account"]),
                        encoding="ascii")
        path.chmod(0o600)
        self.assertReason("ZERO_EXPOSURE_ACCOUNT_SNAPSHOT_INVALID",
                          self.fixture.run)
        fixture = self._fresh_fixture()
        path = fixture.paths["account"]
        payload = path.read_bytes().replace(
            b'"account_complete":true,',
            b'"account_complete":true,"account_complete":true,', 1)
        path.write_bytes(payload)
        path.chmod(0o600)
        self.assertReason("ZERO_EXPOSURE_ACCOUNT_SNAPSHOT_INVALID",
                          fixture.run)

    def _fresh_fixture(self) -> EvidenceFixture:
        for path in tuple(self.root.iterdir()):
            if path.name.startswith("fresh-"):
                continue
        nested = self.root / ("fresh-" + str(len(list(self.root.glob("fresh-*")))))
        nested.mkdir(mode=0o700)
        fixture = EvidenceFixture(nested, self.now_ms)
        # Constants must point at this fixture while it is exercised. Reuse the
        # primary fixed executables/trust and only isolate receipt files.
        fixture.attestor = self.fixture.attestor
        fixture.snapshot_producer = self.fixture.snapshot_producer
        fixture.handoff_producer = self.fixture.handoff_producer
        fixture.broker_helper = self.fixture.broker_helper
        fixture.verifier = self.fixture.verifier
        fixture.private_key = self.fixture.private_key
        fixture.public_key = self.fixture.public_key
        fixture.build()
        return fixture

    def test_active_orders_nonzero_positions_and_connectors_halt(self):
        cases = (
            {
                "active_order_id_sha256s": [
                    ATTESTOR.digest_bytes(b"order-a")],
                "positions": [], "gross_absolute_position": 0,
                "authorized_connector_count": 0, "end_flat": False,
            },
            {
                "active_order_id_sha256s": [],
                "positions": [{"instrument": "EUR.USD", "quantity": -3}],
                "gross_absolute_position": 3,
                "authorized_connector_count": 0, "end_flat": False,
            },
            {
                "active_order_id_sha256s": [], "positions": [],
                "gross_absolute_position": 0,
                "authorized_connector_count": 1, "end_flat": True,
            },
        )
        for changes in cases:
            with self.subTest(changes=changes):
                fixture = self._fresh_fixture()
                fixture.update_remote_state(**changes)
                receipt = fixture.run()
                self.assertEqual(receipt["status"], "HALT")
                for field in ATTESTOR.BOUNDARY_FIELDS:
                    self.assertIs(receipt[field], False)

    def test_each_local_broker_exposure_halts(self):
        mutations = {
            "authorized_connectors": 1,
            "authorized_uids": [1000],
            "broker_socket_count": 1,
            "broker_process_count": 1,
            "credential_exposure_count": 1,
        }
        for field, value in mutations.items():
            with self.subTest(field=field):
                fixture = self._fresh_fixture()
                fixture.documents["broker"][field] = value
                fixture.rewrite("broker")
                self.assertEqual(fixture.run()["status"], "HALT")

    def test_any_authority_signal_halts_but_output_authority_is_false(self):
        payload = dict(self.fixture.documents["evidence"]["payload"])
        payload["paper_authorized"] = True
        self.fixture._write_signed_evidence(payload)
        self.fixture._write_snapshots()
        self.fixture.documents["account"]["paper_authorized"] = True
        self.fixture.rewrite("account")
        receipt = self.fixture.run()
        self.assertEqual(receipt["status"], "HALT")
        for field in ATTESTOR.BOUNDARY_FIELDS:
            self.assertIs(receipt[field], False)

    def test_prechallenge_stale_signed_evidence_is_rejected(self):
        payload = dict(self.fixture.documents["evidence"]["payload"])
        payload["observed_at_ms"] = (
            self.now_ms - ATTESTOR.MAXIMUM_EVIDENCE_AGE_MS - 1)
        self.fixture._write_signed_evidence(payload)
        self.fixture._write_snapshots()
        with self.assertRaises(ATTESTOR.AttestationError) as raised:
            self.fixture.run()
        self.assertEqual(
            raised.exception.reason,
            "ZERO_EXPOSURE_SIGNED_EVIDENCE_PRECHALLENGE")

    def test_expired_snapshot_or_handoff_is_no_go(self):
        for name in ("handoff", "broker", "account"):
            with self.subTest(name=name):
                fixture = self._fresh_fixture()
                document = fixture.documents[name]
                start = "issued_at_ms" if name == "handoff" \
                    else "observed_at_ms"
                document[start] = self.now_ms - 10_000
                document["expires_at_ms"] = self.now_ms - 1
                fixture.rewrite(name)
                # References downstream of handoff must remain exact.
                if name == "handoff":
                    fixture.refresh_reservation_chain()
                self.assertEqual(fixture.run()["status"], "NO_GO")

    def test_incomplete_inventory_or_kill_switch_is_no_go(self):
        for field in (
            "process_inventory_complete", "socket_inventory_complete",
            "credential_inventory_complete", "paper_units_inactive",
            "kill_switch_engaged",
        ):
            with self.subTest(field=field):
                fixture = self._fresh_fixture()
                fixture.documents["broker"][field] = False
                fixture.rewrite("broker")
                self.assertEqual(fixture.run()["status"], "NO_GO")

    def test_query_epoch_fence_or_invocation_mismatch_is_rejected(self):
        for field, value in (
            ("query_epoch", "other-query-epoch"),
            ("query_fencing_generation", 8),
            ("query_invocation_id", "other-query-invocation"),
        ):
            with self.subTest(field=field):
                fixture = self._fresh_fixture()
                fixture.documents["account"][field] = value
                fixture.documents["account"]["snapshot_sha256"] = \
                    ATTESTOR.account_state_sha256(
                        fixture.documents["account"])
                fixture.rewrite("account")
                self.assertReason(
                    "ZERO_EXPOSURE_ACCOUNT_PAYLOAD_BINDING_INVALID",
                    fixture.run)

    def test_account_snapshot_hash_must_bind_exact_state(self):
        self.fixture.documents["account"]["snapshot_sha256"] = \
            ATTESTOR.digest_bytes(b"forged-state")
        self.fixture.rewrite("account")
        self.assertReason("ZERO_EXPOSURE_ACCOUNT_PAYLOAD_BINDING_INVALID",
                          self.fixture.run)

    def test_protected_port_inventory_must_be_exact(self):
        self.fixture.documents["broker"]["protected_broker_ports"] = [4002]
        self.fixture.rewrite("broker")
        self.assertReason("ZERO_EXPOSURE_BROKER_SNAPSHOT_INVALID",
                          self.fixture.run)

    def test_lineage_disagreement_with_operator_expectation_halts(self):
        receipt = self.fixture.run(expected_campaign="other-campaign")
        self.assertEqual(receipt["status"], "HALT")
        for field in ATTESTOR.BOUNDARY_FIELDS:
            self.assertIs(receipt[field], False)

    def test_signed_evidence_file_digest_drift_is_rejected(self):
        self.fixture.documents["account"]["signed_evidence_reference"][
            "file_sha256"] = ATTESTOR.digest_bytes(b"other-envelope")
        self.fixture.rewrite("account")
        self.assertReason("ZERO_EXPOSURE_PRODUCER_BINDING_INVALID",
                          self.fixture.run)

    def test_verifier_or_public_key_reference_drift_is_rejected(self):
        for nested, field in (("verifier", "path"),
                              ("public_key", "file_sha256")):
            with self.subTest(nested=nested):
                fixture = self._fresh_fixture()
                proof = fixture.documents["account"][
                    "signature_verification"]
                proof[nested][field] = str(self.root / "wrong") if \
                    field == "path" else ATTESTOR.digest_bytes(b"wrong")
                fixture.rewrite("account")
                self.assertReason("ZERO_EXPOSURE_SIGNATURE_PROOF_INVALID",
                                  fixture.run)

    def test_rehearsal_mode_cannot_pass(self):
        self.fixture.documents["broker"]["production_mode"] = "REHEARSAL_ONLY"
        self.fixture.rewrite("broker")
        self.assertReason("ZERO_EXPOSURE_PRODUCER_BINDING_INVALID",
                          self.fixture.run)

    def test_run_token_and_explicit_production_mode_are_mandatory(self):
        arguments = dict(
            operator_intent_path=self.fixture.paths["intent"],
            handoff_path=self.fixture.paths["handoff"],
            challenge_path=self.fixture.paths["challenge"],
            signed_evidence_path=self.fixture.paths["evidence"],
            broker_snapshot_path=self.fixture.paths["broker"],
            account_snapshot_path=self.fixture.paths["account"],
            expected_source=self.fixture.source, expected_domain="alpha",
            expected_campaign=self.fixture.campaign,
            output_path=self.fixture.paths["output"],
            production_mode=ATTESTOR.PRODUCTION_MODE,
            expected_uid=self.fixture.uid, expected_gid=self.fixture.gid,
            now_ms=self.now_ms)
        self.assertReason(
            "ZERO_EXPOSURE_CLI_RUN_REQUIRED",
            lambda: ATTESTOR.attest_and_publish(**arguments))
        arguments["_run_token"] = ATTESTOR.CLI_RUN_TOKEN
        arguments["production_mode"] = "REHEARSAL_ONLY"
        self.assertReason(
            "ZERO_EXPOSURE_EXPLICIT_PRODUCTION_INTENT_REQUIRED",
            lambda: ATTESTOR.attest_and_publish(**arguments))

    def test_cli_parser_requires_explicit_run(self):
        with self.assertRaises(SystemExit):
            ATTESTOR._parser().parse_args([])

    def test_output_validator_cannot_upgrade_exposure_to_pass(self):
        receipt = self.fixture.run()
        body = dict(receipt)
        body.pop("body_sha256")
        body["authorized_connectors"] = 1
        invalid = ATTESTOR.seal(body)
        self.assertReason(
            "ZERO_EXPOSURE_OUTPUT_INVALID",
            lambda: ATTESTOR.validate_output(invalid))


if __name__ == "__main__":
    unittest.main()
