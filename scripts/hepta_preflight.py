#!/usr/bin/env python3
"""Public HeptaTrader preflight entry point with strict archive namespace admission."""
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

_ORIGINAL_CHECK_MANIFEST_SHAPE = _check_manifest_shape
_ORIGINAL_INSPECT_ARCHIVE = inspect_archive
_RELEASE_LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


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
    version = manifest.get("version")
    if not isinstance(version, str) or _RELEASE_LABEL_RE.fullmatch(version) is None:
        raise PreflightError("release version must be a bounded canonical label")
    _validate_complete_archive_namespace([item["path"] for item in files])
    return files


def _read_admitted_archive_root(
    artifact: Path,
    admitted_sha256: str,
    policy: LoadedPolicy,
) -> str:
    """Re-open the admitted bytes and return the first member's root component."""
    maximum_members = policy["maximum_archive_members"]
    maximum_member = policy["maximum_member_bytes"]
    maximum_total = policy["maximum_total_unpacked_bytes"]
    maximum_stream = _tar_stream_limit(maximum_members, maximum_total)
    maximum_compressed = _compressed_archive_limit(maximum_members, maximum_total)

    with _open_pinned_regular(artifact, "artifact root identity") as (
        stream,
        pinned,
        directory,
        name,
    ):
        if pinned.st_size <= 0 or pinned.st_size > maximum_compressed:
            raise PreflightError(
                "artifact root identity compressed size is outside compiled bound"
            )
        observed_sha256 = _hash_stream(stream)
        if observed_sha256 != admitted_sha256:
            raise PreflightError("artifact identity changed before root validation")
        stream.seek(0)
        try:
            with _open_gzip_stream(stream) as gzip_stream:
                reader = _BoundedTarReader(
                    gzip_stream,
                    maximum_members=maximum_members,
                    maximum_member_bytes=maximum_member,
                    maximum_total_bytes=maximum_total,
                    maximum_stream_bytes=maximum_stream,
                )
                member = reader.next_member()
                if member is None:
                    raise PreflightError("release archive is empty")
                parts = _canonical_member_name(member.name)
                if len(parts) != 2 or parts[1] != "manifest.json":
                    raise PreflightError(
                        "release manifest must be the first archive member"
                    )
                root_name = parts[0]
        except PreflightError:
            raise
        except (OSError, EOFError, gzip.BadGzipFile) as error:
            raise PreflightError(
                "artifact root identity is not a valid bounded gzip/USTAR archive: "
                f"{error}"
            ) from error
        _assert_stable_file(
            stream,
            pinned,
            directory,
            name,
            "artifact root identity",
        )
    return root_name


def inspect_archive(
    artifact: Path,
    expected_sha256: str,
    policy: LoadedPolicy,
    profile: str,
) -> tuple[dict[str, Any], str, str]:
    manifest, actual_sha256, manifest_sha256 = _ORIGINAL_INSPECT_ARCHIVE(
        artifact,
        expected_sha256,
        policy,
        profile,
    )
    root_name = _read_admitted_archive_root(artifact, actual_sha256, policy)
    expected_root = f"heptatrader-{manifest['version']}-{profile}"
    if root_name != expected_root:
        raise PreflightError(
            "archive root identity does not match manifest version/profile: "
            f"expected={expected_root!r}, observed={root_name!r}"
        )
    return manifest, actual_sha256, manifest_sha256


if _WRAPPER_NAME == "__main__":
    raise SystemExit(main())
