from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
import sys

if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import generate_release_evidence as release_evidence  # noqa: E402


class ReleaseEvidenceTests(unittest.TestCase):
    def _tree(self, root: Path, suffix: str = "") -> Path:
        files = {
            "usr/local/bin/heptactl": "#!/bin/sh\necho hepta\n" + suffix,
            "usr/local/share/doc/HeptaTrader/LICENSE": "Apache-2.0\n",
            "usr/local/share/doc/HeptaTrader/NOTICE": "HeptaTrader\n",
            "usr/local/share/heptatrader/schemas/example.json": "{}\n",
        }
        for relative, content in files.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            path.chmod(0o755 if relative.endswith("heptactl") else 0o644)
        return root

    def _toolchain(self, path: Path) -> Path:
        path.write_text(
            json.dumps(
                {
                    "cmake": "cmake version 3.28.3",
                    "compiler": "g++ 13.3.0",
                    "ninja": "1.11.1",
                    "openssl": "OpenSSL 3.0.13",
                    "python": "Python 3.12.3",
                    "runner_image": "ubuntu-24.04/test",
                    "source_date_epoch": 1788314400,
                }
            ),
            encoding="utf-8",
        )
        return path

    def test_identical_trees_generate_deterministic_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            left = self._tree(root / "left")
            right = self._tree(root / "right")
            toolchain = self._toolchain(root / "toolchain.json")
            output_a = root / "evidence-a"
            output_b = root / "evidence-b"
            git_sha = "1" * 40

            first = release_evidence.generate(
                left, right, output_a, git_sha, toolchain
            )
            second = release_evidence.generate(
                left, right, output_b, git_sha, toolchain
            )
            self.assertEqual(first, second)
            self.assertEqual(
                sorted(path.name for path in output_a.iterdir()),
                [
                    "SHA256SUMS",
                    "evidence-index-v1.json",
                    "install-manifest-v1.json",
                    "provenance-v1.json",
                    "sbom.spdx.json",
                ],
            )
            for path in output_a.iterdir():
                self.assertEqual(
                    path.read_bytes(),
                    (output_b / path.name).read_bytes(),
                    path.name,
                )

            provenance = json.loads(
                (output_a / "provenance-v1.json").read_text(encoding="utf-8")
            )
            self.assertTrue(provenance["reproducible"])
            self.assertEqual(2, provenance["build_count"])
            self.assertEqual("forbidden", provenance["capability_ceiling"]["live"])
            self.assertFalse(
                provenance["capability_ceiling"]["vendor_sdks_included"]
            )

    def test_content_mismatch_fails_closed_without_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            left = self._tree(root / "left")
            right = self._tree(root / "right", suffix="# changed\n")
            output = root / "evidence"
            with self.assertRaisesRegex(
                release_evidence.EvidenceError, "not reproducible"
            ):
                release_evidence.generate(
                    left,
                    right,
                    output,
                    "2" * 40,
                    self._toolchain(root / "toolchain.json"),
                )
            self.assertFalse(output.exists())

    def test_mode_mismatch_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            left = self._tree(root / "left")
            right = self._tree(root / "right")
            (right / "usr/local/bin/heptactl").chmod(0o700)
            with self.assertRaisesRegex(
                release_evidence.EvidenceError, "not reproducible"
            ):
                release_evidence.generate(
                    left,
                    right,
                    root / "evidence",
                    "3" * 40,
                    self._toolchain(root / "toolchain.json"),
                )

    def test_symlink_and_world_writable_files_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            left = self._tree(root / "left")
            right = self._tree(root / "right")
            target = left / "usr/local/share/doc/HeptaTrader/NOTICE"
            target.unlink()
            target.symlink_to("LICENSE")
            with self.assertRaisesRegex(
                release_evidence.EvidenceError, "regular non-symlink"
            ):
                release_evidence.generate(
                    left,
                    right,
                    root / "evidence-symlink",
                    "4" * 40,
                    self._toolchain(root / "toolchain.json"),
                )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            left = self._tree(root / "left")
            right = self._tree(root / "right")
            unsafe = left / "usr/local/share/heptatrader/schemas/example.json"
            unsafe.chmod(0o666)
            with self.assertRaisesRegex(
                release_evidence.EvidenceError, "world-writable"
            ):
                release_evidence.generate(
                    left,
                    right,
                    root / "evidence-world",
                    "5" * 40,
                    self._toolchain(root / "toolchain.json"),
                )

    def test_hardlink_and_secret_like_toolchain_metadata_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            left = self._tree(root / "left")
            right = self._tree(root / "right")
            source = left / "usr/local/share/doc/HeptaTrader/LICENSE"
            os.link(source, left / "usr/local/share/doc/HeptaTrader/LICENSE.copy")
            with self.assertRaisesRegex(
                release_evidence.EvidenceError, "hard-linked"
            ):
                release_evidence.generate(
                    left,
                    right,
                    root / "evidence-hardlink",
                    "6" * 40,
                    self._toolchain(root / "toolchain.json"),
                )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            left = self._tree(root / "left")
            right = self._tree(root / "right")
            toolchain = self._toolchain(root / "toolchain.json")
            data = json.loads(toolchain.read_text(encoding="utf-8"))
            data["compiler"] = "token=not-allowed-in-evidence"
            toolchain.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaisesRegex(
                release_evidence.EvidenceError, "secret-like"
            ):
                release_evidence.generate(
                    left,
                    right,
                    root / "evidence-secret",
                    "7" * 40,
                    toolchain,
                )


if __name__ == "__main__":
    unittest.main()
