#!/usr/bin/env python3
from pathlib import Path
import json
import runpy

ROOT = Path(__file__).resolve().parents[1]
runpy.run_path(str(ROOT / "scripts/apply_priority_remediation_batch3_fix.py"), run_name="__main__")

path = ROOT / "docs/gap-register.json"
value = json.loads(path.read_text(encoding="utf-8"))
for item in value.get("gaps", []):
    if item.get("id") != "OMS-LIFECYCLE-002":
        continue
    replacements = {
        "HeptaTrade/execution/execution_generation_support.inc":
            "HeptaTrade/execution/execution_generation_support.cpp",
        "HeptaTrade/execution/execution_generation_capacity_support.inc":
            "HeptaTrade/execution/execution_generation_support.cpp",
        "HeptaTrade/execution/execution_generation_v2_support.inc":
            "HeptaTrade/execution/execution_generation_support.cpp",
    }
    evidence = []
    for entry in item.get("evidence", []):
        resolved = replacements.get(entry, entry)
        if resolved not in evidence:
            evidence.append(resolved)
    item["evidence"] = evidence
    break
else:
    raise RuntimeError("OMS-LIFECYCLE-002 missing")

path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
print("priority remediation batch 3 gap evidence synchronized")
