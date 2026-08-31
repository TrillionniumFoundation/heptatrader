#!/usr/bin/env python3
"""Generate human-readable views from Hepta machine registries."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"


def load(path: str):
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def header(title: str, applies: str, authority: str) -> list[str]:
    return [
        f"# {title}", "", "Status: generated current view",
        f"Applies to: {applies}",
        "Verification: `python3 scripts/generate_documentation_views.py --check`",
        f"Authority: {authority}", "",
        "> 本文件由机器注册表确定性生成。请修改注册表，不要直接修改本文件。", "",
    ]


def capability_matrix() -> str:
    doc = load("docs/product/capability-registry-v2.json")
    lines = header("Hepta Capability Matrix", "repository-wide capability claims", "generated from capability-registry-v2.json")
    lines += ["| Capability | State | Simulator | PAPER | LIVE | Release |", "|---|---|---|---|---|---|"]
    for item in sorted(doc["capabilities"], key=lambda x: x["id"]):
        env = item["integration"]
        lines.append(f"| `{item['id']}` — {item['title']} | **{item['declared_state']}** | {env['simulator']} | {env['paper']} | {env['live']} | {item['release']} |")
    lines += ["", "状态是声明上限；实际可用性不得超过 exact-revision evidence 与 qualification。", ""]
    return "\n".join(lines)


def contract_index() -> str:
    doc = load("docs/contracts/contract-registry-v2.json")
    lines = header("Hepta Contract Index", "all versioned inter-module contracts", "generated from contract-registry-v2.json")
    lines += ["| Contract | Stability | Canonical document | Schema | Providers | Consumers |", "|---|---|---|---|---|---|"]
    for item in sorted(doc["contracts"], key=lambda x: x["id"]):
        schema = f"`../../{item['schema_path']}`" if item.get("schema_path") else "—"
        providers = ", ".join(f"`{v}`" for v in item["providers"]) or "external/none"
        consumers = ", ".join(f"`{v}`" for v in item["consumers"]) or "none"
        lines.append(f"| `{item['id']}` | {item['stability']} | [`{item['document']}`](../{item['document']}) | {schema} | {providers} | {consumers} |")
    lines.append("")
    return "\n".join(lines)


def module_map() -> str:
    registry = load("docs/modules/module-registry-v2.json")
    doc = {"modules": [load("docs/" + path) for path in registry["manifest_paths"]]}
    lines = header("Hepta Module Map", "current and target module boundaries", "generated from module-registry-v2.json")
    lines += ["| Module | Lifecycle | Authority | Trust domain | Build targets | Ownership | DRI / backup |", "|---|---|---|---|---|---|---|"]
    for item in sorted(doc["modules"], key=lambda x: x["id"]):
        targets = ", ".join(f"`{v}`" for v in item["build_targets"]) or "—"
        migration = f" ({item['migration_gap']})" if item.get("migration_gap") else ""
        owners = item["owners"]
        lines.append(f"| `{item['id']}` | {item['lifecycle']} | {item['authority']} | `{item['trust_domain']}` | {targets} | {item['ownership_mode']}{migration} | {owners['dri']} / {owners['backup']} |")
    lines += ["", "`shared-migration` 是待拆分债务，不是允许永久共享所有权。", ""]
    return "\n".join(lines)


def roadmap() -> str:
    milestones = load("docs/program/milestone-registry-v1.json")["milestones"]
    gaps = load("docs/program/gap-registry-v2.json")["gaps"]
    by_milestone: dict[str, list[dict]] = {}
    for gap in gaps:
        by_milestone.setdefault(gap["milestone"], []).append(gap)
    lines = header("Hepta Modular Runtime Global Development Roadmap V2", "all active development workstreams", "generated from milestone and gap registries")
    lines += ["| ID | Milestone | State | Depends on | Open/blocked gaps | Exit contract |", "|---|---|---|---|---:|---|"]
    for item in milestones:
        open_count = sum(g["state"] != "closed" for g in by_milestone.get(item["id"], []))
        deps = ", ".join(item["depends_on"]) or "—"
        exit_text = "; ".join(item["exit"])
        lines.append(f"| `{item['id']}` | {item['title']} | **{item['state']}** | {deps} | {open_count} | {exit_text} |")
    lines += ["", "实时完成状态必须由 exact-revision evidence 派生；本视图不替代 CI 或外部 qualification。", ""]
    return "\n".join(lines)

OUTPUTS = {
    "docs/product/CAPABILITY-MATRIX.md": capability_matrix,
    "docs/contracts/CONTRACT-INDEX.md": contract_index,
    "docs/modules/MODULE-MAP.md": module_map,
    "docs/program/MASTER-ROADMAP.md": roadmap,
}


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = parser.parse_args()
    drift: list[str] = []
    for relative, render in OUTPUTS.items():
        expected = render()
        path = ROOT / relative
        if args.write:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(expected, encoding="utf-8")
        else:
            try:
                actual = path.read_text(encoding="utf-8")
            except OSError:
                drift.append(f"missing generated view: {relative}")
                continue
            if actual != expected:
                drift.append(f"generated view drift: {relative}")
    if drift:
        for item in drift:
            print(f"[DOC-GENERATOR] {item}", file=sys.stderr)
        return 1
    print("[DOC-GENERATOR] PASS")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
