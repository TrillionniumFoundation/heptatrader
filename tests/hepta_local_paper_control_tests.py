#!/usr/bin/env python3

from __future__ import annotations

import contextlib
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
from typing import Any, Callable
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import hepta_local_ai_paper_agent as agent
from scripts import hepta_local_paper_control as control

TEST_AUTH_PROFILE_ID = "openai:test-profile"
TEST_AUTH_PROFILE_SHA256 = agent.auth_profile_sha256(TEST_AUTH_PROFILE_ID)
TEST_AUTH_PROFILE_ALLOWLIST = [
    TEST_AUTH_PROFILE_ID,
    "openai:other-profile",
]
TEST_AUTH_PROFILE_ALLOWLIST_SHA256 = (
    agent.auth_profile_allowlist_sha256(TEST_AUTH_PROFILE_ALLOWLIST))
ORIGINAL_REQUIRE_RUNTIME_BINDING = agent.require_runtime_binding


class LocalPaperControlTests(unittest.TestCase):
    def test_atomic_replace_parent_requires_precreated_exact_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            uid, gid = os.geteuid(), os.getegid()
            parent = root / "drop-in"
            parent.mkdir(mode=0o755)
            # ``mkdir(mode=...)`` is umask-filtered.  The production contract
            # intentionally requires an exact 0755 parent, so make the test
            # fixture explicit under the restrictive CI umask (077).
            parent.chmod(0o755)
            control._require_atomic_replace_parent(
                parent, uid=uid, gid=gid, mode=0o755)

            with self.assertRaisesRegex(
                    control.LocalPaperError,
                    "atomic replacement parent unsafe"):
                control._require_atomic_replace_parent(
                    root / "absent", uid=uid, gid=gid, mode=0o755)

            parent.chmod(0o700)
            with self.assertRaisesRegex(
                    control.LocalPaperError,
                    "atomic replacement parent unsafe"):
                control._require_atomic_replace_parent(
                    parent, uid=uid, gid=gid, mode=0o755)
            parent.chmod(0o755)

            with self.assertRaisesRegex(
                    control.LocalPaperError,
                    "atomic replacement parent unsafe"):
                control._require_atomic_replace_parent(
                    parent, uid=uid + 1, gid=gid, mode=0o755)

            link = root / "drop-in-link"
            link.symlink_to(parent, target_is_directory=True)
            with self.assertRaisesRegex(
                    control.LocalPaperError,
                    "atomic replacement parent unsafe"):
                control._require_atomic_replace_parent(
                    link, uid=uid, gid=gid, mode=0o755)

            regular = root / "regular"
            regular.write_bytes(b"not a directory\n")
            with self.assertRaisesRegex(
                    control.LocalPaperError,
                    "atomic replacement parent unsafe"):
                control._require_atomic_replace_parent(
                    regular, uid=uid, gid=gid, mode=0o755)

    @staticmethod
    def write_sealed(path: Path, body: dict[str, object]) -> dict[str, object]:
        document = control._sealed_document(body)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(control._canonical_json(document))
        path.chmod(0o600)
        return document

    @staticmethod
    @contextlib.contextmanager
    def guardian_fixture(root: Path):  # type: ignore[no-untyped-def]
        runtime = root / "guardian-runtime"
        host_authority = root / "host-authority"
        with mock.patch.object(control, "ROOT_UID", os.geteuid()), \
                mock.patch.object(control, "ROOT_GID", os.getegid()), \
                mock.patch.object(
                    control, "HOST_AUTHORITY_DIRECTORY", host_authority), \
                mock.patch.object(
                    control, "BROKER_BOUNDARY_RECEIPT_PATH",
                    root / "broker-boundary" / "current-boundary.v1.json"), \
                mock.patch.object(
                    control, "GUARDIAN_RUNTIME_ROOT", runtime), \
                mock.patch.object(
                    control, "GUARDIAN_REQUEST_PATH",
                    runtime / "guardian-request.json"), \
                mock.patch.object(
                    control, "GUARDIAN_ACTIVE_PATH", runtime / "active.json"), \
                mock.patch.object(
                    control, "BROKER_START_PERMIT_PATH",
                    runtime / "broker-start-permit.json"), \
                mock.patch.object(
                    control, "_guardian_identity_matches",
                    return_value=True):
            yield control._process_identity(os.getpid()), "a" * 32

    @staticmethod
    def activation_systemctl(
            calls: list[list[str]] | None = None,
    ) -> Callable[[list[str]], None]:
        def systemctl(arguments: list[str]) -> None:
            if calls is not None:
                calls.append(list(arguments))
            if arguments in (
                    ["start", "hepta-ib-paper-domain-preflight@alpha.service"],
                    ["start", "hepta-execution-ib-paper@alpha.service"]):
                with control._host_authority_lease(
                        control.HOST_AUTHORITY_DIRECTORY) as lease:
                    reservation_payload = (
                        control._host_authority_owner_payload(lease))
                    if reservation_payload is None:
                        return
                    reservation = json.loads(reservation_payload)
                    if reservation.get("schema") != (
                            control.BROKER_ACTIVATION_RESERVATION_SCHEMA):
                        return
                    consumed_path = control._broker_activation_consumed_path(
                        control.HOST_AUTHORITY_DIRECTORY,
                        reservation["activation_id"])
                    consumed_payload = consumed_path.read_bytes()
                    consumed = json.loads(consumed_payload)
                    process = control._process_identity(os.getpid())
                    runtime = control._sealed_document({
                        "schema": control.PAPER_RUNTIME_OWNER_SCHEMA,
                        "version": 1,
                        "status": control.PAPER_RUNTIME_OWNER_STATUS,
                        "adopted_at_ms": reservation["issued_at_ms"],
                        "boot_id": reservation["boot_id"],
                        "domain": "alpha",
                        "activation_id": reservation["activation_id"],
                        "transaction_id": reservation["transaction_id"],
                        "operation": reservation["operation"],
                        "phase": reservation["phase"],
                        "guardian_request_id":
                            reservation["guardian_request_id"],
                        "request_sha256": reservation["request_sha256"],
                        "reservation_file_sha256":
                            control._sha256(reservation_payload),
                        "reservation_body_sha256":
                            reservation["body_sha256"],
                        "activation_consumed_file_sha256":
                            control._sha256(consumed_payload),
                        "activation_consumed_body_sha256":
                            consumed["body_sha256"],
                        "broker_start_permit_file_sha256":
                            reservation["broker_start_permit_file_sha256"],
                        "broker_start_permit_body_sha256":
                            reservation["broker_start_permit_body_sha256"],
                        "pre_activation_boundary_state_sha256":
                            consumed["pre_activation_boundary_state_sha256"],
                        "active_boundary_state_sha256":
                            consumed["active_boundary_state_sha256"],
                        "target_identity_manifest_sha256":
                            reservation["target_identity_manifest_sha256"],
                        "target_drop_in_sha256":
                            reservation["target_drop_in_sha256"],
                        "execution_identity": "hepta-ib-exec-alpha",
                        "execution_uid": 2121, "execution_gid": 2121,
                        "control_directory":
                            "/run/hepta/ib-paper-control-alpha",
                        "kill_switch_marker":
                            "/run/hepta/ib-paper-control-alpha/kill-switch",
                        "guard_pid": process["pid"],
                        "guard_start_ticks": process["start_ticks"],
                        "guard_exe_sha256": process["exe_sha256"],
                        "guard_argv_sha256": process["argv_sha256"],
                        "mutation_scope":
                            "PAPER_DOMAIN_EGRESS_GUARD_ONLY",
                        "paper_authorized": True,
                        "live_authorized": False,
                    })
                    owner_path = (
                        control.HOST_AUTHORITY_DIRECTORY /
                        control.HOST_AUTHORITY_OWNER_NAME)
                    control._write_root_transaction(
                        owner_path, control._canonical_json(runtime),
                        exclusive=False)
                    consumed_path.unlink()
                    control._fsync_directory(consumed_path.parent)
                return
            if (
                    len(arguments) != 2 or
                    arguments[0] not in {"start", "restart"} or
                    arguments[1] != control.BROKER_UNIT):
                return
            with control._host_authority_lease(
                    control.HOST_AUTHORITY_DIRECTORY) as lease:
                reservation_payload = control._host_authority_owner_payload(
                    lease)
                if reservation_payload is None:
                    return
                reservation = json.loads(reservation_payload)
                if reservation.get("schema") != (
                        control.BROKER_ACTIVATION_RESERVATION_SCHEMA):
                    return
                publisher = control._process_identity(os.getpid())
                active_state_sha256 = "sha256:" + "e" * 64
                boundary_body = {
                    "schema": control.BROKER_BOUNDARY_RECEIPT_SCHEMA,
                    "version": 1, "status": "EXACT_ACTIVE",
                    "boot_id": reservation["boot_id"], "generation": 2,
                    "publisher_pid": publisher["pid"],
                    "publisher_start_ticks": publisher["start_ticks"],
                    "observed_at_ms": reservation["issued_at_ms"],
                    "observed_monotonic_ns": 1, "state": "ACTIVE",
                    "family": "inet", "table": "heptatrader",
                    "chain": "output", "guard_chain": "broker_guard",
                    "protected_tcp_destination_ports":
                        [4001, 4002, 7496, 7497],
                    "protected_port_count": 4,
                    "authorized_connector_count": 1,
                    "authorized_uids": [2121],
                    "authorized_connectors": [{"domain_id": "alpha"}],
                    "paper_authorized": True, "live_authorized": False,
                    "source_policy_sha256": "sha256:" + "1" * 64,
                    "identity_manifest_sha256":
                        reservation["target_identity_manifest_sha256"],
                    "effective_policy_sha256": "sha256:" + "2" * 64,
                    "table_semantic_sha256": "sha256:" + "3" * 64,
                    "state_sha256": active_state_sha256,
                    "source_fingerprints": [{
                        "path": str(control.PRODUCTION_IDENTITIES_PATH),
                        "present": True,
                        "sha256": reservation[
                            "target_identity_manifest_sha256"],
                    }],
                }
                boundary = control._sealed_document(boundary_body)
                boundary_payload = control._canonical_json(boundary)
                control._write_root_transaction(
                    control.BROKER_BOUNDARY_RECEIPT_PATH, boundary_payload,
                    exclusive=False)
                body = {
                    "schema": control.BROKER_ACTIVATION_CONSUMED_SCHEMA,
                    "version": 1,
                    "status": control.BROKER_ACTIVATION_CONSUMED_STATUS,
                    "activation_id": reservation["activation_id"],
                    "consumed_at_ms": reservation["issued_at_ms"],
                    "boot_id": reservation["boot_id"],
                    "reservation_file_sha256":
                        control._sha256(reservation_payload),
                    "reservation_body_sha256":
                        reservation["body_sha256"],
                    "broker_start_permit_file_sha256":
                        reservation["broker_start_permit_file_sha256"],
                    "broker_start_permit_body_sha256":
                        reservation["broker_start_permit_body_sha256"],
                    "guardian_request_id":
                        reservation["guardian_request_id"],
                    "domain": reservation["domain"],
                    "transaction_id": reservation["transaction_id"],
                    "operation": reservation["operation"],
                    "phase": reservation["phase"],
                    "request_sha256": reservation["request_sha256"],
                    "target_identity_manifest_sha256":
                        reservation["target_identity_manifest_sha256"],
                    "target_drop_in_sha256":
                        reservation["target_drop_in_sha256"],
                    "control_image_sha256":
                        reservation["control_image_sha256"],
                    "required_pre_activation_boundary": "DENY_ALL",
                    "pre_activation_boundary_state_sha256":
                        "sha256:" + "d" * 64,
                    "active_boundary_status": "EXACT_ACTIVE",
                    "active_boundary_state_sha256": active_state_sha256,
                    "paper_authorized": True, "live_authorized": False,
                }
                completion = control._sealed_document(body)
                completion_path = control._broker_activation_consumed_path(
                    control.HOST_AUTHORITY_DIRECTORY,
                    reservation["activation_id"])
                control._write_root_transaction(
                    completion_path, control._canonical_json(completion),
                    exclusive=True)
                control._remove_runtime_artifact(
                    control.BROKER_START_PERMIT_PATH)
        return systemctl

    def test_host_authority_lease_owner_and_busy_paths_fail_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "host-authority"
            owner_payload = b'{"schema":"terminal-challenge"}\n'
            with mock.patch.object(control, "ROOT_UID", os.geteuid()), \
                    mock.patch.object(control, "ROOT_GID", os.getegid()):
                with control._host_authority_lease(root) as lease:
                    started = time.monotonic()
                    with self.assertRaisesRegex(
                            control.LocalPaperError, "lease busy"):
                        with control._host_authority_lease(root):
                            self.fail("nested host lease unexpectedly acquired")
                    self.assertLess(time.monotonic() - started, 1.0)
                    control._publish_host_authority_owner(
                        lease, owner_payload)
                with control._host_authority_lease(root) as lease:
                    with self.assertRaisesRegex(
                            control.LocalPaperError, "owner active"):
                        control._require_host_authority_owner_absent(lease)
                    control._remove_exact_host_authority_owner(
                        lease, owner_payload, absent_ok=False)
                root.chmod(0o755)
                with self.assertRaisesRegex(
                        control.LocalPaperError, "directory unsafe"):
                    with control._host_authority_lease(root):
                        self.fail("unsafe host directory unexpectedly accepted")

    def test_enable_active_terminal_owner_never_reaches_authorized_inputs(
            self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            authority, identities, env_root, drop_in = self.fixture(root)
            paper = env_root / "alpha.ib-paper.env"
            gateway = env_root / "alpha.env"
            originals = (identities.read_bytes(), paper.read_bytes(),
                         gateway.read_bytes())
            calls: list[list[str]] = []
            with self.guardian_fixture(root) as (guardian, request_id):
                terminal_owner = b'{"schema":"terminal-challenge"}\n'
                with control._host_authority_lease(
                        control.HOST_AUTHORITY_DIRECTORY) as lease:
                    control._publish_host_authority_owner(
                        lease, terminal_owner)
                with self.assertRaisesRegex(
                        control.LocalPaperError, "owner active"):
                    control._enable_transaction(
                        domain="alpha", authority_path=authority,
                        identities_path=identities, env_root=env_root,
                        drop_in_path=drop_in, gateway_env_root=env_root,
                        systemctl=calls.append,
                        transaction_root=root / "control-state",
                        guardian_identity=guardian,
                        guardian_request_id=request_id)
                with control._host_authority_lease(
                        control.HOST_AUTHORITY_DIRECTORY) as lease:
                    self.assertEqual(
                        control._host_authority_owner_payload(lease),
                        terminal_owner)
                    control._remove_exact_host_authority_owner(
                        lease, terminal_owner, absent_ok=False)
            self.assertEqual(identities.read_bytes(), originals[0])
            self.assertEqual(paper.read_bytes(), originals[1])
            self.assertEqual(gateway.read_bytes(), originals[2])
            self.assertFalse(drop_in.exists())
            self.assertNotIn(["restart", control.BROKER_UNIT], calls)

    def test_activation_failure_rolls_back_inputs_profiles_owner_and_permit(
            self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            authority, identities, env_root, drop_in = self.fixture(root)
            paper = env_root / "alpha.ib-paper.env"
            gateway = env_root / "alpha.env"
            original_paper = paper.read_bytes()
            original_gateway = gateway.read_bytes()
            calls: list[list[str]] = []

            def failing_systemctl(arguments: list[str]) -> None:
                calls.append(list(arguments))
                if arguments == ["restart", control.BROKER_UNIT]:
                    raise control.LocalPaperError("injected broker failure")

            with self.guardian_fixture(root) as (guardian, request_id), \
                    self.assertRaisesRegex(
                        control.LocalPaperError, "injected broker failure"):
                control._enable_transaction(
                    domain="alpha", authority_path=authority,
                    identities_path=identities, env_root=env_root,
                    drop_in_path=drop_in, gateway_env_root=env_root,
                    systemctl=failing_systemctl,
                    transaction_root=root / "control-state",
                    guardian_identity=guardian,
                    guardian_request_id=request_id)
            self.assertFalse(json.loads(
                identities.read_text(encoding="ascii"))["paper_authorized"])
            self.assertEqual(paper.read_bytes(), original_paper)
            self.assertEqual(gateway.read_bytes(), original_gateway)
            self.assertFalse(drop_in.exists())
            self.assertFalse((root / "guardian-runtime" /
                              "broker-start-permit.json").exists())
            with mock.patch.object(control, "ROOT_UID", os.geteuid()), \
                    mock.patch.object(control, "ROOT_GID", os.getegid()):
                with control._host_authority_lease(
                        root / "host-authority") as lease:
                    self.assertIsNone(
                        control._host_authority_owner_payload(lease))

    def test_activation_handoff_releases_before_broker_and_execution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            authority, identities, env_root, drop_in = self.fixture(root)
            calls: list[list[str]] = []
            locked_writes: list[Path] = []
            original_atomic_write = control._atomic_write
            consumer = self.activation_systemctl()

            def observed_atomic_write(
                    path: Path, payload: bytes, mode: int,
            ) -> None:
                targets = {
                    identities, drop_in, env_root / "alpha.env",
                    env_root / "alpha.ib-paper.env",
                }
                if path in targets:
                    with self.assertRaisesRegex(
                            control.LocalPaperError, "lease busy"):
                        with control._host_authority_lease(
                                control.HOST_AUTHORITY_DIRECTORY):
                            self.fail("authorization write escaped host lease")
                    locked_writes.append(path)
                original_atomic_write(path, payload, mode)

            def systemctl(arguments: list[str]) -> None:
                calls.append(list(arguments))
                consumer(arguments)
                if arguments == [
                        "start", "hepta-execution-ib-paper@alpha.service"]:
                    owner = json.loads((
                        control.HOST_AUTHORITY_DIRECTORY /
                        control.HOST_AUTHORITY_OWNER_NAME).read_bytes())
                    self.assertEqual(owner["schema"],
                                     control.PAPER_RUNTIME_OWNER_SCHEMA)
                    boundary, _payload = control._load_current_broker_boundary()
                    self.assertEqual(boundary["status"], "EXACT_ACTIVE")

            with self.guardian_fixture(root) as (guardian, request_id), \
                    mock.patch.object(
                        control, "_atomic_write",
                        side_effect=observed_atomic_write):
                enabled = control._enable_transaction(
                    domain="alpha", authority_path=authority,
                    identities_path=identities, env_root=env_root,
                    drop_in_path=drop_in, gateway_env_root=env_root,
                    systemctl=systemctl,
                    transaction_root=root / "control-state",
                    guardian_identity=guardian,
                    guardian_request_id=request_id)
            self.assertTrue(enabled["paper_authorized"])
            self.assertEqual(set(locked_writes), {
                identities, drop_in, env_root / "alpha.env",
                env_root / "alpha.ib-paper.env",
            })
            self.assertLess(
                calls.index(["restart", control.BROKER_UNIT]),
                calls.index([
                    "start", "hepta-execution-ib-paper@alpha.service"]))

    def test_active_boundary_drift_rolls_back_before_execution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            authority, identities, env_root, drop_in = self.fixture(root)
            calls: list[list[str]] = []
            consumer = self.activation_systemctl()

            def drifting_systemctl(arguments: list[str]) -> None:
                calls.append(list(arguments))
                if arguments != ["restart", control.BROKER_UNIT]:
                    return
                consumer(arguments)
                boundary = json.loads(
                    control.BROKER_BOUNDARY_RECEIPT_PATH.read_text(
                        encoding="ascii"))
                boundary.pop("body_sha256")
                boundary["state_sha256"] = "sha256:" + "f" * 64
                drifted = control._sealed_document(boundary)
                control._write_root_transaction(
                    control.BROKER_BOUNDARY_RECEIPT_PATH,
                    control._canonical_json(drifted), exclusive=False)

            with self.guardian_fixture(root) as (guardian, request_id), \
                    self.assertRaisesRegex(
                        control.LocalPaperError,
                        "active broker boundary drifted"):
                control._enable_transaction(
                    domain="alpha", authority_path=authority,
                    identities_path=identities, env_root=env_root,
                    drop_in_path=drop_in, gateway_env_root=env_root,
                    systemctl=drifting_systemctl,
                    transaction_root=root / "control-state",
                    guardian_identity=guardian,
                    guardian_request_id=request_id)
            self.assertNotIn(
                ["start", "hepta-execution-ib-paper@alpha.service"], calls)
            self.assertFalse(json.loads(
                identities.read_text(encoding="ascii"))["paper_authorized"])
            self.assertFalse(drop_in.exists())

    def test_consumed_handoff_accepts_fresh_same_state_new_publisher(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _authority, identities, _env_root, drop_in = self.fixture(root)
            transaction_root = root / "control-state"
            authorized = {
                **control._deny_all_document("sha256:" + "8" * 64),
                "identities": [{"uid": 2121}], "paper_authorized": True,
            }
            identities.write_bytes(control._pretty_json(authorized))
            drop_in.parent.mkdir(parents=True, exist_ok=True)
            drop_in.write_bytes(control._broker_override_payload())
            drop_in.chmod(0o644)
            with self.guardian_fixture(root) as (guardian, request_id):
                transaction = control._new_control_transaction(
                    path=control._transaction_path(transaction_root),
                    operation="ENABLE",
                    request={"guardian_request_id": request_id},
                    target_identity_manifest_sha256=control._sha256(
                        identities.read_bytes()),
                    target_drop_in_sha256=control._sha256(
                        drop_in.read_bytes()))
                transaction = control._persist_control_phase(
                    transaction, control._transaction_path(transaction_root),
                    "BEFORE_001_START_BROKER_LOCAL_PAPER")
                permit = control._issue_broker_start_permit(
                    transaction, guardian_identity=guardian,
                    phase=transaction["phase"])
                with control._host_authority_lease(
                        control.HOST_AUTHORITY_DIRECTORY) as lease:
                    reservation, reservation_payload = (
                        control._publish_broker_activation_reservation(
                            lease, transaction=transaction,
                            guardian_identity=guardian, permit=permit))
                self.activation_systemctl()(
                    ["start", control.BROKER_UNIT])
                child = control.subprocess.Popen(
                    ["/bin/sleep", "5"], stdout=control.subprocess.DEVNULL,
                    stderr=control.subprocess.DEVNULL)
                try:
                    publisher = control._process_identity(child.pid)
                    boundary = json.loads(
                        control.BROKER_BOUNDARY_RECEIPT_PATH.read_text(
                            encoding="ascii"))
                    boundary.pop("body_sha256")
                    boundary.update({
                        "generation": 99,
                        "publisher_pid": publisher["pid"],
                        "publisher_start_ticks": publisher["start_ticks"],
                        "observed_at_ms": time.time_ns() // 1_000_000,
                        "observed_monotonic_ns": time.monotonic_ns(),
                    })
                    republished = control._sealed_document(boundary)
                    control._write_root_transaction(
                        control.BROKER_BOUNDARY_RECEIPT_PATH,
                        control._canonical_json(republished), exclusive=False)
                    consumed = control._broker_activation_consumed_path(
                        control.HOST_AUTHORITY_DIRECTORY,
                        reservation["activation_id"])
                    self.assertTrue(consumed.exists())
                    returned, returned_payload = (
                        control._verify_broker_activation_completion(
                        reservation, reservation_payload,
                        control.HOST_AUTHORITY_DIRECTORY))
                    self.assertEqual(returned_payload, consumed.read_bytes())
                    self.assertEqual(returned["status"],
                                     "ACTIVE_BOUNDARY_COMMITTED")
                    self.assertTrue(consumed.exists())
                    with control._host_authority_lease(
                            control.HOST_AUTHORITY_DIRECTORY) as lease:
                        self.assertEqual(
                            control._host_authority_owner_payload(lease),
                            reservation_payload)
                finally:
                    child.terminate()
                    child.wait(timeout=5)

    def test_recovery_terminal_owner_blocks_authorized_broker_start(
            self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            authority, identities, env_root, drop_in = self.fixture(root)
            stack, handoff, handoff_document, command = self.external_fixture(
                root, identities, env_root)
            with stack:
                recovery_path, recovery = self.recovery_authority_fixture(
                    root, handoff, handoff_document)
                terminal_owner = b'{"schema":"terminal-challenge"}\n'
                with control._host_authority_lease(
                        control.HOST_AUTHORITY_DIRECTORY) as lease:
                    control._publish_host_authority_owner(
                        lease, terminal_owner)
                observations: list[tuple[list[str], bool]] = []

                def systemctl(arguments: list[str]) -> None:
                    observations.append((list(arguments), bool(json.loads(
                        identities.read_text(encoding="ascii"))[
                            "paper_authorized"])))

                with mock.patch.object(
                        control, "RECOVERY_AUTHORITY_PATH", recovery_path), \
                        self.assertRaisesRegex(
                            control.LocalPaperError, "owner active"):
                    control._enable_recovery_transaction(
                        authority_path=authority,
                        identities_path=identities, env_root=env_root,
                        gateway_env_root=env_root, drop_in_path=drop_in,
                        recovery_path=recovery_path,
                        recovery_file_sha256=control._sha256(
                            recovery_path.read_bytes()),
                        recovery_body_sha256=str(recovery["body_sha256"]),
                        systemctl=systemctl, command=command,
                        transaction_root=control.LOCAL_PAPER_STATE_ROOT,
                        now_ms=2_000_000,
                        guardian_identity=control._process_identity(
                            os.getpid()), guardian_request_id="a" * 32)
                self.assertFalse(json.loads(
                    identities.read_text(encoding="ascii"))[
                        "paper_authorized"])
                self.assertFalse(any(
                    arguments == ["start", control.BROKER_UNIT] and authorized
                    for arguments, authorized in observations))
                with control._host_authority_lease(
                        control.HOST_AUTHORITY_DIRECTORY) as lease:
                    self.assertEqual(
                        control._host_authority_owner_payload(lease),
                        terminal_owner)
                    control._remove_exact_host_authority_owner(
                        lease, terminal_owner, absent_ok=False)

    @staticmethod
    def effective_status_command(
            *, authorized: bool,
            active_units: set[str] | None = None,
    ) -> object:
        active = set() if active_units is None else set(active_units)

        def command(arguments: list[str]) -> SimpleNamespace:
            if arguments[:3] == [
                    "/usr/bin/systemctl", "show", arguments[2]]:
                state = "active" if arguments[2] in active else "inactive"
                return SimpleNamespace(
                    returncode=0,
                    stdout=("LoadState=loaded\nActiveState=" + state +
                            "\nJob=\n"), stderr="")
            connectors = 1 if authorized else 0
            uids = "2121" if authorized else ""
            return SimpleNamespace(
                returncode=0,
                stdout=(
                    "hepta_broker_egress_policy: PASS policy_sha256=" +
                    "b" * 64 + f" authorized_connectors={connectors} " +
                    f"authorized_uids={uids} protected_ports=4\n"),
                stderr="")
        return command

    @staticmethod
    def _evidence(path: Path) -> dict[str, object]:
        raw = path.read_bytes()
        metadata = path.stat()
        return {
            "path": str(path), "file_sha256": control._sha256(raw),
            "bytes": len(raw), "mode": metadata.st_mode,
            "uid": metadata.st_uid, "gid": metadata.st_gid,
            "nlink": metadata.st_nlink, "device": metadata.st_dev,
            "inode": metadata.st_ino, "mtime_ns": metadata.st_mtime_ns,
            "ctime_ns": metadata.st_ctime_ns,
        }

    def external_fixture(
            self, root: Path, identities: Path, env_root: Path,
    ) -> tuple[contextlib.ExitStack, Path, dict[str, object], object]:
        stack = contextlib.ExitStack()
        uid, gid = os.geteuid(), os.getegid()
        profile = env_root / "alpha.env"
        paper_profile = env_root / "alpha.ib-paper.env"
        paper_profile.write_text(
            "HEPTA_IB_EXECUTION_MODE=PAPER\n"
            "HEPTA_IB_PAPER_ACCOUNT=DU12345\n"
            "HEPTA_IB_PAPER_HOST=127.0.0.1\n"
            "HEPTA_IB_PAPER_PORT=4002\n"
            "HEPTA_IB_PAPER_CLIENT_ID=701\n"
            "HEPTA_IB_PAPER_MAX_ORDER_QTY=1\n"
            "HEPTA_IB_PAPER_MAX_ORDER_NOTIONAL=35000\n"
            "HEPTA_IB_PAPER_MAX_ORDERS_PER_MINUTE=1\n"
            "HEPTA_IB_PAPER_MAX_ACTIVE_ORDERS=1\n"
            "HEPTA_IB_PAPER_MAX_GROSS_POSITION=1\n"
            "HEPTA_IB_PAPER_QUOTE_CONTRACTS="
            "EUR.USD|EUR|CASH|IDEALPRO|USD\n"
            "HEPTA_IB_PAPER_PRIMARY_QUOTE_INSTRUMENT=EUR.USD\n"
            "HEPTA_IB_PAPER_QUOTE_MAX_AGE_MS=5000\n"
            "HEPTA_IB_EXECUTION_GATEWAY_UID=2101\n"
            "HEPTA_IB_EXECUTION_GATEWAY_AGENT_ID=alpha\n"
            "HEPTA_IB_EXECUTION_DOMAIN_ID=PAPER:alpha\n"
            "HEPTA_IB_EXECUTION_MAX_REQUEST_BYTES=16384\n"
            "HEPTA_IB_EXECUTION_IO_TIMEOUT_MS=2500\n"
            "HEPTA_IB_EXECUTION_READINESS_TIMEOUT_MS=12000\n"
            "HEPTA_IB_EXECUTION_RECONNECT_TIMEOUT_MS=180000\n",
            encoding="ascii")
        paper_profile.chmod(0o644)
        dormant = control._paper_gateway_environment(
            profile, paper_profile, "alpha", 2121)
        profile.write_bytes(dormant)
        profile.chmod(0o644)
        artifact_root = root / "p1-artifacts"
        artifact_root.mkdir(mode=0o700)
        paths = {
            "backup": artifact_root / "dormant-backup.env",
            "retained": artifact_root / "dormant-retained.env",
            "retired": artifact_root / "retired-watch.env",
            "transition": artifact_root / "transition.json",
            "deployment": artifact_root / "deployment.json",
            "preimage": artifact_root / "preimage.json",
            "candidate": artifact_root / "candidate.env",
            "domain_kill": artifact_root / "domain-kill",
            "global_kill": artifact_root / "global-kill",
            "paper_candidate": artifact_root / "paper-candidate.env",
            "paper_legacy_backup": artifact_root / "paper-legacy-backup.env",
            "paper_retained_legacy": artifact_root / "paper-retained-legacy.env",
        }
        for name in ("backup", "retained"):
            paths[name].write_bytes(dormant)
            paths[name].chmod(0o600)
        watch = b"passive-watch-profile\n"
        paths["retired"].write_bytes(watch)
        paths["retired"].chmod(0o600)
        for name in ("transition", "deployment", "preimage"):
            body = {"schema": "hepta.test-" + name + ".v1",
                    "version": 1, "status": "PASS"}
            paths[name].write_bytes(control._canonical_json({
                **body,
                "body_sha256": control._sha256(
                    control._canonical_json(body)),
            }))
            paths[name].chmod(0o600)
        for name in ("domain_kill", "global_kill"):
            paths[name].write_bytes(b"engaged")
            paths[name].chmod(0o440)
        legacy_paper = b"legacy-paper-runtime-profile\n"
        for name in ("paper_legacy_backup", "paper_retained_legacy"):
            paths[name].write_bytes(legacy_paper)
            paths[name].chmod(0o600)
        handoff = artifact_root / "handoff-v2.json"
        state_root = artifact_root / "state"
        session_root = artifact_root / "sessions"
        authority_root = state_root / "session-authority"
        state_root.mkdir(mode=0o700)
        session_root.mkdir(mode=0o700)
        authority_root.mkdir(mode=0o700)
        patches = {
            "ROOT_UID": uid, "ROOT_GID": gid,
            "PAPER_CONTROL_GID": gid, "GLOBAL_PAPER_CONTROL_GID": gid,
            "EXTERNAL_P1_HANDOFF_PATH": handoff,
            "EXTERNAL_P1_PAPER_ENV_PATH": paper_profile,
            "EXTERNAL_P1_PAPER_ENV_CANDIDATE_PATH": paths["paper_candidate"],
            "EXTERNAL_P1_PAPER_ENV_BACKUP_PATH": paths["paper_legacy_backup"],
            "EXTERNAL_P1_PAPER_ENV_RETAINED_PATH":
                paths["paper_retained_legacy"],
            "EXTERNAL_P1_PAPER_PROFILE_SHA256": control._sha256(
                paper_profile.read_bytes()),
            "EXTERNAL_P1_PAPER_PROFILE_BYTES": len(
                paper_profile.read_bytes()),
            "EXTERNAL_P1_LEGACY_PAPER_PROFILE_SHA256":
                control._sha256(legacy_paper),
            "EXTERNAL_P1_LEGACY_PAPER_PROFILE_BYTES": len(legacy_paper),
            "PROFILE_TARGET_PATH": profile,
            "PROFILE_DORMANT_BACKUP_PATH": paths["backup"],
            "PROFILE_FORWARD_RETAINED_PATH": paths["retained"],
            "PROFILE_RETIRED_WATCH_PATH": paths["retired"],
            "PROFILE_FORWARD_TRANSITION_RECEIPT_PATH": paths["transition"],
            "PROFILE_DEPLOYMENT_RECEIPT_PATH": paths["deployment"],
            "PROFILE_FORWARD_PREIMAGE_PATH": paths["preimage"],
            "PROFILE_CANDIDATE_PATH": paths["candidate"],
            "DOMAIN_KILL_SWITCH_PATH": paths["domain_kill"],
            "GLOBAL_KILL_SWITCH_PATH": paths["global_kill"],
            "LOCAL_PAPER_STATE_ROOT": state_root,
            "SESSION_ROOT": session_root,
            "SESSION_AUTHORITY_ROOT": authority_root,
            "EXTERNAL_P1_RESIDUE_PATHS": (state_root / "wal.json",),
            "DORMANT_PAPER_PROFILE_SHA256": control._sha256(dormant),
            "DORMANT_PAPER_PROFILE_BYTES": len(dormant),
            "WATCH_PROFILE_SHA256": control._sha256(watch),
            "WATCH_PROFILE_BYTES": len(watch),
            "DISABLED_IDENTITY_MANIFEST_SHA256": control._sha256(
                identities.read_bytes()),
            "GUARDIAN_RUNTIME_ROOT": artifact_root / "guardian-runtime",
            "GUARDIAN_REQUEST_PATH":
                artifact_root / "guardian-runtime" / "guardian-request.json",
            "GUARDIAN_ACTIVE_PATH":
                artifact_root / "guardian-runtime" / "active.json",
            "BROKER_START_PERMIT_PATH":
                artifact_root / "guardian-runtime" /
                "broker-start-permit.json",
            "HOST_AUTHORITY_DIRECTORY": artifact_root / "host-authority",
            "BROKER_BOUNDARY_RECEIPT_PATH":
                artifact_root / "broker-boundary" /
                "current-boundary.v1.json",
        }
        for name, value in patches.items():
            stack.enter_context(mock.patch.object(control, name, value))
        stack.enter_context(mock.patch.object(
            control, "_guardian_identity_matches", return_value=True))
        restoration: dict[str, object] = {
            "schema": "hepta.p1-watch-to-paper-profile-restoration.v1",
            "version": 1, "status": "DORMANT_PAPER_PROFILE_RESTORED",
            "target": self._evidence(profile),
            "dormant_backup": self._evidence(paths["backup"]),
            "forward_retained_dormant": self._evidence(paths["retained"]),
            "retired_watch": self._evidence(paths["retired"]),
            "candidate_path": str(paths["candidate"]),
            "retired_watch_path": str(paths["retired"]),
            "exchange_method": "RENAME_EXCHANGE",
            "forward_only_after_exchange": True,
            "restore_intent_record_sha256": "sha256:" + "8" * 64,
            "restore_exchange_record_sha256": "sha256:" + "9" * 64,
        }
        for field, name in (
                ("forward_transition_receipt", "transition"),
                ("profile_deployment_receipt", "deployment"),
                ("forward_preimage_evidence", "preimage")):
            evidence = self._evidence(paths[name])
            evidence["body_sha256"] = json.loads(
                paths[name].read_text(encoding="ascii"))["body_sha256"]
            restoration[field] = evidence
        now_ms = 1_000_000
        hardening = {
            "schema":
                "hepta.p1-watch-to-paper-runtime-profile-hardening.v1",
            "version": 1, "status": "PAPER_RUNTIME_PROFILE_HARDENED",
            "target": self._evidence(paper_profile),
            "legacy_backup": self._evidence(paths["paper_legacy_backup"]),
            "retained_legacy": self._evidence(
                paths["paper_retained_legacy"]),
            "candidate_path": str(paths["paper_candidate"]),
            "retained_legacy_path": str(paths["paper_retained_legacy"]),
            "exchange_method": "RENAME_EXCHANGE",
            "forward_only_after_exchange": True,
            "harden_intent_record_sha256": "sha256:" + "c" * 64,
            "harden_exchange_record_sha256": "sha256:" + "d" * 64,
        }
        body = {
            "schema": control.EXTERNAL_P1_HANDOFF_SCHEMA, "version": 2,
            "status": control.EXTERNAL_P1_HANDOFF_STATUS,
            "issued_at_ms": now_ms - 1_000,
            "expires_at_ms": now_ms + 299_000,
            "round": 114, "domain": "alpha", "campaign_id": "campaign-a",
            "source_baseline_sha256": "sha256:" + "a" * 64,
            "producer": {"path": "/usr/libexec/test",
                         "file_sha256": "sha256:" + "1" * 64},
            "production_mode": "PRODUCTION_ROOT_SYSTEMD",
            "activation_receipt": {"path": "/var/lib/test/a.json",
                                   "file_sha256": "sha256:" + "2" * 64,
                                   "body_sha256": "sha256:" + "3" * 64},
            "p1_audit_receipt": {"path": "/var/lib/test/p1.json",
                                 "file_sha256": "sha256:" + "4" * 64,
                                 "body_sha256": "sha256:" + "5" * 64},
            "freeze_bundle": {"path": "/var/lib/test/f.json",
                              "file_sha256": "sha256:" + "6" * 64,
                              "body_sha256": "sha256:" + "7" * 64},
            "watch_units_inactive": True, "watch_authority_count": 0,
            "watch_socket_count": 0, "watch_timer_count": 0,
            "paper_units_inactive": True, "broker_deny_all": True,
            "kill_switch_engaged": True,
            "global_kill_switch_engaged": True, "identity_count": 0,
            "identity_manifest_sha256":
                control.DISABLED_IDENTITY_MANIFEST_SHA256,
            "paper_profile_restored": True,
            "paper_profile_restoration": restoration,
            "profile_candidate_absent": True,
            "paper_runtime_profile_hardened": True,
            "paper_runtime_profile_hardening": hardening,
            "paper_runtime_profile_candidate_absent": True,
            "crash_recovery_verified": True, "cleanup_residue_count": 0,
            "paper_authorized": False, "live_authorized": False,
            "mutation_authorized": False, "direct_broker_access": False,
            "order_submission_authorized": False,
        }
        document = {
            **body, "body_sha256": control._sha256(
                control._canonical_json(body))}
        handoff.write_bytes(control._canonical_json(document))
        handoff.chmod(0o600)

        def command(arguments: list[str]) -> SimpleNamespace:
            if "systemctl" in arguments[0]:
                return SimpleNamespace(
                    returncode=0,
                    stdout="LoadState=loaded\nActiveState=inactive\nJob=\n",
                    stderr="")
            return SimpleNamespace(
                returncode=0,
                stdout=("hepta_broker_egress_policy: PASS policy_sha256=" +
                        "b" * 64 + " authorized_connectors=0 "
                        "authorized_uids= protected_ports=4\n"),
                stderr="")
        return stack, handoff, document, command

    def recovery_authority_fixture(
            self, root: Path, handoff: Path,
            handoff_document: dict[str, object],
    ) -> tuple[Path, dict[str, object]]:
        recovery_root = root / "recovery-authority"
        common = {
            "version": 1, "recorded_at_ms": 1_400_000,
            "domain": "alpha", "campaign_id": "campaign-a",
            "suspension_id": "suspension-campaign-a",
            "source_baseline_sha256": "sha256:" + "a" * 64,
        }
        artifact_bodies = {
            "policy_preimage_reference": {
                **common,
                "schema": "hepta.local-paper-recovery-policy-preimage.v1",
                "status": "RECOVERY_POLICY_PREIMAGE_FROZEN",
                "policy_terminal": True, "policy_enabled": False,
                "policy_mutations_authorized": False,
                "paper_only": True, "live_authorized": False,
            },
            "incident_state_reference": {
                **common,
                "schema": "hepta.local-paper-recovery-incident-state.v1",
                "status": "RECOVERY_INCIDENT_STATE_FROZEN",
                "recovery_required": True, "trading_suspended": True,
            },
            "mutation_lineage_reference": {
                **common,
                "schema": "hepta.local-paper-recovery-mutation-lineage.v1",
                "status": "RECOVERY_MUTATION_LINEAGE_FROZEN",
                "place_call_ids": ["place-call-1"],
                "cancel_call_ids": [], "flatten_call_ids": [],
                "request_sha256s": ["sha256:" + "1" * 64],
                "response_sha256s": ["sha256:" + "2" * 64],
                "journal_sha256s": ["sha256:" + "3" * 64],
            },
            "session_owner_set_reference": {
                **common,
                "schema": "hepta.local-paper-recovery-session-owner-set.v1",
                "status": "RECOVERY_SESSION_OWNER_SET_FROZEN",
                "lease_store_schema": "HSL8", "owner_count": 1,
                "owners": [{
                    "account": "DU12345", "agent_id": "alpha",
                    "execution_domain": "PAPER:alpha",
                    "lease_generation": 7, "session_id": "session-1",
                    "token_sha256": "sha256:" + "4" * 64,
                }],
                "durable_owners": [{
                    "token_sha256": "sha256:" + "4" * 64,
                    "lease_generation": 7, "session_id": "session-1",
                    "owner_account": "DU12345",
                    "owner_execution_domain": "PAPER:alpha",
                    "paper_finalization_required": True,
                }],
            },
        }
        references: dict[str, object] = {}
        for index, (field, body) in enumerate(artifact_bodies.items(), 1):
            path = recovery_root / f"{index:02d}-{field}.json"
            document = self.write_sealed(path, body)
            metadata = path.stat()
            references[field] = {
                "path": str(path),
                "file_sha256": control._sha256(path.read_bytes()),
                "body_sha256": document["body_sha256"],
                "schema": body["schema"], "status": body["status"],
                "bytes": metadata.st_size, "mode": metadata.st_mode,
                "uid": metadata.st_uid, "gid": metadata.st_gid,
                "nlink": metadata.st_nlink,
            }
        handoff_body = dict(handoff_document)
        handoff_body.pop("body_sha256")
        handoff_body["issued_at_ms"] = 1_000_000
        handoff_body["expires_at_ms"] = 1_300_000
        expired_handoff = control._sealed_document(handoff_body)
        handoff.write_bytes(control._canonical_json(expired_handoff))
        authority_body = {
            "schema": control.RECOVERY_AUTHORITY_SCHEMA, "version": 1,
            "status": control.RECOVERY_AUTHORITY_STATUS,
            "recovery_id": "recovery-campaign-a",
            "recorded_at_ms": 1_500_000, "domain": "alpha",
            "campaign_id": "campaign-a",
            "suspension_id": "suspension-campaign-a",
            "reason_code": "ORDER_SETTLEMENT_UNCERTAIN",
            "source_baseline_sha256": "sha256:" + "a" * 64,
            "watch_handoff_receipt_path": str(handoff),
            "watch_handoff_receipt_file_sha256":
                control._sha256(handoff.read_bytes()),
            "watch_handoff_receipt_body_sha256":
                expired_handoff["body_sha256"],
            "recovery_required": True, "reduce_only": True,
            "paper_only": True, "live_authorized": False,
            "entry_authorized": False,
            "order_submission_authorized": False,
            "session_provision_authorized": False,
            **references, "session_owner_count": 1,
            "all_original_session_owners_bound": True,
        }
        path = recovery_root / "recovery-authority.json"
        document = self.write_sealed(path, authority_body)
        return path, document

    def recovery_completion_fixture(
            self, root: Path, recovery: dict[str, object],
            recovery_path: Path,
    ) -> tuple[Path, dict[str, object]]:
        token_sha256 = "sha256:" + "4" * 64
        canonical_owner_set = (
            f"{token_sha256}\t7\t{b'DU12345'.hex()}\t"
            f"{b'PAPER:alpha'.hex()}\n").encode("ascii")
        owner_set_sha256 = control._sha256(canonical_owner_set)
        finalization_id = "paper-finalization-" + hashlib.sha256((
                str(recovery["recovery_id"]) + "\n" + owner_set_sha256 +
                "\n1\n").encode("ascii")).hexdigest()[:32]
        preliminary_rich = {
            "execution_service_epoch": "hexec-v8-terminal",
            "execution_service_fencing_generation": 12,
            "broker_connection_epoch": 13,
            "broker_active_generation": 14,
            "broker_terminal_generation": 15,
            "broker_risk_generation": 16,
            "broker_account_generation": 17,
            "broker_position_generation": 18,
            "broker_fx_cash_generation": 19,
            "broker_exposure_generation": 21,
            "broker_terminal_exposure_generation": 20,
            "broker_risk_absorbed_exposure_generation": 21,
            "broker_global_active_order_count": 0,
            "owner_active_order_count": 0,
            "owner_uncertain_command_count": 0,
            "broker_position_quantity": "0",
            "broker_gross_absolute_position": "0",
        }
        preliminary_receipt_values = {
            "schema": "hepta.paper-session-finalization-receipt.v1",
            "version": "1", "status": "AUDIT_SEALED",
            "recovery_id": recovery["recovery_id"],
            "finalization_id": finalization_id,
            "expected_owner_set_sha256": owner_set_sha256,
            "expected_owner_count": "1",
            "owner_set_canonical_hex": canonical_owner_set.hex(),
            "owner_account": "DU12345",
            "owner_execution_domain": "PAPER:alpha",
            **{field: str(preliminary_rich[field]) for field in (
                "execution_service_epoch",
                "execution_service_fencing_generation",
                "broker_connection_epoch", "broker_active_generation",
                "broker_terminal_generation", "broker_risk_generation",
                "broker_account_generation", "broker_position_generation",
                "broker_fx_cash_generation", "broker_exposure_generation",
                "broker_terminal_exposure_generation",
                "broker_risk_absorbed_exposure_generation",
                "broker_global_active_order_count", "owner_active_order_count",
                "owner_uncertain_command_count")},
            "broker_post_fill_risk_reconciliation_pending": "0",
            "broker_recovery_audit_barrier_complete": "1",
            "broker_recovery_audit_new_connection_epoch_required": "0",
            "broker_position_quantity": "0",
            "broker_gross_absolute_position": "0",
            "paper_only": "1", "live_authorized": "0",
        }
        preliminary_receipt = "".join(
            f"{field}={preliminary_receipt_values[field]}\n"
            for field in control.PRELIMINARY_FINALIZATION_RECEIPT_KEYS)
        preliminary_sha256 = control._sha256(
            preliminary_receipt.encode("ascii"))
        preliminary = {
            "accepted": True,
            "reason_code": "PAPER_FINALIZATION_AUDIT_SEALED",
            "lease_generation": 7,
            "paper_finalization_state": "AUDIT_SEALED",
            "paper_finalization_required": True,
            "recovery_id": recovery["recovery_id"],
            "finalization_id": finalization_id,
            "expected_owner_set_sha256": owner_set_sha256,
            "expected_owner_count": 1,
            "owner_token_sha256": token_sha256,
            "finalization_receipt_sha256": preliminary_sha256,
            "finalization_receipt": preliminary_receipt,
            "owner_audit_authoritative": True,
            "owner_audit_complete": True,
            "owner_account": "DU12345",
            "owner_execution_domain": "PAPER:alpha",
            "broker_post_fill_risk_reconciliation_pending": False,
            "broker_recovery_audit_barrier_complete": True,
            "broker_recovery_audit_new_connection_epoch_required": False,
            **preliminary_rich,
        }
        terminal_rich: dict[str, object] = {
            "execution_service_epoch": "hexec-v8-terminal",
            "execution_service_fencing_generation": 12,
            "broker_connection_epoch": 23,
            "broker_active_generation": 24,
            "broker_terminal_generation": 25,
            "broker_risk_generation": 26,
            "broker_account_generation": 27,
            "broker_position_generation": 28,
            "broker_fx_cash_generation": 29,
            "broker_exposure_generation": 31,
            "broker_terminal_exposure_generation": 30,
            "broker_risk_absorbed_exposure_generation": 31,
            "broker_global_active_order_count": 0,
            "owner_active_order_count": 0,
            "owner_uncertain_command_count": 0,
            "broker_position_quantity": "0",
            "broker_gross_absolute_position": "0",
            "terminalization_service_epoch": "hexec-v8-terminal",
            "terminalization_service_fencing_generation": 12,
            "terminalization_generation": 1,
            "terminal_latch_sha256": "sha256:" + "9" * 64,
            "execution_mutation_gate_closed": True,
            "broker_transport_connected": False,
            "broker_event_ingress_halted": True,
            "broker_callback_queue_drained": True,
            "broker_callbacks_in_flight": 0,
            "broker_reconnect_permitted": False,
            "terminal_latch_durable": True,
            "terminal_runtime_latch_loaded": True,
            "terminal_runtime_verified": True,
            "terminal_replay": True,
        }
        terminal_receipt_values = {
            "schema": "hepta.paper-session-terminal-ack-receipt.v2",
            "version": "2", "status": "TERMINAL_ACKED",
            "recovery_id": recovery["recovery_id"],
            "finalization_id": finalization_id,
            "expected_owner_set_sha256": owner_set_sha256,
            "expected_owner_count": "1",
            "owner_set_canonical_hex": canonical_owner_set.hex(),
            "preliminary_finalization_receipt_sha256": preliminary_sha256,
            "owner_account": "DU12345",
            "owner_execution_domain": "PAPER:alpha",
            **{field: str(terminal_rich[field]) for field in (
                "execution_service_epoch",
                "execution_service_fencing_generation",
                "terminalization_generation", "terminal_latch_sha256",
                "broker_connection_epoch", "broker_active_generation",
                "broker_terminal_generation", "broker_risk_generation",
                "broker_account_generation", "broker_position_generation",
                "broker_fx_cash_generation", "broker_exposure_generation",
                "broker_terminal_exposure_generation",
                "broker_risk_absorbed_exposure_generation",
                "broker_global_active_order_count", "owner_active_order_count",
                "owner_uncertain_command_count", "broker_callbacks_in_flight",
                "broker_position_quantity", "broker_gross_absolute_position")},
            "execution_mutation_gate_closed": "1",
            "broker_transport_connected": "0",
            "broker_event_ingress_halted": "1",
            "broker_callback_queue_drained": "1",
            "broker_reconnect_permitted": "0",
            "terminal_latch_durable": "1",
            "broker_post_fill_risk_reconciliation_pending": "0",
            "broker_recovery_audit_barrier_complete": "1",
            "broker_recovery_audit_new_connection_epoch_required": "0",
            "paper_only": "1", "live_authorized": "0",
        }
        terminal_receipt = "".join(
            f"{field}={terminal_receipt_values[field]}\n"
            for field in control.TERMINAL_ACK_RECEIPT_KEYS)
        terminal_sha256 = control._sha256(terminal_receipt.encode("ascii"))
        terminal_ack = {
            "accepted": True,
            "reason_code": "PAPER_FINALIZATION_TERMINAL_ACKED",
            "lease_generation": 7,
            "paper_finalization_state": "ACKED",
            "paper_finalization_required": True,
            "recovery_id": recovery["recovery_id"],
            "finalization_id": finalization_id,
            "expected_owner_set_sha256": owner_set_sha256,
            "expected_owner_count": 1,
            "owner_token_sha256": token_sha256,
            "finalization_receipt_sha256": terminal_sha256,
            "finalization_receipt": terminal_receipt,
            "preliminary_finalization_receipt_sha256": preliminary_sha256,
            "owner_audit_authoritative": True,
            "owner_audit_complete": True,
            "owner_account": "DU12345",
            "owner_execution_domain": "PAPER:alpha",
            "broker_post_fill_risk_reconciliation_pending": False,
            "broker_recovery_audit_barrier_complete": True,
            "broker_recovery_audit_new_connection_epoch_required": False,
            **terminal_rich,
        }
        flat_body = {
            "schema": control.RECOVERY_TERMINAL_FLAT_SCHEMA, "version": 3,
            "status": control.RECOVERY_TERMINAL_FLAT_STATUS,
            "completed_at_ms": 1_800_000,
            "recovery_id": recovery["recovery_id"], "domain": "alpha",
            "campaign_id": recovery["campaign_id"],
            "suspension_id": recovery["suspension_id"],
            "source_baseline_sha256": recovery["source_baseline_sha256"],
            "finalization_id": finalization_id,
            "expected_owner_set_sha256": owner_set_sha256,
            "expected_owner_count": 1,
            "preliminary_finalization_receipt_sha256": preliminary_sha256,
            "preliminary_finalization_receipt": preliminary_receipt,
            "preliminary_finalization_result": preliminary,
            "terminal_ack_receipt_sha256": terminal_sha256,
            "terminal_ack_receipt": terminal_receipt,
            "terminal_ack_result": terminal_ack,
            "terminal_latch_sha256": terminal_rich["terminal_latch_sha256"],
            "session_owner_count": 1,
            "session_owner_token_sha256s": [token_sha256],
            "all_original_session_owners_closed": True,
            "terminal_acknowledged": True,
            "terminal_runtime_replay_verified": True,
            "hsl_owner_purged": True,
            "position_quantity": "0", "gross_absolute_position": "0",
            "active_order_count": 0, "paper_only": True,
            "live_authorized": False,
            "pre_finalization_diagnostic_zero_exposure_proofs": [],
        }
        flat_path = control.LOCAL_PAPER_STATE_ROOT / (
            "external-recovery-" + hashlib.sha256(str(
                recovery["suspension_id"]).encode("utf-8")).hexdigest()[:24] +
            ".terminal-flat.json")
        flat = self.write_sealed(flat_path, flat_body)
        metadata = flat_path.stat()
        reference = {
            "path": str(flat_path),
            "file_sha256": control._sha256(flat_path.read_bytes()),
            "body_sha256": flat["body_sha256"],
            "schema": control.RECOVERY_TERMINAL_FLAT_SCHEMA,
            "status": control.RECOVERY_TERMINAL_FLAT_STATUS,
            "bytes": metadata.st_size, "mode": metadata.st_mode,
            "uid": metadata.st_uid, "gid": metadata.st_gid,
            "nlink": metadata.st_nlink,
        }
        body = {
            "schema": control.RECOVERY_COMPLETION_SCHEMA, "version": 3,
            "status": control.RECOVERY_COMPLETION_STATUS,
            "recovery_id": recovery["recovery_id"],
            "completed_at_ms": 1_800_000, "domain": "alpha",
            "campaign_id": recovery["campaign_id"],
            "suspension_id": recovery["suspension_id"],
            "source_baseline_sha256": recovery["source_baseline_sha256"],
            "recovery_authority_file_sha256":
                control._sha256(recovery_path.read_bytes()),
            "recovery_authority_body_sha256": recovery["body_sha256"],
            "authoritative_flat_receipt_reference": reference,
            "finalization_id": finalization_id,
            "expected_owner_set_sha256": owner_set_sha256,
            "expected_owner_count": 1,
            "preliminary_finalization_receipt_sha256": preliminary_sha256,
            "terminal_ack_receipt_sha256": terminal_sha256,
            "terminal_latch_sha256": terminal_rich["terminal_latch_sha256"],
            "session_owner_count": 1,
            "all_original_session_owners_closed": True,
            "terminal_acknowledged": True,
            "terminal_runtime_replay_verified": True,
            "hsl_owner_purged": True,
            "position_quantity": "0", "gross_absolute_position": "0",
            "active_order_count": 0, "paper_only": True,
            "live_authorized": False,
        }
        path = root / "recovery-authority" / "completion.json"
        return path, self.write_sealed(path, body)

    @staticmethod
    def replace_receipt_field(receipt: str, field: str, value: str) -> str:
        prefix = field + "="
        rows = receipt.splitlines()
        matches = [index for index, row in enumerate(rows)
                   if row.startswith(prefix)]
        if len(matches) != 1:
            raise AssertionError("receipt fixture field is not unique")
        rows[matches[0]] = prefix + value
        return "\n".join(rows) + "\n"

    def rebind_terminal_result_field(
            self, flat: dict[str, Any], completion: dict[str, Any],
            field: str, value: Any, receipt_value: str,
    ) -> None:
        result = flat["terminal_ack_result"]
        assert isinstance(result, dict)
        result[field] = value
        receipt = self.replace_receipt_field(
            str(result["finalization_receipt"]), field, receipt_value)
        receipt_sha256 = control._sha256(receipt.encode("ascii"))
        result["finalization_receipt"] = receipt
        result["finalization_receipt_sha256"] = receipt_sha256
        flat["terminal_ack_receipt"] = receipt
        flat["terminal_ack_receipt_sha256"] = receipt_sha256
        completion["terminal_ack_receipt_sha256"] = receipt_sha256

    def assert_recovery_completion_mutation_rejected(
            self, mutate: Callable[[dict[str, Any], dict[str, Any]], None],
            pattern: str = "recovery (?:completion|terminal-flat|terminal ACK)",
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _authority, identities, env_root, _drop_in = self.fixture(root)
            stack, handoff, handoff_document, _command = self.external_fixture(
                root, identities, env_root)
            with stack:
                recovery_path, recovery = self.recovery_authority_fixture(
                    root, handoff, handoff_document)
                completion_path, completion = self.recovery_completion_fixture(
                    root, recovery, recovery_path)
                completion_body = dict(completion)
                completion_body.pop("body_sha256")
                reference = dict(
                    completion_body["authoritative_flat_receipt_reference"])
                flat_path = Path(str(reference["path"]))
                flat_body = json.loads(flat_path.read_text(encoding="ascii"))
                flat_body.pop("body_sha256")
                mutate(flat_body, completion_body)
                flat = self.write_sealed(flat_path, flat_body)
                metadata = flat_path.stat()
                reference.update({
                    "file_sha256": control._sha256(flat_path.read_bytes()),
                    "body_sha256": flat["body_sha256"],
                    "schema": flat["schema"], "status": flat["status"],
                    "bytes": metadata.st_size, "mode": metadata.st_mode,
                    "uid": metadata.st_uid, "gid": metadata.st_gid,
                    "nlink": metadata.st_nlink,
                })
                completion_body["authoritative_flat_receipt_reference"] = (
                    reference)
                completion = self.write_sealed(
                    completion_path, completion_body)
                with mock.patch.object(
                        control, "RECOVERY_COMPLETION_PATH", completion_path), \
                        self.assertRaisesRegex(
                            control.LocalPaperError, pattern):
                    control.validate_recovery_completion(
                        completion_path=completion_path,
                        expected_file_sha256=control._sha256(
                            completion_path.read_bytes()),
                        expected_body_sha256=str(completion["body_sha256"]),
                        recovery=recovery,
                        recovery_file_sha256=control._sha256(
                            recovery_path.read_bytes()),
                        recovery_body_sha256=str(recovery["body_sha256"]),
                        now_ms=2_000_000)

    def test_external_v2_enable_reopens_profiles_and_never_rewrites_them(
            self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            authority, identities, env_root, drop_in = self.fixture(root)
            stack, handoff, document, command = self.external_fixture(
                root, identities, env_root)
            with stack:
                gateway = env_root / "alpha.env"
                paper = env_root / "alpha.ib-paper.env"
                before_gateway = (gateway.read_bytes(), gateway.stat().st_ino)
                before_paper = (paper.read_bytes(), paper.stat().st_ino)
                checks: list[dict[str, object]] = []

                def verifier(**kwargs: object) -> dict[str, object]:
                    result = control.verify_external_p1(
                        **kwargs, command=command, now_ms=1_000_000)
                    checks.append(result)
                    return result

                calls: list[list[str]] = []
                result = control._enable_transaction(
                    domain="alpha", authority_path=authority,
                    identities_path=identities, env_root=env_root,
                    drop_in_path=drop_in, gateway_env_root=env_root,
                    external_p1_finalized=True, handoff_path=handoff,
                    handoff_file_sha256=control._sha256(handoff.read_bytes()),
                    handoff_body_sha256=str(document["body_sha256"]),
                    campaign_id="campaign-a",
                    source_baseline_sha256="sha256:" + "a" * 64,
                    systemctl=self.activation_systemctl(calls),
                    external_verifier=verifier,
                    guardian_identity=control._process_identity(os.getpid()),
                    guardian_request_id="a" * 32)
                self.assertEqual(len(checks), 2)
                self.assertEqual(result["admission_mode"],
                                 "external-p1-finalized")
                self.assertEqual(result["quote_max_age_ms"], 5000)
                self.assertEqual(
                    result["paper_runtime_profile_sha256"],
                    control._sha256(before_paper[0]))
                self.assertEqual(
                    (gateway.read_bytes(), gateway.stat().st_ino),
                    before_gateway)
                self.assertEqual(
                    (paper.read_bytes(), paper.stat().st_ino), before_paper)
                active_units = {
                    control.BROKER_UNIT,
                    "hepta-execution-ib-paper@alpha.service",
                    "hepta-tool-gateway@alpha.socket",
                    "hepta-tool-session-supervisor@alpha.socket",
                    "hepta-tool-gateway@alpha.service",
                    "hepta-ib-paper-campaign-operator@alpha.socket",
                }
                self.assertEqual(control.status(
                    identities,
                    drop_in_path=drop_in,
                    command=self.effective_status_command(
                        authorized=True, active_units=active_units))["mode"],
                                 "LOCAL_PAPER")

    def test_external_v1_candidate_and_profile_tamper_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _authority, identities, env_root, _drop_in = self.fixture(root)
            stack, handoff, document, command = self.external_fixture(
                root, identities, env_root)
            with stack:
                arguments = {
                    "handoff_path": handoff,
                    "expected_file_sha256": control._sha256(
                        handoff.read_bytes()),
                    "expected_body_sha256": document["body_sha256"],
                    "campaign_id": "campaign-a",
                    "source_baseline_sha256": "sha256:" + "a" * 64,
                    "identities_path": identities,
                    "gateway_env_path": env_root / "alpha.env",
                    "paper_env_path": env_root / "alpha.ib-paper.env",
                    "command": command,
                    "now_ms": 1_000_000,
                }
                self.assertEqual(
                    control.verify_external_p1(**arguments)["mode"],
                    "DENY_ALL")

                v1 = dict(document)
                v1["schema"] = (
                    "hepta.p1-watch-to-paper-handoff-receipt.v1")
                v1["version"] = 1
                v1_body = dict(v1)
                v1_body.pop("body_sha256")
                v1["body_sha256"] = control._sha256(
                    control._canonical_json(v1_body))
                v1_raw = control._canonical_json(v1)
                with self.assertRaisesRegex(
                        control.LocalPaperError, "handoff invalid"):
                    control._validate_external_handoff_document(
                        v1_raw,
                        expected_file_sha256=control._sha256(v1_raw),
                        expected_body_sha256=str(v1["body_sha256"]),
                        campaign_id="campaign-a",
                        source_baseline_sha256="sha256:" + "a" * 64,
                        now_ms=1_000_000)

                candidate = control.PROFILE_CANDIDATE_PATH
                candidate.write_bytes(b"residue")
                with self.assertRaisesRegex(
                        control.LocalPaperError, "candidate residue"):
                    control.verify_external_p1(**arguments)
                candidate.unlink()

                paper_candidate = control.EXTERNAL_P1_PAPER_ENV_CANDIDATE_PATH
                paper_candidate.write_bytes(b"residue")
                with self.assertRaisesRegex(
                        control.LocalPaperError,
                        "PAPER profile candidate residue"):
                    control.verify_external_p1(**arguments)
                paper_candidate.unlink()

                paper = env_root / "alpha.ib-paper.env"
                paper.write_text(
                    paper.read_text(encoding="ascii").replace(
                        "HEPTA_IB_PAPER_QUOTE_MAX_AGE_MS=5000",
                        "HEPTA_IB_PAPER_QUOTE_MAX_AGE_MS=30000"),
                    encoding="ascii")
                with self.assertRaisesRegex(
                        control.LocalPaperError,
                        "(?:restoration artifact|PAPER profile)"):
                    control.verify_external_p1(**arguments)
                self.assertEqual(control.status(identities)["mode"],
                                 "DENY_ALL")

    def test_external_boundary_drift_before_mutation_keeps_deny_all(
            self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            authority, identities, env_root, drop_in = self.fixture(root)
            stack, handoff, document, _command = self.external_fixture(
                root, identities, env_root)
            with stack:
                calls = 0

                def verifier(**_kwargs: object) -> dict[str, object]:
                    nonlocal calls
                    calls += 1
                    return {"boundary_fingerprint": f"sha256:{calls:064x}"}

                with self.assertRaisesRegex(
                        control.LocalPaperError,
                        "boundary drifted before mutation"):
                    control._enable_transaction(
                        domain="alpha", authority_path=authority,
                        identities_path=identities, env_root=env_root,
                        drop_in_path=drop_in, gateway_env_root=env_root,
                        external_p1_finalized=True, handoff_path=handoff,
                        handoff_file_sha256=control._sha256(
                            handoff.read_bytes()),
                        handoff_body_sha256=str(document["body_sha256"]),
                        campaign_id="campaign-a",
                        source_baseline_sha256="sha256:" + "a" * 64,
                        systemctl=lambda _arguments: None,
                        external_verifier=verifier)
                self.assertEqual(calls, 2)
                self.assertEqual(control.status(identities)["mode"],
                                 "DENY_ALL")

    def test_enable_crash_replay_is_forward_safe_and_pin_drift_is_rejected(
            self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            authority, identities, env_root, drop_in = self.fixture(root)
            transaction_root = root / "control-state"
            original_persist = control._persist_control_phase

            def crash_after_identity(
                    transaction: dict[str, object], path: Path, phase: str,
            ) -> dict[str, object]:
                updated = original_persist(transaction, path, phase)
                if "WRITE_BROKER_DROP_IN" in phase:
                    raise SystemExit("simulated crash")
                return updated

            arguments = {
                "domain": "alpha", "authority_path": authority,
                "identities_path": identities, "env_root": env_root,
                "drop_in_path": drop_in, "gateway_env_root": env_root,
                "systemctl": lambda _arguments: None,
                "transaction_root": transaction_root,
                "host_authority_root": root / "host-authority",
            }
            with mock.patch.object(control, "ROOT_UID", os.geteuid()), \
                    mock.patch.object(control, "ROOT_GID", os.getegid()), \
                    mock.patch.object(
                        control, "_persist_control_phase",
                        side_effect=crash_after_identity), \
                    self.assertRaisesRegex(SystemExit, "simulated crash"):
                control._enable_transaction(**arguments)
            wal = transaction_root / "local-paper-control-transaction.json"
            self.assertTrue(wal.exists())
            self.assertTrue(json.loads(
                identities.read_text(encoding="ascii"))["paper_authorized"])
            drifted = dict(arguments)
            drifted["drop_in_path"] = root / "other-drop-in.conf"
            with mock.patch.object(control, "ROOT_UID", os.geteuid()), \
                    mock.patch.object(control, "ROOT_GID", os.getegid()), \
                    self.assertRaisesRegex(
                        control.LocalPaperError, "WAL argument drift"):
                control._enable_transaction(**drifted)
            with mock.patch.object(control, "ROOT_UID", os.geteuid()), \
                    mock.patch.object(control, "ROOT_GID", os.getegid()):
                result = control._enable_transaction(**arguments)
            self.assertTrue(result["enable_replay_forward_safe"])
            self.assertFalse(wal.exists())
            self.assertFalse(json.loads(
                identities.read_text(encoding="ascii"))["paper_authorized"])
            self.assertFalse(drop_in.exists())

    def test_disable_stops_all_consumers_and_broker_before_identity_change(
            self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            authority, identities, env_root, drop_in = self.fixture(root)
            transaction_root = root / "control-state"
            with self.guardian_fixture(root) as (guardian, request_id):
                control._enable_transaction(
                    domain="alpha", authority_path=authority,
                    identities_path=identities, env_root=env_root,
                    drop_in_path=drop_in, gateway_env_root=env_root,
                    systemctl=self.activation_systemctl(),
                    transaction_root=transaction_root,
                    guardian_identity=guardian,
                    guardian_request_id=request_id)
            observations: list[tuple[list[str], bool]] = []

            def systemctl(arguments: list[str]) -> None:
                observations.append((list(arguments), bool(json.loads(
                    identities.read_text(encoding="ascii"))[
                        "paper_authorized"])))

            with mock.patch.object(control, "ROOT_UID", os.geteuid()), \
                    mock.patch.object(control, "ROOT_GID", os.getegid()):
                control._disable_transaction(
                    identities_path=identities, drop_in_path=drop_in,
                    systemctl=systemctl, transaction_root=transaction_root)
            self.assertEqual(observations[0][0], [
                "stop", *control.CONTROL_STOP_UNITS])
            self.assertEqual(
                observations[1][0], ["stop", control.BROKER_UNIT])
            self.assertTrue(observations[0][1])
            self.assertTrue(observations[1][1])
            self.assertFalse(observations[2][1])

    def test_broker_preflight_invalid_or_recovery_wal_forces_deny_all(
            self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _authority, identities, _env_root, drop_in = self.fixture(root)
            transaction_root = root / "control-state"
            transaction_root.mkdir(mode=0o700)
            wal = transaction_root / "local-paper-control-transaction.json"
            wal.write_bytes(b"not-json\n")
            wal.chmod(0o600)
            drop_in.parent.mkdir(parents=True, exist_ok=True)
            drop_in.write_text("[Service]\n", encoding="ascii")
            authorized = json.loads(identities.read_text(encoding="ascii"))
            authorized["paper_authorized"] = True
            authorized["identities"] = [{"uid": 2121}]
            identities.write_bytes(control._pretty_json(authorized))
            with mock.patch.object(control, "ROOT_UID", os.geteuid()), \
                    mock.patch.object(control, "ROOT_GID", os.getegid()):
                result = control.reconcile_before_broker(
                    identities_path=identities, drop_in_path=drop_in,
                    transaction_root=transaction_root)
            self.assertEqual(result["wal_operation"], "INVALID")
            self.assertTrue(result["wal_retained"])
            self.assertTrue(wal.exists())
            self.assertFalse(drop_in.exists())
            self.assertEqual(
                json.loads(identities.read_text(encoding="ascii"))[
                    "identities"], [])

    def test_broker_start_requires_one_use_permit_or_live_guardian_receipt(
            self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _authority, identities, _env_root, drop_in = self.fixture(root)
            transaction_root = root / "control-state"
            authorized = {
                **control._deny_all_document("sha256:" + "8" * 64),
                "identities": [{"uid": 2121}], "paper_authorized": True,
            }
            identities.write_bytes(control._pretty_json(authorized))
            drop_in.parent.mkdir(parents=True, exist_ok=True)
            drop_in.write_bytes(control._broker_override_payload())
            drop_in.chmod(0o644)
            with self.guardian_fixture(root) as (guardian, request_id):
                transaction = control._new_control_transaction(
                    path=control._transaction_path(transaction_root),
                    operation="ENABLE",
                    request={"guardian_request_id": request_id},
                    target_identity_manifest_sha256=control._sha256(
                        identities.read_bytes()),
                    target_drop_in_sha256=control._sha256(
                        drop_in.read_bytes()), now_ms=1_000_000)
                transaction = control._persist_control_phase(
                    transaction, control._transaction_path(transaction_root),
                    "BEFORE_001_START_BROKER_LOCAL_PAPER")
                permit = control._issue_broker_start_permit(
                    transaction, guardian_identity=guardian,
                    phase=transaction["phase"])
                with control._host_authority_lease(
                        control.HOST_AUTHORITY_DIRECTORY) as lease:
                    control._publish_broker_activation_reservation(
                        lease, transaction=transaction,
                        guardian_identity=guardian, permit=permit)
                first = control.reconcile_before_broker(
                    identities_path=identities, drop_in_path=drop_in,
                    transaction_root=transaction_root)
                self.assertEqual(first["mode"], "GUARDIAN_AUTHORIZED_START")
                self.assertTrue(control.BROKER_START_PERMIT_PATH.exists())
                self.activation_systemctl()(
                    ["start", control.BROKER_UNIT])
                self.assertFalse(control.BROKER_START_PERMIT_PATH.exists())

                transaction = control._persist_control_phase(
                    transaction, control._transaction_path(transaction_root),
                    control.CONTROL_NORMAL_TERMINAL_PHASE)
                control._write_guardian_active_receipt(
                    transaction, guardian_identity=guardian,
                    phase=control.CONTROL_NORMAL_TERMINAL_PHASE,
                    mode="LOCAL_PAPER")
                restarted = control.reconcile_before_broker(
                    identities_path=identities, drop_in_path=drop_in,
                    transaction_root=transaction_root)
                self.assertEqual(
                    restarted["mode"], "GUARDIAN_AUTHORIZED_START")
                with mock.patch.object(
                        control, "_guardian_identity_matches",
                        return_value=False):
                    rebooted = control.reconcile_before_broker(
                        identities_path=identities, drop_in_path=drop_in,
                        transaction_root=transaction_root)
                self.assertEqual(rebooted["mode"], "DENY_ALL")
                self.assertFalse(drop_in.exists())
                self.assertFalse(json.loads(
                    identities.read_text(encoding="ascii"))[
                        "paper_authorized"])

    def test_guardian_submit_rejects_systemd_noop_and_stale_request(
            self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _authority, identities, _env_root, drop_in = self.fixture(root)
            state = root / "control-state"
            calls: list[list[str]] = []
            with self.guardian_fixture(root), \
                    mock.patch.object(control, "LOCAL_PAPER_STATE_ROOT", state), \
                    mock.patch.object(control, "DEFAULT_IDENTITIES", identities), \
                    mock.patch.object(control, "DEFAULT_DROP_IN", drop_in):
                with self.assertRaisesRegex(
                        control.LocalPaperError,
                        "activation did not commit exactly"):
                    control._submit_guardian_request(
                        operation="ENABLE", arguments={"test": True},
                        systemctl=calls.append)
                self.assertEqual(calls[0], ["start", control.GUARDIAN_UNIT])
                self.assertEqual(calls[-1], ["stop", control.GUARDIAN_UNIT])
                with self.assertRaisesRegex(
                        control.LocalPaperError,
                        "zero transaction residue"):
                    control._submit_guardian_request(
                        operation="ENABLE", arguments={"test": True},
                        systemctl=calls.append)

    def test_guardian_submit_returns_verified_identity_manifest_hash(
            self) -> None:
        identity_manifest_sha256 = "sha256:" + "a" * 64
        request_id = "b" * 32
        transaction = {
            "operation": "ENABLE",
            "phase": control.CONTROL_NORMAL_TERMINAL_PHASE,
            "request": {"guardian_request_id": request_id},
            "target_identity_manifest_sha256": identity_manifest_sha256,
        }
        active = {
            "guardian_request_id": request_id, "operation": "ENABLE",
            "phase": control.CONTROL_NORMAL_TERMINAL_PHASE,
            "mode": "LOCAL_PAPER",
            "target_identity_manifest_sha256": identity_manifest_sha256,
        }
        effective = {
            "mode": "LOCAL_PAPER", "effective_state_verified": True,
            "identity_manifest_sha256": identity_manifest_sha256,
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            transaction_path = root / "transaction.json"
            request_path = root / "guardian-request.json"
            active_path = root / "active.json"
            permit_path = root / "broker-start-permit.json"
            calls: list[list[str]] = []
            with mock.patch.multiple(
                    control,
                    _require_production_mutation_parents=mock.Mock(),
                    _transaction_path=mock.Mock(
                        return_value=transaction_path),
                    _load_control_transaction=mock.Mock(
                        side_effect=[None, transaction]),
                    GUARDIAN_REQUEST_PATH=request_path,
                    GUARDIAN_ACTIVE_PATH=active_path,
                    BROKER_START_PERMIT_PATH=permit_path,
                    _write_guardian_request=mock.Mock(return_value={
                        "request_id": request_id}),
                    _runtime_document=mock.Mock(return_value=active),
                    status=mock.Mock(return_value=effective),
                    _runtime_binding_matches=mock.Mock(return_value=True)):
                result = control._submit_guardian_request(
                    operation="ENABLE",
                    arguments={"external_p1_finalized": False},
                    systemctl=calls.append)
        self.assertEqual(result["identity_manifest_sha256"],
                         identity_manifest_sha256)
        self.assertEqual(calls, [["start", control.GUARDIAN_UNIT]])

    def test_guardian_clean_stop_defers_input_revoke_to_exec_stop_post(
            self) -> None:
        clean = {"mode": "DENY_ALL_PENDING_EXEC_STOP_POST",
                 "guardian_stopped": True}
        systemctl = mock.Mock()
        with mock.patch.object(
                control, "_guardian_run_body", return_value=clean), \
                mock.patch.object(control, "guardian_fail_close") as ordered, \
                mock.patch.object(
                    control, "_guardian_direct_input_fail_close") as direct:
            self.assertEqual(control.guardian_run(systemctl=systemctl), clean)
        ordered.assert_not_called()
        direct.assert_not_called()

        abnormal = {"mode": "DENY_ALL", "guardian_stopped": True}
        with mock.patch.object(
                control, "_guardian_run_body", return_value=abnormal), \
                mock.patch.object(
                    control, "_guardian_ordered_fail_close_or_direct") as ordered, \
                mock.patch.object(
                    control, "_guardian_direct_input_fail_close") as direct:
            self.assertEqual(
                control.guardian_run(systemctl=systemctl), abnormal)
        ordered.assert_called_once_with(systemctl=systemctl)
        direct.assert_not_called()

    def test_guardian_failure_uses_ordered_fail_close_and_preserves_exception(
            self) -> None:
        systemctl = mock.Mock()
        original = control.LocalPaperError("injected sd_notify failure")
        events: list[str] = []

        def ordered(**_kwargs: Any) -> None:
            events.append("ordered")

        def direct() -> None:
            events.append("direct")

        with mock.patch.object(
                control, "_guardian_run_body", side_effect=original), \
                mock.patch.object(control, "_sd_notify"), \
                mock.patch.object(
                    control, "guardian_fail_close", side_effect=ordered), \
                mock.patch.object(
                    control, "_guardian_direct_input_fail_close",
                    side_effect=direct):
            with self.assertRaisesRegex(
                    control.LocalPaperError, "sd_notify failure"):
                control.guardian_run(systemctl=systemctl)
        self.assertEqual(events, ["ordered"])

    def test_guardian_failure_falls_back_to_direct_revoke_only_after_ordered(
            self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _authority, identities, _env_root, drop_in = self.fixture(root)
            authorized = json.loads(identities.read_text(encoding="ascii"))
            authorized["paper_authorized"] = True
            authorized["identities"] = [{"uid": 2121}]
            identities.write_bytes(control._pretty_json(authorized))
            drop_in.parent.mkdir(parents=True, exist_ok=True)
            drop_in.write_bytes(control._broker_override_payload())
            with self.guardian_fixture(root), \
                    mock.patch.object(control, "DEFAULT_IDENTITIES", identities), \
                    mock.patch.object(control, "DEFAULT_DROP_IN", drop_in), \
                    mock.patch.object(
                        control, "guardian_fail_close",
                        side_effect=control.LocalPaperError(
                            "injected ordered fail-close failure")), \
                    mock.patch.object(control, "_sd_notify"), \
                    mock.patch.object(
                        control, "_guardian_run_body",
                        side_effect=control.LocalPaperError(
                            "injected sd_notify failure")), \
                    self.assertRaisesRegex(
                        control.LocalPaperError, "sd_notify failure"):
                control.guardian_run()
            self.assertFalse(drop_in.exists())
            self.assertFalse(json.loads(
                identities.read_text(encoding="ascii"))["paper_authorized"])

    def test_guardian_fail_close_heartbeats_while_stop_blocks(self) -> None:
        started = threading.Event()
        release = threading.Event()
        calls: list[list[str]] = []
        notifications: list[str] = []

        def slow_systemctl(arguments: list[str]) -> None:
            calls.append(list(arguments))
            started.set()
            release.wait(1.0)

        def ordered(**kwargs: Any) -> None:
            cleanup = kwargs["systemctl"]
            cleanup(["stop", "consumers"])
            cleanup(["stop", "broker"])
            calls.append(["identity"])

        def notify(message: str) -> None:
            notifications.append(message)
            if sum("WATCHDOG=1" in item for item in notifications) >= 3:
                release.set()

        with mock.patch.object(
                control, "guardian_fail_close", side_effect=ordered), \
                mock.patch.object(
                    control, "_guardian_watchdog_interval", return_value=0.01), \
                mock.patch.object(control, "_sd_notify", side_effect=notify), \
                mock.patch.object(
                    control, "GUARDIAN_FAIL_CLOSE_MAX_SECONDS", 1.0), \
                mock.patch.object(
                    control, "_guardian_direct_input_fail_close") as direct:
            control._guardian_ordered_fail_close_or_direct(
                systemctl=slow_systemctl)

        self.assertTrue(started.is_set())
        self.assertGreaterEqual(
            sum("WATCHDOG=1" in item for item in notifications), 3)
        self.assertEqual(calls, [
            ["stop", "consumers"], ["stop", "broker"], ["identity"]])
        direct.assert_not_called()

    def test_guardian_fail_close_timeout_uses_direct_fallback(self) -> None:
        started = threading.Event()
        release = threading.Event()

        def blocked_systemctl(_arguments: list[str]) -> None:
            started.set()
            release.wait(1.0)

        def ordered(**kwargs: Any) -> None:
            kwargs["systemctl"](["stop", "blocked"])

        with mock.patch.object(
                control, "guardian_fail_close", side_effect=ordered), \
                mock.patch.object(
                    control, "_guardian_watchdog_interval", return_value=0.01), \
                mock.patch.object(control, "_sd_notify"), \
                mock.patch.object(
                    control, "GUARDIAN_FAIL_CLOSE_MAX_SECONDS", 0.05), \
                mock.patch.object(
                    control, "_guardian_direct_input_fail_close") as direct:
            began = time.monotonic()
            control._guardian_ordered_fail_close_or_direct(
                systemctl=blocked_systemctl)
            elapsed = time.monotonic() - began

        self.assertTrue(started.is_set())
        self.assertLess(elapsed, 0.5)
        direct.assert_called_once_with()
        release.set()

    def test_systemctl_timeout_terminates_its_process_group(self) -> None:
        process = mock.Mock()
        process.pid = 8123
        process.poll.return_value = None
        process.communicate.side_effect = control.subprocess.TimeoutExpired(
            ["systemctl", "stop", "blocked"], 0.05)
        with mock.patch.object(
                control.subprocess, "Popen", return_value=process), \
                mock.patch.object(control.os, "killpg") as killpg, \
                self.assertRaisesRegex(
                    control.LocalPaperError, "systemctl stop blocked timed out"):
            control._run_systemctl(["stop", "blocked"], timeout=0.05)
        killpg.assert_called_once_with(process.pid, control.signal.SIGTERM)
        process.wait.assert_called_once_with(timeout=3)

    def test_guardian_failure_preserves_original_when_both_closes_fail(
            self) -> None:
        systemctl = mock.Mock()
        original = control.LocalPaperError("original guardian failure")
        with mock.patch.object(
                control, "_guardian_run_body", side_effect=original), \
                mock.patch.object(
                    control, "guardian_fail_close",
                    side_effect=control.LocalPaperError("ordered failed")), \
                mock.patch.object(
                    control, "_guardian_direct_input_fail_close",
                    side_effect=RuntimeError("direct failed")):
            with self.assertRaisesRegex(
                    control.LocalPaperError, "original guardian failure"):
                control.guardian_run(systemctl=systemctl)

    def test_recovery_authority_historical_handoff_enable_and_completion(
            self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            authority, identities, env_root, drop_in = self.fixture(root)
            stack, handoff, handoff_document, command = self.external_fixture(
                root, identities, env_root)
            with stack:
                recovery_path, recovery = self.recovery_authority_fixture(
                    root, handoff, handoff_document)
                completion_path, completion = self.recovery_completion_fixture(
                    root, recovery, recovery_path)
                transaction_root = control.LOCAL_PAPER_STATE_ROOT
                calls: list[list[str]] = []
                with mock.patch.object(
                        control, "RECOVERY_AUTHORITY_PATH", recovery_path), \
                        mock.patch.object(
                            control, "RECOVERY_COMPLETION_PATH",
                            completion_path):
                    with self.assertRaisesRegex(
                            control.LocalPaperError, "handoff expired"):
                        control.verify_external_p1(
                            handoff_path=handoff,
                            expected_file_sha256=control._sha256(
                                handoff.read_bytes()),
                            expected_body_sha256=str(
                                json.loads(handoff.read_text())[
                                    "body_sha256"]),
                            campaign_id="campaign-a",
                            source_baseline_sha256="sha256:" + "a" * 64,
                            identities_path=identities,
                            gateway_env_path=env_root / "alpha.env",
                            paper_env_path=env_root / "alpha.ib-paper.env",
                            command=command, now_ms=2_000_000)
                    result = control._enable_recovery_transaction(
                        authority_path=authority,
                        identities_path=identities, env_root=env_root,
                        gateway_env_root=env_root,
                        drop_in_path=drop_in,
                        recovery_path=recovery_path,
                        recovery_file_sha256=control._sha256(
                            recovery_path.read_bytes()),
                        recovery_body_sha256=str(recovery["body_sha256"]),
                        systemctl=self.activation_systemctl(calls),
                        command=command,
                        transaction_root=transaction_root,
                        now_ms=2_000_000,
                        guardian_identity=control._process_identity(
                            os.getpid()), guardian_request_id="a" * 32)
                    self.assertEqual(result["mode"], "RECOVERY_PAPER")
                    self.assertTrue(result["transaction_retained"])
                    self.assertFalse(result["entry_authorized"])
                    self.assertFalse(result["session_provision_authorized"])
                    self.assertNotIn(
                        ["start",
                         "hepta-ib-paper-campaign-operator@alpha.socket"],
                        calls)
                    self.assertNotIn(
                        ["start", "hepta-local-ai-paper-agent.service"], calls)
                    wal = transaction_root / (
                        "local-paper-control-transaction.json")
                    self.assertEqual(
                        json.loads(wal.read_text(encoding="ascii"))["phase"],
                        "RECOVERY_READY")
                    active = {control.BROKER_UNIT,
                              *control.RECOVERY_START_UNITS}
                    self.assertEqual(control.status(
                        identities, transaction_root=transaction_root,
                        drop_in_path=drop_in,
                        command=self.effective_status_command(
                            authorized=True, active_units=active))["mode"],
                        "RECOVERY_PAPER")
                    with self.assertRaisesRegex(
                            control.LocalPaperError,
                            "terminal-flat completion evidence"):
                        control._complete_control_transaction(
                            control._load_control_transaction(wal), wal)

                    def stopping_systemctl(arguments: list[str]) -> None:
                        calls.append(list(arguments))
                        if arguments != ["stop", *control.CONTROL_STOP_UNITS]:
                            return
                        with control._host_authority_lease(
                                control.HOST_AUTHORITY_DIRECTORY) as lease:
                            owner = control._host_authority_owner_payload(lease)
                            if owner is not None:
                                control._remove_exact_host_authority_owner(
                                    lease, owner, absent_ok=False)

                    reconciled = control.reconcile_control_transaction(
                        identities_path=identities,
                        drop_in_path=drop_in, systemctl=stopping_systemctl,
                        transaction_root=transaction_root)
                    self.assertTrue(reconciled["recovery_retained"])
                    self.assertTrue(wal.exists())
                    completed = control.complete_recovery(
                        identities_path=identities, drop_in_path=drop_in,
                        recovery_path=recovery_path,
                        recovery_file_sha256=control._sha256(
                            recovery_path.read_bytes()),
                        recovery_body_sha256=str(recovery["body_sha256"]),
                        completion_path=completion_path,
                        completion_file_sha256=control._sha256(
                            completion_path.read_bytes()),
                        completion_body_sha256=str(
                            completion["body_sha256"]),
                        systemctl=calls.append,
                        transaction_root=transaction_root,
                        now_ms=2_000_000)
                self.assertTrue(completed["recovery_completed"])
                self.assertFalse(wal.exists())
                self.assertFalse(json.loads(
                    identities.read_text(encoding="ascii"))[
                        "paper_authorized"])

    def test_recovery_completion_rejects_legacy_v1_v2_completion_and_flat(
            self) -> None:
        for version in (1, 2):
            with self.subTest(artifact="completion", version=version):
                self.assert_recovery_completion_mutation_rejected(
                    lambda _flat, completion, item=version:
                        completion.update({
                            "schema": (
                                "hepta.local-paper-control-"
                                f"recovery-completion.v{item}"),
                            "version": item,
                        }),
                    "recovery completion invalid")
            with self.subTest(artifact="terminal-flat", version=version):
                self.assert_recovery_completion_mutation_rejected(
                    lambda flat, _completion, item=version: flat.update({
                        "schema": (
                            "hepta.local-ai-paper-external-"
                            f"recovery-terminal-flat.v{item}"),
                        "version": item,
                    }),
                    "recovery terminal-flat reference invalid")

    def test_recovery_completion_never_treats_preliminary_as_terminal_flat(
            self) -> None:
        def mutate(flat: dict[str, Any], completion: dict[str, Any]) -> None:
            flat["terminal_ack_result"] = dict(
                flat["preliminary_finalization_result"])
            flat["terminal_ack_receipt"] = flat[
                "preliminary_finalization_receipt"]
            flat["terminal_ack_receipt_sha256"] = flat[
                "preliminary_finalization_receipt_sha256"]
            completion["terminal_ack_receipt_sha256"] = completion[
                "preliminary_finalization_receipt_sha256"]

        self.assert_recovery_completion_mutation_rejected(
            mutate, "recovery completion invalid")

    def test_recovery_completion_requires_replayed_inert_terminal_latch(
            self) -> None:
        cases = (
            ("terminal_replay", False, None),
            ("terminal_runtime_latch_loaded", False, None),
            ("terminal_runtime_verified", False, None),
            ("execution_mutation_gate_closed", False, "0"),
            ("broker_transport_connected", True, "1"),
            ("broker_event_ingress_halted", False, "0"),
            ("broker_callback_queue_drained", False, "0"),
            ("broker_callbacks_in_flight", 1, "1"),
            ("broker_reconnect_permitted", True, "1"),
            ("terminal_latch_durable", False, "0"),
        )
        for field, value, receipt_value in cases:
            def mutate(
                    flat: dict[str, Any], completion: dict[str, Any],
                    item: str = field, replacement: Any = value,
                    encoded: str | None = receipt_value,
            ) -> None:
                if encoded is None:
                    result = flat["terminal_ack_result"]
                    assert isinstance(result, dict)
                    result[item] = replacement
                else:
                    self.rebind_terminal_result_field(
                        flat, completion, item, replacement, encoded)

            with self.subTest(field=field):
                self.assert_recovery_completion_mutation_rejected(
                    mutate, "recovery terminal ACK result invalid")

    def test_recovery_completion_requires_owner_group_closed_and_purged(
            self) -> None:
        fields = (
            "all_original_session_owners_closed", "terminal_acknowledged",
            "terminal_runtime_replay_verified", "hsl_owner_purged",
        )
        for artifact in ("completion", "terminal-flat"):
            for field in fields:
                def mutate(
                        flat: dict[str, Any], completion: dict[str, Any],
                        target: str = artifact, item: str = field,
                ) -> None:
                    (completion if target == "completion" else flat)[item] = (
                        False)

                with self.subTest(artifact=artifact, field=field):
                    self.assert_recovery_completion_mutation_rejected(
                        mutate,
                        "recovery (?:completion|terminal-flat) invalid")

    def test_recovery_completion_rejects_latch_hash_and_epoch_drift(
            self) -> None:
        def hash_drift(
                _flat: dict[str, Any], completion: dict[str, Any],
        ) -> None:
            completion["terminal_ack_receipt_sha256"] = "sha256:" + "a" * 64

        def latch_drift(
                flat: dict[str, Any], _completion: dict[str, Any],
        ) -> None:
            flat["terminal_latch_sha256"] = "sha256:" + "b" * 64

        def zero_latch(
                flat: dict[str, Any], completion: dict[str, Any],
        ) -> None:
            zero = "sha256:" + "0" * 64
            self.rebind_terminal_result_field(
                flat, completion, "terminal_latch_sha256", zero, zero)
            flat["terminal_latch_sha256"] = zero
            completion["terminal_latch_sha256"] = zero

        def epoch_drift(
                flat: dict[str, Any], completion: dict[str, Any],
        ) -> None:
            epoch = "hexec-v8-terminal-drift"
            self.rebind_terminal_result_field(
                flat, completion, "execution_service_epoch", epoch, epoch)
            result = flat["terminal_ack_result"]
            assert isinstance(result, dict)
            result["terminalization_service_epoch"] = epoch

        for name, mutation, pattern in (
                ("receipt-hash", hash_drift, "recovery terminal-flat invalid"),
                ("latch-binding", latch_drift,
                 "recovery terminal-flat invalid"),
                ("zero-latch", zero_latch, "recovery completion invalid"),
                ("service-epoch", epoch_drift,
                 "recovery terminal ACK result invalid")):
            with self.subTest(name=name):
                self.assert_recovery_completion_mutation_rejected(
                    mutation, pattern)

    def test_recovery_completion_rejects_late_fill_and_exposure_residue(
            self) -> None:
        cases = (
            ("broker_post_fill_risk_reconciliation_pending", True, "1"),
            ("broker_position_quantity", "1", "1"),
            ("broker_gross_absolute_position", "1", "1"),
            ("broker_global_active_order_count", 1, "1"),
            ("owner_active_order_count", 1, "1"),
            ("owner_uncertain_command_count", 1, "1"),
            ("broker_risk_absorbed_exposure_generation", 30, "30"),
        )
        for field, value, receipt_value in cases:
            with self.subTest(field=field):
                self.assert_recovery_completion_mutation_rejected(
                    lambda flat, completion, item=field, replacement=value,
                    encoded=receipt_value:
                        self.rebind_terminal_result_field(
                            flat, completion, item, replacement, encoded),
                    "recovery terminal ACK result invalid")

    def test_recovery_completion_rejects_terminal_field_shape_drift(
            self) -> None:
        def result_injection(
                flat: dict[str, Any], _completion: dict[str, Any],
        ) -> None:
            result = flat["terminal_ack_result"]
            assert isinstance(result, dict)
            result["injected"] = True

        def result_removal(
                flat: dict[str, Any], _completion: dict[str, Any],
        ) -> None:
            result = flat["terminal_ack_result"]
            assert isinstance(result, dict)
            result.pop("terminal_runtime_verified")

        def receipt_injection(
                flat: dict[str, Any], completion: dict[str, Any],
        ) -> None:
            result = flat["terminal_ack_result"]
            assert isinstance(result, dict)
            receipt = str(result["finalization_receipt"]) + "injected=1\n"
            sha256 = control._sha256(receipt.encode("ascii"))
            result["finalization_receipt"] = receipt
            result["finalization_receipt_sha256"] = sha256
            flat["terminal_ack_receipt"] = receipt
            flat["terminal_ack_receipt_sha256"] = sha256
            completion["terminal_ack_receipt_sha256"] = sha256

        def receipt_removal(
                flat: dict[str, Any], completion: dict[str, Any],
        ) -> None:
            result = flat["terminal_ack_result"]
            assert isinstance(result, dict)
            rows = str(result["finalization_receipt"]).splitlines()
            receipt = "\n".join(rows[:-1]) + "\n"
            sha256 = control._sha256(receipt.encode("ascii"))
            result["finalization_receipt"] = receipt
            result["finalization_receipt_sha256"] = sha256
            flat["terminal_ack_receipt"] = receipt
            flat["terminal_ack_receipt_sha256"] = sha256
            completion["terminal_ack_receipt_sha256"] = sha256

        for name, mutation in (
                ("result-injection", result_injection),
                ("result-removal", result_removal),
                ("receipt-injection", receipt_injection),
                ("receipt-removal", receipt_removal)):
            with self.subTest(name=name):
                self.assert_recovery_completion_mutation_rejected(
                    mutation, "recovery terminal ACK result invalid")

    def test_recovery_authority_rejects_zero_owner_and_reference_drift(
            self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _authority, identities, env_root, _drop_in = self.fixture(root)
            stack, handoff, handoff_document, _command = self.external_fixture(
                root, identities, env_root)
            with stack:
                recovery_path, recovery = self.recovery_authority_fixture(
                    root, handoff, handoff_document)
                with mock.patch.object(
                        control, "RECOVERY_AUTHORITY_PATH", recovery_path):
                    body = dict(recovery)
                    body.pop("body_sha256")
                    body["session_owner_count"] = 0
                    invalid = control._sealed_document(body)
                    recovery_path.write_bytes(control._canonical_json(invalid))
                    with self.assertRaisesRegex(
                            control.LocalPaperError, "authority invalid"):
                        control.validate_recovery_authority(
                            recovery_path=recovery_path,
                            expected_file_sha256=control._sha256(
                                recovery_path.read_bytes()),
                            expected_body_sha256=str(
                                invalid["body_sha256"]), now_ms=2_000_000)

    def test_recovery_authority_rejects_hsl7_owner_projection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _authority, identities, env_root, _drop_in = self.fixture(root)
            stack, handoff, handoff_document, _command = self.external_fixture(
                root, identities, env_root)
            with stack:
                recovery_path, recovery = self.recovery_authority_fixture(
                    root, handoff, handoff_document)
                reference = dict(recovery["session_owner_set_reference"])
                owner_path = Path(str(reference["path"]))
                owner_body = json.loads(owner_path.read_text(encoding="ascii"))
                owner_body.pop("body_sha256")
                owner_body["lease_store_schema"] = "HSL7"
                owner = self.write_sealed(owner_path, owner_body)
                metadata = owner_path.stat()
                reference.update({
                    "file_sha256": control._sha256(owner_path.read_bytes()),
                    "body_sha256": owner["body_sha256"],
                    "bytes": metadata.st_size, "mode": metadata.st_mode,
                    "uid": metadata.st_uid, "gid": metadata.st_gid,
                    "nlink": metadata.st_nlink,
                })
                recovery_body = dict(recovery)
                recovery_body.pop("body_sha256")
                recovery_body["session_owner_set_reference"] = reference
                recovery = self.write_sealed(recovery_path, recovery_body)
                with mock.patch.object(
                        control, "RECOVERY_AUTHORITY_PATH", recovery_path), \
                        self.assertRaisesRegex(
                            control.LocalPaperError,
                            "recovery session owner set invalid"):
                    control.validate_recovery_authority(
                        recovery_path=recovery_path,
                        expected_file_sha256=control._sha256(
                            recovery_path.read_bytes()),
                        expected_body_sha256=str(recovery["body_sha256"]),
                        now_ms=2_000_000)

    def test_canonical_strategy_matches_paper_runtime_quantity(self) -> None:
        strategy_path = (
            Path(__file__).resolve().parents[1] /
            "configs/hepta-local-ai-paper-strategy-v3.json")
        strategy = json.loads(strategy_path.read_text(encoding="utf-8"))
        self.assertEqual(
            strategy["schema"], "hepta.local-ai-paper-strategy.v3")
        self.assertEqual(strategy["version"], 3)
        self.assertEqual(strategy["max_order_quantity"], agent.ORDER_QUANTITY)
        self.assertEqual(strategy["order_type"], "MKT")
        self.assertEqual(strategy["max_holding_seconds"], 0)
        self.assertEqual(strategy["exit_mode"], "MODEL_REVERSAL")
        self.assertTrue(strategy["rate_limit_fail_closed"])
        self.assertTrue(strategy["emergency_reduce_only_recovery"])
        self.assertTrue(strategy["auth_rearm_required_after_rate_limit"])
        self.assertTrue(strategy["campaign_end_flat_required"])
        self.assertTrue(strategy["paper_only"])
        self.assertFalse(strategy["live_authorized"])

    def fixture(self, root: Path) -> tuple[Path, Path, Path, Path]:
        identities = root / "identities.json"
        env_root = root / "trust-domains"
        env_root.mkdir()
        drop_in = root / "systemd" / "20-local-paper.conf"
        source = "sha256:" + "8" * 64
        deny = control._deny_all_document(source)
        identities.write_bytes(control._pretty_json(deny))
        identities.chmod(0o600)
        authorized = {
            "identities": [{
                "domain_id": "alpha", "gid": 2121,
                "identity": "hepta-ib-exec-alpha",
                "role": "ib-paper-execution-authority", "uid": 2121,
            }],
            "live_authorized": False,
            "paper_authorized": True,
            "schema": control.IDENTITY_SCHEMA,
            "source_policy_sha256": source,
            "version": 1,
        }
        authority = root / "authority.json"
        authority.write_text(json.dumps({
            "authorizations": [{
                "domain_id": "alpha", "gid": 2121,
                "identity": "hepta-ib-exec-alpha", "uid": 2121,
            }],
            "live_authorized": False,
            "network_identity_manifest_sha256": control._sha256(
                control._pretty_json(authorized)),
            "paper_authorized": True,
            "schema": control.AUTHORITY_SCHEMA,
            "version": 1,
        }), encoding="utf-8")
        (env_root / "alpha.ib-paper.env").write_text(
            "HEPTA_IB_EXECUTION_MODE=PAPER\n"
            "HEPTA_IB_PAPER_ACCOUNT=DU12345\n"
            "HEPTA_IB_PAPER_HOST=127.0.0.1\n"
            "HEPTA_IB_PAPER_PORT=4002\n"
            "HEPTA_IB_PAPER_MAX_ORDER_QTY=25000\n"
            "HEPTA_IB_PAPER_MAX_ORDERS_PER_MINUTE=1\n"
            "HEPTA_IB_PAPER_MAX_ACTIVE_ORDERS=1\n",
            encoding="utf-8")
        (env_root / "alpha.env").write_text(
            "HEPTA_EXECUTION_IO_TIMEOUT_MS=2500\n"
            "HEPTA_EXECUTION_MAX_RESPONSE_BYTES=32768\n"
            "HEPTA_TOOL_CONTRACT_BINDINGS=EUR.USD|EUR|CASH|IDEALPRO|USD\n"
            "HEPTA_TOOL_AGENT_UID=2104\n"
            "HEPTA_TOOL_SUPERVISOR_UID=0\n"
            "HEPTA_TOOL_SUPERVISOR_MAX_TTL_SEC=86400\n"
            "HEPTA_TOOL_SERVER_WORKERS=4\n"
            "HEPTA_TOOL_SERVER_MAX_PENDING=32\n"
            "HEPTA_TOOL_SERVER_MAX_CONCURRENT_PER_OWNER=1\n"
            "HEPTA_TOOL_SERVER_MAX_PENDING_PER_OWNER=8\n"
            "HEPTA_TOOL_SERVER_INGRESS_WORKERS=2\n",
            encoding="utf-8")
        return authority, identities, env_root, drop_in

    def test_enable_and_disable_local_paper(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            authority, identities, env_root, drop_in = self.fixture(root)
            calls: list[list[str]] = []
            transaction_root = root / "control-state"
            guardian_stack = contextlib.ExitStack()
            self.addCleanup(guardian_stack.close)
            guardian, request_id = guardian_stack.enter_context(
                self.guardian_fixture(root))
            enabled = control._enable_transaction(
                domain="alpha", authority_path=authority,
                identities_path=identities, env_root=env_root,
                drop_in_path=drop_in, gateway_env_root=env_root,
                systemctl=self.activation_systemctl(calls),
                transaction_root=transaction_root,
                guardian_identity=guardian,
                guardian_request_id=request_id)
            self.assertTrue(enabled["paper_authorized"])
            self.assertFalse(enabled["live_authorized"])
            self.assertIn("--supervise --paper-identities", drop_in.read_text())
            self.assertIn(
                ["start", "hepta-execution-ib-paper@alpha.service"], calls)
            gateway = (env_root / "alpha.env").read_text()
            self.assertIn("HEPTA_TOOL_ALLOW_TRADE=1\n", gateway)
            self.assertIn("HEPTA_TOOL_SESSION_TEMPLATES=watch,paper\n", gateway)
            self.assertIn("HEPTA_TOOL_MAX_ORDER_QTY=25000\n", gateway)
            paper = (env_root / "alpha.ib-paper.env").read_text()
            self.assertIn("HEPTA_IB_PAPER_QUOTE_MAX_AGE_MS=30000\n", paper)
            self.assertEqual(enabled["quote_max_age_ms"], 30000)
            active_units = {
                control.BROKER_UNIT,
                "hepta-execution-ib-paper@alpha.service",
                "hepta-tool-gateway@alpha.socket",
                "hepta-tool-session-supervisor@alpha.socket",
                "hepta-tool-gateway@alpha.service",
                "hepta-ib-paper-campaign-operator@alpha.socket",
            }
            with mock.patch.object(control, "ROOT_UID", os.geteuid()), \
                    mock.patch.object(control, "ROOT_GID", os.getegid()):
                self.assertEqual(control.status(
                    identities, transaction_root=transaction_root,
                    drop_in_path=drop_in,
                    command=self.effective_status_command(
                        authorized=True, active_units=active_units))["mode"],
                    "LOCAL_PAPER")

            calls.clear()
            with mock.patch.object(control, "ROOT_UID", os.geteuid()), \
                    mock.patch.object(control, "ROOT_GID", os.getegid()):
                disabled = control._disable_transaction(
                    identities_path=identities, drop_in_path=drop_in,
                    systemctl=calls.append,
                    transaction_root=transaction_root)
            self.assertFalse(disabled["paper_authorized"])
            self.assertFalse(drop_in.exists())
            with mock.patch.object(control, "ROOT_UID", os.geteuid()), \
                    mock.patch.object(control, "ROOT_GID", os.getegid()):
                self.assertEqual(control.status(
                    identities, transaction_root=transaction_root,
                    command=self.effective_status_command(
                        authorized=False))["mode"], "DENY_ALL")

    def test_enable_rejects_live_or_non_du_environment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            authority, identities, env_root, drop_in = self.fixture(root)
            (env_root / "alpha.ib-paper.env").write_text(
                "HEPTA_IB_EXECUTION_MODE=LIVE\n"
                "HEPTA_IB_PAPER_ACCOUNT=U12345\n"
                "HEPTA_IB_PAPER_HOST=127.0.0.1\n"
                "HEPTA_IB_PAPER_PORT=4001\n"
                "HEPTA_IB_PAPER_MAX_ACTIVE_ORDERS=1\n",
                encoding="utf-8")
            with self.assertRaisesRegex(
                    control.LocalPaperError, "safety boundary"):
                with mock.patch.object(control, "ROOT_UID", os.geteuid()), \
                        mock.patch.object(control, "ROOT_GID", os.getegid()):
                    control._enable_transaction(
                        domain="alpha", authority_path=authority,
                        identities_path=identities, env_root=env_root,
                        drop_in_path=drop_in, gateway_env_root=env_root,
                        systemctl=lambda _arguments: None,
                        transaction_root=root / "control-state")


class LocalAiPaperAgentTests(unittest.TestCase):
    @staticmethod
    def owner_orders_payload(
            active: list[int], owned: list[int] | None = None,
    ) -> dict[str, object]:
        return {
            "source": "IB", "authoritative": True,
            "active_orders_source": "IB_OPEN_ORDERS",
            "active_orders_connection_epoch": 7,
            "active_orders_generation": 11,
            "global_active_orders_complete": True,
            "owner_projection_source":
                "EXECUTION_COORDINATOR_ORDER_OWNERS",
            "owner_projection_connection_epoch": 7,
            "owner_projection_generation": 11,
            "owner_projection_complete": True,
            "owned_active_order_ids_authoritative": True,
            "owner_scope": {
                "agent_id": "paper-agent", "session_id": "paper-session",
                "execution_domain": "alpha", "account": "DU12345",
            },
            "reason_code": "", "active_order_ids": list(active),
            "owned_active_order_ids": list(
                active if owned is None else owned),
            "unmapped_active_order_ids": [], "recent_orders": [],
        }

    def test_order_projection_preserves_global_and_exact_owner_sets(self) -> None:
        payload = self.owner_orders_payload([41, 42], [41])
        projection = agent.validate_order_projection(payload)
        self.assertEqual(projection["global_active_order_ids"], (41, 42))
        self.assertEqual(projection["owned_active_order_ids"], (41,))

    def test_order_projection_rejects_unmapped_or_boundary_mismatch(self) -> None:
        unmapped = self.owner_orders_payload([41], [])
        unmapped.update({
            "authoritative": False, "owner_projection_complete": False,
            "owned_active_order_ids_authoritative": False,
            "unmapped_active_order_ids": [41],
            "reason_code": "EXECUTION_ORDER_OWNER_PROJECTION_INCOMPLETE",
        })
        with self.assertRaisesRegex(RuntimeError, "not authoritative"):
            agent.validate_order_projection(unmapped)
        mismatch = self.owner_orders_payload([])
        mismatch["owner_projection_generation"] = 12
        with self.assertRaisesRegex(RuntimeError, "contract invalid"):
            agent.validate_order_projection(mismatch)

    def setUp(self) -> None:
        patcher = mock.patch.object(agent, "_verify_effective_auth_profile")
        self.verify_auth_order = patcher.start()
        self.addCleanup(patcher.stop)
        runtime_patcher = mock.patch.object(agent, "require_runtime_binding")
        self.require_runtime_binding = runtime_patcher.start()
        self.addCleanup(runtime_patcher.stop)
        mutation_patcher = mock.patch.object(
            agent, "_broker_mutation_lock",
            side_effect=lambda: contextlib.nullcontext())
        self.mutation_lock = mutation_patcher.start()
        self.addCleanup(mutation_patcher.stop)
        token_patcher = mock.patch.object(
            agent, "_tool_session_token_sha256",
            return_value="sha256:" + "8" * 64)
        token_patcher.start()
        self.addCleanup(token_patcher.stop)

    def test_agent_json_publish_fsyncs_file_and_parent_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            with mock.patch.object(
                    agent.os, "fsync", wraps=os.fsync) as sync:
                agent.write_json(path, {"phase": "intent-durable"})
            value = json.loads(path.read_text(encoding="ascii"))
            temporary_residue = list(path.parent.glob(".*.tmp"))
        self.assertEqual(value, {"phase": "intent-durable"})
        self.assertGreaterEqual(sync.call_count, 2)
        self.assertEqual(temporary_residue, [])

    def test_agent_json_failed_write_preserves_previous_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            agent.write_json(path, {"phase": "old"})
            real_write = os.write
            attempts = 0

            def fail_after_partial(descriptor: int, payload: bytes) -> int:
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    return real_write(descriptor, payload[:1])
                raise OSError("simulated torn state write")

            with mock.patch.object(
                    agent.os, "write", side_effect=fail_after_partial), \
                    self.assertRaisesRegex(OSError, "simulated torn"):
                agent.write_json(path, {"phase": "new"})
            value = json.loads(path.read_text(encoding="ascii"))
            temporary_residue = list(path.parent.glob(".*.tmp"))
        self.assertEqual(value, {"phase": "old"})
        self.assertEqual(temporary_residue, [])

    def test_pending_mutation_persists_exact_owner_before_dispatch(self) -> None:
        arguments = SimpleNamespace(
            token_file="/run/hepta-agent-alpha/sessions/local-paper.token",
            state_file=None)
        state = agent.empty_state()

        def persisted(_arguments: object, value: dict[str, object]) -> None:
            self.assertEqual(value["pending_mutation_kind"], "PLACE_ORDER")
            self.assertEqual(
                value["pending_mutation_command_id"], "command-owner-001")
            self.assertEqual(
                value["pending_mutation_token_name"], "local-paper.token")
            self.assertEqual(
                value["pending_mutation_token_sha256"],
                "sha256:" + "8" * 64)

        with mock.patch.object(
                agent, "_persist_state", side_effect=persisted) as persist:
            agent._record_pending_mutation(
                arguments, state, "PLACE_ORDER", "command-owner-001")
        persist.assert_called_once_with(arguments, state)

    def test_broker_mutation_lock_wraps_entry_and_flatten_settlement(self) -> None:
        events: list[str] = []

        class ObservedLock:
            def __enter__(self) -> object:
                events.append("lock")
                return self

            def __exit__(self, *_unused: object) -> None:
                events.append("unlock")

        with mock.patch.object(
                agent, "_broker_mutation_lock",
                side_effect=lambda: ObservedLock()), \
                mock.patch.object(
                    agent, "_enter_locked",
                    side_effect=lambda *_args: events.append("enter") or 1.0), \
                mock.patch.object(
                    agent, "_flatten_locked",
                    side_effect=lambda *_args: events.append("flatten") or 0.0):
            self.assertEqual(agent.enter(object(), {}, {}, {}), 1.0)
            self.assertEqual(agent.flatten(object(), {}), 0.0)
        self.assertEqual(events, [
            "lock", "enter", "unlock", "lock", "flatten", "unlock",
        ])

    def test_broker_mutation_lock_rejects_non_root_metadata(self) -> None:
        metadata = SimpleNamespace(
            st_mode=0o100600, st_nlink=1, st_uid=2104, st_gid=2104)
        with mock.patch.object(agent.os, "open", return_value=41), \
                mock.patch.object(agent.os, "fstat", return_value=metadata), \
                mock.patch.object(agent.os, "close") as close, \
                mock.patch.object(agent.fcntl, "flock") as flock, \
                self.assertRaisesRegex(
                    RuntimeError, "BROKER_MUTATION_LOCK_UNSAFE"):
            with agent._BrokerMutationLock():
                pass
        close.assert_called_once_with(41)
        flock.assert_not_called()

    @staticmethod
    def manual_start_runtime_binding() -> dict[str, object]:
        return {
            "campaign_id": "test-campaign",
            "execution_service_epoch": "hexec-v6-" + "1" * 32,
            "execution_service_fencing_generation": 7,
            "tool_gateway_epoch": "htgw-v1-" + "2" * 32,
            "tool_session_token_sha256": "sha256:" + "3" * 64,
        }

    @staticmethod
    def consumed_manual_start_permit(
            runtime_binding: dict[str, object], *,
            invocation_id: str = "a" * 32,
            issued_at_ms: int = 123_000,
            not_after_ms: int = 124_000) -> dict[str, object]:
        return {
            "schema": "hepta.local-ai-paper-start-permit.v1",
            "permit_id": "4" * 64,
            "unit": "hepta-local-ai-paper-agent.service",
            "boot_id": "5" * 32,
            "issued_at_ms": issued_at_ms,
            "not_after_ms": not_after_ms,
            "campaign_id": "test-campaign",
            "policy_sha256": "sha256:" + "6" * 64,
            "agent_env_sha256": "sha256:" + "7" * 64,
            "state_sha256": "sha256:" + "8" * 64,
            "deadline_timer_sha256": "sha256:" + "9" * 64,
            "strategy_acceptance_sha256": "sha256:" + "c" * 64,
            "auth_rearm_receipt_sha256": "sha256:" + "a" * 64,
            "prelaunch_zero_receipt_sha256": "sha256:" + "b" * 64,
            "runtime_binding": runtime_binding,
            "policy_expires_at_ms": 200_000,
            "manual_start_required": True,
            "paper_only": True,
            "live_authorized": False,
            "phase": "CONSUMED",
            "invocation_id": invocation_id,
            "consumed_at_ms": issued_at_ms,
        }

    @staticmethod
    def write_manual_start_permit(
            path: Path, permit: dict[str, object]) -> SimpleNamespace:
        raw = agent.canonical(permit)
        path.write_bytes(raw)
        return SimpleNamespace(
            st_mode=0o100600, st_nlink=1, st_uid=0, st_gid=0,
            st_size=len(raw))

    def assert_invalid_manual_start_safely_stops(
            self, *, permit: dict[str, object] | None,
            invocation_id: str, now: int = 123_456) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path = root / "state.json"
            permit_path = root / "start-permit.consumed.json"
            runtime_binding = self.manual_start_runtime_binding()
            state = agent.empty_state()
            state.update({
                "auth_generation_rearmed": "auth-generation-new",
                "auth_profile_sha256_rearmed": TEST_AUTH_PROFILE_SHA256,
                "auth_profile_allowlist_sha256_rearmed":
                    TEST_AUTH_PROFILE_ALLOWLIST_SHA256,
                "runtime_binding": runtime_binding,
                "manual_start_required": True,
            })
            state_path.write_bytes(agent.canonical(state))
            if permit is None:
                lstat = mock.patch.object(
                    agent.os, "lstat", side_effect=FileNotFoundError(
                        2, "No such file or directory", str(permit_path)))
            else:
                metadata = self.write_manual_start_permit(permit_path, permit)
                lstat = mock.patch.object(
                    agent.os, "lstat", return_value=metadata)
            argv = [
                "hepta-local-ai-paper-agent",
                "--campaign-id", "test-campaign",
                "--strategy-id", "test-strategy",
                "--strategy-sha256", "sha256:" + "1" * 64,
                "--auth-profile-id", TEST_AUTH_PROFILE_ID,
                "--auth-profile-allowlist-sha256",
                TEST_AUTH_PROFILE_ALLOWLIST_SHA256,
                "--auth-generation", "auth-generation-new",
                "--state-file", str(state_path),
            ]
            with mock.patch.object(sys, "argv", argv), \
                    mock.patch.object(
                        agent, "START_PERMIT_CONSUMED", permit_path), \
                    lstat, \
                    mock.patch.dict(
                        agent.os.environ, {"INVOCATION_ID": invocation_id}), \
                    mock.patch.object(agent, "now_ms", return_value=now), \
                    mock.patch.object(agent, "quote") as quote:
                result = agent.main()
            persisted = json.loads(state_path.read_text(encoding="ascii"))
        self.assertEqual(result, agent.SAFETY_STOP_EXIT_STATUS)
        self.assertTrue(persisted["manual_start_required"])
        self.assertIsNone(persisted.get("manual_start_permit_id"))
        self.assertIsNone(persisted.get("manual_start_invocation_id"))
        self.assertTrue(persisted["trading_suspended"])
        self.assertTrue(persisted["recovery_required"])
        self.assertEqual(
            persisted["suspension_code"], agent.ORDER_STATE_UNCERTAIN)
        quote.assert_not_called()

    def test_model_decisions_use_gateway_and_isolated_short_sessions(self) -> None:
        arguments = SimpleNamespace(
            model_user="qian-qi",
            openclaw_agent="telegram-bot-8681289317",
            model="codex/gpt-5.3-codex-spark",
            auth_profile_allowlist_sha256=TEST_AUTH_PROFILE_ALLOWLIST_SHA256,
            model_session_key=(
                "agent:telegram-bot-8681289317:hepta-local-paper-decision"),
        )
        envelope = {
            "payloads": [{"text": json.dumps({
                "action": "HOLD", "confidence": 0.9,
                "rationale": "no coherent edge",
            })}],
        }
        completed = SimpleNamespace(
            returncode=0, stdout=json.dumps(envelope), stderr="")
        history = [
            {"observed_at_ms": index, "bid": 1.1, "ask": 1.2,
             "mid": 1.15}
            for index in range(6)
        ]
        with mock.patch.object(
                agent.uuid, "uuid4",
                return_value=SimpleNamespace(hex="a" * 32)), \
                mock.patch.object(
                    agent, "_run_model_command", return_value=completed) as run:
            decision = agent.model_decision(
                arguments, history, 0.0,
                {"realized_pnl_estimate": 0.0,
                 "unrealized_pnl_estimate": 0.0})
        command = run.call_args.args[0]
        self.assertNotIn("--local", command)
        self.assertEqual(command[command.index("--model") + 1], arguments.model)
        pinned_message = command[command.index("--message") + 1]
        self.assertTrue(pinned_message.startswith("{"))
        self.assertNotIn("/model ", pinned_message)
        self.assertNotIn(TEST_AUTH_PROFILE_ID, command[command.index("--model") + 1])
        self.verify_auth_order.assert_called_once_with(arguments)
        key_index = command.index("--session-key") + 1
        self.assertEqual(
            command[key_index],
            arguments.model_session_key + "-" + "a" * 32)
        self.assertEqual(decision["action"], "HOLD")

    def test_structured_tool_rate_limit_is_preserved(self) -> None:
        completed = SimpleNamespace(
            returncode=6,
            stdout=json.dumps({
                "status": "rejected", "tool": "trade.cancel_order",
                "reason_code": "AGENT_TRADE_RATE_LIMIT",
                "detail": "Agent session exhausted its trade-call budget",
                "order_id": -1, "payload": None,
            }),
            stderr="",
        )
        with mock.patch.object(
                agent.subprocess, "run", return_value=completed), \
                self.assertRaises(agent.ToolRejectedError) as raised:
            agent.run_json(["heptactl"])
        self.assertEqual(
            raised.exception.reason_code, "AGENT_TRADE_RATE_LIMIT")
        self.assertEqual(
            agent._suspension_code(raised.exception),
            agent.TRADE_TOOL_BUDGET_EXHAUSTED)

    def test_trade_rate_classifier_follows_wrapped_cause(self) -> None:
        rejected = agent.ToolRejectedError({
            "status": "rejected", "tool": "trade.cancel_order",
            "reason_code": "AGENT_TRADE_RATE_LIMIT", "detail": "budget",
        })
        try:
            try:
                raise rejected
            except agent.ToolRejectedError as error:
                raise agent.RecoveryRequiredError(
                    "RECOVERY_REQUIRED: settlement failed") from error
        except agent.RecoveryRequiredError as wrapped:
            self.assertEqual(
                agent._suspension_code(wrapped),
                agent.TRADE_TOOL_BUDGET_EXHAUSTED)

    def test_model_request_rate_limit_backs_off_without_safety_stop(self) -> None:
        arguments = SimpleNamespace(
            model_user="qian-qi",
            openclaw_agent="telegram-bot-8681289317",
            model="codex/gpt-5.3-codex-spark",
            auth_profile_allowlist_sha256=TEST_AUTH_PROFILE_ALLOWLIST_SHA256,
            model_session_key="paper-decision",
        )
        completed = SimpleNamespace(
            returncode=1,
            stdout=json.dumps({
                "status": "error",
                "error": {"raw_reason": "request_rate_limit"},
            }),
            stderr="",
        )
        history = [
            {"observed_at_ms": index, "bid": 1.1, "ask": 1.2,
             "mid": 1.15}
            for index in range(6)
        ]
        with mock.patch.object(
                agent, "_run_model_command", return_value=completed), \
                self.assertRaises(agent.ModelRequestRateLimitError):
            agent.model_decision(
                arguments, history, 0.0,
                {"realized_pnl_estimate": 0.0,
                 "unrealized_pnl_estimate": 0.0})

    def test_profile_scoped_rate_limit_requests_safety_stop(self) -> None:
        arguments = SimpleNamespace(
            model_user="qian-qi",
            openclaw_agent="telegram-bot-8681289317",
            model="codex/gpt-5.3-codex-spark",
            auth_profile_allowlist_sha256=TEST_AUTH_PROFILE_ALLOWLIST_SHA256,
            model_session_key="paper-decision",
        )
        completed = SimpleNamespace(
            returncode=1,
            stdout=json.dumps({
                "status": "error",
                "error": {
                    "profileId": "openai:test-profile",
                    "profileFailureReason": "rate_limit",
                },
            }),
            stderr="",
        )
        history = [
            {"observed_at_ms": index, "bid": 1.1, "ask": 1.2,
             "mid": 1.15}
            for index in range(6)
        ]
        with mock.patch.object(
                agent, "_run_model_command", return_value=completed), \
                self.assertRaises(agent.SafetyStopError) as raised:
            agent.model_decision(
                arguments, history, 0.0,
                {"realized_pnl_estimate": 0.0,
                 "unrealized_pnl_estimate": 0.0})
        self.assertEqual(
            raised.exception.suspension_code, agent.MODEL_AUTH_RATE_LIMIT)

    def test_ambiguous_profile_429_fails_closed(self) -> None:
        document = {
            "status": "error",
            "error": {
                "profileId": "openai:current-profile",
                "promptError": "429 Too Many Requests: rate limit exceeded",
            },
        }
        self.assertEqual(
            agent._model_failure_category(document),
            agent.MODEL_AUTH_RATE_LIMIT)

    def test_explicit_profile_failure_promotes_prompt_429(self) -> None:
        document = {
            "status": "error",
            "error": {
                "profileId": "openai:current-profile",
                "profileFailureReason": "rate_limit",
                "promptError": "429 Too Many Requests: rate limit exceeded",
            },
        }
        self.assertEqual(
            agent._model_failure_category(document),
            agent.MODEL_AUTH_RATE_LIMIT)

    def test_codex_subscription_usage_limit_requests_safety_stop(self) -> None:
        document = {
            "type": "session.ended",
            "data": {
                "status": "error",
                "promptError": (
                    "You've reached your Codex subscription usage limit. "
                    "Wait until the retry time or use another Codex account."),
            },
        }
        self.assertEqual(
            agent._model_failure_category(document),
            agent.MODEL_AUTH_RATE_LIMIT)

    def test_plain_prompt_429_fails_closed(self) -> None:
        document = {
            "type": "session.ended",
            "data": {
                "status": "error",
                "promptError": "429 Too Many Requests: rate limit exceeded",
            },
        }
        self.assertEqual(
            agent._model_failure_category(document),
            agent.MODEL_AUTH_RATE_LIMIT)

    def test_gateway_ok_error_payload_subscription_limit_stops(self) -> None:
        arguments = SimpleNamespace(
            model_user="qian-qi",
            openclaw_agent="telegram-bot-8681289317",
            model="codex/gpt-5.3-codex-spark",
            auth_profile_allowlist_sha256=TEST_AUTH_PROFILE_ALLOWLIST_SHA256,
            model_session_key="paper-decision",
        )
        envelope = {
            "status": "ok",
            "summary": "completed",
            "result": {
                "payloads": [{
                    "isError": True,
                    "text": (
                        "You've reached your Codex subscription usage limit. "
                        "Use another Codex account."),
                }],
                "meta": {"agentMeta": {"fallbackAttempts": [{
                    "profileId": "openai:old-profile",
                    "failoverReason": "rate_limit",
                }]}},
            },
        }
        completed = SimpleNamespace(
            returncode=0, stdout=json.dumps(envelope), stderr="")
        history = [
            {"observed_at_ms": index, "bid": 1.1, "ask": 1.2,
             "mid": 1.15}
            for index in range(6)
        ]
        with mock.patch.object(
                agent, "_run_model_command", return_value=completed), \
                self.assertRaises(agent.SafetyStopError) as raised:
            agent.model_decision(
                arguments, history, 0.0,
                {"realized_pnl_estimate": 0.0,
                 "unrealized_pnl_estimate": 0.0})
        self.assertEqual(
            raised.exception.suspension_code, agent.MODEL_AUTH_RATE_LIMIT)

    def test_gateway_ok_error_payload_plain_429_stops(self) -> None:
        arguments = SimpleNamespace(
            model_user="qian-qi",
            openclaw_agent="telegram-bot-8681289317",
            model="codex/gpt-5.3-codex-spark",
            auth_profile_allowlist_sha256=TEST_AUTH_PROFILE_ALLOWLIST_SHA256,
            model_session_key="paper-decision",
        )
        envelope = {
            "status": "ok",
            "summary": "completed",
            "result": {"payloads": [{
                "isError": True,
                "text": "429 Too Many Requests: rate limit exceeded",
            }]},
        }
        completed = SimpleNamespace(
            returncode=0, stdout=json.dumps(envelope), stderr="")
        history = [
            {"observed_at_ms": index, "bid": 1.1, "ask": 1.2,
             "mid": 1.15}
            for index in range(6)
        ]
        with mock.patch.object(
                agent, "_run_model_command", return_value=completed), \
                self.assertRaises(agent.SafetyStopError) as raised:
            agent.model_decision(
                arguments, history, 0.0,
                {"realized_pnl_estimate": 0.0,
                 "unrealized_pnl_estimate": 0.0})
        self.assertEqual(
            raised.exception.suspension_code, agent.MODEL_AUTH_RATE_LIMIT)

    def test_successful_decision_ignores_historical_rate_limit_metadata(
            self) -> None:
        arguments = SimpleNamespace(
            model_user="qian-qi",
            openclaw_agent="telegram-bot-8681289317",
            model="codex/gpt-5.3-codex-spark",
            auth_profile_allowlist_sha256=TEST_AUTH_PROFILE_ALLOWLIST_SHA256,
            model_session_key="paper-decision",
        )
        envelope = {
            "status": "ok",
            "result": {
                "finalAssistantVisibleText": json.dumps({
                    "action": "HOLD", "confidence": 0.8,
                    "rationale": "healthy profile completed the decision",
                }),
                "meta": {
                    "traceAttempts": [{
                        "profileId": "openai:old-profile",
                        "failoverReason": "rate_limit",
                    }],
                },
            },
        }
        completed = SimpleNamespace(
            returncode=0, stdout=json.dumps(envelope), stderr="")
        history = [
            {"observed_at_ms": index, "bid": 1.1, "ask": 1.2,
             "mid": 1.15}
            for index in range(6)
        ]
        with mock.patch.object(
                agent, "_run_model_command", return_value=completed):
            decision = agent.model_decision(
                arguments, history, 0.0,
                {"realized_pnl_estimate": 0.0,
                 "unrealized_pnl_estimate": 0.0})
        self.assertEqual(decision["action"], "HOLD")

    def test_success_envelope_with_bad_decision_ignores_old_failover(
            self) -> None:
        arguments = SimpleNamespace(
            model_user="qian-qi",
            openclaw_agent="telegram-bot-8681289317",
            model="codex/gpt-5.3-codex-spark",
            auth_profile_allowlist_sha256=TEST_AUTH_PROFILE_ALLOWLIST_SHA256,
            model_session_key="paper-decision",
        )
        envelope = {
            "status": "ok",
            "result": {
                "finalAssistantVisibleText": "not-json",
                "meta": {
                    "traceAttempts": [{
                        "profileId": "openai:old-profile",
                        "failoverReason": "rate_limit",
                    }],
                },
            },
        }
        completed = SimpleNamespace(
            returncode=0, stdout=json.dumps(envelope), stderr="")
        history = [
            {"observed_at_ms": index, "bid": 1.1, "ask": 1.2,
             "mid": 1.15}
            for index in range(6)
        ]
        with mock.patch.object(
                agent, "_run_model_command", return_value=completed), \
                self.assertRaises(RuntimeError) as raised:
            agent.model_decision(
                arguments, history, 0.0,
                {"realized_pnl_estimate": 0.0,
                 "unrealized_pnl_estimate": 0.0})
        self.assertNotIsInstance(raised.exception, agent.SafetyStopError)

    def test_current_429_beats_history_independent_of_key_order(self) -> None:
        historical = {
            "traceAttempts": [{
                "profileId": "openai:old-profile",
                "profileFailureReason": "rate_limit",
            }],
        }
        current = {"status": 429, "message": "Too Many Requests"}
        first = {
            "status": "error", "error": current, "meta": historical,
        }
        second = {
            "status": "error", "meta": historical, "error": current,
        }
        self.assertEqual(
            agent._model_failure_category(first),
            agent.MODEL_AUTH_RATE_LIMIT)
        self.assertEqual(
            agent._model_failure_category(second),
            agent.MODEL_AUTH_RATE_LIMIT)

    def test_numeric_http_429_fails_closed(self) -> None:
        document = {
            "status": "error",
            "error": {"statusCode": 429, "message": "Too Many Requests"},
        }
        self.assertEqual(
            agent._model_failure_category(document),
            agent.MODEL_AUTH_RATE_LIMIT)

    def test_numeric_http_401_is_auth_unusable(self) -> None:
        document = {
            "status": "error",
            "error": {"statusCode": 401, "message": "Unauthorized"},
        }
        self.assertEqual(
            agent._model_failure_category(document),
            agent.MODEL_AUTH_UNUSABLE)

    def test_status_only_http_401_is_auth_unusable(self) -> None:
        document = {"status": "error", "error": {"statusCode": 401}}
        self.assertEqual(
            agent._model_failure_category(document),
            agent.MODEL_AUTH_UNUSABLE)

    def test_status_only_http_402_is_auth_billing(self) -> None:
        document = {"status": "error", "error": {"httpStatus": 402}}
        self.assertEqual(
            agent._model_failure_category(document),
            agent.MODEL_AUTH_BILLING)

    def test_string_error_429_fails_closed(self) -> None:
        document = {
            "status": "error",
            "error": "429 Too Many Requests: rate limit exceeded",
        }
        self.assertEqual(
            agent._model_failure_category(document),
            agent.MODEL_AUTH_RATE_LIMIT)

    def test_stderr_auth_error_is_not_hidden_by_unrelated_stdout_json(
            self) -> None:
        completed = SimpleNamespace(
            returncode=1,
            stdout=json.dumps({"diagnostic": "gateway invocation started"}),
            stderr=json.dumps({
                "status": "error",
                "error": {"code": "refresh_token_reused"},
            }),
        )
        documents = agent._model_failure_documents(completed)
        self.assertEqual(
            agent._model_failure_category(documents),
            agent.MODEL_AUTH_UNUSABLE)

    def test_gateway_bad_decision_keeps_stderr_auth_failure(self) -> None:
        arguments = SimpleNamespace(
            model_user="qian-qi",
            openclaw_agent="telegram-bot-8681289317",
            model="codex/gpt-5.3-codex-spark",
            auth_profile_allowlist_sha256=TEST_AUTH_PROFILE_ALLOWLIST_SHA256,
            model_session_key="paper-decision",
        )
        envelope = {
            "status": "ok",
            "result": {"finalAssistantVisibleText": "not-json"},
        }
        completed = SimpleNamespace(
            returncode=0,
            stdout=json.dumps(envelope),
            stderr=json.dumps({
                "status": "error",
                "error": {"code": "refresh_token_reused"},
            }),
        )
        history = [
            {"observed_at_ms": index, "bid": 1.1, "ask": 1.2,
             "mid": 1.15}
            for index in range(6)
        ]
        with mock.patch.object(
                agent, "_run_model_command", return_value=completed), \
                self.assertRaises(agent.SafetyStopError) as raised:
            agent.model_decision(
                arguments, history, 0.0,
                {"realized_pnl_estimate": 0.0,
                 "unrealized_pnl_estimate": 0.0})
        self.assertEqual(
            raised.exception.suspension_code, agent.MODEL_AUTH_UNUSABLE)

    def test_valid_decision_ignores_unrelated_stderr_failure(self) -> None:
        arguments = SimpleNamespace(
            model_user="qian-qi",
            openclaw_agent="telegram-bot-8681289317",
            model="codex/gpt-5.3-codex-spark",
            auth_profile_allowlist_sha256=TEST_AUTH_PROFILE_ALLOWLIST_SHA256,
            model_session_key="paper-decision",
        )
        envelope = {
            "status": "ok",
            "result": {"finalAssistantVisibleText": json.dumps({
                "action": "HOLD", "confidence": 0.8,
                "rationale": "current decision completed",
            })},
        }
        completed = SimpleNamespace(
            returncode=0,
            stdout=json.dumps(envelope),
            stderr=json.dumps({
                "status": "error",
                "error": {"code": "refresh_token_reused"},
            }),
        )
        history = [
            {"observed_at_ms": index, "bid": 1.1, "ask": 1.2,
             "mid": 1.15}
            for index in range(6)
        ]
        with mock.patch.object(
                agent, "_run_model_command", return_value=completed):
            decision = agent.model_decision(
                arguments, history, 0.0,
                {"realized_pnl_estimate": 0.0,
                 "unrealized_pnl_estimate": 0.0})
        self.assertEqual(decision["action"], "HOLD")

    def test_model_timeout_is_not_misclassified_as_auth(self) -> None:
        document = {"status": "error", "error": {"reason": "timeout"}}
        self.assertIsNone(agent._model_failure_category(document))

    def test_real_oauth_refresh_error_is_structurally_classified(self) -> None:
        document = {
            "type": "session.ended",
            "data": {
                "status": "error",
                "promptError": (
                    "OAuth token refresh failed: 401 "
                    '{"type":"error","status":401,"error":'
                    '{"type":"invalid_request_error",'
                    '"code":"refresh_token_reused"}}'),
            },
        }
        self.assertEqual(
            agent._model_failure_category(document),
            agent.MODEL_AUTH_UNUSABLE)

    def test_unclassified_model_transport_failure_is_not_auth(self) -> None:
        arguments = SimpleNamespace(
            model_user="qian-qi",
            openclaw_agent="telegram-bot-8681289317",
            model="codex/gpt-5.3-codex-spark",
            auth_profile_allowlist_sha256=TEST_AUTH_PROFILE_ALLOWLIST_SHA256,
            model_session_key="paper-decision",
        )
        completed = SimpleNamespace(
            returncode=1, stdout="", stderr="gateway temporarily unavailable")
        history = [
            {"observed_at_ms": index, "bid": 1.1, "ask": 1.2,
             "mid": 1.15}
            for index in range(6)
        ]
        with mock.patch.object(
                agent, "_run_model_command", return_value=completed), \
                self.assertRaises(RuntimeError) as raised:
            agent.model_decision(
                arguments, history, 0.0,
                {"realized_pnl_estimate": 0.0,
                 "unrealized_pnl_estimate": 0.0})
        self.assertNotIsInstance(raised.exception, agent.SafetyStopError)

    def test_mutation_accepts_null_payload_and_top_level_order_id(self) -> None:
        arguments = SimpleNamespace(
            agent_user="hepta-agent-alpha", heptactl="/usr/bin/heptactl",
            tool_socket="/run/hepta-agent-alpha/tools.sock",
            token_file="/run/hepta-agent-alpha/sessions/local-paper.token",
        )
        response = {
            "status": "ok", "tool": "trade.place_order",
            "reason_code": "", "detail": "", "order_id": 5,
            "payload": None,
        }
        with mock.patch.object(agent, "run_json", return_value=response):
            result = agent.tool_response(
                arguments, "trade.place_order", {"instrument": "EUR.USD"},
                "command-1")
        self.assertEqual(result, response)

    def test_standard_tool_call_uses_stable_frame_timeout(self) -> None:
        arguments = SimpleNamespace(
            agent_user="hepta-agent-alpha", heptactl="/usr/bin/heptactl",
            tool_socket="/run/hepta-agent-alpha/tools.sock",
            token_file="/run/hepta-agent-alpha/sessions/local-paper.token",
        )
        response = {
            "status": "ok", "tool": "portfolio.list_positions",
            "reason_code": "", "detail": "", "order_id": -1,
            "payload": {},
        }
        with mock.patch.object(
                agent, "run_json", return_value=response) as run:
            agent.tool_response(arguments, "portfolio.list_positions")
        command = run.call_args.args[0]
        timeout_index = command.index("--io-timeout-ms") + 1
        self.assertEqual(
            command[timeout_index],
            str(agent.HEPTACTL_STABLE_IO_TIMEOUT_MS))

    def test_short_tool_call_keeps_default_frame_timeout(self) -> None:
        arguments = SimpleNamespace(
            agent_user="hepta-agent-alpha", heptactl="/usr/bin/heptactl",
            tool_socket="/run/hepta-agent-alpha/tools.sock",
            token_file="/run/hepta-agent-alpha/sessions/local-paper.token",
        )
        response = {
            "status": "ok", "tool": "orders.list",
            "reason_code": "", "detail": "", "order_id": -1,
            "payload": {},
        }
        with mock.patch.object(
                agent, "run_json", return_value=response) as run:
            agent.tool_response(arguments, "orders.list", timeout=4.0)
        command = run.call_args.args[0]
        timeout_index = command.index("--io-timeout-ms") + 1
        self.assertEqual(
            command[timeout_index],
            str(agent.HEPTACTL_DEFAULT_IO_TIMEOUT_MS))

    def test_read_tool_still_requires_object_payload(self) -> None:
        arguments = SimpleNamespace(
            agent_user="hepta-agent-alpha", heptactl="/usr/bin/heptactl",
            tool_socket="/run/hepta-agent-alpha/tools.sock",
            token_file="/run/hepta-agent-alpha/sessions/local-paper.token",
        )
        response = {
            "status": "ok", "tool": "portfolio.list_positions",
            "reason_code": "", "detail": "", "order_id": -1,
            "payload": None,
        }
        with mock.patch.object(agent, "run_json", return_value=response), \
                self.assertRaisesRegex(RuntimeError, "invalid payload"):
            agent.tool(arguments, "portfolio.list_positions")

    def test_legacy_state_migration_clears_polluted_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text(json.dumps({
                "schema": agent.LEGACY_SCHEMA,
                "decisions": 24,
                "entries": 3,
                "realized_pnl_estimate": 1.25,
                "entry_mid": None,
                "entry_quantity": 0.0,
                "entry_at_ms": None,
                "history": [{
                    "at_ms": 1000, "bid": 1.1, "ask": 1.2, "mid": 1.15,
                }],
                "last_decision": {"action": "HOLD"},
            }), encoding="utf-8")
            state = agent.load_state(path)
            self.assertEqual(state["schema"], agent.SCHEMA)
            self.assertEqual(state["decisions"], 24)
            self.assertEqual(state["entries"], 3)
            self.assertEqual(state["realized_pnl_estimate"], 1.25)
            self.assertEqual(state["history"], [])
            self.assertIsNone(state["last_decision"])

    def test_recovery_pnl_evidence_survives_state_reload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            state = agent.empty_state()
            state.update({
                "recovery_raw_price_pnl": -1.0,
                "recovery_raw_price_pnl_quote_currency": "USD",
                "recovery_raw_price_pnl_commission_included": False,
                "recovery_raw_price_pnl_evidence": {
                    "schema": (
                        "hepta.local-ai-paper-recovery-raw-price-pnl-"
                        "evidence.v1"),
                    "amount": -1.0,
                },
            })
            path.write_text(json.dumps(state), encoding="utf-8")
            loaded = agent.load_state(path)
        self.assertEqual(loaded["recovery_raw_price_pnl"], -1.0)
        self.assertEqual(
            loaded["recovery_raw_price_pnl_quote_currency"], "USD")
        self.assertFalse(
            loaded["recovery_raw_price_pnl_commission_included"])
        self.assertEqual(
            loaded["recovery_raw_price_pnl_evidence"]["amount"], -1.0)

    def test_quote_history_deduplicates_observation_timestamp(self) -> None:
        first = {
            "observed_at_ms": 1000, "bid": 1.1, "ask": 1.2, "mid": 1.15,
        }
        replacement = {
            "observed_at_ms": 1000, "bid": 1.2, "ask": 1.3, "mid": 1.25,
        }
        history = agent.append_quote_sample([first], replacement)
        self.assertEqual(history, [replacement])

    def test_hold_does_not_request_execution_quote(self) -> None:
        arguments = SimpleNamespace(confidence=0.62)
        decision = {"action": "HOLD", "confidence": 0.99,
                    "rationale": "no edge"}
        with mock.patch.object(agent, "fresh_quote") as fresh, \
                mock.patch.object(agent, "active_orders") as orders:
            agent.apply_decision(
                arguments, agent.empty_state(), 0.0, decision, False)
        fresh.assert_not_called()
        orders.assert_not_called()

    def test_low_confidence_reversal_does_not_exit(self) -> None:
        arguments = SimpleNamespace(confidence=0.62)
        decision = {
            "action": "SELL", "confidence": 0.61,
            "rationale": "weak reversal",
        }
        with mock.patch.object(agent, "_close_strategy_position") as close:
            agent.apply_decision(
                arguments, agent.empty_state(), 25_000.0, decision, False)
        close.assert_not_called()

    def test_confident_reversal_records_strategy_exit(self) -> None:
        arguments = SimpleNamespace(confidence=0.62)
        state = agent.empty_state()
        decision = {
            "action": "SELL", "confidence": 0.8,
            "rationale": "confirmed reversal",
        }
        with mock.patch.object(agent, "_close_strategy_position") as close:
            agent.apply_decision(
                arguments, state, 25_000.0, decision, False)
        close.assert_called_once_with(
            arguments, state, 25_000.0, "MODEL_REVERSAL", decision)

    def test_explicit_holding_timeout_exits_without_reversal(self) -> None:
        arguments = SimpleNamespace(confidence=0.62, max_holding_sec=1)
        state = agent.empty_state()
        decision = {
            "action": "HOLD", "confidence": 0.1,
            "rationale": "timeout owns the exit",
        }
        with mock.patch.object(agent, "_close_strategy_position") as close:
            agent.apply_decision(
                arguments, state, -25_000.0, decision, True)
        close.assert_called_once_with(
            arguments, state, -25_000.0, "MAX_HOLDING_TIMEOUT", None)

    def test_disabled_holding_timeout_cannot_be_forced_by_caller(self) -> None:
        arguments = SimpleNamespace(confidence=0.62, max_holding_sec=0)
        decision = {
            "action": "HOLD", "confidence": 0.1,
            "rationale": "model keeps the position open",
        }
        with mock.patch.object(agent, "_close_strategy_position") as close:
            agent.apply_decision(
                arguments, agent.empty_state(), -25_000.0, decision, True)
        close.assert_not_called()

    def test_confirmed_strategy_exit_persists_flat_snapshot(self) -> None:
        arguments = SimpleNamespace(
            strategy_id="test-strategy", strategy_version="2",
            strategy_sha256="sha256:" + "1" * 64,
        )
        state = agent.empty_state()
        state.update({
            "position": -25_000.0,
            "unrealized_pnl_estimate": -1.0,
            "entry_mid": 1.2,
            "entry_quantity": -25_000.0,
            "entry_at_ms": 1,
            "entry_order_id": 95,
        })
        current = {
            "observed_at_ms": 1000, "bid": 1.2001,
            "ask": 1.2002, "mid": 1.20015,
        }
        with mock.patch.object(
                agent, "fresh_quote", return_value=current), \
                mock.patch.object(agent, "flatten", return_value=0.0), \
                mock.patch.object(agent, "_persist_state"):
            confirmed = agent._close_strategy_position(
                arguments, state, -25_000.0, "MAX_HOLDING_TIMEOUT")
        self.assertEqual(confirmed, 0.0)
        self.assertEqual(state["position"], 0.0)
        self.assertEqual(state["unrealized_pnl_estimate"], 0.0)
        self.assertEqual(state["entry_quantity"], 0.0)
        self.assertIsNone(state["entry_mid"])
        self.assertIsNone(state["entry_at_ms"])
        self.assertIsNone(state["entry_order_id"])
        self.assertEqual(state["exits"], 1)
        self.assertEqual(
            state["last_exit_trigger"]["result"],
            "ECONOMIC_FLATTEN_CONFIRMED")

    def test_max_adverse_move_is_directional(self) -> None:
        self.assertTrue(agent.adverse_move_reached(25_000, 1.2, 1.198))
        self.assertFalse(agent.adverse_move_reached(25_000, 1.2, 1.201))
        self.assertTrue(agent.adverse_move_reached(-25_000, 1.2, 1.202))
        self.assertFalse(agent.adverse_move_reached(-25_000, 1.2, 1.199))

    def test_preview_and_place_share_order_fingerprint_fields(self) -> None:
        current = {
            "observed_at_ms": 1000, "bid": 1.1,
            "ask": 1.2, "mid": 1.15,
        }
        for side, expected_reference in (("BUY", 1.2), ("SELL", 1.1)):
            with self.subTest(side=side):
                order = agent.order_arguments(side, current, 5000)
                self.assertEqual(order["quantity"], 25_000)
                self.assertEqual(order["order_type"], "MKT")
                self.assertEqual(order["tif"], "DAY")
                self.assertEqual(
                    order["reference_price"], expected_reference)
                self.assertNotIn("limit_price", order)
                self.assertNotIn("reduce_only", order)
                place = {
                    **order, "preview_permit": "sha256:" + "1" * 64,
                }
                self.assertEqual(
                    {key: value for key, value in place.items()
                     if key != "preview_permit"}, order)

    def test_flatten_preview_uses_nested_authoritative_contract(self) -> None:
        preview = {
            "command_id": "command-1",
            "preview_permit": "sha256:" + "2" * 64,
            "authoritative_preview": {
                "source": "IB", "authoritative": True,
                "position_generation": 7,
                "position_quantity": -225_000, "side": "BUY",
                "quantity": 25_000, "order_type": "MKT",
                "reference_price": 1.15325, "reduce_only": True,
                "risk_approved": True,
            },
        }
        self.assertEqual(
            agent._validated_flatten_preview(preview, -225_000, 7),
            ("BUY", 25_000.0))

    def test_flatten_preview_rejects_stale_or_oversized_contract(self) -> None:
        base = {
            "source": "IB", "authoritative": True,
            "position_generation": 7,
            "position_quantity": -225_000, "side": "BUY",
            "quantity": 25_000, "order_type": "MKT",
            "reference_price": 1.15325, "reduce_only": True,
            "risk_approved": True,
        }
        for update in (
                {"position_generation": 6}, {"quantity": 225_000},
                {"position_quantity": -200_000}, {"reduce_only": False},
                {"side": "SELL"}, {"order_type": "LMT"},
                {"instrument": None}, {"instrument": ""},
                {"instrument": "GBP.USD"}):
            authoritative = {**base, **update}
            with self.subTest(update=update), self.assertRaisesRegex(
                    agent.RecoveryRequiredError, "strict reduce-only"):
                agent._validated_flatten_preview(
                    {"authoritative_preview": authoritative}, -225_000, 7)

        exact_instrument = {**base, "instrument": "EUR.USD"}
        self.assertEqual(agent._validated_flatten_preview(
            {"authoritative_preview": exact_instrument}, -225_000, 7),
            ("BUY", 25_000.0))

    def test_flatten_preview_rejects_missing_authoritative_contract(self) -> None:
        with self.assertRaisesRegex(
                agent.RecoveryRequiredError, "lacked authoritative detail"):
            agent._validated_flatten_preview({"side": "BUY"}, -25_000, 1)

    def test_flatten_mutation_uncertainty_latches_recovery(self) -> None:
        arguments = SimpleNamespace(
            fill_timeout_sec=30, state_file=None,
        )
        state = agent.empty_state()
        preview = {
            "command_id": "command-1",
            "preview_permit": "sha256:" + "2" * 64,
            "authoritative_preview": {
                "source": "IB", "authoritative": True,
                "position_generation": 7,
                "position_quantity": -25_000, "side": "BUY",
                "quantity": 25_000, "order_type": "MKT",
                "reference_price": 1.15325, "reduce_only": True,
                "risk_approved": True,
            },
        }
        with mock.patch.object(
                agent, "position_snapshot", return_value=(-25_000.0, 7, 9)), \
                mock.patch.object(agent, "tool", return_value=preview), \
                mock.patch.object(
                    agent, "tool_response",
                    side_effect=TimeoutError("response lost")), \
                mock.patch.object(
                    agent, "_ensure_recovery_halt", return_value=True) as halt, \
                self.assertRaisesRegex(
                    agent.RecoveryRequiredError,
                    "flatten outcome uncertain"):
            agent.flatten(arguments, state)
        self.assertTrue(state["recovery_required"])
        self.assertEqual(state["last_order_result"], "RECOVERY_REQUIRED")
        self.assertEqual(state["pending_mutation_kind"], "FLATTEN_POSITION")
        self.assertEqual(state["pending_mutation_command_id"], "command-1")
        self.assertEqual(
            state["pending_mutation_token_name"], "local-paper.token")
        halt.assert_called_once()

    def test_flatten_persists_confirmed_order_identity_for_accounting(
            self) -> None:
        arguments = SimpleNamespace(fill_timeout_sec=30, state_file=None)
        state = agent.empty_state()
        preview = {
            "command_id": "command-1",
            "preview_permit": "sha256:" + "2" * 64,
            "authoritative_preview": {
                "source": "IB", "authoritative": True,
                "position_generation": 7,
                "position_quantity": -25_000, "side": "BUY",
                "quantity": 25_000, "order_type": "MKT",
                "reference_price": 1.15325, "reduce_only": True,
                "risk_approved": True,
            },
        }
        with mock.patch.object(
                agent, "position_snapshot",
                side_effect=[(-25_000.0, 7, 9), (0.0, 8, 10)]), \
                mock.patch.object(agent, "tool", return_value=preview), \
                mock.patch.object(agent, "tool_response", return_value={
                    "status": "ok", "order_id": 96, "payload": None,
                }), \
                mock.patch.object(
                    agent, "_settle_order", return_value="FILLED"), \
                mock.patch.object(agent, "_persist_state"):
            self.assertEqual(agent.flatten(arguments, state), 0.0)
        self.assertEqual(state["last_flatten_order_id"], 96)
        self.assertEqual(
            state["last_order_result"], "ECONOMIC_FLATTEN_CONFIRMED")

    def test_entry_waits_for_order_resolution_before_campaign_close(self) -> None:
        arguments = SimpleNamespace(
            runtime_dir="/run/hepta-local-ai-paper-agent",
            agent_user="hepta-agent-alpha", strategy_id="strategy-a",
            strategy_sha256="sha256:" + "1" * 64,
            strategy_version="1", max_holding_sec=0,
            fill_timeout_sec=30,
        )
        decision = {
            "action": "BUY", "confidence": 0.9,
            "rationale": "coherent trend",
        }
        quote = {
            "observed_at_ms": agent.now_ms(), "bid": 1.1,
            "ask": 1.2, "mid": 1.15,
        }
        events: list[str] = []

        def campaign_call(
                _arguments, action, _request_id, _extra=None, **_kwargs):
            events.append("campaign:" + action)
            return {"status": "ok", "state": {
                "active_deadline_at_ms": agent.now_ms() + 20_000,
            }}

        def tool_call(
                _arguments, name, _values=None, _call_id=None, **_kwargs):
            events.append("tool:" + name)
            if name == "account.get_summary":
                return {"authoritative": True}
            if name == "risk.preview_order":
                self.assertEqual(_values["order_type"], "MKT")
                self.assertEqual(
                    _values["reference_price"], quote["ask"])
                self.assertNotIn("limit_price", _values)
                return {
                    "command_id": "command-1",
                    "preview_permit": "sha256:" + "2" * 64,
                }
            raise AssertionError(name)

        def tool_response_call(
                _arguments, name, _values=None, _call_id=None, **_kwargs):
            self.assertEqual(
                state["pending_mutation_command_id"], "command-1")
            self.assertEqual(
                state["pending_mutation_token_name"], "local-paper.token")
            self.assertEqual(_values["order_type"], "MKT")
            self.assertEqual(_values["reference_price"], quote["ask"])
            self.assertNotIn("limit_price", _values)
            events.append("tool_response:" + name)
            return {"status": "ok", "order_id": 4, "payload": None}

        fill_event = {
            "stream_epoch": "execution-1", "sequence": 1,
            "type": "order.fill", "order_id": 4,
            "instrument": "EUR.USD", "side": "BOT",
            "status": "ExecutionDetails", "reason_code": "",
            "filled_quantity": 25_000,
            "remaining_quantity": 0,
            "average_fill_price": 1.2,
        }
        state = agent.empty_state()

        with mock.patch.object(agent, "write_agent_json") as write, \
                mock.patch.object(agent, "campaign", side_effect=campaign_call), \
                mock.patch.object(agent, "tool", side_effect=tool_call), \
                mock.patch.object(
                    agent, "tool_response", side_effect=tool_response_call), \
                mock.patch.object(
                    agent, "position_snapshot",
                    side_effect=[
                        (0.0, 1, 1),
                        (25_000.0, 2, 2),
                        (25_000.0, 2, 2),
                    ]), \
                mock.patch.object(
                    agent, "active_orders", return_value=[]), \
                mock.patch.object(
                    agent, "next_execution_event", return_value=fill_event), \
                mock.patch.object(
                    agent, "authoritative_broker_fill", return_value={
                        "broker_execution_id": "execution-entry-4",
                        "account": "DU12345",
                        "execution_domain": "alpha",
                        "instrument": "EUR.USD", "order_id": 4,
                        "side": "BUY", "filled_quantity": 25_000,
                        "average_fill_price": 1.2,
                    }), \
                mock.patch.object(agent.time, "sleep"):
            filled = agent.enter(arguments, state, decision, quote)

        self.assertEqual(filled, 25_000.0)
        self.assertEqual(state["last_order_result"], "ECONOMIC_FILL_CONFIRMED")
        self.assertEqual(state["entry_order_id"], 4)
        self.assertIsNone(state["pending_order_id"])

        intent = write.call_args_list[0].args[1]
        self.assertEqual(intent["schema"], "hepta.trade-intent.v2")
        self.assertEqual(intent["order_type"], "MKT")
        self.assertEqual(intent["tif"], "DAY")
        self.assertEqual(intent["reference_price"], quote["ask"])
        self.assertNotIn("limit_price", intent)
        self.assertEqual(intent["max_holding_ms"], 0)
        self.assertIn("AI reversal", intent["exit_plan"])
        self.assertIn("campaign end-flat", intent["exit_plan"])

        self.assertLess(
            events.index("tool_response:trade.place_order"),
            events.index("campaign:close_cycle"))
        self.assertGreater(
            events.index("campaign:close_cycle"),
            events.index("tool:account.get_summary"))

    def test_disappeared_order_without_evidence_halts_and_requires_recovery(
            self) -> None:
        arguments = SimpleNamespace(
            runtime_dir="/run/hepta-local-ai-paper-agent",
            agent_user="hepta-agent-alpha", strategy_id="strategy-a",
            strategy_sha256="sha256:" + "1" * 64,
            strategy_version="1", max_holding_sec=0,
            fill_timeout_sec=0,
        )
        decision = {
            "action": "SELL", "confidence": 0.9,
            "rationale": "coherent trend",
        }
        quote = {
            "observed_at_ms": agent.now_ms(), "bid": 1.1,
            "ask": 1.2, "mid": 1.15,
        }
        state = agent.empty_state()
        campaign_calls: list[tuple[str, list[str] | None]] = []

        def campaign_call(
                _arguments, action, _request_id, extra=None, **_kwargs):
            campaign_calls.append((action, extra))
            return {"status": "ok", "state": {
                "active_deadline_at_ms": agent.now_ms() + 20_000,
            }}

        def tool_call(
                _arguments, name, _values=None, _call_id=None, **_kwargs):
            if name == "account.get_summary":
                return {"authoritative": True}
            if name == "risk.preview_order":
                return {
                    "command_id": "command-1",
                    "preview_permit": "sha256:" + "2" * 64,
                }
            raise AssertionError(name)

        with mock.patch.object(agent, "write_agent_json"), \
                mock.patch.object(agent, "campaign", side_effect=campaign_call), \
                mock.patch.object(agent, "tool", side_effect=tool_call), \
                mock.patch.object(agent, "tool_response", return_value={
                    "status": "ok", "order_id": 4, "payload": None,
                }), \
                mock.patch.object(
                    agent, "position_snapshot", side_effect=[
                        (0.0, 1, 1), (0.0, 1, 1), (0.0, 1, 1),
                    ]), \
                mock.patch.object(agent, "active_orders", return_value=[]), \
                mock.patch.object(agent, "orders_snapshot", return_value={
                    "authoritative": True, "active_order_ids": [],
                    "recent_orders": [],
                }), \
                self.assertRaisesRegex(
                    agent.RecoveryRequiredError, "RECOVERY_REQUIRED"):
            agent.enter(arguments, state, decision, quote)

        self.assertTrue(state["recovery_required"])
        self.assertTrue(state["recovery_halt_confirmed"])
        self.assertEqual(state["pending_order_id"], 4)
        self.assertEqual(
            [action for action, _extra in campaign_calls],
            ["open_cycle", "close_cycle", "halt"])
        close_extra = campaign_calls[1][1]
        self.assertIsNotNone(close_extra)
        self.assertEqual(close_extra[-1], "PLACE_UNCERTAIN")
        self.assertEqual(
            campaign_calls[2][1],
            ["--reason-code", agent.RECOVERY_HALT_REASON])

    def test_explicit_inactive_is_resolved_non_fill_not_success(self) -> None:
        arguments = SimpleNamespace(
            runtime_dir="/run/hepta-local-ai-paper-agent",
            agent_user="hepta-agent-alpha", strategy_id="strategy-a",
            strategy_sha256="sha256:" + "1" * 64,
            strategy_version="1", max_holding_sec=0,
            fill_timeout_sec=30,
        )
        decision = {
            "action": "BUY", "confidence": 0.9,
            "rationale": "coherent trend",
        }
        quote = {
            "observed_at_ms": agent.now_ms(), "bid": 1.1,
            "ask": 1.2, "mid": 1.15,
        }
        state = agent.empty_state()
        campaign_calls: list[tuple[str, list[str] | None]] = []

        def campaign_call(
                _arguments, action, _request_id, extra=None, **_kwargs):
            campaign_calls.append((action, extra))
            return {"status": "ok", "state": {
                "active_deadline_at_ms": agent.now_ms() + 20_000,
            }}

        def tool_call(
                _arguments, name, _values=None, _call_id=None, **_kwargs):
            if name == "account.get_summary":
                return {"authoritative": True}
            if name == "risk.preview_order":
                return {
                    "command_id": "command-1",
                    "preview_permit": "sha256:" + "2" * 64,
                }
            raise AssertionError(name)

        inactive = {
            "stream_epoch": "execution-1", "sequence": 3,
            "type": "order.status", "order_id": 4,
            "instrument": "EUR.USD", "side": "BUY",
            "status": "Inactive", "reason_code": "IB_ORDER_INACTIVE",
            "filled_quantity": 0, "remaining_quantity": 0,
            "average_fill_price": 0,
        }
        with mock.patch.object(agent, "write_agent_json"), \
                mock.patch.object(agent, "campaign", side_effect=campaign_call), \
                mock.patch.object(agent, "tool", side_effect=tool_call), \
                mock.patch.object(agent, "tool_response", return_value={
                    "status": "ok", "order_id": 4, "payload": None,
                }), \
                mock.patch.object(
                    agent, "position_snapshot", side_effect=[
                        (0.0, 1, 1), (0.0, 1, 1),
                    ]), \
                mock.patch.object(agent, "active_orders", return_value=[]), \
                mock.patch.object(
                    agent, "next_execution_event", return_value=inactive):
            filled = agent.enter(arguments, state, decision, quote)

        self.assertFalse(filled)
        self.assertFalse(state["recovery_required"])
        self.assertEqual(state["last_order_result"], "BROKER_TERMINAL_NON_FILL")
        self.assertEqual(
            [action for action, _extra in campaign_calls],
            ["open_cycle", "close_cycle"])
        self.assertEqual(campaign_calls[1][1][-1], "PLACE_REJECTED")

    def test_zero_quantity_filled_requires_recovery(self) -> None:
        arguments = SimpleNamespace(fill_timeout_sec=30)
        zero_fill = {
            "stream_epoch": "execution-1", "sequence": 1,
            "type": "order.status", "order_id": 4,
            "instrument": "EUR.USD", "side": "BUY",
            "status": "Filled", "reason_code":
                "IB_FILLED_ECONOMIC_EVIDENCE_REQUIRED",
            "filled_quantity": 0, "remaining_quantity": 0,
            "average_fill_price": 0,
        }
        with mock.patch.object(agent, "position_quantity", return_value=0.0), \
                mock.patch.object(
                    agent, "next_execution_event", return_value=zero_fill), \
                self.assertRaisesRegex(
                    agent.RecoveryRequiredError,
                    "Filled lacked positive economic evidence"):
            agent._settle_order(
                arguments, 4, "BUY", 25_000, 0.0,
                agent.time.monotonic() + 10)

    def test_recent_order_projection_resolves_event_relay_gap(self) -> None:
        arguments = SimpleNamespace(fill_timeout_sec=30)
        recent_fill = {
            "authoritative": True, "active_order_ids": [],
            "recent_orders": [{
                "order_id": 4, "status": "Filled", "terminal": True,
                "economic_fill": True, "filled_quantity": 25_000,
                "remaining_quantity": 0, "average_fill_price": 1.2,
                "reason_code": "", "observed_at_ms": 1234,
                "instrument": "EUR.USD", "side": "BUY",
            }],
        }
        with mock.patch.object(
                agent, "position_quantity", return_value=25_000.0), \
                mock.patch.object(
                    agent, "next_execution_event",
                    side_effect=agent.RecoveryRequiredError(
                        "RECOVERY_REQUIRED: EXECUTION_EVENT_RELAY_PUBLISH_MISSING")), \
                mock.patch.object(
                    agent, "orders_snapshot", return_value=recent_fill):
            result = agent._settle_order(
                arguments, 4, "BUY", 25_000, 0.0,
                agent.time.monotonic() + 10)
        self.assertEqual(result, "FILLED")

    def test_fill_waits_for_new_cash_and_position_generations(self) -> None:
        arguments = SimpleNamespace(fill_timeout_sec=1)
        fill_event = {
            "stream_epoch": "execution-1", "sequence": 1,
            "type": "order.fill", "order_id": 4,
            "instrument": "EUR.USD", "side": "BUY",
            "status": "ExecutionDetails", "reason_code": "",
            "filled_quantity": 25_000, "remaining_quantity": 0,
            "average_fill_price": 1.2,
        }
        recent_fill = {
            "authoritative": True, "active_order_ids": [],
            "recent_orders": [{
                "order_id": 4, "status": "Filled", "terminal": True,
                "economic_fill": True, "filled_quantity": 25_000,
                "remaining_quantity": 0, "average_fill_price": 1.2,
                "reason_code": "", "observed_at_ms": 1234,
                "instrument": "EUR.USD", "side": "BUY",
            }],
        }
        with mock.patch.object(
                agent, "position_snapshot", side_effect=[
                    RuntimeError("positions are not authoritative"),
                    (25_000.0, 2, 2),
                ]), \
                mock.patch.object(
                    agent, "next_execution_event",
                    side_effect=[fill_event, None]), \
                mock.patch.object(
                    agent, "orders_snapshot", return_value=recent_fill):
            result = agent._settle_order(
                arguments, 4, "BUY", 25_000, 0.0,
                agent.time.monotonic() + 10,
                baseline_position_generation=1,
                baseline_fx_cash_generation=1)
        self.assertEqual(result, "FILLED")

    def test_partial_fill_and_partial_position_require_recovery(self) -> None:
        arguments = SimpleNamespace(fill_timeout_sec=0)
        partial = {
            "authoritative": True, "active_order_ids": [],
            "recent_orders": [{
                "order_id": 4, "status": "Cancelled", "terminal": True,
                "economic_fill": True, "filled_quantity": 12_500,
                "remaining_quantity": 12_500, "average_fill_price": 1.2,
                "reason_code": "IB_ORDER_CANCELLED",
                "observed_at_ms": 1234, "instrument": "EUR.USD",
                "side": "BUY",
            }],
        }
        with mock.patch.object(
                agent, "position_quantity", return_value=12_500.0), \
                mock.patch.object(
                    agent, "orders_snapshot", return_value=partial), \
                self.assertRaisesRegex(
                    agent.RecoveryRequiredError, "filled=12500"):
            agent._settle_order(
                arguments, 4, "BUY", 25_000, 0.0,
                agent.time.monotonic() + 10)

    def test_full_fill_without_matching_position_requires_recovery(self) -> None:
        arguments = SimpleNamespace(fill_timeout_sec=0)
        full = {
            "authoritative": True, "active_order_ids": [],
            "recent_orders": [{
                "order_id": 4, "status": "Filled", "terminal": True,
                "economic_fill": True, "filled_quantity": 25_000,
                "remaining_quantity": 0, "average_fill_price": 1.2,
                "reason_code": "", "observed_at_ms": 1234,
                "instrument": "EUR.USD", "side": "BUY",
            }],
        }
        with mock.patch.object(agent, "position_quantity", return_value=0.0), \
                mock.patch.object(agent, "orders_snapshot", return_value=full), \
                self.assertRaisesRegex(
                    agent.RecoveryRequiredError, "current_position=0"):
            agent._settle_order(
                arguments, 4, "BUY", 25_000, 0.0,
                agent.time.monotonic() + 10)

    def test_economic_evidence_side_mismatch_requires_recovery(self) -> None:
        evidence = {
            "filled_quantity": 0.0, "remaining_quantity": 25_000.0,
            "average_fill_price": 0.0, "terminal_non_fill": "",
            "reason_code": "",
        }
        record = {
            "order_id": 4, "type": "order.fill",
            "instrument": "EUR.USD", "side": "SELL",
            "status": "ExecutionDetails", "filled_quantity": 25_000,
            "remaining_quantity": 0, "average_fill_price": 1.2,
        }
        with self.assertRaisesRegex(
                agent.RecoveryRequiredError, "side mismatch"):
            agent._apply_order_evidence(
                record, 4, "BUY", 25_000, 0.0, 25_000.0, evidence)

    def test_apply_decision_does_not_count_unconfirmed_entry(self) -> None:
        arguments = SimpleNamespace(confidence=0.62)
        state = agent.empty_state()
        decision = {
            "action": "BUY", "confidence": 0.9,
            "rationale": "coherent trend",
        }
        quote = {
            "observed_at_ms": 1000, "bid": 1.1,
            "ask": 1.2, "mid": 1.15,
        }
        with mock.patch.object(agent, "active_orders", return_value=[]), \
                mock.patch.object(agent, "fresh_quote", return_value=quote), \
                mock.patch.object(agent, "enter", return_value=None):
            agent.apply_decision(
                arguments, state, 0.0, decision, False)
        self.assertEqual(state["entries"], 0)
        self.assertIsNone(state["entry_mid"])

    def test_close_failure_marks_recovery_and_halts(self) -> None:
        arguments = SimpleNamespace(
            runtime_dir="/run/hepta-local-ai-paper-agent",
            agent_user="hepta-agent-alpha", strategy_id="strategy-a",
            strategy_sha256="sha256:" + "1" * 64,
            strategy_version="1", max_holding_sec=0,
            fill_timeout_sec=30,
        )
        decision = {
            "action": "BUY", "confidence": 0.9,
            "rationale": "coherent trend",
        }
        quote = {
            "observed_at_ms": agent.now_ms(), "bid": 1.1,
            "ask": 1.2, "mid": 1.15,
        }
        state = agent.empty_state()
        actions: list[str] = []

        def campaign_call(
                _arguments, action, _request_id, _extra=None, **_kwargs):
            actions.append(action)
            if action == "close_cycle":
                raise RuntimeError("close transport failed")
            return {"status": "ok", "state": {
                "active_deadline_at_ms": agent.now_ms() + 20_000,
            }}

        def tool_call(
                _arguments, name, _values=None, _call_id=None, **_kwargs):
            if name == "account.get_summary":
                return {"authoritative": True}
            if name == "risk.preview_order":
                return {
                    "command_id": "command-1",
                    "preview_permit": "sha256:" + "2" * 64,
                }
            raise AssertionError(name)

        with mock.patch.object(agent, "write_agent_json"), \
                mock.patch.object(agent, "campaign", side_effect=campaign_call), \
                mock.patch.object(agent, "tool", side_effect=tool_call), \
                mock.patch.object(agent, "active_orders", return_value=[]), \
                mock.patch.object(
                    agent, "position_snapshot",
                    side_effect=[(0.0, 1, 1), (25_000.0, 2, 2)]), \
                mock.patch.object(agent, "tool_response", return_value={
                    "status": "ok", "order_id": 4, "payload": None,
                }), \
                mock.patch.object(agent, "_settle_order", return_value="FILLED"), \
                self.assertRaisesRegex(
                    agent.RecoveryRequiredError, "campaign close failed"):
            agent.enter(arguments, state, decision, quote)
        self.assertTrue(state["recovery_required"])
        self.assertTrue(state["recovery_halt_confirmed"])
        self.assertEqual(actions, ["open_cycle", "close_cycle", "halt"])

    def test_recovery_state_blocks_new_entry(self) -> None:
        state = agent.empty_state()
        state["recovery_required"] = True
        decision = {
            "action": "BUY", "confidence": 0.99,
            "rationale": "must not execute",
        }
        with mock.patch.object(agent, "fresh_quote") as fresh, \
                mock.patch.object(agent, "active_orders") as orders:
            agent.apply_decision(
                SimpleNamespace(confidence=0.62), state, 0.0,
                decision, False)
        fresh.assert_not_called()
        orders.assert_not_called()

    def test_pending_order_on_restart_forces_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            state = agent.empty_state()
            state["pending_order_id"] = 11
            state["pending_order_since_ms"] = 1234
            path.write_text(json.dumps(state), encoding="utf-8")
            loaded = agent.load_state(path)
        self.assertTrue(loaded["recovery_required"])
        self.assertEqual(loaded["pending_order_id"], 11)
        self.assertIn("pending order survived", loaded["recovery_reason"])

    def test_runtime_binding_exact_match_is_required(self) -> None:
        expected = {
            "campaign_id": "test-campaign",
            "execution_service_epoch": "hexec-v6-" + "1" * 32,
            "execution_service_fencing_generation": 9,
            "tool_gateway_epoch": "htgw-v1-" + "2" * 32,
            "tool_session_token_sha256": "sha256:" + "3" * 64,
        }
        arguments = SimpleNamespace(campaign_id="test-campaign")
        with mock.patch.object(
                agent, "current_runtime_binding", return_value=expected):
            ORIGINAL_REQUIRE_RUNTIME_BINDING(
                arguments, {"runtime_binding": expected})
        with mock.patch.object(
                agent, "current_runtime_binding", return_value={
                    **expected,
                    "execution_service_epoch": "hexec-v6-" + "4" * 32,
                }), self.assertRaises(agent.SafetyStopError) as raised:
            ORIGINAL_REQUIRE_RUNTIME_BINDING(
                arguments, {"runtime_binding": expected})
        self.assertEqual(
            raised.exception.suspension_code, agent.RUNTIME_EPOCH_CHANGED)

    def test_runtime_binding_waits_for_same_identity_during_reconnect(self) -> None:
        expected = {
            "campaign_id": "test-campaign",
            "execution_service_epoch": "hexec-v6-" + "1" * 32,
            "execution_service_fencing_generation": 9,
            "tool_gateway_epoch": "htgw-v1-" + "2" * 32,
            "tool_session_token_sha256": "sha256:" + "3" * 64,
        }
        arguments = SimpleNamespace(campaign_id="test-campaign")
        with mock.patch.object(
                agent, "current_runtime_binding",
                side_effect=[RuntimeError("reconnect pending"), expected]) \
                as binding, mock.patch.object(agent.time, "sleep") as sleep:
            ORIGINAL_REQUIRE_RUNTIME_BINDING(
                arguments, {"runtime_binding": expected})
        self.assertEqual(binding.call_count, 2)
        sleep.assert_called_once()

    def test_runtime_binding_empty_identity_waits_but_nonempty_drift_halts(
            self) -> None:
        expected = {
            "campaign_id": "test-campaign",
            "execution_service_epoch": "hexec-v6-" + "1" * 32,
            "execution_service_fencing_generation": 9,
            "tool_gateway_epoch": "htgw-v1-" + "2" * 32,
            "tool_session_token_sha256": "sha256:" + "3" * 64,
        }
        arguments = SimpleNamespace(campaign_id="test-campaign")
        unavailable = agent.RuntimeBindingUnavailableError(
            "reconnect pending", observed={
                **expected,
                "execution_service_epoch": "",
                "execution_service_fencing_generation": 0,
            }, execution_mode="PAPER")
        with mock.patch.object(
                agent, "current_runtime_binding",
                side_effect=[unavailable, expected]) as binding, \
                mock.patch.object(agent.time, "sleep") as sleep:
            ORIGINAL_REQUIRE_RUNTIME_BINDING(
                arguments, {"runtime_binding": expected})
        self.assertEqual(binding.call_count, 2)
        sleep.assert_called_once()

        drift = agent.RuntimeBindingUnavailableError(
            "reconnect pending", observed={
                **expected,
                "execution_service_epoch": "hexec-v6-" + "4" * 32,
            }, execution_mode="PAPER")
        with mock.patch.object(
                agent, "current_runtime_binding", side_effect=drift), \
                mock.patch.object(agent.time, "sleep") as sleep, \
                self.assertRaises(agent.SafetyStopError) as raised:
            ORIGINAL_REQUIRE_RUNTIME_BINDING(
                arguments, {"runtime_binding": expected})
        self.assertEqual(
            raised.exception.suspension_code, agent.RUNTIME_EPOCH_CHANGED)
        self.assertLessEqual(sleep.call_count, 1)

    def test_agent_binding_grace_exceeds_execution_reconnect_deadline(
            self) -> None:
        self.assertGreater(
            agent.RUNTIME_BINDING_UNAVAILABLE_GRACE_SECONDS, 180.0)

    def test_runtime_binding_persistent_unavailability_fails_closed(self) -> None:
        expected = {
            "campaign_id": "test-campaign",
            "execution_service_epoch": "hexec-v6-" + "1" * 32,
            "execution_service_fencing_generation": 9,
            "tool_gateway_epoch": "htgw-v1-" + "2" * 32,
            "tool_session_token_sha256": "sha256:" + "3" * 64,
        }
        arguments = SimpleNamespace(campaign_id="test-campaign")
        with mock.patch.object(
                agent, "RUNTIME_BINDING_UNAVAILABLE_GRACE_SECONDS", 0.0), \
                mock.patch.object(
                    agent, "current_runtime_binding",
                    side_effect=RuntimeError("still unavailable")), \
                self.assertRaises(agent.SafetyStopError) as raised:
            ORIGINAL_REQUIRE_RUNTIME_BINDING(
                arguments, {"runtime_binding": expected})
        self.assertEqual(
            raised.exception.suspension_code, agent.RUNTIME_EPOCH_CHANGED)
        self.assertIn("bounded reconnect grace", str(raised.exception))

    def test_runtime_drift_relatches_before_market_read(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            state = agent.empty_state()
            state.update({
                "auth_generation_rearmed": "auth-generation-new",
                "auth_profile_sha256_rearmed": TEST_AUTH_PROFILE_SHA256,
                "auth_profile_allowlist_sha256_rearmed":
                    TEST_AUTH_PROFILE_ALLOWLIST_SHA256,
                "runtime_binding": {"campaign_id": "test-campaign"},
            })
            state_path.write_text(json.dumps(state), encoding="utf-8")
            argv = [
                "hepta-local-ai-paper-agent",
                "--campaign-id", "test-campaign",
                "--strategy-id", "test-strategy",
                "--strategy-sha256", "sha256:" + "1" * 64,
                "--auth-profile-id", TEST_AUTH_PROFILE_ID,
                "--auth-profile-allowlist-sha256",
                TEST_AUTH_PROFILE_ALLOWLIST_SHA256,
                "--auth-generation", "auth-generation-new",
                "--state-file", str(state_path),
            ]
            with mock.patch.object(sys, "argv", argv), \
                    mock.patch.object(
                        agent, "require_runtime_binding",
                        side_effect=agent.SafetyStopError(
                            agent.RUNTIME_EPOCH_CHANGED,
                            "RUNTIME_EPOCH_CHANGED: drifted")), \
                    mock.patch.object(
                        agent, "_ensure_recovery_halt", return_value=True), \
                    mock.patch.object(agent, "quote") as quote:
                result = agent.main()
            persisted = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(result, agent.SAFETY_STOP_EXIT_STATUS)
        self.assertTrue(persisted["trading_suspended"])
        self.assertEqual(
            persisted["suspension_code"], agent.RUNTIME_EPOCH_CHANGED)
        quote.assert_not_called()

    def test_persisted_recovery_exits_with_non_restart_status(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            state = agent.empty_state()
            state.update({
                "recovery_required": True,
                "recovery_reason": "RECOVERY_REQUIRED: uncertain order",
                "pending_order_id": 44,
            })
            state_path.write_text(json.dumps(state), encoding="utf-8")
            argv = [
                "hepta-local-ai-paper-agent",
                "--campaign-id", "test-campaign",
                "--strategy-id", "test-strategy",
                "--strategy-sha256", "sha256:" + "1" * 64,
                "--auth-profile-id", TEST_AUTH_PROFILE_ID,
                "--auth-profile-allowlist-sha256", TEST_AUTH_PROFILE_ALLOWLIST_SHA256,
                "--auth-generation", "auth-generation-1",
                "--state-file", str(state_path),
            ]
            with mock.patch.object(sys, "argv", argv), \
                    mock.patch.object(
                        agent, "_ensure_recovery_halt", return_value=True), \
                    mock.patch.object(agent, "quote") as quote:
                result = agent.main()
            persisted = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(result, agent.SAFETY_STOP_EXIT_STATUS)
        self.assertTrue(persisted["trading_suspended"])
        self.assertEqual(
            persisted["suspension_code"], agent.ORDER_STATE_UNCERTAIN)
        self.assertEqual(persisted["incident_pending_order_id"], 44)
        self.assertEqual(
            persisted["auth_generation_at_suspend"], "auth-generation-1")
        quote.assert_not_called()

    def test_rearmed_generation_mismatch_relatches_before_market_read(
            self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            state = agent.empty_state()
            state["auth_generation_rearmed"] = "auth-generation-old"
            state_path.write_text(json.dumps(state), encoding="utf-8")
            argv = [
                "hepta-local-ai-paper-agent",
                "--campaign-id", "test-campaign",
                "--strategy-id", "test-strategy",
                "--strategy-sha256", "sha256:" + "1" * 64,
                "--auth-profile-id", TEST_AUTH_PROFILE_ID,
                "--auth-profile-allowlist-sha256", TEST_AUTH_PROFILE_ALLOWLIST_SHA256,
                "--auth-generation", "auth-generation-new",
                "--state-file", str(state_path),
            ]
            with mock.patch.object(sys, "argv", argv), \
                    mock.patch.object(
                        agent, "_ensure_recovery_halt", return_value=True), \
                    mock.patch.object(agent, "quote") as quote:
                result = agent.main()
            persisted = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(result, agent.SAFETY_STOP_EXIT_STATUS)
        self.assertTrue(persisted["recovery_required"])
        self.assertTrue(persisted["trading_suspended"])
        self.assertEqual(
            persisted["suspension_code"], agent.MODEL_AUTH_UNUSABLE)
        quote.assert_not_called()

    def test_missing_rearmed_generation_relatches_before_market_read(
            self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            state = agent.empty_state()
            state["auth_profile_allowlist_sha256_rearmed"] = TEST_AUTH_PROFILE_ALLOWLIST_SHA256
            state_path.write_text(json.dumps(state), encoding="utf-8")
            argv = [
                "hepta-local-ai-paper-agent",
                "--campaign-id", "test-campaign",
                "--strategy-id", "test-strategy",
                "--strategy-sha256", "sha256:" + "1" * 64,
                "--auth-profile-id", TEST_AUTH_PROFILE_ID,
                "--auth-profile-allowlist-sha256", TEST_AUTH_PROFILE_ALLOWLIST_SHA256,
                "--auth-generation", "auth-generation-new",
                "--state-file", str(state_path),
            ]
            with mock.patch.object(sys, "argv", argv), \
                    mock.patch.object(
                        agent, "_ensure_recovery_halt", return_value=True), \
                    mock.patch.object(agent, "quote") as quote:
                result = agent.main()
            persisted = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(result, agent.SAFETY_STOP_EXIT_STATUS)
        self.assertTrue(persisted["recovery_required"])
        self.assertTrue(persisted["trading_suspended"])
        self.assertEqual(
            persisted["suspension_code"], agent.MODEL_AUTH_UNUSABLE)
        quote.assert_not_called()

    def test_verified_manual_start_permit_accepts_matching_consumed_marker(
            self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            permit_path = Path(directory) / "start-permit.consumed.json"
            runtime_binding = self.manual_start_runtime_binding()
            permit = self.consumed_manual_start_permit(runtime_binding)
            metadata = self.write_manual_start_permit(permit_path, permit)
            arguments = SimpleNamespace(campaign_id="test-campaign")
            state = {"runtime_binding": runtime_binding}
            with mock.patch.object(
                    agent, "START_PERMIT_CONSUMED", permit_path), \
                    mock.patch.object(
                        agent.os, "lstat", return_value=metadata), \
                    mock.patch.dict(
                        agent.os.environ, {"INVOCATION_ID": "a" * 32}), \
                    mock.patch.object(agent, "now_ms", return_value=123_456):
                verified = agent.verified_manual_start_permit(
                    arguments, state)
        self.assertEqual(verified, ("4" * 64, "a" * 32))

    def test_verified_manual_start_permit_rejects_missing_marker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            permit_path = Path(directory) / "start-permit.consumed.json"
            arguments = SimpleNamespace(campaign_id="test-campaign")
            state = {"runtime_binding": self.manual_start_runtime_binding()}
            with mock.patch.object(
                    agent, "START_PERMIT_CONSUMED", permit_path), \
                    mock.patch.dict(
                        agent.os.environ, {"INVOCATION_ID": "a" * 32}), \
                    self.assertRaises(FileNotFoundError):
                agent.verified_manual_start_permit(arguments, state)

    def test_explicit_start_consumes_matching_manual_permit_before_market_read(
            self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path = root / "state.json"
            permit_path = root / "start-permit.consumed.json"
            runtime_binding = self.manual_start_runtime_binding()
            state = agent.empty_state()
            state.update({
                "auth_generation_rearmed": "auth-generation-new",
                "auth_profile_sha256_rearmed": TEST_AUTH_PROFILE_SHA256,
                "auth_profile_allowlist_sha256_rearmed":
                    TEST_AUTH_PROFILE_ALLOWLIST_SHA256,
                "runtime_binding": runtime_binding,
                "manual_start_required": True,
            })
            state_path.write_text(json.dumps(state), encoding="utf-8")
            permit = self.consumed_manual_start_permit(runtime_binding)
            metadata = self.write_manual_start_permit(permit_path, permit)
            argv = [
                "hepta-local-ai-paper-agent",
                "--campaign-id", "test-campaign",
                "--strategy-id", "test-strategy",
                "--strategy-sha256", "sha256:" + "1" * 64,
                "--auth-profile-id", TEST_AUTH_PROFILE_ID,
                "--auth-profile-allowlist-sha256", TEST_AUTH_PROFILE_ALLOWLIST_SHA256,
                "--auth-generation", "auth-generation-new",
                "--state-file", str(state_path),
            ]
            current = {
                "observed_at_ms": 10, "bid": 1.1,
                "ask": 1.2, "mid": 1.15,
            }
            with mock.patch.object(sys, "argv", argv), \
                    mock.patch.object(
                        agent, "START_PERMIT_CONSUMED", permit_path), \
                    mock.patch.object(
                        agent.os, "lstat", return_value=metadata), \
                    mock.patch.dict(
                        agent.os.environ, {"INVOCATION_ID": "a" * 32}), \
                    mock.patch.object(agent, "now_ms", return_value=123456), \
                    mock.patch.object(agent, "quote", return_value=current), \
                    mock.patch.object(
                        agent, "position_quantity", return_value=0.0), \
                    mock.patch.object(
                        agent.time, "sleep", side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt):
                    agent.main()
            persisted = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertFalse(persisted["manual_start_required"])
        self.assertEqual(persisted["manual_started_at_ms"], 123456)
        self.assertEqual(persisted["manual_start_permit_id"], "4" * 64)
        self.assertEqual(persisted["manual_start_invocation_id"], "a" * 32)
        self.assertEqual(
            persisted["last_order_result"],
            "AUTH_REARM_MANUAL_START_CONSUMED")

    def test_missing_manual_start_permit_safely_stops_before_market_read(
            self) -> None:
        self.assert_invalid_manual_start_safely_stops(
            permit=None, invocation_id="a" * 32)

    def test_expired_manual_start_permit_safely_stops_before_market_read(
            self) -> None:
        runtime_binding = self.manual_start_runtime_binding()
        permit = self.consumed_manual_start_permit(
            runtime_binding, issued_at_ms=120_000,
            not_after_ms=123_455)
        self.assert_invalid_manual_start_safely_stops(
            permit=permit, invocation_id="a" * 32)

    def test_wrong_invocation_manual_start_permit_safely_stops_before_market_read(
            self) -> None:
        runtime_binding = self.manual_start_runtime_binding()
        permit = self.consumed_manual_start_permit(
            runtime_binding, invocation_id="b" * 32)
        self.assert_invalid_manual_start_safely_stops(
            permit=permit, invocation_id="a" * 32)

    def test_missing_rearmed_allowlist_hash_relatches_before_market_read(
            self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            state = agent.empty_state()
            state["auth_generation_rearmed"] = "auth-generation-new"
            state_path.write_text(json.dumps(state), encoding="utf-8")
            argv = [
                "hepta-local-ai-paper-agent",
                "--campaign-id", "test-campaign",
                "--strategy-id", "test-strategy",
                "--strategy-sha256", "sha256:" + "1" * 64,
                "--auth-generation", "auth-generation-new",
                "--auth-profile-id", TEST_AUTH_PROFILE_ID,
                "--auth-profile-allowlist-sha256", TEST_AUTH_PROFILE_ALLOWLIST_SHA256,
                "--state-file", str(state_path),
            ]
            with mock.patch.object(sys, "argv", argv), \
                    mock.patch.object(
                        agent, "_ensure_recovery_halt", return_value=True), \
                    mock.patch.object(agent, "quote") as quote:
                result = agent.main()
            persisted = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(result, agent.SAFETY_STOP_EXIT_STATUS)
        self.assertEqual(
            persisted["suspension_code"], agent.MODEL_AUTH_UNUSABLE)
        self.assertTrue(persisted["trading_suspended"])
        quote.assert_not_called()

    def test_mismatched_rearmed_allowlist_hash_relatches_before_market_read(
            self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            state = agent.empty_state()
            state["auth_generation_rearmed"] = "auth-generation-new"
            state["auth_profile_sha256_rearmed"] = TEST_AUTH_PROFILE_SHA256
            state["auth_profile_allowlist_sha256_rearmed"] = (
                agent.auth_profile_allowlist_sha256(
                    ["openai:different-profile"]))
            state_path.write_text(json.dumps(state), encoding="utf-8")
            argv = [
                "hepta-local-ai-paper-agent",
                "--campaign-id", "test-campaign",
                "--strategy-id", "test-strategy",
                "--strategy-sha256", "sha256:" + "1" * 64,
                "--auth-generation", "auth-generation-new",
                "--auth-profile-id", TEST_AUTH_PROFILE_ID,
                "--auth-profile-allowlist-sha256", TEST_AUTH_PROFILE_ALLOWLIST_SHA256,
                "--state-file", str(state_path),
            ]
            with mock.patch.object(sys, "argv", argv), \
                    mock.patch.object(
                        agent, "_ensure_recovery_halt", return_value=True), \
                    mock.patch.object(agent, "quote") as quote:
                result = agent.main()
            persisted = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(result, agent.SAFETY_STOP_EXIT_STATUS)
        self.assertEqual(
            persisted["suspension_code"], agent.MODEL_AUTH_UNUSABLE)
        self.assertTrue(persisted["trading_suspended"])
        quote.assert_not_called()

    def test_effective_auth_allowlist_drift_stops_before_model_request(
            self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            state = agent.empty_state()
            state["auth_generation_rearmed"] = "auth-generation-new"
            state["auth_profile_sha256_rearmed"] = TEST_AUTH_PROFILE_SHA256
            state["auth_profile_allowlist_sha256_rearmed"] = TEST_AUTH_PROFILE_ALLOWLIST_SHA256
            state["history"] = [
                {"observed_at_ms": index, "bid": 1.1,
                 "ask": 1.2, "mid": 1.15}
                for index in range(1, 6)
            ]
            state_path.write_text(json.dumps(state), encoding="utf-8")
            argv = [
                "hepta-local-ai-paper-agent",
                "--campaign-id", "test-campaign",
                "--strategy-id", "test-strategy",
                "--strategy-sha256", "sha256:" + "1" * 64,
                "--auth-generation", "auth-generation-new",
                "--auth-profile-id", TEST_AUTH_PROFILE_ID,
                "--auth-profile-allowlist-sha256", TEST_AUTH_PROFILE_ALLOWLIST_SHA256,
                "--state-file", str(state_path),
            ]
            self.verify_auth_order.side_effect = agent.SafetyStopError(
                agent.MODEL_AUTH_UNUSABLE,
                "MODEL_AUTH_UNUSABLE: effective auth profile allowlist drifted")
            current = {
                "observed_at_ms": 10, "bid": 1.1,
                "ask": 1.2, "mid": 1.15,
            }
            with mock.patch.object(sys, "argv", argv), \
                    mock.patch.object(
                        agent, "quote", return_value=current) as quote, \
                    mock.patch.object(
                        agent, "position_quantity", return_value=0.0), \
                    mock.patch.object(
                        agent, "_ensure_recovery_halt", return_value=True), \
                    mock.patch.object(
                        agent, "_attempt_immediate_auth_safety_flatten",
                        return_value=True) as immediate:
                result = agent.main()
            persisted = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(result, agent.SAFETY_STOP_EXIT_STATUS)
        self.assertEqual(
            persisted["suspension_code"], agent.MODEL_AUTH_UNUSABLE)
        self.assertGreaterEqual(quote.call_count, 1)
        immediate.assert_called_once()

    def test_immediate_auth_safety_flatten_proves_zero_and_stays_latched(
            self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            state = agent.empty_state()
            state.update({
                "recovery_required": True,
                "trading_suspended": True,
                "suspension_code": agent.MODEL_AUTH_RATE_LIMIT,
                "suspension_id": "suspension-immediate-flat",
                "recovery_phase": "REQUESTED",
            })
            arguments = SimpleNamespace(
                state_file=state_path, fill_timeout_sec=30)
            with mock.patch.object(
                    agent, "active_orders", side_effect=[[], []]), \
                    mock.patch.object(
                        agent, "flatten", return_value=0.0) as flatten, \
                    mock.patch.object(
                        agent, "position_snapshot", return_value=(0.0, 11, 21)), \
                    mock.patch.object(
                        agent, "tool", return_value={
                            "gross_absolute_position": 0.0}), \
                    mock.patch.object(agent, "now_ms", return_value=123456):
                self.assertTrue(agent._attempt_immediate_auth_safety_flatten(
                    arguments, state, agent.MODEL_AUTH_RATE_LIMIT))
            persisted = json.loads(state_path.read_text(encoding="ascii"))
        flatten.assert_called_once_with(arguments, state)
        self.assertTrue(persisted["recovery_required"])
        self.assertTrue(persisted["trading_suspended"])
        self.assertFalse(persisted["recovery_complete"])
        self.assertEqual(
            persisted["recovery_phase"], "IMMEDIATE_FLAT_OBSERVED")
        self.assertEqual(persisted["position"], 0.0)
        self.assertEqual(persisted["active_order_ids"], [])
        self.assertEqual(persisted["gross_absolute_position"], 0.0)
        self.assertEqual(persisted["immediate_flat_position_generation"], 11)
        self.assertEqual(persisted["immediate_flat_fx_cash_generation"], 21)

    def test_immediate_auth_safety_flatten_never_crosses_active_order(
            self) -> None:
        state = agent.empty_state()
        state.update({
            "recovery_required": True,
            "trading_suspended": True,
            "suspension_code": agent.MODEL_AUTH_RATE_LIMIT,
        })
        with tempfile.TemporaryDirectory() as directory:
            arguments = SimpleNamespace(
                state_file=Path(directory) / "state.json")
            with mock.patch.object(
                    agent, "active_orders", return_value=[17]), \
                    mock.patch.object(agent, "flatten") as flatten, \
                    self.assertRaisesRegex(
                        agent.RecoveryRequiredError,
                        "active orders requiring cancel/reconcile"):
                agent._attempt_immediate_auth_safety_flatten(
                    arguments, state, agent.MODEL_AUTH_RATE_LIMIT)
        flatten.assert_not_called()

    def test_model_auth_rate_limit_latches_and_terminates_loop(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            state = agent.empty_state()
            state["auth_generation_rearmed"] = "old-auth"
            state["auth_profile_sha256_rearmed"] = TEST_AUTH_PROFILE_SHA256
            state["auth_profile_allowlist_sha256_rearmed"] = TEST_AUTH_PROFILE_ALLOWLIST_SHA256
            state["history"] = [
                {"observed_at_ms": index + 1, "bid": 1.1,
                 "ask": 1.2, "mid": 1.15}
                for index in range(6)
            ]
            state_path.write_text(json.dumps(state), encoding="utf-8")
            argv = [
                "hepta-local-ai-paper-agent",
                "--campaign-id", "test-campaign",
                "--strategy-id", "test-strategy",
                "--strategy-sha256", "sha256:" + "1" * 64,
                "--auth-profile-id", TEST_AUTH_PROFILE_ID,
                "--auth-profile-allowlist-sha256", TEST_AUTH_PROFILE_ALLOWLIST_SHA256,
                "--auth-generation", "old-auth",
                "--state-file", str(state_path),
            ]
            current = {
                "observed_at_ms": 10, "bid": 1.1,
                "ask": 1.2, "mid": 1.15,
            }
            with mock.patch.object(sys, "argv", argv), \
                    mock.patch.object(agent, "quote", return_value=current), \
                    mock.patch.object(
                        agent, "position_quantity", return_value=0.0), \
                    mock.patch.object(
                        agent, "model_decision",
                        side_effect=agent.SafetyStopError(
                            agent.MODEL_AUTH_RATE_LIMIT,
                            "MODEL_AUTH_RATE_LIMIT: provider rejected auth")), \
                    mock.patch.object(
                        agent, "_ensure_recovery_halt", return_value=True), \
                    mock.patch.object(
                        agent, "_attempt_immediate_auth_safety_flatten",
                        return_value=True) as immediate, \
                    mock.patch.object(agent.time, "sleep") as sleep:
                result = agent.main()
            persisted = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(result, agent.SAFETY_STOP_EXIT_STATUS)
        self.assertTrue(persisted["trading_suspended"])
        self.assertEqual(
            persisted["suspension_code"], agent.MODEL_AUTH_RATE_LIMIT)
        self.assertFalse(persisted["recovery_complete"])
        immediate.assert_called_once_with(
            mock.ANY, mock.ANY, agent.MODEL_AUTH_RATE_LIMIT)
        self.assertEqual(sleep.call_args_list, [mock.call(10)])

    def test_model_request_rate_limit_holds_without_latching(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            state = agent.empty_state()
            state["auth_generation_rearmed"] = "old-auth"
            state["auth_profile_sha256_rearmed"] = TEST_AUTH_PROFILE_SHA256
            state["auth_profile_allowlist_sha256_rearmed"] = TEST_AUTH_PROFILE_ALLOWLIST_SHA256
            state["history"] = [
                {"observed_at_ms": index + 1, "bid": 1.1,
                 "ask": 1.2, "mid": 1.15}
                for index in range(6)
            ]
            state_path.write_text(json.dumps(state), encoding="utf-8")
            argv = [
                "hepta-local-ai-paper-agent",
                "--campaign-id", "test-campaign",
                "--strategy-id", "test-strategy",
                "--strategy-sha256", "sha256:" + "1" * 64,
                "--auth-profile-id", TEST_AUTH_PROFILE_ID,
                "--auth-profile-allowlist-sha256", TEST_AUTH_PROFILE_ALLOWLIST_SHA256,
                "--auth-generation", "old-auth",
                "--state-file", str(state_path),
            ]
            current = {
                "observed_at_ms": 10, "bid": 1.1,
                "ask": 1.2, "mid": 1.15,
            }
            with mock.patch.object(sys, "argv", argv), \
                    mock.patch.object(agent, "quote", return_value=current), \
                    mock.patch.object(
                        agent, "position_quantity", return_value=0.0), \
                    mock.patch.object(
                        agent, "model_decision",
                        side_effect=agent.ModelRequestRateLimitError(
                            "MODEL_REQUEST_RATE_LIMIT: transient")), \
                    mock.patch.object(
                        agent, "_ensure_recovery_halt") as halt, \
                    mock.patch.object(
                        agent.time, "sleep",
                        side_effect=[None, KeyboardInterrupt]):
                with self.assertRaises(KeyboardInterrupt):
                    agent.main()
            persisted = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertFalse(persisted["trading_suspended"])
        self.assertFalse(persisted["recovery_required"])
        self.assertEqual(persisted["model_request_rate_limit_count"], 1)
        self.assertIsInstance(
            persisted["last_model_request_rate_limit_at_ms"], int)
        halt.assert_not_called()

    def test_persisted_model_request_limit_blocks_restart_retry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            state = agent.empty_state()
            state["auth_generation_rearmed"] = "old-auth"
            state["auth_profile_sha256_rearmed"] = TEST_AUTH_PROFILE_SHA256
            state["auth_profile_allowlist_sha256_rearmed"] = TEST_AUTH_PROFILE_ALLOWLIST_SHA256
            state["history"] = [
                {"observed_at_ms": index + 1, "bid": 1.1,
                 "ask": 1.2, "mid": 1.15}
                for index in range(6)
            ]
            state["last_model_request_rate_limit_at_ms"] = 100_000
            state_path.write_text(json.dumps(state), encoding="utf-8")
            argv = [
                "hepta-local-ai-paper-agent",
                "--campaign-id", "test-campaign",
                "--strategy-id", "test-strategy",
                "--strategy-sha256", "sha256:" + "1" * 64,
                "--auth-profile-id", TEST_AUTH_PROFILE_ID,
                "--auth-profile-allowlist-sha256", TEST_AUTH_PROFILE_ALLOWLIST_SHA256,
                "--auth-generation", "old-auth",
                "--state-file", str(state_path),
                "--decision-sec", "120",
            ]
            current = {
                "observed_at_ms": 10, "bid": 1.1,
                "ask": 1.2, "mid": 1.15,
            }
            with mock.patch.object(sys, "argv", argv), \
                    mock.patch.object(agent, "now_ms", return_value=100_001), \
                    mock.patch.object(agent, "quote", return_value=current), \
                    mock.patch.object(
                        agent, "position_quantity", return_value=0.0), \
                    mock.patch.object(agent, "model_decision") as model, \
                    mock.patch.object(
                        agent.time, "sleep", side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt):
                    agent.main()
        model.assert_not_called()

    def test_model_request_limit_backoff_expires_after_interval(self) -> None:
        state = agent.empty_state()
        state["last_model_request_rate_limit_at_ms"] = 100_000
        self.assertTrue(agent._model_request_backoff_active(
            state, 120, current_ms=219_999))
        self.assertFalse(agent._model_request_backoff_active(
            state, 120, current_ms=220_000))

    def test_completed_attempt_uses_completion_based_no_catch_up(self) -> None:
        state = agent.empty_state()
        arguments = SimpleNamespace(state_file=None)
        state.update({
            "model_consecutive_failures": 3,
            "last_model_failure_code": agent.MODEL_ATTEMPT_TIMEOUT,
            "last_model_failure_at_ms": 1,
        })
        with mock.patch.object(agent, "now_ms", return_value=500_000):
            agent._complete_model_attempt(arguments, state, 120, None)
        self.assertEqual(state["next_model_attempt_after_ms"], 620_000)
        self.assertEqual(state["model_consecutive_failures"], 0)
        self.assertEqual(state["last_model_failure_code"],
                         agent.MODEL_ATTEMPT_TIMEOUT)
        self.assertEqual(state["last_model_failure_at_ms"], 1)
        self.assertTrue(agent._model_request_backoff_active(
            state, 120, current_ms=619_999))
        self.assertTrue(state["model_attempt_count"] == 0)

    def test_typed_timeout_and_contract_failures_persist_counters(self) -> None:
        state = agent.empty_state()
        arguments = SimpleNamespace(state_file=None)
        with mock.patch.object(agent, "now_ms", side_effect=[10_000, 20_000]):
            agent._complete_model_attempt(
                arguments, state, 120,
                agent.ModelAttemptFailure(
                    agent.MODEL_ATTEMPT_TIMEOUT, "timed out"))
            agent._complete_model_attempt(
                arguments, state, 120,
                agent.ModelAttemptFailure(
                    agent.MODEL_ATTEMPT_CONTRACT_INVALID, "bad contract"))
        self.assertEqual(state["model_timeout_count"], 1)
        self.assertEqual(state["model_contract_failure_count"], 1)
        self.assertEqual(state["model_consecutive_failures"], 2)
        self.assertEqual(
            state["last_model_failure_code"],
            agent.MODEL_ATTEMPT_CONTRACT_INVALID)
        self.assertEqual(state["next_model_attempt_after_ms"], 140_000)

    def test_restart_preserves_backoff_and_failure_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            state = agent.empty_state()
            state.update({
                "model_attempt_count": 7,
                "model_timeout_count": 2,
                "model_contract_failure_count": 3,
                "model_consecutive_failures": 4,
                "last_model_failure_at_ms": 123_000,
                "last_model_failure_code":
                    agent.MODEL_ATTEMPT_CONTRACT_INVALID,
                "next_model_attempt_after_ms": 243_000,
            })
            path.write_text(json.dumps(state), encoding="utf-8")
            loaded = agent.load_state(path)
        for key in (
                "model_attempt_count", "model_timeout_count",
                "model_contract_failure_count", "model_consecutive_failures",
                "last_model_failure_at_ms", "last_model_failure_code",
                "next_model_attempt_after_ms"):
            self.assertEqual(loaded[key], state[key])

    def test_restart_with_inflight_attempt_latches_terminal_uncertain(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            state = agent.empty_state()
            state["model_attempt_in_flight"] = True
            state["model_attempt_started_at_ms"] = 123_000
            path.write_text(json.dumps(state), encoding="utf-8")
            loaded = agent.load_state(path)
        self.assertTrue(loaded["trading_suspended"])
        self.assertTrue(loaded["recovery_required"])
        self.assertEqual(
            loaded["suspension_code"],
            agent.MODEL_ATTEMPT_TERMINAL_UNCERTAIN)

    def test_restart_after_terminal_uncertain_completion_stays_latched(
            self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            state = agent.empty_state()
            state.update({
                "model_attempt_in_flight": False,
                "model_timeout_count": 1,
                "model_consecutive_failures": 1,
                "last_model_failure_at_ms": 123_000,
                "last_model_failure_code":
                    agent.MODEL_ATTEMPT_TERMINAL_UNCERTAIN,
                "next_model_attempt_after_ms": 243_000,
            })
            path.write_text(json.dumps(state), encoding="utf-8")
            loaded = agent.load_state(path)
        self.assertTrue(loaded["trading_suspended"])
        self.assertTrue(loaded["recovery_required"])
        self.assertEqual(
            loaded["suspension_code"],
            agent.MODEL_ATTEMPT_TERMINAL_UNCERTAIN)
        self.assertEqual(loaded["model_timeout_count"], 1)
        self.assertEqual(loaded["model_consecutive_failures"], 1)
        self.assertEqual(loaded["last_model_failure_at_ms"], 123_000)
        self.assertEqual(
            loaded["last_model_failure_code"],
            agent.MODEL_ATTEMPT_TERMINAL_UNCERTAIN)
        self.assertEqual(loaded["next_model_attempt_after_ms"], 243_000)

    def test_model_worker_keeps_main_thread_available(self) -> None:
        release = __import__("threading").Event()
        started = __import__("threading").Event()
        arguments = SimpleNamespace()
        history = [{"observed_at_ms": 1, "mid": 1.15}]

        def delayed(*_arguments: object) -> dict[str, object]:
            started.set()
            release.wait(2)
            return {"action": "HOLD", "confidence": 1.0,
                    "rationale": "done"}

        with mock.patch.object(agent, "model_decision", side_effect=delayed):
            worker = agent.ModelAttemptWorker(
                arguments, history, 0.0, {
                    "realized_pnl_estimate": 0.0,
                    "unrealized_pnl_estimate": 0.0})
            self.assertTrue(started.wait(1))
            self.assertFalse(worker.done())
            release.set()
            self.assertEqual(worker.result()["action"], "HOLD")

    def test_outer_timeout_is_terminal_uncertain_even_after_local_reap(
            self) -> None:
        process = mock.Mock()
        process.pid = 12345
        process.communicate.side_effect = agent.subprocess.TimeoutExpired(
            ["openclaw", "agent"], agent.MODEL_ATTEMPT_TIMEOUT_SEC)
        process.poll.return_value = -15
        with mock.patch.object(agent.subprocess, "Popen", return_value=process), \
                mock.patch.object(
                    agent, "_terminate_model_process_group",
                    return_value=True) as terminate, \
                self.assertRaises(
                    agent.ModelAttemptTerminalUncertainError) as raised:
            agent._run_model_command(["openclaw", "agent"])
        terminate.assert_called_once_with(process)
        self.assertEqual(
            raised.exception.suspension_code,
            agent.MODEL_ATTEMPT_TERMINAL_UNCERTAIN)

    def test_outer_timeout_becomes_typed_timeout_after_exact_terminal_proof(
            self) -> None:
        process = mock.Mock()
        process.pid = 12345
        process.communicate.side_effect = agent.subprocess.TimeoutExpired(
            ["openclaw", "agent"], agent.MODEL_ATTEMPT_TIMEOUT_SEC)
        process.poll.return_value = -15
        arguments = SimpleNamespace(
            model_user="qian-qi",
            openclaw_agent="telegram-bot-8681289317")
        with mock.patch.object(agent.subprocess, "Popen", return_value=process), \
                mock.patch.object(
                    agent, "_terminate_model_process_group",
                    return_value=True), \
                mock.patch.object(
                    agent, "_wait_for_model_session_terminal",
                    return_value=True) as terminal, \
                self.assertRaises(agent.ModelAttemptFailure) as raised:
            agent._run_model_command(
                ["openclaw", "agent"], arguments=arguments,
                decision_session_key="decision-unique",
                attempt_started_at_ms=100_000)
        self.assertEqual(
            raised.exception.failure_code, agent.MODEL_ATTEMPT_TIMEOUT)
        terminal.assert_called_once_with(
            arguments, "decision-unique", 100_000)

    def test_model_session_terminal_requires_exact_unique_done_session(
            self) -> None:
        arguments = SimpleNamespace(
            model_user="qian-qi",
            openclaw_agent="telegram-bot-8681289317")
        expected_key = (
            "agent:telegram-bot-8681289317:decision-unique")
        terminal = {
            "key": expected_key,
            "status": "done",
            "sessionStartedAt": 101_000,
            "lastInteractionAt": 102_000,
        }
        completed = SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"sessions": [terminal]}), stderr="")
        with mock.patch.object(
                agent.subprocess, "run", return_value=completed) as run:
            self.assertTrue(agent._model_session_terminal(
                arguments, "decision-unique", 100_000))
        self.assertIn("--limit", run.call_args.args[0])
        self.assertIn("100", run.call_args.args[0])
        self.assertIn("--active", run.call_args.args[0])

        fully_qualified = (
            "agent:telegram-bot-8681289317:decision-unique")
        with mock.patch.object(
                agent.subprocess, "run", return_value=completed):
            self.assertTrue(agent._model_session_terminal(
                arguments, fully_qualified, 100_000))

        for changed in (
                {**terminal, "status": "running"},
                {**terminal, "key": expected_key + "-other"},
                {**terminal, "sessionStartedAt": 90_000},
                {**terminal, "lastInteractionAt": 99_999}):
            with self.subTest(changed=changed), mock.patch.object(
                    agent.subprocess, "run", return_value=SimpleNamespace(
                        returncode=0,
                        stdout=json.dumps({"sessions": [changed]}),
                        stderr="")):
                self.assertFalse(agent._model_session_terminal(
                    arguments, "decision-unique", 100_000))

    def test_model_session_terminal_wait_caps_probe_to_remaining_budget(
            self) -> None:
        arguments = SimpleNamespace(
            model_user="qian-qi",
            openclaw_agent="telegram-bot-8681289317")
        with mock.patch.object(
                agent, "MODEL_ATTEMPT_TERMINAL_PROOF_TIMEOUT_SEC", 3.0), \
                mock.patch.object(
                    agent.time, "monotonic",
                    side_effect=[100.0, 101.0, 103.0]), \
                mock.patch.object(
                    agent, "_model_session_terminal",
                    return_value=False) as terminal, \
                mock.patch.object(agent.time, "sleep") as sleep:
            self.assertFalse(agent._wait_for_model_session_terminal(
                arguments, "decision-unique", 100_000))
        terminal.assert_called_once_with(
            arguments, "decision-unique", 100_000, 2.0)
        sleep.assert_not_called()

    def test_terminal_uncertain_counts_timeout_and_persists_code(self) -> None:
        state = agent.empty_state()
        failure = agent.ModelAttemptTerminalUncertainError(
            "Gateway terminal status is unproven")
        with mock.patch.object(agent, "now_ms", return_value=100_000):
            agent._complete_model_attempt(
                SimpleNamespace(state_file=None), state, 120, failure)
        self.assertEqual(state["model_timeout_count"], 1)
        self.assertEqual(
            state["last_model_failure_code"],
            agent.MODEL_ATTEMPT_TERMINAL_UNCERTAIN)

    def test_stale_model_result_is_typed_contract_failure(self) -> None:
        state = agent.empty_state()
        state["model_attempt_in_flight"] = True
        state["model_attempt_started_at_ms"] = 1
        failure = agent.ModelAttemptFailure(
            agent.MODEL_ATTEMPT_CONTRACT_INVALID,
            "decision market snapshot expired")
        with mock.patch.object(agent, "now_ms", return_value=100_000):
            agent._complete_model_attempt(
                SimpleNamespace(state_file=None), state, 120, failure)
        self.assertEqual(state["model_contract_failure_count"], 1)
        self.assertEqual(
            state["last_model_failure_code"],
            agent.MODEL_ATTEMPT_CONTRACT_INVALID)

    def test_position_drift_result_is_typed_contract_failure(self) -> None:
        state = agent.empty_state()
        failure = agent.ModelAttemptFailure(
            agent.MODEL_ATTEMPT_CONTRACT_INVALID,
            "authoritative position changed while decision was in flight")
        with mock.patch.object(agent, "now_ms", return_value=200_000):
            agent._complete_model_attempt(
                SimpleNamespace(state_file=None), state, 120, failure)
        self.assertEqual(state["model_contract_failure_count"], 1)
        self.assertEqual(state["next_model_attempt_after_ms"], 320_000)

    def test_delayed_worker_does_not_block_risk_sample(self) -> None:
        release = __import__("threading").Event()
        history = [{"observed_at_ms": 10, "mid": 1.15}]

        def delayed(*_arguments: object) -> dict[str, object]:
            release.wait(2)
            return {"action": "HOLD", "confidence": 1.0,
                    "rationale": "done"}

        with mock.patch.object(agent, "model_decision", side_effect=delayed), \
                mock.patch.object(
                    agent, "position_quantity", return_value=25_000.0) as pos:
            worker = agent.ModelAttemptWorker(
                SimpleNamespace(), history, 25_000.0,
                {"realized_pnl_estimate": 0.0,
                 "unrealized_pnl_estimate": 0.0})
            self.assertEqual(agent.position_quantity(SimpleNamespace()), 25_000.0)
            pos.assert_called_once()
            self.assertFalse(worker.done())
            release.set()
            worker.result()

    def test_safety_latch_persist_failure_still_exits_without_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            state = agent.empty_state()
            state["auth_generation_rearmed"] = "old-auth"
            state["auth_profile_sha256_rearmed"] = TEST_AUTH_PROFILE_SHA256
            state["auth_profile_allowlist_sha256_rearmed"] = TEST_AUTH_PROFILE_ALLOWLIST_SHA256
            state["history"] = [
                {"observed_at_ms": index + 1, "bid": 1.1,
                 "ask": 1.2, "mid": 1.15}
                for index in range(6)
            ]
            state_path.write_text(json.dumps(state), encoding="utf-8")
            argv = [
                "hepta-local-ai-paper-agent",
                "--campaign-id", "test-campaign",
                "--strategy-id", "test-strategy",
                "--strategy-sha256", "sha256:" + "1" * 64,
                "--auth-profile-id", TEST_AUTH_PROFILE_ID,
                "--auth-profile-allowlist-sha256", TEST_AUTH_PROFILE_ALLOWLIST_SHA256,
                "--auth-generation", "old-auth",
                "--state-file", str(state_path),
            ]
            current = {
                "observed_at_ms": 10, "bid": 1.1,
                "ask": 1.2, "mid": 1.15,
            }
            with mock.patch.object(sys, "argv", argv), \
                    mock.patch.object(agent, "quote", return_value=current), \
                    mock.patch.object(
                        agent, "position_quantity", return_value=0.0), \
                    mock.patch.object(
                        agent, "model_decision",
                        side_effect=agent.SafetyStopError(
                            agent.MODEL_AUTH_RATE_LIMIT, "rate limited")), \
                    mock.patch.object(
                        agent, "_mark_trading_suspended",
                        side_effect=OSError("disk full")), \
                    mock.patch.object(agent.time, "sleep") as sleep:
                result = agent.main()
        self.assertEqual(result, agent.SAFETY_STOP_EXIT_STATUS)
        self.assertLessEqual(sleep.call_count, 1)

    def test_main_always_applies_sampling_delay(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            state = agent.empty_state()
            state["auth_generation_rearmed"] = "unversioned"
            state["auth_profile_sha256_rearmed"] = TEST_AUTH_PROFILE_SHA256
            state["auth_profile_allowlist_sha256_rearmed"] = TEST_AUTH_PROFILE_ALLOWLIST_SHA256
            state_path.write_text(json.dumps(state), encoding="utf-8")
            argv = [
                "hepta-local-ai-paper-agent",
                "--campaign-id", "test-campaign",
                "--strategy-id", "test-strategy",
                "--strategy-sha256", "sha256:" + "1" * 64,
                "--auth-profile-id", TEST_AUTH_PROFILE_ID,
                "--auth-profile-allowlist-sha256", TEST_AUTH_PROFILE_ALLOWLIST_SHA256,
                "--state-file", str(state_path),
            ]
            current = {
                "observed_at_ms": 1000, "bid": 1.1,
                "ask": 1.2, "mid": 1.15,
            }
            with mock.patch.object(sys, "argv", argv), \
                    mock.patch.object(agent, "quote", return_value=current), \
                    mock.patch.object(
                        agent, "position_quantity", side_effect=KeyboardInterrupt), \
                    mock.patch.object(agent.time, "sleep") as sleep:
                with self.assertRaises(KeyboardInterrupt):
                    agent.main()
            sleep.assert_not_called()

    def test_main_runs_explicit_timeout_exit_before_model(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            state = agent.empty_state()
            state.update({
                "auth_generation_rearmed": "unversioned",
                "auth_profile_sha256_rearmed": TEST_AUTH_PROFILE_SHA256,
                "auth_profile_allowlist_sha256_rearmed":
                    TEST_AUTH_PROFILE_ALLOWLIST_SHA256,
                "entry_mid": 1.2,
                "entry_quantity": 25_000.0,
                "entry_at_ms": 1,
            })
            state_path.write_text(json.dumps(state), encoding="utf-8")
            argv = [
                "hepta-local-ai-paper-agent",
                "--campaign-id", "test-campaign",
                "--strategy-id", "test-strategy",
                "--strategy-sha256", "sha256:" + "1" * 64,
                "--auth-profile-id", TEST_AUTH_PROFILE_ID,
                "--auth-profile-allowlist-sha256", TEST_AUTH_PROFILE_ALLOWLIST_SHA256,
                "--state-file", str(state_path),
                "--max-holding-sec", "1",
            ]
            current = {
                "observed_at_ms": agent.now_ms(), "bid": 1.1999,
                "ask": 1.2, "mid": 1.19995,
            }
            with mock.patch.object(sys, "argv", argv), \
                    mock.patch.object(agent, "quote", return_value=current), \
                    mock.patch.object(
                        agent, "position_quantity", return_value=25_000.0), \
                    mock.patch.object(
                        agent, "_close_strategy_position",
                        side_effect=KeyboardInterrupt) as close, \
                    mock.patch.object(agent, "model_decision") as model, \
                    mock.patch.object(agent.time, "sleep"):
                with self.assertRaises(KeyboardInterrupt):
                    agent.main()
            close.assert_called_once_with(
                mock.ANY, mock.ANY, 25_000.0, "MAX_HOLDING_TIMEOUT")
            model.assert_not_called()

    def test_main_default_does_not_time_exit_old_position(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            state = agent.empty_state()
            state.update({
                "auth_generation_rearmed": "unversioned",
                "auth_profile_sha256_rearmed": TEST_AUTH_PROFILE_SHA256,
                "auth_profile_allowlist_sha256_rearmed":
                    TEST_AUTH_PROFILE_ALLOWLIST_SHA256,
                "entry_mid": 1.2,
                "entry_quantity": 25_000.0,
                "entry_at_ms": 1,
            })
            state_path.write_text(json.dumps(state), encoding="utf-8")
            argv = [
                "hepta-local-ai-paper-agent",
                "--campaign-id", "test-campaign",
                "--strategy-id", "test-strategy",
                "--strategy-sha256", "sha256:" + "1" * 64,
                "--auth-profile-id", TEST_AUTH_PROFILE_ID,
                "--auth-profile-allowlist-sha256", TEST_AUTH_PROFILE_ALLOWLIST_SHA256,
                "--state-file", str(state_path),
            ]
            current = {
                "observed_at_ms": agent.now_ms(), "bid": 1.1999,
                "ask": 1.2, "mid": 1.19995,
            }
            history = [
                {**current, "observed_at_ms": current["observed_at_ms"] - i}
                for i in range(6)
            ]
            with mock.patch.object(sys, "argv", argv), \
                    mock.patch.object(agent, "quote", return_value=current), \
                    mock.patch.object(
                        agent, "append_quote_sample", return_value=history), \
                    mock.patch.object(
                        agent, "position_quantity", return_value=25_000.0), \
                    mock.patch.object(agent, "_close_strategy_position") as close, \
                    mock.patch.object(
                        agent, "model_decision",
                        side_effect=KeyboardInterrupt) as model, \
                    mock.patch.object(agent.time, "sleep"):
                with self.assertRaises(KeyboardInterrupt):
                    agent.main()
            model.assert_called_once()
            close.assert_not_called()

    def test_main_adverse_move_exit_remains_before_model(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            state = agent.empty_state()
            state.update({
                "auth_generation_rearmed": "unversioned",
                "auth_profile_sha256_rearmed": TEST_AUTH_PROFILE_SHA256,
                "auth_profile_allowlist_sha256_rearmed":
                    TEST_AUTH_PROFILE_ALLOWLIST_SHA256,
                "entry_mid": 1.2,
                "entry_quantity": 25_000.0,
                "entry_at_ms": 1,
            })
            state_path.write_text(json.dumps(state), encoding="utf-8")
            argv = [
                "hepta-local-ai-paper-agent",
                "--campaign-id", "test-campaign",
                "--strategy-id", "test-strategy",
                "--strategy-sha256", "sha256:" + "1" * 64,
                "--auth-profile-id", TEST_AUTH_PROFILE_ID,
                "--auth-profile-allowlist-sha256", TEST_AUTH_PROFILE_ALLOWLIST_SHA256,
                "--state-file", str(state_path),
            ]
            current = {
                "observed_at_ms": agent.now_ms(), "bid": 1.1979,
                "ask": 1.198, "mid": 1.19795,
            }
            with mock.patch.object(sys, "argv", argv), \
                    mock.patch.object(agent, "quote", return_value=current), \
                    mock.patch.object(
                        agent, "position_quantity", return_value=25_000.0), \
                    mock.patch.object(
                        agent, "_close_strategy_position",
                        side_effect=KeyboardInterrupt) as close, \
                    mock.patch.object(agent, "model_decision") as model, \
                    mock.patch.object(agent.time, "sleep"):
                with self.assertRaises(KeyboardInterrupt):
                    agent.main()
            close.assert_called_once_with(
                mock.ANY, mock.ANY, 25_000.0, "MAX_ADVERSE_MOVE")
            model.assert_not_called()

    def test_broker_fill_ledger_deduplicates_reconnect_execution(self) -> None:
        state = agent.empty_state()
        entry = {
            "broker_execution_id": "0000e215.entry.01.01",
            "account": "DU12345", "execution_domain": "alpha",
            "instrument": "EUR.USD", "order_id": 95, "side": "SELL",
            "filled_quantity": 25_000.0, "average_fill_price": 1.152010,
        }
        close = {
            "broker_execution_id": "0000e215.close.01.01",
            "account": "DU12345", "execution_domain": "alpha",
            "instrument": "EUR.USD", "order_id": 96, "side": "BUY",
            "filled_quantity": 25_000.0, "average_fill_price": 1.152050,
        }
        agent.record_broker_entry(state, entry, -25_000.0)
        self.assertTrue(agent.record_broker_close(
            state, close, recovery=True))
        self.assertFalse(agent.record_broker_close(
            state, close, entry_fill=entry))
        self.assertEqual(state["realized_gross_pnl"], -1.0)
        self.assertEqual(state["closed_trades"], 1)
        self.assertEqual(state["broker_fill_exits"], 1)
        self.assertEqual(state["recovery_broker_fill_exits"], 1)
        self.assertEqual(state["recovery_closed_trades"], 1)
        self.assertEqual(state["exits"], 0)
        self.assertFalse(state["fees_known"])
        self.assertIsNone(state["realized_fees"])
        self.assertIsNone(state["realized_net_pnl"])

    def test_broker_execution_identity_conflict_fails_closed(self) -> None:
        state = agent.empty_state()
        fill = {
            "broker_execution_id": "execution-95", "account": "DU12345",
            "execution_domain": "alpha", "instrument": "EUR.USD",
            "order_id": 95, "side": "SELL",
            "filled_quantity": 25_000.0, "average_fill_price": 1.152010,
        }
        self.assertTrue(agent._record_broker_fill(state, fill))
        with self.assertRaisesRegex(
                agent.RecoveryRequiredError, "replay conflicted"):
            agent._record_broker_fill(
                state, {**fill, "average_fill_price": 1.152020})


class LocalAiPaperAuthOrderTests(unittest.TestCase):
    def arguments(self) -> SimpleNamespace:
        return SimpleNamespace(
            model_user="qian-qi",
            openclaw_agent="telegram-bot-8681289317",
            auth_profile_allowlist_sha256=TEST_AUTH_PROFILE_ALLOWLIST_SHA256,
        )

    def completed(
            self, order: object = TEST_AUTH_PROFILE_ALLOWLIST,
            agent_id: str = "telegram-bot-8681289317",
            provider: str = "openai") -> SimpleNamespace:
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({
                "agentId": agent_id,
                "provider": provider,
                "order": order,
            }),
            stderr="",
        )

    def assert_auth_safety_stop(self, completed: SimpleNamespace) -> None:
        with mock.patch.object(
                agent.subprocess, "run", return_value=completed), \
                self.assertRaises(agent.SafetyStopError) as raised:
            agent._verify_effective_auth_profile(self.arguments())
        self.assertEqual(
            raised.exception.suspension_code, agent.MODEL_AUTH_UNUSABLE)

    def test_auth_profile_allowlist_digest_is_order_independent(self) -> None:
        self.assertEqual(
            agent.auth_profile_allowlist_sha256(TEST_AUTH_PROFILE_ALLOWLIST),
            agent.auth_profile_allowlist_sha256(
                list(reversed(TEST_AUTH_PROFILE_ALLOWLIST))))

    def test_effective_auth_order_accepts_bound_allowlist(self) -> None:
        completed = SimpleNamespace(
            returncode=0,
            stdout=json.dumps({
                "agentId": "telegram-bot-8681289317",
                "provider": "openai",
                "order": [TEST_AUTH_PROFILE_ID, "openai:other-profile"],
            }),
            stderr="",
        )
        with mock.patch.object(
                agent.subprocess, "run", return_value=completed) as run:
            agent._verify_effective_auth_profile(self.arguments())
        command = run.call_args.args[0]
        self.assertIn("order", command)
        self.assertIn("get", command)

    def test_effective_auth_order_accepts_same_allowlist_reordered(self) -> None:
        with mock.patch.object(
                agent.subprocess, "run",
                return_value=self.completed(
                    list(reversed(TEST_AUTH_PROFILE_ALLOWLIST)))):
            agent._verify_effective_auth_profile(self.arguments())

    def test_effective_auth_order_rejects_allowlist_membership_drift(
            self) -> None:
        changed_orders = [
            [TEST_AUTH_PROFILE_ID],
            [*TEST_AUTH_PROFILE_ALLOWLIST, "openai:unexpected-profile"],
        ]
        for order in changed_orders:
            with self.subTest(order=order):
                self.assert_auth_safety_stop(self.completed(order))

    def test_effective_auth_order_rejects_duplicate_profile(self) -> None:
        self.assert_auth_safety_stop(self.completed([
            TEST_AUTH_PROFILE_ID,
            TEST_AUTH_PROFILE_ID,
        ]))

    def test_effective_auth_order_rejects_wrong_agent_or_provider(self) -> None:
        responses = [
            self.completed(agent_id="wrong-agent"),
            self.completed(provider="anthropic"),
        ]
        for completed in responses:
            with self.subTest(stdout=completed.stdout):
                self.assert_auth_safety_stop(completed)

    def test_effective_auth_order_rejects_invalid_response(self) -> None:
        responses = [
            SimpleNamespace(returncode=0, stdout="not-json", stderr=""),
            SimpleNamespace(returncode=0, stdout="[]", stderr=""),
            self.completed([]),
            self.completed("not-a-list"),
            self.completed(["invalid profile id"]),
        ]
        for completed in responses:
            with self.subTest(stdout=completed.stdout):
                self.assert_auth_safety_stop(completed)

    def test_effective_auth_order_timeout_is_transient(self) -> None:
        with mock.patch.object(
                agent.subprocess, "run",
                side_effect=agent.subprocess.TimeoutExpired("openclaw", 60)):
            with self.assertRaisesRegex(
                    RuntimeError, "temporarily unavailable") as raised:
                agent._verify_effective_auth_profile(self.arguments())
        self.assertNotIsInstance(raised.exception, agent.SafetyStopError)

    def test_effective_auth_order_command_failure_is_transient(self) -> None:
        completed = SimpleNamespace(returncode=1, stdout="", stderr="busy")
        with mock.patch.object(
                agent.subprocess, "run", return_value=completed), \
                self.assertRaisesRegex(
                    RuntimeError, "temporarily unavailable") as raised:
            agent._verify_effective_auth_profile(self.arguments())
        self.assertNotIsInstance(raised.exception, agent.SafetyStopError)


if __name__ == "__main__":
    unittest.main(verbosity=2)
