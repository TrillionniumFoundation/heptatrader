#!/usr/bin/env python3
"""Build the exact role map consumed by the engineering-closure builder."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


SCRIPT_DIRECTORY = Path(__file__).resolve(strict=True).parent
sys.path.insert(0, str(SCRIPT_DIRECTORY))

import build_heptatrader_engineering_closure as closure  # noqa: E402


def _artifact(value: str) -> dict[str, str]:
    if "=" not in value:
        raise closure.EngineeringClosureError(
            "artifact binding must be ROLE=PATH")
    role, relative = value.split("=", 1)
    if role not in closure.REQUIRED_ROLES:
        raise closure.EngineeringClosureError(
            f"unsupported engineering artifact role: {role}")
    return {
        "role": role,
        "path": closure._relative(relative, f"{role} path"),
    }


def build(
    round_number: int,
    release_version: str,
    git_head: str,
    values: list[str],
) -> dict:
    if (type(round_number) is not int or round_number <= 0 or
            closure.RELEASE.fullmatch(release_version) is None or
            not release_version.endswith(f"-round{round_number}") or
            closure.HEX40.fullmatch(git_head) is None):
        raise closure.EngineeringClosureError(
            "engineering artifact map identity is invalid")
    records = [_artifact(value) for value in values]
    roles = [record["role"] for record in records]
    if (len(roles) != len(set(roles)) or
            set(roles) != set(closure.REQUIRED_ROLES)):
        raise closure.EngineeringClosureError(
            "engineering artifact role closure is invalid")
    records.sort(key=lambda record: record["role"])
    return {
        "schema": closure.MAP_SCHEMA,
        "version": 2,
        "round": round_number,
        "release_version": release_version,
        "git_head": git_head,
        "artifacts": records,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--round", dest="round_number", type=int, required=True)
    parser.add_argument("--release-version", required=True)
    parser.add_argument(
        "--product-git-head", "--git-head",
        dest="product_git_head", required=True,
        help="Product commit P recorded by source artifacts and baseline")
    parser.add_argument("--artifact", action="append", default=[])
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    document = build(
        arguments.round_number, arguments.release_version,
        arguments.product_git_head, arguments.artifact)
    closure.write_private(arguments.output, document)
    print(
        f"PASS: {closure.MAP_SCHEMA} artifacts={len(document['artifacts'])}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except closure.EngineeringClosureError as error:
        print(f"FAIL: {error}", file=sys.stderr)
        raise SystemExit(1)
