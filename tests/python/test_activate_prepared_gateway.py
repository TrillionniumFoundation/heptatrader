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
import traceback
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

    def enable_audit_fixture(self):
        # This suite already injects an accepted inspect result. Qualification
        # parsing and real reader behavior have independent tests.
        self.audit_path = put(self.root / "gateway/audit", b'{"retained_legacy_fixture":true}\n')
        self.audit_original = self.audit_path.read_bytes()
        self.environment["HEPTA_TOOL_SUPERVISOR_AUDIT_JOURNAL"] = str(self.audit_path)
        self.plan["audit_seal"] = {"path": str(self.audit_path), "qualification": {"fixture": True}}
        self.snapshot["audit_seal"] = {"path": str(self.audit_path), "sessionctl": self.plan["sessionctl"]}
        self.audit_cut = None
        self.audit_seals = self.audit_verifies = 0
        self.patches.enter_context(mock.patch.object(gateway, "audit_seal_configuration",
            side_effect=lambda *args: copy.deepcopy(self.snapshot["audit_seal"]), create=True))
        self.patches.enter_context(mock.patch.object(gateway.subprocess, "run", self.audit_native_call))

    def audit_native_call(self, argv, **kwargs):
        if len(argv) != 3 or argv[1] not in ("--audit-seal-stopped", "--audit-verify"):
            return self.native_call(argv, **kwargs)
        self.events.append(tuple(argv))
        self.assertEqual(kwargs["env"], {})
        self.assertEqual(self.units[gateway.GATEWAY]["ActiveState"], "inactive")
        self.assertEqual(self.units[self.driver_unit]["ActiveState"], "inactive")
        if argv[1] == "--audit-verify":
            self.audit_verifies += 1
            if self.audit_cut == "verify":
                return subprocess.CompletedProcess(argv, 1, b"", b"FIXTURE_VERIFY_REFUSED\n")
            return subprocess.CompletedProcess(argv, 0, b"audit verified records=0\n", b"")
        self.audit_seals += 1
        checkpoint = json.loads((self.hold.parent / gateway.CHECKPOINT).read_text())
        self.assertEqual(checkpoint["audit_seal"]["status"], "UNKNOWN")
        raw = self.audit_path.read_bytes()
        if raw and not raw.startswith(b"HJA3\t"):
            archive = Path(str(self.audit_path) + ".segments")
            archive.mkdir(mode=0o700, exist_ok=True)
            segment = archive / (gateway.digest(raw) + ".hja2")
            if not segment.exists():
                put(segment, raw)
            if self.audit_cut == "segment":
                raise subprocess.TimeoutExpired("private-audit-fixture", 1)
            anchor = b"HJA3\t" + gateway.digest(raw).encode() + b"\t0\n"
            staged = put(self.audit_path.with_name(".anchor-fixture"), anchor)
            os.replace(staged, self.audit_path)
            if self.audit_cut == "anchor":
                raise subprocess.TimeoutExpired("private-audit-fixture", 1)
        return subprocess.CompletedProcess(argv, 0, b"audit sealed\n", b"")

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

    def test_gateway_socket_startup_wait_keeps_one_fence_start_and_resume(self):
        pending_reads = 0
        def native(argv, **kwargs):
            nonlocal pending_reads
            result = self.native_call(argv, **kwargs)
            if ("system.get_health" in argv and self.current_binary == self.packages["candidate"]["binary"]
                    and pending_reads < 2):
                pending_reads += 1
                return subprocess.CompletedProcess(argv, 4, b"", b"SOCKET_CONNECT_FAILED\n")
            return result
        with mock.patch.object(gateway.subprocess, "run", native):
            result = self.run_upgrade()
        self.assertEqual(result["status"], "COMPLETE", result)
        self.assertEqual(pending_reads, 2)
        self.assertEqual(self.resume_count, 1)
        self.assertEqual(sum("recovery-query" in event for event in self.events), 1)
        self.assertEqual(self.events.count(("start", gateway.GATEWAY)), 1)
        self.assertEqual(self.events.count(("stop", "--no-block", gateway.GATEWAY)), 1)
        self.assertFalse(any(event[0] in ("start", "stop") and event[-1] in
            (gateway.EXECUTION, gateway.driver.BROKER_UNIT) for event in self.events))

    def test_gateway_socket_timeout_keeps_verified_seal_and_same_plan_resume_does_not_replay(self):
        self.enable_audit_fixture()
        def native(argv, **kwargs):
            result = self.audit_native_call(argv, **kwargs)
            if "system.get_health" in argv and self.current_binary == self.packages["candidate"]["binary"]:
                return subprocess.CompletedProcess(argv, 4, b"", b"SOCKET_CONNECT_FAILED\n")
            return result
        with mock.patch.object(gateway, "GATEWAY_READINESS_TIMEOUT_SECONDS", 0.45):
            with mock.patch.object(gateway.subprocess, "run", native):
                result = self.run_upgrade()
        self.assertEqual(result["status"], "HELD", result)
        self.assertEqual(result["phase"], "GATEWAY_STARTED")
        self.assertIn("GATEWAY_READINESS_TIMEOUT", result["error"])
        self.assertEqual(result["audit_seal"]["status"], "VERIFIED")
        self.assertEqual(self.resume_count, 0)
        self.assertEqual(self.audit_seals, 1)
        checkpoint_path = self.hold.parent / gateway.CHECKPOINT
        before = json.loads(checkpoint_path.read_text())
        controls = [event for event in self.events if event[0] in ("start", "stop")]
        fences = sum("recovery-query" in event for event in self.events)
        result = self.run_upgrade(resume=True)
        self.assertEqual(result["status"], "COMPLETE", result)
        after = json.loads(checkpoint_path.read_text())
        self.assertEqual(after["audit_seal"], before["audit_seal"])
        self.assertEqual(after["gateway_started"], before["gateway_started"])
        # The driver starts once after readiness; no Gateway/Broker/Execution
        # stop/start, original owner fence or offline seal is repeated.
        remaining_controls = [event for event in self.events if event[0] in ("start", "stop")]
        self.assertEqual(remaining_controls, controls + [("start", self.driver_unit)])
        self.assertEqual(sum("recovery-query" in event for event in self.events), fences)
        self.assertEqual(self.audit_seals, 1)
        self.assertEqual(self.resume_count, 1)

    def test_audit_same_binary_maintenance_and_complete_reentry_preserve_producer(self):
        self.enable_audit_fixture()
        for package in self.packages.values():
            package["binary"] = copy.deepcopy(self.current_binary)
        result = self.run_upgrade()
        self.assertEqual(result["status"], "COMPLETE", result)
        self.assertEqual(result["audit_seal"]["status"], "VERIFIED")
        self.assertEqual(self.audit_seals, 1)
        checkpoint = self.hold.parent / gateway.CHECKPOINT
        completed = checkpoint.read_bytes()
        calls = len(self.events)
        self.assertEqual(self.run_upgrade()["status"], "ALREADY_COMPLETE")
        self.assertEqual(checkpoint.read_bytes(), completed)
        self.assertEqual(len(self.events), calls)
        self.assertFalse(any(event[0] in ("stop", "start") and event[-1] in
            (gateway.EXECUTION, gateway.driver.BROKER_UNIT) for event in self.events))
        self.assertEqual(self.resume_count, 1)

    def test_audit_unknown_anchor_stops_both_start_paths_and_same_plan_recovers(self):
        self.enable_audit_fixture()
        self.audit_cut = "anchor"
        result = self.run_upgrade()
        self.assertEqual(result["status"], "HELD", result)
        self.assertEqual(result["phase"], "GATEWAY_STOPPED")
        self.assertEqual(self.resume_count, 0)
        self.assertFalse(any(event[0] == "start" for event in self.events))
        checkpoint = json.loads((self.hold.parent / gateway.CHECKPOINT).read_text())
        intent = checkpoint["audit_seal"]["intent"]
        self.audit_cut = None
        result = self.run_upgrade(resume=True)
        self.assertEqual(result["status"], "COMPLETE", result)
        checkpoint = json.loads((self.hold.parent / gateway.CHECKPOINT).read_text())
        self.assertEqual(checkpoint["audit_seal"]["intent"], intent)
        self.assertEqual(self.audit_seals, 2)
        self.assertEqual(self.resume_count, 1)

    def test_audit_receipt_directory_sync_failure_cannot_trigger_fallback_start(self):
        self.enable_audit_fixture()
        original = gateway.atomic_json
        def write(path, value):
            original(path, value)
            if Path(path).name == gateway.CHECKPOINT and value.get("audit_seal", {}).get("status") == "VERIFIED":
                raise OSError("FIXTURE_CHECKPOINT_FSYNC_FAILED")
        with mock.patch.object(gateway, "atomic_json", write):
            result = self.run_upgrade()
        self.assertEqual(result["status"], "HELD", result)
        self.assertEqual(result["audit_seal"]["status"], "UNKNOWN")
        self.assertFalse(any(event[0] == "start" for event in self.events))
        self.assertEqual(self.resume_count, 0)
        self.assertEqual(self.run_upgrade(resume=True)["status"], "COMPLETE")
        self.assertEqual(self.audit_seals, 2)
        self.assertEqual(self.resume_count, 1)

    def test_audit_native_verify_failure_keeps_original_writer_stopped(self):
        self.enable_audit_fixture()
        self.audit_cut = "verify"
        result = self.run_upgrade()
        self.assertEqual(result["status"], "HELD", result)
        self.assertIn("AUDIT_SEAL_VERIFY_NOT_CONFIRMED", result["error"])
        self.assertEqual(self.units[gateway.GATEWAY]["ActiveState"], "inactive")
        self.assertFalse(any(event[0] == "start" for event in self.events))
        self.assertEqual(self.resume_count, 0)

    def test_audit_fallback_start_lost_checkpoint_recovers_from_original_release_bytes(self):
        self.enable_audit_fixture()
        self.candidate_dies = True
        original = gateway.atomic_json
        def interrupt(path, value):
            started = value.get("gateway_started") or {}
            if (Path(path).name == gateway.CHECKPOINT and value.get("status") == "HELD"
                    and started.get("binary") == self.packages["fallback"]["binary"]):
                raise OSError("FIXTURE_AFTER_FALLBACK_START_BEFORE_CHECKPOINT")
            original(path, value)
        with mock.patch.object(gateway, "atomic_json", interrupt):
            with self.assertRaisesRegex(OSError, "AFTER_FALLBACK_START"):
                self.run_upgrade()
        self.assertEqual(self.current_binary, self.packages["fallback"]["binary"])
        checkpoint = json.loads((self.hold.parent / gateway.CHECKPOINT).read_text())
        self.assertEqual(checkpoint["selected_release"], "candidate")
        self.assertIn(self.packages["fallback"]["binary"]["path"], checkpoint["release_bytes"])
        starts = sum(event == ("start", gateway.GATEWAY) for event in self.events)
        result = self.run_upgrade(resume=True)
        self.assertEqual(result["status"], "COMPLETE", result)
        self.assertEqual(result["selected_release"], "fallback")
        self.assertEqual(sum(event == ("start", gateway.GATEWAY) for event in self.events), starts)
        self.assertEqual(self.audit_seals, 1)
        self.assertEqual(self.resume_count, 1)

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
        reads = 0
        def observe(unit, instrument):
            nonlocal reads
            if unit == self.driver_unit:
                reads += 1
                if reads <= 2:
                    raise RuntimeError("RUNNING_DRIVER_ARGV_MISMATCH")
            return self.running_driver(unit, instrument)
        with mock.patch.object(gateway.driver, "running_driver", observe):
            result = self.run_upgrade(resume=True)
        self.assertEqual(result["status"], "COMPLETE", result)
        self.assertEqual(self.events.count(("start", self.driver_unit)), starts)
        self.assertEqual(self.resume_count, 1)
        self.assertGreater(reads, 2)

    def test_original_controller_start_waits_for_initial_exec_without_repeating_start_or_resume(self):
        reads = 0
        def observe(unit, instrument):
            nonlocal reads
            if unit == self.driver_unit and ("start", unit) in self.events:
                reads += 1
                if reads <= 2:
                    raise RuntimeError("RUNNING_DRIVER_ARGV_MISMATCH")
            return self.running_driver(unit, instrument)
        with mock.patch.object(gateway.driver, "running_driver", observe):
            result = self.run_upgrade()
        self.assertEqual(result["status"], "COMPLETE", result)
        self.assertEqual(self.events.count(("start", self.driver_unit)), 1)
        self.assertEqual(self.resume_count, 1)
        self.assertGreater(reads, 2)

    def test_resumed_start_intent_keeps_its_first_verified_identity_across_context_checks(self):
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
            self.assertEqual(self.run_upgrade()["status"], "HELD")
        checkpoint = self.root / "operator-maintenance" / gateway.CHECKPOINT
        checkpoint.write_bytes(durable_before_start)
        starts = self.events.count(("start", self.driver_unit))
        reads = 0
        def observe(unit, instrument):
            nonlocal reads
            if unit == self.driver_unit:
                reads += 1
                if reads == 2:
                    raise RuntimeError("RUNNING_DRIVER_ARGV_MISMATCH")
            return self.running_driver(unit, instrument)
        with mock.patch.object(gateway.driver, "running_driver", observe):
            result = self.run_upgrade(resume=True)
        self.assertEqual(result["status"], "HELD", result)
        self.assertIn("RUNNING_DRIVER_ARGV_MISMATCH", result["error"])
        self.assertEqual(reads, 2)
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

    def test_new_gateway_read_failure_is_diagnostic_and_same_plan_resume_does_not_repeat_controls(self):
        failed_reads = 0
        def native(argv, **kwargs):
            nonlocal failed_reads
            result = self.native_call(argv, **kwargs)
            if ("system.get_health" in argv and self.current_binary == self.packages["candidate"]["binary"]):
                failed_reads += 1
                return subprocess.CompletedProcess(argv, 6, b"", b"DISCOVERY_LIST_REJECTED\n")
            return result
        with mock.patch.object(gateway.subprocess, "run", native):
            result = self.run_upgrade()
        self.assertEqual(result["status"], "HELD", result)
        self.assertEqual(result["phase"], "GATEWAY_STARTED")
        self.assertIn('"client_reason":"DISCOVERY_LIST_REJECTED"', result["error"])
        self.assertEqual(failed_reads, 1)
        self.assertEqual(self.resume_count, 0)
        checkpoint = json.loads((self.root / "operator-maintenance" / gateway.CHECKPOINT).read_text())
        self.assertFalse(checkpoint.get("owner_progress", {}).get(self.owner["token_sha256"], {}).get("resume_sent"))
        controls = [event for event in self.events if event and event[0] in ("start", "stop")
            and event[-1] in (gateway.GATEWAY, gateway.EXECUTION, gateway.driver.BROKER_UNIT)]
        fences = sum("recovery-query" in event for event in self.events)
        audits = self.audit_number
        result = self.run_upgrade(resume=True)
        self.assertEqual(result["status"], "COMPLETE", result)
        self.assertEqual([event for event in self.events if event and event[0] in ("start", "stop")
            and event[-1] in (gateway.GATEWAY, gateway.EXECUTION, gateway.driver.BROKER_UNIT)], controls)
        self.assertEqual(sum("recovery-query" in event for event in self.events), fences)
        self.assertEqual(self.resume_count, 1)
        self.assertGreater(self.audit_number, audits)

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

    def test_managed_broker_with_zero_main_pid_survives_gateway_transaction_untouched(self):
        broker = gateway.driver.BROKER_UNIT
        execution = gateway.driver.EXECUTION_UNIT
        self.units[broker].update(ActiveState="active", SubState="running", MainPID="0",
                                 InvocationID="original-managed-broker")
        original = copy.deepcopy(self.units[broker])
        proof = {"managed_unit": dict(original, Id=broker, User="", Type="simple", ExitType="cgroup",
                    ControlGroup="/fixture/ibgateway.service", ExecStart="configured-original-launcher",
                    RemainAfterExit="no", ControlPID="0", Job="", KillMode="control-group",
                    SendSIGKILL="yes", UnitFileState="static", StopWhenUnneeded="no"),
            "broker_process": {"pid_key": ["99", "unchanged-java-start"], "exe_sha256": "a" * 64,
                "cgroup": "/fixture/ibgateway.service", "netns": [1, 2]},
            "execution_process": {"pid_key": list(self.alive[self.units[execution]["MainPID"]])},
            "broker_manual_stop_shield_claimed": True,
            "sockets": {"execution": {"inode": 10, "fds": [1]},
                        "accepted": {"inode": 11, "fds": [2]}, "listener": {"inode": 12, "fds": [3]}}}
        self.snapshot["interactive"] = copy.deepcopy(proof)
        guards = {unit: self.guards[unit].read_bytes() for unit in (broker, execution)}
        with mock.patch.object(gateway.driver, "interactive_broker_snapshot",
                               side_effect=lambda *args: copy.deepcopy(proof)):
            result = self.run_upgrade()
        self.assertEqual(result["status"], "COMPLETE", result)
        self.assertEqual(self.units[broker], original)
        self.assertEqual({unit: self.guards[unit].read_bytes() for unit in guards}, guards)
        for unit in (broker, execution):
            self.assertFalse(any(event and event[0] in ("stop", "start") and event[-1] == unit
                                 for event in self.events))
            self.assertFalse(self.guards[unit].with_name(gateway.POLICY).exists())
            self.assertFalse(self.guards[unit].with_name(gateway.RELEASE).exists())
        self.assertTrue(self.last_audit is not None)

    def test_only_known_expired_watch_may_disappear(self):
        watch = dict(record(), paper=False, watch=True, token_sha256="e" * 64, expires_at_ms=1)
        self.assertEqual(gateway.comparable_catalog([self.rows[0], watch], self.rows), self.rows)
        active = dict(watch, expires_at_ms=int(self.clock * 1000) + 60000)
        self.assertIn(active, gateway.comparable_catalog([active], []))

    def prepare_reconnect_boundary(self):
        # Exact production boundary: old Gateway drained, replacement is
        # running, but its first observation was not checkpointed yet.
        proof = {"execution_process": {"pid_key": ["368455", "100"], "exe_sha256": "e" * 64,
                "cgroup": "/execution", "netns": [1, 2]},
            "broker_process": {"pid_key": ["10617", "200"], "exe_sha256": "b" * 64,
                "cgroup": "/gnome", "netns": [1, 2]}, "scope": {"InvocationID": "gnome-original"},
            "profile": {"HEPTA_IB_PAPER_HOST": "127.0.0.1", "HEPTA_IB_PAPER_PORT": "4002"},
            "selected_edges": {"PropagatesStopTo": ""},
            "sockets": {
                "execution": {"inode": 2918032, "fds": [44], "local": ["127.0.0.1", 44164],
                    "remote": ["127.0.0.1", 4002], "state": "01", "uid": os.geteuid()},
                "accepted": {"inode": 2869080, "fds": [91], "local": ["127.0.0.1", 4002],
                    "remote": ["127.0.0.1", 44164], "state": "01", "uid": os.geteuid()},
                "listener": {"inode": 50, "fds": [90], "local": ["127.0.0.1", 4002],
                    "remote": ["0.0.0.0", 0], "state": "0A", "uid": os.geteuid()}}}
        self.snapshot["interactive"] = copy.deepcopy(proof)
        self.interactive_current = copy.deepcopy(proof)
        self.patches.enter_context(mock.patch.object(gateway.driver, "interactive_broker_snapshot",
            side_effect=lambda *args: copy.deepcopy(self.interactive_current)))
        original = gateway.atomic_json
        last_durable = None
        failed = False
        def write(path, value):
            nonlocal last_durable, failed
            if Path(path).name == gateway.CHECKPOINT and value.get("phase") == "GATEWAY_STARTED" and not failed:
                last_durable = Path(path).read_bytes(); failed = True
                raise PermissionError("FIXTURE_INITIAL_PROC_PERMISSION_TRANSITION")
            original(path, value)
        with mock.patch.object(gateway, "atomic_json", write):
            result = self.run_upgrade()
        self.assertEqual(result["status"], "HELD", result)
        self.assertEqual(self.resume_count, 0)
        self.checkpoint = self.root / "operator-maintenance" / gateway.CHECKPOINT
        self.checkpoint.write_bytes(last_durable)
        value = json.loads(self.checkpoint.read_text())
        self.assertEqual(value["phase"], "GATEWAY_STOPPED")
        self.assertTrue(value["gateway_stopped"] and value["owners_fenced"])
        self.assertNotIn("gateway_started", value)
        self.original_interactive = copy.deepcopy(proof)
        self.interactive_current["sockets"]["accepted"].update(
            inode=12592053, fds=[101], remote=["127.0.0.1", 33336])
        self.interactive_current["sockets"]["execution"].update(
            inode=12626971, fds=[45], local=["127.0.0.1", 33336])
        return value

    def assert_no_reconnect_refresh(self, result):
        self.assertEqual(result["status"], "HELD", result)
        value = json.loads(self.checkpoint.read_text())
        self.assertNotIn("interactive_refresh", value)
        self.assertEqual(value["snapshot"]["interactive"], self.original_interactive)
        self.assertEqual(self.resume_count, 0)

    def test_exact_stopped_gateway_reconnect_refreshes_witness_and_recovers_once(self):
        self.prepare_reconnect_boundary()
        queries = sum("recovery-query" in event for event in self.events)
        starts = self.events.count(("start", gateway.GATEWAY))
        result = self.run_upgrade(resume=True)
        self.assertEqual(result["status"], "COMPLETE", result)
        self.assertEqual(self.events.count(("start", gateway.GATEWAY)), starts)
        self.assertEqual(sum("recovery-query" in event for event in self.events), queries)
        self.assertEqual(self.resume_count, 1)
        value = json.loads(self.checkpoint.read_text())
        self.assertEqual(value["snapshot"]["interactive"], self.interactive_current)
        refresh = value["interactive_refresh"]
        self.assertEqual(refresh["gateway_binary_sha256"], self.packages["candidate"]["binary"]["sha256"])
        self.assertEqual(refresh["witness_sha256"], gateway.digest(json.dumps(self.interactive_current, sort_keys=True)))
        self.assertEqual(len(refresh["audits"]), 1)
        self.assertTrue(refresh["audits"][0]["native_health"]["owner_recovery_only"])
        self.assertTrue(refresh["audits"][0]["native_health"]["native_new_risk_guard_blocked"])
        self.assertNotIn("DU_FIXTURE", json.dumps(refresh))
        self.assertEqual(self.checkpoint.stat().st_mode & 0o777, 0o600)

    def test_reconnect_cannot_refresh_after_resume_or_controller_start_intent(self):
        value = self.prepare_reconnect_boundary()
        for changes in (
                {"owner_progress": {self.owner["token_sha256"]: {"resume_sent": True}}},
                {"owner_progress": {self.owner["token_sha256"]: {"resume_sent": False}}},
                {"starting_controller": self.driver_unit},
                {"controllers_started": {self.driver_unit: self.owner["resident"]}}):
            with self.subTest(changes=changes):
                self.checkpoint.write_text(json.dumps(dict(value, **changes)))
                result = self.run_upgrade(resume=True)
                self.assert_no_reconnect_refresh(result)

    def test_reconnect_never_masks_listener_process_or_endpoint_host_changes(self):
        value = self.prepare_reconnect_boundary()
        tcp_only = copy.deepcopy(self.interactive_current)
        for change in ("listener", "execution-process", "broker-process", "endpoint-host"):
            with self.subTest(change=change):
                self.interactive_current = copy.deepcopy(tcp_only)
                if change == "listener":
                    self.interactive_current["sockets"]["listener"]["fds"] = [999]
                elif change == "execution-process":
                    self.interactive_current["execution_process"]["pid_key"][1] = "replaced"
                elif change == "broker-process":
                    self.interactive_current["broker_process"]["exe_sha256"] = "f" * 64
                else:
                    self.interactive_current["sockets"]["execution"]["local"][0] = "127.0.0.2"
                    self.interactive_current["sockets"]["accepted"]["remote"][0] = "127.0.0.2"
                self.checkpoint.write_text(json.dumps(value))
                result = self.run_upgrade(resume=True)
                self.assert_no_reconnect_refresh(result)
                self.assertIn("BROKER_IDENTITY_CHANGED", result["error"])

    def test_reconnect_requires_original_catalog_scope_generation_and_command(self):
        value = self.prepare_reconnect_boundary()
        original = copy.deepcopy(self.rows[0])
        for changes in ({"generation": original["generation"] + 1}, {"token_sha256": "f" * 64},
                {"session_sha256": "e" * 64}, {"recovery_command_sha256": "d" * 64}, {"recovery_only": False}):
            with self.subTest(changes=changes):
                self.rows[0] = dict(original, **changes)
                self.checkpoint.write_text(json.dumps(value))
                result = self.run_upgrade(resume=True)
                self.assert_no_reconnect_refresh(result)
                self.assertIn("ORIGINAL_CATALOG_CHANGED", result["error"])

    def test_reconnect_rejects_uncertain_nonflat_or_incomplete_native_audit(self):
        value = self.prepare_reconnect_boundary()
        original = self.native_call
        for changes in ({"owner_uncertain_command_count": 1}, {"broker_position_quantity": "1"},
                {"broker_recovery_audit_barrier_complete": False}, {"broker_recovery_audit_new_connection_epoch_required": True},
                {"broker_connection_epoch": 0}, {"service_epoch": ""}):
            with self.subTest(changes=changes):
                def native(argv, **kwargs):
                    result = original(argv, **kwargs)
                    if "owner-audit" in argv:
                        reply = json.loads(result.stdout); reply.update(changes)
                        result.stdout = json.dumps(reply).encode()
                    return result
                self.checkpoint.write_text(json.dumps(value))
                with mock.patch.object(gateway.subprocess, "run", native):
                    result = self.run_upgrade(resume=True)
                self.assert_no_reconnect_refresh(result)

    def test_reconnect_requires_native_recovery_hold_and_original_command_same_epoch(self):
        value = self.prepare_reconnect_boundary()
        original = self.native_call
        for change in ("recovery-only", "entry-gate", "command-epoch"):
            with self.subTest(change=change):
                def native(argv, **kwargs):
                    result = original(argv, **kwargs)
                    reply = json.loads(result.stdout)
                    if "system.get_health" in argv and change != "command-epoch":
                        field = "owner_recovery_only" if change == "recovery-only" else "native_new_risk_guard_blocked"
                        reply["payload"]["execution_authoritative_health"][field] = False
                    elif "execution.get_command_status" in argv and change == "command-epoch":
                        reply["payload"]["execution_service_epoch"] = "unknown-new-service"
                    result.stdout = json.dumps(reply).encode()
                    return result
                self.checkpoint.write_text(json.dumps(value))
                with mock.patch.object(gateway.subprocess, "run", native):
                    result = self.run_upgrade(resume=True)
                self.assert_no_reconnect_refresh(result)

    def test_reconnect_requires_tcp_stability_across_native_observation(self):
        self.prepare_reconnect_boundary()
        original = self.native_call
        def native(argv, **kwargs):
            result = original(argv, **kwargs)
            if "execution.get_command_status" in argv:
                self.interactive_current["sockets"]["accepted"]["inode"] += 1
            return result
        with mock.patch.object(gateway.subprocess, "run", native):
            result = self.run_upgrade(resume=True)
        self.assert_no_reconnect_refresh(result)
        self.assertIn("INTERACTIVE_BROKER_CHANGED", result["error"])

    def test_reconnect_audit_cannot_expire_during_final_context_checks(self):
        self.prepare_reconnect_boundary()
        original = self.native_call
        observed_command = False
        def native(argv, **kwargs):
            nonlocal observed_command
            result = original(argv, **kwargs)
            if "execution.get_command_status" in argv:
                observed_command = True
            return result
        def runtime(*args):
            if observed_command:
                self.clock += 6
            return {"same_execution": True}
        with mock.patch.object(gateway.subprocess, "run", native), mock.patch.object(gateway, "native_hold_runtime", runtime):
            result = self.run_upgrade(resume=True)
        self.assert_no_reconnect_refresh(result)
        self.assertIn("FRESH_AUDIT_EXPIRED", result["error"])

    def test_reconnect_witness_requires_every_writer_in_one_native_authority(self):
        unit = "heptatrader-crypto-driver.service"
        self.units[unit].update(ActiveState="active", SubState="running", MainPID="7000", InvocationID="crypto-original")
        self.alive["7000"] = ("7000", "123")
        path = self.root / "crypto-driver/state.json"
        put(path, json.dumps(dict(self.initial_state, instrument="BTC.USD")).encode())
        put(path.parent / "driver.lock", b"")
        put(self.home / ".config/heptatrader/crypto.token", b"second-token\n")
        owner = gateway.controller("BTC.USD", self.home)
        owner["resident"] = self.running_driver(unit, "BTC.USD")
        row = dict(record(), token_sha256=gateway.digest("second-token"), session_sha256=gateway.digest("second-session"))
        owner["record"] = copy.deepcopy(row); self.rows.append(row)
        self.snapshot["owners"].append(owner); self.snapshot["records"] = copy.deepcopy(self.rows)
        self.snapshot["statuses"] = copy.deepcopy(self.units); self.snapshot["frozen"] = {}
        self.snapshot["service_policy"][unit] = dict(self.snapshot["service_policy"][self.driver_unit])
        value = self.prepare_reconnect_boundary()
        original = self.native_call
        def native(argv, **kwargs):
            result = original(argv, **kwargs)
            if "owner-audit" in argv and argv[argv.index("--token-file") + 1] == owner["token_path"]:
                reply = json.loads(result.stdout); reply["broker_connection_epoch"] += 1
                result.stdout = json.dumps(reply).encode()
            return result
        with mock.patch.object(gateway.subprocess, "run", native):
            result = self.run_upgrade(resume=True)
        self.assert_no_reconnect_refresh(result)
        self.assertIn("NATIVE_AUTHORITIES_CHANGED", result["error"])
        self.checkpoint.write_text(json.dumps(value))
        with mock.patch.object(gateway, "resume_owner", side_effect=RuntimeError("FIXTURE_STOP_AFTER_WITNESS")):
            result = self.run_upgrade(resume=True)
        self.assertEqual(result["status"], "HELD", result)
        self.assertIn("FIXTURE_STOP_AFTER_WITNESS", result["error"])
        refreshed = json.loads(self.checkpoint.read_text())["interactive_refresh"]
        self.assertEqual({item["token_sha256"] for item in refreshed["audits"]}, {r["token_sha256"] for r in self.rows})
        self.assertEqual(len(refreshed["audits"]), 2)
        self.assertEqual(self.resume_count, 0)

    def test_activation_never_globally_ignores_dynamic_tcp_changes(self):
        self.snapshot["interactive"] = {"sockets": {}}
        result = self.run_upgrade()
        self.assertEqual(result["status"], "HELD", result)
        self.assertIn("REFRESH_NOT_ALLOWED", result["error"])
        self.assertFalse(any(event[0] == "stop" for event in self.events))


