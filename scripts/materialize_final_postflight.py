#!/usr/bin/env python3
"""Materialize the reviewed immutable final postflight over exact workflow blobs."""
from pathlib import Path

WORKFLOWS = (
    (Path('.github/workflows/github-governance-qualification.yml'), 'github-governance', 'qualify'),
    (Path('.github/workflows/ib-paper-qualification.yml'), 'ib-paper', 'build-candidate'),
)


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f'{label}: expected one anchor, found {count}')
    return text.replace(old, new, 1)


def final_step(prefix: str) -> str:
    return f'''      - name: Reassert immutable exact source after candidate-controlled validation
        if: always()
        env:
          BASH_ENV: /dev/null
          ENV: /dev/null
          EXPECTED_HEAD_SHA: ${{{{ github.event.pull_request.head.sha }}}}
          EXPECTED_TREE_SHA: ${{{{ steps.exact_source.outputs.head_tree }}}}
          EXPECTED_VERIFIER_SHA256: ${{{{ steps.exact_source.outputs.verifier_sha256 }}}}
          LD_LIBRARY_PATH: ''
          LD_PRELOAD: ''
          PATH: /usr/bin:/bin
          POSTFLIGHT_VERIFIER: ${{{{ runner.temp }}}}/{prefix}-exact-index-postflight.py
          PYTHONHOME: ''
          PYTHONPATH: ''
        run: |
          set -euo pipefail
          test -n "$EXPECTED_TREE_SHA"
          test -n "$EXPECTED_VERIFIER_SHA256"
          actual_verifier_sha256="$(/usr/bin/sha256sum "$POSTFLIGHT_VERIFIER" | /usr/bin/awk '{{print $1}}')"
          test "$actual_verifier_sha256" = "$EXPECTED_VERIFIER_SHA256"
          test "$(/usr/bin/git -C candidate rev-parse HEAD)" = "$EXPECTED_HEAD_SHA"
          test "$(/usr/bin/git -C candidate rev-parse 'HEAD^{{tree}}')" = "$EXPECTED_TREE_SHA"
          test "$(/usr/bin/git -C postflight rev-parse HEAD)" = "$EXPECTED_HEAD_SHA"
          test "$(/usr/bin/git -C postflight rev-parse 'HEAD^{{tree}}')" = "$EXPECTED_TREE_SHA"
          /usr/bin/env -i \\
            PATH=/usr/bin:/bin \\
            HOME="$RUNNER_TEMP/{prefix}-final-postflight-home" \\
            GIT_CONFIG_NOSYSTEM=1 \\
            GIT_CONFIG_GLOBAL=/dev/null \\
            GIT_NO_REPLACE_OBJECTS=1 \\
            PYTHONDONTWRITEBYTECODE=1 \\
            PYTHONPYCACHEPREFIX="$RUNNER_TEMP/{prefix}-final-postflight-pycache" \\
            /usr/bin/python3 "$POSTFLIGHT_VERIFIER" --root "$GITHUB_WORKSPACE/candidate"
          /usr/bin/env -i \\
            PATH=/usr/bin:/bin \\
            HOME="$RUNNER_TEMP/{prefix}-final-postflight-home" \\
            GIT_CONFIG_NOSYSTEM=1 \\
            GIT_CONFIG_GLOBAL=/dev/null \\
            GIT_NO_REPLACE_OBJECTS=1 \\
            PYTHONDONTWRITEBYTECODE=1 \\
            PYTHONPYCACHEPREFIX="$RUNNER_TEMP/{prefix}-final-postflight-pycache" \\
            /usr/bin/python3 "$POSTFLIGHT_VERIFIER" --root "$GITHUB_WORKSPACE/postflight"
          candidate_status="$(/usr/bin/env -i \\
            PATH=/usr/bin:/bin \\
            HOME="$RUNNER_TEMP/{prefix}-final-postflight-home" \\
            GIT_CONFIG_NOSYSTEM=1 \\
            GIT_CONFIG_GLOBAL=/dev/null \\
            GIT_NO_REPLACE_OBJECTS=1 \\
            /usr/bin/git -C "$GITHUB_WORKSPACE/candidate" status \\
              --porcelain=v2 --untracked-files=all --ignored=matching)"
          postflight_status="$(/usr/bin/env -i \\
            PATH=/usr/bin:/bin \\
            HOME="$RUNNER_TEMP/{prefix}-final-postflight-home" \\
            GIT_CONFIG_NOSYSTEM=1 \\
            GIT_CONFIG_GLOBAL=/dev/null \\
            GIT_NO_REPLACE_OBJECTS=1 \\
            /usr/bin/git -C "$GITHUB_WORKSPACE/postflight" status \\
              --porcelain=v2 --untracked-files=all --ignored=matching)"
          test -z "$candidate_status"
          test -z "$postflight_status"
'''


