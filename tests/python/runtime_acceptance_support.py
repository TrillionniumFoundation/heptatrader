"""Bounded measurements and OFFLINE backups for disposable simulator fixtures.

Not a live-service backup/restore API. Callers must have stopped every fixture
process. Private checkpoint contents stay in the fixture TemporaryDirectory;
only digests and measurements belong in exported test evidence.
"""
from __future__ import annotations
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import stat
import subprocess
import sys
import time

MAX_FILES = 64
MAX_BYTES = 16 * 1024 * 1024


def bounded_integer(text: str, minimum: int, maximum: int) -> int:
    if not isinstance(text, str) or not text.isascii() or not text.isdecimal():
        raise ValueError("expected a bounded decimal integer")
    value = int(text)
    if not minimum <= value <= maximum:
        raise ValueError(f"value must be between {minimum} and {maximum}")
    return value


def summarize_latency(samples_ns: list[int], elapsed_ns: int) -> dict:
    if not samples_ns or any(type(x) is not int or x <= 0 for x in samples_ns):
        raise ValueError("positive observed latency samples required")
    if type(elapsed_ns) is not int or elapsed_ns < sum(samples_ns):
        raise ValueError("elapsed time cannot understate measured call time")
    ordered = sorted(samples_ns)
    def rank(p: float) -> float:
        return ordered[math.ceil(p * len(ordered)) - 1] / 1_000_000
    return {
        "sample_count": len(ordered), "elapsed_seconds": elapsed_ns / 1_000_000_000,
        "completed_calls_per_second": len(ordered) * 1_000_000_000 / elapsed_ns,
        "latency_ms": {"p50": rank(.50), "p95": rank(.95), "p99": rank(.99),
                       "p999": rank(.999) if len(ordered) >= 1000 else None,
                       "maximum": ordered[-1] / 1_000_000},
        "quantile_method": "nearest-rank",
        "measurement_boundary": "CLI process spawn + authentication + IPC + response decode",
        "p999_note": "not reported below 1000 samples; sample quantiles are not SLO guarantees",
    }


def measure_reads(call, samples: int) -> dict:
    bounded_integer(str(samples), 128, 2000)
    observations = []
    started = time.perf_counter_ns()
    for _ in range(samples):
        before = time.perf_counter_ns()
        value = call()
        observations.append(time.perf_counter_ns() - before)
        if value.get("authoritative") is not True or value.get("stale") is True:
            raise AssertionError("load probe did not receive an authoritative fresh quote")
    return summarize_latency(observations, time.perf_counter_ns() - started)



def process_resources(runtime) -> dict:
    """Inspect each already verified fixture PID as that service's own UID."""
    result = {}
    for process, _, _, name in runtime.processes:
        owner = next(item for item in reversed(runtime.observed_processes) if item["pid"] == process.pid)
        script = """import json,os,sys
from pathlib import Path
root=Path('/proc')/sys.argv[1]
status={line.split(':',1)[0]:line.split(':',1)[1].strip() for line in (root/'status').read_text().splitlines() if ':' in line}
print(json.dumps({'fd_count':len(list((root/'fd').iterdir())), 'rss_kib':int(status['VmRSS'].split()[0]), 'peak_rss_kib':int(status['VmHWM'].split()[0]), 'threads':int(status['Threads'])}))
"""
        observed = subprocess.run([sys.executable, "-c", script, str(process.pid)],
            user=owner["uid"], group=(runtime.root / "es").stat().st_gid, extra_groups=[],
            env={"PATH":"/usr/bin:/bin","LC_ALL":"C"}, stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=5, check=True)
        result[name] = json.loads(observed.stdout)
    return result


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
            info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def regular_bytes(path: Path, limit: int = MAX_BYTES) -> tuple[bytes, os.stat_result]:
    # A FIFO must be rejected before read; a pathname exchange cannot substitute
    # a different leaf after the bounded descriptor has been admitted.
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > limit:
            raise ValueError("unsafe or oversized checkpoint file")
        chunks = []; size = 0
        while size <= limit:
            chunk = os.read(fd, min(65536, limit + 1 - size))
            if not chunk: break
            chunks.append(chunk); size += len(chunk)
        if size > limit or _identity(before) != _identity(os.fstat(fd)) or _identity(before) != _identity(path.lstat()):
            raise ValueError("checkpoint input changed or exceeded bounds")
        return b"".join(chunks), before
    finally:
        os.close(fd)


def _write_new(path: Path, content: bytes, *, mode: int, uid: int, gid: int) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    try:
        os.fchmod(fd, mode)
        os.fchown(fd, uid, gid)
        with os.fdopen(fd, "wb", closefd=False) as stream:
            stream.write(content); stream.flush(); os.fsync(fd)
    finally:
        os.close(fd)
    parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try: os.fsync(parent)
    finally: os.close(parent)


