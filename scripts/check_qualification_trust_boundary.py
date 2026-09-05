#!/usr/bin/env python3
"""Fail-closed static and hostile validation of qualification boundaries."""
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


def _require(block: str, token: str, label: str, errors: list[str]) -> None:
    if token not in block:
        errors.append(f"{label}: missing required trust-boundary token: {token}")


def _forbid(block: str, token: str, label: str, errors: list[str]) -> None:
    if token in block:
        errors.append(f"{label}: forbidden candidate-controlled token: {token}")


def _ordered(block: str, tokens: tuple[str, ...], label: str, errors: list[str]) -> None:
    positions = [block.find(token) for token in tokens]
    if any(position < 0 for position in positions) or positions != sorted(positions):
        errors.append(f"{label}: required step ordering is not preserved: {tokens}")


def _global_pr_trigger(text: str, label: str, errors: list[str]) -> None:
    pull = text.split("  workflow_dispatch:", 1)[0]
    _require(pull, "  pull_request:\n    branches: [main]", label, errors)
    _forbid(pull, "    paths:", label, errors)
    _require(text, "cancel-in-progress: ${{ github.event_name == 'pull_request' }}", label, errors)


def validate(root: Path = ROOT) -> list[str]:
    root = Path(root).resolve()
    errors: list[str] = []
    governance = _read(root, GOVERNANCE, errors)
    ib = _read(root, IB, errors)
    contents = {relative: _read(root, relative, errors) for relative in TRUSTED_FILES}
    _global_pr_trigger(governance, "governance workflow", errors)
    _global_pr_trigger(ib, "IB workflow", errors)

    governance_bootstrap = _job(governance, "bootstrap-audit", GOVERNANCE.as_posix(), errors)
    for token in (
        "ref: ${{ github.event.pull_request.head.sha }}",
        "persist-credentials: false",
        "python3 scripts/verify_exact_git_index.py --root .",
        "python3 scripts/check_qualification_trust_boundary.py --self-test",
        "test_git_index_authority.py",
        "test_team_codeowners_activation.py",
    ):
        _require(governance_bootstrap, token, "governance bootstrap", errors)
    _forbid(governance_bootstrap, "secrets.", "governance bootstrap", errors)

    governance_qualify = _job(governance, "qualify", GOVERNANCE.as_posix(), errors)
    for token in (
        "if: github.event_name == 'workflow_dispatch'",
        "environment: repository-governance",
        "ref: ${{ github.sha }}",
        "path: trusted",
        "python3 trusted/scripts/verify_exact_git_index.py --root trusted",
        "python3 trusted/scripts/verify_github_governance.py",
        "HEPTA_GOVERNANCE_TOKEN: ${{ secrets.HEPTA_GOVERNANCE_TOKEN }}",
        "test \"$DISPATCH_REF\" = 'refs/heads/main'",
        "test \"$ACKNOWLEDGE_NO_BYPASS\" = 'true'",
    ):
        _require(governance_qualify, token, "governance qualify", errors)
    for token in ("ref: ${{ inputs.expected_head_sha }}", "path: candidate", "candidate/scripts/"):
        _forbid(governance_qualify, token, "governance qualify", errors)
    if governance_qualify.count("uses: actions/checkout@") != 1:
        errors.append("governance qualify: exactly one trusted-main checkout is required")
    if REF_INPUT_RE.search(governance_qualify):
        errors.append("governance qualify: an input SHA controls a checkout")

    ib_bootstrap = _job(ib, "bootstrap-audit", IB.as_posix(), errors)
    for token in (
        "ref: ${{ github.event.pull_request.head.sha }}",
        "persist-credentials: false",
        "python3 scripts/verify_exact_git_index.py --root .",
        "python3 scripts/check_qualification_trust_boundary.py --self-test",
        "test_git_index_authority.py",
    ):
        _require(ib_bootstrap, token, "IB bootstrap", errors)
    for token in ("secrets.", "HEPTA_QUALIFICATION_MUTATIONS: '1'"):
        _forbid(ib_bootstrap, token, "IB bootstrap", errors)

    ib_build = _job(ib, "build-candidate", IB.as_posix(), errors)
    for token in (
        "if: github.event_name == 'workflow_dispatch'",
        "heptatrader-ib-builder",
        "environment: repository-governance",
        "ref: ${{ github.sha }}",
        "path: trusted",
        "python3 trusted/scripts/verify_exact_git_index.py --root trusted",
        "python3 trusted/scripts/verify_qualification_candidate.py",
        "+refs/pull/${PULL_NUMBER}/head:refs/remotes/origin/candidate",
        "test \"$(git -C candidate rev-parse HEAD)\" = \"$EXPECTED_HEAD_SHA\"",
        "HEPTA_IB_SDK_ARCHIVE: ${{ vars.HEPTA_IB_SDK_ARCHIVE }}",
        "docker inspect --format='{{index .RepoDigests 0}}'",
        "docker run --rm --network none --read-only",
        "/work/trusted/scripts/build_ib_candidate_artifact.sh",
        "uses: actions/upload-artifact@",
    ):
        _require(ib_build, token, "IB candidate build", errors)
    for token in (
        "secrets.",
        "HEPTA_IB_ACCOUNT_ID",
        "HEPTA_IB_GATEWAY_HOST",
        "HEPTA_IB_GATEWAY_PORT",
        "heptatrader-ib-paper",
        "HEPTA_QUALIFICATION_MUTATIONS: '1'",
    ):
        _forbid(ib_build, token, "IB candidate build", errors)
    if REF_INPUT_RE.search(ib_build):
        errors.append("IB candidate build: an input SHA controls a trusted checkout")

    ib_qualify = _job(ib, "paper-qualification", IB.as_posix(), errors)
    for token in (
        "needs: build-candidate",
        "environment: ib-paper",
        "heptatrader-ib-paper",
        "ref: ${{ github.sha }}",
        "path: trusted",
        "python3 trusted/scripts/verify_exact_git_index.py --root trusted",
        "Record pre-campaign exact admission",
        "uses: actions/download-artifact@",
        "python3 trusted/scripts/verify_ib_candidate_artifact.py",
        "Run controlled PAPER campaign through trusted external harness",
        "HEPTA_IB_QUALIFIER_SHA256",
        "sha256sum \"$HEPTA_IB_QUALIFIER_COMMAND\"",
        "--paper-only",
        "HEPTA_QUALIFICATION_MUTATIONS: '1'",
        "Revalidate source and artifact after Broker campaign",
        "Re-admit unchanged candidate after Broker campaign",
        "--compare-before",
        "Issue final receipt only after stable post-campaign admission",
        "test -s \"$RUNNER_TEMP/campaign-admission-pair.json\"",
        "python3 trusted/scripts/verify_ib_paper_qualification.py",
        "uses: actions/upload-artifact@",
    ):
        _require(ib_qualify, token, "IB PAPER qualification", errors)
    _ordered(
        ib_qualify,
        (
            "Record pre-campaign exact admission",
            "Verify immutable candidate before Broker access",
            "Run controlled PAPER campaign through trusted external harness",
            "Revalidate source and artifact after Broker campaign",
            "Re-admit unchanged candidate after Broker campaign",
            "Issue final receipt only after stable post-campaign admission",
        ),
        "IB PAPER qualification",
        errors,
    )
    for token in (
        "ref: ${{ inputs.expected_head_sha }}",
        "path: candidate",
        "candidate/scripts/",
        "cmake ",
        "ctest ",
        "--repository-root",
        "--build-dir",
    ):
        _forbid(ib_qualify, token, "IB PAPER qualification", errors)
    if ib_qualify.count("uses: actions/checkout@") != 1:
        errors.append("IB PAPER qualification: exactly one trusted-main checkout is required")
    if REF_INPUT_RE.search(ib_qualify):
        errors.append("IB PAPER qualification: an input SHA controls a credential-domain checkout")

    builder = contents.get(Path("scripts/build_ib_candidate_artifact.sh"), "")
    for token in (
        "@sha256:", "--network none", "--read-only", "--cap-drop ALL",
        "--security-opt no-new-privileges", "--pids-limit", "builder-provenance",
        "TOOLCHAIN_SHA256", "RESOURCE_POLICY_SHA256", "BUILD_TESTING=OFF",
    ):
        _require(builder, token, "trusted candidate builder", errors)
    for token in ("--network host", "--privileged", "/var/run/docker.sock", "eval ", "GITHUB_TOKEN"):
        _forbid(builder, token, "trusted candidate builder", errors)

    runner = contents.get(Path("scripts/run_ib_paper_artifact_qualification.sh"), "")
    for token in (
        "HEPTA_IB_PAPER_QUALIFIER_SHA256", "qualification harness digest mismatch",
        "env -i", "--operation-allowlist", "--candidate-environment cleared",
        "--candidate-network-policy broker-proxy-only", "--credential-delivery harness-only",
    ):
        _require(runner, token, "trusted PAPER runner", errors)

    evidence = contents.get(Path("scripts/github_qualification_evidence.py"), "")
    for token in ("get_paginated", "maximum_pages", "workflow_id", "run_attempt", "jobs_by_run", "non-empty successful execution step", "DETAILS_RE"):
        _require(evidence, token, "GitHub evidence helper", errors)

    governance_source = "\n".join(
        contents.get(relative, "")
        for relative in (
            Path("scripts/verify_github_governance.py"),
            Path("scripts/verify_github_governance_legacy.py"),
            Path("scripts/github_qualification_evidence.py"),
        )
    )
    for token in ("git/matching-refs", "merge_group_commit", "validate_reviews", "collect_check_evidence"):
        _require(governance_source, token, "governance verifier trusted source set", errors)

    admission = contents.get(Path("scripts/verify_qualification_candidate.py"), "")
    for token in ("validate_reviews", "collect_check_evidence", "admission_state_sha256", "compare_admission_receipts", "state changed"):
        _require(admission, token, "candidate admission verifier", errors)
    return errors


