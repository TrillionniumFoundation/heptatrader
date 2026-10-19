"""Prune only reproducible COMGR files of verified terminal research tasks."""
from __future__ import annotations
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import time
import uuid
from contextlib import ExitStack

PROFILE = "TERMINAL_TASK_COMGR_ONLY_V1"
DF = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
FF = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
TERMINAL = {"candidates": {"COMPLETED", "FAILED"},
            "evaluation": {"REJECTED", "QUALIFIED_AWAITING_CANONICAL_OWNER"}}


def signature(s):
    return (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns, s.st_mode, s.st_nlink)


def open_dir(path):
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("ABSOLUTE_NO_SYMLINK_DIRECTORY_REQUIRED")
    fd = os.open("/", DF)
    try:
        for component in path.parts[1:]:
            child = os.open(component, DF, dir_fd=fd)
            os.close(fd); fd = child
        return fd
    except BaseException:
        os.close(fd); raise


def below(fd, parts):
    fd = os.dup(fd)
    try:
        for part in parts:
            child = os.open(part, DF, dir_fd=fd)
            os.close(fd); fd = child
        return fd
    except BaseException:
        os.close(fd); raise


def hash_entry(fd, name):
    expected = signature(os.stat(name, dir_fd=fd, follow_symlinks=False))
    if not stat.S_ISREG(expected[5]) or expected[6] != 1:
        raise ValueError("NOT_REGULAR_SINGLE_LINK")
    file_fd = os.open(name, FF, dir_fd=fd)
    try:
        if signature(os.fstat(file_fd)) != expected:
            raise ValueError("CHANGED_BEFORE_HASH")
        digest = hashlib.sha256()
        while True:
            chunk = os.read(file_fd, 1024**2)
            if not chunk: break
            digest.update(chunk)
        if signature(os.fstat(file_fd)) != expected:
            raise ValueError("CHANGED_DURING_HASH")
        return expected, digest.hexdigest()
    finally:
        os.close(file_fd)


def write_new(fd, name, value):
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
    file_fd = os.open(name, flags, 0o600, dir_fd=fd)
    with os.fdopen(file_fd, "w") as stream:
        json.dump(value, stream, sort_keys=True, separators=(",", ":"), allow_nan=False)
        stream.write("\n"); stream.flush(); os.fsync(stream.fileno())
        identity = signature(os.fstat(stream.fileno()))
    os.fsync(fd)
    return identity


