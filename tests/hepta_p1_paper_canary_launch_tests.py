#!/usr/bin/env python3

from __future__ import annotations

from copy import deepcopy
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


JOINER = load(
    "hepta_p1_paper_canary_launch_joiner_test_module",
    ROOT / "scripts/hepta_p1_paper_canary_launch_joiner.py")
PRODUCER = load(
    "hepta_p1_paper_canary_handoff_producer_test_module",
    ROOT / "scripts/hepta_p1_paper_canary_handoff_producer.py")
EXECUTOR = load(
    "hepta_p1_paper_canary_executor_launch_test_module",
    ROOT / "scripts/hepta_p1_paper_canary_executor.py")
FINALIZER = load(
    "hepta_p1_paper_canary_root_finalizer_launch_test_module",
    ROOT / "scripts/hepta_p1_paper_canary_root_finalizer.py")
COORDINATOR = load(
    "hepta_p1_paper_canary_root_coordinator_test_module",
    ROOT / "scripts/hepta_p1_paper_canary_root_coordinator.py")
OWNER = load(
    "hepta_p1_paper_canary_owner_provisioner_test_module",
    ROOT / "scripts/hepta_p1_paper_canary_owner_provisioner.py")
CLOSER = load(
    "hepta_p1_paper_canary_crash_closer_test_module",
    ROOT / "scripts/hepta_p1_paper_canary_crash_emergency_closer.py")
TERMINAL = load(
    "hepta_p1_paper_canary_terminal_prover_test_module",
    ROOT / "scripts/hepta_p1_paper_canary_terminal_prover.py")


def digest(label: str) -> str:
    return JOINER.sha((label + "\n").encode("ascii"))


def trade_intent() -> dict:
    return {
        "schema": "hepta.trade-intent.v1", "paper_only": True,
        "strategy_id": "strategy-a", "strategy_version": "1",
        "strategy_sha256": digest("strategy"), "intent_id": "intent-a",
        "instrument": "EUR.USD", "symbol": "EUR", "currency": "USD",
        "sec_type": "CASH", "exchange": "IDEALPRO", "side": "BUY",
        "quantity": 1, "order_type": "LMT", "limit_price": 1.08765000,
        "tif": "DAY", "observed_bid": 1.08764000,
        "observed_ask": 1.08765000, "observed_at_ms": 1_000_000,
        "expires_at_ms": 1_050_000, "entry_thesis": "canary",
        "invalidation_condition": "time", "max_holding_ms": 30_000,
        "max_adverse_move": 0.00050000, "expected_slippage": 0.00010000,
        "exit_plan": "immediate close",
    }


def decision(*, trade: bool = True, intent: dict | None = None) -> bytes:
    value = trade_intent() if intent is None else intent
    document = {
        "schema": "hepta.autonomous-paper-decision-receipt.v1",
        "campaign_id": "campaign-a", "strategy_id": "strategy-a",
        "strategy_version": "1", "strategy_sha256": digest("strategy"),
        "decision_id": "decision-a",
        "cycle_id": "shadow-cycle-a" if trade else None,
        "started_at_ms": 1_000_000, "finished_at_ms": 1_000_001,
        "paper_only": True, "live_authorized": False, "shadow_only": True,
        "information_packet_sha256": digest("packet"),
        "catalog_sha256": digest("catalog"),
        "descriptor_sha256": digest("descriptors"), "preflight_sha256": None,
        "regime": "trend", "setup_gates": ["FRESH_EVIDENCE"] if trade else [],
        "risk_challenges": [] if trade else ["NO_DIRECTION"],
        "evidence_refs": [digest("evidence")], "conflicts": [],
        "decision": "TRADE" if trade else "NO_TRADE",
        "reason_codes": [] if trade else ["NO_DIRECTION"],
        "trade_intent": value if trade else None,
        "trade_intent_sha256": JOINER.canonical_sha(value) if trade else None,
        "campaign_open_request_id": None, "campaign_close_request_id": None,
        "mutation_attempted": False, "direct_broker_access": False,
        "final_outcome": "SHADOW_TRADE" if trade else "NO_TRADE",
    }
    return JOINER.canonical_json(document)


def ref(name: str) -> object:
    body = {"schema": f"hepta.test-{name}.v1", "body_sha256": digest(name)}
    return JOINER.Reference(
        f"/test/{name}.json", JOINER.canonical_json(body), body,
        digest(name + "-file"), digest(name + "-body"))


def local_exclusive(path: Path, raw: bytes, *, uid: int, gid: int,
                    mode: int) -> None:
    del uid, gid
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        raise FileExistsError(path)
    path.write_bytes(raw)
    path.chmod(mode)


