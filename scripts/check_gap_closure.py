#!/usr/bin/env python3
"""Validate repository-executable closure and external qualification receipts.

Repository-executable gaps must be closed on the exact tree. Registered external
gates may remain open. An external gate may be marked closed only when it points
at a separate, verifier-produced, schema-valid receipt. This checker validates
the receipt envelope and binding; it never creates broker or governance evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GAP_REGISTRY = ROOT / "docs/program/gap-registry-v2.json"
DEFAULT_MODULE_REGISTRY = ROOT / "docs/modules/module-registry-v2.json"

FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
FINGERPRINT = re.compile(r"^sha256:[0-9a-f]{64}$")
IB_SCENARIOS = {
    "connect_authoritative_snapshot",
    "disconnect_reconnect",
    "partial_fill",
    "duplicate_out_of_order_status",
    "broker_reject",
    "stale_quote",
    "outcome_uncertain",
    "cancel_race",
    "reconcile_divergence",
    "lease_fencing",
    "kill_switch",
    "terminal_recovery",
}


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load(path: Path, label: str, errors: list[str]) -> dict[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_unique_object,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        errors.append(f"{label}: cannot load {path}: {exc}")
        return {}
    if not isinstance(value, dict):
        errors.append(f"{label}: root must be an object")
        return {}
    return value


def _repository_root(module_registry_path: Path) -> Path:
    path = module_registry_path.resolve()
    if (
        path.name == "module-registry-v2.json"
        and path.parent.name == "modules"
        and path.parent.parent.name == "docs"
    ):
        return path.parents[2]
    return path.parent


def _safe_receipt_path(
    raw: object,
    *,
    repository_root: Path,
    gate_id: str,
    errors: list[str],
) -> Path | None:
    if not isinstance(raw, str) or not raw.strip():
        errors.append(
            f"external gate {gate_id}: closed state requires qualification_receipt"
        )
        return None
    relative = Path(raw)
    if relative.is_absolute() or ".." in relative.parts:
        errors.append(
            f"external gate {gate_id}: qualification_receipt path is unsafe"
        )
        return None
    root = repository_root.resolve()
    resolved = (root / relative).resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        errors.append(
            f"external gate {gate_id}: qualification_receipt escapes repository"
        )
        return None
    try:
        metadata = resolved.stat()
    except OSError as exc:
        errors.append(
            f"external gate {gate_id}: qualification_receipt is unavailable: {exc}"
        )
        return None
    if not metadata.st_size or metadata.st_size > 4 * 1024 * 1024:
        errors.append(
            f"external gate {gate_id}: qualification_receipt size is invalid"
        )
        return None
    if not resolved.is_file():
        errors.append(
            f"external gate {gate_id}: qualification_receipt must be a regular file"
        )
        return None
    return resolved


def _required_sha256(value: object, label: str, errors: list[str]) -> None:
    if not isinstance(value, str) or SHA256.fullmatch(value) is None:
        errors.append(f"{label}: expected canonical sha256 hex")


def _verify_ib_receipt(payload: dict[str, Any], errors: list[str]) -> None:
    label = "G-IB-001 receipt"
    if payload.get("schema") != "hepta.ib-paper-qualification-verification.v1":
        errors.append(f"{label}: schema mismatch")
    if payload.get("verified") is not True or payload.get("qualified") is not True:
        errors.append(f"{label}: verified and qualified must both be true")
    git_sha = payload.get("git_sha")
    if not isinstance(git_sha, str) or FULL_SHA.fullmatch(git_sha) is None:
        errors.append(f"{label}: git_sha is not canonical")
    _required_sha256(payload.get("result_sha256"), f"{label}.result_sha256", errors)

    for field in ("binary", "harness"):
        value = payload.get(field)
        if not isinstance(value, dict):
            errors.append(f"{label}.{field}: expected object")
            continue
        if not isinstance(value.get("name"), str) or not value["name"]:
            errors.append(f"{label}.{field}.name: missing")
        _required_sha256(value.get("sha256"), f"{label}.{field}.sha256", errors)

    broker = payload.get("broker")
    if not isinstance(broker, dict):
        errors.append(f"{label}.broker: expected object")
    else:
        if broker.get("venue") != "IB" or broker.get("environment") != "PAPER":
            errors.append(f"{label}.broker: must bind IB PAPER")
        for field in ("session_id", "account_fingerprint", "host_fingerprint"):
            value = broker.get(field)
            if not isinstance(value, str) or not value:
                errors.append(f"{label}.broker.{field}: missing")

    scenarios = payload.get("scenarios")
    if not isinstance(scenarios, list):
        errors.append(f"{label}.scenarios: expected array")
        return
    observed: set[str] = set()
    for index, scenario in enumerate(scenarios):
        if not isinstance(scenario, dict):
            errors.append(f"{label}.scenarios[{index}]: expected object")
            continue
        scenario_id = scenario.get("id")
        if not isinstance(scenario_id, str) or scenario_id in observed:
            errors.append(f"{label}.scenarios[{index}].id: invalid or duplicate")
            continue
        observed.add(scenario_id)
        if scenario.get("status") != "PASS":
            errors.append(f"{label}.scenarios[{index}]: status must be PASS")
        evidence = scenario.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            errors.append(f"{label}.scenarios[{index}]: evidence is missing")
    if observed != IB_SCENARIOS:
        errors.append(f"{label}: scenario set does not match protected verifier")


def _canonical_digest(value: Any) -> str:
    data = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _verify_governance_receipt(payload: dict[str, Any], errors: list[str]) -> None:
    label = "G-TEAM-001 receipt"
    if set(payload) != {"body", "receipt_sha256"}:
        errors.append(f"{label}: top-level keys must be body and receipt_sha256")
    body = payload.get("body")
    if not isinstance(body, dict):
        errors.append(f"{label}.body: expected object")
        return
    if body.get("schema") != "heptatrader.github-governance-receipt.v1":
        errors.append(f"{label}: schema mismatch")
    if body.get("repository") != "TrillionniumFoundation/heptatrader":
        errors.append(f"{label}: repository binding mismatch")
    if body.get("default_branch") != "main":
        errors.append(f"{label}: default branch binding mismatch")
    for field in ("head_sha", "merge_group_sha"):
        value = body.get(field)
        if not isinstance(value, str) or FULL_SHA.fullmatch(value) is None:
            errors.append(f"{label}.{field}: expected canonical full SHA")
    if not isinstance(body.get("pull_number"), int) or isinstance(
        body.get("pull_number"), bool
    ) or body["pull_number"] <= 0:
        errors.append(f"{label}.pull_number: expected positive integer")
    if not isinstance(body.get("ruleset_id"), int) or isinstance(
        body.get("ruleset_id"), bool
    ) or body["ruleset_id"] <= 0:
        errors.append(f"{label}.ruleset_id: expected active ruleset id")
    teams = body.get("team_slugs")
    if (
        not isinstance(teams, list)
        or len(teams) < 4
        or any(not isinstance(item, str) or not item for item in teams)
        or len(set(teams)) != len(teams)
    ):
        errors.append(f"{label}.team_slugs: four distinct teams are required")
    for field in (
        "required_pull_request_contexts",
        "required_merge_group_contexts",
    ):
        contexts = body.get(field)
        if (
            not isinstance(contexts, list)
            or not contexts
            or any(not isinstance(item, str) or not item for item in contexts)
            or len(set(contexts)) != len(contexts)
        ):
            errors.append(f"{label}.{field}: expected unique non-empty contexts")
    digests = body.get("api_response_digests")
    if not isinstance(digests, dict) or not digests:
        errors.append(f"{label}.api_response_digests: live evidence is missing")
    else:
        for endpoint, digest in digests.items():
            if not isinstance(endpoint, str) or not endpoint.startswith("/"):
                errors.append(f"{label}.api_response_digests: unsafe endpoint")
            if not isinstance(digest, str) or FINGERPRINT.fullmatch(digest) is None:
                errors.append(f"{label}.api_response_digests: invalid digest")
    expected = _canonical_digest(body)
    if payload.get("receipt_sha256") != expected:
        errors.append(f"{label}: receipt digest mismatch")


_RECEIPT_VERIFIERS: dict[str, Callable[[dict[str, Any], list[str]], None]] = {
    "G-IB-001": _verify_ib_receipt,
    "G-TEAM-001": _verify_governance_receipt,
}


def _verify_external_receipt(
    gate_id: str,
    gate: dict[str, Any],
    *,
    repository_root: Path,
    errors: list[str],
) -> None:
    path = _safe_receipt_path(
        gate.get("qualification_receipt"),
        repository_root=repository_root,
        gate_id=gate_id,
        errors=errors,
    )
    if path is None:
        return
    payload = _load(path, f"external gate {gate_id} receipt", errors)
    if not payload:
        return
    verifier = _RECEIPT_VERIFIERS.get(gate_id)
    if verifier is None:
        errors.append(f"external gate {gate_id}: no receipt verifier is registered")
        return
    verifier(payload, errors)


def validate(
    gap_registry_path: Path = DEFAULT_GAP_REGISTRY,
    module_registry_path: Path = DEFAULT_MODULE_REGISTRY,
    repository_root: Path | None = None,
) -> list[str]:
    """Return deterministic validation errors for the exact repository tree."""
    errors: list[str] = []
    gap_doc = _load(gap_registry_path, "gap registry", errors)
    module_doc = _load(module_registry_path, "module registry", errors)
    if errors:
        return errors

    if gap_doc.get("schema") != "heptatrader.gap-registry.v2":
        errors.append("gap registry: schema mismatch")
    if module_doc.get("schema") != "heptatrader.module-registry.v2":
        errors.append("module registry: schema mismatch")

    allowed_states = gap_doc.get("allowed_states")
    if not isinstance(allowed_states, list) or not allowed_states:
        errors.append("gap registry: allowed_states must be a non-empty array")
        allowed_state_set: set[str] = set()
    else:
        allowed_state_set = {
            value for value in allowed_states if isinstance(value, str)
        }
        if len(allowed_state_set) != len(allowed_states):
            errors.append("gap registry: allowed_states must be unique strings")
    if "closed" not in allowed_state_set:
        errors.append("gap registry: closed state is required")

    policy = module_doc.get("implementation_evidence_policy")
    if not isinstance(policy, dict):
        errors.append("module registry: implementation_evidence_policy missing")
        policy = {}
    raw_external = policy.get("external_gate_ids")
    if not isinstance(raw_external, list):
        errors.append("module registry: external_gate_ids must be an array")
        external_gate_ids: set[str] = set()
    else:
        external_gate_ids = {
            value for value in raw_external if isinstance(value, str) and value
        }
        if len(external_gate_ids) != len(raw_external):
            errors.append(
                "module registry: external_gate_ids must be unique non-empty strings"
            )
    if policy.get("external_gates_fail_closed") is not True:
        errors.append("module registry: external_gates_fail_closed must be true")
    for gate_id in sorted(external_gate_ids):
        if gate_id not in _RECEIPT_VERIFIERS:
            errors.append(
                f"module registry: external gate has no receipt verifier: {gate_id}"
            )

    gaps = gap_doc.get("gaps")
    if not isinstance(gaps, list):
        errors.append("gap registry: gaps must be an array")
        return errors

    root = (
        repository_root.resolve()
        if repository_root is not None
        else _repository_root(module_registry_path)
    )
    by_id: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(gaps):
        label = f"gap registry: gaps[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{label} must be an object")
            continue
        gap_id = item.get("id")
        if not isinstance(gap_id, str) or not gap_id:
            errors.append(f"{label} id missing")
            continue
        if gap_id in by_id:
            errors.append(f"gap registry: duplicate gap id {gap_id}")
            continue
        by_id[gap_id] = item
        state = item.get("state")
        if state not in allowed_state_set:
            errors.append(f"gap {gap_id}: invalid state {state!r}")
            continue
        if state != "closed" and gap_id not in external_gate_ids:
            errors.append(
                f"gap {gap_id}: repository-executable gap must be closed; "
                "only registered external qualification gates may remain open"
            )

    for gate_id in sorted(external_gate_ids):
        gate = by_id.get(gate_id)
        if gate is None:
            errors.append(
                f"module registry: external gate is absent from gap registry: {gate_id}"
            )
            continue
        if gate.get("state") == "closed":
            _verify_external_receipt(
                gate_id, gate, repository_root=root, errors=errors
            )

    return errors


def summary(
    gap_registry_path: Path = DEFAULT_GAP_REGISTRY,
    module_registry_path: Path = DEFAULT_MODULE_REGISTRY,
) -> dict[str, Any]:
    gap_doc = json.loads(gap_registry_path.read_text(encoding="utf-8"))
    module_doc = json.loads(module_registry_path.read_text(encoding="utf-8"))
    policy = module_doc["implementation_evidence_policy"]
    external = set(policy["external_gate_ids"])
    gaps = gap_doc["gaps"]
    return {
        "schema": "heptatrader.gap-closure-summary.v2",
        "repository_executable_open": sorted(
            item["id"]
            for item in gaps
            if item["state"] != "closed" and item["id"] not in external
        ),
        "external_open": sorted(
            item["id"]
            for item in gaps
            if item["state"] != "closed" and item["id"] in external
        ),
        "external_closed_with_receipt": sorted(
            item["id"]
            for item in gaps
            if item["state"] == "closed" and item["id"] in external
        ),
        "closed": sorted(item["id"] for item in gaps if item["state"] == "closed"),
        "external_evidence_synthesized": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gap-registry", type=Path, default=DEFAULT_GAP_REGISTRY)
    parser.add_argument(
        "--module-registry", type=Path, default=DEFAULT_MODULE_REGISTRY
    )
    parser.add_argument("--repository-root", type=Path, default=ROOT)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    errors = validate(
        args.gap_registry,
        args.module_registry,
        repository_root=args.repository_root,
    )
    for error in errors:
        print(f"[GAP-CLOSURE] {error}", file=sys.stderr)
    if errors:
        return 1
    result = summary(args.gap_registry, args.module_registry)
    if args.json:
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    else:
        print(
            "[GAP-CLOSURE] PASS "
            f"repository_open={len(result['repository_executable_open'])} "
            f"external_open={','.join(result['external_open']) or 'none'} "
            "external_closed="
            f"{','.join(result['external_closed_with_receipt']) or 'none'}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
