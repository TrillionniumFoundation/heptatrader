from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import activate_prepared_gateway as gateway
import native_execution_maintenance as native
import test_activate_prepared_gateway as gateway_tests


REQUIRE_STOP_AUTHORITY = native.require_stop_authority


class NativeHandoffTests(unittest.TestCase):
    def setUp(self):
        self.h = gateway_tests.GatewayUpgradeTests(methodName="runTest")
        self.h.setUp()
        self.addCleanup(self.h.doCleanups)
        self.addCleanup(self.h.tearDown)
        h = self.h
        # Exercise the retained internal recovery state machine only. This mock
        # is not a native stop-permit producer and grants no production scope.
        h.patches.enter_context(mock.patch.object(native, "require_stop_authority", return_value=None))
        self.original_binary = {"path": "/fixture/native-original", "sha256": "1" * 64}
        self.candidate_binary = {"path": "/fixture/native-candidate", "sha256": "2" * 64}
        self.current_binary = self.original_binary
        self.original_pid = h.units[native.SERVICE]["MainPID"]
        self.fail_candidate = False
        self.pending_native_stop = False
        self.pending_native_start = False
        self.lose_stop = False
        self.lose_start = False
        self.lose_start_after_effect = False
        self.leave_socket = False
        self.same_epoch = False
        self.partial_start_mode = None
        self.native_epoch = "fixture-service-epoch"
        self.base_ctl = h.ctl
        self.base_properties = h.properties
        self.base_native_call = h.native_call
        self.original_image = self.image(gateway, h.units, self.original_binary, "1" * 40)
        h.plan["native_execution"] = {"source_sha": "2" * 40, "binary": self.candidate_binary}
        h.snapshot["native_execution"] = {
            "original": self.original_image,
            "candidate": h.plan["native_execution"],
            "unit_files": {unit: {"fixture": "original-config"} for unit in native.UNITS},
            "topology": {unit: {"same": True} for unit in native.UNITS},
            "service_policy": {unit: self.original_policy(unit) for unit in native.UNITS},
            "original_release_bytes": None,
        }
        for obj, name, replacement in (
                (native, "image", self.image),
                (native, "topology", lambda *args: {"same": True}),
                (native, "broker_unchanged", lambda *args: None),
                (gateway.driver, "ctl", self.ctl),
                (gateway.driver, "unit_properties", self.properties),
                (gateway.subprocess, "run", self.native_call)):
            h.patches.enter_context(mock.patch.object(obj, name, replacement))

    def original_policy(self, unit):
        return ({"RefuseManualStop": "yes", "SendSIGKILL": "yes", "TimeoutStopUSec": "35s", "Restart": "on-failure"}
            if unit == native.SERVICE else {"RefuseManualStop": "yes"})

    def path(self, unit, name=native.POLICY):
        return self.h.guards[unit].with_name(name)

    def properties(self, unit, names, reason):
        if unit not in native.UNITS:
            return self.base_properties(unit, names, reason)
        if names == ("Job",):
            pending = ((self.pending_native_stop and self.h.units[unit]["ActiveState"] == "deactivating")
                or (self.pending_native_start and unit == native.SERVICE))
            return {"Job": "pending" if pending else ""}
        path = self.path(unit)
        value = self.original_policy(unit)
        if path.exists():
            raw = path.read_bytes()
            value["RefuseManualStop"] = "no" if b"RefuseManualStop=no" in raw else "yes"
            if unit == native.SERVICE:
                value.update(SendSIGKILL="no", TimeoutStopUSec="infinity", Restart="no")
        return {key: value[key] for key in names}

    def image(self, api, statuses, binary, source):
        status = statuses[native.SERVICE]
        if status["ActiveState"] != "active" or status["MainPID"] == "0":
            raise RuntimeError("NATIVE_EXECUTION_NOT_RUNNING")
        if binary != self.current_binary:
            raise RuntimeError("NATIVE_EXECUTION_RUNNING_IMAGE_MISMATCH")
        process = {"pid_key": self.h.alive[status["MainPID"]], "exe": binary["path"], "exe_sha256": binary["sha256"]}
        return {"status": copy.deepcopy(status), "process": process, "binary": copy.deepcopy(binary),
            "source_sha": source, "profile": {"held": True}, "pid_key": process["pid_key"]}

    def finish_stop(self):
        for unit in native.UNITS:
            if unit == native.SOCKETS[0] and self.leave_socket:
                continue
            status = self.h.units[unit]
            self.h.alive.pop(status["MainPID"], None)
            status.update(ActiveState="inactive", SubState="dead", MainPID="0", InvocationID="")

    def ctl(self, *args):
        h = self.h
        if args == ("daemon-reload",):
            self.base_ctl(*args)
            for unit in native.UNITS:
                h.units[unit]["RefuseManualStop"] = self.properties(unit, ("RefuseManualStop",), "fixture")["RefuseManualStop"]
            return
        if args[:2] == ("stop", "--no-block") and set(args[2:]) == set(native.UNITS):
            h.events.append(args)
            self.assertEqual(h.units[gateway.GATEWAY]["ActiveState"], "inactive")
            self.assertEqual(h.units[h.driver_unit]["ActiveState"], "inactive")
            self.assertTrue(h.rows[0]["recovery_only"])
            self.assertEqual(self.path(native.SERVICE).read_bytes(), gateway.SAFE_STOP)
            for unit in native.SOCKETS:
                self.assertEqual(self.path(unit).read_bytes(), native.SOCKET_STOP)
            if self.lose_stop:
                self.lose_stop = False
                raise RuntimeError("LOST_GROUP_STOP_BEFORE_SUBMIT")
            if self.pending_native_stop:
                h.units[native.SERVICE].update(ActiveState="deactivating", SubState="stop-sigterm")
            else:
                self.finish_stop()
            return
        if args[:2] == ("start", "--no-block") and set(args[2:]) == set(native.UNITS):
            h.events.append(args)
            self.assertEqual(self.path(native.SERVICE).read_bytes(), gateway.HELD_STOP)
            for unit in native.SOCKETS:
                self.assertEqual(self.path(unit).read_bytes(), native.SOCKET_HELD)
            if self.lose_start:
                self.lose_start = False
                raise RuntimeError("LOST_GROUP_START_BEFORE_SUBMIT")
            if self.pending_native_start:
                h.units[native.SERVICE].update(ActiveState="activating", SubState="start-pre", MainPID="0", InvocationID="pending-native-start")
                return
            raw = self.path(native.SERVICE, native.RELEASE).read_bytes()
            self.current_binary = self.original_binary if raw == native.release_bytes(self.original_binary) else self.candidate_binary
            started_units = native.UNITS
            if self.partial_start_mode == "sockets_only":
                started_units = native.SOCKETS
            elif self.partial_start_mode in ("service_one_socket", "service_one_socket_persistent"):
                started_units = (native.SERVICE, native.SOCKETS[0])
            new_service = h.units[native.SERVICE]["ActiveState"] != "active" and native.SERVICE in started_units
            for index, unit in enumerate(started_units):
                if h.units[unit]["ActiveState"] == "active":
                    continue  # systemctl start is idempotent for an active unit.
                pid = str(8000 + len(h.events) + index)
                h.alive[pid] = (pid, "native-start")
                h.units[unit].update(ActiveState="active", SubState="running", MainPID=pid, InvocationID="native-" + pid)
            if self.fail_candidate and self.current_binary == self.candidate_binary:
                pid = h.units[native.SERVICE]["MainPID"]
                h.alive.pop(pid)
                h.units[native.SERVICE].update(ActiveState="failed", SubState="failed", MainPID="0", InvocationID="")
            if new_service and not self.same_epoch:
                self.native_epoch = "new-native-epoch-" + str(len(h.events))
            if self.partial_start_mode == "sockets_only":
                self.partial_start_mode = None
                raise RuntimeError("LOST_PARTIAL_START_AFTER_SOCKETS")
            if self.partial_start_mode == "service_one_socket":
                self.partial_start_mode = None
            if self.lose_start_after_effect:
                self.lose_start_after_effect = False
                raise RuntimeError("LOST_START_REPLY_AFTER_EFFECT")
            return
        return self.base_ctl(*args)

    def native_call(self, argv, **kwargs):
        response = self.base_native_call(argv, **kwargs)
        value = json.loads(response.stdout)
        if "owner-audit" in argv:
            value["service_epoch"] = self.native_epoch
        elif "payload" in value:
            value["payload"]["execution_service_epoch"] = self.native_epoch
        return subprocess.CompletedProcess(argv, response.returncode, json.dumps(value).encode(), response.stderr)

    def run_upgrade(self, *, resume=False, fallback=False, observe_start=True):
        result = gateway.maintenance_upgrade(self.h.plan, "a" * 64, home=self.h.home,
            resume=resume, native_fallback=fallback)
        if observe_start and result.get("error", "").startswith("StartPending:"):
            # Existing state-machine fixtures make the mock service ready in
            # ctl(). Observe it in a NEW transaction, after the original lock
            # has been released. This does not submit another start.
            result = gateway.maintenance_upgrade(self.h.plan, "a" * 64, home=self.h.home,
                resume=True, native_fallback=False)
        return result

    def checkpoint(self):
        return json.loads((self.h.root / "operator-maintenance" / gateway.CHECKPOINT).read_text())

    def assert_held(self):
        self.assertEqual(self.h.units[gateway.GATEWAY]["ActiveState"], "inactive")
        self.assertEqual(self.h.units[self.h.driver_unit]["ActiveState"], "inactive")
        self.assertEqual(self.h.resume_count, 0)
        self.assertEqual(self.path(native.SERVICE).read_bytes(), gateway.HELD_STOP)
        for unit in native.SOCKETS:
            self.assertEqual(self.path(unit).read_bytes(), native.SOCKET_HELD)

    def retained_stopped_group(self):
        # The fixture's pre-existing stop was submitted before this recovery.
        # All subsequent start tests restore the real PR494 rejection.
        self.pending_native_stop = True
        result = self.run_upgrade(observe_start=False)
        self.assertIn("NORMAL_STOP_STILL_PENDING", result["error"])
        self.pending_native_stop = False
        self.finish_stop()

    def test_retained_start_returns_held_then_accepts_only_on_separate_observation(self):
        self.retained_stopped_group()
        previous = self.checkpoint()
        previous.update(rollback="INCOMPLETE_HELD", rollback_error="old failure")
        gateway.atomic_json(self.h.root / "operator-maintenance" / gateway.CHECKPOINT, previous)
        with mock.patch.object(native, "require_stop_authority", REQUIRE_STOP_AUTHORITY):
            result = self.run_upgrade(resume=True, observe_start=False)
        self.assertEqual(result["status"], "HELD", result)
        self.assertIn("NATIVE_GROUP_START_PENDING_RELEASE_OPERATOR_LOCK", result["error"])
        self.assertEqual(result["rollback"], "NOT_ATTEMPTED_NATIVE_START_PENDING")
        self.assertNotIn("rollback_error", self.checkpoint())
        self.assertFalse(result["native_execution"]["accepted"])
        self.assertEqual(self.checkpoint()["native_execution"]["phase"], "GROUP_START_REQUESTED")
        self.assert_held()
        self.assertFalse(gateway_tests.driver_tests._lock_busy(self.h.root / gateway.driver.latch.LOCK))
        starts = [event for event in self.h.events if event[:2] == ("start", "--no-block")]
        with mock.patch.object(native, "require_stop_authority", REQUIRE_STOP_AUTHORITY):
            result = self.run_upgrade(resume=True, observe_start=False)
        self.assertEqual(result["status"], "COMPLETE", result)
        self.assertEqual(starts, [event for event in self.h.events if event[:2] == ("start", "--no-block")])

    def test_early_resume_yields_pending_start_without_waiting_or_resubmitting(self):
        self.retained_stopped_group()
        self.pending_native_start = True
        with mock.patch.object(native, "require_stop_authority", REQUIRE_STOP_AUTHORITY):
            first = self.run_upgrade(resume=True, observe_start=False)
            before_clock, before_events = self.h.clock, list(self.h.events)
            second = self.run_upgrade(resume=True, observe_start=False)
        for result in (first, second):
            self.assertEqual(result["status"], "HELD", result)
            self.assertIn("StartPending:", result["error"])
            self.assertEqual(result["rollback"], "NOT_ATTEMPTED_NATIVE_START_PENDING")
        self.assertEqual(self.h.clock, before_clock)
        self.assertEqual(self.h.events, before_events)
        self.assert_held()

    @unittest.skipIf(os.geteuid() == 0, "actual startup owner checks require an unprivileged test user")
    def test_real_startup_hold_waits_for_controller_exit_and_preserves_exact_marker(self):
        runtime = self.h.home / "fixture-runtime"
        control = runtime / "heptatrader/control"
        control.mkdir(parents=True, mode=0o750)
        control.chmod(0o750)
        runtime.chmod(0o700); control.parent.chmod(0o700)
        self.h.kill.unlink()
        self.h.kill = gateway_tests.put(control / "kill-switch", b"", 0o440)
        self.h.markers = self.h.marker_identity(self.h.home)
        identity = lambda: (gateway.driver.latch._identity(self.h.kill.stat()), self.h.kill.read_bytes())
        before_marker = identity()
        self.retained_stopped_group()
        processes = []
        code = '''import importlib.util,json,sys
from pathlib import Path
spec=importlib.util.spec_from_file_location("fixture_actual_startup_hold",sys.argv[1])
latch=importlib.util.module_from_spec(spec);spec.loader.exec_module(latch)
original=latch.fcntl.flock
def observed(fd,operation):
 if operation==latch.fcntl.LOCK_EX: print("STARTUP_WAITING_OPERATOR_LOCK",flush=True)
 return original(fd,operation)
latch.fcntl.flock=observed
print(json.dumps(latch.perform("native-startup-hold",Path(sys.argv[2]),Path(sys.argv[3]))),flush=True)
'''
        def clean_child(process):
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)
        def start_with_real_latch(*args):
            if args[:2] != ("start", "--no-block") or set(args[2:]) != set(native.UNITS):
                return self.ctl(*args)
            self.h.events.append(args)
            retained = self.checkpoint()["native_execution"]
            self.assertTrue(retained["stopped"] and retained["start_requested"])
            self.assertEqual(retained["phase"], "GROUP_START_REQUESTED")
            self.assertEqual(self.path(native.SERVICE).read_bytes(), gateway.HELD_STOP)
            self.h.units[native.SERVICE].update(ActiveState="activating", SubState="start-pre", MainPID="0", InvocationID="fixture-pre")
            process = subprocess.Popen([sys.executable, "-I", "-B", "-c", code,
                str(Path(gateway.driver.latch.__file__).resolve()), str(control), str(self.h.root)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            processes.append(process); self.addCleanup(clean_child, process)
            ready, _, _ = select.select([process.stdout], [], [], 5)
            self.assertTrue(ready, "startup child never reached the original operator lock")
            self.assertEqual(process.stdout.readline().strip(), "STARTUP_WAITING_OPERATOR_LOCK")
            self.assertIsNone(process.poll())
            self.assertTrue(gateway_tests.driver_tests._lock_busy(self.h.root / gateway.driver.latch.LOCK))
        with mock.patch.object(native, "require_stop_authority", REQUIRE_STOP_AUTHORITY), \
                mock.patch.object(gateway.driver, "ctl", start_with_real_latch):
            before_clock = self.h.clock
            result = self.run_upgrade(resume=True, observe_start=False)
        self.assertEqual(result["status"], "HELD", result)
        self.assertIn("StartPending:", result["error"])
        self.assertEqual(self.h.clock, before_clock)
        self.assert_held()
        self.assertEqual(len(processes), 1)
        stdout, stderr = processes[0].communicate(timeout=5)
        self.assertEqual(processes[0].returncode, 0, stderr)
        startup = json.loads(stdout.strip())
        self.assertFalse(startup["created_marker"])
        self.assertEqual(startup["authorization_effect"], "BLOCK_RISK_INCREASE_ONLY")
        self.assertEqual(identity(), before_marker)
        self.assertFalse(gateway_tests.driver_tests._lock_busy(self.h.root / gateway.driver.latch.LOCK))
        # The same retained start now becomes observable. No ctl/start is sent.
        self.current_binary = self.candidate_binary
        self.native_epoch = "native-after-real-startup-hold"
        for index, unit in enumerate(native.UNITS):
            pid = str(88000 + index)
            self.h.alive[pid] = (pid, "original-group-start")
            self.h.units[unit].update(ActiveState="active", SubState="running", MainPID=pid, InvocationID="original-" + pid)
        with mock.patch.object(native, "require_stop_authority", REQUIRE_STOP_AUTHORITY):
            result = self.run_upgrade(resume=True, observe_start=False)
        self.assertEqual(result["status"], "COMPLETE", result)
        self.assertEqual(identity(), before_marker)
        self.assertEqual(len([event for event in self.h.events if event[:2] == ("start", "--no-block")]), 1)

    def test_complete_handoff_stops_socket_group_then_restores_same_owners(self):
        result = self.run_upgrade()
        self.assertEqual(result["status"], "COMPLETE", result)
        self.assertEqual(self.current_binary, self.candidate_binary)
        self.assertIn(("stop", "--no-block", *native.UNITS), self.h.events)
        self.assertEqual(self.h.resume_count, 1)
        self.assertTrue(self.checkpoint()["native_execution"]["accepted"])
        for unit in native.UNITS:
            self.assertFalse(self.path(unit).exists())

    def test_failed_candidate_keeps_gateway_and_controllers_held_then_restores_original(self):
        self.fail_candidate = True
        result = self.run_upgrade()
        self.assertEqual(result["status"], "HELD", result)
        self.assertIn("NATIVE_CANDIDATE_START_FAILED_HELD", result["error"])
        self.assert_held()
        result = self.run_upgrade(resume=True, fallback=True)
        self.assertEqual(result["status"], "COMPLETE", result)
        self.assertEqual(self.current_binary, self.original_binary)
        self.assertEqual(self.h.resume_count, 1)

    def test_pending_normal_stop_is_not_forced_and_resume_does_not_resend(self):
        self.pending_native_stop = True
        result = self.run_upgrade()
        self.assertEqual(result["status"], "HELD", result)
        self.assert_held()
        self.assertIn("NORMAL_STOP_STILL_PENDING", result["error"])
        with mock.patch.object(native, "require_stop_authority", REQUIRE_STOP_AUTHORITY):
            pending = self.run_upgrade(resume=True)
        self.assertEqual(pending["status"], "HELD", pending)
        self.assertIn("NORMAL_STOP_STILL_PENDING", pending["error"])
        self.assertEqual(self.h.events.count(("stop", "--no-block", *native.UNITS)), 1)
        self.pending_native_stop = False
        self.finish_stop()
        with mock.patch.object(native, "require_stop_authority", REQUIRE_STOP_AUTHORITY):
            result = self.run_upgrade(resume=True)
        self.assertEqual(result["status"], "COMPLETE", result)
        self.assertEqual(self.h.events.count(("stop", "--no-block", *native.UNITS)), 1)

    def test_lost_unsubmitted_stop_is_reobserved_and_reaudited(self):
        self.lose_stop = True
        result = self.run_upgrade()
        self.assertEqual(result["status"], "HELD", result)
        first_audit = self.h.audit_number
        result = self.run_upgrade(resume=True)
        self.assertEqual(result["status"], "COMPLETE", result)
        self.assertGreater(self.h.audit_number, first_audit)
        self.assertEqual(self.h.events.count(("stop", "--no-block", *native.UNITS)), 2)

    def test_lost_start_without_process_or_job_is_not_resubmitted(self):
        self.lose_start = True
        result = self.run_upgrade()
        self.assertEqual(result["status"], "HELD", result)
        self.assert_held()
        result = self.run_upgrade(resume=True)
        self.assertEqual(result["status"], "HELD", result)
        self.assertIn("NATIVE_GROUP_START_OUTCOME_UNCONFIRMED_HELD", result["error"])
        self.assertEqual(len([event for event in self.h.events if event[:2] == ("start", "--no-block")]), 1)
        self.assert_held()

    def test_live_unrecognized_candidate_is_never_killed_for_fallback(self):
        self.fail_candidate = True
        self.assertEqual(self.run_upgrade()["status"], "HELD")
        self.h.units[native.SERVICE].update(ActiveState="active", SubState="running", MainPID="77777", InvocationID="foreign")
        self.h.alive["77777"] = ("77777", "foreign")
        self.current_binary = {"path": "/foreign", "sha256": "f" * 64}
        result = self.run_upgrade(resume=True, fallback=True)
        self.assertEqual(result["status"], "HELD", result)
        self.assertEqual(self.h.units[native.SERVICE]["MainPID"], "77777")
        self.assertEqual(self.h.resume_count, 0)

    def test_socket_that_did_not_stop_prevents_candidate_selection(self):
        self.leave_socket = True
        result = self.run_upgrade()
        self.assertEqual(result["status"], "HELD", result)
        self.assertIn("NATIVE_SOCKET_STOP_STILL_PENDING", result["error"])
        self.assertFalse(self.path(native.SERVICE, native.RELEASE).exists())
        self.assert_held()

    def test_same_native_epoch_does_not_resume_gateway_or_owner(self):
        self.same_epoch = True
        result = self.run_upgrade()
        self.assertEqual(result["status"], "HELD", result)
        self.assertIn("NATIVE_CANDIDATE_SERVICE_EPOCH_NOT_NEW", result["error"])
        self.assert_held()

    def test_post_rename_failure_resumes_only_exact_owned_release(self):
        original = gateway.put_owned
        failed = [False]
        def fail_after(path, raw, expected=None):
            original(path, raw, expected)
            if path.name == native.RELEASE and not failed[0]:
                failed[0] = True
                raise OSError("FIXTURE_RENAME_PERSIST_REPLY_LOST")
        with mock.patch.object(gateway, "put_owned", fail_after):
            result = self.run_upgrade()
        self.assertEqual(result["status"], "HELD", result)
        result = self.run_upgrade(resume=True)
        self.assertEqual(result["status"], "COMPLETE", result)
        self.assertEqual(self.current_binary, self.candidate_binary)

    def test_foreign_release_after_failed_start_is_not_overwritten(self):
        self.fail_candidate = True
        self.assertEqual(self.run_upgrade()["status"], "HELD")
        path = self.path(native.SERVICE, native.RELEASE)
        path.write_bytes(b"foreign replacement\n")
        result = self.run_upgrade(resume=True, fallback=True)
        self.assertEqual(result["status"], "HELD", result)
        self.assertEqual(path.read_bytes(), b"foreign replacement\n")
        self.assertEqual(self.h.resume_count, 0)

    def test_partial_original_group_stop_completes_on_resume(self):
        self.leave_socket = True
        self.assertEqual(self.run_upgrade()["status"], "HELD")
        self.leave_socket = False
        result = self.run_upgrade(resume=True)
        self.assertEqual(result["status"], "COMPLETE", result)
        self.assertEqual(self.h.resume_count, 1)

    def test_fallback_stop_reply_lost_resumes_without_reissuing_fallback_selection(self):
        self.fail_candidate = True
        self.assertEqual(self.run_upgrade()["status"], "HELD")
        self.lose_stop = True
        self.assertEqual(self.run_upgrade(resume=True, fallback=True)["status"], "HELD")
        result = self.run_upgrade(resume=True)
        self.assertEqual(result["status"], "COMPLETE", result)
        self.assertEqual(self.current_binary, self.original_binary)

    def test_partial_start_with_only_sockets_does_not_resubmit_missing_service(self):
        self.partial_start_mode = "sockets_only"
        self.assertEqual(self.run_upgrade()["status"], "HELD")
        result = self.run_upgrade(resume=True)
        self.assertEqual(result["status"], "HELD", result)
        self.assertIn("NATIVE_GROUP_START_OUTCOME_UNCONFIRMED_HELD", result["error"])
        self.assertEqual(len([event for event in self.h.events if event[:2] == ("start", "--no-block")]), 1)

    def test_running_native_without_events_socket_is_not_accepted(self):
        self.partial_start_mode = "service_one_socket_persistent"
        result = self.run_upgrade()
        self.assertEqual(result["status"], "HELD", result)
        self.assertIn("NATIVE_SOCKET_GROUP_NOT_READY", result["error"])
        self.assert_held()
        self.assertFalse(self.checkpoint()["native_execution"].get("accepted", False))
        self.partial_start_mode = None
        result = self.run_upgrade(resume=True)
        self.assertEqual(result["status"], "HELD", result)
        self.assertIn("NATIVE_SOCKET_GROUP_NOT_READY", result["error"])
        self.assertEqual(len([event for event in self.h.events if event[:2] == ("start", "--no-block")]), 1)

    def test_missing_no_kill_policy_is_not_treated_as_a_pending_normal_stop(self):
        self.pending_native_stop = True
        self.assertEqual(self.run_upgrade()["status"], "HELD")
        self.path(native.SERVICE).unlink()
        self.ctl("daemon-reload")
        before = self.h.events.count(("stop", "--no-block", *native.UNITS))
        result = self.run_upgrade(resume=True)
        self.assertEqual(result["status"], "HELD", result)
        self.assertIn("NATIVE_PENDING_LIFECYCLE_POLICY_CHANGED", result["error"])
        self.assertIn("NATIVE_PENDING_LIFECYCLE_POLICY_MISSING", self.checkpoint()["native_protection_error"])
        self.assertEqual(self.h.events.count(("stop", "--no-block", *native.UNITS)), before)
        self.assertEqual(self.h.resume_count, 0)

    def test_lost_effective_start_then_lost_fallback_stop_remains_resumable(self):
        self.fail_candidate = True
        self.lose_start_after_effect = True
        self.assertEqual(self.run_upgrade()["status"], "HELD")
        self.assertNotIn("started_socket_statuses", self.checkpoint()["native_execution"])
        self.lose_stop = True
        self.assertEqual(self.run_upgrade(resume=True, fallback=True)["status"], "HELD")
        self.assertEqual(set(self.checkpoint()["native_execution"]["started_socket_statuses"]), set(native.SOCKETS))
        result = self.run_upgrade(resume=True)
        self.assertEqual(result["status"], "COMPLETE", result)
        self.assertEqual(self.current_binary, self.original_binary)

    def test_release_rejects_systemd_expansions_for_candidate_and_fallback(self):
        for suffix in (" space", "$PATH", "'quoted'", '"quoted"', "%h", "\\escape", "\nnext"):
            with self.subTest(suffix=suffix):
                with self.assertRaisesRegex(RuntimeError, "NATIVE_EXECUTION_UNIT_PATH_INVALID"):
                    native.release_bytes({"path": "/fixture/native" + suffix})
        self.assertIn(b"ExecStart=/fixture/native-original\n", native.release_bytes(self.original_binary))

    def test_preflight_refuses_native_before_any_artifact_or_runtime_read(self):
        for self_reported in ({}, {"maintenance_stop_permitted": True}):
            with self.subTest(self_reported=self_reported):
                api = mock.Mock()
                api.fail.side_effect = gateway.fail
                with mock.patch.object(native, "require_stop_authority", REQUIRE_STOP_AUTHORITY):
                    with self.assertRaisesRegex(RuntimeError, "NATIVE_EXECUTION_STOP_AUTHORITY_UNSUPPORTED"):
                        native.prepare(api, {"native_execution": self_reported}, None,
                            {"hold_runtime": {"maintenance_stop_permitted": True}})
                self.assertEqual(api.mock_calls, [mock.call.fail("NATIVE_EXECUTION_STOP_AUTHORITY_UNSUPPORTED")])

    def test_gateway_only_has_no_native_stop_authority_requirement(self):
        api = mock.Mock()
        with mock.patch.object(native, "require_stop_authority", REQUIRE_STOP_AUTHORITY):
            self.assertIsNone(native.prepare(api, {}, None, None))
            native.run(api, {}, None, {}, {}, mock.Mock(), mock.Mock())
        self.assertEqual(api.mock_calls, [])

    def test_new_or_fallback_stop_refuses_before_context_audit_or_checkpoint(self):
        states = [({}, False), ({"native_execution": {"phase": "PREPARED"}}, False),
            ({"native_execution": {"stop_requested": True, "start_requested": True}}, True)]
        for state, fallback in states:
            with self.subTest(state=state, fallback=fallback):
                before = copy.deepcopy(state)
                save, context = mock.Mock(), mock.Mock()
                with mock.patch.object(native, "require_stop_authority", REQUIRE_STOP_AUTHORITY), \
                        mock.patch.object(native, "audit_all") as audit:
                    with self.assertRaisesRegex(RuntimeError, "NATIVE_EXECUTION_STOP_AUTHORITY_UNSUPPORTED"):
                        native.run(gateway, self.h.plan, self.h.home, self.h.snapshot,
                            state, save, context, fallback=fallback)
                self.assertEqual(state, before)
                save.assert_not_called(); context.assert_not_called(); audit.assert_not_called()
        self.assertEqual(self.h.events, [])

    def test_unsubmitted_old_stop_intent_cannot_be_reset_or_reaudited(self):
        state = {"native_execution": {"stop_requested": True, "owned_policies": [], "phase": "GROUP_STOP_REQUESTED"}}
        before = copy.deepcopy(state)
        save, context = mock.Mock(), mock.Mock()
        with mock.patch.object(native, "require_stop_authority", REQUIRE_STOP_AUTHORITY), \
                mock.patch.object(gateway, "check_transition_catalog"), mock.patch.object(native, "audit_all") as audit:
            with self.assertRaisesRegex(RuntimeError, "NATIVE_EXECUTION_STOP_AUTHORITY_UNSUPPORTED"):
                native.run(gateway, self.h.plan, self.h.home, self.h.snapshot, state, save, context)
        self.assertEqual(state, before)
        save.assert_not_called(); audit.assert_not_called()
        self.assertEqual(self.h.events, [])

    def test_empty_group_resubmission_cannot_open_policies_or_submit_stop(self):
        self.h.alive.pop(self.original_pid)
        native_state = {"stop_requested": True, "owned_policies": []}
        for active_state in ("inactive", "failed"):
            with self.subTest(active_state=active_state):
                self.h.units[native.SERVICE].update(ActiveState=active_state, MainPID="0", InvocationID="")
                before = copy.deepcopy(native_state)
                save = mock.Mock()
                with mock.patch.object(native, "require_stop_authority", REQUIRE_STOP_AUTHORITY):
                    with self.assertRaisesRegex(RuntimeError, "NATIVE_EXECUTION_STOP_AUTHORITY_UNSUPPORTED"):
                        native.resume_empty_stop(gateway, self.h.home, self.h.snapshot, native_state, save)
                self.assertEqual(native_state, before)
                save.assert_not_called()
        self.assertEqual(self.h.events, [])
        self.assertFalse(any(self.path(unit).exists() for unit in native.UNITS))

    def test_direct_stop_policy_open_cannot_treat_checkpoint_as_authority(self):
        state = {"stop_requested": True, "accepted": True, "owned_policies": []}
        before = copy.deepcopy(state)
        save = mock.Mock()
        with mock.patch.object(native, "require_stop_authority", REQUIRE_STOP_AUTHORITY):
            with self.assertRaisesRegex(RuntimeError, "NATIVE_EXECUTION_STOP_AUTHORITY_UNSUPPORTED"):
                native.open_stop_policies(gateway, self.h.home, state, save)
        self.assertEqual(state, before)
        save.assert_not_called()
        self.assertEqual(self.h.events, [])
        self.assertFalse(any(self.path(unit).exists() for unit in native.UNITS))

    def test_already_accepted_native_retains_existing_gateway_recovery(self):
        state = {"native_execution": {"accepted": True}}
        before = copy.deepcopy(state)
        save, context = mock.Mock(), mock.Mock()
        with mock.patch.object(native, "require_stop_authority", REQUIRE_STOP_AUTHORITY):
            native.run(gateway, self.h.plan, self.h.home, self.h.snapshot, state, save, context)
        self.assertEqual(state, before)
        self.assertEqual(context.call_count, 2)
        save.assert_not_called()
        self.assertEqual(self.h.events, [])


if __name__ == "__main__":
    unittest.main()
