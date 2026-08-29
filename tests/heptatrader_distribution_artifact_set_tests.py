#!/usr/bin/env python3

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest import mock


REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY / "scripts"))

import build_heptatrader_distribution_artifact_set as builder  # noqa: E402
import verify_heptatrader_distribution_artifact_set as verifier  # noqa: E402


def sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


class DistributionFixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.release = "0.1.0-test.1"
        self.git_head = "1" * 40
        self.source_root = f"heptatrader-{self.release}"
        self.source_tar = root / "strict-source.tar"
        self.source_manifest = root / "strict-source.manifest.json"
        self.vendor_set = root / "vendor-overlay-set.json"
        self.runtime_tar = root / "passive-runtime.tar"
        self.runtime_manifest = root / "passive-runtime.manifest.json"
        self.artifact_set = root / "distribution-artifact-set.json"
        self.vendor_payloads = self._vendor_payload_documents()
        self._build_source()
        self._build_vendor()
        self._build_runtime()

    @staticmethod
    def _json(value: object) -> bytes:
        return (
            json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) +
            "\n"
        ).encode("ascii")

    @staticmethod
    def _write(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        path.chmod(0o600)

    @staticmethod
    def _add(
            archive: tarfile.TarFile, name: str, data: bytes,
            mode: int = 0o644) -> None:
        member = tarfile.TarInfo(name)
        member.size = len(data)
        member.mode = mode
        member.uid = member.gid = 0
        member.uname = member.gname = "root"
        member.mtime = 0
        member.type = tarfile.REGTYPE
        member.linkname = ""
        member.devmajor = member.devminor = 0
        member.pax_headers = {}
        archive.addfile(member, io.BytesIO(data))

    def _vendor_payload_documents(self) -> dict[str, dict[str, object]]:
        return {
            "third_party/ctp/6.5.1-tools/manifest-v1.json": {
                "schema": "hepta.ctp-tools-overlay-manifest.v1",
                "assets": [{"path": "payload.dll"}],
                "payload_count": 1,
                "distribution_authorized": False,
            },
            "third_party/ctp/6.7.7/manifest-v1.json": {
                "schema": "hepta.vendor-asset-manifest.v1",
                "canonical_headers": [{"path": "include/api.h"}],
                "platform_assets": [{"path": "payload.so"}],
                "distribution_authorized": False,
                "paper_authorized": False,
                "live_authorized": False,
            },
            "third_party/prebuilt-dependencies/manifest-v1.json": {
                "schema": "hepta.prebuilt-asset-manifest.v1",
                "assets": [{
                    "path": "payload.lib",
                    "distribution_authorized": False,
                }],
                "payload_count": 1,
                "payload_distribution_authorized": False,
            },
        }

    def _build_source(self) -> None:
        encoded = {
            path: self._json(document)
            for path, document in self.vendor_payloads.items()
        }
        records = [
            {
                "path": path,
                "mode": "0644",
                "size": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
            for path, payload in sorted(encoded.items())
        ]
        files_sha = hashlib.sha256(
            verifier.canonical_json(records)).hexdigest()
        self.source_document = {
            "schema": verifier.SOURCE_SCHEMA,
            "bundle_class": "strict-source-only",
            "version": self.release,
            "git_head": self.git_head,
            "root": self.source_root,
            "files_sha256": files_sha,
            "security_manifest_sha256": "sha256:" + "2" * 64,
            "nonredistributable_vendor_payload_included": False,
            "prebuilt_payload_included": False,
            "paper_authorized": False,
            "live_authorized": False,
            "files": records,
        }
        self._write(
            self.source_manifest, self._json(self.source_document))
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w:") as archive:
            for path, payload in sorted(encoded.items()):
                self._add(
                    archive, f"{self.source_root}/{path}", payload)
        self._write(self.source_tar, stream.getvalue())

    def source_ref(self) -> dict[str, object]:
        manifest_bytes = self.source_manifest.read_bytes()
        return {
            "schema": verifier.SOURCE_SCHEMA,
            "bundle_sha256": sha(self.source_tar.read_bytes()),
            "manifest_sha256": sha(manifest_bytes),
            "files_sha256":
                "sha256:" + self.source_document["files_sha256"],
            "security_manifest_sha256":
                self.source_document["security_manifest_sha256"],
            "git_head": self.git_head,
            "root": self.source_root,
        }

    def _build_vendor(self) -> None:
        overlays = []
        for overlay_id in sorted(verifier.EXPECTED_OVERLAYS):
            target, schema = verifier.EXPECTED_OVERLAYS[overlay_id]
            manifest_path = f"{target}/manifest-v1.json"
            payload = self._json(self.vendor_payloads[manifest_path])
            document = self.vendor_payloads[manifest_path]
            if isinstance(document.get("assets"), list):
                count = len(document["assets"])
                payload_count = document.get("payload_count", count)
            else:
                count = (
                    len(document["canonical_headers"]) +
                    len(document["platform_assets"]))
                payload_count = count
            overlays.append({
                "overlay_id": overlay_id,
                "target": target,
                "manifest_path": manifest_path,
                "manifest_schema": schema,
                "manifest_sha256": sha(payload),
                "identity": {
                    "vendor": (
                        "CTP" if overlay_id.startswith("ctp-")
                        else "mixed-reviewed-prebuilt"),
                    "version": (
                        overlay_id.removeprefix("ctp-")
                        if overlay_id.startswith("ctp-")
                        else "inventory-v1"),
                },
                "capability_status": "disabled-fixture",
                "declared_asset_count": count,
                "declared_payload_count": payload_count,
                "payload_included": False,
                "distribution_authorized": False,
                "required_by_runtime_package_ids": [],
            })
        source_ref = self.source_ref()
        self.vendor_document = {
            "schema": verifier.VENDOR_SCHEMA,
            "release_version": self.release,
            "artifact_class": "metadata-only-vendor-overlay-set",
            "source_ref": {
                key: source_ref[key]
                for key in verifier.VENDOR_SOURCE_REF_FIELDS
            },
            "overlay_count": len(overlays),
            "overlays": overlays,
            "payload_included": False,
            "distribution_authorized": False,
            "required_by_runtime_package_ids": [],
            "paper_authorized": False,
            "live_authorized": False,
        }
        self._write(self.vendor_set, self._json(self.vendor_document))

    @staticmethod
    def _elf() -> bytes:
        return Path("/bin/true").read_bytes()

    def _runtime_payloads(self) -> dict[
            str, tuple[int, bytes, dict[str, object]]]:
        runtime = verifier.runtime_verifier
        payloads: dict[str, tuple[int, bytes, dict[str, object]]] = {}
        for path, mode in runtime.PRODUCT_FILES.items():
            if path == runtime.PAPER_IDENTITY_SOURCE_PATH:
                data = runtime.PAPER_IDENTITY_SOURCE_BYTES
            elif path in runtime.ELF_FILES:
                data = self._elf()
            elif path in runtime.PYTHON_MODULE_FILES:
                data = b'"""passive fixture runtime module."""\n'
            elif path in runtime.PYTHON_FILES:
                data = (
                    b"#!/usr/bin/python3\n"
                    b'"""passive fixture runtime."""\n')
            elif path.endswith((".service", ".socket", ".timer", ".target")):
                data = (
                    REPOSITORY / "systemd" / Path(path).name
                ).read_bytes()
            elif path.endswith(
                    "hepta-tool-gateway.service.d/"
                    "10-hepta-broker-egress-policy.conf"):
                data = (
                    REPOSITORY / "systemd/hepta-tool-gateway.service.d/"
                    "10-hepta-broker-egress-policy.conf").read_bytes()
            elif path.endswith(
                    "hepta-tool-gateway@.service.d/"
                    "10-hepta-broker-egress-policy.conf"):
                data = (
                    REPOSITORY / "systemd/hepta-tool-gateway@.service.d/"
                    "10-hepta-broker-egress-policy.conf").read_bytes()
            else:
                data = f"fixture:{path}\n".encode("utf-8")
            payloads[path] = (
                mode, data, runtime.payload_record(path, data))
        return payloads

    def _runtime_vendor_ref(self) -> dict[str, object]:
        return {
            "schema": verifier.VENDOR_SCHEMA,
            "descriptor_sha256": sha(self.vendor_set.read_bytes()),
            "release_version": self.release,
            "overlay_count": self.vendor_document["overlay_count"],
            "required_overlay_ids": [],
        }

    def _build_runtime(self) -> None:
        payloads = self._runtime_payloads()
        records = [
            {
                "path": path,
                "mode": format(mode, "04o"),
                "size": len(data),
                "sha256": sha(data),
                "payload": deepcopy(payload),
            }
            for path, (mode, data, payload) in sorted(payloads.items())
        ]
        elf_payload = next(
            payload for _, _, payload in payloads.values()
            if payload["kind"] == "elf")
        target = {
            "os": "linux",
            "elf_class": elf_payload["class"],
            "endian": elf_payload["endian"],
            "machine": elf_payload["machine"],
        }
        self.runtime_document = {
            "schema": verifier.RUNTIME_SCHEMA,
            "package_class": "passive-agent-simulator-runtime",
            "release_version": self.release,
            "root":
                f"heptatrader-runtime-{self.release}-linux-"
                f"{target['machine']}",
            "source_ref": self.source_ref(),
            "vendor_ref": self._runtime_vendor_ref(),
            "target": target,
            "boundary": {
                "components": [
                    "hepta-agent-os-runtime",
                    "hepta-execution-runtime",
                ],
                "build_type": "Release",
                "ibapi_enabled": False,
                "legacy_0dte_bridge_enabled": False,
                "legacy_monolith_enabled": False,
                "legacy_simulator_enabled": False,
                "passive_provisioning": True,
                "paper_authorized": False,
                "live_authorized": False,
                "sdk_included": False,
                "vendor_payload_included": False,
                "prebuilt_payload_included": False,
                "host_state_paths_included": False,
            },
            "file_count": len(records),
            "files_sha256": sha(verifier.canonical_json(records)),
            "files": records,
        }
        self.write_runtime(self.runtime_document)

    def _build_runtime_tar(
            self, *, extra: tuple[str, bytes] | None = None,
            omit: str | None = None) -> None:
        payloads = self._runtime_payloads()
        manifest_bytes = self.runtime_manifest.read_bytes()
        root = self.runtime_document["root"]
        stream = io.BytesIO()
        with tarfile.open(
                fileobj=stream, mode="w:",
                format=tarfile.USTAR_FORMAT) as archive:
            for record in self.runtime_document["files"]:
                path = record["path"]
                if path == omit:
                    continue
                if path in payloads:
                    mode, data, _ = payloads[path]
                else:
                    mode = int(record["mode"], 8)
                    data = b"x" * record["size"]
                self._add(archive, f"{root}/{path}", data, mode)
            self._add(
                archive,
                f"{root}/.hepta/runtime-package-manifest.json",
                manifest_bytes)
            if extra is not None:
                self._add(
                    archive, f"{root}/{extra[0]}", extra[1])
        self._write(self.runtime_tar, stream.getvalue())

    def write_runtime(
            self, value: dict[str, object], *,
            rebuild_tar: bool = True) -> None:
        self.runtime_document = deepcopy(value)
        self._write(
            self.runtime_manifest,
            verifier.runtime_verifier.canonical_json(value) + b"\n")
        if rebuild_tar:
            self._build_runtime_tar()

    def write_set(self, value: object, *, canonical: bool = True) -> None:
        data = (
            verifier.canonical_json(value) + b"\n"
            if canonical else self._json(value))
        self._write(self.artifact_set, data)

    def fake_source_report(
            self, bundle: Path, manifest: Path) -> dict[str, object]:
        bundle_bytes = bundle.read_bytes()
        manifest_bytes = manifest.read_bytes()
        document = json.loads(manifest_bytes)
        return {
            "version": document["version"],
            "git_head": document["git_head"],
            "files_sha256": document["files_sha256"],
            "bundle_sha256": hashlib.sha256(bundle_bytes).hexdigest(),
            "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
            "paper_authorized": False,
            "live_authorized": False,
            "nonredistributable_vendor_payload_included": False,
            "prebuilt_payload_included": False,
        }

    @contextmanager
    def source_verification(self):
        def fake_vendor_report(
                bundle: Path, manifest: Path,
                overlay_set: Path) -> dict[str, object]:
            document = json.loads(overlay_set.read_bytes())
            return {
                "schema": verifier.VENDOR_SCHEMA,
                "release_version": document["release_version"],
                "artifact_class": document["artifact_class"],
                "overlay_count": document["overlay_count"],
                "overlay_set_sha256":
                    hashlib.sha256(overlay_set.read_bytes()).hexdigest(),
                "source_ref": document["source_ref"],
                "payload_included": document["payload_included"],
                "distribution_authorized":
                    document["distribution_authorized"],
                "paper_authorized": document["paper_authorized"],
                "live_authorized": document["live_authorized"],
            }

        with mock.patch.object(
                verifier.source_verifier, "verify_bundle",
                side_effect=self.fake_source_report), mock.patch.object(
                    verifier.vendor_verifier, "verify_vendor_overlay_set",
                    side_effect=fake_vendor_report):
            yield

    def build(self, output: Path | None = None) -> dict[str, object]:
        output = output or self.artifact_set
        with self.source_verification():
            return builder.build_and_publish(
                self.source_tar, self.source_manifest, self.vendor_set,
                self.runtime_tar, self.runtime_manifest, output)

    def verify(self) -> dict[str, object]:
        with self.source_verification():
            return verifier.verify(
                self.artifact_set, self.source_tar, self.source_manifest,
                self.vendor_set, self.runtime_tar, self.runtime_manifest)


class DistributionArtifactSetTests(unittest.TestCase):
    def fixture(self, root: Path) -> DistributionFixture:
        return DistributionFixture(root)

    def test_build_is_deterministic_and_verifies(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-dist-") as temporary:
            fixture = self.fixture(Path(temporary))
            document = fixture.build()
            first = fixture.artifact_set.read_bytes()
            second = fixture.root / "distribution-artifact-set.second.json"
            fixture.build(second)
            self.assertEqual(first, second.read_bytes())
            report = fixture.verify()
            self.assertEqual(report["artifact_count"], 5)
            self.assertEqual(document["scope"], verifier.SCOPE)
            self.assertNotIn("generated_at", document)

    def test_artifact_records_are_exact_and_canonically_ordered(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-dist-") as temporary:
            fixture = self.fixture(Path(temporary))
            document = fixture.build()
            self.assertEqual(
                [item["role"] for item in document["artifacts"]],
                list(verifier.ROLE_ORDER))
            for item in document["artifacts"]:
                self.assertEqual(set(item), verifier.ARTIFACT_FIELDS)
                self.assertRegex(item["sha256"], r"^sha256:[0-9a-f]{64}$")

    def test_boundary_is_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-dist-") as temporary:
            fixture = self.fixture(Path(temporary))
            document = fixture.build()
            self.assertEqual(document["boundary"], verifier.SET_BOUNDARY)
            report = fixture.verify()
            for key in (
                    "vendor_payload_included", "prebuilt_payload_included",
                    "broker_payload_included", "paper_authorized",
                    "live_authorized"):
                self.assertIs(report[key], False)

    def test_duplicate_json_member_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-dist-") as temporary:
            fixture = self.fixture(Path(temporary))
            fixture.build()
            fixture._write(
                fixture.artifact_set,
                b'{"schema":"a","schema":"b"}\n')
            with self.assertRaisesRegex(
                    verifier.DistributionArtifactSetError,
                    "duplicate JSON key"):
                fixture.verify()

    def test_nonfinite_json_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-dist-") as temporary:
            fixture = self.fixture(Path(temporary))
            fixture._write(fixture.artifact_set, b'{"value":NaN}\n')
            with self.assertRaisesRegex(
                    verifier.DistributionArtifactSetError, "non-finite"):
                fixture.verify()

    def test_missing_extra_and_reordered_roles_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-dist-") as temporary:
            fixture = self.fixture(Path(temporary))
            document = fixture.build()
            cases = []
            missing = deepcopy(document)
            missing["artifacts"].pop()
            cases.append(missing)
            extra = deepcopy(document)
            extra["artifacts"][0]["extra"] = False
            cases.append(extra)
            reordered = deepcopy(document)
            reordered["artifacts"][0], reordered["artifacts"][1] = (
                reordered["artifacts"][1], reordered["artifacts"][0])
            cases.append(reordered)
            for case in cases:
                fixture.write_set(case)
                with self.assertRaises(
                        verifier.DistributionArtifactSetError):
                    fixture.verify()

    def test_noncanonical_artifact_set_json_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-dist-") as temporary:
            fixture = self.fixture(Path(temporary))
            document = fixture.build()
            fixture.write_set(document, canonical=False)
            with self.assertRaisesRegex(
                    verifier.DistributionArtifactSetError, "canonical JSON"):
                fixture.verify()

    def test_cross_wired_roles_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-dist-") as temporary:
            fixture = self.fixture(Path(temporary))
            with fixture.source_verification(), self.assertRaises(
                    verifier.DistributionArtifactSetError):
                verifier.build_artifact_set(
                    fixture.source_tar, fixture.vendor_set,
                    fixture.source_manifest, fixture.runtime_tar,
                    fixture.runtime_manifest)
            with fixture.source_verification(), self.assertRaises(
                    verifier.DistributionArtifactSetError):
                verifier.build_artifact_set(
                    fixture.runtime_tar, fixture.source_manifest,
                    fixture.vendor_set, fixture.source_tar,
                    fixture.runtime_manifest)

    def test_symlink_and_duplicate_filename_inputs_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-dist-") as temporary:
            fixture = self.fixture(Path(temporary))
            real = fixture.root / "real-runtime.json"
            real.write_bytes(fixture.runtime_manifest.read_bytes())
            real.chmod(0o600)
            fixture.runtime_manifest.unlink()
            fixture.runtime_manifest.symlink_to(real)
            with fixture.source_verification(), self.assertRaises(
                    verifier.DistributionArtifactSetError):
                verifier.build_artifact_set(
                    fixture.source_tar, fixture.source_manifest,
                    fixture.vendor_set, fixture.runtime_tar,
                    fixture.runtime_manifest)

        with tempfile.TemporaryDirectory(prefix="hepta-dist-") as temporary:
            fixture = self.fixture(Path(temporary))
            duplicate = fixture.root / "nested" / fixture.source_manifest.name
            fixture._write(duplicate, fixture.runtime_manifest.read_bytes())
            with fixture.source_verification(), self.assertRaisesRegex(
                    verifier.DistributionArtifactSetError, "filenames"):
                verifier.build_artifact_set(
                    fixture.source_tar, fixture.source_manifest,
                    fixture.vendor_set, fixture.runtime_tar, duplicate)

    def test_artifact_drift_after_build_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-dist-") as temporary:
            fixture = self.fixture(Path(temporary))
            fixture.build()
            fixture._write(
                fixture.runtime_tar,
                fixture.runtime_tar.read_bytes() + b"drift")
            with self.assertRaises(
                    verifier.DistributionArtifactSetError):
                fixture.verify()

    def test_runtime_source_lineage_drift_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-dist-") as temporary:
            fixture = self.fixture(Path(temporary))
            document = deepcopy(fixture.runtime_document)
            document["source_ref"]["manifest_sha256"] = "sha256:" + "0" * 64
            fixture.write_runtime(document)
            with fixture.source_verification(), self.assertRaisesRegex(
                    verifier.DistributionArtifactSetError, "lineage"):
                verifier.build_artifact_set(
                    fixture.source_tar, fixture.source_manifest,
                    fixture.vendor_set, fixture.runtime_tar,
                    fixture.runtime_manifest)

    def test_runtime_vendor_lineage_drift_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-dist-") as temporary:
            fixture = self.fixture(Path(temporary))
            document = deepcopy(fixture.runtime_document)
            document["vendor_ref"]["descriptor_sha256"] = (
                "sha256:" + "0" * 64)
            fixture.write_runtime(document)
            with fixture.source_verification(), self.assertRaisesRegex(
                    verifier.DistributionArtifactSetError, "lineage"):
                verifier.build_artifact_set(
                    fixture.source_tar, fixture.source_manifest,
                    fixture.vendor_set, fixture.runtime_tar,
                    fixture.runtime_manifest)

    def test_release_and_target_drift_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-dist-") as temporary:
            fixture = self.fixture(Path(temporary))
            document = deepcopy(fixture.runtime_document)
            document["release_version"] = "wrong-release"
            fixture.write_runtime(document)
            with fixture.source_verification(), self.assertRaisesRegex(
                    verifier.DistributionArtifactSetError, "lineage"):
                verifier.build_artifact_set(
                    fixture.source_tar, fixture.source_manifest,
                    fixture.vendor_set, fixture.runtime_tar,
                    fixture.runtime_manifest)

        with tempfile.TemporaryDirectory(prefix="hepta-dist-") as temporary:
            fixture = self.fixture(Path(temporary))
            set_document = fixture.build()
            set_document["target"]["machine"] = "aarch64"
            fixture.write_set(set_document)
            with self.assertRaisesRegex(
                    verifier.DistributionArtifactSetError, "bind"):
                fixture.verify()

    def test_vendor_payload_or_authority_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-dist-") as temporary:
            fixture = self.fixture(Path(temporary))
            document = deepcopy(fixture.vendor_document)
            document["payload_included"] = True
            fixture._write(fixture.vendor_set, fixture._json(document))
            with fixture.source_verification(), self.assertRaisesRegex(
                    verifier.DistributionArtifactSetError, "boundary"):
                verifier.build_artifact_set(
                    fixture.source_tar, fixture.source_manifest,
                    fixture.vendor_set, fixture.runtime_tar,
                    fixture.runtime_manifest)

    def test_runtime_paper_or_broker_boundary_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-dist-") as temporary:
            fixture = self.fixture(Path(temporary))
            document = deepcopy(fixture.runtime_document)
            document["boundary"]["paper_authorized"] = True
            fixture.write_runtime(document)
            with fixture.source_verification(), self.assertRaisesRegex(
                    verifier.DistributionArtifactSetError, "boundary"):
                verifier.build_artifact_set(
                    fixture.source_tar, fixture.source_manifest,
                    fixture.vendor_set, fixture.runtime_tar,
                    fixture.runtime_manifest)

        with tempfile.TemporaryDirectory(prefix="hepta-dist-") as temporary:
            fixture = self.fixture(Path(temporary))
            document = deepcopy(fixture.runtime_document)
            document["files"][0]["path"] = "third_party/ibapi/sdk.so"
            document["files"] = sorted(
                document["files"], key=lambda item: item["path"])
            document["files_sha256"] = sha(
                verifier.canonical_json(document["files"]))
            fixture.write_runtime(document)
            with fixture.source_verification(), self.assertRaisesRegex(
                    verifier.DistributionArtifactSetError, "payload path"):
                verifier.build_artifact_set(
                    fixture.source_tar, fixture.source_manifest,
                    fixture.vendor_set, fixture.runtime_tar,
                    fixture.runtime_manifest)

    def test_runtime_tar_extra_and_missing_members_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-dist-") as temporary:
            fixture = self.fixture(Path(temporary))
            fixture._build_runtime_tar(extra=("unexpected.txt", b"x"))
            with fixture.source_verification(), self.assertRaisesRegex(
                    verifier.DistributionArtifactSetError, "closure"):
                verifier.build_artifact_set(
                    fixture.source_tar, fixture.source_manifest,
                    fixture.vendor_set, fixture.runtime_tar,
                    fixture.runtime_manifest)

        with tempfile.TemporaryDirectory(prefix="hepta-dist-") as temporary:
            fixture = self.fixture(Path(temporary))
            fixture._build_runtime_tar(omit="usr/bin/heptactl")
            with fixture.source_verification(), self.assertRaisesRegex(
                    verifier.DistributionArtifactSetError, "incomplete"):
                verifier.build_artifact_set(
                    fixture.source_tar, fixture.source_manifest,
                    fixture.vendor_set, fixture.runtime_tar,
                    fixture.runtime_manifest)

    def test_reviewed_python_module_descriptor_is_exact(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-dist-") as temporary:
            fixture = self.fixture(Path(temporary))
            document = deepcopy(fixture.runtime_document)
            helper = next(
                record for record in document["files"]
                if record["path"] ==
                "usr/libexec/hepta_ops/agent_os_source.py")
            self.assertEqual(helper["payload"], {"kind": "python-module"})
            helper["payload"]["shebang"] = "#!/usr/bin/python3"
            document["files_sha256"] = sha(
                verifier.canonical_json(document["files"]))
            fixture.write_runtime(document)
            with fixture.source_verification(), self.assertRaisesRegex(
                    verifier.DistributionArtifactSetError,
                    "Python module descriptor"):
                verifier.build_artifact_set(
                    fixture.source_tar, fixture.source_manifest,
                    fixture.vendor_set, fixture.runtime_tar,
                    fixture.runtime_manifest)

    def test_distribution_independently_rejects_arbitrary_runtime_path(
            self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-dist-") as temporary:
            fixture = self.fixture(Path(temporary))
            document = deepcopy(fixture.runtime_document)
            document["files"].append({
                "path": "usr/share/heptatrader/innocent-extra.txt",
                "mode": "0644",
                "size": 2,
                "sha256": sha(b"x\n"),
                "payload": {"kind": "data"},
            })
            document["files"] = sorted(
                document["files"], key=lambda item: item["path"])
            document["file_count"] = len(document["files"])
            document["files_sha256"] = sha(
                verifier.canonical_json(document["files"]))
            fixture.write_runtime(document)
            with fixture.source_verification(), self.assertRaisesRegex(
                    verifier.DistributionArtifactSetError,
                    r"exact \d+-file product surface|unapproved payload path"):
                verifier.build_artifact_set(
                    fixture.source_tar, fixture.source_manifest,
                    fixture.vendor_set, fixture.runtime_tar,
                    fixture.runtime_manifest)

    def test_runtime_manifest_must_be_canonical_for_distribution(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-dist-") as temporary:
            fixture = self.fixture(Path(temporary))
            fixture._write(
                fixture.runtime_manifest,
                fixture._json(fixture.runtime_document))
            fixture._build_runtime_tar()
            with fixture.source_verification(), self.assertRaisesRegex(
                    verifier.DistributionArtifactSetError,
                    "runtime manifest is not canonical"):
                verifier.build_artifact_set(
                    fixture.source_tar, fixture.source_manifest,
                    fixture.vendor_set, fixture.runtime_tar,
                    fixture.runtime_manifest)

    def test_standalone_runtime_verifier_is_called_and_bound(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-dist-") as temporary:
            fixture = self.fixture(Path(temporary))
            real_verify = verifier.runtime_verifier.verify_package
            with fixture.source_verification(), mock.patch.object(
                    verifier.runtime_verifier, "verify_package",
                    wraps=real_verify) as standalone:
                verifier.build_artifact_set(
                    fixture.source_tar, fixture.source_manifest,
                    fixture.vendor_set, fixture.runtime_tar,
                    fixture.runtime_manifest)
            standalone.assert_called_once_with(
                fixture.runtime_tar, fixture.runtime_manifest)

            def drifted_report(runtime_tar: Path, runtime_manifest: Path):
                report = real_verify(runtime_tar, runtime_manifest)
                report["package_sha256"] = "sha256:" + "0" * 64
                return report

            with fixture.source_verification(), mock.patch.object(
                    verifier.runtime_verifier, "verify_package",
                    side_effect=drifted_report), self.assertRaisesRegex(
                        verifier.DistributionArtifactSetError,
                        "does not bind captured inputs"):
                verifier.build_artifact_set(
                    fixture.source_tar, fixture.source_manifest,
                    fixture.vendor_set, fixture.runtime_tar,
                    fixture.runtime_manifest)

    def test_private_publication_rejects_writable_parent(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-dist-") as temporary:
            parent = Path(temporary) / "writable"
            parent.mkdir(mode=0o700)
            parent.chmod(0o777)
            with self.assertRaisesRegex(
                    builder.DistributionArtifactSetBuildError,
                    "not group/world-writable"):
                builder.publish_private_json(
                    parent / "distribution-artifact-set.json",
                    {"schema": verifier.SCHEMA})

    def test_private_publication_never_overwrites_destination_racer(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-dist-") as temporary:
            parent = Path(temporary) / "private"
            parent.mkdir(mode=0o700)
            output = parent / "distribution-artifact-set.json"
            real_link = builder.os.link
            raced = False

            def install_racer(source: object, destination: object, *args,
                              **kwargs):
                nonlocal raced
                if not raced and str(destination) == output.name:
                    raced = True
                    output.write_bytes(b"racer")
                    output.chmod(0o600)
                return real_link(source, destination, *args, **kwargs)

            with mock.patch.object(
                    builder.os, "link", side_effect=install_racer):
                with self.assertRaisesRegex(
                        builder.DistributionArtifactSetBuildError,
                        "already exists"):
                    builder.publish_private_json(
                        output, {"schema": verifier.SCHEMA})
            self.assertTrue(raced)
            self.assertEqual(output.read_bytes(), b"racer")
            self.assertEqual(
                list(parent.glob(
                    ".distribution-artifact-set.json.*.tmp")), [])

    def test_private_publication_rejects_parent_replacement_and_rolls_back(
            self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-dist-") as temporary:
            root = Path(temporary)
            parent = root / "private"
            moved = root / "moved-private"
            parent.mkdir(mode=0o700)
            output = parent / "distribution-artifact-set.json"
            real_link = builder.os.link
            replaced = False

            def replace_parent(source: object, destination: object, *args,
                               **kwargs):
                nonlocal replaced
                result = real_link(source, destination, *args, **kwargs)
                if not replaced and str(destination) == output.name:
                    replaced = True
                    parent.rename(moved)
                    parent.mkdir(mode=0o700)
                return result

            with mock.patch.object(
                    builder.os, "link", side_effect=replace_parent):
                with self.assertRaisesRegex(
                        builder.DistributionArtifactSetBuildError,
                        "ancestor identity changed"):
                    builder.publish_private_json(
                        output, {"schema": verifier.SCHEMA})
            self.assertTrue(replaced)
            self.assertFalse(output.exists())
            self.assertFalse((moved / output.name).exists())
            self.assertEqual(
                list(moved.glob(
                    ".distribution-artifact-set.json.*.tmp")), [])

    def test_failed_publication_rolls_back_only_its_exact_inode(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-dist-") as temporary:
            parent = Path(temporary) / "private"
            parent.mkdir(mode=0o700)
            output = parent / "distribution-artifact-set.json"
            real_link = builder.os.link
            replaced = False

            def replace_destination(source: object, destination: object, *args,
                                    **kwargs):
                nonlocal replaced
                result = real_link(source, destination, *args, **kwargs)
                if not replaced and str(destination) == output.name:
                    replaced = True
                    output.unlink()
                    output.write_bytes(b"racer")
                    output.chmod(0o600)
                return result

            with mock.patch.object(
                    builder.os, "link", side_effect=replace_destination):
                with self.assertRaisesRegex(
                        builder.DistributionArtifactSetBuildError,
                        "publication identity|no-overwrite link"):
                    builder.publish_private_json(
                        output, {"schema": verifier.SCHEMA})
            self.assertTrue(replaced)
            self.assertEqual(output.read_bytes(), b"racer")
            self.assertEqual(
                list(parent.glob(
                    ".distribution-artifact-set.json.*.tmp")), [])

    def test_vendor_overlay_missing_reordered_or_crosswired_is_rejected(
            self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-dist-") as temporary:
            fixture = self.fixture(Path(temporary))
            document = deepcopy(fixture.vendor_document)
            document["overlays"].pop()
            document["overlay_count"] -= 1
            fixture._write(fixture.vendor_set, fixture._json(document))
            with fixture.source_verification(), self.assertRaisesRegex(
                    verifier.DistributionArtifactSetError, "incomplete"):
                verifier.build_artifact_set(
                    fixture.source_tar, fixture.source_manifest,
                    fixture.vendor_set, fixture.runtime_tar,
                    fixture.runtime_manifest)

        with tempfile.TemporaryDirectory(prefix="hepta-dist-") as temporary:
            fixture = self.fixture(Path(temporary))
            document = deepcopy(fixture.vendor_document)
            document["overlays"].reverse()
            fixture._write(fixture.vendor_set, fixture._json(document))
            with fixture.source_verification(), self.assertRaisesRegex(
                    verifier.DistributionArtifactSetError, "canonical"):
                verifier.build_artifact_set(
                    fixture.source_tar, fixture.source_manifest,
                    fixture.vendor_set, fixture.runtime_tar,
                    fixture.runtime_manifest)

        with tempfile.TemporaryDirectory(prefix="hepta-dist-") as temporary:
            fixture = self.fixture(Path(temporary))
            document = deepcopy(fixture.vendor_document)
            document["overlays"][0]["manifest_path"] = (
                document["overlays"][1]["manifest_path"])
            fixture._write(fixture.vendor_set, fixture._json(document))
            with fixture.source_verification(), self.assertRaisesRegex(
                    verifier.DistributionArtifactSetError, "lineage"):
                verifier.build_artifact_set(
                    fixture.source_tar, fixture.source_manifest,
                    fixture.vendor_set, fixture.runtime_tar,
                    fixture.runtime_manifest)

    def test_runtime_unsafe_relative_path_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-dist-") as temporary:
            fixture = self.fixture(Path(temporary))
            document = deepcopy(fixture.runtime_document)
            document["files"][0]["path"] = "../escape"
            document["files"] = sorted(
                document["files"], key=lambda item: item["path"])
            document["files_sha256"] = sha(
                verifier.canonical_json(document["files"]))
            fixture.write_runtime(document)
            with fixture.source_verification(), self.assertRaisesRegex(
                    verifier.DistributionArtifactSetError,
                    "normalized relative path"):
                verifier.build_artifact_set(
                    fixture.source_tar, fixture.source_manifest,
                    fixture.vendor_set, fixture.runtime_tar,
                    fixture.runtime_manifest)

    def test_same_bytes_rewrite_during_source_verification_is_rejected(
            self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-dist-") as temporary:
            fixture = self.fixture(Path(temporary))

            def rewrite(
                    bundle: Path, manifest: Path) -> dict[str, object]:
                report = fixture.fake_source_report(bundle, manifest)
                payload = bundle.read_bytes()
                bundle.write_bytes(payload)
                bundle.chmod(0o600)
                return report

            with fixture.source_verification(), mock.patch.object(
                    verifier.source_verifier, "verify_bundle",
                    side_effect=rewrite), self.assertRaisesRegex(
                        verifier.DistributionArtifactSetError, "changed"):
                verifier.build_artifact_set(
                    fixture.source_tar, fixture.source_manifest,
                    fixture.vendor_set, fixture.runtime_tar,
                    fixture.runtime_manifest)


if __name__ == "__main__":
    unittest.main()
