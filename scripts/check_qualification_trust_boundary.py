#!/usr/bin/env python3
"""Validate build-once -> preflight -> progressive PAPER -> certification trust boundaries."""
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
OWNER_GATE = (
    "github.event_name == 'workflow_dispatch' && "
    "github.ref == 'refs/heads/main' && "
    "github.repository == 'TrillionniumFoundation/heptatrader' && "
    "github.actor == 'ProfHepta' && "
    "github.actor_id == 102159240 && "
    "github.triggering_actor == 'ProfHepta' && "
    "inputs.mutation_mode == true && inputs.candidate_sha == github.sha"
)


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
        "merge_queue",
        "acknowledge_no_bypass",
        "verify_qualification_candidate.py",
        "github_qualification_evidence.py",
        "secrets.",
    ):
        if token in workflow:
            errors.append(f"{WORKFLOW}: retired or unsafe token remains: {token}")

    if workflow.count(OWNER_GATE) != 6:
        errors.append(
            f"{WORKFLOW}: all six self-hosted jobs must share the immutable owner/exact-SHA gate"
        )
    if workflow.count("Bind dispatch authority to immutable owner identity") < 2:
        errors.append(f"{WORKFLOW}: builder and host preflight must reassert immutable owner identity")
    for token in (
        "DISPATCH_ACTOR: ${{ github.actor }}",
        "DISPATCH_ACTOR_ID: ${{ github.actor_id }}",
        "TRIGGERING_ACTOR: ${{ github.triggering_actor }}",
        "test \"$DISPATCH_ACTOR\" = 'ProfHepta'",
        "test \"$DISPATCH_ACTOR_ID\" = '102159240'",
        "test \"$TRIGGERING_ACTOR\" = 'ProfHepta'",
    ):
        if token not in workflow:
            errors.append(f"{WORKFLOW}: missing immutable-owner assertion: {token}")

    if "ref: ${{ inputs.candidate_sha }}" in workflow:
        errors.append(f"{WORKFLOW}: candidate input may not select checkout control code")
    if "candidate/scripts/" in workflow:
        errors.append(f"{WORKFLOW}: candidate-controlled script execution is forbidden")
    if workflow.count("path: candidate") != 1:
        errors.append(f"{WORKFLOW}: candidate source may be checked out only once for the single build")
    if workflow.count("trusted/scripts/build_ib_candidate_artifact.sh") != 1:
        errors.append(f"{WORKFLOW}: candidate must be built exactly once")
    if workflow.count("Upload the single immutable no-secret candidate") != 1:
        errors.append(f"{WORKFLOW}: exactly one candidate artifact upload is required")
    if workflow.count("python3 trusted/scripts/verify_exact_git_index.py --root candidate") != 2:
        errors.append(f"{WORKFLOW}: candidate exact-tree verification must bracket the single build")

    for job in (
        "  build-candidate:\n",
        "  preflight:\n",
        "  canary:\n",
        "  pilot:\n",
        "  extended:\n",
        "  qualify:\n",
    ):
        if job not in workflow:
            errors.append(f"{WORKFLOW}: missing staged job: {job.strip()}")

    if workflow.count("\n    environment: ib-paper\n") != 4:
        errors.append(
            f"{WORKFLOW}: canary, pilot, extended and certification must use ib-paper environment"
        )
    preflight_block = workflow.split("\n  preflight:\n", 1)[1].split("\n  canary:\n", 1)[0] if "\n  preflight:\n" in workflow and "\n  canary:\n" in workflow else ""
    if "environment: ib-paper" in preflight_block:
        errors.append(f"{WORKFLOW}: lightweight host preflight must not request mutation environment")
    for token in (
        "ib-paper-lightweight-host-preflight",
        "/usr/libexec/hepta-ib-paper-host-probe",
        "HEPTA_IB_PAPER_HOST_PROBE_SHA256",
        "Verify target-host isolation and PAPER boundary only",
    ):
        if token not in workflow:
            errors.append(f"{WORKFLOW}: missing lightweight host preflight control: {token}")

    if workflow.count("run_ib_paper_artifact_rollout.sh") != 3:
        errors.append(f"{WORKFLOW}: canary/pilot/extended must each use the rollout wrapper")
    if workflow.count("verify_ib_paper_rollout.py") != 3:
        errors.append(f"{WORKFLOW}: every progressive stage must verify terminal rollout evidence")
    for stage in ("canary", "pilot", "extended"):
        if f"--expected-stage {stage}" not in workflow:
            errors.append(f"{WORKFLOW}: missing verified rollout stage: {stage}")
    for token in (
        "inputs.rollout_stage == 'pilot'",
        "inputs.rollout_stage == 'extended'",
        "inputs.rollout_stage == 'certify'",
        "Run one minimal terminal PAPER-V4 round trip",
        "Run three independently flat PAPER-V4 round trips",
        "Run ten independently flat PAPER-V4 round trips",
    ):
        if token not in workflow:
            errors.append(f"{WORKFLOW}: missing progressive promotion control: {token}")

    if workflow.count("run_ib_paper_artifact_qualification.sh") != 1:
        errors.append(f"{WORKFLOW}: heavy certification must execute exactly once and only on demand")
    if workflow.count("verify_ib_paper_qualification.py") != 1:
        errors.append(f"{WORKFLOW}: heavy certification must have one final verifier")
    qualify_block = workflow.split("\n  qualify:\n", 1)[1] if "\n  qualify:\n" in workflow else ""
    if "inputs.rollout_stage == 'certify'" not in qualify_block:
        errors.append(f"{WORKFLOW}: heavy certification must be explicitly selected")
    if "needs: extended" not in qualify_block:
        errors.append(f"{WORKFLOW}: heavy certification must follow successful extended rollout")

    artifact_name = "ib-paper-candidate-${{ github.sha }}-${{ github.run_id }}-${{ github.run_attempt }}"
    if workflow.count(artifact_name) < 6:
        errors.append(f"{WORKFLOW}: every post-build stage must consume the same candidate artifact identity")
    if workflow.count("HEPTA_QUALIFICATION_MUTATIONS: '1'") != 4:
        errors.append(f"{WORKFLOW}: mutation opt-in must be explicit in exactly four mutation stages")

    if "git ls-remote --exit-code" in workflow:
        errors.append(f"{WORKFLOW}: rollout must not depend on mutable main after artifact creation")
    for token in (
        "main-before-campaign.txt",
        "main-after-campaign.txt",
        "Issue final exact-current-main Broker receipt",
    ):
        if token in workflow:
            errors.append(f"{WORKFLOW}: obsolete mutable-main proof remains: {token}")

    for match in re.finditer(r"(?m)^\s*uses:\s*([^@\s]+)@([^\s]+)\s*$", workflow):
        action, revision = match.groups()
        if ACTION_SHA.fullmatch(revision) is None:
            errors.append(f"{WORKFLOW}: action not pinned: {action}@{revision}")
    return errors


