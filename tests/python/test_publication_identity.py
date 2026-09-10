from __future__ import annotations

import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import build_release_package as release  # noqa: E402
import hepta_preflight as preflight  # noqa: E402


class PublicationIdentityTests(unittest.TestCase):
    def _exercise_decoy_attack(
        self,
        module,
        writer,
        final_name: str,
        expected: bytes,
        *,
        symlink: bool,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            output = work / final_name
            attacker_target = work / "attacker-target"
            attacker_target.write_bytes(b"attacker-controlled\n")
            original_publish = module._publish_noreplace
            decoy_name = f".{final_name}.attacker"
            attacked = False

            def interposed_publish(
                directory_fd: int,
                descriptor: int,
                destination: str,
            ) -> None:
                nonlocal attacked
                self.assertEqual(destination, final_name)
                self.assertIsInstance(descriptor, int)
                self.assertEqual(os.fstat(descriptor).st_nlink, 0)
                self.assertFalse(
                    [
                        name
                        for name in os.listdir(directory_fd)
                        if name.startswith(f".{final_name}.")
                    ]
                )
                if symlink:
                    os.symlink(
                        attacker_target.name,
                        decoy_name,
                        dir_fd=directory_fd,
                    )
                else:
                    decoy = os.open(
                        decoy_name,
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                        0o600,
                        dir_fd=directory_fd,
                    )
                    try:
                        os.write(decoy, b"replacement-inode\n")
                        os.fsync(decoy)
                    finally:
                        os.close(decoy)
                attacked = True
                original_publish(
                    directory_fd, descriptor, destination
                )

            with mock.patch.object(
                module,
                "_publish_noreplace",
                side_effect=interposed_publish,
            ):
                writer(output)
            self.assertTrue(attacked)
            self.assertFalse(output.is_symlink())
            self.assertEqual(output.read_bytes(), expected)
            self.assertEqual(stat.S_IMODE(output.stat().st_mode),
                             0o600 if module is preflight else 0o644)
            decoy = work / decoy_name
            self.assertTrue(decoy.is_symlink() if symlink else decoy.is_file())

    def test_package_and_checksum_ignore_regular_and_symlink_source_decoys(
        self,
    ) -> None:
        for final_name in (
            "release.tar.gz",
            "release.tar.gz.sha256",
        ):
            for symlink in (False, True):
                with self.subTest(
                    final_name=final_name, symlink=symlink
                ):
                    expected = (final_name + "-expected\n").encode()
                    self._exercise_decoy_attack(
                        release,
                        lambda output, value=expected: release._write_atomic(
                            output, value, 0o644
                        ),
                        final_name,
                        expected,
                        symlink=symlink,
                    )

    def test_private_receipt_ignores_regular_and_symlink_source_decoys(
        self,
    ) -> None:
        value = {"result": "PASS", "authorization_effect": "NONE"}
        expected = preflight.canonical_json(value)
        for symlink in (False, True):
            with self.subTest(symlink=symlink):
                self._exercise_decoy_attack(
                    preflight,
                    lambda output: preflight._write_private_receipt(
                        output, value
                    ),
                    "receipt.json",
                    expected,
                    symlink=symlink,
                )

    def test_destination_collisions_preserve_competitor_bytes(self) -> None:
        sentinel = b"competitor\n"
        for final_name in (
            "release.tar.gz",
            "release.tar.gz.sha256",
        ):
            with self.subTest(final_name=final_name):
                with tempfile.TemporaryDirectory() as directory:
                    output = Path(directory) / final_name
                    output.write_bytes(sentinel)
                    with self.assertRaises(release.PackageError):
                        release._write_atomic(output, b"ours\n", 0o644)
                    self.assertEqual(output.read_bytes(), sentinel)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "receipt.json"
            output.write_bytes(sentinel)
            with self.assertRaises(preflight.PreflightError):
                preflight._write_private_receipt(
                    output, {"result": "PASS"}
                )
            self.assertEqual(output.read_bytes(), sentinel)

    def test_shared_output_directories_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            work.chmod(0o777)
            try:
                with self.assertRaisesRegex(
                    release.PackageError, "operator-custodied"
                ):
                    release._write_atomic(
                        work / "release.tar.gz", b"archive\n", 0o644
                    )
                with self.assertRaisesRegex(
                    preflight.PreflightError, "operator-custodied"
                ):
                    preflight._write_private_receipt(
                        work / "receipt.json", {"result": "PASS"}
                    )
            finally:
                work.chmod(0o700)


if __name__ == "__main__":
    unittest.main()
