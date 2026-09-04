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
        return path.read_text(encoding="utf-8-sig")
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


def validate(root: Path = ROOT) -> list[str]:
    root = Path(root).resolve()
    errors: list[str] = []
    governance = _read(root, GOVERNANCE, errors)
    ib = _read(root, IB, errors)
    contents = {relative: _read(root, relative, errors) for relative in TRUSTED_FILES}

    governance_qualify = _job(governance, "qualify", GOVERNANCE.as_posix(), errors)
    for token in (
        "if: github.event_name == 'workflow_dispatch'",
        "environment: repository-governance",
        "ref: ${{ github.sha }}",
        "path: trusted",
        "trusted/scripts/github_qualification_evidence.py",
        "python3 trusted/scripts/verify_github_governance.py",
        "HEPTA_GOVERNANCE_TOKEN: ${{ secrets.HEPTA_GOVERNANCE_TOKEN }}",
        "test \"$DISPATCH_REF\" = 'refs/heads/main'",
    ):
        _require(governance_qualify, token, "governance qualify", errors)
    for token in (
        "ref: ${{ inputs.expected_head_sha }}",
        "path: candidate",
        "candidate/scripts/",
        "python3 scripts/verify_github_governance.py",
    ):
        _forbid(governance_qualify, token, "governance qualify", errors)
    if governance_qualify.count("uses: actions/checkout@") != 1:
        errors.append("governance qualify: exactly one trusted-main checkout is required")
    if governance_qualify.count("HEPTA_GOVERNANCE_TOKEN") != 2:
        errors.append("governance qualify: governance token must exist only in one step env binding")
    if REF_INPUT_RE.search(governance_qualify):
        errors.append("governance qualify: an input SHA controls a checkout")

    ib_build = _job(ib, "build-candidate", IB.as_posix(), errors)
    for token in (
        "if: github.event_name == 'workflow_dispatch'",
        "heptatrader-ib-builder",
        "HEPTA_IB_BUILD_QUOTA_ROOT: ${{ vars.HEPTA_IB_BUILD_QUOTA_ROOT }}",
        "HEPTA_IB_BUILDER_IMAGE: ${{ vars.HEPTA_IB_BUILDER_IMAGE }}",
        "ref: ${{ github.sha }}",
        "path: trusted",
        "ref: ${{ inputs.candidate_sha }}",
        "path: candidate",
        "python3 trusted/scripts/verify_qualification_candidate.py",
        "trusted/scripts/build_ib_candidate_artifact.sh",
        "uses: actions/upload-artifact@",
    ):
        _require(ib_build, token, "IB candidate build", errors)
    for token in (
        "environment:",
        "secrets.",
        "HEPTA_QUALIFICATION_MUTATIONS",
        "heptatrader-ib-paper",
        "HEPTA_IB_PAPER_QUALIFIER",
    ):
        _forbid(ib_build, token, "IB candidate build", errors)

    ib_qualify = _job(ib, "qualify", IB.as_posix(), errors)
    for token in (
        "needs: build-candidate",
        "environment: ib-paper",
        "heptatrader-ib-paper",
        "ref: ${{ github.sha }}",
        "path: trusted",
        "uses: actions/download-artifact@",
        "Record pre-campaign exact admission",
        "Run controlled PAPER campaign through trusted external harness",
        "Re-admit unchanged candidate after Broker campaign",
        "--compare-before",
        "Issue final receipt only after stable post-campaign admission",
        "trusted/scripts/verify_ib_candidate_artifact.py verify",
        "trusted/scripts/run_ib_paper_artifact_qualification.sh",
        "HEPTA_QUALIFICATION_MUTATIONS: '1'",
    ):
        _require(ib_qualify, token, "IB qualify", errors)
    _ordered(
        ib_qualify,
        (
            "Record pre-campaign exact admission",
            "Run controlled PAPER campaign through trusted external harness",
            "Re-admit unchanged candidate after Broker campaign",
            "Issue final receipt only after stable post-campaign admission",
        ),
        "IB qualify",
        errors,
    )
    for token in (
        "ref: ${{ inputs.candidate_sha }}",
        "path: candidate",
        "candidate/scripts/",
        "cmake ",
        "ctest ",
        "run_ib_paper_qualification.sh",
        "--repository-root",
        "--build-dir",
    ):
        _forbid(ib_qualify, token, "IB qualify", errors)
    if ib_qualify.count("uses: actions/checkout@") != 1:
        errors.append("IB qualify: exactly one trusted-main checkout is required")
    if REF_INPUT_RE.search(ib_qualify):
        errors.append("IB qualify: an input SHA controls a credential-domain checkout")

    builder = contents.get(Path("scripts/build_ib_candidate_artifact.sh"), "")
    for token in (
        "@sha256:",
        "docker pull --quiet",
        "--network none",
        "--read-only",
        "--cap-drop ALL",
        "--security-opt no-new-privileges",
        "--memory \"$MEMORY_LIMIT\"",
        "--cpus \"$CPU_LIMIT\"",
        "--pids-limit \"$PIDS_LIMIT\"",
        "--tmpfs \"/tmp:",
        "mountpoint -q",
        "FILESYSTEM_BYTES",
        "snapshot-tree",
        "SDK_SOURCE_BEFORE",
        "SDK_SOURCE_AFTER",
        "SDK_SNAPSHOT_AFTER",
        "builder-provenance",
        "TOOLCHAIN_SHA256",
        "RESOURCE_POLICY_SHA256",
        ">>\"$BUILD_LOG\" 2>&1",
        "BUILD_TESTING=OFF",
    ):
        _require(builder, token, "trusted candidate builder", errors)
    for token in (
        "--network host",
        "--privileged",
        "/var/run/docker.sock",
        "eval ",
        "source \"$CANDIDATE",
        "bash \"$CANDIDATE",
        "HEPTA_IB_PAPER_QUALIFIER",
        "HEPTA_QUALIFICATION_MUTATIONS",
        "GITHUB_TOKEN",
    ):
        _forbid(builder, token, "trusted candidate builder", errors)

    runner = contents.get(Path("scripts/run_ib_paper_artifact_qualification.sh"), "")
    for token in (
        "HEPTA_IB_PAPER_QUALIFIER_SHA256",
        "qualification harness digest mismatch",
        "Only the independently pinned external harness",
        "env -i",
        "--operation-allowlist",
        "--candidate-environment cleared",
        "--candidate-network-policy broker-proxy-only",
        "--credential-delivery harness-only",
        "post-campaign admission",
    ):
        _require(runner, token, "trusted PAPER runner", errors)
    for token in (
        "verify_ib_paper_qualification.py",
        "qualification-verification.json",
        "--repository-root",
        "--build-dir",
        "cmake ",
        "ctest ",
        "candidate/scripts/",
    ):
        _forbid(runner, token, "trusted PAPER runner", errors)

    evidence = contents.get(Path("scripts/github_qualification_evidence.py"), "")
    for token in (
        "get_paginated",
        "maximum_pages",
        "workflow_id",
        "workflow_path",
        "run_attempt",
        "jobs_by_run",
        "non-empty successful execution step",
        "app.get(\"id\")",
        "DETAILS_RE",
    ):
        _require(evidence, token, "GitHub evidence helper", errors)

    governance_entry = contents.get(Path("scripts/verify_github_governance.py"), "")
    for token in (
        "LEGACY_BLOB_SHA",
        "verify_github_governance_legacy.py",
        "_git_blob_sha",
        "_legacy._main_ruleset = _main_ruleset",
        "ruleset_ref_condition_sha256",
        "RULESET_SCOPE_SELF_TEST_PASSED",
    ):
        _require(governance_entry, token, "governance verifier entry point", errors)

    # The public entry point intentionally stays small: it digest-binds the
    # previously reviewed implementation, replaces only the ruleset selector,
    # and delegates pagination/provenance collection to the shared helper. Scan
    # the complete, regular-file trusted source set rather than treating a thin
    # wrapper as if it must duplicate every inherited control string.
    governance_script = "\n".join(
        contents.get(relative, "")
        for relative in (
            Path("scripts/verify_github_governance.py"),
            Path("scripts/verify_github_governance_legacy.py"),
            Path("scripts/github_qualification_evidence.py"),
        )
    )
    for token in (
        "git/matching-refs",
        "merge_group_commit",
        "commit parents do not contain the exact admitted head and current base",
        "workflow-run PR identity",
        "validate_reviews",
        "collect_check_evidence",
    ):
        _require(governance_script, token, "governance verifier trusted source set", errors)

    admission = contents.get(Path("scripts/verify_qualification_candidate.py"), "")
    for token in (
        "latest exact-head decisive",
        "validate_reviews",
        "collect_check_evidence",
        "admission_state_sha256",
        "compare_admission_receipts",
        "review/check/PR admission state changed during protected campaign",
    ):
        _require(admission, token, "candidate admission verifier", errors)
    return errors


