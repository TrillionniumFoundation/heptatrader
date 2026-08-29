#!/usr/bin/env python3

from __future__ import annotations

import contextlib
import fcntl
from itertools import combinations
from importlib.machinery import SourceFileLoader
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
import uuid


REAL_FSTAT = os.fstat
REAL_LSTAT = os.lstat
REAL_STAT = os.stat
ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "scripts/run_paper_repair.py"
loader = SourceFileLoader("hepta_paper_repair", str(SOURCE))
spec = importlib.util.spec_from_loader(loader.name, loader)
assert spec is not None and spec.loader is not None
repair = importlib.util.module_from_spec(spec)
spec.loader.exec_module(repair)
ORIGINAL_REQUIRE_ACTIVE_LOCAL_PAPER_CONTROL = (
    repair._require_active_local_paper_control)
ORIGINAL_ENSURE_END_FLAT_RECOVERY_RUNTIME = (
    repair._ensure_end_flat_recovery_runtime)
ORIGINAL_VALIDATED_PREPARED_CAMPAIGN = repair._validated_prepared_campaign
PREPARE_SOURCE = ROOT / "scripts/prepare_repair_campaign.py"
prepare_loader = SourceFileLoader("hepta_prepare_paper_campaign", str(PREPARE_SOURCE))
prepare_spec = importlib.util.spec_from_loader(
    prepare_loader.name, prepare_loader)
assert prepare_spec is not None and prepare_spec.loader is not None
prepare = importlib.util.module_from_spec(prepare_spec)
prepare_spec.loader.exec_module(prepare)
ORIGINAL_PREPARE_STABLE_READ = prepare._read_stable_root_file
ORIGINAL_LOAD_DEPLOYMENT_EVIDENCE_TRANSACTION = (
    prepare._load_deployment_evidence_transaction)
LEGACY_V4_UNBOUND_DEPLOYMENT_FIELDS = frozenset({
    "deployment_evidence_file_sha256",
    "deployment_evidence_body_sha256",
    "deployment_install_transaction_id",
})

# Captured verbatim from the linked C++ supervisor fixture's
# HEPTA_REAL_CLI_ACK_REPLAY output.  This is intentionally a JSON wire sample,
# rather than a Python-built lookalike, so the repair consumer stays bound to
# the real sessionctl field names, JSON types, ordered receipt, and hashes.
REAL_TERMINAL_ACK_CLI_JSON = r'''{"accepted":true,"reason_code":"PAPER_FINALIZATION_TERMINAL_ACKED","lease_generation":1,"paper_finalization_state":"ACKED","paper_finalization_required":true,"recovery_id":"recovery-multiowner-1","finalization_id":"finalization-multiowner-1","expected_owner_set_sha256":"sha256:b84109085acbd97a6f6b30cac4c3dbb5e7385e8d2200e659b37c9f150f666cae","expected_owner_count":2,"owner_token_sha256":"sha256:44c2336fedab8ff6a85c74c2b94165377b0981f526adb9487895ca6314165e86","finalization_receipt_sha256":"sha256:1c96d7653b395cc87b370811acc2fee881082adf0ac3a5ce16c39bb52dc0a0ef","finalization_receipt":"schema=hepta.paper-session-terminal-ack-receipt.v2\nversion=2\nstatus=TERMINAL_ACKED\nrecovery_id=recovery-multiowner-1\nfinalization_id=finalization-multiowner-1\nexpected_owner_set_sha256=sha256:b84109085acbd97a6f6b30cac4c3dbb5e7385e8d2200e659b37c9f150f666cae\nexpected_owner_count=2\nowner_set_canonical_hex=7368613235363a34346332333336666564616238666636613835633734633262393431363533373762303938316635323661646239343837383935636136333134313635653836093109343435353331333233333334333509353034313530343535323361363136633730363836310a7368613235363a35633634343964613333356261646233353530393763613261373839383530396263653866656562666533316465646365343737393838323566646439613131093109343435353331333233333334333509353034313530343535323361363136633730363836310a\npreliminary_finalization_receipt_sha256=sha256:6fdccdc0b1c11ced4841d54512ef2c1f57f9bd8ae7fce14e52a415ba69263b09\nowner_account=DU12345\nowner_execution_domain=PAPER:alpha\nexecution_service_epoch=execution-epoch-finalization-1\nexecution_service_fencing_generation=17\nterminalization_generation=1\nterminal_latch_sha256=sha256:eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee\nexecution_mutation_gate_closed=1\nbroker_transport_connected=0\nbroker_event_ingress_halted=1\nbroker_callback_queue_drained=1\nbroker_callbacks_in_flight=0\nbroker_reconnect_permitted=0\nterminal_latch_durable=1\nbroker_connection_epoch=23\nbroker_active_generation=29\nbroker_terminal_generation=31\nbroker_risk_generation=37\nbroker_account_generation=41\nbroker_position_generation=43\nbroker_fx_cash_generation=47\nbroker_exposure_generation=0\nbroker_terminal_exposure_generation=0\nbroker_risk_absorbed_exposure_generation=0\nbroker_global_active_order_count=0\nowner_active_order_count=0\nowner_uncertain_command_count=0\nbroker_post_fill_risk_reconciliation_pending=0\nbroker_recovery_audit_barrier_complete=1\nbroker_recovery_audit_new_connection_epoch_required=0\nbroker_position_quantity=0\nbroker_gross_absolute_position=0\npaper_only=1\nlive_authorized=0\n","owner_audit_authoritative":true,"owner_audit_complete":true,"owner_active_order_count":0,"owner_uncertain_command_count":0,"owner_account":"DU12345","owner_execution_domain":"PAPER:alpha","execution_service_epoch":"execution-epoch-finalization-1","execution_service_fencing_generation":17,"broker_connection_epoch":23,"broker_active_generation":29,"broker_terminal_generation":31,"broker_risk_generation":37,"broker_account_generation":41,"broker_position_generation":43,"broker_fx_cash_generation":47,"broker_exposure_generation":0,"broker_terminal_exposure_generation":0,"broker_risk_absorbed_exposure_generation":0,"broker_global_active_order_count":0,"broker_post_fill_risk_reconciliation_pending":false,"broker_recovery_audit_barrier_complete":true,"broker_recovery_audit_new_connection_epoch_required":false,"broker_position_quantity":"0","broker_gross_absolute_position":"0","preliminary_finalization_receipt_sha256":"sha256:6fdccdc0b1c11ced4841d54512ef2c1f57f9bd8ae7fce14e52a415ba69263b09","terminalization_service_epoch":"execution-epoch-finalization-1","terminalization_service_fencing_generation":17,"terminalization_generation":1,"terminal_latch_sha256":"sha256:eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee","execution_mutation_gate_closed":true,"broker_transport_connected":false,"broker_event_ingress_halted":true,"broker_callback_queue_drained":true,"broker_callbacks_in_flight":0,"broker_reconnect_permitted":false,"terminal_latch_durable":true,"terminal_runtime_latch_loaded":true,"terminal_runtime_verified":true,"terminal_replay":true}'''


class FakeAgent:
    SCHEMA = "hepta.local-ai-paper-agent-state.v3"

    @staticmethod
    def empty_state() -> dict[str, object]:
        return {
            "schema": FakeAgent.SCHEMA,
            "recovery_required": False,
            "trading_suspended": False,
            "pending_order_id": None,
            "runtime_binding": None,
        }

    @staticmethod
    def write_json(path: Path, value: object) -> None:
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(value), encoding="ascii")
        temporary.replace(path)

    @staticmethod
    def _quantity_equal(left: float, right: float) -> bool:
        return left == right

    @staticmethod
    def current_runtime_binding(_arguments: object) -> dict[str, object]:
        return {
            "campaign_id": "new-campaign",
            "execution_service_epoch": "hexec-v6-" + "1" * 32,
            "execution_service_fencing_generation": 9,
            "tool_gateway_epoch": "htgw-v1-" + "2" * 32,
            "tool_session_token_sha256": "sha256:" + "3" * 64,
        }


class PaperRepairTests(unittest.TestCase):
    def setUp(self) -> None:
        defaults = (
            {"HEPTA_LOCAL_AI_CAMPAIGN_ID": "campaign-test"},
            {"campaign_id": "campaign-test", "expires_at_ms": 10**18},
        )
        for target, replacement in (
                ("_campaign_lifecycle_locks",
                 mock.Mock(side_effect=lambda: contextlib.nullcontext())),
                ("_broker_mutation_lock", mock.Mock(side_effect=lambda **_kw:
                    contextlib.nullcontext(
                        SimpleNamespace(acquired=True)))),
                ("_force_safe_recovery_after_admission_failure", mock.Mock()),
                ("_validated_prepared_campaign",
                 mock.Mock(return_value=defaults)),
                ("_require_only_primary_session_authority", mock.Mock()),
                ("_require_active_local_paper_control", mock.Mock()),
                ("_ensure_end_flat_recovery_runtime", mock.Mock()),
                ("_verify_waiting_timer", mock.Mock())):
            patcher = mock.patch.object(repair, target, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

    @staticmethod
    def root_metadata() -> SimpleNamespace:
        return SimpleNamespace(
            st_mode=stat.S_IFREG | 0o600, st_nlink=1,
            st_uid=0, st_gid=0)

    @staticmethod
    def root_fstat(descriptor: int) -> SimpleNamespace:
        metadata = REAL_FSTAT(descriptor)
        return SimpleNamespace(
            st_mode=metadata.st_mode, st_nlink=metadata.st_nlink,
            st_uid=0, st_gid=0, st_size=metadata.st_size,
            st_dev=metadata.st_dev, st_ino=metadata.st_ino,
            st_mtime_ns=metadata.st_mtime_ns,
            st_ctime_ns=metadata.st_ctime_ns)

    @staticmethod
    def root_path_metadata(metadata: os.stat_result) -> SimpleNamespace:
        return SimpleNamespace(
            st_mode=metadata.st_mode, st_nlink=metadata.st_nlink,
            st_uid=0, st_gid=0, st_size=metadata.st_size,
            st_dev=metadata.st_dev, st_ino=metadata.st_ino,
            st_mtime_ns=metadata.st_mtime_ns,
            st_ctime_ns=metadata.st_ctime_ns)

    @staticmethod
    def external_p1_control_policy() -> dict[str, object]:
        valid_after_ms = 1_000_000
        return {
            "schema": repair.ACTIVE_POLICY_SCHEMA,
            "version": 5,
            "campaign_id": "p1-campaign-test",
            "domain_id": "alpha",
            "paper_only": True,
            "live_authorized": False,
            "valid_after_ms": valid_after_ms,
            "expires_at_ms": (
                valid_after_ms + repair.EXTERNAL_P1_POLICY_DURATION_MS),
            "max_cycles": 1,
            "max_quantity": 1,
            "max_active_orders": 1,
            "order_type": "LMT",
            "tif": "DAY",
            "end_flat_required": True,
            "source_baseline_sha256": "sha256:" + "1" * 64,
            "admission_mode": "external-p1-finalized",
            "watch_handoff_receipt_path":
                "/var/lib/hepta/p1-admission/"
                "p1-watch-to-paper-handoff-receipt-v2.json",
            "watch_handoff_receipt_file_sha256": "sha256:" + "2" * 64,
            "watch_handoff_receipt_body_sha256": "sha256:" + "3" * 64,
        }

    def test_paper_control_enable_command_preserves_local_only_path(
            self) -> None:
        self.assertEqual(
            repair._paper_control_enable_command(
                {"admission_mode": "local-only"}),
            [repair.LOCAL_PAPER_CONTROL, "enable", "--domain", "alpha"])

    def test_paper_control_enable_command_pins_external_p1_boundary(
            self) -> None:
        policy = self.external_p1_control_policy()
        self.assertEqual(
            repair._paper_control_enable_command(policy),
            [
                repair.LOCAL_PAPER_CONTROL, "enable", "--domain", "alpha",
                "--watch-handoff-receipt",
                policy["watch_handoff_receipt_path"],
                "--watch-handoff-receipt-file-sha256",
                policy["watch_handoff_receipt_file_sha256"],
                "--watch-handoff-receipt-body-sha256",
                policy["watch_handoff_receipt_body_sha256"],
                "--campaign-id", policy["campaign_id"],
                "--source-baseline-sha256",
                policy["source_baseline_sha256"],
                "--external-p1-finalized",
            ])

    def test_paper_control_enable_command_rejects_external_p1_drift(
            self) -> None:
        policy = self.external_p1_control_policy()
        for field, value in (
                ("max_quantity", 2),
                ("order_type", "MKT"),
                ("expires_at_ms",
                 policy["expires_at_ms"] + 1),
                ("watch_handoff_receipt_file_sha256",
                 "sha256:" + "0" * 64)):
            drifted = dict(policy)
            drifted[field] = value
            with self.subTest(field=field), self.assertRaisesRegex(
                    RuntimeError, "EXTERNAL_P1_CONTROL_POLICY_INVALID"):
                repair._paper_control_enable_command(drifted)

    @staticmethod
    def external_flatten_preview(
            *, position: object = "0.1", side: str = "SELL",
            order_type: str = "LMT", limit_price: object = "1.1",
    ) -> dict[str, object]:
        now_ms = repair.time.time_ns() // 1_000_000
        return {
            "approved": True,
            "preview_permit": "p" * 32,
            "command_id": "planned-flatten-command",
            "permit_expires_at_ms": now_ms + 5_000,
            "single_use": True,
            "service_epoch": "hexec-v6-" + "1" * 32,
            "service_fencing_generation": 7,
            "authoritative_preview": {
                "source": "IB",
                "authoritative": True,
                "position_connection_epoch": 3,
                "position_generation": 11,
                "position_quantity": position,
                "side": side,
                "quantity": position.lstrip("-") if isinstance(
                    position, str) else position,
                "order_type": order_type,
                "tif": "DAY",
                "limit_price": limit_price,
                "reference_price": limit_price,
                "quote_bid": limit_price,
                "quote_ask": "1.11",
                "quote_subscription_id": "IB:3:4:5",
                "quote_observed_at_ms": now_ms,
                "reduce_only": True,
                "atomic": True,
                "risk_approved": True,
            },
        }

    def test_external_runtime_profile_pins_exact_p1_limits(self) -> None:
        self.assertEqual(
            repair.EXTERNAL_P1_RUNTIME_PROFILE,
            Path("/etc/heptatrader/trust-domains/alpha.ib-paper.env"))
        self.assertEqual(repair.EXTERNAL_P1_RUNTIME_PROFILE_BYTES, 851)
        self.assertEqual(
            repair.EXTERNAL_P1_RUNTIME_PROFILE_SHA256,
            "sha256:7d8395db4f04d65310eb7ec8c87d4aae"
            "84b0ba259b4dc40196d7e19af3ed9e02")
        profile_values = {
            key: "fixed-value"
            for key in repair.EXTERNAL_P1_RUNTIME_PROFILE_KEYS}
        profile_values.update({
            "HEPTA_EXECUTION_EXTERNAL_P1_CANARY_LMT_DAY": "1",
            "HEPTA_EXECUTION_MAX_ORDER_NOTIONAL": "5000",
            "HEPTA_IB_PAPER_MAX_ORDER_QTY": "1",
            "HEPTA_IB_PAPER_MAX_ORDER_NOTIONAL": "5000",
            "HEPTA_IB_PAPER_MAX_ORDERS_PER_MINUTE": "1",
            "HEPTA_IB_PAPER_MAX_ACTIVE_ORDERS": "1",
            "HEPTA_IB_PAPER_MAX_GROSS_POSITION": "1",
            "HEPTA_IB_PAPER_QUOTE_MAX_AGE_MS": "5000",
        })
        raw = "".join(
            f"{key}={profile_values[key]}\n"
            for key in repair.EXTERNAL_P1_RUNTIME_PROFILE_KEYS).encode("ascii")
        profile_pins = {
            "EXTERNAL_P1_RUNTIME_PROFILE_BYTES": len(raw),
            "EXTERNAL_P1_RUNTIME_PROFILE_SHA256":
                "sha256:" + hashlib.sha256(raw).hexdigest(),
        }
        with mock.patch.multiple(repair, **profile_pins), \
                mock.patch.object(
                    repair, "_stable_file_bytes",
                    return_value=(raw, self.root_metadata())):
            values, observed = repair._external_p1_runtime_profile()
        self.assertEqual(observed, raw)
        self.assertEqual(values["HEPTA_EXECUTION_MAX_ORDER_NOTIONAL"], "5000")
        self.assertEqual(values["HEPTA_IB_PAPER_MAX_ORDER_NOTIONAL"], "5000")
        self.assertEqual(values["HEPTA_IB_PAPER_QUOTE_MAX_AGE_MS"], "5000")

        for label, drifted in (
                ("ib-notional", raw.replace(
                    b"HEPTA_IB_PAPER_MAX_ORDER_NOTIONAL=5000",
                    b"HEPTA_IB_PAPER_MAX_ORDER_NOTIONAL=35000")),
                ("execution-notional", raw.replace(
                    b"HEPTA_EXECUTION_MAX_ORDER_NOTIONAL=5000",
                    b"HEPTA_EXECUTION_MAX_ORDER_NOTIONAL=35000")),
                ("quote-4999", raw.replace(
                    b"HEPTA_IB_PAPER_QUOTE_MAX_AGE_MS=5000",
                    b"HEPTA_IB_PAPER_QUOTE_MAX_AGE_MS=4999")),
                ("extra-key", raw + b"HEPTA_UNEXPECTED=1\n"),
        ):
            drifted_pins = {
                "EXTERNAL_P1_RUNTIME_PROFILE_BYTES": len(drifted),
                "EXTERNAL_P1_RUNTIME_PROFILE_SHA256":
                    "sha256:" + hashlib.sha256(drifted).hexdigest(),
            }
            with self.subTest(label=label), \
                    mock.patch.multiple(repair, **drifted_pins), \
                    mock.patch.object(
                        repair, "_stable_file_bytes",
                        return_value=(drifted, self.root_metadata())), \
                    self.assertRaisesRegex(
                        RuntimeError,
                        "EXTERNAL_RECOVERY_RUNTIME_PROFILE_INVALID"):
                repair._external_p1_runtime_profile()

    def test_external_recovery_reasserts_exact_disabled_policy(self) -> None:
        enabled = self.external_p1_control_policy()
        enabled.update({"enabled": True, "mutations_authorized": True})
        disabled = dict(enabled)
        disabled.update({"enabled": False, "mutations_authorized": False})
        with mock.patch.object(
                repair, "_end_flat_persist_policy_disabled") as persist, \
                mock.patch.object(
                    repair, "_external_p1_recovery_policy",
                    return_value=(disabled, b"disabled")):
            observed, raw = repair._external_reassert_disabled_policy(enabled)
        persist.assert_called_once_with(enabled["campaign_id"])
        self.assertEqual(observed, disabled)
        self.assertEqual(raw, b"disabled")

        drifted = dict(disabled)
        drifted["max_quantity"] = 2
        with mock.patch.object(
                repair, "_end_flat_persist_policy_disabled"), \
                mock.patch.object(
                    repair, "_external_p1_recovery_policy",
                    return_value=(drifted, b"drifted")), \
                self.assertRaisesRegex(
                    RuntimeError, "EXTERNAL_RECOVERY_POLICY_NOT_TERMINAL"):
            repair._external_reassert_disabled_policy(enabled)

    def test_external_completed_resume_disables_policy_before_recovery(self) -> None:
        enabled = self.external_p1_control_policy()
        enabled.update({"enabled": True, "mutations_authorized": True})
        disabled = dict(enabled)
        disabled.update({"enabled": False, "mutations_authorized": False})
        authority = {"recovery_id": "external-recovery-test"}
        completion = {"status": "complete"}
        events: list[str] = []

        def reassert(_policy: object) -> tuple[dict[str, object], bytes]:
            events.append("disable-policy")
            return disabled, b"disabled"

        def runtime() -> tuple[dict[str, str], bytes]:
            events.append("runtime-profile")
            return {}, b"profile"

        def loaded(_policy: object) -> tuple[object, bytes, object]:
            events.append("load-authority")
            return authority, b"authority", self.root_metadata()

        with mock.patch.object(
                repair, "_external_p1_recovery_policy",
                return_value=(enabled, b"enabled")), \
                mock.patch.object(
                    repair, "_external_reassert_disabled_policy",
                    side_effect=reassert) as reasserted, \
                mock.patch.object(
                    repair, "_external_p1_runtime_profile",
                    side_effect=runtime), \
                mock.patch.object(repair, "load_agent", return_value=FakeAgent), \
                mock.patch.object(
                    repair, "_load_root_agent_state", return_value={}), \
                mock.patch.object(
                    repair, "_load_external_recovery_authority",
                    side_effect=loaded), \
                mock.patch.object(
                    repair, "_load_external_recovery_completion",
                    return_value=(completion, b"completion")), \
                mock.patch.object(
                    repair, "_external_existing_checkpoint_owners",
                    return_value=[{"token_name": "session.token"}]), \
                mock.patch.object(
                    repair, "_external_recovery_checkpoint", return_value={
                        "phase": "TERMINAL_ACKED", "pending_mutation": None}), \
                mock.patch.object(
                    repair, "_external_terminalize_and_ack",
                    side_effect=lambda _checkpoint: events.append(
                        "replay-terminal")) as replay, \
                mock.patch.object(
                    repair, "_external_cleanup_terminal_owner_material",
                    side_effect=lambda _checkpoint: events.append(
                        "cleanup-owner")) as cleanup, \
                mock.patch.object(
                    repair, "_persist_external_recovery_checkpoint"), \
                mock.patch.object(
                    repair, "_external_verify_completed_control"), \
                mock.patch.object(
                    repair, "_external_mark_recovery_complete"):
            repair._external_risk_recover_locked(
                safety_exit=True, automatic=True)
        reasserted.assert_called_once_with(enabled)
        replay.assert_called_once()
        cleanup.assert_called_once()
        self.assertEqual(
            events, ["disable-policy", "runtime-profile", "load-authority",
                     "replay-terminal", "cleanup-owner"])

    @staticmethod
    def external_owner_material_fixture(
    ) -> tuple[dict[str, object], bytes, bytes]:
        bearer = b"a" * 64 + b"\n"
        token_sha256 = "sha256:" + hashlib.sha256(bearer).hexdigest()
        authority = repair._sealed_json_document({
            "schema": "test-owner-authority.v1",
            "token_name": "session.token",
            "session_id": "session-owner-test",
            "lease_generation": 19,
            "peer_uid": 2104,
            "peer_gid": 2104,
            "token_sha256": token_sha256,
        })
        authority_raw = repair._canonical_json_bytes(authority)
        owner = {
            "token_name": "session.token",
            "token_path": "/run/hepta-agent-alpha/sessions/session.token",
            "authority_path": str(
                repair.SESSION_AUTHORITY_ROOT /
                "session.token.authority.json"),
            "authority_file_sha256": "sha256:" + hashlib.sha256(
                authority_raw).hexdigest(),
            "authority_body_sha256": authority["body_sha256"],
            "lease_generation": 19,
            "session_id": "session-owner-test",
            "peer_uid": 2104,
            "peer_gid": 2104,
            "token_sha256": token_sha256,
            "revoke_bearer_path": str(
                repair.SESSION_AUTHORITY_ROOT /
                "session.token.revoke-token"),
            "revoke_bearer_sha256": token_sha256,
        }
        return owner, authority_raw, bearer

    @staticmethod
    def external_zero_proof(
            proof_index: int, observed_at_ms: int,
    ) -> dict[str, object]:
        return repair._sealed_json_document({
            "schema":
                "hepta.local-ai-paper-external-recovery-zero-proof.v1",
            "version": 1, "proof_index": proof_index,
            "observed_at_ms": observed_at_ms,
            "position_quantity": 0, "gross_absolute_position": 0,
            "active_order_count": 0, "position_generation": proof_index + 10,
            "fx_cash_generation": proof_index + 20,
            "orders_connection_epoch": 7,
            "orders_generation": proof_index + 30, "owner_count": 1,
            "paper_only": True, "live_authorized": False,
        })

    @staticmethod
    def external_finalization_result(
            owners: list[dict[str, object]], owner: dict[str, object],
            *, recovery_id: str = "external-recovery-test-0001",
            overrides: dict[str, object] | None = None,
    ) -> dict[str, object]:
        (owner_set_sha256, owner_count, canonical, account, domain) = (
            repair._external_finalization_owner_binding(owners))
        finalization_id = repair._external_finalization_id(
            recovery_id, owner_set_sha256, owner_count)
        values: dict[str, object] = {
            "execution_service_epoch": "hexec-v7-final-audit",
            "execution_service_fencing_generation": 12,
            "broker_connection_epoch": 13,
            "broker_active_generation": 14,
            "broker_terminal_generation": 15,
            "broker_risk_generation": 16,
            "broker_account_generation": 17,
            "broker_position_generation": 18,
            "broker_fx_cash_generation": 19,
            "broker_exposure_generation": 21,
            "broker_terminal_exposure_generation": 20,
            "broker_risk_absorbed_exposure_generation": 21,
            "broker_global_active_order_count": 0,
            "owner_active_order_count": 0,
            "owner_uncertain_command_count": 0,
            "broker_position_quantity": "0",
            "broker_gross_absolute_position": "0",
        }
        if overrides:
            values.update(overrides)
        receipt_values = {
            "schema": "hepta.paper-session-finalization-receipt.v1",
            "version": "1", "status": "AUDIT_SEALED",
            "recovery_id": recovery_id, "finalization_id": finalization_id,
            "expected_owner_set_sha256": owner_set_sha256,
            "expected_owner_count": str(owner_count),
            "owner_set_canonical_hex": canonical.hex(),
            "owner_account": account, "owner_execution_domain": domain,
            **{key: str(values[key]) for key in (
                "execution_service_epoch",
                "execution_service_fencing_generation",
                "broker_connection_epoch", "broker_active_generation",
                "broker_terminal_generation", "broker_risk_generation",
                "broker_account_generation", "broker_position_generation",
                "broker_fx_cash_generation", "broker_exposure_generation",
                "broker_terminal_exposure_generation",
                "broker_risk_absorbed_exposure_generation",
                "broker_global_active_order_count", "owner_active_order_count",
                "owner_uncertain_command_count")},
            "broker_post_fill_risk_reconciliation_pending": "0",
            "broker_recovery_audit_barrier_complete": "1",
            "broker_recovery_audit_new_connection_epoch_required": "0",
            "broker_position_quantity": str(
                values["broker_position_quantity"]),
            "broker_gross_absolute_position": str(
                values["broker_gross_absolute_position"]),
            "paper_only": "1", "live_authorized": "0",
        }
        receipt = "".join(
            f"{key}={receipt_values[key]}\n"
            for key in
            repair.EXTERNAL_PRELIMINARY_FINALIZATION_RECEIPT_KEYS)
        return {
            "accepted": True,
            "reason_code": "PAPER_FINALIZATION_AUDIT_SEALED",
            "lease_generation": owner["lease_generation"],
            "paper_finalization_state": "AUDIT_SEALED",
            "paper_finalization_required": True,
            "recovery_id": recovery_id, "finalization_id": finalization_id,
            "expected_owner_set_sha256": owner_set_sha256,
            "expected_owner_count": owner_count,
            "owner_token_sha256": owner["token_sha256"],
            "finalization_receipt_sha256": "sha256:" + hashlib.sha256(
                receipt.encode("ascii")).hexdigest(),
            "finalization_receipt": receipt,
            "owner_audit_authoritative": True,
            "owner_audit_complete": True,
            "owner_account": account, "owner_execution_domain": domain,
            "broker_post_fill_risk_reconciliation_pending": False,
            "broker_recovery_audit_barrier_complete": True,
            "broker_recovery_audit_new_connection_epoch_required": False,
            **values,
        }

    @staticmethod
    def external_terminal_ack_result(
            owners: list[dict[str, object]],
            preliminary: dict[str, object], *, replay: bool,
            recovery_id: str = "external-recovery-test-0001",
            overrides: dict[str, object] | None = None,
    ) -> dict[str, object]:
        (owner_set_sha256, owner_count, canonical, account, domain) = (
            repair._external_finalization_owner_binding(owners))
        owner = min(owners, key=lambda item: str(item["token_sha256"]))
        finalization_id = repair._external_finalization_id(
            recovery_id, owner_set_sha256, owner_count)
        digest = lambda label: "sha256:" + hashlib.sha256(
            label.encode("ascii")).hexdigest()
        preliminary_sha256 = preliminary["finalization_receipt_sha256"]
        receipt_values = {
            "schema": "hepta.paper-session-terminal-ack-receipt.v3",
            "version": "3", "status": "TERMINAL_ACKED",
            "terminal_proof_kind":
                "POST_CUTOFF_SIGNED_ACCOUNT_WITNESS_V1",
            "recovery_id": recovery_id, "finalization_id": finalization_id,
            "campaign_id": "campaign-test", "cycle_id": "cycle-test",
            "expected_owner_set_sha256": owner_set_sha256,
            "expected_owner_count": str(owner_count),
            "owner_set_canonical_hex": canonical.hex(),
            "preliminary_finalization_receipt_sha256": preliminary_sha256,
            "owner_agent_id": "hepta-agent-alpha",
            "owner_session_id": owner["session_id"],
            "owner_account": account, "owner_execution_domain": domain,
            "account_id_sha256": "sha256:" + hashlib.sha256(
                account.encode("ascii")).hexdigest(),
            "execution_service_epoch": preliminary[
                "execution_service_epoch"],
            "execution_service_fencing_generation": str(preliminary[
                "execution_service_fencing_generation"]),
            "recovery_ingress_fence": str(owner["lease_generation"]),
            "terminalization_generation": "1",
            "terminalizing_latch_sha256": digest("terminalizing-latch"),
            "terminal_external_halt_latch_sha256": digest("external-latch"),
            "transport_cutoff_receipt_file_sha256": digest("cutoff-file"),
            "transport_cutoff_receipt_body_sha256": digest("cutoff-body"),
            "post_cutoff_terminal_witness_file_sha256": digest(
                "witness-file"),
            "post_cutoff_terminal_witness_body_sha256": digest(
                "witness-body"),
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
            "query_started_after_challenge": "1",
            "observed_after_cutoff": "1",
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
            "broker_credential_count": "0",
            "execution_service_inactive": "1", "paper_units_inactive": "1",
            "execution_mutation_gate_closed": "1",
            "broker_transport_connected": "0",
            "broker_reconnect_permitted": "0",
            "read_only_authority": "1", "mutation_attempted": "0",
            "paper_authorized": "0", "live_authorized": "0",
            "mutation_authorized": "0", "direct_broker_access": "0",
            "order_submission_authorized": "0", "order_authorized": "0",
            "paper_only": "1", "authority_granted": "0",
            "terminal_external_halt_latch_durable": "1",
            "terminal_witness_durable": "1",
            "current_host_boundary_verified": "1",
            "terminal_evidence_file_sha256": digest("evidence-file"),
            "terminal_evidence_body_sha256": digest("evidence-body"),
        }
        # The current production contract binds the HSL8 receipt to an
        # independently produced HPE1 stable witness.  Build a deterministic
        # fixture witness first, then bind its exact file/body digests into
        # the receipt (the real root producer performs the same ordering).
        evidence_values = {
            key: receipt_values[key]
            for key in repair.EXTERNAL_TERMINAL_EVIDENCE_KEYS
            if key in receipt_values and key != "evidence_body_sha256"
        }
        evidence_values.update({
            "schema": "hepta.paper-terminal-witness-evidence.v1",
            "version": "1",
            "status": "CURRENT_POST_CUTOFF_TERMINAL_WITNESS_VERIFIED",
        })
        evidence_prefix = (
            b"HPE1\n" + b"".join(
                f"{key}={evidence_values[key]}\n".encode("ascii")
                for key in repair.EXTERNAL_TERMINAL_EVIDENCE_KEYS[:-1]))
        evidence_body_sha256 = "sha256:" + hashlib.sha256(
            evidence_prefix).hexdigest()
        evidence_raw = evidence_prefix + (
            f"evidence_body_sha256={evidence_body_sha256}\n".encode("ascii"))
        receipt_values["terminal_evidence_file_sha256"] = (
            "sha256:" + hashlib.sha256(evidence_raw).hexdigest())
        receipt_values["terminal_evidence_body_sha256"] = evidence_body_sha256
        receipt = "".join(
            f"{key}={receipt_values[key]}\n"
            for key in repair.EXTERNAL_TERMINAL_ACK_RECEIPT_KEYS)
        result: dict[str, object] = {
            "accepted": True,
            "reason_code": "PAPER_FINALIZATION_TERMINAL_ACKED",
            "lease_generation": owner["lease_generation"],
            "paper_finalization_state": "ACKED",
            "paper_finalization_required": True,
            "recovery_id": recovery_id, "finalization_id": finalization_id,
            "expected_owner_set_sha256": owner_set_sha256,
            "expected_owner_count": owner_count,
            "owner_token_sha256": owner["token_sha256"],
            "finalization_receipt_sha256": "sha256:" + hashlib.sha256(
                receipt.encode("ascii")).hexdigest(),
            "finalization_receipt": receipt,
            "preliminary_finalization_receipt_sha256": preliminary_sha256,
            "owner_audit_authoritative": True,
            "owner_audit_complete": True,
            "owner_account": account, "owner_execution_domain": domain,
            "execution_service_epoch": preliminary[
                "execution_service_epoch"],
            "execution_service_fencing_generation": preliminary[
                "execution_service_fencing_generation"],
            "broker_connection_epoch": 0, "broker_active_generation": 0,
            "broker_terminal_generation": 0, "broker_risk_generation": 0,
            "broker_account_generation": 0, "broker_position_generation": 0,
            "broker_fx_cash_generation": 0,
            "broker_exposure_generation": 0,
            "broker_terminal_exposure_generation": 0,
            "broker_risk_absorbed_exposure_generation": 0,
            "broker_global_active_order_count": 0,
            "owner_active_order_count": 0,
            "owner_uncertain_command_count": 0,
            "broker_post_fill_risk_reconciliation_pending": False,
            "broker_recovery_audit_barrier_complete": False,
            "broker_recovery_audit_new_connection_epoch_required": False,
            "broker_position_quantity": "0",
            "broker_gross_absolute_position": "0",
            "terminalization_service_epoch": preliminary[
                "execution_service_epoch"],
            "terminalization_service_fencing_generation": preliminary[
                "execution_service_fencing_generation"],
            "terminalization_generation": 1,
            "terminal_latch_sha256": receipt_values[
                "terminalizing_latch_sha256"],
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
            "terminal_proof_kind": receipt_values["terminal_proof_kind"],
            "terminal_external_halt_latch_sha256": receipt_values[
                "terminal_external_halt_latch_sha256"],
            "transport_cutoff_receipt_file_sha256": receipt_values[
                "transport_cutoff_receipt_file_sha256"],
            "transport_cutoff_receipt_body_sha256": receipt_values[
                "transport_cutoff_receipt_body_sha256"],
            "post_cutoff_terminal_witness_file_sha256": receipt_values[
                "post_cutoff_terminal_witness_file_sha256"],
            "post_cutoff_terminal_witness_body_sha256": receipt_values[
                "post_cutoff_terminal_witness_body_sha256"],
            "terminal_evidence_sha256": receipt_values[
                "terminal_evidence_file_sha256"],
            "terminal_evidence_body_sha256": receipt_values[
                "terminal_evidence_body_sha256"],
            "egress_policy_sha256": receipt_values["egress_policy_sha256"],
            "egress_publisher_pid": 4102,
            "egress_publisher_start_ticks": 99123,
            "provider_trust_policy_body_sha256": receipt_values[
                "provider_trust_policy_body_sha256"],
            "signed_account_signature_sha256": receipt_values[
                "signed_account_signature_sha256"],
            "terminal_external_latch_loaded": True,
            "terminal_current_evidence_verified": True,
        }
        if overrides:
            result.update(overrides)
        return result

    def external_hsl8_ack_fields(self) -> list[str]:
        token = "a" * 64
        token_sha256 = repair._external_hsl7_token_sha256(token)
        owner: dict[str, object] = {
            "token_sha256": token_sha256, "lease_generation": 19,
            "agent_id": "hepta-agent-alpha",
            "session_id": "external-session",
            "owner_account": "DU12345",
            "owner_execution_domain": "PAPER:alpha",
        }
        preliminary = self.external_finalization_result([owner], owner)
        terminal = self.external_terminal_ack_result(
            [owner], preliminary, replay=True, overrides={
                "execution_service_epoch": preliminary[
                    "execution_service_epoch"],
                "execution_service_fencing_generation": preliminary[
                    "execution_service_fencing_generation"],
                "terminalization_service_epoch": preliminary[
                    "execution_service_epoch"],
                "terminalization_service_fencing_generation": preliminary[
                    "execution_service_fencing_generation"],
            })

        def encode(value: object) -> str:
            return str(value).encode("utf-8").hex()

        fields = [
            "A", encode(preliminary["recovery_id"]),
            encode(preliminary["finalization_id"]),
            encode(preliminary["expected_owner_set_sha256"]), "1",
            encode(preliminary["finalization_receipt_sha256"]),
            encode(preliminary["finalization_receipt"]),
            encode(terminal["finalization_receipt_sha256"]),
            encode(terminal["finalization_receipt"]), encode(token_sha256),
            "19", encode("hepta.os.external"), encode("hepta-agent-alpha"),
            encode("external-session"), encode("DU12345"),
            encode("PAPER:alpha"),
        ]
        self.assertEqual(len(fields), 16)
        return fields

    @staticmethod
    def external_terminal_evidence(result: dict[str, object]) -> bytes:
        """Reconstruct the exact HPE1 fixture bound into a terminal result."""
        receipt, _raw = repair._external_parse_terminal_ack_receipt(
            str(result["finalization_receipt"]))
        values = {
            key: receipt[key]
            for key in repair.EXTERNAL_TERMINAL_EVIDENCE_KEYS[:-1]
        }
        values.update({
            "schema": "hepta.paper-terminal-witness-evidence.v1",
            "version": "1",
            "status": "CURRENT_POST_CUTOFF_TERMINAL_WITNESS_VERIFIED",
        })
        prefix = (
            b"HPE1\n" + b"".join(
                f"{key}={values[key]}\n".encode("ascii")
                for key in repair.EXTERNAL_TERMINAL_EVIDENCE_KEYS[:-1]))
        body = "sha256:" + hashlib.sha256(prefix).hexdigest()
        evidence = prefix + f"evidence_body_sha256={body}\n".encode("ascii")
        self_hash = "sha256:" + hashlib.sha256(evidence).hexdigest()
        if (receipt.get("terminal_evidence_file_sha256") != self_hash or
                receipt.get("terminal_evidence_body_sha256") != body):
            raise ValueError("fixture HPE1 digest binding drifted")
        return evidence

    @staticmethod
    def external_prepare_result(
            checkpoint: dict[str, object], owner: dict[str, object],
            sealed: dict[str, object], *, pending: bool = False,
    ) -> dict[str, object]:
        return {
            "accepted": not pending,
            "reason_code": (
                "PAPER_TERMINAL_WITNESS_PREPARE_INTENT_PENDING"
                if pending else "PAPER_TERMINAL_WITNESS_PREPARED"),
            "lease_generation": owner["lease_generation"],
            "paper_finalization_state": "AUDIT_SEALED",
            "paper_finalization_required": True,
            "recovery_id": checkpoint["recovery_id"],
            "finalization_id": checkpoint["finalization_id"],
            "expected_owner_set_sha256": checkpoint[
                "expected_owner_set_sha256"],
            "expected_owner_count": checkpoint["expected_owner_count"],
            "owner_token_sha256": owner["token_sha256"],
            "finalization_receipt_sha256": sealed[
                "finalization_receipt_sha256"],
        }

    def external_encrypted_store_records(
            self, plaintext: str, *, require_paper_owner: bool = False,
    ) -> tuple[list[dict[str, object]], str]:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        key = b"K" * 32
        nonce = b"N" * 12
        sealed = AESGCM(key).encrypt(
            nonce, plaintext.encode("utf-8"), repair.SUPERVISOR_LEASE_AAD)
        encoded = (
            "HSL2\n" + nonce.hex() + "\n" + sealed[-16:].hex() + "\n" +
            sealed[:-16].hex() + "\n").encode("ascii")
        metadata = self.root_metadata()
        gateway = SimpleNamespace(pw_uid=2104, pw_gid=2104)
        with mock.patch.object(
                repair.pwd, "getpwnam", return_value=gateway), \
                mock.patch.object(
                    repair, "_stable_file_bytes", side_effect=[
                        (encoded, metadata), (key, metadata)]):
            return repair._external_hsl7_records(
                require_paper_owner=require_paper_owner)

    @staticmethod
    def external_finalization_checkpoint(
            owners: list[dict[str, object]],
            *, phase: str = "RISK_ZERO_SEALED",
    ) -> dict[str, object]:
        for owner in owners:
            owner.setdefault("agent_id", "hepta-agent-alpha")
        recovery_id = "external-recovery-test-0001"
        owner_set_sha256, owner_count, _canonical, _account, _domain = (
            repair._external_finalization_owner_binding(owners))
        return {
            "recovery_id": recovery_id,
            "finalization_id": repair._external_finalization_id(
                recovery_id, owner_set_sha256, owner_count),
            "expected_owner_set_sha256": owner_set_sha256,
            "expected_owner_count": owner_count,
            "campaign_id": "campaign-test", "cycle_id": "cycle-test",
            "owners": owners, "phase": phase,
            "preliminary_owner_token_sha256s": [],
            "pending_mutation": None,
            "zero_exposure_proofs": [
                PaperRepairTests.external_zero_proof(1, 10),
                PaperRepairTests.external_zero_proof(2, 20),
            ],
            "preliminary_finalization_result": None,
            "terminal_ack_result": None,
        }

    def test_external_owner_rejects_each_fixed_path_drift(self) -> None:
        root = SimpleNamespace(
            st_mode=stat.S_IFDIR | 0o700, st_uid=0, st_gid=0)
        for field, drifted in (
                ("token_path", "/run/hepta-agent-alpha/session.token"),
                ("authority_path", str(
                    repair.SESSION_AUTHORITY_ROOT / "other.authority.json")),
                ("revoke_bearer_path", str(
                    repair.SESSION_AUTHORITY_ROOT / "other.revoke-token")),
        ):
            owner, _authority_raw, _bearer = (
                self.external_owner_material_fixture())
            owner[field] = drifted
            with self.subTest(field=field), \
                    mock.patch.object(repair.os, "lstat", return_value=root), \
                    mock.patch.object(
                        repair, "_stable_file_bytes") as stable, \
                    self.assertRaisesRegex(
                        RuntimeError,
                        "EXTERNAL_RECOVERY_SESSION_OWNER_INVALID"):
                repair._external_recovery_owner_material(owner)
            stable.assert_not_called()
        owner, _authority_raw, _bearer = self.external_owner_material_fixture()
        unsafe_root = SimpleNamespace(
            st_mode=stat.S_IFDIR | 0o750, st_uid=0, st_gid=0)
        with mock.patch.object(
                repair.os, "lstat", return_value=unsafe_root), \
                mock.patch.object(repair, "_stable_file_bytes") as stable, \
                self.assertRaisesRegex(
                    RuntimeError,
                    "EXTERNAL_RECOVERY_SESSION_OWNER_INVALID"):
            repair._external_recovery_owner_material(owner)
        stable.assert_not_called()

    def test_external_owner_files_pin_0400_and_0600_modes(self) -> None:
        owner, authority_raw, bearer = self.external_owner_material_fixture()
        root = SimpleNamespace(
            st_mode=stat.S_IFDIR | 0o700, st_uid=0, st_gid=0)
        metadata = self.root_metadata()
        with mock.patch.object(repair.os, "lstat", return_value=root), \
                mock.patch.object(
                    repair, "_stable_file_bytes", side_effect=[
                        (authority_raw, metadata), (bearer, metadata),
                    ]) as stable:
            repair._external_recovery_owner_material(owner)
        self.assertEqual(
            stable.call_args_list[0].kwargs["allowed_modes"],
            frozenset({0o600}))
        self.assertEqual(
            stable.call_args_list[1].kwargs["allowed_modes"],
            frozenset({0o600}))

        identity = SimpleNamespace(pw_uid=2104, pw_gid=2104)
        with mock.patch.object(
                repair.pwd, "getpwnam", return_value=identity), \
                mock.patch.object(
                    repair, "_stable_file_bytes",
                    return_value=(bearer, metadata)) as stable, \
                mock.patch.object(repair.os, "unlink"), \
                mock.patch.object(repair, "_fsync_parent"):
            repair._external_remove_delivery_token(owner)
        self.assertEqual(
            stable.call_args.kwargs["allowed_modes"], frozenset({0o400}))

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "owner-material"
            path.write_bytes(b"owner")
            for label, mode, allowed in (
                    ("token", 0o600, frozenset({0o400})),
                    ("authority", 0o640, frozenset({0o600})),
                    ("revoke", 0o400, frozenset({0o600})),
            ):
                path.chmod(mode)
                with self.subTest(label=label), self.assertRaisesRegex(
                        RuntimeError, "OWNER_MODE_INVALID"):
                    repair._stable_file_bytes(
                        path, "OWNER_MODE_INVALID", allowed_modes=allowed)

    def test_external_durable_owner_scope_is_bound_into_checkpoint(self) -> None:
        owner = {
            "token_sha256": "sha256:" + "a" * 64,
            "session_id": "session-owner-test", "peer_uid": 2104,
            "lease_generation": 19,
            "recovery_command_ids": ["historic-place-command"],
        }
        durable = {
            **owner, "peer_gid": 2104, "agent_id": "hepta-agent-alpha",
            "owner_account": "DU12345",
            "owner_execution_domain": "PAPER:alpha", "fence_pending": False,
            "fence_complete": False, "recovery_command_id":
                "historic-place-command",
            "paper_finalization_required": True,
            "paper_finalization_state": "NONE",
        }
        with mock.patch.object(
                repair, "_external_hsl7_records", return_value=(
                    [durable], "sha256:" + "b" * 64)):
            records, store_sha256 = (
                repair._external_validate_durable_owner_set([owner]))
        self.assertEqual(records, [durable])
        self.assertEqual(store_sha256, "sha256:" + "b" * 64)
        self.assertEqual(owner["owner_account"], "DU12345")
        self.assertEqual(owner["owner_execution_domain"], "PAPER:alpha")
        self.assertEqual(owner["agent_id"], "hepta-agent-alpha")

        local_legacy = dict(durable)
        local_legacy["paper_finalization_required"] = False
        with mock.patch.object(
                repair, "_external_hsl7_records", return_value=(
                    [local_legacy], "sha256:" + "b" * 64)), \
                self.assertRaisesRegex(
                    RuntimeError,
                    "EXTERNAL_RECOVERY_DURABLE_OWNER_SET_MISMATCH"):
            repair._external_validate_durable_owner_set([owner])

    def test_external_recovery_authority_consumes_exact_hsl8_projection(
            self) -> None:
        metadata = self.root_metadata()
        policy = {
            "campaign_id": "campaign-hsl8-test",
            "source_baseline_sha256": "sha256:" + "a" * 64,
            "watch_handoff_receipt_path": "/var/lib/hepta/handoff.json",
            "watch_handoff_receipt_file_sha256": "sha256:" + "b" * 64,
            "watch_handoff_receipt_body_sha256": "sha256:" + "c" * 64,
        }

        def documents(
                lease_store_schema: str,
        ) -> tuple[dict[str, object], bytes, list[bytes]]:
            references: dict[str, dict[str, object]] = {}
            artifact_raws: list[bytes] = []
            for index, (name, (schema, status_value)) in enumerate(
                    repair.EXTERNAL_RECOVERY_REFERENCE_SPECS.items()):
                body: dict[str, object] = {
                    "schema": schema, "status": status_value,
                }
                if name == "session_owner_set_reference":
                    body.update({
                        "lease_store_schema": lease_store_schema,
                        "lease_store_file_sha256": "sha256:" + "d" * 64,
                        "owner_count": 1,
                        "owners": [{"session_id": "owner-session"}],
                        "durable_owners": [{
                            "session_id": "owner-session",
                            "token_sha256": "sha256:" + "e" * 64,
                        }],
                        "paper_only": True, "live_authorized": False,
                    })
                elif name == "mutation_lineage_reference":
                    body.update({
                        "cycle_ids": ["cycle-hsl8-test"],
                        "executor_recovery_records": [{
                            "cycle_id": "cycle-hsl8-test"}],
                    })
                artifact = repair._sealed_json_document(body)
                artifact_raw = repair._canonical_json_bytes(artifact)
                path = Path(
                    f"/var/lib/hepta/hsl8-artifact-{index}.json")
                references[name] = repair._external_recovery_reference(
                    path, artifact, artifact_raw, metadata)
                artifact_raws.append(artifact_raw)
            authority = repair._sealed_json_document({
                "schema": repair.EXTERNAL_RECOVERY_AUTHORITY_SCHEMA,
                "version": 1,
                "status": repair.EXTERNAL_RECOVERY_AUTHORITY_STATUS,
                "domain": "alpha",
                "campaign_id": policy["campaign_id"],
                "source_baseline_sha256":
                    policy["source_baseline_sha256"],
                "watch_handoff_receipt_path":
                    policy["watch_handoff_receipt_path"],
                "watch_handoff_receipt_file_sha256":
                    policy["watch_handoff_receipt_file_sha256"],
                "watch_handoff_receipt_body_sha256":
                    policy["watch_handoff_receipt_body_sha256"],
                "recovery_required": True, "reduce_only": True,
                "paper_only": True, "live_authorized": False,
                "entry_authorized": False,
                "order_submission_authorized": False,
                "session_provision_authorized": False,
                "session_owner_count": 1,
                "all_original_session_owners_bound": True,
                "suspension_id": "suspension-hsl8-test",
                "recovery_id": "external-recovery-hsl8-test",
                **references,
            })
            return authority, repair._canonical_json_bytes(
                authority), artifact_raws

        authority, authority_raw, artifact_raws = documents("HSL8")
        with mock.patch.object(
                repair, "_stable_file_bytes", side_effect=[
                    (authority_raw, metadata),
                    *((raw, metadata) for raw in artifact_raws),
                ]):
            loaded = repair._load_external_recovery_authority(
                policy, required=True)
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded[0], authority)

        _legacy, legacy_raw, legacy_artifacts = documents("HSL7")
        with mock.patch.object(
                repair, "_stable_file_bytes", side_effect=[
                    (legacy_raw, metadata),
                    *((raw, metadata) for raw in legacy_artifacts),
                ]), self.assertRaisesRegex(
                    RuntimeError, "EXTERNAL_RECOVERY_REFERENCE_DRIFTED"):
            repair._load_external_recovery_authority(policy, required=True)

    def test_external_hsl8_token_digest_requires_canonical_newline(self) -> None:
        token = "a" * 64
        with_newline = "sha256:" + hashlib.sha256(
            token.encode("ascii") + b"\n").hexdigest()
        without_newline = "sha256:" + hashlib.sha256(
            token.encode("ascii")).hexdigest()
        self.assertEqual(
            repair._external_hsl7_token_sha256(token), with_newline)
        self.assertNotEqual(with_newline, without_newline)
        with self.assertRaisesRegex(
                RuntimeError, "EXTERNAL_RECOVERY_HSL8_STORE_INVALID"):
            repair._external_hsl7_token_sha256(token + "\n")

    def test_external_hsl8_reads_persisted_finalization_discriminator(
            self) -> None:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        key = b"K" * 32
        nonce = b"N" * 12
        token = "a" * 64

        def encode(value: str) -> str:
            return value.encode("utf-8").hex()

        fields = [
            "R", encode("paper"), encode("hepta.os.external"),
            encode(token), encode("external-agent"),
            encode("external-session"), "2104", "1800000000000", "19",
            "", "0", "0", "0", "", "0",
            encode("historic-place-command"), "1", encode("DU12345"),
            encode("PAPER:alpha"), "0", "", "", "", "0", "", "", "",
        ]
        self.assertEqual(len(fields), 27)
        plaintext = ("HSL8\n" + "\t".join(fields) + "\n").encode("utf-8")
        sealed = AESGCM(key).encrypt(
            nonce, plaintext, repair.SUPERVISOR_LEASE_AAD)
        encoded = (
            "HSL2\n" + nonce.hex() + "\n" + sealed[-16:].hex() + "\n" +
            sealed[:-16].hex() + "\n").encode("ascii")
        metadata = self.root_metadata()
        gateway = SimpleNamespace(pw_uid=2104, pw_gid=2104)
        with mock.patch.object(
                repair.pwd, "getpwnam", return_value=gateway), \
                mock.patch.object(
                    repair, "_stable_file_bytes", side_effect=[
                        (encoded, metadata), (key, metadata)]):
            records, store_sha256 = repair._external_hsl7_records()
        self.assertEqual(len(records), 1)
        self.assertIs(records[0]["paper_finalization_required"], True)
        self.assertEqual(records[0]["owner_account"], "DU12345")
        self.assertEqual(
            records[0]["token_sha256"],
            "sha256:" + hashlib.sha256(
                token.encode("ascii") + b"\n").hexdigest())
        self.assertEqual(
            store_sha256, "sha256:" + hashlib.sha256(encoded).hexdigest())

    def test_external_hsl8_reads_strict_terminal_ack_ledger(self) -> None:
        fields = self.external_hsl8_ack_fields()
        plaintext = "HSL8\n" + "\t".join(fields) + "\n"
        records, store_sha256 = self.external_encrypted_store_records(
            plaintext)
        self.assertEqual(records, [])
        self.assertRegex(store_sha256, r"^sha256:[0-9a-f]{64}$")

    def test_external_hsl8_rejects_hsl7_headers_and_legacy_ack(self) -> None:
        fields = self.external_hsl8_ack_fields()
        legacy_ack = [
            fields[0], fields[1], fields[2], fields[3], fields[4], fields[5],
            fields[6], fields[9], fields[10],
        ]
        cases = {
            "hsl7-terminal-ack": "HSL7\n" + "\t".join(fields) + "\n",
            "hsl7-legacy-ack":
                "HSL7\n" + "\t".join(legacy_ack) + "\n",
            "hsl8-legacy-ack":
                "HSL8\n" + "\t".join(legacy_ack) + "\n",
        }
        for name, plaintext in cases.items():
            with self.subTest(name=name), self.assertRaisesRegex(
                    RuntimeError, "EXTERNAL_RECOVERY_HSL8_STORE_INVALID"):
                self.external_encrypted_store_records(plaintext)

    def test_external_hsl8_rejects_malformed_terminal_ack_ledger(self) -> None:
        def encode(value: str) -> str:
            return value.encode("utf-8").hex()

        def receipt_mutation(
                fields: list[str], *, receipt_index: int, digest_index: int,
                old: str, new: str,
        ) -> None:
            receipt = bytes.fromhex(fields[receipt_index]).decode("utf-8")
            self.assertIn(old, receipt)
            receipt = receipt.replace(old, new, 1)
            fields[receipt_index] = encode(receipt)
            fields[digest_index] = encode(
                "sha256:" + hashlib.sha256(
                    receipt.encode("ascii")).hexdigest())

        def missing(fields: list[str]) -> None:
            fields.pop()

        def injected(fields: list[str]) -> None:
            fields.append(encode("injected"))

        def leading_zero_count(fields: list[str]) -> None:
            fields[4] = "01"

        def preliminary_hash(fields: list[str]) -> None:
            fields[5] = encode("sha256:" + "b" * 64)

        def terminal_hash(fields: list[str]) -> None:
            fields[7] = encode("sha256:" + "c" * 64)

        def owner_token(fields: list[str]) -> None:
            fields[9] = encode("sha256:" + "d" * 64)

        def owner_generation(fields: list[str]) -> None:
            fields[10] = "20"

        def owner_issuer(fields: list[str]) -> None:
            fields[11] = ""

        def owner_agent(fields: list[str]) -> None:
            fields[12] = encode("bad agent")

        def owner_session(fields: list[str]) -> None:
            fields[13] = encode("bad/session")

        def owner_account(fields: list[str]) -> None:
            fields[14] = encode("DU99999")

        def owner_domain(fields: list[str]) -> None:
            fields[15] = encode("LIVE:alpha")

        def preliminary_zero_order(fields: list[str]) -> None:
            receipt_mutation(
                fields, receipt_index=6, digest_index=5,
                old="broker_global_active_order_count=0",
                new="broker_global_active_order_count=1")

        def terminal_transport_reopen(fields: list[str]) -> None:
            receipt_mutation(
                fields, receipt_index=8, digest_index=7,
                old="broker_transport_connected=0",
                new="broker_transport_connected=1")

        def terminal_epoch_drift(fields: list[str]) -> None:
            receipt_mutation(
                fields, receipt_index=8, digest_index=7,
                old="execution_service_epoch=hexec-v7-final-audit",
                new="execution_service_epoch=hexec-v8-drift")

        def terminal_zero_latch(fields: list[str]) -> None:
            receipt = bytes.fromhex(fields[8]).decode("utf-8")
            match = re.search(
                r"terminalizing_latch_sha256=sha256:[0-9a-f]{64}", receipt)
            self.assertIsNotNone(match)
            assert match is not None
            receipt_mutation(
                fields, receipt_index=8, digest_index=7,
                old=match.group(0),
                new="terminalizing_latch_sha256=sha256:" + "0" * 64)

        mutations = {
            "field-removal": missing,
            "field-injection": injected,
            "noncanonical-count": leading_zero_count,
            "preliminary-hash": preliminary_hash,
            "terminal-hash": terminal_hash,
            "owner-token": owner_token,
            "owner-generation": owner_generation,
            "owner-issuer": owner_issuer,
            "owner-agent": owner_agent,
            "owner-session": owner_session,
            "owner-account": owner_account,
            "owner-domain": owner_domain,
            "preliminary-active-order": preliminary_zero_order,
            "terminal-transport-reopen": terminal_transport_reopen,
            "terminal-epoch-drift": terminal_epoch_drift,
            "terminal-zero-latch": terminal_zero_latch,
        }
        for name, mutate in mutations.items():
            fields = self.external_hsl8_ack_fields()
            mutate(fields)
            plaintext = "HSL8\n" + "\t".join(fields) + "\n"
            with self.subTest(name=name), self.assertRaisesRegex(
                    RuntimeError, "EXTERNAL_RECOVERY_HSL8_STORE_INVALID"):
                self.external_encrypted_store_records(plaintext)

        duplicate = self.external_hsl8_ack_fields()
        line = "\t".join(duplicate) + "\n"
        with self.subTest(name="duplicate-ledger"), self.assertRaisesRegex(
                RuntimeError, "EXTERNAL_RECOVERY_HSL8_STORE_INVALID"):
            self.external_encrypted_store_records("HSL8\n" + line + line)

    def test_external_recovery_query_rejects_durable_scope_substitution(
            self) -> None:
        owner = {
            name: "unused" for name in repair.EXTERNAL_CANARY_OWNER_FIELDS}
        owner.update({
            "lease_generation": 19, "token_sha256": "sha256:" + "a" * 64,
            "revoke_bearer_path": "/root/revoke-token",
            "owner_account": "DU12345",
            "owner_execution_domain": "PAPER:alpha",
        })
        result = {
            "accepted": True, "authoritative_command_status": True,
            "recovery_only": True, "owner_fenced": False,
            "paper_finalization_required": True,
            "owner_audit_authoritative": True, "owner_audit_complete": True,
            "lease_generation": 19, "command_id": "historic-place-command",
            "command_status": "rejected",
            "reason_code": "RECOVERY_QUERY_PROVEN_RECOVERY_ONLY",
            "command_reason_code": "ORDER_REJECTED", "order_id": -1,
            "execution_service_epoch": "hexec-v6-test",
            "execution_service_fencing_generation": 7,
            "recovery_expires_at_ms": 10**18,
            "owner_active_order_count": 0,
            "owner_uncertain_command_count": 0,
            "broker_connection_epoch": 3, "broker_active_generation": 4,
            "broker_terminal_generation": 5,
            "owner_account": "DU12345",
            "owner_execution_domain": "PAPER:alpha",
        }

        def completed(value: dict[str, object]) -> SimpleNamespace:
            return SimpleNamespace(returncode=0, stdout=json.dumps(value))

        with mock.patch.object(
                repair, "_external_recovery_owner_material",
                return_value=({}, b"authority", b"bearer")), \
                mock.patch.object(
                    repair, "run", return_value=completed(result)) as dispatch:
            observed = repair._external_recovery_query_owner(
                owner, "historic-place-command")
        self.assertEqual(observed["owner_account"], "DU12345")
        self.assertEqual(dispatch.call_args.args[0][-1],
                         "--require-paper-finalization")

        for field, value in (
                ("owner_account", "DU99999"),
                ("owner_execution_domain", "PAPER:beta"),
                ("paper_finalization_required", False)):
            substituted = dict(result)
            substituted[field] = value
            with self.subTest(field=field), mock.patch.object(
                    repair, "_external_recovery_owner_material",
                    return_value=({}, b"authority", b"bearer")), \
                    mock.patch.object(
                        repair, "run", return_value=completed(substituted)), \
                    self.assertRaisesRegex(
                        RuntimeError,
                        "EXTERNAL_RECOVERY_COMMAND_STATUS_INVALID"):
                repair._external_recovery_query_owner(
                    owner, "historic-place-command")

    def test_external_tool_projection_rejects_durable_scope_substitution(
            self) -> None:
        snapshot = self.owner_orders_snapshot([])
        snapshot["owner_scope"].update({
            "account": "DU12345", "execution_domain": "PAPER:alpha"})
        arguments = SimpleNamespace(
            managed_owner_session_id="session-local-paper",
            managed_owner_account="DU12345",
            managed_owner_execution_domain="PAPER:alpha")
        agent = SimpleNamespace(
            orders_snapshot=mock.Mock(return_value=snapshot))
        repair._owner_order_projection(agent, arguments, "EXTERNAL_RECOVERY")
        for field, value in (
                ("account", "DU99999"),
                ("execution_domain", "PAPER:beta")):
            drifted = dict(snapshot)
            drifted["owner_scope"] = dict(snapshot["owner_scope"])
            drifted["owner_scope"][field] = value
            agent.orders_snapshot.return_value = drifted
            with self.subTest(field=field), self.assertRaisesRegex(
                    RuntimeError,
                    "EXTERNAL_RECOVERY_ORDER_OWNER_SCOPE_MISMATCH"):
                repair._owner_order_projection(
                    agent, arguments, "EXTERNAL_RECOVERY")

    def test_external_flatten_preview_accepts_exact_fractional_lmt_day(
            self) -> None:
        agent = SimpleNamespace()
        preview = self.external_flatten_preview()
        authoritative = preview["authoritative_preview"]
        quote = {
            "bid": authoritative["quote_bid"],
            "ask": authoritative["quote_ask"],
            "subscription_id": authoritative["quote_subscription_id"],
            "observed_at_ms": authoritative["quote_observed_at_ms"],
        }
        self.assertEqual(
            repair._external_validate_flatten_preview(
                agent, preview, "planned-flatten-command",
                repair.Decimal("0.1"), 11, quote),
            ("p" * 32, repair.Decimal("0.1"), "SELL"))

        for label, mutate in (
                ("market-order", lambda value: value.update(
                    {"order_type": "MKT"})),
                ("non-exact-bid", lambda value: value.update(
                    {"limit_price": "1.105", "reference_price": "1.105"})),
                ("too-large", lambda value: value.update({
                    "position_quantity": "1.01", "quantity": "1.01"})),
                ("boolean-quantity", lambda value: value.update({
                    "position_quantity": True, "quantity": True})),
                ("json-float", lambda value: value.update({
                    "position_quantity": 0.1, "quantity": 0.1})),
                ("exponent", lambda value: value.update({
                    "position_quantity": "1e-1", "quantity": "1e-1"})),
                ("negative-zero", lambda value: value.update({
                    "position_quantity": "-0", "quantity": "0"})),
                ("nan", lambda value: value.update({
                    "position_quantity": "NaN", "quantity": "NaN"})),
                ("price-over-5000", lambda value: value.update({
                    "limit_price": "5000.0000000000001",
                    "reference_price": "5000.0000000000001",
                    "quote_bid": "5000.0000000000001",
                    "quote_ask": "5000.0000000000001"})),
        ):
            rejected = self.external_flatten_preview()
            mutate(rejected["authoritative_preview"])
            rejected_authoritative = rejected["authoritative_preview"]
            rejected_quote = {
                "bid": rejected_authoritative["quote_bid"],
                "ask": rejected_authoritative["quote_ask"],
                "subscription_id":
                    rejected_authoritative["quote_subscription_id"],
                "observed_at_ms":
                    rejected_authoritative["quote_observed_at_ms"],
            }
            position = (
                repair.Decimal("1.01") if label == "too-large" else
                repair.Decimal("1") if label in {
                    "boolean-quantity", "negative-zero"} else
                repair.Decimal("0.1"))
            with self.subTest(label=label), self.assertRaisesRegex(
                    RuntimeError,
                    "EXTERNAL_RECOVERY_FLATTEN_PREVIEW_INVALID"):
                repair._external_validate_flatten_preview(
                    agent, rejected, "planned-flatten-command", position, 11,
                    rejected_quote)

    def test_external_risk_and_position_require_decimal_strings(self) -> None:
        risk = {
            "source": "IB", "authoritative": True,
            "gross_scope": "PAPER_BASELINE_DELTA",
            "max_order_quantity": "1",
            "max_order_notional": "5000",
            "max_active_orders": 1,
            "max_gross_position": "1",
            "gross_absolute_position": "0.1",
        }
        validated = repair._external_validate_risk_snapshot(risk)
        self.assertEqual(
            validated["gross_absolute_position"], repair.Decimal("0.1"))
        for field, value in (
                ("max_order_notional", "5000.0000000000001"),
                ("gross_absolute_position", 0.1),
                ("gross_absolute_position", "1e-1"),
                ("gross_absolute_position", "-0"),
        ):
            drifted = dict(risk)
            drifted[field] = value
            with self.subTest(field=field, value=value), \
                    self.assertRaisesRegex(
                        RuntimeError,
                        "EXTERNAL_RECOVERY_RISK_BOUNDARY_INVALID"):
                repair._external_validate_risk_snapshot(drifted)

        payload = {
            "source": "IB", "authoritative": True,
            "position_generation": 11, "fx_cash_generation": 12,
            "positions": [{
                "instrument": "EUR.USD", "quantity": "0.1"}],
        }
        agent = SimpleNamespace(
            INSTRUMENT="EUR.USD", tool=mock.Mock(return_value=payload))
        contexts = {
            "session.token": (Path("/unused"), SimpleNamespace())}
        self.assertEqual(
            repair._external_position_boundary(agent, contexts),
            (repair.Decimal("0.1"), 11, 12))
        payload["positions"][0]["quantity"] = 0.1
        with self.assertRaisesRegex(
                RuntimeError, "EXTERNAL_RECOVERY_POSITION_BOUNDARY_INVALID"):
            repair._external_position_boundary(agent, contexts)

    def test_external_cancel_transport_uncertainty_never_retries_mutation(
            self) -> None:
        command_id = "planned-cancel-command"
        checkpoint: dict[str, object] = {
            "owners": [{
                "token_name": "session.token",
                "recovery_cancel_command_id": command_id,
            }],
            "pending_mutation": None,
        }
        not_found = {
            "command_status": "not_found", "order_id": -1,
            "command_reason_code": "NOT_FOUND",
            "execution_service_epoch": "hexec-v6-" + "1" * 32,
            "execution_service_fencing_generation": 7,
            "broker_connection_epoch": 3,
            "broker_active_generation": 4,
            "broker_terminal_generation": 5,
        }
        agent = SimpleNamespace(
            tool_response=mock.Mock(side_effect=TimeoutError("uncertain")))
        with mock.patch.object(
                repair, "_external_mutation_query",
                side_effect=[not_found, not_found]) as query, \
                mock.patch.object(
                    repair, "_persist_external_recovery_checkpoint") as persist, \
                self.assertRaisesRegex(
                    RuntimeError,
                    "EXTERNAL_RECOVERY_CANCEL_OUTCOME_UNRESOLVED"):
            repair._external_dispatch_cancel(
                agent, SimpleNamespace(), checkpoint, "session.token", 41, {})
        agent.tool_response.assert_called_once_with(
            mock.ANY, "trade.cancel_order", {"order_id": 41},
            command_id, timeout=16)
        self.assertEqual(query.call_count, 2)
        self.assertTrue(checkpoint["pending_mutation"]["dispatch_attempted"])
        self.assertGreaterEqual(persist.call_count, 2)

    def test_external_dispatched_not_found_checkpoint_is_never_retried(
            self) -> None:
        mutation = {
            "kind": "CANCEL_ORDER", "token_name": "session.token",
            "command_id": "planned-cancel-command", "order_id": 41,
            "dispatch_attempted": True,
        }
        checkpoint = {
            "owners": [{"token_name": "session.token"}],
            "pending_mutation": mutation,
        }
        with mock.patch.object(
                repair, "_external_mutation_query", return_value={
                    "command_status": "not_found", "order_id": -1,
                    "command_reason_code": "NOT_FOUND",
                }), self.assertRaisesRegex(
                    RuntimeError,
                    "EXTERNAL_RECOVERY_PENDING_OUTCOME_UNRESOLVED"):
            repair._external_resume_pending_before_tools(
                SimpleNamespace(), {}, checkpoint)

    def test_external_not_found_can_rearm_same_id_only_after_new_fence(
            self) -> None:
        command_id = "planned-cancel-command"
        mutation = {
            "kind": "CANCEL_ORDER", "token_name": "session.token",
            "command_id": command_id, "order_id": 41,
            "dispatch_attempted": True,
            "dispatch_execution_service_epoch": "hexec-v6-old",
            "dispatch_execution_service_fencing_generation": 6,
            "dispatch_broker_connection_epoch": 2,
            "dispatch_broker_active_generation": 3,
            "dispatch_broker_terminal_generation": 4,
        }
        checkpoint: dict[str, object] = {
            "owners": [{"token_name": "session.token"}],
            "pending_mutation": mutation,
            "terminal_order_ids": [],
        }
        status = {
            "command_status": "not_found",
            "command_reason_code": "EXECUTION_COMMAND_NOT_FOUND",
            "order_id": -1,
            "execution_service_epoch": "hexec-v6-new",
            "execution_service_fencing_generation": 7,
            "broker_connection_epoch": 8,
            "broker_active_generation": 9,
            "broker_terminal_generation": 10,
            "owner_active_order_count": 1,
            "owner_uncertain_command_count": 0,
        }
        contexts = {
            "session.token": (Path("/unused"), SimpleNamespace())}
        with mock.patch.object(
                repair, "_external_mutation_query",
                side_effect=[status, status]) as query, \
                mock.patch.object(
                    repair, "_managed_owner_order_projection",
                    return_value=({41}, {"session.token": {41}})), \
                mock.patch.object(
                    repair, "_owner_order_projection", return_value=({}, {
                        "connection_epoch": 8, "generation": 9,
                        "global_active_order_ids": (41,),
                        "owned_active_order_ids": (41,),
                    })), \
                mock.patch.object(
                    repair, "_external_position_boundary",
                    return_value=(repair.Decimal("0.1"), 11, 12)), \
                mock.patch.object(
                    repair, "_external_risk_boundary", return_value={
                        "gross_absolute_position": repair.Decimal("0.1")}), \
                mock.patch.object(
                    repair, "_persist_external_recovery_checkpoint") as persist:
            repair._external_resume_pending_before_tools(
                SimpleNamespace(), contexts, checkpoint)
        self.assertEqual(query.call_count, 2)
        self.assertEqual(checkpoint["pending_mutation"], {
            "kind": "CANCEL_ORDER", "token_name": "session.token",
            "command_id": command_id, "order_id": 41,
            "dispatch_attempted": False,
        })
        persist.assert_called_once_with(checkpoint)

    def test_external_bare_session_absence_is_never_finalization_proof(
            self) -> None:
        owner, _authority, _bearer = self.external_owner_material_fixture()
        owner.update({
            "owner_account": "DU12345",
            "owner_execution_domain": "PAPER:alpha",
            "recovery_command_ids": ["historic-place-command"],
        })
        checkpoint = self.external_finalization_checkpoint([owner])
        checkpoint["zero_exposure_proofs"] = [
            self.external_zero_proof(1, 10),
            self.external_zero_proof(3, 30),
        ]
        checkpoint["pending_mutation"] = {
            "kind": "PAPER_FINALIZE", "token_name": owner["token_name"],
            "token_sha256": owner["token_sha256"],
            "lease_generation": owner["lease_generation"],
            "recovery_id": checkpoint["recovery_id"],
            "finalization_id": checkpoint["finalization_id"],
            "expected_owner_set_sha256":
                checkpoint["expected_owner_set_sha256"],
            "expected_owner_count": 1,
        }
        completed = SimpleNamespace(returncode=1, stdout=json.dumps({
            "accepted": False, "reason_code": "SESSION_LEASE_NOT_FOUND",
            "lease_generation": 19,
        }))
        with mock.patch.object(
                repair, "_external_recovery_owner_material",
                return_value=({}, b"authority", b"bearer")), \
                mock.patch.object(repair, "run", return_value=completed), \
                mock.patch.object(
                    repair, "_persist_external_recovery_checkpoint"), \
                mock.patch.object(
                    repair, "_external_remove_delivery_token") as remove, \
                self.assertRaisesRegex(
                    RuntimeError, "EXTERNAL_RECOVERY_FINALIZATION_PENDING"):
            repair._external_finalize_all_owners(checkpoint)
        remove.assert_not_called()
        self.assertEqual(checkpoint["preliminary_owner_token_sha256s"], [])
        self.assertIsNotNone(checkpoint["pending_mutation"])

    def test_external_final_audit_precedes_composite_finalize_intent(
            self) -> None:
        owner, _authority, _bearer = self.external_owner_material_fixture()
        owner.update({
            "owner_account": "DU12345",
            "owner_execution_domain": "PAPER:alpha",
            "recovery_command_ids": ["historic-place-command"],
        })
        checkpoint = self.external_finalization_checkpoint([owner])
        events: list[str] = []
        settled = {
            "command_status": "accepted", "owner_active_order_count": 0,
            "owner_uncertain_command_count": 0,
        }
        final = self.external_zero_proof(3, 30)
        result = self.external_finalization_result([owner], owner)
        completed = SimpleNamespace(returncode=0, stdout=json.dumps(result))

        def query(*_args: object) -> dict[str, object]:
            events.append("audit")
            return settled

        def proof(*_args: object) -> dict[str, object]:
            events.append("proof")
            return final

        def finalize(*_args: object, **_kwargs: object) -> SimpleNamespace:
            events.append("finalize")
            return completed

        with mock.patch.object(
                repair, "_external_recovery_query_owner",
                side_effect=query), \
                mock.patch.object(
                    repair, "_external_zero_exposure_proof",
                    side_effect=proof), \
                mock.patch.object(
                    repair, "_persist_external_recovery_checkpoint"), \
                mock.patch.object(
                    repair, "_external_recovery_owner_material",
                    return_value=({}, b"authority", b"bearer")), \
                mock.patch.object(repair, "run", side_effect=finalize), \
                mock.patch.object(
                    repair, "_external_remove_delivery_token") as remove:
            repair._external_finalize_all_owners(
                checkpoint, agent=SimpleNamespace(), contexts={
                    "session.token": (Path("/unused"), SimpleNamespace())})
        self.assertEqual(events, ["audit", "proof", "finalize"])
        self.assertEqual(
            checkpoint["zero_exposure_proofs"][-1]["proof_index"], 3)
        self.assertEqual(checkpoint["phase"], "PRELIMINARY_SEALED")
        self.assertEqual(checkpoint["preliminary_finalization_result"], result)
        remove.assert_not_called()

    def test_external_late_fill_before_finalize_cannot_dispatch(
            self) -> None:
        owner, _authority, _bearer = self.external_owner_material_fixture()
        owner.update({
            "owner_account": "DU12345",
            "owner_execution_domain": "PAPER:alpha",
            "recovery_command_ids": ["historic-place-command"],
        })
        checkpoint = self.external_finalization_checkpoint([owner])
        with mock.patch.object(
                repair, "_external_recovery_query_owner", return_value={
                    "command_status": "accepted",
                    "owner_active_order_count": 0,
                    "owner_uncertain_command_count": 0,
                }) as query, mock.patch.object(
                    repair, "_external_zero_exposure_proof",
                    side_effect=RuntimeError(
                        "EXTERNAL_RECOVERY_ZERO_PROOF_NOT_FLAT")) as proof, \
                mock.patch.object(repair, "run") as run, \
                self.assertRaisesRegex(
                    RuntimeError, "EXTERNAL_RECOVERY_ZERO_PROOF_NOT_FLAT"):
            repair._external_finalize_all_owners(
                checkpoint, agent=SimpleNamespace(), contexts={
                    "session.token": (Path("/unused"), SimpleNamespace())})
        query.assert_called_once_with(owner, "historic-place-command")
        proof.assert_called_once()
        run.assert_not_called()
        self.assertIsNone(checkpoint["pending_mutation"])

    def test_external_finalize_exact_replay_recovers_lost_response(
            self) -> None:
        owner, _authority, _bearer = self.external_owner_material_fixture()
        owner.update({
            "owner_account": "DU12345",
            "owner_execution_domain": "PAPER:alpha",
            "recovery_command_ids": ["historic-place-command"],
        })
        checkpoint = self.external_finalization_checkpoint([owner])
        checkpoint["zero_exposure_proofs"] = [
            self.external_zero_proof(1, 10),
            self.external_zero_proof(3, 30),
        ]
        checkpoint["pending_mutation"] = {
            "kind": "PAPER_FINALIZE", "token_name": owner["token_name"],
            "token_sha256": owner["token_sha256"],
            "lease_generation": owner["lease_generation"],
            "recovery_id": checkpoint["recovery_id"],
            "finalization_id": checkpoint["finalization_id"],
            "expected_owner_set_sha256":
                checkpoint["expected_owner_set_sha256"],
            "expected_owner_count": 1,
        }
        sealed = self.external_finalization_result([owner], owner)
        responses = [
            SimpleNamespace(returncode=1, stdout="{}"),
            SimpleNamespace(returncode=0, stdout=json.dumps(sealed)),
        ]
        with mock.patch.object(
                repair, "_external_recovery_owner_material",
                return_value=({}, b"authority", b"bearer")), \
                mock.patch.object(
                    repair, "run", side_effect=responses) as dispatch, \
                mock.patch.object(
                    repair, "_persist_external_recovery_checkpoint"):
            with self.assertRaisesRegex(
                    RuntimeError, "EXTERNAL_RECOVERY_FINALIZATION_PENDING"):
                repair._external_finalize_all_owners(checkpoint)
            repair._external_finalize_all_owners(checkpoint)
        self.assertEqual(dispatch.call_count, 2)
        self.assertEqual(
            dispatch.call_args_list[0].args[0],
            dispatch.call_args_list[1].args[0])
        self.assertEqual(checkpoint["preliminary_finalization_result"], sealed)

    def test_external_fence_response_loss_replays_same_owner_binding(
            self) -> None:
        first, _authority, _bearer = self.external_owner_material_fixture()
        first.update({
            "owner_account": "DU12345",
            "owner_execution_domain": "PAPER:alpha",
            "recovery_command_ids": ["historic-place-command-a"],
        })
        second = dict(first)
        second_raw = b"b" * 64 + b"\n"
        second.update({
            "token_name": "session-b.token",
            "token_path": "/run/hepta-agent-alpha/sessions/session-b.token",
            "authority_path": str(
                repair.SESSION_AUTHORITY_ROOT /
                "session-b.token.authority.json"),
            "revoke_bearer_path": str(
                repair.SESSION_AUTHORITY_ROOT /
                "session-b.token.revoke-token"),
            "token_sha256": "sha256:" + hashlib.sha256(
                second_raw).hexdigest(),
            "revoke_bearer_sha256": "sha256:" + hashlib.sha256(
                second_raw).hexdigest(),
            "lease_generation": 20, "session_id": "session-owner-test-b",
            "recovery_command_ids": ["historic-place-command-b"],
        })
        owners = sorted([first, second], key=lambda item: str(
            item["token_sha256"]))
        current = owners[0]
        checkpoint = self.external_finalization_checkpoint(owners)
        checkpoint["zero_exposure_proofs"] = [
            self.external_zero_proof(1, 10),
            self.external_zero_proof(3, 30),
        ]
        checkpoint["pending_mutation"] = {
            "kind": "PAPER_FINALIZE", "token_name": current["token_name"],
            "token_sha256": current["token_sha256"],
            "lease_generation": current["lease_generation"],
            "recovery_id": checkpoint["recovery_id"],
            "finalization_id": checkpoint["finalization_id"],
            "expected_owner_set_sha256":
                checkpoint["expected_owner_set_sha256"],
            "expected_owner_count": 2,
        }
        pending = {
            field: (False if type(value) is bool else
                    0 if type(value) is int else "")
            for field, value in self.external_finalization_result(
                owners, current).items()}
        pending.update({
            "accepted": False,
            "reason_code": "PAPER_FINALIZATION_GROUP_PENDING",
            "lease_generation": current["lease_generation"],
            "paper_finalization_state": "FENCE_COMPLETE",
            "paper_finalization_required": True,
            "recovery_id": checkpoint["recovery_id"],
            "finalization_id": checkpoint["finalization_id"],
            "expected_owner_set_sha256":
                checkpoint["expected_owner_set_sha256"],
            "expected_owner_count": 2,
            "owner_token_sha256": current["token_sha256"],
        })
        repair._external_validate_finalization_result(
            pending, current, checkpoint, expected_state="FENCE_COMPLETE")
        for field, forged_value in (
                ("owner_audit_authoritative", True),
                ("execution_service_epoch", "forged-epoch"),
                ("broker_connection_epoch", 1),
                ("owner_account", "DU12345")):
            forged = dict(pending)
            forged[field] = forged_value
            with self.subTest(pending_rich_field=field), \
                    self.assertRaisesRegex(
                        RuntimeError,
                        "EXTERNAL_RECOVERY_FINALIZATION_RESPONSE_INVALID"):
                repair._external_validate_finalization_result(
                    forged, current, checkpoint,
                    expected_state="FENCE_COMPLETE")
        sealed = self.external_finalization_result(owners, owners[1])
        responses = [
            SimpleNamespace(returncode=1, stdout="{}"),
            SimpleNamespace(returncode=4, stdout=json.dumps(pending)),
            SimpleNamespace(returncode=0, stdout=json.dumps(sealed)),
        ]
        with mock.patch.object(
                repair, "_external_recovery_owner_material",
                return_value=({}, b"authority", b"bearer")), \
                mock.patch.object(
                    repair, "run", side_effect=responses) as dispatch, \
                mock.patch.object(
                    repair, "_persist_external_recovery_checkpoint"):
            with self.assertRaisesRegex(
                    RuntimeError, "EXTERNAL_RECOVERY_FINALIZATION_PENDING"):
                repair._external_finalize_all_owners(checkpoint)
            repair._external_finalize_all_owners(checkpoint)
        self.assertEqual(
            dispatch.call_args_list[0].args[0],
            dispatch.call_args_list[1].args[0])
        self.assertEqual(checkpoint["phase"], "PRELIMINARY_SEALED")

    def test_external_finalize_multiowner_seals_only_after_group_complete(
            self) -> None:
        first, _authority, _bearer = self.external_owner_material_fixture()
        first.update({
            "owner_account": "DU12345",
            "owner_execution_domain": "PAPER:alpha",
            "recovery_command_ids": ["historic-place-command-a"],
        })
        second = dict(first)
        second_raw = b"b" * 64 + b"\n"
        second.update({
            "token_name": "session-b.token",
            "token_path": "/run/hepta-agent-alpha/sessions/session-b.token",
            "authority_path": str(
                repair.SESSION_AUTHORITY_ROOT /
                "session-b.token.authority.json"),
            "revoke_bearer_path": str(
                repair.SESSION_AUTHORITY_ROOT /
                "session-b.token.revoke-token"),
            "token_sha256": "sha256:" + hashlib.sha256(
                second_raw).hexdigest(),
            "revoke_bearer_sha256": "sha256:" + hashlib.sha256(
                second_raw).hexdigest(),
            "lease_generation": 20, "session_id": "session-owner-test-b",
            "recovery_command_ids": ["historic-place-command-b"],
        })
        owners = sorted([first, second], key=lambda item: str(
            item["token_sha256"]))
        checkpoint = self.external_finalization_checkpoint(owners)
        checkpoint["zero_exposure_proofs"] = [
            self.external_zero_proof(1, 10),
            self.external_zero_proof(3, 30),
        ]
        current = owners[0]
        checkpoint["pending_mutation"] = {
            "kind": "PAPER_FINALIZE", "token_name": current["token_name"],
            "token_sha256": current["token_sha256"],
            "lease_generation": current["lease_generation"],
            "recovery_id": checkpoint["recovery_id"],
            "finalization_id": checkpoint["finalization_id"],
            "expected_owner_set_sha256":
                checkpoint["expected_owner_set_sha256"],
            "expected_owner_count": 2,
        }
        pending = {
            field: (False if type(value) is bool else
                    0 if type(value) is int else "")
            for field, value in self.external_finalization_result(
                owners, current).items()}
        pending.update({
            "accepted": False,
            "reason_code": "PAPER_FINALIZATION_GROUP_PENDING",
            "lease_generation": current["lease_generation"],
            "paper_finalization_state": "FENCE_COMPLETE",
            "paper_finalization_required": True,
            "recovery_id": checkpoint["recovery_id"],
            "finalization_id": checkpoint["finalization_id"],
            "expected_owner_set_sha256":
                checkpoint["expected_owner_set_sha256"],
            "expected_owner_count": 2,
            "owner_token_sha256": current["token_sha256"],
        })
        sealed = self.external_finalization_result(owners, owners[1])
        with mock.patch.object(
                repair, "_external_recovery_owner_material",
                return_value=({}, b"authority", b"bearer")), \
                mock.patch.object(repair, "run", side_effect=[
                    SimpleNamespace(returncode=4, stdout=json.dumps(pending)),
                    SimpleNamespace(returncode=0, stdout=json.dumps(sealed)),
                ]) as dispatch, \
                mock.patch.object(
                    repair, "_persist_external_recovery_checkpoint"):
            repair._external_finalize_all_owners(checkpoint)
        self.assertEqual(dispatch.call_count, 2)
        self.assertEqual(
            checkpoint["preliminary_owner_token_sha256s"],
            sorted(str(owner["token_sha256"]) for owner in owners))
        self.assertEqual(checkpoint["phase"], "PRELIMINARY_SEALED")

    def test_external_finalize_rejects_wrong_owner_set_binding(self) -> None:
        owner, _authority, _bearer = self.external_owner_material_fixture()
        owner.update({
            "owner_account": "DU12345",
            "owner_execution_domain": "PAPER:alpha",
            "recovery_command_ids": ["historic-place-command"],
        })
        checkpoint = self.external_finalization_checkpoint([owner])
        checkpoint["zero_exposure_proofs"] = [
            self.external_zero_proof(1, 10),
            self.external_zero_proof(3, 30),
        ]
        checkpoint["pending_mutation"] = {
            "kind": "PAPER_FINALIZE", "token_name": owner["token_name"],
            "token_sha256": owner["token_sha256"],
            "lease_generation": owner["lease_generation"],
            "recovery_id": checkpoint["recovery_id"],
            "finalization_id": checkpoint["finalization_id"],
            "expected_owner_set_sha256":
                checkpoint["expected_owner_set_sha256"],
            "expected_owner_count": 1,
        }
        drifted = self.external_finalization_result([owner], owner)
        drifted["expected_owner_set_sha256"] = "sha256:" + "f" * 64
        with mock.patch.object(
                repair, "_external_recovery_owner_material",
                return_value=({}, b"authority", b"bearer")), \
                mock.patch.object(repair, "run", return_value=SimpleNamespace(
                    returncode=0, stdout=json.dumps(drifted))), \
                mock.patch.object(
                    repair, "_persist_external_recovery_checkpoint"), \
                self.assertRaisesRegex(
                    RuntimeError,
                    "EXTERNAL_RECOVERY_FINALIZATION_RESPONSE_INVALID"):
            repair._external_finalize_all_owners(checkpoint)
        self.assertIsNotNone(checkpoint["pending_mutation"])

    def test_external_finalization_rejects_malformed_decimal_and_exposure(
            self) -> None:
        owner, _authority, _bearer = self.external_owner_material_fixture()
        owner.update({
            "owner_account": "DU12345",
            "owner_execution_domain": "PAPER:alpha",
            "recovery_command_ids": ["historic-place-command"],
        })
        checkpoint = self.external_finalization_checkpoint([owner])
        for label, overrides in (
                ("zero-watermarks", {
                    "broker_exposure_generation": 0,
                    "broker_terminal_exposure_generation": 0,
                    "broker_risk_absorbed_exposure_generation": 0}),
                ("negative-zero", {"broker_position_quantity": "-0"}),
                ("noncanonical-zero", {
                    "broker_gross_absolute_position": "0.0"}),
                ("unabsorbed-fill", {
                    "broker_terminal_exposure_generation": 22}),
                ("stale-risk", {
                    "broker_risk_absorbed_exposure_generation": 20}),
        ):
            result = self.external_finalization_result(
                [owner], owner, overrides=overrides)
            if label == "zero-watermarks":
                repair._external_validate_finalization_result(
                    result, owner, checkpoint, expected_state="AUDIT_SEALED")
                continue
            with self.subTest(label=label), self.assertRaisesRegex(
                    RuntimeError,
                    "EXTERNAL_RECOVERY_FINALIZATION_RESPONSE_INVALID"):
                repair._external_validate_finalization_result(
                    result, owner, checkpoint, expected_state="AUDIT_SEALED")

    def test_external_terminal_ack_is_durable_and_replayed_before_publish(
            self) -> None:
        owner, _authority, _bearer = self.external_owner_material_fixture()
        owner.update({
            "owner_account": "DU12345",
            "owner_execution_domain": "PAPER:alpha",
            "recovery_command_ids": ["historic-place-command"],
        })
        checkpoint = self.external_finalization_checkpoint(
            [owner], phase="PRELIMINARY_SEALED")
        checkpoint["preliminary_owner_token_sha256s"] = [
            owner["token_sha256"]]
        sealed = self.external_finalization_result([owner], owner)
        initial = self.external_terminal_ack_result(
            [owner], sealed, replay=False)
        replayed = self.external_terminal_ack_result(
            [owner], sealed, replay=True)
        prepared = self.external_prepare_result(checkpoint, owner, sealed)
        evidence = self.external_terminal_evidence(replayed)
        checkpoint["preliminary_finalization_result"] = sealed
        events: list[str] = []

        def persist(value: dict[str, object]) -> None:
            events.append("persist-" + str(value["phase"]))

        with mock.patch.object(
                repair, "_external_recovery_owner_material",
                return_value=({}, b"authority", b"bearer")), \
                mock.patch.object(repair, "run", side_effect=[
                    SimpleNamespace(returncode=0, stdout=json.dumps(prepared)),
                    SimpleNamespace(returncode=0, stdout=json.dumps(initial)),
                    SimpleNamespace(returncode=0, stdout=json.dumps(replayed)),
                ]) as dispatch, \
                mock.patch.object(
                    repair, "_stable_file_bytes",
                    return_value=(evidence, self.root_metadata())), \
                mock.patch.object(
                    repair, "_persist_external_recovery_checkpoint",
                    side_effect=persist), \
                mock.patch.object(
                    repair, "_external_remove_delivery_token",
                    side_effect=lambda *_a: events.append(
                        "delete-delivery")) as remove, \
                mock.patch.object(
                    repair, "_external_destroy_root_owner_material",
                    side_effect=lambda *_a, **_k: events.append(
                        "delete-root")) as root, \
                mock.patch.object(
                    repair, "_external_hsl7_records",
                    return_value=([], "sha256:" + "b" * 64)):
            repair._external_terminalize_and_ack(checkpoint)
        self.assertEqual(dispatch.call_count, 3)
        self.assertEqual(
            dispatch.call_args_list[1].args[0],
            dispatch.call_args_list[2].args[0])
        self.assertIn(
            "paper-terminal-witness-prepare",
            dispatch.call_args_list[0].args[0])
        self.assertIn("paper-terminal-witness-ack", dispatch.call_args.args[0])
        self.assertEqual(events, [
            "persist-PRELIMINARY_SEALED",
            "persist-TERMINAL_WITNESS_REQUIRED",
            "persist-TERMINAL_WITNESS_REQUIRED",
            "persist-TERMINAL_ACKED", "persist-TERMINAL_ACKED",
            "persist-TERMINAL_ACKED"])
        remove.assert_not_called()
        root.assert_not_called()
        self.assertEqual(checkpoint["phase"], "TERMINAL_ACKED")
        self.assertEqual(checkpoint["terminal_ack_result"], replayed)
        self.assertEqual(initial["finalization_receipt"],
                         replayed["finalization_receipt"])
        self.assertEqual(initial["finalization_receipt_sha256"],
                         replayed["finalization_receipt_sha256"])

    def test_external_terminal_ack_rejects_legacy_linked_v2_sessionctl_json(
            self) -> None:
        result = json.loads(REAL_TERMINAL_ACK_CLI_JSON)
        owners = [{
            "token_sha256": token_sha256,
            "lease_generation": 1,
            "owner_account": "DU12345",
            "owner_execution_domain": "PAPER:alpha",
        } for token_sha256 in (
            "sha256:44c2336fedab8ff6a85c74c2b94165377b0981f526adb9487895ca6314165e86",
            "sha256:5c6449da335badb355097ca2a7898509bce8feebfe31dedce47798825fdd9a11",
        )]
        checkpoint = {
            "recovery_id": "recovery-multiowner-1",
            "finalization_id": "finalization-multiowner-1",
            "campaign_id": "campaign-supervisor-test",
            "cycle_id": "cycle-supervisor-test",
            "owners": owners,
            "preliminary_finalization_result": {
                "finalization_receipt_sha256":
                    "sha256:6fdccdc0b1c11ced4841d54512ef2c1f57f9bd8ae7fce14e52a415ba69263b09",
            },
        }
        owner_set_sha256, owner_count, _canonical, _account, _domain = (
            repair._external_finalization_owner_binding(owners))
        self.assertEqual(owner_set_sha256,
                         result["expected_owner_set_sha256"])
        self.assertEqual(owner_count, result["expected_owner_count"])
        with self.assertRaisesRegex(
                RuntimeError,
                "EXTERNAL_RECOVERY_TERMINAL_ACK_RESPONSE_INVALID"):
            repair._external_validate_terminal_ack_result(
                result, checkpoint, expected_replay=True)

    def test_external_hsl7_preliminary_seal_cannot_use_legacy_ack(
            self) -> None:
        owner, _authority, _bearer = self.external_owner_material_fixture()
        owner.update({
            "owner_account": "DU12345",
            "owner_execution_domain": "PAPER:alpha",
            "recovery_command_ids": ["historic-place-command"],
        })
        checkpoint = self.external_finalization_checkpoint([owner])
        preliminary = self.external_finalization_result([owner], owner)
        with self.assertRaisesRegex(
                RuntimeError,
                "EXTERNAL_RECOVERY_FINALIZATION_RESPONSE_INVALID"):
            repair._external_validate_finalization_result(
                preliminary, owner, checkpoint, expected_state="ACKED")
        command_literals = {
            value for value in
            repair._external_terminalize_and_ack.__code__.co_consts
            if isinstance(value, str)
        }
        self.assertIn("paper-terminal-witness-ack", command_literals)
        self.assertIn("paper-terminal-witness-prepare", command_literals)
        self.assertNotIn("paper-terminalize-ack", command_literals)
        self.assertNotIn("paper-finalize-ack", command_literals)

    def test_external_group_ack_exact_replay_recovers_lost_response(
            self) -> None:
        owner, _authority, _bearer = self.external_owner_material_fixture()
        owner.update({
            "owner_account": "DU12345",
            "owner_execution_domain": "PAPER:alpha",
            "recovery_command_ids": ["historic-place-command"],
        })
        checkpoint = self.external_finalization_checkpoint(
            [owner], phase="PRELIMINARY_SEALED")
        checkpoint["preliminary_owner_token_sha256s"] = [
            owner["token_sha256"]]
        sealed = self.external_finalization_result([owner], owner)
        checkpoint["preliminary_finalization_result"] = sealed
        acknowledged = self.external_terminal_ack_result(
            [owner], sealed, replay=True)
        prepared = self.external_prepare_result(checkpoint, owner, sealed)
        evidence = self.external_terminal_evidence(acknowledged)
        responses = [
            SimpleNamespace(returncode=0, stdout=json.dumps(prepared)),
            SimpleNamespace(returncode=1, stdout="{}"),
            SimpleNamespace(returncode=0, stdout=json.dumps(acknowledged)),
        ]
        with mock.patch.object(
                repair, "_external_recovery_owner_material",
                return_value=({}, b"authority", b"bearer")), \
                mock.patch.object(
                    repair, "run", side_effect=responses) as dispatch, \
                mock.patch.object(
                    repair, "_stable_file_bytes",
                    return_value=(evidence, self.root_metadata())), \
                mock.patch.object(
                    repair, "_persist_external_recovery_checkpoint"), \
                mock.patch.object(
                    repair, "_external_hsl7_records",
                    return_value=([], "sha256:" + "b" * 64)):
            with self.assertRaisesRegex(
                    RuntimeError,
                    "EXTERNAL_RECOVERY_TERMINAL_ACK_PENDING"):
                repair._external_terminalize_and_ack(checkpoint)
            repair._external_terminalize_and_ack(checkpoint)
        self.assertEqual(dispatch.call_count, 3)
        self.assertEqual(
            dispatch.call_args_list[1].args[0],
            dispatch.call_args_list[2].args[0])
        self.assertIn(
            "paper-terminal-witness-prepare",
            dispatch.call_args_list[0].args[0])
        self.assertEqual(checkpoint["phase"], "TERMINAL_ACKED")

    def test_external_terminal_acked_resume_revalidates_current_runtime(
            self) -> None:
        owner, _authority, _bearer = self.external_owner_material_fixture()
        owner.update({
            "owner_account": "DU12345",
            "owner_execution_domain": "PAPER:alpha",
            "recovery_command_ids": ["historic-place-command"],
        })
        checkpoint = self.external_finalization_checkpoint(
            [owner], phase="TERMINAL_ACKED")
        checkpoint["preliminary_owner_token_sha256s"] = [
            owner["token_sha256"]]
        sealed = self.external_finalization_result([owner], owner)
        checkpoint["preliminary_finalization_result"] = sealed
        checkpoint["terminal_ack_result"] = self.external_terminal_ack_result(
            [owner], sealed, replay=False)
        replayed = self.external_terminal_ack_result(
            [owner], sealed, replay=True)
        evidence = self.external_terminal_evidence(replayed)
        with mock.patch.object(
                repair, "_external_recovery_owner_material",
                return_value=({}, b"authority", b"bearer")), \
                mock.patch.object(repair, "run", return_value=SimpleNamespace(
                    returncode=0, stdout=json.dumps(replayed))) as dispatch, \
                mock.patch.object(
                    repair, "_stable_file_bytes",
                    return_value=(evidence, self.root_metadata())), \
                mock.patch.object(
                    repair, "_external_hsl7_records",
                    return_value=([], "sha256:" + "b" * 64)), \
                mock.patch.object(
                    repair, "_persist_external_recovery_checkpoint"):
            repair._external_terminalize_and_ack(checkpoint)
        dispatch.assert_called_once()
        self.assertEqual(checkpoint["terminal_ack_result"], replayed)

    def test_external_terminal_ack_rejects_every_new_witness_drift(
            self) -> None:
        owner, _authority, _bearer = self.external_owner_material_fixture()
        owner.update({
            "owner_account": "DU12345",
            "owner_execution_domain": "PAPER:alpha",
            "recovery_command_ids": ["historic-place-command"],
        })
        checkpoint = self.external_finalization_checkpoint(
            [owner], phase="TERMINAL_ACKED")
        checkpoint["preliminary_owner_token_sha256s"] = [
            owner["token_sha256"]]
        preliminary = self.external_finalization_result([owner], owner)
        checkpoint["preliminary_finalization_result"] = preliminary
        base = self.external_terminal_ack_result(
            [owner], preliminary, replay=True)
        checkpoint["terminal_ack_result"] = base
        drifts: tuple[tuple[str, object], ...] = (
            ("preliminary_finalization_receipt_sha256",
             "sha256:" + "8" * 64),
            ("terminalization_service_epoch", "reopened-service"),
            ("terminalization_service_fencing_generation", 23),
            ("terminalization_generation", 2),
            ("terminal_latch_sha256", "sha256:" + "8" * 64),
            ("execution_mutation_gate_closed", False),
            ("broker_transport_connected", True),
            ("broker_event_ingress_halted", False),
            ("broker_callback_queue_drained", True),
            ("broker_callbacks_in_flight", 1),
            ("broker_reconnect_permitted", True),
            ("terminal_latch_durable", False),
            ("terminal_runtime_latch_loaded", True),
            ("terminal_runtime_verified", True),
            ("terminal_replay", False),
        )
        for field, drifted in drifts:
            value = dict(base)
            value[field] = drifted
            with self.subTest(field=field), self.assertRaisesRegex(
                    RuntimeError,
                    "EXTERNAL_RECOVERY_TERMINAL_ACK_RESPONSE_INVALID"):
                repair._external_validate_terminal_ack_result(
                    value, checkpoint, expected_replay=True)

    def test_external_terminal_ack_rejects_each_receipt_key_tamper(
            self) -> None:
        owner, _authority, _bearer = self.external_owner_material_fixture()
        owner.update({
            "owner_account": "DU12345",
            "owner_execution_domain": "PAPER:alpha",
            "recovery_command_ids": ["historic-place-command"],
        })
        checkpoint = self.external_finalization_checkpoint(
            [owner], phase="TERMINAL_ACKED")
        checkpoint["preliminary_owner_token_sha256s"] = [
            owner["token_sha256"]]
        preliminary = self.external_finalization_result([owner], owner)
        checkpoint["preliminary_finalization_result"] = preliminary
        base = self.external_terminal_ack_result(
            [owner], preliminary, replay=True)
        checkpoint["terminal_ack_result"] = base
        evidence = self.external_terminal_evidence(base)
        receipt, _raw = repair._external_parse_terminal_ack_receipt(
            base["finalization_receipt"])
        for field in repair.EXTERNAL_TERMINAL_ACK_RECEIPT_KEYS:
            tampered = dict(receipt)
            tampered[field] = (
                "sha256:" + "8" * 64 if field.endswith("sha256") else
                "999" if tampered[field].isdigit() else "TAMPERED")
            tampered_raw = "".join(
                f"{key}={tampered[key]}\n"
                for key in repair.EXTERNAL_TERMINAL_ACK_RECEIPT_KEYS)
            value = dict(base)
            value["finalization_receipt"] = tampered_raw
            value["finalization_receipt_sha256"] = (
                "sha256:" + hashlib.sha256(
                    tampered_raw.encode("ascii")).hexdigest())
            with self.subTest(receipt_field=field), self.assertRaisesRegex(
                    RuntimeError,
                    "EXTERNAL_RECOVERY_TERMINAL_ACK_RESPONSE_INVALID"):
                repair._external_validate_terminal_ack_result(
                    value, checkpoint, expected_replay=True,
                    terminal_evidence_raw=evidence)

    def test_external_terminal_ack_rejects_late_fill_and_runtime_reopen(
            self) -> None:
        owner, _authority, _bearer = self.external_owner_material_fixture()
        owner.update({
            "owner_account": "DU12345",
            "owner_execution_domain": "PAPER:alpha",
            "recovery_command_ids": ["historic-place-command"],
        })
        checkpoint = self.external_finalization_checkpoint(
            [owner], phase="TERMINAL_ACKED")
        checkpoint["preliminary_owner_token_sha256s"] = [
            owner["token_sha256"]]
        preliminary = self.external_finalization_result([owner], owner)
        checkpoint["preliminary_finalization_result"] = preliminary
        for label, overrides in (
                ("late-fill", {
                    "broker_terminal_exposure_generation": 32}),
                ("post-fill-pending", {
                    "broker_post_fill_risk_reconciliation_pending": True}),
                ("transport-reopened", {
                    "broker_transport_connected": True}),
                ("reconnect-reenabled", {
                    "broker_reconnect_permitted": True}),
                ("latch-corrupt", {
                    "terminal_latch_sha256": "not-a-digest"}),
        ):
            result = self.external_terminal_ack_result(
                [owner], preliminary, replay=True, overrides=overrides)
            with self.subTest(label=label), self.assertRaisesRegex(
                    RuntimeError,
                    "EXTERNAL_RECOVERY_TERMINAL_ACK_RESPONSE_INVALID"):
                repair._external_validate_terminal_ack_result(
                    result, checkpoint, expected_replay=True)

    def test_external_terminal_ack_is_atomic_for_multiowner_group(
            self) -> None:
        first, _authority, _bearer = self.external_owner_material_fixture()
        first.update({
            "owner_account": "DU12345",
            "owner_execution_domain": "PAPER:alpha",
            "recovery_command_ids": ["historic-place-command-a"],
        })
        second = dict(first)
        second_raw = b"b" * 64 + b"\n"
        second.update({
            "token_name": "session-b.token",
            "token_path": "/run/hepta-agent-alpha/sessions/session-b.token",
            "authority_path": str(
                repair.SESSION_AUTHORITY_ROOT /
                "session-b.token.authority.json"),
            "revoke_bearer_path": str(
                repair.SESSION_AUTHORITY_ROOT /
                "session-b.token.revoke-token"),
            "token_sha256": "sha256:" + hashlib.sha256(
                second_raw).hexdigest(),
            "revoke_bearer_sha256": "sha256:" + hashlib.sha256(
                second_raw).hexdigest(),
            "lease_generation": 20, "session_id": "session-owner-test-b",
            "recovery_command_ids": ["historic-place-command-b"],
        })
        owners = sorted([first, second], key=lambda item: str(
            item["token_sha256"]))
        checkpoint = self.external_finalization_checkpoint(
            owners, phase="PRELIMINARY_SEALED")
        checkpoint["preliminary_owner_token_sha256s"] = sorted(
            str(owner["token_sha256"]) for owner in owners)
        preliminary = self.external_finalization_result(
            owners, owners[-1])
        checkpoint["preliminary_finalization_result"] = preliminary
        initial = self.external_terminal_ack_result(
            owners, preliminary, replay=False)
        replayed = self.external_terminal_ack_result(
            owners, preliminary, replay=True)
        prepared = self.external_prepare_result(
            checkpoint, owners[0], preliminary)
        evidence = self.external_terminal_evidence(replayed)
        with mock.patch.object(
                repair, "_external_recovery_owner_material",
                return_value=({}, b"authority", b"bearer")), \
                mock.patch.object(repair, "run", side_effect=[
                    SimpleNamespace(returncode=0, stdout=json.dumps(prepared)),
                    SimpleNamespace(returncode=0, stdout=json.dumps(initial)),
                    SimpleNamespace(returncode=0, stdout=json.dumps(replayed)),
                ]) as dispatch, \
                mock.patch.object(
                    repair, "_stable_file_bytes",
                    return_value=(evidence, self.root_metadata())), \
                mock.patch.object(
                    repair, "_persist_external_recovery_checkpoint"), \
                mock.patch.object(
                    repair, "_external_hsl7_records",
                    return_value=([], "sha256:" + "b" * 64)):
            repair._external_terminalize_and_ack(checkpoint)
        self.assertEqual(dispatch.call_count, 3)
        arguments = dispatch.call_args_list[0].args[0]
        self.assertIn("paper-terminal-witness-prepare", arguments)
        self.assertEqual(arguments[arguments.index(
            "--expected-owner-count") + 1], "2")
        deterministic = min(
            owners, key=lambda item: str(item["token_sha256"]))
        self.assertEqual(arguments[arguments.index("--token-file") + 1],
                         deterministic["revoke_bearer_path"])
        self.assertEqual(checkpoint["terminal_ack_result"], replayed)

    def test_external_completion_v3_binds_only_replayed_terminal_ack(
            self) -> None:
        owner, _authority_raw, _bearer = self.external_owner_material_fixture()
        owner.update({
            "owner_account": "DU12345",
            "owner_execution_domain": "PAPER:alpha",
            "recovery_command_ids": ["historic-place-command"],
        })
        checkpoint = self.external_finalization_checkpoint(
            [owner], phase="TERMINAL_ACKED")
        checkpoint.update({
            "updated_at_ms": 200,
            "preliminary_owner_token_sha256s": [owner["token_sha256"]],
        })
        preliminary = self.external_finalization_result([owner], owner)
        checkpoint["preliminary_finalization_result"] = preliminary
        checkpoint["terminal_ack_result"] = self.external_terminal_ack_result(
            [owner], preliminary, replay=True)
        evidence = self.external_terminal_evidence(
            checkpoint["terminal_ack_result"])
        authority = {
            "recorded_at_ms": 100,
            "recovery_id": checkpoint["recovery_id"],
            "campaign_id": "campaign-a", "suspension_id": "suspension-a",
            "source_baseline_sha256": "sha256:" + "a" * 64,
        }
        published: list[tuple[Path, dict[str, object]]] = []

        def publish(
                path: Path, body: dict[str, object], _failure: str,
        ) -> tuple[dict[str, object], bytes, SimpleNamespace]:
            document = repair._sealed_json_document(body)
            published.append((path, document))
            return (document, repair._canonical_json_bytes(document),
                    self.root_metadata())

        with mock.patch.object(
                repair, "_publish_immutable_sealed_json",
                side_effect=publish), mock.patch.object(
                    repair, "_stable_file_bytes",
                    return_value=(evidence, self.root_metadata())), \
                mock.patch.object(
                    repair, "_external_recovery_control_pins",
                    return_value=(
                        "sha256:" + "b" * 64, "sha256:" + "c" * 64)):
            terminal, _terminal_raw, completion, _completion_raw = (
                repair._external_publish_completion(
                    authority, b"authority", checkpoint))
        self.assertEqual(len(published), 2)
        self.assertEqual(terminal["schema"],
                         repair.EXTERNAL_RECOVERY_TERMINAL_FLAT_SCHEMA)
        self.assertEqual(terminal["version"], 4)
        self.assertEqual(completion["schema"],
                         repair.EXTERNAL_RECOVERY_COMPLETION_SCHEMA)
        self.assertEqual(completion["version"], 4)
        self.assertEqual(
            terminal["terminal_ack_receipt_sha256"],
            checkpoint["terminal_ack_result"]["finalization_receipt_sha256"])
        self.assertTrue(terminal["terminal_current_evidence_replay_verified"])
        self.assertTrue(completion[
            "terminal_current_evidence_replay_verified"])
        self.assertNotIn("finalization_ack", terminal)
        self.assertNotIn("finalization_receipt_sha256", completion)

        unverified = dict(checkpoint)
        unverified["terminal_ack_result"] = self.external_terminal_ack_result(
            [owner], preliminary, replay=False)
        with self.assertRaisesRegex(
                RuntimeError,
                "EXTERNAL_RECOVERY_TERMINAL_ACK_RESPONSE_INVALID"):
            with mock.patch.object(
                    repair, "_stable_file_bytes",
                    return_value=(evidence, self.root_metadata())):
                repair._external_publish_completion(
                    authority, b"authority", unverified)

    def test_external_completion_v3_loads_exact_time_path_and_flat_schema(
            self) -> None:
        owner, _authority_raw, _bearer = self.external_owner_material_fixture()
        owner.update({
            "owner_account": "DU12345",
            "owner_execution_domain": "PAPER:alpha",
            "recovery_command_ids": ["historic-place-command"],
        })
        checkpoint = self.external_finalization_checkpoint(
            [owner], phase="TERMINAL_ACKED")
        checkpoint.update({
            "updated_at_ms": 200,
            "preliminary_owner_token_sha256s": [owner["token_sha256"]],
        })
        preliminary = self.external_finalization_result([owner], owner)
        checkpoint["preliminary_finalization_result"] = preliminary
        checkpoint["terminal_ack_result"] = self.external_terminal_ack_result(
            [owner], preliminary, replay=True)
        evidence = self.external_terminal_evidence(
            checkpoint["terminal_ack_result"])
        authority = {
            "recorded_at_ms": 100,
            "recovery_id": checkpoint["recovery_id"],
            "campaign_id": "campaign-a", "suspension_id": "suspension-a",
            "source_baseline_sha256": "sha256:" + "a" * 64,
            "session_owner_count": 1,
        }
        published: list[tuple[Path, dict[str, object], bytes]] = []
        metadata = self.root_metadata()

        def publish(
                path: Path, body: dict[str, object], _failure: str,
        ) -> tuple[dict[str, object], bytes, SimpleNamespace]:
            document = repair._sealed_json_document(body)
            raw = repair._canonical_json_bytes(document)
            published.append((path, document, raw))
            return document, raw, metadata

        control_pins = ("sha256:" + "b" * 64, "sha256:" + "c" * 64)
        with mock.patch.object(
                repair, "_publish_immutable_sealed_json",
                side_effect=publish), mock.patch.object(
                    repair, "_stable_file_bytes",
                    return_value=(evidence, metadata)), mock.patch.object(
                    repair, "_external_recovery_control_pins",
                    return_value=control_pins):
            terminal, terminal_raw, completion, completion_raw = (
                repair._external_publish_completion(
                    authority, b"authority", checkpoint))
        terminal_path = published[0][0]

        def reseal(value: dict[str, object]) -> dict[str, object]:
            body = dict(value)
            body.pop("body_sha256", None)
            return repair._sealed_json_document(body)

        def load(
                completion_document: dict[str, object],
                terminal_document: dict[str, object],
        ) -> tuple[dict[str, object], bytes] | None:
            completion_bytes = repair._canonical_json_bytes(
                completion_document)
            terminal_bytes = repair._canonical_json_bytes(terminal_document)
            with mock.patch.object(
                    repair, "_stable_file_bytes", side_effect=[
                        (completion_bytes, metadata),
                        (evidence, metadata),
                        (terminal_bytes, metadata),
                    ]), mock.patch.object(
                        repair, "_external_existing_checkpoint_owners",
                        return_value=[owner]), mock.patch.object(
                            repair, "_external_recovery_checkpoint",
                            return_value=checkpoint), mock.patch.object(
                                repair, "_external_recovery_control_pins",
                                return_value=control_pins):
                return repair._load_external_recovery_completion(
                    authority, b"authority")

        self.assertEqual(load(completion, terminal),
                         (completion, completion_raw))

        stale_time = dict(completion)
        stale_time["completed_at_ms"] = 99
        stale_time = reseal(stale_time)
        with self.assertRaisesRegex(
                RuntimeError, "EXTERNAL_RECOVERY_COMPLETION_INVALID"):
            load(stale_time, terminal)

        wrong_path = dict(completion)
        wrong_reference = dict(
            wrong_path["authoritative_flat_receipt_reference"])
        wrong_reference["path"] = str(terminal_path.with_name("other.json"))
        wrong_path["authoritative_flat_receipt_reference"] = wrong_reference
        wrong_path = reseal(wrong_path)
        with self.assertRaisesRegex(
                RuntimeError, "EXTERNAL_RECOVERY_COMPLETION_INVALID"):
            load(wrong_path, terminal)

        for version in (1, 2):
            legacy_terminal = dict(terminal)
            legacy_terminal["schema"] = (
                "hepta.local-ai-paper-external-recovery-terminal-flat.v" +
                str(version))
            legacy_terminal["version"] = version
            legacy_terminal = reseal(legacy_terminal)
            legacy_raw = repair._canonical_json_bytes(legacy_terminal)
            legacy_completion = dict(completion)
            legacy_completion["authoritative_flat_receipt_reference"] = (
                repair._external_recovery_reference(
                    terminal_path, legacy_terminal, legacy_raw, metadata))
            legacy_completion = reseal(legacy_completion)
            with self.subTest(terminal_version=version), \
                    self.assertRaisesRegex(
                        RuntimeError,
                        "EXTERNAL_RECOVERY_TERMINAL_FLAT_INVALID"):
                load(legacy_completion, legacy_terminal)

    def test_external_owner_material_never_deletes_before_v3_completion(
            self) -> None:
        owner, _authority_raw, _bearer = self.external_owner_material_fixture()
        owner.update({
            "owner_account": "DU12345",
            "owner_execution_domain": "PAPER:alpha",
            "recovery_command_ids": ["historic-place-command"],
        })
        checkpoint = self.external_finalization_checkpoint(
            [owner], phase="TERMINAL_ACKED")
        checkpoint["preliminary_owner_token_sha256s"] = [
            owner["token_sha256"]]
        preliminary = self.external_finalization_result([owner], owner)
        checkpoint["preliminary_finalization_result"] = preliminary
        terminal_ack = self.external_terminal_ack_result(
            [owner], preliminary, replay=True)
        checkpoint["terminal_ack_result"] = terminal_ack
        with mock.patch.object(
                repair, "_stable_file_bytes",
                side_effect=FileNotFoundError()), mock.patch.object(
                    repair, "_external_remove_delivery_token") as delivery, \
                mock.patch.object(
                    repair, "_external_destroy_root_owner_material") as root, \
                self.assertRaisesRegex(
                    RuntimeError, "EXTERNAL_RECOVERY_COMPLETION_MISSING"):
            repair._external_cleanup_terminal_owner_material(checkpoint)
        delivery.assert_not_called()
        root.assert_not_called()

        completion = repair._sealed_json_document({
            "schema": repair.EXTERNAL_RECOVERY_COMPLETION_SCHEMA,
            "version": 4,
            "status": repair.EXTERNAL_RECOVERY_COMPLETION_STATUS,
            "recovery_id": checkpoint["recovery_id"],
            "finalization_id": checkpoint["finalization_id"],
            "terminal_ack_receipt_sha256": terminal_ack[
                "finalization_receipt_sha256"],
            "terminal_evidence_file_sha256": terminal_ack[
                "terminal_evidence_sha256"],
            "terminal_current_evidence_replay_verified": True,
        })
        evidence = self.external_terminal_evidence(terminal_ack)
        with mock.patch.object(
                repair, "_stable_file_bytes", side_effect=[
                    (repair._canonical_json_bytes(completion),
                     self.root_metadata()),
                    (evidence, self.root_metadata()),
                ]), mock.patch.object(
                        repair, "_external_hsl7_records",
                        return_value=([], "sha256:" + "b" * 64)), \
                mock.patch.object(
                    repair, "_external_remove_delivery_token") as delivery, \
                mock.patch.object(
                    repair, "_external_destroy_root_owner_material") as root:
            repair._external_cleanup_terminal_owner_material(checkpoint)
        delivery.assert_called_once_with(owner)
        root.assert_called_once_with(owner, allow_absent=True)

    def test_external_legacy_completion_versions_fail_closed(self) -> None:
        owner, _authority_raw, _bearer = self.external_owner_material_fixture()
        owner.update({
            "owner_account": "DU12345",
            "owner_execution_domain": "PAPER:alpha",
            "recovery_command_ids": ["historic-place-command"],
        })
        checkpoint = self.external_finalization_checkpoint(
            [owner], phase="TERMINAL_ACKED")
        checkpoint["preliminary_owner_token_sha256s"] = [
            owner["token_sha256"]]
        preliminary = self.external_finalization_result([owner], owner)
        checkpoint["preliminary_finalization_result"] = preliminary
        checkpoint["terminal_ack_result"] = self.external_terminal_ack_result(
            [owner], preliminary, replay=True)
        evidence = self.external_terminal_evidence(
            checkpoint["terminal_ack_result"])
        authority = {
            "recorded_at_ms": 100,
            "recovery_id": "external-recovery-test-0001",
            "campaign_id": "campaign-a", "suspension_id": "suspension-a",
            "source_baseline_sha256": "sha256:" + "a" * 64,
            "session_owner_count": 1,
        }
        for version in (1, 2):
            completion = repair._sealed_json_document({
                "schema": (
                    "hepta.local-paper-control-recovery-completion.v" +
                    str(version)),
                "version": version,
                "status": repair.EXTERNAL_RECOVERY_COMPLETION_STATUS,
                "recovery_id": authority["recovery_id"],
            })
            with self.subTest(version=version), mock.patch.object(
                    repair, "_stable_file_bytes", return_value=(
                        repair._canonical_json_bytes(completion),
                        self.root_metadata())), mock.patch.object(
                    repair, "_external_existing_checkpoint_owners",
                    return_value=[owner]), \
                    mock.patch.object(
                        repair, "_external_recovery_checkpoint",
                        return_value=checkpoint), mock.patch.object(
                            repair, "_external_recovery_control_pins",
                            return_value=(
                                "sha256:" + "b" * 64,
                                "sha256:" + "c" * 64)), \
                    self.assertRaisesRegex(
                    RuntimeError, "EXTERNAL_RECOVERY_COMPLETION_INVALID"):
                repair._load_external_recovery_completion(
                    authority, b"authority")

    def test_external_zero_proof_rejects_order_appearing_on_second_projection(
            self) -> None:
        contexts = {
            "session.token": (Path("/unused"), SimpleNamespace())}
        with mock.patch.object(
                repair, "_managed_owner_order_projection",
                return_value=(set(), {"session.token": set()})), \
                mock.patch.object(
                    repair, "_owner_order_projection", return_value=({}, {
                        "connection_epoch": 8, "generation": 9,
                        "global_active_order_ids": (41,),
                        "owned_active_order_ids": (41,),
                    })), \
                mock.patch.object(
                    repair, "_external_position_boundary") as position, \
                mock.patch.object(
                    repair, "_external_risk_boundary") as risk, \
                self.assertRaisesRegex(
                    RuntimeError,
                    "EXTERNAL_RECOVERY_ZERO_PROOF_ACTIVE_ORDERS"):
            repair._external_zero_exposure_proof(
                SimpleNamespace(), contexts, 3)
        position.assert_not_called()
        risk.assert_not_called()

    def test_external_final_zero_proof_requires_fresh_generations(self) -> None:
        first = self.external_zero_proof(1, 10)
        final = self.external_zero_proof(3, 30)
        self.assertTrue(repair._external_final_zero_proof_is_sealed({
            "zero_exposure_proofs": [first, final]}))
        for field in (
                "position_generation", "fx_cash_generation",
                "orders_generation"):
            stale = dict(final)
            stale[field] = first[field]
            stale = repair._sealed_json_document({
                key: value for key, value in stale.items()
                if key != "body_sha256"})
            with self.subTest(field=field):
                self.assertFalse(
                    repair._external_final_zero_proof_is_sealed({
                        "zero_exposure_proofs": [first, stale]}))

    def test_external_final_zero_proof_rejects_epoch_drift_and_legacy_pair(
            self) -> None:
        first = self.external_zero_proof(1, 10)
        final = self.external_zero_proof(3, 30)
        drifted = dict(final)
        drifted["orders_connection_epoch"] = 8
        drifted = repair._sealed_json_document({
            key: value for key, value in drifted.items()
            if key != "body_sha256"})
        self.assertFalse(repair._external_final_zero_proof_is_sealed({
            "zero_exposure_proofs": [first, drifted]}))
        self.assertFalse(repair._external_final_zero_proof_is_sealed({
            "zero_exposure_proofs": [
                first, self.external_zero_proof(2, 20)]}))

    def test_external_historic_uncertainty_is_rejected_before_zero_proofs(
            self) -> None:
        owner = {
            "token_name": "session.token",
            "recovery_command_ids": ["historic-place-command"],
        }
        checkpoint = {"owners": [owner]}
        with mock.patch.object(
                repair, "_external_recovery_query_owner", return_value={
                    "command_status": "uncertain",
                    "owner_active_order_count": 0,
                    "owner_uncertain_command_count": 1,
                }), mock.patch.object(
                    repair, "_external_zero_exposure_proof") as proof, \
                self.assertRaisesRegex(
                    RuntimeError,
                    "EXTERNAL_RECOVERY_OWNER_NOT_TERMINALLY_AUDITED"):
            repair._external_require_historic_commands_settled(checkpoint)
        proof.assert_not_called()

    def test_external_finalize_resume_rejects_missing_diagnostic_proof(
            self) -> None:
        owner, _authority, _bearer = self.external_owner_material_fixture()
        owner.update({
            "owner_account": "DU12345",
            "owner_execution_domain": "PAPER:alpha",
            "recovery_command_ids": ["historic-place-command"],
        })
        checkpoint = self.external_finalization_checkpoint([owner])
        checkpoint["zero_exposure_proofs"] = []
        checkpoint["pending_mutation"] = {
            "kind": "PAPER_FINALIZE", "token_name": owner["token_name"],
            "token_sha256": owner["token_sha256"],
            "lease_generation": owner["lease_generation"],
            "recovery_id": checkpoint["recovery_id"],
            "finalization_id": checkpoint["finalization_id"],
            "expected_owner_set_sha256":
                checkpoint["expected_owner_set_sha256"],
            "expected_owner_count": 1,
        }
        with mock.patch.object(repair, "run") as run, \
                self.assertRaisesRegex(
                    RuntimeError,
                    "EXTERNAL_RECOVERY_FINAL_ZERO_PROOF_REQUIRED"):
            repair._external_finalize_all_owners(checkpoint)
        run.assert_not_called()

    def test_external_v1_and_v2_checkpoints_are_not_v3_evidence(
            self) -> None:
        owner, _authority_raw, _bearer = self.external_owner_material_fixture()
        owner.update({
            "owner_account": "DU12345",
            "owner_execution_domain": "PAPER:alpha",
            "recovery_command_ids": [
                "historic-place-command", "planned-cancel-command",
                "planned-flatten-command"],
            "recovery_cancel_command_id": "planned-cancel-command",
            "recovery_flatten_command_id": "planned-flatten-command",
        })
        authority = {
            "recovery_id": "external-recovery-test-0001",
            "campaign_id": "campaign-a",
            "suspension_id": "suspension-a",
            "source_baseline_sha256": "sha256:" + "a" * 64,
            "body_sha256": "sha256:" + "b" * 64,
        }
        for version in (1, 2):
            legacy = repair._sealed_json_document({
                "schema": (
                    "hepta.local-ai-paper-external-recovery-checkpoint.v" +
                    str(version)),
                "version": version,
                "recovery_id": authority["recovery_id"],
                "campaign_id": authority["campaign_id"],
                "suspension_id": authority["suspension_id"],
                "source_baseline_sha256": authority[
                    "source_baseline_sha256"],
                "phase": "FINALIZATION_ACKED", "owners": [owner],
                "zero_exposure_proofs": [
                    self.external_zero_proof(1, 10),
                    self.external_zero_proof(3, 30)],
                "finalized_owner_token_sha256s": [owner["token_sha256"]],
            })
            with self.subTest(version=version), mock.patch.object(
                    repair, "_stable_file_bytes", return_value=(
                        repair._canonical_json_bytes(legacy),
                        self.root_metadata())), self.assertRaisesRegex(
                    RuntimeError, "EXTERNAL_RECOVERY_CHECKPOINT_INVALID"):
                repair._external_recovery_checkpoint(
                    authority, b"authority-v3", [owner])

    def test_external_risk_dispatch_skips_legacy_recovery_path(self) -> None:
        external = mock.Mock()
        legacy = mock.Mock()
        with mock.patch.object(
                repair, "_external_policy_for_dispatch",
                return_value={"admission_mode": "external-p1-finalized"}), \
                mock.patch.object(
                    repair, "_external_risk_recover_locked", external), \
                mock.patch.object(repair, "run_checked", legacy):
            repair._risk_recover_locked(safety_exit=True, automatic=True)
        external.assert_called_once_with(safety_exit=True, automatic=True)
        legacy.assert_not_called()

    def test_v5_local_market_policy_is_accepted_by_start_boundary(self) -> None:
        now_ms = 1_000_000
        policy = {
            "schema": repair.ACTIVE_POLICY_SCHEMA,
            "version": 5,
            "campaign_id": "campaign-test",
            "domain_id": "alpha",
            "enabled": True,
            "mutations_authorized": True,
            "paper_only": True,
            "live_authorized": False,
            "strategy_id": "strategy-test",
            "strategy_version": "3",
            "strategy_sha256": "sha256:" + "1" * 64,
            "valid_after_ms": now_ms - 1,
            "expires_at_ms": now_ms + 86_399_999,
            "allowed_instruments": ["EUR.USD"],
            "max_cycles": 720,
            "max_quantity": 25_000,
            "max_active_orders": 1,
            "order_type": "MKT",
            "tif": "DAY",
            "end_flat_required": True,
            "source_baseline_sha256": "sha256:" + "2" * 64,
            "admission_mode": "local-only",
            "deployment_evidence_file_sha256": "sha256:" + "3" * 64,
            "deployment_evidence_body_sha256": "sha256:" + "4" * 64,
            "deployment_install_transaction_id": "install-round106",
        }
        env = {
            "HEPTA_LOCAL_AI_CAMPAIGN_ID": "campaign-test",
            "HEPTA_LOCAL_AI_STRATEGY_ID": "strategy-test",
            "HEPTA_LOCAL_AI_STRATEGY_VERSION": "3",
            "HEPTA_LOCAL_AI_STRATEGY_SHA256": "sha256:" + "1" * 64,
        }
        with tempfile.TemporaryDirectory() as directory:
            policy_path = Path(directory) / "alpha.json"
            policy_path.write_text(json.dumps(policy), encoding="ascii")
            policy_path.chmod(0o600)
            real_lstat = repair.os.lstat

            def root_lstat(path: object) -> SimpleNamespace:
                metadata = real_lstat(path)
                return SimpleNamespace(
                    st_mode=metadata.st_mode, st_nlink=metadata.st_nlink,
                    st_uid=0, st_gid=0, st_size=metadata.st_size)

            with mock.patch.object(repair, "CAMPAIGN_POLICY", policy_path), \
                    mock.patch.object(
                        repair.os, "lstat", side_effect=root_lstat), \
                    mock.patch.object(repair, "read_env", return_value=env), \
                    mock.patch.object(
                        repair.time, "time_ns",
                        return_value=now_ms * 1_000_000), \
                    mock.patch.object(
                        repair, "_verify_deadline_timer") as deadline:
                values, validated = ORIGINAL_VALIDATED_PREPARED_CAMPAIGN()
                self.assertEqual(values, env)
                self.assertEqual(validated, policy)
                deadline.assert_called_once_with(policy["expires_at_ms"])
                for field, invalid in (
                        ("admission_mode", "external-p1-finalized"),
                        ("order_type", "LMT")):
                    rejected = dict(policy)
                    rejected[field] = invalid
                    policy_path.write_text(
                        json.dumps(rejected), encoding="ascii")
                    with self.subTest(field=field), self.assertRaisesRegex(
                            RuntimeError,
                            "CAMPAIGN_START_POLICY_BOUNDARY_INVALID"):
                        ORIGINAL_VALIDATED_PREPARED_CAMPAIGN()

    @staticmethod
    def auth_canary_events(
            session_id: str, profile_id: str,
    ) -> list[dict[str, object]]:
        session_key = (
            "agent:telegram-bot-8681289317:explicit:" + session_id)
        run_id = "87654321-4321-6789-4321-678987654321"
        timestamps = (
            "1970-01-01T00:00:00.001Z",
            "1970-01-01T00:00:00.002Z",
            "1970-01-01T00:00:00.003Z",
            "1970-01-01T00:00:00.004Z",
            "1970-01-01T00:00:00.005Z",
        )
        event_data: tuple[tuple[str, dict[str, object]], ...] = (
            ("session.started", {"authProfileId": profile_id}),
            ("context.compiled", {
                "prompt": repair.AUTH_CANARY_PROMPT, "imagesCount": 0}),
            ("prompt.submitted", {
                "prompt": repair.AUTH_CANARY_PROMPT, "imagesCount": 0}),
            ("model.completed", {
                "timedOut": False, "aborted": False, "promptError": None,
                "assistantTexts": ["AUTH_OK"]}),
            ("session.ended", {
                "status": "success", "timedOut": False,
                "promptError": None}),
        )
        return [
            {
                "traceSchema": "openclaw-trajectory", "schemaVersion": 1,
                "traceId": session_id, "source": "runtime",
                "type": event_type, "ts": timestamps[index],
                "seq": index + 1, "sourceSeq": index + 1,
                "sessionId": session_id, "sessionKey": session_key,
                "runId": run_id, "provider": "codex",
                "modelId": "gpt-5.3-codex-spark",
                "modelApi": "openai-chatgpt-responses", "data": data,
            }
            for index, (event_type, data) in enumerate(event_data)
        ]

    @staticmethod
    def incident_state() -> dict[str, object]:
        return {
            "schema": FakeAgent.SCHEMA,
            "recovery_required": True,
            "trading_suspended": True,
            "recovery_complete": True,
            "recovery_phase": "FLAT_CONFIRMED",
            "suspension_id": "suspension-auth-rearm-test",
            "suspension_code": "MODEL_AUTH_RATE_LIMIT",
            "suspended_at_ms": 1,
            "auth_generation_at_suspend": "auth-generation-old",
            "campaign_id_at_suspend": "old-campaign",
            "recovery_receipt_sha256": "sha256:" + "1" * 64,
            "pending_order_id": None,
        }

    @staticmethod
    def renew_admission_state() -> dict[str, object]:
        return {
            "schema": FakeAgent.SCHEMA,
            "recovery_required": False,
            "trading_suspended": False,
            "pending_order_id": None,
            "incident_pending_order_id": None,
            "pending_mutation_state_unproven": False,
            "pending_mutation_kind": None,
            "pending_mutation_command_id": None,
            "pending_mutation_recorded_at_ms": None,
            "pending_mutation_token_name": None,
            "pending_mutation_token_sha256": None,
        }

    @staticmethod
    def owner_orders_snapshot(
            active: list[int], owned: list[int] | None = None, *,
            recent: list[dict[str, object]] | None = None,
            session_id: str = "session-local-paper", generation: int = 1,
    ) -> dict[str, object]:
        return {
            "source": "IB", "authoritative": True,
            "active_orders_source": "IB_OPEN_ORDERS",
            "active_orders_connection_epoch": 7,
            "active_orders_generation": generation,
            "global_active_orders_complete": True,
            "owner_projection_source":
                "EXECUTION_COORDINATOR_ORDER_OWNERS",
            "owner_projection_connection_epoch": 7,
            "owner_projection_generation": generation,
            "owner_projection_complete": True,
            "owned_active_order_ids_authoritative": True,
            "owner_scope": {
                "agent_id": "telegram-bot-8681289317",
                "session_id": session_id,
                "execution_domain": "alpha", "account": "DU12345",
            },
            "reason_code": "", "active_order_ids": list(active),
            "owned_active_order_ids": list(
                active if owned is None else owned),
            "unmapped_active_order_ids": [],
            "recent_orders": [] if recent is None else recent,
        }

    @staticmethod
    def risk_recovery_checkpoint_fixture(
            *, phase: str = "RISK_ZERO_SEALED",
    ) -> dict[str, object]:
        value: dict[str, object] = {
            "schema":
                "hepta.local-ai-paper-risk-recovery-checkpoint.v1",
            "campaign_id": "old-campaign",
            "campaign_id_at_suspend": "old-campaign",
            "suspension_id": "suspension-auth-rearm-test",
            "suspension_code": "MODEL_AUTH_RATE_LIMIT",
            "suspended_at_ms": 1,
            "auth_generation_at_suspend": "auth-generation-old",
            "phase": phase,
            "halt_result": "halt_confirmed",
            "incident_pending_order_id": 41,
            "retained_original_session": True,
            "selected_token_name": "local-paper.token",
            "cancel_attempted_order_ids": [41],
            "terminally_reconciled_order_ids": [41],
            "command_reconciliation": [],
            "last_flatten_order_id": 42,
            "position": 0,
            "active_orders": 0,
            "gross_absolute_position": 0,
            "first_position_generation": 10,
            "first_fx_cash_generation": 20,
            "second_position_generation": 11,
            "second_fx_cash_generation": 21,
            "campaign_policy_sha256": "sha256:" + "9" * 64,
            "recovery_raw_price_pnl_evidence": None,
            "sessions": [{
                "token_name": "local-paper.token",
                "token_sha256": "sha256:" + "7" * 64,
                "lease_generation": 9,
                "revoked": phase == "SESSIONS_REVOKED",
            }],
            "paper_only": True,
            "live_authorized": False,
            "updated_at_ms": 2,
        }
        if phase == "SESSIONS_REVOKED":
            value["completed_at_ms"] = 3
        return value

    @staticmethod
    def durable_active_session_fixture(
            root: Path, *, expires_at_ms: int, generation: int = 7,
    ) -> tuple[Path, Path, Path, bytes]:
        token = root / "sessions" / "local-paper.token"
        token.parent.mkdir(parents=True)
        authority_root = root / "authority"
        authority_root.mkdir()
        raw = ("e" * 64 + "\n").encode("ascii")
        token_sha256 = "sha256:" + hashlib.sha256(raw).hexdigest()
        bearer = authority_root / (token.name + ".revoke-token")
        bearer.write_bytes(raw)
        bearer.chmod(0o600)
        observed_at_ms = expires_at_ms - 86_400_000
        record = authority_root / (token.name + ".authority.json")
        record.write_text(json.dumps({
            "schema": "hepta.local-paper-session-provision-intent.v1",
            "phase": "ACTIVE", "token_name": token.name,
            "authority_bearer_name": bearer.name,
            "token_sha256": token_sha256,
            "expected_lease_generation": 1,
            "lease_generation": generation,
            "session_name": "paper-reboot-owner",
            "session_id": "paper-reboot-owner-session",
            "peer_uid": 0, "ttl_seconds": 86_400,
            "created_at_ms": observed_at_ms,
            "accepted_at_ms": observed_at_ms,
            "expires_at_ms": expires_at_ms,
            "paper_only": True, "live_authorized": False,
        }) + "\n", encoding="ascii")
        record.chmod(0o600)
        return token, authority_root, record, raw

    def test_recovery_raw_price_pnl_requires_exact_authoritative_fills(
            self) -> None:
        records = [{
            "order_id": 95, "status": "Filled", "terminal": True,
            "economic_fill": True, "filled_quantity": 25_000,
            "remaining_quantity": 0, "average_fill_price": 1.152010,
            "observed_at_ms": 1000,
            "evidence_service_epoch": "hexec-v6-entry",
            "evidence_connection_epoch": 1,
            "broker_execution_id": "execution-entry-95",
            "broker_execution_ambiguous": False,
            "broker_execution_quantity": 25_000,
            "broker_execution_price": 1.152010,
            "account": "DU12345", "execution_domain": "alpha",
            "instrument": "EUR.USD", "side": "SLD",
        }, {
            "order_id": 96, "status": "Filled", "terminal": True,
            "economic_fill": True, "filled_quantity": 25_000,
            "remaining_quantity": 0, "average_fill_price": 1.152050,
            "observed_at_ms": 2000,
            "evidence_service_epoch": "hexec-v6-close",
            "evidence_connection_epoch": 2,
            "broker_execution_id": "execution-close-96",
            "broker_execution_ambiguous": False,
            "broker_execution_quantity": 25_000,
            "broker_execution_price": 1.152050,
            "account": "DU12345", "execution_domain": "alpha",
            "instrument": "EUR.USD", "side": "BOT",
        }]
        agent = SimpleNamespace(
            INSTRUMENT="EUR.USD", ORDER_QUANTITY=25_000,
            _quantity_equal=lambda left, right: abs(
                float(left) - float(right)) <= 1e-6,
            orders_snapshot=mock.Mock(return_value={
                "source": "IB", "authoritative": True,
                "active_order_ids": [], "recent_orders": records,
                "owner_scope": {"account": "DU12345",
                                "execution_domain": "alpha"},
            }))
        main_state = {
            "entry_order_id": 95, "entry_quantity": -25_000.0,
            "entry_at_ms": 999, "incident_pending_order_id": None,
        }
        recovery_state = {"last_flatten_order_id": 96}
        evidence = repair._risk_recovery_raw_price_pnl_evidence(
            agent, object(), main_state, recovery_state, -25_000.0, True)
        assert evidence is not None
        self.assertEqual(evidence["amount"], -1.0)
        self.assertEqual(evidence["quote_currency"], "USD")
        self.assertFalse(evidence["commission_included"])
        self.assertEqual(evidence["entry_fill"]["order_id"], 95)
        self.assertEqual(evidence["recovery_close_fill"]["order_id"], 96)

        records[1]["terminal"] = False
        self.assertIsNone(repair._risk_recovery_raw_price_pnl_evidence(
            agent, object(), main_state, recovery_state, -25_000.0, True))
        records[1]["terminal"] = True
        self.assertIsNone(repair._risk_recovery_raw_price_pnl_evidence(
            agent, object(), main_state, recovery_state, -25_000.0, False))

    def test_public_provision_session_cli_is_not_exposed(self) -> None:
        with mock.patch.object(
                repair.sys, "argv",
                [str(SOURCE), "provision-session"]), \
                mock.patch.object(repair, "provision_session") as provision, \
                self.assertRaises(SystemExit) as raised:
            repair.main()
        self.assertEqual(raised.exception.code, 2)
        provision.assert_not_called()

    def test_generic_reset_cannot_clear_latched_incident(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            state_path.write_text("{}", encoding="ascii")
            agent = mock.Mock()
            agent.load_state.return_value = self.incident_state()
            with mock.patch.object(repair, "AGENT_STATE", state_path), \
                    mock.patch.object(repair, "load_agent", return_value=agent), \
                    self.assertRaisesRegex(
                        RuntimeError, "LATCH_REQUIRES_AUTH_REARM"):
                repair.reset_main_state()

    def test_state_snapshot_is_independent_and_never_links_main_state(
            self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path = root / "state.json"
            backup = root / "state.backup.json"
            state_path.write_bytes(b'{"latched":true}\n')
            state_path.chmod(0o600)
            before = os.stat(state_path, follow_symlinks=False)
            with mock.patch.object(repair.os, "fchown"), \
                    mock.patch.object(
                        repair.os, "fstat", side_effect=self.root_fstat):
                repair._copy_root_state_snapshot(
                    state_path, backup, self.root_metadata())
            after = os.stat(state_path, follow_symlinks=False)
            archived = os.stat(backup, follow_symlinks=False)
            self.assertEqual(state_path.read_bytes(), backup.read_bytes())
            self.assertEqual(before.st_ino, after.st_ino)
            self.assertEqual(after.st_nlink, 1)
            self.assertEqual(archived.st_nlink, 1)
            self.assertNotEqual(after.st_ino, archived.st_ino)

    def test_state_snapshot_atomic_restore_after_failed_replacement(
            self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path = root / "state.json"
            backup = root / "state.backup.json"
            original = b'{"recovery_required":true}\n'
            state_path.write_bytes(original)
            state_path.chmod(0o600)
            with mock.patch.object(repair.os, "fchown"), \
                    mock.patch.object(
                        repair.os, "fstat", side_effect=self.root_fstat):
                repair._copy_root_state_snapshot(
                    state_path, backup, self.root_metadata())
            replacement = root / "state.failed-write.json"
            replacement.write_bytes(b'{"recovery_required":false}\n')
            replacement.replace(state_path)
            with mock.patch.object(
                    repair.os, "lstat", return_value=self.root_metadata()), \
                    mock.patch.object(
                        repair.os, "fstat", side_effect=self.root_fstat), \
                    mock.patch.object(repair.os, "chown"), \
                    mock.patch.object(repair.os, "chmod"):
                repair._restore_root_state_snapshot(
                    backup, state_path, 0o600)
            self.assertEqual(state_path.read_bytes(), original)
            self.assertEqual(
                os.stat(state_path, follow_symlinks=False).st_nlink, 1)
            self.assertFalse(backup.exists())

    def test_state_snapshot_rejects_oversize_source_without_linking_it(
            self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path = root / "state.json"
            backup = root / "state.backup.json"
            state_path.write_bytes(
                b"x" * (repair.AGENT_STATE_SNAPSHOT_MAX_BYTES + 1))
            state_path.chmod(0o600)
            with self.assertRaisesRegex(
                    RuntimeError, "SNAPSHOT_SOURCE_UNSAFE"):
                repair._copy_root_state_snapshot(
                    state_path, backup, self.root_metadata())
            self.assertEqual(
                os.stat(state_path, follow_symlinks=False).st_nlink, 1)
            self.assertFalse(backup.exists())

    def test_flat_reset_preserves_existing_auth_rearm_binding(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            state_path.write_text("{}", encoding="ascii")
            state_path.chmod(0o600)
            current_state = {
                "recovery_required": False,
                "trading_suspended": False,
                "pending_order_id": None,
                "auth_generation_rearmed": "auth-generation-current",
                "auth_profile_sha256_rearmed": "sha256:" + "2" * 64,
                "auth_profile_allowlist_sha256_rearmed":
                    "sha256:" + "4" * 64,
                "auth_rearm_receipt_sha256": "sha256:" + "3" * 64,
            }
            agent = FakeAgent()
            agent.load_state = mock.Mock(return_value=current_state)
            agent.tool = mock.Mock(
                return_value={"gross_absolute_position": 0.0})
            with mock.patch.object(repair, "AGENT_STATE", state_path), \
                    mock.patch.object(
                        repair, "load_agent", return_value=agent), \
                    mock.patch.object(
                        repair, "agent_arguments", return_value=object()), \
                    mock.patch.object(
                        repair, "authoritative_state",
                        return_value=(0.0, 2, 2)), \
                    mock.patch.object(
                        repair.os, "lstat", return_value=self.root_metadata()), \
                    mock.patch.object(
                        repair.os, "fstat", side_effect=self.root_fstat), \
                    mock.patch.object(repair.os, "fchown"), \
                    mock.patch.object(repair.os, "chown"), \
                    mock.patch.object(repair.os, "chmod"):
                repair.reset_main_state()
            reset = json.loads(state_path.read_text(encoding="ascii"))
        self.assertEqual(
            reset["auth_generation_rearmed"], "auth-generation-current")
        self.assertEqual(
            reset["auth_profile_sha256_rearmed"], "sha256:" + "2" * 64)
        self.assertEqual(
            reset["auth_profile_allowlist_sha256_rearmed"],
            "sha256:" + "4" * 64)
        self.assertEqual(
            reset["auth_rearm_receipt_sha256"], "sha256:" + "3" * 64)

    def test_prepare_campaign_updates_generation_and_profile_binding(
            self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env_path = Path(directory) / "agent.env"
            env_path.write_text(
                "HEPTA_LOCAL_AI_CAMPAIGN_ID=old-campaign\n"
                "HEPTA_LOCAL_AI_STRATEGY_SHA256=sha256:old\n"
                "HEPTA_LOCAL_AI_AUTH_GENERATION=old-generation\n"
                "HEPTA_LOCAL_AI_AUTH_PROFILE_ID=openai:old-profile\n"
                "HEPTA_LOCAL_AI_AUTH_PROFILE_ALLOWLIST_SHA256="
                "sha256:" + "4" * 64 + "\n",
                encoding="ascii")
            written: list[bytes] = []
            with mock.patch.object(prepare, "AGENT_ENV_PATH", env_path), \
                    mock.patch.object(
                        prepare, "atomic_write",
                        side_effect=lambda _path, payload: written.append(payload)):
                prepare.update_agent_env(
                    "new-campaign", "sha256:new", "new-generation",
                    "openai:new-profile")
        self.assertEqual(len(written), 1)
        rendered = written[0].decode("ascii")
        self.assertIn("HEPTA_LOCAL_AI_CAMPAIGN_ID=new-campaign\n", rendered)
        self.assertIn(
            "HEPTA_LOCAL_AI_STRATEGY_ID="
            "hepta-local-ai-paper-strategy-v3\n", rendered)
        self.assertIn("HEPTA_LOCAL_AI_STRATEGY_VERSION=3\n", rendered)
        self.assertIn(
            "HEPTA_LOCAL_AI_STRATEGY_SHA256=sha256:new\n", rendered)
        self.assertIn("HEPTA_LOCAL_AI_AUTH_GENERATION=new-generation\n", rendered)
        self.assertIn(
            "HEPTA_LOCAL_AI_AUTH_PROFILE_ID=openai:new-profile\n", rendered)
        self.assertIn(
            "HEPTA_LOCAL_AI_AUTH_PROFILE_ALLOWLIST_SHA256=sha256:" +
            "4" * 64 + "\n", rendered)

    def test_rearm_publishes_allowlist_digest_to_root_env(self) -> None:
        digest = "sha256:" + "a" * 64
        with tempfile.TemporaryDirectory() as directory:
            env_path = Path(directory) / "agent.env"
            env_path.write_text(
                "HEPTA_LOCAL_AI_AUTH_GENERATION=auth-generation-new\n",
                encoding="ascii")
            with mock.patch.object(repair, "AGENT_ENV", env_path), \
                    mock.patch.object(
                        repair.os, "lstat", return_value=self.root_metadata()), \
                    mock.patch.object(repair.os, "fchown"):
                repair.set_auth_profile_allowlist_sha256(digest)
            rendered = env_path.read_text(encoding="ascii")
        self.assertIn(
            "HEPTA_LOCAL_AI_AUTH_PROFILE_ALLOWLIST_SHA256=" + digest + "\n",
            rendered)

    def test_rearm_rejects_agent_env_drift_after_allowlist_publish(self) -> None:
        digest = "sha256:" + "a" * 64
        expected = {
            "HEPTA_LOCAL_AI_AUTH_GENERATION": "auth-generation-new",
            "HEPTA_LOCAL_AI_AUTH_PROFILE_ID": "openai:test-profile",
        }
        drifted = dict(expected)
        drifted["HEPTA_LOCAL_AI_AUTH_PROFILE_ID"] = "openai:other-profile"
        drifted[repair.AUTH_PROFILE_ALLOWLIST_ENV] = digest
        with mock.patch.object(
                repair, "set_auth_profile_allowlist_sha256") as publish, \
                mock.patch.object(
                    repair, "read_env", return_value=drifted), \
                self.assertRaisesRegex(RuntimeError, "AGENT_ENV_DRIFTED"):
            repair._publish_auth_profile_allowlist_sha256(expected, digest)
        publish.assert_called_once_with(digest)

    def test_safety_exit_missing_state_creates_latched_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path = root / "state.json"
            sentinel = root / "safety-stop.pending.json"
            sentinel.write_text("sentinel\n", encoding="ascii")
            with mock.patch.object(repair, "AGENT_STATE", state_path), \
                    mock.patch.object(repair, "SAFETY_LATCH", sentinel), \
                    mock.patch.object(repair, "read_env", return_value={
                        "HEPTA_LOCAL_AI_AUTH_GENERATION": "auth-generation-old",
                        "HEPTA_LOCAL_AI_AUTH_PROFILE_ID": "openai:test-profile",
                        "HEPTA_LOCAL_AI_CAMPAIGN_ID": "old-campaign",
                    }), \
                    mock.patch.object(repair.os, "chown"), \
                    mock.patch.object(repair.os, "chmod"):
                state = repair._load_root_agent_state(
                    FakeAgent(), allow_safety_exit=True)

            persisted = json.loads(state_path.read_text(encoding="ascii"))
            sentinel_content = sentinel.read_text(encoding="ascii")

        self.assertTrue(state["recovery_required"])
        self.assertTrue(state["trading_suspended"])
        self.assertEqual(state["suspension_code"], "ORDER_STATE_UNCERTAIN")
        self.assertEqual(state["recovery_phase"], "REQUESTED")
        self.assertFalse(state["recovery_complete"])
        self.assertEqual(
            state["auth_generation_at_suspend"], "auth-generation-old")
        self.assertEqual(state["campaign_id_at_suspend"], "old-campaign")
        self.assertEqual(persisted, state)
        self.assertEqual(sentinel_content, "sentinel\n")

    def test_missing_state_without_safety_exit_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(
                    repair, "AGENT_STATE", Path(directory) / "state.json"), \
                self.assertRaisesRegex(
                    RuntimeError, "RISK_RECOVERY_STATE_MISSING"):
            repair._load_root_agent_state(
                FakeAgent(), allow_safety_exit=False)

    def test_safety_exit_archives_unreadable_state_and_reconstructs_latch(
            self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path = root / "state.json"
            state_path.write_text("{not-json\n", encoding="ascii")
            state_path.chmod(0o600)
            sentinel = root / "safety-stop.pending.json"
            sentinel.write_text("sentinel\n", encoding="ascii")
            agent = FakeAgent()
            agent.load_state = lambda path: json.loads(
                Path(path).read_text(encoding="ascii"))
            main_link_counts: list[int] = []

            def observed_write(path: Path, value: object) -> None:
                main_link_counts.append(
                    os.stat(state_path, follow_symlinks=False).st_nlink)
                FakeAgent.write_json(path, value)

            agent.write_json = observed_write
            with mock.patch.object(repair, "AGENT_STATE", state_path), \
                    mock.patch.object(repair, "SAFETY_LATCH", sentinel), \
                    mock.patch.object(repair, "read_env", return_value={
                        "HEPTA_LOCAL_AI_AUTH_GENERATION": "auth-generation-old",
                        "HEPTA_LOCAL_AI_AUTH_PROFILE_ID": "openai:test-profile",
                        "HEPTA_LOCAL_AI_CAMPAIGN_ID": "old-campaign",
                    }), \
                    mock.patch.object(
                        repair.os, "lstat", return_value=self.root_metadata()), \
                    mock.patch.object(
                        repair.os, "fstat", side_effect=self.root_fstat), \
                    mock.patch.object(repair.os, "fchown"), \
                    mock.patch.object(repair.os, "chown"), \
                    mock.patch.object(repair.os, "chmod"):
                state = repair._load_root_agent_state(
                    agent, allow_safety_exit=True)

            archives = list(root.glob(
                "state.unreadable-safety-exit-*.json"))
            persisted = json.loads(state_path.read_text(encoding="ascii"))
            sentinel_content = sentinel.read_text(encoding="ascii")
            archive_content = (
                archives[0].read_text(encoding="ascii")
                if len(archives) == 1 else "")
            main_metadata = os.stat(state_path, follow_symlinks=False)
            archive_metadata = (
                os.stat(archives[0], follow_symlinks=False)
                if len(archives) == 1 else None)

        self.assertEqual(len(archives), 1)
        self.assertEqual(archive_content, "{not-json\n")
        self.assertEqual(main_link_counts, [1])
        self.assertEqual(main_metadata.st_nlink, 1)
        assert archive_metadata is not None
        self.assertEqual(archive_metadata.st_nlink, 1)
        self.assertNotEqual(main_metadata.st_ino, archive_metadata.st_ino)
        self.assertEqual(persisted, state)
        self.assertTrue(state["recovery_required"])
        self.assertTrue(state["trading_suspended"])
        self.assertEqual(state["recovery_phase"], "REQUESTED")
        self.assertIn("original archived", state["recovery_reason"])
        self.assertEqual(sentinel_content, "sentinel\n")

    def test_auth_rearm_rejects_same_generation(self) -> None:
        state = self.incident_state()
        with mock.patch.object(
                repair, "run",
                return_value=SimpleNamespace(returncode=3, stdout="", stderr="")), \
                mock.patch.object(repair, "load_agent", return_value=FakeAgent()), \
                mock.patch.object(
                    repair, "_load_root_agent_state", return_value=state), \
                mock.patch.object(repair, "read_env", return_value={
                    "HEPTA_LOCAL_AI_AUTH_GENERATION": "auth-generation-old",
                    "HEPTA_LOCAL_AI_AUTH_PROFILE_ID": "openai:test-profile",
                    "HEPTA_LOCAL_AI_CAMPAIGN_ID": "new-campaign",
                }), \
                self.assertRaisesRegex(RuntimeError, "GENERATION_NOT_CHANGED"):
            repair.auth_rearm(
                "openai:new-profile@example.com", "auth-generation-old")

    def test_auth_rearm_rejects_same_campaign(self) -> None:
        state = self.incident_state()
        with mock.patch.object(
                repair, "run",
                return_value=SimpleNamespace(returncode=3, stdout="", stderr="")), \
                mock.patch.object(repair, "load_agent", return_value=FakeAgent()), \
                mock.patch.object(
                    repair, "_load_root_agent_state", return_value=state), \
                mock.patch.object(repair, "read_env", return_value={
                    "HEPTA_LOCAL_AI_AUTH_GENERATION": "auth-generation-new",
                    "HEPTA_LOCAL_AI_AUTH_PROFILE_ID": "openai:test-profile",
                    "HEPTA_LOCAL_AI_CAMPAIGN_ID": "old-campaign",
                }), \
                self.assertRaisesRegex(RuntimeError, "NEW_CAMPAIGN_REQUIRED"):
            repair.auth_rearm(
                "openai:new-profile@example.com", "auth-generation-new")

    def test_auth_rearm_requires_valid_recovery_receipt(self) -> None:
        state = self.incident_state()
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.object(
                    repair, "END_FLAT_RECEIPT_ROOT", Path(directory)), \
                    self.assertRaises(FileNotFoundError):
                repair._verified_risk_recovery_receipt(state)

    def test_auth_rearm_rejects_profile_not_bound_in_root_env(self) -> None:
        state = self.incident_state()
        with mock.patch.object(
                repair, "run",
                return_value=SimpleNamespace(returncode=3, stdout="", stderr="")), \
                mock.patch.object(repair, "load_agent", return_value=FakeAgent()), \
                mock.patch.object(
                    repair, "_load_root_agent_state", return_value=state), \
                mock.patch.object(repair, "read_env", return_value={
                    "HEPTA_LOCAL_AI_AUTH_GENERATION": "auth-generation-new",
                    "HEPTA_LOCAL_AI_AUTH_PROFILE_ID": "openai:other-profile",
                    "HEPTA_LOCAL_AI_CAMPAIGN_ID": "new-campaign",
                }), \
                mock.patch.object(repair, "_probe_auth_profile") as probe, \
                self.assertRaisesRegex(
                    RuntimeError, "PROFILE_NOT_CONFIGURED"):
            repair.auth_rearm(
                "openai:new-profile@example.com", "auth-generation-new")
        probe.assert_not_called()

    def test_production_canary_uses_base_model_and_verifies_trajectory(
            self) -> None:
        profile_id = "openai:test-profile"
        session_id = "12345678-1234-5678-1234-567812345678"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            trajectory = root / (session_id + ".trajectory.jsonl")
            events = self.auth_canary_events(session_id, profile_id)
            trajectory.write_text(
                "\n".join(json.dumps(item) for item in events) + "\n",
                encoding="utf-8")
            trajectory.chmod(0o600)
            completed = SimpleNamespace(returncode=0, stdout="{}", stderr="")
            with mock.patch.object(repair, "OPENCLAW_SESSION_ROOT", root), \
                    mock.patch.object(
                        repair.uuid, "uuid4", return_value=uuid.UUID(session_id)), \
                    mock.patch.object(repair.time, "time_ns", return_value=0), \
                    mock.patch.object(
                        repair, "run", return_value=completed) as run:
                (_, returned_session_id,
                 returned_profile_sha256) = repair._production_auth_canary(
                    ("openai:other-profile", profile_id))
        command = run.call_args.args[0]
        self.assertEqual(returned_session_id, session_id)
        self.assertEqual(
            returned_profile_sha256,
            "sha256:" + hashlib.sha256(profile_id.encode()).hexdigest())
        self.assertEqual(
            command[command.index("--model") + 1],
            "codex/gpt-5.3-codex-spark")
        self.assertEqual(
            command[command.index("--message") + 1],
            "AUTH_CANARY: Do not call any tools. Reply with exactly AUTH_OK and nothing else.")

    def test_profile_probe_uses_campaign_model_and_exact_profile(self) -> None:
        profile_id = "openai:test-profile"
        session_id = "12345678-1234-5678-1234-567812345678"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            trajectory = root / (session_id + ".trajectory.jsonl")
            events = self.auth_canary_events(session_id, profile_id)
            trajectory.write_text(
                "\n".join(json.dumps(item) for item in events) + "\n",
                encoding="utf-8")
            trajectory.chmod(0o600)
            completed = SimpleNamespace(returncode=0, stdout="{}", stderr="")
            with mock.patch.object(repair, "OPENCLAW_SESSION_ROOT", root), \
                    mock.patch.object(
                        repair.uuid, "uuid4", return_value=uuid.UUID(session_id)), \
                    mock.patch.object(repair.time, "time_ns", return_value=0), \
                    mock.patch.object(
                        repair, "run", return_value=completed) as run:
                finished_at_ms, model = repair._probe_auth_profile(profile_id)
        command = run.call_args.args[0]
        self.assertEqual(finished_at_ms, 5)
        self.assertEqual(model, "codex/gpt-5.3-codex-spark")
        self.assertEqual(
            command[command.index("--model") + 1],
            "codex/gpt-5.3-codex-spark")

    def test_profile_probe_rejects_failover_to_other_profile(self) -> None:
        profile_id = "openai:test-profile"
        session_id = "12345678-1234-5678-1234-567812345678"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            trajectory = root / (session_id + ".trajectory.jsonl")
            events = self.auth_canary_events(
                session_id, "openai:other-profile")
            trajectory.write_text(
                "\n".join(json.dumps(item) for item in events) + "\n",
                encoding="utf-8")
            trajectory.chmod(0o600)
            completed = SimpleNamespace(returncode=0, stdout="{}", stderr="")
            with mock.patch.object(repair, "OPENCLAW_SESSION_ROOT", root), \
                    mock.patch.object(
                        repair.uuid, "uuid4", return_value=uuid.UUID(session_id)), \
                    mock.patch.object(repair.time, "time_ns", return_value=0), \
                    mock.patch.object(repair, "run", return_value=completed), \
                    self.assertRaisesRegex(
                        RuntimeError, "AUTH_REARM_PROFILE_PROBE_NOT_OK"):
                repair._probe_auth_profile(profile_id)

    def test_model_canary_rejects_missing_trajectory_identity(self) -> None:
        session_id = "12345678-1234-5678-1234-567812345678"
        events = self.auth_canary_events(session_id, "openai:test-profile")
        del events[3]["provider"]
        valid, _, _ = repair._auth_canary_trajectory_ok(
            events, session_id=session_id, invoked_at_ms=0,
            profile_ids=("openai:test-profile",),
            required_profile_id="openai:test-profile")
        self.assertFalse(valid)

    def test_model_canary_rejects_multiple_model_completions(self) -> None:
        session_id = "12345678-1234-5678-1234-567812345678"
        events = self.auth_canary_events(session_id, "openai:test-profile")
        events.insert(4, dict(events[3]))
        valid, _, _ = repair._auth_canary_trajectory_ok(
            events, session_id=session_id, invoked_at_ms=0,
            profile_ids=("openai:test-profile",),
            required_profile_id="openai:test-profile")
        self.assertFalse(valid)

    def test_model_canary_rejects_cross_run_event_splice(self) -> None:
        session_id = "12345678-1234-5678-1234-567812345678"
        events = self.auth_canary_events(session_id, "openai:test-profile")
        events[-1]["runId"] = "11111111-1111-4111-8111-111111111111"
        valid, _, _ = repair._auth_canary_trajectory_ok(
            events, session_id=session_id, invoked_at_ms=0,
            profile_ids=("openai:test-profile",),
            required_profile_id="openai:test-profile")
        self.assertFalse(valid)

    def test_rearm_effective_auth_order_hash_is_order_independent(self) -> None:
        expected_profiles = (
            "openai:other-profile", "openai:test-profile")
        expected_digest = "sha256:" + hashlib.sha256(
            b'["openai:other-profile","openai:test-profile"]\n').hexdigest()
        results = []
        for order in (
                ["openai:test-profile", "openai:other-profile"],
                ["openai:other-profile", "openai:test-profile"]):
            completed = SimpleNamespace(
                returncode=0,
                stdout=json.dumps({
                    "agentId": "telegram-bot-8681289317",
                    "provider": "openai",
                    "order": order,
                }),
                stderr="",
            )
            with mock.patch.object(
                    repair, "run", return_value=completed) as run:
                results.append(repair._verify_effective_auth_order(
                    "openai:test-profile"))
            self.assertEqual(run.call_args.kwargs["timeout"], 60)
        self.assertEqual(results, [
            (expected_profiles, expected_digest),
            (expected_profiles, expected_digest),
        ])

    def test_rearm_effective_auth_order_rejects_response_drift(self) -> None:
        invalid_documents = (
            {
                "agentId": "wrong-agent", "provider": "openai",
                "order": ["openai:test-profile"],
            },
            {
                "agentId": "telegram-bot-8681289317", "provider": "other",
                "order": ["openai:test-profile"],
            },
            {
                "agentId": "telegram-bot-8681289317", "provider": "openai",
                "order": [],
            },
            {
                "agentId": "telegram-bot-8681289317", "provider": "openai",
                "order": ["openai:test-profile", "openai:test-profile"],
            },
            {
                "agentId": "telegram-bot-8681289317", "provider": "openai",
                "order": ["bad id with spaces"],
            },
            {
                "agentId": "telegram-bot-8681289317", "provider": "openai",
                "order": ["openai:other-profile"],
            },
        )
        for document in invalid_documents:
            completed = SimpleNamespace(
                returncode=0, stdout=json.dumps(document), stderr="")
            with self.subTest(document=document), \
                    mock.patch.object(repair, "run", return_value=completed), \
                    self.assertRaisesRegex(
                        RuntimeError, "EFFECTIVE_PROFILE_ORDER_INVALID"):
                repair._verify_effective_auth_order("openai:test-profile")

    def test_auth_rearm_rejects_allowlist_change_across_canary(self) -> None:
        state = self.incident_state()
        first = (
            "openai:new-profile@example.com", "openai:other-profile")
        second = (
            "openai:new-profile@example.com", "openai:third-profile")
        first_digest = repair._auth_profile_allowlist_sha256(list(first))
        second_digest = repair._auth_profile_allowlist_sha256(list(second))
        env = {
            "HEPTA_LOCAL_AI_AUTH_GENERATION": "auth-generation-new",
            "HEPTA_LOCAL_AI_AUTH_PROFILE_ID":
                "openai:new-profile@example.com",
            "HEPTA_LOCAL_AI_CAMPAIGN_ID": "new-campaign",
        }
        with mock.patch.object(
                repair, "run", return_value=SimpleNamespace(
                    returncode=3, stdout="inactive\n", stderr="")), \
                mock.patch.object(
                    repair, "load_agent", return_value=FakeAgent()), \
                mock.patch.object(
                    repair, "_load_root_agent_state", return_value=state), \
                mock.patch.object(repair, "read_env", return_value=env), \
                mock.patch.object(
                    repair, "_verified_risk_recovery_receipt",
                    return_value=(Path("risk.json"), {
                        "completed_at_ms": 2}, "a" * 64)), \
                mock.patch.object(
                    repair, "_verified_rearm_stack_receipt",
                    return_value=({"completed_at_ms": 2},
                                  "sha256:" + "9" * 64)), \
                mock.patch.object(
                    repair, "_verify_effective_auth_order",
                    side_effect=[
                        (first, first_digest), (second, second_digest)]), \
                mock.patch.object(
                    repair, "_probe_auth_profile",
                    return_value=(3, "probe-model")), \
                mock.patch.object(
                    repair, "_production_auth_canary",
                    return_value=(4, "canary-id", "sha256:" + "5" * 64)), \
                mock.patch.object(
                    repair, "_publish_auth_profile_allowlist_sha256") as publish, \
                self.assertRaisesRegex(
                    RuntimeError, "PROFILE_ALLOWLIST_DRIFTED"):
            repair.auth_rearm(
                "openai:new-profile@example.com", "auth-generation-new")
        publish.assert_not_called()

    def test_reset_agent_failure_state_reloads_garbage_collected_unit(
            self) -> None:
        responses = [
            SimpleNamespace(
                returncode=1, stdout="",
                stderr=(
                    "Failed to reset failed state of unit "
                    "hepta-local-ai-paper-agent.service: Unit "
                    "hepta-local-ai-paper-agent.service not loaded.")),
            SimpleNamespace(
                returncode=0,
                stdout=(
                    "Result=success\nExecMainStatus=0\nLoadState=loaded\n"
                    "ActiveState=inactive\n"),
                stderr=""),
        ]
        with mock.patch.object(repair, "run", side_effect=responses) as run:
            repair._reset_agent_failure_state()
        self.assertEqual(run.call_count, 2)
        self.assertEqual(run.call_args_list[1], mock.call([
            "/usr/bin/systemctl", "show",
            "hepta-local-ai-paper-agent.service",
            "--property=LoadState", "--property=ActiveState",
            "--property=Result", "--property=ExecMainStatus", "--no-pager",
        ], timeout=15))

    def test_reset_agent_failure_state_does_not_mask_other_errors(self) -> None:
        completed = SimpleNamespace(
            returncode=1, stdout="", stderr="Access denied")
        with mock.patch.object(repair, "run", return_value=completed), \
                self.assertRaisesRegex(
                    RuntimeError, "REPAIR_COMMAND_FAILED: Access denied"):
            repair._reset_agent_failure_state()

    def test_reset_agent_failure_state_rejects_unclean_reloaded_unit(
            self) -> None:
        responses = [
            SimpleNamespace(
                returncode=1, stdout="",
                stderr=(
                    "Failed to reset failed state of unit "
                    "hepta-local-ai-paper-agent.service: Unit "
                    "hepta-local-ai-paper-agent.service not loaded.")),
            SimpleNamespace(
                returncode=0,
                stdout=(
                    "Result=failed\nExecMainStatus=75\nLoadState=loaded\n"
                    "ActiveState=failed\n"),
                stderr=""),
        ]
        with mock.patch.object(repair, "run", side_effect=responses), \
                self.assertRaisesRegex(
                    RuntimeError, "AUTH_REARM_AGENT_UNIT_STATE_INVALID"):
            repair._reset_agent_failure_state()

    def test_auth_rearm_success_writes_generation_bound_state(self) -> None:
        state = self.incident_state()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path = root / "state.json"
            state_path.write_text(json.dumps(state), encoding="ascii")
            state_path.chmod(0o600)
            safety_latch = root / "safety-stop.pending.json"
            safety_latch.write_text("{}\n", encoding="ascii")
            automatic_attempt = root / "automatic-risk-attempt.json"
            env = {
                "HEPTA_LOCAL_AI_AUTH_GENERATION": "auth-generation-new",
                "HEPTA_LOCAL_AI_AUTH_PROFILE_ID": "openai:new-profile@example.com",
                "HEPTA_LOCAL_AI_CAMPAIGN_ID": "new-campaign",
                "HEPTA_LOCAL_AI_STRATEGY_ID": "strategy",
                "HEPTA_LOCAL_AI_STRATEGY_VERSION": "2",
                "HEPTA_LOCAL_AI_STRATEGY_SHA256": "sha256:" + "2" * 64,
            }
            allowlist = (
                "openai:new-profile@example.com", "openai:other-profile")
            allowlist_digest = repair._auth_profile_allowlist_sha256(
                list(allowlist))
            canary_profile_digest = "sha256:" + hashlib.sha256(
                b"openai:other-profile").hexdigest()
            with mock.patch.object(repair, "AGENT_STATE", state_path), \
                    mock.patch.object(repair, "END_FLAT_RECEIPT_ROOT", root), \
                    mock.patch.object(repair, "SAFETY_LATCH", safety_latch), \
                    mock.patch.object(
                        repair, "AUTOMATIC_RISK_ATTEMPT",
                        automatic_attempt), \
                    mock.patch.object(repair, "load_agent", return_value=FakeAgent()), \
                    mock.patch.object(
                        repair, "_load_root_agent_state", return_value=state), \
                    mock.patch.object(repair, "read_env", return_value=env), \
                    mock.patch.multiple(
                        repair,
                        _verified_risk_recovery_receipt=mock.Mock(
                            return_value=(root / "risk.json", {
                                "completed_at_ms": 2}, "a" * 64)),
                        _verified_rearm_stack_receipt=mock.Mock(
                            return_value=({"completed_at_ms": 2},
                                          "sha256:" + "9" * 64))), \
                    mock.patch.object(
                        repair, "_verify_effective_auth_order",
                        return_value=(allowlist, allowlist_digest)) as verify_order, \
                    mock.patch.object(
                        repair, "_probe_auth_profile", return_value=(3, "probe-model")), \
                    mock.patch.object(
                        repair, "_production_auth_canary",
                        return_value=(
                            4, "canary-id", canary_profile_digest)), \
                    mock.patch.object(
                        repair, "_current_zero_proof",
                        side_effect=[(10, 20), (11, 21)]), \
                    mock.patch.object(repair.time, "sleep"), \
                    mock.patch.object(
                        repair, "run", return_value=SimpleNamespace(
                            returncode=3, stdout="inactive\n", stderr="")), \
                    mock.patch.object(
                        repair, "_reset_agent_failure_state") as reset_failed, \
                    mock.patch.object(
                        repair, "_publish_auth_profile_allowlist_sha256") as set_allowlist, \
                    mock.patch.multiple(
                        repair.os,
                        lstat=mock.Mock(return_value=self.root_metadata()),
                        fstat=mock.Mock(side_effect=self.root_fstat),
                        fchown=mock.DEFAULT, chown=mock.DEFAULT,
                        chmod=mock.DEFAULT):
                repair.auth_rearm(
                    "openai:new-profile@example.com", "auth-generation-new")
            reset_failed.assert_called_once_with()
            set_allowlist.assert_called_once_with(env, allowlist_digest)
            self.assertEqual(verify_order.call_count, 2)
            rearmed = json.loads(state_path.read_text(encoding="ascii"))
            receipt_path = next(root.glob("auth-rearm-*.receipt.json"))
            receipt = json.loads(receipt_path.read_text(encoding="ascii"))
            safety_latch_removed = not safety_latch.exists()
        self.assertEqual(
            rearmed["auth_generation_rearmed"], "auth-generation-new")
        self.assertEqual(
            rearmed["auth_profile_sha256_rearmed"],
            "sha256:" + hashlib.sha256(
                b"openai:new-profile@example.com").hexdigest())
        self.assertEqual(
            rearmed["auth_profile_allowlist_sha256_rearmed"],
            allowlist_digest)
        self.assertEqual(
            receipt["auth_profile_allowlist_sha256"], allowlist_digest)
        self.assertEqual(
            receipt["production_canary_auth_profile_sha256"],
            canary_profile_digest)
        self.assertTrue(
            rearmed["auth_rearm_receipt_sha256"].startswith("sha256:"))
        self.assertEqual(
            rearmed["runtime_binding"], receipt["runtime_binding"])
        self.assertFalse(rearmed["trading_suspended"])
        self.assertTrue(rearmed["manual_start_required"])
        self.assertTrue(safety_latch_removed)

    def test_auth_rearm_state_write_failure_restores_old_latch(self) -> None:
        state = self.incident_state()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path = root / "state.json"
            state_path.write_text(json.dumps(state), encoding="ascii")
            state_path.chmod(0o600)
            safety_latch = root / "safety-stop.pending.json"
            safety_latch.write_text("{}\n", encoding="ascii")
            automatic_attempt = root / "automatic-risk-attempt.json"
            env = {
                "HEPTA_LOCAL_AI_AUTH_GENERATION": "auth-generation-new",
                "HEPTA_LOCAL_AI_AUTH_PROFILE_ID": "openai:new-profile@example.com",
                "HEPTA_LOCAL_AI_CAMPAIGN_ID": "new-campaign",
                "HEPTA_LOCAL_AI_STRATEGY_ID": "strategy",
                "HEPTA_LOCAL_AI_STRATEGY_VERSION": "2",
                "HEPTA_LOCAL_AI_STRATEGY_SHA256": "sha256:" + "2" * 64,
            }
            allowlist = (
                "openai:new-profile@example.com", "openai:other-profile")
            allowlist_digest = repair._auth_profile_allowlist_sha256(
                list(allowlist))
            agent = FakeAgent()
            original_write_json = agent.write_json
            main_link_counts: list[int] = []

            def failing_write(path: Path, value: object) -> None:
                if (Path(path) == state_path and isinstance(value, dict) and
                        value.get("auth_generation_rearmed") ==
                        "auth-generation-new"):
                    main_link_counts.append(
                        os.stat(
                            state_path, follow_symlinks=False).st_nlink)
                    raise OSError("simulated state write failure")
                original_write_json(path, value)

            with mock.patch.object(agent, "write_json", side_effect=failing_write), \
                    mock.patch.object(repair, "AGENT_STATE", state_path), \
                    mock.patch.object(repair, "END_FLAT_RECEIPT_ROOT", root), \
                    mock.patch.object(repair, "SAFETY_LATCH", safety_latch), \
                    mock.patch.object(
                        repair, "AUTOMATIC_RISK_ATTEMPT",
                        automatic_attempt), \
                    mock.patch.object(repair, "load_agent", return_value=agent), \
                    mock.patch.object(
                        repair, "_load_root_agent_state", return_value=state), \
                    mock.patch.object(repair, "read_env", return_value=env), \
                    mock.patch.multiple(
                        repair,
                        _verified_risk_recovery_receipt=mock.Mock(
                            return_value=(root / "risk.json", {
                                "completed_at_ms": 2}, "a" * 64)),
                        _verified_rearm_stack_receipt=mock.Mock(
                            return_value=({"completed_at_ms": 2},
                                          "sha256:" + "9" * 64))), \
                    mock.patch.object(
                        repair, "_verify_effective_auth_order",
                        return_value=(allowlist, allowlist_digest)), \
                    mock.patch.object(
                        repair, "_probe_auth_profile",
                        return_value=(3, "probe-model")), \
                    mock.patch.object(
                        repair, "_production_auth_canary",
                        return_value=(
                            4, "canary-id", "sha256:" + "5" * 64)), \
                    mock.patch.object(
                        repair, "_current_zero_proof",
                        side_effect=[(10, 20), (11, 21)]), \
                    mock.patch.object(repair.time, "sleep"), \
                    mock.patch.object(
                        repair, "run", return_value=SimpleNamespace(
                            returncode=3, stdout="inactive\n", stderr="")), \
                    mock.patch.object(repair, "_reset_agent_failure_state"), \
                    mock.patch.object(
                        repair, "_publish_auth_profile_allowlist_sha256"), \
                    mock.patch.multiple(
                        repair.os,
                        lstat=mock.Mock(return_value=self.root_metadata()),
                        fstat=mock.Mock(side_effect=self.root_fstat),
                        fchown=mock.DEFAULT, chown=mock.DEFAULT,
                        chmod=mock.DEFAULT), \
                    self.assertRaisesRegex(
                        OSError, "simulated state write failure"):
                repair.auth_rearm(
                    "openai:new-profile@example.com", "auth-generation-new")

            restored = json.loads(state_path.read_text(encoding="ascii"))
            sentinel_removed = not safety_latch.exists()
            restored_metadata = os.stat(
                state_path, follow_symlinks=False)
            backup_residue = list(root.glob("state.pre-auth-rearm-*.json"))

        self.assertTrue(restored["recovery_required"])
        self.assertTrue(restored["trading_suspended"])
        self.assertNotIn("auth_generation_rearmed", restored)
        self.assertEqual(main_link_counts, [1])
        self.assertEqual(restored_metadata.st_nlink, 1)
        self.assertEqual(backup_residue, [])
        self.assertTrue(sentinel_removed)

    def test_unusable_recovery_token_is_preserved_without_pending_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = root / "local-paper.token"
            recovery_root = root
            recovery = root / "risk-recovery-deadbeef.token"
            original.write_text("original\n", encoding="ascii")
            recovery.write_text("preserve-me\n", encoding="ascii")
            with mock.patch.object(repair, "TOKEN_FILE", original), \
                    mock.patch.object(
                        repair, "RISK_RECOVERY_TOKEN_ROOT", recovery_root), \
                    mock.patch.object(
                        repair, "session_usable", side_effect=[False, False]), \
                    mock.patch.object(repair, "provision_session") as provision, \
                    self.assertRaisesRegex(
                        RuntimeError, "SESSION_UNUSABLE_PRESERVED"):
                repair._select_risk_recovery_session(
                    "deadbeef", {"pending_order_id": None})
            self.assertEqual(recovery.read_text(encoding="ascii"),
                             "preserve-me\n")
            provision.assert_not_called()

    def test_unusable_recovery_token_is_preserved_with_pending_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = root / "local-paper.token"
            recovery = root / "risk-recovery-deadbeef.token"
            original.write_text("original\n", encoding="ascii")
            recovery.write_text("preserve-me\n", encoding="ascii")
            with mock.patch.object(repair, "TOKEN_FILE", original), \
                    mock.patch.object(
                        repair, "RISK_RECOVERY_TOKEN_ROOT", root), \
                    mock.patch.object(
                        repair, "session_usable", side_effect=[False, False]), \
                    mock.patch.object(repair, "provision_session") as provision, \
                    self.assertRaisesRegex(
                        RuntimeError, "SESSION_UNUSABLE_PRESERVED"):
                repair._select_risk_recovery_session(
                    "deadbeef", {"pending_order_id": 41})
            self.assertEqual(recovery.read_text(encoding="ascii"),
                             "preserve-me\n")
            provision.assert_not_called()

    def test_end_flat_resolves_revoke_pending_authority_before_selection(
            self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = root / "local-paper.token"
            recovery = root / (
                "risk-recovery-" + "a" * 24 + ".token")
            pending_authority = {"phase": "REVOKE_PENDING"}
            active_authority = {"phase": "ACTIVE"}

            def load_authority(path: Path) -> dict[str, str]:
                return (pending_authority if path == original
                        else active_authority)

            resolver = mock.Mock()
            session_usable = mock.Mock(
                side_effect=lambda path: path == recovery)
            provision = mock.Mock()
            managed_calls = [[original, recovery], [recovery]]
            with mock.patch.multiple(
                    repair,
                    TOKEN_FILE=original,
                    RISK_RECOVERY_TOKEN_ROOT=root,
                    _campaign_session_token_paths=mock.Mock(
                        side_effect=lambda: managed_calls.pop(0)),
                    _load_session_provision_intent=mock.Mock(
                        side_effect=load_authority),
                    _resolve_session_provision_intent=resolver,
                    session_usable=session_usable,
                    provision_session=provision):
                selected = repair._select_end_flat_session("campaign-test")

            self.assertEqual(selected, (recovery, False))
            resolver.assert_called_once_with(
                original, allow_active_revoke=True, cleanup=True)
            session_usable.assert_called_once_with(recovery)
            provision.assert_not_called()

    def test_end_flat_resolves_late_revoke_pending_before_returning_owner(
            self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = root / "local-paper.token"
            pending = root / (
                "risk-recovery-" + "b" * 24 + ".token")
            authorities = {
                original: {"phase": "ACTIVE"},
                pending: {"phase": "REVOKE_PENDING"},
            }
            resolver = mock.Mock()
            managed_calls = [[original, pending], [original]]
            session_usable = mock.Mock(return_value=True)
            with mock.patch.multiple(
                    repair,
                    TOKEN_FILE=original,
                    RISK_RECOVERY_TOKEN_ROOT=root,
                    _campaign_session_token_paths=mock.Mock(
                        side_effect=lambda: managed_calls.pop(0)),
                    _load_session_provision_intent=mock.Mock(
                        side_effect=lambda path: authorities[path]),
                    _resolve_session_provision_intent=resolver,
                    session_usable=session_usable,
                    provision_session=mock.Mock()):
                selected = repair._select_end_flat_session("campaign-test")

            self.assertEqual(selected, (original, True))
            resolver.assert_called_once_with(
                pending, allow_active_revoke=True, cleanup=True)
            session_usable.assert_called_once_with(original)

    def test_recovery_session_is_revoked_by_exact_generation_after_flat(
            self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            token = root / "risk-recovery-test.token"
            token.write_text("a" * 64 + "\n", encoding="ascii")
            token.chmod(0o600)
            lease = repair.session_lease_path(token)
            lease.write_text(json.dumps({
                "schema": "hepta.local-paper-session-lease.v1",
                "session_name": "paper-risk-recovery-test",
                "lease_generation": 13,
                "ttl_seconds": 900,
                "observed_at_ms": 1,
                "expires_at_ms": 901_000,
                "token_sha256": "sha256:" + hashlib.sha256(
                    token.read_bytes()).hexdigest(),
            }), encoding="ascii")
            lease.chmod(0o600)
            real_lstat = repair.os.lstat

            def owned_metadata(path: object) -> SimpleNamespace:
                metadata = real_lstat(path)
                owner = 2104 if Path(path) == token else 0
                return SimpleNamespace(
                    st_dev=metadata.st_dev, st_ino=metadata.st_ino,
                    st_mode=metadata.st_mode, st_nlink=metadata.st_nlink,
                    st_uid=owner, st_gid=owner,
                    st_size=metadata.st_size)

            response = SimpleNamespace(
                returncode=0, stdout=json.dumps({
                    "accepted": True,
                    "reason_code": "OK",
                    "lease_generation": 13,
                }), stderr="")
            with mock.patch.object(
                    repair.pwd, "getpwnam",
                    return_value=SimpleNamespace(pw_uid=2104, pw_gid=2104)), \
                    mock.patch.object(
                        repair, "token_metadata_safe", return_value=True), \
                    mock.patch.object(
                        repair, "_load_session_provision_intent",
                        return_value=None), \
                    mock.patch.object(
                        repair.os, "lstat", side_effect=owned_metadata), \
                    mock.patch.object(
                        repair, "run", return_value=response) as run:
                evidence = repair._revoke_recovery_session(token)

            token_exists = token.exists()
            lease_exists = lease.exists()

        self.assertFalse(token_exists)
        self.assertFalse(lease_exists)
        self.assertEqual(evidence, {
            "tool_session_revoked": True,
            "tool_session_lease_generation": 13,
            "tool_session_token_sha256": "sha256:" + hashlib.sha256(
                ("a" * 64 + "\n").encode("ascii")).hexdigest(),
        })
        self.assertEqual(run.call_args, mock.call([
            "/usr/bin/hepta-sessionctl", "--socket",
            repair.SUPERVISOR_SOCKET, "revoke", "--token-file", str(token),
            "--generation", "13", "--token-owner-uid", "2104",
        ], timeout=15))

    def test_end_flat_finalizer_preserves_active_recovery_without_zero_proof(
            self) -> None:
        token = Path("/tmp/end-flat-" + "a" * 24 + ".token")
        authority = {"phase": "ACTIVE"}
        load = mock.Mock(return_value=authority)
        resolve = mock.Mock()
        with mock.patch.multiple(
                repair,
                TOKEN_FILE=Path("/tmp/local-paper.token"),
                RISK_RECOVERY_TOKEN_ROOT=Path("/tmp"),
                _load_session_provision_intent=load,
                _resolve_session_provision_intent=resolve), \
                mock.patch.object(repair, "print") as output:
            outcome = repair._finalize_failed_end_flat_recovery_session(
                token, False)

        self.assertEqual(outcome, "PRESERVED")
        load.assert_called_once_with(token)
        resolve.assert_not_called()
        rendered = str(output.call_args.args[0])
        self.assertIn(
            "END_FLAT_RECOVERY_SESSION_FINALIZER_PRESERVED", rendered)
        self.assertIn("authority_phase=ACTIVE", rendered)
        self.assertIn("preserved=true", rendered)
        self.assertIn("RISK_ZERO_NOT_PROVEN", rendered)
        self.assertEqual(authority["phase"], "ACTIVE")

    def test_end_flat_finalizer_never_touches_primary_even_if_flag_is_wrong(
            self) -> None:
        token = Path("/tmp/local-paper.token")
        load = mock.Mock()
        resolve = mock.Mock()
        with mock.patch.multiple(
                repair,
                TOKEN_FILE=token,
                RISK_RECOVERY_TOKEN_ROOT=Path("/tmp"),
                _load_session_provision_intent=load,
                _resolve_session_provision_intent=resolve):
            outcome = repair._finalize_failed_end_flat_recovery_session(
                token, False)

        self.assertEqual(outcome, "SKIPPED_ORIGINAL")
        load.assert_not_called()
        resolve.assert_not_called()

    def test_end_flat_finalizer_exact_revoke_requires_zero_proof(
            self) -> None:
        token = Path("/tmp/end-flat-" + "b" * 24 + ".token")
        authority = {"phase": "ACTIVE"}
        load = mock.Mock(return_value=authority)
        resolve = mock.Mock(side_effect=RuntimeError(
            "SESSION_OWNER_RECOVERY_REQUIRED"))
        with mock.patch.multiple(
                repair,
                TOKEN_FILE=Path("/tmp/local-paper.token"),
                RISK_RECOVERY_TOKEN_ROOT=Path("/tmp"),
                _load_session_provision_intent=load,
                _resolve_session_provision_intent=resolve), \
                mock.patch.object(repair, "print") as output:
            outcome = repair._finalize_failed_end_flat_recovery_session(
                token, False, risk_zero_proven=True)

        self.assertEqual(outcome, "UNCERTAIN")
        resolve.assert_called_once_with(
            token, allow_active_revoke=True, cleanup=True)
        rendered = str(output.call_args.args[0])
        self.assertIn("authority_phase=ACTIVE", rendered)
        self.assertIn("preserved=true", rendered)

    def test_end_flat_finalizer_exact_revoke_reports_success_after_zero_proof(
            self) -> None:
        token = Path("/tmp/end-flat-" + "c" * 24 + ".token")
        load = mock.Mock(return_value={"phase": "REVOKE_PENDING"})
        resolve = mock.Mock(return_value=True)
        with mock.patch.multiple(
                repair,
                TOKEN_FILE=Path("/tmp/local-paper.token"),
                RISK_RECOVERY_TOKEN_ROOT=Path("/tmp"),
                _load_session_provision_intent=load,
                _resolve_session_provision_intent=resolve), \
                mock.patch.object(repair, "print") as output:
            outcome = repair._finalize_failed_end_flat_recovery_session(
                token, False, risk_zero_proven=True)

        self.assertEqual(outcome, "REVOKED")
        resolve.assert_called_once_with(
            token, allow_active_revoke=True, cleanup=True)
        rendered = str(output.call_args.args[0])
        self.assertIn("END_FLAT_RECOVERY_SESSION_FINALIZED", rendered)
        self.assertIn("authority_phase=REVOKE_PENDING", rendered)

    def test_risk_recover_cancels_flattens_receipts_and_stays_latched(
            self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path = root / "state.json"
            lock_path = root / "risk-recovery.lock"
            sentinel = root / "safety-stop.pending.json"
            sentinel.write_text("{}\n", encoding="ascii")
            automatic_attempt = root / "automatic-risk-attempt.json"
            state = self.incident_state()
            state.update({
                "recovery_complete": False,
                "recovery_phase": "REQUESTED",
                "pending_order_id": 41,
                "incident_pending_order_id": 41,
            })
            state_path.write_text(json.dumps(state), encoding="ascii")
            env = {
                "HEPTA_LOCAL_AI_AUTH_GENERATION": "auth-generation-old",
                "HEPTA_LOCAL_AI_AUTH_PROFILE_ID": "openai:test-profile",
                "HEPTA_LOCAL_AI_CAMPAIGN_ID": "old-campaign",
                "HEPTA_LOCAL_AI_STRATEGY_ID": "strategy",
                "HEPTA_LOCAL_AI_STRATEGY_VERSION": "2",
                "HEPTA_LOCAL_AI_STRATEGY_SHA256": "sha256:" + "2" * 64,
            }
            events: list[str] = []
            agent = mock.Mock()
            agent.SCHEMA = FakeAgent.SCHEMA
            agent.INSTRUMENT = "EUR.USD"
            agent.TERMINAL_NON_FILL_STATUSES = frozenset({
                "rejected", "inactive", "cancelled", "apicancelled",
            })
            agent.empty_state.return_value = FakeAgent.empty_state()
            agent.load_state.side_effect = lambda path: json.loads(
                Path(path).read_text(encoding="ascii"))
            agent.write_json.side_effect = FakeAgent.write_json
            agent._quantity_equal.side_effect = (
                lambda left, right: abs(float(left) - float(right)) <= 1e-6)
            agent.active_orders.side_effect = [[41], []]

            def cancel(*_args: object, **_kwargs: object) -> dict[str, object]:
                self.assertTrue(automatic_attempt.exists())
                events.append("cancel")
                return {"status": "ok"}

            def flatten(*_args: object, **_kwargs: object) -> float:
                events.append("flatten")
                return 0.0

            def authoritative(*_args: object, **_kwargs: object) \
                    -> tuple[float, int, int]:
                values = authoritative_values.pop(0)
                events.append(f"position:{values[0]:g}")
                return values

            authoritative_values = [
                (-25000.0, 10, 20), (0.0, 11, 21), (0.0, 12, 22)]
            agent.tool_response.side_effect = cancel
            agent.flatten.side_effect = flatten
            order_snapshots = [{
                **self.owner_orders_snapshot([41]),
                "recent_orders": [{
                    "order_id": 41, "instrument": "EUR.USD",
                    "terminal": False,
                }],
            }, {
                **self.owner_orders_snapshot([]),
                "recent_orders": [{
                    "order_id": 41,
                    "status": "Filled",
                    "terminal": True,
                    "economic_fill": True,
                    "filled_quantity": 25000,
                    "remaining_quantity": 0,
                    "average_fill_price": 1.2,
                    "instrument": "EUR.USD",
                    "side": "SELL",
                }],
            }, {
                **self.owner_orders_snapshot([]),
                "recent_orders": [{
                    "order_id": 41,
                    "status": "Filled",
                    "terminal": True,
                    "economic_fill": True,
                    "filled_quantity": 25000,
                    "remaining_quantity": 0,
                    "average_fill_price": 1.2,
                    "instrument": "EUR.USD",
                    "side": "SELL",
                }],
            }]

            def order_snapshot(*_args: object, **_kwargs: object) \
                    -> dict[str, object]:
                value = order_snapshots.pop(0)
                if (value.get("active_order_ids") == [] and
                        "terminal" not in events):
                    events.append("terminal")
                return value

            agent.orders_snapshot.side_effect = order_snapshot
            agent.validate_order_projection.side_effect = lambda snapshot: {
                "connection_epoch": 1,
                "generation": 1,
                "owner_scope": {
                    "agent_id": "paper-agent",
                    "session_id": "paper-session",
                    "execution_domain": "alpha",
                    "account": "DU123",
                },
                "global_active_order_ids": tuple(
                    snapshot["active_order_ids"]),
                "owned_active_order_ids": tuple(
                    snapshot["active_order_ids"]),
            }
            agent.tool.side_effect = [
                {"source": "IB", "authoritative": True,
                 "gross_scope": "PAPER_BASELINE_DELTA",
                 "gross_absolute_position": 0},
                {"source": "IB", "authoritative": True,
                 "gross_scope": "PAPER_BASELINE_DELTA",
                 "gross_absolute_position": 0},
            ]
            arguments = SimpleNamespace(state_file=root / "recovery-state.json")
            inactive = SimpleNamespace(
                returncode=0, stdout="active\n", stderr="")

            def persist_checkpoint(
                    checkpoint: dict[str, object], *, create: bool = False,
            ) -> None:
                path = repair._risk_recovery_checkpoint_path(
                    str(checkpoint["suspension_id"]))
                if create and path.exists():
                    raise FileExistsError(path)
                checkpoint["updated_at_ms"] = 1_786_000_000_001
                FakeAgent.write_json(path, checkpoint)

            session_descriptor = {
                "token_name": "local-paper.token",
                "token_sha256": "sha256:" + "7" * 64,
                "lease_generation": 9,
                "revoked": False,
                "revoke_retry_intent": False,
            }
            revoke_sessions = mock.Mock(return_value={
                "tool_session_revoked": True,
                "tool_session_lease_generation": 9,
                "tool_session_token_sha256": "sha256:" + "7" * 64,
            })
            run_checked = mock.Mock()
            with mock.patch.multiple(
                    repair,
                    AGENT_STATE=state_path, END_FLAT_RECEIPT_ROOT=root,
                    RISK_RECOVERY_TOKEN_ROOT=root,
                    SESSION_AUTHORITY_ROOT=root / "authority",
                    RISK_RECOVERY_LOCK=lock_path, SAFETY_LATCH=sentinel,
                    AUTOMATIC_RISK_ATTEMPT=automatic_attempt,
                    load_agent=mock.Mock(return_value=agent),
                    _load_root_agent_state=mock.Mock(return_value=state),
                    read_env=mock.Mock(return_value=env),
                    _select_risk_recovery_session=mock.Mock(return_value=(
                        root / "local-paper.token", True)),
                    _campaign_session_token_paths=mock.Mock(return_value=[]),
                    agent_arguments=mock.Mock(return_value=arguments),
                    _risk_recovery_halt_campaign=mock.Mock(
                        return_value="halt_confirmed"),
                    _risk_recovery_raw_price_pnl_evidence=mock.Mock(
                        side_effect=RuntimeError("accounting unavailable")),
                    _risk_recovery_session_descriptors_before_revoke=
                        mock.Mock(return_value=[session_descriptor]),
                    _risk_recovery_policy_sha256=mock.Mock(
                        return_value="sha256:" + "8" * 64),
                    _persist_risk_recovery_checkpoint=mock.Mock(
                        side_effect=persist_checkpoint),
                    _revoke_recovery_session=revoke_sessions,
                    authoritative_state=mock.Mock(side_effect=authoritative),
                    run=mock.Mock(return_value=inactive),
                    run_checked=run_checked), \
                    mock.patch.multiple(
                        repair.os,
                        fstat=mock.Mock(return_value=self.root_metadata()),
                        fchown=mock.DEFAULT, fchmod=mock.DEFAULT,
                        chown=mock.DEFAULT, chmod=mock.DEFAULT), \
                    mock.patch.object(repair.time, "sleep"):
                repair.risk_recover(automatic=True)

            final_state = json.loads(state_path.read_text(encoding="ascii"))
            receipts = list(root.glob("risk-recovery-*.receipt.json"))
            self.assertEqual(len(receipts), 1)
            receipt_raw = receipts[0].read_bytes()
            receipt = json.loads(receipt_raw)
            automatic_marker = json.loads(
                automatic_attempt.read_text(encoding="ascii"))
            sentinel_still_present = sentinel.exists()

        self.assertEqual(
            events,
            ["cancel", "terminal", "position:-25000", "flatten", "position:0",
             "position:0"])
        expected_call_id = "risk-cancel-" + hashlib.sha256(
            b"suspension-auth-rearm-test:41").hexdigest()[:32]
        self.assertEqual(
            agent.tool_response.call_args_list[0].args[3], expected_call_id)
        self.assertEqual(receipt["position"], 0)
        self.assertEqual(receipt["active_orders"], 0)
        self.assertEqual(receipt["gross_absolute_position"], 0)
        self.assertFalse(receipt["trading_resumed"])
        self.assertEqual(receipt["cancel_attempted_order_ids"], [41])
        self.assertEqual(receipt["terminally_reconciled_order_ids"], [41])
        self.assertEqual(receipt["first_position_generation"], 11)
        self.assertEqual(receipt["second_position_generation"], 12)
        self.assertIsNone(receipt["recovery_raw_price_pnl"])
        self.assertFalse(
            receipt["recovery_raw_price_pnl_commission_included"])
        self.assertEqual(automatic_marker["first_mutation"], "cancel")
        self.assertTrue(automatic_marker["automatic_attempt_consumed"])
        self.assertTrue(receipt["tool_session_revoked"])
        self.assertEqual(receipt["tool_session_lease_generation"], 9)
        revoke_sessions.assert_called_once_with(
            root / "local-paper.token", unlink=False,
            allow_already_absent=False)
        self.assertTrue(final_state["recovery_required"])
        self.assertTrue(final_state["trading_suspended"])
        self.assertTrue(final_state["recovery_complete"])
        self.assertEqual(final_state["recovery_phase"], "FLAT_CONFIRMED")
        self.assertEqual(final_state["unrealized_pnl_estimate"], 0.0)
        self.assertIsNone(final_state["entry_mid"])
        self.assertEqual(final_state["entry_quantity"], 0.0)
        self.assertIsNone(final_state["entry_order_id"])
        self.assertEqual(
            final_state["recovery_receipt_sha256"],
            "sha256:" + hashlib.sha256(receipt_raw).hexdigest())
        self.assertTrue(sentinel_still_present)
        run_checked.assert_called_once_with([
            "/usr/bin/systemctl", "stop",
            repair.SESSION_RENEW_TIMER,
            repair.SESSION_RENEW_SERVICE,
            repair.SUPERVISOR_TIMER,
            repair.SUPERVISOR_SERVICE,
            repair.AGENT_SERVICE,
        ], timeout=30)
        self.assertNotIn(
            "start", [str(value) for call in run_checked.call_args_list
                      for value in call.args[0]])

    def test_risk_recover_second_zero_proof_failure_keeps_latch(
            self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path = root / "state.json"
            sentinel = root / "safety-stop.pending.json"
            sentinel.write_text("{}\n", encoding="ascii")
            state = self.incident_state()
            state.update({
                "recovery_complete": False,
                "recovery_phase": "REQUESTED",
                "pending_order_id": None,
            })
            state_path.write_text(json.dumps(state), encoding="ascii")
            env = {
                "HEPTA_LOCAL_AI_AUTH_GENERATION": "auth-generation-old",
                "HEPTA_LOCAL_AI_AUTH_PROFILE_ID": "openai:test-profile",
                "HEPTA_LOCAL_AI_CAMPAIGN_ID": "old-campaign",
                "HEPTA_LOCAL_AI_STRATEGY_ID": "strategy",
                "HEPTA_LOCAL_AI_STRATEGY_VERSION": "2",
                "HEPTA_LOCAL_AI_STRATEGY_SHA256": "sha256:" + "2" * 64,
            }
            agent = mock.Mock()
            agent.SCHEMA = FakeAgent.SCHEMA
            agent.INSTRUMENT = "EUR.USD"
            agent.TERMINAL_NON_FILL_STATUSES = frozenset({
                "rejected", "inactive", "cancelled", "apicancelled",
            })
            agent.empty_state.return_value = FakeAgent.empty_state()
            agent.write_json.side_effect = FakeAgent.write_json
            agent._quantity_equal.side_effect = (
                lambda left, right: abs(float(left) - float(right)) <= 1e-6)
            agent.active_orders.return_value = []
            agent.orders_snapshot.return_value = self.owner_orders_snapshot([])
            agent.validate_order_projection.return_value = {
                "connection_epoch": 1,
                "generation": 1,
                "owner_scope": {
                    "agent_id": "paper-agent",
                    "session_id": "paper-session",
                    "execution_domain": "alpha",
                    "account": "DU123",
                },
                "global_active_order_ids": (),
                "owned_active_order_ids": (),
            }
            agent.flatten.return_value = 0.0
            agent.tool.side_effect = [
                {"source": "IB", "authoritative": True,
                 "gross_scope": "PAPER_BASELINE_DELTA",
                 "gross_absolute_position": 0},
                {"source": "IB", "authoritative": True,
                 "gross_scope": "PAPER_BASELINE_DELTA",
                 "gross_absolute_position": 25000},
            ]
            arguments = SimpleNamespace(state_file=root / "recovery-state.json")
            with mock.patch.object(repair, "AGENT_STATE", state_path), \
                    mock.patch.object(repair, "END_FLAT_RECEIPT_ROOT", root), \
                    mock.patch.object(
                        repair, "RISK_RECOVERY_LOCK",
                        root / "risk-recovery.lock"), \
                    mock.patch.object(repair, "SAFETY_LATCH", sentinel), \
                    mock.patch.object(repair, "load_agent", return_value=agent), \
                    mock.patch.object(
                        repair, "_load_root_agent_state", return_value=state), \
                    mock.patch.object(repair, "read_env", return_value=env), \
                    mock.patch.object(
                        repair, "_select_risk_recovery_session",
                        return_value=(root / "session.token", True)), \
                    mock.patch.object(
                        repair, "_campaign_session_token_paths",
                        return_value=[]), \
                    mock.patch.object(
                        repair, "agent_arguments", return_value=arguments), \
                    mock.patch.object(
                        repair, "_risk_recovery_halt_campaign",
                        return_value="halt_confirmed"), \
                    mock.patch.object(
                        repair, "authoritative_state", side_effect=[
                            (-25000.0, 10, 20),
                            (0.0, 11, 21),
                            (0.0, 12, 22),
                        ]), \
                    mock.patch.object(
                        repair, "run", return_value=SimpleNamespace(
                            returncode=0, stdout="active\n", stderr="")), \
                    mock.patch.object(repair, "run_checked") as run_checked, \
                    mock.patch.multiple(
                        repair.os,
                        fstat=mock.Mock(return_value=self.root_metadata()),
                        fchown=mock.DEFAULT,
                        chown=mock.DEFAULT,
                        chmod=mock.DEFAULT), \
                    mock.patch.object(repair.time, "sleep"), \
                    self.assertRaisesRegex(
                        RuntimeError, "END_FLAT_FINAL_RISK_NOT_ZERO"):
                repair.risk_recover()

            persisted = json.loads(state_path.read_text(encoding="ascii"))
            receipt_paths = list(root.glob("risk-recovery-*.receipt.json"))
            sentinel_still_present = sentinel.exists()

        self.assertFalse(persisted["recovery_complete"])
        self.assertEqual(persisted["recovery_phase"], "REQUESTED")
        self.assertTrue(persisted["recovery_required"])
        self.assertTrue(persisted["trading_suspended"])
        self.assertEqual(receipt_paths, [])
        self.assertTrue(sentinel_still_present)
        self.assertNotIn(
            "start", [str(value) for call in run_checked.call_args_list
                      for value in call.args[0]])

    def test_automatic_recovery_read_only_failure_does_not_consume_attempt(
            self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path = root / "state.json"
            state = self.incident_state()
            state.update({
                "recovery_complete": False,
                "recovery_phase": "REQUESTED",
                "pending_order_id": None,
            })
            state_path.write_text(json.dumps(state), encoding="ascii")
            automatic_attempt = root / "automatic-risk-attempt.json"
            agent = mock.Mock()
            agent.empty_state.return_value = FakeAgent.empty_state()
            agent.orders_snapshot.side_effect = RuntimeError(
                "EXECUTION_GATEWAY_DAEMON_IDENTITY_MISMATCH")
            env = {
                "HEPTA_LOCAL_AI_AUTH_GENERATION": "auth-generation-old",
                "HEPTA_LOCAL_AI_AUTH_PROFILE_ID": "openai:test-profile",
                "HEPTA_LOCAL_AI_CAMPAIGN_ID": "old-campaign",
                "HEPTA_LOCAL_AI_STRATEGY_ID": "strategy",
                "HEPTA_LOCAL_AI_STRATEGY_VERSION": "2",
                "HEPTA_LOCAL_AI_STRATEGY_SHA256": "sha256:" + "2" * 64,
            }
            with mock.patch.object(repair, "AGENT_STATE", state_path), \
                    mock.patch.object(repair, "END_FLAT_RECEIPT_ROOT", root), \
                    mock.patch.object(
                        repair, "RISK_RECOVERY_LOCK",
                        root / "risk-recovery.lock"), \
                    mock.patch.object(
                        repair, "AUTOMATIC_RISK_ATTEMPT",
                        automatic_attempt), \
                    mock.patch.object(repair, "load_agent", return_value=agent), \
                    mock.patch.object(
                        repair, "_load_root_agent_state",
                        return_value=state), \
                    mock.patch.object(repair, "read_env", return_value=env), \
                    mock.patch.object(
                        repair, "_select_risk_recovery_session",
                        return_value=(root / "session.token", True)), \
                    mock.patch.object(
                        repair, "_campaign_session_token_paths",
                        return_value=[]), \
                    mock.patch.object(
                        repair, "agent_arguments", return_value=object()), \
                    mock.patch.object(
                        repair, "_risk_recovery_halt_campaign",
                        return_value="halt_confirmed"), \
                    mock.patch.object(
                        repair, "run", return_value=SimpleNamespace(
                            returncode=0, stdout="active\n", stderr="")), \
                    mock.patch.object(repair, "run_checked"), \
                    mock.patch.object(
                        repair.os, "fstat", return_value=self.root_metadata()), \
                    self.assertRaisesRegex(
                        RuntimeError,
                        "EXECUTION_GATEWAY_DAEMON_IDENTITY_MISMATCH"):
                repair.risk_recover(automatic=True)
            self.assertFalse(automatic_attempt.exists())

    def test_explicit_recovery_is_not_blocked_by_automatic_marker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path = root / "state.json"
            state = self.incident_state()
            state.update({
                "recovery_complete": False,
                "recovery_phase": "REQUESTED",
                "pending_order_id": None,
            })
            state_path.write_text(json.dumps(state), encoding="ascii")
            automatic_attempt = root / "automatic-risk-attempt.json"
            automatic_attempt.write_text(json.dumps({
                "schema": "hepta.local-paper-automatic-risk-attempt.v1",
                "attempted_at_ms": 1,
                "state_sha256": "sha256:" + "1" * 64,
                "automatic_attempt_consumed": True,
                "paper_only": True,
                "live_authorized": False,
            }) + "\n", encoding="ascii")
            automatic_attempt.chmod(0o600)
            agent = mock.Mock()
            agent.empty_state.return_value = FakeAgent.empty_state()
            agent.orders_snapshot.side_effect = RuntimeError(
                "EXPLICIT_RECOVERY_REACHED_READ_ONLY_PREFLIGHT")
            env = {
                "HEPTA_LOCAL_AI_AUTH_GENERATION": "auth-generation-old",
                "HEPTA_LOCAL_AI_AUTH_PROFILE_ID": "openai:test-profile",
                "HEPTA_LOCAL_AI_CAMPAIGN_ID": "old-campaign",
                "HEPTA_LOCAL_AI_STRATEGY_ID": "strategy",
                "HEPTA_LOCAL_AI_STRATEGY_VERSION": "2",
                "HEPTA_LOCAL_AI_STRATEGY_SHA256": "sha256:" + "2" * 64,
            }
            with mock.patch.object(repair, "AGENT_STATE", state_path), \
                    mock.patch.object(repair, "END_FLAT_RECEIPT_ROOT", root), \
                    mock.patch.object(
                        repair, "RISK_RECOVERY_LOCK",
                        root / "risk-recovery.lock"), \
                    mock.patch.object(
                        repair, "AUTOMATIC_RISK_ATTEMPT",
                        automatic_attempt), \
                    mock.patch.object(repair, "load_agent", return_value=agent), \
                    mock.patch.object(
                        repair, "_load_root_agent_state",
                        return_value=state), \
                    mock.patch.object(repair, "read_env", return_value=env), \
                    mock.patch.object(
                        repair, "_select_risk_recovery_session",
                        return_value=(root / "session.token", True)), \
                    mock.patch.object(
                        repair, "_campaign_session_token_paths",
                        return_value=[]), \
                    mock.patch.object(
                        repair, "agent_arguments", return_value=object()), \
                    mock.patch.object(
                        repair, "_risk_recovery_halt_campaign",
                        return_value="halt_confirmed"), \
                    mock.patch.object(
                        repair, "run", return_value=SimpleNamespace(
                            returncode=0, stdout="active\n", stderr="")), \
                    mock.patch.object(repair, "run_checked"), \
                    mock.patch.object(
                        repair.os, "fstat", return_value=self.root_metadata()), \
                    self.assertRaisesRegex(
                        RuntimeError,
                        "EXPLICIT_RECOVERY_REACHED_READ_ONLY_PREFLIGHT"):
                repair.risk_recover(automatic=False)

    def test_automatic_already_flat_recovery_does_not_consume_attempt(
            self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path = root / "state.json"
            state = self.incident_state()
            state.update({
                "recovery_complete": False,
                "recovery_phase": "REQUESTED",
                "pending_order_id": None,
                "incident_pending_order_id": None,
            })
            state_path.write_text(json.dumps(state), encoding="ascii")
            automatic_attempt = root / "automatic-risk-attempt.json"
            agent = mock.Mock()
            agent.SCHEMA = FakeAgent.SCHEMA
            agent.empty_state.return_value = FakeAgent.empty_state()
            agent.write_json.side_effect = FakeAgent.write_json
            agent.active_orders.return_value = []
            agent.orders_snapshot.return_value = self.owner_orders_snapshot([])
            agent.validate_order_projection.return_value = {
                "connection_epoch": 1,
                "generation": 1,
                "owner_scope": {
                    "agent_id": "paper-agent",
                    "session_id": "paper-session",
                    "execution_domain": "alpha",
                    "account": "DU123",
                },
                "global_active_order_ids": (),
                "owned_active_order_ids": (),
            }
            agent._quantity_equal.side_effect = (
                lambda left, right: float(left) == float(right))
            env = {
                "HEPTA_LOCAL_AI_AUTH_GENERATION": "auth-generation-old",
                "HEPTA_LOCAL_AI_AUTH_PROFILE_ID": "openai:test-profile",
                "HEPTA_LOCAL_AI_CAMPAIGN_ID": "old-campaign",
                "HEPTA_LOCAL_AI_STRATEGY_ID": "strategy",
                "HEPTA_LOCAL_AI_STRATEGY_VERSION": "2",
                "HEPTA_LOCAL_AI_STRATEGY_SHA256": "sha256:" + "2" * 64,
            }

            def persist_checkpoint(
                    checkpoint: dict[str, object], *, create: bool = False,
            ) -> None:
                path = repair._risk_recovery_checkpoint_path(
                    str(checkpoint["suspension_id"]))
                if create and path.exists():
                    raise FileExistsError(path)
                checkpoint["updated_at_ms"] = 1_786_000_000_001
                FakeAgent.write_json(path, checkpoint)

            session_descriptor = {
                "token_name": "local-paper.token",
                "token_sha256": "sha256:" + "7" * 64,
                "lease_generation": 9,
                "revoked": False,
                "revoke_retry_intent": False,
            }
            with mock.patch.multiple(
                    repair,
                    AGENT_STATE=state_path, END_FLAT_RECEIPT_ROOT=root,
                    RISK_RECOVERY_TOKEN_ROOT=root,
                    SESSION_AUTHORITY_ROOT=root / "authority",
                    RISK_RECOVERY_LOCK=root / "risk-recovery.lock",
                    AUTOMATIC_RISK_ATTEMPT=automatic_attempt,
                    load_agent=mock.Mock(return_value=agent),
                    _load_root_agent_state=mock.Mock(return_value=state),
                    read_env=mock.Mock(return_value=env),
                    _select_risk_recovery_session=mock.Mock(return_value=(
                        root / "local-paper.token", True)),
                    _campaign_session_token_paths=mock.Mock(return_value=[]),
                    agent_arguments=mock.Mock(return_value=object()),
                    _risk_recovery_halt_campaign=mock.Mock(
                        return_value="halt_confirmed"),
                    authoritative_state=mock.Mock(
                        return_value=(0.0, 10, 20)),
                    _end_flat_authoritative_proof=mock.Mock(
                        side_effect=[(11, 21), (12, 22)]),
                    _risk_recovery_session_descriptors_before_revoke=
                        mock.Mock(return_value=[session_descriptor]),
                    _risk_recovery_policy_sha256=mock.Mock(
                        return_value="sha256:" + "8" * 64),
                    _persist_risk_recovery_checkpoint=mock.Mock(
                        side_effect=persist_checkpoint),
                    _revoke_recovery_session=mock.Mock(return_value={
                            "tool_session_revoked": True,
                            "tool_session_lease_generation": 9,
                            "tool_session_token_sha256":
                                "sha256:" + "7" * 64,
                        }),
                    run=mock.Mock(return_value=SimpleNamespace(
                        returncode=0, stdout="active\n", stderr="")),
                    run_checked=mock.Mock()), \
                    mock.patch.multiple(
                        repair.os,
                        fstat=mock.Mock(return_value=self.root_metadata()),
                        fchown=mock.DEFAULT,
                        chown=mock.DEFAULT,
                        chmod=mock.DEFAULT), \
                    mock.patch.object(repair.time, "sleep"):
                repair.risk_recover(automatic=True)
            self.assertFalse(automatic_attempt.exists())
            self.assertTrue(json.loads(
                state_path.read_text(encoding="ascii"))["recovery_complete"])

    def test_risk_recovery_stops_async_authority_units_before_runtime(
            self) -> None:
        events: list[object] = []
        expected_stop = [
            "/usr/bin/systemctl", "stop",
            repair.SESSION_RENEW_TIMER,
            repair.SESSION_RENEW_SERVICE,
            repair.SUPERVISOR_TIMER,
            repair.SUPERVISOR_SERVICE,
            repair.AGENT_SERVICE,
        ]

        def checked(command: list[str], timeout: int = 30) -> str:
            events.append((list(command), timeout))
            return ""

        def runtime() -> None:
            events.append("runtime")
            raise RuntimeError("RUNTIME_TEST_STOP")

        with mock.patch.object(repair, "run_checked", side_effect=checked), \
                mock.patch.object(
                    repair, "_ensure_risk_recovery_runtime",
                    side_effect=runtime), \
                self.assertRaisesRegex(RuntimeError, "RUNTIME_TEST_STOP"):
            repair._risk_recover_locked()

        self.assertEqual(events, [(expected_stop, 30), "runtime"])
        self.assertNotIn(repair.SAFE_RECOVERY_TIMER, expected_stop)
        self.assertNotIn(repair.SAFE_RECOVERY_SERVICE, expected_stop)

    def test_pending_command_owner_conflict_fails_before_any_query_or_write(
            self) -> None:
        token_sha256 = "sha256:" + "a" * 64
        first = {
            "pending_mutation_kind": "PLACE_ORDER",
            "pending_mutation_command_id": "entry-command-1001",
            "pending_mutation_recorded_at_ms": 1_786_000_000_000,
            "pending_mutation_token_name": "local-paper.token",
            "pending_mutation_token_sha256": token_sha256,
        }
        second = {
            "pending_mutation_kind": "FLATTEN_POSITION",
            "pending_mutation_command_id": "flatten-command-1002",
            "pending_mutation_recorded_at_ms": 1_786_000_000_001,
            "pending_mutation_token_name": "local-paper.token",
            "pending_mutation_token_sha256": token_sha256,
        }
        token_lookup = mock.Mock()
        arguments = mock.Mock()
        query = mock.Mock()
        write = mock.Mock()

        with mock.patch.multiple(
                repair,
                _pending_mutation_token_file=token_lookup,
                agent_arguments=arguments,
                _query_pending_mutation_status=query,
                _write_root_json=write), \
                self.assertRaisesRegex(
                    RuntimeError,
                    "TEST_COMMAND_OWNER_MULTIPLE_PENDING_MUTATIONS"):
            repair._reconcile_pending_mutation_records(
                object(), [(Path("first"), first), (Path("second"), second)],
                "TEST")

        token_lookup.assert_not_called()
        arguments.assert_not_called()
        query.assert_not_called()
        write.assert_not_called()

    def test_pending_command_token_digest_conflict_fails_full_set_prevalidation(
            self) -> None:
        first = {
            "pending_mutation_kind": "PLACE_ORDER",
            "pending_mutation_command_id": "entry-command-1101",
            "pending_mutation_recorded_at_ms": 1_786_000_000_000,
            "pending_mutation_token_name": "local-paper.token",
            "pending_mutation_token_sha256": "sha256:" + "a" * 64,
            "pending_order_id": None,
        }
        second = dict(first)
        second["pending_mutation_token_sha256"] = "sha256:" + "b" * 64
        token_lookup = mock.Mock()
        query = mock.Mock()
        write = mock.Mock()
        with mock.patch.multiple(
                repair,
                _pending_mutation_token_file=token_lookup,
                _query_pending_mutation_status=query,
                _write_root_json=write), self.assertRaisesRegex(
                    RuntimeError, "TEST_COMMAND_OWNER_TOKEN_SHA_CONFLICT"):
            repair._reconcile_pending_mutation_records(
                object(), [(Path("first"), first), (Path("second"), second)],
                "TEST")
        token_lookup.assert_not_called()
        query.assert_not_called()
        write.assert_not_called()

    def test_pending_command_duplicate_lineage_and_projection_must_match(
            self) -> None:
        base = {
            "pending_mutation_kind": "PLACE_ORDER",
            "pending_mutation_command_id": "entry-command-1102",
            "pending_mutation_recorded_at_ms": 1_786_000_000_000,
            "pending_mutation_token_name": "local-paper.token",
            "pending_mutation_token_sha256": "sha256:" + "a" * 64,
            "pending_order_id": None,
        }
        cases = []
        recorded_drift = dict(base)
        recorded_drift["pending_mutation_recorded_at_ms"] += 1
        cases.append((
            "recorded-at", recorded_drift,
            "TEST_COMMAND_OWNER_MULTIPLE_PENDING_MUTATIONS"))
        pending_drift = dict(base)
        pending_drift["pending_order_id"] = 41
        cases.append((
            "pending-order", pending_drift,
            "TEST_COMMAND_OWNER_DUPLICATE_PROJECTION_CONFLICT"))
        invalid_bool = dict(base)
        invalid_bool["pending_order_id"] = True
        cases.append((
            "pending-order-bool", invalid_bool,
            "TEST_MUTATION_PENDING_ORDER_ID_INVALID"))
        for label, second, failure in cases:
            token_lookup = mock.Mock()
            query = mock.Mock()
            write = mock.Mock()
            with self.subTest(label=label), mock.patch.multiple(
                    repair,
                    _pending_mutation_token_file=token_lookup,
                    _query_pending_mutation_status=query,
                    _write_root_json=write), self.assertRaisesRegex(
                        RuntimeError, failure):
                repair._reconcile_pending_mutation_records(
                    object(),
                    [(Path("first"), dict(base)), (Path("second"), second)],
                    "TEST")
            token_lookup.assert_not_called()
            query.assert_not_called()
            write.assert_not_called()

    def test_pending_command_queries_full_set_before_any_projection_write(
            self) -> None:
        def state(token_name: str, digest: str, command: str) \
                -> dict[str, object]:
            return {
                "pending_mutation_kind": "PLACE_ORDER",
                "pending_mutation_command_id": command,
                "pending_mutation_recorded_at_ms": 1_786_000_000_000,
                "pending_mutation_token_name": token_name,
                "pending_mutation_token_sha256": "sha256:" + digest * 64,
                "pending_order_id": None,
            }

        first = state("local-paper.token", "a", "entry-command-1103")
        second = state(
            "risk-recovery-0123456789abcdef01234567.token", "b",
            "entry-command-1104")
        status = {
            "command_status": "rejected", "order_id": -1,
            "reason_code": "EXECUTION_COMMAND_NOT_FOUND",
        }
        query = mock.Mock(side_effect=[status, RuntimeError("OWNER_UNAVAILABLE")])
        apply_status = mock.Mock()
        write = mock.Mock()
        with mock.patch.multiple(
                repair,
                _pending_mutation_token_file=mock.Mock(
                    side_effect=lambda value, _failure: Path(
                        str(value["pending_mutation_token_name"]))),
                agent_arguments=mock.Mock(return_value=object()),
                _query_pending_mutation_status=query,
                _apply_pending_mutation_status=apply_status,
                _write_root_json=write), self.assertRaisesRegex(
                    RuntimeError, "OWNER_UNAVAILABLE"):
            repair._reconcile_pending_mutation_records(
                object(), [(Path("first"), first), (Path("second"), second)],
                "TEST")
        self.assertEqual(query.call_count, 2)
        apply_status.assert_not_called()
        write.assert_not_called()

    def test_pending_command_projects_all_duplicates_before_first_write(
            self) -> None:
        base = {
            "pending_mutation_kind": "PLACE_ORDER",
            "pending_mutation_command_id": "entry-command-1105",
            "pending_mutation_recorded_at_ms": 1_786_000_000_000,
            "pending_mutation_token_name": "local-paper.token",
            "pending_mutation_token_sha256": "sha256:" + "a" * 64,
            "pending_order_id": 42,
        }
        first = dict(base)
        second = dict(base)
        status = {
            "command_status": "accepted", "order_id": 41,
            "reason_code": "ORDER_ACCEPTED",
        }
        write = mock.Mock()
        with mock.patch.multiple(
                repair,
                _pending_mutation_token_file=mock.Mock(
                    return_value=Path("local-paper.token")),
                agent_arguments=mock.Mock(return_value=object()),
                _query_pending_mutation_status=mock.Mock(return_value=status),
                _write_root_json=write), self.assertRaisesRegex(
                    RuntimeError, "PENDING_MUTATION_ORDER_ID_CONFLICT"):
            repair._reconcile_pending_mutation_records(
                object(), [(Path("first"), first), (Path("second"), second)],
                "TEST")
        self.assertEqual(first, base)
        self.assertEqual(second, base)
        write.assert_not_called()

    def test_pending_command_status_rejects_order_id_conflicts_before_mutation(
            self) -> None:
        cases = ((
            "accepted-different", 42,
            {"command_status": "accepted", "order_id": 41,
             "reason_code": "ORDER_ACCEPTED"},
        ), (
            "accepted-bool", True,
            {"command_status": "accepted", "order_id": 1,
             "reason_code": "ORDER_ACCEPTED"},
        ), (
            "rejected-existing", 41,
            {"command_status": "rejected", "order_id": -1,
             "reason_code": "ORDER_REJECTED"},
        ), (
            "flat-noop-existing", 41,
            {"command_status": "accepted", "order_id": -1,
             "reason_code": "POSITION_ALREADY_FLAT"},
        ))
        for label, existing_order_id, status in cases:
            state = {
                "pending_order_id": existing_order_id,
                "pending_order_since_ms": 123,
                "pending_mutation_kind": "PLACE_ORDER",
                "pending_mutation_command_id": "entry-command-1003",
            }
            original = json.loads(json.dumps(state))
            with self.subTest(label=label), self.assertRaisesRegex(
                    RuntimeError, "PENDING_MUTATION_ORDER_ID_CONFLICT"):
                repair._apply_pending_mutation_status(state, status)
            self.assertEqual(state, original)

    def test_risk_recovery_multiple_active_owners_persists_end_flat_request(
            self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            selected = Path("/run/hepta/local-paper.token")
            foreign = Path(
                "/run/hepta/risk-recovery-0123456789abcdef01234567.token")
            agent = mock.Mock()
            agent.empty_state.return_value = {}
            agent_arguments = mock.Mock()
            halt = mock.Mock()

            def root_lstat(path: object) -> SimpleNamespace:
                metadata = REAL_LSTAT(path)
                return SimpleNamespace(
                    st_mode=metadata.st_mode, st_nlink=metadata.st_nlink,
                    st_uid=0, st_gid=0, st_size=metadata.st_size)

            with mock.patch.multiple(
                    repair,
                    END_FLAT_RECEIPT_ROOT=root,
                    run_checked=mock.Mock(),
                    _ensure_risk_recovery_runtime=mock.Mock(),
                    load_agent=mock.Mock(return_value=agent),
                    _load_root_agent_state=mock.Mock(return_value={
                        "suspension_id": "suspension-test",
                    }),
                    read_env=mock.Mock(return_value={
                        "HEPTA_LOCAL_AI_CAMPAIGN_ID": "campaign-current",
                    }),
                    _ensure_suspension_metadata=mock.Mock(),
                    _reconcile_pending_mutation_records=mock.Mock(
                        return_value=([], {}, [])),
                    _select_risk_recovery_session=mock.Mock(
                        return_value=(selected, True)),
                    _campaign_session_token_paths=mock.Mock(
                        return_value=[selected, foreign]),
                    _load_session_provision_intent=mock.Mock(
                        return_value={"phase": "ACTIVE"}),
                    session_usable=mock.Mock(return_value=True),
                    agent_arguments=agent_arguments,
                    _risk_recovery_halt_campaign=halt), \
                    mock.patch.object(
                        repair.os, "lstat", side_effect=root_lstat), \
                    mock.patch.object(repair.os, "fchown"), \
                    self.assertRaisesRegex(
                        RuntimeError,
                        "RISK_RECOVERY_MULTIPLE_SESSION_OWNERS_REQUIRE_END_FLAT"):
                repair._risk_recover_locked()

            request = root / "end-flat-campaign-current.requested.json"
            persisted = json.loads(request.read_text(encoding="ascii"))
            self.assertEqual(persisted["campaign_id"], "campaign-current")
            self.assertEqual(
                persisted["schema"],
                "hepta.local-ai-paper-end-flat-request.v1")
            agent_arguments.assert_not_called()
            halt.assert_not_called()

    def test_risk_recovery_foreign_command_owner_persists_end_flat_request(
            self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            selected = Path("/run/hepta/local-paper.token")
            foreign_name = (
                "risk-recovery-0123456789abcdef01234567.token")
            agent = mock.Mock()
            agent.empty_state.return_value = {}
            halt = mock.Mock()

            def root_lstat(path: object) -> SimpleNamespace:
                metadata = REAL_LSTAT(path)
                return SimpleNamespace(
                    st_mode=metadata.st_mode, st_nlink=metadata.st_nlink,
                    st_uid=0, st_gid=0, st_size=metadata.st_size)

            with mock.patch.multiple(
                    repair,
                    END_FLAT_RECEIPT_ROOT=root,
                    run_checked=mock.Mock(),
                    _ensure_risk_recovery_runtime=mock.Mock(),
                    load_agent=mock.Mock(return_value=agent),
                    _load_root_agent_state=mock.Mock(return_value={
                        "suspension_id": "suspension-test",
                    }),
                    read_env=mock.Mock(return_value={
                        "HEPTA_LOCAL_AI_CAMPAIGN_ID": "campaign-current",
                    }),
                    _ensure_suspension_metadata=mock.Mock(),
                    _reconcile_pending_mutation_records=mock.Mock(
                        return_value=([], {foreign_name: {41}}, [])),
                    _select_risk_recovery_session=mock.Mock(
                        return_value=(selected, True)),
                    _campaign_session_token_paths=mock.Mock(
                        return_value=[selected]),
                    _load_session_provision_intent=mock.Mock(
                        return_value={"phase": "ACTIVE"}),
                    session_usable=mock.Mock(return_value=True),
                    agent_arguments=mock.Mock(return_value=object()),
                    _risk_recovery_halt_campaign=halt), \
                    mock.patch.object(
                        repair.os, "lstat", side_effect=root_lstat), \
                    mock.patch.object(repair.os, "fchown"), \
                    self.assertRaisesRegex(
                        RuntimeError,
                        "RISK_RECOVERY_MULTIPLE_COMMAND_OWNERS_REQUIRE_END_FLAT"):
                repair._risk_recover_locked()

            request = root / "end-flat-campaign-current.requested.json"
            persisted = json.loads(request.read_text(encoding="ascii"))
            self.assertEqual(persisted["campaign_id"], "campaign-current")
            halt.assert_not_called()

    def test_risk_recovery_never_infers_terminal_from_order_disappearance(
            self) -> None:
        agent = SimpleNamespace(
            INSTRUMENT="EUR.USD",
            TERMINAL_NON_FILL_STATUSES=frozenset({
                "rejected", "inactive", "cancelled", "apicancelled",
            }),
            orders_snapshot=lambda *_args, **_kwargs:
                self.owner_orders_snapshot([]),
        )
        with self.assertRaisesRegex(
                RuntimeError,
                "RISK_RECOVERY_ORDER_TERMINAL_EVIDENCE_MISSING:41"):
            repair._risk_recovery_terminal_order_proof(
                agent, SimpleNamespace(), [41])

    def test_pending_command_without_order_id_queries_exact_owner_and_maps_order(
            self) -> None:
        token_sha256 = "sha256:" + "a" * 64
        state: dict[str, object] = {
            "pending_order_id": None,
            "pending_mutation_kind": "PLACE_ORDER",
            "pending_mutation_command_id": "entry-command-0001",
            "pending_mutation_recorded_at_ms": 1_786_000_000_000,
            "pending_mutation_token_name": "local-paper.token",
            "pending_mutation_token_sha256": token_sha256,
        }
        arguments = SimpleNamespace(
            campaign_id="campaign-test",
            token_file=Path("/run/hepta/local-paper.token"),
        )
        authority = {
            "phase": "ACTIVE", "token_sha256": token_sha256,
            "lease_generation": 7,
        }
        bearer = Path("/var/lib/hepta-custodian/local-paper.revoke-token")
        run_query = mock.Mock(return_value=SimpleNamespace(
            returncode=0, stderr="", stdout=json.dumps({
                "accepted": True,
                "reason_code": "RECOVERY_QUERY_CANNOT_FULL_FENCE",
                "lease_generation": 7,
                "authoritative_command_status": True,
                "command_id": "entry-command-0001",
                "command_status": "accepted",
                "order_id": 41,
                "command_reason_code": "ORDER_ACCEPTED",
                "recovery_only": True,
                "paper_finalization_required": False,
                "owner_fenced": False,
                "execution_service_epoch": "hexec-v6-" + "1" * 32,
                "execution_service_fencing_generation": 9,
            })))

        with mock.patch.multiple(
                repair,
                RISK_RECOVERY_TOKEN_ROOT=Path("/run/hepta"),
                _load_session_provision_intent=mock.Mock(
                    return_value=authority),
                session_authority_bearer_path=mock.Mock(
                    return_value=bearer),
                run=run_query):
            status = repair._query_pending_mutation_status(
                object(), arguments, state, "TEST")
        self.assertIsNotNone(status)
        assert status is not None
        order_id = repair._apply_pending_mutation_status(state, status)

        self.assertEqual(order_id, 41)
        self.assertEqual(state["pending_order_id"], 41)
        self.assertEqual(state["incident_pending_order_id"], 41)
        self.assertEqual(
            state["pending_mutation_command_id"], "entry-command-0001")
        self.assertEqual(status["tool_session_lease_generation"], 7)
        run_query.assert_called_once_with([
            "/usr/bin/hepta-sessionctl", "--socket",
            repair.SUPERVISOR_SOCKET, "recovery-query",
            "--token-file", str(bearer), "--generation", "7",
            "--command-id", "entry-command-0001",
            "--token-owner-uid", "0",
        ], timeout=15)

    def test_pending_command_requires_per_owner_arguments(self) -> None:
        local_sha256 = "sha256:" + "a" * 64
        recovery_sha256 = "sha256:" + "b" * 64
        local_state = {
            "pending_mutation_kind": "PLACE_ORDER",
            "pending_mutation_command_id": "local-command-0001",
            "pending_mutation_recorded_at_ms": 1_786_000_000_000,
            "pending_mutation_token_name": "local-paper.token",
            "pending_mutation_token_sha256": local_sha256,
        }
        recovery_state = {
            "pending_mutation_kind": "FLATTEN_POSITION",
            "pending_mutation_command_id": "risk-command-0001",
            "pending_mutation_recorded_at_ms": 1_786_000_000_001,
            "pending_mutation_token_name":
                "risk-recovery-0123456789abcdef01234567.token",
            "pending_mutation_token_sha256": recovery_sha256,
        }
        local_arguments = SimpleNamespace(
            campaign_id="campaign-test",
            token_file=Path("/run/hepta/local-paper.token"),
        )
        recovery_arguments = SimpleNamespace(
            campaign_id="campaign-test",
            token_file=Path(
                "/run/hepta/risk-recovery-0123456789abcdef01234567.token"),
        )

        authority = {
            "phase": "ACTIVE", "token_sha256": recovery_sha256,
            "lease_generation": 11,
        }
        bearer = Path("/var/lib/hepta-custodian/risk.revoke-token")
        run_query = mock.Mock(return_value=SimpleNamespace(
            returncode=0, stderr="", stdout=json.dumps({
                "accepted": True,
                "reason_code": "RECOVERY_QUERY_PROVEN_RECOVERY_ONLY",
                "lease_generation": 11,
                "authoritative_command_status": True,
                "command_id": "risk-command-0001",
                "command_status": "accepted",
                "order_id": -1,
                "command_reason_code": "POSITION_ALREADY_FLAT",
                "recovery_only": True,
                "owner_fenced": False,
                "execution_service_epoch": "hexec-v6-" + "1" * 32,
                "execution_service_fencing_generation": 9,
            })))

        with self.assertRaisesRegex(
                RuntimeError, "TEST_COMMAND_OWNER_MISMATCH"):
            repair._query_pending_mutation_status(
                object(), local_arguments, recovery_state, "TEST")
        run_query.assert_not_called()

        with mock.patch.multiple(
                repair,
                RISK_RECOVERY_TOKEN_ROOT=Path("/run/hepta"),
                _load_session_provision_intent=mock.Mock(
                    return_value=authority),
                session_authority_bearer_path=mock.Mock(
                    return_value=bearer),
                run=run_query):
            status = repair._query_pending_mutation_status(
                object(), recovery_arguments, recovery_state, "TEST")
        self.assertEqual(status["command_id"], "risk-command-0001")
        self.assertEqual(status["reason_code"], "POSITION_ALREADY_FLAT")
        with self.assertRaisesRegex(
                RuntimeError, "TEST_COMMAND_OWNER_MISMATCH"):
            repair._query_pending_mutation_status(
                object(), recovery_arguments, local_state, "TEST")
        self.assertEqual(run_query.call_count, 1)

    def test_command_status_unavailable_preserves_pending_identity(self) -> None:
        token_sha256 = "sha256:" + "a" * 64
        state: dict[str, object] = {
            "pending_order_id": None,
            "pending_mutation_kind": "PLACE_ORDER",
            "pending_mutation_command_id": "entry-command-0002",
            "pending_mutation_recorded_at_ms": 1_786_000_000_000,
            "pending_mutation_token_name": "local-paper.token",
            "pending_mutation_token_sha256": token_sha256,
        }
        original = json.loads(json.dumps(state))
        arguments = SimpleNamespace(
            campaign_id="campaign-test",
            token_file=Path("/run/hepta/local-paper.token"),
        )
        authority = {
            "phase": "ACTIVE", "token_sha256": token_sha256,
            "lease_generation": 7,
        }
        run_query = mock.Mock(return_value=SimpleNamespace(
            returncode=3, stdout="", stderr="transport unavailable"))

        with mock.patch.multiple(
                repair,
                RISK_RECOVERY_TOKEN_ROOT=Path("/run/hepta"),
                _load_session_provision_intent=mock.Mock(
                    return_value=authority),
                session_authority_bearer_path=mock.Mock(
                    return_value=Path("/var/lib/hepta-custodian/revoke.token")),
                run=run_query), \
                self.assertRaisesRegex(
                    RuntimeError, "TEST_COMMAND_STATUS_UNAVAILABLE"):
            repair._query_pending_mutation_status(
                object(), arguments, state, "TEST")

        self.assertEqual(state, original)
        self.assertEqual(
            state["pending_mutation_command_id"], "entry-command-0002")

    def test_root_recovery_query_not_found_proves_rejection_and_clears(
            self) -> None:
        token_sha256 = "sha256:" + "a" * 64
        state: dict[str, object] = {
            "pending_order_id": None,
            "pending_mutation_kind": "PLACE_ORDER",
            "pending_mutation_command_id": "entry-command-0003",
            "pending_mutation_recorded_at_ms": 1_786_000_000_000,
            "pending_mutation_token_name": "local-paper.token",
            "pending_mutation_token_sha256": token_sha256,
        }
        arguments = SimpleNamespace(
            campaign_id="campaign-test",
            token_file=Path("/run/hepta/local-paper.token"),
        )
        response = {
            "accepted": True,
            "reason_code":
                "RECOVERY_QUERY_NOT_FOUND_PROVEN_RECOVERY_ONLY",
            "lease_generation": 7,
            "authoritative_command_status": True,
            "command_id": "entry-command-0003",
            "command_status": "not_found",
            "order_id": -1,
            "command_reason_code": "EXECUTION_COMMAND_NOT_FOUND",
            "recovery_only": True,
            "owner_fenced": False,
            "execution_service_epoch": "hexec-v6-" + "1" * 32,
            "execution_service_fencing_generation": 9,
        }
        with mock.patch.multiple(
                repair,
                RISK_RECOVERY_TOKEN_ROOT=Path("/run/hepta"),
                _load_session_provision_intent=mock.Mock(return_value={
                    "phase": "ACTIVE", "token_sha256": token_sha256,
                    "lease_generation": 7,
                }),
                session_authority_bearer_path=mock.Mock(
                    return_value=Path("/var/lib/hepta-custodian/revoke.token")),
                run=mock.Mock(return_value=SimpleNamespace(
                    returncode=0, stderr="", stdout=json.dumps(response)))):
            status = repair._query_pending_mutation_status(
                object(), arguments, state, "TEST")
        self.assertEqual(status["command_status"], "rejected")
        self.assertIsNone(
            repair._apply_pending_mutation_status(state, status))
        self.assertIsNone(state["pending_mutation_command_id"])
        self.assertEqual(state["last_order_result"],
                         "COMMAND_STATUS_REJECTED")

    def test_end_flat_command_status_unavailable_blocks_every_terminal_seal(
            self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path = root / "end-flat-campaign-test.state.json"
            state_path.write_text("{}\n", encoding="ascii")
            pending = {
                "pending_order_id": None,
                "pending_mutation_kind": "FLATTEN_POSITION",
                "pending_mutation_command_id": "flatten-command-0002",
                "pending_mutation_recorded_at_ms": 1_786_000_000_000,
                "pending_mutation_token_name": "local-paper.token",
                "pending_mutation_token_sha256": "sha256:" + "a" * 64,
            }
            agent = SimpleNamespace(
                flatten=mock.Mock(),
                write_json=mock.Mock(),
            )
            halt = mock.Mock()
            cancel = mock.Mock()
            cancel_one = mock.Mock()
            policy_seal = mock.Mock()
            session_revoke = mock.Mock()
            control_revoke = mock.Mock()
            retry_seal = mock.Mock()
            managed_contexts = mock.Mock()
            reconcile = mock.Mock(side_effect=RuntimeError(
                "END_FLAT_COMMAND_STATUS_UNAVAILABLE"))

            with contextlib.ExitStack() as stack:
                stack.enter_context(mock.patch.multiple(
                    repair,
                    END_FLAT_RECEIPT_ROOT=root,
                    END_FLAT_LOCK=root / "end-flat.lock",
                    CAMPAIGN_LIFECYCLE_LOCK=root / "lifecycle.lock",
                    RISK_RECOVERY_LOCK=root / "risk.lock"))
                stack.enter_context(mock.patch.multiple(
                    repair,
                    read_env=mock.Mock(return_value={
                        "HEPTA_LOCAL_AI_CAMPAIGN_ID": "campaign-test",
                    }),
                    run_checked=mock.Mock(),
                    _ensure_end_flat_request_marker=mock.Mock(),
                    _validated_end_flat_receipt=mock.Mock(return_value=None),
                    load_agent=mock.Mock(return_value=agent),
                    _load_end_flat_checkpoint=mock.Mock(return_value=None),
                    _ensure_end_flat_recovery_runtime=mock.Mock(),
                    _select_end_flat_session=mock.Mock(return_value=(
                        Path("/tmp/local-paper.token"), True)),
                    _end_flat_state_records=mock.Mock(return_value=[
                        (state_path, pending),
                    ]),
                    _reconcile_pending_mutation_records=reconcile,
                    _managed_session_contexts=managed_contexts,
                    _end_flat_halt_campaign=halt,
                    _cancel_all_managed_session_orders=cancel,
                    _end_flat_cancel_orders=cancel_one,
                    _end_flat_persist_policy_disabled=policy_seal,
                    _revoke_checkpoint_sessions=session_revoke,
                    _end_flat_revoke_local_paper_control=control_revoke,
                    _seal_end_flat_retry_timer=retry_seal,
                ))
                stack.enter_context(mock.patch.object(
                    repair.os, "fstat", return_value=self.root_metadata()))
                stack.enter_context(self.assertRaisesRegex(
                    RuntimeError, "END_FLAT_COMMAND_STATUS_UNAVAILABLE"))
                repair.end_flat()

            reconcile.assert_called_once_with(
                agent, [(state_path, pending)], "END_FLAT")
            managed_contexts.assert_not_called()
            halt.assert_not_called()
            cancel.assert_not_called()
            cancel_one.assert_not_called()
            agent.flatten.assert_not_called()
            policy_seal.assert_not_called()
            session_revoke.assert_not_called()
            control_revoke.assert_not_called()
            agent.write_json.assert_not_called()
            retry_seal.assert_not_called()
            self.assertFalse(
                (root / "end-flat-campaign-test.receipt.json").exists())

    def test_end_flat_read_failure_preserves_new_recovery_authority(
            self) -> None:
        """A pre-proof orders/read failure must not revoke unknown exposure."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path = root / "end-flat-campaign-test.state.json"
            (root / "agent-state.json").write_text("{}\n", encoding="ascii")
            recovery_token = Path(
                "/tmp/end-flat-" + "d" * 24 + ".token")
            agent = SimpleNamespace(load_state=mock.Mock(return_value={
                "pending_order_id": None,
                "active_order_ids": [],
            }))
            read_failure = RuntimeError("END_FLAT_ORDER_PROJECTION_READ_FAILED")
            resolver = mock.Mock()
            load_authority = mock.Mock(return_value={"phase": "ACTIVE"})
            real_lstat = repair.os.lstat

            def root_lstat(path: object) -> object:
                if Path(path) == root / "agent-state.json":
                    return self.root_metadata()
                return real_lstat(path)

            with contextlib.ExitStack() as stack:
                stack.enter_context(mock.patch.multiple(
                    repair,
                    TOKEN_FILE=Path("/tmp/local-paper.token"),
                    RISK_RECOVERY_TOKEN_ROOT=Path("/tmp"),
                    AGENT_STATE=root / "agent-state.json",
                    END_FLAT_RECEIPT_ROOT=root,
                    END_FLAT_LOCK=root / "end-flat.lock",
                    CAMPAIGN_LIFECYCLE_LOCK=root / "lifecycle.lock",
                    RISK_RECOVERY_LOCK=root / "risk.lock"))
                stack.enter_context(mock.patch.multiple(
                    repair,
                    read_env=mock.Mock(return_value={
                        "HEPTA_LOCAL_AI_CAMPAIGN_ID": "campaign-test",
                    }),
                    run_checked=mock.Mock(),
                    _ensure_end_flat_request_marker=mock.Mock(),
                    _validated_end_flat_receipt=mock.Mock(return_value=None),
                    load_agent=mock.Mock(return_value=agent),
                    _load_end_flat_checkpoint=mock.Mock(return_value=None),
                    _ensure_end_flat_recovery_runtime=mock.Mock(),
                    _select_end_flat_session=mock.Mock(return_value=(
                        recovery_token, False)),
                    _end_flat_state_records=mock.Mock(
                        side_effect=read_failure),
                    _load_session_provision_intent=load_authority,
                    _resolve_session_provision_intent=resolver,
                ))
                stack.enter_context(mock.patch.object(
                    repair.os, "lstat", side_effect=root_lstat))
                stack.enter_context(mock.patch.object(
                    repair.os, "fstat", return_value=self.root_metadata()))
                with self.assertRaisesRegex(
                        RuntimeError, "END_FLAT_ORDER_PROJECTION_READ_FAILED"):
                    repair.end_flat()

            load_authority.assert_called_once_with(recovery_token)
            resolver.assert_not_called()

    def test_end_flat_selection_failure_reverts_runtime_fail_closed(
            self) -> None:
        """A runtime opened for recovery cannot outlive owner selection."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_checked = mock.Mock()
            ensure_runtime = mock.Mock()
            selection_error = RuntimeError(
                "END_FLAT_SESSION_UNUSABLE_PRESERVED")
            revoke_control = mock.Mock(return_value={
                "identity_manifest_sha256": "sha256:" + "a" * 64,
                "identity_count": 0,
            })
            verify_deny_all = mock.Mock(return_value={
                "broker_policy_sha256": "sha256:" + "b" * 64,
                "authorized_connector_count": 0,
                "authorized_uids": [],
                "protected_port_count": 4,
            })
            verify_stopped = mock.Mock()
            with contextlib.ExitStack() as stack:
                stack.enter_context(mock.patch.multiple(
                    repair,
                    END_FLAT_RECEIPT_ROOT=root,
                    END_FLAT_LOCK=root / "end-flat.lock",
                    CAMPAIGN_LIFECYCLE_LOCK=root / "lifecycle.lock",
                    RISK_RECOVERY_LOCK=root / "risk.lock",
                    read_env=mock.Mock(return_value={
                        "HEPTA_LOCAL_AI_CAMPAIGN_ID": "campaign-test",
                    }),
                    _external_policy_for_dispatch=mock.Mock(
                        return_value=None),
                    _ensure_end_flat_request_marker=mock.Mock(),
                    _validated_end_flat_receipt=mock.Mock(return_value=None),
                    load_agent=mock.Mock(return_value=SimpleNamespace()),
                    _load_end_flat_checkpoint=mock.Mock(return_value=None),
                    _ensure_end_flat_recovery_runtime=ensure_runtime,
                    _select_end_flat_session=mock.Mock(
                        side_effect=selection_error),
                    run_checked=run_checked,
                    _end_flat_revoke_local_paper_control=revoke_control,
                    _end_flat_verify_deny_all=verify_deny_all,
                    _end_flat_verify_runtime_stopped=verify_stopped,
                ))
                stack.enter_context(mock.patch.object(
                    repair.os, "fstat", return_value=self.root_metadata()))
                with self.assertRaisesRegex(
                        RuntimeError, "END_FLAT_SESSION_UNUSABLE_PRESERVED"):
                    repair.end_flat()

            ensure_runtime.assert_called_once_with("campaign-test")
            revoke_control.assert_called_once_with()
            verify_deny_all.assert_called_once_with()
            verify_stopped.assert_called_once_with()
            self.assertEqual(run_checked.call_count, 2)
            self.assertEqual(run_checked.call_args_list[1], mock.call([
                "/usr/bin/systemctl", "stop",
                *repair.END_FLAT_EXECUTION_UNITS,
                *repair.END_FLAT_TOOL_UNITS,
            ], timeout=60))
            self.assertFalse(
                (root / "end-flat-campaign-test.receipt.json").exists())

    def test_end_flat_cancel_requires_terminal_proof_after_disappearance(
            self) -> None:
        agent = SimpleNamespace(
            INSTRUMENT="EUR.USD",
            orders_snapshot=mock.Mock(side_effect=[
                self.owner_orders_snapshot([41]),
                self.owner_orders_snapshot([]),
            ]),
            tool_response=mock.Mock(return_value={"status": "ok"}),
        )
        terminal_proof = mock.Mock(return_value=[41, 42])
        with mock.patch.object(
                repair, "_risk_recovery_terminal_order_proof",
                terminal_proof), \
                mock.patch.object(repair.time, "sleep"):
            cancelled, proven = repair._end_flat_cancel_orders(
                agent, object(), terminal_order_ids=(42,))

        self.assertEqual(cancelled, [41])
        self.assertEqual(proven, [41, 42])
        terminal_proof.assert_called_once_with(
            agent, mock.ANY, [41, 42], failure_prefix="END_FLAT")
        agent.tool_response.assert_called_once_with(
            mock.ANY, "trade.cancel_order", {"order_id": 41},
            "end-flat-cancel-41", timeout=5)

    def test_end_flat_cancel_does_not_accept_disappearance_without_proof(
            self) -> None:
        agent = SimpleNamespace(
            INSTRUMENT="EUR.USD",
            orders_snapshot=mock.Mock(side_effect=[
                self.owner_orders_snapshot([41]),
                self.owner_orders_snapshot([]),
            ]),
            tool_response=mock.Mock(return_value={"status": "ok"}),
        )
        with mock.patch.object(
                repair, "_risk_recovery_terminal_order_proof",
                side_effect=RuntimeError(
                    "RISK_RECOVERY_ORDER_TERMINAL_EVIDENCE_MISSING:41")), \
                mock.patch.object(repair.time, "sleep"), \
                self.assertRaisesRegex(
                    RuntimeError,
                    "RISK_RECOVERY_ORDER_TERMINAL_EVIDENCE_MISSING:41"):
            repair._end_flat_cancel_orders(agent, object())

    def test_end_flat_reconciles_each_session_owner_independently(self) -> None:
        local_arguments = object()
        recovery_arguments = object()
        contexts = {
            "local-paper.token": (
                Path("/run/hepta/local-paper.token"), local_arguments),
            "risk-recovery-0123456789abcdef01234567.token": (
                Path(
                    "/run/hepta/"
                    "risk-recovery-0123456789abcdef01234567.token"),
                recovery_arguments),
        }
        terminal_targets = {
            "local-paper.token": {41},
            "risk-recovery-0123456789abcdef01234567.token": {42},
        }
        snapshots = iter([
            self.owner_orders_snapshot(
                [43], [43], session_id="session-local"),
            self.owner_orders_snapshot(
                [43], [], session_id="session-recovery"),
            self.owner_orders_snapshot([], [], session_id="session-local"),
            self.owner_orders_snapshot([], [], session_id="session-recovery"),
        ])
        agent = SimpleNamespace(
            orders_snapshot=mock.Mock(side_effect=lambda *_a, **_k: next(
                snapshots)),
            tool_response=mock.Mock(return_value={"status": "ok"}),
        )
        terminal = mock.Mock(side_effect=[[41, 43], [42]])
        with mock.patch.object(
                repair, "_risk_recovery_terminal_order_proof", terminal), \
                mock.patch.object(repair.time, "sleep"):
            cancelled, proven = repair._cancel_all_managed_session_orders(
                agent, contexts, terminal_targets)

        self.assertEqual(cancelled, {
            "local-paper.token": [43],
            "risk-recovery-0123456789abcdef01234567.token": [],
        })
        self.assertEqual(proven, {
            "local-paper.token": {41, 43},
            "risk-recovery-0123456789abcdef01234567.token": {42},
        })
        self.assertEqual(agent.tool_response.call_args_list, [
            mock.call(
                local_arguments, "trade.cancel_order", {"order_id": 43},
                "end-flat-cancel-43", timeout=5),
        ])

    def test_end_flat_refuses_terminal_target_without_owner_session(
            self) -> None:
        with self.assertRaisesRegex(
                RuntimeError, "END_FLAT_COMMAND_OWNER_SESSION_MISSING"):
            repair._cancel_all_managed_session_orders(
                object(), {}, {"local-paper.token": {41}})

    def test_load_end_flat_state_prefers_existing_durable_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "end-flat-campaign.state.json"
            state_path.write_text("{}\n", encoding="ascii")
            durable = {
                "pending_order_id": None,
                "pending_mutation_kind": "FLATTEN_POSITION",
                "pending_mutation_command_id": "flatten-command-0001",
                "pending_mutation_recorded_at_ms": 1_786_000_000_000,
                "pending_mutation_token_name": "local-paper.token",
                "pending_mutation_token_sha256": "sha256:" + "a" * 64,
            }
            agent = SimpleNamespace(
                load_state=mock.Mock(return_value=durable),
                empty_state=mock.Mock(return_value={"fresh": True}),
            )
            with mock.patch.object(
                    repair.os, "lstat", return_value=self.root_metadata()):
                loaded = repair._load_end_flat_state(agent, state_path)

        self.assertIs(loaded, durable)
        agent.load_state.assert_called_once_with(state_path)
        agent.empty_state.assert_not_called()

    def test_load_end_flat_state_uses_empty_only_when_absent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "end-flat-campaign.state.json"
            fresh = {"fresh": True}
            agent = SimpleNamespace(
                load_state=mock.Mock(),
                empty_state=mock.Mock(return_value=fresh),
            )
            loaded = repair._load_end_flat_state(agent, state_path)

        self.assertIs(loaded, fresh)
        agent.load_state.assert_not_called()
        agent.empty_state.assert_called_once_with()

    def test_end_flat_scans_only_exact_current_risk_recovery_binding(
            self) -> None:
        suspension_id = "suspension-current-binding"
        digest = hashlib.sha256(
            suspension_id.encode("utf-8")).hexdigest()[:24]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            risk_path = root / f"risk-recovery-{digest}.state.json"
            risk_path.write_text("{}\n", encoding="ascii")
            end_state = root / "end-flat-campaign-current.state.json"
            current = {
                "schema": FakeAgent.SCHEMA,
                "campaign_id_at_suspend": "campaign-current",
                "suspension_id": suspension_id,
                "pending_order_id": None,
                "pending_mutation_kind": None,
                "pending_mutation_command_id": None,
                "pending_mutation_recorded_at_ms": None,
                "pending_mutation_token_name": None,
                "pending_mutation_token_sha256": None,
            }

            def load(_agent: object, path: Path) -> dict[str, object]:
                return dict(current) if path == risk_path else {}

            with mock.patch.multiple(
                    repair,
                    END_FLAT_RECEIPT_ROOT=root,
                    AGENT_STATE=root / "agent-state.json",
                    STRATEGY_ACCEPTANCE_STATE=root / "acceptance-state.json",
                    _load_end_flat_state=mock.Mock(side_effect=load)):
                records = repair._end_flat_state_records(
                    object(), "campaign-current", end_state)
        self.assertEqual(records[0][0], risk_path)
        self.assertEqual(records[0][1]["suspension_id"], suspension_id)

    def test_end_flat_foreign_or_ambiguous_risk_state_fails_before_query_set(
            self) -> None:
        suspension_id = "suspension-historical-binding"
        digest = hashlib.sha256(
            suspension_id.encode("utf-8")).hexdigest()[:24]
        cases = ((
            "foreign", {
                "schema": FakeAgent.SCHEMA,
                "campaign_id_at_suspend": "campaign-old",
                "suspension_id": suspension_id,
            }, "END_FLAT_FOREIGN_RISK_RECOVERY_STATE_PRESENT",
        ), (
            "ambiguous", {
                "schema": FakeAgent.SCHEMA,
                "campaign_id_at_suspend": None,
                "suspension_id": suspension_id,
            }, "END_FLAT_RISK_RECOVERY_OWNERSHIP_AMBIGUOUS",
        ))
        for label, risk_state, failure in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                risk_path = root / f"risk-recovery-{digest}.state.json"
                risk_path.write_text("{}\n", encoding="ascii")
                end_state = root / "end-flat-campaign-current.state.json"

                def load(_agent: object, path: Path) -> dict[str, object]:
                    return dict(risk_state) if path == risk_path else {}

                with mock.patch.multiple(
                        repair,
                        END_FLAT_RECEIPT_ROOT=root,
                        AGENT_STATE=root / "agent-state.json",
                        STRATEGY_ACCEPTANCE_STATE=(
                            root / "acceptance-state.json"),
                        _load_end_flat_state=mock.Mock(side_effect=load)), \
                        self.assertRaisesRegex(RuntimeError, failure):
                    repair._end_flat_state_records(
                        object(), "campaign-current", end_state)
                self.assertTrue(risk_path.exists())

    def test_risk_recovery_state_rejects_cross_session_lineage(self) -> None:
        suspension_id = "suspension-session-binding"
        digest = hashlib.sha256(
            suspension_id.encode("utf-8")).hexdigest()[:24]
        state = {
            "schema": FakeAgent.SCHEMA,
            "campaign_id_at_suspend": "campaign-current",
            "suspension_id": suspension_id,
            "pending_order_id": None,
            "pending_mutation_kind": "FLATTEN_POSITION",
            "pending_mutation_command_id": "flatten-command-2201",
            "pending_mutation_recorded_at_ms": 1_786_000_000_000,
            "pending_mutation_token_name":
                "risk-recovery-ffffffffffffffffffffffff.token",
            "pending_mutation_token_sha256": "sha256:" + "a" * 64,
        }
        with self.assertRaisesRegex(
                RuntimeError,
                "END_FLAT_RISK_RECOVERY_SESSION_LINEAGE_INVALID"):
            repair._require_risk_recovery_state_binding(
                Path(f"risk-recovery-{digest}.state.json"), state,
                "campaign-current", "END_FLAT")

    def test_end_flat_policy_seal_is_atomic_and_paper_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            policy_path = Path(directory) / "alpha.json"
            policy = {
                "schema": "hepta.ib-paper-campaign-policy.v4",
                "version": 4,
                "domain_id": "alpha",
                "campaign_id": "campaign-test",
                "enabled": True,
                "mutations_authorized": True,
                "paper_only": True,
                "live_authorized": False,
            }
            policy_path.write_text(json.dumps(policy), encoding="ascii")
            with mock.patch.object(
                    repair, "CAMPAIGN_POLICY", policy_path), \
                    mock.patch.object(
                        repair.os, "lstat", return_value=self.root_metadata()), \
                    mock.patch.object(repair.os, "fchown"):
                digest = repair._end_flat_persist_policy_disabled(
                    "campaign-test")
            persisted_raw = policy_path.read_bytes()
            persisted = json.loads(persisted_raw)

        self.assertFalse(persisted["enabled"])
        self.assertFalse(persisted["mutations_authorized"])
        self.assertTrue(persisted["paper_only"])
        self.assertFalse(persisted["live_authorized"])
        self.assertEqual(
            digest, "sha256:" + hashlib.sha256(persisted_raw).hexdigest())

    def test_end_flat_policy_seal_rejects_campaign_or_live_drift(self) -> None:
        invalid_policies = (
            {
                "schema": "hepta.ib-paper-campaign-policy.v4",
                "domain_id": "alpha", "campaign_id": "other-campaign",
                "enabled": True, "mutations_authorized": True,
                "paper_only": True, "live_authorized": False,
            },
            {
                "schema": "hepta.ib-paper-campaign-policy.v4",
                "domain_id": "alpha", "campaign_id": "campaign-test",
                "enabled": True, "mutations_authorized": True,
                "paper_only": True, "live_authorized": True,
            },
        )
        for policy in invalid_policies:
            with self.subTest(policy=policy), \
                    tempfile.TemporaryDirectory() as directory:
                policy_path = Path(directory) / "alpha.json"
                original = json.dumps(policy)
                policy_path.write_text(original, encoding="ascii")
                with mock.patch.object(
                        repair, "CAMPAIGN_POLICY", policy_path), \
                        mock.patch.object(
                            repair.os, "lstat",
                            return_value=self.root_metadata()), \
                        self.assertRaisesRegex(
                            RuntimeError, "END_FLAT_POLICY_BOUNDARY_INVALID"):
                    repair._end_flat_persist_policy_disabled("campaign-test")
                self.assertEqual(
                    policy_path.read_text(encoding="ascii"), original)

    def test_end_flat_control_requires_verified_deny_all(self) -> None:
        digest = "sha256:" + "a" * 64
        valid = json.dumps({
            "mode": "DENY_ALL",
            "paper_authorized": False,
            "live_authorized": False,
            "identity_count": 0,
            "identity_manifest_sha256": digest,
        })
        with mock.patch.object(
                repair, "run_checked", return_value=valid) as run_checked:
            self.assertEqual(
                repair._end_flat_revoke_local_paper_control(), {
                    "identity_manifest_sha256": digest,
                    "identity_count": 0,
                })
        run_checked.assert_called_once_with([
            repair.LOCAL_PAPER_CONTROL, "disable", "--domain", "alpha",
        ], timeout=60)

        invalid = json.dumps({
            "mode": "DENY_ALL",
            "paper_authorized": False,
            "live_authorized": True,
            "identity_manifest_sha256": digest,
        })
        with mock.patch.object(
                repair, "run_checked", return_value=invalid), \
                self.assertRaisesRegex(
                    RuntimeError, "END_FLAT_CONTROL_RESPONSE_INVALID"):
                repair._end_flat_revoke_local_paper_control()

    def test_start_campaign_starts_all_timers_before_agent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime_binding = {"campaign_id": "campaign-test"}
            agent = mock.Mock()
            agent.load_state.return_value = {
                "manual_start_required": False,
                "manual_started_at_ms": 10**18,
                "manual_start_permit_id": "a" * 64,
                "manual_start_invocation_id": "b" * 32,
                "runtime_binding": runtime_binding,
                "recovery_required": False,
                "trading_suspended": False,
            }
            values = {"HEPTA_LOCAL_AI_CAMPAIGN_ID": "campaign-test"}
            policy = {"expires_at_ms": 10**15, "max_cycles": 4}
            state = {"runtime_binding": runtime_binding}
            commands: list[list[str]] = []

            def checked(command: list[str], timeout: int = 30) -> str:
                del timeout
                commands.append(command)
                return ""

            with mock.patch.multiple(
                    repair,
                    CAMPAIGN_LIFECYCLE_LOCK=root / "lifecycle.lock",
                    RISK_RECOVERY_LOCK=root / "risk.lock",
                    END_FLAT_LOCK=root / "end-flat.lock",
                    END_FLAT_RECEIPT_ROOT=root,
                    START_PERMIT_PENDING=root / "start-permit.pending.json",
                    START_PERMIT_CLAIMED=root / "start-permit.claimed.json",
                    START_PERMIT_CONSUMED=root / "start-permit.consumed.json",
                    _validate_campaign_start_boundary=mock.Mock(
                        return_value=(agent, values, policy, state)),
                    _start_permit_paths_absent=mock.Mock(),
                    _capture_campaign_timer_states=mock.Mock(return_value={}),
                    _verify_waiting_timer=mock.Mock(),
                    _verify_deadline_timer=mock.Mock(),
                    _verify_start_dependencies=mock.Mock(),
                    _fresh_prelaunch_zero_proof=mock.Mock(
                        return_value=(1, 2, 3, 4)),
                    _write_prelaunch_zero_receipt=mock.Mock(return_value=(
                        root / "prelaunch.json", "sha256:" + "c" * 64)),
                    _publish_start_permit=mock.Mock(return_value={
                        "permit_id": "a" * 64,
                        "prelaunch_zero_receipt_sha256":
                            "sha256:" + "c" * 64,
                    }),
                    _verified_consumed_start_permit=mock.Mock(return_value={
                        "invocation_id": "b" * 32,
                    }),
                    _verify_active_agent_invocation=mock.Mock(),
                    _write_root_json=mock.Mock(),
                    _remove_start_permit_file=mock.Mock(),
                    _unit_is_active=mock.Mock(return_value=True),
                    run_checked=mock.Mock(side_effect=checked)), \
                    mock.patch.object(
                        repair.os, "fstat", return_value=self.root_metadata()):
                repair.manual_start_campaign()

        agent_start = commands.index([
            "/usr/bin/systemctl", "start", repair.AGENT_SERVICE])
        for unit in repair.CAMPAIGN_TIMER_UNITS:
            self.assertTrue(any(
                command[:2] == ["/usr/bin/systemctl", "start"] and
                unit in command
                for command in commands[:agent_start]))

    def test_external_p1_never_enters_legacy_manual_start(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy = {
                "admission_mode": "external-p1-finalized",
                "expires_at_ms": 10**15, "max_cycles": 1,
            }
            start_paths = mock.Mock()
            run_checked = mock.Mock()
            with mock.patch.multiple(
                    repair,
                    CAMPAIGN_LIFECYCLE_LOCK=root / "lifecycle.lock",
                    RISK_RECOVERY_LOCK=root / "risk.lock",
                    END_FLAT_LOCK=root / "end-flat.lock",
                    _validate_campaign_start_boundary=mock.Mock(return_value=(
                        mock.Mock(), {
                            "HEPTA_LOCAL_AI_CAMPAIGN_ID": "campaign-test"},
                        policy, {})),
                    _start_permit_paths_absent=start_paths,
                    run_checked=run_checked), \
                    mock.patch.object(
                        repair.os, "fstat", return_value=self.root_metadata()), \
                    self.assertRaisesRegex(
                        RuntimeError,
                        "CAMPAIGN_START_EXTERNAL_P1_MANUAL_START_FORBIDDEN"):
                repair.manual_start_campaign()
        start_paths.assert_not_called()
        run_checked.assert_not_called()

    def test_external_p1_pre_start_guard_cannot_consume_permit(self) -> None:
        rename = mock.Mock()
        verify_timer = mock.Mock()
        with mock.patch.multiple(
                repair,
                _load_start_permit=mock.Mock(return_value={}),
                _validate_campaign_start_boundary=mock.Mock(return_value=(
                    mock.Mock(), {}, {
                        "admission_mode": "external-p1-finalized"}, {})),
                _verify_waiting_timer=verify_timer,
                _rename_root_file_noreplace=rename), \
                self.assertRaisesRegex(
                    RuntimeError,
                    "CAMPAIGN_START_EXTERNAL_P1_MANUAL_START_FORBIDDEN"):
            repair.pre_start_guard()
        verify_timer.assert_not_called()
        rename.assert_not_called()

    def test_start_failure_always_reenables_recovery_after_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            agent = mock.Mock()
            values = {"HEPTA_LOCAL_AI_CAMPAIGN_ID": "campaign-test"}
            policy = {"expires_at_ms": 10**15, "max_cycles": 4}
            state = {"runtime_binding": {"campaign_id": "campaign-test"}}
            commands: list[list[str]] = []

            def checked(command: list[str], timeout: int = 30) -> str:
                del timeout
                commands.append(command)
                if command == [
                        "/usr/bin/systemctl", "start",
                        repair.AGENT_SERVICE]:
                    raise RuntimeError("start response lost")
                return ""

            with mock.patch.multiple(
                    repair,
                    CAMPAIGN_LIFECYCLE_LOCK=root / "lifecycle.lock",
                    RISK_RECOVERY_LOCK=root / "risk.lock",
                    END_FLAT_LOCK=root / "end-flat.lock",
                    END_FLAT_RECEIPT_ROOT=root,
                    START_PERMIT_PENDING=root / "start-permit.pending.json",
                    START_PERMIT_CLAIMED=root / "start-permit.claimed.json",
                    START_PERMIT_CONSUMED=root / "start-permit.consumed.json",
                    _validate_campaign_start_boundary=mock.Mock(
                        return_value=(agent, values, policy, state)),
                    _start_permit_paths_absent=mock.Mock(),
                    _capture_campaign_timer_states=mock.Mock(return_value={}),
                    _verify_waiting_timer=mock.Mock(),
                    _verify_deadline_timer=mock.Mock(),
                    _verify_start_dependencies=mock.Mock(),
                    _fresh_prelaunch_zero_proof=mock.Mock(
                        return_value=(1, 2, 3, 4)),
                    _write_prelaunch_zero_receipt=mock.Mock(return_value=(
                        root / "prelaunch.json", "sha256:" + "c" * 64)),
                    _publish_start_permit=mock.Mock(return_value={
                        "permit_id": "a" * 64,
                        "prelaunch_zero_receipt_sha256":
                            "sha256:" + "c" * 64,
                    }),
                    _unit_is_active=mock.Mock(return_value=False),
                    _restore_campaign_timer_states=mock.Mock(
                        side_effect=RuntimeError("restore failed")),
                    run_checked=mock.Mock(side_effect=checked)), \
                    mock.patch.object(
                        repair.os, "fstat", return_value=self.root_metadata()), \
                    self.assertRaisesRegex(
                        RuntimeError, "CAMPAIGN_START_ROLLBACK_FAILED"):
                repair.manual_start_campaign()

        self.assertIn([
            "/usr/bin/systemctl", "enable", "--now",
            repair.SAFE_RECOVERY_TIMER,
        ], commands)

    def test_prelaunch_zero_failure_never_starts_agent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            agent = mock.Mock()
            values = {"HEPTA_LOCAL_AI_CAMPAIGN_ID": "campaign-test"}
            policy = {"expires_at_ms": 10**15, "max_cycles": 4}
            state = {"runtime_binding": {"campaign_id": "campaign-test"}}
            commands: list[list[str]] = []

            def checked(command: list[str], timeout: int = 30) -> str:
                del timeout
                commands.append(command)
                return ""

            with mock.patch.multiple(
                    repair,
                    CAMPAIGN_LIFECYCLE_LOCK=root / "lifecycle.lock",
                    RISK_RECOVERY_LOCK=root / "risk.lock",
                    END_FLAT_LOCK=root / "end-flat.lock",
                    END_FLAT_RECEIPT_ROOT=root,
                    START_PERMIT_PENDING=root / "start-permit.pending.json",
                    START_PERMIT_CLAIMED=root / "start-permit.claimed.json",
                    START_PERMIT_CONSUMED=root / "start-permit.consumed.json",
                    _validate_campaign_start_boundary=mock.Mock(
                        return_value=(agent, values, policy, state)),
                    _start_permit_paths_absent=mock.Mock(),
                    _capture_campaign_timer_states=mock.Mock(return_value={}),
                    _verify_waiting_timer=mock.Mock(),
                    _verify_deadline_timer=mock.Mock(),
                    _verify_start_dependencies=mock.Mock(),
                    _fresh_prelaunch_zero_proof=mock.Mock(
                        side_effect=RuntimeError("not flat")),
                    _unit_is_active=mock.Mock(return_value=False),
                    _restore_campaign_timer_states=mock.Mock(),
                    run_checked=mock.Mock(side_effect=checked)), \
                    mock.patch.object(
                        repair.os, "fstat", return_value=self.root_metadata()), \
                    self.assertRaisesRegex(RuntimeError, "not flat"):
                repair.manual_start_campaign()

        self.assertNotIn([
            "/usr/bin/systemctl", "start", repair.AGENT_SERVICE,
        ], commands)

    def test_end_flat_seals_only_after_two_zero_proofs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            events: list[str] = []
            agent = mock.Mock()
            agent.empty_state.return_value = {}
            agent.active_orders.return_value = []
            agent._quantity_equal.side_effect = (
                lambda left, right: float(left) == float(right))

            def write_receipt(path: Path, value: object) -> None:
                events.append("receipt")
                FakeAgent.write_json(path, value)

            agent.write_json.side_effect = write_receipt

            def checked(command: list[str], timeout: int = 30) -> str:
                if command[:3] == [
                        "/usr/bin/systemctl", "stop",
                        repair.SAFE_RECOVERY_TIMER]:
                    self.assertEqual(command, [
                        "/usr/bin/systemctl", "stop",
                        repair.SAFE_RECOVERY_TIMER,
                        repair.SAFE_RECOVERY_SERVICE,
                        repair.SESSION_RENEW_TIMER,
                        repair.SESSION_RENEW_SERVICE,
                        repair.SUPERVISOR_TIMER,
                        repair.SUPERVISOR_SERVICE,
                        "hepta-local-ai-paper-agent.service",
                    ])
                    events.append("initial-stop")
                elif command[:3] == [
                        "/usr/bin/systemctl", "stop",
                        "hepta-ib-paper-campaign-operator@alpha.socket"]:
                    self.assertEqual(command, [
                        "/usr/bin/systemctl", "stop",
                        *repair.END_FLAT_EXECUTION_UNITS,
                        *repair.END_FLAT_TOOL_UNITS,
                    ])
                    events.append("stop-runtime")
                else:
                    self.fail("unexpected command: " + repr(command))
                return ""

            proof_results = iter(((10, 20), (11, 21)))

            def proof(*_args: object) -> tuple[int, int]:
                result = next(proof_results)
                events.append("proof-" + str(result[0]))
                return result

            def seal(_campaign_id: str) -> str:
                events.append("policy-seal")
                return "sha256:" + "b" * 64

            def revoke() -> str:
                events.append("control-deny-all")
                return {
                    "identity_manifest_sha256": "sha256:" + "c" * 64,
                    "identity_count": 0,
                }

            def deny_all() -> dict[str, object]:
                events.append("deny-all-check")
                return {
                    "broker_policy_sha256": "sha256:" + "d" * 64,
                    "authorized_connector_count": 0,
                    "authorized_uids": [],
                    "protected_port_count": 4,
                }

            session_descriptor = {
                "token_name": "local-paper.token",
                "token_sha256": "sha256:" + "e" * 64,
                "lease_generation": 7,
                "revoked": False,
            }

            def revoke_sessions(checkpoint: dict[str, object]) -> None:
                events.append("revoke-sessions")
                sessions = checkpoint["sessions"]
                sessions[0]["revoked"] = True
                checkpoint["phase"] = "SESSIONS_REVOKED"

            with mock.patch.multiple(
                    repair,
                    END_FLAT_RECEIPT_ROOT=root,
                    END_FLAT_LOCK=root / "end-flat.lock",
                    CAMPAIGN_LIFECYCLE_LOCK=root / "lifecycle.lock",
                    RISK_RECOVERY_LOCK=root / "risk.lock"), \
                    mock.patch.object(repair, "run_checked", side_effect=checked), \
                    mock.patch.object(
                        repair, "run", return_value=SimpleNamespace(
                            returncode=0, stdout="active\n", stderr="")), \
                    mock.patch.multiple(
                        repair,
                        _select_end_flat_session=mock.Mock(return_value=(
                            Path("/tmp/local-paper.token"), True)),
                        _end_flat_session_descriptors=mock.Mock(
                            return_value=[session_descriptor]),
                        _revoke_checkpoint_sessions=mock.Mock(
                            side_effect=revoke_sessions),
                        _ensure_end_flat_request_marker=mock.Mock(),
                        _validate_no_campaign_session_residue=mock.Mock(),
                        _persist_end_flat_checkpoint=mock.Mock(),
                        load_agent=mock.Mock(return_value=agent),
                        read_env=mock.Mock(return_value={
                            "HEPTA_LOCAL_AI_CAMPAIGN_ID": "campaign-test",
                        }),
                        agent_arguments=mock.Mock(return_value=object()),
                        _end_flat_state_records=mock.Mock(
                            side_effect=lambda _agent, _campaign, path:
                                [(path, {})]),
                        _reconcile_pending_mutation_records=mock.Mock(
                            return_value=([], {}, [])),
                        _managed_session_contexts=mock.Mock(return_value={
                            "local-paper.token": (
                                Path("/tmp/local-paper.token"), object()),
                        }),
                        _cancel_all_managed_session_orders=mock.Mock(
                            return_value=(
                                {"local-paper.token": []},
                                {"local-paper.token": set()})),
                        _clear_terminal_pending_mutation_records=mock.Mock(),
                        _require_managed_sessions_no_active_orders=mock.Mock(),
                        _end_flat_halt_campaign=mock.Mock(
                            return_value="halt_confirmed"),
                        _end_flat_cancel_orders=mock.Mock(
                            return_value=([], [])),
                        authoritative_state=mock.Mock(
                            return_value=(0.0, 9, 19)),
                        _end_flat_verify_deny_all=mock.Mock(
                            side_effect=deny_all),
                        _end_flat_verify_runtime_stopped=mock.Mock(
                            side_effect=lambda: events.append(
                                "runtime-stopped")),
                        _seal_end_flat_runtime_units=mock.Mock(
                            side_effect=lambda: events.append("seal-runtime")),
                        _seal_start_permit_residue=mock.Mock(return_value=0),
                        _seal_end_flat_retry_timer=mock.Mock(
                            side_effect=lambda: events.append("seal-retry"))), \
                    mock.patch.object(
                        repair, "_end_flat_authoritative_proof",
                        side_effect=proof), \
                    mock.patch.object(
                        repair, "_end_flat_persist_policy_disabled",
                        side_effect=seal), \
                    mock.patch.object(
                        repair, "_end_flat_revoke_local_paper_control",
                        side_effect=revoke), \
                    mock.patch.multiple(
                        repair.os,
                        fstat=mock.Mock(return_value=self.root_metadata()),
                        fchown=mock.DEFAULT,
                        chown=mock.DEFAULT,
                        chmod=mock.DEFAULT), \
                    mock.patch.object(repair.time, "sleep"):
                repair.end_flat()

            receipt_path = root / "end-flat-campaign-test.receipt.json"
            receipt = json.loads(receipt_path.read_text(encoding="ascii"))

        self.assertEqual(events, [
            "initial-stop", "proof-10", "proof-11", "policy-seal",
            "revoke-sessions", "control-deny-all", "deny-all-check", "stop-runtime",
            "runtime-stopped", "deny-all-check", "seal-runtime",
            "seal-retry",
        ])
        self.assertTrue(receipt["reboot_durable"])
        self.assertFalse(receipt["campaign_enabled"])
        self.assertFalse(receipt["mutations_authorized"])
        self.assertFalse(receipt["local_paper_authorized"])
        self.assertTrue(receipt["deny_all_verified"])
        self.assertFalse(receipt["live_authorized"])

    def test_end_flat_zero_proof_failure_does_not_seal_policy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            agent = mock.Mock()
            agent.empty_state.return_value = {}
            agent.active_orders.return_value = []
            agent._quantity_equal.side_effect = (
                lambda left, right: float(left) == float(right))
            seal = mock.Mock()
            revoke = mock.Mock()
            with mock.patch.multiple(
                    repair,
                    END_FLAT_RECEIPT_ROOT=root,
                    END_FLAT_LOCK=root / "end-flat.lock",
                    CAMPAIGN_LIFECYCLE_LOCK=root / "lifecycle.lock",
                    RISK_RECOVERY_LOCK=root / "risk.lock"), \
                    mock.patch.object(repair, "run_checked") as run_checked, \
                    mock.patch.object(
                        repair, "run", return_value=SimpleNamespace(
                            returncode=0, stdout="active\n", stderr="")), \
                    mock.patch.object(
                        repair, "_select_end_flat_session",
                        return_value=(Path("/tmp/local-paper.token"), True)), \
                    mock.patch.object(repair, "load_agent", return_value=agent), \
                    mock.patch.object(repair, "read_env", return_value={
                        "HEPTA_LOCAL_AI_CAMPAIGN_ID": "campaign-test",
                    }), \
                    mock.patch.object(
                        repair, "_ensure_end_flat_request_marker"), \
                    mock.patch.object(
                        repair, "agent_arguments", return_value=object()), \
                    mock.patch.multiple(
                        repair,
                        _end_flat_state_records=mock.Mock(
                            side_effect=lambda _agent, _campaign, path:
                                [(path, {})]),
                        _reconcile_pending_mutation_records=mock.Mock(
                            return_value=([], {}, [])),
                        _managed_session_contexts=mock.Mock(return_value={
                            "local-paper.token": (
                                Path("/tmp/local-paper.token"), object()),
                        }),
                        _cancel_all_managed_session_orders=mock.Mock(
                            return_value=(
                                {"local-paper.token": []},
                                {"local-paper.token": set()})),
                        _clear_terminal_pending_mutation_records=mock.Mock(),
                        _require_managed_sessions_no_active_orders=mock.Mock()), \
                    mock.patch.object(
                        repair, "_end_flat_halt_campaign",
                        return_value="halt_confirmed"), \
                    mock.patch.object(
                        repair, "_end_flat_cancel_orders",
                        return_value=([], [])), \
                    mock.patch.object(
                        repair, "authoritative_state",
                        return_value=(0.0, 9, 19)), \
                    mock.patch.object(
                        repair, "_end_flat_authoritative_proof",
                        side_effect=[
                            (10, 20),
                            RuntimeError("END_FLAT_FINAL_RISK_NOT_ZERO"),
                        ]), \
                    mock.patch.object(
                        repair, "_end_flat_persist_policy_disabled", seal), \
                    mock.patch.object(
                        repair, "_end_flat_revoke_local_paper_control", revoke), \
                    mock.patch.object(
                        repair.os, "fstat", return_value=self.root_metadata()), \
                    mock.patch.object(repair.time, "sleep"), \
                    self.assertRaisesRegex(
                        RuntimeError, "END_FLAT_FINAL_RISK_NOT_ZERO"):
                repair.end_flat()

        seal.assert_not_called()
        revoke.assert_not_called()
        self.assertEqual(run_checked.call_count, 1)
        self.assertEqual(run_checked.call_args.args[0][0:3], [
            "/usr/bin/systemctl", "stop", repair.SAFE_RECOVERY_TIMER,
        ])
        self.assertFalse(
            (root / "end-flat-campaign-test.receipt.json").exists())

    def test_legacy_latch_gets_complete_rearm_metadata(self) -> None:
        state: dict[str, object] = {
            "schema": "hepta.local-ai-paper-agent-state.v2",
            "recovery_required": True,
            "pending_order_id": 44,
            "pending_order_since_ms": 1786082441240,
        }
        repair._ensure_suspension_metadata(state, {
            "HEPTA_LOCAL_AI_AUTH_GENERATION": "auth-generation-old",
            "HEPTA_LOCAL_AI_AUTH_PROFILE_ID": "openai:test-profile",
            "HEPTA_LOCAL_AI_CAMPAIGN_ID": "old-campaign",
        })
        self.assertRegex(
            str(state["suspension_id"]), r"^suspension-[0-9a-f]{32}$")
        self.assertEqual(state["suspended_at_ms"], 1786082441240)
        self.assertEqual(state["suspension_code"], "ORDER_STATE_UNCERTAIN")
        self.assertEqual(
            state["auth_generation_at_suspend"], "auth-generation-old")
        self.assertEqual(state["campaign_id_at_suspend"], "old-campaign")

    def test_legacy_latch_migration_preserves_existing_metadata(self) -> None:
        state: dict[str, object] = {
            "suspension_id": "suspension-existing",
            "suspended_at_ms": 123,
            "suspension_code": "MODEL_AUTH_RATE_LIMIT",
            "auth_generation_at_suspend": "auth-generation-existing",
            "campaign_id_at_suspend": "campaign-existing",
            "pending_order_since_ms": 456,
        }
        repair._ensure_suspension_metadata(state, {
            "HEPTA_LOCAL_AI_AUTH_GENERATION": "auth-generation-new",
            "HEPTA_LOCAL_AI_AUTH_PROFILE_ID": "openai:test-profile",
            "HEPTA_LOCAL_AI_CAMPAIGN_ID": "campaign-new",
        })
        self.assertEqual(state["suspension_id"], "suspension-existing")
        self.assertEqual(state["suspended_at_ms"], 123)
        self.assertEqual(state["suspension_code"], "MODEL_AUTH_RATE_LIMIT")
        self.assertEqual(
            state["auth_generation_at_suspend"],
            "auth-generation-existing")
        self.assertEqual(
            state["campaign_id_at_suspend"], "campaign-existing")

    def test_provision_timeout_after_accept_exact_revokes_durable_bearer(
            self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            token = root / "sessions" / "local-paper.token"
            authority_root = root / "authority"
            real_lstat = repair.os.lstat

            def root_lstat(path: object) -> SimpleNamespace:
                metadata = real_lstat(path)
                return SimpleNamespace(
                    st_dev=metadata.st_dev, st_ino=metadata.st_ino,
                    st_mode=metadata.st_mode, st_nlink=metadata.st_nlink,
                    st_uid=0, st_gid=0, st_size=metadata.st_size)

            calls: list[list[str]] = []

            def command(arguments: list[str], timeout: int = 30) -> object:
                del timeout
                calls.append(arguments)
                if "provision" in arguments:
                    raise repair.subprocess.TimeoutExpired(arguments, 30)
                return SimpleNamespace(
                    returncode=0, stderr="", stdout=json.dumps({
                        "accepted": True, "reason_code": "OK",
                        "lease_generation": 1,
                    }))

            with mock.patch.multiple(
                    repair,
                    TOKEN_FILE=token,
                    RISK_RECOVERY_TOKEN_ROOT=token.parent,
                    SESSION_AUTHORITY_ROOT=authority_root,
                    run=mock.Mock(side_effect=command)), \
                    mock.patch.object(
                        repair.os, "lstat", side_effect=root_lstat), \
                    mock.patch.object(repair.os, "fchown"), \
                    self.assertRaisesRegex(
                        RuntimeError,
                        "REPAIR_SESSION_PROVISION_FAILED_REVOKED"):
                repair.provision_session(
                    900, token, "paper-timeout-after-accept")

            intent = authority_root / (
                token.name + ".authority.json")
            bearer = authority_root / (token.name + ".revoke-token")
            self.assertFalse(token.exists())
            self.assertFalse(intent.exists())
            self.assertFalse(bearer.exists())
            revoke = next(value for value in calls if "revoke" in value)
            self.assertEqual(revoke[revoke.index("--generation") + 1], "1")
            self.assertEqual(revoke[revoke.index("--token-owner-uid") + 1], "0")
            self.assertEqual(
                Path(revoke[revoke.index("--token-file") + 1]), bearer)

    def test_session_tools_list_retries_gateway_transport_until_ready(
            self) -> None:
        token = Path("/tmp/end-flat-" + "e" * 24 + ".token")
        responses = [
            SimpleNamespace(returncode=1, stdout="", stderr="connect failed"),
            SimpleNamespace(returncode=0, stdout='{"status":"ok"}', stderr=""),
        ]
        with mock.patch.object(repair, "run", side_effect=responses) as invoke:
            usable, absence = repair._session_tools_list(
                token, retry_until_ready=True)
        self.assertTrue(usable)
        self.assertIsNone(absence)
        self.assertEqual(invoke.call_count, 2)

    def test_session_tools_list_retries_expected_session_publication_lag(
            self) -> None:
        token = Path("/tmp/end-flat-" + "f" * 24 + ".token")
        responses = [
            SimpleNamespace(
                returncode=4,
                stdout='{"status":"error","reason_code":"SESSION_NOT_FOUND"}',
                stderr=""),
            SimpleNamespace(returncode=0, stdout='{"status":"ok"}', stderr=""),
        ]
        with mock.patch.object(repair, "run", side_effect=responses) as invoke:
            usable, absence = repair._session_tools_list(
                token, retry_until_ready=True, expect_present=True)
        self.assertTrue(usable)
        self.assertIsNone(absence)
        self.assertEqual(invoke.call_count, 2)

    def test_session_tools_list_keeps_existing_missing_session_terminal(
            self) -> None:
        token = Path("/tmp/end-flat-" + "0" * 24 + ".token")
        response = SimpleNamespace(
            returncode=4,
            stdout='{"status":"error","reason_code":"SESSION_NOT_FOUND"}',
            stderr="")
        with mock.patch.object(repair, "run", return_value=response) as invoke:
            usable, absence = repair._session_tools_list(
                token, retry_until_ready=True)
        self.assertFalse(usable)
        self.assertEqual(absence, "SESSION_NOT_FOUND")
        self.assertEqual(invoke.call_count, 1)

    def test_authority_revoke_retries_transient_transport_same_generation(
            self) -> None:
        token = Path("/tmp/local-paper-revoke-" + "a" * 24 + ".token")
        intent = {"token_sha256": "sha256:" + "b" * 64}
        responses = [
            SimpleNamespace(
                returncode=3, stdout="",
                stderr="SUPERVISOR_SOCKET_CONNECT_FAILED"),
            SimpleNamespace(
                returncode=0,
                stdout=json.dumps({
                    "accepted": True, "reason_code": "OK",
                    "lease_generation": 7,
                }), stderr=""),
        ]
        with mock.patch.object(
                repair, "_session_authority_bearer_matches") as matches, \
                mock.patch.object(
                    repair, "run", side_effect=responses) as invoke, \
                mock.patch.object(repair.time, "sleep"):
            outcome = repair._revoke_authority_generation(token, intent, 7)

        self.assertEqual(outcome, "ACCEPTED")
        self.assertEqual(invoke.call_count, 2)
        self.assertEqual(matches.call_count, 2)
        first_command = invoke.call_args_list[0].args[0]
        second_command = invoke.call_args_list[1].args[0]
        self.assertEqual(first_command, second_command)
        self.assertEqual(
            first_command[first_command.index("--generation") + 1], "7")
        self.assertEqual(
            first_command[first_command.index("--token-owner-uid") + 1], "0")

    def test_authority_revoke_retries_transient_readiness_rejection(
            self) -> None:
        token = Path("/tmp/local-paper-revoke-" + "c" * 24 + ".token")
        intent = {"token_sha256": "sha256:" + "d" * 64}
        responses = [
            SimpleNamespace(
                returncode=4,
                stdout=json.dumps({
                    "accepted": False,
                    "reason_code": "EXECUTION_EVENT_SERVICE_NOT_READY",
                    "lease_generation": 3,
                }), stderr=""),
            SimpleNamespace(
                returncode=0,
                stdout=json.dumps({
                    "accepted": True, "reason_code": "OK",
                    "lease_generation": 3,
                }), stderr=""),
        ]
        with mock.patch.object(
                repair, "_session_authority_bearer_matches"), \
                mock.patch.object(
                    repair, "run", side_effect=responses) as invoke, \
                mock.patch.object(repair.time, "sleep"):
            outcome = repair._revoke_authority_generation(token, intent, 3)

        self.assertEqual(outcome, "ACCEPTED")
        self.assertEqual(invoke.call_count, 2)

    def test_authority_revoke_does_not_retry_nontransient_rejection(
            self) -> None:
        token = Path("/tmp/local-paper-revoke-" + "e" * 24 + ".token")
        intent = {"token_sha256": "sha256:" + "f" * 64}
        response = SimpleNamespace(
            returncode=4,
            stdout=json.dumps({
                "accepted": False,
                "reason_code": "SESSION_OWNER_RECOVERY_REQUIRED",
                "lease_generation": 1,
            }), stderr="")
        with mock.patch.object(
                repair, "_session_authority_bearer_matches"), \
                mock.patch.object(
                    repair, "run", return_value=response) as invoke, \
                mock.patch.object(repair.time, "sleep") as sleep:
            with self.assertRaisesRegex(
                    RuntimeError, "REPAIR_SESSION_REVOKE_UNCERTAIN"):
                repair._revoke_authority_generation(token, intent, 1)

        self.assertEqual(invoke.call_count, 1)
        sleep.assert_not_called()

    def test_provision_commits_active_only_after_delivery_lease(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            token = root / "sessions/local-paper.token"
            authority_root = root / "authority"
            events: list[str] = []
            real_lstat = repair.os.lstat
            original_write = repair._write_root_json

            def root_lstat(path: object) -> SimpleNamespace:
                metadata = real_lstat(path)
                return SimpleNamespace(
                    st_dev=metadata.st_dev, st_ino=metadata.st_ino,
                    st_mode=metadata.st_mode, st_nlink=metadata.st_nlink,
                    st_uid=0, st_gid=0, st_size=metadata.st_size)

            def write(path: Path, value: object) -> None:
                if path.parent == authority_root and isinstance(value, dict):
                    events.append("record:" + str(value.get("phase")))
                original_write(path, value)

            def command(arguments: list[str], timeout: int = 30) -> object:
                del timeout
                if "provision" in arguments:
                    events.append("remote:accepted")
                    return SimpleNamespace(
                        returncode=0, stderr="", stdout=json.dumps({
                            "accepted": True, "reason_code": "OK",
                            "lease_generation": 1,
                        }))
                events.append("tools:list")
                return SimpleNamespace(
                    returncode=0, stderr="", stdout=json.dumps({
                        "status": "ok",
                    }))

            with mock.patch.multiple(
                    repair,
                    TOKEN_FILE=token,
                    RISK_RECOVERY_TOKEN_ROOT=token.parent,
                    SESSION_AUTHORITY_ROOT=authority_root,
                    _resolve_session_provision_intent=mock.Mock(),
                    _write_root_json=mock.Mock(side_effect=write),
                    token_metadata_safe=mock.Mock(return_value=True),
                    write_session_lease=mock.Mock(
                        side_effect=lambda *_args, **_kwargs:
                        events.append("delivery:lease")),
                    run=mock.Mock(side_effect=command)), \
                    mock.patch.object(
                        repair.os, "lstat", side_effect=root_lstat), \
                    mock.patch.object(repair.os, "fchown"), \
                    mock.patch.object(repair.os, "chown"), \
                    mock.patch.object(repair.os, "chmod"):
                repair.provision_session(900, token, "paper-commit-order")

            self.assertLess(
                events.index("delivery:lease"), events.index("record:ACTIVE"))
            self.assertLess(
                events.index("record:ACTIVE"), events.index("tools:list"))

    def test_unresolved_provision_revoke_keeps_bearer_for_retry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            token = root / "sessions" / "local-paper.token"
            authority_root = root / "authority"
            real_lstat = repair.os.lstat

            def root_lstat(path: object) -> SimpleNamespace:
                metadata = real_lstat(path)
                return SimpleNamespace(
                    st_dev=metadata.st_dev, st_ino=metadata.st_ino,
                    st_mode=metadata.st_mode, st_nlink=metadata.st_nlink,
                    st_uid=0, st_gid=0, st_size=metadata.st_size)

            def uncertain(arguments: list[str], timeout: int = 30) -> object:
                raise repair.subprocess.TimeoutExpired(arguments, timeout)

            with mock.patch.multiple(
                    repair,
                    TOKEN_FILE=token,
                    RISK_RECOVERY_TOKEN_ROOT=token.parent,
                    SESSION_AUTHORITY_ROOT=authority_root,
                    run=mock.Mock(side_effect=uncertain)), \
                    mock.patch.object(
                        repair.os, "lstat", side_effect=root_lstat), \
                    mock.patch.object(repair.os, "fchown"), \
                    self.assertRaisesRegex(
                        RuntimeError,
                        "PROVISION_UNCERTAIN_RECOVERY_REQUIRED"):
                repair.provision_session(900, token, "paper-timeout")

            record_path = authority_root / (
                token.name + ".authority.json")
            bearer = authority_root / (token.name + ".revoke-token")
            record = json.loads(record_path.read_text(encoding="ascii"))
            self.assertEqual(record["phase"], "REVOKE_PENDING")
            self.assertTrue(token.exists())
            self.assertTrue(bearer.exists())

            accepted = SimpleNamespace(
                returncode=0, stderr="", stdout=json.dumps({
                    "accepted": True, "reason_code": "OK",
                    "lease_generation": 1,
                }))
            with mock.patch.multiple(
                    repair,
                    TOKEN_FILE=token,
                    RISK_RECOVERY_TOKEN_ROOT=token.parent,
                    SESSION_AUTHORITY_ROOT=authority_root,
                    run=mock.Mock(return_value=accepted)), \
                    mock.patch.object(
                        repair.os, "lstat", side_effect=root_lstat), \
                    mock.patch.object(repair.os, "fchown"):
                repair._resolve_session_provision_intent(token)
            self.assertFalse(record_path.exists())
            self.assertFalse(bearer.exists())
            self.assertFalse(token.exists())

    def test_renew_admission_reads_stable_clear_agent_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            expected = self.renew_admission_state()
            state_path.write_text(
                json.dumps(expected, sort_keys=True) + "\n", encoding="ascii")
            state_path.chmod(0o600)

            def root_lstat(path: object) -> SimpleNamespace:
                return self.root_path_metadata(REAL_LSTAT(path))

            def root_stat(path: object, **kwargs: object) -> SimpleNamespace:
                return self.root_path_metadata(REAL_STAT(path, **kwargs))

            with mock.patch.object(repair, "AGENT_STATE", state_path), \
                    mock.patch.object(
                        repair.os, "lstat", side_effect=root_lstat), \
                    mock.patch.object(
                        repair.os, "fstat", side_effect=self.root_fstat), \
                    mock.patch.object(
                        repair.os, "stat", side_effect=root_stat):
                loaded = repair._load_session_renew_admission_state()
        self.assertEqual(loaded, expected)

    def test_renew_admission_hands_every_recovery_or_malformed_state_off(
            self) -> None:
        base = self.renew_admission_state()
        mutation = dict(base)
        mutation.update({
            "pending_mutation_kind": "PLACE_ORDER",
            "pending_mutation_command_id": "entry-command-renew-0001",
            "pending_mutation_recorded_at_ms": 1_786_000_000_000,
            "pending_mutation_token_name": "local-paper.token",
            "pending_mutation_token_sha256": "sha256:" + "a" * 64,
        })
        cases: tuple[tuple[str, object], ...] = (
            ("recovery-required", {**base, "recovery_required": True}),
            ("trading-suspended", {**base, "trading_suspended": True}),
            ("mutation-unproven", {
                **base, "pending_mutation_state_unproven": True}),
            ("pending-mutation", mutation),
            ("pending-order", {**base, "pending_order_id": 41}),
            ("incident-order", {**base, "incident_pending_order_id": 41}),
            ("bool-order", {**base, "pending_order_id": True}),
            ("malformed-flag", {**base, "recovery_required": "false"}),
            ("invalid-json", b"{not-json\n"),
        )
        for label, value in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                state_path = Path(directory) / "state.json"
                if isinstance(value, bytes):
                    state_path.write_bytes(value)
                else:
                    state_path.write_text(
                        json.dumps(value, sort_keys=True) + "\n",
                        encoding="ascii")
                state_path.chmod(0o600)

                def root_lstat(path: object) -> SimpleNamespace:
                    return self.root_path_metadata(REAL_LSTAT(path))

                def root_stat(
                        path: object, **kwargs: object) -> SimpleNamespace:
                    return self.root_path_metadata(REAL_STAT(path, **kwargs))

                with mock.patch.object(repair, "AGENT_STATE", state_path), \
                        mock.patch.object(
                            repair.os, "lstat", side_effect=root_lstat), \
                        mock.patch.object(
                            repair.os, "fstat", side_effect=self.root_fstat), \
                        mock.patch.object(
                            repair.os, "stat", side_effect=root_stat), \
                        self.assertRaisesRegex(
                            repair._SessionRenewRecoveryHandoff,
                            "SESSION_RENEW_RECOVERY_HANDOFF_REQUIRED"):
                    repair._load_session_renew_admission_state()

    def test_renew_handoff_bypasses_all_authority_cleanup_and_routes_on_failure(
            self) -> None:
        events: list[str] = []

        @contextlib.contextmanager
        def lifecycle() -> object:
            events.append("lifecycle-enter")
            try:
                yield
            finally:
                events.append("lifecycle-exit")

        @contextlib.contextmanager
        def broker(**_kwargs: object) -> object:
            events.append("broker-enter")
            try:
                yield SimpleNamespace(acquired=True)
            finally:
                events.append("broker-exit")

        def handoff() -> None:
            events.append("admission-gate")
            raise repair._SessionRenewRecoveryHandoff(
                "SESSION_RENEW_RECOVERY_HANDOFF_REQUIRED")

        renew = mock.Mock()
        load_authority = mock.Mock()
        resolve = mock.Mock()
        command = mock.Mock()
        with mock.patch.multiple(
                repair,
                _campaign_lifecycle_locks=mock.Mock(side_effect=lifecycle),
                _broker_mutation_lock=mock.Mock(side_effect=broker),
                _load_session_renew_admission_state=mock.Mock(
                    side_effect=handoff),
                _renew_session_once=renew,
                _load_session_provision_intent=load_authority,
                _resolve_session_provision_intent=resolve,
                run=command), self.assertRaisesRegex(
                    repair._SessionRenewRecoveryHandoff,
                    "SESSION_RENEW_RECOVERY_HANDOFF_REQUIRED"):
            repair.renew_session()
        self.assertEqual(events, [
            "lifecycle-enter", "broker-enter", "admission-gate",
            "broker-exit", "lifecycle-exit",
        ])
        renew.assert_not_called()
        load_authority.assert_not_called()
        resolve.assert_not_called()
        command.assert_not_called()
        unit = (ROOT / "systemd/hepta-local-paper-session-renew.service").read_text(
            encoding="ascii")
        self.assertIn(
            "OnFailure=hepta-local-paper-safe-recover.service", unit)

    def test_renew_order_boundary_requires_fresh_authoritative_global_zero(
            self) -> None:
        clear_agent = SimpleNamespace(
            orders_snapshot=mock.Mock(return_value=
                self.owner_orders_snapshot([])))
        with mock.patch.multiple(
                repair,
                load_agent=mock.Mock(return_value=clear_agent),
                agent_arguments=mock.Mock(return_value=object())):
            repair._require_session_renew_order_boundary()

        active_agent = SimpleNamespace(
            orders_snapshot=mock.Mock(return_value=
                self.owner_orders_snapshot([41], [41])))
        with mock.patch.multiple(
                repair,
                load_agent=mock.Mock(return_value=active_agent),
                agent_arguments=mock.Mock(return_value=object())), \
                self.assertRaisesRegex(
                    repair._SessionRenewRecoveryHandoff,
                    "SESSION_RENEW_RECOVERY_HANDOFF_REQUIRED"):
            repair._require_session_renew_order_boundary()

    def test_end_flat_foreign_owner_blocks_before_any_cancel(self) -> None:
        contexts = {
            "local-paper.token": (Path("/run/local-paper.token"), object()),
        }
        agent = SimpleNamespace(
            orders_snapshot=mock.Mock(return_value=
                self.owner_orders_snapshot([41], [])),
            tool_response=mock.Mock())
        with self.assertRaisesRegex(
                RuntimeError, "END_FLAT_UNMANAGED_ACTIVE_ORDER_PRESENT"):
            repair._cancel_all_managed_session_orders(agent, contexts, {})
        agent.tool_response.assert_not_called()

    def test_end_flat_owner_projection_must_match_durable_session(self) -> None:
        arguments = SimpleNamespace(
            managed_owner_session_id="session-expected")
        contexts = {
            "local-paper.token": (
                Path("/run/local-paper.token"), arguments),
        }
        agent = SimpleNamespace(
            orders_snapshot=mock.Mock(return_value=
                self.owner_orders_snapshot(
                    [], [], session_id="session-swapped")),
            tool_response=mock.Mock())
        with self.assertRaisesRegex(
                RuntimeError, "END_FLAT_ORDER_OWNER_SCOPE_MISMATCH"):
            repair._cancel_all_managed_session_orders(agent, contexts, {})
        agent.tool_response.assert_not_called()

    def test_end_flat_unmapped_order_blocks_before_any_cancel(self) -> None:
        projection = self.owner_orders_snapshot([41], [])
        projection.update({
            "authoritative": False,
            "owner_projection_complete": False,
            "owned_active_order_ids_authoritative": False,
            "unmapped_active_order_ids": [41],
            "reason_code": "EXECUTION_ORDER_OWNER_PROJECTION_INCOMPLETE",
        })
        contexts = {
            "local-paper.token": (Path("/run/local-paper.token"), object()),
        }
        agent = SimpleNamespace(
            orders_snapshot=mock.Mock(return_value=projection),
            tool_response=mock.Mock())
        with self.assertRaisesRegex(
                RuntimeError, "END_FLAT_ORDER_PROJECTION_NOT_AUTHORITATIVE"):
            repair._cancel_all_managed_session_orders(agent, contexts, {})
        agent.tool_response.assert_not_called()

    def test_renew_timeout_revokes_candidate_generation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            token = root / "sessions" / "local-paper.token"
            token.parent.mkdir(parents=True)
            authority_root = root / "authority"
            authority_root.mkdir()
            raw_token = ("d" * 64 + "\n").encode("ascii")
            token.write_bytes(raw_token)
            token.chmod(0o600)
            bearer = authority_root / (token.name + ".revoke-token")
            bearer.write_bytes(raw_token)
            bearer.chmod(0o600)
            token_sha256 = "sha256:" + hashlib.sha256(raw_token).hexdigest()
            record_path = authority_root / (
                token.name + ".authority.json")
            record_path.write_text(json.dumps({
                "schema": "hepta.local-paper-session-provision-intent.v1",
                "phase": "ACTIVE", "token_name": token.name,
                "authority_bearer_name": bearer.name,
                "token_sha256": token_sha256,
                "expected_lease_generation": 1,
                "lease_generation": 7,
                "session_name": "paper-renew-test",
                "session_id": "paper-renew-test-id", "peer_uid": 2004,
                "ttl_seconds": 900, "created_at_ms": 1,
                "expires_at_ms": 10**18,
                "paper_only": True, "live_authorized": False,
            }) + "\n", encoding="ascii")
            record_path.chmod(0o600)
            lease = repair.session_lease_path(token)
            lease.write_text(json.dumps({
                "schema": "hepta.local-paper-session-lease.v1",
                "session_name": "paper-renew-test",
                "lease_generation": 7, "token_sha256": token_sha256,
            }) + "\n", encoding="ascii")
            lease.chmod(0o600)
            policy = root / "alpha.json"
            policy.write_text(json.dumps({
                "schema": "hepta.ib-paper-campaign-policy.v4",
                "campaign_id": "campaign-test", "enabled": True,
                "mutations_authorized": True, "paper_only": True,
                "live_authorized": False,
                "expires_at_ms": repair.time.time_ns() // 1_000_000 + 3_600_000,
            }), encoding="ascii")
            real_lstat = repair.os.lstat

            def root_lstat(path: object) -> SimpleNamespace:
                metadata = real_lstat(path)
                return SimpleNamespace(
                    st_dev=metadata.st_dev, st_ino=metadata.st_ino,
                    st_mode=metadata.st_mode, st_nlink=metadata.st_nlink,
                    st_uid=0, st_gid=0, st_size=metadata.st_size)

            calls: list[list[str]] = []

            def command(arguments: list[str], timeout: int = 30) -> object:
                del timeout
                calls.append(arguments)
                if "renew" in arguments:
                    raise repair.subprocess.TimeoutExpired(arguments, 30)
                return SimpleNamespace(
                    returncode=0, stderr="", stdout=json.dumps({
                        "accepted": True, "reason_code": "OK",
                        "lease_generation": 8,
                    }))

            with mock.patch.multiple(
                    repair,
                    TOKEN_FILE=token,
                    RISK_RECOVERY_TOKEN_ROOT=token.parent,
                    SESSION_AUTHORITY_ROOT=authority_root,
                    CAMPAIGN_POLICY=policy,
                    run=mock.Mock(side_effect=command),
                    read_env=mock.Mock(return_value={
                        "HEPTA_LOCAL_AI_CAMPAIGN_ID": "campaign-test",
                    }),
                    _load_session_renew_admission_state=mock.Mock(
                        return_value={}),
                    _require_session_renew_order_boundary=mock.Mock(),
                    token_metadata_safe=mock.Mock(return_value=True)), \
                    mock.patch.object(
                        repair.os, "lstat", side_effect=root_lstat), \
                    mock.patch.object(repair.os, "fchown"), \
                    self.assertRaises(repair.subprocess.TimeoutExpired):
                repair.renew_session()

            renew = next(value for value in calls if "renew" in value)
            revoke = next(value for value in calls if "revoke" in value)
            self.assertEqual(
                renew[renew.index("--ttl-sec") + 1], "86400")
            self.assertEqual(revoke[revoke.index("--generation") + 1], "8")
            self.assertFalse(token.exists())
            self.assertFalse(record_path.exists())
            self.assertFalse(bearer.exists())

    def test_renew_busy_mutation_lock_skips_without_revoke(self) -> None:
        busy = contextlib.nullcontext(SimpleNamespace(acquired=False))
        with mock.patch.object(
                repair, "_broker_mutation_lock", return_value=busy), \
                mock.patch.object(repair, "_renew_session_once") as renew, \
                mock.patch.object(
                    repair, "_load_session_provision_intent") as load, \
                mock.patch.object(
                    repair, "_resolve_session_provision_intent") as revoke:
            repair.renew_session()
        renew.assert_not_called()
        load.assert_not_called()
        revoke.assert_not_called()

    def test_hourly_renewal_preserves_maximum_recovery_runway(self) -> None:
        now_ms = 1_000_000
        self.assertEqual(
            repair._renewal_recovery_ttl_seconds(now_ms + 1, now_ms),
            86_400)
        self.assertEqual(
            repair._renewal_recovery_ttl_seconds(
                now_ms + 23 * 60 * 60 * 1000, now_ms),
            86_400)
        self.assertIsNone(
            repair._renewal_recovery_ttl_seconds(now_ms, now_ms))

    def test_renew_timeout_before_accept_revokes_current_generation(
            self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            token = root / "sessions" / "local-paper.token"
            token.parent.mkdir(parents=True)
            authority_root = root / "authority"
            authority_root.mkdir()
            raw_token = ("f" * 64 + "\n").encode("ascii")
            token.write_bytes(raw_token)
            token.chmod(0o600)
            bearer = authority_root / (token.name + ".revoke-token")
            bearer.write_bytes(raw_token)
            bearer.chmod(0o600)
            token_sha256 = "sha256:" + hashlib.sha256(raw_token).hexdigest()
            record_path = authority_root / (
                token.name + ".authority.json")
            record_path.write_text(json.dumps({
                "schema": "hepta.local-paper-session-provision-intent.v1",
                "phase": "ACTIVE", "token_name": token.name,
                "authority_bearer_name": bearer.name,
                "token_sha256": token_sha256,
                "expected_lease_generation": 1,
                "lease_generation": 7,
                "session_name": "paper-renew-before-test",
                "session_id": "paper-renew-before-test-id",
                "peer_uid": 2004, "ttl_seconds": 900,
                "created_at_ms": 1, "expires_at_ms": 10**18,
                "paper_only": True, "live_authorized": False,
            }) + "\n", encoding="ascii")
            record_path.chmod(0o600)
            lease = repair.session_lease_path(token)
            lease.write_text(json.dumps({
                "schema": "hepta.local-paper-session-lease.v1",
                "session_name": "paper-renew-before-test",
                "lease_generation": 7, "token_sha256": token_sha256,
            }) + "\n", encoding="ascii")
            lease.chmod(0o600)
            policy = root / "alpha.json"
            policy.write_text(json.dumps({
                "schema": "hepta.ib-paper-campaign-policy.v4",
                "campaign_id": "campaign-test", "enabled": True,
                "mutations_authorized": True, "paper_only": True,
                "live_authorized": False,
                "expires_at_ms":
                    repair.time.time_ns() // 1_000_000 + 3_600_000,
            }), encoding="ascii")
            real_lstat = repair.os.lstat

            def root_lstat(path: object) -> SimpleNamespace:
                metadata = real_lstat(path)
                return SimpleNamespace(
                    st_dev=metadata.st_dev, st_ino=metadata.st_ino,
                    st_mode=metadata.st_mode, st_nlink=metadata.st_nlink,
                    st_uid=0, st_gid=0, st_size=metadata.st_size)

            revoke_generations: list[int] = []

            def command(arguments: list[str], timeout: int = 30) -> object:
                del timeout
                if "renew" in arguments:
                    raise repair.subprocess.TimeoutExpired(arguments, 30)
                generation = int(
                    arguments[arguments.index("--generation") + 1])
                revoke_generations.append(generation)
                if generation == 8:
                    return SimpleNamespace(
                        returncode=4, stderr="", stdout=json.dumps({
                            "accepted": False,
                            "reason_code":
                                "SESSION_LEASE_GENERATION_MISMATCH",
                            "lease_generation": 7,
                        }))
                return SimpleNamespace(
                    returncode=0, stderr="", stdout=json.dumps({
                        "accepted": True, "reason_code": "OK",
                        "lease_generation": 7,
                    }))

            with mock.patch.multiple(
                    repair,
                    TOKEN_FILE=token,
                    RISK_RECOVERY_TOKEN_ROOT=token.parent,
                    SESSION_AUTHORITY_ROOT=authority_root,
                    CAMPAIGN_POLICY=policy,
                    run=mock.Mock(side_effect=command),
                    read_env=mock.Mock(return_value={
                        "HEPTA_LOCAL_AI_CAMPAIGN_ID": "campaign-test",
                    }),
                    _load_session_renew_admission_state=mock.Mock(
                        return_value={}),
                    _require_session_renew_order_boundary=mock.Mock(),
                    token_metadata_safe=mock.Mock(return_value=True)), \
                    mock.patch.object(
                        repair.os, "lstat", side_effect=root_lstat), \
                    mock.patch.object(repair.os, "fchown"), \
                    self.assertRaises(repair.subprocess.TimeoutExpired):
                repair.renew_session()

            self.assertEqual(revoke_generations, [8, 7])
            self.assertFalse(token.exists())
            self.assertFalse(record_path.exists())
            self.assertFalse(bearer.exists())

    def test_reboot_missing_run_material_still_revokes_durable_authority(
            self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            token = root / "sessions" / "local-paper.token"
            token.parent.mkdir(parents=True)
            authority_root = root / "authority"
            authority_root.mkdir()
            raw_token = ("e" * 64 + "\n").encode("ascii")
            token_sha256 = "sha256:" + hashlib.sha256(raw_token).hexdigest()
            bearer = authority_root / (token.name + ".revoke-token")
            bearer.write_bytes(raw_token)
            bearer.chmod(0o600)
            record_path = authority_root / (
                token.name + ".authority.json")
            record_path.write_text(json.dumps({
                "schema": "hepta.local-paper-session-provision-intent.v1",
                "phase": "ACTIVE", "token_name": token.name,
                "authority_bearer_name": bearer.name,
                "token_sha256": token_sha256,
                "expected_lease_generation": 1,
                "lease_generation": 4,
                "session_name": "paper-reboot-test",
                "session_id": "paper-reboot-test-id", "peer_uid": 2004,
                "ttl_seconds": 900, "created_at_ms": 1,
                "expires_at_ms": 10**18,
                "paper_only": True, "live_authorized": False,
            }) + "\n", encoding="ascii")
            record_path.chmod(0o600)
            real_lstat = repair.os.lstat

            def root_lstat(path: object) -> SimpleNamespace:
                metadata = real_lstat(path)
                return SimpleNamespace(
                    st_dev=metadata.st_dev, st_ino=metadata.st_ino,
                    st_mode=metadata.st_mode, st_nlink=metadata.st_nlink,
                    st_uid=0, st_gid=0, st_size=metadata.st_size)

            accepted = SimpleNamespace(
                returncode=0, stderr="", stdout=json.dumps({
                    "accepted": True, "reason_code": "OK",
                    "lease_generation": 4,
                }))
            with mock.patch.multiple(
                    repair,
                    TOKEN_FILE=token,
                    RISK_RECOVERY_TOKEN_ROOT=token.parent,
                    SESSION_AUTHORITY_ROOT=authority_root,
                    run=mock.Mock(return_value=accepted)), \
                    mock.patch.object(
                        repair.os, "lstat", side_effect=root_lstat), \
                    mock.patch.object(repair.os, "fchown"):
                evidence = repair._revoke_recovery_session(token)
            self.assertEqual(evidence["tool_session_lease_generation"], 4)
            self.assertFalse(record_path.exists())
            self.assertFalse(bearer.exists())

    def test_reboot_rematerializes_original_active_order_owner(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            expires_at_ms = repair.time.time_ns() // 1_000_000 + 3_600_000
            token, authority_root, record, raw = (
                self.durable_active_session_fixture(
                    root, expires_at_ms=expires_at_ms))
            real_lstat = repair.os.lstat
            real_stat = repair.os.stat

            def as_root(metadata: object) -> SimpleNamespace:
                return SimpleNamespace(
                    st_dev=metadata.st_dev, st_ino=metadata.st_ino,
                    st_mode=metadata.st_mode, st_nlink=metadata.st_nlink,
                    st_uid=0, st_gid=0, st_size=metadata.st_size)

            def root_lstat(path: object) -> SimpleNamespace:
                return as_root(real_lstat(path))

            def root_stat(path: object, *args: object,
                          **kwargs: object) -> SimpleNamespace:
                return as_root(real_stat(path, *args, **kwargs))

            listed = SimpleNamespace(
                returncode=0, stderr="", stdout=json.dumps({
                    "status": "ok", "tool": "system.tools.list",
                    "reason_code": "",
                }))
            provision = mock.Mock()
            with mock.patch.multiple(
                    repair,
                    TOKEN_FILE=token,
                    RISK_RECOVERY_TOKEN_ROOT=token.parent,
                    SESSION_AUTHORITY_ROOT=authority_root,
                    run=mock.Mock(return_value=listed),
                    provision_session=provision), \
                    mock.patch.object(
                        repair.pwd, "getpwnam", return_value=SimpleNamespace(
                            pw_uid=0, pw_gid=0)), \
                    mock.patch.object(
                        repair.os, "lstat", side_effect=root_lstat), \
                    mock.patch.object(
                        repair.os, "stat", side_effect=root_stat), \
                    mock.patch.object(
                        repair.os, "fstat", side_effect=self.root_fstat), \
                    mock.patch.object(repair.os, "fchown"):
                selected, retained = repair._select_risk_recovery_session(
                    "a" * 24, {"pending_order_id": 95})

            self.assertEqual(selected, token)
            self.assertTrue(retained)
            self.assertEqual(token.read_bytes(), raw)
            lease = json.loads(
                repair.session_lease_path(token).read_text(encoding="ascii"))
            self.assertEqual(lease["lease_generation"], 7)
            self.assertEqual(lease["expires_at_ms"], expires_at_ms)
            self.assertTrue(record.exists())
            provision.assert_not_called()

    def test_active_rematerial_token_and_lease_torn_writes_are_retryable(
            self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            token = root / "sessions" / "local-paper.token"
            token.parent.mkdir(parents=True)
            raw = ("e" * 64 + "\n").encode("ascii")
            now = repair.time.time_ns() // 1_000_000
            authority = {
                "phase": "ACTIVE", "lease_generation": 7,
                "session_name": "paper-reboot-owner",
                "ttl_seconds": 900, "accepted_at_ms": now,
                "expires_at_ms": now + 900_000,
                "token_sha256":
                    "sha256:" + hashlib.sha256(raw).hexdigest(),
            }
            real_write = repair.os.write

            def fail_write(_descriptor: int, _payload: bytes) -> int:
                raise OSError("simulated torn write")

            identity = SimpleNamespace(
                pw_uid=os.geteuid(), pw_gid=os.getegid())
            with mock.patch.object(
                    repair.pwd, "getpwnam", return_value=identity), \
                    mock.patch.object(repair.os, "fchown"), \
                    mock.patch.object(
                        repair.os, "write", side_effect=fail_write), \
                    self.assertRaisesRegex(OSError, "simulated torn write"):
                repair._write_rematerialized_delivery_token(
                    token, authority, raw)
            self.assertFalse(token.exists())
            self.assertEqual(list(token.parent.glob(".*.tmp")), [])

            with mock.patch.object(
                    repair.pwd, "getpwnam", return_value=identity), \
                    mock.patch.object(repair.os, "fchown"):
                repair._write_rematerialized_delivery_token(
                    token, authority, raw)
            self.assertEqual(token.read_bytes(), raw)

            lease = repair.session_lease_path(token)
            with mock.patch.object(
                    repair.os, "write", side_effect=fail_write), \
                    mock.patch.object(repair.os, "fchown"), \
                    self.assertRaisesRegex(OSError, "simulated torn write"):
                repair._write_rematerialized_delivery_lease(
                    token, authority)
            self.assertFalse(lease.exists())
            self.assertEqual(list(lease.parent.glob(".*.tmp")), [])

            with mock.patch.object(repair.os, "write", wraps=real_write), \
                    mock.patch.object(repair.os, "fchown"):
                repair._write_rematerialized_delivery_lease(
                    token, authority)
            lease_value = json.loads(lease.read_text(encoding="ascii"))
            self.assertEqual(lease_value["lease_generation"], 7)
            self.assertEqual(
                lease_value["token_sha256"], authority["token_sha256"])

    def test_private_atomic_publish_never_overwrites_existing_final(
            self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "local-paper.token"
            original = b"existing-durable-material\n"
            path.write_bytes(original)
            path.chmod(0o600)
            with self.assertRaises(FileExistsError):
                repair._create_private_bytes_exclusive(
                    path, b"replacement\n", uid=os.geteuid(),
                    gid=os.getegid(), failure_prefix="TEST_PRIVATE_BYTES")
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(list(path.parent.glob(".*.tmp")), [])

    def test_rematerialized_remote_absence_is_fenced_before_fallback(
            self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            token, authority_root, record, _raw = (
                self.durable_active_session_fixture(
                    root,
                    expires_at_ms=(
                        repair.time.time_ns() // 1_000_000 + 3_600_000)))
            real_lstat = repair.os.lstat
            real_stat = repair.os.stat

            def as_root(metadata: object) -> SimpleNamespace:
                return SimpleNamespace(
                    st_dev=metadata.st_dev, st_ino=metadata.st_ino,
                    st_mode=metadata.st_mode, st_nlink=metadata.st_nlink,
                    st_uid=0, st_gid=0, st_size=metadata.st_size)

            def root_lstat(path: object) -> SimpleNamespace:
                return as_root(real_lstat(path))

            def root_stat(path: object, *args: object,
                          **kwargs: object) -> SimpleNamespace:
                return as_root(real_stat(path, *args, **kwargs))

            calls: list[list[str]] = []

            def command(arguments: list[str], timeout: int = 30) -> object:
                del timeout
                calls.append(arguments)
                if "tools" in arguments:
                    return SimpleNamespace(
                        returncode=4, stderr="", stdout=json.dumps({
                            "status": "rejected",
                            "reason_code": "SESSION_EXPIRED",
                        }))
                return SimpleNamespace(
                    returncode=4, stderr="", stdout=json.dumps({
                        "accepted": False,
                        "reason_code": "SESSION_LEASE_NOT_FOUND",
                        "lease_generation": 7,
                    }))

            with mock.patch.multiple(
                    repair,
                    TOKEN_FILE=token,
                    RISK_RECOVERY_TOKEN_ROOT=token.parent,
                    SESSION_AUTHORITY_ROOT=authority_root,
                    run=mock.Mock(side_effect=command)), \
                    mock.patch.object(
                        repair.pwd, "getpwnam", return_value=SimpleNamespace(
                            pw_uid=0, pw_gid=0)), \
                    mock.patch.object(
                        repair.os, "lstat", side_effect=root_lstat), \
                    mock.patch.object(
                        repair.os, "stat", side_effect=root_stat), \
                    mock.patch.object(
                        repair.os, "fstat", side_effect=self.root_fstat), \
                    mock.patch.object(repair.os, "fchown"):
                self.assertFalse(repair.session_usable(token))

            self.assertIn("tools", calls[0])
            self.assertIn("revoke", calls[1])
            self.assertEqual(
                calls[1][calls[1].index("--generation") + 1], "7")
            self.assertFalse(token.exists())
            self.assertFalse(repair.session_lease_path(token).exists())
            self.assertFalse(record.exists())

    def test_expired_durable_authority_is_fenced_before_rematerialize(
            self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            token, authority_root, record, _raw = (
                self.durable_active_session_fixture(
                    root,
                    expires_at_ms=(
                        repair.time.time_ns() // 1_000_000 - 1)))
            real_lstat = repair.os.lstat
            calls: list[list[str]] = []

            def root_lstat(path: object) -> SimpleNamespace:
                metadata = real_lstat(path)
                return SimpleNamespace(
                    st_dev=metadata.st_dev, st_ino=metadata.st_ino,
                    st_mode=metadata.st_mode, st_nlink=metadata.st_nlink,
                    st_uid=0, st_gid=0, st_size=metadata.st_size)

            def command(arguments: list[str], timeout: int = 30) -> object:
                del timeout
                calls.append(arguments)
                return SimpleNamespace(
                    returncode=4, stderr="", stdout=json.dumps({
                        "accepted": False,
                        "reason_code": "SESSION_LEASE_NOT_FOUND",
                        "lease_generation": 7,
                    }))

            with mock.patch.multiple(
                    repair,
                    TOKEN_FILE=token,
                    RISK_RECOVERY_TOKEN_ROOT=token.parent,
                    SESSION_AUTHORITY_ROOT=authority_root,
                    run=mock.Mock(side_effect=command)), \
                    mock.patch.object(
                        repair.pwd, "getpwnam", return_value=SimpleNamespace(
                            pw_uid=0, pw_gid=0)), \
                    mock.patch.object(
                        repair.os, "lstat", side_effect=root_lstat), \
                    mock.patch.object(
                        repair.os, "fstat", side_effect=self.root_fstat), \
                    mock.patch.object(repair.os, "fchown"):
                self.assertFalse(repair.session_usable(token))

            self.assertEqual(len(calls), 1)
            self.assertIn("revoke", calls[0])
            self.assertFalse(token.exists())
            self.assertFalse(record.exists())

    def test_crash_before_bearer_creation_cleans_local_only_intent(
            self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            token = root / "sessions" / "local-paper.token"
            token.parent.mkdir(parents=True)
            authority_root = root / "authority"
            authority_root.mkdir()
            token_raw = ("a" * 64 + "\n").encode("ascii")
            record_path = authority_root / (
                token.name + ".authority.json")
            record_path.write_text(json.dumps({
                "schema": "hepta.local-paper-session-provision-intent.v1",
                "phase": "TOKEN_PENDING", "token_name": token.name,
                "authority_bearer_name": token.name + ".revoke-token",
                "token_sha256":
                    "sha256:" + hashlib.sha256(token_raw).hexdigest(),
                "expected_lease_generation": 1,
                "lease_generation": None,
                "session_name": "paper-crash-test",
                "session_id": "paper-crash-test-id", "peer_uid": 2004,
                "ttl_seconds": 900, "created_at_ms": 1,
                "paper_only": True, "live_authorized": False,
            }) + "\n", encoding="ascii")
            record_path.chmod(0o600)
            real_lstat = repair.os.lstat

            def root_lstat(path: object) -> SimpleNamespace:
                metadata = real_lstat(path)
                return SimpleNamespace(
                    st_dev=metadata.st_dev, st_ino=metadata.st_ino,
                    st_mode=metadata.st_mode, st_nlink=metadata.st_nlink,
                    st_uid=0, st_gid=0, st_size=metadata.st_size)

            with mock.patch.multiple(
                    repair,
                    TOKEN_FILE=token,
                    RISK_RECOVERY_TOKEN_ROOT=token.parent,
                    SESSION_AUTHORITY_ROOT=authority_root), \
                    mock.patch.object(
                        repair.os, "lstat", side_effect=root_lstat), \
                    mock.patch.object(repair.os, "fchown"):
                evidence = repair._revoke_all_managed_sessions_after_zero()
            self.assertEqual(len(evidence), 1)
            self.assertEqual(evidence[0]["token_name"], token.name)
            self.assertTrue(evidence[0]["tool_session_revoked"])
            self.assertTrue(evidence[0]["tool_session_already_absent"])
            self.assertFalse(record_path.exists())

    def test_risk_checkpoint_remote_revoke_crash_retries_exact_absence(
            self) -> None:
        checkpoint = self.risk_recovery_checkpoint_fixture()
        persisted: list[dict[str, object]] = []
        persist = mock.Mock(side_effect=lambda value, **_kwargs:
            persisted.append(json.loads(json.dumps(value))))
        remote_revoke = mock.Mock(side_effect=KeyboardInterrupt(
            "simulated crash after remote revoke accepted"))
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(
                    repair, "RISK_RECOVERY_TOKEN_ROOT", Path(directory)), \
                mock.patch.object(
                    repair, "_persist_risk_recovery_checkpoint", persist), \
                mock.patch.object(
                    repair, "_revoke_recovery_session", remote_revoke), \
                mock.patch.object(
                    repair, "_unlink_bound_session_files") as unlink, \
                self.assertRaisesRegex(
                    KeyboardInterrupt, "after remote revoke accepted"):
            repair._revoke_risk_recovery_checkpoint_sessions(checkpoint)

        owner = checkpoint["sessions"][0]
        self.assertTrue(owner["revoke_retry_intent"])
        self.assertFalse(owner["revoked"])
        self.assertTrue(persisted[-1]["sessions"][0]["revoke_retry_intent"])
        remote_revoke.assert_called_once_with(
            Path(directory) / "local-paper.token", unlink=False,
            allow_already_absent=False)
        unlink.assert_not_called()

        retry_revoke = mock.Mock(return_value={
            "tool_session_revoked": True,
            "tool_session_lease_generation": 9,
            "tool_session_token_sha256": "sha256:" + "7" * 64,
            "tool_session_already_absent": True,
        })
        with tempfile.TemporaryDirectory() as retry_directory, \
                mock.patch.object(
                    repair, "RISK_RECOVERY_TOKEN_ROOT",
                    Path(retry_directory)), \
                mock.patch.object(
                    repair, "_persist_risk_recovery_checkpoint", persist), \
                mock.patch.object(
                    repair, "_revoke_recovery_session", retry_revoke), \
                mock.patch.object(
                    repair, "_unlink_bound_session_files") as retry_unlink, \
                mock.patch.object(
                    repair, "_validate_no_campaign_session_residue"):
            evidence = repair._revoke_risk_recovery_checkpoint_sessions(
                checkpoint)

        retry_revoke.assert_called_once_with(
            Path(retry_directory) / "local-paper.token", unlink=False,
            allow_already_absent=True)
        retry_unlink.assert_called_once_with(owner)
        self.assertEqual(checkpoint["phase"], "SESSIONS_REVOKED")
        self.assertTrue(owner["revoked"])
        self.assertTrue(owner["already_absent"])
        self.assertNotIn("revoke_retry_intent", owner)
        self.assertTrue(evidence[0]["tool_session_already_absent"])

    def test_risk_checkpoint_persists_revoke_proof_before_bearer_cleanup(
            self) -> None:
        checkpoint = self.risk_recovery_checkpoint_fixture()
        snapshots: list[dict[str, object]] = []

        def persist(value: dict[str, object], **_kwargs: object) -> None:
            snapshots.append(json.loads(json.dumps(value)))

        def crash_during_cleanup(raw: dict[str, object]) -> None:
            self.assertTrue(snapshots[-1]["sessions"][0]["revoked"])
            self.assertTrue(raw["revoked"])
            raise KeyboardInterrupt("simulated crash before bearer cleanup")

        remote_revoke = mock.Mock(return_value={
            "tool_session_revoked": True,
            "tool_session_lease_generation": 9,
            "tool_session_token_sha256": "sha256:" + "7" * 64,
        })
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(
                    repair, "RISK_RECOVERY_TOKEN_ROOT", Path(directory)), \
                mock.patch.object(
                    repair, "_persist_risk_recovery_checkpoint",
                    side_effect=persist), \
                mock.patch.object(
                    repair, "_revoke_recovery_session", remote_revoke), \
                mock.patch.object(
                    repair, "_unlink_bound_session_files",
                    side_effect=crash_during_cleanup), \
                self.assertRaisesRegex(
                    KeyboardInterrupt, "before bearer cleanup"):
            repair._revoke_risk_recovery_checkpoint_sessions(checkpoint)

        self.assertTrue(checkpoint["sessions"][0]["revoked"])
        self.assertNotIn(
            "revoke_retry_intent", checkpoint["sessions"][0])
        retry_revoke = mock.Mock()
        with tempfile.TemporaryDirectory() as retry_directory, \
                mock.patch.object(
                    repair, "RISK_RECOVERY_TOKEN_ROOT",
                    Path(retry_directory)), \
                mock.patch.object(
                    repair, "_persist_risk_recovery_checkpoint",
                    side_effect=persist), \
                mock.patch.object(
                    repair, "_revoke_recovery_session", retry_revoke), \
                mock.patch.object(
                    repair, "_unlink_bound_session_files") as retry_unlink, \
                mock.patch.object(
                    repair, "_validate_no_campaign_session_residue"):
            repair._revoke_risk_recovery_checkpoint_sessions(checkpoint)
        retry_revoke.assert_not_called()
        retry_unlink.assert_called_once_with(checkpoint["sessions"][0])
        self.assertEqual(checkpoint["phase"], "SESSIONS_REVOKED")

    def test_risk_checkpoint_resume_skips_all_session_and_broker_queries(
            self) -> None:
        state = self.incident_state()
        state.update({
            "recovery_complete": False,
            "recovery_phase": "REQUESTED",
            "pending_mutation_state_unproven": True,
        })
        checkpoint = self.risk_recovery_checkpoint_fixture(
            phase="SESSIONS_REVOKED")
        agent = SimpleNamespace()
        complete = mock.Mock()
        forbidden = mock.Mock(side_effect=AssertionError(
            "checkpoint resume attempted a session or broker query"))
        with mock.patch.object(repair, "run_checked"), \
                mock.patch.object(repair, "_ensure_risk_recovery_runtime"), \
                mock.patch.object(repair, "load_agent", return_value=agent), \
                mock.patch.object(
                    repair, "_load_root_agent_state", return_value=state), \
                mock.patch.object(repair, "read_env", return_value={
                    "HEPTA_LOCAL_AI_CAMPAIGN_ID": "old-campaign",
                    "HEPTA_LOCAL_AI_AUTH_GENERATION": "auth-generation-old",
                }), \
                mock.patch.object(
                    repair, "_load_risk_recovery_checkpoint",
                    return_value=checkpoint), \
                mock.patch.object(
                    repair, "_complete_risk_recovery_checkpoint", complete), \
                mock.patch.object(
                    repair, "_reconcile_pending_mutation_records",
                    forbidden), \
                mock.patch.object(
                    repair, "_select_risk_recovery_session", forbidden):
            repair._risk_recover_locked()

        complete.assert_called_once_with(agent, state, checkpoint)
        self.assertEqual(forbidden.call_count, 0)

    def test_risk_checkpoint_load_requires_exact_policy_and_suspension(
            self) -> None:
        checkpoint = self.risk_recovery_checkpoint_fixture()
        state = self.incident_state()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / (
                "risk-recovery-" + hashlib.sha256(
                    b"suspension-auth-rearm-test").hexdigest()[:24] +
                ".checkpoint.json")
            path.write_text(json.dumps(checkpoint), encoding="ascii")
            path.chmod(0o600)
            with mock.patch.object(
                    repair, "END_FLAT_RECEIPT_ROOT", root), \
                    mock.patch.object(
                        repair.os, "lstat", return_value=self.root_metadata()), \
                    mock.patch.object(
                        repair, "_risk_recovery_policy_sha256",
                        return_value="sha256:" + "9" * 64):
                loaded = repair._load_risk_recovery_checkpoint(
                    "old-campaign", "suspension-auth-rearm-test", state)
                self.assertEqual(loaded, checkpoint)
                drifted = dict(state)
                drifted["suspension_code"] = "DIFFERENT_INCIDENT"
                with self.assertRaisesRegex(
                        RuntimeError, "RISK_RECOVERY_CHECKPOINT_INVALID"):
                    repair._load_risk_recovery_checkpoint(
                        "old-campaign", "suspension-auth-rearm-test",
                        drifted)
            with mock.patch.object(
                    repair, "END_FLAT_RECEIPT_ROOT", root), \
                    mock.patch.object(
                        repair.os, "lstat", return_value=self.root_metadata()), \
                    mock.patch.object(
                        repair, "_risk_recovery_policy_sha256",
                        return_value="sha256:" + "8" * 64), \
                    self.assertRaisesRegex(
                        RuntimeError, "RISK_RECOVERY_CHECKPOINT_INVALID"):
                repair._load_risk_recovery_checkpoint(
                    "old-campaign", "suspension-auth-rearm-test", state)

    def test_pre_start_guard_consumes_one_boot_bound_permit_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pending = root / "start-permit.pending.json"
            claimed = root / "start-permit.claimed.json"
            consumed = root / "start-permit.consumed.json"
            boot = root / "boot-id"
            boot.write_text(
                "12345678-1234-1234-1234-123456789abc\n",
                encoding="ascii")
            prelaunch = root / "prelaunch-zero-test.receipt.json"
            hashes = {
                "policy_sha256": "sha256:" + "1" * 64,
                "agent_env_sha256": "sha256:" + "2" * 64,
                "state_sha256": "sha256:" + "3" * 64,
                "deadline_timer_sha256": "sha256:" + "4" * 64,
                "strategy_acceptance_sha256": "sha256:" + "c" * 64,
            }
            now = repair.time.time_ns() // 1_000_000
            runtime_binding = {"campaign_id": "campaign-test"}
            policy_expires_at_ms = now + 3_600_000
            prelaunch_value = {
                "schema":
                    "hepta.local-ai-paper-prelaunch-zero-receipt.v1",
                "campaign_id": "campaign-test",
                "completed_at_ms": now - 2,
                "runtime_binding": runtime_binding,
                "first_position_generation": 1,
                "first_fx_cash_generation": 1,
                "second_position_generation": 2,
                "second_fx_cash_generation": 2,
                "policy_expires_at_ms": policy_expires_at_ms,
                **hashes,
                "position": 0,
                "active_orders": 0,
                "gross_absolute_position": 0,
                "paper_only": True,
                "live_authorized": False,
            }
            prelaunch.write_text(json.dumps(
                prelaunch_value, ensure_ascii=True, sort_keys=True,
                separators=(",", ":")) + "\n", encoding="ascii")
            prelaunch.chmod(0o600)
            prelaunch_sha256 = "sha256:" + hashlib.sha256(
                prelaunch.read_bytes()).hexdigest()
            permit = {
                "schema": "hepta.local-ai-paper-start-permit.v1",
                "permit_id": "a" * 64,
                "unit": repair.AGENT_SERVICE,
                "boot_id": boot.read_text(encoding="ascii").strip(),
                "issued_at_ms": now - 1,
                "not_after_ms": now + 20_000,
                "campaign_id": "campaign-test",
                **hashes,
                "auth_rearm_receipt_sha256": "sha256:" + "5" * 64,
                "prelaunch_zero_receipt_sha256": prelaunch_sha256,
                "runtime_binding": runtime_binding,
                "policy_expires_at_ms": policy_expires_at_ms,
                "manual_start_required": True,
                "paper_only": True,
                "live_authorized": False,
            }
            pending.write_text(json.dumps(
                permit, ensure_ascii=True, sort_keys=True,
                separators=(",", ":")) + "\n", encoding="ascii")
            pending.chmod(0o600)
            real_lstat = repair.os.lstat

            def root_lstat(path: object) -> SimpleNamespace:
                metadata = real_lstat(path)
                return SimpleNamespace(
                    st_dev=metadata.st_dev, st_ino=metadata.st_ino,
                    st_mode=metadata.st_mode, st_nlink=metadata.st_nlink,
                    st_uid=0, st_gid=0, st_size=metadata.st_size)

            state = {
                "runtime_binding": runtime_binding,
                "auth_rearm_receipt_sha256": "sha256:" + "5" * 64,
            }
            values = {"HEPTA_LOCAL_AI_CAMPAIGN_ID": "campaign-test"}
            policy = {"expires_at_ms": permit["policy_expires_at_ms"]}
            validate_boundary = mock.Mock(
                return_value=(object(), values, policy, state))
            with mock.patch.multiple(
                    repair,
                    END_FLAT_RECEIPT_ROOT=root,
                    START_PERMIT_PENDING=pending,
                    START_PERMIT_CLAIMED=claimed,
                    START_PERMIT_CONSUMED=consumed,
                    BOOT_ID_PATH=boot,
                    _validate_campaign_start_boundary=validate_boundary,
                    _verify_waiting_timer=mock.Mock(),
                    _verify_deadline_timer=mock.Mock(),
                    _verify_start_dependencies=mock.Mock(),
                    _start_boundary_hashes=mock.Mock(return_value=hashes)), \
                    mock.patch.object(
                        repair.os, "lstat", side_effect=root_lstat), \
                    mock.patch.object(repair.os, "fchown"), \
                    mock.patch.dict(
                        repair.os.environ, {"INVOCATION_ID": "b" * 32}):
                repair.pre_start_guard()
                with self.assertRaises(FileNotFoundError):
                    repair.pre_start_guard()
            validate_boundary.assert_called_once_with(
                require_session_authority=False)
            self.assertFalse(pending.exists())
            self.assertTrue(claimed.exists())
            consumed_value = json.loads(consumed.read_text(encoding="ascii"))
            self.assertEqual(consumed_value["permit_id"], "a" * 64)
            self.assertEqual(consumed_value["invocation_id"], "b" * 32)

    def test_exclusive_root_json_publish_is_atomic_and_no_replace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "start-permit.pending.json"
            first = {"permit_id": "a" * 64}
            with mock.patch.object(repair.os, "fchown"), \
                    mock.patch.object(
                        repair.os, "fstat", side_effect=self.root_fstat):
                repair._create_root_json_exclusive(path, first)
                original = path.read_bytes()
                with self.assertRaises(FileExistsError):
                    repair._create_root_json_exclusive(
                        path, {"permit_id": "b" * 64})
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(
                json.loads(original), first)
            self.assertEqual(list(root.glob(".*.tmp")), [])

    def test_exclusive_root_json_write_failure_never_publishes_torn_file(
            self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "start-permit.consumed.json"
            real_write = repair.os.write
            calls = 0

            def torn_write(descriptor: int, payload: bytes) -> int:
                nonlocal calls
                calls += 1
                if calls == 1:
                    return real_write(
                        descriptor, payload[:max(1, len(payload) // 2)])
                raise OSError("simulated torn permit write")

            with mock.patch.object(repair.os, "fchown"), \
                    mock.patch.object(
                        repair.os, "fstat", side_effect=self.root_fstat), \
                    mock.patch.object(
                        repair.os, "write", side_effect=torn_write), \
                    self.assertRaisesRegex(
                        OSError, "simulated torn permit write"):
                repair._create_root_json_exclusive(
                    path, {"permit_id": "a" * 64})
            self.assertFalse(path.exists())
            self.assertEqual(list(root.glob(".*.tmp")), [])

    def test_automatic_risk_marker_torn_write_never_publishes_final(
            self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "automatic-risk-attempt.json"
            real_write = repair.os.write
            calls = 0

            def torn_write(descriptor: int, payload: bytes) -> int:
                nonlocal calls
                calls += 1
                if calls == 1:
                    return real_write(descriptor, payload[:8])
                raise OSError("simulated automatic marker tear")

            state = {
                "schema": FakeAgent.SCHEMA,
                "suspension_id": "suspension-test",
            }
            with mock.patch.object(
                    repair, "AUTOMATIC_RISK_ATTEMPT", marker), \
                    mock.patch.object(
                        repair, "_automatic_risk_recovery_consumed",
                        return_value=False), \
                    mock.patch.object(repair.os, "fchown"), \
                    mock.patch.object(
                        repair.os, "write", side_effect=torn_write), \
                    self.assertRaisesRegex(
                        OSError, "simulated automatic marker tear"):
                repair._consume_automatic_risk_recovery_attempt(
                    state, "flatten")
            self.assertFalse(marker.exists())
            self.assertEqual(list(marker.parent.glob(".*.tmp")), [])

    def test_start_permit_cleanup_reproves_terminal_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy_path = root / "policy.json"
            policy = {
                "schema": "hepta.ib-paper-campaign-policy.v4",
                "campaign_id": "campaign-test", "domain_id": "alpha",
                "enabled": False, "mutations_authorized": False,
                "paper_only": True, "live_authorized": False,
            }
            policy_path.write_text(json.dumps(policy), encoding="ascii")
            policy_path.chmod(0o600)
            broker_hash = "sha256:" + "a" * 64
            checkpoint = {
                "phase": "EGRESS_REVOKED",
                "sessions": [{"revoked": True}],
                "broker_policy_sha256": broker_hash,
            }
            real_lstat = repair.os.lstat

            def root_lstat(path: object) -> SimpleNamespace:
                metadata = real_lstat(path)
                return SimpleNamespace(
                    st_mode=metadata.st_mode, st_nlink=metadata.st_nlink,
                    st_uid=0, st_gid=0, st_size=metadata.st_size)

            with mock.patch.multiple(
                    repair, CAMPAIGN_POLICY=policy_path,
                    _load_end_flat_checkpoint=mock.Mock(
                        return_value=checkpoint),
                    _validated_end_flat_receipt=mock.Mock(return_value=None),
                    _validate_no_campaign_session_residue=mock.Mock(),
                    _end_flat_verify_deny_all=mock.Mock(return_value={
                        "broker_policy_sha256": broker_hash,
                        "authorized_connector_count": 0,
                    }),
                    _end_flat_verify_runtime_stopped=mock.Mock()), \
                    mock.patch.object(
                        repair.os, "lstat", side_effect=root_lstat):
                repair._prove_start_permit_cleanup_boundary("campaign-test")
                policy["enabled"] = True
                policy["mutations_authorized"] = True
                policy_path.write_text(json.dumps(policy), encoding="ascii")
                with self.assertRaisesRegex(
                        RuntimeError,
                        "END_FLAT_PERMIT_CLEANUP_POLICY_NOT_DISABLED"):
                    repair._prove_start_permit_cleanup_boundary(
                        "campaign-test")

    def test_terminal_cleanup_removes_metadata_safe_torn_permits(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pending = root / "start-permit.pending.json"
            claimed = root / "start-permit.claimed.json"
            consumed = root / "start-permit.consumed.json"
            pending.write_bytes(b"")
            claimed.write_bytes(b'{"schema":')
            pending.chmod(0o600)
            claimed.chmod(0o600)
            with mock.patch.multiple(
                    repair,
                    START_PERMIT_PENDING=pending,
                    START_PERMIT_CLAIMED=claimed,
                    START_PERMIT_CONSUMED=consumed,
                    _prove_start_permit_cleanup_boundary=mock.Mock()), \
                    mock.patch.object(
                        repair.os, "fstat", side_effect=self.root_fstat):
                self.assertEqual(
                    repair._seal_start_permit_residue("campaign-test"), 2)
            self.assertFalse(pending.exists())
            self.assertFalse(claimed.exists())

    def test_torn_permit_is_preserved_until_terminal_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pending = root / "start-permit.pending.json"
            pending.write_bytes(b"")
            pending.chmod(0o600)
            with mock.patch.multiple(
                    repair,
                    START_PERMIT_PENDING=pending,
                    START_PERMIT_CLAIMED=root / "claimed.json",
                    START_PERMIT_CONSUMED=root / "consumed.json",
                    _prove_start_permit_cleanup_boundary=mock.Mock(
                        side_effect=RuntimeError("terminal unproven"))), \
                    self.assertRaisesRegex(
                        RuntimeError, "terminal unproven"):
                repair._seal_start_permit_residue("campaign-test")
            self.assertTrue(pending.exists())

    def test_orphan_permit_recheck_is_boot_and_expiry_bound(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pending = root / "start-permit.pending.json"
            claimed = root / "start-permit.claimed.json"
            consumed = root / "start-permit.consumed.json"
            boot = root / "boot-id"
            boot.write_text(
                "12345678-1234-1234-1234-123456789abc\n",
                encoding="ascii")
            now = repair.time.time_ns() // 1_000_000
            permit = {
                "schema": "hepta.local-ai-paper-start-permit.v1",
                "permit_id": "a" * 64,
                "campaign_id": "campaign-test",
                "boot_id": boot.read_text(encoding="ascii").strip(),
                "issued_at_ms": now - 1,
                "not_after_ms": now + 20_000,
                "paper_only": True,
                "live_authorized": False,
            }
            pending.write_text(json.dumps(permit), encoding="ascii")
            pending.chmod(0o600)
            real_lstat = repair.os.lstat

            def root_lstat(path: object) -> SimpleNamespace:
                metadata = real_lstat(path)
                return SimpleNamespace(
                    st_mode=metadata.st_mode, st_nlink=metadata.st_nlink,
                    st_uid=0, st_gid=0, st_size=metadata.st_size)

            with mock.patch.multiple(
                    repair,
                    START_PERMIT_PENDING=pending,
                    START_PERMIT_CLAIMED=claimed,
                    START_PERMIT_CONSUMED=consumed,
                    BOOT_ID_PATH=boot), \
                    mock.patch.object(
                        repair.os, "lstat", side_effect=root_lstat):
                self.assertFalse(repair._terminal_orphan_start_permit(
                    "campaign-test"))
                permit["not_after_ms"] = now - 1
                permit["issued_at_ms"] = now - 20_000
                pending.write_text(json.dumps(permit), encoding="ascii")
                self.assertTrue(repair._terminal_orphan_start_permit(
                    "campaign-test"))
                permit["boot_id"] = boot.read_text(
                    encoding="ascii").strip()
                permit["not_after_ms"] = now + 20_000
                permit["issued_at_ms"] = now - 1
                pending.write_text(json.dumps(permit), encoding="ascii")
                pending.rename(claimed)
                self.assertTrue(repair._terminal_orphan_start_permit(
                    "campaign-test"))
                claimed.unlink()
                permit["not_after_ms"] = now + 20_000
                permit["issued_at_ms"] = now - 1
                permit["boot_id"] = (
                    "87654321-4321-4321-4321-cba987654321")
                pending.write_text(json.dumps(permit), encoding="ascii")
                pending.chmod(0o600)
                self.assertTrue(repair._terminal_orphan_start_permit(
                    "campaign-test"))

    def test_orphan_request_rechecks_after_launcher_lock_release(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "request.json"
            marker.write_bytes(b"durable request\n")
            common = {
                "_campaign_lifecycle_locks": mock.Mock(
                    return_value=contextlib.nullcontext()),
                "read_env": mock.Mock(return_value={
                    "HEPTA_LOCAL_AI_CAMPAIGN_ID": "campaign-test"}),
                "_terminal_orphan_start_permit": mock.Mock(return_value=True),
                "_ensure_end_flat_request_marker": mock.Mock(
                    return_value=marker),
            }
            with mock.patch.multiple(
                    repair, **common,
                    _unit_is_active=mock.Mock(return_value=False)):
                self.assertTrue(repair.request_end_flat_if_orphan_start())
            with mock.patch.multiple(
                    repair, **common,
                    _unit_is_active=mock.Mock(return_value=True)):
                self.assertFalse(repair.request_end_flat_if_orphan_start())
            common["_ensure_end_flat_request_marker"].assert_called_once_with(
                "campaign-test")

    def test_strategy_acceptance_is_campaign_runtime_and_time_bound(
            self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "strategy-acceptance-state.json"
            runtime = {"campaign_id": "campaign-test", "epoch": "e1"}
            intent_id = "9" * 64
            value = {
                "schema": "hepta.local-ai-paper-agent-state.v3",
                "entries": 1, "exits": 1,
                "last_order_result": "ECONOMIC_FLATTEN_CONFIRMED",
                "recovery_required": False,
                "trading_suspended": False,
                "pending_order_id": None,
                "last_exit_trigger": {
                    "trigger": "MODEL_REVERSAL",
                    "result": "ECONOMIC_FLATTEN_CONFIRMED",
                    "position_after": 0.0,
                },
                "strategy_acceptance_intent_id": intent_id,
                "strategy_acceptance_campaign_id": "campaign-test",
                "strategy_acceptance_cycle_consumed": 1,
                "strategy_cycle_budget": 3,
                "strategy_acceptance_campaign_cycles_opened": 1,
                "strategy_acceptance_campaign_cycles_closed": 1,
                "strategy_acceptance_performance_included": False,
                "strategy_acceptance_runtime_binding": runtime,
                "strategy_acceptance_completed_at_ms": 200,
                "strategy_acceptance_position_generation": 7,
                "strategy_acceptance_fx_cash_generation": 8,
                "strategy_acceptance_gross_absolute_position": 0,
                "strategy_acceptance_paper_only": True,
                "strategy_acceptance_live_authorized": False,
            }
            state_raw = (json.dumps(
                value, ensure_ascii=True, sort_keys=True,
                separators=(",", ":")) + "\n").encode("ascii")
            policy = {
                "campaign_id": "campaign-test", "valid_after_ms": 100,
                "max_cycles": 4,
            }
            intent_path = root / (
                "strategy-acceptance-campaign-test.intent.json")
            immutable_state = root / (
                "strategy-acceptance-campaign-test.state.json")
            receipt_path = root / (
                "strategy-acceptance-campaign-test.receipt.json")
            intent = {
                "schema": repair.STRATEGY_ACCEPTANCE_INTENT_SCHEMA,
                "intent_id": intent_id,
                "campaign_id": "campaign-test",
                "auth_generation": "generation-test",
                "created_at_ms": 125,
                "one_shot": True,
                "failure_requires_end_flat": True,
                "fresh_campaign_required_after_failure": True,
                "paper_only": True,
                "live_authorized": False,
            }
            acceptance_receipt = {
                "schema": repair.STRATEGY_ACCEPTANCE_RECEIPT_SCHEMA,
                "intent_id": intent_id,
                "campaign_id": "campaign-test",
                "completed_at_ms": 200,
                "runtime_binding": runtime,
                "state_sha256": "sha256:" + hashlib.sha256(
                    state_raw).hexdigest(),
                "policy_max_cycles": 4,
                "acceptance_cycle_consumed": 1,
                "strategy_cycle_budget": 3,
                "campaign_cycles_opened": 1,
                "campaign_cycles_closed": 1,
                "acceptance_performance_included": False,
                "position": 0,
                "active_orders": 0,
                "gross_absolute_position": 0,
                "paper_only": True,
                "live_authorized": False,
            }
            for target, raw in (
                    (path, state_raw), (immutable_state, state_raw),
                    (intent_path, (json.dumps(
                        intent, ensure_ascii=True, sort_keys=True,
                        separators=(",", ":")) + "\n").encode("ascii")),
                    (receipt_path, (json.dumps(
                        acceptance_receipt, ensure_ascii=True, sort_keys=True,
                        separators=(",", ":")) + "\n").encode("ascii"))):
                target.write_bytes(raw)
                target.chmod(0o600)
            real_lstat = repair.os.lstat

            def root_lstat(target: object) -> SimpleNamespace:
                metadata = real_lstat(target)
                return SimpleNamespace(
                    st_mode=metadata.st_mode, st_nlink=metadata.st_nlink,
                    st_uid=0, st_gid=0, st_size=metadata.st_size)

            state = {"runtime_binding": runtime}
            receipt = {"completed_at_ms": 150}
            with mock.patch.multiple(
                    repair, STRATEGY_ACCEPTANCE_STATE=path,
                    END_FLAT_RECEIPT_ROOT=root), \
                    mock.patch.object(
                        repair.os, "lstat", side_effect=root_lstat), \
                    mock.patch.object(
                        repair.time, "time_ns", return_value=250_000_000):
                observed = repair._verified_strategy_acceptance(
                    policy, state, receipt)
                self.assertEqual(observed, value)
                acceptance_receipt["strategy_cycle_budget"] = 4
                receipt_path.write_text(json.dumps(
                    acceptance_receipt, ensure_ascii=True, sort_keys=True,
                    separators=(",", ":")) + "\n", encoding="ascii")
                with self.assertRaisesRegex(
                        RuntimeError, "STRATEGY_ACCEPTANCE_INVALID"):
                    repair._verified_strategy_acceptance(
                        policy, state, receipt)

    def test_external_p1_strategy_acceptance_begin_has_no_side_effects(
            self) -> None:
        policy = {"admission_mode": "external-p1-finalized"}
        load_state = mock.Mock()
        residue = mock.Mock()
        write = mock.Mock()
        create = mock.Mock()
        read_env = mock.Mock()
        load_agent = mock.Mock()
        with mock.patch.multiple(
                repair,
                _campaign_policy_for_control=mock.Mock(return_value=policy),
                _raw_strategy_acceptance_rearm_state=load_state,
                _strategy_acceptance_current_campaign_residue=residue,
                _write_root_json=write,
                _create_root_json_exclusive=create,
                read_env=read_env,
                load_agent=load_agent), \
                self.assertRaisesRegex(
                    RuntimeError,
                    "STRATEGY_ACCEPTANCE_EXTERNAL_P1_FORBIDDEN"):
            repair._begin_strategy_acceptance()
        load_state.assert_not_called()
        residue.assert_not_called()
        write.assert_not_called()
        create.assert_not_called()
        read_env.assert_not_called()
        load_agent.assert_not_called()

    def test_external_p1_strategy_acceptance_cannot_reach_legacy_mkt(
            self) -> None:
        values = {"HEPTA_LOCAL_AI_CAMPAIGN_ID": "campaign-external"}
        policy = {"admission_mode": "external-p1-finalized"}
        agent = mock.Mock()
        verify_timer = mock.Mock()
        create = mock.Mock()
        authoritative = mock.Mock()
        with mock.patch.multiple(
                repair,
                _unit_is_active=mock.Mock(return_value=False),
                _validated_prepared_campaign=mock.Mock(
                    return_value=(values, policy)),
                _verify_waiting_timer=verify_timer,
                _create_root_json_exclusive=create,
                authoritative_state=authoritative), \
                self.assertRaisesRegex(
                    RuntimeError,
                    "STRATEGY_ACCEPTANCE_EXTERNAL_P1_FORBIDDEN"):
            repair._strategy_acceptance_locked(
                0.0, agent, values, {}, {}, Path("unused-state"),
                Path("unused-receipt"))
        verify_timer.assert_not_called()
        create.assert_not_called()
        authoritative.assert_not_called()
        agent.fresh_quote.assert_not_called()
        agent.apply_decision.assert_not_called()

    def test_strategy_acceptance_preboundary_failure_is_durably_latched(
            self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            main_state = root / "state.json"
            acceptance_state = root / "strategy-acceptance-state.json"
            runtime = {"campaign_id": "campaign-test"}
            rearmed = FakeAgent.empty_state()
            rearmed.update({
                "recovery_required": False,
                "trading_suspended": False,
                "pending_order_id": None,
                "manual_start_required": True,
                "runtime_binding": runtime,
                "auth_generation_rearmed": "generation-test",
            })
            main_state.write_text(json.dumps(rearmed), encoding="ascii")
            main_state.chmod(0o600)
            values = {
                "HEPTA_LOCAL_AI_CAMPAIGN_ID": "campaign-test",
                "HEPTA_LOCAL_AI_AUTH_GENERATION": "generation-test",
            }
            agent = SimpleNamespace(
                empty_state=FakeAgent.empty_state,
                load_state=lambda _path: dict(rearmed))
            real_lstat = repair.os.lstat

            def root_lstat(target: object) -> SimpleNamespace:
                metadata = real_lstat(target)
                return SimpleNamespace(
                    st_mode=metadata.st_mode, st_nlink=metadata.st_nlink,
                    st_uid=0, st_gid=0, st_size=metadata.st_size)

            recovery = mock.Mock()
            with mock.patch.multiple(
                    repair,
                    END_FLAT_RECEIPT_ROOT=root,
                    AGENT_STATE=main_state,
                    STRATEGY_ACCEPTANCE_STATE=acceptance_state,
                    _require_legacy_strategy_acceptance_policy=mock.Mock(),
                    load_agent=mock.Mock(return_value=agent),
                    read_env=mock.Mock(return_value=values),
                    _validate_strategy_acceptance_boundary=mock.Mock(
                        side_effect=RuntimeError("deadline invalid")),
                    _force_safe_recovery_after_admission_failure=recovery), \
                    mock.patch.object(
                        repair.os, "lstat", side_effect=root_lstat), \
                    mock.patch.object(repair.os, "fchown"), \
                    self.assertRaisesRegex(
                        RuntimeError, "deadline invalid"):
                repair.strategy_acceptance(0.0)
            latched = json.loads(main_state.read_text(encoding="ascii"))
            self.assertTrue(latched["recovery_required"])
            self.assertTrue(latched["trading_suspended"])
            self.assertEqual(
                latched["suspension_code"],
                "STRATEGY_ACCEPTANCE_ADMISSION_LATCHED")
            self.assertTrue(latched["manual_start_required"])
            intent_paths = list(root.glob(
                "strategy-acceptance-campaign-test.intent.json"))
            self.assertEqual(len(intent_paths), 1)
            intent = json.loads(intent_paths[0].read_text(encoding="ascii"))
            self.assertEqual(
                intent["intent_id"], latched["strategy_acceptance_intent_id"])
            recovery.assert_called_once_with()

    def test_strategy_acceptance_crash_before_intent_is_not_replayable(
            self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            main_state = root / "state.json"
            runtime = {"campaign_id": "campaign-test"}
            rearmed = FakeAgent.empty_state()
            rearmed.update({
                "manual_start_required": True,
                "runtime_binding": runtime,
                "auth_generation_rearmed": "generation-test",
            })
            main_state.write_text(json.dumps(rearmed), encoding="ascii")
            main_state.chmod(0o600)
            values = {
                "HEPTA_LOCAL_AI_CAMPAIGN_ID": "campaign-test",
                "HEPTA_LOCAL_AI_AUTH_GENERATION": "generation-test",
            }
            agent = SimpleNamespace(
                load_state=lambda path: json.loads(path.read_text(
                    encoding="ascii")))
            real_lstat = repair.os.lstat

            def root_lstat(target: object) -> SimpleNamespace:
                metadata = real_lstat(target)
                return SimpleNamespace(
                    st_mode=metadata.st_mode, st_nlink=metadata.st_nlink,
                    st_uid=0, st_gid=0, st_size=metadata.st_size)

            with mock.patch.multiple(
                    repair, END_FLAT_RECEIPT_ROOT=root,
                    AGENT_STATE=main_state,
                    STRATEGY_ACCEPTANCE_STATE=root / "acceptance-cache.json",
                    _require_legacy_strategy_acceptance_policy=mock.Mock(),
                    load_agent=mock.Mock(return_value=agent),
                    read_env=mock.Mock(return_value=values)), \
                    mock.patch.object(
                        repair.os, "lstat", side_effect=root_lstat), \
                    mock.patch.object(repair.os, "fchown"), \
                    mock.patch.object(
                        repair, "_create_root_json_exclusive",
                        side_effect=RuntimeError("crash before intent")), \
                    self.assertRaisesRegex(
                        RuntimeError, "crash before intent"):
                repair._begin_strategy_acceptance()
            latched = json.loads(main_state.read_text(encoding="ascii"))
            self.assertEqual(
                latched["strategy_acceptance_intent_campaign_id"],
                "campaign-test")
            with mock.patch.multiple(
                    repair, END_FLAT_RECEIPT_ROOT=root,
                    AGENT_STATE=main_state,
                    STRATEGY_ACCEPTANCE_STATE=root / "acceptance-cache.json",
                    _require_legacy_strategy_acceptance_policy=mock.Mock(),
                    load_agent=mock.Mock(return_value=agent),
                    read_env=mock.Mock(return_value=values)), \
                    mock.patch.object(
                        repair.os, "lstat", side_effect=root_lstat), \
                    self.assertRaisesRegex(
                        RuntimeError, "STRATEGY_ACCEPTANCE_ALREADY_ATTEMPTED"):
                repair._begin_strategy_acceptance()

    def test_any_current_campaign_acceptance_artifact_blocks_replay(
            self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = {"runtime_binding": {"campaign_id": "campaign-test"}}
            with mock.patch.object(
                    repair, "END_FLAT_RECEIPT_ROOT", root), \
                    mock.patch.object(
                        repair, "STRATEGY_ACCEPTANCE_STATE",
                        root / "acceptance-cache.json"):
                for path in repair._strategy_acceptance_artifact_paths(
                        "campaign-test"):
                    path.write_text("{}\n", encoding="ascii")
                    with self.assertRaisesRegex(
                            RuntimeError,
                            "STRATEGY_ACCEPTANCE_ALREADY_ATTEMPTED"):
                        repair._strategy_acceptance_current_campaign_residue(
                            "campaign-test", state)
                    path.unlink()

    def test_repair_owned_admission_fields_survive_agent_normalization(
            self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            state_path.write_text(json.dumps({
                "schema": FakeAgent.SCHEMA,
                "rearm_stack_receipt_sha256": "sha256:" + "a" * 64,
                "strategy_acceptance_intent_id": "b" * 64,
                "strategy_acceptance_intent_campaign_id": "campaign-test",
            }), encoding="ascii")
            normalized = FakeAgent.empty_state()
            with mock.patch.object(repair, "AGENT_STATE", state_path):
                repair._merge_repair_owned_agent_state_fields(normalized)
            self.assertEqual(
                normalized["rearm_stack_receipt_sha256"],
                "sha256:" + "a" * 64)
            self.assertEqual(
                normalized["strategy_acceptance_intent_id"], "b" * 64)
            self.assertEqual(
                normalized["strategy_acceptance_intent_campaign_id"],
                "campaign-test")

    def test_successful_acceptance_replay_latches_then_forces_terminal_end(
            self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            main_state = root / "state.json"
            runtime = {"campaign_id": "campaign-test"}
            rearmed = FakeAgent.empty_state()
            rearmed.update({
                "manual_start_required": True,
                "runtime_binding": runtime,
                "auth_generation_rearmed": "generation-test",
            })
            main_state.write_text(json.dumps(rearmed), encoding="ascii")
            main_state.chmod(0o600)
            intent_path = root / (
                "strategy-acceptance-campaign-test.intent.json")
            intent_path.write_text("{}\n", encoding="ascii")
            values = {
                "HEPTA_LOCAL_AI_CAMPAIGN_ID": "campaign-test",
                "HEPTA_LOCAL_AI_AUTH_GENERATION": "generation-test",
            }
            agent = SimpleNamespace(load_state=lambda _path: dict(rearmed))
            real_lstat = repair.os.lstat

            def root_lstat(target: object) -> SimpleNamespace:
                metadata = real_lstat(target)
                return SimpleNamespace(
                    st_mode=metadata.st_mode, st_nlink=metadata.st_nlink,
                    st_uid=0, st_gid=0, st_size=metadata.st_size)

            recovery = mock.Mock()
            with mock.patch.multiple(
                    repair, END_FLAT_RECEIPT_ROOT=root,
                    AGENT_STATE=main_state,
                    STRATEGY_ACCEPTANCE_STATE=root / "acceptance-cache.json",
                    _require_legacy_strategy_acceptance_policy=mock.Mock(),
                    load_agent=mock.Mock(return_value=agent),
                    read_env=mock.Mock(return_value=values),
                    _force_safe_recovery_after_admission_failure=recovery), \
                    mock.patch.object(
                        repair.os, "lstat", side_effect=root_lstat), \
                    mock.patch.object(repair.os, "fchown"), \
                    self.assertRaisesRegex(
                        RuntimeError, "STRATEGY_ACCEPTANCE_ALREADY_ATTEMPTED"):
                repair.strategy_acceptance(0.0)
            latched = json.loads(main_state.read_text(encoding="ascii"))
            self.assertEqual(
                latched["suspension_code"],
                "STRATEGY_ACCEPTANCE_REPLAY_REJECTED")
            self.assertTrue(latched["recovery_required"])
            self.assertEqual(intent_path.read_text(encoding="ascii"), "{}\n")
            recovery.assert_called_once_with()

    def test_acceptance_agent_load_failure_occurs_after_durable_intent(
            self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            main_state = root / "state.json"
            rearmed = FakeAgent.empty_state()
            rearmed.update({
                "manual_start_required": True,
                "runtime_binding": {"campaign_id": "campaign-test"},
                "auth_generation_rearmed": "generation-test",
            })
            main_state.write_text(json.dumps(rearmed), encoding="ascii")
            main_state.chmod(0o600)
            values = {
                "HEPTA_LOCAL_AI_CAMPAIGN_ID": "campaign-test",
                "HEPTA_LOCAL_AI_AUTH_GENERATION": "generation-test",
            }
            real_lstat = repair.os.lstat

            def root_lstat(target: object) -> SimpleNamespace:
                metadata = real_lstat(target)
                return SimpleNamespace(
                    st_mode=metadata.st_mode, st_nlink=metadata.st_nlink,
                    st_uid=0, st_gid=0, st_size=metadata.st_size)

            recovery = mock.Mock()
            with mock.patch.multiple(
                    repair, END_FLAT_RECEIPT_ROOT=root,
                    AGENT_STATE=main_state,
                    STRATEGY_ACCEPTANCE_STATE=root / "acceptance-cache.json",
                    _require_legacy_strategy_acceptance_policy=mock.Mock(),
                    read_env=mock.Mock(return_value=values),
                    load_agent=mock.Mock(
                        side_effect=RuntimeError("agent module broken")),
                    _force_safe_recovery_after_admission_failure=recovery), \
                    mock.patch.object(
                        repair.os, "lstat", side_effect=root_lstat), \
                    mock.patch.object(repair.os, "fchown"), \
                    self.assertRaisesRegex(
                        RuntimeError, "agent module broken"):
                repair.strategy_acceptance(0.0)
            latched = json.loads(main_state.read_text(encoding="ascii"))
            self.assertEqual(
                latched["suspension_code"],
                "STRATEGY_ACCEPTANCE_ADMISSION_LATCHED")
            self.assertEqual(len(list(root.glob(
                "strategy-acceptance-campaign-test.intent.json"))), 1)
            recovery.assert_called_once_with()

    def test_strategy_acceptance_failure_forces_terminal_recovery(self) -> None:
        recovery = mock.Mock()
        admission = (object(), {}, {}, {}, Path("state"), Path("receipt"))
        with mock.patch.object(
                repair, "_begin_strategy_acceptance",
                return_value=admission), \
                mock.patch.object(
                repair, "_strategy_acceptance_locked",
                side_effect=RuntimeError("acceptance failed")), \
                mock.patch.object(
                    repair, "_force_safe_recovery_after_admission_failure",
                    recovery), \
                self.assertRaisesRegex(RuntimeError, "acceptance failed"):
            repair.strategy_acceptance(0.0)
        recovery.assert_called_once_with()

    def test_legacy_acceptance_is_fail_closed(self) -> None:
        with mock.patch.object(repair, "load_agent") as load_agent, \
                self.assertRaisesRegex(
                    RuntimeError,
                    "LEGACY_ACCEPTANCE_DISABLED_USE_STRATEGY_ACCEPTANCE"):
            repair.acceptance(0.0)
        load_agent.assert_not_called()

    def test_rearm_stack_rollback_revokes_before_deny_all_and_stop(
            self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            events: list[str] = []
            authority_present = False
            authority = {
                "schema":
                    "hepta.local-paper-session-provision-intent.v1",
                "phase": "ACTIVE", "lease_generation": 1,
                "token_sha256": "sha256:" + "1" * 64,
            }

            def load_authority(_token_file: Path) -> object:
                return authority if authority_present else None

            def provision(*_arguments: object) -> None:
                nonlocal authority_present
                events.append("provision")
                authority_present = True

            def revoke(*_arguments: object, **_keywords: object) -> None:
                events.append("revoke")
                raise RuntimeError("revoke uncertain")

            def checked(command: list[str], timeout: int = 30) -> str:
                del timeout
                if command[:2] == ["/usr/bin/systemctl", "enable"] or \
                        command[:2] == ["/usr/bin/systemctl", "start"]:
                    events.append(command[1])
                    return ""
                if command[:2] == [repair.LOCAL_PAPER_CONTROL, "enable"]:
                    events.append("enable")
                    return json.dumps({
                        "mode": "LOCAL_PAPER", "domain": "alpha",
                        "paper_authorized": True,
                        "live_authorized": False,
                        "admission_mode": "local-only",
                        "identity_manifest_sha256":
                            "sha256:" + "2" * 64,
                    })
                if command[:3] == [
                        "/usr/bin/systemctl", "stop",
                        repair.END_FLAT_EXECUTION_UNITS[0]]:
                    events.append("stop")
                    return ""
                raise AssertionError(command)

            values = {
                "HEPTA_LOCAL_AI_CAMPAIGN_ID": "campaign-test",
                "HEPTA_LOCAL_AI_STRATEGY_ID": "strategy-test",
                "HEPTA_LOCAL_AI_STRATEGY_VERSION": "1",
                "HEPTA_LOCAL_AI_STRATEGY_SHA256": "sha256:" + "3" * 64,
                "HEPTA_LOCAL_AI_AUTH_GENERATION": "generation-test",
                "HEPTA_LOCAL_AI_AUTH_PROFILE_ID": "openai:test-profile",
            }
            with mock.patch.multiple(
                    repair,
                    CAMPAIGN_LIFECYCLE_LOCK=root / "lifecycle.lock",
                    RISK_RECOVERY_LOCK=root / "risk.lock",
                    END_FLAT_LOCK=root / "end-flat.lock",
                    TOKEN_FILE=root / "local-paper.token",
                    RISK_RECOVERY_TOKEN_ROOT=root,
                    SESSION_AUTHORITY_ROOT=root / "session-authority",
                    _unit_is_active=mock.Mock(return_value=False),
                    _load_session_provision_intent=mock.Mock(
                        side_effect=load_authority),
                    _resolve_session_provision_intent=mock.Mock(
                        side_effect=revoke),
                    provision_session=mock.Mock(side_effect=provision),
                    run_checked=mock.Mock(side_effect=checked),
                    _end_flat_revoke_local_paper_control=mock.Mock(
                        side_effect=lambda: events.append("deny-all")),
                    _end_flat_verify_deny_all=mock.Mock(
                        side_effect=lambda: events.append("deny-verified")),
                    _end_flat_verify_runtime_stopped=mock.Mock(
                        side_effect=lambda: events.append("stopped-verified")),
                    load_agent=mock.Mock(return_value=FakeAgent()),
                    _load_root_agent_state=mock.Mock(return_value={}),
                    read_env=mock.Mock(return_value=values),
                    _validated_prepared_campaign=mock.Mock(return_value=(
                        values, {
                            "campaign_id": "campaign-test",
                            "admission_mode": "local-only",
                        })),
                    _current_zero_proof=mock.Mock(
                        side_effect=RuntimeError("zero proof failed"))), \
                    mock.patch.object(
                        repair.os, "fstat", return_value=self.root_metadata()), \
                    self.assertRaisesRegex(
                        RuntimeError, "REARM_STACK_ROLLBACK_FAILED"):
                repair.bring_up_rearm_stack()

            self.assertLess(events.index("revoke"), events.index("deny-all"))
            self.assertLess(events.index("deny-verified"), events.index("stop"))
            self.assertLess(
                events.index("stop"), events.index("stopped-verified"))

    def test_prepared_abort_rejects_current_rearm_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            receipt = root / "rearm-stack-campaign-test.receipt.json"
            receipt.write_text("{}\n", encoding="ascii")
            receipt.chmod(0o600)
            real_lstat = repair.os.lstat

            def root_lstat(path: object) -> object:
                metadata = real_lstat(path)
                return SimpleNamespace(
                    st_mode=metadata.st_mode, st_nlink=metadata.st_nlink,
                    st_uid=0, st_gid=0, st_size=metadata.st_size)

            with mock.patch.multiple(
                    repair,
                    END_FLAT_RECEIPT_ROOT=root,
                    START_PERMIT_PENDING=root / "pending.json",
                    START_PERMIT_CLAIMED=root / "claimed.json",
                    START_PERMIT_CONSUMED=root / "consumed.json",
                    AGENT_STATE=root / "state.json"), \
                    mock.patch.object(repair.os, "lstat", side_effect=root_lstat), \
                    self.assertRaisesRegex(
                        RuntimeError, "PREPARED_ABORT_ALREADY_REARMED"):
                repair._prepared_abort_require_never_rearmed("campaign-test")

    def test_prepared_abort_fences_policy_before_end_flat_outside_locks(
            self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            events: list[str] = []
            lock_active = False

            class Lock:
                def __enter__(self) -> "Lock":
                    nonlocal lock_active
                    lock_active = True
                    return self

                def __exit__(self, *_unused: object) -> None:
                    nonlocal lock_active
                    lock_active = False

            values = {"HEPTA_LOCAL_AI_CAMPAIGN_ID": "campaign-test"}
            policy = {"campaign_id": "campaign-test"}

            def fence(_campaign_id: str) -> str:
                self.assertTrue(lock_active)
                events.append("policy-fence")
                return "sha256:" + "a" * 64

            def finish() -> None:
                self.assertFalse(lock_active)
                events.append("end-flat")

            with mock.patch.multiple(
                    repair,
                    _campaign_lifecycle_locks=mock.Mock(
                        side_effect=lambda: Lock()),
                    _validated_prepared_campaign=mock.Mock(
                        return_value=(values, policy)),
                    _prepared_abort_require_enabled_policy=mock.Mock(),
                    _prepared_abort_require_never_rearmed=mock.Mock(),
                    _unit_is_active=mock.Mock(return_value=False),
                    _end_flat_verify_runtime_stopped=mock.Mock(),
                    _validate_no_campaign_session_residue=mock.Mock(),
                    _prepared_abort_verify_local_deny_all=mock.Mock(),
                    _end_flat_verify_deny_all=mock.Mock(),
                    _ensure_end_flat_request_marker=mock.Mock(
                        side_effect=lambda _campaign_id: events.append(
                            "request") or root / "request.json"),
                    _prepared_abort_stop_timers=mock.Mock(
                        side_effect=lambda: events.append("timers")),
                    _end_flat_persist_policy_disabled=mock.Mock(
                        side_effect=fence),
                    _campaign_policy_expired=mock.Mock(return_value=True),
                    _end_flat_trigger_file=mock.Mock(return_value=True),
                    end_flat=mock.Mock(side_effect=finish)):
                repair.abort_prepared()

            self.assertEqual(
                events, ["request", "policy-fence", "timers", "end-flat"])

    def test_prepared_abort_policy_fence_precedes_timer_stop_on_failure(
            self) -> None:
        """A failed policy write must leave the custodian/timers untouched.

        Disabling the admission policy is the irreversible fence.  If that
        write fails, stopping the prepared timers would create an enabled
        policy with no recovery custodian; the timer operation must therefore
        be reached only after the disabled policy has been durably verified.
        """
        fence = mock.Mock(side_effect=RuntimeError("policy write failed"))
        stop_timers = mock.Mock()
        finish = mock.Mock()
        with mock.patch.multiple(
                repair,
                _campaign_lifecycle_locks=mock.Mock(
                    side_effect=contextlib.nullcontext),
                _validated_prepared_campaign=mock.Mock(return_value=(
                    {"HEPTA_LOCAL_AI_CAMPAIGN_ID": "campaign-test"},
                    {"campaign_id": "campaign-test"})),
                _prepared_abort_require_enabled_policy=mock.Mock(),
                _prepared_abort_require_never_rearmed=mock.Mock(),
                _unit_is_active=mock.Mock(return_value=False),
                _end_flat_verify_runtime_stopped=mock.Mock(),
                _validate_no_campaign_session_residue=mock.Mock(),
                _prepared_abort_verify_local_deny_all=mock.Mock(),
                _end_flat_verify_deny_all=mock.Mock(),
                _ensure_end_flat_request_marker=mock.Mock(
                    return_value=Path("/tmp/request.json")),
                _prepared_abort_stop_timers=stop_timers,
                _end_flat_persist_policy_disabled=fence,
                end_flat=finish):
            with self.assertRaisesRegex(
                    RuntimeError, "policy write failed"):
                repair.abort_prepared()
        stop_timers.assert_not_called()
        finish.assert_not_called()

    def test_prepared_abort_fails_closed_when_agent_is_active(self) -> None:
        fence = mock.Mock()
        finish = mock.Mock()
        with mock.patch.multiple(
                repair,
                _campaign_lifecycle_locks=mock.Mock(
                    side_effect=contextlib.nullcontext),
                _validated_prepared_campaign=mock.Mock(return_value=(
                    {"HEPTA_LOCAL_AI_CAMPAIGN_ID": "campaign-test"},
                    {"campaign_id": "campaign-test"})),
                _prepared_abort_require_enabled_policy=mock.Mock(),
                _prepared_abort_require_never_rearmed=mock.Mock(),
                _unit_is_active=mock.Mock(return_value=True),
                _end_flat_persist_policy_disabled=fence,
                end_flat=finish):
            with self.assertRaisesRegex(
                    RuntimeError, "PREPARED_ABORT_AGENT_ACTIVE"):
                repair.abort_prepared()
            fence.assert_not_called()
            finish.assert_not_called()

    def test_prepared_abort_requires_exact_local_deny_all(self) -> None:
        valid = {
            "mode": "DENY_ALL", "paper_authorized": False,
            "live_authorized": False, "identity_count": 0,
            "identity_manifest_sha256": "sha256:" + "a" * 64,
            "effective_state_verified": True, "wal_state": "ABSENT",
            "egress_verified": True,
        }
        with mock.patch.object(
                repair, "run_checked", return_value=json.dumps(valid)):
            self.assertEqual(
                repair._prepared_abort_verify_local_deny_all(), valid)
        for field, value in (
                ("paper_authorized", True),
                ("live_authorized", True),
                ("identity_count", 1),
                ("effective_state_verified", False),
                ("wal_state", "ENABLE"),
                ("egress_verified", False)):
            drifted = dict(valid)
            drifted[field] = value
            with self.subTest(field=field), mock.patch.object(
                    repair, "run_checked",
                    return_value=json.dumps(drifted)), \
                    self.assertRaisesRegex(
                        RuntimeError,
                        "PREPARED_ABORT_CONTROL_STATUS_INVALID"):
                repair._prepared_abort_verify_local_deny_all()

    def test_end_flat_refuses_unowned_global_active_order(self) -> None:
        agent = SimpleNamespace(
            INSTRUMENT="EUR.USD",
            orders_snapshot=mock.Mock(return_value=
                self.owner_orders_snapshot([41], [])),
            tool_response=mock.Mock(),
        )
        with self.assertRaisesRegex(
                RuntimeError, "END_FLAT_UNMANAGED_ACTIVE_ORDER_PRESENT"):
            repair._end_flat_cancel_orders(agent, object())
        agent.tool_response.assert_not_called()

    def test_end_flat_halt_failure_requires_policy_expiry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            policy_path = Path(directory) / "alpha.json"
            arguments = SimpleNamespace(campaign_id="campaign-test")
            agent = SimpleNamespace(
                campaign=mock.Mock(side_effect=RuntimeError("lost")))
            real_lstat = repair.os.lstat

            def root_lstat(path: object) -> SimpleNamespace:
                metadata = real_lstat(path)
                return SimpleNamespace(
                    st_mode=metadata.st_mode, st_nlink=metadata.st_nlink,
                    st_uid=0, st_gid=0, st_size=metadata.st_size)

            base = {
                "schema": "hepta.ib-paper-campaign-policy.v4",
                "domain_id": "alpha", "campaign_id": "campaign-test",
                "paper_only": True, "live_authorized": False,
                "enabled": True, "mutations_authorized": True,
            }
            future = dict(base)
            future["expires_at_ms"] = (
                repair.time.time_ns() // 1_000_000 + 60_000)
            policy_path.write_text(json.dumps(future), encoding="ascii")
            policy_path.chmod(0o600)
            with mock.patch.object(repair, "CAMPAIGN_POLICY", policy_path), \
                    mock.patch.object(
                        repair.os, "lstat", side_effect=root_lstat), \
                    self.assertRaisesRegex(
                        RuntimeError,
                        "END_FLAT_HALT_UNCONFIRMED_BEFORE_EXPIRY"):
                repair._end_flat_halt_campaign(agent, arguments)
            expired = dict(base)
            expired["expires_at_ms"] = 1
            policy_path.write_text(json.dumps(expired), encoding="ascii")
            policy_path.chmod(0o600)
            with mock.patch.object(repair, "CAMPAIGN_POLICY", policy_path), \
                    mock.patch.object(
                        repair.os, "lstat", side_effect=root_lstat):
                result = repair._end_flat_halt_campaign(agent, arguments)
            self.assertTrue(result.startswith("halt_unconfirmed_after_expiry:"))
            disabled = dict(future)
            disabled["enabled"] = False
            disabled["mutations_authorized"] = False
            policy_path.write_text(json.dumps(disabled), encoding="ascii")
            policy_path.chmod(0o600)
            with mock.patch.object(repair, "CAMPAIGN_POLICY", policy_path), \
                    mock.patch.object(
                        repair.os, "lstat", side_effect=root_lstat):
                result = repair._end_flat_halt_campaign(agent, arguments)
            self.assertTrue(result.startswith("halt_unconfirmed_after_expiry:"))

    def test_end_flat_retry_condition_is_deadline_or_durable_request_bound(
            self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = root / "end-flat-campaign-test.requested.json"
            real_lstat = repair.os.lstat

            def root_lstat(path: object) -> SimpleNamespace:
                metadata = real_lstat(path)
                return SimpleNamespace(
                    st_mode=metadata.st_mode, st_nlink=metadata.st_nlink,
                    st_uid=0, st_gid=0, st_size=metadata.st_size)

            with mock.patch.multiple(
                    repair,
                    END_FLAT_RECEIPT_ROOT=root,
                    read_env=mock.Mock(return_value={
                        "HEPTA_LOCAL_AI_CAMPAIGN_ID": "campaign-test",
                    }),
                    _campaign_policy_expired=mock.Mock(return_value=False)), \
                    mock.patch.object(
                        repair.os, "lstat", side_effect=root_lstat):
                self.assertFalse(repair.end_flat_condition())
                marker.write_text(json.dumps({
                    "schema": "hepta.local-ai-paper-end-flat-request.v1",
                    "campaign_id": "campaign-test",
                    "paper_only": True,
                    "live_authorized": False,
                }), encoding="ascii")
                marker.chmod(0o600)
                self.assertTrue(repair.end_flat_condition())
                marker.unlink()
                receipt = root / "end-flat-campaign-test.receipt.json"
                receipt.write_text(json.dumps({
                    "schema": "hepta.local-ai-paper-end-flat-receipt.v1",
                    "campaign_id": "campaign-test",
                    "paper_only": True,
                    "live_authorized": False,
                }), encoding="ascii")
                receipt.chmod(0o600)
                self.assertTrue(repair.end_flat_condition())

    def test_request_end_flat_persists_campaign_bound_trigger(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            real_lstat = repair.os.lstat

            def root_lstat(path: object) -> SimpleNamespace:
                metadata = real_lstat(path)
                return SimpleNamespace(
                    st_mode=metadata.st_mode, st_nlink=metadata.st_nlink,
                    st_uid=0, st_gid=0, st_size=metadata.st_size)

            with mock.patch.multiple(
                    repair,
                    END_FLAT_RECEIPT_ROOT=root,
                    read_env=mock.Mock(return_value={
                        "HEPTA_LOCAL_AI_CAMPAIGN_ID": "campaign-test",
                    })), \
                    mock.patch.object(
                        repair.os, "lstat", side_effect=root_lstat), \
                    mock.patch.object(repair.os, "fchown"):
                repair.request_end_flat()

            marker = root / "end-flat-campaign-test.requested.json"
            value = json.loads(marker.read_text(encoding="ascii"))
            self.assertEqual(value["campaign_id"], "campaign-test")
            self.assertTrue(value["paper_only"])
            self.assertFalse(value["live_authorized"])

    def test_end_flat_reboot_bringup_requires_verified_local_paper_stack(
            self) -> None:
        response = json.dumps({
            "mode": "LOCAL_PAPER", "domain": "alpha",
            "paper_authorized": True, "live_authorized": False,
            "admission_mode": "local-only",
            "identity_manifest_sha256": "sha256:" + "a" * 64,
        })
        status = json.dumps({
            "mode": "LOCAL_PAPER", "paper_authorized": True,
            "live_authorized": False, "identity_count": 1,
            "identity_manifest_sha256": "sha256:" + "a" * 64,
        })
        broker = (
            "hepta_broker_egress_policy: PASS policy_sha256=" + "b" * 64 +
            " authorized_connectors=1 authorized_uids=1234 "
            "protected_ports=4\n")
        checked = mock.Mock(side_effect=[response, status, broker])
        with mock.patch.multiple(
                repair,
                _unit_is_active=mock.Mock(
                    side_effect=[False, True, True, True]),
                _end_flat_trigger_file=mock.Mock(return_value=True),
                _campaign_policy_expired=mock.Mock(return_value=True),
                _campaign_policy_for_control=mock.Mock(return_value={
                    "campaign_id": "campaign-test",
                    "admission_mode": "local-only",
                }),
                run_checked=checked), \
                mock.patch.object(
                    repair, "_ensure_end_flat_recovery_runtime",
                    side_effect=ORIGINAL_ENSURE_END_FLAT_RECOVERY_RUNTIME), \
                mock.patch.object(
                    repair, "_require_active_local_paper_control",
                    side_effect=ORIGINAL_REQUIRE_ACTIVE_LOCAL_PAPER_CONTROL):
            repair._ensure_end_flat_recovery_runtime("campaign-test")
        self.assertEqual(checked.call_args_list, [mock.call([
            repair.LOCAL_PAPER_CONTROL, "enable", "--domain", "alpha",
        ], timeout=120), mock.call([
            repair.LOCAL_PAPER_CONTROL, "status", "--domain", "alpha",
        ], timeout=30), mock.call([
            "/usr/libexec/hepta-broker-egress-policy",
            "--policy",
            "/usr/share/heptatrader/hepta-broker-network-policy-v1.json",
            "--identity-manifest",
            "/usr/share/heptatrader/hepta-service-identities-v1.json",
            "--paper-identities",
            "/etc/heptatrader/"
            "hepta-agent-trust-domain-paper-identities-v1.json",
            "--check-active",
        ], timeout=15)])

    def test_wait_for_end_flat_runtime_units_immediate_ready(self) -> None:
        with mock.patch.object(
                repair, "_unit_is_active", return_value=True) as active, \
                mock.patch.object(repair.time, "sleep") as sleep:
            repair._wait_for_end_flat_runtime_units((
                "hepta-execution-ib-paper@alpha.service",
                "hepta-tool-gateway@alpha.service",
            ))
        self.assertEqual(active.call_count, 2)
        sleep.assert_not_called()

    def test_end_flat_recovery_runtime_waits_for_delayed_units(self) -> None:
        response = json.dumps({
            "mode": "LOCAL_PAPER", "domain": "alpha",
            "paper_authorized": True, "live_authorized": False,
            "admission_mode": "local-only",
            "identity_manifest_sha256": "sha256:" + "a" * 64,
        })
        # The first probe is the pre-enable fast-path check.  The next two
        # probes model one incomplete poll, followed by all required units
        # becoming active.
        active = mock.Mock(side_effect=[
            False,
            False, True, True,
            True, True, True,
        ])
        checked = mock.Mock(return_value=response)
        with mock.patch.multiple(
                repair,
                _unit_is_active=active,
                _end_flat_trigger_file=mock.Mock(return_value=True),
                _campaign_policy_expired=mock.Mock(return_value=True),
                _campaign_policy_for_control=mock.Mock(return_value={
                    "campaign_id": "campaign-test",
                    "admission_mode": "local-only",
                }),
                run_checked=checked), \
                mock.patch.object(
                    repair, "_ensure_end_flat_recovery_runtime",
                    side_effect=ORIGINAL_ENSURE_END_FLAT_RECOVERY_RUNTIME), \
                mock.patch.object(repair.time, "sleep") as sleep:
            repair._ensure_end_flat_recovery_runtime("campaign-test")
        self.assertEqual(active.call_count, 7)
        sleep.assert_called_once()
        repair._require_active_local_paper_control.assert_called_once_with(
            "END_FLAT_RUNTIME_CONTROL_STATUS_INVALID")
        checked.assert_called_once_with([
            repair.LOCAL_PAPER_CONTROL, "enable", "--domain", "alpha",
        ], timeout=120)

    def test_end_flat_recovery_runtime_timeout_rolls_back(self) -> None:
        response = json.dumps({
            "mode": "LOCAL_PAPER", "domain": "alpha",
            "paper_authorized": True, "live_authorized": False,
            "admission_mode": "local-only",
            "identity_manifest_sha256": "sha256:" + "a" * 64,
        })
        checked = mock.Mock(side_effect=[response, "stop-ok"])
        with mock.patch.multiple(
                repair,
                _unit_is_active=mock.Mock(return_value=False),
                _end_flat_trigger_file=mock.Mock(return_value=True),
                _campaign_policy_expired=mock.Mock(return_value=True),
                _campaign_policy_for_control=mock.Mock(return_value={
                    "campaign_id": "campaign-test",
                    "admission_mode": "local-only",
                }),
                run_checked=checked), \
                mock.patch.object(
                    repair, "END_FLAT_RUNTIME_READY_TIMEOUT_SECONDS", 0.0), \
                mock.patch.object(
                    repair, "_end_flat_revoke_local_paper_control") as revoke, \
                mock.patch.object(
                    repair, "_end_flat_verify_deny_all") as deny_all, \
                mock.patch.object(
                    repair, "_end_flat_verify_runtime_stopped") as stopped, \
                mock.patch.object(
                    repair, "_require_active_local_paper_control") as verify, \
                mock.patch.object(
                    repair, "_ensure_end_flat_recovery_runtime",
                    side_effect=ORIGINAL_ENSURE_END_FLAT_RECOVERY_RUNTIME), \
                self.assertRaisesRegex(
                    RuntimeError, "END_FLAT_RUNTIME_BRINGUP_FAILED") as raised:
            repair._ensure_end_flat_recovery_runtime("campaign-test")
        revoke.assert_called_once_with()
        deny_all.assert_called_once_with()
        stopped.assert_called_once_with()
        verify.assert_not_called()
        self.assertEqual(checked.call_count, 2)
        self.assertEqual(checked.call_args_list[1], mock.call([
            "/usr/bin/systemctl", "stop",
            *repair.END_FLAT_EXECUTION_UNITS, *repair.END_FLAT_TOOL_UNITS,
        ], timeout=60))
        cause = raised.exception.__cause__
        self.assertIsNotNone(cause)
        self.assertTrue(str(cause).startswith(
            "END_FLAT_RUNTIME_UNITS_NOT_READY:"))
        for unit in (
                "hepta-execution-ib-paper@alpha.service",
                "hepta-tool-gateway@alpha.service",
                "hepta-ib-paper-campaign-operator@alpha.socket"):
            self.assertIn(unit, str(cause))

    def test_active_recovery_runtime_requires_exact_live_paper_policy(
            self) -> None:
        status = json.dumps({
            "mode": "LOCAL_PAPER", "paper_authorized": True,
            "live_authorized": False, "identity_count": 1,
            "identity_manifest_sha256": "sha256:" + "a" * 64,
        })
        drifted = (
            "hepta_broker_egress_policy: PASS policy_sha256=" + "b" * 64 +
            " authorized_connectors=0 authorized_uids= protected_ports=4\n")
        checked = mock.Mock(side_effect=[status, drifted])
        with mock.patch.multiple(
                repair,
                _unit_is_active=mock.Mock(return_value=True),
                run_checked=checked), \
                mock.patch.object(
                    repair, "_require_active_local_paper_control",
                    side_effect=ORIGINAL_REQUIRE_ACTIVE_LOCAL_PAPER_CONTROL), \
                self.assertRaisesRegex(
                    RuntimeError, "RISK_RECOVERY_CONTROL_STATUS_INVALID"):
            repair._ensure_risk_recovery_runtime()
        self.assertEqual(checked.call_count, 2)

    def test_active_recovery_runtime_rejects_multiple_authorized_uids(
            self) -> None:
        status = json.dumps({
            "mode": "LOCAL_PAPER", "paper_authorized": True,
            "live_authorized": False, "identity_count": 1,
            "identity_manifest_sha256": "sha256:" + "a" * 64,
        })
        malformed = (
            "hepta_broker_egress_policy: PASS policy_sha256=" + "b" * 64 +
            " authorized_connectors=1 authorized_uids=1234,1235 "
            "protected_ports=4\n")
        with mock.patch.multiple(
                repair,
                _unit_is_active=mock.Mock(return_value=True),
                run_checked=mock.Mock(side_effect=[status, malformed])), \
                mock.patch.object(
                    repair, "_require_active_local_paper_control",
                    side_effect=ORIGINAL_REQUIRE_ACTIVE_LOCAL_PAPER_CONTROL), \
                self.assertRaisesRegex(
                    RuntimeError, "RISK_RECOVERY_CONTROL_STATUS_INVALID"):
            repair._ensure_risk_recovery_runtime()

    def test_active_recovery_runtime_rejects_deny_all_fast_path(self) -> None:
        deny_all = json.dumps({
            "mode": "DENY_ALL", "paper_authorized": False,
            "live_authorized": False, "identity_count": 0,
            "identity_manifest_sha256": "sha256:" + "a" * 64,
        })
        with mock.patch.multiple(
                repair,
                _unit_is_active=mock.Mock(return_value=True),
                run_checked=mock.Mock(return_value=deny_all)), \
                mock.patch.object(
                    repair, "_require_active_local_paper_control",
                    side_effect=ORIGINAL_REQUIRE_ACTIVE_LOCAL_PAPER_CONTROL), \
                self.assertRaisesRegex(
                    RuntimeError, "RISK_RECOVERY_CONTROL_STATUS_INVALID"):
            repair._ensure_risk_recovery_runtime()

    def test_active_end_flat_runtime_rejects_live_fast_path(self) -> None:
        live = json.dumps({
            "mode": "LOCAL_PAPER", "paper_authorized": True,
            "live_authorized": True, "identity_count": 1,
            "identity_manifest_sha256": "sha256:" + "a" * 64,
        })
        with mock.patch.multiple(
                repair,
                _unit_is_active=mock.Mock(return_value=True),
                _end_flat_trigger_file=mock.Mock(return_value=True),
                _campaign_policy_expired=mock.Mock(return_value=False),
                run_checked=mock.Mock(return_value=live)), \
                mock.patch.object(
                    repair, "_ensure_end_flat_recovery_runtime",
                    side_effect=ORIGINAL_ENSURE_END_FLAT_RECOVERY_RUNTIME), \
                mock.patch.object(
                    repair, "_require_active_local_paper_control",
                    side_effect=ORIGINAL_REQUIRE_ACTIVE_LOCAL_PAPER_CONTROL), \
                self.assertRaisesRegex(
                    RuntimeError, "END_FLAT_RUNTIME_CONTROL_STATUS_INVALID"):
            repair._ensure_end_flat_recovery_runtime("campaign-test")

    def test_risk_recovery_halt_failure_requires_policy_expiry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            policy_path = Path(directory) / "alpha.json"
            arguments = SimpleNamespace(
                campaign_id="campaign-test")
            agent = SimpleNamespace(
                campaign=mock.Mock(side_effect=RuntimeError("lost")))
            state = {"recovery_halt_confirmed": False}
            real_lstat = repair.os.lstat

            def root_lstat(path: object) -> SimpleNamespace:
                metadata = real_lstat(path)
                return SimpleNamespace(
                    st_mode=metadata.st_mode, st_nlink=metadata.st_nlink,
                    st_uid=0, st_gid=0, st_size=metadata.st_size)

            base = {
                "schema": "hepta.ib-paper-campaign-policy.v4",
                "domain_id": "alpha", "campaign_id": "campaign-test",
                "paper_only": True, "live_authorized": False,
                "enabled": True, "mutations_authorized": True,
            }
            future = dict(base)
            future["expires_at_ms"] = (
                repair.time.time_ns() // 1_000_000 + 60_000)
            policy_path.write_text(json.dumps(future), encoding="ascii")
            policy_path.chmod(0o600)
            with mock.patch.object(repair, "CAMPAIGN_POLICY", policy_path), \
                    mock.patch.object(
                        repair.os, "lstat", side_effect=root_lstat), \
                    self.assertRaisesRegex(
                        RuntimeError,
                        "RISK_RECOVERY_HALT_UNCONFIRMED_BEFORE_EXPIRY"):
                repair._risk_recovery_halt_campaign(
                    agent, arguments, state)
            expired = dict(base)
            expired["expires_at_ms"] = 1
            policy_path.write_text(json.dumps(expired), encoding="ascii")
            policy_path.chmod(0o600)
            with mock.patch.object(repair, "CAMPAIGN_POLICY", policy_path), \
                    mock.patch.object(
                        repair.os, "lstat", side_effect=root_lstat):
                result = repair._risk_recovery_halt_campaign(
                    agent, arguments, state)
            self.assertTrue(result.startswith(
                "halt_unconfirmed_after_expiry:"))
            disabled = dict(future)
            disabled["enabled"] = False
            disabled["mutations_authorized"] = False
            policy_path.write_text(json.dumps(disabled), encoding="ascii")
            policy_path.chmod(0o600)
            with mock.patch.object(repair, "CAMPAIGN_POLICY", policy_path), \
                    mock.patch.object(
                        repair.os, "lstat", side_effect=root_lstat):
                result = repair._risk_recovery_halt_campaign(
                    agent, arguments, state)
            self.assertTrue(result.startswith(
                "halt_unconfirmed_after_expiry:"))

    def test_end_flat_unit_closure_rejects_pending_job(self) -> None:
        with mock.patch.object(repair, "_unit_properties", return_value={
                "LoadState": "loaded", "ActiveState": "inactive",
                "UnitFileState": "disabled", "Job": "91 stop",
        }), self.assertRaisesRegex(
                RuntimeError, "END_FLAT_UNIT_NOT_DISABLED"):
            repair._verify_end_flat_units_disabled(("example.service",))


class PrepareRepairCampaignTransactionTests(unittest.TestCase):
    def setUp(self) -> None:
        for target, replacement in (
                ("_read_stable_root_file", mock.Mock(
                    side_effect=lambda path, _failure: path.read_bytes())),
                ("_load_deployment_evidence_transaction",
                 mock.Mock(return_value=None))):
            patcher = mock.patch.object(prepare, target, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

    @staticmethod
    def root_file_metadata(
            metadata: os.stat_result, *, uid: int = 0, gid: int = 0,
    ) -> SimpleNamespace:
        return SimpleNamespace(
            st_mode=metadata.st_mode, st_nlink=metadata.st_nlink,
            st_uid=uid, st_gid=gid, st_size=metadata.st_size,
            st_dev=metadata.st_dev, st_ino=metadata.st_ino,
            st_mtime_ns=metadata.st_mtime_ns,
            st_ctime_ns=metadata.st_ctime_ns)

    @classmethod
    def root_file_fstat(cls, descriptor: int) -> SimpleNamespace:
        return cls.root_file_metadata(REAL_FSTAT(descriptor))

    @classmethod
    def root_file_lstat(cls, path: object) -> SimpleNamespace:
        return cls.root_file_metadata(REAL_LSTAT(path))

    def test_optional_owned_read_rejects_post_open_disappearance(
            self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "transaction.json"
            path.write_bytes(b"{}\n")
            path.chmod(0o600)
            with mock.patch.object(
                    prepare.os, "fstat",
                    side_effect=self.root_file_fstat), \
                    mock.patch.object(
                        prepare.os, "lstat",
                        side_effect=FileNotFoundError()), \
                    self.assertRaisesRegex(
                        RuntimeError,
                        "REPAIR_DEPLOYMENT_EVIDENCE_TRANSACTION_PATH_UNSAFE"):
                prepare._read_stable_owned_file(
                    path,
                    "REPAIR_DEPLOYMENT_EVIDENCE_TRANSACTION_PATH_UNSAFE",
                    uid=0, gid=0, mode=0o600)
            with self.assertRaises(FileNotFoundError):
                prepare._read_stable_owned_file(
                    root / "absent.json", "FAIL",
                    uid=0, gid=0, mode=0o600)

    @staticmethod
    def policy() -> dict[str, object]:
        return {
            "schema": "hepta.ib-paper-campaign-policy.v4",
            "version": 4,
            "campaign_id": "campaign-test",
            "domain_id": "alpha",
            "paper_only": True,
            "live_authorized": False,
            "enabled": False,
            "mutations_authorized": False,
            "admission_mode": "local-only",
            "strategy_id": "local-ai-paper-mkt-v2",
            "strategy_version": "2",
            "strategy_sha256": "sha256:" + "1" * 64,
            "valid_after_ms": 1_000_000,
            "expires_at_ms": 2_000_000,
            "allowed_instruments": ["EUR.USD"],
            "max_cycles": 720,
            "source_baseline_sha256": "sha256:" + "a" * 64,
            "order_type": "MKT",
            "max_quantity": 25000,
            "min_cycle_interval_ms": 120_000,
            "operator_ttl_seconds": 20,
            "max_intent_horizon_ms": 60_000,
            "max_holding_ms": 0,
            "max_active_orders": 1,
            "tif": "DAY",
            "end_flat_required": True,
            "deployment_evidence_file_sha256": "sha256:" + "b" * 64,
            "deployment_evidence_body_sha256": "sha256:" + "c" * 64,
            "deployment_install_transaction_id": "install-round105-test",
        }

    @classmethod
    def legacy_unbound_v4_policy(cls) -> dict[str, object]:
        policy = cls.policy()
        for field in LEGACY_V4_UNBOUND_DEPLOYMENT_FIELDS:
            policy.pop(field)
        return policy

    @staticmethod
    def cleanup_intent_document(
            *, lease_key_file_sha256: str = "sha256:" + "4" * 64,
    ) -> dict[str, object]:
        body: dict[str, object] = {
            "schema": prepare.SUPERVISOR_LEASE_CLEANUP_INTENT_SCHEMA,
            "version": 1,
            "migration_id": "1" * 32,
            "campaign_id": "campaign-test",
            "policy_file_sha256": "sha256:" + "2" * 64,
            "terminal_receipt_path": (
                "/var/lib/hepta-local-ai-paper-agent/"
                "end-flat-campaign-test.receipt.json"),
            "terminal_receipt_file_sha256": "sha256:" + "3" * 64,
            "lease_store_path": str(prepare.SUPERVISOR_LEASE_STORE),
            "lease_key_path": str(prepare.SUPERVISOR_LEASE_KEY),
            "lease_key_file_sha256": lease_key_file_sha256,
            "lease_lock_path": str(
                prepare.SUPERVISOR_LEASE_CLEANUP_LOCK),
            "pre_store_sha256": "sha256:" + "5" * 64,
            "backup_path": str(prepare.SUPERVISOR_LEASE_BACKUP),
            "expected_issuer": "hepta.os.bootstrap",
            "expected_agent_id": "alpha",
            "expected_peer_uid": 2104,
            "expected_key_uid": 0,
            "expected_key_gid": 0,
            "expected_key_mode": 0o400,
            "expected_source_uid": 2101,
            "expected_source_gid": 2101,
            "expected_source_mode": 0o600,
            "created_at_ms": 1_000,
            "paper_only": True,
            "live_authorized": False,
        }
        return prepare._sealed_cleanup_document(body)

    @classmethod
    def cleanup_receipt_document(
            cls, *, lease_key_file_sha256: str = "sha256:" + "4" * 64,
    ) -> dict[str, object]:
        intent = cls.cleanup_intent_document(
            lease_key_file_sha256=lease_key_file_sha256)
        body: dict[str, object] = {
            **{key: value for key, value in intent.items()
               if key not in {"schema", "version", "body_sha256"}},
            "schema": prepare.SUPERVISOR_LEASE_CLEANUP_RECEIPT_SCHEMA,
            "version": 1,
            "migration_intent_body_sha256": intent["body_sha256"],
            "post_store_sha256": "sha256:" + "6" * 64,
            "backup_store_sha256": intent["pre_store_sha256"],
            "retired_records": 2,
            "helper_already_migrated": False,
            "completed_at_ms": 2_000,
            "mutation_authorized": False,
            "paper_only": True,
            "live_authorized": False,
        }
        return prepare._sealed_cleanup_document(body)

    @classmethod
    def v5_policy(
            cls, *, strategy_sha256: str = "sha256:" + "1" * 64,
    ) -> dict[str, object]:
        deployment = cls.deployment_snapshot()
        binding = prepare._deployment_binding_record(deployment)
        valid_after_ms = 1_000_000_000_000
        return {
            "schema": prepare.PAPER_POLICY_V5_SCHEMA,
            "version": 5,
            "campaign_id": "paper-v5-pinned-campaign",
            "domain_id": "alpha",
            "enabled": False,
            "mutations_authorized": False,
            "paper_only": True,
            "live_authorized": False,
            "strategy_id": "local-ai-paper-lmt-v3",
            "strategy_version": "3",
            "strategy_sha256": strategy_sha256,
            "valid_after_ms": valid_after_ms,
            "expires_at_ms": (
                valid_after_ms + prepare.PAPER_POLICY_V5_EXTERNAL_DURATION_MS),
            "allowed_instruments": ["EUR.USD"],
            "max_cycles": 1,
            "max_quantity": 1,
            "min_cycle_interval_ms": 120_000,
            "operator_ttl_seconds": 10,
            "max_intent_horizon_ms": 60_000,
            "max_holding_ms": 0,
            "max_active_orders": 1,
            "order_type": "LMT",
            "tif": "DAY",
            "end_flat_required": True,
            "source_baseline_sha256": "sha256:" + "a" * 64,
            "admission_receipt_name": "paper-admission-round105.json",
            "admission_receipt_file_sha256": "sha256:" + "6" * 64,
            "admission_receipt_body_sha256": "sha256:" + "7" * 64,
            "admission_finalization_current_pointer_path":
                "/var/lib/hepta/paper-testing-admission/"
                "finalization-current.v1.json",
            "admission_finalization_current_pointer_file_sha256":
                "sha256:" + "8" * 64,
            "admission_finalization_current_pointer_body_sha256":
                "sha256:" + "9" * 64,
            "admission_finalization_tombstone_path":
                "/var/lib/hepta/paper-testing-admission/finalized."
                "zero-exposure-" + "a" * 48 + ".v1.json",
            "admission_finalization_tombstone_file_sha256":
                "sha256:" + "b" * 64,
            "admission_finalization_tombstone_body_sha256":
                "sha256:" + "c" * 64,
            "admission_mode": "external-p1-finalized",
            "deployment_evidence_file_sha256":
                binding["evidence_file_sha256"],
            "deployment_evidence_body_sha256":
                binding["evidence_body_sha256"],
            "deployment_install_transaction_id":
                binding["install_transaction_id"],
            "p1_audit_receipt_path":
                "/var/lib/hepta/paper-testing-admission/p1-audit.json",
            "p1_audit_receipt_file_sha256": "sha256:" + "d" * 64,
            "p1_audit_receipt_body_sha256": "sha256:" + "e" * 64,
            "watch_handoff_receipt_path":
                "/var/lib/hepta/p1-admission/"
                "p1-watch-to-paper-handoff-receipt-v2.json",
            "watch_handoff_receipt_file_sha256": "sha256:" + "f" * 64,
            "watch_handoff_receipt_body_sha256": "sha256:" + "0f" * 32,
        }

    @staticmethod
    def v5_local_policy() -> dict[str, object]:
        return {
            "schema": prepare.PAPER_POLICY_V5_SCHEMA,
            "version": 5,
            "campaign_id": "local-paper-v5-disabled-seed",
            "domain_id": "alpha",
            "enabled": False,
            "mutations_authorized": False,
            "paper_only": True,
            "live_authorized": False,
            "strategy_id": prepare.STRATEGY_ID,
            "strategy_version": prepare.STRATEGY_VERSION,
            "strategy_sha256": prepare.ZERO_DIGEST,
            "valid_after_ms": 0,
            "expires_at_ms": 0,
            "allowed_instruments": ["EUR.USD"],
            "max_cycles": 720,
            "max_quantity": 25_000,
            "min_cycle_interval_ms": 120_000,
            "operator_ttl_seconds": 20,
            "max_intent_horizon_ms": 60_000,
            "max_holding_ms": 0,
            "max_active_orders": 1,
            "order_type": "MKT",
            "tif": "DAY",
            "end_flat_required": True,
            "source_baseline_sha256": prepare.ZERO_DIGEST,
            "admission_mode": "local-only",
            "deployment_evidence_file_sha256": prepare.ZERO_DIGEST,
            "deployment_evidence_body_sha256": prepare.ZERO_DIGEST,
            "deployment_install_transaction_id":
                "replace-with-certified-install-transaction",
        }

    @staticmethod
    def strategy(*, order_type: str = "LMT") -> dict[str, object]:
        return {
            "schema": "hepta.local-ai-paper-strategy.v3",
            "version": 3,
            "paper_only": True,
            "live_authorized": False,
            "order_type": order_type,
            "max_order_quantity": 25000 if order_type == "MKT" else 1,
            "max_holding_seconds": 0,
            "exit_mode": "MODEL_REVERSAL",
            "rate_limit_fail_closed": True,
            "emergency_reduce_only_recovery": True,
            "auth_rearm_required_after_rate_limit": True,
            "campaign_end_flat_required": True,
        }

    @staticmethod
    def agent_env() -> bytes:
        return (
            "HEPTA_LOCAL_AI_CAMPAIGN_ID=old-campaign\n"
            "HEPTA_LOCAL_AI_STRATEGY_ID=local-ai-paper-mkt-v2\n"
            "HEPTA_LOCAL_AI_STRATEGY_VERSION=2\n"
            "HEPTA_LOCAL_AI_STRATEGY_SHA256=sha256:" + "0" * 64 + "\n"
            "HEPTA_LOCAL_AI_AUTH_GENERATION=auth-gen-old\n"
            "HEPTA_LOCAL_AI_AUTH_PROFILE_ID=profile-old\n"
        ).encode("ascii")

    @staticmethod
    def legacy_minimal_end_flat_receipt(
            policy_raw: bytes,
    ) -> dict[str, object]:
        return {
            "schema": "hepta.local-ai-paper-end-flat-receipt.v1",
            "campaign_id": "campaign-test",
            "completed_at_ms": 1_000_000,
            "halt_result": "already-disabled",
            "cancelled_order_ids": [],
            "position": 0,
            "active_orders": 0,
            "gross_absolute_position": 0,
            "first_position_generation": 1,
            "first_fx_cash_generation": 1,
            "second_position_generation": 2,
            "second_fx_cash_generation": 2,
            "campaign_policy_sha256": "sha256:" + hashlib.sha256(
                policy_raw).hexdigest(),
            "campaign_enabled": False,
            "mutations_authorized": False,
            "local_paper_authorized": False,
            "identity_manifest_sha256": "sha256:" + "a" * 64,
            "deny_all_verified": True,
            "reboot_durable": True,
            "paper_only": True,
            "live_authorized": False,
        }

    def check_terminal_receipt(
            self, policy: dict[str, object], receipt: dict[str, object],
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy_path = root / "alpha.json"
            policy_raw = prepare.canonical(policy)
            policy_path.write_bytes(policy_raw)
            receipt["campaign_policy_sha256"] = (
                "sha256:" + hashlib.sha256(policy_raw).hexdigest())
            receipt_path = root / (
                "end-flat-" + str(policy["campaign_id"]) +
                ".receipt.json")
            receipt_path.write_text(json.dumps(receipt), encoding="ascii")
            receipt_path.chmod(0o600)

            def root_lstat(path: object) -> SimpleNamespace:
                metadata = REAL_LSTAT(path)
                return SimpleNamespace(
                    st_mode=metadata.st_mode, st_nlink=metadata.st_nlink,
                    st_uid=0, st_gid=0, st_size=metadata.st_size)

            with mock.patch.multiple(
                    prepare, POLICY_PATH=policy_path, STATE_ROOT=root), \
                    mock.patch.object(
                        prepare.os, "lstat", side_effect=root_lstat):
                prepare._require_terminal_end_flat(policy)

    def test_terminal_end_flat_accepts_exact_legacy_minimal_v4_receipt(
            self) -> None:
        policy = self.legacy_unbound_v4_policy()
        policy.update({
            "version": 4,
            "campaign_id": "campaign-test",
        })
        receipt = self.legacy_minimal_end_flat_receipt(
            prepare.canonical(policy))
        self.check_terminal_receipt(policy, receipt)

    def test_terminal_end_flat_rejects_legacy_minimal_receipt_for_v5(
            self) -> None:
        policy = self.v5_local_policy()
        policy["campaign_id"] = "campaign-test"
        receipt = self.legacy_minimal_end_flat_receipt(
            prepare.canonical(policy))
        with self.assertRaisesRegex(
                RuntimeError, "REPAIR_PREPARE_PRIOR_RECEIPT_INVALID"):
            self.check_terminal_receipt(policy, receipt)

    def test_terminal_end_flat_rejects_partial_current_receipt_as_legacy(
            self) -> None:
        policy = self.policy()
        policy.update({
            "version": 4,
            "campaign_id": "campaign-test",
        })
        receipt = self.legacy_minimal_end_flat_receipt(
            prepare.canonical(policy))
        receipt["authorized_connector_count"] = 0
        with self.assertRaisesRegex(
                RuntimeError, "REPAIR_PREPARE_PRIOR_RECEIPT_INVALID"):
            self.check_terminal_receipt(policy, receipt)

    def test_legacy_hsl5_cleanup_requires_exact_canonical_disabled_v4_policy(
            self) -> None:
        policy = self.policy()
        cases: list[tuple[str, dict[str, object], bool]] = []
        missing = dict(policy)
        missing.pop("domain_id")
        cases.append(("missing-field", missing, True))
        cases.extend((
            ("extra-field", {**policy, "unexpected": "value"}, True),
            ("wrong-domain", {**policy, "domain_id": "beta"}, True),
            ("enabled", {**policy, "enabled": True}, True),
            ("authorized", {
                **policy, "mutations_authorized": True,
            }, True),
            ("unsafe-order", {**policy, "order_type": "LMT"}, True),
            ("boolean-limit", {**policy, "max_cycles": True}, True),
            ("noncanonical", policy, False),
        ))
        terminal = mock.Mock()
        helper = mock.Mock()
        with tempfile.TemporaryDirectory() as directory:
            policy_path = Path(directory) / "alpha.json"
            with mock.patch.multiple(
                    prepare,
                    POLICY_PATH=policy_path,
                    campaign_lifecycle_locks=mock.Mock(
                        return_value=contextlib.nullcontext()),
                    _load_prepare_transaction=mock.Mock(return_value=None),
                    _require_terminal_end_flat=terminal,
                    _run_legacy_lease_cleanup_helper=helper):
                for label, document, canonical_encoding in cases:
                    with self.subTest(label=label):
                        raw = (prepare.canonical(document)
                               if canonical_encoding else
                               json.dumps(document).encode("ascii"))
                        policy_path.write_bytes(raw)
                        with self.assertRaisesRegex(
                                RuntimeError,
                                "REPAIR_LEGACY_LEASE_CLEANUP_POLICY_INVALID"):
                            prepare.migrate_legacy_hsl5_paper_leases()
        terminal.assert_not_called()
        helper.assert_not_called()

    def test_legacy_hsl5_cleanup_accepts_only_exact_unbound_v4_shape(
            self) -> None:
        self.assertEqual(
            prepare.PAPER_POLICY_DEPLOYMENT_BINDING_FIELDS,
            LEGACY_V4_UNBOUND_DEPLOYMENT_FIELDS)
        legacy = self.legacy_unbound_v4_policy()
        raw = prepare.canonical(legacy)
        self.assertEqual(
            prepare._validate_disabled_v4_cleanup_policy(legacy, raw),
            legacy)

        current = self.policy()
        fields = sorted(LEGACY_V4_UNBOUND_DEPLOYMENT_FIELDS)
        for count in range(1, len(fields)):
            for present in combinations(fields, count):
                with self.subTest(partial_fields=present):
                    partial = {
                        **legacy,
                        **{field: current[field] for field in present},
                    }
                    with self.assertRaisesRegex(
                            RuntimeError,
                            "REPAIR_LEGACY_LEASE_CLEANUP_POLICY_INVALID"):
                        prepare._validate_disabled_v4_cleanup_policy(
                            partial, prepare.canonical(partial))

    def test_legacy_hsl5_cleanup_binds_terminal_zero_and_remains_non_authorizing(
            self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_root = root / "state"
            state_root.mkdir()
            policy_path = root / "alpha.json"
            policy = self.legacy_unbound_v4_policy()
            policy.update({"version": 4, "campaign_id": "campaign-test"})
            policy_raw = prepare.canonical(policy)
            policy_path.write_bytes(policy_raw)
            terminal_path = state_root / (
                "end-flat-campaign-test.receipt.json")
            terminal = self.legacy_minimal_end_flat_receipt(policy_raw)
            terminal_raw = prepare.canonical(terminal)
            terminal_path.write_bytes(terminal_raw)
            intent_path = state_root / "cleanup.intent.json"
            receipt_path = state_root / "cleanup.receipt.json"
            pre_store = b"encrypted-hsl5-store\n"
            post_store = b"encrypted-hsl6-store\n"
            pre_sha = "sha256:" + hashlib.sha256(pre_store).hexdigest()
            post_sha = "sha256:" + hashlib.sha256(post_store).hexdigest()
            key_sha = prepare._cleanup_file_sha256(b"k" * 32)
            cleanup_lock_held = False

            @contextlib.contextmanager
            def cleanup_lock() -> object:
                nonlocal cleanup_lock_held
                self.assertFalse(cleanup_lock_held)
                cleanup_lock_held = True
                try:
                    yield
                finally:
                    cleanup_lock_held = False

            def install(path: Path, payload: bytes, mode: int = 0o644) -> None:
                if path == receipt_path:
                    self.assertTrue(cleanup_lock_held)
                path.write_bytes(payload)
                path.chmod(mode)

            helper = mock.Mock(return_value={
                "accepted": True,
                "reason_code": "OK",
                "retired_records": 3,
                "pre_store_sha256": pre_sha,
                "post_store_sha256": post_sha,
                "backup_store_sha256": pre_sha,
                "already_migrated": False,
            })
            deny_all = mock.Mock()
            no_residue = mock.Mock()
            inactive = mock.Mock()
            lock_reader = mock.Mock(return_value=b"")
            with mock.patch.multiple(
                    prepare,
                    POLICY_PATH=policy_path,
                    STATE_ROOT=state_root,
                    SUPERVISOR_LEASE_CLEANUP_INTENT=intent_path,
                    SUPERVISOR_LEASE_CLEANUP_RECEIPT=receipt_path,
                    campaign_lifecycle_locks=mock.Mock(
                        return_value=contextlib.nullcontext()),
                    _load_prepare_transaction=mock.Mock(return_value=None),
                    _require_terminal_end_flat=mock.Mock(),
                    _require_deny_all=deny_all,
                    _require_no_session_or_permit_residue=no_residue,
                    _require_runtime_inactive=inactive,
                    _read_supervisor_lease_store=mock.Mock(
                        side_effect=[pre_store, post_store]),
                    _read_supervisor_lease_backup=mock.Mock(
                        return_value=pre_store),
                    _read_supervisor_lease_key=mock.Mock(
                        return_value=b"k" * 32),
                    _read_supervisor_lease_lock=lock_reader,
                    _run_legacy_lease_cleanup_helper=helper,
                    supervisor_lease_cleanup_exclusive_lock=cleanup_lock,
                    atomic_install=mock.Mock(side_effect=install),
                    _remove_cleanup_intent=mock.Mock(
                        side_effect=lambda: intent_path.unlink()),
                    ), mock.patch.object(
                        prepare.pwd, "getpwnam",
                        side_effect=lambda name: SimpleNamespace(
                            pw_uid=2104 if name == "hepta-agent-alpha" else 2101,
                            pw_gid=2104 if name == "hepta-agent-alpha" else 2101)), \
                    mock.patch.object(
                        prepare.time, "time_ns",
                        side_effect=[1_000_000_000, 2_000_000_000]):
                result = prepare.migrate_legacy_hsl5_paper_leases()

            self.assertEqual(result["retired_records"], 3)
            self.assertIs(result["mutation_authorized"], False)
            self.assertIs(result["paper_only"], True)
            self.assertIs(result["live_authorized"], False)
            self.assertFalse(intent_path.exists())
            persisted = json.loads(receipt_path.read_bytes())
            self.assertEqual(persisted, result)
            helper.assert_called_once_with(
                pre_sha, key_sha, 2104, 2101, 2101)
            self.assertEqual(deny_all.call_count, 2)
            self.assertEqual(no_residue.call_count, 2)
            self.assertEqual(inactive.call_count, 2)
            self.assertEqual(lock_reader.call_count, 2)

    def test_legacy_hsl5_cleanup_fast_path_finishes_intent_delete_and_binds_key(
            self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_root = root / "state"
            state_root.mkdir()
            policy_path = root / "alpha.json"
            policy = self.policy()
            policy.update({"version": 4, "campaign_id": "campaign-test"})
            policy_raw = prepare.canonical(policy)
            policy_path.write_bytes(policy_raw)
            terminal_path = state_root / (
                "end-flat-campaign-test.receipt.json")
            terminal_path.write_bytes(prepare.canonical(
                self.legacy_minimal_end_flat_receipt(policy_raw)))
            intent_path = state_root / "cleanup.intent.json"
            receipt_path = state_root / "cleanup.receipt.json"
            pre_store = b"encrypted-hsl5-store\n"
            post_store = b"encrypted-hsl6-store\n"
            pre_sha = prepare._cleanup_file_sha256(pre_store)
            post_sha = prepare._cleanup_file_sha256(post_store)
            key_sha = prepare._cleanup_file_sha256(b"k" * 32)
            store_state = {"payload": pre_store}
            backup_state = {"payload": pre_store}
            key_state = {"payload": b"k" * 32}
            lock_reader = mock.Mock(return_value=b"")
            remove_attempts = 0
            receipt_publish_attempts = 0

            def install(path: Path, payload: bytes, mode: int = 0o644) -> None:
                nonlocal receipt_publish_attempts
                if path == receipt_path:
                    receipt_publish_attempts += 1
                    if receipt_publish_attempts == 1:
                        raise RuntimeError("simulated receipt publish failure")
                path.write_bytes(payload)
                path.chmod(mode)

            def helper(*_arguments: object) -> dict[str, object]:
                already_migrated = store_state["payload"] == post_store
                store_state["payload"] = post_store
                return {
                    "accepted": True,
                    "reason_code": "OK",
                    "retired_records": 3,
                    "pre_store_sha256": pre_sha,
                    "post_store_sha256": post_sha,
                    "backup_store_sha256": pre_sha,
                    "already_migrated": already_migrated,
                }

            def remove_intent() -> None:
                nonlocal remove_attempts
                remove_attempts += 1
                if remove_attempts == 1:
                    raise RuntimeError("simulated crash after receipt publish")
                intent_path.unlink()

            helper_mock = mock.Mock(side_effect=helper)
            with mock.patch.multiple(
                    prepare,
                    POLICY_PATH=policy_path,
                    STATE_ROOT=state_root,
                    SUPERVISOR_LEASE_CLEANUP_INTENT=intent_path,
                    SUPERVISOR_LEASE_CLEANUP_RECEIPT=receipt_path,
                    campaign_lifecycle_locks=mock.Mock(
                        return_value=contextlib.nullcontext()),
                    _load_prepare_transaction=mock.Mock(return_value=None),
                    _require_terminal_end_flat=mock.Mock(),
                    _require_deny_all=mock.Mock(),
                    _require_no_session_or_permit_residue=mock.Mock(),
                    _require_runtime_inactive=mock.Mock(),
                    _read_supervisor_lease_store=mock.Mock(
                        side_effect=lambda: store_state["payload"]),
                    _read_supervisor_lease_backup=mock.Mock(
                        side_effect=lambda: backup_state["payload"]),
                    _read_supervisor_lease_key=mock.Mock(
                        side_effect=lambda: key_state["payload"]),
                    _read_supervisor_lease_lock=lock_reader,
                    _run_legacy_lease_cleanup_helper=helper_mock,
                    supervisor_lease_cleanup_exclusive_lock=mock.Mock(
                        return_value=contextlib.nullcontext()),
                    atomic_install=mock.Mock(side_effect=install),
                    _remove_cleanup_intent=mock.Mock(
                        side_effect=remove_intent),
                    ), mock.patch.object(
                        prepare.os, "fstat",
                        side_effect=self.root_file_fstat), \
                    mock.patch.object(
                        prepare.os, "lstat",
                        side_effect=self.root_file_lstat), \
                    mock.patch.object(
                        prepare.pwd, "getpwnam",
                        side_effect=lambda name: SimpleNamespace(
                            pw_uid=2104 if name == "hepta-agent-alpha" else 2101,
                            pw_gid=2104 if name == "hepta-agent-alpha" else 2101)), \
                    mock.patch.object(
                        prepare.time, "time_ns",
                        side_effect=[
                            1_000_000_000, 2_000_000_000, 3_000_000_000,
                        ]):
                with self.assertRaisesRegex(
                        RuntimeError, "simulated receipt publish failure"):
                    prepare.migrate_legacy_hsl5_paper_leases()
                self.assertTrue(intent_path.exists())
                self.assertFalse(receipt_path.exists())

                with self.assertRaisesRegex(
                        RuntimeError, "simulated crash after receipt publish"):
                    prepare.migrate_legacy_hsl5_paper_leases()
                self.assertTrue(intent_path.exists())
                self.assertTrue(receipt_path.exists())

                recovered = prepare.migrate_legacy_hsl5_paper_leases()
                self.assertEqual(recovered["post_store_sha256"], post_sha)
                self.assertIs(recovered["helper_already_migrated"], True)
                self.assertFalse(intent_path.exists())
                self.assertEqual(helper_mock.call_count, 2)
                helper_mock.assert_has_calls([
                    mock.call(pre_sha, key_sha, 2104, 2101, 2101),
                    mock.call(pre_sha, key_sha, 2104, 2101, 2101),
                ])

                store_state["payload"] = b"drifted-hsl6-store\n"
                with self.assertRaisesRegex(
                        RuntimeError,
                        "REPAIR_LEGACY_LEASE_CLEANUP_RECEIPT_DRIFTED"):
                    prepare.migrate_legacy_hsl5_paper_leases()
                store_state["payload"] = post_store

                backup_state["payload"] = b"drifted-hsl5-backup\n"
                with self.assertRaisesRegex(
                        RuntimeError,
                        "REPAIR_LEGACY_LEASE_CLEANUP_RECEIPT_DRIFTED"):
                    prepare.migrate_legacy_hsl5_paper_leases()
                backup_state["payload"] = pre_store

                key_state["payload"] = b"x" * 32
                with self.assertRaisesRegex(
                        RuntimeError,
                        "REPAIR_LEGACY_LEASE_CLEANUP_RECEIPT_DRIFTED"):
                    prepare.migrate_legacy_hsl5_paper_leases()
                key_state["payload"] = b"k" * 32

                lock_reader.side_effect = [b"", b"drifted"]
                with self.assertRaisesRegex(
                        RuntimeError,
                        "REPAIR_LEGACY_LEASE_CLEANUP_RECEIPT_DRIFTED"):
                    prepare.migrate_legacy_hsl5_paper_leases()
                lock_reader.side_effect = None
                lock_reader.return_value = b""
                self.assertEqual(helper_mock.call_count, 2)

    def test_legacy_hsl5_cleanup_helper_rejects_zero_record_noop(self) -> None:
        expected = "sha256:" + "a" * 64
        key_sha = "sha256:" + "c" * 64
        response = {
            "accepted": True,
            "reason_code": "OK",
            "retired_records": 0,
            "pre_store_sha256": expected,
            "post_store_sha256": "sha256:" + "b" * 64,
            "backup_store_sha256": expected,
            "already_migrated": False,
        }
        completed = SimpleNamespace(
            returncode=0, stdout=json.dumps(response), stderr="")
        runner = mock.Mock(return_value=completed)
        with mock.patch.object(
                prepare.subprocess, "run", runner), \
                self.assertRaisesRegex(
                    RuntimeError,
                    "REPAIR_LEGACY_LEASE_CLEANUP_RESPONSE_INVALID"):
            prepare._run_legacy_lease_cleanup_helper(
                expected, key_sha, 2104, 2101, 2101)
        command = runner.call_args.args[0]
        self.assertEqual(command[command.index("--backup") + 1],
                         str(prepare.SUPERVISOR_LEASE_BACKUP))
        self.assertEqual(command[command.index("--lock-file") + 1],
                         str(prepare.SUPERVISOR_LEASE_CLEANUP_LOCK))
        self.assertEqual(command[command.index("--expected-key-uid") + 1],
                         "0")
        self.assertEqual(command[command.index("--expected-key-gid") + 1],
                         "0")
        self.assertEqual(command[command.index("--expected-key-mode") + 1],
                         "0400")
        self.assertEqual(
            command[command.index("--expected-key-file-sha256") + 1],
            key_sha)
        self.assertEqual(command[command.index("--expected-source-uid") + 1],
                         "2101")
        self.assertEqual(command[command.index("--expected-source-gid") + 1],
                         "2101")
        self.assertEqual(command[command.index("--expected-source-mode") + 1],
                         "0600")

    def test_legacy_hsl5_cleanup_helper_rejects_unchanged_post_store(self) -> None:
        expected = "sha256:" + "a" * 64
        key_sha = "sha256:" + "c" * 64
        completed = SimpleNamespace(
            returncode=0,
            stdout=json.dumps({
                "accepted": True,
                "reason_code": "OK",
                "retired_records": 1,
                "pre_store_sha256": expected,
                "post_store_sha256": expected,
                "backup_store_sha256": expected,
                "already_migrated": False,
            }),
            stderr="")
        with mock.patch.object(
                prepare.subprocess, "run", return_value=completed), \
                self.assertRaisesRegex(
                    RuntimeError,
                    "REPAIR_LEGACY_LEASE_CLEANUP_RESPONSE_INVALID"):
            prepare._run_legacy_lease_cleanup_helper(
                expected, key_sha, 2104, 2101, 2101)

    def test_cleanup_documents_require_exact_root_private_metadata(self) -> None:
        document = self.cleanup_intent_document()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "cleanup.json"
            path.write_bytes(prepare.canonical(document))
            path.chmod(0o600)

            def load(*, uid: int = 0, gid: int = 0) -> dict[str, object] | None:
                with mock.patch.object(
                        prepare.os, "fstat",
                        side_effect=lambda descriptor:
                        self.root_file_metadata(
                            REAL_FSTAT(descriptor), uid=uid, gid=gid)), \
                        mock.patch.object(
                            prepare.os, "lstat",
                            side_effect=lambda target:
                            self.root_file_metadata(
                                REAL_LSTAT(target), uid=uid, gid=gid)):
                    return prepare._load_cleanup_document(
                        path, prepare.SUPERVISOR_LEASE_CLEANUP_INTENT_SCHEMA)

            self.assertEqual(load(), document)
            for label, mode, uid, gid in (
                    ("world-readable", 0o644, 0, 0),
                    ("owner-read-only", 0o400, 0, 0),
                    ("wrong-owner", 0o600, 1, 0),
                    ("wrong-group", 0o600, 0, 1)):
                with self.subTest(label=label):
                    path.chmod(mode)
                    with self.assertRaisesRegex(
                            RuntimeError,
                            "REPAIR_LEGACY_LEASE_CLEANUP_ARTIFACT_UNSAFE"):
                        load(uid=uid, gid=gid)
            path.chmod(0o600)
            hardlink = root / "cleanup-hardlink.json"
            os.link(path, hardlink)
            with self.assertRaisesRegex(
                    RuntimeError,
                    "REPAIR_LEGACY_LEASE_CLEANUP_ARTIFACT_UNSAFE"):
                load()

    def test_supervisor_lease_cleanup_lock_is_exact_and_empty(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cleanup.lock"
            path.write_bytes(b"")
            path.chmod(0o644)

            def read() -> bytes:
                with mock.patch.object(
                        prepare, "SUPERVISOR_LEASE_CLEANUP_LOCK", path), \
                        mock.patch.object(
                            prepare.os, "fstat",
                            side_effect=self.root_file_fstat), \
                        mock.patch.object(
                            prepare.os, "lstat",
                            side_effect=self.root_file_lstat):
                    return prepare._read_supervisor_lease_lock()

            self.assertEqual(read(), b"")
            path.chmod(0o600)
            with self.assertRaisesRegex(
                    RuntimeError,
                    "REPAIR_LEGACY_LEASE_CLEANUP_LOCK_UNSAFE"):
                read()
            path.chmod(0o644)
            path.write_bytes(b"unexpected\n")
            with self.assertRaisesRegex(
                    RuntimeError,
                    "REPAIR_LEGACY_LEASE_CLEANUP_LOCK_INVALID"):
                read()
            path.write_bytes(b"x" * 4097)
            with self.assertRaisesRegex(
                    RuntimeError,
                    "REPAIR_LEGACY_LEASE_CLEANUP_LOCK_UNSAFE"):
                read()

    def test_supervisor_lease_cleanup_exclusive_lock_blocks_runtime_race(
            self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            parent.chmod(0o711)
            path = parent / "cleanup.lock"
            path.write_bytes(b"")
            path.chmod(0o644)

            def root_fstat(descriptor: int) -> SimpleNamespace:
                return self.root_file_metadata(REAL_FSTAT(descriptor))

            def root_lstat(target: object) -> SimpleNamespace:
                return self.root_file_metadata(REAL_LSTAT(target))

            def root_stat(
                    target: object, *arguments: object,
                    **keywords: object) -> SimpleNamespace:
                return self.root_file_metadata(
                    REAL_STAT(target, *arguments, **keywords))

            def lock_context() -> contextlib.AbstractContextManager[object]:
                return prepare.supervisor_lease_cleanup_exclusive_lock()

            patches = (
                mock.patch.object(
                    prepare, "SUPERVISOR_LEASE_CLEANUP_LOCK", path),
                mock.patch.object(prepare.os, "fstat", side_effect=root_fstat),
                mock.patch.object(prepare.os, "lstat", side_effect=root_lstat),
                mock.patch.object(prepare.os, "stat", side_effect=root_stat),
            )
            shared = os.open(path, os.O_RDONLY | os.O_CLOEXEC)
            try:
                fcntl.flock(shared, fcntl.LOCK_SH | fcntl.LOCK_NB)
                with patches[0], patches[1], patches[2], patches[3], \
                        self.assertRaisesRegex(
                            RuntimeError,
                            "REPAIR_LEGACY_LEASE_CLEANUP_IN_PROGRESS"):
                    with lock_context():
                        self.fail("exclusive lock accepted an active runtime")
            finally:
                fcntl.flock(shared, fcntl.LOCK_UN)
                os.close(shared)

            with mock.patch.object(
                    prepare, "SUPERVISOR_LEASE_CLEANUP_LOCK", path), \
                    mock.patch.object(
                        prepare.os, "fstat", side_effect=root_fstat), \
                    mock.patch.object(
                        prepare.os, "lstat", side_effect=root_lstat), \
                    mock.patch.object(
                        prepare.os, "stat", side_effect=root_stat), \
                    lock_context():
                contender = os.open(path, os.O_RDONLY | os.O_CLOEXEC)
                try:
                    with self.assertRaises(BlockingIOError):
                        fcntl.flock(
                            contender, fcntl.LOCK_SH | fcntl.LOCK_NB)
                finally:
                    os.close(contender)

    def test_legacy_hsl5_cleanup_documents_require_exact_fields_and_types(
            self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cleanup.json"
            cases = (
                (self.cleanup_intent_document(),
                 prepare.SUPERVISOR_LEASE_CLEANUP_INTENT_SCHEMA,
                 "unexpected", "value"),
                (self.cleanup_intent_document(),
                 prepare.SUPERVISOR_LEASE_CLEANUP_INTENT_SCHEMA,
                 "expected_source_mode", True),
                (self.cleanup_receipt_document(),
                 prepare.SUPERVISOR_LEASE_CLEANUP_RECEIPT_SCHEMA,
                 "retired_records", True),
                (self.cleanup_receipt_document(),
                 prepare.SUPERVISOR_LEASE_CLEANUP_RECEIPT_SCHEMA,
                 "unexpected", "value"),
                (self.cleanup_receipt_document(),
                 prepare.SUPERVISOR_LEASE_CLEANUP_RECEIPT_SCHEMA,
                 "expected_source_uid", 2102),
                (self.cleanup_receipt_document(),
                 prepare.SUPERVISOR_LEASE_CLEANUP_RECEIPT_SCHEMA,
                 "post_store_sha256", "sha256:" + "5" * 64),
                (self.cleanup_intent_document(),
                 prepare.SUPERVISOR_LEASE_CLEANUP_INTENT_SCHEMA,
                 "expected_key_mode", True),
            )
            for original, schema, field, value in cases:
                with self.subTest(schema=schema, field=field):
                    body = dict(original)
                    body.pop("body_sha256")
                    body[field] = value
                    path.write_bytes(prepare.canonical(
                        prepare._sealed_cleanup_document(body)))
                    path.chmod(0o600)
                    with mock.patch.object(
                            prepare.os, "fstat",
                            side_effect=self.root_file_fstat), \
                            mock.patch.object(
                                prepare.os, "lstat",
                                side_effect=self.root_file_lstat), \
                            self.assertRaisesRegex(
                                RuntimeError,
                                "REPAIR_LEGACY_LEASE_CLEANUP_ARTIFACT_INVALID"):
                        prepare._load_cleanup_document(path, schema)

    @staticmethod
    def deployment_snapshot() -> prepare.DeploymentEvidenceSnapshot:
        files = [
            {"path": str(path), "sha256": "sha256:" + "b" * 64,
             "mode": mode}
            for path, mode in prepare.LOCAL_PAPER_DEPLOYMENT_FILES
        ]
        body: dict[str, object] = {
            "schema": prepare.LOCAL_PAPER_DEPLOYMENT_EVIDENCE_SCHEMA,
            "version": 1,
            "source_freeze_commit": "1" * 40,
            "source_freeze_tree": "2" * 40,
            "source_manifest_sha256": "sha256:" + "3" * 64,
            "source_baseline_sha256": "sha256:" + "a" * 64,
            "install_transaction_id": "install-transaction-round98",
            "installed_at_ms": 1,
            "generated_at_ms": 2,
            "files": files,
            "certified_install_closure_file_sha256":
                "sha256:" + "4" * 64,
            "certified_install_closure_body_sha256":
                "sha256:" + "5" * 64,
            "paper_authorized": False,
            "live_authorized": False,
            "mutation_authorized": False,
        }
        document = {
            **body,
            "body_sha256": "sha256:" + hashlib.sha256(
                prepare.canonical(body)).hexdigest(),
        }
        payload = prepare.canonical(document)
        return prepare.DeploymentEvidenceSnapshot(
            payload=payload, document=document,
            evidence_identity=(1,), installed_identities=tuple(
                (str(path), (index + 1,))
                for index, (path, _mode) in enumerate(
                    prepare.LOCAL_PAPER_DEPLOYMENT_FILES)))

    def test_bound_v5_upgrade_validates_old_artifact_not_old_runtime(
            self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            installed = root / "installed-runtime"
            installed.write_bytes(b"new-runtime\n")
            installed.chmod(0o755)
            evidence_path = root / "deployment.json"
            old_digest = "sha256:" + hashlib.sha256(
                b"old-runtime\n").hexdigest()
            body: dict[str, object] = {
                "schema": prepare.LOCAL_PAPER_DEPLOYMENT_EVIDENCE_SCHEMA,
                "version": 1,
                "source_freeze_commit": "1" * 40,
                "source_freeze_tree": "2" * 40,
                "source_manifest_sha256": "sha256:" + "3" * 64,
                "source_baseline_sha256": "sha256:" + "a" * 64,
                "install_transaction_id": "install-old-generation",
                "installed_at_ms": 1,
                "generated_at_ms": 2,
                "files": [{
                    "path": str(installed), "sha256": old_digest,
                    "mode": 0o755,
                }],
                "certified_install_closure_file_sha256":
                    "sha256:" + "4" * 64,
                "certified_install_closure_body_sha256":
                    "sha256:" + "5" * 64,
                "paper_authorized": False,
                "live_authorized": False,
                "mutation_authorized": False,
            }
            document = {
                **body,
                "body_sha256": "sha256:" + hashlib.sha256(
                    prepare.canonical(body)).hexdigest(),
            }
            payload = prepare.canonical(document)
            evidence_path.write_bytes(payload)
            evidence_path.chmod(0o600)
            policy = self.v5_local_policy()
            policy.update({
                "source_baseline_sha256":
                    document["source_baseline_sha256"],
                "deployment_evidence_file_sha256":
                    "sha256:" + hashlib.sha256(payload).hexdigest(),
                "deployment_evidence_body_sha256": document["body_sha256"],
                "deployment_install_transaction_id":
                    document["install_transaction_id"],
            })
            with mock.patch.multiple(
                    prepare,
                    LOCAL_PAPER_DEPLOYMENT_EVIDENCE_PATH=evidence_path,
                    LOCAL_PAPER_DEPLOYMENT_FILES=((installed, 0o755),),
                    _deployment_strategy_sha256=mock.Mock(
                        return_value="sha256:" + "b" * 64),
                    _local_v5_disabled_seed_from_deployment=mock.Mock(
                        return_value=policy)), \
                    mock.patch.object(
                        prepare.os, "fstat", side_effect=lambda descriptor:
                            self.root_file_metadata(REAL_FSTAT(descriptor))), \
                    mock.patch.object(
                        prepare.os, "lstat", side_effect=lambda path:
                            self.root_file_metadata(REAL_LSTAT(path))):
                artifact = (
                    prepare._load_local_paper_deployment_evidence_artifact())
                self.assertEqual(artifact.document, document)
                self.assertEqual(artifact.installed_identities, ())
                with self.assertRaisesRegex(
                        RuntimeError, "REPAIR_DEPLOYED_FILE_INVALID"):
                    prepare._load_local_paper_deployment_evidence()
                self.assertEqual(
                    prepare._prior_v5_local_deployment_artifact(policy),
                    (artifact, True))
                for field in (
                        "source_baseline_sha256",
                        "deployment_evidence_file_sha256",
                        "deployment_evidence_body_sha256",
                        "deployment_install_transaction_id"):
                    drifted = dict(policy)
                    drifted[field] = (
                        "install-drifted-generation"
                        if field == "deployment_install_transaction_id" else
                        "sha256:" + "f" * 64)
                    with self.subTest(field=field), self.assertRaisesRegex(
                            RuntimeError,
                            "REPAIR_SOURCE_POLICY_DEPLOYMENT_MISMATCH"):
                        prepare._prior_v5_local_deployment_artifact(drifted)
                for field, value in (
                        ("campaign_id", "stale-bound-seed"),
                        ("strategy_sha256", "sha256:" + "e" * 64),
                        ("max_cycles", 719)):
                    drifted = dict(policy)
                    drifted[field] = value
                    with self.subTest(field=field):
                        self.assertEqual(
                            prepare._prior_v5_local_deployment_artifact(
                                drifted),
                            (artifact, False))
                terminal = mock.Mock()
                with mock.patch.object(
                        prepare, "require_agent_inactive"), \
                        mock.patch.object(
                            prepare, "_require_terminal_end_flat", terminal), \
                        mock.patch.object(prepare, "_require_deny_all"), \
                        mock.patch.object(
                            prepare, "_require_no_session_or_permit_residue"), \
                        mock.patch.object(
                            prepare, "_require_runtime_inactive"):
                    prepare.require_fresh_campaign_admission(
                        drifted, verified_local_disabled_seed=False)
                terminal.assert_called_once_with(drifted)

    def test_v5_upgrade_accepts_only_exact_unbound_seed_without_evidence(
            self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            evidence_path = Path(directory) / "deployment.json"
            policy = self.v5_local_policy()
            with mock.patch.object(
                    prepare, "LOCAL_PAPER_DEPLOYMENT_EVIDENCE_PATH",
                    evidence_path):
                self.assertEqual(
                    prepare._prior_v5_local_deployment_artifact(policy),
                    (None, True))
                cases = {
                    "partial_binding": {
                        "source_baseline_sha256": "sha256:" + "1" * 64},
                    "stale_campaign": {"campaign_id": "stale-disabled-seed"},
                    "strategy_bound": {
                        "strategy_sha256": "sha256:" + "2" * 64},
                    "window_bound": {"valid_after_ms": 1},
                }
                for label, changed in cases.items():
                    drifted = dict(policy)
                    drifted.update(changed)
                    with self.subTest(label=label), self.assertRaisesRegex(
                            RuntimeError,
                            "REPAIR_SOURCE_POLICY_DEPLOYMENT_MISMATCH"):
                        prepare._prior_v5_local_deployment_artifact(drifted)
                evidence_path.write_bytes(b"present\n")
                with self.assertRaisesRegex(
                        RuntimeError,
                        "REPAIR_SOURCE_POLICY_DEPLOYMENT_MISMATCH"):
                    prepare._prior_v5_local_deployment_artifact(policy)

    def test_prior_deployment_artifact_replacement_is_drift(self) -> None:
        expected = self.deployment_snapshot()._replace(
            installed_identities=())
        replacement = expected._replace(evidence_identity=(2,))
        with mock.patch.object(
                prepare, "_load_local_paper_deployment_evidence_artifact",
                return_value=replacement), self.assertRaisesRegex(
                    RuntimeError, "REPAIR_DEPLOYMENT_EVIDENCE_DRIFTED"):
            prepare._require_deployment_artifact_unchanged(expected)

    def test_deployment_artifacts_reject_unbound_transaction_id(
            self) -> None:
        snapshot = self.deployment_snapshot()
        evidence_body = dict(snapshot.document)
        evidence_body.pop("body_sha256")
        evidence_body["install_transaction_id"] = (
            prepare.UNBOUND_DEPLOYMENT_TRANSACTION_ID)
        evidence = {
            **evidence_body,
            "body_sha256": "sha256:" + hashlib.sha256(
                prepare.canonical(evidence_body)).hexdigest(),
        }
        with self.assertRaisesRegex(
                RuntimeError, "REPAIR_DEPLOYMENT_EVIDENCE_INVALID"):
            prepare._validate_deployment_evidence_document(evidence)

        with tempfile.TemporaryDirectory() as directory:
            closure_path = Path(directory) / "closure.json"
            closure_body: dict[str, object] = {
                "schema": prepare.CERTIFIED_INSTALL_CLOSURE_SCHEMA,
                "version": 1,
                "source_freeze_commit": "1" * 40,
                "source_freeze_tree": "2" * 40,
                "source_manifest_sha256": "sha256:" + "3" * 64,
                "source_baseline_sha256": "sha256:" + "4" * 64,
                "install_transaction_id":
                    prepare.UNBOUND_DEPLOYMENT_TRANSACTION_ID,
                "installed_at_ms": 1,
                "files": snapshot.document["files"],
            }
            closure = {
                **closure_body,
                "body_sha256": "sha256:" + hashlib.sha256(
                    prepare.canonical(closure_body)).hexdigest(),
            }
            payload = prepare.canonical(closure)
            closure_path.write_bytes(payload)
            closure_path.chmod(0o600)
            with mock.patch.object(
                    prepare, "CERTIFIED_INSTALL_CLOSURE_PATH",
                    closure_path), mock.patch.object(
                        prepare.os, "lstat",
                        side_effect=self.root_file_lstat), \
                    self.assertRaisesRegex(
                        RuntimeError,
                        "REPAIR_CERTIFIED_INSTALL_CLOSURE_INVALID"):
                prepare._load_certified_install_closure(
                    closure_path,
                    "sha256:" + hashlib.sha256(payload).hexdigest())

    def _assert_record_deployment_upgrade_recovers(
            self, *, bound: bool, policy_mode: int = 0o600,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy_path = root / "policy.json"
            evidence_path = root / "deployment.json"
            certified_path = root / "certified.json"
            installed = root / "installed-runtime"
            installed.write_bytes(b"new-runtime\n")
            installed.chmod(0o755)

            old_body: dict[str, object] = {
                "schema": prepare.LOCAL_PAPER_DEPLOYMENT_EVIDENCE_SCHEMA,
                "version": 1,
                "source_freeze_commit": "1" * 40,
                "source_freeze_tree": "2" * 40,
                "source_manifest_sha256": "sha256:" + "3" * 64,
                "source_baseline_sha256": "sha256:" + "4" * 64,
                "install_transaction_id": "install-old-generation",
                "installed_at_ms": 1,
                "generated_at_ms": 2,
                "files": [{
                    "path": str(installed),
                    "sha256": "sha256:" + hashlib.sha256(
                        b"old-runtime\n").hexdigest(),
                    "mode": 0o755,
                }],
                "certified_install_closure_file_sha256":
                    "sha256:" + "5" * 64,
                "certified_install_closure_body_sha256":
                    "sha256:" + "6" * 64,
                "paper_authorized": False,
                "live_authorized": False,
                "mutation_authorized": False,
            }
            old_document = {
                **old_body,
                "body_sha256": "sha256:" + hashlib.sha256(
                    prepare.canonical(old_body)).hexdigest(),
            }
            old_payload = prepare.canonical(old_document)
            policy = self.v5_local_policy()
            if bound:
                evidence_path.write_bytes(old_payload)
                evidence_path.chmod(0o600)
                policy.update({
                    "source_baseline_sha256":
                        old_document["source_baseline_sha256"],
                    "deployment_evidence_file_sha256":
                        "sha256:" + hashlib.sha256(old_payload).hexdigest(),
                    "deployment_evidence_body_sha256":
                        old_document["body_sha256"],
                    "deployment_install_transaction_id":
                        old_document["install_transaction_id"],
                })
            policy_path.write_bytes(prepare.canonical(policy))
            policy_path.chmod(policy_mode)

            certified_body: dict[str, object] = {
                "schema": prepare.CERTIFIED_INSTALL_CLOSURE_SCHEMA,
                "version": 1,
                "source_freeze_commit": "7" * 40,
                "source_freeze_tree": "8" * 40,
                "source_manifest_sha256": "sha256:" + "9" * 64,
                "source_baseline_sha256": "sha256:" + "a" * 64,
                "install_transaction_id": "install-new-generation",
                "installed_at_ms": 3,
                "files": [{
                    "path": str(installed),
                    "sha256": "sha256:" + hashlib.sha256(
                        b"new-runtime\n").hexdigest(),
                    "mode": 0o755,
                }],
            }
            certified = {
                **certified_body,
                "body_sha256": "sha256:" + hashlib.sha256(
                    prepare.canonical(certified_body)).hexdigest(),
            }
            certified_raw = prepare.canonical(certified)
            certified_path.write_bytes(certified_raw)
            certified_path.chmod(0o600)

            def rooted(metadata: object) -> SimpleNamespace:
                return SimpleNamespace(
                    st_dev=metadata.st_dev, st_ino=metadata.st_ino,
                    st_mode=metadata.st_mode, st_nlink=metadata.st_nlink,
                    st_uid=0, st_gid=0, st_size=metadata.st_size,
                    st_mtime_ns=metadata.st_mtime_ns,
                    st_ctime_ns=metadata.st_ctime_ns)

            def write(path: Path, payload: bytes, mode: int = 0o600) -> None:
                path.write_bytes(payload)
                path.chmod(mode)

            policy_write_attempts = 0

            def crash_once_then_write(
                    path: Path, payload: bytes, mode: int = 0o600,
            ) -> None:
                nonlocal policy_write_attempts
                if path == policy_path:
                    policy_write_attempts += 1
                    if policy_write_attempts == 1:
                        raise RuntimeError("simulated-policy-write-crash")
                write(path, payload, mode)

            def migrated(snapshot: prepare.DeploymentEvidenceSnapshot,
                         ) -> dict[str, object]:
                binding = prepare._deployment_binding_record(snapshot)
                result = self.v5_local_policy()
                result.update({
                    "source_baseline_sha256":
                        binding["source_baseline_sha256"],
                    "deployment_evidence_file_sha256":
                        binding["evidence_file_sha256"],
                    "deployment_evidence_body_sha256":
                        binding["evidence_body_sha256"],
                    "deployment_install_transaction_id":
                        binding["install_transaction_id"],
                })
                return result

            with mock.patch.multiple(
                    prepare,
                    POLICY_PATH=policy_path,
                    CERTIFIED_INSTALL_CLOSURE_PATH=certified_path,
                    LOCAL_PAPER_DEPLOYMENT_EVIDENCE_PATH=evidence_path,
                    DEPLOYMENT_EVIDENCE_TRANSACTION_PATH=
                        root / "deployment-transaction.json",
                    LOCAL_PAPER_DEPLOYMENT_FILES=((installed, 0o755),),
                    campaign_lifecycle_locks=mock.Mock(
                        return_value=contextlib.nullcontext()),
                    _load_prepare_transaction=mock.Mock(return_value=None),
                    _load_deployment_evidence_transaction=mock.Mock(
                        side_effect=
                            ORIGINAL_LOAD_DEPLOYMENT_EVIDENCE_TRANSACTION),
                    require_fresh_campaign_admission=mock.Mock(),
                    _deployment_strategy_sha256=mock.Mock(
                        return_value="sha256:" + "b" * 64),
                    _local_v5_disabled_seed_from_deployment=mock.Mock(
                        return_value=policy),
                    _v5_local_seed_from_deployment=mock.Mock(
                        side_effect=migrated),
                    atomic_install=mock.Mock(side_effect=write),
                    atomic_write=mock.Mock(
                        side_effect=crash_once_then_write)), \
                    mock.patch.object(
                        prepare.os, "fstat", side_effect=lambda descriptor:
                            rooted(REAL_FSTAT(descriptor))), \
                    mock.patch.object(
                        prepare.os, "lstat", side_effect=lambda path:
                            rooted(REAL_LSTAT(path))), \
                    mock.patch.object(
                        prepare.time, "time_ns", return_value=10_000_000):
                closure_sha = "sha256:" + hashlib.sha256(
                    certified_raw).hexdigest()
                if policy_mode != 0o600:
                    with self.assertRaisesRegex(
                            RuntimeError,
                            "REPAIR_SOURCE_POLICY_PATH_UNSAFE"):
                        prepare.record_local_paper_deployment_evidence(
                            certified_closure_path=certified_path,
                            certified_closure_file_sha256=closure_sha)
                    self.assertFalse(
                        (root / "deployment-transaction.json").exists())
                    return
                with self.assertRaisesRegex(
                        RuntimeError, "simulated-policy-write-crash"):
                    prepare.record_local_paper_deployment_evidence(
                        certified_closure_path=certified_path,
                        certified_closure_file_sha256=closure_sha)
                self.assertTrue(
                    (root / "deployment-transaction.json").exists())
                self.assertEqual(
                    json.loads(policy_path.read_bytes())["source_baseline_sha256"],
                    (old_document["source_baseline_sha256"]
                     if bound else prepare.ZERO_DIGEST))
                snapshot = prepare.record_local_paper_deployment_evidence(
                    certified_closure_path=certified_path,
                    certified_closure_file_sha256=closure_sha)
                self.assertFalse(
                    (root / "deployment-transaction.json").exists())
            self.assertEqual(
                snapshot.document["source_baseline_sha256"],
                certified["source_baseline_sha256"])
            self.assertEqual(
                snapshot.document["install_transaction_id"],
                certified["install_transaction_id"])
            rebound = json.loads(policy_path.read_bytes())
            self.assertEqual(
                rebound["deployment_install_transaction_id"],
                certified["install_transaction_id"])

    def test_record_deployment_upgrades_exact_bound_v5_generation(
            self) -> None:
        self._assert_record_deployment_upgrade_recovers(bound=True)

    def test_record_deployment_upgrades_exact_unbound_v5_generation(
            self) -> None:
        self._assert_record_deployment_upgrade_recovers(bound=False)

    def test_record_deployment_rejects_inexact_v5_policy_mode_before_wal(
            self) -> None:
        self._assert_record_deployment_upgrade_recovers(
            bound=True, policy_mode=0o400)

    def transaction_record(self) -> dict[str, object]:
        campaign_id = "local-ai-paper-test-transaction-123456789abc"
        deadline_seconds = 1_000_003_600
        previous_policy = prepare.canonical(self.policy())
        target = dict(self.policy())
        target.update({
            "campaign_id": campaign_id,
            "enabled": True,
            "mutations_authorized": True,
            "strategy_id": prepare.STRATEGY_ID,
            "strategy_version": prepare.STRATEGY_VERSION,
            "strategy_sha256": "sha256:" + "1" * 64,
            "valid_after_ms": 999_999_999_000,
            "expires_at_ms": deadline_seconds * 1000,
            "max_cycles": 2,
            "max_holding_ms": 0,
            "tif": "DAY",
        })
        deployment = self.deployment_snapshot()
        binding = prepare._deployment_binding_record(deployment)
        target.update({
            "deployment_evidence_file_sha256":
                binding["evidence_file_sha256"],
            "deployment_evidence_body_sha256":
                binding["evidence_body_sha256"],
            "deployment_install_transaction_id":
                binding["install_transaction_id"],
        })
        previous_env = self.agent_env()
        target_env = prepare.render_agent_env(
            campaign_id, "sha256:" + "1" * 64,
            "auth-gen-new", "profile-new", previous_env)
        previous_unit_files = {
            path: prepare.UnitFileSnapshot(None, None)
            for path in prepare.generated_stop_unit_paths()
        }
        previous_unit_states = {
            unit: prepare.SystemdUnitSnapshot(
                "loaded", "disabled", "inactive")
            for unit in (
                prepare.stop_runtime_units() +
                prepare.background_timer_units())
        }
        with mock.patch.object(
                prepare.time, "time_ns",
                return_value=1_000_000_000_000_000_000):
            return prepare._prepare_transaction_record(
                campaign_id, deadline_seconds, 3600,
                previous_policy, previous_env, previous_unit_files,
                previous_unit_states, prepare.canonical(target), target_env,
                prepare.stop_unit_payloads(deadline_seconds), deployment)

    def v5_transaction_record(self) -> dict[str, object]:
        previous = self.v5_policy()
        target = dict(previous)
        target["enabled"] = True
        target["mutations_authorized"] = True
        deadline_seconds = int(target["expires_at_ms"]) // 1000
        previous_env = self.agent_env()
        target_env = prepare.render_agent_env(
            str(target["campaign_id"]), str(target["strategy_sha256"]),
            "auth-gen-new", "profile-new", previous_env,
            strategy_id=str(target["strategy_id"]),
            strategy_version=str(target["strategy_version"]))
        previous_unit_files = {
            path: prepare.UnitFileSnapshot(None, None)
            for path in prepare.generated_stop_unit_paths()
        }
        previous_unit_states = {
            unit: prepare.SystemdUnitSnapshot(
                "loaded", "disabled", "inactive")
            for unit in (
                prepare.stop_runtime_units() +
                prepare.background_timer_units())
        }
        with mock.patch.object(
                prepare.time, "time_ns",
                return_value=1_000_000_000_000_000_000):
            return prepare._prepare_transaction_record(
                str(target["campaign_id"]), deadline_seconds, 300,
                prepare.canonical(previous), previous_env,
                previous_unit_files, previous_unit_states,
                prepare.canonical(target), target_env,
                prepare.stop_unit_payloads(deadline_seconds),
                self.deployment_snapshot())

    def test_certified_closure_rejects_self_consistent_stale_install(
            self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy_path = root / "policy.json"
            legacy_policy = self.policy()
            legacy_policy["source_baseline_sha256"] = "sha256:" + "c" * 64
            policy_path.write_bytes(prepare.canonical(legacy_policy))
            installed = root / "installed-runtime"
            installed.write_bytes(b"old-runtime\n")
            installed.chmod(0o755)
            certified_body: dict[str, object] = {
                "schema": prepare.CERTIFIED_INSTALL_CLOSURE_SCHEMA,
                "version": 1,
                "source_freeze_commit": "1" * 40,
                "source_freeze_tree": "2" * 40,
                "source_manifest_sha256": "sha256:" + "3" * 64,
                "source_baseline_sha256": "sha256:" + "a" * 64,
                "install_transaction_id": "install-transaction-round98",
                "installed_at_ms": 1,
                "files": [{
                    "path": str(installed),
                    "sha256": "sha256:" + hashlib.sha256(
                        b"new-runtime\n").hexdigest(),
                    "mode": 0o755,
                }],
            }
            certified = {
                **certified_body,
                "body_sha256": "sha256:" + hashlib.sha256(
                    prepare.canonical(certified_body)).hexdigest(),
            }
            certified_raw = prepare.canonical(certified)
            certified_path = root / "certified.json"
            certified_path.write_bytes(certified_raw)
            certified_path.chmod(0o600)
            evidence_path = root / "deployment.json"

            def rooted(metadata: object) -> SimpleNamespace:
                return SimpleNamespace(
                    st_dev=metadata.st_dev, st_ino=metadata.st_ino,
                    st_mode=metadata.st_mode, st_nlink=metadata.st_nlink,
                    st_uid=0, st_gid=0, st_size=metadata.st_size,
                    st_mtime_ns=metadata.st_mtime_ns,
                    st_ctime_ns=metadata.st_ctime_ns)

            def install(path: Path, payload: bytes, mode: int = 0o644) -> None:
                path.write_bytes(payload)
                path.chmod(mode)

            common = (
                mock.patch.multiple(
                    prepare,
                    POLICY_PATH=policy_path,
                    CERTIFIED_INSTALL_CLOSURE_PATH=certified_path,
                    LOCAL_PAPER_DEPLOYMENT_EVIDENCE_PATH=evidence_path,
                    LOCAL_PAPER_DEPLOYMENT_FILES=((installed, 0o755),),
                    campaign_lifecycle_locks=mock.Mock(
                        return_value=contextlib.nullcontext()),
                    _load_prepare_transaction=mock.Mock(return_value=None),
                    require_fresh_campaign_admission=mock.Mock(),
                    _v5_local_seed_from_deployment=mock.Mock(
                        return_value=self.v5_local_policy()),
                    atomic_install=mock.Mock(side_effect=install),
                    atomic_write=mock.Mock(side_effect=lambda path, payload:
                                           install(path, payload, 0o600))),
                mock.patch.object(
                    prepare.os, "fstat", side_effect=lambda descriptor:
                        rooted(REAL_FSTAT(descriptor))),
                mock.patch.object(
                    prepare.os, "lstat", side_effect=lambda path:
                        rooted(REAL_LSTAT(path))),
                mock.patch.object(
                    prepare.time, "time_ns", return_value=10_000_000),
            )
            with contextlib.ExitStack() as stack:
                for patcher in common:
                    stack.enter_context(patcher)
                with self.assertRaisesRegex(
                        RuntimeError,
                        "REPAIR_DEPLOYED_FILE_CERTIFICATION_MISMATCH"):
                    prepare.record_local_paper_deployment_evidence(
                        certified_closure_path=certified_path,
                        certified_closure_file_sha256="sha256:" +
                        hashlib.sha256(certified_raw).hexdigest())
            self.assertFalse(evidence_path.exists())

            installed.write_bytes(b"new-runtime\n")
            installed.chmod(0o755)
            with contextlib.ExitStack() as stack:
                for patcher in common:
                    stack.enter_context(patcher)
                snapshot = prepare.record_local_paper_deployment_evidence(
                    certified_closure_path=certified_path,
                    certified_closure_file_sha256="sha256:" +
                    hashlib.sha256(certified_raw).hexdigest())
            self.assertEqual(
                snapshot.document["source_baseline_sha256"],
                "sha256:" + "a" * 64)
            self.assertEqual(
                snapshot.document[
                    "certified_install_closure_file_sha256"],
                "sha256:" + hashlib.sha256(certified_raw).hexdigest())
            self.assertEqual(stat.S_IMODE(evidence_path.stat().st_mode), 0o600)
            migrated = json.loads(policy_path.read_bytes())
            self.assertEqual(migrated["schema"], prepare.PAPER_POLICY_V5_SCHEMA)
            self.assertEqual(migrated["admission_mode"], "local-only")
            self.assertIs(migrated["enabled"], False)

    def test_wal_recovery_rejects_deployment_drift_after_policy_publish(
            self) -> None:
        record = self.transaction_record()
        drifted = self.deployment_snapshot()
        drifted_document = dict(drifted.document)
        drifted_document["install_transaction_id"] = (
            "install-transaction-drifted")
        drifted_payload = prepare.canonical(drifted_document)
        drifted = prepare.DeploymentEvidenceSnapshot(
            payload=drifted_payload, document=drifted_document,
            evidence_identity=(2,), installed_identities=())
        with tempfile.TemporaryDirectory() as directory:
            policy_path = Path(directory) / "policy.json"
            policy_path.write_bytes(prepare._decode_payload_record(
                record["target_policy"]))
            rollback = mock.Mock()
            with mock.patch.multiple(
                    prepare,
                    POLICY_PATH=policy_path,
                    _load_prepare_transaction=mock.Mock(return_value=record),
                    _load_local_paper_deployment_evidence=mock.Mock(
                        return_value=drifted),
                    _rollback_prepare_transaction=rollback,
                    _remove_prepare_transaction=mock.Mock()):
                result = prepare.reconcile_prepare_transaction_locked()
            self.assertEqual(result, "ROLLED_BACK")
            rollback.assert_called_once_with(
                record, require_safe_boundary=True)

    def test_deployment_closure_covers_every_ib_installed_authority(self) -> None:
        operator_source = SourceFileLoader(
            "hepta_operator_closure_check",
            str(ROOT / "scripts/hepta_ib_paper_campaign_operator.py"))
        operator_spec = importlib.util.spec_from_loader(
            operator_source.name, operator_source)
        assert (operator_spec is not None and
                operator_spec.loader is not None)
        operator = importlib.util.module_from_spec(operator_spec)
        sys.modules[operator_spec.name] = operator
        operator_spec.loader.exec_module(operator)
        self.assertEqual(
            prepare.LOCAL_PAPER_DEPLOYMENT_FILES,
            operator.LOCAL_PAPER_DEPLOYMENT_FILES)
        cmake = (ROOT / "HeptaTrade/CMakeLists.txt").read_text(
            encoding="utf-8")
        marker = "if(HEPTA_ENABLE_IBAPI)\n    # Never package"
        self.assertIn(marker, cmake)
        ib_block = cmake.split(marker, 1)[1].split("\nendif()", 1)[0]
        renamed_programs = set(re.findall(
            r"\n\s*RENAME (hepta-[A-Za-z0-9._-]+)", ib_block))
        unit_names = set(re.findall(
            r'\.\./systemd/(hepta-[^"/]+\.(?:service|socket|timer))',
            ib_block))
        installed_paths = {
            str(path) for path, _mode in
            prepare.LOCAL_PAPER_DEPLOYMENT_FILES
        }
        expected_from_ib_block = {
            "/usr/libexec/hepta-ib-executiond",
            *("/usr/libexec/" + name for name in renamed_programs),
            *("/usr/lib/systemd/system/" + name for name in unit_names),
        }
        self.assertFalse(expected_from_ib_block - installed_paths)
        for required in (
                "/usr/libexec/hepta-agent-mcp-launcher",
                "/usr/libexec/hepta-agent-session-bootstrap",
                "/usr/libexec/hepta_agent_trust_domain.py",
                "/usr/libexec/hepta-mcp-server",
                "/usr/libexec/hepta-tool-gatewayd"):
            self.assertIn(required, installed_paths)

    def test_prepare_rejects_zero_cycle_budget_before_side_effects(self) -> None:
        with mock.patch.object(prepare.os, "geteuid", return_value=0), \
                mock.patch.object(
                    prepare.sys, "argv",
                    [str(PREPARE_SOURCE), "--duration-seconds", "300",
                     "--max-cycles", "0"]), \
                self.assertRaisesRegex(
                    RuntimeError, "REPAIR_CAMPAIGN_CYCLE_LIMIT_INVALID"):
            prepare.main()

    def test_commit_rejects_zero_cycle_budget_before_mutation(
            self) -> None:
        admission = mock.Mock()
        with mock.patch.object(
                prepare, "campaign_lifecycle_locks",
                return_value=contextlib.nullcontext()), \
                mock.patch.object(
                    prepare, "require_fresh_campaign_admission", admission), \
                self.assertRaisesRegex(
                    RuntimeError, "REPAIR_CAMPAIGN_CYCLE_LIMIT_INVALID"):
            prepare.commit_campaign(
                self.policy(), "sha256:" + "1" * 64,
                1300, 300, 0, None, None)
        admission.assert_not_called()

    def test_every_legacy_active_v4_wal_phase_rolls_back_after_reboot(
            self) -> None:
        for phase in prepare.PREPARE_TRANSACTION_PHASES:
            with self.subTest(phase=phase), \
                    tempfile.TemporaryDirectory() as directory:
                record = self.transaction_record()
                record["phase"] = phase
                policy_path = Path(directory) / "policy.json"
                policy_path.write_bytes(prepare._decode_payload_record(
                    record["target_policy"]))
                rollback = mock.Mock()
                remove = mock.Mock()
                deployment = mock.Mock()
                with mock.patch.multiple(
                        prepare,
                        POLICY_PATH=policy_path,
                        _load_prepare_transaction=mock.Mock(
                            return_value=record),
                        _load_local_paper_deployment_evidence=deployment,
                        _rollback_prepare_transaction=rollback,
                        _remove_prepare_transaction=remove):
                    result = prepare.reconcile_prepare_transaction_locked()
                self.assertEqual(result, "ROLLED_BACK")
                rollback.assert_called_once_with(
                    record, require_safe_boundary=True)
                remove.assert_not_called()
                deployment.assert_not_called()

    def test_reconcile_fences_policy_before_safe_boundary_rollback(
            self) -> None:
        record = self.transaction_record()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy_path = root / "policy.json"
            env_path = root / "agent.env"
            policy_path.write_bytes(prepare._decode_payload_record(
                record["target_policy"]))
            env_path.write_bytes(prepare._decode_payload_record(
                record["target_env"]))
            events: list[str] = []

            def atomic(path: Path, payload: bytes) -> None:
                path.write_bytes(payload)
                events.append("fence")

            def deny_all() -> None:
                policy = json.loads(policy_path.read_bytes())
                self.assertIs(policy["enabled"], False)
                self.assertIs(policy["mutations_authorized"], False)
                events.append("deny-all")

            def rollback(
                    previous_policy: bytes, previous_env: bytes,
                    _unit_files: object, _unit_states: object) -> None:
                self.assertIn("deny-all", events)
                policy_path.write_bytes(previous_policy)
                env_path.write_bytes(previous_env)
                events.append("restore")

            remove = mock.Mock(side_effect=lambda: events.append("remove"))
            with mock.patch.multiple(
                    prepare,
                    POLICY_PATH=policy_path,
                    AGENT_ENV_PATH=env_path,
                    atomic_write=mock.Mock(side_effect=atomic),
                    _require_deny_all=mock.Mock(side_effect=deny_all),
                    _require_no_session_or_permit_residue=mock.Mock(
                        side_effect=lambda: events.append("no-session")),
                    _require_runtime_inactive=mock.Mock(
                        side_effect=lambda: events.append("runtime-inactive")),
                    rollback_prepare=mock.Mock(side_effect=rollback),
                    _remove_prepare_transaction=remove):
                prepare._rollback_prepare_transaction(
                    record, require_safe_boundary=True)
            self.assertEqual(events, [
                "fence", "deny-all", "no-session", "runtime-inactive",
                "restore", "remove",
            ])
            self.assertEqual(
                policy_path.read_bytes(),
                prepare._decode_payload_record(record["previous_policy"]))
            self.assertEqual(
                env_path.read_bytes(),
                prepare._decode_payload_record(record["previous_env"]))

    def test_reconcile_boundary_failure_keeps_wal_and_policy_disabled(
            self) -> None:
        record = self.transaction_record()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy_path = root / "policy.json"
            env_path = root / "agent.env"
            policy_path.write_bytes(prepare._decode_payload_record(
                record["target_policy"]))
            env_path.write_bytes(prepare._decode_payload_record(
                record["target_env"]))
            rollback = mock.Mock()
            remove = mock.Mock()
            with mock.patch.multiple(
                    prepare,
                    POLICY_PATH=policy_path,
                    AGENT_ENV_PATH=env_path,
                    atomic_write=mock.Mock(
                        side_effect=lambda path, payload:
                            path.write_bytes(payload)),
                    _require_deny_all=mock.Mock(
                        side_effect=RuntimeError("not deny-all")),
                    rollback_prepare=rollback,
                    _remove_prepare_transaction=remove), \
                    self.assertRaisesRegex(
                        RuntimeError,
                        "REPAIR_PREPARE_RECOVERY_BOUNDARY_UNSAFE"):
                prepare._rollback_prepare_transaction(
                    record, require_safe_boundary=True)
            policy = json.loads(policy_path.read_bytes())
            self.assertIs(policy["enabled"], False)
            self.assertIs(policy["mutations_authorized"], False)
            rollback.assert_not_called()
            remove.assert_not_called()

    def test_reconcile_never_finalizes_unverified_enabled_target(self) -> None:
        record = self.transaction_record()
        with tempfile.TemporaryDirectory() as directory:
            policy_path = Path(directory) / "policy.json"
            policy_path.write_bytes(prepare._decode_payload_record(
                record["target_policy"]))
            rollback = mock.Mock()
            remove = mock.Mock()
            with mock.patch.multiple(
                    prepare,
                    POLICY_PATH=policy_path,
                    _load_prepare_transaction=mock.Mock(return_value=record),
                    _verify_prepare_target=mock.Mock(
                        side_effect=RuntimeError("timer drift")),
                    _rollback_prepare_transaction=rollback,
                    _remove_prepare_transaction=remove):
                result = prepare.reconcile_prepare_transaction_locked()
            self.assertEqual(result, "ROLLED_BACK")
            rollback.assert_called_once_with(
                record, require_safe_boundary=True)
            remove.assert_not_called()

    def test_corrupt_wal_fences_enabled_policy_and_blocks_prepare(self) -> None:
        record = self.transaction_record()
        with tempfile.TemporaryDirectory() as directory:
            policy_path = Path(directory) / "policy.json"
            policy_path.write_bytes(prepare._decode_payload_record(
                record["target_policy"]))
            with mock.patch.multiple(
                    prepare,
                    POLICY_PATH=policy_path,
                    _load_prepare_transaction=mock.Mock(
                        side_effect=RuntimeError("torn wal")),
                    atomic_write=mock.Mock(
                        side_effect=lambda path, payload:
                            path.write_bytes(payload))), \
                    self.assertRaisesRegex(
                        RuntimeError,
                        "REPAIR_PREPARE_TRANSACTION_UNRECOVERABLE"):
                prepare.reconcile_prepare_transaction_locked()
            policy = json.loads(policy_path.read_bytes())
            self.assertIs(policy["enabled"], False)
            self.assertIs(policy["mutations_authorized"], False)

    def test_root_only_wal_round_trips_phase_across_process_restart(
            self) -> None:
        record = self.transaction_record()
        with tempfile.TemporaryDirectory() as directory:
            wal_path = Path(directory) / "prepare-transaction.json"

            def install(path: Path, payload: bytes, mode: int = 0o644) -> None:
                path.write_bytes(payload)
                path.chmod(mode)

            def write(path: Path, payload: bytes) -> None:
                path.write_bytes(payload)
                path.chmod(0o600)

            def root_fstat(descriptor: int) -> SimpleNamespace:
                metadata = REAL_FSTAT(descriptor)
                return SimpleNamespace(
                    st_mode=metadata.st_mode,
                    st_nlink=metadata.st_nlink,
                    st_uid=0,
                    st_gid=0,
                    st_size=metadata.st_size,
                )

            with mock.patch.multiple(
                    prepare,
                    PREPARE_TRANSACTION_PATH=wal_path,
                    atomic_install=mock.Mock(side_effect=install),
                    atomic_write=mock.Mock(side_effect=write)), \
                    mock.patch.object(
                        prepare.os, "fstat", side_effect=root_fstat):
                prepare._persist_prepare_transaction(record, create=True)
                loaded = prepare._load_prepare_transaction()
                self.assertEqual(loaded, record)
                prepare._advance_prepare_transaction(
                    record, "BACKGROUND_TIMERS_STOPPED")
                reloaded = prepare._load_prepare_transaction()
            self.assertEqual(
                reloaded["phase"], "BACKGROUND_TIMERS_STOPPED")
            self.assertEqual(stat.S_IMODE(wal_path.stat().st_mode), 0o600)

    def test_stable_config_read_rejects_path_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            payload = b'{"paper_only":true}\n'
            path.write_bytes(payload)
            path.chmod(0o600)

            def root_metadata(metadata: object, inode_delta: int = 0):
                return SimpleNamespace(
                    st_mode=metadata.st_mode,
                    st_nlink=metadata.st_nlink,
                    st_uid=0,
                    st_gid=0,
                    st_size=metadata.st_size,
                    st_dev=metadata.st_dev,
                    st_ino=metadata.st_ino + inode_delta,
                    st_mtime_ns=metadata.st_mtime_ns,
                    st_ctime_ns=metadata.st_ctime_ns,
                )

            with mock.patch.object(
                    prepare.os, "fstat",
                    side_effect=lambda descriptor:
                        root_metadata(REAL_FSTAT(descriptor))), \
                    mock.patch.object(
                        prepare.os, "lstat",
                        side_effect=lambda target:
                            root_metadata(REAL_LSTAT(target))):
                self.assertEqual(
                    ORIGINAL_PREPARE_STABLE_READ(path, "unsafe"), payload)
            with mock.patch.object(
                    prepare.os, "fstat",
                    side_effect=lambda descriptor:
                        root_metadata(REAL_FSTAT(descriptor))), \
                    mock.patch.object(
                        prepare.os, "lstat",
                        side_effect=lambda target:
                            root_metadata(REAL_LSTAT(target), 1)), \
                    self.assertRaisesRegex(RuntimeError, "unsafe"):
                ORIGINAL_PREPARE_STABLE_READ(path, "unsafe")

    def test_generated_end_flat_service_has_only_required_writable_roots(
            self) -> None:
        installed: dict[Path, bytes] = {}

        def systemctl(*arguments: str, **_kwargs: object) -> str:
            if arguments[0] == "show":
                if (arguments[1] ==
                        prepare.PERSISTENT_STOP_UNIT + ".service"):
                    return (
                        "LoadState=loaded\nUnitFileState=static\n"
                        "ActiveState=inactive\n"
                        "FragmentPath=" + str(
                            prepare.SYSTEMD_ROOT / arguments[1]) + "\n"
                        "DropInPaths=\nJob=\n")
                realtime = (
                    "Thu 2026-08-13 08:00:00 CST"
                    if arguments[1] ==
                    prepare.PERSISTENT_STOP_UNIT + ".timer" else "")
                monotonic = (
                    "42s" if arguments[1] ==
                    prepare.RETRY_TIMER_UNIT + ".timer" else "")
                return (
                    "LoadState=loaded\nActiveState=active\nSubState=waiting\n"
                    "UnitFileState=enabled\nJob=\n"
                    "FragmentPath=" + str(
                        prepare.SYSTEMD_ROOT / arguments[1]) + "\n"
                    "DropInPaths=\n"
                    f"NextElapseUSecRealtime={realtime}\n"
                    f"NextElapseUSecMonotonic={monotonic}\n")
            return ""

        control = mock.Mock(side_effect=systemctl)
        with mock.patch.object(
                prepare, "atomic_install",
                side_effect=lambda path, payload, mode=0o644:
                    installed.__setitem__(path, payload)), \
                mock.patch.object(
                    prepare, "snapshot_unit_file",
                    side_effect=lambda path: prepare.UnitFileSnapshot(
                        installed[path], 0o644)), \
                mock.patch.object(
                    prepare.time, "time_ns",
                    return_value=1_786_000_000_000_000_000), \
                mock.patch.object(prepare, "_systemctl", control), \
                mock.patch.object(
                    prepare, "_systemd_timer_realtime_usec",
                    return_value=1_786_579_200 * 1_000_000):
            prepare.arm_stop_timer(1_786_579_200)

        service_path = (
            prepare.SYSTEMD_ROOT /
            (prepare.PERSISTENT_STOP_UNIT + ".service"))
        service = installed[service_path].decode("ascii")
        line = next(item for item in service.splitlines()
                    if item.startswith("ReadWritePaths="))
        roots = line.removeprefix("ReadWritePaths=").split()
        self.assertEqual(roots, [
            "/var/lib/hepta-local-ai-paper-agent",
            "/etc/heptatrader",
            "/etc/systemd/system/"
            "hepta-broker-egress-policy.service.d",
            "-/run/hepta-agent-alpha",
        ])
        self.assertNotIn("/etc/systemd/system", roots)
        self.assertIn(
            "ExecCondition=/usr/libexec/hepta-local-paper-repair "
            "end-flat-condition", service)
        retry_path = (
            prepare.SYSTEMD_ROOT / (prepare.RETRY_TIMER_UNIT + ".timer"))
        retry = installed[retry_path].decode("ascii")
        self.assertIn("OnActiveSec=60s", retry)
        self.assertNotIn("OnBootSec=", retry)
        self.assertIn("OnUnitInactiveSec=60s", retry)
        self.assertIn("[Install]\nWantedBy=timers.target", retry)
        timers = (
            prepare.PERSISTENT_STOP_UNIT + ".timer",
            prepare.RETRY_TIMER_UNIT + ".timer",
        )
        self.assertIn(mock.call("enable", *timers), control.call_args_list)
        self.assertIn(mock.call("start", *timers), control.call_args_list)

    def test_systemd_timer_realtime_usec_reads_raw_dbus_integer(self) -> None:
        completed = SimpleNamespace(
            returncode=0, stdout="t 1786579200000000\n", stderr="")
        with mock.patch.object(
                prepare.subprocess, "run", return_value=completed) as runner:
            observed = prepare._systemd_timer_realtime_usec(
                prepare.PERSISTENT_STOP_UNIT + ".timer")
        self.assertEqual(observed, 1_786_579_200 * 1_000_000)
        arguments = runner.call_args.args[0]
        self.assertEqual(arguments[:3], [
            "/usr/bin/busctl", "--system", "get-property"])
        self.assertEqual(
            arguments[4],
            "/org/freedesktop/systemd1/unit/"
            "hepta_2dlocal_2dai_2dpaper_2d24h_2dstop_2etimer")
        self.assertEqual(arguments[-2:], [
            "org.freedesktop.systemd1.Timer", "NextElapseUSecRealtime"])
        self.assertEqual(
            prepare._systemd_unit_dbus_path("1foo_bar.timer"),
            "/org/freedesktop/systemd1/unit/_31foo_5fbar_2etimer")

    def test_systemd_timer_realtime_usec_rejects_malformed_dbus_value(
            self) -> None:
        for output in (
                "", "u 1\n", "t nope\n", "t 0\n",
                "t 18446744073709551615\n"):
            with self.subTest(output=output), \
                    mock.patch.object(
                        prepare.subprocess, "run",
                        return_value=SimpleNamespace(
                            returncode=0, stdout=output, stderr="")), \
                    self.assertRaisesRegex(
                        RuntimeError,
                        "REPAIR_STOP_TIMER_VERIFICATION_FAILED"):
                prepare._systemd_timer_realtime_usec(
                    prepare.PERSISTENT_STOP_UNIT + ".timer")
        with mock.patch.object(
                prepare.subprocess, "run",
                return_value=SimpleNamespace(
                    returncode=0, stdout="t 1\n", stderr="warning\n")), \
                self.assertRaisesRegex(
                    RuntimeError,
                    "REPAIR_STOP_TIMER_VERIFICATION_FAILED"):
            prepare._systemd_timer_realtime_usec(
                prepare.PERSISTENT_STOP_UNIT + ".timer")

    def test_waiting_timer_accepts_monotonic_recurring_timer(self) -> None:
        state = {
            "LoadState": "loaded", "ActiveState": "active",
            "SubState": "waiting", "NextElapseUSecRealtime": "",
            "NextElapseUSecMonotonic": "1min 2s", "UnitFileState": "enabled",
            "Job": "",
        }
        with mock.patch.object(
                repair, "_unit_properties", return_value=state) as properties:
            observed = repair._verify_waiting_timer(
                repair.SAFE_RECOVERY_TIMER)
        self.assertEqual(observed, state)
        properties.assert_called_once_with(
            repair.SAFE_RECOVERY_TIMER, "LoadState", "ActiveState", "SubState",
            "NextElapseUSecRealtime", "NextElapseUSecMonotonic",
            "UnitFileState", "Job")

    def test_waiting_timer_requires_realtime_for_deadline_timer(self) -> None:
        state = {
            "LoadState": "loaded", "ActiveState": "active",
            "SubState": "waiting", "NextElapseUSecRealtime": "",
            "NextElapseUSecMonotonic": "1min 2s", "UnitFileState": "enabled",
            "Job": "",
        }
        with mock.patch.object(repair, "_unit_properties", return_value=state), \
                self.assertRaisesRegex(
                    RuntimeError,
                    "CAMPAIGN_TIMER_NOT_WAITING:" +
                    repair.PERSISTENT_STOP_TIMER):
            repair._verify_waiting_timer(repair.PERSISTENT_STOP_TIMER)

    def test_waiting_timer_rejects_unknown_unit_and_empty_monotonic_deadline(
            self) -> None:
        state = {
            "LoadState": "loaded", "ActiveState": "active",
            "SubState": "waiting", "NextElapseUSecRealtime": "",
            "NextElapseUSecMonotonic": "", "UnitFileState": "enabled",
            "Job": "",
        }
        with mock.patch.object(repair, "_unit_properties", return_value=state), \
                self.assertRaisesRegex(
                    RuntimeError,
                    "CAMPAIGN_TIMER_NOT_WAITING:" +
                    repair.SAFE_RECOVERY_TIMER):
            repair._verify_waiting_timer(repair.SAFE_RECOVERY_TIMER)
        with self.assertRaisesRegex(
                RuntimeError, "CAMPAIGN_TIMER_NOT_WAITING:unknown.timer"):
            with mock.patch.object(
                    repair, "_unit_properties", return_value=state):
                repair._verify_waiting_timer("unknown.timer")

    def test_retry_timer_seed_is_activation_relative_not_boot_relative(
            self) -> None:
        retry_path = (
            prepare.SYSTEMD_ROOT / (prepare.RETRY_TIMER_UNIT + ".timer"))
        retry = prepare.stop_unit_payloads(1_786_579_200)[
            retry_path].decode("ascii")
        self.assertIn("OnActiveSec=60s", retry)
        self.assertIn("OnUnitInactiveSec=60s", retry)
        self.assertNotIn("OnBootSec=", retry)

    def test_v5_policy_is_canonical_single_quantity_one_lmt_day_canary(
            self) -> None:
        policy = self.v5_policy()
        raw = prepare.canonical(policy)
        self.assertEqual(
            prepare._validate_v5_prepare_policy(
                policy, raw=raw, require_disabled=True),
            policy)
        cases = {
            "noncanonical": (policy, json.dumps(policy).encode("ascii")),
            "mkt": ({**policy, "order_type": "MKT"}, None),
            "too_many_cycles": ({**policy, "max_cycles": 2}, None),
            "too_much_quantity": ({**policy, "max_quantity": 2}, None),
            "too_long": ({
                **policy,
                "expires_at_ms": int(policy["expires_at_ms"]) + 1,
            }, None),
            "wrong_mode": ({**policy, "admission_mode": "local-only"}, None),
        }
        for label, (document, document_raw) in cases.items():
            with self.subTest(label=label), self.assertRaises(RuntimeError):
                prepare._validate_v5_prepare_policy(
                    document, raw=document_raw, require_disabled=True)

    def test_v5_local_seed_is_canonical_mkt_day_and_unbound(self) -> None:
        seed = self.v5_local_policy()
        self.assertEqual(
            prepare._validate_v5_prepare_policy(
                seed, raw=prepare.canonical(seed), require_disabled=True),
            seed)
        target = dict(seed)
        target.update({
            "campaign_id": "local-ai-paper-mkt-model-exit-20260815T000000Z-"
                           "123456789abc",
            "enabled": True,
            "mutations_authorized": True,
            "strategy_sha256": "sha256:" + "1" * 64,
            "valid_after_ms": 1_000_000_000_000,
            "expires_at_ms": 1_000_086_400_000,
            "source_baseline_sha256": "sha256:" + "2" * 64,
            "deployment_evidence_file_sha256": "sha256:" + "3" * 64,
            "deployment_evidence_body_sha256": "sha256:" + "4" * 64,
            "deployment_install_transaction_id": "install-round106-local",
        })
        self.assertEqual(prepare._validate_v5_prepare_policy(target), target)
        for label, changed in (
                ("lmt", {"order_type": "LMT"}),
                ("live", {"live_authorized": True}),
                ("too_many_cycles", {"max_cycles": 721}),
                ("active_unbound", {
                    "source_baseline_sha256": prepare.ZERO_DIGEST,
                })):
            document = dict(target)
            document.update(changed)
            with self.subTest(label=label), self.assertRaises(RuntimeError):
                prepare._validate_v5_prepare_policy(document)

    def test_v5_local_binding_omits_external_p1_fields(self) -> None:
        target = self.v5_local_policy()
        target.update({
            "enabled": True,
            "mutations_authorized": True,
            "strategy_sha256": "sha256:" + "1" * 64,
            "valid_after_ms": 1_000_000_000_000,
            "expires_at_ms": 1_000_086_400_000,
            "source_baseline_sha256": "sha256:" + "2" * 64,
            "deployment_evidence_file_sha256": "sha256:" + "3" * 64,
            "deployment_evidence_body_sha256": "sha256:" + "4" * 64,
            "deployment_install_transaction_id": "install-round106-local",
        })
        binding = prepare._v5_policy_binding_record(target)
        self.assertEqual(
            set(binding), prepare.PAPER_POLICY_V5_LOCAL_WAL_BINDING_FIELDS)
        self.assertNotIn("p1_audit_receipt_path", binding)
        self.assertNotIn("watch_handoff_receipt_path", binding)

    def test_deployment_migrates_disabled_seed_to_bound_v5_local(self) -> None:
        strategy_raw = prepare.canonical(self.strategy(order_type="MKT"))
        deployment = self.deployment_snapshot()
        binding = prepare._deployment_binding_record(deployment)
        with tempfile.TemporaryDirectory() as directory:
            strategy_path = Path(directory) / "strategy.json"
            strategy_path.write_bytes(strategy_raw)
            with mock.patch.object(prepare, "STRATEGY_PATH", strategy_path):
                migrated = prepare._v5_local_seed_from_deployment(deployment)
        self.assertEqual(migrated["schema"], prepare.PAPER_POLICY_V5_SCHEMA)
        self.assertEqual(migrated["version"], 5)
        self.assertEqual(migrated["admission_mode"], "local-only")
        self.assertEqual(migrated["order_type"], "MKT")
        self.assertEqual(migrated["max_cycles"], 720)
        self.assertEqual(migrated["valid_after_ms"], 0)
        self.assertEqual(migrated["expires_at_ms"], 0)
        self.assertEqual(
            migrated["strategy_sha256"],
            "sha256:" + hashlib.sha256(strategy_raw).hexdigest())
        self.assertEqual(
            migrated["source_baseline_sha256"],
            binding["source_baseline_sha256"])
        self.assertEqual(
            migrated["deployment_evidence_file_sha256"],
            binding["evidence_file_sha256"])

    def test_v5_wal_persists_all_p1_and_deployment_pins(self) -> None:
        record = self.v5_transaction_record()
        self.assertEqual(
            record["schema"], prepare.PREPARE_TRANSACTION_SCHEMA_V2)
        target = json.loads(prepare._decode_payload_record(
            record["target_policy"]))
        binding = record["v5_policy_binding"]
        self.assertEqual(set(binding), prepare.PAPER_POLICY_V5_WAL_BINDING_FIELDS)
        self.assertEqual(
            binding, prepare._v5_policy_binding_record(target))
        self.assertEqual(
            record["deployment_binding"]["evidence_file_sha256"],
            target["deployment_evidence_file_sha256"])
        self.assertEqual(prepare._validate_prepare_transaction(record), record)

    def test_legacy_v1_wal_cannot_smuggle_a_v5_policy_binding(self) -> None:
        record = self.transaction_record()
        self.assertEqual(
            record["schema"], prepare.PREPARE_TRANSACTION_SCHEMA_V1)
        record["v5_policy_binding"] = prepare._v5_policy_binding_record(
            self.v5_policy())
        with self.assertRaisesRegex(
                RuntimeError, "REPAIR_PREPARE_TRANSACTION_INVALID"):
            prepare._validate_prepare_transaction(record)

    def test_v5_wal_rejects_each_direct_p1_binding_single_field_drift(
            self) -> None:
        direct_fields = (
            "p1_audit_receipt_path",
            "p1_audit_receipt_file_sha256",
            "p1_audit_receipt_body_sha256",
            "watch_handoff_receipt_path",
            "watch_handoff_receipt_file_sha256",
            "watch_handoff_receipt_body_sha256",
        )
        for field in direct_fields:
            with self.subTest(field=field):
                record = self.v5_transaction_record()
                target = json.loads(prepare._decode_payload_record(
                    record["target_policy"]))
                if field.endswith("_path"):
                    target[field] = str(target[field]).replace(
                        ".json", "-drifted.json")
                else:
                    target[field] = "sha256:" + "5" * 64
                record["target_policy"] = prepare._payload_record(
                    prepare.canonical(target))
                with self.assertRaisesRegex(
                        RuntimeError,
                        "REPAIR_PREPARE_TRANSACTION_INVALID"):
                    prepare._validate_prepare_transaction(record)

    def test_v5_recovery_always_fences_and_rolls_back_never_commits(
            self) -> None:
        record = self.v5_transaction_record()
        with tempfile.TemporaryDirectory() as directory:
            policy_path = Path(directory) / "policy.json"
            policy_path.write_bytes(prepare._decode_payload_record(
                record["target_policy"]))
            rollback = mock.Mock()
            verify = mock.Mock()
            remove = mock.Mock()
            with mock.patch.multiple(
                    prepare,
                    POLICY_PATH=policy_path,
                    _load_prepare_transaction=mock.Mock(return_value=record),
                    _rollback_prepare_transaction=rollback,
                    _verify_prepare_target=verify,
                    _remove_prepare_transaction=remove):
                result = prepare.reconcile_prepare_transaction_locked()
        self.assertEqual(result, "ROLLED_BACK")
        rollback.assert_called_once_with(record, require_safe_boundary=True)
        verify.assert_not_called()
        remove.assert_not_called()

    def test_v5_commit_preserves_every_pin_and_only_enables_authority(
            self) -> None:
        strategy_raw = prepare.canonical(self.strategy())
        strategy_digest = "sha256:" + hashlib.sha256(
            strategy_raw).hexdigest()
        source_policy = self.v5_policy(strategy_sha256=strategy_digest)
        deadline_seconds = int(source_policy["expires_at_ms"]) // 1000
        deployment = self.deployment_snapshot()
        captured: list[dict[str, object]] = []
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy_path = root / "policy.json"
            strategy_path = root / "strategy.json"
            env_path = root / "agent.env"
            state_root = root / "state"
            state_root.mkdir()
            policy_path.write_bytes(prepare.canonical(source_policy))
            strategy_path.write_bytes(strategy_raw)
            env_path.write_bytes(self.agent_env())
            previous_unit_files = {
                path: prepare.UnitFileSnapshot(None, None)
                for path in prepare.generated_stop_unit_paths()
            }
            previous_unit_states = {
                unit: prepare.SystemdUnitSnapshot(
                    "loaded", "disabled", "inactive")
                for unit in (
                    prepare.stop_runtime_units() +
                    prepare.background_timer_units())
            }

            def write(path: Path, payload: bytes) -> None:
                path.write_bytes(payload)

            with mock.patch.multiple(
                    prepare,
                    POLICY_PATH=policy_path,
                    STRATEGY_PATH=strategy_path,
                    AGENT_ENV_PATH=env_path,
                    STATE_ROOT=state_root,
                    campaign_lifecycle_locks=mock.Mock(
                        return_value=contextlib.nullcontext()),
                    reconcile_prepare_transaction_locked=mock.Mock(
                        return_value=None),
                    _load_local_paper_deployment_evidence=mock.Mock(
                        return_value=deployment),
                    require_fresh_campaign_admission=mock.Mock(),
                    snapshot_stop_unit_files=mock.Mock(
                        return_value=previous_unit_files),
                    snapshot_systemd_unit_states=mock.Mock(
                        return_value=previous_unit_states),
                    _persist_prepare_transaction=mock.Mock(
                        side_effect=lambda record, **_kwargs:
                            captured.append(record)),
                    _advance_prepare_transaction=mock.Mock(),
                    keep_background_timers_stopped=mock.Mock(),
                    disarm_old_stop_units=mock.Mock(),
                    atomic_write=mock.Mock(side_effect=write),
                    arm_stop_timer=mock.Mock(),
                    _verify_prepare_target=mock.Mock(),
                    _require_deployment_snapshot_unchanged=mock.Mock(),
                    _require_external_p1_boundary=mock.Mock(),
                    _remove_prepare_transaction=mock.Mock()), \
                    mock.patch.object(
                        prepare.time, "time_ns",
                        return_value=1_000_000_000_000_000_000):
                campaign_id, target = prepare.commit_campaign(
                    source_policy, strategy_digest, deadline_seconds,
                    300, 1, None, None)
        self.assertEqual(campaign_id, source_policy["campaign_id"])
        expected = dict(source_policy)
        expected["enabled"] = True
        expected["mutations_authorized"] = True
        self.assertEqual(target, expected)
        self.assertTrue(captured)
        self.assertEqual(
            captured[0]["v5_policy_binding"],
            prepare._v5_policy_binding_record(expected))
        self.assertEqual(
            json.loads(prepare._decode_payload_record(
                captured[0]["target_policy"])), expected)

    def test_v5_local_commit_derives_fresh_24h_mkt_campaign(self) -> None:
        strategy_raw = prepare.canonical(self.strategy(order_type="MKT"))
        strategy_digest = "sha256:" + hashlib.sha256(
            strategy_raw).hexdigest()
        deployment = self.deployment_snapshot()
        binding = prepare._deployment_binding_record(deployment)
        source_policy = self.v5_local_policy()
        source_policy.update({
            "strategy_id": "legacy-local-paper-strategy-seed",
            "strategy_version": "2",
            "strategy_sha256": strategy_digest,
            "source_baseline_sha256": binding["source_baseline_sha256"],
            "deployment_evidence_file_sha256":
                binding["evidence_file_sha256"],
            "deployment_evidence_body_sha256":
                binding["evidence_body_sha256"],
            "deployment_install_transaction_id":
                binding["install_transaction_id"],
        })
        captured: list[dict[str, object]] = []
        deadline_seconds = 1_000_086_400
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy_path = root / "policy.json"
            strategy_path = root / "strategy.json"
            env_path = root / "agent.env"
            state_root = root / "state"
            state_root.mkdir()
            policy_path.write_bytes(prepare.canonical(source_policy))
            strategy_path.write_bytes(strategy_raw)
            env_path.write_bytes(self.agent_env())
            unit_files = {
                path: prepare.UnitFileSnapshot(None, None)
                for path in prepare.generated_stop_unit_paths()
            }
            unit_states = {
                unit: prepare.SystemdUnitSnapshot(
                    "loaded", "disabled", "inactive")
                for unit in (
                    prepare.stop_runtime_units() +
                    prepare.background_timer_units())
            }

            def write(path: Path, payload: bytes) -> None:
                path.write_bytes(payload)

            with mock.patch.multiple(
                    prepare,
                    POLICY_PATH=policy_path,
                    STRATEGY_PATH=strategy_path,
                    AGENT_ENV_PATH=env_path,
                    STATE_ROOT=state_root,
                    campaign_lifecycle_locks=mock.Mock(
                        return_value=contextlib.nullcontext()),
                    reconcile_prepare_transaction_locked=mock.Mock(
                        return_value=None),
                    _load_local_paper_deployment_evidence=mock.Mock(
                        return_value=deployment),
                    require_fresh_campaign_admission=mock.Mock(),
                    snapshot_stop_unit_files=mock.Mock(
                        return_value=unit_files),
                    snapshot_systemd_unit_states=mock.Mock(
                        return_value=unit_states),
                    _persist_prepare_transaction=mock.Mock(
                        side_effect=lambda record, **_kwargs:
                            captured.append(record)),
                    _advance_prepare_transaction=mock.Mock(),
                    keep_background_timers_stopped=mock.Mock(),
                    disarm_old_stop_units=mock.Mock(),
                    atomic_write=mock.Mock(side_effect=write),
                    arm_stop_timer=mock.Mock(),
                    _verify_prepare_target=mock.Mock(),
                    _require_deployment_snapshot_unchanged=mock.Mock(),
                    _remove_prepare_transaction=mock.Mock()), \
                    mock.patch.object(
                        prepare.time, "time_ns",
                        return_value=1_000_000_000_000_000_000):
                campaign_id, target = prepare.commit_campaign(
                    source_policy, strategy_digest, deadline_seconds,
                    86_400, 720, None, None)
        self.assertRegex(
            campaign_id,
            r"^local-ai-paper-mkt-model-exit-\d{8}T\d{6}Z-[0-9a-f]{12}$")
        self.assertEqual(target["campaign_id"], campaign_id)
        self.assertIs(target["enabled"], True)
        self.assertIs(target["mutations_authorized"], True)
        self.assertEqual(target["order_type"], "MKT")
        self.assertEqual(target["tif"], "DAY")
        self.assertEqual(target["strategy_id"], prepare.STRATEGY_ID)
        self.assertEqual(target["strategy_version"], prepare.STRATEGY_VERSION)
        self.assertEqual(target["max_cycles"], 720)
        self.assertEqual(target["valid_after_ms"], 1_000_000_000_000)
        self.assertEqual(target["expires_at_ms"], 1_000_086_400_000)
        self.assertEqual(
            set(captured[0]["v5_policy_binding"]),
            prepare.PAPER_POLICY_V5_LOCAL_WAL_BINDING_FIELDS)
        target_env = prepare._decode_payload_record(captured[0]["target_env"])
        self.assertIn(
            b"HEPTA_LOCAL_AI_STRATEGY_ID=" +
            prepare.STRATEGY_ID.encode("ascii") + b"\n", target_env)
        self.assertIn(
            b"HEPTA_LOCAL_AI_STRATEGY_VERSION=" +
            prepare.STRATEGY_VERSION.encode("ascii") + b"\n", target_env)
        self.assertNotIn(b"legacy-local-paper-strategy-seed", target_env)

    def test_v5_main_accepts_exact_single_lmt_canary_policy_and_cli(self) -> None:
        strategy_raw = prepare.canonical(self.strategy())
        strategy_digest = "sha256:" + hashlib.sha256(
            strategy_raw).hexdigest()
        policy = self.v5_policy(strategy_sha256=strategy_digest)
        target = dict(policy)
        target["enabled"] = True
        target["mutations_authorized"] = True
        deadline_seconds = int(policy["expires_at_ms"]) // 1000
        commit = mock.Mock(return_value=(policy["campaign_id"], target))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy_path = root / "policy.json"
            strategy_path = root / "strategy.json"
            policy_path.write_bytes(prepare.canonical(policy))
            strategy_path.write_bytes(strategy_raw)
            with mock.patch.multiple(
                    prepare,
                    POLICY_PATH=policy_path,
                    STRATEGY_PATH=strategy_path,
                    campaign_lifecycle_locks=mock.Mock(
                        return_value=contextlib.nullcontext()),
                    reconcile_prepare_transaction_locked=mock.Mock(
                        return_value=None),
                    commit_campaign=commit), \
                    mock.patch.object(prepare.os, "geteuid", return_value=0), \
                    mock.patch.object(
                        prepare.time, "time_ns",
                        return_value=1_000_000_005_000_000_000), \
                    mock.patch.object(prepare.sys, "argv", [
                        str(PREPARE_SOURCE), "--duration-seconds", "300",
                        "--max-cycles", "1",
                    ]):
                self.assertEqual(prepare.main(), 0)
        commit.assert_called_once_with(
            policy, strategy_digest, deadline_seconds, 300, 1,
            None, None)

    def test_v5_local_main_derives_fresh_24h_mkt_window(self) -> None:
        strategy_raw = prepare.canonical(self.strategy(order_type="MKT"))
        strategy_digest = "sha256:" + hashlib.sha256(
            strategy_raw).hexdigest()
        policy = self.v5_local_policy()
        target = dict(policy)
        target.update({
            "campaign_id": "local-ai-paper-mkt-model-exit-"
                           "20260815T000000Z-123456789abc",
            "enabled": True,
            "mutations_authorized": True,
            "strategy_sha256": strategy_digest,
            "valid_after_ms": 1_000_000_000_000,
            "expires_at_ms": 1_000_086_400_000,
            "source_baseline_sha256": "sha256:" + "a" * 64,
            "deployment_evidence_file_sha256": "sha256:" + "b" * 64,
            "deployment_evidence_body_sha256": "sha256:" + "c" * 64,
            "deployment_install_transaction_id": "install-round106-local",
        })
        commit = mock.Mock(return_value=(target["campaign_id"], target))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy_path = root / "policy.json"
            strategy_path = root / "strategy.json"
            policy_path.write_bytes(prepare.canonical(policy))
            strategy_path.write_bytes(strategy_raw)
            with mock.patch.multiple(
                    prepare,
                    POLICY_PATH=policy_path,
                    STRATEGY_PATH=strategy_path,
                    campaign_lifecycle_locks=mock.Mock(
                        return_value=contextlib.nullcontext()),
                    reconcile_prepare_transaction_locked=mock.Mock(
                        return_value=None),
                    commit_campaign=commit), \
                    mock.patch.object(prepare.os, "geteuid", return_value=0), \
                    mock.patch.object(
                        prepare.time, "time_ns",
                        return_value=1_000_000_000_000_000_000), \
                    mock.patch.object(prepare.sys, "argv", [
                        str(PREPARE_SOURCE), "--duration-seconds", "86400",
                        "--max-cycles", "720",
                    ]):
                self.assertEqual(prepare.main(), 0)
        commit.assert_called_once_with(
            policy, strategy_digest, 1_000_086_400, 86_400, 720,
            None, None)

    def test_v5_cli_window_and_cycle_mismatch_precedes_deployment(
            self) -> None:
        policy = self.v5_policy()
        deadline_seconds = int(policy["expires_at_ms"]) // 1000
        deployment = mock.Mock()
        admission = mock.Mock()
        with tempfile.TemporaryDirectory() as directory:
            policy_path = Path(directory) / "policy.json"
            policy_path.write_bytes(prepare.canonical(policy))
            for label, deadline, duration, cycles in (
                    ("deadline", deadline_seconds + 1, 300, 1),
                    ("duration", deadline_seconds, 301, 1),
                    ("cycles", deadline_seconds, 300, 2)):
                deployment.reset_mock()
                admission.reset_mock()
                with self.subTest(label=label), mock.patch.multiple(
                        prepare,
                        POLICY_PATH=policy_path,
                        campaign_lifecycle_locks=mock.Mock(
                            return_value=contextlib.nullcontext()),
                        reconcile_prepare_transaction_locked=mock.Mock(
                            return_value=None),
                        _load_local_paper_deployment_evidence=deployment,
                        require_fresh_campaign_admission=admission), \
                        self.assertRaisesRegex(
                            RuntimeError,
                            "REPAIR_CAMPAIGN_POLICY_PIN_MISMATCH"):
                    prepare.commit_campaign(
                        policy, str(policy["strategy_sha256"]), deadline,
                        duration, cycles, None, None)
                deployment.assert_not_called()
                admission.assert_not_called()

    def test_commit_quarantines_v4_before_strategy_deployment_or_wal(
            self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy_path = root / "policy.json"
            strategy_path = root / "strategy.json"
            policy = self.policy()
            policy_path.write_text(json.dumps(policy), encoding="ascii")
            stale_digest = "sha256:" + hashlib.sha256(
                b"strategy-before-validation\n").hexdigest()
            admission = mock.Mock()
            deployment = mock.Mock()
            persist = mock.Mock()
            timer_mutation = mock.Mock()
            with mock.patch.multiple(
                    prepare,
                    POLICY_PATH=policy_path,
                    STRATEGY_PATH=strategy_path,
                    campaign_lifecycle_locks=mock.Mock(
                        return_value=contextlib.nullcontext()),
                    _load_prepare_transaction=mock.Mock(return_value=None),
                    _load_local_paper_deployment_evidence=deployment,
                    _persist_prepare_transaction=persist,
                    keep_background_timers_stopped=timer_mutation,
                    require_fresh_campaign_admission=admission), \
                    self.assertRaisesRegex(
                        RuntimeError, "REPAIR_P1_ADMISSION_REQUIRED"):
                prepare.commit_campaign(
                    policy, stale_digest, 1300, 300, 2, None, None)
            admission.assert_not_called()
            deployment.assert_not_called()
            persist.assert_not_called()
            timer_mutation.assert_not_called()

    def test_commit_quarantine_precedes_deadline_and_deployment_checks(
            self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy_path = root / "policy.json"
            strategy_path = root / "strategy.json"
            policy = self.policy()
            policy_path.write_text(json.dumps(policy), encoding="ascii")
            strategy_raw = b"strategy\n"
            strategy_path.write_bytes(strategy_raw)
            strategy_digest = "sha256:" + hashlib.sha256(
                strategy_raw).hexdigest()
            deployment = mock.Mock(return_value=self.deployment_snapshot())
            with mock.patch.multiple(
                    prepare,
                    POLICY_PATH=policy_path,
                    STRATEGY_PATH=strategy_path,
                    campaign_lifecycle_locks=mock.Mock(
                        return_value=contextlib.nullcontext()),
                    _load_prepare_transaction=mock.Mock(return_value=None),
                    _load_local_paper_deployment_evidence=deployment,
                    require_fresh_campaign_admission=mock.Mock()), \
                    mock.patch.object(
                        prepare.time, "time_ns",
                        return_value=1_001_000_000_000), \
                    self.assertRaisesRegex(
                        RuntimeError, "REPAIR_P1_ADMISSION_REQUIRED"):
                prepare.commit_campaign(
                    policy, strategy_digest, 1300, 300, 2, None, None)
            deployment.assert_not_called()

    def test_restore_unit_file_preserves_payload_and_mode_or_absence(
            self) -> None:
        path = Path("/etc/systemd/system/test.service")
        with mock.patch.object(prepare, "atomic_install") as install, \
                mock.patch.object(
                    prepare, "_remove_installed_unit_file") as remove:
            prepare.restore_unit_file(
                path, prepare.UnitFileSnapshot(b"old-unit\n", 0o640))
            prepare.restore_unit_file(
                path, prepare.UnitFileSnapshot(None, None))
        install.assert_called_once_with(path, b"old-unit\n", 0o640)
        remove.assert_called_once_with(path)

    def test_disarm_is_checked_and_only_targets_loaded_old_units(self) -> None:
        missing = prepare.SystemdUnitSnapshot(
            "not-found", "", "inactive")
        snapshots = {
            prepare.STOP_UNIT + ".timer": missing,
            prepare.STOP_UNIT + ".service": missing,
            prepare.PERSISTENT_STOP_UNIT + ".timer":
                prepare.SystemdUnitSnapshot("loaded", "enabled", "active"),
            prepare.PERSISTENT_STOP_UNIT + ".service":
                prepare.SystemdUnitSnapshot("loaded", "static", "inactive"),
            prepare.RETRY_TIMER_UNIT + ".timer":
                prepare.SystemdUnitSnapshot("loaded", "static", "inactive"),
        }
        loaded = (
            prepare.PERSISTENT_STOP_UNIT + ".timer",
            prepare.PERSISTENT_STOP_UNIT + ".service",
            prepare.RETRY_TIMER_UNIT + ".timer",
        )
        with mock.patch.object(prepare, "_systemctl") as systemctl:
            prepare.disarm_old_stop_units(snapshots)
        self.assertEqual(systemctl.call_args_list[:2], [
            mock.call("stop", *loaded, timeout=330),
            mock.call(
                "disable", prepare.PERSISTENT_STOP_UNIT + ".timer",
                timeout=30),
        ])
        self.assertCountEqual(systemctl.call_args_list[2:], [
            mock.call(
                "disable", "--runtime",
                prepare.PERSISTENT_STOP_UNIT + ".timer"),
            mock.call("reset-failed", *loaded),
        ])

    def test_disarm_accepts_inactive_units_reset_failed_not_loaded(self) -> None:
        loaded = (
            prepare.PERSISTENT_STOP_UNIT + ".timer",
            prepare.PERSISTENT_STOP_UNIT + ".service",
            prepare.RETRY_TIMER_UNIT + ".timer",
        )
        snapshots = {
            unit: prepare.SystemdUnitSnapshot(
                "not-found", "", "inactive")
            for unit in prepare.stop_runtime_units()
        }
        snapshots.update({
            unit: prepare.SystemdUnitSnapshot(
                "loaded", "disabled", "inactive")
            for unit in loaded
        })
        reset_error = RuntimeError(
            "REPAIR_SYSTEMD_FAILED: " + "\n".join(
                f"Failed to reset failed state of unit {unit}: Unit {unit} "
                "not loaded."
                for unit in loaded))
        current = {
            unit: prepare.SystemdUnitSnapshot(
                "loaded", "disabled", "inactive")
            for unit in loaded
        }

        def systemctl(*arguments: str, **_kwargs: object) -> str:
            if arguments[0] == "reset-failed":
                raise reset_error
            return ""

        with mock.patch.object(prepare, "_systemctl", side_effect=systemctl), \
                mock.patch.object(
                    prepare, "read_systemd_unit_state",
                    side_effect=lambda unit, **_kwargs: current[unit]):
            prepare.disarm_old_stop_units(snapshots)

    def test_disarm_does_not_mask_reset_failed_permission_error(self) -> None:
        loaded = (
            prepare.PERSISTENT_STOP_UNIT + ".timer",
            prepare.PERSISTENT_STOP_UNIT + ".service",
        )
        snapshots = {
            unit: prepare.SystemdUnitSnapshot(
                "not-found", "", "inactive")
            for unit in prepare.stop_runtime_units()
        }
        snapshots.update({
            unit: prepare.SystemdUnitSnapshot(
                "loaded", "disabled", "inactive")
            for unit in loaded
        })

        def systemctl(*arguments: str, **_kwargs: object) -> str:
            if arguments[0] == "reset-failed":
                raise RuntimeError(
                    "REPAIR_SYSTEMD_FAILED: Access denied")
            return ""

        with mock.patch.object(prepare, "_systemctl", side_effect=systemctl), \
                self.assertRaisesRegex(
                    RuntimeError, "REPAIR_SYSTEMD_FAILED: Access denied"):
            prepare.disarm_old_stop_units(snapshots)

    def test_disarm_rejects_not_loaded_reset_when_unit_active(self) -> None:
        loaded = (
            prepare.PERSISTENT_STOP_UNIT + ".timer",
            prepare.PERSISTENT_STOP_UNIT + ".service",
        )
        snapshots = {
            unit: prepare.SystemdUnitSnapshot(
                "not-found", "", "inactive")
            for unit in prepare.stop_runtime_units()
        }
        snapshots.update({
            unit: prepare.SystemdUnitSnapshot(
                "loaded", "disabled", "inactive")
            for unit in loaded
        })
        reset_error = RuntimeError(
            "REPAIR_SYSTEMD_FAILED: Failed to reset failed state of unit "
            f"{loaded[0]}: Unit {loaded[0]} not loaded.")
        current = {
            loaded[0]: prepare.SystemdUnitSnapshot(
                "loaded", "disabled", "active"),
            loaded[1]: prepare.SystemdUnitSnapshot(
                "loaded", "disabled", "inactive"),
        }

        def systemctl(*arguments: str, **_kwargs: object) -> str:
            if arguments[0] == "reset-failed":
                raise reset_error
            return ""

        with mock.patch.object(prepare, "_systemctl", side_effect=systemctl), \
                mock.patch.object(
                    prepare, "read_systemd_unit_state",
                    side_effect=lambda unit, **_kwargs: current[unit]), \
                self.assertRaisesRegex(
                    RuntimeError, "REPAIR_SYSTEMD_RESET_FAILED_STATE_DRIFTED"):
            prepare.disarm_old_stop_units(snapshots)

    def test_restore_systemd_states_reinstates_enablement_and_activity(
            self) -> None:
        snapshots = {
            "enabled.timer": prepare.SystemdUnitSnapshot(
                "loaded", "enabled", "active"),
            "runtime.timer": prepare.SystemdUnitSnapshot(
                "loaded", "enabled-runtime", "inactive"),
            "disabled.timer": prepare.SystemdUnitSnapshot(
                "loaded", "disabled", "inactive"),
            "static.service": prepare.SystemdUnitSnapshot(
                "loaded", "static", "inactive"),
            "missing.timer": prepare.SystemdUnitSnapshot(
                "not-found", "", "inactive"),
        }
        with mock.patch.object(prepare, "_systemctl") as systemctl, \
                mock.patch.object(
                    prepare, "read_systemd_unit_state",
                    side_effect=lambda unit: snapshots[unit]):
            prepare.restore_systemd_unit_states(snapshots)
        prefix = systemctl.call_args_list[:2]
        self.assertEqual(prefix[0], mock.call(
            "disable", "enabled.timer", "runtime.timer",
            "disabled.timer", timeout=30))
        self.assertEqual(prefix[1], mock.call(
            "disable", "--runtime", "enabled.timer", "runtime.timer",
            "disabled.timer"))
        self.assertCountEqual(systemctl.call_args_list[2:], [
            mock.call("enable", "enabled.timer"),
            mock.call("enable", "--runtime", "runtime.timer"),
            mock.call("start", "enabled.timer", timeout=330),
            mock.call("stop", "runtime.timer", timeout=330),
            mock.call("stop", "disabled.timer", timeout=330),
            mock.call("stop", "static.service", timeout=330),
        ])

    def test_main_quarantines_v4_before_strategy_commit_or_side_effect(
            self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy_path = root / "policy.json"
            env_path = root / "agent.env"
            strategy_path = root / "strategy.json"
            policy_path.write_text(json.dumps(self.policy()), encoding="ascii")
            env_path.write_bytes(b"old-env\n")
            reconcile = mock.Mock(return_value=None)
            commit = mock.Mock()
            persist = mock.Mock()
            keep = mock.Mock()
            disarm = mock.Mock()
            rollback = mock.Mock()
            arm = mock.Mock()
            write = mock.Mock()
            argv = [str(PREPARE_SOURCE), "--duration-seconds", "300"]
            with contextlib.ExitStack() as stack:
                stack.enter_context(mock.patch.multiple(
                    prepare,
                    POLICY_PATH=policy_path,
                    AGENT_ENV_PATH=env_path,
                    STRATEGY_PATH=strategy_path,
                    campaign_lifecycle_locks=mock.Mock(
                        return_value=contextlib.nullcontext()),
                    reconcile_prepare_transaction_locked=reconcile,
                    commit_campaign=commit,
                    _persist_prepare_transaction=persist,
                    keep_background_timers_stopped=keep,
                    disarm_old_stop_units=disarm,
                    atomic_write=write,
                    arm_stop_timer=arm,
                    _rollback_prepare_transaction=rollback,
                ))
                stack.enter_context(mock.patch.object(
                    prepare.os, "geteuid", return_value=0))
                stack.enter_context(mock.patch.object(
                    prepare.time, "time_ns",
                    return_value=1_000_000_000_000))
                stack.enter_context(mock.patch.object(
                    prepare.sys, "argv", argv))
                stack.enter_context(self.assertRaisesRegex(
                    RuntimeError, "REPAIR_P1_ADMISSION_REQUIRED"))
                prepare.main()
        reconcile.assert_called_once_with()
        commit.assert_not_called()
        persist.assert_not_called()
        keep.assert_not_called()
        disarm.assert_not_called()
        arm.assert_not_called()
        write.assert_not_called()
        rollback.assert_not_called()

    def test_prepare_quarantine_rejects_v4_but_allows_v5_local(self) -> None:
        future = {
            "schema": "hepta.ib-paper-campaign-policy.v5",
            "version": 5,
            "admission_mode": "external-p1-finalized",
        }
        prepare._require_p1_bound_prepare_policy(future)
        local = dict(future)
        local["admission_mode"] = "local-only"
        prepare._require_p1_bound_prepare_policy(local)
        for marker in (
                {"schema": "hepta.ib-paper-campaign-policy.v4"},
                {"version": 4}):
            document = dict(future)
            document.update(marker)
            with self.subTest(marker=marker), self.assertRaisesRegex(
                    RuntimeError, "REPAIR_P1_ADMISSION_REQUIRED"):
                prepare._require_p1_bound_prepare_policy(document)


if __name__ == "__main__":
    unittest.main(verbosity=2)
