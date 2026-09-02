from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]

if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import check_workflow_check_contexts  # noqa: E402


class WorkflowCheckContextTests(unittest.TestCase):
    def _write_fixture(self, root: Path) -> Path:
        github = root / ".github"
        workflows = github / "workflows"
        workflows.mkdir(parents=True)
        policy = {
            "schema": "heptatrader.required-check-contexts.v1",
            "policy": {
                "explicit_job_names": True,
                "globally_unique_contexts": True,
                "dynamic_context_expressions": "matrix-only",
                "required_contexts_must_be_event_reachable": True,
                "skipped_or_missing_is_not_success": True,
                "merge_group_cancel_in_progress": "forbidden",
            },
            "required_branch_contexts": ["branch-check"],
            "external_qualification_contexts": ["external-check"],
            "non_required_observation_contexts": [
                "observation (g++)",
                "observation (clang++)",
            ],
        }
        (github / "required-check-contexts-v1.json").write_text(
            json.dumps(policy), encoding="utf-8"
        )
        (workflows / "branch.yml").write_text(
            """name: branch
on:
  pull_request: {}
  merge_group:
    types: [checks_requested]
permissions:
  contents: read
concurrency:
  group: branch-${{ github.ref }}
  cancel-in-progress: ${{ github.event_name == 'pull_request' }}
jobs:
  verify:
    name: branch-check
    runs-on: ubuntu-latest
    steps:
      - run: true
""",
            encoding="utf-8",
        )
        (workflows / "external.yml").write_text(
            """name: external
on:
  workflow_dispatch: {}
permissions:
  contents: read
jobs:
  qualify:
    name: external-check
    runs-on: ubuntu-latest
    steps:
      - run: true
""",
            encoding="utf-8",
        )
        (workflows / "observation.yml").write_text(
            """name: observation
on:
  schedule:
    - cron: '0 0 * * *'
permissions:
  contents: read
jobs:
  observe:
    name: observation (${{ matrix.compiler }})
    strategy:
      matrix:
        compiler: [g++, clang++]
    runs-on: ubuntu-latest
    steps:
      - run: true
""",
            encoding="utf-8",
        )
        return workflows

    def test_repository_contexts_are_unique_registered_and_event_bound(self) -> None:
        self.assertEqual(check_workflow_check_contexts.validate(ROOT), [])

    def test_valid_fixture_passes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_fixture(root)
            self.assertEqual(check_workflow_check_contexts.validate(root), [])

    def test_duplicate_context_is_rejected_across_workflows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflows = self._write_fixture(root)
            (workflows / "duplicate.yml").write_text(
                """name: duplicate
on:
  workflow_dispatch: {}
permissions:
  contents: read
jobs:
  verify:
    name: branch-check
    runs-on: ubuntu-latest
    steps:
      - run: true
""",
                encoding="utf-8",
            )
            errors = check_workflow_check_contexts.validate(root)
            self.assertTrue(
                any(
                    "duplicate workflow check context branch-check" in error
                    for error in errors
                ),
                errors,
            )

    def test_missing_explicit_job_name_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflows = self._write_fixture(root)
            (workflows / "branch.yml").write_text(
                """name: branch
on:
  pull_request: {}
  merge_group: {}
permissions:
  contents: read
concurrency:
  cancel-in-progress: false
jobs:
  verify:
    runs-on: ubuntu-latest
    steps:
      - run: true
""",
                encoding="utf-8",
            )
            errors = check_workflow_check_contexts.validate(root)
            self.assertTrue(
                any(
                    "requires exactly one explicit non-empty name" in error
                    for error in errors
                ),
                errors,
            )

    def test_non_matrix_dynamic_context_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflows = self._write_fixture(root)
            (workflows / "external.yml").write_text(
                """name: external
on:
  workflow_dispatch: {}
permissions:
  contents: read
jobs:
  qualify:
    name: external-${{ github.ref }}
    runs-on: ubuntu-latest
    steps:
      - run: true
""",
                encoding="utf-8",
            )
            errors = check_workflow_check_contexts.validate(root)
            self.assertTrue(
                any(
                    "dynamic context expression is not matrix-bound" in error
                    for error in errors
                ),
                errors,
            )

    def test_required_context_must_run_on_merge_group(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflows = self._write_fixture(root)
            (workflows / "branch.yml").write_text(
                """name: branch
on:
  pull_request: {}
permissions:
  contents: read
concurrency:
  cancel-in-progress: false
jobs:
  verify:
    name: branch-check
    runs-on: ubuntu-latest
    steps:
      - run: true
""",
                encoding="utf-8",
            )
            errors = check_workflow_check_contexts.validate(root)
            self.assertIn(
                "required_branch_contexts: context is not reachable on "
                "merge_group: branch-check",
                errors,
            )

    def test_required_job_level_event_filter_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflows = self._write_fixture(root)
            (workflows / "branch.yml").write_text(
                """name: branch
on:
  pull_request: {}
  merge_group: {}
permissions:
  contents: read
concurrency:
  cancel-in-progress: false
jobs:
  verify:
    name: branch-check
    if: github.event_name == 'merge_group'
    runs-on: ubuntu-latest
    steps:
      - run: true
""",
                encoding="utf-8",
            )
            errors = check_workflow_check_contexts.validate(root)
            self.assertIn(
                "required_branch_contexts: context is not reachable on "
                "pull_request: branch-check",
                errors,
            )

    def test_required_merge_group_run_cannot_be_cancelled(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflows = self._write_fixture(root)
            path = workflows / "branch.yml"
            path.write_text(
                path.read_text(encoding="utf-8").replace(
                    "cancel-in-progress: ${{ github.event_name == 'pull_request' }}",
                    "cancel-in-progress: true",
                ),
                encoding="utf-8",
            )
            errors = check_workflow_check_contexts.validate(root)
            self.assertTrue(
                any(
                    "must not cancel an in-progress merge-group run" in error
                    for error in errors
                ),
                errors,
            )


if __name__ == "__main__":
    unittest.main()
