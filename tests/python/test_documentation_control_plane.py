from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import check_documentation_control_plane as control  # noqa: E402
import check_module_discipline as discipline  # noqa: E402
import hepta_registry_checks as registry_checks  # noqa: E402
from hepta_module_boundaries import (  # noqa: E402
    canonical_relative_path,
    selector_from_object,
    selector_matches,
)


class DocumentationControlPlaneTests(unittest.TestCase):
    def run_script(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, *args],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )

    def test_generated_views_match_registries(self) -> None:
        result = self.run_script("scripts/generate_documentation_views.py", "--check")
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_documentation_control_plane_is_self_consistent(self) -> None:
        result = self.run_script("scripts/check_documentation_control_plane.py")
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_module_discipline_is_self_consistent(self) -> None:
        errors = discipline.validate()
        self.assertEqual(errors, [], "\n".join(errors))

    def _schema_errors(self, manifest: dict) -> list[str]:
        schema = json.loads(
            (ROOT / "docs/modules/module-manifest-schema-v3.json").read_text()
        )
        errors: list[str] = []
        control.validate_module_manifest(manifest, schema, "fixture", errors)
        return errors

    def _manifest(self, name: str = "hepta-execution-runtime.json") -> dict:
        return json.loads(
            (ROOT / "docs/modules/manifests" / name).read_text()
        )

    def test_module_schema_rejects_wrong_version_and_extra_fields(self) -> None:
        manifest = self._manifest()
        manifest["schema"] = "heptatrader.module-manifest.v1"
        self.assertTrue(self._schema_errors(manifest))

        manifest = self._manifest()
        manifest["undeclared"] = True
        self.assertTrue(self._schema_errors(manifest))

    def test_module_schema_rejects_invalid_lifecycle_and_nested_types(self) -> None:
        manifest = self._manifest()
        manifest["lifecycle"] = "finished"
        self.assertTrue(self._schema_errors(manifest))

        manifest = self._manifest()
        manifest["owners"]["reviewers"] = "@hepta/reviewer"
        self.assertTrue(self._schema_errors(manifest))

    def test_module_schema_rejects_duplicates_and_unsafe_paths(self) -> None:
        manifest = self._manifest()
        manifest["source_roots"].append(manifest["source_roots"][0])
        self.assertTrue(self._schema_errors(manifest))

        manifest = self._manifest()
        manifest["source_roots"][0] = "../outside"
        self.assertTrue(self._schema_errors(manifest))

    def test_module_schema_enforces_migration_conditionals(self) -> None:
        manifest = self._manifest()
        manifest["ownership_mode"] = "shared-migration"
        manifest["migration_gap"] = "G-MOD-002"
        manifest.pop("migration_gap")
        self.assertTrue(self._schema_errors(manifest))

        exclusive = self._manifest("hepta-venue-ib.json")
        exclusive["migration_gap"] = "G-MOD-002"
        self.assertTrue(self._schema_errors(exclusive))

    def test_selector_matching_has_path_boundaries(self) -> None:
        directory = selector_from_object(
            ROOT, {"kind": "directory", "path": "HeptaTrade/tool_host/"}
        )
        self.assertTrue(
            selector_matches("HeptaTrade/tool_host/typed_tool_protocol.cpp", directory)
        )
        self.assertFalse(
            selector_matches("HeptaTrade/tool_host_extra/typed_tool_protocol.cpp", directory)
        )

        prefix = selector_from_object(
            ROOT, {"kind": "prefix", "path": "HeptaTrade/tool_host/typed_tool_"}
        )
        self.assertTrue(
            selector_matches("HeptaTrade/tool_host/typed_tool_protocol.cpp", prefix)
        )
        self.assertFalse(
            selector_matches("HeptaTrade/tool_host/typed_tool_extra/protocol.cpp", prefix)
        )

    def test_repository_path_aliases_are_rejected(self) -> None:
        for value in ("../outside", "/absolute", "HeptaTrade/../outside", "a\\b"):
            with self.assertRaises(ValueError, msg=value):
                canonical_relative_path(ROOT, value)

    def test_repository_markdown_is_entrypoint_only(self) -> None:
        registry = json.loads((ROOT / "docs/document-registry-v2.json").read_text())
        registered = {item["path"] for item in registry["repository_entrypoints"]}
        actual = {
            path.relative_to(ROOT).as_posix()
            for path in ROOT.rglob("*.md")
            if path.is_file()
            and path.relative_to(ROOT).parts[0] not in {"docs", "legacy", "build"}
        }
        self.assertEqual(actual, registered)
        for relative in registered:
            head = "\n".join((ROOT / relative).read_text().splitlines()[:14])
            self.assertIn("Authority: entrypoint only", head)

    def test_cross_module_lock_exception_is_narrow_and_documented(self) -> None:
        errors: list[str] = []
        modules, _ = registry_checks.load_modules(ROOT, errors)
        self.assertEqual(errors, [])
        exception = "marketdata-feature-capability-transaction-only"
        permitted = {"hepta.marketdata.runtime", "hepta.feature.runtime"}
        declared = {
            module_id for module_id, manifest in modules.items()
            if manifest["concurrency"]["cross_module_lock"] != "forbidden"
        }
        self.assertEqual(declared, permitted)
        document = (ROOT / "docs/architecture/CONCURRENCY-AND-SHARDING.md").read_text()
        self.assertIn(exception, document)
        self.assertIn("MarketDataAuthorityState::mutex", document)
        self.assertIn("GetRiskReady", document)
        for module_id in sorted(permitted):
            manifest = modules[module_id]
            self.assertEqual(manifest["concurrency"]["cross_module_lock"], exception)
            guide = (ROOT / "docs" / manifest["documentation"]["technical_guide"]).read_text()
            self.assertIn(f"**cross module lock:** `{exception}`", guide)

    def test_legacy_tree_contains_no_docs_media_or_build_entrypoints(self) -> None:
        forbidden_suffixes = {".md", ".txt", ".pdf", ".png", ".jpg", ".jpeg", ".webp"}
        residual = []
        for path in (ROOT / "legacy").rglob("*"):
            if not path.is_file() or path.name == "QUARANTINE.json":
                continue
            lower = path.name.lower()
            if (
                path.suffix.lower() in forbidden_suffixes
                or path.name == "CMakeLists.txt"
                or lower.endswith((".sln", ".vcxproj", ".vcxproj.filters", ".cmake"))
            ):
                residual.append(str(path.relative_to(ROOT)))
        self.assertEqual(residual, [])


class ContractRelationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.modules = {
            "provider": {"provides": ["contract.v1"], "consumes": []},
            "consumer": {"provides": [], "consumes": ["contract.v1"]},
            "unused": {"provides": [], "consumes": []},
        }
        self.contracts = {
            "contract.v1": {"providers": ["provider"], "consumers": ["consumer"]},
            "external.v1": {"providers": [], "consumers": []},
        }

    def errors(self) -> list[str]:
        errors: list[str] = []
        registry_checks.validate_contract_relations(self.modules, self.contracts, errors)
        return errors

    def test_exact_inverse_and_external_empty_contract_pass(self) -> None:
        self.assertEqual(self.errors(), [])

    def test_missing_consumer_is_rejected(self) -> None:
        self.contracts["contract.v1"]["consumers"] = []
        self.assertIn("missing consumer consumer", "\n".join(self.errors()))

    def test_missing_provider_is_rejected(self) -> None:
        self.contracts["contract.v1"]["providers"] = []
        self.assertIn("missing provider provider", "\n".join(self.errors()))

    def test_extra_consumer_and_provider_are_rejected(self) -> None:
        for relation in ("consumers", "providers"):
            with self.subTest(relation=relation):
                self.setUp()
                self.contracts["contract.v1"][relation].append("unused")
                self.assertIn("unused does not declare", "\n".join(self.errors()))

    def test_unknown_module_and_contract_are_rejected(self) -> None:
        self.contracts["contract.v1"]["consumers"].append("unknown")
        self.modules["consumer"]["consumes"].append("unknown.v1")
        errors = "\n".join(self.errors())
        self.assertIn("unknown consumer unknown", errors)
        self.assertIn("unknown contract unknown.v1", errors)

    def test_duplicate_references_on_both_sides_are_rejected(self) -> None:
        for side, key, relation, value in (
            ("modules", "provider", "provides", "contract.v1"),
            ("modules", "consumer", "consumes", "contract.v1"),
            ("contracts", "contract.v1", "providers", "provider"),
            ("contracts", "contract.v1", "consumers", "consumer"),
        ):
            with self.subTest(side=side, relation=relation):
                self.setUp()
                getattr(self, side)[key][relation].append(value)
                self.assertIn("duplicate reference", "\n".join(self.errors()))

    def test_malformed_reference_values_fail_without_crashing(self) -> None:
        for bad in (None, "consumer", {}, [None], [{}], [[]], [False], [1], [""], [" "]):
            for side, key, relation in (
                ("contracts", "contract.v1", "consumers"),
                ("modules", "consumer", "consumes"),
            ):
                with self.subTest(bad=bad, side=side):
                    self.setUp()
                    getattr(self, side)[key][relation] = bad
                    self.assertTrue(self.errors())

    def test_missing_relation_fields_are_rejected(self) -> None:
        del self.contracts["external.v1"]["providers"]
        self.assertTrue(self.errors())
        self.setUp()
        del self.modules["unused"]["consumes"]
        self.assertTrue(self.errors())

    def test_unsupported_module_is_not_exempt_from_inverse_edges(self) -> None:
        self.modules["provider"]["lifecycle"] = "unsupported"
        self.contracts["contract.v1"]["providers"] = []
        self.assertIn("missing provider", "\n".join(self.errors()))

    def test_validation_is_deterministic_and_never_repairs_inputs(self) -> None:
        self.contracts["contract.v1"]["consumers"] = []
        before = deepcopy((self.modules, self.contracts))
        first = self.errors()
        self.modules = dict(reversed(list(self.modules.items())))
        self.contracts = dict(reversed(list(self.contracts.items())))
        self.assertEqual(first, self.errors())
        self.assertEqual((self.modules, self.contracts), before)

    def test_repository_relations_and_regression_edges(self) -> None:
        errors: list[str] = []
        self.modules, _ = registry_checks.load_modules(ROOT, errors)
        self.assertEqual(errors, [])
        document = json.loads((ROOT / "docs/contracts/contract-registry-v2.json").read_text())
        self.contracts = {item["id"]: item for item in document["contracts"]}
        self.assertEqual(self.errors(), [])
        for contract_id, module_id in (
            ("hepta.authoritative-snapshot.v2", "hepta.portfolio.compiler"),
            ("hepta.event-envelope.v1", "hepta.agent.support"),
            ("hepta.event-envelope.v1", "hepta.marketdata.runtime"),
            ("hepta.risk-policy.v2", "hepta.venue.ib"),
        ):
            with self.subTest(contract=contract_id, module=module_id):
                original = self.contracts[contract_id]["consumers"]
                self.contracts[contract_id]["consumers"] = [x for x in original if x != module_id]
                self.assertIn(f"missing consumer {module_id}", "\n".join(self.errors()))
                self.contracts[contract_id]["consumers"] = original


if __name__ == "__main__":
    unittest.main()
