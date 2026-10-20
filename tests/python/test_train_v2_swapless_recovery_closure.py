"""Independent immutable fault/recovery/source receipt semantics, no live OOM."""
import copy
import unittest

from historical_training.common import EvidenceError
from train_v2.swapless_fault_recovery import GIB, MIB, MODES, SCHEMA as PROBE
from train_v2.swapless_recovery_closure import SOURCE_SCHEMA as SOURCE, close


def cgroup(mode,at,pid):
    r = MODES[mode]
    controls = {
        "memory.max":r["max"],"memory.high":r["high"],
        "memory.swap.max":0,"memory.swap.current":0,
        "memory.current":12*MIB,"memory.oom.group":0,
        "cpu.max":"100000 100000","pids.max":32}
    if mode == "fault":
        first, second = (0,0),(1,1)
        exit_code,marker,peak=-9,False,r["max"]
    else:
        first, second = (0,0),(0,0)
        exit_code,marker,peak=0,True,80*MIB
    def events(pair):
        return {"oom":pair[0],"oom_kill":pair[1]}
    return {
        "schema":PROBE,"mode":mode,"actual_kernel_verdict":"PASS",
        "broker_orders_sent":0,"broker_queries_sent":0,
        "gpu_used":False,"model_loaded":False,
        "original_training_guard_changed":False,
        "source_admission_changed":False,"trade_authority":False,
        "actual_kernel_before":controls,"actual_kernel_after":dict(controls),
        "memory_swap_peak_bytes":0,"observed_at_ms":at,"unit_pid":pid,
        "unit_path":"/user.slice/ht-trainv2-cg-"+mode+".service",
        "memory_events_before":events(first),
        "memory_events_after":events(second),
        "actual_oom_kill_delta":second[1]-first[1],
        "child_returncode":exit_code,
        "child_stdout_marker":marker,
        "memory_peak_bytes":peak,
    }


def source():
    return {
        "schema":SOURCE,
        "at_ms":300,
        "isolated_research_preflight":"READY_FOR_OPERATOR_REVIEW_NO_JOB",
        "isolated_preflight_eligible":True,
        "isolated_hold_reasons":[],
        "legacy_production_guard_admit_new_worker":False,
        "legacy_guard_reasons_unchanged":["HOST_SWAP_RESERVE_BELOW_1GIB"],
        "source_authoritative_method":"PINNED_ORIGINAL_GUARD_REFRESH",
        "existing_services_modified":False,"actual_model_loaded":False,
        "broker_orders_sent":0,"trade_authority":False,
        "kernel_cgroup_proof":{
            "cgroup_memory_max_bytes":6*GIB,
            "cgroup_memory_high_bytes":5*GIB,
            "cgroup_swap_current_bytes":0,
            "cgroup_swap_max_bytes":0,
            "cgroup_pids_max":32,
            "cgroup_cpu_max":"100000 100000"},
        "original_live_source_evidence":{
            "schema":"heptatrader.original-live-freshness.v1",
            "source_ttl_ms":5000,"broker_queries_sent":0,
            "quote_timestamp_rewritten":False,
            "quote_known_at_ms":200,
            "verified_live_prefix":{
                "sha256":"f"*64,"uncommitted_tail_bytes":0,
            },
        },
    }


def fixture():
    return (
        {"verdict":"FAILED_NO_QUALIFIED_KERNEL_OOM_RECEIPT",
         "at_ms":25,"failure_never_reclassified_as_success":True},
        cgroup("fault",100,10),
        cgroup("recover",200,20),
        source(),
    )


