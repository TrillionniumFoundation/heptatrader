"""GPU + original swapless preflight cannot accidentally grant a model run."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from train_v2.gpu_owner_join import verify_join, _safe_error


def source(*, ready=True, authority=True):
    return {
        "schema":"heptatrader.train-v2-swapless-isolated-admission.v1",
        "isolated_preflight_eligible":ready,
        "isolated_research_preflight":(
            "READY_FOR_OPERATOR_REVIEW_NO_JOB" if ready else
            "HOLD_ISOLATED_RESEARCH"),
        "isolated_hold_reasons":[] if ready else ["LATEST_SOURCE_QUOTE_NOT_FRESH"],
        "real_quote_gate_unchanged":True,
        "source_authoritative_method":(
            "PINNED_ORIGINAL_GUARD_REFRESH" if authority else
            "PERIODIC_STATUS_ONLY_NO_NEW_QUOTE_PROOF"),
        "legacy_production_guard_admit_new_worker":False,
    }


def gpu(*, holders=0, incomplete=0, illegal_success=False):
    return {
        "schema":"heptatrader.train-v2-gpu-owner-fence.v1",
        "gpu_lease_exclusive":illegal_success,
        "can_start_gpu_training":illegal_success,
        "before":{"holders":[{"pid":123}]*holders},
        "after":{"holders":[{"pid":123}]*holders,
                 "uninspectable_or_racing_pid_count":incomplete},
        "fail_closed_reasons":(
            ["GPU_RENDER_OR_KFD_HELD_BY_UNRELATED_PROCESS"] if holders else
            ["GPU_PROCESS_HANDLE_CENSUS_INCOMPLETE"] if incomplete else
            ["GPU_EXCLUSIVE_LEASE_NOT_ESTABLISHED_BY_IDLE_SNAPSHOT"]),
    }


class GPUJoinedPreflightTests(unittest.TestCase):
    def test_both_look_ready_still_no_training_lease(self):
        result=verify_join(source(),gpu())
        self.assertEqual(result["gpu_training_admission"],"HOLD_NO_EXCLUSIVE_GPU_LEASE")
        self.assertIn("GPU_NONCOOPERATING_OWNER_LEASE_NOT_ENFORCED",
                      result["blocked_reasons"])
        self.assertFalse(result["actual_rocm_device_memory_ceiling_proven"])
        self.assertFalse(result["trade_authority"])
        self.assertEqual(result["broker_orders_sent"],0)

    def test_actual_gpu_owner_and_unknown_permissions_are_holds(self):
        result=verify_join(source(),gpu(holders=2,incomplete=10))
        self.assertIn("GPU_RENDER_OR_KFD_HELD_BY_UNRELATED_PROCESS",
                      result["blocked_reasons"])
        self.assertFalse(result["kernel_gpu_device_isolation_proven"])

    def test_original_source_outage_retained(self):
        result=verify_join(source(ready=False),gpu())
        self.assertIn("LATEST_SOURCE_QUOTE_NOT_FRESH",result["blocked_reasons"])
        self.assertIn("ORIGINAL_CGROUP_RESOURCE_OR_SOURCE_NOT_READY",
                      result["blocked_reasons"])

    def test_original_unverified_cached_status_cannot_pass(self):
        result=verify_join(source(authority=False),gpu())
        self.assertIn("ORIGINAL_SOURCE_NOT_REVERIFIED",result["blocked_reasons"])

    def test_caller_claiming_gpu_lease_is_denied(self):
        result=verify_join(source(),gpu(illegal_success=True))
        self.assertIn("GPU_SNAPSHOT_CANNOT_DECLARE_LEASE",result["blocked_reasons"])
        self.assertFalse(result["automatic_training"])

    def test_source_exception_and_gpu_exception_both_deny(self):
        result=verify_join(None,None,source_exception="EvidenceError",
                           gpu_exception="PermissionError")
        self.assertIn("ORIGINAL_SOURCE_CGROUP_PREFLIGHT_UNAVAILABLE",
                      result["blocked_reasons"])
        self.assertIn("GPU_OWNER_KERNEL_CENSUS_UNAVAILABLE",
                      result["blocked_reasons"])
        self.assertFalse(result["existing_gpu_process_signaled_or_paused"])

    def test_error_text_must_not_leak_paths(self):
        self.assertEqual(_safe_error(ValueError("/private/path")), "ValueError")
        self.assertEqual(_safe_error(ZeroDivisionError("surprise")), "ZeroDivisionError")

    def test_incorrect_schema_stays_denied(self):
        result=verify_join({"schema":"wrong"},{"schema":"wrong"})
        self.assertIn("ORIGINAL_SOURCE_CGROUP_PREFLIGHT_INVALID",result["blocked_reasons"])
        self.assertIn("GPU_OWNER_SNAPSHOT_INVALID",result["blocked_reasons"])
