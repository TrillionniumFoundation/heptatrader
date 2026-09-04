#!/usr/bin/env python3
"""Admit an exact reviewed PR head as qualification data.

The verifier is trusted-main code. It never imports or executes candidate code.
It follows pagination, reduces every reviewer to the latest exact-head decisive
state, and binds each accepted context to the configured GitHub Actions app,
workflow id/path, current attempt and a non-empty successful job.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Any
from urllib.parse import quote

from github_qualification_evidence import (
    GitHubEvidenceError,
    GitHubReader,
    canonical_bytes,
    canonical_digest,
    collect_check_evidence,
    context_specs,
    review_state_projection,
    strict_object_pairs,
    validate_context_evidence,
    validate_reviews,
)

ROOT = Path(__file__).resolve().parents[1]
CONTEXTS_REL = Path(".github/required-check-contexts-v1.json")
POLICY_REL = Path(".github/github-governance-policy-v1.json")
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
RECEIPT_SCHEMA = "heptatrader.qualification-candidate-admission.v2"
PAIR_SCHEMA = "heptatrader.qualification-admission-pair.v1"


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=strict_object_pairs)
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected object")
    return value


def load_policy(root: Path = ROOT) -> tuple[dict[str, Any], dict[str, Any], list[str], int, int, dict[str, dict[str, Any]]]:
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
    minimum = approval.get("minimum_non_author_approvals_on_exact_head") if isinstance(approval, dict) else None
    if not isinstance(minimum, int) or isinstance(minimum, bool) or minimum < 1:
        raise ValueError("minimum exact-head approval policy is invalid")
    provenance = contexts.get("required_check_provenance")
    integration_id = provenance.get("integration_id") if isinstance(provenance, dict) else None
    if not isinstance(integration_id, int) or isinstance(integration_id, bool) or integration_id <= 0:
        raise ValueError("required check integration id is invalid")
    specs = context_specs(contexts)
    for name in required:
        if name not in specs:
            raise ValueError(f"missing provenance for required context {name}")
    return governance, contexts, list(required), minimum, integration_id, specs


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
    workflow_runs_payload: Any | None = None,
    jobs_by_run: Any | None = None,
    specs: dict[str, dict[str, Any]] | None = None,
    integration_id: int = 15368,
) -> tuple[list[str], dict[str, Any]]:
    errors: list[str] = []
    projection: dict[str, Any] = {}
    if FULL_SHA.fullmatch(candidate_sha) is None:
        errors.append("candidate SHA is not canonical")
    if not isinstance(pull, dict):
        return errors + ["pull-request evidence is missing"], projection
    if pull.get("number") != pull_number:
        errors.append("pull-request number mismatch")
    if pull.get("state") != "open" or pull.get("merged") is True:
        errors.append("qualification candidate pull request is not open and unmerged")
    head = pull.get("head")
    base = pull.get("base")
    author = pull.get("user")
    head_sha = head.get("sha") if isinstance(head, dict) else None
    head_repo = head.get("repo") if isinstance(head, dict) else None
    base_ref = base.get("ref") if isinstance(base, dict) else None
    base_sha = base.get("sha") if isinstance(base, dict) else None
    base_repo = base.get("repo") if isinstance(base, dict) else None
    author_login = author.get("login") if isinstance(author, dict) else None
    head_repo_name = head_repo.get("full_name") if isinstance(head_repo, dict) else None
    base_repo_name = base_repo.get("full_name") if isinstance(base_repo, dict) else None
    if head_sha != candidate_sha:
        errors.append("candidate SHA is not the current pull-request head")
    if head_repo_name != repository or base_repo_name != repository:
        errors.append("qualification candidate must use the canonical repository")
    if base_ref != default_branch:
        errors.append("qualification candidate does not target the default branch")
    if not isinstance(base_sha, str) or FULL_SHA.fullmatch(base_sha) is None:
        errors.append("pull-request base SHA is missing or non-canonical")
    if not isinstance(author_login, str) or not author_login:
        errors.append("pull-request author identity is missing")
        author_login = ""

    validate_reviews(
        reviews_payload,
        candidate_sha,
        author_login,
        minimum_approvals,
        errors,
        label="qualification candidate",
    )

    evidence = {
        "check_runs": checks_payload,
        "workflow_runs": workflow_runs_payload,
        "jobs_by_run": jobs_by_run,
    }
    selected: dict[str, dict[str, Any]] = {}
    if specs is None:
        errors.append("qualification candidate: check provenance specification is missing")
    else:
        selected = validate_context_evidence(
            evidence,
            candidate_sha,
            "pull_request",
            required_contexts,
            specs,
            integration_id,
            "qualification candidate source head",
            errors,
        )

    projection = {
        "repository": repository,
        "pull_number": pull_number,
        "state": pull.get("state"),
        "draft": pull.get("draft"),
        "author": author_login,
        "head_sha": head_sha,
        "base_ref": base_ref,
        "base_sha": base_sha,
        "reviews": review_state_projection(reviews_payload, candidate_sha),
        "required_contexts": [selected[name] for name in required_contexts if name in selected],
    }
    return errors, projection


def collect_snapshot(
    reader: GitHubReader,
    repository: str,
    pull_number: int,
    candidate_sha: str,
) -> tuple[Any, Any, dict[str, Any]]:
    owner, repo = repository.split("/", 1)
    base = f"/repos/{quote(owner, safe='')}/{quote(repo, safe='')}"
    pull = reader.get_json(base + f"/pulls/{pull_number}")
    reviews = reader.get_paginated(base + f"/pulls/{pull_number}/reviews")
    evidence = collect_check_evidence(reader, base, candidate_sha, "pull_request")
    return pull, reviews, evidence


def _write_private_json(path: Path, value: dict[str, Any]) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(canonical_bytes(value) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        if path.exists() or path.is_symlink():
            raise FileExistsError(f"receipt output already exists: {path}")
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise


def _read_receipt(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file() or path.stat().st_nlink != 1:
        raise ValueError(f"admission receipt is not a regular single-link file: {path}")
    value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=strict_object_pairs)
    if not isinstance(value, dict) or set(value) != {"body", "receipt_sha256"}:
        raise ValueError("admission receipt envelope is invalid")
    body = value.get("body")
    if not isinstance(body, dict) or body.get("schema") != RECEIPT_SCHEMA:
        raise ValueError("admission receipt schema is invalid")
    if value.get("receipt_sha256") != canonical_digest(body):
        raise ValueError("admission receipt digest mismatch")
    return value


def compare_admission_receipts(before_path: Path, after_path: Path) -> dict[str, Any]:
    before = _read_receipt(before_path)
    after = _read_receipt(after_path)
    before_body = before["body"]
    after_body = after["body"]
    identity_fields = ("repository", "pull_number", "candidate_sha", "base_sha", "required_contexts")
    for field in identity_fields:
        if before_body.get(field) != after_body.get(field):
            raise ValueError(f"admission identity changed during protected campaign: {field}")
    if before_body.get("admission_state_sha256") != after_body.get("admission_state_sha256"):
        raise ValueError("review/check/PR admission state changed during protected campaign")
    body = {
        "schema": PAIR_SCHEMA,
        "repository": before_body["repository"],
        "pull_number": before_body["pull_number"],
        "candidate_sha": before_body["candidate_sha"],
        "base_sha": before_body["base_sha"],
        "admission_state_sha256": before_body["admission_state_sha256"],
        "before_receipt_sha256": before["receipt_sha256"],
        "after_receipt_sha256": after["receipt_sha256"],
    }
    return {"body": body, "receipt_sha256": canonical_digest(body)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pull-number", type=int)
    parser.add_argument("--candidate-sha")
    parser.add_argument("--token-env", default="GITHUB_TOKEN")
    parser.add_argument("--api-base", default="https://api.github.com")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--compare-before", type=Path)
    parser.add_argument("--compare-after", type=Path)
    parser.add_argument("--comparison-output", type=Path)
    args = parser.parse_args(argv)

    if args.compare_before is not None or args.compare_after is not None:
        if args.compare_before is None or args.compare_after is None or args.comparison_output is None:
            parser.error("comparison requires --compare-before, --compare-after and --comparison-output")
        try:
            pair = compare_admission_receipts(args.compare_before, args.compare_after)
            _write_private_json(args.comparison_output, pair)
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
            print(f"[QUALIFICATION-CANDIDATE] admission comparison failed: {exc}", file=sys.stderr)
            return 1
        print("[QUALIFICATION-CANDIDATE] PRE/POST ADMISSION MATCH")
        return 0

    if args.pull_number is None or args.candidate_sha is None:
        parser.error("verification requires --pull-number and --candidate-sha")
    token = os.environ.get(args.token_env, "")
    if not token:
        print(f"[QUALIFICATION-CANDIDATE] missing token environment {args.token_env}", file=sys.stderr)
        return 2
    try:
        policy, contexts, required, minimum, integration_id, specs = load_policy(ROOT)
        repository = policy.get("repository")
        default_branch = policy.get("default_branch")
        if not isinstance(repository, str) or "/" not in repository:
            raise ValueError("policy.repository is invalid")
        if not isinstance(default_branch, str) or not default_branch:
            raise ValueError("policy.default_branch is invalid")
        reader = GitHubReader(token=token, api_base=args.api_base)
        pull, reviews, evidence = collect_snapshot(reader, repository, args.pull_number, args.candidate_sha)
        errors, projection = validate_snapshot(
            repository,
            args.pull_number,
            args.candidate_sha,
            default_branch,
            required,
            minimum,
            pull,
            reviews,
            evidence["check_runs"],
            evidence["workflow_runs"],
            evidence["jobs_by_run"],
            specs,
            integration_id,
        )
    except (GitHubEvidenceError, OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        print(f"[QUALIFICATION-CANDIDATE] evidence collection failed: {exc}", file=sys.stderr)
        return 2
    for error in errors:
        print(f"[QUALIFICATION-CANDIDATE] {error}", file=sys.stderr)
    if errors:
        return 1
    if args.output is not None:
        body = {
            "schema": RECEIPT_SCHEMA,
            "verified_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "repository": repository,
            "pull_number": args.pull_number,
            "candidate_sha": args.candidate_sha,
            "base_sha": projection["base_sha"],
            "required_contexts": required,
            "admission_state_sha256": canonical_digest(projection),
            "api_response_digests": dict(sorted(reader.response_digests.items())),
        }
        receipt = {"body": body, "receipt_sha256": canonical_digest(body)}
        _write_private_json(args.output, receipt)
    print("[QUALIFICATION-CANDIDATE] PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
