#!/usr/bin/env python3
"""Public HeptaTrader preflight entry point with strict archive namespace admission."""
from __future__ import annotations

import os
from pathlib import Path
import stat
import sys

_WRAPPER_NAME = __name__
_WRAPPER_FILE = __file__


def _read_stable_core() -> tuple[Path, bytes]:
    directory = Path(_WRAPPER_FILE).resolve().parent
    candidates = (
        directory / "hepta_preflight_core.py",
        directory / "hepta-preflight-core.py",
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
                | getattr(os, "O_NOFOLLOW", 0),
            )
            pinned = os.fstat(descriptor)
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

_ORIGINAL_CHECK_MANIFEST_SHAPE = _check_manifest_shape


def _validate_complete_archive_namespace(paths: list[str]) -> None:
    """Reject any file path that is an ancestor of another archive file."""
    namespace = set(GENERATED_ARCHIVE_PATHS)
    namespace.update(paths)
    for candidate in sorted(namespace):
        parts = candidate.split("/")
        for depth in range(1, len(parts)):
            ancestor = "/".join(parts[:depth])
            if ancestor in namespace:
                raise PreflightError(
                    "manifest archive namespace contains a file/directory "
                    "prefix collision: "
                    f"{ancestor!r} versus {candidate!r}"
                )


def _check_manifest_shape(manifest: Any, profile: str) -> list[dict[str, Any]]:
    files = _ORIGINAL_CHECK_MANIFEST_SHAPE(manifest, profile)
    _validate_complete_archive_namespace([item["path"] for item in files])
    return files


if _WRAPPER_NAME == "__main__":
    raise SystemExit(main())
