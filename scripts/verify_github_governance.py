#!/usr/bin/env python3
"""Hardened entry point for live GitHub governance qualification.

The full, previously reviewed verifier is retained as an immutable sibling blob.
This entry point binds that exact blob, replaces its ruleset selector with a
fail-closed evaluator that proves the default branch is not excluded, and adds
the effective ref condition to the signed receipt body.
"""
from __future__ import annotations

import argparse
from fnmatch import fnmatchcase
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from typing import Any

LEGACY_BLOB_SHA = "d83b7c02f1981ddbfcdcc32c583356dc8a77b711"
LEGACY_PATH = Path(__file__).with_name("verify_github_governance_legacy.py")


def _git_blob_sha(path: Path) -> str:
    data = path.read_bytes()
    header = f"blob {len(data)}\0".encode("ascii")
    return hashlib.sha1(header + data).hexdigest()  # Git object identity.


def _load_legacy() -> Any:
    if not LEGACY_PATH.is_file() or LEGACY_PATH.is_symlink():
        raise RuntimeError("trusted governance legacy verifier must be a regular file")
    actual = _git_blob_sha(LEGACY_PATH)
    if actual != LEGACY_BLOB_SHA:
        raise RuntimeError(
            f"trusted governance legacy blob mismatch: {actual} != {LEGACY_BLOB_SHA}"
        )
    name = "_heptatrader_governance_legacy"
    spec = importlib.util.spec_from_file_location(name, LEGACY_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load trusted governance legacy verifier")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_legacy = _load_legacy()
for _name in dir(_legacy):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_legacy, _name)

_LAST_RULESET_SCOPE: dict[str, Any] | None = None


def _canonical_ref_patterns(
    value: Any,
    label: str,
    errors: list[str],
    *,
    allow_empty: bool,
) -> list[str] | None:
    if not isinstance(value, list):
        errors.append(f"{label}: expected array")
        return None
    if not value and not allow_empty:
        errors.append(f"{label}: must not be empty")
        return None
    result: list[str] = []
    for position, item in enumerate(value):
        if not isinstance(item, str) or not item:
            errors.append(f"{label}[{position}]: expected non-empty string")
        elif item in result:
            errors.append(f"{label}: duplicate ref pattern {item}")
        else:
            result.append(item)
    return result


def _ref_pattern_excludes_default(pattern: str, default_branch: str) -> bool:
    """Conservatively reject every exclusion that can remove the default ref."""
    full_ref = f"refs/heads/{default_branch}"
    if pattern in {"~ALL", "~DEFAULT_BRANCH", full_ref, default_branch}:
        return True
    if pattern == "~NON_DEFAULT_BRANCH":
        return False
    if pattern.startswith("~"):
        return True  # Unknown special selector: fail closed.
    return fnmatchcase(full_ref, pattern) or fnmatchcase(default_branch, pattern)


def _ruleset_ref_condition_projection(
    ruleset: dict[str, Any], default_branch: str
) -> dict[str, Any]:
    ref_name = ruleset["conditions"]["ref_name"]
    return {
        "effective_default_ref": f"refs/heads/{default_branch}",
        "include": sorted(ref_name["include"]),
        "exclude": sorted(ref_name["exclude"]),
        "default_branch_effectively_included": True,
    }


def _main_ruleset(
    policy: dict[str, Any], rulesets: Any, errors: list[str]
) -> dict[str, Any] | None:
    global _LAST_RULESET_SCOPE
    _LAST_RULESET_SCOPE = None
    if not isinstance(rulesets, list):
        errors.append("rulesets: expected array")
        return None
    expected = policy["ruleset"]
    default_branch = policy.get("default_branch")
    if not isinstance(default_branch, str) or not default_branch:
        errors.append("policy.default_branch: expected non-empty string")
        return None
    accepted_refs = set(expected["accepted_ref_includes"])
    candidates: list[dict[str, Any]] = []
    for position, item in enumerate(rulesets):
        if not isinstance(item, dict):
            errors.append(f"rulesets[{position}]: expected object")
            continue
        if (
            item.get("target") != expected["target"]
            or item.get("enforcement") != expected["enforcement"]
        ):
            continue
        ruleset_id = item.get("id", position)
        conditions = item.get("conditions")
        ref_name = conditions.get("ref_name") if isinstance(conditions, dict) else None
        if not isinstance(ref_name, dict):
            errors.append(f"ruleset {ruleset_id}: conditions.ref_name must be an object")
            continue
        includes = _canonical_ref_patterns(
            ref_name.get("include"),
            f"ruleset {ruleset_id}.conditions.ref_name.include",
            errors,
            allow_empty=False,
        )
        excludes = _canonical_ref_patterns(
            ref_name.get("exclude"),
            f"ruleset {ruleset_id}.conditions.ref_name.exclude",
            errors,
            allow_empty=True,
        )
        if includes is None or excludes is None:
            continue
        if not accepted_refs.intersection(includes):
            continue
        matching = sorted(
            pattern
            for pattern in excludes
            if _ref_pattern_excludes_default(pattern, default_branch)
        )
        if matching:
            errors.append(
                f"ruleset {ruleset_id}: ref condition excludes protected default branch "
                f"refs/heads/{default_branch}: {matching}"
            )
            continue
        candidates.append(item)
    if len(candidates) != 1:
        errors.append(
            "rulesets: expected exactly one active branch ruleset effectively targeting "
            f"main/default; found {len(candidates)}"
        )
        return None
    selected = candidates[0]
    _LAST_RULESET_SCOPE = _ruleset_ref_condition_projection(selected, default_branch)
    return selected


