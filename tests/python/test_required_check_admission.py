from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import ci_change_scope as scope
import verify_required_checks as admission


class RequiredCheckAdmissionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = admission.load_policy(
            ROOT / ".github/required-check-contexts-v1.json"
        )
        self.head = "a" * 40
        self.context = admission.CORE_CONTEXT
        self.owner = admission.EXPECTED_PROVENANCE[self.context]

    def successful_check(self) -> dict:
        return {
            "id": 9,
            "name": self.context,
            "head_sha": self.head,
            "status": "completed",
            "conclusion": "success",
            "app": {"id": 15368},
            "details_url": "https://github.com/o/r/actions/runs/77/job/88",
        }

    def job(self) -> dict:
        return {
            "id": 88,
            "run_id": 77,
            "name": self.context,
            "head_sha": self.head,
            "status": "completed",
            "conclusion": "success",
        }

    def workflow_run(self) -> dict:
        return {
            "id": 77,
            "head_sha": self.head,
            "workflow_id": self.owner["workflow_id"],
            "path": self.owner["workflow_path"],
            "status": "in_progress",
            "conclusion": None,
        }

    def verify(self, rows, job=None, run=None):
        return admission.verify_context(
            self.context,
            rows,
            15368,
            self.policy["required_check_provenance"]["contexts"],
            self.head,
            lambda _: self.job() if job is None else job,
            lambda _: self.workflow_run() if run is None else run,
        )

    def test_repository_policy_and_workflow_are_exact(self) -> None:
        for field in (
            "required_branch_contexts",
            "required_pull_request_contexts",
            "required_merge_group_contexts",
        ):
            self.assertEqual(self.policy[field], [admission.ADMISSION_CONTEXT])
        self.assertEqual(
            self.policy["admission_profiles"], admission.EXPECTED_PROFILES
        )
        self.assertEqual(
            self.policy["required_check_provenance"]["contexts"],
            admission.EXPECTED_PROVENANCE,
        )
        workflow = yaml.load(
            (ROOT / ".github/workflows/documentation-control-plane.yml").read_text(),
            Loader=yaml.BaseLoader,
        )
        self.assertEqual(workflow["permissions"]["actions"], "read")
        self.assertEqual(workflow["permissions"]["checks"], "read")
        for event in ("pull_request", "push", "merge_group"):
            self.assertIn(event, workflow["on"])
            config = workflow["on"][event]
            if isinstance(config, dict):
                self.assertNotIn("paths", config)
                self.assertNotIn("paths-ignore", config)
        job = workflow["jobs"]["admission"]
        self.assertEqual(job["name"], admission.ADMISSION_CONTEXT)
        self.assertEqual(job["needs"], "documentation")
        self.assertEqual(job["if"], "${{ always() }}")
        self.assertEqual(job["timeout-minutes"], "65")
        blocks = [step["run"] for step in job["steps"] if "run" in step]
        self.assertTrue(
            any("scripts/verify_required_checks.py" in block for block in blocks)
        )

    def test_documentation_profile_reuses_canonical_scope_classifier(self) -> None:
        paths = [
            "README.md",
            "docs/index.md",
            "docs/technical/a.md",
            "doc/old.txt",
            "pic/a.png",
        ]
        self.assertTrue(scope.is_documentation_only_pull_request(paths, "pull_request"))
        self.assertEqual(
            admission.profile_for("pull_request", paths),
            "documentation_only_pull_request",
        )
        for event, changed in (
            ("pull_request", []),
            ("push", paths),
            ("merge_group", paths),
            ("pull_request", paths + ["scripts/tool.py"]),
            ("pull_request", ["docs/policy.json"]),
        ):
            with self.subTest(event=event, changed=changed):
                self.assertFalse(
                    scope.is_documentation_only_pull_request(changed, event)
                )
                self.assertEqual(admission.profile_for(event, changed), "full")

    def test_success_requires_exact_check_job_run_and_workflow(self) -> None:
        self.assertEqual(self.verify([self.successful_check()]), admission.SUCCESS)
        hostile_check = self.successful_check()
        hostile_check["head_sha"] = "b" * 40
        with self.assertRaisesRegex(ValueError, "check-run source drift"):
            self.verify([hostile_check])

        for key, value in (
            ("id", None),
            ("run_id", 78),
            ("name", "lookalike"),
            ("head_sha", None),
            ("status", "queued"),
            ("conclusion", "failure"),
        ):
            hostile_job = self.job()
            hostile_job[key] = value
            with self.subTest(job_key=key), self.assertRaisesRegex(
                ValueError, "job identity/source/result drift"
            ):
                self.verify([self.successful_check()], job=hostile_job)

        # Parent workflow completion cannot be required: the source job and this
        # admission job are in the same run, which remains in progress here.
        self.assertEqual(self.verify([self.successful_check()]), admission.SUCCESS)
        for key, value in (
            ("id", 78),
            ("head_sha", "b" * 40),
            ("workflow_id", 1),
            ("path", ".github/workflows/other.yml"),
        ):
            hostile_run = self.workflow_run()
            hostile_run[key] = value
            with self.subTest(run_key=key), self.assertRaisesRegex(
                ValueError, "workflow/source provenance drift"
            ):
                self.verify([self.successful_check()], run=hostile_run)

    def test_missing_queued_spoofed_failed_and_unbound_are_not_success(self) -> None:
        self.assertEqual(self.verify([]), admission.PENDING)
        queued = self.successful_check()
        queued.update(status="queued", conclusion=None)
        self.assertEqual(self.verify([queued]), admission.PENDING)
        spoof = self.successful_check()
        spoof["app"] = {"id": 1}
        self.assertEqual(self.verify([spoof]), admission.PENDING)
        failed = self.successful_check()
        failed["conclusion"] = "failure"
        with self.assertRaisesRegex(ValueError, "completed with"):
            self.verify([failed])
        unbound = self.successful_check()
        unbound["details_url"] = "https://example.invalid/check"
        with self.assertRaisesRegex(ValueError, "no bound Actions job URL"):
            self.verify([unbound])

    def test_latest_correct_actions_check_is_selected(self) -> None:
        old = self.successful_check()
        old["id"] = 1
        old["conclusion"] = "failure"
        new = self.successful_check()
        new["id"] = 2
        spoof = self.successful_check()
        spoof["id"] = 999
        spoof["app"] = {"id": 1}
        self.assertEqual(self.verify([old, new, spoof]), admission.SUCCESS)

    def test_link_parser_and_api_base_are_fail_closed(self) -> None:
        header = (
            '<https://api.github.com/p2>; rel="next", '
            '<https://api.github.com/p4>; rel="last"'
        )
        self.assertEqual(admission.next_link(header), "https://api.github.com/p2")
        self.assertEqual(admission.next_link("garbage"), "")
        api = admission.GitHubApi("https://api.github.com", "fixture-token")
        with self.assertRaisesRegex(ValueError, "escaped the admitted API base"):
            api.get("https://example.invalid/repos/o/r")
        with self.assertRaisesRegex(ValueError, "path must be absolute"):
            api.get("repos/o/r")

    def test_policy_rejects_profile_provenance_and_flag_drift(self) -> None:
        original = ROOT / ".github/required-check-contexts-v1.json"
        value = json.loads(original.read_text())
        mutations = []
        hostile = copy.deepcopy(value)
        hostile["required_branch_contexts"] = [admission.CORE_CONTEXT]
        mutations.append(("ruleset", hostile))
        hostile = copy.deepcopy(value)
        hostile["admission_profiles"]["full"].remove(admission.CLANG_CONTEXT)
        mutations.append(("profile", hostile))
        hostile = copy.deepcopy(value)
        hostile["required_check_provenance"]["contexts"][self.context][
            "workflow_id"
        ] = 1
        mutations.append(("provenance", hostile))
        hostile = copy.deepcopy(value)
        hostile["required_check_provenance"]["contexts"][
            admission.ADMISSION_CONTEXT
        ]["workflow_path"] = ".github/workflows/other.yml"
        mutations.append(("admission-provenance", hostile))
        hostile = copy.deepcopy(value)
        hostile["policy"]["all_pages_must_be_collected"] = False
        mutations.append(("flag", hostile))

        for name, hostile in mutations:
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "policy.json"
                path.write_text(json.dumps(hostile))
                with self.subTest(mutation=name), self.assertRaises(ValueError):
                    admission.load_policy(path)


if __name__ == "__main__":
    unittest.main()
