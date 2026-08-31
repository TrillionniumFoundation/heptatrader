#!/usr/bin/env python3
"""Fail closed unless an exact main commit has the complete successful CI set."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
REQUIRED_JOBS = frozenset(
    {
        "repository-contracts",
        "core (gcc, Debug)",
        "core (gcc, Release)",
        "core (clang, Debug)",
        "core (clang, Release)",
        "asan-ubsan",
        "package",
    }
)


def successful_job_names(payload: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    for job in payload.get("jobs", []):
        if not isinstance(job, dict):
            continue
        name = job.get("name")
        if (
            isinstance(name, str)
            and job.get("status") == "completed"
            and job.get("conclusion") == "success"
        ):
            names.add(name)
    return names


def eligible_runs(sha: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    for run in payload.get("workflow_runs", []):
        if not isinstance(run, dict):
            continue
        if (
            run.get("head_sha") == sha
            and run.get("head_branch") == "main"
            and run.get("event") == "push"
            and run.get("status") == "completed"
            and run.get("conclusion") == "success"
            and isinstance(run.get("id"), int)
        ):
            result.append(run)
    return sorted(result, key=lambda item: int(item["id"]), reverse=True)


def validate_candidate(
    sha: str,
    main_sha: str,
    runs_payload: dict[str, Any],
    jobs_by_run: dict[int, dict[str, Any]],
) -> list[str]:
    errors: list[str] = []
    if FULL_SHA.fullmatch(sha) is None:
        errors.append(f"candidate SHA is not canonical: {sha!r}")
    if FULL_SHA.fullmatch(main_sha) is None:
        errors.append(f"main SHA is not canonical: {main_sha!r}")
    if errors:
        return errors
    if sha != main_sha:
        return [f"candidate {sha} is not the exact current main commit {main_sha}"]

    runs = eligible_runs(sha, runs_payload)
    if not runs:
        return ["no successful main push run of .github/workflows/ci.yml exists for candidate"]

    missing_by_run: list[str] = []
    for run in runs:
        run_id = int(run["id"])
        successful = successful_job_names(jobs_by_run.get(run_id, {}))
        missing = sorted(REQUIRED_JOBS - successful)
        if not missing:
            return []
        missing_by_run.append(f"run {run_id}: {', '.join(missing)}")
    return ["no CI run contains the complete required successful job set; " + "; ".join(missing_by_run)]


def fetch_json(url: str, token: str) -> dict[str, Any]:
    request = Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "heptatrader-release-verifier",
        },
    )
    try:
        with urlopen(request, timeout=30) as response:
            payload = json.load(response)
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as error:
        raise RuntimeError(f"GitHub API request failed for {url}: {error}") from error
    if not isinstance(payload, dict):
        raise RuntimeError(f"GitHub API returned a non-object for {url}")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--main-sha", required=True)
    parser.add_argument("--api-url", default="https://api.github.com")
    parser.add_argument("--token-env", default="GITHUB_TOKEN")
    args = parser.parse_args()

    repository = args.repository.strip()
    sha = args.sha.strip().lower()
    main_sha = args.main_sha.strip().lower()
    if REPOSITORY.fullmatch(repository) is None:
        print(f"ERROR: invalid repository identifier: {repository!r}", file=sys.stderr)
        return 2
    token = os.environ.get(args.token_env, "")
    if not token:
        print(f"ERROR: {args.token_env} is required", file=sys.stderr)
        return 2

    base = args.api_url.rstrip("/")
    workflow = quote("ci.yml", safe="")
    query = urlencode(
        {
            "head_sha": sha,
            "branch": "main",
            "event": "push",
            "status": "success",
            "per_page": "100",
        }
    )
    try:
        runs_payload = fetch_json(
            f"{base}/repos/{repository}/actions/workflows/{workflow}/runs?{query}",
            token,
        )
        jobs_by_run: dict[int, dict[str, Any]] = {}
        for run in eligible_runs(sha, runs_payload):
            run_id = int(run["id"])
            jobs_by_run[run_id] = fetch_json(
                f"{base}/repos/{repository}/actions/runs/{run_id}/jobs?per_page=100",
                token,
            )
    except RuntimeError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    errors = validate_candidate(sha, main_sha, runs_payload, jobs_by_run)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"release CI qualification PASS: repository={repository} sha={sha}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
