"""Exact opt-in systemd risk-path stop-fence tests; no real unit operations."""
from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import install_risk_stop_fence as fence


def fixture_home(path: Path):
    home = path / "alex"
    unit_dir = home / ".config/systemd/user"
    unit_dir.mkdir(parents=True)
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
        "LoadState": "loaded", "ActiveState": "active",
        "SubState": "running", "MainPID": "31337",
        "InvocationID": name + "-fixed", "RefuseManualStop": "yes" if armed else "no",
        "PartOf": "" if armed else "heptatrader.target",
    }


class RiskStopFenceContract(unittest.TestCase):
    def test_exact_chain_and_broker_scope(self):
        self.assertEqual(len(fence.RISK_UNITS), 8)
        self.assertIn("heptatrader-execution.socket", fence.RISK_UNITS)
        self.assertIn("heptatrader-events.socket", fence.RISK_UNITS)
        self.assertIn("heptatrader-runtime.service", fence.RISK_UNITS)
        self.assertIn("ibgateway.service", fence.RISK_UNITS)
        self.assertEqual(fence.GUARD_TEXT, "[Unit]\nRefuseManualStop=yes\nPartOf=\n")

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
                        "RefuseManualStop": "yes", "PartOf": ""}}
        with self.assertRaisesRegex(fence.StopFenceError, "LIVE_RISK_PROCESS_CHANGED"):
            fence.check_state(before, after, required=True)
        after[name]["MainPID"] = before[name]["MainPID"]
        after[name]["PartOf"] = "heptatrader.target"
        with self.assertRaisesRegex(fence.StopFenceError, "EFFECTIVE_STOP_FENCE_NOT_INSTALLED"):
            fence.check_state(before, after, required=True)

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
