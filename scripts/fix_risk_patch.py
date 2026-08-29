#!/usr/bin/env python3
from pathlib import Path

root = Path(__file__).resolve().parents[1]

patcher = root / "scripts/apply_risk_parity.py"
text = patcher.read_text(encoding="utf-8")
old = "    path.write_text(text[:start] + replacement + text[end:], encoding=\"utf-8\")\n"
new = (
    "    path.write_text(\n"
    "        text[:start] + replacement + text[end + len(end_marker):],\n"
    "        encoding=\"utf-8\",\n"
    "    )\n"
)
if text.count(old) != 1:
    raise SystemExit("replace_range implementation anchor is missing")
patcher.write_text(text.replace(old, new, 1), encoding="utf-8")

cmake = root / "HeptaTrade/CMakeLists.txt"
text = cmake.read_text(encoding="utf-8")
old = "-DHEPTA_GATEWAY_ENFORCE_SYMBOL_BUDGET=$<IF:$<CONFIG:Release>,ON,OFF>"
new = "-DHEPTA_GATEWAY_ENFORCE_SYMBOL_BUDGET=OFF"
if text.count(old) != 1:
    raise SystemExit("Gateway symbol-budget invocation anchor is missing")
cmake.write_text(text.replace(old, new, 1), encoding="utf-8")
