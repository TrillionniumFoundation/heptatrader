#!/usr/bin/env python3
"""Hostile self-test for immutable hosted-audit postflight contracts."""
from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import tempfile

import check_bootstrap_postflight as postflight

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = postflight.WORKFLOWS
JOB_IDS = postflight.JOB_IDS
FINAL_NAME = postflight.FINAL_NAME
CHECKOUT_NAME = postflight.CHECKOUT_NAME
EXACT_SHA_EXPR = postflight.EXACT_SHA_EXPR


def _job_block(text: str, job_id: str, errors: list[str], label: str) -> str:
    return postflight._job_block(text, job_id, errors, label)


def validate(root: Path | str = ROOT) -> list[str]:
    root = Path(root).resolve()
    errors = postflight.validate(root)
    for relative in WORKFLOWS:
        label = relative.as_posix()
        path = root / relative
        try:
            text = path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError):
            continue
        block = _job_block(text, JOB_IDS[relative], errors, label)
        if not block:
            continue

        job_header = block.split("    steps:\n", 1)[0]
        if "${{ runner." in job_header:
            errors.append(f"{label}: runner context is forbidden before runner allocation")
        if block.count(EXACT_SHA_EXPR) != 3:
            errors.append(
                f"{label}: checkout, identity and final postflight must each bind the exact event SHA"
            )
        if block.count(postflight.CHECKOUT_ACTION) != 1:
            errors.append(f"{label}: exactly one digest-pinned checkout is required")

        final_start = block.find(f"- name: {FINAL_NAME}")
        final_block = block[final_start:] if final_start >= 0 else ""
        required_final = (
            f"- name: {FINAL_NAME}\n        if: always()",
            f"EXPECTED_SHA: {EXACT_SHA_EXPR}",
            'test "$(git rev-parse HEAD)" = "$EXPECTED_SHA"',
            "git diff --exit-code -- .",
            "git diff --cached --exit-code -- .",
            "--untracked-files=all",
            "--ignored=matching",
        )
        for token in required_final:
            if token not in final_block:
                errors.append(f"{label}: missing final-postflight token: {token}")
        if "if: success()" in final_block or "if: failure()" in final_block:
            errors.append(f"{label}: final postflight cannot be success/failure gated")
    return errors


def self_test() -> None:
    mutations = (
        lambda text: text.replace(
            f"- name: {FINAL_NAME}", "- name: Removed immutable postflight", 1
        ),
        lambda text: text.replace(
            f"- name: {FINAL_NAME}\n        if: always()",
            f"- name: {FINAL_NAME}\n        if: success()",
            1,
        ),
        lambda text: text.replace(
            "persist-credentials: false", "persist-credentials: true", 1
        ),
        lambda text: text.replace(
            f"ref: {EXACT_SHA_EXPR}", "ref: ${{ github.sha }}", 1
        ),
        lambda text: text.replace(
            "runs-on: ubuntu-24.04", "runs-on: self-hosted", 1
        ),
        lambda text: text.replace("--ignored=matching", "--ignored=no", 1),
        lambda text: text.replace(
            "git diff --cached --exit-code -- .",
            "git diff --cached --quiet -- .",
            1,
        ),
        lambda text: text.replace(
            "set -euo pipefail", "set -euo pipefail\n          git clean -fdx", 1
        ),
        lambda text: text.replace(
            "  pull_request:", "  pull_request_target:", 1
        ),
    )
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        for relative in WORKFLOWS:
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, target)

        baseline = validate(root)
        if baseline:
            raise AssertionError(baseline)

        for relative in WORKFLOWS:
            path = root / relative
            original = path.read_text(encoding="utf-8")
            for mutation in mutations:
                mutated = mutation(original)
                if mutated == original:
                    raise AssertionError(f"{relative}: mutation did not alter fixture")
                path.write_text(mutated, encoding="utf-8")
                if not validate(root):
                    raise AssertionError(f"{relative}: hostile mutation was accepted")
                path.write_text(original, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
    errors = validate(args.root)
    for error in errors:
        print(f"[HOSTED-AUDIT-FINAL-POSTFLIGHT] {error}")
    if errors:
        return 1
    print("[HOSTED-AUDIT-FINAL-POSTFLIGHT] PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
