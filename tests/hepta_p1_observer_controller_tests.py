#!/usr/bin/env python3

from __future__ import annotations

from contextlib import nullcontext
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parent
SCRIPTS = ROOT.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


POLICY = load(
    "p1_policy", SCRIPTS / "build_hepta_p1_observation_policy.py")
CONTROLLER = load(
    "p1_controller", SCRIPTS / "hepta_p1_shadow_observer_controller.py")


def seal(body):
    return {
        **body,
        "body_sha256": CONTROLLER.digest_bytes(
            CONTROLLER.canonical_bytes(body)),
    }


def write(path: Path, document) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(CONTROLLER.canonical_bytes(document))


def test_environment() -> dict:
    return {
        "boot_id": "00000000-0000-0000-0000-000000000000",
        "audit_journal_device": 1,
        "audit_journal_inode": 2,
        "collector_sha256": "sha256:" + "1" * 64,
        "exporter_sha256": "sha256:" + "2" * 64,
        "heptactl_sha256": "sha256:" + "3" * 64,
        "gateway_sha256": "sha256:" + "4" * 64,
        "custodian_sha256": "sha256:" + "5" * 64,
        "observer_sha256": "sha256:" + "6" * 64,
        "host_controller_sha256": "sha256:" + "7" * 64,
        "domain_config_sha256": "sha256:" + "8" * 64,
        "gateway_profile_sha256": "sha256:" + "9" * 64,
        "gateway_process_profile_sha256": CONTROLLER.digest_bytes(
            CONTROLLER.ALPHA_GATEWAY_PROCESS_PROFILE_BYTES),
        "gateway_invocation_id": "a" * 32,
        "gateway_main_pid": 123,
        "gateway_exec_main_start_timestamp_monotonic_us": 456,
        "gateway_socket_device": 7,
        "gateway_socket_inode": 8,
    }


def export_triplet(
    directory: Path,
    *,
    generation: int = 1,
    execution_epoch: str = "epoch-1",
    execution_fencing: int = 1,
) -> None:
    snapshot = seal({
        "schema": "hepta.shadow-watch-snapshot.v2",
        "domain_id": "alpha",
        "agent_uid": 1001,
        "generated_at_ms": 1_000_000,
        "reads": {
            "system.get_health": {
                "execution_service_epoch": execution_epoch,
                "execution_service_fencing_generation": execution_fencing,
            },
        },
    })
    lease = seal({
        "schema": "hepta.shadow-watch-lease-receipt.v1",
        "domain_id": "alpha",
        "agent_uid": 1001,
        "lease_generation": generation,
    })
    snapshot_contents = CONTROLLER.canonical_bytes(snapshot)
    lease_contents = CONTROLLER.canonical_bytes(lease)
    export = seal({
        "schema": "hepta.shadow-watch-export-receipt.v1",
        "version": 1,
        "domain_id": "alpha",
        "agent_uid": 1001,
        "reader_uid": 1000,
        "reader_gid": 1000,
        "boundary": "WATCH_EXPORT",
        "lease_generation": generation,
        "lease_receipt_body_sha256": lease["body_sha256"],
        "lease_receipt_file_sha256":
            CONTROLLER.digest_bytes(lease_contents),
        "snapshot_body_sha256": snapshot["body_sha256"],
        "snapshot_file_sha256":
            CONTROLLER.digest_bytes(snapshot_contents),
        "snapshot_generated_at_ms": 1_000_000,
        "exported_at_ms": 1_000_001,
        "paper_authorized": False,
        "live_authorized": False,
        "mutation_attempted": False,
        "direct_broker_access": False,
    })
    export_contents = CONTROLLER.canonical_bytes(export)
    sequence = generation
    generation_name = (
        f"generation-{sequence:020d}-testfixture")
    generation_directory = (
        directory / CONTROLLER.EXPORT_GENERATIONS_NAME / generation_name)
    generation_directory.mkdir(mode=0o750, parents=True, exist_ok=True)
    directory.chmod(0o750)
    (directory / CONTROLLER.EXPORT_GENERATIONS_NAME).chmod(0o750)
    generation_directory.chmod(0o750)
    for name, contents in zip(
            CONTROLLER.EXPORT_FILES,
            (snapshot_contents, lease_contents, export_contents),
            strict=True):
        path = generation_directory / name
        path.write_bytes(contents)
        path.chmod(0o440)
    now_ms = CONTROLLER.time.time_ns() // 1_000_000
    commit = seal({
        "schema": "hepta.shadow-watch-export-commit.v1",
        "version": 1,
        "authority_status": "ACTIVE",
        "authority_changed_at_ms": now_ms,
        "close_reason": None,
        "commit_sequence": sequence,
        "generation": generation_name,
        "domain_id": "alpha",
        "agent_uid": 1001,
        "reader_uid": 1000,
        "reader_gid": 1000,
        "lease_generation": generation,
        "snapshot_body_sha256": snapshot["body_sha256"],
        "snapshot_file_sha256": CONTROLLER.digest_bytes(snapshot_contents),
        "lease_receipt_body_sha256": lease["body_sha256"],
        "lease_receipt_file_sha256": CONTROLLER.digest_bytes(lease_contents),
        "export_receipt_body_sha256": export["body_sha256"],
        "export_receipt_file_sha256": CONTROLLER.digest_bytes(export_contents),
        "committed_at_ms": now_ms,
        "paper_authorized": False,
        "live_authorized": False,
        "mutation_attempted": False,
        "direct_broker_access": False,
    })
    current = directory / CONTROLLER.EXPORT_COMMIT_NAME
    if current.exists():
        current.chmod(0o600)
    write(current, commit)
    current.chmod(0o440)


def end_export_authority(
        directory: Path,
        *,
        status: str = "CLOSED",
        reason: str = "service-stop",
) -> None:
    current = directory / CONTROLLER.EXPORT_COMMIT_NAME
    previous = json.loads(current.read_text(encoding="ascii"))
    body = {
        **{
            key: value for key, value in previous.items()
            if key != "body_sha256"
        },
        "authority_status": status,
        "authority_changed_at_ms":
            CONTROLLER.time.time_ns() // 1_000_000,
        "close_reason": reason,
        "commit_sequence": previous["commit_sequence"] + 1,
        "generation": None,
        "snapshot_body_sha256": None,
        "snapshot_file_sha256": None,
        "lease_receipt_file_sha256": None,
        "export_receipt_body_sha256": None,
        "export_receipt_file_sha256": None,
        "committed_at_ms": None,
    }
    replacement = current.with_name(".closed-current")
    write(replacement, seal(body))
    replacement.chmod(0o440)
    os.replace(replacement, current)


