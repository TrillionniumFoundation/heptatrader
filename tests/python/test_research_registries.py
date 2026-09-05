import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "check_research_registries",
    ROOT / "scripts/check_research_registries.py",
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class ResearchRegistryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.datasets = MODULE.load_object(
            ROOT / "docs/research/dataset-registry-v1.json"
        )
        self.features = MODULE.load_object(
            ROOT / "docs/research/feature-registry-v1.json"
        )

    def test_checked_in_registries_are_valid(self) -> None:
        MODULE.run()

    def _temporary_root(self, csv_text: str) -> Path:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        fixture = root / "research/fixtures/eurusd_sequence_v1.csv"
        fixture.parent.mkdir(parents=True)
        fixture.write_text(csv_text, encoding="utf-8")
        implementation = root / "HeptaTrade/features/feature_generation.cpp"
        implementation.parent.mkdir(parents=True)
        implementation.write_text('const char* id = "mid-spread-v1";\n')
        return root

    def test_digest_tampering_is_rejected(self) -> None:
        csv_text = (
            ROOT / "research/fixtures/eurusd_sequence_v1.csv"
        ).read_text(encoding="utf-8") + "\n"
        root = self._temporary_root(csv_text)
        with self.assertRaises(MODULE.RegistryError):
            MODULE.validate_dataset_registry(copy.deepcopy(self.datasets), root)

    def test_future_available_time_and_sequence_gap_are_rejected(self) -> None:
        csv_text = "\n".join([
            "venue,instrument,producer_epoch,sequence,observed_at_ms,available_at_ms,bid_raw,ask_raw",
            "SIM,EUR.USD,1,1,1000,999,1100000,1200000",
            "SIM,EUR.USD,1,3,2000,2010,1110000,1210000",
            "",
        ])
        root = self._temporary_root(csv_text)
        datasets = copy.deepcopy(self.datasets)
        raw = (root / datasets["datasets"][0]["path"]).read_bytes()
        import hashlib
        datasets["datasets"][0]["sha256"] = hashlib.sha256(raw).hexdigest()
        datasets["datasets"][0]["row_count"] = 2
        with self.assertRaises(MODULE.RegistryError):
            MODULE.validate_dataset_registry(datasets, root)

    def test_unknown_feature_dataset_is_rejected(self) -> None:
        indexed = MODULE.validate_dataset_registry(self.datasets)
        features = copy.deepcopy(self.features)
        features["features"][0]["dataset"] = "unknown"
        with self.assertRaises(MODULE.RegistryError):
            MODULE.validate_feature_registry(features, indexed)

    def test_unsafe_registry_path_is_rejected(self) -> None:
        datasets = copy.deepcopy(self.datasets)
        datasets["datasets"][0]["path"] = "../secret.csv"
        with self.assertRaises(MODULE.RegistryError):
            MODULE.validate_dataset_registry(datasets)


if __name__ == "__main__":
    unittest.main()
