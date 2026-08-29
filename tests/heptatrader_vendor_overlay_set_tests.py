#!/usr/bin/env python3

from __future__ import annotations

import copy
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


REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY / "scripts"))
import build_heptatrader_vendor_overlay_set as builder  # noqa: E402
import verify_heptatrader_vendor_overlay_set as verifier  # noqa: E402


MANIFEST_PATHS = tuple(
    spec["manifest_path"] for spec in builder.OVERLAY_SPECS)


def private_write(path: Path, payload: bytes) -> None:
    path.write_bytes(payload)
    path.chmod(0o600)


def add_tar_bytes(
        archive: tarfile.TarFile, name: str, payload: bytes,
        mode: int = 0o644) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(payload)
    info.mode = mode
    info.uid = 0
    info.gid = 0
    info.uname = "root"
    info.gname = "root"
    info.mtime = 0
    archive.addfile(info, io.BytesIO(payload))


class StrictSourceFixture:
    VERSION = "0.1.0-beta.1-test"
    GIT_HEAD = "a" * 40

    def __init__(
            self, root: Path,
            manifest_payloads: dict[str, bytes] | None = None,
            record_overrides: dict[str, dict[str, object]] | None = None,
    ) -> None:
        self.root = root
        self.bundle = root / "strict-source.tar"
        self.manifest_path = root / "strict-source-manifest.json"
        payloads = {
            path: (REPOSITORY / path).read_bytes()
            for path in MANIFEST_PATHS
        }
        if manifest_payloads:
            payloads.update(manifest_payloads)
        records = []
        overrides = record_overrides or {}
        for path in sorted(payloads):
            record = {
                "mode": "0644",
                "path": path,
                "sha256": builder.sha256(payloads[path]),
                "size": len(payloads[path]),
            }
            record.update(overrides.get(path, {}))
            records.append(record)
        records_digest = builder.sha256(json.dumps(
            records, ensure_ascii=True, separators=(",", ":"),
            sort_keys=True).encode())
        self.manifest = {
            "schema": "hepta.clean-source-bundle.v2",
            "bundle_class": "strict-source-only",
            "version": self.VERSION,
            "git_head": self.GIT_HEAD,
            "root": f"heptatrader-{self.VERSION}",
            "file_count": len(records),
            "files_sha256": records_digest,
            "security_manifest_sha256": "sha256:" + "b" * 64,
            "security_manifest_file_count": 1,
            "prebuilt_payload_included": False,
            "nonredistributable_vendor_payload_included": False,
            "paper_authorized": False,
            "live_authorized": False,
            "files": records,
        }
        self.manifest_bytes = (
            json.dumps(self.manifest, indent=2, sort_keys=True) + "\n"
        ).encode()
        with tarfile.open(self.bundle, "w", format=tarfile.GNU_FORMAT) as archive:
            prefix = self.manifest["root"]
            add_tar_bytes(
                archive,
                f"{prefix}/.hepta/source-bundle-manifest.json",
                self.manifest_bytes)
            for path in sorted(payloads):
                add_tar_bytes(archive, f"{prefix}/{path}", payloads[path])
        self.bundle.chmod(0o600)
        private_write(self.manifest_path, self.manifest_bytes)

    def report(self) -> dict[str, object]:
        return {
            "bundle_sha256": builder.sha256(self.bundle.read_bytes()),
            "manifest_sha256": builder.sha256(
                self.manifest_path.read_bytes()),
            "files_sha256": self.manifest["files_sha256"],
            "git_head": self.manifest["git_head"],
            "paper_authorized": False,
            "live_authorized": False,
        }


