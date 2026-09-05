#!/usr/bin/env python3
"""Verify the static team-only CODEOWNERS activation contract.

The verifier binds every qualifying CODEOWNERS rule to the exact stage-0 Git
index of the repository root.  It proves checked-in bytes and policy
consistency only; live team existence, staffing, visibility, repository
permission, rulesets and protected admission remain external governance facts.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
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
TRACKED_PATH_POLICY: dict[str, Any] = {
    "source": "git-ls-files-stage-zero",
    "scope": "all-top-level-directories",
    "require_match_per_rule": True,
    "require_byte_sorted_root_rules": True,
    "require_git_repository_root": True,
    "allow_filesystem_fallback": False,
    "allow_future_only_patterns": False,
    "wildcard_counts_for_team_qualification": False,
    "reject_gitlinks": True,
    "reject_unmerged_index": True,
}
TEAM_SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,98}[a-z0-9])?$")
LOGICAL_HANDLE_RE = re.compile(r"^@hepta/[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
TEAM_OWNER_RE = re.compile(r"^@([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)$")
OBJECT_ID_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
ALLOWED_STAGE_ZERO_MODES = {"100644", "100755", "120000"}


class ActivationError(ValueError):
    """Raised for malformed checked-in activation inputs."""


def _strict_object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ActivationError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _read_regular(root: Path, relative: Path, errors: list[str]) -> bytes | None:
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


def _decode_utf8(data: bytes, relative: Path, errors: list[str]) -> str | None:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        errors.append(f"{relative}: invalid UTF-8: {exc}")
        return None


def _reject_nonfinite_json(token: str) -> Any:
    raise ActivationError(f"non-finite JSON constant: {token}")


def _load_json(root: Path, relative: Path, errors: list[str]) -> dict[str, Any] | None:
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
        errors.append(f"{MAPPING_REL}: organization must be {EXPECTED_ORGANIZATION}")
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


def _normalize_tracked_path(raw: str, errors: list[str]) -> str | None:
    if not raw or raw.startswith("/") or "\\" in raw:
        errors.append(f"tracked path is not a canonical repository-relative path: {raw!r}")
        return None
    parts = raw.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        errors.append(f"tracked path contains an unsafe component: {raw!r}")
        return None
    return raw


def _git_environment() -> dict[str, str]:
    # Caller-controlled Git redirection variables must not select a different
    # repository, index or object store.  System/global config is unnecessary
    # for the two read-only plumbing commands used below.
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("GIT_")
    }
    environment.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    return environment


def _run_git(root: Path, arguments: list[str], errors: list[str], label: str) -> bytes | None:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), *arguments],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=20,
            env=_git_environment(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        errors.append(f"{label} failed: {exc}")
        return None
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        errors.append(f"{label} failed with {completed.returncode}: {detail}")
        return None
    return completed.stdout


def _collect_tracked_paths(root: Path, errors: list[str]) -> list[str]:
    metadata = root / ".git"
    try:
        info = metadata.lstat()
    except OSError as exc:
        errors.append(f"Git metadata is required at repository root: {exc}")
        return []
    if metadata.is_symlink() or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
        errors.append("Git metadata must be a non-symlink directory or worktree pointer file")
        return []

    top_level_raw = _run_git(root, ["rev-parse", "--show-toplevel"], errors, "git rev-parse")
    if top_level_raw is None:
        return []
    try:
        top_level = Path(top_level_raw.decode("utf-8").strip()).resolve()
    except (UnicodeDecodeError, OSError) as exc:
        errors.append(f"git repository root is invalid: {exc}")
        return []
    if top_level != root:
        errors.append(f"validator root is not the Git repository root: {top_level}")
        return []

    index_raw = _run_git(
        root,
        ["ls-files", "--stage", "-z", "--"],
        errors,
        "git ls-files --stage",
    )
    if index_raw is None:
        return []
    try:
        decoded = index_raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        errors.append(f"git index contains a non-UTF-8 path: {exc}")
        return []

    paths: list[str] = []
    seen: set[str] = set()
    for record in decoded.split("\0"):
        if not record:
            continue
        try:
            metadata_text, raw_path = record.split("\t", 1)
            mode, object_id, stage = metadata_text.split(" ")
        except ValueError:
            errors.append(f"malformed git index record: {record!r}")
            continue
        normalized = _normalize_tracked_path(raw_path, errors)
        if normalized is None:
            continue
        if stage != "0":
            errors.append(f"unmerged git index entry is not permitted: {normalized} (stage {stage})")
            continue
        if not OBJECT_ID_RE.fullmatch(object_id) or set(object_id) == {"0"}:
            errors.append(f"git index object id is invalid for {normalized}")
            continue
        if mode == "160000":
            errors.append(f"tracked gitlink/submodule is not permitted: {normalized}")
            continue
        if mode not in ALLOWED_STAGE_ZERO_MODES:
            errors.append(f"unsupported git index mode {mode} for {normalized}")
            continue
        if normalized in seen:
            errors.append(f"duplicate stage-zero git index path: {normalized}")
            continue
        seen.add(normalized)
        paths.append(normalized)

    result = sorted(paths)
    if not result:
        errors.append("tracked repository path inventory must not be empty")
    return result


def _directory_prefix(pattern: str, label: str, errors: list[str]) -> str | None:
    if pattern == "*":
        return None
    if (
        not pattern.startswith("/")
        or not pattern.endswith("/")
        or pattern.count("/") != 2
        or any(character in pattern for character in "*?[]\\")
        or any(character.isspace() for character in pattern)
    ):
        errors.append(
            f"{label}.pattern: qualification rule must be an exact top-level "
            "directory pattern"
        )
        return None
    root = pattern[1:-1]
    if root in {"", ".", ".."}:
        errors.append(f"{label}.pattern: invalid top-level directory")
        return None
    return root + "/"


def _render_template(
    mapping: dict[str, Any],
    organization: str,
    team_slugs: list[str],
    errors: list[str],
) -> tuple[str, list[str], dict[str, list[str]]]:
    raw_rules = mapping.get("codeowners_rules")
    if not isinstance(raw_rules, list) or not raw_rules:
        errors.append(f"{MAPPING_REL}: codeowners_rules must be a non-empty array")
        return "", [], {}
    lines = [
        "# Generated from .github/github-team-mapping-v1.json.",
        "# Static ownership is not live governance evidence: teams, permissions and",
        "# protected main admission must be independently verified before merge.",
    ]
    patterns: list[str] = []
    owners_by_pattern: dict[str, list[str]] = {}
    known = set(team_slugs)
    for position, item in enumerate(raw_rules):
        label = f"{MAPPING_REL}.codeowners_rules[{position}]"
        if not isinstance(item, dict):
            errors.append(f"{label}: expected object")
            continue
        pattern = item.get("pattern")
        if not isinstance(pattern, str) or not pattern:
            errors.append(f"{label}.pattern: invalid CODEOWNERS pattern")
            continue
        if pattern != "*" and _directory_prefix(pattern, label, errors) is None:
            continue
        if pattern in patterns:
            errors.append(f"{MAPPING_REL}: duplicate CODEOWNERS pattern {pattern}")
            continue
        patterns.append(pattern)
        owners = _unique_strings(item.get("teams"), f"{label}.teams", errors)
        if len(owners) < 2:
            errors.append(f"{label}: requires at least two independent teams")
        rendered: list[str] = []
        accepted: list[str] = []
        for slug in owners:
            if slug not in known:
                errors.append(f"{label}: unknown team slug {slug}")
            else:
                accepted.append(slug)
                rendered.append(f"@{organization}/{slug}")
        owners_by_pattern[pattern] = accepted
        if rendered:
            lines.append(pattern + " " + " ".join(rendered))
    return "\n".join(lines) + "\n", patterns, owners_by_pattern


def _validate_tree_binding(
    patterns: list[str],
    owners_by_pattern: dict[str, list[str]],
    tracked_paths: list[str],
    team_slugs: list[str],
    errors: list[str],
) -> None:
    if not tracked_paths:
        return
    actual_roots = sorted(
        {path.split("/", 1)[0] for path in tracked_paths if "/" in path}
    )
    expected_root_patterns = [f"/{root}/" for root in actual_roots]
    if not patterns or patterns[0] != "*" or patterns.count("*") != 1:
        errors.append(f"{MAPPING_REL}: wildcard fallback must appear exactly once first")
    qualification_patterns = [pattern for pattern in patterns if pattern != "*"]
    qualified_teams: set[str] = set()
    for position, pattern in enumerate(qualification_patterns, start=1):
        label = f"{MAPPING_REL}.codeowners_rules[{position}]"
        prefix = _directory_prefix(pattern, label, errors)
        if prefix is None:
            continue
        if not any(path.startswith(prefix) for path in tracked_paths):
            errors.append(f"{MAPPING_REL}: qualification pattern {pattern} matches no tracked path")
            continue
        qualified_teams.update(owners_by_pattern.get(pattern, []))

    mapped_set = set(qualification_patterns)
    expected_set = set(expected_root_patterns)
    missing = sorted(expected_set - mapped_set)
    extra = sorted(mapped_set - expected_set)
    if missing:
        errors.append(
            f"{MAPPING_REL}: missing exact top-level CODEOWNERS rules: "
            + ", ".join(missing)
        )
    if extra:
        errors.append(
            f"{MAPPING_REL}: qualification rules are not current tracked top-level "
            "directories: " + ", ".join(extra)
        )
    if not missing and not extra and qualification_patterns != expected_root_patterns:
        errors.append(
            f"{MAPPING_REL}: top-level CODEOWNERS rules must follow byte-sorted "
            "tracked-root order"
        )
    for slug in sorted(set(team_slugs) - qualified_teams):
        errors.append(
            f"{MAPPING_REL}: team {slug} has no matched non-wildcard CODEOWNERS rule"
        )


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
    if mapping is None or policy is None or template_bytes is None or active_bytes is None:
        return errors
    template = _decode_utf8(template_bytes, TEMPLATE_REL, errors)
    active = _decode_utf8(active_bytes, ACTIVE_REL, errors)
    if template is None or active is None:
        return errors
    tracked_paths = _collect_tracked_paths(root, errors)

    if mapping.get("schema") != "heptatrader.github-team-mapping.v1":
        errors.append(f"{MAPPING_REL}: schema mismatch")
    if policy.get("schema") != "heptatrader.github-governance-policy.v1":
        errors.append(f"{POLICY_REL}: schema mismatch")
    if policy.get("repository") != EXPECTED_REPOSITORY:
        errors.append(f"{POLICY_REL}: repository mismatch")
    if policy.get("default_branch") != EXPECTED_DEFAULT_BRANCH:
        errors.append(f"{POLICY_REL}: default_branch mismatch")
    if mapping.get("tracked_path_policy") != TRACKED_PATH_POLICY:
        errors.append(f"{MAPPING_REL}: tracked_path_policy mismatch")

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
        errors.append(f"{MAPPING_REL}: minimum_maintainers_per_team must be integer >= 1")

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
    if codeowners_policy.get("tracked_path_policy") != TRACKED_PATH_POLICY:
        errors.append(f"{POLICY_REL}: tracked_path_policy mismatch")
    minimum_distinct = codeowners_policy.get("minimum_distinct_teams")
    if (
        type(minimum_distinct) is not int
        or minimum_distinct < MINIMUM_DISTINCT_TEAMS_FLOOR
        or len(team_slugs) < minimum_distinct
    ):
        errors.append("team mapping does not satisfy the policy minimum_distinct_teams floor")

    if mapping.get("template_path") != TEMPLATE_REL.as_posix():
        errors.append(f"{MAPPING_REL}: template_path mismatch")
    rendered, mapped_patterns, owners_by_pattern = _render_template(
        mapping, organization, team_slugs, errors
    )
    _validate_tree_binding(
        mapped_patterns, owners_by_pattern, tracked_paths, team_slugs, errors
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
