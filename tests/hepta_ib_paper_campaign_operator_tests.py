#!/usr/bin/env python3

from __future__ import annotations

import argparse
import copy
from dataclasses import replace
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import stat
import struct
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    specification = importlib.util.spec_from_file_location(name, path)
    if specification is None or specification.loader is None:
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


CAMPAIGN = load_module(
    "hepta_ib_paper_campaign_operator_under_test",
    ROOT / "scripts/hepta_ib_paper_campaign_operator.py")
ADMISSION_VERIFIER = load_module(
    "hepta_p1_paper_admission_verifier_for_operator_contract",
    ROOT / "scripts/hepta_p1_paper_admission_verifier.py")
ZERO_ATTESTOR = load_module(
    "hepta_p1_paper_zero_exposure_attestor_for_operator_contract",
    ROOT / "scripts/hepta_p1_paper_zero_exposure_attestor.py")
ZERO_PRODUCER = load_module(
    "hepta_p1_paper_zero_exposure_producer_for_operator_contract",
    ROOT / "scripts/hepta_p1_paper_zero_exposure_snapshot_producer.py")
CLIENT = load_module(
    "hepta_campaignctl_under_test",
    ROOT / "scripts/hepta_campaignctl.py")
REAL_FSTAT = os.fstat
REAL_LSTAT = os.lstat


# This is deliberately independent of CAMPAIGN.ADMISSION_INPUT_NAMES.  A
# consumer test derived from the consumer constant cannot detect contract
# drift between the admission producer and the campaign operator.
EXPECTED_ADMISSION_INPUT_NAMES = frozenset({
    "source_baseline",
    "install_manifest",
    "install_receipt",
    "install_pointer",
    "profile_receipt",
    "activation_receipt",
    "p1_audit_receipt",
    "release_validation_receipt",
    "agent_os_rootful_gate_receipt",
    "dual_domain_gate_receipt",
    "rootful_gate_receipt",
    "p1_liveness_gate_receipt",
    "network_gate_receipt",
    "hard_network_gate_receipt",
    "native_gate_receipt",
    "watch_handoff_receipt",
    "zero_exposure_receipt",
})

LEGACY_ADMISSION_INPUT_NAMES = frozenset({
    "source_baseline",
    "install_manifest",
    "install_receipt",
    "install_pointer",
    "profile_receipt",
    "activation_receipt",
    "p1_audit_receipt",
    "dual_domain_gate_receipt",
    "rootful_gate_receipt",
    "network_gate_receipt",
    "hard_network_gate_receipt",
    "native_gate_receipt",
    "watch_handoff_receipt",
    "zero_exposure_receipt",
})

POLICY_V3_TERMINAL_FIELDS = {
    "admission_finalization_current_pointer_path",
    "admission_finalization_current_pointer_file_sha256",
    "admission_finalization_current_pointer_body_sha256",
    "admission_finalization_tombstone_path",
    "admission_finalization_tombstone_file_sha256",
    "admission_finalization_tombstone_body_sha256",
}


def canonical(value: object) -> bytes:
    return (
        json.dumps(
            value, sort_keys=True, separators=(",", ":"),
            allow_nan=False) + "\n").encode("ascii")


def digest(raw: bytes) -> str:
    return "sha256:" + __import__("hashlib").sha256(raw).hexdigest()


def admission_document(
        *, campaign_id: str = "campaign-a", domain_id: str = "alpha",
        source_baseline_sha256: str = "sha256:" + "a" * 64,
        strategy_sha256: str = "sha256:" + "1" * 64,
        evaluated_at_ms: int = 950_000, expires_at_ms: int = 1_100_000,
) -> dict[str, object]:
    bindings = {
        name: {
            "path": f"/var/lib/hepta/evidence/{name}.json",
            "file_sha256": digest(f"file-{name}".encode()),
            "body_sha256": digest(f"body-{name}".encode()),
            "schema": f"hepta.test-{name}.v1", "version": 1,
            "status": "PASS",
        }
        for name in EXPECTED_ADMISSION_INPUT_NAMES
    }
    body = {
        "schema": CAMPAIGN.ADMISSION_SCHEMA, "version": 1, "status": "GO",
        "evaluated_at_ms": evaluated_at_ms, "expires_at_ms": expires_at_ms,
        "round": 114, "domain": domain_id, "campaign_id": campaign_id,
        "source_baseline_sha256": source_baseline_sha256,
        "strategy_sha256": strategy_sha256,
        "input_bindings": bindings, "findings": [],
        "paper_test_admission_candidate": True,
        "paper_authorized": False, "live_authorized": False,
        "mutation_authorized": False, "direct_broker_access": False,
        "order_submission_authorized": False,
        "authorization_effect": "NONE_READ_ONLY_CANDIDATE_ONLY",
    }
    return {**body, "body_sha256": digest(canonical(body))}


def reseal_admission(document: dict[str, object]) -> dict[str, object]:
    body = copy.deepcopy(document)
    body.pop("body_sha256", None)
    body["body_sha256"] = digest(canonical(body))
    return body


def pin_admission(
        policy: dict[str, object], admission: dict[str, object],
        raw: bytes | None = None,
) -> None:
    payload = canonical(admission) if raw is None else raw
    policy["admission_receipt_file_sha256"] = digest(payload)
    policy["admission_receipt_body_sha256"] = admission["body_sha256"]


def policy_document(
        *, campaign_id: str = "campaign-a", enabled: bool = True,
        strategy_sha256: str = "sha256:" + "1" * 64,
        max_cycles: int = 2,
        host_authority_root: Path = CAMPAIGN.HOST_AUTHORITY_DIRECTORY,
) -> dict[str, object]:
    admission = admission_document(campaign_id=campaign_id)
    admission_raw = canonical(admission)
    return {
        "schema": CAMPAIGN.LEGACY_POLICY_V3_SCHEMA,
        "version": 3,
        "campaign_id": campaign_id,
        "domain_id": "alpha",
        "enabled": enabled,
        "mutations_authorized": enabled,
        "paper_only": True,
        "live_authorized": False,
        "strategy_id": "strategy-a",
        "strategy_version": "1",
        "strategy_sha256": strategy_sha256,
        "valid_after_ms": 990_000 if enabled else 0,
        "expires_at_ms": 1_080_000 if enabled else 0,
        "allowed_instruments": ["EUR.USD"],
        "max_cycles": max_cycles,
        "max_quantity": 2,
        "min_cycle_interval_ms": 5_000,
        "operator_ttl_seconds": 20,
        "max_intent_horizon_ms": 20_000,
        "max_holding_ms": 20_000,
        "max_active_orders": 1,
        "order_type": "LMT",
        "tif": "DAY",
        "end_flat_required": True,
        "source_baseline_sha256": "sha256:" + "a" * 64,
        "admission_receipt_name": "candidate-a.json",
        "admission_receipt_file_sha256": digest(admission_raw),
        "admission_receipt_body_sha256": admission["body_sha256"],
        "admission_finalization_current_pointer_path": str(
            host_authority_root / "finalization-current.v1.json"),
        "admission_finalization_current_pointer_file_sha256":
            "sha256:" + "2" * 64,
        "admission_finalization_current_pointer_body_sha256":
            "sha256:" + "3" * 64,
        "admission_finalization_tombstone_path": str(
            host_authority_root /
            ("finalized.zero-exposure-" + "a" * 48 + ".v1.json")),
        "admission_finalization_tombstone_file_sha256":
            "sha256:" + "4" * 64,
        "admission_finalization_tombstone_body_sha256":
            "sha256:" + "5" * 64,
    }


def legacy_policy_document(*, enabled: bool = False) -> dict[str, object]:
    document = policy_document(enabled=enabled)
    document["schema"] = CAMPAIGN.LEGACY_POLICY_SCHEMA
    document["version"] = 1
    for field in (
            "source_baseline_sha256", "admission_receipt_name",
            "admission_receipt_file_sha256",
            "admission_receipt_body_sha256", *POLICY_V3_TERMINAL_FIELDS):
        del document[field]
    return document


def legacy_v2_policy_document(*, enabled: bool = False) -> dict[str, object]:
    document = policy_document(enabled=enabled)
    document["schema"] = CAMPAIGN.LEGACY_POLICY_V2_SCHEMA
    document["version"] = 2
    for field in POLICY_V3_TERMINAL_FIELDS:
        del document[field]
    return document


def local_policy_document(
        *, campaign_id: str = "local-campaign-a", enabled: bool = False,
        max_cycles: int = 2,
) -> dict[str, object]:
    document = policy_document(
        campaign_id=campaign_id, enabled=enabled, max_cycles=max_cycles)
    document["schema"] = CAMPAIGN.LEGACY_POLICY_V4_SCHEMA
    document["version"] = 4
    document["admission_mode"] = "local-only"
    # A disabled document is non-authorizing but retains a well-formed window
    # so intent-contract tests can exercise v4 syntax independently.
    document["valid_after_ms"] = 990_000
    document["expires_at_ms"] = 1_080_000
    document["order_type"] = "MKT"
    document["deployment_evidence_file_sha256"] = "sha256:" + "d" * 64
    document["deployment_evidence_body_sha256"] = "sha256:" + "e" * 64
    document["deployment_install_transaction_id"] = (
        "install-transaction-round98")
    for field in (
            "admission_receipt_name", "admission_receipt_file_sha256",
            "admission_receipt_body_sha256", *POLICY_V3_TERMINAL_FIELDS):
        del document[field]
    return document


def v5_policy_document(
        *, campaign_id: str = "campaign-a", enabled: bool = True,
        max_cycles: int = 1,
) -> dict[str, object]:
    document = policy_document(
        campaign_id=campaign_id, enabled=enabled, max_cycles=max_cycles)
    document["schema"] = CAMPAIGN.POLICY_SCHEMA
    document["version"] = 5
    document["admission_mode"] = "external-p1-finalized"
    document["deployment_evidence_file_sha256"] = "sha256:" + "d" * 64
    document["deployment_evidence_body_sha256"] = "sha256:" + "e" * 64
    document["deployment_install_transaction_id"] = (
        "install-transaction-round105")
    document["max_quantity"] = 1
    document["valid_after_ms"] = 990_000
    document["expires_at_ms"] = (
        990_000 + CAMPAIGN.EXTERNAL_P1_CAMPAIGN_DURATION_MS)
    for name, prefix in (
            ("p1_audit_receipt", "p1_audit_receipt"),
            ("watch_handoff_receipt", "watch_handoff_receipt")):
        document[f"{prefix}_path"] = (
            f"/var/lib/hepta/evidence/{name}.json")
        document[f"{prefix}_file_sha256"] = digest(
            f"file-{name}".encode())
        document[f"{prefix}_body_sha256"] = digest(
            f"body-{name}".encode())
    document["watch_handoff_receipt_path"] = str(
        CAMPAIGN.EXTERNAL_P1_HANDOFF_PATH)
    return document


def local_v5_policy_document(
        *, campaign_id: str = "local-v5-campaign-a", enabled: bool = True,
        max_cycles: int = 2,
) -> dict[str, object]:
    document = local_policy_document(
        campaign_id=campaign_id, enabled=enabled, max_cycles=max_cycles)
    document["schema"] = CAMPAIGN.POLICY_SCHEMA
    document["version"] = 5
    document["deployment_install_transaction_id"] = (
        "install-transaction-round106-local")
    return document


def trade_intent(
        *, intent_id: str = "intent-a", observed_at_ms: int = 999_000,
        expires_at_ms: int = 1_010_000,
        order_type: str = "LMT",
        limit_price: float = 1.10002,
) -> dict[str, object]:
    intent = {
        "schema": (
            CAMPAIGN.TRADE_INTENT_SCHEMA if order_type == "MKT" else
            CAMPAIGN.LEGACY_TRADE_INTENT_SCHEMA),
        "paper_only": True,
        "strategy_id": "strategy-a",
        "strategy_version": "1",
        "strategy_sha256": "sha256:" + "1" * 64,
        "intent_id": intent_id,
        "instrument": "EUR.USD",
        "symbol": "EUR",
        "currency": "USD",
        "sec_type": "CASH",
        "exchange": "IDEALPRO",
        "side": "BUY",
        "quantity": 1,
        "order_type": order_type,
        "tif": "DAY",
        "observed_bid": 1.10000,
        "observed_ask": 1.10001,
        "observed_at_ms": observed_at_ms,
        "expires_at_ms": expires_at_ms,
        "entry_thesis": "Bounded PAPER strategy thesis",
        "invalidation_condition": "Authority or quote changes",
        "max_holding_ms": 10_000,
        "max_adverse_move": 0.0005,
        "expected_slippage": 0.00005,
        "exit_plan": "Canonical atomic reduce-only flatten",
    }
    if order_type == "MKT":
        intent["reference_price"] = intent["observed_ask"]
    else:
        intent["limit_price"] = limit_price
    return intent


def open_request(
        policy: CAMPAIGN.CampaignPolicy, *,
        request_id: str = "request-open-a", cycle_id: str = "cycle-a",
        intent: dict[str, object] | None = None,
        now_ms: int = 1_000_000,
) -> dict[str, object]:
    selected_intent = intent or trade_intent(
        order_type=policy.order_type,
        limit_price=1.10001 if policy.version == 5 else 1.10002)
    _validated, digest = CAMPAIGN.validate_trade_intent(
        selected_intent, policy, now_ms)
    return {
        "schema": CAMPAIGN.REQUEST_SCHEMA,
        "version": 1,
        "action": "open_cycle",
        "request_id": request_id,
        "domain_id": "alpha",
        "campaign_id": policy.campaign_id,
        "cycle_id": cycle_id,
        "intent": selected_intent,
        "intent_sha256": digest,
        "preflight_sha256": "sha256:" + "2" * 64,
    }


def close_request(
        policy: CAMPAIGN.CampaignPolicy, digest: str, *,
        request_id: str = "request-close-a", cycle_id: str = "cycle-a",
        outcome: str = "PLACE_ACCEPTED",
) -> dict[str, object]:
    return {
        "schema": CAMPAIGN.REQUEST_SCHEMA,
        "version": 1,
        "action": "close_cycle",
        "request_id": request_id,
        "domain_id": "alpha",
        "campaign_id": policy.campaign_id,
        "cycle_id": cycle_id,
        "intent_sha256": digest,
        "outcome": outcome,
    }


class FakeClock:
    def __init__(self, value: int = 1_000_000):
        self.value = value

    def __call__(self) -> int:
        return self.value


class SequenceClock:
    def __init__(self, *values: int):
        if not values:
            raise ValueError("at least one clock value is required")
        self.values = list(values)
        self.index = 0

    def __call__(self) -> int:
        value = self.values[min(self.index, len(self.values) - 1)]
        self.index += 1
        return value


class FakeOperator:
    def __init__(self):
        self.disarms: list[tuple[str, str, str, int]] = []
        self.reengages: list[tuple[str, str, str]] = []
        self.fail_disarm = False
        self.fail_reengage = False
        self.deadline_at_ms = 1_020_000
        self.on_disarm = None

    def disarm(
            self, domain_id: str, cycle_id: str, intent_sha256: str,
            ttl_seconds: int,
    ) -> dict[str, object]:
        self.disarms.append(
            (domain_id, cycle_id, intent_sha256, ttl_seconds))
        if self.fail_disarm:
            raise CAMPAIGN.CampaignError(
                "CAMPAIGN_OPERATOR_REJECTED", recovery_required=True)
        if self.on_disarm is not None:
            self.on_disarm()
        return {
            "status": "disarmed",
            "domain": domain_id,
            "cycle_id": cycle_id,
            "intent_sha256": intent_sha256,
            "deadline_at_ms": self.deadline_at_ms,
        }

    def reengage(
            self, domain_id: str, cycle_id: str,
            intent_sha256: str,
    ) -> dict[str, object]:
        self.reengages.append((domain_id, cycle_id, intent_sha256))
        if self.fail_reengage:
            raise CAMPAIGN.CampaignError(
                "CAMPAIGN_OPERATOR_REJECTED", recovery_required=True)
        return {
            "status": "engaged",
            "domain": domain_id,
            "cycle_id": cycle_id,
            "intent_sha256": intent_sha256,
            "deadline_at_ms": 1_020_000,
        }


