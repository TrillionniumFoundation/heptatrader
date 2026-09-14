from __future__ import annotations

import contextlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import source_json as reader
import check_component_coverage as coverage
import check_documentation as documentation
import check_gap_register as gaps
import verify_build_ownership as ownership


class SourceJsonTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.path = self.root / "source.json"

    def load(self, raw: bytes, **kwargs):
        self.path.write_bytes(raw)
        return reader.load_source_json(self.path, **kwargs)

    def test_valid_utf8_and_schema_neutral_values(self) -> None:
        self.assertEqual(self.load('{"名称":"执行", "limit":5, "active":false}'.encode()),
                         {"名称": "执行", "limit": 5, "active": False})
        self.assertEqual(self.load(b'[1, 2.5, null, true]'), [1, 2.5, None, True])

    def test_duplicate_keys_including_escaped_and_nested_keys_fail(self) -> None:
        for raw in (b'{"a":1,"a":2}', b'{"a":1,"\\u0061":2}',
                    b'{"outer":{"a":1,"a":2}}'):
            with self.subTest(raw=raw), self.assertRaisesRegex(reader.SourceJsonError, "duplicate"):
                self.load(raw)

    def test_nonfinite_constants_and_exponent_overflow_fail(self) -> None:
        for value in (b'NaN', b'Infinity', b'-Infinity', b'1e309', b'-1E9999'):
            with self.subTest(value=value), self.assertRaisesRegex(reader.SourceJsonError, "non-finite"):
                self.load(b'{"v":' + value + b'}')

    def test_nonzero_underflow_fails_but_true_zero_is_preserved(self) -> None:
        for value in (b'1e-9999', b'-0.01E-9999'):
            with self.subTest(value=value), self.assertRaisesRegex(reader.SourceJsonError, "underflows"):
                self.load(b'{"v":' + value + b'}')
        for value in (b'0e-9999', b'-0.00e9999', b'0E-' + b'9' * 5000):
            with self.subTest(value=value[:30]):
                self.assertEqual(self.load(b'{"v":' + value + b'}')["v"], 0.0)

    def test_malformed_utf8_and_json_have_one_error_type(self) -> None:
        for raw in (b'\xff', b'{', b'{"x":1,}'):
            with self.subTest(raw=raw[:20]), self.assertRaises(reader.SourceJsonError):
                self.load(raw)

    def test_parser_recursion_exhaustion_has_one_error_type(self) -> None:
        # The parser's nesting budget differs across Python versions. Test
        # exception normalization without asserting an undocumented depth cap.
        self.path.write_text('{"valid":true}')
        with mock.patch.object(reader.json, "loads", side_effect=RecursionError("parser budget")):
            with self.assertRaises(reader.SourceJsonError) as raised:
                reader.load_source_json(self.path)
        self.assertIsInstance(raised.exception.__cause__, RecursionError)

    def test_exact_byte_bound_and_one_over(self) -> None:
        self.assertEqual(self.load(b'{"a":1}', max_bytes=7), {"a": 1})
        with self.assertRaisesRegex(reader.SourceJsonError, "byte bound"):
            self.load(b'{"a":1} ', max_bytes=7)

    def test_invalid_byte_bound_is_rejected_before_open(self) -> None:
        for limit in (0, -1, True, 1.5, "8"):
            with self.subTest(limit=limit), mock.patch.object(reader.os, "open") as opened:
                with self.assertRaises(reader.SourceJsonError):
                    reader.load_source_json(self.path, max_bytes=limit)
                opened.assert_not_called()

    def test_missing_directory_and_links_are_rejected(self) -> None:
        with self.assertRaises(reader.SourceJsonError):
            reader.load_source_json(self.path)
        with self.assertRaises(reader.SourceJsonError):
            reader.load_source_json(self.root)
        self.path.write_text('{}')
        link = self.root / "link.json"
        link.symlink_to(self.path)
        with self.assertRaises(reader.SourceJsonError):
            reader.load_source_json(link)
        link.unlink()
        os.link(self.path, link)
        with self.assertRaises(reader.SourceJsonError):
            reader.load_source_json(self.path)

    def test_fifo_is_rejected_without_blocking(self) -> None:
        os.mkfifo(self.path)
        command = [sys.executable, "-c",
                   "from source_json import load_source_json; import sys; load_source_json(sys.argv[1])",
                   str(self.path)]
        result = subprocess.run(command, cwd=ROOT / "scripts", capture_output=True,
                                text=True, timeout=5)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("regular single-link", result.stderr)

    def test_changed_file_cannot_supply_valid_but_substituted_json(self) -> None:
        self.path.write_text('{"old":1}')
        other = self.root / "other.json"
        other.write_text('{"new":2}')
        fdopen = os.fdopen

        @contextlib.contextmanager
        def exchanged(fd, *args, **kwargs):
            with fdopen(fd, *args, **kwargs) as stream:
                yield stream
                os.replace(other, self.path)

        with mock.patch.object(reader.os, "fdopen", side_effect=exchanged):
            with self.assertRaisesRegex(reader.SourceJsonError, "changed while reading"):
                reader.load_source_json(self.path)

    def test_shared_reader_does_not_write_input(self) -> None:
        raw = b'{"retained": [1, 2, 3]}\n'
        self.path.write_bytes(raw)
        before = self.path.stat()
        reader.load_source_json(self.path)
        after = self.path.stat()
        self.assertEqual(self.path.read_bytes(), raw)
        self.assertEqual((before.st_ino, before.st_mtime_ns, before.st_mode),
                         (after.st_ino, after.st_mtime_ns, after.st_mode))

    def test_consumers_preserve_their_diagnostic_contracts(self) -> None:
        self.path.write_bytes(b'{"a":1,"a":2}')
        errors = []
        self.assertIsNone(documentation._load_json(self.path, errors))
        self.assertEqual(len(errors), 1)
        self.assertIn("invalid JSON", errors[0])
        for function, error in ((coverage._load_json, coverage.CoverageError),
                                (gaps.load_json, gaps.GapRegisterError),
                                (ownership.load_json, ownership.OwnershipError)):
            with self.subTest(function=function.__module__):
                with self.assertRaisesRegex(error, "duplicate JSON key"):
                    function(self.path)


if __name__ == "__main__":
    unittest.main()
