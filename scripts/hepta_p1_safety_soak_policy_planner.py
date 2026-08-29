#!/usr/bin/env python3
"""Create a prospective, non-authorizing P1 SHADOW policy before admission.

The installed root helper calls only the policy builder's pure ``build_policy``
API.  It never consumes an admission receipt or creates an authority marker.
Both executing images are pinned to one clean frozen source baseline, and the
canonical policy is published with no-replace durability semantics.
"""

from __future__ import annotations

import argparse
import ctypes
from dataclasses import dataclass
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys
import time
import types
from typing import Any, Mapping, Sequence


ROOT_UID = 0
ROOT_GID = 0
INSTALLED_EXECUTABLE = Path(
    "/usr/libexec/hepta-p1-safety-soak-policy-planner")
BUILDER_EXECUTABLE = Path(
    "/usr/libexec/build-hepta-p1-observation-policy")
PLANNER_SOURCE_PATH = "scripts/hepta_p1_safety_soak_policy_planner.py"
BUILDER_SOURCE_PATH = "scripts/build_hepta_p1_observation_policy.py"
SOURCE_BASELINE_SCHEMA = "hepta.versioned-source-baseline.v1"
POLICY_SCHEMA = "hepta.strategy-shadow-observation-policy.v1"
PRODUCTION_MODE = "PRODUCTION_ROOT_PROSPECTIVE_POLICY_PLANNING"

LAUNCHER_WARMUP_MS = 210 * 60 * 1000
LAUNCHER_EARLY_START_LEAD_MS = 20 * 60 * 1000
SLOT_INTERVAL_MS = 2 * 60 * 1000
MAXIMUM_ITERATIONS = 241
MAXIMUM_LATENESS_MS = 60 * 1000

MAXIMUM_INPUT_BYTES = 64 * 1024 * 1024
MAXIMUM_OUTPUT_BYTES = 4 * 1024 * 1024
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
COMMIT = re.compile(r"[0-9a-f]{40}")
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
BASELINE_FIELDS = frozenset({
    "schema", "version", "generated_at", "git_head", "source_manifest",
    "source_baseline_frozen", "clean_checkout_certified",
    "release_authorized", "paper_authorized", "live_authorized",
    "worktree_status_entry_count", "blocked_reason", "excluded_unsafe_tree",
})
MANIFEST_FIELDS = frozenset({"file_count", "sha256", "files"})
POLICY_FIELDS = frozenset({
    "schema", "version", "campaign_id", "campaign_sha256", "strategy_id",
    "strategy_version", "strategy_sha256", "valid_after_ms",
    "expires_at_ms", "slot_interval_ms", "maximum_iterations",
    "maximum_lateness_ms", "shadow_only", "paper_authorized",
    "live_authorized", "mutation_attempted", "direct_broker_access",
    "body_sha256",
})
CAMPAIGN_FIELDS = frozenset({
    "schema", "campaign_id", "valid_after_ms", "expires_at_ms",
    "slot_interval_ms", "maximum_iterations", "maximum_lateness_ms",
    "shadow_only", "paper_authorized", "live_authorized",
    "mutation_attempted", "direct_broker_access",
})

NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
CLOEXEC = getattr(os, "O_CLOEXEC", 0)
READ_FLAGS = os.O_RDONLY | NOFOLLOW | CLOEXEC
DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | NOFOLLOW | CLOEXEC
CREATE_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | NOFOLLOW | CLOEXEC
RENAME_NOREPLACE = 1
_LIBC = ctypes.CDLL(None, use_errno=True)


class PlannerError(RuntimeError):
    """Stable prospective-policy planning failure."""


@dataclass(frozen=True)
class Snapshot:
    path: Path
    payload: bytes
    metadata: os.stat_result
    document: dict[str, Any]
    file_sha256: str


@dataclass(frozen=True)
class DirectoryBinding:
    path: Path
    metadata: os.stat_result


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise PlannerError(reason)


