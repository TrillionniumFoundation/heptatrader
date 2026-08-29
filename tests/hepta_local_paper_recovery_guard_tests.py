#!/usr/bin/env python3

from __future__ import annotations

import contextlib
from importlib.machinery import SourceFileLoader
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "scripts/run_paper_safe_recover_guard.py"
loader = SourceFileLoader("hepta_safe_recover_guard", str(SOURCE))
spec = importlib.util.spec_from_loader(loader.name, loader)
assert spec is not None and spec.loader is not None
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)
ORIGINAL_SAFETY_LATCH_EXISTS = guard.safety_latch_exists
ORIGINAL_AUTOMATIC_RISK_RECOVERY_CONSUMED = (
    guard.automatic_risk_recovery_consumed)
ORIGINAL_START_ATTEMPT_UNCERTAIN = guard.start_attempt_uncertain
ORIGINAL_RECOVERY_CAMPAIGN_BINDING = guard.recovery_campaign_binding
ORIGINAL_SCHEDULE_TERMINAL_END_FLAT = guard.schedule_terminal_end_flat
ORIGINAL_SCHEDULE_ORPHAN_START_END_FLAT = (
    guard.schedule_orphan_start_end_flat)

SAFE_SOURCE = ROOT / "scripts/run_paper_safe_recover.py"
safe_loader = SourceFileLoader("hepta_safe_recover", str(SAFE_SOURCE))
safe_spec = importlib.util.spec_from_loader(safe_loader.name, safe_loader)
assert safe_spec is not None and safe_spec.loader is not None
safe_recover = importlib.util.module_from_spec(safe_spec)
safe_spec.loader.exec_module(safe_recover)


