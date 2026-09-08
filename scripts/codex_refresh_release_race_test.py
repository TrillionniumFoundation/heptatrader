#!/usr/bin/env python3
"""Refresh the hostile release-race test with policy-derived fixtures."""
from pathlib import Path

TEST = '''from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import build_release_package as release  # noqa: E402
import hepta_preflight as preflight  # noqa: E402


SOURCE_SHA = "1" * 40
EPOCH = 1700000000
VERSION = "0.1.0-beta.1"
POLICY = ROOT / "docs/preflight-policy-v1.json"


def fixture_tree(root: Path, marker: str = "original") -> Path:
    policy = json.loads(POLICY.read_text(encoding="utf-8"))
    required = policy["profiles"]["core"]["required_package_paths"]
    for relative in required:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"{marker}:{relative}\\n".encode("utf-8"))
        executable = relative.startswith(("bin/", "libexec/"))
        path.chmod(0o755 if executable else 0o644)

    build_info = {
        "schema": "heptatrader.installed-build.v1",
        "release_label": VERSION,
        "project_version": "0.1.0",
        "ib_api_compiled": False,
        "paper_authorized": False,
        "live_authorized": False,
    }
    path = root / "share/heptatrader/heptatrader-build-info.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(build_info, sort_keys=True, separators=(",", ":")) + "\\n",
        encoding="utf-8",
    )
    path.chmod(0o644)
    return root


def package(root: Path, output: Path) -> dict:
    return release.package_install_root(
        root,
        output,
        version=VERSION,
        profile="core",
        source_sha=SOURCE_SHA,
        source_date_epoch=EPOCH,
    )


def create_competing_file(directory_fd: int, final_name: str, body: bytes) -> None:
    descriptor = os.open(
        final_name,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
        dir_fd=directory_fd,
    )
    try:
        view = memoryview(body)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short competitor write")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class ReleaseRaceBoundaryTests(unittest.TestCase):
    def test_concurrent_package_publisher_cannot_be_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            output = work / "release.tar.gz"
            sentinel = b"competitor-won\\n"
            original_publish = release._publish_noreplace

            def race(directory_fd: int, temporary_name: str, final_name: str) -> None:
                create_competing_file(directory_fd, final_name, sentinel)
                original_publish(directory_fd, temporary_name, final_name)

            with mock.patch.object(release, "_publish_noreplace", side_effect=race):
                with self.assertRaisesRegex(
                    release.PackageError, "refusing to replace existing output"
                ):
                    package(fixture_tree(work / "root"), output)

            self.assertEqual(output.read_bytes(), sentinel)
            self.assertFalse(Path(str(output) + ".sha256").exists())
            self.assertFalse(Path(str(output) + ".receipt.json").exists())

    def test_payload_archive_uses_the_bytes_hashed_into_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            root = fixture_tree(work / "root")
            target = root / "bin/heptactl"
            original = target.read_bytes()
            payload = release.collect_payload(root)
            try:
                manifest = release.build_manifest(
                    payload, VERSION, "core", SOURCE_SHA, EPOCH
                )
                target.write_bytes(b"mutated-after-snapshot\\n")
                archive_bytes, _ = release.archive_bytes(
                    payload, manifest, VERSION, "core", EPOCH
                )
            finally:
                release.close_payload(payload)

            with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as archive:
                member = archive.getmember(
                    f"heptatrader-{VERSION}-core/bin/heptactl"
                )
                stream = archive.extractfile(member)
                self.assertIsNotNone(stream)
                archived = stream.read()
                manifest_member = archive.getmember(
                    f"heptatrader-{VERSION}-core/manifest.json"
                )
                manifest_stream = archive.extractfile(manifest_member)
                self.assertIsNotNone(manifest_stream)
                emitted_manifest = json.loads(manifest_stream.read())

            self.assertEqual(archived, original)
            entry = next(
                item
                for item in emitted_manifest["files"]
                if item["path"] == "bin/heptactl"
            )
            self.assertEqual(entry["sha256"], hashlib.sha256(original).hexdigest())
            self.assertNotEqual(archived, target.read_bytes())

    def test_preflight_rejects_path_swap_after_pinning_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            artifact = work / "release.tar.gz"
            original_receipt = package(fixture_tree(work / "root-a", "a"), artifact)
            replacement = work / "replacement.tar.gz"
            package(fixture_tree(work / "root-b", "b"), replacement)
            policy = preflight._load_policy(POLICY)
            original_inspect = preflight._inspect_archive_stream

            def swap(stream, selected_policy, profile):
                os.replace(replacement, artifact)
                return original_inspect(stream, selected_policy, profile)

            with mock.patch.object(
                preflight, "_inspect_archive_stream", side_effect=swap
            ):
                with self.assertRaisesRegex(
                    preflight.PreflightError, "changed or was replaced"
                ):
                    preflight.inspect_archive(
                        artifact,
                        original_receipt["package_sha256"],
                        policy,
                        "core",
                    )

    def test_concurrent_receipt_publisher_cannot_be_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "preflight.json"
            sentinel = b"independent-receipt\\n"
            original_publish = preflight._publish_noreplace

            def race(directory_fd: int, temporary_name: str, final_name: str) -> None:
                create_competing_file(directory_fd, final_name, sentinel)
                original_publish(directory_fd, temporary_name, final_name)

            with mock.patch.object(preflight, "_publish_noreplace", side_effect=race):
                with self.assertRaisesRegex(
                    preflight.PreflightError,
                    "refusing to replace existing receipt",
                ):
                    preflight._write_private_receipt(
                        path,
                        {
                            "schema": preflight.RECEIPT_SCHEMA,
                            "result": "PASS",
                            "authorization_effect": "NONE",
                            "paper_authorized": False,
                            "live_authorized": False,
                        },
                    )

            self.assertEqual(path.read_bytes(), sentinel)


if __name__ == "__main__":
    unittest.main()
'''

path = Path(__file__).resolve().parents[1] / "tests/python/test_release_security_races.py"
path.parent.mkdir(parents=True, exist_ok=True)
compile(TEST, str(path), "exec")
path.write_text(TEST, encoding="utf-8")