def patch(path: Path, prefix: str, next_job: str) -> None:
    text = path.read_text(encoding='utf-8')
    text = replace_once(
        text,
        '    timeout-minutes: 25\n    steps:\n',
        "    timeout-minutes: 25\n"
        "    env:\n"
        "      PYTHONDONTWRITEBYTECODE: '1'\n"
        f"      PYTHONPYCACHEPREFIX: ${{{{ runner.temp }}}}/{prefix}-bootstrap-pycache\n"
        "    steps:\n",
        f'{path}: bootstrap environment',
    )
    old_preflight = '''      - name: Assert exact bootstrap revision and clean Git authority
        working-directory: candidate
        env:
          EXPECTED_SHA: ${{ github.event.pull_request.head.sha }}
        run: |
          set -euo pipefail
          test "$(git rev-parse HEAD)" = "$EXPECTED_SHA"
          python3 scripts/verify_exact_git_index.py --root .
'''
    new_preflight = f'''      - name: Assert exact bootstrap revision and capture immutable final verifier
        id: exact_source
        working-directory: candidate
        env:
          EXPECTED_SHA: ${{{{ github.event.pull_request.head.sha }}}}
        run: |
          set -euo pipefail
          test "$(/usr/bin/git rev-parse HEAD)" = "$EXPECTED_SHA"
          /usr/bin/python3 scripts/verify_exact_git_index.py --root .
          postflight="$RUNNER_TEMP/{prefix}-exact-index-postflight.py"
          /usr/bin/install -m 0500 scripts/verify_exact_git_index.py "$postflight"
          verifier_sha256="$(/usr/bin/sha256sum "$postflight" | /usr/bin/awk '{{print $1}}')"
          head_tree="$(/usr/bin/git rev-parse 'HEAD^{{tree}}')"
          {{
            echo "verifier_sha256=$verifier_sha256"
            echo "head_tree=$head_tree"
          }} >> "$GITHUB_OUTPUT"
'''
    text = replace_once(text, old_preflight, new_preflight, f'{path}: immutable preflight')
    text = replace_once(
        text,
        '            scripts/verify_exact_git_index.py \\\n',
        '            scripts/verify_exact_git_index.py \\\n'
        '            scripts/verify_bootstrap_postflight_contract.py \\\n',
        f'{path}: compile contract',
    )
    text = replace_once(
        text,
        '          python3 scripts/check_qualification_trust_boundary.py --self-test\n',
        '          python3 scripts/verify_bootstrap_postflight_contract.py --self-test\n'
        '          python3 scripts/check_qualification_trust_boundary.py --self-test\n',
        f'{path}: contract self-test',
    )
    text = replace_once(
        text,
        "          python3 -m unittest discover -s tests/python -p 'test_bootstrap_postflight.py'\n",
        "          python3 -m unittest discover -s tests/python -p 'test_bootstrap_postflight.py'\n"
        "          python3 -m unittest discover -s tests/python -p 'test_bootstrap_postflight_contract.py'\n",
        f'{path}: contract regression',
    )
    boundary = f'\n\n\n  {next_job}:\n'
    text = replace_once(
        text,
        boundary,
        '\n' + final_step(prefix) + f'\n\n  {next_job}:\n',
        f'{path}: final bootstrap position',
    )
    path.write_text(text, encoding='utf-8', newline='')


for workflow, prefix, next_job in WORKFLOWS:
    patch(workflow, prefix, next_job)
