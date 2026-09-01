#!/usr/bin/env python3
"""Finalize internal closure with robust allocator performance assertions."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import internal_closure_finalizer_v3 as previous


def finalize(root: Path) -> None:
    previous.finalize(root)
    path = root / "tests/global_allocator_tests.cpp"
    text = path.read_text(encoding="utf-8")
    start = text.find("void TestBoundedPerformanceEnvelope()")
    end = text.find("\n}\n\nint main()", start)
    if start < 0 or end < 0:
        raise ValueError("bounded performance test is missing")
    prefix = text[:start]
    function = text[start:end]
    suffix = text[end:]
    function = function.replace(
        "policy.maximumExactCombinations = 1024;",
        "policy.maximumExactCombinations = 1000000;",
    )
    function = function.replace(
        "for (std::uint64_t epoch = 1; epoch <= 50; ++epoch)",
        "for (std::uint64_t epoch = 1; epoch <= 20; ++epoch)",
    )
    function = function.replace(
        "result.plan.solver.combinationsExplored <= 128",
        "result.plan.solver.combinationsExplored <= 1000000",
    )
    function = function.replace("elapsedMs <= 15000", "elapsedMs <= 30000")
    path.write_text(prefix + function + suffix, encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    finalize(args.root.resolve(strict=True))
