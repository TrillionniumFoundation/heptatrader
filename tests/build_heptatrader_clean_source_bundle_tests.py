#!/usr/bin/env python3

from pathlib import Path
import copy
import io
import json
import os
import re
import stat
import sys
import tarfile
import tempfile
import unittest
from unittest import mock


REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY / "scripts"))
import build_heptatrader_clean_source_bundle as bundle  # noqa: E402
import verify_heptatrader_clean_source_bundle as verifier  # noqa: E402


def cmake_repo_install_sources(cmake_path: Path) -> set[str]:
    """Return every repository source in install(FILES/PROGRAMS ...)."""
    text = cmake_path.read_text(encoding="utf-8")
    command = re.compile(
        r"\binstall\s*\(\s*(FILES|PROGRAMS)\b", re.IGNORECASE)
    sources: set[str] = set()
    for match in command.finditer(text):
        opening = text.find("(", match.start())
        depth = 0
        quoted = False
        escaped = False
        closing = None
        for offset in range(opening, len(text)):
            character = text[offset]
            if quoted:
                if escaped:
                    escaped = False
                elif character == "\\":
                    escaped = True
                elif character == '"':
                    quoted = False
                continue
            if character == '"':
                quoted = True
            elif character == "(":
                depth += 1
            elif character == ")":
                depth -= 1
                if depth == 0:
                    closing = offset
                    break
        if closing is None:
            raise AssertionError("unterminated install(FILES/PROGRAMS) command")

        arguments = re.findall(
            r'"(?:\\.|[^"\\])*"|[^\s()]+', text[opening + 1:closing])
        install_kind = arguments.pop(0).upper()
        if install_kind not in {"FILES", "PROGRAMS"}:
            raise AssertionError("install command kind parser drift")
        try:
            destination = arguments.index("DESTINATION")
        except ValueError as error:
            raise AssertionError(
                "repository install(FILES/PROGRAMS) must use DESTINATION"
            ) from error
        file_arguments = arguments[:destination]
        if not file_arguments:
            raise AssertionError("install(FILES/PROGRAMS) has no sources")

        for raw in file_arguments:
            value = raw[1:-1] if raw.startswith('"') else raw
            if value.startswith("${CMAKE_CURRENT_BINARY_DIR}/"):
                # Generated configuration files are not source inputs.
                continue
            source_prefix = "${CMAKE_CURRENT_SOURCE_DIR}/"
            if value.startswith(source_prefix):
                candidate = cmake_path.parent / value[len(source_prefix):]
            elif "${" in value or "$<" in value:
                raise AssertionError(
                    f"unreviewed install source expression: {value}")
            else:
                candidate = cmake_path.parent / value
            candidate = candidate.resolve(strict=True)
            try:
                relative = candidate.relative_to(REPOSITORY).as_posix()
            except ValueError as error:
                raise AssertionError(
                    f"install source escapes repository: {value}") from error
            sources.add(relative)

    return sources


def repository_cmake_inputs() -> list[Path]:
    """Find source CMake inputs without entering generated/build trees."""
    result: list[Path] = []
    for directory, names, files in os.walk(REPOSITORY, followlinks=False):
        directory_path = Path(directory)
        retained = []
        for name in names:
            candidate = directory_path / name
            relative = candidate.relative_to(REPOSITORY).as_posix() + "/"
            if (name == "__pycache__" or name == ".git" or
                    relative.startswith(bundle.FORBIDDEN_PREFIXES)):
                continue
            retained.append(name)
        names[:] = retained
        for name in files:
            if name == "CMakeLists.txt" or name.endswith(".cmake"):
                result.append(directory_path / name)
    return sorted(result)


