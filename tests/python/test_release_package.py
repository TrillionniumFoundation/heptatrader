from __future__ import annotations

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


SOURCE_SHA = "1" * 40
EPOCH = 1700000000
VERSION = "0.1.0-beta.1"


def fixture_tree(root: Path, *, ib: bool = False) -> Path:
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
        path.write_bytes((relative + "\n").encode("utf-8"))
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


class ReleasePackageTests(unittest.TestCase):
    def package(self, root: Path, output: Path, *, profile: str = "core") -> dict:
        receipt = release.package_install_root(
            root,
            output,
            version=VERSION,
            profile=profile,
            source_sha=SOURCE_SHA,
            source_date_epoch=EPOCH,
        )
        with tarfile.open(output, "r:gz") as archive:
            names = [member.name for member in archive.getmembers()]
        self.assertEqual(len(names), len(set(names)))
        return receipt

    def test_identical_input_produces_identical_archive(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            root = fixture_tree(work / "root")
            first = work / "first.tar.gz"
            second = work / "second.tar.gz"
            receipt_a = self.package(root, first)
            receipt_b = self.package(root, second)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            self.assertEqual(receipt_a["package_sha256"], receipt_b["package_sha256"])

    def test_manifest_is_sorted_and_binds_every_payload_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            output = work / "release.tar.gz"
            root = fixture_tree(work / "root")
            receipt = self.package(root, output)
            with tarfile.open(output, "r:gz") as archive:
                manifest_member = next(
                    item for item in archive.getmembers() if item.name.endswith("/manifest.json")
                )
                stream = archive.extractfile(manifest_member)
                self.assertIsNotNone(stream)
                manifest = json.loads(stream.read())
            paths = [item["path"] for item in manifest["files"]]
            self.assertEqual(paths, sorted(paths))
            self.assertEqual(len(paths), len(set(paths)))
            self.assertEqual(receipt["file_count"], len(paths))
            self.assertEqual(manifest["source_sha"], SOURCE_SHA)

    def test_receipt_cannot_claim_paper_or_live_authority(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            receipt = self.package(fixture_tree(work / "root"), work / "release.tgz")
            self.assertEqual(receipt["authorization_effect"], "NONE")
            self.assertIs(receipt["paper_authorized"], False)
            self.assertIs(receipt["live_authorized"], False)

    def test_generated_manifest_name_is_rejected_without_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            root = fixture_tree(work / "root")
            (root / "manifest.json").write_text("payload\n", encoding="utf-8")
            output = work / "release.tar.gz"
            with self.assertRaisesRegex(
                release.PackageError, "generated archive namespace"
            ):
                self.package(root, output)
            self.assertFalse(output.exists())
            self.assertFalse(Path(str(output) + ".sha256").exists())
            self.assertFalse(Path(str(output) + ".receipt.json").exists())

    def test_generated_manifest_directory_prefix_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            root = fixture_tree(work / "root")
            nested = root / "manifest.json/child"
            nested.parent.mkdir(parents=True)
            nested.write_text("payload\n", encoding="utf-8")
            output = work / "release.tar.gz"
            with self.assertRaisesRegex(
                release.PackageError, "generated archive namespace"
            ):
                self.package(root, output)
            self.assertFalse(output.exists())

    def test_archive_namespace_rejects_file_prefix_collisions(self) -> None:
        admitted: set[str] = set()
        release._admit_payload_path("alpha", admitted)
        with self.assertRaisesRegex(
            release.PackageError, "prefix collision"
        ):
            release._admit_payload_path("alpha/child", admitted)

    def test_symlinked_payload_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            root = fixture_tree(work / "root")
            (root / "bin/link").symlink_to(root / "bin/heptactl")
            with self.assertRaises(release.PackageError):
                self.package(root, work / "release.tar.gz")

    def test_hard_linked_payload_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            root = fixture_tree(work / "root")
            os.link(root / "bin/heptactl", root / "bin/heptactl-copy")
            with self.assertRaises(release.PackageError):
                self.package(root, work / "release.tar.gz")

    def test_private_key_suffix_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            root = fixture_tree(work / "root")
            (root / "share/private.pem").write_text("not-a-real-key\n", encoding="utf-8")
            with self.assertRaises(release.PackageError):
                self.package(root, work / "release.tar.gz")

    def test_noncanonical_source_sha_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            with self.assertRaises(release.PackageError):
                release.package_install_root(
                    fixture_tree(work / "root"),
                    work / "release.tar.gz",
                    version=VERSION,
                    profile="core",
                    source_sha="abc",
                    source_date_epoch=EPOCH,
                )

    def test_existing_output_is_never_replaced(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            root = fixture_tree(work / "root")
            output = work / "release.tar.gz"
            output.write_bytes(b"sentinel")
            with self.assertRaises(release.PackageError):
                self.package(root, output)
            self.assertEqual(output.read_bytes(), b"sentinel")

    def test_concurrent_output_creation_is_never_replaced(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            root = fixture_tree(work / "root")
            output = work / "release.tar.gz"
            original_publish = release._publish_noreplace
            sentinel = b"concurrent-writer\n"

            def competing_publish(
                directory_fd: int, temporary_name: str, final_name: str
            ) -> None:
                if final_name == output.name:
                    descriptor = os.open(
                        final_name,
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                        0o600,
                        dir_fd=directory_fd,
                    )
                    try:
                        os.write(descriptor, sentinel)
                        os.fsync(descriptor)
                    finally:
                        os.close(descriptor)
                original_publish(directory_fd, temporary_name, final_name)

            with mock.patch.object(
                release, "_publish_noreplace", side_effect=competing_publish
            ):
                with self.assertRaises(release.PackageError):
                    self.package(root, output)
            self.assertEqual(output.read_bytes(), sentinel)

    def test_source_mutation_after_snapshot_cannot_change_archive_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            root = fixture_tree(work / "root")
            target = root / "bin/heptactl"
            original_bytes = target.read_bytes()
            output = work / "release.tar.gz"
            original_build_manifest = release.build_manifest

            def mutating_manifest(*args, **kwargs):
                manifest = original_build_manifest(*args, **kwargs)
                target.write_bytes(b"mutated-after-snapshot\n")
                return manifest

            with mock.patch.object(
                release, "build_manifest", side_effect=mutating_manifest
            ):
                self.package(root, output)

            with tarfile.open(output, "r:gz") as archive:
                payload_member = next(
                    item
                    for item in archive.getmembers()
                    if item.name.endswith("/bin/heptactl")
                )
                payload_stream = archive.extractfile(payload_member)
                self.assertIsNotNone(payload_stream)
                archived_bytes = payload_stream.read()
                manifest_member = next(
                    item
                    for item in archive.getmembers()
                    if item.name.endswith("/manifest.json")
                )
                manifest_stream = archive.extractfile(manifest_member)
                self.assertIsNotNone(manifest_stream)
                manifest = json.loads(manifest_stream.read())
            self.assertEqual(archived_bytes, original_bytes)
            entry = next(
                item for item in manifest["files"] if item["path"] == "bin/heptactl"
            )
            self.assertEqual(
                entry["sha256"], release.sha256_bytes(original_bytes)
            )
            self.assertNotEqual(target.read_bytes(), original_bytes)


if __name__ == "__main__":
    unittest.main()
