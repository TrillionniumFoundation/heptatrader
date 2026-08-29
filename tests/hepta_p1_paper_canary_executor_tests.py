#!/usr/bin/env python3

from __future__ import annotations

from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "scripts/hepta_p1_paper_canary_executor.py"
BACKEND_SOURCE = ROOT / "scripts/hepta_p1_paper_canary_backend_adapter.py"
V3_SOURCE = ROOT / "scripts/hepta_paper_receipt_contracts.py"
V2_SOURCE = ROOT / "scripts/hepta_paper_receipt_contracts_v2_compat.py"


def load_module():
    spec = importlib.util.spec_from_file_location("hepta_canary_executor_test", SOURCE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


EXECUTOR = load_module()


def load_backend_module():
    spec = importlib.util.spec_from_file_location(
        "hepta_canary_backend_adapter_test", BACKEND_SOURCE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


BACKEND_ADAPTER = load_backend_module()


def digest(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode("ascii")).hexdigest()


def reseal(document: dict) -> bytes:
    body = dict(document)
    body.pop("body_sha256", None)
    document["body_sha256"] = EXECUTOR.canonical_sha256(body)
    return EXECUTOR.canonical_json(document)


def exact_profile(**updates: str) -> bytes:
    values = {
        "HEPTA_EXECUTION_EXTERNAL_P1_CANARY_LMT_DAY": "1",
        "HEPTA_EXECUTION_MAX_ORDER_NOTIONAL": "5000",
        "HEPTA_IB_EXECUTION_MODE": "PAPER",
        "HEPTA_IB_PAPER_ACCOUNT": "DU123",
        "HEPTA_IB_PAPER_HOST": "127.0.0.1",
        "HEPTA_IB_PAPER_PORT": "4002",
        "HEPTA_IB_PAPER_CLIENT_ID": "701",
        "HEPTA_IB_PAPER_MAX_ORDER_QTY": "1",
        "HEPTA_IB_PAPER_MAX_ORDER_NOTIONAL": "5000",
        "HEPTA_IB_PAPER_MAX_ORDERS_PER_MINUTE": "1",
        "HEPTA_IB_PAPER_MAX_ACTIVE_ORDERS": "1",
        "HEPTA_IB_PAPER_MAX_GROSS_POSITION": "1",
        "HEPTA_IB_PAPER_QUOTE_CONTRACTS":
            "EUR.USD|EUR|CASH|IDEALPRO|USD",
        "HEPTA_IB_PAPER_PRIMARY_QUOTE_INSTRUMENT": "EUR.USD",
        "HEPTA_IB_PAPER_QUOTE_MAX_AGE_MS": "5000",
        "HEPTA_IB_EXECUTION_GATEWAY_UID": "2101",
        "HEPTA_IB_EXECUTION_GATEWAY_AGENT_ID": "alpha",
        "HEPTA_IB_EXECUTION_DOMAIN_ID": "alpha",
        "HEPTA_IB_EXECUTION_MAX_REQUEST_BYTES": "16384",
        "HEPTA_IB_EXECUTION_IO_TIMEOUT_MS": "2500",
        "HEPTA_IB_EXECUTION_READINESS_TIMEOUT_MS": "30000",
        "HEPTA_IB_EXECUTION_RECONNECT_TIMEOUT_MS": "180000",
    }
    values.update(updates)
    return "".join(
        f"{key}={values[key]}\n" for key in BACKEND_ADAPTER.PROFILE_KEYS
    ).encode("ascii")


def handoff_document(now_ms: int = 1_000_000) -> dict:
    intent = {
        "schema": "hepta.trade-intent.v1", "paper_only": True,
        "strategy_id": "strategy-eurusd", "strategy_version": "1",
        "strategy_sha256": digest("strategy"), "intent_id": "intent-canary-1",
        "instrument": "EUR.USD", "symbol": "EUR", "currency": "USD",
        "sec_type": "CASH", "exchange": "IDEALPRO", "side": "BUY",
        "quantity": 1, "order_type": "LMT", "tif": "DAY",
        "observed_bid": "1.15320", "observed_ask": "1.15325",
        "observed_at_ms": now_ms - 100, "expires_at_ms": now_ms + 50_000,
        "entry_thesis": "immutable upstream decision",
        "invalidation_condition": "handoff horizon expires",
        "max_holding_ms": 10_000, "max_adverse_move": "0.00050",
        "expected_slippage": "0.00005", "exit_plan": "immediate end-flat",
        "limit_price": "1.15325",
    }
    calls = []
    for index, (role, name, effect, phase) in enumerate(EXECUTOR.ROLE_PLAN, 1):
        calls.append({
            "call_role": role, "tool_call_id": f"call-canary-{index:02d}",
            "tool_name": name, "tool_descriptor_sha256": digest("descriptor-" + role),
            "effect": effect, "phase": phase,
            "command_id": (
                None if effect == "READ_ONLY" else f"call-canary-{index:02d}"),
        })
    projection = [
        {field: call[field] for field in EXECUTOR.TOOL_BINDING_FIELDS}
        for call in calls
    ]
    image_payloads = {
        "executor": SOURCE.read_bytes(),
        "receipt-validator-v3": V3_SOURCE.read_bytes(),
        "receipt-validator-v2": V2_SOURCE.read_bytes(),
        "backend-adapter": b"fixed reviewed backend adapter fixture\n",
        "handoff-producer": b"fixed root handoff producer fixture\n",
        "native-tool-client": b"fixed native tool client fixture\n",
        "campaign-operator": b"fixed campaign operator fixture\n",
        "root-finalizer": b"fixed root finalizer fixture\n",
        "launch-joiner": b"fixed launch joiner fixture\n",
        "owner-provisioner": b"fixed owner provisioner fixture\n",
        "root-coordinator": b"fixed root coordinator fixture\n",
        "crash-emergency-closer":
            b"fixed crash emergency closer fixture\n",
        "terminal-prover": b"fixed terminal prover fixture\n",
    }
    installed_images = [
        {
            "role": role, "path": path,
            "file_sha256": "sha256:" + hashlib.sha256(
                image_payloads[role]).hexdigest(),
            "mode": 0o755, "uid": 0, "gid": 0, "nlink": 1,
        }
        for role, path in EXECUTOR.INSTALLED_IMAGE_PATHS
    ]
    document = {
        "schema": EXECUTOR.HANDOFF_SCHEMA, "version": 1,
        "issued_at_ms": now_ms - 1_000,
        "expires_at_ms": now_ms - 1_000 + EXECUTOR.POLICY_WINDOW_MS,
        "campaign_id": "campaign-canary-1", "domain_id": "alpha",
        "policy_sha256": digest("policy"),
        "source_baseline_sha256": digest("source"),
        "p1_audit_receipt_sha256": digest("p1-audit"),
        "watch_handoff_receipt_file_sha256": digest("watch-handoff-file"),
        "watch_handoff_receipt_body_sha256": digest("watch-handoff-body"),
        "zero_exposure_attestation_sha256": digest("zero"),
        "admission_finalization_receipt_sha256": digest("finalization"),
        "strategy_id": intent["strategy_id"],
        "strategy_version": intent["strategy_version"],
        "strategy_sha256": intent["strategy_sha256"],
        "decision_id": "decision-canary-1", "decision_sha256": digest("decision"),
        "cycle_id": "cycle-canary-1", "intent": intent,
        "intent_sha256": EXECUTOR.canonical_sha256(intent),
        "tool_catalog_sha256": digest("catalog"),
        "tool_descriptor_set_sha256": EXECUTOR.canonical_sha256(projection),
        "tool_calls": calls,
        "root_cleanup_call": {
            "call_role": "cleanup-control",
            "tool_name": "host.finalize_external_p1",
            "operation": EXECUTOR.ROOT_CLEANUP_OPERATION,
            "effect": "CONTROL", "phase": "ROOT_CLEANUP",
            "socket_path": EXECUTOR.ROOT_FINALIZER_SOCKET,
            "request_schema": EXECUTOR.ROOT_CLEANUP_REQUEST_SCHEMA,
            "emergency_request_schema":
                EXECUTOR.ROOT_EMERGENCY_CLEANUP_REQUEST_SCHEMA,
            "response_schema": EXECUTOR.ROOT_CLEANUP_RECEIPT_SCHEMA,
            "emergency_response_schema":
                EXECUTOR.ROOT_EMERGENCY_CLEANUP_RECEIPT_SCHEMA,
            "tool_call_id": "call-root-cleanup-01",
            "command_id": "call-root-cleanup-01",
            "tool_descriptor_sha256":
                EXECUTOR.ROOT_CLEANUP_DESCRIPTOR_SHA256,
        },
        "installed_images": installed_images,
        "installed_images_sha256": EXECUTOR.canonical_sha256(installed_images),
        "runtime_profile_reference": {
            "path": "/etc/heptatrader/trust-domains/alpha.ib-paper.env",
            "file_sha256": digest("external-p1-lmt-day-runtime-profile"),
            "size": 900, "mode": 0o644, "uid": 0, "gid": 0, "nlink": 1,
        },
        "backend_transform_version": EXECUTOR.BACKEND_TRANSFORM_VERSION,
        "execution_service_epoch": "epoch-a",
        "execution_service_fencing_generation": 7,
        "session_owner_reference": {
            "token_name": "session.token",
            "token_path": "/run/hepta-agent-alpha/sessions/session.token",
            "authority_path": (
                "/var/lib/hepta-local-ai-paper-agent/session-authority/"
                "session.token.authority.json"),
            "authority_file_sha256": digest("owner-file"),
            "authority_body_sha256": digest("owner-body"), "lease_generation": 7,
            "session_id": "session-canary-1", "peer_uid": 2104,
            "peer_gid": 2104,
            "token_sha256": digest("token"),
            "revoke_bearer_path": (
                "/var/lib/hepta-local-ai-paper-agent/session-authority/"
                "session.token.revoke-token"),
            "revoke_bearer_sha256": digest("token"),
            "owner_account": "DU123",
            "owner_execution_domain": "PAPER:alpha",
        },
        "paper_only": True, "live_authorized": False,
        "direct_broker_access": False, "authority_granted": False,
        "one_order_only": True, "end_flat_required": True,
    }
    reseal(document)
    return document


class FakeBackend:
    def __init__(
            self, handoff: dict, *, scenario: str = "cancel",
            uncertain_role: str | None = None, drift_role: str | None = None,
            wrong_pin_role: str | None = None, fatal_role: str | None = None,
            scope_substitution_role: str | None = None,
            cleanup_risk_overrides: dict | None = None,
            root_receipt_overrides: dict | None = None):
        self.handoff = handoff
        self.now = 1_000_000
        self.scenario = scenario
        self.uncertain_role = uncertain_role
        self.drift_role = drift_role
        self.wrong_pin_role = wrong_pin_role
        self.fatal_role = fatal_role
        self.scope_substitution_role = scope_substitution_role
        self.cleanup_risk_overrides = cleanup_risk_overrides or {}
        self.root_receipt_overrides = root_receipt_overrides or {}
        self.journal = b""
        validated = EXECUTOR.validate_handoff(
            EXECUTOR.canonical_json(handoff), now_ms=self.now)
        self.journal_path = EXECUTOR.journal_path_for(validated)
        self.calls = {call["tool_call_id"]: call for call in handoff["tool_calls"]}
        self.trace: list[str] = []
        self.published: dict[str, bytes] = {}
        self.checkpoint: dict[str, bytes] = {}
        self.cancelled = False
        self.flattened = False
        self.order_id = digest("broker-order-1")

    def now_ms(self) -> int:
        self.now += 1
        return self.now

    def read_handoff(self) -> bytes:
        return EXECUTOR.canonical_json(self.handoff)

    def append_journal(self, record: bytes) -> None:
        self.journal += record

    def reopen_journal(self):
        return {
            "path": self.journal_path, "raw": self.journal,
            "secure_reopen": True, "mode": 0o600, "nlink": 1,
        }

    def publish_artifacts(self, artifacts):
        self.published = dict(artifacts)

    def publish_checkpoint(self, artifacts):
        self.checkpoint = dict(artifacts)

    def finalize_root_cleanup(self, request_raw: bytes) -> bytes:
        self.trace.append("root-cleanup")
        request = EXECUTOR._load_canonical(
            request_raw, "TEST_ROOT_CLEANUP_REQUEST")
        emergency = request["schema"] == \
            EXECUTOR.ROOT_EMERGENCY_CLEANUP_REQUEST_SCHEMA
        evidence_name = (
            "root-emergency-cleanup-evidence.v1.json" if emergency else
            "pre-cleanup-flat-evidence.v1.json")
        evidence_raw = self.checkpoint[evidence_name]
        evidence = EXECUTOR._load_canonical(
            evidence_raw, "TEST_PRE_CLEANUP_EVIDENCE")
        validated = EXECUTOR.validate_handoff(
            EXECUTOR.canonical_json(self.handoff), require_fresh=False)
        body = {
            "schema": (
                EXECUTOR.ROOT_EMERGENCY_CLEANUP_RECEIPT_SCHEMA if emergency
                else EXECUTOR.ROOT_CLEANUP_RECEIPT_SCHEMA),
            "version": (
                1 if emergency else EXECUTOR.ROOT_CLEANUP_RECEIPT_VERSION),
            "status": (
                "ROOT_EMERGENCY_CLEANUP_COMPLETE_DENY_ALL" if emergency
                else "ROOT_CLEANUP_COMPLETE_DENY_ALL"),
            "completed_at_ms": self.now_ms(),
            "campaign_id": self.handoff["campaign_id"],
            "domain_id": self.handoff["domain_id"],
            "cycle_id": self.handoff["cycle_id"],
            "cleanup_tool_call_id": request["cleanup_tool_call_id"],
            "cleanup_command_id": request["cleanup_command_id"],
            "tool_descriptor_sha256": request["tool_descriptor_sha256"],
            "execution_handoff_path": EXECUTOR.handoff_path_for(validated),
            "execution_handoff_file_sha256": validated.file_sha256,
            "execution_handoff_body_sha256": validated.body_sha256,
            "watch_handoff_file_sha256": self.handoff[
                "watch_handoff_receipt_file_sha256"],
            "watch_handoff_body_sha256": self.handoff[
                "watch_handoff_receipt_body_sha256"],
            "intent_sha256": self.handoff["intent_sha256"],
            "installed_images_sha256": self.handoff[
                "installed_images_sha256"],
            "executor_image_sha256": next(
                item["file_sha256"] for item in self.handoff["installed_images"]
                if item["role"] == "executor"),
            "backend_adapter_image_sha256": next(
                item["file_sha256"] for item in self.handoff["installed_images"]
                if item["role"] == "backend-adapter"),
            "root_finalizer_image_sha256": next(
                item["file_sha256"] for item in self.handoff["installed_images"]
                if item["role"] == "root-finalizer"),
            "backend_transform_version": EXECUTOR.BACKEND_TRANSFORM_VERSION,
            "session_owner_reference_sha256": EXECUTOR.canonical_sha256(
                self.handoff["session_owner_reference"]),
            "execution_service_epoch": self.handoff[
                "execution_service_epoch"],
            "execution_service_fencing_generation": self.handoff[
                "execution_service_fencing_generation"],
            "journal_path": self.journal_path,
            "journal_sha256": EXECUTOR.sha256_bytes(self.journal),
            "journal_size": len(self.journal),
            "journal_last_sequence": evidence["journal_last_sequence"],
            "tool_evidence_sha256": evidence["tool_evidence_sha256"],
            "guardian_request_id": "guardian-request-1",
            "local_control_transaction_id": "local-control-transaction-1",
            "local_control_request_sha256": digest("local-control-request"),
            "guardian_active_receipt_file_sha256": digest(
                "guardian-active-file"),
            "guardian_active_receipt_body_sha256": digest(
                "guardian-active-body"),
            "completed_actions": list(
                EXECUTOR.ROOT_EMERGENCY_CLEANUP_ACTIONS if emergency else
                EXECUTOR.ROOT_CLEANUP_ACTIONS),
            "guardian_stopped": True,
            "execution_control_disabled": True,
            "kill_switch_engaged": True,
            "global_kill_switch_engaged": True,
            "broker_deny_all": True,
            "broker_mutation_units_inactive": True,
            "broker_mutation_units": list(EXECUTOR.BROKER_MUTATION_UNITS),
            "broker_mutation_units_sha256": EXECUTOR.canonical_sha256(
                list(EXECUTOR.BROKER_MUTATION_UNITS)),
            "permit_absent": True,
            "runtime_session_count": 0,
            "guardian_runtime_absent": True,
            "authorized_connector_count": 0,
            "identity_count": 0,
            "identity_manifest_sha256": digest("identity-manifest"),
            "broker_policy_sha256": digest("broker-policy"),
            "durable_owner_reference_sha256": EXECUTOR.canonical_sha256(
                self.handoff["session_owner_reference"]),
            "paper_only": True,
            "live_authorized": False,
            "authority_granted": False,
        }
        if emergency:
            body.update({
                "emergency_evidence_path": request["emergency_evidence_path"],
                "emergency_evidence_file_sha256": request[
                    "emergency_evidence_file_sha256"],
                "emergency_evidence_body_sha256": request[
                    "emergency_evidence_body_sha256"],
                "root_emergency_cleanup_request_path":
                    EXECUTOR.root_emergency_cleanup_request_path_for(validated),
                "root_emergency_cleanup_request_file_sha256":
                    EXECUTOR.sha256_bytes(request_raw),
                "root_emergency_cleanup_request_body_sha256": request[
                    "body_sha256"],
                "recovery_reason_codes": request["recovery_reason_codes"],
                "broker_flat_proven": False,
                "recovery_required": True,
                "evidence_retained": True,
                "durable_owner_count": 1,
                "durable_owner_status": "RECOVERY_ONLY",
                "durable_recovery_owner_reference_path":
                    EXECUTOR.durable_recovery_owner_reference_path_for(
                        validated),
                "durable_recovery_owner_reference_file_sha256": digest(
                    "durable-recovery-owner-reference-file"),
                "durable_recovery_owner_reference_body_sha256": digest(
                    "durable-recovery-owner-reference-body"),
            })
        else:
            body.update({
                "pre_cleanup_evidence_path": request[
                    "pre_cleanup_evidence_path"],
                "pre_cleanup_evidence_file_sha256": request[
                    "pre_cleanup_evidence_file_sha256"],
                "pre_cleanup_evidence_body_sha256": request[
                    "pre_cleanup_evidence_body_sha256"],
                "root_cleanup_request_path":
                    EXECUTOR.root_cleanup_request_path_for(validated),
                "root_cleanup_request_file_sha256": EXECUTOR.sha256_bytes(
                    request_raw),
                "root_cleanup_request_body_sha256": request["body_sha256"],
                "durable_owner_count": 0,
                "durable_owner_status": "RETIRED",
                "durable_owner_retirement_receipt_path":
                    EXECUTOR.durable_owner_retirement_receipt_path_for(
                        validated),
                "durable_owner_retirement_receipt_file_sha256": digest(
                    "durable-owner-retirement-receipt-file"),
                "durable_owner_retirement_receipt_body_sha256": digest(
                    "durable-owner-retirement-receipt-body"),
            })
        body.update(self.root_receipt_overrides)
        return EXECUTOR.canonical_json({
            **body, "body_sha256": EXECUTOR.canonical_sha256(body)})

    def invoke(self, _tool_name: str, call_id: str, request_raw: bytes) -> bytes:
        request = EXECUTOR._load_canonical(request_raw, "TEST_REQUEST")
        call = self.calls[call_id]
        role = call["call_role"]
        self.trace.append(role)
        if role == self.fatal_role:
            raise SystemExit("injected process crash")
        status = "UNCERTAIN" if role == self.uncertain_role else "OK"
        reason = "INJECTED_UNCERTAINTY" if status != "OK" else "OK"
        epoch = "epoch-drift" if role == self.drift_role else "epoch-a"
        payload = {} if status != "OK" else self._payload(role, request)
        raw_tool_request = EXECUTOR.canonical_json({
            "tool_call_id": call_id, "tool_name": call["tool_name"],
            "arguments": request["arguments"],
        })
        raw_tool_response = EXECUTOR.canonical_json({
            "status": status.lower(), "tool": call["tool_name"],
            "reason_code": reason, "payload": payload,
        })
        response = {
            "schema": EXECUTOR.BACKEND_RESPONSE_SCHEMA, "version": 1,
            "tool_call_id": call_id, "tool_name": call["tool_name"],
            "command_id": call["command_id"],
            "tool_catalog_sha256": request["tool_catalog_sha256"],
            "tool_descriptor_sha256": request["tool_descriptor_sha256"],
            "status": status, "reason_code": reason,
            "service_epoch": epoch, "fencing_generation": 7,
            "adapter_image_sha256": next(
                item["file_sha256"] for item in self.handoff["installed_images"]
                if item["role"] == "backend-adapter"),
            "adapter_transform_version": EXECUTOR.BACKEND_TRANSFORM_VERSION,
            "raw_request_sha256": EXECUTOR.sha256_bytes(raw_tool_request),
            "raw_response_sha256": EXECUTOR.sha256_bytes(raw_tool_response),
            "normalized_payload_sha256": EXECUTOR.canonical_sha256(payload),
            "payload": payload,
        }
        if role == self.wrong_pin_role:
            response["tool_catalog_sha256"] = digest("wrong-catalog")
        return EXECUTOR.canonical_json(response)

    def _payload(self, role: str, request: dict) -> dict:
        intent = self.handoff["intent"]
        owner = self.handoff["session_owner_reference"]
        owner_account = owner["owner_account"]
        owner_domain = owner["owner_execution_domain"]
        if role == self.scope_substitution_role:
            owner_account = "DU999"
        if role in {"preflight-health", "final-health"}:
            return {
                "execution_mode": "PAPER", "paper_account": True,
                "connected": True, "authorized_connector_count": 1,
                "complete": True,
            }
        if role == "preflight-quote":
            return {
                "instrument": "EUR.USD", "symbol": "EUR", "currency": "USD",
                "sec_type": "CASH", "exchange": "IDEALPRO",
                "bid": intent["observed_bid"], "ask": intent["observed_ask"],
                "observed_at_ms": intent["observed_at_ms"],
                "authoritative": True, "complete": True,
            }
        if role in {"preflight-account", "final-account"}:
            gross = 1 if role == "final-account" and self.scenario == \
                "late-fill-account" else 0
            return {
                "account_id_sha256": digest("paper-account"),
                "account_kind": "PAPER", "authoritative": True,
                "account_complete": True, "gross_absolute_position": gross,
                "fx_cash_generation": 1,
                "owner_account": owner_account,
                "owner_execution_domain": owner_domain,
            }
        if role in {"preflight-positions", "reconcile-positions", "final-positions"}:
            positions = []
            if role == "reconcile-positions" and self.scenario in {"flatten", "both"}:
                positions = [{"instrument": "EUR.USD", "quantity": 1}]
            if role == "final-positions" and not self.flattened and self.scenario in {
                    "flatten", "both"}:
                positions = [{"instrument": "EUR.USD", "quantity": 1}]
            if role == "final-positions" and self.scenario == "late-fill-position":
                positions = [{"instrument": "EUR.USD", "quantity": 1}]
            gross = sum(abs(item["quantity"]) for item in positions)
            return {
                "authoritative": True, "complete": True,
                "snapshot_sha256": digest(role + str(self.flattened)),
                "positions": positions, "gross_absolute_position": gross,
                "position_generation": 1,
                "fx_cash_generation": (
                    2 if role == "final-positions" and
                    self.scenario == "generation-mismatch" else 1),
                "owner_account": owner_account,
                "owner_execution_domain": owner_domain,
            }
        if role in {"preflight-orders", "reconcile-orders", "final-orders"}:
            orders = []
            if role == "reconcile-orders" and self.scenario in {
                    "cancel", "both", "late-fill-account",
                    "late-fill-position", "current-gross",
                    "generation-mismatch", "scope-substitution"}:
                orders = [{
                    "order_id_sha256": self.order_id, "instrument": "EUR.USD",
                    "owned": True, "active": True,
                }]
            if role == "final-orders" and not self.cancelled and self.scenario in {
                    "cancel", "both"}:
                orders = [{
                    "order_id_sha256": self.order_id, "instrument": "EUR.USD",
                    "owned": True, "active": True,
                }]
            return {
                "authoritative": True, "complete": True,
                "snapshot_sha256": digest(role + str(self.cancelled)),
                "orders": orders,
                "connection_epoch": 1, "generation": 1,
                "owner_account": owner_account,
                "owner_execution_domain": owner_domain,
            }
        if role in {"preflight-risk", "cleanup-risk"}:
            payload = {
                "paper_only": True, "live_authorized": False,
                "max_order_quantity": "1", "max_order_notional": "5000",
                "max_orders_per_minute": 1, "max_active_orders": 1,
                "max_gross_position": "1",
                "gross_absolute_position": (
                    "1" if role == "cleanup-risk" and
                    self.scenario == "current-gross" else "0"),
                "gross_scope": "PAPER_BASELINE_DELTA",
                "connection_epoch": 1, "orders_generation": 1,
                "position_generation": 1, "fx_cash_generation": 1,
                "owner_account": owner_account,
                "owner_execution_domain": owner_domain,
                "allowed_instruments": ["EUR.USD"],
                "order_types": ["LMT"], "tifs": ["DAY"], "complete": True,
            }
            if role == "cleanup-risk":
                payload.update(self.cleanup_risk_overrides)
            return payload
        if role == "preflight-campaign":
            return {
                "state": "IDLE", "cycle_id": None, "remaining_cycles": 1,
                "authority_granted": False,
            }
        if role == "open":
            return {
                "opened": True, "cycle_id": self.handoff["cycle_id"],
                "intent_sha256": self.handoff["intent_sha256"],
                "deadline_at_ms": self.now + 20_000, "authority_granted": False,
            }
        if role == "preview-order":
            return {
                "approved": True, "cycle_id": self.handoff["cycle_id"],
                "intent_sha256": self.handoff["intent_sha256"],
                "order_request_sha256": EXECUTOR.canonical_sha256(intent),
                "authority_granted": False,
            }
        if role == "place":
            return {
                "accepted": True, "cycle_id": self.handoff["cycle_id"],
                "intent_sha256": self.handoff["intent_sha256"],
                "order_id_sha256": self.order_id, "owned": True,
                "authority_granted": False,
            }
        if role == "close":
            return {
                "closed": True, "cycle_id": self.handoff["cycle_id"],
                "intent_sha256": self.handoff["intent_sha256"],
                "outcome": request["arguments"]["outcome"],
                "authority_granted": False,
            }
        if role == "cancel-order":
            self.cancelled = True
            return {
                "cancelled": True, "order_id_sha256": self.order_id,
                "stable_cancel": True, "authority_granted": False,
            }
        if role == "preview-flatten":
            return {
                "approved": True, "instrument": "EUR.USD",
                "position_quantity": request["arguments"]["position_quantity"],
                "side": request["arguments"]["side"], "quantity": 1,
                "order_type": "LMT", "tif": "DAY",
                "limit_price": (
                    "1.15320" if request["arguments"]["side"] == "SELL"
                    else "1.15325"),
                "observed_bid": "1.15320", "observed_ask": "1.15325",
                "quote_observed_at_ms": self.now,
                "expires_at_ms": self.now + 30_000,
                "reduce_only": True, "atomic": True,
                "authority_granted": False,
            }
        if role == "flatten-position":
            self.flattened = True
            return {
                "flattened": True, "instrument": "EUR.USD",
                "position_quantity": request["arguments"]["position_quantity"],
                "side": request["arguments"]["side"], "quantity": 1,
                "order_type": "LMT", "tif": "DAY",
                "limit_price": request["arguments"]["limit_price"],
                "quote_observed_at_ms": request["arguments"][
                    "quote_observed_at_ms"],
                "expires_at_ms": request["arguments"]["expires_at_ms"],
                "reduce_only": True, "atomic": True,
                "authority_granted": False,
            }
        raise AssertionError(role)


class CanaryExecutorTests(unittest.TestCase):
    def execute(self, *, scenario: str = "cancel", **backend_options):
        document = handoff_document()
        backend = FakeBackend(document, scenario=scenario, **backend_options)
        result = EXECUTOR.execute(EXECUTOR.canonical_json(document), backend)
        return document, backend, result

    def test_cancelled_flat_happy_path_has_valid_v3_and_strict_v2(self):
        document, backend, result = self.execute(scenario="cancel")
        self.assertEqual(result.status, "SUCCESS")
        self.assertFalse(result.authority_granted)
        self.assertTrue(result.v2_compatible)
        self.assertEqual(backend.trace.count("place"), 1)
        self.assertEqual(backend.trace.count("close"), 1)
        self.assertEqual(backend.trace.count("cancel-order"), 1)
        self.assertEqual(
            backend.trace[backend.trace.index("place") + 1], "close")
        self.assertNotIn("execution.get_command_status", [
            call["tool_name"] for call in document["tool_calls"]])
        self.assertIn("receipt-v3.json", result.artifacts)
        self.assertIn("receipt-v2-compat.json", result.artifacts)
        self.assertNotIn("recovery-record-v1.json", result.artifacts)
        cross = EXECUTOR.validate_cross_binding(
            result.artifacts["receipt-v2-v3-cross-binding.json"],
            handoff_raw=EXECUTOR.canonical_json(document),
            artifacts=result.artifacts)
        self.assertEqual(
            cross["v2"]["receipt_payload_sha256"],
            cross["v3"]["receipt_payload_sha256"])

    def test_normal_cleanup_v4_contract_and_long_wal_window_are_exact(self):
        document = handoff_document()
        document["issued_at_ms"] = 750_000
        document["expires_at_ms"] = 1_050_000
        reseal(document)
        backend = FakeBackend(document)
        result = EXECUTOR.execute(EXECUTOR.canonical_json(document), backend)
        self.assertEqual(result.status, "SUCCESS")
        request = EXECUTOR._load_canonical(
            backend.checkpoint["root-cleanup-request.v1.json"],
            "TEST_ROOT_CLEANUP_REQUEST")
        self.assertLess(request["issued_at_ms"], document["expires_at_ms"])
        self.assertEqual(
            request["expires_at_ms"] - request["issued_at_ms"], 240_000)
        self.assertGreater(request["expires_at_ms"], document["expires_at_ms"])
        self.assertEqual(
            request["required_actions"], list(EXECUTOR.ROOT_CLEANUP_ACTIONS))
        self.assertEqual(len(request["required_actions"]), 8)
        self.assertEqual(EXECUTOR.ROOT_CLEANUP_DESCRIPTOR["timeout_ms"], 240_000)
        self.assertEqual(EXECUTOR.POLICY_WINDOW_MS, 300_000)
        self.assertEqual(EXECUTOR.MAX_INTENT_HORIZON_MS, 60_000)
        self.assertIn("root-cleanup-receipt.v4.json", result.artifacts)
        self.assertNotIn("root-cleanup-receipt.v1.json", result.artifacts)
        self.assertNotIn("root-cleanup-receipt.v2.json", result.artifacts)
        validated = EXECUTOR.validate_handoff(
            EXECUTOR.canonical_json(document), require_fresh=False)
        self.assertTrue(
            EXECUTOR.root_cleanup_receipt_path_for(validated).endswith(
                "/root-cleanup-receipt.v4.json"))
        self.assertTrue(
            EXECUTOR.durable_owner_retirement_receipt_path_for(
                validated).endswith(
                    "/durable-owner-retirement-receipt.v4.json"))

    def test_old_normal_receipt_and_absence_proofs_fail_closed(self):
        cases = (
            {
                "schema": "hepta.p1-paper-canary-root-cleanup-receipt.v1",
                "version": 1,
            },
            {
                "schema": "hepta.p1-paper-canary-root-cleanup-receipt.v2",
                "version": 2,
            },
            {
                "schema": "hepta.p1-paper-canary-root-cleanup-receipt.v3",
                "version": 3,
            },
            {"durable_owner_status": "ABSENT"},
            {"durable_owner_status": "GENERATION_ABSENT_AFTER_REVOKE"},
            {"completed_actions": list(
                EXECUTOR.ROOT_EMERGENCY_CLEANUP_ACTIONS)},
        )
        for overrides in cases:
            with self.subTest(overrides=overrides):
                _document, _backend, result = self.execute(
                    root_receipt_overrides=overrides)
                self.assertEqual(result.status, "RECOVERY_REQUIRED")
                self.assertFalse(result.v2_compatible)
                self.assertNotIn(
                    "root-cleanup-receipt.v4.json", result.artifacts)

    def test_emergency_cleanup_stays_v1_with_five_actions(self):
        document = handoff_document()
        backend = FakeBackend(document, fatal_role="place")
        with self.assertRaises(SystemExit):
            EXECUTOR.execute(EXECUTOR.canonical_json(document), backend)
        backend.fatal_role = None
        clock_calls = 0

        def late_recovery_clock() -> int:
            nonlocal clock_calls
            clock_calls += 1
            if clock_calls == 1:
                # Recovery admission itself still begins while the intent is
                # fresh.  Cleanup is delayed close to handoff expiry.
                return document["issued_at_ms"] + 2_000
            return document["expires_at_ms"] - 10_000 + clock_calls

        backend.now_ms = late_recovery_clock
        result = EXECUTOR.execute(EXECUTOR.canonical_json(document), backend)
        self.assertEqual(result.status, "RECOVERY_REQUIRED")
        request = EXECUTOR._load_canonical(
            backend.checkpoint["root-emergency-cleanup-request.v1.json"],
            "TEST_ROOT_EMERGENCY_REQUEST")
        receipt = EXECUTOR._load_canonical(
            result.artifacts["root-emergency-cleanup-receipt.v1.json"],
            "TEST_ROOT_EMERGENCY_RECEIPT")
        self.assertEqual(request["version"], 1)
        self.assertEqual(receipt["version"], 1)
        self.assertEqual(
            request["required_actions"],
            list(EXECUTOR.ROOT_EMERGENCY_CLEANUP_ACTIONS))
        self.assertEqual(
            request["expires_at_ms"] - request["issued_at_ms"], 45_000)
        self.assertGreater(
            request["expires_at_ms"], document["expires_at_ms"])
        self.assertEqual(len(receipt["completed_actions"]), 5)

    def test_backend_uses_mode_specific_timeout_and_rejects_old_normal_versions(
            self):
        document = handoff_document()
        validated = EXECUTOR.validate_handoff(
            EXECUTOR.canonical_json(document), now_ms=1_000_000)

        class Channel:
            def __init__(self, response: bytes):
                self.response = response
                self.timeouts: list[int] = []

            def settimeout(self, value: int) -> None:
                self.timeouts.append(value)

            def connect(self, _path: str) -> None:
                pass

            def sendall(self, _request: bytes) -> None:
                pass

            def shutdown(self, _direction: int) -> None:
                pass

            def recv(self, _maximum: int) -> bytes:
                response, self.response = self.response, b""
                return response

            def close(self) -> None:
                pass

        def backend_for() -> object:
            backend = object.__new__(BACKEND_ADAPTER.ProductionBackend)
            backend._root_cleanup_receipt_raw = None
            backend._handoff = validated
            backend._images = {"root-finalizer": {}}
            return backend

        call = document["root_cleanup_call"]
        for emergency, expected_timeout in ((False, 240), (True, 45)):
            schema = (
                EXECUTOR.ROOT_EMERGENCY_CLEANUP_REQUEST_SCHEMA if emergency
                else EXECUTOR.ROOT_CLEANUP_REQUEST_SCHEMA)
            response_schema = (
                EXECUTOR.ROOT_EMERGENCY_CLEANUP_RECEIPT_SCHEMA if emergency
                else EXECUTOR.ROOT_CLEANUP_RECEIPT_SCHEMA)
            response_version = (
                1 if emergency else EXECUTOR.ROOT_CLEANUP_RECEIPT_VERSION)
            status = (
                "ROOT_EMERGENCY_CLEANUP_COMPLETE_DENY_ALL" if emergency else
                "ROOT_CLEANUP_COMPLETE_DENY_ALL")
            request = EXECUTOR.canonical_json({
                "schema": schema,
                "cleanup_tool_call_id": call["tool_call_id"],
                "cleanup_command_id": call["command_id"],
            })
            response = EXECUTOR.canonical_json({
                "schema": response_schema, "version": response_version,
                "status": status,
            })
            channel = Channel(response)
            with mock.patch.object(BACKEND_ADAPTER, "_stable_image"), \
                    mock.patch.object(
                        BACKEND_ADAPTER.socket, "socket", return_value=channel):
                BACKEND_ADAPTER.ProductionBackend.finalize_root_cleanup(
                    backend_for(), request)
            self.assertEqual(channel.timeouts, [expected_timeout])

        normal_request = EXECUTOR.canonical_json({
            "schema": EXECUTOR.ROOT_CLEANUP_REQUEST_SCHEMA,
            "cleanup_tool_call_id": call["tool_call_id"],
            "cleanup_command_id": call["command_id"],
        })
        for version in (1, 2):
            with self.subTest(version=version):
                old_response = EXECUTOR.canonical_json({
                    "schema": (
                        "hepta.p1-paper-canary-root-cleanup-receipt."
                        f"v{version}"),
                    "version": version,
                    "status": "ROOT_CLEANUP_COMPLETE_DENY_ALL",
                })
                old_channel = Channel(old_response)
                with mock.patch.object(BACKEND_ADAPTER, "_stable_image"), \
                        mock.patch.object(
                            BACKEND_ADAPTER.socket, "socket",
                            return_value=old_channel):
                    with self.assertRaisesRegex(
                            BACKEND_ADAPTER.AdapterError,
                            "ADAPTER_ROOT_CLEANUP_RECEIPT_INVALID"):
                        BACKEND_ADAPTER.ProductionBackend.finalize_root_cleanup(
                            backend_for(), normal_request)

    def test_filled_position_uses_atomic_reduce_only_flatten(self):
        _document, backend, result = self.execute(scenario="flatten")
        self.assertEqual(result.status, "SUCCESS")
        self.assertTrue(result.v2_compatible)
        self.assertNotIn("cancel-order", backend.trace)
        self.assertEqual(backend.trace.count("preview-flatten"), 1)
        self.assertEqual(backend.trace.count("flatten-position"), 1)
        receipt = EXECUTOR._load_canonical(
            result.artifacts["receipt-v3.json"], "TEST_RECEIPT")
        self.assertEqual(receipt["payload"]["final_outcome"], "FILLED_AND_FLAT")
        self.assertEqual(receipt["payload"]["final_authoritative_state"][
            "gross_absolute_position"], 0)

    def test_both_owned_order_and_exact_position_reduces_both_without_v2(self):
        _document, backend, result = self.execute(scenario="both")
        self.assertEqual(result.status, "SUCCESS")
        self.assertFalse(result.v2_compatible)
        self.assertEqual(backend.trace.count("cancel-order"), 1)
        self.assertEqual(backend.trace.count("flatten-position"), 1)
        self.assertNotIn("receipt-v2-compat.json", result.artifacts)
        receipt = EXECUTOR._load_canonical(
            result.artifacts["receipt-v3.json"], "TEST_RECEIPT")
        self.assertEqual(receipt["payload"]["final_outcome"], "RECOVERED")

    def test_duplicate_noncanonical_float_and_unknown_handoffs_fail(self):
        document = handoff_document()
        raw = EXECUTOR.canonical_json(document)
        malformed = [
            raw.replace(b'"schema":', b'"schema":"duplicate","schema":', 1),
            b" " + raw,
            raw.replace(b'"quantity":1', b'"quantity":1.0', 1),
        ]
        unknown = deepcopy(document)
        unknown["unknown"] = True
        malformed.append(reseal(unknown))
        for candidate in malformed:
            with self.subTest(candidate=candidate[:40]):
                with self.assertRaises(EXECUTOR.CanaryContractError):
                    EXECUTOR.validate_handoff(candidate, now_ms=1_000_000)

    def test_wrong_pins_mkt_quantity_and_live_are_rejected(self):
        cases = []
        wrong_descriptor = handoff_document()
        wrong_descriptor["tool_calls"][0]["tool_descriptor_sha256"] = digest("wrong")
        cases.append(wrong_descriptor)
        mkt = handoff_document()
        mkt["intent"]["order_type"] = "MKT"
        mkt["intent_sha256"] = EXECUTOR.canonical_sha256(mkt["intent"])
        cases.append(mkt)
        quantity = handoff_document()
        quantity["intent"]["quantity"] = 2
        quantity["intent_sha256"] = EXECUTOR.canonical_sha256(quantity["intent"])
        cases.append(quantity)
        live = handoff_document()
        live["live_authorized"] = True
        cases.append(live)
        for document in cases:
            with self.subTest(order=document["intent"]["order_type"],
                              quantity=document["intent"]["quantity"],
                              live=document["live_authorized"]):
                with self.assertRaises(EXECUTOR.CanaryContractError):
                    EXECUTOR.validate_handoff(reseal(document), now_ms=1_000_000)

    def test_intent_notional_and_holding_boundaries_are_exact(self):
        boundary = handoff_document()
        boundary["intent"].update({
            "observed_bid": "4999.99999999",
            "observed_ask": "5000",
            "limit_price": "5000",
            "max_holding_ms": 1,
        })
        boundary["intent_sha256"] = EXECUTOR.canonical_sha256(
            boundary["intent"])
        EXECUTOR.validate_handoff(reseal(boundary), now_ms=1_000_000)

        over = deepcopy(boundary)
        over["intent"].update({
            "observed_ask": "5000.00000001",
            "limit_price": "5000.00000001",
        })
        over["intent_sha256"] = EXECUTOR.canonical_sha256(over["intent"])
        with self.assertRaisesRegex(
                EXECUTOR.CanaryContractError,
                "HANDOFF_INTENT_NOTIONAL_INVALID"):
            EXECUTOR.validate_handoff(reseal(over), now_ms=1_000_000)

        zero_hold = deepcopy(boundary)
        zero_hold["intent"]["max_holding_ms"] = 0
        zero_hold["intent_sha256"] = EXECUTOR.canonical_sha256(
            zero_hold["intent"])
        with self.assertRaisesRegex(
                EXECUTOR.CanaryContractError,
                "HANDOFF_INTENT_HOLDING_INVALID"):
            EXECUTOR.validate_handoff(reseal(zero_hold), now_ms=1_000_000)

    def test_runtime_profile_and_decimal_numbers_are_exact_and_canonical(self):
        accepted = BACKEND_ADAPTER._profile_fields(exact_profile())
        self.assertEqual(accepted["HEPTA_IB_PAPER_ACCOUNT"], "DU123")
        profile_cases = (
            exact_profile(HEPTA_IB_PAPER_MAX_ORDER_QTY="2"),
            exact_profile(HEPTA_IB_PAPER_MAX_ORDER_QTY="0"),
            exact_profile(HEPTA_IB_PAPER_MAX_ORDER_QTY="-1"),
            exact_profile(HEPTA_IB_PAPER_MAX_ORDER_QTY="1.0"),
            exact_profile(HEPTA_IB_PAPER_QUOTE_MAX_AGE_MS="5001"),
            exact_profile(HEPTA_IB_PAPER_CLIENT_ID="702"),
            exact_profile(HEPTA_IB_EXECUTION_GATEWAY_UID="2102"),
            exact_profile(HEPTA_IB_EXECUTION_GATEWAY_AGENT_ID="beta"),
            exact_profile(HEPTA_IB_EXECUTION_DOMAIN_ID="PAPER:alpha"),
            exact_profile(HEPTA_IB_EXECUTION_MAX_REQUEST_BYTES="16385"),
            exact_profile(HEPTA_IB_EXECUTION_IO_TIMEOUT_MS="2501"),
            exact_profile(HEPTA_IB_EXECUTION_READINESS_TIMEOUT_MS="30001"),
            exact_profile(HEPTA_IB_EXECUTION_RECONNECT_TIMEOUT_MS="180001"),
            exact_profile() + b"EXTRA_KEY=1\n",
            b"\n" + exact_profile(),
        )
        for raw in profile_cases:
            with self.subTest(raw=raw[:100]), self.assertRaises(
                    BACKEND_ADAPTER.AdapterError):
                BACKEND_ADAPTER._profile_fields(raw)

        for token in (b"1.0", b"1e0", b"-0.0"):
            with self.subTest(token=token):
                value = BACKEND_ADAPTER._strict_json(
                    b'{"value":' + token + b'}\n', "TEST", decimals=True)[
                        "value"]
                with self.assertRaises(BACKEND_ADAPTER.AdapterError):
                    BACKEND_ADAPTER._canonical_nonnegative_decimal(
                        value, "TEST_DECIMAL_INVALID", expected="1")
        with self.assertRaises(BACKEND_ADAPTER.AdapterError):
            BACKEND_ADAPTER._strict_json(
                b'{"value":NaN}\n', "TEST", decimals=True)

    def test_backend_constructor_binds_exact_profile_account_and_domain(self):
        document = handoff_document()
        document["session_owner_reference"]["owner_account"] = "DU999"
        validated = EXECUTOR.validate_handoff(reseal(document), now_ms=1_000_000)

        def stable(path, **_parameters):
            if str(path) == document["session_owner_reference"]["token_path"]:
                return b"token"
            return exact_profile()

        with mock.patch.object(BACKEND_ADAPTER.os, "geteuid", return_value=2104), \
                mock.patch.object(BACKEND_ADAPTER.os, "getegid", return_value=2104), \
                mock.patch.object(BACKEND_ADAPTER, "_stable_read",
                                  side_effect=stable), \
                mock.patch.object(BACKEND_ADAPTER, "_stable_image"), \
                mock.patch.object(BACKEND_ADAPTER, "_ensure_private_directory"):
            with self.assertRaisesRegex(
                    BACKEND_ADAPTER.AdapterError,
                    "ADAPTER_SESSION_OWNER_SCOPE_MISMATCH"):
                BACKEND_ADAPTER.ProductionBackend(EXECUTOR, validated)

    def test_post_fence_late_fill_scope_and_generation_drift_fail_closed(self):
        cases = (
            ("late-fill-account", {}),
            ("late-fill-position", {}),
            ("generation-mismatch", {}),
            ("scope-substitution", {
                "scope_substitution_role": "final-orders"}),
        )
        for scenario, options in cases:
            with self.subTest(scenario=scenario):
                _document, backend, result = self.execute(
                    scenario=scenario, **options)
                self.assertEqual(result.status, "RECOVERY_REQUIRED")
                self.assertFalse(result.v2_compatible)
                self.assertIn("recovery-record-v1.json", result.artifacts)
                self.assertNotIn("receipt-v2-compat.json", result.artifacts)
                receipt = EXECUTOR._load_canonical(
                    result.artifacts["receipt-v3.json"], "TEST_RECEIPT")
                self.assertEqual(
                    receipt["payload"]["final_outcome"], "RECOVERY_REQUIRED")
                self.assertFalse(receipt["payload"]["cleanup_complete"])
                self.assertIn("root-cleanup", backend.trace)
                self.assertLess(
                    backend.trace.index("close"),
                    backend.trace.index("final-orders"))

    def test_nonzero_or_drifted_cleanup_risk_never_publishes_success(self):
        cases = (
            ("current-gross", {}),
            ("cancel", {"max_gross_position": "2"}),
            ("cancel", {"max_gross_position": "0.5"}),
            ("cancel", {"max_gross_position": "-1"}),
            ("cancel", {"max_order_notional": "5000.0"}),
            ("cancel", {"max_orders_per_minute": 2}),
        )
        for scenario, risk_override in cases:
            with self.subTest(scenario=scenario, override=risk_override):
                _document, backend, result = self.execute(
                    scenario=scenario,
                    cleanup_risk_overrides=risk_override)
                self.assertEqual(result.status, "RECOVERY_REQUIRED")
                self.assertFalse(result.v2_compatible)
                self.assertNotIn("receipt-v2-compat.json", result.artifacts)
                self.assertIn("recovery-record-v1.json", result.artifacts)
                receipt = EXECUTOR._load_canonical(
                    result.artifacts["receipt-v3.json"], "TEST_RECEIPT")
                self.assertEqual(
                    receipt["payload"]["final_outcome"], "RECOVERY_REQUIRED")
                self.assertFalse(receipt["payload"]["cleanup_complete"])
                self.assertEqual(backend.trace.count("root-cleanup"), 1)

    def test_stale_quote_fails_before_any_backend_call(self):
        document = handoff_document()
        document["intent"]["observed_at_ms"] = 994_000
        document["intent"]["expires_at_ms"] = 1_044_000
        document["intent_sha256"] = EXECUTOR.canonical_sha256(document["intent"])
        backend = FakeBackend(handoff_document())
        with self.assertRaisesRegex(
                EXECUTOR.CanaryContractError, "HANDOFF_INTENT_STALE"):
            EXECUTOR.execute(reseal(document), backend)
        self.assertEqual(backend.trace, [])

    def test_uncertainty_at_each_stage_seals_recovery_without_v2_or_retry(self):
        for role, scenario in (
                ("preflight-health", "cancel"), ("open", "cancel"),
                ("preview-order", "cancel"), ("place", "cancel"),
                ("close", "cancel"), ("reconcile-orders", "cancel"),
                ("cancel-order", "cancel"), ("preview-flatten", "flatten"),
                ("flatten-position", "flatten"), ("final-health", "cancel")):
            with self.subTest(role=role):
                document, backend, result = self.execute(
                    scenario=scenario, uncertain_role=role)
                self.assertEqual(result.status, "RECOVERY_REQUIRED")
                self.assertFalse(result.v2_compatible)
                self.assertIn("recovery-record-v1.json", result.artifacts)
                self.assertNotIn("receipt-v2-compat.json", result.artifacts)
                self.assertLessEqual(backend.trace.count("place"), 1)
                self.assertLessEqual(backend.trace.count("close"), 1)
                self.assertLessEqual(backend.trace.count("cancel-order"), 1)
                self.assertLessEqual(backend.trace.count("flatten-position"), 1)
                recovery = EXECUTOR.validate_recovery_record(
                    result.artifacts["recovery-record-v1.json"],
                    handoff_raw=EXECUTOR.canonical_json(document),
                    journal_snapshot=backend.reopen_journal())
                self.assertFalse(recovery["authority_granted"])

    def test_epoch_drift_and_wrong_response_pins_fail_closed(self):
        for option in (
                {"drift_role": "preview-order"},
                {"wrong_pin_role": "preflight-account"}):
            with self.subTest(option=option):
                _document, backend, result = self.execute(**option)
                self.assertEqual(result.status, "RECOVERY_REQUIRED")
                self.assertFalse(result.v2_compatible)
                self.assertLessEqual(backend.trace.count("place"), 1)

    def test_crash_journal_is_never_replayed(self):
        document = handoff_document()
        backend = FakeBackend(document, fatal_role="place")
        with self.assertRaises(SystemExit):
            EXECUTOR.execute(EXECUTOR.canonical_json(document), backend)
        self.assertEqual(backend.trace.count("place"), 1)
        backend.fatal_role = None
        before = list(backend.trace)
        result = EXECUTOR.execute(EXECUTOR.canonical_json(document), backend)
        self.assertEqual(result.status, "RECOVERY_REQUIRED")
        self.assertEqual(backend.trace, [*before, "root-cleanup"])
        self.assertEqual(backend.trace.count("place"), 1)
        self.assertIn(
            "root-emergency-cleanup-receipt.v1.json", result.artifacts)
        self.assertIn("recovery-record-v1.json", result.artifacts)
        recovery = EXECUTOR.validate_recovery_record(
            result.artifacts["recovery-record-v1.json"],
            handoff_raw=EXECUTOR.canonical_json(document),
            journal_snapshot=backend.reopen_journal())
        self.assertTrue(recovery["place_attempted"])
        self.assertFalse(recovery["close_attempted"])

    def test_v2_validator_is_exact_historical_blob(self):
        raw = V2_SOURCE.read_bytes()
        self.assertEqual(len(raw), 49_870)
        self.assertEqual(
            hashlib.sha256(raw).hexdigest(), EXECUTOR.HISTORICAL_V2_RAW_SHA256)
        self.assertEqual(EXECUTOR.HISTORICAL_V2_BLOB,
                         "b854aa90eab1cabe8742c99d09253bd337c09613")
        self.assertIn(b'TOOL_POLICY_VERSION = 1', raw)
        self.assertNotIn(b'execution.get_command_status', raw)

    def test_source_executor_has_no_direct_host_or_broker_surface(self):
        text = SOURCE.read_text(encoding="utf-8")
        for forbidden in (
                "import subprocess", "import socket", "EClientSocket",
                "placeOrder(", "ReqOrderInsert", "/usr/bin/systemctl",
                "password", "private_key"):
            self.assertNotIn(forbidden, text)
        self.assertIn("class InjectedBackend(Protocol)", text)
        self.assertIn('"authority_granted": False', text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
