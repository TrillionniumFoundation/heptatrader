#!/usr/bin/env python3
"""Validate independent exact-head postflight in qualification PR audits."""
from __future__ import annotations

from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = (
    Path('.github/workflows/github-governance-qualification.yml'),
    Path('.github/workflows/ib-paper-qualification.yml'),
)
JOB = re.compile(r'^  ([A-Za-z0-9_.-]+):\s*$', re.M)
CHECKOUT = 'uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262'
VALIDATE = {
    WORKFLOWS[0]: 'Validate trusted harnesses, provenance, team activation and hostile isolation',
    WORKFLOWS[1]: 'Validate PAPER qualification harnesses and hostile isolation',
}
POST = 'Checkout independent exact-head postflight verifier'
REBIND = 'Rebind executed source to unchanged reviewed bytes'
SUMMARY = 'Record successful source-only bootstrap audit'


def _bootstrap(text: str, label: str, errors: list[str]) -> str:
    matches = list(JOB.finditer(text))
    chosen = [m for m in matches if m.group(1) == 'bootstrap-audit']
    if len(chosen) != 1:
        errors.append(f'{label}: expected exactly one bootstrap-audit job')
        return ''
    start = chosen[0].start()
    later = [m.start() for m in matches if m.start() > start]
    return text[start:min(later) if later else len(text)]


def validate(root: Path | str = ROOT) -> list[str]:
    root = Path(root).resolve()
    errors: list[str] = []
    for relative in WORKFLOWS:
        label = relative.as_posix()
        try:
            text = (root / relative).read_text(encoding='utf-8')
        except (OSError, UnicodeError) as exc:
            errors.append(f'{label}: unreadable: {exc}')
            continue
        block = _bootstrap(text, label, errors)
        required = (
            VALIDATE[relative], POST, REBIND, SUMMARY,
            'path: candidate', 'path: postflight',
            'working-directory: candidate',
            'ref: ${{ github.event.pull_request.head.sha }}',
            'python3 scripts/check_bootstrap_postflight.py',
            "test_bootstrap_postflight.py",
            'python3 postflight/scripts/verify_exact_git_index.py --root postflight',
            'python3 postflight/scripts/verify_exact_git_index.py --root candidate',
            'test "$(git -C postflight rev-parse HEAD)" = "$EXPECTED_SHA"',
        )
        for token in required:
            if token not in block:
                errors.append(f'{label}: missing postflight token: {token}')
        if block.count(CHECKOUT) != 2:
            errors.append(f'{label}: bootstrap must contain exactly two pinned checkouts')
        if block.count('path: candidate') != 1 or block.count('path: postflight') != 1:
            errors.append(f'{label}: candidate/postflight paths must each occur once')
        if block.count('persist-credentials: false') != 2:
            errors.append(f'{label}: both bootstrap checkouts must disable credentials')
        positions = [block.find(token) for token in (VALIDATE[relative], POST, REBIND, SUMMARY)]
        if any(pos < 0 for pos in positions) or positions != sorted(positions):
            errors.append(f'{label}: candidate validation/postflight/summary order is invalid')
        if 'python3 candidate/scripts/verify_exact_git_index.py --root candidate' in block:
            errors.append(f'{label}: candidate-controlled verifier cannot certify postflight')
    return errors


def main() -> int:
    errors = validate()
    for error in errors:
        print(f'[BOOTSTRAP-POSTFLIGHT] {error}', file=sys.stderr)
    if errors:
        return 1
    print('[BOOTSTRAP-POSTFLIGHT] PASS')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
