from __future__ import annotations

import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import check_gap_closure as CHECKER
import receipt_file_boundary as BOUNDARY

SOURCE = "1" * 40
MERGE = "2" * 40
CONTEXTS = ["documentation-control-plane-exact-head", "core-runtime-exact-head",
            "canonical-full-suite-core", "canonical-full-suite-reliability (g++)",
            "canonical-full-suite-reliability (clang++)", "exact-merge-candidate"]


class GapClosureTests(unittest.TestCase):
    """Envelope fixtures are synthetic tests, never qualification evidence."""
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.gap_path = self.root / "gaps.json"
        self.module_path = self.root / "modules.json"
        self.gaps = {
            "schema": "heptatrader.gap-registry.v2",
            "allowed_states": ["planned", "in-progress", "blocked", "closed"],
            "gaps": [{"id": "G-DOC-003", "state": "closed"},
                     {"id": "G-IB-001", "state": "in-progress"},
                     {"id": "G-TEAM-001", "state": "in-progress"}],
        }
        self.modules = {"schema": "heptatrader.module-registry.v2",
                        "implementation_evidence_policy": {
                            "external_gate_ids": ["G-IB-001", "G-TEAM-001"],
                            "external_gates_fail_closed": True}}
        self.write_json(".github/required-check-contexts-v1.json",
                        {"required_branch_contexts": CONTEXTS})

    def write_json(self, name: str, payload: object) -> Path:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def validate(self, **overrides: object) -> list[str]:
        self.write_json("gaps.json", self.gaps)
        self.write_json("modules.json", self.modules)
        arguments = {"expected_source_sha": SOURCE,
                     "expected_merge_group_sha": MERGE, "expected_pull_number": 17}
        arguments.update(overrides)
        return CHECKER.validate(self.gap_path, self.module_path,
                                repository_root=self.root, **arguments)

    def close(self, gate_id: str, receipt: str) -> None:
        item = next(g for g in self.gaps["gaps"] if g["id"] == gate_id)
        item.update(state="closed", qualification_receipt=receipt)

    def ib_receipt(self) -> dict:
        return {
            "schema": "hepta.ib-paper-qualification-verification.v1",
            "verified": True, "qualified": True, "git_sha": SOURCE,
            "binary": {"name": "hepta-ib-executiond", "sha256": "2" * 64},
            "harness": {"name": "ib-paper-harness", "sha256": "3" * 64},
            "result_sha256": "4" * 64,
            "broker": {"venue": "IB", "environment": "PAPER", "session_id": "fixture-only",
                       "account_fingerprint": "sha256:" + "5" * 64,
                       "host_fingerprint": "sha256:" + "6" * 64},
            "scenarios": [{"id": scenario, "status": "PASS", "evidence": [
                {"path": f"{scenario}/evidence.json", "kind": "oms-journal",
                 "sha256": "a" * 64, "size": 1}]} for scenario in sorted(CHECKER.IB_SCENARIOS)],
        }

    def governance_receipt(self) -> dict:
        body = {
            "schema": "heptatrader.github-governance-receipt.v1",
            "verified_at": "2026-09-03T00:00:00Z",
            "repository": "TrillionniumFoundation/heptatrader", "default_branch": "main",
            "pull_number": 17, "head_sha": SOURCE, "merge_group_sha": MERGE, "ruleset_id": 123,
            "team_slugs": ["architecture", "execution", "risk", "reliability"],
            "required_pull_request_contexts": list(CONTEXTS),
            "required_merge_group_contexts": list(CONTEXTS),
            "api_response_digests": {"/repos/TrillionniumFoundation/heptatrader": "sha256:" + "9" * 64},
        }
        return {"body": body, "receipt_sha256": CHECKER._canonical_digest(body)}

    def install_ib(self) -> Path:
        self.close("G-IB-001", "receipts/ib.json")
        return self.write_json("receipts/ib.json", self.ib_receipt())

    def install_governance(self, payload: dict | None = None) -> Path:
        self.close("G-TEAM-001", "receipts/governance.json")
        return self.write_json("receipts/governance.json", payload or self.governance_receipt())

    def assertError(self, errors: list[str], text: str) -> None:
        self.assertTrue(any(text in error for error in errors), errors)

    def test_open_external_gates_do_not_require_receipts(self) -> None:
        self.assertEqual([], self.validate(expected_source_sha=None))

    def test_current_repository_has_zero_open_internal_gaps(self) -> None:
        if not CHECKER.DEFAULT_GAP_REGISTRY.exists() or not CHECKER.DEFAULT_MODULE_REGISTRY.exists():
            self.skipTest("full source checkout unavailable in focused local test workspace")
        self.assertEqual([], CHECKER.validate())
        result = CHECKER.summary()
        self.assertEqual([], result["repository_executable_open"])
        self.assertEqual(["G-IB-001", "G-TEAM-001"], result["external_open"])
        self.assertFalse(result["grants_qualification"])

    def test_open_repository_gap_is_rejected(self) -> None:
        self.gaps["gaps"][0]["state"] = "in-progress"
        self.assertError(self.validate(), "repository-executable gap must be closed")

    def test_unknown_external_gate_is_rejected(self) -> None:
        self.modules["implementation_evidence_policy"]["external_gate_ids"].append("G-UNKNOWN")
        errors = self.validate()
        self.assertError(errors, "no receipt verifier")
        self.assertError(errors, "external gate is absent")

    def test_protected_external_gate_cannot_be_deleted(self) -> None:
        self.modules["implementation_evidence_policy"]["external_gate_ids"].remove("G-IB-001")
        self.gaps["gaps"] = [g for g in self.gaps["gaps"] if g["id"] != "G-IB-001"]
        self.assertError(self.validate(), "protected external gate missing")

    def test_external_gate_cannot_be_closed_without_receipt(self) -> None:
        self.gaps["gaps"][1]["state"] = "closed"
        self.assertError(self.validate(), "requires qualification_receipt")

    def test_ib_fixture_passes_only_integrity_and_binding_checks(self) -> None:
        self.install_ib()
        self.assertEqual([], self.validate())
        result = CHECKER.summary(self.gap_path, self.module_path,
                                 repository_root=self.root, expected_source_sha=SOURCE)
        self.assertFalse(result["grants_qualification"])
        self.assertFalse(result["external_evidence_synthesized"])

    def test_source_identity_is_required_for_closed_gate(self) -> None:
        self.install_ib()
        self.assertError(self.validate(expected_source_sha=None), "requires exact source identity")

    def test_stale_ib_source_sha_is_rejected(self) -> None:
        self.install_ib()
        self.assertError(self.validate(expected_source_sha="b" * 40), "source SHA binding mismatch")

    def test_expected_sha_must_match_checked_out_head(self) -> None:
        self.install_ib()
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        subprocess.run(["git", "-C", str(self.root), "-c", "user.name=Fixture",
                        "-c", "user.email=fixture@example.invalid", "commit", "--allow-empty", "-qm", "fixture"], check=True)
        self.assertError(self.validate(), "does not match repository HEAD")

    def initialize_git(self, root: Path) -> str:
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(root), "-c", "user.name=Fixture",
                        "-c", "user.email=fixture@example.invalid", "commit", "--allow-empty", "-qm", "fixture"], check=True)
        return subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()

    def test_git_environment_cannot_redirect_source_identity(self) -> None:
        source = self.initialize_git(self.root)
        with tempfile.TemporaryDirectory() as directory:
            other = Path(directory)
            self.initialize_git(other)
            errors: list[str] = []
            with mock.patch.dict(os.environ, {"GIT_DIR": str(other / ".git"), "GIT_WORK_TREE": str(other)}):
                self.assertEqual(source, CHECKER._source_identity(self.root, source, errors))
            self.assertEqual([], errors)

    def test_dirty_source_checkout_is_not_exact_evidence(self) -> None:
        source = self.initialize_git(self.root)
        (self.root / "untracked-source.py").write_text("changed = True")
        errors: list[str] = []
        self.assertIsNone(CHECKER._source_identity(self.root, source, errors))
        self.assertError(errors, "worktree is not clean")

    def test_parent_git_repository_is_not_candidate_identity(self) -> None:
        source = self.initialize_git(self.root)
        child = self.root / "not-the-checkout-root"
        child.mkdir()
        errors: list[str] = []
        self.assertIsNone(CHECKER._source_identity(child, source, errors))
        self.assertError(errors, "root is not the candidate checkout")

    def test_ib_receipt_capability_inflation_is_rejected(self) -> None:
        self.install_ib()
        receipt = self.ib_receipt()
        receipt["broker"]["environment"] = "LIVE"
        self.write_json("receipts/ib.json", receipt)
        self.assertError(self.validate(), "must bind IB PAPER")

    def test_ib_invalid_fingerprint_is_rejected(self) -> None:
        self.install_ib()
        receipt = self.ib_receipt()
        receipt["broker"]["account_fingerprint"] = "account-plaintext"
        self.write_json("receipts/ib.json", receipt)
        self.assertError(self.validate(), "expected canonical fingerprint")

    def test_ib_malformed_evidence_is_rejected(self) -> None:
        self.install_ib()
        for malformed in ({}, {"path": "../escape", "kind": "oms-journal", "size": True, "sha256": "no"}):
            with self.subTest(malformed=malformed):
                receipt = self.ib_receipt()
                receipt["scenarios"][0]["evidence"] = [malformed]
                self.write_json("receipts/ib.json", receipt)
                self.assertTrue(self.validate())

    def test_ib_missing_or_duplicate_scenario_is_rejected(self) -> None:
        self.install_ib()
        for duplicate in (False, True):
            receipt = self.ib_receipt()
            receipt["scenarios"].pop()
            if duplicate:
                receipt["scenarios"].append(copy.deepcopy(receipt["scenarios"][0]))
            self.write_json("receipts/ib.json", receipt)
            self.assertError(self.validate(), "scenario set does not match")

    def test_governance_fixture_passes_only_integrity_and_binding_checks(self) -> None:
        self.install_governance()
        self.assertEqual([], self.validate())

    def test_stale_governance_source_sha_is_rejected(self) -> None:
        self.install_governance()
        self.assertError(self.validate(expected_source_sha="b" * 40), "source SHA binding mismatch")

    def test_governance_requires_exact_merge_group_and_pr(self) -> None:
        self.install_governance()
        for parameters in ({"expected_merge_group_sha": None}, {"expected_merge_group_sha": "f" * 40},
                           {"expected_pull_number": None}, {"expected_pull_number": True},
                           {"expected_pull_number": 99}):
            with self.subTest(parameters=parameters):
                self.assertTrue(self.validate(**parameters))

    def test_governance_policy_projection_mismatch_is_rejected_even_with_valid_digest(self) -> None:
        receipt = self.governance_receipt()
        receipt["body"]["required_merge_group_contexts"] = ["exact-merge-candidate"]
        receipt["receipt_sha256"] = CHECKER._canonical_digest(receipt["body"])
        self.install_governance(receipt)
        errors = self.validate()
        self.assertError(errors, "contexts must be identical")
        self.assertError(errors, "differs from canonical policy")

    def test_governance_missing_canonical_policy_is_rejected(self) -> None:
        self.install_governance()
        (self.root / ".github/required-check-contexts-v1.json").unlink()
        self.assertError(self.validate(), "canonical required_branch_contexts missing")

    def test_governance_receipt_tampering_is_rejected(self) -> None:
        receipt = self.governance_receipt()
        receipt["body"]["team_slugs"].pop()
        self.install_governance(receipt)
        errors = self.validate()
        self.assertError(errors, "four distinct teams")
        self.assertError(errors, "digest mismatch")

    def test_external_fail_closed_policy_is_mandatory(self) -> None:
        self.modules["implementation_evidence_policy"]["external_gates_fail_closed"] = False
        self.assertError(self.validate(), "external_gates_fail_closed must be true")

    def test_duplicate_registry_keys_are_rejected(self) -> None:
        self.validate()
        self.gap_path.write_text('{"schema":"wrong","schema":"heptatrader.gap-registry.v2"}')
        errors = CHECKER.validate(self.gap_path, self.module_path, repository_root=self.root)
        self.assertError(errors, "duplicate JSON key")

    def test_non_string_state_is_rejected_without_crash(self) -> None:
        self.gaps["gaps"][0]["state"] = {"not": "a-state"}
        self.assertError(self.validate(), "invalid state")

    def test_unsafe_receipt_paths_are_rejected(self) -> None:
        for name in ("../x", "/tmp/x", "receipts//x", "receipts/./x", "receipts/../x", "receipts\\x", "receipts/x\x00"):
            with self.subTest(name=name):
                self.close("G-IB-001", name)
                self.assertError(self.validate(), "path is unsafe")

    def test_detached_evidence_does_not_require_source_commit_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            evidence = Path(directory)
            (evidence / "receipts").mkdir()
            (evidence / "receipts/ib.json").write_text(json.dumps(self.ib_receipt()))
            self.close("G-IB-001", "receipts/ib.json")
            self.assertFalse((self.root / "receipts/ib.json").exists())
            self.assertEqual([], self.validate(receipt_root=evidence))
            self.assertFalse((self.root / "receipts/ib.json").exists())
            self.assertError(self.validate(receipt_root=evidence, expected_source_sha="e" * 40),
                             "source SHA binding mismatch")

    def test_detached_evidence_requires_explicit_root(self) -> None:
        self.close("G-IB-001", "receipts/ib.json")
        self.assertTrue(self.validate())

    def test_symlink_leaf_inside_repository_is_rejected(self) -> None:
        path = self.install_ib()
        moved = path.with_name("real.json")
        path.rename(moved)
        path.symlink_to(moved.name)
        self.assertTrue(self.validate())

    def test_symlink_parent_inside_repository_is_rejected(self) -> None:
        self.install_ib()
        (self.root / "receipts").rename(self.root / "actual")
        (self.root / "receipts").symlink_to("actual", target_is_directory=True)
        self.assertTrue(self.validate())

    def test_hard_link_is_rejected(self) -> None:
        path = self.install_ib()
        os.link(path, self.root / "alias.json")
        self.assertError(self.validate(), "exactly one hard link")

    def test_world_writable_receipt_is_rejected(self) -> None:
        path = self.install_ib()
        path.chmod(0o666)
        self.assertError(self.validate(), "world-writable")

    def test_fifo_is_rejected_without_blocking(self) -> None:
        path = self.install_ib()
        path.unlink()
        os.mkfifo(path)
        self.assertError(self.validate(), "regular file")

    def test_empty_and_oversize_receipts_are_rejected(self) -> None:
        path = self.install_ib()
        for size in (0, BOUNDARY.MAX_RECEIPT_BYTES + 1):
            with path.open("wb") as output:
                output.truncate(size)
            self.assertError(self.validate(), "size is invalid")

    def test_receipt_json_is_strict(self) -> None:
        path = self.install_ib()
        for content in (b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":Infinity}', b'[]', b'\xff', b'{' * 2000):
            with self.subTest(content=content[:20]):
                path.write_bytes(content)
                self.assertTrue(self.validate())

    def test_leaf_replacement_during_read_is_rejected(self) -> None:
        path = self.install_ib()
        original_read = os.read
        replaced = False
        def replace(fd: int, size: int) -> bytes:
            nonlocal replaced
            data = original_read(fd, size)
            if not replaced:
                replaced = True
                path.unlink()
                path.write_text('{"replaced":true}')
            return data
        with mock.patch.object(BOUNDARY.os, "read", side_effect=replace):
            with self.assertRaisesRegex(ValueError, "identity changed"):
                BOUNDARY.read_receipt(self.root, "receipts/ib.json")

    def test_parent_replacement_during_read_is_rejected(self) -> None:
        self.install_ib()
        original_read = os.read
        replaced = False
        def replace(fd: int, size: int) -> bytes:
            nonlocal replaced
            data = original_read(fd, size)
            if not replaced:
                replaced = True
                (self.root / "receipts").rename(self.root / "old-receipts")
                (self.root / "receipts").mkdir()
            return data
        with mock.patch.object(BOUNDARY.os, "read", side_effect=replace):
            with self.assertRaisesRegex(ValueError, "directory binding changed"):
                BOUNDARY.read_receipt(self.root, "receipts/ib.json")


if __name__ == "__main__":
    unittest.main()
