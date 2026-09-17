from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests" / "python"))

import hepta_oms_checkpoint as checkpoint
import hepta_oms_lifecycle as lifecycle
from test_oms_lifecycle_rotation import command_events, encode


class StreamingOnlyIterator:
    """Iterator that fails if a caller tries to pre-size list(self)."""

    def __init__(self, source):
        self._source = iter(source)

    def __iter__(self):
        return self

    def __next__(self):
        return next(self._source)

    def __length_hint__(self):
        raise AssertionError("cumulative index verification must stay streaming")


class OmsLifecycleStreamingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="hepta-oms-streaming-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        os.chmod(self.root, 0o700)
        self.journal = self.root / "oms.jsonl"
        self.store = Path(str(self.journal) + ".generations")
        self.journal.write_bytes(encode(command_events("old", 1000, 101)))
        os.chmod(self.journal, 0o600)

    def test_generation_verification_does_not_materialize_cumulative_indexes(self) -> None:
        lifecycle.seal_generation(self.journal, self.store, stopped=True)
        original = lifecycle._iter_private_lines

        def streaming(path: Path):
            return StreamingOnlyIterator(original(path))

        with mock.patch.object(lifecycle, "_iter_private_lines", side_effect=streaming):
            verified = lifecycle.verify_generation(self.store, journal=self.journal)
        self.assertEqual(verified["result"], "PASS")

    def test_generation_chain_limit_is_explicit_and_fail_closed(self) -> None:
        def manifest_for(_store: Path, generation: str):
            index = int(generation[1:])
            return {
                "generation": generation,
                "parent_generation": f"g{index - 1}" if index else "",
            }

        with mock.patch.object(lifecycle, "_manifest_for", side_effect=manifest_for):
            with self.assertRaisesRegex(
                    checkpoint.GenerationError,
                    "OMS_GENERATION_PARENT_CHAIN_INVALID"):
                lifecycle._generation_chain(self.store, f"g{lifecycle.MAX_CHAIN}")


if __name__ == "__main__":
    unittest.main()
