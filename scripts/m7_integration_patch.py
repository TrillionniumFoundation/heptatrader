#!/usr/bin/env python3
"""Apply the durable M7 merge-candidate verification integration."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read_json(relative: str):
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def write_json(relative: str, value, *, compact: bool = False) -> None:
    path = ROOT / relative
    if compact:
        rendered = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    else:
        rendered = json.dumps(value, ensure_ascii=False, indent=2)
    path.write_text(rendered + "\n", encoding="utf-8")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        if new in text:
            return text
        raise RuntimeError(f"{label}: anchor is missing")
    if text.count(old) != 1:
        raise RuntimeError(f"{label}: anchor is not unique")
    return text.replace(old, new, 1)


def patch_test_matrix() -> None:
    relative = "docs/verification/test-matrix-v2.json"
    value = read_json(relative)
    found_strategy = False
    found_impact = False
    for check in value["checks"]:
        if check.get("id") == "strategy-isolation":
            check["state"] = "implemented"
            check["evidence"] = (
                "StrategyProposal authority exclusion, dependency boundary and "
                "lifecycle quarantine fault-isolation CTests"
            )
            found_strategy = True
        elif check.get("id") == "merge-candidate-impact":
            check["state"] = "implemented"
            check["lane"] = "C-merge-candidate"
            check["evidence"] = (
                "synthetic merge parent binding, physical owner and reverse "
                "dependency impact evidence"
            )
            found_impact = True
    if not found_strategy:
        raise RuntimeError("strategy-isolation verification entry is missing")
    if not found_impact:
        value["checks"].append({
            "id": "merge-candidate-impact",
            "lane": "C-merge-candidate",
            "state": "implemented",
            "evidence": (
                "synthetic merge parent binding, physical owner and reverse "
                "dependency impact evidence"
            ),
        })
    write_json(relative, value, compact=True)


def patch_program_state() -> None:
    relative = "docs/program/gap-registry-v2.json"
    value = read_json(relative)
    found = False
    for gap in value["gaps"]:
        if gap.get("id") != "G-REL-002":
            continue
        gap["state"] = "in-progress"
        evidence = gap.setdefault("evidence", [])
        if "merge-candidate-impact" not in evidence:
            evidence.append("merge-candidate-impact")
        found = True
    if not found:
        raise RuntimeError("G-REL-002 is missing")
    write_json(relative, value, compact=True)

    relative = "docs/program/milestone-registry-v1.json"
    value = read_json(relative)
    found = False
    for milestone in value["milestones"]:
        if milestone.get("id") == "M7":
            milestone["state"] = "in-progress"
            found = True
    if not found:
        raise RuntimeError("M7 is missing")
    write_json(relative, value)


def patch_python_compile_lists() -> None:
    paths = (
        ".github/workflows/core-ci.yml",
        ".github/workflows/canonical-full-suite.yml",
    )
    for relative in paths:
        path = ROOT / relative
        text = path.read_text(encoding="utf-8")
        if "scripts/check_change_impact.py" not in text:
            text = replace_once(
                text,
                "            scripts/check_cmake_module_graph.py \\\n",
                "            scripts/check_cmake_module_graph.py \\\n"
                "            scripts/check_change_impact.py \\\n",
                relative,
            )
        path.write_text(text, encoding="utf-8")

    relative = ".github/workflows/documentation-control-plane.yml"
    path = ROOT / relative
    text = path.read_text(encoding="utf-8")
    if "scripts/check_change_impact.py" not in text:
        text = replace_once(
            text,
            "            scripts/check_cmake_module_graph.py\n",
            "            scripts/check_cmake_module_graph.py \\\n"
            "            scripts/check_change_impact.py\n",
            relative,
        )
    path.write_text(text, encoding="utf-8")


def write_merge_candidate_workflow() -> None:
    path = ROOT / ".github/workflows/merge-candidate.yml"
    path.write_text(
        """name: merge-candidate

on:
  pull_request: {}
  merge_group: {}

permissions:
  contents: read

