from __future__ import annotations

import json
import os
from pathlib import Path
import re
import stat
from typing import Any


MAX_REGISTRY_BYTES = 1024 * 1024
JOB_ID = re.compile(r"^[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*$")
WRAPPER_NAME = re.compile(r"^[a-z][a-z0-9_.-]*\.sh$")
ALLOWED_LIFECYCLES = frozenset({"canonical", "compat", "archive"})


class RegistryError(RuntimeError):
    pass


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON value is forbidden: {value}")


def _unique_json_object(
        pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _safe_bytes(path: Path) -> bytes:
    try:
        before = path.lstat()
    except OSError as error:
        raise RegistryError(f"registry is unavailable: {path}") from error
    if (stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode) or
            before.st_nlink != 1):
        raise RegistryError("registry must be a regular non-symlink file")
    if before.st_mode & 0o022:
        raise RegistryError("registry must not be group/world writable")
    if before.st_size > MAX_REGISTRY_BYTES:
        raise RegistryError("registry exceeds the size limit")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise RegistryError("registry open failed") from error
    try:
        opened = os.fstat(descriptor)
        chunks: list[bytes] = []
        remaining = MAX_REGISTRY_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
    finally:
        os.close(descriptor)
    try:
        after = path.lstat()
    except OSError as error:
        raise RegistryError("registry disappeared during read") from error
    identity = lambda value: (
        value.st_dev, value.st_ino, value.st_mode, value.st_nlink,
        value.st_uid, value.st_gid, value.st_size, value.st_mtime_ns,
        value.st_ctime_ns,
    )
    if identity(before) != identity(opened) or identity(opened) != identity(after):
        raise RegistryError("registry changed during read")
    if len(payload) > MAX_REGISTRY_BYTES:
        raise RegistryError("registry exceeds the size limit")
    return payload


def _relative_path(value: Any, field: str) -> str:
    if (not isinstance(value, str) or not value or "\0" in value or
            "\\" in value):
        raise RegistryError(f"{field} must be a non-empty string")
    path = Path(value)
    normalized = path.as_posix()
    if (path.is_absolute() or ".." in path.parts or "." in path.parts or
            normalized != value):
        raise RegistryError(f"{field} must be a normalized relative path")
    return normalized


def _validate_job(job_id: str, job: Any) -> dict[str, Any]:
    if not JOB_ID.fullmatch(job_id):
        raise RegistryError(f"invalid job id: {job_id}")
    if not isinstance(job, dict):
        raise RegistryError(f"job must be an object: {job_id}")
    required = {
        "lifecycle", "executable", "arguments", "allow_user_arguments",
        "network_allowed", "paper_authorized", "live_authorized",
        "compatibility_wrappers",
    }
    if set(job) != required:
        raise RegistryError(f"job fields do not exactly match schema: {job_id}")
    lifecycle = job["lifecycle"]
    if lifecycle not in ALLOWED_LIFECYCLES:
        raise RegistryError(f"invalid lifecycle for {job_id}")
    executable = _relative_path(job["executable"], f"{job_id}.executable")
    if not executable.startswith("scripts/") or not executable.endswith(".py"):
        raise RegistryError(
            f"{job_id}.executable must be a Python entry point under scripts/")
    arguments = job["arguments"]
    if not isinstance(arguments, list) or not all(
            isinstance(argument, str) and "\0" not in argument
            for argument in arguments):
        raise RegistryError(f"arguments must be a string array: {job_id}")
    wrappers = job["compatibility_wrappers"]
    if not isinstance(wrappers, list):
        raise RegistryError(f"compatibility_wrappers must be an array: {job_id}")
    normalized_wrappers = [
        _relative_path(wrapper, f"{job_id}.compatibility_wrappers")
        for wrapper in wrappers
    ]
    for wrapper in normalized_wrappers:
        wrapper_path = Path(wrapper)
        if (wrapper_path.parent.as_posix() != "compat/hepta-ops-generated" or
                not WRAPPER_NAME.fullmatch(wrapper_path.name)):
            raise RegistryError(
                f"compatibility wrapper is outside the generated tree: {job_id}")
    for field in (
            "allow_user_arguments", "network_allowed", "paper_authorized",
            "live_authorized"):
        if not isinstance(job[field], bool):
            raise RegistryError(f"{field} must be boolean: {job_id}")
    if lifecycle == "archive" and normalized_wrappers:
        raise RegistryError(f"archive job cannot install wrappers: {job_id}")
    if (job["network_allowed"] or job["paper_authorized"] or
            job["live_authorized"]):
        raise RegistryError(
            f"operations job cannot authorize network or trading: {job_id}")
    normalized = dict(job)
    normalized["executable"] = executable
    normalized["compatibility_wrappers"] = normalized_wrappers
    return normalized


def load_registry(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(
            _safe_bytes(path).decode("utf-8", errors="strict"),
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise RegistryError("registry is not strict UTF-8 JSON") from error
    if not isinstance(payload, dict) or set(payload) != {
            "schema", "version", "jobs"}:
        raise RegistryError("registry fields do not exactly match schema")
    if payload["schema"] != "hepta.ops-registry.v1":
        raise RegistryError("unsupported registry schema")
    if payload["version"] != 1:
        raise RegistryError("unsupported registry version")
    if not isinstance(payload["jobs"], dict) or not payload["jobs"]:
        raise RegistryError("registry jobs must be a non-empty object")
    jobs = {
        job_id: _validate_job(job_id, job)
        for job_id, job in sorted(payload["jobs"].items())
    }
    wrappers = [
        wrapper
        for job in jobs.values()
        for wrapper in job["compatibility_wrappers"]
    ]
    if len(wrappers) != len(set(wrappers)):
        raise RegistryError("compatibility wrapper paths must be unique")
    wrapper_names = [Path(wrapper).name for wrapper in wrappers]
    if len(wrapper_names) != len(set(wrapper_names)):
        raise RegistryError("compatibility wrapper basenames must be unique")
    return {"schema": payload["schema"], "version": 1, "jobs": jobs}
