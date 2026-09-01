#!/usr/bin/env python3
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
GENERATOR_PATH = ROOT / "scripts/generate_documentation_views.py"
SPEC = importlib.util.spec_from_file_location("hepta_doc_generator", GENERATOR_PATH)
assert SPEC is not None and SPEC.loader is not None
GENERATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GENERATOR)


class ModuleDocumentationCoverageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = json.loads(
            (ROOT / "docs/modules/module-registry-v2.json").read_text(
                encoding="utf-8"
            )
        )
        self.schema = json.loads(
            (ROOT / "docs/modules/module-manifest-schema-v3.json").read_text(
                encoding="utf-8"
            )
        )
        self.profile = json.loads(
            (
                ROOT
                / "docs/modules/module-documentation-profiles-v1.json"
            ).read_text(encoding="utf-8")
        )

    def manifest(self) -> dict:
        relative = self.registry["manifest_paths"][0]
        return json.loads((ROOT / "docs" / relative).read_text(encoding="utf-8"))

    def test_all_22_modules_have_unique_profiles_and_guides(self) -> None:
        manifests = [
            json.loads((ROOT / "docs" / relative).read_text(encoding="utf-8"))
            for relative in self.registry["manifest_paths"]
        ]
        profiles = self.profile["profiles"]
        self.assertEqual(22, len(manifests))
        self.assertEqual(22, len(profiles))
        self.assertEqual(
            {item["id"] for item in manifests},
            {item["module_id"] for item in profiles},
        )
        guides = [
            item["documentation"]["technical_guide"] for item in manifests
        ]
        self.assertEqual(len(guides), len(set(guides)))
        for guide in guides:
            self.assertTrue((ROOT / "docs" / guide).is_file(), guide)

    def test_schema_rejects_missing_documentation(self) -> None:
        manifest = self.manifest()
        manifest.pop("documentation")
        errors = list(Draft202012Validator(self.schema).iter_errors(manifest))
        self.assertTrue(errors)

    def test_schema_rejects_incomplete_topic_coverage(self) -> None:
        manifest = copy.deepcopy(self.manifest())
        manifest["documentation"]["coverage_topics"].pop()
        errors = list(Draft202012Validator(self.schema).iter_errors(manifest))
        self.assertTrue(errors)

    def test_schema_rejects_duplicate_topic_coverage(self) -> None:
        manifest = copy.deepcopy(self.manifest())
        topics = manifest["documentation"]["coverage_topics"]
        topics[-1] = topics[0]
        errors = list(Draft202012Validator(self.schema).iter_errors(manifest))
        self.assertTrue(errors)

    def test_generated_output_set_contains_every_module_guide(self) -> None:
        outputs = GENERATOR.outputs()
        guides = {
            "docs/" + json.loads(
                (ROOT / "docs" / relative).read_text(encoding="utf-8")
            )["documentation"]["technical_guide"]
            for relative in self.registry["manifest_paths"]
        }
        self.assertTrue(guides.issubset(outputs))
        self.assertEqual(26, len(outputs))

    def test_each_guide_contains_every_required_section(self) -> None:
        headings = [
            "## Purpose and Scope",
            "## Responsibilities and Non-Responsibilities",
            "## Trust Domain and Authority",
            "## Physical Source and Build Boundaries",
            "## Contracts and Public Interfaces",
            "## State and Data Model",
            "## Concurrency, Ordering, and Backpressure",
            "## Failure and Recovery",
            "## Configuration and Compatibility",
            "## Observability and Resource Budgets",
            "## Security",
            "## Verification and Testing",
            "## Operations, Rollout, and Known Gaps",
        ]
        for relative in self.registry["manifest_paths"]:
            manifest = json.loads(
                (ROOT / "docs" / relative).read_text(encoding="utf-8")
            )
            text = (
                ROOT / "docs" / manifest["documentation"]["technical_guide"]
            ).read_text(encoding="utf-8")
            for heading in headings:
                self.assertIn(heading, text)


if __name__ == "__main__":
    unittest.main()
