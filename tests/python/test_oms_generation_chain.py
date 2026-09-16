from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import hepta_oms_checkpoint as checkpoint
import hepta_oms_lifecycle as lifecycle


class OmsGenerationChainTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="hepta-oms-chain-")
        self.addCleanup(self.temp.cleanup)
        self.store = Path(self.temp.name) / "store"
        self.store.mkdir(mode=0o700)
        os.chmod(self.store, 0o700)

    def manifest(self, generation: str, parent: str = "", *, schema: str | None = None) -> None:
        root = self.store / generation
        root.mkdir(mode=0o700)
        os.chmod(root, 0o700)
        path = root / "manifest.json"
        path.write_text(json.dumps({
            "schema": lifecycle.SCHEMA if schema is None else schema,
            "generation": generation,
            "parent_generation": parent,
        }, sort_keys=True) + "\n", encoding="utf-8")
        os.chmod(path, 0o600)

    def test_chain_has_no_artificial_1024_generation_limit(self) -> None:
        parent = ""
        expected: list[str] = []
        for index in range(1030):
            generation = f"g-{index:04d}"
            self.manifest(generation, parent)
            expected.append(generation)
            parent = generation
        chain = lifecycle._generation_chain(self.store, parent)
        self.assertEqual([generation for generation, _ in chain], expected)

    def test_chain_stops_at_newest_complete_v1_base(self) -> None:
        self.manifest("ancient-v2")
        self.manifest("full-v1", "ancient-v2", schema=checkpoint.SCHEMA)
        self.manifest("delta-1", "full-v1")
        self.manifest("delta-2", "delta-1")
        chain = lifecycle._generation_chain(self.store, "delta-2")
        self.assertEqual(
            [generation for generation, _ in chain],
            ["full-v1", "delta-1", "delta-2"],
        )

    def test_cycle_is_rejected_without_a_numeric_depth_guard(self) -> None:
        self.manifest("cycle-a", "cycle-b")
        self.manifest("cycle-b", "cycle-a")
        with self.assertRaisesRegex(
                checkpoint.GenerationError,
                "OMS_GENERATION_PARENT_CHAIN_INVALID"):
            lifecycle._generation_chain(self.store, "cycle-a")

    def test_unknown_parent_schema_is_rejected(self) -> None:
        self.manifest("unknown", schema="heptatrader.oms-generation.unknown")
        with self.assertRaisesRegex(
                checkpoint.GenerationError,
                "OMS_GENERATION_PARENT_SCHEMA_UNSUPPORTED"):
            lifecycle._generation_chain(self.store, "unknown")


if __name__ == "__main__":
    unittest.main()
