"""The privileged fixture may not silently fall back to mocked activation."""
from __future__ import annotations
import os
from pathlib import Path
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests"))
import systemd_simulator_smoke as smoke


class SystemdSmokeAdmissionTests(unittest.TestCase):
    def test_opt_in_required_before_any_host_or_artifact_change(self):
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(smoke, "run") as run:
            with self.assertRaisesRegex(RuntimeError, "HEPTA_DISPOSABLE_SYSTEMD_TEST"):
                smoke.host_preconditions()
            run.assert_not_called()

    def test_non_root_rejected_before_pid1_or_host_changes(self):
        with mock.patch.dict(os.environ, {"HEPTA_DISPOSABLE_SYSTEMD_TEST": "1"}), \
             mock.patch.object(smoke.os, "geteuid", return_value=12345), \
             mock.patch.object(smoke, "run") as run:
            with self.assertRaisesRegex(RuntimeError, "root"):
                smoke.host_preconditions()
            run.assert_not_called()

    def test_non_systemd_pid1_cannot_masquerade_as_manager_evidence(self):
        with mock.patch.dict(os.environ, {"HEPTA_DISPOSABLE_SYSTEMD_TEST": "1"}), \
             mock.patch.object(smoke.os, "geteuid", return_value=0), \
             mock.patch.object(Path, "read_text", return_value="supervisord\n"), \
             mock.patch.object(smoke, "run") as run:
            with self.assertRaisesRegex(RuntimeError, "real systemd PID 1"):
                smoke.host_preconditions()
            run.assert_not_called()

    def test_existing_account_refuses_all_privileged_work(self):
        with mock.patch.dict(os.environ, {"HEPTA_DISPOSABLE_SYSTEMD_TEST": "1"}), \
             mock.patch.object(smoke.os, "geteuid", return_value=0), \
             mock.patch.object(Path, "read_text", return_value="systemd\n"), \
             mock.patch.object(smoke.pwd, "getpwnam", return_value=object()), \
             mock.patch.object(smoke, "run") as run:
            with self.assertRaisesRegex(RuntimeError, "existing account"):
                smoke.host_preconditions()
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
