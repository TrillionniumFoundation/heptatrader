#!/usr/bin/env python3
"""Validate immutable final PR-bootstrap source reassertion in both workflows."""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import shutil
import tempfile

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = (
    Path('.github/workflows/github-governance-qualification.yml'),
    Path('.github/workflows/ib-paper-qualification.yml'),
)
VALIDATION_NAMES = {
    WORKFLOWS[0]: 'Validate trusted harnesses, provenance, team activation and hostile isolation',
    WORKFLOWS[1]: 'Validate PAPER qualification harnesses and hostile isolation',
}
NEXT_JOBS = {
    WORKFLOWS[0]: 'qualify',
    WORKFLOWS[1]: 'build-candidate',
}
FINAL_NAME = 'Reassert immutable exact source after candidate-controlled validation'
CHECKOUT_NAME = 'Checkout independent exact-head postflight verifier'
REBIND_NAME = 'Rebind executed source to unchanged reviewed bytes'
JOB_RE = re.compile(r'^  ([A-Za-z0-9_.-]+):\s*$', re.MULTILINE)
FORBIDDEN_CLEANUP = (
    'git reset --hard', '/usr/bin/git reset --hard',
    'git clean -fd', '/usr/bin/git clean -fd',
    'git clean -xd', '/usr/bin/git clean -xd',
    'git checkout -- .', '/usr/bin/git checkout -- .',
    'git restore .', '/usr/bin/git restore .',
)


def _job_block(text: str, name: str, errors: list[str], label: str) -> str:
    matches = list(JOB_RE.finditer(text))
    selected = [match for match in matches if match.group(1) == name]
    if len(selected) != 1:
        errors.append(f'{label}: expected exactly one {name} job')
        return ''
    start = selected[0].start()
    later = [match.start() for match in matches if match.start() > start]
    return text[start:min(later) if later else len(text)]


def _validate_one(relative: Path, text: str, errors: list[str]) -> None:
    label = relative.as_posix()
    block = _job_block(text, 'bootstrap-audit', errors, label)
    if not block:
        return
    prefix = 'github-governance' if relative == WORKFLOWS[0] else 'ib-paper'
    required = (
        "PYTHONDONTWRITEBYTECODE: '1'",
        f'PYTHONPYCACHEPREFIX="$RUNNER_TEMP/{prefix}-final-postflight-pycache"',
        'id: exact_source',
        'working-directory: candidate',
        '/usr/bin/python3 scripts/verify_exact_git_index.py --root .',
        f'postflight="$RUNNER_TEMP/{prefix}-exact-index-postflight.py"',
        '/usr/bin/install -m 0500 scripts/verify_exact_git_index.py "$postflight"',
        'verifier_sha256="$(/usr/bin/sha256sum "$postflight"',
        "head_tree=\"$(/usr/bin/git rev-parse 'HEAD^{tree}')\"",
        'scripts/verify_bootstrap_postflight_contract.py',
        'python3 scripts/verify_bootstrap_postflight_contract.py --self-test',
        "test_bootstrap_postflight_contract.py",
        f'- name: {CHECKOUT_NAME}\n        if: always()',
        f'- name: {REBIND_NAME}\n        if: always()',
        f'- name: {FINAL_NAME}',
        'BASH_ENV: /dev/null',
        'ENV: /dev/null',
        'PATH: /usr/bin:/bin',
        'PYTHONHOME:',
        'PYTHONPATH:',
        'LD_PRELOAD:',
        'EXPECTED_TREE_SHA: ${{ steps.exact_source.outputs.head_tree }}',
        'EXPECTED_VERIFIER_SHA256: ${{ steps.exact_source.outputs.verifier_sha256 }}',
        '/usr/bin/sha256sum "$POSTFLIGHT_VERIFIER"',
        '/usr/bin/python3 "$POSTFLIGHT_VERIFIER" --root "$GITHUB_WORKSPACE/candidate"',
        '/usr/bin/python3 "$POSTFLIGHT_VERIFIER" --root "$GITHUB_WORKSPACE/postflight"',
        'test -z "$candidate_status"',
        'test -z "$postflight_status"',
    )
    for token in required:
        if token not in block:
            errors.append(f'{label}: missing immutable final-postflight token: {token}')

    if 'PYTHONPYCACHEPREFIX: ${{ runner.temp }}' in block:
        errors.append(f'{label}: runner context is forbidden in job-level environment')

    positions = [
        block.find(VALIDATION_NAMES[relative]),
        block.find(CHECKOUT_NAME),
        block.find(REBIND_NAME),
        block.find('Record successful source-only bootstrap audit'),
        block.find(f'- name: {FINAL_NAME}'),
    ]
    if any(position < 0 for position in positions) or positions != sorted(positions):
        errors.append(f'{label}: candidate validation and final-postflight ordering is invalid')
    if block.count(f'- name: {FINAL_NAME}') != 1:
        errors.append(f'{label}: final immutable postflight must occur exactly once')
    final_start = block.find(f'- name: {FINAL_NAME}')
    final_block = block[final_start:] if final_start >= 0 else ''
    if final_block.count('if: always()') != 1:
        errors.append(f'{label}: final immutable postflight must run exactly once with always()')
    if final_block.count('/usr/bin/env -i') != 4:
        errors.append(f'{label}: final postflight must isolate two verifier and two status invocations')
    if final_block.count('--ignored=matching') != 2:
        errors.append(f'{label}: candidate and pristine trees must both expose ignored artifacts')
    if final_block.count('--untracked-files=all') != 2:
        errors.append(f'{label}: candidate and pristine trees must both expose all untracked files')
    if 'candidate/scripts/verify_exact_git_index.py --root candidate' in final_block:
        errors.append(f'{label}: candidate-controlled verifier cannot certify final state')
    for token in FORBIDDEN_CLEANUP:
        if token in block.lower():
            errors.append(f'{label}: evidence-destroying cleanup is forbidden: {token}')

    next_job = f'  {NEXT_JOBS[relative]}:'
    final_absolute = text.find(f'      - name: {FINAL_NAME}')
    next_absolute = text.find(next_job)
    if final_absolute < 0 or next_absolute < 0 or final_absolute > next_absolute:
        errors.append(f'{label}: final postflight must remain inside bootstrap-audit')
    elif text[final_absolute:next_absolute].count('      - name:') != 1:
        errors.append(f'{label}: final postflight must be the last bootstrap step')


