#!/usr/bin/env python3
"""User-scoped PAPER fail-closed marker rearm before native Execution startup.

No broker or trading imports, API calls, network, service control, or disarm.
The production CLI accepts no path overrides. A failure aborts ExecStartPre.
"""
from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

MARKER = "kill-switch"
NOFOLLOW_DIR = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW


class HoldRefused(ValueError):
    pass


def require(ok: bool, code: str) -> None:
    if not ok:
        raise HoldRefused(code)


def checked_dir(fd: int, uid: int, gid: int, mode: int) -> os.stat_result:
    s = os.fstat(fd)
    require(stat.S_ISDIR(s.st_mode) and s.st_uid == uid and s.st_gid == gid and
            stat.S_IMODE(s.st_mode) == mode and s.st_nlink >= 2,
            "PAPER_HOLD_DIRECTORY_UNSAFE")
    return s


def rearm_from_runtime_root(root: str, uid: int, gid: int) -> dict:
    """Testable narrow helper; production main pins root to /run/user/<euid>."""
    parent = os.open(root, NOFOLLOW_DIR)
    try:
        checked_dir(parent, uid, gid, 0o700)
        hepta = os.open("heptatrader", NOFOLLOW_DIR, dir_fd=parent)
        try:
            checked_dir(hepta, uid, gid, 0o700)
            created_control = False
            try:
                control = os.open("control", NOFOLLOW_DIR, dir_fd=hepta)
            except FileNotFoundError:
                # Refuse symlinks and untrusted existing trees; only create the
                # exact missing child inside the owner-verified pinned parent.
                os.mkdir("control", 0o700, dir_fd=hepta)
                created_control = True
                control = os.open("control", NOFOLLOW_DIR, dir_fd=hepta)
                os.fchmod(control, 0o750)
                os.fsync(control)
                os.fsync(hepta)
            try:
                cs = checked_dir(control, uid, gid, 0o750)
                require(cs.st_nlink == 2, "PAPER_HOLD_DIRECTORY_UNSAFE")
                # Exact kernel object identity: no directory replacement.
                now = os.stat("control", dir_fd=hepta, follow_symlinks=False)
                require((cs.st_dev, cs.st_ino) == (now.st_dev, now.st_ino),
                        "PAPER_HOLD_CONTROL_CHANGED")
                created_marker = False
                try:
                    marker = os.open(MARKER, os.O_RDONLY | os.O_NONBLOCK |
                                     os.O_CLOEXEC | os.O_NOFOLLOW,
                                     dir_fd=control)
                except FileNotFoundError:
                    marker = os.open(MARKER, os.O_CREAT | os.O_EXCL |
                                     os.O_WRONLY | os.O_CLOEXEC |
                                     os.O_NOFOLLOW, 0o400, dir_fd=control)
                    created_marker = True
                    os.fchmod(marker, 0o440)
                    os.fsync(marker)
                    os.fsync(control)
                try:
                    ms = os.fstat(marker)
                    require(stat.S_ISREG(ms.st_mode) and ms.st_uid == uid and
                            ms.st_gid == gid and stat.S_IMODE(ms.st_mode) == 0o440 and
                            ms.st_nlink == 1 and ms.st_dev == cs.st_dev,
                            "PAPER_HOLD_MARKER_UNSAFE")
                    again = os.stat(MARKER, dir_fd=control, follow_symlinks=False)
                    require((ms.st_dev, ms.st_ino) == (again.st_dev, again.st_ino),
                            "PAPER_HOLD_MARKER_CHANGED")
                finally:
                    os.close(marker)
                return {"state": "ENGAGED", "created_marker": created_marker,
                        "created_control": created_control,
                        "mode": "0440", "marker_name": MARKER,
                        "authorization_effect": "BLOCK_RISK_INCREASE_ONLY"}
            finally:
                os.close(control)
        finally:
            os.close(hepta)
    finally:
        os.close(parent)


def main(argv: list[str]) -> int:
    require(not argv, "PAPER_HOLD_NO_ARGUMENTS_ALLOWED")
    uid, gid = os.geteuid(), os.getegid()
    require(uid >= 1000 and gid != 0, "PAPER_HOLD_NONROOT_REQUIRED")
    root = f"/run/user/{uid}"
    result = rearm_from_runtime_root(root, uid, gid)
    print("PAPER_HOLD_ENGAGED_BY_EXACT_NATIVE_MARKER "
          f"marker={result['marker_name']} mode={result['mode']}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except (OSError, HoldRefused) as exc:
        print(f"PAPER_HOLD_REARM_REFUSED: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(2)