def prune_comgr_cache(run_root, heavy_lock, allowed_tasks, receipt_path, *,
                      dry_run=False, existing_lease_fd=None, selection_evidence=None):
    """Select tasks inside the EXISTING global lease and prune exact filenames.

    A caller-held fd may be duplicated; closing the duplicate never LOCK_UNs or
    closes the original lease. allowed_tasks may be a callable evaluated in-lock.
    Original data, artifacts, logs and every other cache entry are preserved.
    """
    run_root, heavy_lock, receipt_path = map(Path, (run_root, heavy_lock, receipt_path))
    if "comgr-cache" in receipt_path.parts:
        raise ValueError("RECEIPT_MUST_BE_OUTSIDE_COMPILER_CACHE")
    with ExitStack() as stack:
        def keep(fd):
            stack.callback(os.close, fd); return fd
        lock_parent = keep(open_dir(heavy_lock.parent))
        lock_fd = keep(os.dup(existing_lease_fd) if existing_lease_fd is not None else
                       os.open(heavy_lock.name, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=lock_parent))
        ls = os.fstat(lock_fd)
        if not stat.S_ISREG(ls.st_mode) or ls.st_nlink != 1 or ls.st_uid != os.geteuid():
            raise ValueError("EXISTING_PRIVATE_HEAVY_LOCK_REQUIRED")
        binding = (ls.st_dev, ls.st_ino)
        def check_lock():
            current = os.stat(heavy_lock.name, dir_fd=lock_parent, follow_symlinks=False)
            if (not stat.S_ISREG(current.st_mode) or current.st_nlink != 1 or
                (current.st_dev, current.st_ino) != binding):
                raise ValueError("HEAVY_LOCK_BINDING_CHANGED")
        check_lock()
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        check_lock()
        allowed = dict(allowed_tasks() if callable(allowed_tasks) else allowed_tasks)
        tasks = {}
        for task, state in sorted(allowed.items()):
            parts = tuple(task.split("/"))
            if (len(parts) != 2 or parts[0] not in TERMINAL or
                parts[1] in ("", ".", "..") or state not in TERMINAL[parts[0]]):
                raise ValueError("NON_TERMINAL_OR_OUT_OF_SCOPE_TASK")
            tasks[task] = parts
        if not tasks:
            return {"phase": "NO_WORK", "removed_files": 0, "removed_bytes": 0}
        root_fd = keep(open_dir(run_root))
        receipt_fd = keep(open_dir(receipt_path.parent))
        directories = {}
        report = {"schema": "heptatrader.terminal-comgr-cleanup.v1", "profile": PROFILE,
                  "run_root": str(run_root), "heavy_lock": str(heavy_lock),
                  "terminal_states": allowed, "selection_evidence": selection_evidence or {},
                  "dry_run": dry_run, "started_at_ms": int(time.time()*1000),
                  "phase": "PLANNED", "files": [], "skipped": [],
                  "training_evidence_pruned": False}
        for task, parts in tasks.items():
            try:
                cache_fd = keep(below(root_fd, parts + ("gpu-tmp", "comgr-cache")))
            except OSError as exc:
                report["skipped"].append({"task": task, "reason": "CACHE_UNAVAILABLE", "errno": exc.errno})
                continue
            directories[task] = cache_fd
            for name in sorted(os.listdir(cache_fd)):
                if not name.startswith("llvmcache-") or name == "llvmcache-": continue
                relative = task + "/gpu-tmp/comgr-cache/" + name
                try:
                    sig, sha = hash_entry(cache_fd, name)
                except (OSError, ValueError) as exc:
                    report["skipped"].append({"path": relative, "reason": str(exc)})
                    continue
                report["files"].append({"path": relative, "task": task, "name": name,
                                        "size": sig[2], "sha256": sha,
                                        "signature": list(sig), "status": "PLANNED"})
        report["eligible_files"] = len(report["files"])
        report["eligible_bytes"] = sum(v["size"] for v in report["files"])
        if not report["files"]:
            return {**report, "phase": "NO_ELIGIBLE_FILES", "removed_files": 0, "removed_bytes": 0}
        identity = write_new(receipt_fd, receipt_path.name, report)
        report["phase"] = "INTERRUPTED"
        try:
            for entry in report["files"]:
                if dry_run: continue
                check_lock(); cache_fd = directories[entry["task"]]
                try:
                    current = os.stat(entry["name"], dir_fd=cache_fd, follow_symlinks=False)
                    if signature(current) != tuple(entry["signature"]):
                        raise ValueError("IDENTITY_CHANGED_BEFORE_UNLINK")
                    os.unlink(entry["name"], dir_fd=cache_fd)
                    entry["status"] = "REMOVED"
                except (OSError, ValueError) as exc:
                    entry.update(status="SKIPPED", reason=str(exc))
            if not dry_run:
                for fd in directories.values(): os.fsync(fd)
            check_lock(); report["phase"] = "COMPLETED"
        finally:
            removed = [v for v in report["files"] if v["status"] == "REMOVED"]
            report.update(removed_files=len(removed), removed_bytes=sum(v["size"] for v in removed),
                          finished_at_ms=int(time.time()*1000))
            temporary = receipt_path.name + "." + uuid.uuid4().hex + ".tmp"
            write_new(receipt_fd, temporary, report)
            if signature(os.stat(receipt_path.name, dir_fd=receipt_fd, follow_symlinks=False)) != identity:
                raise RuntimeError("RECEIPT_CHANGED_COMPLETED_TEMPORARY_RETAINED")
            os.replace(temporary, receipt_path.name, src_dir_fd=receipt_fd, dst_dir_fd=receipt_fd)
            os.fsync(receipt_fd)
        return report


