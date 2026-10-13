"""Adversarial GPU owner census; never claims exclusive device access."""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from historical_training.common import EvidenceError
from train_v2.gpu_owner_fence import (
    _device_identity, _start_ticks, owner_fence, scan_processes,
)


class FakeProc(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "proc"
        self.root.mkdir()
        self.dev = _device_identity("/dev/null")
        self.devices = [self.dev]

    def new_process(self, pid, *, fd=None, start=500):
        p = self.root / str(pid)
        p.mkdir()
        (p / "fd").mkdir()
        # Linux field 22 is first index 19 AFTER ') '.
        stat_suffix = ["S"] + ["0"] * 18 + [str(start)] + ["0"] * 4
        (p / "stat").write_text(
            f"{pid} (a process with ) and spaces) " + " ".join(stat_suffix))
        (p / "comm").write_text("fixture-process\n")
        if fd is not None:
            (p / "fd" / "4").symlink_to(fd)
        return p


class GPUOwnerFenceTests(FakeProc):
    def test_real_char_device_identity(self):
        self.assertTrue(stat.S_ISCHR(Path("/dev/null").stat().st_mode))
        self.assertEqual(self.dev["name"], "null")

    def test_linux_pid_stat_with_parenthesized_comm(self):
        p = self.new_process(123, start=89311)
        self.assertEqual(_start_ticks((p / "stat").read_text()), 89311)
        self.assertRaises(ValueError, _start_ticks, "no parentheses")
        self.assertRaises(ValueError, _start_ticks, "123 (name) S 1 2 3")

    def test_actual_char_fd_owner_detected_without_opening_device(self):
        self.new_process(123, fd="/dev/null")
        self.new_process(124, fd="/dev/zero")
        result = scan_processes(self.root, self.devices)
        self.assertEqual(result["pids_fully_inspected"], 2)
        self.assertTrue(result["all_processes_proven_inspected"])
        self.assertEqual(len(result["holders"]), 1)
        self.assertEqual(result["holders"][0]["pid"], 123)
        self.assertEqual(result["holders"][0]["start_ticks"], 500)
        self.assertEqual(result["holders"][0]["device_handles"],
                         [{"fd": 4, "device": "null"}])

    def test_two_scans_observing_owner_are_denied(self):
        self.new_process(99, fd="/dev/null")
        result = owner_fence(self.root, device_specs=self.devices, delay_seconds=0)
        self.assertIn("GPU_RENDER_OR_KFD_HELD_BY_UNRELATED_PROCESS",
                      result["fail_closed_reasons"])
        self.assertFalse(result["gpu_lease_exclusive"])
        self.assertFalse(result["can_start_gpu_training"])
        self.assertFalse(result["actual_gpu_4to6gib_memory_ceiling_proven"])
        self.assertEqual(result["broker_orders_sent"], 0)

    def test_idle_census_cannot_mint_gpu_exclusive_lease(self):
        self.new_process(99, fd="/dev/zero")
        result = owner_fence(self.root, device_specs=self.devices, delay_seconds=0)
        self.assertEqual(result["fail_closed_reasons"],
                         ["GPU_EXCLUSIVE_LEASE_NOT_ESTABLISHED_BY_IDLE_SNAPSHOT"])
        self.assertEqual(result["before"]["holders"], [])
        self.assertFalse(result["can_start_gpu_training"])

    def test_inaccessible_fd_directory_marks_scan_incomplete(self):
        self.new_process(99, fd="/dev/null")
        original = os.scandir

        def forced(path):
            if str(path).endswith("/99/fd"):
                raise PermissionError("restricted")
            return original(path)

        with patch("train_v2.gpu_owner_fence.os.scandir", side_effect=forced):
            result = scan_processes(self.root, self.devices)
        self.assertFalse(result["all_processes_proven_inspected"])
        self.assertEqual(result["uninspectable_or_racing_pid_count"], 1)
        self.assertEqual(result["unknown"][0]["reason"],
                         "PID_FD_DIRECTORY_NOT_INSPECTABLE")
        self.assertFalse(result["holders"])

    def test_fd_disappears_during_stat_is_never_proof_of_idle(self):
        self.new_process(99, fd="/dev/null")
        original = os.stat

        def forced(path, *args, **kwargs):
            if str(path).endswith("/99/fd/4"):
                raise FileNotFoundError("closed")
            return original(path, *args, **kwargs)

        with patch("train_v2.gpu_owner_fence.os.stat", side_effect=forced):
            result = scan_processes(self.root, self.devices)
        self.assertEqual(result["unknown"][0]["reason"], "PID_FD_RACED")
        self.assertFalse(result["all_processes_proven_inspected"])

    def test_same_pid_changed_starttime_detected(self):
        self.new_process(99, fd="/dev/null")
        original = Path.read_text
        counter = 0

        def changed(path, *args, **kwargs):
            nonlocal counter
            content = original(path, *args, **kwargs)
            if str(path).endswith("/99/stat"):
                counter += 1
                if counter >= 2:
                    return content.replace(" 500 ", " 501 ")
            return content

        with patch("pathlib.Path.read_text", autospec=True, side_effect=changed):
            result = scan_processes(self.root, self.devices)
        self.assertFalse(result["all_processes_proven_inspected"])
        self.assertEqual(result["unknown"][0]["reason"], "PID_REUSE_OR_CLOCK_CHANGE")

    def test_owner_set_racing_between_two_scans_denied(self):
        self.new_process(99, fd="/dev/null")
        first = scan_processes(self.root, self.devices)
        (self.root / "99" / "fd" / "4").unlink()
        second = scan_processes(self.root, self.devices)
        with patch("train_v2.gpu_owner_fence.scan_processes",
                   side_effect=[first, second]):
            result = owner_fence(self.root, device_specs=self.devices, delay_seconds=0)
        self.assertIn("GPU_HOLDER_SET_CHANGED_BETWEEN_SCANS",
                      result["fail_closed_reasons"])
        self.assertFalse(result["gpu_lease_exclusive"])

    def test_pid_count_bound_fails_closed(self):
        self.new_process(99)
        self.new_process(100)
        with patch("train_v2.gpu_owner_fence.MAX_PIDS", 1):
            with self.assertRaisesRegex(EvidenceError, "GPU_PROCESS_ENUMERATION_BOUND"):
                scan_processes(self.root, self.devices)

    def test_fd_count_bound_fails_closed(self):
        p = self.new_process(99)
        for i in range(10):
            (p / "fd" / str(i)).symlink_to("/dev/zero")
        with patch("train_v2.gpu_owner_fence.MAX_FDS", 5):
            result = scan_processes(self.root, self.devices)
        self.assertFalse(result["all_processes_proven_inspected"])
        self.assertEqual(result["unknown"][0]["reason"], "PID_FD_COUNT_UNBOUNDED")

    def test_symlinked_proc_root_rejected(self):
        alias = Path(self.tmp.name) / "fake-link"
        alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(EvidenceError, "GPU_PROC_ROOT_INVALID"):
            scan_processes(alias, self.devices)

    def test_invalid_gpu_observation_interval_refused(self):
        self.new_process(99)
        with self.assertRaisesRegex(EvidenceError, "GPU_OBSERVATION_INTERVAL_BOUND"):
            owner_fence(self.root, device_specs=self.devices, delay_seconds=10)
