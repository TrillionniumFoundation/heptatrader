#!/usr/bin/env python3

import hashlib
import io
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock


REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY / "scripts"))

import build_heptatrader_verification_evidence as evidence  # noqa: E402
import build_heptatrader_delivery_closure as common  # noqa: E402
import run_heptatrader_coverage_evidence as coverage_runner  # noqa: E402


class VerificationEvidenceTests(unittest.TestCase):
    @staticmethod
    def _cache_text(
        label: str,
        home: str = "/source",
        build: str = "/build",
        ctest: str = "/usr/bin/ctest",
    ) -> str:
        ibapi = "ON" if label in evidence.IBAPI_ON_LABELS else "OFF"
        cxx_flags = ""
        linker_flags = ""
        build_type = "Release" if label in evidence.MATRIX_LABELS else "Debug"
        if label in evidence.SANITIZER_LABELS:
            cxx_flags = evidence.SANITIZER_FLAGS[label]
            linker_flags = evidence.SANITIZER_FLAGS[label]
        elif label == "coverage":
            cxx_flags = "--coverage"
            linker_flags = "--coverage"
        return (
            "BUILD_TESTING:BOOL=ON\n"
            "CMAKE_C_COMPILER:FILEPATH=/usr/bin/cc\n"
            f"CMAKE_CACHEFILE_DIR:INTERNAL={build}\n"
            "CMAKE_C_FLAGS:STRING=\n"
            f"CMAKE_CTEST_COMMAND:FILEPATH={ctest}\n"
            "CMAKE_CXX_COMPILER:FILEPATH=/usr/bin/c++\n"
            "CMAKE_GENERATOR:INTERNAL=Unix Makefiles\n"
            f"CMAKE_BUILD_TYPE:STRING={build_type}\n"
            f"CMAKE_HOME_DIRECTORY:INTERNAL={home}\n"
            f"CMAKE_CXX_FLAGS:STRING={cxx_flags}\n"
            f"CMAKE_EXE_LINKER_FLAGS:STRING={linker_flags}\n"
            "CMAKE_CXX_FLAGS_DEBUG:STRING=-g\n"
            "CMAKE_CXX_FLAGS_RELEASE:STRING=-O3 -DNDEBUG\n"
            "CMAKE_CXX_FLAGS_RELWITHDEBINFO:STRING=-O2 -g\n"
            "CMAKE_CXX_FLAGS_MINSIZEREL:STRING=-Os\n"
            f"HEPTA_ENABLE_IBAPI:BOOL={ibapi}\n"
            "HEPTA_ENABLE_LEGACY_0DTE_BRIDGE:BOOL=OFF\n"
            "HEPTA_BUILD_LEGACY_MONOLITH:BOOL=OFF\n"
            "HEPTA_BUILD_LEGACY_SIMULATOR:BOOL=OFF\n"
        )

    @staticmethod
    def _private_ctest(
            root: Path, *, mutating: bool = False) -> Path:
        tool_directory = root / "fixture-tools"
        tool_directory.mkdir(mode=0o700, exist_ok=True)
        wrapper = tool_directory / (
            "ctest-mutating" if mutating else "ctest")
        if not wrapper.exists():
            native = shutil.which("ctest")
            if native is None:
                raise AssertionError("ctest is unavailable")
            mutation = (
                "printf '# drift\\n' >> \"$0\"\n"
                if mutating else "")
            wrapper.write_text(
                "#!/bin/sh\n"
                f"{mutation}"
                f"exec {shlex.quote(Path(native).resolve(strict=True).as_posix())} "
                "\"$@\"\n",
                encoding="utf-8")
            wrapper.chmod(0o700)
        return wrapper

    @staticmethod
    def _unprovisioned_coverage_toolchain() -> dict[str, object]:
        executable = lambda path: {
            "configured_path": path,
            "realpath": None,
            "sha256": None,
            "size": None,
            "mode": None,
        }
        return {
            "schema": evidence.COVERAGE_TOOLCHAIN_SCHEMA,
            "version": 1,
            "provisioned": False,
            "runner_labels": [
                "self-hosted", "linux", "x64",
                "heptatrader-coverage-v1",
            ],
            "python": executable(
                "/opt/heptatrader/coverage-v1/bin/python"),
            "gcov": executable(
                "/opt/heptatrader/coverage-v1/bin/gcov"),
            "immutable_tool_root_receipt": executable(
                "/opt/heptatrader/coverage-v1/"
                "immutable-tool-root-receipt.json"),
            "distributions": [{
                "name": name,
                "version": version,
                "root": None,
                "file_count": None,
                "files_sha256": None,
            } for name, version in
                evidence.COVERAGE_TOOLCHAIN_DISTRIBUTIONS],
        }

    @staticmethod
    def _agent_manifest() -> dict[str, object]:
        release = "0.1.0-beta.1-round38"
        records = [{
            "path": "CMakeLists.txt",
            "mode": "0644",
            "size": 1,
            "sha256": hashlib.sha256(b"x").hexdigest(),
        }]
        return {
            "schema": "hepta.agent-os-source-bundle.v1",
            "version": 1,
            "bundle_class": "agent-os-source-only",
            "release_version": release,
            "root": f"heptatrader-agent-os-{release}",
            "file_count": 1,
            "paper_authorized": False,
            "live_authorized": False,
            "files_sha256": hashlib.sha256(
                common.canonical_json(records)).hexdigest(),
            "policy_sha256": "a" * 64,
            "excluded_non_product_prefixes": [],
            "excluded_non_product_files": [],
            "excluded_legacy_prefixes": [],
            "excluded_legacy_files": [],
            "parent_strict_source": {
                "schema": "hepta.clean-source-bundle.v2",
                "git_head": "b" * 40,
                "root": f"heptatrader-{release}",
                "file_count": 2,
                "files_sha256": "c" * 64,
                "bundle_sha256": "d" * 64,
                "manifest_sha256": "c" * 64,
            },
            "files": records,
        }

    @staticmethod
    def _strict_manifest() -> dict[str, object]:
        release = "0.1.0-beta.1-round38"
        records = [{
            "path": "CMakeLists.txt",
            "mode": "0644",
            "size": 1,
            "sha256": hashlib.sha256(b"x").hexdigest(),
        }]
        return {
            "schema": "hepta.clean-source-bundle.v2",
            "version": release,
            "root": f"heptatrader-{release}",
            "git_head": "b" * 40,
            "file_count": 1,
            "files_sha256": hashlib.sha256(
                common.canonical_json(records)).hexdigest(),
            "paper_authorized": False,
            "live_authorized": False,
            "files": records,
        }

    def _ctest_lane(
        self, root: Path, label: str, count: int, *,
        volatile_directory: bool = False,
        mutating_ctest: bool = False,
        expect_success: bool = True,
    ) -> str | None:
        work = root / f"work-{label}"
        work.mkdir(mode=0o700)
        ctest = self._private_ctest(
            root, mutating=mutating_ctest)
        source = work / "source"
        if label in evidence.NO_GIT_LABELS:
            source = work / str(self._agent_manifest()["root"])
        elif label in evidence.STRICT_SOURCE_LABELS:
            source = work / str(self._strict_manifest()["root"])
        source.mkdir(mode=0o700)
        volatile = work / "volatile-condition"
        if volatile_directory:
            volatile.mkdir(mode=0o700)
        test_commands = []
        for index in range(count):
            if volatile_directory and index == 0:
                test_commands.append(
                    f'add_test(NAME test-{index} COMMAND '
                    f'"/usr/bin/test" "-d" "{volatile.as_posix()}")')
            else:
                test_commands.append(
                    f'add_test(NAME test-{index} COMMAND "/usr/bin/true")')
        tests = "\n".join(test_commands)
        cmake_text = (
            "cmake_minimum_required(VERSION 3.16)\n"
            "project(hepta_evidence_fixture LANGUAGES C CXX)\n"
            "include(CTest)\n"
            "option(HEPTA_ENABLE_IBAPI \"\" OFF)\n"
            "option(HEPTA_ENABLE_LEGACY_0DTE_BRIDGE \"\" OFF)\n"
            "option(HEPTA_BUILD_LEGACY_MONOLITH \"\" OFF)\n"
            "option(HEPTA_BUILD_LEGACY_SIMULATOR \"\" OFF)\n"
            f"{tests}\n"
        )
        cmake_file = source / "CMakeLists.txt"
        cmake_file.write_text(cmake_text, encoding="utf-8")
        cmake_file.chmod(0o644)
        build = work / "build"
        build_type = (
            "Debug" if label in evidence.SANITIZER_LABELS else "Release")
        sanitizer_flag = evidence.SANITIZER_FLAGS.get(label, "")
        configure = [
            shutil.which("cmake") or "/usr/bin/cmake",
            "-S", source.as_posix(),
            "-B", build.as_posix(),
            f"-DCMAKE_BUILD_TYPE={build_type}",
            "-DBUILD_TESTING=ON",
            "-DHEPTA_ENABLE_IBAPI=" +
            ("ON" if label in evidence.IBAPI_ON_LABELS else "OFF"),
            "-DHEPTA_ENABLE_LEGACY_0DTE_BRIDGE=OFF",
            "-DHEPTA_BUILD_LEGACY_MONOLITH=OFF",
            "-DHEPTA_BUILD_LEGACY_SIMULATOR=OFF",
            "-DCMAKE_C_FLAGS=",
            f"-DCMAKE_CXX_FLAGS={sanitizer_flag}",
            f"-DCMAKE_EXE_LINKER_FLAGS={sanitizer_flag}",
            f"-DCMAKE_CTEST_COMMAND={ctest.as_posix()}",
        ]
        old_umask = os.umask(0o077)
        try:
            configured = subprocess.run(
                configure,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
                timeout=120,
            )
        finally:
            os.umask(old_umask)
        self.assertEqual(
            configured.returncode, 0,
            configured.stdout.decode("utf-8", errors="replace"))
        arguments = [
            sys.executable,
            (REPOSITORY / evidence.CTEST_RUNNER_SOURCE).as_posix(),
            "--label", label,
            "--expected-count", str(count),
            "--build-dir", build.as_posix(),
            "--artifact-root", root.as_posix(),
            "--stdout", f"{label}.stdout.log",
            "--inventory", f"{label}.inventory.json",
            "--sidecar", f"{label}.sidecar.json",
            "--cache-output", f"{label}.cache",
            "--timeout-seconds", "120",
        ]
        if label in evidence.SOURCE_ATTESTATION_LABELS:
            manifest = (
                self._agent_manifest()
                if label in evidence.NO_GIT_LABELS else
                self._strict_manifest())
            record = manifest["files"][0]
            record["size"] = len(cmake_text.encode())
            record["sha256"] = hashlib.sha256(cmake_text.encode()).hexdigest()
            manifest["files_sha256"] = hashlib.sha256(
                common.canonical_json(manifest["files"])).hexdigest()
            manifest_payload = (
                json.dumps(manifest, sort_keys=True) + "\n").encode()
            manifest_source = work / "source-manifest.json"
            manifest_source.write_bytes(manifest_payload)
            manifest_source.chmod(0o600)
            if label in evidence.NO_GIT_LABELS:
                internal = (
                    source / evidence.AGENT_SOURCE_INTERNAL_MANIFEST)
                internal.parent.mkdir(mode=0o700)
                internal.write_bytes(manifest_payload)
                internal.chmod(0o644)
            else:
                internal = (
                    source / evidence.STRICT_SOURCE_INTERNAL_MANIFEST)
                internal.parent.mkdir(mode=0o700)
                internal.write_bytes(manifest_payload)
                internal.chmod(0o644)
            arguments.extend([
                "--source-manifest", manifest_source.as_posix(),
                "--source-manifest-output", f"{label}.source.json",
            ])
        executed = subprocess.run(
            arguments,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=300,
        )
        output = executed.stdout.decode("utf-8", errors="replace")
        if not expect_success:
            self.assertNotEqual(executed.returncode, 0, output)
            self.assertIn(
                "configured CTest changed during execution", output)
            return None
        self.assertEqual(executed.returncode, 0, output)
        return f"{label}={count}={label}.sidecar.json"

    def _coverage_lane(self, root: Path, line_rate: float = 0.799) -> str:
        names = {
            "stdout": "coverage.stdout",
            "inventory": "coverage.inventory.json",
            "gcovr": "coverage.gcovr.stdout",
            "cache": "coverage.cache",
            "xml": "coverage.xml",
            "strict": "strict-source.json",
            "policy": "coverage-policy.json",
            "raw_manifest": "coverage-raw.json",
            "raw_archive": "coverage-raw.tar",
            "sidecar": "coverage-sidecar.json",
        }
        source = "/tmp/heptatrader-test"
        build = "/build-coverage"
        (root / names["stdout"]).write_text(
            "100% tests passed, 0 tests failed out of 1\n",
            encoding="utf-8")
        inventory_document = {
            "kind": "ctestInfo",
            "version": {"major": 1, "minor": 0},
            "tests": [{
                "name": "coverage-test",
                "command": ["/usr/bin/true"],
                "properties": [{
                    "name": "WORKING_DIRECTORY",
                    "value": build,
                }],
            }],
        }
        (root / names["inventory"]).write_text(
            json.dumps(inventory_document, sort_keys=True) + "\n",
            encoding="utf-8")
        (root / names["gcovr"]).write_text(
            f"lines: {line_rate * 100:.1f}%\n", encoding="utf-8")
        ctest = self._private_ctest(root)
        (root / names["cache"]).write_text(
            self._cache_text(
                "coverage", source, build, ctest.as_posix()),
            encoding="utf-8")
        (root / names["xml"]).write_text(
            f'<coverage line-rate="{line_rate}"></coverage>\n',
            encoding="utf-8")
        source_records = [{
            "path": "dummy",
            "mode": "0644",
            "size": 1,
            "sha256": hashlib.sha256(b"x").hexdigest(),
        }]
        strict = {
            "schema": "hepta.clean-source-bundle.v2",
            "version": "test",
            "root": "heptatrader-test",
            "git_head": "a" * 40,
            "file_count": len(source_records),
            "files_sha256": hashlib.sha256(
                common.canonical_json(source_records)).hexdigest(),
            "paper_authorized": False,
            "live_authorized": False,
            "files": source_records,
        }
        (root / names["strict"]).write_text(
            json.dumps(strict, sort_keys=True) + "\n", encoding="utf-8")
        (root / names["policy"]).write_text(
            json.dumps({
                "coverage": {
                    "line_minimum_percent": 70,
                    "toolchain":
                        self._unprovisioned_coverage_toolchain(),
                },
            }, sort_keys=True) + "\n", encoding="utf-8")
        (root / names["policy"]).chmod(0o600)
        raw_data = b"raw-gcov-input"
        raw_records = [{
            "path": "tests/raw.gcno",
            "mode": "0644",
            "size": len(raw_data),
            "sha256": hashlib.sha256(raw_data).hexdigest(),
        }]
        raw_manifest = {
            "schema": "hepta.coverage-raw-inputs.v1",
            "version": 1,
            "build_directory": build,
            "file_count": 1,
            "files_sha256": hashlib.sha256(
                common.canonical_json(raw_records)).hexdigest(),
            "files": raw_records,
        }
        (root / names["raw_manifest"]).write_text(
            json.dumps(raw_manifest, sort_keys=True) + "\n",
            encoding="utf-8")
        payload = io.BytesIO()
        with tarfile.open(
                fileobj=payload, mode="w",
                format=tarfile.USTAR_FORMAT) as archive:
            member = tarfile.TarInfo("tests/raw.gcno")
            member.size = len(raw_data)
            member.mode = 0o644
            member.uid = 0
            member.gid = 0
            member.mtime = 0
            archive.addfile(member, io.BytesIO(raw_data))
        (root / names["raw_archive"]).write_bytes(payload.getvalue())
        python = Path(sys.executable).resolve()
        gcov = Path(shutil.which("gcov") or "/usr/bin/gcov").resolve()
        ctest_argv, inventory_argv, coverage_argv = (
            evidence._expected_coverage_commands(
                python.as_posix(), gcov.as_posix(),
                ctest.as_posix(), build, source, root / names["xml"],
                0.70))
        toolchain_identity = {
            "schema": evidence.COVERAGE_TOOLCHAIN_SCHEMA,
            "version": 1,
            "python": evidence._regular_file_attestation(
                python.as_posix(), "coverage"),
            "gcov": evidence._regular_file_attestation(
                gcov.as_posix(), "coverage"),
            "immutable_tool_root_receipt":
                evidence._regular_file_attestation(
                    (root / names["policy"]).as_posix(),
                    "coverage receipt fixture"),
            "immutable_tool_root_claims": {
                "schema": "hepta.coverage-tool-root-receipt.v1",
                "version": 1,
                "tool_root": "/tmp/site-packages",
                "tool_root_file_count": 1,
                "tool_root_tree_sha256": "d" * 64,
                "controlled_runner_image_digest":
                    f"sha256:{'e' * 64}",
            },
            "tool_root_identity": {
                "root": "/tmp/site-packages",
                "file_count": 1,
                "files_sha256": "d" * 64,
            },
            "distributions": [{
                "name": name,
                "version": version,
                "root": "/tmp/site-packages",
                "file_count": 1,
                "files_sha256": "f" * 64,
            } for name, version in
                evidence.COVERAGE_TOOLCHAIN_DISTRIBUTIONS],
        }
        sidecar = {
            "schema": evidence.COVERAGE_SIDECAR_SCHEMA,
            "version": 2,
            "label": "coverage",
            "runner_source_path": evidence.COVERAGE_RUNNER_SOURCE,
            "runner_sha256": hashlib.sha256(
                (REPOSITORY / evidence.COVERAGE_RUNNER_SOURCE).read_bytes()
            ).hexdigest(),
            "helper_source_path": evidence.VERIFICATION_HELPER_SOURCE,
            "helper_sha256": hashlib.sha256(
                (
                    REPOSITORY /
                    evidence.VERIFICATION_HELPER_SOURCE
                ).read_bytes()
            ).hexdigest(),
            "ctest_path": ctest.as_posix(),
            "ctest_sha256": evidence._regular_file_attestation(
                ctest.as_posix(), "coverage")["sha256"],
            "toolchain_identity": toolchain_identity,
            "build_directory": build,
            "source_directory": source,
            "selection": ["-E", evidence.COVERAGE_EXCLUDE],
            "environment": evidence.execution_environment("coverage"),
            "ctest_argv": ctest_argv,
            "inventory_argv": inventory_argv,
            "coverage_argv": coverage_argv,
            "inventory_returncode": 0,
            "ctest_returncode": 0,
            "coverage_returncode": 0,
            "expected_count": 1,
            "minimum_line_rate": 0.70,
            "stdout_path": names["stdout"],
            "inventory_path": names["inventory"],
            "coverage_stdout_path": names["gcovr"],
            "cache_path": names["cache"],
            "coverage_xml_path": names["xml"],
            "strict_source_manifest_path": names["strict"],
            "policy_path": names["policy"],
            "raw_manifest_path": names["raw_manifest"],
            "raw_archive_path": names["raw_archive"],
            "test_attestations": evidence.inventory_attestations(
                inventory_document, "coverage"),
            "source_tree_attestation": {
                "root": "heptatrader-test",
                "file_count": 1,
                "files_sha256": strict["files_sha256"],
                "git_directory_absent": True,
            },
        }
        (root / names["sidecar"]).write_text(
            json.dumps(sidecar, sort_keys=True) + "\n", encoding="utf-8")
        for path in root.iterdir():
            if path != ctest.parent:
                path.chmod(0o600)
        return names["sidecar"]

    def test_ctest_evidence_requires_complete_matrix_and_raw_sidecars(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-verify-") as temporary:
            root = Path(temporary)
            values = [
                self._ctest_lane(root, label, 2)
                for label in sorted(evidence.MATRIX_LABELS)
            ]
            report = evidence.build_ctest(
                "matrix", root, values, "2026-07-25T00:00:00Z")
            self.assertTrue(report["passed"])
            self.assertEqual(len(report["cases"]), 4)
            self.assertEqual(len(report["inputs"]), 18)
            self.assertFalse(report["boundary"]["paper_authorized"])
            with self.assertRaisesRegex(
                    evidence.EvidenceError, "labels are incomplete"):
                evidence.build_ctest(
                    "matrix", root, values[:-1],
                    "2026-07-25T00:00:00Z")

    def test_sanitizer_ctest_uses_strict_source_manifest(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-sanitizer-source-") as temporary:
            root = Path(temporary)
            value = self._ctest_lane(root, "asan", 1)
            sidecar = json.loads(
                (root / value.split("=", 2)[2]).read_text(
                    encoding="utf-8"))
            manifest = json.loads(
                (root / sidecar["source_manifest_path"]).read_text(
                    encoding="utf-8"))
            self.assertEqual(
                manifest["schema"], "hepta.clean-source-bundle.v2")
            self.assertEqual(
                sidecar["source_tree_attestation"]["root"],
                manifest["root"])

    def test_forged_or_failed_ctest_sidecar_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-forged-") as temporary:
            root = Path(temporary)
            value = self._ctest_lane(root, "repository-ibapi-off", 1)
            relative = value.split("=", 2)[2]
            sidecar = root / relative
            document = json.loads(sidecar.read_text(encoding="utf-8"))
            document["returncode"] = 1
            sidecar.write_text(
                json.dumps(document, sort_keys=True) + "\n",
                encoding="utf-8")
            sidecar.chmod(0o600)
            with self.assertRaises(evidence.EvidenceError):
                evidence._ctest_sidecar(
                    root, "repository-ibapi-off", 1,
                    relative)
        with tempfile.TemporaryDirectory(prefix="hepta-forged-") as temporary:
            root = Path(temporary)
            value = self._ctest_lane(root, "repository-ibapi-off", 1)
            relative = value.split("=", 2)[2]
            sidecar = root / relative
            document = json.loads(sidecar.read_text(encoding="utf-8"))
            document["argv"].append("-R")
            sidecar.write_text(
                json.dumps(document, sort_keys=True) + "\n",
                encoding="utf-8")
            sidecar.chmod(0o600)
            with self.assertRaisesRegex(
                    evidence.EvidenceError, "fixed command drift"):
                evidence._ctest_sidecar(
                    root, "repository-ibapi-off", 1, relative)
            document["argv"].pop()
            document["runner_sha256"] = hashlib.sha256(
                b"FORGED_RUNNER").hexdigest()
            sidecar.write_text(
                json.dumps(document, sort_keys=True) + "\n",
                encoding="utf-8")
            sidecar.chmod(0o600)
            with self.assertRaisesRegex(
                    evidence.EvidenceError, "runner source digest drift"):
                evidence._ctest_sidecar(
                    root, "repository-ibapi-off", 1, relative)

    def test_handwritten_coverage_sidecar_is_not_execution_evidence(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-coverage-") as temporary:
            root = Path(temporary)
            sidecar = self._coverage_lane(root)
            with self.assertRaises(evidence.EvidenceError):
                evidence.build_coverage(
                    root, sidecar, 0.70,
                    "2026-07-25T00:00:00Z")

    def test_coverage_toolchain_is_fail_closed_until_provisioned(self) -> None:
        policy = {
            "coverage": {
                "line_minimum_percent": 70,
                "toolchain": self._unprovisioned_coverage_toolchain(),
            },
        }
        self.assertFalse(
            evidence._coverage_toolchain_contract(policy)["provisioned"])
        with self.assertRaisesRegex(
                evidence.EvidenceError, "not provisioned"):
            evidence.coverage_toolchain_identity(policy)

    def test_coverage_command_pins_the_policy_gcov_executable(self) -> None:
        _ctest, _inventory, command = (
            evidence._expected_coverage_commands(
                "/controlled/python", "/controlled/gcov",
                "/controlled/ctest", "/build", "/source",
                Path("/evidence/coverage.xml"), 0.70))
        self.assertEqual(
            command[command.index("--gcov-executable") + 1],
            "/controlled/gcov")
        self.assertNotIn("pip", " ".join(command))

    def test_distribution_identity_includes_native_extensions(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-distribution-") as temporary:
            root = Path(temporary)
            package = root / "fixture"
            package.mkdir(mode=0o700)
            (package / "__init__.py").write_bytes(b"python")
            native = package / "native.so"
            native.write_bytes(b"native-v1")
            for path in package.iterdir():
                path.chmod(0o600)

            class Distribution:
                version = "1.0"
                files = [
                    Path("fixture/__init__.py"),
                    Path("fixture/native.so"),
                ]

                @staticmethod
                def locate_file(entry: object) -> Path:
                    return root / Path(entry)

            with mock.patch.object(
                    evidence.importlib.metadata, "distribution",
                    return_value=Distribution()):
                before = evidence._distribution_identity("fixture")
                native.write_bytes(b"native-v2")
                native.chmod(0o600)
                after = evidence._distribution_identity("fixture")
            self.assertEqual(before["file_count"], 2)
            self.assertNotEqual(
                before["files_sha256"], after["files_sha256"])

    def test_tool_root_identity_rejects_or_binds_extra_files(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-tool-root-") as temporary:
            root = Path(temporary)
            root.chmod(0o700)
            receipt = root / "receipt.json"
            receipt.write_bytes(b"receipt")
            receipt.chmod(0o600)
            payload = root / "tool"
            payload.write_bytes(b"v1")
            payload.chmod(0o600)
            with self.assertRaisesRegex(
                    evidence.EvidenceError,
                    "root|parent chain"):
                evidence._tool_root_identity(root, receipt)
            before = evidence._tool_root_identity(
                root, receipt, require_root_owned=False)
            shadow = root / "sitecustomize.py"
            shadow.write_bytes(b"shadow")
            shadow.chmod(0o600)
            after = evidence._tool_root_identity(
                root, receipt, require_root_owned=False)
            self.assertEqual(before["file_count"], 1)
            self.assertEqual(after["file_count"], 2)
            self.assertNotEqual(
                before["files_sha256"], after["files_sha256"])
            shadow.unlink()
            shadow.symlink_to(payload)
            with self.assertRaisesRegex(
                    evidence.EvidenceError, "unsafe file"):
                evidence._tool_root_identity(
                    root, receipt, require_root_owned=False)

    def test_coverage_requires_real_raw_pairs_and_consistent_cobertura(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-coverage-") as temporary:
            root = Path(temporary)
            source = root / "sample.cpp"
            source.write_text(
                "int main() { return 0; }\n", encoding="utf-8")
            source.chmod(0o600)
            build = root / "build"
            build.mkdir(mode=0o700)
            executable = build / "sample"
            compile_run = subprocess.run(
                [
                    "/usr/bin/c++", "--coverage", source.as_posix(),
                    "-o", executable.as_posix(),
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
                timeout=120,
            )
            self.assertEqual(
                compile_run.returncode, 0,
                compile_run.stdout.decode("utf-8", errors="replace"))
            executable.chmod(0o700)
            test_run = subprocess.run(
                [executable.as_posix()],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
                timeout=30,
            )
            self.assertEqual(test_run.returncode, 0)
            for raw_path in build.glob("*.gc*"):
                raw_path.chmod(0o600)
            manifest, archive = coverage_runner._raw_coverage(build)
            parsed, records = evidence._verify_raw_coverage(
                common.canonical_json(manifest), archive, build)
            self.assertEqual(parsed["file_count"], 2)
            self.assertEqual(
                {Path(record["path"]).suffix for record in records},
                {".gcda", ".gcno"})
            xml = b"""\
<coverage line-rate="0.666667" lines-covered="2" lines-valid="3">
  <packages><package name="fixture"><classes>
    <class name="sample" filename="sample.cpp"><lines>
      <line number="1" hits="1"/>
      <line number="2" hits="0"/>
      <line number="3" hits="2"/>
    </lines></class>
  </classes></package></packages>
</coverage>
"""
            semantics = evidence._coverage_semantics(xml)
            self.assertEqual(semantics["lines_covered"], 2)
            self.assertEqual(semantics["lines_valid"], 3)
            with self.assertRaises(evidence.EvidenceError):
                evidence._coverage_semantics(
                    b'<coverage line-rate="0.999"></coverage>')
            gcda = next(build.glob("*.gcda"))
            gcda.write_bytes(b"forged")
            gcda.chmod(0o600)
            with self.assertRaises(evidence.EvidenceError):
                evidence._verify_raw_coverage(
                    common.canonical_json(manifest), archive, build)

    def test_handwritten_ctest_inventory_cannot_claim_missing_command(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-forged-") as temporary:
            root = Path(temporary)
            value = self._ctest_lane(root, "repository-ibapi-off", 1)
            relative = value.split("=", 2)[2]
            sidecar = root / relative
            document = json.loads(sidecar.read_text(encoding="utf-8"))
            inventory = root / document["inventory_path"]
            inventory_document = json.loads(
                inventory.read_text(encoding="utf-8"))
            inventory_document["tests"][0]["command"] = [
                "/definitely/not/a/real/test"]
            inventory.write_text(
                json.dumps(inventory_document, sort_keys=True) + "\n",
                encoding="utf-8")
            inventory.chmod(0o600)
            document["test_attestations"] = [{
                "name": inventory_document["tests"][0]["name"],
                "command": ["/definitely/not/a/real/test"],
                "working_directory": evidence._working_directory(
                    inventory_document["tests"][0]),
                "files": [{
                    "configured_path": "/definitely/not/a/real/test",
                    "realpath": "/usr/bin/true",
                    "sha256": "a" * 64,
                    "size": 1,
                    "mode": "0755",
                }],
            }]
            sidecar.write_text(
                json.dumps(document, sort_keys=True) + "\n",
                encoding="utf-8")
            sidecar.chmod(0o600)
            with self.assertRaisesRegex(
                    evidence.EvidenceError, "absent"):
                evidence._ctest_sidecar(
                    root, "repository-ibapi-off", 1, relative)

    def test_short_soak_report_is_a_bound_output_not_a_late_input(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-short-soak-output-") as temporary:
            build = Path(temporary)
            report = build / evidence.SHORT_SOAK_REPORT
            runner = build / "soak.py"
            runner.write_text("raise SystemExit(0)\n", encoding="utf-8")
            runner.chmod(0o600)
            document = {
                "kind": "ctestInfo",
                "version": {"major": 1, "minor": 0},
                "tests": [{
                    "name": evidence.SHORT_SOAK_TEST,
                    "command": [
                        "/usr/bin/python3",
                        runner.as_posix(),
                        "--build-dir", build.as_posix(),
                        "--rounds", "2",
                        "--timeout-sec", "15",
                        "--report", report.as_posix(),
                    ],
                    "properties": [{
                        "name": "WORKING_DIRECTORY",
                        "value": build.as_posix(),
                    }],
                }],
            }
            before = evidence.inventory_attestations(document, "no-git")
            report.write_text("first output\n", encoding="utf-8")
            after = evidence.inventory_attestations(document, "no-git")
            report.write_text("changed output\n", encoding="utf-8")
            changed = evidence.inventory_attestations(document, "no-git")
            self.assertEqual(before, after)
            self.assertEqual(after, changed)
            self.assertEqual(before[0]["outputs"], [{
                "option": "--report",
                "configured_path": report.as_posix(),
            }])
            document["tests"][0]["command"][-1] = (
                build / "unreviewed-output.json").as_posix()
            with self.assertRaisesRegex(
                    evidence.EvidenceError, "output contract"):
                evidence.inventory_attestations(document, "no-git")

    def test_stale_pass_stdout_is_rejected_by_live_ctest_replay(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-stale-pass-") as temporary:
            root = Path(temporary)
            value = self._ctest_lane(
                root, "repository-ibapi-off", 1,
                volatile_directory=True)
            volatile = (
                root / "work-repository-ibapi-off" /
                "volatile-condition")
            volatile.rmdir()
            with self.assertRaisesRegex(
                    evidence.EvidenceError, "CTest replay failed"):
                evidence._ctest_sidecar(
                    root, "repository-ibapi-off", 1,
                    value.split("=", 2)[2])

    def test_ctest_runner_rejects_executable_mutation(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-ctest-mutation-") as temporary:
            result = self._ctest_lane(
                Path(temporary), "repository-ibapi-off", 1,
                mutating_ctest=True, expect_success=False)
            self.assertIsNone(result)

    def test_both_evidence_runners_recheck_ctest_identity(self) -> None:
        for relative, failure in (
                (
                    evidence.CTEST_RUNNER_SOURCE,
                    "configured CTest changed during execution",
                ),
                (
                    evidence.COVERAGE_RUNNER_SOURCE,
                    "configured CTest changed during coverage",
                ),
        ):
            with self.subTest(relative=relative):
                source = (REPOSITORY / relative).read_text(
                    encoding="utf-8")
                self.assertIn("ctest_before", source)
                self.assertIn("ctest_after", source)
                self.assertIn(failure, source)

    def test_intermediate_symlink_and_hardlink_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-input-") as temporary:
            root = Path(temporary)
            outside = root.parent / f"{root.name}-outside"
            outside.mkdir(mode=0o700)
            try:
                log = outside / "log"
                log.write_text("data\n", encoding="utf-8")
                log.chmod(0o600)
                (root / "escape").symlink_to(outside, target_is_directory=True)
                with self.assertRaises(evidence.EvidenceError):
                    evidence._binding(root, "bad", "escape/log")
                local = root / "local"
                local.write_text("data\n", encoding="utf-8")
                local.chmod(0o600)
                os.link(local, root / "second-link")
                with self.assertRaises(evidence.EvidenceError):
                    evidence._binding(root, "bad", "local")
            finally:
                log.unlink(missing_ok=True)
                outside.rmdir()

    def test_invalid_timestamp_is_rejected(self) -> None:
        with self.assertRaises(evidence.EvidenceError):
            evidence._base("coverage", "not-a-time")

    def test_runner_binds_fixed_caches_and_no_git_source_heads(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-runner-") as temporary:
            root = Path(temporary)
            caches = []
            release = "0.1.0-beta.1-round38"
            source_root = f"heptatrader-agent-os-{release}"
            no_git_source = root / source_root
            no_git_source.mkdir(mode=0o700)
            no_git_cmake = no_git_source / "CMakeLists.txt"
            no_git_cmake.write_bytes(b"x")
            no_git_cmake.chmod(0o644)
            strict_root = f"heptatrader-{release}"
            strict_source = root / strict_root
            strict_source.mkdir(mode=0o700)
            strict_cmake = strict_source / "CMakeLists.txt"
            strict_cmake.write_bytes(b"x")
            strict_cmake.chmod(0o644)
            for label in sorted(evidence.RUNNER_LABELS):
                path = root / f"{label}.cache"
                if label in evidence.NO_GIT_LABELS:
                    home_path = no_git_source
                    build_path = root / f"build-{label}"
                elif label in evidence.STRICT_SOURCE_LABELS:
                    home_path = strict_source
                    build_path = root / f"build-{label}"
                else:
                    home_path = root / f"source-{label}"
                    home_path.mkdir(mode=0o700)
                    build_path = root / f"build-{label}"
                build_path.mkdir(mode=0o700)
                cache_text = self._cache_text(
                    label, home_path.as_posix(), build_path.as_posix())
                path.write_text(cache_text, encoding="utf-8")
                path.chmod(0o600)
                live_cache = build_path / "CMakeCache.txt"
                live_cache.write_text(cache_text, encoding="utf-8")
                live_cache.chmod(0o600)
                caches.append(f"{label}={path.name}")
            sources = []
            records = [{
                "path": "CMakeLists.txt",
                "mode": "0644",
                "size": 1,
                "sha256": hashlib.sha256(b"x").hexdigest(),
            }]
            files_sha256 = hashlib.sha256(
                common.canonical_json(records)).hexdigest()
            agent_manifest_payload = None
            strict_manifest_payload = None
            for label in sorted(evidence.SOURCE_ATTESTATION_LABELS):
                path = root / f"{label}.source.json"
                manifest = (
                    self._agent_manifest()
                    if label in evidence.NO_GIT_LABELS else
                    self._strict_manifest())
                manifest["files_sha256"] = files_sha256
                payload = (
                    json.dumps(manifest, sort_keys=True) + "\n").encode()
                path.write_bytes(payload)
                path.chmod(0o600)
                if label in evidence.NO_GIT_LABELS:
                    agent_manifest_payload = payload
                else:
                    strict_manifest_payload = payload
                sources.append(f"{label}={path.name}")
            self.assertIsNotNone(agent_manifest_payload)
            self.assertIsNotNone(strict_manifest_payload)
            internal = (
                no_git_source / evidence.AGENT_SOURCE_INTERNAL_MANIFEST)
            internal.parent.mkdir(mode=0o700)
            internal.write_bytes(agent_manifest_payload)
            internal.chmod(0o644)
            strict_internal = (
                strict_source / evidence.STRICT_SOURCE_INTERNAL_MANIFEST)
            strict_internal.parent.mkdir(mode=0o700)
            strict_internal.write_bytes(strict_manifest_payload)
            strict_internal.chmod(0o644)
            old_cxx = os.environ.get("CXX")
            os.environ["CXX"] = "/definitely/not/executed"
            try:
                report = evidence.build_runner(
                    root, caches, "2026-07-25T00:00:00Z", sources)
            finally:
                if old_cxx is None:
                    os.environ.pop("CXX", None)
                else:
                    os.environ["CXX"] = old_cxx
            self.assertEqual(
                {case["name"] for case in report["cases"]},
                evidence.RUNNER_LABELS)
            self.assertTrue(all(
                case["cxx_compiler"]["sha256"]
                for case in report["cases"]))
            by_name = {case["name"]: case for case in report["cases"]}
            self.assertTrue(all(
                by_name[label]["source"]["schema"] ==
                "hepta.agent-os-source-bundle.v1"
                for label in evidence.NO_GIT_LABELS))
            self.assertTrue(all(
                by_name[label]["source"]["schema"] ==
                "hepta.clean-source-bundle.v2"
                for label in evidence.STRICT_SOURCE_LABELS))

    def test_source_and_build_directories_must_be_disjoint(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-source-build-") as temporary:
            source = Path(temporary) / "source"
            source.mkdir()
            build = source / "build"
            build.mkdir()
            with self.assertRaisesRegex(
                    evidence.EvidenceError, "overlap"):
                evidence._require_disjoint_source_build(
                    source, build, "fixture")

    def test_lane_cache_policy_rejects_forged_runner(self) -> None:
        values = evidence._cache_values(
            self._cache_text("repository-ibapi-off").encode(),
            "repository-ibapi-off")
        values["HEPTA_ENABLE_IBAPI"] = "ON"
        with self.assertRaisesRegex(
                evidence.EvidenceError, "IBAPI lane identity drift"):
            evidence._validate_lane_cache(
                "repository-ibapi-off", values)
        values = evidence._cache_values(
            self._cache_text("asan").encode(), "asan")
        values["CMAKE_EXE_LINKER_FLAGS"] = ""
        with self.assertRaisesRegex(
                evidence.EvidenceError, "sanitizer"):
            evidence._validate_lane_cache("asan", values)
        values = evidence._cache_values(
            self._cache_text("coverage").encode(), "coverage")
        values["HEPTA_BUILD_LEGACY_MONOLITH"] = "ON"
        with self.assertRaisesRegex(
                evidence.EvidenceError, "legacy"):
            evidence._validate_lane_cache("coverage", values)

    def test_sparse_no_git_manifest_is_rejected(self) -> None:
        sparse = json.dumps({
            "schema": "hepta.agent-os-source-bundle.v1",
            "paper_authorized": False,
            "live_authorized": False,
            "parent_strict_source": {"git_head": "b" * 40},
        }).encode()
        with self.assertRaisesRegex(
                evidence.EvidenceError, "manifest is invalid"):
            evidence._source_identity(sparse, "forged")

    def test_source_tree_attestation_rejects_git_or_content_drift(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-source-attest-") as temporary:
            root = Path(temporary)
            manifest = self._agent_manifest()
            source = root / str(manifest["root"])
            source.mkdir()
            cmake = source / "CMakeLists.txt"
            cmake.write_bytes(b"x")
            cmake.chmod(0o644)
            payload = (
                json.dumps(manifest, sort_keys=True) + "\n").encode()
            marker = source / evidence.AGENT_SOURCE_INTERNAL_MANIFEST
            marker.parent.mkdir()
            marker.write_bytes(payload)
            marker.chmod(0o644)
            attestation = evidence.attest_source_tree(
                source, payload, "no-git", agent_source=True)
            self.assertTrue(attestation["git_directory_absent"])
            marker.unlink()
            with self.assertRaisesRegex(
                    evidence.EvidenceError, "file closure drift"):
                evidence.attest_source_tree(
                    source, payload, "no-git", agent_source=True)
            marker.write_bytes(payload)
            marker.chmod(0o644)
            marker.write_bytes(payload + b" ")
            with self.assertRaisesRegex(
                    evidence.EvidenceError,
                    "internal source manifest drift"):
                evidence.attest_source_tree(
                    source, payload, "no-git", agent_source=True)
            marker.write_bytes(payload)
            marker.chmod(0o644)
            extra = source / "extra.txt"
            extra.write_bytes(b"extra")
            extra.chmod(0o644)
            with self.assertRaisesRegex(
                    evidence.EvidenceError, "file closure drift"):
                evidence.attest_source_tree(
                    source, payload, "no-git", agent_source=True)
            extra.unlink()
            (source / ".git").mkdir()
            with self.assertRaisesRegex(
                    evidence.EvidenceError, "not no-Git"):
                evidence.attest_source_tree(
                    source, payload, "no-git", agent_source=True)
            (source / ".git").rmdir()
            cmake.write_bytes(b"y")
            cmake.chmod(0o644)
            with self.assertRaisesRegex(
                    evidence.EvidenceError, "source file drift"):
                evidence.attest_source_tree(
                    source, payload, "no-git", agent_source=True)

    def test_strict_source_tree_attestation_binds_internal_manifest(
            self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-strict-source-attest-") as temporary:
            root = Path(temporary)
            manifest = self._strict_manifest()
            source = root / str(manifest["root"])
            source.mkdir()
            cmake = source / "CMakeLists.txt"
            cmake.write_bytes(b"x")
            cmake.chmod(0o644)
            payload = (
                json.dumps(manifest, sort_keys=True) + "\n").encode()
            marker = (
                source / evidence.STRICT_SOURCE_INTERNAL_MANIFEST)
            marker.parent.mkdir()
            marker.write_bytes(payload)
            marker.chmod(0o644)
            attestation = evidence.attest_source_tree(
                source, payload, "strict", agent_source=False)
            self.assertTrue(attestation["git_directory_absent"])
            marker.write_bytes(payload + b" ")
            with self.assertRaisesRegex(
                    evidence.EvidenceError,
                    "internal source manifest drift"):
                evidence.attest_source_tree(
                    source, payload, "strict", agent_source=False)
            marker.write_bytes(payload)
            marker.chmod(0o600)
            with self.assertRaisesRegex(
                    evidence.EvidenceError,
                    "internal source manifest drift"):
                evidence.attest_source_tree(
                    source, payload, "strict", agent_source=False)

    def test_evidence_entrypoints_disable_bytecode_before_local_imports(
            self) -> None:
        entrypoints = (
            evidence.CTEST_RUNNER_SOURCE,
            evidence.COVERAGE_RUNNER_SOURCE,
            evidence.VERIFICATION_HELPER_SOURCE,
        )
        local_modules = {
            "build_heptatrader_delivery_closure",
            "build_heptatrader_verification_evidence",
            "converge_ctp_vendor_headers",
            "run_execution_gateway_soak",
            "verify_heptatrader_clean_source_bundle",
            "verify_heptatrader_prebuilt_assets",
            "verify_heptatrader_vendor_assets",
        }
        for entrypoint in entrypoints:
            with self.subTest(entrypoint=entrypoint):
                with tempfile.TemporaryDirectory(
                        prefix="hepta-no-pyc-") as temporary:
                    cache = Path(temporary) / "cache"
                    environment = dict(os.environ)
                    environment.pop("PYTHONDONTWRITEBYTECODE", None)
                    environment["PYTHONPYCACHEPREFIX"] = cache.as_posix()
                    executed = subprocess.run(
                        [
                            sys.executable,
                            (REPOSITORY / entrypoint).as_posix(),
                            "--help",
                        ],
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        env=environment,
                        timeout=120,
                        check=False,
                    )
                    self.assertEqual(
                        executed.returncode, 0,
                        executed.stdout.decode(
                            "utf-8", errors="replace"))
                    observed = {
                        path.name.split(".", 1)[0]
                        for path in cache.rglob("*.pyc")
                    }
                    self.assertTrue(
                        observed.isdisjoint(local_modules),
                        sorted(observed & local_modules))


if __name__ == "__main__":
    unittest.main(verbosity=2)
