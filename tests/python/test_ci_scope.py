"""Verified tool scope stays bounded; unknown paths restore full CI."""
import subprocess
import unittest
from unittest.mock import patch
from pathlib import Path
import shutil
import sys
import tempfile

from ci_scope import docs_only, diff_paths, scope_for_paths, VERIFIED_PYTHON_GROUPS


class DocsOnlyScope(unittest.TestCase):
    def test_documentation_only(self):
        self.assertTrue(docs_only(["README.md", "docs/technical/a.md", "docs/b.md"]))

    def test_research_readmes_and_dated_reports_are_prose(self):
        self.assertTrue(docs_only([
            "research/README.md", "research/train_v2/README.md",
            "research/train_v2/FX_ORIGINAL_HEALTH_FIRST160_PHYSICAL_20261013.md",
        ]))

    def test_research_contracts_and_configuration_keep_full_feedback(self):
        for path in ("research/MODELS.md", "research/CLIENT_PACKAGE.md",
                     "research/train_v2/contract.md",
                     "research/train_v2/SOURCE_CONTRACT_20261013.md",
                     "research/train_v2/CONFIG_20261013.md",
                     "research/train_v2/plan.json",
                     "research/train_v2/config.yaml",
                     "research/train_v2/source.py"):
            with self.subTest(path=path):
                self.assertFalse(docs_only([path]))
                self.assertEqual(
                    scope_for_paths([path]),
                    {"run_native": True, "run_python": True, "run_learning": True})

    def test_empty_must_not_skip_everything(self):
        self.assertFalse(docs_only([]))

    def test_source_and_docs_always_full(self):
        self.assertFalse(docs_only(["docs/technical/a.md", "scripts/hepta_maintenance_latch.py"]))

    def test_native_and_broker_code_always_full(self):
        self.assertFalse(docs_only(["HeptaTrade/execution/execution_coordinator.cpp"]))

    def test_ci_workflow_edits_always_full(self):
        self.assertFalse(docs_only([".github/workflows/core-ci.yml"]))

    def test_catalog_and_mixed_nonmarkdown_docs_always_full(self):
        self.assertFalse(docs_only(["docs/module-catalog.json"]))
        self.assertFalse(docs_only(["docs/assets/chart.svg"]))
        self.assertFalse(docs_only(["docs/report.md", "docs/contracts.json"]))

    def test_path_traversal_and_uncertain_paths_always_full(self):
        for sample in ["docs/../scripts/a.md", "/docs/report.md", "docs//report.md",
                       "docs\\evil.md", "docs/.hidden/../report.md"]:
            with self.subTest(path=sample):
                self.assertFalse(docs_only([sample]))

    def test_git_diff_checks_both_sides_of_rename(self):
        base = "a" * 40
        with patch("ci_scope.subprocess.check_output", return_value=b"src/a.cpp\0docs/a.md\0") as check:
            names = diff_paths(base)
        self.assertEqual(names, ["src/a.cpp", "docs/a.md"])
        self.assertFalse(docs_only(names))
        self.assertIn("--no-renames", check.call_args.args[0])

    def test_git_failure_runs_full(self):
        with patch("ci_scope.subprocess.check_output",
                   side_effect=subprocess.CalledProcessError(1, ["git"])):
            self.assertIsNone(diff_paths("a" * 40))

    def test_bad_or_zero_base_runs_full(self):
        with patch("ci_scope.subprocess.check_output") as check:
            self.assertIsNone(diff_paths("0" * 39))
        check.assert_not_called()

    def test_nul_separated_nonempty_only(self):
        with patch("ci_scope.subprocess.check_output", return_value=b"docs/a.md\0"):
            self.assertEqual(diff_paths("a" * 40), ["docs/a.md"])
        for bad in [b"", b"docs/a.md", b"\0"]:
            with patch("ci_scope.subprocess.check_output", return_value=bad):
                self.assertFalse(docs_only(diff_paths("a" * 40) or []))


