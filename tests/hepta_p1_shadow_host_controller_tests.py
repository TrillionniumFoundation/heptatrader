#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import errno
import hashlib
import json
from dataclasses import replace
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
SCRIPT = SCRIPTS / "hepta_p1_shadow_host_controller.py"
SPEC = importlib.util.spec_from_file_location("p1_controller", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
controller = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = controller
SPEC.loader.exec_module(controller)

BUILDER_SCRIPT = (
    Path(__file__).resolve().parent.parent /
    "scripts" / "build_hepta_p1_observation_policy.py")
BUILDER_SPEC = importlib.util.spec_from_file_location(
    "p1_policy_builder_for_host_tests", BUILDER_SCRIPT)
assert BUILDER_SPEC is not None and BUILDER_SPEC.loader is not None
policy_builder = importlib.util.module_from_spec(BUILDER_SPEC)
sys.modules[BUILDER_SPEC.name] = policy_builder
BUILDER_SPEC.loader.exec_module(policy_builder)

READER_SCRIPT = (
    Path(__file__).resolve().parent.parent /
    "scripts" / "hepta_p1_shadow_observer_controller.py")
READER_SPEC = importlib.util.spec_from_file_location(
    "p1_reader_for_host_tests", READER_SCRIPT)
assert READER_SPEC is not None and READER_SPEC.loader is not None
reader_controller = importlib.util.module_from_spec(READER_SPEC)
sys.modules[READER_SPEC.name] = reader_controller
READER_SPEC.loader.exec_module(reader_controller)

VALIDATOR_SCRIPT = (
    Path(__file__).resolve().parent.parent /
    "scripts" / "hepta_p1_load_probe_validator.py")
VALIDATOR_SPEC = importlib.util.spec_from_file_location(
    "p1_validator_for_host_tests", VALIDATOR_SCRIPT)
assert VALIDATOR_SPEC is not None and VALIDATOR_SPEC.loader is not None
load_probe_validator = importlib.util.module_from_spec(VALIDATOR_SPEC)
sys.modules[VALIDATOR_SPEC.name] = load_probe_validator
VALIDATOR_SPEC.loader.exec_module(load_probe_validator)

DIGEST = "sha256:" + "a" * 64


def boot_id_consumers():
    return (
        (
            "policy-builder", policy_builder, policy_builder.BOOT_ID_PATH,
            policy_builder.PolicyBuildError, "P1_POLICY_BOOT_ID_INVALID",
        ),
        (
            "host-controller", controller, controller.REQUIRED_BOOT_ID,
            controller.ControllerError, "P1_LOAD_PROBE_BOOT_ID_INVALID",
        ),
        (
            "load-probe-validator", load_probe_validator,
            load_probe_validator.BOOT_ID_PATH,
            load_probe_validator.ValidationError,
            "P1_LOAD_PROBE_BOOT_ID_FILE_INVALID",
        ),
        (
            "observer-controller", reader_controller,
            reader_controller.BOOT_ID_PATH,
            reader_controller.ControllerError,
            "P1_CONTROLLER_BOOT_ID_FILE_INVALID",
        ),
    )


class StatOverride:
    def __init__(self, metadata, **changes):
        self._metadata = metadata
        self._changes = changes

    def __getattr__(self, name):
        if name in self._changes:
            return self._changes[name]
        return getattr(self._metadata, name)


def seal(body):
    return {
        **body,
        "body_sha256": "sha256:" + controller.hashlib.sha256(
            controller._canonical(body)).hexdigest(),
    }


def formal_admission(config, environment):
    first_started = 80_000
    last_started = first_started + 90 * 10_000
    return seal({
        "schema": "hepta.p1-shadow-load-probe-admission-receipt.v1",
        "version": 1,
        "status": "GO",
        "campaign_id": "p1-load-probe-round0",
        "prospective_campaign_id": config.campaign_id,
        "prospective_policy_path": str(config.policy_path),
        "authority_marker_path": str(config.authority_marker_path),
        "validated_at_ms": 1_000_000,
        "host_receipt_body_sha256": "sha256:" + "1" * 64,
        "observer_controller_status_body_sha256": "sha256:" + "2" * 64,
        "observer_state_body_sha256": "sha256:" + "3" * 64,
        "history_head_body_sha256": "sha256:" + "4" * 64,
        "probe_execution_service_epoch": "epoch-1",
        "probe_execution_service_fencing_generation": 1,
        "probe_first_collection_started_at_ms": first_started,
        "probe_first_exported_at_ms": first_started + 200,
        "probe_first_record_sha256": "sha256:" + "5" * 64,
        "probe_first_snapshot_body_sha256": "sha256:" + "6" * 64,
        "probe_last_collection_started_at_ms": last_started,
        "probe_last_exported_at_ms": last_started + 200,
        "probe_last_record_sha256": "sha256:" + "7" * 64,
        "probe_last_snapshot_body_sha256": "sha256:" + "8" * 64,
        "probe_history_record_bytes": 12345,
        "probe_audit_cursor_sequence": 91,
        "probe_audit_expected_previous_sha256": "sha256:" + "7" * 64,
        "sample_count": 91,
        "collection_cadence_ms": 10_000,
        "maximum_collection_jitter_ms": 1_000,
        "missed_sample_count": 0,
        "missed_decision_count": 0,
        "environment": environment,
        "paper_authorized": False,
        "live_authorized": False,
        "mutation_authorized": False,
        "direct_broker_access": False,
    })


class FakeClock:
    def __init__(self, wall_ms: int = 1_000_000) -> None:
        self.now_ns = 0
        self.wall_ns = wall_ms * 1_000_000

    def monotonic_ns(self) -> int:
        return self.now_ns

    def time_ns(self) -> int:
        return self.wall_ns + self.now_ns

    def sleep(self, seconds: float) -> None:
        self.now_ns += round(seconds * 1_000_000_000)


class FakeCapture:
    def __init__(self, returncode: int | None = 0) -> None:
        self.returncode = returncode
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        return self.returncode

    def communicate(self, timeout: float | None = None) -> tuple[str, str]:
        del timeout
        return ("hepta-official-source-capture: PASS\n", "")

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        if self.returncode is None:
            raise subprocess.TimeoutExpired("fake", 0)
        return self.returncode


class FakeExecutor:
    def __init__(
        self,
        clock: FakeClock,
        *,
        capture_mode: str = "valid",
        collector_advance_ns: int = 0,
        reader_error: str | None = None,
        accepted_close: bool = False,
        completion_results: list[str | None] | None = None,
        completion_ready_after_collects: int | None = None,
    ) -> None:
        self.clock = clock
        self.capture_mode = capture_mode
        self.collector_advance_ns = collector_advance_ns
        self.reader_error = reader_error
        self.accepted_close = accepted_close
        self.completion_results = (
            [None] if completion_results is None
            else list(completion_results))
        self.completion_ready_after_collects = completion_ready_after_collects
        self.collect_times: list[int] = []
        self.rotate_calls: list[tuple[int, int]] = []
        self.capture_calls: list[tuple[int, int, Path]] = []
        self.close_reasons: list[str] = []
        self.completion_calls: list[int] = []
        self.last_capture: FakeCapture | None = None

    def collect(self) -> None:
        self.collect_times.append(self.clock.monotonic_ns())
        self.clock.now_ns += self.collector_advance_ns

    def assert_reader_active(
        self,
        config: controller.Configuration,
        now_ms: int,
    ) -> None:
        del config, now_ms
        if self.reader_error is not None:
            raise controller.ControllerError(self.reader_error)

    def assert_reader_completed(
        self,
        config: controller.Configuration,
        now_ms: int,
    ) -> dict[str, object]:
        self.completion_calls.append(now_ms)
        if (
            self.completion_ready_after_collects is not None and
            len(self.collect_times) < self.completion_ready_after_collects
        ):
            raise controller.ControllerError(
                "P1_READER_COMPLETION_PENDING")
        result = (
            self.completion_results.pop(0)
            if len(self.completion_results) > 1
            else self.completion_results[0]
        )
        if result is not None:
            raise controller.ControllerError(result)
        return {
            "reader_unit": config.reader_unit,
            "reader_pid": 1234,
            "acknowledged_at_ms": now_ms,
            "controller_status_file_sha256": "sha256:" + "1" * 64,
            "controller_status_body_sha256": "sha256:" + "2" * 64,
            "observer_state_file_sha256": "sha256:" + "3" * 64,
            "observer_state_body_sha256": "sha256:" + "4" * 64,
        }

    def rotate(
        self,
        config: controller.Configuration,
        current_generation: int,
    ) -> dict[str, object]:
        self.rotate_calls.append(
            (self.clock.monotonic_ns(), current_generation))
        return {
            "schema": "hepta.shadow-watch-custodian-rotation.v1",
            "status": "ROTATED",
            "campaign_id": config.campaign_id,
            "previous_lease_generation": current_generation,
            "lease_generation": current_generation + 1,
            "previous_authority_outcome": "ROTATED",
            "paper_authorized": False,
            "live_authorized": False,
            "mutation_authorized": False,
            "direct_broker_access": False,
        }

    def start_capture(
        self,
        config: controller.Configuration,
        iteration: int,
        receipt_path: Path,
    ) -> FakeCapture:
        self.capture_calls.append(
            (self.clock.monotonic_ns(), iteration, receipt_path))
        process = FakeCapture(
            None if self.capture_mode == "hang" else 0)
        self.last_capture = process
        if self.capture_mode != "hang":
            if self.capture_mode == "invalid-json":
                receipt_path.write_text("{x", encoding="ascii")
            else:
                receipt = {
                    "schema":
                    "hepta.official-source-root-capture-receipt.v1",
                    "status": "OFFICIAL_CAPTURE_COMPLETE",
                    "observed_at_ms":
                    self.clock.time_ns() // 1_000_000,
                    "capture_helper_sha256":
                    config.capture_helper_sha256,
                    "exported_bundle_path":
                    str(config.export_root /
                        "official-source-bundle.json"),
                    "paper_authorized": (
                        self.capture_mode == "permission-true"),
                    "live_authorized": False,
                    "mutation_attempted": False,
                    "direct_broker_access": False,
                }
                receipt_path.write_text(
                    json.dumps(receipt), encoding="ascii")
        return process

    def close(
        self,
        config: controller.Configuration,
        reason: str,
    ) -> dict[str, object]:
        self.close_reasons.append(reason)
        if self.accepted_close:
            return {
                "schema": "hepta.shadow-watch-custodian-closure.v1",
                "version": 1,
                "campaign_id": config.campaign_id,
                "lease_generation": config.start_generation,
                "authoritative_revoke_outcome": "ACCEPTED",
                "local_authority_removed": True,
                "export_evidence_removed": True,
                "paper_authorized": False,
                "live_authorized": False,
                "mutation_authorized": False,
                "direct_broker_access": False,
            }
        return {
            "schema": "hepta.shadow-watch-custodian-status.v1",
            "status": "NO_ACTIVE_TRANSACTION",
            "domain_id": "alpha",
            "paper_authorized": False,
            "live_authorized": False,
            "mutation_authorized": False,
            "direct_broker_access": False,
        }


def configuration(
    root: Path,
    clock: FakeClock,
    *,
    first_slot_delta_sec: int = 200,
    maximum_runtime_sec: int | None = None,
    load_probe_runs: int | None = None,
) -> controller.Configuration:
    evidence = root / "evidence"
    export = root / "export"
    evidence.mkdir()
    export.mkdir()
    return controller.Configuration(
        campaign_id="p1-test-campaign",
        domain_config=Path("/etc/heptatrader/trust-domains/alpha.json"),
        start_generation=1,
        maximum_runtime_sec=maximum_runtime_sec,
        valid_after_ms=(
            clock.time_ns() // 1_000_000 +
            first_slot_delta_sec * 1000
        ),
        maximum_iterations=1,
        capture_lead_sec=180,
        capture_timeout_sec=150,
        evidence_root=evidence,
        export_root=export,
        reader_uid=1000,
        reader_gid=1000,
        capture_helper_sha256=DIGEST,
        load_probe_runs=load_probe_runs,
        load_probe_receipt_output=(
            root / "root-receipts" / "host.json"
            if load_probe_runs is not None else None),
        reader_unit="hepta-p1-shadow-reader-round1.service",
        reader_status_path=root / "reader-status.json",
    )


def write_completion_documents(
    config: controller.Configuration,
    clock: FakeClock,
    *,
    status_overrides: dict[str, object] | None = None,
    state_overrides: dict[str, object] | None = None,
) -> None:
    now_ms = clock.time_ns() // 1_000_000
    status_body = {
        "schema": "hepta.p1-shadow-observer-controller-status.v1",
        "version": 1,
        "campaign_id": config.campaign_id,
        "controller_pid": 1234,
        "controller_uid": config.reader_uid,
        "controller_gid": config.reader_gid,
        "state": "TERMINAL",
        "updated_at_ms": now_ms,
        "observer_status": "COMPLETE",
        "observer_outcome": "COMPLETE",
        "completed_iterations": config.maximum_iterations,
        "reason": None,
        "paper_authorized": False,
        "live_authorized": False,
        "mutation_attempted": False,
        "direct_broker_access": False,
    }
    state_body = {
        "schema": "hepta.bounded-shadow-observer-state.v1",
        "version": 1,
        "campaign_id": config.campaign_id,
        "status": "COMPLETE",
        "maximum_iterations": config.maximum_iterations,
        "completed_iterations": config.maximum_iterations,
        "segment_status": "OPEN",
        "missed_sample_count": 0,
        "missed_decision_count": 0,
        "sample_count": 100,
        "last_generated_at_ms": now_ms,
        "paper_authorized": False,
        "live_authorized": False,
        "mutation_attempted": False,
        "direct_broker_access": False,
    }
    status_body.update(status_overrides or {})
    state_body.update(state_overrides or {})
    assert config.reader_status_path is not None
    observer_path = (
        config.reader_status_path.parent / "observer" / "observer-state.json")
    observer_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    config.reader_status_path.write_bytes(
        controller._canonical(seal(status_body)))
    observer_path.write_bytes(controller._canonical(seal(state_body)))
    config.reader_status_path.chmod(0o600)
    observer_path.chmod(0o600)


def reader_unit_show(pid: int = 1234) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        [],
        0,
        "ActiveState=active\nSubState=running\nResult=success\n"
        f"ExecMainStatus=0\nMainPID={pid}\n",
        "",
    )


