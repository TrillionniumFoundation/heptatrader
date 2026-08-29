#!/usr/bin/env python3

from pathlib import Path
import json
import os
import shutil
import sys
import tempfile
import unittest


REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY / "scripts"))
import verify_heptatrader_prebuilt_assets as verifier  # noqa: E402


class PrebuiltAssetBoundaryTests(unittest.TestCase):
    def fixture(self, root: Path, include_payloads: bool = True) -> None:
        for relative in (
                verifier.PREBUILT_MANIFEST,
                verifier.LEGACY_CTP_MANIFEST,
                verifier.CURRENT_CTP_MANIFEST):
            destination = root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(REPOSITORY / relative, destination)
        if not include_payloads:
            return
        manifests = (
            json.loads((root / verifier.PREBUILT_MANIFEST).read_text(
                encoding="utf-8")),
            json.loads((root / verifier.LEGACY_CTP_MANIFEST).read_text(
                encoding="utf-8")),
        )
        for payload in manifests:
            for record in payload["assets"]:
                source = REPOSITORY / record["path"]
                destination = root / record["path"]
                destination.parent.mkdir(parents=True, exist_ok=True)
                if source.is_file():
                    # Keep the negative fixture inode-independent from the
                    # reviewed repository payload.  A hard link changes the
                    # source inode ctime while parallel test processes create
                    # their fixtures, which correctly trips the production
                    # verifier's descriptor-stability check.
                    shutil.copy2(source, destination)
                else:
                    # The strict source distribution intentionally carries
                    # metadata only.  These negative fixtures need path
                    # presence, not reviewed vendor bytes: auto/absent mode
                    # must reject partial or any payload before content
                    # verification is reached.
                    destination.write_bytes(
                        b"hepta-test-unreviewed-prebuilt-placeholder\n")
        for relative in (
                "Interface/include/tinyxml.h",
                "Interface/include/heptaVersion.h"):
            destination = root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(REPOSITORY / relative, destination)

    def test_repository_overlay_and_metadata_are_exact(self) -> None:
        bundled = (REPOSITORY / ".hepta/source-bundle-manifest.json").is_file()
        expected_mode = "absent" if bundled else "present"
        result = verifier.verify(REPOSITORY, "auto")
        self.assertEqual(result["payload_mode"], expected_mode)
        self.assertEqual(result["prebuilt_asset_count"], 8)
        self.assertEqual(result["legacy_ctp_asset_count"], 4)
        self.assertFalse(result["distribution_authorized"])
        self.assertFalse(result["agent_os_core_requires_prebuilt"])
        self.assertTrue(result["ctp_versions_separate"])

    def test_metadata_only_source_distribution_passes(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-prebuilt-metadata-") as temporary:
            root = Path(temporary)
            self.fixture(root, include_payloads=False)
            result = verifier.verify(root, "absent")
            self.assertEqual(result["payload_mode"], "absent")

    def test_partial_overlay_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-prebuilt-partial-") as temporary:
            root = Path(temporary)
            self.fixture(root)
            first = next(iter(verifier.EXPECTED_PREBUILT_IDENTITIES))
            (root / first).unlink()
            with self.assertRaisesRegex(
                    verifier.PrebuiltVerificationError, "partially"):
                verifier.verify(root, "auto")

    def test_distribution_authorization_drift_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-prebuilt-authority-") as temporary:
            root = Path(temporary)
            self.fixture(root, include_payloads=False)
            manifest = root / verifier.PREBUILT_MANIFEST
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            payload["payload_distribution_authorized"] = True
            manifest.write_text(
                json.dumps(payload, indent=2, sort_keys=True) + "\n",
                encoding="utf-8")
            with self.assertRaisesRegex(
                    verifier.PrebuiltVerificationError, "policy drift"):
                verifier.verify(root, "absent")

    def test_duplicate_manifest_key_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-prebuilt-duplicate-") as temporary:
            root = Path(temporary)
            self.fixture(root, include_payloads=False)
            manifest = root / verifier.PREBUILT_MANIFEST
            manifest.write_bytes(
                manifest.read_bytes().replace(
                    b"{", b'{"schema":"forged",', 1))
            with self.assertRaisesRegex(
                    verifier.PrebuiltVerificationError, "strict UTF-8 JSON"):
                verifier.verify(root, "absent")

    def test_ctp_651_and_677_cannot_be_merged(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-prebuilt-ctp-mix-") as temporary:
            root = Path(temporary)
            self.fixture(root, include_payloads=False)
            current = root / verifier.CURRENT_CTP_MANIFEST
            payload = json.loads(current.read_text(encoding="utf-8"))
            payload["version"] = "6.5.1"
            current.write_text(
                json.dumps(payload, indent=2, sort_keys=True) + "\n",
                encoding="utf-8")
            with self.assertRaisesRegex(
                    verifier.PrebuiltVerificationError,
                    "current CTP manifest identity"):
                verifier.verify(root, "absent")

    def test_symlink_payload_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-prebuilt-symlink-") as temporary:
            root = Path(temporary)
            self.fixture(root)
            first = next(iter(verifier.EXPECTED_PREBUILT_IDENTITIES))
            path = root / first
            path.unlink()
            path.symlink_to(REPOSITORY / first)
            with self.assertRaisesRegex(
                    verifier.PrebuiltVerificationError, "symlink"):
                verifier.verify(root, "present")

    def test_payload_is_forbidden_in_metadata_only_mode(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-prebuilt-source-only-") as temporary:
            root = Path(temporary)
            self.fixture(root)
            with self.assertRaisesRegex(
                    verifier.PrebuiltVerificationError, "metadata-only"):
                verifier.verify(root, "absent")


if __name__ == "__main__":
    unittest.main(verbosity=2)
