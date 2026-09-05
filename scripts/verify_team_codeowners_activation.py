#!/usr/bin/env python3
"""Verify the static team-only CODEOWNERS activation contract.

This verifier intentionally proves only checked-in bytes and policy consistency.
Live team existence, membership, maintainer, visibility and repository permission
remain external facts for the trusted governance verifier.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import shlex
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MAPPING_REL = Path(".github/github-team-mapping-v1.json")
POLICY_REL = Path(".github/github-governance-policy-v1.json")
TEMPLATE_REL = Path(".github/CODEOWNERS.team-template")
ACTIVE_REL = Path(".github/CODEOWNERS")
EXPECTED_REPOSITORY = "TrillionniumFoundation/heptatrader"
EXPECTED_ORGANIZATION = "TrillionniumFoundation"
EXPECTED_DEFAULT_BRANCH = "main"
MINIMUM_DISTINCT_TEAMS_FLOOR = 4
TEAM_SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,98}[a-z0-9])?$")
LOGICAL_HANDLE_RE = re.compile(r"^@hepta/[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
TEAM_OWNER_RE = re.compile(r"^@([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)$")


class ActivationError(ValueError):
    """Raised for malformed checked-in activation inputs."""


def _strict_object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ActivationError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _read_regular(
    root: Path, relative: Path, errors: list[str]
) -> bytes | None:
    path = root / relative
    try:
        info = path.lstat()
        if path.is_symlink() or not path.is_file() or info.st_nlink != 1:
            errors.append(f"{relative}: must be a regular single-link file")
            return None
        data = path.read_bytes()
        if not data:
            errors.append(f"{relative}: must not be empty")
            return None
        return data
    except OSError as exc:
        errors.append(f"{relative}: unreadable: {exc}")
        return None


def _decode_utf8(
    data: bytes, relative: Path, errors: list[str]
) -> str | None:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        errors.append(f"{relative}: invalid UTF-8: {exc}")
        return None


def _reject_nonfinite_json(token: str) -> Any:
    raise ActivationError(f"non-finite JSON constant: {token}")


def _load_json(
    root: Path, relative: Path, errors: list[str]
) -> dict[str, Any] | None:
    data = _read_regular(root, relative, errors)
    if data is None:
        return None
    text = _decode_utf8(data, relative, errors)
    if text is None:
        return None
    try:
        document = json.loads(
            text,
            object_pairs_hook=_strict_object_pairs,
            parse_constant=_reject_nonfinite_json,
        )
    except (ValueError, json.JSONDecodeError) as exc:
        errors.append(f"{relative}: invalid JSON: {exc}")
        return None
    if not isinstance(document, dict):
        errors.append(f"{relative}: top-level JSON value must be an object")
        return None
    return document


def _unique_strings(
    value: Any,
    label: str,
    errors: list[str],
    *,
    allow_empty: bool = False,
) -> list[str]:
    if not isinstance(value, list):
        errors.append(f"{label}: expected array")
        return []
    if not value and not allow_empty:
        errors.append(f"{label}: must not be empty")
        return []
    result: list[str] = []
    for position, item in enumerate(value):
        if not isinstance(item, str) or not item:
            errors.append(f"{label}[{position}]: expected non-empty string")
        elif item in result:
            errors.append(f"{label}: duplicate value {item}")
        else:
            result.append(item)
    return result


def _team_index(
    mapping: dict[str, Any], errors: list[str]
) -> tuple[str, list[str], dict[str, str]]:
    organization = mapping.get("organization")
    if not isinstance(organization, str) or not organization:
        errors.append(f"{MAPPING_REL}: organization must be non-empty")
        organization = ""
    elif organization != EXPECTED_ORGANIZATION:
        errors.append(
            f"{MAPPING_REL}: organization must be {EXPECTED_ORGANIZATION}"
        )
    raw_teams = mapping.get("teams")
    if not isinstance(raw_teams, list) or not raw_teams:
        errors.append(f"{MAPPING_REL}: teams must be a non-empty array")
        return organization, [], {}
    slugs: list[str] = []
    handles: dict[str, str] = {}
    for position, item in enumerate(raw_teams):
        label = f"{MAPPING_REL}.teams[{position}]"
        if not isinstance(item, dict):
            errors.append(f"{label}: expected object")
            continue
        slug = item.get("slug")
        if not isinstance(slug, str) or not TEAM_SLUG_RE.fullmatch(slug):
            errors.append(f"{label}.slug: invalid GitHub team slug")
            continue
        if slug in slugs:
            errors.append(f"{MAPPING_REL}: duplicate team slug {slug}")
            continue
        slugs.append(slug)
        logical_handles = _unique_strings(
            item.get("logical_handles"), f"{label}.logical_handles", errors
        )
        for handle in logical_handles:
            if not LOGICAL_HANDLE_RE.fullmatch(handle):
                errors.append(f"{label}: invalid logical handle {handle}")
            elif handle in handles:
                errors.append(
                    f"{MAPPING_REL}: logical handle {handle} is mapped by both "
                    f"{handles[handle]} and {slug}"
                )
            else:
                handles[handle] = slug
    return organization, slugs, handles


def _render_template(
    mapping: dict[str, Any],
    organization: str,
    team_slugs: list[str],
    errors: list[str],
) -> tuple[str, list[str]]:
    raw_rules = mapping.get("codeowners_rules")
    if not isinstance(raw_rules, list) or not raw_rules:
        errors.append(f"{MAPPING_REL}: codeowners_rules must be a non-empty array")
        return "", []
    lines = [
        "# Generated from .github/github-team-mapping-v1.json.",
        "# This template is not active until the named organization teams exist and an",
        "# independently reviewed change replaces .github/CODEOWNERS with these bytes.",
    ]
    patterns: list[str] = []
    known = set(team_slugs)
    used_teams: set[str] = set()
    for position, item in enumerate(raw_rules):
        label = f"{MAPPING_REL}.codeowners_rules[{position}]"
        if not isinstance(item, dict):
            errors.append(f"{label}: expected object")
            continue
        pattern = item.get("pattern")
        if (
            not isinstance(pattern, str)
            or not pattern
            or any(character.isspace() for character in pattern)
        ):
            errors.append(f"{label}.pattern: invalid CODEOWNERS pattern")
            continue
        if pattern in patterns:
            errors.append(f"{MAPPING_REL}: duplicate CODEOWNERS pattern {pattern}")
            continue
        patterns.append(pattern)
        owners = _unique_strings(item.get("teams"), f"{label}.teams", errors)
        if len(owners) < 2:
            errors.append(f"{label}: requires at least two independent teams")
        rendered: list[str] = []
        for slug in owners:
            if slug not in known:
                errors.append(f"{label}: unknown team slug {slug}")
            else:
                rendered.append(f"@{organization}/{slug}")
                used_teams.add(slug)
        if rendered:
            lines.append(pattern + " " + " ".join(rendered))
    for slug in sorted(known - used_teams):
        errors.append(f"{MAPPING_REL}: team {slug} has no CODEOWNERS rule")
    return "\n".join(lines) + "\n", patterns


def _parse_active_codeowners(
    text: str,
    organization: str,
    known_slugs: set[str],
    errors: list[str],
) -> list[str]:
    patterns: list[str] = []
    for line_number, raw in enumerate(text.splitlines(), start=1):
        try:
            tokens = shlex.split(raw, comments=True, posix=True)
        except ValueError as exc:
            errors.append(f"{ACTIVE_REL}:{line_number}: invalid syntax: {exc}")
            continue
        if not tokens:
            continue
        if len(tokens) < 2:
            errors.append(f"{ACTIVE_REL}:{line_number}: rule has no owner")
            continue
        pattern, owners = tokens[0], tokens[1:]
        if pattern in patterns:
            errors.append(f"{ACTIVE_REL}:{line_number}: duplicate exact pattern {pattern}")
            continue
        patterns.append(pattern)
        accepted: list[str] = []
        for owner in owners:
            match = TEAM_OWNER_RE.fullmatch(owner)
            if match is None:
                errors.append(
                    f"{ACTIVE_REL}:{line_number}: owner must be an organization team: {owner}"
                )
                continue
            owner_org, slug = match.groups()
            if owner_org.lower() != organization.lower():
                errors.append(
                    f"{ACTIVE_REL}:{line_number}: owner organization mismatch: {owner}"
                )
            elif slug not in known_slugs:
                errors.append(
                    f"{ACTIVE_REL}:{line_number}: unknown mapped team owner: {owner}"
                )
            elif owner in accepted:
                errors.append(
                    f"{ACTIVE_REL}:{line_number}: duplicate owner on rule: {owner}"
                )
            else:
                accepted.append(owner)
        if len(accepted) < 2:
            errors.append(
                f"{ACTIVE_REL}:{line_number}: requires at least two independent team owners"
            )
    return patterns


def validate(root: Path | None = None) -> list[str]:
    root = Path(ROOT if root is None else root).resolve()
    errors: list[str] = []
    mapping = _load_json(root, MAPPING_REL, errors)
    policy = _load_json(root, POLICY_REL, errors)
    template_bytes = _read_regular(root, TEMPLATE_REL, errors)
    active_bytes = _read_regular(root, ACTIVE_REL, errors)
    if (
        mapping is None
        or policy is None
        or template_bytes is None
        or active_bytes is None
    ):
        return errors
    template = _decode_utf8(template_bytes, TEMPLATE_REL, errors)
    active = _decode_utf8(active_bytes, ACTIVE_REL, errors)
    if template is None or active is None:
        return errors
    if mapping.get("schema") != "heptatrader.github-team-mapping.v1":
        errors.append(f"{MAPPING_REL}: schema mismatch")
    if policy.get("schema") != "heptatrader.github-governance-policy.v1":
        errors.append(f"{POLICY_REL}: schema mismatch")
    if policy.get("repository") != EXPECTED_REPOSITORY:
        errors.append(f"{POLICY_REL}: repository mismatch")
    if policy.get("default_branch") != EXPECTED_DEFAULT_BRANCH:
        errors.append(f"{POLICY_REL}: default_branch mismatch")

    organization, team_slugs, _ = _team_index(mapping, errors)
    if policy.get("organization") != organization:
        errors.append("team mapping and governance policy organization mismatch")
    if policy.get("organization") != EXPECTED_ORGANIZATION:
        errors.append(f"{POLICY_REL}: organization mismatch")

    minimum_members = mapping.get("minimum_members_per_team")
    minimum_maintainers = mapping.get("minimum_maintainers_per_team")
    if type(minimum_members) is not int or minimum_members < 2:
        errors.append(f"{MAPPING_REL}: minimum_members_per_team must be integer >= 2")
    if type(minimum_maintainers) is not int or minimum_maintainers < 1:
        errors.append(
            f"{MAPPING_REL}: minimum_maintainers_per_team must be integer >= 1"
        )

    codeowners_policy = policy.get("codeowners")
    if not isinstance(codeowners_policy, dict):
        errors.append(f"{POLICY_REL}: codeowners must be an object")
        codeowners_policy = {}
    if codeowners_policy.get("path") != ACTIVE_REL.as_posix():
        errors.append("governance policy CODEOWNERS path mismatch")
    if codeowners_policy.get("required_team_prefix") != f"@{organization}/":
        errors.append("governance policy required_team_prefix mismatch")
    if codeowners_policy.get("minimum_members_per_team") != minimum_members:
        errors.append("team mapping/policy minimum_members_per_team mismatch")
    if codeowners_policy.get("minimum_maintainers_per_team") != minimum_maintainers:
        errors.append("team mapping/policy minimum_maintainers_per_team mismatch")
    if codeowners_policy.get("require_non_secret_team") is not True:
        errors.append("governance policy must require non-secret CODEOWNERS teams")
    if codeowners_policy.get("require_write_access") is not True:
        errors.append("governance policy must require repository write access")
    minimum_distinct = codeowners_policy.get("minimum_distinct_teams")
    if (
        type(minimum_distinct) is not int
        or minimum_distinct < MINIMUM_DISTINCT_TEAMS_FLOOR
        or len(team_slugs) < minimum_distinct
    ):
        errors.append(
            "team mapping does not satisfy the policy minimum_distinct_teams floor"
        )

    if mapping.get("template_path") != TEMPLATE_REL.as_posix():
        errors.append(f"{MAPPING_REL}: template_path mismatch")
    rendered, mapped_patterns = _render_template(
        mapping, organization, team_slugs, errors
    )
    required_patterns = codeowners_policy.get("required_patterns")
    if required_patterns != mapped_patterns:
        errors.append(
            "governance policy required_patterns must exactly match the ordered "
            "team CODEOWNERS mapping"
        )
    rendered_bytes = rendered.encode("utf-8")
    if template_bytes != rendered_bytes:
        errors.append(f"{TEMPLATE_REL}: drift from deterministic team mapping")
    if active_bytes != template_bytes:
        errors.append(f"{ACTIVE_REL}: active bytes differ from reviewed team template")
    active_patterns = _parse_active_codeowners(
        active, organization, set(team_slugs), errors
    )
    if active_patterns != mapped_patterns:
        errors.append(f"{ACTIVE_REL}: ordered patterns differ from team mapping")
    return errors


def main() -> int:
    errors = validate()
    for error in errors:
        print(f"[TEAM-CODEOWNERS-ACTIVATION] {error}", file=sys.stderr)
    if errors:
        return 1
    print("[TEAM-CODEOWNERS-ACTIVATION] PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