concurrency:
  group: merge-candidate-${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true

env:
  HEPTA_JOBS: \"2\"

jobs:
  pull-request-candidate:
    name: exact-pr-merge-candidate
    if: github.event_name == 'pull_request'
    runs-on: ubuntu-24.04
    timeout-minutes: 45
    steps:
      - name: Checkout synthetic merge candidate
        uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262
        with:
          ref: ${{ github.sha }}
          fetch-depth: 0
          persist-credentials: false
      - name: Install deterministic toolchain
        run: |
          sudo apt-get update
          sudo apt-get install --yes --no-install-recommends \\
            cmake ninja-build g++ libssl-dev python3 python3-jsonschema
      - name: Bind exact parents and derive module impact
        env:
          BASE_SHA: ${{ github.event.pull_request.base.sha }}
          HEAD_SHA: ${{ github.event.pull_request.head.sha }}
          MERGE_SHA: ${{ github.sha }}
        run: |
          set -euo pipefail
          test \"$(git rev-parse HEAD)\" = \"$MERGE_SHA\"
          python3 scripts/check_change_impact.py \\
            --base \"$BASE_SHA\" \\
            --head \"$HEAD_SHA\" \\
            --merge-candidate \"$MERGE_SHA\" \\
            --output \"$RUNNER_TEMP/change-impact.json\"
      - name: Validate merged deterministic core
        env:
          HEPTA_BUILD_TYPE: Release
          HEPTA_CMAKE_GENERATOR: Ninja
          HEPTA_BUILD_DIR: ${{ runner.temp }}/heptatrader-merge-core
        run: ./scripts/dev_core.sh
      - name: Validate merged reliability and performance lane
        env:
          CXX: g++
          HEPTA_CMAKE_GENERATOR: Ninja
          HEPTA_RELIABILITY_BUILD_DIR: ${{ runner.temp }}/heptatrader-merge-reliability
        run: ./scripts/reliability_core.sh
      - name: Publish deterministic impact evidence
        uses: actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a
        with:
          name: change-impact-${{ github.event.pull_request.number }}-${{ github.sha }}
          path: ${{ runner.temp }}/change-impact.json
          if-no-files-found: error
          retention-days: 30

  merge-queue-candidate:
    name: exact-merge-group-candidate
    if: github.event_name == 'merge_group'
    runs-on: ubuntu-24.04
    timeout-minutes: 45
    steps:
      - name: Checkout exact merge-group revision
        uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262
        with:
          ref: ${{ github.sha }}
          fetch-depth: 0
          persist-credentials: false
      - name: Assert exact merge-group revision
        run: test \"$(git rev-parse HEAD)\" = \"${{ github.sha }}\"
      - name: Install deterministic toolchain
        run: |
          sudo apt-get update
          sudo apt-get install --yes --no-install-recommends \\
            cmake ninja-build g++ libssl-dev python3 python3-jsonschema
      - name: Validate merge-group deterministic core
        env:
          HEPTA_BUILD_TYPE: Release
          HEPTA_CMAKE_GENERATOR: Ninja
          HEPTA_BUILD_DIR: ${{ runner.temp }}/heptatrader-merge-group-core
        run: ./scripts/dev_core.sh
      - name: Validate merge-group reliability and performance lane
        env:
          CXX: g++
          HEPTA_CMAKE_GENERATOR: Ninja
          HEPTA_RELIABILITY_BUILD_DIR: ${{ runner.temp }}/heptatrader-merge-group-reliability
        run: ./scripts/reliability_core.sh
""",
        encoding="utf-8",
    )


def patch_workflow_documentation() -> None:
    relative = "docs/development/PULL-REQUEST-WORKFLOW.md"
    path = ROOT / relative
    text = path.read_text(encoding="utf-8")
    heading = "## Exact merge-candidate and impact evidence"
    if heading not in text:
        text = text.rstrip() + """

## Exact merge-candidate and impact evidence

`merge-candidate` Lane C checks GitHub's synthetic two-parent merge commit, not
only the source branch head. It binds the first parent to the live base SHA and
the second parent to the live PR head SHA, derives directly changed physical
owners, expands through the reverse module dependency graph, and records a
canonical `heptatrader.change-impact.v1` digest. Contract, build, governance,
test and unknown surfaces conservatively expand to every active module.

The same merged revision then runs the deterministic core plus the bounded
ASAN/UBSAN reliability and performance lane. A merge queue revision receives
the same full validation. Impact selection may add evidence and reviewers; it
must never remove the full merge-candidate gates or external qualification.
"""
    path.write_text(text, encoding="utf-8")


def main() -> None:
    patch_test_matrix()
    patch_program_state()
    patch_python_compile_lists()
    write_merge_candidate_workflow()
    patch_workflow_documentation()


if __name__ == "__main__":
    main()
