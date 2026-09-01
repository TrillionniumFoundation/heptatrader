#!/usr/bin/env python3
"""Repair the one-shot M2 patcher's CMake command parser, then self-delete."""

from pathlib import Path
import re

path = Path("scripts/m2_dedupe_patch.py")
text = path.read_text(encoding="utf-8")
pattern = re.compile(
    r"def rewrite_add_sources\(\n.*?\n\ndef add_link_libraries",
    re.DOTALL,
)
replacement = '''def rewrite_add_sources(
    text: str,
    target: str,
    *,
    remove_exact: set[str] | None = None,
    remove_prefixes: tuple[str, ...] = (),
    append: tuple[str, ...] = (),
) -> str:
    candidates = (f"add_library({target}", f"add_executable({target}")
    starts = [text.find(candidate) for candidate in candidates]
    starts = [value for value in starts if value >= 0]
    if not starts:
        raise SystemExit(f"target source block not found: {target}")
    start = min(starts)
    opening = text.find("(", start)
    depth = 0
    end = -1
    for index in range(opening, len(text)):
        if text[index] == "(":
            depth += 1
        elif text[index] == ")":
            depth -= 1
            if depth == 0:
                end = index
                break
    if end < 0:
        raise SystemExit(f"unbalanced target source block: {target}")
    command = text[start : end + 1]
    first_newline = command.find("\\n")
    if first_newline < 0:
        raise SystemExit(f"single-line target source block unsupported: {target}")
    header = command[: first_newline + 1]
    lines = command[first_newline + 1 : -1].splitlines()
    remove_exact = remove_exact or set()
    kept: list[str] = []
    for line in lines:
        token = line.strip()
        if token in remove_exact or any(token.startswith(prefix) for prefix in remove_prefixes):
            continue
        kept.append(line)
    existing = {line.strip() for line in kept}
    for source in append:
        if source not in existing:
            kept.append(f"    {source}")
    replacement_command = header + "\\n".join(kept) + ")"
    return text[:start] + replacement_command + text[end + 1 :]


def add_link_libraries'''
text, count = pattern.subn(replacement, text)
if count != 1:
    raise SystemExit(f"expected one parser function, found {count}")
path.write_text(text, encoding="utf-8")
Path(__file__).unlink()
