from __future__ import annotations

import json
import copy
import io
from contextlib import redirect_stdout
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import check_gap_register as gap_register  # noqa: E402
import verify_source_gap_closures as source_gaps  # noqa: E402
import verify_build_ownership as build_ownership  # noqa: E402
import test_build_ownership as build_fixtures  # noqa: E402


class GapRegisterTests(unittest.TestCase):
    def test_repository_register_passes(self) -> None:
        self.assertEqual(gap_register.validate(ROOT), [])

    def test_repository_source_gap_static_supplement_passes(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(source_gaps.main(["--root", str(ROOT)]), 0)
        self.assertIn("registry/static contracts", output.getvalue())
        self.assertIn("no C++ behavioral tests executed", output.getvalue())

    def build_fixture(self, directory: str, omit_risk_dependency: bool) -> Path:
        root, inventory = build_fixtures.BuildOwnershipTests().fixture(directory)
        names = sorted(source_gaps.REQUIRED_TEST_TARGETS)
        dependencies = [name for name in names if not (
            omit_risk_dependency and name == "hepta_pre_trade_risk_engine_tests")]
        with (root / "tests/CMakeLists.txt").open("a") as stream:
            for name in names:
                stream.write(f"add_executable({name} component_tests.cpp)\n")
            stream.write("add_custom_target(hepta_core_test_binaries DEPENDS " + " ".join(dependencies) + ")\n")
            # This decoy satisfied the old text-only aggregate check.
            stream.write("# add_dependencies(hepta_core_test_binaries hepta_pre_trade_risk_engine_tests)\n")
        inventory["profiles"]["core"] = build_ownership.observe(root, "core")
        inventory["profiles"]["ib"] = copy.deepcopy(inventory["profiles"]["core"])
        inventory["profiles"]["ib"]["requires_ib_sdk"] = True
        inventory["profiles"]["ib"]["options"] = build_ownership.profile_options("ib")
        (root / "docs/build-targets.json").write_text(json.dumps(inventory))
        return root

    def test_real_aggregate_contains_gap_critical_test_targets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.build_fixture(directory, False)
            self.assertEqual(source_gaps.validate_test_inventory(root), [])

    def test_test_name_and_decoy_dependency_comment_cannot_replace_build_edge(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.build_fixture(directory, True)
            errors = source_gaps.validate_test_inventory(root)
            self.assertTrue(any("aggregate does not build" in error and
                                "hepta_pre_trade_risk_engine_tests" in error for error in errors), errors)

    def test_manifest_dependency_cannot_forge_actual_cmake_edge(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.build_fixture(directory, True)
            path = root / "docs/build-targets.json"
            inventory = json.loads(path.read_text())
            aggregate = next(target for target in inventory["profiles"]["core"]["targets"]
                             if target["name"] == "hepta_core_test_binaries")
            aggregate["dependencies"].append("hepta_pre_trade_risk_engine_tests")
            path.write_text(json.dumps(inventory))
            errors = source_gaps.validate_test_inventory(root)
            self.assertTrue(any("inventory drift" in error for error in errors), errors)

    def test_oms_static_scan_does_not_claim_to_prove_send_ordering(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative in (
                "HeptaTrade/execution/execution_place_order_dispatch.cpp",
                "tests/execution_coordinator_tests.cpp",
                "tests/oms_journal_durability_tests.cpp",
                "scripts/verify_oms_journal_replay.py",
            ):
                target = root / relative;target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / relative, target)
            dispatch = root / "HeptaTrade/execution/execution_place_order_dispatch.cpp"
            dispatch.write_text("// Negative fixture: an early send cannot be evaluated by static token presence.\n"
                                "m_callbacks.placeIbOrder(command.contract, command.order, nullptr);\n" + dispatch.read_text())
            # This script deliberately reports static presence only. The
            # separately executed coordinator tests must establish send order.
            self.assertEqual(source_gaps.validate_oms(root), [])
            dispatch.write_text(dispatch.read_text().replace('"place_send_attempt"', '"removed_send_attempt"'))
            self.assertTrue(any("missing contract token" in error for error in source_gaps.validate_oms(root)))

    def fixture(self, directory: str) -> Path:
        root = Path(directory)
        (root / "docs").mkdir(parents=True)
        for path in (
            "docs/gap-register.json",
            "docs/capabilities.json",
            "docs/module-catalog.json",
        ):
            destination = root / path
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / path, destination)
        register = json.loads((root / "docs/gap-register.json").read_text(encoding="utf-8"))
        for gap in register["gaps"]:
            for evidence in gap["evidence"]:
                target = root / evidence
                target.parent.mkdir(parents=True, exist_ok=True)
                if not target.exists():
                    if (ROOT / evidence).is_dir():
                        target.mkdir(parents=True, exist_ok=True)
                    else:
                        target.write_text("fixture\n", encoding="utf-8")
        return root

    def mutate(self, root: Path, callback) -> None:
        path = root / "docs/gap-register.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        callback(value)
        path.write_text(json.dumps(value), encoding="utf-8")

    def test_source_cannot_close_external_control(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.fixture(directory)
            self.mutate(
                root,
                lambda value: next(
                    gap for gap in value["gaps"] if gap["id"] == "G-TEAM-001"
                ).update({"state": "CLOSED_SOURCE"}),
            )
            errors = gap_register.validate(root)
            self.assertTrue(any("cannot mark an external control closed" in error for error in errors), errors)

    def test_repository_gap_cannot_be_left_open(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.fixture(directory)
            self.mutate(
                root,
                lambda value: next(
                    gap for gap in value["gaps"] if gap["id"] == "RISK-001"
                ).update({"state": "OPEN_EXTERNAL"}),
            )
            errors = gap_register.validate(root)
            self.assertTrue(any("repository-controlled gap must be closed" in error for error in errors), errors)

    def test_paper_or_live_source_authorization_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.fixture(directory)
            self.mutate(
                root,
                lambda value: value["authorization"].update(
                    {"paper_authorized": True, "live_authorized": True}
                ),
            )
            errors = gap_register.validate(root)
            self.assertTrue(any("PAPER cannot be source-authorized" in error for error in errors), errors)

    def test_external_issue_binding_is_immutable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.fixture(directory)
            self.mutate(
                root,
                lambda value: next(
                    gap for gap in value["gaps"] if gap["id"] == "G-IB-001"
                ).update({"issue": "https://example.invalid/closed"}),
            )
            errors = gap_register.validate(root)
            self.assertTrue(any("external issue binding is invalid" in error for error in errors), errors)

    def test_static_supplement_reuses_canonical_external_issue_and_blocking_rules(self) -> None:
        for change, expected in (
            ({"issue": "https://example.invalid/closed"}, "external issue binding is invalid"),
            ({"blocking_authorization": False}, "external gap must block authorization"),
        ):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as directory:
                root = self.fixture(directory)
                self.mutate(root, lambda value: next(gap for gap in value["gaps"]
                            if gap["id"] == "G-IB-001").update(change))
                errors = source_gaps.validate_register_projection(root)
                self.assertTrue(any(expected in error for error in errors), errors)

    def test_external_gap_cannot_be_source_closed_in_registry_projection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "docs").mkdir(parents=True)
            shutil.copyfile(
                ROOT / "docs/gap-register.json",
                root / "docs/gap-register.json",
            )
            self.mutate(
                root,
                lambda value: next(
                    gap for gap in value["gaps"] if gap["id"] == "G-TEAM-001"
                ).update({"state": "CLOSED_SOURCE"}),
            )
            errors = source_gaps.validate_register_projection(root)
            self.assertTrue(
                any("G-TEAM-001: external evidence cannot be closed by source" in error for error in errors),
                errors,
            )

    def test_unregistered_repository_gap_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "docs").mkdir(parents=True)
            shutil.copyfile(
                ROOT / "docs/gap-register.json",
                root / "docs/gap-register.json",
            )
            self.mutate(
                root,
                lambda value: value["gaps"].append(
                    {
                        "id": "UNVERIFIED-001",
                        "domain": "REPOSITORY",
                        "state": "CLOSED_SOURCE",
                        "blocking_authorization": False,
                        "summary": "must not self-certify",
                        "evidence": ["README.md"],
                        "issue": None,
                    }
                ),
            )
            errors = source_gaps.validate_register_projection(root)
            self.assertTrue(
                any("repository gap/verifier set mismatch" in error for error in errors),
                errors,
            )

    def test_temporary_encoded_payload_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "scripts").mkdir(parents=True)
            (root / "scripts/.hepta-gap-closure-payload-00.b64").write_text(
                "H4sI", encoding="utf-8"
            )
            errors = source_gaps.validate_temporary_artifacts(root)
            self.assertTrue(
                any("temporary encoded gap-closure payload" in error for error in errors),
                errors,
            )

    def test_temporary_flatten_materializer_cannot_ship(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = (
                ".github/workflows/flatten-capacity-materialize.yml",
                "scripts/apply_flatten_capacity_fix.py",
            )
            for relative in paths:
                path = root / relative;path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("# Negative fixture only; never executed.\n")
            errors = source_gaps.validate_temporary_artifacts(root)
            self.assertTrue(any("CI-001" in error and paths[0] in error for error in errors), errors)
            self.assertTrue(any("BUILD-001" in error and paths[1] in error for error in errors), errors)


if __name__ == "__main__":
    unittest.main()
