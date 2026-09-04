#!/usr/bin/env python3
"""Fail-closed validation of qualification code/secret trust boundaries."""
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
    Path("scripts/verify_github_governance.py"),
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
    end = min(later) if later else len(text)
    return text[start:end]


def _require(block: str, token: str, label: str, errors: list[str]) -> None:
    if token not in block:
        errors.append(f"{label}: missing required trust-boundary token: {token}")


def _forbid(block: str, token: str, label: str, errors: list[str]) -> None:
    if token in block:
        errors.append(f"{label}: forbidden candidate-controlled token: {token}")


def validate(root: Path = ROOT) -> list[str]:
    root = Path(root).resolve()
    errors: list[str] = []
    governance = _read(root, GOVERNANCE, errors)
    ib = _read(root, IB, errors)
    for relative in TRUSTED_FILES:
        _read(root, relative, errors)

    governance_qualify = _job(
        governance, "qualify", GOVERNANCE.as_posix(), errors
    )
    for token in (
        "if: github.event_name == 'workflow_dispatch'",
        "environment: repository-governance",
        "ref: ${{ github.sha }}",
        "path: trusted",
        "python3 trusted/scripts/verify_github_governance.py",
        "HEPTA_GOVERNANCE_TOKEN: ${{ secrets.HEPTA_GOVERNANCE_TOKEN }}",
        "test \"$DISPATCH_REF\" = 'refs/heads/main'",
    ):
        _require(governance_qualify, token, "governance qualify", errors)
    for token in (
        "ref: ${{ inputs.expected_head_sha }}",
        "path: candidate",
        "cd candidate",
        "candidate/scripts/",
        "python3 scripts/verify_github_governance.py",
    ):
        _forbid(governance_qualify, token, "governance qualify", errors)
    if governance_qualify.count("uses: actions/checkout@") != 1:
        errors.append("governance qualify: exactly one trusted-main checkout is required")
    if governance_qualify.count("HEPTA_GOVERNANCE_TOKEN") != 2:
        errors.append("governance qualify: governance token must exist only in one step env binding")

    ib_build = _job(ib, "build-candidate", IB.as_posix(), errors)
    for token in (
        "if: github.event_name == 'workflow_dispatch'",
        "heptatrader-ib-builder",
        "ref: ${{ github.sha }}",
        "path: trusted",
        "ref: ${{ inputs.candidate_sha }}",
        "path: candidate",
        "python3 trusted/scripts/verify_qualification_candidate.py",
        "GITHUB_TOKEN: ${{ github.token }}",
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
        "python3 trusted/scripts/verify_qualification_candidate.py",
        "GITHUB_TOKEN: ${{ github.token }}",
        "trusted/scripts/verify_ib_candidate_artifact.py",
        "trusted/scripts/run_ib_paper_artifact_qualification.sh",
        "HEPTA_QUALIFICATION_MUTATIONS: '1'",
    ):
        _require(ib_qualify, token, "IB qualify", errors)
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

    if REF_INPUT_RE.search(governance_qualify):
        errors.append("governance qualify: an input SHA controls a checkout")
    if REF_INPUT_RE.search(ib_qualify):
        errors.append("IB qualify: an input SHA controls a credential-domain checkout")

    build_script = _read(root, Path("scripts/build_ib_candidate_artifact.sh"), errors)
    for token in (
        "--unshare-all",
        "--unshare-net",
        "--clearenv",
        "--cap-drop ALL",
        "--ro-bind \"$SOURCE_ROOT\" /src",
        "--ro-bind \"$SDK_ROOT\" /sdk",
        "candidate-build.log",
        ">>\"$BUILD_LOG\" 2>&1",
        "BUILD_TESTING=OFF",
    ):
        _require(build_script, token, "trusted candidate builder", errors)
    for token in (
        "--ro-bind / /",
        "eval ",
        "source \"$CANDIDATE",
        "bash \"$CANDIDATE",
        "HEPTA_IB_PAPER_QUALIFIER",
        "HEPTA_QUALIFICATION_MUTATIONS",
        "GITHUB_TOKEN",
    ):
        _forbid(build_script, token, "trusted candidate builder", errors)

    runner_script = _read(
        root, Path("scripts/run_ib_paper_artifact_qualification.sh"), errors
    )
    for token in (
        "HEPTA_IB_PAPER_QUALIFIER_SHA256",
        "qualification harness digest mismatch",
        "Only the independently pinned external harness",
        "verify_ib_paper_qualification.py",
    ):
        _require(runner_script, token, "trusted PAPER runner", errors)
    for token in (
        "--repository-root",
        "--build-dir",
        "cmake ",
        "ctest ",
        "candidate/scripts/",
    ):
        _forbid(runner_script, token, "trusted PAPER runner", errors)
    return errors


def isolation_probe() -> list[str]:
    errors: list[str] = []
    executable = shutil.which("bwrap")
    if executable is None:
        return ["hostile isolation probe requires bubblewrap"]
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        work = root / "work"
        work.mkdir()
        secret = root / "host-secret"
        secret.write_text("not-visible-to-candidate", encoding="utf-8")
        outside = root / "outside-sentinel"
        log = root / "captured.log"
        command = """
set -euo pipefail
[[ -z "${HEPTA_TEST_SECRET:-}" ]]
[[ -z "${GITHUB_ENV:-}" ]]
[[ ! -e /host-secret ]]
if printf pwned > /host/outside-sentinel 2>/dev/null; then exit 91; fi
if timeout 1 bash -c '</dev/tcp/127.0.0.1/4002' 2>/dev/null; then exit 92; fi
printf '::error::hostile-workflow-command\n'
printf '%s\n' '${{ secrets.HEPTA_GOVERNANCE_TOKEN }}' > /work/inert-candidate-output
"""
        arguments = [
            executable,
            "--unshare-all",
            "--unshare-net",
            "--die-with-parent",
            "--new-session",
            "--cap-drop",
            "ALL",
            "--clearenv",
            "--dev",
            "/dev",
            "--proc",
            "/proc",
            "--tmpfs",
            "/tmp",
            "--bind",
            str(work),
            "/work",
            "--setenv",
            "PATH",
            "/usr/bin:/bin",
        ]
        for path in ("/usr", "/bin", "/lib", "/lib64"):
            if Path(path).exists() or Path(path).is_symlink():
                arguments.extend(("--ro-bind", path, path))
        arguments.extend(("/bin/bash", "-c", command))
        environment = dict(os.environ)
        environment["HEPTA_TEST_SECRET"] = "must-not-cross-boundary"
        environment["GITHUB_ENV"] = str(outside)
        with log.open("wb") as stream:
            completed = subprocess.run(
                arguments,
                env=environment,
                stdout=stream,
                stderr=subprocess.STDOUT,
                timeout=15,
                check=False,
            )
        if completed.returncode != 0:
            errors.append(f"hostile sandbox probe failed closed with unexpected status {completed.returncode}")
        if outside.exists():
            errors.append("hostile candidate modified a host workflow-command file")
        output = log.read_bytes()
        if b"::error::hostile-workflow-command" not in output:
            errors.append("hostile workflow-command fixture did not execute inside capture")
        if b"must-not-cross-boundary" in output:
            errors.append("parent secret crossed the cleared sandbox environment")
        if not (work / "inert-candidate-output").is_file():
            errors.append("hostile output was not confined to the disposable output mount")
        # Print only a digest, never untrusted captured bytes.
        print(
            "[QUALIFICATION-TRUST-BOUNDARY] hostile-output-sha256="
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
