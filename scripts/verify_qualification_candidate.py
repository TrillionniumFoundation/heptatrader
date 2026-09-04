#!/usr/bin/env python3
"""Admit an exact PR head as qualification input without executing candidate code.

This trusted-main verifier reads only GitHub API data. It requires the requested
SHA to be the current head of an open pull request targeting main, all canonical
source-head contexts to be terminal-success, and current exact-head non-author
review state to contain the required approvals and no change request.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
CONTEXTS_REL = Path(".github/required-check-contexts-v1.json")
POLICY_REL = Path(".github/github-governance-policy-v1.json")
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")


class AdmissionError(RuntimeError):
    """Raised when live GitHub evidence cannot be collected safely."""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(
        path.read_text(encoding="utf-8-sig"), object_pairs_hook=_unique_object
    )
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected object")
    return value


def _canonical_digest(value: Any) -> str:
    data = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(data).hexdigest()


@dataclass
class GitHubReader:
    token: str
    api_base: str = "https://api.github.com"
    timeout_seconds: int = 30

    def __post_init__(self) -> None:
        self.api_base = self.api_base.rstrip("/")
        self.response_digests: dict[str, str] = {}

    def get_json(self, path: str, query: dict[str, str] | None = None) -> Any:
        if not path.startswith("/"):
            raise AdmissionError(f"unsafe GitHub API path: {path}")
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
                "User-Agent": "heptatrader-qualification-admission/1",
            },
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                payload = response.read()
                status = response.status
        except HTTPError as exc:
            detail = exc.read(4096).decode("utf-8", errors="replace")
            raise AdmissionError(
                f"GitHub API GET {path} failed with HTTP {exc.code}: {detail}"
            ) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise AdmissionError(f"GitHub API GET {path} failed: {exc}") from exc
        if status != 200:
            raise AdmissionError(f"GitHub API GET {path} returned HTTP {status}")
        try:
            value = json.loads(
                payload.decode("utf-8"), object_pairs_hook=_unique_object
            )
        except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
            raise AdmissionError(
                f"GitHub API GET {path} returned invalid JSON: {exc}"
            ) from exc
        key = path + ("?" + urlencode(query) if query else "")
        self.response_digests[key] = _canonical_digest(value)
        return value


def load_policy(root: Path = ROOT) -> tuple[dict[str, Any], list[str], int]:
    governance = _load(Path(root) / POLICY_REL)
    contexts = _load(Path(root) / CONTEXTS_REL)
    if governance.get("schema") != "heptatrader.github-governance-policy.v1":
        raise ValueError("governance policy schema mismatch")
    if contexts.get("schema") != "heptatrader.required-check-contexts.v1":
        raise ValueError("required-context policy schema mismatch")
    required = contexts.get("required_pull_request_contexts")
    if (
        not isinstance(required, list)
        or not required
        or any(not isinstance(item, str) or not item for item in required)
        or len(required) != len(set(required))
    ):
        raise ValueError("required pull-request contexts are invalid")
    approval = governance.get("approval")
    minimum = (
        approval.get("minimum_non_author_approvals_on_exact_head")
        if isinstance(approval, dict)
        else None
    )
    if not isinstance(minimum, int) or isinstance(minimum, bool) or minimum < 1:
        raise ValueError("minimum exact-head approval policy is invalid")
    return governance, list(required), minimum


def _latest_check(checks: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    matching = [item for item in checks if item.get("name") == name]
    if not matching:
        return None
    return max(
        matching,
        key=lambda item: (
            item.get("id") if isinstance(item.get("id"), int) else -1,
            item.get("completed_at") or "",
            item.get("started_at") or "",
        ),
    )


def _latest_reviews(reviews: list[dict[str, Any]], candidate_sha: str) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for review in reviews:
        if review.get("commit_id") != candidate_sha:
            continue
        user = review.get("user")
        login = user.get("login") if isinstance(user, dict) else None
        if not isinstance(login, str) or not login:
            continue
        current = latest.get(login)
        key = (
            review.get("submitted_at") or "",
            review.get("id") if isinstance(review.get("id"), int) else -1,
        )
        current_key = (
            current.get("submitted_at") or "",
            current.get("id") if isinstance(current, dict) and isinstance(current.get("id"), int) else -1,
        ) if current is not None else ("", -1)
        if current is None or key > current_key:
            latest[login] = review
    return latest


def validate_snapshot(
    repository: str,
    pull_number: int,
    candidate_sha: str,
    default_branch: str,
    required_contexts: list[str],
    minimum_approvals: int,
    pull: Any,
    reviews_payload: Any,
    checks_payload: Any,
) -> list[str]:
    errors: list[str] = []
    if not FULL_SHA.fullmatch(candidate_sha):
        errors.append("candidate SHA is not canonical")
    if not isinstance(pull, dict):
        return errors + ["pull-request evidence is missing"]
    if pull.get("number") != pull_number:
        errors.append("pull-request number mismatch")
    if pull.get("state") != "open" or pull.get("merged") is True:
        errors.append("qualification candidate pull request is not open and unmerged")
    head = pull.get("head")
    base = pull.get("base")
    author = pull.get("user")
    head_sha = head.get("sha") if isinstance(head, dict) else None
    head_repo = head.get("repo") if isinstance(head, dict) else None
    head_repo_name = head_repo.get("full_name") if isinstance(head_repo, dict) else None
    base_ref = base.get("ref") if isinstance(base, dict) else None
    base_repo = base.get("repo") if isinstance(base, dict) else None
    base_repo_name = base_repo.get("full_name") if isinstance(base_repo, dict) else None
    author_login = author.get("login") if isinstance(author, dict) else None
    if head_sha != candidate_sha:
        errors.append("candidate SHA is not the current pull-request head")
    if head_repo_name != repository or base_repo_name != repository:
        errors.append("qualification candidate must use the canonical repository")
    if base_ref != default_branch:
        errors.append("qualification candidate does not target the default branch")
    if not isinstance(author_login, str) or not author_login:
        errors.append("pull-request author identity is missing")

    if not isinstance(reviews_payload, list):
        errors.append("review evidence is missing")
        reviews: list[dict[str, Any]] = []
    else:
        reviews = [item for item in reviews_payload if isinstance(item, dict)]
    current = _latest_reviews(reviews, candidate_sha)
    blockers = sorted(
        login
        for login, review in current.items()
        if review.get("state") == "CHANGES_REQUESTED"
    )
    if blockers:
        errors.append(
            "current exact-head change requests remain: " + ", ".join(blockers)
        )
    approvers = {
        login
        for login, review in current.items()
        if review.get("state") == "APPROVED" and login != author_login
    }
    if len(approvers) < minimum_approvals:
        errors.append(
            f"exact-head non-author approvals below policy: {len(approvers)} < {minimum_approvals}"
        )

    if not isinstance(checks_payload, dict) or not isinstance(
        checks_payload.get("check_runs"), list
    ):
        errors.append("check-run evidence is missing")
        checks: list[dict[str, Any]] = []
    else:
        checks = [
            item for item in checks_payload["check_runs"] if isinstance(item, dict)
        ]
    for context in required_contexts:
        check = _latest_check(checks, context)
        if check is None:
            errors.append(f"missing source-head check: {context}")
            continue
        if check.get("head_sha") != candidate_sha:
            errors.append(f"source-head check bound to another SHA: {context}")
        if check.get("status") != "completed" or check.get("conclusion") != "success":
            errors.append(f"source-head check is not terminal success: {context}")
    return errors


def collect_snapshot(
    reader: GitHubReader,
    repository: str,
    pull_number: int,
    candidate_sha: str,
) -> tuple[Any, Any, Any]:
    owner, repo = repository.split("/", 1)
    base = f"/repos/{quote(owner, safe='')}/{quote(repo, safe='')}"
    pull = reader.get_json(base + f"/pulls/{pull_number}")
    reviews = reader.get_json(
        base + f"/pulls/{pull_number}/reviews", {"per_page": "100"}
    )
    checks = reader.get_json(
        base + f"/commits/{candidate_sha}/check-runs", {"per_page": "100"}
    )
    return pull, reviews, checks


def _write_receipt(path: Path, value: dict[str, Any]) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(
            json.dumps(
                value,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ).encode("utf-8")
            + b"\n"
        )
        stream.flush()
        os.fsync(stream.fileno())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pull-number", required=True, type=int)
    parser.add_argument("--candidate-sha", required=True)
    parser.add_argument("--token-env", default="GITHUB_TOKEN")
    parser.add_argument("--api-base", default="https://api.github.com")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    token = os.environ.get(args.token_env, "")
    if not token:
        print(
            f"[QUALIFICATION-CANDIDATE] missing token environment {args.token_env}",
            file=sys.stderr,
        )
        return 2
    try:
        policy, required_contexts, minimum = load_policy(ROOT)
        repository = policy.get("repository")
        default_branch = policy.get("default_branch")
        if not isinstance(repository, str) or "/" not in repository:
            raise ValueError("policy.repository is invalid")
        if not isinstance(default_branch, str) or not default_branch:
            raise ValueError("policy.default_branch is invalid")
        reader = GitHubReader(token=token, api_base=args.api_base)
        pull, reviews, checks = collect_snapshot(
            reader, repository, args.pull_number, args.candidate_sha
        )
        errors = validate_snapshot(
            repository,
            args.pull_number,
            args.candidate_sha,
            default_branch,
            required_contexts,
            minimum,
            pull,
            reviews,
            checks,
        )
    except (AdmissionError, OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        print(f"[QUALIFICATION-CANDIDATE] evidence collection failed: {exc}", file=sys.stderr)
        return 2
    for error in errors:
        print(f"[QUALIFICATION-CANDIDATE] {error}", file=sys.stderr)
    if errors:
        return 1
    if args.output is not None:
        body = {
            "schema": "heptatrader.qualification-candidate-admission.v1",
            "repository": repository,
            "pull_number": args.pull_number,
            "candidate_sha": args.candidate_sha,
            "required_contexts": required_contexts,
            "api_response_digests": dict(sorted(reader.response_digests.items())),
        }
        receipt = {"body": body, "receipt_sha256": _canonical_digest(body)}
        _write_receipt(args.output, receipt)
    print("[QUALIFICATION-CANDIDATE] PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
