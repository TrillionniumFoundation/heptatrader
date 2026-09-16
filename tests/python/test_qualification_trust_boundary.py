from __future__ import annotations

import copy
from pathlib import Path
import sys
import tempfile
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import check_qualification_trust_boundary as boundary


class QualificationTrustBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workflow = boundary.load_workflow(ROOT / boundary.WORKFLOW)

    def phase(self, identity: str) -> dict:
        return next(s for s in self.workflow["jobs"]["qualify"]["steps"] if s.get("id") == identity)

    def assert_rejected(self) -> None:
        self.assertTrue(boundary.validate_workflow(self.workflow))

    def test_repository_boundary_passes(self) -> None:
        self.assertEqual(boundary.validate(ROOT), [])
        self.assertIn("on", self.workflow)
        self.assertNotIn(True, self.workflow)

    def test_human_names_and_comments_are_not_security_contracts(self) -> None:
        self.workflow["name"] = "CODEOWNERS candidate/scripts/ ordinary human label"
        for job in self.workflow["jobs"].values():
            job["name"] = "renamed"
            for index, step in enumerate(job["steps"]):
                step["name"] = f"phase {index}"
                if "run" in step:
                    step["run"] = "# innocuous explanatory comment\n" + step["run"]
        self.assertEqual(boundary.validate_workflow(self.workflow), [])

    def test_yaml_reformatting_does_not_change_admission(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "workflow.yml"
            path.write_text(yaml.safe_dump(self.workflow, sort_keys=False, width=60))
            self.assertEqual(boundary.validate_workflow(boundary.load_workflow(path)), [])

    def test_conjunction_order_parentheses_and_comparison_order_are_immaterial(self) -> None:
        for job in self.workflow["jobs"].values():
            terms = job["if"].split(" && ")
            job["if"] = "${{ (" + ") && (".join(reversed(terms)) + ") }}"
        self.assertEqual(boundary.validate_workflow(self.workflow), [])
        self.assertEqual(boundary.admission_terms("'owner' == github.actor"),
                         boundary.admission_terms("github.actor == 'owner'"))

    def test_every_critical_phase_rejects_a_false_condition(self) -> None:
        for job in self.workflow["jobs"].values():
            for step in job["steps"]:
                if "run" not in step:
                    continue
                with self.subTest(phase=step.get("id")):
                    step["if"] = "${{ false }}"
                    self.assert_rejected()
                    del step["if"]

    def test_no_continue_on_error_on_verification(self) -> None:
        self.phase("verify-qualification")["continue-on-error"] = True
        self.assert_rejected()

    def test_echo_is_not_execution(self) -> None:
        step = self.phase("verify-qualification")
        step["run"] = "echo " + step["run"]
        self.assert_rejected()

    def test_conditionally_hidden_command_is_not_execution(self) -> None:
        step = self.phase("verify-qualification")
        step["run"] = "if false; then\n" + step["run"] + "\nfi"
        self.assert_rejected()

    def test_error_swallowing_and_background_work_are_rejected(self) -> None:
        step = self.phase("verify-qualification")
        original = step["run"]
        for suffix in (" || true", " &", "; exit 0"):
            with self.subTest(suffix=suffix):
                step["run"] = original + suffix
                self.assert_rejected()
        step["run"] = original

    def test_commented_out_command_is_not_execution(self) -> None:
        self.phase("verify-qualification")["run"] = "# python3 trusted/scripts/verify_ib_paper_qualification.py\necho no-op"
        self.assert_rejected()

    def test_missing_owner_gate_and_or_bypass_are_rejected(self) -> None:
        job = self.workflow["jobs"]["qualify"]
        original = job["if"]
        for changed in (original.replace("github.actor_id == 102159240 && ", ""),
                        original + " || true", original.replace("github.actor == 'ProfHepta'", "github.actor == 'someone-else'")):
            with self.subTest(changed=changed):
                job["if"] = changed
                self.assert_rejected()
        job["if"] = original

    def test_checkout_cannot_use_candidate_selected_control_code(self) -> None:
        job = self.workflow["jobs"]["qualify"]
        next(s for s in job["steps"] if s.get("uses", "").startswith("actions/checkout@"))["with"]["ref"] = "${{ inputs.candidate_sha }}"
        self.assert_rejected()

    def test_broker_environment_may_not_be_removed(self) -> None:
        del self.workflow["jobs"]["qualify"]["environment"]
        self.assert_rejected()

    def test_raw_directory_upload_is_rejected(self) -> None:
        self.phase("publish-evidence")["with"]["path"] = "${{ runner.temp }}/heptatrader-ib-evidence-${{ github.run_id }}-${{ github.run_attempt }}/"
        self.assert_rejected()

    def test_wrong_order_is_rejected(self) -> None:
        steps = self.workflow["jobs"]["qualify"]["steps"]
        final = steps.index(self.phase("verify-qualification"))
        campaign = steps.index(self.phase("run-campaign"))
        steps[final], steps[campaign] = steps[campaign], steps[final]
        self.assert_rejected()

    def test_attestation_may_not_be_skipped(self) -> None:
        self.phase("attest-ib-paper-receipt")["if"] = "${{ false }}"
        self.assert_rejected()

    def test_builder_may_not_gain_write_credentials(self) -> None:
        self.workflow["jobs"]["build-candidate"]["permissions"] = "write-all"
        self.assert_rejected()

    def test_critical_path_may_not_override_interpreter_path(self) -> None:
        self.phase("verify-qualification").setdefault("env", {})["PATH"] = "/untrusted"
        self.assert_rejected()

    def test_mutable_action_revision_is_rejected(self) -> None:
        self.phase("publish-evidence")["uses"] = "actions/upload-artifact@main"
        self.assert_rejected()

    def test_duplicate_yaml_keys_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "workflow.yml"
            path.write_text("jobs:\n  qualify: {}\n  qualify: {}\n")
            with self.assertRaises(boundary.BoundaryError):
                boundary.load_workflow(path)

    def test_shell_flag_order_and_quote_style_are_immaterial(self) -> None:
        step = self.phase("verify-source-after-campaign")
        step["run"] = "python3 'trusted/scripts/verify_exact_git_index.py' --root 'trusted'\n"
        self.assertEqual(boundary.validate_workflow(self.workflow), [])


    def test_exec_cannot_short_circuit_a_multicommand_phase(self):
        workflow = boundary.load_workflow(ROOT / boundary.WORKFLOW)
        phase = next(s for s in workflow["jobs"]["build-candidate"]["steps"] if s.get("id") == "verify-source-before-build")
        phase["run"] = phase["run"].replace("python3 trusted/scripts/verify_exact_git_index.py --root trusted", "exec python3 trusted/scripts/verify_exact_git_index.py --root trusted")
        self.assertTrue(boundary.validate_workflow(workflow))

    def test_additional_upload_or_unreviewed_action_is_rejected(self):
        for action in ("actions/upload-artifact", "example/arbitrary-action"):
            workflow = boundary.load_workflow(ROOT / boundary.WORKFLOW)
            workflow["jobs"]["qualify"]["steps"].append({"id":"extra", "uses":action+"@"+"a"*40, "with":{"path":"/private"}})
            self.assertTrue(boundary.validate_workflow(workflow))

    def test_download_and_publication_identity_must_match(self):
        for job, phase_id, key in (("qualify","download-candidate","path"), ("build-candidate","upload-candidate","name")):
            workflow = boundary.load_workflow(ROOT / boundary.WORKFLOW)
            phase = next(s for s in workflow["jobs"][job]["steps"] if s.get("id") == phase_id)
            phase["with"][key] = "other"
            self.assertTrue(boundary.validate_workflow(workflow))

    def test_runner_paths_cannot_return_to_job_environment(self):
        for job_name in ("build-candidate", "qualify"):
            with self.subTest(job=job_name):
                workflow = boundary.load_workflow(ROOT / boundary.WORKFLOW)
                workflow["jobs"][job_name]["env"]["CANDIDATE_ARCHIVE"] = "${{ runner.temp }}/candidate.tar"
                self.assertTrue(boundary.validate_workflow(workflow))

    def test_each_path_consuming_step_requires_exact_run_attempt_binding(self):
        workflow = boundary.load_workflow(ROOT / boundary.WORKFLOW)
        for job_name, job in workflow["jobs"].items():
            for index, step in enumerate(job["steps"]):
                for key in ("CANDIDATE_ARCHIVE", "ARTIFACT_DIR", "EVIDENCE_DIR"):
                    if key not in step.get("env", {}):
                        continue
                    for change in (None, "/tmp/unbound-candidate", "${{ runner.temp }}/another-attempt"):
                        with self.subTest(job=job_name, step=step["id"], key=key, change=change):
                            mutant = copy.deepcopy(workflow)
                            env = mutant["jobs"][job_name]["steps"][index]["env"]
                            if change is None:
                                del env[key]
                            else:
                                env[key] = change
                            self.assertTrue(boundary.validate_workflow(mutant))


if __name__ == "__main__":
    unittest.main()
