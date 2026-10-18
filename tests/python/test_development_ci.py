from pathlib import Path
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[2]


def workflow(name):
    return yaml.load((ROOT / ".github/workflows" / name).read_text(),
                     Loader=yaml.BaseLoader)


def command_steps(ci, command):
    return [(name, job, step)
            for name, job in ci["jobs"].items()
            for step in job["steps"] if command in step.get("run", "")]


def ancestors(ci, name):
    found = set()
    pending = [name]
    while pending:
        job = ci["jobs"][pending.pop()]
        needed = job.get("needs", [])
        if isinstance(needed, str):
            needed = [needed]
        for dependency in needed:
            if dependency not in found:
                found.add(dependency)
                pending.append(dependency)
    return found


class DevelopmentCiTests(unittest.TestCase):
    def step(self, ci, command):
        matches = command_steps(ci, command)
        self.assertEqual(len(matches), 1, command)
        return matches[0]

    def test_each_behavior_lane_runs_once(self):
        ci = workflow("core-ci.yml")
        self.step(ci, "./scripts/dev_core.sh")
        for lane in ("core", "source", "learning"):
            self.step(ci, "scripts/run_python_tests.py --lane " + lane)
        commands = "\n".join(step.get("run", "")
                             for job in ci["jobs"].values() for step in job["steps"])
        for obsolete in ("verify_required_checks", "accept_core_release",
                         "ci_change_scope", "--write-module-metadata"):
            self.assertNotIn(obsolete, commands)

    def test_learning_and_native_feedback_do_not_depend_on_metadata(self):
        ci = workflow("core-ci.yml")
        source_name, _, _ = self.step(ci, "scripts/run_python_tests.py --lane source")
        learning_name, _, _ = self.step(ci, "scripts/run_python_tests.py --lane learning")
        native_name, _, _ = self.step(ci, "./scripts/dev_core.sh")
        for name in (learning_name, native_name):
            self.assertNotEqual(name, source_name)
            self.assertNotIn(source_name, ancestors(ci, name))

    def test_routine_events_use_read_only_credentials_and_exact_source(self):
        ci = workflow("core-ci.yml")
        self.assertEqual(ci["permissions"], {"contents": "read"})
        self.assertIn("main", ci["on"]["push"]["branches"])
        self.assertNotIn("branches", ci["on"]["pull_request"])
        self.assertNotIn("pull_request_target", ci["on"])
        for event in ("opened", "synchronize", "reopened", "ready_for_review"):
            self.assertIn(event, ci["on"]["pull_request"]["types"])
        for job in ci["jobs"].values():
            self.assertNotIn("if", job)
            checkouts = [step for step in job["steps"]
                         if step.get("uses", "").startswith("actions/checkout@")]
            self.assertEqual(len(checkouts), 1)
            checkout = checkouts[0]
            self.assertRegex(checkout["uses"], r"^actions/checkout@[0-9a-f]{40}$")
            self.assertEqual(checkout["with"]["persist-credentials"], "false")
            self.assertIn("github.event.pull_request.head.sha", checkout["with"]["ref"])
            commands = "\n".join(step.get("run", "") for step in job["steps"])
            self.assertIn('git rev-parse HEAD', commands)
            self.assertIn('$EXPECTED_SHA', commands)

    def test_scope_outputs_control_their_actual_execution(self):
        ci = workflow("core-ci.yml")
        for command, flag in (
                ("./scripts/dev_core.sh", "run_native"),
                ("scripts/run_python_tests.py --lane core", "run_python"),
                ("scripts/run_python_tests.py --lane source", "run_python"),
                ("scripts/run_python_tests.py --lane learning", "run_learning")):
            _, job, step = self.step(ci, command)
            self.assertEqual(step.get("if"), "steps.scope.outputs." + flag + " == 'true'")
            scopes = [item for item in job["steps"] if item.get("id") == "scope"]
            self.assertEqual(len(scopes), 1)
            self.assertIn("scripts/ci_scope.py --base", scopes[0]["run"])
            self.assertIn('--format github >> "$GITHUB_OUTPUT"', scopes[0]["run"])
            self.assertIn("github.event.pull_request.base.sha", scopes[0]["env"]["BASE_SHA"])

    def test_documentation_diagnostics_remain_unconditional(self):
        ci = workflow("core-ci.yml")
        _, _, docs = self.step(ci, "scripts/check_documentation.py")
        self.assertNotIn("if", docs)
        self.assertNotIn("continue-on-error", docs)

    def test_learning_cache_cannot_replace_installation_or_tests(self):
        ci = workflow("core-ci.yml")
        _, job, tests = self.step(ci, "scripts/run_python_tests.py --lane learning")
        caches = [step for step in job["steps"]
                  if step.get("uses", "").startswith("actions/cache@")]
        self.assertEqual(len(caches), 1)
        cache = caches[0]
        self.assertRegex(cache["uses"], r"^actions/cache@[0-9a-f]{40}$")
        self.assertEqual(cache["with"]["path"], "~/.cache/pip")
        self.assertEqual(cache.get("continue-on-error"), "true")
        installs = [step for step in job["steps"]
                    if "pip install torch==" in step.get("run", "")]
        self.assertEqual(len(installs), 1)
        install = installs[0]
        self.assertIn("--index-url https://download.pytorch.org/whl/cpu", install["run"])
        self.assertIn("safetensors==", install["run"])
        self.assertIn("torch.version.cuda is None", install["run"])
        self.assertLess(job["steps"].index(install), job["steps"].index(tests))
        for step in (cache, install, tests):
            self.assertEqual(step.get("if"), "steps.scope.outputs.run_learning == 'true'")
            self.assertNotIn("cache-hit", str(step))
        self.assertNotIn("continue-on-error", install)
        self.assertNotIn("continue-on-error", tests)

    def test_deep_suites_are_manual_and_broker_qualification_is_separate(self):
        for name in ("canonical-full-suite.yml", "heptadll-integration.yml"):
            ci = workflow(name)
            self.assertEqual(set(ci["on"]), {"workflow_dispatch"})
            self.assertTrue(ci["jobs"])
        release = workflow("release.yml")
        self.assertIn("tags", release["on"]["push"])
        self.assertIn("scripts/accept_core_release.py", str(release))
        self.assertTrue((ROOT / ".github/workflows/ib-paper-qualification.yml").is_file())

    def test_superseded_feedback_can_cancel_but_broker_run_cannot(self):
        ci = workflow("core-ci.yml")
        self.assertEqual(ci["concurrency"]["cancel-in-progress"],
                         "${{ github.event_name != 'workflow_dispatch' }}")
        self.assertIn("github.event_name", ci["concurrency"]["group"])
        self.assertEqual(
            workflow("ib-paper-qualification.yml")["concurrency"]["cancel-in-progress"],
            "false")


if __name__ == "__main__":
    unittest.main()
