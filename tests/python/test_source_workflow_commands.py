"""Execute the source workflow command block with inert command dependencies.

No CI job, repository credential, Broker or hosted administration is invoked.
"""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/documentation-control-plane.yml"
SHELLS = ("build_ib_candidate_artifact.sh", "run_ib_paper_artifact_qualification.sh")

class SourceWorkflowCommandTests(unittest.TestCase):
    def block(self) -> str:
        value = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
        self.assertIsInstance(value, dict)
        candidates = [step["run"] for step in value["jobs"]["documentation"]["steps"]
            if isinstance(step.get("run"), str) and "scripts/check_documentation.py" in step["run"]]
        self.assertEqual(len(candidates), 1)
        return candidates[0]

    def run_block(self, block: str, *, fail_script: str = "", broken_shell: str = ""):
        with tempfile.TemporaryDirectory(prefix="hepta-source-block-") as folder:
            root = Path(folder)
            binary = root / "bin"
            binary.mkdir()
            scripts = root / "scripts"
            scripts.mkdir()
            log = root / "calls"
            for name in SHELLS:
                (scripts / name).write_text("if\n" if name == broken_shell else "#!/bin/bash\nexit 0\n")
            for name, body in {
                "python3": '#!/bin/sh\nprintf "python %s\\n" "$*" >> "$CALLS"\n'
                           '[ "$1" != "$FAIL_SCRIPT" ] || exit 37\nexit 0\n',
                "git": '#!/bin/sh\nprintf "git %s\\n" "$*" >> "$CALLS"\nexit 0\n',
            }.items():
                target = binary / name
                target.write_text(body)
                target.chmod(0o755)
            env = dict(os.environ, PATH=str(binary) + os.pathsep + os.defpath,
                       CALLS=str(log), FAIL_SCRIPT=fail_script)
            result = subprocess.run(["/bin/bash", "--noprofile", "--norc", "-c", block],
                                    cwd=root, env=env, capture_output=True, text=True, timeout=10)
            return result.returncode, log.read_text() if log.exists() else ""

    def assert_controls_execute(self, block: str):
        code, calls = self.run_block(block)
        self.assertEqual(code, 0)
        self.assertIn("git diff --check\n", calls)
        self.assertIn("python scripts/verify_exact_git_index.py --root .\n", calls)
        self.assertIn("python scripts/check_documentation.py\n", calls)
        self.assertIn("python scripts/run_python_tests.py --lane source\n", calls)
        for shell in SHELLS:
            code, _ = self.run_block(block, broken_shell=shell)
            self.assertNotEqual(code, 0, f"syntax failure ignored: {shell}")
        code, calls = self.run_block(block, fail_script="scripts/verify_exact_git_index.py")
        self.assertEqual(code, 37)
        self.assertNotIn("python scripts/check_documentation.py\n", calls)

    def test_real_command_block_preserves_all_moved_controls(self):
        self.assert_controls_execute(self.block())

    def test_comment_or_noop_does_not_count_as_executed_control(self):
        for block in (
            self.block().replace("python3 scripts/verify_exact_git_index.py", "# python3 scripts/verify_exact_git_index.py"),
            "exit 0\n" + "\n".join("# " + line for line in self.block().splitlines()),
        ):
            with self.subTest(block=block), self.assertRaises(AssertionError):
                self.assert_controls_execute(block)

    def test_swallowed_failure_is_detected(self):
        block = self.block().replace("python3 scripts/verify_exact_git_index.py --root .",
                                   "python3 scripts/verify_exact_git_index.py --root . || true")
        with self.assertRaises(AssertionError):
            self.assert_controls_execute(block)

if __name__ == "__main__":
    unittest.main()