class JoinerTests(unittest.TestCase):
    def test_root_cleanup_descriptors_are_byte_equal_v4_contract(self):
        descriptors = (
            FINALIZER.ROOT_CLEANUP_DESCRIPTOR,
            PRODUCER.ROOT_CLEANUP_DESCRIPTOR,
            JOINER._root_cleanup_descriptor(),
            EXECUTOR.ROOT_CLEANUP_DESCRIPTOR,
        )
        canonical = [JOINER.canonical_json(value) for value in descriptors]
        self.assertTrue(all(raw == canonical[0] for raw in canonical[1:]))
        self.assertEqual(
            descriptors[0]["response_schema"],
            "hepta.p1-paper-canary-root-cleanup-receipt.v4")
        self.assertEqual(descriptors[0]["timeout_ms"], 240_000)

    def normalize(self, raw: bytes):
        return JOINER.normalize_trade_decision(
            decision_raw=raw, decision_path="/test/decision.json",
            campaign_id="campaign-a", requested_cycle_id="cycle-a",
            now_ms=1_000_100, validator_path="/test/validator",
            validator_file_sha256=digest("validator"),
            strategy_path="/test/strategy.json",
            strategy_file_sha256=digest("strategy-file"),
            information_packet_path="/test/packet.json",
            information_packet_file_sha256=digest("packet-file"),
            catalog=ref("catalog"), capture=ref("capture"))

    def test_float_decision_becomes_distinct_decimal_normalization(self):
        receipt, raw = self.normalize(decision())
        self.assertEqual(receipt["normalized_intent"]["limit_price"], "1.08765")
        self.assertEqual(
            receipt["normalized_intent"]["expected_slippage"], "0.0001")
        self.assertEqual(receipt["original_decision_cycle_id"], "shadow-cycle-a")
        self.assertEqual(receipt["cycle_id"], "cycle-a")
        self.assertEqual(JOINER.strict_json(raw, "TEST")[0], receipt)

    def test_exponent_negative_zero_and_excess_precision_are_rejected(self):
        cases = []
        exponent = trade_intent()
        exponent["expected_slippage"] = 1e-9
        cases.append(exponent)
        negative_zero = trade_intent()
        negative_zero["max_adverse_move"] = -0.0
        cases.append(negative_zero)
        precision = trade_intent()
        precision["expected_slippage"] = 0.123456789
        cases.append(precision)
        for intent in cases:
            with self.subTest(intent=intent):
                with self.assertRaises(JOINER.JoinError):
                    self.normalize(decision(intent=intent))

    def test_no_trade_is_terminal_without_normalization_or_handoff(self):
        receipt, raw = JOINER.no_trade_receipt(
            decision_raw=decision(trade=False),
            decision_path="/test/decision.json", campaign_id="campaign-a",
            requested_cycle_id="cycle-a", now_ms=1_000_100,
            validator_path="/test/validator",
            validator_file_sha256=digest("validator"),
            strategy_path="/test/strategy.json",
            strategy_file_sha256=digest("strategy-file"),
            information_packet_path="/test/packet.json",
            information_packet_file_sha256=digest("packet-file"),
            capture=ref("capture"))
        self.assertEqual(receipt["status"], "NO_TRADE")
        self.assertFalse(receipt["handoff_created"])
        self.assertFalse(receipt["authority_started"])
        self.assertEqual(JOINER.strict_json(raw, "TEST")[0], receipt)

    def test_capture_owner_is_journaled_before_provision_and_audited_absent(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            control_root = root / "control"
            control = control_root / "campaign-a" / "cycle-a"
            token_path = root / "capture.token"
            calls: list[str] = []

            def sessionctl(arguments):
                operation = arguments[0]
                calls.append(operation)
                self.assertTrue(
                    (control / "capture-session-owner.v1.json").exists())
                if operation == "provision":
                    return 0, {"accepted": True, "reason_code": "OK",
                               "lease_generation": 1}
                if calls.count("revoke") == 1:
                    return 0, {"accepted": True, "reason_code": "OK",
                               "lease_generation": 1}
                return 4, {"accepted": False,
                           "reason_code": "SESSION_LEASE_NOT_FOUND",
                           "lease_generation": 1}

            with mock.patch.object(JOINER, "CAPTURE_TOKEN_SOURCE", token_path), \
                    mock.patch.object(JOINER, "_write_exclusive",
                                      side_effect=local_exclusive), \
                    mock.patch.object(JOINER, "_sessionctl",
                                      side_effect=sessionctl), \
                    mock.patch.object(JOINER.os, "urandom",
                                      return_value=b"c" * 32):
                intent, token = JOINER._capture_owner_begin(
                    control, "campaign-a", "cycle-a", 1_000_000)
                raw = JOINER._capture_owner_retire(
                    control, intent, token, 1_000_001)

            receipt, _lexical = JOINER.strict_json(raw, "TEST")
            self.assertEqual(calls, ["provision", "revoke", "revoke"])
            self.assertEqual(receipt["lease_generation"], 1)
            self.assertEqual(
                receipt["revoke_audit_reason_code"],
                "SESSION_LEASE_NOT_FOUND")
            self.assertEqual(
                receipt["durable_hsl_audit"],
                "GENERATION_ABSENT_AFTER_REVOKE")
            self.assertFalse(token_path.exists())
            self.assertTrue(
                (control / "capture-session-owner.v1.json").exists())

            original_stable_read = TERMINAL.stable_read

            def stable_read(path, **parameters):
                candidate = Path(path)
                if candidate.is_relative_to(root):
                    if parameters.get("uid") == 0:
                        parameters["uid"] = os.getuid()
                    if parameters.get("gid") == 0:
                        parameters["gid"] = os.getgid()
                return original_stable_read(candidate, **parameters)

            with mock.patch.object(TERMINAL, "CONTROL_ROOT", control_root), \
                    mock.patch.object(TERMINAL, "CAPTURE_TOKEN", token_path), \
                    mock.patch.object(
                        TERMINAL, "stable_read", side_effect=stable_read):
                TERMINAL._capture_evidence("campaign-a", "cycle-a")

                receipt_path = control / \
                    "capture-session-retirement-receipt.v1.json"
                changed = TERMINAL.strict_json(
                    receipt_path.read_bytes(), "TEST")
                changed["revoke_audit_reason_code"] = "OK"
                body = dict(changed)
                body.pop("body_sha256")
                changed["body_sha256"] = TERMINAL.sha(
                    TERMINAL.canonical_json(body))
                receipt_path.write_bytes(TERMINAL.canonical_json(changed))
                with self.assertRaisesRegex(
                        TERMINAL.ProverError,
                        "TERMINAL_PROVER_CAPTURE_RETIREMENT_INVALID"):
                    TERMINAL._capture_evidence("campaign-a", "cycle-a")

    def test_capture_audit_uncertainty_retains_recovery_bearer(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            control = root / "control"
            token_path = root / "capture.token"
            revoke_count = 0

            def sessionctl(arguments):
                nonlocal revoke_count
                if arguments[0] == "provision":
                    return 0, {"accepted": True, "reason_code": "OK",
                               "lease_generation": 1}
                revoke_count += 1
                return 0, {"accepted": True, "reason_code": "OK",
                           "lease_generation": 1}

            with mock.patch.object(JOINER, "CAPTURE_TOKEN_SOURCE", token_path), \
                    mock.patch.object(JOINER, "_write_exclusive",
                                      side_effect=local_exclusive), \
                    mock.patch.object(JOINER, "_sessionctl",
                                      side_effect=sessionctl), \
                    mock.patch.object(JOINER.os, "urandom",
                                      return_value=b"c" * 32):
                intent, token = JOINER._capture_owner_begin(
                    control, "campaign-a", "cycle-a", 1_000_000)
                with self.assertRaisesRegex(
                        JOINER.JoinError,
                        "JOIN_CAPTURE_SESSION_REVOKE_AUDIT_UNCERTAIN"):
                    JOINER._capture_owner_retire(
                        control, intent, token, 1_000_001)

            self.assertEqual(revoke_count, 2)
            self.assertTrue(token_path.exists())
            self.assertTrue(
                (control / "capture-session-owner.v1.json").exists())
            self.assertFalse((control /
                              "capture-session-retirement-receipt.v1.json").exists())


def coord_sealed(body: dict) -> bytes:
    return COORDINATOR.canonical_json(COORDINATOR.sealed_body(body))


class FakeControl:
    def __init__(self, *, no_trade: bool = False, lost_response: bool = False,
                 launch_crash: bool = False,
                 crash_before_phase: str | None = None,
                 crash_after_phase: str | None = None,
                 completion_response_loss: bool = False,
                 purge_response_loss: bool = False):
        self.clock = 1_000_000
        self.no_trade = no_trade
        self.lost_response = lost_response
        self.launch_crash = launch_crash
        self.crash_before_phase = crash_before_phase
        self.crash_after_phase = crash_after_phase
        self.completion_response_loss = completion_response_loss
        self.purge_response_loss = purge_response_loss
        self.crash_fired = False
        self.completion_loss_fired = False
        self.purge_loss_fired = False
        self.retry_requests: list[bytes] = []
        self.wals: list[bytes] = []
        self.current_wal = None
        self.cleared = False
        self.published = b""
        self.purge_receipt = b""
        self.launch_count = 0
        self.executor_count = 0
        self.terminal_proof_count = 0
        self.owner_purge_count = 0
        self.owner_purge_creation_count = 0
        self.launch_artifacts_ready = False
        self.capture_surface_enable_count = 0
        self.orphan_recovery_count = 0
        self.force_deny_all_count = 0
        self.stop_outer_count = 0
        self.handoff = COORDINATOR.sealed_body({
            "schema": COORDINATOR.HANDOFF_SCHEMA, "campaign_id": "campaign-a",
            "cycle_id": "cycle-a", "paper_only": True,
            "live_authorized": False, "authority_granted": False,
            "session_owner_reference": {
                "revoke_bearer_path": str(
                    COORDINATOR.STATE_ROOT / "session-authority" /
                    "session.token.revoke-token"),
                "revoke_bearer_sha256": digest("token"),
            },
            "root_cleanup_call": {
                "tool_call_id": "cleanup-call-a",
                "command_id": "cleanup-call-a",
                "tool_descriptor_sha256": digest("cleanup-descriptor"),
            },
        })
        self.request = COORDINATOR.sealed_body({
            "schema": COORDINATOR.NORMAL_REQUEST_SCHEMA,
            "version": 1, "issued_at_ms": 1_000_000,
            "expires_at_ms": 1_240_000,
            "campaign_id": "campaign-a", "cycle_id": "cycle-a",
            "cleanup_tool_call_id": "cleanup-call-a",
            "cleanup_command_id": "cleanup-call-a",
            "tool_descriptor_sha256": digest("cleanup-descriptor"),
            "required_actions": list(COORDINATOR.NORMAL_REQUIRED_ACTIONS),
            "paper_only": True, "live_authorized": False,
            "authority_granted": False,
        })
        self.receipt = COORDINATOR.sealed_body({
            "schema": COORDINATOR.NORMAL_ROOT_SCHEMA,
            "version": COORDINATOR.OUTER_VERSION,
            "status": COORDINATOR.NORMAL_STATUS,
            "campaign_id": "campaign-a", "cycle_id": "cycle-a",
            "cleanup_tool_call_id": "cleanup-call-a",
            "cleanup_command_id": "cleanup-call-a", "broker_deny_all": True,
            "authorized_connector_count": 0, "identity_count": 0,
            "runtime_session_count": 0, "durable_owner_count": 0,
            "durable_owner_status": "RETIRED",
            "durable_owner_retirement_receipt_path": str(
                COORDINATOR.CONTROL_ROOT / "campaign-a" / "cycle-a" /
                "durable-owner-retirement-receipt.v4.json"),
            "durable_owner_retirement_receipt_file_sha256":
                digest("owner-file"),
            "durable_owner_retirement_receipt_body_sha256":
                digest("owner-body"),
            "mutation_credentials_destroyed": True,
            "credentials_destroyed_scope":
                "PEER_MUTATION_TOKEN_AND_AUTHORITY_ONLY",
            "retained_root_recovery_bearer_count": 1,
            "retained_root_recovery_bearer_path": str(
                COORDINATOR.STATE_ROOT / "session-authority" /
                "session.token.revoke-token"),
            "retained_root_recovery_bearer_sha256": digest("token"),
            "retained_root_recovery_bearer_mutation_authority": False,
            "completed_actions": list(COORDINATOR.NORMAL_REQUIRED_ACTIONS),
            "paper_only": True,
            "live_authorized": False, "authority_granted": False,
        })

    def now_ms(self):
        self.clock += 1
        return self.clock

    def load_wal(self, _campaign, _cycle):
        return self.current_wal

    def persist_wal(self, raw):
        phase = COORDINATOR.strict_json(raw, "TEST")["phase"]
        if (not self.crash_fired and phase == self.crash_before_phase):
            self.crash_fired = True
            raise SystemExit("injected pre-WAL process death: " + phase)
        self.wals.append(raw)
        self.current_wal = raw
        if (not self.crash_fired and phase == self.crash_after_phase):
            self.crash_fired = True
            raise SystemExit("injected post-WAL process death: " + phase)

    def clear_wal(self):
        self.cleared = True
        self.current_wal = None

    def ensure_prelaunch_deny_all(self, _campaign, _cycle):
        return None

    def enable_capture_surface(self, _campaign, _cycle):
        self.capture_surface_enable_count += 1

    def launch(self, _campaign, _cycle):
        self.launch_count += 1
        if self.launch_crash:
            raise RuntimeError("injected launch failure seam")
        self.launch_artifacts_ready = True
        if self.no_trade:
            return COORDINATOR.LaunchOutcome(
                "NO_TRADE", b"capture\n", None, b"no-trade\n", None)
        return COORDINATOR.LaunchOutcome(
            "TRADE", b"capture\n", b"normalization\n", None,
            COORDINATOR.canonical_json(self.handoff))

    def reopen_launch(self, campaign, cycle):
        del campaign, cycle
        if not self.launch_artifacts_ready:
            return None
        if self.no_trade:
            return COORDINATOR.LaunchOutcome(
                "NO_TRADE", b"capture\n", None, b"no-trade\n", None)
        return COORDINATOR.LaunchOutcome(
            "TRADE", b"capture\n", b"normalization\n", None,
            COORDINATOR.canonical_json(self.handoff))

    def run_executor(self, handoff_raw):
        del handoff_raw
        if hasattr(self, "execution_result"):
            return self.execution_result
        self.executor_count += 1
        self.execution_result = coord_sealed({
            "schema": COORDINATOR.RESULT_SCHEMA, "status": "SUCCESS",
            "handoff_body_sha256": self.handoff["body_sha256"],
            "authority_granted": False,
        })
        return self.execution_result

    def reopen_execution_result(self, _campaign, _cycle):
        return getattr(self, "execution_result", None)

    def reopen_inner_request(self, _campaign, _cycle):
        return COORDINATOR.canonical_json(self.request)

    def reopen_inner_receipt(self, _campaign, _cycle):
        return None if self.lost_response and not self.retry_requests else \
            COORDINATOR.canonical_json(self.receipt)

    def retry_same_inner_request(self, raw):
        self.retry_requests.append(raw)
        return COORDINATOR.canonical_json(self.receipt)

    def force_deny_all(self, _campaign, _cycle):
        self.force_deny_all_count += 1

    def stop_outer_units(self):
        self.stop_outer_count += 1

    def recover_pre_handoff_owners(self, _campaign, _cycle):
        self.orphan_recovery_count += 1

    def prove_terminal(
            self, _campaign, _cycle, _receipt, expected_owner_status):
        self.terminal_proof_count += 1
        status = "NONE" if self.no_trade else "RETIRED"
        if expected_owner_status != status:
            raise AssertionError("unexpected terminal owner status")
        return {
            "broker_deny_all": True, "kill_switches_engaged": True,
            "permit_absent": True, "identity_count": 0,
            "identity_manifest_sha256": digest("identity"),
            "authorized_connector_count": 0, "runtime_session_count": 0,
            "paper_authority_and_mutation_units": list(
                COORDINATOR.PAPER_AUTHORITY_AND_MUTATION_UNITS),
            "paper_authority_and_mutation_units_inactive": True,
            "peer_capture_unit_inactive": True,
            "peer_executor_unit_inactive": True,
            "finalizer_listener_unit_inactive": True,
            "finalizer_connection_units_inactive": True,
            "durable_owner_count": 0, "durable_owner_status": status,
            "durable_owner_evidence_path": None if self.no_trade else
                "/test/owner-retired.json",
            "durable_owner_evidence_file_sha256": None if self.no_trade else
                digest("owner-file"),
            "durable_owner_evidence_body_sha256": None if self.no_trade else
                digest("owner-body"),
        }

    def publish_completion(self, _campaign, _cycle, raw):
        if self.published and self.published != raw:
            raise AssertionError("completion changed across replay")
        self.published = raw
        if self.completion_response_loss and not self.completion_loss_fired:
            self.completion_loss_fired = True
            raise SystemExit("injected completion publish response loss")

    def reopen_completion(self, _campaign, _cycle):
        return self.published or None

    def confirm_owner_purge(self, campaign, cycle):
        self.owner_purge_count += 1
        if self.purge_receipt:
            if self.purge_response_loss and not self.purge_loss_fired:
                self.purge_loss_fired = True
                raise SystemExit("injected purge response loss")
            return self.purge_receipt
        completion = COORDINATOR.strict_json(self.published, "TEST")
        directory = COORDINATOR.CONTROL_ROOT / campaign / cycle
        value = COORDINATOR.sealed_body({
            "schema":
                "hepta.p1-paper-canary-outer-owner-purge-receipt.v1",
            "version": 1, "status": "OWNER_BEARER_PURGED",
            "campaign_id": campaign, "domain_id": "alpha",
            "cycle_id": cycle,
            "owner_purge_intent_path": str(
                directory / "outer-owner-purge-intent.v1.json"),
            "owner_purge_intent_file_sha256": digest("purge-intent-file"),
            "owner_purge_intent_body_sha256": digest("purge-intent-body"),
            "outer_completion_path": str(
                directory / "cycle-completion-receipt.v4.json"),
            "outer_completion_file_sha256":
                COORDINATOR.sha(self.published),
            "outer_completion_body_sha256": completion["body_sha256"],
            "root_cleanup_receipt_path": str(
                directory / "root-cleanup-receipt.v4.json"),
            "root_cleanup_receipt_file_sha256": digest("root-file"),
            "root_cleanup_receipt_body_sha256": digest("root-body"),
            "owner_retirement_receipt_path": str(
                directory / "durable-owner-retirement-receipt.v4.json"),
            "owner_retirement_receipt_file_sha256": digest("retire-file"),
            "owner_retirement_receipt_body_sha256": digest("retire-body"),
            "terminal_ack_receipt_sha256": digest("terminal-ack"),
            "revoke_bearer_file_sha256": digest("bearer"),
            "owner_bearer_purged": True,
            "durable_owner_credential_count": 0, "paper_only": True,
            "live_authorized": False, "authority_granted": False,
        })
        self.purge_receipt = COORDINATOR.canonical_json(value)
        self.owner_purge_creation_count += 1
        if self.purge_response_loss and not self.purge_loss_fired:
            self.purge_loss_fired = True
            raise SystemExit("injected purge response loss")
        return self.purge_receipt


class CoordinatorTests(unittest.TestCase):
    def test_owner_compensating_revoke_requires_durable_absence_audit(self):
        accepted = (0, {
            "accepted": True, "reason_code": "OK", "lease_generation": 1,
        })
        absent = (4, {
            "accepted": False, "reason_code": "SESSION_NOT_FOUND",
            "lease_generation": 1,
        })
        with mock.patch.object(
                OWNER, "_sessionctl",
                side_effect=[accepted, absent]) as sessionctl:
            OWNER._revoke(1)
        self.assertEqual(sessionctl.call_count, 2)

        with mock.patch.object(
                OWNER, "_sessionctl",
                side_effect=[accepted, accepted]), \
                self.assertRaisesRegex(
                    OWNER.OwnerError,
                    "OWNER_COMPENSATING_REVOKE_AUDIT_UNCERTAIN"):
            OWNER._revoke(1)

    def test_no_trade_never_runs_executor_and_is_not_p2_success(self):
        control = FakeControl(no_trade=True)
        raw = COORDINATOR.coordinate("campaign-a", "cycle-a", control)
        receipt = COORDINATOR.strict_json(raw, "TEST")
        self.assertEqual(receipt["status"], "NO_TRADE")
        self.assertFalse(receipt["p2_success"])
        self.assertFalse(receipt["recovery_required"])
        self.assertEqual(receipt["durable_owner_status"], "NONE")
        self.assertEqual(receipt["schema"], COORDINATOR.COMPLETION_SCHEMA)
        self.assertEqual(receipt["version"], COORDINATOR.OUTER_VERSION)
        self.assertTrue(control.cleared)
        self.assertEqual(control.capture_surface_enable_count, 1)

    def test_lost_inner_response_retries_only_identical_cleanup_request(self):
        control = FakeControl(lost_response=True)
        raw = COORDINATOR.coordinate("campaign-a", "cycle-a", control)
        receipt = COORDINATOR.strict_json(raw, "TEST")
        self.assertEqual(receipt["status"], "P2_SUCCESS")
        self.assertTrue(receipt["inner_cleanup_retry_attempted"])
        self.assertEqual(
            control.retry_requests,
            [COORDINATOR.canonical_json(control.request)])
        retried = COORDINATOR.strict_json(control.retry_requests[0], "TEST")
        self.assertEqual(retried["cleanup_command_id"], "cleanup-call-a")
        self.assertEqual(control.capture_surface_enable_count, 1)

    def test_existing_wal_phases_forward_recover_without_relaunch(self):
        seams = (
            ("LAUNCH_COMPLETE", "before"),
            ("EXECUTOR_EXITED", "before"),
            ("INNER_JOINED", "before"),
            ("TERMINAL_PROVEN", "after"),
            ("COMPLETION_PUBLISHED", "after"),
            ("OWNER_PURGE_CONFIRMED", "after"),
        )
        for phase, timing in seams:
            with self.subTest(phase=phase, timing=timing):
                parameters = {
                    ("crash_before_phase" if timing == "before" else
                     "crash_after_phase"): phase,
                }
                control = FakeControl(**parameters)
                with self.assertRaises(SystemExit):
                    COORDINATOR.coordinate("campaign-a", "cycle-a", control)
                durable_phase = COORDINATOR.strict_json(
                    control.current_wal, "TEST")["phase"]
                if timing == "before":
                    self.assertLess(
                        COORDINATOR.PHASES.index(durable_phase),
                        COORDINATOR.PHASES.index(phase))
                raw = COORDINATOR.coordinate(
                    "campaign-a", "cycle-a", control)
                receipt = COORDINATOR.strict_json(raw, "TEST")
                self.assertEqual(receipt["status"], "P2_SUCCESS")
                self.assertEqual(control.launch_count, 1)
                self.assertEqual(control.executor_count, 1)
                self.assertEqual(control.owner_purge_creation_count, 1)
                self.assertTrue(control.cleared)

    def test_outer_completion_and_purge_response_loss_replay_same_cycle(self):
        completion_loss = FakeControl(completion_response_loss=True)
        with self.assertRaisesRegex(SystemExit, "completion publish"):
            COORDINATOR.coordinate(
                "campaign-a", "cycle-a", completion_loss)
        durable_completion = completion_loss.published
        self.assertTrue(durable_completion)
        recovered = COORDINATOR.coordinate(
            "campaign-a", "cycle-a", completion_loss)
        self.assertEqual(recovered, durable_completion)
        self.assertEqual(completion_loss.launch_count, 1)
        self.assertEqual(completion_loss.executor_count, 1)

        purge_loss = FakeControl(purge_response_loss=True)
        with self.assertRaisesRegex(SystemExit, "purge response loss"):
            COORDINATOR.coordinate("campaign-a", "cycle-a", purge_loss)
        completion = purge_loss.published
        purge = purge_loss.purge_receipt
        self.assertTrue(completion)
        self.assertTrue(purge)
        self.assertEqual(purge_loss.owner_purge_creation_count, 1)
        recovered = COORDINATOR.coordinate(
            "campaign-a", "cycle-a", purge_loss)
        self.assertEqual(recovered, completion)
        self.assertEqual(purge_loss.purge_receipt, purge)
        self.assertEqual(purge_loss.owner_purge_creation_count, 1)
        self.assertEqual(purge_loss.launch_count, 1)
        self.assertEqual(purge_loss.executor_count, 1)

    def test_legacy_normal_inner_receipts_cannot_publish_p2_success(self):
        for version in (1, 2):
            with self.subTest(version=version):
                control = FakeControl()
                legacy = dict(control.receipt)
                legacy.pop("body_sha256")
                legacy["schema"] = (
                    "hepta.p1-paper-canary-root-cleanup-receipt."
                    f"v{version}")
                legacy["version"] = version
                control.receipt = COORDINATOR.sealed_body(legacy)
                with self.assertRaises(COORDINATOR.CoordinatorError):
                    COORDINATOR.coordinate("campaign-a", "cycle-a", control)
                self.assertEqual(control.force_deny_all_count, 1)
                self.assertEqual(control.stop_outer_count, 1)
                self.assertFalse(control.published)

    def test_legacy_outer_completion_and_wal_never_short_circuit_v4(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cycle = root / "control" / "campaign-a" / "cycle-a"
            cycle.mkdir(parents=True)
            for version in (1, 2):
                with self.subTest(kind="completion", version=version):
                    legacy_completion = cycle / (
                        f"cycle-completion-receipt.v{version}.json")
                    legacy_completion.write_bytes(b"legacy\n")
                    with mock.patch.object(
                            COORDINATOR, "CONTROL_ROOT", root / "control"):
                        with self.assertRaisesRegex(
                                COORDINATOR.CoordinatorError,
                                "COORDINATOR_LEGACY_COMPLETION_PRESENT"):
                            COORDINATOR._completion_raw(
                                "campaign-a", "cycle-a")
                    legacy_completion.unlink()

            for version in (1, 2):
                with self.subTest(kind="wal", version=version):
                    legacy_wal = root / (
                        f"coordinator-transaction.v{version}.json")
                    legacy_wal.write_bytes(b"legacy\n")
                    with mock.patch.object(
                            COORDINATOR, "LEGACY_WAL_PATHS", (legacy_wal,)), \
                            mock.patch.object(
                                COORDINATOR, "WAL_PATH",
                                root / "coordinator-transaction.v3.json"):
                        with self.assertRaisesRegex(
                                COORDINATOR.CoordinatorError,
                                "COORDINATOR_LEGACY_WAL_PRESENT"):
                            COORDINATOR.ProductionControl().load_wal(
                                "campaign-a", "cycle-a")
                    legacy_wal.unlink()

    def test_launch_sigkill_has_durable_owner_obligation_and_recovers_orphans(self):
        control = FakeControl(launch_crash=True)
        with self.assertRaises(COORDINATOR.CoordinatorError):
            COORDINATOR.coordinate("campaign-a", "cycle-a", control)
        wal_values = [
            COORDINATOR.strict_json(raw, "TEST") for raw in control.wals]
        owner_wal = next(
            value for value in wal_values if value["phase"] == "OWNER_MAY_EXIST")
        self.assertTrue(owner_wal["capture_owner_may_exist"])
        self.assertTrue(owner_wal["mutation_owner_may_exist"])
        self.assertEqual(control.orphan_recovery_count, 1)
        self.assertEqual(control.force_deny_all_count, 1)
        self.assertEqual(control.stop_outer_count, 1)

    def test_paper_owner_sigkill_after_hsl_commit_retains_durable_recovery(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            owner_root = root / "owner"
            token_root = root / "runtime"
            paths = {
                "OWNER_ROOT": owner_root,
                "AUTHORITY_PATH": owner_root / "session.token.authority.json",
                "REVOKE_PATH": owner_root / "session.token.revoke-token",
                "PROVISIONING_PATH": owner_root / ".session.token.provisioning",
                "INTENT_PATH": owner_root /
                    "session.token.owner-may-exist.v1.json",
                "TOKEN_ROOT": token_root,
                "TOKEN_PATH": token_root / "session.token",
            }
            phases: list[str] = []

            def sessionctl(arguments):
                self.assertEqual(arguments[0], "provision")
                self.assertTrue(paths["INTENT_PATH"].exists())
                phases.append("SUPERVISOR_PROVISION")
                return 0, {"accepted": True, "reason_code": "OK",
                           "lease_generation": 1}

            def crash_hook(phase):
                phases.append(phase)
                if phase == "SESSION_PROVISION_COMMITTED":
                    raise OWNER.OwnerProcessDeath()

            patches = [mock.patch.object(OWNER, name, value)
                       for name, value in paths.items()]
            with mock.patch.object(OWNER.os, "geteuid", return_value=0), \
                    mock.patch.object(OWNER.os, "getegid", return_value=0), \
                    mock.patch.object(OWNER, "_ensure_directories"), \
                    mock.patch.object(OWNER, "_exclusive",
                                      side_effect=local_exclusive), \
                    mock.patch.object(OWNER, "_sessionctl",
                                      side_effect=sessionctl), \
                    mock.patch.object(OWNER.os, "urandom",
                                      return_value=b"m" * 32), \
                    patches[0], patches[1], patches[2], patches[3], \
                    patches[4], patches[5], patches[6]:
                with self.assertRaises(OWNER.OwnerProcessDeath):
                    OWNER.provision(
                        "campaign-a", "cycle-a", "epoch-a", 1,
                        "DU123", "PAPER:alpha", now_ms=1_000_000,
                        crash_hook=crash_hook)

            self.assertEqual(phases, [
                "OWNER_MAY_EXIST_DURABLE", "SUPERVISOR_PROVISION",
                "SESSION_PROVISION_COMMITTED"])
            self.assertTrue(paths["INTENT_PATH"].exists())
            self.assertTrue(paths["PROVISIONING_PATH"].exists())
            self.assertFalse(paths["AUTHORITY_PATH"].exists())
            self.assertFalse(paths["TOKEN_PATH"].exists())

    def test_orphan_closer_retires_capture_and_mutation_by_generation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            control_root = root / "control"
            control = control_root / "campaign-a" / "cycle-a"
            artifact_root = root / "artifact"
            artifact = artifact_root / "campaign-a" / "cycle-a"
            owner_root = root / "owner"
            capture_token = root / "capture.token"
            mutation_token = owner_root / ".session.token.provisioning"
            capture_raw = JOINER.canonical_json(JOINER.sealed_body({
                "schema": "hepta.p1-paper-canary-capture-owner.v1",
                "version": 1, "status": "OWNER_MAY_EXIST",
                "created_at_ms": 1_000_000, "campaign_id": "campaign-a",
                "domain_id": "alpha", "cycle_id": "cycle-a",
                "template_id": "watch", "session_id": "capture-session-a",
                "expected_lease_generation": 1, "peer_uid": 2104,
                "token_path": str(capture_token),
                "token_sha256": CLOSER.sha(b"capture-token\n"),
                "paper_only": True, "live_authorized": False,
                "authority_granted": False,
            }))
            mutation_body = {
                "schema": "hepta.p1-paper-canary-owner-may-exist.v1",
                "version": 1, "created_at_ms": 1_000_000,
                "campaign_id": "campaign-a", "domain_id": "alpha",
                "cycle_id": "cycle-a", "token_name": "session.token",
                "token_sha256": CLOSER.sha(b"mutation-token\n"),
                "token_bearer_path": str(mutation_token),
                "expected_lease_generation": 1,
                "session_id": "mutation-session-a", "peer_uid": 2104,
                "peer_gid": 2104, "owner_account": "DU123",
                "owner_execution_domain": "PAPER:alpha",
                "paper_only": True, "live_authorized": False,
                "authority_granted": False,
            }
            mutation_raw = CLOSER.canonical_json({
                **mutation_body,
                "body_sha256": CLOSER.sha(CLOSER.canonical_json(mutation_body)),
            })
            control.mkdir(parents=True)
            artifact.mkdir(parents=True)
            owner_root.mkdir(parents=True)
            for name in (
                    "capture-request.v1.json",
                    "normalized-launch-intent-receipt.v1.json"):
                (control / name).write_bytes(b"pre-handoff\n")
                (control / name).chmod(0o600)
            for name in (
                    "tool-catalog.v1.json", "read-only-capture.v1.json",
                    "original-strategy-decision.v1.json",
                    "canary-strategy-state.v1.json"):
                (artifact / name).write_bytes(b"read-only\n")
                (artifact / name).chmod(0o600)
            (control / "capture-session-owner.v1.json").write_bytes(capture_raw)
            (control / "capture-session-owner.v1.json").chmod(0o600)
            capture_token.write_bytes(b"capture-token\n")
            capture_token.chmod(0o400)
            (owner_root / "session.token.owner-may-exist.v1.json").write_bytes(
                mutation_raw)
            (owner_root / "session.token.owner-may-exist.v1.json").chmod(0o600)
            mutation_token.write_bytes(b"mutation-token\n")
            mutation_token.chmod(0o600)
            original_stable = CLOSER.stable_read

            def stable(path, **parameters):
                candidate = Path(path)
                if candidate.is_relative_to(root):
                    if parameters.get("uid") == 0:
                        parameters["uid"] = os.getuid()
                    if parameters.get("gid") == 0:
                        parameters["gid"] = os.getgid()
                return original_stable(candidate, **parameters)

            revoked: list[tuple[Path, int]] = []

            def revoke(path, generation):
                revoked.append((path, generation))
                return "SESSION_LEASE_NOT_FOUND"

            with mock.patch.object(CLOSER, "CONTROL_ROOT", control_root), \
                    mock.patch.object(CLOSER, "ARTIFACT_ROOT", artifact_root), \
                    mock.patch.object(
                        CLOSER, "ACTIVE_EXECUTION_HANDOFF",
                        root / "active-execution-handoff.v1.json"), \
                    mock.patch.object(CLOSER, "OWNER_ROOT", owner_root), \
                    mock.patch.object(
                        CLOSER, "ROOT_FINALIZER_WAL",
                        root / "root-finalizer-wal.json"), \
                    mock.patch.object(
                        CLOSER, "LEGACY_ROOT_FINALIZER_WALS",
                        tuple(root / f"legacy-root-finalizer-wal-{index}.json"
                              for index in range(4))), \
                    mock.patch.object(CLOSER, "OWNER_INTENT", owner_root /
                                      "session.token.owner-may-exist.v1.json"), \
                    mock.patch.object(CLOSER, "OWNER_AUTHORITY", owner_root /
                                      "session.token.authority.json"), \
                    mock.patch.object(CLOSER, "OWNER_REVOKE", owner_root /
                                      "session.token.revoke-token"), \
                    mock.patch.object(CLOSER, "OWNER_PROVISIONING",
                                      mutation_token), \
                    mock.patch.object(CLOSER, "OWNER_RUNTIME_TOKEN", root /
                                      "runtime-session.token"), \
                    mock.patch.object(CLOSER, "CAPTURE_TOKEN", capture_token), \
                    mock.patch.object(CLOSER, "stable_read", side_effect=stable), \
                    mock.patch.object(CLOSER, "_revoke_owner",
                                      side_effect=revoke), \
                    mock.patch.object(CLOSER.os, "chown"), \
                    mock.patch.object(CLOSER.os, "fchown"):
                result = CLOSER.reconcile_orphan_owners(
                    "campaign-a", "cycle-a")

            self.assertEqual(result["status"], "OWNERS_RETIRED")
            self.assertTrue(result["capture_owner_retired"])
            self.assertTrue(result["mutation_owner_retired"])
            self.assertEqual(revoked, [(capture_token, 1), (mutation_token, 1)])
            self.assertFalse(capture_token.exists())
            self.assertFalse(mutation_token.exists())
            for name in (
                    "capture-session-orphan-retirement-receipt.v1.json",
                    "mutation-session-orphan-retirement-receipt.v1.json"):
                receipt = CLOSER._strict(
                    (control / name).read_bytes(), "TEST")
                self.assertEqual(receipt["lease_generation"], 1)
                self.assertEqual(
                    receipt["durable_hsl_audit"],
                    "GENERATION_ABSENT_AFTER_REVOKE")

    def test_orphan_revoke_requires_second_durable_store_absence_response(self):
        responses = [
            CLOSER.subprocess.CompletedProcess(
                [], 0, stdout=CLOSER.canonical_json({
                    "accepted": True, "reason_code": "OK",
                    "lease_generation": 1}), stderr=b""),
            CLOSER.subprocess.CompletedProcess(
                [], 4, stdout=CLOSER.canonical_json({
                    "accepted": False,
                    "reason_code": "SESSION_LEASE_NOT_FOUND",
                    "lease_generation": 1}), stderr=b""),
        ]
        with mock.patch.object(
                CLOSER.subprocess, "run", side_effect=responses) as run:
            reason = CLOSER._revoke_owner(Path("/test/token"), 1)
        self.assertEqual(reason, "SESSION_LEASE_NOT_FOUND")
        self.assertEqual(run.call_count, 2)

        repeated_success = CLOSER.subprocess.CompletedProcess(
            [], 0, stdout=CLOSER.canonical_json({
                "accepted": True, "reason_code": "OK",
                "lease_generation": 1}), stderr=b"")
        with mock.patch.object(
                CLOSER.subprocess, "run",
                side_effect=[repeated_success, repeated_success]):
            with self.assertRaisesRegex(
                    CLOSER.CloserError,
                    "CRASH_CLOSER_OWNER_REVOKE_UNCERTAIN"):
                CLOSER._revoke_owner(Path("/test/token"), 1)

    def test_crash_closer_preserves_watch_generation_absence_receipt(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            intent_path = root / "capture-session-owner.v1.json"
            intent_body = {
                "schema": "hepta.p1-paper-canary-capture-owner.v1",
                "version": 1, "status": "OWNER_MAY_EXIST",
                "created_at_ms": 1_000_000, "campaign_id": "campaign-a",
                "domain_id": "alpha", "cycle_id": "cycle-a",
                "template_id": "watch", "session_id": "capture-session-a",
                "expected_lease_generation": 1, "peer_uid": 2104,
                "token_path": str(root / "capture.token"),
                "token_sha256": CLOSER.sha(b"capture-token\n"),
                "paper_only": True, "live_authorized": False,
                "authority_granted": False,
            }
            intent = JOINER.sealed_body(intent_body)
            intent_raw = CLOSER.canonical_json(intent)
            intent_path.write_bytes(intent_raw)
            intent_path.chmod(0o600)
            retirement_body = {
                "schema": "hepta.p1-paper-canary-capture-owner-retirement.v1",
                "version": 1, "status": "RETIRED",
                "completed_at_ms": 1_000_001,
                "campaign_id": "campaign-a", "domain_id": "alpha",
                "cycle_id": "cycle-a", "template_id": "watch",
                "session_id": "capture-session-a", "lease_generation": 1,
                "token_sha256": intent["token_sha256"],
                "owner_intent_body_sha256": intent["body_sha256"],
                "revoke_accepted": True, "revoke_reason_code": "OK",
                "revoke_audit_reason_code": "SESSION_LEASE_NOT_FOUND",
                "durable_hsl_audit": "GENERATION_ABSENT_AFTER_REVOKE",
                "paper_only": True, "live_authorized": False,
                "authority_granted": False,
            }
            retirement = JOINER.sealed_body(retirement_body)
            retirement_path = root / \
                "capture-session-retirement-receipt.v1.json"
            retirement_path.write_bytes(CLOSER.canonical_json(retirement))
            retirement_path.chmod(0o600)
            original_stable = CLOSER.stable_read

            def stable(path, **parameters):
                parameters["uid"] = os.getuid()
                parameters["gid"] = os.getgid()
                return original_stable(Path(path), **parameters)

            with mock.patch.object(CLOSER, "stable_read", side_effect=stable), \
                    mock.patch.object(CLOSER, "_revoke_owner") as revoke:
                retired = CLOSER._recover_intent(
                    intent_path=intent_path,
                    token_candidates=(root / "capture.token",),
                    receipt_path=root /
                        "capture-session-orphan-retirement-receipt.v1.json",
                    campaign="campaign-a", cycle="cycle-a", template="watch")
            self.assertTrue(retired)
            self.assertFalse(intent_path.exists())
            revoke.assert_not_called()

    def test_paper_generic_revoke_is_forbidden_after_handoff_artifact(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            control_root = root / "control"
            artifact_root = root / "artifact"
            owner_root = root / "owner"
            control = control_root / "campaign-a" / "cycle-a"
            artifact = artifact_root / "campaign-a" / "cycle-a"
            control.mkdir(parents=True)
            artifact.mkdir(parents=True)
            owner_root.mkdir(parents=True)
            for name in (
                    "capture-request.v1.json",
                    "normalized-launch-intent-receipt.v1.json"):
                (control / name).write_bytes(b"pre-handoff\n")
            for name in (
                    "tool-catalog.v1.json", "read-only-capture.v1.json",
                    "original-strategy-decision.v1.json",
                    "canary-strategy-state.v1.json"):
                (artifact / name).write_bytes(b"read-only\n")
            (control / "execution-handoff.v1.json").write_bytes(b"handoff\n")
            with mock.patch.object(CLOSER, "CONTROL_ROOT", control_root), \
                    mock.patch.object(CLOSER, "ARTIFACT_ROOT", artifact_root), \
                    mock.patch.object(CLOSER, "OWNER_ROOT", owner_root), \
                    mock.patch.object(
                        CLOSER, "ROOT_FINALIZER_WAL",
                        root / "root-finalizer-wal.json"), \
                    mock.patch.object(
                        CLOSER, "LEGACY_ROOT_FINALIZER_WALS",
                        tuple(root / f"legacy-root-finalizer-wal-{index}.json"
                              for index in range(4))), \
                    mock.patch.object(
                        CLOSER, "ACTIVE_EXECUTION_HANDOFF",
                        root / "active-execution-handoff.v1.json"):
                with self.assertRaisesRegex(
                        CLOSER.CloserError,
                        "CRASH_CLOSER_PAPER_OWNER_REQUIRES_HSL8"):
                    CLOSER._prove_paper_owner_is_strictly_pre_handoff(
                        "campaign-a", "cycle-a")

    def test_crash_closer_accepts_only_hsl8_normal_v4_retirement(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            control_root = root / "control"
            directory = control_root / "campaign-a" / "cycle-a"
            directory.mkdir(parents=True)
            bearer_path = root / "revoke-token"
            bearer_raw = b"0" * 64 + b"\n"
            bearer_path.write_bytes(bearer_raw)
            bearer_path.chmod(0o600)
            terminal_evidence_path = root / "terminal-evidence.v1"
            preliminary_sha = digest("preliminary-receipt")
            terminal_receipt_values = {
                key: "0" for key in CLOSER.TERMINAL_ACK_RECEIPT_KEYS}
            terminal_receipt_values.update({
                "schema": "hepta.paper-session-terminal-ack-receipt.v3",
                "version": "3", "status": "TERMINAL_ACKED",
                "terminal_proof_kind":
                    "POST_CUTOFF_SIGNED_ACCOUNT_WITNESS_V1",
                "recovery_id": "recovery-a",
                "finalization_id": "finalization-a",
                "campaign_id": "campaign-a", "cycle_id": "cycle-a",
                "expected_owner_set_sha256": digest("owner-set"),
                "expected_owner_count": "1", "owner_set_canonical_hex": "00",
                "preliminary_finalization_receipt_sha256": preliminary_sha,
                "owner_agent_id": "hepta-agent-alpha",
                "owner_session_id": "session-a",
                "owner_account": "DU123",
                "owner_execution_domain": "PAPER:alpha",
                "account_id_sha256": CLOSER.sha(b"DU123"),
                "execution_service_epoch": "epoch-a",
                "execution_service_fencing_generation": "7",
                "recovery_ingress_fence": "7",
                "terminalization_generation": "1",
                "terminalizing_latch_sha256": digest("terminal-latch"),
                "terminal_external_halt_latch_sha256":
                    digest("external-latch"),
                "transport_cutoff_receipt_file_sha256":
                    digest("cutoff-file"),
                "transport_cutoff_receipt_body_sha256":
                    digest("cutoff-body"),
                "post_cutoff_terminal_witness_file_sha256":
                    digest("witness-file"),
                "post_cutoff_terminal_witness_body_sha256":
                    digest("witness-body"),
                "provider_trust_policy_file_sha256": digest("trust-file"),
                "provider_trust_policy_body_sha256": digest("trust-body"),
                "provider_id": "reviewed-provider-test",
                "provider_capability":
                    "ACCOUNT_WIDE_ATOMIC_OR_CAUSAL_POST_CUTOFF_READ_ONLY_V1",
                "signed_account_payload_sha256": digest("signed-payload"),
                "signed_account_signature_sha256":
                    digest("signed-signature"),
                "host_boot_id": "11111111-1111-1111-1111-111111111111",
                "egress_publisher_pid": "4102",
                "egress_publisher_start_ticks": "99123",
                "egress_policy_generation": "23",
                "egress_policy_sha256": digest("egress-policy"),
                "query_started_after_challenge": "1",
                "observed_after_cutoff": "1",
                "snapshot_consistency": "CAUSAL_WATERMARK",
                "causal_watermark_dominates_cutoff": "1",
                "causal_watermark_dominates_all_mutations": "1",
                "account_queries_complete": "1",
                "active_orders_complete": "1",
                "completed_orders_complete": "1",
                "executions_complete": "1",
                "positions_complete": "1", "cash_fx_complete": "1",
                "risk_complete": "1",
                "known_mutation_command_set_sha256":
                    digest("known-mutations"),
                "known_mutation_command_count": "1",
                "known_correlation_set_sha256":
                    digest("known-correlations"),
                "known_correlation_count": "1",
                "all_known_mutation_commands_settled": "1",
                "settled_mutation_command_count": "1",
                "unknown_mutation_command_count": "0",
                "unresolved_mutation_command_count": "0",
                "unknown_active_order_count": "0",
                "active_order_count": "0", "position_count": "0",
                "nonzero_cash_fx_count": "0",
                "gross_absolute_position": "0",
                "gross_fx_exposure": "0", "gross_risk": "0",
                "mutation_connector_count": "0",
                "broker_socket_count": "0", "broker_process_count": "0",
                "broker_credential_count": "0",
                "execution_service_inactive": "1",
                "paper_units_inactive": "1",
                "execution_mutation_gate_closed": "1",
                "broker_transport_connected": "0",
                "broker_reconnect_permitted": "0",
                "read_only_authority": "1", "mutation_attempted": "0",
                "paper_authorized": "0", "live_authorized": "0",
                "mutation_authorized": "0", "direct_broker_access": "0",
                "order_submission_authorized": "0",
                "order_authorized": "0", "paper_only": "1",
                "authority_granted": "0",
                "terminal_external_halt_latch_durable": "1",
                "terminal_witness_durable": "1",
                "current_host_boundary_verified": "1",
                "terminal_evidence_file_sha256":
                    digest("terminal-evidence-file"),
                "terminal_evidence_body_sha256":
                    digest("terminal-evidence-body"),
            })
            # Build the same independent HPE1 wire witness used by the
            # root-owned terminal verifier.  The crash closer must bind this
            # exact file, not merely trust the receipt's self-hash.
            hpe_values = {
                key: terminal_receipt_values[key]
                for key in CLOSER.EXTERNAL_TERMINAL_EVIDENCE_KEYS
                if key in terminal_receipt_values}
            hpe_values.update({
                "schema": "hepta.paper-terminal-witness-evidence.v1",
                "version": "1",
                "status":
                    "CURRENT_POST_CUTOFF_TERMINAL_WITNESS_VERIFIED",
                "terminal_external_halt_latch_durable": "1",
                "terminal_witness_durable": "1",
                "current_host_boundary_verified": "1",
            })
            hpe_prefix = b"HPE1\n" + b"".join(
                f"{key}={hpe_values[key]}\n".encode("ascii")
                for key in CLOSER.EXTERNAL_TERMINAL_EVIDENCE_KEYS[:-1])
            hpe_values["evidence_body_sha256"] = CLOSER.sha(hpe_prefix)
            terminal_evidence_raw = hpe_prefix + (
                f"evidence_body_sha256={hpe_values['evidence_body_sha256']}\n"
            ).encode("ascii")
            terminal_evidence_path.write_bytes(terminal_evidence_raw)
            terminal_evidence_path.chmod(0o400)
            terminal_receipt_values.update({
                "terminal_evidence_file_sha256":
                    CLOSER.sha(terminal_evidence_raw),
                "terminal_evidence_body_sha256":
                    hpe_values["evidence_body_sha256"],
            })
            terminal_receipt = "".join(
                f"{key}={terminal_receipt_values[key]}\n"
                for key in CLOSER.TERMINAL_ACK_RECEIPT_KEYS)
            terminal_result = {
                field: "fixture" for field in CLOSER.TERMINAL_ACK_RESULT_FIELDS}
            terminal_result.update({
                "accepted": True,
                "reason_code": "PAPER_FINALIZATION_TERMINAL_ACKED",
                "lease_generation": 7, "paper_finalization_state": "ACKED",
                "paper_finalization_required": True,
                "recovery_id": "recovery-a",
                "finalization_id": "finalization-a",
                "expected_owner_set_sha256": digest("owner-set"),
                "expected_owner_count": 1,
                "owner_token_sha256": digest("owner-token"),
                "finalization_receipt_sha256": CLOSER.sha(
                    terminal_receipt.encode("ascii")),
                "finalization_receipt": terminal_receipt,
                "owner_audit_authoritative": True,
                "owner_audit_complete": True,
                "owner_active_order_count": 0,
                "owner_uncertain_command_count": 0,
                "owner_account": "DU123",
                "owner_execution_domain": "PAPER:alpha",
                "execution_service_epoch": "epoch-a",
                "execution_service_fencing_generation": 7,
                "broker_connection_epoch": 0,
                "broker_active_generation": 0,
                "broker_terminal_generation": 0,
                "broker_risk_generation": 0,
                "broker_account_generation": 0,
                "broker_position_generation": 0,
                "broker_fx_cash_generation": 0,
                "broker_exposure_generation": 0,
                "broker_terminal_exposure_generation": 0,
                "broker_risk_absorbed_exposure_generation": 0,
                "broker_global_active_order_count": 0,
                "broker_post_fill_risk_reconciliation_pending": False,
                "broker_recovery_audit_barrier_complete": False,
                "broker_recovery_audit_new_connection_epoch_required": False,
                "broker_position_quantity": "0",
                "broker_gross_absolute_position": "0",
                "preliminary_finalization_receipt_sha256": preliminary_sha,
                "terminalization_service_epoch": "epoch-a",
                "terminalization_service_fencing_generation": 7,
                "terminalization_generation": 1,
                "terminal_latch_sha256": digest("terminal-latch"),
                "execution_mutation_gate_closed": True,
                "broker_transport_connected": False,
                "broker_event_ingress_halted": True,
                "broker_callback_queue_drained": False,
                "broker_callbacks_in_flight": 0,
                "broker_reconnect_permitted": False,
                "terminal_latch_durable": True,
                "terminal_runtime_latch_loaded": False,
                "terminal_runtime_verified": False,
                "terminal_replay": True,
                "terminal_proof_kind":
                    "POST_CUTOFF_SIGNED_ACCOUNT_WITNESS_V1",
                "terminal_external_halt_latch_sha256":
                    digest("external-latch"),
                "transport_cutoff_receipt_file_sha256":
                    digest("cutoff-file"),
                "transport_cutoff_receipt_body_sha256":
                    digest("cutoff-body"),
                "post_cutoff_terminal_witness_file_sha256":
                    digest("witness-file"),
                "post_cutoff_terminal_witness_body_sha256":
                    digest("witness-body"),
                "terminal_evidence_sha256":
                    CLOSER.sha(terminal_evidence_raw),
                "terminal_evidence_body_sha256":
                    hpe_values["evidence_body_sha256"],
                "egress_policy_sha256": digest("egress-policy"),
                "egress_publisher_pid": 4102,
                "egress_publisher_start_ticks": 99123,
                "provider_trust_policy_body_sha256": digest("trust-body"),
                "signed_account_signature_sha256":
                    digest("signed-signature"),
                "terminal_external_latch_loaded": True,
                "terminal_current_evidence_verified": True,
            })
            retirement_body = {
                field: "fixture" for field in
                CLOSER.OWNER_RETIREMENT_FIELDS - {"body_sha256"}}
            retirement_body.update({
                "schema": (
                    "hepta.p1-paper-canary-durable-owner-retirement-"
                    "receipt.v4"),
                "version": 4, "completed_at_ms": 1_000_001,
                "campaign_id": "campaign-a", "domain_id": "alpha",
                "cycle_id": "cycle-a", "cleanup_command_id": "cleanup-a",
                "session_owner_reference_sha256": digest("owner-reference"),
                "token_sha256": digest("owner-token"),
                "lease_generation": 7, "session_id": "session-a",
                "paper_finalization_required": True,
                "recovery_id": "recovery-a",
                "finalization_id": "finalization-a",
                "expected_owner_set_sha256": digest("owner-set"),
                "expected_owner_count": 1, "owner_set_canonical_hex": "00",
                "owner_token_sha256": digest("owner-token"),
                "query_command_id": "cleanup-a",
                "recovery_query_result": {
                    "owner_account": "DU123",
                    "owner_execution_domain": "PAPER:alpha",
                },
                "finalization_receipt_sha256": preliminary_sha,
                "finalization_receipt": "preliminary\n",
                "finalization_result": {},
                "terminal_ack_receipt_sha256": CLOSER.sha(
                    terminal_receipt.encode("ascii")),
                "terminal_ack_receipt": terminal_receipt,
                "terminal_ack_result": terminal_result,
                "terminal_acknowledged": True,
                "durable_hsl_audit": "HSL8_POST_CUTOFF_SIGNED_TERMINAL_ACK_V3",
                "hsl_owner_purged": True, "broker_flat_proven": True,
                "terminal_flat_proof_kind": "HSL8_POST_CUTOFF_SIGNED_TERMINAL_ACK_V3",
                "pre_cleanup_flat_evidence_role":
                    "DIAGNOSTIC_ONLY_NOT_TERMINAL_PROOF",
                "authority_path": "/test/authority.json",
                "authority_file_sha256": digest("authority-file"),
                "authority_body_sha256": digest("authority-body"),
                "revoke_bearer_path": str(bearer_path),
                "revoke_bearer_file_sha256": CLOSER.sha(bearer_raw),
                "credentials_destroyed": True,
                "mutation_credentials_destroyed": True,
                "credentials_destroyed_scope":
                    "PEER_MUTATION_TOKEN_AND_AUTHORITY_ONLY",
                "retained_root_recovery_bearer_count": 1,
                "retained_root_recovery_bearer_path": str(bearer_path),
                "retained_root_recovery_bearer_sha256":
                    CLOSER.sha(bearer_raw),
                "retained_root_recovery_bearer_mutation_authority": False,
                "runtime_session_count": 0,
                "durable_owner_count": 0,
                "durable_owner_status": "RETIRED",
                "paper_only": True, "live_authorized": False,
                "authority_granted": False,
            })
            retirement = CLOSER.canonical_json({
                **retirement_body,
                "body_sha256": CLOSER.sha(
                    CLOSER.canonical_json(retirement_body)),
            })
            retirement_path = directory / \
                "durable-owner-retirement-receipt.v4.json"
            retirement_path.write_bytes(retirement)
            retirement_path.chmod(0o600)
            receipt_body = {
                "schema": "hepta.p1-paper-canary-root-cleanup-receipt.v4",
                "version": 4, "status": "ROOT_CLEANUP_COMPLETE_DENY_ALL",
                "campaign_id": "campaign-a", "cycle_id": "cycle-a",
                "cleanup_command_id": "cleanup-a",
                "execution_service_epoch": "epoch-a",
                "execution_service_fencing_generation": 7,
                "broker_deny_all": True, "durable_owner_count": 0,
                "durable_owner_status": "RETIRED",
                "mutation_credentials_destroyed": True,
                "credentials_destroyed_scope":
                    "PEER_MUTATION_TOKEN_AND_AUTHORITY_ONLY",
                "retained_root_recovery_bearer_count": 1,
                "retained_root_recovery_bearer_path": str(bearer_path),
                "retained_root_recovery_bearer_sha256":
                    CLOSER.sha(bearer_raw),
                "retained_root_recovery_bearer_mutation_authority": False,
                "completed_actions": list(
                    COORDINATOR.NORMAL_REQUIRED_ACTIONS),
                "durable_owner_retirement_receipt_path":
                    str(retirement_path),
                "durable_owner_retirement_receipt_file_sha256":
                    CLOSER.sha(retirement),
                "durable_owner_retirement_receipt_body_sha256":
                    CLOSER._strict(retirement, "TEST")["body_sha256"],
                "authority_granted": False,
            }
            receipt = CLOSER.canonical_json({
                **receipt_body,
                "body_sha256": CLOSER.sha(
                    CLOSER.canonical_json(receipt_body)),
            })
            receipt_path = directory / "root-cleanup-receipt.v4.json"
            receipt_path.write_bytes(receipt)
            receipt_path.chmod(0o600)
            original_stable = CLOSER.stable_read

            def stable(path, **parameters):
                if Path(path).is_relative_to(root):
                    parameters["uid"] = os.getuid()
                    parameters["gid"] = os.getgid()
                return original_stable(Path(path), **parameters)

            with mock.patch.object(CLOSER, "CONTROL_ROOT", control_root), \
                    mock.patch.object(CLOSER, "OWNER_REVOKE", bearer_path), \
                    mock.patch.object(
                        CLOSER, "TERMINAL_EVIDENCE_PATH",
                        terminal_evidence_path), \
                    mock.patch.object(CLOSER, "stable_read", side_effect=stable), \
                    mock.patch.object(
                        CLOSER.subprocess, "run",
                        return_value=CLOSER.subprocess.CompletedProcess(
                            [], 0, CLOSER.canonical_json(terminal_result), b"")):
                self.assertTrue(CLOSER._normal_owner_retired(
                    "campaign-a", "cycle-a"))
                self.assertEqual(
                    CLOSER.OWNER_RETIREMENT_FIELDS,
                    TERMINAL.OWNER_RETIREMENT_FIELDS)
                self.assertEqual(
                    CLOSER.TERMINAL_ACK_RESULT_FIELDS,
                    TERMINAL.TERMINAL_ACK_RESULT_FIELDS)
                self.assertEqual(
                    CLOSER.TERMINAL_ACK_RECEIPT_KEYS,
                    TERMINAL.TERMINAL_ACK_RECEIPT_KEYS)
                root_receipt = CLOSER._strict(receipt, "TEST")
                retirement_value = CLOSER._strict(retirement, "TEST")
                parsed_hpe, exact_hpe, hpe_prefix = \
                    CLOSER._external_parse_terminal_evidence(
                        terminal_evidence_raw)
                self.assertEqual(exact_hpe, terminal_evidence_raw)
                self.assertEqual(
                    parsed_hpe["evidence_body_sha256"],
                    CLOSER.sha(hpe_prefix))
                # Recomputing the HSL8 receipt hash must not authorize a
                # changed provider/egress/known-command provenance field.
                for field, replacement in (
                        ("provider_trust_policy_file_sha256",
                         digest("tampered-trust-file")),
                        ("provider_id", "attacker-provider"),
                        ("signed_account_payload_sha256",
                         digest("tampered-signed-payload")),
                        ("egress_policy_generation", "24"),
                        ("known_mutation_command_set_sha256",
                         digest("tampered-known-mutations")),
                        ("known_correlation_set_sha256",
                         digest("tampered-known-correlations")),
                        ("known_correlation_count", "2")):
                    with self.subTest(independent_field=field):
                        changed = deepcopy(retirement_value)
                        values = {
                            key: terminal_receipt_values[key]
                            for key in CLOSER.TERMINAL_ACK_RECEIPT_KEYS}
                        values[field] = replacement
                        changed_receipt = "".join(
                            f"{key}={values[key]}\n"
                            for key in CLOSER.TERMINAL_ACK_RECEIPT_KEYS)
                        changed["terminal_ack_receipt"] = changed_receipt
                        changed["terminal_ack_receipt_sha256"] = CLOSER.sha(
                            changed_receipt.encode("ascii"))
                        changed["terminal_ack_result"] = deepcopy(
                            retirement_value["terminal_ack_result"])
                        changed["terminal_ack_result"][
                            "finalization_receipt"] = changed_receipt
                        changed["terminal_ack_result"][
                            "finalization_receipt_sha256"] = \
                            changed["terminal_ack_receipt_sha256"]
                        changed_body = dict(changed)
                        changed_body.pop("body_sha256")
                        changed["body_sha256"] = CLOSER.sha(
                            CLOSER.canonical_json(changed_body))
                        with self.assertRaisesRegex(
                                CLOSER.CloserError,
                                "CRASH_CLOSER_NORMAL_RECEIPT_INVALID"):
                            CLOSER._validate_hsl8_retirement(
                                changed, root_receipt,
                                terminal_evidence_raw=terminal_evidence_raw)
                # Even a fully resealed HPE1 body and matching receipt hashes
                # cannot alter the independent provider identity.
                tampered_hpe_values = dict(parsed_hpe)
                tampered_hpe_values["provider_id"] = "attacker-provider"
                tampered_hpe_prefix = b"HPE1\n" + b"".join(
                    f"{key}={tampered_hpe_values[key]}\n".encode("ascii")
                    for key in CLOSER.EXTERNAL_TERMINAL_EVIDENCE_KEYS[:-1])
                tampered_hpe_values["evidence_body_sha256"] = CLOSER.sha(
                    tampered_hpe_prefix)
                tampered_hpe = tampered_hpe_prefix + (
                    "evidence_body_sha256=" +
                    tampered_hpe_values["evidence_body_sha256"] + "\n"
                ).encode("ascii")
                changed = deepcopy(retirement_value)
                changed_values = {
                    key: terminal_receipt_values[key]
                    for key in CLOSER.TERMINAL_ACK_RECEIPT_KEYS}
                changed_values.update({
                    "terminal_evidence_file_sha256":
                        CLOSER.sha(tampered_hpe),
                    "terminal_evidence_body_sha256":
                        tampered_hpe_values["evidence_body_sha256"],
                })
                changed_receipt = "".join(
                    f"{key}={changed_values[key]}\n"
                    for key in CLOSER.TERMINAL_ACK_RECEIPT_KEYS)
                changed["terminal_ack_receipt"] = changed_receipt
                changed["terminal_ack_receipt_sha256"] = CLOSER.sha(
                    changed_receipt.encode("ascii"))
                changed["terminal_ack_result"] = deepcopy(
                    retirement_value["terminal_ack_result"])
                changed["terminal_ack_result"].update({
                    "finalization_receipt": changed_receipt,
                    "finalization_receipt_sha256":
                        changed["terminal_ack_receipt_sha256"],
                    "terminal_evidence_sha256": CLOSER.sha(tampered_hpe),
                    "terminal_evidence_body_sha256":
                        tampered_hpe_values["evidence_body_sha256"],
                })
                changed_body = dict(changed)
                changed_body.pop("body_sha256")
                changed["body_sha256"] = CLOSER.sha(
                    CLOSER.canonical_json(changed_body))
                with self.assertRaisesRegex(
                        CLOSER.CloserError,
                        "CRASH_CLOSER_NORMAL_RECEIPT_INVALID"):
                    CLOSER._validate_hsl8_retirement(
                        changed, root_receipt,
                        terminal_evidence_raw=tampered_hpe)
                for field, replacement in (
                        ("durable_hsl_audit",
                         "HSL7_POST_FENCE_COMPOSITE_AUDIT"),
                        ("terminal_flat_proof_kind",
                         "HSL7_POST_FENCE_COMPOSITE_AUDIT")):
                    with self.subTest(field=field):
                        changed = CLOSER._strict(retirement, "TEST")
                        changed[field] = replacement
                        changed_body = dict(changed)
                        changed_body.pop("body_sha256")
                        changed["body_sha256"] = CLOSER.sha(
                            CLOSER.canonical_json(changed_body))
                        with self.assertRaisesRegex(
                                CLOSER.CloserError,
                                "CRASH_CLOSER_NORMAL_RECEIPT_INVALID"):
                            CLOSER._validate_hsl8_retirement(
                                changed, root_receipt,
                                terminal_evidence_raw=terminal_evidence_raw)
                changed = CLOSER._strict(retirement, "TEST")
                changed["terminal_ack_result"]["terminal_replay"] = False
                changed_body = dict(changed)
                changed_body.pop("body_sha256")
                changed["body_sha256"] = CLOSER.sha(
                    CLOSER.canonical_json(changed_body))
                with self.assertRaisesRegex(
                        CLOSER.CloserError,
                        "CRASH_CLOSER_NORMAL_RECEIPT_INVALID"):
                    CLOSER._validate_hsl8_retirement(
                        changed, root_receipt,
                        terminal_evidence_raw=terminal_evidence_raw)
                for name in (
                        "root-cleanup-receipt.v1.json",
                        "root-cleanup-receipt.v2.json",
                        "root-cleanup-receipt.v3.json",
                        "durable-owner-retirement-receipt.v1.json",
                        "durable-owner-retirement-receipt.v2.json",
                        "durable-owner-retirement-receipt.v3.json"):
                    with self.subTest(name=name):
                        legacy = directory / name
                        legacy.write_bytes(b"legacy\n")
                        with self.assertRaisesRegex(
                                CLOSER.CloserError,
                                "CRASH_CLOSER_LEGACY_NORMAL_RECEIPT_PRESENT"):
                            CLOSER._normal_owner_retired(
                                "campaign-a", "cycle-a")
                        legacy.unlink()


if __name__ == "__main__":
    unittest.main(verbosity=2)