def authority(
    root: Path,
    campaign_id: str = "p1-test",
    *,
    load_probe: bool = False,
    expires_at_ms: int | None = None,
    policy_expires_at_ms: int | None = None,
    admitted_at_ms: int | None = None,
    marker_created_at_ms: int | None = None,
) -> tuple[Path, Path]:
    now_ms = CONTROLLER.time.time_ns() // 1_000_000
    policy_expiry = (
        now_ms + 4 * 24 * 60 * 60 * 1000
        if policy_expires_at_ms is None else policy_expires_at_ms)
    policy_path = root / "policy.json"
    policy = seal({
        "schema": "hepta.strategy-shadow-observation-policy.v1",
        "version": 1,
        "campaign_id": campaign_id,
        "valid_after_ms": now_ms + 10 * 60 * 1000,
        "expires_at_ms": policy_expiry,
        "paper_authorized": False,
        "live_authorized": False,
        "mutation_attempted": False,
        "direct_broker_access": False,
    })
    write(policy_path, policy)
    policy_path.chmod(0o644)
    marker_path = root / "authority.json"
    common = {
        "version": 1,
        "status": "ACTIVE",
        "campaign_id": campaign_id,
        "policy_path": str(policy_path),
        "policy_file_sha256": CONTROLLER.digest_bytes(
            CONTROLLER.canonical_bytes(policy)),
        "policy_body_sha256": policy["body_sha256"],
        "environment": test_environment(),
        "paper_authorized": False,
        "live_authorized": False,
        "mutation_authorized": False,
        "direct_broker_access": False,
    }
    if load_probe:
        marker_body = {
            **common,
            "schema": "hepta.p1-shadow-load-probe-authority-marker.v1",
            "scope": "LOAD_PROBE",
            "mode": "LOAD_PROBE",
            "marker_created_at_ms": now_ms,
            "expires_at_ms": (
                now_ms + 1_200_000
                if expires_at_ms is None else expires_at_ms),
            "execution_binding_status": "PENDING_FIRST_SNAPSHOT",
            "execution_service_epoch": None,
            "execution_service_fencing_generation": None,
        }
    else:
        admitted = now_ms if admitted_at_ms is None else admitted_at_ms
        created = (
            now_ms if marker_created_at_ms is None
            else marker_created_at_ms)
        marker_body = {
            **common,
            "schema": "hepta.p1-shadow-admission-authority-marker.v1",
            "admission_receipt_path": "/root/admission.json",
            "admission_receipt_file_sha256": "sha256:" + "a" * 64,
            "admission_receipt_body_sha256": "sha256:" + "b" * 64,
            "admitted_at_ms": admitted,
            "marker_created_at_ms": created,
            "expires_at_ms": (
                policy_expiry if expires_at_ms is None else expires_at_ms),
            "execution_service_epoch": "epoch-1",
            "execution_service_fencing_generation": 1,
        }
    marker = seal(marker_body)
    write(marker_path, marker)
    marker_path.chmod(0o644)
    return policy_path, marker_path


