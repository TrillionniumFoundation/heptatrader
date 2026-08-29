#!/usr/bin/env python3

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import fcntl
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts/hepta_broker_egress_policy.py"
SPEC = importlib.util.spec_from_file_location(
    "hepta_broker_egress_policy_under_test", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("cannot import broker egress policy")
POLICY = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = POLICY
SPEC.loader.exec_module(POLICY)
ROOTFUL_MODULE_PATH = (
    ROOT / "tests/agent_os_rootful_systemd/"
    "hepta_broker_network_rootful_probe.py")
ROOTFUL_SPEC = importlib.util.spec_from_file_location(
    "hepta_broker_network_rootful_probe_under_test", ROOTFUL_MODULE_PATH)
if ROOTFUL_SPEC is None or ROOTFUL_SPEC.loader is None:
    raise RuntimeError("cannot import broker network rootful probe")
ROOTFUL = importlib.util.module_from_spec(ROOTFUL_SPEC)
sys.modules[ROOTFUL_SPEC.name] = ROOTFUL
ROOTFUL_SPEC.loader.exec_module(ROOTFUL)


def identity_bytes(uid: int = 2003) -> bytes:
    document = {
        "schema": "hepta.service-identities.v1",
        "identities": {
            "hepta-ib-exec": {
                "uid": uid,
                "gid": 2003,
                "role": "ib-paper-execution-authority",
            },
        },
    }
    return (json.dumps(document, sort_keys=True, separators=(",", ":")) +
            "\n").encode("utf-8")


def policy_bytes(identities: bytes, **overrides: object) -> bytes:
    document = {
        "schema": "hepta.broker-network-policy.v1",
        "version": 1,
        "family": "inet",
        "table": "hepta_broker_egress_v1",
        "chain": "output",
        "protected_tcp_destination_ports": [4001, 4002, 7496, 7497],
        "authorized_connectors": [{
            "domain_id": "default",
            "identity": "hepta-ib-exec",
            "uid": 2003,
            "gid": 2003,
            "role": "ib-paper-execution-authority",
        }],
        "paper_identity_manifest": {
            "path": (
                "/etc/heptatrader/"
                "hepta-agent-trust-domain-paper-identities-v1.json"),
            "schema": "hepta.agent-trust-domain-paper-identities.v1",
            "required": False,
            "max_identities": 1,
            "default_paper_authorized": False,
        },
        "default_for_protected_ports": "reject",
        "preserve_other_egress": True,
        "identity_manifest_sha256":
            "sha256:" + hashlib.sha256(identities).hexdigest(),
    }
    document.update(overrides)
    return (json.dumps(document, sort_keys=True, separators=(",", ":")) +
            "\n").encode("utf-8")


def paper_identity_bytes(
        policy: bytes,
        identities: list[dict[str, object]],
        *,
        paper_authorized: bool = True,
        source_policy_sha256: str | None = None) -> bytes:
    document = {
        "schema": "hepta.agent-trust-domain-paper-identities.v1",
        "version": 1,
        "source_policy_sha256": (
            source_policy_sha256 or
            "sha256:" + hashlib.sha256(policy).hexdigest()),
        "paper_authorized": paper_authorized,
        "live_authorized": False,
        "identities": identities,
    }
    return (json.dumps(document, sort_keys=True, separators=(",", ":")) +
            "\n").encode("utf-8")


def dedicated_connector(
        domain_id: str, uid: int, gid: int) -> dict[str, object]:
    return {
        "domain_id": domain_id,
        "identity": f"hepta-ib-exec-{domain_id}",
        "uid": uid,
        "gid": gid,
        "role": "ib-paper-execution-authority",
    }


def live_nft_json(policy: object) -> bytes:
    digest = policy.effective_sha256
    uids = policy.authorized_uids
    if not uids:
        with_uid = SimpleNamespace(
            effective_sha256=digest,
            authorized_uids=(1,),
            family=policy.family,
            table=policy.table,
            chain=policy.chain,
            ports=policy.ports,
        )
        document = json.loads(live_nft_json(with_uid))
        document["nftables"] = [
            item for item in document["nftables"]
            if not (
                isinstance(item, dict) and
                isinstance(item.get("rule"), dict) and
                item["rule"].get("comment") ==
                f"heptatrader-ib-uids:{digest}")
        ]
        return encoded_json(document)
    uid_value: object = (
        uids[0] if len(uids) == 1 else {"set": list(uids)})
    document = {
        "nftables": [
            {"metainfo": {"json_schema_version": 1}},
            {"table": {
                "family": policy.family,
                "name": policy.table,
                "handle": 1,
            }},
            {"chain": {
                "family": policy.family,
                "table": policy.table,
                "name": policy.chain,
                "handle": 2,
                "type": "filter",
                "hook": "output",
                "prio": 0,
                "policy": "accept",
            }},
            {"chain": {
                "family": policy.family,
                "table": policy.table,
                "name": POLICY.GUARD_CHAIN,
                "handle": 3,
            }},
            {"rule": {
                "family": policy.family,
                "table": policy.table,
                "chain": policy.chain,
                "handle": 4,
                "comment": f"heptatrader-ib-ports:{digest}",
                "expr": [
                    {"match": {
                        "op": "==",
                        "left": {"fib": {
                            "result": "type",
                            "flags": ["daddr"],
                        }},
                        "right": "local",
                    }},
                    {"match": {
                        "op": "==",
                        "left": {"meta": {"key": "l4proto"}},
                        "right": "tcp",
                    }},
                    {"match": {
                        "op": "==",
                        "left": {
                            "payload": {
                                "protocol": "tcp",
                                "field": "dport",
                            },
                        },
                        "right": {"set": list(policy.ports)},
                    }},
                    {"jump": {"target": POLICY.GUARD_CHAIN}},
                ],
            }},
            {"rule": {
                "family": policy.family,
                "table": policy.table,
                "chain": POLICY.GUARD_CHAIN,
                "handle": 5,
                "comment": f"heptatrader-ib-uids:{digest}",
                "expr": [
                    {"match": {
                        "op": "==",
                        "left": {"meta": {"key": "skuid"}},
                        "right": uid_value,
                    }},
                    {"counter": {"packets": 0, "bytes": 0}},
                    {"return": None},
                ],
            }},
            {"rule": {
                "family": policy.family,
                "table": policy.table,
                "chain": POLICY.GUARD_CHAIN,
                "handle": 6,
                "comment": (
                    f"heptatrader-ib-default-reject:{digest}"),
                "expr": [
                    {"counter": {"packets": 0, "bytes": 0}},
                    {"reject": {"type": "tcp reset"}},
                ],
            }},
        ],
    }
    return encoded_json(document)


def encoded_json(document: object) -> bytes:
    return (
        json.dumps(document, sort_keys=True, separators=(",", ":")) +
        "\n").encode("utf-8")


class FakeRunner:
    def __init__(
            self, policy: object, *, table_exists: bool = True):
        self.policy = policy
        self.table_exists = table_exists
        self.calls: list[tuple[tuple[str, ...], bytes | None]] = []

    def __call__(
            self, arguments: tuple[str, ...],
            standard_input: bytes | None) -> subprocess.CompletedProcess[bytes]:
        self.calls.append((arguments, standard_input))
        if arguments[:2] == ("list", "table") and not self.table_exists:
            return subprocess.CompletedProcess(arguments, 1, b"", b"missing")
        if arguments[:3] == ("--json", "list", "table"):
            return subprocess.CompletedProcess(
                arguments, 0, live_nft_json(self.policy), b"")
        return subprocess.CompletedProcess(arguments, 0, b"", b"")


class FakeNotifier:
    def __init__(self):
        self.messages: list[str] = []

    def send(self, message: str) -> None:
        self.messages.append(message)


class LifecycleRunner:
    def __init__(
            self, active: object | None,
            available: tuple[object, ...]):
        self.active = active
        self.available = available
        self.calls: list[tuple[tuple[str, ...], bytes | None]] = []

    def __call__(
            self, arguments: tuple[str, ...],
            standard_input: bytes | None) -> subprocess.CompletedProcess[bytes]:
        self.calls.append((arguments, standard_input))
        if arguments[:3] == ("--json", "list", "table"):
            if self.active is None:
                return subprocess.CompletedProcess(
                    arguments, 1, b"", b"missing")
            return subprocess.CompletedProcess(
                arguments, 0, live_nft_json(self.active), b"")
        if arguments[:2] == ("list", "table"):
            return subprocess.CompletedProcess(
                arguments, 0 if self.active is not None else 1, b"", b"")
        if arguments == ("--file", "-"):
            assert standard_input is not None
            matches = [
                policy for policy in self.available
                if policy.effective_sha256.encode("ascii") in standard_input]
            if len(matches) != 1:
                return subprocess.CompletedProcess(
                    arguments, 1, b"", b"unknown transaction")
            self.active = matches[0]
        return subprocess.CompletedProcess(arguments, 0, b"", b"")


class BrokerEgressPolicyTests(unittest.TestCase):
    def boundary_fixture(self, root: Path):
        identities = identity_bytes()
        source_policy = policy_bytes(identities)
        paper = paper_identity_bytes(
            source_policy,
            [dedicated_connector("alpha", os.geteuid() + 10_000,
                                 os.geteuid() + 10_000)])
        identity_path = root / "identities.json"
        policy_path = root / "policy.json"
        paper_path = root / "paper.json"
        for path, payload, mode in (
                (identity_path, identities, 0o644),
                (policy_path, source_policy, 0o644),
                (paper_path, paper, 0o600)):
            path.write_bytes(payload)
            path.chmod(mode)
        loaded = POLICY.load_policy_bundle(
            policy_path, identity_path, paper_path,
            require_installed_metadata=False)
        runtime = root / "runtime"
        runtime.mkdir(mode=0o700)
        runtime.chmod(0o700)
        boot = root / "boot_id"
        boot.write_text(
            "11111111-2222-4333-8444-555555555555\n", encoding="ascii")
        return loaded, runtime, boot

    def activation_fixture(self, root: Path) -> dict[str, object]:
        loaded, runtime, boot = self.boundary_fixture(root)
        authority = root / "authority"
        authority.mkdir(mode=0o700)
        authority.chmod(0o700)
        lease = authority / POLICY.HOST_AUTHORITY_LEASE_NAME
        lease.write_bytes(b"")
        lease.chmod(0o600)
        permit_root = root / "permit"
        permit_root.mkdir(mode=0o700)
        permit_root.chmod(0o700)
        permit_path = permit_root / "broker-start-permit.json"
        drop_in = root / "20-local-paper.conf"
        drop_in.write_bytes(b"[Service]\nExecStart=reviewed\n")
        drop_in.chmod(0o644)
        control = root / "hepta-local-paper-control.py"
        control.write_bytes(b"#!/usr/bin/python3\n# reviewed\n")
        control.chmod(0o400)
        paper_path = loaded.fingerprints[2].path
        now_ms = 1_000_000
        digest = lambda value: (
            "sha256:" + hashlib.sha256(value).hexdigest())
        permit_body = {
            "schema": POLICY.BROKER_START_PERMIT_SCHEMA, "version": 1,
            "issued_at_ms": now_ms,
            "expires_at_ms": now_ms + POLICY.ACTIVATION_TTL_MS,
            "boot_id": boot.read_text(encoding="ascii").strip(),
            "guardian_pid": 1234, "guardian_start_ticks": 5678,
            "guardian_exe_sha256": digest(b"guardian-exe"),
            "guardian_argv_sha256": digest(b"guardian-argv"),
            "control_image_sha256": digest(control.read_bytes()),
            "guardian_request_id": "1" * 32, "domain": "alpha",
            "transaction_id": "2" * 32, "operation": "ENABLE",
            "phase": "BEFORE_001_START_BROKER_LOCAL_PAPER",
            "request_sha256": digest(b"request"),
            "target_identity_manifest_sha256": digest(paper_path.read_bytes()),
            "target_drop_in_sha256": digest(drop_in.read_bytes()),
        }
        permit_raw = POLICY._sealed_json(permit_body)
        permit_path.write_bytes(permit_raw)
        permit_path.chmod(0o600)
        permit = json.loads(permit_raw)
        reservation_raw = POLICY._sealed_json({
            "schema": POLICY.ACTIVATION_RESERVATION_SCHEMA, "version": 1,
            "status": "PENDING_BROKER_ACTIVE", "activation_id": "3" * 32,
            **{key: permit[key] for key in (
                "issued_at_ms", "expires_at_ms", "boot_id", "guardian_pid",
                "guardian_start_ticks", "guardian_exe_sha256",
                "guardian_argv_sha256", "control_image_sha256",
                "guardian_request_id", "domain", "transaction_id",
                "operation", "phase", "request_sha256",
                "target_identity_manifest_sha256",
                "target_drop_in_sha256")},
            "broker_start_permit_file_sha256": digest(permit_raw),
            "broker_start_permit_body_sha256": permit["body_sha256"],
            "required_pre_activation_boundary": "DENY_ALL",
            "paper_only": True, "live_authorized": False,
        })
        owner = authority / POLICY.HOST_AUTHORITY_OWNER_NAME
        owner.write_bytes(reservation_raw)
        owner.chmod(0o600)
        publisher = POLICY.BoundaryReceiptPublisher(
            runtime, expected_uid=os.geteuid(), expected_gid=os.getegid(),
            boot_id_path=boot, require_installed_metadata=False)
        runner = LifecycleRunner(
            loaded.deny_all, (loaded.deny_all, loaded.policy))
        return {
            "loaded": loaded, "runtime": runtime, "boot": boot,
            "authority": authority, "permit": permit_path,
            "drop_in": drop_in, "control": control, "paper": paper_path,
            "publisher": publisher, "runner": runner, "now_ms": now_ms,
            "owner": owner, "reservation_raw": reservation_raw,
        }

    def test_activation_reservation_ttl_is_canonical_policy_constant(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = self.activation_fixture(Path(directory))
            permit = json.loads(fixture["permit"].read_bytes())
            self.assertEqual(
                permit["expires_at_ms"] - permit["issued_at_ms"],
                POLICY.ACTIVATION_TTL_MS,
            )

    def apply_activation(self, fixture: dict[str, object]):
        loaded = fixture["loaded"]
        assert isinstance(loaded, POLICY.LoadedPolicy)
        return POLICY.apply_authorizing_policy_guarded(
            loaded.policy, loaded.fingerprints, fixture["runner"],
            fixture["publisher"],
            host_authority_directory=fixture["authority"],
            expected_uid=os.geteuid(), expected_gid=os.getegid(),
            require_activation_reservation=True,
            pre_activation_policy=loaded.deny_all,
            permit_path=fixture["permit"],
            paper_identity_path=fixture["paper"],
            drop_in_path=fixture["drop_in"],
            control_image_path=fixture["control"],
            boot_id_path=fixture["boot"], now_ms=fixture["now_ms"])

    def test_boundary_generation_is_stable_and_restart_monotonic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            loaded, runtime, boot = self.boundary_fixture(root)
            publisher = POLICY.BoundaryReceiptPublisher(
                runtime, expected_uid=os.geteuid(), expected_gid=os.getegid(),
                boot_id_path=boot, require_installed_metadata=False)
            first = publisher.publish(loaded.deny_all, loaded.fingerprints)
            second = publisher.publish(loaded.deny_all, loaded.fingerprints)
            self.assertEqual(first.document["generation"], 1)
            self.assertEqual(second.document["generation"], 1)
            active = publisher.publish(loaded.policy, loaded.fingerprints)
            self.assertEqual(active.document["generation"], 2)
            # A systemd stop/start must reopen the preserved ledger.  A new
            # publisher object in the same daemon epoch cannot reset or reuse
            # generation 1.
            reopened = POLICY.BoundaryReceiptPublisher(
                runtime, expected_uid=os.geteuid(), expected_gid=os.getegid(),
                boot_id_path=boot, require_installed_metadata=False)
            same_epoch = reopened.publish(loaded.policy, loaded.fingerprints)
            self.assertEqual(same_epoch.document["generation"], 2)
            with mock.patch.object(
                    POLICY, "_process_start_ticks",
                    return_value=active.document["publisher_start_ticks"] + 1):
                restarted = reopened.publish(
                    loaded.policy, loaded.fingerprints)
                observed = POLICY.load_current_boundary_receipt(
                    runtime, expected_uid=os.geteuid(),
                    expected_gid=os.getegid(), boot_id_path=boot,
                    require_installed_metadata=False)
            self.assertEqual(restarted.document["generation"], 3)
            self.assertEqual(observed.payload, restarted.payload)

    def test_boundary_reader_rejects_rollback_torn_stale_and_wrong_boot(
            self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            loaded, runtime, boot = self.boundary_fixture(root)
            publisher = POLICY.BoundaryReceiptPublisher(
                runtime, expected_uid=os.geteuid(), expected_gid=os.getegid(),
                boot_id_path=boot, require_installed_metadata=False)
            old = publisher.publish(loaded.deny_all, loaded.fingerprints)
            publisher.publish(loaded.policy, loaded.fingerprints)
            (runtime / POLICY.BOUNDARY_RECEIPT_NAME).write_bytes(old.payload)
            (runtime / POLICY.BOUNDARY_RECEIPT_NAME).chmod(0o600)
            with self.assertRaisesRegex(POLICY.PolicyError, "rolled back"):
                POLICY.load_current_boundary_receipt(
                    runtime, expected_uid=os.geteuid(),
                    expected_gid=os.getegid(), boot_id_path=boot,
                    require_installed_metadata=False)

            publisher.publish(loaded.deny_all, loaded.fingerprints)
            (runtime / POLICY.BOUNDARY_RECEIPT_NAME).write_bytes(b"{\n")
            (runtime / POLICY.BOUNDARY_RECEIPT_NAME).chmod(0o600)
            with self.assertRaises(POLICY.PolicyError):
                POLICY.load_current_boundary_receipt(
                    runtime, expected_uid=os.geteuid(),
                    expected_gid=os.getegid(), boot_id_path=boot,
                    require_installed_metadata=False)

            current = publisher.publish(loaded.deny_all, loaded.fingerprints)
            with mock.patch.object(
                    POLICY, "_process_start_ticks",
                    side_effect=POLICY.PolicyError("gone")), \
                    self.assertRaisesRegex(POLICY.PolicyError, "not alive"):
                POLICY.load_current_boundary_receipt(
                    runtime, expected_uid=os.geteuid(),
                    expected_gid=os.getegid(), boot_id_path=boot,
                    require_installed_metadata=False)
            with mock.patch.object(
                    POLICY, "_process_start_ticks",
                    return_value=current.document["publisher_start_ticks"] + 1), \
                    self.assertRaisesRegex(POLICY.PolicyError, "reused"):
                POLICY.load_current_boundary_receipt(
                    runtime, expected_uid=os.geteuid(),
                    expected_gid=os.getegid(), boot_id_path=boot,
                    require_installed_metadata=False)
            with self.assertRaisesRegex(POLICY.PolicyError, "stale"):
                POLICY.load_current_boundary_receipt(
                    runtime, expected_uid=os.geteuid(),
                    expected_gid=os.getegid(), boot_id_path=boot,
                    now_ms=current.document["observed_at_ms"] + 3_000,
                    now_monotonic_ns=
                        current.document["observed_monotonic_ns"] +
                        3_000_000_000,
                    require_installed_metadata=False)
            boot.write_text(
                "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee\n", encoding="ascii")
            with self.assertRaisesRegex(POLICY.PolicyError, "rolled back"):
                POLICY.load_current_boundary_receipt(
                    runtime, expected_uid=os.geteuid(),
                    expected_gid=os.getegid(), boot_id_path=boot,
                    require_installed_metadata=False)

    def test_boundary_receipt_publication_can_be_ordered_under_host_lock(
            self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            loaded, runtime, boot = self.boundary_fixture(root)
            host_lock = root / "host-authority.lock"
            host_lock.write_bytes(b"")
            host_lock.chmod(0o600)
            publisher = POLICY.BoundaryReceiptPublisher(
                runtime, expected_uid=os.geteuid(), expected_gid=os.getegid(),
                boot_id_path=boot, require_installed_metadata=False)
            publisher.publish(loaded.deny_all, loaded.fingerprints)
            activated = threading.Event()

            def activate() -> None:
                descriptor = os.open(host_lock, os.O_RDONLY)
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX)
                    publisher.publish(loaded.policy, loaded.fingerprints)
                    activated.set()
                finally:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                    os.close(descriptor)

            thread = threading.Thread(target=activate)
            thread.start()
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
            self.assertTrue(activated.is_set())
            receipt = POLICY.load_current_boundary_receipt(
                runtime, expected_uid=os.geteuid(), expected_gid=os.getegid(),
                boot_id_path=boot, require_installed_metadata=False)
            self.assertEqual(receipt.document["state"], "ACTIVE")

    def test_authorizing_apply_is_cas_guarded_by_host_owner_and_lock(
            self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            loaded, _runtime, _boot = self.boundary_fixture(root)
            authority = root / "authority"
            authority.mkdir(mode=0o700)
            authority.chmod(0o700)
            lease_path = authority / POLICY.HOST_AUTHORITY_LEASE_NAME
            lease_path.write_bytes(b"")
            lease_path.chmod(0o600)
            publisher = mock.Mock()
            publisher.publish.return_value = object()
            with mock.patch.object(POLICY, "apply_policy") as apply:
                result = POLICY.apply_authorizing_policy_guarded(
                    loaded.policy, loaded.fingerprints, mock.Mock(), publisher,
                    host_authority_directory=authority,
                    expected_uid=os.geteuid(), expected_gid=os.getegid(),
                    require_activation_reservation=False)
            self.assertIs(result, publisher.publish.return_value)
            apply.assert_called_once()
            publisher.publish.assert_called_once_with(
                loaded.policy, loaded.fingerprints)

            (authority / POLICY.HOST_AUTHORITY_OWNER_NAME).write_bytes(
                b"terminal-owner\n")
            (authority / POLICY.HOST_AUTHORITY_OWNER_NAME).chmod(0o600)
            with mock.patch.object(POLICY, "apply_policy") as apply, \
                    self.assertRaisesRegex(POLICY.PolicyError, "owner blocks"):
                POLICY.apply_authorizing_policy_guarded(
                    loaded.policy, loaded.fingerprints, mock.Mock(), publisher,
                    host_authority_directory=authority,
                    expected_uid=os.geteuid(), expected_gid=os.getegid(),
                    require_activation_reservation=False)
            apply.assert_not_called()
            (authority / POLICY.HOST_AUTHORITY_OWNER_NAME).unlink()

            held = os.open(lease_path, os.O_RDONLY)
            try:
                fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with mock.patch.object(POLICY, "apply_policy") as apply, \
                        self.assertRaisesRegex(
                            POLICY.PolicyError, "lease is busy"):
                    POLICY.apply_authorizing_policy_guarded(
                        loaded.policy, loaded.fingerprints, mock.Mock(),
                        publisher, host_authority_directory=authority,
                        expected_uid=os.geteuid(), expected_gid=os.getegid(),
                        require_activation_reservation=False)
                apply.assert_not_called()
            finally:
                fcntl.flock(held, fcntl.LOCK_UN)
                os.close(held)

    def test_templated_activation_consumes_exact_reservation_and_permit(
            self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = self.activation_fixture(Path(directory))
            with mock.patch.object(
                    POLICY, "_guardian_identity_is_current",
                    return_value=True):
                receipt = self.apply_activation(fixture)
            self.assertEqual(receipt.document["status"], "EXACT_ACTIVE")
            self.assertEqual(
                fixture["owner"].read_bytes(), fixture["reservation_raw"])
            self.assertFalse(fixture["permit"].exists())
            consumed_path = fixture["authority"] / (
                POLICY.ACTIVATION_CONSUMED_NAME_PREFIX + "3" * 32 +
                POLICY.ACTIVATION_CONSUMED_NAME_SUFFIX)
            consumed = POLICY._validate_sealed_json(
                consumed_path.read_bytes(),
                schema=POLICY.ACTIVATION_CONSUMED_SCHEMA,
                expected_fields=POLICY.ACTIVATION_CONSUMED_FIELDS,
                label="test consumed")
            self.assertEqual(consumed["status"], "ACTIVE_BOUNDARY_COMMITTED")
            self.assertEqual(
                consumed["active_boundary_state_sha256"],
                receipt.document["state_sha256"])
            self.assertNotIn("active_boundary_generation", consumed)
            self.assertNotIn("active_boundary_publisher_pid", consumed)
            intent_path = fixture["authority"] / (
                POLICY.ACTIVATION_INTENT_NAME_PREFIX + "3" * 32 +
                POLICY.ACTIVATION_CONSUMED_NAME_SUFFIX)
            self.assertFalse(intent_path.exists())
            first = consumed_path.read_bytes()
            fixture["publisher"] = POLICY.BoundaryReceiptPublisher(
                fixture["runtime"], expected_uid=os.geteuid(),
                expected_gid=os.getegid(), boot_id_path=fixture["boot"],
                require_installed_metadata=False)
            # A crash after the permit unlink is replayable from the durable
            # consumed record even after the original guardian/TTL is gone.
            fixture["now_ms"] += 60_000
            with mock.patch.object(
                    POLICY, "_guardian_identity_is_current",
                    return_value=False), mock.patch.object(
                        POLICY, "_process_start_ticks", return_value=112233):
                replayed = self.apply_activation(fixture)
            self.assertEqual(replayed.document["status"], "EXACT_ACTIVE")
            self.assertEqual(consumed_path.read_bytes(), first)
            self.assertEqual(
                fixture["owner"].read_bytes(), fixture["reservation_raw"])

    def test_activation_resumes_after_kernel_apply_before_tombstone(
            self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = self.activation_fixture(Path(directory))
            publisher = fixture["publisher"]
            original_publish = publisher.publish

            def crash_before_active_receipt(policy, fingerprints):
                if policy.authorized_connectors:
                    raise POLICY.PolicyError("injected after kernel apply")
                return original_publish(policy, fingerprints)

            publisher.publish = crash_before_active_receipt
            with mock.patch.object(
                    POLICY, "_guardian_identity_is_current",
                    return_value=True), self.assertRaisesRegex(
                        POLICY.PolicyError, "injected after kernel apply"):
                self.apply_activation(fixture)
            self.assertEqual(fixture["runner"].active,
                             fixture["loaded"].policy)
            self.assertTrue(fixture["owner"].exists())
            fixture["publisher"] = POLICY.BoundaryReceiptPublisher(
                fixture["runtime"], expected_uid=os.geteuid(),
                expected_gid=os.getegid(), boot_id_path=fixture["boot"],
                require_installed_metadata=False)
            with mock.patch.object(
                    POLICY, "_guardian_identity_is_current",
                    return_value=True), mock.patch.object(
                        POLICY, "_process_start_ticks", return_value=987654):
                receipt = self.apply_activation(fixture)
            self.assertEqual(receipt.document["status"], "EXACT_ACTIVE")
            self.assertTrue(fixture["owner"].exists())
            self.assertFalse(fixture["permit"].exists())

    def test_activation_resumes_after_tombstone_before_owner_cleanup(
            self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = self.activation_fixture(Path(directory))
            real_unlink = POLICY._unlink_exact_private_path
            crashed = False

            def crash_before_cleanup(*args, **kwargs):
                nonlocal crashed
                if not crashed:
                    crashed = True
                    raise POLICY.PolicyError("injected after tombstone")
                return real_unlink(*args, **kwargs)

            with mock.patch.object(
                    POLICY, "_guardian_identity_is_current",
                    return_value=True), mock.patch.object(
                        POLICY, "_unlink_exact_private_path",
                        side_effect=crash_before_cleanup), \
                    self.assertRaisesRegex(
                        POLICY.PolicyError, "injected after tombstone"):
                self.apply_activation(fixture)
            consumed_path = fixture["authority"] / (
                POLICY.ACTIVATION_CONSUMED_NAME_PREFIX + "3" * 32 +
                POLICY.ACTIVATION_CONSUMED_NAME_SUFFIX)
            first = consumed_path.read_bytes()
            self.assertTrue(fixture["owner"].exists())
            fixture["publisher"] = POLICY.BoundaryReceiptPublisher(
                fixture["runtime"], expected_uid=os.geteuid(),
                expected_gid=os.getegid(), boot_id_path=fixture["boot"],
                require_installed_metadata=False)
            with mock.patch.object(
                    POLICY, "_guardian_identity_is_current",
                    return_value=True), mock.patch.object(
                        POLICY, "_process_start_ticks", return_value=246810):
                receipt = self.apply_activation(fixture)
            self.assertEqual(receipt.document["status"], "EXACT_ACTIVE")
            self.assertEqual(consumed_path.read_bytes(), first)
            self.assertTrue(fixture["owner"].exists())
            self.assertFalse(fixture["permit"].exists())

    def test_unproven_already_active_reservation_tightens_and_fails(
            self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = self.activation_fixture(Path(directory))
            fixture["runner"].active = fixture["loaded"].policy
            with mock.patch.object(
                    POLICY, "_guardian_identity_is_current",
                    return_value=True), self.assertRaisesRegex(
                        POLICY.PolicyError, "lacks durable predecessor"):
                self.apply_activation(fixture)
            self.assertEqual(fixture["runner"].active,
                             fixture["loaded"].deny_all)
            self.assertTrue(fixture["owner"].exists())

    def test_expired_pending_already_active_tightens_without_guardian(
            self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = self.activation_fixture(Path(directory))
            fixture["runner"].active = fixture["loaded"].policy
            fixture["now_ms"] += 16_000
            with mock.patch.object(
                    POLICY, "_guardian_identity_is_current",
                    return_value=False), self.assertRaisesRegex(
                        POLICY.PolicyError, "reservation is invalid"):
                self.apply_activation(fixture)
            self.assertEqual(fixture["runner"].active,
                             fixture["loaded"].deny_all)
            self.assertTrue(fixture["permit"].exists())
            self.assertTrue(fixture["owner"].exists())

    def test_consumed_replay_cannot_reauthorize_from_deny_all(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = self.activation_fixture(Path(directory))
            with mock.patch.object(
                    POLICY, "_guardian_identity_is_current",
                    return_value=True):
                self.apply_activation(fixture)
            self.assertFalse(fixture["permit"].exists())
            fixture["runner"].active = fixture["loaded"].deny_all
            fixture["now_ms"] += 60_000
            with mock.patch.object(
                    POLICY, "_guardian_identity_is_current",
                    return_value=False), self.assertRaisesRegex(
                        POLICY.PolicyError, "cannot reauthorize"):
                self.apply_activation(fixture)
            self.assertEqual(fixture["runner"].active,
                             fixture["loaded"].deny_all)
            self.assertTrue(fixture["owner"].exists())

    def test_local_supervise_argv_consumes_reservation_before_ready(
            self) -> None:
        local_control = (
            ROOT / "scripts/hepta_local_paper_control.py"
        ).read_text(encoding="utf-8", errors="strict")
        self.assertIn(
            '"--supervise --paper-identities "', local_control)
        with tempfile.TemporaryDirectory() as directory:
            loaded, _runtime, _boot = self.boundary_fixture(Path(directory))
            guarded = mock.Mock(return_value=object())
            supervised = mock.Mock()
            publisher = mock.Mock()
            with mock.patch.object(
                    POLICY, "load_policy_bundle", return_value=loaded), \
                    mock.patch.object(POLICY, "validate_os_identities"), \
                    mock.patch.object(
                        POLICY, "_trusted_nft_binary", return_value=Path("/nft")), \
                    mock.patch.object(
                        POLICY, "BoundaryReceiptPublisher",
                        return_value=publisher), \
                    mock.patch.object(
                        POLICY, "apply_authorizing_policy_guarded", guarded), \
                    mock.patch.object(POLICY, "supervise_policy", supervised), \
                    mock.patch.object(
                        POLICY, "SystemdNotifier", return_value=mock.Mock()), \
                    mock.patch.object(
                        POLICY, "_install_stop_handlers", return_value={}), \
                    mock.patch.object(POLICY, "_restore_stop_handlers"), \
                    mock.patch.object(POLICY.os, "geteuid", return_value=0), \
                    mock.patch.object(POLICY.os, "getegid", return_value=0):
                result = POLICY.main([
                    "--supervise", "--paper-identities", "/paper.json"])
            self.assertEqual(result, 0)
            guarded.assert_called_once_with(
                loaded.policy, loaded.fingerprints, mock.ANY, publisher,
                pre_activation_policy=loaded.deny_all)
            supervised.assert_called_once()
            self.assertIs(supervised.call_args.args[0], loaded)

    def test_boundary_unit_has_private_preserved_runtime(self) -> None:
        unit = (ROOT / "systemd/hepta-broker-egress-policy.service").read_text(
            encoding="utf-8", errors="strict")
        self.assertIn("RuntimeDirectory=hepta-broker-egress-policy\n", unit)
        self.assertIn("RuntimeDirectoryMode=0700\n", unit)
        # Preserve the generation ledger across explicit stop/start too; only
        # a new boot may restart the generation sequence.
        self.assertIn("RuntimeDirectoryPreserve=yes\n", unit)
        self.assertNotIn("CAP_NET_RAW", unit)

    def test_watchdog_budget_dominates_bounded_nft_calls(self) -> None:
        unit = (
            ROOT / "systemd/hepta-broker-egress-policy.service"
        ).read_text(encoding="utf-8", errors="strict")
        self.assertEqual(POLICY.NFT_COMMAND_TIMEOUT_SECONDS, 2)
        self.assertEqual(POLICY.NFT_QUERY_TIMEOUT_SECONDS, 3)
        self.assertIn("WatchdogSec=15s\n", unit)
        self.assertIn("TimeoutStopSec=30s\n", unit)
        self.assertGreater(
            15,
            2 * POLICY.NFT_QUERY_TIMEOUT_SECONDS +
            3 * POLICY.NFT_COMMAND_TIMEOUT_SECONDS)
        self.assertGreater(
            30,
            POLICY.NFT_QUERY_TIMEOUT_SECONDS +
            3 * POLICY.NFT_COMMAND_TIMEOUT_SECONDS)

    def test_read_only_nft_query_gets_bounded_scheduler_slack(self) -> None:
        timeout = subprocess.TimeoutExpired(
            cmd=("/usr/sbin/nft", "--json", "list", "table"),
            timeout=POLICY.NFT_QUERY_TIMEOUT_SECONDS)
        with (
                mock.patch.object(
                    POLICY.subprocess, "run", side_effect=timeout) as run,
                self.assertRaisesRegex(
                    POLICY.PolicyError,
                    "nftables command execution failed")):
            POLICY._run_nft(
                Path("/usr/sbin/nft"),
                ("--json", "list", "table", "inet", "table"), None)
        self.assertEqual(
            run.call_args.kwargs["timeout"],
            POLICY.NFT_QUERY_TIMEOUT_SECONDS)

        completed = subprocess.CompletedProcess((), 0, b"", b"")
        with mock.patch.object(
                POLICY.subprocess, "run", return_value=completed) as run:
            POLICY._run_nft(
                Path("/usr/sbin/nft"), ("--check", "--file", "-"), b"")
        self.assertEqual(
            run.call_args.kwargs["timeout"],
            POLICY.NFT_COMMAND_TIMEOUT_SECONDS)

    def test_systemd_supervisor_executes_frozen_deny_all_helper(self) -> None:
        unit = (
            ROOT / "systemd/hepta-broker-egress-policy.service"
        ).read_text(encoding="utf-8", errors="strict")
        self.assertIn(
            "LoadCredential=hepta-broker-egress-policy.py:"
            "/usr/libexec/hepta-broker-egress-policy\n",
            unit)
        self.assertIn(
            "ExecStart=/usr/bin/python3.12 -I -S "
            "${CREDENTIALS_DIRECTORY}/hepta-broker-egress-policy.py "
            "--supervise-deny-all --paper-identities "
            "/etc/heptatrader/"
            "hepta-agent-trust-domain-paper-identities-v1.json\n",
            unit)
        self.assertIn(
            "ExecStopPost=/usr/bin/python3.12 -I -S "
            "${CREDENTIALS_DIRECTORY}/hepta-broker-egress-policy.py "
            "--tighten-deny-all\n",
            unit)
        self.assertNotIn(" --supervise --paper-identities ", unit)

    def test_rootful_probe_uses_only_inert_exact_port_sentinels(self) -> None:
        self.assertEqual(ROOTFUL.PROTECTED_PORTS, (4001, 4002, 7496, 7497))
        self.assertEqual(ROOTFUL.IB_EXECUTION_IDENTITY, (2003, 2003))
        self.assertEqual(ROOTFUL.AGENT_IDENTITY, (2004, 2004))
        self.assertEqual(
            ROOTFUL.DOMAIN_AGENT_IDENTITIES,
            ((2104, 2104), (2105, 2105)))
        self.assertEqual(
            ROOTFUL.DOMAIN_GATEWAY_IDENTITIES,
            ((2101, 2101), (2102, 2102)))
        self.assertEqual(
            ROOTFUL.DOMAIN_SIMULATOR_EXECUTION_IDENTITIES,
            ((2111, 2111), (2112, 2112)))
        self.assertNotIn(
            ROOTFUL.MODEL_EGRESS_SENTINEL_PORT, ROOTFUL.PROTECTED_PORTS)
        source = ROOTFUL_MODULE_PATH.read_text(
            encoding="utf-8", errors="strict")
        for forbidden in (
                "ibapi", "placeOrder", "reqIds", "EClientSocket",
                "trade.place_order"):
            self.assertNotIn(forbidden, source)

    def test_repository_policy_is_manifest_bound_and_deterministic(self) -> None:
        policy_raw = (
            ROOT / "systemd/hepta-broker-network-policy-v1.json").read_bytes()
        identity_raw = (
            ROOT / "systemd/hepta-service-identities-v1.json").read_bytes()
        paper_identity_raw = (
            ROOT / "systemd/"
            "hepta-agent-trust-domain-paper-identities-v1.json.example"
        ).read_bytes()
        parsed = POLICY.parse_policy(
            policy_raw, identity_raw, paper_identity_raw)
        self.assertEqual(parsed.authorized_uids, (2003,))
        self.assertEqual(
            parsed.authorized_connectors[0].identity, "hepta-ib-exec")
        self.assertEqual(parsed.ports, (4001, 4002, 7496, 7497))
        rendered = POLICY.render_transaction(parsed).decode("ascii")
        self.assertIn(
            "fib daddr type local meta l4proto tcp "
            "tcp dport { 4001, 4002, 7496, 7497 } "
            "jump ib_guard", rendered)
        self.assertIn("meta skuid 2003 counter return", rendered)
        self.assertNotIn("meta skuid !=", rendered)
        self.assertIn("hook output", rendered)
        self.assertIn("policy accept", rendered)
        self.assertNotIn("dport { 80", rendered)
        self.assertNotIn("dport { 443", rendered)
        self.assertNotIn("policy drop", rendered)

    def test_apply_is_checked_and_replaces_table_in_one_transaction(self) -> None:
        identities = identity_bytes()
        parsed = POLICY.parse_policy(policy_bytes(identities), identities)
        runner = FakeRunner(parsed)
        POLICY.apply_policy(parsed, runner)
        self.assertEqual(
            [arguments for arguments, _input in runner.calls],
            [
                ("list", "table", "inet", "hepta_broker_egress_v1"),
                ("--check", "--file", "-"),
                ("--file", "-"),
                ("--json", "list", "table", "inet",
                 "hepta_broker_egress_v1"),
            ],
        )
        transaction = runner.calls[1][1]
        self.assertIsNotNone(transaction)
        assert transaction is not None
        self.assertTrue(transaction.startswith(
            b"delete table inet hepta_broker_egress_v1\n"
            b"add table inet hepta_broker_egress_v1\n"))
        self.assertEqual(transaction, runner.calls[2][1])

    def test_first_apply_creates_complete_policy_in_one_transaction(
            self) -> None:
        identities = identity_bytes()
        parsed = POLICY.parse_policy(policy_bytes(identities), identities)
        runner = FakeRunner(parsed, table_exists=False)
        POLICY.apply_policy(parsed, runner)
        self.assertEqual(runner.calls[1][0], ("--check", "--file", "-"))
        transaction = runner.calls[1][1]
        self.assertIsNotNone(transaction)
        assert transaction is not None
        self.assertTrue(transaction.startswith(
            b"add table inet hepta_broker_egress_v1\n"))
        self.assertNotIn(b"delete table", transaction)

    def test_identity_digest_drift_fails_closed(self) -> None:
        identities = identity_bytes()
        other = identity_bytes(uid=2013)
        with self.assertRaisesRegex(
                POLICY.PolicyError, "digest binding mismatch"):
            POLICY.parse_policy(policy_bytes(identities), other)

    def test_explicit_paper_identity_replaces_fixed_compatibility_uid(
            self) -> None:
        identities = identity_bytes()
        source_policy = policy_bytes(identities)
        paper = paper_identity_bytes(source_policy, [
            dedicated_connector("codex-a", 2121, 2121),
        ])
        parsed = POLICY.parse_policy(source_policy, identities, paper)
        self.assertEqual(parsed.authorized_uids, (2121,))
        self.assertEqual(
            [item.identity for item in parsed.authorized_connectors],
            ["hepta-ib-exec-codex-a"],
        )
        rendered = POLICY.render_transaction(parsed).decode("ascii")
        self.assertIn("meta skuid 2121 counter return", rendered)
        self.assertNotIn("meta skuid 2003", rendered)
        self.assertNotIn("2111", rendered)
        self.assertNotIn("2112", rendered)
        self.assertNotIn("2122", rendered)

    def test_second_templated_paper_domain_is_rejected(self) -> None:
        identities = identity_bytes()
        source_policy = policy_bytes(identities)
        paper = paper_identity_bytes(source_policy, [
            dedicated_connector("codex-a", 2121, 2121),
            dedicated_connector("openclaw-b", 2122, 2122),
        ])
        with self.assertRaisesRegex(
                POLICY.PolicyError, "authorization/list mismatch"):
            POLICY.parse_policy(source_policy, identities, paper)

    def test_default_paper_manifest_has_no_authority(self) -> None:
        identities = identity_bytes()
        source_policy = policy_bytes(identities)
        paper = paper_identity_bytes(
            source_policy, [], paper_authorized=False)
        parsed = POLICY.parse_policy(source_policy, identities, paper)
        self.assertEqual(parsed.authorized_uids, (2003,))

    def test_deny_all_supervisor_requires_explicit_false_authority(
            self) -> None:
        identities = identity_bytes()
        source_policy = policy_bytes(identities)
        valid = paper_identity_bytes(
            source_policy, [], paper_authorized=False)
        authorized = paper_identity_bytes(
            source_policy,
            [dedicated_connector("codex-a", 2121, 2121)])
        live_document = json.loads(valid)
        live_document["live_authorized"] = True
        live = encoded_json(live_document)
        invalid_cases = (
            ("absent", None),
            ("paper-authorized", authorized),
            ("live-authorized", live),
        )
        for label, paper in invalid_cases:
            with (
                    self.subTest(label=label),
                    tempfile.TemporaryDirectory(
                        prefix="hepta-deny-all-auth-") as directory):
                root = Path(directory)
                identity_path = root / "identities.json"
                policy_path = root / "policy.json"
                paper_path = root / "paper.json"
                identity_path.write_bytes(identities)
                policy_path.write_bytes(source_policy)
                os.chmod(identity_path, 0o644)
                os.chmod(policy_path, 0o644)
                if paper is not None:
                    paper_path.write_bytes(paper)
                    os.chmod(paper_path, 0o600)
                with self.assertRaises(POLICY.PolicyError):
                    POLICY.load_policy_bundle(
                        policy_path, identity_path, paper_path,
                        require_installed_metadata=False,
                        require_explicit_deny_all_authorization=True)

        with tempfile.TemporaryDirectory(
                prefix="hepta-deny-all-auth-valid-") as directory:
            root = Path(directory)
            identity_path = root / "identities.json"
            policy_path = root / "policy.json"
            paper_path = root / "paper.json"
            identity_path.write_bytes(identities)
            policy_path.write_bytes(source_policy)
            paper_path.write_bytes(valid)
            os.chmod(identity_path, 0o644)
            os.chmod(policy_path, 0o644)
            os.chmod(paper_path, 0o600)
            loaded = POLICY.load_policy_bundle(
                policy_path, identity_path, paper_path,
                require_installed_metadata=False,
                require_explicit_deny_all_authorization=True)
        self.assertTrue(loaded.explicit_deny_all_authorization)
        self.assertEqual(loaded.deny_all.authorized_connectors, ())

    def test_paper_identity_manifest_is_source_policy_bound(self) -> None:
        identities = identity_bytes()
        source_policy = policy_bytes(identities)
        paper = paper_identity_bytes(
            source_policy,
            [dedicated_connector("codex-a", 2121, 2121)],
            source_policy_sha256="sha256:" + "0" * 64)
        with self.assertRaisesRegex(
                POLICY.PolicyError, "fixed contract mismatch"):
            POLICY.parse_policy(source_policy, identities, paper)

    def test_simulator_identity_cannot_enter_paper_allowlist(self) -> None:
        identities = identity_bytes()
        source_policy = policy_bytes(identities)
        simulator = dedicated_connector("codex-a", 2111, 2111)
        simulator["identity"] = "hepta-exec-codex-a"
        paper = paper_identity_bytes(source_policy, [simulator])
        with self.assertRaisesRegex(
                POLICY.PolicyError,
                "dedicated IB Execution identity mismatch"):
            POLICY.parse_policy(source_policy, identities, paper)

    def test_paper_identity_uid_and_domain_must_be_unique(self) -> None:
        identities = identity_bytes()
        source_policy = policy_bytes(identities)
        cases = (
            [
                dedicated_connector("codex-a", 2121, 2121),
                dedicated_connector("openclaw-b", 2121, 2122),
            ],
            [
                dedicated_connector("codex-a", 2121, 2121),
                dedicated_connector("codex-a", 2122, 2122),
            ],
        )
        for records in cases:
            with self.subTest(records=records), self.assertRaises(
                    POLICY.PolicyError):
                POLICY.parse_policy(
                    source_policy, identities,
                    paper_identity_bytes(source_policy, records))

    def test_false_paper_authorization_cannot_carry_identities(self) -> None:
        identities = identity_bytes()
        source_policy = policy_bytes(identities)
        paper = paper_identity_bytes(
            source_policy,
            [dedicated_connector("codex-a", 2121, 2121)],
            paper_authorized=False)
        with self.assertRaisesRegex(
                POLICY.PolicyError, "authorization/list mismatch"):
            POLICY.parse_policy(source_policy, identities, paper)

    def test_paper_identity_records_are_sorted_and_os_name_bounded(
            self) -> None:
        identities = identity_bytes()
        source_policy = policy_bytes(identities)
        cases = (
            [
                dedicated_connector("openclaw-b", 2122, 2122),
                dedicated_connector("codex-a", 2121, 2121),
            ],
            [
                dedicated_connector(
                    "domain-name-longer-than-eighteen", 2121, 2121),
            ],
        )
        for records in cases:
            with self.subTest(records=records), self.assertRaises(
                    POLICY.PolicyError):
                POLICY.parse_policy(
                    source_policy, identities,
                    paper_identity_bytes(source_policy, records))

    def test_paper_identity_requires_matching_dedicated_uid_gid(self) -> None:
        identities = identity_bytes()
        source_policy = policy_bytes(identities)
        record = dedicated_connector("codex-a", 2121, 2122)
        with self.assertRaisesRegex(
                POLICY.PolicyError, "UID/GID mismatch"):
            POLICY.parse_policy(
                source_policy, identities,
                paper_identity_bytes(source_policy, [record]))

    def test_authorized_uid_drift_fails_closed(self) -> None:
        identities = identity_bytes()
        connector = {
            "domain_id": "default",
            "identity": "hepta-ib-exec",
            "uid": 2004,
            "gid": 2003,
            "role": "ib-paper-execution-authority",
        }
        with self.assertRaisesRegex(
                POLICY.PolicyError, "differs from manifest"):
            POLICY.parse_policy(
                policy_bytes(identities, authorized_connectors=[connector]),
                identities)

    def test_authorized_numeric_identity_rejects_passwd_group_aliases(
            self) -> None:
        identities = identity_bytes()
        parsed = POLICY.parse_policy(policy_bytes(identities), identities)
        account = SimpleNamespace(
            pw_name="hepta-ib-exec", pw_uid=2003, pw_gid=2003,
            pw_dir="/nonexistent", pw_shell="/usr/sbin/nologin")
        account_alias = SimpleNamespace(
            pw_name="paper-alias", pw_uid=2003, pw_gid=2003,
            pw_dir="/nonexistent", pw_shell="/usr/sbin/nologin")
        group = SimpleNamespace(
            gr_name="hepta-ib-exec", gr_gid=2003, gr_mem=[])
        group_alias = SimpleNamespace(
            gr_name="paper-alias", gr_gid=2003, gr_mem=[])
        for accounts, groups in (
                ([account, account_alias], [group]),
                ([account], [group, group_alias])):
            with (
                    self.subTest(accounts=accounts, groups=groups),
                    mock.patch.object(
                        POLICY.pwd, "getpwnam", return_value=account),
                    mock.patch.object(
                        POLICY.pwd, "getpwuid", return_value=account),
                    mock.patch.object(
                        POLICY.pwd, "getpwall", return_value=accounts),
                    mock.patch.object(
                        POLICY.grp, "getgrnam", return_value=group),
                    mock.patch.object(
                        POLICY.grp, "getgrgid", return_value=group),
                    mock.patch.object(
                        POLICY.grp, "getgrall", return_value=groups),
                    mock.patch.object(
                        POLICY.os, "getgrouplist", return_value=[2003]),
                    self.assertRaisesRegex(
                        POLICY.PolicyError, "metadata mismatch")):
                POLICY.validate_os_identities(parsed)

    def test_port_removal_or_addition_fails_closed(self) -> None:
        identities = identity_bytes()
        for ports in (
                [4002, 7496, 7497],
                [4001, 4002, 7496, 7497, 8000],
                [4002, 4001, 7496, 7497]):
            with self.subTest(ports=ports):
                with self.assertRaisesRegex(
                        POLICY.PolicyError, "port set mismatch"):
                    POLICY.parse_policy(
                        policy_bytes(
                            identities,
                            protected_tcp_destination_ports=ports),
                        identities)

    def test_preserve_other_egress_cannot_be_disabled(self) -> None:
        identities = identity_bytes()
        with self.assertRaisesRegex(
                POLICY.PolicyError, "fixed contract mismatch"):
            POLICY.parse_policy(
                policy_bytes(identities, preserve_other_egress=False),
                identities)

    def test_duplicate_policy_key_fails_closed(self) -> None:
        identities = identity_bytes()
        raw = policy_bytes(identities)
        duplicate = raw[:-2] + b',"version":1}\n'
        with self.assertRaisesRegex(POLICY.PolicyError, "duplicate JSON key"):
            POLICY.parse_policy(duplicate, identities)

    def test_boolean_versions_fail_closed(self) -> None:
        identities = identity_bytes()
        source_policy = policy_bytes(identities, version=True)
        with self.assertRaisesRegex(POLICY.PolicyError, "fixed contract"):
            POLICY.parse_policy(source_policy, identities)
        valid_policy = policy_bytes(identities)
        paper = paper_identity_bytes(
            valid_policy, [], paper_authorized=False)
        document = json.loads(paper)
        document["version"] = True
        boolean_paper = (
            json.dumps(document, sort_keys=True, separators=(",", ":")) +
            "\n").encode("utf-8")
        with self.assertRaisesRegex(POLICY.PolicyError, "fixed contract"):
            POLICY.parse_policy(valid_policy, identities, boolean_paper)

    def test_live_nft_json_requires_the_exact_complete_semantics(self) -> None:
        identities = identity_bytes()
        source_policy = policy_bytes(identities)
        policy = POLICY.parse_policy(
            source_policy, identities,
            paper_identity_bytes(
                source_policy,
                [dedicated_connector("codex-a", 2121, 2121)]))
        valid = json.loads(live_nft_json(policy))
        POLICY.verify_active_policy_json(policy, encoded_json(valid))
        kernel_normalized = json.loads(json.dumps(valid))
        del kernel_normalized["nftables"][-3]["rule"]["expr"][1]
        kernel_normalized["nftables"][-1]["rule"]["expr"].insert(0, {
            "match": {
                "op": "==",
                "left": {"meta": {"key": "l4proto"}},
                "right": "tcp",
            },
        })
        POLICY.verify_active_policy_json(
            policy, encoded_json(kernel_normalized))
        mutations = []
        extra = json.loads(json.dumps(valid))
        extra["nftables"].append(copy_rule := {
            "rule": json.loads(json.dumps(
                valid["nftables"][-1]["rule"])),
        })
        copy_rule["rule"]["handle"] = 99
        mutations.append(extra)
        wrong_uid = json.loads(json.dumps(valid))
        wrong_uid["nftables"][-2]["rule"]["expr"][0]["match"]["right"] = 2122
        mutations.append(wrong_uid)
        missing_port = json.loads(json.dumps(valid))
        missing_port["nftables"][-3]["rule"]["expr"][1]["match"]["right"] = {
            "set": [4002, 7496, 7497],
        }
        mutations.append(missing_port)
        wrong_policy = json.loads(json.dumps(valid))
        wrong_policy["nftables"][2]["chain"]["policy"] = "drop"
        mutations.append(wrong_policy)
        stale_digest = json.loads(json.dumps(valid))
        stale_digest["nftables"][-1]["rule"]["comment"] = (
            "heptatrader-ib-default-reject:" + "0" * 64)
        mutations.append(stale_digest)
        for mutation in mutations:
            with self.subTest(mutation=mutation), self.assertRaises(
                    POLICY.PolicyError):
                POLICY.verify_active_policy_json(
                    policy, encoded_json(mutation))

    def test_guard_flush_or_input_drift_tightens_to_deny_all(self) -> None:
        identities = identity_bytes()
        source_policy = policy_bytes(identities)
        active = POLICY.parse_policy(
            source_policy, identities,
            paper_identity_bytes(
                source_policy,
                [dedicated_connector("codex-a", 2121, 2121)]))
        fixed = POLICY.parse_policy(source_policy, identities)
        deny_all = POLICY._deny_all_policy(fixed)
        loaded = POLICY.LoadedPolicy(
            policy=active,
            fixed_only=fixed,
            deny_all=deny_all,
            fingerprints=(
                POLICY.SourceFingerprint(
                    Path("/policy"), present=True, sha256="a" * 64),
            ),
        )
        for failure in ("input", "flush"):
            runner = LifecycleRunner(active, (active, deny_all))
            notifier = FakeNotifier()

            def checker(_fingerprint: object) -> bool:
                if failure == "flush":
                    runner.active = None
                    return True
                return False

            with (
                    self.subTest(failure=failure),
                    self.assertRaisesRegex(
                        POLICY.PolicyError, "installed deny-all")):
                POLICY.supervise_policy(
                    loaded,
                    runner,
                    notifier,
                    threading.Event(),
                    poll_interval=0.01,
                    source_checker=checker)
            self.assertEqual(runner.active, deny_all)
            self.assertTrue(any(
                "READY=1" in message for message in notifier.messages))
            self.assertTrue(any(
                "STOPPING=1" in message for message in notifier.messages))
            if failure == "flush":
                self.assertTrue(any(
                    "broker boundary validating" in message
                    for message in notifier.messages))

    def test_guarded_policy_reads_live_table_once_for_all_candidates(self) -> None:
        identities = identity_bytes()
        source_policy = policy_bytes(identities)
        active = POLICY.parse_policy(
            source_policy, identities,
            paper_identity_bytes(
                source_policy,
                [dedicated_connector("codex-a", 2121, 2121)]))
        fixed = POLICY.parse_policy(source_policy, identities)
        deny_all = POLICY._deny_all_policy(fixed)
        loaded = POLICY.LoadedPolicy(
            policy=active,
            fixed_only=fixed,
            deny_all=deny_all,
            fingerprints=())
        for observed in (active, deny_all):
            with self.subTest(observed=observed):
                runner = LifecycleRunner(
                    observed, (active, deny_all))
                self.assertEqual(
                    POLICY.verify_guarded_policy(loaded, runner), observed)
                reads = [
                    arguments for arguments, _input in runner.calls
                    if arguments[:3] == ("--json", "list", "table")
                ]
                self.assertEqual(len(reads), 1)

    def test_guarded_policy_sampling_stays_linear_under_pressure(self) -> None:
        identities = identity_bytes()
        source_policy = policy_bytes(identities)
        active = POLICY.parse_policy(
            source_policy, identities,
            paper_identity_bytes(
                source_policy,
                [dedicated_connector("codex-a", 2121, 2121)]))
        fixed = POLICY.parse_policy(source_policy, identities)
        deny_all = POLICY._deny_all_policy(fixed)
        loaded = POLICY.LoadedPolicy(
            policy=active,
            fixed_only=fixed,
            deny_all=deny_all,
            fingerprints=())
        runner = LifecycleRunner(active, (active, deny_all))

        samples = 512
        for _sample in range(samples):
            self.assertEqual(
                POLICY.verify_guarded_policy(loaded, runner), active)

        reads = [
            arguments for arguments, _input in runner.calls
            if arguments[:3] == ("--json", "list", "table")]
        mutations = [
            arguments for arguments, _input in runner.calls
            if arguments == ("--file", "-")]
        self.assertEqual(len(reads), samples)
        self.assertEqual(mutations, [])

    def test_guard_query_timeout_revokes_without_early_watchdog_credit(
            self) -> None:
        identities = identity_bytes()
        source_policy = policy_bytes(identities)
        active = POLICY.parse_policy(
            source_policy, identities,
            paper_identity_bytes(
                source_policy,
                [dedicated_connector("codex-a", 2121, 2121)]))
        fixed = POLICY.parse_policy(source_policy, identities)
        deny_all = POLICY._deny_all_policy(fixed)
        loaded = POLICY.LoadedPolicy(
            policy=active,
            fixed_only=fixed,
            deny_all=deny_all,
            fingerprints=())

        class TimeoutOnSecondSample(LifecycleRunner):
            def __init__(inner_self) -> None:
                super().__init__(active, (active, deny_all))
                inner_self.reads = 0

            def __call__(inner_self, arguments, standard_input):
                if arguments[:3] == ("--json", "list", "table"):
                    inner_self.reads += 1
                    if inner_self.reads == 2:
                        inner_self.calls.append((arguments, standard_input))
                        raise POLICY.PolicyError(
                            "nftables command execution failed")
                return super().__call__(arguments, standard_input)

        runner = TimeoutOnSecondSample()
        notifier = FakeNotifier()
        with self.assertRaisesRegex(
                POLICY.PolicyError,
                "detected drift and installed deny-all"):
            POLICY.supervise_policy(
                loaded, runner, notifier, threading.Event(),
                poll_interval=0.01)

        self.assertEqual(runner.active, deny_all)
        self.assertEqual(
            sum(message.count("WATCHDOG=1") for message in notifier.messages),
            1)
        validating = [
            message for message in notifier.messages
            if "broker boundary validating" in message]
        self.assertEqual(len(validating), 1)
        self.assertNotIn("WATCHDOG=1", validating[0])
        self.assertTrue(any(
            arguments == ("--file", "-")
            for arguments, _input in runner.calls))

    def test_clean_guard_stop_also_tightens_to_deny_all(self) -> None:
        identities = identity_bytes()
        source_policy = policy_bytes(identities)
        active = POLICY.parse_policy(
            source_policy, identities,
            paper_identity_bytes(
                source_policy,
                [dedicated_connector("codex-a", 2121, 2121)]))
        fixed = POLICY.parse_policy(source_policy, identities)
        deny_all = POLICY._deny_all_policy(fixed)
        loaded = POLICY.LoadedPolicy(
            policy=active,
            fixed_only=fixed,
            deny_all=deny_all,
            fingerprints=())
        runner = LifecycleRunner(active, (active, deny_all))
        stop = threading.Event()
        stop.set()
        POLICY.supervise_policy(
            loaded, runner, FakeNotifier(), stop, poll_interval=0.01)
        self.assertEqual(runner.active, deny_all)

    def test_source_drift_observed_with_stop_event_is_clean_shutdown(self) -> None:
        identities = identity_bytes()
        source_policy = policy_bytes(identities)
        active = POLICY.parse_policy(
            source_policy, identities,
            paper_identity_bytes(
                source_policy,
                [dedicated_connector("codex-a", 2121, 2121)]))
        fixed = POLICY.parse_policy(source_policy, identities)
        deny_all = POLICY._deny_all_policy(fixed)
        loaded = POLICY.LoadedPolicy(
            policy=active,
            fixed_only=fixed,
            deny_all=deny_all,
            fingerprints=(POLICY.SourceFingerprint(
                Path("/policy"), present=True, sha256="a" * 64),))
        runner = LifecycleRunner(active, (active, deny_all))
        stop = threading.Event()

        def stopping_checker(_fingerprint: object) -> bool:
            stop.set()
            return False

        POLICY.supervise_policy(
            loaded, runner, FakeNotifier(), stop, poll_interval=0.01,
            source_checker=stopping_checker)
        self.assertEqual(runner.active, deny_all)

    def test_query_failure_after_stop_event_is_clean_shutdown(self) -> None:
        identities = identity_bytes()
        source_policy = policy_bytes(identities)
        active = POLICY.parse_policy(
            source_policy, identities,
            paper_identity_bytes(
                source_policy,
                [dedicated_connector("codex-a", 2121, 2121)]))
        fixed = POLICY.parse_policy(source_policy, identities)
        deny_all = POLICY._deny_all_policy(fixed)
        loaded = POLICY.LoadedPolicy(
            policy=active,
            fixed_only=fixed,
            deny_all=deny_all,
            fingerprints=())
        stop = threading.Event()

        class StopDuringQuery(LifecycleRunner):
            def __init__(inner_self, *args):
                super().__init__(*args)
                inner_self.interrupted = False

            def __call__(inner_self, arguments, standard_input):
                if (
                        arguments[:3] == ("--json", "list", "table") and
                        not inner_self.interrupted):
                    inner_self.interrupted = True
                    stop.set()
                    raise POLICY.PolicyError("query interrupted")
                return super().__call__(arguments, standard_input)

        runner = StopDuringQuery(active, (active, deny_all))
        POLICY.supervise_policy(
            loaded, runner, FakeNotifier(), stop, poll_interval=0.01)
        self.assertEqual(runner.active, deny_all)

    def test_deny_all_supervisor_first_mutation_precedes_ready(self) -> None:
        identities = identity_bytes()
        fixed = POLICY.parse_policy(policy_bytes(identities), identities)
        deny_all = POLICY._deny_all_policy(fixed)
        loaded = POLICY.LoadedPolicy(
            policy=fixed,
            fixed_only=fixed,
            deny_all=deny_all,
            fingerprints=(),
            explicit_deny_all_authorization=True)
        runner = LifecycleRunner(fixed, (fixed, deny_all))

        class ReadinessNotifier(FakeNotifier):
            def send(inner_self, message: str) -> None:
                if "READY=1" in message:
                    self.assertEqual(runner.active, deny_all)
                super().send(message)

        notifier = ReadinessNotifier()
        stop = threading.Event()
        stop.set()
        POLICY.supervise_deny_all_policy(
            loaded, runner, notifier, stop, poll_interval=0.01)
        mutations = [
            standard_input for arguments, standard_input in runner.calls
            if arguments == ("--file", "-")]
        self.assertGreaterEqual(len(mutations), 2)
        self.assertEqual(
            mutations[0],
            POLICY.render_transaction(deny_all, replace_existing=True))
        assert mutations[0] is not None
        self.assertNotIn(b"meta skuid", mutations[0])
        self.assertTrue(any(
            "READY=1" in message for message in notifier.messages))
        self.assertEqual(runner.active, deny_all)

    def test_deny_all_supervisor_source_drift_prevents_ready(self) -> None:
        identities = identity_bytes()
        fixed = POLICY.parse_policy(policy_bytes(identities), identities)
        deny_all = POLICY._deny_all_policy(fixed)
        loaded = POLICY.LoadedPolicy(
            policy=fixed,
            fixed_only=fixed,
            deny_all=deny_all,
            fingerprints=(POLICY.SourceFingerprint(
                Path("/policy"), present=True, sha256="a" * 64),),
            explicit_deny_all_authorization=True)
        runner = LifecycleRunner(fixed, (fixed, deny_all))
        notifier = FakeNotifier()
        with self.assertRaisesRegex(
                POLICY.PolicyError, "reinstalled deny-all"):
            POLICY.supervise_deny_all_policy(
                loaded, runner, notifier, threading.Event(),
                poll_interval=0.01,
                source_checker=lambda _fingerprint: False)
        self.assertFalse(any(
            "READY=1" in message for message in notifier.messages))
        self.assertEqual(runner.active, deny_all)

    def test_deny_all_watchdog_rejects_drift_then_retightens(self) -> None:
        identities = identity_bytes()
        fixed = POLICY.parse_policy(policy_bytes(identities), identities)
        deny_all = POLICY._deny_all_policy(fixed)
        loaded = POLICY.LoadedPolicy(
            policy=fixed,
            fixed_only=fixed,
            deny_all=deny_all,
            fingerprints=(POLICY.SourceFingerprint(
                Path("/policy"), present=True, sha256="a" * 64),),
            explicit_deny_all_authorization=True)
        runner = LifecycleRunner(fixed, (fixed, deny_all))

        class WatchdogNotifier(FakeNotifier):
            watchdog_states: list[object]

            def __init__(inner_self) -> None:
                super().__init__()
                inner_self.watchdog_states = []

            def send(inner_self, message: str) -> None:
                if "WATCHDOG=1" in message:
                    inner_self.watchdog_states.append(runner.active)
                super().send(message)

        notifier = WatchdogNotifier()
        checks = 0

        def drift_live_table(_fingerprint: object) -> bool:
            nonlocal checks
            checks += 1
            if checks == 2:
                runner.active = fixed
            return True

        with self.assertRaisesRegex(
                POLICY.PolicyError, "reinstalled deny-all"):
            POLICY.supervise_deny_all_policy(
                loaded, runner, notifier, threading.Event(),
                poll_interval=0.01,
                source_checker=drift_live_table)
        self.assertEqual(notifier.watchdog_states, [deny_all])
        self.assertEqual(runner.active, deny_all)

    def test_deny_all_supervisor_rejects_unvalidated_bundle(self) -> None:
        identities = identity_bytes()
        fixed = POLICY.parse_policy(policy_bytes(identities), identities)
        deny_all = POLICY._deny_all_policy(fixed)
        loaded = POLICY.LoadedPolicy(
            policy=fixed,
            fixed_only=fixed,
            deny_all=deny_all,
            fingerprints=())
        runner = LifecycleRunner(fixed, (fixed, deny_all))
        with self.assertRaisesRegex(
                POLICY.PolicyError, "authorization state was not validated"):
            POLICY.supervise_deny_all_policy(
                loaded, runner, FakeNotifier(), threading.Event(),
                poll_interval=0.01)
        self.assertEqual(runner.calls, [])

    def test_emergency_deny_all_never_reads_mutable_inputs(self) -> None:
        policy = POLICY.load_deny_all_policy(
            Path("/missing-or-corrupt-policy.json"),
            Path("/missing-or-corrupt-identities.json"),
            require_installed_metadata=True)
        self.assertEqual(policy.family, "inet")
        self.assertEqual(policy.table, "hepta_broker_egress_v1")
        self.assertEqual(policy.chain, "output")
        self.assertEqual(policy.ports, (4001, 4002, 7496, 7497))
        self.assertEqual(policy.authorized_connectors, ())
        transaction = POLICY.render_transaction(policy)
        self.assertIn(
            b"delete table inet hepta_broker_egress_v1\n",
            transaction)
        self.assertNotIn(b"meta skuid", transaction)

    def test_atomic_manifest_inode_replacement_tightens_deny_all(
            self) -> None:
        identities = identity_bytes()
        source_policy = policy_bytes(identities)
        paper = paper_identity_bytes(
            source_policy,
            [dedicated_connector("codex-a", 2121, 2121)])
        with tempfile.TemporaryDirectory(
                prefix="hepta-broker-inode-drift-") as directory:
            root = Path(directory)
            identity_path = root / "identities.json"
            policy_path = root / "policy.json"
            paper_path = root / "paper.json"
            identity_path.write_bytes(identities)
            policy_path.write_bytes(source_policy)
            paper_path.write_bytes(paper)
            os.chmod(identity_path, 0o644)
            os.chmod(policy_path, 0o644)
            os.chmod(paper_path, 0o600)
            loaded = POLICY.load_policy_bundle(
                policy_path, identity_path, paper_path,
                require_installed_metadata=False)
            replacement = root / "replacement.json"
            replacement.write_bytes(paper)
            os.chmod(replacement, 0o600)
            os.replace(replacement, paper_path)
            runner = LifecycleRunner(
                loaded.policy, (loaded.policy, loaded.deny_all))

            with self.assertRaisesRegex(
                    POLICY.PolicyError, "installed deny-all"):
                POLICY.supervise_policy(
                    loaded, runner, FakeNotifier(), threading.Event(),
                    poll_interval=0.01,
                    source_checker=lambda fingerprint:
                    POLICY.source_fingerprint_matches(
                        fingerprint,
                        require_installed_metadata=False))
            self.assertEqual(runner.active, loaded.deny_all)


if __name__ == "__main__":
    unittest.main(verbosity=2)