def checkpoint_stopped_fixture(runtime, destination: Path) -> dict:
    if runtime.processes:
        raise ValueError("stop every fixture process before checkpointing")
    destination.mkdir(mode=0o700)
    rows = []; total = 0
    for family in ("es", "gs"):
        directory = runtime.root / family
        if not stat.S_ISDIR(directory.lstat().st_mode):
            raise ValueError("fixture state must be a real private directory")
        (destination / family).mkdir(mode=0o700)
        for source in sorted(directory.iterdir()):
            contents, info = regular_bytes(source, MAX_BYTES - total)
            total += len(contents)
            if len(rows) >= MAX_FILES:
                raise ValueError("checkpoint file count exceeded")
            name = family + "/" + source.name
            row = {"path": name, "sha256": hashlib.sha256(contents).hexdigest(),
                   "size": len(contents), "mode": stat.S_IMODE(info.st_mode),
                   "uid": info.st_uid, "gid": info.st_gid}
            _write_new(destination / name, contents, mode=0o400, uid=os.geteuid(), gid=os.getegid())
            rows.append(row)
    manifest = {"schema": "heptatrader.simulator-test-checkpoint.v1", "files": rows}
    _write_new(destination / "manifest.json", json.dumps(manifest, sort_keys=True).encode(),
               mode=0o600, uid=os.geteuid(), gid=os.getegid())
    return manifest


def load_checkpoint(destination: Path) -> list[tuple[dict, bytes]]:
    def unique(pairs):
        result = {}
        for k, v in pairs:
            if k in result: raise ValueError("duplicate checkpoint field")
            result[k] = v
        return result
    manifest = json.loads(regular_bytes(destination / "manifest.json", 65536)[0], object_pairs_hook=unique)
    if not isinstance(manifest, dict) or set(manifest) != {"schema", "files"} or manifest["schema"] != "heptatrader.simulator-test-checkpoint.v1":
        raise ValueError("unsupported checkpoint")
    rows = manifest["files"]
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_FILES:
        raise ValueError("invalid checkpoint file inventory")
    result = []; seen = set(); total = 0
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"path", "sha256", "size", "mode", "uid", "gid"}:
            raise ValueError("invalid checkpoint entry")
        path = PurePosixPath(row["path"])
        if len(path.parts) != 2 or path.parts[0] not in {"es", "gs"} or path.as_posix() != row["path"] or path.name in {".", ".."} or row["path"] in seen:
            raise ValueError("unsafe or duplicate checkpoint path")
        seen.add(row["path"])
        if any(type(row[k]) is not int or row[k] < 0 for k in ("mode", "uid", "gid", "size")) or row["mode"] & ~0o700:
            raise ValueError("unsafe checkpoint metadata")
        parent = destination / path.parts[0]
        if not stat.S_ISDIR(parent.lstat().st_mode):
            raise ValueError("checkpoint namespace substitution")
        data, _ = regular_bytes(destination / row["path"], MAX_BYTES - total)
        total += len(data)
        if len(data) != row["size"] or hashlib.sha256(data).hexdigest() != row["sha256"]:
            raise ValueError("checkpoint digest/size mismatch")
        result.append((row, data))
    return result


def restore_new_fixture(destination: Path, runtime) -> dict:
    if runtime.processes:
        raise ValueError("cannot restore running fixture")
    entries = load_checkpoint(destination)  # validate ALL bytes before writes
    # Never adopt an old state tree: only a freshly constructed test fixture is
    # allowed, including its one known placeholder key. Root/path supplied by
    # the caller comes from TemporaryDirectory, never a service command line.
    if list((runtime.root / "es").iterdir()) or {p.name for p in (runtime.root / "gs").iterdir()} != {"key"}:
        raise ValueError("restore destination is not a new fixture")
    key = runtime.root / "gs/key"
    placeholder, info = regular_bytes(key, 32)
    if placeholder != b"K" * 32 or stat.S_IMODE(info.st_mode) != 0o400:
        raise ValueError("restore placeholder identity mismatch")
    for row, _ in entries:
        expected = (runtime.root / PurePosixPath(row["path"]).parts[0]).stat()
        if row["uid"] != expected.st_uid or row["gid"] != expected.st_gid:
            raise ValueError("checkpoint service identity mismatch")
    if "gs/key" not in {row["path"] for row, _ in entries}:
        raise ValueError("checkpoint has no lease key")
    key.unlink()  # exclusively created, validated placeholder in our fixture
    for row, data in entries:
        _write_new(runtime.root / row["path"], data, mode=row["mode"], uid=row["uid"], gid=row["gid"])
    return {"file_count": len(entries), "bytes": sum(len(data) for _, data in entries),
            "manifest_sha256": hashlib.sha256(regular_bytes(destination / "manifest.json", 65536)[0]).hexdigest()}
