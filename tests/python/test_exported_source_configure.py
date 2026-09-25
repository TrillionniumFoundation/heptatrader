"""Exercise the real top-level CMake configure from a Git archive without .git.

This is broker-disabled and performs no network or credential access.  It proves
that the exported-source path used by the IB candidate builder can render the
installed documentation when the already-verified source SHA is supplied.
"""
from __future__ import annotations

from pathlib import Path
import re
import os
import hashlib
import json
import sys
from unittest import mock
import subprocess
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
BUILDER = ROOT / "scripts/build_ib_candidate_artifact.sh"


class ExportedSourceConfigureTests(unittest.TestCase):
    def test_candidate_builder_binds_documentation_to_verified_sha(self) -> None:
        text = BUILDER.read_text(encoding="utf-8")
        configure = re.search(
            r"cmake -S /src -B /build/work -G Ninja \\\n(?P<body>(?:\s+-D[^\n]+\\\n)+)",
            text,
        )
        self.assertIsNotNone(configure)
        self.assertIn('-DHEPTA_DOCUMENTATION_SOURCE_SHA="$EXPECTED_SHA"', configure.group("body"))

    def test_candidate_builder_separates_host_nproc_from_container_pids(self) -> None:
        text = BUILDER.read_text(encoding="utf-8")
        self.assertIn('PIDS_LIMIT="${HEPTA_IB_BUILD_PIDS_LIMIT:-256}"', text)
        self.assertIn('NPROC_LIMIT="${HEPTA_IB_BUILD_NPROC_LIMIT:-65535}"', text)
        self.assertIn('--pids-limit "$PIDS_LIMIT"', text)
        self.assertIn('--ulimit nproc="$NPROC_LIMIT:$NPROC_LIMIT"', text)
        self.assertNotIn('--ulimit nproc="$PIDS_LIMIT:$PIDS_LIMIT"', text)
        self.assertIn('"nproc_rlimit": int(nproc)', text)

    def test_readonly_probe_build_phase_propagates_behavior_failure(self) -> None:
        text = BUILDER.read_text(encoding="utf-8")
        phase = re.search(r"if ! timeout --signal=TERM --kill-after=5s 60s .*?\nfi", text, re.S)
        self.assertIsNotNone(phase)
        with tempfile.TemporaryDirectory(prefix="hepta-probe-build-phase-") as folder:
            root = Path(folder)
            stub, calls, log = root / "docker", root / "calls", root / "build.log"
            stub.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$RECORD"\nexit "$STUB_STATUS"\n')
            stub.chmod(0o700)
            for status in (0, 37):
                with self.subTest(status=status):
                    log.write_text("")
                    script = 'set -euo pipefail\nCOMMON_DOCKER=("$STUB" --network none)\n'
                    script += phase.group(0) + '\nprintf "PHASE_COMPLETED\\n"\n'
                    result = subprocess.run(["bash", "-c", script], capture_output=True,
                        text=True, timeout=5, env={**os.environ, "STUB": str(stub),
                            "RECORD": str(calls), "STUB_STATUS": str(status),
                            "BUILD_LOG": str(log), "BUILDER_IMAGE": "synthetic-image"})
                    self.assertEqual(result.returncode, 0 if status == 0 else 70,
                                     result.stdout + result.stderr)
                    self.assertEqual("PHASE_COMPLETED" in result.stdout, status == 0)
                    self.assertEqual(calls.read_text().splitlines(), [
                        "--network", "none", "synthetic-image", "python3", "-I", "-B",
                        "/src/tests/ib_connection_probe_behavior.py", "--probe",
                        "/build/work/docs/ib_probe/ib_connection_probe"])

    def test_real_exported_source_configures_without_git_metadata(self) -> None:
        source_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True,
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        self.assertRegex(source_sha, r"^[0-9a-f]{40}$")
        with tempfile.TemporaryDirectory(prefix="hepta-exported-configure-") as folder:
            root = Path(folder)
            archive = root / "source.tar"
            source = root / "source"
            build = root / "build"
            with archive.open("wb") as stream:
                subprocess.run(
                    ["git", "archive", "--format=tar", source_sha], cwd=ROOT,
                    check=True, stdout=stream, timeout=30,
                )
            source.mkdir()
            with tarfile.open(archive, "r:") as bundle:
                bundle.extractall(source)
            self.assertFalse((source / ".git").exists())
            result = subprocess.run(
                [
                    "cmake", "-S", str(source), "-B", str(build), "-G", "Ninja",
                    "-DCMAKE_BUILD_TYPE=Release",
                    "-DBUILD_TESTING=OFF",
                    "-DHEPTA_ENABLE_IBAPI=OFF",
                    f"-DHEPTA_DOCUMENTATION_SOURCE_SHA={source_sha}",
                ],
                capture_output=True, text=True, timeout=120,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            rendered = build / "installed-documentation"
            self.assertTrue((rendered / "index.md").is_file())
            self.assertIn(source_sha, (rendered / "index.md").read_text(encoding="utf-8"))


class BuilderDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, str(ROOT / "scripts"))
        import verify_ib_candidate_artifact
        self.artifact = verify_ib_candidate_artifact
        self.folder = tempfile.TemporaryDirectory(prefix="hepta-builder-diagnostic-")
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.log = self.root / "compiler.log"
        self.log.write_bytes(b"::error::candidate text stays in an inert artifact\ncompile failed\n")
        self.log.chmod(0o600)
        self.output = self.root / "diagnostics"

    def capture(self, status=70):
        self.artifact.capture_builder_diagnostics(self.log, "a" * 40, "compile", status, self.output)
        return json.loads((self.output / "build-status.json").read_text())

    def test_actual_error_bytes_and_failure_identity_are_retained(self):
        value = self.capture()
        self.assertEqual(value["candidate_sha"], "a" * 40)
        self.assertEqual(value["exit_code"], 70)
        self.assertEqual(value["phase"], "compile")
        self.assertFalse(value["log_truncated"])
        self.assertEqual((self.output / "candidate-build.log").read_bytes(), self.log.read_bytes())
        self.assertEqual(value["retained_log_sha256"], hashlib.sha256(self.log.read_bytes()).hexdigest())
        self.assertEqual((self.output.stat().st_mode & 0o777), 0o700)
        self.assertEqual({p.name for p in self.output.iterdir()}, {"candidate-build.log", "build-status.json"})
        self.assertNotIn(str(self.root), (self.output / "build-status.json").read_text())

    def test_large_logs_keep_only_a_bounded_tail(self):
        data = b"older output\n" + b"x" * self.artifact.MAX_DIAGNOSTIC_LOG_BYTES + b"final diagnostic\n"
        self.log.write_bytes(data)
        value = self.capture()
        kept = (self.output / "candidate-build.log").read_bytes()
        self.assertEqual(kept, data[-self.artifact.MAX_DIAGNOSTIC_LOG_BYTES:])
        self.assertEqual(value["source_log_bytes"], len(data))
        self.assertEqual(value["retained_log_bytes"], len(kept))
        self.assertTrue(value["log_truncated"])
        self.assertEqual(value["retained_log_sha256"], hashlib.sha256(kept).hexdigest())

    def test_precompiler_failure_is_not_an_invented_log(self):
        self.log.unlink()
        value = self.capture(78)
        self.assertIsNone(value["source_log_bytes"])
        self.assertEqual(value["retained_log_bytes"], 0)
        self.assertEqual(value["exit_code"], 78)

    def test_symlink_fifo_and_public_log_are_refused_without_reading(self):
        self.log.unlink()
        secret = self.root / "not-a-build-log"
        secret.write_text("must-not-copy")
        for kind in ("link", "fifo", "public"):
            with self.subTest(kind=kind):
                if kind == "link": self.log.symlink_to(secret)
                elif kind == "fifo": os.mkfifo(self.log, 0o600)
                else:
                    self.log.write_text("public")
                    self.log.chmod(0o644)
                with self.assertRaises((OSError, self.artifact.ArtifactError)):
                    self.capture()
                self.assertFalse(self.output.exists())
                self.log.unlink()

    def test_existing_output_is_never_overwritten_or_followed(self):
        self.capture()
        before = (self.output / "build-status.json").read_bytes()
        with self.assertRaises(FileExistsError): self.capture(0)
        self.assertEqual((self.output / "build-status.json").read_bytes(), before)
        target = self.root / "absent-directory"
        self.output = self.root / "linked-output"
        self.output.symlink_to(target)
        with self.assertRaises(FileExistsError): self.capture()
        self.assertFalse(target.exists())

    def test_log_change_during_read_is_rejected(self):
        pread = os.pread
        def changed(fd, count, offset):
            result = pread(fd, count, offset)
            with self.log.open("ab") as stream: stream.write(b"late")
            return result
        with mock.patch.object(self.artifact.os, "pread", side_effect=changed):
            with self.assertRaises(self.artifact.ArtifactError): self.capture()
        self.assertFalse(self.output.exists())

    def test_real_shell_cleanup_preserves_diagnostic_before_removing_work(self):
        text = BUILDER.read_text()
        begin = text.index("cleanup() {\n")
        end = text.index('SOURCE_ROOT="$WORK_ROOT/source"', begin)
        for status in (0, 70, 143):
            with self.subTest(status=status):
                work = self.root / f"work-{status}"
                work.mkdir()
                log = work / "candidate-build.log"
                log.write_bytes(b"real compiler diagnostic\n")
                log.chmod(0o600)
                output = self.root / f"candidate-{status}.tar"
                env = {**os.environ, "TRUSTED_ROOT": str(ROOT), "WORK_ROOT": str(work),
                       "EXPECTED_SHA": "a" * 40, "BUILD_PHASE": "configure", "BUILD_LOG": str(log),
                       "ARTIFACT_OUTPUT": str(output)}
                command = 'kill -TERM "$$"' if status == 143 else f"exit {status}"
                result = subprocess.run(["bash", "-c", "set -euo pipefail\n" + text[begin:end] + command],
                                        capture_output=True, text=True, timeout=15, env=env)
                self.assertEqual(result.returncode, status, result.stdout + result.stderr)
                self.assertNotIn("real compiler diagnostic", result.stdout)
                self.assertFalse(work.exists())
                diagnostic = Path(str(output) + ".diagnostics")
                self.assertEqual((diagnostic / "candidate-build.log").read_bytes(), b"real compiler diagnostic\n")
                value = json.loads((diagnostic / "build-status.json").read_text())
                self.assertEqual(value["exit_code"], status)

    def test_cleanup_diagnostic_failure_never_turns_build_failure_into_success(self):
        text = BUILDER.read_text()
        begin = text.index("cleanup() {\n")
        end = text.index('SOURCE_ROOT="$WORK_ROOT/source"', begin)
        for status, expected in ((70, 70), (0, 74)):
            work = self.root / f"conflict-work-{status}"
            work.mkdir()
            output = self.root / f"conflict-{status}.tar"
            diagnostic = Path(str(output) + ".diagnostics")
            diagnostic.mkdir()
            sentinel = diagnostic / "existing"
            sentinel.write_text("preserve")
            env = {**os.environ, "TRUSTED_ROOT": str(ROOT), "WORK_ROOT": str(work),
                   "EXPECTED_SHA": "a" * 40, "BUILD_PHASE": "configure", "BUILD_LOG": str(work / "none"),
                   "ARTIFACT_OUTPUT": str(output)}
            result = subprocess.run(["bash", "-c", "set -euo pipefail\n" + text[begin:end] + f"exit {status}"],
                                    capture_output=True, text=True, timeout=15, env=env)
            self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
            self.assertEqual(sentinel.read_text(), "preserve")
            self.assertFalse(work.exists())


if __name__ == "__main__":
    unittest.main()