class CampaignFixture:
    def __init__(
            self, root: Path, document: dict[str, object],
            clock: FakeClock | None = None,
            admission: dict[str, object] | None = None,
            monotonic_clock: FakeClock | SequenceClock | None = None,
    ):
        self.root = root
        self.document = document
        self.clock = clock or FakeClock()
        self.monotonic_clock = monotonic_clock or FakeClock(5_000_000)
        self.operator = FakeOperator()
        self.paths = CAMPAIGN.CampaignPaths(
            root / "runtime", root / "receipts")
        self.uid = os.getuid()
        self.gid = os.getgid()
        self.evidence_root = root / "evidence"
        self.evidence_root.mkdir(mode=0o700)
        self.p1_audit_path = self.evidence_root / "p1-audit.json"
        self.watch_handoff_path = self.evidence_root / "watch-handoff.json"
        self.admission_root = root / "admission"
        self.admission_root.mkdir(mode=0o700)
        self.zero_root = root / "zero"
        self.zero_root.mkdir(mode=0o700)
        self.host_authority_root = root / "host-authority"
        self.host_authority_root.mkdir(mode=0o700)
        self.lease_path = self.host_authority_root / "lease.lock"
        self.lease_path.write_bytes(b"")
        self.lease_path.chmod(0o600)
        self.boot_id = "11111111-1111-1111-1111-111111111111"
        self.boot_id_path = self.host_authority_root / "boot-id"
        self.boot_id_path.write_text(self.boot_id + "\n", encoding="ascii")
        self.boot_id_path.chmod(0o400)
        self.reservation_id = "zero-exposure-" + "a" * 48
        self.tombstone_path = self.host_authority_root / (
            "finalized." + self.reservation_id + ".v1.json")
        self.pointer_path = (
            self.host_authority_root / "finalization-current.v1.json")
        self.zero_path = self.zero_root / "zero-exposure.json"
        self.admission = admission or admission_document(
            campaign_id=str(document["campaign_id"]))
        self.admission_path = self.admission_root / "candidate-a.json"
        self.admission_reads = 0
        self.deployment_reads = 0
        self.deployment_generation = 1
        self.last_session: CAMPAIGN.FinalizedAdmissionSession | None = None
        external_v5 = (
            document.get("version") == 5 and
            document.get("admission_mode") == "external-p1-finalized")
        if external_v5:
            prior_handoff_path = CAMPAIGN.EXTERNAL_P1_HANDOFF_PATH
            policy_handoff_path_was_expected = (
                document.get("watch_handoff_receipt_path") ==
                str(prior_handoff_path))
            CAMPAIGN.EXTERNAL_P1_HANDOFF_PATH = self.watch_handoff_path
            profile_root = root / "external-profile"
            profile_root.mkdir(mode=0o700)
            CAMPAIGN.EXTERNAL_P1_DORMANT_PROFILE_PATH = (
                profile_root / "alpha.env")
            CAMPAIGN.EXTERNAL_P1_PAPER_PROFILE_PATH = (
                profile_root / "alpha.ib-paper.env")
            CAMPAIGN.EXTERNAL_P1_PAPER_PROFILE_CANDIDATE_PATH = (
                profile_root / "paper-candidate.env")
            CAMPAIGN.EXTERNAL_P1_PAPER_PROFILE_BACKUP_PATH = (
                profile_root / "paper-legacy-backup.env")
            CAMPAIGN.EXTERNAL_P1_PAPER_PROFILE_RETAINED_PATH = (
                profile_root / "paper-retained-legacy.env")
            CAMPAIGN.EXTERNAL_P1_DORMANT_BACKUP_PATH = (
                profile_root / "dormant-backup.env")
            CAMPAIGN.EXTERNAL_P1_FORWARD_RETAINED_PATH = (
                profile_root / "dormant-retained.env")
            CAMPAIGN.EXTERNAL_P1_RETIRED_WATCH_PATH = (
                profile_root / "retired-watch.env")
            CAMPAIGN.EXTERNAL_P1_TRANSITION_PATH = (
                profile_root / "transition.json")
            CAMPAIGN.EXTERNAL_P1_DEPLOYMENT_PATH = (
                profile_root / "deployment.json")
            CAMPAIGN.EXTERNAL_P1_PREIMAGE_PATH = (
                profile_root / "preimage.json")
            CAMPAIGN.EXTERNAL_P1_CANDIDATE_PATH = (
                profile_root / "candidate.env")
            if policy_handoff_path_was_expected:
                document["watch_handoff_receipt_path"] = str(
                    self.watch_handoff_path)
            bindings = self.admission.get("input_bindings")
            if isinstance(bindings, dict) and isinstance(
                    bindings.get("watch_handoff_receipt"), dict):
                binding = bindings["watch_handoff_receipt"]
                if (
                        binding.get("schema") ==
                            "hepta.test-watch_handoff_receipt.v1" and
                        binding.get("version") == 1 and
                        binding.get("status") == "PASS"):
                    binding.update({
                        "path": str(self.watch_handoff_path),
                        "schema": CAMPAIGN.EXTERNAL_P1_HANDOFF_SCHEMA,
                        "version": 2,
                        "status": CAMPAIGN.EXTERNAL_P1_HANDOFF_STATUS,
                    })
            self.admission = reseal_admission(self.admission)
            self.document["admission_receipt_file_sha256"] = digest(
                canonical(self.admission))
            self.document["admission_receipt_body_sha256"] = self.admission[
                "body_sha256"]
            self._prepare_v5_pinned_evidence()
        if document.get("version") == 3 or external_v5:
            self._build_finalized_admission()
        else:
            self.write_admission()

    @staticmethod
    def _document_body_valid(document: dict[str, object]) -> bool:
        claimed = document.get("body_sha256")
        body = copy.deepcopy(document)
        body.pop("body_sha256", None)
        return claimed == digest(canonical(body))

    @staticmethod
    def _reference(path: Path, document: dict[str, object]) -> dict[str, str]:
        payload = canonical(document)
        return {
            "path": str(path), "file_sha256": digest(payload),
            "body_sha256": str(document["body_sha256"]),
        }

    def _prepare_v5_pinned_evidence(self) -> None:
        admission_was_sealed = self._document_body_valid(self.admission)
        original_raw = canonical(self.admission)
        candidate_file_pin_matched = (
            self.document.get("admission_receipt_file_sha256") ==
            digest(original_raw))
        candidate_body_pin_matched = (
            self.document.get("admission_receipt_body_sha256") ==
            self.admission.get("body_sha256"))
        bindings = self.admission.get("input_bindings")
        if not isinstance(bindings, dict):
            return
        handoff_evidence = self._external_handoff_document()
        for name, prefix, path in (
                ("p1_audit_receipt", "p1_audit_receipt",
                 self.p1_audit_path),
                ("watch_handoff_receipt", "watch_handoff_receipt",
                 self.watch_handoff_path)):
            original_binding = bindings.get(name)
            direct_pin_matched = (
                isinstance(original_binding, dict) and
                all(self.document.get(f"{prefix}_{field}") ==
                    original_binding.get(field)
                    for field in ("path", "file_sha256", "body_sha256")))
            if name == "watch_handoff_receipt":
                evidence = handoff_evidence
            else:
                body = {
                    "schema": f"hepta.test-{name}.v1",
                    "version": 1,
                    "status": "PASS",
                    "evidence_kind": name,
                }
                evidence = {
                    **body, "body_sha256": digest(canonical(body))}
            path.write_bytes(canonical(evidence))
            path.chmod(0o600)
            reference = self._reference(path, evidence)
            if isinstance(original_binding, dict):
                metadata_matched = all(
                    original_binding.get(field) == evidence[field]
                    for field in ("schema", "version", "status"))
                original_binding.update(reference)
                if metadata_matched:
                    original_binding.update({
                        "schema": evidence["schema"],
                        "version": evidence["version"],
                        "status": evidence["status"],
                    })
            if direct_pin_matched:
                self.document.update({
                    f"{prefix}_path": reference["path"],
                    f"{prefix}_file_sha256": reference["file_sha256"],
                    f"{prefix}_body_sha256": reference["body_sha256"],
                })
        if admission_was_sealed:
            self.admission = reseal_admission(self.admission)
            if candidate_file_pin_matched:
                self.document["admission_receipt_file_sha256"] = digest(
                    canonical(self.admission))
            if candidate_body_pin_matched:
                self.document["admission_receipt_body_sha256"] = (
                    self.admission["body_sha256"])

    @staticmethod
    def _profile_evidence(path: Path) -> dict[str, object]:
        raw = path.read_bytes()
        metadata = path.stat()
        return {
            "path": str(path), "file_sha256": digest(raw),
            "bytes": len(raw), "mode": metadata.st_mode,
            "uid": metadata.st_uid, "gid": metadata.st_gid,
            "nlink": metadata.st_nlink, "device": metadata.st_dev,
            "inode": metadata.st_ino, "mtime_ns": metadata.st_mtime_ns,
            "ctime_ns": metadata.st_ctime_ns,
        }

    def _external_handoff_document(self) -> dict[str, object]:
        dormant = b"dormant-paper-profile\n"
        watch = b"passive-watch-profile\n"
        paper = b"reviewed-paper-runtime-profile\n"
        legacy_paper = b"legacy-paper-runtime-profile\n"
        CAMPAIGN.EXTERNAL_P1_DORMANT_PROFILE_SHA256 = digest(dormant)
        CAMPAIGN.EXTERNAL_P1_DORMANT_PROFILE_BYTES = len(dormant)
        CAMPAIGN.EXTERNAL_P1_WATCH_PROFILE_SHA256 = digest(watch)
        CAMPAIGN.EXTERNAL_P1_WATCH_PROFILE_BYTES = len(watch)
        CAMPAIGN.EXTERNAL_P1_PAPER_PROFILE_SHA256 = digest(paper)
        CAMPAIGN.EXTERNAL_P1_PAPER_PROFILE_BYTES = len(paper)
        CAMPAIGN.EXTERNAL_P1_LEGACY_PAPER_PROFILE_SHA256 = digest(legacy_paper)
        CAMPAIGN.EXTERNAL_P1_LEGACY_PAPER_PROFILE_BYTES = len(legacy_paper)
        for path, payload, mode in (
                (CAMPAIGN.EXTERNAL_P1_DORMANT_PROFILE_PATH,
                 dormant, 0o644),
                (CAMPAIGN.EXTERNAL_P1_DORMANT_BACKUP_PATH,
                 dormant, 0o600),
                (CAMPAIGN.EXTERNAL_P1_FORWARD_RETAINED_PATH,
                 dormant, 0o600),
                (CAMPAIGN.EXTERNAL_P1_RETIRED_WATCH_PATH,
                 watch, 0o600),
                (CAMPAIGN.EXTERNAL_P1_PAPER_PROFILE_PATH,
                 paper, 0o644),
                (CAMPAIGN.EXTERNAL_P1_PAPER_PROFILE_BACKUP_PATH,
                 legacy_paper, 0o600),
                (CAMPAIGN.EXTERNAL_P1_PAPER_PROFILE_RETAINED_PATH,
                 legacy_paper, 0o600)):
            path.write_bytes(payload)
            path.chmod(mode)
        for path, schema in (
                (CAMPAIGN.EXTERNAL_P1_TRANSITION_PATH,
                 "hepta.test-transition.v1"),
                (CAMPAIGN.EXTERNAL_P1_DEPLOYMENT_PATH,
                 "hepta.test-deployment.v1"),
                (CAMPAIGN.EXTERNAL_P1_PREIMAGE_PATH,
                 "hepta.test-preimage.v1")):
            body = {"schema": schema, "version": 1, "status": "PASS"}
            path.write_bytes(canonical({
                **body, "body_sha256": digest(canonical(body))}))
            path.chmod(0o600)
        restoration = {
            "schema": "hepta.p1-watch-to-paper-profile-restoration.v1",
            "version": 1, "status": "DORMANT_PAPER_PROFILE_RESTORED",
            "target": self._profile_evidence(
                CAMPAIGN.EXTERNAL_P1_DORMANT_PROFILE_PATH),
            "dormant_backup": self._profile_evidence(
                CAMPAIGN.EXTERNAL_P1_DORMANT_BACKUP_PATH),
            "forward_retained_dormant": self._profile_evidence(
                CAMPAIGN.EXTERNAL_P1_FORWARD_RETAINED_PATH),
            "retired_watch": self._profile_evidence(
                CAMPAIGN.EXTERNAL_P1_RETIRED_WATCH_PATH),
            "candidate_path": str(CAMPAIGN.EXTERNAL_P1_CANDIDATE_PATH),
            "retired_watch_path": str(
                CAMPAIGN.EXTERNAL_P1_RETIRED_WATCH_PATH),
            "exchange_method": "RENAME_EXCHANGE",
            "forward_only_after_exchange": True,
            "restore_intent_record_sha256": "sha256:" + "8" * 64,
            "restore_exchange_record_sha256": "sha256:" + "9" * 64,
        }
        for field, path in (
                ("forward_transition_receipt",
                 CAMPAIGN.EXTERNAL_P1_TRANSITION_PATH),
                ("profile_deployment_receipt",
                 CAMPAIGN.EXTERNAL_P1_DEPLOYMENT_PATH),
                ("forward_preimage_evidence",
                 CAMPAIGN.EXTERNAL_P1_PREIMAGE_PATH)):
            evidence = self._profile_evidence(path)
            evidence["body_sha256"] = json.loads(
                path.read_text(encoding="ascii"))["body_sha256"]
            restoration[field] = evidence
        hardening = {
            "schema":
                "hepta.p1-watch-to-paper-runtime-profile-hardening.v1",
            "version": 1, "status": "PAPER_RUNTIME_PROFILE_HARDENED",
            "target": self._profile_evidence(
                CAMPAIGN.EXTERNAL_P1_PAPER_PROFILE_PATH),
            "legacy_backup": self._profile_evidence(
                CAMPAIGN.EXTERNAL_P1_PAPER_PROFILE_BACKUP_PATH),
            "retained_legacy": self._profile_evidence(
                CAMPAIGN.EXTERNAL_P1_PAPER_PROFILE_RETAINED_PATH),
            "candidate_path": str(
                CAMPAIGN.EXTERNAL_P1_PAPER_PROFILE_CANDIDATE_PATH),
            "retained_legacy_path": str(
                CAMPAIGN.EXTERNAL_P1_PAPER_PROFILE_RETAINED_PATH),
            "exchange_method": "RENAME_EXCHANGE",
            "forward_only_after_exchange": True,
            "harden_intent_record_sha256": "sha256:" + "c" * 64,
            "harden_exchange_record_sha256": "sha256:" + "d" * 64,
        }
        now_ms = __import__("time").time_ns() // 1_000_000
        body = {
            "schema": CAMPAIGN.EXTERNAL_P1_HANDOFF_SCHEMA, "version": 2,
            "status": CAMPAIGN.EXTERNAL_P1_HANDOFF_STATUS,
            "issued_at_ms": now_ms - 1_000,
            "expires_at_ms": now_ms + 299_000,
            "round": 114, "domain": "alpha",
            "campaign_id": self.document["campaign_id"],
            "source_baseline_sha256": self.document[
                "source_baseline_sha256"],
            "producer": {"path": "/usr/libexec/test",
                         "file_sha256": "sha256:" + "1" * 64},
            "production_mode": "PRODUCTION_ROOT_SYSTEMD",
            "activation_receipt": {"path": "/var/lib/test/a.json",
                                   "file_sha256": "sha256:" + "2" * 64,
                                   "body_sha256": "sha256:" + "3" * 64},
            "p1_audit_receipt": {"path": str(self.p1_audit_path),
                                 "file_sha256": "sha256:" + "4" * 64,
                                 "body_sha256": "sha256:" + "5" * 64},
            "freeze_bundle": {"path": "/var/lib/test/f.json",
                              "file_sha256": "sha256:" + "6" * 64,
                              "body_sha256": "sha256:" + "7" * 64},
            "watch_units_inactive": True, "watch_authority_count": 0,
            "watch_socket_count": 0, "watch_timer_count": 0,
            "paper_units_inactive": True, "broker_deny_all": True,
            "kill_switch_engaged": True,
            "global_kill_switch_engaged": True, "identity_count": 0,
            "identity_manifest_sha256":
                CAMPAIGN.EXTERNAL_P1_DISABLED_IDENTITY_SHA256,
            "paper_profile_restored": True,
            "paper_profile_restoration": restoration,
            "profile_candidate_absent": True,
            "paper_runtime_profile_hardened": True,
            "paper_runtime_profile_hardening": hardening,
            "paper_runtime_profile_candidate_absent": True,
            "crash_recovery_verified": True, "cleanup_residue_count": 0,
            "paper_authorized": False, "live_authorized": False,
            "mutation_authorized": False, "direct_broker_access": False,
            "order_submission_authorized": False,
        }
        return {**body, "body_sha256": digest(canonical(body))}

    def _lease_reference(self) -> dict[str, object]:
        directory = self.host_authority_root.stat()
        lease = self.lease_path.stat()
        return {
            "directory_path": str(self.host_authority_root),
            "lease_path": str(self.lease_path),
            "owner_path": str(self.host_authority_root / "owner.v1"),
            "directory_device": directory.st_dev,
            "directory_inode": directory.st_ino,
            "directory_uid": directory.st_uid,
            "directory_gid": directory.st_gid,
            "directory_mode": 0o700,
            "lease_device": lease.st_dev,
            "lease_inode": lease.st_ino,
            "lease_uid": lease.st_uid,
            "lease_gid": lease.st_gid,
            "lease_mode": 0o600,
            "lease_size": 0,
            "held_exclusive": True,
            "boot_id": self.boot_id,
        }

    def _reservation_reference(self) -> dict[str, object]:
        return {
            "path": str(self.host_authority_root / "owner.v1"),
            "file_sha256": "sha256:" + "6" * 64,
            "body_sha256": "sha256:" + "7" * 64,
            "device": self.host_authority_root.stat().st_dev,
            "inode": 999_999, "uid": self.uid, "gid": self.gid,
            "mode": 0o600, "size": 1024,
            "mtime_ns": 1_000_000, "ctime_ns": 1_000_000,
        }

    def _zero_document(self) -> dict[str, object]:
        body = {
            field: None for field in ZERO_ATTESTOR.OUTPUT_FIELDS
            if field != "body_sha256"
        }
        body.update({
            "schema": ZERO_ATTESTOR.OUTPUT_SCHEMA,
            "version": 1, "status": "PASS",
            "observed_at_ms": 950_000, "expires_at_ms": 1_100_000,
            "round": 114, "domain": "alpha",
            "campaign_id": self.document["campaign_id"],
            "source_baseline_sha256": self.document[
                "source_baseline_sha256"],
            "host_authority_reservation": self._reservation_reference(),
            "reservation_id": self.reservation_id,
            "reservation_generation": 1,
            "reservation_lifecycle": ZERO_ATTESTOR.RESERVATION_LIFECYCLE,
            "reservation_predecessor_finalization_body_sha256": None,
            "reservation_prior_finalization_pointer_reference": None,
            "reservation_next_consumer": ZERO_ATTESTOR.RESERVATION_NEXT_CONSUMER,
            "reservation_continuity_verified": True,
            "reservation_finalization_tombstone_path": str(
                self.tombstone_path),
            "reservation_finalization_current_pointer_path": str(
                self.pointer_path),
            "reservation_finalization_tombstone_absent": True,
            "reservation_finalization_schema":
                ZERO_ATTESTOR.RESERVATION_FINALIZATION_SCHEMA,
            "reservation_finalization_order":
                ZERO_ATTESTOR.RESERVATION_FINALIZATION_ORDER,
            "reservation_boot_id": self.boot_id,
            "reservation_lease_device":
                self._lease_reference()["lease_device"],
            "reservation_lease_inode":
                self._lease_reference()["lease_inode"],
            "read_only_authority": True, "authoritative": True,
            "account_complete": True, "observation_complete": True,
            "broker_deny_all": True, "authorized_connectors": 0,
            "authorized_uids": [], "broker_socket_count": 0,
            "broker_process_count": 0, "credential_exposure_count": 0,
            "order_count": 0, "position_count": 0,
            "gross_absolute_position": 0, "end_flat": True,
            "paper_units_inactive": True, "kill_switch_engaged": True,
            "process_inventory_complete": True,
            "socket_inventory_complete": True,
            "credential_inventory_complete": True,
            "host_authority_lease": self._lease_reference(),
            "host_authority_lease_reacquired": True,
            "paper_authorized": False, "live_authorized": False,
            "mutation_authorized": False, "direct_broker_access": False,
            "order_submission_authorized": False,
        })
        return ZERO_ATTESTOR.seal(body)

    def _build_finalized_admission(self) -> None:
        original = copy.deepcopy(self.admission)
        original_raw = canonical(original)
        original_body_valid = self._document_body_valid(original)
        candidate_file_pin_matched = (
            self.document.get("admission_receipt_file_sha256") ==
            digest(original_raw))
        candidate_body_pin_matched = (
            self.document.get("admission_receipt_body_sha256") ==
            original.get("body_sha256"))

        self.zero = self._zero_document()
        self.write_zero()
        zero_reference = self._reference(self.zero_path, self.zero)
        bindings = self.admission.get("input_bindings")
        if isinstance(bindings, dict) and "zero_exposure_receipt" in bindings:
            bindings["zero_exposure_receipt"] = {
                **zero_reference, "schema": ZERO_ATTESTOR.OUTPUT_SCHEMA,
                "version": 1, "status": "PASS",
            }
        if original_body_valid:
            self.admission = reseal_admission(self.admission)
        self.write_admission()
        admission_raw = self.admission_path.read_bytes()
        admission_reference = {
            "path": str(self.admission_path),
            "file_sha256": digest(admission_raw),
            "body_sha256": self.admission["body_sha256"],
        }
        if candidate_file_pin_matched:
            self.document["admission_receipt_file_sha256"] = digest(
                admission_raw)
        if candidate_body_pin_matched:
            self.document["admission_receipt_body_sha256"] = self.admission[
                "body_sha256"]

        terminal_body = {
            field: None for field in ZERO_PRODUCER.RESERVATION_FINALIZATION_FIELDS
            if field != "body_sha256"
        }
        terminal_body.update({
            "schema": ZERO_PRODUCER.RESERVATION_FINALIZATION_SCHEMA,
            "version": 1, "status": "ADMISSION_GO",
            "finalized_at_ms": 960_000, "round": 114, "domain": "alpha",
            "campaign_id": self.document["campaign_id"],
            "source_baseline_sha256": self.document[
                "source_baseline_sha256"],
            "reservation_id": self.reservation_id,
            "reservation_generation": 1,
            "predecessor_finalization_body_sha256": None,
            "prior_finalization_pointer_reference": None,
            "boot_id": self.boot_id,
            "reservation_reference": self._reservation_reference(),
            "candidate_reference": admission_reference,
            "zero_exposure_receipt_reference": zero_reference,
            "host_authority_lease": self._lease_reference(),
            "recovery_observation": None,
            "owner_present_at_tombstone_commit": True,
            "owner_removal_required_after_commit": True,
            "finalization_order": ZERO_PRODUCER.RESERVATION_FINALIZATION_ORDER,
            "recovery_reason": None,
            "paper_authorized": False, "live_authorized": False,
            "mutation_authorized": False, "direct_broker_access": False,
            "order_submission_authorized": False,
        })
        self.tombstone = ZERO_PRODUCER.seal(terminal_body)
        self.write_tombstone()
        tombstone_reference = self._reference(
            self.tombstone_path, self.tombstone)
        pointer_body = {
            field: None for field in ZERO_PRODUCER.RESERVATION_CURRENT_POINTER_FIELDS
            if field != "body_sha256"
        }
        pointer_body.update({
            "schema": ZERO_PRODUCER.RESERVATION_CURRENT_POINTER_SCHEMA,
            "version": 1, "status": "CURRENT",
            "updated_at_ms": 960_000, "round": 114, "domain": "alpha",
            "campaign_id": self.document["campaign_id"],
            "source_baseline_sha256": self.document[
                "source_baseline_sha256"],
            "boot_id": self.boot_id, "reservation_id": self.reservation_id,
            "reservation_generation": 1,
            "predecessor_finalization_body_sha256": None,
            "finalization_tombstone_reference": tombstone_reference,
            "host_authority_lease": self._lease_reference(),
            "paper_authorized": False, "live_authorized": False,
            "mutation_authorized": False, "direct_broker_access": False,
            "order_submission_authorized": False,
        })
        self.pointer = ZERO_PRODUCER.seal(pointer_body)
        self.write_pointer()
        self.pin_terminal_policy()

    def pin_terminal_policy(self) -> None:
        self.document.update({
            "admission_finalization_current_pointer_path": str(
                self.pointer_path),
            "admission_finalization_current_pointer_file_sha256": digest(
                canonical(self.pointer)),
            "admission_finalization_current_pointer_body_sha256":
                self.pointer["body_sha256"],
            "admission_finalization_tombstone_path": str(self.tombstone_path),
            "admission_finalization_tombstone_file_sha256": digest(
                canonical(self.tombstone)),
            "admission_finalization_tombstone_body_sha256":
                self.tombstone["body_sha256"],
        })

    def write_admission(self, raw: bytes | None = None) -> None:
        self.admission_path.write_bytes(
            canonical(self.admission) if raw is None else raw)
        self.admission_path.chmod(0o600)

    def write_zero(self) -> None:
        self.zero_path.write_bytes(canonical(self.zero))
        self.zero_path.chmod(0o600)

    def write_tombstone(self) -> None:
        self.tombstone_path.write_bytes(canonical(self.tombstone))
        self.tombstone_path.chmod(0o600)

    def write_pointer(self) -> None:
        self.pointer_path.write_bytes(canonical(self.pointer))
        self.pointer_path.chmod(0o600)

    def read_admission(
            self, policy: CAMPAIGN.CampaignPolicy, now_ms: int,
    ) -> CAMPAIGN.FinalizedAdmissionSession:
        self.admission_reads += 1
        self.last_session = CAMPAIGN.open_finalized_admission_session(
            self.admission_root, policy, now_ms,
            expected_uid=self.uid, expected_gid=self.gid,
            host_authority_root=self.host_authority_root,
            boot_id_path=self.boot_id_path)
        return self.last_session

    def provider(self):
        raw = canonical(self.document)
        return CAMPAIGN.parse_policy(raw, "alpha"), CAMPAIGN._sha256(raw)

    def read_deployment(
            self, policy: CAMPAIGN.CampaignPolicy,
    ) -> CAMPAIGN.LocalDeploymentSnapshot:
        self.deployment_reads += 1
        generation = self.deployment_generation
        return CAMPAIGN.LocalDeploymentSnapshot(
            payload=f"deployment-{generation}\n".encode("ascii"),
            document={"generation": generation},
            evidence_identity=(generation,), certified_identity=(generation,),
            installed_identities=(),
            file_sha256=str(policy.deployment_evidence_file_sha256),
            body_sha256=str(policy.deployment_evidence_body_sha256),
            source_baseline_sha256=str(policy.source_baseline_sha256),
            install_transaction_id=str(
                policy.deployment_install_transaction_id))

    def controller(self):
        return CAMPAIGN.CampaignController(
            self.provider, self.operator, self.paths, self.clock,
            root_uid=self.uid, root_gid=self.gid,
            admission_provider=self.read_admission,
            deployment_provider=self.read_deployment,
            now_monotonic_ms=self.monotonic_clock)


