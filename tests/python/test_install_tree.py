from __future__ import annotations

from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
import sys

if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import check_install_tree  # noqa: E402


class InstallTreeTests(unittest.TestCase):
    def _populate_prefix(self, prefix: Path) -> Path:
        version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
        for relative in check_install_tree.REQUIRED_RELATIVE_PATHS:
            path = prefix / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            if relative == "share/doc/HeptaTrader/VERSION":
                path.write_text(version + "\n", encoding="utf-8")
            elif relative == "bin/heptactl":
                path.write_text(
                    "#!/bin/sh\n"
                    "if [ \"${1:-}\" = \"--version\" ]; then\n"
                    f"  printf '%s\\n' '{version}'\n"
                    "  exit 0\n"
                    "fi\n"
                    "exit 64\n",
                    encoding="utf-8",
                )
                path.chmod(0o755)
            else:
                path.write_text("", encoding="utf-8")
        service = prefix / "lib/systemd/system/hepta-tool-gateway.service"
        service.parent.mkdir(parents=True, exist_ok=True)
        service.write_text(
            "[Unit]\nDocumentation=file:/usr/local/share/doc/HeptaTrader/runtime.md\n",
            encoding="utf-8",
        )
        return prefix

    def _minimal_tree(self, root: Path) -> Path:
        prefix = root / "usr/local"
        self._populate_prefix(prefix)
        return root

    def test_unresolved_cmake_service_placeholder_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._minimal_tree(root)
            service = root / "usr/local/lib/systemd/system/hepta-tool-gateway.service"
            service.write_text(
                "Documentation=file:@HEPTA_RUNTIME_DOC_DIR@/runtime.md\n",
                encoding="utf-8",
            )

            errors = check_install_tree.validate(root)
            self.assertTrue(
                any("unresolved CMake service placeholder" in error for error in errors),
                errors,
            )

    def test_invalid_utf8_in_example_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._minimal_tree(root)
            example = root / "usr/local/share/heptatrader/examples/bad.env.example"
            example.parent.mkdir(parents=True, exist_ok=True)
            example.write_bytes(b"HEPTA_TOOL_ACCOUNT=SIM\xff\n")

            errors = check_install_tree.validate(root)
            self.assertTrue(
                any("invalid UTF-8" in error for error in errors),
                errors,
            )

    def test_valid_usr_prefix_is_not_masked_by_empty_usr_local(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "usr/local").mkdir(parents=True)
            # Populate a distro-style /usr prefix while retaining an empty
            # /usr/local directory at the staging root.
            self._populate_prefix(root / "usr")

            errors = check_install_tree.validate(root)
            self.assertEqual(errors, [], errors)

    def test_complete_usr_prefix_is_not_masked_by_partial_usr_local(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            # A stale local prefix can contain a single old binary while the
            # distro prefix contains the complete current contract. Prefix
            # selection must score the full allowlist rather than preferring
            # /usr/local solely because it exists.
            stale = root / "usr/local"
            stale_path = stale / check_install_tree.REQUIRED_RELATIVE_PATHS[0]
            stale_path.parent.mkdir(parents=True, exist_ok=True)
            stale_path.write_text("stale", encoding="utf-8")
            self._populate_prefix(root / "usr")

            errors = check_install_tree.validate(root)
            self.assertEqual(errors, [], errors)


if __name__ == "__main__":
    unittest.main()
