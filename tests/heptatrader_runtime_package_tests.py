#!/usr/bin/env python3

from __future__ import annotations

import copy
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import runpy
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock


REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY / "scripts"))
import build_heptatrader_runtime_package as builder  # noqa: E402
import build_hepta_shadow_install_manifest as install_manifest_builder  # noqa: E402
import build_hepta_shadow_runtime_archive as shadow_archive_builder  # noqa: E402
import hepta_p1_shadow_admission_launcher as admission_launcher  # noqa: E402
import hepta_p1_paper_admission_verifier as paper_admission  # noqa: E402
import hepta_p1_safety_soak_auditor as safety_auditor  # noqa: E402
import hepta_p1_safety_soak_evidence_recorder as evidence_recorder  # noqa: E402
import hepta_p1_safety_soak_independent_observer as independent_observer  # noqa: E402
import hepta_p1_watch_activation_transaction as activation  # noqa: E402
import hepta_p1_watch_profile_deployer as profile_deployer  # noqa: E402
import hepta_p1_watch_to_paper_handoff as watch_handoff  # noqa: E402
import hepta_local_paper_control as local_paper_control  # noqa: E402
import hepta_shadow_host_installer as shadow_installer  # noqa: E402
import verify_heptatrader_runtime_package as verifier  # noqa: E402


VERSION = "0.1.0-beta.1-runtime-test"
VENDOR_SPECS = (
    (
        "ctp-6.5.1-tools",
        "third_party/ctp/6.5.1-tools",
        "third_party/ctp/6.5.1-tools/manifest-v1.json",
        "hepta.ctp-tools-overlay-manifest.v1",
        4,
        {"vendor": "CTP", "version": "6.5.1"},
        "archived-legacy-runtime",
    ),
    (
        "ctp-6.7.7",
        "third_party/ctp/6.7.7",
        "third_party/ctp/6.7.7/manifest-v1.json",
        "hepta.vendor-asset-manifest.v1",
        23,
        {"vendor": "CTP", "version": "6.7.7"},
        "disabled-experimental",
    ),
    (
        "prebuilt-dependencies",
        "third_party/prebuilt-dependencies",
        "third_party/prebuilt-dependencies/manifest-v1.json",
        "hepta.prebuilt-asset-manifest.v1",
        8,
        {"vendor": "mixed-legacy", "version": "per-asset"},
        "archived-legacy-prebuilt-inventory",
    ),
)


def private_write(path: Path, payload: bytes) -> None:
    path.write_bytes(payload)
    path.chmod(0o600)


def vendor_canonical(value: object) -> bytes:
    return (
        json.dumps(
            value, ensure_ascii=True, indent=2, sort_keys=True,
            allow_nan=False) + "\n"
    ).encode("utf-8")


class RuntimeFixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.agent = root / "agent"
        self.execution = root / "execution"
        self.elf = Path("/bin/true").read_bytes()
        manifest_records = []
        self.vendor_manifest_payloads: dict[str, bytes] = {}
        for _, _, path, _, _, _, _ in VENDOR_SPECS:
            payload = (REPOSITORY / path).read_bytes()
            self.vendor_manifest_payloads[path] = payload
            manifest_records.append({
                "path": path,
                "mode": "0644",
                "size": len(payload),
                "sha256": verifier.sha256(payload),
            })
        self.source_manifest = {
            "version": VERSION,
            "root": f"heptatrader-{VERSION}",
            "files": manifest_records,
        }
        self.source_ref = {
            "schema": "hepta.clean-source-bundle.v2",
            "bundle_sha256": "sha256:" + "1" * 64,
            "manifest_sha256": "sha256:" + "2" * 64,
            "files_sha256": "sha256:" + "3" * 64,
            "security_manifest_sha256": "sha256:" + "4" * 64,
            "git_head": "5" * 40,
            "root": f"heptatrader-{VERSION}",
        }
        overlays = []
        for (overlay_id, target, path, schema, count, identity,
             capability_status) in VENDOR_SPECS:
            overlays.append({
                "overlay_id": overlay_id,
                "target": target,
                "manifest_path": path,
                "manifest_schema": schema,
                "manifest_sha256":
                    "sha256:" + verifier.sha256(
                        self.vendor_manifest_payloads[path]),
                "declared_asset_count": count,
                "declared_payload_count": count,
                "identity": identity,
                "capability_status": capability_status,
                "payload_included": False,
                "distribution_authorized": False,
                "required_by_runtime_package_ids": [],
            })
        self.vendor = {
            "schema": builder.VENDOR_SCHEMA,
            "release_version": VERSION,
            "artifact_class": builder.VENDOR_ARTIFACT_CLASS,
            "source_ref": {
                key: self.source_ref[key]
                for key in builder.VENDOR_SOURCE_FIELDS
            },
            "overlay_count": 3,
            "overlays": overlays,
            "payload_included": False,
            "distribution_authorized": False,
            "required_by_runtime_package_ids": [],
            "paper_authorized": False,
            "live_authorized": False,
        }
        self.vendor_bytes = vendor_canonical(self.vendor)
        self._create_component(
            self.agent, verifier.AGENT_FILES)
        self._create_component(
            self.execution, verifier.EXECUTION_FILES)

    def _payload(self, relative: str) -> bytes:
        if relative == verifier.PAPER_IDENTITY_SOURCE_PATH:
            return (
                REPOSITORY /
                "systemd/hepta-agent-trust-domain-paper-identities-v1.json.example"
            ).read_bytes()
        if relative == "usr/libexec/hepta-shadow-host-installer":
            return (
                REPOSITORY / "scripts/hepta_shadow_host_installer.py"
            ).read_bytes()
        if relative in verifier.ELF_FILES:
            return self.elf
        if relative in verifier.PYTHON_MODULE_FILES:
            return b'"""fixture runtime Python module."""\n'
        if relative in verifier.PYTHON_FILES:
            shebang = (
                b"#!/usr/bin/env python3\n"
                if relative.endswith("session-bootstrap")
                else b"#!/usr/bin/python3\n")
            return shebang + b'"""fixture runtime script."""\n'
        if relative.endswith((".service", ".socket", ".timer", ".target")):
            return (REPOSITORY / "systemd" / Path(relative).name).read_bytes()
        if relative.endswith(
                "hepta-tool-gateway.service.d/"
                "10-hepta-broker-egress-policy.conf"):
            return (
                REPOSITORY / "systemd/hepta-tool-gateway.service.d/"
                "10-hepta-broker-egress-policy.conf").read_bytes()
        if relative.endswith(
                "hepta-tool-gateway@.service.d/"
                "10-hepta-broker-egress-policy.conf"):
            return (
                REPOSITORY / "systemd/hepta-tool-gateway@.service.d/"
                "10-hepta-broker-egress-policy.conf").read_bytes()
        return (f"fixture:{relative}\n").encode()

    def _create_component(
            self, root: Path, expected: dict[str, int]) -> None:
        root.mkdir(mode=0o755)
        root.chmod(0o755)
        for relative, mode in expected.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
            path.parent.chmod(0o755)
            path.write_bytes(self._payload(relative))
            path.chmod(mode)
        for path in root.rglob("*"):
            if path.is_dir():
                path.chmod(0o755)

    def package(self) -> tuple[bytes, bytes, dict[str, object]]:
        return builder.package_staged_components(
            self.agent, self.execution,
            source_ref=self.source_ref,
            source_manifest=self.source_manifest,
            vendor_descriptor=self.vendor,
            vendor_descriptor_bytes=self.vendor_bytes)

    def publish(
            self, package_bytes: bytes, manifest_bytes: bytes,
            suffix: str = "") -> tuple[Path, Path]:
        package = self.root / f"runtime{suffix}.tar"
        manifest = self.root / f"runtime{suffix}.json"
        private_write(package, package_bytes)
        private_write(manifest, manifest_bytes)
        return package, manifest


