#!/usr/bin/env python3
"""Finish the ModuleManifest V3 migration and remove all half-version states."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
OLD_ID = "hepta.module-manifest.v2"
NEW_ID = "hepta.module-manifest.v3"


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def replace_contract_identity() -> None:
    registry_path = DOCS / "contracts/contract-registry-v2.json"
    registry = load(registry_path)
    matches = [item for item in registry["contracts"] if item.get("id") in {OLD_ID, NEW_ID}]
    if len(matches) != 1:
        raise RuntimeError(f"expected one module-manifest contract, found {len(matches)}")
    contract = matches[0]
    contract["id"] = NEW_ID
    contract["version"] = "3"
    contract["schema_path"] = "docs/modules/module-manifest-schema-v3.json"
    write_json(registry_path, registry)

    module_registry = load(DOCS / "modules/module-registry-v2.json")
    for relative in module_registry["manifest_paths"]:
        path = DOCS / relative
        manifest = load(path)
        manifest["provides"] = [NEW_ID if value == OLD_ID else value for value in manifest["provides"]]
        manifest["consumes"] = [NEW_ID if value == OLD_ID else value for value in manifest["consumes"]]
        write_json(path, manifest)


def replace_remaining_text_references() -> None:
    allowed = {".md", ".json", ".py", ".yml", ".yaml"}
    for path in ROOT.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in allowed:
            continue
        relative = path.relative_to(ROOT)
        if ".git" in relative.parts or "build" in relative.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeError:
            continue
        updated = text.replace(OLD_ID, NEW_ID)
        if updated != text:
            path.write_text(updated, encoding="utf-8")


def write_normative_documents() -> None:
    (DOCS / "modules/MODULE-MANIFEST-SPEC.md").write_text("""# ModuleManifest V3 Specification

Status: current normative
Applies to: all current, experimental, planned, unsupported and deprecated modules
Verification: Draft 2020-12 validation, documentation coverage, source ownership validation and configured CMake File API graph validation
Authority: module manifest contract

A module is an authority, state, failure, concurrency and maintainability boundary—not merely a directory. Every module manifest is validated against `module-manifest-schema-v3.json` before semantic checks run.

## Required identity and lifecycle

Each manifest declares:

```text
schema = heptatrader.module-manifest.v3
stable module id
semantic version
lifecycle
kind and trust domain
authority statement
exclusive or shared-migration ownership
```

`shared-migration` requires an open `migration_gap`. An exclusive module must not carry a migration gap. Unknown fields, malformed nested objects, duplicate arrays, unsafe paths and invalid lifecycle/ownership values are rejected by the schema.

## Required engineering contracts

Every manifest declares:

```text
source roots and CMake targets
provided and consumed contract IDs
allowed and forbidden module dependencies
state model / persistence / writer
concurrency / shard / blocking-I/O / cross-module-lock policy
backpressure and overflow behavior
risk-increase and safe-exit failure behavior
resource budget
DRI / backup / cross-domain reviewers
verification check IDs
```

All contract, module and verification IDs must resolve to canonical registries. Current and experimental CMake targets must exist in the configured or explicitly optional profile.

## Required technical documentation contract

Every manifest contains a closed-world `documentation` object:

```text
technical_guide = modules/technical/<manifest-stem>.md
coverage_topics = exact ordered ModuleManifest V3 topic set
```

The required topic set is:

```text
purpose-scope
responsibilities-boundaries
trust-authority
source-build
contracts-interfaces
state-data
concurrency-ordering-backpressure
failure-recovery
configuration-compatibility
observability-resources
security
verification-testing
operations-rollout-gaps
```

Module-specific design semantics live in `module-documentation-profiles-v1.json`. Physical facts—source roots, targets, contracts, state, concurrency, failure, budgets, owners and verification IDs—come from the manifest. `generate_documentation_views.py` combines both authorities into one registered technical guide for every module. Generated guides are immutable views: direct edits are rejected as drift.

A profile must exist exactly once for every registered module, may not exist for an unregistered module, and must provide non-empty module-specific content for every semantic section. A guide path must be unique. All current, experimental, planned, unsupported and deprecated modules are covered; unsupported modules document prohibition, enablement prerequisites and qualification boundaries rather than pretending to implement runtime behavior.

## Physical source ownership

Manifest claims are not the physical ownership authority. The exact owner of every active C/C++ file is defined in `source-ownership-registry-v1.json`. Multiple manifest claims are permitted only through a bounded exception whose participant set exactly matches the observed owners and whose physical owner, gap, milestone and exit condition are explicit.

The configured CMake graph is read through the File API. A source compiled by a target owned by another module requires an exact `(target, source)` migration exception. Tests that directly compile production sources are governed by the same rule. No wildcard exception or “any owner marked shared” shortcut is allowed.

