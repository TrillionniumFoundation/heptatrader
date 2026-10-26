"""Gateway fault boundaries using real locks, private files and atomic writes."""
from __future__ import annotations
import contextlib
import copy
import fcntl
import json
import os
from pathlib import Path
import subprocess
import shutil
import sys
import tempfile
import types
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import activate_prepared_gateway as gateway
import test_activate_prepared_driver as driver_tests


def put(path, value, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_bytes(value); path.chmod(mode)
    return path


def record():
    audit = driver_tests.MaintenanceUpgradeTests._accepted_reply("native-audit-original")
    return {"paper": True, "watch": False, "bootstrap_issuer": True, "peer_uid": os.geteuid(),
        "token_sha256": gateway.digest("original-token"), "agent_sha256": gateway.digest(audit["agent_id"]),
        "session_sha256": gateway.digest(audit["session_id"]), "account_sha256": gateway.digest(audit["owner_account"]),
        "domain_sha256": gateway.digest(audit["owner_execution_domain"]), "predecessor_sha256": "",
        "recovery_command_sha256": "", "generation": 112, "expires_at_ms": 1000000 + 86400000,
        "recovery_only": False, "fence_pending": False, "fence_complete": False,
        "finalization_required": False, "finalization_state": 0}


class GatewayUpgradeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="hepta-gateway-transaction-")
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.root = self.home / ".local/state/heptatrader"
        self.root.mkdir(parents=True, mode=0o700)
        self.hold = put(self.root / "operator-maintenance/maintenance-hold.json", b"original-external-hold")
        self.kill = put(self.home / "controls/kill-switch", b"engaged\n", 0o440)
        self.markers = self.marker_identity(self.home)
        self.state_path = self.root / "paper-driver/state.json"
        self.initial_state = {"instrument": "EUR.USD", "position": 0, "pending": None,
            "entries": 2, "exits": 2, "net_pnl": -0.5, "entry_command_id": "original-entry",
            "lease_generation": 112, "lease_last_renew_ms": 10, "lease_uncertain": False,
            "lease_renewal_state": "IDLE", "halt_new_entries": True, "risk_halt_reason": "MANUAL_HOLD",
            "last_reconciliation": {"command_id": "original-close-command"}}
        put(self.state_path, json.dumps(self.initial_state).encode())
        self.lock = put(self.state_path.parent / "driver.lock", b"")
        self.token = put(self.home / ".config/heptatrader/paper.token", b"original-token\n")
        self.driver_unit = gateway.driver.attribution.unit_for("EUR.USD")
        self.units = {}
        self.guards = {}
        for index, unit in enumerate(gateway.driver.fence.RISK_UNITS):
            self.units[unit] = {"LoadState": "loaded", "ActiveState": "active", "SubState": "running",
                "MainPID": str(1000 + index), "InvocationID": "invocation-" + unit,
                "RefuseManualStop": "yes", "PartOf": ""}
            self.guards[unit] = put(self.home / ".config/systemd/user" / (unit + ".d") /
                gateway.driver.fence.GUARD_NAME, gateway.driver.GUARD_YES)
        for unit in ("heptatrader-crypto-driver.service", gateway.driver.BROKER_UNIT):
            self.units[unit].update(ActiveState="inactive", SubState="dead", MainPID="0", InvocationID="")
        self.alive = {s["MainPID"]: (s["MainPID"], "123") for s in self.units.values() if s["MainPID"] != "0"}
        self.clock = 1000.0
        self.events = []
        self.audit_number = 0
        self.last_audit = None
        self.audit_used = set()
        self.resume_count = 0
        self.rows = [record()]
        self.environment = {"HEPTA_TOOL_AGENT_ID": "fixture-agent", "HEPTA_TOOL_ACCOUNT": "DU_FIXTURE",
            "HEPTA_EXECUTION_DOMAIN_ID": "PAPER", "HEPTA_TOOL_SOCKET": "/fixture/tools.sock",
            "HEPTA_TOOL_SUPERVISOR_SOCKET": "/fixture/supervisor.sock"}
        self.plan = {"sessionctl": {"path": "/fixture/sessionctl", "sha256": "a" * 64},
            "reader": {"path": "/fixture/heptactl", "sha256": "b" * 64}, "interactive_broker_pid": 99}
        self.packages = {name: {"binary": {"path": "/fixture/" + name, "sha256": name[0] * 64}}
            for name in ("candidate", "fallback")}
        self.current_binary = {"path": "/fixture/original", "sha256": "0" * 64}
        self.owner = gateway.controller("EUR.USD", self.home)
        self.owner["record"] = copy.deepcopy(self.rows[0])
        self.owner["resident"] = self.running_driver(self.driver_unit, "EUR.USD")
        self.snapshot = {"packages": self.packages, "gateway": self.running_gateway(), "environment": self.environment,
            "statuses": copy.deepcopy(self.units), "owners": [self.owner],
            "frozen": {"heptatrader-crypto-driver.service": dict(self.units["heptatrader-crypto-driver.service"],
                UnitFileState="disabled", ExecStart="original-crypto")}, "records": copy.deepcopy(self.rows),
            "deadline": self.rows[0]["expires_at_ms"], "unit_files": {"fixture": "original-config"},
            "interactive": {"same_broker": True}, "hold_runtime": {"same_execution": True}, "original_release_bytes": None,
            "service_policy": {unit: {"RefuseManualStop": "yes", "SendSIGKILL": "yes", "TimeoutStopUSec": "35s", "Restart": "on-failure"}
                for unit in (gateway.GATEWAY, self.driver_unit)},
            "controller_topology": {self.driver_unit: {"edges": {
                key: "" for key in (*gateway.driver.STOP_EDGES, "Conflicts", "ConflictedBy")}, "shutdown": None}}}
        self.fd = None
        self.acquire_driver()
        self.addCleanup(self.release_driver)
        self.fail_reload = False
        self.candidate_dies = False
        self.stop_pending = False
        self.drift_on_gateway_stop = False
        self.unsafe_audit = False
        self.guard_blocked = True
        self.slow_gateway_reload = 0
        self.audit_latency = 0
        self.bad_stop_edge = False
        self.patches = contextlib.ExitStack(); self.addCleanup(self.patches.close)
        for obj, name, replacement in (
            (gateway, "inspect", self.inspect), (gateway, "catalog", lambda *args: copy.deepcopy(self.rows)),
            (gateway, "unit_files", lambda unit: {"fixture": "original-config"}),
            (gateway, "configured_environment", lambda expected: dict(expected)),
            (gateway, "gateway_environment", lambda pid: dict(self.environment)),
            (gateway, "native_hold_runtime", lambda *args: {"same_execution": True}),
            (gateway, "gateway", self.running_gateway), (gateway, "pinned", lambda item: Path(item["path"])),
            (gateway, "now_ms", lambda: int(self.clock * 1000)),
            (gateway, "time", types.SimpleNamespace(monotonic=lambda: self.clock, sleep=self.sleep)),
            (gateway.driver, "ctl", self.ctl), (gateway.driver, "unit_properties", self.properties),
            (gateway.driver, "running_driver", self.running_driver),
            (gateway.driver, "frozen_driver_snapshot", lambda unit: dict(self.units[unit], UnitFileState="disabled", ExecStart="original-crypto")),
            (gateway.driver, "pid_key", lambda pid: self.alive.get(str(pid))),
            (gateway.driver, "marker_identity", self.marker_identity),
            (gateway.driver, "interactive_broker_snapshot", lambda *args: {"same_broker": True}),
            (gateway.driver.fence, "show_unit", lambda unit: copy.deepcopy(self.units[unit])),
            (gateway.subprocess, "run", self.native_call),
        ):
            self.patches.enter_context(mock.patch.object(obj, name, replacement))

    def inspect(self, plan, home):
        result = copy.deepcopy(self.snapshot)
        result["gateway"] = self.running_gateway()
        result["statuses"] = copy.deepcopy(self.units)
        result["records"] = copy.deepcopy(self.rows)
        refreshed = []
        for old in result["owners"]:
            owner = gateway.controller(old["instrument"], home)
            owner["resident"] = self.running_driver(old["unit"], old["instrument"]) if self.units[old["unit"]]["ActiveState"] == "active" else None
            refreshed.append(owner)
        result["owners"] = refreshed
        gateway.bind_owners(self.rows, result["owners"], self.environment)
        release = self.guards[gateway.GATEWAY].with_name(gateway.RELEASE)
        result["original_release_bytes"] = release.read_text() if release.exists() else None
        return result

    def marker_identity(self, home):
        return tuple((p.stat().st_ino, gateway.digest(p.read_bytes()), p.stat().st_mode) for p in (self.hold, self.kill))

    def acquire_driver(self):
        self.fd = os.open(self.lock, os.O_RDWR)
        fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def release_driver(self):
        if self.fd is not None:
            os.close(self.fd); self.fd = None

    def running_driver(self, unit, instrument):
        value = self.units[unit]
        if value["ActiveState"] != "active":
            raise RuntimeError("DRIVER_NOT_ACTIVE")
        return {"status": copy.deepcopy(value), "pid_key": self.alive[value["MainPID"]],
            "manifest": "/fixture/original-driver/manifest.json", "manifest_sha256": "d" * 64}

    def running_gateway(self, binary=None):
        value = self.units[gateway.GATEWAY]
        if value["ActiveState"] != "active":
            raise RuntimeError("GATEWAY_NOT_RUNNING_WITHOUT_ACTIVATOR")
        if binary is not None:
            self.assertEqual(binary, self.current_binary)
        return {"status": dict(value, NRestarts="0"), "pid_key": self.alive[value["MainPID"]],
            "binary": copy.deepcopy(self.current_binary)}

    def sleep(self, seconds):
        self.clock += seconds

    def policy_path(self, unit):
        return self.guards[unit].with_name(gateway.POLICY)

    def properties(self, unit, names, reason):
        if names == (*gateway.driver.STOP_EDGES, "Conflicts", "ConflictedBy"):
            value = {key: "" for key in names}
            if self.bad_stop_edge and unit == self.driver_unit:
                value["PropagatesStopTo"] = gateway.EXECUTION
            return value
        if names == ("Job",):
            return {"Job": "pending" if self.stop_pending and self.units[unit]["ActiveState"] == "deactivating" else ""}
        path = self.policy_path(unit)
        if not path.exists():
            return {"RefuseManualStop": "yes", "SendSIGKILL": "yes", "TimeoutStopUSec": "35s", "Restart": "on-failure"}
        raw = path.read_bytes()
        return {"RefuseManualStop": "no" if b"RefuseManualStop=no" in raw else "yes",
            "SendSIGKILL": "no", "TimeoutStopUSec": "infinity", "Restart": "no"}

    def ctl(self, *args):
        self.events.append(args)
        if args == ("daemon-reload",):
            if self.fail_reload:
                self.fail_reload = False
                raise RuntimeError("FIXTURE_RELOAD_FAILED")
            for unit, value in self.units.items():
                raw = self.policy_path(unit).read_bytes() if self.policy_path(unit).exists() else b""
                value["RefuseManualStop"] = "no" if b"RefuseManualStop=no" in raw else "yes"
            if self.policy_path(gateway.GATEWAY).exists() and self.policy_path(gateway.GATEWAY).read_bytes() == gateway.SAFE_STOP:
                self.clock += self.slow_gateway_reload
            return
        unit = args[-1]; value = self.units[unit]
        if args[0] == "stop":
            self.assertEqual(args, ("stop", "--no-block", unit))
            self.assertEqual(self.policy_path(unit).read_bytes(), gateway.SAFE_STOP)
            self.assertIsNotNone(self.last_audit)
            self.assertNotIn(self.last_audit, self.audit_used)
            self.audit_used.add(self.last_audit)
            if self.stop_pending:
                value.update(ActiveState="deactivating", SubState="stop-sigterm")
                return
            self.alive.pop(value["MainPID"])
            value.update(ActiveState="inactive", SubState="dead", MainPID="0", InvocationID="")
            if unit == self.driver_unit:
                self.release_driver()
            if unit == gateway.GATEWAY and self.drift_on_gateway_stop:
                extra = copy.deepcopy(self.rows[0]); extra["token_sha256"] = "f" * 64; self.rows.append(extra)
        elif args[0] == "start":
            self.assertEqual(self.policy_path(unit).read_bytes(), gateway.HELD_STOP)
            if unit == gateway.GATEWAY:
                text = self.guards[unit].with_name(gateway.RELEASE).read_text()
                self.current_binary = copy.deepcopy(next(v["binary"] for v in self.packages.values() if v["binary"]["path"] in text))
                if self.current_binary == self.packages["candidate"]["binary"] and self.candidate_dies:
                    value.update(ActiveState="failed", SubState="failed", MainPID="0", InvocationID="")
                    return
            pid = str(9000 + len(self.events)); self.alive[pid] = (pid, "456")
            value.update(ActiveState="active", SubState="running", MainPID=pid, InvocationID="new-" + pid)
            if unit == self.driver_unit:
                self.acquire_driver()
        elif args[0] == "reset-failed":
            self.assertEqual(value["MainPID"], "0"); value.update(ActiveState="inactive", SubState="dead")
        else:
            self.fail("unexpected systemctl operation: " + str(args))

    def native_call(self, argv, **kwargs):
        self.events.append(tuple(argv))
        if "env" in kwargs:
            self.assertLessEqual(set(kwargs["env"]), gateway.driver.AUDIT_ENV)
        token_path = Path(argv[argv.index("--token-file") + 1])
        row = next(r for r in self.rows if r["token_sha256"] == gateway.digest(token_path.read_text().strip()))
        if "owner-audit" in argv:
            self.clock += self.audit_latency
            self.audit_number += 1; self.last_audit = "native-audit-" + str(self.audit_number)
            value = driver_tests.MaintenanceUpgradeTests._accepted_reply(self.last_audit)
            if row["session_sha256"] != gateway.digest("fixture-session"):
                value["session_id"] = "second-session"
            if self.unsafe_audit:
                value["owner_uncertain_command_count"] = 1
        elif "recovery-query" in argv:
            self.assertEqual(argv[argv.index("--command-id") + 1], "original-close-command")
            row.update(recovery_only=True, recovery_command_sha256=gateway.digest("original-close-command"))
            value = {"accepted": True, "recovery_only": True, "authoritative_command_status": True,
                "command_id": "original-close-command", "command_status": "accepted", "lease_generation": row["generation"],
                "owner_audit_authoritative": True, "owner_audit_complete": True,
                "owner_active_order_count": 0, "owner_uncertain_command_count": 0, "paper_finalization_required": False}
        elif "paper-resume" in argv:
            self.resume_count += 1
            ttl = int(argv[argv.index("--ttl-sec") + 1])
            self.assertLessEqual(int(self.clock * 1000) + ttl * 1000, self.snapshot["deadline"])
            row.update(generation=row["generation"] + 1, recovery_only=False, recovery_command_sha256="",
                expires_at_ms=int(self.clock * 1000) + ttl * 1000)
            value = {"accepted": True, "reason_code": "SESSION_OWNER_RESUMED", "lease_generation": row["generation"]}
        else:
            if "system.get_health" in argv:
                value = {"status": "ok", "tool": "system.get_health", "payload": {
                    "execution_service_epoch": "fixture-service-epoch", "execution_service_fencing_generation": 1,
                    "execution_authoritative_health": {"source": "IB", "authoritative": True,
                        "native_new_risk_guard_blocked": self.guard_blocked, "mutation_ready": not self.guard_blocked,
                        "owner_fenced": False, "owner_recovery_only": row["recovery_only"]}}}
                return subprocess.CompletedProcess(argv, 0, json.dumps(value).encode(), b"")
            self.assertIn("execution.get_command_status", argv)
            self.assertIn("command_id=original-close-command", argv)
            self.assertFalse(any(x.startswith("target_command_id=") for x in argv))
            value = {"status": "ok", "tool": "execution.get_command_status", "payload": {
                "authoritative": True, "command_id": "original-close-command", "command_status": "accepted",
                "execution_service_epoch": "fixture-service-epoch", "execution_service_fencing_generation": 1}}
        return subprocess.CompletedProcess(argv, 0, json.dumps(value).encode(), b"")

    def run_upgrade(self, resume=False, plan_sha="a" * 64):
        return gateway.maintenance_upgrade(self.plan, plan_sha, home=self.home, resume=resume)

    def tearDown(self):
        self.assertEqual(self.marker_identity(self.home), self.markers)
        for path in self.guards.values():
            self.assertEqual(path.read_bytes(), gateway.driver.GUARD_YES)
        state = json.loads(self.state_path.read_text())
        for key, value in self.initial_state.items():
            if key not in ("lease_generation", "lease_last_renew_ms"):
                self.assertEqual(state[key], value)

    def test_complete_normal_handoff_preserves_hold_scope_and_business(self):
        result = self.run_upgrade()
        self.assertEqual(result["status"], "COMPLETE", result)
        self.assertEqual(self.resume_count, 1)
        self.assertEqual(json.loads(self.state_path.read_text())["lease_generation"], 113)
        self.assertFalse(any(self.policy_path(unit).exists() for unit in self.units))
        self.assertTrue(all(unit["RefuseManualStop"] == "yes" for unit in self.units.values()))

    def test_resume_success_state_sync_failure_is_idempotent_without_second_resume(self):
        original = gateway.atomic_json
        failed = False
        def write(path, value):
            nonlocal failed
            if Path(path) == self.state_path and not failed:
                failed = True; raise OSError("FIXTURE_STATE_SYNC_FAILED")
            original(path, value)
        with mock.patch.object(gateway, "atomic_json", write):
            result = self.run_upgrade()
        self.assertEqual(result["status"], "HELD", result)
        self.assertEqual(self.resume_count, 1)
        self.assertEqual(json.loads(self.state_path.read_text())["lease_generation"], 112)
        result = self.run_upgrade(resume=True)
        self.assertEqual(result["status"], "COMPLETE", result)
        self.assertEqual(self.resume_count, 1)

    def test_policy_preparation_failure_retry_consumes_new_audit(self):
        self.fail_reload = True
        result = self.run_upgrade()
        self.assertEqual(result["status"], "HELD")
        self.assertFalse(any(e[0] == "stop" for e in self.events))
        audits = self.audit_number
        result = self.run_upgrade(resume=True)
        self.assertEqual(result["status"], "COMPLETE", result)
        self.assertGreater(self.audit_number, audits)

    def test_failed_candidate_no_pid_uses_reviewed_fallback_and_can_resume(self):
        self.candidate_dies = True
        result = self.run_upgrade()
        self.assertEqual(result["status"], "HELD", result)
        self.assertEqual(result["rollback"], "COMPATIBLE_GATEWAY_RUNNING_HOLD_RETAINED", result)
        self.assertEqual(self.current_binary, self.packages["fallback"]["binary"])
        result = self.run_upgrade(resume=True)
        self.assertEqual(result["status"], "COMPLETE", result)

    def test_final_catalog_drift_keeps_hold_and_never_resumes_unknown_owner(self):
        self.drift_on_gateway_stop = True
        result = self.run_upgrade()
        self.assertEqual(result["status"], "HELD", result)
        self.assertIn("FINAL_CATALOG_DRIFT", result["error"])
        self.assertEqual(self.resume_count, 0)

    def test_pending_normal_stop_never_kills_or_starts_replacement(self):
        self.stop_pending = True
        result = self.run_upgrade()
        self.assertEqual(result["status"], "HELD", result)
        self.assertIn("NORMAL_STOP_STILL_PENDING", result["error"])
        self.assertEqual(self.policy_path(self.driver_unit).read_bytes(), gateway.HELD_STOP)
        self.assertFalse(any(e[0] in ("kill", "start") for e in self.events))

    def test_uncertain_native_audit_cannot_be_replaced_with_local_flat(self):
        self.unsafe_audit = True
        result = self.run_upgrade()
        self.assertEqual(result["status"], "HELD")
        self.assertFalse(any(e[0] == "stop" for e in self.events))

    def test_native_hold_requires_runtime_health_observation(self):
        self.guard_blocked = False
        result = self.run_upgrade()
        self.assertEqual(result["status"], "HELD", result)
        self.assertIn("NATIVE_RISK_INCREASE_HOLD_NOT_OBSERVED", result["error"])
        self.assertFalse(any(e[0] == "stop" for e in self.events))

    def test_full_private_policy_is_not_forwarded_to_native_control_children(self):
        self.environment.update(HEPTA_FUTURE_CREDENTIAL="private-policy-value", LD_PRELOAD="/private/library.so")
        result = self.run_upgrade()
        self.assertEqual(result["status"], "COMPLETE", result)
        self.assertNotIn("private-policy-value", json.dumps(result))
        checkpoint = self.root / "operator-maintenance" / gateway.CHECKPOINT
        self.assertEqual(checkpoint.stat().st_mode & 0o777, 0o600)

    def test_unknown_template_refuses_before_any_checkpoint_or_stop(self):
        extra = dict(record(), paper=False, watch=False, token_sha256="c" * 64)
        self.rows.append(extra)
        with self.assertRaisesRegex(RuntimeError, "UNKNOWN_CATALOG_TEMPLATE"):
            self.run_upgrade()
        self.assertFalse(self.events)
        self.assertFalse((self.root / "operator-maintenance" / gateway.CHECKPOINT).exists())

    def test_duplicate_controller_token_refuses(self):
        with self.assertRaisesRegex(RuntimeError, "DUPLICATE_CONTROLLER_TOKEN"):
            gateway.bind_owners(self.rows, [self.owner, self.owner], self.environment)

    def test_resume_deadline_must_outlive_existing_four_hour_renewal_interval(self):
        for ttl in (120000, 4 * 60 * 60 * 1000, gateway.MIN_TTL_MS - 1):
            row = dict(record(), expires_at_ms=int(self.clock * 1000) + ttl)
            with self.assertRaisesRegex(RuntimeError, "OWNER_SCOPE_OR_TTL_NOT_SAFE"):
                gateway.bind_owners([row], [copy.deepcopy(self.owner)], self.environment)

    def test_all_owners_audits_must_remain_fresh_after_slow_gateway_policy_reload(self):
        unit = "heptatrader-crypto-driver.service"
        self.units[unit].update(ActiveState="active", SubState="running", MainPID="7000", InvocationID="crypto-original")
        self.alive["7000"] = ("7000", "123")
        path = self.root / "crypto-driver/state.json"
        put(path, json.dumps(dict(self.initial_state, instrument="BTC.USD")).encode())
        put(path.parent / "driver.lock", b"")
        put(self.home / ".config/heptatrader/crypto.token", b"second-token\n")
        other = gateway.controller("BTC.USD", self.home)
        other["resident"] = self.running_driver(unit, "BTC.USD")
        row = dict(record(), token_sha256=gateway.digest("second-token"), session_sha256=gateway.digest("second-session"))
        other["record"] = copy.deepcopy(row)
        self.rows.append(row)
        self.snapshot["owners"].append(other); self.snapshot["records"] = copy.deepcopy(self.rows)
        self.snapshot["statuses"] = copy.deepcopy(self.units); self.snapshot["frozen"] = {}
        self.snapshot["service_policy"][unit] = dict(self.snapshot["service_policy"][self.driver_unit])
        self.audit_latency = 1
        self.slow_gateway_reload = 4.5
        result = self.run_upgrade()
        self.assertEqual(result["status"], "HELD", result)
        self.assertIn("FRESH_STOP_AUDIT_EXPIRED", result["error"])
        self.assertFalse(any(e[0] == "stop" and e[-1] == gateway.GATEWAY for e in self.events))

    def test_controller_started_before_checkpoint_is_adopted_only_from_exact_start_intent(self):
        original = gateway.atomic_json
        durable_before_start = None
        failed = False
        def write(path, value):
            nonlocal durable_before_start, failed
            if Path(path).name == gateway.CHECKPOINT and value.get("phase") == "CONTROLLER_START_REQUESTED":
                original(path, value); durable_before_start = Path(path).read_bytes(); return
            if Path(path).name == gateway.CHECKPOINT and value.get("phase") == "CONTROLLER_STARTED" and not failed:
                failed = True; raise OSError("FIXTURE_CRASH_AFTER_START")
            original(path, value)
        with mock.patch.object(gateway, "atomic_json", write):
            result = self.run_upgrade()
        self.assertEqual(result["status"], "HELD", result)
        # Restore the LAST durable checkpoint to model abrupt process loss,
        # which cannot execute Python's exception handler/checkpoint write.
        checkpoint = self.root / "operator-maintenance" / gateway.CHECKPOINT
        checkpoint.write_bytes(durable_before_start)
        starts = self.events.count(("start", self.driver_unit))
        result = self.run_upgrade(resume=True)
        self.assertEqual(result["status"], "COMPLETE", result)
        self.assertEqual(self.events.count(("start", self.driver_unit)), starts)
        self.assertEqual(self.resume_count, 1)

    def test_gateway_started_before_checkpoint_is_recognized_without_second_start(self):
        original = gateway.atomic_json
        last_durable = None
        failed = False
        def write(path, value):
            nonlocal last_durable, failed
            if Path(path).name == gateway.CHECKPOINT and value.get("phase") == "GATEWAY_STARTED" and not failed:
                last_durable = Path(path).read_bytes(); failed = True
                raise OSError("FIXTURE_CRASH_BEFORE_GATEWAY_CHECKPOINT")
            original(path, value)
        with mock.patch.object(gateway, "atomic_json", write):
            result = self.run_upgrade()
        self.assertEqual(result["status"], "HELD", result)
        checkpoint = self.root / "operator-maintenance" / gateway.CHECKPOINT
        checkpoint.write_bytes(last_durable)
        starts = self.events.count(("start", gateway.GATEWAY))
        result = self.run_upgrade(resume=True)
        self.assertEqual(result["status"], "COMPLETE", result)
        self.assertEqual(self.events.count(("start", gateway.GATEWAY)), starts)
        self.assertEqual(self.resume_count, 1)

    def test_committed_recovery_query_is_not_replayed_after_checkpoint_interruption(self):
        original = gateway.atomic_json
        durable_before_fence = None
        failed = False
        def write(path, value):
            nonlocal durable_before_fence, failed
            if Path(path).name == gateway.CHECKPOINT and value.get("phase") == "OWNERS_FENCED" and not failed:
                durable_before_fence = Path(path).read_bytes(); failed = True
                raise OSError("FIXTURE_CRASH_AFTER_NATIVE_FENCE")
            original(path, value)
        with mock.patch.object(gateway, "atomic_json", write):
            result = self.run_upgrade()
        self.assertEqual(result["status"], "HELD", result)
        before = sum("recovery-query" in event for event in self.events)
        checkpoint = self.root / "operator-maintenance" / gateway.CHECKPOINT
        checkpoint.write_bytes(durable_before_fence)
        result = self.run_upgrade(resume=True)
        self.assertEqual(result["status"], "COMPLETE", result)
        self.assertEqual(sum("recovery-query" in event for event in self.events), before)

    def test_authenticated_reread_cannot_rebind_original_token_to_new_session(self):
        row = dict(self.rows[0], session_sha256="e" * 64)
        with self.assertRaisesRegex(RuntimeError, "ORIGINAL_OWNER_SCOPE_CHANGED"):
            gateway.bind_owners([row], [copy.deepcopy(self.owner)], self.environment)

    def test_completed_same_plan_is_read_only_and_next_plan_archives_prior_receipt(self):
        self.assertEqual(self.run_upgrade()["status"], "COMPLETE")
        before = list(self.events)
        checkpoint = self.root / "operator-maintenance" / gateway.CHECKPOINT
        completed = checkpoint.read_bytes()
        self.assertEqual(self.run_upgrade()["status"], "ALREADY_COMPLETE")
        self.assertEqual(self.events, before)
        self.assertEqual(checkpoint.read_bytes(), completed)
        self.packages["candidate"]["binary"] = {"path": "/fixture/candidate-next", "sha256": "e" * 64}
        result = self.run_upgrade(plan_sha="b" * 64)
        self.assertEqual(result["status"], "COMPLETE", result)
        archive = checkpoint.with_name("gateway-upgrade-" + gateway.digest(completed) + ".json")
        self.assertEqual(archive.read_bytes(), completed)
        self.assertEqual(self.resume_count, 2)

    def test_controller_configuration_drift_refuses_before_stop(self):
        self.snapshot["controller_files"] = {self.driver_unit: {"environment": "original"}}
        result = self.run_upgrade()
        self.assertEqual(result["status"], "HELD", result)
        self.assertIn("ORIGINAL_CONTROLLER_CONFIG_CHANGED", result["error"])
        self.assertFalse(any(e[0] == "stop" for e in self.events))

    def test_each_controller_stop_topology_uses_existing_broker_guard_before_stop(self):
        self.bad_stop_edge = True
        result = self.run_upgrade()
        self.assertEqual(result["status"], "HELD", result)
        self.assertIn("STOP_PROPAGATION", result["error"])
        self.assertFalse(any(e[0] == "stop" for e in self.events))

    def test_only_known_expired_watch_may_disappear(self):
        watch = dict(record(), paper=False, watch=True, token_sha256="e" * 64, expires_at_ms=1)
        self.assertEqual(gateway.comparable_catalog([self.rows[0], watch], self.rows), self.rows)
        active = dict(watch, expires_at_ms=int(self.clock * 1000) + 60000)
        self.assertIn(active, gateway.comparable_catalog([active], []))


