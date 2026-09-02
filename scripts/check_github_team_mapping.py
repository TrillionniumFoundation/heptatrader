#!/usr/bin/env python3
"""Validate the deterministic handoff from logical module owners to GitHub teams."""
from __future__ import annotations

import json
from pathlib import Path
import re
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
MAPPING_REL = Path(".github/github-team-mapping-v1.json")
POLICY_REL = Path(".github/github-governance-policy-v1.json")
MODULE_REGISTRY_REL = Path("docs/modules/module-registry-v2.json")
TEAM_SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,98}[a-z0-9])?$")
LOGICAL_HANDLE_RE = re.compile(r"^@hepta/[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")


class MappingError(ValueError):
    """Raised for malformed checked-in mapping inputs."""


def _strict_object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise MappingError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load(path: Path, root: Path, errors: list[str]) -> Any:
    try:
        return json.loads(
            (root / path).read_text(encoding="utf-8-sig"),
            object_pairs_hook=_strict_object_pairs,
        )
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        errors.append(f"{path.as_posix()}: invalid JSON: {exc}")
        return None


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
            continue
        if item in result:
            errors.append(f"{label}: duplicate value {item}")
            continue
        result.append(item)
    return result


def _manifest_handles(root: Path, errors: list[str]) -> set[str]:
    registry = _load(MODULE_REGISTRY_REL, root, errors)
    if not isinstance(registry, dict):
        return set()
    if registry.get("schema") != "heptatrader.module-registry.v2":
        errors.append(f"{MODULE_REGISTRY_REL}: schema mismatch")
    manifest_paths = _unique_strings(
        registry.get("manifest_paths"),
        f"{MODULE_REGISTRY_REL}.manifest_paths",
        errors,
    )
    handles: set[str] = set()
    module_ids: set[str] = set()
    for relative in manifest_paths:
        candidate = Path("docs") / relative
        if candidate.is_absolute() or ".." in candidate.parts:
            errors.append(f"{MODULE_REGISTRY_REL}: unsafe manifest path {relative}")
            continue
        manifest = _load(candidate, root, errors)
        if not isinstance(manifest, dict):
            continue
        module_id = manifest.get("id")
        if not isinstance(module_id, str) or not module_id:
            errors.append(f"{candidate}: missing module id")
        elif module_id in module_ids:
            errors.append(f"{candidate}: duplicate module id {module_id}")
        else:
            module_ids.add(module_id)
        owners = manifest.get("owners")
        if not isinstance(owners, dict):
            errors.append(f"{candidate}: owners must be an object")
            continue
        values: list[tuple[str, Any]] = [
            ("dri", owners.get("dri")),
            ("backup", owners.get("backup")),
        ]
        reviewers = owners.get("reviewers")
        if not isinstance(reviewers, list) or not reviewers:
            errors.append(f"{candidate}: owners.reviewers must be non-empty")
            reviewers = []
        values.extend((f"reviewers[{index}]", item) for index, item in enumerate(reviewers))
        for field, value in values:
            if not isinstance(value, str) or not LOGICAL_HANDLE_RE.fullmatch(value):
                errors.append(
                    f"{candidate}: owners.{field} is not a canonical logical handle"
                )
                continue
            handles.add(value)
    return handles


def _mapping_index(
    mapping: dict[str, Any], errors: list[str]
) -> tuple[str, dict[str, set[str]], dict[str, str]]:
    organization = mapping.get("organization")
    if not isinstance(organization, str) or not organization:
        errors.append(f"{MAPPING_REL}: organization must be non-empty")
        organization = ""
    raw_teams = mapping.get("teams")
    if not isinstance(raw_teams, list) or not raw_teams:
        errors.append(f"{MAPPING_REL}: teams must be a non-empty array")
        return organization, {}, {}
    by_team: dict[str, set[str]] = {}
    by_handle: dict[str, str] = {}
    for position, item in enumerate(raw_teams):
        label = f"{MAPPING_REL}.teams[{position}]"
        if not isinstance(item, dict):
            errors.append(f"{label}: expected object")
            continue
        slug = item.get("slug")
        if not isinstance(slug, str) or not TEAM_SLUG_RE.fullmatch(slug):
            errors.append(f"{label}.slug: invalid GitHub team slug")
            continue
        if slug in by_team:
            errors.append(f"{MAPPING_REL}: duplicate team slug {slug}")
            continue
        handles = _unique_strings(
            item.get("logical_handles"), f"{label}.logical_handles", errors
        )
        accepted: set[str] = set()
        for handle in handles:
            if not LOGICAL_HANDLE_RE.fullmatch(handle):
                errors.append(f"{label}: invalid logical handle {handle}")
                continue
            if handle in by_handle:
                errors.append(
                    f"{MAPPING_REL}: logical handle {handle} is mapped by both "
                    f"{by_handle[handle]} and {slug}"
                )
                continue
            by_handle[handle] = slug
            accepted.add(handle)
        by_team[slug] = accepted
    return organization, by_team, by_handle


