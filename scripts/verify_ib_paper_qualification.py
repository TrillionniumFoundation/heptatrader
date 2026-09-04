#!/usr/bin/env python3
"""Verify a controlled IB PAPER qualification evidence set fail-closed."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import tempfile
import time
from typing import Any

SCHEMA = "hepta.ib-paper-qualification.v1"
RECEIPT_SCHEMA = "hepta.ib-paper-qualification-verification.v1"
MAX_QUALIFICATION_DURATION_MS = 6 * 60 * 60 * 1000
MAX_EVIDENCE_FILE_BYTES = 512 * 1024 * 1024
MAX_RESULT_BYTES = 4 * 1024 * 1024
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
FINGERPRINT = re.compile(r"^sha256:[0-9a-f]{64}$")
TOKEN = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
SESSION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")

REQUIRED_SCENARIOS = (
    "connect_authoritative_snapshot",
    "disconnect_reconnect",
    "partial_fill",
    "duplicate_out_of_order_status",
    "broker_reject",
    "stale_quote",
    "outcome_uncertain",
    "cancel_race",
    "reconcile_divergence",
    "lease_fencing",
    "kill_switch",
    "terminal_recovery",
)

REQUIRED_ASSERTIONS = {
    "connect_authoritative_snapshot": frozenset(
        {
            "IB_CONNECTION_READY",
            "AUTHORITATIVE_ACCOUNT_COMPLETE",
            "AUTHORITATIVE_POSITIONS_COMPLETE",
            "AUTHORITATIVE_OPEN_ORDERS_COMPLETE",
        }
    ),
    "disconnect_reconnect": frozenset(
        {
            "DISCONNECT_OBSERVED",
            "RISK_INCREASE_BLOCKED_DURING_DISCONNECT",
            "NEW_CONNECTION_EPOCH",
            "RECONCILIATION_COMPLETED",
        }
    ),
    "partial_fill": frozenset(
        {
            "PARTIAL_FILL_OBSERVED",
            "REMAINING_QUANTITY_RECONCILED",
            "FINAL_TERMINAL_STATUS",
        }
    ),
    "duplicate_out_of_order_status": frozenset(
        {
            "DUPLICATE_EVENT_IDEMPOTENT",
            "OUT_OF_ORDER_EVENT_REJECTED_OR_RECONCILED",
            "PROJECTION_CONVERGED",
        }
    ),
    "broker_reject": frozenset(
        {
            "BROKER_REJECT_OBSERVED",
            "REJECT_DETAILS_DURABLE",
            "ORDER_NOT_ACTIVE",
        }
    ),
    "stale_quote": frozenset(
        {
            "STALE_QUOTE_REJECTED",
            "NO_PLACE_SEND_ATTEMPT",
        }
    ),
    "outcome_uncertain": frozenset(
        {
            "OUTCOME_UNCERTAIN_DURABLE",
            "NO_BLIND_RETRY",
            "AUTHORITATIVE_RESOLUTION",
        }
    ),
    "cancel_race": frozenset(
        {
            "CANCEL_RACE_OBSERVED",
            "TERMINAL_STATE_CONVERGED",
            "NO_DUPLICATE_CANCEL_EFFECT",
        }
    ),
    "reconcile_divergence": frozenset(
        {
            "DIVERGENCE_DETECTED",
            "RISK_INCREASE_BLOCKED",
            "RECONCILIATION_RESOLVED_OR_TERMINAL",
        }
    ),
    "lease_fencing": frozenset(
        {
            "STALE_LEASE_REJECTED",
            "ACTIVE_LEASE_ACCEPTED",
            "NO_STALE_VENUE_SEND",
        }
    ),
    "kill_switch": frozenset(
        {
            "KILL_SWITCH_ENGAGED",
            "RISK_INCREASE_BLOCKED",
            "EXIT_PATH_REMAINS_BOUNDED",
        }
    ),
    "terminal_recovery": frozenset(
        {
            "PROCESS_RESTART_OBSERVED",
            "JOURNAL_REPLAY_COMPLETE",
            "BROKER_STATE_RECONCILED",
            "NO_DUPLICATE_RISK_INCREASE",
        }
    ),
}

REQUIRED_EVIDENCE_KINDS = {
    "connect_authoritative_snapshot": frozenset(
        {"broker-callbacks", "authoritative-snapshot"}
    ),
    "disconnect_reconnect": frozenset({"broker-callbacks", "oms-journal"}),
    "partial_fill": frozenset({"broker-callbacks", "oms-journal"}),
    "duplicate_out_of_order_status": frozenset(
        {"broker-callbacks", "oms-journal"}
    ),
    "broker_reject": frozenset({"broker-callbacks", "oms-journal"}),
    "stale_quote": frozenset({"execution-events", "oms-journal"}),
    "outcome_uncertain": frozenset({"broker-callbacks", "oms-journal"}),
    "cancel_race": frozenset({"broker-callbacks", "oms-journal"}),
    "reconcile_divergence": frozenset(
        {"authoritative-snapshot", "oms-journal"}
    ),
    "lease_fencing": frozenset({"execution-events", "oms-journal"}),
    "kill_switch": frozenset({"execution-events", "oms-journal"}),
    "terminal_recovery": frozenset({"broker-callbacks", "oms-journal"}),
}

TOP_LEVEL_KEYS = frozenset(
    {
        "schema",
        "qualified",
        "mode",
        "git_sha",
        "binary",
        "harness",
        "broker",
        "started_at_ms",
        "completed_at_ms",
        "scenarios",
    }
)
BINARY_KEYS = frozenset({"name", "sha256"})
HARNESS_KEYS = frozenset({"name", "sha256"})
BROKER_KEYS = frozenset(
    {
        "venue",
        "environment",
        "transport",
        "api_version",
        "session_id",
        "account_fingerprint",
        "host_fingerprint",
        "origin",
        "simulated",
        "test_double",
    }
)
SCENARIO_KEYS = frozenset(
    {"id", "status", "started_at_ms", "completed_at_ms", "assertions", "evidence"}
)
EVIDENCE_KEYS = frozenset({"path", "kind", "sha256", "size"})


class QualificationError(ValueError):
    pass


def reject_constant(value: str) -> None:
    raise QualificationError(f"non-finite JSON number is forbidden: {value}")


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise QualificationError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def parse_json(data: bytes, label: str) -> dict[str, Any]:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise QualificationError(f"{label} is not UTF-8: {error}") from error
    try:
        payload = json.loads(
            text,
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
        )
    except (json.JSONDecodeError, QualificationError) as error:
        raise QualificationError(f"invalid {label}: {error}") from error
    if not isinstance(payload, dict):
        raise QualificationError(f"{label} must contain a JSON object")
    ensure_finite_numbers(payload, label)
    return payload


def ensure_finite_numbers(value: Any, label: str) -> None:
    if isinstance(value, bool) or value is None or isinstance(value, (str, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise QualificationError(f"{label} contains a non-finite number")
        return
    if isinstance(value, list):
        for item in value:
            ensure_finite_numbers(item, label)
        return
    if isinstance(value, dict):
        for item in value.values():
            ensure_finite_numbers(item, label)
        return
    raise QualificationError(f"{label} contains an unsupported JSON value")


def exact_keys(value: Any, keys: frozenset[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise QualificationError(f"{label} must be an object")
    actual = frozenset(value)
    if actual != keys:
        missing = sorted(keys - actual)
        unknown = sorted(actual - keys)
        raise QualificationError(
            f"{label} keys mismatch; missing={missing}, unknown={unknown}"
        )
    return value


def integer(value: Any, label: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise QualificationError(f"{label} must be an integer >= {minimum}")
    return value


def required_string(
    value: Any, label: str, maximum: int = 256, pattern: re.Pattern[str] | None = None
) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise QualificationError(f"{label} must be a non-empty string <= {maximum} bytes")
    if not value.isascii():
        raise QualificationError(f"{label} must be ASCII")
    if pattern is not None and pattern.fullmatch(value) is None:
        raise QualificationError(f"{label} is not canonical: {value!r}")
    return value


def stable_regular_bytes(
    path: Path,
    *,
    label: str,
    maximum: int,
    allowed_owners: frozenset[int],
    require_single_link: bool = True,
) -> tuple[bytes, os.stat_result]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise QualificationError(f"unable to open {label} safely: {path}: {error}") from error
    try:
        before = os.fstat(descriptor)
        mode = stat.S_IMODE(before.st_mode)
        if not stat.S_ISREG(before.st_mode):
            raise QualificationError(f"{label} is not a regular file: {path}")
        if before.st_uid not in allowed_owners:
            raise QualificationError(f"{label} has an untrusted owner: {path}")
        if mode & 0o022:
            raise QualificationError(
                f"{label} is group/world writable: {path} mode={mode:04o}"
            )
        if require_single_link and before.st_nlink != 1:
            raise QualificationError(
                f"{label} must have exactly one hard link: {path} links={before.st_nlink}"
            )
        if before.st_size < 0 or before.st_size > maximum:
            raise QualificationError(
                f"{label} exceeds the bounded size: {path} size={before.st_size}"
            )
        chunks: list[bytes] = []
        remaining = before.st_size
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                raise QualificationError(f"{label} changed or truncated during read: {path}")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise QualificationError(f"{label} grew during read: {path}")
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)

    fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
    if any(getattr(before, field) != getattr(after, field) for field in fields):
        raise QualificationError(f"{label} changed during read: {path}")
    try:
        path_after = path.lstat()
    except FileNotFoundError as error:
        raise QualificationError(f"{label} path disappeared during read: {path}") from error
    if path_after.st_dev != after.st_dev or path_after.st_ino != after.st_ino:
        raise QualificationError(f"{label} path was replaced during read: {path}")
    return b"".join(chunks), after


def secure_evidence_root(root: Path) -> tuple[Path, os.stat_result]:
    root = Path(os.path.abspath(os.fspath(root)))
    try:
        metadata = root.lstat()
    except FileNotFoundError as error:
        raise QualificationError(f"evidence root does not exist: {root}") from error
    mode = stat.S_IMODE(metadata.st_mode)
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise QualificationError("evidence root must be a non-symlink directory")
    if metadata.st_uid != os.geteuid():
        raise QualificationError("evidence root must be owned by the verifier user")
    if mode != 0o700:
        raise QualificationError(f"evidence root mode must be 0700, got {mode:04o}")
    return root, metadata


def canonical_evidence_path(value: Any, scenario_id: str) -> str:
    raw = required_string(value, "evidence.path", maximum=512)
    if raw.startswith("/") or "\\" in raw:
        raise QualificationError(f"evidence path must be relative POSIX: {raw!r}")
    parsed = PurePosixPath(raw)
    if any(part in ("", ".", "..") for part in parsed.parts):
        raise QualificationError(f"evidence path contains unsafe components: {raw!r}")
    if parsed.as_posix() != raw:
        raise QualificationError(f"evidence path is not canonical: {raw!r}")
    prefix = PurePosixPath("scenarios") / scenario_id
    try:
        parsed.relative_to(prefix)
    except ValueError as error:
        raise QualificationError(
            f"evidence path must be under {prefix.as_posix()}/: {raw!r}"
        ) from error
    if parsed == prefix:
        raise QualificationError("evidence reference must identify a file")
    return raw


def verify_path_components(root: Path, relative: str) -> Path:
    current = root
    parts = PurePosixPath(relative).parts
    for index, part in enumerate(parts):
        current = current / part
        try:
            metadata = current.lstat()
        except FileNotFoundError as error:
            raise QualificationError(f"evidence path is missing: {relative}") from error
        mode = stat.S_IMODE(metadata.st_mode)
        if metadata.st_uid != os.geteuid():
            raise QualificationError(f"evidence path has an untrusted owner: {relative}")
        if mode & 0o022:
            raise QualificationError(
                f"evidence path is group/world writable: {relative} mode={mode:04o}"
            )
        final = index == len(parts) - 1
        if final:
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
                raise QualificationError(
                    f"evidence reference is not a regular non-symlink file: {relative}"
                )
        elif stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise QualificationError(
                f"evidence parent is not a regular non-symlink directory: {relative}"
            )
    return current


def file_digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def verify_tool(path: Path, label: str) -> tuple[str, str]:
    data, metadata = stable_regular_bytes(
        Path(os.path.abspath(os.fspath(path))),
        label=label,
        maximum=2 * 1024 * 1024 * 1024,
        allowed_owners=frozenset({0, os.geteuid()}),
    )
    if stat.S_IMODE(metadata.st_mode) & 0o111 == 0:
        raise QualificationError(f"{label} is not executable: {path}")
    return Path(path).name, file_digest(data)


def validate_result(
    payload: dict[str, Any],
    *,
    root: Path,
    result_path: Path,
    expected_git_sha: str,
    binary_name: str,
    binary_sha256: str,
    harness_name: str,
    harness_sha256: str,
) -> dict[str, Any]:
    exact_keys(payload, TOP_LEVEL_KEYS, "qualification result")
    if payload["schema"] != SCHEMA:
        raise QualificationError(f"unsupported qualification schema: {payload['schema']!r}")
    if payload["qualified"] is not True:
        raise QualificationError("qualification result must explicitly set qualified=true")
    if payload["mode"] != "bounded-mutations":
        raise QualificationError("only bounded-mutations can produce qualification")
    if payload["git_sha"] != expected_git_sha:
        raise QualificationError("qualification git_sha does not match the exact source commit")

    binary = exact_keys(payload["binary"], BINARY_KEYS, "binary")
    if binary["name"] != binary_name or binary["sha256"] != binary_sha256:
        raise QualificationError("qualification binary identity does not match the tested file")
    harness = exact_keys(payload["harness"], HARNESS_KEYS, "harness")
    if harness["name"] != harness_name or harness["sha256"] != harness_sha256:
        raise QualificationError("qualification harness identity does not match the invoked file")

    broker = exact_keys(payload["broker"], BROKER_KEYS, "broker")
    if broker["venue"] != "IB" or broker["environment"] != "PAPER":
        raise QualificationError("qualification must target the IB PAPER environment")
    if broker["transport"] != "TWS_API":
        raise QualificationError("qualification transport must be TWS_API")
    required_string(broker["api_version"], "broker.api_version", maximum=64)
    required_string(broker["session_id"], "broker.session_id", pattern=SESSION_ID)
    required_string(
        broker["account_fingerprint"],
        "broker.account_fingerprint",
        maximum=71,
        pattern=FINGERPRINT,
    )
    required_string(
        broker["host_fingerprint"],
        "broker.host_fingerprint",
        maximum=71,
        pattern=FINGERPRINT,
    )
    if broker["origin"] != "broker-observed":
        raise QualificationError("broker evidence origin must be broker-observed")
    if broker["simulated"] is not False or broker["test_double"] is not False:
        raise QualificationError("simulator or test-double evidence cannot qualify IB PAPER")

    started = integer(payload["started_at_ms"], "started_at_ms", 1)
    completed = integer(payload["completed_at_ms"], "completed_at_ms", 1)
    if completed < started:
        raise QualificationError("qualification completion precedes start")
    if completed - started > MAX_QUALIFICATION_DURATION_MS:
        raise QualificationError("qualification duration exceeds the bounded maximum")

    scenarios = payload["scenarios"]
    if not isinstance(scenarios, list):
        raise QualificationError("scenarios must be an array")
    scenario_ids = [
        item.get("id") if isinstance(item, dict) else None for item in scenarios
    ]
    if tuple(scenario_ids) != REQUIRED_SCENARIOS:
        raise QualificationError(
            "qualification scenarios must exactly match the canonical ordered set"
        )

    referenced_paths: set[str] = set()
    total_evidence_bytes = 0
    scenario_receipts: list[dict[str, Any]] = []
    for index, raw_scenario in enumerate(scenarios):
        scenario_id = REQUIRED_SCENARIOS[index]
        scenario = exact_keys(raw_scenario, SCENARIO_KEYS, f"scenario {scenario_id}")
        if scenario["status"] != "PASS":
            raise QualificationError(f"scenario {scenario_id} did not pass")
        scenario_started = integer(
            scenario["started_at_ms"], f"{scenario_id}.started_at_ms", 1
        )
        scenario_completed = integer(
            scenario["completed_at_ms"], f"{scenario_id}.completed_at_ms", 1
        )
        if not (started <= scenario_started <= scenario_completed <= completed):
            raise QualificationError(
                f"scenario {scenario_id} timestamps escape the qualification window"
            )

        assertions = scenario["assertions"]
        if not isinstance(assertions, list) or any(
            not isinstance(item, str) for item in assertions
        ):
            raise QualificationError(f"scenario {scenario_id} assertions must be strings")
        if len(assertions) != len(set(assertions)):
            raise QualificationError(f"scenario {scenario_id} has duplicate assertions")
        if frozenset(assertions) != REQUIRED_ASSERTIONS[scenario_id]:
            raise QualificationError(
                f"scenario {scenario_id} assertions do not match the required invariant set"
            )

        evidence = scenario["evidence"]
        if not isinstance(evidence, list) or len(evidence) < 2 or len(evidence) > 16:
            raise QualificationError(
                f"scenario {scenario_id} must contain 2-16 evidence references"
            )
        observed_kinds: set[str] = set()
        verified_evidence: list[dict[str, Any]] = []
        for reference_index, raw_reference in enumerate(evidence):
            reference = exact_keys(
                raw_reference,
                EVIDENCE_KEYS,
                f"scenario {scenario_id} evidence {reference_index}",
            )
            relative = canonical_evidence_path(reference["path"], scenario_id)
            if relative in referenced_paths:
                raise QualificationError(f"evidence file is reused: {relative}")
            referenced_paths.add(relative)
            kind = required_string(
                reference["kind"], "evidence.kind", maximum=64, pattern=TOKEN
            )
            observed_kinds.add(kind)
            expected_digest = required_string(
                reference["sha256"], "evidence.sha256", maximum=64, pattern=SHA256
            )
            expected_size = integer(reference["size"], "evidence.size", 1)
            if expected_size > MAX_EVIDENCE_FILE_BYTES:
                raise QualificationError(f"evidence file is too large: {relative}")
            path = verify_path_components(root, relative)
            data, metadata = stable_regular_bytes(
                path,
                label=f"scenario evidence {relative}",
                maximum=MAX_EVIDENCE_FILE_BYTES,
                allowed_owners=frozenset({os.geteuid()}),
            )
            actual_digest = file_digest(data)
            if metadata.st_size != expected_size or actual_digest != expected_digest:
                raise QualificationError(f"evidence identity mismatch: {relative}")
            total_evidence_bytes += metadata.st_size
            verified_evidence.append(
                {
                    "path": relative,
                    "kind": kind,
                    "sha256": actual_digest,
                    "size": metadata.st_size,
                }
            )
        missing_kinds = REQUIRED_EVIDENCE_KINDS[scenario_id] - observed_kinds
        if missing_kinds:
            raise QualificationError(
                f"scenario {scenario_id} lacks required evidence kinds: {sorted(missing_kinds)}"
            )
        scenario_receipts.append(
            {
                "id": scenario_id,
                "status": "PASS",
                "evidence": verified_evidence,
            }
        )

    allowed_files = referenced_paths | {
        result_path.relative_to(root).as_posix(),
        "qualification-verification.json",
    }
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix()
        metadata = path.lstat()
        mode = stat.S_IMODE(metadata.st_mode)
        if metadata.st_uid != os.geteuid():
            raise QualificationError(f"untrusted owner in evidence tree: {relative}")
        if mode & 0o022:
            raise QualificationError(
                f"group/world writable evidence tree entry: {relative} mode={mode:04o}"
            )
        if stat.S_ISLNK(metadata.st_mode):
            raise QualificationError(f"symlink in evidence tree: {relative}")
        if stat.S_ISDIR(metadata.st_mode):
            continue
        if not stat.S_ISREG(metadata.st_mode):
            raise QualificationError(f"special file in evidence tree: {relative}")
        if relative not in allowed_files:
            raise QualificationError(f"unreferenced evidence file: {relative}")

    return {
        "schema": RECEIPT_SCHEMA,
        "verified": True,
        "qualified": True,
        "git_sha": expected_git_sha,
        "binary": {"name": binary_name, "sha256": binary_sha256},
        "harness": {"name": harness_name, "sha256": harness_sha256},
        "result_sha256": "",
        "broker": {
            "venue": "IB",
            "environment": "PAPER",
            "session_id": broker["session_id"],
            "account_fingerprint": broker["account_fingerprint"],
            "host_fingerprint": broker["host_fingerprint"],
        },
        "started_at_ms": started,
        "completed_at_ms": completed,
        "verified_at_ms": int(time.time() * 1000),
        "evidence_file_count": len(referenced_paths),
        "evidence_bytes": total_evidence_bytes,
        "scenarios": scenario_receipts,
    }


def atomic_private_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        data = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
        offset = 0
        while offset < len(data):
            written = os.write(descriptor, data[offset:])
            if written <= 0:
                raise OSError("short receipt write")
            offset += written
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--expected-git-sha", required=True)
    parser.add_argument("--expected-binary", type=Path, required=True)
    parser.add_argument("--expected-harness", type=Path, required=True)
    parser.add_argument("--receipt", type=Path)
    args = parser.parse_args()

    try:
        root, root_before = secure_evidence_root(args.evidence_root)
        result_path = Path(os.path.abspath(os.fspath(args.result)))
        try:
            relative_result = result_path.relative_to(root).as_posix()
        except ValueError as error:
            raise QualificationError("qualification result must be inside evidence root") from error
        if relative_result != "qualification-result.json":
            raise QualificationError(
                "qualification result must be evidence-root/qualification-result.json"
            )
        expected_git_sha = args.expected_git_sha.strip().lower()
        if FULL_SHA.fullmatch(expected_git_sha) is None:
            raise QualificationError("expected git SHA is not canonical")
        binary_name, binary_sha256 = verify_tool(args.expected_binary, "execution binary")
        harness_name, harness_sha256 = verify_tool(args.expected_harness, "qualification harness")
        result_data, _ = stable_regular_bytes(
            result_path,
            label="qualification result",
            maximum=MAX_RESULT_BYTES,
            allowed_owners=frozenset({os.geteuid()}),
        )
        payload = parse_json(result_data, "qualification result")
        receipt = validate_result(
            payload,
            root=root,
            result_path=result_path,
            expected_git_sha=expected_git_sha,
            binary_name=binary_name,
            binary_sha256=binary_sha256,
            harness_name=harness_name,
            harness_sha256=harness_sha256,
        )
        root_after = root.lstat()
        if root_before.st_dev != root_after.st_dev or root_before.st_ino != root_after.st_ino:
            raise QualificationError("evidence root was replaced during verification")
        receipt["result_sha256"] = file_digest(result_data)
        receipt_path = args.receipt or root / "qualification-verification.json"
        receipt_path = Path(os.path.abspath(os.fspath(receipt_path)))
        if receipt_path != root / "qualification-verification.json":
            raise QualificationError(
                "verification receipt must be evidence-root/qualification-verification.json"
            )
        atomic_private_json(receipt_path, receipt)
    except (OSError, QualificationError) as error:
        print(f"ERROR: IB PAPER qualification rejected: {error}", file=sys.stderr)
        return 1

    print(
        "IB PAPER qualification PASS: "
        f"git_sha={receipt['git_sha']} scenarios={len(receipt['scenarios'])} "
        f"evidence_files={receipt['evidence_file_count']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
