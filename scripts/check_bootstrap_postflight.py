#!/usr/bin/env python3
"""Validate immutable exact-head postflight in hosted PR/merge-group audits."""
from __future__ import annotations

from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = (
    Path(".github/workflows/governance-bootstrap-admission.yml"),
    Path(".github/workflows/qualification-source-audit.yml"),
)
JOB_IDS = {
    WORKFLOWS[0]: "admission",
    WORKFLOWS[1]: "audit",
}
VALIDATION_NAMES = {
    WORKFLOWS[0]: (
        "Verify privileged workflows, source audit and context registry as immutable data"
    ),
    WORKFLOWS[1]: "Validate base qualification source on an unprivileged hosted runner",
}
CHECKOUT_NAME = "Checkout exact event subject without credentials"
IDENTITY_NAME = "Assert exact pull-request or merge-group identity"
FINAL_NAME = "Reassert immutable event subject"
CHECKOUT_ACTION = "uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262"
EXACT_SHA_EXPR = "${{ github.event.pull_request.head.sha || github.sha }}"
JOB_RE = re.compile(r"^  ([A-Za-z0-9_.-]+):\s*$", re.MULTILINE)
FORBIDDEN_CLEANUP = (
    "git reset --hard",
    "/usr/bin/git reset --hard",
    "git clean -fd",
    "/usr/bin/git clean -fd",
    "git clean -xd",
    "/usr/bin/git clean -xd",
    "git checkout -- .",
    "/usr/bin/git checkout -- .",
    "git restore .",
    "/usr/bin/git restore .",
)


def _job_block(text: str, job_id: str, errors: list[str], label: str) -> str:
    matches = list(JOB_RE.finditer(text))
    selected = [match for match in matches if match.group(1) == job_id]
    if len(selected) != 1:
        errors.append(f"{label}: expected exactly one {job_id} job")
        return ""
    start = selected[0].start()
    later = [match.start() for match in matches if match.start() > start]
    return text[start : min(later) if later else len(text)]


def _validate_one(relative: Path, text: str, errors: list[str]) -> None:
    label = relative.as_posix()
    for token in (
        "  pull_request:\n    branches: [main]",
        "  merge_group:\n    types: [checks_requested]",
        "permissions:\n  contents: read\n\nconcurrency:",
        "cancel-in-progress: ${{ github.event_name == 'pull_request' }}",
    ):
        if token not in text:
            errors.append(f"{label}: missing hosted-audit token: {token}")

    trigger_header = text.split("\npermissions:", 1)[0]
    if "pull_request_target" in trigger_header:
        errors.append(f"{label}: pull_request_target is forbidden")
    if re.search(r"^  cancel-in-progress: true\s*$", text, re.MULTILINE):
        errors.append(f"{label}: unconditional cancellation is forbidden")

    block = _job_block(text, JOB_IDS[relative], errors, label)
    if not block:
        return

    required = (
        "runs-on: ubuntu-24.04",
        f"- name: {CHECKOUT_NAME}",
        CHECKOUT_ACTION,
        "repository: ${{ github.event.pull_request.head.repo.full_name || github.repository }}",
        f"ref: {EXACT_SHA_EXPR}",
        "fetch-depth: 1",
        "persist-credentials: false",
        f"- name: {IDENTITY_NAME}",
        f"EXPECTED_SHA: {EXACT_SHA_EXPR}",
        'test "$(git rev-parse HEAD)" = "$EXPECTED_SHA"',
        "pull_request)",
        'test "$EVENT_BASE_REF" = main',
        "merge_group)",
        '[[ "$EVENT_REF" == refs/heads/gh-readonly-queue/main/pr-* ]]',
        VALIDATION_NAMES[relative],
        f"- name: {FINAL_NAME}\n        if: always()",
        "git diff --exit-code -- .",
        "git diff --cached --exit-code -- .",
        'test -z "$(git status --porcelain=v1 --untracked-files=all --ignored=matching)"',
    )
    for token in required:
        if token not in block:
            errors.append(f"{label}: missing immutable postflight token: {token}")

    if block.count(CHECKOUT_ACTION) != 1:
        errors.append(f"{label}: hosted audit must contain exactly one pinned checkout")
    if block.count("persist-credentials: false") != 1:
        errors.append(f"{label}: hosted audit checkout must disable credentials exactly once")
    runs_on = [line for line in block.splitlines() if line.startswith("    runs-on:")]
    if runs_on != ["    runs-on: ubuntu-24.04"]:
        errors.append(f"{label}: hosted audit must select only ubuntu-24.04")
    if "${{ secrets." in block:
        errors.append(f"{label}: hosted audit cannot consume secrets")

    positions = [
        block.find(CHECKOUT_NAME),
        block.find(IDENTITY_NAME),
        block.find(VALIDATION_NAMES[relative]),
        block.find(FINAL_NAME),
    ]
    if any(position < 0 for position in positions) or positions != sorted(positions):
        errors.append(f"{label}: checkout/identity/validation/postflight order is invalid")

    if block.count(f"- name: {FINAL_NAME}") != 1:
        errors.append(f"{label}: immutable postflight must occur exactly once")
    final_start = block.find(f"- name: {FINAL_NAME}")
    final_block = block[final_start:] if final_start >= 0 else ""
    if final_block.count("if: always()") != 1:
        errors.append(f"{label}: immutable postflight must run exactly once with always()")
    if "\n      - name:" in final_block:
        errors.append(f"{label}: immutable postflight must be the last explicit job step")
    if final_block.count(f"EXPECTED_SHA: {EXACT_SHA_EXPR}") != 1:
        errors.append(f"{label}: final postflight must bind the exact event subject once")
    if final_block.count("--untracked-files=all") != 1:
        errors.append(f"{label}: final postflight must expose every untracked path")
    if final_block.count("--ignored=matching") != 1:
        errors.append(f"{label}: final postflight must expose ignored artifacts")

    lowered = block.lower()
    for token in FORBIDDEN_CLEANUP:
        if token in lowered:
            errors.append(f"{label}: evidence-destroying cleanup is forbidden: {token}")


def validate(root: Path | str = ROOT) -> list[str]:
    root = Path(root).resolve()
    errors: list[str] = []
    for relative in WORKFLOWS:
        path = root / relative
        try:
            info = path.lstat()
            if path.is_symlink() or not path.is_file() or info.st_nlink != 1:
                errors.append(f"{relative}: must be a regular single-link file")
                continue
            text = path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError) as exc:
            errors.append(f"{relative}: unreadable: {exc}")
            continue
        _validate_one(relative, text, errors)
    return errors


def main() -> int:
    errors = validate()
    for error in errors:
        print(f"[HOSTED-AUDIT-POSTFLIGHT] {error}", file=sys.stderr)
    if errors:
        return 1
    print("[HOSTED-AUDIT-POSTFLIGHT] PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
