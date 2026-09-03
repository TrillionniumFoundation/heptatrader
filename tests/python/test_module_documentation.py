#!/usr/bin/env python3
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import re
import unittest

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
GENERATOR_PATH = ROOT / "scripts/generate_documentation_views.py"
SPEC = importlib.util.spec_from_file_location("hepta_doc_generator", GENERATOR_PATH)
assert SPEC is not None and SPEC.loader is not None
GENERATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GENERATOR)

REQUIRED_HEADINGS = [
    "## Current Implementation Evidence",
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
PLACEHOLDER = re.compile(r"\b(?:TODO|TBD|FIXME|coming soon)\b", re.IGNORECASE)


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
                ROOT / "docs/modules/module-documentation-profiles-v1.json"
            ).read_text(encoding="utf-8")
        )
        self.manifests = [
            json.loads((ROOT / "docs" / relative).read_text(encoding="utf-8"))
            for relative in self.registry["manifest_paths"]
        ]
        self.profiles = {
            item["module_id"]: item for item in self.profile["profiles"]
        }
        self.evidence = {
            item["module_id"]: item
            for item in self.registry["implementation_evidence"]
        }

    def manifest(self) -> dict:
        return copy.deepcopy(self.manifests[0])

    @staticmethod
    def guide_text(manifest: dict) -> str:
        return (
            ROOT / "docs" / manifest["documentation"]["technical_guide"]
        ).read_text(encoding="utf-8")

    @staticmethod
    def section(text: str, heading: str) -> str:
        start = text.index(heading) + len(heading)
        next_heading = text.find("\n## ", start)
        return text[start:] if next_heading < 0 else text[start:next_heading]

    def test_all_22_modules_have_unique_profiles_and_guides(self) -> None:
        self.assertEqual(22, len(self.manifests))
        self.assertEqual(22, len(self.profiles))
        self.assertEqual(
            {item["id"] for item in self.manifests},
            set(self.profiles),
        )
        guides = [
            item["documentation"]["technical_guide"]
            for item in self.manifests
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
        manifest = self.manifest()
        manifest["documentation"]["coverage_topics"].pop()
        errors = list(Draft202012Validator(self.schema).iter_errors(manifest))
        self.assertTrue(errors)

    def test_schema_rejects_duplicate_topic_coverage(self) -> None:
        manifest = self.manifest()
        topics = manifest["documentation"]["coverage_topics"]
        topics[-1] = topics[0]
        errors = list(Draft202012Validator(self.schema).iter_errors(manifest))
        self.assertTrue(errors)

    def test_generated_output_set_contains_every_module_guide(self) -> None:
        outputs = GENERATOR.outputs()
        guides = {
            "docs/" + item["documentation"]["technical_guide"]
            for item in self.manifests
        }
        self.assertTrue(guides.issubset(outputs))
        self.assertEqual(26, len(outputs))

    def test_module_map_materializes_current_evidence_state(self) -> None:
        text = (ROOT / "docs/modules/MODULE-MAP.md").read_text(encoding="utf-8")
        self.assertIn("| Module | Lifecycle | Current evidence |", text)
        for manifest in self.manifests:
            state = self.evidence[manifest["id"]]["state"]
            self.assertIn(
                f"| `{manifest['id']}` | {manifest['lifecycle']} | `{state}` |",
                text,
                manifest["id"],
            )

    def test_each_guide_contains_every_required_section_with_real_body(self) -> None:
        for manifest in self.manifests:
            text = self.guide_text(manifest)
            self.assertGreaterEqual(
                len(text.encode("utf-8")),
                4000,
                manifest["id"],
            )
            self.assertIsNone(PLACEHOLDER.search(text), manifest["id"])
            for heading in REQUIRED_HEADINGS:
                self.assertIn(heading, text, manifest["id"])
                body = self.section(text, heading).strip()
                self.assertGreaterEqual(
                    len(body),
                    120,
                    f"{manifest['id']} {heading} is too shallow",
                )

    def test_each_guide_materializes_manifest_engineering_contract(self) -> None:
        for manifest in self.manifests:
            text = self.guide_text(manifest)
            expected_values: list[str] = [
                manifest["id"],
                manifest["version"],
                manifest["lifecycle"],
                manifest["kind"],
                manifest["trust_domain"],
                manifest["authority"],
                manifest["resource_budget"],
                manifest["owners"]["dri"],
                manifest["owners"]["backup"],
            ]
            for field in (
                "source_roots",
                "build_targets",
                "provides",
                "consumes",
                "allowed_dependencies",
                "forbidden_dependencies",
                "verification",
            ):
                expected_values.extend(manifest[field])
            expected_values.extend(manifest["owners"]["reviewers"])
            expected_values.extend(manifest["state"].values())
            expected_values.extend(manifest["concurrency"].values())
            expected_values.extend(manifest["backpressure"].values())
            expected_values.extend(manifest["failure"].values())
            for value in expected_values:
                self.assertIn(str(value), text, f"{manifest['id']}: {value}")

    def test_each_guide_materializes_current_implementation_evidence(self) -> None:
        self.assertEqual(
            {item["id"] for item in self.manifests},
            set(self.evidence),
        )
        for manifest in self.manifests:
            text = self.guide_text(manifest)
            evidence = self.evidence[manifest["id"]]
            self.assertIn(
                f"- **Evidence state:** `{evidence['state']}`",
                text,
                manifest["id"],
            )
            self.assertIn(
                evidence["resource_guardrail_profile"],
                text,
                manifest["id"],
            )
            for field in (
                "implemented_scope",
                "excluded_scope",
                "source_evidence",
                "test_evidence",
                "external_gates",
            ):
                for value in evidence[field]:
                    self.assertIn(value, text, f"{manifest['id']}: {field}")
            if evidence["state"] != "implemented":
                self.assertTrue(
                    evidence["excluded_scope"],
                    f"{manifest['id']}: bounded evidence must retain exclusions",
                )

    def test_each_guide_materializes_profile_specific_detail(self) -> None:
        fields = (
            "purpose",
            "responsibilities",
            "non_responsibilities",
            "state_notes",
            "ordering_and_backpressure",
            "recovery",
            "configuration",
            "observability",
            "security",
            "operations",
            "known_gaps",
        )
        for manifest in self.manifests:
            text = self.guide_text(manifest)
            profile = self.profiles[manifest["id"]]
            for field in fields:
                values = profile[field]
                if isinstance(values, str):
                    values = [values]
                for value in values:
                    self.assertIn(value, text, f"{manifest['id']}: {field}")


if __name__ == "__main__":
    unittest.main()
