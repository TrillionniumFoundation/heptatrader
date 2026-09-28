#!/usr/bin/env python3
"""Verify the exact-head evidence profile behind one always-present admission check."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from ci_change_scope import (
    changed_paths as discover_changed_paths,
    is_documentation_only_pull_request,
)
from source_json import load_source_json

SHA_RE = re.compile(r"[0-9a-f]{40}")
DETAILS_RE = re.compile(r"/actions/runs/([0-9]+)/job/([0-9]+)(?:$|[/?#])")
SUCCESS = "success"
PENDING = "pending"
SOURCE_CONTEXT = "documentation-control-plane-exact-head"
CORE_CONTEXT = "core-runtime-exact-head"
GCC_CONTEXT = "canonical-full-suite-reliability (g++)"
CLANG_CONTEXT = "canonical-full-suite-reliability (clang++)"
ADMISSION_CONTEXT = "canonical-admission-exact-head"
EXPECTED_PROFILES = {
    "documentation_only_pull_request": [SOURCE_CONTEXT],
    "full": [SOURCE_CONTEXT, CORE_CONTEXT, GCC_CONTEXT, CLANG_CONTEXT],
}
EXPECTED_PROVENANCE = {
    SOURCE_CONTEXT: {
        "workflow_id": 346626270,
        "workflow_path": ".github/workflows/documentation-control-plane.yml",
        "job_name": SOURCE_CONTEXT,
    },
    CORE_CONTEXT: {
        "workflow_id": 345160803,
        "workflow_path": ".github/workflows/core-ci.yml",
        "job_name": CORE_CONTEXT,
    },
    GCC_CONTEXT: {
        "workflow_id": 346064356,
        "workflow_path": ".github/workflows/canonical-full-suite.yml",
        "job_name": GCC_CONTEXT,
    },
    CLANG_CONTEXT: {
        "workflow_id": 346064356,
        "workflow_path": ".github/workflows/canonical-full-suite.yml",
        "job_name": CLANG_CONTEXT,
    },
    ADMISSION_CONTEXT: {
        "workflow_id": 346626270,
        "workflow_path": ".github/workflows/documentation-control-plane.yml",
        "job_name": ADMISSION_CONTEXT,
    },
}


def profile_for(event_name: str, paths: list[str]) -> str:
    if is_documentation_only_pull_request(paths, event_name):
        return "documentation_only_pull_request"
    return "full"


def _git(root: Path, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", *arguments], cwd=root, check=check, capture_output=True, timeout=120
    )



def load_policy(path: Path) -> dict[str, Any]:
    value = load_source_json(path)
    if not isinstance(value, dict) or value.get("schema") != "heptatrader.required-check-contexts.v1":
        raise ValueError("unsupported required-check policy")
    policy = value.get("policy")
    if not isinstance(policy, dict):
        raise ValueError("required-check policy flags are missing")
    for flag in (
        "required_contexts_must_be_event_reachable",
        "skipped_or_missing_is_not_success",
        "all_pages_must_be_collected",
        "check_run_must_bind_workflow_and_non_empty_job",
    ):
        if policy.get(flag) is not True:
            raise ValueError(f"required-check policy flag is not enabled: {flag}")
    required = [ADMISSION_CONTEXT]
    for field in (
        "required_branch_contexts",
        "required_pull_request_contexts",
        "required_merge_group_contexts",
    ):
        if value.get(field) != required:
            raise ValueError(f"{field} must require only canonical admission")
    owner = value.get("required_check_provenance")
    if not isinstance(owner, dict) or owner.get("integration_id") != 15368:
        raise ValueError("required-check integration identity is invalid")
    if owner.get("contexts") != EXPECTED_PROVENANCE:
        raise ValueError("required-check workflow provenance differs from the reviewed set")
    if value.get("admission_profiles") != EXPECTED_PROFILES:
        raise ValueError("admission profiles differ from the reviewed evidence set")
    return value


def api_base(server_url: str) -> str:
    parsed = urlparse(server_url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("HTTPS GitHub server URL is required")
    if parsed.netloc == "github.com":
        return "https://api.github.com"
    return server_url.rstrip("/") + "/api/v3"


class GitHubApi:
    def __init__(self, base: str, token: str) -> None:
        if not token:
            raise ValueError("GH_TOKEN is required")
        self.base = base.rstrip("/")
        self.token = token

    def get(self, path_or_url: str) -> tuple[Any, dict[str, str]]:
        if path_or_url.startswith("https://"):
            if path_or_url != self.base and not path_or_url.startswith(self.base + "/"):
                raise ValueError("GitHub API pagination escaped the admitted API base")
            url = path_or_url
        elif path_or_url.startswith("/"):
            url = self.base + path_or_url
        else:
            raise ValueError("GitHub API path must be absolute")
        request = Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "heptatrader-canonical-admission",
            },
        )
        try:
            with urlopen(request, timeout=30) as response:
                raw = response.read(16 * 1024 * 1024 + 1)
                if len(raw) > 16 * 1024 * 1024:
                    raise ValueError("GitHub API response exceeds bound")
                return json.loads(raw), {key.lower(): value for key, value in response.headers.items()}
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as error:
            raise ValueError(f"GitHub API read failed: {error}") from error

    def check_runs(self, repository: str, head_sha: str) -> list[dict[str, Any]]:
        url = f"{self.base}/repos/{repository}/commits/{head_sha}/check-runs?filter=latest&per_page=100"
        output: list[dict[str, Any]] = []
        for _ in range(20):
            value, headers = self.get(url)
            rows = value.get("check_runs") if isinstance(value, dict) else None
            if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
                raise ValueError("malformed check-runs response")
            output.extend(rows)
            next_url = next_link(headers.get("link", ""))
            if not next_url:
                return output
            url = next_url
        raise ValueError("check-runs pagination exceeded bound")


def next_link(header: str) -> str:
    for item in header.split(","):
        match = re.fullmatch(r'\s*<([^>]+)>;\s*rel="([^"]+)"\s*', item)
        if match and match.group(2) == "next":
            return match.group(1)
    return ""


def _latest_correct_run(
    check_runs: list[dict[str, Any]], context: str, integration_id: int
) -> dict[str, Any] | None:
    candidates = [
        row for row in check_runs
        if row.get("name") == context
        and isinstance(row.get("app"), dict)
        and row["app"].get("id") == integration_id
    ]
    return max(candidates, key=lambda row: row.get("id", -1), default=None)


def verify_context(
    context: str,
    check_runs: list[dict[str, Any]],
    integration_id: int,
    provenance: dict[str, Any],
    head_sha: str,
    fetch_job: Callable[[int], dict[str, Any]],
    fetch_run: Callable[[int], dict[str, Any]],
) -> str:
    check = _latest_correct_run(check_runs, context, integration_id)
    if check is None:
        return PENDING
    if check.get("head_sha") != head_sha:
        raise ValueError(f"{context} check-run source drift")
    status = check.get("status")
    conclusion = check.get("conclusion")
    if status != "completed":
        return PENDING
    if conclusion != SUCCESS:
        raise ValueError(f"{context} completed with {conclusion!r}")
    details = check.get("details_url")
    match = DETAILS_RE.search(details) if isinstance(details, str) else None
    if not match:
        raise ValueError(f"{context} has no bound Actions job URL")
    run_id, job_id = map(int, match.groups())
    job = fetch_job(job_id)
    run = fetch_run(run_id)
    expected = provenance.get(context)
    if not isinstance(job, dict) or not isinstance(run, dict) or not isinstance(expected, dict):
        raise ValueError(f"{context} provenance response is malformed")
    if (
        job.get("id") != job_id
        or job.get("run_id") != run_id
        or job.get("name") != context
        or job.get("head_sha") != head_sha
        or job.get("status") != "completed"
        or job.get("conclusion") != SUCCESS
    ):
        raise ValueError(f"{context} job identity/source/result drift")
    # The source job and canonical admission share one workflow run, so that run
    # is necessarily still in progress while admission executes. Bind the exact
    # run identity, workflow and source here; the required job result above owns
    # the behavior-bearing success fact.
    if (
        run.get("id") != run_id
        or run.get("head_sha") != head_sha
        or run.get("workflow_id") != expected.get("workflow_id")
        or run.get("path") != expected.get("workflow_path")
    ):
        raise ValueError(f"{context} workflow/source provenance drift")
    return SUCCESS


def wait_for_profile(
    api: GitHubApi,
    repository: str,
    head_sha: str,
    contexts: list[str],
    policy: dict[str, Any],
    timeout_seconds: int,
    poll_seconds: int,
) -> None:
    provenance = policy["required_check_provenance"]["contexts"]
    integration_id = policy["required_check_provenance"]["integration_id"]
    deadline = time.monotonic() + timeout_seconds
    last_pending: tuple[str, ...] | None = None
    while True:
        rows = api.check_runs(repository, head_sha)
        pending: list[str] = []
        for context in contexts:
            state = verify_context(
                context,
                rows,
                integration_id,
                provenance,
                head_sha,
                lambda job_id: api.get(f"/repos/{repository}/actions/jobs/{job_id}")[0],
                lambda run_id: api.get(f"/repos/{repository}/actions/runs/{run_id}")[0],
            )
            if state == PENDING:
                pending.append(context)
        if not pending:
            return
        current = tuple(pending)
        if current != last_pending:
            print("Waiting for exact-head contexts: " + ", ".join(pending), flush=True)
            last_pending = current
        if time.monotonic() >= deadline:
            raise ValueError("timed out waiting for exact-head contexts: " + ", ".join(pending))
        time.sleep(poll_seconds)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--policy", type=Path, default=Path(".github/required-check-contexts-v1.json"))
    parser.add_argument("--event-name", required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--base-sha", default="")
    parser.add_argument("--base-repository", default="")
    parser.add_argument("--server-url", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--source-result", required=True)
    parser.add_argument("--timeout-seconds", type=int, default=3600)
    parser.add_argument("--poll-seconds", type=int, default=10)
    args = parser.parse_args(argv)
    try:
        root = args.root.resolve()
        if args.source_result != SUCCESS:
            raise ValueError(f"source owner job result is {args.source_result!r}")
        if not SHA_RE.fullmatch(args.head_sha):
            raise ValueError("exact 40-hex head SHA is required")
        actual = _git(root, "rev-parse", "HEAD").stdout.decode().strip()
        if actual != args.head_sha:
            raise ValueError("checkout does not match exact head SHA")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.repository):
            raise ValueError("invalid repository identity")
        if not (1 <= args.poll_seconds <= 60 and 1 <= args.timeout_seconds <= 3600):
            raise ValueError("poll/timeout bounds are invalid")
        policy_path = args.policy if args.policy.is_absolute() else root / args.policy
        policy = load_policy(policy_path)
        paths = discover_changed_paths(
            root, args.event_name, args.base_sha, args.base_repository, args.server_url
        )
        profile = profile_for(args.event_name, paths)
        contexts = policy["admission_profiles"][profile]
        print(f"Admission profile={profile}; exact_head={args.head_sha}; contexts={','.join(contexts)}")
        api = GitHubApi(api_base(args.server_url), os.environ.get("GH_TOKEN", ""))
        wait_for_profile(
            api, args.repository, args.head_sha, contexts, policy,
            args.timeout_seconds, args.poll_seconds,
        )
        print("[CANONICAL-ADMISSION] PASS exact-head evidence; no Broker authority")
        return 0
    except (OSError, UnicodeError, ValueError, subprocess.SubprocessError) as error:
        print(f"[CANONICAL-ADMISSION] FAIL: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
