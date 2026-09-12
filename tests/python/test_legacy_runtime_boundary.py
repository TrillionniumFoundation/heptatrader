from __future__ import annotations

import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]


class LegacyRuntimeBoundaryTests(unittest.TestCase):
    def test_capability_matrix_keeps_real_authority_fail_closed(self) -> None:
        matrix = json.loads(
            (ROOT / "docs/capabilities.json").read_text(encoding="utf-8")
        )
        self.assertIs(matrix["live_trading_authorized"], False)
        capabilities = {item["id"]: item for item in matrix["capabilities"]}
        self.assertEqual(
            capabilities["deterministic-simulator"],
            {
                "id": "deterministic-simulator",
                "status": "CURRENT",
                "order_transport": "LOCAL_DETERMINISTIC",
                "requires_external_qualification": False,
                "transport_implemented": True,
                "advertisable": False,
                "authorized": False,
            },
        )
        self.assertEqual(capabilities["ib-paper"]["status"], "QUALIFICATION_REQUIRED")
        self.assertTrue(capabilities["ib-paper"]["requires_external_qualification"])
        for capability_id in ("ctp", "xt-qmt"):
            capability = capabilities[capability_id]
            self.assertEqual(capability["status"], "EXPERIMENTAL")
            self.assertEqual(capability["order_transport"], "NONE")
            self.assertFalse(capability["transport_implemented"])
            self.assertFalse(capability["advertisable"])
            self.assertFalse(capability["authorized"])
        self.assertEqual(capabilities["live"]["status"], "UNAVAILABLE")
        self.assertEqual(capabilities["live"]["order_transport"], "NONE")
        self.assertFalse(capabilities["live"]["transport_implemented"])
        self.assertFalse(capabilities["live"]["advertisable"])
        self.assertFalse(capabilities["live"]["authorized"])

    def test_module_catalog_marks_legacy_and_experimental_boundaries(self) -> None:
        catalog = json.loads(
            (ROOT / "docs/module-catalog.json").read_text(encoding="utf-8")
        )
        modules = {item["id"]: item for item in catalog["modules"]}
        self.assertEqual(modules["legacy-runtime"]["status"], "LEGACY")
        self.assertEqual(modules["legacy-runtime"]["broker_mutation"], "NONE")
        self.assertIs(modules["legacy-runtime"]["production_authorized"], False)
        for module_id in ("ctp-adapter", "xt-adapter"):
            self.assertEqual(modules[module_id]["status"], "EXPERIMENTAL")
            self.assertEqual(modules[module_id]["broker_mutation"], "NONE")
            self.assertIs(modules[module_id]["production_authorized"], False)


if __name__ == "__main__":
    unittest.main()
