#!/usr/bin/env python3
"""One-shot hardening for concrete ModuleManifest engineering semantics."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GENERIC_SENTINELS = ("declared-only", "module-declared")
ACTIVE_LIFECYCLES = {"current", "experimental", "unsupported"}

SEMANTICS = {
    "hepta.agent.support": {
        "persistence": "process-local-reconstructible-cache",
        "shard_key": "agent-session-owner",
        "blocking_io": "forbidden-on-event-dispatch",
    },
    "hepta.client.runtime": {
        "persistence": "none-ephemeral-request-context",
        "shard_key": "caller-owned",
        "blocking_io": "bounded-client-transport-only",
    },
    "hepta.documentation.control": {
        "persistence": "git-versioned-repository-state",
        "shard_key": "repository-revision",
        "blocking_io": "filesystem-batch-validation-only",
    },
    "hepta.execution.runtime": {
        "persistence": "durable-oms-journal-and-authoritative-state",
        "shard_key": "execution-domain-account-order",
        "blocking_io": "journal-and-venue-boundary-only",
    },
    "hepta.feature.runtime": {
        "persistence": "derived-process-local-generation-cache",
        "shard_key": "venue-instrument-feature-set",
        "blocking_io": "forbidden-on-feature-compute",
    },
    "hepta.gateway.runtime": {
        "persistence": "durable-session-audit-plus-ephemeral-connections",
        "shard_key": "session-owner",
        "blocking_io": "bounded-af-unix-and-audit-only",
    },
    "hepta.global.decision": {
        "persistence": "none-recomputable-from-immutable-inputs",
        "shard_key": "capital-pool-policy-revision",
        "blocking_io": "forbidden-on-solver-path",
    },
    "hepta.management.control": {
        "persistence": "canonical-config-plus-versioned-rollout-state",
        "shard_key": "module-rollout-domain",
        "blocking_io": "control-path-only",
    },
    "hepta.marketdata.runtime": {
        "persistence": "process-local-state-rebuilt-from-feed-or-replay",
        "shard_key": "venue-instrument",
        "blocking_io": "forbidden-on-admission-and-snapshot",
    },
    "hepta.numeric.core": {
        "persistence": "none",
        "shard_key": "none",
        "blocking_io": "forbidden",
    },
    "hepta.observability.runtime": {
        "persistence": "bounded-non-authoritative-export-buffer",
        "shard_key": "thread-metric-target",
        "blocking_io": "exporter-thread-only",
    },
    "hepta.portfolio.compiler": {
        "persistence": "none-recomputable-from-plan-and-metadata",
        "shard_key": "none-pure-reentrant",
        "blocking_io": "forbidden",
    },
    "hepta.protocol.contracts": {
        "persistence": "none",
        "shard_key": "none",
        "blocking_io": "forbidden",
    },
    "hepta.research.protocol": {
        "persistence": "append-only-run-artifacts",
        "shard_key": "research-run",
        "blocking_io": "offline-runner-only",
    },
    "hepta.risk.policy": {
        "persistence": "none-immutable-policy-input",
        "shard_key": "none-pure-reentrant",
        "blocking_io": "forbidden",
    },
    "hepta.session.runtime": {
        "persistence": "encrypted-lease-store-atomic-replace",
        "shard_key": "supervisor-single-owner",
        "blocking_io": "durable-store-and-af-unix-control-only",
    },
    "hepta.simulation.runtime": {
        "persistence": "none-scenario-replay-from-immutable-inputs",
        "shard_key": "capital-pool-scenario",
        "blocking_io": "forbidden",
    },
    "hepta.strategy.runtime": {
        "persistence": "module-isolated-approved-checkpoint",
        "shard_key": "strategy-agent-instance",
        "blocking_io": "startup-checkpoint-only",
    },
    "hepta.venue.ctp": {
        "persistence": "none-unsupported",
        "shard_key": "none-unsupported",
        "blocking_io": "forbidden-unsupported",
    },
    "hepta.venue.ib": {
        "persistence": "process-local-adapter-state-reconciled-from-broker",
        "shard_key": "broker-session-account",
        "blocking_io": "official-ib-transport-thread-only",
    },
    "hepta.venue.simulator": {
        "persistence": "scenario-local-replayable-state",
        "shard_key": "scenario-order-book",
        "blocking_io": "forbidden",
    },
    "hepta.venue.xt": {
        "persistence": "none-unsupported",
        "shard_key": "none-unsupported",
        "blocking_io": "forbidden-unsupported",
    },
}


def load(path: str):
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def write(path: str, value) -> None:
    (ROOT / path).write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def update_manifests() -> None:
    registry_path = "docs/modules/module-registry-v2.json"
    registry = load(registry_path)
    policy = registry.setdefault("policy", {})
    if "generic_engineering_sentinels_forbidden" in policy:
        raise SystemExit("generic engineering sentinel policy already exists")
    policy["generic_engineering_sentinels_forbidden"] = list(GENERIC_SENTINELS)

    observed: set[str] = set()
    for relative in registry["manifest_paths"]:
        path = "docs/" + relative
        manifest = load(path)
        module_id = manifest.get("id")
        if module_id not in SEMANTICS:
            raise SystemExit(f"missing concrete semantics for {module_id}: {path}")
        if module_id in observed:
            raise SystemExit(f"duplicate module id: {module_id}")
        observed.add(module_id)
        values = SEMANTICS[module_id]
        manifest["state"]["persistence"] = values["persistence"]
        manifest["concurrency"]["shard_key"] = values["shard_key"]
        manifest["concurrency"]["blocking_io"] = values["blocking_io"]
        for object_name, field_name in (
            ("state", "persistence"),
            ("concurrency", "shard_key"),
            ("concurrency", "blocking_io"),
        ):
            value = manifest[object_name][field_name]
            if value in GENERIC_SENTINELS or not value.strip():
                raise SystemExit(
                    f"{module_id}: non-concrete {object_name}.{field_name}: {value!r}"
                )
        write(path, manifest)

    if observed != set(SEMANTICS):
        raise SystemExit(
            "semantic mapping has unregistered modules: "
            + ", ".join(sorted(set(SEMANTICS) - observed))
        )
    write(registry_path, registry)


def update_schema_and_spec() -> None:
    schema_path = "docs/modules/module-manifest-schema-v3.json"
    schema = load(schema_path)
    targets = (
        schema["properties"]["state"]["properties"]["persistence"],
        schema["properties"]["concurrency"]["properties"]["shard_key"],
        schema["properties"]["concurrency"]["properties"]["blocking_io"],
    )
    for target in targets:
        if "not" in target:
            raise SystemExit("manifest schema already has concrete semantic guard")
        target["not"] = {"enum": list(GENERIC_SENTINELS)}
    write(schema_path, schema)

    spec_path = ROOT / "docs/modules/MODULE-MANIFEST-SPEC.md"
    text = spec_path.read_text(encoding="utf-8")
    marker = "## Concrete engineering semantics"
    if marker in text:
        raise SystemExit("module semantic specification already exists")
    section = """

