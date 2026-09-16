"""Execute the unchanged install module with inert payloads, not source tokens.

These fixtures prove CMake install semantics only. The existing opt-in
registered_core_install test still verifies the actual production build.
"""
from __future__ import annotations
from contextlib import contextmanager
from pathlib import Path
import os
import importlib.util
import shutil
import stat
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
HELPERS = (
    "scripts/hepta_agent_mcp_launcher.py", "scripts/hepta_agent_trust_domain.py",
    "scripts/hepta_broker_egress_policy.py", "scripts/resolve_hepta_config.py",
    "scripts/run_release_simulator_smoke.py",
    "scripts/verify_canonical_ib_paper_profile.py", "scripts/verify_oms_journal_replay.py",
    "adapters/mcp/hepta_mcp_server.py", "scripts/hepta_oms_report.py",
    "scripts/hepta_ib_runtime_report.py", "scripts/hepta_oms_archive.py",
    "scripts/hepta_oms_checkpoint.py", "scripts/hepta_oms_lifecycle.py",
    "scripts/oms_archive_codec.py", "scripts/hepta_telemetry_collect.py",
)


def validate_fixture_helpers(source_root: Path) -> None:
    helpers = (*HELPERS, "scripts/hepta_preflight.py", "scripts/hepta_preflight_core.py")
    missing = [helper for helper in helpers if not (source_root / helper).is_file()]
    if missing:
        raise AssertionError("Install fixture references missing production helpers: " + ", ".join(missing))


@contextmanager
def installed_fixture(*, readme: bool = False):
    validate_fixture_helpers(ROOT)
    with tempfile.TemporaryDirectory(prefix="hepta-cmake-install-") as folder:
        root = Path(folder)
        source, build, stage = root / "source", root / "build", root / "stage"
        def write(relative: str, value: str):
            path = source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(value, encoding="utf-8")
        write("cmake/heptatrader-build-info.json.in", '{"fixture_only":true}\n')
        shutil.copyfile(ROOT / "cmake/HeptaInstall.cmake", source / "cmake/HeptaInstall.cmake")
        shutil.copyfile(ROOT / "cmake/render_installed_documentation.py", source / "cmake/render_installed_documentation.py")
        write("scripts/hepta_preflight.py", "# public fixture, never run as preflight\n")
        write("scripts/hepta_preflight_core.py", "# private fixture implementation\n")
        for helper in HELPERS:
            write(helper, "# inert packaging fixture\n")
        for name in ("capabilities.json", "ib-paper-profile-policy-v1.json", "preflight-policy-v1.json"):
            write("docs/" + name, '{"fixture_only":true}\n')
        write("docs/developer.md", "# installed fixture documentation\n")
        write("tmpfiles.d/heptatrader-agent-os.conf", "# inert\n")
        for unit in ("hepta-execution.service", "hepta-execution-ib-paper.service", "hepta-broker-egress-policy.service"):
            write("systemd/" + unit, "[Service]\nExecStart=/bin/false\n")
        write("systemd/monitoring/hepta-telemetry@.service.example", "# inert observer example\n")
        if readme:
            write("README.md", "# optional fixture README\n[Guide](docs/developer.md)\n[Build source](CMakeLists.txt)\n")
        write("fixture.c", '#include <stdio.h>\nint main(void) { puts("install-fixture-only"); return 0; }\n')
        write("CMakeLists.txt", """cmake_minimum_required(VERSION 3.16)
project(InstallFixture VERSION 0.1.0 LANGUAGES C)
set(HEPTA_ENABLE_IBAPI OFF)
foreach(name heptactl hepta_sessionctl hepta_tool_gatewayd hepta_executiond hepta_agent_simulator_e2e_tests)
  add_executable(${name} fixture.c)
endforeach()
include(cmake/HeptaInstall.cmake)
""")
        environment = dict(os.environ, DESTDIR=str(stage))
        for command in (
            ["cmake", "-S", str(source), "-B", str(build), "-DCMAKE_INSTALL_PREFIX=/usr",
                "-DCMAKE_INSTALL_LIBDIR=lib", "-DCMAKE_INSTALL_LIBEXECDIR=libexec",
                "-DHEPTA_DOCUMENTATION_SOURCE_SHA=" + "a" * 40],
            ["cmake", "--build", str(build), "--parallel", "2"],
            ["cmake", "--install", str(build)],
        ):
            result = subprocess.run(command, cwd=source, env=environment, capture_output=True,
                                    text=True, timeout=60)
            if result.returncode:
                raise AssertionError(f"{command!r}\n{result.stdout}\n{result.stderr}")
        yield stage / "usr"

