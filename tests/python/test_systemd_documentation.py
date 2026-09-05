from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]

if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import check_systemd_documentation  # noqa: E402


class SystemdDocumentationTests(unittest.TestCase):
    def _fixture_errors(
        self,
        documentation_value: str,
        registered_paths: tuple[str, ...] = (
            "README.md",
            "operations/DEPLOYMENT.md",
            "operations/IB-PAPER-QUALIFICATION.md",
        ),
    ) -> list[str]:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            docs = root / "docs"
            systemd = root / "systemd"
            docs.mkdir(parents=True)
            systemd.mkdir(parents=True)

            documents = []
            for relative in registered_paths:
                target = docs / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("fixture\n", encoding="utf-8")
                documents.append({"path": relative})
            (docs / "document-registry-v2.json").write_text(
                json.dumps({"documents": documents}),
                encoding="utf-8",
            )
            (systemd / "fixture.service").write_text(
                "[Unit]\n"
                "Description=fixture\n"
                f"Documentation={documentation_value}\n",
                encoding="utf-8",
            )
            return check_systemd_documentation.validate(root)

    def test_repository_units_reference_registered_canonical_docs(self) -> None:
        self.assertEqual(check_systemd_documentation.validate(ROOT), [])

    def test_template_and_packaged_document_roots_are_accepted(self) -> None:
        template_errors = self._fixture_errors(
            "file:@HEPTA_RUNTIME_DOC_DIR@/operations/DEPLOYMENT.md"
        )
        self.assertEqual(template_errors, [], template_errors)

        packaged_errors = self._fixture_errors(
            "file:/usr/share/doc/heptatrader/operations/IB-PAPER-QUALIFICATION.md"
        )
        self.assertEqual(packaged_errors, [], packaged_errors)

    def test_stale_or_unregistered_document_is_rejected(self) -> None:
        errors = self._fixture_errors(
            "file:/usr/share/doc/heptatrader/AGENT-NATIVE-TRADING-OS-ARCHITECTURE.md"
        )
        self.assertTrue(
            any("not a registered canonical document" in error for error in errors),
            errors,
        )

    def test_local_document_outside_package_doc_roots_is_rejected(self) -> None:
        errors = self._fixture_errors("file:/tmp/README.md")
        self.assertTrue(
            any("outside canonical install roots" in error for error in errors),
            errors,
        )

    def test_non_file_documentation_reference_is_allowed(self) -> None:
        errors = self._fixture_errors("https://example.invalid/heptatrader")
        self.assertEqual(errors, [], errors)


if __name__ == "__main__":
    unittest.main()
