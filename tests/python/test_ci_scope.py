"""Conservative changed-path CI scope: uncertain and source changes run full."""
import subprocess
import unittest
from unittest.mock import patch

from ci_scope import docs_only, diff_paths


class DocsOnlyScope(unittest.TestCase):
    def test_documentation_only(self):
        self.assertTrue(docs_only(["README.md", "docs/technical/a.md", "docs/b.md"]))

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


if __name__ == "__main__":
    unittest.main()
