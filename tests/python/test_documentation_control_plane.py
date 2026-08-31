from __future__ import annotations
import json
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]

class DocumentationControlPlaneTests(unittest.TestCase):
    def run_script(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, *args], cwd=ROOT, text=True,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              check=False)

    def test_generated_views_match_registries(self) -> None:
        result = self.run_script("scripts/generate_documentation_views.py", "--check")
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_documentation_control_plane_is_self_consistent(self) -> None:
        result = self.run_script("scripts/check_documentation_control_plane.py")
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_no_alias_or_historical_document_class(self) -> None:
        registry = json.loads((ROOT / "docs/document-registry-v2.json").read_text())
        self.assertNotIn("alias", {d["class"] for d in registry["documents"]})
        self.assertFalse((ROOT / "docs/legacy").exists())
        self.assertFalse((ROOT / "docs/proposals").exists())
        self.assertEqual(
            {p.name for p in (ROOT / "docs").iterdir() if p.is_file()},
            {"README.md", "document-registry-v2.json"},
        )

    def test_legacy_tree_contains_no_development_docs_or_media(self) -> None:
        forbidden = {".md", ".txt", ".pdf", ".png", ".jpg", ".jpeg", ".webp"}
        residual = [str(p.relative_to(ROOT)) for p in (ROOT / "legacy").rglob("*")
                    if p.is_file() and p.suffix.lower() in forbidden]
        self.assertEqual(residual, [])

if __name__ == "__main__":
    unittest.main()
