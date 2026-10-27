"""Execute the source workflow command block with inert command dependencies.

No CI job, repository credential, Broker or hosted administration is invoked.
"""
from pathlib import Path
import json
import os
import shutil
import sys
import xml.etree.ElementTree as ET
import subprocess
import tempfile
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[2]
class CanonicalSanitizerWorkflowCommandTests(unittest.TestCase):
    """Execute CI shell blocks with real CTest and deliberately tiny fixtures.

    This verifies automation failure/reporting semantics, NOT the Execution
    Service, sanitizer correctness, or broker qualification. Those run in CI.
    """
    def block(self, step_id: str) -> str:
        path = ROOT / ".github/workflows/canonical-full-suite.yml"
        workflow = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
        steps = workflow["jobs"]["reliability"]["steps"]
        matches = [step["run"] for step in steps if step.get("id") == step_id]
        self.assertEqual(len(matches), 1, step_id)
        return matches[0]

    def run_ctest_block(self, name: str, *, core: bool = True,
                        recovery: bool = True, fail_at: int = 0,
                        fail_core: bool = False):
        # CTest is already installed by the maintained source workflow. Missing
        # tooling must fail rather than silently skip these acceptance tests.
        self.assertIsNotNone(shutil.which("ctest"), "source CI requires CTest")
        with tempfile.TemporaryDirectory(prefix="hepta-ci-ctest-") as folder:
            root = Path(folder)
            build = root / "build" / "fixture"
            build.mkdir(parents=True)
            helper = root / "fixture.py"
            helper.write_text(
                "from pathlib import Path\nimport sys\n"
                "counter = Path(sys.argv[1])\n"
                "n = int(counter.read_text()) + 1 if counter.exists() else 1\n"
                "counter.write_text(str(n))\n"
                "print('automation-fixture-run', n)\n"
                "raise SystemExit(23 if int(sys.argv[2]) == n else 0)\n",
                encoding="utf-8")
            entries = []
            for test, enabled, counter, failure in (
                ("automation_core_fixture", core, "core-count", int(fail_core)),
                ("hepta_research_native_execution_process_crash_tests", recovery,
                 "recovery-count", fail_at),
            ):
                if not enabled:
                    continue
                # Bracket arguments avoid turning local path characters into
                # CMake syntax. Neither helper accepts a command from user data.
                entries.append(
                    f"add_test({test} [==[{sys.executable}]==] "
                    f"[==[{helper}]==] [==[{root / counter}]==] {failure})\n")
                if test == "automation_core_fixture":
                    entries.append(f'set_tests_properties({test} PROPERTIES LABELS "core")\n')
            (build / "CTestTestfile.cmake").write_text("".join(entries), encoding="utf-8")
            result = subprocess.run(
                ["/bin/bash", "--noprofile", "--norc", "-e", "-o", "pipefail",
                 "-c", self.block(name)], cwd=root,
                env=dict(os.environ, BUILD_DIR="build/fixture", BUILD_PARALLEL="2"),
                capture_output=True, text=True, timeout=20)
            counts = {key: int((root / key).read_text()) if (root / key).exists() else 0
                      for key in ("core-count", "recovery-count")}
            outputs = {p.name: p.read_text(encoding="utf-8") for p in build.iterdir()
                       if p.is_file() and p.suffix in (".xml", ".log", ".json")}
            return result, counts, outputs

    def test_core_results_are_actual_and_empty_or_failed_selection_fails(self):
        name = "core_tests"
        result, counts, outputs = self.run_ctest_block(name)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(counts, {"core-count": 1, "recovery-count": 0})
        inventory = json.loads(outputs["test-inventory.json"])
        self.assertEqual(len(inventory["tests"]), 2)
        report = ET.fromstring(outputs["core-results.xml"])
        self.assertEqual(report.attrib["tests"], "1")
        self.assertEqual(report.attrib["failures"], "0")
        self.assertIn("automation_core_fixture", outputs["core-ctest.log"])
        result, counts, outputs = self.run_ctest_block(name, fail_core=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(counts["core-count"], 1)
        self.assertEqual(ET.fromstring(outputs["core-results.xml"]).attrib["failures"], "1")
        result, counts, _ = self.run_ctest_block(name, core=False)
        self.assertNotEqual(result.returncode, 0, "empty core selection must not pass")
        self.assertEqual(counts["recovery-count"], 0)

    def test_recovery_runs_five_times_and_stops_on_failure(self):
        name = "recovery_tests"
        result, counts, outputs = self.run_ctest_block(name)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(counts, {"core-count": 0, "recovery-count": 5})
        self.assertEqual(ET.fromstring(outputs["research-recovery-results.xml"]).attrib["failures"], "0")
        self.assertIn("hepta_research_native_execution_process_crash_tests", outputs["research-recovery-ctest.log"])
        result, counts, outputs = self.run_ctest_block(name, fail_at=3)
        self.assertNotEqual(result.returncode, 0, "third repetition failure must fail CI")
        self.assertEqual(counts["recovery-count"], 3)
        self.assertEqual(ET.fromstring(outputs["research-recovery-results.xml"]).attrib["failures"], "1")
        result, counts, _ = self.run_ctest_block(name, recovery=False)
        self.assertNotEqual(result.returncode, 0, "missing recovery executable must not pass")
        self.assertEqual(counts["core-count"], 0)

    def test_identity_and_cleanliness_blocks_execute_real_git_checks(self):
        with tempfile.TemporaryDirectory(prefix="hepta-ci-identity-") as folder:
            root = Path(folder)
            def git(*args):
                return subprocess.run(["git", *args], cwd=root, check=True,
                                      capture_output=True, text=True, timeout=10).stdout.strip()
            git("init", "-q")
            git("config", "user.name", "CI test fixture")
            git("config", "user.email", "fixture@example.invalid")
            (root / "tracked").write_text("original\n", encoding="utf-8")
            git("add", "tracked")
            git("-c", "commit.gpgsign=false", "commit", "-qm", "local synthetic input")
            sha = git("rev-parse", "HEAD")
            tree = git("rev-parse", "HEAD^{tree}")
            def run(name, expected=sha):
                return subprocess.run(
                    ["/bin/bash", "--noprofile", "--norc", "-e", "-o", "pipefail",
                     "-c", self.block(name)], cwd=root,
                    env=dict(os.environ, BUILD_DIR="build/fixture", EXPECTED_SHA=expected),
                    capture_output=True, text=True, timeout=10)
            identity = "identity"
            clean = "clean_source"
            self.assertNotEqual(run(identity, "0" * 40).returncode, 0)
            self.assertFalse((root / "build/fixture/SOURCE_COMMIT").exists())
            self.assertEqual(run(identity).returncode, 0)
            self.assertEqual((root / "build/fixture/SOURCE_COMMIT").read_text().strip(), sha)
            self.assertEqual((root / "build/fixture/SOURCE_TREE").read_text().strip(), tree)
            self.assertEqual(run(clean).returncode, 0)
            self.assertNotEqual(run(clean, "0" * 40).returncode, 0)
            (root / "tracked").write_text("changed\n", encoding="utf-8")
            self.assertNotEqual(run(clean).returncode, 0, "unstaged source edit must fail")
            git("add", "tracked")
            self.assertNotEqual(run(clean).returncode, 0, "staged source edit must fail")


class FocusedDevelopmentRunnerTests(unittest.TestCase):
    def test_existing_makefiles_build_survives_ninja_becoming_available(self):
        # Exercise real CMake cache reuse and CTest with a tiny build. The
        # fixture Ninja deliberately fails if the entry point switches to it.
        with tempfile.TemporaryDirectory(prefix="hepta-dev-cache-") as directory:
            root = Path(directory)
            (root / "scripts").mkdir()
            entry = root / "scripts/dev_core.sh"
            shutil.copyfile(ROOT / "scripts/dev_core.sh", entry)
            (root / "CMakeLists.txt").write_text(
                "cmake_minimum_required(VERSION 3.16)\n"
                "project(DevelopmentCacheFixture NONE)\ninclude(CTest)\n"
                "add_custom_target(hepta_core_test_binaries)\n"
                "add_test(NAME cached_core COMMAND ${CMAKE_COMMAND} -E true)\n"
                'set_tests_properties(cached_core PROPERTIES LABELS "core")\n',
                encoding="utf-8")
            build = root / "build with spaces"
            configured = subprocess.run(
                ["cmake", "-S", str(root), "-B", str(build), "-G", "Unix Makefiles"],
                capture_output=True, text=True, timeout=20)
            self.assertEqual(configured.returncode, 0, configured.stdout + configured.stderr)
            binary = root / "bin"
            binary.mkdir()
            ninja = binary / "ninja"
            ninja.write_text("#!/bin/sh\necho unexpected-generator-switch >&2\nexit 97\n")
            ninja.chmod(0o755)
            result = subprocess.run(
                ["/bin/bash", "--noprofile", "--norc", str(entry)],
                env=dict(os.environ, PATH=str(binary) + os.pathsep + os.environ["PATH"],
                         HEPTA_BUILD_DIR=str(build), HEPTA_JOBS="1", BASH_ENV="/dev/null"),
                capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            report = ET.parse(build / "core-results.xml").getroot()
            self.assertEqual(report.attrib["tests"], "1")
            self.assertEqual(report.attrib["failures"], "0")

    def invoke(self, *arguments, fail="", jobs="2", tools=("cmake", "ctest")):
        with tempfile.TemporaryDirectory(prefix="hepta-dev-entry-") as directory:
            root = Path(directory)
            binary, build, calls = root / "bin", root / "build with spaces", root / "calls.jsonl"
            binary.mkdir()
            dirname = shutil.which("dirname")
            self.assertIsNotNone(dirname)
            (binary / "dirname").symlink_to(dirname)
            stub = "#!" + sys.executable + "\n" + (
                "import json, os, pathlib, sys\n"
                "name = pathlib.Path(sys.argv[0]).name\n"
                "with open(os.environ['HEPTA_TEST_CALLS'], 'a') as log:\n"
                "    log.write(json.dumps([name] + sys.argv[1:]) + '\\n')\n"
                "phase = ('build' if '--build' in sys.argv else 'configure') if name == 'cmake' else 'test'\n"
                "if phase == os.environ['HEPTA_TEST_FAIL']: sys.exit(37)\n"
                "if phase == 'configure': pathlib.Path(sys.argv[sys.argv.index('-B')+1]).mkdir(parents=True)\n"
            )
            for name in tools:
                executable = binary / name
                executable.write_text(stub, encoding="utf-8")
                executable.chmod(0o755)
            result = subprocess.run(
                ["/bin/bash", "--noprofile", "--norc", str(ROOT / "scripts/dev_core.sh"), *arguments],
                env=dict(os.environ, PATH=str(binary),
                         BASH_ENV="/dev/null", HEPTA_BUILD_DIR=str(build), HEPTA_JOBS=jobs,
                         HEPTA_TEST_CALLS=str(calls), HEPTA_TEST_FAIL=fail),
                capture_output=True, text=True, timeout=10)
            recorded = [json.loads(row) for row in calls.read_text().splitlines()] if calls.exists() else []
            return result, recorded

    def test_default_still_builds_and_executes_complete_core(self):
        result, calls = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("hepta_core_test_binaries", calls[1])
        self.assertEqual(calls[2][calls[2].index("-L") + 1], "core")
        self.assertIn("--no-tests=error", calls[2])
        self.assertTrue(calls[2][calls[2].index("--output-junit") + 1].endswith("/core-results.xml"))

    def test_storage_build_and_test_selection_are_exactly_the_same(self):
        import re
        result, calls = self.invoke("--storage")
        self.assertEqual(result.returncode, 0, result.stderr)
        targets = calls[1][calls[1].index("--target") + 1:calls[1].index("--parallel")]
        self.assertEqual(set(targets), {
            "hepta_session_supervisor_lease_store_migration_tests", "hepta_audit_journal_lifecycle_tests",
            "hepta_oms_journal_durability_tests", "hepta_oms_journal_schema_v4_tests",
            "hepta_recovery_projection_faults"})
        expression = calls[2][calls[2].index("-R") + 1]
        for target in targets:
            self.assertIsNotNone(re.fullmatch(expression, target))
            self.assertIsNone(re.fullmatch(expression, target + "_extra"))
        self.assertIsNone(re.fullmatch(expression, "hepta_unix_tool_server_tests"))
        self.assertIn("--no-tests=error", calls[2])
        self.assertNotIn("-L", calls[2])
        self.assertTrue(calls[2][calls[2].index("--output-junit") + 1].endswith("/storage-results.xml"))
        self.assertTrue(calls[2][calls[2].index("--output-log") + 1].endswith("/storage-ctest.log"))

    def test_no_phase_failure_is_hidden(self):
        for phase, expected_calls in (("configure", 1), ("build", 2), ("test", 3)):
            with self.subTest(phase=phase):
                result, calls = self.invoke("--storage", fail=phase)
                self.assertEqual(result.returncode, 37, result.stderr)
                self.assertEqual(len(calls), expected_calls)

    def test_missing_tool_fails_once_before_configuration(self):
        result, calls = self.invoke("--storage", tools=("cmake",))
        self.assertEqual(result.returncode, 69, result.stderr)
        self.assertIn("ctest", result.stderr)
        self.assertEqual(calls, [])

    def test_invalid_selection_or_parallelism_fails_before_configuration(self):
        for args, jobs in ((("--unknown",), "2"), (("--storage", "extra"), "2"),
                           (("--storage",), "0"), (("--storage",), "-1"),
                           (("--storage",), "2;true")):
            with self.subTest(args=args, jobs=jobs):
                result, calls = self.invoke(*args, jobs=jobs)
                self.assertEqual(result.returncode, 64, result.stderr)
                self.assertEqual(calls, [])

if __name__ == "__main__":
    unittest.main()
