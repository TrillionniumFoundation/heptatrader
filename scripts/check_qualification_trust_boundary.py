#!/usr/bin/env python3
"""Fail closed on unsafe governance or IB qualification composition.

This checker proves checked-in workflow/script composition only. It cannot
create teams, rulesets, environments, runners, credentials, Broker evidence,
or qualification receipts.
"""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
GOVERNANCE = Path(".github/workflows/github-governance-qualification.yml")
IB = Path(".github/workflows/ib-paper-qualification.yml")
TRUSTED_FILES = (
    Path("scripts/github_qualification_evidence.py"),
    Path("scripts/verify_exact_git_index.py"),
    Path("scripts/verify_github_governance.py"),
    Path("scripts/verify_github_governance_legacy.py"),
    Path("scripts/verify_ib_paper_qualification.py"),
    Path("scripts/verify_ib_candidate_artifact.py"),
    Path("scripts/verify_qualification_candidate.py"),
    Path("scripts/build_ib_candidate_artifact.sh"),
    Path("scripts/run_ib_paper_artifact_qualification.sh"),
)
JOB_RE = re.compile(r"^  ([A-Za-z0-9_.-]+):\s*(?:#.*)?$", re.MULTILINE)
REF_INPUT_RE = re.compile(r"^\s*ref:\s*\$\{\{\s*inputs\.", re.MULTILINE)


def _read(root: Path, relative: Path, errors: list[str]) -> str:
    path = root / relative
    try:
        info = path.lstat()
        if path.is_symlink() or not path.is_file() or info.st_nlink != 1:
            errors.append(f"{relative}: must be a regular single-link file")
            return ""
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        errors.append(f"{relative}: unreadable: {exc}")
        return ""


def _job(text: str, job_id: str, label: str, errors: list[str]) -> str:
    matches = list(JOB_RE.finditer(text))
    selected = [item for item in matches if item.group(1) == job_id]
    if len(selected) != 1:
        errors.append(f"{label}: expected exactly one job {job_id}")
        return ""
    start = selected[0].start()
    later = [item.start() for item in matches if item.start() > start]
    return text[start : min(later) if later else len(text)]


def _require(block: str, tokens: tuple[str, ...], label: str, errors: list[str]) -> None:
    for token in tokens:
        if token not in block:
            errors.append(f"{label}: missing required trust-boundary token: {token}")


def _forbid(block: str, tokens: tuple[str, ...], label: str, errors: list[str]) -> None:
    for token in tokens:
        if token in block:
            errors.append(f"{label}: forbidden candidate-controlled token: {token}")


def _count(block: str, token: str, expected: int, label: str, errors: list[str]) -> None:
    observed = block.count(token)
    if observed != expected:
        errors.append(f"{label}: expected {expected} occurrence(s) of {token}, observed {observed}")


def _ordered(block: str, tokens: tuple[str, ...], label: str, errors: list[str]) -> None:
    positions = [block.find(token) for token in tokens]
    if any(position < 0 for position in positions) or positions != sorted(positions):
        errors.append(f"{label}: required step ordering is not preserved: {tokens}")


def _global_pr_trigger(text: str, label: str, errors: list[str]) -> None:
    pull = text.split("  workflow_dispatch:", 1)[0]
    _require(pull, ("  pull_request:\n    branches: [main]",), label, errors)
    _forbid(pull, ("    paths:", "pull_request_target"), label, errors)
    _require(
        text,
        ("cancel-in-progress: ${{ github.event_name == 'pull_request' }}",),
        label,
        errors,
    )


