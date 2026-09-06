#!/usr/bin/env python3
"""Staging-only deterministic materializer for the reviewed IB postflight patch."""
from pathlib import Path

path = Path('.github/workflows/ib-paper-qualification.yml')
text = path.read_text(encoding='utf-8')

def replace_once(old: str, new: str, label: str) -> None:
    global text
    count = text.count(old)
    if count != 1:
        raise SystemExit(f'{label}: expected one anchor, found {count}')
    text = text.replace(old, new, 1)

replace_once(
    "    timeout-minutes: 25\n    steps:\n",
    "    timeout-minutes: 25\n"
    "    env:\n"
    "      PYTHONDONTWRITEBYTECODE: '1'\n"
    "      PYTHONPYCACHEPREFIX: ${{ runner.temp }}/ib-paper-bootstrap-pycache\n"
    "    steps:\n",
    'bootstrap environment',
)
replace_once(
    "      - name: Assert exact bootstrap revision and clean Git authority\n"
    "        env:\n"
    "          EXPECTED_SHA: ${{ github.event.pull_request.head.sha }}\n"
    "        run: |\n"
    "          set -euo pipefail\n"
    "          test \"$(git rev-parse HEAD)\" = \"$EXPECTED_SHA\"\n"
    "          python3 scripts/verify_exact_git_index.py --root .\n",
    "      - name: Assert exact bootstrap revision and capture immutable postflight verifier\n"
    "        id: exact_source\n"
    "        env:\n"
    "          EXPECTED_SHA: ${{ github.event.pull_request.head.sha }}\n"
    "        run: |\n"
    "          set -euo pipefail\n"
    "          test \"$(/usr/bin/git rev-parse HEAD)\" = \"$EXPECTED_SHA\"\n"
    "          /usr/bin/python3 scripts/verify_exact_git_index.py --root .\n"
    "          postflight=\"$RUNNER_TEMP/ib-paper-exact-index-postflight.py\"\n"
    "          /usr/bin/install -m 0500 scripts/verify_exact_git_index.py \"$postflight\"\n"
    "          verifier_sha256=\"$(/usr/bin/sha256sum \"$postflight\" | /usr/bin/awk '{print $1}')\"\n"
    "          head_tree=\"$(/usr/bin/git rev-parse 'HEAD^{tree}')\"\n"
    "          {\n"
    "            echo \"verifier_sha256=$verifier_sha256\"\n"
    "            echo \"head_tree=$head_tree\"\n"
    "          } >> \"$GITHUB_OUTPUT\"\n",
    'exact-source capture',
)
replace_once(
    "            scripts/verify_exact_git_index.py \\\n"
    "            scripts/verify_ib_paper_qualification.py \\\n",
    "            scripts/verify_exact_git_index.py \\\n"
    "            scripts/verify_bootstrap_postflight_contract.py \\\n"
    "            scripts/verify_ib_paper_qualification.py \\\n",
    'postflight static checker compilation',
)
replace_once(
    "          python3 scripts/check_qualification_trust_boundary.py --self-test\n",
    "          python3 scripts/verify_bootstrap_postflight_contract.py --self-test\n"
    "          python3 scripts/check_qualification_trust_boundary.py --self-test\n",
    'postflight hostile self-test',
)
postflight = r'''      - name: Reassert immutable exact source after candidate-controlled validation
      if: always()
      env:
        BASH_ENV: /dev/null
        ENV: /dev/null
        EXPECTED_HEAD_SHA: ${{ github.event.pull_request.head.sha }}
        EXPECTED_TREE_SHA: ${{ steps.exact_source.outputs.head_tree }}
        EXPECTED_VERIFIER_SHA256: ${{ steps.exact_source.outputs.verifier_sha256 }}
        LD_LIBRARY_PATH: ''
        LD_PRELOAD: ''
        PATH: /usr/bin:/bin
        POSTFLIGHT_VERIFIER: ${{ runner.temp }}/ib-paper-exact-index-postflight.py
        PYTHONHOME: ''
        PYTHONPATH: ''
      run: |
        set -euo pipefail
        test -n "$EXPECTED_TREE_SHA"
        test -n "$EXPECTED_VERIFIER_SHA256"
        actual_verifier_sha256="$(/usr/bin/sha256sum "$POSTFLIGHT_VERIFIER" | /usr/bin/awk '{print $1}')"
        test "$actual_verifier_sha256" = "$EXPECTED_VERIFIER_SHA256"
        test "$(/usr/bin/git rev-parse HEAD)" = "$EXPECTED_HEAD_SHA"
        test "$(/usr/bin/git rev-parse 'HEAD^{tree}')" = "$EXPECTED_TREE_SHA"
        /usr/bin/env -i \
          PATH=/usr/bin:/bin \
          HOME="$RUNNER_TEMP/ib-paper-postflight-home" \
          GIT_CONFIG_NOSYSTEM=1 \
          GIT_CONFIG_GLOBAL=/dev/null \
          GIT_NO_REPLACE_OBJECTS=1 \
          PYTHONDONTWRITEBYTECODE=1 \
          PYTHONPYCACHEPREFIX="$RUNNER_TEMP/ib-paper-postflight-pycache" \
          /usr/bin/python3 "$POSTFLIGHT_VERIFIER" --root "$GITHUB_WORKSPACE"
        clean_status="$(/usr/bin/env -i \
          PATH=/usr/bin:/bin \
          HOME="$RUNNER_TEMP/ib-paper-postflight-home" \
          GIT_CONFIG_NOSYSTEM=1 \
          GIT_CONFIG_GLOBAL=/dev/null \
          GIT_NO_REPLACE_OBJECTS=1 \
          /usr/bin/git -C "$GITHUB_WORKSPACE" status \
            --porcelain=v2 --untracked-files=all --ignored=matching)"
        test -z "$clean_status"
'''
replace_once(
    "\n  build-candidate:\n",
    "\n" + postflight + "\n  build-candidate:\n",
    'final bootstrap position',
)
path.write_text(text, encoding='utf-8', newline='')