def canonical_bytes(value: Any) -> bytes:
    try:
        return (json.dumps(
            value, ensure_ascii=True, allow_nan=False, sort_keys=True,
            separators=(",", ":")) + "\n").encode("ascii")
    except (TypeError, ValueError, UnicodeError) as error:
        raise PlannerError("P1_POLICY_PLANNER_CANONICALIZATION_FAILED") \
            from error


def digest_bytes(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in values:
        _require(key not in result, "P1_POLICY_PLANNER_DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def _document(payload: bytes, reason: str) -> dict[str, Any]:
    try:
        value = json.loads(
            payload.decode("utf-8", errors="strict"),
            object_pairs_hook=_pairs,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                PlannerError(reason)))
    except PlannerError:
        raise
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise PlannerError(reason) from error
    _require(isinstance(value, dict), reason)
    return value


def _digest(value: Any, reason: str) -> str:
    _require(type(value) is str and DIGEST.fullmatch(value) is not None and
             value != "sha256:" + "0" * 64, reason)
    return value


def _absolute(path: Path, reason: str) -> Path:
    _require(path.is_absolute() and Path(os.path.normpath(str(path))) == path and
             path.name not in {"", ".", ".."} and
             all(part not in {"", ".", ".."} for part in path.parts[1:]),
             reason)
    return path


def _identity(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev, value.st_ino, value.st_uid, value.st_gid,
        stat.S_IMODE(value.st_mode), value.st_nlink, value.st_size,
        value.st_mtime_ns, value.st_ctime_ns,
    )


def _directory_identity(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev, value.st_ino, value.st_uid, value.st_gid,
        stat.S_IMODE(value.st_mode),
    )


def _trusted_directory_metadata(
    value: os.stat_result, *, expected_uid: int, expected_gid: int,
    reason: str,
) -> None:
    allowed_uids = {ROOT_UID} if expected_uid == ROOT_UID else {
        ROOT_UID, expected_uid}
    allowed_gids = {ROOT_GID} if expected_gid == ROOT_GID else {
        ROOT_GID, expected_gid}
    _require(
        stat.S_ISDIR(value.st_mode) and value.st_uid in allowed_uids and
        value.st_gid in allowed_gids and
        stat.S_IMODE(value.st_mode) & 0o022 == 0,
        reason)


def _open_trusted_directory(
    path: Path, *, expected_uid: int, expected_gid: int, reason: str,
) -> int:
    """Traverse an absolute directory without following any component."""

    _absolute(path, reason)
    descriptors: list[int] = []
    try:
        current = os.open("/", DIRECTORY_FLAGS)
        descriptors.append(current)
        _trusted_directory_metadata(
            os.fstat(current), expected_uid=expected_uid,
            expected_gid=expected_gid, reason=reason)
        for component in path.parts[1:]:
            parent_before = os.fstat(current)
            child = os.open(component, DIRECTORY_FLAGS, dir_fd=current)
            descriptors.append(child)
            opened = os.fstat(child)
            named = os.stat(
                component, dir_fd=current, follow_symlinks=False)
            parent_after = os.fstat(current)
            _trusted_directory_metadata(
                opened, expected_uid=expected_uid,
                expected_gid=expected_gid, reason=reason)
            _require(
                _directory_identity(opened) == _directory_identity(named) and
                _directory_identity(parent_before) ==
                    _directory_identity(parent_after), reason)
            current = child
        result = descriptors.pop()
        return result
    except (OSError, PlannerError) as error:
        if isinstance(error, PlannerError):
            raise
        raise PlannerError(reason) from error
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def bind_directory(
    path: Path, *, expected_uid: int, expected_gid: int,
) -> DirectoryBinding:
    reason = "P1_POLICY_PLANNER_DIRECTORY_INVALID"
    descriptor = _open_trusted_directory(
        path, expected_uid=expected_uid, expected_gid=expected_gid,
        reason=reason)
    try:
        return DirectoryBinding(path=path, metadata=os.fstat(descriptor))
    finally:
        os.close(descriptor)


def _assert_directory(
    binding: DirectoryBinding, *, expected_uid: int, expected_gid: int,
) -> None:
    descriptor = _open_trusted_directory(
        binding.path, expected_uid=expected_uid, expected_gid=expected_gid,
        reason="P1_POLICY_PLANNER_DIRECTORY_DRIFT")
    try:
        _require(
            _directory_identity(os.fstat(descriptor)) ==
                _directory_identity(binding.metadata),
            "P1_POLICY_PLANNER_DIRECTORY_DRIFT")
    finally:
        os.close(descriptor)


def secure_read(
    path: Path, *, expected_uid: int, expected_gid: int,
    modes: frozenset[int], maximum: int = MAXIMUM_INPUT_BYTES,
) -> tuple[bytes, os.stat_result]:
    reason = "P1_POLICY_PLANNER_INPUT_INVALID"
    _absolute(path, reason)
    parent: int | None = None
    descriptor: int | None = None
    reopened: int | None = None
    try:
        parent = _open_trusted_directory(
            path.parent, expected_uid=expected_uid,
            expected_gid=expected_gid, reason=reason)
        parent_before = os.fstat(parent)
        named_before = os.stat(
            path.name, dir_fd=parent, follow_symlinks=False)
        descriptor = os.open(path.name, READ_FLAGS, dir_fd=parent)
        before = os.fstat(descriptor)
        _require(
            _identity(named_before) == _identity(before) and
            stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and
            before.st_uid == expected_uid and before.st_gid == expected_gid and
            stat.S_IMODE(before.st_mode) in modes and
            0 < before.st_size <= maximum,
            reason)
        chunks: list[bytes] = []
        remaining = maximum + 1
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        after = os.fstat(descriptor)
        named_after = os.stat(
            path.name, dir_fd=parent, follow_symlinks=False)
        reopened = os.open(path.name, READ_FLAGS, dir_fd=parent)
        rebound = os.fstat(reopened)
        rebound_payload = bytearray()
        while len(rebound_payload) <= maximum:
            chunk = os.read(
                reopened, min(1024 * 1024,
                              maximum + 1 - len(rebound_payload)))
            if not chunk:
                break
            rebound_payload.extend(chunk)
        parent_after = os.fstat(parent)
        _require(
            0 < len(payload) <= maximum and len(payload) == before.st_size and
            _identity(before) == _identity(after) ==
                _identity(named_after) == _identity(rebound) and
            bytes(rebound_payload) == payload and
            _directory_identity(parent_before) ==
                _directory_identity(parent_after), reason)
        return payload, after
    except PlannerError:
        raise
    except OSError as error:
        raise PlannerError(reason) from error
    finally:
        for opened in (reopened, descriptor, parent):
            if opened is not None:
                os.close(opened)


def load_baseline(
    path: Path, *, expected_uid: int, expected_gid: int,
) -> Snapshot:
    payload, metadata = secure_read(
        path, expected_uid=expected_uid, expected_gid=expected_gid,
        modes=frozenset({0o400, 0o600}))
    return Snapshot(
        path=path, payload=payload, metadata=metadata,
        document=_document(payload, "P1_POLICY_PLANNER_BASELINE_INVALID"),
        file_sha256=digest_bytes(payload))


def validate_source_bindings(
    baseline: Snapshot, *, expected_baseline_file_sha256: str,
    planner_path: Path, builder_path: Path, expected_uid: int,
    expected_gid: int,
) -> dict[str, str]:
    reason = "P1_POLICY_PLANNER_SOURCE_BINDING_INVALID"
    value = baseline.document
    _require(set(value) == BASELINE_FIELDS and
             baseline.file_sha256 == _digest(
                 expected_baseline_file_sha256, reason) and
             value.get("schema") == SOURCE_BASELINE_SCHEMA and
             type(value.get("version")) is str and bool(value["version"]) and
             type(value.get("generated_at")) is str and
             COMMIT.fullmatch(str(value.get("git_head", ""))) is not None and
             value.get("source_baseline_frozen") is True and
             value.get("clean_checkout_certified") is True and
             value.get("release_authorized") is False and
             value.get("paper_authorized") is False and
             value.get("live_authorized") is False and
             value.get("worktree_status_entry_count") == 0 and
             value.get("blocked_reason") is None and
             value.get("excluded_unsafe_tree") ==
                "compat/unsafe-direct-broker", reason)
    manifest = value.get("source_manifest")
    _require(isinstance(manifest, dict) and
             set(manifest) == MANIFEST_FIELDS, reason)
    files = manifest.get("files")
    _require(isinstance(files, list) and bool(files) and
             manifest.get("file_count") == len(files) and
             manifest.get("sha256") == digest_bytes(json.dumps(
                 files, ensure_ascii=True, allow_nan=False, sort_keys=True,
                 separators=(",", ":")).encode("ascii")), reason)
    by_path: dict[str, str] = {}
    for item in files:
        _require(isinstance(item, dict) and type(item.get("path")) is str and
                 item["path"] not in by_path, reason)
        by_path[item["path"]] = _digest(item.get("sha256"), reason)
    _require(PLANNER_SOURCE_PATH in by_path and
             BUILDER_SOURCE_PATH in by_path, reason)
    planner_payload, _planner_metadata = secure_read(
        planner_path, expected_uid=expected_uid, expected_gid=expected_gid,
        modes=frozenset({0o755}))
    builder_payload, _builder_metadata = secure_read(
        builder_path, expected_uid=expected_uid, expected_gid=expected_gid,
        modes=frozenset({0o755}))
    _require(
        digest_bytes(planner_payload) == by_path[PLANNER_SOURCE_PATH] and
        digest_bytes(builder_payload) == by_path[BUILDER_SOURCE_PATH], reason)
    return {
        "planner": by_path[PLANNER_SOURCE_PATH],
        "builder": by_path[BUILDER_SOURCE_PATH],
    }


def _load_builder(snapshot: Snapshot) -> Any:
    reason = "P1_POLICY_PLANNER_BUILDER_LOAD_FAILED"
    name = "hepta_bound_p1_observation_policy_builder_" + \
        snapshot.file_sha256.removeprefix("sha256:")
    module = types.ModuleType(name)
    module.__file__ = str(snapshot.path)
    module.__package__ = ""
    sys.modules[name] = module
    try:
        code = compile(snapshot.payload, str(snapshot.path), "exec")
        exec(code, module.__dict__)
    except Exception as error:
        raise PlannerError(reason) from error
    return module


def _validate_policy(
    value: Any, *, campaign_id: str, launcher_start_ms: int,
    expected_strategy_sha256: str,
) -> dict[str, Any]:
    reason = "P1_POLICY_PLANNER_POLICY_INVALID"
    _require(isinstance(value, dict) and set(value) == POLICY_FIELDS, reason)
    body = dict(value)
    claimed = body.pop("body_sha256", None)
    _require(_digest(claimed, reason) == digest_bytes(canonical_bytes(body)),
             reason)
    valid_after = value.get("valid_after_ms")
    expires = value.get("expires_at_ms")
    _require(
        value.get("schema") == POLICY_SCHEMA and value.get("version") == 1 and
        value.get("campaign_id") == campaign_id and
        type(valid_after) is int and
        valid_after == launcher_start_ms + LAUNCHER_WARMUP_MS and
        value.get("slot_interval_ms") == SLOT_INTERVAL_MS and
        value.get("maximum_iterations") == MAXIMUM_ITERATIONS and
        value.get("maximum_lateness_ms") == MAXIMUM_LATENESS_MS and
        expires == valid_after + SLOT_INTERVAL_MS * MAXIMUM_ITERATIONS and
        value.get("strategy_sha256") == expected_strategy_sha256 and
        value.get("shadow_only") is True and
        value.get("paper_authorized") is False and
        value.get("live_authorized") is False and
        value.get("mutation_attempted") is False and
        value.get("direct_broker_access") is False,
        reason)
    campaign = {
        key: value[key] for key in CAMPAIGN_FIELDS if key != "schema"
    }
    campaign["schema"] = "hepta.strategy-shadow-observation-campaign.v1"
    _require(value.get("campaign_sha256") ==
             digest_bytes(canonical_bytes(campaign)), reason)
    _require(type(value.get("strategy_id")) is str and
             IDENTIFIER.fullmatch(value["strategy_id"]) is not None and
             type(value.get("strategy_version")) is str and
             IDENTIFIER.fullmatch(value["strategy_version"]) is not None,
             reason)
    return value


def plan_policy(
    *, campaign_id: str, launcher_start_ms: int, strategy_path: Path,
    runtime_directory: Path, expected_strategy_sha256: str,
    builder: Any, now_ms: int,
) -> dict[str, Any]:
    reason = "P1_POLICY_PLANNER_REQUEST_INVALID"
    _require(type(campaign_id) is str and
             IDENTIFIER.fullmatch(campaign_id) is not None and
             type(launcher_start_ms) is int and launcher_start_ms > 0 and
             launcher_start_ms - LAUNCHER_EARLY_START_LEAD_MS > now_ms and
             strategy_path.is_absolute() and runtime_directory.is_absolute() and
             _digest(expected_strategy_sha256, reason) and
             getattr(builder, "MINIMUM_WARMUP_MS", None) ==
                LAUNCHER_WARMUP_MS and
             getattr(builder, "SLOT_INTERVAL_MS", None) == SLOT_INTERVAL_MS and
             getattr(builder, "MAXIMUM_ITERATIONS", None) ==
                MAXIMUM_ITERATIONS and
             getattr(builder, "MAXIMUM_LATENESS_MS", None) ==
                MAXIMUM_LATENESS_MS and
             callable(getattr(builder, "build_policy", None)), reason)
    _require(
        (launcher_start_ms + LAUNCHER_WARMUP_MS) % SLOT_INTERVAL_MS == 0,
        "P1_POLICY_PLANNER_LAUNCH_ALIGNMENT_INVALID")
    try:
        value = builder.build_policy(
            campaign_id=campaign_id,
            start_ms=launcher_start_ms,
            strategy_path=strategy_path,
            runtime_directory=runtime_directory,
            expected_strategy_sha256=expected_strategy_sha256)
    except Exception as error:
        raise PlannerError("P1_POLICY_PLANNER_BUILD_FAILED") from error
    return _validate_policy(
        value, campaign_id=campaign_id,
        launcher_start_ms=launcher_start_ms,
        expected_strategy_sha256=expected_strategy_sha256)


def _rename_noreplace(parent: int, source: str, destination: str) -> None:
    function = getattr(_LIBC, "renameat2", None)
    _require(function is not None,
             "P1_POLICY_PLANNER_RENAMEAT2_UNAVAILABLE")
    function.argtypes = (
        ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p,
        ctypes.c_uint)
    function.restype = ctypes.c_int
    ctypes.set_errno(0)
    result = function(
        parent, os.fsencode(source), parent, os.fsencode(destination),
        RENAME_NOREPLACE)
    if result != 0:
        number = ctypes.get_errno()
        if number == errno.EEXIST:
            raise PlannerError("P1_POLICY_PLANNER_OUTPUT_ALREADY_EXISTS")
        raise PlannerError("P1_POLICY_PLANNER_OUTPUT_RENAME_FAILED")


def publish_policy(
    output: Path, policy: Mapping[str, Any], *, expected_uid: int,
    expected_gid: int,
) -> Snapshot:
    reason = "P1_POLICY_PLANNER_OUTPUT_INVALID"
    _absolute(output, reason)
    payload = canonical_bytes(dict(policy))
    _require(0 < len(payload) <= MAXIMUM_OUTPUT_BYTES, reason)
    parent_path = output.parent
    try:
        parent = _open_trusted_directory(
            parent_path, expected_uid=expected_uid,
            expected_gid=expected_gid, reason=reason)
    except OSError as error:
        raise PlannerError(reason) from error
    temporary = "." + output.name + ".tmp-" + secrets.token_hex(16)
    descriptor: int | None = None
    renamed = False
    try:
        parent_metadata = os.fstat(parent)
        _require(
            stat.S_ISDIR(parent_metadata.st_mode) and
            parent_metadata.st_uid == expected_uid and
            parent_metadata.st_gid == expected_gid and
            stat.S_IMODE(parent_metadata.st_mode) & 0o022 == 0,
            reason)
        descriptor = os.open(temporary, CREATE_FLAGS, 0o600, dir_fd=parent)
        view = memoryview(payload)
        while view:
            count = os.write(descriptor, view)
            _require(count > 0, "P1_POLICY_PLANNER_OUTPUT_WRITE_FAILED")
            view = view[count:]
        os.fsync(descriptor)
        prepared = os.fstat(descriptor)
        _require(
            stat.S_ISREG(prepared.st_mode) and prepared.st_nlink == 1 and
            prepared.st_uid == expected_uid and
            prepared.st_gid == expected_gid and
            stat.S_IMODE(prepared.st_mode) == 0o600 and
            prepared.st_size == len(payload), reason)
        os.fsync(parent)
        _rename_noreplace(parent, temporary, output.name)
        renamed = True
        os.fsync(parent)
    except PlannerError:
        raise
    except OSError as error:
        raise PlannerError("P1_POLICY_PLANNER_OUTPUT_PUBLISH_FAILED") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if not renamed:
            try:
                os.unlink(temporary, dir_fd=parent)
                os.fsync(parent)
            except (FileNotFoundError, OSError):
                pass
        os.close(parent)
    committed, metadata = secure_read(
        output, expected_uid=expected_uid, expected_gid=expected_gid,
        modes=frozenset({0o600}), maximum=MAXIMUM_OUTPUT_BYTES)
    _require(committed == payload, "P1_POLICY_PLANNER_OUTPUT_POST_VERIFY_FAILED")
    restored = _document(committed, reason)
    _require(restored == policy, reason)
    return Snapshot(
        path=output, payload=committed, metadata=metadata,
        document=restored, file_sha256=digest_bytes(committed))


def _assert_snapshot(snapshot: Snapshot, *, uid: int, gid: int) -> None:
    payload, metadata = secure_read(
        snapshot.path, expected_uid=uid, expected_gid=gid,
        modes=frozenset({stat.S_IMODE(snapshot.metadata.st_mode)}))
    _require(payload == snapshot.payload and
             _identity(metadata) == _identity(snapshot.metadata),
             "P1_POLICY_PLANNER_INPUT_DRIFT")


def bind_executing_image() -> Snapshot:
    reason = "P1_POLICY_PLANNER_INSTALLED_IMAGE_REQUIRED"
    try:
        path = Path(__file__)
        _require(not path.is_symlink() and
                 path.resolve(strict=True) == INSTALLED_EXECUTABLE and
                 os.path.samefile(path, INSTALLED_EXECUTABLE), reason)
        payload, metadata = secure_read(
            INSTALLED_EXECUTABLE, expected_uid=ROOT_UID,
            expected_gid=ROOT_GID, modes=frozenset({0o755}))
    except (OSError, PlannerError) as error:
        raise PlannerError(reason) from error
    return Snapshot(
        path=INSTALLED_EXECUTABLE, payload=payload, metadata=metadata,
        document={}, file_sha256=digest_bytes(payload))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build one pre-admission P1 SHADOW policy")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--source-baseline", type=Path, required=True)
    parser.add_argument(
        "--expected-source-baseline-file-sha256", required=True)
    parser.add_argument("--strategy", type=Path, required=True)
    parser.add_argument("--runtime-directory", type=Path, required=True)
    parser.add_argument("--expected-strategy-sha256", required=True)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--launcher-start-ms", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args(argv)
    try:
        _require(arguments.run, "P1_POLICY_PLANNER_EXPLICIT_RUN_REQUIRED")
        _require(os.geteuid() == ROOT_UID and os.getegid() == ROOT_GID,
                 "P1_POLICY_PLANNER_ROOT_REQUIRED")
        for path in (
                arguments.source_baseline, arguments.strategy,
                arguments.runtime_directory, arguments.output):
            _absolute(path, "P1_POLICY_PLANNER_PATH_INVALID")
        producer = bind_executing_image()
        baseline = load_baseline(
            arguments.source_baseline, expected_uid=ROOT_UID,
            expected_gid=ROOT_GID)
        pins = validate_source_bindings(
            baseline,
            expected_baseline_file_sha256=
                arguments.expected_source_baseline_file_sha256,
            planner_path=INSTALLED_EXECUTABLE,
            builder_path=BUILDER_EXECUTABLE,
            expected_uid=ROOT_UID, expected_gid=ROOT_GID)
        _require(producer.file_sha256 == pins["planner"],
                 "P1_POLICY_PLANNER_SOURCE_BINDING_INVALID")
        builder_payload, builder_metadata = secure_read(
            BUILDER_EXECUTABLE, expected_uid=ROOT_UID,
            expected_gid=ROOT_GID, modes=frozenset({0o755}))
        builder_snapshot = Snapshot(
            path=BUILDER_EXECUTABLE, payload=builder_payload,
            metadata=builder_metadata, document={},
            file_sha256=digest_bytes(builder_payload))
        _require(builder_snapshot.file_sha256 == pins["builder"],
                 "P1_POLICY_PLANNER_SOURCE_BINDING_INVALID")
        strategy_payload, strategy_metadata = secure_read(
            arguments.strategy, expected_uid=ROOT_UID,
            expected_gid=ROOT_GID,
            modes=frozenset({0o400, 0o440, 0o444, 0o600, 0o640, 0o644}))
        _require(digest_bytes(strategy_payload) ==
                 arguments.expected_strategy_sha256,
                 "P1_POLICY_PLANNER_STRATEGY_BINDING_INVALID")
        strategy_snapshot = Snapshot(
            path=arguments.strategy, payload=strategy_payload,
            metadata=strategy_metadata, document={},
            file_sha256=digest_bytes(strategy_payload))
        runtime_binding = bind_directory(
            arguments.runtime_directory, expected_uid=ROOT_UID,
            expected_gid=ROOT_GID)
        builder = _load_builder(builder_snapshot)
        _assert_snapshot(builder_snapshot, uid=ROOT_UID, gid=ROOT_GID)
        policy = plan_policy(
            campaign_id=arguments.campaign_id,
            launcher_start_ms=arguments.launcher_start_ms,
            strategy_path=arguments.strategy,
            runtime_directory=arguments.runtime_directory,
            expected_strategy_sha256=arguments.expected_strategy_sha256,
            builder=builder, now_ms=time.time_ns() // 1_000_000)
        _assert_snapshot(baseline, uid=ROOT_UID, gid=ROOT_GID)
        _assert_snapshot(producer, uid=ROOT_UID, gid=ROOT_GID)
        _assert_snapshot(builder_snapshot, uid=ROOT_UID, gid=ROOT_GID)
        _assert_snapshot(strategy_snapshot, uid=ROOT_UID, gid=ROOT_GID)
        _assert_directory(
            runtime_binding, expected_uid=ROOT_UID, expected_gid=ROOT_GID)
        publish_policy(
            arguments.output, policy,
            expected_uid=ROOT_UID, expected_gid=ROOT_GID)
        _assert_snapshot(baseline, uid=ROOT_UID, gid=ROOT_GID)
        _assert_snapshot(producer, uid=ROOT_UID, gid=ROOT_GID)
        _assert_snapshot(builder_snapshot, uid=ROOT_UID, gid=ROOT_GID)
        _assert_snapshot(strategy_snapshot, uid=ROOT_UID, gid=ROOT_GID)
        _assert_directory(
            runtime_binding, expected_uid=ROOT_UID, expected_gid=ROOT_GID)
    except (PlannerError, OSError, ValueError) as error:
        print("hepta_p1_safety_soak_policy_planner: FAIL " + str(error),
              file=sys.stderr)
        return 3
    sys.stdout.buffer.write(canonical_bytes(policy))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
