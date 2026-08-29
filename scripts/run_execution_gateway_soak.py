#!/usr/bin/env python3
"""Repeat the Simulator, Gateway, and fake-IB process E2E suite.

This runner never starts HeptaTrader or a real broker connection. The invoked process
E2E binary owns its temporary Unix sockets, fork/exec Simulator servers, OMS
journals, fake-IB composition children, and fsync venue ledgers and removes
them after each certified round.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import os
import pathlib
import re
import signal
import stat
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from typing import (
    Callable, Dict, Iterable, List, NamedTuple, Optional, Set, Tuple)


MAX_CAPTURE_BYTES_DEFAULT = 1024 * 1024
OUTPUT_TAIL_BYTES = 8192
STABLE_STAT_FIELDS = (
    "st_dev", "st_ino", "st_mode", "st_nlink", "st_uid", "st_gid",
    "st_size", "st_mtime_ns", "st_ctime_ns",
)
SOAK_SCHEMA = "hepta.execution-gateway-soak.v11"
SOAK_SOURCE_BINARY_BINDING = (
    "fresh-build evidence is external; v11 pins executed inodes, binds "
    "the exact pre-send IB contract gate, and proves source/cache/binary "
    "snapshots remained unchanged"
)
SOAK_BINARY_NAMES = (
    "hepta_execution_service_process_e2e_tests",
    "hepta_execution_service_runtime_composition_tests",
    "hepta_execution_gateway_runtime_composition_tests",
    "hepta_execution_event_feed_tests",
    "hepta_ib_authoritative_event_queue_tests",
    "hepta_ib_paper_execution_profile_tests",
    "hepta_ib_paper_execution_process_e2e_tests",
    "hepta_tool_gateway_runtime_composition_tests",
    "hepta_ib_paper_agent_tool_e2e_tests",
)
EXTERNAL_BUILD_EVIDENCE_ROOT = "build-tree"
DIRECTORY_ID_FIELDS = (
    "st_dev", "st_ino", "st_mode", "st_uid", "st_gid",
)


class BuildTree(NamedTuple):
    path: pathlib.Path
    anchor: pathlib.Path
    relative: str
    logical: str
    identity: Tuple[int, ...]


class EvidenceContractResult(NamedTuple):
    fields: Dict[str, str]
    parse_error: str
    line_count: int
    missing_fields: List[str]
    unexpected_fields: List[str]
    mismatched_fields: Dict[str, Dict[str, Optional[str]]]
    observed: bool
    satisfied: bool


SOAK_MINIMUM_OBSERVED_PROCESSES = {
    "hepta_execution_service_process_e2e_tests": 2,
    "hepta_execution_service_runtime_composition_tests": 0,
    "hepta_execution_gateway_runtime_composition_tests": 1,
    "hepta_execution_event_feed_tests": 0,
    "hepta_ib_authoritative_event_queue_tests": 0,
    "hepta_ib_paper_execution_profile_tests": 0,
    "hepta_ib_paper_execution_process_e2e_tests": 2,
    "hepta_tool_gateway_runtime_composition_tests": 0,
    "hepta_ib_paper_agent_tool_e2e_tests": 0,
}
SOAK_DEFAULT_LIMITS = {
    "timeout_sec_per_binary": 30.0,
    "max_runner_fd_growth": 4,
    "max_runner_thread_growth": 2,
    "max_runner_rss_growth_kb": 16384,
    "max_process_tree_fds": 256,
    "max_process_tree_threads": 64,
    "max_process_tree_rss_kb": 262144,
    "max_output_bytes_per_binary": MAX_CAPTURE_BYTES_DEFAULT,
}
SOAK_EVIDENCE_CONTRACTS = (
    {
        "prefix": "execution_service_process_e2e_evidence:",
        "fields": {
            "crash_windows": "4",
            "venue_send_ledger": "verified",
            "oms_replay": "verified",
            "service_lease": "verified",
            "multi_instrument": "verified",
            "gateway_restart": "verified",
            "session_rotate": "verified",
            "agent_lease_not_on_wire": "verified",
            "authoritative_read_rpc": "verified",
            "owner_reconcile": "verified",
            "preview_permit": "single_use",
            "flatten_preview_permit": "single_use",
            "command_id": "execution_issued",
            "owner_fence_revokes_preview": "verified",
            "same_command_retry": "exactly_once",
        },
    },
    {
        "prefix": "execution_service_runtime_composition_evidence:",
        "fields": {
            "quote_feed": "execution_owned_periodic",
            "old_ttl_elapsed": "verified",
            "post_ttl_authoritative_read": "verified",
            "post_ttl_authoritative_preview": "verified",
            "stop_join": "verified",
            "start_failure_rollback": "verified",
        },
    },
    {
        "prefix": "execution_gateway_runtime_evidence:",
        "fields": {
            "remote_reconnect": "verified",
            "event_gap": "verified",
            "local_remote_merge": "verified",
            "session_fence_control": "verified",
            "paper_context_isolation": "verified",
            "old_epoch_backlog_rejected": "verified",
            "activated_fd_preserved": "verified",
            "first_post_restart_identity_mismatch": "verified",
            "explicit_identity_refresh_before_reconcile": "verified",
            "old_event_identity_backlog_rejected": "verified",
            "dual_socket_identity_mismatch_rejected": "verified",
            "event_restart_identity_refresh": "verified",
            "validated_pair_dispatch_pinned": "verified",
            "owner_wait_identity_serialized": "verified",
            "mutation_tools_remote_only": "verified",
            "resync_control_exact_match": "verified",
        },
    },
    {
        "prefix": "execution_event_fault_matrix_evidence:",
        "fields": {
            "server_identity_change": "verified",
            "ring_backpressure_gap": "verified",
            "gap_cursor_resume": "verified",
            "relay_gap_resync_latch": "verified",
            "relay_identity_reset": "verified",
            "peer_rejection_no_consume": "verified",
            "stale_event_identity_no_read": "verified",
            "identity_reject_no_cursor_publish": "verified",
        },
    },
    {
        "prefix": "ib_authoritative_fault_matrix_evidence:",
        "fields": {
            "stale_connection_epoch_drop": "verified",
            "queue_overflow_resync_latch": "verified",
            "positions_multi_stale_end_fence": "verified",
            "market_data_admission_state": "verified",
            "cash_farm_marker_epoch_sequence": "verified",
            "event_queue_try_push_drop": "verified",
            "active_duplicate_conflict": "verified",
            "active_incremental_conflict": "verified",
            "terminal_invalid_evidence": "verified",
            "terminal_overflow_fail_closed": "verified",
            "risk_snapshot_fail_closed": "verified",
            "contract_binding_fail_closed": "verified",
            "reduce_only_send_revalidation": "verified",
        },
    },
    {
        "prefix": "ib_paper_reconcile_fault_matrix_evidence:",
        "fields": {
            "active_snapshot_incomplete_rejected": "verified",
            "active_terminal_correlation_conflict_rejected": "verified",
            "terminal_snapshot_incomplete_rejected": "verified",
            "active_terminal_epoch_mismatch_rejected": "verified",
            "complete_epoch_aligned_reconcile": "verified",
            "recovery_owner_exact_scope": "verified",
            "recovery_owner_unmapped_and_uncertain_rejected": "verified",
        },
    },
    {
        "prefix": "ib_paper_execution_process_e2e_evidence:",
        "fields": {
            "composition_child_exec": "verified",
            "startup_handshake": "verified",
            "idempotent_fixture_setup": "verified",
            "state_lock": "verified",
            "restart_replay": "verified",
            "sigkill_windows": "4",
            "broker_send_ledger": "verified",
            "cancel_sigkill_scenarios": "5",
            "cancel_terminal_resolution": "verified",
            "cancel_no_resend": "verified",
            "oms_durability": "verified",
        },
    },
    {
        "prefix": "tool_gateway_runtime_composition_tests:",
        "fields": {
            "remote_only": "verified",
            "sessionctl": "verified",
            "authoritative_read": "verified",
            "place": "verified",
            "owner_fence": "verified",
            "liveness": "verified",
        },
    },
    {
        "prefix": "ib_paper_agent_tool_e2e_tests:",
        "fields": {
            "authoritative_quote": "verified",
            "preview_permit": "single_use",
            "command_id": "execution_issued",
            "same_command_retry": "exactly_once",
            "broker_place_exactly_once": "verified",
            "cancel": "verified",
            "authoritative_flatten": "verified",
            "flatten_snapshot_toc_tou": "blocked",
        },
    },
)


@dataclass(frozen=True)
class SoakProfile:
    """Named soak policy; keep profile-to-round mapping in one place."""

    name: str
    rounds: int

    # The former CTest gate ran two rounds while the CI release lane ran
    # eight.  Keep those same evidence levels behind one named policy so a
    # caller cannot accidentally mix a short report with a release claim.
    _ROUNDS = {"pr-smoke": 2, "release": 8, "nightly": 8}

    @classmethod
    def resolve(cls, name: str) -> "SoakProfile":
        if name not in cls._ROUNDS:
            raise ValueError(f"unknown soak profile: {name}")
        return cls(name, cls._ROUNDS[name])
SOAK_EXPECTED_INVARIANTS = {
    "in_flight_crash_windows": 4,
    "transport_unavailable_is_uncertain": True,
    "same_command_retry_no_second_send": True,
    "oms_replay_and_owner_reconcile": True,
    "service_owned_decision_lease": True,
    "agent_decision_lease_not_serialized": True,
    "multi_instrument_service_leases": True,
    "gateway_restart_renews_service_lease": True,
    "session_rotate_renews_service_lease": True,
    "mutation_tools_require_remote_execution": True,
    "standalone_tool_gateway_remote_only": True,
    "standalone_gateway_os_session_lifecycle": True,
    "execution_authoritative_read_rpc": True,
    "execution_owned_preview_permit_single_use": True,
    "execution_issued_future_mutation_command_id": True,
    "execution_owner_fence_revokes_preview_permits": True,
    "same_command_place_retry_is_exactly_once": True,
    "simulator_quote_feed_is_execution_owned_periodic": True,
    "simulator_quote_survives_original_ttl": True,
    "simulator_quote_feed_stop_and_start_rollback_join": True,
    "standalone_gateway_remote_place": True,
    "standalone_gateway_owner_fence": True,
    "standalone_gateway_reports_execution_liveness": True,
    "session_fence_release_control": True,
    "event_gap_detected": True,
    "event_gap_cursor_resume": True,
    "event_resync_latch_survives_timeout": True,
    "event_resync_latch_requires_explicit_authoritative_reconcile": True,
    "event_service_identity_reset": True,
    "stale_event_identity_rejected_before_source_read": True,
    "event_identity_rejection_does_not_advance_cursor_or_publish": True,
    "local_remote_event_merge": True,
    "same_gateway_object_reconnect": True,
    "gateway_paper_context_isolation": True,
    "old_service_epoch_backlog_rejected": True,
    "old_event_identity_backlog_rejected": True,
    "dual_socket_identity_mismatch_rejected_without_dispatch": True,
    "validated_dual_socket_pair_is_pinned_through_dispatch": True,
    "same_owner_event_identity_observation_and_cursor_are_serialized": True,
    "resync_control_requires_exact_type_venue_and_reason": True,
    "event_restart_identity_refresh_is_explicit": True,
    "first_post_restart_event_wait_rejected_before_identity_refresh": True,
    "second_post_restart_event_wait_publishes_resync_without_source_read": True,
    "third_post_reconcile_event_wait_reads_new_daemon_event": True,
    "activated_listen_fd_preserved_across_service_restart": True,
    "ib_stale_connection_epoch_event_dropped": True,
    "ib_event_queue_overflow_requires_resync": True,
    "ib_active_correlation_conflicts_fail_closed": True,
    "ib_terminal_correlation_invalid_or_overflow_fails_closed": True,
    "ib_risk_snapshot_overflow_fails_closed": True,
    "ib_paper_contract_binding_fails_closed": True,
    "ib_reconcile_incomplete_active_snapshot_rejected": True,
    "ib_reconcile_active_terminal_correlation_conflict_rejected": True,
    "ib_reconcile_incomplete_terminal_snapshot_rejected": True,
    "ib_reconcile_active_terminal_epoch_mismatch_rejected": True,
    "ib_paper_recovery_owner_exact_scope": True,
    "ib_paper_recovery_owner_unmapped_and_uncertain_rejected": True,
    "ib_paper_composition_child_exec_and_state_lock": True,
    "ib_paper_startup_identity_handshake": True,
    "ib_paper_idempotent_fixture_setup": True,
    "ib_paper_in_flight_sigkill_windows": 4,
    "ib_paper_cancel_sigkill_scenarios": 5,
    "ib_paper_cancel_terminal_resolution": True,
    "ib_paper_cancel_no_resend": True,
    "ib_paper_positive_only_reconcile": True,
    "ib_paper_same_command_no_second_broker_send": True,
    "ib_paper_critical_oms_replay": True,
    "ib_agent_tool_authoritative_quote": True,
    "ib_agent_tool_execution_preview_permit": True,
    "ib_agent_tool_future_command_id": True,
    "ib_agent_tool_same_command_retry_exactly_once": True,
    "ib_agent_tool_cancel": True,
    "ib_agent_tool_authoritative_flatten": True,
    "ib_agent_tool_flatten_snapshot_toc_tou_blocked": True,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--build-dir", default="build-release")
    parser.add_argument(
        "--soak-profile",
        choices=("pr-smoke", "release", "nightly"),
        default="release",
        help=("Execution profile: pr-smoke runs 2 rounds; release/nightly run "
              "the full 8-round certification unless --rounds is supplied."),
    )
    parser.add_argument(
        "--rounds", type=int, default=None,
        help="Explicit round count (overrides --soak-profile default).",
    )
    parser.add_argument(
        "--timeout-sec",
        type=float,
        default=SOAK_DEFAULT_LIMITS["timeout_sec_per_binary"],
    )
    parser.add_argument("--sample-ms", type=int, default=10)
    parser.add_argument(
        "--max-runner-fd-growth",
        type=int,
        default=SOAK_DEFAULT_LIMITS["max_runner_fd_growth"],
    )
    parser.add_argument(
        "--max-runner-thread-growth",
        type=int,
        default=SOAK_DEFAULT_LIMITS["max_runner_thread_growth"],
    )
    parser.add_argument(
        "--max-runner-rss-growth-kb",
        type=int,
        default=SOAK_DEFAULT_LIMITS["max_runner_rss_growth_kb"],
    )
    parser.add_argument(
        "--max-process-tree-fds",
        type=int,
        default=SOAK_DEFAULT_LIMITS["max_process_tree_fds"],
    )
    parser.add_argument(
        "--max-process-tree-threads",
        type=int,
        default=SOAK_DEFAULT_LIMITS["max_process_tree_threads"],
    )
    parser.add_argument(
        "--max-process-tree-rss-kb",
        type=int,
        default=SOAK_DEFAULT_LIMITS["max_process_tree_rss_kb"],
    )
    parser.add_argument(
        "--max-output-bytes",
        type=int,
        default=SOAK_DEFAULT_LIMITS["max_output_bytes_per_binary"],
    )
    parser.add_argument(
        "--require-build-type", default="",
        choices=("", "Debug", "Release", "RelWithDebInfo", "MinSizeRel"))
    parser.add_argument(
        "--report",
        default="",
        help="Report path; defaults to a direct child of --build-dir")
    args = parser.parse_args()
    if args.rounds is None:
        args.rounds = SoakProfile.resolve(args.soak_profile).rounds
    if not 1 <= args.rounds <= 10000:
        parser.error("--rounds must be in [1, 10000]")
    if not 1.0 <= args.timeout_sec <= 600.0:
        parser.error("--timeout-sec must be in [1, 600]")
    if not 1 <= args.sample_ms <= 1000:
        parser.error("--sample-ms must be in [1, 1000]")
    if not 65536 <= args.max_output_bytes <= 16 * 1024 * 1024:
        parser.error("--max-output-bytes must be in [65536, 16777216]")
    return args


def proc_stat(pid: int) -> Dict[str, int]:
    base = pathlib.Path("/proc") / str(pid)
    result = {"rss_kb": 0, "fds": 0, "threads": 0}
    try:
        result["fds"] = len(list((base / "fd").iterdir()))
        result["threads"] = len(list((base / "task").iterdir()))
        for line in (base / "status").read_text(encoding="utf-8").splitlines():
            if line.startswith("VmRSS:"):
                result["rss_kb"] = int(line.split()[1])
                break
    except (FileNotFoundError, PermissionError, ProcessLookupError):
        pass
    return result


def process_tree(root_pid: int) -> Set[int]:
    # Walk only the target process tree. Scanning every /proc/<pid>/stat on
    # each sample can itself exceed the short child hold used by the process
    # E2E binaries on a busy host, producing a false "tree not observed".
    # Children are per-thread on Linux, so inspect every task rather than only
    # the thread-group leader.
    tree: Set[int] = set()
    pending = [root_pid]
    while pending:
        pid = pending.pop()
        if pid in tree:
            continue
        tree.add(pid)
        try:
            task_directories = list(
                (pathlib.Path("/proc") / str(pid) / "task").iterdir())
        except (FileNotFoundError, NotADirectoryError, PermissionError, ProcessLookupError):
            continue
        for task_directory in task_directories:
            try:
                children = (task_directory / "children").read_text(
                    encoding="utf-8").split()
            except (FileNotFoundError, NotADirectoryError, PermissionError,
                    ProcessLookupError):
                continue
            for child in children:
                try:
                    child_pid = int(child)
                except ValueError:
                    continue
                if child_pid > 0 and child_pid not in tree:
                    pending.append(child_pid)
    return tree


def aggregate(pids: Iterable[int]) -> Dict[str, int]:
    total = {"rss_kb": 0, "fds": 0, "threads": 0, "processes": 0}
    for pid in pids:
        current = proc_stat(pid)
        if current["fds"] or current["threads"] or current["rss_kb"]:
            total["processes"] += 1
            for key in ("rss_kb", "fds", "threads"):
                total[key] += current[key]
    return total


def process_group_members(process_group: int) -> Set[int]:
    members: Set[int] = set()
    for entry in pathlib.Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            pid = int(entry.name)
            if os.getpgid(pid) == process_group:
                members.add(pid)
        except (PermissionError, ProcessLookupError, ValueError):
            continue
    return members


def sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def git_output_sha256(root: pathlib.Path, arguments: List[str]) -> str:
    try:
        output = subprocess.check_output(
            ["git", *arguments], cwd=root, stderr=subprocess.DEVNULL
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise RuntimeError("git provenance command failed") from error
    return sha256_bytes(output)


def _unique_json_object(
        pairs: List[Tuple[str, object]]) -> Dict[str, object]:
    document: Dict[str, object] = {}
    for key, value in pairs:
        if key in document:
            raise ValueError(f"duplicate JSON key: {key}")
        document[key] = value
    return document


def validated_bundle_provenance(root: pathlib.Path) -> Dict[str, object]:
    strict_relative = ".hepta/source-bundle-manifest.json"
    agent_relative = ".hepta/agent-os-source-manifest.json"
    marker_relatives = [
        relative for relative in (strict_relative, agent_relative)
        if (root / relative).exists()
    ]
    if len(marker_relatives) != 1:
        raise RuntimeError(
            "validated source-bundle provenance is unavailable or ambiguous")
    manifest_relative = marker_relatives[0]
    manifest_path = root / manifest_relative
    try:
        metadata = manifest_path.lstat()
        if (not stat.S_ISREG(metadata.st_mode) or
                stat.S_ISLNK(metadata.st_mode) or
                metadata.st_size > 16 * 1024 * 1024):
            raise RuntimeError("source-bundle manifest is unsafe")
        manifest = json.loads(
            manifest_path.read_text(encoding="utf-8"),
            object_pairs_hook=_unique_json_object)
    except (OSError, ValueError) as error:
        raise RuntimeError(
            "validated source-bundle provenance is unavailable") from error
    if not isinstance(manifest, dict) or manifest.get("root") != root.name:
        raise RuntimeError("source-bundle provenance contract is invalid")
    if manifest_relative == strict_relative:
        valid_identity = (
            manifest.get("schema") == "hepta.clean-source-bundle.v2" and
            manifest.get("bundle_class") == "strict-source-only" and
            manifest.get("excluded_legacy_runtime_tree") == "Tools" and
            manifest.get("prebuilt_payload_included") is False
        )
        head = manifest.get("git_head", "")
    else:
        parent = manifest.get("parent_strict_source")
        valid_identity = (
            manifest.get("schema") == "hepta.agent-os-source-bundle.v1" and
            manifest.get("bundle_class") == "agent-os-source-only" and
            isinstance(parent, dict) and
            parent.get("schema") == "hepta.clean-source-bundle.v2"
        )
        head = parent.get("git_head", "") if isinstance(parent, dict) else ""
    if (not valid_identity or
            manifest.get("paper_authorized") is not False or
            manifest.get("live_authorized") is not False):
        raise RuntimeError("source-bundle provenance contract is invalid")
    if re.fullmatch(r"[0-9a-f]{40,64}", head) is None:
        raise RuntimeError("source-bundle git HEAD is invalid")
    entries = manifest.get("files", [])
    if not isinstance(entries, list) or len(entries) != manifest.get("file_count"):
        raise RuntimeError("source-bundle file closure is invalid")
    current = []
    seen: Set[str] = set()
    for expected in entries:
        relative = expected.get("path", "")
        if relative in seen or not isinstance(relative, str):
            raise RuntimeError("source-bundle file closure is ambiguous")
        seen.add(relative)
        record = stable_file_snapshot(root, relative)[0]
        normalized_mode = "0755" if int(record["mode"], 8) & 0o100 else "0644"
        actual = {"path": record["path"], "mode": normalized_mode,
                  "size": record["size"], "sha256": record["sha256"].removeprefix("sha256:")}
        if actual != expected:
            raise RuntimeError("source-bundle file closure drift: " + relative)
        current.append(actual)
    canonical = json.dumps(current, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode()
    if hashlib.sha256(canonical).hexdigest() != manifest.get("files_sha256"):
        raise RuntimeError("source-bundle closure digest mismatch")
    return {
        "manifest": stable_file_snapshot(root, manifest_relative)[0],
        "schema": manifest["schema"],
        "git_head": head,
        "file_count": len(current),
        "files_sha256": manifest["files_sha256"],
        "files": current,
    }


def _relative_parts(relative: str) -> Tuple[str, ...]:
    path = pathlib.PurePosixPath(relative)
    if path.is_absolute() or not path.parts or any(
            part in ("", ".", "..") for part in path.parts):
        raise RuntimeError("unsafe evidence input path")
    return path.parts


def _same_stable_metadata(left: os.stat_result,
                          right: os.stat_result) -> bool:
    return all(getattr(left, field) == getattr(right, field)
               for field in STABLE_STAT_FIELDS)


def stable_file_snapshot(
        root: pathlib.Path,
        relative: str,
        *,
        capture_limit: int = 0,
) -> Tuple[Dict[str, object], bytes]:
    """Hash one regular file through a no-follow descriptor-anchored walk."""
    parts = _relative_parts(relative)
    directory_flags = os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY
    if not hasattr(os, "O_NOFOLLOW"):
        raise RuntimeError("O_NOFOLLOW is required for evidence snapshots")
    directory_flags |= os.O_NOFOLLOW
    root_descriptor = os.open(root, directory_flags)
    parent = os.dup(root_descriptor)
    os.close(root_descriptor)
    descriptor = -1
    try:
        for component in parts[:-1]:
            next_parent = os.open(
                component, directory_flags, dir_fd=parent)
            os.close(parent)
            parent = next_parent
        name = parts[-1]
        before = os.stat(name, dir_fd=parent, follow_symlinks=False)
        descriptor = os.open(
            name,
            os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=parent,
        )
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise RuntimeError(f"evidence input is not regular: {relative}")
        if not _same_stable_metadata(before, opened):
            raise RuntimeError(f"evidence input changed while opening: {relative}")
        if capture_limit and opened.st_size > capture_limit:
            raise RuntimeError(f"evidence input exceeds size limit: {relative}")
        digest = hashlib.sha256()
        chunks: List[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            total += len(chunk)
            if capture_limit:
                if total > capture_limit:
                    raise RuntimeError(
                        f"evidence input exceeds size limit: {relative}")
                chunks.append(chunk)
        after = os.fstat(descriptor)
        current = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if (not _same_stable_metadata(opened, after) or
                not _same_stable_metadata(after, current)):
            raise RuntimeError(f"evidence input changed while hashing: {relative}")
        return ({
            "path": relative,
            "size": after.st_size,
            "mode": f"{stat.S_IMODE(after.st_mode):04o}",
            "sha256": "sha256:" + digest.hexdigest(),
        }, b"".join(chunks))
    except OSError as error:
        raise RuntimeError(
            f"cannot securely snapshot evidence input {relative}: "
            f"{error.strerror}") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(parent)


def build_file_snapshot(
        build: BuildTree,
        suffix: str,
        *,
        capture_limit: int = 0,
) -> Tuple[Dict[str, object], bytes]:
    actual = build.relative + "/" + suffix
    logical = build.logical + "/" + suffix
    record, payload = stable_file_snapshot(
        build.anchor, actual, capture_limit=capture_limit)
    record["path"] = logical
    return record, payload


def open_pinned_executable(
        root: pathlib.Path,
        relative: str,
        expected: Dict[str, object],
        *,
        evidence_relative: Optional[str] = None,
) -> int:
    """Open and hash the exact inode that will be executed through /proc."""
    parts = _relative_parts(relative)
    directory_flags = (
        os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW)
    parent = os.open(root, directory_flags)
    descriptor = -1
    try:
        for component in parts[:-1]:
            next_parent = os.open(
                component, directory_flags, dir_fd=parent)
            os.close(parent)
            parent = next_parent
        name = parts[-1]
        before = os.stat(name, dir_fd=parent, follow_symlinks=False)
        descriptor = os.open(
            name, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
            dir_fd=parent)
        opened = os.fstat(descriptor)
        if (not stat.S_ISREG(opened.st_mode) or
                stat.S_IMODE(opened.st_mode) & 0o111 == 0):
            raise RuntimeError(f"soak input is not executable: {relative}")
        if not _same_stable_metadata(before, opened):
            raise RuntimeError(f"soak input changed while opening: {relative}")
        digest = hashlib.sha256()
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        os.lseek(descriptor, 0, os.SEEK_SET)
        after = os.fstat(descriptor)
        current = os.stat(name, dir_fd=parent, follow_symlinks=False)
        observed = {
            "path": evidence_relative or relative,
            "size": after.st_size,
            "mode": f"{stat.S_IMODE(after.st_mode):04o}",
            "sha256": "sha256:" + digest.hexdigest(),
        }
        if (not _same_stable_metadata(opened, after) or
                not _same_stable_metadata(after, current) or
                observed != expected):
            raise RuntimeError(f"soak input changed before execution: {relative}")
        return descriptor
    except OSError as error:
        if descriptor >= 0:
            os.close(descriptor)
        raise RuntimeError(
            f"cannot securely pin soak input {relative}: "
            f"{error.strerror}") from error
    except Exception:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    finally:
        os.close(parent)


def redact_output_tail(
        value: str,
        root: pathlib.Path,
        *additional_roots: pathlib.Path,
) -> str:
    """Keep bounded diagnostics without publishing accounts or local paths."""
    redacted = value.replace(str(root), "<repo>")
    for additional in additional_roots:
        redacted = redacted.replace(str(additional), "<build>")
    redacted = re.sub(r"DU[0-9]{1,16}", "DU<redacted>", redacted)
    redacted = re.sub(
        r"(?i)\b(password|token|secret|authorization|credential|account)"
        r"([=:])[^\s]+",
        lambda match: match.group(1) + match.group(2) + "<redacted>",
        redacted,
    )
    redacted = re.sub(
        r"/(?:home|tmp|run/user)/[^\s\]\[(){}<>\"']+",
        "<local-path>", redacted)
    return redacted[-OUTPUT_TAIL_BYTES:]


def _cache_entries(cache_text: str) -> Dict[str, Tuple[str, str]]:
    entries: Dict[str, Tuple[str, str]] = {}
    for line in cache_text.splitlines():
        if not line or line.startswith(("//", "#")) or "=" not in line:
            continue
        typed_key, value = line.split("=", 1)
        if ":" not in typed_key:
            continue
        key, kind = typed_key.rsplit(":", 1)
        if key in entries:
            raise RuntimeError("duplicate CMake cache key")
        entries[key] = (kind, value)
    return entries


def validate_build_cache(
        source_root: pathlib.Path,
        build: BuildTree,
        required_build_type: str,
) -> Dict[str, object]:
    validate_build_tree(build)
    cache_record, cache_bytes = build_file_snapshot(
        build, "CMakeCache.txt", capture_limit=4 * 1024 * 1024)
    try:
        cache_text = cache_bytes.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise RuntimeError("CMake cache is not strict UTF-8") from error
    entries = _cache_entries(cache_text)

    def value(key: str) -> str:
        if key not in entries:
            raise RuntimeError(f"required CMake cache key is missing: {key}")
        return entries[key][1]

    try:
        configured_root = pathlib.Path(value("CMAKE_HOME_DIRECTORY")).resolve(
            strict=True)
    except OSError as error:
        raise RuntimeError("configured CMake source root is unavailable") from error
    if configured_root != source_root:
        raise RuntimeError("build tree belongs to a foreign CMake source root")
    try:
        configured_build = pathlib.Path(
            value("CMAKE_CACHEFILE_DIR")).resolve(strict=True)
    except OSError as error:
        raise RuntimeError(
            "configured CMake build root is unavailable") from error
    if configured_build != build.path:
        raise RuntimeError("CMake cache belongs to a foreign build root")
    build_type = value("CMAKE_BUILD_TYPE")
    allowed_build_types = {"Debug", "Release", "RelWithDebInfo", "MinSizeRel"}
    if build_type not in allowed_build_types:
        raise RuntimeError("CMAKE_BUILD_TYPE is empty or unsupported")
    if required_build_type and build_type != required_build_type:
        raise RuntimeError("CMAKE_BUILD_TYPE does not match --require-build-type")
    if value("BUILD_TESTING").upper() != "ON":
        raise RuntimeError("BUILD_TESTING must be ON")
    if value("HEPTA_ENABLE_LEGACY_0DTE_BRIDGE").upper() != "OFF":
        raise RuntimeError("legacy 0DTE bridge must be OFF")
    if value("HEPTA_BUILD_LEGACY_MONOLITH").upper() != "OFF":
        raise RuntimeError("legacy monolith must be OFF")
    if value("HEPTA_BUILD_LEGACY_SIMULATOR").upper() != "OFF":
        raise RuntimeError("legacy simulator must be OFF")
    ibapi = value("HEPTA_ENABLE_IBAPI").upper()
    if ibapi not in {"ON", "OFF"}:
        raise RuntimeError("HEPTA_ENABLE_IBAPI must be a canonical BOOL")

    compile_commands_setting = entries.get(
        "CMAKE_EXPORT_COMPILE_COMMANDS", ("BOOL", ""))[1].upper()
    compile_commands: object = "not-enabled"
    if compile_commands_setting == "ON":
        compile_commands = build_file_snapshot(
            build, "compile_commands.json")[0]
    return {
        "cmake_cache": cache_record,
        "build_type": build_type,
        "ibapi_enabled": ibapi == "ON",
        "legacy_0dte_bridge_enabled": False,
        "legacy_monolith_built": False,
        "legacy_simulator_built": False,
        "generator": value("CMAKE_GENERATOR"),
        "cxx_compiler_name": pathlib.Path(value("CMAKE_CXX_COMPILER")).name,
        "compile_commands": compile_commands,
    }


def source_manifest(
        root: pathlib.Path,
        snapshot: Optional[
            Callable[[pathlib.Path, str], Dict[str, object]]] = None,
) -> Dict[str, object]:
    """Snapshot security-relevant dirty-tree source and deployment inputs."""
    agent_marker = root / ".hepta/agent-os-source-manifest.json"
    if not (root / ".git").exists() and agent_marker.exists():
        provenance = validated_bundle_provenance(root)
        if provenance["schema"] != "hepta.agent-os-source-bundle.v1":
            raise RuntimeError(
                "Agent OS source marker resolved to the wrong bundle class")
        entries = [
            {
                "path": record["path"],
                "size": record["size"],
                "mode": record["mode"],
                "sha256": "sha256:" + record["sha256"],
            }
            for record in provenance["files"]
        ]
        canonical = json.dumps(
            entries, ensure_ascii=True, separators=(",", ":"),
            sort_keys=True).encode("utf-8")
        return {
            "file_count": len(entries),
            "sha256": sha256_bytes(canonical),
            "files": entries,
        }
    snapshot_file = snapshot or (
        lambda repository, relative:
        stable_file_snapshot(repository, relative)[0])
    source_directories = {
        "HeptaTrade": {".cpp", ".h"},
        "tests": {".cpp", ".h", ".py", ".json"},
        "hepta_ops": {".py"},
        "ops": {".json"},
        "policies": {".json"},
        "compat/hepta-ops-generated": {".sh"},
    }
    fixed_files = {
        ".github/workflows/ci-gate.yml",
        ".gitignore",
        "CMakeLists.txt",
        "README.md",
        "VERSION",
        "HeptaTrade/CMakeLists.txt",
        "HeptaTrade/cmake/HeptaTraderAgentOsSdkConfig.cmake.in",
        "cmake/verify_gateway_forbidden_symbols.cmake",
        "HeptaTrade/oms_journal.cpp",
        "HeptaTrade/oms_journal.h",
        "configs/hepta-local-ai-paper-strategy-v1.json",
        "configs/hepta-local-ai-paper-strategy-v2.json",
        "configs/hepta-local-ai-paper-strategy-v3.json",
        "HeptaTrade/HeptaTraderConfig.xml.example",
        "HeptaTrade/HeptaTraderConfig.paper.xml",
        "adapters/mcp/hepta_mcp_server.py",
        "docs/AGENT-NATIVE-TRADING-OS-ARCHITECTURE.md",
        "docs/AUTONOMOUS-PAPER-CAMPAIGN.md",
        "docs/BROKER-NETWORK-ISOLATION.md",
        "docs/EURUSD-CONFIRMED-MOMENTUM-SHADOW-V2.md",
        "docs/IB-PROD-HARDENING.md",
        "docs/archive/README.md",
        "docs/archive/ROUND37-CONSOLIDATION.md",
        "docs/ROUND38-CONSOLIDATION.md",
        "docs/RUNBOOK-KILLSWITCH.md",
        "docs/RUNBOOK-STARTUP.md",
        "scripts/check_hepta_agent_os_provisioned_host.py",
        "scripts/check_hepta_agent_os_product_boundary.py",
        "scripts/check_hepta_agent_trust_domains.py",
        "scripts/check_hepta_agent_os_units.py",
        "scripts/check_hepta_broker_network_policy.py",
        "scripts/check_no_direct_broker_paths.py",
        "scripts/check_hepta_execution_provisioned_host.py",
        "scripts/check_heptatrader_code_quality.py",
        "scripts/check_heptatrader_ctest_inventory.py",
        "scripts/audit_heptatrader_workspace_layout.py",
        "scripts/aggregate_hepta_execution_native_systemd_gate.py",
        "scripts/build_hepta_execution_native_vm_bundle.py",
        "scripts/run_hepta_execution_native_systemd_gate.py",
        "scripts/run_hepta_execution_rootful_systemd_gate.py",
        "scripts/run_hepta_broker_network_hard_isolation_gate.py",
        "scripts/run_hepta_paper_domain_rootful_systemd_gate.py",
        "scripts/run_hepta_p1_dual_domain_rootful_gate.py",
        "scripts/run_hepta_p1_campaign_rootful_liveness_gate.py",
        "scripts/run_hepta_agent_os_rootful_systemd_e2e_gate.py",
        "scripts/run_hepta_agent_os_systemd_lifecycle_gate.py",
        "scripts/run_hepta_broker_network_rootful_gate.py",
        "tests/broker_network_rootful/Dockerfile",
        "scripts/run_heptatrader_openclaw_loader_gate.py",
        "scripts/run_execution_gateway_soak.py",
        "scripts/hepta_release_check.py",
        "scripts/hepta_ops.py",
        "scripts/resolve_hepta_config.py",
        "scripts/freeze_heptatrader_source_baseline.py",
        "scripts/build_heptatrader_clean_source_bundle.py",
        "scripts/build_heptatrader_agent_os_source_bundle.py",
        "scripts/build_heptatrader_delivery_closure.py",
        "scripts/heptatrader_secure_artifacts.py",
        "scripts/build_heptatrader_engineering_artifact_map.py",
        "scripts/build_heptatrader_engineering_closure.py",
        "scripts/build_heptatrader_recovery_evidence.py",
        "scripts/build_heptatrader_round38_verification_reports.py",
        "scripts/build_heptatrader_verification_evidence.py",
        "scripts/build_heptatrader_evidence_index.py",
        "scripts/build_heptatrader_evidence_ingestion_request.py",
        "scripts/build_heptatrader_evidence_set.py",
        "scripts/build_heptatrader_release_validation_closure.py",
        "scripts/build_heptatrader_distribution_artifact_set.py",
        "scripts/build_heptatrader_runtime_package.py",
        "scripts/build_hepta_shadow_install_manifest.py",
        "scripts/build_hepta_shadow_runtime_archive.py",
        "scripts/build_heptatrader_round36_certification.py",
        "scripts/build_heptatrader_vendor_overlay_set.py",
        "scripts/converge_ctp_vendor_headers.py",
        "scripts/hepta_agent_mcp_launcher.py",
        "scripts/hepta_agent_session_bootstrap.py",
        "scripts/hepta_agent_trust_domain.py",
        "scripts/hepta_broker_egress_policy.py",
        "scripts/hepta_campaignctl.py",
        "scripts/hepta_ib_paper_campaign_operator.py",
        "scripts/hepta_ib_paper_domain_authority.py",
        "scripts/hepta_local_ai_paper_agent.py",
        "scripts/hepta_local_paper_control.py",
        "scripts/prepare_repair_campaign.py",
        "scripts/run_paper_repair.py",
        "scripts/run_paper_safe_recover.py",
        "scripts/run_paper_safe_recover_guard.py",
        "scripts/run_paper_session_renew.py",
        "scripts/run_paper_supervisor.py",
        "scripts/hepta_paper_receipt_contracts.py",
        "scripts/hepta_paper_receipt_contracts_v2_compat.py",
        "scripts/hepta_p1_paper_canary_backend_adapter.py",
        "scripts/hepta_p1_paper_canary_crash_emergency_closer.py",
        "scripts/hepta_p1_paper_canary_executor.py",
        "scripts/hepta_p1_paper_canary_handoff_producer.py",
        "scripts/hepta_p1_paper_canary_launch_joiner.py",
        "scripts/hepta_p1_paper_canary_owner_provisioner.py",
        "scripts/hepta_p1_paper_canary_root_coordinator.py",
        "scripts/hepta_p1_paper_canary_root_finalizer.py",
        "scripts/hepta_p1_paper_canary_terminal_prover.py",
        "scripts/hepta_p1_paper_terminal_witness_verifier.py",
        "scripts/hepta_p1_shadow_host_controller.py",
        "scripts/hepta_p1_load_probe_validator.py",
        "scripts/build_hepta_p1_observation_policy.py",
        "scripts/hepta_p1_shadow_observer_controller.py",
        "scripts/hepta_p1_shadow_admission_launcher.py",
        "scripts/hepta_p1_safety_soak_campaign_freezer.py",
        "scripts/hepta_p1_safety_soak_policy_planner.py",
        "scripts/hepta_p1_safety_soak_campaign_coordinator.py",
        "scripts/hepta_p1_safety_soak_observer_worker.py",
        "scripts/hepta_p1_safety_soak_recorder_worker.py",
        "scripts/hepta_p1_safety_soak_fault_pin_producer.py",
        "scripts/hepta_p1_safety_soak_evidence_recorder.py",
        "scripts/hepta_p1_safety_soak_independent_observer.py",
        "scripts/hepta_p1_safety_soak_root_fault_injector.py",
        "scripts/hepta_p1_safety_soak_auditor.py",
        "scripts/hepta_p1_watch_to_paper_handoff.py",
        "scripts/hepta_p1_paper_zero_exposure_snapshot_producer.py",
        "scripts/hepta_p1_paper_zero_exposure_attestor.py",
        "scripts/hepta_p1_paper_admission_verifier.py",
        "scripts/hepta_p1_paper_kill_switch_bootstrap.py",
        "scripts/hepta_rootful_review_closure_consumer.py",
        "scripts/hepta_rootful_systemd_environment_provenance.py",
        "scripts/hepta_p1_watch_profile_deployer.py",
        "scripts/hepta_p1_watch_activation_transaction.py",
        "scripts/hepta_bounded_shadow_closure_verifier.py",
        "scripts/hepta_bounded_shadow_observer.py",
        "scripts/hepta_market_context_builder.py",
        "scripts/hepta_market_evidence_normalizer.py",
        "scripts/hepta_market_official_source_extractor.py",
        "scripts/hepta_eurusd_confirmed_momentum_strategy.py",
        "scripts/hepta_shadow_market_history.py",
        "scripts/hepta_shadow_host_installer.py",
        "scripts/hepta_strategy_replay_evaluator.py",
        "scripts/hepta_strategy_shadow_runner.py",
        "scripts/hepta_strategy_contracts.py",
        "scripts/validate_hepta_strategy_decision_receipt.py",
        "scripts/hepta_official_source_capture.py",
        "scripts/hepta_shadow_watch_collector.py",
        "scripts/hepta_shadow_watch_custodian.py",
        "scripts/hepta_shadow_watch_exporter.py",
        "scripts/hepta_ops.py",
        "scripts/inventory_heptatrader_legacy_wrappers.py",
        "tests/hepta_p1_shadow_host_controller_tests.py",
        "tests/hepta_p1_load_probe_validator_tests.py",
        "tests/hepta_p1_observer_controller_tests.py",
        "tests/hepta_p1_shadow_admission_launcher_tests.py",
        "tests/hepta_p1_safety_soak_auditor_tests.py",
        "tests/hepta_p1_paper_zero_exposure_snapshot_producer_tests.py",
        "tests/hepta_p1_paper_admission_verifier_tests.py",
        "tests/hepta_p1_watch_profile_deployer_tests.py",
        "tests/hepta_p1_watch_activation_transaction_tests.py",
        "tests/run_hepta_p1_dual_domain_rootful_gate_fixture.py",
        "tests/hepta_bounded_shadow_closure_verifier_tests.py",
        "strategies/eurusd-confirmed-momentum-shadow-v2.json",
        "scripts/run_heptatrader_coverage_evidence.py",
        "scripts/run_heptatrader_ctest_evidence.py",
        "scripts/verify_heptatrader_clean_source_bundle.py",
        "scripts/verify_heptatrader_agent_os_source_bundle.py",
        "scripts/verify_heptatrader_delivery_closure.py",
        "scripts/verify_heptatrader_engineering_closure.py",
        "scripts/verify_heptatrader_recovery_materialization.py",
        "scripts/verify_heptatrader_evidence_index.py",
        "scripts/verify_heptatrader_evidence_ingestion_receipt.py",
        "scripts/verify_heptatrader_evidence_set.py",
        "scripts/verify_heptatrader_release_validation_closure.py",
        "scripts/verify_heptatrader_distribution_artifact_set.py",
        "scripts/verify_heptatrader_runtime_package.py",
        "scripts/verify_heptatrader_round36_certification.py",
        "scripts/verify_heptatrader_vendor_overlay_set.py",
        "scripts/verify_heptatrader_source_baseline.py",
        "scripts/verify_heptatrader_vendor_assets.py",
        "scripts/verify_heptatrader_prebuilt_assets.py",
        "policies/heptatrader-workspace-layout-v1.json",
        "policies/heptatrader-agent-os-source-v2.json",
        "policies/heptatrader-code-quality-v1.json",
        "third_party/prebuilt-dependencies/README.md",
        "third_party/prebuilt-dependencies/manifest-v1.json",
        "third_party/ctp/6.5.1-tools/README.md",
        "third_party/ctp/6.5.1-tools/manifest-v1.json",
        "third_party/ctp/6.7.7/README.md",
        "third_party/ctp/6.7.7/manifest-v1.json",
        "scripts/strategy_iterate_paper.py",
        "scripts/run_ib_regression_round.ps1",
        "scripts/ib_paper_order_loop.py",
        "scripts/fx_strategy_paper.py",
        "scripts/verify_hepta_execution_native_vm_bundle.py",
        "plugins/heptatrader-agent-os/.codex-plugin/plugin.json",
        "plugins/heptatrader-agent-os/.mcp.json",
        "plugins/heptatrader-agent-os/README.md",
        ".agents/plugins/marketplace.json",
        "systemd/hepta-agent-broker-egress-policy.conf.example",
        "systemd/hepta-agent-host-identity.conf.example",
        "systemd/hepta-agent-trust-domain-paper-identities-v1.json.example",
        "systemd/hepta-agent-trust-domain-policy-v1.json",
        "systemd/hepta-agent-trust-domain.json.example",
        "systemd/hepta-broker-egress-policy.service",
        "systemd/hepta-p1-watch-activation.service",
        "systemd/hepta-p1-watch-activation-reconcile.service",
        "systemd/hepta-p1-watch-activation-reconcile.timer",
        "systemd/hepta-local-paper-authority@.service",
        "systemd/hepta-local-paper-fail-close@.service",
        "systemd/hepta-p1-paper-canary-capture.service",
        "systemd/hepta-p1-paper-canary-executor.service",
        "systemd/hepta-p1-paper-canary-finalizer.socket",
        "systemd/hepta-p1-paper-canary-finalizer@.service",
        "systemd/hepta-p1-paper-canary-root-coordinator.service",
        "systemd/hepta-p1-paper-terminal-cutoff@.service",
        "systemd/hepta-p1-paper-terminal-witness-verifier@.service",
        "systemd/hepta-p1-safety-soak-campaign@.service",
        "systemd/hepta-p1-safety-soak-observer-worker@.service",
        "systemd/hepta-p1-safety-soak-recorder-worker@.service",
        "systemd/hepta-p1-safety-soak@.target",
        "systemd/hepta-paper-terminal-latch-committer@.service",
        "systemd/hepta-systemd-gate.apparmor",
        "systemd/hepta-broker-network-policy-v1.json",
        "systemd/hepta-ib-paper-campaign-operator@.service",
        "systemd/hepta-ib-paper-campaign-operator@.socket",
        "systemd/hepta-ib-paper-campaign-policy-v1.json.example",
        "systemd/hepta-ib-paper-campaign-policy-local-v4.json.example",
        "systemd/hepta-ib-paper-campaign-policy-p1-v5.json.example",
        "systemd/hepta-execution-gateway-paper.env.example",
        "systemd/hepta-execution-gateway-paper-domain.env.example",
        "systemd/hepta-execution-events-ib-paper.socket",
        "systemd/hepta-execution-events-ib-paper@.socket",
        "systemd/hepta-execution-events-simulator.socket",
        "systemd/hepta-execution-events-simulator@.socket",
        "systemd/hepta-execution-ib-paper.env.example",
        "systemd/hepta-execution-ib-paper-domain.env.example",
        "systemd/hepta-execution-ib-paper.service",
        "systemd/hepta-execution-ib-paper.service.d/"
        "10-hepta-broker-egress-policy.conf",
        "systemd/hepta-execution-ib-paper.socket",
        "systemd/hepta-execution-ib-paper@.service",
        "systemd/hepta-execution-ib-paper@.service.d/"
        "10-hepta-broker-egress-policy.conf",
        "systemd/hepta-execution-ib-paper@.socket",
        "systemd/hepta-execution-simulator.env.example",
        "systemd/hepta-execution-simulator.service",
        "systemd/hepta-execution-simulator.socket",
        "systemd/hepta-execution-simulator@.service",
        "systemd/hepta-execution-simulator@.socket",
        "systemd/hepta-ib-paper-domain-authorizations-v1.json.example",
        "systemd/hepta-ib-paper-domain-preflight@.service",
        "systemd/hepta-shadow-watch-collector@.service",
        "systemd/hepta-shadow-watch-collector@.timer",
        "systemd/hepta-shadow-watch-custodian-reconcile@.service",
        "systemd/hepta-shadow-watch-custodian-reconcile@.timer",
        "systemd/hepta-shadow-watch-custodian@.service",
        "systemd/hepta-shadow-watch-domain.env.example",
        "systemd/hepta-shadow-watch-export@.service",
        "systemd/hepta-tool-gateway-domain.env.example",
        "systemd/hepta-tool-gateway.env.example",
        "systemd/hepta-tool-gateway.socket",
        "systemd/hepta-tool-gateway.service",
        "systemd/hepta-tool-gateway.service.d/"
        "10-hepta-broker-egress-policy.conf",
        "systemd/hepta-tool-gateway@.service",
        "systemd/hepta-tool-gateway@.service.d/"
        "10-hepta-broker-egress-policy.conf",
        "systemd/hepta-tool-gateway@.socket",
        "systemd/hepta-tool-session-supervisor.socket",
        "systemd/hepta-tool-session-supervisor@.socket",
        "tests/CMakeLists.txt",
        "tests/agent_simulator_e2e_tests.cpp",
        "tests/check_hepta_execution_install_tree.py",
        "tests/check_hepta_execution_provisioned_host_fixture.py",
        "tests/heptatrader-agent-os-ctest-inventory-v1.json",
        "tests/build_hepta_execution_native_vm_bundle_fixture.py",
        "tests/execution_coordinator_tests.cpp",
        "tests/execution_decision_lease_authority_tests.cpp",
        "tests/execution_event_feed_tests.cpp",
        "tests/execution_gateway_runtime_composition_tests.cpp",
        "tests/execution_service_process_e2e_tests.cpp",
        "tests/execution_service_runtime_composition_tests.cpp",
        "tests/execution_systemd_client_probe.cpp",
        "tests/execution_systemd_sandbox_probe.cpp",
        "tests/ib_authoritative_event_queue_tests.cpp",
        "tests/ib_paper_execution_runtime_composition_tests.cpp",
        "tests/ib_paper_execution_runtime_config_tests.cpp",
        "tests/ib_paper_execution_process_e2e_tests.cpp",
        "tests/ib_paper_execution_profile_tests.cpp",
        "tests/ib_paper_kill_switch_tests.cpp",
        "tests/hepta_mcp_adapter_tests.py",
        "tests/check_no_direct_broker_paths_tests.py",
        "tests/check_hepta_agent_os_install_tree.py",
        "tests/oms_journal_durability_tests.cpp",
        "tests/run_execution_gateway_soak_provenance_fixture.py",
        "tests/aggregate_hepta_execution_native_systemd_gate_fixture.py",
        "tests/run_hepta_execution_native_systemd_gate_fixture.py",
        "tests/run_hepta_execution_rootful_systemd_gate_fixture.py",
        "tests/run_hepta_paper_domain_rootful_systemd_gate_fixture.py",
        "tests/verify_hepta_execution_native_vm_bundle_fixture.py",
        "tests/build_heptatrader_clean_source_bundle_tests.py",
        "tests/native_systemd/platform-policy-v1.json",
        "systemd/hepta-service-identities-v1.json",
        "systemd/hepta-local-ai-paper-agent.service",
        "systemd/hepta-local-ai-paper-agent.env.example",
        "systemd/hepta-local-paper-safe-recover.service",
        "systemd/hepta-local-paper-safe-recover.timer",
        "systemd/hepta-local-paper-session-renew.service",
        "systemd/hepta-local-paper-session-renew.timer",
        "systemd/hepta-local-paper-supervisor.service",
        "systemd/hepta-local-paper-supervisor.timer",
        "scripts/hepta_service_identities.py",
        "tests/rootful_systemd/Dockerfile",
        "tests/rootful_systemd/hepta-systemd-entrypoint",
        "tests/rootful_systemd/hepta-rootful-systemd-gate.target",
        "tests/rootful_systemd/hepta_execution_rootful_inner_gate.py",
        "tests/agent_os_rootful_systemd/Dockerfile",
        "tests/agent_os_rootful_systemd/hepta-agent-os-systemd-entrypoint",
        "tests/agent_os_rootful_systemd/hepta-agent-os-rootful-e2e.target",
        "tests/paper_domain_rootful_systemd/Dockerfile",
        "tests/paper_domain_rootful_systemd/"
        "hepta-paper-domain-systemd-entrypoint",
        "tests/paper_domain_rootful_systemd/"
        "hepta-paper-domain-rootful-systemd.target",
        "tests/paper_domain_rootful_systemd/"
        "hepta_paper_domain_rootful_inner_gate.py",
        "tests/paper_domain_rootful_systemd/"
        "hepta_paper_inert_execution_stub.py",
        "tests/p1_dual_domain_rootful_systemd/Dockerfile",
        "tests/p1_dual_domain_rootful_systemd/"
        "hepta-p1-dual-domain-systemd-entrypoint",
        "tests/p1_dual_domain_rootful_systemd/"
        "hepta-p1-dual-domain-rootful.target",
        "tests/p1_dual_domain_rootful_systemd/"
        "hepta-p1-dual-watch@.service",
        "tests/p1_dual_domain_rootful_systemd/"
        "hepta-p1-dual-watch@.socket",
        "tests/p1_dual_domain_rootful_systemd/"
        "hepta-p1-dual-paper@.service",
        "tests/p1_dual_domain_rootful_systemd/"
        "hepta-p1-dual-paper@.socket",
        "tests/p1_dual_domain_rootful_systemd/"
        "hepta_p1_dual_domain_daemon.py",
        "tests/p1_dual_domain_rootful_systemd/"
        "hepta_p1_dual_domain_inner_gate.py",
        "tests/rootful_systemd_base/Dockerfile",
        "tests/p1_campaign_rootful_liveness_systemd/Dockerfile",
        "tests/p1_campaign_rootful_liveness_systemd/"
        "hepta-p1-liveness-systemd-entrypoint",
        "tests/p1_campaign_rootful_liveness_systemd/"
        "hepta-p1-campaign-rootful-liveness.target",
        "tests/p1_campaign_rootful_liveness_systemd/"
        "hepta-p1-liveness-coordinator.service",
        "tests/p1_campaign_rootful_liveness_systemd/"
        "hepta-p1-liveness-watchdog.service",
        "tests/p1_campaign_rootful_liveness_systemd/"
        "hepta-p1-liveness-worker.service",
        "tests/p1_campaign_rootful_liveness_systemd/"
        "hepta_p1_liveness_daemon.py",
        "tests/p1_campaign_rootful_liveness_systemd/"
        "hepta_p1_liveness_inner_gate.py",
        "tmpfiles.d/heptatrader-agent-os.conf",
        "tmpfiles.d/heptatrader-ib-paper.conf",
    }
    candidates = {root / name for name in fixed_files}
    for directory_name, suffixes in source_directories.items():
        directory = root / directory_name
        if not directory.is_dir() or directory.is_symlink():
            raise RuntimeError(
                "security-relevant source directory is missing or unsafe: "
                + directory_name
            )
        for path in directory.rglob("*"):
            if path.suffix in suffixes:
                candidates.add(path)

    entries: List[Dict[str, object]] = []
    for path in sorted(candidates, key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix()
        entries.append(snapshot_file(root, relative))
    canonical = json.dumps(
        entries, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return {
        "file_count": len(entries),
        "sha256": sha256_bytes(canonical),
        "files": entries,
    }


def provenance(
        source_root: pathlib.Path,
        build: BuildTree,
        required_build_type: str,
) -> Dict[str, object]:
    runner = stable_file_snapshot(
        source_root, "scripts/run_execution_gateway_soak.py")[0]
    bundle = None
    if not (source_root / ".git").exists():
        bundle = validated_bundle_provenance(source_root)
    return {
        "runner": runner,
        "build_configuration": validate_build_cache(
            source_root, build, required_build_type),
        "tracked_worktree_status_sha256": sha256_bytes(b"") if bundle else git_output_sha256(
            source_root,
            ["status", "--porcelain=v1", "-z", "--untracked-files=no"]),
        "tracked_diff_sha256": sha256_bytes(b"") if bundle else git_output_sha256(
            source_root, ["diff", "--binary", "HEAD"]),
        "source_bundle": bundle,
        "source_manifest": source_manifest(source_root),
    }


def parse_machine_evidence(
    output_tail: str, prefix: str
) -> Tuple[Dict[str, str], str, int]:
    """Parse one `prefix: key=value ...` line without accepting ambiguity."""
    lines = [line.strip() for line in output_tail.splitlines() if line.startswith(prefix)]
    if len(lines) != 1:
        return {}, "missing" if not lines else "duplicate_lines", len(lines)
    fields: Dict[str, str] = {}
    payload = lines[0][len(prefix) :].strip()
    for token in payload.split():
        if token.count("=") != 1:
            return {}, "malformed_token", 1
        key, value = token.split("=", 1)
        if not key or not value:
            return {}, "empty_key_or_value", 1
        if key in fields:
            return {}, "duplicate_field", 1
        fields[key] = value
    return fields, "", 1


def evaluate_evidence_contract(
        output: str,
        evidence_contract: Dict[str, object],
) -> EvidenceContractResult:
    """Evaluate one machine-evidence line against an exact field contract."""
    prefix = str(evidence_contract["prefix"])
    expected = dict(evidence_contract["fields"])
    fields, parse_error, line_count = parse_machine_evidence(output, prefix)
    missing = sorted(set(expected) - set(fields))
    unexpected = sorted(set(fields) - set(expected))
    mismatched = {
        key: {"expected": expected[key], "observed": fields.get(key)}
        for key in expected
        if key in fields and fields[key] != expected[key]
    }
    observed = line_count == 1
    satisfied = (
        observed
        and not parse_error
        and not missing
        and not unexpected
        and not mismatched
    )
    return EvidenceContractResult(
        fields,
        parse_error,
        line_count,
        missing,
        unexpected,
        mismatched,
        observed,
        satisfied,
    )


def run_one(
    source_root: pathlib.Path,
    build: BuildTree,
    binary_name: str,
    expected_binary: Dict[str, object],
    timeout_sec: float,
    sample_ms: int,
    evidence_contract: Dict[str, object],
    max_process_tree_fds: int,
    max_process_tree_threads: int,
    max_process_tree_rss_kb: int,
    max_output_bytes: int,
) -> Dict[str, object]:
    binary_relative = build.relative + "/tests/" + binary_name
    binary_logical = build.logical + "/tests/" + binary_name
    started = time.monotonic()
    high = {"rss_kb": 0, "fds": 0, "threads": 0, "processes": 0}
    timed_out = False
    output_limit_exceeded = False
    with tempfile.TemporaryFile(mode="w+b") as output:
        environment = {
            "PATH": "/usr/bin:/bin",
            "LANG": "C",
            "LC_ALL": "C",
            "TZ": "UTC",
        }
        if binary_name in {
            "hepta_execution_service_process_e2e_tests",
            "hepta_ib_paper_execution_process_e2e_tests",
        }:
            environment["HEPTA_E2E_SOAK_HOLD_MS"] = "100"
        executable_descriptor = open_pinned_executable(
            build.anchor,
            binary_relative,
            expected_binary,
            evidence_relative=binary_logical)
        executable = f"/proc/self/fd/{executable_descriptor}"
        try:
            process = subprocess.Popen(
                [executable],
                executable=executable,
                stdout=output,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                close_fds=True,
                pass_fds=(executable_descriptor,),
                cwd=source_root,
                env=environment,
            )
        finally:
            os.close(executable_descriptor)
        initial = proc_stat(process.pid)
        if initial["fds"] or initial["threads"] or initial["rss_kb"]:
            high.update(initial)
            high["processes"] = 1
        while True:
            sample = aggregate(process_tree(process.pid))
            for key, value in sample.items():
                high[key] = max(high[key], value)
            if os.fstat(output.fileno()).st_size > max_output_bytes:
                output_limit_exceeded = True
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
                break
            if process.poll() is not None:
                break
            if time.monotonic() - started > timeout_sec:
                timed_out = True
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
                break
            time.sleep(sample_ms / 1000.0)
        output_size = os.fstat(output.fileno()).st_size
        if output_size > max_output_bytes:
            output_limit_exceeded = True
        if output_limit_exceeded:
            output_bytes = b""
            output_sha256 = "not-computed-output-limit-exceeded"
            tail = "output exceeded the certified capture limit"
        else:
            output.seek(0)
            output_bytes = output.read()
            output_sha256 = sha256_bytes(output_bytes)
            tail = output_bytes[-OUTPUT_TAIL_BYTES:].decode(
                "utf-8", errors="replace")
        full_output = output_bytes.decode("utf-8", errors="replace")
    evidence_prefix = str(evidence_contract["prefix"])
    expected_evidence = dict(evidence_contract["fields"])
    evaluation = evaluate_evidence_contract(full_output, evidence_contract)
    remaining_group_members = sorted(process_group_members(process.pid))
    post_cleanup_group_members = list(remaining_group_members)
    if remaining_group_members:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        cleanup_deadline = time.monotonic() + 2.0
        while post_cleanup_group_members and time.monotonic() < cleanup_deadline:
            time.sleep(0.01)
            post_cleanup_group_members = sorted(process_group_members(process.pid))
    cleanup_succeeded = not post_cleanup_group_members
    process_resources_ok = (
        high["fds"] <= max_process_tree_fds
        and high["threads"] <= max_process_tree_threads
        and high["rss_kb"] <= max_process_tree_rss_kb
    )
    return {
        "binary": binary_logical,
        "pinned_binary": expected_binary,
        "exit_code": process.returncode,
        "timed_out": timed_out,
        "output_limit_exceeded": output_limit_exceeded,
        "output_size_bytes": output_size,
        "output_sha256": output_sha256,
        "duration_ms": int((time.monotonic() - started) * 1000),
        "high_water": high,
        "output_tail_redacted": redact_output_tail(
            tail, source_root, build.path),
        "evidence_prefix": evidence_prefix,
        "expected_evidence_fields": expected_evidence,
        "evidence_fields": evaluation.fields,
        "evidence_parse_error": evaluation.parse_error,
        "evidence_line_count": evaluation.line_count,
        "missing_evidence_fields": evaluation.missing_fields,
        "unexpected_evidence_fields": evaluation.unexpected_fields,
        "mismatched_evidence_fields": evaluation.mismatched_fields,
        "evidence_observed": evaluation.observed,
        "evidence_contract_satisfied": evaluation.satisfied,
        "remaining_process_group_members": remaining_group_members,
        "post_cleanup_process_group_members": post_cleanup_group_members,
        "process_group_cleanup_succeeded": cleanup_succeeded,
        "process_resources_within_limit": process_resources_ok,
        "passed": process.returncode == 0
        and not timed_out
        and not output_limit_exceeded
        and evaluation.satisfied
        and not remaining_group_members
        and cleanup_succeeded
        and process_resources_ok,
    }


def atomic_write_json(path: pathlib.Path, value: Dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp-" + str(os.getpid()))
    descriptor = os.open(
        temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o600
    )
    try:
        payload = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
        offset = 0
        while offset < len(payload):
            offset += os.write(descriptor, payload[offset:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(temporary, path)
    directory_flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_DIRECTORY"):
        directory_flags |= os.O_DIRECTORY
    directory = os.open(path.parent, directory_flags)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def git_head(root: pathlib.Path) -> str:
    if not (root / ".git").exists():
        head = str(validated_bundle_provenance(root)["git_head"])
    else:
        try:
            top_level = pathlib.Path(subprocess.check_output(
                ["git", "rev-parse", "--show-toplevel"],
                cwd=root, text=True,
                stderr=subprocess.DEVNULL).strip()).resolve(strict=True)
            if top_level != root:
                raise RuntimeError("Git provenance escaped the source root")
            head = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=root, text=True,
                stderr=subprocess.DEVNULL).strip()
        except (OSError, subprocess.CalledProcessError) as error:
            raise RuntimeError("Git provenance is unavailable") from error
    if re.fullmatch(r"[0-9a-f]{40,64}", head) is None:
        raise RuntimeError("git HEAD is not a canonical object ID")
    return head


def _directory_identity(metadata: os.stat_result) -> Tuple[int, ...]:
    return tuple(
        int(getattr(metadata, field)) for field in DIRECTORY_ID_FIELDS)


def _canonical_protected_directory(
        requested: pathlib.Path,
        label: str,
) -> Tuple[pathlib.Path, os.stat_result]:
    absolute = pathlib.Path(os.path.abspath(requested))
    current = pathlib.Path(absolute.anchor)
    try:
        for component in absolute.parts[1:]:
            current /= component
            metadata = current.lstat()
            if (stat.S_ISLNK(metadata.st_mode) or
                    not stat.S_ISDIR(metadata.st_mode)):
                raise RuntimeError(f"{label} has an unsafe path component")
    except OSError as error:
        raise RuntimeError(f"{label} is unavailable") from error
    resolved = absolute.resolve(strict=True)
    if resolved != absolute:
        raise RuntimeError(f"{label} is not canonical")
    metadata = resolved.lstat()
    if (metadata.st_uid != os.geteuid() or
            stat.S_IMODE(metadata.st_mode) & 0o022):
        raise RuntimeError(
            f"{label} must be caller-owned and not group/world writable")
    return resolved, metadata


def validate_build_tree(build: BuildTree) -> None:
    path, metadata = _canonical_protected_directory(
        build.path, "--build-dir")
    if (path != build.path or
            _directory_identity(metadata) != build.identity):
        raise RuntimeError("--build-dir changed during evidence collection")


def build_location(
        root: pathlib.Path,
        requested: str,
) -> BuildTree:
    """Resolve a build tree without requiring it to pollute the source tree.

    In-tree builds retain their source-relative evidence paths. An external
    build is descriptor-anchored at its canonical parent and represented by a
    stable logical label, so local absolute paths never enter the report.
    """
    path = pathlib.Path(requested)
    resolved, metadata = _canonical_protected_directory(
        path if path.is_absolute() else root / path,
        "--build-dir")
    if resolved == root:
        raise RuntimeError("--build-dir must be a distinct regular directory")
    try:
        relative = resolved.relative_to(root).as_posix()
        anchor = root
        logical = relative
    except ValueError:
        anchor = resolved.parent.resolve(strict=True)
        relative = resolved.name
        logical = EXTERNAL_BUILD_EVIDENCE_ROOT
    _relative_parts(relative)
    _relative_parts(logical)
    return BuildTree(
        resolved, anchor, relative, logical,
        _directory_identity(metadata))


def report_location(
        root: pathlib.Path,
        build: BuildTree,
        requested: str,
) -> Tuple[pathlib.Path, str]:
    path = pathlib.Path(requested)
    candidate = path if path.is_absolute() else root / path
    try:
        parent = candidate.parent.resolve(strict=True)
    except OSError as error:
        raise RuntimeError("--report parent is unavailable") from error
    if parent != build.path:
        raise RuntimeError("--report must be a direct child of --build-dir")
    validate_build_tree(build)
    if candidate.name in {"", ".", ".."}:
        raise RuntimeError("--report filename is invalid")
    resolved = parent / candidate.name
    try:
        metadata = resolved.lstat()
    except FileNotFoundError:
        metadata = None
    if metadata is not None and (
            stat.S_ISLNK(metadata.st_mode) or
            not stat.S_ISREG(metadata.st_mode)):
        raise RuntimeError("--report target is not a regular file")
    relative = build.logical + "/" + resolved.name
    _relative_parts(relative)
    return resolved, relative


def input_snapshot(
        source_root: pathlib.Path,
        build: BuildTree,
        binary_names: List[str],
        required_build_type: str,
) -> Dict[str, object]:
    return {
        "git_head": git_head(source_root),
        "provenance": provenance(
            source_root,
            build,
            required_build_type),
        "binaries": {
            name: build_file_snapshot(build, "tests/" + name)[0]
            for name in binary_names
        },
    }


def main() -> int:
    args = parse_args()
    root = pathlib.Path(__file__).resolve().parents[1].resolve(strict=True)
    build = build_location(root, args.build_dir)
    requested_report = (
        args.report or str(build.path / "execution-gateway-soak-report.json"))
    report_path, report_relative = report_location(
        root, build, requested_report)
    binaries = list(SOAK_BINARY_NAMES)
    evidence_contracts: List[Dict[str, object]] = deepcopy(
        list(SOAK_EVIDENCE_CONTRACTS))
    # In-process binaries can finish between /proc samples; their exit status
    # and exact evidence contract certify execution even when the sampler does
    # not observe a live process tree.
    minimum_observed_processes = [
        SOAK_MINIMUM_OBSERVED_PROCESSES[name]
        for name in SOAK_BINARY_NAMES
    ]
    if not (len(binaries) == len(evidence_contracts) == len(minimum_observed_processes)):
        raise RuntimeError("soak matrix configuration length mismatch")
    pre_run_snapshot = input_snapshot(
        root,
        build,
        binaries,
        args.require_build_type)
    binary_records = dict(pre_run_snapshot["binaries"])

    runner_baseline = proc_stat(os.getpid())
    rounds: List[Dict[str, object]] = []
    all_passed = True
    for index in range(args.rounds):
        checks = [
            run_one(
                root,
                build,
                binary,
                dict(binary_records[binary]),
                args.timeout_sec,
                args.sample_ms,
                contract,
                args.max_process_tree_fds,
                args.max_process_tree_threads,
                args.max_process_tree_rss_kb,
                args.max_output_bytes,
            )
            for binary, contract in zip(binaries, evidence_contracts)
        ]
        process_tree_observed = all(
            check["high_water"]["processes"] >= minimum
            for check, minimum in zip(checks, minimum_observed_processes)
        )
        runner_after = proc_stat(os.getpid())
        growth = {
            key: runner_after[key] - runner_baseline[key]
            for key in ("rss_kb", "fds", "threads")
        }
        no_orphans = len(process_tree(os.getpid())) == 1
        resource_ok = (
            growth["fds"] <= args.max_runner_fd_growth
            and growth["threads"] <= args.max_runner_thread_growth
            and growth["rss_kb"] <= args.max_runner_rss_growth_kb
        )
        passed = (
            all(check["passed"] for check in checks)
            and process_tree_observed
            and no_orphans
            and resource_ok
        )
        rounds.append(
            {
                "round": index + 1,
                "checks": checks,
                "runner_growth": growth,
                "no_orphan_descendants": no_orphans,
                "process_tree_observed": process_tree_observed,
                "resource_growth_within_limit": resource_ok,
                "passed": passed,
            }
        )
        if not passed:
            all_passed = False
            break

    post_snapshot_error = ""
    try:
        post_run_snapshot: object = input_snapshot(
            root,
            build,
            binaries,
            args.require_build_type)
    except RuntimeError as error:
        post_run_snapshot = None
        post_snapshot_error = str(error)
    inputs_stable = (
        post_run_snapshot is not None and
        pre_run_snapshot == post_run_snapshot)
    report_passed = (
        all_passed and len(rounds) == args.rounds and inputs_stable)
    report: Dict[str, object] = {
        "schema": SOAK_SCHEMA,
        "generated_at_unix_ms": int(time.time() * 1000),
        "git_head": pre_run_snapshot["git_head"],
        "provenance": {
            "pre_run": pre_run_snapshot,
            "post_run": post_run_snapshot,
            "post_snapshot_error": post_snapshot_error,
            "inputs_stable": inputs_stable,
            "source_binary_binding": SOAK_SOURCE_BINARY_BINDING,
        },
        "build_dir": build.logical,
        "soak_profile": args.soak_profile,
        "requested_rounds": args.rounds,
        "completed_rounds": len(rounds),
        "binary_inputs": binary_records,
        "evidence_contracts": evidence_contracts,
        "minimum_observed_processes": {
            binary: minimum
            for binary, minimum in zip(binaries, minimum_observed_processes)
        },
        "expected_invariants_per_round": dict(SOAK_EXPECTED_INVARIANTS),
        "all_invariants_certified": report_passed,
        "limits": {
            "timeout_sec_per_binary": args.timeout_sec,
            "max_runner_fd_growth": args.max_runner_fd_growth,
            "max_runner_thread_growth": args.max_runner_thread_growth,
            "max_runner_rss_growth_kb": args.max_runner_rss_growth_kb,
            "max_process_tree_fds": args.max_process_tree_fds,
            "max_process_tree_threads": args.max_process_tree_threads,
            "max_process_tree_rss_kb": args.max_process_tree_rss_kb,
            "max_output_bytes_per_binary": args.max_output_bytes,
        },
        "rounds": rounds,
        "passed": report_passed,
    }
    atomic_write_json(report_path, report)
    print(
        f"execution_gateway_soak: {'PASS' if report['passed'] else 'FAIL'} "
        f"rounds={len(rounds)}/{args.rounds} report={report_relative}"
    )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    try:
        exit_code = main()
    except RuntimeError as error:
        print(f"execution_gateway_soak: FAIL: {error}", file=sys.stderr)
        exit_code = 2
    raise SystemExit(exit_code)
