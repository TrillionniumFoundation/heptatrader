"""Pinned module imports execute only the verified source buffer."""
from __future__ import annotations

import hashlib
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from hepta_verified_loader import load_verified_module


class VerifiedPythonLoaderTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="hepta-verified-loader-")
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name).resolve() / "fixture.py"
        self.module_name = "_hepta_verified_loader_fixture"
        previous = sys.modules.pop(self.module_name, None)
        def restore_module():
            sys.modules.pop(self.module_name, None)
            if previous is not None:
                sys.modules[self.module_name] = previous
        self.addCleanup(restore_module)

    def write_source(self, source):
        data = source.encode("utf-8")
        self.path.write_bytes(data)
        return hashlib.sha256(data).hexdigest()

    def test_executes_the_verified_buffer_even_if_path_changes_after_read(self):
        pin = self.write_source("VALUE = 'verified'\n")
        original_read = Path.read_bytes
        reads = []
        def read_then_replace(path):
            data = original_read(path)
            reads.append(path)
            if path == self.path:
                path.write_bytes(b"VALUE = 'replacement'\n")
            return data
        with mock.patch.object(Path, "read_bytes", read_then_replace):
            module = load_verified_module(self.module_name, self.path, pin)
        self.assertEqual(reads, [self.path])
        self.assertEqual(module.VALUE, "verified")
        self.assertEqual(self.path.read_text(), "VALUE = 'replacement'\n")

    def test_hash_mismatch_does_not_execute_or_replace_an_existing_module(self):
        self.write_source("raise AssertionError('must not execute')\n")
        existing = types.ModuleType(self.module_name)
        sys.modules[self.module_name] = existing
        with self.assertRaisesRegex(RuntimeError, "SHA-256 mismatch"):
            load_verified_module(self.module_name, self.path, "0" * 64)
        self.assertIs(sys.modules[self.module_name], existing)

    def test_execution_failure_restores_the_previous_registration(self):
        pin = self.write_source("raise RuntimeError('fixture failure')\n")
        for existing in (None, types.ModuleType(self.module_name)):
            with self.subTest(previous=existing is not None):
                if existing is None:
                    sys.modules.pop(self.module_name, None)
                else:
                    sys.modules[self.module_name] = existing
                with self.assertRaisesRegex(RuntimeError, "fixture failure"):
                    load_verified_module(self.module_name, self.path, pin)
                self.assertIs(sys.modules.get(self.module_name), existing)

    def test_module_metadata_and_dataclass_registration_are_preserved(self):
        pin = self.write_source("from dataclasses import dataclass\n@dataclass\nclass Item:\n    value: int\n")
        module = load_verified_module(self.module_name, self.path, pin)
        self.assertEqual(module.Item(7).value, 7)
        self.assertEqual(module.__file__, str(self.path))
        self.assertEqual(module.__spec__.origin, str(self.path))
        self.assertIs(sys.modules[self.module_name], module)


if __name__ == "__main__":
    unittest.main()