class CleanSourcePathPolicyTests(unittest.TestCase):
    def test_repo_install_sources_enter_positive_security_closure(self) -> None:
        cmake_inputs = repository_cmake_inputs()
        self.assertIn(REPOSITORY / "HeptaTrade" / "CMakeLists.txt", cmake_inputs)
        installed_sources: set[str] = set()
        for cmake_path in cmake_inputs:
            installed_sources.update(cmake_repo_install_sources(cmake_path))
        source_manifest, _ = bundle.load_security_manifest(REPOSITORY)
        security_sources = {
            record["path"] for record in source_manifest["files"]}
        self.assertGreater(len(installed_sources), 100)
        self.assertEqual(installed_sources - security_sources, set())

    def test_round38_trust_domain_templates_enter_strict_source(self) -> None:
        source_manifest, _ = bundle.load_security_manifest(REPOSITORY)
        paths = {record["path"] for record in source_manifest["files"]}
        self.assertTrue({
            "systemd/hepta-agent-trust-domain.json.example",
            "systemd/hepta-tool-gateway-domain.env.example",
            "systemd/hepta-tool-gateway@.service",
            "systemd/hepta-tool-gateway@.socket",
            "systemd/hepta-tool-session-supervisor@.socket",
        }.issubset(paths))

    def test_shadow_release_projection_helpers_enter_security_closure(
            self) -> None:
        source_manifest, _ = bundle.load_security_manifest(REPOSITORY)
        paths = {record["path"] for record in source_manifest["files"]}
        self.assertTrue({
            "scripts/build_hepta_shadow_install_manifest.py",
            "scripts/build_hepta_shadow_runtime_archive.py",
        }.issubset(paths))

    def test_visual_studio_output_trees_are_forbidden(self) -> None:
        for path in (
                "HeptaSimulator/x64/Release/HeptaSimulator.obj",
                "HeptaStrategy/x64/Release/HeptaStrategy.lib.recipe",
                "HeptaTrade/x64/Release/HeptaTrader.exe",
                "HeptaTrade/HeptaTrader/x64/Release/HeptaTrader.tlog/a.tlog"):
            self.assertTrue(path.startswith(bundle.FORBIDDEN_IDE_OUTPUT_PREFIXES))

    def test_reviewed_interface_prebuilt_libraries_are_overlay_only(self) -> None:
        for path in (
                "Interface/IBApi/bin/CSharpAPI.dll",
                "Interface/IBApi/lib/libbid.lib",
                "Interface/lib/Ubuntu/Release/libTinyXml_Linux.a",
                "Interface/lib/Ubuntu/Release/libheptaHeptaDLL_Linux.a",
                "Interface/lib/X64/Release/heptaHeptaDLL.lib",
                "Interface/lib/X64/Release/tinyxml.lib"):
            self.assertIn(path, bundle.PREBUILT_PAYLOAD_PATHS)
            self.assertTrue(bundle.is_compiled_payload(path))

    def test_unreviewed_compiled_payload_types_are_denied(self) -> None:
        for path in (
                "HeptaTrade/future.o",
                "Interface/lib/future.a",
                "third_party/future.dll",
                "tests/fixture.so",
                "tests/libfixture.so.1",
                "tests/libfixture.so.1.2",
                "tests/addon.node",
                "tests/native.pyd",
                "tests/module.wasm",
                "tests/Fixture.class",
                "tests/fixture.jar"):
            self.assertTrue(bundle.is_compiled_payload(path))
        self.assertFalse(bundle.is_compiled_payload("tests/fixture.cpp"))

    def test_compiled_payload_magic_is_denied_without_extension(self) -> None:
        pe = bytearray(b"MZ" + b"\0" * 126)
        pe[60:64] = (96).to_bytes(4, "little")
        pe[96:100] = b"PE\0\0"
        coff = (
            (0x8664).to_bytes(2, "little") +
            (1).to_bytes(2, "little") + b"\0" * 16)
        payloads = {
            "tests/fixture-elf.cpp": b"\x7fELF" + b"\0" * 64,
            "tests/fixture-pe.cpp": bytes(pe),
            "tests/fixture-coff.cpp": coff,
            "tests/fixture-ar.cpp": b"!<arch>\n" + b"\0" * 64,
            "tests/fixture-thin-ar.cpp": b"!<thin>\n" + b"\0" * 64,
            "tests/fixture-bitcode.cpp": b"BC\xc0\xde" + b"\0" * 64,
            "tests/fixture-wasm.cpp": b"\0asm" + b"\1\0\0\0",
            "tests/fixture-zip.cpp": b"PK\x03\x04" + b"\0" * 64,
            "tests/fixture-gzip.cpp": b"\x1f\x8b\x08\0" + b"\0" * 64,
            "tests/fixture-class.cpp": b"\xca\xfe\xba\xbe" + b"\0" * 16,
        }
        for path, data in payloads.items():
            with self.subTest(path=path):
                self.assertIsNotNone(bundle.compiled_payload_magic(data))
                with self.assertRaisesRegex(
                        RuntimeError, "compiled payload content"):
                    bundle.reject_compiled_payload(path, data)
        self.assertIsNone(
            bundle.compiled_payload_magic(b"ordinary reviewed source\n"))
        self.assertIsNone(
            bundle.compiled_payload_magic(b"MZ source prose is not PE\n"))
        disguised = b"/* reviewed-looking prefix */\n" + b"\x7fELF\0payload"
        with self.assertRaisesRegex(
                RuntimeError, "binary or non-text"):
            bundle.reject_compiled_payload(
                "tests/disguised.cpp", disguised)
        with self.assertRaisesRegex(
                SystemExit, "binary or non-text"):
            verifier.reject_compiled_payload(
                "tests/disguised.cpp", disguised)
        for path, data in payloads.items():
            with self.subTest(verifier_path=path):
                with self.assertRaisesRegex(
                        SystemExit, "compiled payload content"):
                    verifier.reject_compiled_payload(path, data)
        with self.assertRaisesRegex(
                SystemExit, "unsupported strict-source path"):
            verifier.reject_compiled_payload(
                "tests/libfixture.so.1", b"not-even-required")

    def test_strict_source_path_allowlist_and_policy_are_exact(self) -> None:
        for path in (
                "HeptaTrade/source.cpp",
                "HeptaTrade/header.h",
                "cmake/verify_gateway_forbidden_symbols.cmake",
                "scripts/check.py",
                "systemd/hepta-systemd-gate.apparmor",
                "systemd/example.service",
                "tests/agent_os_rootful_systemd/Dockerfile",
                "tests/agent_os_rootful_systemd/"
                "hepta-agent-os-systemd-entrypoint",
                "tests/broker_network_rootful/Dockerfile",
                "tests/paper_domain_rootful_systemd/Dockerfile",
                "tests/paper_domain_rootful_systemd/"
                "hepta-paper-domain-systemd-entrypoint",
                "tests/p1_campaign_rootful_liveness_systemd/Dockerfile",
                "tests/p1_campaign_rootful_liveness_systemd/"
                "hepta-p1-liveness-systemd-entrypoint",
                "tests/p1_dual_domain_rootful_systemd/Dockerfile",
                "tests/p1_dual_domain_rootful_systemd/"
                "hepta-p1-dual-domain-systemd-entrypoint",
                "tests/rootful_systemd/Dockerfile",
                "tests/rootful_systemd/hepta-systemd-entrypoint",
                "tests/rootful_systemd_base/Dockerfile"):
            bundle.reject_compiled_payload(path, b"reviewed source\n")
            verifier.reject_compiled_payload(path, b"reviewed source\n")
        for path in (
                "tests/future_rootful_systemd/Dockerfile",
                "tests/paper_domain_rootful_systemd/unreviewed-entrypoint",
                "tests/module.so.1.debug",
                "tests/disguised-payload",
                "tests/archive.zip",
                "tests/package.bin"):
            with self.subTest(path=path):
                with self.assertRaisesRegex(
                        RuntimeError, "unsupported strict-source path"):
                    bundle.reject_compiled_payload(path, b"payload")
                with self.assertRaisesRegex(
                        SystemExit, "unsupported strict-source path"):
                    verifier.reject_compiled_payload(path, b"payload")
        self.assertEqual(
            bundle.compiled_payload_policy_sha256(),
            verifier.compiled_payload_policy_sha256())
        self.assertRegex(
            bundle.compiled_payload_policy_sha256(),
            r"^sha256:[0-9a-f]{64}$")

    def test_unlicensed_ctp_overlay_is_not_distributable(self) -> None:
        for path in (
                "Interface/CTPTradeApi32/thostmduserapi_se.dll",
                "Interface/CTPTradeApi64/thosttraderapi_se.lib",
                "Interface/CTPTradeApiLinux/thosttraderapi_se.so",
                "third_party/ctp/6.7.7/include/ThostFtdcTraderApi.h",
                "third_party/ctp/6.7.7/lib/future-vendor-payload.so"):
            self.assertTrue(
                bundle.is_nonredistributable_vendor_artifact(path))

    def test_ctp_provenance_metadata_remains_eligible(self) -> None:
        for path in (
                "third_party/ctp/6.7.7/README.md",
                "third_party/ctp/6.7.7/manifest-v1.json"):
            self.assertFalse(
                bundle.is_nonredistributable_vendor_artifact(path))

    def test_local_config_and_ide_user_state_are_forbidden(self) -> None:
        for path in (
                "HeptaTrade/HeptaTraderConfig.xml",
                "HeptaTrade/HeptaTrader.vcxproj.user",
                "HeptaTrade/local.suo",
                "HeptaTrade/cache.VC.db"):
            self.assertTrue(bundle.is_forbidden_local_artifact(path))

    def test_reviewed_fail_closed_templates_remain_eligible(self) -> None:
        for path in (
                "HeptaTrade/HeptaTraderConfig.xml.example",
                "HeptaTrade/HeptaTraderConfig.paper.xml",
                "HeptaTrade/IBRisk.template.xml"):
            self.assertFalse(bundle.is_forbidden_local_artifact(path))

    def test_bundle_verifier_rejects_noncanonical_paths(self) -> None:
        for path in (
                "x/../third_party/ctp/6.7.7/include/payload.h",
                "/absolute/path",
                "./relative",
                "double//separator",
                "windows\\separator",
                "nul\0path"):
            with self.assertRaises(SystemExit):
                verifier.canonical_relative(path, "fixture path")
        self.assertEqual(
            verifier.canonical_relative(
                "third_party/ctp/6.7.7/manifest-v1.json", "fixture path"),
            "third_party/ctp/6.7.7/manifest-v1.json")
        for path in ("../escape", "double//separator", "windows\\separator"):
            with self.assertRaises(RuntimeError):
                bundle.canonical_relative(path)

    def test_bundle_verifier_rejects_unsafe_internal_manifest_mode(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-clean-source-verifier-") as temporary:
            root = Path(temporary)
            version = "fixture"
            baseline_path = (
                "release-manifests/"
                "heptatrader-agent-os-vfixture/manifest.json")
            files = [
                {
                    "path": "README.md", "mode": "0644", "size": 0,
                    "sha256": verifier.digest(b""),
                },
                {
                    "path": baseline_path, "mode": "0644", "size": 0,
                    "sha256": verifier.digest(b""),
                },
                *({
                    "path": path, "mode": "0644", "size": 0,
                    "sha256": verifier.digest(b""),
                } for path in sorted(
                    verifier.REDISTRIBUTABLE_VENDOR_METADATA)),
            ]
            manifest = {
                "schema": "hepta.clean-source-bundle.v2",
                "bundle_class": "strict-source-only",
                "version": version,
                "git_head": "0" * 40,
                "root": "heptatrader-fixture",
                "file_count": len(files),
                "files_sha256": verifier.digest(json.dumps(
                    files, ensure_ascii=True, separators=(",", ":"),
                    sort_keys=True).encode()),
                "security_manifest_sha256": "sha256:" + "0" * 64,
                "security_manifest_file_count": 1,
                "excluded_unsafe_tree": "compat/unsafe-direct-broker",
                "excluded_legacy_runtime_tree": "Tools",
                "excluded_nonredistributable_vendor_prefixes": list(
                    verifier.NONREDISTRIBUTABLE_VENDOR_PREFIXES),
                "redistributable_vendor_metadata_allowlist": sorted(
                    verifier.REDISTRIBUTABLE_VENDOR_METADATA),
                "nonredistributable_vendor_payload_included": False,
                "excluded_prebuilt_payload_paths": sorted(
                    verifier.PREBUILT_PAYLOAD_PATHS),
                "excluded_prebuilt_overlay_prefixes": list(
                    verifier.PREBUILT_OVERLAY_PREFIXES),
                "compiled_payload_suffixes_denied": sorted(
                    verifier.COMPILED_PAYLOAD_SUFFIXES),
                "compiled_payload_policy_version":
                    verifier.COMPILED_PAYLOAD_POLICY_VERSION,
                "compiled_payload_policy_sha256":
                    verifier.compiled_payload_policy_sha256(),
                "prebuilt_payload_included": False,
                "paper_authorized": False,
                "live_authorized": False,
                "files": files,
            }
            manifest_bytes = (
                json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
            external = root / "manifest.json"
            external.write_bytes(manifest_bytes)
            external.chmod(0o600)
            bundle_path = root / "bundle.tar"
            with tarfile.open(
                    bundle_path, "w", format=tarfile.GNU_FORMAT) as archive:
                member = tarfile.TarInfo(
                    "heptatrader-fixture/"
                    ".hepta/source-bundle-manifest.json")
                member.size = len(manifest_bytes)
                member.mode = 0o777
                member.uid = member.gid = 0
                member.uname = member.gname = "root"
                member.mtime = 0
                archive.addfile(member, io.BytesIO(manifest_bytes))
            bundle_path.chmod(0o600)
            with self.assertRaisesRegex(
                    SystemExit, "internal bundle manifest mode"):
                verifier.verify_bundle(bundle_path, external)


class CleanSourceStableIoTests(unittest.TestCase):
    def test_source_is_captured_once_and_reused_for_manifest_and_tar(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-clean-source-capture-") as temporary:
            root = Path(temporary)
            root.chmod(0o700)
            source = root / "source.txt"
            source.write_bytes(b"captured-once")
            source.chmod(0o644)
            captures = bundle.capture_sources(root, [source])
            source.write_bytes(b"changed-after-capture")
            manifest = {
                "root": "heptatrader-fixture",
                "files": [{
                    "path": captures[0].path,
                    "mode": format(captures[0].mode, "04o"),
                    "size": len(captures[0].data),
                    "sha256": verifier.digest(captures[0].data),
                }],
            }
            output = root / "bundle.tar"
            external = root / "bundle.manifest.json"
            bundle.publish_bundle(
                output, external, manifest, captures, root)
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(external.stat().st_mode), 0o600)
            with tarfile.open(output, "r:") as archive:
                packaged = archive.extractfile(
                    "heptatrader-fixture/source.txt")
                self.assertIsNotNone(packaged)
                assert packaged is not None
                self.assertEqual(packaged.read(), b"captured-once")
            external_manifest = json.loads(external.read_text())
            self.assertEqual(
                external_manifest["files"][0]["sha256"],
                verifier.digest(b"captured-once"))

    def test_stable_source_detects_in_read_mutation(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-clean-source-toctou-") as temporary:
            root = Path(temporary)
            source = root / "source.txt"
            source.write_bytes(b"alpha")
            descriptor = os.open(root, bundle.directory_flags())
            original_read = os.read
            changed = False

            def mutating_read(fd: int, count: int) -> bytes:
                nonlocal changed
                data = original_read(fd, count)
                if data and not changed:
                    changed = True
                    source.write_bytes(b"bravo")
                return data

            try:
                with mock.patch.object(
                        bundle.os, "read", side_effect=mutating_read):
                    with self.assertRaisesRegex(
                            RuntimeError, "changed while reading"):
                        bundle.stable_source_bytes(descriptor, "source.txt")
            finally:
                os.close(descriptor)

    def test_stable_source_rejects_symlink_leaf(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-clean-source-symlink-") as temporary:
            root = Path(temporary)
            (root / "source.txt").write_bytes(b"source")
            (root / "alias.txt").symlink_to("source.txt")
            descriptor = os.open(root, bundle.directory_flags())
            try:
                with self.assertRaises(RuntimeError):
                    bundle.stable_source_bytes(descriptor, "alias.txt")
            finally:
                os.close(descriptor)

    def test_output_rejects_source_and_symlink_collisions(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-clean-source-output-") as temporary:
            root = Path(temporary)
            root.chmod(0o700)
            source = root / "source.txt"
            source.write_bytes(b"source")
            captures = bundle.capture_sources(root, [source])
            source_paths = {Path(os.path.abspath(source))}
            identities = {
                (captures[0].metadata.st_dev, captures[0].metadata.st_ino)}
            with self.assertRaisesRegex(RuntimeError, "collides"):
                bundle.prepare_output_target(
                    source, source_paths, identities)
            for injected in (
                    'bundle";touch-INJECTED;.tar', "bundle\nforged.tar",
                    "bundle$(id).tar"):
                with self.assertRaisesRegex(RuntimeError, "filename is invalid"):
                    bundle.prepare_output_target(
                        root / injected, source_paths, identities)

            linked = root / "linked-output"
            linked.symlink_to(source.name)
            with self.assertRaisesRegex(RuntimeError, "unsafe"):
                bundle.prepare_output_target(
                    linked, source_paths, identities)

            real_parent = root / "real-parent"
            real_parent.mkdir(mode=0o700)
            alias_parent = root / "alias-parent"
            alias_parent.symlink_to(real_parent.name, target_is_directory=True)
            with self.assertRaises(OSError):
                bundle.prepare_output_target(
                    alias_parent / "bundle.tar", source_paths, identities)

            manifest = {
                "root": "heptatrader-fixture",
                "files": [{
                    "path": captures[0].path,
                    "mode": format(captures[0].mode, "04o"),
                    "size": len(captures[0].data),
                    "sha256": verifier.digest(captures[0].data),
                }],
            }
            with self.assertRaisesRegex(RuntimeError, "must be distinct"):
                bundle.publish_bundle(
                    root / "same-output", root / "same-output",
                    manifest, captures, root)

    def test_verifier_requires_nofollow_private_single_link_inputs(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-clean-source-input-") as temporary:
            root = Path(temporary)
            payload = root / "payload"
            payload.write_bytes(b"payload")
            payload.chmod(0o600)
            self.assertEqual(
                verifier.stable_private_bytes(payload, "fixture", 1024),
                b"payload")
            payload.chmod(0o644)
            with self.assertRaisesRegex(SystemExit, "single-link 0600"):
                verifier.stable_private_bytes(payload, "fixture", 1024)
            payload.chmod(0o600)
            alias = root / "alias"
            alias.symlink_to(payload.name)
            with self.assertRaisesRegex(SystemExit, "single-link 0600"):
                verifier.stable_private_bytes(alias, "fixture", 1024)
            alias.unlink()
            os.link(payload, alias)
            with self.assertRaisesRegex(SystemExit, "single-link 0600"):
                verifier.stable_private_bytes(payload, "fixture", 1024)

    def test_verifier_detects_in_read_input_mutation(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-clean-source-verify-toctou-") as temporary:
            payload = Path(temporary) / "payload"
            payload.write_bytes(b"alpha")
            payload.chmod(0o600)
            original_read = os.read
            changed = False

            def mutating_read(fd: int, count: int) -> bytes:
                nonlocal changed
                data = original_read(fd, count)
                if data and not changed:
                    changed = True
                    payload.write_bytes(b"bravo")
                    payload.chmod(0o600)
                return data

            with mock.patch.object(
                    verifier.os, "read", side_effect=mutating_read):
                with self.assertRaisesRegex(
                        SystemExit, "changed while reading"):
                    verifier.stable_private_bytes(payload, "fixture", 1024)


class CleanSourceProvenanceTests(unittest.TestCase):
    def provenance(self) -> dict[str, bytes]:
        return {
            path: (REPOSITORY / path).read_bytes()
            for path in (
                verifier.prebuilt_verifier.PREBUILT_MANIFEST,
                verifier.prebuilt_verifier.LEGACY_CTP_MANIFEST,
                verifier.prebuilt_verifier.CURRENT_CTP_MANIFEST,
            )
        }

    @staticmethod
    def encoded(document: dict) -> bytes:
        return (
            json.dumps(document, ensure_ascii=True, sort_keys=True) + "\n"
        ).encode()

    def test_all_three_metadata_manifests_are_fully_validated(self) -> None:
        provenance = self.provenance()
        verifier.verify_provenance_manifests(provenance)

        cases = []
        prebuilt = json.loads(
            provenance[verifier.prebuilt_verifier.PREBUILT_MANIFEST])
        prebuilt["payload_distribution_authorized"] = True
        cases.append((
            verifier.prebuilt_verifier.PREBUILT_MANIFEST, prebuilt))

        legacy = json.loads(
            provenance[verifier.prebuilt_verifier.LEGACY_CTP_MANIFEST])
        legacy["distribution_authorized"] = True
        cases.append((
            verifier.prebuilt_verifier.LEGACY_CTP_MANIFEST, legacy))

        current = json.loads(
            provenance[verifier.prebuilt_verifier.CURRENT_CTP_MANIFEST])
        current["distribution_authorized"] = True
        cases.append((
            verifier.prebuilt_verifier.CURRENT_CTP_MANIFEST, current))

        incomplete = json.loads(
            provenance[verifier.prebuilt_verifier.CURRENT_CTP_MANIFEST])
        incomplete["platform_assets"] = incomplete["platform_assets"][:-1]
        cases.append((
            verifier.prebuilt_verifier.CURRENT_CTP_MANIFEST, incomplete))

        for path, document in cases:
            with self.subTest(path=path):
                changed = dict(provenance)
                changed[path] = self.encoded(document)
                with self.assertRaisesRegex(
                        SystemExit, "supply-chain provenance"):
                    verifier.verify_provenance_manifests(changed)

    def test_duplicate_keys_and_nonfinite_values_are_rejected(self) -> None:
        for payload in (
                b'{"schema":"first","schema":"second"}',
                b'{"value":NaN}',
                b'{"value":Infinity}'):
            with self.subTest(payload=payload):
                with self.assertRaisesRegex(
                        SystemExit, "strict UTF-8 JSON"):
                    verifier.strict_json(payload, "fixture")

        provenance = self.provenance()
        path = verifier.prebuilt_verifier.CURRENT_CTP_MANIFEST
        duplicate = provenance[path].replace(
            b"{", b'{"schema":"forged",', 1)
        changed = dict(provenance)
        changed[path] = duplicate
        with self.assertRaisesRegex(
                SystemExit, "strict UTF-8 JSON"):
            verifier.verify_provenance_manifests(changed)

    def test_baseline_file_record_is_bound_to_bundle_record(self) -> None:
        record = {
            "mode": "0644",
            "path": "README.md",
            "sha256": "sha256:" + verifier.digest(b"reviewed"),
            "size": len(b"reviewed"),
        }
        records = [record]
        security_sha256 = "sha256:" + verifier.digest(json.dumps(
            records, ensure_ascii=True, separators=(",", ":"),
            sort_keys=True).encode())
        baseline = self.encoded({
            "version": "fixture",
            "git_head": "0" * 40,
            "source_manifest": {
                "file_count": 1,
                "files": records,
                "sha256": security_sha256,
            },
        })
        expected = {
            "README.md": {
                "path": "README.md", "mode": "0644",
                "size": len(b"reviewed"),
                "sha256": verifier.digest(b"reviewed"),
            },
        }
        verifier.verify_security_baseline(
            baseline, expected, version="fixture", git_head="0" * 40,
            security_sha256=security_sha256, security_file_count=1)
        drifted = copy.deepcopy(expected)
        drifted["README.md"]["sha256"] = verifier.digest(b"swapped")
        with self.assertRaisesRegex(SystemExit, "file record drift"):
            verifier.verify_security_baseline(
                baseline, drifted, version="fixture", git_head="0" * 40,
                security_sha256=security_sha256, security_file_count=1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