def rewrite_tar(
        payload: bytes,
        mutate: callable) -> bytes:
    members: list[tuple[tarfile.TarInfo, bytes]] = []
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:") as archive:
        for member in archive.getmembers():
            stream = archive.extractfile(member)
            data = b"" if stream is None else stream.read()
            members.append((copy.copy(member), data))
    mutate(members)
    output = io.BytesIO()
    archive_format = (
        tarfile.PAX_FORMAT
        if any(member.pax_headers for member, _ in members)
        else tarfile.USTAR_FORMAT)
    with tarfile.open(
            fileobj=output, mode="w:",
            format=archive_format) as archive:
        for member, data in members:
            archive.addfile(member, io.BytesIO(data) if member.isfile() else None)
    return output.getvalue()


def rewrite_payload_and_manifest(
        package_bytes: bytes,
        manifest: dict[str, object],
        path: str,
        data: bytes) -> tuple[bytes, bytes]:
    changed = copy.deepcopy(manifest)
    records = changed["files"]
    assert isinstance(records, list)
    for index, record in enumerate(records):
        if record["path"] == path:
            records[index] = verifier.file_record(
                path, int(record["mode"], 8), data)
            break
    else:
        raise AssertionError(f"fixture runtime path missing: {path}")
    changed["files_sha256"] = (
        "sha256:" + verifier.sha256(verifier.canonical_json(records)))
    manifest_bytes = verifier.canonical_json(changed) + b"\n"

    def replace_members(
            members: list[tuple[tarfile.TarInfo, bytes]]) -> None:
        payload_found = False
        manifest_found = False
        for index, (member, previous) in enumerate(members):
            if member.name.endswith("/" + path):
                member.size = len(data)
                members[index] = member, data
                payload_found = True
            elif member.name.endswith("/" + verifier.INTERNAL_MANIFEST):
                member.size = len(manifest_bytes)
                members[index] = member, manifest_bytes
                manifest_found = True
        if not payload_found or not manifest_found:
            raise AssertionError("fixture package closure is incomplete")

    return rewrite_tar(package_bytes, replace_members), manifest_bytes


