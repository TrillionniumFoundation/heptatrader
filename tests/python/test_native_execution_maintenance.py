from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import activate_prepared_gateway as gateway
import native_execution_maintenance as native
import test_activate_prepared_gateway as gateway_tests


class NativeHandoffTests(unittest.TestCase):
    def setUp(self):
        self.h = gateway_tests.GatewayUpgradeTests(methodName="runTest")
        self.h.setUp()
        self.addCleanup(self.h.doCleanups)
        self.addCleanup(self.h.tearDown)
        h = self.h
        self.original_binary = {"path": "/fixture/native-original", "sha256": "1" * 64}
        self.candidate_binary = {"path": "/fixture/native-candidate", "sha256": "2" * 64}
        self.current_binary = self.original_binary
        self.original_pid = h.units[native.SERVICE]["MainPID"]
        self.fail_candidate = False
        self.pending_native_stop = False
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
            return {"Job": "pending" if self.pending_native_stop and self.h.units[unit]["ActiveState"] == "deactivating" else ""}
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

    def run_upgrade(self, *, resume=False, fallback=False):
        return gateway.maintenance_upgrade(self.h.plan, "a" * 64, home=self.h.home,
            resume=resume, native_fallback=fallback)

    def checkpoint(self):
        return json.loads((self.h.root / "operator-maintenance" / gateway.CHECKPOINT).read_text())

    def assert_held(self):
        self.assertEqual(self.h.units[gateway.GATEWAY]["ActiveState"], "inactive")
        self.assertEqual(self.h.units[self.h.driver_unit]["ActiveState"], "inactive")
        self.assertEqual(self.h.resume_count, 0)
        self.assertEqual(self.path(native.SERVICE).read_bytes(), gateway.HELD_STOP)
        for unit in native.SOCKETS:
            self.assertEqual(self.path(unit).read_bytes(), native.SOCKET_HELD)

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
        self.pending_native_stop = False
        self.finish_stop()
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

    def test_lost_unsubmitted_start_resumes_exact_selected_binary(self):
        self.lose_start = True
        result = self.run_upgrade()
        self.assertEqual(result["status"], "HELD", result)
        self.assert_held()
        result = self.run_upgrade(resume=True)
        self.assertEqual(result["status"], "COMPLETE", result)
        self.assertEqual(self.current_binary, self.candidate_binary)

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

    def test_partial_start_with_only_sockets_resumes_idempotently(self):
        self.partial_start_mode = "sockets_only"
        self.assertEqual(self.run_upgrade()["status"], "HELD")
        result = self.run_upgrade(resume=True)
        self.assertEqual(result["status"], "COMPLETE", result)
        self.assertEqual(self.current_binary, self.candidate_binary)

    def test_running_native_without_events_socket_is_not_accepted(self):
        self.partial_start_mode = "service_one_socket_persistent"
        result = self.run_upgrade()
        self.assertEqual(result["status"], "HELD", result)
        self.assertIn("NATIVE_SOCKET_GROUP_NOT_READY", result["error"])
        self.assert_held()
        self.assertFalse(self.checkpoint()["native_execution"].get("accepted", False))
        self.partial_start_mode = None
        result = self.run_upgrade(resume=True)
        self.assertEqual(result["status"], "COMPLETE", result)

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


if __name__ == "__main__":
    unittest.main()
