#!/usr/bin/env python3
"""Correct the materialized verifier so simulator invariants are checked in the simulator source."""
from pathlib import Path

path = Path(__file__).resolve().parents[1] / "scripts/verify_source_gap_closures.py"
text = path.read_text(encoding="utf-8")
old = '''            "evidence.quantity * evidence.contract.multiplier * price * fx.rate",\n            "flattenCapacityReserved",\n            "SIM_FLATTEN_RESERVATION_INCONSISTENT",\n            "afterPosition >= 0.0",\n'''
new = '''            "evidence.quantity * evidence.contract.multiplier * price * fx.rate",\n            "afterPosition >= 0.0",\n'''
if text.count(old) != 1:
    raise RuntimeError("unexpected generic-risk verifier anchor")
text = text.replace(old, new, 1)
anchor = '''    tests = require_tokens(\n        root,\n        "tests/pre_trade_risk_engine_tests.cpp",\n'''
insertion = '''    require_tokens(\n        root,\n        "HeptaTrade/simulator/deterministic_execution_venue.cpp",\n        (\n            "flattenCapacityReserved",\n            "SIM_FLATTEN_RESERVATION_INCONSISTENT",\n            "preservesZeroBoundary",\n            'order.terminalStatus = "Rejected"',\n        ),\n        "RISK-001",\n        errors,\n    )\n    tests = require_tokens(\n        root,\n        "tests/pre_trade_risk_engine_tests.cpp",\n'''
if text.count(anchor) != 1:
    raise RuntimeError("unexpected simulator verifier insertion anchor")
path.write_text(text.replace(anchor, insertion, 1), encoding="utf-8")
print("flatten verifier source ownership corrected")
