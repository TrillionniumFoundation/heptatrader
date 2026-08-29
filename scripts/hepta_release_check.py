#!/usr/bin/env python3
"""Native/Linux release phase gate.

This is the canonical, authority-free release check used by CI and Linux
operators.  It deliberately only *consumes* rootful/P1/PAPER receipts; it
never provisions a host, starts systemd units, or grants trading authority.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib
import json
import os
from pathlib import Path
import secrets
import stat
import subprocess
import sys
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
import run_execution_gateway_soak as soak_runner
from run_execution_gateway_soak import SoakProfile


# These are deliberately duplicated as small, immutable protocol constants
# instead of accepting a family/prefix.  A release check consumes receipts
# produced by a *specific* gate version; accepting ``hepta.`` would let an
# unrelated (or future, incompatible) document masquerade as a promotion
# input.  Keep these values synchronized with the producer modules named in
# the comments below.
ROOTFUL_REPORT_SCHEMA = "hepta.execution-native-systemd-aggregate.v6"
ROOTFUL_CERTIFICATION_LEVEL = (
    "native-disposable-vm-agent-os-watch-runtime-rootful-systemd")
P1_REPORT_SCHEMA = (
    "hepta.p1-safety-soak-campaign-rootful-liveness-gate.v1")
PAPER_AUTHORITY_REPORT_SCHEMA = (
    "hepta.paper-testing-admission-candidate-receipt.v1")

# Short aliases are useful to callers/tests and make the contract map easy to
# read.  They are aliases, not additional accepted schema versions.
ROOTFUL_SCHEMA = ROOTFUL_REPORT_SCHEMA
P1_SCHEMA = P1_REPORT_SCHEMA
PAPER_AUTHORITY_SCHEMA = PAPER_AUTHORITY_REPORT_SCHEMA

# ``paper --rc-report`` is an optional cross-phase hand-off.  It is not an
# authority grant: the paper command still performs its own checks and keeps
# every capability bit false.  When a caller supplies the hand-off, however,
# it must be the exact v1 RC summary produced for the same config, promotion
# soak profile, and already-validated soak artifact.
RELEASE_CHECK_SCHEMA = "hepta.release-check.v1"
RC_SUMMARY_AUTHORITY_FIELDS = (
    "authority_granted", "paper_test_admission_candidate",
    "paper_admission_authorized", "paper_authorized", "live_authorized",
    "mutation_authorized", "direct_broker_access",
    "order_submission_authorized",
)
RC_SUMMARY_REQUIRED_CHECKS = (
    "CONFIG_PROFILE_LOCK", "STATIC_CHECKS", "SOAK_PROFILE_POLICY",
    "EXECUTION_GATEWAY_SOAK",
)

# A short PR smoke is useful feedback but is not promotion evidence.  Both
# release profiles currently carry the eight-round certification; keeping the
# allow-list here prevents a caller from pairing a two-round receipt with an
# ``rc``/``paper`` phase merely by passing ``--soak-profile pr-smoke``.
PROMOTION_SOAK_PROFILES = frozenset(("release", "nightly"))

ROOTFUL_REQUIRED_FIELDS = frozenset({
    "schema", "passed", "certification_level", "variants",
    "common_closure", "aggregation_inputs", "boundary",
})
P1_REQUIRED_FIELDS = frozenset({
    "schema", "run_id", "decision", "passed", "rehearsal_passed",
    "certification_ready", "certification_blockers", "scope",
    "production_mode", "paper_test_admission_candidate",
    "paper_admission_authorized", "paper_authorized", "live_authorized",
    "mutation_authorized", "direct_broker_access",
    "order_submission_authorized", "boundary", "body_sha256",
})
PAPER_AUTHORITY_REQUIRED_FIELDS = frozenset({
    "schema", "version", "status", "paper_test_admission_candidate",
    "paper_authorized", "live_authorized", "mutation_authorized",
    "direct_broker_access", "order_submission_authorized",
    "authorization_effect", "findings", "body_sha256",
})
MAX_RECEIPT_BYTES = 4 * 1024 * 1024
DEFAULT_REPORT_DIRECTORY = ".hepta-release-logs"

# These are the producer-owned inert boundaries for the exact receipt
# versions above.  Checking equality (rather than merely checking any flags
# that happen to be present) prevents a forged receipt from omitting a zero
# counter or an explicit no-broker marker and still being treated as a valid
# prerequisite.
ROOTFUL_BOUNDARY = {
    "real_ibapi_elf_executed": False,
    "real_broker_connections": 0,
    "paper_orders": 0,
    "live_enabled": False,
    "paper_authorized": False,
    "native_agent_os_installation_gate_satisfied": True,
    "native_agent_os_runtime_gate_satisfied": True,
    "agent_os_runtime_preflight_executed": True,
    "agent_os_runtime_preflight_required": True,
    "agent_os_runtime_evidence_fabricated": False,
    "agent_os_runtime_source":
        "three-distinct-externally-attested-native-vms",
    "ib_adapter_visible_during_agent_os_runtime": False,
    "paper_certification": "requires_separate_explicit_authorization",
}
P1_BOUNDARY = {
    "broker_connectors": 0,
    "broker_connections": 0,
    "broker_protocol_messages": 0,
    "paper_orders": 0,
    "paper_test_admission_candidate": False,
    "paper_authorized": False,
    "live_authorized": False,
    "mutation_authorized": False,
    "direct_broker_access": False,
    "order_submission_authorized": False,
    "host_bind_mounts": 0,
    "host_systemd_units_touched": 0,
    "host_network_rules_touched": 0,
    "real_credentials": 0,
}

AUTHORITY_BOOLEAN_FIELDS = frozenset({
    "authority_granted", "paper_admission_authorized", "paper_authorized",
    "paper_test_admission_candidate", "live_authorized",
    "mutation_authorized", "direct_broker_access",
    "order_submission_authorized",
})
_FALSE_AUTHORITY_FIELDS = frozenset({
    "authority_granted", "paper_admission_authorized", "paper_authorized",
    "live_authorized", "mutation_authorized", "direct_broker_access",
    "order_submission_authorized",
})


def _strict_object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Reject duplicate JSON members instead of silently taking the last."""
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def _canonical_json(value: Any) -> bytes:
    return (json.dumps(
        value, ensure_ascii=True, allow_nan=False, sort_keys=True,
        separators=(",", ":")) + "\n").encode("ascii")


def _sealed_body_matches(payload: dict[str, Any]) -> bool:
    claimed = payload.get("body_sha256")
    if not isinstance(claimed, str) or len(claimed) != 71 or \
            not claimed.startswith("sha256:"):
        return False
    body = dict(payload)
    body.pop("body_sha256", None)
    try:
        expected = "sha256:" + hashlib.sha256(_canonical_json(body)).hexdigest()
    except (TypeError, ValueError, UnicodeError):
        return False
    return claimed == expected


def _receipt_metadata(value: Any) -> tuple[int, ...]:
    return tuple(int(getattr(value, field)) for field in (
        "st_dev", "st_ino", "st_mode", "st_nlink", "st_uid", "st_gid",
        "st_size", "st_mtime_ns", "st_ctime_ns"))