def validate(root: Path = ROOT) -> list[str]:
    root = Path(root).resolve()
    errors: list[str] = []
    governance = _read(root, GOVERNANCE, errors)
    ib = _read(root, IB, errors)
    source = {path: _read(root, path, errors) for path in TRUSTED_FILES}
    _global_pr_trigger(governance, "governance workflow", errors)
    _global_pr_trigger(ib, "IB workflow", errors)

    gov_boot = _job(governance, "bootstrap-audit", str(GOVERNANCE), errors)
    _require(
        gov_boot,
        (
            "ref: ${{ github.event.pull_request.head.sha }}",
            "persist-credentials: false",
            "python3 scripts/verify_exact_git_index.py --root .",
            "python3 scripts/check_qualification_trust_boundary.py --self-test",
            "test_git_index_authority.py",
            "test_team_codeowners_activation.py",
        ),
        "governance bootstrap",
        errors,
    )
    _forbid(gov_boot, ("secrets.", "HEPTA_QUALIFICATION_MUTATIONS"), "governance bootstrap", errors)

    gov_live = _job(governance, "qualify", str(GOVERNANCE), errors)
    _require(
        gov_live,
        (
            "if: github.event_name == 'workflow_dispatch'",
            "environment: repository-governance",
            "ref: ${{ github.sha }}",
            "path: trusted",
            "python3 trusted/scripts/verify_exact_git_index.py --root trusted",
            "python3 trusted/scripts/verify_github_governance.py",
            "HEPTA_GOVERNANCE_TOKEN: ${{ secrets.HEPTA_GOVERNANCE_TOKEN }}",
            "test \"$DISPATCH_REF\" = 'refs/heads/main'",
            "test \"$ACKNOWLEDGE_NO_BYPASS\" = 'true'",
        ),
        "governance qualify",
        errors,
    )
    _forbid(
        gov_live,
        ("ref: ${{ inputs.expected_head_sha }}", "path: candidate", "candidate/scripts/", "pull_request_target"),
        "governance qualify",
        errors,
    )
    _count(gov_live, "uses: actions/checkout@", 1, "governance qualify", errors)
    if REF_INPUT_RE.search(gov_live):
        errors.append("governance qualify: an input SHA controls a checkout")

    ib_boot = _job(ib, "bootstrap-audit", str(IB), errors)
    _require(
        ib_boot,
        (
            "ref: ${{ github.event.pull_request.head.sha }}",
            "persist-credentials: false",
            "python3 scripts/verify_exact_git_index.py --root .",
            "python3 scripts/check_qualification_trust_boundary.py --self-test",
            "test_git_index_authority.py",
            "test_ib_workflow_interfaces.py",
        ),
        "IB bootstrap",
        errors,
    )
    _forbid(
        ib_boot,
        ("secrets.", "HEPTA_QUALIFICATION_MUTATIONS: '1'", "pull_request_target"),
        "IB bootstrap",
        errors,
    )

    build = _job(ib, "build-candidate", str(IB), errors)
    _require(
        build,
        (
            "if: github.event_name == 'workflow_dispatch'",
            "runs-on: [self-hosted, linux, x64, heptatrader-ib-builder]",
            "environment: repository-governance",
            "HEPTA_IB_BUILD_SDK_ROOT: ${{ vars.HEPTA_IB_BUILD_SDK_ROOT }}",
            "HEPTA_IB_BUILD_SDK_TREE_SHA256: ${{ vars.HEPTA_IB_BUILD_SDK_TREE_SHA256 }}",
            "HEPTA_IB_BUILD_QUOTA_ROOT: ${{ vars.HEPTA_IB_BUILD_QUOTA_ROOT }}",
            "ref: ${{ github.sha }}",
            "path: trusted",
            "python3 trusted/scripts/verify_exact_git_index.py --root trusted",
            "python3 trusted/scripts/check_qualification_trust_boundary.py --root trusted",
            "python3 trusted/scripts/verify_qualification_candidate.py",
            "+refs/pull/${PULL_NUMBER}/head:refs/remotes/origin/candidate",
            "test \"$(git -C candidate rev-parse HEAD)\" = \"$EXPECTED_HEAD_SHA\"",
            "python3 trusted/scripts/verify_exact_git_index.py --root candidate",
            "verify_ib_candidate_artifact.py hash-tree",
            "trusted/scripts/build_ib_candidate_artifact.sh \\\n            candidate \"$EXPECTED_HEAD_SHA\" \"$artifact\"",
            "uses: actions/upload-artifact@",
        ),
        "IB candidate build",
        errors,
    )
    _forbid(
        build,
        (
            "secrets.",
            "HEPTA_IB_ACCOUNT_ID",
            "HEPTA_IB_GATEWAY_HOST",
            "HEPTA_IB_GATEWAY_PORT",
            "heptatrader-ib-paper",
            "HEPTA_QUALIFICATION_MUTATIONS: '1'",
            "/work/trusted/scripts/build_ib_candidate_artifact.sh",
            "--candidate-root",
            "--ib-api-archive",
            "docker run",
            "pull_request_target",
        ),
        "IB candidate build",
        errors,
    )
    _count(build, "uses: actions/checkout@", 1, "IB candidate build", errors)
    if REF_INPUT_RE.search(build):
        errors.append("IB candidate build: an input SHA controls a trusted checkout")

    paper = _job(ib, "paper-qualification", str(IB), errors)
    _require(
        paper,
        (
            "if: github.event_name == 'workflow_dispatch'",
            "needs: build-candidate",
            "runs-on: [self-hosted, linux, x64, heptatrader-ib-paper]",
            "environment: ib-paper",
            "test \"$DISPATCH_REF\" = 'refs/heads/main'",
            "ref: ${{ github.sha }}",
            "path: trusted",
            "python3 trusted/scripts/verify_exact_git_index.py --root trusted",
            "Record pre-campaign exact admission",
            "uses: actions/download-artifact@",
            "python3 trusted/scripts/verify_ib_candidate_artifact.py verify",
            "--expected-candidate-sha \"$EXPECTED_HEAD_SHA\"",
            "--expected-builder-image \"$HEPTA_IB_BUILDER_IMAGE\"",
            "--trusted-root trusted",
            "Run controlled PAPER campaign through trusted external harness",
            "HEPTA_IB_PAPER_QUALIFIER: ${{ vars.HEPTA_IB_PAPER_QUALIFIER }}",
            "HEPTA_IB_PAPER_QUALIFIER_SHA256: ${{ env.HEPTA_IB_PAPER_QUALIFIER_SHA256 }}",
            "HEPTA_QUALIFICATION_MUTATIONS: '1'",
            "trusted/scripts/run_ib_paper_artifact_qualification.sh",
            "Revalidate source and artifact after Broker campaign",
            "Re-admit unchanged candidate after Broker campaign",
            "--compare-before",
            "Issue final receipt only after stable post-campaign admission",
            "--result \"$evidence/qualification-result.json\"",
            "--evidence-root \"$evidence\"",
            "--expected-git-sha \"$EXPECTED_HEAD_SHA\"",
            "--expected-binary \"$artifact_dir/hepta-ib-executiond\"",
            "--expected-harness \"$HEPTA_IB_PAPER_QUALIFIER\"",
            "--receipt \"$evidence/qualification-verification.json\"",
            "Upload immutable verified PAPER and admission evidence",
        ),
        "IB PAPER qualification",
        errors,
    )
    _ordered(
        paper,
        (
            "Record pre-campaign exact admission",
            "Verify and extract immutable candidate before Broker access",
            "Run controlled PAPER campaign through trusted external harness",
            "Revalidate source and artifact after Broker campaign",
            "Re-admit unchanged candidate after Broker campaign",
            "Issue final receipt only after stable post-campaign admission",
            "Upload immutable verified PAPER and admission evidence",
        ),
        "IB PAPER qualification",
        errors,
    )
    _forbid(
        paper,
        (
            "ref: ${{ inputs.expected_head_sha }}",
            "path: candidate",
            "candidate/scripts/",
            "HEPTA_IB_ACCOUNT_ID",
            "HEPTA_IB_GATEWAY_HOST",
            "HEPTA_IB_GATEWAY_PORT",
            "HEPTA_IB_QUALIFIER_COMMAND",
            "--account-id",
            "--gateway-host",
            "--gateway-port",
            "--observation",
            "--extract-to",
            "--manifest",
            "cmake ",
            "ctest ",
            "pull_request_target",
            "secrets.",
        ),
        "IB PAPER qualification",
        errors,
    )
    _count(paper, "uses: actions/checkout@", 1, "IB PAPER qualification", errors)
    _count(
        paper,
        "trusted/scripts/run_ib_paper_artifact_qualification.sh",
        1,
        "IB PAPER qualification",
        errors,
    )
    if REF_INPUT_RE.search(paper):
        errors.append("IB PAPER qualification: an input SHA controls a credential-domain checkout")

    builder = source.get(Path("scripts/build_ib_candidate_artifact.sh"), "")
    _require(
        builder,
        (
            "[[ $# -eq 3 ]]",
            "HEPTA_IB_BUILD_SDK_ROOT",
            "HEPTA_IB_BUILD_QUOTA_ROOT",
            "@sha256:",
            "--network none",
            "--read-only",
            "--cap-drop ALL",
            "--security-opt no-new-privileges",
            "--pids-limit",
            "builder-provenance",
            "TOOLCHAIN_SHA256",
            "RESOURCE_POLICY_SHA256",
            "BUILD_TESTING=OFF",
        ),
        "trusted candidate builder",
        errors,
    )
    _forbid(
        builder,
        ("--network host", "--privileged", "/var/run/docker.sock", "eval ", "GITHUB_TOKEN"),
        "trusted candidate builder",
        errors,
    )

    runner = source.get(Path("scripts/run_ib_paper_artifact_qualification.sh"), "")
    _require(
        runner,
        (
            "[[ $# -eq 3 ]]",
            "HEPTA_IB_PAPER_QUALIFIER",
            "HEPTA_IB_PAPER_QUALIFIER_SHA256",
            "qualification harness digest mismatch",
            "env -i",
            "--operation-allowlist",
            "--candidate-environment cleared",
            "--candidate-network-policy broker-proxy-only",
            "--credential-delivery harness-only",
        ),
        "trusted PAPER runner",
        errors,
    )

    artifact = source.get(Path("scripts/verify_ib_candidate_artifact.py"), "")
    _require(
        artifact,
        (
            'subparsers.add_parser("verify")',
            'verify_parser.add_argument("--archive"',
            'verify_parser.add_argument("--expected-candidate-sha"',
            'verify_parser.add_argument("--expected-builder-image"',
            'verify_parser.add_argument("--trusted-root"',
            'verify_parser.add_argument("--destination"',
        ),
        "candidate artifact verifier",
        errors,
    )

    receipt = source.get(Path("scripts/verify_ib_paper_qualification.py"), "")
    _require(
        receipt,
        (
            'parser.add_argument("--result"',
            'parser.add_argument("--evidence-root"',
            'parser.add_argument("--expected-git-sha"',
            'parser.add_argument("--expected-binary"',
            'parser.add_argument("--expected-harness"',
            'parser.add_argument("--receipt"',
        ),
        "PAPER receipt verifier",
        errors,
    )

    evidence = source.get(Path("scripts/github_qualification_evidence.py"), "")
    _require(
        evidence,
        (
            "get_paginated",
            "maximum_pages",
            "workflow_id",
            "run_attempt",
            "jobs_by_run",
            "non-empty successful execution step",
            "DETAILS_RE",
        ),
        "GitHub evidence helper",
        errors,
    )
    governance_source = "\n".join(
        source.get(path, "")
        for path in (
            Path("scripts/verify_github_governance.py"),
            Path("scripts/verify_github_governance_legacy.py"),
            Path("scripts/github_qualification_evidence.py"),
        )
    )
    _require(
        governance_source,
        ("git/matching-refs", "merge_group_commit", "validate_reviews", "collect_check_evidence"),
        "governance verifier trusted source set",
        errors,
    )
    admission = source.get(Path("scripts/verify_qualification_candidate.py"), "")
    _require(
        admission,
        ("validate_reviews", "collect_check_evidence", "admission_state_sha256", "compare_admission_receipts", "state changed"),
        "candidate admission verifier",
        errors,
    )
    return errors


