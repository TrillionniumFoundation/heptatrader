#!/usr/bin/env python3
# Validate the owner-operated exact-current-main IB PAPER boundary.
from __future__ import annotations

import argparse
from pathlib import Path
import re
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = Path(".github/workflows/ib-paper-qualification.yml")
RETIRED = (
    Path(".github/CODEOWNERS"),
    Path(".github/github-governance-policy-v1.json"),
    Path(".github/github-team-mapping-v1.json"),
    Path(".github/workflows/github-governance-qualification.yml"),
    Path(".github/workflows/governance-bootstrap-admission.yml"),
    Path("scripts/github_qualification_evidence.py"),
    Path("scripts/verify_github_governance.py"),
    Path("scripts/verify_qualification_candidate.py"),
)
ACTION_SHA = re.compile(r"^[0-9a-f]{40}$")


def validate(root: Path | str = ROOT) -> list[str]:
    root = Path(root).resolve()
    errors: list[str] = []
    for relative in RETIRED:
        if (root / relative).exists() or (root / relative).is_symlink():
            errors.append(f"retired governance artifact remains: {relative}")

    path = root / WORKFLOW
    try:
        workflow = path.read_text(encoding="utf-8")
    except OSError as error:
        return [f"{WORKFLOW}: {error}"]

    for token in (
        "pull_number",
        "repository-governance",
        "CODEOWNERS",
        "merge_group",
        "merge_queue",
        "acknowledge_no_bypass",
        "verify_qualification_candidate.py",
        "github_qualification_evidence.py",
        "secrets.",
    ):
        if token in workflow:
            errors.append(f"{WORKFLOW}: retired or unsafe token remains: {token}")

    condition = (
        "github.event_name == 'workflow_dispatch' && "
        "github.ref == 'refs/heads/main' && "
        "github.repository == 'TrillionniumFoundation/heptatrader' && "
        "github.actor == 'ProfHepta' && "
        "github.actor_id == 102159240 && "
        "github.triggering_actor == 'ProfHepta' && "
        "inputs.mutation_mode == true && inputs.candidate_sha == github.sha"
    )
    if workflow.count(condition) != 2:
        errors.append(
            f"{WORKFLOW}: both jobs must require immutable owner dispatch "
            "authority and exact current main before runner allocation"
        )
    if workflow.count("Bind dispatch authority to immutable owner identity") != 2:
        errors.append(f"{WORKFLOW}: both jobs must reassert immutable owner identity")
    if workflow.count(
        "python3 trusted/scripts/verify_exact_git_index.py --root trusted"
    ) < 4:
        errors.append(f"{WORKFLOW}: trusted checkout exact-tree verification is incomplete")
    if workflow.count(
        "python3 trusted/scripts/verify_exact_git_index.py --root candidate"
    ) != 2:
        errors.append(f"{WORKFLOW}: candidate exact-tree verification must bracket build")
    if workflow.count("ref: ${{ github.sha }}") != 3:
        errors.append(f"{WORKFLOW}: every trusted and candidate checkout must use github.sha")
    if workflow.count("${{ inputs.candidate_sha }}") != 1:
        errors.append(
            f"{WORKFLOW}: candidate input must only enter the quoted exact-main gate"
        )
    if workflow.count("git ls-remote --exit-code") < 3:
        errors.append(f"{WORKFLOW}: current main must be checked before and after campaign")

    for token in (
        "heptatrader-ib-builder",
        "heptatrader-ib-paper",
        "build_ib_candidate_artifact.sh",
        "verify_ib_candidate_artifact.py",
        "run_ib_paper_artifact_qualification.sh",
        "verify_ib_paper_qualification.py",
        "Record exact remote main before Broker campaign",
        "Reverify unchanged remote main after Broker campaign",
        "qualification-verification.json",
        "HEPTA_QUALIFICATION_MUTATIONS: '1'",
        "DISPATCH_ACTOR: ${{ github.actor }}",
        "DISPATCH_ACTOR_ID: ${{ github.actor_id }}",
        "TRIGGERING_ACTOR: ${{ github.triggering_actor }}",
        "test \"$DISPATCH_ACTOR\" = 'ProfHepta'",
        "test \"$DISPATCH_ACTOR_ID\" = '102159240'",
        "test \"$TRIGGERING_ACTOR\" = 'ProfHepta'",
        "verify_exact_git_index.py",
    ):
        if token not in workflow:
            errors.append(f"{WORKFLOW}: missing token: {token}")

    for match in re.finditer(r"(?m)^\s*uses:\s*([^@\s]+)@([^\s]+)\s*$", workflow):
        action, revision = match.groups()
        if ACTION_SHA.fullmatch(revision) is None:
            errors.append(f"{WORKFLOW}: action not pinned: {action}@{revision}")

    if "candidate/scripts/" in workflow:
        errors.append(f"{WORKFLOW}: candidate-controlled script execution is forbidden")
    return errors


def self_test() -> None:
    errors = validate(ROOT)
    if errors:
        raise RuntimeError("\n".join(errors))
    mutations = (
        (
            "inputs.candidate_sha == github.sha",
            "inputs.candidate_sha != github.sha",
            "exact current main",
        ),
        (
            "github.actor == 'ProfHepta'",
            "github.actor != 'ProfHepta'",
            "immutable owner dispatch authority",
        ),
        (
            "python3 trusted/scripts/verify_exact_git_index.py --root candidate",
            "python3 trusted/scripts/verify_exact_git_index.py --root missing",
            "candidate exact-tree verification",
        ),
        (
            "ref: ${{ github.sha }}",
            "ref: ${{ inputs.candidate_sha }}",
            "every trusted and candidate checkout must use github.sha",
        ),
    )
    for old, new, expected in mutations:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / WORKFLOW
            target.parent.mkdir(parents=True)
            shutil.copy2(ROOT / WORKFLOW, target)
            text = target.read_text(encoding="utf-8")
            if text.count(old) == 0:
                raise RuntimeError(f"self-test fixture token missing: {old}")
            target.write_text(text.replace(old, new, 1), encoding="utf-8")
            mutated = validate(root)
            if not any(expected in item for item in mutated):
                raise RuntimeError(
                    f"self-test failed to reject mutation {old!r}: {mutated}"
                )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test:
        self_test()
        print("[QUALIFICATION-BOUNDARY] SELF-TEST PASS")
        return 0
    errors = validate(args.root)
    for error in errors:
        print(f"[QUALIFICATION-BOUNDARY] {error}", file=sys.stderr)
    if errors:
        return 1
    print("[QUALIFICATION-BOUNDARY] PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
