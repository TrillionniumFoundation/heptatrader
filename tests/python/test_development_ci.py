from pathlib import Path
import ast
import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[2]


def workflow(name):
    return yaml.load((ROOT / ".github/workflows" / name).read_text(),
                     Loader=yaml.BaseLoader)


def identified_steps(ci, step_id):
    return [(name, job, step)
            for name, job in ci["jobs"].items()
             for step in job["steps"] if step.get("id") == step_id]


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
    def step(self, ci, step_id):
        matches = identified_steps(ci, step_id)
        self.assertEqual(len(matches), 1, step_id)
        return matches[0]

    def test_each_behavior_lane_runs_once(self):
        ci = workflow("core-ci.yml")
        with tempfile.TemporaryDirectory(prefix="hepta-ci-lanes-") as directory:
            root = Path(directory)
            scripts, binary, log = root / "scripts", root / "bin", root / "calls.jsonl"
            scripts.mkdir()
            binary.mkdir()
            (binary / "python3").symlink_to(sys.executable)
            record = (
                "import json, os\n"
                "with open(os.environ['HEPTA_TEST_CALLS'], 'a') as output:\n"
                "    output.write(json.dumps(call) + '\\n')\n")
            (scripts / "run_python_tests.py").write_text(
                "import argparse\nparser = argparse.ArgumentParser()\n"
                "parser.add_argument('--lane', choices=('core', 'source', 'packaging', 'learning'), required=True)\n"
                "call = ['python', parser.parse_args().lane]\n" + record)
            native = scripts / "dev_core.sh"
            native.write_text("#!" + sys.executable + "\nimport sys\n"
                              "assert sys.argv[1:] in ([], ['--core'])\n"
                              "call = ['native']\n" + record)
            native.chmod(0o755)
            for step_id in ("native", "python_core", "python_source", "python_packaging", "python_learning"):
                _, _, step = self.step(ci, step_id)
                result = subprocess.run(
                    [shutil.which("bash"), "-e", "-o", "pipefail", "-c", step["run"]],
                    cwd=root, capture_output=True, text=True, timeout=10,
                    env={**os.environ, "PATH": str(binary), "HEPTA_TEST_CALLS": str(log)})
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual([json.loads(line) for line in log.read_text().splitlines()],
                             [["native"], ["python", "core"], ["python", "source"],
                              ["python", "packaging"], ["python", "learning"]])

    def test_behavior_steps_follow_scope_and_real_dependencies(self):
        ci = workflow("core-ci.yml")
        _, job, identity = self.step(ci, "identity")
        _, _, deps = self.step(ci, "deps")
        _, _, scope = self.step(ci, "scope")
        _, _, install = self.step(ci, "learning_install")
        ids = [step["id"] for step in job["steps"] if "id" in step]
        context = {f"steps.{name}.{field}": "success" for name in ids
                   for field in ("outcome", "conclusion")}
        flags = ("run_native", "run_python", "run_learning")
        context.update({f"steps.{scope['id']}.outputs.{flag}": "true" for flag in flags})
        self.assertNotIn("continue-on-error", scope)
        self.assertIn("github.event.pull_request.base.sha", scope["env"]["BASE_SHA"])
        lanes = (("native", "run_native"), ("python_core", "run_python"),
                 ("python_source", "run_python"), ("python_packaging", "run_native"),
                 ("python_learning", "run_learning"),
                 ("learning_install", "run_learning"), ("docs", None), ("whitespace", None))
        for step_id, flag in lanes:
            _, _, step = self.step(ci, step_id)
            required = {identity["id"]} | ({deps["id"]} if flag else set())
            if step_id == "python_learning":
                required.add(install["id"])
            with self.subTest(step_id=step_id):
                self.assertNotIn("continue-on-error", step)
                self.assertTrue(condition_value(step, context))
                self.assertTrue(condition_value(step, context, succeeded=False))
                self.assertFalse(condition_value(step, context, cancelled=True))
                for failed in ids:
                    failed_context = {**context, **{f"steps.{failed}.{field}": "failure"
                                                    for field in ("outcome", "conclusion")}}
                    self.assertEqual(condition_value(step, failed_context, succeeded=False),
                                      failed not in required, (step_id, failed))
                for output in ("false", ""):
                    values = {**context, **{f"steps.{scope['id']}.outputs.{name}": output for name in flags}}
                    self.assertEqual(condition_value(step, values, succeeded=False),
                                      flag is None or output != "false", (step_id, output))
                if flag:
                    values = {**context, **{f"steps.{scope['id']}.outputs.{name}": "false" for name in flags}}
                    values[f"steps.{scope['id']}.outputs.{flag}"] = "true"
                    self.assertTrue(condition_value(step, values))

    def test_scope_step_publishes_machine_outputs_with_the_original_base(self):
        _, _, step = self.step(workflow("core-ci.yml"), "scope")
        with tempfile.TemporaryDirectory(prefix="hepta-ci-scope-") as directory:
            root = Path(directory)
            binary, scripts = root / "bin", root / "scripts"
            binary.mkdir()
            scripts.mkdir()
            (binary / "python3").symlink_to(sys.executable)
            git = binary / "git"
            git.write_text("#!/bin/sh\nexit 0\n")  # Never contacts a remote.
            git.chmod(0o755)
            (scripts / "ci_scope.py").write_text(
                "import argparse, os\n"
                "parser = argparse.ArgumentParser()\n"
                "parser.add_argument('--base', required=True)\n"
                "parser.add_argument('--format', default='text')\n"
                "args = parser.parse_args()\n"
                "assert args.base == os.environ['BASE_SHA']\n"
                "print('run_native=false\\nrun_python=true\\nrun_learning=false'\n"
                "      if args.format == 'github' else 'human-readable scope')\n")
            output = root / "github-output"
            result = subprocess.run(
                [shutil.which("bash"), "-e", "-o", "pipefail", "-c", step["run"]],
                cwd=root, capture_output=True, text=True, timeout=10,
                env={**os.environ, "PATH": str(binary), "BASE_SHA": "a" * 40,
                     "GITHUB_OUTPUT": str(output)})
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(dict(line.split("=", 1) for line in output.read_text().splitlines()),
                             {"run_native": "false", "run_python": "true", "run_learning": "false"})


    def test_routine_events_use_read_only_credentials_and_exact_source(self):
        ci = workflow("core-ci.yml")
        self.assertEqual(ci["permissions"], {"contents": "read"})
        self.assertIn("main", ci["on"]["push"]["branches"])
        self.assertNotIn("branches", ci["on"]["pull_request"])
        self.assertNotIn("pull_request_target", ci["on"])
        for event in ("opened", "synchronize", "reopened"):
            self.assertIn(event, ci["on"]["pull_request"]["types"])
        for job in ci["jobs"].values():
            checkouts = [step for step in job["steps"]
                         if step.get("uses", "").startswith("actions/checkout@")]
            self.assertEqual(len(checkouts), 1)
            checkout = checkouts[0]
            self.assertRegex(checkout["uses"], r"^actions/checkout@[0-9a-f]{40}$")
            self.assertEqual(checkout["with"]["persist-credentials"], "false")
            self.assertIn("github.event.pull_request.head.sha", checkout["with"]["ref"])
        self.step(ci, "identity")  # The real Git fixture below checks its behavior.

    def test_archived_contract_lane_runs_only_for_its_inputs(self):
        ci = workflow("legacy-qualification.yml")
        self.assertEqual(ci["permissions"], {"contents": "read"})
        self.assertNotIn("pull_request_target", ci["on"])
        self.assertIn("workflow_dispatch", ci["on"])
        self.step(ci, "legacy_tests")
        inputs = (".github/retired-workflows/ib-paper-qualification.yml",
                  ".github/workflows/legacy-qualification.yml",
                  "scripts/check_qualification_trust_boundary.py", "scripts/run_python_tests.py",
                  "tests/python/test_ib_workflow_interfaces.py",
                  "tests/python/test_qualification_trust_boundary.py",
                  "tests/python/test_heptadll_migration_state.py",
                  "tests/python/test_heptadll_source_disposition.py",
                  "docs/technical/heptadll-consumer-migrations.json",
                  "docs/technical/heptadll-lifecycle-status.json",
                  "docs/technical/heptadll-source-disposition.json",
                  "tests/python/test_python_test_partition.py",
                  "research/train_v2/__init__.py",
                  "research/train_v2/fx_original_health_portable_prefix_verify.py",
                  "research/train_v2/fx_original_health_portable_160_verify.py",
                  "research/train_v2/fx_original_health_portable_240_verify.py",
                  "research/train_v2/fx_original_health_portable_320_verify.py",
                  "research/train_v2/fx_original_health_sustained_frontier.py",
                  "research/train_v2/fx_original_health_sustained_audit.py",
                  "research/train_v2/fx_original_health_fullcycle_audit.py",
                  "tests/python/test_fx_original_health_sustained_frontier.py",
                  "tests/python/test_fx_original_health_portable_320_verify.py",
                  "tests/python/portable_archive_fixtures.py")
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
        _, job, identity = self.step(ci, "identity")
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
        _, _, step = self.step(ci, "whitespace")
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


    def test_hosted_apt_transport_preserves_fallbacks_and_fails_closed(self):
        import os
        import subprocess
        import tempfile

        _, _, step = self.step(workflow("core-ci.yml"), "deps")
        with tempfile.TemporaryDirectory(prefix="hepta-ci-apt-") as directory:
            root = Path(directory)
            mirrors = root / "apt-mirrors.txt"
            log = root / "apt.log"
            script = step["run"].replace("/etc/apt/apt-mirrors.txt", str(mirrors))
            self.assertNotIn("/etc/apt/", script)
            subprocess.run(["bash", "-n"], input=script, text=True, check=True,
                           capture_output=True, timeout=10)
            embedded = re.search(r"<<'PY'\n(.*?)\nPY\n", script, re.S)
            self.assertIsNotNone(embedded)
            compile(embedded[1], "hosted APT setup", "exec")
            (root / "sudo").write_text('#!/bin/sh\nexec "$@"\n')
            (root / "apt-get").write_text(
                '#!/bin/sh\nprintf \'%s\\n\' "$*" >> "$APT_LOG"\n'
                'if [ "$1" = "${APT_FAIL_COMMAND:-}" ]; then exit 23; fi\n'
                'exit 0\n')
            for name in ("sudo", "apt-get"):
                (root / name).chmod(0o755)
            env = {**os.environ, "PATH": str(root) + os.pathsep + os.environ["PATH"],
                   "APT_LOG": str(log)}
            original = (
                "# Keep the configured mirrors and metadata.\n"
                "http://azure.archive.ubuntu.com/ubuntu/\tpriority:1 arch:amd64\n"
                "http://archive.ubuntu.com/ubuntu/\tpriority:9 suite:noble\n"
                "https://example.invalid/ubuntu/\tpriority:8\n")
            expected = (
                "# Keep the configured mirrors and metadata.\n"
                "http://azure.archive.ubuntu.com/ubuntu/\tpriority:2 arch:amd64\n"
                "https://archive.ubuntu.com/ubuntu/\tpriority:1 suite:noble\n"
                "https://example.invalid/ubuntu/\tpriority:8\n")

            def run(native="false", learning="false", failure=""):
                log.unlink(missing_ok=True)
                result = subprocess.run(
                    ["bash", "-c", script], capture_output=True, text=True, timeout=10,
                    env={**env, "RUN_NATIVE": native, "RUN_LEARNING": learning,
                         "APT_FAIL_COMMAND": failure})
                calls = [line.split() for line in log.read_text().splitlines()] if log.exists() else []
                return result, calls

            for initial, wanted in ((original, expected), (expected, expected),
                                    (None, None),
                                    ("https://example.invalid/ubuntu/\n", "https://example.invalid/ubuntu/\n")):
                for native, learning in (("false", "false"), ("true", "false"),
                                         ("false", "true"), ("true", "true")):
                    with self.subTest(mirrors=initial, native=native, learning=learning):
                        if initial is None:
                            mirrors.unlink(missing_ok=True)
                        else:
                            mirrors.write_text(initial)
                        result, calls = run(native, learning)
                        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                        self.assertEqual(mirrors.read_text() if mirrors.exists() else None, wanted)
                        self.assertEqual([call[0] for call in calls], ["update", "install"])
                        for call in calls:
                            options = {call[i + 1].split("=", 1)[0]: call[i + 1].split("=", 1)[1]
                                       for i, arg in enumerate(call) if arg == "-o"}
                            self.assertEqual(set(options), {"Acquire::http::Timeout", "Acquire::https::Timeout", "Acquire::Retries"})
                            for transport in ("http", "https"):
                                self.assertGreater(int(options[f"Acquire::{transport}::Timeout"]), 0)
                                self.assertLessEqual(int(options[f"Acquire::{transport}::Timeout"]), 30)
                            self.assertEqual(int(options["Acquire::Retries"]), 1)
                        packages = ["cmake", "libssl-dev", "zlib1g-dev", "python3-yaml", "python3-numpy", "python3-cryptography"]
                        if native == "true":
                            packages.extend(("ninja-build", "ccache"))
                        if learning == "true":
                            packages.append("python3-venv")
                        install = [arg for arg in calls[1][1:] if arg != "-o" and not arg.startswith("Acquire::")]
                        self.assertIn("--yes", install)
                        self.assertIn("--no-install-recommends", install)
                        installed_packages = [arg for arg in install if not arg.startswith("-")]
                        self.assertTrue(set(packages).issubset(installed_packages))
                        if native == "false":
                            self.assertNotIn("ccache", installed_packages)
            for failure, commands in (("update", ["update"]), ("install", ["update", "install"])):
                with self.subTest(failure=failure):
                    result, calls = run(failure=failure)
                    self.assertEqual(result.returncode, 23, result.stdout + result.stderr)
                    self.assertEqual([call[0] for call in calls], commands)

    def test_native_cache_does_not_replace_current_build_or_behavior(self):
        ci = workflow("core-ci.yml")
        _, job, cache = self.step(ci, "native_cache")
        _, _, native = self.step(ci, "native")
        self.assertEqual(cache.get("continue-on-error"), "true")
        self.assertEqual(native["env"].get("CMAKE_CXX_COMPILER_LAUNCHER"), "ccache")
        self.assertEqual(job["env"].get("CCACHE_COMPILERCHECK"), "content")
        self.assertNotIn("cache-hit", native["if"])
        self.assertLess(job["steps"].index(cache), job["steps"].index(native))
        context = {f"steps.{step['id']}.{field}": "success"
                   for step in job["steps"] if "id" in step
                   for field in ("outcome", "conclusion")}
        context.update({f"steps.scope.outputs.{flag}": "true"
                        for flag in ("run_native", "run_python", "run_learning")})
        for cache_outcome in ("success", "failure", "skipped"):
            values = {**context, "steps.native_cache.outcome": cache_outcome,
                      "steps.native_cache.conclusion": cache_outcome}
            self.assertTrue(condition_value(native, values, succeeded=False))
            self.assertFalse(condition_value(native, values, cancelled=True))

    def test_learning_cache_cannot_replace_installation_or_tests(self):
        ci = workflow("core-ci.yml")
        _, job, tests = self.step(ci, "python_learning")
        caches = [step for step in job["steps"]
                  if step.get("uses", "").startswith("actions/cache@")]
        self.assertTrue(caches)
        for cache in caches:
            self.assertRegex(cache["uses"], r"^actions/cache@[0-9a-f]{40}$")
            self.assertEqual(cache.get("continue-on-error"), "true")
        _, _, install = self.step(ci, "learning_install")
        self.assertLess(job["steps"].index(install), job["steps"].index(tests))
        for step in (install, tests):
            self.assertNotIn("continue-on-error", step)
        context = {f"steps.{step['id']}.{field}": "success"
                   for step in job["steps"] if "id" in step
                   for field in ("outcome", "conclusion")}
        context.update({f"steps.scope.outputs.{flag}": "true"
                        for flag in ("run_native", "run_python", "run_learning")})
        for cache in caches:
            for hit in ("true", "false", ""):
                with self.subTest(cache=cache["id"], hit=hit):
                    values = {**context, f"steps.{cache['id']}.outputs.cache-hit": hit}
                    self.assertTrue(condition_value(install, values))
                    self.assertTrue(condition_value(tests, values))

    def test_learning_install_executes_pinned_cpu_checks_and_propagates_failure(self):
        _, _, install = self.step(workflow("core-ci.yml"), "learning_install")
        with tempfile.TemporaryDirectory(prefix="hepta-ci-learning-") as directory:
            root = Path(directory)
            binary, site = root / "bin", root / "fake-site"
            binary.mkdir()
            site.mkdir()
            shim = binary / "python3"
            shim.write_text("#!" + sys.executable + "\n" + '''
import json, os, pathlib, shutil, sys
args = sys.argv[1:]
if args[:2] == ['-m', 'venv']:
    target = pathlib.Path(args[-1]) / 'bin' / 'python'
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(__file__, target)
    target.chmod(0o755)
elif args[:3] == ['-m', 'pip', 'install']:
    with open(os.environ['HEPTA_TEST_PIP_LOG'], 'a') as log:
        log.write(json.dumps(args[3:]) + '\\n')
    if os.environ.get('HEPTA_TEST_PIP_FAIL'):
        raise SystemExit(23)
    site = pathlib.Path(os.environ['HEPTA_TEST_SITE'])
    for package in args[3:]:
        if package.startswith('torch=='):
            version = package.split('==', 1)[1]
            (site / 'torch.py').write_text(
                'import os, types\\n__version__=' + repr(version + '+cpu') + '\\n' +
                "version=types.SimpleNamespace(cuda=os.environ.get('HEPTA_TEST_CUDA'))\\n")
        elif package.startswith('safetensors=='):
            version = package.split('==', 1)[1]
            (site / 'safetensors.py').write_text('__version__=' + repr(version) + '\\n')
else:
    os.execv(sys.executable, [sys.executable, *args])
''')
            shim.chmod(0o755)
            log = root / "pip.jsonl"
            for cuda, pip_failure, expected in (("", "", True), ("12.0", "", False),
                                                ("", "1", False)):
                with self.subTest(cuda=cuda, pip_failure=pip_failure):
                    log.unlink(missing_ok=True)
                    for package in ("torch", "safetensors"):
                        (site / (package + ".py")).write_text(
                            "raise RuntimeError('dependency was not installed')\n")
                    env = {**os.environ, "PATH": str(binary), "PYTHONPATH": str(site),
                           "PYTHONDONTWRITEBYTECODE": "1", "RUNNER_TEMP": str(root),
                           "GITHUB_PATH": str(root / "github-path"),
                           "HEPTA_TEST_SITE": str(site), "HEPTA_TEST_PIP_LOG": str(log),
                           "HEPTA_TEST_PIP_FAIL": pip_failure}
                    env.pop("HEPTA_TEST_CUDA", None)
                    if cuda:
                        env["HEPTA_TEST_CUDA"] = cuda
                    result = subprocess.run(
                        [shutil.which("bash"), "-e", "-o", "pipefail", "-c", install["run"]],
                        cwd=root, capture_output=True, text=True, timeout=15, env=env)
                    self.assertEqual(result.returncode == 0, expected, result.stdout + result.stderr)
                    if expected:
                        calls = [json.loads(line) for line in log.read_text().splitlines()]
                        torch_calls = [call for call in calls if any(arg.startswith("torch==") for arg in call)]
                        tensor_calls = [call for call in calls if any(arg.startswith("safetensors==") for arg in call)]
                        self.assertEqual(len(torch_calls), 1)
                        self.assertEqual(len(tensor_calls), 1)
                        parser = __import__("argparse").ArgumentParser()
                        parser.add_argument("--index-url")
                        options, _ = parser.parse_known_args(torch_calls[0])
                        self.assertEqual(options.index_url, "https://download.pytorch.org/whl/cpu")

    def test_deep_suites_are_manual_and_broker_qualification_is_separate(self):
        for name in ("canonical-full-suite.yml", "heptadll-integration.yml"):
            ci = workflow(name)
            self.assertEqual(set(ci["on"]), {"workflow_dispatch"})
            self.assertTrue(ci["jobs"])
        release = workflow("release.yml")
        self.assertIn("tags", release["on"]["push"])
        self.step(release, "packaging_tests")
        self.assertIn("scripts/accept_core_release.py", str(release))
        self.assertTrue((ROOT / ".github/retired-workflows/ib-paper-qualification.yml").is_file())
        for retired in ("ib-paper-qualification.yml", "self-hosted-ib-availability.yml"):
            self.assertFalse((ROOT / ".github/workflows" / retired).exists())

    def test_superseded_feedback_can_cancel_but_broker_run_cannot(self):
        ci = workflow("core-ci.yml")
        cancel = {"if": ci["concurrency"]["cancel-in-progress"]}
        for event, expected in (("push", True), ("pull_request", True),
                                ("workflow_dispatch", False)):
            with self.subTest(event=event):
                self.assertEqual(condition_value(cancel, {"github.event_name": event}), expected)
        self.assertIn("github.event_name", ci["concurrency"]["group"])
        retired = yaml.load(
            (ROOT / ".github/retired-workflows/ib-paper-qualification.yml").read_text(),
            Loader=yaml.BaseLoader)
        self.assertFalse(condition_value({"if": retired["concurrency"]["cancel-in-progress"]}, {}))


if __name__ == "__main__":
    unittest.main()
