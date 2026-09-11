from __future__ import annotations

import gzip
import hashlib
import io
import json
from pathlib import Path
import stat
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import run_release_simulator_smoke as smoke  # noqa: E402


def package(path: Path, *, executable_body: bytes = b"#!/bin/sh\nexit 0\n") -> str:
    root = "heptatrader-test-core"
    entries = [
        ("manifest.json", b"{}\n", 0o644, tarfile.REGTYPE),
        (
            "libexec/heptatrader/hepta_agent_simulator_e2e_tests",
            executable_body,
            0o755,
            tarfile.REGTYPE,
        ),
    ]
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for relative, body, mode, kind in entries:
            member = tarfile.TarInfo(f"{root}/{relative}")
            member.size = len(body)
            member.mode = mode
            member.mtime = 1700000000
            member.uid = 0
            member.gid = 0
            member.uname = "root"
            member.gname = "root"
            member.type = kind
            archive.addfile(member, io.BytesIO(body))
    with path.open("wb") as fileobj:
        with gzip.GzipFile(
            filename="", fileobj=fileobj, mode="wb", mtime=1700000000
        ) as output:
            output.write(raw.getvalue())
    return hashlib.sha256(path.read_bytes()).hexdigest()


def admitted_receipt(digest: str) -> dict:
    return {
        "result": "PASS",
        "artifact": {"sha256": digest},
        "authorization_effect": "NONE",
        "paper_authorized": False,
        "live_authorized": False,
    }


