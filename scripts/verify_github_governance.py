#!/usr/bin/env python3
"""Live, read-only verification for team ownership and protected merge controls.

The verifier never mutates GitHub. It reads repository, ruleset, CODEOWNERS,
team, review and check-run state from the GitHub REST API, validates it against
checked-in policy, and emits a digest-bound receipt. Repository prose,
screenshots and manually authored success JSON are not accepted as evidence.
"""
from __future__ import annotations

import argparse
import base64
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import sys
import tempfile
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
POLICY_REL = Path(".github/github-governance-policy-v1.json")
CONTEXTS_REL = Path(".github/required-check-contexts-v1.json")
TEAM_OWNER_RE = re.compile(r"^@([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class GovernanceError(RuntimeError):
    """Raised when live evidence cannot be collected safely."""


def _strict_object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load_json(path: Path) -> Any:
    return json.loads(
        path.read_text(encoding="utf-8-sig"),
        object_pairs_hook=_strict_object_pairs,
    )


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical_bytes(value)).hexdigest()


def load_policy(root: Path = ROOT) -> tuple[dict[str, Any], dict[str, Any]]:
    policy = _load_json(Path(root) / POLICY_REL)
    contexts = _load_json(Path(root) / CONTEXTS_REL)
    if not isinstance(policy, dict):
        raise ValueError(f"{POLICY_REL}: expected object")
    if policy.get("schema") != "heptatrader.github-governance-policy.v1":
        raise ValueError(f"{POLICY_REL}: schema mismatch")
    if not isinstance(contexts, dict):
        raise ValueError(f"{CONTEXTS_REL}: expected object")
    if contexts.get("schema") != "heptatrader.required-check-contexts.v1":
        raise ValueError(f"{CONTEXTS_REL}: schema mismatch")
    return policy, contexts


@dataclass
class GitHubClient:
    token: str
    api_base: str = "https://api.github.com"
    timeout_seconds: int = 30

    def __post_init__(self) -> None:
        self.api_base = self.api_base.rstrip("/")
        self.response_digests: dict[str, str] = {}

    def get_json(self, path: str, query: dict[str, str] | None = None) -> Any:
        if not path.startswith("/"):
            raise GovernanceError(f"unsafe GitHub API path: {path}")
        url = self.api_base + path
        if query:
            url += "?" + urlencode(query)
        request = Request(
            url,
            method="GET",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "heptatrader-governance-verifier/1",
            },
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                payload = response.read()
                status = response.status
        except HTTPError as exc:
            detail = exc.read(4096).decode("utf-8", errors="replace")
            raise GovernanceError(
                f"GitHub API GET {path} failed with HTTP {exc.code}: {detail}"
            ) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise GovernanceError(f"GitHub API GET {path} failed: {exc}") from exc
        if status != 200:
            raise GovernanceError(f"GitHub API GET {path} returned HTTP {status}")
        try:
            value = json.loads(
                payload.decode("utf-8"), object_pairs_hook=_strict_object_pairs
            )
        except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
            raise GovernanceError(
                f"GitHub API GET {path} returned invalid JSON: {exc}"
            ) from exc
        key = path + ("?" + urlencode(query) if query else "")
        self.response_digests[key] = _digest(value)
        return value


def _required_string_list(
    document: dict[str, Any], field: str, errors: list[str]
) -> list[str]:
    value = document.get(field)
    if not isinstance(value, list) or not value:
        errors.append(f"{field}: expected non-empty array")
        return []
    result: list[str] = []
    for position, item in enumerate(value):
        if not isinstance(item, str) or not item:
            errors.append(f"{field}[{position}]: expected non-empty string")
            continue
        if item in result:
            errors.append(f"{field}: duplicate value {item}")
            continue
        result.append(item)
    return result


