"""Managed Broker proofs using temporary proc files and original socket parsing."""
import copy
import unittest
from unittest import mock

import test_activate_prepared_driver as fixtures
import activate_prepared_gateway as gateway

activation = fixtures.activation


class ManagedBrokerSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.InteractiveBrokerSnapshotTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        f = self.f
        group = f"/user.slice/user-{f.uid}.slice/user@{f.uid}.service/app.slice/{activation.BROKER_UNIT}"
        fixtures._put(f.proc / str(f.broker_pid) / "cgroup", ("0::" + group + "\n").encode())
        base = dict(f.current[activation.EXECUTION_UNIT], MainPID="0",
                    InvocationID="managed-broker-invocation", PartOf="")
        f.current[activation.BROKER_UNIT] = base
        launcher = str(f.root / "ibgateway-start.sh")
        self.start = ("{ path=" + launcher + " ; argv[]=" + launcher
            + " ; ignore_errors=no ; start_time=[Mon 2026-10-26 06:01:35 PDT] ; "
            "stop_time=[Mon 2026-10-26 06:01:36 PDT] ; pid=1403000 ; code=exited ; status=0 }")
        self.unit = dict(base, Id=activation.BROKER_UNIT, UnitFileState="static",
            ExecStart=self.start, ControlGroup=group, User="", Type="simple", ExitType="cgroup",
            RemainAfterExit="no", ControlPID="0", Job="", KillMode="control-group",
            SendSIGKILL="yes", Restart="no", Transient="no", Delegate="no", NeedDaemonReload="no", StopWhenUnneeded="no")
        f.units[activation.BROKER_UNIT] = self.unit

    def test_zero_main_pid_requires_actual_connected_java_and_never_mutates(self):
        f = self.f
        with mock.patch.object(activation.subprocess, "run") as mutation:
            proof = f._snapshot()
            self.assertEqual(proof, f._snapshot())
            mutation.assert_not_called()
        self.assertEqual(proof["managed_unit"]["MainPID"], "0")
        self.assertEqual(proof["managed_unit"]["ExitType"], "cgroup")
        self.assertEqual(proof["managed_unit"]["ControlGroup"], proof["broker_process"]["cgroup"])
        self.assertEqual(proof["broker_process"]["pid_key"], (str(f.broker_pid), "8029516"))
        self.assertEqual([proof["sockets"][key]["fds"] for key in ("execution", "accepted", "listener")],
                         [[9], [92], [87]])
        self.assertTrue(proof["broker_manual_stop_shield_claimed"])
        for key in ("unused_unit", "scope", "gnome_shell_process"):
            self.assertNotIn(key, proof)

    def test_nonzero_main_pid_must_be_the_same_proven_java(self):
        f = self.f
        f.current[activation.BROKER_UNIT]["MainPID"] = str(f.broker_pid)
        self.unit.update(MainPID=str(f.broker_pid), ExitType="main")
        self.assertEqual(f._snapshot()["managed_unit"]["MainPID"], str(f.broker_pid))

    def test_original_protection_owner_cgroup_and_lifetime_are_required(self):
        f = self.f
        for name, changed in (("Id", "other.service"), ("LoadState", "not-found"),
                ("ActiveState", "deactivating"), ("SubState", "exited"), ("InvocationID", ""),
                ("RefuseManualStop", "no"), ("User", str(f.uid + 1)),
                ("ControlGroup", f.execution_group), ("MainPID", str(f.execution_pid)),
                ("ControlPID", str(f.broker_pid)), ("Job", "19/start"),
                ("RemainAfterExit", "yes"), ("StopWhenUnneeded", "yes"), ("NeedDaemonReload", "yes"), ("ExitType", "main")):
            with self.subTest(property=name):
                original = self.unit[name]
                self.unit[name] = changed
                if name in activation.fence.SHOW_PROPS:
                    f.current[activation.BROKER_UNIT][name] = changed
                try:
                    with self.assertRaises(RuntimeError):
                        f._snapshot()
                finally:
                    self.unit[name] = original
                    if name in activation.fence.SHOW_PROPS:
                        f.current[activation.BROKER_UNIT][name] = original
        self.unit["InvocationID"] = "different-from-original-fence-snapshot"
        with self.assertRaisesRegex(RuntimeError, "MANAGED_BROKER_UNIT_NOT_PROTECTED"):
            f._snapshot()

    def test_unit_policy_or_invocation_change_during_inspection_refuses(self):
        original = activation.unit_properties
        for field, changed_value in (("Type", "exec"), ("KillMode", "process"),
                ("ControlGroup", self.f.execution_group), ("SendSIGKILL", "no"),
                ("InvocationID", "restarted"), ("RefuseManualStop", "no"),
                ("ExecStart", self.start.replace("ignore_errors=no", "ignore_errors=yes"))):
            with self.subTest(field=field):
                reads = 0
                def changed(unit, *args):
                    nonlocal reads
                    result = original(unit, *args)
                    if unit == activation.BROKER_UNIT:
                        reads += 1
                        if reads > 1:
                            result[field] = changed_value
                    return result
                with mock.patch.object(activation, "unit_properties", side_effect=changed):
                    with self.assertRaisesRegex(RuntimeError, "MANAGED_BROKER_UNIT_"):
                        self.f._snapshot()

    def test_launcher_reload_diagnostics_do_not_mask_command_changes(self):
        expected = self.f._snapshot()
        self.unit["ExecStart"] = self.start.replace(
            "start_time=[Mon 2026-10-26 06:01:35 PDT]", "start_time=[n/a]").replace(
            "stop_time=[Mon 2026-10-26 06:01:36 PDT]", "stop_time=[n/a]").replace("pid=1403000", "pid=0")
        self.assertEqual(self.f._snapshot(), expected)
        self.unit["ExecStart"] = self.start.replace("ibgateway-start.sh", "changed-broker-start.sh")
        self.assertNotEqual(self.f._snapshot(), expected)
        self.unit["ExecStart"] = self.start + " " + self.start
        with self.assertRaisesRegex(RuntimeError, "MANAGED_BROKER_EXECSTART_INVALID"):
            self.f._snapshot()

    def test_java_keeps_original_socket_and_stop_edge_checks(self):
        f = self.f
        f.test_listener_and_reverse_accepted_socket_must_both_belong_to_given_pid()
        f.test_every_outgoing_stop_edge_and_nondefault_start_conflict_refuses()
        f.test_fd_reuse_mid_inspection_is_rejected()
        fixtures._put(f.proc / str(f.broker_pid) / "cgroup", ("0::" + f.scope_group + "\n").encode())
        with self.assertRaisesRegex(RuntimeError, "MANAGED_BROKER_UNIT_NOT_PROTECTED"):
            f._snapshot()

    def test_java_replacement_during_inspection_is_rejected(self):
        original = activation.unit_properties
        def replace_process(unit, *args):
            result = original(unit, *args)
            if unit == self.f.selected:
                path = self.f.proc / str(self.f.broker_pid) / "stat"
                fixtures._put(path, path.read_bytes().replace(b"8029516", b"8029517"))
            return result
        with mock.patch.object(activation, "unit_properties", side_effect=replace_process):
            with self.assertRaisesRegex(RuntimeError, "PROCESS_CHANGED_DURING_INSPECTION"):
                self.f._snapshot()

    def test_gateway_reconnect_preserves_managed_unit_and_process_identity(self):
        proof = self.f._snapshot()
        connection = copy.deepcopy(proof)
        connection["sockets"]["execution"].update(inode=900, fds=[19], local=("127.0.0.1", 50000))
        connection["sockets"]["accepted"].update(inode=901, fds=[192], remote=("127.0.0.1", 50000))
        expected = gateway.interactive_process_identity(proof)
        self.assertEqual(gateway.interactive_process_identity(connection), expected)
        for field in ("InvocationID", "ControlGroup", "ExecStart", "RefuseManualStop", "ExitType"):
            changed = copy.deepcopy(connection)
            changed["managed_unit"][field] = "changed"
            self.assertNotEqual(gateway.interactive_process_identity(changed), expected)
        connection["broker_process"]["pid_key"] = (str(self.f.broker_pid), "reused")
        self.assertNotEqual(gateway.interactive_process_identity(connection), expected)


if __name__ == "__main__":
    unittest.main()
