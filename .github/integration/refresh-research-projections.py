#!/usr/bin/env python3
"""Refresh additive research metadata. IB projection is not SDK qualification."""
from __future__ import annotations
import copy
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from verify_build_ownership import load_json, observe, validate_inventory

BRANCH = "integration/heptadll-modular-20260921"
NAMES = {"hepta_research_market_data", "hepta_research_replay", "hepta_research_strategy",
         "hepta_research_client", "hepta_research", "hepta_research_core_tests",
         "hepta_research_client_tests"}
AGGREGATE = "hepta_core_test_binaries"
ALLOWED = {"docs/build-targets.json", "docs/module-catalog.json",
           "docs/modules/shadow-research.md", "docs/modules/agent-entry.md"}

def require(ok, reason):
    if not ok:
        raise ValueError(reason)

def main():
    require(sys.argv[1:] == [BRANCH], "explicit integration branch binding required")
    require(not subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT,
                                       text=True).strip(), "requires clean source checkout")
    path = ROOT / "docs/build-targets.json"
    inventory = load_json(path)
    fresh = observe(ROOT, "core")
    old = {x["name"]: x for x in inventory["profiles"]["core"]["targets"]}
    new = {x["name"]: x for x in fresh["targets"]}
    require(set(old) <= set(new), "cannot remove an existing target")
    require(set(new) - set(old) in (NAMES, set()), "unexpected/partial target introduction")
    for name in set(old) & set(new):
        if name == AGGREGATE:
            before, after = copy.deepcopy(old[name]), copy.deepcopy(new[name])
            a, b = set(before.pop("dependencies")), set(after.pop("dependencies"))
            require(before == after and a <= b and b - a <= NAMES,
                    "only additive research test dependencies permitted")
        else:
            require(old[name] == new[name], "existing target changed: " + name)
    for name in NAMES:
        require(name in new and new[name]["declared_in"] == "cmake/HeptaResearch.cmake",
                "unexpected research target declaration")
        require(all(x["kind"] in {"test", "implementation"}
                    for x in new[name]["translation_units"]), "external/generated source introduced")
        require(not any(x.startswith("hepta_ib") for x in new[name]["dependencies"]),
                "research must not introduce a broker dependency")
    ib = {x["name"]: x for x in inventory["profiles"]["ib"]["targets"]}
    for name in NAMES:
        require(name not in ib or ib[name] == new[name], "IB research projection conflict")
        ib[name] = copy.deepcopy(new[name])
    extra = set(new[AGGREGATE]["dependencies"]) - set(old[AGGREGATE]["dependencies"])
    ib[AGGREGATE]["dependencies"] = sorted(set(ib[AGGREGATE]["dependencies"]) | extra)
    inventory["profiles"]["core"] = fresh
    inventory["profiles"]["ib"]["targets"] = sorted(ib.values(), key=lambda x: x["name"])
    validate_inventory(ROOT, inventory)
    catalog_path = ROOT / "docs/module-catalog.json"
    catalog = load_json(catalog_path)
    additions = {"shadow-research": "tests/research_core_tests.cpp",
                 "agent-entry": "tests/research_intent_client_tests.cpp"}
    documents, seen = {}, set()
    for module in catalog["modules"]:
        name = module["id"]
        if name not in additions:
            continue
        seen.add(name)
        require(module["document"] == f"docs/modules/{name}.md", "unexpected module document")
        if additions[name] not in module["tests"]:
            module["tests"].append(additions[name])
        doc = ROOT / module["document"]
        text = doc.read_text(encoding="utf-8")
        line = "Tests: " + ", ".join(f"`{test}`" for test in module["tests"])
        text, count = re.subn(r"^Tests:.*$", lambda _: line, text, count=1, flags=re.MULTILINE)
        require(count == 1, "missing canonical Tests metadata")
        if "../technical/heptadll-integration.md" not in text:
            text += ("\n## Native research integration\n\n"
                     "[HeptaDLL capability integration](../technical/heptadll-integration.md) "
                     "defines the native research API, offline-only replay, durable unprivileged "
                     "proposal client, test scope and retained source/venue boundaries. "
                     "No broker capability or legacy runtime is restored.\n")
        documents[doc] = text
    require(seen == set(additions), "missing module owner")
    path.write_text(json.dumps(inventory, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    catalog_path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for doc, text in documents.items():
        doc.write_text(text, encoding="utf-8")
    changed = set(subprocess.check_output(["git", "diff", "--name-only"], cwd=ROOT, text=True).splitlines())
    require(changed <= ALLOWED, "unapproved path changed")
    print("Observed core graph and updated additive metadata; IB SDK NOT executed.")

if __name__ == "__main__":
    main()
