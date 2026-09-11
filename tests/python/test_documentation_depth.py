#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import check_documentation as documentation  # noqa: E402


class DocumentationDepthTests(unittest.TestCase):
    def test_repository_documents_satisfy_depth_contract(self) -> None:
        errors = documentation.validate(ROOT)
        depth_errors = [
            error
            for error in errors
            if "technical document is too shallow" in error
            or "missing required engineering topics" in error
            or "engineering topic groups" in error
            or "substantive prose paragraphs" in error
        ]
        self.assertEqual(depth_errors, [])

    def test_catalog_shaped_stub_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "docs/modules/stub.md"
            path.parent.mkdir(parents=True)
            path.write_text(
                "# Stub\n\n"
                "Status: CURRENT\n"
                "Applies to: repository HEAD\n"
                "Implementation: `src`\n"
                "Tests: `tests`\n\n"
                "## Responsibilities\n\nExists.\n\n"
                "## Tests\n\nExists.\n",
                encoding="utf-8",
            )
            modules = {
                "stub": {
                    "id": "stub",
                    "status": "CURRENT",
                    "document": "docs/modules/stub.md",
                    "implementation": ["src"],
                    "tests": ["tests"],
                    "broker_mutation": "NONE",
                    "production_authorized": False,
                }
            }
            errors: list[str] = []
            documentation._validate_documentation_depth(root, modules, errors)
            self.assertTrue(errors)
            self.assertTrue(any("too shallow" in error for error in errors))
            self.assertTrue(any("missing required engineering topics" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
