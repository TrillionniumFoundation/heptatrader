#!/usr/bin/env python3
"""Validate stable, globally unique GitHub Actions check contexts.

GitHub merge-queue protection couples pull-request and merge-group checks. Every
required context must therefore be emitted by one uniquely named job that runs
on both events. A PR-only or merge-group-only required name either leaves the
queue waiting forever or allows a different check to satisfy the wrong gate.
"""
from __future__ import annotations

from dataclasses import dataclass
import itertools
import json
from pathlib import Path
import re
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
POLICY_REL = Path(".github/required-check-contexts-v1.json")
WORKFLOWS_REL = Path(".github/workflows")
CONTEXT_LIST_FIELDS = (
    "required_branch_contexts",
    "external_qualification_contexts",
    "non_required_observation_contexts",
)
EVENT_REQUIREMENTS = {
    "required_branch_contexts": frozenset({"pull_request", "merge_group"}),
    "external_qualification_contexts": frozenset({"workflow_dispatch"}),
}
EXPRESSION_RE = re.compile(r"\$\{\{\s*([^}]+?)\s*}}")
JOB_KEY_RE = re.compile(r"^  ([A-Za-z0-9_.-]+):\s*(?:#.*)?$")
JOB_NAME_RE = re.compile(r"^    name:\s*(.*?)\s*$")
JOB_IF_EVENT_RE = re.compile(
    r"^    if:\s*github\.event_name\s*==\s*(['\"])([A-Za-z0-9_]+)\1\s*$"
)
MATRIX_RE = re.compile(r"^      matrix:\s*(?:#.*)?$")
MATRIX_VALUE_RE = re.compile(r"^        ([A-Za-z0-9_-]+):\s*\[(.*)]\s*(?:#.*)?$")
TOP_LEVEL_RE = re.compile(r"^[A-Za-z0-9_.-]+:")
EVENT_RE = re.compile(r"^  ([A-Za-z0-9_-]+):")
CANCEL_RE = re.compile(r"^  cancel-in-progress:\s*(.*?)\s*$")
ALLOWED_CONTEXT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._()+/:-]{0,127}$")
SAFE_MERGE_GROUP_CANCELLATION = "${{ github.event_name == 'pull_request' }}"


@dataclass(frozen=True)
class JobContext:
    workflow: str
    job_id: str
    template: str
    context: str
    events: frozenset[str]


def _strict_object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load_policy(root: Path, errors: list[str]) -> dict[str, Any]:
    path = root / POLICY_REL
    try:
        document = json.loads(
            path.read_text(encoding="utf-8-sig"),
            object_pairs_hook=_strict_object_pairs,
        )
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        errors.append(f"{POLICY_REL.as_posix()}: invalid: {exc}")
        return {}
    if not isinstance(document, dict):
        errors.append(f"{POLICY_REL.as_posix()}: expected object")
        return {}
    if document.get("schema") != "heptatrader.required-check-contexts.v1":
        errors.append(f"{POLICY_REL.as_posix()}: schema mismatch")
    policy = document.get("policy")
    expected_policy = {
        "explicit_job_names": True,
        "globally_unique_contexts": True,
        "dynamic_context_expressions": "matrix-only",
        "required_contexts_must_be_event_reachable": True,
        "skipped_or_missing_is_not_success": True,
        "merge_group_cancel_in_progress": "forbidden",
    }
    if not isinstance(policy, dict):
        errors.append(f"{POLICY_REL.as_posix()}: policy must be an object")
    else:
        for key, expected in expected_policy.items():
            if policy.get(key) != expected:
                errors.append(
                    f"{POLICY_REL.as_posix()}: policy.{key} must be {expected!r}"
                )

    all_contexts: set[str] = set()
    for field in CONTEXT_LIST_FIELDS:
        values = document.get(field)
        if not isinstance(values, list) or not values:
            errors.append(f"{POLICY_REL.as_posix()}: {field} must be a non-empty array")
            continue
        for position, value in enumerate(values):
            if not isinstance(value, str) or not ALLOWED_CONTEXT_RE.fullmatch(value):
                errors.append(
                    f"{POLICY_REL.as_posix()}: {field}[{position}] is not a stable context"
                )
                continue
            if value in all_contexts:
                errors.append(
                    f"{POLICY_REL.as_posix()}: duplicate registered context: {value}"
                )
            all_contexts.add(value)
    return document