def render_template(mapping: dict[str, Any], errors: list[str]) -> str:
    organization = mapping.get("organization")
    if not isinstance(organization, str) or not organization:
        errors.append(f"{MAPPING_REL}: organization is invalid")
        return ""
    teams = mapping.get("teams")
    team_slugs = {
        item.get("slug")
        for item in teams
        if isinstance(item, dict) and isinstance(item.get("slug"), str)
    } if isinstance(teams, list) else set()
    rules = mapping.get("codeowners_rules")
    if not isinstance(rules, list) or not rules:
        errors.append(f"{MAPPING_REL}: codeowners_rules must be non-empty")
        return ""
    lines = [
        "# Generated from .github/github-team-mapping-v1.json.",
        "# This template is not active until the named organization teams exist and an",
        "# independently reviewed change replaces .github/CODEOWNERS with these bytes.",
    ]
    patterns: set[str] = set()
    for position, item in enumerate(rules):
        label = f"{MAPPING_REL}.codeowners_rules[{position}]"
        if not isinstance(item, dict):
            errors.append(f"{label}: expected object")
            continue
        pattern = item.get("pattern")
        if not isinstance(pattern, str) or not pattern or any(
            character.isspace() for character in pattern
        ):
            errors.append(f"{label}.pattern: invalid CODEOWNERS pattern")
            continue
        if pattern in patterns:
            errors.append(f"{MAPPING_REL}: duplicate CODEOWNERS pattern {pattern}")
            continue
        patterns.add(pattern)
        owners = _unique_strings(item.get("teams"), f"{label}.teams", errors)
        if len(owners) < 2:
            errors.append(f"{label}: requires at least two independent teams")
        rendered: list[str] = []
        for slug in owners:
            if slug not in team_slugs:
                errors.append(f"{label}: unknown team slug {slug}")
                continue
            rendered.append(f"@{organization}/{slug}")
        if rendered:
            lines.append(pattern + " " + " ".join(rendered))
    return "\n".join(lines) + "\n"


def validate(root: Path = ROOT) -> list[str]:
    root = Path(root).resolve()
    errors: list[str] = []
    mapping = _load(MAPPING_REL, root, errors)
    policy = _load(POLICY_REL, root, errors)
    if not isinstance(mapping, dict) or not isinstance(policy, dict):
        return errors
    if mapping.get("schema") != "heptatrader.github-team-mapping.v1":
        errors.append(f"{MAPPING_REL}: schema mismatch")
    if policy.get("schema") != "heptatrader.github-governance-policy.v1":
        errors.append(f"{POLICY_REL}: schema mismatch")

    organization, by_team, by_handle = _mapping_index(mapping, errors)
    manifest_handles = _manifest_handles(root, errors)
    for handle in sorted(manifest_handles - set(by_handle)):
        errors.append(f"unmapped ModuleManifest owner handle: {handle}")
    for handle in sorted(set(by_handle) - manifest_handles):
        errors.append(f"stale mapped owner handle not present in ModuleManifest: {handle}")

    minimum_members = mapping.get("minimum_members_per_team")
    minimum_maintainers = mapping.get("minimum_maintainers_per_team")
    if not isinstance(minimum_members, int) or minimum_members < 2:
        errors.append(f"{MAPPING_REL}: minimum_members_per_team must be >= 2")
    if not isinstance(minimum_maintainers, int) or minimum_maintainers < 1:
        errors.append(f"{MAPPING_REL}: minimum_maintainers_per_team must be >= 1")
    if len(by_team) < 4:
        errors.append(f"{MAPPING_REL}: at least four distinct teams are required")
    for slug, handles in sorted(by_team.items()):
        if not handles:
            errors.append(f"team {slug}: has no logical ownership handles")

    if policy.get("organization") != organization:
        errors.append("team mapping and governance policy organization mismatch")
    codeowners_policy = policy.get("codeowners")
    if not isinstance(codeowners_policy, dict):
        errors.append(f"{POLICY_REL}: codeowners must be an object")
        codeowners_policy = {}
    if codeowners_policy.get("minimum_members_per_team") != minimum_members:
        errors.append("team mapping/policy minimum_members_per_team mismatch")
    if codeowners_policy.get("minimum_maintainers_per_team") != minimum_maintainers:
        errors.append("team mapping/policy minimum_maintainers_per_team mismatch")
    minimum_distinct = codeowners_policy.get("minimum_distinct_teams")
    if not isinstance(minimum_distinct, int) or len(by_team) < minimum_distinct:
        errors.append("team mapping does not satisfy policy minimum_distinct_teams")

    rendered = render_template(mapping, errors)
    template_path = mapping.get("template_path")
    if not isinstance(template_path, str) or not template_path:
        errors.append(f"{MAPPING_REL}: template_path must be non-empty")
    else:
        relative = Path(template_path)
        if relative.is_absolute() or ".." in relative.parts:
            errors.append(f"{MAPPING_REL}: unsafe template_path {template_path}")
        else:
            try:
                actual = (root / relative).read_text(encoding="utf-8-sig")
            except (OSError, UnicodeError) as exc:
                errors.append(f"{relative}: unreadable: {exc}")
            else:
                if actual != rendered:
                    errors.append(
                        f"{relative}: drift from deterministic GitHub team mapping"
                    )

    required_patterns = codeowners_policy.get("required_patterns")
    rules = mapping.get("codeowners_rules")
    mapped_patterns = [
        item.get("pattern")
        for item in rules
        if isinstance(item, dict) and isinstance(item.get("pattern"), str)
    ] if isinstance(rules, list) else []
    if required_patterns != mapped_patterns:
        errors.append(
            "governance policy required_patterns must exactly match the ordered "
            "team CODEOWNERS mapping"
        )
    return errors


def main() -> int:
    errors = validate()
    for error in errors:
        print(f"[GITHUB-TEAM-MAPPING] {error}", file=sys.stderr)
    if errors:
        return 1
    print("[GITHUB-TEAM-MAPPING] PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