def _directory_identity(value: Any) -> tuple[int, ...]:
    """Return directory identity fields that do not change on child creation."""
    metadata = _receipt_metadata(value)
    return (metadata[0], metadata[1], metadata[2], metadata[4], metadata[5])


def _read_receipt_bytes(
        path: Path, *, limit: int = MAX_RECEIPT_BYTES,
        require_not_writable: bool = False) -> bytes:
    """Read one receipt through an anchored no-follow descriptor walk.

    ``Path.read_text`` would follow a swapped symlink and has no size bound.
    Receipts are independent admission evidence, so a path replacement or an
    oversized document must fail closed rather than being partially parsed.
    """
    try:
        raw_path = os.fspath(path)
    except TypeError as error:
        raise ValueError("receipt path is not filesystem-compatible") from error
    if not raw_path or "\0" in raw_path:
        raise ValueError("receipt path is empty or contains NUL")
    absolute = Path(os.path.abspath(raw_path))
    parts = absolute.parts
    if len(parts) < 2 or absolute == Path("/"):
        raise ValueError("receipt path is invalid")
    directory_flags = (os.O_RDONLY | os.O_CLOEXEC |
                       getattr(os, "O_DIRECTORY", 0) |
                       getattr(os, "O_NOFOLLOW", 0))
    file_flags = (os.O_RDONLY | os.O_CLOEXEC |
                  getattr(os, "O_NOFOLLOW", 0))
    descriptors: list[int] = []
    components: list[tuple[str, tuple[int, ...]]] = []
    file_descriptor = -1
    try:
        parent = os.open("/", directory_flags)
        descriptors.append(parent)
        for component in parts[1:-1]:
            before = os.stat(component, dir_fd=parent, follow_symlinks=False)
            if (not stat.S_ISDIR(before.st_mode) or
                    stat.S_ISLNK(before.st_mode)):
                raise ValueError("receipt path ancestor is a symlink/non-directory")
            child = os.open(component, directory_flags, dir_fd=parent)
            descriptors.append(child)
            opened = os.fstat(child)
            if _receipt_metadata(before)[:6] != _receipt_metadata(opened)[:6]:
                raise ValueError("receipt path ancestor changed while opening")
            components.append((component, _receipt_metadata(before)[:6]))
            parent = child
        before = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
        if (stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode) or
                before.st_nlink != 1 or before.st_size < 2 or
                before.st_size > limit or
                (require_not_writable and before.st_mode & 0o022)):
            raise ValueError("receipt is not a bounded single-link regular file")
        file_descriptor = os.open(parts[-1], file_flags, dir_fd=parent)
        opened = os.fstat(file_descriptor)
        if _receipt_metadata(before) != _receipt_metadata(opened):
            raise ValueError("receipt changed while opening")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(file_descriptor, min(64 * 1024, limit + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > limit:
                raise ValueError("receipt exceeds size limit")
        after_descriptor = os.fstat(file_descriptor)
        after_path = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
        if (_receipt_metadata(opened) != _receipt_metadata(after_descriptor) or
                _receipt_metadata(after_descriptor) != _receipt_metadata(after_path) or
                total != opened.st_size):
            raise ValueError("receipt changed during read")
        for index, (component, expected) in enumerate(components):
            current = os.stat(component, dir_fd=descriptors[index],
                              follow_symlinks=False)
            if _receipt_metadata(current)[:6] != expected:
                raise ValueError("receipt path ancestor changed during read")
        return b"".join(chunks)
    finally:
        if file_descriptor >= 0:
            os.close(file_descriptor)
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass


def _trusted_receipt_file(path: Path) -> tuple[bool, str]:
    """Require a paper-phase receipt to come from the root trust domain.

    The paper phase is normally run by the root custodian.  A user-owned JSON
    file, even with a valid self-sealed body, is only a test fixture and must
    not be accepted as an admission input.  Walk the lexical parent chain
    without following symlinks and require root:root 0600 for the file.
    """
    try:
        raw = os.fspath(path)
        if not raw or "\0" in raw:
            return False, "receipt path is empty or contains NUL"
        absolute = Path(os.path.abspath(raw))
        if absolute == Path("/") or absolute.resolve(strict=True) != absolute:
            return False, "receipt path is not canonical"
        current = Path(absolute.anchor)
        for component in absolute.parts[1:-1]:
            current /= component
            metadata = os.lstat(current)
            if (not stat.S_ISDIR(metadata.st_mode) or
                    stat.S_ISLNK(metadata.st_mode) or
                    metadata.st_uid != 0 or metadata.st_gid != 0 or
                    stat.S_IMODE(metadata.st_mode) & 0o022 or
                    stat.S_IMODE(metadata.st_mode) & 0o7000):
                return False, "receipt parent is not a protected root directory"
        metadata = os.lstat(absolute)
    except FileNotFoundError:
        return False, "receipt is missing"
    except OSError as error:
        return False, f"receipt path is unavailable: {error}"
    if (not stat.S_ISREG(metadata.st_mode) or
            stat.S_ISLNK(metadata.st_mode) or metadata.st_nlink != 1 or
            metadata.st_uid != 0 or metadata.st_gid != 0 or
            stat.S_IMODE(metadata.st_mode) != 0o600):
        return False, "receipt must be a root-owned single-link 0600 file"
    return True, ""


def _safe_write_json(path: Path, value: Any) -> None:
    """Publish a summary through anchored ``openat``/atomic-rename calls.

    Release summaries are evidence.  Never use ``Path.write_text`` (or open a
    caller-supplied absolute path with ``O_TRUNC``): a same-UID race can swap a
    parent directory or a hard link after a lexical check and make a privileged
    checker truncate an unrelated file.  We hold every parent directory
    descriptor opened with ``O_NOFOLLOW``, write a fresh private temporary file
    with ``O_EXCL``, then atomically replace the requested leaf from that held
    descriptor.  Thus a replacement race can at worst make this publication
    fail; it cannot redirect bytes outside the checked directory.
    """
    raw = os.fspath(path)
    if not raw or "\0" in raw:
        raise ValueError("summary path is empty or contains NUL")
    absolute = Path(os.path.abspath(raw))
    if absolute == Path("/"):
        raise ValueError("summary path is invalid")
    payload = _canonical_json(value)
    parts = absolute.parts
    if len(parts) < 2:
        raise ValueError("summary path is invalid")
    directory_flags = (os.O_RDONLY | os.O_CLOEXEC |
                       getattr(os, "O_DIRECTORY", 0) |
                       getattr(os, "O_NOFOLLOW", 0))
    descriptors: list[int] = []
    parent_records: list[tuple[int, str, tuple[int, ...]]] = []
    parent_fd = -1
    temporary_name: str | None = None
    temporary_fd = -1
    try:
        parent_fd = os.open("/", directory_flags)
        descriptors.append(parent_fd)
        # Resolve and hold the parent one component at a time.  Missing
        # components are created relative to the held descriptor, never via a
        # second absolute-path lookup.
        for component in parts[1:-1]:
            try:
                before = os.stat(component, dir_fd=parent_fd,
                                 follow_symlinks=False)
            except FileNotFoundError:
                try:
                    os.mkdir(component, 0o700, dir_fd=parent_fd)
                except FileExistsError:
                    pass
                before = os.stat(component, dir_fd=parent_fd,
                                 follow_symlinks=False)
            if (not stat.S_ISDIR(before.st_mode) or
                    stat.S_ISLNK(before.st_mode)):
                raise ValueError("summary parent component is not a directory")
            child = os.open(component, directory_flags, dir_fd=parent_fd)
            opened = os.fstat(child)
            if _directory_identity(before) != _directory_identity(opened):
                os.close(child)
                raise ValueError("summary parent changed while opening")
            parent_records.append((
                parent_fd, component, _directory_identity(opened)))
            descriptors.append(child)
            parent_fd = child

        parent_metadata = os.fstat(parent_fd)
        if (not stat.S_ISDIR(parent_metadata.st_mode) or
                parent_metadata.st_uid != os.geteuid() or
                stat.S_IMODE(parent_metadata.st_mode) & 0o022 or
                stat.S_IMODE(parent_metadata.st_mode) & 0o7000):
            raise ValueError("summary parent must be caller-owned and protected")

        target_name = parts[-1]
        try:
            target_before = os.stat(target_name, dir_fd=parent_fd,
                                    follow_symlinks=False)
        except FileNotFoundError:
            target_before = None
        if target_before is not None and (
                not stat.S_ISREG(target_before.st_mode) or
                stat.S_ISLNK(target_before.st_mode) or
                target_before.st_nlink != 1 or
                target_before.st_uid != os.geteuid() or
                stat.S_IMODE(target_before.st_mode) & 0o022):
            raise ValueError("summary target is not a safe regular file")

        temporary_flags = (os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                           os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0))
        for _attempt in range(64):
            candidate = (f".{target_name}.tmp-{os.getpid()}-"
                         f"{secrets.token_hex(12)}")
            try:
                temporary_fd = os.open(
                    candidate, temporary_flags, 0o600, dir_fd=parent_fd)
            except FileExistsError:
                continue
            temporary_name = candidate
            break
        if temporary_fd < 0 or temporary_name is None:
            raise ValueError("could not allocate a private summary temporary")
        temporary_metadata = os.fstat(temporary_fd)
        if (not stat.S_ISREG(temporary_metadata.st_mode) or
                temporary_metadata.st_nlink != 1 or
                temporary_metadata.st_uid != os.geteuid() or
                stat.S_IMODE(temporary_metadata.st_mode) & 0o022):
            raise ValueError("summary temporary is not a safe regular file")
        offset = 0
        while offset < len(payload):
            offset += os.write(temporary_fd, payload[offset:])
        os.fchmod(temporary_fd, 0o600)
        os.fsync(temporary_fd)
        # Compare the published leaf with the final on-disk metadata.  The
        # initial O_EXCL mode is subject to the caller's umask (including an
        # unusually restrictive one), while fchmod above establishes the
        # canonical 0600 mode used for evidence.
        temporary_metadata = os.fstat(temporary_fd)

        # Reject a leaf/ancestor replacement observed after the initial
        # snapshot.  Even if a race occurs after this check, renameat remains
        # confined to the held directory and never follows a symlink.
        for descriptor, component, expected in parent_records:
            current = os.stat(component, dir_fd=descriptor,
                              follow_symlinks=False)
            if _directory_identity(current) != expected:
                raise ValueError("summary parent changed before publication")
        try:
            target_now = os.stat(target_name, dir_fd=parent_fd,
                                 follow_symlinks=False)
        except FileNotFoundError:
            target_now = None
        if target_before is None:
            if target_now is not None:
                raise ValueError("summary target appeared during publication")
        elif (target_now is None or
              _receipt_metadata(target_now) !=
              _receipt_metadata(target_before) or
              not stat.S_ISREG(target_now.st_mode) or
              target_now.st_nlink != 1 or
              target_now.st_uid != os.geteuid() or
              stat.S_IMODE(target_now.st_mode) & 0o022):
            raise ValueError("summary target changed before publication")

        os.replace(temporary_name, target_name,
                   src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        temporary_name = None
        published = os.stat(target_name, dir_fd=parent_fd,
                            follow_symlinks=False)
        if (_receipt_metadata(published)[:6] !=
                _receipt_metadata(temporary_metadata)[:6] or
                published.st_size != len(payload)):
            raise ValueError("summary publication identity changed")
        os.fsync(parent_fd)
    finally:
        if temporary_fd >= 0:
            os.close(temporary_fd)
        # Deliberately do not unlink ``temporary_name`` on an exceptional
        # path.  POSIX has no unlinkat operation conditional on inode identity;
        # even stat-at followed by unlink-at has a replacement race.  Leaving
        # one caller-owned 0600 orphan for a separate trusted GC is safer than
        # deleting an entry that a same-UID watcher swapped into this name.
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass


def result(name: str, passed: bool, detail: str, **extra: Any) -> dict[str, Any]:
    item = {"name": name, "pass": bool(passed), "detail": detail}
    if extra:
        item.update(extra)
    return item


def _run(command: list[str], *, timeout: int = 120,
         cwd: Path | None = None) -> tuple[int, str]:
    """Run a child gate and turn launcher failures into a failed check."""
    try:
        completed = subprocess.run(
            command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", timeout=timeout,
            check=False, cwd=str(cwd) if cwd else None,
        )
    except subprocess.TimeoutExpired as error:
        output = error.stdout or ""
        if isinstance(output, bytes):
            output = output.decode("utf-8", "replace")
        return 124, f"timed out after {timeout}s\n{output}"[-8192:]
    except OSError as error:
        return 127, str(error)
    return completed.returncode, completed.stdout[-8192:]


def _resolve_config(root: Path, phase: str, config: Path | None) -> dict[str, Any]:
    profile = "sim" if phase == "dev" else "paper"
    command = [sys.executable, str(root / "scripts" / "resolve_hepta_config.py"),
               "--project-root", str(root), "--profile", profile]
    if config:
        command.extend(["--config", str(config)])
    elif phase == "dev":
        # The resolver's normal auto-order prefers the paper fixture when it
        # exists.  Developer checks must remain offline and therefore pin the
        # simulator template explicitly.
        example = root / "HeptaTrade" / "HeptaTraderConfig.xml.example"
        if example.exists():
            command.extend(["--config", str(example)])
    code, output = _run(command, cwd=root)
    if code:
        raise RuntimeError(f"config/profile lock failed: {output.strip()}")
    return json.loads(output)


def _authority_free(payload: Any, *, allow_paper_authorized: bool = False) -> bool:
    """Return whether a receipt contains no affirmative authority claim.

    ``allow_paper_authorized`` is retained as a source-compatible argument
    for older callers, but canonical phase receipts never use it: a PAPER
    admission *candidate* is explicitly non-authorizing too.  Values are
    checked by exact type/value rather than truthiness so strings such as
    ``"false"`` cannot be confused with the boolean contract.
    """
    if isinstance(payload, dict):
        for key, value in payload.items():
            lowered = key.lower()
            if lowered in _FALSE_AUTHORITY_FIELDS:
                # The compatibility switch cannot weaken the canonical
                # authority-free contract.  It only documents why old callers
                # may still pass the keyword while they migrate.
                if value is not False:
                    return False
            if not _authority_free(value,
                                   allow_paper_authorized=allow_paper_authorized):
                return False
    elif isinstance(payload, list):
        return all(_authority_free(item,
                                   allow_paper_authorized=allow_paper_authorized)
                   for item in payload)
    return True


def _candidate_free(payload: Any) -> bool:
    """Reject a paper-admission candidate marker in inert evidence."""
    if isinstance(payload, dict):
        for key, value in payload.items():
            if (isinstance(key, str) and
                    key.lower() == "paper_test_admission_candidate" and
                    value is not False):
                return False
            if not _candidate_free(value):
                return False
    elif isinstance(payload, list):
        return all(_candidate_free(item) for item in payload)
    return True


def _required_fields(payload: Any, fields: frozenset[str]) -> tuple[bool, str]:
    if not isinstance(payload, dict):
        return False, "receipt root must be a JSON object"
    missing = sorted(fields.difference(payload))
    if missing:
        return False, "missing required fields: " + ",".join(missing)
    return True, ""


def _boundary_has_false_authority(payload: Any) -> bool:
    """Check the explicit boundary flags used by rootful/P1 producers."""
    if not isinstance(payload, dict):
        return False
    boundary = payload.get("boundary")
    if not isinstance(boundary, dict):
        return False
    for key in _FALSE_AUTHORITY_FIELDS:
        if key in boundary and boundary[key] is not False:
            return False
    for key in ("real_broker_connections", "broker_connections",
                "broker_connectors", "broker_protocol_messages",
                "paper_orders", "host_systemd_units_touched",
                "host_network_rules_touched", "host_nft_tables_touched",
                "real_credentials"):
        if key in boundary and (type(boundary[key]) is not int or
                                boundary[key] != 0):
            return False
    return True


def _validate_rootful_receipt(payload: Any) -> tuple[bool, str]:
    valid, detail = _required_fields(payload, ROOTFUL_REQUIRED_FIELDS)
    if not valid:
        return valid, detail
    assert isinstance(payload, dict)
    # Native aggregation is an exact protocol, not a loose receipt family.
    # Reject additional fields (including a forged candidate marker) before
    # handing the document to the producer-owned reconstruction verifier.
    if set(payload) != set(ROOTFUL_REQUIRED_FIELDS):
        return False, "native rootful receipt has unexpected fields"
    if payload.get("schema") != ROOTFUL_REPORT_SCHEMA:
        return False, "exact rootful schema mismatch"
    if payload.get("passed") is not True:
        return False, "rootful receipt is not passed=true"
    if payload.get("certification_level") != \
            ROOTFUL_CERTIFICATION_LEVEL:
        return False, "rootful certification level mismatch"
    boundary = payload.get("boundary")
    if (boundary != ROOTFUL_BOUNDARY or
            not _boundary_has_false_authority(payload) or
            not _candidate_free(payload)):
        return False, "rootful boundary is missing or not the inert native contract"
    # The aggregate contains paths and digests for three independently
    # attested native disposable VMs.  Reconstruct it from those raw reports;
    # accepting only the self-described top-level flags would make a locally
    # forged JSON file sufficient for PAPER admission.
    try:
        native_aggregate = importlib.import_module(
            "aggregate_hepta_execution_native_systemd_gate")
        rebuilt = native_aggregate.verify_runtime_aggregate(payload)
    except Exception as error:  # producer raises a fail-closed AggregateError
        return False, f"native rootful reconstruction failed: {error}"
    if rebuilt != payload:
        return False, "native rootful receipt reconstruction drift"
    return True, "native aggregate reconstructed from all raw variants"


def _validate_p1_receipt(payload: Any) -> tuple[bool, str]:
    valid, detail = _required_fields(payload, P1_REQUIRED_FIELDS)
    if not valid:
        return valid, detail
    assert isinstance(payload, dict)
    if payload.get("schema") != P1_REPORT_SCHEMA:
        return False, "exact P1 schema mismatch"
    if (payload.get("decision") != "GO" or payload.get("passed") is not True or
            payload.get("rehearsal_passed") is not True or
            payload.get("certification_ready") is not True or
            payload.get("certification_blockers") != []):
        return False, "P1 receipt is not a certifying GO"
    if payload.get("scope") != \
            "p1-campaign-coordinator-rootful-liveness-prerequisite-only":
        return False, "P1 scope mismatch"
    if payload.get("production_mode") != \
            "PRODUCTION_REVIEWED_ROOTFUL_CERTIFICATION":
        return False, "P1 production mode mismatch"
    # The P1 liveness receipt is a prerequisite only.  Its candidate and all
    # authority switches must remain explicitly false; unlike the PAPER
    # admission-candidate receipt, P1 is not allowed to advertise even a
    # candidate transition.  Keep this check here (rather than relying solely
    # on the generic recursive authority scan) because
    # ``paper_test_admission_candidate`` is a positive capability marker in
    # the separate candidate contract and is intentionally excluded from
    # that scan.
    p1_authority_fields = (
        "paper_test_admission_candidate", "paper_admission_authorized",
        "paper_authorized", "live_authorized", "mutation_authorized",
        "direct_broker_access", "order_submission_authorized",
    )
    if any(payload.get(key) is not False for key in p1_authority_fields):
        return False, "P1 receipt claims an authority or admission candidate"
    if not _sealed_body_matches(payload):
        return False, "P1 receipt body seal mismatch"
    if not _candidate_free(payload):
        return False, "P1 receipt claims a paper admission candidate"
    if payload.get("boundary") != P1_BOUNDARY:
        return False, "P1 boundary is missing or not the inert v1 contract"
    return True, ""


def _validate_paper_authority_receipt(payload: Any) -> tuple[bool, str]:
    valid, detail = _required_fields(payload, PAPER_AUTHORITY_REQUIRED_FIELDS)
    if not valid:
        return valid, detail
    assert isinstance(payload, dict)
    if payload.get("schema") != PAPER_AUTHORITY_REPORT_SCHEMA:
        return False, "exact PAPER authority schema mismatch"
    if payload.get("version") != 1 or payload.get("status") != "GO":
        return False, "PAPER admission candidate is not status=GO"
    if payload.get("paper_test_admission_candidate") is not True:
        return False, "PAPER admission candidate flag is not true"
    if payload.get("authorization_effect") != \
            "NONE_READ_ONLY_CANDIDATE_ONLY":
        return False, "PAPER receipt has an unexpected authorization effect"
    if payload.get("findings") != []:
        return False, "PAPER admission candidate contains findings"
    if not _sealed_body_matches(payload):
        return False, "PAPER admission candidate body seal mismatch"
    # A GO candidate is evidence for a later, separately authorized action;
    # this phase gate itself must remain authority-free.
    candidate_authority_fields = (
        "paper_authorized", "live_authorized", "mutation_authorized",
        "direct_broker_access", "order_submission_authorized",
    )
    if any(payload.get(key) is not False for key in candidate_authority_fields):
        return False, "PAPER authority receipt claims authority"
    return True, ""


def _validate_p1_receipt_production(payload: Any) -> tuple[bool, str]:
    """Run the producer-owned P1 validator for a paper-phase receipt.

    ``_validate_p1_receipt`` remains intentionally small for offline contract
    fixtures.  The paper phase additionally requires the complete independent
    liveness lineage and exact field set implemented by the producer module.
    """
    valid, detail = _validate_p1_receipt(payload)
    if not valid:
        return valid, detail
    try:
        verifier = importlib.import_module("hepta_p1_paper_admission_verifier")
        verifier.validate_p1_liveness_gate(payload)
    except Exception as error:
        return False, f"producer P1 liveness verification failed: {error}"
    return True, "producer P1 liveness receipt verified"


def _validate_paper_authority_receipt_production(
        payload: Any) -> tuple[bool, str]:
    """Run the producer-owned PAPER candidate validator.

    The verifier module still carries the historical round constant used by
    the installed compatibility layout.  Bind that one expected value for
    this in-process, read-only validation and restore it immediately; no
    authority or mutation API is reachable from this checker.
    """
    valid, detail = _validate_paper_authority_receipt(payload)
    if not valid:
        return valid, detail
    if type(payload.get("round")) is not int or payload["round"] <= 0:
        return False, "PAPER authority receipt round is invalid"
    try:
        verifier = importlib.import_module("hepta_p1_paper_admission_verifier")
        previous_round = verifier.ROUND
        verifier.ROUND = payload["round"]
        try:
            verified = verifier.validate_output_receipt(payload)
        finally:
            verifier.ROUND = previous_round
    except Exception as error:
        return False, f"producer PAPER candidate verification failed: {error}"
    if verified != payload:
        return False, "producer PAPER candidate validator returned drifted receipt"
    return True, "producer PAPER candidate receipt verified"


def _receipt(path: Path, label: str, expected_schema: str,
             *, required_fields: frozenset[str] = frozenset(),
             validator: Any = None,
    allow_paper_authorized: bool = False,
    require_trusted_owner: bool = False) -> dict[str, Any]:
    if require_trusted_owner:
        trusted, trust_detail = _trusted_receipt_file(path)
        if not trusted:
            return result(label, False,
                          f"receipt trust-domain check failed: {trust_detail}",
                          artifacts=[str(path)])
    try:
        payload = json.loads(
            _read_receipt_bytes(
                path, require_not_writable=True).decode("utf-8"),
            object_pairs_hook=_strict_object_pairs,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON value: {value}")),
        )
    except FileNotFoundError:
        return result(label, False, f"missing receipt: {path}", artifacts=[str(path)])
    except (OSError, UnicodeError, ValueError) as error:
        return result(label, False, f"invalid or unsafe JSON receipt: {error}",
                      artifacts=[str(path)])
    schema = str(payload.get("schema", "")) if isinstance(payload, dict) else ""
    required_ok, required_detail = _required_fields(payload, required_fields)
    schema_ok = isinstance(payload, dict) and schema == expected_schema
    safe = _authority_free(payload,
                           allow_paper_authorized=allow_paper_authorized)
    if validator is not None:
        semantic_ok, semantic_detail = validator(payload)
    else:
        # Keep the helper useful to a small, explicitly named future contract
        # without reintroducing the old prefix-based acceptance rule.
        semantic_ok = isinstance(payload, dict) and (
            payload.get("passed") is True or
            payload.get("overall") in {"PASS", "GO"} or
            payload.get("status") in {"PASS", "GO", "CERTIFIED"})
        semantic_detail = "" if semantic_ok else "receipt is not passed"
    # Keep the generic safety check as a final defense even when a validator
    # has already checked the explicit top-level flags.
    ok = schema_ok and required_ok and semantic_ok and safe
    detail_parts = [f"schema={schema or '<missing>'}",
                    f"required={required_ok}", f"authority_free={safe}"]
    if required_detail:
        detail_parts.append(required_detail)
    if semantic_detail:
        detail_parts.append(semantic_detail)
    detail = "; ".join(detail_parts)
    return result(label, ok, detail, artifacts=[str(path)])


def _read_soak_json(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    """Read a CTest-generated soak receipt with a no-follow descriptor."""
    try:
        raw = _read_receipt_bytes(
            path, limit=16 * 1024 * 1024, require_not_writable=True)
        payload = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_strict_object_pairs,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON value: {value}")),
        )
    except FileNotFoundError:
        return None, f"missing execution soak report: {path}"
    except (OSError, UnicodeDecodeError, ValueError) as error:
        return None, f"invalid execution soak report: {error}"
    if not isinstance(payload, dict):
        return None, "execution soak report root must be an object"
    return payload, None


def _is_sha256_digest(value: Any, *, prefixed: bool = False) -> bool:
    """Return whether *value* is an exact lowercase SHA-256 digest string."""
    if not isinstance(value, str):
        return False
    expected_length = 71 if prefixed else 64
    prefix = "sha256:" if prefixed else ""
    if len(value) != expected_length or not value.startswith(prefix):
        return False
    digest = value[len(prefix):]
    return all(character in "0123456789abcdef" for character in digest)


def _protected_file_sha256(path: Path, *, limit: int = 16 * 1024 * 1024) -> str:
    """Hash a receipt through the same bounded, no-follow reader used by gates."""
    return "sha256:" + hashlib.sha256(
        _read_receipt_bytes(
            path, limit=limit, require_not_writable=True)).hexdigest()


def _artifact_is_exact_path(value: Any, expected: Path) -> bool:
    """Check a generated check's artifact list without following symlinks."""
    if not isinstance(value, list) or len(value) != 1:
        return False
    artifact = value[0]
    if not isinstance(artifact, str) or not artifact or "\0" in artifact:
        return False
    try:
        return Path(os.path.abspath(artifact)) == expected
    except (OSError, TypeError, ValueError):
        return False


def _rc_summary_authority_free(value: Any) -> bool:
    """Reject affirmative authority fields at any depth of an RC summary."""
    if isinstance(value, dict):
        for key, nested in value.items():
            if (isinstance(key, str) and
                    key.lower() in AUTHORITY_BOOLEAN_FIELDS and
                    (type(nested) is not bool or nested is not False)):
                return False
            if not _rc_summary_authority_free(nested):
                return False
    elif isinstance(value, list):
        return all(_rc_summary_authority_free(item) for item in value)
    return True


def _validate_rc_summary(
        path: Path, *, expected_config_sha256: Any,
        expected_profile: Any, expected_soak_profile: SoakProfile,
        expected_soak_report: Path | None,
        expected_soak_report_sha256: str | None,
) -> dict[str, Any]:
    """Validate an optional RC-to-PAPER summary hand-off.

    The summary is treated as a protected local receipt, not as a signature
    or a source of authority.  Its useful purpose is continuity: a paper
    invocation can prove that the RC summary refers to the exact config and
    parameterized soak receipt it is validating now.  The current paper gate
    still executes its own config/soak and rootful/P1/admission checks.
    """
    artifacts = [str(path)]
    try:
        raw = _read_receipt_bytes(path, require_not_writable=True)
        payload = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_strict_object_pairs,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON value: {value}")),
        )
    except FileNotFoundError:
        return result("RC_SUMMARY_BINDING", False,
                      f"missing RC summary: {path}", artifacts=artifacts)
    except (OSError, UnicodeError, ValueError) as error:
        return result("RC_SUMMARY_BINDING", False,
                      f"invalid or unsafe RC summary: {error}",
                      artifacts=artifacts)

    if not isinstance(payload, dict):
        return result("RC_SUMMARY_BINDING", False,
                      "RC summary root must be a JSON object",
                      artifacts=artifacts)
    if payload.get("schema") != RELEASE_CHECK_SCHEMA:
        return result("RC_SUMMARY_BINDING", False,
                      "RC summary schema must be hepta.release-check.v1",
                      artifacts=artifacts)
    if payload.get("phase") != "rc":
        return result("RC_SUMMARY_BINDING", False,
                      "RC summary phase must be rc", artifacts=artifacts)
    if payload.get("overall") != "PASS":
        return result("RC_SUMMARY_BINDING", False,
                      "RC summary overall must be PASS", artifacts=artifacts)

    # Require every capability bit in the v1 summary, including the candidate
    # marker that the generic recursive authority scan intentionally permits.
    for field in RC_SUMMARY_AUTHORITY_FIELDS:
        if type(payload.get(field)) is not bool or payload.get(field) is not False:
            return result(
                "RC_SUMMARY_BINDING", False,
                f"RC summary authority field {field} is not boolean false",
                artifacts=artifacts)
    if (not _authority_free(payload) or not _candidate_free(payload) or
            not _rc_summary_authority_free(payload)):
        return result("RC_SUMMARY_BINDING", False,
                      "RC summary contains an affirmative authority claim",
                      artifacts=artifacts)

    # The resolver emits a plain 64-hex config digest.  Requiring exact types
    # avoids Python's bool==int coercion and keeps the binding unambiguous.
    if (not _is_sha256_digest(expected_config_sha256) or
            payload.get("config_sha256") != expected_config_sha256 or
            not _is_sha256_digest(payload.get("config_sha256"))):
        return result("RC_SUMMARY_BINDING", False,
                      "RC summary config digest does not match current config",
                      artifacts=artifacts)
    if payload.get("profile") != expected_profile:
        return result("RC_SUMMARY_BINDING", False,
                      "RC summary config profile does not match current profile",
                      artifacts=artifacts)
    if payload.get("soak_profile") != expected_soak_profile.name:
        return result("RC_SUMMARY_BINDING", False,
                      "RC summary soak profile does not match current profile",
                      artifacts=artifacts)
    if (type(payload.get("requested_rounds")) is not int or
            payload.get("requested_rounds") != expected_soak_profile.rounds):
        return result("RC_SUMMARY_BINDING", False,
                      "RC summary requested rounds do not match soak profile",
                      artifacts=artifacts)

    if (expected_soak_report is None or
            not isinstance(expected_soak_report_sha256, str) or
            not _is_sha256_digest(expected_soak_report_sha256, prefixed=True)):
        return result("RC_SUMMARY_BINDING", False,
                      "current validated soak artifact is unavailable",
                      artifacts=artifacts)
    expected_report = Path(os.path.abspath(os.fspath(expected_soak_report)))
    claimed_report = payload.get("soak_report")
    try:
        claimed_report_path = (
            Path(os.path.abspath(claimed_report))
            if isinstance(claimed_report, str) else None)
    except (OSError, TypeError, ValueError):
        claimed_report_path = None
    if claimed_report_path != expected_report:
        return result("RC_SUMMARY_BINDING", False,
                      "RC summary soak artifact path does not match current artifact",
                      artifacts=artifacts + [str(expected_report)])
    if payload.get("soak_report_sha256") != expected_soak_report_sha256:
        return result("RC_SUMMARY_BINDING", False,
                      "RC summary soak artifact digest does not match current artifact",
                      artifacts=artifacts + [str(expected_report)])

    checks = payload.get("checks")
    if not isinstance(checks, list) or not checks:
        return result("RC_SUMMARY_BINDING", False,
                      "RC summary checks must be a non-empty list",
                      artifacts=artifacts)
    names: list[str] = []
    for item in checks:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            return result("RC_SUMMARY_BINDING", False,
                          "RC summary contains a malformed check",
                          artifacts=artifacts)
        names.append(item["name"])
        if item.get("pass") is not True:
            return result("RC_SUMMARY_BINDING", False,
                          "RC summary contains a failed check",
                          artifacts=artifacts)
    if len(names) != len(set(names)):
        return result("RC_SUMMARY_BINDING", False,
                      "RC summary contains duplicate check names",
                      artifacts=artifacts)
    missing = [name for name in RC_SUMMARY_REQUIRED_CHECKS
               if names.count(name) != 1]
    if missing:
        return result("RC_SUMMARY_BINDING", False,
                      "RC summary is missing required checks: " +
                      ",".join(missing), artifacts=artifacts)
    soak_checks = [item for item in checks
                   if item.get("name") == "EXECUTION_GATEWAY_SOAK"]
    if not _artifact_is_exact_path(soak_checks[0].get("artifacts"), expected_report):
        return result("RC_SUMMARY_BINDING", False,
                      "RC summary soak check is not bound to current artifact",
                      artifacts=artifacts + [str(expected_report)])
    if soak_checks[0].get("soak_report_sha256") != expected_soak_report_sha256:
        return result("RC_SUMMARY_BINDING", False,
                      "RC summary soak check digest is internally inconsistent",
                      artifacts=artifacts + [str(expected_report)])
    return result(
        "RC_SUMMARY_BINDING", True,
        "RC summary is PASS, authority-free, and bound to current config/soak",
        artifacts=artifacts + [str(expected_report)])


def _soak_receipt(
        path: Path, build_dir: Path, profile: SoakProfile, root: Path,
) -> dict[str, Any]:
    """Consume the one parameterized soak report already produced by CTest."""
    try:
        build = build_dir.resolve(strict=True)
        # Compare the lexical report parent before opening it.  Resolving the
        # report itself would follow a symlink and defeat the no-follow
        # contract enforced by _read_receipt_bytes.
        report = Path(os.path.abspath(os.fspath(path)))
    except (OSError, RuntimeError, TypeError) as error:
        return result(
            "EXECUTION_GATEWAY_SOAK", False,
            f"soak report/build path is unavailable: {error}",
            artifacts=[str(path)])
    try:
        build_lexical = Path(os.path.abspath(os.fspath(build_dir)))
        build_metadata = build_lexical.lstat()
    except (OSError, TypeError) as error:
        return result(
            "EXECUTION_GATEWAY_SOAK", False,
            f"soak build directory is unavailable: {error}",
            artifacts=[str(path)])
    if (stat.S_ISLNK(build_metadata.st_mode) or
            not stat.S_ISDIR(build_metadata.st_mode) or
            build_metadata.st_mode & 0o022):
        return result(
            "EXECUTION_GATEWAY_SOAK", False,
            "soak build directory must be a protected non-symlink directory",
            artifacts=[str(path)])
    if build != build_lexical:
        return result(
            "EXECUTION_GATEWAY_SOAK", False,
            "soak build directory resolves through an unsafe ancestor",
            artifacts=[str(path)])
    if report.parent != build:
        return result(
            "EXECUTION_GATEWAY_SOAK", False,
            "soak report must be a protected direct child of build-dir",
            artifacts=[str(path)])
    # Bind the receipt to the CMake invocation that produced the binaries.
    # Without this check a copied eight-round JSON could be paired with a
    # short/default build and still look like a release result.
    cache_path = build / "CMakeCache.txt"
    try:
        cache_text = _read_receipt_bytes(
            cache_path, limit=2 * 1024 * 1024,
            require_not_writable=True).decode("utf-8")
    except (OSError, UnicodeDecodeError, ValueError) as error:
        return result(
            "EXECUTION_GATEWAY_SOAK", False,
            f"CMakeCache.txt is missing or unsafe: {error}",
            artifacts=[str(path), str(cache_path)])

    cache_values: dict[str, str] = {}
    for line in cache_text.splitlines():
        if "=" not in line or line.startswith("//") or line.startswith("#"):
            continue
        key, value = line.split("=", 1)
        if ":" in key:
            name, _kind = key.split(":", 1)
        else:
            name = key
        if name in {"HEPTA_SOAK_PROFILE", "CMAKE_BUILD_TYPE"}:
            cache_values[name] = value
    if (cache_values.get("HEPTA_SOAK_PROFILE") != profile.name or
            cache_values.get("CMAKE_BUILD_TYPE") != "Release"):
        return result(
            "EXECUTION_GATEWAY_SOAK", False,
            "soak receipt does not match Release CMake profile/build type",
            artifacts=[str(path), str(cache_path)])
    payload, error = _read_soak_json(path)
    if error is not None:
        return result(
            "EXECUTION_GATEWAY_SOAK", False, error, artifacts=[str(path)])
    assert payload is not None
    # Bind the receipt to the exact source/build/binary inputs present at
    # verification time. A report copied from another build must not pass
    # merely because the destination CMake cache has the same profile. The
    # soak runner records a descriptor-stable pre/post snapshot; recompute the
    # same snapshot here and require an exact match.
    provenance = payload.get("provenance")
    if not isinstance(provenance, dict):
        return result(
            "EXECUTION_GATEWAY_SOAK", False,
            "soak provenance is missing or malformed", artifacts=[str(path)])
    pre_run = provenance.get("pre_run")
    post_run = provenance.get("post_run")
    if (not isinstance(pre_run, dict) or not isinstance(post_run, dict) or
            provenance.get("inputs_stable") is not True or
            pre_run != post_run):
        return result(
            "EXECUTION_GATEWAY_SOAK", False,
            "soak provenance is not a stable pre/post snapshot",
            artifacts=[str(path)])
    try:
        build_tree = soak_runner.build_location(root, str(build))
        current_snapshot = soak_runner.input_snapshot(
            root, build_tree, list(soak_runner.SOAK_BINARY_NAMES), "Release")
    except (OSError, RuntimeError, ValueError, KeyError) as snapshot_error:
        return result(
            "EXECUTION_GATEWAY_SOAK", False,
            f"cannot recompute soak input binding: {snapshot_error}",
            artifacts=[str(path)])
    if (payload.get("build_dir") != build_tree.logical or
            pre_run != current_snapshot or
            payload.get("binary_inputs") != pre_run.get("binaries") or
            payload.get("git_head") != pre_run.get("git_head") or
            provenance.get("source_binary_binding") !=
                soak_runner.SOAK_SOURCE_BINARY_BINDING):
        return result(
            "EXECUTION_GATEWAY_SOAK", False,
            "soak report is not bound to the current source/build/binaries",
            artifacts=[str(path)])
    expected_processes = {
        name: soak_runner.SOAK_MINIMUM_OBSERVED_PROCESSES[name]
        for name in soak_runner.SOAK_BINARY_NAMES
    }
    if (not _authority_free(payload) or
            payload.get("expected_invariants_per_round") !=
                soak_runner.SOAK_EXPECTED_INVARIANTS or
            payload.get("evidence_contracts") !=
                list(soak_runner.SOAK_EVIDENCE_CONTRACTS) or
            payload.get("minimum_observed_processes") != expected_processes):
        return result(
            "EXECUTION_GATEWAY_SOAK", False,
            "soak report contract/invariant matrix does not match the runner",
            artifacts=[str(path)])
    if not _candidate_free(payload):
        return result(
            "EXECUTION_GATEWAY_SOAK", False,
            "soak report claims a paper admission candidate",
            artifacts=[str(path)])
    rounds = payload.get("rounds")
    rounds_ok = (
        payload.get("requested_rounds") == profile.rounds and
        payload.get("completed_rounds") == profile.rounds and
        isinstance(rounds, list) and len(rounds) == profile.rounds and
        all(isinstance(item, dict) and item.get("passed") is True
            for item in rounds)
    )
    passed = (
        payload.get("passed") is True and
        payload.get("all_invariants_certified") is True and
        isinstance(provenance, dict) and
        provenance.get("inputs_stable") is True
    )
    schema_ok = payload.get("schema") == "hepta.execution-gateway-soak.v11"
    profile_ok = payload.get("soak_profile") == profile.name
    ok = schema_ok and profile_ok and rounds_ok and passed
    detail = (
        f"schema={payload.get('schema', '<missing>')}; "
        f"profile={payload.get('soak_profile', '<missing>')}; "
        f"rounds={payload.get('completed_rounds', '<missing>')}"
        f"/{payload.get('requested_rounds', '<missing>')}; "
        f"passed={payload.get('passed') is True}; "
        f"invariants={payload.get('all_invariants_certified') is True}; "
        f"inputs_stable={isinstance(provenance, dict) and provenance.get('inputs_stable') is True}"
    )
    return result(
        "EXECUTION_GATEWAY_SOAK", ok, detail, artifacts=[str(path)])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hepta release check",
        description="Run the native/Linux dev, rc, or paper release phase.",
    )
    parser.add_argument("--root", type=Path,
                        default=Path(__file__).resolve().parents[1])
    parser.add_argument("--phase", choices=("dev", "rc", "paper"),
                        default="dev")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--build-dir", type=Path,
                        help="CMake build tree containing the CTest soak receipt")
    parser.add_argument("--soak-report", type=Path,
                        help="existing CTest soak receipt (direct build child)")
    parser.add_argument("--soak-profile", choices=("pr-smoke", "release", "nightly"),
                        default=None)
    parser.add_argument(
        "--rc-report", type=Path,
        help=("optional protected PASS summary from the preceding rc phase "
              "(paper only; must bind the current config and soak receipt)"),
    )
    parser.add_argument("--report", type=Path,
                        help="summary JSON path (default: runtime-logs/...)")
    parser.add_argument("--rootful-report", type=Path,
                        help="independently produced rootful/systemd receipt (paper)")
    parser.add_argument("--p1-report", type=Path,
                        help="independently produced P1 liveness receipt (paper)")
    parser.add_argument("--authority-report", type=Path,
                        help="independently produced PAPER authority receipt (paper)")
    return parser