def parse_codeowners(
    text: str, organization: str, team_prefix: str, errors: list[str]
) -> tuple[dict[str, list[str]], set[str]]:
    rules: dict[str, list[str]] = {}
    team_slugs: set[str] = set()
    for line_number, raw in enumerate(text.splitlines(), start=1):
        try:
            tokens = shlex.split(raw, comments=True, posix=True)
        except ValueError as exc:
            errors.append(f"CODEOWNERS:{line_number}: invalid syntax: {exc}")
            continue
        if not tokens:
            continue
        if len(tokens) < 2:
            errors.append(f"CODEOWNERS:{line_number}: rule has no owner")
            continue
        pattern, owners = tokens[0], tokens[1:]
        if pattern in rules:
            errors.append(
                f"CODEOWNERS:{line_number}: duplicate exact pattern {pattern}"
            )
            continue
        accepted: list[str] = []
        for owner in owners:
            match = TEAM_OWNER_RE.fullmatch(owner)
            if match is None or not owner.lower().startswith(team_prefix.lower()):
                errors.append(
                    f"CODEOWNERS:{line_number}: owner must be an organization team: "
                    f"{owner}"
                )
                continue
            owner_org, slug = match.groups()
            if owner_org.lower() != organization.lower():
                errors.append(
                    f"CODEOWNERS:{line_number}: owner organization mismatch: {owner}"
                )
                continue
            accepted.append(owner)
            team_slugs.add(slug)
        rules[pattern] = accepted
    return rules, team_slugs


def _main_ruleset(
    policy: dict[str, Any], rulesets: Any, errors: list[str]
) -> dict[str, Any] | None:
    if not isinstance(rulesets, list):
        errors.append("rulesets: expected array")
        return None
    expected = policy["ruleset"]
    accepted_refs = set(expected["accepted_ref_includes"])
    candidates: list[dict[str, Any]] = []
    for position, item in enumerate(rulesets):
        if not isinstance(item, dict):
            errors.append(f"rulesets[{position}]: expected object")
            continue
        conditions = item.get("conditions")
        ref_name = conditions.get("ref_name") if isinstance(conditions, dict) else None
        includes = ref_name.get("include") if isinstance(ref_name, dict) else None
        if (
            item.get("target") == expected["target"]
            and item.get("enforcement") == expected["enforcement"]
            and isinstance(includes, list)
            and accepted_refs.intersection(
                value for value in includes if isinstance(value, str)
            )
        ):
            candidates.append(item)
    if len(candidates) != 1:
        errors.append(
            "rulesets: expected exactly one active branch ruleset targeting "
            f"main/default; found {len(candidates)}"
        )
        return None
    return candidates[0]


