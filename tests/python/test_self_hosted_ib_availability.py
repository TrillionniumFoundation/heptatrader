"""Execute the checkout-free probe with inert commands; never contact a Broker."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

from workflow_test_support import ROOT, shell, step, workflow


class SelfHostedIbAvailabilityWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.workflow = workflow("self-hosted-ib-availability.yml")
        self.job = self.workflow["jobs"]["ib-runner-probe"]

    def test_dispatch_only_and_checkout_free_custody(self):
        self.assertEqual(set(self.workflow["on"]), {"workflow_dispatch"})
        self.assertEqual(self.workflow["permissions"], {})
        self.assertEqual(self.job["permissions"], {})
        self.assertEqual(self.job["runs-on"]["group"], "trillionnium-ib-paper")
        self.assertEqual(set(self.job["runs-on"]["labels"]),
                         {"self-hosted", "linux", "x64", "heptatrader-ib-paper", "desktop-ib-paper"})
        self.assertFalse(any("uses" in item for item in self.job["steps"]))
        self.assertEqual({term.strip() for term in self.job["if"].split("&&")},
                         {"github.event_name == 'workflow_dispatch'", "github.ref == 'refs/heads/main'"})
        dispatch = step(self.job, "check-dispatch")
        self.assertEqual(dispatch["env"]["PROBE_REASON"], "${{ inputs.reason }}")
        self.assertNotIn("${{", dispatch["run"])  # user text must not become shell source
        isolation = step(self.job, "check-isolation")
        self.assertEqual(isolation["env"]["HOST_PROBE_SHA256"], "${{ vars.HEPTA_IB_PAPER_HOST_PROBE_SHA256 }}")

    def dispatch(self, **overrides):
        env = {"GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_REF": "refs/heads/main",
               "RUNNER_NAME": "desktop-ib-paper", "RUNNER_OS": "Linux", "RUNNER_ARCH": "X64",
               "PROBE_REASON": "operator-check", **overrides}
        with tempfile.TemporaryDirectory() as directory:
            return shell(step(self.job, "check-dispatch")["run"], Path(directory), env)

    def test_dispatch_authority_and_reason_are_executed(self):
        self.assertEqual(self.dispatch().returncode, 0)
        for field, value in (("GITHUB_EVENT_NAME", "push"), ("GITHUB_REF", "refs/heads/other"),
                             ("RUNNER_NAME", "other"), ("RUNNER_NAME", "x230-ib-paper"),
                             ("RUNNER_NAME", "desktop-ib-builder"), ("RUNNER_OS", "Windows"),
                             ("RUNNER_ARCH", "ARM64"), ("PROBE_REASON", "$(touch injected)"),
                             ("PROBE_REASON", ""), ("PROBE_REASON", "a" * 81)):
            with self.subTest(field=field, value=value):
                self.assertNotEqual(self.dispatch(**{field: value}).returncode, 0)

    def probe(self, body=None, *, use_nc=True, reachable=False, helper_exit=0,
              digest_override=None, metadata="root:root:555:1"):
        """Only fixed fixture path/metadata and external command results are seams."""
        body = body or step(self.job, "check-isolation")["run"]
        fixed = "probe=/usr/libexec/hepta-ib-paper-host-probe"
        self.assertEqual(body.count(fixed), 1, "keep production helper path fixed")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            commands = root / "bin"
            commands.mkdir()
            trace = root / "trace"
            helper = root / "probe"
            helper.write_text(f"#!{sys.executable}\nimport json, os, sys\n"
                              "with open(os.environ['TRACE'], 'a') as f: f.write(json.dumps(sys.argv[1:])+'\\n')\n"
                              f"raise SystemExit({helper_exit})\n")
            helper.chmod(0o555)
            for name in ("sha256sum", "awk"):
                (commands / name).symlink_to(shutil.which(name))
            for name, output, code in (("stat", metadata, 0), ("id", "994", 0),
                                       ("nc" if use_nc else "timeout", "", 0 if reachable else 1)):
                target = commands / name
                target.write_text(f"#!{sys.executable}\nimport os\n"
                                  f"with open(os.environ['TRACE'], 'a') as f: f.write('{name}\\n')\n"
                                  f"print({output!r})\nraise SystemExit({code})\n")
                target.chmod(0o755)
            # Real shell flow; only the immutable host path is relocated to a
            # private unprivileged fixture. timeout/nc cannot open a socket.
            executable = body.replace(fixed, 'probe="$TEST_PROBE"')
            digest = hashlib.sha256(helper.read_bytes()).hexdigest()
            result = shell(executable, root, {"PATH": str(commands), "TEST_PROBE": str(helper),
                                              "TRACE": str(trace), "HOST_PROBE_SHA256":
                                              digest if digest_override is None else digest_override})
            observations = trace.read_text().splitlines() if trace.exists() else []
            return result, observations

    def assert_isolation(self, body):
        for use_nc in (True, False):
            result, trace = self.probe(body, use_nc=use_nc, reachable=True)
            self.assertNotEqual(result.returncode, 0, (use_nc, result.stdout, trace))
            self.assertFalse(any(line.startswith("[") for line in trace), trace)

    def test_both_reachable_paths_stop_before_root_helper(self):
        self.assert_isolation(step(self.job, "check-isolation")["run"])

    def test_unreachable_paths_delegate_exact_identity_and_propagate_failure(self):
        for use_nc in (True, False):
            result, trace = self.probe(use_nc=use_nc)
            self.assertEqual(result.returncode, 0, result.stderr)
            argv = json.loads(next(line for line in trace if line.startswith("[")))
            self.assertEqual(argv, ["--paper-port", "4002", "--logical-execution-uid", "2003",
                                    "--execution-uid", "2003",
                                    "--runner-uid", "994"])
            self.assertNotEqual(self.probe(use_nc=use_nc, helper_exit=23)[0].returncode, 0)

    def test_unsafe_helper_is_rejected_before_probe(self):
        for kwargs in ({"digest_override": "0" * 64}, {"digest_override": "malformed"},
                       {"metadata": "agent:agent:555:1"}, {"metadata": "root:root:777:1"},
                       {"metadata": "root:root:555:2"}):
            result, trace = self.probe(**kwargs)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(any(x in trace for x in ("nc", "timeout")), trace)
            self.assertFalse(any(x.startswith("[") for x in trace), trace)

    def test_behavior_harness_kills_noop_and_negated_errexit_mutants(self):
        body = step(self.job, "check-isolation")["run"]
        # Losing explicit exit must be detectable even if all original safety
        # words survive in the shell block. `!` alone is exempt from errexit.
        mutants = [body.replace("exit 1", ": # disabled exit 1"),
                   body.replace("if nc -z -w 3 127.0.0.1 4002; then", "if ! nc -z -w 3 127.0.0.1 4002; then")]
        for mutant in mutants:
            with self.subTest(mutant=mutants.index(mutant)), self.assertRaises(AssertionError):
                self.assert_isolation(mutant)


if __name__ == "__main__":
    unittest.main()
