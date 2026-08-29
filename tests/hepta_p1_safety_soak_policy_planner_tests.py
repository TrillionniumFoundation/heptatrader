#!/usr/bin/env python3
"""Offline contract tests for the prospective P1 policy planner."""

from __future__ import annotations

import copy
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "hepta_p1_safety_soak_policy_planner.py"
SPEC = importlib.util.spec_from_file_location("p1_policy_planner", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
PLANNER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = PLANNER
SPEC.loader.exec_module(PLANNER)

STRATEGY_SHA = "sha256:" + "1" * 64
NOW_MS = 1_800_000_000_000
VALID_AFTER_MS = (
    (NOW_MS + PLANNER.LAUNCHER_EARLY_START_LEAD_MS +
     PLANNER.LAUNCHER_WARMUP_MS) // PLANNER.SLOT_INTERVAL_MS + 1
) * PLANNER.SLOT_INTERVAL_MS
LAUNCHER_START_MS = VALID_AFTER_MS - PLANNER.LAUNCHER_WARMUP_MS


class FakeBuilder:
    MINIMUM_WARMUP_MS = PLANNER.LAUNCHER_WARMUP_MS
    SLOT_INTERVAL_MS = PLANNER.SLOT_INTERVAL_MS
    MAXIMUM_ITERATIONS = PLANNER.MAXIMUM_ITERATIONS
    MAXIMUM_LATENESS_MS = PLANNER.MAXIMUM_LATENESS_MS

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def build_policy(self, **arguments):
        self.calls.append(dict(arguments))
        valid_after = arguments["start_ms"] + self.MINIMUM_WARMUP_MS
        expires = valid_after + self.SLOT_INTERVAL_MS * self.MAXIMUM_ITERATIONS
        campaign = {
            "schema": "hepta.strategy-shadow-observation-campaign.v1",
            "campaign_id": arguments["campaign_id"],
            "valid_after_ms": valid_after,
            "expires_at_ms": expires,
            "slot_interval_ms": self.SLOT_INTERVAL_MS,
            "maximum_iterations": self.MAXIMUM_ITERATIONS,
            "maximum_lateness_ms": self.MAXIMUM_LATENESS_MS,
            "shadow_only": True,
            "paper_authorized": False,
            "live_authorized": False,
            "mutation_attempted": False,
            "direct_broker_access": False,
        }
        body = {
            "schema": PLANNER.POLICY_SCHEMA, "version": 1,
            "campaign_id": arguments["campaign_id"],
            "campaign_sha256": PLANNER.digest_bytes(
                PLANNER.canonical_bytes(campaign)),
            "strategy_id": "eurusd-confirmed-momentum",
            "strategy_version": "v2",
            "strategy_sha256": arguments["expected_strategy_sha256"],
            "valid_after_ms": valid_after, "expires_at_ms": expires,
            "slot_interval_ms": self.SLOT_INTERVAL_MS,
            "maximum_iterations": self.MAXIMUM_ITERATIONS,
            "maximum_lateness_ms": self.MAXIMUM_LATENESS_MS,
            "shadow_only": True, "paper_authorized": False,
            "live_authorized": False, "mutation_attempted": False,
            "direct_broker_access": False,
        }
        return {
            **body,
            "body_sha256": PLANNER.digest_bytes(
                PLANNER.canonical_bytes(body)),
        }


def plan(builder: object | None = None) -> dict:
    return PLANNER.plan_policy(
        campaign_id="hepta-p1-formal-round101-20260803",
        launcher_start_ms=LAUNCHER_START_MS,
        strategy_path=Path("/opt/hepta/strategy.json"),
        runtime_directory=Path("/usr/libexec/hepta/strategy"),
        expected_strategy_sha256=STRATEGY_SHA,
        builder=FakeBuilder() if builder is None else builder,
        now_ms=NOW_MS)


def write(path: Path, payload: bytes, mode: int) -> None:
    path.write_bytes(payload)
    path.chmod(mode)


def baseline_fixture(root: Path) -> tuple[object, Path, Path]:
    planner_path = root / "planner"
    builder_path = root / "builder"
    write(planner_path, b"#!/usr/bin/python3\n# planner\n", 0o755)
    write(builder_path, b"#!/usr/bin/python3\n# builder\n", 0o755)
    files = [{
        "path": PLANNER.BUILDER_SOURCE_PATH,
        "sha256": PLANNER.digest_bytes(builder_path.read_bytes()),
    }, {
        "path": PLANNER.PLANNER_SOURCE_PATH,
        "sha256": PLANNER.digest_bytes(planner_path.read_bytes()),
    }]
    manifest_sha = PLANNER.digest_bytes(json.dumps(
        files, ensure_ascii=True, allow_nan=False, sort_keys=True,
        separators=(",", ":")).encode("ascii"))
    document = {
        "schema": PLANNER.SOURCE_BASELINE_SCHEMA,
        "version": "round95", "generated_at": "2026-08-03T00:00:00Z",
        "git_head": "1" * 40,
        "source_manifest": {
            "file_count": len(files), "sha256": manifest_sha,
            "files": files,
        },
        "source_baseline_frozen": True,
        "clean_checkout_certified": True,
        "release_authorized": False, "paper_authorized": False,
        "live_authorized": False, "worktree_status_entry_count": 0,
        "blocked_reason": None,
        "excluded_unsafe_tree": "compat/unsafe-direct-broker",
    }
    baseline_path = root / "source-baseline.json"
    write(baseline_path, PLANNER.canonical_bytes(document), 0o600)
    snapshot = PLANNER.load_baseline(
        baseline_path, expected_uid=os.getuid(), expected_gid=os.getgid())
    return snapshot, planner_path, builder_path


class PolicyPlannerTests(unittest.TestCase):
    def test_calls_only_pure_build_policy_without_admission_inputs(self):
        builder = FakeBuilder()
        policy = plan(builder)
        self.assertEqual(len(builder.calls), 1)
        self.assertEqual(set(builder.calls[0]), {
            "campaign_id", "start_ms", "strategy_path",
            "runtime_directory", "expected_strategy_sha256",
        })
        self.assertNotIn("admission_receipt_path", builder.calls[0])
        self.assertNotIn("marker_path", builder.calls[0])
        self.assertEqual(policy["valid_after_ms"], VALID_AFTER_MS)
        self.assertTrue(all(policy[field] is False for field in (
            "paper_authorized", "live_authorized", "mutation_attempted",
            "direct_broker_access")))

    def test_unaligned_or_late_launcher_start_fails_closed(self) -> None:
        arguments = {
            "campaign_id": "hepta-p1-formal-round101-20260803",
            "strategy_path": Path("/opt/hepta/strategy.json"),
            "runtime_directory": Path("/usr/libexec/hepta/strategy"),
            "expected_strategy_sha256": STRATEGY_SHA,
            "builder": FakeBuilder(), "now_ms": NOW_MS,
        }
        with self.assertRaisesRegex(PLANNER.PlannerError, "ALIGNMENT"):
            PLANNER.plan_policy(
                launcher_start_ms=LAUNCHER_START_MS + 1, **arguments)
        with self.assertRaisesRegex(PLANNER.PlannerError, "REQUEST_INVALID"):
            PLANNER.plan_policy(
                launcher_start_ms=NOW_MS +
                    PLANNER.LAUNCHER_EARLY_START_LEAD_MS,
                **arguments)

    def test_builder_contract_drift_is_rejected(self) -> None:
        builder = FakeBuilder()
        builder.MAXIMUM_ITERATIONS -= 1
        with self.assertRaisesRegex(PLANNER.PlannerError, "REQUEST_INVALID"):
            plan(builder)

    def test_builder_cannot_return_self_authorizing_policy(self) -> None:
        class UnsafeBuilder(FakeBuilder):
            def build_policy(self, **arguments):
                policy = super().build_policy(**arguments)
                body = dict(policy)
                body.pop("body_sha256")
                body["paper_authorized"] = True
                return {
                    **body,
                    "body_sha256": PLANNER.digest_bytes(
                        PLANNER.canonical_bytes(body)),
                }

        with self.assertRaisesRegex(PLANNER.PlannerError, "POLICY_INVALID"):
            plan(UnsafeBuilder())

    def test_clean_source_baseline_pins_both_installed_images(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT / "tests") as temporary:
            root = Path(temporary)
            root.chmod(0o700)
            snapshot, planner_path, builder_path = baseline_fixture(root)
            pins = PLANNER.validate_source_bindings(
                snapshot,
                expected_baseline_file_sha256=snapshot.file_sha256,
                planner_path=planner_path, builder_path=builder_path,
                expected_uid=os.getuid(), expected_gid=os.getgid())
            self.assertEqual(
                pins["planner"],
                PLANNER.digest_bytes(planner_path.read_bytes()))
            self.assertEqual(
                pins["builder"],
                PLANNER.digest_bytes(builder_path.read_bytes()))

    def test_dirty_baseline_or_builder_drift_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT / "tests") as temporary:
            root = Path(temporary)
            root.chmod(0o700)
            snapshot, planner_path, builder_path = baseline_fixture(root)
            changed = copy.deepcopy(snapshot.document)
            changed["worktree_status_entry_count"] = 1
            dirty_path = root / "dirty-baseline.json"
            write(dirty_path, PLANNER.canonical_bytes(changed), 0o600)
            dirty = PLANNER.load_baseline(
                dirty_path, expected_uid=os.getuid(), expected_gid=os.getgid())
            with self.assertRaisesRegex(PLANNER.PlannerError, "SOURCE_BINDING"):
                PLANNER.validate_source_bindings(
                    dirty, expected_baseline_file_sha256=dirty.file_sha256,
                    planner_path=planner_path, builder_path=builder_path,
                    expected_uid=os.getuid(), expected_gid=os.getgid())
            write(builder_path, b"#!/usr/bin/python3\n# drift\n", 0o755)
            with self.assertRaisesRegex(PLANNER.PlannerError, "SOURCE_BINDING"):
                PLANNER.validate_source_bindings(
                    snapshot,
                    expected_baseline_file_sha256=snapshot.file_sha256,
                    planner_path=planner_path, builder_path=builder_path,
                    expected_uid=os.getuid(), expected_gid=os.getgid())

    def test_symlink_and_writable_ancestor_directories_are_rejected(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "tests") as temporary:
            root = Path(temporary)
            root.chmod(0o700)
            real = root / "real"
            child = real / "child"
            child.mkdir(parents=True, mode=0o700)
            alias = root / "alias"
            alias.symlink_to(real, target_is_directory=True)
            with self.assertRaisesRegex(
                    PLANNER.PlannerError, "DIRECTORY_INVALID"):
                PLANNER.bind_directory(
                    alias / "child", expected_uid=os.getuid(),
                    expected_gid=os.getgid())
            real.chmod(0o777)
            with self.assertRaisesRegex(
                    PLANNER.PlannerError, "DIRECTORY_INVALID"):
                PLANNER.bind_directory(
                    child, expected_uid=os.getuid(),
                    expected_gid=os.getgid())

    def test_named_file_replacement_during_secure_read_is_rejected(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "tests") as temporary:
            root = Path(temporary)
            root.chmod(0o700)
            target = root / "target.json"
            replacement = root / "replacement.json"
            write(target, b"original", 0o600)
            write(replacement, b"replacement", 0o600)
            real_open = PLANNER.os.open
            target_opens = 0

            def replacing_open(path, flags, mode=0o777, *, dir_fd=None):
                nonlocal target_opens
                if path == target.name and dir_fd is not None:
                    target_opens += 1
                    if target_opens == 2:
                        os.replace(replacement, target)
                return real_open(path, flags, mode, dir_fd=dir_fd)

            with mock.patch.object(
                    PLANNER.os, "open", side_effect=replacing_open):
                with self.assertRaisesRegex(
                        PLANNER.PlannerError, "INPUT_INVALID"):
                    PLANNER.secure_read(
                        target, expected_uid=os.getuid(),
                        expected_gid=os.getgid(), modes=frozenset({0o600}))

    def test_builder_load_executes_pinned_bytes_after_path_replacement(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "tests") as temporary:
            root = Path(temporary)
            root.chmod(0o700)
            builder_path = root / "builder.py"
            write(builder_path, b"MARKER = 'PINNED'\n", 0o755)
            payload, metadata = PLANNER.secure_read(
                builder_path, expected_uid=os.getuid(),
                expected_gid=os.getgid(), modes=frozenset({0o755}))
            snapshot = PLANNER.Snapshot(
                path=builder_path, payload=payload, metadata=metadata,
                document={}, file_sha256=PLANNER.digest_bytes(payload))
            replacement = root / "builder-new.py"
            write(replacement, b"MARKER = 'REPLACED'\n", 0o755)
            os.replace(replacement, builder_path)
            loaded = PLANNER._load_builder(snapshot)
            self.assertEqual(loaded.MARKER, "PINNED")

    def test_baseline_inode_rebind_is_detected_on_reopen(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT / "tests") as temporary:
            root = Path(temporary)
            root.chmod(0o700)
            snapshot, _planner, _builder = baseline_fixture(root)
            replacement = root / "baseline-replacement.json"
            write(replacement, snapshot.payload, 0o600)
            os.replace(replacement, snapshot.path)
            with self.assertRaisesRegex(
                    PLANNER.PlannerError, "INPUT_DRIFT"):
                PLANNER._assert_snapshot(
                    snapshot, uid=os.getuid(), gid=os.getgid())

    def test_post_build_strategy_drift_is_detected(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT / "tests") as temporary:
            root = Path(temporary)
            root.chmod(0o700)
            strategy = root / "strategy.json"
            write(strategy, b"frozen-strategy", 0o600)
            payload, metadata = PLANNER.secure_read(
                strategy, expected_uid=os.getuid(), expected_gid=os.getgid(),
                modes=frozenset({0o600}))
            snapshot = PLANNER.Snapshot(
                path=strategy, payload=payload, metadata=metadata,
                document={}, file_sha256=PLANNER.digest_bytes(payload))

            class DriftingBuilder(FakeBuilder):
                def build_policy(self, **arguments):
                    write(strategy, b"drifted-strategy", 0o600)
                    return super().build_policy(**arguments)

            plan(DriftingBuilder())
            with self.assertRaisesRegex(
                    PLANNER.PlannerError, "INPUT_DRIFT"):
                PLANNER._assert_snapshot(
                    snapshot, uid=os.getuid(), gid=os.getgid())

    def test_atomic_publish_is_canonical_and_no_replace(self) -> None:
        policy = plan()
        with tempfile.TemporaryDirectory(dir=ROOT / "tests") as temporary:
            root = Path(temporary)
            root.chmod(0o700)
            output = root / "policy.json"
            snapshot = PLANNER.publish_policy(
                output, policy, expected_uid=os.getuid(),
                expected_gid=os.getgid())
            self.assertEqual(snapshot.payload, PLANNER.canonical_bytes(policy))
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
            with self.assertRaisesRegex(
                    PLANNER.PlannerError, "ALREADY_EXISTS"):
                PLANNER.publish_policy(
                    output, policy, expected_uid=os.getuid(),
                    expected_gid=os.getgid())

    def test_cli_requires_explicit_run_before_any_input_access(self) -> None:
        result = PLANNER.main([
            "--source-baseline", "/missing/source.json",
            "--expected-source-baseline-file-sha256", "sha256:" + "1" * 64,
            "--strategy", "/missing/strategy.json",
            "--runtime-directory", "/missing/runtime",
            "--expected-strategy-sha256", STRATEGY_SHA,
            "--campaign-id", "campaign", "--launcher-start-ms", "1",
            "--output", "/missing/policy.json",
        ])
        self.assertEqual(result, 3)


if __name__ == "__main__":
    unittest.main()
