#!/usr/bin/env python3

import io
import multiprocessing
import os
from contextlib import redirect_stderr
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_hepta_shadow_install_manifest as builder  # noqa: E402
import hepta_shadow_host_installer as installer  # noqa: E402


PAPER_IDENTITY_ENTRY = {
    "name": installer.PAPER_IDENTITY_MANIFEST.as_posix(),
    "payload": installer.PAPER_IDENTITY_MANIFEST_BYTES,
    "mode": 0o600,
}
INSTALLER_PATH = ROOT / "scripts/hepta_shadow_host_installer.py"


def installer_entry(payload: bytes | None = None) -> dict[str, object]:
    return {
        "name": installer.INSTALLER_MEMBER.as_posix(),
        "payload": INSTALLER_PATH.read_bytes() if payload is None else payload,
        "mode": 0o755,
    }


def installer_record(payload: bytes | None = None) -> dict[str, object]:
    value = INSTALLER_PATH.read_bytes() if payload is None else payload
    return {
        "path": installer.INSTALLER_MEMBER.as_posix(),
        "mode": "0755",
        "size": len(value),
        "sha256": installer.digest_bytes(value),
    }


def identity_record() -> dict[str, object]:
    return {
        "path": installer.PAPER_IDENTITY_MANIFEST.as_posix(),
        "mode": "0600",
        "size": len(installer.PAPER_IDENTITY_MANIFEST_BYTES),
        "sha256": installer.PAPER_IDENTITY_MANIFEST_SHA256,
    }


def manifest_document(
        records: list[dict[str, object]]) -> dict[str, object]:
    return {
        "schema": installer.MANIFEST_SCHEMA,
        "version": installer.MANIFEST_VERSION,
        "archive_sha256": "sha256:" + "1" * 64,
        "source_baseline_sha256": "sha256:" + "2" * 64,
        "installer_sha256": installer.digest_file(INSTALLER_PATH),
        "files": records,
        "paper_authorized": False,
        "live_authorized": False,
        "mutation_attempted": False,
        "direct_broker_access": False,
    }


def safe_preflight() -> dict[str, object]:
    return {
        "domain": "alpha",
        "paper_units": {unit: "inactive" for unit in installer.PAPER_UNITS},
        "installation_blocking_units": {
            unit: "inactive"
            for unit in installer.INSTALLATION_BLOCKING_UNITS
        },
        "campaign_policy_count": 0,
        "kill_switch_engaged": True,
        "broker_egress_deny_all": True,
    }


def lock_evidence(created: bool = False) -> dict[str, object]:
    return {
        "path": "/var/lib/hepta/.shadow-runtime-install.lock",
        "device": 11,
        "inode": 22,
        "nlink": 1,
        "uid": 0,
        "gid": 0,
        "mode": "0600",
        "size": 0,
        "mtime_ns": 33,
        "ctime_ns": 44,
        "created_during_transaction": created,
        "persistent": True,
        "held_during_transaction": True,
    }


def write_archive(path: Path, entries: list[dict[str, object]]) -> None:
    with tarfile.open(path, "w:gz", format=tarfile.PAX_FORMAT) as handle:
        for entry in entries:
            info = tarfile.TarInfo(str(entry["name"]))
            info.uid = int(entry.get("uid", 0))
            info.gid = int(entry.get("gid", 0))
            info.mtime = int(entry.get("mtime", 0))
            info.mode = int(entry.get("mode", 0o644))
            payload = bytes(entry.get("payload", b""))
            kind = entry.get("kind", "file")
            if kind == "file":
                info.size = len(payload)
                handle.addfile(info, io.BytesIO(payload))
            elif kind == "symlink":
                info.type = tarfile.SYMTYPE
                info.linkname = str(entry.get("linkname", "/tmp/escape"))
                handle.addfile(info)
            elif kind == "directory":
                info.type = tarfile.DIRTYPE
                info.mode = int(entry.get("mode", 0o755))
                handle.addfile(info)
            else:
                raise AssertionError("unsupported fixture kind")


def transaction_lock_worker(
        lock_path: str, start, release, results) -> None:
    start.wait()
    try:
        lock = installer._acquire_transaction_lock(
            Path(lock_path), owner_uid=os.geteuid(), owner_gid=os.getegid(),
            strict_ancestors=False)
    except installer.InstallError as error:
        results.put(("error", str(error)))
        return
    try:
        results.put(("ok", lock[5]))
        release.wait()
    finally:
        installer._release_transaction_lock(lock)


def consumer_lock_validation_patch(
        evidence: dict[str, object], lock_path: Path):
    """Keep consumer tests rootless while retaining the strict lock contract."""

    expected_uid = os.geteuid()
    expected_gid = os.getegid()
    real_validate = installer.validate_transaction_lock_evidence

    def validate(value, expected_path=installer.TRANSACTION_LOCK_PATH):
        if (
                not isinstance(value, dict) or value.get("uid") != expected_uid or
                value.get("gid") != expected_gid or value != evidence or
                expected_path not in {installer.TRANSACTION_LOCK_PATH, lock_path}):
            raise installer.InstallError(
                "INSTALL_TRANSACTION_LOCK_EVIDENCE_INVALID")
        root_owned = dict(value)
        root_owned["uid"] = 0
        root_owned["gid"] = 0
        real_validate(root_owned, lock_path)
        return value

    return mock.patch.object(
        installer, "validate_transaction_lock_evidence",
        side_effect=validate)


def create_consumer_fixture(root: Path) -> dict[str, object]:
    """Create a small, fully real passive-install closure under one temp root."""

    state_root = root / "state"
    filesystem_root = root / "filesystem"
    state_root.mkdir(mode=0o700)
    filesystem_root.mkdir(mode=0o700)
    lock_path = state_root / ".shadow-runtime-install.lock"
    lock_options = {
        "owner_uid": os.geteuid(),
        "owner_gid": os.getegid(),
        "strict_ancestors": False,
    }
    transaction_lock = installer._acquire_transaction_lock(
        lock_path, **lock_options)
    try:
        transaction_lock_evidence = installer._transaction_lock_evidence(
            transaction_lock)
    finally:
        installer._release_transaction_lock(transaction_lock)

    installer_payload = b"#!/usr/bin/env python3\n# frozen consumer fixture\n"
    payloads = {
        installer.PAPER_IDENTITY_MANIFEST.as_posix():
            installer.PAPER_IDENTITY_MANIFEST_BYTES,
        "usr/bin/hepta-consumer-alpha": b"#!/bin/sh\nexit 0\n",
        installer.INSTALLER_MEMBER.as_posix(): installer_payload,
        "usr/share/doc/heptatrader/consumer-readme.txt": b"passive only\n",
        "usr/share/heptatrader/consumer-zeta.json": b"{}\n",
    }
    modes = {
        installer.PAPER_IDENTITY_MANIFEST.as_posix(): 0o600,
        "usr/bin/hepta-consumer-alpha": 0o755,
        installer.INSTALLER_MEMBER.as_posix(): 0o755,
        "usr/share/doc/heptatrader/consumer-readme.txt": 0o644,
        "usr/share/heptatrader/consumer-zeta.json": 0o644,
    }
    records = [{
        "path": path,
        "mode": f"{modes[path]:04o}",
        "size": len(payload),
        "sha256": installer.digest_bytes(payload),
    } for path, payload in payloads.items()]
    self_sorted_records = sorted(records, key=lambda record: record["path"])
    manifest = manifest_document(self_sorted_records)
    manifest["installer_sha256"] = installer.digest_bytes(installer_payload)

    for path, payload in payloads.items():
        target = filesystem_root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        target.chmod(modes[path])

    manifest_path = state_root / "runtime.manifest.json"
    manifest_payload = installer.canonical_bytes(manifest)
    manifest_path.write_bytes(manifest_payload)
    manifest_path.chmod(0o600)

    backup_root = state_root / "backups/round-consumer"
    installed_paths = [record["path"] for record in self_sorted_records]
    with consumer_lock_validation_patch(
            transaction_lock_evidence, lock_path):
        receipt = installer.build_install_receipt(
            finished_at_ms=123,
            domain="alpha",
            expected_archive_sha256=manifest["archive_sha256"],
            expected_baseline_sha256=manifest["source_baseline_sha256"],
            expected_installer_sha256=manifest["installer_sha256"],
            installed=installed_paths,
            backup_root=backup_root,
            replaced=[],
            absent=installed_paths,
            preflight_before=safe_preflight(),
            preflight_after=safe_preflight(),
            transaction_lock_evidence=transaction_lock_evidence,
            receipt_reader_gid=installer.RECEIPT_READER_GID,
            install_generation=1,
            predecessor_install_generation=0,
            predecessor_current_install_pointer_file_sha256="absent",
        )
    receipt_path = state_root / "install.receipt.json"
    receipt_payload = installer.canonical_bytes(receipt)
    receipt_path.write_bytes(receipt_payload)
    receipt_path.chmod(0o440)

    current_pointer_path = state_root / "current-install-v1.json"
    current_pointer = installer.build_current_install_pointer(
        generation=1,
        domain="alpha",
        backup_root=backup_root,
        manifest_path=manifest_path,
        manifest_payload=manifest_payload,
        manifest=manifest,
        receipt_path=receipt_path,
        receipt_payload=receipt_payload,
        receipt=receipt,
        current_pointer_path=current_pointer_path,
        lock_path=lock_path,
    )
    current_pointer_payload = installer.canonical_bytes(current_pointer)
    current_pointer_path.write_bytes(current_pointer_payload)
    current_pointer_path.chmod(0o600)

    return {
        "state_root": state_root,
        "filesystem_root": filesystem_root,
        "lock_path": lock_path,
        "lock_options": lock_options,
        "lock_evidence": transaction_lock_evidence,
        "manifest": manifest,
        "manifest_path": manifest_path,
        "receipt": receipt,
        "receipt_path": receipt_path,
        "current_pointer": current_pointer,
        "current_pointer_payload": current_pointer_payload,
        "current_pointer_path": current_pointer_path,
        "backup_root": backup_root,
        "payloads": payloads,
        "modes": modes,
        "paths": installed_paths,
    }


def consumer_acquire_arguments(fixture: dict[str, object]) -> dict[str, object]:
    manifest_path = fixture["manifest_path"]
    receipt_path = fixture["receipt_path"]
    assert isinstance(manifest_path, Path)
    assert isinstance(receipt_path, Path)
    return {
        "receipt_path": receipt_path,
        "manifest_path": manifest_path,
        "expected_domain": "alpha",
        "expected_backup_root": fixture["backup_root"],
        "expected_manifest_sha256": installer.digest_bytes(
            manifest_path.read_bytes()),
        "expected_receipt_sha256": installer.digest_bytes(
            receipt_path.read_bytes()),
        "filesystem_root": fixture["filesystem_root"],
        "lock_path": fixture["lock_path"],
        "current_install_pointer_path": fixture["current_pointer_path"],
        "lock_owner_uid": os.geteuid(),
        "lock_owner_gid": os.getegid(),
        "runtime_uid": os.geteuid(),
        "runtime_gid": os.getegid(),
        "receipt_reader_gid": installer.RECEIPT_READER_GID,
        "strict_ancestors": False,
        "expected_file_count": len(fixture["paths"]),
    }


def replace_consumer_lock(fixture: dict[str, object]) -> None:
    lock_path = fixture["lock_path"]
    assert isinstance(lock_path, Path)
    saved = lock_path.with_name(lock_path.name + ".saved")
    lock_path.rename(saved)
    lock_path.write_bytes(b"")
    lock_path.chmod(0o600)


def receipt_with_recomputed_body(document: dict[str, object]) -> bytes:
    body = dict(document)
    body.pop("body_sha256", None)
    body["body_sha256"] = installer.digest_bytes(
        installer.canonical_bytes(body))
    return installer.canonical_bytes(body)


def current_pointer_with_recomputed_body(
        document: dict[str, object]) -> bytes:
    body = dict(document)
    body.pop("body_sha256", None)
    body["body_sha256"] = installer.digest_bytes(
        installer.canonical_bytes(body))
    return installer.canonical_bytes(body)


def build_fixture_current_pointer(
        fixture: dict[str, object],
        generation: int,
        *,
        current_pointer_path: Path | None = None,
        manifest_path: Path | None = None,
        receipt_path: Path | None = None,
        backup_root: Path | None = None,
) -> dict[str, object]:
    manifest = fixture["manifest"]
    receipt = fixture["receipt"]
    fixture_manifest_path = fixture["manifest_path"]
    fixture_receipt_path = fixture["receipt_path"]
    fixture_pointer_path = fixture["current_pointer_path"]
    lock_path = fixture["lock_path"]
    assert isinstance(manifest, dict)
    assert isinstance(receipt, dict)
    assert isinstance(fixture_manifest_path, Path)
    assert isinstance(fixture_receipt_path, Path)
    assert isinstance(fixture_pointer_path, Path)
    assert isinstance(lock_path, Path)
    selected_manifest_path = manifest_path or fixture_manifest_path
    selected_receipt_path = receipt_path or fixture_receipt_path
    return installer.build_current_install_pointer(
        generation=generation,
        domain="alpha",
        backup_root=backup_root or fixture["backup_root"],
        manifest_path=selected_manifest_path,
        manifest_payload=installer.canonical_bytes(manifest),
        manifest=manifest,
        receipt_path=selected_receipt_path,
        receipt_payload=installer.canonical_bytes(receipt),
        receipt=receipt,
        current_pointer_path=current_pointer_path or fixture_pointer_path,
        lock_path=lock_path,
    )


