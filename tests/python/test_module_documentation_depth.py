#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "check_module_documentation_depth",
    ROOT / "scripts/check_module_documentation_depth.py",
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ModuleDocumentationDepthTests(unittest.TestCase):
    def test_repository_documents_meet_depth_contract(self) -> None:
        self.assertEqual([], MODULE.validate(ROOT))

    def _fixture(self, document: str) -> Path:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "docs/modules").mkdir(parents=True)
        (root / "src").mkdir()
        (root / "tests").mkdir()
        (root / "src/component.cpp").write_text("int component;\n", encoding="utf-8")
        (root / "tests/component_test.cpp").write_text("int test;\n", encoding="utf-8")
        (root / "docs/modules/component.md").write_text(document, encoding="utf-8")
        catalog = {
            "schema": "heptatrader.module-catalog.v1",
            "modules": [{
                "id": "component",
                "status": "CURRENT",
                "document": "docs/modules/component.md",
                "implementation": ["src/component.cpp"],
                "tests": ["tests/component_test.cpp"],
                "broker_mutation": "NONE",
                "production_authorized": False,
            }],
        }
        (root / "docs/module-catalog.json").write_text(
            json.dumps(catalog), encoding="utf-8"
        )
        return root

    def test_shallow_document_is_rejected(self) -> None:
        root = self._fixture(
            "# Component\n\nStatus: CURRENT\n\nImplementation: `src/component.cpp`\n"
            "Tests: `tests/component_test.cpp`\n\n## Scope\nToo small.\n"
        )
        errors = MODULE.validate(root)
        self.assertTrue(any("technical sections" in error for error in errors))
        self.assertTrue(any("too shallow" in error for error in errors))

    def test_complete_document_is_accepted(self) -> None:
        prose = " ".join(["deterministic contract evidence state behavior"] * 70)
        root = self._fixture(
            "# Component\n\nStatus: CURRENT\nApplies to: fixture\n"
            "Implementation: `src/component.cpp`\n"
            "Tests: `tests/component_test.cpp`\n\n"
            f"## Responsibilities and scope\n{prose}\n\n"
            f"## Public contract\n{prose}\n\n"
            f"## State and persistence\n{prose}\n\n"
            f"## Security and trust boundary\n{prose}\n\n"
            f"## Failure behavior and recovery\n{prose}\n\n"
            f"## Observability and operations\n{prose}\n\n"
            f"## Test verification\n{prose}\n\n"
            f"## Known limitations\n{prose}\n"
        )
        self.assertEqual([], MODULE.validate(root))


if __name__ == "__main__":
    unittest.main()
