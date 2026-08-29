#!/usr/bin/env python3

import math
from pathlib import Path
import os
import sys
import tempfile
import unittest


REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY / "scripts"))

import heptatrader_secure_artifacts as secure  # noqa: E402


class SecureArtifactsTests(unittest.TestCase):
    def test_canonical_modes_are_deterministic(self) -> None:
        value = {"z": 1, "a": [True, None]}
        self.assertEqual(
            secure.canonical_json(value), b'{"a":[true,null],"z":1}')
        self.assertEqual(
            secure.canonical_json(value, pretty=True, trailing_newline=True),
            b'{\n  "a": [\n    true,\n    null\n  ],\n  "z": 1\n}\n')

    def test_nonfinite_json_is_rejected(self) -> None:
        with self.assertRaises(secure.SecureArtifactError):
            secure.canonical_json({"unsafe": math.nan})

    def test_stable_bytes_rejects_symlink_leaf(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target"
            target.write_bytes(b"bound")
            target.chmod(0o600)
            link = root / "link"
            link.symlink_to(target)
            with self.assertRaises(secure.SecureArtifactError):
                secure.stable_bytes(link, label="fixture", limit=1024)

    def test_stable_bytes_captures_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "artifact"
            path.write_bytes(b"bound")
            path.chmod(0o600)
            data, snapshot = secure.stable_bytes(
                path, label="fixture", limit=1024)
            self.assertEqual(data, b"bound")
            self.assertEqual(snapshot.size, 5)
            self.assertEqual(snapshot.identity[0], os.stat(path).st_dev)


if __name__ == "__main__":
    unittest.main(verbosity=2)
