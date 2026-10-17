#!/usr/bin/env python3
"""Owner-local PAPER maintenance latch; no service-stop or Broker authority.

All callers on Pocket4 share one flock before inspecting/changing the
transient native kill-switch. A durable operator hold makes resident
`paper-on` fail without deleting native protection. This is a strategy-side
admission fence, NOT a native owner fence or maintenance-stop permit.
"""
from __future__ import annotations
import fcntl
import json
import os
import pwd
from pathlib import Path
import stat
import subprocess
import sys
import time

SCHEMA = "heptatrader.operator-maintenance-hold.v1"
SWITCH = "kill-switch"
HOLD = "maintenance-hold.json"
LOCK = "operator-maintenance.lock"
ACTIONS = {"pause", "paper-on", "maintenance-hold", "maintenance-release",
           "maintenance-status"}


class HoldError(Exception):
    pass


def _directory(path: Path, mode: int, *, make=False, exact_links=None) -> int:
    if make:
        try:
            path.mkdir(mode=mode)
        except FileExistsError:
            pass
    before = path.lstat()
    if (not stat.S_ISDIR(before.st_mode) or before.st_uid != os.geteuid()
            or stat.S_IMODE(before.st_mode) != mode or before.st_nlink < 2
            or (exact_links is not None and before.st_nlink != exact_links)):
        raise HoldError("UNSAFE_DIRECTORY:" + path.name)
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    after = os.fstat(fd)
    if ((before.st_dev, before.st_ino) != (after.st_dev, after.st_ino)
            or after.st_uid != os.geteuid() or stat.S_IMODE(after.st_mode) != mode):
        os.close(fd)
        raise HoldError("DIRECTORY_CHANGED:" + path.name)
    return fd


