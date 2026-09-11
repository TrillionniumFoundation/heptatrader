from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import check_component_coverage as coverage  # noqa: E402


class ComponentCoverageTests(unittest.TestCase):
    def fixture(self, directory: str) -> Path:
        root = Path(directory)
        for relative in (
            "HeptaTrade/execution",
            "scripts",
            "docs/modules",
            "docs",
        ):
            (root / relative).mkdir(parents=True, exist_ok=True)
        (root / "CMakeLists.txt").write_text("project(fixture)\n", encoding="utf-8")
        (root / "HeptaTrade/execution/core.cpp").write_text(
            "int fixture() { return 0; }\n", encoding="utf-8"
        )
        (root / "scripts/check.py").write_text("pass\n", encoding="utf-8")
        for name in ("release-engineering", "execution-service", "repository-control"):
            (root / f"docs/modules/{name}.md").write_text(
                f"# {name}\n\nStatus: CURRENT\n", encoding="utf-8"
            )
        catalog = {
            "schema": "heptatrader.module-catalog.v1",
            "modules": [
                {
                    "id": "release-engineering",
                    "status": "CURRENT",
                    "document": "docs/modules/release-engineering.md",
                    "implementation": ["CMakeLists.txt"],
                    "tests": ["scripts/check.py"],
                    "broker_mutation": "NONE",
                    "production_authorized": False,
                },
                {
                    "id": "execution-service",
                    "status": "CURRENT",
                    "document": "docs/modules/execution-service.md",
                    "implementation": ["HeptaTrade/execution"],
                    "tests": ["scripts/check.py"],
                    "broker_mutation": "SOLE_AUTHORITY",
                    "production_authorized": False,
                },
                {
                    "id": "repository-control",
                    "status": "CURRENT",
                    "document": "docs/modules/repository-control.md",
                    "implementation": ["scripts/check.py"],
                    "tests": ["scripts/check.py"],
                    "broker_mutation": "NONE",
                    "production_authorized": False,
                },
            ],
        }
        inventory = {
            "schema": "heptatrader.build-targets.v1",
            "profiles": {
                "core": {
                    "targets": [
                        {
                            "name": "fixture",
                            "type": "STATIC_LIBRARY",
                            "translation_units": [
                                {
                                    "path": "HeptaTrade/execution/core.cpp",
                                    "kind": "implementation",
                                    "owner": "execution-service",
                                }
                            ],
                        }
                    ]
                }
            },
        }
        (root / "docs/module-catalog.json").write_text(
            json.dumps(catalog, indent=2) + "\n", encoding="utf-8"
        )
        (root / "docs/build-targets.json").write_text(
            json.dumps(inventory, indent=2) + "\n", encoding="utf-8"
        )
        (root / "docs/DEVELOPMENT-DOCUMENTATION-INDEX.md").write_text(
            "\n".join(
                [
                    "# Development documentation",
                    "docs/modules/release-engineering.md",
                    "docs/modules/execution-service.md",
                    "docs/modules/repository-control.md",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        subprocess.run(["git", "-C", str(root), "add", "."], check=True)
        return root

    def test_fixture_has_complete_discovered_ownership(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(coverage.validate(self.fixture(directory)), [])

    def test_new_unowned_production_component_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.fixture(directory)
            path = root / "HeptaTrade/new_component/new.cpp"
            path.parent.mkdir()
            path.write_text("int orphan() { return 0; }\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(root), "add", str(path)], check=True)
            errors = coverage.validate(root)
            self.assertTrue(
                any("unowned production path" in item and "new_component" in item for item in errors),
                errors,
            )

    def test_new_unowned_script_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.fixture(directory)
            script = root / "scripts/new_release_path.py"
            script.parent.mkdir(parents=True, exist_ok=True)
            script.write_text("print('unowned')\n", encoding="utf-8")
            subprocess.run(
                ["git", "-C", str(root), "add", script.relative_to(root)],
                check=True,
            )
            errors = coverage.validate(root)
            self.assertTrue(
                any("unowned production path: scripts/new_release_path.py" in item for item in errors),
                errors,
            )

    def test_build_inventory_owner_drift_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.fixture(directory)
            path = root / "docs/build-targets.json"
            value = json.loads(path.read_text(encoding="utf-8"))
            value["profiles"]["core"]["targets"][0]["translation_units"][0][
                "owner"
            ] = "release-engineering"
            path.write_text(json.dumps(value), encoding="utf-8")
            subprocess.run(["git", "-C", str(root), "add", str(path)], check=True)
            errors = coverage.validate(root)
            self.assertTrue(any("owner drift" in item for item in errors), errors)

    def test_development_index_must_name_every_module_document(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.fixture(directory)
            index = root / "docs/DEVELOPMENT-DOCUMENTATION-INDEX.md"
            index.write_text("# Development documentation\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(root), "add", str(index)], check=True)
            errors = coverage.validate(root)
            self.assertTrue(
                any("omits module document" in item for item in errors), errors
            )


if __name__ == "__main__":
    unittest.main()