# Every legacy validation call resolves this hardened selector through its own
# module globals. No caller can reach the former include-only selector.
_legacy._main_ruleset = _main_ruleset


def _self_test_ruleset_scope() -> bool:
    policy = {
        "default_branch": "main",
        "ruleset": {
            "target": "branch",
            "enforcement": "active",
            "accepted_ref_includes": ["~DEFAULT_BRANCH", "refs/heads/main"],
        },
    }
    base = {
        "id": 42,
        "target": "branch",
        "enforcement": "active",
        "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}},
    }
    for pattern in (
        "refs/heads/main",
        "~DEFAULT_BRANCH",
        "~ALL",
        "main",
        "refs/heads/m*",
        "refs/heads/**",
        "**/main",
        "~UNKNOWN_SELECTOR",
    ):
        candidate = json.loads(json.dumps(base))
        candidate["conditions"]["ref_name"]["exclude"] = [pattern]
        errors: list[str] = []
        if _main_ruleset(policy, [candidate], errors) is not None:
            raise AssertionError(f"default-branch exclusion was accepted: {pattern}")
        if not any("excludes protected default branch" in error for error in errors):
            raise AssertionError(f"default-branch exclusion lacked a hard error: {pattern}")
    candidate = json.loads(json.dumps(base))
    candidate["conditions"]["ref_name"]["exclude"] = ["refs/heads/release/**"]
    errors = []
    if _main_ruleset(policy, [candidate], errors) is None or errors:
        raise AssertionError(f"nonmatching exclusion was rejected: {errors}")
    return True


# Existing governance unit tests import this module, so the hostile exclusion
# matrix is executed in every bootstrap audit even before an external dispatch.
RULESET_SCOPE_SELF_TEST_PASSED = _self_test_ruleset_scope()


def _rewrite_output_argument(argv: list[str], replacement: str) -> tuple[list[str], Path]:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--output", required=True, type=Path)
    known, _ = parser.parse_known_args(argv)
    rewritten = list(argv)
    index = rewritten.index("--output")
    rewritten[index + 1] = replacement
    return rewritten, known.output


def main(argv: list[str] | None = None) -> int:
    global _LAST_RULESET_SCOPE
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments == ["--self-test"]:
        _self_test_ruleset_scope()
        print("[GITHUB-GOVERNANCE-SCOPE] PASS")
        return 0
    with tempfile.TemporaryDirectory(prefix="hepta-governance-scope-") as directory:
        temporary_receipt = Path(directory) / "legacy-receipt.json"
        rewritten, output = _rewrite_output_argument(arguments, str(temporary_receipt))
        _LAST_RULESET_SCOPE = None
        result = _legacy.main(rewritten)
        if result != 0:
            return result
        scope = _LAST_RULESET_SCOPE
        if scope is None:
            print("[GITHUB-GOVERNANCE] effective ruleset scope was not retained", file=sys.stderr)
            return 1
        receipt = json.loads(
            temporary_receipt.read_text(encoding="utf-8"),
            object_pairs_hook=_legacy.strict_object_pairs,
        )
        body = receipt.get("body") if isinstance(receipt, dict) else None
        if not isinstance(body, dict):
            print("[GITHUB-GOVERNANCE] legacy receipt body is invalid", file=sys.stderr)
            return 1
        body["ruleset_ref_condition"] = scope
        body["ruleset_ref_condition_sha256"] = _legacy.canonical_digest(scope)
        receipt["receipt_sha256"] = _legacy.canonical_digest(body)
        _legacy._write_receipt(output, receipt)
    print("[GITHUB-GOVERNANCE-SCOPE] receipt bound to effective default-branch condition")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())