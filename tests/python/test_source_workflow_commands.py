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
WORKFLOW = ROOT / ".github/workflows/documentation-control-plane.yml"
SHELLS = ("build_ib_candidate_artifact.sh", "run_ib_paper_artifact_qualification.sh")

class SourceWorkflowCommandTests(unittest.TestCase):
    def block(self) -> str:
        value = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
        self.assertIsInstance(value, dict)
        candidates = [step["run"] for step in value["jobs"]["documentation"]["steps"]
            if isinstance(step.get("run"), str) and "scripts/check_documentation.py" in step["run"]]
        self.assertEqual(len(candidates), 1)
        return candidates[0]

    def run_block(self, block: str, *, fail_script: str = "", broken_shell: str = ""):
        with tempfile.TemporaryDirectory(prefix="hepta-source-block-") as folder:
            root = Path(folder)
            binary = root / "bin"
            binary.mkdir()
            scripts = root / "scripts"
            scripts.mkdir()
            log = root / "calls"
            for name in SHELLS:
                (scripts / name).write_text("if\n" if name == broken_shell else "#!/bin/bash\nexit 0\n")
            for name, body in {
                "python3": '#!/bin/sh\nprintf "python %s\\n" "$*" >> "$CALLS"\n'
                           '[ "$1" != "$FAIL_SCRIPT" ] || exit 37\nexit 0\n',
                "git": '#!/bin/sh\nprintf "git %s\\n" "$*" >> "$CALLS"\nexit 0\n',
            }.items():
                target = binary / name
                target.write_text(body)
                target.chmod(0o755)
            env = dict(os.environ, PATH=str(binary) + os.pathsep + os.defpath,
                       CALLS=str(log), FAIL_SCRIPT=fail_script)
            result = subprocess.run(["/bin/bash", "--noprofile", "--norc", "-c", block],
                                    cwd=root, env=env, capture_output=True, text=True, timeout=10)
            return result.returncode, log.read_text() if log.exists() else ""

    def assert_controls_execute(self, block: str):
        code, calls = self.run_block(block)
        self.assertEqual(code, 0)
        self.assertIn("git diff --check\n", calls)
        self.assertIn("python scripts/verify_exact_git_index.py --root .\n", calls)
        self.assertIn("python scripts/check_documentation.py\n", calls)
        self.assertIn("python scripts/run_python_tests.py --lane source\n", calls)
        for shell in SHELLS:
            code, _ = self.run_block(block, broken_shell=shell)
            self.assertNotEqual(code, 0, f"syntax failure ignored: {shell}")
        code, calls = self.run_block(block, fail_script="scripts/verify_exact_git_index.py")
        self.assertEqual(code, 37)
        self.assertNotIn("python scripts/check_documentation.py\n", calls)

    def test_real_command_block_preserves_all_moved_controls(self):
        self.assert_controls_execute(self.block())

    def test_comment_or_noop_does_not_count_as_executed_control(self):
        for block in (
            self.block().replace("python3 scripts/verify_exact_git_index.py", "# python3 scripts/verify_exact_git_index.py"),
            "exit 0\n" + "\n".join("# " + line for line in self.block().splitlines()),
        ):
            with self.subTest(block=block), self.assertRaises(AssertionError):
                self.assert_controls_execute(block)

    def test_swallowed_failure_is_detected(self):
        block = self.block().replace("python3 scripts/verify_exact_git_index.py --root .",
                                   "python3 scripts/verify_exact_git_index.py --root . || true")
        with self.assertRaises(AssertionError):
            self.assert_controls_execute(block)


