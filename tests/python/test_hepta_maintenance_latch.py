"""Operator new-risk hold tests on isolated private directories only.

Tests never touch real /run/user/UID paths, the IB API, or actual orders.
"""
from concurrent.futures import ThreadPoolExecutor
import fcntl
import os
from pathlib import Path
import stat
import tempfile
import time
import unittest

from hepta_maintenance_latch import HoldError, perform


class HoldTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.parent = root / "runtime";self.parent.mkdir(mode=0o700)
        self.control = self.parent / "control";self.control.mkdir(mode=0o750);self.control.chmod(0o750)
        self.state = root / "state";self.state.mkdir(mode=0o700)
        self.hold_path = self.state / "operator-maintenance" / "maintenance-hold.json"
        self.kill_path = self.control / "kill-switch"

    def run_action(self, action):
        return perform(action, self.control, self.state)

    def test_original_pause_resume_without_persistent_hold(self):
        self.assertFalse(self.run_action("maintenance-status")["persistent_hold"])
        p = self.run_action("pause")
        self.assertTrue(p["native_kill_switch_engaged"])
        self.assertEqual(self.kill_path.read_bytes(), b"engaged\n")
        self.assertFalse(p["persistent_hold"])
        self.assertFalse(self.run_action("paper-on")["native_kill_switch_engaged"])

    def test_operator_hold_defeats_resident_auto_rearm(self):
        p = self.run_action("maintenance-hold")
        self.assertTrue(p["persistent_hold"])
        self.assertTrue(p["native_kill_switch_engaged"])
        before=self.hold_path.read_bytes()
        for _ in range(3):
            with self.assertRaisesRegex(HoldError,"BLOCKED_BY_PERSISTENT"):
                self.run_action("paper-on")
        self.assertEqual(self.hold_path.read_bytes(),before)
        self.assertEqual(self.kill_path.read_bytes(),b"engaged\n")
        self.assertFalse(self.run_action("maintenance-status")["may_stop_driver"])

    def test_durable_hold_idempotent_and_explicit_release(self):
        self.run_action("maintenance-hold")
        before=self.hold_path.read_bytes()
        self.run_action("maintenance-hold")
        self.assertEqual(before,self.hold_path.read_bytes())
        released=self.run_action("maintenance-release")
        self.assertFalse(released["persistent_hold"])
        self.assertFalse(released["native_kill_switch_engaged"])
        self.assertFalse(self.hold_path.exists())
        self.assertFalse(self.kill_path.exists())
        self.assertFalse(self.run_action("paper-on")["persistent_hold"])

    def test_persisted_hold_blocks_rearm_even_if_native_marker_lost(self):
        self.run_action("maintenance-hold")
        self.kill_path.unlink()
        with self.assertRaisesRegex(HoldError,"BLOCKED_BY_PERSISTENT"):
            self.run_action("paper-on")
        with self.assertRaisesRegex(HoldError,"RELEASE_REQUIRES_BOTH"):
            self.run_action("maintenance-release")
        self.assertTrue(self.hold_path.exists())
        self.run_action("maintenance-hold")
        self.assertTrue(self.kill_path.exists())

    def test_corrupt_hold_fails_closed(self):
        self.run_action("maintenance-hold")
        self.hold_path.chmod(0o600)
        self.hold_path.write_bytes(b"{\"bad\":1}")
        with self.assertRaisesRegex(HoldError,"BAD_HOLD_SCOPE"):
            self.run_action("paper-on")
        self.assertTrue(self.kill_path.exists())

    def test_symlink_hold_refuses_and_does_not_follow(self):
        self.run_action("pause")
        maintenance = self.state / "operator-maintenance"
        maintenance.mkdir(mode=0o700,exist_ok=True)
        foreign=self.state/"foreign";foreign.write_text("untrusted")
        (maintenance/"maintenance-hold.json").symlink_to(foreign)
        with self.assertRaisesRegex(HoldError,"UNSAFE_MARKER"):
            self.run_action("paper-on")
        self.assertTrue(self.kill_path.exists())

    def test_modified_native_marker_does_not_get_erased(self):
        self.run_action("pause")
        self.kill_path.chmod(0o600)
        with self.assertRaisesRegex(HoldError,"UNSAFE_MARKER"):
            self.run_action("paper-on")
        self.assertTrue(self.kill_path.exists())

    def test_unexpected_native_marker_contents_fail_closed(self):
        self.run_action("pause")
        self.kill_path.chmod(0o600)
        self.kill_path.write_bytes(b"not-the-original-native-marker")
        self.kill_path.chmod(0o440)
        with self.assertRaisesRegex(HoldError,"UNKNOWN_EXISTING_NATIVE"):
            self.run_action("maintenance-hold")
        self.assertTrue(self.kill_path.exists())

    def test_bad_state_directory_refuses_without_touching_switch(self):
        self.state.chmod(0o777)
        with self.assertRaisesRegex(HoldError,"UNSAFE_DIRECTORY"):
            self.run_action("pause")
        self.assertFalse(self.kill_path.exists())

    def test_lock_serializes_concurrent_paper_on(self):
        self.run_action("pause")
        lock=self.state/"operator-maintenance.lock"
        fd=os.open(lock,os.O_RDWR|os.O_NOFOLLOW)
        try:
            fcntl.flock(fd,fcntl.LOCK_EX)
            with ThreadPoolExecutor(max_workers=1) as pool:
                future=pool.submit(self.run_action,"paper-on")
                time.sleep(0.06)
                self.assertFalse(future.done())
                fcntl.flock(fd,fcntl.LOCK_UN)
                self.assertFalse(future.result(timeout=2)["native_kill_switch_engaged"])
        finally:
            os.close(fd)

    def test_release_without_hold_must_not_erase_legacy_pause(self):
        self.run_action("pause")
        with self.assertRaisesRegex(HoldError,"RELEASE_REQUIRES_BOTH"):
            self.run_action("maintenance-release")
        self.assertTrue(self.kill_path.exists())

    def test_latch_file_is_private_and_has_no_order_permission(self):
        result=self.run_action("maintenance-hold")
        self.assertEqual(stat.S_IMODE(self.hold_path.stat().st_mode),0o600)
        self.assertEqual(stat.S_IMODE(self.kill_path.stat().st_mode),0o440)
        self.assertEqual(result["broker_orders_sent"],0)
        self.assertFalse(result["may_stop_driver"])
        self.assertEqual(result["schema"],"heptatrader.maintenance-latch-status.v1")

    def test_public_wrapper_routes_every_entry_to_pinned_helper(self):
        script=Path(__file__).resolve().parents[2]/"scripts/heptatrader-user"
        value=script.read_text()
        self.assertIn("maintenance-hold|maintenance-release|maintenance-status",value)
        self.assertIn("hepta_maintenance_latch.py",value)
        self.assertNotIn("elif action==\"paper-on\" and marker",value)


if __name__=="__main__":
    unittest.main()