def _run_probe(arguments: list[str], environment: dict[str, str], log: Path) -> subprocess.CompletedProcess[bytes]:
    with log.open("wb") as stream:
        return subprocess.run(
            arguments,
            env=environment,
            stdout=stream,
            stderr=subprocess.STDOUT,
            timeout=20,
            check=False,
        )


def isolation_probe() -> list[str]:
    """Prove the same closed container boundary used by the production builder.

    GitHub-hosted Ubuntu deliberately restricts unprivileged user namespaces, so
    bubblewrap is not a portable audit primitive there. Docker is already the
    production isolation authority: this probe pulls a tiny image, resolves it
    to its immutable local image ID, then verifies no-network/read-only/cgroup/
    PID and workflow-command confinement without passing parent secrets.
    """
    errors: list[str] = []
    executable = shutil.which("docker")
    if executable is None:
        return ["hostile isolation probe requires Docker"]
    image_reference = os.environ.get(
        "HEPTA_HOSTILE_PROBE_IMAGE", "busybox:1.36.1"
    )
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        work = root / "work"
        guard = root / "host-guard"
        work.mkdir(mode=0o700)
        guard.mkdir(mode=0o700)
        secret = root / "host-secret"
        secret.write_text("must-not-cross-boundary", encoding="utf-8")
        secret.chmod(0o600)
        outside = guard / "outside-sentinel"
        pull_log = root / "docker-pull.log"
        with pull_log.open("wb") as stream:
            pulled = subprocess.run(
                [executable, "pull", "--quiet", image_reference],
                stdout=stream,
                stderr=subprocess.STDOUT,
                timeout=120,
                check=False,
            )
        if pulled.returncode != 0:
            output = pull_log.read_bytes()
            errors.append(
                "hostile isolation probe could not pull its audit image; "
                f"status={pulled.returncode} log_sha256={hashlib.sha256(output).hexdigest()}"
            )
            return errors
        inspected = subprocess.run(
            [
                executable,
                "image",
                "inspect",
                "--format",
                "{{.Id}}",
                image_reference,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
            check=False,
        )
        image_id = inspected.stdout.strip()
        if inspected.returncode != 0 or re.fullmatch(r"sha256:[0-9a-f]{64}", image_id) is None:
            errors.append("hostile isolation probe could not resolve an immutable image ID")
            return errors

        command = r"""
set -eu
[ -z "${HEPTA_TEST_SECRET:-}" ]
[ -z "${GITHUB_ENV:-}" ]
[ ! -e /host-secret ]
if (printf pwned > /host/outside-sentinel) 2>/dev/null; then exit 91; fi
if nc -w 1 127.0.0.1 4002 </dev/null >/dev/null 2>&1; then exit 92; fi
printf '::error::hostile-workflow-command\n'
printf '%s\n' '${{ secrets.HEPTA_GOVERNANCE_TOKEN }}' > /work/inert-candidate-output
"""
        run_log = root / "captured.log"
        environment = dict(os.environ)
        environment["HEPTA_TEST_SECRET"] = "must-not-cross-boundary"
        environment["GITHUB_ENV"] = str(outside)
        arguments = [
            executable,
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--memory",
            "128m",
            "--memory-swap",
            "128m",
            "--cpus",
            "0.5",
            "--pids-limit",
            "32",
            "--ulimit",
            "nofile=128:128",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,noexec,size=16777216",
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "--mount",
            f"type=bind,src={work},dst=/work",
            "--mount",
            f"type=bind,src={guard},dst=/host,readonly",
            image_id,
            "/bin/sh",
            "-ceu",
            command,
        ]
        completed = _run_probe(arguments, environment, run_log)
        output = run_log.read_bytes()
        if completed.returncode != 0:
            errors.append(f"hostile container probe failed with status {completed.returncode}")
        if outside.exists():
            errors.append("hostile candidate modified a host read-only guard")
        if b"::error::hostile-workflow-command" not in output:
            errors.append("hostile workflow-command fixture did not execute inside capture")
        if b"must-not-cross-boundary" in output:
            errors.append("parent secret crossed the cleared container environment")
        inert = work / "inert-candidate-output"
        if not inert.is_file():
            errors.append("hostile output was not confined to the disposable output mount")
        elif inert.read_text(encoding="utf-8") != "${{ secrets.HEPTA_GOVERNANCE_TOKEN }}\n":
            errors.append("hostile output was interpreted instead of retained as inert bytes")
        print(
            "[QUALIFICATION-TRUST-BOUNDARY] hostile-image-id="
            + image_id
            + " hostile-output-sha256="
            + hashlib.sha256(output).hexdigest()
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
