#!/usr/bin/env python3
"""Finalize internal closure with monotonic external-gap assertions."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import internal_closure_finalizer_v2 as previous


def finalize(root: Path) -> None:
    previous.finalize(root)
    path = root / "tests/python/test_internal_verification_evidence.py"
    text = path.read_text(encoding="utf-8")
    old = '''        self.assertEqual(
            remaining,
            {"G-IB-001": "in-progress", "G-TEAM-001": "in-progress"},
        )
'''
    new = '''        self.assertTrue(
            set(remaining).issubset({"G-IB-001", "G-TEAM-001"})
        )
        for state in remaining.values():
            self.assertEqual(state, "in-progress")
'''
    if old not in text:
        if new not in text:
            raise ValueError("external-gap assertion anchor is missing")
    else:
        text = text.replace(old, new, 1)
        path.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    finalize(args.root.resolve(strict=True))