class PaperCampaignOperatorTests(unittest.TestCase):
    def test_finalization_contracts_match_independent_real_producers(
            self) -> None:
        self.assertEqual(
            CAMPAIGN.FINALIZATION_FIELDS,
            set(ZERO_PRODUCER.RESERVATION_FINALIZATION_FIELDS))
        self.assertEqual(
            CAMPAIGN.FINALIZATION_POINTER_FIELDS,
            set(ZERO_PRODUCER.RESERVATION_CURRENT_POINTER_FIELDS))
        self.assertEqual(
            CAMPAIGN.ZERO_EXPOSURE_FIELDS,
            set(ZERO_ATTESTOR.OUTPUT_FIELDS))
        self.assertEqual(
            CAMPAIGN.FINALIZATION_SCHEMA,
            ZERO_PRODUCER.RESERVATION_FINALIZATION_SCHEMA)
        self.assertEqual(
            CAMPAIGN.FINALIZATION_POINTER_SCHEMA,
            ZERO_PRODUCER.RESERVATION_CURRENT_POINTER_SCHEMA)
        self.assertEqual(
            CAMPAIGN.ZERO_EXPOSURE_SCHEMA, ZERO_ATTESTOR.OUTPUT_SCHEMA)
        self.assertEqual(
            CAMPAIGN.FINALIZATION_ORDER,
            ZERO_PRODUCER.RESERVATION_FINALIZATION_ORDER)

    def test_admission_input_contract_matches_real_producer_and_is_accepted(
            self) -> None:
        self.assertEqual(
            frozenset(CAMPAIGN.ADMISSION_INPUT_NAMES),
            EXPECTED_ADMISSION_INPUT_NAMES)
        self.assertEqual(
            frozenset(ADMISSION_VERIFIER.INPUT_NAMES),
            EXPECTED_ADMISSION_INPUT_NAMES)
        admission = admission_document()
        ADMISSION_VERIFIER.validate_output_receipt(admission)
        policy_document_value = policy_document()
        pin_admission(policy_document_value, admission)
        with tempfile.TemporaryDirectory(
                prefix="hepta-campaign-real-admission-contract-") as directory:
            fixture = CampaignFixture(
                Path(directory), policy_document_value, admission=admission)
            policy, _digest = fixture.provider()
            response = fixture.controller().process(open_request(policy))
            self.assertEqual(response["status"], "ok")
            self.assertEqual(response["state"]["status"], "open")
            self.assertEqual(len(fixture.operator.disarms), 1)

    def test_legacy_fourteen_input_candidate_never_disarms(self) -> None:
        self.assertEqual(len(LEGACY_ADMISSION_INPUT_NAMES), 14)
        admission = admission_document()
        admission["input_bindings"] = {
            name: admission["input_bindings"][name]
            for name in LEGACY_ADMISSION_INPUT_NAMES
        }
        admission = reseal_admission(admission)
        policy_document_value = policy_document()
        pin_admission(policy_document_value, admission)
        with tempfile.TemporaryDirectory(
                prefix="hepta-campaign-legacy-admission-inputs-") as directory:
            fixture = CampaignFixture(
                Path(directory), policy_document_value, admission=admission)
            policy, _digest = fixture.provider()
            response = fixture.controller().process(open_request(policy))
            self.assertEqual(
                response["reason_code"],
                "CAMPAIGN_ADMISSION_BINDINGS_INVALID")
            self.assertFalse(fixture.operator.disarms)

    def test_candidate_missing_any_required_admission_input_never_disarms(
            self) -> None:
        for missing in sorted(EXPECTED_ADMISSION_INPUT_NAMES):
            with self.subTest(missing=missing), tempfile.TemporaryDirectory(
                    prefix="hepta-campaign-missing-admission-input-") \
                    as directory:
                admission = admission_document()
                del admission["input_bindings"][missing]
                admission = reseal_admission(admission)
                policy_document_value = policy_document()
                pin_admission(policy_document_value, admission)
                fixture = CampaignFixture(
                    Path(directory), policy_document_value,
                    admission=admission)
                policy, _digest = fixture.provider()
                response = fixture.controller().process(open_request(policy))
                self.assertEqual(
                    response["reason_code"],
                    "CAMPAIGN_ADMISSION_BINDINGS_INVALID")
                self.assertFalse(fixture.operator.disarms)

    def test_installed_example_is_disabled_and_live_closed(self) -> None:
        path = (
            ROOT / "systemd/"
            "hepta-ib-paper-campaign-policy-v1.json.example")
        policy = CAMPAIGN.parse_policy(path.read_bytes(), "alpha")
        self.assertFalse(policy.enabled)
        self.assertFalse(policy.mutations_authorized)
        document = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(policy.version, 1)
        self.assertTrue(document["paper_only"])
        self.assertFalse(document["live_authorized"])

        local_path = (
            ROOT / "systemd/"
            "hepta-ib-paper-campaign-policy-local-v4.json.example")
        local = json.loads(local_path.read_text(encoding="utf-8"))
        self.assertFalse(local["enabled"])
        self.assertFalse(local["mutations_authorized"])
        self.assertEqual(
            local["deployment_evidence_file_sha256"],
            "sha256:" + "0" * 64)
        self.assertEqual(
            local["deployment_evidence_body_sha256"],
            "sha256:" + "0" * 64)
        with self.assertRaisesRegex(
                CAMPAIGN.CampaignError,
                "CAMPAIGN_POLICY_SOURCE_BASELINE_INVALID"):
            CAMPAIGN.parse_policy(local_path.read_bytes(), "alpha")

    def test_v1_is_disabled_only_and_can_never_open(self) -> None:
        active = legacy_policy_document(enabled=True)
        with self.assertRaisesRegex(
                CAMPAIGN.CampaignError,
                "CAMPAIGN_POLICY_V1_ACTIVE_FORBIDDEN"):
            CAMPAIGN.parse_policy(canonical(active), "alpha")

        with tempfile.TemporaryDirectory(
                prefix="hepta-campaign-v1-closed-") as directory:
            fixture = CampaignFixture(
                Path(directory), legacy_policy_document())
            request_policy = CAMPAIGN.parse_policy(
                canonical(policy_document()), "alpha")
            response = fixture.controller().process(
                open_request(request_policy))
            self.assertEqual(response["status"], "rejected")
            self.assertEqual(
                response["reason_code"],
                "CAMPAIGN_POLICY_VERSION_UNSUPPORTED")
            self.assertFalse(fixture.operator.disarms)
            self.assertEqual(fixture.admission_reads, 0)

    def test_v2_is_disabled_only_and_active_authority_requires_v3(self) -> None:
        active = legacy_v2_policy_document(enabled=True)
        with self.assertRaisesRegex(
                CAMPAIGN.CampaignError,
                "CAMPAIGN_POLICY_V2_ACTIVE_FORBIDDEN"):
            CAMPAIGN.parse_policy(canonical(active), "alpha")

        disabled = CAMPAIGN.parse_policy(
            canonical(legacy_v2_policy_document()), "alpha")
        self.assertEqual(disabled.version, 2)
        self.assertFalse(disabled.enabled)
        self.assertFalse(disabled.mutations_authorized)
        with tempfile.TemporaryDirectory(
                prefix="hepta-campaign-v2-closed-") as directory:
            fixture = CampaignFixture(
                Path(directory), legacy_v2_policy_document())
            request_policy = CAMPAIGN.parse_policy(
                canonical(policy_document()), "alpha")
            response = fixture.controller().process(
                open_request(request_policy))
            self.assertEqual(response["status"], "rejected")
            self.assertEqual(
                response["reason_code"],
                "CAMPAIGN_POLICY_VERSION_UNSUPPORTED")
            self.assertFalse(fixture.operator.disarms)
            self.assertEqual(fixture.admission_reads, 0)

    def _open_and_close_v5(
            self, fixture: CampaignFixture,
    ) -> tuple[
            CAMPAIGN.CampaignController, CAMPAIGN.CampaignPolicy,
            dict[str, object]]:
        controller = fixture.controller()
        policy, _digest = fixture.provider()
        opened = open_request(policy)
        self.assertEqual(controller.process(opened)["status"], "ok")
        fixture.clock.value = 1_005_000
        closed = controller.process(close_request(
            policy, str(opened["intent_sha256"])))
        self.assertEqual(closed["status"], "ok")
        self.assertEqual(closed["state"]["status"], "halted")
        self.assertEqual(closed["state"]["halt_reason"], "CAMPAIGN_COMPLETE")
        return controller, policy, opened

    def _write_legacy_state(
            self, fixture: CampaignFixture, status: str,
    ) -> tuple[CAMPAIGN.CampaignPolicy, Path, dict[str, object]]:
        policy, policy_sha256 = fixture.provider()
        state = CAMPAIGN._new_state(
            policy, policy_sha256, fixture.clock())
        state["schema"] = CAMPAIGN.LEGACY_STATE_SCHEMA
        state["version"] = 1
        for field in CAMPAIGN.STATE_FIELDS - CAMPAIGN.LEGACY_STATE_FIELDS:
            del state[field]
        state["status"] = status
        if status in {"opening", "open", "closing"}:
            state["cycles_opened"] = 1
            state["active_cycle"] = {
                "cycle_id": "legacy-cycle-a",
                "intent_sha256": "sha256:" + "4" * 64,
                "preflight_sha256": "sha256:" + "5" * 64,
                "opened_at_ms": 1_000_000,
                "deadline_at_ms": 1_020_000,
            }
        fixture.paths.runtime_root.mkdir(mode=0o700)
        state_path = fixture.paths.runtime_root / "alpha.json"
        state_path.write_bytes(canonical(state))
        state_path.chmod(0o600)
        return policy, state_path, state

    @staticmethod
    def _status_request(
            policy: CAMPAIGN.CampaignPolicy, request_id: str,
    ) -> dict[str, object]:
        return {
            "schema": CAMPAIGN.REQUEST_SCHEMA,
            "version": 1,
            "action": "status",
            "request_id": request_id,
            "domain_id": policy.domain_id,
            "campaign_id": policy.campaign_id,
        }

    def test_v5_policy_is_exact_single_quantity_one_lmt_day_canary(
            self) -> None:
        self.assertEqual(
            CAMPAIGN.POLICY_V5_FIELDS,
            CAMPAIGN.POLICY_V3_FIELDS | {
                "admission_mode", "deployment_evidence_file_sha256",
                "deployment_evidence_body_sha256",
                "deployment_install_transaction_id",
                "p1_audit_receipt_path",
                "p1_audit_receipt_file_sha256",
                "p1_audit_receipt_body_sha256",
                "watch_handoff_receipt_path",
                "watch_handoff_receipt_file_sha256",
                "watch_handoff_receipt_body_sha256",
            })
        document = v5_policy_document(max_cycles=1)
        document["valid_after_ms"] = 1_000_000
        document["expires_at_ms"] = (
            1_000_000 + CAMPAIGN.EXTERNAL_P1_CAMPAIGN_DURATION_MS)
        policy = CAMPAIGN.parse_policy(canonical(document), "alpha")
        self.assertEqual(policy.version, 5)
        self.assertEqual(policy.order_type, "LMT")
        self.assertEqual(policy.admission_mode, "external-p1-finalized")
        self.assertEqual(policy.max_cycles, 1)
        self.assertEqual(policy.max_quantity, 1)

        zero_holding = v5_policy_document()
        zero_holding["max_holding_ms"] = 0
        zero_holding_policy = CAMPAIGN.parse_policy(
            canonical(zero_holding), "alpha")
        zero_holding_intent = trade_intent(
            order_type="LMT", limit_price=1.10001)
        zero_holding_intent["max_holding_ms"] = 0
        CAMPAIGN.validate_trade_intent(
            zero_holding_intent, zero_holding_policy, 1_000_000)

        market = dict(document)
        market["order_type"] = "MKT"
        with self.assertRaisesRegex(
                CAMPAIGN.CampaignError,
                "CAMPAIGN_POLICY_SAFETY_BOUNDARY_INVALID"):
            CAMPAIGN.parse_policy(canonical(market), "alpha")

        too_long = dict(document)
        too_long["expires_at_ms"] = (
            int(too_long["valid_after_ms"]) +
            CAMPAIGN.EXTERNAL_P1_CAMPAIGN_DURATION_MS + 1)
        with self.assertRaisesRegex(
                CAMPAIGN.CampaignError,
                "CAMPAIGN_POLICY_EXTERNAL_CANARY_INVALID"):
            CAMPAIGN.parse_policy(canonical(too_long), "alpha")

        too_many = dict(document)
        too_many["max_cycles"] = 2
        with self.assertRaisesRegex(
                CAMPAIGN.CampaignError,
                "CAMPAIGN_POLICY_EXTERNAL_CANARY_INVALID"):
            CAMPAIGN.parse_policy(canonical(too_many), "alpha")

        too_large = dict(document)
        too_large["max_quantity"] = 2
        with self.assertRaisesRegex(
                CAMPAIGN.CampaignError,
                "CAMPAIGN_POLICY_EXTERNAL_CANARY_INVALID"):
            CAMPAIGN.parse_policy(canonical(too_large), "alpha")

        wrong_mode = dict(document)
        wrong_mode["admission_mode"] = "local-only"
        with self.assertRaisesRegex(
                CAMPAIGN.CampaignError,
                "CAMPAIGN_POLICY_ADMISSION_MODE_INVALID"):
            CAMPAIGN.parse_policy(canonical(wrong_mode), "alpha")

    def test_v5_local_policy_is_exact_mkt_day_and_bounded_to_24h_720_cycles(
            self) -> None:
        self.assertEqual(
            CAMPAIGN.POLICY_V5_LOCAL_FIELDS,
            CAMPAIGN.POLICY_V4_FIELDS)
        document = local_v5_policy_document(max_cycles=720)
        document["max_quantity"] = 25_000
        document["valid_after_ms"] = 1_000_000
        document["expires_at_ms"] = (
            1_000_000 + CAMPAIGN.MAX_V5_CAMPAIGN_DURATION_MS)
        policy = CAMPAIGN.parse_policy(canonical(document), "alpha")
        self.assertEqual(policy.version, 5)
        self.assertEqual(policy.admission_mode, "local-only")
        self.assertEqual(policy.order_type, "MKT")
        self.assertEqual(policy.max_cycles, 720)
        self.assertEqual(policy.max_quantity, 25_000)
        self.assertIsNone(policy.admission_receipt_name)
        self.assertIsNone(policy.p1_audit_receipt_path)
        self.assertIsNone(policy.watch_handoff_receipt_path)

        intent = trade_intent(order_type="MKT")
        validated, _intent_sha256 = CAMPAIGN.validate_trade_intent(
            intent, policy, 1_000_000)
        self.assertEqual(validated["reference_price"], 1.10001)
        self.assertNotIn("limit_price", validated)

        wrong_order_type = dict(document)
        wrong_order_type["order_type"] = "LMT"
        with self.assertRaisesRegex(
                CAMPAIGN.CampaignError,
                "CAMPAIGN_POLICY_SAFETY_BOUNDARY_INVALID"):
            CAMPAIGN.parse_policy(canonical(wrong_order_type), "alpha")

        too_long = dict(document)
        too_long["expires_at_ms"] = (
            int(too_long["valid_after_ms"]) +
            CAMPAIGN.MAX_V5_CAMPAIGN_DURATION_MS + 1)
        with self.assertRaisesRegex(
                CAMPAIGN.CampaignError,
                "CAMPAIGN_POLICY_ACTIVE_WINDOW_INVALID"):
            CAMPAIGN.parse_policy(canonical(too_long), "alpha")

        too_many = dict(document)
        too_many["max_cycles"] = CAMPAIGN.MAX_V5_CAMPAIGN_CYCLES + 1
        with self.assertRaisesRegex(
                CAMPAIGN.CampaignError,
                "CAMPAIGN_POLICY_CYCLE_LIMIT_INVALID"):
            CAMPAIGN.parse_policy(canonical(too_many), "alpha")

    def test_v5_local_open_skips_p1_and_reopens_deployment(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-campaign-v5-local-open-") as directory:
            fixture = CampaignFixture(
                Path(directory), local_v5_policy_document())
            policy, _digest = fixture.provider()
            response = fixture.controller().process(open_request(policy))
            self.assertEqual(response["status"], "ok")
            self.assertEqual(response["state"]["status"], "open")
            self.assertEqual(fixture.admission_reads, 0)
            self.assertEqual(fixture.deployment_reads, 2)
            self.assertEqual(len(fixture.operator.disarms), 1)
            self.assertFalse(fixture.operator.reengages)
            self.assertFalse(any(
                path.name.startswith("consumption.")
                for path in fixture.paths.receipt_root.iterdir()))

    def test_v5_local_deployment_failure_never_disarms(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-campaign-v5-local-deployment-") as directory:
            fixture = CampaignFixture(
                Path(directory), local_v5_policy_document())
            policy, _digest = fixture.provider()
            controller = CAMPAIGN.CampaignController(
                fixture.provider, fixture.operator, fixture.paths,
                fixture.clock, root_uid=fixture.uid, root_gid=fixture.gid,
                admission_provider=fixture.read_admission,
                deployment_provider=mock.Mock(side_effect=
                    CAMPAIGN.CampaignError(
                        "CAMPAIGN_DEPLOYMENT_EVIDENCE_INVALID")),
                now_monotonic_ms=fixture.monotonic_clock)
            response = controller.process(open_request(policy))
            self.assertEqual(response["status"], "rejected")
            self.assertEqual(
                response["reason_code"],
                "CAMPAIGN_DEPLOYMENT_EVIDENCE_INVALID")
            self.assertFalse(fixture.operator.disarms)
            self.assertFalse(fixture.operator.reengages)
            self.assertEqual(fixture.admission_reads, 0)

    def test_v5_local_deployment_drift_reengages_and_halts(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-campaign-v5-local-deployment-drift-") \
                as directory:
            fixture = CampaignFixture(
                Path(directory), local_v5_policy_document())
            fixture.operator.on_disarm = lambda: setattr(
                fixture, "deployment_generation",
                fixture.deployment_generation + 1)
            policy, _digest = fixture.provider()
            response = fixture.controller().process(open_request(policy))
            self.assertEqual(response["status"], "rejected")
            self.assertEqual(
                response["reason_code"],
                "CAMPAIGN_DEPLOYMENT_CHANGED_DURING_OPEN")
            self.assertEqual(response["state"]["status"], "halted")
            self.assertEqual(len(fixture.operator.disarms), 1)
            self.assertEqual(len(fixture.operator.reengages), 1)
            self.assertEqual(fixture.deployment_reads, 2)
            self.assertEqual(fixture.admission_reads, 0)

    def test_v5_local_intent_expiry_during_open_reengages_and_halts(
            self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-campaign-v5-local-intent-expiry-") \
                as directory:
            fixture = CampaignFixture(
                Path(directory), local_v5_policy_document(),
                clock=SequenceClock(1_000_000, 1_010_000))
            policy, _digest = fixture.provider()
            response = fixture.controller().process(open_request(policy))
            self.assertEqual(response["status"], "rejected")
            self.assertEqual(
                response["reason_code"], "CAMPAIGN_INTENT_TIME_INVALID")
            self.assertEqual(response["state"]["status"], "halted")
            self.assertEqual(len(fixture.operator.disarms), 1)
            self.assertEqual(len(fixture.operator.reengages), 1)
            self.assertEqual(fixture.deployment_reads, 2)
            self.assertEqual(fixture.admission_reads, 0)

    def test_v5_direct_p1_and_handoff_pin_drift_never_disarms(self) -> None:
        for field in (
                "p1_audit_receipt_path",
                "p1_audit_receipt_file_sha256",
                "p1_audit_receipt_body_sha256",
                "watch_handoff_receipt_path",
                "watch_handoff_receipt_file_sha256",
                "watch_handoff_receipt_body_sha256"):
            with self.subTest(field=field), tempfile.TemporaryDirectory(
                    prefix="hepta-campaign-v5-direct-pin-") as directory:
                document = v5_policy_document()
                document[field] = (
                    "/var/lib/hepta/evidence/drifted.json"
                    if field.endswith("_path") else
                    "sha256:" + "9" * 64)
                fixture = CampaignFixture(Path(directory), document)
                if field == "watch_handoff_receipt_path":
                    with self.assertRaisesRegex(
                            CAMPAIGN.CampaignError,
                            "CAMPAIGN_POLICY_P1_HANDOFF_PIN_INVALID"):
                        fixture.provider()
                    self.assertFalse(fixture.operator.disarms)
                    continue
                policy, _digest = fixture.provider()
                response = fixture.controller().process(
                    open_request(policy))
                self.assertEqual(response["status"], "rejected")
                self.assertEqual(
                    response["reason_code"],
                    "CAMPAIGN_ADMISSION_DIRECT_PIN_MISMATCH")
                self.assertFalse(fixture.operator.disarms)
                self.assertFalse(fixture.operator.reengages)
                self.assertEqual(fixture.deployment_reads, 0)

    def test_v5_candidate_metadata_must_match_physical_pinned_evidence(
            self) -> None:
        for name in ("p1_audit_receipt", "watch_handoff_receipt"):
            for field, value in (
                    ("schema", "hepta.wrong-evidence.v1"),
                    ("version", 2), ("status", "STALE")):
                with self.subTest(name=name, field=field), \
                        tempfile.TemporaryDirectory(
                            prefix="hepta-campaign-v5-evidence-metadata-") \
                        as directory:
                    admission = admission_document()
                    admission["input_bindings"][name][field] = value
                    admission = reseal_admission(admission)
                    document = v5_policy_document()
                    pin_admission(document, admission)
                    fixture = CampaignFixture(
                        Path(directory), document, admission=admission)
                    policy, _digest = fixture.provider()
                    response = fixture.controller().process(
                        open_request(policy))
                    self.assertEqual(response["status"], "rejected")
                    self.assertEqual(
                        response["reason_code"],
                        ("CAMPAIGN_ADMISSION_DIRECT_PIN_MISMATCH"
                         if name == "watch_handoff_receipt" else
                         "CAMPAIGN_PINNED_EVIDENCE_INVALID"))
                    self.assertEqual(
                        response["state"]["status"],
                        "idle" if name == "watch_handoff_receipt" else
                        "halted")
                    self.assertFalse(fixture.operator.disarms)
                    self.assertEqual(
                        len(fixture.operator.reengages),
                        0 if name == "watch_handoff_receipt" else 1)

    def test_v5_lmt_price_is_exactly_bound_to_the_executable_quote(
            self) -> None:
        policy = CAMPAIGN.parse_policy(
            canonical(v5_policy_document()), "alpha")
        buy = trade_intent(order_type="LMT", limit_price=1.10001)
        CAMPAIGN.validate_trade_intent(buy, policy, 1_000_000)
        buy_one_tick_away = dict(buy)
        buy_one_tick_away["limit_price"] = 1.10002
        with self.assertRaisesRegex(
                CAMPAIGN.CampaignError,
                "CAMPAIGN_INTENT_QUOTE_INVALID"):
            CAMPAIGN.validate_trade_intent(
                buy_one_tick_away, policy, 1_000_000)

        sell = trade_intent(order_type="LMT", limit_price=1.10000)
        sell["side"] = "SELL"
        CAMPAIGN.validate_trade_intent(sell, policy, 1_000_000)
        sell_one_tick_away = dict(sell)
        sell_one_tick_away["limit_price"] = 1.09999
        with self.assertRaisesRegex(
                CAMPAIGN.CampaignError,
                "CAMPAIGN_INTENT_QUOTE_INVALID"):
            CAMPAIGN.validate_trade_intent(
                sell_one_tick_away, policy, 1_000_000)

    def test_v5_open_reopens_finalized_admission_and_deployment(
            self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-campaign-v5-open-") as directory:
            fixture = CampaignFixture(Path(directory), v5_policy_document())
            policy, _digest = fixture.provider()
            response = fixture.controller().process(open_request(policy))
            self.assertEqual(response["status"], "ok")
            self.assertEqual(response["state"]["status"], "open")
            self.assertEqual(fixture.admission_reads, 1)
            self.assertEqual(fixture.deployment_reads, 2)
            self.assertEqual(len(fixture.operator.disarms), 1)
            self.assertFalse(fixture.operator.reengages)
            self.assertIsNotNone(fixture.last_session)
            self.assertEqual(fixture.last_session.reopen_count, 1)
            consumption_path = fixture.paths.receipt_root / (
                CAMPAIGN._consumption_receipt_name(policy))
            metadata = consumption_path.stat()
            self.assertTrue(stat.S_ISREG(metadata.st_mode))
            self.assertEqual(metadata.st_nlink, 1)
            self.assertEqual(stat.S_IMODE(metadata.st_mode), 0o600)
            consumption = json.loads(
                consumption_path.read_text(encoding="ascii"))
            self.assertEqual(consumption["status"], "CONSUMED")
            self.assertEqual(consumption["consumed_at_ms"], 1_000_000)
            self.assertEqual(consumption["monotonic_clock"], "CLOCK_BOOTTIME")
            self.assertEqual(
                consumption["consumed_monotonic_ms"], 5_000_000)
            self.assertEqual(
                consumption["monotonic_expires_at_ms"] -
                consumption["consumed_monotonic_ms"],
                policy.expires_at_ms - consumption["consumed_at_ms"])
            self.assertEqual(
                consumption["policy_file_sha256"], _digest)
            self.assertEqual(
                consumption["p1_audit_receipt_reference"]["path"],
                str(policy.p1_audit_receipt_path))
            self.assertEqual(
                consumption["p1_audit_receipt_snapshot"]["identity"],
                list(CAMPAIGN._metadata_identity(
                    fixture.p1_audit_path.stat(),
                    CAMPAIGN._FILE_IDENTITY_FIELDS)))
            self.assertEqual(
                consumption["watch_handoff_receipt_snapshot"]["identity"],
                list(CAMPAIGN._metadata_identity(
                    fixture.watch_handoff_path.stat(),
                    CAMPAIGN._FILE_IDENTITY_FIELDS)))
            self.assertTrue(
                consumption["p1_audit_receipt_snapshot"]["anchor_identity"])

    def test_v5_runtime_profile_hardening_drift_never_disarms(self) -> None:
        for label in ("target", "legacy-backup", "candidate"):
            with self.subTest(label=label), tempfile.TemporaryDirectory(
                    prefix="hepta-campaign-v5-paper-profile-") as directory:
                fixture = CampaignFixture(
                    Path(directory), v5_policy_document())
                path, mode = {
                    "target": (
                        CAMPAIGN.EXTERNAL_P1_PAPER_PROFILE_PATH, 0o644),
                    "legacy-backup": (
                        CAMPAIGN.EXTERNAL_P1_PAPER_PROFILE_BACKUP_PATH, 0o600),
                    "candidate": (
                        CAMPAIGN.EXTERNAL_P1_PAPER_PROFILE_CANDIDATE_PATH,
                        0o644),
                }[label]
                path.write_bytes(b"drifted-runtime-profile\n")
                path.chmod(mode)
                policy, _digest = fixture.provider()
                response = fixture.controller().process(open_request(policy))
                self.assertEqual(response["status"], "rejected")
                self.assertFalse(fixture.operator.disarms)

    def test_v5_single_canary_consumes_once_and_second_cycle_is_closed(
            self) -> None:
        """The external v5 contract permits exactly one broker-free cycle."""
        with tempfile.TemporaryDirectory(
                prefix="hepta-campaign-v5-two-cycle-") as directory:
            fixture = CampaignFixture(
                Path(directory), v5_policy_document(max_cycles=1),
                clock=FakeClock(1_000_000),
                monotonic_clock=FakeClock(5_000_000))
            controller = fixture.controller()
            policy, _policy_sha256 = fixture.provider()

            first = open_request(policy)
            first_open = controller.process(first)
            self.assertEqual(first_open["status"], "ok")
            consumption_path = fixture.paths.receipt_root / (
                CAMPAIGN._consumption_receipt_name(policy))
            first_raw = consumption_path.read_bytes()
            first_identity = CAMPAIGN._metadata_identity(
                consumption_path.stat(), CAMPAIGN._FILE_IDENTITY_FIELDS)
            first_document = json.loads(first_raw)
            self.assertEqual(first_document["status"], "CONSUMED")

            fixture.clock.value = 1_005_000
            first_close = controller.process(close_request(
                policy, str(first["intent_sha256"])))
            self.assertEqual(first_close["status"], "ok")
            self.assertEqual(first_close["state"]["status"], "halted")
            self.assertEqual(
                first_close["state"]["halt_reason"], "CAMPAIGN_COMPLETE")

            fixture.clock.value = 1_010_000
            fixture.monotonic_clock.value = 5_010_000
            fixture.operator.deadline_at_ms = 1_030_000
            second = open_request(
                policy, request_id="request-open-b", cycle_id="cycle-b",
                intent=trade_intent(
                    observed_at_ms=1_009_000, expires_at_ms=1_020_000,
                    order_type="LMT", limit_price=1.10001),
                now_ms=1_010_000)
            second_open = controller.process(second)
            self.assertEqual(second_open["status"], "rejected")
            self.assertEqual(second_open["reason_code"], "CAMPAIGN_NOT_IDLE")
            self.assertEqual(second_open["state"]["cycles_opened"], 1)
            self.assertEqual(consumption_path.read_bytes(), first_raw)
            self.assertEqual(
                CAMPAIGN._metadata_identity(
                    consumption_path.stat(), CAMPAIGN._FILE_IDENTITY_FIELDS),
                first_identity)

            self.assertEqual(len(fixture.operator.disarms), 1)
            self.assertEqual(len(fixture.operator.reengages), 1)
            self.assertEqual(consumption_path.read_bytes(), first_raw)

    def test_v5_completed_canary_cannot_reopen_after_evidence_replacement(
            self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-campaign-v5-evidence-between-") as directory:
            fixture = CampaignFixture(
                Path(directory), v5_policy_document(max_cycles=1))
            controller, policy, _opened = self._open_and_close_v5(fixture)
            replacement = fixture.evidence_root / ".replace-p1"
            replacement.write_bytes(fixture.p1_audit_path.read_bytes())
            replacement.chmod(0o600)
            os.replace(replacement, fixture.p1_audit_path)
            fixture.clock.value = 1_020_000
            fixture.operator.deadline_at_ms = 1_040_000
            response = controller.process(open_request(
                policy, request_id="request-p1-replaced-between",
                cycle_id="cycle-p1-replaced-between",
                intent=trade_intent(
                    observed_at_ms=1_019_000, expires_at_ms=1_030_000,
                    order_type="LMT", limit_price=1.10001),
                now_ms=1_020_000))
            self.assertEqual(response["status"], "rejected")
            self.assertEqual(response["reason_code"], "CAMPAIGN_NOT_IDLE")
            self.assertEqual(response["state"]["status"], "halted")
            self.assertEqual(len(fixture.operator.disarms), 1)
            self.assertEqual(len(fixture.operator.reengages), 1)

    def test_v5_pinned_evidence_replacement_during_disarm_reengages_and_halts(
            self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-campaign-v5-evidence-open-replace-") \
                as directory:
            fixture = CampaignFixture(Path(directory), v5_policy_document())

            def replace_handoff() -> None:
                replacement = fixture.evidence_root / ".replace-handoff"
                replacement.write_bytes(
                    fixture.watch_handoff_path.read_bytes())
                replacement.chmod(0o600)
                os.replace(replacement, fixture.watch_handoff_path)

            fixture.operator.on_disarm = replace_handoff
            policy, _digest = fixture.provider()
            response = fixture.controller().process(open_request(policy))
            self.assertEqual(response["status"], "rejected")
            self.assertEqual(
                response["reason_code"],
                "CAMPAIGN_PINNED_EVIDENCE_CHANGED_DURING_OPEN")
            self.assertEqual(response["state"]["status"], "halted")
            self.assertEqual(len(fixture.operator.disarms), 1)
            self.assertEqual(len(fixture.operator.reengages), 1)

    def test_v5_pinned_evidence_deletion_during_disarm_reengages_and_halts(
            self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-campaign-v5-evidence-open-delete-") as directory:
            fixture = CampaignFixture(Path(directory), v5_policy_document())
            fixture.operator.on_disarm = fixture.p1_audit_path.unlink
            policy, _digest = fixture.provider()
            response = fixture.controller().process(open_request(policy))
            self.assertEqual(response["status"], "rejected")
            self.assertEqual(
                response["reason_code"],
                "CAMPAIGN_PINNED_EVIDENCE_CHANGED_DURING_OPEN")
            self.assertEqual(response["state"]["status"], "halted")
            self.assertEqual(len(fixture.operator.disarms), 1)
            self.assertEqual(len(fixture.operator.reengages), 1)

    def test_v5_pinned_evidence_metadata_drift_reengages_and_halts(
            self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-campaign-v5-evidence-open-mode-") as directory:
            fixture = CampaignFixture(Path(directory), v5_policy_document())
            fixture.operator.on_disarm = lambda: (
                fixture.watch_handoff_path.chmod(0o640))
            policy, _digest = fixture.provider()
            response = fixture.controller().process(open_request(policy))
            self.assertEqual(response["status"], "rejected")
            self.assertEqual(
                response["reason_code"],
                "CAMPAIGN_PINNED_EVIDENCE_CHANGED_DURING_OPEN")
            self.assertEqual(response["state"]["status"], "halted")
            self.assertEqual(len(fixture.operator.disarms), 1)
            self.assertEqual(len(fixture.operator.reengages), 1)

    def test_v5_consumed_candidate_expiry_cannot_reopen_completed_canary(
            self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-campaign-v5-consumed-expiry-") as directory:
            document = v5_policy_document(max_cycles=1)
            fixture = CampaignFixture(Path(directory), document)
            controller, policy, _opened = self._open_and_close_v5(fixture)
            fixture.clock.value = 1_200_000
            fixture.operator.deadline_at_ms = 1_220_000
            later_intent = trade_intent(
                observed_at_ms=1_199_000, expires_at_ms=1_210_000,
                order_type="LMT", limit_price=1.10001)
            later = open_request(
                policy, request_id="request-open-after-candidate-expiry",
                cycle_id="cycle-after-candidate-expiry",
                intent=later_intent, now_ms=1_200_000)
            response = controller.process(later)
            self.assertEqual(response["status"], "rejected")
            self.assertEqual(response["reason_code"], "CAMPAIGN_NOT_IDLE")
            self.assertEqual(response["state"]["cycles_opened"], 1)
            self.assertEqual(fixture.admission_reads, 1)
            self.assertEqual(fixture.deployment_reads, 2)

    def test_v5_consumption_boottime_expiry_rejects_wall_clock_rollback(
            self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-campaign-v5-boottime-expiry-") as directory:
            document = v5_policy_document(max_cycles=1)
            fixture = CampaignFixture(
                Path(directory), document)
            controller, policy, _opened = self._open_and_close_v5(fixture)
            # A later wall-clock observation followed by rollback cannot
            # revive the consumed 24-hour authority window.
            fixture.clock.value = policy.expires_at_ms - 1
            fixture.clock.value = 1_010_000
            fixture.monotonic_clock.value = (
                5_000_000 + policy.expires_at_ms - 1_000_000)
            fixture.operator.deadline_at_ms = 1_030_000
            response = controller.process(open_request(
                policy, request_id="request-wall-rollback",
                cycle_id="cycle-wall-rollback",
                intent=trade_intent(
                    observed_at_ms=1_009_000, expires_at_ms=1_020_000,
                    order_type="LMT", limit_price=1.10001),
                now_ms=1_010_000))
            self.assertEqual(response["status"], "rejected")
            self.assertEqual(response["reason_code"], "CAMPAIGN_NOT_IDLE")
            self.assertEqual(response["state"]["status"], "halted")
            self.assertEqual(len(fixture.operator.disarms), 1)
            self.assertEqual(len(fixture.operator.reengages), 1)

    def test_v5_consumption_boottime_regression_halts_before_disarm(
            self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-campaign-v5-boottime-regression-") as directory:
            fixture = CampaignFixture(
                Path(directory), v5_policy_document(max_cycles=1))
            controller, policy, _opened = self._open_and_close_v5(fixture)
            fixture.clock.value = 1_010_000
            fixture.monotonic_clock.value = 4_999_999
            response = controller.process(open_request(
                policy, request_id="request-boottime-regression",
                cycle_id="cycle-boottime-regression",
                intent=trade_intent(
                    observed_at_ms=1_009_000, expires_at_ms=1_020_000,
                    order_type="LMT", limit_price=1.10001),
                now_ms=1_010_000))
            self.assertEqual(response["status"], "rejected")
            self.assertEqual(response["reason_code"], "CAMPAIGN_NOT_IDLE")
            self.assertEqual(response["state"]["status"], "halted")
            self.assertEqual(len(fixture.operator.disarms), 1)
            self.assertEqual(len(fixture.operator.reengages), 1)

    def test_v5_consumption_lacks_operator_window_before_disarm(
            self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-campaign-v5-pre-disarm-window-") as directory:
            fixture = CampaignFixture(
                Path(directory), v5_policy_document(),
                monotonic_clock=SequenceClock(5_000_000, 5_290_001))
            policy, _digest = fixture.provider()
            response = fixture.controller().process(open_request(policy))
            self.assertEqual(response["status"], "rejected")
            self.assertEqual(
                response["reason_code"],
                "CAMPAIGN_CONSUMPTION_TIME_INVALID")
            self.assertEqual(response["state"]["status"], "halted")
            self.assertFalse(fixture.operator.disarms)
            self.assertFalse(fixture.operator.reengages)

    def test_v5_consumption_expires_during_disarm_reengages_and_halts(
            self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-campaign-v5-disarm-expiry-") as directory:
            fixture = CampaignFixture(
                Path(directory), v5_policy_document(),
                monotonic_clock=SequenceClock(
                    5_000_000, 5_000_000, 5_300_000))
            policy, _digest = fixture.provider()
            response = fixture.controller().process(open_request(policy))
            self.assertEqual(response["status"], "rejected")
            self.assertEqual(
                response["reason_code"],
                "CAMPAIGN_CONSUMPTION_TIME_INVALID_DURING_OPEN")
            self.assertEqual(response["state"]["status"], "halted")
            self.assertEqual(len(fixture.operator.disarms), 1)
            self.assertEqual(len(fixture.operator.reengages), 1)

    def test_v5_unconsumed_expired_candidate_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-campaign-v5-unconsumed-expiry-") as directory:
            document = v5_policy_document(max_cycles=1)
            fixture = CampaignFixture(
                Path(directory), document, FakeClock(1_200_000))
            policy, _digest = fixture.provider()
            intent = trade_intent(
                observed_at_ms=1_199_000, expires_at_ms=1_210_000,
                order_type="LMT", limit_price=1.10001)
            response = fixture.controller().process(open_request(
                policy, request_id="request-unconsumed-expired",
                cycle_id="cycle-unconsumed-expired", intent=intent,
                now_ms=1_200_000))
            self.assertEqual(response["status"], "rejected")
            self.assertEqual(
                response["reason_code"], "CAMPAIGN_ADMISSION_TIME_INVALID")
            self.assertEqual(response["state"]["status"], "idle")
            self.assertFalse(fixture.operator.disarms)
            self.assertFalse((fixture.paths.receipt_root /
                CAMPAIGN._consumption_receipt_name(policy)).exists())

    def test_v5_consumption_same_byte_replacement_halts_before_disarm(
            self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-campaign-v5-consumption-replace-") as directory:
            fixture = CampaignFixture(
                Path(directory), v5_policy_document(max_cycles=1))
            controller, policy, _opened = self._open_and_close_v5(fixture)
            path = fixture.paths.receipt_root / (
                CAMPAIGN._consumption_receipt_name(policy))
            replacement = fixture.paths.receipt_root / ".consumption-replace"
            replacement.write_bytes(path.read_bytes())
            replacement.chmod(0o600)
            os.replace(replacement, path)
            fixture.clock.value = 1_020_000
            fixture.operator.deadline_at_ms = 1_040_000
            response = controller.process(open_request(
                policy, request_id="request-consumption-replaced",
                cycle_id="cycle-consumption-replaced",
                intent=trade_intent(
                    observed_at_ms=1_019_000, expires_at_ms=1_030_000,
                    order_type="LMT", limit_price=1.10001),
                now_ms=1_020_000))
            self.assertEqual(response["status"], "rejected")
            self.assertEqual(response["reason_code"], "CAMPAIGN_NOT_IDLE")
            self.assertEqual(response["state"]["status"], "halted")
            self.assertEqual(len(fixture.operator.disarms), 1)
            self.assertEqual(len(fixture.operator.reengages), 1)

    def test_v5_consumption_cross_boot_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-campaign-v5-consumption-boot-") as directory:
            fixture = CampaignFixture(
                Path(directory), v5_policy_document(max_cycles=1))
            controller, policy, _opened = self._open_and_close_v5(fixture)
            fixture.boot_id_path.chmod(0o600)
            fixture.boot_id_path.write_text(
                "22222222-2222-2222-2222-222222222222\n",
                encoding="ascii")
            fixture.boot_id_path.chmod(0o400)
            fixture.clock.value = 1_020_000
            fixture.operator.deadline_at_ms = 1_040_000
            response = controller.process(open_request(
                policy, request_id="request-consumption-new-boot",
                cycle_id="cycle-consumption-new-boot",
                intent=trade_intent(
                    observed_at_ms=1_019_000, expires_at_ms=1_030_000,
                    order_type="LMT", limit_price=1.10001),
                now_ms=1_020_000))
            self.assertEqual(response["status"], "rejected")
            self.assertEqual(response["reason_code"], "CAMPAIGN_NOT_IDLE")
            self.assertEqual(response["state"]["status"], "halted")
            self.assertEqual(len(fixture.operator.disarms), 1)

    def test_v5_consumption_drift_during_disarm_reengages_and_halts(
            self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-campaign-v5-consumption-open-drift-") \
                as directory:
            fixture = CampaignFixture(Path(directory), v5_policy_document())
            policy, _digest = fixture.provider()
            path = fixture.paths.receipt_root / (
                CAMPAIGN._consumption_receipt_name(policy))

            def replace_consumption() -> None:
                replacement = fixture.paths.receipt_root / ".replace-open"
                replacement.write_bytes(path.read_bytes())
                replacement.chmod(0o600)
                os.replace(replacement, path)

            fixture.operator.on_disarm = replace_consumption
            response = fixture.controller().process(open_request(policy))
            self.assertEqual(response["status"], "rejected")
            self.assertEqual(
                response["reason_code"],
                "CAMPAIGN_CONSUMPTION_CHANGED_DURING_OPEN")
            self.assertEqual(response["state"]["status"], "halted")
            self.assertEqual(len(fixture.operator.disarms), 1)
            self.assertEqual(len(fixture.operator.reengages), 1)

    def test_v5_deployment_failure_before_disarm_is_closed(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-campaign-v5-deployment-preflight-") as directory:
            fixture = CampaignFixture(Path(directory), v5_policy_document())
            controller = CAMPAIGN.CampaignController(
                fixture.provider, fixture.operator, fixture.paths,
                fixture.clock, root_uid=fixture.uid, root_gid=fixture.gid,
                admission_provider=fixture.read_admission,
                deployment_provider=mock.Mock(side_effect=
                    CAMPAIGN.CampaignError(
                        "CAMPAIGN_DEPLOYMENT_EVIDENCE_INVALID")))
            policy, _digest = fixture.provider()
            response = controller.process(open_request(policy))
            self.assertEqual(response["status"], "rejected")
            self.assertEqual(
                response["reason_code"],
                "CAMPAIGN_DEPLOYMENT_EVIDENCE_INVALID")
            self.assertEqual(fixture.admission_reads, 1)
            self.assertFalse(fixture.operator.disarms)
            self.assertFalse(fixture.operator.reengages)

    def test_v5_deployment_drift_after_disarm_reengages_and_halts(
            self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-campaign-v5-deployment-drift-") as directory:
            fixture = CampaignFixture(Path(directory), v5_policy_document())
            fixture.operator.on_disarm = lambda: setattr(
                fixture, "deployment_generation",
                fixture.deployment_generation + 1)
            policy, _digest = fixture.provider()
            response = fixture.controller().process(open_request(policy))
            self.assertEqual(response["status"], "rejected")
            self.assertEqual(
                response["reason_code"],
                "CAMPAIGN_DEPLOYMENT_CHANGED_DURING_OPEN")
            self.assertEqual(response["state"]["status"], "halted")
            self.assertEqual(response["state"]["cycles_opened"], 0)
            self.assertEqual(fixture.admission_reads, 1)
            self.assertEqual(fixture.deployment_reads, 2)
            self.assertEqual(len(fixture.operator.disarms), 1)
            self.assertEqual(len(fixture.operator.reengages), 1)

    def test_v4_active_local_policy_is_quarantined_before_any_provider(
            self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-campaign-local-policy-") as directory:
            fixture = CampaignFixture(
                Path(directory), local_policy_document(enabled=True))
            request_policy = CAMPAIGN.parse_policy(
                canonical(local_policy_document()), "alpha")
            with self.assertRaisesRegex(
                    CAMPAIGN.CampaignError,
                    "CAMPAIGN_POLICY_V4_ACTIVE_P1_ADMISSION_REQUIRED"):
                fixture.controller().process(open_request(request_policy))
            self.assertFalse(fixture.operator.disarms)
            self.assertEqual(fixture.admission_reads, 0)
            self.assertEqual(fixture.deployment_reads, 0)
            self.assertFalse(fixture.paths.runtime_root.exists())
            self.assertFalse(fixture.paths.receipt_root.exists())

    def test_v4_direct_policy_object_is_quarantined_before_disarm(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-campaign-local-policy-defense-") as directory:
            fixture = CampaignFixture(
                Path(directory), local_policy_document())
            disabled, policy_sha256 = fixture.provider()
            active = replace(
                disabled, enabled=True, mutations_authorized=True)
            controller = CAMPAIGN.CampaignController(
                lambda: (active, policy_sha256), fixture.operator,
                fixture.paths, fixture.clock, root_uid=fixture.uid,
                root_gid=fixture.gid,
                admission_provider=fixture.read_admission,
                deployment_provider=fixture.read_deployment)
            with self.assertRaisesRegex(
                    CAMPAIGN.CampaignError,
                    "CAMPAIGN_POLICY_V4_ACTIVE_P1_ADMISSION_REQUIRED"):
                controller.process(open_request(disabled))
            self.assertFalse(fixture.paths.runtime_root.exists())
            self.assertFalse(fixture.paths.receipt_root.exists())
            state = CAMPAIGN._new_state(
                active, policy_sha256, fixture.clock())
            with self.assertRaisesRegex(
                    CAMPAIGN.CampaignError,
                    "CAMPAIGN_POLICY_V4_ACTIVE_P1_ADMISSION_REQUIRED"):
                fixture.controller()._ensure_open_allowed(
                    state, active, fixture.clock())
            self.assertFalse(fixture.operator.disarms)
            self.assertEqual(fixture.admission_reads, 0)
            self.assertEqual(fixture.deployment_reads, 0)

    def test_v4_direct_policy_cannot_replay_preexisting_ok_receipt(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-campaign-local-policy-replay-") as directory:
            fixture = CampaignFixture(
                Path(directory), local_policy_document())
            disabled, policy_sha256 = fixture.provider()
            active = replace(
                disabled, enabled=True, mutations_authorized=True)
            controller = CAMPAIGN.CampaignController(
                lambda: (active, policy_sha256), fixture.operator,
                fixture.paths, fixture.clock, root_uid=fixture.uid,
                root_gid=fixture.gid,
                admission_provider=fixture.read_admission,
                deployment_provider=fixture.read_deployment)
            request = open_request(disabled)
            fixture.paths.runtime_root.mkdir(mode=0o700)
            fixture.paths.receipt_root.mkdir(mode=0o700)
            receipt_path = fixture.paths.receipt_root / (
                controller._request_receipt_name(request["request_id"]))
            receipt = canonical({
                "schema": CAMPAIGN.RECEIPT_SCHEMA,
                "version": 1,
                "request_id": request["request_id"],
                "request_sha256": CAMPAIGN._sha256(
                    CAMPAIGN._canonical_json(request)),
                "recorded_at_ms": fixture.clock(),
                "response": {"status": "ok"},
            })
            receipt_path.write_bytes(receipt)
            receipt_path.chmod(0o600)
            with self.assertRaisesRegex(
                    CAMPAIGN.CampaignError,
                    "CAMPAIGN_POLICY_V4_ACTIVE_P1_ADMISSION_REQUIRED"):
                controller.process(request)
            self.assertEqual(receipt_path.read_bytes(), receipt)
            self.assertEqual(list(fixture.paths.runtime_root.iterdir()), [])
            self.assertFalse(fixture.operator.disarms)
            self.assertEqual(fixture.admission_reads, 0)
            self.assertEqual(fixture.deployment_reads, 0)

    def test_v4_disabled_parser_policy_cannot_replay_old_open_receipt(
            self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-campaign-local-policy-disabled-replay-"
        ) as directory:
            fixture = CampaignFixture(
                Path(directory), local_policy_document())
            policy, _policy_sha256 = fixture.provider()
            controller = fixture.controller()
            request = open_request(policy)
            fixture.paths.runtime_root.mkdir(mode=0o700)
            fixture.paths.receipt_root.mkdir(mode=0o700)
            receipt_path = fixture.paths.receipt_root / (
                controller._request_receipt_name(request["request_id"]))
            receipt = canonical({
                "schema": CAMPAIGN.RECEIPT_SCHEMA,
                "version": 1,
                "request_id": request["request_id"],
                "request_sha256": CAMPAIGN._sha256(
                    CAMPAIGN._canonical_json(request)),
                "recorded_at_ms": fixture.clock(),
                "response": {
                    "status": "ok", "state": {"status": "open"},
                },
            })
            receipt_path.write_bytes(receipt)
            receipt_path.chmod(0o600)
            with self.assertRaisesRegex(
                    CAMPAIGN.CampaignError,
                    "CAMPAIGN_POLICY_V4_ACTIVE_P1_ADMISSION_REQUIRED"):
                controller.process(request)
            self.assertEqual(receipt_path.read_bytes(), receipt)
            self.assertEqual(list(fixture.paths.runtime_root.iterdir()), [])
            self.assertFalse(fixture.operator.disarms)
            self.assertEqual(fixture.admission_reads, 0)
            self.assertEqual(fixture.deployment_reads, 0)

    def test_v4_local_policy_and_intent_are_mkt_day_only(self) -> None:
        document = local_policy_document()
        policy = CAMPAIGN.parse_policy(canonical(document), "alpha")
        self.assertEqual(policy.order_type, "MKT")
        intent = trade_intent(order_type="MKT")
        validated, _digest = CAMPAIGN.validate_trade_intent(
            intent, policy, 1_000_000)
        self.assertEqual(validated["reference_price"], 1.10001)
        self.assertNotIn("limit_price", validated)

        document["order_type"] = "LMT"
        with self.assertRaisesRegex(
                CAMPAIGN.CampaignError,
                "CAMPAIGN_POLICY_SAFETY_BOUNDARY_INVALID"):
            CAMPAIGN.parse_policy(canonical(document), "alpha")

        with self.assertRaisesRegex(
            CAMPAIGN.CampaignError,
                "CAMPAIGN_INTENT_CONTRACT_INVALID"):
            CAMPAIGN.validate_trade_intent(
                trade_intent(order_type="LMT"), policy, 1_000_000)

    def test_v4_local_policy_accepts_disabled_holding_timeout(self) -> None:
        document = local_policy_document()
        document["max_holding_ms"] = 0
        policy = CAMPAIGN.parse_policy(canonical(document), "alpha")
        intent = trade_intent(order_type="MKT")
        intent["max_holding_ms"] = 0
        validated, _digest = CAMPAIGN.validate_trade_intent(
            intent, policy, 1_000_000)
        self.assertEqual(policy.max_holding_ms, 0)
        self.assertEqual(validated["max_holding_ms"], 0)

        positive_intent = dict(intent)
        positive_intent["max_holding_ms"] = 1_000
        with self.assertRaisesRegex(
                CAMPAIGN.CampaignError,
                "CAMPAIGN_INTENT_HOLDING_INVALID"):
            CAMPAIGN.validate_trade_intent(
                positive_intent, policy, 1_000_000)

    def test_v4_local_policy_rejects_subsecond_holding_timeout(self) -> None:
        document = local_policy_document()
        document["max_holding_ms"] = 999
        with self.assertRaisesRegex(
                CAMPAIGN.CampaignError,
                "CAMPAIGN_POLICY_HOLDING_INVALID"):
            CAMPAIGN.parse_policy(canonical(document), "alpha")

    def test_positive_holding_policy_rejects_disabled_intent_timeout(self) -> None:
        policy = CAMPAIGN.parse_policy(
            canonical(local_policy_document()), "alpha")
        intent = trade_intent(order_type="MKT")
        intent["max_holding_ms"] = 0
        with self.assertRaisesRegex(
                CAMPAIGN.CampaignError,
                "CAMPAIGN_INTENT_HOLDING_INVALID"):
            CAMPAIGN.validate_trade_intent(intent, policy, 1_000_000)

    def test_legacy_policy_rejects_disabled_intent_timeout(self) -> None:
        policy = CAMPAIGN.parse_policy(
            canonical(policy_document()), "alpha")
        intent = trade_intent()
        intent["max_holding_ms"] = 0
        with self.assertRaisesRegex(
                CAMPAIGN.CampaignError,
                "CAMPAIGN_INTENT_HOLDING_INVALID"):
            CAMPAIGN.validate_trade_intent(intent, policy, 1_000_000)

    def test_v4_local_policy_accepts_idealpro_minimum_quantity(self) -> None:
        document = local_policy_document()
        document["max_quantity"] = 25_000
        policy = CAMPAIGN.parse_policy(canonical(document), "alpha")
        self.assertEqual(policy.max_quantity, 25_000)

        document["max_quantity"] = 25_001
        with self.assertRaisesRegex(
                CAMPAIGN.CampaignError,
                "CAMPAIGN_POLICY_QUANTITY_INVALID"):
            CAMPAIGN.parse_policy(canonical(document), "alpha")

    def test_v4_rejects_nonlocal_admission_mode(self) -> None:
        document = local_policy_document()
        document["admission_mode"] = "external-certified"
        with self.assertRaisesRegex(
                CAMPAIGN.CampaignError,
                "CAMPAIGN_POLICY_ADMISSION_MODE_INVALID"):
            CAMPAIGN.parse_policy(canonical(document), "alpha")

    def test_v4_requires_nonzero_deployment_pins_even_when_disabled(
            self) -> None:
        for field, value in (
                ("source_baseline_sha256", "sha256:" + "0" * 64),
                ("deployment_evidence_file_sha256",
                 "sha256:" + "0" * 64),
                ("deployment_evidence_body_sha256",
                 "sha256:" + "0" * 64),
                ("deployment_install_transaction_id", "short")):
            document = local_policy_document(enabled=False)
            document[field] = value
            with self.subTest(field=field), self.assertRaises(
                    CAMPAIGN.CampaignError):
                CAMPAIGN.parse_policy(canonical(document), "alpha")

    def test_v4_quarantine_prevents_deployment_drift_disarm(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-campaign-deployment-drift-") as directory:
            fixture = CampaignFixture(
                Path(directory), local_policy_document(enabled=True))
            fixture.operator.on_disarm = lambda: setattr(
                fixture, "deployment_generation",
                fixture.deployment_generation + 1)
            request_policy = CAMPAIGN.parse_policy(
                canonical(local_policy_document()), "alpha")
            with self.assertRaisesRegex(
                    CAMPAIGN.CampaignError,
                    "CAMPAIGN_POLICY_V4_ACTIVE_P1_ADMISSION_REQUIRED"):
                fixture.controller().process(open_request(request_policy))
            self.assertFalse(fixture.operator.disarms)
            self.assertFalse(fixture.operator.reengages)
            self.assertEqual(fixture.deployment_reads, 0)

    def test_v4_quarantine_precedes_deployment_provider(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-campaign-deployment-missing-") as directory:
            fixture = CampaignFixture(
                Path(directory), local_policy_document(enabled=True))
            controller = CAMPAIGN.CampaignController(
                fixture.provider, fixture.operator, fixture.paths,
                fixture.clock, root_uid=fixture.uid, root_gid=fixture.gid,
                admission_provider=fixture.read_admission,
                deployment_provider=mock.Mock(side_effect=
                    CAMPAIGN.CampaignError(
                        "CAMPAIGN_DEPLOYMENT_EVIDENCE_INVALID")))
            request_policy = CAMPAIGN.parse_policy(
                canonical(local_policy_document()), "alpha")
            with self.assertRaisesRegex(
                    CAMPAIGN.CampaignError,
                    "CAMPAIGN_POLICY_V4_ACTIVE_P1_ADMISSION_REQUIRED"):
                controller.process(open_request(request_policy))
            self.assertFalse(fixture.operator.disarms)
            self.assertFalse(fixture.operator.reengages)
            self.assertEqual(fixture.deployment_reads, 0)

    def test_v4_operator_rehashes_certified_installed_closure(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-campaign-deployment-proof-") as directory:
            root = Path(directory)
            installed = root / "hepta-runtime"
            installed.write_bytes(b"runtime-a\n")
            installed.chmod(0o755)
            closure_files = ({
                "path": str(installed), "sha256": digest(b"runtime-a\n"),
                "mode": 0o755,
            },)
            closure_body = {
                "schema": CAMPAIGN.CERTIFIED_INSTALL_CLOSURE_SCHEMA,
                "version": 1,
                "source_freeze_commit": "1" * 40,
                "source_freeze_tree": "2" * 40,
                "source_manifest_sha256": "sha256:" + "3" * 64,
                "source_baseline_sha256": "sha256:" + "4" * 64,
                "install_transaction_id": "install-transaction-round98",
                "installed_at_ms": 1,
                "files": list(closure_files),
            }
            closure = {
                **closure_body, "body_sha256": digest(canonical(closure_body)),
            }
            closure_raw = canonical(closure)
            closure_path = root / "certified.json"
            closure_path.write_bytes(closure_raw)
            closure_path.chmod(0o600)
            evidence_body = {
                "schema": CAMPAIGN.LOCAL_PAPER_DEPLOYMENT_EVIDENCE_SCHEMA,
                "version": 1,
                "source_freeze_commit": closure["source_freeze_commit"],
                "source_freeze_tree": closure["source_freeze_tree"],
                "source_manifest_sha256":
                    closure["source_manifest_sha256"],
                "source_baseline_sha256":
                    closure["source_baseline_sha256"],
                "install_transaction_id":
                    closure["install_transaction_id"],
                "installed_at_ms": closure["installed_at_ms"],
                "generated_at_ms": 2,
                "files": list(closure_files),
                "certified_install_closure_file_sha256": digest(closure_raw),
                "certified_install_closure_body_sha256":
                    closure["body_sha256"],
                "paper_authorized": False,
                "live_authorized": False,
                "mutation_authorized": False,
            }
            evidence = {
                **evidence_body,
                "body_sha256": digest(canonical(evidence_body)),
            }
            evidence_raw = canonical(evidence)
            evidence_path = root / "deployment.json"
            evidence_path.write_bytes(evidence_raw)
            evidence_path.chmod(0o600)
            document = local_policy_document()
            document["source_baseline_sha256"] = (
                closure["source_baseline_sha256"])
            document["deployment_evidence_file_sha256"] = digest(evidence_raw)
            document["deployment_evidence_body_sha256"] = (
                evidence["body_sha256"])
            document["deployment_install_transaction_id"] = (
                closure["install_transaction_id"])
            policy = CAMPAIGN.parse_policy(canonical(document), "alpha")

            def rooted(metadata: object) -> SimpleNamespace:
                return SimpleNamespace(
                    st_dev=metadata.st_dev, st_ino=metadata.st_ino,
                    st_mode=metadata.st_mode, st_nlink=metadata.st_nlink,
                    st_uid=0, st_gid=0, st_size=metadata.st_size,
                    st_mtime_ns=metadata.st_mtime_ns,
                    st_ctime_ns=metadata.st_ctime_ns)

            with mock.patch.multiple(
                    CAMPAIGN,
                    LOCAL_PAPER_DEPLOYMENT_EVIDENCE_PATH=evidence_path,
                    CERTIFIED_INSTALL_CLOSURE_PATH=closure_path,
                    LOCAL_PAPER_DEPLOYMENT_FILES=((installed, 0o755),)), \
                    mock.patch.object(
                        CAMPAIGN.os, "fstat",
                        side_effect=lambda descriptor:
                            rooted(REAL_FSTAT(descriptor))), \
                    mock.patch.object(
                        CAMPAIGN.os, "lstat",
                        side_effect=lambda path: rooted(REAL_LSTAT(path))):
                snapshot = CAMPAIGN.load_local_paper_deployment(policy)
                self.assertEqual(snapshot.file_sha256, digest(evidence_raw))

                wrong_pin_document = dict(document)
                wrong_pin_document["deployment_evidence_body_sha256"] = (
                    "sha256:" + "f" * 64)
                wrong_pin_policy = CAMPAIGN.parse_policy(
                    canonical(wrong_pin_document), "alpha")
                with self.assertRaisesRegex(
                        CAMPAIGN.CampaignError,
                        "CAMPAIGN_POLICY_DEPLOYMENT_MISMATCH"):
                    CAMPAIGN.load_local_paper_deployment(wrong_pin_policy)

                stale_files = [{
                    "path": str(installed),
                    "sha256": digest(b"runtime-old\n"), "mode": 0o755,
                }]
                stale_body = dict(evidence_body)
                stale_body["files"] = stale_files
                stale_evidence = {
                    **stale_body,
                    "body_sha256": digest(canonical(stale_body)),
                }
                stale_raw = canonical(stale_evidence)
                evidence_path.write_bytes(stale_raw)
                evidence_path.chmod(0o600)
                installed.write_bytes(b"runtime-old\n")
                installed.chmod(0o755)
                stale_policy_document = dict(document)
                stale_policy_document["deployment_evidence_file_sha256"] = (
                    digest(stale_raw))
                stale_policy_document["deployment_evidence_body_sha256"] = (
                    stale_evidence["body_sha256"])
                stale_policy = CAMPAIGN.parse_policy(
                    canonical(stale_policy_document), "alpha")
                with self.assertRaisesRegex(
                        CAMPAIGN.CampaignError,
                        "CAMPAIGN_DEPLOYMENT_CERTIFICATION_MISMATCH"):
                    CAMPAIGN.load_local_paper_deployment(stale_policy)

                evidence_path.write_bytes(evidence_raw)
                evidence_path.chmod(0o600)
                installed.write_bytes(b"runtime-a\n")
                installed.chmod(0o755)
                original_snapshot_file = (
                    CAMPAIGN._snapshot_local_deployed_file)
                swapped = [False]

                def swap_certified_path(
                        path: Path, expected_sha256: str,
                        expected_mode: int,
                ) -> tuple[int, ...]:
                    result = original_snapshot_file(
                        path, expected_sha256, expected_mode)
                    if not swapped[0]:
                        replacement = root / "certified-replacement.json"
                        replacement.write_bytes(closure_raw)
                        replacement.chmod(0o600)
                        os.replace(replacement, closure_path)
                        swapped[0] = True
                    return result

                with mock.patch.object(
                        CAMPAIGN, "_snapshot_local_deployed_file",
                        side_effect=swap_certified_path), \
                        self.assertRaisesRegex(
                            CAMPAIGN.CampaignError,
                            "CAMPAIGN_CERTIFIED_INSTALL_CLOSURE_CHANGED"):
                    CAMPAIGN.load_local_paper_deployment(policy)

                installed.write_bytes(b"runtime-b\n")
                installed.chmod(0o755)
                with self.assertRaisesRegex(
                        CAMPAIGN.CampaignError,
                        "CAMPAIGN_DEPLOYMENT_FILE_INVALID"):
                    CAMPAIGN.load_local_paper_deployment(policy)

    def test_v2_policy_and_admission_location_are_exact(self) -> None:
        noncanonical = json.dumps(
            policy_document(), sort_keys=True, indent=2).encode("ascii") + b"\n"
        with self.assertRaisesRegex(
                CAMPAIGN.CampaignError,
                "CAMPAIGN_POLICY_NON_CANONICAL"):
            CAMPAIGN.parse_policy(noncanonical, "alpha")
        unsafe_name = policy_document()
        unsafe_name["admission_receipt_name"] = "../candidate-a.json"
        with self.assertRaisesRegex(
                CAMPAIGN.CampaignError,
                "CAMPAIGN_POLICY_ADMISSION_NAME_INVALID"):
            CAMPAIGN.parse_policy(canonical(unsafe_name), "alpha")

    def test_policy_rejects_live_and_unbounded_authority(self) -> None:
        cases = []
        live = policy_document()
        live["live_authorized"] = True
        cases.append(live)
        market = policy_document()
        market["order_type"] = "MKT"
        cases.append(market)
        broad = policy_document()
        broad["allowed_instruments"] = ["EUR.USD", "USD.JPY"]
        cases.append(broad)
        cycles = policy_document()
        cycles["max_cycles"] = CAMPAIGN.MAX_CAMPAIGN_CYCLES + 1
        cases.append(cycles)
        dormant = policy_document(enabled=False)
        dormant["mutations_authorized"] = True
        cases.append(dormant)
        zero_strategy = policy_document()
        zero_strategy["strategy_sha256"] = "sha256:" + "0" * 64
        cases.append(zero_strategy)
        zero_source = policy_document()
        zero_source["source_baseline_sha256"] = "sha256:" + "0" * 64
        cases.append(zero_source)
        boolean_active_order = policy_document()
        boolean_active_order["max_active_orders"] = True
        cases.append(boolean_active_order)
        for document in cases:
            with self.subTest(document=document), self.assertRaises(
                    CAMPAIGN.CampaignError):
                CAMPAIGN.parse_policy(canonical(document), "alpha")

    def test_missing_stale_or_window_mismatched_admission_never_disarms(
            self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-campaign-admission-missing-") as directory:
            fixture = CampaignFixture(Path(directory), policy_document())
            fixture.admission_path.unlink()
            policy, _digest = fixture.provider()
            response = fixture.controller().process(open_request(policy))
            self.assertEqual(
                response["reason_code"], "CAMPAIGN_ADMISSION_MISSING")
            self.assertFalse(fixture.operator.disarms)

        cases = (
            ("stale", 1_000_000, "CAMPAIGN_ADMISSION_TIME_INVALID"),
            ("window", 1_099_999, "CAMPAIGN_ADMISSION_WINDOW_INVALID"),
        )
        for label, expires_at_ms, reason in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory(
                    prefix=f"hepta-campaign-admission-{label}-") as directory:
                admission = admission_document(expires_at_ms=expires_at_ms)
                policy_document_value = policy_document()
                pin_admission(policy_document_value, admission)
                fixture = CampaignFixture(
                    Path(directory), policy_document_value,
                    admission=admission)
                policy, _digest = fixture.provider()
                response = fixture.controller().process(open_request(policy))
                self.assertEqual(response["reason_code"], reason)
                self.assertFalse(fixture.operator.disarms)

    def test_admission_time_boundary_accepts_evaluation_and_rejects_expiry(
            self) -> None:
        admission = admission_document(evaluated_at_ms=1_000_000)
        policy_document_value = policy_document()
        policy_document_value["valid_after_ms"] = 1_000_000
        pin_admission(policy_document_value, admission)
        with tempfile.TemporaryDirectory(
                prefix="hepta-campaign-admission-evaluation-time-") as directory:
            fixture = CampaignFixture(
                Path(directory), policy_document_value,
                admission=admission)
            policy, _digest = fixture.provider()
            response = fixture.controller().process(open_request(policy))
            self.assertEqual(response["status"], "ok")
            self.assertEqual(response["state"]["status"], "open")

        admission = admission_document(expires_at_ms=1_000_000)
        policy_document_value = policy_document()
        pin_admission(policy_document_value, admission)
        with tempfile.TemporaryDirectory(
                prefix="hepta-campaign-admission-expiry-time-") as directory:
            fixture = CampaignFixture(
                Path(directory), policy_document_value,
                admission=admission)
            policy, _digest = fixture.provider()
            response = fixture.controller().process(open_request(policy))
            self.assertEqual(
                response["reason_code"],
                "CAMPAIGN_ADMISSION_TIME_INVALID")
            self.assertFalse(fixture.operator.disarms)

    def test_unpinned_tampering_never_disarms(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-campaign-admission-tamper-") as directory:
            fixture = CampaignFixture(Path(directory), policy_document())
            fixture.admission["status"] = "NO_GO"
            fixture.admission = reseal_admission(fixture.admission)
            fixture.write_admission()
            policy, _digest = fixture.provider()
            response = fixture.controller().process(open_request(policy))
            self.assertEqual(
                response["reason_code"],
                "CAMPAIGN_ADMISSION_FILE_PIN_MISMATCH")
            self.assertFalse(fixture.operator.disarms)

    def test_wrong_admission_lineage_never_disarms(self) -> None:
        cases = (
            ("domain", "domain", "beta"),
            ("campaign", "campaign_id", "campaign-b"),
            ("source", "source_baseline_sha256", "sha256:" + "b" * 64),
            ("strategy", "strategy_sha256", "sha256:" + "b" * 64),
        )
        for label, field, value in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory(
                    prefix=f"hepta-campaign-lineage-{label}-") as directory:
                admission = admission_document()
                admission[field] = value
                admission = reseal_admission(admission)
                policy_document_value = policy_document()
                pin_admission(policy_document_value, admission)
                fixture = CampaignFixture(
                    Path(directory), policy_document_value,
                    admission=admission)
                policy, _digest = fixture.provider()
                response = fixture.controller().process(open_request(policy))
                self.assertEqual(
                    response["reason_code"],
                    "CAMPAIGN_ADMISSION_SEMANTIC_INVALID")
                self.assertFalse(fixture.operator.disarms)

    def test_pinned_candidate_must_still_be_exact_nonauthorizing_go(
            self) -> None:
        mutations = {
            "schema": lambda value: value.__setitem__("schema", "wrong.v1"),
            "version_type": lambda value: value.__setitem__("version", True),
            "round": lambda value: value.__setitem__("round", 94),
            "status": lambda value: value.__setitem__("status", "NO_GO"),
            "candidate": lambda value: value.__setitem__(
                "paper_test_admission_candidate", False),
            "findings": lambda value: value.__setitem__(
                "findings", ["NOT_READY"]),
            "authorization_effect": lambda value: value.__setitem__(
                "authorization_effect", "PAPER_AUTHORIZED"),
            "unexpected_field": lambda value: value.__setitem__(
                "unexpected", False),
            "duplicate_binding_path": lambda value: value["input_bindings"][
                "install_manifest"].__setitem__(
                    "path", value["input_bindings"]["source_baseline"]["path"]),
            "binding_version_type": lambda value: value["input_bindings"][
                "source_baseline"].__setitem__("version", True),
            "zero_binding_digest": lambda value: value["input_bindings"][
                "source_baseline"].__setitem__(
                    "file_sha256", "sha256:" + "0" * 64),
        }
        for authority_field in (
                "paper_authorized", "live_authorized",
                "mutation_authorized", "direct_broker_access",
                "order_submission_authorized"):
            mutations[authority_field] = (
                lambda value, field=authority_field:
                value.__setitem__(field, True))
        for label, mutate in mutations.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory(
                    prefix=f"hepta-campaign-exact-{label}-") as directory:
                admission = admission_document()
                mutate(admission)
                admission = reseal_admission(admission)
                policy_document_value = policy_document()
                pin_admission(policy_document_value, admission)
                fixture = CampaignFixture(
                    Path(directory), policy_document_value,
                    admission=admission)
                policy, _digest = fixture.provider()
                response = fixture.controller().process(open_request(policy))
                self.assertIn(response["reason_code"], {
                    "CAMPAIGN_ADMISSION_CONTRACT_INVALID",
                    "CAMPAIGN_ADMISSION_BINDINGS_INVALID",
                    "CAMPAIGN_ADMISSION_SEMANTIC_INVALID",
                })
                self.assertFalse(fixture.operator.disarms)

    def test_candidate_requires_canonical_file_and_valid_body_pin(self) -> None:
        admission = admission_document()
        pretty = (
            json.dumps(admission, sort_keys=True, indent=2) + "\n"
        ).encode("ascii")
        policy_document_value = policy_document()
        pin_admission(policy_document_value, admission, pretty)
        with tempfile.TemporaryDirectory(
                prefix="hepta-campaign-admission-pretty-") as directory:
            fixture = CampaignFixture(
                Path(directory), policy_document_value, admission=admission)
            fixture.write_admission(pretty)
            policy, _digest = fixture.provider()
            response = fixture.controller().process(open_request(policy))
            self.assertEqual(
                response["reason_code"],
                "CAMPAIGN_ADMISSION_NON_CANONICAL")
            self.assertFalse(fixture.operator.disarms)

        tampered_body = admission_document()
        tampered_body["status"] = "NO_GO"
        policy_document_value = policy_document()
        pin_admission(policy_document_value, tampered_body)
        with tempfile.TemporaryDirectory(
                prefix="hepta-campaign-admission-body-") as directory:
            fixture = CampaignFixture(
                Path(directory), policy_document_value,
                admission=tampered_body)
            policy, _digest = fixture.provider()
            response = fixture.controller().process(open_request(policy))
            self.assertEqual(
                response["reason_code"],
                "CAMPAIGN_ADMISSION_BODY_DIGEST_INVALID")
            self.assertFalse(fixture.operator.disarms)

        admission = admission_document()
        policy_document_value = policy_document()
        policy_document_value["admission_receipt_body_sha256"] = (
            "sha256:" + "b" * 64)
        with tempfile.TemporaryDirectory(
                prefix="hepta-campaign-admission-body-pin-") as directory:
            fixture = CampaignFixture(
                Path(directory), policy_document_value, admission=admission)
            policy, _digest = fixture.provider()
            response = fixture.controller().process(open_request(policy))
            self.assertEqual(
                response["reason_code"],
                "CAMPAIGN_ADMISSION_BODY_PIN_MISMATCH")
            self.assertFalse(fixture.operator.disarms)

    def test_admission_symlinks_and_unsafe_metadata_never_disarm(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-campaign-admission-file-link-") as directory:
            fixture = CampaignFixture(Path(directory), policy_document())
            target = fixture.admission_root / "target.json"
            target.write_bytes(fixture.admission_path.read_bytes())
            target.chmod(0o600)
            fixture.admission_path.unlink()
            fixture.admission_path.symlink_to(target.name)
            policy, _digest = fixture.provider()
            response = fixture.controller().process(open_request(policy))
            self.assertEqual(
                response["reason_code"],
                "CAMPAIGN_ADMISSION_METADATA_UNSAFE")
            self.assertFalse(fixture.operator.disarms)

        with tempfile.TemporaryDirectory(
                prefix="hepta-campaign-admission-root-link-") as directory:
            fixture = CampaignFixture(Path(directory), policy_document())
            alias = fixture.root / "admission-alias"
            alias.symlink_to(fixture.admission_root, target_is_directory=True)
            controller = CAMPAIGN.CampaignController(
                fixture.provider, fixture.operator, fixture.paths,
                fixture.clock, root_uid=fixture.uid, root_gid=fixture.gid,
                admission_provider=lambda policy, now_ms:
                    CAMPAIGN.load_admission_receipt(
                        alias, policy, now_ms, expected_uid=fixture.uid,
                        expected_gid=fixture.gid))
            policy, _digest = fixture.provider()
            response = controller.process(open_request(policy))
            self.assertEqual(
                response["reason_code"],
                "CAMPAIGN_ADMISSION_ROOT_UNSAFE")
            self.assertFalse(fixture.operator.disarms)

        for label, mutate in (
                ("mode", lambda fixture: fixture.admission_path.chmod(0o644)),
                ("hardlink", lambda fixture: os.link(
                    fixture.admission_path,
                    fixture.admission_root / "candidate-copy.json")),
                ("fifo", lambda fixture: (
                    fixture.admission_path.unlink(),
                    os.mkfifo(fixture.admission_path, mode=0o600))),
                ("root_mode", lambda fixture:
                 fixture.admission_root.chmod(0o770))):
            with self.subTest(label=label), tempfile.TemporaryDirectory(
                    prefix=f"hepta-campaign-admission-{label}-") as directory:
                fixture = CampaignFixture(Path(directory), policy_document())
                mutate(fixture)
                policy, _digest = fixture.provider()
                response = fixture.controller().process(open_request(policy))
                self.assertIn(response["reason_code"], {
                    "CAMPAIGN_ADMISSION_METADATA_UNSAFE",
                    "CAMPAIGN_ADMISSION_ROOT_UNSAFE",
                })
                self.assertFalse(fixture.operator.disarms)

    def test_finalization_lock_and_owner_precede_candidate_read(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-campaign-finalization-owner-") as directory:
            fixture = CampaignFixture(Path(directory), policy_document())
            fixture.admission_path.unlink()
            owner = fixture.host_authority_root / "owner.v1"
            owner.write_bytes(b"{}\n")
            owner.chmod(0o600)
            policy, _digest = fixture.provider()
            response = fixture.controller().process(open_request(policy))
            self.assertEqual(
                response["reason_code"],
                "CAMPAIGN_ADMISSION_FINALIZATION_INCOMPLETE")
            self.assertFalse(fixture.operator.disarms)

        with tempfile.TemporaryDirectory(
                prefix="hepta-campaign-finalization-busy-") as directory:
            fixture = CampaignFixture(Path(directory), policy_document())
            fixture.admission_path.unlink()
            descriptor = os.open(
                fixture.lease_path, os.O_RDONLY | os.O_CLOEXEC)
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                policy, _digest = fixture.provider()
                response = fixture.controller().process(open_request(policy))
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)
            self.assertEqual(
                response["reason_code"],
                "CAMPAIGN_ADMISSION_FINALIZATION_BUSY")
            self.assertFalse(fixture.operator.disarms)

    def test_production_procfs_boot_id_path_is_supported(self) -> None:
        if not CAMPAIGN.BOOT_ID_PATH.is_file():
            self.skipTest("Linux boot_id is unavailable")
        metadata = CAMPAIGN.BOOT_ID_PATH.stat()
        self.assertEqual(metadata.st_uid, 0)
        self.assertEqual(metadata.st_gid, 0)
        boot_id = CAMPAIGN._read_boot_id(
            CAMPAIGN.BOOT_ID_PATH, expected_uid=0, expected_gid=0)
        self.assertIsNotNone(CAMPAIGN.BOOT_ID.fullmatch(boot_id))

    def test_candidate_commit_without_completed_terminal_never_disarms(
            self) -> None:
        for label, remove_tombstone in (
                ("candidate_only", True),
                ("tombstone_without_pointer", False)):
            with self.subTest(label=label), tempfile.TemporaryDirectory(
                    prefix=f"hepta-campaign-unfinalized-{label}-") as directory:
                fixture = CampaignFixture(Path(directory), policy_document())
                fixture.pointer_path.unlink()
                if remove_tombstone:
                    fixture.tombstone_path.unlink()
                self.assertTrue(fixture.admission_path.is_file())
                self.assertTrue(fixture.zero_path.is_file())
                policy, _digest = fixture.provider()
                response = fixture.controller().process(open_request(policy))
                self.assertEqual(
                    response["reason_code"],
                    "CAMPAIGN_ADMISSION_FINALIZATION_INVALID")
                self.assertFalse(fixture.operator.disarms)

    def test_pointer_and_tombstone_semantic_tamper_never_disarm(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-campaign-pointer-tamper-") as directory:
            fixture = CampaignFixture(Path(directory), policy_document())
            fixture.pointer["status"] = "STALE"
            fixture.pointer = ZERO_PRODUCER.seal(fixture.pointer)
            fixture.write_pointer()
            fixture.pin_terminal_policy()
            policy, _digest = fixture.provider()
            response = fixture.controller().process(open_request(policy))
            self.assertEqual(
                response["reason_code"],
                "CAMPAIGN_ADMISSION_FINALIZATION_INVALID")
            self.assertFalse(fixture.operator.disarms)

        with tempfile.TemporaryDirectory(
                prefix="hepta-campaign-tombstone-tamper-") as directory:
            fixture = CampaignFixture(Path(directory), policy_document())
            fixture.tombstone["status"] = "ADMISSION_NO_GO"
            fixture.tombstone = ZERO_PRODUCER.seal(fixture.tombstone)
            fixture.write_tombstone()
            fixture.pointer["finalization_tombstone_reference"] = (
                fixture._reference(
                    fixture.tombstone_path, fixture.tombstone))
            fixture.pointer = ZERO_PRODUCER.seal(fixture.pointer)
            fixture.write_pointer()
            fixture.pin_terminal_policy()
            policy, _digest = fixture.provider()
            response = fixture.controller().process(open_request(policy))
            self.assertEqual(
                response["reason_code"],
                "CAMPAIGN_ADMISSION_FINALIZATION_INVALID")
            self.assertFalse(fixture.operator.disarms)

    def test_candidate_and_zero_exact_cross_bindings_are_required(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-campaign-candidate-reference-") as directory:
            fixture = CampaignFixture(Path(directory), policy_document())
            fixture.admission["input_bindings"]["source_baseline"][
                "file_sha256"] = digest(b"different-valid-input")
            fixture.admission = reseal_admission(fixture.admission)
            fixture.write_admission()
            pin_admission(fixture.document, fixture.admission)
            policy, _digest = fixture.provider()
            response = fixture.controller().process(open_request(policy))
            self.assertEqual(
                response["reason_code"],
                "CAMPAIGN_ADMISSION_FINALIZATION_INVALID")
            self.assertFalse(fixture.operator.disarms)

        with tempfile.TemporaryDirectory(
                prefix="hepta-campaign-zero-reference-") as directory:
            fixture = CampaignFixture(Path(directory), policy_document())
            fixture.zero["query_epoch"] = "different-valid-epoch"
            fixture.zero = ZERO_ATTESTOR.seal(fixture.zero)
            fixture.write_zero()
            fixture.tombstone["zero_exposure_receipt_reference"] = (
                fixture._reference(fixture.zero_path, fixture.zero))
            fixture.tombstone = ZERO_PRODUCER.seal(fixture.tombstone)
            fixture.write_tombstone()
            fixture.pointer["finalization_tombstone_reference"] = (
                fixture._reference(
                    fixture.tombstone_path, fixture.tombstone))
            fixture.pointer = ZERO_PRODUCER.seal(fixture.pointer)
            fixture.write_pointer()
            fixture.pin_terminal_policy()
            policy, _digest = fixture.provider()
            response = fixture.controller().process(open_request(policy))
            self.assertEqual(
                response["reason_code"],
                "CAMPAIGN_ADMISSION_FINALIZATION_INVALID")
            self.assertFalse(fixture.operator.disarms)

    def test_terminal_lineage_and_boundary_drift_never_disarm(self) -> None:
        cases = (
            ("campaign", "pointer", lambda value: value.__setitem__(
                "campaign_id", "campaign-other")),
            ("source", "tombstone", lambda value: value.__setitem__(
                "source_baseline_sha256", "sha256:" + "b" * 64)),
            ("generation", "pointer", lambda value: value.__setitem__(
                "reservation_generation", 2)),
            ("boot", "pointer", lambda value: value.__setitem__(
                "boot_id", "22222222-2222-2222-2222-222222222222")),
            ("lease", "tombstone", lambda value: value[
                "host_authority_lease"].__setitem__(
                    "lease_inode",
                    value["host_authority_lease"]["lease_inode"] + 1)),
            ("boundary", "pointer", lambda value: value.__setitem__(
                "paper_authorized", True)),
        )
        for label, target, mutate in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory(
                    prefix=f"hepta-campaign-lineage-{label}-") as directory:
                fixture = CampaignFixture(Path(directory), policy_document())
                artifact = (
                    fixture.pointer if target == "pointer" else
                    fixture.tombstone)
                mutate(artifact)
                if target == "tombstone":
                    fixture.tombstone = ZERO_PRODUCER.seal(
                        fixture.tombstone)
                    fixture.write_tombstone()
                    fixture.pointer[
                        "finalization_tombstone_reference"] = (
                            fixture._reference(
                                fixture.tombstone_path,
                                fixture.tombstone))
                fixture.pointer = ZERO_PRODUCER.seal(fixture.pointer)
                fixture.write_pointer()
                fixture.pin_terminal_policy()
                policy, _digest = fixture.provider()
                response = fixture.controller().process(open_request(policy))
                self.assertEqual(
                    response["reason_code"],
                    "CAMPAIGN_ADMISSION_FINALIZATION_INVALID")
                self.assertFalse(fixture.operator.disarms)

    def test_stale_generation_with_consistently_resealed_graph_is_rejected(
            self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-campaign-stale-generation-") as directory:
            fixture = CampaignFixture(Path(directory), policy_document())
            fixture.zero["reservation_generation"] = 2
            fixture.zero = ZERO_ATTESTOR.seal(fixture.zero)
            fixture.write_zero()
            fixture.admission["input_bindings"]["zero_exposure_receipt"] = {
                **fixture._reference(fixture.zero_path, fixture.zero),
                "schema": ZERO_ATTESTOR.OUTPUT_SCHEMA,
                "version": 1,
                "status": "PASS",
            }
            fixture.admission = reseal_admission(fixture.admission)
            fixture.write_admission()
            pin_admission(fixture.document, fixture.admission)
            fixture.tombstone.update({
                "reservation_generation": 2,
                "candidate_reference": fixture._reference(
                    fixture.admission_path, fixture.admission),
                "zero_exposure_receipt_reference": fixture._reference(
                    fixture.zero_path, fixture.zero),
            })
            fixture.tombstone = ZERO_PRODUCER.seal(fixture.tombstone)
            fixture.write_tombstone()
            fixture.pointer.update({
                "reservation_generation": 2,
                "finalization_tombstone_reference": fixture._reference(
                    fixture.tombstone_path, fixture.tombstone),
            })
            fixture.pointer = ZERO_PRODUCER.seal(fixture.pointer)
            fixture.write_pointer()
            fixture.pin_terminal_policy()
            policy, _digest = fixture.provider()
            response = fixture.controller().process(open_request(policy))
            self.assertEqual(
                response["reason_code"],
                "CAMPAIGN_ADMISSION_FINALIZATION_INVALID")
            self.assertFalse(fixture.operator.disarms)

    def test_host_authority_lease_is_held_through_disarm_and_reopen(
            self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-campaign-finalization-lock-held-") as directory:
            fixture = CampaignFixture(Path(directory), policy_document())
            lock_was_busy: list[bool] = []

            def probe_lock() -> None:
                descriptor = os.open(
                    fixture.lease_path, os.O_RDONLY | os.O_CLOEXEC)
                try:
                    try:
                        fcntl.flock(
                            descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        lock_was_busy.append(True)
                    else:
                        lock_was_busy.append(False)
                        fcntl.flock(descriptor, fcntl.LOCK_UN)
                finally:
                    os.close(descriptor)

            fixture.operator.on_disarm = probe_lock
            policy, _digest = fixture.provider()
            response = fixture.controller().process(open_request(policy))
            self.assertEqual(response["status"], "ok")
            self.assertEqual(lock_was_busy, [True])
            self.assertEqual(fixture.last_session.reopen_count, 1)
            self.assertTrue(fixture.last_session.closed)

    def test_disarm_time_terminal_drift_reengages_and_halts(self) -> None:
        for label, attribute in (
                ("pointer", "pointer_path"),
                ("tombstone", "tombstone_path"),
                ("candidate", "admission_path"),
                ("zero", "zero_path")):
            with self.subTest(label=label), tempfile.TemporaryDirectory(
                    prefix=f"hepta-campaign-disarm-{label}-") as directory:
                fixture = CampaignFixture(Path(directory), policy_document())
                path = getattr(fixture, attribute)

                def replace_artifact(path: Path = path) -> None:
                    replacement = path.parent / ("replacement-" + path.name)
                    replacement.write_bytes(path.read_bytes())
                    replacement.chmod(0o600)
                    os.replace(replacement, path)

                fixture.operator.on_disarm = replace_artifact
                policy, _digest = fixture.provider()
                response = fixture.controller().process(open_request(policy))
                self.assertEqual(response["status"], "rejected")
                self.assertEqual(
                    response["reason_code"],
                    "CAMPAIGN_ADMISSION_CHANGED_DURING_OPEN")
                self.assertEqual(response["state"]["status"], "halted")
                self.assertEqual(response["state"]["cycles_opened"], 0)
                self.assertEqual(len(fixture.operator.disarms), 1)
                self.assertEqual(len(fixture.operator.reengages), 1)

        with tempfile.TemporaryDirectory(
                prefix="hepta-campaign-disarm-owner-") as directory:
            fixture = CampaignFixture(Path(directory), policy_document())

            def restore_owner() -> None:
                owner = fixture.host_authority_root / "owner.v1"
                owner.write_bytes(b"{}\n")
                owner.chmod(0o600)

            fixture.operator.on_disarm = restore_owner
            policy, _digest = fixture.provider()
            response = fixture.controller().process(open_request(policy))
            self.assertEqual(response["status"], "rejected")
            self.assertEqual(
                response["reason_code"],
                "CAMPAIGN_ADMISSION_CHANGED_DURING_OPEN")
            self.assertEqual(response["state"]["status"], "halted")
            self.assertEqual(response["state"]["cycles_opened"], 0)
            self.assertEqual(len(fixture.operator.disarms), 1)
            self.assertEqual(len(fixture.operator.reengages), 1)

    def test_successful_open_exactly_reopens_admission(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-campaign-admission-reopen-") as directory:
            fixture = CampaignFixture(Path(directory), policy_document())
            policy, _digest = fixture.provider()
            response = fixture.controller().process(open_request(policy))
            self.assertEqual(response["status"], "ok")
            self.assertEqual(response["state"]["status"], "open")
            self.assertEqual(fixture.admission_reads, 1)
            self.assertIsNotNone(fixture.last_session)
            self.assertEqual(fixture.last_session.reopen_count, 1)
            self.assertTrue(fixture.last_session.closed)
            self.assertEqual(len(fixture.operator.disarms), 1)
            self.assertFalse(fixture.operator.reengages)

    def test_unrelated_ancestor_link_churn_does_not_change_admission(
            self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-campaign-admission-sibling-") as directory:
            fixture = CampaignFixture(Path(directory), policy_document())
            fixture.operator.on_disarm = lambda: (
                fixture.root / "unrelated-sibling").mkdir()
            policy, _digest = fixture.provider()
            response = fixture.controller().process(open_request(policy))
            self.assertEqual(response["status"], "ok")
            self.assertEqual(response["state"]["status"], "open")
            self.assertEqual(fixture.admission_reads, 1)
            self.assertEqual(len(fixture.operator.disarms), 1)
            self.assertFalse(fixture.operator.reengages)

    def test_admission_root_rebind_during_open_reengages_and_halts(
            self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-campaign-admission-rebind-") as directory:
            fixture = CampaignFixture(Path(directory), policy_document())
            payload = fixture.admission_path.read_bytes()

            def rebind_root() -> None:
                fixture.admission_root.rename(
                    fixture.root / "rebound-old-admission")
                fixture.admission_root.mkdir(mode=0o700)
                fixture.admission_path.write_bytes(payload)
                fixture.admission_path.chmod(0o600)

            fixture.operator.on_disarm = rebind_root
            policy, _digest = fixture.provider()
            response = fixture.controller().process(open_request(policy))
            self.assertEqual(response["status"], "rejected")
            self.assertEqual(
                response["reason_code"],
                "CAMPAIGN_ADMISSION_CHANGED_DURING_OPEN")
            self.assertEqual(response["state"]["status"], "halted")
            self.assertEqual(response["state"]["cycles_opened"], 0)
            self.assertEqual(fixture.admission_reads, 1)
            self.assertEqual(len(fixture.operator.disarms), 1)
            self.assertEqual(len(fixture.operator.reengages), 1)

    def test_admission_identity_drift_during_open_reengages_and_halts(
            self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-campaign-admission-open-drift-") as directory:
            fixture = CampaignFixture(Path(directory), policy_document())

            def drift_metadata() -> None:
                metadata = fixture.admission_path.stat()
                os.utime(
                    fixture.admission_path,
                    ns=(metadata.st_atime_ns, metadata.st_mtime_ns + 1_000_000))

            fixture.operator.on_disarm = drift_metadata
            policy, _digest = fixture.provider()
            response = fixture.controller().process(open_request(policy))
            self.assertEqual(response["status"], "rejected")
            self.assertEqual(
                response["reason_code"],
                "CAMPAIGN_ADMISSION_CHANGED_DURING_OPEN")
            self.assertEqual(response["state"]["status"], "halted")
            self.assertEqual(response["state"]["cycles_opened"], 0)
            self.assertEqual(fixture.admission_reads, 1)
            self.assertEqual(len(fixture.operator.disarms), 1)
            self.assertEqual(len(fixture.operator.reengages), 1)

    def test_same_byte_admission_replacement_during_open_reengages(
            self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-campaign-admission-replacement-") as directory:
            fixture = CampaignFixture(Path(directory), policy_document())
            payload = fixture.admission_path.read_bytes()

            def replace_admission() -> None:
                replacement = fixture.admission_root / "replacement.json"
                replacement.write_bytes(payload)
                replacement.chmod(0o600)
                os.replace(replacement, fixture.admission_path)

            fixture.operator.on_disarm = replace_admission
            policy, _digest = fixture.provider()
            response = fixture.controller().process(open_request(policy))
            self.assertEqual(response["status"], "rejected")
            self.assertEqual(
                response["reason_code"],
                "CAMPAIGN_ADMISSION_CHANGED_DURING_OPEN")
            self.assertEqual(response["state"]["status"], "halted")
            self.assertEqual(response["state"]["cycles_opened"], 0)
            self.assertEqual(fixture.admission_reads, 1)
            self.assertEqual(len(fixture.operator.disarms), 1)
            self.assertEqual(len(fixture.operator.reengages), 1)

    def test_admission_expiry_during_open_reengages_and_halts(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-campaign-admission-open-expiry-") as directory:
            clock = FakeClock()
            fixture = CampaignFixture(
                Path(directory), policy_document(), clock)
            fixture.operator.on_disarm = lambda: setattr(
                clock, "value", 1_100_000)
            policy, _digest = fixture.provider()
            response = fixture.controller().process(open_request(policy))
            self.assertEqual(response["status"], "rejected")
            self.assertEqual(
                response["reason_code"],
                "CAMPAIGN_ADMISSION_EXPIRED_DURING_OPEN")
            self.assertEqual(response["state"]["status"], "halted")
            self.assertEqual(response["state"]["cycles_opened"], 0)
            self.assertEqual(fixture.admission_reads, 1)
            self.assertEqual(len(fixture.operator.disarms), 1)
            self.assertEqual(len(fixture.operator.reengages), 1)

    def test_trade_intent_is_strategy_time_and_digest_bound(self) -> None:
        policy = CAMPAIGN.parse_policy(
            canonical(policy_document()), "alpha")
        intent = trade_intent()
        _validated, digest = CAMPAIGN.validate_trade_intent(
            intent, policy, 1_000_000)
        self.assertEqual(
            digest, "sha256:" + __import__("hashlib").sha256(
                canonical(intent)).hexdigest())
        wrong_strategy = dict(intent)
        wrong_strategy["strategy_id"] = "other"
        with self.assertRaisesRegex(
                CAMPAIGN.CampaignError,
                "CAMPAIGN_INTENT_STRATEGY_MISMATCH"):
            CAMPAIGN.validate_trade_intent(
                wrong_strategy, policy, 1_000_000)
        wrong_digest = dict(intent)
        wrong_digest["strategy_sha256"] = "sha256:" + "9" * 64
        with self.assertRaisesRegex(
                CAMPAIGN.CampaignError,
                "CAMPAIGN_INTENT_STRATEGY_MISMATCH"):
            CAMPAIGN.validate_trade_intent(
                wrong_digest, policy, 1_000_000)
        expired = dict(intent)
        expired["expires_at_ms"] = 1_000_500
        with self.assertRaisesRegex(
                CAMPAIGN.CampaignError, "CAMPAIGN_INTENT_TIME_INVALID"):
            CAMPAIGN.validate_trade_intent(expired, policy, 1_000_000)

    def test_open_close_is_idempotent_and_never_duplicates_operator(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-campaign-cycle-") as directory:
            fixture = CampaignFixture(
                Path(directory), policy_document(max_cycles=2))
            controller = fixture.controller()
            policy, _digest = fixture.provider()
            opened = open_request(policy)
            first = controller.process(opened)
            duplicate = controller.process(opened)
            self.assertEqual(first, duplicate)
            self.assertEqual(first["status"], "ok")
            self.assertEqual(first["state"]["status"], "open")
            self.assertEqual(len(fixture.operator.disarms), 1)

            closed_request = close_request(
                policy, opened["intent_sha256"])
            closed = controller.process(closed_request)
            closed_duplicate = controller.process(closed_request)
            self.assertEqual(closed, closed_duplicate)
            self.assertEqual(closed["state"]["status"], "idle")
            self.assertEqual(closed["state"]["cycles_closed"], 1)
            self.assertEqual(len(fixture.operator.reengages), 1)

    def test_request_id_payload_reuse_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-campaign-request-reuse-") as directory:
            fixture = CampaignFixture(Path(directory), policy_document())
            controller = fixture.controller()
            policy, _digest = fixture.provider()
            status = {
                "schema": CAMPAIGN.REQUEST_SCHEMA,
                "version": 1,
                "action": "status",
                "request_id": "same-request",
                "domain_id": "alpha",
                "campaign_id": policy.campaign_id,
            }
            self.assertEqual(controller.process(status)["status"], "ok")
            changed = dict(status)
            changed["action"] = "halt"
            changed["reason_code"] = "OPERATOR_STOP"
            with self.assertRaisesRegex(
                    CAMPAIGN.CampaignError, "CAMPAIGN_REQUEST_ID_REUSE"):
                controller.process(changed)

    def test_cooldown_and_cycle_cap_are_root_state_enforced(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-campaign-budget-") as directory:
            clock = FakeClock()
            fixture = CampaignFixture(
                Path(directory), policy_document(max_cycles=2), clock)
            controller = fixture.controller()
            policy, _digest = fixture.provider()
            first_open = open_request(policy)
            self.assertEqual(controller.process(first_open)["status"], "ok")
            self.assertEqual(controller.process(close_request(
                policy, first_open["intent_sha256"]))["status"], "ok")

            second_intent = trade_intent(
                intent_id="intent-b", observed_at_ms=999_500,
                expires_at_ms=1_010_000)
            second_open = open_request(
                policy, request_id="request-open-b", cycle_id="cycle-b",
                intent=second_intent)
            cooldown = controller.process(second_open)
            self.assertEqual(cooldown["status"], "rejected")
            self.assertEqual(
                cooldown["reason_code"], "CAMPAIGN_CYCLE_COOLDOWN")

            clock.value += 5_000
            second_open["request_id"] = "request-open-b-after-cooldown"
            second_open["intent"]["observed_at_ms"] = clock.value - 500
            second_open["intent"]["expires_at_ms"] = clock.value + 10_000
            _intent, second_digest = CAMPAIGN.validate_trade_intent(
                second_open["intent"], policy, clock.value)
            second_open["intent_sha256"] = second_digest
            self.assertEqual(controller.process(second_open)["status"], "ok")
            second_close = close_request(
                policy, second_digest, request_id="request-close-b",
                cycle_id="cycle-b")
            complete = controller.process(second_close)
            self.assertEqual(complete["state"]["status"], "halted")
            self.assertEqual(
                complete["state"]["halt_reason"], "CAMPAIGN_COMPLETE")

    def test_policy_drift_reengages_before_halting(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-campaign-drift-") as directory:
            fixture = CampaignFixture(Path(directory), policy_document())
            controller = fixture.controller()
            policy, _digest = fixture.provider()
            opened = open_request(policy)
            self.assertEqual(controller.process(opened)["status"], "ok")
            fixture.document["strategy_sha256"] = "sha256:" + "9" * 64
            status = {
                "schema": CAMPAIGN.REQUEST_SCHEMA,
                "version": 1,
                "action": "status",
                "request_id": "status-after-drift",
                "domain_id": "alpha",
                "campaign_id": policy.campaign_id,
            }
            response = controller.process(status)
            self.assertEqual(response["status"], "ok")
            self.assertEqual(response["state"]["status"], "halted")
            self.assertEqual(
                response["state"]["halt_reason"], "CAMPAIGN_POLICY_DRIFT")
            self.assertEqual(len(fixture.operator.reengages), 1)

    def test_policy_change_during_open_reengages_without_opening(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-campaign-open-drift-") as directory:
            fixture = CampaignFixture(Path(directory), policy_document())
            fixture.operator.on_disarm = lambda: fixture.document.update({
                "strategy_sha256": "sha256:" + "9" * 64,
            })
            policy, _digest = fixture.provider()
            response = fixture.controller().process(open_request(policy))
            self.assertEqual(response["status"], "rejected")
            self.assertEqual(
                response["reason_code"],
                "CAMPAIGN_POLICY_CHANGED_DURING_OPEN")
            self.assertEqual(response["state"]["status"], "halted")
            self.assertEqual(response["state"]["cycles_opened"], 0)
            self.assertEqual(len(fixture.operator.disarms), 1)
            self.assertEqual(len(fixture.operator.reengages), 1)

    def test_same_byte_policy_replacement_during_open_reengages(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-campaign-policy-replacement-") as directory:
            root = Path(directory)
            fixture = CampaignFixture(root, policy_document())
            policy_path = root / "active-policy.json"
            policy_raw = canonical(fixture.document)
            policy_path.write_bytes(policy_raw)
            policy_path.chmod(0o600)

            def replace_policy() -> None:
                replacement = root / "replacement-policy.json"
                replacement.write_bytes(policy_raw)
                replacement.chmod(0o600)
                os.replace(replacement, policy_path)

            fixture.operator.on_disarm = replace_policy
            controller = CAMPAIGN.CampaignController(
                lambda: CAMPAIGN.load_policy(
                    policy_path, "alpha", installed=False),
                fixture.operator, fixture.paths, fixture.clock,
                root_uid=fixture.uid, root_gid=fixture.gid,
                admission_provider=fixture.read_admission)
            policy_snapshot = CAMPAIGN.load_policy(
                policy_path, "alpha", installed=False)
            response = controller.process(
                open_request(policy_snapshot.policy))
            self.assertEqual(response["status"], "rejected")
            self.assertEqual(
                response["reason_code"],
                "CAMPAIGN_POLICY_CHANGED_DURING_OPEN")
            self.assertEqual(response["state"]["status"], "halted")
            self.assertEqual(response["state"]["cycles_opened"], 0)
            self.assertEqual(len(fixture.operator.disarms), 1)
            self.assertEqual(len(fixture.operator.reengages), 1)

    def test_invalid_operator_deadline_reengages_without_opening(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-campaign-deadline-") as directory:
            fixture = CampaignFixture(Path(directory), policy_document())
            fixture.operator.deadline_at_ms = 1_200_001
            policy, _digest = fixture.provider()
            response = fixture.controller().process(open_request(policy))
            self.assertEqual(response["status"], "rejected")
            self.assertEqual(
                response["reason_code"],
                "CAMPAIGN_OPERATOR_DEADLINE_INVALID")
            self.assertEqual(response["state"]["status"], "halted")
            self.assertEqual(response["state"]["cycles_opened"], 0)
            self.assertEqual(len(fixture.operator.reengages), 1)

    def test_new_campaign_recovers_previous_active_cycle_first(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-campaign-replacement-") as directory:
            fixture = CampaignFixture(Path(directory), policy_document())
            controller = fixture.controller()
            policy, _digest = fixture.provider()
            self.assertEqual(
                controller.process(open_request(policy))["status"], "ok")
            fixture.document["campaign_id"] = "campaign-b"
            status = {
                "schema": CAMPAIGN.REQUEST_SCHEMA,
                "version": 1,
                "action": "status",
                "request_id": "replacement-status-a",
                "domain_id": "alpha",
                "campaign_id": "campaign-b",
            }
            recovered = controller.process(status)
            self.assertEqual(recovered["state"]["status"], "halted")
            self.assertEqual(
                recovered["state"]["halt_reason"],
                "CAMPAIGN_POLICY_DRIFT")
            self.assertEqual(len(fixture.operator.reengages), 1)
            status["request_id"] = "replacement-status-b"
            reset = controller.process(status)
            self.assertEqual(reset["state"]["status"], "idle")
            self.assertEqual(reset["state"]["cycles_opened"], 0)

    def test_expired_operator_window_reengages_and_halts(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-campaign-window-") as directory:
            clock = FakeClock()
            fixture = CampaignFixture(
                Path(directory), policy_document(), clock)
            controller = fixture.controller()
            policy, _digest = fixture.provider()
            opened = open_request(policy)
            self.assertEqual(controller.process(opened)["status"], "ok")
            clock.value = 1_020_000
            status = {
                "schema": CAMPAIGN.REQUEST_SCHEMA,
                "version": 1,
                "action": "status",
                "request_id": "status-expired-window",
                "domain_id": "alpha",
                "campaign_id": policy.campaign_id,
            }
            response = controller.process(status)
            self.assertEqual(response["state"]["status"], "halted")
            self.assertEqual(
                response["state"]["halt_reason"],
                "CAMPAIGN_OPERATOR_WINDOW_EXPIRED")
            self.assertEqual(len(fixture.operator.reengages), 1)

    def test_operator_failure_is_recovery_required_and_persistent(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-campaign-operator-fail-") as directory:
            fixture = CampaignFixture(Path(directory), policy_document())
            fixture.operator.fail_disarm = True
            controller = fixture.controller()
            policy, _digest = fixture.provider()
            response = controller.process(open_request(policy))
            self.assertEqual(response["status"], "recovery_required")
            self.assertEqual(
                response["reason_code"], "CAMPAIGN_OPERATOR_REJECTED")
            self.assertEqual(response["state"]["status"], "closing")

    def test_legacy_idle_state_is_migrated_to_v2_before_use(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-campaign-state-v1-idle-") as directory:
            fixture = CampaignFixture(
                Path(directory), v5_policy_document())
            policy, state_path, _legacy = self._write_legacy_state(
                fixture, "idle")
            response = fixture.controller().process(self._status_request(
                policy, "status-migrate-v1-idle"))
            self.assertEqual(response["status"], "ok")
            self.assertEqual(response["state"]["status"], "idle")
            migrated = json.loads(state_path.read_text(encoding="ascii"))
            self.assertEqual(migrated["schema"], CAMPAIGN.STATE_SCHEMA)
            self.assertEqual(migrated["version"], 2)
            self.assertEqual(set(migrated), CAMPAIGN.STATE_FIELDS)
            self.assertIsNone(migrated["consumption_receipt_name"])
            self.assertFalse(fixture.operator.reengages)

    def test_legacy_active_states_reengage_before_v2_halt(self) -> None:
        for legacy_status in ("opening", "open", "closing"):
            with self.subTest(status=legacy_status), \
                    tempfile.TemporaryDirectory(
                        prefix="hepta-campaign-state-v1-active-") \
                    as directory:
                fixture = CampaignFixture(
                    Path(directory), v5_policy_document())
                policy, state_path, _legacy = self._write_legacy_state(
                    fixture, legacy_status)
                response = fixture.controller().process(
                    self._status_request(
                        policy, f"status-migrate-v1-{legacy_status}"))
                self.assertEqual(response["status"], "ok")
                self.assertEqual(response["state"]["status"], "halted")
                self.assertEqual(
                    response["state"]["halt_reason"],
                    "CAMPAIGN_STATE_SCHEMA_UPGRADE")
                self.assertEqual(len(fixture.operator.reengages), 1)
                migrated = json.loads(
                    state_path.read_text(encoding="ascii"))
                self.assertEqual(migrated["schema"], CAMPAIGN.STATE_SCHEMA)
                self.assertEqual(migrated["version"], 2)
                self.assertEqual(migrated["status"], "halted")
                self.assertIsNone(migrated["active_cycle"])

    def test_legacy_active_reengage_failure_persists_v2_recovery_state(
            self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-campaign-state-v1-recovery-") as directory:
            fixture = CampaignFixture(
                Path(directory), v5_policy_document())
            policy, state_path, _legacy = self._write_legacy_state(
                fixture, "open")
            fixture.operator.fail_reengage = True
            with self.assertRaisesRegex(
                    CAMPAIGN.CampaignError,
                    "CAMPAIGN_OPERATOR_REJECTED"):
                fixture.controller().process(self._status_request(
                    policy, "status-migrate-v1-recovery"))
            self.assertEqual(len(fixture.operator.reengages), 1)
            persisted = json.loads(
                state_path.read_text(encoding="ascii"))
            self.assertEqual(persisted["schema"], CAMPAIGN.STATE_SCHEMA)
            self.assertEqual(persisted["version"], 2)
            self.assertEqual(persisted["status"], "closing")
            self.assertIsInstance(persisted["active_cycle"], dict)
            self.assertEqual(
                persisted["halt_reason"],
                "CAMPAIGN_STATE_SCHEMA_UPGRADE")

    def test_malformed_legacy_active_state_fails_before_reengage(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-campaign-state-v1-malformed-") as directory:
            fixture = CampaignFixture(
                Path(directory), v5_policy_document())
            policy, state_path, legacy = self._write_legacy_state(
                fixture, "open")
            active = legacy["active_cycle"]
            self.assertIsInstance(active, dict)
            active["deadline_at_ms"] = active["opened_at_ms"]
            original = canonical(legacy)
            state_path.write_bytes(original)
            state_path.chmod(0o600)
            with self.assertRaisesRegex(
                    CAMPAIGN.CampaignError,
                    "CAMPAIGN_ACTIVE_CYCLE_INVALID"):
                fixture.controller().process(self._status_request(
                    policy, "status-migrate-v1-malformed"))
            self.assertFalse(fixture.operator.reengages)
            self.assertEqual(state_path.read_bytes(), original)

    def test_state_contract_rejects_boolean_counters_and_bad_deadline(
            self) -> None:
        policy = CAMPAIGN.parse_policy(
            canonical(policy_document()), "alpha")
        state = CAMPAIGN._new_state(
            policy, "sha256:" + "3" * 64, 1_000_000)
        state["cycles_opened"] = True
        with self.assertRaisesRegex(
                CAMPAIGN.CampaignError,
                "CAMPAIGN_STATE_CONTRACT_INVALID"):
            CAMPAIGN._validate_state(state)
        state = CAMPAIGN._new_state(
            policy, "sha256:" + "3" * 64, 1_000_000)
        state["status"] = "opening"
        state["active_cycle"] = {
            "cycle_id": "cycle-a",
            "intent_sha256": "sha256:" + "4" * 64,
            "preflight_sha256": "sha256:" + "5" * 64,
            "opened_at_ms": 1_000_000,
            "deadline_at_ms": policy.expires_at_ms + 1,
        }
        with self.assertRaisesRegex(
                CAMPAIGN.CampaignError,
                "CAMPAIGN_ACTIVE_CYCLE_INVALID"):
            CAMPAIGN._validate_state(state)

    def test_insecure_runtime_directory_is_rejected_before_operator(
            self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-campaign-directory-") as directory:
            fixture = CampaignFixture(Path(directory), policy_document())
            fixture.paths.runtime_root.mkdir(mode=0o755)
            fixture.paths.runtime_root.chmod(0o755)
            self.assertEqual(
                stat.S_IMODE(fixture.paths.runtime_root.stat().st_mode), 0o755)
            status = {
                "schema": CAMPAIGN.REQUEST_SCHEMA,
                "version": 1,
                "action": "status",
                "request_id": "unsafe-directory",
                "domain_id": "alpha",
                "campaign_id": "campaign-a",
            }
            with self.assertRaisesRegex(
                    CAMPAIGN.CampaignError,
                    "CAMPAIGN_DIRECTORY_METADATA_UNSAFE"):
                fixture.controller().process(status)
            self.assertFalse(fixture.operator.disarms)

    def test_peer_uid_mismatch_is_rejected_before_reading_request(self) -> None:
        class FakeChannel:
            def __init__(self) -> None:
                self.closed = False
                self.read = False

            def getsockopt(self, *_arguments):
                return struct.pack("3i", os.getpid(), os.getuid(), os.getgid())

            def recv(self, _size: int) -> bytes:
                self.read = True
                return b""

            def close(self) -> None:
                self.closed = True

        class FakeListener:
            def __init__(self, channel: FakeChannel) -> None:
                self.channel = channel
                self.closed = False

            def accept(self):
                return self.channel, None

            def close(self) -> None:
                self.closed = True

        channel = FakeChannel()
        listener = FakeListener(channel)
        with (
                mock.patch.object(
                    CAMPAIGN, "_socket_activation_listener",
                    return_value=listener),
                self.assertRaisesRegex(
                    CAMPAIGN.CampaignError,
                    "CAMPAIGN_PEER_IDENTITY_REJECTED")):
            CAMPAIGN.serve_once(
                "alpha", mock.Mock(), os.getuid() + 1)
        self.assertTrue(listener.closed)
        self.assertTrue(channel.closed)
        self.assertFalse(channel.read)

    def test_client_hashes_intent_and_preflight_without_sending_preflight(
            self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-campaign-client-") as directory:
            root = Path(directory)
            intent_path = root / "intent.json"
            preflight_path = root / "preflight.json"
            intent_path.write_bytes(canonical(trade_intent()))
            preflight_path.write_bytes(canonical({"authoritative": True}))
            intent_path.chmod(0o600)
            preflight_path.chmod(0o600)
            arguments = argparse.Namespace(
                action="open_cycle", request_id="client-open",
                domain="alpha", campaign_id="campaign-a",
                cycle_id="cycle-a", intent_file=intent_path,
                preflight_file=preflight_path)
            request = CLIENT._request(arguments)
            self.assertEqual(request["intent"], trade_intent())
            self.assertNotIn("preflight", request)
            self.assertEqual(
                request["preflight_sha256"],
                "sha256:" + __import__("hashlib").sha256(
                    preflight_path.read_bytes()).hexdigest())

    def test_request_parser_rejects_unknown_or_live_fields(self) -> None:
        policy = CAMPAIGN.parse_policy(
            canonical(policy_document()), "alpha")
        request = open_request(policy)
        request["live_authorized"] = True
        with self.assertRaisesRegex(
                CAMPAIGN.CampaignError,
                "CAMPAIGN_REQUEST_CONTRACT_INVALID"):
            CAMPAIGN.parse_request(canonical(request))


if __name__ == "__main__":
    unittest.main(verbosity=2)
