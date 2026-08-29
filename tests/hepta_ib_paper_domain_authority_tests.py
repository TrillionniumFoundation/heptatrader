#!/usr/bin/env python3

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts/hepta_ib_paper_domain_authority.py"
SPEC = importlib.util.spec_from_file_location(
    "hepta_ib_paper_domain_authority_under_test", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("cannot import per-domain PAPER authority helper")
AUTHORITY = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = AUTHORITY
SPEC.loader.exec_module(AUTHORITY)


class FakeNotifier:
    def __init__(self):
        self.messages: list[str] = []
        self.ready = threading.Event()

    def send(self, message: str) -> None:
        self.messages.append(message)
        if "READY=1" in message:
            self.ready.set()


def encoded(value: object) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":")) +
        "\n").encode("utf-8")


def network_manifest(records: list[dict[str, object]]) -> bytes:
    return encoded({
        "schema": "hepta.agent-trust-domain-paper-identities.v1",
        "version": 1,
        "source_policy_sha256": "sha256:" + "a" * 64,
        "paper_authorized": bool(records),
        "live_authorized": False,
        "identities": records,
    })


def network_record(domain: str, uid: int) -> dict[str, object]:
    return {
        "domain_id": domain,
        "identity": f"hepta-ib-exec-{domain}",
        "uid": uid,
        "gid": uid,
        "role": "ib-paper-execution-authority",
    }


def authority_record(domain: str, uid: int) -> dict[str, object]:
    control = f"/run/hepta/ib-paper-control-{domain}"
    return {
        "domain_id": domain,
        "identity": f"hepta-ib-exec-{domain}",
        "uid": uid,
        "gid": uid,
        "control_directory": control,
        "kill_switch_marker": control + "/kill-switch",
        "control_directory_mode": "0750",
        "kill_switch_mode": "0440",
        "kill_switch_initial_state": "engaged",
    }


def authority_manifest(
        network: bytes, records: list[dict[str, object]]) -> bytes:
    return encoded({
        "schema": "hepta.ib-paper-domain-authorizations.v1",
        "version": 1,
        "network_identity_manifest_sha256":
            "sha256:" + hashlib.sha256(network).hexdigest(),
        "paper_authorized": bool(records),
        "live_authorized": False,
        "authorizations": records,
    })


def create_test_owner(domain: str, path: Path) -> None:
    if path.exists():
        raise AUTHORITY.AuthorityError(
            "previous host PAPER authority finalization is incomplete")
    descriptor = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o600)
    try:
        os.write(descriptor, (domain + "\n").encode("ascii"))
    finally:
        os.close(descriptor)


def clear_test_owner(
        domain: str, path: Path, *, missing_ok: bool = False) -> None:
    try:
        observed = path.read_text(encoding="ascii").strip()
    except FileNotFoundError:
        if missing_ok:
            return
        raise
    if observed != domain:
        raise AUTHORITY.AuthorityError(
            "test owner tombstone belongs to another domain")
    path.unlink()


def read_test_owner(path: Path) -> str | None:
    try:
        return path.read_text(encoding="ascii").strip()
    except FileNotFoundError:
        return None


