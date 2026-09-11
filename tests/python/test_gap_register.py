from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import check_gap_register as gaps  # noqa: E402
import verify_source_gap_closures as source_gaps  # noqa: E402


RELEASE_FIXTURE_FILES = (
    "CMakeLists.txt",
    "cmake/HeptaInstall.cmake",
    "scripts/build_release_package.py",
    "scripts/hepta_preflight.py",
    "scripts/hepta_preflight_core.py",
    "docs/preflight-policy-v1.json",
    "docs/module-catalog.json",
    "docs/modules/release-engineering.md",
    "docs/RELEASE-PUBLICATION-SECURITY.md",
    "docs/adr/0001-release-publication-atomicity.md",
    "docs/operations/release-package.md",
    "docs/operations/preflight.md",
    "tests/python/test_release_package.py",
    "tests/python/test_hepta_preflight.py",
    "tests/python/test_preflight_special_files.py",
    "tests/python/test_cmake_install_integration.py",
    "tests/python/test_preflight_entrypoint_boundary.py",
)


class GapRegisterTests(unittest.TestCase):
    def value(self) -> dict:
        return json.loads(
            (ROOT / "docs/gap-register.json").read_text(encoding="utf-8")
        )

    def release_fixture(self, destination: Path) -> Path:
        for relative in RELEASE_FIXTURE_FILES:
            source = ROOT / relative
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        return destination

    def test_repository_register_passes(self) -> None:
        self.assertEqual(gaps.validate(ROOT), [])

    def test_all_supported_scope_gaps_are_closed(self) -> None:
        value = self.value()
        self.assertEqual(value["authorization"]["source_state"], "READY")
        self.assertTrue(value["gaps"])
        self.assertFalse(
            [
                item
                for item in value["gaps"]
                if item["domain"] == "EXTERNAL"
            ]
        )
        self.assertTrue(
            all(item["state"] == "CLOSED_SOURCE" for item in value["gaps"])
        )
        self.assertTrue(
            all(
                item["blocking_authorization"] is False
                for item in value["gaps"]
            )
        )
        self.assertEqual(gaps.REQUIRED_EXTERNAL_GAPS, {})
        self.assertEqual(source_gaps.EXPECTED_EXTERNAL_GAPS, set())

    def test_optional_broker_capability_does_not_self_authorize(self) -> None:
        value = self.value()
        self.assertIs(value["authorization"]["paper_authorized"], False)
        self.assertIs(value["authorization"]["live_authorized"], False)
        capabilities = json.loads(
            (ROOT / "docs/capabilities.json").read_text(encoding="utf-8")
        )
        by_id = {
            item["id"]: item
            for item in capabilities["capabilities"]
        }
        self.assertEqual(
            by_id["ib-paper"]["status"], "QUALIFICATION_REQUIRED"
        )
        self.assertEqual(by_id["live"]["status"], "UNAVAILABLE")

    def test_source_projection_passes(self) -> None:
        self.assertEqual(source_gaps.validate_register_projection(ROOT), [])

    def test_release_source_closure_accepts_split_preflight_layout(self) -> None:
        self.assertEqual(source_gaps.validate_release(ROOT), [])

    def test_release_source_closure_rejects_missing_core(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.release_fixture(Path(directory))
            (root / "scripts/hepta_preflight_core.py").unlink()
            errors = source_gaps.validate_release(root)
        self.assertTrue(
            any(
                "scripts/hepta_preflight_core.py" in error
                and "unreadable" in error
                for error in errors
            ),
            errors,
        )

    def test_release_source_closure_rejects_replaced_core(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.release_fixture(Path(directory))
            wrapper = (root / "scripts/hepta_preflight.py").read_text(
                encoding="utf-8"
            )
            (root / "scripts/hepta_preflight_core.py").write_text(
                wrapper,
                encoding="utf-8",
            )
            errors = source_gaps.validate_release(root)
        self.assertTrue(
            any(
                "scripts/hepta_preflight_core.py" in error
                and "missing contract token" in error
                for error in errors
            ),
            errors,
        )

    def test_release_source_closure_rejects_detached_wrapper(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.release_fixture(Path(directory))
            wrapper_path = root / "scripts/hepta_preflight.py"
            wrapper = wrapper_path.read_text(encoding="utf-8")
            self.assertIn("hepta_preflight_core.py", wrapper)
            wrapper_path.write_text(
                wrapper.replace(
                    "hepta_preflight_core.py",
                    "hepta_preflight_core.disabled",
                ),
                encoding="utf-8",
            )
            errors = source_gaps.validate_release(root)
        self.assertTrue(
            any(
                "scripts/hepta_preflight.py" in error
                and "hepta_preflight_core.py" in error
                for error in errors
            ),
            errors,
        )

    def test_release_source_closure_rejects_weakened_core_safeguards(
        self,
    ) -> None:
        safeguards = (
            'POLICY_SCHEMA = "heptatrader.preflight-policy.v1"',
            'RECEIPT_SCHEMA = "heptatrader.preflight-receipt.v1"',
            'getattr(os, "O_NOFOLLOW", 0)',
            'getattr(os, "O_NONBLOCK", 0)',
            "HARD_MAXIMUM_ARCHIVE_MEMBERS",
            "artifact_admitted",
            "Broker probing requires successful artifact and policy admission",
            "RELEASE_LABEL_RE",
            "_validate_complete_archive_namespace",
            "_ORIGINAL_CHECK_MANIFEST_SHAPE",
            "_ORIGINAL_INSPECT_ARCHIVE",
            "_read_admitted_archive_root",
            "archive root identity does not match manifest ",
            "version/profile:",
            "private implementation module; ",
            "use hepta-preflight",
            "raise SystemExit(2)",
            "paper_authorized",
            "live_authorized",
        )
        for index, safeguard in enumerate(safeguards):
            with self.subTest(safeguard=safeguard):
                with tempfile.TemporaryDirectory() as directory:
                    root = self.release_fixture(Path(directory))
                    core_path = root / "scripts/hepta_preflight_core.py"
                    core = core_path.read_text(encoding="utf-8")
                    self.assertIn(safeguard, core)
                    core_path.write_text(
                        core.replace(
                            safeguard,
                            f"REMOVED_CORE_SAFEGUARD_{index}",
                        ),
                        encoding="utf-8",
                    )
                    errors = source_gaps.validate_release(root)
                self.assertTrue(
                    any(
                        "scripts/hepta_preflight_core.py" in error
                        and safeguard in error
                        for error in errors
                    ),
                    errors,
                )

    def test_release_source_closure_rejects_wrapper_semantic_override(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.release_fixture(Path(directory))
            wrapper_path = root / "scripts/hepta_preflight.py"
            wrapper = wrapper_path.read_text(encoding="utf-8")
            wrapper_path.write_text(
                wrapper + "\n_ORIGINAL_CHECK_MANIFEST_SHAPE = object()\n",
                encoding="utf-8",
            )
            errors = source_gaps.validate_release(root)
        self.assertTrue(
            any(
                "scripts/hepta_preflight.py" in error
                and "forbidden fail-open token" in error
                for error in errors
            ),
            errors,
        )


if __name__ == "__main__":
    unittest.main()
