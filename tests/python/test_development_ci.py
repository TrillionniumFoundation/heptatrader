from pathlib import Path
import ast
import fnmatch
import re
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


def condition_value(step, context, *, succeeded=True, cancelled=False):
    """Evaluate the boolean subset used by CI conditions, including implicit success."""
    expression = step.get("if", "true").strip().removeprefix("${{").removesuffix("}}").strip()
    if not re.search(r"\b(?:success|failure|cancelled|always)\s*\(", expression) and not succeeded:
        return False
    tokens = re.findall(r"'(?:''|[^'])*'|[A-Za-z_][\w.-]*(?:\(\))?|&&|\|\||==|!=|[!()]", expression)
    if "".join(tokens) != re.sub(r"\s+", "", re.sub(r"'(?:''|[^'])*'", lambda m: m[0].replace(" ", ""), expression)):
        raise AssertionError("Unsupported CI condition: " + expression)
    operators = {"&&": " and ", "||": " or ", "!": " not ", "==": " == ", "!=": " != ", "(": "(", ")": ")"}
    statuses = {"success()": succeeded, "failure()": not succeeded and not cancelled,
                "cancelled()": cancelled, "always()": True, "true": True, "false": False}
    translated = []
    for token in tokens:
        if token in operators:
            translated.append(operators[token])
        elif token.startswith("'"):
            translated.append(repr(token[1:-1].replace("''", "'")))
        else:
            translated.append(repr(statuses.get(token, context.get(token, ""))))
    def value(node):
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.BoolOp):
            operands = [value(item) for item in node.values]
            return all(operands) if isinstance(node.op, ast.And) else any(operands)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            return not value(node.operand)
        if isinstance(node, ast.Compare) and len(node.ops) == 1:
            left, right = value(node.left), value(node.comparators[0])
            if isinstance(node.ops[0], ast.Eq):
                return left == right
            if isinstance(node.ops[0], ast.NotEq):
                return left != right
        raise AssertionError("Unsupported CI condition node: " + ast.dump(node))
    return bool(value(ast.parse("".join(translated).strip(), mode="eval").body))


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

    def test_behavior_steps_follow_scope_and_real_dependencies(self):
        ci = workflow("core-ci.yml")
        _, job, identity = self.step(ci, 'test "$(git rev-parse HEAD)"')
        _, _, deps = self.step(ci, "apt-get install")
        _, _, scope = self.step(ci, "scripts/ci_scope.py")
        _, _, install = self.step(ci, "pip install torch==")
        ids = [step["id"] for step in job["steps"] if "id" in step]
        context = {f"steps.{name}.{field}": "success" for name in ids
                   for field in ("outcome", "conclusion")}
        flags = ("run_native", "run_python", "run_learning")
        context.update({f"steps.{scope['id']}.outputs.{flag}": "true" for flag in flags})
        self.assertNotIn("continue-on-error", scope)
        self.assertIn("--format github", scope["run"])
        self.assertIn("GITHUB_OUTPUT", scope["run"])
        self.assertIn("github.event.pull_request.base.sha", scope["env"]["BASE_SHA"])
        lanes = (("./scripts/dev_core.sh", "run_native"),
                 ("scripts/run_python_tests.py --lane core", "run_python"),
                 ("scripts/run_python_tests.py --lane source", "run_python"),
                 ("scripts/run_python_tests.py --lane learning", "run_learning"),
                 ("pip install torch==", "run_learning"),
                 ("scripts/check_documentation.py", None), ("git diff --check", None))
        for command, flag in lanes:
            _, _, step = self.step(ci, command)
            required = {identity["id"]} | ({deps["id"]} if flag else set())
            if command.endswith("--lane learning"):
                required.add(install["id"])
            with self.subTest(command=command):
                self.assertNotIn("continue-on-error", step)
                self.assertTrue(condition_value(step, context))
                self.assertTrue(condition_value(step, context, succeeded=False))
                self.assertFalse(condition_value(step, context, cancelled=True))
                for failed in ids:
                    failed_context = {**context, **{f"steps.{failed}.{field}": "failure"
                                                    for field in ("outcome", "conclusion")}}
                    self.assertEqual(condition_value(step, failed_context, succeeded=False),
                                     failed not in required, (command, failed))
                for output in ("false", ""):
                    values = {**context, **{f"steps.{scope['id']}.outputs.{name}": output for name in flags}}
                    self.assertEqual(condition_value(step, values, succeeded=False),
                                     flag is None or output != "false", (command, output))
                if flag:
                    values = {**context, **{f"steps.{scope['id']}.outputs.{name}": "false" for name in flags}}
                    values[f"steps.{scope['id']}.outputs.{flag}"] = "true"
                    self.assertTrue(condition_value(step, values))


    def test_routine_events_use_read_only_credentials_and_exact_source(self):
        ci = workflow("core-ci.yml")
        self.assertEqual(ci["permissions"], {"contents": "read"})
        self.assertIn("main", ci["on"]["push"]["branches"])
        self.assertNotIn("branches", ci["on"]["pull_request"])
        self.assertNotIn("pull_request_target", ci["on"])
        for event in ("opened", "synchronize", "reopened", "ready_for_review"):
            self.assertIn(event, ci["on"]["pull_request"]["types"])
        for job in ci["jobs"].values():
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

    def test_archived_contract_lane_runs_only_for_its_inputs(self):
        ci = workflow("legacy-qualification.yml")
        self.assertEqual(ci["permissions"], {"contents": "read"})
        self.assertNotIn("pull_request_target", ci["on"])
        self.assertIn("workflow_dispatch", ci["on"])
        self.step(ci, "scripts/run_python_tests.py --lane legacy")
        inputs = (".github/retired-workflows/ib-paper-qualification.yml",
                  ".github/workflows/legacy-qualification.yml",
                  "scripts/check_qualification_trust_boundary.py", "scripts/run_python_tests.py",
                  "tests/python/test_ib_workflow_interfaces.py",
                  "tests/python/test_qualification_trust_boundary.py",
                  "tests/python/test_python_test_partition.py")
        for event in ("pull_request", "push"):
            patterns = ci["on"][event]["paths"]
            for path in inputs:
                self.assertTrue(any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns), path)
            for path in ("research/neuron_learning/laya_cell/resident.py",
                         "scripts/heptatrader_paper_driver.py", "docs/technical/runtime-cost-observations.md"):
                self.assertFalse(any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns), path)


    def test_identity_mismatch_fails_before_behavior_can_run(self):
        import os
        import subprocess
        import tempfile
        ci = workflow("core-ci.yml")
        _, job, identity = self.step(ci, 'test "$(git rev-parse HEAD)"')
        self.assertNotIn("if", identity)
        self.assertNotIn("continue-on-error", identity)
        self.assertNotIn("continue-on-error", job)
        with tempfile.TemporaryDirectory(prefix="hepta-ci-identity-") as directory:
            root = Path(directory)
            def git(*args):
                return subprocess.check_output(
                    ["git", "-c", "core.hooksPath=/dev/null",
                     "-c", "commit.gpgsign=false",
                     "-c", "user.name=Identity Test",
                     "-c", "user.email=identity-test@example.invalid", *args],
                    cwd=root, stderr=subprocess.PIPE, text=True, timeout=15).strip()
            git("init", "-q")
            git("commit", "--allow-empty", "-qm", "identity fixture")
            head = git("rev-parse", "HEAD")
            for expected, valid in ((head, True), ("f" * 40, False), ("", False)):
                with self.subTest(expected=expected):
                    result = subprocess.run(
                        ["bash", "-c", identity["run"]], cwd=root,
                        capture_output=True, text=True, timeout=15,
                        env={**os.environ, "EXPECTED_SHA": expected})
                    self.assertEqual(result.returncode == 0, valid,
                                     result.stdout + result.stderr)


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


    def test_learning_cache_cannot_replace_installation_or_tests(self):
        ci = workflow("core-ci.yml")
        _, job, tests = self.step(ci, "scripts/run_python_tests.py --lane learning")
        caches = [step for step in job["steps"]
                  if step.get("uses", "").startswith("actions/cache@")]
        self.assertTrue(caches)
        for cache in caches:
            self.assertRegex(cache["uses"], r"^actions/cache@[0-9a-f]{40}$")
            self.assertEqual(cache.get("continue-on-error"), "true")
        _, _, install = self.step(ci, "pip install torch==")
        self.assertIn("--index-url https://download.pytorch.org/whl/cpu", install["run"])
        self.assertIn("safetensors==", install["run"])
        self.assertIn("torch.version.cuda is None", install["run"])
        self.assertLess(job["steps"].index(install), job["steps"].index(tests))
        for step in (install, tests):
            self.assertNotIn("cache-hit", str(step))
            self.assertNotIn("continue-on-error", step)

    def test_deep_suites_are_manual_and_broker_qualification_is_separate(self):
        for name in ("canonical-full-suite.yml", "heptadll-integration.yml"):
            ci = workflow(name)
            self.assertEqual(set(ci["on"]), {"workflow_dispatch"})
            self.assertTrue(ci["jobs"])
        release = workflow("release.yml")
        self.assertIn("tags", release["on"]["push"])
        self.assertIn("scripts/accept_core_release.py", str(release))
        self.assertTrue((ROOT / ".github/retired-workflows/ib-paper-qualification.yml").is_file())
        for retired in ("ib-paper-qualification.yml", "self-hosted-ib-availability.yml"):
            self.assertFalse((ROOT / ".github/workflows" / retired).exists())

    def test_superseded_feedback_can_cancel_but_broker_run_cannot(self):
        ci = workflow("core-ci.yml")
        self.assertEqual(ci["concurrency"]["cancel-in-progress"],
                         "${{ github.event_name != 'workflow_dispatch' }}")
        self.assertIn("github.event_name", ci["concurrency"]["group"])
        self.assertEqual(
            yaml.load((ROOT / ".github/retired-workflows/ib-paper-qualification.yml").read_text(),
                      Loader=yaml.BaseLoader)["concurrency"]["cancel-in-progress"],
            "false")


if __name__ == "__main__":
    unittest.main()
