from __future__ import annotations

import contextlib
import copy
import io
import json
import math
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

# Reuse only synthetic fixture construction, not inherited tests/counts.
import test_gap_closure as fixtures

CHECKER = fixtures.CHECKER
BOUNDARY = fixtures.BOUNDARY


class GapClosureSnapshotTests(unittest.TestCase):
    """Local evidence-consistency tests; no live receipt or authority is issued."""
    def setUp(self) -> None:
        self.fixture = fixtures.GapClosureTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        self.fixture.validate()

    def evaluate(self, **kwargs: object) -> tuple[list[str], dict | None]:
        return CHECKER.evaluate(self.fixture.gap_path, self.fixture.module_path,
                                repository_root=self.root, **kwargs)

    def cli(self) -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            status = CHECKER.main(["--gap-registry", str(self.fixture.gap_path),
                                   "--module-registry", str(self.fixture.module_path),
                                   "--repository-root", str(self.root), "--json"])
        return status, stdout.getvalue(), stderr.getvalue()

    def test_cli_uses_validated_gap_snapshot_after_path_replacement(self) -> None:
        original = Path.read_bytes
        changed = False
        reads: list[Path] = []
        def read(path: Path) -> bytes:
            nonlocal changed
            data = original(path)
            reads.append(path)
            if path == self.fixture.module_path and not changed:
                changed = True
                replacement = copy.deepcopy(self.fixture.gaps)
                replacement["gaps"][1]["state"] = "closed"  # No receipt exists.
                tmp = self.fixture.write_json("replacement.json", replacement)
                tmp.replace(self.fixture.gap_path)
            return data
        with mock.patch.object(Path, "read_bytes", read):
            status, output, errors = self.cli()
        self.assertEqual((0, ""), (status, errors))
        summary = json.loads(output)
        self.assertEqual(["G-IB-001", "G-TEAM-001"], summary["external_open"])
        self.assertEqual([], summary["external_closed_with_receipt"])
        self.assertFalse(summary["grants_qualification"])
        self.assertEqual(1, reads.count(self.fixture.gap_path))
        self.assertEqual(1, reads.count(self.fixture.module_path))
        self.assertTrue(self.evaluate()[0])  # A fresh evaluation sees the changed file.

    def test_cli_does_not_reopen_module_policy_or_deleted_inputs_to_report(self) -> None:
        original = Path.read_bytes
        def read(path: Path) -> bytes:
            data = original(path)
            if path == self.fixture.module_path:
                self.fixture.gap_path.unlink()
                self.fixture.module_path.write_text("{}")
            return data
        with mock.patch.object(Path, "read_bytes", read):
            status, output, errors = self.cli()
        self.assertEqual((0, ""), (status, errors))
        self.assertEqual(["G-IB-001", "G-TEAM-001"], json.loads(output)["external_open"])

    def test_summary_is_not_an_unchecked_closure_projection(self) -> None:
        self.fixture.gaps["gaps"][1]["state"] = "closed"
        self.fixture.write_json("gaps.json", self.fixture.gaps)
        with self.assertRaisesRegex(ValueError, "qualification_receipt"):
            CHECKER.summary(self.fixture.gap_path, self.fixture.module_path,
                            repository_root=self.root, expected_source_sha=fixtures.SOURCE)
        errors, report = self.evaluate(expected_source_sha=fixtures.SOURCE)
        self.assertTrue(errors)
        self.assertIsNone(report)

    def test_prior_validation_never_authorizes_later_summary(self) -> None:
        self.assertEqual([], self.evaluate()[0])
        self.fixture.gaps["gaps"][0]["state"] = "in-progress"
        self.fixture.write_json("gaps.json", self.fixture.gaps)
        with self.assertRaisesRegex(ValueError, "repository-executable gap"):
            CHECKER.summary(self.fixture.gap_path, self.fixture.module_path)

    def test_closed_summary_requires_its_own_independent_source_selection(self) -> None:
        self.fixture.install_ib()
        self.assertEqual([], self.fixture.validate())
        with self.assertRaisesRegex(ValueError, "requires exact source identity"):
            CHECKER.summary(self.fixture.gap_path, self.fixture.module_path)
        report = CHECKER.summary(self.fixture.gap_path, self.fixture.module_path,
                                 repository_root=self.root, expected_source_sha=fixtures.SOURCE)
        self.assertEqual(["G-IB-001"], report["external_closed_with_receipt"])
        self.assertFalse(report["grants_qualification"])
        self.assertFalse(report["external_evidence_synthesized"])

    def test_invalid_evaluation_and_cli_publish_no_success(self) -> None:
        for data in ("{}", "[]", '{"gaps":NaN}', '{"gaps":1e999}', '{"x":1,"x":2}'):
            with self.subTest(data=data):
                self.fixture.gap_path.write_text(data)
                errors, report = self.evaluate()
                self.assertTrue(errors)
                self.assertIsNone(report)
                code, stdout, stderr = self.cli()
                self.assertEqual(1, code)
                self.assertEqual("", stdout)
                self.assertIn("[GAP-CLOSURE]", stderr)

    def test_returned_report_is_not_cached_approval(self) -> None:
        errors, first = self.evaluate()
        self.assertEqual([], errors)
        self.assertIsNotNone(first)
        first["external_open"].clear()
        first["grants_qualification"] = True
        errors, second = self.evaluate()
        self.assertEqual([], errors)
        self.assertEqual(["G-IB-001", "G-TEAM-001"], second["external_open"])
        self.assertFalse(second["grants_qualification"])

    def test_overflowed_json_numbers_reject_at_any_depth(self) -> None:
        for token in ("1e309", "-1e309", "1E+999", "-9.9e999999", "NaN", "Infinity", "-Infinity"):
            for template in ('{"x":%s}', '{"x":[{"y":%s}]}'):
                with self.subTest(token=token, template=template):
                    with self.assertRaisesRegex(ValueError, "non-finite JSON"):
                        BOUNDARY.decode_object((template % token).encode())

    def test_finite_json_numeric_semantics_are_retained(self) -> None:
        data = BOUNDARY.decode_object(b'{"a":1.7976931348623157e308,"b":5e-324,"c":-0.0,"d":7,"e":true}')
        self.assertTrue(math.isfinite(data["a"]))
        self.assertGreater(data["b"], 0)
        self.assertEqual(-1, math.copysign(1, data["c"]))
        self.assertIs(type(data["d"]), int)
        self.assertIs(type(data["e"]), bool)
        self.assertFalse(CHECKER._positive(True))
        self.assertFalse(CHECKER._positive(1.0))

    def test_ignored_overflow_in_receipt_and_both_registries_rejects(self) -> None:
        receipt = self.fixture.install_ib()
        self.fixture.validate()
        for path in (receipt, self.fixture.gap_path, self.fixture.module_path):
            with self.subTest(path=path.name):
                original = path.read_text()
                path.write_text(original.rstrip()[:-1] + ',"extra":[1e999]}')
                errors, report = self.evaluate(expected_source_sha=fixtures.SOURCE)
                self.assertTrue(any("non-finite JSON" in e for e in errors), errors)
                self.assertIsNone(report)
                path.write_text(original)

    def git(self, *args: str) -> str:
        return subprocess.check_output(["git", "-C", str(self.root), *args], text=True).strip()

    def tracked_source(self) -> str:
        (self.root / "source.py").write_text("original = True\n")
        return self.fixture.initialize_git(self.root)

    def test_assume_unchanged_and_skip_worktree_do_not_hide_mutation(self) -> None:
        source = self.tracked_source()
        for flag in ("assume-unchanged", "skip-worktree"):
            with self.subTest(flag=flag):
                self.git("update-index", "--" + flag, "source.py")
                (self.root / "source.py").write_text("mutated = True\n")
                self.assertEqual("", self.git("status", "--porcelain"))
                index = (self.root / ".git/index").read_bytes()
                errors: list[str] = []
                self.assertIsNone(CHECKER._source_identity(self.root, source, errors))
                self.assertTrue(any("candidate index hides" in e for e in errors), errors)
                self.assertEqual(index, (self.root / ".git/index").read_bytes())
                self.git("update-index", "--no-" + flag, "source.py")
                (self.root / "source.py").write_text("original = True\n")

    def test_clean_hidden_flags_also_require_an_inspectable_checkout(self) -> None:
        source = self.tracked_source()
        for flag in ("assume-unchanged", "skip-worktree"):
            self.git("update-index", "--" + flag, "source.py")
            errors: list[str] = []
            self.assertIsNone(CHECKER._source_identity(self.root, source, errors))
            self.git("update-index", "--no-" + flag, "source.py")
        errors = []
        self.assertEqual(source, CHECKER._source_identity(self.root, source, errors))
        self.assertEqual([], errors)

    def test_git_filename_framing_uses_nul_not_lines(self) -> None:
        (self.root / "file\nH with spaces.py").write_text("value = 1\n")
        source = self.fixture.initialize_git(self.root)
        errors: list[str] = []
        self.assertEqual(source, CHECKER._source_identity(self.root, source, errors))
        self.assertEqual([], errors)

    def test_fsmonitor_hook_is_not_executed_and_index_is_not_refreshed(self) -> None:
        source = self.tracked_source()
        hook = self.root / ".git/fsmonitor-fixture"
        marker = self.root / ".git/hook-ran"
        hook.write_text('#!/bin/sh\n: > "' + str(marker) + '"\nprintf "token\\0"\n')
        hook.chmod(0o700)
        self.git("config", "core.fsmonitor", str(hook))
        index = (self.root / ".git/index").read_bytes()
        errors: list[str] = []
        self.assertEqual(source, CHECKER._source_identity(self.root, source, errors))
        self.assertEqual([], errors)
        self.assertFalse(marker.exists())
        self.assertEqual(index, (self.root / ".git/index").read_bytes())

    def test_broken_git_metadata_cannot_fall_back_to_archive(self) -> None:
        (self.root / ".git").symlink_to(self.root / "missing-git")
        errors: list[str] = []
        self.assertIsNone(CHECKER._source_identity(self.root, fixtures.SOURCE, errors))
        self.assertTrue(any("metadata unreadable" in e for e in errors), errors)

    def detached_git_fixture(self) -> tuple[str, Path]:
        self.fixture.close("G-IB-001", "receipts/ib.json")
        self.fixture.write_json("gaps.json", self.fixture.gaps)
        source = self.tracked_source()
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        evidence = Path(directory.name)
        (evidence / "receipts").mkdir()
        payload = self.fixture.ib_receipt()
        payload["git_sha"] = source
        (evidence / "receipts/ib.json").write_text(json.dumps(payload))
        return source, evidence

    def test_changed_source_during_receipt_checks_has_no_projection(self) -> None:
        source, evidence = self.detached_git_fixture()
        original = CHECKER.read_receipt
        def read(root: Path, name: object) -> dict:
            payload = original(root, name)
            (self.root / "source.py").write_text("changed_during_receipt = True\n")
            return payload
        with mock.patch.object(CHECKER, "read_receipt", read):
            errors, report = self.evaluate(expected_source_sha=source, receipt_root=evidence)
        self.assertTrue(any("worktree is not clean" in e for e in errors), errors)
        self.assertIsNone(report)

    def test_new_head_is_not_implicitly_selected_after_receipt_checks(self) -> None:
        _, evidence = self.detached_git_fixture()
        original = CHECKER.read_receipt
        def read(root: Path, name: object) -> dict:
            payload = original(root, name)
            self.git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                     "commit", "--allow-empty", "-qm", "new head during verification")
            return payload
        with mock.patch.object(CHECKER, "read_receipt", read):
            errors, report = self.evaluate(receipt_root=evidence)
        self.assertTrue(any("does not match repository HEAD" in e for e in errors), errors)
        self.assertIsNone(report)

    def test_disappearing_git_metadata_does_not_become_an_archive(self) -> None:
        source, evidence = self.detached_git_fixture()
        original = CHECKER.read_receipt
        def read(root: Path, name: object) -> dict:
            payload = original(root, name)
            (self.root / ".git").rename(evidence / "removed-git")
            return payload
        with mock.patch.object(CHECKER, "read_receipt", read):
            errors, report = self.evaluate(expected_source_sha=source, receipt_root=evidence)
        self.assertTrue(any("Git metadata disappeared" in e for e in errors), errors)
        self.assertIsNone(report)

    def test_detached_closed_evaluation_keeps_checkout_unchanged(self) -> None:
        source, evidence = self.detached_git_fixture()
        index = (self.root / ".git/index").read_bytes()
        errors, report = self.evaluate(expected_source_sha=source, receipt_root=evidence)
        self.assertEqual([], errors)
        self.assertEqual(["G-IB-001"], report["external_closed_with_receipt"])
        self.assertFalse(report["grants_qualification"])
        self.assertEqual(index, (self.root / ".git/index").read_bytes())
        self.assertEqual("", self.git("status", "--porcelain"))
        self.assertFalse((self.root / "receipts").exists())


if __name__ == "__main__":
    unittest.main()
