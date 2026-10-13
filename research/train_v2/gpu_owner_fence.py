"""Read-only Linux GPU handle census; NEVER an exclusive training lease.

This only proves positive busy observations or that inspection is incomplete.
A brief idle snapshot cannot fence noncooperating desktop/LLM GPU owners.
No /dev/dri or /dev/kfd device is opened and no model is dispatched.
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import stat
import time
from pathlib import Path

from historical_training.common import EvidenceError, sha

SCHEMA = "heptatrader.train-v2-gpu-owner-fence.v1"
MAX_PIDS = 32768
MAX_FDS = 8192
MAX_HOLDERS = 512
MAX_UNKNOWN = 512
GPU_PATTERN = ("/dev/dri/renderD*", "/dev/dri/card[0-9]*", "/dev/kfd")


def _device_identity(path):
    p = Path(path)
    if p.is_symlink() or not p.exists():
        raise EvidenceError("GPU_DEVICE_PATH_SYMLINK_OR_MISSING")
    st = p.stat()
    if not stat.S_ISCHR(st.st_mode):
        raise EvidenceError("GPU_DEVICE_NOT_CHARACTER")
    return {
        "name": p.name, "path": str(p),
        "major": os.major(st.st_rdev), "minor": os.minor(st.st_rdev),
        "raw_rdev": st.st_rdev,
    }


def physical_gpu_devices():
    paths = sorted(set(
        path for pattern in GPU_PATTERN for path in glob.glob(pattern)))
    found = [_device_identity(p) for p in paths]
    if not found or not any(x["name"].startswith("renderD") for x in found):
        raise EvidenceError("GPU_RENDER_DEVICE_MISSING")
    if not any(x["name"] == "kfd" for x in found):
        raise EvidenceError("GPU_KFD_MISSING_FOR_ROCM")
    pairs = [(x["major"], x["minor"]) for x in found]
    if len(set(pairs)) != len(pairs):
        raise EvidenceError("GPU_DEVICE_IDENTITY_DUPLICATE")
    return found


def _start_ticks(pid_stat):
    # Linux /proc/<pid>/stat field 22. Parenthesized comm may contain ')'.
    pos = pid_stat.rfind(") ")
    if pos < 0:
        raise ValueError("GPU_PID_STAT_PARSE")
    parts = pid_stat[pos + 2:].split()
    if len(parts) < 20:
        raise ValueError("GPU_PID_STAT_SHORT")
    val = parts[19]
    if not val.isdecimal():
        raise ValueError("GPU_PID_START_INVALID")
    return int(val)


def _comm(pidpath):
    try:
        name = (pidpath / "comm").read_text()[:64].strip()
        return "".join(c for c in name if c.isalnum() or c in "-_.")[:48] or "UNKNOWN"
    except (PermissionError, OSError, UnicodeError):
        return "UNKNOWN"


def scan_processes(proc_root, device_specs):
    """Inspect per-PID fd char-device rdev, never names or command line.

    Permission errors, stale PIDs, and fd churn count as incomplete scan;
    they do not grant an idle/available verdict.
    """
    root = Path(proc_root)
    if root.is_symlink() or not root.is_dir():
        raise EvidenceError("GPU_PROC_ROOT_INVALID")
    targets = {x["raw_rdev"]: x["name"] for x in device_specs}
    if not targets:
        raise EvidenceError("GPU_DEVICE_IDENTITY_REQUIRED")
    try:
        names = sorted((p for p in root.iterdir() if p.name.isdecimal()),
                       key=lambda p: int(p.name))
    except OSError as exc:
        raise EvidenceError("GPU_PROC_ENUMERATION_DENIED") from exc
    if len(names) > MAX_PIDS:
        raise EvidenceError("GPU_PROCESS_ENUMERATION_BOUND")
    holders = []
    unknown = []
    inspected = 0
    def missed(pid, reason):
        if len(unknown) < MAX_UNKNOWN:
            unknown.append({"pid": pid, "reason": reason})
    for p in names:
        pid = int(p.name)
        try:
            begin = _start_ticks((p / "stat").read_text())
            uid = p.stat().st_uid
        except FileNotFoundError:
            # Process may have exited. No assumption of stable idleness.
            missed(pid, "PID_EXITED_DURING_ENUMERATION")
            continue
        except (OSError, ValueError) as exc:
            missed(pid, "PID_IDENTITY_UNAVAILABLE")
            continue
        try:
            with os.scandir(p / "fd") as stream:
                handles = list(stream)
        except FileNotFoundError:
            missed(pid, "PID_FD_DIRECTORY_VANISHED")
            continue
        except OSError:
            missed(pid, "PID_FD_DIRECTORY_NOT_INSPECTABLE")
            continue
        if len(handles) > MAX_FDS:
            missed(pid, "PID_FD_COUNT_UNBOUNDED")
            continue
        gpu_fds = []
        partial_fd_inspection = False
        for handle in handles:
            try:
                st = os.stat(handle.path)
            except FileNotFoundError:
                missed(pid, "PID_FD_RACED")
                partial_fd_inspection = True
                continue
            except OSError:
                missed(pid, "PID_FD_STAT_DENIED")
                partial_fd_inspection = True
                continue
            if stat.S_ISCHR(st.st_mode) and st.st_rdev in targets:
                gpu_fds.append({"fd": int(handle.name), "device": targets[st.st_rdev]})
        try:
            end = _start_ticks((p / "stat").read_text())
        except (OSError, ValueError):
            missed(pid, "PID_IDENTITY_CHANGED_AFTER_INSPECTION")
            continue
        if end != begin:
            missed(pid, "PID_REUSE_OR_CLOCK_CHANGE")
            continue
        # A process with one raced / inaccessible FD is NOT fully inspected,
        # even if its other handles could be read successfully.
        if not partial_fd_inspection:
            inspected += 1
        if gpu_fds:
            holders.append({
                "pid": pid, "uid": uid, "start_ticks": begin,
                "comm": _comm(p),
                "device_handles": sorted(gpu_fds, key=lambda x: (x["fd"],x["device"])),
            })
    if len(holders) > MAX_HOLDERS:
        raise EvidenceError("GPU_OWNER_COUNT_BOUND")
    truncated = len(unknown) >= MAX_UNKNOWN
    return {
        "pid_directories_seen": len(names),
        "pids_fully_inspected": inspected,
        "holders": sorted(holders,key=lambda x: (x["pid"],x["start_ticks"])),
        "unknown": unknown,
        "uninspectable_or_racing_pid_count": len(names)-inspected,
        "unknown_evidence_truncated": truncated,
        "all_processes_proven_inspected": inspected == len(names) and not unknown and not truncated,
    }


def owner_fence(proc_root="/proc", *, device_specs=None, delay_seconds=0.15):
    if device_specs is None:
        device_specs = physical_gpu_devices()
    if not 0 <= delay_seconds <= 1:
        raise EvidenceError("GPU_OBSERVATION_INTERVAL_BOUND")
    before = scan_processes(proc_root, device_specs)
    time.sleep(delay_seconds)
    after = scan_processes(proc_root, device_specs)
    reasons = []
    if before["holders"] or after["holders"]:
        reasons.append("GPU_RENDER_OR_KFD_HELD_BY_UNRELATED_PROCESS")
    if not before["all_processes_proven_inspected"] or not after["all_processes_proven_inspected"]:
        reasons.append("GPU_PROCESS_HANDLE_CENSUS_INCOMPLETE")
    if before["holders"] != after["holders"]:
        reasons.append("GPU_HOLDER_SET_CHANGED_BETWEEN_SCANS")
    if not reasons:
        reasons.append("GPU_EXCLUSIVE_LEASE_NOT_ESTABLISHED_BY_IDLE_SNAPSHOT")
    output = {
        "schema":SCHEMA,
        "observed_at_ms":time.time_ns()//1_000_000,
        "device_identities":[{k:v for k,v in x.items() if k!="raw_rdev"} for x in device_specs],
        "before":before, "after":after,
        "fail_closed_reasons":reasons,
        "gpu_lease_exclusive":False,
        "actual_gpu_4to6gib_memory_ceiling_proven":False,
        "rocm_kernel_or_model_loaded":False,
        "original_source_causal_readiness_proven":False,
        "host_swap_guard_overridden":False,
        "can_start_gpu_training":False,
        "automatic_training":False,
        "trade_authority":False,
        "broker_queries_sent":0,
        "broker_orders_sent":0,
    }
    return output


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--output",required=True,type=Path)
    a=ap.parse_args()
    os.umask(0o077)
    result=owner_fence()
    result["source_sha256"]=sha(Path(__file__))
    path=a.output
    if path.is_symlink() or path.exists():
        raise EvidenceError("GPU_OWNER_RECEIPT_ALREADY_EXISTS")
    path.parent.mkdir(parents=True,mode=0o700,exist_ok=True)
    if path.parent.is_symlink() or path.parent.stat().st_mode & 0o077:
        raise EvidenceError("GPU_OWNER_PRIVATE_RECEIPT_DIRECTORY_REQUIRED")
    raw=(json.dumps(result,sort_keys=True,indent=2)+"\n").encode()
    fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_WRONLY,0o600)
    with os.fdopen(fd,"wb") as out:
        out.write(raw);out.flush();os.fsync(out.fileno())
    directory=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:os.fsync(directory)
    finally:os.close(directory)
    print(json.dumps({
        "gpu_exclusive_lease":False,
        "holders_first":len(result["before"]["holders"]),
        "holders_second":len(result["after"]["holders"]),
        "incomplete_first":result["before"]["uninspectable_or_racing_pid_count"],
        "incomplete_second":result["after"]["uninspectable_or_racing_pid_count"],
        "denial_reasons":result["fail_closed_reasons"],
        "broker_orders_sent":0,
        "receipt_sha256":hashlib.sha256(raw).hexdigest(),
    },sort_keys=True))
    return 2

if __name__=="__main__":
    raise SystemExit(main())
