from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
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
        paths: list[Path] = []
        for item in module["implementation"]:
            path = ROOT / item
            if path.is_dir():
                paths.extend(
                    candidate
                    for candidate in sorted(path.rglob("*"))
                    if candidate.is_file()
                )
            else:
                paths.append(path)
        return paths

    def test_catalogued_research_modules_compile_and_do_not_import_broker_sdks(self) -> None:
        for path in self.research_paths():
            with self.subTest(path=path):
                self.assertTrue(path.is_file())
                if path.suffix == ".json":
                    value = json.loads(path.read_text(encoding="utf-8"))
                    self.assertIsInstance(value, dict)
                    continue
                self.assertEqual(path.suffix, ".py", f"unsupported research asset: {path}")
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

    def test_catalogued_research_programs_import_without_running_cli(self) -> None:
        paths = [str(path) for path in self.research_paths() if path.suffix == ".py"]
        code = """
import importlib.util, pathlib, sys
sys.path.insert(0, str(pathlib.Path(sys.argv[1])))
for index, name in enumerate(sys.argv[2:]):
    spec = importlib.util.spec_from_file_location("research_smoke_" + str(index), name)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
"""
        result = subprocess.run(
            [sys.executable, "-c", code, str(ROOT / "scripts"), *paths],
            capture_output=True, text=True, timeout=15,
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
