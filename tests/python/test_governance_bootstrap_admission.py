from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "governance-bootstrap-admission.yml"
CONTEXTS = ROOT / ".github" / "required-check-contexts-v1.json"
PRIVILEGED = {
    ROOT / ".github" / "workflows" / "github-governance-qualification.yml":
        "0df855faa0345f81ce350e41f2fe860118b517cd68c0823ff2b4992415e58918",
    ROOT / ".github" / "workflows" / "ib-paper-qualification.yml":
        "b413e5e7cb5ca0a109937d3d54c6820efd1cd42406033306517d201801821a5c",
    ROOT / ".github" / "workflows" / "self-hosted-ib-availability.yml":
        "9efc491558eb1af64930fbfc9d1ae8344214ab7680b9f026221ca0d7ddaabbf0",
}
BOUND_FILES = {
    **PRIVILEGED,
    CONTEXTS: "67c82dac9d08c123133d47d94a8a4872a45b40bab461a2861c658861772d2cb7",
}


def _canonical_block_events(text: str) -> list[str] | None:
    lines = text.splitlines()
    if any("\t" in line for line in lines):
        return None

    positions = [index for index, line in enumerate(lines) if line == "on:"]
    if len(positions) != 1:
        return None
    position = positions[0]

    for index, line in enumerate(lines):
        if index == position or not line or line[0].isspace() or ":" not in line:
            continue
        key = line.split(":", 1)[0].strip()
        if (
            len(key) >= 2
            and key[0] in {"'", '"'}
            and key[-1] == key[0]
        ):
            key = key[1:-1]
        if key == "on":
            return None

    events: list[str] = []
    for line in lines[position + 1:]:
        if line and not line[0].isspace():
            break
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if (
            line.startswith("  ")
            and len(line) > 2
            and line[2] != " "
        ):
            events.append(line)
    return events


class GovernanceBootstrapAdmissionWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = WORKFLOW.read_text(encoding="utf-8")
        cls.targets = {
            path: path.read_text(encoding="utf-8")
            for path in PRIVILEGED
        }

    def test_context_is_explicit_and_reachable_on_pr_and_merge_group(self) -> None:
        self.assertIn("name: Governance Bootstrap Admission", self.workflow)
        self.assertIn("name: governance-bootstrap-admission", self.workflow)
        self.assertIn("  pull_request:\n    branches: [main]", self.workflow)
        self.assertIn("  merge_group:\n    types: [checks_requested]", self.workflow)
        self.assertNotIn("paths:", self.workflow)

    def test_merge_group_runs_are_not_cancelled_by_later_events(self) -> None:
        self.assertIn(
            "cancel-in-progress: ${{ github.event_name == 'pull_request' }}",
            self.workflow,
        )
        self.assertNotIn("cancel-in-progress: true", self.workflow)

    def test_checkout_is_exact_and_credential_free(self) -> None:
        self.assertIn(
            "uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262",
            self.workflow,
        )
        self.assertIn(
            "repository: ${{ github.event.pull_request.head.repo.full_name || github.repository }}",
            self.workflow,
        )
        self.assertIn(
            "ref: ${{ github.event.pull_request.head.sha || github.sha }}",
            self.workflow,
        )
        self.assertIn("persist-credentials: false", self.workflow)
        self.assertIn('test "$(git rev-parse HEAD)" = "$EXPECTED_SHA"', self.workflow)

    def test_job_is_hosted_read_only_and_executes_no_candidate_program(self) -> None:
        self.assertIn("permissions:\n  contents: read", self.workflow)
        self.assertIn("runs-on: ubuntu-24.04", self.workflow)
        self.assertNotIn("runs-on: self-hosted", self.workflow)
        self.assertNotIn("    runs-on:\n      group:", self.workflow)
        self.assertNotIn("secrets.", self.workflow)
        trigger_header = self.workflow.split("\npermissions:", 1)[0]
        self.assertNotIn("pull_request_target", trigger_header)
        self.assertNotIn("repository_dispatch", trigger_header)
        self.assertNotIn("workflow_dispatch", trigger_header)
        self.assertNotIn("curl ", self.workflow)
        self.assertNotIn("gh ", self.workflow)
        self.assertNotIn("python", self.workflow.lower())
        self.assertNotIn("./scripts/", self.workflow)
        self.assertNotIn("tests/python", self.workflow)
        self.assertNotIn("docker", self.workflow.lower())

    def test_all_governed_files_are_exact_digest_bound(self) -> None:
        import re

        embedded = set(
            re.findall(
                r"^\s+[A-Z0-9_]+_SHA256: ([0-9a-f]{64})$",
                self.workflow,
                re.MULTILINE,
            )
        )
        expected = set(BOUND_FILES.values())
        self.assertEqual(embedded, expected)
        for path, expected_digest in BOUND_FILES.items():
            with self.subTest(path=path):
                self.assertEqual(
                    hashlib.sha256(path.read_bytes()).hexdigest(),
                    expected_digest,
                )
                self.assertIn(path.name, self.workflow)

    def test_privileged_workflows_are_dispatch_only(self) -> None:
        self.assertIn('normalized == "on"', self.workflow)
        self.assertIn('index($0, "\\t")', self.workflow)
        self.assertIn('if ($0 != "  workflow_dispatch:")', self.workflow)
        self.assertIn("is not canonical workflow_dispatch-only YAML", self.workflow)

        for path, text in self.targets.items():
            with self.subTest(path=path):
                self.assertEqual(
                    _canonical_block_events(text),
                    ["  workflow_dispatch:"],
                )

        hostile = (
            "on: [pull_request]\n",
            "on: {'pull_request': {}}\n",
            'on: {"workflow_call": {}}\n',
            "on:\n  workflow_dispatch:\n  push:\n",
            "on:\n  workflow_dispatch:\n  'pull_request': {}\n",
            'on:\n  workflow_dispatch:\n  "workflow_call": {}\n',
            "on:\n  workflow_dispatch:\n  <<: *events\n",
            "on:\n  workflow_dispatch:\n'on': [pull_request]\n",
            'on:\n  workflow_dispatch:\n"on": [push]\n',
            "on:\n  workflow_dispatch:\non : [schedule]\n",
            "'on':\n  workflow_dispatch:\n",
            "on:\n\tworkflow_dispatch:\n",
        )
        for text in hostile:
            with self.subTest(text=text):
                self.assertNotEqual(
                    _canonical_block_events(text),
                    ["  workflow_dispatch:"],
                )

    def test_runner_selection_is_group_and_role_bound(self) -> None:
        ib = self.targets[ROOT / ".github" / "workflows" / "ib-paper-qualification.yml"]
        probe = self.targets[
            ROOT / ".github" / "workflows" / "self-hosted-ib-availability.yml"
        ]
        governance = self.targets[
            ROOT / ".github" / "workflows" / "github-governance-qualification.yml"
        ]
        self.assertEqual(ib.count("group: trillionnium-ib-paper"), 2)
        self.assertIn("heptatrader-ib-builder", ib)
        self.assertIn("heptatrader-ib-paper", ib)
        self.assertEqual(probe.count("group: trillionnium-ib-paper"), 1)
        self.assertNotIn("uses: actions/checkout@", probe)
        self.assertNotIn("self-hosted", governance)

    def test_context_registry_projection_is_exact_and_fail_closed(self) -> None:
        document = json.loads(CONTEXTS.read_text(encoding="utf-8"))
        self.assertEqual(document["schema"], "heptatrader.required-check-contexts.v1")
        self.assertEqual(
            document["external_qualification_contexts"],
            [
                "github-governance-exact-artifact-verification",
                "ib-paper-exact-artifact-qualification",
            ],
        )
        observations = document["non_required_observation_contexts"]
        self.assertEqual(observations.count("governance-bootstrap-admission"), 1)
        self.assertNotIn("github-governance-workflow-bootstrap-audit", observations)
        self.assertNotIn("ib-paper-workflow-bootstrap-audit", observations)
        self.assertIn("CONTEXTS_SHA256:", self.workflow)
        self.assertIn(".github/required-check-contexts-v1.json", self.workflow)
        self.assertIn("verify_regular_file \\", self.workflow)

    def test_clean_postflight_is_fail_closed(self) -> None:
        self.assertIn(
            '[[ "$EVENT_REF" == refs/heads/gh-readonly-queue/main/pr-* ]]',
            self.workflow,
        )
        self.assertIn("if: always()", self.workflow)
        self.assertIn("git diff --exit-code -- .", self.workflow)
        self.assertIn("git diff --cached --exit-code -- .", self.workflow)
        clean_status = (
            'test -z "$(git status --porcelain=v1 '
            '--untracked-files=all --ignored=matching)"'
        )
        self.assertGreaterEqual(self.workflow.count(clean_status), 2)


if __name__ == "__main__":
    unittest.main()
