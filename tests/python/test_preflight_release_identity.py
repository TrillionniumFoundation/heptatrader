from __future__ import annotations

import gzip
import hashlib
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest import mock

import test_hepta_preflight as fixtures

preflight = fixtures.preflight
release = fixtures.release

_UNSET = object()


def _build_valid_package(work: Path) -> tuple[Path, str]:
    artifact = work / "valid-ib-paper.tar.gz"
    receipt = release.package_install_root(
        fixtures.fixture_tree(work / "root", ib=True),
        artifact,
        version=fixtures.VERSION,
        profile="ib-paper",
        source_sha=fixtures.SOURCE_SHA,
        source_date_epoch=fixtures.EPOCH,
    )
    return artifact, receipt["package_sha256"]


def _rewrite_archive(
    source: Path,
    target: Path,
    *,
    root_name: str | None = None,
    manifest_version: object = _UNSET,
    manifest_profile: object = _UNSET,
) -> str:
    entries: list[tuple[tarfile.TarInfo, bytes]] = []
    with tarfile.open(source, mode="r:gz") as archive:
        for original in archive.getmembers():
            stream = archive.extractfile(original)
            if stream is None:
                raise AssertionError(f"fixture member is not a regular file: {original.name}")
            body = stream.read()
            observed_root, separator, relative = original.name.partition("/")
            if not separator or not relative:
                raise AssertionError(f"fixture member lacks a root: {original.name}")
            effective_root = root_name if root_name is not None else observed_root
            if relative == "manifest.json":
                manifest = json.loads(body.decode("utf-8"))
                if manifest_version is not _UNSET:
                    manifest["version"] = manifest_version
                if manifest_profile is not _UNSET:
                    manifest["profile"] = manifest_profile
                body = preflight.canonical_json(manifest)

            member = tarfile.TarInfo(f"{effective_root}/{relative}")
            member.size = len(body)
            member.mode = original.mode
            member.mtime = original.mtime
            member.uid = 0
            member.gid = 0
            member.uname = "root"
            member.gname = "root"
            member.type = tarfile.REGTYPE
            entries.append((member, body))

    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for member, body in entries:
            archive.addfile(member, io.BytesIO(body))

    compressed = io.BytesIO()
    with gzip.GzipFile(
        filename="",
        fileobj=compressed,
        mode="wb",
        compresslevel=9,
        mtime=fixtures.EPOCH,
    ) as stream:
        stream.write(raw.getvalue())
    target.write_bytes(compressed.getvalue())
    return hashlib.sha256(target.read_bytes()).hexdigest()


def _probe_args(artifact: Path, digest: str):
    args = fixtures.args_for(artifact, digest, profile="ib-paper")
    args.probe_broker = True
    return args


class PreflightReleaseIdentityTests(unittest.TestCase):
    def test_canonical_archive_root_matches_manifest_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            artifact, digest = _build_valid_package(work)
            manifest, actual, _ = preflight.inspect_archive(
                artifact,
                digest,
                preflight._load_policy(fixtures.POLICY),
                "ib-paper",
            )
            self.assertEqual(actual, digest)
            self.assertEqual(manifest["version"], fixtures.VERSION)

    def test_resealed_root_mismatches_fail_before_probe(self) -> None:
        wrong_roots = {
            "wrong-version": "heptatrader-0.1.0-beta.2-ib-paper",
            "wrong-profile": f"heptatrader-{fixtures.VERSION}-core",
            "missing-profile-suffix": f"heptatrader-{fixtures.VERSION}",
            "extra-suffix": f"heptatrader-{fixtures.VERSION}-ib-paper-extra",
        }
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            source, _ = _build_valid_package(work)
            for label, root_name in wrong_roots.items():
                with self.subTest(label=label):
                    artifact = work / f"{label}.tar.gz"
                    digest = _rewrite_archive(
                        source,
                        artifact,
                        root_name=root_name,
                    )
                    with mock.patch.object(
                        preflight.socket, "create_connection"
                    ) as connect:
                        receipt = preflight.run_preflight(
                            _probe_args(artifact, digest)
                        )
                    self.assertEqual(receipt["result"], "FAIL")
                    self.assertIn(
                        "archive root identity does not match",
                        fixtures.check(receipt, "artifact.integrity")["detail"],
                    )
                    connect.assert_not_called()

    def test_manifest_version_uses_builder_label_grammar(self) -> None:
        invalid_versions = {
            "overlong": "a" * 65,
            "leading-punctuation": ".release",
            "slash": "release/beta",
            "control": "release\n",
            "non-ascii": "release-β",
        }
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            source, _ = _build_valid_package(work)
            for label, version in invalid_versions.items():
                with self.subTest(label=label):
                    artifact = work / f"version-{label}.tar.gz"
                    digest = _rewrite_archive(
                        source,
                        artifact,
                        manifest_version=version,
                    )
                    with mock.patch.object(
                        preflight.socket, "create_connection"
                    ) as connect:
                        receipt = preflight.run_preflight(
                            _probe_args(artifact, digest)
                        )
                    self.assertEqual(receipt["result"], "FAIL")
                    self.assertIn(
                        "bounded canonical label",
                        fixtures.check(receipt, "artifact.integrity")["detail"],
                    )
                    connect.assert_not_called()

    def test_identity_regressions_are_discoverable(self) -> None:
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(
            PreflightReleaseIdentityTests
        )
        identifiers = {test.id().rsplit(".", 1)[-1] for test in suite}
        self.assertTrue(
            {
                "test_canonical_archive_root_matches_manifest_identity",
                "test_resealed_root_mismatches_fail_before_probe",
                "test_manifest_version_uses_builder_label_grammar",
            }.issubset(identifiers)
        )


if __name__ == "__main__":
    unittest.main()
