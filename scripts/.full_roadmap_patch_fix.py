#!/usr/bin/env python3
from pathlib import Path
import subprocess

root = Path(__file__).resolve().parents[1]
path = root / "scripts/hepta_oms_lifecycle.py"
value = path.read_text()
needle = "import json\n"
if value.count(needle) != 1:
    raise SystemExit("unexpected lifecycle json import")
path.write_text(value.replace(needle, "import json\nimport math\n", 1))

# execution_generation_support.cpp from PR #95 uses the compact HPM2 summary
# API. Bring the matching serializer/decoder pair as one reviewed unit rather
# than re-introducing the old macro-based implementation just to compile.
pr95 = "origin/remediation/full-priority-cleanup-20260916"
for relative in (
    "HeptaTrade/execution/paper_terminal_mutation_manifest.h",
    "HeptaTrade/execution/paper_terminal_mutation_manifest.cpp",
):
    content = subprocess.check_output(["git", "show", f"{pr95}:{relative}"], cwd=root, text=True)
    (root / relative).write_text(content)
