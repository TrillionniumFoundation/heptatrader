#!/usr/bin/env python3
"""Validate active module ownership and non-growth budgets."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def _load(path: Path, errors: list[str]) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as error:
        errors.append(f"{path.relative_to(ROOT)} invalid: {error}")
        return None
    if not isinstance(value, dict):
        errors.append(f"{path.relative_to(ROOT)} must contain an object")
        return None
    return value


def validate() -> list[str]:
    errors: list[str] = []
    document = _load(ROOT / "docs/development/module-ownership-v1.json", errors)
    if document is None:
        return errors
    if document.get("schema") != "heptatrader.module-ownership.v1":
        errors.append("ownership schema mismatch")
    modules = document.get("modules")
    if not isinstance(modules, dict):
        errors.append("module ownership modules must be an object")
        return errors
    prefixes = [
        entry.get("prefix")
        for entry in modules.values()
        if isinstance(entry, dict) and isinstance(entry.get("prefix"), str)
    ]
    if not prefixes:
        errors.append("module prefixes invalid")
        return errors
    exceptions = document.get("large_file_exceptions")
    if not isinstance(exceptions, dict):
        exceptions = {}
    try:
        cpp_limit = int(document.get("new_cpp_line_limit", 0))
        python_limit = int(document.get("new_python_line_limit", 0))
    except (TypeError, ValueError):
        errors.append("module line limits invalid")
        return errors
    if cpp_limit <= 0 or python_limit <= 0:
        errors.append("module line limits must be positive")

    for path in (ROOT / "HeptaTrade").rglob("*.cpp"):
        relative = path.relative_to(ROOT).as_posix()
        if not any(relative.startswith(prefix) for prefix in prefixes):
            errors.append(f"unowned active C++ source: {relative}")
        line_count = len(path.read_text(encoding="utf-8-sig").splitlines())
        exception = exceptions.get(relative)
        if line_count > cpp_limit and exception is None:
            errors.append(f"large C++ source lacks extraction owner: {relative}")
        if isinstance(exception, dict) and line_count > int(
            exception.get("baseline_lines", 0)
        ):
            errors.append(f"large C++ source grew beyond baseline: {relative}")

    for path in (ROOT / "scripts").glob("*.py"):
        relative = path.relative_to(ROOT).as_posix()
        line_count = len(path.read_text(encoding="utf-8-sig").splitlines())
        exception = exceptions.get(relative)
        if line_count > python_limit and exception is None:
            errors.append(f"large Python script lacks extraction owner: {relative}")
        if isinstance(exception, dict) and line_count > int(
            exception.get("baseline_lines", 0)
        ):
            errors.append(f"large Python script grew beyond baseline: {relative}")

    # Research utilities are active Python libraries even when they are also
    # executable entry points.  Keep the scan narrow (the package is small and
    # intentionally has no generated/vendor payloads), while applying the same
    # no-growth rule used for oversized scripts.  The current protocol runner
    # has a frozen extraction owner until its evaluator/CLI split is completed.
    research_root = ROOT / "research"
    if research_root.is_dir():
        for path in research_root.rglob("*.py"):
            relative = path.relative_to(ROOT).as_posix()
            line_count = len(path.read_text(encoding="utf-8-sig").splitlines())
            exception = exceptions.get(relative)
            if line_count > python_limit and exception is None:
                errors.append(f"large Python research module lacks extraction owner: {relative}")
            if isinstance(exception, dict) and line_count > int(
                exception.get("baseline_lines", 0)
            ):
                errors.append(f"large Python research module grew beyond baseline: {relative}")

    for path in (ROOT / "HeptaTrade/cli").glob("*.cpp"):
        text = path.read_text(encoding="utf-8-sig")
        if "int main(" in text and len(text.splitlines()) > 500:
            errors.append(f"CLI entry point is not thin: {path.relative_to(ROOT)}")
    return errors


def main() -> int:
    errors = validate()
    for error in errors:
        print(f"[MODULE-DISCIPLINE] {error}", file=sys.stderr)
    if errors:
        return 1
    print("[MODULE-DISCIPLINE] PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
