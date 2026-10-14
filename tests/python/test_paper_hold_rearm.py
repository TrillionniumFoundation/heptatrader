from __future__ import annotations
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from hepta_paper_hold_rearm import rearm_from_runtime_root, HoldRefused, main


class HoldRearmTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="paper-hold-r4-")
        self.root = Path(self.temp.name) / "runuser"
        self.root.mkdir(mode=0o700)
        self.hepta = self.root / "heptatrader"
        self.hepta.mkdir(mode=0o700)
        self.uid, self.gid = os.geteuid(), os.getegid()

    def tearDown(self):
        self.temp.cleanup()

    def engage(self):
        return rearm_from_runtime_root(str(self.root), self.uid, self.gid)

    def test_missing_exact_control_created_with_required_mode(self):
        result = self.engage()
        self.assertTrue(result["created_control"])
        self.assertTrue(result["created_marker"])
        self.assertEqual(stat.S_IMODE((self.hepta / "control").stat().st_mode), 0o750)
        self.assertEqual(stat.S_IMODE((self.hepta / "control/kill-switch").stat().st_mode), 0o440)

    def test_idempotent_does_not_replace_marker_inode(self):
        self.engage()
        p = self.hepta / "control/kill-switch"
        identity = (p.stat().st_dev, p.stat().st_ino)
        self.assertFalse(self.engage()["created_marker"])
        self.assertEqual((p.stat().st_dev, p.stat().st_ino), identity)

    def test_existing_control_symlink_refused(self):
        (self.hepta / "control").symlink_to(self.hepta)
        with self.assertRaises(OSError):
            self.engage()

    def test_existing_marker_symlink_refused(self):
        control = self.hepta / "control"
        control.mkdir(mode=0o700)
        control.chmod(0o750)
        (control / "kill-switch").symlink_to("/dev/null")
        with self.assertRaises(OSError):
            self.engage()

    def test_existing_marker_world_write_refused(self):
        control = self.hepta / "control"
        control.mkdir(mode=0o700)
        control.chmod(0o750)
        p = control / "kill-switch"
        p.write_bytes(b"x")
        p.chmod(0o666)
        with self.assertRaisesRegex(HoldRefused, "PAPER_HOLD_MARKER_UNSAFE"):
            self.engage()

    def test_existing_marker_hardlink_refused(self):
        control = self.hepta / "control"
        control.mkdir(mode=0o700)
        control.chmod(0o750)
        p = control / "kill-switch"
        p.touch(mode=0o440)
        p.chmod(0o440)
        os.link(p, control / "duplicate-link")
        with self.assertRaisesRegex(HoldRefused, "PAPER_HOLD_MARKER_UNSAFE"):
            self.engage()

    def test_wrong_control_mode_refused_without_rewrite(self):
        control = self.hepta / "control"
        control.mkdir(mode=0o700)
        with self.assertRaisesRegex(HoldRefused, "PAPER_HOLD_DIRECTORY_UNSAFE"):
            self.engage()
        self.assertFalse((control / "kill-switch").exists())

    def test_missing_parent_refused_without_creating_parent(self):
        self.hepta.rmdir()
        with self.assertRaises(FileNotFoundError):
            self.engage()
        self.assertFalse(self.hepta.exists())

    def test_invalid_runtime_root_mode_refused(self):
        self.root.chmod(0o750)
        with self.assertRaisesRegex(HoldRefused, "PAPER_HOLD_DIRECTORY_UNSAFE"):
            self.engage()

    def test_control_subdirectory_refused_before_touching_marker(self):
        control = self.hepta / "control"
        control.mkdir(mode=0o700)
        control.chmod(0o750)
        (control / "unexpected-subdir").mkdir()
        with self.assertRaisesRegex(HoldRefused, "PAPER_HOLD_DIRECTORY_UNSAFE"):
            self.engage()
        self.assertFalse((control / "kill-switch").exists())

    def test_wrong_parent_mode_refused(self):
        self.hepta.chmod(0o750)
        with self.assertRaisesRegex(HoldRefused, "PAPER_HOLD_DIRECTORY_UNSAFE"):
            self.engage()
        self.assertFalse((self.hepta / "control").exists())

    def test_fifo_marker_refused(self):
        control = self.hepta / "control"
        control.mkdir(mode=0o700)
        control.chmod(0o750)
        os.mkfifo(control / "kill-switch", mode=0o440)
        with self.assertRaisesRegex(HoldRefused, "PAPER_HOLD_MARKER_UNSAFE"):
            self.engage()

    def test_runtime_root_symlink_refused(self):
        alias = Path(self.temp.name) / "root-symlink"
        alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(OSError):
            rearm_from_runtime_root(str(alias), self.uid, self.gid)

    def test_no_cli_path_override(self):
        with self.assertRaisesRegex(HoldRefused, "PAPER_HOLD_NO_ARGUMENTS_ALLOWED"):
            main(["--root", str(self.root)])


if __name__ == "__main__":
    unittest.main()
