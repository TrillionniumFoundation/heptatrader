"""Adversarial cgroup-v2 fault/recovery unit tests, no real OOM in CI."""
import copy
import signal
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch
from historical_training.common import EvidenceError
from train_v2 import swapless_fault_recovery as mod


def controls(mode):
    m = mod.MODES[mode]
    return {
        "memory.max": m["max"], "memory.high": m["high"],
        "memory.swap.max": 0, "memory.swap.current": 0,
        "memory.current": 12 * mod.MIB,
        "pids.max": 32, "memory.oom.group": 0,
        "cpu.max": "100000 100000",
    }


def event(oom, killed):
    return {"low": 0, "high": 0, "max": 0,
            "oom": oom, "oom_kill": killed, "oom_group_kill": 0}


class SwaplessFaultProbeTests(unittest.TestCase):
    def fake(self, mode, *, exit_code=None, after=None, before=None,
             swap_peak=0, peak=None):
        if exit_code is None:
            exit_code = -signal.SIGKILL if mode == "fault" else 0
        if before is None:
            before = event(0, 0)
        if after is None:
            after = event(1, 1) if mode == "fault" else event(0, 0)
        if peak is None:
            peak = mod.MODES[mode]["max"] if mode == "fault" else 80*mod.MIB
        child = subprocess.CompletedProcess(
            args=["controlled-fixture"], returncode=exit_code,
            stdout="CHILD_TOUCHED_BYTES 67108864" if exit_code == 0 else "",
            stderr="")
        with patch.object(mod, "_cgroup", return_value=(
                Path("/user.slice/app.slice/ht-trainv2-cg-fixture.service"),
                Path("/sys/fs/cgroup/testing"))):
            with patch.object(mod, "_kernel_limits", return_value=controls(mode)):
                with patch.object(mod, "_events", side_effect=[before, after]):
                    with patch.object(mod, "_cgroup_value",
                            side_effect=lambda _root, key: str(
                                peak if key=="memory.peak" else swap_peak)):
                        with patch.object(mod, "_child", return_value=child) as worker:
                            result = mod.probe(mode)
                            worker.assert_called_once_with(
                                mod.MODES[mode]["child_mib"])
        return result

    def test_true_oom_child_isolated_and_proven(self):
        result=self.fake("fault")
        self.assertEqual(result["actual_kernel_verdict"],"PASS")
        self.assertEqual(result["actual_oom_kill_delta"],1)
        self.assertEqual(result["child_returncode"],-signal.SIGKILL)
        self.assertEqual(result["memory_swap_peak_bytes"],0)
        self.assertFalse(result["original_training_guard_changed"])
        self.assertFalse(result["gpu_used"])
        self.assertEqual(result["broker_orders_sent"],0)

    def test_signal_kill_without_kernel_oom_never_qualifies(self):
        result=self.fake("fault",after=event(0,0))
        self.assertEqual(result["actual_kernel_verdict"],"FAIL_PRESERVE")

    def test_late_or_timeout_child_is_not_kernel_oom_proof(self):
        for bad in (124, -signal.SIGTERM, 0):
            with self.subTest(exit_code=bad):
                self.assertEqual(self.fake("fault",exit_code=bad)[
                    "actual_kernel_verdict"],"FAIL_PRESERVE")

    def test_true_normal_recovery_independent_no_swap(self):
        r=self.fake("recover")
        self.assertEqual(r["actual_kernel_verdict"],"PASS")
        self.assertEqual(r["actual_oom_kill_delta"],0)
        self.assertEqual(r["actual_kernel_before"]["memory.max"],6*mod.GIB)

    def test_recovery_with_kernel_oom_never_qualifies(self):
        r=self.fake("recover",after=event(1,1))
        self.assertEqual(r["actual_kernel_verdict"],"FAIL_PRESERVE")

    def test_swap_peak_nonzero_invalidates_both(self):
        for mode in ("fault","recover"):
            with self.subTest(mode=mode):
                r=self.fake(mode,swap_peak=4096)
                self.assertEqual(r["actual_kernel_verdict"],"FAIL_PRESERVE")

    def test_recovery_exceeds_memory_ceiling_invalid(self):
        r=self.fake("recover",peak=mod.MODES["recover"]["max"]+1)
        self.assertEqual(r["actual_kernel_verdict"],"FAIL_PRESERVE")

    def test_unregistered_probe_mode_refused(self):
        with self.assertRaisesRegex(EvidenceError,"FAULT_UNREGISTERED_MODE"):
            mod.probe("production")

    def test_kernel_guard_requires_matching_exact_cpu_cap_and_zero_swap(self):
        group=controls("recover")
        cases={
            "memory.max":7*mod.GIB,
            "memory.high":3*mod.GIB,
            "memory.swap.max":1,
            "memory.swap.current":1,
            "memory.oom.group":1,
            "pids.max":100,
            "cpu.max":"max 100000",
        }
        for key,bad in cases.items():
            actual=dict(group)
            actual[key]=bad
            with self.subTest(key=key):
                with patch.object(mod,"_cgroup_value",
                    side_effect=lambda _root,item: str(actual[item])):
                    with self.assertRaisesRegex(
                            EvidenceError,"FAULT_KERNEL_RESOURCE_BOUND_MISMATCH"):
                        mod._kernel_limits(Path("/fake"),"recover")

    def test_cgroup_scope_blocks_nontransient_service(self):
        with patch.object(Path,"read_text",
                          return_value="0::/user.slice/user-1000.slice/unrelated.service\n"):
            with self.assertRaisesRegex(EvidenceError,
                        "FAULT_NOT_IN_NEW_ISOLATED_SERVICE"):
                mod._cgroup()

    def test_non_numeric_kernel_config_refused(self):
        for raw in ("max","-2","1G","", "2 3", "01x"):
            with self.subTest(raw=raw):
                with self.assertRaisesRegex(EvidenceError,
                        "FAULT_CGROUP_NOT_NUMERIC"):
                    mod._integer(raw)

    def test_child_timeout_must_be_explicit_failure_status(self):
        with patch.object(mod.subprocess,"run",
                          side_effect=subprocess.TimeoutExpired("cmd",8)):
            c=mod._child(128)
            self.assertEqual(c.returncode,124)
            self.assertIn("TIMEOUT_NO_OOM_ATTESTATION",c.stderr)