def run(args: argparse.Namespace) -> int:
    root = args.root.resolve(strict=True)
    checks: list[dict[str, Any]] = []
    soak_report_path: Path | None = None
    soak_report_sha256: str | None = None
    try:
        resolved = _resolve_config(root, args.phase, args.config)
        checks.append(result("CONFIG_PROFILE_LOCK", True,
                             f"profile={resolved['profile']}; sha256={resolved['sha256']}",
                             artifacts=[resolved["config_path"]]))
    except (RuntimeError, KeyError) as error:
        resolved = {}
        checks.append(result("CONFIG_PROFILE_LOCK", False, str(error)))

    # One and only one static invocation; callers consume this summary.
    code, output = _run(
        [sys.executable, "-m", "compileall", "-q", str(root / "scripts")],
        cwd=root,
    )
    checks.append(result("STATIC_CHECKS", code == 0,
                         "python compileall passed (single invocation)" if code == 0
                         else f"python compileall exitCode={code}: {output.strip()}"))

    profile_name = args.soak_profile or ("pr-smoke" if args.phase == "dev" else "release")
    soak = SoakProfile.resolve(profile_name)
    soak_for_phase = True
    if args.phase in {"rc", "paper"}:
        # A short PR smoke is useful feedback, but it is never a promotion
        # input. Keep this rejection in a distinct check so the report has no
        # duplicate EXECUTION_GATEWAY_SOAK entries when a caller supplies a
        # short profile together with a build tree.
        profile_allowed = profile_name in PROMOTION_SOAK_PROFILES
        checks.append(result(
            "SOAK_PROFILE_POLICY", profile_allowed,
            (f"promotion profile={profile_name}; eight-round evidence required"
             if profile_allowed else
             "pr-smoke is diagnostic only; rc/paper require release or nightly"),
        ))
        soak_for_phase = profile_allowed
    if args.phase in {"rc", "paper"}:
        if not args.build_dir:
            checks.append(result("EXECUTION_GATEWAY_SOAK", False,
                                 "--build-dir is required for rc/paper phases; "
                                 "run the parameterized CTest soak first"))
        else:
            build_dir = (args.build_dir if args.build_dir.is_absolute()
                         else root / args.build_dir)
            report_path = args.soak_report
            if report_path is None:
                # Keep the CTest report name stable for existing evidence
                # consumers.  No fallback runner is invoked here: a missing
                # report is a failed release check, not permission to rerun a
                # second soak with different parameters.
                report_path = build_dir / "execution-gateway-short-soak.json"
            elif not report_path.is_absolute():
                report_path = root / report_path
            soak_report_path = Path(os.path.abspath(os.fspath(report_path)))
            if soak_for_phase:
                soak_check = _soak_receipt(report_path, build_dir, soak, root)
                if soak_check["pass"]:
                    try:
                        soak_report_sha256 = _protected_file_sha256(report_path)
                        soak_check["soak_report_sha256"] = soak_report_sha256
                    except (OSError, UnicodeError, ValueError) as error:
                        soak_check["pass"] = False
                        soak_check["detail"] = (
                            str(soak_check.get("detail", "")) +
                            f"; cannot hash soak artifact safely: {error}")
                checks.append(soak_check)

    if args.rc_report is not None and args.phase != "paper":
        checks.append(result(
            "RC_SUMMARY_POLICY", False,
            "--rc-report is only valid for the paper phase",
        ))
    elif args.phase == "paper" and args.rc_report is not None:
        rc_report = (args.rc_report if args.rc_report.is_absolute()
                     else root / args.rc_report)
        checks.append(_validate_rc_summary(
            Path(os.path.abspath(os.fspath(rc_report))),
            expected_config_sha256=resolved.get("sha256"),
            expected_profile=resolved.get("profile"),
            expected_soak_profile=soak,
            expected_soak_report=soak_report_path,
            expected_soak_report_sha256=soak_report_sha256,
        ))

    if args.phase == "paper":
        receipts = (("ROOTFUL_SYSTEMD_GATE", args.rootful_report,
                     ROOTFUL_REPORT_SCHEMA, ROOTFUL_REQUIRED_FIELDS,
                     _validate_rootful_receipt),
                    ("P1_LIVENESS_GATE", args.p1_report,
                     P1_REPORT_SCHEMA, P1_REQUIRED_FIELDS,
                     _validate_p1_receipt_production),
                    ("PAPER_AUTHORITY_GATE", args.authority_report,
                     PAPER_AUTHORITY_REPORT_SCHEMA,
                     PAPER_AUTHORITY_REQUIRED_FIELDS,
                     _validate_paper_authority_receipt_production))
        for name, path, schema, required_fields, validator in receipts:
            if path is None:
                checks.append(result(name, False,
                                     "independent receipt is required; this command never grants authority"))
            else:
                checks.append(_receipt(
                    path, name, schema, required_fields=required_fields,
                    validator=validator,
                    require_trusted_owner=True,
                ))

    failed = [item for item in checks if not item["pass"]]
    summary = {
        "schema": RELEASE_CHECK_SCHEMA,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "phase": args.phase,
        "soak_profile": soak.name,
        "requested_rounds": soak.rounds,
        "profile": resolved.get("profile"),
        "config_sha256": resolved.get("sha256"),
        "soak_report": (str(soak_report_path)
                         if soak_report_path is not None else None),
        "soak_report_sha256": soak_report_sha256,
        "overall": "PASS" if not failed else "FAIL",
        "authority_granted": False,
        "paper_test_admission_candidate": False,
        "paper_admission_authorized": False,
        "paper_authorized": False,
        "live_authorized": False,
        "mutation_authorized": False,
        "direct_broker_access": False,
        "order_submission_authorized": False,
        "checks": checks,
    }
    # Keep the implicit developer report in a directory this command can
    # create owner-private.  The legacy ``runtime-logs`` tree is often shared
    # (0775) by older workflows and is intentionally not trusted as a receipt
    # sink; callers that need a different location must provide an explicit,
    # protected ``--report`` path.
    report = args.report or (root / DEFAULT_REPORT_DIRECTORY /
                             f"release-check-linux-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json")
    report = report if report.is_absolute() else root / report
    try:
        _safe_write_json(report, summary)
    except (OSError, ValueError, TypeError) as error:
        # Do not fall back to an unsafe path or silently discard the evidence.
        # A caller can retry with an owner-private report directory.
        print(f"release check summary write failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(summary, sort_keys=True))
    print(f"SUMMARY_JSON={report}")
    return 0 if not failed else 1


def main(argv: list[str] | None = None) -> int:
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