def isolation_probe() -> list[str]:
    docker = shutil.which("docker")
    if docker is None:
        return ["hostile isolation probe requires Docker"]
    image = os.environ.get("HEPTA_HOSTILE_PROBE_IMAGE", "busybox:1.36.1")
    errors: list[str] = []
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        work, guard = root / "work", root / "guard"
        work.mkdir(mode=0o700)
        guard.mkdir(mode=0o700)
        outside = guard / "outside"
        pull = subprocess.run(
            [docker, "pull", "--quiet", image],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=30,
            check=False,
        )
        if pull.returncode:
            return [
                f"hostile isolation image pull failed: status={pull.returncode} "
                f"log_sha256={hashlib.sha256(pull.stdout).hexdigest()}"
            ]
        inspect = subprocess.run(
            [docker, "image", "inspect", "--format", "{{.Id}}", image],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
            check=False,
        )
        image_id = inspect.stdout.strip()
        if inspect.returncode or re.fullmatch(r"sha256:[0-9a-f]{64}", image_id) is None:
            return ["hostile isolation probe could not resolve an immutable image ID"]
        command = r'''set -eu
[ -z "${HEPTA_TEST_SECRET:-}" ]
[ -z "${GITHUB_ENV:-}" ]
if (printf pwned > /host/outside) 2>/dev/null; then exit 91; fi
if nc -w 1 127.0.0.1 4002 </dev/null >/dev/null 2>&1; then exit 92; fi
printf '::error::hostile-workflow-command\n'
printf '%s\n' '${{ secrets.HEPTA_GOVERNANCE_TOKEN }}' > /work/inert
'''
        environment = dict(os.environ)
        environment.update(HEPTA_TEST_SECRET="must-not-cross-boundary", GITHUB_ENV=str(outside))
        run = subprocess.run(
            [
                docker, "run", "--rm", "--network", "none", "--read-only",
                "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                "--memory", "128m", "--memory-swap", "128m", "--cpus", "0.5",
                "--pids-limit", "32", "--ulimit", "nofile=128:128",
                "--tmpfs", "/tmp:rw,nosuid,nodev,noexec,size=16777216",
                "--user", f"{os.getuid()}:{os.getgid()}",
                "--mount", f"type=bind,src={work},dst=/work",
                "--mount", f"type=bind,src={guard},dst=/host,readonly",
                image_id, "/bin/sh", "-ceu", command,
            ],
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=20,
            check=False,
        )
        if run.returncode:
            errors.append(f"hostile container probe failed with status {run.returncode}")
        if outside.exists():
            errors.append("hostile candidate modified a host read-only guard")
        if b"::error::hostile-workflow-command" not in run.stdout:
            errors.append("hostile workflow-command fixture did not execute inside capture")
        if b"must-not-cross-boundary" in run.stdout:
            errors.append("parent secret crossed the cleared container environment")
        inert = work / "inert"
        if not inert.is_file() or inert.read_text(encoding="utf-8") != "${{ secrets.HEPTA_GOVERNANCE_TOKEN }}\n":
            errors.append("hostile output was not retained as inert bytes")
        print(
            "[QUALIFICATION-TRUST-BOUNDARY] "
            f"hostile-image-id={image_id} hostile-output-sha256={hashlib.sha256(run.stdout).hexdigest()}"
        )
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    errors = validate(args.root)
    if args.self_test:
        errors.extend(isolation_probe())
    for error in errors:
        print(f"[QUALIFICATION-TRUST-BOUNDARY] {error}", file=sys.stderr)
    if errors:
        return 1
    print("[QUALIFICATION-TRUST-BOUNDARY] PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
