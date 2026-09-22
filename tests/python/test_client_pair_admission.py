"""Admit bounded inert SDK archives and verify paired-process evidence, no broker."""
from __future__ import annotations

import copy
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests/research"))
import accept_core_release as acceptance
import installed_client_server_pair as pair

SHA = "a" * 40
CORE = "b" * 64
CLIENT = "c" * 64


def build_info(source=SHA):
    return {"package": "HeptaStrategyClient", "source_sha": source,
            "source_tree_state": "clean", "system": "Linux", "processor": platform.machine(),
            "build_type": "Release", "wire_protocol": "HTT1", "transport": "local-unix-only",
            "execution_authority": "none", "broker_transport": "none", "release_label": "0.3.0"}


def paired_receipt(source=SHA, core=CORE, client=CLIENT):
    """Inert validator/orchestration input; not a real process acceptance claim."""
    return {"schema": "hepta.installed-client-server-pair.v1", "result": "PASS",
            "source_sha": source, "core_sha256": core, "client_sha256": client,
            "client_uid": 61003, "concurrent_clients": 4, "place_send_attempts": 1,
            "place_sent_records": 1, "final_position": 0, "final_active_orders": [],
            "authorization_effect": "NONE", "broker_io": False, "systemd_manager_exercised": False,
            "restart_status_only": True, "pre_send_crash_status_only": True,
            "intent_conflict_rejected": True, "binding_change_rejected": True,
            "original_request_bytes_preserved": True, "relocated_client": True,
            "prepared_commands": ["original-one", "original-two"],
            "client_build_info": build_info(source),
            "processes": [{"name": name, "pid": 200 + i, "uid": uid, "executable_sha256": digest}
                for i, (name, uid, digest) in enumerate([
                    ("hepta-executiond", 61002, "d" * 64), ("hepta-tool-gatewayd", 61001, "e" * 64),
                    ("hepta-executiond", 61002, "d" * 64), ("hepta-tool-gatewayd", 61001, "e" * 64)])]}