class CMakeInstallBehaviorTests(unittest.TestCase):
    def test_fixture_helpers_are_existing_production_sources(self) -> None:
        validate_fixture_helpers(ROOT)

    def test_fixture_rejects_missing_production_helper(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder)
            helpers = (*HELPERS, "scripts/hepta_preflight.py", "scripts/hepta_preflight_core.py")
            for helper in helpers:
                path = source / helper
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()
            validate_fixture_helpers(source)
            missing = source / HELPERS[0]
            missing.unlink()
            with self.assertRaisesRegex(AssertionError, HELPERS[0]):
                validate_fixture_helpers(source)

    def test_readme_optional_and_documentation_actually_installed(self) -> None:
        for present in (False, True):
            with self.subTest(readme=present), installed_fixture(readme=present) as tree:
                docs = tree / "share/doc/heptatrader"
                self.assertEqual((docs / "developer.md").read_text(), "# installed fixture documentation\n")
                self.assertEqual((docs / "README.md").exists(), present)

    def test_private_namespace_and_mode_from_real_install(self) -> None:
        with installed_fixture() as tree:
            private = tree / "libexec/heptatrader/hepta-preflight-core.py"
            self.assertEqual(private.read_text(), "# private fixture implementation\n")
            self.assertEqual(stat.S_IMODE(private.stat().st_mode), 0o644)
            self.assertFalse((tree / "bin/hepta-preflight-core.py").exists())
            self.assertTrue(os.access(tree / "bin/hepta-preflight", os.X_OK))
            self.assertFalse((tree / "lib/systemd/system/hepta-execution-ib-paper.service").exists())
            self.assertFalse((tree / "lib/systemd/system/hepta-broker-egress-policy.service").exists())

    def test_collectors_and_oms_generation_helper_are_executable_but_observer_unit_remains_inert(self):
        with installed_fixture() as tree:
            self.assertTrue(os.access(tree / "libexec/heptatrader/hepta_telemetry_collect.py", os.X_OK))
            self.assertTrue(os.access(tree / "libexec/heptatrader/hepta_ib_runtime_report.py", os.X_OK))
            self.assertTrue(os.access(tree / "libexec/heptatrader/hepta_oms_checkpoint.py", os.X_OK))
            self.assertTrue(os.access(tree / "libexec/heptatrader/hepta_oms_lifecycle.py", os.X_OK))
            self.assertTrue((tree / "share/heptatrader/examples/systemd/monitoring/hepta-telemetry@.service.example").is_file())
            self.assertFalse((tree / "lib/systemd/system/hepta-telemetry@.service").exists())

    def test_smoke_target_is_installed_and_executable(self) -> None:
        with installed_fixture() as tree:
            binary = tree / "libexec/heptatrader/hepta_agent_simulator_e2e_tests"
            completed = subprocess.run([str(binary)], capture_output=True, text=True, timeout=5, check=True)
            self.assertEqual(completed.stdout, "install-fixture-only\n")

    def test_actual_installed_links_are_relocatable_and_source_links_are_pinned(self):
        spec = importlib.util.spec_from_file_location("installed_docs", ROOT / "cmake/render_installed_documentation.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with installed_fixture(readme=True) as tree:
            docs = tree / "share/doc/heptatrader"
            self.assertEqual(module.validate_installed_links(docs), [])
            readme = (docs / "README.md").read_text()
            self.assertIn("[Guide](developer.md)", readme)
            self.assertIn("/blob/" + "a" * 40 + "/CMakeLists.txt", readme)
            (docs / "developer.md").unlink()
            self.assertTrue(module.validate_installed_links(docs))

    def test_render_actual_repository_without_mutating_source(self):
        spec = importlib.util.spec_from_file_location("installed_docs", ROOT / "cmake/render_installed_documentation.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        original = (ROOT / "README.md").read_bytes()
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "docs"
            module.render(ROOT, output, "b" * 40)
            self.assertEqual(module.validate_installed_links(output), [])
            self.assertIn("/blob/" + "b" * 40 + "/.github/workflows/release.yml", (output / "index.md").read_text())
            self.assertEqual((ROOT / "README.md").read_bytes(), original)
            self.assertFalse((output / ".github").exists())
            self.assertFalse((output / "doc").exists())

    def test_renderer_rejects_broken_source_and_invalid_identity(self):
        spec = importlib.util.spec_from_file_location("installed_docs", ROOT / "cmake/render_installed_documentation.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "source"
            (source / "docs").mkdir(parents=True)
            (source / "docs/index.md").write_text("[Missing](missing.md)\n")
            for identity in ("a" * 40, "main", "a" * 39):
                with self.subTest(identity=identity), self.assertRaises(ValueError):
                    module.render(source, Path(folder) / "output", identity)
            self.assertFalse((Path(folder) / "output").exists())

if __name__ == "__main__":
    unittest.main()
