from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import check_documentation as documentation  # noqa: E402


class DocumentationControlPlaneTests(unittest.TestCase):
    def test_repository_documentation_is_consistent(self) -> None:
        self.assertEqual(documentation.validate(ROOT), [])

    def fixture(self, directory: str) -> Path:
        root = Path(directory)
        (root / "docs/modules").mkdir(parents=True)
        (root / "docs/operations").mkdir(parents=True)
        (root / "impl").mkdir()
        (root / "tests").mkdir()
        (root / ".github/workflows").mkdir(parents=True)
        (root / "README.md").write_text(
            "# Fixture\n\n" + "documented repository " * 30,
            encoding="utf-8",
        )
        (root / "impl/component.txt").write_text("implementation\n", encoding="utf-8")
        (root / "tests/component.txt").write_text("test\n", encoding="utf-8")
        for workflow in documentation.REQUIRED_WORKFLOWS:
            path = root / workflow
            path.write_text("name: fixture\n", encoding="utf-8")
        (root / "docs/index.md").write_text("# Index\n", encoding="utf-8")
        (root / "docs/DOCUMENTATION-POLICY.md").write_text("# Policy\n", encoding="utf-8")
        (root / "docs/modules/component.md").write_text(
            "# Component\n\n"
            "Status: CURRENT  \n"
            "Applies to: fixture  \n"
            "Implementation: `impl/component.txt`  \n"
            "Tests: `tests/component.txt`\n\n"
            "## Responsibilities\n\nFixture.\n",
            encoding="utf-8",
        )
        catalog = {
            "schema": "heptatrader.module-catalog.v1",
            "modules": [{
                "id": "component",
                "status": "CURRENT",
                "document": "docs/modules/component.md",
                "implementation": ["impl/component.txt"],
                "tests": ["tests/component.txt"],
                "broker_mutation": "NONE",
                "production_authorized": False,
            }],
        }
        capabilities = {
            "schema": "heptatrader.capabilities.v1",
            "live_trading_authorized": False,
            "capabilities": [
                {"id": "deterministic-simulator", "status": "CURRENT", "order_transport": "LOCAL_DETERMINISTIC", "requires_external_qualification": False, "advertise_as_real_venue": False},
                {"id": "ib-paper", "status": "QUALIFICATION_REQUIRED", "order_transport": "IB_CPP_API", "requires_external_qualification": True, "advertise_as_real_venue": True},
                {"id": "ctp", "status": "EXPERIMENTAL", "order_transport": "NONE", "requires_external_qualification": True, "advertise_as_real_venue": False},
                {"id": "xt-qmt", "status": "EXPERIMENTAL", "order_transport": "NONE", "requires_external_qualification": True, "advertise_as_real_venue": False},
                {"id": "live", "status": "UNAVAILABLE", "order_transport": "NONE", "requires_external_qualification": True, "advertise_as_real_venue": False},
            ],
        }
        # Add matching catalog entries needed by capability cross-checks.
        for module_id, status in (
            ("ib-paper", "QUALIFICATION_REQUIRED"),
            ("ctp-adapter", "EXPERIMENTAL"),
            ("xt-adapter", "EXPERIMENTAL"),
        ):
            document = root / f"docs/modules/{module_id}.md"
            document.write_text(
                f"# {module_id}\n\nStatus: {status}  \nApplies to: fixture  \n"
                "Implementation: `impl/component.txt`  \nTests: `tests/component.txt`\n\n"
                "## Responsibilities\n\nFixture.\n",
                encoding="utf-8",
            )
            catalog["modules"].append({
                "id": module_id,
                "status": status,
                "document": f"docs/modules/{module_id}.md",
                "implementation": ["impl/component.txt"],
                "tests": ["tests/component.txt"],
                "broker_mutation": "NONE",
                "production_authorized": False,
            })
        (root / "docs/module-catalog.json").write_text(
            json.dumps(catalog, indent=2) + "\n", encoding="utf-8")
        (root / "docs/capabilities.json").write_text(
            json.dumps(capabilities, indent=2) + "\n", encoding="utf-8")
        return root

    def test_missing_implementation_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.fixture(directory)
            catalog_path = root / "docs/module-catalog.json"
            catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
            catalog["modules"][0]["implementation"] = ["impl/missing.txt"]
            catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
            errors = documentation.validate(root)
            self.assertTrue(any("impl/missing.txt" in item for item in errors), errors)

    def test_experimental_transport_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.fixture(directory)
            path = root / "docs/capabilities.json"
            value = json.loads(path.read_text(encoding="utf-8"))
            for capability in value["capabilities"]:
                if capability["id"] == "ctp":
                    capability["order_transport"] = "CTP_API"
                    capability["advertise_as_real_venue"] = True
            path.write_text(json.dumps(value), encoding="utf-8")
            errors = documentation.validate(root)
            self.assertTrue(any("must fail closed" in item for item in errors), errors)

    def test_live_authorization_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.fixture(directory)
            path = root / "docs/capabilities.json"
            value = json.loads(path.read_text(encoding="utf-8"))
            value["live_trading_authorized"] = True
            path.write_text(json.dumps(value), encoding="utf-8")
            errors = documentation.validate(root)
            self.assertTrue(any("LIVE must remain unauthorized" in item for item in errors), errors)


if __name__ == "__main__":
    unittest.main()