class VendorOverlaySetTests(unittest.TestCase):
    def _build(
            self, fixture: StrictSourceFixture,
    ) -> dict[str, object]:
        with mock.patch.object(
                builder.source_verifier, "verify_bundle",
                return_value=fixture.report()):
            return builder.build_vendor_overlay_set(
                fixture.bundle, fixture.manifest_path)

    def _verify(
            self, fixture: StrictSourceFixture, overlay_path: Path,
    ) -> dict[str, object]:
        with mock.patch.object(
                verifier.source_verifier, "verify_bundle",
                return_value=fixture.report()):
            return verifier.verify_vendor_overlay_set(
                fixture.bundle, fixture.manifest_path, overlay_path)

    def test_builder_is_deterministic_metadata_only_and_exactly_bound(
            self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-vendor-overlay-set-") as temporary:
            fixture = StrictSourceFixture(Path(temporary))
            first = self._build(fixture)
            second = self._build(fixture)
            self.assertEqual(first, second)
            self.assertEqual(first["schema"], builder.SCHEMA)
            self.assertEqual(
                first["artifact_class"], builder.ARTIFACT_CLASS)
            self.assertEqual(first["release_version"], fixture.VERSION)
            self.assertEqual(first["overlay_count"], 3)
            self.assertEqual(
                [record["overlay_id"] for record in first["overlays"]],
                [
                    "ctp-6.5.1-tools",
                    "ctp-6.7.7",
                    "prebuilt-dependencies",
                ])
            self.assertEqual(
                [
                    (
                        record["declared_asset_count"],
                        record["declared_payload_count"],
                    )
                    for record in first["overlays"]
                ],
                [(4, 4), (23, 23), (8, 8)])
            self.assertEqual(
                [record["identity"] for record in first["overlays"]],
                [
                    {"vendor": "CTP", "version": "6.5.1"},
                    {"vendor": "CTP", "version": "6.7.7"},
                    {"vendor": "mixed-legacy", "version": "per-asset"},
                ])
            self.assertEqual(
                [record["capability_status"]
                 for record in first["overlays"]],
                [
                    "archived-legacy-runtime",
                    "disabled-experimental",
                    "archived-legacy-prebuilt-inventory",
                ])
            self.assertEqual(
                set(first["source_ref"]),
                {
                    "bundle_sha256", "manifest_sha256", "files_sha256",
                    "security_manifest_sha256", "git_head",
                })
            self.assertEqual(
                first["source_ref"]["bundle_sha256"],
                builder.sha256_ref(fixture.bundle.read_bytes()))
            self.assertEqual(
                first["source_ref"]["manifest_sha256"],
                builder.sha256_ref(fixture.manifest_bytes))
            self.assertEqual(
                first["source_ref"]["files_sha256"],
                "sha256:" + fixture.manifest["files_sha256"])
            for boundary in (
                    first, *first["overlays"]):
                self.assertIs(boundary["payload_included"], False)
                self.assertIs(
                    boundary["distribution_authorized"], False)
                self.assertEqual(
                    boundary["required_by_runtime_package_ids"], [])
            self.assertIs(first["paper_authorized"], False)
            self.assertIs(first["live_authorized"], False)
            serialized = builder.canonical_json(first)
            self.assertNotIn(b"Tools/thostmduserapi_se.dll", serialized)
            self.assertNotIn(b"Interface/IBApi/bin/CSharpAPI.dll", serialized)

    def test_round_trip_verifier_and_private_atomic_output(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-vendor-overlay-roundtrip-") as temporary:
            root = Path(temporary)
            fixture = StrictSourceFixture(root)
            document = self._build(fixture)
            output = root / "vendor-overlay-set.json"
            published, digest = builder.publish_vendor_overlay_set(
                output, document)
            self.assertEqual(published, output)
            self.assertEqual(
                digest, builder.sha256(builder.canonical_json(document)))
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
            report = self._verify(fixture, output)
            self.assertEqual(report["overlay_count"], 3)
            self.assertEqual(report["release_version"], fixture.VERSION)
            self.assertEqual(
                report["overlay_set_sha256"],
                verifier.sha256(output.read_bytes()))
            self.assertIs(report["payload_included"], False)
            self.assertIs(report["distribution_authorized"], False)
            self.assertIs(report["paper_authorized"], False)
            self.assertIs(report["live_authorized"], False)

    def test_publication_rejects_writable_output_parent(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-vendor-overlay-output-") as temporary:
            root = Path(temporary)
            fixture = StrictSourceFixture(root)
            document = self._build(fixture)
            public = root / "public"
            public.mkdir(mode=0o777)
            public.chmod(0o777)
            with self.assertRaisesRegex(
                    builder.VendorOverlaySetError,
                    "output parent must be caller-owned"):
                builder.publish_vendor_overlay_set(
                    public / "vendor-overlay-set.json", document)

    def test_duplicate_and_nonfinite_overlay_json_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-vendor-overlay-json-") as temporary:
            root = Path(temporary)
            fixture = StrictSourceFixture(root)
            duplicate = root / "duplicate.json"
            private_write(
                duplicate,
                b'{"schema":"hepta.vendor-overlay-set.v1","schema":"x"}\n')
            with self.assertRaisesRegex(
                    verifier.VendorOverlaySetVerificationError,
                    "duplicate JSON member"):
                self._verify(fixture, duplicate)
            nonfinite = root / "nonfinite.json"
            private_write(nonfinite, b'{"value":NaN}\n')
            with self.assertRaisesRegex(
                    verifier.VendorOverlaySetVerificationError,
                    "non-finite JSON number"):
                self._verify(fixture, nonfinite)

    def test_duplicate_vendor_manifest_json_is_rejected(self) -> None:
        current = (
            REPOSITORY /
            "third_party/ctp/6.7.7/manifest-v1.json").read_bytes()
        self.assertTrue(current.endswith(b"}\n"))
        injected = (
            current[:-2] +
            b',\n  "schema": "hepta.vendor-asset-manifest.v1"\n}\n')
        with tempfile.TemporaryDirectory(
                prefix="hepta-vendor-overlay-duplicate-source-") as temporary:
            fixture = StrictSourceFixture(
                Path(temporary),
                manifest_payloads={
                    "third_party/ctp/6.7.7/manifest-v1.json": injected,
                })
            with self.assertRaisesRegex(
                    builder.VendorOverlaySetError,
                    "duplicate JSON member"):
                self._build(fixture)

    def test_source_verification_lineage_mismatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-vendor-overlay-lineage-") as temporary:
            fixture = StrictSourceFixture(Path(temporary))
            report = fixture.report()
            report["bundle_sha256"] = "f" * 64
            with mock.patch.object(
                    builder.source_verifier, "verify_bundle",
                    return_value=report):
                with self.assertRaisesRegex(
                        builder.VendorOverlaySetError,
                        "crossed a source lineage"):
                    builder.build_vendor_overlay_set(
                        fixture.bundle, fixture.manifest_path)

    def test_source_manifest_to_tar_drift_is_rejected(self) -> None:
        current_path = "third_party/ctp/6.7.7/manifest-v1.json"
        with tempfile.TemporaryDirectory(
                prefix="hepta-vendor-overlay-tar-drift-") as temporary:
            fixture = StrictSourceFixture(
                Path(temporary),
                record_overrides={current_path: {"sha256": "c" * 64}})
            with self.assertRaisesRegex(
                    builder.VendorOverlaySetError,
                    "crossed source closure"):
                self._build(fixture)

    def test_verifier_rejects_source_ref_order_and_boundary_drift(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-vendor-overlay-drift-") as temporary:
            root = Path(temporary)
            fixture = StrictSourceFixture(root)
            document = self._build(fixture)
            cases = []

            source_drift = copy.deepcopy(document)
            source_drift["source_ref"]["bundle_sha256"] = "sha256:" + "d" * 64
            cases.append(("source", source_drift))

            order_drift = copy.deepcopy(document)
            order_drift["overlays"].reverse()
            cases.append(("order", order_drift))

            boundary_drift = copy.deepcopy(document)
            boundary_drift["distribution_authorized"] = True
            cases.append(("boundary", boundary_drift))

            count_drift = copy.deepcopy(document)
            count_drift["overlays"][0]["declared_payload_count"] = 5
            cases.append(("count", count_drift))

            for label, changed in cases:
                with self.subTest(label=label):
                    path = root / f"{label}.json"
                    private_write(path, verifier.canonical_json(changed))
                    with self.assertRaisesRegex(
                            verifier.VendorOverlaySetVerificationError,
                            "source, count, or boundary drift"):
                        self._verify(fixture, path)

    def test_verifier_rejects_noncanonical_encoding(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-vendor-overlay-canonical-") as temporary:
            root = Path(temporary)
            fixture = StrictSourceFixture(root)
            document = self._build(fixture)
            output = root / "noncanonical.json"
            private_write(
                output,
                (json.dumps(document, separators=(",", ":")) + "\n").encode())
            with self.assertRaisesRegex(
                    verifier.VendorOverlaySetVerificationError,
                    "not canonical deterministic JSON"):
                self._verify(fixture, output)

    def test_stable_read_rejects_symlinked_overlay_set(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-vendor-overlay-symlink-") as temporary:
            root = Path(temporary)
            fixture = StrictSourceFixture(root)
            document = self._build(fixture)
            real = root / "real.json"
            alias = root / "alias.json"
            private_write(real, verifier.canonical_json(document))
            alias.symlink_to(real.name)
            with self.assertRaises(
                    verifier.VendorOverlaySetVerificationError):
                self._verify(fixture, alias)


if __name__ == "__main__":
    unittest.main()
