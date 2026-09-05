from __future__ import annotations

import subprocess
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]

if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import check_repository_integrity  # noqa: E402


class RepositoryIntegrityTests(unittest.TestCase):
    def test_repository_contracts_are_self_consistent(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "check_repository_integrity.py")],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(
            completed.returncode,
            0,
            msg=completed.stdout + completed.stderr,
        )

    def _workflow_errors(self, name: str, contents: str) -> list[str]:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflows = root / ".github" / "workflows"
            workflows.mkdir(parents=True)
            (workflows / name).write_text(contents, encoding="utf-8")
            return check_repository_integrity.validate_workflows(root)

    def test_workflow_mutation_permissions_and_git_writes_are_rejected(self) -> None:
        errors = self._workflow_errors(
            "mutate.yml",
            """name: mutate
on: workflow_dispatch
permissions:
  contents: write
jobs:
  mutate:
    runs-on: ubuntu-latest
    steps:
      - run: git commit -am 'close gap' && git push origin HEAD
""",
        )
        self.assertTrue(
            any("contents: write" in error for error in errors), errors
        )
        self.assertTrue(any("git commit/push" in error for error in errors), errors)

        inline_errors = self._workflow_errors(
            "inline-write-all.yml",
            """name: inline-write-all
on: workflow_dispatch
permissions: {contents: write-all}
jobs:
  noop:
    runs-on: ubuntu-latest
    steps:
      - run: git diff --check
""",
        )
        self.assertTrue(
            any("contents: write-all" in error for error in inline_errors),
            inline_errors,
        )

        missing = self._workflow_errors(
            "missing-permissions.yml",
            """name: missing-permissions
on: workflow_dispatch
jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - run: git diff --check
""",
        )
        self.assertTrue(
            any("explicit top-level read-only permissions" in error
                for error in missing),
            missing,
        )

        job_only = self._workflow_errors(
            "job-only-permissions.yml",
            """name: job-only-permissions
on: workflow_dispatch
jobs:
  check:
    permissions: {contents: read}
    runs-on: ubuntu-latest
    steps:
      - run: git diff --check
""",
        )
        self.assertTrue(
            any("explicit top-level read-only permissions" in error
                for error in job_only),
            job_only,
        )

        read_all = self._workflow_errors(
            "read-all-permissions.yml",
            """name: read-all-permissions
on: workflow_dispatch
permissions: read-all
jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - run: git diff --check
""",
        )
        self.assertEqual(read_all, [], read_all)

    def test_pr_api_and_self_delete_mutations_are_rejected(self) -> None:
        errors = self._workflow_errors(
            "pr-mutator.yml",
            """name: pr-mutator
on: workflow_dispatch
permissions: {issues: read}
jobs:
  mutate:
    runs-on: ubuntu-latest
    steps:
      - run: gh pr comment 4 --body done
      - run: gh pr create --title done --body done
      - run: gh api --method POST /repos/example/project/issues/4
      - run: rm -f .github/workflows/pr-mutator.yml
""",
        )
        self.assertTrue(any("gh pr mutation" in error for error in errors), errors)
        self.assertTrue(any("gh api mutation" in error for error in errors), errors)
        self.assertTrue(any("workflow self-delete" in error for error in errors), errors)

    def test_alternate_http_and_filesystem_mutations_are_rejected(self) -> None:
        errors = self._workflow_errors(
            "alternate-mutator.yml",
            """name: alternate-mutator
on: workflow_dispatch
permissions: {contents: read}
jobs:
  mutate:
    runs-on: ubuntu-latest
    steps:
      - run: curl -XPOST https://example.test/repos/acme/heptatrader/issues -d '{}'
      - run: gh api -X PATCH /repos/acme/heptatrader/pulls/4
      - run: find .github/workflows -type f -delete
      - run: python3 -c 'from pathlib import Path; Path(".github/workflows/x.yml").unlink()'
""",
        )
        self.assertGreaterEqual(
            sum("HTTP mutation" in error for error in errors), 1, errors
        )
        self.assertTrue(any("gh api mutation" in error for error in errors), errors)
        self.assertGreaterEqual(
            sum("workflow self-delete" in error for error in errors), 2, errors
        )

    def test_workflow_definition_writes_are_rejected_even_with_read_token(self) -> None:
        errors = self._workflow_errors(
            "self-writer.yml",
            """name: self-writer
on: workflow_dispatch
permissions: {contents: read}
jobs:
  mutate:
    runs-on: ubuntu-latest
    steps:
      - run: printf '%s\\n' generated > .github/workflows/generated.yml
      - run: python3 -c 'from pathlib import Path; Path(".github/workflows/x.yml").write_text("x")'
      - run: |
          node - <<'NODE'
          require('fs').writeFileSync('.github/workflows/y.yml', 'x')
          NODE
""",
        )
        self.assertTrue(
            any("workflow file write" in error for error in errors), errors
        )

        read_only = self._workflow_errors(
            "artifact-reader.yml",
            """name: artifact-reader
on: workflow_dispatch
permissions: {contents: read}
jobs:
  inspect:
    runs-on: ubuntu-latest
    steps:
      - run: cp .github/workflows/canonical-full-suite.yml "$RUNNER_TEMP/canonical.yml"
      - run: grep -F workflow .github/workflows/canonical-full-suite.yml
""",
        )
        self.assertEqual(read_only, [], read_only)

    def test_evidence_and_plan_writes_are_rejected(self) -> None:
        errors = self._workflow_errors(
            "plan-writer.yml",
            """name: plan-writer
on: workflow_dispatch
permissions: {contents: read}
jobs:
  close:
    runs-on: ubuntu-latest
    steps:
      - run: echo 'PASS' > docs/development/EXACT-HEAD-RESULTS.md
      - run: sed -i 's/in progress/closed/' docs/development/PLAN.md
""",
        )
        self.assertGreaterEqual(
            sum("evidence/plan mutation" in error for error in errors), 2, errors
        )

    def test_finalizer_name_and_action_are_rejected(self) -> None:
        errors = self._workflow_errors(
            "close-gap-finalizer.yml",
            """name: close gap
on: workflow_dispatch
permissions: {contents: read}
jobs:
  close:
    runs-on: ubuntu-latest
    steps:
      - uses: peter-evans/create-pull-request@v7
      - run: python3 scripts/finalize_remaining_gaps.py
""",
        )
        self.assertTrue(any("finalizer/self-merge" in error for error in errors), errors)
        self.assertTrue(any("mutating action" in error for error in errors), errors)
        self.assertTrue(any("closure/finalizer command" in error for error in errors), errors)

    def test_all_github_write_scopes_and_graphql_mutation_are_rejected(self) -> None:
        errors = self._workflow_errors(
            "api-mutator.yml",
            """name: api-mutator
on: workflow_dispatch
permissions:
  actions: write
  checks: write
  contents: read
jobs:
  mutate:
    runs-on: ubuntu-latest
    steps:
      - uses: pascalgn/automerge-action@v0.16.4
      - uses: actions/github-script@v7
        with:
          script: |
            await github.graphql(`
              mutation { updatePullRequest(input: {pullRequestId: \"x\"}) { clientMutationId } }
            `)
""",
        )
        self.assertGreaterEqual(
            sum("workflow has forbidden mutation:" in error for error in errors),
            2,
            errors,
        )
        self.assertTrue(any("GitHub API mutation" in error for error in errors), errors)
        self.assertTrue(any("mutating action" in error for error in errors), errors)

    def test_comments_and_read_only_commands_are_allowed(self) -> None:
        errors = self._workflow_errors(
            "read-only.yml",
            """name: read-only
on: workflow_dispatch
# A comment may mention git push or finalizer without granting that capability.
permissions:
  contents: read
  pull-requests: read
jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - run: git diff --check && git fetch --depth=1 origin main
      - run: gh pr view 4 --json state
      - run: gh api /repos/example/project
""",
        )
        self.assertEqual(errors, [], errors)

    def test_portfolio_compiler_is_a_pure_policy_boundary(self) -> None:
        portfolio_root = ROOT / "HeptaTrade" / "portfolio"
        self.assertTrue(portfolio_root.is_dir())
        for path in portfolio_root.glob("*.cpp"):
            text = path.read_text(encoding="utf-8-sig")
            self.assertNotRegex(
                text,
                r"#\s*include\s*[<\"](?:\.\./)?(?:execution|adapter|tool_host|client)/",
            )
            self.assertNotRegex(text, r"\b(?:ExecutionAuthority|PlaceOrder|CancelOrder|FlattenPosition)\b")

        # The ordinary Agent target path intentionally remains the narrower
        # single-intent Execution flow.  A future multi-strategy integration
        # must add a reviewed trusted orchestrator rather than quietly making
        # the unprivileged registry own cross-strategy state.
        registry = (ROOT / "HeptaTrade" / "tools" / "trading_tool_registry.cpp").read_text(
            encoding="utf-8-sig"
        )
        self.assertNotIn("portfolio_compiler", registry)


if __name__ == "__main__":
    unittest.main()
