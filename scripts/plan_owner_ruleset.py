#!/usr/bin/env python3
"""Prepare, but never apply, the minimal owner-operated GitHub Ruleset update.

The reviewed result keeps deletion and force-push protection, removes any stale
approval/Merge Queue rules, and requires exactly one always-present canonical
admission check. That check verifies the applicable exact-head source/runtime/
sanitizer evidence without making documentation-only pull requests run unrelated
native suites. No credentials, network access, or Broker authority are involved.
"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import sys

from source_json import load_source_json

REPOSITORY = "TrillionniumFoundation/heptatrader"
RULESET_ID = 22597364
ADMISSION_CHECK = "canonical-admission-exact-head"
INTEGRATION_ID = 15368
FIELDS = ("name", "target", "enforcement", "conditions", "rules", "bypass_actors")
STATUS_RULE = {
    "type": "required_status_checks",
    "parameters": {
        "strict_required_status_checks_policy": True,
        "do_not_enforce_on_create": False,
        "required_status_checks": [
            {"context": ADMISSION_CHECK, "integration_id": INTEGRATION_ID}
        ],
    },
}


def _validate_identity(observed: dict) -> list[dict]:
    if not isinstance(observed, dict):
        raise ValueError("observed ruleset must be an object")
    if (
        observed.get("id") != RULESET_ID
        or observed.get("source") != REPOSITORY
        or observed.get("target") != "branch"
        or observed.get("enforcement") != "active"
        or observed.get("conditions")
        != {"ref_name": {"exclude": [], "include": ["~DEFAULT_BRANCH"]}}
        or observed.get("bypass_actors") != []
    ):
        raise ValueError("unexpected ruleset identity, scope, enforcement or bypass actors; review new state")
    rules = observed.get("rules")
    if not isinstance(rules, list) or not all(
        isinstance(rule, dict) and isinstance(rule.get("type"), str) for rule in rules
    ):
        raise ValueError("rules must be typed objects")
    if len({rule["type"] for rule in rules}) != len(rules):
        raise ValueError("duplicate rule types")
    types = {rule["type"] for rule in rules}
    if not {"deletion", "non_fast_forward"}.issubset(types):
        raise ValueError("baseline deletion/non-fast-forward protections are missing")
    return rules


def plan(observed: dict) -> dict:
    rules = _validate_identity(observed)
    result = {key: copy.deepcopy(observed[key]) for key in FIELDS}
    retained = [
        copy.deepcopy(rule)
        for rule in rules
        if rule["type"] not in {"pull_request", "merge_queue", "required_status_checks"}
    ]
    retained.append(copy.deepcopy(STATUS_RULE))
    result["rules"] = retained
    return result


def verify_applied(observed: dict, actual: dict) -> bool:
    if not isinstance(actual, dict):
        return False
    try:
        expected = plan(observed)
    except (ValueError, KeyError, TypeError):
        return False
    return (
        actual.get("id") == RULESET_ID
        and actual.get("source") == REPOSITORY
        and {key: actual.get(key) for key in FIELDS} == expected
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--observed", type=Path, required=True)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--output", type=Path)
    group.add_argument("--verify-after", type=Path)
    args = parser.parse_args()
    try:
        before = load_source_json(args.observed)
        payload = plan(before)
        if args.verify_after:
            after = load_source_json(args.verify_after)
            if not verify_applied(before, after):
                raise ValueError("server readback differs from reviewed transition")
            print("Ruleset readback matches canonical admission; this grants no Broker authority")
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