## Concrete engineering semantics

Current, experimental and unsupported modules must state concrete engineering behavior. `state.persistence` identifies the actual durable, derived, checkpoint or no-persistence model; `concurrency.shard_key` identifies the real serialization or ownership key, including an explicit `none`; `concurrency.blocking_io` identifies the exact boundary where blocking I/O is allowed or states that it is forbidden.

Generic values such as `module-declared` and `declared-only` are invalid. A non-empty placeholder is not an engineering contract. Unsupported modules still declare concrete `none-unsupported` and `forbidden-unsupported` behavior so activation, packaging and future implementation cannot inherit a permissive default. Any change to these fields must update the affected guide, tests and resource/failure analysis on the same revision.
"""
    spec_path.write_text(text.rstrip() + section.rstrip() + "\n", encoding="utf-8")


def update_checker() -> None:
    path = ROOT / "scripts/check_module_discipline.py"
    text = path.read_text(encoding="utf-8")
    constant_anchor = 'GAPS_REL = "docs/program/gap-registry-v2.json"\n\n'
    if constant_anchor not in text:
        raise SystemExit("checker constant anchor missing")
    if "_GENERIC_ENGINEERING_SENTINELS" in text:
        raise SystemExit("checker semantic guard already exists")
    text = text.replace(
        constant_anchor,
        constant_anchor
        + '_GENERIC_ENGINEERING_SENTINELS = frozenset({"declared-only", "module-declared"})\n\n',
        1,
    )

    helper_anchor = "\ndef validate() -> list[str]:\n"
    if helper_anchor not in text:
        raise SystemExit("checker validate anchor missing")
    helper = r'''

def _validate_manifest_engineering_semantics(
    modules: dict[str, dict[str, Any]], errors: list[str]
) -> None:
    fields = (
        ("state", "persistence"),
        ("concurrency", "shard_key"),
        ("concurrency", "blocking_io"),
    )
    for module_id, module in sorted(modules.items()):
        if module.get("lifecycle") not in ACTIVE_LIFECYCLES:
            continue
        for object_name, field_name in fields:
            parent = module.get(object_name)
            value = parent.get(field_name) if isinstance(parent, dict) else None
            if not isinstance(value, str) or not value.strip():
                errors.append(
                    f"module {module_id}: {object_name}.{field_name} must be concrete"
                )
            elif value in _GENERIC_ENGINEERING_SENTINELS:
                errors.append(
                    f"module {module_id}: {object_name}.{field_name} uses "
                    f"forbidden generic sentinel {value}"
                )
'''
    text = text.replace(helper_anchor, helper + helper_anchor, 1)

    call_anchor = (
        '    if ownership.get("schema") != "heptatrader.source-ownership-registry.v1":\n'
        '        errors.append("source ownership registry schema mismatch")\n\n'
        '    gaps = _gap_map(errors)\n'
    )
    if call_anchor not in text:
        raise SystemExit("checker call anchor missing")
    text = text.replace(
        call_anchor,
        '    if ownership.get("schema") != "heptatrader.source-ownership-registry.v1":\n'
        '        errors.append("source ownership registry schema mismatch")\n\n'
        '    _validate_manifest_engineering_semantics(modules, errors)\n\n'
        '    gaps = _gap_map(errors)\n',
        1,
    )
    path.write_text(text, encoding="utf-8")


def update_tests() -> None:
    path = ROOT / "tests/python/test_module_discipline.py"
    text = path.read_text(encoding="utf-8")
    import_anchor = "import unittest\n"
    if import_anchor not in text or "import jsonschema\n" in text:
        raise SystemExit("module test import anchor invalid")
    text = text.replace(import_anchor, "import unittest\n\nimport jsonschema\n", 1)

    method_anchor = "    def test_source_size_exceptions_are_no_growth_debt_not_closed_gaps(self) -> None:\n"
    if method_anchor not in text:
        raise SystemExit("module test method anchor missing")
    method = r'''    def test_active_manifest_engineering_semantics_are_concrete(self) -> None:
        registry = json.loads(
            (self.root / "docs/modules/module-registry-v2.json").read_text(
                encoding="utf-8"
            )
        )
        schema = json.loads(
            (self.root / "docs/modules/module-manifest-schema-v3.json").read_text(
                encoding="utf-8"
            )
        )
        validator = jsonschema.Draft202012Validator(schema)
        sentinels = {"declared-only", "module-declared"}
        fields = (
            ("state", "persistence"),
            ("concurrency", "shard_key"),
            ("concurrency", "blocking_io"),
        )
        for relative in registry["manifest_paths"]:
            manifest = json.loads(
                (self.root / "docs" / relative).read_text(encoding="utf-8")
            )
            if manifest["lifecycle"] not in {"current", "experimental", "unsupported"}:
                continue
            for object_name, field_name in fields:
                self.assertNotIn(
                    manifest[object_name][field_name], sentinels, manifest["id"]
                )
                for sentinel in sentinels:
                    candidate = json.loads(json.dumps(manifest))
                    candidate[object_name][field_name] = sentinel
                    with self.assertRaises(jsonschema.ValidationError):
                        validator.validate(candidate)

'''
    text = text.replace(method_anchor, method + method_anchor, 1)
    path.write_text(text, encoding="utf-8")


def update_registries() -> None:
    matrix_path = "docs/verification/test-matrix-v2.json"
    matrix = load(matrix_path)
    item = next(
        check for check in matrix["checks"] if check["id"] == "module-manifest-schema"
    )
    item["evidence"] = (
        "Draft 2020-12 validation for every ModuleManifest, including rejection "
        "of generic persistence, shard-key and blocking-I/O sentinels"
    )
    write(matrix_path, matrix)

    gaps_path = "docs/program/gap-registry-v2.json"
    gaps = load(gaps_path)
    gap_id = "G-DOC-004"
    if any(item.get("id") == gap_id for item in gaps["gaps"]):
        raise SystemExit(f"{gap_id} already exists")
    gaps["gaps"].append(
        {
            "id": gap_id,
            "priority": "P0",
            "title": (
                "Active ModuleManifests permit non-empty generic engineering "
                "sentinels instead of concrete persistence and concurrency semantics"
            ),
            "workstream": "WS-DOC",
            "milestone": "M1",
            "state": "closed",
            "evidence": [
                "docs-generated",
                "module-documentation-coverage",
                "module-manifest-schema",
                "module-registry",
            ],
        }
    )
    write(gaps_path, gaps)


def main() -> int:
    update_manifests()
    update_schema_and_spec()
    update_checker()
    update_tests()
    update_registries()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
