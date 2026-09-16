#!/usr/bin/env python3
from pathlib import Path
import runpy

ROOT = Path(__file__).resolve().parents[1]
runpy.run_path(str(ROOT / "scripts/apply_priority_remediation_batch3.py"), run_name="__main__")
path = ROOT / "HeptaTrade/execution/execution_generation_support.cpp"
value = path.read_text(encoding="utf-8")
old = "    if (!Prepare(reason)) return false;\n"
new = "    if (!PrepareGenerationV1(reason)) return false;\n"
if value.count(old) != 1:
    raise RuntimeError(f"expected one V1 Prepare(reason) call, found {value.count(old)}")
path.write_text(value.replace(old, new, 1), encoding="utf-8")
print("priority remediation batch 3 dispatch fix applied")