class AuditSealComponents(unittest.TestCase):
    """Real private files/checkpoints; native publication is injected."""
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="hepta-audit-seal-components-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = put(self.root / "audit", b'{"legacy_fixture":true}\n')
        self.original = self.path.read_bytes()
        self.checkpoint = self.root / "checkpoint.json"
        self.plan = {"sessionctl": {"path": "/fixture/sessionctl", "sha256": "a" * 64}}
        self.snapshot = {"deadline": gateway.now_ms() + 86400000,
            "gateway": {"status": {"MainPID": "1234"}, "pid_key": ["1234", "5678"],
                "binary": {"path": "/fixture/gateway", "sha256": "b" * 64}},
            "owners": [], "audit_seal": {"path": str(self.path), "sessionctl": self.plan["sessionctl"]},
            "packages": {name: {"binary": {"path": "/fixture/gateway", "sha256": "b" * 64}}
                for name in ("candidate", "fallback")}}
        self.state = {"snapshot": self.snapshot, "plan_sha256": "c" * 64, "markers": ["original-hold"],
            "phase": "GATEWAY_STOPPED", "gateway_stopped": True, "owners_fenced": True,
            "stopped_controllers": []}
        self.current = {"ActiveState": "inactive", "MainPID": "0"}
        self.job = ""
        self.old_pid = None
        self.seals = 0
        self.verifies = 0
        self.failure = None
        self.stack = contextlib.ExitStack(); self.addCleanup(self.stack.close)
        self.stack.enter_context(mock.patch.object(gateway.driver.fence, "show_unit", lambda unit: dict(self.current)))
        self.stack.enter_context(mock.patch.object(gateway.driver, "unit_properties", lambda *args: {"Job": self.job}))
        self.stack.enter_context(mock.patch.object(gateway.driver, "pid_key", lambda pid: self.old_pid))
        self.real_cli = gateway.audit_offline_cli
        self.stack.enter_context(mock.patch.object(gateway, "audit_offline_cli", self.cli))

    def save(self):
        gateway.atomic_json(self.checkpoint, self.state)

    def reload(self):
        self.state = json.loads(self.checkpoint.read_text())
        self.snapshot = self.state["snapshot"]

    def cli(self, plan, snapshot, operation):
        self.assertEqual(self.current["ActiveState"], "inactive")
        if operation == "verify":
            self.verifies += 1
            self.assertGreater(self.seals, 0)
            raw = self.path.read_bytes()
            if raw.startswith(b"HJA3\t"):
                predecessor, _ = gateway.audit_anchor(raw)
                segment = Path(str(self.path) + ".segments") / (predecessor + ".hja2")
                if gateway.digest(segment.read_bytes()) != predecessor:
                    raise RuntimeError("FIXTURE_NATIVE_SEGMENT_HASH_INVALID")
            return 0
        self.seals += 1
        durable = json.loads(self.checkpoint.read_text())["audit_seal"]
        self.assertEqual(durable["status"], "UNKNOWN")
        self.assertIn("intent", durable)
        raw = self.path.read_bytes()
        if not raw or raw.startswith(b"HJA3\t"):
            return None  # Native pure-anchor/noop directory fsync.
        archive = Path(str(self.path) + ".segments")
        archive.mkdir(mode=0o700, exist_ok=True)
        segment = archive / (gateway.digest(raw) + ".hja2")
        if not segment.exists():
            put(segment, raw)
        if self.failure == "after_segment":
            raise subprocess.TimeoutExpired("private-fixture", 1)
        anchor = b"HJA3\t" + gateway.digest(raw).encode() + b"\t0\n"
        temporary = put(self.root / ".anchor-fixture", anchor)
        os.replace(temporary, self.path)
        if self.failure == "after_anchor":
            raise subprocess.TimeoutExpired("private-fixture", 1)
        return None

    def run_step(self, save=None):
        return gateway.run_audit_seal(self.plan, self.snapshot, self.state, save or self.save, lambda: None)

    def start_admission(self):
        return gateway.audit_before_gateway_start(self.snapshot, self.state,
            self.snapshot["packages"]["candidate"]["binary"], self.save, lambda: None)


    def test_legacy_zero_records_seals_all_bytes_before_durable_start_admission(self):
        self.run_step()
        progress = self.state["audit_seal"]
        self.assertEqual(progress["intent"]["kind"], "ROTATABLE")
        self.assertIsNone(progress["intent"]["records"])
        self.assertIsNone(progress["intent"]["anchor"])
        self.assertEqual(progress["receipt"]["records"], 0)
        self.assertEqual(self.verifies, 1)
        segment = Path(str(self.path) + ".segments") / (gateway.digest(self.original) + ".hja2")
        self.assertEqual(segment.read_bytes(), self.original)
        self.assertEqual(json.loads(self.checkpoint.read_text())["audit_seal"]["status"], "VERIFIED")
        self.start_admission()
        self.assertIn("writer_start_requested", json.loads(self.checkpoint.read_text())["audit_seal"])

    def test_published_anchor_without_ack_requires_same_plan_native_fsync(self):
        self.failure = "after_anchor"
        with self.assertRaises(subprocess.TimeoutExpired):
            self.run_step()
        with self.assertRaisesRegex(RuntimeError, "VERIFIED_RECEIPT_REQUIRED"):
            self.start_admission()
        original_intent = copy.deepcopy(self.state["audit_seal"]["intent"])
        self.reload()
        self.failure = None
        self.run_step()
        self.assertEqual(self.seals, 2)
        self.assertEqual(self.state["audit_seal"]["intent"], original_intent)
        self.assertEqual(self.state["audit_seal"]["attempts"][1]["observed"], "PUBLISHED_DURABILITY_UNKNOWN")
        self.assertEqual(len(list(Path(str(self.path) + ".segments").iterdir())), 1)

    def test_segment_published_before_active_replacement_is_retained_on_resume(self):
        self.failure = "after_segment"
        with self.assertRaises(subprocess.TimeoutExpired):
            self.run_step()
        self.assertEqual(self.path.read_bytes(), self.original)
        segment = next(Path(str(self.path) + ".segments").iterdir())
        inode = segment.stat().st_ino
        self.reload()
        self.failure = None
        self.run_step()
        self.assertEqual(segment.stat().st_ino, inode)
        self.assertEqual(segment.read_bytes(), self.original)
        self.assertEqual(self.state["audit_seal"]["attempts"][1]["observed"], "NO_ACTIVE_REPLACEMENT")

    def test_verified_receipt_save_failure_cannot_authorize_either_start(self):
        def broken_save():
            self.save()  # Simulate rename visible before parent fsync reports failure.
            if self.state["audit_seal"].get("status") == "VERIFIED":
                raise OSError("CHECKPOINT_DIRECTORY_FSYNC_FAILED")
        with self.assertRaisesRegex(OSError, "CHECKPOINT_DIRECTORY_FSYNC_FAILED"):
            self.run_step(save=broken_save)
        self.assertEqual(self.state["audit_seal"]["status"], "UNKNOWN")
        self.assertIn("unconfirmed_receipt", self.state["audit_seal"])
        for selected in ("candidate", "fallback"):
            with self.assertRaisesRegex(RuntimeError, "VERIFIED_RECEIPT_REQUIRED"):
                gateway.audit_before_gateway_start(self.snapshot, self.state,
                    self.snapshot["packages"][selected]["binary"], self.save, lambda: None)
        self.save()  # Outer controller's HELD checkpoint.
        self.reload()
        self.run_step()
        self.assertEqual(self.seals, 2)


    def test_empty_and_existing_pure_anchor_still_call_native_durability_noop(self):
        self.path.write_bytes(b"")
        self.run_step()
        self.assertEqual(self.state["audit_seal"]["intent"]["kind"], "EMPTY")
        self.assertEqual(self.path.read_bytes(), b"")
        self.assertEqual(self.seals, 1)
        self.assertFalse(Path(str(self.path) + ".segments").exists())
        # A separate stopped transaction can already have an exact anchor.
        archive = Path(str(self.path) + ".segments")
        archive.mkdir(mode=0o700)
        predecessor = gateway.digest(self.original)
        put(archive / (predecessor + ".hja2"), self.original)
        anchor = ("HJA3\t" + predecessor + "\t0\n").encode()
        self.path.write_bytes(anchor)
        self.state.pop("audit_seal")
        self.run_step()
        self.assertEqual(self.state["audit_seal"]["intent"]["kind"], "ANCHOR")
        self.assertEqual(self.path.read_bytes(), anchor)
        self.assertEqual(self.seals, 2)
        self.assertEqual(len(list(archive.iterdir())), 1)

    def test_unexpected_writer_job_or_original_pid_blocks_even_initial_verify(self):
        for field in ("active", "job", "pid"):
            with self.subTest(field=field):
                self.current["ActiveState"] = "active" if field == "active" else "inactive"
                self.job = "pending" if field == "job" else ""
                self.old_pid = ("1234", "5678") if field == "pid" else None
                with self.assertRaisesRegex(RuntimeError, "WRITER_NOT_STOPPED"):
                    self.run_step()
        self.assertEqual((self.seals, self.verifies), (0, 0))

    def test_changed_source_or_segment_cannot_be_treated_as_a_completed_seal(self):
        self.failure = "after_segment"
        with self.assertRaises(subprocess.TimeoutExpired):
            self.run_step()
        self.path.write_bytes(b"unexpected writer data\n")
        self.reload()
        self.failure = None
        with self.assertRaisesRegex(RuntimeError, "PUBLICATION_UNKNOWN"):
            self.run_step()
        self.assertEqual(self.seals, 1)
        self.assertEqual(next(Path(str(self.path) + ".segments").iterdir()).read_bytes(), self.original)

    def test_verified_files_changed_before_first_start_keep_writer_stopped(self):
        self.run_step()
        with self.path.open("ab") as output:
            output.write(b"unrequested append\n")
        with self.assertRaisesRegex(RuntimeError, "PUBLICATION_UNKNOWN"):
            self.start_admission()
        self.assertNotIn("writer_start_requested", self.state["audit_seal"])

    def test_after_durable_start_intent_resume_does_not_reseal_legitimate_appends(self):
        self.run_step()
        self.start_admission()
        with self.path.open("ab") as output:
            output.write(b"new writer append after its durable start intent\n")
        self.state["phase"] = "GATEWAY_STARTED"
        self.state["gateway_started"] = {"known": "candidate"}
        self.current.update(ActiveState="active", MainPID="9000")
        calls = (self.seals, self.verifies)
        self.save(); self.reload()
        self.run_step()
        self.start_admission()
        self.assertEqual((self.seals, self.verifies), calls)

    def test_real_directory_lock_and_unsafe_inode_are_not_bypassed(self):
        directory = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            fcntl.flock(directory, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(BlockingIOError):
                gateway.audit_file(self.path)
        finally:
            os.close(directory)
        os.link(self.path, self.root / "hardlink")
        with self.assertRaisesRegex(RuntimeError, "PRIVATE_FILE_REQUIRED"):
            gateway.audit_file(self.path)
        (self.root / "hardlink").unlink()
        self.path.chmod(0o640)
        with self.assertRaisesRegex(RuntimeError, "PRIVATE_FILE_REQUIRED"):
            gateway.audit_file(self.path)


    def test_offline_cli_has_exact_path_and_strict_uint64_result_without_retry(self):
        valid = subprocess.CompletedProcess([], 0, b"audit verified records=18446744073709551615\n", b"")
        with mock.patch.object(gateway, "pinned", return_value=Path("/fixture/sessionctl")) as pin, \
                mock.patch.object(gateway.subprocess, "run", return_value=valid) as run:
            self.assertEqual(self.real_cli(self.plan, self.snapshot, "verify"), 2 ** 64 - 1)
            self.assertEqual(run.call_args.args[0], ["/fixture/sessionctl", "--audit-verify", str(self.path)])
            self.assertEqual(run.call_args.kwargs["env"], {})
            self.assertLessEqual(run.call_args.kwargs["timeout"], 300)
            self.assertEqual(pin.call_count, 2)
        for raw in (b"audit verified records=-1\n", b"audit verified records=00\n",
                b"audit verified records=18446744073709551616\n", b"audit verified records=1\nextra\n"):
            with self.subTest(raw=raw), \
                    mock.patch.object(gateway, "pinned", return_value=Path("/fixture/sessionctl")), \
                    mock.patch.object(gateway.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, raw, b"")) as run:
                with self.assertRaisesRegex(RuntimeError, "VERIFY_RESULT_INVALID"):
                    self.real_cli(self.plan, self.snapshot, "verify")
                self.assertEqual(run.call_count, 1)

    def test_intent_cannot_move_to_a_new_plan_hold_or_deadline(self):
        self.failure = "after_anchor"
        with self.assertRaises(subprocess.TimeoutExpired):
            self.run_step()
        self.failure = None
        for field in ("plan_sha256", "markers", "deadline"):
            with self.subTest(field=field):
                self.reload()
                if field == "deadline":
                    self.snapshot["deadline"] += 1000
                else:
                    self.state[field] = "different"
                with self.assertRaisesRegex(RuntimeError, "INTENT_BINDING_CHANGED"):
                    self.run_step()
        self.assertEqual(self.seals, 1)

    def test_unreviewed_writer_is_rejected_before_and_after_saved_start_intent(self):
        self.run_step()
        binary = {"path": "/fixture/unreviewed", "sha256": "f" * 64}
        with self.assertRaisesRegex(RuntimeError, "UNREVIEWED_WRITER"):
            gateway.audit_before_gateway_start(self.snapshot, self.state, binary, self.save, lambda: None)
        self.start_admission()
        with self.assertRaisesRegex(RuntimeError, "UNREVIEWED_WRITER"):
            gateway.audit_before_gateway_start(self.snapshot, self.state, binary, self.save, lambda: None)


    def test_visible_verified_receipt_is_durably_republished_before_first_start(self):
        def interrupted_save():
            self.save()
            if self.state["audit_seal"].get("status") == "VERIFIED":
                raise OSError("CHECKPOINT_FSYNC_ACK_LOST")
        with self.assertRaises(OSError):
            self.run_step(save=interrupted_save)
        # Model process death before the outer HELD checkpoint: the visible
        # receipt records an actually acknowledged native seal and verify.
        self.reload()
        self.assertEqual(self.state["audit_seal"]["status"], "VERIFIED")
        self.run_step()
        self.assertEqual(self.seals, 1)
        self.start_admission()
        self.assertIn("writer_start_requested", json.loads(self.checkpoint.read_text())["audit_seal"])

    def test_read_only_start_gate_does_not_record_a_start_intent(self):
        self.run_step()
        gateway.audit_before_gateway_start(self.snapshot, self.state,
            self.snapshot["packages"]["candidate"]["binary"], self.save, lambda: None,
            request_start=False)
        self.assertNotIn("writer_start_requested", self.state["audit_seal"])


    def test_postpublication_count_is_observed_then_verified_without_rewriting_intent(self):
        self.failure = "after_anchor"
        with self.assertRaises(subprocess.TimeoutExpired):
            self.run_step()
        self.reload()
        original_intent = copy.deepcopy(self.state["audit_seal"]["intent"])
        self.assertIsNone(original_intent["records"])
        self.path.write_bytes(self.path.read_bytes().replace(b"\t0\n", b"\t1\n"))
        self.failure = None
        with self.assertRaisesRegex(RuntimeError, "VERIFIED_COUNT_CHANGED"):
            self.run_step()
        self.assertEqual(self.state["audit_seal"]["intent"], original_intent)
        self.assertEqual(self.state["audit_seal"]["status"], "UNKNOWN")
        with self.assertRaisesRegex(RuntimeError, "VERIFIED_RECEIPT_REQUIRED"):
            self.start_admission()

    def test_corrupt_retained_segment_is_never_accepted_from_its_filename(self):
        self.failure = "after_anchor"
        with self.assertRaises(subprocess.TimeoutExpired):
            self.run_step()
        self.reload()
        segment = next(Path(str(self.path) + ".segments").iterdir())
        segment.write_bytes(b"X" + segment.read_bytes()[1:])
        self.failure = None
        with self.assertRaisesRegex(RuntimeError, "NATIVE_SEGMENT_HASH_INVALID"):
            self.run_step()
        self.assertEqual(self.state["audit_seal"]["status"], "UNKNOWN")
        with self.assertRaisesRegex(RuntimeError, "VERIFIED_RECEIPT_REQUIRED"):
            self.start_admission()