class P1PolicyTests(unittest.TestCase):
    def _admission_fixture(self, root: Path, *, validated_at_ms: int = 1_000_000):
        runtime = root / "runtime"
        runtime.mkdir()
        strategy = root / "strategy.json"
        strategy.write_text(json.dumps({
            "schema": "hepta.confirmed-momentum-strategy.v2",
            "strategy_id": "strategy",
            "strategy_version": "2.0.0",
            "paper_only": True,
            "live_authorized": False,
        }))
        for name in (
                "hepta_eurusd_confirmed_momentum_strategy.py",
                "hepta_market_context_builder.py",
                "hepta_market_evidence_normalizer.py",
                "hepta_strategy_contracts.py"):
            (runtime / name).write_text(name)
        policy_path = root / "formal-policy.json"
        marker_path = root / "formal-marker.json"
        environment = test_environment()
        first_started = 80_000
        last_started = first_started + 90 * 10_000
        admission = seal({
            "schema": "hepta.p1-shadow-load-probe-admission-receipt.v1",
            "version": 1,
            "status": "GO",
            "campaign_id": "p1-load-probe",
            "prospective_campaign_id": "p1-formal",
            "prospective_policy_path": str(policy_path),
            "authority_marker_path": str(marker_path),
            "validated_at_ms": validated_at_ms,
            "host_receipt_body_sha256": "sha256:" + "1" * 64,
            "observer_controller_status_body_sha256":
                "sha256:" + "2" * 64,
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
        admission_path = root / "admission.json"
        write(admission_path, admission)
        admission_path.chmod(0o600)
        return runtime, strategy, policy_path, marker_path, environment, admission_path

    def test_exact_window_and_installed_runtime_digest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = root / "runtime"
            runtime.mkdir()
            strategy = root / "strategy.json"
            strategy.write_text(json.dumps({
                "schema": "hepta.confirmed-momentum-strategy.v2",
                "strategy_id": "strategy",
                "strategy_version": "2.0.0",
                "paper_only": True,
                "live_authorized": False,
            }))
            for name in (
                    "hepta_eurusd_confirmed_momentum_strategy.py",
                    "hepta_market_context_builder.py",
                    "hepta_market_evidence_normalizer.py",
                    "hepta_strategy_contracts.py"):
                (runtime / name).write_text(name)
            start = 12_345
            policy = POLICY.build_policy(
                campaign_id="p1-test",
                start_ms=start,
                strategy_path=strategy,
                runtime_directory=runtime,
            )
            self.assertEqual(policy["valid_after_ms"] % 120_000, 0)
            self.assertGreaterEqual(
                policy["valid_after_ms"],
                start + POLICY.MINIMUM_WARMUP_MS)
            self.assertLess(
                policy["valid_after_ms"],
                start + POLICY.MINIMUM_WARMUP_MS +
                POLICY.SLOT_INTERVAL_MS)
            self.assertEqual(policy["maximum_iterations"], 241)
            self.assertEqual(policy["maximum_lateness_ms"], 60_000)
            self.assertEqual(
                policy["expires_at_ms"],
                policy["valid_after_ms"] + 241 * 120_000)
            self.assertEqual(
                policy["body_sha256"],
                POLICY.digest_bytes(POLICY.canonical_bytes({
                    key: value for key, value in policy.items()
                    if key != "body_sha256"
                })))

    def test_admitted_policy_rejects_stale_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime, strategy, policy, marker, environment, admission = (
                self._admission_fixture(root, validated_at_ms=1_000_000))
            metadata = admission.stat()
            with self.assertRaisesRegex(
                    POLICY.PolicyBuildError,
                    "P1_POLICY_ADMISSION_BINDING_INVALID"):
                POLICY.build_admitted_policy(
                    campaign_id="p1-formal",
                    start_ms=1_100_000,
                    strategy_path=strategy,
                    runtime_directory=runtime,
                    expected_strategy_sha256=None,
                    admission_receipt_path=admission,
                    policy_path=policy,
                    marker_path=marker,
                    environment=environment,
                    now_ms=1_060_001,
                    _expected_root_uid=metadata.st_uid,
                    _expected_root_gid=metadata.st_gid,
                    _require_root_identity=False,
                )

    def test_admitted_policy_binds_policy_marker_and_epoch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime, strategy, policy_path, marker_path, environment, admission = (
                self._admission_fixture(root))
            metadata = admission.stat()
            policy, marker = POLICY.build_admitted_policy(
                campaign_id="p1-formal",
                start_ms=1_010_000,
                strategy_path=strategy,
                runtime_directory=runtime,
                expected_strategy_sha256=None,
                admission_receipt_path=admission,
                policy_path=policy_path,
                marker_path=marker_path,
                environment=environment,
                now_ms=1_010_000,
                _expected_root_uid=metadata.st_uid,
                _expected_root_gid=metadata.st_gid,
                _require_root_identity=False,
            )
            self.assertEqual(marker["policy_body_sha256"], policy["body_sha256"])
            self.assertEqual(marker["execution_service_epoch"], "epoch-1")
            self.assertEqual(
                marker["execution_service_fencing_generation"], 1)
            self.assertEqual(
                marker["expires_at_ms"], policy["expires_at_ms"])
            self.assertLessEqual(
                marker["admitted_at_ms"], marker["marker_created_at_ms"])
            self.assertLessEqual(
                marker["marker_created_at_ms"] - marker["admitted_at_ms"],
                POLICY.ADMISSION_MAXIMUM_AGE_MS,
            )

    def test_admission_symlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime, strategy, policy, marker, environment, admission = (
                self._admission_fixture(root))
            link = root / "admission-link.json"
            link.symlink_to(admission)
            metadata = admission.stat()
            with self.assertRaisesRegex(
                    POLICY.PolicyBuildError,
                    "P1_POLICY_ADMISSION_FILE_INVALID"):
                POLICY.build_admitted_policy(
                    campaign_id="p1-formal",
                    start_ms=1_010_000,
                    strategy_path=strategy,
                    runtime_directory=runtime,
                    expected_strategy_sha256=None,
                    admission_receipt_path=link,
                    policy_path=policy,
                    marker_path=marker,
                    environment=environment,
                    now_ms=1_010_000,
                    _expected_root_uid=metadata.st_uid,
                    _expected_root_gid=metadata.st_gid,
                    _require_root_identity=False,
                )

    def test_load_probe_policy_marker_bootstrap_without_admission(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime, strategy, policy_path, marker_path, environment, _ = (
                self._admission_fixture(root))
            policy, marker = POLICY.build_load_probe_policy(
                campaign_id="p1-load-probe",
                start_ms=1_000_000,
                strategy_path=strategy,
                runtime_directory=runtime,
                expected_strategy_sha256=None,
                policy_path=policy_path,
                marker_path=marker_path,
                environment=environment,
                now_ms=1_000_000,
                _require_root_identity=False,
            )
            self.assertEqual(
                marker["schema"],
                "hepta.p1-shadow-load-probe-authority-marker.v1")
            self.assertEqual(marker["scope"], "LOAD_PROBE")
            self.assertEqual(marker["mode"], "LOAD_PROBE")
            self.assertEqual(
                marker["execution_binding_status"],
                "PENDING_FIRST_SNAPSHOT")
            self.assertIsNone(marker["execution_service_epoch"])
            self.assertEqual(marker["policy_body_sha256"], policy["body_sha256"])
            self.assertGreaterEqual(
                marker["expires_at_ms"] - marker["marker_created_at_ms"],
                91 * 10_000)

    def test_formal_consumer_rejects_trimmed_or_forged_probe_go(self) -> None:
        mutations = {
            "trimmed": lambda document: document.pop(
                "history_head_body_sha256"),
            "sample": lambda document: document.__setitem__("sample_count", 90),
            "miss": lambda document: document.__setitem__(
                "missed_sample_count", 1),
            "cursor": lambda document: document.__setitem__(
                "probe_audit_cursor_sequence", 90),
            "anchor": lambda document: document.__setitem__(
                "probe_audit_expected_previous_sha256", "sha256:" + "e" * 64),
            "lateness": lambda document: document.__setitem__(
                "probe_last_collection_started_at_ms",
                document["probe_first_collection_started_at_ms"] +
                90 * 10_000 + 1_001),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                runtime, strategy, policy_path, marker_path, environment, admission = (
                    self._admission_fixture(root))
                document = json.loads(admission.read_text())
                document.pop("body_sha256")
                mutate(document)
                write(admission, seal(document))
                metadata = admission.stat()
                with self.assertRaisesRegex(
                        POLICY.PolicyBuildError,
                        "P1_POLICY_ADMISSION_BINDING_INVALID"):
                    POLICY.build_admitted_policy(
                        campaign_id="p1-formal",
                        start_ms=1_010_000,
                        strategy_path=strategy,
                        runtime_directory=runtime,
                        expected_strategy_sha256=None,
                        admission_receipt_path=admission,
                        policy_path=policy_path,
                        marker_path=marker_path,
                        environment=environment,
                        now_ms=1_010_000,
                        _expected_root_uid=metadata.st_uid,
                        _expected_root_gid=metadata.st_gid,
                        _require_root_identity=False,
                    )

    def test_formal_consumer_accepts_last_anchor_at_jitter_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime, strategy, policy_path, marker_path, environment, admission = (
                self._admission_fixture(root))
            document = json.loads(admission.read_text())
            document.pop("body_sha256")
            document["probe_last_collection_started_at_ms"] += 1_000
            document["probe_last_exported_at_ms"] += 1_000
            write(admission, seal(document))
            metadata = admission.stat()
            POLICY.build_admitted_policy(
                campaign_id="p1-formal",
                start_ms=1_010_000,
                strategy_path=strategy,
                runtime_directory=runtime,
                expected_strategy_sha256=None,
                admission_receipt_path=admission,
                policy_path=policy_path,
                marker_path=marker_path,
                environment=environment,
                now_ms=1_010_000,
                _expected_root_uid=metadata.st_uid,
                _expected_root_gid=metadata.st_gid,
                _require_root_identity=False,
            )


class P1EnvironmentBindingTests(unittest.TestCase):
    def test_uid1000_gateway_identity_rebinds_pid_starttime_and_socket(
            self) -> None:
        status_output = (
            "ActiveState=active\nSubState=running\n" +
            "InvocationID=" + "a" * 32 + "\n" +
            "MainPID=123\nExecMainStartTimestampMonotonic=456\n")
        completed = mock.Mock(
            returncode=0, stdout=status_output, stderr="")
        profile = mock.Mock(raw=b"profile\n")
        process = mock.Mock(
            pid_directory_metadata=(1, 2, 3), starttime_ticks=789)
        gateway_socket = mock.Mock(metadata=(11, 12))
        events: list[str] = []

        def status(*_args, **_kwargs):
            events.append("status")
            return completed

        def identity(_pid):
            events.append("process")
            return process

        def socket_read(_path):
            events.append("socket")
            return gateway_socket

        def profile_read(_path):
            events.append("profile")
            return profile

        with mock.patch.object(
                CONTROLLER.subprocess, "run", side_effect=status), \
                mock.patch.object(
                    CONTROLLER, "read_alpha_gateway_process_identity",
                    side_effect=identity), \
                mock.patch.object(
                    CONTROLLER, "read_alpha_gateway_socket",
                    side_effect=socket_read), \
                mock.patch.object(
                    CONTROLLER, "read_alpha_gateway_profile",
                    side_effect=profile_read):
            result = CONTROLLER._live_gateway_identity(profile)
        self.assertEqual(
            events,
            ["status", "process", "socket", "profile", "status",
             "process", "socket"],
        )
        self.assertEqual(result, {
            "gateway_invocation_id": "a" * 32,
            "gateway_main_pid": 123,
            "gateway_exec_main_start_timestamp_monotonic_us": 456,
            "gateway_socket_device": 11,
            "gateway_socket_inode": 12,
        })

        changed = mock.Mock(
            pid_directory_metadata=process.pid_directory_metadata,
            starttime_ticks=790)
        with mock.patch.object(
                CONTROLLER.subprocess, "run", return_value=completed), \
                mock.patch.object(
                    CONTROLLER, "read_alpha_gateway_process_identity",
                    side_effect=(process, changed)), \
                mock.patch.object(
                    CONTROLLER, "read_alpha_gateway_socket",
                    return_value=gateway_socket), \
                mock.patch.object(
                    CONTROLLER, "read_alpha_gateway_profile",
                    return_value=profile), \
                self.assertRaisesRegex(
                    CONTROLLER.ControllerError,
                    "P1_CONTROLLER_GATEWAY_IDENTITY_CHANGED"):
            CONTROLLER._live_gateway_identity(profile)

    def test_root_only_fields_are_attested_while_all_other_fields_are_live(
            self) -> None:
        environment = test_environment()
        root_attestation = {
            field: environment[field]
            for field in CONTROLLER.ROOT_ATTESTED_ENVIRONMENT_FIELDS
        }
        runtime_contents = {
            path: (name + "\n").encode("ascii")
            for name, path in CONTROLLER.RUNTIME_FILES.items()
        }
        gateway_identity = {
            field: environment[field]
            for field in (
                "gateway_invocation_id", "gateway_main_pid",
                "gateway_exec_main_start_timestamp_monotonic_us",
                "gateway_socket_device", "gateway_socket_inode",
            )
        }
        profile_read = mock.Mock(raw=b"profile\n")

        def secure_read(path, _label, _maximum_bytes):
            self.assertIn(path, runtime_contents)
            return runtime_contents[path]

        with mock.patch.object(
                CONTROLLER, "_read_boot_id",
                return_value=environment["boot_id"]) as boot_reader, \
                mock.patch.object(
                    CONTROLLER, "_secure_read",
                    side_effect=secure_read) as runtime_reader, \
                mock.patch.object(
                    CONTROLLER, "_live_gateway_identity",
                    return_value=gateway_identity) as gateway_reader, \
                mock.patch.object(
                    CONTROLLER, "read_alpha_gateway_profile",
                    return_value=profile_read) as profile_reader, \
                mock.patch.object(
                    CONTROLLER.os, "open",
                    side_effect=AssertionError("unexpected direct open")):
            binding = CONTROLLER.current_environment_binding(
                **root_attestation)

        expected = {
            "boot_id": environment["boot_id"],
            **root_attestation,
            **{
                name: CONTROLLER.digest_bytes(runtime_contents[path])
                for name, path in CONTROLLER.RUNTIME_FILES.items()
            },
            "gateway_profile_sha256": CONTROLLER.digest_bytes(
                profile_read.raw),
            **gateway_identity,
        }
        self.assertEqual(binding, expected)
        self.assertEqual(set(binding), CONTROLLER.ENVIRONMENT_FIELDS)
        self.assertEqual(
            set(root_attestation),
            {
                "audit_journal_device", "audit_journal_inode",
                "domain_config_sha256", "gateway_process_profile_sha256",
            },
        )
        self.assertNotIn("domain_config_sha256", CONTROLLER.RUNTIME_FILES)
        self.assertFalse(hasattr(CONTROLLER, "AUDIT_JOURNAL"))
        boot_reader.assert_called_once_with(
            CONTROLLER.BOOT_ID_PATH, "P1_CONTROLLER_BOOT_ID_FILE_INVALID")
        self.assertEqual(
            [call.args[0] for call in runtime_reader.call_args_list],
            list(CONTROLLER.RUNTIME_FILES.values()),
        )
        profile_reader.assert_called_once_with(CONTROLLER.GATEWAY_PROFILE)
        gateway_reader.assert_called_once_with(profile_read)

    def test_invalid_root_attestation_is_rejected_before_live_reads(self) -> None:
        valid = {
            "audit_journal_device": 1,
            "audit_journal_inode": 2,
            "domain_config_sha256": "sha256:" + "8" * 64,
            "gateway_process_profile_sha256": CONTROLLER.digest_bytes(
                CONTROLLER.ALPHA_GATEWAY_PROCESS_PROFILE_BYTES),
        }
        cases = {
            "device-bool": {**valid, "audit_journal_device": True},
            "device-negative": {**valid, "audit_journal_device": -1},
            "device-overflow": {
                **valid, "audit_journal_device": 1 << 64},
            "inode-bool": {**valid, "audit_journal_inode": True},
            "inode-zero": {**valid, "audit_journal_inode": 0},
            "inode-overflow": {**valid, "audit_journal_inode": 1 << 64},
            "config-digest-type": {**valid, "domain_config_sha256": 1},
            "config-digest-format": {
                **valid, "domain_config_sha256": "sha256:" + "G" * 64},
            "process-digest-type": {
                **valid, "gateway_process_profile_sha256": 1},
            "process-digest-mismatch": {
                **valid,
                "gateway_process_profile_sha256": "sha256:" + "f" * 64},
        }
        for name, values in cases.items():
            with self.subTest(name=name), mock.patch.object(
                    CONTROLLER, "_read_boot_id") as boot_reader, \
                    self.assertRaisesRegex(
                        CONTROLLER.ControllerError,
                        "P1_CONTROLLER_ENVIRONMENT_INVALID"):
                CONTROLLER.current_environment_binding(**values)
            boot_reader.assert_not_called()


class P1ControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root_uid_patch = mock.patch.object(
            CONTROLLER, "ROOT_UID", os.geteuid())
        self.root_uid_patch.start()
        self.environment_patch = mock.patch.object(
            CONTROLLER,
            "current_environment_binding",
            side_effect=lambda **_root_attestation: dict(test_environment()),
        )
        self.environment_provider = self.environment_patch.start()

    def tearDown(self) -> None:
        self.environment_patch.stop()
        self.root_uid_patch.stop()

    def test_marker_policy_mismatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            policy, marker = authority(root)
            document = json.loads(marker.read_text())
            document.pop("body_sha256")
            document["policy_file_sha256"] = "sha256:" + "f" * 64
            write(marker, seal(document))
            marker.chmod(0o644)
            with self.assertRaisesRegex(
                    CONTROLLER.ControllerError,
                    "P1_CONTROLLER_AUTHORITY_MARKER_INVALID"):
                CONTROLLER._load_authority_marker(
                    marker, policy, "p1-test",
                    now_ms=CONTROLLER.time.time_ns() // 1_000_000,
                    expected_uid=os.geteuid())

    def test_marker_rejects_malformed_root_attestation(self) -> None:
        cases = {
            "device-bool": ("audit_journal_device", True),
            "device-negative": ("audit_journal_device", -1),
            "inode-zero": ("audit_journal_inode", 0),
            "config-digest": ("domain_config_sha256", "sha256:" + "G" * 64),
            "process-digest": (
                "gateway_process_profile_sha256", "sha256:" + "f" * 64),
        }
        for name, (field, value) in cases.items():
            with self.subTest(name=name), \
                    tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                policy, marker = authority(root)
                document = json.loads(marker.read_text())
                document.pop("body_sha256")
                document["environment"] = dict(document["environment"])
                document["environment"][field] = value
                write(marker, seal(document))
                marker.chmod(0o644)
                with self.assertRaisesRegex(
                        CONTROLLER.ControllerError,
                        "P1_CONTROLLER_AUTHORITY_MARKER_INVALID"):
                    CONTROLLER._load_authority_marker(
                        marker,
                        policy,
                        "p1-test",
                        now_ms=CONTROLLER.time.time_ns() // 1_000_000,
                        expected_uid=os.geteuid(),
                    )

    def test_default_environment_provider_receives_only_root_attestation(
            self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            policy, marker = authority(root, load_probe=True)
            placeholder = root / "placeholder"
            placeholder.write_text("{}", encoding="ascii")
            result = CONTROLLER.run_controller(
                campaign_id="p1-test",
                policy=policy,
                strategy=placeholder,
                export_directory=root / "export",
                source_bundle=placeholder,
                artifact_root=root / "observer",
                status_path=root / "control" / "status.json",
                observer=placeholder,
                authority_marker=marker,
                _maximum_polls=1,
                _sleeper=lambda _seconds: None,
                _expected_marker_uid=os.geteuid(),
            )
        self.assertEqual(result, 0)
        self.environment_provider.assert_called_once_with(
            audit_journal_device=1,
            audit_journal_inode=2,
            domain_config_sha256="sha256:" + "8" * 64,
            gateway_process_profile_sha256=CONTROLLER.digest_bytes(
                CONTROLLER.ALPHA_GATEWAY_PROCESS_PROFILE_BYTES),
        )

    def test_formal_marker_requires_fresh_start_then_remains_runtime_valid(
            self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            now_ms = CONTROLLER.time.time_ns() // 1_000_000
            admitted_at_ms = now_ms - CONTROLLER.ADMISSION_MAXIMUM_AGE_MS - 1
            policy, marker = authority(
                root,
                admitted_at_ms=admitted_at_ms,
                marker_created_at_ms=(
                    admitted_at_ms + CONTROLLER.ADMISSION_MAXIMUM_AGE_MS),
            )
            with self.assertRaisesRegex(
                    CONTROLLER.ControllerError,
                    "P1_CONTROLLER_AUTHORITY_MARKER_INVALID"):
                CONTROLLER._load_authority_marker(
                    marker,
                    policy,
                    "p1-test",
                    now_ms=now_ms,
                    expected_uid=os.geteuid(),
                )
            loaded = CONTROLLER._load_authority_marker(
                marker,
                policy,
                "p1-test",
                now_ms=now_ms,
                expected_uid=os.geteuid(),
                require_fresh_admission=False,
            )
            self.assertEqual(loaded["campaign_id"], "p1-test")

    def test_formal_marker_rejects_before_creation_and_expiry_mismatch(
            self) -> None:
        now_ms = CONTROLLER.time.time_ns() // 1_000_000
        with self.subTest(case="before-creation"), \
                tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            policy, marker = authority(
                root,
                admitted_at_ms=now_ms,
                marker_created_at_ms=now_ms + 1,
            )
            with self.assertRaisesRegex(
                    CONTROLLER.ControllerError,
                    "P1_CONTROLLER_AUTHORITY_MARKER_INVALID"):
                CONTROLLER._load_authority_marker(
                    marker,
                    policy,
                    "p1-test",
                    now_ms=now_ms,
                    expected_uid=os.geteuid(),
                )
        with self.subTest(case="expiry-mismatch"), \
                tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            policy_expires_at_ms = now_ms + 1_000_000
            policy, marker = authority(
                root,
                expires_at_ms=policy_expires_at_ms - 1,
                policy_expires_at_ms=policy_expires_at_ms,
                admitted_at_ms=now_ms,
                marker_created_at_ms=now_ms,
            )
            with self.assertRaisesRegex(
                    CONTROLLER.ControllerError,
                    "P1_CONTROLLER_AUTHORITY_MARKER_INVALID"):
                CONTROLLER._load_authority_marker(
                    marker,
                    policy,
                    "p1-test",
                    now_ms=now_ms,
                    expected_uid=os.geteuid(),
                )

    def test_formal_marker_expiry_is_enforced_during_controller_loop(
            self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            now_ms = CONTROLLER.time.time_ns() // 1_000_000
            expires_at_ms = now_ms + 1_000
            policy, marker = authority(
                root,
                expires_at_ms=expires_at_ms,
                policy_expires_at_ms=expires_at_ms,
                admitted_at_ms=now_ms,
                marker_created_at_ms=now_ms,
            )
            placeholder = root / "placeholder"
            placeholder.write_text("{}")
            clock = (
                now_ms * 1_000_000,
                now_ms * 1_000_000,
                now_ms * 1_000_000,
                expires_at_ms * 1_000_000,
            )
            with mock.patch.object(
                    CONTROLLER.time, "time_ns", side_effect=clock), \
                    self.assertRaisesRegex(
                        CONTROLLER.ControllerError,
                        "P1_CONTROLLER_AUTHORITY_MARKER_INVALID"):
                CONTROLLER.run_controller(
                    campaign_id="p1-test",
                    policy=policy,
                    strategy=placeholder,
                    export_directory=root / "export",
                    source_bundle=placeholder,
                    artifact_root=root / "observer",
                    status_path=root / "control" / "status.json",
                    observer=placeholder,
                    authority_marker=marker,
                    _maximum_polls=1,
                    _sleeper=lambda _seconds: None,
                    _expected_marker_uid=os.geteuid(),
                )

    def test_formal_complete_holds_until_external_stop_and_refreshes_status(
            self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            export = root / "export"
            export_triplet(export)
            policy, marker = authority(root)
            marker_document = json.loads(marker.read_text())
            clock_ms = marker_document["marker_created_at_ms"]

            def tick_ns() -> int:
                nonlocal clock_ms
                clock_ms += 1
                return clock_ms * 1_000_000

            stop_checks = 0

            def external_stop_requested() -> bool:
                nonlocal stop_checks
                stop_checks += 1
                return stop_checks >= 2

            placeholder = root / "placeholder"
            placeholder.write_text("{}")
            status_path = root / "control" / "status.json"
            status_bodies: list[dict] = []
            sleeps: list[float] = []
            original_atomic_status = CONTROLLER.atomic_status

            def record_status(path, body) -> None:
                status_bodies.append(dict(body))
                original_atomic_status(path, body)

            with mock.patch.object(
                    CONTROLLER.time, "time_ns", side_effect=tick_ns), \
                    mock.patch.object(
                        CONTROLLER, "_invoke_observer",
                        return_value=("ITERATIONS_COMPLETE", "COMPLETE", 241)
                    ) as invoke, \
                    mock.patch.object(
                        CONTROLLER, "_assert_observer_continuity"), \
                    mock.patch.object(
                        CONTROLLER, "atomic_status",
                        side_effect=record_status):
                result = CONTROLLER.run_controller(
                    campaign_id="p1-test",
                    policy=policy,
                    strategy=placeholder,
                    export_directory=export,
                    source_bundle=placeholder,
                    artifact_root=root / "observer",
                    status_path=status_path,
                    observer=placeholder,
                    authority_marker=marker,
                    _maximum_polls=1,
                    _sleeper=sleeps.append,
                    _expected_marker_uid=os.geteuid(),
                    _terminal_hold_stop_requested=external_stop_requested,
                )
            self.assertEqual(result, 0)
            self.assertEqual(stop_checks, 2)
            self.assertEqual(sleeps, [CONTROLLER.TERMINAL_HEARTBEAT_SECONDS])
            invoke.assert_called_once()
            terminal = [
                body for body in status_bodies
                if body.get("state") == "TERMINAL"]
            self.assertEqual(len(terminal), 2)
            self.assertLess(
                terminal[0]["updated_at_ms"], terminal[1]["updated_at_ms"])
            for key in terminal[0]:
                if key != "updated_at_ms":
                    self.assertEqual(terminal[0][key], terminal[1][key])
            self.assertEqual(terminal[1]["observer_status"], "COMPLETE")
            self.assertEqual(
                terminal[1]["observer_outcome"], "ITERATIONS_COMPLETE")
            self.assertEqual(terminal[1]["completed_iterations"], 241)
            final_status = CONTROLLER._document(
                status_path.read_bytes(), "P1_CONTROLLER_STATUS_TEST")
            self.assertEqual(final_status["state"], "TERMINAL")
            self.assertEqual(final_status["observer_status"], "COMPLETE")
            self.assertEqual(
                final_status["updated_at_ms"], terminal[1]["updated_at_ms"])

    def test_load_probe_complete_still_returns_without_terminal_hold(
            self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            export = root / "export"
            export_triplet(export)
            policy, marker = authority(root, load_probe=True)
            placeholder = root / "placeholder"
            placeholder.write_text("{}")

            def unexpected_stop_check() -> bool:
                raise AssertionError("load probe entered formal terminal hold")

            with mock.patch.object(
                    CONTROLLER, "_invoke_observer",
                    return_value=("ITERATIONS_COMPLETE", "COMPLETE", 1)), \
                    mock.patch.object(
                        CONTROLLER, "_assert_observer_continuity"):
                result = CONTROLLER.run_controller(
                    campaign_id="p1-test",
                    policy=policy,
                    strategy=placeholder,
                    export_directory=export,
                    source_bundle=placeholder,
                    artifact_root=root / "observer",
                    status_path=root / "control" / "status.json",
                    observer=placeholder,
                    authority_marker=marker,
                    _maximum_polls=1,
                    _sleeper=lambda _seconds: None,
                    _expected_marker_uid=os.geteuid(),
                    _terminal_hold_stop_requested=unexpected_stop_check,
                )
            self.assertEqual(result, 0)

    def test_snapshot_execution_epoch_drift_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            export = root / "export"
            export_triplet(export, execution_epoch="epoch-2")
            policy, marker = authority(root)
            placeholder = root / "placeholder"
            placeholder.write_text("{}")
            with self.assertRaisesRegex(
                    CONTROLLER.ControllerError,
                    "P1_CONTROLLER_EXECUTION_BINDING_DRIFT"):
                CONTROLLER.run_controller(
                    campaign_id="p1-test",
                    policy=policy,
                    strategy=placeholder,
                    export_directory=export,
                    source_bundle=placeholder,
                    artifact_root=root / "observer",
                    status_path=root / "control" / "status.json",
                    observer=placeholder,
                    authority_marker=marker,
                    _maximum_polls=1,
                    _sleeper=lambda _seconds: None,
                    _expected_marker_uid=os.geteuid(),
                )

    def test_load_probe_marker_first_snapshot_locks_binding(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            export = root / "export"
            export_triplet(export, execution_epoch="epoch-probe", execution_fencing=9)
            policy, marker = authority(root, load_probe=True)
            placeholder = root / "placeholder"
            placeholder.write_text("{}")
            with mock.patch.object(
                    CONTROLLER, "_invoke_observer",
                    return_value=("COLLECTED", "RUNNING", 0)), \
                    mock.patch.object(CONTROLLER, "_assert_observer_continuity"):
                result = CONTROLLER.run_controller(
                    campaign_id="p1-test",
                    policy=policy,
                    strategy=placeholder,
                    export_directory=export,
                    source_bundle=placeholder,
                    artifact_root=root / "observer",
                    status_path=root / "control" / "status.json",
                    observer=placeholder,
                    authority_marker=marker,
                    _maximum_polls=1,
                    _sleeper=lambda _seconds: None,
                    _expected_marker_uid=os.geteuid(),
                )
            self.assertEqual(result, 0)
            status = json.loads((root / "control" / "status.json").read_text())
            self.assertEqual(
                status["locked_execution_service_epoch"], "epoch-probe")
            self.assertEqual(
                status["locked_execution_service_fencing_generation"], 9)

    def test_load_probe_marker_rejects_subsequent_binding_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            policy, marker = authority(root, load_probe=True)
            placeholder = root / "placeholder"
            placeholder.write_text("{}")
            base = {
                "export_receipt_identity": "export-receipt",
                "commit_sequence": 1,
                "generation": "generation-00000000000000000001-fixture00000001",
                "snapshot_body_sha256": "sha256:" + "a" * 64,
                "lease_generation": 1,
                "snapshot_generated_at_ms": 1,
                "exported_at_ms": 2,
                "execution_service_fencing_generation": 1,
                "paths": (placeholder, placeholder, placeholder),
                "contents": (b"snapshot", b"lease", b"export"),
            }
            first = {**base, "identity": "first", "execution_service_epoch": "epoch-1"}
            second = {**base, "identity": "second", "execution_service_epoch": "epoch-2"}
            with mock.patch.object(
                    CONTROLLER, "read_stable_triplet",
                    side_effect=(first, first, second)), \
                    mock.patch.object(
                        CONTROLLER, "_invoke_observer",
                        return_value=("COLLECTED", "RUNNING", 0)), \
                    mock.patch.object(
                        CONTROLLER, "_pinned_triplet",
                        return_value=nullcontext(base["paths"])), \
                    mock.patch.object(CONTROLLER, "_assert_observer_continuity"), \
                    self.assertRaisesRegex(
                        CONTROLLER.ControllerError,
                        "P1_CONTROLLER_EXECUTION_BINDING_DRIFT"):
                CONTROLLER.run_controller(
                    campaign_id="p1-test",
                    policy=policy,
                    strategy=placeholder,
                    export_directory=root / "export",
                    source_bundle=placeholder,
                    artifact_root=root / "observer",
                    status_path=root / "control" / "status.json",
                    observer=placeholder,
                    authority_marker=marker,
                    _maximum_polls=2,
                    _sleeper=lambda _seconds: None,
                    _expected_marker_uid=os.geteuid(),
                )

    def test_load_probe_marker_rejects_live_gateway_identity_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            export = root / "export"
            export_triplet(export)
            policy, marker = authority(root, load_probe=True)
            placeholder = root / "placeholder"
            placeholder.write_text("{}")
            drifted = dict(test_environment())
            drifted["gateway_invocation_id"] = "b" * 32
            environments = iter((test_environment(), drifted))
            with self.assertRaisesRegex(
                    CONTROLLER.ControllerError,
                    "P1_CONTROLLER_GATEWAY_IDENTITY_DRIFT"):
                CONTROLLER.run_controller(
                    campaign_id="p1-test",
                    policy=policy,
                    strategy=placeholder,
                    export_directory=export,
                    source_bundle=placeholder,
                    artifact_root=root / "observer",
                    status_path=root / "control" / "status.json",
                    observer=placeholder,
                    authority_marker=marker,
                    _maximum_polls=1,
                    _sleeper=lambda _seconds: None,
                    _expected_marker_uid=os.geteuid(),
                    _environment_provider=lambda: next(environments),
                )

    def test_expired_load_probe_marker_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            now_ms = CONTROLLER.time.time_ns() // 1_000_000
            policy, marker = authority(
                root, load_probe=True, expires_at_ms=now_ms - 1)
            with self.assertRaisesRegex(
                    CONTROLLER.ControllerError,
                    "P1_CONTROLLER_AUTHORITY_MARKER_INVALID"):
                CONTROLLER._load_authority_marker(
                    marker,
                    policy,
                    "p1-test",
                    now_ms=now_ms,
                    expected_uid=os.geteuid(),
                )

    def test_duplicate_triplet_invoked_once_and_status_is_separate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            export = root / "export"
            export_triplet(export)
            control = root / "control"
            artifact = root / "observer"
            fake = root / "observer-fake"
            count = root / "count"
            arguments = root / "arguments.json"
            fake.write_text(
                "#!/usr/bin/python3\n"
                "import json\n"
                "import sys\n"
                "from pathlib import Path\n"
                f"p=Path({str(count)!r}); p.write_text("
                "(p.read_text() if p.exists() else '')+'1')\n"
                f"Path({str(arguments)!r}).write_text("
                "json.dumps(sys.argv[1:]))\n"
                "print('hepta_bounded_shadow_observer: PASS "
                "outcome=COLLECTED status=RUNNING iterations=0')\n")
            fake.chmod(0o755)
            placeholder = root / "placeholder"
            placeholder.write_text("{}")
            policy, marker = authority(root)
            with mock.patch.object(
                    CONTROLLER, "_assert_observer_continuity"):
                result = CONTROLLER.run_controller(
                    campaign_id="p1-test",
                    policy=policy,
                    strategy=placeholder,
                    export_directory=export,
                    source_bundle=placeholder,
                    artifact_root=artifact,
                    status_path=control / "status.json",
                    observer=fake,
                    authority_marker=marker,
                    _maximum_polls=2,
                    _sleeper=lambda _seconds: None,
                    _expected_marker_uid=os.geteuid(),
                )
            self.assertEqual(result, 0)
            self.assertEqual(count.read_text(), "1")
            invoked = json.loads(arguments.read_text())
            for option, name in zip(
                    (
                        "--snapshot",
                        "--watch-lease-receipt",
                        "--watch-export-receipt",
                    ),
                    CONTROLLER.EXPORT_FILES,
                    strict=True):
                self.assertEqual(
                    Path(invoked[invoked.index(option) + 1]).name,
                    name,
                )
            self.assertEqual(
                list(control.glob(".p1-observer-input-*")), [])
            status_path = control / "status.json"
            self.assertEqual(stat.S_IMODE(status_path.stat().st_mode), 0o600)
            status = json.loads(status_path.read_text())
            self.assertEqual(status["observer_invocations"], 1)
            self.assertFalse(artifact.exists())

    def test_committed_generation_rejects_wrong_hash_and_body(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            export = root / "export"
            export_triplet(export)
            current = export / CONTROLLER.EXPORT_COMMIT_NAME
            commit = json.loads(current.read_text(encoding="ascii"))
            commit.pop("body_sha256")
            commit["snapshot_file_sha256"] = "sha256:" + "f" * 64
            replacement = current.with_name(".wrong-hash")
            write(replacement, seal(commit))
            replacement.chmod(0o440)
            os.replace(replacement, current)
            with self.assertRaisesRegex(
                    CONTROLLER.ControllerError,
                    "P1_CONTROLLER_EXPORT_BINDING_INVALID"):
                CONTROLLER.read_stable_triplet(export)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            export = root / "export"
            export_triplet(export)
            commit = json.loads((
                export / CONTROLLER.EXPORT_COMMIT_NAME
            ).read_text(encoding="ascii"))
            snapshot = (
                export / CONTROLLER.EXPORT_GENERATIONS_NAME /
                commit["generation"] / CONTROLLER.EXPORT_FILES[0])
            document = json.loads(snapshot.read_text(encoding="ascii"))
            document["generated_at_ms"] += 1
            snapshot.chmod(0o600)
            snapshot.write_bytes(CONTROLLER.canonical_bytes(document))
            snapshot.chmod(0o440)
            with self.assertRaisesRegex(
                    CONTROLLER.ControllerError,
                    "P1_CONTROLLER_SNAPSHOT_DIGEST_INVALID"):
                CONTROLLER.read_stable_triplet(export)

    def test_pointer_aba_is_rejected_even_when_bytes_return_to_old_commit(
            self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            export = Path(temporary) / "export"
            export_triplet(export, generation=1)
            current = export / CONTROLLER.EXPORT_COMMIT_NAME
            original_contents = current.read_bytes()
            original_read = CONTROLLER._secure_read
            current_reads = 0

            def aba_read(path, *args, **kwargs):
                nonlocal current_reads
                contents = original_read(path, *args, **kwargs)
                if path == current:
                    current_reads += 1
                    if current_reads == 1:
                        export_triplet(export, generation=2)
                        restored = current.with_name(".aba-restored")
                        restored.write_bytes(original_contents)
                        restored.chmod(0o440)
                        os.replace(restored, current)
                return contents

            with mock.patch.object(
                    CONTROLLER, "_secure_read", side_effect=aba_read), \
                    self.assertRaisesRegex(
                        CONTROLLER.TripletNotReady,
                        "P1_CONTROLLER_EXPORT_COMMIT_IN_PROGRESS"):
                CONTROLLER.read_stable_triplet(export)

    def test_expected_export_close_aborts_without_triplet_lost(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            export = root / "export"
            export_triplet(export)
            end_export_authority(export)
            placeholder = root / "placeholder"
            placeholder.write_text("{}")
            policy, marker = authority(root)
            status_path = root / "control" / "status.json"
            with mock.patch.object(CONTROLLER, "_invoke_observer") as invoke:
                result = CONTROLLER.run_controller(
                    campaign_id="p1-test",
                    policy=policy,
                    strategy=placeholder,
                    export_directory=export,
                    source_bundle=placeholder,
                    artifact_root=root / "observer",
                    status_path=status_path,
                    observer=placeholder,
                    authority_marker=marker,
                    _maximum_polls=1,
                    _sleeper=lambda _seconds: None,
                    _expected_marker_uid=os.geteuid(),
                )
            self.assertEqual(result, 78)
            invoke.assert_not_called()
            status = json.loads(status_path.read_text(encoding="ascii"))
            self.assertEqual(status["state"], "ABORTED")
            self.assertEqual(
                status["reason"],
                "P1_CONTROLLER_EXPORT_AUTHORITY_ENDED_CLOSED")
            self.assertNotIn("TRIPLET_LOST", status["reason"])

    def test_active_authority_without_commit_becomes_lost_after_grace(
            self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            placeholder = root / "placeholder"
            placeholder.write_text("{}")
            policy, marker = authority(root)
            clock = [0.0]

            def sleep(seconds: float) -> None:
                clock[0] += max(seconds, 3.0)

            with mock.patch.object(
                    CONTROLLER.time, "monotonic",
                    side_effect=lambda: clock[0]), \
                    self.assertRaisesRegex(
                        CONTROLLER.ControllerError,
                        "P1_CONTROLLER_EXPORT_TRIPLET_LOST"):
                CONTROLLER.run_controller(
                    campaign_id="p1-test",
                    policy=policy,
                    strategy=placeholder,
                    export_directory=root / "export",
                    source_bundle=placeholder,
                    artifact_root=root / "observer",
                    status_path=root / "control" / "status.json",
                    observer=placeholder,
                    authority_marker=marker,
                    _maximum_polls=4,
                    _sleeper=sleep,
                    _expected_marker_uid=os.geteuid(),
                )

    def test_primary_observer_failure_survives_concurrent_expected_close(
            self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            export = root / "export"
            export_triplet(export)
            placeholder = root / "placeholder"
            placeholder.write_text("{}")
            policy, marker = authority(root)

            def fail_after_close(**_kwargs):
                end_export_authority(export, status="CLOSING")
                raise CONTROLLER.ControllerError(
                    "P1_CONTROLLER_PRIMARY_OBSERVER_FAILURE")

            with mock.patch.object(
                    CONTROLLER, "_invoke_observer",
                    side_effect=fail_after_close), \
                    self.assertRaisesRegex(
                        CONTROLLER.ControllerError,
                        "P1_CONTROLLER_PRIMARY_OBSERVER_FAILURE"):
                CONTROLLER.run_controller(
                    campaign_id="p1-test",
                    policy=policy,
                    strategy=placeholder,
                    export_directory=export,
                    source_bundle=placeholder,
                    artifact_root=root / "observer",
                    status_path=root / "control" / "status.json",
                    observer=placeholder,
                    authority_marker=marker,
                    _maximum_polls=1,
                    _sleeper=lambda _seconds: None,
                    _expected_marker_uid=os.geteuid(),
                )

    def test_status_inside_observer_root_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(
                    CONTROLLER.ControllerError,
                    "P1_CONTROLLER_STATUS_INSIDE_ARTIFACT_ROOT"):
                CONTROLLER.run_controller(
                    campaign_id="p1-test",
                    policy=root / "policy",
                    strategy=root / "strategy",
                    export_directory=root / "export",
                    source_bundle=root / "source",
                    artifact_root=root / "observer",
                    status_path=root / "observer" / "status.json",
                    observer=root / "fake",
                    authority_marker=root / "authority",
                    _maximum_polls=1,
                )

    def test_commit_change_before_exec_skips_old_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            export = root / "export"
            export_triplet(export, generation=1)
            control = root / "control"
            fake = root / "observer-fake"
            arguments = root / "arguments.json"
            fake.write_text(
                "#!/usr/bin/python3\n"
                "import json\n"
                "import sys\n"
                "from pathlib import Path\n"
                f"Path({str(arguments)!r}).write_text("
                "json.dumps(sys.argv[1:]))\n"
                "print('hepta_bounded_shadow_observer: PASS "
                "outcome=COLLECTED status=RUNNING iterations=0')\n")
            fake.chmod(0o755)
            placeholder = root / "placeholder"
            placeholder.write_text("{}")
            policy, marker = authority(root)
            original_read = CONTROLLER.read_stable_triplet
            reads = 0

            def changing_read(directory):
                nonlocal reads
                reads += 1
                if reads == 2:
                    export_triplet(directory, generation=2)
                return original_read(directory)

            with mock.patch.object(
                    CONTROLLER,
                    "read_stable_triplet",
                    side_effect=changing_read), \
                    mock.patch.object(
                        CONTROLLER, "_assert_observer_continuity"):
                result = CONTROLLER.run_controller(
                    campaign_id="p1-test",
                    policy=policy,
                    strategy=placeholder,
                    export_directory=export,
                    source_bundle=placeholder,
                    artifact_root=root / "observer",
                    status_path=control / "status.json",
                    observer=fake,
                    authority_marker=marker,
                    _maximum_polls=2,
                    _sleeper=lambda _seconds: None,
                    _expected_marker_uid=os.geteuid(),
                )
            self.assertEqual(result, 0)
            invoked = json.loads(arguments.read_text())
            self.assertEqual(
                Path(invoked[invoked.index("--snapshot") + 1]).name,
                CONTROLLER.EXPORT_FILES[0],
            )
            status = json.loads((control / "status.json").read_text())
            self.assertEqual(status["observer_invocations"], 1)
            self.assertEqual(status["last_lease_generation"], 2)

    def test_observer_continuity_rejects_missed_sample_immediately(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            artifact = Path(temporary)
            write(artifact / "observer-state.json", seal({
                "schema": "hepta.bounded-shadow-observer-state.v1",
                "version": 1,
                "campaign_id": "p1-test",
                "missed_sample_count": 1,
                "segment_status": "OPEN",
                "paper_authorized": False,
                "live_authorized": False,
                "mutation_attempted": False,
                "direct_broker_access": False,
            }))
            with self.assertRaisesRegex(
                    CONTROLLER.ControllerError,
                    "P1_CONTROLLER_MISSED_SAMPLE_COUNT_NONZERO"):
                CONTROLLER._assert_observer_continuity(
                    artifact,
                    campaign_id="p1-test",
                    observer_outcome="COLLECTED",
                )

    def test_observer_continuity_rejects_closed_segment_immediately(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            artifact = Path(temporary)
            write(artifact / "observer-state.json", seal({
                "schema": "hepta.bounded-shadow-observer-state.v1",
                "version": 1,
                "campaign_id": "p1-test",
                "missed_sample_count": 0,
                "segment_status": "CLOSED",
                "paper_authorized": False,
                "live_authorized": False,
                "mutation_attempted": False,
                "direct_broker_access": False,
            }))
            with self.assertRaisesRegex(
                    CONTROLLER.ControllerError,
                    "P1_CONTROLLER_SEGMENT_CLOSED"):
                CONTROLLER._assert_observer_continuity(
                    artifact,
                    campaign_id="p1-test",
                    observer_outcome="COLLECTED",
                )

    def test_observer_continuity_accepts_zero_miss_open_segment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            artifact = Path(temporary)
            write(artifact / "observer-state.json", seal({
                "schema": "hepta.bounded-shadow-observer-state.v1",
                "version": 1,
                "campaign_id": "p1-test",
                "missed_sample_count": 0,
                "segment_status": "OPEN",
                "paper_authorized": False,
                "live_authorized": False,
                "mutation_attempted": False,
                "direct_broker_access": False,
            }))
            CONTROLLER._assert_observer_continuity(
                artifact,
                campaign_id="p1-test",
                observer_outcome="COLLECTED",
            )

    def test_controller_halts_on_observer_missed_sample(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            export = root / "export"
            export_triplet(export)
            artifact = root / "observer"
            write(artifact / "observer-state.json", seal({
                "schema": "hepta.bounded-shadow-observer-state.v1",
                "version": 1,
                "campaign_id": "p1-test",
                "missed_sample_count": 1,
                "segment_status": "OPEN",
                "paper_authorized": False,
                "live_authorized": False,
                "mutation_attempted": False,
                "direct_broker_access": False,
            }))
            placeholder = root / "placeholder"
            placeholder.write_text("{}", encoding="ascii")
            policy, marker = authority(root)
            with mock.patch.object(
                    CONTROLLER,
                    "_invoke_observer",
                    return_value=("COLLECTED", "RUNNING", 0)), \
                    self.assertRaisesRegex(
                        CONTROLLER.ControllerError,
                        "P1_CONTROLLER_MISSED_SAMPLE_COUNT_NONZERO"):
                CONTROLLER.run_controller(
                    campaign_id="p1-test",
                    policy=policy,
                    strategy=placeholder,
                    export_directory=export,
                    source_bundle=placeholder,
                    artifact_root=artifact,
                    status_path=root / "control" / "status.json",
                    observer=placeholder,
                    authority_marker=marker,
                    _maximum_polls=1,
                    _sleeper=lambda _seconds: None,
                    _expected_marker_uid=os.geteuid(),
                )

    def test_observer_exact_failure_reason_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            export = root / "export"
            export_triplet(export)
            fake = root / "observer-fake"
            fake.write_text(
                "#!/usr/bin/python3\n"
                "import sys\n"
                "print('hepta_bounded_shadow_observer: FAIL "
                "BOUNDED_SHADOW_SNAPSHOT_METADATA_INVALID', "
                "file=sys.stderr)\n"
                "raise SystemExit(78)\n")
            fake.chmod(0o755)
            placeholder = root / "placeholder"
            placeholder.write_text("{}")
            policy, marker = authority(root)
            with self.assertRaisesRegex(
                    CONTROLLER.ControllerError,
                    "P1_CONTROLLER_OBSERVER_FAILED_78_"
                    "BOUNDED_SHADOW_SNAPSHOT_METADATA_INVALID"):
                CONTROLLER.run_controller(
                    campaign_id="p1-test",
                    policy=policy,
                    strategy=placeholder,
                    export_directory=export,
                    source_bundle=placeholder,
                    artifact_root=root / "observer",
                    status_path=root / "control" / "status.json",
                    observer=fake,
                    authority_marker=marker,
                    _maximum_polls=1,
                    _sleeper=lambda _seconds: None,
                    _expected_marker_uid=os.geteuid(),
                )


if __name__ == "__main__":
    unittest.main()
