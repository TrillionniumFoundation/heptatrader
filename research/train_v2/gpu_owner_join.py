"""One isolated zero-swap + original-source + GPU-owner REJECTION receipt.

Deliberately read-only: not a production Guard modification, ROCm probe,
resource lease or GPU admission; cannot start training in any status.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from pathlib import Path

from historical_training.common import EvidenceError, sha
from .gpu_owner_fence import owner_fence
from .swapless_resource import once

SCHEMA="heptatrader.train-v2-gpu-owner-original-gate-join.v1"


def verify_join(source, gpu, *, source_exception=None, gpu_exception=None):
    reasons=[]
    if source_exception is not None:
        reasons.append("ORIGINAL_SOURCE_CGROUP_PREFLIGHT_UNAVAILABLE")
    elif not isinstance(source,dict) or source.get("schema")!="heptatrader.train-v2-swapless-isolated-admission.v1":
        reasons.append("ORIGINAL_SOURCE_CGROUP_PREFLIGHT_INVALID")
    else:
        reasons.extend(source.get("isolated_hold_reasons") or [])
        if (source.get("real_quote_gate_unchanged") is not True
                or source.get("source_authoritative_method")!="PINNED_ORIGINAL_GUARD_REFRESH"):
            reasons.append("ORIGINAL_SOURCE_NOT_REVERIFIED")
        if source.get("isolated_preflight_eligible") is not True:
            reasons.append("ORIGINAL_CGROUP_RESOURCE_OR_SOURCE_NOT_READY")
    if gpu_exception is not None:
        reasons.append("GPU_OWNER_KERNEL_CENSUS_UNAVAILABLE")
    elif not isinstance(gpu,dict) or gpu.get("schema")!="heptatrader.train-v2-gpu-owner-fence.v1":
        reasons.append("GPU_OWNER_SNAPSHOT_INVALID")
    else:
        reasons.extend(gpu.get("fail_closed_reasons") or [])
        if gpu.get("gpu_lease_exclusive") is not False or gpu.get("can_start_gpu_training") is not False:
            reasons.append("GPU_SNAPSHOT_CANNOT_DECLARE_LEASE")
    reasons.append("GPU_NONCOOPERATING_OWNER_LEASE_NOT_ENFORCED")
    return {
        "schema":SCHEMA,
        "observed_at_ms":time.time_ns()//1_000_000,
        "original_swapless_preflight":source,
        "gpu_owner_census":gpu,
        "source_preflight_error":source_exception,
        "gpu_owner_scan_error":gpu_exception,
        "blocked_reasons":list(dict.fromkeys(reasons)),
        "gpu_training_admission":"HOLD_NO_EXCLUSIVE_GPU_LEASE",
        "kernel_gpu_device_isolation_proven":False,
        "actual_rocm_device_memory_ceiling_proven":False,
        "real_4to6gib_model_loaded":False,
        "source_continuity_through_model_job_proven":False,
        "original_prod_guard_modified":False,
        "existing_gpu_process_signaled_or_paused":False,
        "automatic_training":False,
        "broker_queries_sent":0,
        "broker_orders_sent":0,
        "trade_authority":False,
    }


def _safe_error(exc):
    # Avoid emitting arbitrary paths / private data in a public status field.
    if isinstance(exc,EvidenceError):
        msg=str(exc)
        if msg and len(msg)<100 and all(c.isalnum() or c=="_" for c in msg):
            return msg
    return type(exc).__name__[:55]


def main():
    p=argparse.ArgumentParser()
    for field in ("guard-config","source-file","period-file","manual-hold","output"):
        p.add_argument("--"+field,required=True,type=Path)
    args=p.parse_args()
    os.umask(0o077)
    original=None
    gpu=None
    source_error=None
    gpu_error=None
    try:
        original=once(args.source_file,args.period_file,args.manual_hold,args.guard_config)
    except (OSError,ValueError,TypeError,KeyError) as exc:
        source_error=_safe_error(exc)
    try:
        gpu=owner_fence()
    except (OSError,ValueError,TypeError,KeyError) as exc:
        gpu_error=_safe_error(exc)
    report=verify_join(original,gpu,source_exception=source_error,gpu_exception=gpu_error)
    report["source_code_sha256"]={
        name:sha(Path(__file__).with_name(name))
        for name in ("gpu_owner_fence.py","gpu_owner_join.py","swapless_resource.py")
    }
    output=args.output
    if output.exists() or output.is_symlink():
        raise EvidenceError("GPU_JOIN_OUTPUT_IMMUTABLE_REQUIRED")
    output.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    if output.parent.is_symlink() or output.parent.stat().st_mode & 0o077:
        raise EvidenceError("GPU_JOIN_PRIVATE_OUTPUT_REQUIRED")
    raw=(json.dumps(report,sort_keys=True,indent=2)+"\n").encode()
    fd=os.open(output,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,"wb") as target:
        target.write(raw);target.flush();os.fsync(target.fileno())
    parent=os.open(output.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:os.fsync(parent)
    finally:os.close(parent)
    print(json.dumps({
        "gpu_training_admission":report["gpu_training_admission"],
        "source_preflight":(
            original.get("isolated_research_preflight") if isinstance(original,dict)
            else "UNAVAILABLE"),
        "gpu_holders":len(gpu["after"]["holders"]) if isinstance(gpu,dict) else None,
        "gpu_unknown_pids":(
            gpu["after"]["uninspectable_or_racing_pid_count"]
            if isinstance(gpu,dict) else None),
        "gpu_kernel_lease_proven":False,
        "broker_orders_sent":0,
        "receipt_sha256":hashlib.sha256(raw).hexdigest(),
    },sort_keys=True))
    return 2

if __name__=="__main__":
    raise SystemExit(main())