def _validate_ruleset(
    policy: dict[str, Any],
    contexts: dict[str, Any],
    ruleset: dict[str, Any],
    errors: list[str],
) -> None:
    expected = policy["ruleset"]
    bypass = ruleset.get("bypass_actors")
    if not isinstance(bypass, list):
        errors.append(
            "ruleset.bypass_actors is absent; verification token must have enough "
            "access to prove that no bypass actor exists"
        )
    elif bypass != expected["bypass_actors"]:
        errors.append("ruleset.bypass_actors must be empty")

    raw_rules = ruleset.get("rules")
    if not isinstance(raw_rules, list):
        errors.append("ruleset.rules: expected array")
        return
    by_type: dict[str, dict[str, Any]] = {}
    for position, item in enumerate(raw_rules):
        if not isinstance(item, dict) or not isinstance(item.get("type"), str):
            errors.append(f"ruleset.rules[{position}]: invalid rule")
            continue
        rule_type = item["type"]
        if rule_type in by_type:
            errors.append(f"ruleset.rules: duplicate rule type {rule_type}")
            continue
        by_type[rule_type] = item
    for rule_type in expected["required_rule_types"]:
        if rule_type not in by_type:
            errors.append(f"ruleset: missing required rule {rule_type}")

    pull_rule = by_type.get("pull_request", {})
    pull_params = pull_rule.get("parameters") if isinstance(pull_rule, dict) else None
    expected_pull = expected["pull_request"]
    if not isinstance(pull_params, dict):
        errors.append("ruleset.pull_request.parameters: expected object")
    else:
        for field in (
            "dismiss_stale_reviews_on_push",
            "require_code_owner_review",
            "require_last_push_approval",
            "required_review_thread_resolution",
        ):
            if pull_params.get(field) is not expected_pull[field]:
                errors.append(
                    f"ruleset.pull_request.{field} must be {expected_pull[field]!r}"
                )
        approvals = pull_params.get("required_approving_review_count")
        if (
            not isinstance(approvals, int)
            or approvals < expected_pull["required_approving_review_count"]
        ):
            errors.append(
                "ruleset.pull_request.required_approving_review_count is below "
                "policy"
            )
        allowed = pull_params.get("allowed_merge_methods")
        required_method = expected_pull["required_allowed_merge_method"]
        if not isinstance(allowed, list) or required_method not in allowed:
            errors.append(
                "ruleset.pull_request.allowed_merge_methods must include "
                f"{required_method}"
            )

    status_rule = by_type.get("required_status_checks", {})
    status_params = (
        status_rule.get("parameters") if isinstance(status_rule, dict) else None
    )
    expected_status = expected["required_status_checks"]
    required_contexts = set(
        _required_string_list(contexts, "required_pull_request_contexts", errors)
    ) | set(
        _required_string_list(contexts, "required_merge_group_contexts", errors)
    )
    if not isinstance(status_params, dict):
        errors.append("ruleset.required_status_checks.parameters: expected object")
    else:
        for field in (
            "strict_required_status_checks_policy",
            "do_not_enforce_on_create",
        ):
            if status_params.get(field) is not expected_status[field]:
                errors.append(
                    f"ruleset.required_status_checks.{field} must be "
                    f"{expected_status[field]!r}"
                )
        configured = status_params.get("required_status_checks")
        configured_map: dict[str, Any] = {}
        if not isinstance(configured, list):
            errors.append("ruleset.required_status_checks list is missing")
        else:
            for position, item in enumerate(configured):
                if not isinstance(item, dict) or not isinstance(
                    item.get("context"), str
                ):
                    errors.append(
                        f"ruleset.required_status_checks[{position}]: invalid check"
                    )
                    continue
                context = item["context"]
                if context in configured_map:
                    errors.append(
                        f"ruleset.required_status_checks: duplicate {context}"
                    )
                configured_map[context] = item.get("integration_id")
        for context in sorted(required_contexts - set(configured_map)):
            errors.append(
                f"ruleset.required_status_checks: missing context {context}"
            )
        integration_id = expected_status.get("required_integration_id")
        if isinstance(integration_id, int):
            for context in sorted(required_contexts & set(configured_map)):
                if configured_map[context] != integration_id:
                    errors.append(
                        f"ruleset.required_status_checks: {context} must be bound "
                        f"to integration_id {integration_id}"
                    )

    queue_rule = by_type.get("merge_queue", {})
    queue_params = (
        queue_rule.get("parameters") if isinstance(queue_rule, dict) else None
    )
    expected_queue = expected["merge_queue"]
    if not isinstance(queue_params, dict):
        errors.append("ruleset.merge_queue.parameters: expected object")
    else:
        for field in ("grouping_strategy", "merge_method", "max_entries_to_merge"):
            if queue_params.get(field) != expected_queue[field]:
                errors.append(
                    f"ruleset.merge_queue.{field} must be "
                    f"{expected_queue[field]!r}"
                )
        timeout = queue_params.get("check_response_timeout_minutes")
        if not isinstance(timeout, int) or not (
            expected_queue["minimum_check_response_timeout_minutes"]
            <= timeout
            <= expected_queue["maximum_check_response_timeout_minutes"]
        ):
            errors.append(
                "ruleset.merge_queue.check_response_timeout_minutes is out of "
                "policy"
            )


def _write_permission(repository: Any) -> bool:
    if not isinstance(repository, dict):
        return False
    permissions = repository.get("permissions")
    if isinstance(permissions, dict):
        return any(
            permissions.get(key) is True for key in ("push", "maintain", "admin")
        )
    permission = repository.get("permission")
    return permission in {"push", "maintain", "admin"}


def _latest_check(
    check_runs: list[dict[str, Any]], name: str
) -> dict[str, Any] | None:
    matching = [item for item in check_runs if item.get("name") == name]
    if not matching:
        return None
    return max(
        matching,
        key=lambda item: (
            item.get("id") if isinstance(item.get("id"), int) else -1,
            item.get("started_at") or "",
            item.get("completed_at") or "",
        ),
    )


def _validate_checks(
    payload: Any,
    expected_sha: str,
    required: list[str],
    label: str,
    errors: list[str],
) -> None:
    if not isinstance(payload, dict) or not isinstance(
        payload.get("check_runs"), list
    ):
        errors.append(f"{label}: invalid check-runs payload")
        return
    runs = [item for item in payload["check_runs"] if isinstance(item, dict)]
    for context in required:
        check = _latest_check(runs, context)
        if check is None:
            errors.append(f"{label}: missing check {context}")
            continue
        if check.get("head_sha") != expected_sha:
            errors.append(f"{label}: check {context} is bound to another SHA")
        if (
            check.get("status") != "completed"
            or check.get("conclusion") != "success"
        ):
            errors.append(f"{label}: check {context} is not terminal success")


