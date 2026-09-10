from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[2]
PYTHON = sys.executable
TIMEOUT_SECONDS = 5


def run_snippet(source: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [PYTHON, "-c", textwrap.dedent(source)],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=TIMEOUT_SECONDS,
        check=False,
    )


class SpecialFileAdmissionTests(unittest.TestCase):
    def assert_expected_rejection(self, completed: subprocess.CompletedProcess[str]) -> None:
        self.assertEqual(
            completed.returncode,
            0,
            msg=f"stdout={completed.stdout!r}\nstderr={completed.stderr!r}",
        )
        self.assertIn("EXPECTED_REJECTION", completed.stdout)
        self.assertNotIn('"result":"PASS"', completed.stdout)

    def test_fifo_policy_is_rejected_without_blocking_or_receipt(self) -> None:
        completed = run_snippet(
            r'''
            import os
            from pathlib import Path
            import subprocess
            import sys
            import tempfile

            with tempfile.TemporaryDirectory() as directory:
                fifo = Path(directory) / "policy.json"
                receipt = Path(directory) / "receipt.json"
                os.mkfifo(fifo)
                command = [
                    sys.executable,
                    "scripts/hepta_preflight.py",
                    "--artifact", str(Path(directory) / "missing.tar.gz"),
                    "--expected-sha256", "0" * 64,
                    "--profile", "core",
                    "--policy", str(fifo),
                    "--artifact-only",
                    "--output", str(receipt),
                ]
                child = subprocess.run(
                    command,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=2,
                    check=False,
                )
                assert child.returncode != 0, child
                assert not receipt.exists(), receipt
                assert '"result":"PASS"' not in child.stdout
                print("EXPECTED_REJECTION")
            '''
        )
        self.assert_expected_rejection(completed)

    def test_fifo_artifact_is_rejected_without_blocking_or_pass_receipt(self) -> None:
        completed = run_snippet(
            r'''
            import json
            import os
            from pathlib import Path
            import subprocess
            import sys
            import tempfile

            policy = Path("docs/preflight-policy-v1.json").resolve()
            with tempfile.TemporaryDirectory() as directory:
                fifo = Path(directory) / "artifact.tar.gz"
                receipt = Path(directory) / "receipt.json"
                os.mkfifo(fifo)
                child = subprocess.run(
                    [
                        sys.executable,
                        "scripts/hepta_preflight.py",
                        "--artifact", str(fifo),
                        "--expected-sha256", "0" * 64,
                        "--profile", "core",
                        "--policy", str(policy),
                        "--artifact-only",
                        "--output", str(receipt),
                    ],
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=2,
                    check=False,
                )
                assert child.returncode != 0, child
                assert '"result":"PASS"' not in child.stdout
                if receipt.exists():
                    value = json.loads(receipt.read_text(encoding="utf-8"))
                    assert value["result"] == "FAIL", value
                    assert value["authorization_effect"] == "NONE", value
                    assert value["paper_authorized"] is False, value
                    assert value["live_authorized"] is False, value
                print("EXPECTED_REJECTION")
            '''
        )
        self.assert_expected_rejection(completed)

    def test_fifo_installed_leaf_is_rejected_without_blocking(self) -> None:
        completed = run_snippet(
            r'''
            import importlib.util
            import os
            from pathlib import Path
            import sys
            import tempfile

            path = Path("scripts/hepta_preflight_core.py").resolve()
            spec = importlib.util.spec_from_file_location("preflight_core_fifo_installed", path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                leaf = root / "usr/bin/heptactl"
                leaf.parent.mkdir(parents=True)
                os.mkfifo(leaf)
                root_fd = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
                try:
                    try:
                        with module._open_pinned_regular_at(
                            root_fd, "usr/bin/heptactl", "installed heptactl"
                        ):
                            raise AssertionError("FIFO was admitted")
                    except module.PreflightError:
                        print("EXPECTED_REJECTION")
                finally:
                    os.close(root_fd)
            '''
        )
        self.assert_expected_rejection(completed)

    def test_regular_to_fifo_builder_swap_is_rejected_without_blocking(self) -> None:
        completed = run_snippet(
            r'''
            import importlib.util
            import os
            from pathlib import Path
            import sys
            import tempfile

            path = Path("scripts/build_release_package.py").resolve()
            spec = importlib.util.spec_from_file_location("release_builder_fifo_swap", path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            with tempfile.TemporaryDirectory() as directory:
                source = Path(directory) / "payload"
                source.write_bytes(b"ordinary")
                observed = source.lstat()
                source.unlink()
                os.mkfifo(source)
                try:
                    module._snapshot_source(source, observed, "payload")
                    raise AssertionError("regular-to-FIFO swap was admitted")
                except module.PackageError:
                    print("EXPECTED_REJECTION")
            '''
        )
        self.assert_expected_rejection(completed)

    def test_fifo_preflight_core_is_rejected_without_blocking(self) -> None:
        completed = run_snippet(
            r'''
            import importlib.util
            import os
            from pathlib import Path
            import shutil
            import sys
            import tempfile

            source = Path("scripts/hepta_preflight.py").resolve()
            with tempfile.TemporaryDirectory() as directory:
                wrapper = Path(directory) / "hepta_preflight.py"
                shutil.copy2(source, wrapper)
                os.mkfifo(Path(directory) / "hepta_preflight_core.py")
                spec = importlib.util.spec_from_file_location("preflight_wrapper_fifo_core", wrapper)
                module = importlib.util.module_from_spec(spec)
                sys.modules[spec.name] = module
                try:
                    spec.loader.exec_module(module)
                    raise AssertionError("FIFO core was admitted")
                except RuntimeError:
                    print("EXPECTED_REJECTION")
            '''
        )
        self.assert_expected_rejection(completed)

    def test_regular_file_positive_control(self) -> None:
        completed = run_snippet(
            r'''
            import importlib.util
            from pathlib import Path
            import sys
            import tempfile

            path = Path("scripts/hepta_preflight_core.py").resolve()
            spec = importlib.util.spec_from_file_location("preflight_core_regular_control", path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            with tempfile.TemporaryDirectory() as directory:
                regular = Path(directory) / "ordinary"
                regular.write_bytes(b"ordinary")
                with module._open_pinned_regular(regular, "ordinary") as (stream, *_):
                    assert stream.read() == b"ordinary"
                print("EXPECTED_REJECTION")
            '''
        )
        self.assert_expected_rejection(completed)


if __name__ == "__main__":
    unittest.main()