class AuditQualificationComponents(unittest.TestCase):
    def setUp(self):
        self.binary = {"path": "/fixture/gateway", "sha256": "a" * 64}
        self.plan = {"candidate": {"manifest": "/fixture/current.json", "sha256": "b" * 64},
            "fallback": {"manifest": "/fixture/current.json", "sha256": "b" * 64},
            "sessionctl": {"path": "/fixture/sessionctl", "sha256": "c" * 64},
            "recovery_fixes_sha256": "d" * 64}
        self.packages = {name: {"binary": self.binary, "source_sha": "e" * 40} for name in ("candidate", "fallback")}
        good = {"seal_returncode": 0, "verify_before_returncode": 0, "verify_after_returncode": 0,
            "verify_after_rejection": "", "gateway_returncode": 0, "ready": True, "rejection": ""}
        bad = dict(good, verify_after_returncode=1, gateway_returncode=7, ready=False,
            verify_after_rejection="SUPERVISOR_AUDIT_SEGMENT_INVALID",
            rejection="SUPERVISOR_AUDIT_INIT_FAILED:SUPERVISOR_AUDIT_SEGMENT_INVALID")
        self.receipt = {"schema": "heptatrader.gateway-audit-reader-qualification.v1", "result": "PASS",
            "uid": 1000, "execution_mode": "SIMULATOR", "watch_only": True,
            "persistent_lease_store": False, "processes_reaped": True, "production_services_changed": False,
            "source_inputs": {**{key: self.plan[key] for key in ("candidate", "fallback", "sessionctl")},
                "recovery_evidence": {"path": "/fixture/recovery.json", "sha256": "d" * 64},
                "simulator": {"path": "/fixture/simulator", "sha256": "f" * 64}},
            "input_artifact": {"path": "/fixture/inputs.json", "sha256": "1" * 64},
            "qualification_tool": {"path": "/fixture/qualification.py", "sha256": "2" * 64},
            "cases": [{"binary": self.binary, "source_sha": "e" * 40, "roles": ["candidate", "fallback"],
                "valid": good, "missing": copy.deepcopy(bad), "corrupt": copy.deepcopy(bad)}]}

    def test_one_real_reader_identity_can_cover_both_unchanged_release_roles(self):
        self.assertEqual(gateway.audit_qualification(self.receipt, self.plan, self.packages, 1000),
            self.receipt["source_inputs"])

    def test_equal_binary_bytes_at_another_path_need_their_own_actual_reader_case(self):
        self.packages["fallback"] = {"source_sha": "e" * 40,
            "binary": dict(self.binary, path="/fixture/unqualified-gateway")}
        with self.assertRaisesRegex(RuntimeError, "QUALIFIED_BINARY_CHANGED"):
            gateway.audit_qualification(self.receipt, self.plan, self.packages, 1000)

    def test_flag_or_unrelated_start_failure_is_not_a_reader_qualification(self):
        for field, value in (("gateway_returncode", 5), ("gateway_returncode", True),
                ("ready", True), ("rejection", "UNRELATED_CONFIG_FAILURE"), ("verify_after_returncode", 0)):
            with self.subTest(field=field, value=value):
                receipt = copy.deepcopy(self.receipt)
                receipt["cases"][0]["missing"][field] = value
                with self.assertRaisesRegex(RuntimeError, "READER_CASE_NOT_CONFIRMED"):
                    gateway.audit_qualification(receipt, self.plan, self.packages, 1000)

    def test_prior_binary_source_uid_or_unqualified_fallback_cannot_borrow_receipt(self):
        for change in ("source", "manifest", "uid", "roles"):
            with self.subTest(change=change):
                receipt = copy.deepcopy(self.receipt)
                if change == "source": receipt["cases"][0]["source_sha"] = "0" * 40
                elif change == "manifest": receipt["source_inputs"]["candidate"]["sha256"] = "0" * 64
                elif change == "uid": receipt["uid"] = 0
                else: receipt["cases"][0]["roles"] = ["candidate"]
                with self.assertRaises(RuntimeError):
                    gateway.audit_qualification(receipt, self.plan, self.packages, 1000)


class GatewayReadDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.plan = {"reader": {"path": "/fixture/heptactl", "sha256": "a" * 64}}
        self.environment = {"HEPTA_TOOL_SOCKET": "/private/gateway.sock"}
        self.owner = {"token_path": "/private/secret-token"}
        patcher = mock.patch.object(gateway, "pinned", return_value=Path("/fixture/heptactl"))
        patcher.start(); self.addCleanup(patcher.stop)

    def failure(self, result=None, error=None, error_type=RuntimeError):
        with mock.patch.object(gateway.subprocess, "run", return_value=result, side_effect=error) as run:
            with self.assertRaisesRegex(error_type, r"NATIVE_READ_NOT_CONFIRMED:system\.get_health:") as caught:
                gateway.read_tool(self.plan, self.environment, self.owner, "system.get_health")
            run.assert_called_once()
        self.assertIs(type(caught.exception), error_type)
        if isinstance(error, OSError):
            self.assertEqual(caught.exception.errno, error.errno)
        if isinstance(error, subprocess.TimeoutExpired):
            self.assertEqual(caught.exception.timeout, error.timeout)
            self.assertIsNone(caught.exception.output)
            self.assertIsNone(caught.exception.stderr)
        raw = str(caught.exception)
        self.assertLess(len(raw), 1024)
        self.assertNotIn("secret-token", raw)
        self.assertNotIn("secret-output", raw)
        self.assertNotIn("SECRET_OUTPUT", raw)
        self.assertNotIn("/private/", raw)
        rendered = "".join(traceback.format_exception(caught.exception))
        self.assertNotIn("secret-token", rendered)
        self.assertNotIn("secret-output", rendered)
        self.assertNotIn("SECRET_OUTPUT", rendered)
        self.assertNotIn("/private/", rendered)
        return json.JSONDecoder().raw_decode(raw.split("NATIVE_READ_NOT_CONFIRMED:system.get_health:", 1)[1])[0]

    def test_success_preserves_payload_and_existing_single_read_timeouts(self):
        payload = {"execution_service_epoch": "same-native-epoch"}
        response = {"status": "ok", "tool": "system.get_health", "payload": payload}
        with mock.patch.object(gateway.subprocess, "run", return_value=subprocess.CompletedProcess(
                [], 0, json.dumps(response).encode(), b"")) as run:
            self.assertEqual(gateway.read_tool(self.plan, self.environment, self.owner, "system.get_health"), payload)
            run.assert_called_once()
            self.assertEqual(run.call_args.kwargs, {"capture_output": True, "timeout": 12})
            argv = run.call_args.args[0]
            self.assertEqual(argv[argv.index("--io-timeout-ms") + 1], "4000")

    def test_known_client_reason_is_retained_without_retry(self):
        for code in (b"DISCOVERY_LIST_REJECTED", b"SOCKET_CONNECT_FAILED", b"FRAME_BODY_TIMEOUT"):
            with self.subTest(code=code):
                detail = self.failure(subprocess.CompletedProcess([], 6, b"", code + b"\n"))
                self.assertEqual(detail["returncode"], 6)
                self.assertEqual(detail["client_reason"], code.decode())
                self.assertEqual(detail["stdout_bytes"], 0)
                self.assertEqual(detail["stderr_bytes"], len(code) + 1)

    def test_native_error_uses_existing_strict_projection_and_omits_detail(self):
        response = {"status": "error", "tool": "system.get_health", "reason_code": "READ_TOOL_FAILED",
            "detail": "secret-output /private/secret-token", "order_id": -1, "payload": None}
        detail = self.failure(subprocess.CompletedProcess([], 9, json.dumps(response).encode(), b""))
        self.assertEqual(detail["reason_code"], "READ_TOOL_FAILED")
        self.assertEqual(detail["status"], "error")
        self.assertTrue(detail["tool_matches"])
        self.assertFalse(detail["payload_object"])
        for changed in (dict(response, reason_code="secret-output"), dict(response, reason_code="SECRET_OUTPUT"),
                dict(response, extra="secret-output"),
                dict(response, detail="secret-output" * 4096), dict(response, tool="secret-output")):
            with self.subTest(changed=list(changed)):
                detail = self.failure(subprocess.CompletedProcess([], 9, json.dumps(changed).encode(), b""))
                self.assertNotIn("reason_code", detail)

    def test_invalid_json_and_nonobject_envelopes_fail_with_bounded_classification(self):
        for raw in (b"secret-output", b'{"status":"ok","status":"secret-output"}', b"\xff", b"[" * 1200):
            with self.subTest(raw=raw[:24]):
                detail = self.failure(subprocess.CompletedProcess([], 0, raw, b"secret-output"))
                self.assertEqual(detail["kind"], "INVALID_JSON")
        for raw in (b"null", b"[]", b'"secret-output"'):
            with self.subTest(raw=raw):
                detail = self.failure(subprocess.CompletedProcess([], 0, raw, b""))
                self.assertEqual(detail["kind"], "INVALID_ENVELOPE")

    def test_unknown_output_and_invalid_envelope_strings_are_not_copied(self):
        response = {"status": "secret-output", "tool": "/private/secret-token", "payload": ["secret-output"]}
        detail = self.failure(subprocess.CompletedProcess([], 1, json.dumps(response).encode(), b"secret-output"))
        self.assertEqual(detail["status"], "UNKNOWN")
        self.assertFalse(detail["tool_matches"])
        self.assertFalse(detail["payload_object"])
        self.assertNotIn("client_reason", detail)

    def test_timeout_and_os_error_do_not_expose_command_or_exception_text(self):
        detail = self.failure(error=subprocess.TimeoutExpired(["/private/secret-token"], 12,
            output=b"secret-output", stderr=b"secret-output"), error_type=subprocess.TimeoutExpired)
        self.assertEqual(detail["kind"], "TIMEOUT")
        self.assertIsNone(detail["returncode"])
        self.assertEqual(detail["stdout_bytes"], len(b"secret-output"))
        for error_type, number in ((PermissionError, 13), (FileNotFoundError, 2), (OSError, 5)):
            with self.subTest(error_type=error_type):
                detail = self.failure(error=error_type(number, "secret-output", "/private/secret-token"),
                    error_type=error_type)
                self.assertEqual(detail["kind"], "OS_ERROR")
                self.assertEqual(detail["os_errno"], number)

    def test_failed_reader_keeps_original_pin_sequence_and_cannot_mask_failure_type(self):
        # The old code only re-pinned after a reader returned. Adding a pin on
        # exception could turn a timeout into a typed, retryable missing file.
        for error in (subprocess.TimeoutExpired(["/private/secret-token"], 12),
                PermissionError(13, "secret-output", "/private/secret-token")):
            with self.subTest(error_type=type(error)), mock.patch.object(gateway, "pinned", side_effect=[
                    Path("/fixture/heptactl"), FileNotFoundError(2, "secret-output")]) as pin:
                self.failure(error=error, error_type=type(error))
                pin.assert_called_once_with(self.plan["reader"])


