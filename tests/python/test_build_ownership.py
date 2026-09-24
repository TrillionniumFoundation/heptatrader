from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import verify_build_ownership as ownership  # noqa: E402


class BuildOwnershipTests(unittest.TestCase):
    def fixture(self, directory: str) -> Path:
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
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        subprocess.run(["git", "-C", str(root), "add", "."], check=True)
        return root

    @staticmethod
    def track(root: Path, path: Path) -> None:
        subprocess.run(["git", "-C", str(root), "add", path.relative_to(root)], check=True)

    def test_repository_core_matches_live_cmake_model(self) -> None:
        observed = ownership.verify(ROOT)
        self.assertTrue(observed["targets"])

    def test_real_cmake_fixture_matches_without_reviewed_graph(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = self.fixture(temporary)
            observed = ownership.verify(root)
            self.assertTrue(any(t["name"] == "component" for t in observed["targets"]))

    def test_new_target_using_owned_source_needs_no_inventory_ceremony(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = self.fixture(temporary)
            with (root / "HeptaTrade/CMakeLists.txt").open("a") as stream:
                stream.write("add_library(additional STATIC component.cpp)\n")
            observed = ownership.verify(root)
            self.assertTrue(any(t["name"] == "additional" for t in observed["targets"]))

    def test_new_owned_translation_unit_is_accepted_when_live_build_reaches_it(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = self.fixture(temporary)
            extra = root / "HeptaTrade/extra.cpp"
            extra.write_text("int extra() { return 2; }\n")
            self.track(root, extra)
            cmake = root / "HeptaTrade/CMakeLists.txt"
            cmake.write_text(cmake.read_text().replace("component.cpp)", "component.cpp extra.cpp)"))
            ownership.verify(root)

    def test_owned_translation_unit_missing_from_live_build_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = self.fixture(temporary)
            extra = root / "HeptaTrade/extra.cpp"
            extra.write_text("int extra() { return 2; }\n")
            self.track(root, extra)
            with self.assertRaisesRegex(ownership.OwnershipError, "absent from the selected live CMake graph"):
                ownership.verify(root)

    def test_explicit_unbuilt_source_is_allowed_but_stale_unbuilt_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = self.fixture(temporary)
            extra = root / "HeptaTrade/extra.cpp"
            extra.write_text("int extra() { return 2; }\n")
            self.track(root, extra)
            path = root / "docs/module-catalog.json"
            catalog = json.loads(path.read_text())
            catalog["modules"][0]["unbuilt"] = ["HeptaTrade/extra.cpp"]
            path.write_text(json.dumps(catalog))
            self.track(root, path)
            ownership.verify(root)
            cmake = root / "HeptaTrade/CMakeLists.txt"
            cmake.write_text(cmake.read_text() + "target_sources(component PRIVATE extra.cpp)\n")
            with self.assertRaisesRegex(ownership.OwnershipError, "marks live CMake sources as unbuilt"):
                ownership.verify(root)

    def test_unmapped_translation_unit_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = self.fixture(temporary)
            orphan = root / "orphan.cpp"
            orphan.write_text("int orphan() { return 3; }\n")
            self.track(root, orphan)
            with (root / "HeptaTrade/CMakeLists.txt").open("a") as stream:
                stream.write("target_sources(component PRIVATE ../orphan.cpp)\n")
            with self.assertRaisesRegex(ownership.OwnershipError, "canonical module owner"):
                ownership.verify(root)

    def test_unclassified_generated_translation_unit_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = self.fixture(temporary)
            with (root / "HeptaTrade/CMakeLists.txt").open("a") as stream:
                stream.write('file(WRITE "${CMAKE_CURRENT_BINARY_DIR}/generated.cpp" "int generated() { return 5; }\\n")\n'
                             'target_sources(component PRIVATE "${CMAKE_CURRENT_BINARY_DIR}/generated.cpp")\n')
            with self.assertRaisesRegex(ownership.OwnershipError, "unclassified generated translation unit"):
                ownership.verify(root)

    def test_ambiguous_catalog_ownership_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = self.fixture(temporary)
            path = root / "docs/module-catalog.json"
            catalog = json.loads(path.read_text())
            duplicate = dict(catalog["modules"][0], id="conflicting-component")
            catalog["modules"].append(duplicate)
            path.write_text(json.dumps(catalog))
            self.track(root, path)
            with self.assertRaisesRegex(ownership.OwnershipError, "canonical module owner"):
                ownership.verify(root)

    def test_catalog_owner_rename_needs_no_generated_graph_update(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = self.fixture(temporary)
            path = root / "docs/module-catalog.json"
            catalog = json.loads(path.read_text())
            catalog["modules"][0]["id"] = "renamed-component"
            path.write_text(json.dumps(catalog))
            self.track(root, path)
            observed = ownership.verify(root)
            implementation = next(
                source for target in observed["targets"]
                for source in target["translation_units"]
                if source["kind"] == "implementation")
            self.assertEqual(implementation["owner"], "renamed-component")

    def test_ib_external_source_has_no_repository_owner(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, tempfile.TemporaryDirectory() as sdk_dir:
            root = self.fixture(temporary)
            sdk = Path(sdk_dir)
            (sdk / "sdk.cpp").write_text("int sdk_fixture() { return 4; }\n")
            decimal = sdk / "fixture.a"
            decimal.write_text("configure-only fixture")
            with (root / "HeptaTrade/CMakeLists.txt").open("a") as stream:
                stream.write('if(HEPTA_ENABLE_IBAPI)\nadd_library(ib_fixture STATIC "${IBAPI_ROOT}/sdk.cpp")\nendif()\n')
            observed = ownership.verify(root, "ib", sdk, decimal)
            external = next(
                source for target in observed["targets"]
                for source in target["translation_units"]
                if source["kind"] == "external_ib_sdk")
            self.assertIsNone(external["owner"])

    def test_symlinked_repository_source_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = self.fixture(temporary)
            source = root / "HeptaTrade/component.cpp"
            original = root / "HeptaTrade/original.cpp"
            source.rename(original)
            source.symlink_to(original.name)
            subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
            with self.assertRaisesRegex(ownership.OwnershipError, "symlinked"):
                ownership.verify(root)

    def test_duplicate_json_keys_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "catalog.json"
            path.write_text('{"schema":"first","schema":"second"}')
            with self.assertRaisesRegex(ownership.OwnershipError, "duplicate JSON key"):
                ownership.load_json(path)

    def test_core_report_needs_no_sdk_and_failed_all_does_not_replace_it(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = self.fixture(temporary)
            report = root / "observation.json"
            self.assertEqual(ownership.main(["--root", str(root), "--report", str(report)]), 0)
            value = json.loads(report.read_text())
            self.assertEqual(value["schema"], ownership.REPORT_SCHEMA)
            self.assertEqual(set(value["profiles"]), {"core"})
            previous = report.read_bytes()
            self.assertEqual(ownership.main(["--root", str(root), "--profile", "all",
                                            "--report", str(report)]), 1)
            self.assertEqual(report.read_bytes(), previous)
            self.assertFalse(list(report.parent.glob("observation.json.*.tmp")))


if __name__ == "__main__":
    unittest.main()