class ReleaseSimulatorSmokeTests(unittest.TestCase):
    def test_install_switch_rollback_and_repromotion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = root / "release.tar.gz"
            digest = package(artifact)
            output = root / "record.json"
            with mock.patch.object(
                smoke, "_run_preflight", return_value=admitted_receipt(digest)
            ):
                record = smoke.run_release_smoke(
                    artifact=artifact,
                    expected_sha256=digest,
                    policy=root / "policy.json",
                    preflight=root / "preflight.py",
                    work_root=root / "host",
                    output=output,
                )
            self.assertEqual(record["result"], "PASS")
            self.assertEqual(
                [item["id"] for item in record["checks"]],
                [
                    "candidate.simulator-e2e",
                    "rollback.simulator-e2e",
                    "promotion.simulator-e2e",
                ],
            )
            self.assertFalse(record["broker_mutation"])
            self.assertFalse(record["paper_authorized"])
            self.assertFalse(record["live_authorized"])
            self.assertTrue(output.is_file())
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
            self.assertTrue((root / "host/current").is_symlink())
            self.assertTrue(
                (root / "host/current/libexec/heptatrader/"
                 "hepta_agent_simulator_e2e_tests").is_file()
            )

    def test_private_snapshot_is_bound_before_preflight_and_execution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = root / "release.tar.gz"
            digest = package(artifact)

            def mutate_original(_preflight: Path, snapshot: Path, *args, **kwargs):
                self.assertNotEqual(snapshot, artifact)
                self.assertEqual(hashlib.sha256(snapshot.read_bytes()).hexdigest(), digest)
                artifact.write_bytes(b"replaced-after-private-snapshot\n")
                return admitted_receipt(digest)

            with mock.patch.object(
                smoke, "_run_preflight", side_effect=mutate_original
            ):
                record = smoke.run_release_smoke(
                    artifact=artifact,
                    expected_sha256=digest,
                    policy=root / "policy.json",
                    preflight=root / "preflight.py",
                    work_root=root / "host",
                    output=root / "record.json",
                )
            self.assertEqual(record["result"], "PASS")
            self.assertEqual(record["artifact"]["sha256"], digest)
            self.assertEqual(record["artifact"]["snapshot"], "input/release.tar.gz")

    def test_existing_record_is_never_replaced(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = root / "release.tar.gz"
            digest = package(artifact)
            output = root / "record.json"
            output.write_bytes(b"sentinel\n")
            with mock.patch.object(
                smoke, "_run_preflight", return_value=admitted_receipt(digest)
            ):
                with self.assertRaisesRegex(smoke.SmokeError, "refusing to replace"):
                    smoke.run_release_smoke(
                        artifact=artifact,
                        expected_sha256=digest,
                        policy=root / "policy.json",
                        preflight=root / "preflight.py",
                        work_root=root / "host",
                        output=output,
                    )
            self.assertEqual(output.read_bytes(), b"sentinel\n")

    def test_failed_candidate_smoke_rolls_current_to_previous(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = root / "release.tar.gz"
            digest = package(artifact)
            with mock.patch.object(
                smoke, "_run_preflight", return_value=admitted_receipt(digest)
            ), mock.patch.object(
                smoke,
                "_run_installed_smoke",
                side_effect=smoke.SmokeError("injected failure"),
            ):
                with self.assertRaisesRegex(smoke.SmokeError, "injected failure"):
                    smoke.run_release_smoke(
                        artifact=artifact,
                        expected_sha256=digest,
                        policy=root / "policy.json",
                        preflight=root / "preflight.py",
                        work_root=root / "host",
                        output=root / "record.json",
                    )
            self.assertFalse((root / "record.json").exists())
            self.assertTrue((root / "host/current").is_symlink())
            self.assertTrue(os_readlink(root / "host/current").endswith("-previous"))

    def test_record_publication_failure_rolls_current_to_previous(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = root / "release.tar.gz"
            digest = package(artifact)
            with mock.patch.object(
                smoke, "_run_preflight", return_value=admitted_receipt(digest)
            ), mock.patch.object(
                smoke, "_write_record", side_effect=smoke.SmokeError("record race")
            ):
                with self.assertRaisesRegex(smoke.SmokeError, "record race"):
                    smoke.run_release_smoke(
                        artifact=artifact,
                        expected_sha256=digest,
                        policy=root / "policy.json",
                        preflight=root / "preflight.py",
                        work_root=root / "host",
                        output=root / "record.json",
                    )
            self.assertTrue((root / "host/current").is_symlink())
            self.assertTrue(os_readlink(root / "host/current").endswith("-previous"))

    def test_non_regular_archive_member_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = root / "hostile.tar.gz"
            raw = io.BytesIO()
            with tarfile.open(
                fileobj=raw, mode="w", format=tarfile.USTAR_FORMAT
            ) as archive:
                manifest = tarfile.TarInfo("heptatrader-test-core/manifest.json")
                manifest_body = b"{}\n"
                manifest.size = len(manifest_body)
                manifest.mode = 0o644
                manifest.mtime = 1700000000
                manifest.uid = manifest.gid = 0
                manifest.uname = manifest.gname = "root"
                archive.addfile(manifest, io.BytesIO(manifest_body))
                link = tarfile.TarInfo(
                    "heptatrader-test-core/usr/libexec/heptatrader/"
                    "hepta_agent_simulator_e2e_tests"
                )
                link.type = tarfile.SYMTYPE
                link.linkname = "/bin/true"
                link.mode = 0o755
                link.mtime = 1700000000
                archive.addfile(link)
            with artifact.open("wb") as fileobj:
                with gzip.GzipFile(
                    filename="", fileobj=fileobj, mode="wb", mtime=1700000000
                ) as output:
                    output.write(raw.getvalue())
            digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
            with mock.patch.object(
                smoke, "_run_preflight", return_value=admitted_receipt(digest)
            ):
                with self.assertRaisesRegex(smoke.SmokeError, "non-regular member"):
                    smoke.run_release_smoke(
                        artifact=artifact,
                        expected_sha256=digest,
                        policy=root / "policy.json",
                        preflight=root / "preflight.py",
                        work_root=root / "host",
                        output=root / "record.json",
                    )


def os_readlink(path: Path) -> str:
    import os

    return os.readlink(path)


if __name__ == "__main__":
    unittest.main()
