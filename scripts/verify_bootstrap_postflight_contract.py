#!/usr/bin/env python3
"""Fail closed when PR bootstrap workflows omit immutable final source checks."""
from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import stat
import tempfile

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = (
    Path('.github/workflows/github-governance-qualification.yml'),
    Path('.github/workflows/ib-paper-qualification.yml'),
)
POSTFLIGHT_NAME = 'Reassert immutable exact source after candidate-controlled validation'
REQUIRED_TOKENS = (
    'PYTHONDONTWRITEBYTECODE:',
    'PYTHONPYCACHEPREFIX: ${{ runner.temp }}/',
    'id: exact_source',
    '/usr/bin/python3 scripts/verify_exact_git_index.py --root .',
    '/usr/bin/install -m 0500 scripts/verify_exact_git_index.py "$postflight"',
    'verifier_sha256="$(/usr/bin/sha256sum "$postflight"',
    "head_tree=\"$(/usr/bin/git rev-parse 'HEAD^{tree}')\"",
    f'- name: {POSTFLIGHT_NAME}',
    'if: always()',
    'EXPECTED_TREE_SHA: ${{ steps.exact_source.outputs.head_tree }}',
    'EXPECTED_VERIFIER_SHA256: ${{ steps.exact_source.outputs.verifier_sha256 }}',
    '/usr/bin/sha256sum "$POSTFLIGHT_VERIFIER"',
    '/usr/bin/env -i',
    '/usr/bin/python3 "$POSTFLIGHT_VERIFIER" --root "$GITHUB_WORKSPACE"',
    '--porcelain=v2 --untracked-files=all --ignored=matching',
    'test -z "$clean_status"',
)
FORBIDDEN_CLEANUP = (
    'git reset --hard',
    '/usr/bin/git reset --hard',
    'git clean -fd',
    '/usr/bin/git clean -fd',
    'git clean -xd',
    '/usr/bin/git clean -xd',
    'git checkout -- .',
    '/usr/bin/git checkout -- .',
    'git restore .',
    '/usr/bin/git restore .',
)


def _read_regular(root: Path, relative: Path, errors: list[str]) -> str | None:
    path = root / relative
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            errors.append(f'{relative}: must be a regular single-link file')
            return None
        return path.read_text(encoding='utf-8')
    except (OSError, UnicodeError) as exc:
        errors.append(f'{relative}: unreadable: {exc}')
        return None


def _validate_workflow(relative: Path, text: str, errors: list[str]) -> None:
    label = relative.as_posix()
    for token in REQUIRED_TOKENS:
        if token not in text:
            errors.append(f'{label}: missing immutable postflight token: {token}')
    lowered = text.lower()
    for token in FORBIDDEN_CLEANUP:
        if token in lowered:
            errors.append(f'{label}: evidence-destroying cleanup is forbidden: {token}')

    capture = text.find('id: exact_source')
    validation = text.find('Validate trusted harnesses')
    if validation < 0:
        validation = text.find('Validate PAPER qualification harnesses')
    postflight = text.find(f'- name: {POSTFLIGHT_NAME}')
    dispatch = text.find('\n  build-candidate:')
    if dispatch < 0:
        dispatch = text.find('\n  qualify:')
    if min(capture, validation, postflight, dispatch) < 0:
        errors.append(f'{label}: bootstrap/postflight ordering anchors are incomplete')
    elif not capture < validation < postflight < dispatch:
        errors.append(f'{label}: immutable postflight must be the final bootstrap step')

    postflight_block = text[postflight:dispatch] if postflight >= 0 and dispatch > postflight else ''
    if postflight_block.count('if: always()') != 1:
        errors.append(f'{label}: final bootstrap postflight must run exactly once with always()')
    if 'GIT_CONFIG_NOSYSTEM=1' not in postflight_block or 'GIT_NO_REPLACE_OBJECTS=1' not in postflight_block:
        errors.append(f'{label}: postflight Git plumbing environment is not isolated')


def validate(root: Path = ROOT) -> list[str]:
    root = Path(root).resolve()
    errors: list[str] = []
    for relative in WORKFLOWS:
        text = _read_regular(root, relative, errors)
        if text is not None:
            _validate_workflow(relative, text, errors)
    return errors


def self_test() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        for relative in WORKFLOWS:
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, target)
        baseline = validate(root)
        if baseline:
            raise AssertionError(baseline)

        mutations = (
            lambda text: text.replace(f'- name: {POSTFLIGHT_NAME}', '- name: Removed final source check', 1),
            lambda text: text.replace('set -euo pipefail', 'set -euo pipefail\n          git clean -fdx', 1),
            lambda text: text.replace('--ignored=matching', '--ignored=no', 1),
            lambda text: text.replace('if: always()', 'if: success()', 1),
            lambda text: text.replace('/usr/bin/sha256sum "$POSTFLIGHT_VERIFIER"', '/usr/bin/sha256sum scripts/verify_exact_git_index.py', 1),
        )
        for relative in WORKFLOWS:
            original = (root / relative).read_text(encoding='utf-8')
            for mutate in mutations:
                (root / relative).write_text(mutate(original), encoding='utf-8')
                if not validate(root):
                    raise AssertionError(f'{relative}: hostile mutation was accepted')
            (root / relative).write_text(original, encoding='utf-8')


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args()
    if args.self_test:
        self_test()
    errors = validate(args.root)
    for error in errors:
        print(f'[BOOTSTRAP-POSTFLIGHT] {error}')
    if errors:
        return 1
    print('[BOOTSTRAP-POSTFLIGHT] PASS')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
