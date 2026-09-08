from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import build_release_package as release  # noqa: E402
import hepta_preflight as preflight  # noqa: E402


SOURCE_SHA = "2" * 40
EPOCH = 1700000000
VERSION = "0.1.0-beta.1"
POLICY = ROOT / "docs/preflight-policy-v1.json"


def fixture_tree(root: Path) -> Path:
    paths = [
        "bin/hepta-executiond",
        "bin/hepta-sessionctl",
        "bin/hepta-tool-gatewayd",
        "bin/hepta-preflight",
        "bin/heptactl",
        "lib/systemd/system/hepta-execution-simulator.service",
        "share/doc/heptatrader/index.md",
        "share/heptatrader/preflight-policy-v1.json",
    ]
    for relative in paths:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(relative + "\n", encoding="utf-8")
        path.chmod(0o755 if relative.startswith("bin/") else 0o644)
    build_info = {
        "schema": "heptatrader.installed-build.v1",
        "release_label": VERSION,
        "project_version": "0.1.0",
        "ib_api_compiled": False,
        "paper_authorized": False,
        "live_authorized": False,
    }
    path = root / "share/heptatrader/heptatrader-build-info.json"
    path.write_text(json.dumps(build_info, sort_keys=True) + "\n", encoding="utf-8")
    return root


def args_for(artifact: Path, digest: str) -> argparse.Namespace:
    return argparse.Namespace(
        artifact=artifact,
        expected_sha256=digest,
        profile="core",
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
    def valid_package(self, work: Path) -> tuple[Path, dict]:
        output = work / "release.tar.gz"
        receipt = release.package_install_root(
            fixture_tree(work / "root"),
            output,
            version=VERSION,
            profile="core",
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

    def test_duplicate_manifest_key_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "bad.tar.gz"
            body = b'{"schema":"x","schema":"y"}\n'
            member = regular_info("heptatrader-x-core/manifest.json", body)
            digest = write_manual_archive(artifact, [member], [body])
            policy = preflight._load_policy(POLICY)
            with self.assertRaises(preflight.PreflightError):
                preflight.inspect_archive(artifact, digest, policy, "core")

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


if __name__ == "__main__":
    unittest.main()