def validate(root: Path | str = ROOT) -> list[str]:
    root = Path(root).resolve()
    errors: list[str] = []
    for relative in WORKFLOWS:
        try:
            text = (root / relative).read_text(encoding='utf-8')
        except (OSError, UnicodeError) as exc:
            errors.append(f'{relative}: unreadable: {exc}')
            continue
        _validate_one(relative, text, errors)
    return errors


def self_test() -> None:
    mutations = (
        lambda text: text.replace(f'- name: {FINAL_NAME}', '- name: Removed immutable postflight', 1),
        lambda text: text.replace(
            f'- name: {CHECKOUT_NAME}\n        if: always()',
            f'- name: {CHECKOUT_NAME}\n        if: success()', 1,
        ),
        lambda text: text.replace(
            f'- name: {REBIND_NAME}\n        if: always()',
            f'- name: {REBIND_NAME}\n        if: success()', 1,
        ),
        lambda text: text.replace(f'- name: {FINAL_NAME}\n        if: always()', f'- name: {FINAL_NAME}\n        if: success()', 1),
        lambda text: text.replace('--ignored=matching', '--ignored=no'),
        lambda text: text.replace('/usr/bin/python3 "$POSTFLIGHT_VERIFIER"', 'python3 candidate/scripts/verify_exact_git_index.py', 1),
        lambda text: text.replace('set -euo pipefail', 'set -euo pipefail\n          git clean -fdx', 1),
        lambda text: text.replace('PATH: /usr/bin:/bin', 'PATH: ${{ env.PATH }}', 1),
        lambda text: text.replace(
            "PYTHONDONTWRITEBYTECODE: '1'",
            "PYTHONDONTWRITEBYTECODE: '1'\n      PYTHONPYCACHEPREFIX: ${{ runner.temp }}/forbidden-job-cache", 1,
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
            original = path.read_text(encoding='utf-8')
            for mutation in mutations:
                mutated = mutation(original)
                if mutated == original:
                    raise AssertionError(f'{relative}: mutation did not alter fixture')
                path.write_text(mutated, encoding='utf-8')
                if not validate(root):
                    raise AssertionError(f'{relative}: hostile mutation was accepted')
            path.write_text(original, encoding='utf-8')


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args()
    if args.self_test:
        self_test()
    errors = validate(args.root)
    for error in errors:
        print(f'[BOOTSTRAP-FINAL-POSTFLIGHT] {error}')
    if errors:
        return 1
    print('[BOOTSTRAP-FINAL-POSTFLIGHT] PASS')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
