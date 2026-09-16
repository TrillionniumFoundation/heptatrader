#!/usr/bin/env python3
from pathlib import Path
root = Path(__file__).resolve().parents[1]
path = root / "scripts/hepta_oms_lifecycle.py"
value = path.read_text()
needle = "import json\n"
if value.count(needle) != 1:
    raise SystemExit("unexpected lifecycle json import")
path.write_text(value.replace(needle, "import json\nimport math\n", 1))
