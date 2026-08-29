#!/usr/bin/env python3
from pathlib import Path

path = Path("tests/unix_session_supervisor_server_tests.cpp")
text = path.read_text(encoding="utf-8")
needle = "#include <atomic>\n"
replacement = "#include <algorithm>\n#include <atomic>\n"
if replacement not in text:
    if needle not in text:
        raise SystemExit("expected include anchor not found")
    text = text.replace(needle, replacement, 1)
    path.write_text(text, encoding="utf-8")

Path("scripts/apply_source_header_fix.py").unlink()
Path(".github/workflows/apply-source-header-fix.yml").unlink()