def root_owned_metadata(metadata):
    """Project real rootless stat fields into the root-owned test contract."""

    projected = mock.Mock()
    for field in (
            "st_dev", "st_ino", "st_mode", "st_nlink", "st_size",
            "st_mtime_ns", "st_ctime_ns"):
        setattr(projected, field, getattr(metadata, field))
    projected.st_uid = 0
    projected.st_gid = 0
    return projected


class InstallerTests(unittest.TestCase):
    def test_allows_only_parent_directories_not_parent_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            good = root / "good.tar.gz"
            write_archive(good, [
                {"name": "usr", "kind": "directory", "mode": 0o755},
                {"name": "usr/lib", "kind": "directory", "mode": 0o755},
                {"name": "usr/libexec", "kind": "directory", "mode": 0o755},
                {"name": "usr/libexec/probe", "payload": b"x"},
            ])
            path_records = installer.archive_records(good)
            byte_records = installer.archive_records_bytes(good.read_bytes())
            self.assertEqual(set(path_records[0]), set(byte_records[0]))
            self.assertEqual(path_records[1], byte_records[1])
            with self.assertRaisesRegex(
                    installer.InstallError, "INSTALL_ARCHIVE_INVALID"):
                installer.archive_records_bytes(b"not a gzip archive")
            bad = root / "bad.tar.gz"
            write_archive(bad, [{"name": "usr/lib", "payload": b"x"}])
            with self.assertRaisesRegex(
                    installer.InstallError, "FILE_PATH_NOT_ALLOWED"):
                installer.archive_records(bad)
    def test_manifest_and_archive_are_bound(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "runtime.tar.gz"
            write_archive(archive, [{
                "name": "usr/libexec/hepta-passive-probe",
                "payload": b"#!/bin/sh\nexit 0\n",
                "mode": 0o755,
            }, PAPER_IDENTITY_ENTRY, installer_entry()])
            document = builder.build(
                archive, "sha256:" + "1" * 64,
                ROOT / "scripts/hepta_shadow_host_installer.py")
            self.assertEqual(
                installer.verify_archive(archive, document)[
                    "usr/libexec/hepta-passive-probe"],
                b"#!/bin/sh\nexit 0\n")
            archive.write_bytes(archive.read_bytes() + b"tamper")
            with self.assertRaisesRegex(
                    installer.InstallError, "INSTALL_ARCHIVE_DIGEST_MISMATCH"):
                installer.verify_archive(archive, document)

    def test_rejects_unsafe_members(self) -> None:
        cases = (
            ({"name": "../escape", "payload": b"x"}, "PATH_INVALID"),
            ({"name": "usr/libexec/link", "kind": "symlink"}, "TYPE_INVALID"),
            ({"name": "usr/libexec/file", "payload": b"x", "uid": 1},
             "OWNER_INVALID"),
            ({"name": "usr/libexec/file", "payload": b"x", "mtime": 1},
             "MTIME_INVALID"),
            ({"name": "usr/libexec/hepta-ib-executiond", "payload": b"x"},
             "MUTATION_SURFACE_FORBIDDEN"),
        )
        for entry, reason in cases:
            with self.subTest(reason=reason), tempfile.TemporaryDirectory() as temporary:
                archive = Path(temporary) / "bad.tar.gz"
                write_archive(archive, [entry])
                with self.assertRaisesRegex(installer.InstallError, reason):
                    installer.archive_records(archive)

    def test_manifest_rejects_authority_and_duplicates(self) -> None:
        base = {
            "schema": installer.MANIFEST_SCHEMA,
            "version": installer.MANIFEST_VERSION,
            "archive_sha256": "sha256:" + "1" * 64,
            "source_baseline_sha256": "sha256:" + "2" * 64,
            "installer_sha256": installer.digest_file(INSTALLER_PATH),
            "files": [{
                "path": "usr/libexec/a", "mode": "0755", "size": 1,
                "sha256": "sha256:" + "4" * 64,
            }, {
                "path": installer.PAPER_IDENTITY_MANIFEST.as_posix(),
                "mode": "0600",
                "size": len(installer.PAPER_IDENTITY_MANIFEST_BYTES),
                "sha256": installer.PAPER_IDENTITY_MANIFEST_SHA256,
            }, installer_record()],
            "paper_authorized": True,
            "live_authorized": False,
            "mutation_attempted": False,
            "direct_broker_access": False,
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "manifest.json"
            path.write_bytes(installer.canonical_bytes(base))
            with self.assertRaisesRegex(installer.InstallError, "BOUNDARY_INVALID"):
                installer.load_manifest(path)
            base["paper_authorized"] = False
            base["files"].append(dict(base["files"][0]))
            path.write_bytes(installer.canonical_bytes(base))
            with self.assertRaisesRegex(installer.InstallError, "DUPLICATE_PATH"):
                installer.load_manifest(path)

    def test_default_deny_identity_record_is_mandatory_and_exact(self) -> None:
        cases: list[tuple[str, dict[str, object], str]] = []
        missing = manifest_document([{
            "path": "usr/libexec/a", "mode": "0755", "size": 1,
            "sha256": "sha256:" + "4" * 64,
        }])
        cases.append(("missing", missing, "PAPER_IDENTITY_MISSING"))
        for label, field, value, reason in (
                ("mode", "mode", "0644", "MODE_INVALID"),
                ("size", "size", 256, "PAPER_IDENTITY_DRIFT"),
                ("digest", "sha256", "sha256:" + "9" * 64,
                 "PAPER_IDENTITY_DRIFT")):
            record = identity_record()
            record[field] = value
            cases.append((
                label, manifest_document([record]), reason))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for label, document, reason in cases:
                with self.subTest(label=label):
                    path = root / f"{label}.json"
                    path.write_bytes(installer.canonical_bytes(document))
                    with self.assertRaisesRegex(installer.InstallError, reason):
                        installer.load_manifest(path)

    def test_manifest_builder_rejects_byte_different_false_identity(
            self) -> None:
        alternate = (
            b'{"identities":[],"live_authorized":false,'
            b'"paper_authorized":false,'
            b'"schema":"hepta.agent-trust-domain-paper-identities.v1",'
            b'"source_policy_sha256":'
            b'"sha256:08d430d53e4813cd0a43a23beeb92344af2130dca425814cbf7285059d90f90c",'
            b'"version":1}\n')
        with tempfile.TemporaryDirectory() as temporary:
            archive = Path(temporary) / "alternate.tar.gz"
            write_archive(archive, [{
                "name": installer.PAPER_IDENTITY_MANIFEST.as_posix(),
                "payload": alternate,
                "mode": 0o600,
            }])
            with self.assertRaisesRegex(
                    installer.InstallError, "PAPER_IDENTITY_DRIFT"):
                builder.build(
                    archive, "sha256:" + "1" * 64,
                    ROOT / "scripts/hepta_shadow_host_installer.py")

    def test_manifest_v2_types_digests_order_and_installer_binding_are_strict(
            self) -> None:
        valid = manifest_document([identity_record(), installer_record()])
        cases: list[tuple[str, dict[str, object], str]] = []
        old_schema = dict(valid)
        old_schema["schema"] = "hepta.shadow-runtime-install-manifest.v1"
        cases.append(("old-schema", old_schema, "FIELDS_INVALID"))
        bool_version = dict(valid)
        bool_version["version"] = True
        cases.append(("bool-version", bool_version, "VERSION_INVALID"))
        bad_top_digest = dict(valid)
        bad_top_digest["archive_sha256"] = "sha256:" + "A" * 64
        cases.append(("top-digest", bad_top_digest, "DIGEST_INVALID"))
        non_string_path = dict(valid)
        non_string_path["files"] = [
            {**identity_record(), "path": 7}, installer_record()]
        cases.append(("path-type", non_string_path, "FILE_INVALID"))
        bool_size = dict(valid)
        bool_size["files"] = [
            {**identity_record(), "size": True}, installer_record()]
        cases.append(("bool-size", bool_size, "SIZE_INVALID"))
        bad_file_digest = dict(valid)
        bad_file_digest["files"] = [
            identity_record(),
            {**installer_record(), "sha256": "sha256:" + "g" * 64}]
        cases.append(("file-digest", bad_file_digest, "DIGEST_INVALID"))
        unsorted = dict(valid)
        unsorted["files"] = [installer_record(), identity_record()]
        cases.append(("order", unsorted, "PATH_ORDER_INVALID"))
        missing_installer = manifest_document([identity_record()])
        cases.append((
            "missing-installer", missing_installer,
            "INSTALLER_BINDING_INVALID"))
        mismatched_installer = dict(valid)
        mismatched_installer["installer_sha256"] = "sha256:" + "9" * 64
        cases.append((
            "installer-binding", mismatched_installer,
            "INSTALLER_BINDING_INVALID"))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for label, document, reason in cases:
                with self.subTest(label=label):
                    path = root / f"{label}.json"
                    path.write_bytes(installer.canonical_bytes(document))
                    with self.assertRaisesRegex(installer.InstallError, reason):
                        installer.load_manifest(path)

    def test_archive_rejects_any_other_etc_leaf_and_identity_mode_drift(
            self) -> None:
        cases = (
            ({"name": "etc/heptatrader/extra.json", "payload": b"{}\n"},
             "PATH_NOT_ALLOWED"),
            ({**PAPER_IDENTITY_ENTRY, "mode": 0o644},
             "FILE_MODE_INVALID"),
        )
        for entry, reason in cases:
            with self.subTest(reason=reason), tempfile.TemporaryDirectory() as temporary:
                archive = Path(temporary) / "bad.tar.gz"
                write_archive(archive, [entry])
                with self.assertRaisesRegex(installer.InstallError, reason):
                    installer.archive_records(archive)

    def test_parent_symlink_cannot_escape_identity_install(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination = root / "destination"
            outside = root / "outside"
            destination.mkdir()
            outside.mkdir()
            (destination / "etc").symlink_to(
                outside, target_is_directory=True)
            backup = root / "backup"
            backup.mkdir(mode=0o700)
            manifest = manifest_document([identity_record()])
            payloads = {
                installer.PAPER_IDENTITY_MANIFEST.as_posix():
                    installer.PAPER_IDENTITY_MANIFEST_BYTES}
            with self.assertRaises(installer.InstallError):
                installer._install_payloads(
                    manifest, payloads, backup,
                    destination_root=destination,
                    owner_uid=os.geteuid(), owner_gid=os.getegid(),
                    strict_ancestors=False)
            self.assertFalse((outside / "heptatrader").exists())

    def test_final_symlink_and_hardlink_are_rejected_without_outside_write(
            self) -> None:
        for kind in ("symlink", "hardlink"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                destination = root / "destination"
                target_parent = destination / "etc/heptatrader"
                target_parent.mkdir(parents=True)
                outside = root / "outside.json"
                outside.write_bytes(b"outside\n")
                outside.chmod(0o600)
                target = target_parent / installer.PAPER_IDENTITY_MANIFEST.name
                if kind == "symlink":
                    target.symlink_to(outside)
                else:
                    os.link(outside, target)
                backup = root / "backup"
                backup.mkdir(mode=0o700)
                manifest = manifest_document([identity_record()])
                payloads = {
                    installer.PAPER_IDENTITY_MANIFEST.as_posix():
                        installer.PAPER_IDENTITY_MANIFEST_BYTES}
                with self.assertRaises(installer.InstallError):
                    installer._install_payloads(
                        manifest, payloads, backup,
                        destination_root=destination,
                        owner_uid=os.geteuid(), owner_gid=os.getegid(),
                        strict_ancestors=False)
                self.assertEqual(outside.read_bytes(), b"outside\n")

    def test_destination_replacement_is_detected_and_tightened(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination = root / "destination"
            target = (
                destination / installer.PAPER_IDENTITY_MANIFEST.as_posix())
            target.parent.mkdir(parents=True)
            old = b'{"paper_authorized":true}\n'
            target.write_bytes(old)
            target.chmod(0o600)
            backup = root / "backup"
            backup.mkdir(mode=0o700)
            manifest = manifest_document([identity_record()])
            payloads = {
                installer.PAPER_IDENTITY_MANIFEST.as_posix():
                    installer.PAPER_IDENTITY_MANIFEST_BYTES}
            real_write = installer._atomic_write_at
            raced = False

            def replace_before_write(*args, **kwargs):
                nonlocal raced
                parent_path, _parent, name = args[:3]
                if parent_path == target.parent and name == target.name and not raced:
                    raced = True
                    target.unlink()
                    target.write_bytes(b"racer\n")
                    target.chmod(0o600)
                return real_write(*args, **kwargs)

            with mock.patch.object(
                    installer, "_atomic_write_at",
                    side_effect=replace_before_write), self.assertRaisesRegex(
                        installer.InstallError, "DESTINATION_REBOUND"):
                installer._install_payloads(
                    manifest, payloads, backup,
                    destination_root=destination,
                    owner_uid=os.geteuid(), owner_gid=os.getegid(),
                    strict_ancestors=False)
            self.assertTrue(raced)
            self.assertEqual(
                target.read_bytes(), installer.PAPER_IDENTITY_MANIFEST_BYTES)
            self.assertEqual(
                (backup / installer.PAPER_IDENTITY_MANIFEST.as_posix()).read_bytes(),
                old)

    def test_later_failure_keeps_deny_all_and_rolls_back_other_file(
            self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination = root / "destination"
            identity = (
                destination / installer.PAPER_IDENTITY_MANIFEST.as_posix())
            probe = destination / "usr/libexec/probe"
            identity.parent.mkdir(parents=True)
            probe.parent.mkdir(parents=True)
            old_identity = b'{"paper_authorized":true}\n'
            identity.write_bytes(old_identity)
            identity.chmod(0o600)
            probe.write_bytes(b"old probe\n")
            probe.chmod(0o644)
            records = [identity_record(), {
                "path": "usr/libexec/probe", "mode": "0644",
                "size": len(b"new probe\n"),
                "sha256": installer.digest_bytes(b"new probe\n"),
            }]
            manifest = manifest_document(records)
            payloads = {
                installer.PAPER_IDENTITY_MANIFEST.as_posix():
                    installer.PAPER_IDENTITY_MANIFEST_BYTES,
                "usr/libexec/probe": b"new probe\n",
            }
            backup = root / "backup"
            backup.mkdir(mode=0o700)
            real_write = installer._atomic_write_at
            failed = False

            def fail_after_probe_replace(*args, **kwargs):
                nonlocal failed
                result = real_write(*args, **kwargs)
                if (
                        args[2] == "probe" and args[0] == probe.parent and
                        not failed):
                    failed = True
                    raise installer.InstallError("INJECTED_AFTER_REPLACE")
                return result

            with mock.patch.object(
                    installer, "_atomic_write_at",
                    side_effect=fail_after_probe_replace), self.assertRaisesRegex(
                        installer.InstallError, "INJECTED_AFTER_REPLACE"):
                installer._install_payloads(
                    manifest, payloads, backup,
                    destination_root=destination,
                    owner_uid=os.geteuid(), owner_gid=os.getegid(),
                    strict_ancestors=False)
            self.assertEqual(
                identity.read_bytes(), installer.PAPER_IDENTITY_MANIFEST_BYTES)
            self.assertEqual(probe.read_bytes(), b"old probe\n")
            self.assertEqual(
                (backup / installer.PAPER_IDENTITY_MANIFEST.as_posix()).read_bytes(),
                old_identity)

    def test_existing_target_exchange_rejects_racer_without_destroying_it(
            self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent_path = Path(temporary)
            target = parent_path / "target"
            saved = parent_path / "original.saved"
            original = b"original\n"
            racer = b"racer\n"
            target.write_bytes(original)
            target.chmod(0o600)
            parent = installer._open_anchored_directory(
                parent_path, create=False, owner_uid=os.geteuid(),
                owner_gid=os.getegid(), strict_ancestors=False)
            expected_payload, expected = installer._read_at(parent, "target")
            real_exchange = installer._rename_exchange
            injected = False

            def exchange_with_racer(descriptor, left, right):
                nonlocal injected
                if not injected:
                    injected = True
                    target.rename(saved)
                    target.write_bytes(racer)
                    target.chmod(0o600)
                return real_exchange(descriptor, left, right)

            try:
                with mock.patch.object(
                        installer, "_rename_exchange",
                        side_effect=exchange_with_racer), \
                        self.assertRaisesRegex(
                            installer.InstallError, "DESTINATION_REBOUND"):
                    installer._atomic_write_at(
                        parent_path, parent, "target", b"candidate\n", 0o600,
                        owner_uid=os.geteuid(), owner_gid=os.getegid(),
                        expected=expected, expected_payload=expected_payload,
                        reason="INSTALL_DESTINATION_WRITE_FAILED",
                        strict_ancestors=False)
            finally:
                os.close(parent)
            self.assertTrue(injected)
            self.assertEqual(target.read_bytes(), racer)
            self.assertEqual(saved.read_bytes(), original)

    def test_create_only_publication_preserves_destination_racer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent_path = Path(temporary)
            parent = installer._open_anchored_directory(
                parent_path, create=False, owner_uid=os.geteuid(),
                owner_gid=os.getegid(), strict_ancestors=False)
            real_rename = installer._rename_noreplace
            raced = False

            def publish_racer(
                    old_parent, source, new_parent, destination):
                nonlocal raced
                if not raced:
                    raced = True
                    target = parent_path / os.fspath(destination)
                    target.write_bytes(b"racer\n")
                    target.chmod(0o440)
                return real_rename(
                    old_parent, source, new_parent, destination)

            try:
                with mock.patch.object(
                        installer, "_rename_noreplace",
                        side_effect=publish_racer), \
                        self.assertRaisesRegex(
                            installer.InstallError, "DESTINATION_REBOUND"):
                    installer._atomic_write_at(
                        parent_path, parent, "receipt.json", b"receipt\n", 0o440,
                        owner_uid=os.geteuid(), owner_gid=os.getegid(),
                        expected=None, expected_payload=None,
                        reason="INSTALL_RECEIPT_PUBLISH_FAILED",
                        strict_ancestors=False)
            finally:
                os.close(parent)
            self.assertTrue(raced)
            self.assertEqual(
                (parent_path / "receipt.json").read_bytes(), b"racer\n")

    def test_receipt_cleanup_failure_cannot_skip_payload_rollback(self) -> None:
        rollback = mock.Mock()
        with mock.patch.object(
                installer, "_remove_exact_receipt",
                side_effect=installer.InstallError("RECEIPT_REBOUND")), \
                mock.patch.object(
                    installer, "_rollback_payloads", rollback), \
                self.assertRaisesRegex(
                    installer.InstallError, "RECEIPT_ROLLBACK_FAILED"):
            installer._rollback_after_receipt_failure(
                installer.InstallError("PUBLISH_FAILED"),
                receipt_output=Path("/var/lib/hepta/receipt.json"),
                receipt_payload=b"receipt\n", reader_gid=1000,
                manifest=manifest_document([identity_record()]),
                payloads={
                    installer.PAPER_IDENTITY_MANIFEST.as_posix():
                        installer.PAPER_IDENTITY_MANIFEST_BYTES},
                backup_root=Path("/var/lib/hepta/backups/one"),
                installed=[installer.PAPER_IDENTITY_MANIFEST.as_posix()],
                replaced=[installer.PAPER_IDENTITY_MANIFEST.as_posix()],
                absent=[])
        rollback.assert_called_once()

    def test_failed_payload_rollback_retains_candidate_pointer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            pointer = Path(temporary) / "current-install-v1.json"
            candidate = b'{"generation":2}\n'
            old = b'{"generation":1}\n'
            pointer.write_bytes(candidate)
            previous = installer.CurrentInstallPointerState(
                payload=old,
                metadata=pointer.stat(),
                document={"generation": 1})
            restore = mock.Mock()
            with mock.patch.object(
                    installer, "_validate_transaction_lock"), \
                    mock.patch.object(
                        installer, "_remove_exact_receipt"), \
                    mock.patch.object(
                        installer, "_rollback_payloads",
                        side_effect=installer.InstallError(
                            "INSTALL_PAYLOAD_ROLLBACK_FAILED")), \
                    mock.patch.object(
                        installer, "_restore_current_install_pointer",
                        restore), \
                    self.assertRaisesRegex(
                        installer.InstallError,
                        "INSTALL_RECEIPT_ROLLBACK_FAILED"):
                installer._rollback_after_receipt_failure(
                    installer.InstallError("INSTALL_POINTER_PUBLISH_FAILED"),
                    receipt_output=Path(temporary) / "receipt.json",
                    receipt_payload=b"receipt\n", reader_gid=1000,
                    manifest=manifest_document([identity_record()]),
                    payloads={
                        installer.PAPER_IDENTITY_MANIFEST.as_posix():
                            installer.PAPER_IDENTITY_MANIFEST_BYTES},
                    backup_root=Path(temporary) / "backup",
                    installed=[
                        installer.PAPER_IDENTITY_MANIFEST.as_posix()],
                    replaced=[
                        installer.PAPER_IDENTITY_MANIFEST.as_posix()],
                    absent=[], transaction_lock=object(),
                    current_pointer_path=pointer,
                    current_pointer_payload=candidate,
                    previous_current_pointer=previous)
            restore.assert_not_called()
            self.assertEqual(pointer.read_bytes(), candidate)

    def test_install_outputs_cannot_overlap_payloads_or_escape_state_root(
            self) -> None:
        manifest = manifest_document([identity_record()])
        receipt = Path("/var/lib/hepta/receipts/round91.json")
        for backup in (
                Path("/etc/heptatrader/"
                     "hepta-agent-trust-domain-paper-identities-v1.json"),
                Path("/tmp/round91-backup")):
            with self.subTest(backup=backup), self.assertRaisesRegex(
                    installer.InstallError, "OUTPUT_PATH_INVALID"):
                installer._prepare_install_outputs(backup, receipt, manifest)

    def test_install_output_dot_segments_are_canonicalized_before_checks(
            self) -> None:
        manifest = manifest_document([identity_record()])
        cases = (
            (Path("/var/lib/hepta/backups/../../../../etc/heptatrader/x"),
             Path("/var/lib/hepta/receipts/round91.json")),
            (Path("/var/lib/hepta/backups/round91"),
             Path("/var/lib/hepta/receipts/../../../../etc/heptatrader/x")),
        )
        for backup, receipt in cases:
            with self.subTest(backup=backup, receipt=receipt), \
                    mock.patch.object(
                        installer, "_open_anchored_directory") as opened, \
                    self.assertRaisesRegex(
                        installer.InstallError, "OUTPUT_PATH_INVALID"):
                installer._prepare_install_outputs(backup, receipt, manifest)
            opened.assert_not_called()

    def test_existing_receipt_is_detected_before_backup_root_creation(
            self) -> None:
        manifest = manifest_document([identity_record()])
        backup = Path("/var/lib/hepta/backups/round91-new")
        receipt = Path("/var/lib/hepta/receipts/round91-existing.json")

        def endpoint_state(_descriptor, name):
            if name == receipt.name:
                return mock.sentinel.existing_receipt
            return None

        with mock.patch.object(
                installer, "_open_anchored_directory",
                side_effect=(71, 72)), \
                mock.patch.object(
                    installer, "_stat_optional",
                    side_effect=endpoint_state), \
                mock.patch.object(installer.os, "mkdir") as mkdir, \
                mock.patch.object(installer.os, "close"), \
                self.assertRaisesRegex(
                    installer.InstallError, "OUTPUT_EXISTS"):
            installer._prepare_install_outputs(backup, receipt, manifest)
        mkdir.assert_not_called()

    def test_receipt_reader_gid_is_exact_and_required(self) -> None:
        self.assertEqual(installer.validate_receipt_reader_gid(1000), 1000)
        for value in (0, 999, 1001, True, 1000.0, "1000"):
            with self.subTest(value=value), self.assertRaisesRegex(
                    installer.InstallError, "RECEIPT_READER_GID_INVALID"):
                installer.validate_receipt_reader_gid(value)  # type: ignore[arg-type]
        self.assertEqual(installer.receipt_reader_gid_argument("1000"), 1000)
        for value in ("0", "1001", "not-an-integer"):
            with self.subTest(value=value), self.assertRaises(
                    installer.argparse.ArgumentTypeError):
                installer.receipt_reader_gid_argument(value)
        arguments = [
            "--archive", "/archive.tar.gz",
            "--manifest", "/manifest.json",
            "--archive-sha256", "sha256:" + "1" * 64,
            "--source-baseline-sha256", "sha256:" + "2" * 64,
            "--installer-sha256", "sha256:" + "3" * 64,
            "--expected-current-install-generation", "0",
            "--expected-current-install-pointer-sha256", "absent",
            "--backup-root", "/backup",
            "--receipt-output", "/receipt.json",
            "--domain", "alpha",
        ]
        with redirect_stderr(io.StringIO()), self.assertRaises(
                SystemExit) as missing:
            installer.argument_parser().parse_args(arguments)
        self.assertEqual(missing.exception.code, 2)
        with redirect_stderr(io.StringIO()), self.assertRaises(
                SystemExit) as invalid:
            installer.argument_parser().parse_args(
                arguments + ["--receipt-reader-gid", "1001"])
        self.assertEqual(invalid.exception.code, 2)
        parsed = installer.argument_parser().parse_args(
            arguments + ["--receipt-reader-gid", "1000"])
        self.assertEqual(parsed.receipt_reader_gid, 1000)
        self.assertEqual(parsed.expected_current_install_generation, 0)
        self.assertEqual(
            parsed.expected_current_install_pointer_sha256, "absent")
        for value in ("-1", str(installer.MAX_INSTALL_GENERATION + 1), "x"):
            with self.subTest(generation=value), redirect_stderr(
                    io.StringIO()), self.assertRaises(SystemExit):
                installer.argument_parser().parse_args([
                    *arguments,
                    "--expected-current-install-generation", value,
                    "--receipt-reader-gid", "1000",
                ])

    def test_receipt_v4_contract_and_body_digest_are_exact(self) -> None:
        receipt = installer.build_install_receipt(
            finished_at_ms=123,
            domain="alpha",
            expected_archive_sha256="sha256:" + "1" * 64,
            expected_baseline_sha256="sha256:" + "2" * 64,
            expected_installer_sha256="sha256:" + "3" * 64,
            installed=[
                installer.PAPER_IDENTITY_MANIFEST.as_posix(),
                "usr/libexec/a"],
            backup_root=Path("/var/lib/hepta/backups/receipt-test"),
            replaced=[],
            absent=[
                installer.PAPER_IDENTITY_MANIFEST.as_posix(),
                "usr/libexec/a"],
            preflight_before=safe_preflight(),
            preflight_after=safe_preflight(),
            transaction_lock_evidence=lock_evidence(),
            receipt_reader_gid=1000,
            install_generation=1,
            predecessor_install_generation=0,
            predecessor_current_install_pointer_file_sha256="absent",
        )
        self.assertEqual(
            set(receipt),
            {
                "schema", "version", "finished_at_ms", "domain",
                "archive_sha256", "source_baseline_sha256",
                "installer_sha256", "installed_file_count",
                "installed_paths_sha256", "backup_root",
                "replaced_file_count", "new_file_count", "reader_gid",
                "install_generation", "predecessor_install_generation",
                "predecessor_current_install_pointer_file_sha256",
                "default_deny_identity_manifest", "transaction_lock",
                "preflight_before", "preflight_after",
                "preflight_continuity_claimed",
                "paper_authorized", "live_authorized",
                "mutation_attempted", "direct_broker_access",
                "services_started", "services_enabled", "status",
                "body_sha256",
            },
        )
        self.assertEqual(
            receipt["schema"],
            "hepta.shadow-runtime-install-receipt.v4",
        )
        self.assertEqual(receipt["version"], 4)
        self.assertEqual(receipt["install_generation"], 1)
        self.assertEqual(receipt["predecessor_install_generation"], 0)
        self.assertEqual(
            receipt["predecessor_current_install_pointer_file_sha256"],
            "absent")
        self.assertEqual(receipt["reader_gid"], 1000)
        self.assertEqual(
            receipt["transaction_lock"],
            lock_evidence())
        self.assertIs(receipt["preflight_continuity_claimed"], False)
        self.assertEqual(receipt["preflight_before"], safe_preflight())
        self.assertEqual(receipt["preflight_after"], safe_preflight())
        self.assertEqual(
            receipt["default_deny_identity_manifest"],
            {
                "destination": "/" +
                    installer.PAPER_IDENTITY_MANIFEST.as_posix(),
                "archive_path":
                    installer.PAPER_IDENTITY_MANIFEST.as_posix(),
                "uid": 0,
                "gid": 0,
                "mode": "0600",
                "size": 257,
                "sha256": installer.PAPER_IDENTITY_MANIFEST_SHA256,
                "installed": True,
                "preexisting_backed_up": False,
                "new_file": True,
            })
        body = dict(receipt)
        body_sha256 = body.pop("body_sha256")
        self.assertEqual(
            body_sha256,
            installer.digest_bytes(installer.canonical_bytes(body)),
        )

    def test_receipt_v4_rejects_inconsistent_path_partitions(self) -> None:
        identity = installer.PAPER_IDENTITY_MANIFEST.as_posix()
        base = {
            "finished_at_ms": 123,
            "domain": "alpha",
            "expected_archive_sha256": "sha256:" + "1" * 64,
            "expected_baseline_sha256": "sha256:" + "2" * 64,
            "expected_installer_sha256": "sha256:" + "3" * 64,
            "backup_root": Path("/var/lib/hepta/backups/receipt-test"),
            "preflight_before": safe_preflight(),
            "preflight_after": safe_preflight(),
            "transaction_lock_evidence": lock_evidence(),
            "receipt_reader_gid": 1000,
            "install_generation": 1,
            "predecessor_install_generation": 0,
            "predecessor_current_install_pointer_file_sha256": "absent",
        }
        cases = (
            ([identity], [identity], [identity]),
            ([identity, "usr/libexec/a"], [], [identity]),
            (["usr/libexec/a", identity], [], ["usr/libexec/a", identity]),
            ([identity], [], []),
        )
        for installed, replaced, absent in cases:
            with self.subTest(
                    installed=installed, replaced=replaced, absent=absent), \
                    self.assertRaises(installer.InstallError):
                installer.build_install_receipt(
                    **base, installed=installed, replaced=replaced, absent=absent)

    def test_receipt_v4_lineage_is_exact_and_gap_free(self) -> None:
        predecessor = "sha256:" + "a" * 64
        installer.validate_install_receipt_lineage(1, 0, "absent")
        installer.validate_install_receipt_lineage(8, 7, predecessor)
        for current, prior, pointer in (
                (1, 0, predecessor),
                (2, 1, "absent"),
                (3, 1, predecessor),
                (2, 2, predecessor),
                (2, 1, "sha256:" + "A" * 64),
                (True, 0, "absent")):
            with self.subTest(
                    current=current, prior=prior, pointer=pointer), \
                    self.assertRaisesRegex(
                        installer.InstallError,
                        "INSTALL_RECEIPT_LINEAGE_INVALID"):
                installer.validate_install_receipt_lineage(
                    current, prior, pointer)

    def test_current_pointer_rejects_receipt_generation_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = create_consumer_fixture(Path(temporary))
            receipt = dict(fixture["receipt"])
            receipt["install_generation"] = 2
            receipt["predecessor_install_generation"] = 1
            receipt[
                "predecessor_current_install_pointer_file_sha256"] = (
                    "sha256:" + "b" * 64)
            receipt_payload = receipt_with_recomputed_body(receipt)
            manifest = fixture["manifest"]
            assert isinstance(manifest, dict)
            manifest_path = fixture["manifest_path"]
            receipt_path = fixture["receipt_path"]
            backup_root = fixture["backup_root"]
            current_pointer_path = fixture["current_pointer_path"]
            lock_path = fixture["lock_path"]
            assert isinstance(manifest_path, Path)
            assert isinstance(receipt_path, Path)
            assert isinstance(backup_root, Path)
            assert isinstance(current_pointer_path, Path)
            assert isinstance(lock_path, Path)
            pointer = installer.build_current_install_pointer(
                generation=1, domain="alpha", backup_root=backup_root,
                manifest_path=manifest_path,
                manifest_payload=manifest_path.read_bytes(),
                manifest=manifest, receipt_path=receipt_path,
                receipt_payload=receipt_payload, receipt=receipt,
                current_pointer_path=current_pointer_path,
                lock_path=lock_path)
            with self.assertRaisesRegex(
                    installer.InstallError,
                    "INSTALL_CURRENT_POINTER_MISMATCH"):
                installer._validate_current_install_pointer_binding(
                    pointer, manifest_path=manifest_path,
                    manifest_payload=manifest_path.read_bytes(),
                    manifest=manifest, receipt_path=receipt_path,
                    receipt_payload=receipt_payload, receipt=receipt,
                    expected_domain="alpha", expected_backup_root=backup_root,
                    current_pointer_path=current_pointer_path,
                    lock_path=lock_path)

    def test_receipt_v4_rejects_safe_but_different_preflight_samples(
            self) -> None:
        before = safe_preflight()
        after = safe_preflight()
        after["paper_units"] = dict(after["paper_units"])
        after["paper_units"][installer.PAPER_UNITS[0]] = "failed"
        with self.assertRaisesRegex(
                installer.InstallError, "PREFLIGHT_DRIFT"):
            installer.build_install_receipt(
                finished_at_ms=123, domain="alpha",
                expected_archive_sha256="sha256:" + "1" * 64,
                expected_baseline_sha256="sha256:" + "2" * 64,
                expected_installer_sha256="sha256:" + "3" * 64,
                installed=[
                    installer.PAPER_IDENTITY_MANIFEST.as_posix()],
                backup_root=Path("/var/lib/hepta/backups/receipt-test"),
                replaced=[],
                absent=[installer.PAPER_IDENTITY_MANIFEST.as_posix()],
                preflight_before=before, preflight_after=after,
                transaction_lock_evidence=lock_evidence(),
                receipt_reader_gid=1000,
                install_generation=1,
                predecessor_install_generation=0,
                predecessor_current_install_pointer_file_sha256="absent")

    def test_safety_preflight_rejects_every_active_shadow_authority_early(
            self) -> None:
        for target_index, target_unit in enumerate(
                installer.INSTALLATION_BLOCKING_UNITS):
            for active_state in ("active", "activating"):
                with self.subTest(
                        unit=target_unit, state=active_state):
                    def command_result(arguments):
                        unit = arguments[2]
                        state = (
                            active_state if unit == target_unit else
                            "inactive")
                        return mock.Mock(
                            stdout=state + "\n", stderr="", returncode=0)

                    expected_calls = [
                        mock.call([
                            "/usr/bin/systemctl", "is-active", unit])
                        for unit in installer.PAPER_UNITS
                    ] + [
                        mock.call([
                            "/usr/bin/systemctl", "is-active", unit])
                        for unit in installer.INSTALLATION_BLOCKING_UNITS[
                            :target_index + 1]
                    ]
                    with mock.patch.object(
                            installer, "command",
                            side_effect=command_result) as command, \
                            mock.patch.object(installer, "Path") as path, \
                            self.assertRaisesRegex(
                                installer.InstallError,
                                "INSTALL_SHADOW_AUTHORITY_ACTIVE"):
                        installer.safety_preflight("alpha")
                    self.assertEqual(command.call_args_list, expected_calls)
                    path.assert_not_called()
                    self.assertNotIn(
                        mock.call([
                            "/usr/libexec/hepta-broker-egress-policy",
                            "--check-deny-all"]),
                        command.call_args_list)

    def test_safety_preflight_inactive_evidence_exactly_binds_blocking_units(
            self) -> None:
        policy_root = mock.Mock()
        policy_root.exists.return_value = False
        marker = mock.Mock()
        marker.lstat.return_value = mock.Mock(
            st_mode=installer.stat.S_IFREG | 0o440,
            st_uid=0,
            st_nlink=1,
        )
        marker.read_text.return_value = "engaged\n"

        def path_result(value):
            if value == "/etc/heptatrader/paper-campaigns":
                return policy_root
            if value == "/run/hepta/ib-paper-control-alpha/kill-switch":
                return marker
            raise AssertionError(f"unexpected preflight path: {value}")

        def command_result(arguments):
            if arguments[:2] == ["/usr/bin/systemctl", "is-active"]:
                return mock.Mock(
                    stdout="inactive\n", stderr="", returncode=3)
            if arguments == [
                    "/usr/libexec/hepta-broker-egress-policy",
                    "--check-deny-all"]:
                return mock.Mock(stdout="", stderr="", returncode=0)
            raise AssertionError(
                f"unexpected preflight command: {arguments}")

        with mock.patch.object(
                installer, "command",
                side_effect=command_result) as command, \
                mock.patch.object(
                    installer, "Path", side_effect=path_result) as path:
            evidence = installer.safety_preflight("alpha")

        self.assertEqual(evidence, safe_preflight())
        self.assertEqual(
            set(evidence["installation_blocking_units"]),
            set(installer.INSTALLATION_BLOCKING_UNITS))
        self.assertEqual(
            set(evidence["installation_blocking_units"].values()),
            {"inactive"})
        self.assertEqual(
            installer.validate_preflight_evidence(evidence, "alpha"),
            evidence)
        self.assertEqual(
            command.call_args_list,
            [mock.call([
                "/usr/bin/systemctl", "is-active", unit])
             for unit in installer.PAPER_UNITS] +
            [mock.call([
                "/usr/bin/systemctl", "is-active", unit])
             for unit in installer.INSTALLATION_BLOCKING_UNITS] +
            [mock.call([
                "/usr/libexec/hepta-broker-egress-policy",
                "--check-deny-all"])])
        self.assertEqual(
            path.call_args_list,
            [mock.call("/etc/heptatrader/paper-campaigns"),
             mock.call(
                 "/run/hepta/ib-paper-control-alpha/kill-switch")])

    def test_consumer_acquire_validate_member_and_release_are_bound(
            self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = create_consumer_fixture(Path(temporary))
            evidence = fixture["lock_evidence"]
            lock_path = fixture["lock_path"]
            assert isinstance(evidence, dict)
            assert isinstance(lock_path, Path)
            with consumer_lock_validation_patch(evidence, lock_path):
                verified = installer.acquire_verified_installation(
                    **consumer_acquire_arguments(fixture))
                try:
                    self.assertEqual(
                        installer.validate_verified_installation(verified),
                        verified.evidence)
                    payloads = fixture["payloads"]
                    assert isinstance(payloads, dict)
                    installer.require_verified_runtime_member(
                        verified, installer.INSTALLER_MEMBER.as_posix(),
                        payloads[installer.INSTALLER_MEMBER.as_posix()])
                    self.assertEqual(
                        verified.evidence["installed_file_count"],
                        len(fixture["paths"]))
                    self.assertEqual(
                        verified.evidence["default_deny_identity_sha256"],
                        installer.PAPER_IDENTITY_MANIFEST_SHA256)
                    self.assertIs(
                        verified.evidence["verified_under_lock"], True)
                    self.assertEqual(
                        verified.evidence["lock_mode"], "exclusive")
                    self.assertEqual(
                        verified.evidence["schema"],
                        "hepta.shadow-runtime-install-consumption-evidence.v3")
                    self.assertEqual(verified.evidence["version"], 3)
                    self.assertEqual(
                        verified.evidence["current_install_pointer_path"],
                        str(fixture["current_pointer_path"]))
                    self.assertEqual(
                        verified.evidence[
                            "current_install_pointer_file_sha256"],
                        installer.digest_bytes(
                            fixture["current_pointer_payload"]))
                    self.assertEqual(
                        verified.evidence["install_generation"], 1)
                    self.assertEqual(
                        verified.evidence[
                            "predecessor_install_generation"], 0)
                    self.assertEqual(
                        verified.evidence[
                            "predecessor_current_install_pointer_file_sha256"],
                        "absent")
                finally:
                    installer.release_verified_installation(verified)

            reacquired = installer._acquire_existing_transaction_lock(
                lock_path, **fixture["lock_options"])
            installer._release_transaction_lock(reacquired)

    def test_consumer_external_manifest_and_receipt_pins_fail_closed(
            self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = create_consumer_fixture(Path(temporary))
            base = consumer_acquire_arguments(fixture)
            cases = (
                ("manifest-format", "expected_manifest_sha256",
                 "sha256:" + "A" * 64,
                 "INSTALL_CONSUMER_EXPECTED_DIGEST_INVALID"),
                ("receipt-format", "expected_receipt_sha256",
                 "not-a-digest",
                 "INSTALL_CONSUMER_EXPECTED_DIGEST_INVALID"),
                ("manifest-mismatch", "expected_manifest_sha256",
                 "sha256:" + "8" * 64,
                 "INSTALL_CONSUMER_EXPECTED_DIGEST_MISMATCH"),
                ("receipt-mismatch", "expected_receipt_sha256",
                 "sha256:" + "9" * 64,
                 "INSTALL_CONSUMER_EXPECTED_DIGEST_MISMATCH"),
            )
            for label, field, value, reason in cases:
                arguments = dict(base)
                arguments[field] = value
                with self.subTest(label=label), self.assertRaisesRegex(
                        installer.InstallError, reason):
                    installer.acquire_verified_installation(**arguments)

    def test_consumer_requires_one_idle_preexisting_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = create_consumer_fixture(Path(temporary))
            lock_path = fixture["lock_path"]
            assert isinstance(lock_path, Path)
            holder = installer._acquire_existing_transaction_lock(
                lock_path, **fixture["lock_options"])
            try:
                with self.assertRaisesRegex(
                        installer.InstallError,
                        "INSTALL_CONSUMER_LOCK_BUSY"):
                    installer.acquire_verified_installation(
                        **consumer_acquire_arguments(fixture))
            finally:
                installer._release_transaction_lock(holder)

        with tempfile.TemporaryDirectory() as temporary:
            fixture = create_consumer_fixture(Path(temporary))
            lock_path = fixture["lock_path"]
            assert isinstance(lock_path, Path)
            lock_path.unlink()
            with self.assertRaisesRegex(
                    installer.InstallError,
                    "INSTALL_CONSUMER_LOCK_MISSING"):
                installer.acquire_verified_installation(
                    **consumer_acquire_arguments(fixture))

    def test_consumer_rejects_noncanonical_and_semantically_invalid_documents(
            self) -> None:
        def assert_rejected(
                fixture: dict[str, object], reason: str) -> None:
            evidence = fixture["lock_evidence"]
            lock_path = fixture["lock_path"]
            assert isinstance(evidence, dict)
            assert isinstance(lock_path, Path)
            with consumer_lock_validation_patch(evidence, lock_path), \
                    self.assertRaisesRegex(installer.InstallError, reason):
                installer.acquire_verified_installation(
                    **consumer_acquire_arguments(fixture))

        with tempfile.TemporaryDirectory() as temporary:
            fixture = create_consumer_fixture(Path(temporary))
            manifest_path = fixture["manifest_path"]
            manifest = fixture["manifest"]
            assert isinstance(manifest_path, Path)
            assert isinstance(manifest, dict)
            manifest_path.write_bytes((installer.json.dumps(
                manifest, indent=2, sort_keys=True) + "\n").encode("utf-8"))
            assert_rejected(
                fixture, "INSTALL_CONSUMER_MANIFEST_NONCANONICAL")

        with tempfile.TemporaryDirectory() as temporary:
            fixture = create_consumer_fixture(Path(temporary))
            receipt_path = fixture["receipt_path"]
            receipt = fixture["receipt"]
            assert isinstance(receipt_path, Path)
            assert isinstance(receipt, dict)
            receipt_path.chmod(0o600)
            receipt_path.write_bytes((installer.json.dumps(
                receipt, indent=2, sort_keys=True) + "\n").encode("utf-8"))
            receipt_path.chmod(0o440)
            assert_rejected(
                fixture, "INSTALL_CONSUMER_RECEIPT_NONCANONICAL")

        with tempfile.TemporaryDirectory() as temporary:
            fixture = create_consumer_fixture(Path(temporary))
            manifest_path = fixture["manifest_path"]
            manifest = dict(fixture["manifest"])
            assert isinstance(manifest_path, Path)
            manifest["paper_authorized"] = True
            manifest_path.write_bytes(installer.canonical_bytes(manifest))
            assert_rejected(fixture, "INSTALL_MANIFEST_BOUNDARY_INVALID")

        with tempfile.TemporaryDirectory() as temporary:
            fixture = create_consumer_fixture(Path(temporary))
            receipt_path = fixture["receipt_path"]
            receipt = dict(fixture["receipt"])
            assert isinstance(receipt_path, Path)
            receipt["finished_at_ms"] = 124
            receipt_path.chmod(0o600)
            receipt_path.write_bytes(installer.canonical_bytes(receipt))
            receipt_path.chmod(0o440)
            assert_rejected(fixture, "INSTALL_CONSUMER_RECEIPT_INVALID")

        with tempfile.TemporaryDirectory() as temporary:
            fixture = create_consumer_fixture(Path(temporary))
            receipt_path = fixture["receipt_path"]
            receipt = dict(fixture["receipt"])
            assert isinstance(receipt_path, Path)
            receipt["archive_sha256"] = "sha256:" + "7" * 64
            receipt_path.chmod(0o600)
            receipt_path.write_bytes(receipt_with_recomputed_body(receipt))
            receipt_path.chmod(0o440)
            assert_rejected(fixture, "INSTALL_CONSUMER_LINEAGE_MISMATCH")

        with tempfile.TemporaryDirectory() as temporary:
            fixture = create_consumer_fixture(Path(temporary))
            receipt_path = fixture["receipt_path"]
            receipt = dict(fixture["receipt"])
            identity = dict(receipt["default_deny_identity_manifest"])
            assert isinstance(receipt_path, Path)
            identity["installed"] = False
            receipt["default_deny_identity_manifest"] = identity
            receipt_path.chmod(0o600)
            receipt_path.write_bytes(receipt_with_recomputed_body(receipt))
            receipt_path.chmod(0o440)
            assert_rejected(fixture, "INSTALL_CONSUMER_RECEIPT_INVALID")

        with tempfile.TemporaryDirectory() as temporary:
            fixture = create_consumer_fixture(Path(temporary))
            manifest_path = fixture["manifest_path"]
            manifest = dict(fixture["manifest"])
            records = [dict(record) for record in manifest["files"]]
            assert isinstance(manifest_path, Path)
            records[0]["sha256"] = "sha256:" + "6" * 64
            manifest["files"] = records
            manifest_path.write_bytes(installer.canonical_bytes(manifest))
            assert_rejected(
                fixture, "INSTALL_MANIFEST_PAPER_IDENTITY_DRIFT")

    def test_consumer_rejects_first_middle_and_last_closure_drift(
            self) -> None:
        for label, index in (("first", 0), ("middle", 2), ("last", -1)):
            with self.subTest(label=label), \
                    tempfile.TemporaryDirectory() as temporary:
                fixture = create_consumer_fixture(Path(temporary))
                paths = fixture["paths"]
                modes = fixture["modes"]
                filesystem_root = fixture["filesystem_root"]
                evidence = fixture["lock_evidence"]
                lock_path = fixture["lock_path"]
                assert isinstance(paths, list)
                assert isinstance(modes, dict)
                assert isinstance(filesystem_root, Path)
                assert isinstance(evidence, dict)
                assert isinstance(lock_path, Path)
                path = paths[index]
                target = filesystem_root / path
                target.write_bytes(b"closure drift\n")
                target.chmod(modes[path])
                with consumer_lock_validation_patch(evidence, lock_path), \
                        self.assertRaisesRegex(
                            installer.InstallError,
                            "INSTALL_CONSUMER_CLOSURE_INVALID"):
                    installer.acquire_verified_installation(
                        **consumer_acquire_arguments(fixture))

    def test_consumer_validate_rejects_byte_identical_inode_rebound(
            self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = create_consumer_fixture(Path(temporary))
            evidence = fixture["lock_evidence"]
            lock_path = fixture["lock_path"]
            paths = fixture["paths"]
            payloads = fixture["payloads"]
            modes = fixture["modes"]
            filesystem_root = fixture["filesystem_root"]
            assert isinstance(evidence, dict)
            assert isinstance(lock_path, Path)
            assert isinstance(paths, list)
            assert isinstance(payloads, dict)
            assert isinstance(modes, dict)
            assert isinstance(filesystem_root, Path)
            with consumer_lock_validation_patch(evidence, lock_path):
                verified = installer.acquire_verified_installation(
                    **consumer_acquire_arguments(fixture))
                try:
                    path = paths[2]
                    target = filesystem_root / path
                    target.rename(target.with_name(target.name + ".saved"))
                    target.write_bytes(payloads[path])
                    target.chmod(modes[path])
                    with self.assertRaisesRegex(
                            installer.InstallError,
                            "INSTALL_CONSUMER_CLOSURE_REBOUND"):
                        installer.validate_verified_installation(verified)
                finally:
                    installer.release_verified_installation(verified)

    def test_consumer_lock_replacement_during_each_read_window_fails_closed(
            self) -> None:
        seams = (
            ("receipt", "install.receipt.json",
             "INSTALL_TRANSACTION_LOCK_REBOUND"),
            ("manifest", "runtime.manifest.json",
             "INSTALL_TRANSACTION_LOCK_REBOUND"),
            ("closure-first",
             installer.PAPER_IDENTITY_MANIFEST.name,
             "INSTALL_CONSUMER_CLOSURE_INVALID"),
            ("closure-middle", "hepta-shadow-host-installer",
             "INSTALL_CONSUMER_CLOSURE_INVALID"),
            ("closure-last", "consumer-zeta.json",
             "INSTALL_CONSUMER_CLOSURE_INVALID"),
        )
        for label, target_name, reason in seams:
            with self.subTest(label=label), \
                    tempfile.TemporaryDirectory() as temporary:
                fixture = create_consumer_fixture(Path(temporary))
                evidence = fixture["lock_evidence"]
                lock_path = fixture["lock_path"]
                assert isinstance(evidence, dict)
                assert isinstance(lock_path, Path)
                real_read = installer._read_at
                replaced = False

                def read_then_replace(parent, name, **kwargs):
                    nonlocal replaced
                    result = real_read(parent, name, **kwargs)
                    if name == target_name and not replaced:
                        replaced = True
                        replace_consumer_lock(fixture)
                    return result

                with consumer_lock_validation_patch(evidence, lock_path), \
                        mock.patch.object(
                            installer, "_read_at",
                            side_effect=read_then_replace), \
                        self.assertRaisesRegex(installer.InstallError, reason):
                    installer.acquire_verified_installation(
                        **consumer_acquire_arguments(fixture))
                self.assertTrue(replaced)

    def test_consumer_lock_replacement_before_final_return_fails_closed(
            self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = create_consumer_fixture(Path(temporary))
            evidence = fixture["lock_evidence"]
            lock_path = fixture["lock_path"]
            assert isinstance(evidence, dict)
            assert isinstance(lock_path, Path)
            real_build = installer._build_install_consumption_evidence
            replaced = False

            def build_then_replace(**kwargs):
                nonlocal replaced
                result = real_build(**kwargs)
                replaced = True
                replace_consumer_lock(fixture)
                return result

            with consumer_lock_validation_patch(evidence, lock_path), \
                    mock.patch.object(
                        installer, "_build_install_consumption_evidence",
                        side_effect=build_then_replace), \
                    self.assertRaisesRegex(
                        installer.InstallError,
                        "INSTALL_TRANSACTION_LOCK_REBOUND"):
                installer.acquire_verified_installation(
                    **consumer_acquire_arguments(fixture))
            self.assertTrue(replaced)

    def test_current_pointer_first_and_subsequent_generations_use_cas(
            self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = create_consumer_fixture(Path(temporary))
            state_root = fixture["state_root"]
            lock_path = fixture["lock_path"]
            assert isinstance(state_root, Path)
            assert isinstance(lock_path, Path)
            pointer_path = state_root / "cas-current-install-v1.json"
            first = build_fixture_current_pointer(
                fixture, 1, current_pointer_path=pointer_path)
            second = build_fixture_current_pointer(
                fixture, 2, current_pointer_path=pointer_path)
            lock = installer._acquire_existing_transaction_lock(
                lock_path, **fixture["lock_options"])
            real_open = installer._open_anchored_directory
            real_rebind = installer._rebind_directory
            real_atomic = installer._atomic_write_at
            real_read = installer._read_at

            def publish_rootless(document, previous):
                publication_complete = False

                def rootless_open(path, *, create, owner_uid, owner_gid,
                                  strict_ancestors, leaf_mode=None,
                                  transaction_lock=None):
                    return real_open(
                        path, create=create, owner_uid=os.geteuid(),
                        owner_gid=os.getegid(), strict_ancestors=False,
                        leaf_mode=leaf_mode,
                        transaction_lock=transaction_lock)

                def rootless_rebind(path, descriptor, *, owner_uid,
                                    owner_gid, strict_ancestors,
                                    leaf_mode=None):
                    return real_rebind(
                        path, descriptor, owner_uid=os.geteuid(),
                        owner_gid=os.getegid(), strict_ancestors=False,
                        leaf_mode=leaf_mode)

                def rootless_atomic(
                        parent_path, parent, name, payload, mode, **kwargs):
                    nonlocal publication_complete
                    kwargs["owner_uid"] = os.geteuid()
                    kwargs["owner_gid"] = os.getegid()
                    kwargs["strict_ancestors"] = False
                    metadata = real_atomic(
                        parent_path, parent, name, payload, mode, **kwargs)
                    publication_complete = True
                    return root_owned_metadata(metadata)

                def read_with_root_projection(parent, name, **kwargs):
                    payload, metadata = real_read(parent, name, **kwargs)
                    if publication_complete and name == pointer_path.name:
                        metadata = root_owned_metadata(metadata)
                    return payload, metadata

                with mock.patch.object(
                        installer, "_open_anchored_directory",
                        side_effect=rootless_open), \
                        mock.patch.object(
                            installer, "_rebind_directory",
                            side_effect=rootless_rebind), \
                        mock.patch.object(
                            installer, "_atomic_write_at",
                            side_effect=rootless_atomic), \
                        mock.patch.object(
                            installer, "_read_at",
                            side_effect=read_with_root_projection):
                    return installer._publish_current_install_pointer(
                        pointer_path, document, previous, lock)

            try:
                first_state = publish_rootless(first, None)
                self.assertEqual(first_state.document["generation"], 1)
                self.assertEqual(
                    pointer_path.read_bytes(), installer.canonical_bytes(first))
                first_actual = pointer_path.lstat()
                previous = installer.CurrentInstallPointerState(
                    payload=first_state.payload,
                    metadata=first_actual,
                    document=first,
                )
                second_state = publish_rootless(second, previous)
                self.assertEqual(second_state.document["generation"], 2)
                self.assertEqual(
                    pointer_path.read_bytes(),
                    installer.canonical_bytes(second))
                with self.assertRaisesRegex(
                        installer.InstallError,
                        "INSTALL_CURRENT_POINTER_REBOUND"):
                    publish_rootless(second, previous)
                self.assertEqual(
                    pointer_path.read_bytes(),
                    installer.canonical_bytes(second))
            finally:
                installer._release_transaction_lock(lock)

    def test_old_consumer_rejects_pointer_to_new_install_and_extra_field(
            self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = create_consumer_fixture(Path(temporary))
            state_root = fixture["state_root"]
            pointer_path = fixture["current_pointer_path"]
            evidence = fixture["lock_evidence"]
            lock_path = fixture["lock_path"]
            assert isinstance(state_root, Path)
            assert isinstance(pointer_path, Path)
            assert isinstance(evidence, dict)
            assert isinstance(lock_path, Path)
            new_pointer = build_fixture_current_pointer(
                fixture,
                2,
                manifest_path=state_root / "new-install.manifest.json",
                receipt_path=state_root / "new-install.receipt.json",
                backup_root=state_root / "backups/new-install",
            )
            pointer_path.write_bytes(installer.canonical_bytes(new_pointer))
            with consumer_lock_validation_patch(evidence, lock_path), \
                    self.assertRaisesRegex(
                        installer.InstallError,
                        "INSTALL_CURRENT_POINTER_MISMATCH"):
                installer.acquire_verified_installation(
                    **consumer_acquire_arguments(fixture))

        with tempfile.TemporaryDirectory() as temporary:
            fixture = create_consumer_fixture(Path(temporary))
            pointer_path = fixture["current_pointer_path"]
            evidence = fixture["lock_evidence"]
            lock_path = fixture["lock_path"]
            assert isinstance(pointer_path, Path)
            assert isinstance(evidence, dict)
            assert isinstance(lock_path, Path)
            pointer = dict(fixture["current_pointer"])
            pointer["extra_path"] = "/var/lib/hepta/unbound-install.json"
            pointer_path.write_bytes(
                current_pointer_with_recomputed_body(pointer))
            with consumer_lock_validation_patch(evidence, lock_path), \
                    self.assertRaisesRegex(
                        installer.InstallError,
                        "INSTALL_CURRENT_POINTER_INVALID"):
                installer.acquire_verified_installation(
                    **consumer_acquire_arguments(fixture))

    def test_consumer_pointer_rejects_noncanonical_mode_and_symlink(
            self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = create_consumer_fixture(Path(temporary))
            pointer_path = fixture["current_pointer_path"]
            evidence = fixture["lock_evidence"]
            lock_path = fixture["lock_path"]
            assert isinstance(pointer_path, Path)
            assert isinstance(evidence, dict)
            assert isinstance(lock_path, Path)
            pointer_path.write_bytes((installer.json.dumps(
                fixture["current_pointer"], indent=2, sort_keys=True
            ) + "\n").encode("utf-8"))
            with consumer_lock_validation_patch(evidence, lock_path), \
                    self.assertRaisesRegex(
                        installer.InstallError,
                        "INSTALL_CURRENT_POINTER_INVALID"):
                installer.acquire_verified_installation(
                    **consumer_acquire_arguments(fixture))

        with tempfile.TemporaryDirectory() as temporary:
            fixture = create_consumer_fixture(Path(temporary))
            pointer_path = fixture["current_pointer_path"]
            evidence = fixture["lock_evidence"]
            lock_path = fixture["lock_path"]
            assert isinstance(pointer_path, Path)
            assert isinstance(evidence, dict)
            assert isinstance(lock_path, Path)
            pointer_path.chmod(0o640)
            with consumer_lock_validation_patch(evidence, lock_path), \
                    self.assertRaisesRegex(
                        installer.InstallError,
                        "INSTALL_CONSUMER_DOCUMENT_METADATA_INVALID"):
                installer.acquire_verified_installation(
                    **consumer_acquire_arguments(fixture))

        with tempfile.TemporaryDirectory() as temporary:
            fixture = create_consumer_fixture(Path(temporary))
            pointer_path = fixture["current_pointer_path"]
            evidence = fixture["lock_evidence"]
            lock_path = fixture["lock_path"]
            assert isinstance(pointer_path, Path)
            assert isinstance(evidence, dict)
            assert isinstance(lock_path, Path)
            saved = pointer_path.with_name(pointer_path.name + ".saved")
            pointer_path.rename(saved)
            pointer_path.symlink_to(saved.name)
            with consumer_lock_validation_patch(evidence, lock_path), \
                    self.assertRaisesRegex(
                        installer.InstallError,
                        "INSTALL_ANCHORED_FILE_INVALID"):
                installer.acquire_verified_installation(
                    **consumer_acquire_arguments(fixture))

    def test_consumer_pointer_read_inode_rebound_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = create_consumer_fixture(Path(temporary))
            pointer_path = fixture["current_pointer_path"]
            pointer_payload = fixture["current_pointer_payload"]
            evidence = fixture["lock_evidence"]
            lock_path = fixture["lock_path"]
            assert isinstance(pointer_path, Path)
            assert isinstance(pointer_payload, bytes)
            assert isinstance(evidence, dict)
            assert isinstance(lock_path, Path)
            real_read = installer.os.read
            replaced = False

            def read_then_rebind(descriptor, count):
                nonlocal replaced
                payload = real_read(descriptor, count)
                try:
                    opened_path = Path(os.readlink(
                        f"/proc/self/fd/{descriptor}"))
                except OSError:
                    opened_path = Path("/")
                if (
                        payload and not replaced and
                        opened_path == pointer_path):
                    replaced = True
                    pointer_path.rename(pointer_path.with_name(
                        pointer_path.name + ".saved"))
                    pointer_path.write_bytes(pointer_payload)
                    pointer_path.chmod(0o600)
                return payload

            with consumer_lock_validation_patch(evidence, lock_path), \
                    mock.patch.object(
                        installer.os, "read", side_effect=read_then_rebind), \
                    self.assertRaisesRegex(
                        installer.InstallError,
                        "INSTALL_ANCHORED_FILE_REBOUND"):
                installer.acquire_verified_installation(
                    **consumer_acquire_arguments(fixture))
            self.assertTrue(replaced)

    def test_consumer_validate_rejects_pointer_inode_rebound(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = create_consumer_fixture(Path(temporary))
            pointer_path = fixture["current_pointer_path"]
            pointer_payload = fixture["current_pointer_payload"]
            evidence = fixture["lock_evidence"]
            lock_path = fixture["lock_path"]
            assert isinstance(pointer_path, Path)
            assert isinstance(pointer_payload, bytes)
            assert isinstance(evidence, dict)
            assert isinstance(lock_path, Path)
            with consumer_lock_validation_patch(evidence, lock_path):
                verified = installer.acquire_verified_installation(
                    **consumer_acquire_arguments(fixture))
                try:
                    pointer_path.rename(pointer_path.with_name(
                        pointer_path.name + ".saved"))
                    pointer_path.write_bytes(pointer_payload)
                    pointer_path.chmod(0o600)
                    with self.assertRaisesRegex(
                            installer.InstallError,
                            "INSTALL_CONSUMER_DOCUMENT_REBOUND"):
                        installer.validate_verified_installation(verified)
                finally:
                    installer.release_verified_installation(verified)

    def test_current_pointer_rollback_restores_previous_generation(
            self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = create_consumer_fixture(Path(temporary))
            state_root = fixture["state_root"]
            pointer_path = fixture["current_pointer_path"]
            lock_path = fixture["lock_path"]
            old_pointer = fixture["current_pointer"]
            old_payload = fixture["current_pointer_payload"]
            assert isinstance(state_root, Path)
            assert isinstance(pointer_path, Path)
            assert isinstance(lock_path, Path)
            assert isinstance(old_pointer, dict)
            assert isinstance(old_payload, bytes)
            lock = installer._acquire_existing_transaction_lock(
                lock_path, **fixture["lock_options"])
            parent = installer._open_anchored_directory(
                state_root, create=False, owner_uid=os.geteuid(),
                owner_gid=os.getegid(), strict_ancestors=False)
            old_metadata = pointer_path.lstat()
            previous = installer.CurrentInstallPointerState(
                payload=old_payload, metadata=old_metadata,
                document=old_pointer)
            candidate = build_fixture_current_pointer(fixture, 2)
            candidate_payload = installer.canonical_bytes(candidate)
            try:
                installer._atomic_write_at(
                    state_root, parent, pointer_path.name,
                    candidate_payload, 0o600,
                    owner_uid=os.geteuid(), owner_gid=os.getegid(),
                    expected=old_metadata, expected_payload=old_payload,
                    reason="INSTALL_CURRENT_POINTER_PUBLISH_FAILED",
                    strict_ancestors=False, transaction_lock=lock)
            finally:
                os.close(parent)

            real_open = installer._open_anchored_directory
            real_rebind = installer._rebind_directory
            real_atomic = installer._atomic_write_at
            real_read = installer._read_at
            rollback_write_complete = False

            def rootless_open(path, *, create, owner_uid, owner_gid,
                              strict_ancestors, leaf_mode=None,
                              transaction_lock=None):
                return real_open(
                    path, create=create, owner_uid=os.geteuid(),
                    owner_gid=os.getegid(), strict_ancestors=False,
                    leaf_mode=leaf_mode, transaction_lock=transaction_lock)

            def rootless_rebind(path, descriptor, *, owner_uid, owner_gid,
                                strict_ancestors, leaf_mode=None):
                return real_rebind(
                    path, descriptor, owner_uid=os.geteuid(),
                    owner_gid=os.getegid(), strict_ancestors=False,
                    leaf_mode=leaf_mode)

            def rootless_atomic(
                    parent_path, parent_fd, name, payload, mode, **kwargs):
                nonlocal rollback_write_complete
                kwargs["owner_uid"] = os.geteuid()
                kwargs["owner_gid"] = os.getegid()
                kwargs["strict_ancestors"] = False
                result = real_atomic(
                    parent_path, parent_fd, name, payload, mode, **kwargs)
                rollback_write_complete = True
                return result

            def read_with_root_projection(parent_fd, name, **kwargs):
                payload, metadata = real_read(parent_fd, name, **kwargs)
                if rollback_write_complete and name == pointer_path.name:
                    metadata = root_owned_metadata(metadata)
                return payload, metadata

            try:
                with mock.patch.object(
                        installer, "_open_anchored_directory",
                        side_effect=rootless_open), \
                        mock.patch.object(
                            installer, "_rebind_directory",
                            side_effect=rootless_rebind), \
                        mock.patch.object(
                            installer, "_atomic_write_at",
                            side_effect=rootless_atomic), \
                        mock.patch.object(
                            installer, "_read_at",
                            side_effect=read_with_root_projection):
                    installer._restore_current_install_pointer(
                        pointer_path, candidate_payload, previous, lock)
                self.assertEqual(pointer_path.read_bytes(), old_payload)
                self.assertEqual(
                    installer.strict_json_bytes(
                        pointer_path.read_bytes(),
                        "INSTALL_CURRENT_POINTER_INVALID")["generation"],
                    1)
            finally:
                installer._release_transaction_lock(lock)

    def test_current_generation_rejects_same_count_path_set_swap(self) -> None:
        old_paths = ["etc/heptatrader/a", "usr/libexec/b"]
        previous = installer.CurrentInstallPointerState(
            payload=b"pointer\n",
            metadata=mock.Mock(),
            document={
                "installed_file_count": len(old_paths),
                "installed_paths_sha256": installer.digest_bytes(
                    installer.canonical_bytes(old_paths)),
            })
        unchanged = {
            "files": [{"path": path} for path in old_paths],
        }
        installer._validate_current_install_path_set(previous, unchanged)
        swapped = {
            "files": [
                {"path": old_paths[0]},
                {"path": "usr/libexec/c"},
            ],
        }
        with self.assertRaisesRegex(
                installer.InstallError,
                "INSTALL_CURRENT_PATH_SET_DRIFT"):
            installer._validate_current_install_path_set(previous, swapped)

    def test_current_generation_accepts_authenticated_append_only_paths(
            self) -> None:
        old_records = sorted(
            [installer_record(), identity_record()],
            key=lambda record: str(record["path"]))
        old_manifest = manifest_document(old_records)
        old_payload = installer.canonical_bytes(old_manifest)
        old_paths = [record["path"] for record in old_records]
        previous = installer.CurrentInstallPointerState(
            payload=b"pointer\n",
            metadata=mock.Mock(),
            document={
                "manifest_path": "/var/lib/hepta/old-manifest.json",
                "manifest_file_sha256": installer.digest_bytes(old_payload),
                "installed_file_count": len(old_paths),
                "installed_paths_sha256": installer.digest_bytes(
                    installer.canonical_bytes(old_paths)),
            })
        added = {
            "path": "usr/libexec/new-passive-helper",
            "mode": "0755",
            "size": 1,
            "sha256": installer.digest_bytes(b"x"),
        }
        transaction_lock = (
            Path("/var/lib/hepta"), 1, 2,
            ".shadow-runtime-install.lock", tuple(), False, 0, 0, True)
        with mock.patch.object(
                installer, "_read_consumer_document",
                return_value=(old_payload, mock.Mock())):
            installer._validate_current_install_path_set(
                previous,
                {"files": sorted(
                    [*old_records, added],
                    key=lambda record: str(record["path"]))},
                transaction_lock)

            with self.assertRaisesRegex(
                    installer.InstallError,
                    "INSTALL_CURRENT_PATH_SET_DRIFT"):
                installer._validate_current_install_path_set(
                    previous,
                    {"files": sorted([old_records[0], added, {
                        **identity_record(),
                        "path": "etc/heptatrader/replaced-identity.json",
                    }], key=lambda record: str(record["path"]))},
                    transaction_lock)

    def test_caller_lineage_cas_rejects_stale_writer_before_mutation(
            self) -> None:
        prior_payload = b'{"generation":7}\n'
        previous = installer.CurrentInstallPointerState(
            payload=prior_payload,
            metadata=mock.Mock(),
            document={"generation": 7})
        prior_sha = installer.digest_bytes(prior_payload)
        installer._validate_expected_current_install_lineage(
            previous, 7, prior_sha)
        for generation, digest in (
                (6, prior_sha),
                (7, "sha256:" + "0" * 64),
                (0, "absent")):
            with self.subTest(generation=generation, digest=digest), \
                    self.assertRaisesRegex(
                        installer.InstallError,
                        "INSTALL_CURRENT_LINEAGE_MISMATCH"):
                installer._validate_expected_current_install_lineage(
                    previous, generation, digest)
        installer._validate_expected_current_install_lineage(
            None, 0, "absent")
        with self.assertRaisesRegex(
                installer.InstallError,
                "INSTALL_CURRENT_LINEAGE_MISMATCH"):
            installer._validate_expected_current_install_lineage(
                None, 7, prior_sha)
        for generation, digest in (
                (0, prior_sha), (1, "absent"), (1, "not-a-digest")):
            with self.subTest(invalid_generation=generation, digest=digest), \
                    self.assertRaisesRegex(
                        installer.InstallError,
                        "INSTALL_EXPECTED_CURRENT_LINEAGE_INVALID"):
                installer._validate_expected_current_install_lineage(
                    previous, generation, digest)

    def test_install_stale_lineage_stops_before_output_or_payload_mutation(
            self) -> None:
        manifest = manifest_document([identity_record(), installer_record()])
        manifest_payload = installer.canonical_bytes(manifest)
        installer_payload = INSTALLER_PATH.read_bytes()
        previous_payload = b'{"generation":2}\n'
        previous = installer.CurrentInstallPointerState(
            payload=previous_payload,
            metadata=mock.Mock(),
            document={"generation": 2})
        metadata = mock.Mock()
        metadata.st_uid = 0
        metadata.st_gid = 0
        metadata.st_mode = installer.stat.S_IFREG | 0o600
        prepare = mock.Mock()
        mutate = mock.Mock()
        lock = object()
        with mock.patch.object(installer.os, "geteuid", return_value=0), \
                mock.patch.object(
                    installer, "load_manifest", return_value=manifest), \
                mock.patch.object(
                    installer, "verify_archive", return_value={
                        installer.INSTALLER_MEMBER.as_posix():
                            installer_payload}), \
                mock.patch.object(
                    installer, "_acquire_transaction_lock",
                    return_value=lock), \
                mock.patch.object(
                    installer, "_validate_transaction_lock"), \
                mock.patch.object(
                    installer, "_release_transaction_lock"), \
                mock.patch.object(
                    installer, "safety_preflight",
                    return_value=safe_preflight()), \
                mock.patch.object(
                    installer, "_read_consumer_document",
                    return_value=(manifest_payload, metadata)), \
                mock.patch.object(
                    installer, "_read_current_install_pointer_state",
                    return_value=previous), \
                mock.patch.object(
                    installer, "_prepare_install_outputs", prepare), \
                mock.patch.object(
                    installer, "_install_payloads", mutate), \
                self.assertRaisesRegex(
                    installer.InstallError,
                    "INSTALL_CURRENT_LINEAGE_MISMATCH"):
            installer.install(
                Path("/archive.tar.gz"), Path("/manifest.json"),
                manifest["archive_sha256"],
                manifest["source_baseline_sha256"],
                manifest["installer_sha256"],
                1, "sha256:" + "0" * 64,
                Path("/var/lib/hepta/backups/stale"),
                Path("/var/lib/hepta/receipts/stale.json"),
                "alpha", 1000)
        prepare.assert_not_called()
        mutate.assert_not_called()

    def test_quarantine_guard_busy_missing_rebound_and_release(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = create_consumer_fixture(Path(temporary))
            lock_path = fixture["lock_path"]
            assert isinstance(lock_path, Path)
            holder = installer._acquire_existing_transaction_lock(
                lock_path, **fixture["lock_options"])
            try:
                with self.assertRaisesRegex(
                        installer.InstallError,
                        "INSTALL_CONSUMER_LOCK_BUSY"):
                    installer.acquire_installation_quarantine_guard(
                        lock_path=lock_path,
                        lock_owner_uid=os.geteuid(),
                        lock_owner_gid=os.getegid(),
                        strict_ancestors=False)
            finally:
                installer._release_transaction_lock(holder)

        with tempfile.TemporaryDirectory() as temporary:
            fixture = create_consumer_fixture(Path(temporary))
            lock_path = fixture["lock_path"]
            assert isinstance(lock_path, Path)
            lock_path.unlink()
            with self.assertRaisesRegex(
                    installer.InstallError,
                    "INSTALL_CONSUMER_LOCK_MISSING"):
                installer.acquire_installation_quarantine_guard(
                    lock_path=lock_path,
                    lock_owner_uid=os.geteuid(),
                    lock_owner_gid=os.getegid(),
                    strict_ancestors=False)

        with tempfile.TemporaryDirectory() as temporary:
            fixture = create_consumer_fixture(Path(temporary))
            lock_path = fixture["lock_path"]
            assert isinstance(lock_path, Path)
            guard = installer.acquire_installation_quarantine_guard(
                lock_path=lock_path,
                lock_owner_uid=os.geteuid(),
                lock_owner_gid=os.getegid(),
                strict_ancestors=False)
            replace_consumer_lock(fixture)
            with self.assertRaisesRegex(
                    installer.InstallError,
                    "INSTALL_TRANSACTION_LOCK_REBOUND"):
                installer.validate_installation_quarantine_guard(guard)
            with self.assertRaisesRegex(
                    installer.InstallError,
                    "INSTALL_TRANSACTION_LOCK_REBOUND"):
                installer.release_installation_quarantine_guard(guard)
            replacement = installer._acquire_existing_transaction_lock(
                lock_path, **fixture["lock_options"])
            installer._release_transaction_lock(replacement)

        with tempfile.TemporaryDirectory() as temporary:
            fixture = create_consumer_fixture(Path(temporary))
            lock_path = fixture["lock_path"]
            assert isinstance(lock_path, Path)
            guard = installer.acquire_installation_quarantine_guard(
                lock_path=lock_path,
                lock_owner_uid=os.geteuid(),
                lock_owner_gid=os.getegid(),
                strict_ancestors=False)
            installer.validate_installation_quarantine_guard(guard)
            installer.release_installation_quarantine_guard(guard)
            reacquired = installer._acquire_existing_transaction_lock(
                lock_path, **fixture["lock_options"])
            installer._release_transaction_lock(reacquired)

    def test_transaction_lock_is_nonblocking_persistent_and_rebound_safe(
            self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            lock_path = root / ".shadow-runtime-install.lock"
            options = {
                "owner_uid": os.geteuid(), "owner_gid": os.getegid(),
                "strict_ancestors": False,
            }
            first = installer._acquire_transaction_lock(lock_path, **options)
            try:
                self.assertTrue(first[5])
                metadata = lock_path.lstat()
                self.assertTrue(installer.stat.S_ISREG(metadata.st_mode))
                self.assertEqual(metadata.st_nlink, 1)
                self.assertEqual(metadata.st_size, 0)
                self.assertEqual(metadata.st_mode & 0o7777, 0o600)
                with self.assertRaisesRegex(
                        installer.InstallError, "TRANSACTION_BUSY"):
                    installer._acquire_transaction_lock(lock_path, **options)
                installer._validate_transaction_lock(first, **options)
            finally:
                installer._release_transaction_lock(first)
            second = installer._acquire_transaction_lock(lock_path, **options)
            try:
                self.assertFalse(second[5])
            finally:
                installer._release_transaction_lock(second)
            self.assertTrue(lock_path.exists())

    def test_transaction_lock_create_race_binds_existing_winner(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            lock_path = root / ".shadow-runtime-install.lock"
            real_open = installer.os.open
            injected = False

            def create_winner(path, flags, *args, **kwargs):
                nonlocal injected
                if flags == installer.LOCK_CREATE_FLAGS and not injected:
                    injected = True
                    winner = real_open(path, flags, *args, **kwargs)
                    os.close(winner)
                    raise FileExistsError(17, "injected create race")
                return real_open(path, flags, *args, **kwargs)

            with mock.patch.object(
                    installer.os, "open", side_effect=create_winner):
                lock = installer._acquire_transaction_lock(
                    lock_path, owner_uid=os.geteuid(), owner_gid=os.getegid(),
                    strict_ancestors=False)
            try:
                self.assertTrue(injected)
                self.assertFalse(lock[5])
                installer._validate_transaction_lock(
                    lock, owner_uid=os.geteuid(), owner_gid=os.getegid(),
                    strict_ancestors=False)
            finally:
                installer._release_transaction_lock(lock)

    def test_two_process_first_create_has_one_holder_and_one_busy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            lock_path = Path(temporary) / ".shadow-runtime-install.lock"
            context = multiprocessing.get_context("fork")
            start = context.Event()
            release = context.Event()
            results = context.Queue()
            processes = [
                context.Process(
                    target=transaction_lock_worker,
                    args=(str(lock_path), start, release, results))
                for _index in range(2)
            ]
            for process in processes:
                process.start()
            start.set()
            observed = [results.get(timeout=10) for _index in range(2)]
            release.set()
            for process in processes:
                process.join(timeout=10)
                self.assertEqual(process.exitcode, 0)
            self.assertEqual(sum(item[0] == "ok" for item in observed), 1)
            self.assertEqual(
                [item for item in observed if item[0] == "error"],
                [("error", "INSTALL_TRANSACTION_BUSY")])
            metadata = lock_path.lstat()
            self.assertEqual(metadata.st_nlink, 1)
            self.assertEqual(metadata.st_size, 0)
            self.assertEqual(metadata.st_mode & 0o7777, 0o600)

    def test_inner_exchange_lock_compromise_preserves_new_holder_target(
            self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            lock_path = root / ".shadow-runtime-install.lock"
            target = root / "target"
            target.write_bytes(b"original\n")
            target.chmod(0o600)
            options = {
                "owner_uid": os.geteuid(), "owner_gid": os.getegid(),
                "strict_ancestors": False,
            }
            lock = installer._acquire_transaction_lock(lock_path, **options)
            parent = installer._open_anchored_directory(
                root, create=False, **options)
            expected_payload, expected = installer._read_at(parent, "target")
            real_exchange = installer._rename_exchange
            replaced = False
            second_lock = None

            def replace_lock_then_exchange(descriptor, left, right):
                nonlocal replaced, second_lock
                if not replaced:
                    replaced = True
                    lock_path.rename(root / ".lock.saved")
                    second_lock = installer._acquire_transaction_lock(
                        lock_path, **options)
                    real_exchange(descriptor, left, right)
                    target.write_bytes(b"second-holder\n")
                    target.chmod(0o600)
                    return None
                return real_exchange(descriptor, left, right)

            try:
                with mock.patch.object(
                        installer, "_rename_exchange",
                        side_effect=replace_lock_then_exchange), \
                        self.assertRaisesRegex(
                            installer.InstallError, "LOCK_COMPROMISED"):
                    installer._atomic_write_at(
                        root, parent, "target", b"candidate\n", 0o600,
                        owner_uid=os.geteuid(), owner_gid=os.getegid(),
                        expected=expected, expected_payload=expected_payload,
                        reason="INSTALL_DESTINATION_WRITE_FAILED",
                        strict_ancestors=False, transaction_lock=lock)
            finally:
                os.close(parent)
                if second_lock is not None:
                    installer._release_transaction_lock(second_lock)
                installer._release_transaction_lock(lock)
            self.assertTrue(replaced)
            self.assertEqual(target.read_bytes(), b"second-holder\n")
            residue = list(root.glob(".target.hepta-*.tmp"))
            self.assertEqual(len(residue), 1)
            self.assertEqual(residue[0].read_bytes(), b"original\n")

    def test_pre_publish_lock_compromise_retains_candidate_residue(
            self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            lock_path = root / ".shadow-runtime-install.lock"
            target = root / "target"
            options = {
                "owner_uid": os.geteuid(), "owner_gid": os.getegid(),
                "strict_ancestors": False,
            }
            lock = installer._acquire_transaction_lock(lock_path, **options)
            parent = installer._open_anchored_directory(
                root, create=False, **options)
            real_noreplace = installer._rename_noreplace
            replaced = False
            second_lock = None

            def replace_lock_before_publish(*args, **kwargs):
                nonlocal replaced, second_lock
                if not replaced:
                    replaced = True
                    lock_path.rename(root / ".lock.saved")
                    second_lock = installer._acquire_transaction_lock(
                        lock_path, **options)
                    target.write_bytes(b"second-holder\n")
                    target.chmod(0o600)
                    raise OSError("injected publish race")
                return real_noreplace(*args, **kwargs)

            try:
                with mock.patch.object(
                        installer, "_rename_noreplace",
                        side_effect=replace_lock_before_publish), \
                        self.assertRaisesRegex(
                            installer.InstallError, "LOCK_COMPROMISED"):
                    installer._atomic_write_at(
                        root, parent, "target", b"candidate\n", 0o600,
                        owner_uid=os.geteuid(), owner_gid=os.getegid(),
                        expected=None, expected_payload=None,
                        reason="INSTALL_DESTINATION_WRITE_FAILED",
                        strict_ancestors=False, transaction_lock=lock)
            finally:
                os.close(parent)
                if second_lock is not None:
                    installer._release_transaction_lock(second_lock)
                installer._release_transaction_lock(lock)
            self.assertTrue(replaced)
            self.assertEqual(target.read_bytes(), b"second-holder\n")
            residue = list(root.glob(".target.hepta-*.tmp"))
            self.assertEqual(len(residue), 1)
            self.assertEqual(residue[0].read_bytes(), b"candidate\n")

    def test_quarantine_move_lock_compromise_does_not_restore_target(
            self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            lock_path = root / ".shadow-runtime-install.lock"
            target = root / "target"
            target.write_bytes(b"original\n")
            target.chmod(0o600)
            options = {
                "owner_uid": os.geteuid(), "owner_gid": os.getegid(),
                "strict_ancestors": False,
            }
            lock = installer._acquire_transaction_lock(lock_path, **options)
            parent = installer._open_anchored_directory(
                root, create=False, **options)
            expected_payload, expected = installer._read_at(parent, "target")
            real_noreplace = installer._rename_noreplace
            replaced = False
            second_lock = None

            def replace_lock_after_quarantine_move(*args, **kwargs):
                nonlocal replaced, second_lock
                result = real_noreplace(*args, **kwargs)
                if not replaced:
                    replaced = True
                    lock_path.rename(root / ".lock.saved")
                    second_lock = installer._acquire_transaction_lock(
                        lock_path, **options)
                    target.write_bytes(b"second-holder\n")
                    target.chmod(0o600)
                return result

            try:
                with mock.patch.object(
                        installer, "_rename_noreplace",
                        side_effect=replace_lock_after_quarantine_move), \
                        self.assertRaisesRegex(
                            installer.InstallError, "LOCK_COMPROMISED"):
                    installer._unlink_exact_at(
                        root, parent, "target", expected_payload, expected,
                        owner_uid=os.geteuid(), owner_gid=os.getegid(),
                        strict_ancestors=False,
                        reason="INSTALL_TEST_TARGET_REBOUND",
                        transaction_lock=lock)
            finally:
                os.close(parent)
                if second_lock is not None:
                    installer._release_transaction_lock(second_lock)
                installer._release_transaction_lock(lock)
            self.assertTrue(replaced)
            self.assertEqual(target.read_bytes(), b"second-holder\n")
            quarantines = list(root.glob(".target.hepta-quarantine-*"))
            self.assertEqual(len(quarantines), 1)
            self.assertEqual(
                (quarantines[0] / "payload").read_bytes(), b"original\n")

    def test_post_unlink_lock_compromise_retains_empty_quarantine(
            self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            lock_path = root / ".shadow-runtime-install.lock"
            target = root / "target"
            target.write_bytes(b"original\n")
            target.chmod(0o600)
            options = {
                "owner_uid": os.geteuid(), "owner_gid": os.getegid(),
                "strict_ancestors": False,
            }
            lock = installer._acquire_transaction_lock(lock_path, **options)
            parent = installer._open_anchored_directory(
                root, create=False, **options)
            expected_payload, expected = installer._read_at(parent, "target")
            real_unlink = installer.os.unlink
            replaced = False
            second_lock = None

            def replace_lock_after_unlink(path, *args, **kwargs):
                nonlocal replaced, second_lock
                result = real_unlink(path, *args, **kwargs)
                if path == "payload" and not replaced:
                    replaced = True
                    lock_path.rename(root / ".lock.saved")
                    second_lock = installer._acquire_transaction_lock(
                        lock_path, **options)
                    target.write_bytes(b"second-holder\n")
                    target.chmod(0o600)
                return result

            try:
                with mock.patch.object(
                        installer.os, "unlink",
                        side_effect=replace_lock_after_unlink), \
                        self.assertRaisesRegex(
                            installer.InstallError, "LOCK_COMPROMISED"):
                    installer._unlink_exact_at(
                        root, parent, "target", expected_payload, expected,
                        owner_uid=os.geteuid(), owner_gid=os.getegid(),
                        strict_ancestors=False,
                        reason="INSTALL_TEST_TARGET_REBOUND",
                        transaction_lock=lock)
            finally:
                os.close(parent)
                if second_lock is not None:
                    installer._release_transaction_lock(second_lock)
                installer._release_transaction_lock(lock)
            self.assertTrue(replaced)
            self.assertEqual(target.read_bytes(), b"second-holder\n")
            quarantines = list(root.glob(".target.hepta-quarantine-*"))
            self.assertEqual(len(quarantines), 1)
            self.assertEqual(list(quarantines[0].iterdir()), [])

    def test_terminal_withdrawal_retains_exact_payload_in_quarantine(
            self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "receipt.json"
            target.write_bytes(b"receipt\n")
            target.chmod(0o440)
            options = {
                "owner_uid": os.geteuid(), "owner_gid": os.getegid(),
                "strict_ancestors": False,
            }
            parent = installer._open_anchored_directory(
                root, create=False, **options)
            expected_payload, expected = installer._read_at(
                parent, "receipt.json")
            try:
                with mock.patch.object(installer.os, "unlink") as unlink:
                    installer._unlink_exact_at(
                        root, parent, "receipt.json",
                        expected_payload, expected,
                        owner_uid=os.geteuid(), owner_gid=os.getegid(),
                        strict_ancestors=False,
                        reason="INSTALL_TEST_RECEIPT_REBOUND",
                        retain_in_quarantine=True)
                    unlink.assert_not_called()
            finally:
                os.close(parent)
            self.assertFalse(target.exists())
            quarantines = list(root.glob(
                ".receipt.json.hepta-quarantine-*"))
            self.assertEqual(len(quarantines), 1)
            self.assertEqual(
                (quarantines[0] / "payload").read_bytes(), b"receipt\n")

    def test_atomic_write_preserves_requested_owner_group_and_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "receipt.json"
            payload = b'{"status":"PASS"}\n'
            installer.atomic_write(
                path,
                payload,
                0o440,
                owner_uid=installer.os.geteuid(),
                owner_gid=installer.os.getegid(),
            )
            metadata = path.lstat()
            self.assertEqual(metadata.st_uid, installer.os.geteuid())
            self.assertEqual(metadata.st_gid, installer.os.getegid())
            self.assertEqual(metadata.st_mode & 0o7777, 0o440)
            self.assertEqual(path.read_bytes(), payload)
            self.assertEqual(
                installer.stable_verify_file(
                    path,
                    payload,
                    0o440,
                    expected_uid=installer.os.geteuid(),
                    expected_gid=installer.os.getegid(),
                ),
                installer.digest_bytes(payload),
            )
            path.chmod(0o400)
            with self.assertRaisesRegex(
                    installer.InstallError, "RECEIPT_POST_VERIFY_FAILED"):
                installer.stable_verify_file(
                    path,
                    payload,
                    0o440,
                    expected_uid=installer.os.geteuid(),
                    expected_gid=installer.os.getegid(),
                )
            path.chmod(0o440)
            with self.assertRaisesRegex(
                    installer.InstallError, "RECEIPT_POST_VERIFY_FAILED"):
                installer.stable_verify_file(
                    path,
                    payload,
                    0o440,
                    expected_uid=installer.os.geteuid(),
                    expected_gid=installer.os.getegid() + 1,
                )
            path.chmod(0o640)
            path.write_bytes(b'{"status":"TAMPERED"}\n')
            path.chmod(0o440)
            with self.assertRaisesRegex(
                    installer.InstallError, "RECEIPT_POST_VERIFY_FAILED"):
                installer.stable_verify_file(
                    path,
                    payload,
                    0o440,
                    expected_uid=installer.os.geteuid(),
                    expected_gid=installer.os.getegid(),
                )

    def test_receipt_publish_uses_root_reader_group_and_read_only_mode(
            self) -> None:
        receipt = {"schema": installer.RECEIPT_SCHEMA, "reader_gid": 1000}
        expected_payload = installer.canonical_bytes(receipt)
        with mock.patch.object(
                installer, "_open_anchored_directory", return_value=91) as opened, \
                mock.patch.object(
                    installer, "_stat_optional", return_value=None), \
                mock.patch.object(installer, "_atomic_write_at") as atomic, \
                mock.patch.object(installer.os, "close") as close, \
                mock.patch.object(
                    installer,
                    "stable_verify_file",
                    return_value=installer.digest_bytes(expected_payload),
                ) as verify:
            digest = installer.publish_install_receipt(
                Path("/receipt.json"), receipt, 1000)
        opened.assert_called_once_with(
            Path("/"), create=True, owner_uid=0, owner_gid=0,
            strict_ancestors=True, transaction_lock=None)
        atomic.assert_called_once_with(
            Path("/"), 91, "receipt.json", expected_payload, 0o440,
            owner_uid=0,
            owner_gid=1000,
            expected=None,
            expected_payload=None,
            reason="INSTALL_RECEIPT_PUBLISH_FAILED",
            strict_ancestors=True,
            transaction_lock=None,
        )
        close.assert_called_once_with(91)
        verify.assert_called_once_with(
            Path("/receipt.json"),
            expected_payload,
            0o440,
            expected_uid=0,
            expected_gid=1000,
        )
        self.assertEqual(digest, installer.digest_bytes(expected_payload))

    def test_builder_requires_exact_packaged_installer_binding(
            self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            old_installer = root / "old-installer"
            new_installer = root / "new-installer"
            old_installer.write_bytes(b"old installer\n")
            new_installer.write_bytes(b"new installer\n")
            archive = root / "runtime.tar.gz"
            write_archive(archive, [{
                "name": "usr/libexec/hepta-shadow-host-installer",
                "payload": new_installer.read_bytes(),
                "mode": 0o755,
            }, PAPER_IDENTITY_ENTRY])
            baseline = "sha256:" + "a" * 64
            with self.assertRaisesRegex(
                    installer.InstallError, "INSTALLER_BINDING_INVALID"):
                builder.build(archive, baseline, old_installer)
            final = builder.build(archive, baseline, new_installer)
            final_repeat = builder.build(archive, baseline, new_installer)
            self.assertEqual(
                installer.canonical_bytes(final),
                installer.canonical_bytes(final_repeat),
            )
            self.assertEqual(
                final["installer_sha256"],
                installer.digest_file(new_installer),
            )


if __name__ == "__main__":
    unittest.main()