def _file(fd: int, name: str, mode: int) -> bytes | None:
    try:
        st = os.stat(name, dir_fd=fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    if (not stat.S_ISREG(st.st_mode) or st.st_nlink != 1
            or st.st_uid != os.geteuid() or st.st_gid != os.getegid()
            or stat.S_IMODE(st.st_mode) != mode or st.st_size > 4096):
        raise HoldError("UNSAFE_MARKER:" + name)
    item = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
    try:
        actual = os.fstat(item)
        if (st.st_dev, st.st_ino) != (actual.st_dev, actual.st_ino):
            raise HoldError("MARKER_CHANGED:" + name)
        data = os.read(item, actual.st_size + 1)
        if len(data) != actual.st_size:
            raise HoldError("MARKER_SHORT_READ:" + name)
        return data
    finally:
        os.close(item)


def _write_once(fd: int, name: str, data: bytes, mode: int) -> bool:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
    try:
        item = os.open(name, flags, mode, dir_fd=fd)
    except FileExistsError:
        return False
    try:
        os.fchmod(item, mode)
        os.write(item, data)
        os.fsync(item)
    finally:
        os.close(item)
    os.fsync(fd)
    return True


def _unlink(fd: int, name: str, mode: int) -> None:
    if _file(fd, name, mode) is None:
        raise HoldError("MARKER_MISSING:" + name)
    os.unlink(name, dir_fd=fd)
    os.fsync(fd)


def _hold_record(data: bytes | None) -> dict | None:
    if data is None:
        return None
    def pairs(items):
        obj = {}
        for key, value in items:
            if key in obj:
                raise HoldError("DUPLICATE_HOLD_FIELD")
            obj[key] = value
        return obj
    try:
        record = json.loads(data, object_pairs_hook=pairs)
    except (ValueError, UnicodeDecodeError) as exc:
        raise HoldError("BAD_HOLD_JSON") from exc
    if (not isinstance(record, dict)
            or record.get("schema") != SCHEMA
            or record.get("effect") != "NEW_PAPER_RISK_HOLD_ONLY"
            or record.get("uid") != os.geteuid()
            or type(record.get("created_at_ms")) is not int
            or record["created_at_ms"] <= 0):
        raise HoldError("BAD_HOLD_SCOPE")
    return record


def perform(action: str, control: Path, state_root: Path, *,
            reason: str | None = None, task: str | None = None,
            resume_when: str | None = None) -> dict:
    """Testable policy core. CLI main separately pins the real runtime paths."""
    if action not in ACTIONS:
        raise HoldError("UNKNOWN_ACTION")
    metadata = {"reason": reason, "task": task, "resume_when": resume_when}
    if any(value is not None and (not isinstance(value, str) or len(value) > 512
           or any(ord(char) < 32 for char in value)) for value in metadata.values()):
        raise HoldError("BAD_HOLD_DESCRIPTION")
    # An unprivileged owner alone is allowed; never silently act as root.
    if os.geteuid() == 0:
        raise HoldError("ROOT_NOT_ALLOWED")
    # The existing PAPER runtime parent uses 0700, native control uses 0750.
    parent_fd = _directory(control.parent, 0o700)
    root_fd = _directory(state_root, 0o700)
    try:
        ctrl_fd = _directory(control, 0o750, exact_links=2)
        maint = state_root / "operator-maintenance"
        maint_fd = _directory(maint, 0o700, make=True)
        try:
            # Reuse the existing operator maintenance lock across CLI and rollout.
            lock = os.open(LOCK, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC,
                           0o600, dir_fd=root_fd)
            try:
                meta = os.fstat(lock)
                if (not stat.S_ISREG(meta.st_mode) or meta.st_nlink != 1
                        or meta.st_uid != os.geteuid()
                        or stat.S_IMODE(meta.st_mode) != 0o600):
                    raise HoldError("UNSAFE_OPERATOR_LOCK")
                fcntl.flock(lock, fcntl.LOCK_EX)
                hold_raw = _file(maint_fd, HOLD, 0o600)
                record = _hold_record(hold_raw)
                hold = record is not None
                marker = _file(ctrl_fd, SWITCH, 0o440)
                # Native Execution accepts a 0440 single-link marker even
                # when the payload is empty. It creates empty markers on
                # startup/recovery; that is an INDEPENDENT safety hold, not
                # the ordinary CLI's exactly tagged pause.
                independent_empty = marker == b""
                other_native = marker is not None and marker not in (b"engaged\n", b"")
                if other_native and action != "maintenance-status":
                    raise HoldError("UNKNOWN_EXISTING_NATIVE_KILLSWITCH")
                if action == "paper-on":
                    if hold:
                        raise HoldError("BLOCKED_BY_PERSISTENT_MAINTENANCE_HOLD")
                    if marker is not None:
                        if independent_empty:
                            raise HoldError("INDEPENDENT_NATIVE_HOLD_NOT_REARMABLE")
                        _unlink(ctrl_fd, SWITCH, 0o440)
                elif action in ("pause", "maintenance-hold"):
                    if action == "maintenance-hold" and not hold:
                        payload = {"schema": SCHEMA, "uid": os.geteuid(),
                                   "effect": "NEW_PAPER_RISK_HOLD_ONLY",
                                   "created_at_ms": int(time.time() * 1000),
                                   "created_by": "heptatrader-cli", "created_by_pid": os.getpid(),
                                   **{key: value for key, value in metadata.items() if value is not None}}
                        raw = (json.dumps(payload, sort_keys=True, separators=(",", ":"))
                               + "\n").encode()
                        if len(raw) > 4096:
                            raise HoldError("HOLD_DESCRIPTION_TOO_LARGE")
                        if not _write_once(maint_fd, HOLD, raw, 0o600):
                            raise HoldError("RACING_HOLD_CREATION")
                        hold = True
                    if marker is None:
                        if not _write_once(ctrl_fd, SWITCH, b"engaged\n", 0o440):
                            raise HoldError("RACING_KILLSWITCH_CREATION")
                elif action == "maintenance-release":
                    if not hold or marker is None:
                        raise HoldError("RELEASE_REQUIRES_BOTH_VALID_MARKERS")
                    # Under the same exclusive lock. Keep native entry block
                    # installed until the explicit persistent latch is retired.
                    _unlink(maint_fd, HOLD, 0o600)
                    hold = False
                    try:
                        # Release only the operator's own tagged CLI marker.
                        # Execution's zero-byte recovery marker is a separate
                        # owner and must survive maintenance-release intact.
                        if not independent_empty:
                            _unlink(ctrl_fd, SWITCH, 0o440)
                    except Exception:
                        # Restore the original owner's reason and creation time.
                        _write_once(maint_fd, HOLD, hold_raw, 0o600)
                        raise
                # Report the post-action state, without granting service-stop authority.
                marker = _file(ctrl_fd, SWITCH, 0o440)
                record = _hold_record(_file(maint_fd, HOLD, 0o600))
                origin = ("ABSENT" if marker is None else
                          "INDEPENDENT_EMPTY_NATIVE" if marker == b"" else
                          "CLI_TAGGED_PAUSE" if marker == b"engaged\n" else
                          "UNRECOGNIZED_INDEPENDENT_NATIVE")
                blockers = (["PERSISTENT_OPERATOR_HOLD"] if record is not None else [])
                if marker is not None:
                    blockers.append(origin)
                details = None if record is None else {
                    "uid": record["uid"], "created_at_ms": record["created_at_ms"],
                    "age_seconds": max(0, (int(time.time() * 1000) - record["created_at_ms"]) // 1000),
                    "created_by": record.get("created_by"),
                    "reason": record.get("reason"), "task": record.get("task"),
                    "resume_when": record.get("resume_when")}
                return {"schema": "heptatrader.maintenance-latch-status.v1",
                        "action": action, "persistent_hold": record is not None,
                        "native_kill_switch_engaged": marker is not None,
                        "native_marker_origin": origin, "hold_details": details,
                        "new_paper_risk_blocked": bool(blockers), "blockers": blockers,
                        "may_stop_driver": False, "broker_orders_sent": 0}
            finally:
                os.close(lock)
        finally:
            os.close(maint_fd)
            os.close(ctrl_fd)
    finally:
        os.close(root_fd)
        os.close(parent_fd)


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=sorted(ACTIONS))
    ap.add_argument("--reason", help="optional reason for a newly created maintenance hold")
    ap.add_argument("--task", help="optional task associated with a new hold")
    ap.add_argument("--resume-when", help="optional resume condition; does not auto-release a hold")
    args = ap.parse_args(argv)
    uid = os.geteuid()
    expected_control = Path(f"/run/user/{uid}/heptatrader/control")
    expected_state = Path(pwd.getpwuid(uid).pw_dir) / ".local/state/heptatrader"
    try:
        status = perform(args.action, expected_control, expected_state,
                         reason=args.reason, task=args.task, resume_when=args.resume_when)
    except (HoldError, OSError, ValueError) as exc:
        print(json.dumps({"status": "HOLD_BLOCKED", "reason": str(exc)[:200],
                          "may_stop_driver": False, "broker_orders_sent": 0}))
        return 75
    print(json.dumps(status, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
