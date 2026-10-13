"""Optional isolated swapless training-lane *preflight*, never production guard.

All limits must be enforced by the kernel in THIS transient cgroup. Low global
swap free is not excused on the live resident services; no existing worker is
reconfigured and a passed preflight cannot dispatch a GPU job or broker request.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import time

from historical_training.common import EvidenceError, read, sha, atomic
from historical_training.resident_resource_guard import (
    GIB, SOURCE_RULE, RESOURCE_RULE, assess, parse_meminfo, parse_full_psi,
)

SCHEMA="heptatrader.train-v2-swapless-isolated-admission.v1"
MAX_WORKER_BYTES=6*GIB
MIN_WORKER_BYTES=4*GIB
SPARE_AFTER_CAP_BYTES=8*GIB+512*1024*1024


def strict_num(raw, *, allow_max=False):
    if allow_max and raw.strip()=="max":return None
    if raw.strip().isdigit():
        v=int(raw.strip())
        if 0<=v<=2**62:return v
    raise EvidenceError("SWAPLESS_INVALID_CGROUP_NUMBER")


def verify_cgroup(values):
    """Require kernel-enforced real memory isolation, never caller config."""
    if not isinstance(values,dict):
        raise EvidenceError("SWAPLESS_CGROUP_EVIDENCE_MISSING")
    mem=values.get("memory.max")
    high=values.get("memory.high")
    current=values.get("memory.current")
    swap=values.get("memory.swap.max")
    swap_used=values.get("memory.swap.current")
    pids=values.get("pids.max")
    cpu=values.get("cpu.max")
    if (type(mem) is not int or not MIN_WORKER_BYTES<=mem<=MAX_WORKER_BYTES
        or type(high) is not int or not MIN_WORKER_BYTES<=high<=mem
        or type(current) is not int or not 0<=current<high
        or swap != 0 or swap_used != 0
        or type(pids) is not int or not 1<=pids<=64
        or not isinstance(cpu,str)):
        raise EvidenceError("SWAPLESS_CGROUP_KERNEL_BOUNDS_NOT_PROVEN")
    q=cpu.split()
    if len(q)!=2 or not all(x.isdigit() for x in q):
        raise EvidenceError("SWAPLESS_CPU_QUOTA_REQUIRED")
    quota,period=map(int,q)
    if not 1000<=quota<=period or not 1000<=period<=1000000:
        raise EvidenceError("SWAPLESS_CPU_QUOTA_UNBOUNDED")
    return {"cgroup_memory_max_bytes":mem,"cgroup_memory_high_bytes":high,
            "cgroup_memory_current_bytes":current,
            "cgroup_swap_max_bytes":swap,"cgroup_swap_current_bytes":swap_used,
            "cgroup_pids_max":pids,"cgroup_cpu_max":cpu,
            "kernel_enforced_swapless":True}


def current_cgroup():
    """Read unified cgroup v2 for this process, NO writes to sysfs."""
    data=Path("/proc/self/cgroup").read_text()
    rows=[line for line in data.splitlines() if line.startswith("0::/")]
    if len(rows)!=1:raise EvidenceError("SWAPLESS_UNIFIED_CGROUP_NOT_FOUND")
    relative=rows[0][3:]
    p=Path(relative)
    if not p.is_absolute() or ".." in p.parts or not str(p).startswith("/user.slice/"):
        raise EvidenceError("SWAPLESS_CGROUP_PATH_NOT_SCOPED")
    root=Path("/sys/fs/cgroup")
    target=root / str(p).lstrip("/")
    if target.is_symlink() or not target.is_dir():
        raise EvidenceError("SWAPLESS_CGROUP_PATH_INVALID")
    keys=("memory.max","memory.high","memory.current","memory.swap.max",
          "memory.swap.current","pids.max","cpu.max")
    out={}
    for key in keys:
        path=target/key
        if path.is_symlink() or not path.is_file():
            raise EvidenceError("SWAPLESS_CGROUP_KERNEL_FILE_MISSING")
        data=path.read_text()
        out[key]=(data.strip() if key=="cpu.max" else
                   strict_num(data,allow_max=True))
    return out


def assess_isolated(host,pressure,source,now_ms,period,manual_hold,cgroup):
    """Keep original guard intact. Result cannot be production admission."""
    original=assess(host,pressure,source,now_ms,period,manual_hold)
    issues=[x for x in original["reasons"]
            if x!="HOST_SWAP_RESERVE_BELOW_1GIB"]
    proof=None
    try:
        proof=verify_cgroup(cgroup)
    except (EvidenceError,KeyError,TypeError,ValueError) as exc:
        issues.append(str(exc))
    # Exact bound, no rewrite of current source/cost/production policies.
    if proof and host["MemAvailable"]-proof["cgroup_memory_max_bytes"]<SPARE_AFTER_CAP_BYTES:
        if "SWAPLESS_INSUFFICIENT_HOST_RAM_AFTER_HARD_CAP" not in issues:
            issues.append("SWAPLESS_INSUFFICIENT_HOST_RAM_AFTER_HARD_CAP")
    if proof is None:
        issues.append("SWAPLESS_CGROUP_NOT_ACTUALLY_VERIFIED")
    eligible=not issues
    return {
        "schema":SCHEMA,"at_ms":now_ms,
        "legacy_production_guard_admit_new_worker":original["admit_new_worker"],
        "legacy_guard_reasons_unchanged":original["reasons"],
        "isolated_research_preflight":("READY_FOR_OPERATOR_REVIEW_NO_JOB"
                                       if eligible else "HOLD_ISOLATED_RESEARCH"),
        "isolated_preflight_eligible":eligible,
        "isolated_hold_reasons":list(dict.fromkeys(issues)),
        "kernel_cgroup_proof":proof,
        "host_mem_available_bytes":host["MemAvailable"],
        "host_swap_free_bytes":host["SwapFree"],
        "host_memory_full_psi":pressure,
        "real_quote_gate_unchanged":True,
        "original_horizon_rules_unchanged":True,
        "actual_model_loaded":False,
        "existing_services_modified":False,
        "broker_orders_sent":0,
        "automatic_training":False,"trade_authority":False}


def once(source_file,period_file,manual_hold,guard_config=None):
    if guard_config is None:
        raise EvidenceError("SWAPLESS_PINNED_ORIGINAL_GUARD_REQUIRED")
    # Never synthesize quote freshness. With pinned original Guard config,
    # refresh exactly the same ORIGINAL local hash-chained source as the
    # current production guard. A periodic status alone is not new evidence.
    period=sha(period_file)
    src=read(source_file,1024*1024)
    started_at_ms=time.time_ns()//1_000_000
    source_proof=None
    if guard_config is not None:
        from historical_training.resident_resource_guard import verify_config
        from historical_training.resident_live_source import refresh
        cfg=read(guard_config,16384)
        if (cfg.get("period_path")!=str(period_file)
            or cfg.get("source_health_path")!=str(source_file)
            or cfg.get("period_sha256")!=period
            or cfg.get("manual_hold_path")!=str(manual_hold)):
            raise EvidenceError("SWAPLESS_GUARD_ORIGINAL_SCOPE_MISMATCH")
        original=verify_config(cfg)
        src,source_proof=refresh(original,src,started_at_ms)
    # The original source may have expired WHILE we verified its chain.
    # Admission must use the wall clock after expensive source verification.
    now=time.time_ns()//1_000_000
    result=assess_isolated(
        parse_meminfo(Path("/proc/meminfo").read_text()),
        parse_full_psi(Path("/proc/pressure/memory").read_text()),
        src,now,period,os.path.lexists(str(manual_hold)),current_cgroup())
    if source_proof is not None:
        result["original_live_source_evidence"]=source_proof
        result["source_authoritative_method"]="PINNED_ORIGINAL_GUARD_REFRESH"
    else:
        result["source_authoritative_method"]="PERIODIC_STATUS_ONLY_NO_NEW_QUOTE_PROOF"
    return result


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--source-file",type=Path,required=True)
    parser.add_argument("--period-file",type=Path,required=True)
    parser.add_argument("--manual-hold",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--guard-config",type=Path,required=True)
    args=parser.parse_args()
    os.umask(0o077)
    if args.output.is_symlink() or args.output.exists():
        raise EvidenceError("SWAPLESS_OUTPUT_IMMUTABLE_REQUIRED")
    result=once(args.source_file,args.period_file,args.manual_hold,args.guard_config)
    args.output.parent.mkdir(parents=True,mode=0o700,exist_ok=True)
    raw=(json.dumps(result,sort_keys=True,indent=2)+"\n").encode()
    fd=os.open(args.output,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,"wb") as f:
        f.write(raw);f.flush();os.fsync(f.fileno())
    dirfd=os.open(args.output.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:os.fsync(dirfd)
    finally:os.close(dirfd)
    print(json.dumps({
        "preflight":result["isolated_research_preflight"],
        "original_guard_unchanged":True,
        "original_guard_reasons":result["legacy_guard_reasons_unchanged"],
        "isolated_hold_reasons":result["isolated_hold_reasons"],
        "kernel_swapless":bool(result["kernel_cgroup_proof"]),
        "existing_services_modified":False,
        "broker_orders_sent":0,
    },sort_keys=True))
    return 0 if result["isolated_preflight_eligible"] else 2

if __name__=="__main__":
    raise SystemExit(main())