## Completion rule

A module boundary is complete only when:

1. the manifest passes the formal schema;
2. its documentation object covers the exact required topic set;
3. one module-specific profile and one deterministic technical guide exist;
4. the guide matches the generator on the exact revision;
5. all source files have one physical owner;
6. the configured target/source/dependency graph matches the registries;
7. no unregistered cross-module compilation remains;
8. all migration exceptions linked to the module are removed or remain attached to one open gap;
9. module-local, documentation and system verification pass on the same revision.
""", encoding="utf-8")

    (DOCS / "development/MODULE-CREATION-GUIDE.md").write_text("""# Module Creation Guide

Status: current normative
Applies to: new current, experimental, planned or unsupported modules
Verification: ModuleManifest Draft 2020-12 validation, technical-guide generation, physical ownership and configured CMake graph checks
Authority: module onboarding workflow

Create a module in this order:

```text
authority and failure domain
-> stable ID/version/lifecycle/trust domain
-> provided and consumed contracts
-> state writer and consistency model
-> concurrency/shard/backpressure rules
-> numeric and resource budgets
-> DRI, backup and cross-domain reviewers
-> physical source owner and CMake target owner
-> module-specific technical documentation profile
-> negative/fault/performance tests
-> capability, gap and milestone mapping
```

Do not create a directory first and invent its boundary later. Pure policy cannot depend on venue, session or credential code; an untrusted strategy cannot depend on Execution internals; a venue adapter performs transport and event normalization only; Management cannot possess broker authority.

## Required changes

A module change must update, as applicable:

- one manifest under `docs/modules/manifests/`, including the complete ModuleManifest V3 `documentation` object;
- exactly one profile in `docs/modules/module-documentation-profiles-v1.json` with module-specific content for all semantic sections;
- `module-registry-v2.json`;
- `source-ownership-registry-v1.json` for every new active C/C++ path;
- the canonical contract registry and schema;
- the verification, capability, gap and milestone registries;
- generated views and the module technical guide through `generate_documentation_views.py --write`.

The technical guide is never hand-edited. Its physical/build facts come from the manifest and its explanatory design semantics come from the profile. The documentation check rejects missing profiles, extra profiles, duplicate guide paths, incomplete topic coverage, missing required sections and any generated-view drift.

A new current target must have one ModuleManifest owner and must appear in the configured CMake File API graph. A source must have one physical owner. Shared claims or cross-module compilation require an exact exception whose complete participant set, physical owner, open gap, exit milestone and deletion condition are recorded. Tests link public targets; any temporary direct `.cpp` compilation must be enumerated exactly and removed before its gap closes.

## Local acceptance sequence

```bash
python3 scripts/generate_documentation_views.py --write
python3 scripts/generate_documentation_views.py --check
python3 -m unittest discover -s tests/python -p 'test_*.py'
cmake -S . -B build/docs-control -DHEPTA_ENABLE_IBAPI=OFF
python3 scripts/check_documentation_control_plane.py --check-cmake --build-dir build/docs-control
```

A module is not onboarding-complete until these checks and all module verification IDs pass on one exact revision.
""", encoding="utf-8")

    (DOCS / "program/TRACEABILITY-MODEL.md").write_text("""# Development Traceability Model

Status: current normative
Applies to: capability, module, documentation, contract, test, gap, milestone and evidence registries
Verification: documentation-control-plane cross-reference and generated-view checks
Authority: end-to-end development traceability

完整追踪链为：

```text
product capability
  -> providing/consuming modules
  -> ModuleManifest V3 authority/state/concurrency/failure/resource contract
  -> module-specific documentation profile and generated technical guide topics
  -> versioned contracts and schemas
  -> source/build/deployment ownership
  -> verification check IDs and fault/performance budgets
  -> gap/workstream/milestone
  -> exact-revision evidence / external qualification
```

技术文档链进一步要求：

```text
module id
  -> unique manifest documentation object
  -> exact required coverage-topic set
  -> unique module profile
  -> deterministic technical guide
  -> source roots / CMake targets / contracts / state machine
  -> failure recovery / configuration / observability / security / operations
  -> module verification IDs on the same revision
```

任何 capability 如果缺少 module、contract、verification 或 maturity/qualification 映射，只能是 `planned` 或 `unsupported`。任何 current module 如果没有 owner、backup、state/concurrency/failure/resource contract，或者没有完整且无漂移的技术手册，不得作为独立团队交付面。

