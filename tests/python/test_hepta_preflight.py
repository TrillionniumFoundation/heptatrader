from __future__ import annotations

import argparse
import copy
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import build_release_package as release  # noqa: E402
import hepta_preflight as preflight  # noqa: E402


SOURCE_SHA = "2" * 40
EPOCH = 1700000000
VERSION = "0.1.0-beta.1"
POLICY = ROOT / "docs/preflight-policy-v1.json"


def fixture_tree(root: Path, *, ib: bool = False) -> Path:
    paths = [
        "bin/hepta-executiond",
        "bin/hepta-preflight",
        "bin/hepta-sessionctl",
        "bin/hepta-tool-gatewayd",
        "bin/heptactl",
        "lib/systemd/system/hepta-execution-simulator.service",
        "share/doc/heptatrader/index.md",
        "share/heptatrader/preflight-policy-v1.json",
    ]
    if ib:
        paths.extend(
            [
                "bin/hepta-ib-executiond",
                "lib/systemd/system/hepta-broker-egress-policy.service",
                "lib/systemd/system/hepta-execution-ib-paper.service",
                "share/heptatrader/ib-paper-profile-policy-v1.json",
            ]
        )
    for relative in paths:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(relative + "\n", encoding="utf-8")
        path.chmod(0o755 if relative.startswith("bin/") else 0o644)
    build_info = {
        "schema": "heptatrader.installed-build.v1",
        "release_label": VERSION,
        "project_version": "0.1.0",
        "ib_api_compiled": ib,
        "paper_authorized": False,
        "live_authorized": False,
    }
    path = root / "share/heptatrader/heptatrader-build-info.json"
    path.write_text(json.dumps(build_info, sort_keys=True) + "\n", encoding="utf-8")
    return root


def args_for(artifact: Path, digest: str, *, profile: str = "core") -> argparse.Namespace:
    return argparse.Namespace(
        artifact=artifact,
        expected_sha256=digest,
        profile=profile,
        policy=POLICY,
        output=None,
        artifact_only=True,
        host_root=Path("/"),
        execution_uid=None,
        gateway_uid=None,
        kill_switch_path=None,
        probe_broker=False,
        broker_host="127.0.0.1",
        broker_port=4002,
        broker_timeout=0.1,
    )


def check(receipt: dict, check_id: str) -> dict:
    return next(item for item in receipt["checks"] if item["id"] == check_id)


def write_manual_archive(path: Path, members: list[tarfile.TarInfo], bodies: list[bytes | None]) -> str:
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.GNU_FORMAT) as archive:
        for member, body in zip(members, bodies):
            archive.addfile(member, io.BytesIO(body) if body is not None else None)
    compressed = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=compressed, mtime=EPOCH) as stream:
        stream.write(raw.getvalue())
    path.write_bytes(compressed.getvalue())
    return hashlib.sha256(path.read_bytes()).hexdigest()


def regular_info(name: str, body: bytes) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.size = len(body)
    info.mode = 0o644
    info.mtime = EPOCH
    info.uid = 0
    info.gid = 0
    info.uname = "root"
    info.gname = "root"
    return info


