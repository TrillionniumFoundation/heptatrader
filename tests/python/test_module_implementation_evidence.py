from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "check_module_implementation_evidence",
    ROOT / "scripts/check_module_implementation_evidence.py",
)
assert SPEC is not None and SPEC.loader is not None
CHECKER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECKER)


class ModuleImplementationEvidenceTests(unittest.TestCase):
    def _fixture(self, root: Path) -> None:
        (root / "docs/modules/manifests").mkdir(parents=True)
        (root / "docs/program").mkdir(parents=True)
        (root / "src").mkdir()
        (root / "tests").mkdir()
        (root / "tests/test_boundary.py").write_text("# evidence\n", encoding="utf-8")

        manifest = {
            "schema": "heptatrader.module-manifest.v3",
            "id": "hepta.test.boundary",
            "resource_budget": "test-v1",
        }
        (root / "docs/modules/manifests/hepta-test-boundary.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )

        guardrail = {
            "scope": "repository-planning-ceiling-not-target-host-slo",
            "enforcement": "test-checked",
            "max_threads": 1,
            "max_queue_items": 1,
            "max_queue_bytes": 4096,
            "max_memory_mib": 64,
            "max_file_descriptors": 8,
            "deadline_ms": 1000,
            "telemetry_series_max": 16,
            "restart_burst_max": 1,
        }
        registry = {
            "schema": "heptatrader.module-registry.v2",
            "manifest_paths": [
                "modules/manifests/hepta-test-boundary.json"
            ],
            "implementation_evidence_policy": {
                "schema": "heptatrader.module-implementation-evidence.v1",
                "allowed_states": [
                    "implemented",
                    "bounded-implementation",
                    "contract-only",
                    "harness-only",
                    "unsupported",
                    "external-qualification-required",
                ],
                "truth_floor": {
                    "hepta.test.boundary": "bounded-implementation"
                },
                "external_gate_ids": ["G-EXTERNAL-001"],
            },
            "resource_guardrail_profiles": {
                "test-profile": guardrail,
            },
            "implementation_evidence": [
                {
                    "module_id": "hepta.test.boundary",
                    "state": "bounded-implementation",
                    "implemented_scope": ["typed boundary"],
                    "excluded_scope": ["distributed runtime"],
                    "source_evidence": ["src/"],
                    "test_evidence": ["tests/test_boundary.py"],
                    "external_gates": [],
                    "resource_guardrail_profile": "test-profile",
                }
            ],
        }
        (root / "docs/modules/module-registry-v2.json").write_text(
            json.dumps(registry), encoding="utf-8"
        )
        gaps = {
            "schema": "heptatrader.gap-registry.v2",
            "gaps": [
                {"id": "G-EXTERNAL-001", "state": "in-progress"}
            ],
        }
        (root / "docs/program/gap-registry-v2.json").write_text(
            json.dumps(gaps), encoding="utf-8"
        )

    def test_valid_fixture_passes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._fixture(root)
            self.assertEqual([], CHECKER.validate(root))

    def test_missing_evidence_path_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._fixture(root)
            registry_path = root / "docs/modules/module-registry-v2.json"
            registry = json.loads(registry_path.read_text(encoding="utf-8"))
            registry["implementation_evidence"][0]["test_evidence"] = [
                "tests/missing.py"
            ]
            registry_path.write_text(json.dumps(registry), encoding="utf-8")
            errors = CHECKER.validate(root)
            self.assertTrue(
                any("evidence path does not exist" in error for error in errors),
                errors,
            )

    def test_truth_floor_prevents_capability_inflation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._fixture(root)
            registry_path = root / "docs/modules/module-registry-v2.json"
            registry = json.loads(registry_path.read_text(encoding="utf-8"))
            registry["implementation_evidence"][0]["state"] = "implemented"
            registry["implementation_evidence"][0]["excluded_scope"] = []
            registry_path.write_text(json.dumps(registry), encoding="utf-8")
            errors = CHECKER.validate(root)
            self.assertTrue(
                any("truth floor requires" in error for error in errors),
                errors,
            )

    def test_external_gate_cannot_be_closed_without_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._fixture(root)
            gap_path = root / "docs/program/gap-registry-v2.json"
            gaps = json.loads(gap_path.read_text(encoding="utf-8"))
            gaps["gaps"][0]["state"] = "closed"
            gap_path.write_text(json.dumps(gaps), encoding="utf-8")
            errors = CHECKER.validate(root)
            self.assertTrue(
                any("closed without a separate" in error for error in errors),
                errors,
            )


if __name__ == "__main__":
    unittest.main()
