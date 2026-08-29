#!/usr/bin/env python3

from __future__ import annotations

from contextlib import ExitStack
from copy import deepcopy
import hashlib
import importlib.util
import os
from pathlib import Path
from pathlib import PurePosixPath
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "scripts/hepta_p1_paper_canary_root_finalizer.py"
SERVICE = ROOT / "systemd/hepta-p1-paper-canary-finalizer@.service"
SOCKET = ROOT / "systemd/hepta-p1-paper-canary-finalizer.socket"


def load_module():
    spec = importlib.util.spec_from_file_location("hepta_root_finalizer_test", SOURCE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


FINALIZER = load_module()


def load_terminal_prover():
    source = ROOT / "scripts/hepta_p1_paper_canary_terminal_prover.py"
    spec = importlib.util.spec_from_file_location(
        "hepta_terminal_prover_finalizer_test", source)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


TERMINAL = load_terminal_prover()


def load_executor_fixtures():
    source = ROOT / "tests/hepta_p1_paper_canary_executor_tests.py"
    spec = importlib.util.spec_from_file_location(
        "hepta_root_finalizer_executor_fixtures", source)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


EXECUTOR_FIXTURES = load_executor_fixtures()


def digest(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode("ascii")).hexdigest()


def hpe1_from_receipt_values(values):
    """Build the exact independent HPE1 bytes represented by HSL8 values."""
    evidence = {
        key: values[key] for key in FINALIZER.TERMINAL_EVIDENCE_KEYS
        if key != "evidence_body_sha256"
    }
    evidence.update({
        "schema": "hepta.paper-terminal-witness-evidence.v1",
        "version": "1",
        "status": "CURRENT_POST_CUTOFF_TERMINAL_WITNESS_VERIFIED",
    })
    prefix = b"HPE1\n" + b"".join(
        f"{key}={evidence[key]}\n".encode("ascii")
        for key in FINALIZER.TERMINAL_EVIDENCE_KEYS[:-1])
    body = FINALIZER.sha(prefix)
    raw = prefix + f"evidence_body_sha256={body}\n".encode("ascii")
    return raw, body


def hpe1_from_retirement(path: Path):
    retirement = FINALIZER.strict_json(path.read_bytes(), "TEST")
    receipt, _raw = FINALIZER._parse_terminal_ack_receipt(
        retirement["terminal_ack_receipt"])
    return hpe1_from_receipt_values(receipt)[0]


def recovery_query_result(handoff, finalization):
    owner = handoff["session_owner_reference"]
    return {
        "accepted": True,
        "reason_code": "RECOVERY_QUERY_NOT_FOUND_PROVEN_RECOVERY_ONLY",
        "lease_generation": owner["lease_generation"],
        "authoritative_command_status": True,
        "command_id": finalization["query_command_id"],
        "command_status": "not_found",
        "command_reason_code": "EXECUTION_COMMAND_NOT_FOUND",
        "order_id": -1, "recovery_only": True,
        "paper_finalization_required": True, "owner_fenced": False,
        "execution_service_epoch": handoff["execution_service_epoch"],
        "execution_service_fencing_generation": handoff[
            "execution_service_fencing_generation"],
        "recovery_expires_at_ms": 9_000_000_000_000_000,
        "owner_audit_authoritative": True, "owner_audit_complete": True,
        "owner_active_order_count": 0, "owner_uncertain_command_count": 0,
        "broker_connection_epoch": 1, "broker_active_generation": 1,
        "broker_terminal_generation": 1,
        "owner_account": owner["owner_account"],
        "owner_execution_domain": owner["owner_execution_domain"],
    }


def finalization_result(handoff, finalization, state):
    if state != "AUDIT_SEALED":
        raise AssertionError("preliminary result must remain AUDIT_SEALED")
    owner = handoff["session_owner_reference"]
    receipt_values = {
        "schema": "hepta.paper-session-finalization-receipt.v1",
        "version": "1", "status": "AUDIT_SEALED",
        "recovery_id": finalization["recovery_id"],
        "finalization_id": finalization["finalization_id"],
        "expected_owner_set_sha256": finalization[
            "expected_owner_set_sha256"],
        "expected_owner_count": "1",
        "owner_set_canonical_hex": finalization["owner_set_canonical_hex"],
        "owner_account": owner["owner_account"],
        "owner_execution_domain": owner["owner_execution_domain"],
        "execution_service_epoch": handoff["execution_service_epoch"],
        "execution_service_fencing_generation": str(handoff[
            "execution_service_fencing_generation"]),
        "broker_connection_epoch": "1", "broker_active_generation": "1",
        "broker_terminal_generation": "1", "broker_risk_generation": "1",
        "broker_account_generation": "1",
        "broker_position_generation": "1",
        "broker_fx_cash_generation": "1",
        "broker_exposure_generation": "0",
        "broker_terminal_exposure_generation": "0",
        "broker_risk_absorbed_exposure_generation": "0",
        "broker_global_active_order_count": "0",
        "owner_active_order_count": "0",
        "owner_uncertain_command_count": "0",
        "broker_post_fill_risk_reconciliation_pending": "0",
        "broker_recovery_audit_barrier_complete": "1",
        "broker_recovery_audit_new_connection_epoch_required": "0",
        "broker_position_quantity": "0",
        "broker_gross_absolute_position": "0", "paper_only": "1",
        "live_authorized": "0",
    }
    receipt = "".join(
        f"{key}={receipt_values[key]}\n"
        for key in FINALIZER.FINALIZATION_RECEIPT_KEYS)
    receipt_raw = receipt.encode("ascii")
    return {
        "accepted": True,
        "reason_code": "PAPER_FINALIZATION_AUDIT_SEALED",
        "lease_generation": owner["lease_generation"],
        "paper_finalization_state": state,
        "paper_finalization_required": True,
        "recovery_id": finalization["recovery_id"],
        "finalization_id": finalization["finalization_id"],
        "expected_owner_set_sha256": finalization[
            "expected_owner_set_sha256"],
        "expected_owner_count": 1,
        "owner_token_sha256": owner["token_sha256"],
        "finalization_receipt_sha256": FINALIZER.sha(receipt_raw),
        "finalization_receipt": receipt,
        "owner_audit_authoritative": True, "owner_audit_complete": True,
        "owner_active_order_count": 0, "owner_uncertain_command_count": 0,
        "owner_account": owner["owner_account"],
        "owner_execution_domain": owner["owner_execution_domain"],
        "execution_service_epoch": handoff["execution_service_epoch"],
        "execution_service_fencing_generation": handoff[
            "execution_service_fencing_generation"],
        "broker_connection_epoch": 1, "broker_active_generation": 1,
        "broker_terminal_generation": 1, "broker_risk_generation": 1,
        "broker_account_generation": 1, "broker_position_generation": 1,
        "broker_fx_cash_generation": 1, "broker_exposure_generation": 0,
        "broker_terminal_exposure_generation": 0,
        "broker_risk_absorbed_exposure_generation": 0,
        "broker_global_active_order_count": 0,
        "broker_post_fill_risk_reconciliation_pending": False,
        "broker_recovery_audit_barrier_complete": True,
        "broker_recovery_audit_new_connection_epoch_required": False,
        "broker_position_quantity": "0",
        "broker_gross_absolute_position": "0",
    }


def terminal_ack_result(
        handoff, finalization, preliminary, *, replay=False,
        receipt_overrides=None, result_overrides=None):
    owner = handoff["session_owner_reference"]
    values = {
        "schema": "hepta.paper-session-terminal-ack-receipt.v3",
        "version": "3", "status": "TERMINAL_ACKED",
        "terminal_proof_kind":
            "POST_CUTOFF_SIGNED_ACCOUNT_WITNESS_V1",
        "recovery_id": finalization["recovery_id"],
        "finalization_id": finalization["finalization_id"],
        "campaign_id": handoff["campaign_id"],
        "cycle_id": handoff["cycle_id"],
        "expected_owner_set_sha256": finalization[
            "expected_owner_set_sha256"],
        "expected_owner_count": "1",
        "owner_set_canonical_hex": finalization["owner_set_canonical_hex"],
        "preliminary_finalization_receipt_sha256": preliminary[
            "finalization_receipt_sha256"],
        "owner_agent_id": "hepta-agent-alpha",
        "owner_session_id": owner["session_id"],
        "owner_account": owner["owner_account"],
        "owner_execution_domain": owner["owner_execution_domain"],
        "account_id_sha256": FINALIZER.sha(
            owner["owner_account"].encode("ascii")),
        "execution_service_epoch": handoff["execution_service_epoch"],
        "execution_service_fencing_generation": str(handoff[
            "execution_service_fencing_generation"]),
        "recovery_ingress_fence": str(owner["lease_generation"]),
        "terminalization_generation": "1",
        "terminalizing_latch_sha256": digest("terminal-latch"),
        "terminal_external_halt_latch_sha256": digest("external-latch"),
        "transport_cutoff_receipt_file_sha256": digest("cutoff-file"),
        "transport_cutoff_receipt_body_sha256": digest("cutoff-body"),
        "post_cutoff_terminal_witness_file_sha256": digest("witness-file"),
        "post_cutoff_terminal_witness_body_sha256": digest("witness-body"),
        "provider_trust_policy_file_sha256": digest("trust-file"),
        "provider_trust_policy_body_sha256": digest("trust-body"),
        "provider_id": "reviewed-provider-test",
        "provider_capability":
            "ACCOUNT_WIDE_ATOMIC_OR_CAUSAL_POST_CUTOFF_READ_ONLY_V1",
        "signed_account_payload_sha256": digest("signed-payload"),
        "signed_account_signature_sha256": digest("signed-signature"),
        "host_boot_id": "11111111-1111-1111-1111-111111111111",
        "egress_publisher_pid": "4102",
        "egress_publisher_start_ticks": "99123",
        "egress_policy_generation": "23",
        "egress_policy_sha256": digest("egress-policy"),
        "query_started_after_challenge": "1", "observed_after_cutoff": "1",
        "snapshot_consistency": "CAUSAL_WATERMARK",
        "causal_watermark_dominates_cutoff": "1",
        "causal_watermark_dominates_all_mutations": "1",
        "account_queries_complete": "1", "active_orders_complete": "1",
        "completed_orders_complete": "1", "executions_complete": "1",
        "positions_complete": "1", "cash_fx_complete": "1",
        "risk_complete": "1",
        "known_mutation_command_set_sha256": digest("known-mutations"),
        "known_mutation_command_count": "1",
        "known_correlation_set_sha256": digest("known-correlations"),
        "known_correlation_count": "1",
        "all_known_mutation_commands_settled": "1",
        "settled_mutation_command_count": "1",
        "unknown_mutation_command_count": "0",
        "unresolved_mutation_command_count": "0",
        "unknown_active_order_count": "0", "active_order_count": "0",
        "position_count": "0", "nonzero_cash_fx_count": "0",
        "gross_absolute_position": "0", "gross_fx_exposure": "0",
        "gross_risk": "0", "mutation_connector_count": "0",
        "broker_socket_count": "0", "broker_process_count": "0",
        "broker_credential_count": "0", "execution_service_inactive": "1",
        "paper_units_inactive": "1",
        "execution_mutation_gate_closed": "1",
        "broker_transport_connected": "0",
        "broker_reconnect_permitted": "0", "read_only_authority": "1",
        "mutation_attempted": "0", "paper_authorized": "0",
        "live_authorized": "0", "mutation_authorized": "0",
        "direct_broker_access": "0", "order_submission_authorized": "0",
        "order_authorized": "0", "paper_only": "1",
        "authority_granted": "0",
        "terminal_external_halt_latch_durable": "1",
        "terminal_witness_durable": "1",
        "current_host_boundary_verified": "1",
        "terminal_evidence_file_sha256": digest("terminal-evidence-file"),
        "terminal_evidence_body_sha256": digest("terminal-evidence-body"),
    }
    if receipt_overrides is not None:
        values.update(receipt_overrides)
    terminal_evidence_raw, terminal_evidence_body = hpe1_from_receipt_values(
        values)
    values["terminal_evidence_file_sha256"] = FINALIZER.sha(
        terminal_evidence_raw)
    values["terminal_evidence_body_sha256"] = terminal_evidence_body
    receipt = "".join(
        f"{key}={values[key]}\n"
        for key in FINALIZER.TERMINAL_ACK_RECEIPT_KEYS)
    receipt_raw = receipt.encode("ascii")
    result = {
        "accepted": True,
        "reason_code": "PAPER_FINALIZATION_TERMINAL_ACKED",
        "lease_generation": owner["lease_generation"],
        "paper_finalization_state": "ACKED",
        "paper_finalization_required": True,
        "recovery_id": finalization["recovery_id"],
        "finalization_id": finalization["finalization_id"],
        "expected_owner_set_sha256": finalization[
            "expected_owner_set_sha256"],
        "expected_owner_count": 1,
        "owner_token_sha256": owner["token_sha256"],
        "finalization_receipt_sha256": FINALIZER.sha(receipt_raw),
        "finalization_receipt": receipt,
        "owner_audit_authoritative": True, "owner_audit_complete": True,
        "owner_active_order_count": 0, "owner_uncertain_command_count": 0,
        "owner_account": owner["owner_account"],
        "owner_execution_domain": owner["owner_execution_domain"],
        "execution_service_epoch": handoff["execution_service_epoch"],
        "execution_service_fencing_generation": handoff[
            "execution_service_fencing_generation"],
        "broker_connection_epoch": 0, "broker_active_generation": 0,
        "broker_terminal_generation": 0, "broker_risk_generation": 0,
        "broker_account_generation": 0, "broker_position_generation": 0,
        "broker_fx_cash_generation": 0, "broker_exposure_generation": 0,
        "broker_terminal_exposure_generation": 0,
        "broker_risk_absorbed_exposure_generation": 0,
        "broker_global_active_order_count": 0,
        "broker_post_fill_risk_reconciliation_pending": False,
        "broker_recovery_audit_barrier_complete": False,
        "broker_recovery_audit_new_connection_epoch_required": False,
        "broker_position_quantity": "0",
        "broker_gross_absolute_position": "0",
        "preliminary_finalization_receipt_sha256": preliminary[
            "finalization_receipt_sha256"],
        "terminalization_service_epoch": handoff["execution_service_epoch"],
        "terminalization_service_fencing_generation": handoff[
            "execution_service_fencing_generation"],
        "terminalization_generation": 1,
        "terminal_latch_sha256": values["terminalizing_latch_sha256"],
        "execution_mutation_gate_closed": True,
        "broker_transport_connected": False,
        "broker_event_ingress_halted": True,
        "broker_callback_queue_drained": False,
        "broker_callbacks_in_flight": 0,
        "broker_reconnect_permitted": False,
        "terminal_latch_durable": True,
        "terminal_runtime_latch_loaded": False,
        "terminal_runtime_verified": False,
        "terminal_replay": replay,
        "terminal_proof_kind": values["terminal_proof_kind"],
        "terminal_external_halt_latch_sha256": values[
            "terminal_external_halt_latch_sha256"],
        "transport_cutoff_receipt_file_sha256": values[
            "transport_cutoff_receipt_file_sha256"],
        "transport_cutoff_receipt_body_sha256": values[
            "transport_cutoff_receipt_body_sha256"],
        "post_cutoff_terminal_witness_file_sha256": values[
            "post_cutoff_terminal_witness_file_sha256"],
        "post_cutoff_terminal_witness_body_sha256": values[
            "post_cutoff_terminal_witness_body_sha256"],
        "terminal_evidence_sha256": values["terminal_evidence_file_sha256"],
        "terminal_evidence_body_sha256": values[
            "terminal_evidence_body_sha256"],
        "egress_policy_sha256": values["egress_policy_sha256"],
        "egress_publisher_pid": 4102,
        "egress_publisher_start_ticks": 99123,
        "provider_trust_policy_body_sha256": values[
            "provider_trust_policy_body_sha256"],
        "signed_account_signature_sha256": values[
            "signed_account_signature_sha256"],
        "terminal_external_latch_loaded": True,
        "terminal_current_evidence_verified": True,
    }
    if result_overrides is not None:
        result.update(result_overrides)
    return result


class InjectedCrash(RuntimeError):
    pass


class FakeControl:
    def __init__(self, artifact_directory: Path):
        self.artifact_directory = artifact_directory
        self.trace: list[str] = []
        self.prepare_count = 0
        self.advance_count = 0
        self.deny_all = False
        self.terminal_evidence_raw: bytes | None = None

    def capture_authority(self, _handoff):
        self.trace.append("capture")
        return {
            "guardian_request_id": "guardian-request-1",
            "local_control_transaction_id": "local-control-transaction-1",
            "local_control_request_sha256": digest("local-request"),
            "guardian_active_receipt_file_sha256": digest("guardian-file"),
            "guardian_active_receipt_body_sha256": digest("guardian-body"),
        }

    def prepare_owner_transition(
            self, handoff, *, command_id, emergency, artifact_directory,
            completed_at_ms, finalization=None):
        self.trace.append("prepare-emergency" if emergency else "prepare-normal")
        self.prepare_count += 1
        owner = handoff["session_owner_reference"]
        common = {
            "version": 1, "completed_at_ms": completed_at_ms,
            "campaign_id": handoff["campaign_id"], "domain_id": "alpha",
            "cycle_id": handoff["cycle_id"],
            "cleanup_command_id": command_id,
            "session_owner_reference_sha256": FINALIZER.canonical_sha(owner),
            "token_sha256": owner["token_sha256"],
            "lease_generation": owner["lease_generation"],
            "session_id": owner["session_id"],
        }
        if emergency:
            document = {
                "schema": FINALIZER.OWNER_RECOVERY_SCHEMA, **common,
                "query_reason_code": "RECOVERY_QUERY_PROVEN_RECOVERY_ONLY",
                "command_status": "rejected",
                "command_reason_code": "EXECUTION_COMMAND_REJECTED",
                "order_id": -1, "authoritative_command_status": True,
                "recovery_only": True,
                "paper_finalization_required": True,
                "owner_fenced": False,
                "owner_audit_authoritative": True,
                "owner_audit_complete": True,
                "execution_service_epoch": handoff["execution_service_epoch"],
                "execution_service_fencing_generation": handoff[
                    "execution_service_fencing_generation"],
                "recovery_expires_at_ms": 9_000_000_000_000_000,
                "owner_active_order_count": 0,
                "owner_uncertain_command_count": 0,
                "broker_connection_epoch": 1,
                "broker_active_generation": 1,
                "broker_terminal_generation": 1,
                "owner_account": "DU123",
                "owner_execution_domain": "PAPER:alpha",
                "runtime_session_count": 0, "durable_owner_count": 1,
                "durable_owner_status": "RECOVERY_ONLY", "paper_only": True,
                "live_authorized": False, "authority_granted": False,
            }
            name = "durable-recovery-owner-reference.v1.json"
            count, status = 1, "RECOVERY_ONLY"
        else:
            witness = self.terminal_evidence_raw
            if witness is None and isinstance(finalization, dict):
                ack = finalization.get("terminal_ack_result")
                if isinstance(ack, dict):
                    receipt_values, _raw = FINALIZER._parse_terminal_ack_receipt(
                        ack["finalization_receipt"])
                    witness = hpe1_from_receipt_values(receipt_values)[0]
            finalized = FINALIZER._validate_owner_finalization(
                finalization, handoff, command_id,
                terminal_evidence_raw=witness)
            if finalized["state"] != "ACKED":
                raise AssertionError("normal transition prepared before ACK")
            sealed_result = finalized["finalization_result"]
            acknowledged = finalized["terminal_ack_result"]
            document = {
                "schema": FINALIZER.OWNER_RETIREMENT_SCHEMA, **common,
                "version": FINALIZER.OWNER_RETIREMENT_VERSION,
                "paper_finalization_required": True,
                "recovery_id": finalized["recovery_id"],
                "finalization_id": finalized["finalization_id"],
                "expected_owner_set_sha256": finalized[
                    "expected_owner_set_sha256"],
                "expected_owner_count": 1,
                "owner_set_canonical_hex": finalized[
                    "owner_set_canonical_hex"],
                "owner_token_sha256": finalized["owner_token_sha256"],
                "query_command_id": finalized["query_command_id"],
                "recovery_query_result": finalized["recovery_query_result"],
                "finalization_receipt_sha256": sealed_result[
                    "finalization_receipt_sha256"],
                "finalization_receipt": sealed_result[
                    "finalization_receipt"],
                "finalization_result": sealed_result,
                "terminal_ack_receipt_sha256": acknowledged[
                    "finalization_receipt_sha256"],
                "terminal_ack_receipt": acknowledged[
                    "finalization_receipt"],
                "terminal_ack_result": acknowledged,
                "terminal_acknowledged": True,
                "durable_hsl_audit":
                    "HSL8_POST_CUTOFF_SIGNED_TERMINAL_ACK_V3",
                "hsl_owner_purged": True, "broker_flat_proven": True,
                "terminal_flat_proof_kind":
                    "HSL8_POST_CUTOFF_SIGNED_TERMINAL_ACK_V3",
                "pre_cleanup_flat_evidence_role":
                    "DIAGNOSTIC_ONLY_NOT_TERMINAL_PROOF",
                "authority_path": owner["authority_path"],
                "authority_file_sha256": owner["authority_file_sha256"],
                "authority_body_sha256": owner["authority_body_sha256"],
                "revoke_bearer_path": owner["revoke_bearer_path"],
                "revoke_bearer_file_sha256": owner["revoke_bearer_sha256"],
                "credentials_destroyed": True,
                "mutation_credentials_destroyed": True,
                "credentials_destroyed_scope":
                    "PEER_MUTATION_TOKEN_AND_AUTHORITY_ONLY",
                "retained_root_recovery_bearer_count": 1,
                "retained_root_recovery_bearer_path":
                    owner["revoke_bearer_path"],
                "retained_root_recovery_bearer_sha256":
                    owner["revoke_bearer_sha256"],
                "retained_root_recovery_bearer_mutation_authority": False,
                "runtime_session_count": 0,
                "durable_owner_count": 0, "durable_owner_status": "RETIRED",
                "paper_only": True, "live_authorized": False,
                "authority_granted": False,
            }
            name = "durable-owner-retirement-receipt.v4.json"
            count, status = 0, "RETIRED"
        document["body_sha256"] = FINALIZER.canonical_sha(document)
        raw = FINALIZER.canonical_json(document)
        return {
            "path": str(artifact_directory / name),
            "file_sha256": FINALIZER.sha(raw),
            "body_sha256": document["body_sha256"],
            "durable_owner_count": count, "durable_owner_status": status,
            "document": document,
        }

    def advance_owner_finalization(self, handoff, finalization):
        self.advance_count += 1
        state = finalization["state"]
        self.trace.append("advance-" + state.lower())
        updated = deepcopy(finalization)
        if state == "INTENT":
            updated["recovery_query_result"] = recovery_query_result(
                handoff, updated)
            updated["state"] = "RECOVERY_ONLY"
        elif state == "RECOVERY_ONLY":
            updated["finalization_result"] = finalization_result(
                handoff, updated, "AUDIT_SEALED")
            updated["state"] = "AUDIT_SEALED"
        elif state == "AUDIT_SEALED":
            updated["terminal_ack_result"] = terminal_ack_result(
                handoff, updated, updated["finalization_result"])
            receipt_values, _ = FINALIZER._parse_terminal_ack_receipt(
                updated["terminal_ack_result"]["finalization_receipt"])
            self.terminal_evidence_raw, _body = hpe1_from_receipt_values(
                receipt_values)
            updated["state"] = "ACKED"
        else:
            raise AssertionError("unexpected finalization state")
        return updated

    def verify_owner_terminal_ack(self, handoff, finalization):
        self.trace.append("verify-terminal-ack")
        preliminary = finalization["finalization_result"]
        replayed = terminal_ack_result(
            handoff, finalization, preliminary, replay=True)
        witness = self.terminal_evidence_raw
        if witness is None:
            receipt_values, _raw = FINALIZER._parse_terminal_ack_receipt(
                finalization["terminal_ack_result"]["finalization_receipt"])
            witness = hpe1_from_receipt_values(receipt_values)[0]
        FINALIZER._validate_terminal_ack_result(
            replayed, handoff, finalization, preliminary,
            require_replay=True,
            terminal_evidence_raw=witness)
        stored = finalization["terminal_ack_result"]
        if (replayed["finalization_receipt_sha256"] !=
                stored["finalization_receipt_sha256"] or
                replayed["finalization_receipt"] !=
                stored["finalization_receipt"]):
            raise AssertionError("terminal ACK replay changed receipt")
        return replayed

    def owner_terminal_bearer_present(self, _handoff):
        return True

    def commit_owner_transition(self, _handoff, transition, *, emergency):
        self.trace.append("commit-emergency" if emergency else "commit-normal")
        FINALIZER._publish_owner_transition(transition)

    def stop_guardian(self):
        self.trace.append("stop-guardian")

    def fail_close(self):
        self.trace.append("fail-close")
        self.deny_all = True

    def engage_kill_switches(self):
        self.trace.append("kill-switches")

    def prove_exit_deny(self):
        self.trace.append("prove-exit-deny")
        return self.deny_all

    def prove_deny(self, _handoff):
        self.trace.append("prove-deny")
        units = ["hepta-execution-ib-paper@alpha.service"]
        return {
            "authorized_connector_count": 0, "identity_count": 0,
            "identity_manifest_sha256": digest("identities"),
            "broker_policy_sha256": digest("broker-policy"),
            "runtime_session_count": 0, "broker_mutation_units": units,
            "broker_mutation_units_sha256": FINALIZER.canonical_sha(units),
        }


class Fixture:
    def __init__(self, root: Path, emergency: bool = False):
        self.root = root
        self.artifact_root = root / "artifacts"
        self.control_root = root / "control"
        self.state_root = root / "state"
        self.directory = self.artifact_root / "campaign-1" / "cycle-1"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.control_directory = self.control_root / "campaign-1" / "cycle-1"
        self.control_directory.mkdir(parents=True, exist_ok=True)
        self.config = FINALIZER.Config(
            artifact_root=self.artifact_root, control_root=self.control_root,
            state_root=self.state_root,
            wal_path=self.state_root / "root-cleanup-wal.json",
            lock_path=self.state_root / "root-cleanup.lock",
            self_image_path=root / "finalizer")
        self.emergency = emergency
        schema = FINALIZER.EMERGENCY_REQUEST_SCHEMA if emergency else \
            FINALIZER.REQUEST_SCHEMA
        hint = {
            "schema": schema, "campaign_id": "campaign-1",
            "cycle_id": "cycle-1",
        }
        self.raw = FINALIZER.canonical_json(hint)
        owner = {
            "token_name": "session.token",
            "token_path": str(FINALIZER.OWNER_TOKEN_PATH),
            "authority_path": str(FINALIZER.OWNER_AUTHORITY_PATH),
            "authority_file_sha256": digest("authority-file"),
            "authority_body_sha256": digest("authority-body"),
            "lease_generation": 7, "session_id": "session-1",
            "peer_uid": 2104, "peer_gid": 2104,
            "token_sha256": digest("token"),
            "revoke_bearer_path": str(FINALIZER.OWNER_REVOKE_PATH),
            "revoke_bearer_sha256": digest("token"),
            "owner_account": "DU123",
            "owner_execution_domain": "PAPER:alpha",
        }
        self.handoff = {
            "campaign_id": "campaign-1", "cycle_id": "cycle-1",
            "watch_handoff_receipt_file_sha256": digest("watch-file"),
            "watch_handoff_receipt_body_sha256": digest("watch-body"),
            "intent_sha256": digest("intent"),
            "installed_images_sha256": digest("images"),
            "backend_transform_version": FINALIZER.BACKEND_TRANSFORM_VERSION,
            "session_owner_reference": owner,
            "execution_service_epoch": "epoch-1",
            "execution_service_fencing_generation": 7,
        }
        request = {
            "schema": schema, "issued_at_ms": 1,
            "expires_at_ms": 9_000_000_000_000_000,
            "campaign_id": "campaign-1", "domain_id": "alpha",
            "cycle_id": "cycle-1", "cleanup_tool_call_id": "cleanup-1",
            "cleanup_command_id": "cleanup-1",
            "tool_descriptor_sha256": digest("descriptor"),
            "session_owner_reference_sha256": FINALIZER.canonical_sha(owner),
            "execution_service_epoch": "epoch-1",
            "execution_service_fencing_generation": 7,
        }
        if emergency:
            request["recovery_reason_codes"] = ["INJECTED_UNCERTAINTY"]
        evidence = {
            "journal_path": str(self.directory / "execution-journal.v1.jsonl"),
            "journal_sha256": digest("journal"), "journal_size": 10,
            "journal_last_sequence": 2,
            "tool_evidence_sha256": digest("tool-evidence"),
        }
        image_paths = {
            "executor": FINALIZER.PEER_IMAGE_PATH,
            "backend-adapter":
                "/usr/libexec/hepta-p1-paper-canary-backend-adapter",
            "root-finalizer": str(self.config.self_image_path),
            "root-coordinator": FINALIZER.ROOT_COORDINATOR_IMAGE_PATH,
            "crash-emergency-closer":
                FINALIZER.ROOT_EMERGENCY_CLOSER_IMAGE_PATH,
            "terminal-prover": FINALIZER.ROOT_TERMINAL_PROVER_IMAGE_PATH,
        }
        images = {
            role: {
                "role": role, "path": path, "file_sha256": digest(role),
                "mode": 0o755, "uid": 0, "gid": 0, "nlink": 1,
            }
            for role, path in image_paths.items()
        }
        request_path = self.directory / (
            "root-emergency-cleanup-request.v1.json" if emergency else
            "root-cleanup-request.v1.json")
        evidence_path = self.directory / (
            "root-emergency-cleanup-evidence.v1.json" if emergency else
            "pre-cleanup-flat-evidence.v1.json")
        self.value = FINALIZER.Validated(
            request, self.raw, request_path, digest("request-body"), evidence,
            b"evidence\n", evidence_path, digest("evidence-body"), self.handoff,
            b"handoff\n", self.control_root / "campaign-1" / "cycle-1" /
            "execution-handoff.v1.json", digest("handoff-body"), b"journal\n",
            images, {}, b"", "")


class RootFinalizerTests(unittest.TestCase):
    def _run(self, fixture: Fixture, control: FakeControl, **kwargs):
        validator = "validate_emergency_request" if fixture.emergency else \
            "validate_request"
        stable = FINALIZER.stable_read

        def test_stable(path, **parameters):
            candidate = Path(path)
            if candidate == FINALIZER.TERMINAL_EVIDENCE_PATH:
                witness = control.terminal_evidence_raw
                if witness is None and fixture.config.wal_path.exists():
                    wal = FINALIZER.strict_json(
                        fixture.config.wal_path.read_bytes(), "TEST")
                    finalization = wal.get("owner_finalization")
                    if isinstance(finalization, dict):
                        ack = finalization.get("terminal_ack_result")
                        if isinstance(ack, dict):
                            receipt, _raw = FINALIZER._parse_terminal_ack_receipt(
                                ack["finalization_receipt"])
                            witness, _body = hpe1_from_receipt_values(receipt)
                if witness is None:
                    retirement_path = fixture.control_directory / \
                        "durable-owner-retirement-receipt.v4.json"
                    if retirement_path.exists():
                        witness = hpe1_from_retirement(retirement_path)
                if witness is not None:
                    return witness
            if candidate.is_relative_to(fixture.root):
                if parameters.get("uid") == 0:
                    parameters["uid"] = __import__("os").getuid()
                if parameters.get("gid") == 0:
                    parameters["gid"] = __import__("os").getgid()
            return stable(candidate, **parameters)

        with mock.patch.object(FINALIZER, validator, return_value=fixture.value), \
                mock.patch.object(FINALIZER, "stable_read",
                                  side_effect=test_stable), \
                mock.patch("os.fchown", return_value=None), \
                mock.patch("os.chown", return_value=None):
            return FINALIZER.finalize(
                fixture.raw, control=control, config=fixture.config,
                now_ms=1_000, **kwargs)

    def _attest_root(
            self, fixture: Fixture, *, stop_post: bool = False,
            peer=(4242, 0, 0), property_changes=None,
            process_changes=None, credential_changes=None):
        coordinator = b"reviewed root coordinator image\n"
        closer = b"reviewed crash emergency closer image\n"
        fixture.value.images["root-coordinator"]["file_sha256"] = \
            FINALIZER.sha(coordinator)
        fixture.value.images["crash-emergency-closer"]["file_sha256"] = \
            FINALIZER.sha(closer)
        properties = ({
            "LoadState": "loaded", "ActiveState": "deactivating",
            "SubState": "stop-post", "MainPID": "0",
            "ControlPID": str(peer[0]),
            "ControlGroup": FINALIZER.ROOT_COORDINATOR_CGROUP,
        } if stop_post else {
            "LoadState": "loaded", "ActiveState": "active",
            "SubState": "running", "MainPID": str(peer[0]),
            "ControlPID": "0",
            "ControlGroup": FINALIZER.ROOT_COORDINATOR_CGROUP,
        })
        properties.update(property_changes or {})
        status = (
            "Name:\tpython3.12\n"
            "Uid:\t0\t0\t0\t0\n"
            "Gid:\t0\t0\t0\t0\n"
            "Groups:\t0\n").encode("ascii")
        credential_path = (
            FINALIZER.ROOT_EMERGENCY_CLOSER_CREDENTIAL_IMAGE if stop_post else
            FINALIZER.ROOT_COORDINATOR_CREDENTIAL_IMAGE)
        argument = b"--exec-stop-post" if stop_post else b"--service-run"
        process_values = {
            "status": status,
            "cgroup": (
                f"0::{FINALIZER.ROOT_COORDINATOR_CGROUP}\n".encode("ascii")),
            "cmdline": b"\0".join((
                b"/usr/bin/python3.12", b"-I", b"-S",
                credential_path.encode("ascii"), argument)) + b"\0",
        }
        process_values.update(process_changes or {})
        credentials = {
            "hepta-p1-paper-canary-root-coordinator.py": coordinator,
            "hepta-p1-paper-canary-crash-emergency-closer.py": closer,
        }
        credentials.update(credential_changes or {})

        def process(path, _maximum):
            return process_values[path.name]

        def stable(path, **_parameters):
            return credentials[path.name]

        completed = subprocess.CompletedProcess(
            [], 0,
            stdout="".join(f"{key}={value}\n"
                           for key, value in properties.items()), stderr="")
        with mock.patch.object(FINALIZER, "_proc_bytes", side_effect=process), \
                mock.patch.object(FINALIZER, "stable_read", side_effect=stable):
            FINALIZER.attest_requester(
                peer, fixture.value,
                command=lambda *_args, **_kwargs: completed)

    def _generated_normal_artifacts(self, root: Path):
        artifact_root = root / "artifacts"
        control_root = root / "control"
        state_root = root / "state"
        self_image = root / "root-finalizer"
        executor = EXECUTOR_FIXTURES.EXECUTOR
        paths = list(executor.INSTALLED_IMAGE_PATHS[:8])
        paths = [
            (role, str(self_image) if role == "root-finalizer" else path)
            for role, path in paths
        ]
        descriptor = dict(FINALIZER.ROOT_CLEANUP_DESCRIPTOR)
        descriptor_sha256 = executor.canonical_sha256(descriptor)
        with mock.patch.object(executor, "INSTALLED_IMAGE_PATHS", tuple(paths)), \
                mock.patch.object(executor, "POLICY_WINDOW_MS", 60_000), \
                mock.patch.object(
                    executor, "ROOT_CLEANUP_RECEIPT_SCHEMA",
                    FINALIZER.RECEIPT_SCHEMA), \
                mock.patch.object(
                    executor, "ROOT_CLEANUP_ACTIONS",
                    tuple(FINALIZER.NORMAL_REQUIRED_ACTIONS)), \
                mock.patch.object(
                    executor, "ROOT_CLEANUP_DESCRIPTOR", descriptor), \
                mock.patch.object(
                    executor, "ROOT_CLEANUP_DESCRIPTOR_SHA256",
                    descriptor_sha256), \
                mock.patch.object(
                    executor, "ARTIFACT_ROOT",
                    PurePosixPath(str(artifact_root))), \
                mock.patch.object(
                    executor, "CONTROL_ROOT",
                    PurePosixPath(str(control_root))):
            handoff = EXECUTOR_FIXTURES.handoff_document()
            backend = EXECUTOR_FIXTURES.FakeBackend(handoff)
            result = executor.execute(executor.canonical_json(handoff), backend)
        self.assertEqual(result.status, "SUCCESS")
        campaign, cycle = handoff["campaign_id"], handoff["cycle_id"]
        artifact_directory = artifact_root / campaign / cycle
        control_directory = control_root / campaign / cycle
        artifact_directory.mkdir(parents=True)
        control_directory.mkdir(parents=True)
        handoff_path = control_directory / "execution-handoff.v1.json"
        handoff_path.write_bytes(executor.canonical_json(handoff))
        for name, raw in backend.checkpoint.items():
            (artifact_directory / name).write_bytes(raw)
        journal_path = artifact_directory / "execution-journal.v1.jsonl"
        journal_path.write_bytes(backend.journal)
        self_image.write_bytes(b"fixed root finalizer fixture\n")
        self_image.chmod(0o755)
        for path in (*control_directory.iterdir(), *artifact_directory.iterdir()):
            path.chmod(0o600)
        request_path = artifact_directory / "root-cleanup-request.v1.json"
        request = FINALIZER.strict_json(request_path.read_bytes(), "TEST")
        request["expires_at_ms"] = (
            request["issued_at_ms"] + FINALIZER.ROOT_CLEANUP_TIMEOUT_MS)
        request_path.write_bytes(self._reseal(request))
        config = FINALIZER.Config(
            artifact_root=artifact_root, control_root=control_root,
            state_root=state_root, wal_path=state_root / "wal.json",
            lock_path=state_root / "lock", self_image_path=self_image)
        return {
            "root": root, "config": config, "request_path": request_path,
            "evidence_path": artifact_directory /
                "pre-cleanup-flat-evidence.v1.json",
            "bundle_path": artifact_directory /
                "pre-cleanup-response-bundle.v1.json",
            "journal_path": journal_path, "handoff_path": handoff_path,
        }

    def _validate_generated(self, generated):
        root = generated["root"]
        original = FINALIZER.stable_read

        def stable(path, **parameters):
            candidate = Path(path)
            if candidate.is_relative_to(root):
                if parameters.get("uid") in {0, FINALIZER.PEER_UID}:
                    parameters["uid"] = os.getuid()
                if parameters.get("gid") in {0, FINALIZER.PEER_GID}:
                    parameters["gid"] = os.getgid()
            return original(candidate, **parameters)

        raw = generated["request_path"].read_bytes()
        request = FINALIZER.strict_json(raw, "TEST")
        with mock.patch.object(FINALIZER, "stable_read", side_effect=stable):
            return FINALIZER.validate_request(
                raw, config=generated["config"],
                now_ms=request["issued_at_ms"])

    @staticmethod
    def _reseal(document):
        body = dict(document)
        body.pop("body_sha256", None)
        document["body_sha256"] = FINALIZER.canonical_sha(body)
        return FINALIZER.canonical_json(document)

    def _rebind_normal_request(self, generated, *, journal_raw=None,
                               mutate_bundle=lambda _bundle: None):
        bundle_path = generated["bundle_path"]
        evidence_path = generated["evidence_path"]
        request_path = generated["request_path"]
        bundle = FINALIZER.strict_json(bundle_path.read_bytes(), "TEST")
        evidence = FINALIZER.strict_json(evidence_path.read_bytes(), "TEST")
        request = FINALIZER.strict_json(request_path.read_bytes(), "TEST")
        if journal_raw is not None:
            generated["journal_path"].write_bytes(journal_raw)
            bundle["journal_sha256"] = FINALIZER.sha(journal_raw)
            evidence["journal_sha256"] = FINALIZER.sha(journal_raw)
            evidence["journal_size"] = len(journal_raw)
        mutate_bundle(bundle)
        bundle_raw = self._reseal(bundle)
        bundle_path.write_bytes(bundle_raw)
        evidence["response_bundle_file_sha256"] = FINALIZER.sha(bundle_raw)
        evidence["response_bundle_body_sha256"] = bundle["body_sha256"]
        evidence_raw = self._reseal(evidence)
        evidence_path.write_bytes(evidence_raw)
        request["pre_cleanup_evidence_file_sha256"] = \
            FINALIZER.sha(evidence_raw)
        request["pre_cleanup_evidence_body_sha256"] = evidence["body_sha256"]
        request_path.write_bytes(self._reseal(request))

    def test_normal_transaction_is_crash_safe_and_idempotent(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Fixture(Path(temporary))
            first = FakeControl(fixture.directory)

            def crash(phase):
                if phase == "OWNER_TRANSITION_PREPARED":
                    raise InjectedCrash(phase)

            with self.assertRaisesRegex(InjectedCrash, "OWNER_TRANSITION_PREPARED"):
                self._run(fixture, first, crash_hook=crash)
            self.assertTrue(fixture.config.wal_path.exists())
            self.assertFalse((fixture.control_directory /
                              "durable-owner-retirement-receipt.v4.json").exists())

            replay = FakeControl(fixture.directory)
            raw = self._run(fixture, replay)
            receipt = FINALIZER.strict_json(raw, "TEST")
            self.assertEqual(receipt["durable_owner_count"], 0)
            self.assertEqual(receipt["durable_owner_status"], "RETIRED")
            self.assertTrue(receipt["broker_mutation_units_inactive"])
            self.assertNotIn("finalizer_socket_inactive", receipt)
            self.assertNotIn("relevant_units_inactive", receipt)
            self.assertEqual(
                replay.trace[:2], ["verify-terminal-ack", "prepare-normal"])
            self.assertFalse(fixture.config.wal_path.exists())

            again = FakeControl(fixture.directory)
            self.assertEqual(self._run(fixture, again), raw)
            self.assertEqual(
                again.trace, ["verify-terminal-ack", "prove-deny"])

    def test_each_owner_finalization_wal_phase_replays_forward_with_same_ids(
            self):
        cases = {
            "OWNER_FINALIZATION_INTENT": ("INTENT", "advance-intent"),
            "OWNER_RECOVERY_ONLY": (
                "RECOVERY_ONLY", "advance-recovery_only"),
            "OWNER_FINALIZATION_SEALED": (
                "AUDIT_SEALED", "advance-audit_sealed"),
            "OWNER_FINALIZATION_ACKED": ("ACKED", None),
        }
        for phase, (expected_state, first_replay_step) in cases.items():
            with self.subTest(phase=phase), \
                    tempfile.TemporaryDirectory() as temporary:
                fixture = Fixture(Path(temporary))

                def crash(observed):
                    if observed == phase:
                        raise InjectedCrash(observed)

                with self.assertRaisesRegex(InjectedCrash, phase):
                    self._run(
                        fixture, FakeControl(fixture.directory),
                        crash_hook=crash)
                wal = FINALIZER.strict_json(
                    fixture.config.wal_path.read_bytes(), "TEST")
                persisted = wal["owner_finalization"]
                self.assertEqual(persisted["state"], expected_state)
                ids = (persisted["recovery_id"],
                       persisted["finalization_id"])

                replay = FakeControl(fixture.directory)
                raw = self._run(fixture, replay)
                receipt = FINALIZER.strict_json(raw, "TEST")
                retirement = FINALIZER.strict_json(
                    Path(receipt[
                        "durable_owner_retirement_receipt_path"]).read_bytes(),
                    "TEST")
                self.assertEqual(
                    (retirement["recovery_id"],
                     retirement["finalization_id"]), ids)
                if first_replay_step is None:
                    self.assertFalse(any(
                        item.startswith("advance-") for item in replay.trace))
                else:
                    self.assertEqual(
                        next(item for item in replay.trace
                             if item.startswith("advance-")),
                        first_replay_step)
                self.assertIn("verify-terminal-ack", replay.trace)

    def test_owner_finalization_timeout_and_lost_ack_response_keep_replay_ids(
            self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Fixture(Path(temporary))
            control = FINALIZER.ProductionControl(mock.Mock())
            intent = FINALIZER._owner_finalization_intent(
                fixture.handoff, "cleanup-1")
            with mock.patch.object(
                    FINALIZER, "_owner_material",
                    return_value=(b"authority\n", b"bearer\n", b"token\n")), \
                    mock.patch.object(
                        FINALIZER, "_owner_terminal_bearer",
                        return_value=b"0" * 64 + b"\n"), \
                    mock.patch.object(
                        FINALIZER.subprocess, "run",
                        side_effect=subprocess.TimeoutExpired(
                            ["hepta-sessionctl"], 15)), \
                    self.assertRaisesRegex(
                        FINALIZER.FinalizerError,
                        "ROOT_FINALIZER_OWNER_TRANSITION_UNCERTAIN"):
                control.advance_owner_finalization(fixture.handoff, intent)
            self.assertEqual(intent["state"], "INTENT")
            query = recovery_query_result(fixture.handoff, intent)
            completed = subprocess.CompletedProcess(
                [], 0, stdout=FINALIZER.canonical_json(query).decode("ascii"),
                stderr="")
            with mock.patch.object(
                    FINALIZER, "_owner_material",
                    return_value=(b"authority\n", b"bearer\n", b"token\n")), \
                    mock.patch.object(
                        FINALIZER.subprocess, "run", return_value=completed):
                recovered = control.advance_owner_finalization(
                    fixture.handoff, intent)
            self.assertEqual(recovered["recovery_id"], intent["recovery_id"])

            sealed = finalization_result(
                fixture.handoff, recovered, "AUDIT_SEALED")
            sealed_completed = subprocess.CompletedProcess(
                [], 0, stdout=FINALIZER.canonical_json(sealed).decode("ascii"),
                stderr="")
            with mock.patch.object(
                    FINALIZER, "_owner_material",
                    return_value=(b"authority\n", b"bearer\n", b"token\n")), \
                    mock.patch.object(
                        FINALIZER.subprocess, "run",
                        side_effect=subprocess.TimeoutExpired(
                            ["hepta-sessionctl"], 120)), \
                    self.assertRaisesRegex(
                        FINALIZER.FinalizerError,
                        "ROOT_FINALIZER_OWNER_TRANSITION_UNCERTAIN"):
                control.advance_owner_finalization(
                    fixture.handoff, recovered)
            self.assertEqual(recovered["state"], "RECOVERY_ONLY")
            with mock.patch.object(
                    FINALIZER, "_owner_material",
                    return_value=(b"authority\n", b"bearer\n", b"token\n")), \
                    mock.patch.object(
                        FINALIZER.subprocess, "run",
                        return_value=sealed_completed):
                sealed_state = control.advance_owner_finalization(
                    fixture.handoff, recovered)
            self.assertEqual(sealed_state["state"], "AUDIT_SEALED")
            acknowledged = terminal_ack_result(
                fixture.handoff, sealed_state,
                sealed_state["finalization_result"])
            acknowledged_receipt, _ack_raw = FINALIZER._parse_terminal_ack_receipt(
                acknowledged["finalization_receipt"])
            terminal_witness = hpe1_from_receipt_values(acknowledged_receipt)[0]
            fault = deepcopy(acknowledged)
            fault["accepted"] = False
            fault["reason_code"] = (
                "SUPERVISOR_FAULT_INJECTED:after_paper_terminal_ack_commit")
            failed = subprocess.CompletedProcess(
                [], 4, stdout=FINALIZER.canonical_json(fault).decode("ascii"),
                stderr="")
            replayed = terminal_ack_result(
                fixture.handoff, sealed_state,
                sealed_state["finalization_result"], replay=True)
            prepared_response = deepcopy(sealed_state["finalization_result"])
            prepared_response["reason_code"] = \
                "PAPER_TERMINAL_WITNESS_PREPARED"
            prepared = subprocess.CompletedProcess(
                [], 0, stdout=FINALIZER.canonical_json(
                    prepared_response).decode("ascii"), stderr="")
            success = subprocess.CompletedProcess(
                [], 0, stdout=FINALIZER.canonical_json(replayed).decode(
                    "ascii"), stderr="")
            with mock.patch.object(
                    FINALIZER, "_owner_material",
                    return_value=(b"authority\n", b"bearer\n", b"token\n")), \
                    mock.patch.object(
                        FINALIZER.subprocess, "run",
                        side_effect=[prepared, failed]), \
                    mock.patch.object(
                        FINALIZER, "stable_read", return_value=terminal_witness), \
                    self.assertRaisesRegex(
                        FINALIZER.FinalizerError,
                        "ROOT_FINALIZER_OWNER_FINALIZATION_ACK_PENDING"):
                control.advance_owner_finalization(
                    fixture.handoff, sealed_state)
            with mock.patch.object(
                    FINALIZER, "_owner_material",
                    return_value=(b"authority\n", b"bearer\n", b"token\n")), \
                    mock.patch.object(
                        FINALIZER.subprocess, "run",
                        side_effect=[prepared, success]), \
                    mock.patch.object(
                        FINALIZER, "stable_read", return_value=terminal_witness):
                acked = control.advance_owner_finalization(
                    fixture.handoff, sealed_state)
            self.assertEqual(acked["state"], "ACKED")
            self.assertEqual(acked["recovery_id"], intent["recovery_id"])
            self.assertNotEqual(
                acked["finalization_result"]["finalization_receipt"],
                acked["terminal_ack_result"]["finalization_receipt"])
            self.assertEqual(
                acked["terminal_ack_result"]["finalization_receipt"],
                acknowledged["finalization_receipt"])
            self.assertEqual(
                acked["terminal_ack_result"]["finalization_receipt_sha256"],
                acknowledged["finalization_receipt_sha256"])

    def test_emergency_is_separate_recovery_only_terminal(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Fixture(Path(temporary), emergency=True)
            raw = self._run(fixture, FakeControl(fixture.directory))
            receipt = FINALIZER.strict_json(raw, "TEST")
            self.assertEqual(
                receipt["status"], FINALIZER.EMERGENCY_SUCCESS_STATUS)
            self.assertEqual(receipt["durable_owner_count"], 1)
            self.assertEqual(receipt["durable_owner_status"], "RECOVERY_ONLY")
            self.assertFalse(receipt["broker_flat_proven"])
            self.assertTrue(receipt["recovery_required"])
            self.assertTrue(receipt["evidence_retained"])
            self.assertNotIn("pre_cleanup_evidence_path", receipt)
            self.assertTrue((fixture.control_directory /
                             "durable-recovery-owner-reference.v1.json").exists())

    def test_normal_and_emergency_receipts_are_mutually_exclusive(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            normal = Fixture(root)
            self._run(normal, FakeControl(normal.directory))
            emergency = Fixture(root, emergency=True)
            with self.assertRaisesRegex(
                    FINALIZER.FinalizerError,
                    "ROOT_FINALIZER_TERMINAL_MODE_CONFLICT"):
                self._run(emergency, FakeControl(emergency.directory))

    def test_legacy_normal_receipt_is_forensic_only_and_blocks_v3_success(self):
        for version in (1, 2):
            with self.subTest(version=version), \
                    tempfile.TemporaryDirectory() as temporary:
                fixture = Fixture(Path(temporary))
                legacy = fixture.control_directory / \
                    f"root-cleanup-receipt.v{version}.json"
                legacy.write_bytes(b"{}\n")
                legacy.chmod(0o600)
                with self.assertRaisesRegex(
                        FINALIZER.FinalizerError,
                        "ROOT_FINALIZER_LEGACY_NORMAL_ARTIFACT_PRESENT"):
                    self._run(fixture, FakeControl(fixture.directory))

    def test_finalization_receipt_or_owner_binding_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Fixture(Path(temporary))
            intent = FINALIZER._owner_finalization_intent(
                fixture.handoff, "cleanup-1")
            intent["recovery_query_result"] = recovery_query_result(
                fixture.handoff, intent)
            intent["state"] = "RECOVERY_ONLY"
            result = finalization_result(
                fixture.handoff, intent, "AUDIT_SEALED")
            for field, changed in (
                    ("expected_owner_set_sha256", digest("substitute-owner")),
                    ("finalization_receipt_sha256", digest("substitute-receipt")),
                    ("broker_position_quantity", "1")):
                with self.subTest(field=field):
                    mutated = deepcopy(result)
                    mutated[field] = changed
                    with self.assertRaisesRegex(
                            FINALIZER.FinalizerError,
                            "ROOT_FINALIZER_OWNER_FINALIZATION_INVALID"):
                        FINALIZER._validate_finalization_result(
                            mutated, fixture.handoff, intent,
                            expected_state="AUDIT_SEALED")

    def test_terminal_ack_requires_atomic_flat_and_current_runtime_witness(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Fixture(Path(temporary))
            finalized = FINALIZER._owner_finalization_intent(
                fixture.handoff, "cleanup-1")
            finalized["recovery_query_result"] = recovery_query_result(
                fixture.handoff, finalized)
            finalized["state"] = "RECOVERY_ONLY"
            preliminary = finalization_result(
                fixture.handoff, finalized, "AUDIT_SEALED")
            finalized["finalization_result"] = preliminary
            finalized["state"] = "AUDIT_SEALED"

            valid = terminal_ack_result(
                fixture.handoff, finalized, preliminary)
            valid_receipt, _valid_receipt_raw = FINALIZER._parse_terminal_ack_receipt(
                valid["finalization_receipt"])
            terminal_witness = hpe1_from_receipt_values(valid_receipt)[0]
            FINALIZER._validate_terminal_ack_result(
                valid, fixture.handoff, finalized, preliminary,
                require_replay=False,
                terminal_evidence_raw=terminal_witness)

            invalid = (
                ("missing-runtime-witness", lambda value: value.pop(
                    "terminal_runtime_verified")),
                ("legacy-runtime-claimed", lambda value: value.__setitem__(
                    "terminal_runtime_verified", True)),
                ("legacy-latch-claimed", lambda value: value.__setitem__(
                    "terminal_runtime_latch_loaded", True)),
                ("transport-connected", lambda value: value.__setitem__(
                    "broker_transport_connected", True)),
                ("event-ingress-open", lambda value: value.__setitem__(
                    "broker_event_ingress_halted", False)),
                ("opaque-callback-drain-claimed", lambda value: value.__setitem__(
                    "broker_callback_queue_drained", True)),
                ("callback-in-flight", lambda value: value.__setitem__(
                    "broker_callbacks_in_flight", 1)),
                ("reconnect-enabled", lambda value: value.__setitem__(
                    "broker_reconnect_permitted", True)),
                ("latch-not-durable", lambda value: value.__setitem__(
                    "terminal_latch_durable", False)),
                ("external-latch-not-loaded", lambda value: value.__setitem__(
                    "terminal_external_latch_loaded", False)),
                ("current-evidence-not-verified", lambda value: value.__setitem__(
                    "terminal_current_evidence_verified", False)),
                ("latch-digest-tamper", lambda value: value.__setitem__(
                    "terminal_latch_sha256", digest("substitute-latch"))),
                ("terminal-identity-drift", lambda value: value.__setitem__(
                    "execution_service_epoch", "epoch-restarted-1")),
            )
            for name, mutation in invalid:
                changed = deepcopy(valid)
                mutation(changed)
                with self.subTest(case=name), self.assertRaises(
                        FINALIZER.FinalizerError):
                    FINALIZER._validate_terminal_ack_result(
                        changed, fixture.handoff, finalized, preliminary,
                        require_replay=False,
                        terminal_evidence_raw=terminal_witness)

            for name, receipt_fields, result_fields in (
                    ("late-position",
                     {"position_count": "1"},
                     {"broker_position_quantity": "1"}),
                    ("late-active-order",
                     {"active_order_count": "1"},
                     {"broker_global_active_order_count": 1}),
                    ("post-fill-pending",
                     {"unresolved_mutation_command_count": "1"},
                     {"broker_post_fill_risk_reconciliation_pending": True}),
                    ("latch-loss",
                     {"terminal_external_halt_latch_durable": "0"},
                     {"terminal_latch_durable": False})):
                changed = terminal_ack_result(
                    fixture.handoff, finalized, preliminary,
                    receipt_overrides=receipt_fields,
                    result_overrides=result_fields)
                with self.subTest(case=name), self.assertRaises(
                        FINALIZER.FinalizerError):
                    FINALIZER._validate_terminal_ack_result(
                        changed, fixture.handoff, finalized, preliminary,
                        require_replay=False,
                        terminal_evidence_raw=terminal_witness)

    def test_acked_wal_resume_requires_remote_terminal_replay(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Fixture(Path(temporary))

            def crash(phase):
                if phase == "OWNER_FINALIZATION_ACKED":
                    raise InjectedCrash(phase)

            with self.assertRaisesRegex(
                    InjectedCrash, "OWNER_FINALIZATION_ACKED"):
                self._run(
                    fixture, FakeControl(fixture.directory), crash_hook=crash)
            wal_before = FINALIZER.strict_json(
                fixture.config.wal_path.read_bytes(), "TEST")

            class ReplayFailure(FakeControl):
                def verify_owner_terminal_ack(self, _handoff, _finalization):
                    self.trace.append("verify-terminal-ack")
                    raise FINALIZER.FinalizerError(
                        "ROOT_FINALIZER_OWNER_TERMINAL_REPLAY_REQUIRED")

            failed = ReplayFailure(fixture.directory)
            with self.assertRaisesRegex(
                    FINALIZER.FinalizerError,
                    "ROOT_FINALIZER_OWNER_TERMINAL_REPLAY_REQUIRED"):
                self._run(fixture, failed)
            wal_after = FINALIZER.strict_json(
                fixture.config.wal_path.read_bytes(), "TEST")
            self.assertEqual(
                wal_after["owner_finalization"],
                wal_before["owner_finalization"])
            self.assertEqual(
                wal_after["owner_finalization"]["state"], "ACKED")
            self.assertFalse((fixture.control_directory /
                              "root-cleanup-receipt.v4.json").exists())
            self.assertFalse((fixture.control_directory /
                              "durable-owner-retirement-receipt.v4.json").exists())
            self.assertEqual(
                failed.trace, ["verify-terminal-ack"])

            replay = FakeControl(fixture.directory)
            self._run(fixture, replay)
            self.assertIn("verify-terminal-ack", replay.trace)
            self.assertNotIn("purge-terminal-bearer", replay.trace)

    def test_durable_inner_receipt_replays_and_retains_bearer_for_outer_join(
            self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Fixture(Path(temporary))
            shared = {"bearer_present": True}

            class RetainedBearerControl(FakeControl):
                def owner_terminal_bearer_present(self, _handoff):
                    return shared["bearer_present"]

            def crash(phase):
                if phase == "RECEIPT_DURABLE_OWNER_BEARER_RETAINED":
                    raise InjectedCrash(phase)

            first = RetainedBearerControl(fixture.directory)
            with self.assertRaisesRegex(
                    InjectedCrash, "RECEIPT_DURABLE_OWNER_BEARER_RETAINED"):
                self._run(fixture, first, crash_hook=crash)
            receipt_path = fixture.control_directory / \
                "root-cleanup-receipt.v4.json"
            durable = receipt_path.read_bytes()
            self.assertTrue(shared["bearer_present"])
            self.assertTrue(fixture.config.wal_path.exists())

            class ReplayFailure(RetainedBearerControl):
                def verify_owner_terminal_ack(self, _handoff, _finalization):
                    self.trace.append("verify-terminal-ack")
                    raise FINALIZER.FinalizerError(
                        "ROOT_FINALIZER_OWNER_TERMINAL_REPLAY_REQUIRED")

            failed = ReplayFailure(fixture.directory)
            with self.assertRaisesRegex(
                    FINALIZER.FinalizerError,
                    "ROOT_FINALIZER_OWNER_TERMINAL_REPLAY_REQUIRED"):
                self._run(fixture, failed)
            self.assertEqual(failed.trace, ["verify-terminal-ack"])
            self.assertTrue(shared["bearer_present"])
            self.assertTrue(fixture.config.wal_path.exists())
            self.assertEqual(receipt_path.read_bytes(), durable)

            replay = RetainedBearerControl(fixture.directory)
            self.assertEqual(self._run(fixture, replay), durable)
            self.assertEqual(
                replay.trace,
                ["verify-terminal-ack", "prove-deny"])
            self.assertTrue(shared["bearer_present"])
            self.assertFalse(fixture.config.wal_path.exists())

    def test_durable_inner_receipt_rejects_bare_bearer_absence(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Fixture(Path(temporary))
            self._run(fixture, FakeControl(fixture.directory))

            class MissingBearerControl(FakeControl):
                def owner_terminal_bearer_present(self, _handoff):
                    return False

            with self.assertRaisesRegex(
                    FINALIZER.FinalizerError,
                    "ROOT_FINALIZER_OWNER_BEARER_ABSENCE_UNPROVEN"):
                self._run(fixture, MissingBearerControl(fixture.directory))

    def test_terminal_prover_accepts_only_v4_hsl8_retirement(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            owner_root = root / "owner-root"
            owner_root.mkdir(mode=0o700)
            authority_path = owner_root / "session.token.authority.json"
            revoke_path = owner_root / "session.token.revoke-token"
            intent_path = owner_root / "session.token.owner-may-exist.v1.json"
            with mock.patch.object(
                    FINALIZER, "OWNER_AUTHORITY_ROOT", owner_root), \
                    mock.patch.object(
                        FINALIZER, "OWNER_AUTHORITY_PATH", authority_path), \
                    mock.patch.object(
                        FINALIZER, "OWNER_REVOKE_PATH", revoke_path), \
                    mock.patch.object(
                        FINALIZER, "OWNER_INTENT_PATH", intent_path):
                fixture = Fixture(root)
                self._run(fixture, FakeControl(fixture.directory))
            revoke_path.write_bytes(b"0" * 64 + b"\n")
            revoke_path.chmod(0o600)
            retirement_path = fixture.control_directory / \
                "durable-owner-retirement-receipt.v4.json"
            terminal_witness = hpe1_from_retirement(retirement_path)

            original = TERMINAL.stable_read

            def stable(path, **parameters):
                candidate = Path(path)
                if candidate == TERMINAL.TERMINAL_EVIDENCE_PATH:
                    return terminal_witness
                if candidate.is_relative_to(root):
                    if parameters.get("uid") == 0:
                        parameters["uid"] = os.getuid()
                    if parameters.get("gid") == 0:
                        parameters["gid"] = os.getgid()
                return original(candidate, **parameters)

            def prove():
                with mock.patch.object(
                        TERMINAL, "CONTROL_ROOT", fixture.control_root), \
                    mock.patch.object(
                        TERMINAL, "OWNER_ROOT", owner_root), \
                    mock.patch.object(
                        TERMINAL, "stable_read", side_effect=stable), \
                    mock.patch.object(
                        TERMINAL, "_fresh_terminal_replay", return_value={}):
                    return TERMINAL._owner_evidence(
                        "campaign-1", "cycle-1", "RETIRED")

            count, path, _file, _body = prove()
            self.assertEqual(count, 0)
            self.assertEqual(
                path, str(fixture.control_directory /
                          "durable-owner-retirement-receipt.v4.json"))

            retirement_path = fixture.control_directory / \
                "durable-owner-retirement-receipt.v4.json"
            root_path = fixture.control_directory / \
                "root-cleanup-receipt.v4.json"
            retirement = TERMINAL.strict_json(
                retirement_path.read_bytes(), "TEST")
            root_receipt = TERMINAL.strict_json(root_path.read_bytes(), "TEST")
            mutations = (
                ("ack", lambda item: item.__setitem__(
                    "terminal_acknowledged", False)),
                ("purge", lambda item: item.__setitem__(
                    "hsl_owner_purged", False)),
                ("flat", lambda item: item.__setitem__(
                    "broker_flat_proven", False)),
                ("ack-state", lambda item: item[
                    "terminal_ack_result"].__setitem__(
                        "paper_finalization_state", "AUDIT_SEALED")),
                ("late-fill", lambda item: item[
                    "terminal_ack_result"].__setitem__(
                        "broker_position_quantity", "1")),
                ("runtime-witness", lambda item: item[
                    "terminal_ack_result"].__setitem__(
                        "terminal_runtime_verified", False)),
                ("not-replayed", lambda item: item[
                    "terminal_ack_result"].__setitem__(
                        "terminal_replay", False)),
                ("mutation-capable-retained-bearer", lambda item: item.__setitem__(
                    "retained_root_recovery_bearer_mutation_authority", True)),
            )
            for name, mutation in mutations:
                changed = deepcopy(retirement)
                mutation(changed)
                with self.subTest(inner=name), self.assertRaises(
                        TERMINAL.ProverError):
                    TERMINAL._validate_hsl8_retirement(
                        changed, root_receipt)

            for name in (
                    "root-cleanup-receipt.v1.json",
                    "root-cleanup-receipt.v2.json",
                    "durable-owner-retirement-receipt.v1.json",
                    "durable-owner-retirement-receipt.v2.json"):
                legacy = fixture.control_directory / name
                legacy.write_bytes(b"{}\n")
                legacy.chmod(0o600)
                with self.subTest(legacy=name), self.assertRaisesRegex(
                        TERMINAL.ProverError,
                        "TERMINAL_PROVER_LEGACY_NORMAL_EVIDENCE_REJECTED"):
                    prove()
                legacy.unlink()

            retirement["terminal_flat_proof_kind"] = \
                "GENERATION_ABSENT_AFTER_REVOKE"
            retirement_raw = self._reseal(retirement)
            retirement_path.write_bytes(retirement_raw)
            root_receipt[
                "durable_owner_retirement_receipt_file_sha256"] = \
                TERMINAL.sha(retirement_raw)
            root_receipt[
                "durable_owner_retirement_receipt_body_sha256"] = \
                retirement["body_sha256"]
            root_path.write_bytes(self._reseal(root_receipt))
            with self.assertRaisesRegex(
                    TERMINAL.ProverError,
                    "TERMINAL_PROVER_OWNER_EVIDENCE_INVALID"):
                prove()

    def test_terminal_prover_contract_sets_match_v3_producer(self):
        self.assertEqual(
            TERMINAL.NORMAL_ROOT_RECEIPT_FIELDS, FINALIZER.RECEIPT_FIELDS)
        self.assertEqual(
            TERMINAL.OWNER_RETIREMENT_FIELDS,
            FINALIZER.OWNER_RETIREMENT_FIELDS)
        self.assertEqual(
            TERMINAL.RECOVERY_QUERY_RESULT_FIELDS,
            FINALIZER.RECOVERY_QUERY_RESULT_FIELDS)
        self.assertEqual(
            TERMINAL.FINALIZATION_RESULT_FIELDS,
            FINALIZER.FINALIZATION_RESULT_FIELDS)
        self.assertEqual(
            TERMINAL.FINALIZATION_RECEIPT_KEYS,
            FINALIZER.FINALIZATION_RECEIPT_KEYS)
        self.assertEqual(
            TERMINAL.TERMINAL_ACK_RESULT_FIELDS,
            FINALIZER.TERMINAL_ACK_RESULT_FIELDS)
        self.assertEqual(
            TERMINAL.TERMINAL_ACK_RECEIPT_KEYS,
            FINALIZER.TERMINAL_ACK_RECEIPT_KEYS)

    def test_terminal_prover_rejects_owner_residue_after_hsl8_ack(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            owner_root = root / "owner-root"
            owner_root.mkdir(mode=0o700)
            authority_path = owner_root / "session.token.authority.json"
            revoke_path = owner_root / "session.token.revoke-token"
            intent_path = owner_root / "session.token.owner-may-exist.v1.json"
            with mock.patch.object(
                    FINALIZER, "OWNER_AUTHORITY_ROOT", owner_root), \
                    mock.patch.object(
                        FINALIZER, "OWNER_AUTHORITY_PATH", authority_path), \
                    mock.patch.object(
                        FINALIZER, "OWNER_REVOKE_PATH", revoke_path), \
                    mock.patch.object(
                        FINALIZER, "OWNER_INTENT_PATH", intent_path):
                fixture = Fixture(root)
                self._run(fixture, FakeControl(fixture.directory))
            residue = owner_root / "session.token.revoke-token"
            retirement_path = fixture.control_directory / \
                "durable-owner-retirement-receipt.v4.json"
            terminal_witness = hpe1_from_retirement(retirement_path)
            residue.write_bytes(b"retained\n")
            residue.chmod(0o600)
            unexpected = owner_root / "unexpected-authority"
            unexpected.write_bytes(b"residue\n")
            unexpected.chmod(0o600)
            original = TERMINAL.stable_read

            def stable(path, **parameters):
                candidate = Path(path)
                if candidate == TERMINAL.TERMINAL_EVIDENCE_PATH:
                    return terminal_witness
                if candidate.is_relative_to(root):
                    if parameters.get("uid") == 0:
                        parameters["uid"] = os.getuid()
                    if parameters.get("gid") == 0:
                        parameters["gid"] = os.getgid()
                return original(candidate, **parameters)

            with mock.patch.object(
                    TERMINAL, "CONTROL_ROOT", fixture.control_root), \
                    mock.patch.object(TERMINAL, "OWNER_ROOT", owner_root), \
                    mock.patch.object(
                        TERMINAL, "stable_read", side_effect=stable), \
                    self.assertRaisesRegex(
                        TERMINAL.ProverError,
                        "TERMINAL_PROVER_OWNER_CREDENTIAL_REMAINS"):
                TERMINAL._owner_evidence(
                    "campaign-1", "cycle-1", "RETIRED")

    def test_outer_completion_purge_is_durable_and_response_loss_idempotent(
            self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            owner_root = root / "owner-root"
            owner_root.mkdir(mode=0o700)
            bearer = b"0" * 64 + b"\n"
            bearer_path = owner_root / "session.token.revoke-token"
            bearer_path.write_bytes(bearer)
            bearer_path.chmod(0o600)
            fixture = Fixture(root)
            owner = fixture.handoff["session_owner_reference"]
            owner["revoke_bearer_path"] = str(bearer_path)
            owner["revoke_bearer_sha256"] = FINALIZER.sha(bearer)
            owner["token_sha256"] = FINALIZER.sha(bearer)
            authority_path = owner_root / "session.token.authority.json"
            intent_path = owner_root / "session.token.owner-may-exist.v1.json"
            owner["authority_path"] = str(authority_path)
            fixture.value.request["session_owner_reference_sha256"] = \
                FINALIZER.canonical_sha(owner)
            with mock.patch.object(
                    FINALIZER, "OWNER_AUTHORITY_ROOT", owner_root), \
                    mock.patch.object(
                        FINALIZER, "OWNER_AUTHORITY_PATH", authority_path), \
                    mock.patch.object(
                        FINALIZER, "OWNER_REVOKE_PATH", bearer_path), \
                    mock.patch.object(
                        FINALIZER, "OWNER_INTENT_PATH", intent_path):
                self._run(fixture, FakeControl(fixture.directory))
            completion = TERMINAL._seal_body({
                "schema": TERMINAL.OUTER_COMPLETION_SCHEMA, "version": 4,
                "status": "P2_SUCCESS", "campaign_id": "campaign-1",
                "domain_id": "alpha", "cycle_id": "cycle-1",
                "durable_owner_status": "RETIRED", "broker_deny_all": True,
                "mutation_credentials_destroyed": True,
                "credentials_destroyed_scope":
                    "PEER_MUTATION_TOKEN_AND_AUTHORITY_ONLY",
                "retained_root_recovery_bearer_count": 1,
                "retained_root_recovery_bearer_path": str(bearer_path),
                "retained_root_recovery_bearer_sha256": FINALIZER.sha(bearer),
                "retained_root_recovery_bearer_mutation_authority": False,
                "authority_granted": False,
            })
            completion_path = fixture.control_directory / \
                "cycle-completion-receipt.v4.json"
            completion_path.write_bytes(TERMINAL.canonical_json(completion))
            completion_path.chmod(0o600)
            retirement = TERMINAL.strict_json(
                (fixture.control_directory /
                 "durable-owner-retirement-receipt.v4.json").read_bytes(),
                "TEST")
            terminal_witness = hpe1_from_retirement(
                fixture.control_directory /
                "durable-owner-retirement-receipt.v4.json")
            replay = TERMINAL.canonical_json(
                retirement["terminal_ack_result"])
            completed = subprocess.CompletedProcess([], 0, replay, b"")
            original = TERMINAL.stable_read

            def stable(path, **parameters):
                candidate = Path(path)
                if candidate == TERMINAL.TERMINAL_EVIDENCE_PATH:
                    return terminal_witness
                if candidate.is_relative_to(root):
                    if parameters.get("uid") == 0:
                        parameters["uid"] = os.getuid()
                    if parameters.get("gid") == 0:
                        parameters["gid"] = os.getgid()
                return original(candidate, **parameters)

            with mock.patch.object(
                    TERMINAL, "CONTROL_ROOT", fixture.control_root), \
                    mock.patch.object(TERMINAL, "OWNER_ROOT", owner_root), \
                    mock.patch.object(
                        TERMINAL, "stable_read", side_effect=stable), \
                    mock.patch.object(
                        TERMINAL.subprocess, "run", return_value=completed) as run, \
                    mock.patch.object(TERMINAL.os, "fchown"):
                first = TERMINAL.purge_owner_after_completion(
                    "campaign-1", "cycle-1")
                self.assertEqual(first["status"], "OWNER_BEARER_PURGED")
                self.assertFalse(bearer_path.exists())
                self.assertEqual(run.call_count, 2)
                # A lost caller response reopens the exact durable receipt;
                # it neither needs nor can consume a new bearer.
                second = TERMINAL.purge_owner_after_completion(
                    "campaign-1", "cycle-1")
                self.assertEqual(second, first)
                self.assertEqual(run.call_count, 2)
                receipt_path = fixture.control_directory / \
                    "outer-owner-purge-receipt.v1.json"
                durable = receipt_path.read_bytes()
                receipt_path.unlink()
                # Crash after unlink but before receipt publication resumes
                # from the fsynced replay intent with byte-identical output.
                third = TERMINAL.purge_owner_after_completion(
                    "campaign-1", "cycle-1")
                self.assertEqual(third, first)
                self.assertEqual(receipt_path.read_bytes(), durable)
                self.assertEqual(run.call_count, 2)

    def test_completion_after_expiry_keeps_wal_and_publishes_no_receipt(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Fixture(Path(temporary))
            fixture.value.request["expires_at_ms"] = 2
            with self.assertRaisesRegex(
                    FINALIZER.FinalizerError,
                    "ROOT_FINALIZER_COMPLETION_EXPIRED"):
                self._run(fixture, FakeControl(fixture.directory))
            self.assertTrue(fixture.config.wal_path.exists())
            self.assertFalse((fixture.control_directory /
                              "root-cleanup-receipt.v4.json").exists())

    def test_peer_attestation_binds_unit_process_and_credential_image(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Fixture(Path(temporary))
            credential = b"reviewed executor image\n"
            fixture.value.images["executor"]["file_sha256"] = \
                FINALIZER.sha(credential)
            status = (
                "Name:\tpython3.12\n"
                "Uid:\t2104\t2104\t2104\t2104\n"
                "Gid:\t2104\t2104\t2104\t2104\n"
                "Groups:\t2104\n").encode("ascii")
            cmdline = b"\0".join((
                b"/usr/bin/python3.12", b"-I", b"-S",
                FINALIZER.PEER_CREDENTIAL_IMAGE.encode("ascii"))) + b"\0"

            def process(path, _maximum):
                if path.name == "status":
                    return status
                if path.name == "cgroup":
                    return f"0::{FINALIZER.PEER_CGROUP}\n".encode("ascii")
                return cmdline

            completed = subprocess.CompletedProcess(
                [], 0, stdout=(
                    "LoadState=loaded\nMainPID=4242\nControlPID=0\n"
                    "User=hepta-agent-alpha\n"
                    "Group=hepta-agent-alpha\nActiveState=active\n"
                    "SubState=running\nControlGroup=" +
                    FINALIZER.PEER_CGROUP + "\n"), stderr="")
            with ExitStack() as stack:
                stack.enter_context(mock.patch.object(
                    FINALIZER, "_proc_bytes", side_effect=process))
                stack.enter_context(mock.patch.object(
                    FINALIZER, "stable_read", return_value=credential))
                FINALIZER.attest_peer(
                    4242, fixture.value,
                    command=lambda *_args, **_kwargs: completed)

    def test_root_emergency_main_and_exec_stop_post_are_exactly_attested(self):
        with tempfile.TemporaryDirectory() as temporary:
            self._attest_root(Fixture(Path(temporary), emergency=True))
        with tempfile.TemporaryDirectory() as temporary:
            self._attest_root(
                Fixture(Path(temporary), emergency=True), stop_post=True)

    def test_root_requester_is_emergency_only_and_requires_exact_ids(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Fixture(Path(temporary))
            with self.assertRaisesRegex(
                    FINALIZER.FinalizerError,
                    "ROOT_FINALIZER_PEER_CREDENTIAL_INVALID"):
                FINALIZER.attest_requester((4242, 0, 0), fixture.value)
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Fixture(Path(temporary), emergency=True)
            for peer in ((4242, 0, 1), (4242, 1, 0), (1, 0, 0)):
                with self.subTest(peer=peer), self.assertRaisesRegex(
                        FINALIZER.FinalizerError,
                        "ROOT_FINALIZER_PEER_CREDENTIAL_INVALID"):
                    FINALIZER.attest_requester(peer, fixture.value)

    def test_root_emergency_rejects_unit_pid_state_and_cgroup_substitution(self):
        mutations = (
            (False, {"MainPID": "4343"}),
            (False, {"ControlPID": "4343"}),
            (False, {"SubState": "exited"}),
            (False, {"ActiveState": "inactive"}),
            (False, {"ControlGroup": "/system.slice/substitute.service"}),
            (False, {"LoadState": "not-found"}),
            (True, {"MainPID": "4242"}),
            (True, {"ControlPID": "4343"}),
            (True, {"SubState": "running"}),
            (True, {"ActiveState": "active"}),
        )
        for stop_post, changes in mutations:
            with self.subTest(stop_post=stop_post, changes=changes), \
                    tempfile.TemporaryDirectory() as temporary:
                fixture = Fixture(Path(temporary), emergency=True)
                with self.assertRaisesRegex(
                        FINALIZER.FinalizerError,
                        "ROOT_FINALIZER_PEER_UNIT_INVALID"):
                    self._attest_root(
                        fixture, stop_post=stop_post,
                        property_changes=changes)

    def test_root_emergency_rejects_proc_identity_cmdline_and_cgroup(self):
        bad_status = (
            "Uid:\t0\t0\t0\t0\nGid:\t0\t0\t0\t0\n"
            "Groups:\t0 2104\n").encode("ascii")
        mutations = (
            ("cgroup", b"0::/system.slice/substitute.service\n",
             "ROOT_FINALIZER_PEER_CGROUP_INVALID"),
            ("status", bad_status, "ROOT_FINALIZER_PEER_PROCESS_INVALID"),
            ("cmdline", b"/usr/bin/python3.12\0-I\0-S\0wrong\0",
             "ROOT_FINALIZER_PEER_PROCESS_INVALID"),
        )
        for name, raw, reason in mutations:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                fixture = Fixture(Path(temporary), emergency=True)
                with self.assertRaisesRegex(FINALIZER.FinalizerError, reason):
                    self._attest_root(
                        fixture, process_changes={name: raw})

    def test_root_emergency_rejects_missing_swapped_or_changed_images(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Fixture(Path(temporary), emergency=True)
            del fixture.value.images["terminal-prover"]
            with self.assertRaisesRegex(
                    FINALIZER.FinalizerError,
                    "ROOT_FINALIZER_PEER_IMAGE_INVALID"):
                self._attest_root(fixture)
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Fixture(Path(temporary), emergency=True)
            fixture.value.images["root-coordinator"]["path"] = \
                FINALIZER.ROOT_EMERGENCY_CLOSER_IMAGE_PATH
            with self.assertRaisesRegex(
                    FINALIZER.FinalizerError,
                    "ROOT_FINALIZER_PEER_IMAGE_INVALID"):
                self._attest_root(fixture)
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Fixture(Path(temporary), emergency=True)
            with self.assertRaisesRegex(
                    FINALIZER.FinalizerError,
                    "ROOT_FINALIZER_PEER_IMAGE_INVALID"):
                self._attest_root(fixture, credential_changes={
                    "hepta-p1-paper-canary-root-coordinator.py": b"changed\n",
                })

    def test_owner_reference_paths_are_exact_and_not_substitutable(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Fixture(Path(temporary))
            owner = fixture.value.handoff["session_owner_reference"]
            for field in ("token_path", "authority_path",
                          "revoke_bearer_path"):
                changed = deepcopy(owner)
                changed[field] = changed[field] + ".substitute"
                with self.subTest(field=field), self.assertRaisesRegex(
                        FINALIZER.FinalizerError,
                        "ROOT_FINALIZER_SESSION_OWNER_INVALID"):
                    FINALIZER._validate_owner_reference(changed)

    def test_owner_material_rejects_mode_uid_gid_symlink_and_hardlink(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "owner-secret"
            path.write_bytes(b"secret\n")
            path.chmod(0o600)
            uid, gid = os.getuid(), os.getgid()
            self.assertEqual(FINALIZER.stable_read(
                path, uid=uid, gid=gid, mode=0o600), b"secret\n")
            cases = (
                ("mode", {"uid": uid, "gid": gid, "mode": 0o400}),
                ("uid", {"uid": uid + 1, "gid": gid, "mode": 0o600}),
                ("gid", {"uid": uid, "gid": gid + 1, "mode": 0o600}),
            )
            for name, parameters in cases:
                with self.subTest(name=name), self.assertRaisesRegex(
                        FINALIZER.FinalizerError,
                        "ROOT_FINALIZER_ARTIFACT_METADATA_INVALID"):
                    FINALIZER.stable_read(path, **parameters)
            hardlink = root / "owner-secret-hardlink"
            os.link(path, hardlink)
            with self.assertRaisesRegex(
                    FINALIZER.FinalizerError,
                    "ROOT_FINALIZER_ARTIFACT_METADATA_INVALID"):
                FINALIZER.stable_read(path, uid=uid, gid=gid, mode=0o600)
            hardlink.unlink()
            symlink = root / "owner-secret-symlink"
            symlink.symlink_to(path)
            with self.assertRaisesRegex(
                    FINALIZER.FinalizerError,
                    "ROOT_FINALIZER_ARTIFACT_METADATA_INVALID"):
                FINALIZER.stable_read(
                    symlink, uid=uid, gid=gid, mode=0o600)

    def test_owner_unlink_retry_fsyncs_absence_after_crash_seam(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "owner-secret"
            raw = b"secret\n"
            path.write_bytes(raw)
            path.chmod(0o600)
            with mock.patch.object(
                    FINALIZER, "_fsync_directory",
                    side_effect=OSError("injected fsync failure")):
                with self.assertRaisesRegex(
                        FINALIZER.FinalizerError,
                        "ROOT_FINALIZER_OWNER_MATERIAL_DESTROY_FAILED"):
                    FINALIZER._unlink_bound(
                        path, FINALIZER.sha(raw), uid=os.getuid(),
                        gid=os.getgid(), modes=frozenset({0o600}),
                        allow_absent=True)
            self.assertFalse(path.exists())
            with mock.patch.object(FINALIZER, "_fsync_directory") as sync:
                FINALIZER._unlink_bound(
                    path, FINALIZER.sha(raw), uid=os.getuid(), gid=os.getgid(),
                    modes=frozenset({0o600}), allow_absent=True)
            sync.assert_called_once_with(path.parent)

    def test_production_normal_retirement_requires_acked_hsl8_audit(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Fixture(Path(temporary))
            module = mock.Mock()
            with mock.patch.object(
                    FINALIZER, "_owner_material",
                    return_value=(b"authority\n", b"bearer\n", b"token\n")), \
                    mock.patch.object(FINALIZER.subprocess, "run") as command, \
                    self.assertRaisesRegex(
                        FINALIZER.FinalizerError,
                        "ROOT_FINALIZER_OWNER_FINALIZATION_REQUIRED"):
                FINALIZER.ProductionControl(module).prepare_owner_transition(
                    fixture.handoff, command_id="cleanup-1",
                    emergency=False,
                    artifact_directory=fixture.control_directory,
                    completed_at_ms=1_000)
            command.assert_not_called()

            finalized = FINALIZER._owner_finalization_intent(
                fixture.handoff, "cleanup-1")
            finalized["recovery_query_result"] = recovery_query_result(
                fixture.handoff, finalized)
            finalized["state"] = "RECOVERY_ONLY"
            finalized["finalization_result"] = finalization_result(
                fixture.handoff, finalized, "AUDIT_SEALED")
            finalized["state"] = "AUDIT_SEALED"
            finalized["terminal_ack_result"] = terminal_ack_result(
                fixture.handoff, finalized,
                finalized["finalization_result"])
            finalized["state"] = "ACKED"
            ack_receipt, _ack_raw = FINALIZER._parse_terminal_ack_receipt(
                finalized["terminal_ack_result"]["finalization_receipt"])
            terminal_witness = hpe1_from_receipt_values(ack_receipt)[0]
            with mock.patch.object(
                    FINALIZER, "_owner_material",
                    return_value=(b"authority\n", b"bearer\n", b"token\n")), \
                    mock.patch.object(
                        FINALIZER, "stable_read", return_value=terminal_witness), \
                    self.assertRaisesRegex(
                        FINALIZER.FinalizerError,
                        "ROOT_FINALIZER_OWNER_TERMINAL_REPLAY_REQUIRED"):
                FINALIZER.ProductionControl(
                    module).prepare_owner_transition(
                        fixture.handoff, command_id="cleanup-1",
                        emergency=False,
                        artifact_directory=fixture.control_directory,
                        completed_at_ms=1_000, finalization=finalized)
            finalized["terminal_ack_result"] = terminal_ack_result(
                fixture.handoff, finalized,
                finalized["finalization_result"], replay=True)
            with mock.patch.object(
                    FINALIZER, "_owner_material",
                    return_value=(b"authority\n", b"bearer\n", b"token\n")), \
                    mock.patch.object(
                        FINALIZER, "stable_read", return_value=terminal_witness):
                transition = FINALIZER.ProductionControl(
                    module).prepare_owner_transition(
                        fixture.handoff, command_id="cleanup-1",
                        emergency=False,
                        artifact_directory=fixture.control_directory,
                        completed_at_ms=1_000, finalization=finalized)
            self.assertEqual(
                transition["path"], str(fixture.control_directory /
                                        "durable-owner-retirement-receipt.v4.json"))
            document = transition["document"]
            self.assertEqual(document["version"], 4)
            self.assertTrue(document["hsl_owner_purged"])
            self.assertTrue(document["broker_flat_proven"])
            self.assertEqual(
                document["durable_hsl_audit"],
                "HSL8_POST_CUTOFF_SIGNED_TERMINAL_ACK_V3")
            self.assertNotEqual(
                document["finalization_result"]["finalization_receipt"],
                document["terminal_ack_result"]["finalization_receipt"])
            self.assertEqual(
                document["terminal_ack_receipt"],
                document["terminal_ack_result"]["finalization_receipt"])
            self.assertTrue(document["terminal_ack_result"]["terminal_replay"])

    def test_production_finalization_stages_use_exact_ids_and_timeouts(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Fixture(Path(temporary))
            module = mock.Mock()
            control = FINALIZER.ProductionControl(module)
            intent = FINALIZER._owner_finalization_intent(
                fixture.handoff, "cleanup-1")
            query = recovery_query_result(fixture.handoff, intent)
            recovery_only = deepcopy(intent)
            recovery_only["recovery_query_result"] = query
            recovery_only["state"] = "RECOVERY_ONLY"
            sealed = finalization_result(
                fixture.handoff, recovery_only, "AUDIT_SEALED")
            audit_sealed = deepcopy(recovery_only)
            audit_sealed["finalization_result"] = sealed
            audit_sealed["state"] = "AUDIT_SEALED"
            acknowledged = terminal_ack_result(
                fixture.handoff, audit_sealed, sealed)
            replayed = terminal_ack_result(
                fixture.handoff, audit_sealed, sealed, replay=True)
            ack_receipt, _ack_raw = FINALIZER._parse_terminal_ack_receipt(
                acknowledged["finalization_receipt"])
            terminal_witness = hpe1_from_receipt_values(ack_receipt)[0]
            prepared = deepcopy(sealed)
            prepared["reason_code"] = "PAPER_TERMINAL_WITNESS_PREPARED"
            responses = [query, sealed, prepared, acknowledged, replayed]
            completed = [
                subprocess.CompletedProcess(
                    [], 0, stdout=FINALIZER.canonical_json(item).decode(
                        "ascii"), stderr="")
                for item in responses
            ]
            with mock.patch.object(
                    FINALIZER, "_owner_material",
                    return_value=(b"authority\n", b"bearer\n", b"token\n")), \
                    mock.patch.object(
                        FINALIZER, "_owner_terminal_bearer",
                        return_value=b"0" * 64 + b"\n"), \
                    mock.patch.object(
                        FINALIZER, "stable_read",
                        side_effect=lambda path, **_kwargs: terminal_witness
                        if Path(path) == FINALIZER.TERMINAL_EVIDENCE_PATH
                        else b"fixture\n"), \
                    mock.patch.object(
                        FINALIZER.subprocess, "run",
                        side_effect=completed) as command:
                state = control.advance_owner_finalization(
                    fixture.handoff, intent)
                state = control.advance_owner_finalization(
                    fixture.handoff, state)
                state = control.advance_owner_finalization(
                    fixture.handoff, state)
                control.verify_owner_terminal_ack(fixture.handoff, state)
            self.assertEqual(state["state"], "ACKED")
            self.assertEqual(command.call_count, 5)
            calls = command.call_args_list
            self.assertIn("--require-paper-finalization", calls[0].args[0])
            self.assertIn("paper-finalize", calls[1].args[0])
            self.assertIn("paper-terminal-witness-prepare", calls[2].args[0])
            self.assertIn("paper-terminal-witness-ack", calls[3].args[0])
            self.assertIn("paper-terminal-witness-ack", calls[4].args[0])
            self.assertNotIn("revoke", [item for call in calls
                                        for item in call.args[0]])
            self.assertEqual(
                [call.kwargs["timeout"] for call in calls],
                [15, 120, 120, 30, 30])
            self.assertEqual(
                [call.args[0][call.args[0].index("--io-timeout-ms") + 1]
                 for call in calls],
                ["10000", "110000", "110000", "25000", "25000"])
            self.assertEqual(
                calls[3].args[0][calls[3].args[0].index(
                    "--receipt-sha256") + 1],
                sealed["finalization_receipt_sha256"])
            self.assertEqual(
                calls[4].args[0][calls[4].args[0].index(
                    "--receipt-sha256") + 1],
                sealed["finalization_receipt_sha256"])
            self.assertEqual(state["recovery_id"], intent["recovery_id"])
            self.assertEqual(state["finalization_id"],
                             intent["finalization_id"])

    def test_production_replay_rejects_changed_terminal_receipt_or_latch_loss(
            self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Fixture(Path(temporary))
            control = FINALIZER.ProductionControl(mock.Mock())
            finalized = FINALIZER._owner_finalization_intent(
                fixture.handoff, "cleanup-1")
            finalized["recovery_query_result"] = recovery_query_result(
                fixture.handoff, finalized)
            finalized["state"] = "RECOVERY_ONLY"
            preliminary = finalization_result(
                fixture.handoff, finalized, "AUDIT_SEALED")
            finalized["finalization_result"] = preliminary
            finalized["state"] = "AUDIT_SEALED"
            finalized["terminal_ack_result"] = terminal_ack_result(
                fixture.handoff, finalized, preliminary)
            finalized["state"] = "ACKED"

            changed_receipt = terminal_ack_result(
                fixture.handoff, finalized, preliminary, replay=True,
                receipt_overrides={"terminalization_generation": "2"},
                result_overrides={"terminalization_generation": 2})
            latch_loss = terminal_ack_result(
                fixture.handoff, finalized, preliminary, replay=True,
                result_overrides={"terminal_external_latch_loaded": False})
            for name, response in (
                    ("changed-receipt", changed_receipt),
                    ("latch-loss", latch_loss)):
                completed = subprocess.CompletedProcess(
                    [], 0,
                    stdout=FINALIZER.canonical_json(response).decode("ascii"),
                    stderr="")
                with self.subTest(case=name), mock.patch.object(
                        FINALIZER, "_owner_terminal_bearer",
                        return_value=b"0" * 64 + b"\n"), \
                        mock.patch.object(
                            FINALIZER, "stable_read", return_value=b"HPE1\n"), \
                        mock.patch.object(
                            FINALIZER.subprocess, "run",
                            return_value=completed), \
                        self.assertRaisesRegex(
                            FINALIZER.FinalizerError,
                            "ROOT_FINALIZER_OWNER_TERMINAL_(?:REPLAY|ACK)_INVALID"):
                    control.verify_owner_terminal_ack(
                        fixture.handoff, finalized)

    def test_production_emergency_owner_requires_durable_finalization_flag(
            self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Fixture(Path(temporary), emergency=True)
            response = {
                "accepted": True,
                "reason_code": "RECOVERY_QUERY_PROVEN_RECOVERY_ONLY",
                "lease_generation": 7,
                "authoritative_command_status": True,
                "command_id": "cleanup-1",
                "command_status": "rejected",
                "command_reason_code": "EXECUTION_COMMAND_REJECTED",
                "order_id": -1,
                "recovery_only": True,
                "paper_finalization_required": True,
                "owner_fenced": False,
                "execution_service_epoch": "epoch-1",
                "execution_service_fencing_generation": 7,
                "recovery_expires_at_ms": 9_000_000_000_000_000,
                "owner_audit_authoritative": True,
                "owner_audit_complete": True,
                "owner_active_order_count": 0,
                "owner_uncertain_command_count": 0,
                "broker_connection_epoch": 1,
                "broker_active_generation": 1,
                "broker_terminal_generation": 1,
                "owner_account": "DU123",
                "owner_execution_domain": "PAPER:alpha",
            }
            completed = subprocess.CompletedProcess(
                [], 0, stdout=FINALIZER.canonical_json(response).decode(
                    "ascii"), stderr="")
            module = mock.Mock()
            with mock.patch.object(
                    FINALIZER, "_owner_material",
                    return_value=(b"authority\n", b"bearer\n", b"token\n")), \
                    mock.patch.object(
                        FINALIZER.subprocess, "run",
                        return_value=completed) as command:
                transition = FINALIZER.ProductionControl(
                    module).prepare_owner_transition(
                        fixture.handoff, command_id="cleanup-1",
                        emergency=True,
                        artifact_directory=fixture.control_directory,
                        completed_at_ms=1_000)
            arguments = command.call_args.args[0]
            self.assertEqual(arguments[-1], "--require-paper-finalization")
            self.assertNotIn("revoke", arguments)
            self.assertEqual(command.call_args.kwargs["timeout"], 15)
            self.assertIs(
                transition["document"]["paper_finalization_required"], True)

            legacy = dict(response)
            legacy.pop("paper_finalization_required")
            legacy_completed = subprocess.CompletedProcess(
                [], 0, stdout=FINALIZER.canonical_json(legacy).decode(
                    "ascii"), stderr="")
            with mock.patch.object(
                    FINALIZER, "_owner_material",
                    return_value=(b"authority\n", b"bearer\n", b"token\n")), \
                    mock.patch.object(
                        FINALIZER.subprocess, "run",
                        return_value=legacy_completed) as command, \
                    self.assertRaisesRegex(
                        FINALIZER.FinalizerError,
                        "ROOT_FINALIZER_OWNER_RECOVERY_INVALID"):
                FINALIZER.ProductionControl(module).prepare_owner_transition(
                    fixture.handoff, command_id="cleanup-1",
                    emergency=True,
                    artifact_directory=fixture.control_directory,
                    completed_at_ms=1_000)
            self.assertEqual(command.call_count, 1)
            self.assertNotIn("revoke", command.call_args.args[0])

    def test_generated_executor_artifacts_validate_end_to_end(self):
        with tempfile.TemporaryDirectory() as temporary:
            generated = self._generated_normal_artifacts(Path(temporary))
            validated = self._validate_generated(generated)
            self.assertEqual(
                validated.request["schema"], FINALIZER.REQUEST_SCHEMA)
            self.assertEqual(
                validated.response_bundle["final_roles"],
                list(FINALIZER.PRE_CLEANUP_FINAL_ROLES))
            self.assertGreater(
                validated.request["expires_at_ms"],
                validated.handoff["expires_at_ms"])

    def test_journal_sequence_rejected_even_when_outer_digests_are_rebound(self):
        with tempfile.TemporaryDirectory() as temporary:
            generated = self._generated_normal_artifacts(Path(temporary))
            records = generated["journal_path"].read_bytes().splitlines()
            call = FINALIZER.strict_json(records[1] + b"\n", "TEST")
            call["sequence"] = 2
            records[1] = FINALIZER.canonical_json(call).rstrip(b"\n")
            journal_raw = b"\n".join(records) + b"\n"
            self._rebind_normal_request(
                generated, journal_raw=journal_raw)
            with self.assertRaisesRegex(
                    FINALIZER.FinalizerError,
                    "ROOT_FINALIZER_JOURNAL_INVALID"):
                self._validate_generated(generated)

    def test_response_bundle_missing_or_role_swapped_is_rejected(self):
        mutations = (
            lambda bundle: bundle["responses"].pop(),
            lambda bundle: bundle["responses"].__setitem__(
                slice(0, 2), list(reversed(bundle["responses"][:2]))),
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation), \
                    tempfile.TemporaryDirectory() as temporary:
                generated = self._generated_normal_artifacts(Path(temporary))
                self._rebind_normal_request(
                    generated, mutate_bundle=mutation)
                with self.assertRaisesRegex(
                        FINALIZER.FinalizerError,
                        "ROOT_FINALIZER_RESPONSE_BUNDLE_INVALID"):
                    self._validate_generated(generated)

    def test_handoff_image_bundle_rejects_missing_role_swap_and_metadata(self):
        def missing(images):
            images.pop()

        def swapped(images):
            images[0]["role"] = images[1]["role"]

        def bad_path(images):
            next(item for item in images
                 if item["role"] == "root-finalizer")["path"] += ".wrong"

        def bad_mode(images):
            images[0]["mode"] = 0o775

        def bad_uid(images):
            images[0]["uid"] = 2104

        def bad_gid(images):
            images[0]["gid"] = 2104

        def bad_nlink(images):
            images[0]["nlink"] = 2

        for mutation in (
                missing, swapped, bad_path, bad_mode, bad_uid, bad_gid,
                bad_nlink):
            with self.subTest(mutation=mutation.__name__), \
                    tempfile.TemporaryDirectory() as temporary:
                generated = self._generated_normal_artifacts(Path(temporary))
                handoff = FINALIZER.strict_json(
                    generated["handoff_path"].read_bytes(), "TEST")
                mutation(handoff["installed_images"])
                handoff["installed_images_sha256"] = FINALIZER.canonical_sha(
                    handoff["installed_images"])
                generated["handoff_path"].write_bytes(self._reseal(handoff))
                with self.assertRaises(FINALIZER.FinalizerError):
                    self._validate_generated(generated)

    def test_systemd_listener_is_outer_coordinator_owned(self):
        service = SERVICE.read_text(encoding="ascii")
        socket = SOCKET.read_text(encoding="ascii")
        self.assertIn(
            "ExecStopPost=/usr/bin/python3.12 -I -S "
            "${CREDENTIALS_DIRECTORY}/"
            "hepta-p1-paper-canary-finalizer.py fail-close-on-exit",
            service)
        self.assertNotIn("recover-listener", service)
        self.assertNotIn("stop-listener", service)
        self.assertNotIn("ReadWritePaths=/run/hepta\n", service)
        self.assertNotIn("ReadWritePaths=/var/lib/hepta/p1-paper-canary\n",
                         service)
        self.assertIn("TimeoutStartSec=5min", service)
        self.assertEqual(FINALIZER.ROOT_CLEANUP_TIMEOUT_MS, 240_000)
        self.assertLess(
            FINALIZER.SESSIONCTL_IO_TIMEOUT_MS,
            FINALIZER.SESSIONCTL_SUBPROCESS_TIMEOUT_SECONDS * 1_000)
        self.assertLess(
            FINALIZER.SESSIONCTL_SUBPROCESS_TIMEOUT_SECONDS * 1_000,
            FINALIZER.ROOT_CLEANUP_TIMEOUT_MS)
        self.assertIn("Accept=yes", socket)
        self.assertIn("MaxConnections=1", socket)
        self.assertNotIn("Service=", socket)

    def test_exec_stop_post_is_request_independent_monotonic_deny_all(self):
        with tempfile.TemporaryDirectory() as temporary:
            control = FakeControl(Path(temporary))
            with mock.patch("os.geteuid", return_value=0), \
                    mock.patch("os.getegid", return_value=0):
                self.assertEqual(FINALIZER.fail_close_on_exit(control), 0)
            self.assertEqual(control.trace, [
                "kill-switches", "stop-guardian", "fail-close",
                "prove-exit-deny",
            ])
            self.assertTrue(control.deny_all)

            # A second stop path can only repeat the same fail-close actions.
            with mock.patch("os.geteuid", return_value=0), \
                    mock.patch("os.getegid", return_value=0):
                self.assertEqual(FINALIZER.fail_close_on_exit(control), 0)
            self.assertTrue(control.deny_all)

    def test_exec_stop_post_failure_is_visible_to_systemd(self):
        with tempfile.TemporaryDirectory() as temporary:
            control = FakeControl(Path(temporary))
            control.prove_exit_deny = lambda: False
            with mock.patch("os.geteuid", return_value=0), \
                    mock.patch("os.getegid", return_value=0):
                self.assertEqual(FINALIZER.fail_close_on_exit(control), 1)

    def test_credential_loader_executes_stable_credential_bytes_not_path(self):
        source = (
            b"#!/usr/bin/env python3\n"
            b"GUARDIAN_UNIT='guardian.service'\n")
        with mock.patch.dict("os.environ", {"CREDENTIALS_DIRECTORY": "/cred"}), \
                mock.patch.object(FINALIZER, "stable_read", return_value=source):
            control = FINALIZER._load_control()
        self.assertEqual(control.module.GUARDIAN_UNIT, "guardian.service")
        self.assertEqual(
            control.module.__file__,
            "<systemd-credential:hepta-local-paper-control.py>")


if __name__ == "__main__":
    unittest.main()