class HeptaPreflightTests(unittest.TestCase):
    def valid_package(self, work: Path, *, profile: str = "core") -> tuple[Path, dict]:
        output = work / f"{profile}.tar.gz"
        receipt = release.package_install_root(
            fixture_tree(work / "root", ib=profile == "ib-paper"),
            output,
            version=VERSION,
            profile=profile,
            source_sha=SOURCE_SHA,
            source_date_epoch=EPOCH,
        )
        return output, receipt

    def test_valid_artifact_only_preflight_passes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            artifact, package = self.valid_package(Path(directory))
            receipt = preflight.run_preflight(args_for(artifact, package["package_sha256"]))
            self.assertEqual(receipt["result"], "PASS")
            self.assertEqual(receipt["scope"], "ARTIFACT_ONLY")

    def test_receipt_never_grants_authority(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            artifact, package = self.valid_package(Path(directory))
            receipt = preflight.run_preflight(args_for(artifact, package["package_sha256"]))
            self.assertEqual(receipt["authorization_effect"], "NONE")
            self.assertIs(receipt["paper_authorized"], False)
            self.assertIs(receipt["live_authorized"], False)

    def test_digest_mismatch_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            artifact, _ = self.valid_package(Path(directory))
            receipt = preflight.run_preflight(args_for(artifact, "0" * 64))
            self.assertEqual(receipt["result"], "FAIL")

    def test_missing_required_package_file_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            root = fixture_tree(work / "root")
            (root / "bin/heptactl").unlink()
            output = work / "release.tar.gz"
            package = release.package_install_root(
                root,
                output,
                version=VERSION,
                profile="core",
                source_sha=SOURCE_SHA,
                source_date_epoch=EPOCH,
            )
            receipt = preflight.run_preflight(args_for(output, package["package_sha256"]))
            self.assertEqual(receipt["result"], "FAIL")

    def test_archive_path_traversal_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "bad.tar.gz"
            body = b"{}\n"
            member = regular_info("heptatrader-x-core/../manifest.json", body)
            digest = write_manual_archive(artifact, [member], [body])
            policy = preflight._load_policy(POLICY)
            with self.assertRaises(preflight.PreflightError):
                preflight.inspect_archive(artifact, digest, policy, "core")

    def test_archive_symlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "bad.tar.gz"
            body = b"{}\n"
            manifest = regular_info("heptatrader-x-core/manifest.json", body)
            link = tarfile.TarInfo("heptatrader-x-core/bin/heptactl")
            link.type = tarfile.SYMTYPE
            link.linkname = "/tmp/target"
            link.mode = 0o777
            link.mtime = EPOCH
            link.uid = 0
            link.gid = 0
            digest = write_manual_archive(artifact, [manifest, link], [body, None])
            policy = preflight._load_policy(POLICY)
            with self.assertRaises(preflight.PreflightError):
                preflight.inspect_archive(artifact, digest, policy, "core")

    def test_archive_owner_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "bad.tar.gz"
            body = b"{}\n"
            manifest = regular_info("heptatrader-x-core/manifest.json", body)
            manifest.uid = 1000
            digest = write_manual_archive(artifact, [manifest], [body])
            policy = preflight._load_policy(POLICY)
            with self.assertRaisesRegex(preflight.PreflightError, "ownership"):
                preflight.inspect_archive(artifact, digest, policy, "core")

    def test_payload_mode_mismatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            artifact, package = self.valid_package(work)
            members: list[tarfile.TarInfo] = []
            bodies: list[bytes | None] = []
            with tarfile.open(artifact, "r:gz") as source:
                for original in source.getmembers():
                    member = copy.copy(original)
                    stream = source.extractfile(original) if original.isreg() else None
                    body = stream.read() if stream is not None else None
                    if member.name.endswith("/bin/heptactl"):
                        member.mode = 0o600
                    members.append(member)
                    bodies.append(body)
            tampered = work / "tampered.tar.gz"
            digest = write_manual_archive(tampered, members, bodies)
            self.assertNotEqual(digest, package["package_sha256"])
            policy = preflight._load_policy(POLICY)
            with self.assertRaisesRegex(preflight.PreflightError, "mode mismatch"):
                preflight.inspect_archive(tampered, digest, policy, "core")

    def test_duplicate_manifest_key_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "bad.tar.gz"
            body = b'{"schema":"x","schema":"y"}\n'
            member = regular_info("heptatrader-x-core/manifest.json", body)
            digest = write_manual_archive(artifact, [member], [body])
            policy = preflight._load_policy(POLICY)
            with self.assertRaises(preflight.PreflightError):
                preflight.inspect_archive(artifact, digest, policy, "core")

    def test_duplicate_policy_key_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            path.write_text('{"schema":"x","schema":"y"}\n', encoding="utf-8")
            with self.assertRaises(preflight.PreflightError):
                preflight._load_policy(path)

    def test_manifest_cannot_claim_live_authority(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            root = fixture_tree(work / "root")
            payload = release.collect_payload(root)
            manifest = release.build_manifest(payload, VERSION, "core", SOURCE_SHA, EPOCH)
            manifest["authorization"]["live_authorized"] = True
            archive_bytes, _ = release.archive_bytes(payload, manifest, VERSION, "core", EPOCH)
            artifact = work / "bad.tar.gz"
            artifact.write_bytes(archive_bytes)
            digest = hashlib.sha256(archive_bytes).hexdigest()
            policy = preflight._load_policy(POLICY)
            with self.assertRaises(preflight.PreflightError):
                preflight.inspect_archive(artifact, digest, policy, "core")

    def test_ib_package_requires_ib_enabled_build_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            root = fixture_tree(work / "root", ib=True)
            build_info = root / "share/heptatrader/heptatrader-build-info.json"
            value = json.loads(build_info.read_text(encoding="utf-8"))
            value["ib_api_compiled"] = False
            build_info.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
            artifact = work / "ib-paper.tar.gz"
            package = release.package_install_root(
                root,
                artifact,
                version=VERSION,
                profile="ib-paper",
                source_sha=SOURCE_SHA,
                source_date_epoch=EPOCH,
            )
            receipt = preflight.run_preflight(
                args_for(artifact, package["package_sha256"], profile="ib-paper")
            )
            self.assertEqual(receipt["result"], "FAIL")
            self.assertIn("not built with the IB API", check(receipt, "artifact.integrity")["detail"])

    def test_static_host_missing_required_file_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            artifact, package = self.valid_package(work)
            host = work / "host"
            fixture_tree(host / "usr")
            (host / "usr/bin/heptactl").unlink()
            args = args_for(artifact, package["package_sha256"])
            args.artifact_only = False
            args.host_root = host
            with mock.patch.object(preflight.shutil, "which", return_value="/usr/bin/systemctl"):
                receipt = preflight.run_preflight(args)
            self.assertEqual(receipt["result"], "FAIL")
            self.assertIn("heptactl", check(receipt, "host.static")["detail"])

    def test_static_host_required_symlink_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            artifact, package = self.valid_package(work)
            host = work / "host"
            fixture_tree(host / "usr")
            target = host / "usr/bin/heptactl"
            target.unlink()
            target.symlink_to("hepta-executiond")
            args = args_for(artifact, package["package_sha256"])
            args.artifact_only = False
            args.host_root = host
            with mock.patch.object(preflight.shutil, "which", return_value="/usr/bin/systemctl"):
                receipt = preflight.run_preflight(args)
            self.assertEqual(receipt["result"], "FAIL")
            self.assertIn("non-symlink", check(receipt, "host.static")["detail"])

    def test_ib_static_host_requires_distinct_positive_uids(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            artifact, package = self.valid_package(work, profile="ib-paper")
            host = work / "host"
            fixture_tree(host / "usr", ib=True)
            kill_switch = work / "kill-switch"
            kill_switch.write_text("engaged\n", encoding="utf-8")
            kill_switch.chmod(0o600)
            args = args_for(artifact, package["package_sha256"], profile="ib-paper")
            args.artifact_only = False
            args.host_root = host
            args.execution_uid = 1000
            args.gateway_uid = 1000
            args.kill_switch_path = kill_switch
            with mock.patch.object(preflight.shutil, "which", return_value="/usr/bin/tool"):
                receipt = preflight.run_preflight(args)
            self.assertEqual(receipt["result"], "FAIL")
            self.assertIn("distinct positive", check(receipt, "host.static")["detail"])

    def test_kill_switch_rejects_relative_path(self) -> None:
        self.assertIn("absolute", preflight._safe_kill_switch(Path("relative-marker")) or "")

    def test_kill_switch_rejects_world_writable_root_marker(self) -> None:
        fake = os.stat_result((stat.S_IFREG | 0o666, 1, 1, 1, 0, 0, 1, 0, 0, 0))
        with mock.patch.object(Path, "lstat", return_value=fake):
            problem = preflight._safe_kill_switch(Path("/run/heptatrader/kill-switch"))
        self.assertIn("group/world writable", problem or "")

    def test_broker_probe_is_rejected_for_core_profile(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            artifact, package = self.valid_package(Path(directory))
            args = args_for(artifact, package["package_sha256"])
            args.probe_broker = True
            receipt = preflight.run_preflight(args)
            self.assertEqual(receipt["result"], "FAIL")
            self.assertIn("only for ib-paper", check(receipt, "broker.reachability")["detail"])

    def test_broker_probe_rejects_endpoint_outside_policy_without_connecting(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            artifact, package = self.valid_package(Path(directory), profile="ib-paper")
            args = args_for(artifact, package["package_sha256"], profile="ib-paper")
            args.probe_broker = True
            args.broker_port = 4001
            with mock.patch.object(preflight.socket, "create_connection") as connect:
                receipt = preflight.run_preflight(args)
            connect.assert_not_called()
            self.assertEqual(receipt["result"], "FAIL")
            self.assertIn("outside the PAPER policy", check(receipt, "broker.reachability")["detail"])

    def test_receipt_output_is_never_replaced(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "receipt.json"
            output.write_text("sentinel\n", encoding="utf-8")
            with self.assertRaises(preflight.PreflightError):
                preflight._write_private_receipt(output, {"result": "PASS"})
            self.assertEqual(output.read_text(encoding="utf-8"), "sentinel\n")


if __name__ == "__main__":
    unittest.main()
