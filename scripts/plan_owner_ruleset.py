#!/usr/bin/env python3
"""Prepare, but never apply, the owner-operated GitHub Ruleset transition.

Only PR approval and Merge Queue requirements are removed. All other rules,
including the four real status checks, deletion and force-push protection,
remain byte-for-byte equivalent after JSON parsing. No credentials or network.
"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import sys

REPOSITORY = "TrillionniumFoundation/heptatrader"
RULESET_ID = 22597364
REQUIRED_CHECKS = frozenset({"documentation-control-plane-exact-head", "core-runtime-exact-head",
                           "canonical-full-suite-reliability (g++)", "canonical-full-suite-reliability (clang++)"})
FIELDS = ("name", "target", "enforcement", "conditions", "rules", "bypass_actors")


def strict_object(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def plan(observed: dict) -> dict:
    if (observed.get("id") != RULESET_ID or observed.get("source") != REPOSITORY
            or observed.get("target") != "branch" or observed.get("enforcement") != "active"
            or observed.get("conditions") != {"ref_name": {"exclude": [], "include": ["~DEFAULT_BRANCH"]}}
            or observed.get("bypass_actors") != []):
        raise ValueError("unexpected ruleset identity, scope, enforcement or bypass actors; review new state")
    rules = observed.get("rules")
    if not isinstance(rules, list) or not all(isinstance(r, dict) and isinstance(r.get("type"), str) for r in rules):
        raise ValueError("rules must be typed objects")
    if len({r["type"] for r in rules}) != len(rules):
        raise ValueError("duplicate rule types")
    indexed = {r["type"]: r for r in rules}
    if not {"deletion", "non_fast_forward", "required_status_checks"}.issubset(indexed):
        raise ValueError("baseline source protections are missing")
    parameters = indexed["required_status_checks"].get("parameters", {})
    if not isinstance(parameters, dict):
        raise ValueError("status check parameters must be an object")
    checks = parameters.get("required_status_checks", [])
    if (not isinstance(checks, list) or not all(isinstance(c, dict)
            and isinstance(c.get("context"), str) for c in checks)):
        raise ValueError("status checks must be typed objects")
    if (not REQUIRED_CHECKS.issubset({c.get("context") for c in checks})
            or any(c.get("integration_id") != 15368 for c in checks if c.get("context") in REQUIRED_CHECKS)):
        raise ValueError("real GitHub Actions checks are missing or differently bound")
    result = {key: copy.deepcopy(observed[key]) for key in FIELDS}
    result["rules"] = [r for r in result["rules"] if r["type"] not in {"pull_request", "merge_queue"}]
    return result


def verify_applied(observed: dict, actual: dict) -> bool:
    expected = plan(observed)
    # Ignore REST metadata, never ignore any writable field or added bypass.
    return (actual.get("id") == RULESET_ID and actual.get("source") == REPOSITORY
            and {key: actual.get(key) for key in FIELDS} == expected)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--observed", type=Path, required=True)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--output", type=Path)
    group.add_argument("--verify-after", type=Path)
    args = parser.parse_args()
    try:
        before = json.loads(args.observed.read_text(), object_pairs_hook=strict_object)
        payload = plan(before)
        if args.verify_after:
            after = json.loads(args.verify_after.read_text(), object_pairs_hook=strict_object)
            if not verify_applied(before, after):
                raise ValueError("server readback differs from reviewed transition")
            print("Ruleset readback matches; this grants no Broker authority")
        else:
            with args.output.open("x") as stream:
                json.dump(payload, stream, indent=2, sort_keys=True)
                stream.write("\n")
            print("Plan only: no server setting was changed")
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"ruleset plan rejected: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
