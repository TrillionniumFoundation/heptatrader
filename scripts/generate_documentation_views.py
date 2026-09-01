#!/usr/bin/env python3
"""Generate human-readable views from Hepta machine registries."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
MODULE_PROFILE_PATH = "docs/modules/module-documentation-profiles-v1.json"
MODULE_REGISTRY_PATH = "docs/modules/module-registry-v2.json"
MODULE_SCHEMA_VERSION = "heptatrader.module-manifest.v3"


def load(path: str) -> Any:
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
    registry = load(MODULE_REGISTRY_PATH)
    doc = {"modules": [load("docs/" + path) for path in registry["manifest_paths"]]}
    lines = header("Hepta Module Map", "current and target module boundaries", "generated from module-registry-v2.json")
    lines += ["| Module | Lifecycle | Authority | Trust domain | Build targets | Ownership | DRI / backup | Technical guide |", "|---|---|---|---|---|---|---|---|"]
    for item in sorted(doc["modules"], key=lambda x: x["id"]):
        targets = ", ".join(f"`{v}`" for v in item["build_targets"]) or "—"
        migration = f" ({item['migration_gap']})" if item.get("migration_gap") else ""
        owners = item["owners"]
        guide = item["documentation"]["technical_guide"]
        guide_from_map = guide.removeprefix("modules/")
        lines.append(f"| `{item['id']}` | {item['lifecycle']} | {item['authority']} | `{item['trust_domain']}` | {targets} | {item['ownership_mode']}{migration} | {owners['dri']} / {owners['backup']} | [`{guide}`]({guide_from_map}) |")
    lines += ["", "`shared-migration` 是待拆分债务，不是允许永久共享所有权。", ""]
    return "\n".join(lines)


def roadmap() -> str:
    milestones = load("docs/program/milestone-registry-v1.json")["milestones"]
    gaps = load("docs/program/gap-registry-v2.json")["gaps"]
    by_milestone: dict[str, list[dict[str, Any]]] = {}
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


def _string_list(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or not value or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise ValueError(f"{label} must be a non-empty string array")
    if len(value) != len(set(value)):
        raise ValueError(f"{label} contains duplicates")
    return value


def _profile_index() -> tuple[list[str], dict[str, dict[str, Any]]]:
    document = load(MODULE_PROFILE_PATH)
    if document.get("schema") != "heptatrader.module-documentation-profiles.v1":
        raise ValueError("module documentation profile schema mismatch")
    required_topics = _string_list(
        document.get("required_topics"), "module documentation required_topics"
    )
    profiles: dict[str, dict[str, Any]] = {}
    required_fields = (
        "title", "purpose", "responsibilities", "non_responsibilities",
        "state_notes", "ordering_and_backpressure", "recovery",
        "configuration", "observability", "security", "operations",
        "known_gaps",
    )
    raw_profiles = document.get("profiles")
    if not isinstance(raw_profiles, list):
        raise ValueError("module documentation profiles must be an array")
    for position, profile in enumerate(raw_profiles):
        if not isinstance(profile, dict):
            raise ValueError(f"module documentation profile[{position}] must be an object")
        module_id = profile.get("module_id")
        if not isinstance(module_id, str) or not module_id:
            raise ValueError(f"module documentation profile[{position}] has no module_id")
        if module_id in profiles:
            raise ValueError(f"duplicate module documentation profile: {module_id}")
        for field in required_fields:
            value = profile.get(field)
            if field in {"title", "purpose"}:
                if not isinstance(value, str) or not value.strip():
                    raise ValueError(f"profile {module_id}: {field} must be non-empty")
            else:
                _string_list(value, f"profile {module_id}.{field}")
        profiles[module_id] = profile
    return required_topics, profiles


def _bullets(values: list[str]) -> list[str]:
    return [f"- {value}" for value in values]


def _inline(values: list[str]) -> str:
    return ", ".join(f"`{value}`" for value in values) if values else "none"


def _key_values(values: dict[str, Any]) -> list[str]:
    return [f"- **{key.replace('_', ' ')}:** `{value}`" for key, value in values.items()]


def module_technical_guide(
    manifest_path: str,
    manifest: dict[str, Any],
    profile: dict[str, Any],
    required_topics: list[str],
) -> str:
    module_id = manifest["id"]
    documentation = manifest.get("documentation")
    if not isinstance(documentation, dict):
        raise ValueError(f"module {module_id}: documentation object missing")
    if manifest.get("schema") != MODULE_SCHEMA_VERSION:
        raise ValueError(f"module {module_id}: expected schema {MODULE_SCHEMA_VERSION}")
    if documentation.get("coverage_topics") != required_topics:
        raise ValueError(f"module {module_id}: documentation coverage topics drift")
    expected = "modules/technical/" + Path(manifest_path).stem + ".md"
    if documentation.get("technical_guide") != expected:
        raise ValueError(
            f"module {module_id}: technical guide must be {expected}"
        )

    lines = header(
        f"{profile['title']} Technical Guide",
        f"`{module_id}` version `{manifest['version']}` ({manifest['lifecycle']})",
        f"generated from `{manifest_path}`, module-documentation-profiles-v1.json and canonical registries",
    )
    lines += [
        f"Manifest: [`{manifest_path}`](../manifests/{Path(manifest_path).name})",
        "",
        "## Purpose and Scope",
        "",
        profile["purpose"],
        "",
        f"This module is classified as `{manifest['kind']}` in trust domain "
        f"`{manifest['trust_domain']}` with lifecycle `{manifest['lifecycle']}`.",
        "",
        "## Responsibilities and Non-Responsibilities",
        "",
        "### Responsibilities",
        "",
    ]
    lines += _bullets(profile["responsibilities"])
    lines += ["", "### Non-responsibilities", ""]
    lines += _bullets(profile["non_responsibilities"])
    lines += [
        "",
        "## Trust Domain and Authority",
        "",
        f"- **Declared authority:** {manifest['authority']}",
        f"- **Trust domain:** `{manifest['trust_domain']}`",
        f"- **Ownership mode:** `{manifest['ownership_mode']}`",
        f"- **DRI:** `{manifest['owners']['dri']}`",
        f"- **Backup:** `{manifest['owners']['backup']}`",
        f"- **Required reviewers:** {_inline(manifest['owners']['reviewers'])}",
        f"- **Forbidden dependencies:** {_inline(manifest['forbidden_dependencies'])}",
        "",
        "Authority is limited to the statement above. A dependency, public type or "
        "transport message never grants additional runtime authority by itself.",
        "",
        "## Physical Source and Build Boundaries",
        "",
        f"- **Source roots:** {_inline(manifest['source_roots'])}",
        f"- **Build targets:** {_inline(manifest['build_targets'])}",
        f"- **Allowed module dependencies:** {_inline(manifest['allowed_dependencies'])}",
        "",
        "Physical ownership is verified against "
        "[`source-ownership-registry-v1.json`](../source-ownership-registry-v1.json) "
        "and the configured CMake File API graph. Cross-module compilation requires "
        "an exact, open-gap exception.",
        "",
        "## Contracts and Public Interfaces",
        "",
        f"- **Provides:** {_inline(manifest['provides'])}",
        f"- **Consumes:** {_inline(manifest['consumes'])}",
        "",
        "Contract definitions, providers, consumers and compatibility state are "
        "resolved through the [canonical contract index](../../contracts/CONTRACT-INDEX.md). "
        "Inputs are validated before state admission; schema validity alone is not "
        "proof of issuer authority.",
        "",
        "## State and Data Model",
        "",
    ]
    lines += _key_values(manifest["state"])
    lines += [""]
    lines += _bullets(profile["state_notes"])
    lines += [
        "",
        "## Concurrency, Ordering, and Backpressure",
        "",
        "### Concurrency contract",
        "",
    ]
    lines += _key_values(manifest["concurrency"])
    lines += [
        "",
        "### Backpressure contract",
        "",
    ]
    lines += _key_values(manifest["backpressure"])
    lines += [""]
    lines += _bullets(profile["ordering_and_backpressure"])
    lines += [
        "",
        "## Failure and Recovery",
        "",
        f"- **Risk-increase behavior:** `{manifest['failure']['risk_increase']}`",
        f"- **Safe-exit behavior:** `{manifest['failure']['safe_exit']}`",
        "",
    ]
    lines += _bullets(profile["recovery"])
    lines += [
        "",
        "Failures never authorize a weaker validation path. Recovery begins from "
        "authoritative state, preserves fencing and emits a typed reason code.",
        "",
        "## Configuration and Compatibility",
        "",
    ]
    lines += _bullets(profile["configuration"])
    lines += [
        "",
        f"The manifest version is `{manifest['version']}`. Contract or behavior "
        "changes that alter authority, state, failure or compatibility semantics "
        "require a governed version and registry update.",
        "",
        "## Observability and Resource Budgets",
        "",
        f"- **Resource budget:** `{manifest['resource_budget']}`",
        "",
    ]
    lines += _bullets(profile["observability"])
    lines += [
        "",
        "Telemetry is diagnostic unless another contract explicitly designates it "
        "as authoritative evidence. Queues, labels and retained payloads remain bounded.",
        "",
        "## Security",
        "",
    ]
    lines += _bullets(profile["security"])
    lines += [
        "",
        "The module follows least privilege and must not expose secrets, credentials "
        "or capabilities outside its declared trust boundary.",
        "",
        "## Verification and Testing",
        "",
        f"- **Required verification IDs:** {_inline(manifest['verification'])}",
        "",
        "Each ID resolves through the "
        "[verification test matrix](../../verification/test-matrix-v2.json). "
        "Module changes require positive, negative and relevant fault-path evidence "
        "on the same exact revision.",
        "",
        "## Operations, Rollout, and Known Gaps",
        "",
        "### Operations and rollout",
        "",
    ]
    lines += _bullets(profile["operations"])
    lines += ["", "### Known gaps and qualification boundaries", ""]
    lines += _bullets(profile["known_gaps"])
    lines += [
        "",
        "Open and closed program gaps are authoritative only in the "
        "[gap registry](../../program/gap-registry-v2.json); this guide does not "
        "fabricate external qualification, human approval or production authority.",
        "",
    ]
    return "\n".join(lines)


def module_technical_outputs() -> dict[str, Callable[[], str]]:
    required_topics, profiles = _profile_index()
    registry = load(MODULE_REGISTRY_PATH)
    outputs: dict[str, Callable[[], str]] = {}
    seen_modules: set[str] = set()
    for manifest_path in registry["manifest_paths"]:
        manifest = load("docs/" + manifest_path)
        module_id = manifest.get("id")
        if not isinstance(module_id, str) or not module_id:
            raise ValueError(f"{manifest_path}: module id missing")
        if module_id in seen_modules:
            raise ValueError(f"duplicate module id in registry: {module_id}")
        seen_modules.add(module_id)
        profile = profiles.get(module_id)
        if profile is None:
            raise ValueError(f"module {module_id}: documentation profile missing")
        relative = manifest.get("documentation", {}).get("technical_guide")
        if not isinstance(relative, str) or not relative:
            raise ValueError(f"module {module_id}: technical guide path missing")
        output_path = "docs/" + relative
        if output_path in outputs:
            raise ValueError(f"duplicate technical guide path: {output_path}")
        outputs[output_path] = (
            lambda mp=manifest_path, m=manifest, p=profile, t=required_topics:
                module_technical_guide(mp, m, p, t)
        )
    extra = sorted(set(profiles) - seen_modules)
    if extra:
        raise ValueError("documentation profiles without modules: " + ", ".join(extra))
    return outputs


def outputs() -> dict[str, Callable[[], str]]:
    result: dict[str, Callable[[], str]] = {
        "docs/product/CAPABILITY-MATRIX.md": capability_matrix,
        "docs/contracts/CONTRACT-INDEX.md": contract_index,
        "docs/modules/MODULE-MAP.md": module_map,
        "docs/program/MASTER-ROADMAP.md": roadmap,
    }
    for relative, render in module_technical_outputs().items():
        if relative in result:
            raise ValueError(f"duplicate generated output: {relative}")
        result[relative] = render
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = parser.parse_args()
    drift: list[str] = []
    try:
        rendered_outputs = outputs()
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"[DOC-GENERATOR] invalid source registry: {exc}", file=sys.stderr)
        return 1
    for relative, render in rendered_outputs.items():
        try:
            expected = render()
        except (KeyError, TypeError, ValueError) as exc:
            drift.append(f"cannot render {relative}: {exc}")
            continue
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