class GatewayProcessAndEnvironmentTests(unittest.TestCase):
    def test_unit_without_environment_file_accepts_actual_omitted_empty_property_output(self):
        with tempfile.TemporaryDirectory(prefix="hepta-gateway-empty-envfile-") as name:
            path = put(Path(name) / "driver.service", b"[Service]\nExecStart=/bin/true\n")
            def output(argv, **kwargs):
                if "--value" in argv:
                    self.assertEqual(argv[-2:], ["-p", "EnvironmentFiles"])
                    return ""  # Actual empty-array output from this host.
                return "FragmentPath=" + str(path) + "\nDropInPaths=\n"
            with mock.patch.object(gateway.subprocess, "check_output", output):
                files = gateway.unit_files("heptatrader-paper-driver.service")
            self.assertEqual(files[str(path)]["sha256"], gateway.digest(path.read_bytes()))

    def test_optional_environment_file_does_not_relax_required_unit_properties(self):
        for required in ("FragmentPath=/fixture/unit.service\n", "DropInPaths=\n"):
            with self.subTest(required=required):
                with mock.patch.object(gateway.subprocess, "check_output", return_value=required):
                    with self.assertRaisesRegex(RuntimeError, "INCOMPLETE_GATEWAY_CONFIG_READBACK"):
                        gateway.unit_files("heptatrader-paper-driver.service")

    def test_optional_environment_file_read_error_is_not_guessed_empty(self):
        with mock.patch.object(gateway.driver, "unit_properties", return_value={
                "FragmentPath": "/fixture/unit.service", "DropInPaths": ""}), mock.patch.object(
                gateway.subprocess, "check_output", side_effect=subprocess.CalledProcessError(1, ["systemctl"])):
            with self.assertRaises(subprocess.CalledProcessError):
                gateway.unit_files("heptatrader-paper-driver.service")

    def test_actual_running_image_cannot_be_replaced_by_matching_disk_pin(self):
        with tempfile.TemporaryDirectory(prefix="hepta-gateway-image-") as name:
            path = Path(name) / "cat"
            shutil.copyfile("/usr/bin/cat", path); path.chmod(0o500)
            process = subprocess.Popen([str(path)], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
            try:
                status = {"LoadState": "loaded", "ActiveState": "active", "SubState": "running", "MainPID": str(process.pid),
                    "InvocationID": "actual-local-process", "RefuseManualStop": "yes", "PartOf": "",
                    "NRestarts": "0", "TriggeredBy": "", "ExecStart": "argv[]=" + str(path) + " ;"}
                with mock.patch.object(gateway.driver, "unit_properties", return_value=status), mock.patch.object(
                        gateway.driver.fence, "show_unit", return_value=status):
                    pin = {"path": str(path), "sha256": gateway.digest(path.read_bytes())}
                    gateway.gateway(pin)
                    replacement = Path(name) / "replacement"
                    shutil.copyfile("/usr/bin/true", replacement); replacement.chmod(0o500)
                    replacement.replace(path)
                    pin["sha256"] = gateway.digest(path.read_bytes())
                    with self.assertRaisesRegex(RuntimeError, "INTERACTIVE_PROCESS_EXE_INVALID|GATEWAY_RUNNING_IMAGE_MISMATCH"):
                        gateway.gateway(pin)
            finally:
                process.stdin.close(); process.wait(timeout=3); process.stdout.close()

    def test_running_scope_must_match_real_next_environment_file(self):
        with tempfile.TemporaryDirectory(prefix="hepta-gateway-environment-") as name:
            path = Path(name) / "gateway.env"
            expected = {"HEPTA_TOOL_ACCOUNT": "original-account", "HEPTA_TOOL_SOCKET": "/original/tools.sock"}
            put(path, b"HEPTA_TOOL_ACCOUNT=original-account\nHEPTA_TOOL_SOCKET=/original/tools.sock\n")
            props = {"Environment": "", "EnvironmentFiles": str(path) + " (ignore_errors=no)", "UnsetEnvironment": ""}
            with mock.patch.object(gateway.driver, "unit_properties", return_value=props), mock.patch.object(
                    gateway.subprocess, "check_output", return_value=""):
                self.assertEqual(gateway.configured_environment(expected), expected)
                path.write_text("HEPTA_TOOL_ACCOUNT=changed-account\nHEPTA_TOOL_SOCKET=/original/tools.sock\n")
                with self.assertRaisesRegex(RuntimeError, "DIFFERS_FROM_NEXT_CONFIGURED_ENVIRONMENT"):
                    gateway.configured_environment(expected)

    def test_gateway_capture_keeps_all_policy_and_loader_presence(self):
        values = {"HEPTA_TOOL_SUPERVISOR_SOCKET": "/supervisor", "HEPTA_TOOL_SOCKET": "/tools",
            "HEPTA_TOOL_AGENT_ID": "agent", "HEPTA_TOOL_ACCOUNT": "account",
            "HEPTA_TOOL_SUPERVISOR_LEASE_STORE": "/store", "HEPTA_IB_PAPER_USER_SCOPE": "1",
            "HEPTA_EXECUTION_REMOTE_MODE": "PAPER", "HEPTA_FUTURE_POLICY": "default",
            "LD_LIBRARY_PATH": "/private/lib", "LD_PRELOAD": "", "LD_AUDIT": "/private/audit.so",
            "CREDENTIALS_DIRECTORY": "/credentials"}
        raw = b"\0".join((key + "=" + value).encode() for key, value in dict(values, UNRELATED="not-captured").items()) + b"\0"
        with mock.patch.object(gateway.driver, "proc_bytes", return_value=raw):
            self.assertEqual(gateway.gateway_environment(123), values)

    def check_environment_change_refuses(self, expected, changed, *, manager="", unset="", reason="DIFFERS_FROM_NEXT_CONFIGURED_ENVIRONMENT"):
        with tempfile.TemporaryDirectory(prefix="hepta-gateway-policy-") as name:
            path = Path(name) / "gateway.env"
            put(path, changed.encode())
            props = {"Environment": "", "EnvironmentFiles": str(path) + " (ignore_errors=no)", "UnsetEnvironment": unset}
            with mock.patch.object(gateway.driver, "unit_properties", return_value=props), mock.patch.object(
                    gateway.subprocess, "check_output", return_value=manager):
                with self.assertRaisesRegex(RuntimeError, reason):
                    gateway.configured_environment(expected)

    def test_old_runtime_unset_but_file_adds_explicit_policy_or_loader_value_refuses(self):
        for field in ("HEPTA_TOOL_MAX_ORDER_QTY=25000", "HEPTA_FUTURE_POLICY=", "LD_PRELOAD=", "LD_LIBRARY_PATH=/lib", "LD_AUDIT=/private/audit.so"):
            with self.subTest(field=field):
                self.check_environment_change_refuses({}, field + "\n")

    def test_existing_runtime_policy_value_changed_refuses(self):
        self.check_environment_change_refuses({"HEPTA_TOOL_MAX_ORDER_QTY": "25000"}, "HEPTA_TOOL_MAX_ORDER_QTY=100000\n")

    def test_existing_runtime_policy_deleted_or_explicitly_unset_refuses(self):
        expected = {"HEPTA_TOOL_MAX_ORDER_QTY": "25000"}
        self.check_environment_change_refuses(expected, "# deleted\n")
        self.check_environment_change_refuses(expected, "HEPTA_TOOL_MAX_ORDER_QTY=25000\n", unset="HEPTA_TOOL_MAX_ORDER_QTY")

    def test_manager_addition_is_also_part_of_next_policy_presence(self):
        self.check_environment_change_refuses({}, "# no unit value\n", manager="HEPTA_NEW_POLICY=enabled\n")

    def test_inline_hash_is_literal_and_cannot_false_pass_old_runtime(self):
        for suffix in ('#changed', '\u00a0', '\u2028'):
            with self.subTest(suffix=suffix):
                self.check_environment_change_refuses({"HEPTA_TOOL_AGENT_ID": "original"},
                    "HEPTA_TOOL_AGENT_ID=original" + suffix + "\n")

    def test_embedded_quotes_backslash_and_continuations_are_refused(self):
        for value in ('abc"def"', "abc'def'", r"abc\def", "abc\\"):
            with self.subTest(value=value):
                self.check_environment_change_refuses({"HEPTA_TOOL_AGENT_ID": "abcdef"},
                    "HEPTA_TOOL_AGENT_ID=" + value + "\n", reason="UNSUPPORTED_GATEWAY_ENVIRONMENT_FILE_SYNTAX")

    def test_plain_and_whole_quoted_environment_file_subset_preserves_literal_values(self):
        with tempfile.TemporaryDirectory(prefix="hepta-gateway-plain-environment-") as name:
            path = Path(name) / "gateway.env"
            expected = {"HEPTA_TOOL_AGENT_ID": "ordinary#literal;kept", "HEPTA_TOOL_MAX_ORDER_QTY": "100000",
                "HEPTA_TOOL_CONTRACT_BINDINGS": "EUR.USD|EUR|CASH|IDEALPRO|USD;BTC.USD|BTC|CRYPTO|PAXOS|USD",
                "CREDENTIALS_DIRECTORY": "/private/credentials"}
            put(path, ("  # leading comment\n ; another leading comment\n"
                " HEPTA_TOOL_AGENT_ID = ordinary#literal;kept \nHEPTA_TOOL_MAX_ORDER_QTY=\"100000\"\n"
                "HEPTA_TOOL_CONTRACT_BINDINGS='" + expected["HEPTA_TOOL_CONTRACT_BINDINGS"] + "'\n"
                "CREDENTIALS_DIRECTORY='/private/credentials'\n").encode())
            props = {"Environment": "", "EnvironmentFiles": str(path) + " (ignore_errors=no)", "UnsetEnvironment": ""}
            with mock.patch.object(gateway.driver, "unit_properties", return_value=props), mock.patch.object(
                    gateway.subprocess, "check_output", return_value=""):
                self.assertEqual(gateway.configured_environment(expected), expected)


if __name__ == "__main__":
    unittest.main()
