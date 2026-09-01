import copy
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "generate_contract_bindings",
    ROOT / "scripts/generate_contract_bindings.py",
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class GeneratedContractBindingsTest(unittest.TestCase):
    def test_catalog_and_checked_in_bindings_are_current(self) -> None:
        self.assertTrue(MODULE.run(False))

    def test_duplicate_field_id_is_rejected(self) -> None:
        catalog = MODULE.load_catalog()
        invalid = copy.deepcopy(catalog)
        invalid["fields"][1]["id"] = invalid["fields"][0]["id"]
        with self.assertRaises(MODULE.CatalogError):
            MODULE.validate_catalog(invalid)

    def test_numeric_policy_drift_is_rejected(self) -> None:
        catalog = MODULE.load_catalog()
        invalid = copy.deepcopy(catalog)
        invalid["numeric_policy"]["scale"] = 100
        with self.assertRaises(MODULE.CatalogError):
            MODULE.validate_catalog(invalid)

    def test_alias_to_unknown_field_is_rejected(self) -> None:
        catalog = MODULE.load_catalog()
        invalid = copy.deepcopy(catalog)
        invalid["target_intent_aliases"]["target_position"] = "unknown"
        with self.assertRaises(MODULE.CatalogError):
            MODULE.validate_catalog(invalid)


if __name__ == "__main__":
    unittest.main()
