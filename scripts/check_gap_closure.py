#!/usr/bin/env python3
"""Check repository gaps and the integrity/binding of external receipt envelopes.

A PASS here is NOT external qualification: only the protected live verifier may
establish that. Receipt syntax, hashes and caller-supplied identity alone do not
prove issuer provenance. Never use this command to enable PAPER or LIVE.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
from typing import Any

from receipt_file_boundary import decode_object, read_receipt

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GAP_REGISTRY = ROOT / "docs/program/gap-registry-v2.json"
DEFAULT_MODULE_REGISTRY = ROOT / "docs/modules/module-registry-v2.json"
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
FINGERPRINT = re.compile(r"^sha256:[0-9a-f]{64}$")
IB_SCENARIOS = {
    "connect_authoritative_snapshot", "disconnect_reconnect", "partial_fill",
    "duplicate_out_of_order_status", "broker_reject", "stale_quote",
    "outcome_uncertain", "cancel_race", "reconcile_divergence", "lease_fencing",
    "kill_switch", "terminal_recovery",
}
EXTERNAL_GATES = {"G-IB-001", "G-TEAM-001"}


def _load(path: Path, label: str, errors: list[str]) -> dict[str, Any]:
    try:
        return decode_object(path.read_bytes())
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        errors.append(f"{label}: cannot load {path}: {exc}")
        return {}


def _repository_root(path: Path) -> Path:
    path = path.resolve()
    if path.name == "module-registry-v2.json" and path.parent.name == "modules" and path.parent.parent.name == "docs":
        return path.parents[2]
    return path.parent


def _matches(value: object, pattern: re.Pattern[str]) -> bool:
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def _positive(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _strings(value: object) -> bool:
    return (isinstance(value, list) and bool(value)
            and all(isinstance(v, str) and bool(v.strip()) for v in value)
            and len(value) == len(set(value)))


def _canonical_digest(value: Any) -> str:
    data = json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _verify_ib_receipt(payload: dict[str, Any], errors: list[str]) -> None:
    label = "G-IB-001 receipt"
    if payload.get("schema") != "hepta.ib-paper-qualification-verification.v1":
        errors.append(f"{label}: schema mismatch")
    if payload.get("verified") is not True or payload.get("qualified") is not True:
        errors.append(f"{label}: verified and qualified must both be true")
    if not _matches(payload.get("git_sha"), FULL_SHA):
        errors.append(f"{label}: git_sha is not canonical")
    if not _matches(payload.get("result_sha256"), SHA256):
        errors.append(f"{label}.result_sha256: expected canonical sha256 hex")
    for field in ("binary", "harness"):
        value = payload.get(field)
        if not isinstance(value, dict):
            errors.append(f"{label}.{field}: expected object")
            continue
        if not isinstance(value.get("name"), str) or not value["name"]:
            errors.append(f"{label}.{field}.name: missing")
        if not _matches(value.get("sha256"), SHA256):
            errors.append(f"{label}.{field}.sha256: expected canonical sha256 hex")
    broker = payload.get("broker")
    if not isinstance(broker, dict):
        errors.append(f"{label}.broker: expected object")
    else:
        if broker.get("venue") != "IB" or broker.get("environment") != "PAPER":
            errors.append(f"{label}.broker: must bind IB PAPER")
        if not isinstance(broker.get("session_id"), str) or not broker["session_id"]:
            errors.append(f"{label}.broker.session_id: missing")
        for field in ("account_fingerprint", "host_fingerprint"):
            if not _matches(broker.get(field), FINGERPRINT):
                errors.append(f"{label}.broker.{field}: expected canonical fingerprint")
    scenarios = payload.get("scenarios")
    if not isinstance(scenarios, list):
        errors.append(f"{label}.scenarios: expected array")
        return
    observed: set[str] = set()
    for index, scenario in enumerate(scenarios):
        prefix = f"{label}.scenarios[{index}]"
        if not isinstance(scenario, dict):
            errors.append(f"{prefix}: expected object")
            continue
        scenario_id = scenario.get("id")
        if not isinstance(scenario_id, str) or scenario_id in observed:
            errors.append(f"{prefix}.id: invalid or duplicate")
            continue
        observed.add(scenario_id)
        if scenario.get("status") != "PASS":
            errors.append(f"{prefix}: status must be PASS")
        evidence = scenario.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            errors.append(f"{prefix}: evidence is missing")
            continue
        paths: set[str] = set()
        for item in evidence:
            if not isinstance(item, dict):
                errors.append(f"{prefix}: evidence entry must be an object")
                continue
            raw_path = item.get("path")
            path = PurePosixPath(raw_path) if isinstance(raw_path, str) else None
            if (path is None or not path.parts or path.is_absolute() or ".." in path.parts
                    or path.as_posix() != raw_path or "\\" in raw_path or "\x00" in raw_path
                    or raw_path in paths):
                errors.append(f"{prefix}: evidence path is unsafe or duplicate")
            else:
                paths.add(raw_path)
            if not _matches(item.get("sha256"), SHA256) or not _positive(item.get("size")):
                errors.append(f"{prefix}: evidence digest/size invalid")
            if not isinstance(item.get("kind"), str) or not item["kind"]:
                errors.append(f"{prefix}: evidence kind missing")
    if observed != IB_SCENARIOS:
        errors.append(f"{label}: scenario set does not match protected verifier")


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
        if not _matches(body.get(field), FULL_SHA):
            errors.append(f"{label}.{field}: expected canonical full SHA")
    for field in ("pull_number", "ruleset_id"):
        if not _positive(body.get(field)):
            errors.append(f"{label}.{field}: expected positive integer")
    teams = body.get("team_slugs")
    if not _strings(teams) or len(teams) < 4:
        errors.append(f"{label}.team_slugs: four distinct teams are required")
    for field in ("required_pull_request_contexts", "required_merge_group_contexts"):
        if not _strings(body.get(field)):
            errors.append(f"{label}.{field}: expected unique non-empty contexts")
    if body.get("required_pull_request_contexts") != body.get("required_merge_group_contexts"):
        errors.append(f"{label}: PR and merge-group contexts must be identical")
    digests = body.get("api_response_digests")
    if not isinstance(digests, dict) or not digests:
        errors.append(f"{label}.api_response_digests: live evidence is missing")
    else:
        for endpoint, digest in digests.items():
            if not isinstance(endpoint, str) or not endpoint.startswith("/"):
                errors.append(f"{label}.api_response_digests: unsafe endpoint")
            if not _matches(digest, FINGERPRINT):
                errors.append(f"{label}.api_response_digests: invalid digest")
    try:
        if payload.get("receipt_sha256") != _canonical_digest(body):
            errors.append(f"{label}: receipt digest mismatch")
    except (ValueError, TypeError, RecursionError) as exc:
        errors.append(f"{label}: non-canonical digest input: {exc}")


def _source_identity(root: Path, expected: str | None, errors: list[str]) -> str | None:
    if expected is not None and not _matches(expected, FULL_SHA):
        errors.append("receipt binding: expected source SHA must be canonical")
        return None
    # Inherited GIT_DIR/WORK_TREE/config overrides must not redirect identity.
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    def git(*arguments: str) -> str:
        result = subprocess.run(["git", "--no-optional-locks", "-c", "core.fsmonitor=false",
                                 "-C", str(root), *arguments], env=environment,
                                check=True, capture_output=True, text=True, timeout=5)
        return result.stdout.strip()
    try:
        actual = git("rev-parse", "--verify", "HEAD^{commit}")
        if Path(git("rev-parse", "--show-toplevel")).resolve() != root.resolve():
            errors.append("receipt binding: repository root is not the candidate checkout")
            return None
        if expected and actual != expected:
            errors.append("receipt binding: expected source SHA does not match repository HEAD")
            return None
        # Porcelain status can deliberately omit tracked changes hidden by
        # assume-unchanged or skip-worktree. Qualification input must be a full,
        # inspectable checkout; do not mutate index flags to make it pass.
        indexed = git("ls-files", "--cached", "-v", "-z")
        if ((indexed and not indexed.endswith("\0")) or
                any(record and not record.startswith("H ") for record in indexed.split("\0"))):
            errors.append("receipt binding: candidate index hides or incompletely stages tracked files")
            return None
        if git("status", "--porcelain", "--untracked-files=normal"):
            errors.append("receipt binding: candidate worktree is not clean")
            return None
    except (OSError, subprocess.SubprocessError):
        if (root / ".git").exists() or (root / ".git").is_symlink():
            errors.append("receipt binding: Git metadata unreadable; cannot treat checkout as archive")
            return None
        actual = None
    if actual and not _matches(actual, FULL_SHA):
        errors.append("receipt binding: repository HEAD is invalid")
        return None
    if actual and expected and actual != expected:
        errors.append("receipt binding: expected source SHA does not match repository HEAD")
        return None
    if not actual and expected is None:
        errors.append("receipt binding: closed external gate requires exact source identity")
    return expected or actual


def _verify_external_receipt(gate_id: str, gate: dict[str, Any], *, repository_root: Path,
                             source_sha: str | None, merge_group_sha: str | None,
                             pull_number: int | None, receipt_root: Path, errors: list[str]) -> None:
    try:
        payload = read_receipt(receipt_root, gate.get("qualification_receipt"))
    except (ValueError, UnicodeError, RecursionError, OSError) as exc:
        errors.append(f"external gate {gate_id}: {exc}")
        return
    if gate_id == "G-IB-001":
        _verify_ib_receipt(payload, errors)
        if payload.get("git_sha") != source_sha:
            errors.append("G-IB-001 receipt: source SHA binding mismatch")
        return
    _verify_governance_receipt(payload, errors)
    body = payload.get("body")
    if not isinstance(body, dict):
        return
    if body.get("head_sha") != source_sha:
        errors.append("G-TEAM-001 receipt: source SHA binding mismatch")
    if not _matches(merge_group_sha, FULL_SHA) or body.get("merge_group_sha") != merge_group_sha:
        errors.append("G-TEAM-001 receipt: exact merge-group SHA binding required")
    if not _positive(pull_number) or body.get("pull_number") != pull_number:
        errors.append("G-TEAM-001 receipt: exact pull-number binding required")
    policy = _load(repository_root / ".github/required-check-contexts-v1.json", "required contexts", errors)
    contexts = policy.get("required_branch_contexts")
    if not _strings(contexts):
        errors.append("required contexts: canonical required_branch_contexts missing")
    else:
        for field in ("required_pull_request_contexts", "required_merge_group_contexts"):
            if body.get(field) != contexts:
                errors.append(f"G-TEAM-001 receipt: {field} differs from canonical policy")


def evaluate(gap_registry_path: Path = DEFAULT_GAP_REGISTRY,
             module_registry_path: Path = DEFAULT_MODULE_REGISTRY,
             repository_root: Path | None = None, *, expected_source_sha: str | None = None,
             expected_merge_group_sha: str | None = None, expected_pull_number: int | None = None,
             receipt_root: Path | None = None) -> tuple[list[str], dict[str, Any] | None]:
    """Validate and project the same private input snapshot; never reopen to report.

    Errors have no successful projection. This is an integrity observation of
    supplied registries/receipts, not issuer authentication or a deployment lease.
    """
    errors: list[str] = []
    gap_doc = _load(gap_registry_path, "gap registry", errors)
    module_doc = _load(module_registry_path, "module registry", errors)
    if errors:
        return errors, None
    if gap_doc.get("schema") != "heptatrader.gap-registry.v2":
        errors.append("gap registry: schema mismatch")
    if module_doc.get("schema") != "heptatrader.module-registry.v2":
        errors.append("module registry: schema mismatch")
    allowed = gap_doc.get("allowed_states")
    if not _strings(allowed) or "closed" not in allowed:
        errors.append("gap registry: allowed_states must be unique non-empty strings including closed")
        allowed = []
    policy = module_doc.get("implementation_evidence_policy")
    if not isinstance(policy, dict):
        errors.append("module registry: implementation_evidence_policy missing")
        policy = {}
    external = policy.get("external_gate_ids")
    if not _strings(external):
        errors.append("module registry: external_gate_ids must be unique non-empty strings")
        external = []
    if policy.get("external_gates_fail_closed") is not True:
        errors.append("module registry: external_gates_fail_closed must be true")
    for gate_id in external:
        if gate_id not in EXTERNAL_GATES:
            errors.append(f"module registry: external gate has no receipt verifier: {gate_id}")
    # Removing a protected gate from the registry cannot downgrade it to ordinary prose.
    for gate_id in sorted(EXTERNAL_GATES - set(external)):
        errors.append(f"module registry: protected external gate missing from policy: {gate_id}")
    gaps = gap_doc.get("gaps")
    if not isinstance(gaps, list):
        return errors + ["gap registry: gaps must be an array"], None
    root = repository_root.resolve() if repository_root is not None else _repository_root(module_registry_path)
    by_id: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(gaps):
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"]:
            errors.append(f"gap registry: gaps[{index}] id missing or entry is not an object")
            continue
        gap_id = item["id"]
        if gap_id in by_id:
            errors.append(f"gap registry: duplicate gap id {gap_id}")
            continue
        by_id[gap_id] = item
        state = item.get("state")
        if not isinstance(state, str) or state not in allowed:
            errors.append(f"gap {gap_id}: invalid state {state!r}")
        elif state != "closed" and gap_id not in external:
            errors.append(f"gap {gap_id}: repository-executable gap must be closed; only registered external qualification gates may remain open")
    closed_external = any(by_id.get(g, {}).get("state") == "closed" for g in external)
    source_sha = _source_identity(root, expected_source_sha, errors) if closed_external else None
    git_bound = closed_external and ((root / ".git").exists() or (root / ".git").is_symlink())
    for gate_id in external:
        gate = by_id.get(gate_id)
        if gate is None:
            errors.append(f"module registry: external gate is absent from gap registry: {gate_id}")
        elif gate.get("state") == "closed" and gate_id in EXTERNAL_GATES:
            _verify_external_receipt(gate_id, gate, repository_root=root, source_sha=source_sha,
                                     merge_group_sha=expected_merge_group_sha,
                                     pull_number=expected_pull_number, receipt_root=receipt_root or root, errors=errors)
    # Receipt reads and policy validation must not leave a stale source check.
    # Re-admit the initial source, never select a newly moved HEAD implicitly.
    if closed_external and source_sha is not None and not errors:
        if git_bound and not ((root / ".git").exists() or (root / ".git").is_symlink()):
            errors.append("receipt binding: candidate Git metadata disappeared during validation")
        elif _source_identity(root, source_sha, errors) != source_sha:
            errors.append("receipt binding: candidate source changed during validation")
    if errors:
        return errors, None
    return [], _summary_from_documents(gap_doc, module_doc)


def _summary_from_documents(gap_doc: dict[str, Any], module_doc: dict[str, Any]) -> dict[str, Any]:
    # Only evaluate's validated private documents reach this projection helper.
    external = set(module_doc["implementation_evidence_policy"]["external_gate_ids"])
    gaps = gap_doc["gaps"]
    return {
        "schema": "heptatrader.gap-closure-summary.v2",
        "repository_executable_open": sorted(g["id"] for g in gaps if g["state"] != "closed" and g["id"] not in external),
        "external_open": sorted(g["id"] for g in gaps if g["state"] != "closed" and g["id"] in external),
        "external_closed_with_receipt": sorted(g["id"] for g in gaps if g["state"] == "closed" and g["id"] in external),
        "closed": sorted(g["id"] for g in gaps if g["state"] == "closed"),
        "external_evidence_synthesized": False,
        "receipt_validation_scope": "integrity-and-binding-only-not-issuer-provenance",
        "grants_qualification": False,
    }


def validate(gap_registry_path: Path = DEFAULT_GAP_REGISTRY,
             module_registry_path: Path = DEFAULT_MODULE_REGISTRY,
             repository_root: Path | None = None, *, expected_source_sha: str | None = None,
             expected_merge_group_sha: str | None = None, expected_pull_number: int | None = None,
             receipt_root: Path | None = None) -> list[str]:
    errors, _ = evaluate(gap_registry_path, module_registry_path, repository_root,
                         expected_source_sha=expected_source_sha,
                         expected_merge_group_sha=expected_merge_group_sha,
                         expected_pull_number=expected_pull_number, receipt_root=receipt_root)
    return errors


def summary(gap_registry_path: Path = DEFAULT_GAP_REGISTRY,
            module_registry_path: Path = DEFAULT_MODULE_REGISTRY,
            repository_root: Path | None = None, *, expected_source_sha: str | None = None,
            expected_merge_group_sha: str | None = None, expected_pull_number: int | None = None,
            receipt_root: Path | None = None) -> dict[str, Any]:
    """Return a newly validated projection, or raise ValueError on any rejection.

    A prior validate call conveys no cached approval to a subsequent summary.
    Closed external gates require the same independently selected identity inputs.
    """
    errors, result = evaluate(gap_registry_path, module_registry_path, repository_root,
                             expected_source_sha=expected_source_sha,
                             expected_merge_group_sha=expected_merge_group_sha,
                             expected_pull_number=expected_pull_number, receipt_root=receipt_root)
    if errors or result is None:
        raise ValueError("; ".join(errors) or "gap closure produced no validated projection")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gap-registry", type=Path, default=DEFAULT_GAP_REGISTRY)
    parser.add_argument("--module-registry", type=Path, default=DEFAULT_MODULE_REGISTRY)
    parser.add_argument("--repository-root", type=Path, default=ROOT)
    parser.add_argument("--receipt-root", type=Path, help="explicit detached evidence root; defaults to repository root")
    parser.add_argument("--expected-source-sha")
    parser.add_argument("--expected-merge-group-sha")
    parser.add_argument("--expected-pull-number", type=int)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    errors, result = evaluate(args.gap_registry, args.module_registry, repository_root=args.repository_root,
                      expected_source_sha=args.expected_source_sha,
                      expected_merge_group_sha=args.expected_merge_group_sha,
                      expected_pull_number=args.expected_pull_number, receipt_root=args.receipt_root)
    for error in errors:
        print(f"[GAP-CLOSURE] {error}", file=sys.stderr)
    if errors or result is None:
        return 1
    if args.json:
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    else:
        print(f"[GAP-CLOSURE] PASS repository_open={len(result['repository_executable_open'])} "
              f"external_open={','.join(result['external_open']) or 'none'} "
              f"external_closed={','.join(result['external_closed_with_receipt']) or 'none'} "
              "qualification_granted=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