def validate_snapshot(
    policy: dict[str, Any],
    contexts: dict[str, Any],
    snapshot: dict[str, Any],
    expected_head_sha: str,
    merge_group_sha: str,
) -> list[str]:
    errors: list[str] = []
    repository_name = policy.get("repository")
    organization = policy.get("organization")
    default_branch = policy.get("default_branch")
    if not isinstance(repository_name, str) or "/" not in repository_name:
        return ["policy.repository is invalid"]
    if not isinstance(organization, str) or not organization:
        return ["policy.organization is invalid"]
    if not isinstance(default_branch, str) or not default_branch:
        return ["policy.default_branch is invalid"]
    if not SHA_RE.fullmatch(expected_head_sha):
        errors.append(
            "expected head SHA must be 40 lowercase hexadecimal characters"
        )
    if not SHA_RE.fullmatch(merge_group_sha):
        errors.append(
            "merge-group SHA must be 40 lowercase hexadecimal characters"
        )

    repository = snapshot.get("repository")
    if not isinstance(repository, dict):
        errors.append("repository evidence is missing")
    else:
        if repository.get("full_name") != repository_name:
            errors.append("repository.full_name mismatch")
        if repository.get("default_branch") != default_branch:
            errors.append("repository.default_branch mismatch")

    branch = snapshot.get("branch")
    if not isinstance(branch, dict):
        errors.append("branch evidence is missing")
    else:
        if branch.get("name") != default_branch:
            errors.append("branch.name mismatch")
        if branch.get("protected") is not True:
            errors.append("default branch is not protected")

    ruleset = _main_ruleset(policy, snapshot.get("rulesets"), errors)
    if ruleset is not None:
        _validate_ruleset(policy, contexts, ruleset, errors)

    codeowner_errors = snapshot.get("codeowners_errors")
    if (
        not isinstance(codeowner_errors, dict)
        or codeowner_errors.get("errors") != []
    ):
        errors.append(
            "GitHub reports CODEOWNERS errors or evidence is missing"
        )
    codeowners_text = snapshot.get("codeowners_text")
    teams: set[str] = set()
    if not isinstance(codeowners_text, str):
        errors.append("CODEOWNERS content is missing")
    else:
        code_policy = policy["codeowners"]
        rules, teams = parse_codeowners(
            codeowners_text,
            organization,
            code_policy["required_team_prefix"],
            errors,
        )
        for pattern in code_policy["required_patterns"]:
            if pattern not in rules or not rules[pattern]:
                errors.append(
                    "CODEOWNERS: required team-owned pattern is missing: "
                    f"{pattern}"
                )
        if len(teams) < code_policy["minimum_distinct_teams"]:
            errors.append(
                "CODEOWNERS does not distribute ownership across enough teams"
            )

    team_evidence = snapshot.get("teams")
    if not isinstance(team_evidence, dict):
        errors.append("team evidence is missing")
        team_evidence = {}
    code_policy = policy["codeowners"]
    for slug in sorted(teams):
        evidence = team_evidence.get(slug)
        if not isinstance(evidence, dict):
            errors.append(f"team {slug}: evidence is missing")
            continue
        team = evidence.get("team")
        members = evidence.get("members")
        maintainers = evidence.get("maintainers")
        team_repository = evidence.get("repository")
        if not isinstance(team, dict):
            errors.append(f"team {slug}: team object is missing")
        else:
            if team.get("slug") != slug:
                errors.append(f"team {slug}: slug mismatch")
            org = team.get("organization")
            if (
                not isinstance(org, dict)
                or str(org.get("login", "")).lower() != organization.lower()
            ):
                errors.append(f"team {slug}: organization mismatch")
            if (
                code_policy["require_non_secret_team"]
                and team.get("privacy") == "secret"
            ):
                errors.append(
                    f"team {slug}: secret team cannot provide visible CODEOWNERS"
                )
        if not isinstance(members, list):
            errors.append(f"team {slug}: member list is missing")
            members = []
        if not isinstance(maintainers, list):
            errors.append(f"team {slug}: maintainer list is missing")
            maintainers = []
        member_ids = {
            item.get("id")
            for item in members
            if isinstance(item, dict) and isinstance(item.get("id"), int)
        }
        maintainer_ids = {
            item.get("id")
            for item in maintainers
            if isinstance(item, dict) and isinstance(item.get("id"), int)
        }
        if len(member_ids) < code_policy["minimum_members_per_team"]:
            errors.append(f"team {slug}: too few members")
        if len(maintainer_ids) < code_policy["minimum_maintainers_per_team"]:
            errors.append(f"team {slug}: too few maintainers")
        if not maintainer_ids.issubset(member_ids):
            errors.append(
                f"team {slug}: maintainer evidence is not a subset of members"
            )
        if code_policy["require_write_access"] and not _write_permission(
            team_repository
        ):
            errors.append(
                f"team {slug}: no write/maintain/admin repository permission"
            )

    pull = snapshot.get("pull_request")
    if not isinstance(pull, dict):
        errors.append("pull request evidence is missing")
        pull = {}
    head = pull.get("head")
    base = pull.get("base")
    user = pull.get("user")
    head_sha = head.get("sha") if isinstance(head, dict) else None
    author = user.get("login") if isinstance(user, dict) else None
    if head_sha != expected_head_sha:
        errors.append("pull request head SHA mismatch")
    if not isinstance(base, dict) or base.get("ref") != default_branch:
        errors.append(
            "pull request does not target the protected default branch"
        )
    if pull.get("state") != "open":
        errors.append("qualification pull request is not open")
    if pull.get("draft") is not False:
        errors.append("qualification pull request is still draft")
    if not isinstance(author, str) or not author:
        errors.append("pull request author is missing")

    reviews = snapshot.get("reviews")
    if not isinstance(reviews, list):
        errors.append("review evidence is missing")
        reviews = []
    approvers = {
        item["user"]["login"]
        for item in reviews
        if isinstance(item, dict)
        and item.get("state") == "APPROVED"
        and item.get("commit_id") == expected_head_sha
        and isinstance(item.get("user"), dict)
        and isinstance(item["user"].get("login"), str)
        and item["user"]["login"] != author
    }
    required_approvals = policy["approval"][
        "minimum_non_author_approvals_on_exact_head"
    ]
    if len(approvers) < required_approvals:
        errors.append(
            "fresh non-author exact-head approval requirement is not satisfied"
        )

    pr_contexts = _required_string_list(
        contexts, "required_pull_request_contexts", errors
    )
    _validate_checks(
        snapshot.get("head_check_runs"),
        expected_head_sha,
        pr_contexts,
        "source head",
        errors,
    )
    if policy["approval"].get("require_merge_group_success") is True:
        merge_contexts = _required_string_list(
            contexts, "required_merge_group_contexts", errors
        )
        _validate_checks(
            snapshot.get("merge_group_check_runs"),
            merge_group_sha,
            merge_contexts,
            "merge group",
            errors,
        )
    return errors


