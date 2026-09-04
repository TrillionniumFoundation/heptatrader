#!/usr/bin/env python3
"""Trusted GitHub evidence collection and exact check/review reduction.

This module is executed only from the trusted default-branch qualification
harness. It treats pull-request, review, check-run, workflow-run and job data as
untrusted API input and fails closed on pagination, provenance or identity
ambiguity.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


DETAILS_RE = re.compile(r"^https://github\.com/[^/]+/[^/]+/actions/runs/([0-9]+)/job/([0-9]+)(?:\?.*)?$")
DECISIVE_REVIEW_STATES = frozenset({"APPROVED", "CHANGES_REQUESTED", "DISMISSED"})


class GitHubEvidenceError(RuntimeError):
    """Raised when live GitHub evidence cannot be collected unambiguously."""


def strict_object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def canonical_digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_bytes(value)).hexdigest()


def _parse_link(value: str | None) -> dict[str, str]:
    links: dict[str, str] = {}
    if not value:
        return links
    for item in value.split(","):
        sections = [part.strip() for part in item.split(";")]
        if not sections or not sections[0].startswith("<") or not sections[0].endswith(">"):
            continue
        url = sections[0][1:-1]
        for section in sections[1:]:
            if section.startswith('rel="') and section.endswith('"'):
                links[section[5:-1]] = url
    return links


@dataclass
class GitHubReader:
    token: str
    api_base: str = "https://api.github.com"
    timeout_seconds: int = 30
    maximum_pages: int = 100

    def __post_init__(self) -> None:
        self.api_base = self.api_base.rstrip("/")
        self.response_digests: dict[str, str] = {}

    def _request(self, path: str, query: dict[str, str] | None = None) -> tuple[Any, dict[str, str]]:
        if not path.startswith("/"):
            raise GitHubEvidenceError(f"unsafe GitHub API path: {path}")
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
                "User-Agent": "heptatrader-trusted-qualification/2",
            },
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                payload = response.read()
                status = response.status
                headers = {key.lower(): value for key, value in response.headers.items()}
        except HTTPError as exc:
            detail = exc.read(4096).decode("utf-8", errors="replace")
            raise GitHubEvidenceError(
                f"GitHub API GET {path} failed with HTTP {exc.code}: {detail}"
            ) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise GitHubEvidenceError(f"GitHub API GET {path} failed: {exc}") from exc
        if status != 200:
            raise GitHubEvidenceError(f"GitHub API GET {path} returned HTTP {status}")
        try:
            value = json.loads(payload.decode("utf-8"), object_pairs_hook=strict_object_pairs)
        except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
            raise GitHubEvidenceError(f"GitHub API GET {path} returned invalid JSON: {exc}") from exc
        self.response_digests[url] = canonical_digest(value)
        return value, headers

    def get_json(self, path: str, query: dict[str, str] | None = None) -> Any:
        value, _ = self._request(path, query)
        return value

    def get_paginated(
        self,
        path: str,
        query: dict[str, str] | None = None,
        *,
        item_key: str | None = None,
    ) -> Any:
        base_query = dict(query or {})
        base_query["per_page"] = "100"
        all_items: list[Any] = []
        total_count: int | None = None
        for page in range(1, self.maximum_pages + 1):
            page_query = dict(base_query)
            page_query["page"] = str(page)
            value, headers = self._request(path, page_query)
            if item_key is None:
                if not isinstance(value, list):
                    raise GitHubEvidenceError(f"paginated endpoint {path} did not return an array")
                items = value
            else:
                if not isinstance(value, dict) or not isinstance(value.get(item_key), list):
                    raise GitHubEvidenceError(
                        f"paginated endpoint {path} did not return object.{item_key}"
                    )
                items = value[item_key]
                count = value.get("total_count")
                if isinstance(count, int) and not isinstance(count, bool):
                    total_count = count
            all_items.extend(items)
            links = _parse_link(headers.get("link"))
            if "next" not in links:
                if len(items) == 100 and total_count is not None and len(all_items) < total_count:
                    raise GitHubEvidenceError(
                        f"pagination for {path} stopped before total_count={total_count}"
                    )
                break
        else:
            raise GitHubEvidenceError(f"pagination for {path} exceeded {self.maximum_pages} pages")
        if total_count is not None and len(all_items) != total_count:
            raise GitHubEvidenceError(
                f"pagination for {path} returned {len(all_items)} of {total_count} items"
            )
        if item_key is None:
            return all_items
        return {"total_count": len(all_items), item_key: all_items}


def latest_review_states(reviews: Any, candidate_sha: str) -> dict[str, dict[str, Any]]:
    if not isinstance(reviews, list):
        return {}
    latest: dict[str, dict[str, Any]] = {}
    for review in reviews:
        if not isinstance(review, dict) or review.get("commit_id") != candidate_sha:
            continue
        user = review.get("user")
        login = user.get("login") if isinstance(user, dict) else None
        if not isinstance(login, str) or not login:
            continue
        state = review.get("state")
        if state not in DECISIVE_REVIEW_STATES:
            continue
        key = (
            review.get("submitted_at") if isinstance(review.get("submitted_at"), str) else "",
            review.get("id") if isinstance(review.get("id"), int) else -1,
        )
        old = latest.get(login)
        old_key = (
            old.get("submitted_at") if isinstance(old, dict) and isinstance(old.get("submitted_at"), str) else "",
            old.get("id") if isinstance(old, dict) and isinstance(old.get("id"), int) else -1,
        )
        if old is None or key > old_key:
            latest[login] = review
    return latest


def review_state_projection(reviews: Any, candidate_sha: str) -> list[dict[str, Any]]:
    latest = latest_review_states(reviews, candidate_sha)
    return [
        {
            "login": login,
            "state": latest[login].get("state"),
            "review_id": latest[login].get("id"),
            "submitted_at": latest[login].get("submitted_at"),
        }
        for login in sorted(latest)
    ]


def validate_reviews(
    reviews: Any,
    candidate_sha: str,
    author: str,
    minimum_approvals: int,
    errors: list[str],
    *,
    label: str = "pull request",
) -> set[str]:
    if not isinstance(reviews, list):
        errors.append(f"{label}: review evidence is missing")
        return set()
    latest = latest_review_states(reviews, candidate_sha)
    blockers = sorted(
        login for login, review in latest.items() if review.get("state") == "CHANGES_REQUESTED"
    )
    if blockers:
        errors.append(f"{label}: current exact-head change requests remain: {', '.join(blockers)}")
    approvers = {
        login
        for login, review in latest.items()
        if review.get("state") == "APPROVED" and login != author
    }
    if len(approvers) < minimum_approvals:
        errors.append(
            f"{label}: exact-head non-author approvals below policy: "
            f"{len(approvers)} < {minimum_approvals}"
        )
    return approvers


def context_specs(contexts: dict[str, Any]) -> dict[str, dict[str, Any]]:
    provenance = contexts.get("required_check_provenance")
    if not isinstance(provenance, dict):
        raise ValueError("required_check_provenance is missing")
    specs = provenance.get("contexts")
    if not isinstance(specs, dict) or not specs:
        raise ValueError("required_check_provenance.contexts is invalid")
    result: dict[str, dict[str, Any]] = {}
    for name, spec in specs.items():
        if not isinstance(name, str) or not name or not isinstance(spec, dict):
            raise ValueError("required check provenance entry is invalid")
        workflow_id = spec.get("workflow_id")
        workflow_path = spec.get("workflow_path")
        job_name = spec.get("job_name")
        if (
            not isinstance(workflow_id, int)
            or isinstance(workflow_id, bool)
            or workflow_id <= 0
            or not isinstance(workflow_path, str)
            or not workflow_path.startswith(".github/workflows/")
            or not isinstance(job_name, str)
            or not job_name
        ):
            raise ValueError(f"required check provenance is invalid for {name}")
        result[name] = {
            "workflow_id": workflow_id,
            "workflow_path": workflow_path,
            "job_name": job_name,
        }
    return result


def collect_check_evidence(
    reader: GitHubReader,
    repository_base: str,
    sha: str,
    event: str,
) -> dict[str, Any]:
    checks = reader.get_paginated(
        repository_base + f"/commits/{quote(sha, safe='')}/check-runs",
        item_key="check_runs",
    )
    runs = reader.get_paginated(
        repository_base + "/actions/runs",
        {"head_sha": sha, "event": event, "exclude_pull_requests": "false"},
        item_key="workflow_runs",
    )
    jobs: dict[str, Any] = {}
    for run in runs["workflow_runs"]:
        if not isinstance(run, dict) or not isinstance(run.get("id"), int):
            raise GitHubEvidenceError("workflow run collection contains an invalid entry")
        run_id = run["id"]
        jobs[str(run_id)] = reader.get_paginated(
            repository_base + f"/actions/runs/{run_id}/jobs",
            {"filter": "latest"},
            item_key="jobs",
        )
    return {"check_runs": checks, "workflow_runs": runs, "jobs_by_run": jobs}


def _latest(items: list[dict[str, Any]], *, time_fields: tuple[str, ...]) -> dict[str, Any] | None:
    if not items:
        return None
    def key(item: dict[str, Any]) -> tuple[Any, ...]:
        values: list[Any] = []
        for field in time_fields:
            value = item.get(field)
            values.append(value if isinstance(value, str) else "")
        values.append(item.get("run_attempt") if isinstance(item.get("run_attempt"), int) else -1)
        values.append(item.get("id") if isinstance(item.get("id"), int) else -1)
        return tuple(values)
    return max(items, key=key)


def validate_context_evidence(
    evidence: Any,
    expected_sha: str,
    event: str,
    required_contexts: list[str],
    specs: dict[str, dict[str, Any]],
    integration_id: int,
    label: str,
    errors: list[str],
) -> dict[str, dict[str, Any]]:
    selected: dict[str, dict[str, Any]] = {}
    if not isinstance(evidence, dict):
        errors.append(f"{label}: provenance evidence is missing")
        return selected
    checks_payload = evidence.get("check_runs")
    runs_payload = evidence.get("workflow_runs")
    jobs_by_run = evidence.get("jobs_by_run")
    if not isinstance(checks_payload, dict) or not isinstance(checks_payload.get("check_runs"), list):
        errors.append(f"{label}: check-run evidence is invalid")
        return selected
    if not isinstance(runs_payload, dict) or not isinstance(runs_payload.get("workflow_runs"), list):
        errors.append(f"{label}: workflow-run evidence is invalid")
        return selected
    if not isinstance(jobs_by_run, dict):
        errors.append(f"{label}: workflow-job evidence is invalid")
        return selected
    checks = [item for item in checks_payload["check_runs"] if isinstance(item, dict)]
    runs = [item for item in runs_payload["workflow_runs"] if isinstance(item, dict)]

    for context in required_contexts:
        spec = specs.get(context)
        if spec is None:
            errors.append(f"{label}: no provenance specification for {context}")
            continue
        eligible_runs = [
            run
            for run in runs
            if run.get("head_sha") == expected_sha
            and run.get("event") == event
            and run.get("workflow_id") == spec["workflow_id"]
            and run.get("path") == spec["workflow_path"]
        ]
        run = _latest(eligible_runs, time_fields=("created_at", "updated_at"))
        if run is None:
            errors.append(f"{label}: missing workflow run for {context}")
            continue
        if run.get("status") != "completed" or run.get("conclusion") != "success":
            errors.append(f"{label}: latest workflow run for {context} is not terminal success")
            continue
        run_id = run.get("id")
        run_attempt = run.get("run_attempt")
        if not isinstance(run_id, int) or not isinstance(run_attempt, int) or run_attempt < 1:
            errors.append(f"{label}: workflow run identity is invalid for {context}")
            continue
        jobs_payload = jobs_by_run.get(str(run_id))
        if not isinstance(jobs_payload, dict) or not isinstance(jobs_payload.get("jobs"), list):
            errors.append(f"{label}: jobs are missing for workflow run {run_id}")
            continue
        jobs = [
            job
            for job in jobs_payload["jobs"]
            if isinstance(job, dict)
            and job.get("run_id") == run_id
            and job.get("name") == spec["job_name"]
        ]
        if len(jobs) != 1:
            errors.append(
                f"{label}: expected exactly one latest-attempt job {spec['job_name']} "
                f"for run {run_id}; found {len(jobs)}"
            )
            continue
        job = jobs[0]
        if job.get("status") != "completed" or job.get("conclusion") != "success":
            errors.append(f"{label}: job {context} is not terminal success")
            continue
        steps = job.get("steps")
        if not isinstance(steps, list) or not any(
            isinstance(step, dict)
            and isinstance(step.get("number"), int)
            and step["number"] > 1
            and step.get("status") == "completed"
            and step.get("conclusion") == "success"
            for step in steps
        ):
            errors.append(f"{label}: job {context} has no non-empty successful execution step")
            continue
        check_candidates = [
            check
            for check in checks
            if check.get("name") == context and check.get("head_sha") == expected_sha
        ]
        check = _latest(check_candidates, time_fields=("started_at", "completed_at"))
        if check is None:
            errors.append(f"{label}: missing check run {context}")
            continue
        app = check.get("app")
        if not isinstance(app, dict) or app.get("id") != integration_id:
            errors.append(f"{label}: check {context} is not bound to GitHub Actions integration {integration_id}")
            continue
        if check.get("status") != "completed" or check.get("conclusion") != "success":
            errors.append(f"{label}: check {context} is not terminal success")
            continue
        details_url = check.get("details_url")
        match = DETAILS_RE.fullmatch(details_url) if isinstance(details_url, str) else None
        if match is None:
            errors.append(f"{label}: check {context} has no canonical Actions run/job URL")
            continue
        if int(match.group(1)) != run_id or int(match.group(2)) != job.get("id"):
            errors.append(f"{label}: check {context} does not bind the selected workflow run/job")
            continue
        selected[context] = {
            "check_run_id": check.get("id"),
            "workflow_run_id": run_id,
            "workflow_id": run.get("workflow_id"),
            "workflow_path": run.get("path"),
            "run_attempt": run_attempt,
            "job_id": job.get("id"),
            "job_name": job.get("name"),
            "event": event,
            "head_sha": expected_sha,
        }
    return selected