def _scalar(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _workflow_events(lines: list[str], label: str, errors: list[str]) -> set[str]:
    start: int | None = None
    inline: str | None = None
    for index, line in enumerate(lines):
        if line.startswith("on:"):
            start = index
            inline = line.split(":", 1)[1].strip()
            break
    if start is None:
        errors.append(f"{label}: workflow has no top-level on: block")
        return set()
    if inline:
        if inline.startswith("[") and inline.endswith("]"):
            return {
                _scalar(item)
                for item in inline[1:-1].split(",")
                if _scalar(item)
            }
        errors.append(f"{label}: unsupported inline on: value")
        return set()

    events: set[str] = set()
    for line in lines[start + 1:]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if len(line) - len(line.lstrip()) == 0:
            break
        match = EVENT_RE.match(line)
        if match:
            events.add(match.group(1))
    if not events:
        errors.append(f"{label}: workflow has no events")
    return events


def _workflow_cancel_value(lines: list[str]) -> str | None:
    for line in lines:
        if line.strip() == "jobs:" and len(line) - len(line.lstrip()) == 0:
            break
        match = CANCEL_RE.match(line)
        if match:
            return _scalar(match.group(1))
    return None


def _matrix_values(
    block: list[str], label: str, errors: list[str]
) -> dict[str, list[str]]:
    values: dict[str, list[str]] = {}
    matrix_index: int | None = None
    for index, line in enumerate(block):
        if MATRIX_RE.match(line):
            matrix_index = index
            break
    if matrix_index is None:
        return values

    for line in block[matrix_index + 1:]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if indent <= 6:
            break
        match = MATRIX_VALUE_RE.match(line)
        if not match:
            if indent == 8:
                errors.append(f"{label}: matrix values must use a bounded inline array")
            continue
        key = match.group(1)
        parsed = [_scalar(item) for item in match.group(2).split(",") if _scalar(item)]
        if not parsed or len(parsed) != len(set(parsed)):
            errors.append(f"{label}: matrix.{key} must contain unique values")
            continue
        values[key] = parsed
    return values


def _expand_contexts(
    template: str,
    matrix: dict[str, list[str]],
    label: str,
    errors: list[str],
) -> list[str]:
    expressions = EXPRESSION_RE.findall(template)
    keys: list[str] = []
    for expression in expressions:
        normalized = expression.strip()
        if not normalized.startswith("matrix."):
            errors.append(
                f"{label}: dynamic context expression is not matrix-bound: {expression}"
            )
            return []
        key = normalized[len("matrix."):]
        if not re.fullmatch(r"[A-Za-z0-9_-]+", key) or key not in matrix:
            errors.append(f"{label}: context references unknown matrix key: {key}")
            return []
        if key not in keys:
            keys.append(key)

    if not keys:
        if matrix:
            errors.append(f"{label}: matrix job name must bind every context dimension")
        return [template]

    unused = sorted(set(matrix) - set(keys))
    if unused:
        errors.append(f"{label}: job name omits matrix dimensions: {', '.join(unused)}")
        return []

    expanded: list[str] = []
    for combination in itertools.product(*(matrix[key] for key in keys)):
        context = template
        for key, value in zip(keys, combination):
            pattern = re.compile(r"\$\{\{\s*matrix\." + re.escape(key) + r"\s*}}")
            context = pattern.sub(value, context)
        expanded.append(context)
    return expanded


def _workflow_jobs(
    path: Path, workflow_events: set[str], root: Path, errors: list[str]
) -> list[JobContext]:
    label = path.relative_to(root).as_posix()
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    jobs_index: int | None = None
    for index, line in enumerate(lines):
        if line.strip() == "jobs:" and len(line) - len(line.lstrip()) == 0:
            jobs_index = index
            break
    if jobs_index is None:
        errors.append(f"{label}: workflow has no top-level jobs block")
        return []

    starts: list[tuple[int, str]] = []
    for index in range(jobs_index + 1, len(lines)):
        line = lines[index]
        if line and len(line) - len(line.lstrip()) == 0 and TOP_LEVEL_RE.match(line):
            break
        match = JOB_KEY_RE.match(line)
        if match:
            starts.append((index, match.group(1)))
    if not starts:
        errors.append(f"{label}: workflow has no jobs")
        return []

    result: list[JobContext] = []
    for position, (start, job_id) in enumerate(starts):
        end = starts[position + 1][0] if position + 1 < len(starts) else len(lines)
        block = lines[start + 1:end]
        job_label = f"{label}: job {job_id}"
        names = [match.group(1) for line in block if (match := JOB_NAME_RE.match(line))]
        if len(names) != 1 or not _scalar(names[0]):
            errors.append(f"{job_label}: requires exactly one explicit non-empty name")
            continue
        template = _scalar(names[0])
        event_conditions = [
            match.group(2)
            for line in block
            if (match := JOB_IF_EVENT_RE.match(line))
        ]
        if len(event_conditions) > 1:
            errors.append(f"{job_label}: duplicate event-name conditions")
            continue
        reachable = set(workflow_events)
        if event_conditions:
            event = event_conditions[0]
            if event not in workflow_events:
                errors.append(
                    f"{job_label}: condition event {event} is not a workflow trigger"
                )
                continue
            reachable = {event}

        matrix = _matrix_values(block, job_label, errors)
        for context in _expand_contexts(template, matrix, job_label, errors):
            if not ALLOWED_CONTEXT_RE.fullmatch(context):
                errors.append(f"{job_label}: expanded context is invalid: {context}")
                continue
            result.append(
                JobContext(
                    workflow=label,
                    job_id=job_id,
                    template=template,
                    context=context,
                    events=frozenset(reachable),
                )
            )
    return result


def validate(root: Path = ROOT) -> list[str]:
    root = Path(root).resolve()
    errors: list[str] = []
    policy = _load_policy(root, errors)
    workflows = root / WORKFLOWS_REL
    if not workflows.is_dir():
        errors.append(f"{WORKFLOWS_REL.as_posix()}: missing")
        return errors

    contexts: list[JobContext] = []
    cancellation: dict[str, str | None] = {}
    for path in sorted(workflows.glob("*.y*ml")):
        label = path.relative_to(root).as_posix()
        try:
            lines = path.read_text(encoding="utf-8-sig").splitlines()
        except (OSError, UnicodeError) as exc:
            errors.append(f"{label}: unreadable: {exc}")
            continue
        events = _workflow_events(lines, label, errors)
        cancellation[label] = _workflow_cancel_value(lines)
        contexts.extend(_workflow_jobs(path, events, root, errors))

    by_context: dict[str, list[JobContext]] = {}
    by_event: dict[str, set[str]] = {}
    for item in contexts:
        by_context.setdefault(item.context, []).append(item)
        for event in item.events:
            by_event.setdefault(event, set()).add(item.context)
    for context, owners in sorted(by_context.items()):
        identities = {(item.workflow, item.job_id) for item in owners}
        if len(identities) > 1:
            rendered = ", ".join(
                f"{workflow}:{job}" for workflow, job in sorted(identities)
            )
            errors.append(f"duplicate workflow check context {context}: {rendered}")

    registered: set[str] = set()
    if policy:
        for field in CONTEXT_LIST_FIELDS:
            raw_values = policy.get(field)
            if isinstance(raw_values, list):
                registered.update(value for value in raw_values if isinstance(value, str))
        discovered = set(by_context)
        for context in sorted(registered - discovered):
            errors.append(f"registered workflow check context is missing: {context}")
        for context in sorted(discovered - registered):
            errors.append(f"unregistered workflow check context: {context}")
        for field, required_events in EVENT_REQUIREMENTS.items():
            raw_values = policy.get(field)
            if not isinstance(raw_values, list):
                continue
            for context in raw_values:
                if not isinstance(context, str):
                    continue
                for event in sorted(required_events):
                    if context not in by_event.get(event, set()):
                        errors.append(
                            f"{field}: context is not reachable on {event}: {context}"
                        )

        required = set(policy.get("required_branch_contexts", []))
        for context in sorted(required):
            owners = by_context.get(context, [])
            if len(owners) != 1:
                continue
            item = owners[0]
            if "merge_group" not in item.events:
                continue
            value = cancellation.get(item.workflow)
            if value not in {"false", SAFE_MERGE_GROUP_CANCELLATION}:
                errors.append(
                    f"{item.workflow}: required merge-group workflow must not "
                    "cancel an in-progress merge-group run"
                )
    return errors


def main() -> int:
    errors = validate()
    for error in errors:
        print(f"[WORKFLOW-CHECK-CONTEXTS] {error}", file=sys.stderr)
    if errors:
        return 1
    print("[WORKFLOW-CHECK-CONTEXTS] PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