def collect_snapshot(
    client: GitHubClient,
    policy: dict[str, Any],
    pull_number: int,
    expected_head_sha: str,
    merge_group_sha: str,
) -> dict[str, Any]:
    repository = policy["repository"]
    organization = policy["organization"]
    default_branch = policy["default_branch"]
    owner, repo = repository.split("/", 1)
    base = f"/repos/{quote(owner, safe='')}/{quote(repo, safe='')}"

    summaries = client.get_json(base + "/rulesets", {"per_page": "100"})
    if not isinstance(summaries, list):
        raise GovernanceError("ruleset list response is not an array")
    rulesets: list[Any] = []
    for item in summaries:
        if not isinstance(item, dict) or not isinstance(item.get("id"), int):
            raise GovernanceError("ruleset list contains an invalid entry")
        rulesets.append(client.get_json(base + f"/rulesets/{item['id']}"))

    codeowners_blob = client.get_json(
        base + "/contents/.github/CODEOWNERS", {"ref": default_branch}
    )
    if (
        not isinstance(codeowners_blob, dict)
        or codeowners_blob.get("encoding") != "base64"
    ):
        raise GovernanceError("CODEOWNERS contents response is not base64")
    raw_content = codeowners_blob.get("content")
    if not isinstance(raw_content, str):
        raise GovernanceError("CODEOWNERS contents response has no content")
    try:
        codeowners_text = base64.b64decode(
            raw_content, validate=False
        ).decode("utf-8")
    except (ValueError, UnicodeError) as exc:
        raise GovernanceError(
            f"CODEOWNERS content cannot be decoded: {exc}"
        ) from exc

    parse_errors: list[str] = []
    _, team_slugs = parse_codeowners(
        codeowners_text,
        organization,
        policy["codeowners"]["required_team_prefix"],
        parse_errors,
    )
    if parse_errors:
        raise GovernanceError("; ".join(parse_errors))

    teams: dict[str, Any] = {}
    for slug in sorted(team_slugs):
        org_base = (
            f"/orgs/{quote(organization, safe='')}/teams/{quote(slug, safe='')}"
        )
        teams[slug] = {
            "team": client.get_json(org_base),
            "members": client.get_json(
                org_base + "/members", {"role": "all", "per_page": "100"}
            ),
            "maintainers": client.get_json(
                org_base + "/members",
                {"role": "maintainer", "per_page": "100"},
            ),
            "repository": client.get_json(
                org_base
                + f"/repos/{quote(owner, safe='')}/{quote(repo, safe='')}"
            ),
        }

    return {
        "repository": client.get_json(base),
        "branch": client.get_json(
            base + f"/branches/{quote(default_branch, safe='')}"
        ),
        "rulesets": rulesets,
        "codeowners_text": codeowners_text,
        "codeowners_errors": client.get_json(
            base + "/codeowners/errors", {"ref": default_branch}
        ),
        "teams": teams,
        "pull_request": client.get_json(base + f"/pulls/{pull_number}"),
        "reviews": client.get_json(
            base + f"/pulls/{pull_number}/reviews", {"per_page": "100"}
        ),
        "head_check_runs": client.get_json(
            base + f"/commits/{expected_head_sha}/check-runs",
            {"per_page": "100"},
        ),
        "merge_group_check_runs": client.get_json(
            base + f"/commits/{merge_group_sha}/check-runs",
            {"per_page": "100"},
        ),
    }


