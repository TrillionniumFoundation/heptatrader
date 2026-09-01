#!/usr/bin/env python3
"""Add the required documentation-generator write mode, then self-delete."""

from pathlib import Path

path = Path("scripts/m2_dedupe_patch.py")
text = path.read_text(encoding="utf-8")
old = '["python3", str(ROOT / "scripts/generate_documentation_views.py")],'
new = '["python3", str(ROOT / "scripts/generate_documentation_views.py"), "--write"],'
if text.count(old) != 1:
    raise SystemExit(f"expected one generator invocation, found {text.count(old)}")
path.write_text(text.replace(old, new), encoding="utf-8")
Path(__file__).unlink()
