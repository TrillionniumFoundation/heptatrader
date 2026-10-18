"""Exact opt-in systemd risk-path stop-fence tests; no real unit operations."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import fcntl
import os
import subprocess
import threading
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import install_risk_stop_fence as fence
import hepta_maintenance_latch as latch


def fixture_home(path: Path):
    home = path / "alex"
    unit_dir = home / ".config/systemd/user"
    unit_dir.mkdir(parents=True)
    (home / ".local/state/heptatrader").mkdir(mode=0o700, parents=True)
    environment = home / ".config/heptatrader/execution.env"
    environment.parent.mkdir(exist_ok=True)
    environment.write_text(
        "HEPTA_IB_EXECUTION_MODE=PAPER\nHEPTA_IB_PAPER_PORT=4002\n"
    )
    environment.chmod(0o600)
    return home


def status(home, name):
    unit = home / ".config/systemd/user" / (name + ".d") / fence.GUARD_NAME
    armed = unit.exists() and unit.read_text() == fence.GUARD_TEXT
    return {
        "LoadState": "loaded",
        "ActiveState": "inactive" if name == "heptatrader.target" else "active",
        "SubState": "dead" if name == "heptatrader.target" else "running",
        "MainPID": "" if name == "heptatrader.target" else "31337",
        "InvocationID": "" if name == "heptatrader.target" else name + "-fixed",
        "RefuseManualStop": "yes" if armed else "no",
        "PartOf": "" if name in ("ibgateway.service", "heptatrader.target")
                  else "heptatrader.target",
    }


class RiskStopFenceContract(unittest.TestCase):
    def test_exact_chain_and_broker_scope(self):
        self.assertEqual(len(fence.RISK_UNITS), 9)
        self.assertIn("heptatrader-execution.socket", fence.RISK_UNITS)
        self.assertIn("heptatrader-events.socket", fence.RISK_UNITS)
        self.assertIn("heptatrader-runtime.service", fence.RISK_UNITS)
        self.assertIn("ibgateway.service", fence.RISK_UNITS)
        self.assertIn("heptatrader.target", fence.RISK_UNITS)
        self.assertEqual(fence.GUARD_TEXT, "[Unit]\nRefuseManualStop=yes\n")

    def test_wrong_broker_profile_refused(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "execution.env"
            p.write_text("HEPTA_IB_EXECUTION_MODE=LIVE\nHEPTA_IB_PAPER_PORT=4001\n")
            p.chmod(0o600)
            with self.assertRaisesRegex(fence.StopFenceError, "ONLY_EXISTING_PAPER"):
                fence.check_paper_config(p)

    def test_profile_duplicate_or_exposed_permissions_refused(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)/"execution.env"
            p.write_text("HEPTA_IB_EXECUTION_MODE=PAPER\nHEPTA_IB_PAPER_PORT=4002\n"
                         "HEPTA_IB_EXECUTION_MODE=LIVE\n")
            p.chmod(0o600)
            with self.assertRaisesRegex(fence.StopFenceError,"PAPER_PROFILE_DUPLICATE_FIELD"):
                fence.check_paper_config(p)
            p.write_text("HEPTA_IB_EXECUTION_MODE=PAPER\nHEPTA_IB_PAPER_PORT=4002\n")
            p.chmod(0o640)
            with self.assertRaisesRegex(fence.StopFenceError,"PAPER_PROFILE_NOT_PRIVATE"):
                fence.check_paper_config(p)
            p.chmod(0o600)
            alias=Path(td)/"execution-link.env"
            alias.symlink_to(p)
            with self.assertRaises(OSError):
                fence.check_paper_config(alias)

    def test_show_strict_identity(self):
        expected = status(Path("/nowhere"), "heptatrader-paper-driver.service")
        lines = "\n".join(f"{k}={v}" for k, v in expected.items())
        self.assertEqual(fence.parse_show(lines), expected)
        with self.assertRaisesRegex(fence.StopFenceError, "UNRECOGNIZED"):
            fence.parse_show(lines + "\nMainPID=2")
        with self.assertRaisesRegex(fence.StopFenceError, "UNRECOGNIZED"):
            fence.parse_show(lines.replace("InvocationID=", "OtherProperty=", 1))
        with self.assertRaisesRegex(fence.StopFenceError, "INCOMPLETE"):
            fence.parse_show("\n".join(x for x in lines.splitlines()
                                        if not x.startswith("InvocationID=")))

    def test_socket_without_service_mainpid_is_normalized(self):
        sample = status(Path("/nowhere"), "heptatrader-events.socket")
        payload = "\n".join(f"{k}={v}" for k, v in sample.items()
                            if k != "MainPID")
        with self.assertRaisesRegex(fence.StopFenceError, "INCOMPLETE"):
            fence.parse_show(payload)
        self.assertEqual(fence.parse_show(payload, is_socket=True)["MainPID"], "")

    def test_pid_drift_and_missing_shield_fail_closed(self):
        name = fence.RISK_UNITS[0]
        before = {name: status(Path("/nowhere"), name)}
        after = {name: {**before[name], "MainPID": "44",
                        "RefuseManualStop": "yes"}}
        with self.assertRaisesRegex(fence.StopFenceError, "LIVE_RISK_PROCESS_OR_DEPENDENCY_CHANGED"):
            fence.check_state(before, after, required=True)
        after[name]["MainPID"] = before[name]["MainPID"]
        after[name]["PartOf"] = "some-wrong.target"
        with self.assertRaisesRegex(fence.StopFenceError, "LIVE_RISK_PROCESS_OR_DEPENDENCY_CHANGED"):
            fence.check_state(before, after, required=True)
        after[name]["PartOf"] = before[name]["PartOf"]
        after[name]["RefuseManualStop"] = "no"
        with self.assertRaisesRegex(fence.StopFenceError, "EFFECTIVE_STOP_FENCE_NOT_INSTALLED"):
            fence.check_state(before, after, required=True)

    def test_inactive_target_is_allowed_but_target_guard_required(self):
        name = "heptatrader.target"
        before = {name: status(Path("/nowhere"), name)}
        self.assertEqual(before[name]["ActiveState"], "inactive")
        after = {name: {**before[name], "RefuseManualStop": "yes"}}
        fence.check_state(before, after, required=True)
        with self.assertRaisesRegex(fence.StopFenceError, "EFFECTIVE_STOP_FENCE_NOT_INSTALLED"):
            fence.check_state(before, before, required=True)

    def test_atomic_install_no_service_stop_and_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            home = fixture_home(Path(td))
            calls = []
            def fake_run(argv, **kwargs):
                calls.append(tuple(argv))
                return subprocess.CompletedProcess(argv, 0)
            with patch.object(fence, "show_unit",
                              side_effect=lambda name: status(home, name)), \
                 patch.object(fence.subprocess, "run", side_effect=fake_run), \
                 patch.object(fence.os, "geteuid", return_value=__import__("os").geteuid()):
                first = fence.install(home)
                self.assertEqual(set(first["install_status"].values()), {"INSTALLED"})
                self.assertTrue(first["original_unit_invocations_preserved"])
                second = fence.install(home)
                self.assertEqual(set(second["install_status"].values()), {"ALREADY_PRESENT"})
            self.assertTrue(all(command == ("systemctl", "--user", "daemon-reload")
                                for command in calls))

    def test_partial_guard_fsync_failure_cannot_leave_blocking_dropin(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/fence.GUARD_NAME
            with patch.object(fence.os,'fsync',side_effect=OSError('INJECTED_EIO')):
                with self.assertRaisesRegex(OSError,'INJECTED_EIO'):
                    fence.write_new(path,fence.GUARD_TEXT.encode())
            self.assertFalse(path.exists(), 'truncated stop shield survived failed fsync')

    def test_receipt_failure_restores_original_stop_policy(self):
        with tempfile.TemporaryDirectory() as td:
            home=fixture_home(Path(td))
            commands=[]
            def fake_run(argv,**_):
                commands.append(tuple(argv))
                return subprocess.CompletedProcess(argv,0)
            with patch.object(fence,'show_unit',side_effect=lambda name:status(home,name)), \
                 patch.object(fence.subprocess,'run',side_effect=fake_run), \
                 patch.object(fence.os,'geteuid',return_value=__import__('os').geteuid()):
                with self.assertRaisesRegex(OSError,'INJECTED_RECEIPT_IO'):
                    fence.install(home,persist_receipt=lambda _: (_ for _ in ()).throw(OSError('INJECTED_RECEIPT_IO')))
            for name in fence.RISK_UNITS:
                path=home/'.config/systemd/user'/(name+'.d')/fence.GUARD_NAME
                self.assertFalse(path.exists(),'installation without receipt changed stop policy')
            self.assertGreaterEqual(commands.count(('systemctl','--user','daemon-reload')),2)

    def test_installer_waits_for_existing_latch_lock_before_observation(self):
        with tempfile.TemporaryDirectory() as td:
            home = fixture_home(Path(td))
            lock_path = home / ".local/state/heptatrader" / latch.LOCK
            fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX)
            attempted = threading.Event()
            real_flock = fcntl.flock
            def waiting_flock(other, flags):
                attempted.set()
                return real_flock(other, flags)
            try:
                with ThreadPoolExecutor(max_workers=1) as pool:
                    with (
                        patch.object(latch.fcntl, "flock", side_effect=waiting_flock),
                        patch.object(fence, "show_unit",
                                     side_effect=lambda name: status(home, name)) as observe,
                        patch.object(fence.subprocess, "run",
                                     return_value=subprocess.CompletedProcess([], 0)) as reload,
                    ):
                        future = pool.submit(fence.install, home)
                        try:
                            self.assertTrue(attempted.wait(timeout=3))
                            self.assertFalse(future.done())
                            observe.assert_not_called()
                            reload.assert_not_called()
                        finally:
                            real_flock(fd, fcntl.LOCK_UN)
                        result = future.result(timeout=3)
                        self.assertTrue(result["all_effective_refuse_manual_stop"])
            finally:
                os.close(fd)

    def test_receipt_failure_keeps_shared_lock_through_rollback(self):
        with tempfile.TemporaryDirectory() as td:
            home = fixture_home(Path(td))
            lock_path = home / ".local/state/heptatrader" / latch.LOCK
            calls = []
            def assert_locked():
                fd = os.open(lock_path, os.O_RDWR | os.O_NOFOLLOW)
                try:
                    with self.assertRaises(BlockingIOError):
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                finally:
                    os.close(fd)
            def reload(argv, **_):
                assert_locked()
                calls.append(tuple(argv))
                return subprocess.CompletedProcess(argv, 0)
            def failed_receipt(_):
                assert_locked()
                raise OSError("RECEIPT_WRITE_FAILED")
            with (
                patch.object(fence, "show_unit",
                             side_effect=lambda name: status(home, name)),
                patch.object(fence.subprocess, "run", side_effect=reload),
            ):
                with self.assertRaisesRegex(OSError, "RECEIPT_WRITE_FAILED"):
                    fence.install(home, persist_receipt=failed_receipt)
                self.assertEqual(len(calls), 2)
                self.assertTrue(all(not (home / ".config/systemd/user" /
                    (name + ".d") / fence.GUARD_NAME).exists() for name in fence.RISK_UNITS))
                fd = os.open(lock_path, os.O_RDWR | os.O_NOFOLLOW)
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                finally:
                    os.close(fd)
                result = fence.install(home)
                self.assertEqual(set(result["install_status"].values()), {"INSTALLED"})
                self.assertTrue(all(status(home, name)["RefuseManualStop"] == "yes"
                                    for name in fence.RISK_UNITS))

    def test_unsafe_shared_lock_refused_before_any_unit_operation(self):
        for kind in ("symlink", "hardlink", "permissions"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as td:
                home = fixture_home(Path(td))
                root = home / ".local/state/heptatrader"
                lock = root / latch.LOCK
                if kind == "symlink":
                    target = root / "foreign"; target.write_bytes(b"")
                    target.chmod(0o600); lock.symlink_to(target)
                elif kind == "hardlink":
                    target = root / "foreign"; target.write_bytes(b"")
                    target.chmod(0o600); os.link(target, lock)
                else:
                    lock.write_bytes(b""); lock.chmod(0o640)
                with (
                    patch.object(fence, "show_unit") as observe,
                    patch.object(fence.subprocess, "run") as reload,
                ):
                    with self.assertRaises((OSError, fence.StopFenceError)):
                        fence.install(home)
                    observe.assert_not_called()
                    reload.assert_not_called()

    def test_conflicting_dropin_aborts_without_partial_install(self):
        with tempfile.TemporaryDirectory() as td:
            home = fixture_home(Path(td))
            name = fence.RISK_UNITS[-1]
            d = home / ".config/systemd/user" / (name + ".d")
            d.mkdir()
            bad = d / fence.GUARD_NAME
            bad.write_text("TAMPER")
            with patch.object(fence, "show_unit",
                              side_effect=lambda n: status(home, n)), \
                 patch.object(fence.subprocess, "run",
                              return_value=subprocess.CompletedProcess([], 0)), \
                 patch.object(fence.os, "geteuid", return_value=__import__("os").geteuid()):
                with self.assertRaisesRegex(fence.StopFenceError, "EXISTING_STOP_FENCE_CHANGED"):
                    fence.install(home)
            self.assertEqual(bad.read_text(), "TAMPER")
            self.assertFalse((home / ".config/systemd/user" /
                             (fence.RISK_UNITS[0] + ".d") / fence.GUARD_NAME).exists())


if __name__ == "__main__":
    unittest.main()
