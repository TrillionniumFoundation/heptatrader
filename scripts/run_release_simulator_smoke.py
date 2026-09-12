#!/usr/bin/env python3
"""Install, execute, roll back, and re-promote a verified simulator release."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
from typing import Any, BinaryIO

SCHEMA = "heptatrader.release-simulator-smoke.v1"
DEFAULT_SMOKE_PATH = Path(
    "libexec/heptatrader/hepta_agent_simulator_e2e_tests"
)


class SmokeError(RuntimeError):
    pass


def _canonical_json(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode("utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()



def _file_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_nlink,
        metadata.st_uid,
        metadata.st_gid,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _clear_nonblocking(descriptor: int) -> None:
    import fcntl

    flag = getattr(os, "O_NONBLOCK", 0)
    if not flag:
        return
    current = fcntl.fcntl(descriptor, fcntl.F_GETFL)
    if current & flag:
        fcntl.fcntl(descriptor, fcntl.F_SETFL, current & ~flag)


def _prepare_work_root(path: Path) -> None:
    if path.exists() or path.is_symlink():
        if path.is_symlink() or not path.is_dir() or any(path.iterdir()):
            raise SmokeError(
                "work root must be absent or an empty non-symlink directory"
            )
    else:
        path.mkdir(parents=True, mode=0o700)
    metadata = path.stat(follow_symlinks=False)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise SmokeError(
            "work root must be owned by the current user and not group/world writable"
        )


def _snapshot_artifact(
    source: Path, destination: Path, expected_sha256: str
) -> Path:
    destination.parent.mkdir(mode=0o700)
    source_descriptor = -1
    destination_descriptor = -1
    try:
        source_descriptor = os.open(
            source,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0),
        )
        pinned = os.fstat(source_descriptor)
        if not stat.S_ISREG(pinned.st_mode) or pinned.st_nlink != 1:
            raise SmokeError(
                "release artifact must be a regular non-symlink single-link file"
            )
        _clear_nonblocking(source_descriptor)
        current = source.stat(follow_symlinks=False)
        if _file_identity(pinned) != _file_identity(current):
            raise SmokeError("release artifact identity changed before snapshot")
        destination_descriptor = os.open(
            destination,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0),
            0o600,
        )
        digest = hashlib.sha256()
        while True:
            chunk = os.read(source_descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            remaining = memoryview(chunk)
            while remaining:
                count = os.write(destination_descriptor, remaining)
                if count <= 0:
                    raise SmokeError("artifact snapshot write made no progress")
                remaining = remaining[count:]
        os.fsync(destination_descriptor)
        after = os.fstat(source_descriptor)
        current = source.stat(follow_symlinks=False)
        observed = digest.hexdigest()
        if (
            _file_identity(pinned) != _file_identity(after)
            or _file_identity(pinned) != _file_identity(current)
        ):
            raise SmokeError("release artifact identity changed during snapshot")
        if observed != expected_sha256:
            raise SmokeError("artifact digest differs during snapshot")
        snapshot = os.fstat(destination_descriptor)
        if (
            not stat.S_ISREG(snapshot.st_mode)
            or snapshot.st_nlink != 1
            or stat.S_IMODE(snapshot.st_mode) != 0o600
            or snapshot.st_size != pinned.st_size
        ):
            raise SmokeError("artifact snapshot identity is invalid")
        return destination
    except Exception:
        try:
            destination.unlink()
        except FileNotFoundError:
            pass
        raise
    finally:
        if destination_descriptor >= 0:
            os.close(destination_descriptor)
        if source_descriptor >= 0:
            os.close(source_descriptor)

def _canonical_relative(value: str, label: str) -> Path:
    if not value or "\\" in value or "\x00" in value:
        raise SmokeError(f"{label}: non-canonical path")
    pure = PurePosixPath(value)
    if (
        pure.is_absolute()
        or pure.as_posix() != value
        or any(part in {"", ".", ".."} for part in pure.parts)
    ):
        raise SmokeError(f"{label}: path escapes release slot")
    return Path(*pure.parts)


def _command(path: Path) -> list[str]:
    return [sys.executable, str(path)] if path.suffix == ".py" else [str(path)]


def _run_preflight(
    preflight: Path,
    artifact: Path,
    expected_sha256: str,
    policy: Path,
    profile: str,
) -> dict[str, Any]:
    result = subprocess.run(
        _command(preflight)
        + [
            "--artifact",
            str(artifact),
            "--expected-sha256",
            expected_sha256,
            "--profile",
            profile,
            "--policy",
            str(policy),
            "--artifact-only",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=180,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise SmokeError(f"artifact preflight failed: {detail}")
    try:
        receipt = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise SmokeError(f"artifact preflight emitted invalid JSON: {error}") from error
    if (
        not isinstance(receipt, dict)
        or receipt.get("result") != "PASS"
        or receipt.get("paper_authorized") is not False
        or receipt.get("live_authorized") is not False
        or receipt.get("authorization_effect") != "NONE"
    ):
        raise SmokeError("artifact preflight receipt is not a non-authorizing PASS")
    artifact_record = receipt.get("artifact")
    if (
        not isinstance(artifact_record, dict)
        or artifact_record.get("sha256") != expected_sha256
    ):
        raise SmokeError("artifact preflight receipt does not bind the expected digest")
    return receipt


def _copy_member(stream: BinaryIO, target: Path, mode: int) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(
        target,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
        mode,
    )
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as output:
            shutil.copyfileobj(stream, output, length=1024 * 1024)
            output.flush()
            os.fsync(output.fileno())
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != mode
        ):
            raise SmokeError(f"extracted file has invalid identity: {target}")
    finally:
        os.close(descriptor)


def _extract_release(artifact: Path, destination: Path) -> str:
    if destination.exists() or destination.is_symlink():
        raise SmokeError(f"release slot already exists: {destination}")
    destination.mkdir(parents=True, mode=0o700)
    root_name: str | None = None
    try:
        with tarfile.open(artifact, mode="r:gz") as archive:
            members = archive.getmembers()
            if not members:
                raise SmokeError("release archive is empty")
            for index, member in enumerate(members):
                if not member.isreg():
                    raise SmokeError(
                        f"release archive contains a non-regular member: {member.name}"
                    )
                parts = PurePosixPath(member.name).parts
                if len(parts) < 2:
                    raise SmokeError(f"release member lacks package root: {member.name}")
                if root_name is None:
                    root_name = parts[0]
                    if index != 0 or parts[1:] != ("manifest.json",):
                        raise SmokeError(
                            "release manifest must be the first archive member"
                        )
                if parts[0] != root_name:
                    raise SmokeError("release archive contains multiple package roots")
                relative_text = PurePosixPath(*parts[1:]).as_posix()
                relative = _canonical_relative(relative_text, member.name)
                mode = stat.S_IMODE(member.mode)
                if mode not in {0o644, 0o755}:
                    raise SmokeError(
                        f"release member has unsupported mode {oct(mode)}: {member.name}"
                    )
                stream = archive.extractfile(member)
                if stream is None:
                    raise SmokeError(f"release member body is unavailable: {member.name}")
                with stream:
                    _copy_member(stream, destination / relative, mode)
        if root_name is None:
            raise SmokeError("release archive is empty")
        directory = os.open(
            destination,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        return root_name
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise


def _atomic_switch(root: Path, target: Path) -> None:
    try:
        relative = target.relative_to(root)
    except ValueError as error:
        raise SmokeError("release slot is outside the work root") from error
    if not target.is_dir() or target.is_symlink():
        raise SmokeError(f"release slot is not a stable directory: {target}")
    temporary = root / f".current-{os.getpid()}-{os.urandom(6).hex()}"
    current = root / "current"
    os.symlink(relative.as_posix(), temporary)
    try:
        os.replace(temporary, current)
        directory = os.open(
            root,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _run_installed_smoke(current: Path, relative: Path) -> dict[str, Any]:
    relative = _canonical_relative(relative.as_posix(), "smoke-relative-path")
    # Open every path component without following attacker-controlled links and
    # execute the pinned inode through /proc/self/fd.  This keeps an atomic
    # current-slot switch (or a concurrent rename) from changing what runs
    # between validation and exec.
    parts = relative.parts
    if not parts:
        raise SmokeError("installed simulator smoke path is empty")
    directory_fd = -1
    executable_fd = -1
    try:
        # Resolve the current pointer textually, then walk the release slot
        # from its parent directory without following any component links.
        # This avoids validating one slot and executing another if current is
        # atomically replaced while the smoke process is starting.
        root_fd = os.open(
            current.parent,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        directory_fd = root_fd
        pointer = os.readlink(current.name, dir_fd=root_fd)
        try:
            pointer_relative = _canonical_relative(pointer, "current pointer")
        except Exception:
            os.close(directory_fd)
            directory_fd = -1
            raise
        for component in pointer_relative.parts:
            next_fd = os.open(
                component,
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=directory_fd,
            )
            os.close(directory_fd)
            directory_fd = next_fd
        for component in parts[:-1]:
            next_fd = os.open(
                component,
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=directory_fd,
            )
            os.close(directory_fd)
            directory_fd = next_fd
        executable_fd = os.open(
            parts[-1],
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=directory_fd,
        )
        metadata = os.fstat(executable_fd)
    except OSError as error:
        if executable_fd >= 0:
            os.close(executable_fd)
        if directory_fd >= 0:
            os.close(directory_fd)
        raise SmokeError(f"installed simulator smoke is missing: {error}") from error
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or not (metadata.st_mode & 0o111)
    ):
        os.close(executable_fd)
        os.close(directory_fd)
        raise SmokeError("installed simulator smoke is not an executable regular file")
    try:
        result = subprocess.run(
            [f"/proc/self/fd/{executable_fd}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=180,
            check=False,
            pass_fds=(executable_fd,),
            env={
                "PATH": "/usr/bin:/bin",
                "LC_ALL": "C",
                "HEPTA_RELEASE_SMOKE": "1",
            },
        )
        if result.returncode != 0:
            raise SmokeError(
                "installed simulator smoke failed "
                f"(exit={result.returncode}): {result.stdout[-4096:]}"
            )
        return {
            "exit_code": result.returncode,
            "output_sha256": hashlib.sha256(
                result.stdout.encode("utf-8", "replace")
            ).hexdigest(),
        }
    finally:
        os.close(executable_fd)
        os.close(directory_fd)


def _write_record(path: Path, value: dict[str, Any]) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    metadata = path.parent.stat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise SmokeError(
            "deployment-record parent must be owned by the current user "
            "and not group/world writable"
        )
    content = _canonical_json(value)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary = Path(temporary_name)
    published = False
    try:
        os.fchmod(descriptor, 0o600)
        remaining = memoryview(content)
        while remaining:
            count = os.write(descriptor, remaining)
            if count <= 0:
                raise SmokeError("deployment-record write made no progress")
            remaining = remaining[count:]
        os.fsync(descriptor)
        os.link(temporary, path, follow_symlinks=False)
        published = True
        observed = path.stat(follow_symlinks=False)
        if (
            not stat.S_ISREG(observed.st_mode)
            or observed.st_nlink != 2
            or stat.S_IMODE(observed.st_mode) != 0o600
            or _sha256(path) != hashlib.sha256(content).hexdigest()
        ):
            raise SmokeError("published deployment record identity differs from staging")
        temporary.unlink()
        observed = path.stat(follow_symlinks=False)
        if observed.st_nlink != 1:
            raise SmokeError("published deployment record has unexpected link count")
        directory = os.open(
            path.parent,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except FileExistsError as error:
        raise SmokeError(f"refusing to replace deployment record: {path}") from error
    except Exception:
        if published:
            try:
                path.unlink()
            except OSError:
                pass
        raise
    finally:
        os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def run_release_smoke(
    *,
    artifact: Path,
    expected_sha256: str,
    policy: Path,
    preflight: Path,
    work_root: Path,
    output: Path,
    profile: str = "core",
    smoke_relative: Path = DEFAULT_SMOKE_PATH,
) -> dict[str, Any]:
    if profile != "core":
        raise SmokeError("simulator release smoke supports only the core profile")
    smoke_relative = _canonical_relative(
        smoke_relative.as_posix(), "smoke-relative-path"
    )
    if len(expected_sha256) != 64 or any(
        character not in "0123456789abcdef" for character in expected_sha256
    ):
        raise SmokeError("expected SHA-256 is not canonical")
    if os.path.lexists(output):
        raise SmokeError(f"refusing to replace deployment record: {output}")
    _prepare_work_root(work_root)
    input_directory = work_root / "input"
    snapshot = _snapshot_artifact(
        artifact, input_directory / "release.tar.gz", expected_sha256
    )
    preflight_receipt = _run_preflight(
        preflight, snapshot, expected_sha256, policy, profile
    )

    slots = work_root / "releases"
    slots.mkdir(mode=0o700)
    candidate = slots / f"{expected_sha256}-candidate"
    previous = slots / f"{expected_sha256}-previous"
    root_name = _extract_release(snapshot, candidate)
    shutil.copytree(candidate, previous, symlinks=False)
    current = work_root / "current"
    checks: list[dict[str, Any]] = []

    _atomic_switch(work_root, candidate)
    try:
        # Execute the installed binary once.  The rollback and promotion
        # phases validate only the atomic current-pointer transitions; rerun
        # of the identical artifact adds no evidence and obscures failures.
        checks.append(
            {
                "id": "candidate.simulator-e2e",
                "status": "PASS",
                **_run_installed_smoke(current, smoke_relative),
            }
        )
        _atomic_switch(work_root, previous)
        if os.readlink(current) != previous.relative_to(work_root).as_posix():
            raise SmokeError("rollback current pointer mismatch")
        checks.append(
            {
                "id": "rollback.pointer-switch",
                "status": "PASS",
                "current": os.readlink(current),
            }
        )
        _atomic_switch(work_root, candidate)
        if os.readlink(current) != candidate.relative_to(work_root).as_posix():
            raise SmokeError("promotion current pointer mismatch")
        checks.append(
            {
                "id": "promotion.pointer-switch",
                "status": "PASS",
                "current": os.readlink(current),
            }
        )
    except Exception:
        try:
            _atomic_switch(work_root, previous)
        except Exception:
            pass
        raise

    record = {
        "schema": SCHEMA,
        "checked_at": datetime.now(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
        "result": "PASS",
        "profile": profile,
        "artifact": {
            "file": artifact.name,
            "sha256": expected_sha256,
            "snapshot": snapshot.relative_to(work_root).as_posix(),
            "archive_root": root_name,
        },
        "preflight": preflight_receipt,
        "slots": {
            "candidate": candidate.relative_to(work_root).as_posix(),
            "previous": previous.relative_to(work_root).as_posix(),
            "current": os.readlink(current),
            "previous_seed": "same_verified_artifact_pointer_only",
        },
        "checks": checks,
        "broker_mutation": False,
        "authorization_effect": "NONE",
        "paper_authorized": False,
        "live_authorized": False,
    }
    try:
        _write_record(output, record)
    except Exception:
        try:
            _atomic_switch(work_root, previous)
        except Exception:
            pass
        raise
    return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--profile", choices=("core",), default="core")
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--preflight", type=Path, required=True)
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--smoke-relative-path",
        type=Path,
        default=DEFAULT_SMOKE_PATH,
    )
    args = parser.parse_args(argv)
    try:
        record = run_release_smoke(
            artifact=args.artifact.resolve(),
            expected_sha256=args.expected_sha256,
            policy=args.policy.resolve(),
            preflight=args.preflight.resolve(),
            work_root=args.work_root.resolve(),
            output=args.output.resolve(),
            profile=args.profile,
            smoke_relative=args.smoke_relative_path,
        )
    except (OSError, SmokeError, subprocess.SubprocessError, tarfile.TarError) as error:
        print(f"[RELEASE-SIMULATOR-SMOKE] {error}", file=sys.stderr)
        return 2
    print(_canonical_json(record).decode("utf-8"), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