class GatewayReadinessObservationTests(unittest.TestCase):
    def setUp(self):
        self.clock = 0.0
        self.binary = {"path": "/fixture/candidate", "sha256": "a" * 64}
        self.observed = {"status": {"MainPID": "777", "InvocationID": "new-invocation", "NRestarts": "0"},
            "pid_key": ("777", "123"), "binary": self.binary}
        self.plan = {"reader": {"path": "/fixture/heptactl", "sha256": "b" * 64}}
        self.snapshot = {"environment": {"HEPTA_TOOL_SOCKET": "/private/gateway.sock"},
            "owners": [{"token_path": "/private/secret-token"}],
            "deadline": 1000000 + gateway.MIN_TTL_MS + 3600000}
        self.state = {"gateway_started": copy.deepcopy(self.observed)}
        self.context = mock.Mock()
        self.patches = contextlib.ExitStack(); self.addCleanup(self.patches.close)
        self.patches.enter_context(mock.patch.object(gateway, "time", types.SimpleNamespace(
            monotonic=lambda: self.clock, sleep=self.sleep)))
        self.patches.enter_context(mock.patch.object(gateway, "now_ms", lambda: 1000000 + int(self.clock * 1000)))
        self.patches.enter_context(mock.patch.object(gateway, "pinned", return_value=Path("/fixture/heptactl")))
        self.observe = self.patches.enter_context(mock.patch.object(gateway, "gateway", return_value=self.observed))
        self.control = self.patches.enter_context(mock.patch.object(gateway.driver, "ctl"))
        self.rpc = self.patches.enter_context(mock.patch.object(gateway, "rpc"))

    def sleep(self, seconds):
        self.clock += seconds

    @staticmethod
    def pending():
        return subprocess.CompletedProcess([], 4, b"", b"SOCKET_CONNECT_FAILED\n")

    @staticmethod
    def ready():
        return subprocess.CompletedProcess([], 0,
            b'{"status":"ok","tool":"system.get_health","payload":{"fixture":true}}', b"")

    def wait(self):
        return gateway.wait_gateway_readiness(self.plan, self.snapshot, self.state, self.context)

    def test_immediate_ready_preserves_state_and_performs_only_one_read(self):
        original = copy.deepcopy((self.plan, self.snapshot, self.state))
        with mock.patch.object(gateway.subprocess, "run", return_value=self.ready()) as run:
            self.assertIsNone(self.wait())
        self.assertEqual(run.call_count, 1)
        self.assertEqual(self.context.call_count, 2)
        self.assertEqual(self.observe.call_count, 2)
        self.assertEqual(run.call_args.args[0][-2:], ["call", "system.get_health"])
        self.assertEqual(run.call_args.kwargs["timeout"], 12)
        self.assertEqual((self.plan, self.snapshot, self.state), original)
        self.rpc.assert_not_called(); self.control.assert_not_called()
        self.assertEqual(self.clock, 0)

    def test_exact_connect_failure_waits_for_same_process_without_control_replay(self):
        with mock.patch.object(gateway.subprocess, "run", side_effect=[
                self.pending(), self.pending(), self.ready()]) as run:
            self.wait()
        self.assertEqual(run.call_count, 3)
        self.assertEqual(self.context.call_count, 6)
        self.assertEqual(self.observe.call_count, 6)
        self.assertAlmostEqual(self.clock, 0.4)
        self.rpc.assert_not_called(); self.control.assert_not_called()

    def test_rejections_bad_replies_other_client_errors_and_wrong_rc_do_not_retry(self):
        rejection = {"status": "error", "tool": "system.get_health", "reason_code": "READ_TOOL_FAILED",
            "detail": "unready is not a connection failure", "order_id": -1, "payload": None}
        failures = [
            subprocess.CompletedProcess([], 6, b"", b"SOCKET_CONNECT_FAILED\n"),
            subprocess.CompletedProcess([], 4, b"{}", b"SOCKET_CONNECT_FAILED\n"),
            subprocess.CompletedProcess([], 4, b"", b"SOCKET_CONNECT_FAILED"),
            subprocess.CompletedProcess([], 4, b"", b"FRAME_HEADER_TIMEOUT\n"),
            subprocess.CompletedProcess([], 4, b"", b"DISCOVERY_LIST_REJECTED\n"),
            subprocess.CompletedProcess([], 9, json.dumps(rejection).encode(), b""),
            subprocess.CompletedProcess([], 0, b"malformed-json", b""),
        ]
        for result in failures:
            with self.subTest(rc=result.returncode, stderr=result.stderr):
                with mock.patch.object(gateway.subprocess, "run", return_value=result) as run:
                    with self.assertRaisesRegex(RuntimeError, "NATIVE_READ_NOT_CONFIRMED"):
                        self.wait()
                run.assert_called_once()
                self.assertEqual(self.clock, 0)
        self.rpc.assert_not_called(); self.control.assert_not_called()

    def test_os_error_and_timeout_remain_immediate_failures(self):
        for error in (PermissionError(13, "private process"), FileNotFoundError(2, "private reader"),
                subprocess.TimeoutExpired(["private reader"], 12)):
            with self.subTest(kind=type(error).__name__):
                with mock.patch.object(gateway.subprocess, "run", side_effect=error) as run:
                    with self.assertRaises(type(error)):
                        self.wait()
                run.assert_called_once()
                self.assertEqual(self.clock, 0)

    def test_matching_text_without_structured_reader_provenance_does_not_retry(self):
        try:
            with mock.patch.object(gateway.subprocess, "run", return_value=self.pending()):
                gateway.read_tool(self.plan, self.snapshot["environment"], self.snapshot["owners"][0], "system.get_health")
        except RuntimeError as error:
            message = str(error)
        with mock.patch.object(gateway, "read_tool", side_effect=RuntimeError(message)) as read:
            with self.assertRaisesRegex(RuntimeError, "SOCKET_CONNECT_FAILED"):
                self.wait()
            read.assert_called_once()
        self.assertEqual(self.clock, 0)

    def test_persistent_connect_failure_has_bounded_reads_and_probe_timeout(self):
        with mock.patch.object(gateway, "GATEWAY_READINESS_TIMEOUT_SECONDS", 0.45):
            with mock.patch.object(gateway.subprocess, "run", return_value=self.pending()) as run:
                with self.assertRaisesRegex(RuntimeError, "GATEWAY_READINESS_TIMEOUT.*SOCKET_CONNECT_FAILED"):
                    self.wait()
        self.assertEqual(run.call_count, 3)
        budgets = [item.kwargs["timeout"] for item in run.call_args_list]
        self.assertAlmostEqual(budgets[0], 0.45)
        self.assertAlmostEqual(budgets[-1], 0.05)
        self.assertAlmostEqual(self.clock, 0.45)
        self.rpc.assert_not_called(); self.control.assert_not_called()

    def test_marker_or_native_context_drift_aborts_before_another_probe(self):
        self.context.side_effect = [None, RuntimeError("MAINTENANCE_MARKERS_CHANGED")]
        with mock.patch.object(gateway.subprocess, "run", return_value=self.pending()) as run:
            with self.assertRaisesRegex(RuntimeError, "MAINTENANCE_MARKERS_CHANGED"):
                self.wait()
        run.assert_called_once()
        self.assertEqual(self.clock, 0)

    def test_pid_invocation_restart_or_image_drift_never_becomes_a_new_start(self):
        for field, value in (("MainPID", "778"), ("InvocationID", "other"), ("NRestarts", "1"),
                ("pid_key", ("777", "different-start-ticks")),
                ("binary", dict(self.binary, sha256="c" * 64))):
            with self.subTest(field=field):
                changed = copy.deepcopy(self.observed)
                (changed["status"] if field in changed["status"] else changed)[field] = value
                self.observe.side_effect = [self.observed, changed]
                with mock.patch.object(gateway.subprocess, "run", return_value=self.pending()) as run:
                    with self.assertRaisesRegex(RuntimeError, "GATEWAY_PROCESS_CHANGED"):
                        self.wait()
                run.assert_called_once()
                self.assertEqual(self.clock, 0)
        self.control.assert_not_called()

    def test_existing_lease_budget_bounds_probe_and_is_not_extended(self):
        self.snapshot["deadline"] = 1000000 + gateway.MIN_TTL_MS + 100
        before = self.snapshot["deadline"]
        def pending(argv, **kwargs):
            self.assertAlmostEqual(kwargs["timeout"], 0.1)
            self.clock += 0.1
            return self.pending()
        with mock.patch.object(gateway.subprocess, "run", side_effect=pending) as run:
            with self.assertRaisesRegex(RuntimeError, "MAINTENANCE_TTL_BUDGET_EXHAUSTED"):
                self.wait()
        run.assert_called_once()
        self.assertEqual(self.snapshot["deadline"], before)
        self.assertAlmostEqual(self.clock, 0.1)

    def test_expired_or_changed_deadline_is_not_replaced_with_a_new_budget(self):
        self.snapshot["deadline"] = 1000000 + gateway.MIN_TTL_MS
        with mock.patch.object(gateway.subprocess, "run") as run:
            with self.assertRaisesRegex(RuntimeError, "MAINTENANCE_TTL_BUDGET_EXHAUSTED"):
                self.wait()
            run.assert_not_called()
        self.snapshot["deadline"] += 100000
        self.context.reset_mock()
        def change():
            if self.context.call_count == 2:
                self.snapshot["deadline"] += 1000
        self.context.side_effect = change
        with mock.patch.object(gateway.subprocess, "run", return_value=self.pending()) as run:
            with self.assertRaisesRegex(RuntimeError, "GATEWAY_READINESS_DEADLINE_CHANGED"):
                self.wait()
            run.assert_called_once()


