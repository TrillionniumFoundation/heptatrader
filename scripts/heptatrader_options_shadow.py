#!/usr/bin/env python3
"""Bounded native 0DTE observation/replay runner. No strategy or order submission.

Each invocation records a new private capture and replays that same capture.
Systemd may repeat the bounded invocation; it never enables options trading.
"""
import argparse
from datetime import datetime, timezone
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import resource
import stat
import subprocess
import tempfile
import uuid


def private_directory(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    metadata = path.lstat()
    if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.geteuid() or metadata.st_mode & 0o077:
        raise RuntimeError("OPTIONS_SHADOW_STATE_DIRECTORY_UNSAFE")


def atomic_status(path: Path, data: dict) -> None:
    fd, name = tempfile.mkstemp(prefix=".status-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(data, output, indent=2, allow_nan=False)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(name, path)
        dirfd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            os.fsync(dirfd)
        finally:
            os.close(dirfd)
    finally:
        Path(name).unlink(missing_ok=True)


def storage_budget(directory: Path, maximum_bytes: int, minimum_free_bytes: int,
                   reserve_bytes: int = 32 * 1024 * 1024) -> dict:
    if maximum_bytes <= 0 or minimum_free_bytes < 0 or reserve_bytes < 0:
        raise ValueError("OPTIONS_STORAGE_POLICY_INVALID")
    used = 0
    for root, dirs, files in os.walk(directory, followlinks=False):
        for name in dirs + files:
            item = Path(root) / name
            info = item.lstat()
            if stat.S_ISLNK(info.st_mode):
                raise RuntimeError("OPTIONS_STATE_SYMLINK_REJECTED")
            if stat.S_ISREG(info.st_mode):
                used += info.st_size
    filesystem = os.statvfs(directory)
    free = filesystem.f_bavail * filesystem.f_frsize
    reasons = []
    if used + reserve_bytes > maximum_bytes:
        reasons.append("OPTIONS_SHADOW_STATE_BUDGET_EXHAUSTED")
    if free < minimum_free_bytes + reserve_bytes:
        reasons.append("OPTIONS_SHADOW_FILESYSTEM_RESERVE_REQUIRED")
    return {"allowed": not reasons, "state_bytes": used, "available_bytes": free,
            "maximum_bytes": maximum_bytes, "minimum_free_bytes": minimum_free_bytes,
            "attempt_reserve_bytes": reserve_bytes, "reasons": reasons}


def limit_output_files() -> None:
    limits = [16 * 1024 * 1024]
    limits += [x for x in resource.getrlimit(resource.RLIMIT_FSIZE) if x != resource.RLIM_INFINITY]
    limit = min(limits)
    resource.setrlimit(resource.RLIMIT_FSIZE, (limit, limit))


@contextmanager
def single_writer(directory: Path):
    fd = os.open(directory / "observer.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1 or info.st_mode & 0o077:
            raise RuntimeError("OPTIONS_SHADOW_LOCK_UNSAFE")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("OPTIONS_SHADOW_ALREADY_RUNNING") from exc
        yield
    finally:
        os.close(fd)


def observer_result(log: Path, returncode: int) -> dict:
    if returncode not in (0, 2):
        raise RuntimeError("OPTIONS_OBSERVER_FAILED:" + str(returncode))
    complete, starts, accounts, notices = [], [], [], []
    for line in log.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            if line.lstrip().startswith("{"):
                raise RuntimeError("OPTIONS_OBSERVER_INVALID_JSON")
            continue  # SDK diagnostics are not completion evidence.
        if not isinstance(event, dict):
            continue
        kind = event.get("event")
        if kind == "options_readonly_failed":
            raise RuntimeError("OPTIONS_OBSERVER_REPORTED_FAILURE")
        if kind == "options_readonly_complete":
            complete.append(event)
        if kind == "options_readonly_started":
            starts.append(event)
        if kind == "paper_account_check":
            accounts.append(event.get("verified"))
        if kind == "ib_notice" and type(event.get("code")) is int:
            notices.append(event["code"])
    if len(complete) != 1 or len(starts) != 1 or not accounts or any(x is not True for x in accounts):
        raise RuntimeError("OPTIONS_OBSERVER_COMPLETION_UNPROVEN")
    end = complete[0]
    if starts[0].get("order_send_capability") is not False or type(end.get("broker_order_send_count")) is not int or end["broker_order_send_count"] != 0:
        raise RuntimeError("OPTIONS_SHADOW_MUTATION_BOUNDARY_VIOLATION")
    count, ready = end.get("records"), end.get("realtime_ready_records")
    if type(count) is not int or type(ready) is not int or count < 0 or not 0 <= ready <= count or (returncode == 2) != (count == 0):
        raise RuntimeError("OPTIONS_OBSERVER_RECORD_COUNTS_INVALID")
    if end.get("trading_permission") != "UNVERIFIED" or end.get("premium_budget") != "UNSET":
        raise RuntimeError("OPTIONS_OBSERVER_CANNOT_GRANT_TRADING_AUTHORITY")
    return {"records": count, "realtime_ready_records": ready,
            "exchange_date": starts[0].get("exchange_date"),
            "ib_notice_codes": sorted(set(notices))}


def validate_replay(replay: dict, returncode: int, status: dict) -> None:
    if not isinstance(replay, dict) or returncode != 0 or replay.get("replay_pass") is not True or replay.get("broker_authorized") is not False:
        raise RuntimeError("OPTIONS_NATIVE_CAPTURE_REPLAY_FAILED")
    if type(replay.get("records")) is not int or type(replay.get("market_data_ready_records")) is not int or replay["records"] != status["records"] or replay["market_data_ready_records"] != status["realtime_ready_records"]:
        raise RuntimeError("OPTIONS_CAPTURE_REPLAY_COUNT_MISMATCH")


def snowball_source_status() -> dict:
    # No Snowball quote endpoint/schema or credential was found in the deployed
    # project. Never invent a transport, use an App cookie, reinterpret an IB
    # subscription error as Snowball's status, or fall back silently to IB data.
    return {
        "mode": "SHADOW_READ_ONLY",
        "state": "BLOCKED_SNOWBALL_OFFICIAL_FEED_UNCONFIGURED",
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "market_data_source": "SNOWBALL",
        "ib_role": "CONTRACT_ACCOUNT_EXECUTION_VERIFICATION",
        "ib_market_data_required": False,
        "snowball_transport_connected": False,
        "snowball_wire_protocol_verified": False,
        "snowball_api_entitlement_verified": False,
        "reasons": ["SNOWBALL_OFFICIAL_QUOTE_TRANSPORT_NOT_IMPLEMENTED",
                    "SNOWBALL_API_ENTITLEMENT_NOT_VERIFIED",
                    "SNOWBALL_XSP_SPXW_AND_UNDERLYING_COVERAGE_NOT_VERIFIED"],
        "records": 0,
        "realtime_ready_records": 0,
        "stage1_proven": False,
        "broker_order_submission_enabled": False,
        "options_paper_roundtrip_proven": False,
        "trading_permission": "UNVERIFIED",
        "premium_budget": "UNSET",
    }


def run(args, binary: Path) -> int:
    now = datetime.now(timezone.utc)
    run = args.state_dir / (now.strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8])
    private_directory(run)
    os.umask(0o077)
    status = {
        "mode": "SHADOW_READ_ONLY",
        "market_data_source": "IB_DIAGNOSTIC",
        "acceptance_target": "SNOWBALL",
        "stage1_proven": False,
        "started_at_utc": now.isoformat(),
        "observer": str(binary),
        "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        "capture": str(run / "observations.h0d1"),
        "broker_order_submission_enabled": False,
        "options_paper_roundtrip_proven": False,
        "trading_permission": "UNVERIFIED",
        "premium_budget": "UNSET",
        "state": "COLLECTING",
    }
    atomic_status(args.state_dir / "status.json", status)
    args_list = [str(binary), "--account", args.account, "--capture", status["capture"],
                 "--duration-sec", str(args.duration_sec), "--client-id", str(args.client_id)]
    try:
        with (run / "observer.jsonl").open("x", encoding="utf-8") as log:
            process = subprocess.run(args_list, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                     check=False, timeout=args.duration_sec + 150)
        status["observer_exit"] = process.returncode
        status.update(observer_result(run / "observer.jsonl", process.returncode))
        if status.get("records", 0) > 0:
            replay = subprocess.run([str(binary), "--replay", status["capture"]],
                                    stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                    timeout=60, check=False)
            (run / "replay.json").write_text(replay.stdout, encoding="utf-8")
            status["replay_exit"] = replay.returncode
            status["replay"] = json.loads(replay.stdout)
            validate_replay(status["replay"], replay.returncode, status)
            status["state"] = "OBSERVED_NO_TRADING_AUTHORITY"
        else:
            status["state"] = "NO_QUALIFIED_CANDIDATE"
        status["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
        atomic_status(args.state_dir / "status.json", status)
        atomic_status(run / "result.json", status)
        print(json.dumps(status, allow_nan=False), flush=True)
        return 0
    except (OSError, RuntimeError, subprocess.SubprocessError, ValueError) as exc:
        status["state"] = "ERROR"
        status["error"] = str(exc)
        status["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
        atomic_status(args.state_dir / "status.json", status)
        atomic_status(run / "result.json", status)
        print(json.dumps(status, allow_nan=False), flush=True)
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--observer", type=Path, required=True)
    parser.add_argument("--account", required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--duration-sec", type=int, default=300)
    parser.add_argument("--client-id", type=int, default=8662)
    parser.add_argument("--market-data-source", choices=("SNOWBALL", "IB_DIAGNOSTIC"), default="SNOWBALL")
    parser.add_argument("--max-state-mib", type=int, default=2048)
    parser.add_argument("--min-free-mib", type=int, default=4096)
    args = parser.parse_args()
    if os.geteuid() == 0 or not re.fullmatch(r"DU[A-Z0-9]+", args.account):
        parser.error("Requires an unprivileged user and an explicit PAPER account")
    if not args.observer.is_absolute() or not args.state_dir.is_absolute():
        parser.error("Absolute observer and state paths are required")
    if not 15 <= args.duration_sec <= 900 or not 8000 <= args.client_id <= 9999:
        parser.error("Duration/client-id outside read-only limits")
    private_directory(args.state_dir)
    try:
        with single_writer(args.state_dir):
            if args.market_data_source == "SNOWBALL":
                blocked = snowball_source_status()
                atomic_status(args.state_dir / "status.json", blocked)
                print(json.dumps(blocked), flush=True)
                return 78  # configuration blocker; systemd must NOT retry IB.

            # Validate the observer only for the source that can actually run
            # it.  A missing Snowball transport is a deterministic configuration
            # blocker and must be reported as such even when the supplied
            # diagnostic path lives on a noexec filesystem.
            binary = args.observer.resolve(strict=True)
            st = binary.stat()
            if (not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid() or
                    st.st_mode & 0o022 or not os.access(binary, os.X_OK)):
                parser.error("Observer binary ownership/mode is unsafe")

            if args.max_state_mib < 64 or args.min_free_mib < 64:
                raise RuntimeError("OPTIONS_STORAGE_POLICY_TOO_SMALL")
            budget = storage_budget(args.state_dir, args.max_state_mib * 1024 * 1024,
                                    args.min_free_mib * 1024 * 1024)
            if not budget["allowed"]:
                blocked = {"mode": "SHADOW_READ_ONLY", "state": "STORAGE_BUDGET_BLOCKED",
                           "checked_at_utc": datetime.now(timezone.utc).isoformat(),
                           "storage_budget": budget, "broker_order_submission_enabled": False,
                           "options_paper_roundtrip_proven": False}
                atomic_status(args.state_dir / "status.json", blocked)
                print(json.dumps(blocked), flush=True)
                return 2
            limit_output_files()
            def interrupted(signum, frame):
                raise RuntimeError("OPTIONS_SHADOW_INTERRUPTED:" + str(signum))
            signal.signal(signal.SIGTERM, interrupted)
            signal.signal(signal.SIGINT, interrupted)
            return run(args, binary)
    except (OSError, RuntimeError) as exc:
        # A second writer must never overwrite the active owner's status.
        print(json.dumps({"state": "START_REJECTED", "error": str(exc),
                          "broker_order_submission_enabled": False}), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
