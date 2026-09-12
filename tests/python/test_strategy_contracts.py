from __future__ import annotations

import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import hepta_strategy_contracts as contracts


class StrategyContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "evidence.json"

    def load(self, text: str, maximum: int = 4096):
        self.path.write_text(text, encoding="utf-8")
        return contracts.load_document(self.path, "TEST", maximum)

    def test_json_round_trip_and_explicit_zero(self) -> None:
        for number in (0.0, -0.0, 1.25, 1e-200, 1e200):
            with self.subTest(number=number):
                contracts.atomic_write_json(self.path, {"value": number})
                self.assertEqual(contracts.load_document(self.path, "TEST"), {"value": number})
                self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_nonfinite_constants_and_exponents_are_rejected_at_ingestion(self) -> None:
        for token in ("NaN", "Infinity", "-Infinity", "1e309", "-1e309", "1e-999", "-1e-999"):
            with self.subTest(token=token), self.assertRaises(contracts.ContractError):
                self.load('{"nested":{"number":' + token + '}}')

    def test_zero_exponent_is_not_underflow(self) -> None:
        self.assertEqual(self.load('{"zero":0e-999}'), {"zero": 0.0})

    def test_duplicate_keys_including_escaped_nested_names_are_rejected(self) -> None:
        for text in ('{"x":1,"x":2}', '{"nested":{"x":1,"\\u0078":2}}'):
            with self.subTest(text=text), self.assertRaises(contracts.ContractError):
                self.load(text)

    def test_numbers_reject_nan_infinity_huge_integer_and_non_numeric_inputs(self) -> None:
        for value in (float("nan"), float("inf"), -float("inf"), 10**400, True, "1", None):
            with self.subTest(value=repr(value)), self.assertRaisesRegex(contracts.ContractError, "NUMBER_INVALID"):
                contracts.require_number(value, "NUMBER_INVALID", positive=True, minimum=0.0, maximum=1.0)

    def test_number_boundaries_are_inclusive(self) -> None:
        self.assertEqual(contracts.require_number(0, "INVALID", minimum=0, maximum=1), 0.0)
        self.assertEqual(contracts.require_number(1, "INVALID", minimum=0, maximum=1), 1.0)
        for value in (-0.01, 1.01):
            with self.assertRaises(contracts.ContractError):
                contracts.require_number(value, "INVALID", minimum=0, maximum=1)
        with self.assertRaises(contracts.ContractError):
            contracts.require_number(0, "INVALID", positive=True)

    def test_oversized_input_is_rejected_before_any_read(self) -> None:
        self.path.write_bytes(b" " * 4097)
        with mock.patch.object(contracts.os, "read", side_effect=AssertionError("unbounded read")):
            with self.assertRaisesRegex(contracts.ContractError, "TOO_LARGE"):
                contracts.load_document(self.path, "TEST", 4096)

    def test_exact_size_limit_and_read_request_budget(self) -> None:
        raw = b'{"number":1}'
        self.path.write_bytes(raw)
        original_read = os.read
        requests = []
        def read(descriptor, count):
            requests.append(count)
            return original_read(descriptor, count)
        with mock.patch.object(contracts.os, "read", side_effect=read):
            self.assertEqual(contracts.load_document(self.path, "TEST", len(raw)), {"number": 1})
        self.assertTrue(requests)
        self.assertLessEqual(max(requests), len(raw) + 1)
        with self.assertRaises(contracts.ContractError):
            contracts.load_document(self.path, "TEST", len(raw) - 1)

    def test_symlink_and_hardlink_are_rejected(self) -> None:
        self.path.write_text('{"value":1}')
        link = self.path.with_name("alias.json")
        link.symlink_to(self.path)
        with self.assertRaises(contracts.ContractError):
            contracts.load_document(link, "TEST")
        link.unlink()
        os.link(self.path, link)
        with self.assertRaises(contracts.ContractError):
            contracts.load_document(self.path, "TEST")

    def test_fifo_is_rejected_without_blocking(self) -> None:
        os.mkfifo(self.path)
        code = (
            "from pathlib import Path; import hepta_strategy_contracts as c; "
            "c.load_document(Path(__import__('sys').argv[1]), 'FIFO')"
        )
        result = subprocess.run(
            [sys.executable, "-c", code, str(self.path)],
            env={**os.environ, "PYTHONPATH": str(ROOT / "scripts")},
            capture_output=True, text=True, timeout=3,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("FIFO_FILE_INVALID", result.stderr)

    def test_file_replacement_during_read_is_rejected(self) -> None:
        self.path.write_text('{"value":1}')
        original_read = os.read
        changed = False
        def replace_after_read(descriptor, count):
            nonlocal changed
            chunk = original_read(descriptor, count)
            if not changed:
                changed = True
                replacement = self.path.with_name("replacement.json")
                replacement.write_text('{"value":2}')
                os.replace(replacement, self.path)
            return chunk
        with mock.patch.object(contracts.os, "read", side_effect=replace_after_read):
            with self.assertRaisesRegex(contracts.ContractError, "FILE_CHANGED"):
                contracts.load_document(self.path, "TEST")

    def test_failed_publication_preserves_old_json_and_cleans_temporary(self) -> None:
        contracts.atomic_write_json(self.path, {"version": 1})
        with mock.patch.object(contracts.os, "replace", side_effect=OSError("disk failure")):
            with self.assertRaises(OSError):
                contracts.atomic_write_json(self.path, {"version": 2})
        self.assertEqual(contracts.load_document(self.path, "TEST"), {"version": 1})
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])


if __name__ == "__main__":
    unittest.main()
