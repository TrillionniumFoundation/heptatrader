from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "scripts/generate_module_implementation_projection.py"
SPEC = importlib.util.spec_from_file_location(
    "generate_module_implementation_projection", MODULE_PATH
)
assert SPEC is not None and SPEC.loader is not None
GENERATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GENERATOR)


class ModuleImplementationProjectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.readme = (ROOT / "docs/README.md").read_text(encoding="utf-8")
        self.registry = json.loads(
            (ROOT / "docs/modules/module-registry-v2.json").read_text(
                encoding="utf-8"
            )
        )

    def validate(self, readme: str, registry: dict) -> list[str]:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            readme_path = root / "README.md"
            registry_path = root / "module-registry.json"
            readme_path.write_text(readme, encoding="utf-8")
            registry_path.write_text(json.dumps(registry), encoding="utf-8")
            return GENERATOR.validate(readme_path, registry_path)

    def test_current_projection_matches_registry(self) -> None:
        self.assertEqual([], GENERATOR.validate())

    def test_projection_contains_every_registered_module(self) -> None:
        rendered = GENERATOR.render(self.registry)
        self.assertEqual(22, rendered.count("| `hepta."))
        for entry in self.registry["implementation_evidence"]:
            self.assertIn(f"`{entry['module_id']}`", rendered)
            self.assertIn(f"`{entry['state']}`", rendered)

    def test_projection_drift_is_rejected(self) -> None:
        changed = self.readme.replace(
            "`hepta.venue.ib` | `external-qualification-required`",
            "`hepta.venue.ib` | `implemented`",
            1,
        )
        errors = self.validate(changed, self.registry)
        self.assertTrue(any("projection drift" in item for item in errors), errors)

    def test_evidence_state_change_requires_projection_refresh(self) -> None:
        registry = copy.deepcopy(self.registry)
        entry = next(
            item
            for item in registry["implementation_evidence"]
            if item["module_id"] == "hepta.strategy.runtime"
        )
        entry["state"] = "implemented"
        entry["excluded_scope"] = []
        errors = self.validate(self.readme, registry)
        self.assertTrue(any("projection drift" in item for item in errors), errors)

    def test_missing_module_evidence_is_rejected(self) -> None:
        registry = copy.deepcopy(self.registry)
        registry["implementation_evidence"].pop()
        errors = self.validate(self.readme, registry)
        self.assertTrue(
            any("evidence/module mismatch" in item for item in errors), errors
        )

    def test_bounded_state_without_exclusions_is_rejected(self) -> None:
        registry = copy.deepcopy(self.registry)
        entry = next(
            item
            for item in registry["implementation_evidence"]
            if item["state"] == "bounded-implementation"
        )
        entry["excluded_scope"] = []
        errors = self.validate(self.readme, registry)
        self.assertTrue(
            any("bounded state requires excluded_scope" in item for item in errors),
            errors,
        )

    def test_duplicate_projection_markers_are_rejected(self) -> None:
        errors = self.validate(
            self.readme + "\n" + GENERATOR.START + "\n" + GENERATOR.END,
            self.registry,
        )
        self.assertTrue(any("markers must occur once" in item for item in errors), errors)


if __name__ == "__main__":
    unittest.main()
