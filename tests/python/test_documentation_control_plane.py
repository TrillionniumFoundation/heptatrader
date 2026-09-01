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


if __name__ == "__main__":
    unittest.main()