class ComponentScopeTests(unittest.TestCase):
    FULL = {"run_native": True, "run_python": True, "run_learning": True}
    PYTHON = {"run_native": False, "run_python": True, "run_learning": False}

    def setUp(self):
        box = tempfile.TemporaryDirectory()
        self.addCleanup(box.cleanup)
        self.root = Path(box.name)
        for path in frozenset.union(*VERIFIED_PYTHON_GROUPS):
            target = self.root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("pass\n")

    def test_verified_tools_tests_and_docs_use_full_python_only(self):
        for paths in (
            ["scripts/hepta_nav_assurance.py"],
            ["tests/python/test_pnl_cli.py"],
            ["scripts/hepta_maintenance_latch.py", "scripts/hepta_pnl_report.py",
             "tests/python/test_account_observation.py", "docs/technical/change.md"],
        ):
            with self.subTest(paths=paths):
                self.assertEqual(scope_for_paths(paths, self.root), self.PYTHON)
        self.assertEqual(scope_for_paths(["README.md", "docs/technical/a.md"], self.root),
                         {key: False for key in self.FULL})

    def test_unknown_shared_ci_and_invalid_paths_restore_full(self):
        for extra in ("scripts/unknown.py", "scripts/hepta_account_pnl.py",
                      "scripts/heptatrader-user", "scripts/ci_scope.py",
                      "scripts/run_python_tests.py", ".github/workflows/core-ci.yml",
                      "other/hepta_nav_assurance.py", "scripts/hepta_nav_assurance.py.bak",
                      "docs/../scripts/a.md", "requirements.txt"):
            with self.subTest(extra=extra):
                self.assertEqual(scope_for_paths(["scripts/hepta_nav_assurance.py", extra],
                                                 self.root), self.FULL)
        for unknown in (None, [], [""]):
            self.assertEqual(scope_for_paths(unknown, self.root), self.FULL)

    def test_missing_or_symlinked_paired_test_cannot_exempt_a_tool(self):
        test = self.root / "tests/python/test_nav_assurance.py"
        test.unlink()
        self.assertEqual(scope_for_paths(["scripts/hepta_nav_assurance.py"], self.root), self.FULL)
        other = self.root / "retained.py"
        other.write_text("pass\n")
        test.symlink_to(other)
        self.assertEqual(scope_for_paths(["scripts/hepta_nav_assurance.py"], self.root), self.FULL)
        tool = self.root / "scripts/hepta_pnl_report.py"
        tool.unlink()
        self.assertEqual(scope_for_paths(["tests/python/test_pnl_cli.py"], self.root), self.FULL)

    def test_real_git_cli_emits_three_explicit_outputs_and_mixed_fallback(self):
        source = Path(__file__).resolve().parents[2] / "scripts/ci_scope.py"
        script = self.root / "scripts/ci_scope.py"
        shutil.copyfile(source, script)
        def git(*args):
            return subprocess.check_output(
                ["git", "-c", "core.hooksPath=/dev/null", "-c", "user.name=Scope Test",
                 "-c", "user.email=scope-test@example.invalid", *args],
                cwd=self.root, stderr=subprocess.PIPE, text=True, timeout=15).strip()
        def outputs(base):
            result = subprocess.run(
                [sys.executable, "-B", str(script), "--base", base, "--format", "github"],
                cwd=self.root, capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            pairs = [line.split("=", 1) for line in result.stdout.splitlines()]
            self.assertEqual(len(pairs), 3)
            self.assertEqual({key for key, _ in pairs}, set(self.FULL))
            self.assertTrue(all(value in ("true", "false") for _, value in pairs))
            return {key: value == "true" for key, value in pairs}
        git("init", "-q")
        git("add", ".")
        git("commit", "-qm", "initial fixture")
        base = git("rev-parse", "HEAD")
        (self.root / "scripts/hepta_nav_assurance.py").write_text("value = 1\n")
        git("add", ".")
        git("commit", "-qm", "tool change")
        self.assertEqual(outputs(base), self.PYTHON)
        (self.root / "shared.py").write_text("value = 2\n")
        git("add", ".")
        git("commit", "-qm", "mixed unknown")
        self.assertEqual(outputs(base), self.FULL)
        self.assertEqual(outputs("0" * 40), self.FULL)


if __name__ == "__main__":
    unittest.main()


class FrozenPriceLearningScopeTests(unittest.TestCase):
    def test_frozen_inference_and_its_test_require_learning_feedback(self):
        import ci_scope
        for path in ("research/neuron_learning/laya_cell/frozen_price_numpy.py",
                     "tests/python/test_laya_frozen_price_numpy.py"):
            with self.subTest(path=path):
                self.assertTrue(ci_scope.scope_for_paths([path])["run_learning"])
