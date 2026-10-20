"""Independent offline proof of fault -> fresh isolated recovery -> source gate.

Consumes only existing immutable evidence, never launches jobs or contacts IB.
All three positive proofs are actual kernel/original-source measurements.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path

from historical_training.common import EvidenceError, read, sha
from .swapless_fault_recovery import GIB, MIB, MODES, SCHEMA as PROBE_SCHEMA
# This verifier describes the sealed 2026-10-13 experiment, not current
# resource admission. Never reinterpret its low-swap v1 evidence as v2.
SOURCE_SCHEMA = "heptatrader.train-v2-swapless-isolated-admission.v1"

SCHEMA = "heptatrader.train-v2-swapless-fault-recovery-closure.v1"


def _proof(group, kind):
    if (not isinstance(group, dict) or group.get("schema") != PROBE_SCHEMA
            or group.get("mode") != kind
            or group.get("actual_kernel_verdict") != "PASS"
            or group.get("broker_orders_sent") != 0
            or group.get("broker_queries_sent") != 0
            or group.get("gpu_used") is not False
            or group.get("model_loaded") is not False
            or group.get("original_training_guard_changed") is not False
            or group.get("source_admission_changed") is not False
            or group.get("trade_authority") is not False):
        raise EvidenceError("RECOVERY_BROKER_OR_EFFECT_BOUNDARY_CHANGED")
    before, after = group.get("actual_kernel_before"), group.get("actual_kernel_after")
    target = MODES[kind]
    if not isinstance(before, dict) or not isinstance(after, dict):
        raise EvidenceError("RECOVERY_KERNEL_CONTROL_MISSING")
    for snapshot in (before, after):
        if (snapshot.get("memory.max") != target["max"]
                or snapshot.get("memory.high") != target["high"]
                or snapshot.get("memory.swap.max") != 0
                or snapshot.get("memory.swap.current") != 0
                or snapshot.get("memory.oom.group") != 0
                or snapshot.get("pids.max") != 32
                or snapshot.get("cpu.max") != "100000 100000"):
            raise EvidenceError("RECOVERY_KERNEL_CONTROLS_CHANGED")
    if (group.get("memory_swap_peak_bytes") != 0
            or type(group.get("observed_at_ms")) is not int
            or type(group.get("unit_pid")) is not int
            or not str(group.get("unit_path", "")).endswith(".service")
            or "ht-trainv2-cg-" not in group["unit_path"]):
        raise EvidenceError("RECOVERY_KERNEL_PROOF_IDENTITY")
    eb, ea = group.get("memory_events_before"), group.get("memory_events_after")
    if not isinstance(eb, dict) or not isinstance(ea, dict):
        raise EvidenceError("RECOVERY_MEMORY_EVENTS_MISSING")
    delta = ea.get("oom_kill", -1) - eb.get("oom_kill", -1)
    if group.get("actual_oom_kill_delta") != delta:
        raise EvidenceError("RECOVERY_OOM_COUNTER_CHANGED")
    if kind == "fault":
        if (delta < 1 or group.get("child_returncode") != -9
                or ea.get("oom", 0) <= eb.get("oom", 0)):
            raise EvidenceError("RECOVERY_FAULT_NOT_ACTUAL_CGROUP_OOM")
    else:
        if (delta != 0 or group.get("child_returncode") != 0
                or group.get("child_stdout_marker") is not True
                or group.get("memory_peak_bytes", target["max"]+1) > target["max"]):
            raise EvidenceError("RECOVERY_POST_FAULT_WORKER_NOT_HEALTHY")
    return group


def _source(proof):
    if (not isinstance(proof, dict) or proof.get("schema") != SOURCE_SCHEMA
            or proof.get("isolated_research_preflight") != "READY_FOR_OPERATOR_REVIEW_NO_JOB"
            or proof.get("isolated_preflight_eligible") is not True
            or proof.get("isolated_hold_reasons") != []
            or proof.get("legacy_production_guard_admit_new_worker") is not False
            or proof.get("legacy_guard_reasons_unchanged") != ["HOST_SWAP_RESERVE_BELOW_1GIB"]
            or proof.get("source_authoritative_method") != "PINNED_ORIGINAL_GUARD_REFRESH"
            or proof.get("existing_services_modified") is not False
            or proof.get("actual_model_loaded") is not False
            or proof.get("broker_orders_sent") != 0
            or proof.get("trade_authority") is not False):
        raise EvidenceError("RECOVERY_ORIGINAL_GUARD_NOT_PRESERVED")
    kernel = proof.get("kernel_cgroup_proof") or {}
    if (kernel.get("cgroup_memory_max_bytes") != 6*GIB
            or kernel.get("cgroup_memory_high_bytes") != 5*GIB
            or kernel.get("cgroup_swap_current_bytes") != 0
            or kernel.get("cgroup_swap_max_bytes") != 0
            or kernel.get("cgroup_pids_max") != 32
            or kernel.get("cgroup_cpu_max") != "100000 100000"):
        raise EvidenceError("RECOVERY_SOURCE_KERNEL_CELL_NOT_BOUND")
    source = proof.get("original_live_source_evidence") or {}
    prefix = source.get("verified_live_prefix") or {}
    latest = source.get("quote_known_at_ms")
    if (source.get("schema") != "heptatrader.original-live-freshness.v1"
            or source.get("source_ttl_ms") != 5000
            or source.get("broker_queries_sent") != 0
            or source.get("quote_timestamp_rewritten") is not False
            or not isinstance(latest, int)
            or type(proof.get("at_ms")) is not int
            or not latest <= proof["at_ms"] <= latest + 5000
            or not isinstance(prefix.get("sha256"), str)
            or len(prefix["sha256"]) != 64
            or prefix.get("uncommitted_tail_bytes") != 0):
        raise EvidenceError("RECOVERY_FRESH_ORIGINAL_SOURCE_NOT_PROVEN")
    return proof


def close(failed, fault, recovery, source):
    if (failed.get("verdict") != "FAILED_NO_QUALIFIED_KERNEL_OOM_RECEIPT"
            or failed.get("failure_never_reclassified_as_success") is not True):
        raise EvidenceError("RECOVERY_ORIGINAL_FAILURE_NOT_PRESERVED")
    fault = _proof(fault, "fault")
    recovery = _proof(recovery, "recover")
    source = _source(source)
    if (not failed.get("at_ms", 0) < fault["observed_at_ms"]
            < recovery["observed_at_ms"] < source["at_ms"]):
        raise EvidenceError("RECOVERY_REAL_EVENT_TIME_ORDER_INVALID")
    if (fault["unit_path"] == recovery["unit_path"]
            or fault["unit_pid"] == recovery["unit_pid"]):
        raise EvidenceError("RECOVERY_NOT_DISTINCT_TRANSIENT_PROCESS")
    report = {
        "schema": SCHEMA,
        "assessment": "ACTUAL_CGROUP_OOM_RECOVERY_AND_SOURCE_PREFLIGHT_PROVEN",
        "original_timeout_failure_preserved": True,
        "fault_oom_kill_delta": fault["actual_oom_kill_delta"],
        "fault_cgroup_memory_hard_cap_bytes": fault["actual_kernel_after"]["memory.max"],
        "recovery_cgroup_memory_hard_cap_bytes": recovery["actual_kernel_after"]["memory.max"],
        "recovery_distinct_new_pid_and_cgroup": True,
        "verified_zero_swap_peak_both_units": True,
        "source_preflight_state_at_its_original_observation": source["isolated_research_preflight"],
        "production_guard_admitted": False,
        "legacy_guard_reasons_preserved": source["legacy_guard_reasons_unchanged"],
        "source_snapshot_not_permanent_readiness": True,
        "gpu_memory_ceiling_qualified": False,
        "heavy_gpu_training_qualified": False,
        "longitudinal_resource_stability_qualified": False,
        "native_canonical_owner_enrolled": False,
        "financial_alpha_qualified": False,
        "automatic_training": False,
        "production_mutation": False,
        "broker_queries_sent": 0,
        "broker_orders_sent": 0,
        "trade_authority": False,
    }
    return report


def main():
    ap = argparse.ArgumentParser()
    for name in ("failed", "fault", "recovery", "source", "output"):
        ap.add_argument("--"+name, required=True, type=Path)
    opts = ap.parse_args()
    if opts.output.exists() or opts.output.is_symlink():
        raise EvidenceError("RECOVERY_CLOSURE_NEW_IMMUTABLE_OUTPUT_REQUIRED")
    records = [read(getattr(opts, name), 1024*1024)
               for name in ("failed", "fault", "recovery", "source")]
    result = close(*records)
    result["inputs_sha256"] = {
        name: sha(getattr(opts, name)) for name in
        ("failed", "fault", "recovery", "source")}
    result["source_code_sha256"] = {
        name: sha(Path(__file__).with_name(name)) for name in
        ("swapless_fault_recovery.py", "swapless_recovery_closure.py", "swapless_resource.py")}
    os.umask(0o077)
    opts.output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    raw = (json.dumps(result, sort_keys=True, indent=2)+"\n").encode()
    fd=os.open(opts.output, os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd,"wb") as stream:
        stream.write(raw);stream.flush();os.fsync(stream.fileno())
    folder=os.open(opts.output.parent,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(folder)
    finally:os.close(folder)
    print(json.dumps({
        "assessment":result["assessment"],
        "actual_kernel_oom_kills":result["fault_oom_kill_delta"],
        "swap_peak_bytes":0,
        "original_guard_admitted":False,
        "heavy_gpu_qualified":False,
        "orders_sent":0,
        "receipt_sha256":hashlib.sha256(raw).hexdigest(),
    },sort_keys=True))


if __name__=="__main__":
    main()