def self_test() -> None:
    errors = validate(ROOT)
    if errors:
        raise RuntimeError("\n".join(errors))
    mutations = (
        (
            "inputs.candidate_sha == github.sha",
            "inputs.candidate_sha != github.sha",
            "six self-hosted jobs",
        ),
        (
            "trusted/scripts/build_ib_candidate_artifact.sh",
            "trusted/scripts/build_ib_candidate_artifact-removed.sh",
            "built exactly once",
        ),
        (
            "    environment: ib-paper\n",
            "",
            "four mutation stages",
        ),
        (
            "trusted/scripts/run_ib_paper_artifact_rollout.sh",
            "trusted/scripts/run_ib_paper_artifact_rollout-removed.sh",
            "rollout wrapper",
        ),
        (
            "    needs: extended\n    name: ib-paper-exact-artifact-qualification",
            "    needs: canary\n    name: ib-paper-exact-artifact-qualification",
            "must follow successful extended rollout",
        ),
        (
            "ref: ${{ github.sha }}",
            "ref: ${{ inputs.candidate_sha }}",
            "may not select checkout control code",
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
                raise RuntimeError(f"self-test failed to reject mutation {old!r}: {mutated}")

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        target = root / WORKFLOW
        target.parent.mkdir(parents=True)
        shutil.copy2(ROOT / WORKFLOW, target)
        text = target.read_text(encoding="utf-8")
        marker = "      - name: Run explicit heavy twelve-scenario PAPER certification\n"
        target.write_text(
            text.replace(
                marker,
                "      - name: Obsolete mutable-main check\n"
                "        run: git ls-remote --exit-code https://github.com/example/example refs/heads/main\n\n"
                + marker,
                1,
            ),
            encoding="utf-8",
        )
        mutated = validate(root)
        if not any("must not depend on mutable main" in item for item in mutated):
            raise RuntimeError(f"self-test failed to reject mutable-main proof: {mutated}")


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
