from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import check_documentation as documentation


class DocumentationStructureTests(unittest.TestCase):
    def check_metadata(self, text: str) -> list[str]:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            relative = Path("module.md")
            (root / relative).write_text(text, encoding="utf-8")
            errors: list[str] = []
            documentation._validate_doc_metadata(root, relative, {"status": "CURRENT"}, errors)
            return errors

    def document(self) -> str:
        return ("# Bounded parser\n\nStatus: CURRENT\nApplies to: parser v1\n"
                "Implementation: `parser.cpp`\nTests: `parser_test.cpp`\n\n"
                "## Contract\nReject unknown fields. Never perform I/O.\n")

    def test_repository_structure_and_references(self) -> None:
        self.assertEqual(documentation.validate(ROOT), [])

    def test_short_document_has_no_arbitrary_length_quota(self) -> None:
        self.assertEqual(self.check_metadata(self.document()), [])

    def test_prose_padding_does_not_repair_missing_metadata(self) -> None:
        text = self.document().replace("Status: CURRENT", "")
        text += "\n" + "purpose state concurrency failure security observability testing " * 200
        self.assertTrue(any("Status:" in e for e in self.check_metadata(text)))

    def test_missing_title_or_sections_is_rejected(self) -> None:
        for text in (self.document().replace("# Bounded parser", "Bounded parser"),
                     self.document().replace("## Contract", "Contract")):
            with self.subTest(text=text):
                self.assertTrue(self.check_metadata(text))

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