class SafeRecoverGuardTests(unittest.TestCase):
    @staticmethod
    def root_metadata() -> SimpleNamespace:
        return SimpleNamespace(
            st_mode=stat.S_IFREG | 0o600, st_nlink=1,
            st_uid=0, st_gid=0)

    def setUp(self) -> None:
        # Existing state-routing tests predate the independent exit-75 latch.
        # Keep it absent by default; dedicated tests below exercise it.
        patcher = mock.patch.object(
            guard, "safety_latch_exists", return_value=False)
        self.addCleanup(patcher.stop)
        patcher.start()
        for target, value in (
                ("external_p1_policy", None),
                ("single_flight", contextlib.nullcontext(True)),
                ("automatic_risk_recovery_consumed", False),
                ("managed_session_authority_present", False),
                ("recovery_campaign_binding", "same"),
                ("start_attempt_uncertain", False),
                ("schedule_terminal_end_flat", None),
                ("schedule_orphan_start_end_flat", False)):
            patcher = mock.patch.object(guard, target, return_value=value)
            self.addCleanup(patcher.stop)
            patcher.start()
        patcher = mock.patch.object(
            safe_recover, "external_p1_finalized", return_value=False)
        self.addCleanup(patcher.stop)
        patcher.start()

    def test_safety_exit_latch_fsyncs_file_and_parent_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            latch = Path(directory) / "safety-stop.pending.json"
            with mock.patch.object(guard, "SAFETY_LATCH", latch), \
                    mock.patch.object(
                        guard, "safety_latch_exists",
                        wraps=ORIGINAL_SAFETY_LATCH_EXISTS), \
                    mock.patch.object(
                        guard.os, "fstat", return_value=self.root_metadata()), \
                    mock.patch.object(
                        guard.os, "fsync", wraps=os.fsync) as sync:
                guard.persist_safety_exit_latch()
            value = json.loads(latch.read_text(encoding="ascii"))
        self.assertEqual(
            value["schema"], "hepta.local-ai-paper-safety-exit-latch.v1")
        self.assertGreaterEqual(sync.call_count, 2)

    def test_external_retained_enable_recovery_requires_operator(self) -> None:
        policy = {"campaign_id": "p1-campaign-test"}
        with mock.patch.object(
                guard, "reconcile_external_control", return_value={
                    "recovery_retained": True,
                    "wal_operation": "ENABLE_RECOVERY",
                }), \
                mock.patch.object(
                    guard, "external_recovery_incident_present") as incident, \
                mock.patch.object(guard, "stop_agent") as stop_agent, \
                mock.patch.object(guard, "run") as run:
            self.assertEqual(guard.recover_external_once(policy), 0)
        incident.assert_not_called()
        stop_agent.assert_not_called()
        run.assert_not_called()

    def test_external_no_incident_is_a_strict_noop(self) -> None:
        policy = {"campaign_id": "p1-campaign-test"}
        with mock.patch.object(
                guard, "reconcile_external_control", return_value={}), \
                mock.patch.object(
                    guard, "external_recovery_incident_present",
                    return_value=False), \
                mock.patch.object(guard, "stop_agent") as stop_agent, \
                mock.patch.object(guard, "run") as run:
            self.assertEqual(guard.recover_external_once(policy), 0)
        stop_agent.assert_not_called()
        run.assert_not_called()

    def test_external_incident_only_invokes_automatic_risk_recovery(
            self) -> None:
        policy = {"campaign_id": "p1-campaign-test"}
        completed = SimpleNamespace(returncode=0, stdout="", stderr="")
        with mock.patch.object(
                guard, "reconcile_external_control", return_value={}), \
                mock.patch.object(
                    guard, "external_recovery_incident_present",
                    return_value=True), \
                mock.patch.object(guard, "stop_agent") as stop_agent, \
                mock.patch.object(
                    guard, "run", return_value=completed) as run, \
                mock.patch.object(
                    guard, "schedule_terminal_end_flat") as schedule:
            self.assertEqual(guard.recover_external_once(policy), 0)
        stop_agent.assert_called_once_with()
        run.assert_called_once_with(
            [*guard.RISK_RECOVER, "--automatic"],
            timeout=guard.RISK_RECOVER_TIMEOUT_SECONDS)
        schedule.assert_not_called()

    def test_legacy_safe_recover_external_policy_is_a_noop(self) -> None:
        with mock.patch.object(
                safe_recover, "external_p1_finalized", return_value=True), \
                mock.patch.object(
                    safe_recover, "verify_static_boundary") as boundary, \
                mock.patch.object(safe_recover, "active") as active, \
                mock.patch.object(safe_recover, "run") as run:
            self.assertEqual(safe_recover.recover_once(), 0)
        boundary.assert_not_called()
        active.assert_not_called()
        run.assert_not_called()

    def test_orphaned_start_permit_requests_terminal_end_flat(
            self) -> None:
        latched = (False, False, "", True, False)
        with mock.patch.object(
                guard, "agent_runtime_status", return_value=(False, False)), \
                mock.patch.object(guard, "state_latch", return_value=latched), \
                mock.patch.object(
                    guard, "start_attempt_uncertain", return_value=True), \
                mock.patch.object(
                    guard, "schedule_orphan_start_end_flat",
                    return_value=True) as schedule, \
                mock.patch.object(guard, "run") as run:
            result = guard.recover_once()
        self.assertEqual(result, 0)
        schedule.assert_called_once_with()
        run.assert_not_called()

    def test_start_uncertainty_only_accepts_root_bound_permit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pending = root / "start-permit.pending.json"
            claimed = root / "start-permit.claimed.json"
            consumed = root / "start-permit.consumed.json"
            claimed.write_text(json.dumps({
                "schema": "hepta.local-ai-paper-start-permit.v1",
                "campaign_id": "campaign-test", "permit_id": "permit-test",
                "paper_only": True, "live_authorized": False,
            }), encoding="ascii")
            claimed.chmod(0o600)
            real_lstat = guard.os.lstat

            def root_lstat(path: object) -> SimpleNamespace:
                metadata = real_lstat(path)
                return SimpleNamespace(
                    st_mode=metadata.st_mode, st_nlink=metadata.st_nlink,
                    st_uid=0, st_gid=0, st_size=metadata.st_size)

            with mock.patch.object(guard, "START_PERMIT_PENDING", pending), \
                    mock.patch.object(
                        guard, "START_PERMIT_CLAIMED", claimed), \
                    mock.patch.object(
                        guard, "START_PERMIT_CONSUMED", consumed), \
                    mock.patch.object(
                        guard.os, "lstat", side_effect=root_lstat):
                self.assertTrue(ORIGINAL_START_ATTEMPT_UNCERTAIN())
            claimed.write_text("{\"schema\":", encoding="ascii")
            with mock.patch.object(guard, "START_PERMIT_PENDING", pending), \
                    mock.patch.object(
                        guard, "START_PERMIT_CLAIMED", claimed), \
                    mock.patch.object(
                        guard, "START_PERMIT_CONSUMED", consumed), \
                    mock.patch.object(
                        guard.os, "lstat", side_effect=root_lstat):
                self.assertTrue(ORIGINAL_START_ATTEMPT_UNCERTAIN())

    def test_pending_permit_is_terminal_across_expiry_and_reboot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pending = root / "start-permit.pending.json"
            claimed = root / "start-permit.claimed.json"
            consumed = root / "start-permit.consumed.json"
            pending.write_text(json.dumps({
                "schema": "hepta.local-ai-paper-start-permit.v1",
                "campaign_id": "campaign-test", "permit_id": "p" * 64,
                "boot_id": "old-boot", "not_after_ms": 1,
                "paper_only": True, "live_authorized": False,
            }), encoding="ascii")
            pending.chmod(0o600)
            real_lstat = guard.os.lstat

            def root_lstat(path: object) -> SimpleNamespace:
                metadata = real_lstat(path)
                return SimpleNamespace(
                    st_mode=metadata.st_mode, st_nlink=metadata.st_nlink,
                    st_uid=0, st_gid=0, st_size=metadata.st_size)

            with mock.patch.object(guard, "START_PERMIT_PENDING", pending), \
                    mock.patch.object(
                        guard, "START_PERMIT_CLAIMED", claimed), \
                    mock.patch.object(
                        guard, "START_PERMIT_CONSUMED", consumed), \
                    mock.patch.object(
                        guard.os, "lstat", side_effect=root_lstat):
                self.assertTrue(ORIGINAL_START_ATTEMPT_UNCERTAIN())

    def test_torn_automatic_attempt_is_consumed_and_never_retried(
            self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "automatic-attempt.json"
            marker.write_bytes(b'{"schema":')
            marker.chmod(0o600)
            real_lstat = guard.os.lstat

            def root_lstat(path: object) -> SimpleNamespace:
                metadata = real_lstat(path)
                return SimpleNamespace(
                    st_mode=metadata.st_mode, st_nlink=metadata.st_nlink,
                    st_uid=0, st_gid=0, st_size=metadata.st_size)

            with mock.patch.object(
                    guard, "AUTOMATIC_RISK_ATTEMPT", marker), \
                    mock.patch.object(
                        guard.os, "lstat", side_effect=root_lstat):
                self.assertTrue(ORIGINAL_AUTOMATIC_RISK_RECOVERY_CONSUMED())

    def test_recovered_latch_never_delegates_to_normal_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state.json"
            suspension_id = "suspension-recovered-test"
            receipt = {
                "schema": "hepta.local-ai-paper-risk-recovery-receipt.v1",
                "suspension_id": suspension_id,
                "position": 0,
                "active_orders": 0,
                "gross_absolute_position": 0,
                "trading_resumed": False,
            }
            receipt_raw = json.dumps(receipt).encode("ascii")
            receipt_path = Path(directory) / (
                "risk-recovery-" + hashlib.sha256(
                    suspension_id.encode("utf-8")).hexdigest()[:24] +
                ".receipt.json")
            receipt_path.write_bytes(receipt_raw)
            state.write_text(json.dumps({
                "trading_suspended": True,
                "recovery_required": True,
                "recovery_complete": True,
                "recovery_phase": "FLAT_CONFIRMED",
                "suspension_code": "MODEL_AUTH_RATE_LIMIT",
                "suspension_id": suspension_id,
                "recovery_receipt_sha256": (
                    "sha256:" + hashlib.sha256(receipt_raw).hexdigest()),
            }), encoding="ascii")
            with mock.patch.object(guard, "STATE", state), \
                    mock.patch.object(
                        guard.os, "lstat", return_value=self.root_metadata()), \
                    mock.patch.object(guard, "run") as run:
                run.side_effect = [
                    SimpleNamespace(
                        returncode=0,
                        stdout=("ActiveState=inactive\nResult=success\n"
                                "ExecMainStatus=0\n"), stderr=""),
                    SimpleNamespace(returncode=0, stdout="", stderr=""),
                    SimpleNamespace(returncode=3, stdout="inactive\n", stderr=""),
                    SimpleNamespace(returncode=0, stdout="", stderr=""),
                ]
                result = guard.main()
        self.assertEqual(result, 0)
        guard.schedule_terminal_end_flat.assert_called_once_with()
        self.assertEqual(
            run.call_args_list,
            [mock.call([
                "/usr/bin/systemctl", "show",
                "hepta-local-ai-paper-agent.service",
                "--property", "ActiveState", "--property", "Result",
                "--property", "ExecMainStatus"], timeout=10),
             mock.call([
                "/usr/bin/systemctl", "stop",
                "hepta-local-ai-paper-agent.service"], timeout=30),
             mock.call([
                "/usr/bin/systemctl", "is-active",
                "hepta-local-ai-paper-agent.service"], timeout=10),
             mock.call([
                "/usr/bin/systemctl", "disable", "--now",
                *guard.CAMPAIGN_BACKGROUND_TIMERS], timeout=30)])

    def test_recovered_latch_does_not_close_fresh_campaign(self) -> None:
        """An old FLAT_CONFIRMED receipt cannot fence a fresh prepare."""
        latched = (True, True, "MODEL_AUTH_RATE_LIMIT", False, False)
        with mock.patch.object(
                guard, "agent_runtime_status", return_value=(False, False)), \
                mock.patch.object(guard, "state_latch", return_value=latched), \
                mock.patch.object(guard, "stop_agent") as stop_agent, \
                mock.patch.object(
                    guard, "managed_session_authority_present",
                    return_value=False), \
                mock.patch.object(
                    guard, "recovery_campaign_binding", return_value="fresh") \
                as binding, \
                mock.patch.object(
                    guard, "schedule_terminal_end_flat") as schedule, \
                mock.patch.object(
                    guard, "seal_recovered_campaign_timers") as seal:
            result = guard.recover_once()

        self.assertEqual(result, 0)
        stop_agent.assert_not_called()
        binding.assert_called_once_with()
        schedule.assert_not_called()
        seal.assert_not_called()

    def test_active_agent_foreign_binding_is_classified_before_stop(self) -> None:
        """A latched live agent is stopped only after fresh binding proof."""
        latched = (True, True, "MODEL_AUTH_RATE_LIMIT", False, False)
        events: list[str] = []

        def classify() -> str:
            events.append("binding")
            return "fresh"

        def stop() -> None:
            events.append("stop")

        with mock.patch.object(
                guard, "agent_runtime_status", return_value=(True, False)), \
                mock.patch.object(guard, "state_latch", return_value=latched), \
                mock.patch.object(
                    guard, "stop_agent", side_effect=stop) as stop_agent, \
                mock.patch.object(
                    guard, "managed_session_authority_present",
                    return_value=False), \
                mock.patch.object(
                    guard, "recovery_campaign_binding", side_effect=classify) \
                as binding, \
                mock.patch.object(
                    guard, "schedule_terminal_end_flat") as schedule, \
                mock.patch.object(
                    guard, "seal_recovered_campaign_timers") as seal, \
                mock.patch.object(guard, "run") as run:
            result = guard.recover_once()

        self.assertEqual(result, 0)
        self.assertEqual(events, ["binding", "stop"])
        binding.assert_called_once_with()
        stop_agent.assert_called_once_with()
        schedule.assert_not_called()
        seal.assert_not_called()
        run.assert_not_called()

    def test_recovered_latch_unknown_campaign_binding_stays_deferred(self) -> None:
        """Unreadable/drifted binding never grants or schedules authority."""
        latched = (True, True, "MODEL_AUTH_RATE_LIMIT", False, False)
        with mock.patch.object(
                guard, "agent_runtime_status", return_value=(False, False)), \
                mock.patch.object(guard, "state_latch", return_value=latched), \
                mock.patch.object(guard, "stop_agent") as stop_agent, \
                mock.patch.object(
                    guard, "managed_session_authority_present",
                    return_value=False), \
                mock.patch.object(
                    guard, "recovery_campaign_binding", return_value="unknown") \
                as binding, \
                mock.patch.object(
                    guard, "schedule_terminal_end_flat") as schedule, \
                mock.patch.object(
                    guard, "seal_recovered_campaign_timers") as seal:
            result = guard.recover_once()

        self.assertEqual(result, 1)
        stop_agent.assert_not_called()
        binding.assert_called_once_with()
        schedule.assert_not_called()
        seal.assert_not_called()

    def test_recovery_campaign_binding_is_strictly_same_or_fresh(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / "state.json"
            policy = root / "alpha.json"

            def write_policy(campaign_id: str) -> None:
                policy.write_text(json.dumps({
                    "schema": "hepta.ib-paper-campaign-policy.v5",
                    "version": 5,
                    "domain_id": "alpha",
                    "campaign_id": campaign_id,
                }), encoding="ascii")

            state.write_text(json.dumps({
                "campaign_id_at_suspend": "campaign-old-20260812",
            }), encoding="ascii")
            write_policy("campaign-old-20260812")
            with mock.patch.object(guard, "STATE", state), \
                    mock.patch.object(guard, "POLICY_FILE", policy), \
                    mock.patch.object(
                        guard, "recovery_campaign_binding",
                        wraps=ORIGINAL_RECOVERY_CAMPAIGN_BINDING), \
                    mock.patch.object(
                        guard.os, "lstat",
                        return_value=self.root_metadata()):
                self.assertEqual(guard.recovery_campaign_binding(), "same")

                write_policy("campaign-new-20260826")
                self.assertEqual(guard.recovery_campaign_binding(), "fresh")

                write_policy("not valid")
                self.assertEqual(guard.recovery_campaign_binding(), "unknown")

    def test_recovered_state_with_new_authority_forces_exact_recovery(
            self) -> None:
        latched = (True, True, "MODEL_AUTH_RATE_LIMIT", False, False)
        completed = SimpleNamespace(
            returncode=0, stdout="RISK_RECOVERY_COMPLETE\n", stderr="")
        with mock.patch.object(
                guard, "agent_runtime_status", return_value=(False, False)), \
                mock.patch.object(guard, "state_latch", side_effect=[
                    latched, latched]), \
                mock.patch.object(guard, "stop_agent") as stop_agent, \
                mock.patch.object(
                    guard, "managed_session_authority_present",
                    return_value=True), \
                mock.patch.object(
                    guard, "automatic_risk_recovery_consumed",
                    return_value=True), \
                mock.patch.object(
                    guard, "run", return_value=completed) as run, \
                mock.patch.object(
                    guard, "schedule_terminal_end_flat") as schedule, \
                mock.patch.object(
                    guard, "seal_recovered_campaign_timers") as seal:
            result = guard.recover_once()
        self.assertEqual(result, 0)
        stop_agent.assert_called_once_with()
        run.assert_called_once_with(
            [*guard.RISK_RECOVER, "--automatic"],
            timeout=guard.RISK_RECOVER_TIMEOUT_SECONDS)
        schedule.assert_called_once_with()
        seal.assert_called_once_with()

    def test_successful_risk_recovery_defers_while_state_is_unproven(
            self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state.json"
            state.write_text(json.dumps({
                "trading_suspended": True,
                "recovery_required": True,
                "recovery_complete": False,
                "recovery_phase": "REQUESTED",
                "suspension_code": "TRADE_TOOL_BUDGET_EXHAUSTED",
            }), encoding="ascii")
            completed = SimpleNamespace(
                returncode=0, stdout="RISK_RECOVERY_COMPLETE\n", stderr="")
            with mock.patch.object(guard, "STATE", state), \
                    mock.patch.object(
                        guard.os, "lstat", return_value=self.root_metadata()), \
                    mock.patch.object(
                        guard, "run", side_effect=[
                            SimpleNamespace(
                                returncode=0,
                                stdout=("ActiveState=inactive\nResult=success\n"
                                        "ExecMainStatus=0\n"), stderr=""),
                            SimpleNamespace(returncode=0, stdout="", stderr=""),
                            SimpleNamespace(returncode=3, stdout="inactive\n", stderr=""),
                            completed,
                        ]) as run:
                result = guard.main()
        self.assertEqual(result, 1)
        self.assertEqual(run.call_args_list[3], mock.call(
            ["/usr/libexec/hepta-local-paper-repair", "risk-recover",
             "--automatic"],
            timeout=guard.RISK_RECOVER_TIMEOUT_SECONDS))
        self.assertFalse(any(
            item.args[0][:3] == [
                "/usr/bin/systemctl", "disable", "--now"]
            for item in run.call_args_list))
        self.assertNotIn(
            guard.NORMAL_RECOVER,
            [call.args[0] for call in run.call_args_list])

    def test_successful_risk_recovery_seals_timers_after_receipt_proof(
            self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / "state.json"
            suspension_id = "suspension-automatic-recovery-test"
            suspension_code = "ORDER_STATE_UNCERTAIN"
            state.write_text(json.dumps({
                "trading_suspended": True,
                "recovery_required": True,
                "recovery_complete": False,
                "recovery_phase": "REQUESTED",
                "suspension_code": suspension_code,
                "suspension_id": suspension_id,
            }), encoding="ascii")

            def publish_recovered_state() -> None:
                receipt = {
                    "schema":
                        "hepta.local-ai-paper-risk-recovery-receipt.v1",
                    "suspension_id": suspension_id,
                    "position": 0,
                    "active_orders": 0,
                    "gross_absolute_position": 0,
                    "trading_resumed": False,
                }
                receipt_raw = json.dumps(receipt).encode("ascii")
                receipt_path = root / (
                    "risk-recovery-" + hashlib.sha256(
                        suspension_id.encode("utf-8")).hexdigest()[:24] +
                    ".receipt.json")
                receipt_path.write_bytes(receipt_raw)
                state.write_text(json.dumps({
                    "trading_suspended": True,
                    "recovery_required": True,
                    "recovery_complete": True,
                    "recovery_phase": "FLAT_CONFIRMED",
                    "suspension_code": suspension_code,
                    "suspension_id": suspension_id,
                    "recovery_receipt_sha256": (
                        "sha256:" + hashlib.sha256(receipt_raw).hexdigest()),
                }), encoding="ascii")

            def execute(
                    command: list[str], timeout: int
                    ) -> SimpleNamespace:
                if command[:3] == [
                        "/usr/bin/systemctl", "show",
                        guard.AGENT_SERVICE]:
                    return SimpleNamespace(
                        returncode=0,
                        stdout=("ActiveState=inactive\nResult=success\n"
                                "ExecMainStatus=0\n"), stderr="")
                if command[:2] == ["/usr/bin/systemctl", "stop"]:
                    return SimpleNamespace(
                        returncode=0, stdout="", stderr="")
                if command[:2] == ["/usr/bin/systemctl", "is-active"]:
                    return SimpleNamespace(
                        returncode=3, stdout="inactive\n", stderr="")
                if command == [*guard.RISK_RECOVER, "--automatic"]:
                    publish_recovered_state()
                    return SimpleNamespace(
                        returncode=0,
                        stdout="RISK_RECOVERY_COMPLETE\n", stderr="")
                if command[:3] == [
                        "/usr/bin/systemctl", "disable", "--now"]:
                    return SimpleNamespace(
                        returncode=0, stdout="", stderr="")
                raise AssertionError(f"unexpected command: {command!r}")

            with mock.patch.object(guard, "STATE", state), \
                    mock.patch.object(
                        guard.os, "lstat",
                        return_value=self.root_metadata()), \
                    mock.patch.object(
                        guard, "run", side_effect=execute) as run:
                result = guard.main()

        self.assertEqual(result, 0)
        self.assertEqual(run.call_args_list[-1], mock.call([
            "/usr/bin/systemctl", "disable", "--now",
            *guard.CAMPAIGN_BACKGROUND_TIMERS,
        ], timeout=30))
        self.assertNotIn(
            guard.NORMAL_RECOVER,
            [item.args[0] for item in run.call_args_list])

    def test_post_recovery_requires_latched_and_recovered_state(self) -> None:
        for observed in (
                (True, False, "ORDER_STATE_UNCERTAIN", False, False),
                (False, True, "ORDER_STATE_UNCERTAIN", False, False)):
            with self.subTest(observed=observed), \
                    mock.patch.object(
                        guard, "agent_runtime_status",
                        return_value=(False, False)), \
                    mock.patch.object(
                        guard, "state_latch",
                        side_effect=[
                            (True, False, "ORDER_STATE_UNCERTAIN", False,
                             False),
                            observed,
                        ]) as state_latch, \
                    mock.patch.object(guard, "stop_agent"), \
                    mock.patch.object(
                        guard, "run", return_value=SimpleNamespace(
                            returncode=0,
                            stdout="RISK_RECOVERY_COMPLETE\n", stderr="")), \
                    mock.patch.object(
                        guard, "seal_recovered_campaign_timers") as seal:
                result = guard.recover_once()

            self.assertEqual(result, 1)
            self.assertEqual(state_latch.call_count, 2)
            seal.assert_not_called()

    def test_unlatched_state_uses_normal_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state.json"
            state.write_text(json.dumps({
                "trading_suspended": False,
                "recovery_required": False,
                "pending_order_id": None,
            }), encoding="ascii")
            completed = SimpleNamespace(
                returncode=0, stdout="SAFE_RECOVERY_OK\n", stderr="")
            with mock.patch.object(guard, "STATE", state), \
                    mock.patch.object(
                        guard.os, "lstat", return_value=self.root_metadata()), \
                    mock.patch.object(
                        guard, "run", side_effect=[
                            SimpleNamespace(
                                returncode=0,
                                stdout=("ActiveState=inactive\nResult=success\n"
                                        "ExecMainStatus=75\n"), stderr=""),
                            completed,
                        ]) as run:
                result = guard.main()
        self.assertEqual(result, 0)
        self.assertEqual(run.call_args_list[-1], mock.call(
            ["/usr/libexec/hepta-local-paper-safe-recover"],
            timeout=guard.NORMAL_RECOVER_TIMEOUT_SECONDS)
        )

    def test_fresh_pending_with_active_agent_keeps_settlement_running(
            self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state.json"
            state.write_text(json.dumps({
                "trading_suspended": False,
                "recovery_required": False,
                "pending_order_id": 57,
                "pending_order_since_ms": 1_000_000,
            }), encoding="ascii")
            with mock.patch.object(guard, "STATE", state), \
                    mock.patch.object(
                        guard.os, "lstat", return_value=self.root_metadata()), \
                    mock.patch.object(guard, "now_ms", return_value=1_010_000), \
                    mock.patch.object(guard, "run", return_value=SimpleNamespace(
                        returncode=0,
                        stdout=("ActiveState=active\nResult=success\n"
                                "ExecMainStatus=0\n"),
                        stderr="")) as run:
                result = guard.main()

        self.assertEqual(result, 0)
        self.assertEqual(len(run.call_args_list), 1)
        self.assertFalse(any(
            item.args[0][:2] == guard.RISK_RECOVER
            for item in run.call_args_list))
        self.assertNotIn(
            guard.NORMAL_RECOVER,
            [item.args[0] for item in run.call_args_list])

    def test_stale_pending_with_active_agent_routes_to_risk_recovery(
            self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state.json"
            state.write_text(json.dumps({
                "trading_suspended": False,
                "recovery_required": False,
                "pending_order_id": 57,
                "pending_order_since_ms": 1_000_000,
            }), encoding="ascii")
            completed = SimpleNamespace(
                returncode=0, stdout="RISK_RECOVERY_COMPLETE\n", stderr="")
            with mock.patch.object(guard, "STATE", state), \
                    mock.patch.object(
                        guard.os, "lstat", return_value=self.root_metadata()), \
                    mock.patch.object(
                        guard, "now_ms", return_value=(
                            1_000_000 + guard.PENDING_SETTLEMENT_GRACE_MS + 1)), \
                    mock.patch.object(guard, "run", side_effect=[
                        SimpleNamespace(
                            returncode=0,
                            stdout=("ActiveState=active\nResult=success\n"
                                    "ExecMainStatus=0\n"), stderr=""),
                        SimpleNamespace(returncode=0, stdout="", stderr=""),
                        SimpleNamespace(
                            returncode=3, stdout="inactive\n", stderr=""),
                        completed,
                    ]) as run:
                result = guard.main()

        self.assertEqual(result, 1)
        self.assertEqual(run.call_args_list[-1], mock.call(
            ["/usr/libexec/hepta-local-paper-repair", "risk-recover",
             "--automatic"],
            timeout=guard.RISK_RECOVER_TIMEOUT_SECONDS))
        self.assertNotIn(
            guard.NORMAL_RECOVER,
            [item.args[0] for item in run.call_args_list])

    def test_fresh_pending_without_active_agent_routes_to_risk_recovery(
            self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state.json"
            state.write_text(json.dumps({
                "trading_suspended": False,
                "recovery_required": False,
                "pending_order_id": 57,
                "pending_order_since_ms": 1_000_000,
            }), encoding="ascii")
            with mock.patch.object(guard, "STATE", state), \
                    mock.patch.object(
                        guard.os, "lstat", return_value=self.root_metadata()), \
                    mock.patch.object(guard, "now_ms", return_value=1_010_000), \
                    mock.patch.object(guard, "run", side_effect=[
                        SimpleNamespace(
                            returncode=0,
                            stdout=("ActiveState=inactive\nResult=success\n"
                                    "ExecMainStatus=0\n"), stderr=""),
                        SimpleNamespace(returncode=0, stdout="", stderr=""),
                        SimpleNamespace(
                            returncode=3, stdout="inactive\n", stderr=""),
                        SimpleNamespace(
                            returncode=0, stdout="RISK_RECOVERY_COMPLETE\n",
                            stderr=""),
                    ]) as run:
                result = guard.main()

        self.assertEqual(result, 1)
        self.assertEqual(run.call_args_list[-1], mock.call(
            ["/usr/libexec/hepta-local-paper-repair", "risk-recover",
             "--automatic"],
            timeout=guard.RISK_RECOVER_TIMEOUT_SECONDS))

    def test_pending_with_missing_or_future_timestamp_is_latched(self) -> None:
        for pending_since_ms in (None, 1_000_001):
            with self.subTest(pending_since_ms=pending_since_ms), \
                    tempfile.TemporaryDirectory() as directory:
                state = Path(directory) / "state.json"
                state.write_text(json.dumps({
                    "trading_suspended": False,
                    "recovery_required": False,
                    "pending_order_id": 57,
                    "pending_order_since_ms": pending_since_ms,
                }), encoding="ascii")
                with mock.patch.object(guard, "STATE", state), \
                        mock.patch.object(
                            guard.os, "lstat",
                            return_value=self.root_metadata()), \
                        mock.patch.object(
                            guard, "now_ms", return_value=1_000_000):
                    latched, _, _, _, fresh_pending = guard.state_latch()
            self.assertTrue(latched)
            self.assertFalse(fresh_pending)

    def test_explicit_latch_overrides_fresh_pending_and_active_agent(
            self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state.json"
            state.write_text(json.dumps({
                "trading_suspended": True,
                "recovery_required": True,
                "recovery_complete": False,
                "pending_order_id": 57,
                "pending_order_since_ms": 1_000_000,
                "suspension_code": "ORDER_STATE_UNCERTAIN",
            }), encoding="ascii")
            with mock.patch.object(guard, "STATE", state), \
                    mock.patch.object(
                        guard.os, "lstat", return_value=self.root_metadata()), \
                    mock.patch.object(guard, "now_ms", return_value=1_010_000), \
                    mock.patch.object(guard, "run", side_effect=[
                        SimpleNamespace(
                            returncode=0,
                            stdout=("ActiveState=active\nResult=success\n"
                                    "ExecMainStatus=0\n"), stderr=""),
                        SimpleNamespace(returncode=0, stdout="", stderr=""),
                        SimpleNamespace(
                            returncode=3, stdout="inactive\n", stderr=""),
                        SimpleNamespace(
                            returncode=0, stdout="RISK_RECOVERY_COMPLETE\n",
                            stderr=""),
                    ]) as run:
                result = guard.main()

        self.assertEqual(result, 1)
        self.assertEqual(run.call_args_list[-1], mock.call(
            ["/usr/libexec/hepta-local-paper-repair", "risk-recover",
             "--automatic"],
            timeout=guard.RISK_RECOVER_TIMEOUT_SECONDS))

    def test_agent_stop_failure_defers_with_nonzero_status(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state.json"
            state.write_text(json.dumps({
                "trading_suspended": True,
                "recovery_required": True,
                "recovery_complete": False,
            }), encoding="ascii")
            with mock.patch.object(guard, "STATE", state), \
                    mock.patch.object(
                        guard, "persist_safety_exit_latch") as persist, \
                    mock.patch.object(
                        guard.os, "lstat", return_value=self.root_metadata()), \
                    mock.patch.object(guard, "run", side_effect=[
                        SimpleNamespace(
                            returncode=0,
                            stdout=("ActiveState=failed\nResult=exit-code\n"
                                    "ExecMainStatus=75\n"), stderr=""),
                        SimpleNamespace(returncode=1, stdout="", stderr="stop failed"),
                    ]) as run:
                result = guard.main()
        self.assertEqual(result, 1)
        persist.assert_called_once_with()
        self.assertFalse(any(
            call.args[0] == guard.RISK_RECOVER for call in run.call_args_list))

    def test_exit_75_without_state_stays_latched_across_guard_runs(
            self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / "state.json"
            safety_latch = root / "safety-stop.pending.json"

            def metadata(path: object) -> SimpleNamespace:
                candidate = Path(path)
                if candidate == state:
                    raise FileNotFoundError(candidate)
                if candidate == safety_latch and safety_latch.exists():
                    return self.root_metadata()
                raise FileNotFoundError(candidate)

            first_risk = SimpleNamespace(
                returncode=1, stdout="", stderr="recovery deferred\n")
            second_risk = SimpleNamespace(
                returncode=1, stdout="", stderr="recovery deferred\n")
            with mock.patch.object(guard, "STATE", state), \
                    mock.patch.object(guard, "SAFETY_LATCH", safety_latch), \
                    mock.patch.object(
                        guard, "safety_latch_exists",
                        wraps=ORIGINAL_SAFETY_LATCH_EXISTS), \
                    mock.patch.object(guard.os, "lstat", side_effect=metadata), \
                    mock.patch.object(
                        guard.os, "fstat", return_value=self.root_metadata()), \
                    mock.patch.object(guard, "run", side_effect=[
                        SimpleNamespace(
                            returncode=0,
                            stdout=("ActiveState=failed\nResult=exit-code\n"
                                    "ExecMainStatus=75\n"), stderr=""),
                        SimpleNamespace(returncode=0, stdout="", stderr=""),
                        SimpleNamespace(
                            returncode=3, stdout="inactive\n", stderr=""),
                        first_risk,
                        SimpleNamespace(
                            returncode=0,
                            stdout=("ActiveState=inactive\nResult=success\n"
                                    "ExecMainStatus=0\n"), stderr=""),
                        SimpleNamespace(returncode=0, stdout="", stderr=""),
                        SimpleNamespace(
                            returncode=3, stdout="inactive\n", stderr=""),
                        second_risk,
                    ]) as run:
                first = guard.main()
                second = guard.main()
                latch_existed = safety_latch.exists()

        self.assertEqual((first, second), (1, 1))
        self.assertTrue(latch_existed)
        risk_calls = [
            item for item in run.call_args_list
            if item.args and item.args[0][:2] == guard.RISK_RECOVER]
        self.assertEqual(len(risk_calls), 2)
        self.assertTrue(all(
            item.args[0][-1] == "--safety-exit" for item in risk_calls))
        self.assertNotIn(
            guard.NORMAL_RECOVER,
            [item.args[0] for item in run.call_args_list])

    def test_safety_sentinel_routes_unreadable_state_to_risk_recovery(
            self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / "state.json"
            state.write_text("{not-json\n", encoding="ascii")
            safety_latch = root / "safety-stop.pending.json"
            safety_latch.write_text("{}\n", encoding="ascii")

            def metadata(path: object) -> SimpleNamespace:
                if Path(path) in {state, safety_latch}:
                    return self.root_metadata()
                raise FileNotFoundError(path)

            with mock.patch.object(guard, "STATE", state), \
                    mock.patch.object(guard, "SAFETY_LATCH", safety_latch), \
                    mock.patch.object(
                        guard, "safety_latch_exists",
                        wraps=ORIGINAL_SAFETY_LATCH_EXISTS), \
                    mock.patch.object(guard.os, "lstat", side_effect=metadata), \
                    mock.patch.object(guard, "run", side_effect=[
                        SimpleNamespace(
                            returncode=0,
                            stdout=("ActiveState=inactive\nResult=success\n"
                                    "ExecMainStatus=0\n"), stderr=""),
                        SimpleNamespace(returncode=0, stdout="", stderr=""),
                        SimpleNamespace(
                            returncode=3, stdout="inactive\n", stderr=""),
                        SimpleNamespace(
                            returncode=1, stdout="", stderr="deferred\n"),
                    ]) as run:
                result = guard.main()

        self.assertEqual(result, 1)
        self.assertEqual(run.call_args_list[-1], mock.call(
            ["/usr/libexec/hepta-local-paper-repair", "risk-recover",
             "--automatic", "--safety-exit"],
            timeout=guard.RISK_RECOVER_TIMEOUT_SECONDS))
        self.assertNotIn(
            guard.NORMAL_RECOVER,
            [item.args[0] for item in run.call_args_list])

    def test_stale_exit_75_after_reset_does_not_relatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state.json"
            state.write_text(json.dumps({
                "trading_suspended": False,
                "recovery_required": False,
                "pending_order_id": None,
                "auth_generation_rearmed": "auth-generation-new",
            }), encoding="ascii")
            completed = SimpleNamespace(
                returncode=0, stdout="SAFE_RECOVERY_OK\n", stderr="")
            with mock.patch.object(guard, "STATE", state), \
                    mock.patch.object(
                        guard.os, "lstat", return_value=self.root_metadata()), \
                    mock.patch.object(
                        guard, "run", side_effect=[
                            SimpleNamespace(
                                returncode=0,
                                stdout=("ActiveState=inactive\nResult=success\n"
                                        "ExecMainStatus=75\n"), stderr=""),
                            completed,
                        ]) as run:
                result = guard.main()
        self.assertEqual(result, 0)
        self.assertEqual(run.call_args_list[-1], mock.call(
            ["/usr/libexec/hepta-local-paper-safe-recover"],
            timeout=guard.NORMAL_RECOVER_TIMEOUT_SECONDS))

    def test_auth_rearm_requires_separate_manual_start(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state.json"
            state.write_text(json.dumps({
                "trading_suspended": False,
                "recovery_required": False,
                "pending_order_id": None,
                "auth_generation_rearmed": "auth-generation-new",
                "manual_start_required": True,
            }), encoding="ascii")
            with mock.patch.object(guard, "STATE", state), \
                    mock.patch.object(
                        guard.os, "lstat", return_value=self.root_metadata()), \
                    mock.patch.object(guard, "run", side_effect=[
                        SimpleNamespace(
                            returncode=0,
                            stdout=("ActiveState=inactive\nResult=success\n"
                                    "ExecMainStatus=0\n"), stderr=""),
                        SimpleNamespace(returncode=0, stdout="", stderr=""),
                        SimpleNamespace(
                            returncode=3, stdout="inactive\n", stderr=""),
                    ]) as run:
                result = guard.main()
        self.assertEqual(result, 0)
        self.assertEqual(len(run.call_args_list), 3)
        self.assertNotIn(
            guard.NORMAL_RECOVER,
            [item.args[0] for item in run.call_args_list])
        self.assertFalse(any(
            item.args[0][:2] == guard.RISK_RECOVER
            for item in run.call_args_list))

    def test_normal_recovery_cannot_consume_manual_start_marker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state.json"
            state.write_text(json.dumps({
                "manual_start_required": True,
                "auth_generation_rearmed": "auth-generation-new",
            }), encoding="ascii")
            with mock.patch.object(safe_recover, "AGENT_STATE", state), \
                    mock.patch.object(
                        safe_recover.os, "lstat",
                        return_value=self.root_metadata()), \
                    self.assertRaisesRegex(
                        safe_recover.Deferred,
                        "AUTH_REARM_MANUAL_START_REQUIRED"):
                safe_recover.require_no_manual_rearm_start()

    def test_static_boundary_accepts_v5_local_market_campaign(self) -> None:
        now_ms = 1_000_000
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            execution = root / "execution.env"
            gateway = root / "gateway.env"
            agent = root / "agent.env"
            policy = root / "policy.json"
            strategy = root / "strategy.json"
            strategy.write_bytes(b"{\"order_type\":\"MKT\"}\n")
            strategy_sha256 = "sha256:" + hashlib.sha256(
                strategy.read_bytes()).hexdigest()
            execution.write_text(
                "HEPTA_IB_EXECUTION_MODE=PAPER\n"
                "HEPTA_IB_PAPER_PORT=4002\n"
                "HEPTA_IB_PAPER_ACCOUNT=DU12345\n"
                "HEPTA_IB_PAPER_MAX_ORDER_QTY=25000\n"
                "HEPTA_IB_PAPER_MAX_ORDER_NOTIONAL=35000\n"
                "HEPTA_IB_PAPER_MAX_GROSS_POSITION=25000\n",
                encoding="ascii")
            gateway.write_text(
                "HEPTA_TOOL_ACCOUNT=DU12345\n"
                "HEPTA_TOOL_MAX_ORDER_QTY=25000\n", encoding="ascii")
            agent.write_text(
                "HEPTA_LOCAL_AI_CAMPAIGN_ID=campaign-test\n"
                "HEPTA_LOCAL_AI_STRATEGY_ID=strategy-test\n"
                "HEPTA_LOCAL_AI_STRATEGY_VERSION=3\n"
                f"HEPTA_LOCAL_AI_STRATEGY_SHA256={strategy_sha256}\n",
                encoding="ascii")
            value = {
                "schema": safe_recover.ACTIVE_POLICY_SCHEMA,
                "version": 5,
                "admission_mode": "local-only",
                "paper_only": True,
                "live_authorized": False,
                "order_type": "MKT",
                "tif": "DAY",
                "enabled": True,
                "mutations_authorized": True,
                "max_quantity": 25_000,
                "max_active_orders": 1,
                "end_flat_required": True,
                "max_cycles": 720,
                "valid_after_ms": now_ms - 1,
                "expires_at_ms": now_ms + 86_399_999,
                "campaign_id": "campaign-test",
                "strategy_id": "strategy-test",
                "strategy_version": "3",
                "strategy_sha256": strategy_sha256,
            }
            policy.write_text(json.dumps(value), encoding="ascii")
            with mock.patch.multiple(
                    safe_recover,
                    EXECUTION_ENV=execution, GATEWAY_ENV=gateway,
                    AGENT_ENV=agent, POLICY_FILE=policy,
                    STRATEGY_FILE=strategy):
                self.assertEqual(
                    safe_recover.verify_static_boundary(now_ms),
                    value["expires_at_ms"])
                for field, invalid in (
                        ("admission_mode", "external-p1-finalized"),
                        ("order_type", "LMT")):
                    rejected = dict(value)
                    rejected[field] = invalid
                    policy.write_text(json.dumps(rejected), encoding="ascii")
                    with self.subTest(field=field), self.assertRaisesRegex(
                            safe_recover.Deferred,
                            "STATIC_PAPER_BOUNDARY_MISMATCH"):
                        safe_recover.verify_static_boundary(now_ms)
                for invalid in ("720", True, None):
                    rejected = dict(value)
                    rejected["max_cycles"] = invalid
                    policy.write_text(json.dumps(rejected), encoding="ascii")
                    with self.subTest(max_cycles=invalid), \
                            self.assertRaisesRegex(
                                safe_recover.Deferred,
                                "STATIC_PAPER_BOUNDARY_MISMATCH"):
                        safe_recover.verify_static_boundary(now_ms)

    def test_dependency_loss_latches_once_without_reenabling_stack(self) -> None:
        expected = {
            "campaign_id": "campaign-test",
            "execution_service_epoch": "hexec-v6-" + "1" * 32,
            "execution_service_fencing_generation": 7,
            "tool_gateway_epoch": "htgw-v1-" + "2" * 32,
            "tool_session_token_sha256": "sha256:" + "3" * 64,
        }

        def active(unit: str) -> bool:
            return unit in {
                safe_recover.STOP_TIMER, safe_recover.GATEWAY_SERVICE,
            }

        with mock.patch.object(
                safe_recover, "verify_static_boundary",
                return_value=9_999_999_999_999), \
                mock.patch.object(
                    safe_recover, "require_no_manual_rearm_start"), \
                mock.patch.object(safe_recover, "active", side_effect=active), \
                mock.patch.object(
                    safe_recover, "read_agent_state",
                    return_value={"runtime_binding": expected}), \
                mock.patch.object(
                    safe_recover, "read_env", return_value={
                        "HEPTA_LOCAL_AI_CAMPAIGN_ID": "campaign-test",
                    }), \
                mock.patch.object(
                    safe_recover, "persist_runtime_incident",
                    return_value="sha256:" + "4" * 64) as persist, \
                mock.patch.object(safe_recover, "run") as run:
            result = safe_recover.recover_once()

        self.assertEqual(result, 0)
        persist.assert_called_once_with(
            "RUNTIME_BINDING_DEPENDENCY_LOST", expected=expected,
            observed={
                "campaign_id": "campaign-test",
                "execution_service_active": False,
                "tool_gateway_active": True,
            })
        self.assertFalse(any(
            item.args and item.args[0][:2] == [safe_recover.CONTROL, "enable"]
            for item in run.call_args_list))

    def test_epoch_change_stops_agent_and_persists_exact_bindings(self) -> None:
        expected = {
            "campaign_id": "campaign-test",
            "execution_service_epoch": "hexec-v6-" + "1" * 32,
        }
        observed = {
            "campaign_id": "campaign-test",
            "execution_service_epoch": "hexec-v6-" + "2" * 32,
        }

        def active(unit: str) -> bool:
            return unit in {
                safe_recover.STOP_TIMER, safe_recover.AGENT_SERVICE,
                safe_recover.EXECUTION_SERVICE, safe_recover.GATEWAY_SERVICE,
            }

        changed = safe_recover.RuntimeBindingChanged(
            "RUNTIME_BINDING_CHANGED", expected=expected, observed=observed)
        with mock.patch.object(
                safe_recover, "verify_static_boundary",
                return_value=9_999_999_999_999), \
                mock.patch.object(
                    safe_recover, "require_no_manual_rearm_start"), \
                mock.patch.object(safe_recover, "active", side_effect=active), \
                mock.patch.object(
                    safe_recover, "read_agent_state",
                    return_value={"runtime_binding": expected}), \
                mock.patch.object(
                    safe_recover, "runtime_binding", side_effect=changed), \
                mock.patch.object(safe_recover, "stop_agent") as stop, \
                mock.patch.object(
                    safe_recover, "persist_runtime_incident") as persist:
            result = safe_recover.recover_once()

        self.assertEqual(result, 0)
        stop.assert_called_once_with()
        persist.assert_called_once_with(
            "RUNTIME_BINDING_CHANGED", expected=expected,
            observed=observed)

    def test_same_epoch_reconnect_grace_returns_without_incident(self) -> None:
        expected = {
            "campaign_id": "campaign-test",
            "execution_service_epoch": "hexec-v6-" + "1" * 32,
            "execution_service_fencing_generation": 7,
            "tool_gateway_epoch": "htgw-v1-" + "2" * 32,
            "tool_session_token_sha256": "sha256:" + "3" * 64,
        }

        def active(unit: str) -> bool:
            return unit in {
                safe_recover.STOP_TIMER, safe_recover.AGENT_SERVICE,
                safe_recover.EXECUTION_SERVICE, safe_recover.GATEWAY_SERVICE,
            }

        with mock.patch.object(
                safe_recover, "verify_static_boundary",
                return_value=9_999_999_999_999), \
                mock.patch.object(
                    safe_recover, "require_no_manual_rearm_start"), \
                mock.patch.object(safe_recover, "active", side_effect=active), \
                mock.patch.object(
                    safe_recover, "read_agent_state",
                    return_value={"runtime_binding": expected}), \
                mock.patch.object(
                    safe_recover, "runtime_binding",
                    return_value=expected) as binding, \
                mock.patch.object(safe_recover, "stop_agent") as stop, \
                mock.patch.object(
                    safe_recover, "persist_runtime_incident") as persist:
            result = safe_recover.recover_once()

        self.assertEqual(result, 0)
        binding.assert_called_once_with(
            {"runtime_binding": expected}, reconnect_grace=True)
        stop.assert_not_called()
        persist.assert_not_called()

    def test_reconnect_grace_rejects_unsafe_token_without_health_retry(
            self) -> None:
        expected = {
            "campaign_id": "campaign-test",
            "execution_service_epoch": "hexec-v6-" + "1" * 32,
            "execution_service_fencing_generation": 7,
            "tool_gateway_epoch": "htgw-v1-" + "2" * 32,
            "tool_session_token_sha256": "sha256:" + "3" * 64,
        }
        with tempfile.TemporaryDirectory() as directory:
            policy = Path(directory) / "policy.json"
            policy.write_text(json.dumps({
                "campaign_id": "campaign-test",
            }), encoding="ascii")
            with mock.patch.object(safe_recover, "POLICY_FILE", policy), \
                    mock.patch.object(safe_recover, "read_env", return_value={
                        "HEPTA_LOCAL_AI_CAMPAIGN_ID": "campaign-test",
                    }), \
                    mock.patch.object(
                        safe_recover.pwd, "getpwnam",
                        return_value=SimpleNamespace(
                            pw_uid=2104, pw_gid=2104)), \
                    mock.patch.object(
                        safe_recover, "token_metadata_safe",
                        return_value=False), \
                    mock.patch.object(safe_recover, "call_tool") as health, \
                    mock.patch.object(safe_recover.time, "sleep") as sleep, \
                    self.assertRaisesRegex(
                        safe_recover.RuntimeBindingChanged,
                        "RUNTIME_BINDING_SESSION_UNAVAILABLE"):
                safe_recover.runtime_binding(
                    {"runtime_binding": expected}, reconnect_grace=True)
        health.assert_not_called()
        sleep.assert_not_called()

    def test_reconnect_grace_rejects_incomplete_expected_binding_immediately(
            self) -> None:
        incomplete = {
            "campaign_id": "campaign-test",
            "execution_service_epoch": "",
            "execution_service_fencing_generation": 0,
            "tool_gateway_epoch": "htgw-v1-" + "2" * 32,
            "tool_session_token_sha256": "sha256:" + "3" * 64,
        }
        with mock.patch.object(safe_recover, "read_env") as read_env, \
                mock.patch.object(safe_recover.time, "sleep") as sleep, \
                self.assertRaisesRegex(
                    safe_recover.RuntimeBindingChanged,
                    "RUNTIME_BINDING_REQUIRED"):
            safe_recover.runtime_binding(
                {"runtime_binding": incomplete}, reconnect_grace=True)
        read_env.assert_not_called()
        sleep.assert_not_called()

    def test_reconnect_grace_rejects_token_digest_drift_without_health_retry(
            self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            token = root / "session.token"
            token.write_text("a" * 64 + "\n", encoding="ascii")
            policy = root / "policy.json"
            policy.write_text(json.dumps({
                "campaign_id": "campaign-test",
            }), encoding="ascii")
            expected = {
                "campaign_id": "campaign-test",
                "execution_service_epoch": "hexec-v6-" + "1" * 32,
                "execution_service_fencing_generation": 7,
                "tool_gateway_epoch": "htgw-v1-" + "2" * 32,
                "tool_session_token_sha256": "sha256:" + "3" * 64,
            }
            with mock.patch.object(safe_recover, "TOKEN_FILE", token), \
                    mock.patch.object(safe_recover, "POLICY_FILE", policy), \
                    mock.patch.object(safe_recover, "read_env", return_value={
                        "HEPTA_LOCAL_AI_CAMPAIGN_ID": "campaign-test",
                    }), \
                    mock.patch.object(
                        safe_recover.pwd, "getpwnam",
                        return_value=SimpleNamespace(
                            pw_uid=2104, pw_gid=2104)), \
                    mock.patch.object(
                        safe_recover, "token_metadata_safe",
                        return_value=True), \
                    mock.patch.object(safe_recover, "call_tool") as health, \
                    mock.patch.object(safe_recover.time, "sleep") as sleep, \
                    self.assertRaisesRegex(
                        safe_recover.RuntimeBindingChanged,
                        "RUNTIME_BINDING_CHANGED"):
                safe_recover.runtime_binding(
                    {"runtime_binding": expected}, reconnect_grace=True)
        health.assert_not_called()
        sleep.assert_not_called()

    def test_reconnect_grace_rejects_missing_campaign_without_health_retry(
            self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            token = root / "session.token"
            token.write_text("a" * 64 + "\n", encoding="ascii")
            policy = root / "policy.json"
            policy.write_text(json.dumps({
                "campaign_id": "campaign-test",
            }), encoding="ascii")
            expected = {
                "campaign_id": "campaign-test",
                "execution_service_epoch": "hexec-v6-" + "1" * 32,
                "execution_service_fencing_generation": 7,
                "tool_gateway_epoch": "htgw-v1-" + "2" * 32,
                "tool_session_token_sha256": "sha256:" + hashlib.sha256(
                    token.read_bytes()).hexdigest(),
            }
            with mock.patch.object(safe_recover, "TOKEN_FILE", token), \
                    mock.patch.object(safe_recover, "POLICY_FILE", policy), \
                    mock.patch.object(safe_recover, "read_env", return_value={}), \
                    mock.patch.object(
                        safe_recover.pwd, "getpwnam",
                        return_value=SimpleNamespace(
                            pw_uid=2104, pw_gid=2104)), \
                    mock.patch.object(
                        safe_recover, "token_metadata_safe",
                        return_value=True), \
                    mock.patch.object(safe_recover, "call_tool") as health, \
                    mock.patch.object(safe_recover.time, "sleep") as sleep, \
                    self.assertRaisesRegex(
                        safe_recover.RuntimeBindingChanged,
                        "RUNTIME_BINDING_IDENTITY_UNAVAILABLE"):
                safe_recover.runtime_binding(
                    {"runtime_binding": expected}, reconnect_grace=True)
        health.assert_not_called()
        sleep.assert_not_called()

    def test_reconnect_grace_rejects_campaign_mismatch_without_health_retry(
            self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            token = root / "session.token"
            token.write_text("a" * 64 + "\n", encoding="ascii")
            policy = root / "policy.json"
            policy.write_text(json.dumps({
                "campaign_id": "campaign-other",
            }), encoding="ascii")
            expected = {
                "campaign_id": "campaign-test",
                "execution_service_epoch": "hexec-v6-" + "1" * 32,
                "execution_service_fencing_generation": 7,
                "tool_gateway_epoch": "htgw-v1-" + "2" * 32,
                "tool_session_token_sha256": "sha256:" + hashlib.sha256(
                    token.read_bytes()).hexdigest(),
            }
            with mock.patch.object(safe_recover, "TOKEN_FILE", token), \
                    mock.patch.object(safe_recover, "POLICY_FILE", policy), \
                    mock.patch.object(safe_recover, "read_env", return_value={
                        "HEPTA_LOCAL_AI_CAMPAIGN_ID": "campaign-test",
                    }), \
                    mock.patch.object(
                        safe_recover.pwd, "getpwnam",
                        return_value=SimpleNamespace(
                            pw_uid=2104, pw_gid=2104)), \
                    mock.patch.object(
                        safe_recover, "token_metadata_safe",
                        return_value=True), \
                    mock.patch.object(safe_recover, "call_tool") as health, \
                    mock.patch.object(safe_recover.time, "sleep") as sleep, \
                    self.assertRaisesRegex(
                        safe_recover.RuntimeBindingChanged,
                        "RUNTIME_BINDING_CHANGED"):
                safe_recover.runtime_binding(
                    {"runtime_binding": expected}, reconnect_grace=True)
        health.assert_not_called()
        sleep.assert_not_called()

    def test_renewal_failure_token_loss_latches_without_reconnect_grace(
            self) -> None:
        expected = {
            "campaign_id": "campaign-test",
            "execution_service_epoch": "hexec-v6-" + "1" * 32,
            "execution_service_fencing_generation": 7,
            "tool_gateway_epoch": "htgw-v1-" + "2" * 32,
            "tool_session_token_sha256": "sha256:" + "3" * 64,
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            token = root / "renewal-removed.token"
            policy = root / "policy.json"
            policy.write_text(json.dumps({
                "campaign_id": "campaign-test",
            }), encoding="ascii")
            with mock.patch.object(safe_recover, "TOKEN_FILE", token), \
                    mock.patch.object(safe_recover, "POLICY_FILE", policy), \
                    mock.patch.object(
                        safe_recover, "verify_static_boundary",
                        return_value=9_999_999_999_999), \
                    mock.patch.object(
                        safe_recover, "require_no_manual_rearm_start"), \
                    mock.patch.object(
                        safe_recover, "active", return_value=True), \
                    mock.patch.object(
                        safe_recover, "read_agent_state",
                        return_value={"runtime_binding": expected}), \
                    mock.patch.object(safe_recover, "read_env", return_value={
                        "HEPTA_LOCAL_AI_CAMPAIGN_ID": "campaign-test",
                    }), \
                    mock.patch.object(
                        safe_recover.pwd, "getpwnam",
                        return_value=SimpleNamespace(
                            pw_uid=2104, pw_gid=2104)), \
                    mock.patch.object(safe_recover, "call_tool") as health, \
                    mock.patch.object(safe_recover.time, "sleep") as sleep, \
                    mock.patch.object(safe_recover, "stop_agent") as stop, \
                    mock.patch.object(
                        safe_recover, "persist_runtime_incident") as persist:
                result = safe_recover.recover_once()
        self.assertEqual(result, 0)
        health.assert_not_called()
        sleep.assert_not_called()
        stop.assert_called_once_with()
        persist.assert_called_once_with(
            "RUNTIME_BINDING_SESSION_UNAVAILABLE", expected=expected,
            observed={
                "campaign_id": "campaign-test",
                "policy_campaign_id": "campaign-test",
                "tool_session_token_sha256": None,
                "tool_session_token_metadata_safe": False,
            })

    def test_safe_recovery_waits_for_empty_identity_then_exact_recovery(
            self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            token = root / "session.token"
            token.write_text("a" * 64 + "\n", encoding="ascii")
            policy = root / "policy.json"
            policy.write_text(json.dumps({
                "campaign_id": "campaign-test",
            }), encoding="ascii")
            token_sha256 = "sha256:" + hashlib.sha256(
                token.read_bytes()).hexdigest()
            expected = {
                "campaign_id": "campaign-test",
                "execution_service_epoch": "hexec-v6-" + "1" * 32,
                "execution_service_fencing_generation": 7,
                "tool_gateway_epoch": "htgw-v1-" + "2" * 32,
                "tool_session_token_sha256": token_sha256,
            }
            pending = {
                "gateway_ready": True,
                "remote_execution_ready": False,
                "execution_mode": "PAPER",
                "execution_service_epoch": "",
                "execution_service_fencing_generation": 0,
                "tool_gateway_epoch": expected["tool_gateway_epoch"],
            }
            ready = {
                **pending,
                "remote_execution_ready": True,
                "execution_service_epoch":
                    expected["execution_service_epoch"],
                "execution_service_fencing_generation": 7,
            }
            with mock.patch.object(safe_recover, "TOKEN_FILE", token), \
                    mock.patch.object(safe_recover, "POLICY_FILE", policy), \
                    mock.patch.object(safe_recover, "read_env", return_value={
                        "HEPTA_LOCAL_AI_CAMPAIGN_ID": "campaign-test",
                    }), \
                    mock.patch.object(
                        safe_recover.pwd, "getpwnam",
                        return_value=SimpleNamespace(pw_uid=2104, pw_gid=2104)), \
                    mock.patch.object(
                        safe_recover, "token_metadata_safe",
                        return_value=True), \
                    mock.patch.object(
                        safe_recover, "call_tool",
                        side_effect=[pending, ready]) as health, \
                    mock.patch.object(safe_recover.time, "sleep") as sleep:
                observed = safe_recover.runtime_binding(
                    {"runtime_binding": expected}, reconnect_grace=True)
        self.assertEqual(observed, expected)
        self.assertEqual(health.call_count, 2)
        sleep.assert_called_once()

    def test_safe_recovery_nonempty_identity_drift_halts_immediately(
            self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            token = root / "session.token"
            token.write_text("a" * 64 + "\n", encoding="ascii")
            policy = root / "policy.json"
            policy.write_text(json.dumps({
                "campaign_id": "campaign-test",
            }), encoding="ascii")
            expected = {
                "campaign_id": "campaign-test",
                "execution_service_epoch": "hexec-v6-" + "1" * 32,
                "execution_service_fencing_generation": 7,
                "tool_gateway_epoch": "htgw-v1-" + "2" * 32,
                "tool_session_token_sha256": "sha256:" + hashlib.sha256(
                    token.read_bytes()).hexdigest(),
            }
            drifted = {
                "gateway_ready": True,
                "remote_execution_ready": False,
                "execution_mode": "PAPER",
                "execution_service_epoch": "hexec-v6-" + "4" * 32,
                "execution_service_fencing_generation": 7,
                "tool_gateway_epoch": expected["tool_gateway_epoch"],
            }
            with mock.patch.object(safe_recover, "TOKEN_FILE", token), \
                    mock.patch.object(safe_recover, "POLICY_FILE", policy), \
                    mock.patch.object(safe_recover, "read_env", return_value={
                        "HEPTA_LOCAL_AI_CAMPAIGN_ID": "campaign-test",
                    }), \
                    mock.patch.object(
                        safe_recover.pwd, "getpwnam",
                        return_value=SimpleNamespace(pw_uid=2104, pw_gid=2104)), \
                    mock.patch.object(
                        safe_recover, "token_metadata_safe",
                        return_value=True), \
                    mock.patch.object(
                        safe_recover, "call_tool", return_value=drifted), \
                    mock.patch.object(safe_recover.time, "sleep") as sleep, \
                    self.assertRaisesRegex(
                        safe_recover.RuntimeBindingChanged,
                        "RUNTIME_BINDING_CHANGED"):
                safe_recover.runtime_binding(
                    {"runtime_binding": expected}, reconnect_grace=True)
        sleep.assert_not_called()

    def test_safe_recovery_grace_exceeds_execution_reconnect_deadline(
            self) -> None:
        self.assertGreater(
            safe_recover.RUNTIME_RECONNECT_GRACE_SECONDS, 180.0)

    def test_automatic_risk_recovery_is_consumed_after_one_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state.json"
            state.write_text(json.dumps({
                "trading_suspended": True,
                "recovery_required": True,
                "recovery_complete": False,
                "recovery_phase": "REQUESTED",
                "suspension_code": "ORDER_STATE_UNCERTAIN",
            }), encoding="ascii")
            with mock.patch.object(guard, "STATE", state), \
                    mock.patch.object(
                        guard.os, "lstat", return_value=self.root_metadata()), \
                    mock.patch.object(
                        guard, "automatic_risk_recovery_consumed",
                        side_effect=[False, True, True]), \
                    mock.patch.object(guard, "run", side_effect=[
                        SimpleNamespace(
                            returncode=0,
                            stdout=("ActiveState=inactive\nResult=success\n"
                                    "ExecMainStatus=0\n"), stderr=""),
                        SimpleNamespace(returncode=0, stdout="", stderr=""),
                        SimpleNamespace(
                            returncode=3, stdout="inactive\n", stderr=""),
                        SimpleNamespace(
                            returncode=1, stdout="", stderr="deferred\n"),
                        SimpleNamespace(
                            returncode=0,
                            stdout=("ActiveState=inactive\nResult=success\n"
                                    "ExecMainStatus=0\n"), stderr=""),
                        SimpleNamespace(returncode=0, stdout="", stderr=""),
                        SimpleNamespace(
                            returncode=3, stdout="inactive\n", stderr=""),
                    ]) as run:
                first = guard.main()
                second = guard.main()

        self.assertEqual((first, second), (1, 0))
        risk_calls = [
            item for item in run.call_args_list
            if item.args and item.args[0][:2] == guard.RISK_RECOVER]
        self.assertEqual(len(risk_calls), 1)
        self.assertEqual(risk_calls[0].args[0], [
            *guard.RISK_RECOVER, "--automatic",
        ])
        self.assertEqual(
            guard.schedule_terminal_end_flat.call_count, 2)

    def test_terminal_schedule_persists_request_before_start(self) -> None:
        completed = SimpleNamespace(returncode=0, stdout="", stderr="")
        with mock.patch.object(
                guard, "run", return_value=completed) as run:
            ORIGINAL_SCHEDULE_TERMINAL_END_FLAT()
        self.assertEqual(run.call_args_list, [
            mock.call(guard.REQUEST_END_FLAT, timeout=30),
            mock.call([
                "/usr/bin/systemctl", "start", "--no-block",
                guard.END_FLAT_SERVICE,
            ], timeout=30),
        ])

    def test_orphan_schedule_rechecks_under_lifecycle_owner(self) -> None:
        completed = SimpleNamespace(returncode=0, stdout="", stderr="")
        with mock.patch.object(
                guard, "run", return_value=completed) as run:
            self.assertTrue(ORIGINAL_SCHEDULE_ORPHAN_START_END_FLAT())
        self.assertEqual(run.call_args_list, [
            mock.call(guard.REQUEST_ORPHAN_END_FLAT, timeout=60),
            mock.call([
                "/usr/bin/systemctl", "start", "--no-block",
                guard.END_FLAT_SERVICE,
            ], timeout=30),
        ])

    def test_orphan_schedule_does_not_abort_revalidated_active_start(
            self) -> None:
        with mock.patch.object(guard, "run", return_value=SimpleNamespace(
                returncode=3, stdout="revalidated\n", stderr="")) as run:
            self.assertFalse(ORIGINAL_SCHEDULE_ORPHAN_START_END_FLAT())
        run.assert_called_once_with(guard.REQUEST_ORPHAN_END_FLAT, timeout=60)

    def test_single_flight_busy_is_a_noop(self) -> None:
        with mock.patch.object(
                guard, "single_flight",
                return_value=contextlib.nullcontext(False)), \
                mock.patch.object(guard, "recover_once") as recover:
            result = guard.main()
        self.assertEqual(result, 0)
        recover.assert_not_called()

    def test_provision_session_writes_token_before_provisioning(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            token = root / "local-paper.token"
            lease = root / "local-paper.token.lease.json"
            identity = SimpleNamespace(pw_uid=2104, pw_gid=2104)
            provisioned = SimpleNamespace(
                returncode=0,
                stdout=json.dumps({
                    "accepted": True,
                    "lease_generation": 9,
                }),
                stderr="",
            )
            with mock.patch.object(safe_recover, "TOKEN_FILE", token), \
                    mock.patch.object(
                        safe_recover, "SESSION_LEASE_FILE", lease), \
                    mock.patch.object(
                        safe_recover.pwd, "getpwnam",
                        return_value=identity), \
                    mock.patch.object(safe_recover.os, "fchown"), \
                    mock.patch.object(safe_recover.os, "chown"), \
                    mock.patch.object(
                        safe_recover, "token_metadata_safe",
                        return_value=True) as metadata_safe, \
                    mock.patch.object(
                        safe_recover, "session_usable",
                        return_value=True) as session_usable, \
                    mock.patch.object(
                        safe_recover, "run",
                        return_value=provisioned) as run:
                safe_recover.provision_session(
                    int(safe_recover.time.time() * 1000) + 3_600_000)

            token_raw = token.read_text(encoding="ascii")
            lease_payload = json.loads(lease.read_text(encoding="ascii"))
            token_mode = stat.S_IMODE(token.stat().st_mode)

        self.assertRegex(token_raw, r"\A[0-9a-f]{64}\n\Z")
        self.assertEqual(len(token_raw.encode("ascii")), 65)
        self.assertEqual(token_mode, 0o600)
        self.assertEqual(
            lease_payload["token_sha256"],
            "sha256:" + hashlib.sha256(
                token_raw.encode("ascii")).hexdigest())
        self.assertEqual(lease_payload["lease_generation"], 9)
        metadata_safe.assert_called_once_with(2104, 2104)
        session_usable.assert_called_once_with()
        self.assertEqual(run.call_count, 1)
        command = run.call_args.args[0]
        self.assertEqual(command[:4], [
            safe_recover.SESSIONCTL,
            "--socket",
            safe_recover.SUPERVISOR_SOCKET,
            "provision",
        ])
        self.assertIn(str(token), command)
        self.assertIn("2104", command)

    def test_systemd_contract_prevents_restart_and_routes_failure(self) -> None:
        agent_unit = (
            ROOT / "systemd/hepta-local-ai-paper-agent.service"
        ).read_text(encoding="ascii")
        renew_unit = (
            ROOT / "systemd/hepta-local-paper-session-renew.service"
        ).read_text(encoding="ascii")
        recovery_unit = (
            ROOT / "systemd/hepta-local-paper-safe-recover.service"
        ).read_text(encoding="ascii")
        self.assertNotIn("\n[Install]\n", agent_unit)
        self.assertIn("Restart=no", agent_unit)
        self.assertNotIn("RestartPreventExitStatus=", agent_unit)
        self.assertIn(
            "ExecCondition=/usr/libexec/hepta-local-paper-repair "
            "pre-start-guard",
            agent_unit)
        self.assertIn(
            "Requisite=hepta-tool-gateway@alpha.service "
            "hepta-execution-ib-paper@alpha.service "
            "hepta-ib-paper-campaign-operator@alpha.socket "
            "hepta-local-paper-safe-recover.timer "
            "hepta-local-paper-session-renew.timer "
            "hepta-local-paper-supervisor.timer "
            "hepta-local-ai-paper-24h-stop.timer "
            "hepta-local-ai-paper-end-flat-retry.timer",
            agent_unit)
        self.assertIn(
            "InaccessiblePaths=/var/lib/hepta-local-ai-paper-agent/"
            "session-authority",
            agent_unit)
        agent_lines = agent_unit.splitlines()
        self.assertEqual(
            [line for line in agent_lines
             if line.startswith("CapabilityBoundingSet=")],
            ["CapabilityBoundingSet=CAP_CHOWN CAP_DAC_OVERRIDE CAP_FOWNER "
             "CAP_KILL CAP_SETGID CAP_SETUID"])
        for forbidden_capability in (
                "CAP_SYS_ADMIN", "CAP_SYS_PTRACE", "CAP_DAC_READ_SEARCH",
                "CAP_SYS_CHROOT"):
            self.assertNotIn(forbidden_capability, agent_unit)
        self.assertEqual(
            [line for line in agent_lines
             if line.startswith("AmbientCapabilities=")],
            ["AmbientCapabilities="])
        self.assertEqual(
            [line for line in agent_lines
             if line.startswith("RestrictNamespaces=")],
            ["RestrictNamespaces=yes"])
        self.assertEqual(
            [line for line in agent_lines
             if line.startswith("SystemCallFilter=")],
            ["SystemCallFilter=~@mount"])
        self.assertIn(
            "OnFailure=hepta-local-paper-safe-recover.service", agent_unit)
        self.assertIn(
            "OnFailure=hepta-local-paper-safe-recover.service", renew_unit)
        self.assertIn(
            "ReadWritePaths=/var/lib/hepta-local-ai-paper-agent "
            "-/run/hepta-agent-alpha",
            renew_unit)
        self.assertIn(
            "ExecStart=/usr/libexec/hepta-local-paper-safe-recover-guard",
            recovery_unit)
        self.assertIn("TimeoutStartSec=300s", recovery_unit)
        self.assertIn(
            "/var/lib/hepta-local-ai-paper-agent", recovery_unit)
        recovery_timer = (
            ROOT / "systemd/hepta-local-paper-safe-recover.timer"
        ).read_text(encoding="ascii")
        self.assertIn("OnUnitInactiveSec=60s", recovery_timer)
        renew_timer = (
            ROOT / "systemd/hepta-local-paper-session-renew.timer"
        ).read_text(encoding="ascii")
        self.assertIn("OnBootSec=60s", renew_timer)
        self.assertIn("OnUnitInactiveSec=1h", renew_timer)
        self.assertIn("AccuracySec=5s", renew_timer)


if __name__ == "__main__":
    unittest.main(verbosity=2)
