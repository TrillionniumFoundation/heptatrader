"""Behavioral regressions for development validation, not deployment evidence."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import check_module_implementation_evidence as evidence
import check_repository_integrity as repository
import hepta_document_checks as documents
from hepta_document_metadata import META, missing_metadata
import test_module_implementation_evidence as existing_evidence


class EvidenceInputTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        existing_evidence.ModuleImplementationEvidenceTests()._fixture(self.root)
        self.registry_path = self.root / "docs/modules/module-registry-v2.json"
        self.original = json.loads(self.registry_path.read_text())

    def write(self, value: object) -> None:
        self.registry_path.write_text(json.dumps(value), encoding="utf-8")

    def test_unhashable_entry_fields_return_diagnostics(self) -> None:
        for field, value in (
            ("excluded_scope", [{}]), ("excluded_scope", [[]]),
            ("excluded_scope", [None]), ("state", {}), ("state", []),
            ("external_gates", [{}]), ("external_gates", [[]]),
            ("source_evidence", [{}]), ("test_evidence", [[]]),
            ("resource_guardrail_profile", {}),
        ):
            with self.subTest(field=field, value=value):
                registry = json.loads(json.dumps(self.original))
                registry["implementation_evidence"][0][field] = value
                self.write(registry)
                errors = evidence.validate(self.root)
                self.assertTrue(any(field in error for error in errors), errors)

    def test_invalid_guardrail_enforcement_is_not_hashed(self) -> None:
        for value in ({}, [], None, True):
            with self.subTest(value=value):
                registry = json.loads(json.dumps(self.original))
                registry["resource_guardrail_profiles"]["test-profile"]["enforcement"] = value
                self.write(registry)
                self.assertTrue(any("enforcement" in e for e in evidence.validate(self.root)))

    def test_duplicate_and_empty_scope_strings_still_reject(self) -> None:
        for value in (["same", "same"], [""], ["   "], None, {}):
            with self.subTest(value=value):
                registry = json.loads(json.dumps(self.original))
                registry["implementation_evidence"][0]["excluded_scope"] = value
                self.write(registry)
                self.assertTrue(evidence.validate(self.root))

    def test_empty_registries_never_report_success(self) -> None:
        self.write({})
        self.assertTrue(evidence.validate(self.root))
        self.write(self.original)
        (self.root / "docs/program/gap-registry-v2.json").write_text("{}")
        self.assertTrue(evidence.validate(self.root))

    def test_malformed_and_duplicate_gap_records_reject(self) -> None:
        path = self.root / "docs/program/gap-registry-v2.json"
        gate = {"id": "G-EXTERNAL-001", "state": "in-progress"}
        for value in (None, {}, [None], [{"id": []}], [gate, gate]):
            with self.subTest(value=value):
                path.write_text(json.dumps({"gaps": value}))
                self.assertTrue(evidence.validate(self.root))

    def test_root_nul_and_escaping_evidence_paths_reject(self) -> None:
        for value in (".", "../", str(self.root), "src/\x00bad"):
            with self.subTest(value=value):
                registry = json.loads(json.dumps(self.original))
                registry["implementation_evidence"][0]["source_evidence"] = [value]
                self.write(registry)
                self.assertTrue(evidence.validate(self.root))

    def test_unsafe_manifest_paths_reject_before_open(self) -> None:
        for value in ("../outside.json", str(self.root / "outside.json"), "bad\x00.json"):
            with self.subTest(value=value):
                registry = json.loads(json.dumps(self.original))
                registry["manifest_paths"] = [value]
                self.write(registry)
                with mock.patch.object(evidence, "_read_json", wraps=evidence._read_json) as reader:
                    self.assertTrue(evidence.validate(self.root))
                    self.assertEqual(reader.call_count, 2, "unsafe manifest must not be read")

    def test_explicit_roots_do_not_modify_default_or_globals(self) -> None:
        original_globals = (evidence.ROOT, evidence.REGISTRY_PATH, evidence.GAP_REGISTRY_PATH)
        with tempfile.TemporaryDirectory() as directory:
            other = Path(directory)
            existing_evidence.ModuleImplementationEvidenceTests()._fixture(other)
            (other / "tests/test_boundary.py").unlink()
            with mock.patch.object(evidence, "ROOT", self.root):
                self.assertTrue(evidence.validate(other))
                self.assertEqual(evidence.ROOT, self.root)
                self.assertEqual([], evidence.validate())
        self.assertEqual(original_globals,
                         (evidence.ROOT, evidence.REGISTRY_PATH, evidence.GAP_REGISTRY_PATH))

    def test_parallel_roots_have_independent_results(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            other = Path(directory)
            existing_evidence.ModuleImplementationEvidenceTests()._fixture(other)
            (other / "tests/test_boundary.py").unlink()
            roots = [self.root, other] * 16
            with ThreadPoolExecutor(max_workers=4) as pool:
                results = list(pool.map(evidence.validate, roots))
            for index, errors in enumerate(results):
                self.assertEqual(bool(errors), bool(index % 2), (index, errors))


class MetadataTests(unittest.TestCase):
    def repository_errors(self, path: Path, canonical_return: int = 0) -> list[str]:
        # Isolate repository-local document checks from the larger canonical
        # graph. The separate document checker below executes real check_markdown.
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(repository, "ROOT", path.parent))
            stack.enter_context(mock.patch.object(repository, "REQUIRED_FILES", ()))
            stack.enter_context(mock.patch.object(repository, "_document_paths", return_value=[path]))
            stack.enter_context(mock.patch.object(repository, "_active_files", return_value=[]))
            child = stack.enter_context(mock.patch.object(
                repository.subprocess, "run",
                return_value=subprocess.CompletedProcess([], canonical_return, stdout="child result")))
            result = repository.validate()
            self.assertEqual(child.call_count, 1)
            self.assertEqual(Path(child.call_args.args[0][1]).name,
                             "check_documentation_control_plane.py")
            return result

    def test_both_consumers_share_metadata_contract(self) -> None:
        self.assertIs(repository.missing_metadata, documents.missing_metadata)
        self.assertIs(repository.META, documents.META)
        self.assertEqual(META, ("Status:", "Applies to:", "Verification:", "Authority:"))

    def test_header_window_boundaries_agree(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "guide.md"
            for position in (12, 13, 14, 15):
                with self.subTest(position=position):
                    lines = ["Status: current", "Applies to: test", "Authority: test"]
                    lines += [""] * (position - 1 - len(lines))
                    lines += ["Verification: direct tests"]
                    path.write_text("\n".join(lines), encoding="utf-8")
                    doc_errors: list[str] = []
                    with mock.patch.object(documents, "ROOT", path.parent):
                        documents.check_markdown(path, "normative", doc_errors)
                    repo_errors = self.repository_errors(path)
                    self.assertEqual(bool(doc_errors), position > 14, doc_errors)
                    self.assertEqual(bool(repo_errors), position > 14, repo_errors)

    def test_blank_missing_or_indented_fields_reject(self) -> None:
        for field in META:
            for replacement in ("", field + "   ", " " + field + " text"):
                lines = [replacement if item == field else item + " value" for item in META]
                with self.subTest(field=field, replacement=replacement):
                    self.assertIn(field, missing_metadata("\n".join(lines)))

    def test_canonical_failure_is_not_swallowed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "guide.md"
            path.write_text("\n".join(field + " value" for field in META))
            errors = self.repository_errors(path, canonical_return=1)
            self.assertTrue(any("documentation control plane failed" in e for e in errors))


class WorkflowLintTests(unittest.TestCase):
    def errors(self, command: str, permissions: str = "{contents: read}") -> list[str]:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflows = root / ".github/workflows"
            workflows.mkdir(parents=True)
            (workflows / "check.yml").write_text(
                "name: finalize report\non: workflow_dispatch\npermissions: " + permissions +
                "\njobs:\n  check:\n    runs-on: ubuntu-latest\n    steps:\n      - run: " +
                command + "\n")
            return repository.validate_workflows(root)

    def test_plain_text_is_not_a_finalizer(self) -> None:
        for command in ('echo "finalize"', "printf '%s\\n' finalizer", "echo self-merge",
                        "git diff --check", "gh pr view 17 --json state"):
            with self.subTest(command=command):
                self.assertEqual([], self.errors(command))

    def test_known_finalizer_invocations_still_reject(self) -> None:
        for command in ("python3 scripts/finalize_remaining_gaps.py",
                        "python3 -O scripts/finalize_remaining_gaps.py",
                        "./scripts/finalize_remaining_gaps.py", "bash scripts/close-gap.sh",
                        "echo ok && python3 scripts/finalize_remaining_gaps.py",
                        "|\n          python3 scripts/finalize_remaining_gaps.py"):
            with self.subTest(command=command):
                self.assertTrue(any("closure/finalizer command" in e for e in self.errors(command)))

    def test_actual_mutations_and_write_permissions_still_reject(self) -> None:
        for command in ("git push origin HEAD", "gh pr merge 17", "gh api -X POST /repos/x/y/issues",
                        "curl -XPOST https://example.test -d '{}'",
                        "rm .github/workflows/check.yml", "echo PASS > docs/program/gaps.json"):
            with self.subTest(command=command):
                self.assertTrue(self.errors(command))
        self.assertTrue(self.errors("git diff --check", "{contents: write}"))
        self.assertTrue(self.errors("git diff --check", "{checks: write}"))


class DevelopmentChainTests(unittest.TestCase):
    def run_chain(self, fail: str = "") -> tuple[subprocess.CompletedProcess[str], list[list[str]]]:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scripts = root / "scripts"
            scripts.mkdir()
            shutil.copy2(ROOT / "scripts/dev_core.sh", scripts / "dev_core.sh")
            (scripts / "run_ib_paper_qualification.sh").write_text("#!/bin/bash\nexit 0\n")
            tools = root / "fake-tools"
            tools.mkdir()
            log = root / "commands.jsonl"
            # Real Bash driver, fake tool endpoints: this proves orchestration
            # and failure propagation, never a CMake build or canonical PASS.
            shim = (f"#!{sys.executable}\nimport json, os, sys\nfrom pathlib import Path\n"
                    "with open(os.environ['CHAIN_LOG'], 'a') as out:\n"
                    " out.write(json.dumps([Path(sys.argv[0]).name] + sys.argv[1:]) + '\\n')\n"
                    "raise SystemExit(9 if os.environ.get('CHAIN_FAIL') and any(\n"
                    " Path(arg).name == os.environ['CHAIN_FAIL'] for arg in sys.argv[1:]) else 0)\n")
            for name in ("python3", "cmake", "ctest"):
                path = tools / name
                path.write_text(shim)
                path.chmod(0o755)
            env = {key: value for key, value in os.environ.items()
                   if not key.startswith("HEPTA_") and key != "RUNNER_TEMP"}
            env.update(PATH=str(tools) + os.pathsep + os.environ["PATH"],
                       CHAIN_LOG=str(log), CHAIN_FAIL=fail, HEPTA_JOBS="1",
                       HEPTA_RUN_PYTHON_TESTS="0")
            result = subprocess.run(["bash", str(scripts / "dev_core.sh")], cwd=root,
                                    env=env, text=True, capture_output=True, timeout=20)
            calls = [json.loads(line) for line in log.read_text().splitlines()]
            return result, calls

    def test_canonical_document_chain_has_one_direct_invocation(self) -> None:
        result, calls = self.run_chain()
        self.assertEqual(0, result.returncode, result.stderr)
        names = [Path(call[1]).name for call in calls if len(call) > 1]
        self.assertEqual(names.count("check_repository_integrity.py"), 1)
        self.assertNotIn("check_documentation_control_plane.py", names)
        self.assertNotIn("generate_documentation_views.py", names)
        self.assertIn("generate_contract_bindings.py", names)
        self.assertIn("check_module_discipline.py", names)
        self.assertIn("check_cmake_module_graph.py", names)
        self.assertTrue(any(call[0] == "ctest" and "--no-tests=error" in call for call in calls))

    def test_repository_failure_stops_before_build(self) -> None:
        result, calls = self.run_chain("check_repository_integrity.py")
        self.assertEqual(result.returncode, 9)
        self.assertFalse(any(call[0] in {"cmake", "ctest"} for call in calls))


if __name__ == "__main__":
    unittest.main()