生成视图只展示注册表结果，不创建新状态。PR 描述、issue、dashboard 和 release note 必须引用同一 ID，不能发明平行命名。外部 PAPER、组织团队和独立审批证据只能由相应环境或主体产生，仓库文档不得代签。
""", encoding="utf-8")

    (DOCS / "program/DOCUMENTATION-UPGRADE-PLAN.md").write_text("""# Documentation Control Plane Continuous Upgrade Plan

Status: current normative plan
Applies to: documentation, registries, schemas, generators, validators, CI, build graph and install tree
Verification: M0/M1/M2 gaps and exact-revision gates
Authority: documentation-upgrade implementation sequence

## Objective

The current tree keeps one discoverable development-document authority. Normative facts are singular, structural facts are machine-readable, generated views are reproducible, every module has a detailed technical guide, and completion state is derived from exact-revision evidence. Git history—not checked-in aliases, archived prose or dormant build entrypoints—is the historical record.

## Current audit closure sequence

1. **Physical historical cleanup**  
   Remove compatibility aliases, old PLAN/status files, legacy Markdown/text/media, and dormant CMake/Visual Studio entrypoints that can be indexed or opened as an alternative project.
2. **Repository-wide document inventory**  
   Register every Markdown surface outside `docs/` as an entrypoint-only document linked to one canonical target. No package README may create independent architecture, capability or roadmap authority.
3. **Formal manifest validation**  
   Apply the checked-in Draft 2020-12 ModuleManifest V3 schema to every manifest. Reject unknown fields, wrong versions/types, unsafe paths, duplicate arrays, incomplete documentation topics and invalid migration states before semantic validation.
4. **Per-module technical documentation**  
   Require one module-specific profile and one generated technical guide for every registered module. Cover purpose, boundaries, authority, source/build, contracts, state, concurrency, backpressure, recovery, configuration, observability, resources, security, testing, rollout and known gaps. Reject missing, extra, duplicate or drifted outputs.
5. **Physical source ownership**  
   Map every active C/C++ file to exactly one physical owner. Permit overlap only for an exact participant set attached to one open gap, one physical owner and one exit milestone.
6. **Configured build-graph binding**  
   Query the CMake File API after configure and validate actual target ownership, compiled sources, direct production-source test inclusion and inter-module dependencies. Static target-name regexes are not sufficient evidence.
7. **Contract and capability traceability**  
   Resolve capability → module → technical documentation → contract/schema → source/target owner → verification → gap/workstream/milestone → exact evidence. Generated matrices and guides display registry facts but never create completion state.
8. **Exact-revision evidence**  
   Run read-only documentation, core and canonical-full workflows on the unchanged head and merge candidate. External PAPER qualification remains a separate protected lane and never grants LIVE.

## Exit contract

The documentation-control-plane milestone can close only when all of the following hold on one unchanged revision:

- `docs/` contains one registered current graph and no alias, old PLAN or manual exact-head file;
- all Markdown outside `docs/` is explicitly registered as entrypoint-only and links to a canonical `docs/` target;
- `legacy/` contains no development prose, media or build-system entrypoint;
- every ModuleManifest V3 passes Draft 2020-12 validation;
- all 22 registered modules have exactly one profile, a complete documentation topic set and a registered deterministic technical guide;
- the 22 guides include all required technical sections and match the generator byte-for-byte;
- every active C/C++ file has exactly one physical owner or one exact same-gap overlap exception;
- the configured CMake target/source/dependency graph matches module ownership;
- every direct production-source compilation is an exact, open-gap migration exception;
- generated views, documentation, repository, module, CMake graph, install, test and reliability gates pass on the same head;
- M0/M1/M2 state is closed only by evidence, never by editing prose or a status field.

Execution remains the sole venue-mutation authority throughout this work. CTP, XT/MiniQMT and LIVE remain unsupported/fail-closed; IB PAPER remains conditional on external exact-artifact qualification.
""", encoding="utf-8")


def verify() -> None:
    registry = load(DOCS / "contracts/contract-registry-v2.json")
    contracts = [item for item in registry["contracts"] if item.get("id") == NEW_ID]
    if len(contracts) != 1 or contracts[0].get("version") != "3":
        raise RuntimeError("ModuleManifest V3 contract identity was not migrated")
    stale = []
    for path in ROOT.rglob("*"):
        if path.is_file() and path.suffix.lower() in {".md", ".json", ".py", ".yml", ".yaml"}:
            try:
                if OLD_ID in path.read_text(encoding="utf-8"):
                    stale.append(str(path.relative_to(ROOT)))
            except UnicodeError:
                pass
    if stale:
        raise RuntimeError("stale ModuleManifest V2 references: " + ", ".join(stale))


def main() -> int:
    replace_contract_identity()
    replace_remaining_text_references()
    write_normative_documents()
    verify()
    print("[MODULE-DOCS-FINALIZE] ModuleManifest V3 identity and normative docs complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