class PaperDomainAuthorityTests(unittest.TestCase):
    def test_activation_handoff_atomically_adopts_runtime_owner(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-paper-runtime-adopt-") as directory:
            root = Path(directory) / "authority"
            root.mkdir(mode=0o700)
            boot = Path(directory) / "boot-id"
            boot_id = "01234567-89ab-cdef-8123-456789abcdef"
            boot.write_text(boot_id + "\n", encoding="ascii")
            drop_in = Path(directory) / "20-local-paper.conf"
            drop_in_raw = b"[Service]\nExecStart=/reviewed\n"
            drop_in.write_bytes(drop_in_raw)
            drop_in.chmod(0o644)
            permit = Path(directory) / "permit.json"
            owner_path = root / "owner.v1"
            activation_id = "a" * 32
            digest = lambda letter: "sha256:" + letter * 64
            reservation = AUTHORITY._sealed_json({
                "schema": AUTHORITY.ACTIVATION_RESERVATION_SCHEMA,
                "version": 1, "status": "PENDING_BROKER_ACTIVE",
                "activation_id": activation_id,
                "issued_at_ms": 1_000_000,
                "expires_at_ms": 1_045_000, "boot_id": boot_id,
                "guardian_pid": 1, "guardian_start_ticks": 2,
                "guardian_exe_sha256": digest("1"),
                "guardian_argv_sha256": digest("2"),
                "control_image_sha256": digest("3"),
                "guardian_request_id": "b" * 32, "domain": "alpha",
                "transaction_id": "c" * 32, "operation": "ENABLE",
                "phase": "BEFORE_001_START_BROKER_LOCAL_PAPER",
                "request_sha256": digest("4"),
                "target_identity_manifest_sha256": digest("5"),
                "target_drop_in_sha256": AUTHORITY._sha256(drop_in_raw),
                "broker_start_permit_file_sha256": digest("6"),
                "broker_start_permit_body_sha256": digest("7"),
                "required_pre_activation_boundary": "DENY_ALL",
                "paper_only": True, "live_authorized": False,
            })
            owner_path.write_bytes(reservation)
            owner_path.chmod(0o600)
            reservation_doc = json.loads(reservation)
            consumed = AUTHORITY._sealed_json({
                "schema": AUTHORITY.ACTIVATION_CONSUMED_SCHEMA,
                "version": 1, "status": "ACTIVE_BOUNDARY_COMMITTED",
                "activation_id": activation_id,
                "consumed_at_ms": 1_000_001, "boot_id": boot_id,
                "reservation_file_sha256": AUTHORITY._sha256(reservation),
                "reservation_body_sha256": reservation_doc["body_sha256"],
                "broker_start_permit_file_sha256": digest("6"),
                "broker_start_permit_body_sha256": digest("7"),
                "guardian_request_id": "b" * 32,
                "transaction_id": "c" * 32, "operation": "ENABLE",
                "phase": "BEFORE_001_START_BROKER_LOCAL_PAPER",
                "request_sha256": digest("4"), "domain": "alpha",
                "target_identity_manifest_sha256": digest("5"),
                "target_drop_in_sha256": AUTHORITY._sha256(drop_in_raw),
                "control_image_sha256": digest("3"),
                "required_pre_activation_boundary": "DENY_ALL",
                "pre_activation_boundary_state_sha256": digest("8"),
                "active_boundary_status": "EXACT_ACTIVE",
                "active_boundary_state_sha256": digest("9"),
                "paper_authorized": True, "live_authorized": False,
            })
            consumed_path = root / (
                AUTHORITY.ACTIVATION_CONSUMED_PREFIX + activation_id +
                AUTHORITY.ACTIVATION_ARTIFACT_SUFFIX)
            consumed_path.write_bytes(consumed)
            consumed_path.chmod(0o600)
            item = AUTHORITY.DomainAuthority(
                "alpha", "hepta-ib-exec-alpha", 2121, 2121,
                "/run/hepta/ib-paper-control-alpha",
                "/run/hepta/ib-paper-control-alpha/kill-switch")
            fingerprint = AUTHORITY.ManifestFingerprint(
                Path("/network"), 1, 2, 0o100600, 1, 0, 0, 10, 1, 1,
                "5" * 64)
            process = {
                "pid": 42, "start_ticks": 43,
                "exe_sha256": digest("a"),
                "argv_sha256": digest("b"),
            }
            original_read = AUTHORITY._read_stable

            def read_stable(path: Path, **kwargs: object) -> bytes:
                if path == drop_in:
                    return drop_in_raw
                return original_read(path, **kwargs)

            with mock.patch.object(
                    AUTHORITY, "_read_stable", side_effect=read_stable):
                runtime, runtime_raw = AUTHORITY.adopt_runtime_owner(
                    item, fingerprint, owner_path,
                    drop_in_path=drop_in, boot_id_path=boot,
                    permit_path=permit, expected_uid=os.getuid(),
                    expected_gid=os.getgid(), now_ms=1_000_002,
                    process_identity_provider=lambda _pid: process)
            self.assertEqual(owner_path.read_bytes(), runtime_raw)
            self.assertEqual(runtime["status"], "ACTIVE_RUNTIME_GUARD")
            self.assertEqual(runtime["reservation_file_sha256"],
                             AUTHORITY._sha256(reservation))
            self.assertEqual(runtime["activation_consumed_file_sha256"],
                             AUTHORITY._sha256(consumed))
            self.assertFalse(consumed_path.exists())

    def test_default_examples_remain_unauthorized(self) -> None:
        network = (
            ROOT / "systemd/"
            "hepta-agent-trust-domain-paper-identities-v1.json.example"
        ).read_bytes()
        authority = (
            ROOT / "systemd/"
            "hepta-ib-paper-domain-authorizations-v1.json.example"
        ).read_bytes()
        self.assertEqual(
            AUTHORITY.parse_authorities(network, authority), ())

    def test_explicit_authorities_render_default_engaged_tmpfiles(self) -> None:
        network = network_manifest([
            network_record("codex-a", 2121),
        ])
        parsed = AUTHORITY.parse_authorities(
            network,
            authority_manifest(network, [
                authority_record("codex-a", 2121),
            ]))
        rendered = AUTHORITY.render_tmpfiles(parsed).decode("ascii")
        self.assertIn(
            "d /run/hepta/ib-paper-control-codex-a 0750 root "
            "hepta-ib-exec-codex-a -", rendered)
        self.assertIn(
            "f /run/hepta/ib-paper-control-codex-a/kill-switch "
            "0440 root hepta-ib-exec-codex-a - engaged", rendered)

    def test_second_templated_paper_domain_is_rejected(self) -> None:
        network = network_manifest([
            network_record("codex-a", 2121),
            network_record("openclaw-b", 2122),
        ])
        with self.assertRaisesRegex(
                AUTHORITY.AuthorityError,
                "broker network identity manifest contract mismatch"):
            AUTHORITY.parse_authorities(
                network,
                authority_manifest(network, [
                    authority_record("codex-a", 2121),
                    authority_record("openclaw-b", 2122),
                ]))

    def test_network_manifest_stays_strict_five_field(self) -> None:
        network = network_manifest([network_record("codex-a", 2121)])
        changed = network_record("codex-a", 2121)
        changed["kill_switch_marker"] = "/run/hepta/wrong"
        with self.assertRaisesRegex(
                AUTHORITY.AuthorityError, "strict five-field"):
            AUTHORITY.parse_authorities(
                network_manifest([changed]),
                authority_manifest(
                    network_manifest([changed]),
                    [authority_record("codex-a", 2121)]))

    def test_authority_is_digest_and_identity_bound(self) -> None:
        network = network_manifest([network_record("codex-a", 2121)])
        valid = json.loads(
            authority_manifest(
                network, [authority_record("codex-a", 2121)]))
        cases = []
        digest = dict(valid)
        digest["network_identity_manifest_sha256"] = "sha256:" + "0" * 64
        cases.append(digest)
        uid = json.loads(json.dumps(valid))
        uid["authorizations"][0]["uid"] = 2122
        cases.append(uid)
        marker = json.loads(json.dumps(valid))
        marker["authorizations"][0]["kill_switch_mode"] = "0640"
        cases.append(marker)
        for document in cases:
            with self.subTest(document=document), self.assertRaises(
                    AUTHORITY.AuthorityError):
                AUTHORITY.parse_authorities(network, encoded(document))

    def test_false_authority_cannot_carry_records(self) -> None:
        network = network_manifest([])
        document = json.loads(authority_manifest(network, []))
        document["authorizations"] = [authority_record("codex-a", 2121)]
        with self.assertRaises(AUTHORITY.AuthorityError):
            AUTHORITY.parse_authorities(network, encoded(document))

    def test_empty_authority_cannot_render_tmpfiles(self) -> None:
        with self.assertRaisesRegex(
                AUTHORITY.AuthorityError, "explicit PAPER"):
            AUTHORITY.render_tmpfiles(())

    def test_runtime_allows_secure_disarm_after_initial_engaged_check(
            self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-paper-kill-runtime-") as directory:
            control = Path(directory) / "control"
            control.mkdir(mode=0o750)
            control.chmod(0o750)
            self.assertEqual(stat.S_IMODE(control.stat().st_mode), 0o750)
            marker = control / "kill-switch"
            marker.write_bytes(b"engaged")
            marker.chmod(0o440)
            item = AUTHORITY.DomainAuthority(
                domain_id="codex-a",
                identity="hepta-ib-exec-codex-a",
                uid=2121,
                gid=2121,
                control_directory=str(control),
                kill_switch_marker=str(marker),
            )
            ownership = lambda _path, _metadata: (0, 2121)
            with mock.patch.object(
                    AUTHORITY, "_validate_identity",
                    lambda _item: None):
                AUTHORITY.validate_runtime(
                    item, ownership_provider=ownership)
                marker.unlink()
                AUTHORITY.validate_runtime_lifecycle(
                    item, ownership_provider=ownership)
                with self.assertRaisesRegex(
                        AUTHORITY.AuthorityError,
                        "not initially engaged"):
                    AUTHORITY.validate_runtime(
                        item, ownership_provider=ownership)
                marker.write_bytes(b"malformed")
                marker.chmod(0o440)
                with self.assertRaisesRegex(
                        AUTHORITY.AuthorityError,
                        "marker content mismatch"):
                    AUTHORITY.validate_runtime_lifecycle(
                        item, ownership_provider=ownership)

    def test_one_shot_operator_arms_watchdog_before_disarm_and_reengages(
            self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-paper-one-shot-") as directory:
            root = Path(directory)
            control = root / "control"
            control.mkdir(mode=0o750)
            control.chmod(0o750)
            marker = control / "kill-switch"
            marker.write_bytes(b"engaged")
            marker.chmod(0o440)
            item = AUTHORITY.DomainAuthority(
                domain_id="codex-a",
                identity="hepta-ib-exec-codex-a",
                uid=os.getuid(), gid=os.getgid(),
                control_directory=str(control),
                kill_switch_marker=str(marker))
            paths = AUTHORITY.OperatorPaths(
                root / "runtime", root / "receipts")
            actions: list[str] = []
            clock = iter((1000, 1001, 1002))

            def arm(*_arguments: object) -> str:
                self.assertTrue(marker.exists())
                actions.append("armed")
                return "hepta-test-watchdog"

            state = AUTHORITY.operator_disarm(
                item, "cycle-a", "sha256:" + "a" * 64, 20,
                paths=paths, watchdog=arm, now_ms=lambda: next(clock),
                root_uid=os.getuid(), root_gid=os.getgid())
            self.assertEqual(actions, ["armed"])
            self.assertEqual(state["status"], "disarmed")
            self.assertFalse(marker.exists())

            stopped: list[str] = []

            def stop(command: list[str], **_kwargs: object):
                stopped.append(command[-1])
                return mock.Mock(returncode=0)

            state = AUTHORITY.operator_reengage(
                item, "cycle-a", "sha256:" + "a" * 64,
                paths=paths, now_ms=lambda: next(clock),
                timer_stopper=stop, root_uid=os.getuid(),
                root_gid=os.getgid())
            self.assertEqual(state["status"], "engaged")
            self.assertEqual(state["reengage_source"], "operator")
            self.assertEqual(stopped, ["hepta-test-watchdog.timer"])
            self.assertEqual(marker.read_bytes(), b"engaged")
            self.assertEqual(marker.stat().st_mode & 0o777, 0o440)
            receipt = json.loads(next(paths.receipt_root.iterdir()).read_text())
            self.assertEqual(receipt["status"], "engaged")
            self.assertEqual(receipt["intent_sha256"], "sha256:" + "a" * 64)

    def test_one_shot_operator_never_disarms_without_watchdog(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-paper-one-shot-arm-fail-") as directory:
            root = Path(directory)
            control = root / "control"
            control.mkdir(mode=0o750)
            control.chmod(0o750)
            marker = control / "kill-switch"
            marker.write_bytes(b"engaged")
            marker.chmod(0o440)
            item = AUTHORITY.DomainAuthority(
                "codex-a", "hepta-ib-exec-codex-a",
                os.getuid(), os.getgid(), str(control), str(marker))

            def fail(*_arguments: object) -> str:
                raise AUTHORITY.AuthorityError("watchdog unavailable")

            with self.assertRaisesRegex(
                    AUTHORITY.AuthorityError, "watchdog unavailable"):
                AUTHORITY.operator_disarm(
                    item, "cycle-a", "sha256:" + "b" * 64, 20,
                    paths=AUTHORITY.OperatorPaths(
                        root / "runtime", root / "receipts"),
                    watchdog=fail, root_uid=os.getuid(),
                    root_gid=os.getgid())
            self.assertEqual(marker.read_bytes(), b"engaged")

    def test_one_shot_watchdog_is_systemd_owned_and_verified(self) -> None:
        item = AUTHORITY.DomainAuthority(
            "codex-a", "hepta-ib-exec-codex-a", 2121, 2121,
            "/run/hepta/ib-paper-control-codex-a",
            "/run/hepta/ib-paper-control-codex-a/kill-switch")
        calls: list[list[str]] = []

        def run(command: list[str], **_kwargs: object):
            calls.append(command)
            return mock.Mock(returncode=0, stdout="active\n", stderr="")

        unit = AUTHORITY._arm_operator_watchdog(
            item, "cycle-a", "sha256:" + "e" * 64, 20,
            "0123456789abcdef0123", runner=run)
        self.assertEqual(
            unit, "hepta-ib-paper-reengage-codex-a-0123456789abcdef0123")
        self.assertEqual(calls[0][0], "/usr/bin/systemd-run")
        self.assertIn("--on-active=20s", calls[0])
        self.assertIn("--operator-reengage", calls[0])
        self.assertIn("watchdog", calls[0])
        self.assertEqual(calls[1][0:2], [
            "/usr/bin/systemctl", "is-active"])
        self.assertTrue(calls[1][-1].endswith(".timer"))

    def test_one_shot_watchdog_verification_failure_is_fail_closed(self) -> None:
        item = AUTHORITY.DomainAuthority(
            "codex-a", "hepta-ib-exec-codex-a", 2121, 2121,
            "/run/hepta/ib-paper-control-codex-a",
            "/run/hepta/ib-paper-control-codex-a/kill-switch")
        count = 0

        def run(command: list[str], **_kwargs: object):
            nonlocal count
            count += 1
            if count == 2:
                raise AUTHORITY.subprocess.CalledProcessError(3, command)
            return mock.Mock(returncode=0, stdout="", stderr="")

        with self.assertRaisesRegex(
                AUTHORITY.AuthorityError, "watchdog did not arm"):
            AUTHORITY._arm_operator_watchdog(
                item, "cycle-a", "sha256:" + "f" * 64, 20,
                "0123456789abcdef0123", runner=run)

    def test_one_shot_operator_bounds_and_binds_every_window(self) -> None:
        item = AUTHORITY.DomainAuthority(
            "codex-a", "hepta-ib-exec-codex-a",
            os.getuid(), os.getgid(), "/unused", "/unused/kill-switch")
        with self.assertRaisesRegex(
                AUTHORITY.AuthorityError, "5-20 second"):
            AUTHORITY.operator_disarm(
                item, "cycle-a", "sha256:" + "c" * 64, 21)
        with self.assertRaisesRegex(
                AUTHORITY.AuthorityError, "cycle id"):
            AUTHORITY.operator_disarm(
                item, "../cycle", "sha256:" + "c" * 64, 20)
        with self.assertRaisesRegex(
                AUTHORITY.AuthorityError, "intent digest"):
            AUTHORITY.operator_disarm(item, "cycle-a", "c" * 64, 20)

    def test_watchdog_reengage_restores_marker_before_state_validation(
            self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-paper-one-shot-recover-") as directory:
            root = Path(directory)
            control = root / "control"
            control.mkdir(mode=0o750)
            control.chmod(0o750)
            item = AUTHORITY.DomainAuthority(
                "codex-a", "hepta-ib-exec-codex-a",
                os.getuid(), os.getgid(), str(control),
                str(control / "kill-switch"))
            paths = AUTHORITY.OperatorPaths(
                root / "runtime", root / "receipts")
            paths.runtime_root.mkdir(mode=0o700)
            paths.receipt_root.mkdir(mode=0o700)
            with self.assertRaisesRegex(
                    FileNotFoundError, "codex-a.json"):
                AUTHORITY.operator_reengage(
                    item, "cycle-a", "sha256:" + "d" * 64,
                    source="watchdog", paths=paths,
                    root_uid=os.getuid(), root_gid=os.getgid())
            self.assertEqual(
                (control / "kill-switch").read_bytes(), b"engaged")

    def test_host_lease_rejects_second_domain_until_first_stops(self) -> None:
        items = (
            AUTHORITY.DomainAuthority(
                domain_id="codex-a",
                identity="hepta-ib-exec-codex-a",
                uid=2121,
                gid=2121,
                control_directory="/run/hepta/ib-paper-control-codex-a",
                kill_switch_marker=(
                    "/run/hepta/ib-paper-control-codex-a/kill-switch"),
            ),
            AUTHORITY.DomainAuthority(
                domain_id="openclaw-b",
                identity="hepta-ib-exec-openclaw-b",
                uid=2122,
                gid=2122,
                control_directory="/run/hepta/ib-paper-control-openclaw-b",
                kill_switch_marker=(
                    "/run/hepta/ib-paper-control-openclaw-b/kill-switch"),
            ),
        )
        fingerprints = (
            AUTHORITY.ManifestFingerprint(
                Path("/network"), 1, 2, 0o100600, 1, 0, 0, 10, 1, 1,
                "a" * 64),
            AUTHORITY.ManifestFingerprint(
                Path("/authority"), 1, 3, 0o100600, 1, 0, 0, 10, 1, 1,
                "b" * 64),
        )
        with tempfile.TemporaryDirectory(
                prefix="hepta-paper-host-lease-") as directory:
            lock_directory = Path(directory) / "lock"
            lock_directory.mkdir(mode=0o700)
            lock_path = lock_directory / "lease.lock"

            def acquire(path: Path) -> int:
                return AUTHORITY.acquire_host_lease(
                    path,
                    ownership_provider=lambda _path, _metadata: (0, 0))

            stop_a = threading.Event()
            notifier_a = FakeNotifier()
            failures: list[BaseException] = []

            def run_a() -> None:
                try:
                    AUTHORITY.guard_authority(
                        items[0], fingerprints, notifier_a, stop_a,
                        lock_path=lock_path,
                        poll_interval=0.01,
                        fingerprint_checker=lambda _item: True,
                        startup_runtime_checker=lambda _item: None,
                        runtime_checker=lambda _item: None,
                        network_checker=lambda _path: None,
                        network_activator=lambda _path, _domain: None,
                        network_revoker=lambda: None,
                        lease_acquirer=acquire,
                        owner_creator=create_test_owner,
                        owner_clearer=clear_test_owner)
                except BaseException as error:
                    failures.append(error)

            thread = threading.Thread(target=run_a)
            thread.start()
            self.assertTrue(notifier_a.ready.wait(2))
            stop_b = threading.Event()
            stop_b.set()
            with self.assertRaisesRegex(
                    AUTHORITY.AuthorityError,
                    "another host PAPER authority"):
                AUTHORITY.guard_authority(
                    items[1], fingerprints, FakeNotifier(), stop_b,
                    lock_path=lock_path,
                    poll_interval=0.01,
                    fingerprint_checker=lambda _item: True,
                    startup_runtime_checker=lambda _item: None,
                    runtime_checker=lambda _item: None,
                    network_checker=lambda _path: None,
                    network_activator=lambda _path, _domain: None,
                    network_revoker=lambda: None,
                    lease_acquirer=acquire,
                    owner_creator=create_test_owner,
                    owner_clearer=clear_test_owner)
            stop_a.set()
            thread.join(timeout=2)
            self.assertFalse(thread.is_alive())
            self.assertEqual(failures, [])

            notifier_b = FakeNotifier()
            AUTHORITY.guard_authority(
                items[1], fingerprints, notifier_b, stop_b,
                lock_path=lock_path,
                poll_interval=0.01,
                fingerprint_checker=lambda _item: True,
                startup_runtime_checker=lambda _item: None,
                runtime_checker=lambda _item: None,
                network_checker=lambda _path: None,
                network_activator=lambda _path, _domain: None,
                network_revoker=lambda: None,
                lease_acquirer=acquire,
                owner_creator=create_test_owner,
                owner_clearer=clear_test_owner)
            self.assertTrue(notifier_b.ready.is_set())

    def test_manifest_drift_tightens_network_and_releases_lease(self) -> None:
        item = AUTHORITY.DomainAuthority(
            domain_id="codex-a",
            identity="hepta-ib-exec-codex-a",
            uid=2121,
            gid=2121,
            control_directory="/run/hepta/ib-paper-control-codex-a",
            kill_switch_marker=(
                "/run/hepta/ib-paper-control-codex-a/kill-switch"),
        )
        fingerprints = (
            AUTHORITY.ManifestFingerprint(
                Path("/network"), 1, 2, 0o100600, 1, 0, 0, 10, 1, 1,
                "a" * 64),
            AUTHORITY.ManifestFingerprint(
                Path("/authority"), 1, 3, 0o100600, 1, 0, 0, 10, 1, 1,
                "b" * 64),
        )
        with tempfile.TemporaryDirectory(
                prefix="hepta-paper-host-drift-") as directory:
            lock_directory = Path(directory) / "lock"
            lock_directory.mkdir(mode=0o700)
            lock_path = lock_directory / "lease.lock"
            revoked: list[bool] = []

            def acquire(path: Path) -> int:
                return AUTHORITY.acquire_host_lease(
                    path,
                    ownership_provider=lambda _path, _metadata: (0, 0))

            with self.assertRaisesRegex(
                    AUTHORITY.AuthorityError,
                    "revoked domain broker authority"):
                AUTHORITY.guard_authority(
                    item, fingerprints, FakeNotifier(), threading.Event(),
                    lock_path=lock_path,
                    poll_interval=0.01,
                    fingerprint_checker=lambda _item: False,
                    startup_runtime_checker=lambda _item: None,
                    runtime_checker=lambda _item: None,
                    network_checker=lambda _path: None,
                    network_activator=lambda _path, _domain: None,
                    network_revoker=lambda: revoked.append(True),
                    lease_acquirer=acquire,
                    owner_creator=create_test_owner,
                    owner_clearer=clear_test_owner)
            self.assertEqual(revoked, [True])
            self.assertFalse(lock_path.with_name(
                AUTHORITY.HOST_OWNER_PATH.name).exists())
            descriptor = acquire(lock_path)
            os.close(descriptor)

    def test_clean_guard_stop_revokes_domain_and_releases_lease(
            self) -> None:
        item = AUTHORITY.DomainAuthority(
            domain_id="codex-a",
            identity="hepta-ib-exec-codex-a",
            uid=2121,
            gid=2121,
            control_directory="/run/hepta/ib-paper-control-codex-a",
            kill_switch_marker=(
                "/run/hepta/ib-paper-control-codex-a/kill-switch"),
        )
        fingerprints = (
            AUTHORITY.ManifestFingerprint(
                Path("/network"), 1, 2, 0o100600, 1, 0, 0, 10, 1, 1,
                "a" * 64),
            AUTHORITY.ManifestFingerprint(
                Path("/authority"), 1, 3, 0o100600, 1, 0, 0, 10, 1, 1,
                "b" * 64),
        )
        with tempfile.TemporaryDirectory(
                prefix="hepta-paper-host-clean-stop-") as directory:
            lock_directory = Path(directory) / "lock"
            lock_directory.mkdir(mode=0o700)
            lock_path = lock_directory / "lease.lock"
            revoked: list[bool] = []
            stop = threading.Event()
            stop.set()

            def acquire(path: Path) -> int:
                return AUTHORITY.acquire_host_lease(
                    path,
                    ownership_provider=lambda _path, _metadata: (0, 0))

            AUTHORITY.guard_authority(
                item, fingerprints, FakeNotifier(), stop,
                lock_path=lock_path,
                poll_interval=0.01,
                fingerprint_checker=lambda _item: True,
                startup_runtime_checker=lambda _item: None,
                runtime_checker=lambda _item: None,
                network_checker=lambda _path: None,
                network_activator=lambda _path, _domain: None,
                network_revoker=lambda: revoked.append(True),
                lease_acquirer=acquire,
                owner_creator=create_test_owner,
                owner_clearer=clear_test_owner)
            self.assertEqual(revoked, [True])

    def test_revoke_accepts_verified_concurrent_deny_all(self) -> None:
        results = (
            mock.Mock(returncode=1, stderr=b"concurrent transaction"),
            mock.Mock(returncode=0, stderr=b""),
        )
        with mock.patch.object(
                AUTHORITY, "_network_command",
                side_effect=results) as network_command:
            AUTHORITY.revoke_live_network()
        self.assertEqual(
            [call.args[1] for call in network_command.call_args_list],
            ["revoke", "check-deny-all"])

    def test_revoke_skips_verification_after_direct_success(self) -> None:
        with mock.patch.object(
                AUTHORITY, "_network_command",
                return_value=mock.Mock(
                    returncode=0, stderr=b"")) as network_command:
            AUTHORITY.revoke_live_network()
        network_command.assert_called_once_with(
            AUTHORITY.DEFAULT_NETWORK_IDENTITIES, "revoke")

    def test_revoke_rejects_unverified_deny_all(self) -> None:
        results = (
            mock.Mock(returncode=1, stderr=b"concurrent transaction"),
            mock.Mock(returncode=1, stderr=b"policy mismatch"),
        )
        with mock.patch.object(
                AUTHORITY, "_network_command", side_effect=results):
            with self.assertRaisesRegex(
                    AUTHORITY.AuthorityError,
                    "deny-all broker network revocation failed"):
                AUTHORITY.revoke_live_network()

    def test_guard_treats_concurrent_stop_during_network_check_as_clean(
            self) -> None:
        item = AUTHORITY.DomainAuthority(
            domain_id="codex-a", identity="hepta-ib-exec-codex-a",
            uid=2121, gid=2121,
            control_directory="/run/hepta/ib-paper-control-codex-a",
            kill_switch_marker=(
                "/run/hepta/ib-paper-control-codex-a/kill-switch"),
        )
        fingerprints = (
            AUTHORITY.ManifestFingerprint(
                Path("/network"), 1, 2, 0o100600, 1, 0, 0, 10, 1, 1,
                "a" * 64),
            AUTHORITY.ManifestFingerprint(
                Path("/authority"), 1, 3, 0o100600, 1, 0, 0, 10, 1, 1,
                "b" * 64),
        )
        with tempfile.TemporaryDirectory(
                prefix="hepta-paper-host-stop-race-") as directory:
            lock_directory = Path(directory) / "lock"
            lock_directory.mkdir(mode=0o700)
            lock_path = lock_directory / "lease.lock"
            stop = threading.Event()
            checks: list[int] = []
            revoked: list[bool] = []
            notifier = FakeNotifier()

            def acquire(path: Path) -> int:
                return AUTHORITY.acquire_host_lease(
                    path,
                    ownership_provider=lambda _path, _metadata: (0, 0))

            def check_network(_path: Path) -> None:
                checks.append(1)
                if len(checks) == 3:
                    stop.set()
                    raise AUTHORITY.AuthorityError("deny-all during stop")

            AUTHORITY.guard_authority(
                item, fingerprints, notifier, stop,
                lock_path=lock_path,
                poll_interval=0.01,
                fingerprint_checker=lambda _item: True,
                startup_runtime_checker=lambda _item: None,
                runtime_checker=lambda _item: None,
                network_checker=check_network,
                network_activator=lambda _path, _domain: None,
                network_revoker=lambda: revoked.append(True),
                lease_acquirer=acquire,
                owner_creator=create_test_owner,
                owner_clearer=clear_test_owner)
            self.assertEqual(len(checks), 3)
            self.assertEqual(revoked, [True])
            self.assertTrue(any(
                "PAPER host lease validating codex-a" in message
                for message in notifier.messages))
            self.assertTrue(any(
                "PAPER network boundary validating codex-a" in message
                for message in notifier.messages))
            self.assertFalse(lock_path.with_name(
                AUTHORITY.HOST_OWNER_PATH.name).exists())
            descriptor = acquire(lock_path)
            os.close(descriptor)

    def test_crash_tombstone_blocks_competing_domain_until_finalizer(
            self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-paper-crash-owner-") as directory:
            root = Path(directory)
            lock_path = root / "lease.lock"
            owner_path = root / "owner.v1"

            def acquire(
                    path: Path, *, nonblocking: bool = True) -> int:
                return AUTHORITY.acquire_host_lease(
                    path,
                    nonblocking=nonblocking,
                    ownership_provider=lambda _path, _metadata: (0, 0))

            crashed_lease = acquire(lock_path)
            create_test_owner("codex-a", owner_path)
            os.close(crashed_lease)
            revoked: list[str] = []
            item_b = AUTHORITY.DomainAuthority(
                domain_id="openclaw-b",
                identity="hepta-ib-exec-openclaw-b",
                uid=2122,
                gid=2122,
                control_directory="/run/hepta/ib-paper-control-openclaw-b",
                kill_switch_marker=(
                    "/run/hepta/ib-paper-control-openclaw-b/kill-switch"),
            )
            fingerprints = (
                AUTHORITY.ManifestFingerprint(
                    Path("/network"), 1, 2, 0o100600, 1, 0, 0, 10, 1, 1,
                    "a" * 64),
                AUTHORITY.ManifestFingerprint(
                    Path("/authority"), 1, 3, 0o100600, 1, 0, 0, 10, 1, 1,
                    "b" * 64),
            )
            with self.assertRaisesRegex(
                    AUTHORITY.AuthorityError,
                    "previous host PAPER authority finalization is incomplete"):
                AUTHORITY.guard_authority(
                    item_b,
                    fingerprints,
                    FakeNotifier(),
                    threading.Event(),
                    lock_path=lock_path,
                    poll_interval=0.01,
                    fingerprint_checker=lambda _item: True,
                    startup_runtime_checker=lambda _item: None,
                    runtime_checker=lambda _item: None,
                    network_activator=lambda _path, _domain: None,
                    network_checker=lambda _path: None,
                    network_revoker=lambda: revoked.append("blocked-b"),
                    lease_acquirer=acquire,
                    owner_creator=create_test_owner,
                    owner_clearer=clear_test_owner)
            self.assertEqual(revoked, ["blocked-b"])
            self.assertTrue(owner_path.exists())

            AUTHORITY.finalize_stop(
                "codex-a",
                lock_path=lock_path,
                owner_path=owner_path,
                network_revoker=lambda: revoked.append("finalize-a"),
                lease_acquirer=acquire,
                owner_reader=read_test_owner,
                owner_clearer=clear_test_owner)
            self.assertEqual(revoked, ["blocked-b", "finalize-a"])
            self.assertFalse(owner_path.exists())

    def test_finalizer_waits_for_host_lease_then_always_revokes(
            self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-paper-finalizer-lock-") as directory:
            root = Path(directory)
            lock_path = root / "lease.lock"
            owner_path = root / "owner.v1"

            def acquire(
                    path: Path, *, nonblocking: bool = True) -> int:
                return AUTHORITY.acquire_host_lease(
                    path,
                    nonblocking=nonblocking,
                    ownership_provider=lambda _path, _metadata: (0, 0))

            held = acquire(lock_path)
            create_test_owner("codex-a", owner_path)
            revoked = threading.Event()
            failures: list[BaseException] = []

            def finalize() -> None:
                try:
                    AUTHORITY.finalize_stop(
                        "codex-a",
                        lock_path=lock_path,
                        owner_path=owner_path,
                        network_revoker=revoked.set,
                        lease_acquirer=acquire,
                        owner_reader=read_test_owner,
                        owner_clearer=clear_test_owner)
                except BaseException as error:
                    failures.append(error)

            thread = threading.Thread(target=finalize)
            thread.start()
            time.sleep(0.05)
            self.assertFalse(revoked.is_set())
            os.close(held)
            thread.join(timeout=2)
            self.assertFalse(thread.is_alive())
            self.assertEqual(failures, [])
            self.assertTrue(revoked.is_set())
            self.assertFalse(owner_path.exists())

    def test_foreign_domain_finalizer_is_immediate_and_side_effect_free(
            self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-paper-foreign-finalizer-") as directory:
            root = Path(directory)
            lock_path = root / "lease.lock"
            owner_path = root / "owner.v1"
            create_test_owner("codex-a", owner_path)
            actions: list[str] = []

            AUTHORITY.finalize_stop(
                "openclaw-b",
                lock_path=lock_path,
                owner_path=owner_path,
                network_revoker=lambda: actions.append("revoked"),
                lease_acquirer=lambda *_args, **_kwargs: (
                    actions.append("acquired") or -1),
                owner_reader=read_test_owner,
                owner_clearer=lambda *_args, **_kwargs:
                    actions.append("cleared"))

            self.assertEqual(actions, [])
            self.assertEqual(read_test_owner(owner_path), "codex-a")

    def test_owner_absent_busy_finalizer_is_immediate_and_side_effect_free(
            self) -> None:
        actions: list[str] = []

        def busy(_path: Path, *, nonblocking: bool = True) -> int:
            self.assertTrue(nonblocking)
            actions.append("busy")
            raise AUTHORITY.LeaseBusyError(
                "another host PAPER authority already holds the lease")

        AUTHORITY.finalize_stop(
            "openclaw-b",
            lock_path=Path("/unused/lease.lock"),
            owner_path=Path("/unused/owner.v1"),
            network_revoker=lambda: actions.append("revoked"),
            lease_acquirer=busy,
            owner_reader=lambda _path: None,
            owner_clearer=lambda *_args, **_kwargs:
                actions.append("cleared"))
        self.assertEqual(actions, ["busy"])

    def test_malformed_owner_revokes_but_retains_lockout(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-paper-malformed-owner-") as directory:
            owner_path = Path(directory) / "owner.v1"
            owner_path.write_bytes(b"malformed-owner")
            actions: list[str] = []

            def malformed(_path: Path) -> str | None:
                raise AUTHORITY.AuthorityError(
                    "host authority owner tombstone content mismatch")

            with self.assertRaisesRegex(
                    AUTHORITY.AuthorityError,
                    "owner tombstone content mismatch"):
                AUTHORITY.finalize_stop(
                    "codex-a",
                    lock_path=Path(directory) / "lease.lock",
                    owner_path=owner_path,
                    network_revoker=lambda: actions.append("revoked"),
                    lease_acquirer=lambda *_args, **_kwargs:
                        (actions.append("acquired") or -1),
                    owner_reader=malformed,
                    owner_clearer=lambda *_args, **_kwargs:
                        actions.append("cleared"))
            self.assertEqual(actions, ["revoked"])
            self.assertEqual(owner_path.read_bytes(), b"malformed-owner")

    def test_malformed_lease_revokes_without_clearing_owner(self) -> None:
        actions: list[str] = []

        def malformed(
                _path: Path, *, nonblocking: bool = True) -> int:
            self.assertFalse(nonblocking)
            raise AUTHORITY.AuthorityError(
                "host authority lock metadata mismatch")

        with self.assertRaisesRegex(
                AUTHORITY.AuthorityError,
                "host authority lock metadata mismatch"):
            AUTHORITY.finalize_stop(
                "codex-a",
                lock_path=Path("/unused/lease.lock"),
                owner_path=Path("/unused/owner.v1"),
                network_revoker=lambda: actions.append("revoked"),
                lease_acquirer=malformed,
                owner_reader=lambda _path: "codex-a",
                owner_clearer=lambda *_args, **_kwargs:
                    actions.append("cleared"))
        self.assertEqual(actions, ["revoked"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
