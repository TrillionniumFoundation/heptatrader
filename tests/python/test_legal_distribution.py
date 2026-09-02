from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


class LegalDistributionTests(unittest.TestCase):
    def test_original_work_has_apache_2_license(self) -> None:
        text = (ROOT / "LICENSE").read_text(encoding="utf-8")
        self.assertIn("Apache License", text)
        self.assertIn("Version 2.0, January 2004", text)
        self.assertIn("Copyright 2026 Trillionnium Foundation", text)
        self.assertIn("END OF TERMS AND CONDITIONS", text)

    def test_notice_preserves_vendor_and_capability_boundaries(self) -> None:
        text = (ROOT / "NOTICE").read_text(encoding="utf-8")
        self.assertIn("not relicensed", text)
        self.assertIn("external input", text)
        self.assertIn("does not constitute investment advice", text)
        self.assertIn("PAPER and LIVE permissions", text)
        self.assertIn("legacy/", text)

    def test_runtime_install_always_ships_version_license_and_notice(self) -> None:
        install = (ROOT / "cmake/RuntimeInstall.cmake").read_text(
            encoding="utf-8"
        )
        for name in ("VERSION", "LICENSE", "NOTICE"):
            self.assertIn(f'"${{CMAKE_SOURCE_DIR}}/{name}"', install)

        checker = (ROOT / "scripts/check_install_tree.py").read_text(
            encoding="utf-8"
        )
        for name in ("VERSION", "LICENSE", "NOTICE"):
            self.assertIn(f"share/doc/HeptaTrader/{name}", checker)

    def test_release_evidence_declares_the_same_license_and_no_vendor_sdk(self) -> None:
        generator = (ROOT / "scripts/generate_release_evidence.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('"licenseDeclared": "Apache-2.0"', generator)
        self.assertIn('"vendor_sdks_included": False', generator)
        self.assertIn('"live": "forbidden"', generator)


if __name__ == "__main__":
    unittest.main()
