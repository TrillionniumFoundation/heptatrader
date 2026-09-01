#!/usr/bin/env python3
from pathlib import Path

path = Path(__file__).resolve().parents[1] / "tests/strategy_proposal_tests.cpp"
text = path.read_text(encoding="utf-8")
old = "proposals, expected, 1500)"
count = text.count(old)
if count != 4:
    raise SystemExit(f"expected four ProposalSetBuilder fixtures, found {count}")
path.write_text(text.replace(old, "proposals, expected, 1500, 1800)"), encoding="utf-8")
