"""Admission is one pinned-descriptor operation, not patch-on-patch reopens."""
from __future__ import annotations
import contextlib
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import test_preflight_complete_namespace as fixtures

preflight = fixtures.preflight


class PreflightSinglePassTests(unittest.TestCase):
    def test_hash_and_parse_use_one_admitted_descriptor(self):
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "release.tar.gz"
            digest, policy = fixtures.build_archive(artifact, {})
            opened = []
            original = preflight._open_pinned_regular

            @contextlib.contextmanager
            def observe(path, label):
                opened.append(Path(path))
                with original(path, label) as value:
                    yield value

            with mock.patch.object(preflight, "_open_pinned_regular", observe):
                manifest, actual, _ = preflight.inspect_archive(artifact, digest, policy, "core")
            self.assertEqual(actual, digest)
            self.assertEqual(manifest["version"], fixtures.VERSION)
            self.assertEqual(opened, [artifact])

    def test_root_mismatch_rejected_before_payload_consumption(self):
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "release.tar.gz"
            with mock.patch.object(fixtures, "ROOT_NAME", "heptatrader-wrong-core"):
                digest, policy = fixtures.build_archive(artifact, {"payload": b"must not be consumed"})
            consumed = []
            original = preflight._BoundedTarReader.consume

            def observe(reader, member, **kwargs):
                consumed.append(member.name)
                return original(reader, member, **kwargs)

            with mock.patch.object(preflight._BoundedTarReader, "consume", observe):
                with self.assertRaisesRegex(preflight.PreflightError, "root identity"):
                    preflight.inspect_archive(artifact, digest, policy, "core")
            self.assertEqual(consumed, ["heptatrader-wrong-core/manifest.json"])

    def test_path_substitution_during_parse_is_not_admitted_even_with_same_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "release.tar.gz"
            digest, policy = fixtures.build_archive(artifact, {})
            replacement = artifact.with_name("replacement.tar.gz")
            replacement.write_bytes(artifact.read_bytes())
            original = preflight._BoundedTarReader.consume
            changed = False

            def substitute(reader, member, **kwargs):
                nonlocal changed
                result = original(reader, member, **kwargs)
                if not changed:
                    replacement.replace(artifact)
                    changed = True
                return result

            with mock.patch.object(preflight._BoundedTarReader, "consume", substitute):
                with self.assertRaises(preflight.PreflightError):
                    preflight.inspect_archive(artifact, digest, policy, "core")
            self.assertEqual(hashlib.sha256(artifact.read_bytes()).hexdigest(), digest)


if __name__ == "__main__":
    unittest.main()