def write_builder_formal_documents(
    root: Path,
    config: controller.Configuration,
    environment: dict[str, object],
    *,
    validated_at_ms: int = 1_000_000,
    marker_created_at_ms: int = 1_010_000,
) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    assert config.policy_path is not None
    assert config.admission_receipt_path is not None
    assert config.authority_marker_path is not None
    runtime = root / "runtime"
    runtime.mkdir()
    strategy = root / "strategy.json"
    strategy.write_text(json.dumps({
        "schema": "hepta.confirmed-momentum-strategy.v2",
        "strategy_id": "strategy",
        "strategy_version": "2.0.0",
        "paper_only": True,
        "live_authorized": False,
    }), encoding="ascii")
    for name in (
            "hepta_eurusd_confirmed_momentum_strategy.py",
            "hepta_market_context_builder.py",
            "hepta_market_evidence_normalizer.py",
            "hepta_strategy_contracts.py"):
        (runtime / name).write_text(name + "\n", encoding="ascii")
    admission = formal_admission(config, environment)
    if validated_at_ms != 1_000_000:
        admission_body = dict(admission)
        admission_body.pop("body_sha256")
        admission_body["validated_at_ms"] = validated_at_ms
        admission = seal(admission_body)
    config.admission_receipt_path.write_bytes(
        controller._canonical(admission))
    config.admission_receipt_path.chmod(0o600)
    policy, marker = policy_builder.build_admitted_policy(
        campaign_id=config.campaign_id,
        start_ms=marker_created_at_ms,
        strategy_path=strategy,
        runtime_directory=runtime,
        expected_strategy_sha256=None,
        admission_receipt_path=config.admission_receipt_path,
        policy_path=config.policy_path,
        marker_path=config.authority_marker_path,
        environment=environment,
        now_ms=marker_created_at_ms,
        _expected_root_uid=os.geteuid(),
        _expected_root_gid=os.getegid(),
        _require_root_identity=False,
    )
    config.policy_path.write_bytes(controller._canonical(policy))
    config.authority_marker_path.write_bytes(controller._canonical(marker))
    config.policy_path.chmod(0o600)
    config.authority_marker_path.chmod(0o600)
    return policy, admission, marker