def archive(*, info=None, omit=None, extra=None, change=None, metadata_suffix=b""):
    """Only inert bytes; no test archive contains executable code."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as stream:
        for name in sorted(pair.CLIENT_FILES):
            if name == omit:
                continue
            payload = b"inert\n"
            if name.endswith("strategy-client-build-info.txt"):
                payload = "".join(f"{k}={v}\n" for k, v in (info or build_info()).items()).encode() + metadata_suffix
            item = tarfile.TarInfo("./" + name)
            item.size = len(payload)
            item.mode = 0o755 if name.startswith("bin/") else 0o644
            if change:
                change(item)
            stream.addfile(item, io.BytesIO(payload))
        if extra:
            stream.addfile(extra, io.BytesIO(b"x" * extra.size))
    return buffer.getvalue()


class ClientPackageAdmissionTests(unittest.TestCase):
    def test_exact_inventory_and_metadata_are_consumed_from_captured_bytes(self):
        files, info = pair.client_members(archive(), SHA)
        self.assertEqual(set(files), pair.CLIENT_FILES)
        self.assertEqual(info, build_info())
        self.assertEqual(files["bin/hepta-strategy-native"], (b"inert\n", 0o755))

    def test_install_relocates_only_the_verified_regular_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "client.tar.gz"
            data = archive(); source.write_bytes(data)
            prefix, info = pair.install_client(source, hashlib.sha256(data).hexdigest(), SHA, root / "install")
            self.assertEqual(prefix.name, "relocated")
            self.assertFalse((root / "install/original").exists())
            self.assertEqual({p.relative_to(prefix).as_posix() for p in prefix.rglob("*") if p.is_file()}, pair.CLIENT_FILES)
            self.assertEqual(info["source_sha"], SHA)
            self.assertEqual((prefix / "bin/hepta-strategy-gateway").stat().st_mode & 0o777, 0o755)

    def test_missing_and_duplicate_files_reject(self):
        with self.assertRaisesRegex(ValueError, "incomplete"):
            pair.client_members(archive(omit="bin/hepta-strategy-native"), SHA)
        duplicate = tarfile.TarInfo("bin/hepta-strategy-native"); duplicate.mode = 0o755
        with self.assertRaisesRegex(ValueError, "duplicate"):
            pair.client_members(archive(extra=duplicate), SHA)

    def test_foreign_paths_and_types_are_not_extracted(self):
        for name in ("../outside", "/absolute", "bin/../outside", "bin//nested", "bin\\evil", "unexpected"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                pair.client_members(archive(extra=tarfile.TarInfo(name)), SHA)
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE, tarfile.CHRTYPE, tarfile.BLKTYPE):
            def change(item):
                if item.name.endswith("hepta-strategy-native"):
                    item.type = kind; item.linkname = "/bin/true"; item.size = 0
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                pair.client_members(archive(change=change), SHA)

    def test_unsafe_modes_and_nonexecutable_launcher_reject(self):
        for mode in (0o4755, 0o777, 0o644):
            def change(item):
                if item.name.endswith("hepta-strategy-native"):
                    item.mode = mode
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                pair.client_members(archive(change=change), SHA)

    def test_metadata_binding_and_source_format_reject(self):
        for field, value in (("source_sha", "f" * 40), ("source_tree_state", "dirty"),
                             ("system", "Windows"), ("processor", "other-architecture"),
                             ("transport", "tcp"), ("execution_authority", "live"),
                             ("broker_transport", "enabled"), ("build_type", "Debug")):
            info = build_info(); info[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                pair.client_members(archive(info=info), SHA)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            pair.client_members(archive(metadata_suffix=b"source_sha=" + SHA.encode() + b"\n"), SHA)
        with self.assertRaisesRegex(ValueError, "source identity"):
            pair.client_members(archive(), "not-a-sha")

    def test_compressed_and_expanded_bounds_reject(self):
        data = archive()
        with mock.patch.object(pair, "MAX_PACKAGE_BYTES", len(data) - 1):
            with self.assertRaisesRegex(ValueError, "size bound"):
                pair.client_members(data, SHA)
        extra = tarfile.TarInfo("extra"); extra.size = 10000
        # A single known member can be large even when highly compressed.
        def change(item):
            if item.name.endswith("hepta-strategy-native"):
                item.size = 10000
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as stream:
            item = tarfile.TarInfo("bin/hepta-strategy-native")
            item.mode = 0o755; item.size = 10000
            stream.addfile(item, io.BytesIO(b"x" * item.size))
        with mock.patch.object(pair, "MAX_PACKAGE_BYTES", 1000):
            with self.assertRaises(ValueError):
                pair.client_members(buffer.getvalue(), SHA)

    def test_corrupt_payload_and_wrong_digest_reject_before_install(self):
        with self.assertRaises(tarfile.TarError):
            pair.client_members(b"not gzip", SHA)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / "client.tar.gz"; source.write_bytes(archive())
            with self.assertRaises((ValueError, RuntimeError)):
                pair.install_client(source, "0" * 64, SHA, root / "wrong")
            self.assertFalse((root / "wrong/relocated").exists())

    def test_host_requires_explicit_opt_in_and_preserves_existing_interlock(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "existing"; path.mkdir(); (path / "owned-elsewhere").write_text("keep")
            with mock.patch.object(pair, "HOST_INTERLOCK", path), mock.patch.object(pair.os, "geteuid", return_value=0):
                with mock.patch.dict(os.environ, {"HEPTA_ISOLATED_PROCESS_TESTS": ""}):
                    with self.assertRaises(RuntimeError), pair.isolated_interlock():
                        self.fail("not opted in")
                with mock.patch.dict(os.environ, {"HEPTA_ISOLATED_PROCESS_TESTS": "1"}):
                    with self.assertRaises(FileExistsError), pair.isolated_interlock():
                        self.fail("must not adopt prior state")
                self.assertEqual((path / "owned-elsewhere").read_text(), "keep")


class ClientPairEvidenceTests(unittest.TestCase):
    def validate(self, value):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "receipt.json"; path.write_text(json.dumps(value))
            return acceptance.validate_client_pair_evidence(path, SHA, CORE, CLIENT)

    def test_valid_pair_requires_two_distinct_packages_and_two_service_generations(self):
        self.assertEqual(self.validate(paired_receipt()), paired_receipt())

    def test_missing_malformed_and_null_evidence_reject(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "absent"
            with self.assertRaises(ValueError):
                acceptance.validate_client_pair_evidence(path, SHA, CORE, CLIENT)
        for bad in (None, [], {}, "PASS"):
            with self.subTest(value=bad), self.assertRaises(ValueError):
                self.validate(bad)

    def test_each_required_scenario_and_identity_is_checked(self):
        baseline = paired_receipt()
        for field in baseline:
            bad = copy.deepcopy(baseline); del bad[field]
            with self.subTest(missing=field), self.assertRaises(ValueError):
                self.validate(bad)
        for field, value in (("source_sha", "f" * 40), ("core_sha256", CLIENT),
                             ("client_sha256", CORE), ("place_send_attempts", 2),
                             ("place_sent_records", 0), ("place_sent_records", True),
                             ("client_uid", 0), ("broker_io", True), ("final_position", False),
                             ("systemd_manager_exercised", True), ("prepared_commands", ["a", "a"]),
                             ("prepared_commands", [1, 2]), ("restart_status_only", False)):
            bad = copy.deepcopy(baseline); bad[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                self.validate(bad)

    def test_changed_runtime_or_client_identity_is_not_accepted(self):
        for change in (lambda x: x["processes"].pop(),
                       lambda x: x["processes"][2].update(pid=200),
                       lambda x: x["processes"][0].update(uid=0),
                       lambda x: x["processes"][0].update(name="other-executable"),
                       lambda x: x["processes"][2].update(executable_sha256="f" * 64),
                       lambda x: x["client_build_info"].update(source_sha="f" * 40),
                       lambda x: x["client_build_info"].update(execution_authority="live")):
            bad = paired_receipt(); change(bad)
            with self.assertRaises(ValueError):
                self.validate(bad)


if __name__ == "__main__":
    unittest.main()