def _write_receipt(path: Path, receipt: dict[str, Any]) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=path.name + ".", dir=path.parent
    )
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(_canonical_bytes(receipt) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pull-number", required=True, type=int)
    parser.add_argument("--expected-head-sha", required=True)
    parser.add_argument("--merge-group-sha", required=True)
    parser.add_argument("--token-env", default="HEPTA_GOVERNANCE_TOKEN")
    parser.add_argument("--api-base", default="https://api.github.com")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)

    token = os.environ.get(args.token_env, "")
    if not token:
        print(
            f"[GITHUB-GOVERNANCE] missing token environment {args.token_env}",
            file=sys.stderr,
        )
        return 2
    try:
        policy, contexts = load_policy()
        client = GitHubClient(token=token, api_base=args.api_base)
        snapshot = collect_snapshot(
            client,
            policy,
            args.pull_number,
            args.expected_head_sha,
            args.merge_group_sha,
        )
        errors = validate_snapshot(
            policy,
            contexts,
            snapshot,
            args.expected_head_sha,
            args.merge_group_sha,
        )
    except (
        GovernanceError,
        OSError,
        UnicodeError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        print(
            f"[GITHUB-GOVERNANCE] evidence collection failed: {exc}",
            file=sys.stderr,
        )
        return 2
    for error in errors:
        print(f"[GITHUB-GOVERNANCE] {error}", file=sys.stderr)
    if errors:
        return 1

    ruleset = _main_ruleset(policy, snapshot["rulesets"], [])
    team_slugs = sorted(snapshot["teams"])
    body = {
        "schema": "heptatrader.github-governance-receipt.v1",
        "verified_at": datetime.now(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
        "repository": policy["repository"],
        "default_branch": policy["default_branch"],
        "pull_number": args.pull_number,
        "head_sha": args.expected_head_sha,
        "merge_group_sha": args.merge_group_sha,
        "ruleset_id": ruleset.get("id") if isinstance(ruleset, dict) else None,
        "team_slugs": team_slugs,
        "required_pull_request_contexts": contexts[
            "required_pull_request_contexts"
        ],
        "required_merge_group_contexts": contexts[
            "required_merge_group_contexts"
        ],
        "api_response_digests": dict(sorted(client.response_digests.items())),
    }
    receipt = {"body": body, "receipt_sha256": _digest(body)}
    _write_receipt(args.output, receipt)
    print("[GITHUB-GOVERNANCE] PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
