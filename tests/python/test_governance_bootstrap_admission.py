from __future__ import annotations

import hashlib
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WF = ROOT / ".github" / "workflows"
ADMISSION = WF / "governance-bootstrap-admission.yml"
SOURCE_AUDIT = WF / "qualification-source-audit.yml"
CONTEXTS = ROOT / ".github" / "required-check-contexts-v1.json"
PRIVILEGED = {
    WF / "github-governance-qualification.yml":
        "eb379a0327b3f9c556e24b388f59d7144e2da57f9bc4b51d6c7e49c65ac518af",
    WF / "ib-paper-qualification.yml":
        "709c6d02505e31399b106b48ebeb053d7a221205822008ed5ecbe510b0d9355d",
    WF / "self-hosted-ib-availability.yml":
        "dbe93dc266ff63ed8d8a4cad8a563692c8925d3780521871ad9e8bcb22e4572a",
}
ENGINEERING = {
    WF / "canonical-full-suite.yml",
    WF / "core-ci.yml",
    WF / "documentation-control-plane.yml",
    WF / "merge-candidate.yml",
}
BOUND = {
    **PRIVILEGED,
    CONTEXTS: "6d704de38201632181be5e652c87be06b155ba71ef53ece334e681a16ae8e98b",
    SOURCE_AUDIT: "90066115d950c69aefbd7b46cb45365b48bf8783ae5c49efc5540e7fdadad566",
}
EXPECTED_WORKFLOWS = tuple(
    sorted((*PRIVILEGED, *ENGINEERING, ADMISSION, SOURCE_AUDIT))
)
TOP = {"name", "on", "permissions", "concurrency", "jobs"}
KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*\Z")


def events(text: str) -> list[str] | None:
    lines = text.splitlines()
    if any("\t" in line for line in lines):
        return None
    seen: set[str] = set()
    on_line: int | None = None
    for index, line in enumerate(lines):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line[0].isspace():
            continue
        if ":" not in line:
            return None
        key = line.split(":", 1)[0]
        if KEY.fullmatch(key) is None or key not in TOP or key in seen:
            return None
        seen.add(key)
        if key == "on":
            if line != "on:":
                return None
            on_line = index
    if seen != TOP or on_line is None:
        return None
    found: list[str] = []
    for line in lines[on_line + 1:]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line[0].isspace():
            break
        if not line.startswith("  "):
            return None
        if len(line) > 2 and line[2] != " ":
            found.append(line)
    return found


class GovernanceBootstrapAdmissionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.admission = ADMISSION.read_text(encoding="utf-8")
        cls.source_audit = SOURCE_AUDIT.read_text(encoding="utf-8")
        cls.privileged = {
            path: path.read_text(encoding="utf-8") for path in PRIVILEGED
        }
        cls.engineering = {
            path: path.read_text(encoding="utf-8") for path in ENGINEERING
        }

    def test_admission_is_hosted_read_only_and_data_only(self) -> None:
        self.assertIn("name: governance-bootstrap-admission", self.admission)
        job_header = self.admission.split("jobs:\n", 1)[1].split("\n    steps:", 1)[0]
        self.assertEqual(job_header.count("runs-on: ubuntu-24.04"), 1)
        self.assertIn("permissions:\n  contents: read", self.admission)
        self.assertIn("persist-credentials: false", self.admission)

        trigger_header = self.admission.split("\npermissions:", 1)[0]
        self.assertNotIn("pull_request_target", trigger_header)
        self.assertNotIn("runs-on:\n      group:", self.admission)
        self.assertNotIn("runs-on: self-hosted", self.admission)
        self.assertNotIn("${{ secrets.", self.admission)
        for candidate_execution in (
            "python3 ",
            "./scripts/",
            "tests/python",
            "docker ",
        ):
            self.assertNotIn(candidate_execution, self.admission.lower())

    def test_workflow_set_and_bound_bytes_are_exact(self) -> None:
        actual = tuple(
            sorted(
                path
                for path in WF.rglob("*")
                if path.is_file() or path.is_symlink()
            )
        )
        self.assertEqual(actual, EXPECTED_WORKFLOWS)
        embedded = set(
            re.findall(
                r"^\s+[A-Z0-9_]+_SHA256: ([0-9a-f]{64})$",
                self.admission,
                re.MULTILINE,
            )
        )
        self.assertEqual(embedded, set(BOUND.values()))
        for path, digest in BOUND.items():
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest)
            self.assertIn(path.name, self.admission)

    def test_privileged_workflows_are_canonical_dispatch_only(self) -> None:
        for path, text in self.privileged.items():
            with self.subTest(path=path):
                self.assertEqual(events(text), ["  workflow_dispatch:"])
        for hostile in (
            "on: [pull_request]\n",
            "'on':\n  workflow_dispatch:\n",
            "on:\n workflow_dispatch:\n",
            "on:\n  workflow_dispatch:\n  push:\n",
            "on:\n  workflow_dispatch:\n---\non: [push]\n",
            "defaults: {}\non:\n  workflow_dispatch:\n",
            "name: one\nname: two\non:\n  workflow_dispatch:\n",
        ):
            self.assertNotEqual(events(hostile), ["  workflow_dispatch:"])

    def test_engineering_workflows_are_unprivileged_and_always_reachable(self) -> None:
        required_jobs = {
            "canonical-full-suite.yml": (
                "canonical-full-suite-core",
                "canonical-full-suite-reliability (g++)",
                "canonical-full-suite-reliability (clang++)",
            ),
            "core-ci.yml": ("core-runtime-exact-head",),
            "documentation-control-plane.yml": (
                "documentation-control-plane-exact-head",
            ),
            "merge-candidate.yml": ("exact-merge-candidate",),
        }
        for path, text in self.engineering.items():
            with self.subTest(path=path):
                self.assertIn("permissions:\n  contents: read", text)
                self.assertIn("  pull_request:\n    branches: [main]", text)
                self.assertIn("  push:\n    branches: [main]", text)
                self.assertIn("  merge_group:\n    types: [checks_requested]", text)
                self.assertNotIn("pull_request_target", text)
                self.assertNotIn("secrets.", text)
                self.assertNotIn("self-hosted", text)
                self.assertIn("persist-credentials: false", text)
                for job in required_jobs[path.name]:
                    self.assertIn(f"name: {job}", text)

    def test_source_audit_restores_unprivileged_execution_coverage(self) -> None:
        self.assertIn("name: qualification-source-audit", self.source_audit)
        self.assertIn("runs-on: ubuntu-24.04", self.source_audit)
        self.assertIn("permissions:\n  contents: read", self.source_audit)
        self.assertIn("persist-credentials: false", self.source_audit)
        self.assertIn(
            "partial team-governance extension is not admissible",
            self.source_audit,
        )
        for token in (
            "test_governance_bootstrap_admission.py",
            "test_self_hosted_ib_availability.py",
            "test_qualification_trust_boundary.py",
            "test_bootstrap_postflight_contract.py",
            "test_team_codeowners_activation.py",
        ):
            self.assertIn(token, self.source_audit)
        for forbidden in ("pull_request_target", "self-hosted", "secrets."):
            self.assertNotIn(forbidden, self.source_audit)

    def test_preallocation_gates_selectors_and_context_projection_are_bound(
        self,
    ) -> None:
        governance = self.privileged[WF / "github-governance-qualification.yml"]
        paper = self.privileged[WF / "ib-paper-qualification.yml"]
        probe = self.privileged[WF / "self-hosted-ib-availability.yml"]
        self.assertEqual(
            governance.count(
                "if: github.event_name == 'workflow_dispatch' && github.ref == "
                "'refs/heads/main' && inputs.acknowledge_no_bypass == true"
            ),
            1,
        )
        self.assertEqual(
            paper.count(
                "if: github.event_name == 'workflow_dispatch' && github.ref == "
                "'refs/heads/main' && inputs.mutation_mode == true"
            ),
            2,
        )
        self.assertEqual(
            probe.count(
                "if: github.event_name == 'workflow_dispatch' && github.ref == "
                "'refs/heads/main'"
            ),
            1,
        )
        selector = (
            "runs-on:\n"
            "      group: trillionnium-ib-paper\n"
            "      labels: [self-hosted, linux, x64, x230-ib-paper]"
        )
        self.assertEqual(probe.count(selector), 1)
        self.assertNotIn("heptatrader-ib-builder", probe)
        self.assertNotIn("heptatrader-ib-paper", probe)
        self.assertNotIn("x230-ib-paper", paper)
        self.assertIn('x230_selector="', self.admission)
        document = json.loads(CONTEXTS.read_text(encoding="utf-8"))
        observations = document["non_required_observation_contexts"]
        self.assertEqual(observations.count("governance-bootstrap-admission"), 1)
        self.assertEqual(observations.count("qualification-source-audit"), 1)
        self.assertEqual(
            document["external_qualification_contexts"],
            [
                "github-governance-exact-artifact-verification",
                "ib-paper-exact-artifact-qualification",
            ],
        )

    def test_both_hosted_workflows_have_unconditional_clean_postflight(self) -> None:
        for text in (self.admission, self.source_audit):
            self.assertIn("if: always()", text)
            self.assertIn("git diff --exit-code -- .", text)
            self.assertIn("git diff --cached --exit-code -- .", text)
            self.assertIn("--untracked-files=all --ignored=matching", text)


if __name__ == "__main__":
    unittest.main()