def terminal_tasks(original, evidence):
    """Read the scheduler/qualification terminal identities under the held lease."""
    root = Path(original["state_root"]); allowed = {}; refs = {}
    database = Path(original["control_directory"]) / "scheduler.sqlite"
    with sqlite3.connect("file:" + str(database) + "?mode=ro", uri=True) as db:
        for job_id, state, payload in db.execute("SELECT id,status,payload FROM jobs WHERE status IN ('COMPLETED','FAILED')"):
            task = "candidates/" + job_id
            job = json.loads(payload)
            if Path(job["output_directory"]) != root/task/"artifact":
                raise ValueError("TERMINAL_CANDIDATE_OUTPUT_IDENTITY")
            allowed[task] = state
            refs[task] = {"job_id": job_id, "state": state,
                          "payload_sha256": hashlib.sha256(payload.encode()).hexdigest()}
    for path in Path(original["evaluation_directory"]).glob("*/qualification.json"):
        cache = path.parent/"gpu-tmp/comgr-cache"
        if not cache.is_dir() or not any(p.name.startswith("llvmcache-") for p in cache.iterdir()):
            continue
        raw = path.read_bytes(); q = json.loads(raw)
        if q.get("state") not in TERMINAL["evaluation"]: continue
        plan_raw = (path.parent/"plan.json").read_bytes(); plan = json.loads(plan_raw)
        if (q["plan_id"] != plan["plan_id"] or q["candidate_id"] != plan["candidate_id"] or
            Path(plan["candidate_directory"]) != root/"candidates"/plan["job_id"]/"artifact"):
            # Rechallenge directories intentionally have a distinct name; their
            # sealed plan still binds the exact original candidate directory.
            raise ValueError("TERMINAL_EVALUATION_IDENTITY")
        task = "evaluation/" + path.parent.name; allowed[task] = q["state"]
        refs[task] = {"qualification_sha256": hashlib.sha256(raw).hexdigest(),
                      "plan_sha256": hashlib.sha256(plan_raw).hexdigest(), "state": q["state"]}
    populated = {}
    for task, state in allowed.items():
        cache = root/task/"gpu-tmp/comgr-cache"
        if cache.is_dir() and not cache.is_symlink() and any(
                p.name.startswith("llvmcache-") and p.name != "llvmcache-" for p in cache.iterdir()):
            populated[task] = state
    if not populated: return {}
    # The shared lease prevents another cooperating worker launch. Check live
    # references as well, so a terminal receipt alone never proves child exit.
    active = set(); unknown_research = []; unreadable_nonresearch = []; scanned = 0
    target = {task: str(root/task) for task in populated}
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit() or int(proc.name) == os.getpid(): continue
        try:
            if proc.stat().st_uid != os.geteuid(): continue
            comm = (proc/"comm").read_text().strip()
            cmd = (proc/"cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
            is_research = comm.startswith(("python", "clang", "ld.lld", "hipcc", "lld")) or "laya_cell" in cmd
            try:
                references = [(proc/"maps").read_text(), os.readlink(proc/"cwd")]
                for fd in (proc/"fd").iterdir():
                    try: references.append(os.readlink(fd))
                    except FileNotFoundError: pass
                scanned += 1
                for task, prefix in target.items():
                    job_config = str(Path(original["control_directory"])/"jobs"/(Path(task).name+".json"))
                    if (any(prefix + "/" in r for r in references) or prefix + "/" in cmd
                        or (task.startswith("candidates/") and job_config in cmd)):
                        active.add(task)
            except PermissionError:
                (unknown_research if is_research else unreadable_nonresearch).append(
                    {"pid": int(proc.name), "comm": comm})
        except (FileNotFoundError, ProcessLookupError):
            continue
    evidence.update(terminal_refs={k: refs[k] for k in populated},
                    readable_processes_scanned=scanned, active_tasks=sorted(active),
                    unknown_research_processes=unknown_research,
                    unreadable_nonresearch_processes=unreadable_nonresearch)
    if unknown_research:
        return {}
    return {task: state for task, state in populated.items() if task not in active}


def sweep_terminal(config, *, existing_lease_fd=None):
    """Opportunistic caller-side maintenance; no training/source admission change."""
    if config.get("cache_maintenance") != PROFILE:
        return {"phase": "DISABLED", "removed_files": 0, "removed_bytes": 0}
    original_path = Path(config["original_config"]); raw = original_path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != config["original_config_sha256"]:
        raise ValueError("ORIGINAL_CONFIG_CHANGED")
    original = json.loads(raw)
    if original.get("automatic_trading") is not False:
        raise ValueError("RESEARCH_SCOPE_REQUIRED")
    receipts = Path(config["state_root"])/"comgr-cache-receipts"
    receipts.mkdir(mode=0o700, exist_ok=True)
    context = {"original_config_sha256": config["original_config_sha256"]}
    return prune_comgr_cache(
        original["state_root"], Path(config["state_root"])/"heavy-worker.lock",
        lambda: terminal_tasks(original, context),
        receipts/("cleanup-"+str(time.time_ns())+"-"+uuid.uuid4().hex[:8]+".json"),
        existing_lease_fd=existing_lease_fd, selection_evidence=context)