class ControllerTests(unittest.TestCase):
    def test_all_boot_id_consumers_read_real_procfs_and_keep_generic_reader_strict(
            self) -> None:
        real_path = Path("/proc/sys/kernel/random/boot_id")
        metadata = real_path.stat()
        self.assertTrue(stat.S_ISREG(metadata.st_mode))
        self.assertEqual(metadata.st_size, 0)
        self.assertEqual(stat.S_IMODE(metadata.st_mode), 0o444)
        expected = real_path.read_bytes()[:-1].decode("ascii")
        with tempfile.TemporaryDirectory() as temporary:
            empty_regular = Path(temporary) / "empty"
            empty_regular.write_bytes(b"")
            for name, module, path, error_type, reason in boot_id_consumers():
                with self.subTest(consumer=name):
                    self.assertEqual(module._read_boot_id(path, reason), expected)
                    with self.assertRaises(error_type):
                        module._secure_read(path, "GENERIC_PROC_SIZE_ZERO", 128)
                    with self.assertRaises(error_type):
                        module._secure_read(
                            empty_regular, "GENERIC_EMPTY_REGULAR", 128)

    def test_all_boot_id_consumers_reject_noncanonical_paths(self) -> None:
        alternates = (
            Path("proc/sys/kernel/random/boot_id"),
            Path("/proc/sys/kernel/random/../random/boot_id"),
            Path("/proc/self/root/proc/sys/kernel/random/boot_id"),
            Path("/tmp/boot_id"),
        )
        for name, module, _path, error_type, reason in boot_id_consumers():
            for alternate in alternates:
                with self.subTest(consumer=name, path=str(alternate)):
                    with self.assertRaises(error_type) as raised:
                        module._read_boot_id(alternate, reason)
                    self.assertEqual(str(raised.exception), reason)

    def test_all_boot_id_consumers_require_exact_canonical_bytes(self) -> None:
        valid = b"00000000-0000-0000-0000-000000000000\n"
        invalid_payloads = {
            "empty": b"",
            "truncated": valid[:-1],
            "extra-newline": valid + b"\n",
            "extra-space": valid[:-1] + b" \n",
            "uppercase": valid[:-2] + b"A\n",
            "nul": valid[:-2] + b"\x00\n",
            "non-ascii": valid[:-2] + b"\xff\n",
            "oversized": b"x" * 129,
        }
        real_read = os.read
        real_fstat = os.fstat
        boot_metadata = os.stat("/proc/sys/kernel/random/boot_id")
        boot_identity = (boot_metadata.st_dev, boot_metadata.st_ino)

        def run_with_payload(module, path, reason, payload, chunk_size=None):
            positions = {}

            def fake_read(descriptor, count):
                metadata = real_fstat(descriptor)
                if (metadata.st_dev, metadata.st_ino) != boot_identity:
                    return real_read(descriptor, count)
                position = positions.get(descriptor, 0)
                if position >= len(payload):
                    return b""
                if chunk_size is not None:
                    count = min(count, chunk_size)
                chunk = payload[position:position + count]
                positions[descriptor] = position + len(chunk)
                return chunk

            with mock.patch.object(module.os, "read", side_effect=fake_read):
                return module._read_boot_id(path, reason)

        for name, module, path, error_type, reason in boot_id_consumers():
            with self.subTest(consumer=name, payload="short-reads"):
                self.assertEqual(
                    run_with_payload(module, path, reason, valid, chunk_size=3),
                    "00000000-0000-0000-0000-000000000000",
                )
            for payload_name, payload in invalid_payloads.items():
                with self.subTest(consumer=name, payload=payload_name):
                    with self.assertRaises(error_type) as raised:
                        run_with_payload(module, path, reason, payload)
                    self.assertEqual(str(raised.exception), reason)

    def test_all_boot_id_consumers_reject_final_metadata_mismatch(self) -> None:
        real_fstat = os.fstat
        real_stat = os.stat
        boot_metadata = real_stat("/proc/sys/kernel/random/boot_id")
        boot_identity = (boot_metadata.st_dev, boot_metadata.st_ino)
        changes = {
            "type": {"st_mode": stat.S_IFDIR | 0o444},
            "mode": {"st_mode": stat.S_IFREG | 0o640},
            "uid": {"st_uid": 1},
            "gid": {"st_gid": 1},
            "nlink": {"st_nlink": 2},
            "size": {"st_size": 1},
            "device": {"st_dev": boot_metadata.st_dev + 1},
        }
        for name, module, path, error_type, reason in boot_id_consumers():
            for field, override in changes.items():
                def fake_fstat(descriptor, override=override):
                    metadata = real_fstat(descriptor)
                    if (metadata.st_dev, metadata.st_ino) == boot_identity:
                        return StatOverride(metadata, **override)
                    return metadata

                def fake_stat(stat_path, *args, override=override, **kwargs):
                    metadata = real_stat(stat_path, *args, **kwargs)
                    if stat_path == "boot_id" and kwargs.get("dir_fd") is not None:
                        return StatOverride(metadata, **override)
                    return metadata

                with self.subTest(consumer=name, field=field), \
                        mock.patch.object(
                            module.os, "fstat", side_effect=fake_fstat), \
                        mock.patch.object(
                            module.os, "stat", side_effect=fake_stat):
                    with self.assertRaises(error_type) as raised:
                        module._read_boot_id(path, reason)
                    self.assertEqual(str(raised.exception), reason)

    def test_all_boot_id_consumers_reject_directory_and_rebinding_drift(
            self) -> None:
        real_fstat = os.fstat
        real_stat = os.stat
        parent_metadata = real_stat("/proc/sys/kernel/random")
        parent_identity = (parent_metadata.st_dev, parent_metadata.st_ino)
        boot_metadata = real_stat("/proc/sys/kernel/random/boot_id")
        boot_identity = (boot_metadata.st_dev, boot_metadata.st_ino)

        for name, module, path, error_type, reason in boot_id_consumers():
            def writable_parent(descriptor):
                metadata = real_fstat(descriptor)
                if (metadata.st_dev, metadata.st_ino) == parent_identity:
                    return StatOverride(
                        metadata, st_mode=metadata.st_mode | 0o022)
                return metadata

            with self.subTest(consumer=name, drift="writable-parent"), \
                    mock.patch.object(
                        module.os, "fstat", side_effect=writable_parent):
                with self.assertRaises(error_type) as raised:
                    module._read_boot_id(path, reason)
                self.assertEqual(str(raised.exception), reason)

            first_parent_fd = None

            def rebound_parent(descriptor):
                nonlocal first_parent_fd
                metadata = real_fstat(descriptor)
                if (metadata.st_dev, metadata.st_ino) == parent_identity:
                    if first_parent_fd is None:
                        first_parent_fd = descriptor
                    elif descriptor != first_parent_fd:
                        return StatOverride(
                            metadata, st_ino=metadata.st_ino + 1)
                return metadata

            with self.subTest(consumer=name, drift="rebound-parent"), \
                    mock.patch.object(
                        module.os, "fstat", side_effect=rebound_parent):
                with self.assertRaises(error_type) as raised:
                    module._read_boot_id(path, reason)
                self.assertEqual(str(raised.exception), reason)

            for target, drift_name in ((2, "during-first-read"),
                                       (4, "during-rebound-read")):
                boot_fstat_calls = 0

                def changed_during_read(
                        descriptor, target=target):
                    nonlocal boot_fstat_calls
                    metadata = real_fstat(descriptor)
                    if (metadata.st_dev, metadata.st_ino) == boot_identity:
                        boot_fstat_calls += 1
                        if boot_fstat_calls == target:
                            return StatOverride(
                                metadata,
                                st_mtime_ns=metadata.st_mtime_ns + 1)
                    return metadata

                with self.subTest(consumer=name, drift=drift_name), \
                        mock.patch.object(
                            module.os, "fstat", side_effect=changed_during_read):
                    with self.assertRaises(error_type) as raised:
                        module._read_boot_id(path, reason)
                    self.assertEqual(str(raised.exception), reason)

            for target, drift_name in (
                    (1, "pre-open-entry"),
                    (2, "post-read-entry"),
                    (3, "canonical-reopen-entry"),
                    (4, "rebound-post-read-entry"),
            ):
                entry_calls = 0

                def changed_entry(
                        stat_path, *args, target=target, **kwargs):
                    nonlocal entry_calls
                    metadata = real_stat(stat_path, *args, **kwargs)
                    if stat_path == "boot_id" and kwargs.get("dir_fd") is not None:
                        entry_calls += 1
                        if entry_calls == target:
                            return StatOverride(
                                metadata, st_ino=metadata.st_ino + 1)
                    return metadata

                with self.subTest(consumer=name, drift=drift_name), \
                        mock.patch.object(
                            module.os, "stat", side_effect=changed_entry):
                    with self.assertRaises(error_type) as raised:
                        module._read_boot_id(path, reason)
                    self.assertEqual(str(raised.exception), reason)

    def test_all_boot_id_consumers_reject_rebound_content_drift(self) -> None:
        real_read = os.read
        real_fstat = os.fstat
        boot_metadata = os.stat("/proc/sys/kernel/random/boot_id")
        boot_identity = (boot_metadata.st_dev, boot_metadata.st_ino)
        payloads = (
            b"00000000-0000-0000-0000-000000000000\n",
            b"11111111-1111-1111-1111-111111111111\n",
        )
        for name, module, path, error_type, reason in boot_id_consumers():
            positions = {}
            assigned = {}

            def changed_read(descriptor, count):
                metadata = real_fstat(descriptor)
                if (metadata.st_dev, metadata.st_ino) != boot_identity:
                    return real_read(descriptor, count)
                if descriptor not in assigned:
                    assigned[descriptor] = payloads[len(assigned)]
                payload = assigned[descriptor]
                position = positions.get(descriptor, 0)
                if position >= len(payload):
                    return b""
                chunk = payload[position:position + count]
                positions[descriptor] = position + len(chunk)
                return chunk

            with self.subTest(consumer=name), mock.patch.object(
                    module.os, "read", side_effect=changed_read):
                with self.assertRaises(error_type) as raised:
                    module._read_boot_id(path, reason)
                self.assertEqual(str(raised.exception), reason)

    def test_boot_id_reader_requires_linux_no_follow_flags(self) -> None:
        module = policy_builder
        path = module.BOOT_ID_PATH
        reason = "P1_POLICY_BOOT_ID_INVALID"
        original_open = os.open
        opened = []

        def capture_open(open_path, flags, mode=0o777, *, dir_fd=None):
            opened.append((open_path, flags, dir_fd))
            return original_open(open_path, flags, mode, dir_fd=dir_fd)

        with mock.patch.object(module.os, "open", side_effect=capture_open):
            module._read_boot_id(path, reason)
        self.assertTrue(opened)
        for open_path, flags, _dir_fd in opened:
            self.assertNotEqual(flags & os.O_NOFOLLOW, 0, open_path)
            if open_path == "boot_id":
                self.assertNotEqual(flags & os.O_NONBLOCK, 0)
            else:
                self.assertNotEqual(flags & os.O_DIRECTORY, 0)
        with mock.patch.object(module.os, "O_NOFOLLOW", None):
            with self.assertRaisesRegex(
                    module.PolicyBuildError, reason):
                module._read_boot_id(path, reason)

    def test_all_boot_id_consumers_close_every_fd_and_preserve_stable_errors(
            self) -> None:
        real_open = os.open
        real_close = os.close
        real_fstat = os.fstat
        real_read = os.read
        boot_metadata = os.stat("/proc/sys/kernel/random/boot_id")
        boot_identity = (boot_metadata.st_dev, boot_metadata.st_ino)

        for name, module, path, error_type, reason in boot_id_consumers():
            for scenario in (
                    "intermediate-close-error",
                    "final-close-error",
                    "ambient-final-close-error",
                    "read-and-cleanup-close-error",
            ):
                opened = []
                close_attempts = {}
                current_generation = {}
                close_without_open = []
                open_generation = 0
                close_index = 0
                injected_token = None
                read_injected = False
                close_error = None
                read_error = None
                before_fd_count = len(os.listdir("/proc/self/fd"))

                def fake_open(
                        open_path, flags, mode=0o777, *, dir_fd=None):
                    nonlocal open_generation
                    descriptor = real_open(
                        open_path, flags, mode, dir_fd=dir_fd)
                    open_generation += 1
                    current_generation[descriptor] = open_generation
                    opened.append(descriptor)
                    return descriptor

                def fake_close(descriptor):
                    nonlocal close_error, close_index, injected_token
                    close_index += 1
                    generation = current_generation.get(descriptor)
                    if generation is None:
                        close_without_open.append(descriptor)
                    token = (descriptor, generation)
                    close_attempts[token] = close_attempts.get(token, 0) + 1
                    metadata = real_fstat(descriptor)
                    is_boot_id = (
                        metadata.st_dev, metadata.st_ino) == boot_identity
                    real_close(descriptor)
                    current_generation.pop(descriptor, None)
                    inject = (
                        (scenario == "intermediate-close-error" and
                         close_index == 1) or
                        (scenario in {
                            "final-close-error",
                            "ambient-final-close-error",
                            "read-and-cleanup-close-error",
                        } and is_boot_id and injected_token is None)
                    )
                    if inject:
                        injected_token = token
                        close_error = OSError(
                            errno.EIO, "injected close-after-release")
                        raise close_error

                def fake_read(descriptor, count):
                    nonlocal read_error, read_injected
                    metadata = real_fstat(descriptor)
                    if (
                            scenario == "read-and-cleanup-close-error" and
                            (metadata.st_dev, metadata.st_ino) == boot_identity and
                            not read_injected):
                        read_injected = True
                        read_error = OSError(errno.EIO, "injected read failure")
                        raise read_error
                    return real_read(descriptor, count)

                def invoke_reader():
                    if scenario == "ambient-final-close-error":
                        try:
                            raise RuntimeError("ambient handled exception")
                        except RuntimeError:
                            return module._read_boot_id(path, reason)
                    return module._read_boot_id(path, reason)

                with self.subTest(consumer=name, scenario=scenario), \
                        mock.patch.object(
                            module.os, "open", side_effect=fake_open), \
                        mock.patch.object(
                            module.os, "close", side_effect=fake_close), \
                        mock.patch.object(
                            module.os, "read", side_effect=fake_read):
                    with self.assertRaises(error_type) as raised:
                        invoke_reader()
                    self.assertEqual(str(raised.exception), reason)

                live = []
                for descriptor in set(opened):
                    try:
                        real_fstat(descriptor)
                    except OSError as error:
                        self.assertEqual(error.errno, errno.EBADF)
                    else:
                        live.append(descriptor)
                        real_close(descriptor)
                self.assertEqual(live, [])
                self.assertEqual(
                    len(os.listdir("/proc/self/fd")), before_fd_count)
                self.assertEqual(close_without_open, [])
                self.assertIsNotNone(injected_token)
                self.assertEqual(close_attempts[injected_token], 1)
                if scenario == "read-and-cleanup-close-error":
                    self.assertTrue(read_injected)
                    self.assertIs(raised.exception.__cause__, read_error)
                else:
                    self.assertIs(raised.exception.__cause__, close_error)

    def test_environment_binding_call_sites_use_dedicated_boot_id_reader(
            self) -> None:
        dummy = Path("/")
        sentinel = "BOOT_ID_READER_SENTINEL"
        builder_arguments = {
            "boot_id_path": policy_builder.BOOT_ID_PATH,
            "audit_journal": dummy,
            "collector": dummy,
            "exporter": dummy,
            "heptactl": dummy,
            "gateway": dummy,
            "custodian": dummy,
            "observer": dummy,
            "host_controller": dummy,
            "domain_config": dummy,
            "gateway_profile": dummy,
            "gateway_socket": dummy,
        }
        validator_arguments = dict(builder_arguments)
        validator_arguments.pop("boot_id_path")
        validator_arguments["boot_id_path"] = load_probe_validator.BOOT_ID_PATH

        with mock.patch.object(
                policy_builder, "_read_boot_id",
                side_effect=policy_builder.PolicyBuildError(sentinel)):
            with self.assertRaisesRegex(
                    policy_builder.PolicyBuildError, sentinel):
                policy_builder.current_environment_binding(**builder_arguments)
        with mock.patch.object(
                controller, "_read_boot_id",
                side_effect=controller.ControllerError(sentinel)):
            with self.assertRaisesRegex(controller.ControllerError, sentinel):
                controller._load_probe_environment_binding()
        with mock.patch.object(
                load_probe_validator, "_read_boot_id",
                side_effect=load_probe_validator.ValidationError(sentinel)):
            with self.assertRaisesRegex(
                    load_probe_validator.ValidationError, sentinel):
                load_probe_validator.current_environment_binding(
                    **validator_arguments)
        with mock.patch.object(
                reader_controller, "_read_boot_id",
                side_effect=reader_controller.ControllerError(sentinel)):
            with self.assertRaisesRegex(
                    reader_controller.ControllerError, sentinel):
                reader_controller.current_environment_binding(
                    audit_journal_device=1,
                    audit_journal_inode=2,
                    domain_config_sha256="sha256:" + "a" * 64,
                    gateway_process_profile_sha256=
                        reader_controller.digest_bytes(
                            reader_controller.
                            ALPHA_GATEWAY_PROCESS_PROFILE_BYTES),
                )

    def test_root_gateway_bindings_use_full_rebind_sequence(self) -> None:
        status_output = (
            "ActiveState=active\nSubState=running\n" +
            "InvocationID=" + "a" * 32 + "\n" +
            "MainPID=123\nExecMainStartTimestampMonotonic=456\n")
        completed = subprocess.CompletedProcess(
            [], 0, stdout=status_output, stderr="")
        profile = mock.Mock(raw=b"profile\n")
        process_profile = mock.Mock(
            pid_directory_metadata=(1, 2, 3), starttime_ticks=789,
            canonical_projection=b"process-profile\n")
        process_identity = mock.Mock(
            pid_directory_metadata=(1, 2, 3), starttime_ticks=789)
        gateway_socket = mock.Mock(metadata=(11, 12))

        for module in (policy_builder, load_probe_validator):
            events: list[str] = []

            def status(*_args, **_kwargs):
                events.append("status")
                return completed

            def process_profile_read(_pid):
                events.append("process-profile")
                return process_profile

            def socket_read(_path):
                events.append("socket")
                return gateway_socket

            def profile_read(_path):
                events.append("profile")
                return profile

            def process_identity_read(_pid):
                events.append("process-identity")
                return process_identity

            with self.subTest(consumer=module.__name__), \
                    mock.patch.object(
                        module.subprocess, "run", side_effect=status), \
                    mock.patch.object(
                        module, "read_alpha_gateway_process_profile",
                        side_effect=process_profile_read), \
                    mock.patch.object(
                        module, "read_alpha_gateway_socket",
                        side_effect=socket_read), \
                    mock.patch.object(
                        module, "read_alpha_gateway_profile",
                        side_effect=profile_read), \
                    mock.patch.object(
                        module, "read_alpha_gateway_process_identity",
                        side_effect=process_identity_read):
                result = module._live_gateway_identity(
                    Path("/run/hepta-agent-alpha/tools.sock"),
                    Path("/etc/heptatrader/trust-domains/alpha.env"),
                    profile,
                )
            self.assertEqual(events, [
                "status", "process-profile", "socket", "profile", "status",
                "process-identity", "socket",
            ])
            self.assertEqual(result["gateway_main_pid"], 123)
            self.assertEqual(result["gateway_socket_device"], 11)
            self.assertEqual(
                result["gateway_process_profile_sha256"],
                module.digest_bytes(process_profile.canonical_projection),
            )

        events = []

        def host_status(*_args, **_kwargs):
            events.append("status")
            return completed

        def host_process_profile(_pid):
            events.append("process-profile")
            return process_profile

        def host_socket(_path):
            events.append("socket")
            return gateway_socket

        def host_profile(_path):
            events.append("profile")
            return profile

        def host_process_identity(_pid):
            events.append("process-identity")
            return process_identity

        with mock.patch.object(
                controller.CommandExecutor, "_run",
                side_effect=host_status), \
                mock.patch.object(
                    controller, "read_alpha_gateway_process_profile",
                    side_effect=host_process_profile), \
                mock.patch.object(
                    controller, "read_alpha_gateway_socket",
                    side_effect=host_socket), \
                mock.patch.object(
                    controller, "read_alpha_gateway_profile",
                    side_effect=host_profile), \
                mock.patch.object(
                    controller, "read_alpha_gateway_process_identity",
                    side_effect=host_process_identity):
            result = controller._live_gateway_identity(profile)
        self.assertEqual(events, [
            "status", "process-profile", "socket", "profile", "status",
            "process-identity", "socket",
        ])
        self.assertEqual(result["gateway_main_pid"], 123)
        self.assertEqual(
            result["gateway_process_profile_sha256"],
            "sha256:" + hashlib.sha256(
                process_profile.canonical_projection).hexdigest(),
        )

    def test_load_probe_runs_exactly_91_without_other_actions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            clock = FakeClock()
            config = configuration(
                Path(temporary),
                clock,
                first_slot_delta_sec=-1,
                load_probe_runs=controller.LOAD_PROBE_REQUIRED_RUNS,
            )
            executor = FakeExecutor(clock, collector_advance_ns=125_000_000)
            result = controller.Controller(
                config, clock=clock, executor=executor).run()
        self.assertEqual(result.status, "LOAD_PROBE_COMPLETE")
        self.assertEqual(result.collector_runs, 91)
        self.assertEqual(result.completed_iterations, 0)
        self.assertEqual(result.generation, 1)
        self.assertEqual(result.probe_duration_ms, 910_000)
        self.assertEqual(result.maximum_start_lateness_ms, 0)
        self.assertEqual(result.maximum_collector_elapsed_ms, 125)
        self.assertEqual(
            executor.collect_times,
            [
                index * controller.COLLECTOR_INTERVAL_NS
                for index in range(91)
            ],
        )
        self.assertEqual(executor.rotate_calls, [])
        self.assertEqual(executor.capture_calls, [])

    def test_environment_guard_rejects_pre_collection_root_binding_drift(
            self) -> None:
        baseline = {
            "audit_journal_device": 1,
            "audit_journal_inode": 2,
            "domain_config_sha256": "sha256:" + "1" * 64,
        }
        for load_probe_runs in (controller.LOAD_PROBE_REQUIRED_RUNS, None):
            for field, replacement in (
                ("audit_journal_device", 3),
                ("audit_journal_inode", 4),
                ("domain_config_sha256", "sha256:" + "2" * 64),
            ):
                with self.subTest(
                        load_probe_runs=load_probe_runs, field=field), \
                        tempfile.TemporaryDirectory() as temporary:
                    clock = FakeClock()
                    config = configuration(
                        Path(temporary), clock,
                        load_probe_runs=load_probe_runs)
                    executor = FakeExecutor(clock)
                    drifted = dict(baseline)
                    drifted[field] = replacement
                    guarded = controller.Controller(
                        config,
                        clock=clock,
                        executor=executor,
                        expected_environment=baseline,
                        environment_provider=lambda value=drifted: dict(value),
                    )
                    with self.assertRaisesRegex(
                            controller.ControllerError,
                            "P1_CAMPAIGN_ENVIRONMENT_DRIFT"):
                        guarded._collect()
                    self.assertEqual(executor.collect_times, [])

    def test_environment_guard_rejects_post_collection_root_binding_drift(
            self) -> None:
        baseline = {
            "audit_journal_device": 1,
            "audit_journal_inode": 2,
            "domain_config_sha256": "sha256:" + "1" * 64,
        }
        for load_probe_runs in (controller.LOAD_PROBE_REQUIRED_RUNS, None):
            with self.subTest(load_probe_runs=load_probe_runs), \
                    tempfile.TemporaryDirectory() as temporary:
                clock = FakeClock()
                config = configuration(
                    Path(temporary), clock,
                    load_probe_runs=load_probe_runs)
                executor = FakeExecutor(clock)
                drifted = dict(baseline)
                drifted["audit_journal_inode"] = 3
                environments = iter((dict(baseline), drifted))
                guarded = controller.Controller(
                    config,
                    clock=clock,
                    executor=executor,
                    expected_environment=baseline,
                    environment_provider=lambda: next(environments),
                )
                with self.assertRaisesRegex(
                        controller.ControllerError,
                        "P1_CAMPAIGN_ENVIRONMENT_DRIFT"):
                    guarded._collect()
                self.assertEqual(executor.collect_times, [0])
                self.assertEqual(executor.rotate_calls, [])
                self.assertEqual(executor.capture_calls, [])

    def test_environment_guard_wraps_every_collection_for_both_modes(
            self) -> None:
        baseline = {"binding": "fixed"}
        for load_probe_runs in (controller.LOAD_PROBE_REQUIRED_RUNS, None):
            with self.subTest(load_probe_runs=load_probe_runs), \
                    tempfile.TemporaryDirectory() as temporary:
                clock = FakeClock()
                config = configuration(
                    Path(temporary), clock,
                    load_probe_runs=load_probe_runs)
                executor = FakeExecutor(clock)
                calls = 0

                def environment_provider() -> dict[str, str]:
                    nonlocal calls
                    calls += 1
                    return dict(baseline)

                result = controller.Controller(
                    config,
                    clock=clock,
                    executor=executor,
                    expected_environment=baseline,
                    environment_provider=environment_provider,
                ).run()
                self.assertIn(
                    result.status,
                    {"LOAD_PROBE_COMPLETE", "ITERATIONS_COMPLETE"},
                )
                self.assertEqual(calls, 2 * len(executor.collect_times))

    def test_load_probe_collector_budget_fails_without_catchup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            clock = FakeClock()
            config = configuration(
                Path(temporary),
                clock,
                first_slot_delta_sec=-1,
                load_probe_runs=controller.LOAD_PROBE_REQUIRED_RUNS,
            )
            executor = FakeExecutor(
                clock,
                collector_advance_ns=(
                    controller.LOAD_PROBE_MAXIMUM_COLLECTOR_NS + 1),
            )
            with self.assertRaisesRegex(
                    controller.ControllerError,
                    "P1_LOAD_PROBE_COLLECTOR_BUDGET_EXCEEDED"):
                controller.Controller(
                    config, clock=clock, executor=executor).run()
        self.assertEqual(executor.collect_times, [0])
        self.assertEqual(executor.rotate_calls, [])
        self.assertEqual(executor.capture_calls, [])

    def test_load_probe_requires_exact_fixed_run_count(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            clock = FakeClock()
            config = configuration(
                Path(temporary), clock, load_probe_runs=90)
            with self.assertRaisesRegex(
                    controller.ControllerError,
                    "P1_LOAD_PROBE_RUN_COUNT_INVALID"):
                controller.Controller(
                    config,
                    clock=clock,
                    executor=FakeExecutor(clock),
                ).run()

    def test_load_probe_receipt_is_canonical_and_closure_bound(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            clock = FakeClock()
            config = configuration(
                Path(temporary),
                clock,
                load_probe_runs=controller.LOAD_PROBE_REQUIRED_RUNS,
            )
            close_result = {
                "schema": "hepta.shadow-watch-custodian-closure.v1",
                "campaign_id": config.campaign_id,
                "lease_generation": config.start_generation,
                "authoritative_revoke_outcome": "ACCEPTED",
                "local_authority_removed": True,
                "export_evidence_removed": True,
                "paper_authorized": False,
                "live_authorized": False,
                "mutation_authorized": False,
                "direct_broker_access": False,
            }
            result = controller.LoopResult(
                status="LOAD_PROBE_COMPLETE",
                generation=1,
                collector_runs=91,
                completed_iterations=0,
                probe_duration_ms=900_125,
                maximum_start_lateness_ms=0,
                maximum_collector_elapsed_ms=125,
            )
            receipt = controller._load_probe_receipt(
                config,
                result,
                {
                    "boot_id": "00000000-0000-0000-0000-000000000000",
                    "audit_journal_device": 1,
                    "audit_journal_inode": 2,
                    "collector_sha256": DIGEST,
                    "exporter_sha256": DIGEST,
                    "heptactl_sha256": DIGEST,
                    "gateway_sha256": DIGEST,
                    "custodian_sha256": DIGEST,
                    "observer_sha256": DIGEST,
                    "host_controller_sha256": DIGEST,
                },
                close_result,
            )
        body = {
            key: value for key, value in receipt.items()
            if key != "body_sha256"
        }
        self.assertEqual(
            receipt["body_sha256"],
            "sha256:" + controller.hashlib.sha256(
                controller._canonical(body)).hexdigest(),
        )
        self.assertEqual(receipt["collector_runs"], 91)
        self.assertEqual(receipt["close_result"], close_result)

    def test_load_probe_receipt_output_is_exclusive_and_mode_0600(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            directory.chmod(0o700)
            output = directory / "host-receipt.json"
            document = seal({"schema": "test"})
            with mock.patch.object(
                    controller, "ROOT_UID", directory.stat().st_uid), \
                    mock.patch.object(
                        controller, "ROOT_GID", directory.stat().st_gid):
                controller._write_root_exclusive(output, document)
                self.assertEqual(output.stat().st_mode & 0o777, 0o600)
                self.assertEqual(output.read_bytes(), controller._canonical(document))
                with self.assertRaisesRegex(
                        controller.ControllerError,
                        "P1_LOAD_PROBE_RECEIPT_ALREADY_EXISTS"):
                    controller._write_root_exclusive(output, document)

    def test_load_probe_failure_still_closes_custodian(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            clock = FakeClock()
            config = configuration(
                Path(temporary),
                clock,
                load_probe_runs=controller.LOAD_PROBE_REQUIRED_RUNS,
            )
            executor = FakeExecutor(
                clock,
                collector_advance_ns=(
                    controller.LOAD_PROBE_MAXIMUM_COLLECTOR_NS + 1),
            )
            with mock.patch.object(
                    controller, "_validate_configuration"), \
                    mock.patch.object(
                        controller, "_install_signal_handlers",
                        return_value={}), \
                    mock.patch.object(
                        controller, "_load_probe_environment_binding",
                        return_value={"binding": "fixed"}), \
                    mock.patch.object(
                        controller, "_configuration",
                        return_value=config), \
                    mock.patch.object(
                        controller, "CommandExecutor",
                        return_value=executor), \
                    mock.patch.object(
                        controller, "Clock",
                        return_value=clock), \
                    mock.patch.object(
                        controller, "_parser") as parser:
                parser.return_value.parse_args.return_value = object()
                rc = controller.main()
        self.assertEqual(rc, 78)
        self.assertEqual(executor.close_reasons, ["operator-request"])

    def test_load_probe_validation_failure_still_closes_custodian(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            clock = FakeClock()
            config = configuration(
                Path(temporary),
                clock,
                load_probe_runs=controller.LOAD_PROBE_REQUIRED_RUNS,
            )
            executor = FakeExecutor(clock)
            with mock.patch.object(
                    controller,
                    "_validate_configuration",
                    side_effect=controller.ControllerError(
                        "P1_INPUT_INVALID")), \
                    mock.patch.object(
                        controller, "_configuration",
                        return_value=config), \
                    mock.patch.object(
                        controller, "CommandExecutor",
                        return_value=executor), \
                    mock.patch.object(
                        controller, "Clock",
                        return_value=clock), \
                    mock.patch.object(
                        controller, "_parser") as parser:
                parser.return_value.parse_args.return_value = object()
                rc = controller.main()
        self.assertEqual(rc, 78)
        self.assertEqual(executor.close_reasons, ["operator-request"])

    def test_formal_preflight_failure_still_closes_provisioned_watch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            clock = FakeClock()
            config = configuration(Path(temporary), clock)
            executor = FakeExecutor(clock)
            with mock.patch.object(
                    controller,
                    "_validate_configuration",
                    side_effect=controller.ControllerError(
                        "P1_FORMAL_ADMISSION_BINDING_INVALID")), \
                    mock.patch.object(
                        controller, "_configuration", return_value=config), \
                    mock.patch.object(
                        controller, "CommandExecutor", return_value=executor), \
                    mock.patch.object(controller, "Clock", return_value=clock), \
                    mock.patch.object(controller, "_parser") as parser:
                parser.return_value.parse_args.return_value = object()
                rc = controller.main()
        self.assertEqual(rc, 78)
        self.assertEqual(executor.close_reasons, ["operator-request"])

    def test_absolute_ten_second_collection_and_capture(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            clock = FakeClock()
            config = configuration(Path(temporary), clock)
            executor = FakeExecutor(clock)
            result = controller.Controller(
                config, clock=clock, executor=executor).run()
        self.assertEqual(result.status, "ITERATIONS_COMPLETE")
        self.assertEqual(result.completed_iterations, 1)
        self.assertEqual(
            result.reader_completion,
            {
                "reader_unit": config.reader_unit,
                "reader_pid": 1234,
                "acknowledged_at_ms": 1_200_000,
                "controller_status_file_sha256": "sha256:" + "1" * 64,
                "controller_status_body_sha256": "sha256:" + "2" * 64,
                "observer_state_file_sha256": "sha256:" + "3" * 64,
                "observer_state_body_sha256": "sha256:" + "4" * 64,
            },
        )
        self.assertEqual(
            executor.collect_times,
            [
                index * controller.COLLECTOR_INTERVAL_NS
                for index in range(21)
            ],
        )
        self.assertEqual(executor.capture_calls[0][0], 20_000_000_000)
        self.assertEqual(executor.capture_calls[0][1], 1)
        self.assertIn("p1-test-campaign", executor.capture_calls[0][2].name)
        self.assertIn("000001", executor.capture_calls[0][2].name)
        self.assertEqual(executor.rotate_calls, [])

    def test_final_ack_waits_for_reader_lag_without_skipping_cadence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            clock = FakeClock()
            config = configuration(Path(temporary), clock)
            executor = FakeExecutor(
                clock,
                completion_results=[
                    "P1_READER_COMPLETION_PENDING",
                    "P1_READER_COMPLETION_PENDING",
                    None,
                ],
            )
            result = controller.Controller(
                config, clock=clock, executor=executor).run()
        self.assertEqual(result.status, "ITERATIONS_COMPLETE")
        self.assertEqual(len(executor.completion_calls), 3)
        self.assertEqual(
            executor.completion_calls,
            [1_200_000, 1_200_100, 1_200_200],
        )
        self.assertEqual(executor.collect_times[-1], 200_000_000_000)
        self.assertLess(
            clock.monotonic_ns(),
            210_000_000_000,
        )

    def test_phase_misaligned_final_ack_keeps_collector_cadence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            clock = FakeClock()
            config = configuration(
                Path(temporary), clock, first_slot_delta_sec=205)
            executor = FakeExecutor(
                clock,
                completion_ready_after_collects=22,
            )
            result = controller.Controller(
                config, clock=clock, executor=executor).run()
        self.assertEqual(result.status, "ITERATIONS_COMPLETE")
        self.assertEqual(executor.collect_times[-1], 210_000_000_000)
        self.assertIn(210_000_000_000, executor.collect_times)
        self.assertEqual(clock.monotonic_ns(), 210_000_000_000)
        self.assertEqual(executor.completion_calls[-1], 1_210_000)

    def test_final_ack_times_out_only_after_continuing_cadence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            clock = FakeClock()
            config = configuration(
                Path(temporary), clock, first_slot_delta_sec=205)
            executor = FakeExecutor(
                clock,
                completion_results=["P1_READER_COMPLETION_PENDING"],
            )
            with self.assertRaisesRegex(
                    controller.ControllerError,
                    "P1_READER_COMPLETION_TIMEOUT"):
                controller.Controller(
                    config, clock=clock, executor=executor).run()
        self.assertEqual(executor.collect_times[-1], 230_000_000_000)
        self.assertEqual(
            executor.collect_times,
            [index * controller.COLLECTOR_INTERVAL_NS for index in range(24)],
        )
        self.assertEqual(clock.monotonic_ns(), 235_000_000_000)

    def test_final_ack_keeps_due_rotation_in_main_loop(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            clock = FakeClock()
            config = configuration(
                Path(temporary), clock, first_slot_delta_sec=2_995)
            executor = FakeExecutor(
                clock,
                completion_ready_after_collects=301,
            )
            result = controller.Controller(
                config, clock=clock, executor=executor).run()
        self.assertEqual(result.status, "ITERATIONS_COMPLETE")
        self.assertEqual(result.generation, 2)
        self.assertEqual(
            executor.rotate_calls,
            [(controller.ROTATION_INTERVAL_NS, 1)],
        )
        self.assertEqual(executor.collect_times[-1], 3_000_000_000_000)

    def test_final_ack_accepts_exact_reader_and_observer_commit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            clock = FakeClock()
            config = configuration(Path(temporary), clock)
            write_completion_documents(config, clock)
            executor = controller.CommandExecutor()
            with mock.patch.object(
                    executor, "_run", return_value=reader_unit_show()):
                completion = executor.assert_reader_completed(
                    config, clock.time_ns() // 1_000_000)
            self.assertEqual(
                set(completion),
                {
                    "reader_unit", "reader_pid", "acknowledged_at_ms",
                    "controller_status_file_sha256",
                    "controller_status_body_sha256",
                    "observer_state_file_sha256",
                    "observer_state_body_sha256",
                },
            )
            self.assertEqual(completion["reader_unit"], config.reader_unit)
            self.assertEqual(completion["reader_pid"], 1234)
            self.assertEqual(completion["acknowledged_at_ms"], 1_000_000)

    def test_final_ack_accepts_real_reader_terminal_status(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            clock = FakeClock()
            config = configuration(Path(temporary), clock)
            write_completion_documents(config, clock)
            triplet = {
                "identity": "sha256:" + "1" * 64,
                "export_receipt_identity": "sha256:" + "3" * 64,
                "commit_sequence": 1,
                "generation": "generation-00000000000000000001-fixture",
                "snapshot_body_sha256": "sha256:" + "2" * 64,
                "lease_generation": 1,
                "snapshot_generated_at_ms": 999_000,
                "exported_at_ms": 999_001,
                "execution_service_epoch": "epoch-1",
                "execution_service_fencing_generation": 1,
                "paths": {},
                "contents": {},
            }
            with mock.patch.object(reader_controller.os, "getpid", return_value=1234), \
                    mock.patch.object(reader_controller.os, "geteuid", return_value=1000), \
                    mock.patch.object(reader_controller.os, "getegid", return_value=1000), \
                    mock.patch.object(
                        reader_controller.time, "time_ns",
                        return_value=clock.time_ns()):
                status_body = reader_controller._status_body(
                    campaign_id=config.campaign_id,
                    state="TERMINAL",
                    started_at_ms=900_000,
                    invocations=config.maximum_iterations,
                    last_triplet=triplet,
                    observer_status="COMPLETE",
                    observer_outcome="COMPLETE",
                    completed_iterations=config.maximum_iterations,
                    reason=None,
                )
            assert config.reader_status_path is not None
            config.reader_status_path.write_bytes(
                controller._canonical(seal(status_body)))
            config.reader_status_path.chmod(0o600)
            executor = controller.CommandExecutor()
            with mock.patch.object(
                    executor, "_run", return_value=reader_unit_show()):
                completion = executor.assert_reader_completed(
                    config, clock.time_ns() // 1_000_000)
            self.assertEqual(completion["reader_pid"], 1234)
            self.assertEqual(
                completion["controller_status_body_sha256"],
                seal(status_body)["body_sha256"],
            )

    def test_final_ack_rejects_reader_unit_pid_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            clock = FakeClock()
            config = configuration(Path(temporary), clock)
            write_completion_documents(config, clock)
            executor = controller.CommandExecutor()
            with mock.patch.object(
                    executor, "_run", return_value=reader_unit_show(4321)), \
                    self.assertRaisesRegex(
                        controller.ControllerError,
                        "P1_READER_COMPLETION_STATUS_INVALID"):
                executor.assert_reader_completed(
                    config, clock.time_ns() // 1_000_000)

    def test_final_ack_rejects_terminal_iteration_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            clock = FakeClock()
            config = configuration(Path(temporary), clock)
            write_completion_documents(
                config, clock,
                status_overrides={"completed_iterations": 0},
            )
            with self.assertRaisesRegex(
                    controller.ControllerError,
                    "P1_READER_COMPLETION_MISMATCH"):
                executor = controller.CommandExecutor()
                with mock.patch.object(
                        executor, "_run", return_value=reader_unit_show()):
                    executor.assert_reader_completed(
                        config, clock.time_ns() // 1_000_000)

    def test_final_ack_rejects_stale_or_invalid_status(self) -> None:
        cases = (
            ({"updated_at_ms": 984_999}, {},
             "P1_READER_COMPLETION_STATUS_INVALID"),
            ({"state": "FAILED", "reason": "FAILED"}, {},
             "P1_READER_COMPLETION_STATUS_INVALID"),
            ({"paper_authorized": True}, {},
             "P1_PERMISSION_FLAGS_INVALID"),
            ({}, {"campaign_id": "wrong-campaign"},
             "P1_READER_COMPLETION_STATE_INVALID"),
        )
        for status_overrides, state_overrides, expected in cases:
            with self.subTest(expected=expected), \
                    tempfile.TemporaryDirectory() as temporary:
                clock = FakeClock()
                config = configuration(Path(temporary), clock)
                write_completion_documents(
                    config,
                    clock,
                    status_overrides=status_overrides,
                    state_overrides=state_overrides,
                )
                with self.assertRaisesRegex(
                        controller.ControllerError, expected):
                    executor = controller.CommandExecutor()
                    with mock.patch.object(
                            executor, "_run", return_value=reader_unit_show()):
                        executor.assert_reader_completed(
                            config, clock.time_ns() // 1_000_000)

    def test_rotation_is_exact_generation_at_3000_seconds(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            clock = FakeClock()
            config = configuration(
                Path(temporary),
                clock,
                first_slot_delta_sec=10_000,
                maximum_runtime_sec=3010,
            )
            executor = FakeExecutor(clock)
            result = controller.Controller(
                config, clock=clock, executor=executor).run()
        self.assertEqual(result.status, "MAXIMUM_RUNTIME_REACHED")
        self.assertEqual(result.generation, 2)
        self.assertEqual(
            executor.rotate_calls,
            [(controller.ROTATION_INTERVAL_NS, 1)],
        )
        self.assertEqual(
            executor.collect_times,
            [
                index * controller.COLLECTOR_INTERVAL_NS
                for index in range(301)
            ],
        )

    def test_missed_collector_cadence_fails_without_catchup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            clock = FakeClock()
            config = configuration(Path(temporary), clock)
            executor = FakeExecutor(
                clock, collector_advance_ns=11_100_000_000)
            with self.assertRaisesRegex(
                    controller.ControllerError,
                    "P1_COLLECTOR_CADENCE_MISSED"):
                controller.Controller(
                    config, clock=clock, executor=executor).run()
        self.assertEqual(executor.collect_times, [0])

    def test_capture_invalid_json_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            clock = FakeClock()
            config = configuration(Path(temporary), clock)
            executor = FakeExecutor(clock, capture_mode="invalid-json")
            with self.assertRaisesRegex(
                    controller.ControllerError,
                    "P1_CAPTURE_RECEIPT_JSON_INVALID"):
                controller.Controller(
                    config, clock=clock, executor=executor).run()

    def test_capture_permission_true_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            clock = FakeClock()
            config = configuration(Path(temporary), clock)
            executor = FakeExecutor(clock, capture_mode="permission-true")
            with self.assertRaisesRegex(
                    controller.ControllerError,
                    "P1_PERMISSION_FLAGS_INVALID"):
                controller.Controller(
                    config, clock=clock, executor=executor).run()

    def test_capture_timeout_precedes_slot_and_terminates_child(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            clock = FakeClock()
            config = configuration(Path(temporary), clock)
            executor = FakeExecutor(clock, capture_mode="hang")
            with self.assertRaisesRegex(
                    controller.ControllerError,
                    "P1_CAPTURE_TIMEOUT"):
                controller.Controller(
                    config, clock=clock, executor=executor).run()
        self.assertLess(clock.monotonic_ns(), 200_000_000_000)
        self.assertIsNotNone(executor.last_capture)
        assert executor.last_capture is not None
        self.assertTrue(executor.last_capture.terminated)

    def test_exact_production_commands_and_json_contracts(self) -> None:
        config = controller.Configuration(
            campaign_id="p1-command-test",
            domain_config=controller.REQUIRED_DOMAIN_CONFIG,
            start_generation=7,
            maximum_runtime_sec=1,
            valid_after_ms=9_999_999_999,
            maximum_iterations=1,
            capture_lead_sec=180,
            capture_timeout_sec=150,
            evidence_root=controller.REQUIRED_EVIDENCE_ROOT,
            export_root=controller.REQUIRED_EXPORT_ROOT,
            reader_uid=1000,
            reader_gid=1000,
            capture_helper_sha256=DIGEST,
        )
        rotation = {
            "schema": "hepta.shadow-watch-custodian-rotation.v1",
            "status": "ROTATED",
            "campaign_id": config.campaign_id,
            "previous_lease_generation": 7,
            "lease_generation": 8,
            "previous_authority_outcome": "ROTATED",
            "paper_authorized": False,
            "live_authorized": False,
            "mutation_authorized": False,
            "direct_broker_access": False,
        }
        no_active = {
            "schema": "hepta.shadow-watch-custodian-status.v1",
            "status": "NO_ACTIVE_TRANSACTION",
            "domain_id": "alpha",
            "paper_authorized": False,
            "live_authorized": False,
            "mutation_authorized": False,
            "direct_broker_access": False,
        }
        calls: list[list[str]] = []

        def fake_run(arguments: list[str], **_kwargs: object):
            calls.append(arguments)
            if "rotate" in arguments:
                return subprocess.CompletedProcess(
                    arguments, 0, json.dumps(rotation), "")
            if "close" in arguments:
                return subprocess.CompletedProcess(
                    arguments, 0, json.dumps(no_active), "")
            return subprocess.CompletedProcess(arguments, 0, "", "")

        executor = controller.CommandExecutor()
        with mock.patch.object(
                controller.subprocess, "run", side_effect=fake_run):
            executor.collect()
            result = executor.rotate(config, 7)
            closed = executor.close(config, "operator-request")
        self.assertEqual(result["lease_generation"], 8)
        self.assertEqual(closed["status"], "NO_ACTIVE_TRANSACTION")
        self.assertEqual(
            calls[0],
            [
                "/usr/bin/systemctl",
                "start",
                "--wait",
                "hepta-shadow-watch-collector@alpha.service",
            ],
        )
        self.assertEqual(
            calls[1],
            [
                "/usr/libexec/hepta-shadow-watch-custodian",
                "--domain-config",
                "/etc/heptatrader/trust-domains/alpha.json",
                "rotate",
                "--campaign-id",
                "p1-command-test",
                "--current-generation",
                "7",
                "--ttl-sec",
                "3600",
            ],
        )
        self.assertEqual(calls[2][-2:], ["--reason", "operator-request"])

    def test_formal_close_reconciles_pending_before_return(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            clock = FakeClock()
            config = configuration(Path(temporary), clock)
            pending = {
                "schema": "hepta.shadow-watch-custodian-status.v1",
                "status": "PENDING_EXPIRY",
                "domain_id": "alpha",
                "campaign_id": config.campaign_id,
                "lease_generation": config.start_generation,
                "paper_authorized": False,
                "live_authorized": False,
                "mutation_authorized": False,
                "direct_broker_access": False,
            }
            no_active = {
                "schema": "hepta.shadow-watch-custodian-status.v1",
                "status": "NO_ACTIVE_TRANSACTION",
                "domain_id": "alpha",
                "paper_authorized": False,
                "live_authorized": False,
                "mutation_authorized": False,
                "direct_broker_access": False,
            }
            calls: list[list[str]] = []

            def fake_run(arguments: list[str], _timeout: float):
                calls.append(arguments)
                document = pending if "close" in arguments else no_active
                return subprocess.CompletedProcess(
                    arguments, 0, json.dumps(document), "")

            executor = controller.CommandExecutor()
            with mock.patch.object(executor, "_run", side_effect=fake_run):
                result = executor.close(config, "operator-request")
            self.assertEqual(result, no_active)
            self.assertEqual(calls[0][-2:], ["--reason", "operator-request"])
            self.assertEqual(calls[1][-1], "reconcile")

    def test_formal_close_requires_exact_terminal_proof(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            clock = FakeClock()
            config = configuration(Path(temporary), clock)
            accepted = {
                "schema": "hepta.shadow-watch-custodian-closure.v1",
                "version": 1,
                "campaign_id": config.campaign_id,
                "lease_generation": config.start_generation,
                "authoritative_revoke_outcome": "ACCEPTED",
                "local_authority_removed": True,
                "export_evidence_removed": True,
                "paper_authorized": False,
                "live_authorized": False,
                "mutation_authorized": False,
                "direct_broker_access": False,
            }
            controller._validate_formal_close(accepted, config)
            no_active = {
                "schema": "hepta.shadow-watch-custodian-status.v1",
                "status": "NO_ACTIVE_TRANSACTION",
                "domain_id": "alpha",
                "paper_authorized": False,
                "live_authorized": False,
                "mutation_authorized": False,
                "direct_broker_access": False,
            }
            controller._validate_formal_close(no_active, config)
            mutations = (
                {**accepted, "authoritative_revoke_outcome": "ALREADY_ABSENT"},
                {**accepted, "local_authority_removed": False},
                {**accepted, "export_evidence_removed": False},
                {**no_active, "campaign_id": config.campaign_id},
            )
            for document in mutations:
                with self.subTest(document=document), self.assertRaisesRegex(
                        controller.ControllerError,
                        "P1_FORMAL_CLOSE_INVALID"):
                    controller._validate_formal_close(document, config)

    def test_configuration_hashes_capture_helper_as_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            capture_helper = Path(temporary) / "capture-helper"
            capture_helper.write_text("#!/bin/true\n", encoding="ascii")
            digest = controller._sha256_file(capture_helper)
            config = controller.Configuration(
                campaign_id="p1-validation-test",
                domain_config=controller.REQUIRED_DOMAIN_CONFIG,
                start_generation=1,
                maximum_runtime_sec=None,
                valid_after_ms=9_999_999_999,
                maximum_iterations=1,
                capture_lead_sec=180,
                capture_timeout_sec=150,
                evidence_root=controller.REQUIRED_EVIDENCE_ROOT,
                export_root=controller.REQUIRED_EXPORT_ROOT,
                reader_uid=1000,
                reader_gid=1000,
                capture_helper_sha256=digest,
                reader_unit="hepta-p1-shadow-reader-round1.service",
                reader_status_path=Path("/run/hepta/p1-reader-status.json"),
            )
            with mock.patch.object(
                    controller.os, "geteuid", return_value=0), \
                    mock.patch.object(
                        controller.os, "getegid", return_value=0), \
                    mock.patch.object(
                        controller, "CAPTURE_HELPER",
                        str(capture_helper)), \
                    mock.patch.object(
                        controller, "_validate_formal_admission"):
                controller._validate_configuration(config, now_wall_ms=1)

    def test_capture_rehash_rejects_drift_before_second_popen(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            clock = FakeClock()
            config = configuration(root, clock)
            capture_helper = root / "capture-helper"
            capture_helper.write_text("#!/bin/true\n", encoding="ascii")
            config = replace(
                config,
                capture_helper_sha256=controller._sha256_file(capture_helper),
            )
            executor = controller.CommandExecutor()
            with mock.patch.object(
                    controller, "CAPTURE_HELPER", str(capture_helper)), \
                    mock.patch.object(
                        controller.subprocess, "Popen",
                        return_value=FakeCapture()) as popen:
                executor.start_capture(
                    config, 1, root / "capture-receipt-1.json")
                capture_helper.write_text(
                    "#!/bin/false\n", encoding="ascii")
                with self.assertRaisesRegex(
                        controller.ControllerError,
                        "P1_CAPTURE_HELPER_DIGEST_MISMATCH"):
                    executor.start_capture(
                        config, 2, root / "capture-receipt-2.json")
            self.assertEqual(popen.call_count, 1)

    def test_direct_formal_start_without_admission_is_denied(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            clock = FakeClock()
            config = configuration(Path(temporary), clock)
            with mock.patch.object(controller.os, "geteuid", return_value=0), \
                    mock.patch.object(controller.os, "getegid", return_value=0), \
                    mock.patch.object(controller, "_sha256_file", return_value=DIGEST), \
                    mock.patch.object(
                        controller, "REQUIRED_EVIDENCE_ROOT", config.evidence_root), \
                    mock.patch.object(
                        controller, "REQUIRED_EXPORT_ROOT", config.export_root), \
                    self.assertRaisesRegex(
                        controller.ControllerError,
                        "P1_FORMAL_ADMISSION_REQUIRED"):
                controller._validate_configuration(config, now_wall_ms=1)

    def test_actual_first_formal_collection_is_bound_to_warmup_anchor(
            self) -> None:
        self.assertEqual(
            controller.FORMAL_HISTORY_WARMUP_MS,
            policy_builder.MINIMUM_WARMUP_MS,
        )
        with tempfile.TemporaryDirectory() as temporary:
            clock = FakeClock()
            base = configuration(Path(temporary), clock)
            warmup_start_ms = 1_800_000_000_000
            config = replace(
                base,
                valid_after_ms=(
                    warmup_start_ms +
                    controller.FORMAL_HISTORY_WARMUP_MS),
            )
            for offset_ms in (
                    0,
                    controller.FORMAL_FIRST_COLLECTION_TOLERANCE_MS):
                controller._validate_formal_first_collection(config, {
                    "collection_started_at_ms":
                        warmup_start_ms + offset_ms,
                })
            for invalid in (
                    warmup_start_ms - 1,
                    warmup_start_ms +
                    controller.FORMAL_FIRST_COLLECTION_TOLERANCE_MS + 1,
                    True,
                    None):
                with self.subTest(invalid=invalid), self.assertRaisesRegex(
                        controller.ControllerError,
                        "P1_FORMAL_FIRST_COLLECTION_WINDOW_INVALID"):
                    controller._validate_formal_first_collection(config, {
                        "collection_started_at_ms": invalid,
                    })

    def test_formal_admission_exact_contract_rejects_forged_go(self) -> None:
        mutations = {
            "trimmed": lambda document: document.pop(
                "history_head_body_sha256"),
            "sample": lambda document: document.__setitem__("sample_count", 90),
            "miss": lambda document: document.__setitem__(
                "missed_decision_count", 1),
            "cursor": lambda document: document.__setitem__(
                "probe_audit_cursor_sequence", 90),
            "anchor": lambda document: document.__setitem__(
                "probe_audit_expected_previous_sha256", "sha256:" + "f" * 64),
            "lateness": lambda document: document.__setitem__(
                "probe_last_collection_started_at_ms",
                document["probe_first_collection_started_at_ms"] +
                90 * 10_000 + 1_001),
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            clock = FakeClock(wall_ms=1_010_000)
            base = configuration(root, clock)
            config = replace(
                base,
                campaign_id="p1-formal-round1",
                valid_after_ms=policy_builder.aligned_valid_after(1_010_000),
                maximum_iterations=controller.FORMAL_MAXIMUM_ITERATIONS,
                policy_path=root / "policy.json",
                admission_receipt_path=root / "admission.json",
                authority_marker_path=root / "marker.json",
                watch_snapshot_path=controller.REQUIRED_WATCH_SNAPSHOT,
            )
            environment = {"gateway_invocation_id": "a" * 32}
            for name, mutate in mutations.items():
                with self.subTest(name=name):
                    admission = formal_admission(config, environment)
                    admission.pop("body_sha256")
                    mutate(admission)
                    admission = seal(admission)
                    with self.assertRaisesRegex(
                            controller.ControllerError,
                            "P1_FORMAL_ADMISSION_BINDING_INVALID"):
                        controller._validate_admission_payload(
                            admission,
                            config=config,
                            expected_environment=environment,
                            now_ms=1_010_000,
                        )

    def test_formal_admission_accepts_jitter_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            clock = FakeClock(wall_ms=1_010_000)
            base = configuration(root, clock)
            config = replace(
                base,
                campaign_id="p1-formal-round1",
                valid_after_ms=policy_builder.aligned_valid_after(1_010_000),
                maximum_iterations=controller.FORMAL_MAXIMUM_ITERATIONS,
                policy_path=root / "policy.json",
                admission_receipt_path=root / "admission.json",
                authority_marker_path=root / "marker.json",
                watch_snapshot_path=controller.REQUIRED_WATCH_SNAPSHOT,
            )
            environment = {"gateway_invocation_id": "a" * 32}
            admission = formal_admission(config, environment)
            admission.pop("body_sha256")
            admission["probe_last_collection_started_at_ms"] += 1_000
            admission["probe_last_exported_at_ms"] += 1_000
            controller._validate_admission_payload(
                seal(admission),
                config=config,
                expected_environment=environment,
                now_ms=1_010_000,
            )

    def test_formal_admission_accepts_real_builder_marker_lifetime(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            clock = FakeClock(wall_ms=1_020_000)
            base = configuration(root, clock)
            config = replace(
                base,
                campaign_id="p1-formal-round1",
                valid_after_ms=policy_builder.aligned_valid_after(1_010_000),
                maximum_iterations=controller.FORMAL_MAXIMUM_ITERATIONS,
                policy_path=root / "policy.json",
                admission_receipt_path=root / "admission.json",
                authority_marker_path=root / "marker.json",
                watch_snapshot_path=controller.REQUIRED_WATCH_SNAPSHOT,
            )
            environment = {"gateway_invocation_id": "a" * 32}
            policy, admission, marker = write_builder_formal_documents(
                root, config, environment)
            self.assertEqual(policy["valid_after_ms"], config.valid_after_ms)
            self.assertEqual(policy["maximum_iterations"], 241)
            self.assertEqual(policy["slot_interval_ms"], 120_000)
            self.assertEqual(policy["maximum_lateness_ms"], 60_000)
            self.assertEqual(
                marker["admitted_at_ms"], admission["validated_at_ms"])
            self.assertEqual(
                marker["expires_at_ms"], policy["expires_at_ms"])
            self.assertGreater(
                marker["expires_at_ms"],
                admission["validated_at_ms"] +
                controller.ADMISSION_MAXIMUM_AGE_MS,
            )
            with mock.patch.object(controller, "ROOT_UID", os.geteuid()), \
                    mock.patch.object(controller, "ROOT_GID", os.getegid()), \
                    mock.patch.object(
                        controller, "_load_probe_environment_binding",
                        return_value=environment), \
                    mock.patch.object(
                        controller.sys, "argv",
                        [str(Path(controller.__file__))]):
                controller._validate_formal_admission(config, 1_020_000)

    def test_formal_admission_binds_every_schedule_field(self) -> None:
        cases = (
            ("config-valid-after", "config", "valid_after_ms", 1),
            ("config-iterations", "config", "maximum_iterations", -1),
            ("policy-valid-after", "policy", "valid_after_ms", 1),
            ("policy-iterations", "policy", "maximum_iterations", -1),
            ("policy-slot-interval", "policy", "slot_interval_ms", -1),
            ("policy-lateness", "policy", "maximum_lateness_ms", -1),
            ("policy-expiry-formula", "policy", "expires_at_ms", 1),
        )
        for name, target, field, delta in cases:
            with self.subTest(name=name), \
                    tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                clock = FakeClock(wall_ms=1_020_000)
                base = configuration(root, clock)
                config = replace(
                    base,
                    campaign_id="p1-formal-round1",
                    valid_after_ms=policy_builder.aligned_valid_after(
                        1_010_000),
                    maximum_iterations=controller.FORMAL_MAXIMUM_ITERATIONS,
                    policy_path=root / "policy.json",
                    admission_receipt_path=root / "admission.json",
                    authority_marker_path=root / "marker.json",
                    watch_snapshot_path=controller.REQUIRED_WATCH_SNAPSHOT,
                )
                environment = {"gateway_invocation_id": "a" * 32}
                policy, _admission, marker = write_builder_formal_documents(
                    root, config, environment)
                checked_config = config
                if target == "config":
                    checked_config = replace(
                        config,
                        **{field: getattr(config, field) + delta},
                    )
                else:
                    policy_body = dict(policy)
                    policy_body.pop("body_sha256")
                    policy_body[field] = policy_body[field] + delta
                    policy = seal(policy_body)
                    assert config.policy_path is not None
                    config.policy_path.write_bytes(
                        controller._canonical(policy))
                    marker_body = dict(marker)
                    marker_body.pop("body_sha256")
                    marker_body["policy_file_sha256"] = (
                        "sha256:" + controller.hashlib.sha256(
                            controller._canonical(policy)).hexdigest()
                    )
                    marker_body["policy_body_sha256"] = policy["body_sha256"]
                    if field == "expires_at_ms":
                        marker_body["expires_at_ms"] = policy[field]
                    assert config.authority_marker_path is not None
                    config.authority_marker_path.write_bytes(
                        controller._canonical(seal(marker_body)))
                with mock.patch.object(
                        controller, "ROOT_UID", os.geteuid()), \
                        mock.patch.object(
                            controller, "ROOT_GID", os.getegid()), \
                        mock.patch.object(
                            controller, "_load_probe_environment_binding",
                            return_value=environment), \
                        mock.patch.object(
                            controller.sys, "argv",
                            [str(Path(controller.__file__))]), \
                        self.assertRaisesRegex(
                            controller.ControllerError,
                            "P1_FORMAL_ADMISSION_BINDING_INVALID"):
                    controller._validate_formal_admission(
                        checked_config, 1_020_000)

    def test_formal_admission_rejects_marker_expiry_mismatch(self) -> None:
        for expiry_delta in (-1, 1):
            with self.subTest(expiry_delta=expiry_delta), \
                    tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                clock = FakeClock(wall_ms=1_020_000)
                base = configuration(root, clock)
                config = replace(
                    base,
                    campaign_id="p1-formal-round1",
                    valid_after_ms=policy_builder.aligned_valid_after(
                        1_010_000),
                    maximum_iterations=controller.FORMAL_MAXIMUM_ITERATIONS,
                    policy_path=root / "policy.json",
                    admission_receipt_path=root / "admission.json",
                    authority_marker_path=root / "marker.json",
                    watch_snapshot_path=controller.REQUIRED_WATCH_SNAPSHOT,
                )
                environment = {"gateway_invocation_id": "a" * 32}
                policy, _admission, marker = write_builder_formal_documents(
                    root, config, environment)
                marker_body = dict(marker)
                marker_body.pop("body_sha256")
                marker_body["expires_at_ms"] = (
                    policy["expires_at_ms"] + expiry_delta)
                assert config.authority_marker_path is not None
                config.authority_marker_path.write_bytes(
                    controller._canonical(seal(marker_body)))
                with mock.patch.object(
                        controller, "ROOT_UID", os.geteuid()), \
                        mock.patch.object(
                            controller, "ROOT_GID", os.getegid()), \
                        mock.patch.object(
                            controller, "_load_probe_environment_binding",
                            return_value=environment), \
                        mock.patch.object(
                            controller.sys, "argv",
                            [str(Path(controller.__file__))]), \
                        self.assertRaisesRegex(
                            controller.ControllerError,
                            "P1_FORMAL_ADMISSION_BINDING_INVALID"):
                    controller._validate_formal_admission(config, 1_020_000)

    def test_formal_admission_rejects_marker_creation_bounds(self) -> None:
        for created_at_ms in (999_999, 1_020_001):
            with self.subTest(created_at_ms=created_at_ms), \
                    tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                clock = FakeClock(wall_ms=1_020_000)
                base = configuration(root, clock)
                config = replace(
                    base,
                    campaign_id="p1-formal-round1",
                    valid_after_ms=policy_builder.aligned_valid_after(
                        1_010_000),
                    maximum_iterations=controller.FORMAL_MAXIMUM_ITERATIONS,
                    policy_path=root / "policy.json",
                    admission_receipt_path=root / "admission.json",
                    authority_marker_path=root / "marker.json",
                    watch_snapshot_path=controller.REQUIRED_WATCH_SNAPSHOT,
                )
                environment = {"gateway_invocation_id": "a" * 32}
                _policy, _admission, marker = write_builder_formal_documents(
                    root, config, environment)
                marker_body = dict(marker)
                marker_body.pop("body_sha256")
                marker_body["marker_created_at_ms"] = created_at_ms
                assert config.authority_marker_path is not None
                config.authority_marker_path.write_bytes(
                    controller._canonical(seal(marker_body)))
                with mock.patch.object(
                        controller, "ROOT_UID", os.geteuid()), \
                        mock.patch.object(
                            controller, "ROOT_GID", os.getegid()), \
                        mock.patch.object(
                            controller, "_load_probe_environment_binding",
                            return_value=environment), \
                        mock.patch.object(
                            controller.sys, "argv",
                            [str(Path(controller.__file__))]), \
                        self.assertRaisesRegex(
                            controller.ControllerError,
                            "P1_FORMAL_ADMISSION_BINDING_INVALID"):
                    controller._validate_formal_admission(config, 1_020_000)

    def test_formal_admission_rejects_load_probe_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            clock = FakeClock(wall_ms=1_010_000)
            base = configuration(root, clock)
            policy_path = root / "policy.json"
            admission_path = root / "admission.json"
            marker_path = root / "marker.json"
            config = replace(
                base,
                campaign_id="p1-formal-round1",
                policy_path=policy_path,
                admission_receipt_path=admission_path,
                authority_marker_path=marker_path,
                watch_snapshot_path=controller.REQUIRED_WATCH_SNAPSHOT,
            )
            environment = {"gateway_invocation_id": "a" * 32}
            policy = seal({
                "schema": "hepta.strategy-shadow-observation-policy.v1",
                "version": 1,
                "campaign_id": config.campaign_id,
            })
            admission = formal_admission(config, environment)
            probe_marker = seal({
                "schema": "hepta.p1-shadow-load-probe-authority-marker.v1",
                "version": 1,
                "status": "ACTIVE",
                "scope": "LOAD_PROBE",
                "mode": "LOAD_PROBE",
                "campaign_id": config.campaign_id,
                "policy_path": str(policy_path),
                "policy_file_sha256": "sha256:" + controller.hashlib.sha256(
                    controller._canonical(policy)).hexdigest(),
                "policy_body_sha256": policy["body_sha256"],
                "marker_created_at_ms": 1_000_000,
                "expires_at_ms": 2_200_000,
                "execution_binding_status": "PENDING_FIRST_SNAPSHOT",
                "execution_service_epoch": None,
                "execution_service_fencing_generation": None,
                "environment": environment,
                "paper_authorized": False,
                "live_authorized": False,
                "mutation_authorized": False,
                "direct_broker_access": False,
            })
            for path, document in (
                    (policy_path, policy),
                    (admission_path, admission),
                    (marker_path, probe_marker)):
                path.write_bytes(controller._canonical(document))
                path.chmod(0o600)
            with mock.patch.object(controller, "ROOT_UID", os.geteuid()), \
                    mock.patch.object(controller, "ROOT_GID", os.getegid()), \
                    mock.patch.object(
                        controller, "_load_probe_environment_binding",
                        return_value=environment), \
                    mock.patch.object(
                        controller.sys, "argv", [str(Path(controller.__file__))]), \
                    self.assertRaisesRegex(
                        controller.ControllerError,
                        "P1_FORMAL_ADMISSION_BINDING_INVALID"):
                controller._validate_formal_admission(config, 1_010_000)

    def test_reader_failure_closes_accepted_without_collecting(self) -> None:
        for reason in (
                "P1_READER_UNIT_NOT_ACTIVE",
                "P1_READER_HEARTBEAT_INVALID",
                "P1_READER_UNIT_STATUS_INVALID"):
            with self.subTest(reason=reason), tempfile.TemporaryDirectory() as temporary:
                clock = FakeClock()
                config = configuration(Path(temporary), clock)
                executor = FakeExecutor(
                    clock, reader_error=reason, accepted_close=True)
                with mock.patch.object(controller, "_validate_configuration"), \
                        mock.patch.object(
                            controller, "_install_signal_handlers",
                            return_value={}), \
                        mock.patch.object(
                            controller, "_load_probe_environment_binding",
                            return_value={"binding": "fixed"}), \
                        mock.patch.object(
                            controller, "_configuration", return_value=config), \
                        mock.patch.object(
                            controller, "CommandExecutor", return_value=executor), \
                        mock.patch.object(controller, "Clock", return_value=clock), \
                        mock.patch.object(controller, "_parser") as parser:
                    parser.return_value.parse_args.return_value = object()
                    rc = controller.main()
                self.assertEqual(rc, 78)
                self.assertEqual(executor.collect_times, [])
                self.assertEqual(executor.close_reasons, ["operator-request"])

    def test_reader_status_failed_and_stale_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            clock = FakeClock(wall_ms=1_000_000)
            config = configuration(root, clock)
            executor = controller.CommandExecutor()
            active = subprocess.CompletedProcess(
                [], 0, "active\n", "")
            shown = subprocess.CompletedProcess(
                [], 0,
                "ActiveState=active\nSubState=running\nResult=success\n"
                "ExecMainStatus=0\n", "")
            for state, updated, expected in (
                    ("FAILED", 1_000_000, "P1_READER_HEARTBEAT_INVALID"),
                    ("RUNNING", 984_999, "P1_READER_HEARTBEAT_INVALID")):
                status = seal({
                    "schema": "hepta.p1-shadow-observer-controller-status.v1",
                    "version": 1,
                    "campaign_id": config.campaign_id,
                    "controller_uid": 1000,
                    "controller_gid": 1000,
                    "state": state,
                    "updated_at_ms": updated,
                    "reason": None if state == "RUNNING" else "FAILED",
                    "paper_authorized": False,
                    "live_authorized": False,
                    "mutation_attempted": False,
                    "direct_broker_access": False,
                })
                assert config.reader_status_path is not None
                config.reader_status_path.write_bytes(controller._canonical(status))
                with mock.patch.object(
                        executor, "_run", side_effect=[active, shown]), \
                        self.assertRaisesRegex(
                            controller.ControllerError, expected):
                    executor.assert_reader_active(config, 1_000_000)

    def test_main_error_path_closes_custodian(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            clock = FakeClock()
            config = configuration(Path(temporary), clock)
            executor = FakeExecutor(
                clock, collector_advance_ns=11_100_000_000)
            with mock.patch.object(
                    controller, "_validate_configuration"), \
                    mock.patch.object(
                        controller, "_install_signal_handlers",
                        return_value={}), \
                    mock.patch.object(
                        controller, "_load_probe_environment_binding",
                        return_value={"binding": "fixed"}), \
                    mock.patch.object(
                        controller, "_configuration",
                        return_value=config), \
                    mock.patch.object(
                        controller, "CommandExecutor",
                        return_value=executor), \
                    mock.patch.object(
                        controller, "Clock",
                        return_value=clock), \
                    mock.patch.object(
                        controller, "_parser") as parser:
                parser.return_value.parse_args.return_value = object()
                rc = controller.main()
        self.assertEqual(rc, 78)
        self.assertEqual(executor.close_reasons, ["operator-request"])

    def test_unexpected_runtime_error_is_typed_and_closes_custodian(
            self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            clock = FakeClock()
            config = configuration(Path(temporary), clock)
            executor = FakeExecutor(clock, accepted_close=True)
            with mock.patch.object(controller, "_validate_configuration"), \
                    mock.patch.object(
                        controller, "_install_signal_handlers",
                        return_value={}), \
                    mock.patch.object(
                        controller, "_load_probe_environment_binding",
                        return_value={"binding": "fixed"}), \
                    mock.patch.object(
                        controller, "_configuration", return_value=config), \
                    mock.patch.object(
                        controller, "CommandExecutor", return_value=executor), \
                    mock.patch.object(
                        controller, "Clock", return_value=clock), \
                    mock.patch.object(
                        controller.Controller, "run",
                        side_effect=RuntimeError("unexpected splice")), \
                    mock.patch.object(controller, "_parser") as parser:
                parser.return_value.parse_args.return_value = object()
                rc = controller.main()
        self.assertEqual(rc, 78)
        self.assertEqual(executor.close_reasons, ["operator-request"])

    def test_all_terminal_signal_handlers_latch_before_close(self) -> None:
        for signum in (
                controller.signal.SIGINT,
                controller.signal.SIGTERM,
                controller.signal.SIGHUP):
            with self.subTest(signum=signum), \
                    mock.patch.object(controller.signal, "signal") as install:
                install.return_value = controller.signal.SIG_DFL
                previous = controller._install_signal_handlers()
                handler = next(
                    call.args[1] for call in install.call_args_list
                    if call.args[0] == signum)
                with self.assertRaises(controller.ControllerSignal) as raised:
                    handler(signum, None)
                self.assertEqual(raised.exception.signum, signum)
                self.assertIsNone(handler(signum, None))
                self.assertEqual(
                    set(previous), {
                        controller.signal.SIGINT,
                        controller.signal.SIGTERM,
                        controller.signal.SIGHUP,
                    })

    def test_main_final_environment_drift_closes_both_modes(self) -> None:
        baseline = {"binding": "fixed"}
        drifted = {"binding": "drifted"}
        for load_probe_runs, expected_collects in (
            (controller.LOAD_PROBE_REQUIRED_RUNS,
             controller.LOAD_PROBE_REQUIRED_RUNS),
            (None, 21),
        ):
            with self.subTest(load_probe_runs=load_probe_runs), \
                    tempfile.TemporaryDirectory() as temporary:
                clock = FakeClock()
                config = configuration(
                    Path(temporary), clock,
                    load_probe_runs=load_probe_runs)
                executor = FakeExecutor(clock, accepted_close=True)
                bindings = (
                    [dict(baseline)] * (2 * expected_collects + 1) +
                    [dict(drifted)]
                )
                with mock.patch.object(
                        controller, "_validate_configuration"), \
                        mock.patch.object(
                            controller, "_install_signal_handlers",
                            return_value={}), \
                        mock.patch.object(
                            controller, "_load_probe_environment_binding",
                            side_effect=bindings), \
                        mock.patch.object(
                            controller, "_configuration", return_value=config), \
                        mock.patch.object(
                            controller, "CommandExecutor",
                            return_value=executor), \
                        mock.patch.object(
                            controller, "Clock", return_value=clock), \
                        mock.patch.object(controller, "_parser") as parser:
                    parser.return_value.parse_args.return_value = object()
                    rc = controller.main()
                self.assertEqual(rc, 78)
                self.assertEqual(len(executor.collect_times), expected_collects)
                self.assertEqual(executor.close_reasons, ["operator-request"])


if __name__ == "__main__":
    unittest.main()