def _run_probe(arguments: list[str], environment: dict[str, str], log: Path) -> subprocess.CompletedProcess[bytes]:
    with log.open("wb") as stream:
        return subprocess.run(arguments, env=environment, stdout=stream, stderr=subprocess.STDOUT, timeout=20, check=False)


def isolation_probe() -> list[str]:
    errors: list[str] = []
    executable = shutil.which("docker")
    if executable is None:
        return ["hostile isolation probe requires Docker"]
    image = os.environ.get("HEPTA_HOSTILE_PROBE_IMAGE", "busybox:1.36.1")
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        work, guard = root / "work", root / "guard"
        work.mkdir(mode=0o700)
        guard.mkdir(mode=0o700)
        outside = guard / "outside"
        pull_log = root / "pull.log"
        pulled = _run_probe([executable, "pull", "--quiet", image], dict(os.environ), pull_log)
        if pulled.returncode:
            data = pull_log.read_bytes()
            return [f"hostile isolation image pull failed: status={pulled.returncode} log_sha256={hashlib.sha256(data).hexdigest()}"]
        inspected = subprocess.run([executable, "image", "inspect", "--format", "{{.Id}}", image], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30, check=False)
        image_id = inspected.stdout.strip()
        if inspected.returncode or re.fullmatch(r"sha256:[0-9a-f]{64}", image_id) is None:
            return ["hostile isolation probe could not resolve an immutable image ID"]
        command = r'''set -eu
[ -z "${HEPTA_TEST_SECRET:-}" ]
[ -z "${GITHUB_ENV:-}" ]
if (printf pwned > /host/outside) 2>/dev/null; then exit 91; fi
if nc -w 1 127.0.0.1 4002 </dev/null >/dev/null 2>&1; then exit 92; fi
printf '::error::hostile-workflow-command\n'
printf '%s\n' '${{ secrets.HEPTA_GOVERNANCE_TOKEN }}' > /work/inert
'''
        log = root / "captured.log"
        environment = dict(os.environ)
        environment.update(HEPTA_TEST_SECRET="must-not-cross-boundary", GITHUB_ENV=str(outside))
        args = [
            executable, "run", "--rm", "--network", "none", "--read-only",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--memory", "128m", "--memory-swap", "128m", "--cpus", "0.5",
            "--pids-limit", "32", "--ulimit", "nofile=128:128",
            "--tmpfs", "/tmp:rw,nosuid,nodev,noexec,size=16777216",
            "--user", f"{os.getuid()}:{os.getgid()}",
            "--mount", f"type=bind,src={work},dst=/work",
            "--mount", f"type=bind,src={guard},dst=/host,readonly",
            image_id, "/bin/sh", "-ceu", command,
        ]
        completed = _run_probe(args, environment, log)
        output = log.read_bytes()
        if completed.returncode:
            errors.append(f"hostile container probe failed with status {completed.returncode}")
        if outside.exists():
            errors.append("hostile candidate modified a host read-only guard")
        if b"::error::hostile-workflow-command" not in output:
            errors.append("hostile workflow-command fixture did not execute inside capture")
        if b"must-not-cross-boundary" in output:
            errors.append("parent secret crossed the cleared container environment")
        inert = work / "inert"
        if not inert.is_file() or inert.read_text(encoding="utf-8") != "${{ secrets.HEPTA_GOVERNANCE_TOKEN }}\n":
            errors.append("hostile output was not retained as inert bytes")
        print(f"[QUALIFICATION-TRUST-BOUNDARY] hostile-image-id={image_id} hostile-output-sha256={hashlib.sha256(output).hexdigest()}")
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
