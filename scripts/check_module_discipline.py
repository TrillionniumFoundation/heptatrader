#!/usr/bin/env python3
"""Validate ModuleManifest V2, active source coverage and no-growth budgets."""
from __future__ import annotations
import json
from pathlib import Path
import re
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "docs/modules/module-registry-v2.json"
BUDGET = ROOT / "docs/modules/source-size-budget-v1.json"


def load(path: Path, errors: list[str]) -> dict[str, Any] | None:
    try: value=json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError,UnicodeError,json.JSONDecodeError) as exc:
        errors.append(f"{path.relative_to(ROOT)} invalid: {exc}"); return None
    if not isinstance(value,dict): errors.append(f"{path.relative_to(ROOT)} must be object"); return None
    return value


def matches(relative: str, prefix: str) -> bool:
    return relative == prefix.rstrip("/") or relative.startswith(prefix)


def validate() -> list[str]:
    errors=[]
    registry=load(REGISTRY,errors); budget=load(BUDGET,errors)
    if registry is None or budget is None: return errors
    if registry.get("schema")!="heptatrader.module-registry.v2": errors.append("module registry schema mismatch")
    if budget.get("schema")!="heptatrader.source-size-budget.v1": errors.append("source budget schema mismatch")
    manifest_paths=registry.get("manifest_paths")
    if not isinstance(manifest_paths,list): return errors+["module registry manifest_paths must be array"]
    modules=[]
    for relative in manifest_paths:
        if not isinstance(relative,str): errors.append("invalid module manifest path"); continue
        manifest=load(ROOT / "docs" / relative, errors)
        if manifest is not None: modules.append(manifest)
    by_id={}; target_owners={}
    for item in modules:
        if not isinstance(item,dict) or not isinstance(item.get("id"),str): errors.append("invalid module entry"); continue
        mid=item["id"]
        if mid in by_id: errors.append(f"duplicate module: {mid}")
        by_id[mid]=item
        if item.get("ownership_mode")=="shared-migration" and not item.get("migration_gap"):
            errors.append(f"module {mid}: shared-migration lacks gap")
        for target in item.get("build_targets",[]): target_owners.setdefault(target,[]).append(item)
    cmake="\n".join(p.read_text(encoding="utf-8-sig") for p in (ROOT/"CMakeLists.txt",ROOT/"HeptaTrade/CMakeLists.txt") if p.is_file())
    actual_targets=set(re.findall(r"add_(?:library|executable)\s*\(\s*([A-Za-z0-9_.+-]+)",cmake))
    for target,owners in target_owners.items():
        current=[o for o in owners if o.get("lifecycle") in {"current","experimental"}]
        if current and target not in actual_targets: errors.append(f"declared current target missing: {target}")
        if len(owners)>1:
            gaps={o.get("migration_gap") for o in owners}
            if any(o.get("ownership_mode")!="shared-migration" for o in owners) or len(gaps)!=1:
                errors.append(f"target has ambiguous ownership: {target}")
    active_sources=sorted((ROOT/"HeptaTrade").rglob("*.cpp"))
    for path in active_sources:
        rel=path.relative_to(ROOT).as_posix()
        owners=[m for m in modules if m.get("lifecycle") in {"current","experimental","unsupported"} and any(matches(rel,p) for p in m.get("source_roots",[]))]
        if not owners: errors.append(f"unowned active C++ source: {rel}")
        elif len(owners)>1 and not any(m.get("ownership_mode")=="shared-migration" for m in owners):
            errors.append(f"active source has ambiguous exclusive ownership: {rel}")
    try:
        cpp_limit=int(budget.get("new_cpp_line_limit",0)); py_limit=int(budget.get("new_python_line_limit",0))
    except (TypeError,ValueError): return errors+["invalid source-size limits"]
    exceptions=budget.get("exceptions",{}) if isinstance(budget.get("exceptions"),dict) else {}
    for path in active_sources:
        rel=path.relative_to(ROOT).as_posix(); count=len(path.read_text(encoding="utf-8-sig").splitlines()); exc=exceptions.get(rel)
        if count>cpp_limit and not isinstance(exc,dict): errors.append(f"large C++ source lacks migration budget: {rel}")
        if isinstance(exc,dict) and count>int(exc.get("baseline_lines",0)): errors.append(f"large C++ source grew beyond baseline: {rel}")
    for base in (ROOT/"scripts",ROOT/"research"):
        if not base.is_dir(): continue
        for path in base.rglob("*.py"):
            rel=path.relative_to(ROOT).as_posix(); count=len(path.read_text(encoding="utf-8-sig").splitlines()); exc=exceptions.get(rel)
            if count>py_limit and not isinstance(exc,dict): errors.append(f"large Python source lacks migration budget: {rel}")
            if isinstance(exc,dict) and count>int(exc.get("baseline_lines",0)): errors.append(f"large Python source grew beyond baseline: {rel}")
    for path in (ROOT/"HeptaTrade/cli").glob("*.cpp"):
        text=path.read_text(encoding="utf-8-sig")
        if "int main(" in text and len(text.splitlines())>500: errors.append(f"CLI entry point is not thin: {path.relative_to(ROOT)}")
    if "../HeptaTrade/" in (ROOT/"tests/CMakeLists.txt").read_text(encoding="utf-8-sig"):
        gaps=json.loads((ROOT/"docs/program/gap-registry-v2.json").read_text(encoding="utf-8"))
        state={g["id"]:g["state"] for g in gaps.get("gaps",[])}
        if state.get("G-MOD-002")=="closed": errors.append("G-MOD-002 closed while tests still compile production sources directly")
    return errors


def main() -> int:
    errors=validate()
    for error in errors: print(f"[MODULE-DISCIPLINE] {error}",file=sys.stderr)
    if errors: return 1
    print("[MODULE-DISCIPLINE] PASS"); return 0

if __name__=="__main__": raise SystemExit(main())
