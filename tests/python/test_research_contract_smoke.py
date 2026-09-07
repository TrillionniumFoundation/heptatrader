from __future__ import annotations

import ast
import json
from pathlib import Path
import py_compile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
FORBIDDEN_IMPORT_PREFIXES = (
    "ibapi",
    "xtquant",
    "ctp",
)
FORBIDDEN_CALL_NAMES = {
    "placeOrder",
    "order_stock",
    "order_stock_async",
    "ReqOrderInsert",
}


class ResearchContractSmokeTests(unittest.TestCase):
    def research_paths(self) -> list[Path]:
        catalog = json.loads(
            (ROOT / "docs/module-catalog.json").read_text(encoding="utf-8")
        )
        module = next(
            item for item in catalog["modules"] if item["id"] == "shadow-research"
        )
        return [ROOT / item for item in module["implementation"]]

    def test_catalogued_research_modules_compile_and_do_not_import_broker_sdks(self) -> None:
        for path in self.research_paths():
            with self.subTest(path=path):
                self.assertTrue(path.is_file())
                with tempfile.TemporaryDirectory() as directory:
                    py_compile.compile(
                        str(path),
                        cfile=str(Path(directory) / (path.stem + ".pyc")),
                        doraise=True,
                    )
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
                imports: list[str] = []
                calls: list[str] = []
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        imports.extend(alias.name for alias in node.names)
                    elif isinstance(node, ast.ImportFrom) and node.module:
                        imports.append(node.module)
                    elif isinstance(node, ast.Call):
                        function = node.func
                        if isinstance(function, ast.Name):
                            calls.append(function.id)
                        elif isinstance(function, ast.Attribute):
                            calls.append(function.attr)
                self.assertFalse(
                    any(name.startswith(FORBIDDEN_IMPORT_PREFIXES) for name in imports),
                    (path, imports),
                )
                self.assertEqual(set(calls) & FORBIDDEN_CALL_NAMES, set(), path)

    def test_shadow_document_does_not_claim_missing_observer_is_installed(self) -> None:
        text = (ROOT / "docs/modules/shadow-research.md").read_text(encoding="utf-8")
        self.assertIn("does not contain a canonical `hepta_bounded_shadow_observer.py`", text)
        self.assertNotIn("Installed Files", text)


if __name__ == "__main__":
    unittest.main()