class MonitoringScopeWorkflowTests(unittest.TestCase):
    def block(self) -> str:
        value = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
        steps = value["jobs"]["documentation"]["steps"]
        matches = [step["run"] for step in steps
                   if step.get("name") == "Classify monitoring acceptance scope"]
        self.assertEqual(len(matches), 1)
        return matches[0]

    def run_scope(self, changed_path: str, *, event: str = "pull_request"):
        with tempfile.TemporaryDirectory(prefix="hepta-monitoring-scope-") as folder:
            root = Path(folder)
            def git(*args):
                return subprocess.run(["git", *args], cwd=root, check=True,
                                      capture_output=True, text=True, timeout=10).stdout.strip()
            git("init", "-q")
            git("config", "user.name", "CI test fixture")
            git("config", "user.email", "fixture@example.invalid")
            (root / "base.txt").write_text("base\n", encoding="utf-8")
            git("add", ".")
            git("-c", "commit.gpgsign=false", "commit", "-qm", "base")
            base = git("rev-parse", "HEAD")
            target = root / changed_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("change\n", encoding="utf-8")
            git("add", ".")
            git("-c", "commit.gpgsign=false", "commit", "-qm", "head")
            output = root / "github-output"
            result = subprocess.run(
                ["/bin/bash", "--noprofile", "--norc", "-c", self.block()],
                cwd=root,
                env=dict(os.environ, EVENT_NAME=event,
                         BASE_SHA=base if event == "pull_request" else "",
                         BASE_REPOSITORY="unused/base",
                         SERVER_URL="file:///unused",
                         GITHUB_OUTPUT=str(output)),
                capture_output=True, text=True, timeout=10)
            value = output.read_text(encoding="utf-8").strip() if output.exists() else ""
            return result, value

    def test_docs_only_pr_skips_only_monitoring_process_chain(self):
        for path in ("README.md", "docs/change.md", "doc/change.md", "pic/change.txt"):
            with self.subTest(path=path):
                result, value = self.run_scope(path)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(value, "required=false")

    def test_source_or_workflow_pr_retains_monitoring_acceptance(self):
        for path in ("HeptaTrade/change.cpp", ".github/workflows/change.yml", "scripts/change.py"):
            with self.subTest(path=path):
                result, value = self.run_scope(path)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(value, "required=true")

    def test_push_never_skips_monitoring_acceptance(self):
        result, value = self.run_scope("docs/change.md", event="push")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(value, "required=true")

    def test_fork_checkout_fetches_missing_base_commit(self):
        with tempfile.TemporaryDirectory(prefix="hepta-monitoring-fork-") as folder:
            root = Path(folder)
            server = root / "server"
            server.mkdir()
            base_work = root / "base-work"
            base_work.mkdir()
            def git(cwd: Path, *args: str) -> str:
                return subprocess.run(["git", *args], cwd=cwd, check=True,
                                      capture_output=True, text=True,
                                      timeout=10).stdout.strip()
            git(base_work, "init", "-q")
            git(base_work, "config", "user.name", "CI base fixture")
            git(base_work, "config", "user.email", "base@example.invalid")
            (base_work / "README.md").write_text("base\n", encoding="utf-8")
            git(base_work, "add", ".")
            git(base_work, "-c", "commit.gpgsign=false", "commit", "-qm", "base")
            base = git(base_work, "rev-parse", "HEAD")
            subprocess.run(["git", "clone", "--bare", "-q", str(base_work),
                            str(server / "base.git")], check=True, timeout=10)

            head = root / "head"
            head.mkdir()
            git(head, "init", "-q")
            git(head, "config", "user.name", "CI head fixture")
            git(head, "config", "user.email", "head@example.invalid")
            (head / "README.md").write_text("changed\n", encoding="utf-8")
            git(head, "add", ".")
            git(head, "-c", "commit.gpgsign=false", "commit", "-qm", "head")
            self.assertNotEqual(
                subprocess.run(["git", "cat-file", "-e", f"{base}^{{commit}}"],
                               cwd=head, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL).returncode, 0)
            output = root / "github-output"
            result = subprocess.run(
                ["/bin/bash", "--noprofile", "--norc", "-c", self.block()],
                cwd=head,
                env=dict(os.environ, EVENT_NAME="pull_request",
                         BASE_SHA=base, BASE_REPOSITORY="base",
                         SERVER_URL=server.as_uri(), GITHUB_OUTPUT=str(output)),
                capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(output.read_text(encoding="utf-8").strip(),
                             "required=false")
            git(head, "cat-file", "-e", f"{base}^{{commit}}")

class CanonicalSanitizerWorkflowCommandTests(unittest.TestCase):
    """Execute CI shell blocks with real CTest and deliberately tiny fixtures.

    This verifies automation failure/reporting semantics, NOT the Execution
    Service, sanitizer correctness, or broker qualification. Those run in CI.
    """
    def block(self, name: str) -> str:
        path = ROOT / ".github/workflows/canonical-full-suite.yml"
        workflow = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
        steps = workflow["jobs"]["reliability"]["steps"]
        matches = [step["run"] for step in steps if step.get("name") == name]
        self.assertEqual(len(matches), 1)
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
        name = "Run nonempty core suite and retain machine-readable results"
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
        name = "Repeat real research Execution crash and recovery acceptance"
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
            identity = "Assert and retain exact source identity"
            clean = "Reassert clean exact source"
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

if __name__ == "__main__":
    unittest.main()