class ControllerStartupObservationTests(unittest.TestCase):
    def setUp(self):
        self.clock = 0.0
        self.observed = {"status": {"MainPID": "777", "InvocationID": "new-invocation", "ActiveState": "active"},
            "pid_key": ("777", "123"), "manifest": "/fixture/original/manifest.json", "manifest_sha256": "a" * 64}
        self.owner = {"unit": "fixture-driver.service", "instrument": "EUR.USD", "resident": self.observed}
        self.patches = contextlib.ExitStack(); self.addCleanup(self.patches.close)
        self.patches.enter_context(mock.patch.object(gateway, "time", types.SimpleNamespace(
            monotonic=lambda: self.clock, sleep=self.sleep)))
        self.status = self.patches.enter_context(mock.patch.object(gateway.driver.fence, "show_unit",
            return_value={"ActiveState": "active", "MainPID": "777"}))

    def sleep(self, seconds):
        self.clock += seconds

    def test_known_initial_exec_observation_races_retry_only_until_first_strict_identity(self):
        for error in (RuntimeError("DRIVER_NOT_ACTIVE"), RuntimeError("RUNNING_DRIVER_ARGV_MISMATCH"),
                      RuntimeError("DRIVER_CHANGED_DURING_INSPECTION"), FileNotFoundError("process exec")):
            with self.subTest(error=str(error)):
                self.clock = 0.0
                with mock.patch.object(gateway.driver, "running_driver", side_effect=[error, self.observed]) as observe:
                    self.assertEqual(gateway.observe_controller_start(self.owner), self.observed)
                    self.assertEqual(observe.call_count, 2)
                self.assertEqual(self.clock, 0.2)

    def test_initial_exec_wait_is_bounded_and_failed_service_does_not_wait(self):
        with mock.patch.object(gateway.driver, "running_driver", side_effect=RuntimeError("DRIVER_NOT_ACTIVE")):
            with self.assertRaisesRegex(RuntimeError, "CONTROLLER_STARTUP_NOT_OBSERVED"):
                gateway.observe_controller_start(self.owner)
        self.assertGreaterEqual(self.clock, 30)
        self.assertLessEqual(self.clock, 30.3)
        self.clock = 0.0
        self.status.return_value = {"ActiveState": "failed", "MainPID": "0"}
        with mock.patch.object(gateway.driver, "running_driver", side_effect=RuntimeError("DRIVER_NOT_ACTIVE")) as observe:
            with self.assertRaisesRegex(RuntimeError, "CONTROLLER_START_FAILED"):
                gateway.observe_controller_start(self.owner)
            observe.assert_called_once()
        self.assertEqual(self.clock, 0)

    def test_first_verified_identity_disables_all_transient_retries(self):
        for error in (RuntimeError("DRIVER_NOT_ACTIVE"), RuntimeError("RUNNING_DRIVER_ARGV_MISMATCH"),
                      RuntimeError("DRIVER_CHANGED_DURING_INSPECTION"), FileNotFoundError("process lost"),
                      PermissionError("process no longer readable")):
            with self.subTest(error=str(error)):
                with mock.patch.object(gateway.driver, "running_driver", side_effect=error) as observe:
                    with self.assertRaises(type(error)):
                        gateway.observe_controller_start(self.owner, self.observed)
                    observe.assert_called_once()
                self.assertEqual(self.clock, 0)

    def test_wrong_release_permissions_or_process_identity_are_immediate_refusals(self):
        for field, value in (("manifest", "/fixture/another/manifest.json"), ("manifest_sha256", "b" * 64)):
            with self.subTest(field=field):
                with mock.patch.object(gateway.driver, "running_driver", return_value=dict(self.observed, **{field: value})) as observe:
                    with self.assertRaisesRegex(RuntimeError, "ORIGINAL_CONTROLLER_RELEASE_CHANGED"):
                        gateway.observe_controller_start(self.owner)
                    observe.assert_called_once()
        with mock.patch.object(gateway.driver, "running_driver", side_effect=PermissionError("unreadable")) as observe:
            with self.assertRaises(PermissionError):
                gateway.observe_controller_start(self.owner)
            observe.assert_called_once()
        with mock.patch.object(gateway.driver, "running_driver", return_value=dict(self.observed, pid_key=("777", "999"))) as observe:
            with self.assertRaisesRegex(RuntimeError, "DRIVER_PROCESS_CHANGED"):
                gateway.observe_controller_start(self.owner, self.observed)
            observe.assert_called_once()
        self.assertEqual(self.clock, 0)


