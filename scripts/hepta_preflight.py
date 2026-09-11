#!/usr/bin/env python3
"""Single public HeptaTrader preflight entry point."""
from __future__ import annotations

import fcntl
import os
from pathlib import Path
import stat
import sys

_WRAPPER_NAME = __name__
_WRAPPER_FILE = __file__


def _clear_nonblocking(descriptor: int) -> None:
    nonblocking = getattr(os, "O_NONBLOCK", 0)
    if not nonblocking:
        return
    flags = fcntl.fcntl(descriptor, fcntl.F_GETFL)
    if flags & nonblocking:
        fcntl.fcntl(descriptor, fcntl.F_SETFL, flags & ~nonblocking)


def _read_stable_core() -> tuple[Path, bytes]:
    directory = Path(_WRAPPER_FILE).resolve().parent
    candidates = (
        # Source-tree execution.
        directory / "hepta_preflight_core.py",
        # Canonical installed tree: <prefix>/bin/hepta-preflight and
        # <prefix>/libexec/heptatrader/hepta-preflight-core.py.
        directory.parent
        / "libexec"
        / "heptatrader"
        / "hepta-preflight-core.py",
    )
    failures: list[str] = []
    for candidate in candidates:
        descriptor = -1
        try:
            before_path = candidate.stat(follow_symlinks=False)
            if candidate.is_symlink() or not stat.S_ISREG(before_path.st_mode):
                raise OSError("not a regular non-symlink file")
            descriptor = os.open(
                candidate,
                os.O_RDONLY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_NONBLOCK", 0),
            )
            pinned = os.fstat(descriptor)
            if not stat.S_ISREG(pinned.st_mode) or pinned.st_nlink != 1:
                raise OSError("not a regular non-symlink single-link file")
            _clear_nonblocking(descriptor)
            chunks: list[bytes] = []
            while True:
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    break
                chunks.append(chunk)
            after = os.fstat(descriptor)
            after_path = candidate.stat(follow_symlinks=False)

            def identity(value: os.stat_result) -> tuple[int, ...]:
                return (
                    value.st_dev,
                    value.st_ino,
                    value.st_mode,
                    value.st_nlink,
                    value.st_size,
                    value.st_mtime_ns,
                    value.st_ctime_ns,
                )

            if (
                identity(pinned) != identity(after)
                or identity(pinned) != identity(after_path)
            ):
                raise OSError("identity changed while reading")
            return candidate, b"".join(chunks)
        except OSError as error:
            failures.append(f"{candidate}: {error}")
        finally:
            if descriptor >= 0:
                os.close(descriptor)
    raise RuntimeError("preflight core is unavailable: " + "; ".join(failures))


_CORE_PATH, _CORE_BYTES = _read_stable_core()
_CORE_MODULE_NAME = "_hepta_preflight_core"
_WRAPPER_MODULE = sys.modules[_WRAPPER_NAME]
sys.modules[_CORE_MODULE_NAME] = _WRAPPER_MODULE
globals()["__name__"] = _CORE_MODULE_NAME
globals()["__file__"] = str(_CORE_PATH)
try:
    exec(compile(_CORE_BYTES, str(_CORE_PATH), "exec"), globals(), globals())
finally:
    globals()["__name__"] = _WRAPPER_NAME
    globals()["__file__"] = _WRAPPER_FILE
    sys.modules.pop(_CORE_MODULE_NAME, None)


if _WRAPPER_NAME == "__main__":
    raise SystemExit(main())