class SwaplessClosureTests(unittest.TestCase):
    def test_current_policy_cannot_relabel_historical_receipt(self):
        values=list(fixture())
        values[3]["schema"]="heptatrader.train-v2-swapless-isolated-admission.v2"
        with self.assertRaisesRegex(EvidenceError,"RECOVERY_ORIGINAL_GUARD_NOT_PRESERVED"):
            close(*values)

    def test_actual_chain_semantics_returns_no_production_authority(self):
        result=close(*fixture())
        self.assertEqual(result["assessment"],
           "ACTUAL_CGROUP_OOM_RECOVERY_AND_SOURCE_PREFLIGHT_PROVEN")
        self.assertEqual(result["fault_oom_kill_delta"],1)
        self.assertEqual(result["recovery_cgroup_memory_hard_cap_bytes"],6*GIB)
        self.assertFalse(result["gpu_memory_ceiling_qualified"])
        self.assertFalse(result["production_guard_admitted"])
        self.assertFalse(result["automatic_training"])
        self.assertEqual(result["broker_orders_sent"],0)

    def test_missing_kernel_oom_never_qualified(self):
        a=list(fixture())
        a[1]["actual_oom_kill_delta"]=0
        with self.assertRaisesRegex(EvidenceError,"RECOVERY_OOM_COUNTER_CHANGED"):
            close(*a)

    def test_successful_child_is_not_a_fault(self):
        a=list(fixture())
        a[1]["child_returncode"]=0
        with self.assertRaisesRegex(EvidenceError,"RECOVERY_FAULT_NOT_ACTUAL_CGROUP_OOM"):
            close(*a)

    def test_forged_zero_swap_peak_rejects_nonzero(self):
        for index in (1,2):
            a=list(fixture())
            a[index]["memory_swap_peak_bytes"]=4096
            with self.subTest(index=index),self.assertRaises(EvidenceError):
                close(*a)

    def test_forged_unbounded_recovery_limits_refused(self):
        a=list(fixture())
        a[2]["actual_kernel_after"]["memory.max"]=8*GIB
        with self.assertRaisesRegex(EvidenceError,"RECOVERY_KERNEL_CONTROLS_CHANGED"):
            close(*a)

    def test_reused_unit_or_pid_fails_recovery(self):
        for key in ("unit_path","unit_pid"):
            a=list(fixture())
            a[2][key]=a[1][key]
            with self.subTest(key=key),self.assertRaisesRegex(
                    EvidenceError,"RECOVERY_NOT_DISTINCT_TRANSIENT_PROCESS"):
                close(*a)

    def test_clock_order_refuses_replayed_late_source(self):
        a=list(fixture())
        a[3]["at_ms"]=199
        a[3]["original_live_source_evidence"]["quote_known_at_ms"]=190
        with self.assertRaisesRegex(EvidenceError,"RECOVERY_REAL_EVENT_TIME_ORDER_INVALID"):
            close(*a)

    def test_current_production_guard_must_remain_hold(self):
        a=list(fixture())
        a[3]["legacy_production_guard_admit_new_worker"]=True
        with self.assertRaisesRegex(EvidenceError,"RECOVERY_ORIGINAL_GUARD_NOT_PRESERVED"):
            close(*a)

    def test_source_additional_unsafe_hold_refused(self):
        a=list(fixture())
        a[3]["isolated_hold_reasons"]=["SOURCE_CONTINUITY_NOT_READY"]
        with self.assertRaisesRegex(EvidenceError,"RECOVERY_ORIGINAL_GUARD_NOT_PRESERVED"):
            close(*a)

    def test_old_source_and_large_gap_must_not_promote(self):
        a=list(fixture())
        a[3]["original_live_source_evidence"]["quote_known_at_ms"]=-6000
        with self.assertRaisesRegex(EvidenceError,
                      "RECOVERY_FRESH_ORIGINAL_SOURCE_NOT_PROVEN"):
            close(*a)

    def test_source_original_method_is_not_cached_status(self):
        a=list(fixture())
        a[3]["source_authoritative_method"]="PERIODIC_STATUS_ONLY"
        with self.assertRaisesRegex(EvidenceError,
                       "RECOVERY_ORIGINAL_GUARD_NOT_PRESERVED"):
            close(*a)

    def test_failure_receipt_cannot_be_rewritten_success(self):
        a=list(fixture())
        a[0]["verdict"]="PASS"
        with self.assertRaisesRegex(EvidenceError,
                       "RECOVERY_ORIGINAL_FAILURE_NOT_PRESERVED"):
            close(*a)

    def test_gpu_claim_not_inferred_from_kernel_memory(self):
        result=close(*fixture())
        for field in ("gpu_memory_ceiling_qualified",
                      "heavy_gpu_training_qualified",
                      "financial_alpha_qualified",
                      "longitudinal_resource_stability_qualified"):
            with self.subTest(field=field):
                self.assertIs(result[field],False)