class RuntimePackageTests(unittest.TestCase):
    def test_shadow_install_projection_is_deterministic_agent_only(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-shadow-projection-") as temporary:
            fixture = RuntimeFixture(Path(temporary))
            package_bytes, manifest_bytes, _manifest = fixture.package()
            package, manifest_path = fixture.publish(
                package_bytes, manifest_bytes)
            first = fixture.root / "shadow-first.tar.gz"
            second = fixture.root / "shadow-second.tar.gz"
            first_report = shadow_archive_builder.build(
                package, manifest_path, first)
            second_report = shadow_archive_builder.build(
                package, manifest_path, second)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            self.assertEqual(
                first.read_bytes()[:10],
                bytes.fromhex("1f8b08000000000002ff"))
            self.assertEqual(first_report, second_report)
            self.assertEqual(
                first_report["file_count"], len(shadow_archive_builder.SHADOW_FILES))
            self.assertEqual(first_report["schema"],
                             "hepta.shadow-runtime-projection.v2")
            self.assertIs(first_report["host_state_paths_included"], True)
            self.assertEqual(first_report["host_state_path_count"], 1)
            self.assertIs(first_report["default_deny_identity_included"], True)
            records, directories = shadow_installer.archive_records(first)
            self.assertEqual(
                set(records), set(shadow_archive_builder.SHADOW_FILES))
            self.assertEqual(
                directories,
                set(shadow_archive_builder._directories(
                    set(shadow_archive_builder.SHADOW_FILES))))
            self.assertNotIn("usr/libexec/hepta-executiond", records)
            self.assertIn(shadow_archive_builder.IDENTITY_ARCHIVE_PATH, records)
            self.assertIn("usr", directories)
            self.assertIn("etc/heptatrader", directories)
            with tarfile.open(first, "r:gz") as archive:
                for member in archive.getmembers():
                    self.assertEqual((member.uid, member.gid), (0, 0))
                    self.assertEqual((member.uname, member.gname),
                                     ("root", "root"))
                    self.assertEqual(member.mtime, 0)
                    self.assertEqual(member.linkname, "")
                    self.assertEqual((member.devmajor, member.devminor), (0, 0))
                    self.assertEqual(member.pax_headers, {})
                    expected_mode = (
                        0o755 if member.isdir()
                        else shadow_archive_builder.SHADOW_FILES[member.name])
                    self.assertEqual(stat.S_IMODE(member.mode), expected_mode)
                    if member.name == shadow_archive_builder.IDENTITY_ARCHIVE_PATH:
                        stream = archive.extractfile(member)
                        self.assertIsNotNone(stream)
                        assert stream is not None
                        payload = stream.read()
                        self.assertEqual(
                            payload, shadow_archive_builder.IDENTITY_BYTES)
                        self.assertEqual(len(payload), 257)
                        self.assertEqual(
                            shadow_installer.digest_bytes(payload),
                            shadow_archive_builder.IDENTITY_SHA256)
            document = install_manifest_builder.build(
                first, "sha256:" + "1" * 64,
                REPOSITORY / "scripts/hepta_shadow_host_installer.py")
            verified = shadow_installer.verify_archive(first, document)
            self.assertEqual(
                set(verified), set(shadow_archive_builder.SHADOW_FILES))
            self.assertEqual(document["schema"], shadow_installer.MANIFEST_SCHEMA)
            self.assertEqual(document["version"], shadow_installer.MANIFEST_VERSION)
            self.assertIs(document["paper_authorized"], False)
            self.assertIs(document["live_authorized"], False)
            identity_record = next(
                record for record in document["files"]
                if record["path"] == shadow_archive_builder.IDENTITY_ARCHIVE_PATH)
            self.assertEqual(
                identity_record,
                {
                    "path": shadow_archive_builder.IDENTITY_ARCHIVE_PATH,
                    "mode": "0600",
                    "size": 257,
                    "sha256": shadow_archive_builder.IDENTITY_SHA256,
                })

    def test_shadow_install_projection_rejects_runtime_tamper(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-shadow-projection-tamper-") as temporary:
            fixture = RuntimeFixture(Path(temporary))
            package_bytes, manifest_bytes, _manifest = fixture.package()

            def mutate(
                    members: list[tuple[tarfile.TarInfo, bytes]]) -> None:
                member, payload = members[0]
                changed = payload + b"tamper"
                member.size = len(changed)
                members[0] = member, changed

            package, manifest_path = fixture.publish(
                rewrite_tar(package_bytes, mutate), manifest_bytes)
            with self.assertRaises(verifier.RuntimePackageError):
                shadow_archive_builder.build(
                    package, manifest_path,
                    fixture.root / "shadow.tar.gz")

    def test_shadow_install_projection_refuses_existing_output(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-shadow-projection-existing-") as temporary:
            fixture = RuntimeFixture(Path(temporary))
            package_bytes, manifest_bytes, _manifest = fixture.package()
            package, manifest_path = fixture.publish(
                package_bytes, manifest_bytes)
            output = fixture.root / "shadow.tar.gz"
            output.write_bytes(b"owned\n")
            output.chmod(0o600)
            with self.assertRaisesRegex(
                    shadow_archive_builder.ShadowArchiveBuildError,
                    "output already exists"):
                shadow_archive_builder.build(package, manifest_path, output)

    def test_shadow_install_projection_rejects_symlinked_output_parent(
            self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-shadow-projection-parent-") as temporary:
            fixture = RuntimeFixture(Path(temporary))
            package_bytes, manifest_bytes, _manifest = fixture.package()
            package, manifest_path = fixture.publish(
                package_bytes, manifest_bytes)
            real_parent = fixture.root / "real-parent"
            real_parent.mkdir(mode=0o700)
            linked_parent = fixture.root / "linked-parent"
            linked_parent.symlink_to(real_parent, target_is_directory=True)
            with self.assertRaisesRegex(
                    shadow_archive_builder.ShadowArchiveBuildError,
                    "output parent path is unsafe"):
                shadow_archive_builder.build(
                    package, manifest_path,
                    linked_parent / "shadow.tar.gz")
            self.assertFalse((real_parent / "shadow.tar.gz").exists())

    def test_shadow_install_projection_validates_bytes_before_publish(
            self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-shadow-projection-prepublish-") as temporary:
            fixture = RuntimeFixture(Path(temporary))
            package_bytes, manifest_bytes, _manifest = fixture.package()
            package, manifest_path = fixture.publish(
                package_bytes, manifest_bytes)
            output = fixture.root / "shadow.tar.gz"
            with mock.patch.object(
                    shadow_archive_builder.installer,
                    "archive_records_bytes",
                    side_effect=shadow_installer.InstallError(
                        "INSTALL_ARCHIVE_INVALID")):
                with self.assertRaisesRegex(
                        shadow_installer.InstallError,
                        "INSTALL_ARCHIVE_INVALID"):
                    shadow_archive_builder.build(
                        package, manifest_path, output)
            self.assertFalse(output.exists())

    def test_agent_runtime_contract_matches_install_tree_contract(self) -> None:
        install_checker = runpy.run_path(
            str(REPOSITORY / "tests/check_hepta_agent_os_install_tree.py"))
        self.assertEqual(verifier.AGENT_FILES, install_checker["FILES"])

    def test_every_executable_product_has_a_closed_payload_class(self) -> None:
        classified = (
            verifier.ELF_FILES | verifier.PYTHON_FILES |
            verifier.PYTHON_MODULE_FILES)
        executable = {
            path for path, mode in verifier.PRODUCT_FILES.items()
            if mode & 0o111
        }
        self.assertEqual(executable - classified, set())
        self.assertEqual(classified - set(verifier.PRODUCT_FILES), set())
        self.assertEqual(verifier.ELF_FILES & verifier.PYTHON_FILES, set())
        self.assertEqual(
            verifier.PYTHON_MODULE_FILES & verifier.PYTHON_FILES, set())

    def test_shadow_host_installer_is_classified_as_python(self) -> None:
        path = "usr/libexec/hepta-shadow-host-installer"
        self.assertEqual(verifier.AGENT_FILES[path], 0o755)
        self.assertIn(path, verifier.PYTHON_FILES)
        payload = (
            REPOSITORY / "scripts/hepta_shadow_host_installer.py"
        ).read_bytes()
        self.assertEqual(
            verifier.payload_record(path, payload),
            {"kind": "python", "shebang": "#!/usr/bin/env python3"},
        )

    def test_custodian_reconcile_source_matches_approved_semantics(
            self) -> None:
        path = (
            "usr/lib/systemd/system/"
            "hepta-shadow-watch-custodian-reconcile@.service")
        payload = (
            REPOSITORY /
            "systemd/hepta-shadow-watch-custodian-reconcile@.service"
        ).read_bytes()
        lines, _sections = verifier._parse_systemd_unit(path, payload)
        self.assertEqual(
            lines, verifier.APPROVED_SYSTEMD_SEMANTICS[path])
        self.assertIn(
            "ReadWritePaths=-/run/hepta-agent-%i/sessions", lines)
        self.assertIn(
            "ReadWritePaths=-/var/lib/hepta-shadow-watch-%i/private", lines)

    def test_external_p1_canary_units_match_approved_runtime_semantics(
            self) -> None:
        paths = (
            "usr/lib/systemd/system/"
            "hepta-p1-paper-canary-capture.service",
            "usr/lib/systemd/system/"
            "hepta-p1-paper-canary-executor.service",
            "usr/lib/systemd/system/"
            "hepta-p1-paper-canary-root-coordinator.service",
        )
        for path in paths:
            with self.subTest(path=path):
                payload = (
                    REPOSITORY / "systemd" / Path(path).name
                ).read_bytes()
                lines, _sections = verifier._parse_systemd_unit(path, payload)
                self.assertEqual(
                    lines, verifier.APPROVED_SYSTEMD_SEMANTICS[path])
                self.assertIn(path, verifier.SYSTEMD_EXECSTART_CLOSURE)
                self.assertIn(
                    path, verifier.SYSTEMD_CREDENTIAL_SOURCE_CLOSURE)

    def test_certified_paper_deployment_closure_is_exactly_63_files(
            self) -> None:
        prepare = runpy.run_path(
            str(REPOSITORY / "scripts/prepare_repair_campaign.py"))
        operator = runpy.run_path(
            str(REPOSITORY / "scripts/hepta_ib_paper_campaign_operator.py"))
        self.assertEqual(
            prepare["LOCAL_PAPER_DEPLOYMENT_FILE_COUNT"], 63)
        self.assertEqual(
            operator["LOCAL_PAPER_DEPLOYMENT_FILE_COUNT"], 63)
        self.assertEqual(
            prepare["LOCAL_PAPER_DEPLOYMENT_FILES"],
            operator["LOCAL_PAPER_DEPLOYMENT_FILES"])
        self.assertEqual(len(prepare["LOCAL_PAPER_DEPLOYMENT_FILES"]), 63)

    def test_paper_receipt_validator_is_classified_as_python(self) -> None:
        path = "usr/libexec/hepta-paper-receipt-contracts"
        self.assertIn(path, verifier.PYTHON_FILES)
        payload = (
            REPOSITORY / "scripts/hepta_paper_receipt_contracts.py"
        ).read_bytes()
        self.assertEqual(
            verifier.payload_record(path, payload),
            {"kind": "python", "shebang": "#!/usr/bin/env python3"},
        )

    def test_watch_custodian_is_classified_as_python(self) -> None:
        path = "usr/libexec/hepta-shadow-watch-custodian"
        self.assertIn(path, verifier.PYTHON_FILES)
        payload = (
            REPOSITORY / "scripts/hepta_shadow_watch_custodian.py"
        ).read_bytes()
        self.assertEqual(
            verifier.payload_record(path, payload),
            {"kind": "python", "shebang": "#!/usr/bin/env python3"},
        )

    def test_p1_shadow_helpers_are_exact_passive_python_files(self) -> None:
        helpers = {
            "usr/libexec/hepta-p1-shadow-host-controller":
                "scripts/hepta_p1_shadow_host_controller.py",
            "usr/libexec/hepta-p1-load-probe-validator":
                "scripts/hepta_p1_load_probe_validator.py",
            "usr/libexec/build-hepta-p1-observation-policy":
                "scripts/build_hepta_p1_observation_policy.py",
            "usr/libexec/hepta-p1-shadow-observer-controller":
                "scripts/hepta_p1_shadow_observer_controller.py",
            "usr/libexec/hepta-p1-shadow-admission-launcher":
                "scripts/hepta_p1_shadow_admission_launcher.py",
            "usr/libexec/hepta-p1-safety-soak-campaign-freezer":
                "scripts/hepta_p1_safety_soak_campaign_freezer.py",
            "usr/libexec/hepta-p1-safety-soak-policy-planner":
                "scripts/hepta_p1_safety_soak_policy_planner.py",
            "usr/libexec/hepta-p1-safety-soak-campaign-coordinator":
                "scripts/hepta_p1_safety_soak_campaign_coordinator.py",
            "usr/libexec/hepta-p1-safety-soak-observer-worker":
                "scripts/hepta_p1_safety_soak_observer_worker.py",
            "usr/libexec/hepta-p1-safety-soak-recorder-worker":
                "scripts/hepta_p1_safety_soak_recorder_worker.py",
            "usr/libexec/hepta-p1-safety-soak-fault-pin-producer":
                "scripts/hepta_p1_safety_soak_fault_pin_producer.py",
            "usr/libexec/hepta-p1-safety-soak-evidence-recorder":
                "scripts/hepta_p1_safety_soak_evidence_recorder.py",
            "usr/libexec/hepta-p1-safety-soak-independent-observer":
                "scripts/hepta_p1_safety_soak_independent_observer.py",
            "usr/libexec/hepta-p1-safety-soak-root-fault-injector":
                "scripts/hepta_p1_safety_soak_root_fault_injector.py",
            "usr/libexec/hepta-p1-safety-soak-auditor":
                "scripts/hepta_p1_safety_soak_auditor.py",
            "usr/libexec/hepta-p1-watch-to-paper-handoff":
                "scripts/hepta_p1_watch_to_paper_handoff.py",
            "usr/libexec/hepta-p1-watch-profile-deployer":
                "scripts/hepta_p1_watch_profile_deployer.py",
            "usr/libexec/hepta-p1-watch-activation-transaction":
                "scripts/hepta_p1_watch_activation_transaction.py",
            "usr/libexec/hepta-paper-receipt-contracts-v2-compat":
                "scripts/hepta_paper_receipt_contracts_v2_compat.py",
            "usr/libexec/hepta-p1-paper-canary-backend-adapter":
                "scripts/hepta_p1_paper_canary_backend_adapter.py",
            "usr/libexec/hepta-p1-paper-canary-crash-emergency-closer":
                "scripts/hepta_p1_paper_canary_crash_emergency_closer.py",
            "usr/libexec/hepta-p1-paper-canary-executor":
                "scripts/hepta_p1_paper_canary_executor.py",
            "usr/libexec/hepta-p1-paper-canary-handoff-producer":
                "scripts/hepta_p1_paper_canary_handoff_producer.py",
            "usr/libexec/hepta-p1-paper-canary-launch-joiner":
                "scripts/hepta_p1_paper_canary_launch_joiner.py",
            "usr/libexec/hepta-p1-paper-canary-owner-provisioner":
                "scripts/hepta_p1_paper_canary_owner_provisioner.py",
            "usr/libexec/hepta-p1-paper-canary-root-coordinator":
                "scripts/hepta_p1_paper_canary_root_coordinator.py",
            "usr/libexec/hepta-p1-paper-canary-terminal-prover":
                "scripts/hepta_p1_paper_canary_terminal_prover.py",
            "usr/libexec/hepta-bounded-shadow-closure-verifier":
                "scripts/hepta_bounded_shadow_closure_verifier.py",
        }
        for installed, source in helpers.items():
            with self.subTest(installed=installed):
                self.assertEqual(verifier.AGENT_FILES[installed], 0o755)
                self.assertIn(installed, verifier.PYTHON_FILES)
                payload = (REPOSITORY / source).read_bytes()
                expected_shebang = (
                    "#!/usr/bin/env -S /usr/bin/python3.12 -I -S"
                    if installed in {
                        "usr/libexec/hepta-p1-watch-profile-deployer",
                        "usr/libexec/hepta-p1-watch-activation-transaction",
                        "usr/libexec/hepta-p1-watch-to-paper-handoff",
                        "usr/libexec/hepta-p1-paper-canary-backend-adapter",
                        "usr/libexec/"
                        "hepta-p1-paper-canary-crash-emergency-closer",
                        "usr/libexec/hepta-p1-paper-canary-executor",
                        "usr/libexec/hepta-p1-paper-canary-handoff-producer",
                        "usr/libexec/hepta-p1-paper-canary-launch-joiner",
                        "usr/libexec/hepta-p1-paper-canary-owner-provisioner",
                        "usr/libexec/hepta-p1-paper-canary-root-coordinator",
                        "usr/libexec/hepta-p1-paper-canary-terminal-prover",
                    }
                    else "#!/usr/bin/env python3"
                )
                self.assertEqual(
                    verifier.payload_record(installed, payload),
                    {"kind": "python", "shebang": expected_shebang},
                )

    def test_shadow_file_count_is_shared_by_build_and_all_consumers(
            self) -> None:
        expected_paths = {
            path: mode for path, mode in verifier.AGENT_FILES.items()
            if path not in shadow_archive_builder.SHADOW_AGENT_EXCLUSIONS
        }
        self.assertEqual(
            shadow_archive_builder.SHADOW_AGENT_FILES, expected_paths)
        expected_paths[shadow_archive_builder.IDENTITY_ARCHIVE_PATH] = 0o600
        self.assertEqual(shadow_archive_builder.SHADOW_FILES, expected_paths)
        expected_count = len(shadow_archive_builder.SHADOW_FILES)
        self.assertEqual(len(verifier.AGENT_FILES), 143)
        self.assertEqual(expected_count, 128)
        self.assertEqual(
            shadow_archive_builder.SHADOW_AGENT_EXCLUSIONS,
            {
                "usr/libexec/hepta-paper-receipt-contracts-v2-compat",
                "usr/libexec/hepta-p1-paper-canary-backend-adapter",
                "usr/libexec/hepta-p1-paper-canary-crash-emergency-closer",
                "usr/libexec/hepta-p1-paper-canary-executor",
                "usr/libexec/hepta-p1-paper-canary-handoff-producer",
                "usr/libexec/hepta-p1-paper-canary-launch-joiner",
                "usr/libexec/hepta-p1-paper-canary-owner-provisioner",
                "usr/libexec/hepta-p1-paper-canary-root-coordinator",
                "usr/libexec/hepta-p1-paper-canary-terminal-prover",
                "usr/lib/systemd/system/"
                "hepta-p1-paper-canary-capture.service",
                "usr/lib/systemd/system/"
                "hepta-p1-paper-canary-executor.service",
                "usr/lib/systemd/system/"
                "hepta-p1-paper-canary-root-coordinator.service",
                "usr/libexec/hepta-p1-paper-terminal-witness-verifier",
                "usr/lib/systemd/system/hepta-local-paper-fail-close@.service",
                "usr/lib/systemd/system/hepta-p1-paper-terminal-cutoff@.service",
                "usr/lib/systemd/system/"
                "hepta-p1-paper-terminal-witness-verifier@.service",
            },
        )
        self.assertEqual(
            {
                shadow_installer.EXPECTED_SHADOW_FILE_COUNT,
                profile_deployer.SHADOW_INSTALL_FILE_COUNT,
                activation.SHADOW_INSTALL_FILE_COUNT,
                admission_launcher.SHADOW_INSTALL_FILE_COUNT,
            },
            {expected_count},
        )

    def test_passive_shadow_local_control_cannot_self_authorize(self) -> None:
        installed = "usr/libexec/hepta-local-paper-control"
        self.assertEqual(verifier.AGENT_FILES[installed], 0o755)
        self.assertIn(installed, verifier.PYTHON_FILES)
        self.assertIn(installed, shadow_archive_builder.SHADOW_AGENT_FILES)
        self.assertNotIn(
            "usr/lib/systemd/system/hepta-local-paper-authority@.service",
            shadow_archive_builder.SHADOW_FILES)

        with tempfile.TemporaryDirectory(
                prefix="hepta-passive-shadow-paper-control-") as temporary:
            root = Path(temporary)
            state = root / "state"
            runtime = root / "run"
            identities = root / "etc/identities.json"
            authority = root / "state/authority.json"
            env_root = root / "etc/execution"
            gateway_env_root = root / "etc/gateway"
            drop_in = root / "etc/systemd/broker/override.conf"
            calls: list[list[str]] = []
            patches = {
                "ROOT_UID": os.getuid(),
                "ROOT_GID": os.getgid(),
                "LOCAL_PAPER_STATE_ROOT": state,
                "GUARDIAN_RUNTIME_ROOT": runtime,
                "GUARDIAN_REQUEST_PATH": runtime / "guardian-request.json",
                "GUARDIAN_ACTIVE_PATH": runtime / "guardian-active.json",
                "BROKER_START_PERMIT_PATH":
                    runtime / "broker-start-permit.json",
                "DEFAULT_AUTHORITY": authority,
                "DEFAULT_IDENTITIES": identities,
                "DEFAULT_ENV_ROOT": env_root,
                "DEFAULT_GATEWAY_ENV_ROOT": gateway_env_root,
                "DEFAULT_DROP_IN": drop_in,
            }
            with mock.patch.multiple(local_paper_control, **patches):
                with self.assertRaisesRegex(
                        local_paper_control.LocalPaperError,
                        "guardian activation did not commit exactly"):
                    local_paper_control.enable(
                        domain="alpha", authority_path=authority,
                        identities_path=identities, env_root=env_root,
                        drop_in_path=drop_in,
                        gateway_env_root=gateway_env_root,
                        systemctl=lambda arguments: calls.append(arguments))
                self.assertEqual(
                    calls,
                    [["start", local_paper_control.GUARDIAN_UNIT],
                     ["stop", local_paper_control.GUARDIAN_UNIT]])
                self.assertFalse(identities.exists())
                self.assertFalse(drop_in.exists())
                self.assertFalse(
                    (state / "local-paper-control-transaction.json").exists())
                self.assertTrue(
                    (runtime / "guardian-request.json").is_file())

    def test_round114_lineage_contract_is_shared_by_all_consumers(
            self) -> None:
        activation_fields = activation.RECEIPT_FIELDS
        self.assertEqual(
            {
                frozenset(admission_launcher.ACTIVATION_RECEIPT_FIELDS),
                frozenset(paper_admission.ACTIVATION_RECEIPT_FIELDS),
                frozenset(safety_auditor.ACTIVATION_RECEIPT_FIELDS),
                frozenset(evidence_recorder.ACTIVATION_RECEIPT_FIELDS),
                frozenset(independent_observer.ACTIVATION_RECEIPT_FIELDS),
                frozenset(watch_handoff.ACTIVATION_FIELDS),
            },
            {activation_fields},
        )
        self.assertEqual(
            profile_deployer.ROUND114_RECEIPT_FIELDS,
            activation.PROFILE_RECEIPT_FIELDS)
        self.assertEqual(
            profile_deployer.ROUND114_RECEIPT_FIELDS,
            admission_launcher.PROFILE_DEPLOYMENT_RECEIPT_FIELDS)
        self.assertEqual(
            profile_deployer.ROUND114_RECEIPT_FIELDS,
            paper_admission.PROFILE_RECEIPT_FIELDS)
        self.assertEqual(
            {
                activation.ROUND, paper_admission.ROUND,
                watch_handoff.ROUND,
            },
            {114})
        self.assertEqual(
            {
                activation.EXPECTED_SHADOW_INSTALL_GENERATION,
                admission_launcher.EXPECTED_SHADOW_INSTALL_GENERATION,
                paper_admission.INSTALL_GENERATION,
            },
            {22})
        self.assertEqual(
            {
                activation.EXPECTED_PREDECESSOR_SHADOW_INSTALL_GENERATION,
                admission_launcher.EXPECTED_PREDECESSOR_SHADOW_INSTALL_GENERATION,
                paper_admission.PREDECESSOR_INSTALL_GENERATION,
            },
            {21})
        self.assertEqual(
            {
                activation.SHADOW_INSTALL_FILE_COUNT,
                admission_launcher.SHADOW_INSTALL_FILE_COUNT,
                paper_admission.INSTALLED_FILE_COUNT,
            },
            {128})
        self.assertEqual(
            profile_deployer.ROUND95_SHADOW_INSTALL_FILE_COUNT, 127)
        self.assertEqual(
            {
                activation.EXPECTED_PREDECESSOR_CURRENT_INSTALL_POINTER_FILE_SHA256,
                admission_launcher.EXPECTED_PREDECESSOR_CURRENT_INSTALL_POINTER_FILE_SHA256,
                profile_deployer.CURRENT_SHADOW_PREDECESSOR_POINTER_SHA256,
                paper_admission.PREDECESSOR_INSTALL_POINTER_SHA256,
            },
            {
                "sha256:2beeb507fcafbbfc2c93d2e4756fddf0b27e9872733ff97d28af47006461d406"
            })

    def test_root_fault_injector_loads_installed_hyphenated_observer(
            self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-root-fault-installed-import-") as temporary:
            libexec = Path(temporary)
            injector = libexec / "hepta-p1-safety-soak-root-fault-injector"
            observer = (
                libexec / "hepta-p1-safety-soak-independent-observer")
            injector.write_bytes((
                REPOSITORY /
                "scripts/hepta_p1_safety_soak_root_fault_injector.py"
            ).read_bytes())
            observer.write_bytes((
                REPOSITORY /
                "scripts/hepta_p1_safety_soak_independent_observer.py"
            ).read_bytes())
            injector.chmod(0o755)
            observer.chmod(0o755)
            completed = subprocess.run(
                [sys.executable, str(injector), "--help"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="strict",
                cwd="/",
                env={
                    "PATH": "/usr/bin:/bin",
                    "LANG": "C.UTF-8",
                    "LC_ALL": "C.UTF-8",
                    "PYTHONDONTWRITEBYTECODE": "1",
                },
                timeout=15,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stderr, "")
            self.assertIn("--run", completed.stdout)

    def test_fault_pin_producer_loads_installed_hyphenated_dependencies(
            self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-fault-pin-installed-import-") as temporary:
            libexec = Path(temporary)
            sources = {
                "hepta-p1-safety-soak-fault-pin-producer":
                    "hepta_p1_safety_soak_fault_pin_producer.py",
                "hepta-p1-safety-soak-root-fault-injector":
                    "hepta_p1_safety_soak_root_fault_injector.py",
                "hepta-p1-safety-soak-independent-observer":
                    "hepta_p1_safety_soak_independent_observer.py",
            }
            for installed, source in sources.items():
                path = libexec / installed
                path.write_bytes((REPOSITORY / "scripts" / source).read_bytes())
                path.chmod(0o755)
            completed = subprocess.run(
                [sys.executable, str(
                    libexec / "hepta-p1-safety-soak-fault-pin-producer"),
                 "--help"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="strict",
                cwd="/",
                env={
                    "PATH": "/usr/bin:/bin", "LANG": "C.UTF-8",
                    "LC_ALL": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1",
                },
                timeout=15,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stderr, "")
            self.assertIn("--run", completed.stdout)

    def test_p1_paper_admission_payloads_are_exact_runtime_files(
            self) -> None:
        sources = {
            "hepta-p1-paper-admission-verifier":
                ("hepta_p1_paper_admission_verifier.py", "main"),
            "hepta-p1-paper-zero-exposure-snapshot-producer":
                ("hepta_p1_paper_zero_exposure_snapshot_producer.py", "main"),
            "hepta-p1-paper-zero-exposure-attestor":
                ("hepta_p1_paper_zero_exposure_attestor.py", "main"),
            "hepta-rootful-review-closure-consumer":
                ("hepta_rootful_review_closure_consumer.py",
                 "verify_review_closure"),
            "hepta-rootful-systemd-environment-provenance":
                ("hepta_rootful_systemd_environment_provenance.py", "main"),
        }
        with tempfile.TemporaryDirectory(
                prefix="hepta-paper-admission-installed-import-") as temporary:
            libexec = Path(temporary)
            for index, (installed, (source, api)) in enumerate(sources.items()):
                source_payload = (REPOSITORY / "scripts" / source).read_bytes()
                runtime_path = "usr/libexec/" + installed
                self.assertEqual(verifier.AGENT_FILES[runtime_path], 0o755)
                self.assertIn(runtime_path, verifier.PYTHON_FILES)
                self.assertEqual(
                    verifier.payload_record(runtime_path, source_payload),
                    {
                        "kind": "python",
                        "shebang": source_payload.split(
                            b"\n", 1)[0].decode("ascii", errors="strict"),
                    },
                )
                installed_path = libexec / installed
                installed_path.write_bytes(source_payload)
                installed_path.chmod(0o755)
                self.assertEqual(installed_path.read_bytes(), source_payload)
                name = f"_hepta_zero_exposure_installed_{index}"
                loader = importlib.machinery.SourceFileLoader(
                    name, str(installed_path))
                specification = importlib.util.spec_from_loader(name, loader)
                self.assertIsNotNone(specification)
                module = importlib.util.module_from_spec(specification)
                sys.modules[name] = module
                try:
                    loader.exec_module(module)
                finally:
                    sys.modules.pop(name, None)
                self.assertTrue(callable(getattr(module, api)))

        module_path = "usr/libexec/hepta_rootful_review_closure_consumer.py"
        module_payload = (
            REPOSITORY / "scripts/hepta_rootful_review_closure_consumer.py"
        ).read_bytes()
        self.assertEqual(verifier.AGENT_FILES[module_path], 0o644)
        self.assertIn(module_path, verifier.PYTHON_FILES)
        self.assertEqual(
            verifier.payload_record(module_path, module_payload),
            {
                "kind": "python",
                "shebang": "#!/usr/bin/env python3",
            },
        )

    def test_p1_paper_admission_closure_is_local_and_complete(
            self) -> None:
        admission_files = {
            "usr/libexec/hepta-p1-paper-zero-exposure-snapshot-producer":
                0o755,
            "usr/libexec/hepta-p1-paper-zero-exposure-attestor": 0o755,
            "usr/libexec/hepta-p1-paper-admission-verifier": 0o755,
            "usr/libexec/hepta-rootful-review-closure-consumer": 0o755,
            "usr/libexec/hepta-rootful-systemd-environment-provenance":
                0o755,
            "usr/libexec/hepta_rootful_review_closure_consumer.py": 0o644,
        }
        self.assertEqual(
            {path: verifier.AGENT_FILES[path] for path in admission_files},
            admission_files,
        )
        self.assertTrue(set(admission_files).issubset(verifier.PYTHON_FILES))

    def test_round_trip_exact_passive_files(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-runtime-roundtrip-") as temporary:
            fixture = RuntimeFixture(Path(temporary))
            package_bytes, manifest_bytes, manifest = fixture.package()
            package, manifest_path = fixture.publish(
                package_bytes, manifest_bytes)
            report = verifier.verify_package(package, manifest_path)
            self.assertEqual(report["schema"], verifier.SCHEMA)
            self.assertEqual(
                report["file_count"], verifier.PRODUCT_FILE_COUNT)
            self.assertEqual(
                {record["path"] for record in manifest["files"]},
                set(verifier.PRODUCT_FILES))
            self.assertEqual(
                manifest["boundary"], verifier.BOUNDARY)
            self.assertEqual(
                manifest["vendor_ref"]["overlay_count"], 3)
            self.assertEqual(
                manifest["vendor_ref"]["required_overlay_ids"], [])
            self.assertIs(
                manifest["boundary"]["paper_authorized"], False)
            self.assertIs(
                manifest["boundary"]["live_authorized"], False)
            self.assertIs(
                manifest["boundary"]["passive_provisioning"], True)
            self.assertIs(
                manifest["boundary"]["host_state_paths_included"], False)
            self.assertEqual(
                report["package_sha256"],
                "sha256:" + verifier.sha256(package_bytes))

    def test_default_deny_identity_source_is_exact_and_cannot_be_rebound(
            self) -> None:
        self.assertEqual(
            verifier.AGENT_FILES[verifier.PAPER_IDENTITY_SOURCE_PATH],
            0o644)
        self.assertEqual(len(verifier.PAPER_IDENTITY_SOURCE_BYTES), 257)
        self.assertEqual(
            "sha256:" + verifier.sha256(
                verifier.PAPER_IDENTITY_SOURCE_BYTES),
            verifier.PAPER_IDENTITY_SOURCE_SHA256)
        with tempfile.TemporaryDirectory(
                prefix="hepta-runtime-paper-identity-drift-") as temporary:
            fixture = RuntimeFixture(Path(temporary))
            package_bytes, _, manifest = fixture.package()
            changed = verifier.PAPER_IDENTITY_SOURCE_BYTES.replace(
                b'"paper_authorized": false',
                b'"paper_authorized": true ', 1)
            self.assertEqual(
                len(changed), len(verifier.PAPER_IDENTITY_SOURCE_BYTES))
            changed_package, changed_manifest = rewrite_payload_and_manifest(
                package_bytes, manifest,
                verifier.PAPER_IDENTITY_SOURCE_PATH, changed)
            package, manifest_path = fixture.publish(
                changed_package, changed_manifest)
            with self.assertRaisesRegex(
                    verifier.RuntimePackageError,
                    "PAPER identity source record drift"):
                verifier.verify_package(package, manifest_path)

    def test_stage_rejects_default_deny_manifest_source_drift(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-runtime-paper-identity-stage-") as temporary:
            fixture = RuntimeFixture(Path(temporary))
            target = fixture.agent / verifier.PAPER_IDENTITY_SOURCE_PATH
            target.write_bytes(b"{}\n")
            target.chmod(0o644)
            with self.assertRaisesRegex(
                    builder.RuntimeBuildError,
                    "PAPER identity source"):
                fixture.package()

    def test_packaging_is_byte_deterministic(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-runtime-deterministic-") as temporary:
            fixture = RuntimeFixture(Path(temporary))
            first = fixture.package()
            second = fixture.package()
            self.assertEqual(first[0], second[0])
            self.assertEqual(first[1], second[1])
            self.assertEqual(first[2], second[2])

    def test_component_duplicate_must_match_bytes_and_mode(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-runtime-duplicate-") as temporary:
            fixture = RuntimeFixture(Path(temporary))
            shared = (
                fixture.execution /
                "usr/share/heptatrader/hepta-service-identities-v1.json")
            shared.write_bytes(b"different\n")
            shared.chmod(0o644)
            with self.assertRaisesRegex(
                    builder.RuntimeBuildError, "duplicate differs"):
                fixture.package()

    def test_stage_rejects_symlink_hardlink_missing_and_sdk(self) -> None:
        cases = ("symlink", "hardlink", "missing", "sdk")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory(
                    prefix=f"hepta-runtime-stage-{case}-") as temporary:
                fixture = RuntimeFixture(Path(temporary))
                target = fixture.agent / "usr/bin/heptactl"
                if case == "symlink":
                    target.unlink()
                    target.symlink_to("/bin/true")
                elif case == "hardlink":
                    alternate = fixture.root / "hardlink-source"
                    alternate.write_bytes(fixture.elf)
                    target.unlink()
                    os.link(alternate, target)
                    target.chmod(0o755)
                elif case == "missing":
                    target.unlink()
                else:
                    sdk = fixture.agent / "usr/include/heptatrader/sdk.h"
                    sdk.parent.mkdir(parents=True)
                    sdk.write_text("sdk\n", encoding="utf-8")
                with self.assertRaises(builder.RuntimeBuildError):
                    fixture.package()

    def test_stage_rejects_same_bytes_path_replacement_during_read(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-runtime-stage-replace-") as temporary:
            fixture = RuntimeFixture(Path(temporary))
            target = fixture.agent / "usr/bin/heptactl"
            real_open = os.open
            replaced = False

            def replace_after_open(path: object, flags: int, *args, **kwargs):
                nonlocal replaced
                descriptor = real_open(path, flags, *args, **kwargs)
                if (not replaced and os.path.abspath(os.fspath(path)) ==
                        os.path.abspath(target)):
                    replaced = True
                    target.unlink()
                    target.write_bytes(fixture.elf)
                    target.chmod(0o755)
                return descriptor

            with mock.patch.object(
                    builder.os, "open", side_effect=replace_after_open):
                with self.assertRaisesRegex(
                        builder.RuntimeBuildError, "changed before open"):
                    fixture.package()
            self.assertTrue(replaced)

    def test_unapproved_python_shebang_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-runtime-shebang-") as temporary:
            fixture = RuntimeFixture(Path(temporary))
            script = fixture.agent / "usr/libexec/hepta-mcp-server"
            script.write_bytes(b"#!/usr/bin/python\nprint('x')\n")
            script.chmod(0o755)
            with self.assertRaisesRegex(
                    verifier.RuntimePackageError, "unapproved shebang"):
                fixture.package()

    def test_vendor_descriptor_source_and_boundary_are_exact(self) -> None:
        cases = ("source", "payload", "count", "row")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory(
                    prefix=f"hepta-runtime-vendor-{case}-") as temporary:
                fixture = RuntimeFixture(Path(temporary))
                if case == "source":
                    fixture.vendor["source_ref"]["git_head"] = "6" * 40
                elif case == "payload":
                    fixture.vendor["payload_included"] = True
                elif case == "count":
                    fixture.vendor["overlay_count"] = 2
                else:
                    fixture.vendor["overlays"][0][
                        "distribution_authorized"] = True
                fixture.vendor_bytes = vendor_canonical(fixture.vendor)
                with self.assertRaises(builder.RuntimeBuildError):
                    fixture.package()

    def test_verifier_rejects_internal_external_manifest_drift(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-runtime-manifest-drift-") as temporary:
            fixture = RuntimeFixture(Path(temporary))
            package_bytes, manifest_bytes, manifest = fixture.package()
            changed = copy.deepcopy(manifest)
            changed["source_ref"]["bundle_sha256"] = "sha256:" + "a" * 64
            changed_bytes = verifier.canonical_json(changed) + b"\n"
            package, manifest_path = fixture.publish(
                package_bytes, changed_bytes)
            with self.assertRaisesRegex(
                    verifier.RuntimePackageError,
                    "internal and external runtime manifests differ"):
                verifier.verify_package(package, manifest_path)

    def test_verifier_rejects_symlink_hardlink_and_unsafe_metadata(self) -> None:
        cases = ("symlink", "hardlink", "owner", "mtime", "pax")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory(
                    prefix=f"hepta-runtime-tar-{case}-") as temporary:
                fixture = RuntimeFixture(Path(temporary))
                package_bytes, manifest_bytes, _ = fixture.package()

                def mutate(
                        members: list[tuple[tarfile.TarInfo, bytes]]) -> None:
                    member, data = members[0]
                    if case == "symlink":
                        member.type = tarfile.SYMTYPE
                        member.linkname = "/etc/passwd"
                        member.size = 0
                        members[0] = member, b""
                    elif case == "hardlink":
                        member.type = tarfile.LNKTYPE
                        member.linkname = next(
                            candidate.name for candidate, _ in members
                            if candidate.name.endswith("/usr/bin/heptactl"))
                        member.size = 0
                        members[0] = member, b""
                    elif case == "owner":
                        member.uid = 1000
                    elif case == "mtime":
                        member.mtime = 1
                    else:
                        member.pax_headers = {"path": "../../escape"}

                changed = rewrite_tar(package_bytes, mutate)
                package, manifest_path = fixture.publish(
                    changed, manifest_bytes)
                with self.assertRaises(verifier.RuntimePackageError):
                    verifier.verify_package(package, manifest_path)

    def test_verifier_rejects_payload_and_elf_record_drift(self) -> None:
        cases = ("payload", "elf")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory(
                    prefix=f"hepta-runtime-record-{case}-") as temporary:
                fixture = RuntimeFixture(Path(temporary))
                package_bytes, manifest_bytes, manifest = fixture.package()
                changed = copy.deepcopy(manifest)
                if case == "payload":
                    record = next(
                        item for item in changed["files"]
                        if item["path"] ==
                        "usr/share/heptatrader/.agents/plugins/marketplace.json")
                    record["sha256"] = "sha256:" + "a" * 64
                else:
                    record = next(
                        item for item in changed["files"]
                        if item["path"] == "usr/bin/heptactl")
                    record["payload"]["build_id"] = "ab"
                changed["files_sha256"] = (
                    "sha256:" + verifier.sha256(
                        verifier.canonical_json(changed["files"])))
                changed_bytes = verifier.canonical_json(changed) + b"\n"

                def replace_internal(
                        members: list[tuple[tarfile.TarInfo, bytes]]) -> None:
                    for index, (member, _) in enumerate(members):
                        if member.name.endswith(
                                "/" + verifier.INTERNAL_MANIFEST):
                            member.size = len(changed_bytes)
                            members[index] = member, changed_bytes
                            return
                    self.fail("internal manifest missing")

                changed_package = rewrite_tar(
                    package_bytes, replace_internal)
                package, manifest_path = fixture.publish(
                    changed_package, changed_bytes)
                with self.assertRaises(verifier.RuntimePackageError):
                    verifier.verify_package(package, manifest_path)

    def test_verifier_rejects_noncanonical_runtime_manifest(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-runtime-noncanonical-") as temporary:
            fixture = RuntimeFixture(Path(temporary))
            package_bytes, _, manifest = fixture.package()
            noncanonical = (
                json.dumps(manifest, ensure_ascii=True, indent=2,
                           sort_keys=True) + "\n").encode("ascii")
            package, manifest_path = fixture.publish(
                package_bytes, noncanonical)
            with self.assertRaisesRegex(
                    verifier.RuntimePackageError, "not canonical JSON"):
                verifier.verify_package(package, manifest_path)

    def test_verifier_rejects_registered_arbitrary_runtime_path(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-runtime-extra-path-") as temporary:
            fixture = RuntimeFixture(Path(temporary))
            package_bytes, _, manifest = fixture.package()
            changed = copy.deepcopy(manifest)
            changed["files"].append(verifier.file_record(
                "usr/share/heptatrader/arbitrary-extra.txt",
                0o644, b"extra\n"))
            changed["files"] = sorted(
                changed["files"], key=lambda item: item["path"])
            changed["file_count"] = len(changed["files"])
            changed["files_sha256"] = (
                "sha256:" + verifier.sha256(
                    verifier.canonical_json(changed["files"])))
            changed_bytes = verifier.canonical_json(changed) + b"\n"
            package, manifest_path = fixture.publish(
                package_bytes, changed_bytes)
            with self.assertRaisesRegex(
                    verifier.RuntimePackageError,
                    f"exactly {verifier.PRODUCT_FILE_COUNT} files"):
                verifier.verify_package(package, manifest_path)

    def test_verifier_rejects_unsafe_systemd_service_semantics(self) -> None:
        cases = {
            "execstart": (
                "usr/lib/systemd/system/hepta-tool-gateway.service",
                b"ExecStart=/usr/libexec/hepta-tool-gatewayd",
                b"ExecStart=/bin/sh -c /usr/libexec/hepta-tool-gatewayd",
            ),
            "sandbox": (
                "usr/lib/systemd/system/hepta-tool-gateway.service",
                b"PrivateNetwork=yes",
                b"PrivateNetwork=no",
            ),
            "domain-execstart": (
                "usr/lib/systemd/system/hepta-tool-gateway@.service",
                b"ExecStart=/usr/libexec/hepta-tool-gatewayd",
                b"ExecStart=/bin/sh -c /usr/libexec/hepta-tool-gatewayd",
            ),
            "domain-socket-owner": (
                "usr/lib/systemd/system/hepta-tool-gateway@.socket",
                b"SocketUser=hepta-agent-%i",
                b"SocketUser=hepta-agent",
            ),
            "broker-policy-capability": (
                "usr/lib/systemd/system/hepta-broker-egress-policy.service",
                b"CapabilityBoundingSet=CAP_NET_ADMIN",
                b"CapabilityBoundingSet=",
            ),
            "custodian-reconcile-session-path": (
                "usr/lib/systemd/system/"
                "hepta-shadow-watch-custodian-reconcile@.service",
                b"ReadWritePaths=-/run/hepta-agent-%i/sessions",
                b"ReadWritePaths=/run/hepta-agent-%i/sessions",
            ),
            "custodian-reconcile-private-path": (
                "usr/lib/systemd/system/"
                "hepta-shadow-watch-custodian-reconcile@.service",
                b"ReadWritePaths=-/var/lib/hepta-shadow-watch-%i/private",
                b"ReadWritePaths=-/var/lib/hepta-shadow-watch-%i",
            ),
        }
        for case, (service, before, after) in cases.items():
            with self.subTest(case=case), tempfile.TemporaryDirectory(
                    prefix=f"hepta-runtime-unit-{case}-") as temporary:
                fixture = RuntimeFixture(Path(temporary))
                package_bytes, _, manifest = fixture.package()
                original = (
                    REPOSITORY / "systemd" / Path(service).name
                ).read_bytes()
                self.assertIn(before, original)
                changed_data = original.replace(before, after, 1)
                changed_package, changed_manifest = (
                    rewrite_payload_and_manifest(
                        package_bytes, manifest, service, changed_data))
                package, manifest_path = fixture.publish(
                    changed_package, changed_manifest)
                with self.assertRaisesRegex(
                        verifier.RuntimePackageError,
                        "systemd semantics drift"):
                    verifier.verify_package(package, manifest_path)

    def test_verifier_rejects_broker_policy_dropin_drift(self) -> None:
        relative = (
            "usr/lib/systemd/system/hepta-tool-gateway@.service.d/"
            "10-hepta-broker-egress-policy.conf")
        with tempfile.TemporaryDirectory(
                prefix="hepta-runtime-dropin-") as temporary:
            fixture = RuntimeFixture(Path(temporary))
            package_bytes, _, manifest = fixture.package()
            original = (
                REPOSITORY / "systemd/hepta-tool-gateway@.service.d/"
                "10-hepta-broker-egress-policy.conf").read_bytes()
            changed = original.replace(
                b"BindsTo=hepta-broker-egress-policy.service",
                b"Wants=hepta-broker-egress-policy.service", 1)
            changed_package, changed_manifest = rewrite_payload_and_manifest(
                package_bytes, manifest, relative, changed)
            package, manifest_path = fixture.publish(
                changed_package, changed_manifest)
            with self.assertRaisesRegex(
                    verifier.RuntimePackageError,
                    "systemd drop-in semantics drift"):
                verifier.verify_package(package, manifest_path)

    def test_private_publication_rejects_writable_parent(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-runtime-output-parent-") as temporary:
            private = Path(temporary) / "private"
            private.mkdir(mode=0o700)
            output = private / "runtime.tar"
            builder._write_new_private(
                output, b"payload", "runtime package")
            self.assertEqual(output.read_bytes(), b"payload")
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
            self.assertEqual(output.stat().st_nlink, 1)

            parent = Path(temporary) / "writable"
            parent.mkdir(mode=0o700)
            parent.chmod(0o777)
            with self.assertRaisesRegex(
                    builder.RuntimeBuildError,
                    "not group/world-writable"):
                builder._write_new_private(
                    parent / "runtime.tar", b"payload", "runtime package")

    def test_private_publication_never_overwrites_destination_racer(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-runtime-output-racer-") as temporary:
            parent = Path(temporary) / "private"
            parent.mkdir(mode=0o700)
            output = parent / "runtime.tar"
            real_link = builder.os.link
            raced = False

            def install_racer(source: object, destination: object, *args,
                              **kwargs):
                nonlocal raced
                if not raced and os.fspath(destination) == output.name:
                    raced = True
                    output.write_bytes(b"racer")
                    output.chmod(0o600)
                return real_link(source, destination, *args, **kwargs)

            with mock.patch.object(
                    builder.os, "link", side_effect=install_racer):
                with self.assertRaisesRegex(
                        builder.RuntimeBuildError, "already exists"):
                    builder._write_new_private(
                        output, b"ours", "runtime package")
            self.assertTrue(raced)
            self.assertEqual(output.read_bytes(), b"racer")
            self.assertEqual(
                list(parent.glob(".runtime.tar.*.tmp")), [])

    def test_private_publication_rejects_parent_replacement_and_rolls_back(
            self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-runtime-output-parent-race-") as temporary:
            root = Path(temporary)
            parent = root / "private"
            moved = root / "moved-private"
            parent.mkdir(mode=0o700)
            output = parent / "runtime.tar"
            real_link = builder.os.link
            replaced = False

            def replace_parent(source: object, destination: object, *args,
                               **kwargs):
                nonlocal replaced
                result = real_link(source, destination, *args, **kwargs)
                if not replaced and os.fspath(destination) == output.name:
                    replaced = True
                    parent.rename(moved)
                    parent.mkdir(mode=0o700)
                return result

            with mock.patch.object(
                    builder.os, "link", side_effect=replace_parent):
                with self.assertRaisesRegex(
                        builder.RuntimeBuildError,
                        "ancestor identity changed"):
                    builder._write_new_private(
                        output, b"ours", "runtime package")
            self.assertTrue(replaced)
            self.assertFalse(output.exists())
            self.assertFalse((moved / output.name).exists())
            self.assertEqual(
                list(moved.glob(".runtime.tar.*.tmp")), [])

    def test_failed_publication_rolls_back_only_its_exact_inode(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-runtime-output-inode-race-") as temporary:
            parent = Path(temporary) / "private"
            parent.mkdir(mode=0o700)
            output = parent / "runtime.tar"
            real_link = builder.os.link
            replaced = False

            def replace_destination(source: object, destination: object, *args,
                                    **kwargs):
                nonlocal replaced
                result = real_link(source, destination, *args, **kwargs)
                if not replaced and os.fspath(destination) == output.name:
                    replaced = True
                    output.unlink()
                    output.write_bytes(b"racer")
                    output.chmod(0o600)
                return result

            with mock.patch.object(
                    builder.os, "link", side_effect=replace_destination):
                with self.assertRaisesRegex(
                        builder.RuntimeBuildError,
                        "publication identity|no-overwrite link"):
                    builder._write_new_private(
                        output, b"ours", "runtime package")
            self.assertTrue(replaced)
            self.assertEqual(output.read_bytes(), b"racer")
            self.assertEqual(
                list(parent.glob(".runtime.tar.*.tmp")), [])

    def test_runtime_second_output_failure_rolls_back_first_only(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-runtime-output-pair-") as temporary:
            parent = Path(temporary) / "private"
            parent.mkdir(mode=0o700)
            package = parent / "runtime.tar"
            manifest = parent / "runtime.json"
            manifest.write_bytes(b"racer")
            manifest.chmod(0o600)
            with self.assertRaisesRegex(
                    builder.RuntimeBuildError, "already exists"):
                builder._publish_runtime_outputs(
                    package, b"package", manifest, b"manifest")
            self.assertFalse(package.exists())
            self.assertEqual(manifest.read_bytes(), b"racer")
            self.assertEqual(
                list(parent.glob(".runtime.*.tmp")), [])

    def test_runtime_pair_publication_verifies_before_commit_success(
            self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-runtime-output-pair-positive-") as temporary:
            fixture = RuntimeFixture(Path(temporary))
            package_bytes, manifest_bytes, _ = fixture.package()
            output = fixture.root / "published"
            output.mkdir(mode=0o700)
            package = output / "runtime.tar"
            manifest = output / "runtime.json"
            report = builder._publish_runtime_outputs(
                package, package_bytes, manifest, manifest_bytes)
            self.assertEqual(
                report["file_count"], verifier.PRODUCT_FILE_COUNT)
            self.assertEqual(package.stat().st_nlink, 1)
            self.assertEqual(manifest.stat().st_nlink, 1)
            self.assertEqual(stat.S_IMODE(package.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(manifest.stat().st_mode), 0o600)

    def test_strict_json_and_private_input_modes(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-runtime-json-") as temporary:
            root = Path(temporary)
            duplicate = root / "manifest.json"
            private_write(
                duplicate,
                b'{"schema":"hepta.runtime-package.v1","schema":"x"}\n')
            package = root / "package.tar"
            private_write(package, b"not a tar")
            with self.assertRaisesRegex(
                    verifier.RuntimePackageError, "duplicate JSON key"):
                verifier.verify_package(package, duplicate)
            duplicate.chmod(0o644)
            with self.assertRaisesRegex(
                    verifier.RuntimePackageError, "0600"):
                verifier.verify_package(package, duplicate)

    def test_elf_rpath_is_rejected_when_linker_is_available(self) -> None:
        compiler = shutil.which("cc")
        if compiler is None:
            self.skipTest("C compiler is unavailable")
        with tempfile.TemporaryDirectory(
                prefix="hepta-runtime-rpath-") as temporary:
            root = Path(temporary)
            source = root / "main.c"
            binary = root / "main"
            source.write_text("int main(void) { return 0; }\n", encoding="ascii")
            result = os.spawnv(
                os.P_WAIT, compiler,
                [compiler, str(source), "-Wl,-rpath,/tmp", "-o", str(binary)])
            if result != 0:
                self.skipTest("C linker cannot build the RPATH fixture")
            with self.assertRaisesRegex(
                    verifier.RuntimePackageError, "RPATH or RUNPATH"):
                verifier.inspect_elf(binary.read_bytes())


if __name__ == "__main__":
    unittest.main()
