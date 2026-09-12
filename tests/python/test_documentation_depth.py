"""Structure/navigation tests intentionally do not score prose quality."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import check_documentation as documentation


class DocumentationStructureTests(unittest.TestCase):
    def test_repository_structure_and_navigation(self):
        self.assertEqual(documentation.validate(ROOT), [])

    def test_missing_contract_metadata_is_rejected_without_word_quotas(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            relative = Path("docs/modules/example.md")
            path = root / relative
            path.parent.mkdir(parents=True)
            path.write_text("# Example\n\n## Interface\n\nSee the executable contract.\n")
            errors = []
            documentation._validate_doc_metadata(root, relative, {"status": "CURRENT"}, errors)
            self.assertTrue(any("Status:" in error for error in errors))
            path.write_text("# Example\nStatus: CURRENT\nApplies to: test\nImplementation: `src`\nTests: `tests`\n\n## Interface\n\nShort but specific.\n")
            errors = []
            documentation._validate_doc_metadata(root, relative, {"status": "CURRENT"}, errors)
            self.assertEqual(errors, [])  # Only structure, never a design-quality claim.

    def test_catalog_navigation_is_deterministic_and_detects_status_drift(self):
        modules = {"example": {"id": "example", "status": "EXPERIMENTAL", "document": "docs/modules/example.md"}}
        expected = documentation.render_module_table(modules)
        self.assertEqual(expected, documentation.render_module_table(modules))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "docs").mkdir()
            path = root / "docs/index.md"
            path.write_text("# Index\n" + expected + "\n")
            self.assertEqual(documentation.validate_generated_index(root, modules), [])
            path.write_text("# Index\n" + expected.replace("EXPERIMENTAL", "CURRENT") + "\n")
            self.assertTrue(documentation.validate_generated_index(root, modules))


if __name__ == "__main__":
    unittest.main()