class GatewayStartupObservationTests(unittest.TestCase):
    def setUp(self):
        self.clock = 0.0
        self.binary = {"path": "/fixture/candidate", "sha256": "a" * 64}
        self.observed = {"status": {"MainPID": "777", "InvocationID": "new-invocation", "NRestarts": "0"},
            "pid_key": ("777", "123"), "binary": self.binary}
        self.patches = contextlib.ExitStack(); self.addCleanup(self.patches.close)
        self.patches.enter_context(mock.patch.object(gateway, "time", types.SimpleNamespace(
            monotonic=lambda: self.clock, sleep=self.sleep)))
        self.start = self.patches.enter_context(mock.patch.object(gateway.driver, "ctl"))

    def sleep(self, seconds):
        self.clock += seconds

    def test_transient_initial_process_read_then_strict_stable_observation_succeeds(self):
        for error in (PermissionError("temporary exec permissions"), FileNotFoundError("temporary proc path"),
                      RuntimeError("GATEWAY_ARGV_NOT_EXACT_BINARY")):
            with self.subTest(error=type(error).__name__):
                self.clock = 0.0; self.start.reset_mock()
                first = True
                def observe(binary):
                    nonlocal first
                    if first:
                        first = False; raise error
                    return self.observed
                with mock.patch.object(gateway, "gateway", side_effect=observe) as observations:
                    self.assertEqual(gateway.start_gateway(self.binary), self.observed)
                    self.assertGreater(observations.call_count, 2)
                self.assertGreaterEqual(self.clock, 3)
                self.assertLess(self.clock, 35)
                self.start.assert_called_once_with("start", gateway.GATEWAY)

    def test_continuous_initial_process_errors_are_bounded_by_existing_deadline(self):
        for error in (PermissionError("still unavailable"), RuntimeError("GATEWAY_ARGV_NOT_EXACT_BINARY")):
            with self.subTest(error=str(error)):
                self.clock = 0.0; self.start.reset_mock()
                with mock.patch.object(gateway, "gateway", side_effect=error) as observations:
                    with self.assertRaisesRegex(RuntimeError, "GATEWAY_STARTUP_NOT_STABLE"):
                        gateway.start_gateway(self.binary)
                    self.assertGreater(observations.call_count, 1)
                self.assertGreaterEqual(self.clock, 35)
                self.assertLessEqual(self.clock, 35.3)
                self.start.assert_called_once_with("start", gateway.GATEWAY)

    def test_read_failure_after_first_verified_image_is_immediate_refusal(self):
        for error in (PermissionError("permission lost"), FileNotFoundError("process disappeared"),
                      RuntimeError("GATEWAY_ARGV_NOT_EXACT_BINARY")):
            with self.subTest(error=type(error).__name__):
                self.clock = 0.0; self.start.reset_mock()
                with mock.patch.object(gateway, "gateway", side_effect=[self.observed, error]) as observations:
                    with self.assertRaises(type(error)):
                        gateway.start_gateway(self.binary)
                    self.assertEqual(observations.call_count, 2)
                self.assertEqual(self.clock, 0.2)
                self.start.assert_called_once_with("start", gateway.GATEWAY)

    def test_initial_image_and_configuration_mismatches_remain_immediate(self):
        for reason in ("GATEWAY_RUNNING_IMAGE_MISMATCH", "GATEWAY_PROCESS_EXE_MISMATCH",
                       "GATEWAY_BINARY_MISMATCH", "GATEWAY_EXECSTART_MISMATCH",
                       "GATEWAY_CHANGED_DURING_INSPECTION"):
            with self.subTest(reason=reason):
                self.clock = 0.0; self.start.reset_mock()
                with mock.patch.object(gateway, "gateway", side_effect=RuntimeError(reason)) as observations:
                    with self.assertRaisesRegex(RuntimeError, reason):
                        gateway.start_gateway(self.binary)
                    observations.assert_called_once_with(self.binary)
                self.assertEqual(self.clock, 0.0)
                self.start.assert_called_once_with("start", gateway.GATEWAY)

    def test_actual_multiple_argument_snapshot_cannot_be_admitted_by_polling(self):
        status = {"ActiveState": "active", "SubState": "running", "MainPID": "777",
            "InvocationID": "new-invocation", "TriggeredBy": ""}
        raw = self.binary["path"].encode() + b"\0unexpected-argument\0"
        with mock.patch.object(gateway.driver, "unit_properties", return_value=status), mock.patch.object(
                gateway.driver, "proc_bytes", return_value=raw), mock.patch.object(
                gateway.driver, "interactive_process") as process:
            with self.assertRaisesRegex(RuntimeError, "GATEWAY_ARGV_NOT_EXACT_BINARY"):
                gateway.gateway(self.binary)
            with self.assertRaisesRegex(RuntimeError, "GATEWAY_STARTUP_NOT_STABLE"):
                gateway.start_gateway(self.binary)
            process.assert_not_called()
        self.assertGreaterEqual(self.clock, 35)
        self.assertLessEqual(self.clock, 35.3)
        self.start.assert_called_once_with("start", gateway.GATEWAY)


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
                    # The startup retry must not turn an ACTUAL stale image
                    # into a retryable /proc permission transition.
                    with mock.patch.object(gateway.driver, "ctl") as start, mock.patch.object(
                            gateway.time, "sleep") as sleep:
                        with self.assertRaisesRegex(RuntimeError, "INTERACTIVE_PROCESS_EXE_INVALID|GATEWAY_RUNNING_IMAGE_MISMATCH"):
                            gateway.start_gateway(pin)
                        start.assert_called_once_with("start", gateway.GATEWAY)
                        sleep.assert_not_called()
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
