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
            self.assertIn("steps.scope.outputs." + flag, step.get("if", ""))
            self.assertRegex(step.get("if", ""), r"(== 'true'|!= 'false')")
            self.assertNotIn("continue-on-error", step)
            scopes = [item for item in job["steps"] if item.get("id") == "scope"]
            self.assertEqual(len(scopes), 1)
            self.assertIn("scripts/ci_scope.py --base", scopes[0]["run"])
            self.assertIn('--format github >> "$GITHUB_OUTPUT"', scopes[0]["run"])
            self.assertIn("github.event.pull_request.base.sha", scopes[0]["env"]["BASE_SHA"])

    def test_documentation_diagnostics_remain_unconditional(self):
        ci = workflow("core-ci.yml")
        _, _, docs = self.step(ci, "scripts/check_documentation.py")
        self.assertIn("!cancelled()", docs.get("if", ""))
        self.assertNotIn("steps.scope", docs.get("if", ""))
        self.assertNotIn("continue-on-error", docs)

    def test_docs_core_source_each_report_after_an_earlier_failure(self):
        ci = workflow("core-ci.yml")
        docs = self.step(ci, "scripts/check_documentation.py")[2]
        core = self.step(ci, "scripts/run_python_tests.py --lane core")[2]
        source = self.step(ci, "scripts/run_python_tests.py --lane source")[2]
        self.assertEqual(len({id(docs), id(core), id(source)}), 3)
        for step in (docs, core, source):
            self.assertIn("!cancelled()", step.get("if", ""))
            self.assertNotIn("continue-on-error", step)
        self.assertNotIn("--lane source", core["run"])
        self.assertNotIn("--lane core", source["run"])

    def test_whitespace_checks_committed_event_range_with_a_clean_worktree(self):
        import os
        import subprocess
        import tempfile

        ci = workflow("core-ci.yml")
        _, _, step = self.step(ci, "git diff --check")
        script = step["run"]
        self.assertIn("github.event.pull_request.base.sha", step["env"]["BASE_SHA"])
        self.assertIn("github.event.pull_request.head.sha", step["env"]["HEAD_SHA"])
        with tempfile.TemporaryDirectory(prefix="hepta-ci-whitespace-") as directory:
            root = Path(directory)
            def git(*args, check=True):
                return subprocess.run(["git", *args], cwd=root, check=check,
                                      text=True, capture_output=True)
            git("init", "-q")
            git("config", "user.email", "ci-fixture@example.invalid")
            git("config", "user.name", "CI Fixture")
            path = root / "fixture.txt"
            path.write_text("clean\n")
            git("add", "fixture.txt")
            git("commit", "-qm", "base")
            base = git("rev-parse", "HEAD").stdout.strip()
            path.write_text("clean\ntrailing   \n")
            git("add", "fixture.txt")
            git("commit", "-qm", "introduce whitespace")
            head = git("rev-parse", "HEAD").stdout.strip()
            self.assertEqual(git("diff", "--check").stdout, "")
            def check_range(previous, current):
                return subprocess.run(["bash", "-c", script], cwd=root, text=True,
                                      capture_output=True,
                                      env={**os.environ, "BASE_SHA": previous, "HEAD_SHA": current})
            bad = check_range(base, head)
            self.assertNotEqual(bad.returncode, 0)
            self.assertIn("trailing whitespace", bad.stdout + bad.stderr)
            path.write_text("clean\ntrailing\n")
            git("add", "fixture.txt")
            git("commit", "-qm", "fix whitespace")
            fixed = git("rev-parse", "HEAD").stdout.strip()
            self.assertEqual(check_range(head, fixed).returncode, 0)
            self.assertNotEqual(check_range("f" * 40, fixed).returncode, 0)

    def test_operator_scope_is_bounded_and_missing_dependencies_stay_full(self):
        import tempfile
        import ci_scope

        python_only = {"run_native": False, "run_python": True, "run_learning": False}
        full = {"run_native": True, "run_python": True, "run_learning": True}
        with tempfile.TemporaryDirectory(prefix="hepta-ci-scope-") as directory:
            root = Path(directory)
            for group in ci_scope.VERIFIED_PYTHON_GROUPS:
                for name in group:
                    path = root / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.touch()
            for name in ("scripts/activate_prepared_driver.py", "tests/python/test_activate_prepared_driver.py",
                         "scripts/hepta_runtime_identity.py", "tests/python/test_runtime_identity.py"):
                self.assertEqual(ci_scope.scope_for_paths([name], root), python_only, name)
            for name in ("scripts/heptatrader_paper_driver.py",
                         "research/neuron_learning/laya_cell/resident_train.py", "scripts/new_operator.py"):
                self.assertEqual(ci_scope.scope_for_paths([name], root), full, name)
            (root / "tests/python/test_activate_prepared_driver.py").unlink()
            self.assertEqual(ci_scope.scope_for_paths(["scripts/activate_prepared_driver.py"], root), full)

    def test_resident_and_value_scopes_keep_python_and_learning_feedback(self):
        import tempfile
        import ci_scope

        expected = {"run_native": False, "run_python": True, "run_learning": True}
        with tempfile.TemporaryDirectory(prefix="hepta-ci-resident-scope-") as directory:
            root = Path(directory)
            for group in ci_scope.VERIFIED_PYTHON_LEARNING_GROUPS:
                for name in group:
                    path = root / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.touch()
            cases = [
                ["research/neuron_learning/laya_cell/resident.py"],
                ["research/neuron_learning/laya_cell/resident_store.py"],
                ["research/historical_training/resident_resource_guard.py"],
                ["research/historical_training/tests/test_resident_resource_guard.py"],
                ["tests/python/test_laya_training_window.py"],
                ["research/historical_training/value_successor_evaluator.py",
                 "research/historical_training/value_ledger_stream.py",
                 "tests/python/test_value_successor_evaluator.py"],
                ["docs/technical/runtime-cost-observations.md",
                 "research/historical_training/resident_resource_guard.py",
                 "research/neuron_learning/laya_cell/resident.py",
                 "research/neuron_learning/laya_cell/resident_store.py",
                 "tests/python/test_laya_training_cadence.py"],
            ]
            for paths in cases:
                with self.subTest(paths=paths):
                    self.assertEqual(ci_scope.scope_for_paths(paths, root), expected)
            combined = cases[5] + cases[6] + ["tests/python/test_laya_training_window.py"]
            self.assertEqual(ci_scope.scope_for_paths(combined, root), expected)

    def test_resident_and_value_exemptions_fail_closed_for_missing_or_shared_paths(self):
        import tempfile
        import ci_scope

        full = {"run_native": True, "run_python": True, "run_learning": True}
        resident = "research/neuron_learning/laya_cell/resident.py"
        successor = "research/historical_training/value_successor_evaluator.py"
        with tempfile.TemporaryDirectory(prefix="hepta-ci-scope-boundary-") as directory:
            root = Path(directory)
            for group in ci_scope.VERIFIED_PYTHON_LEARNING_GROUPS:
                for name in group:
                    path = root / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.touch()
            for path in (
                    "HeptaTrade/execution/execution_coordinator.cpp",
                    "research/neuron_learning/laya_cell/resident_train.py",
                    "research/neuron_learning/laya_cell/install_resident.py",
                    "research/neuron_learning/laya_cell/new_resident_helper.py",
                    "research/historical_training/new_value_helper.py",
                    "scripts/install_btc_entry_runtime.py",
                    "scripts/ci_scope.py",
                    "tests/python/test_development_ci.py",
                    ".github/workflows/core-ci.yml",
                    "systemd/heptatrader-laya-trainer.service"):
                with self.subTest(path=path):
                    self.assertEqual(ci_scope.scope_for_paths([resident, successor, path], root), full)
            self.assertEqual(ci_scope.scope_for_paths(
                [resident, "research/neuron_learning/laya_cell/moved_resident.py"], root), full)
            paired = root / "tests/python/test_laya_training_window.py"
            paired.unlink()
            self.assertEqual(ci_scope.scope_for_paths([resident], root), full)
            paired.touch()
            stream = root / "research/historical_training/value_ledger_stream.py"
            stream.unlink()
            self.assertEqual(ci_scope.scope_for_paths([successor], root), full)
            stream.symlink_to(root / successor)
            self.assertEqual(ci_scope.scope_for_paths([successor], root), full)

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
