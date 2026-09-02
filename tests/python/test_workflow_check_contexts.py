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
            },
            "required_pull_request_contexts": ["pr-check"],
            "required_merge_group_contexts": ["merge-check"],
            "external_qualification_contexts": ["external-check"],
            "non_required_observation_contexts": [
                "observation (g++)",
                "observation (clang++)",
            ],
        }
        (github / "required-check-contexts-v1.json").write_text(
            json.dumps(policy), encoding="utf-8"
        )
        (workflows / "pr.yml").write_text(
            """name: pr
on:
  pull_request: {}
permissions:
  contents: read
jobs:
  verify:
    name: pr-check
    runs-on: ubuntu-latest
    steps:
      - run: true
""",
            encoding="utf-8",
        )
        (workflows / "merge.yml").write_text(
            """name: merge
on:
  merge_group: {}
permissions:
  contents: read
jobs:
  verify:
    name: merge-check
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
    name: pr-check
    runs-on: ubuntu-latest
    steps:
      - run: true
""",
                encoding="utf-8",
            )
            errors = check_workflow_check_contexts.validate(root)
            self.assertTrue(
                any("duplicate workflow check context pr-check" in error for error in errors),
                errors,
            )

    def test_missing_explicit_job_name_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflows = self._write_fixture(root)
            (workflows / "pr.yml").write_text(
                """name: pr
on:
  pull_request: {}
permissions:
  contents: read
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
                any("requires exactly one explicit non-empty name" in error for error in errors),
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
                any("dynamic context expression is not matrix-bound" in error for error in errors),
                errors,
            )

    def test_required_context_must_be_reachable_on_declared_event(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflows = self._write_fixture(root)
            (workflows / "pr.yml").write_text(
                """name: pr
on:
  pull_request: {}
  merge_group: {}
permissions:
  contents: read
jobs:
  verify:
    name: pr-check
    if: github.event_name == 'merge_group'
    runs-on: ubuntu-latest
    steps:
      - run: true
""",
                encoding="utf-8",
            )
            errors = check_workflow_check_contexts.validate(root)
            self.assertTrue(
                any(
                    "context is not reachable on pull_request: pr-check" in error
                    for error in errors
                ),
                errors,
            )


if __name__ == "__main__":
    unittest.main()
