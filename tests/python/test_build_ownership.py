from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import verify_build_ownership as ownership  # noqa: E402


class BuildOwnershipTests(unittest.TestCase):
    def fixture(self, directory: str) -> tuple[Path, dict]:
        root = Path(directory)
        (root / "HeptaTrade").mkdir()
        (root / "tests").mkdir()
        (root / "docs/modules").mkdir(parents=True)
        (root / "CMakeLists.txt").write_text(
            "cmake_minimum_required(VERSION 3.16)\nproject(OwnershipFixture LANGUAGES CXX)\n"
            "set(CMAKE_CXX_STANDARD 11)\ninclude(CTest)\n"
            "add_subdirectory(HeptaTrade)\nadd_subdirectory(tests)\n")
        (root / "HeptaTrade/CMakeLists.txt").write_text(
            "set(COMPONENT_SOURCES component.cpp)\n"
            "add_library(component STATIC ${COMPONENT_SOURCES})\n")
        (root / "HeptaTrade/component.cpp").write_text("int component() { return 1; }\n")
        (root / "tests/CMakeLists.txt").write_text(
            "add_executable(component_tests component_tests.cpp)\n"
            "target_link_libraries(component_tests PRIVATE component)\n"
            "add_test(NAME component_tests COMMAND component_tests)\n")
        (root / "tests/component_tests.cpp").write_text("int main() { return 0; }\n")
        (root / "docs/modules/component.md").write_text("# Component fixture\n")
        catalog = {"schema": "heptatrader.module-catalog.v1", "modules": [{
            "id": "component", "document": "docs/modules/component.md",
            "implementation": ["HeptaTrade"]}]}
        (root / "docs/module-catalog.json").write_text(json.dumps(catalog))
        core = ownership.observe(root, "core")
        ib = copy.deepcopy(core)
        ib["requires_ib_sdk"] = True
        ib["options"] = ownership.profile_options("ib")
        inventory = {"schema": ownership.SCHEMA, "coverage": ownership.COVERAGE,
                     "profiles": {"core": core, "ib": ib}}
        return root, inventory

    def implementation(self, inventory: dict) -> dict:
        return next(source for target in inventory["profiles"]["core"]["targets"]
                    for source in target["translation_units"] if source["kind"] == "implementation")

    def test_repository_core_matches_fresh_cmake_model(self) -> None:
        ownership.verify(ROOT, ownership.load_json(ROOT / "docs/build-targets.json"))

    def test_real_cmake_fixture_matches(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, inventory = self.fixture(temporary)
            ownership.verify(root, inventory)

    def test_new_target_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, inventory = self.fixture(temporary)
            with (root / "HeptaTrade/CMakeLists.txt").open("a") as stream:
                stream.write("add_library(unreviewed STATIC component.cpp)\n")
            with self.assertRaisesRegex(ownership.OwnershipError, "inventory drift"):
                ownership.verify(root, inventory)

    def test_new_translation_unit_expanded_from_variable_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, inventory = self.fixture(temporary)
            (root / "HeptaTrade/extra.cpp").write_text("int extra() { return 2; }\n")
            cmake = root / "HeptaTrade/CMakeLists.txt"
            cmake.write_text(cmake.read_text().replace("component.cpp)", "component.cpp extra.cpp)"))
            with self.assertRaisesRegex(ownership.OwnershipError, "inventory drift"):
                ownership.verify(root, inventory)

    def test_unmapped_translation_unit_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, inventory = self.fixture(temporary)
            (root / "orphan.cpp").write_text("int orphan() { return 3; }\n")
            with (root / "HeptaTrade/CMakeLists.txt").open("a") as stream:
                stream.write("target_sources(component PRIVATE ../orphan.cpp)\n")
            with self.assertRaisesRegex(ownership.OwnershipError, "canonical module owner"):
                ownership.verify(root, inventory)

    def test_unclassified_generated_translation_unit_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, inventory = self.fixture(temporary)
            with (root / "HeptaTrade/CMakeLists.txt").open("a") as stream:
                stream.write('file(WRITE "${CMAKE_CURRENT_BINARY_DIR}/generated.cpp" "int generated() { return 5; }\\n")\n'
                             'target_sources(component PRIVATE "${CMAKE_CURRENT_BINARY_DIR}/generated.cpp")\n')
            with self.assertRaisesRegex(ownership.OwnershipError, "unclassified.*translation unit"):
                ownership.verify(root, inventory)

    def test_unknown_owner_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, inventory = self.fixture(temporary)
            self.implementation(inventory)["owner"] = "imaginary-module"
            with self.assertRaisesRegex(ownership.OwnershipError, "owner mismatch"):
                ownership.verify(root, inventory)

    def test_ambiguous_catalog_ownership_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, inventory = self.fixture(temporary)
            path = root / "docs/module-catalog.json"
            catalog = json.loads(path.read_text())
            duplicate = dict(catalog["modules"][0], id="conflicting-component")
            catalog["modules"].append(duplicate)
            path.write_text(json.dumps(catalog))
            with self.assertRaisesRegex(ownership.OwnershipError, "canonical module owner"):
                ownership.verify(root, inventory)

    def test_catalog_owner_rename_invalidates_reviewed_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, inventory = self.fixture(temporary)
            path = root / "docs/module-catalog.json"
            catalog = json.loads(path.read_text());catalog["modules"][0]["id"] = "renamed-component"
            path.write_text(json.dumps(catalog))
            with self.assertRaisesRegex(ownership.OwnershipError, "owner mismatch"):
                ownership.verify(root, inventory)

    def test_duplicate_target_and_translation_unit_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, inventory = self.fixture(temporary)
            repeated = copy.deepcopy(inventory)
            targets = repeated["profiles"]["core"]["targets"]
            targets.append(copy.deepcopy(targets[0]))
            with self.assertRaisesRegex(ownership.OwnershipError, "duplicate target"):
                ownership.verify(root, repeated)
            repeated = copy.deepcopy(inventory)
            sources = repeated["profiles"]["core"]["targets"][0]["translation_units"]
            sources.append(copy.deepcopy(sources[0]))
            with self.assertRaisesRegex(ownership.OwnershipError, "duplicate translation unit"):
                ownership.verify(root, repeated)

    def test_missing_reviewed_target_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, inventory = self.fixture(temporary)
            targets = inventory["profiles"]["core"]["targets"]
            targets[:] = [target for target in targets if target["name"] != "component_tests"]
            with self.assertRaisesRegex(ownership.OwnershipError, "inventory drift"):
                ownership.verify(root, inventory)

    def test_core_cannot_claim_external_sdk_ownership(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, inventory = self.fixture(temporary)
            source = self.implementation(inventory)
            source["kind"] = "external_ib_sdk";source["owner"] = None
            with self.assertRaisesRegex(ownership.OwnershipError, "external SDK"):
                ownership.verify(root, inventory)

    def test_ib_external_source_is_not_a_repository_owned_translation_unit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, inventory = self.fixture(temporary)
            sdk = root / "fixture-sdk";sdk.mkdir()
            decimal = sdk / "fixture.a";decimal.write_text("test fixture; not a real archive")
            (sdk / "sdk.cpp").write_text("int sdk_fixture() { return 4; }\n")
            # It must be outside the repository to model a real external SDK.
            with tempfile.TemporaryDirectory() as external_directory:
                external = Path(external_directory)
                (external / "sdk.cpp").write_text((sdk / "sdk.cpp").read_text())
                with (root / "HeptaTrade/CMakeLists.txt").open("a") as stream:
                    stream.write('if(HEPTA_ENABLE_IBAPI)\nadd_library(ib_fixture STATIC "${IBAPI_ROOT}/sdk.cpp")\nendif()\n')
                inventory["profiles"]["ib"] = ownership.observe(root, "ib", external, decimal)
                source = next(source for target in inventory["profiles"]["ib"]["targets"]
                              for source in target["translation_units"] if source["kind"] == "external_ib_sdk")
                self.assertIsNone(source["owner"])
                ownership.verify(root, inventory, "ib", external, decimal)
                source["owner"] = "component"
                with self.assertRaisesRegex(ownership.OwnershipError, "external SDK"):
                    ownership.verify(root, inventory, "ib", external, decimal)

    def test_symlinked_repository_source_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, inventory = self.fixture(temporary)
            source = root / "HeptaTrade/component.cpp"
            original = root / "HeptaTrade/original.cpp";source.rename(original);source.symlink_to(original)
            with self.assertRaisesRegex(ownership.OwnershipError, "symlinked"):
                ownership.verify(root, inventory)

    def test_duplicate_json_keys_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "inventory.json"
            path.write_text('{"schema":"first","schema":"second"}')
            with self.assertRaisesRegex(ownership.OwnershipError, "duplicate JSON key"):
                ownership.load_json(path)


if __name__ == "__main__":
    unittest.main()
