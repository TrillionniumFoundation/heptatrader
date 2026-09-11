from __future__ import annotations

import gzip
import hashlib
import io
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import hepta_preflight as preflight  # noqa: E402


EPOCH = 1_700_000_000
SOURCE_SHA = "1" * 40
VERSION = "namespace-test"
ROOT_NAME = f"heptatrader-{VERSION}-core"
POLICY_PATH = "share/heptatrader/preflight-policy-v1.json"
BUILD_INFO_PATH = "share/heptatrader/heptatrader-build-info.json"


def canonical_policy() -> preflight.LoadedPolicy:
    required = sorted([POLICY_PATH, BUILD_INFO_PATH])
    value = {
        "schema": preflight.POLICY_SCHEMA,
        "maximum_archive_members": 64,
        "maximum_member_bytes": 4 * 1024 * 1024,
        "maximum_total_unpacked_bytes": 16 * 1024 * 1024,
        "private_key_suffixes": sorted(preflight.HARD_PRIVATE_KEY_SUFFIXES),
        "profiles": {
            "core": {
                "required_package_paths": required,
                "required_host_commands": [],
                "allowed_broker_hosts": [],
                "allowed_broker_ports": [],
            },
            "ib-paper": {
                "required_package_paths": required,
                "required_host_commands": [],
                "allowed_broker_hosts": ["127.0.0.1"],
                "allowed_broker_ports": [4002],
            },
        },
    }
    raw = preflight.canonical_json(value)
    return preflight.LoadedPolicy(value, raw, Path("policy.json"))


def tar_info(name: str, body: bytes, mode: int = 0o644) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.type = tarfile.REGTYPE
    info.size = len(body)
    info.mode = mode
    info.mtime = EPOCH
    info.uid = 0
    info.gid = 0
    info.uname = "root"
    info.gname = "root"
    return info


def build_archive(
    path: Path, extras: dict[str, bytes]
) -> tuple[str, preflight.LoadedPolicy]:
    policy = canonical_policy()
    build_info = preflight.canonical_json(
        {
            "release_label": VERSION,
            "paper_authorized": False,
            "live_authorized": False,
            "ib_api_compiled": False,
        }
    )
    payloads: dict[str, tuple[bytes, int]] = {
        POLICY_PATH: (policy.raw_bytes, 0o644),
        BUILD_INFO_PATH: (build_info, 0o644),
    }
    for relative, body in extras.items():
        if relative in payloads:
            raise AssertionError(f"duplicate test payload: {relative}")
        payloads[relative] = (body, 0o644)

    files = [
        {
            "path": relative,
            "size": len(body),
            "mode": mode,
            "sha256": hashlib.sha256(body).hexdigest(),
        }
        for relative, (body, mode) in sorted(payloads.items())
    ]
    manifest = {
        "schema": preflight.MANIFEST_SCHEMA,
        "version": VERSION,
        "profile": "core",
        "source_sha": SOURCE_SHA,
        "source_date_epoch": EPOCH,
        "created_by": "scripts/build_release_package.py",
        "authorization": {
            "effect": "NONE",
            "paper_authorized": False,
            "live_authorized": False,
        },
        "files": files,
    }
    manifest_body = preflight.canonical_json(manifest)

    raw = io.BytesIO()
    with tarfile.open(
        fileobj=raw, mode="w", format=tarfile.USTAR_FORMAT
    ) as archive:
        archive.addfile(
            tar_info(f"{ROOT_NAME}/manifest.json", manifest_body),
            io.BytesIO(manifest_body),
        )
        for relative, (body, mode) in sorted(payloads.items()):
            archive.addfile(
                tar_info(f"{ROOT_NAME}/{relative}", body, mode),
                io.BytesIO(body),
            )
    path.write_bytes(gzip.compress(raw.getvalue(), compresslevel=9, mtime=EPOCH))
    return hashlib.sha256(path.read_bytes()).hexdigest(), policy


class CompleteArchiveNamespaceTests(unittest.TestCase):
    def test_non_adjacent_prefix_collision_fails_before_payload_consumption(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "collision.tar.gz"
            digest, policy = build_archive(
                artifact,
                {
                    "share/doc/heptatrader/a": b"ancestor\n",
                    "share/doc/heptatrader/a-legal": b"legal-neighbour\n",
                    "share/doc/heptatrader/a/child": b"descendant\n",
                },
            )
            consumed: list[str] = []
            original = preflight._BoundedTarReader.consume

            def tracking_consume(
                reader: preflight._BoundedTarReader,
                member: preflight._CanonicalTarMember,
                *,
                retain_limit: int | None = None,
            ) -> tuple[str, bytes | None]:
                consumed.append(member.name)
                return original(reader, member, retain_limit=retain_limit)

            with mock.patch.object(
                preflight._BoundedTarReader,
                "consume",
                new=tracking_consume,
            ):
                with self.assertRaisesRegex(
                    preflight.PreflightError,
                    "file/directory prefix collision",
                ):
                    preflight.inspect_archive(artifact, digest, policy, "core")

            self.assertEqual(consumed, [f"{ROOT_NAME}/manifest.json"])

    def test_lexical_neighbour_without_prefix_relationship_passes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "valid.tar.gz"
            digest, policy = build_archive(
                artifact,
                {
                    "share/doc/heptatrader/a": b"first\n",
                    "share/doc/heptatrader/a-legal": b"second\n",
                },
            )
            manifest, observed, _ = preflight.inspect_archive(
                artifact, digest, policy, "core"
            )
            self.assertEqual(observed, digest)
            paths = {item["path"] for item in manifest["files"]}
            self.assertIn("share/doc/heptatrader/a", paths)
            self.assertIn("share/doc/heptatrader/a-legal", paths)


if __name__ == "__main__":
    unittest.main()
