#!/usr/bin/env python3
"""Supervise one immutable PAPER attempt; never issue trading authorization.

Failed evidence stays private on the host. HOME and process output are never
included in evidence uploads. Only the separately invoked verifier may publish
an allowlisted, verified evidence bundle.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from typing import Mapping

# The shell entry point uses -I; import only this trusted source directory.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from verify_ib_paper_qualification import (  # noqa: E402
    FULL_SHA, SHA256, MAX_RESULT_BYTES, REQUIRED_SCENARIOS, QualificationError,
    atomic_private_json, parse_json, stable_regular_bytes, verify_tool,
)

TIMEOUT_SECONDS = 900
TERMINATION_GRACE_SECONDS = 30
ISOLATION = {
    "network": "none", "environment": "cleared",
    "rootfs": "read-only-digest-pinned-oci",
    "source_mount": "read-only-git-archive",
    "sdk_mount": "read-only-stable-snapshot",
    "writable_filesystem": "dedicated-size-bounded-mount",
    "resource_control": "oci-cgroup-memory-cpu-pids-plus-tmpfs-limits",
    "candidate_output": "captured-not-replayed",
}


def _milliseconds() -> int:
    return time.time_ns() // 1_000_000


def _private_directory(path: Path) -> tuple[int, int]:
    metadata = path.lstat()
    if (not stat.S_ISDIR(metadata.st_mode) or path.is_symlink()
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) != 0o700):
        raise QualificationError("attempt directory must be a private owned directory")
    return metadata.st_dev, metadata.st_ino


def _terminate(process: subprocess.Popen, grace: float) -> None:
    # The child has its own process group; never signal the controller's group.
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        pass
    # A leader may exit before descendants. Kill the original group as well.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()


def run_campaign(artifact: Path, source_sha: str, attempt: Path, *,
                 environ: Mapping[str, str] | None = None,
                 timeout_seconds: float = TIMEOUT_SECONDS,
                 termination_grace: float = TERMINATION_GRACE_SECONDS) -> int:
    """Run a bounded child. Test callers may shorten, never enlarge, bounds.

    There is intentionally no command-line/environment timeout override.
    A controller crash leaves RESERVED/RUNNING, not a success receipt.
    """
    if not (0 < timeout_seconds <= TIMEOUT_SECONDS
            and 0 <= termination_grace <= TERMINATION_GRACE_SECONDS):
        raise QualificationError("invalid campaign supervision bound")
    env = os.environ if environ is None else environ
    if FULL_SHA.fullmatch(source_sha) is None:
        raise QualificationError("expected candidate SHA must be canonical")
    if env.get("HEPTA_QUALIFICATION_MUTATIONS") != "1":
        raise QualificationError("explicit HEPTA_QUALIFICATION_MUTATIONS=1 is required")
    broker_host = env.get("HEPTA_IB_PAPER_BROKER_HOST", "")
    broker_port = env.get("HEPTA_IB_PAPER_BROKER_PORT", "")
    if (broker_host, broker_port) != ("127.0.0.1", "4002"):
        raise QualificationError("desktop PAPER requires the explicit 127.0.0.1:4002 endpoint")
    harness_digest = env.get("HEPTA_IB_PAPER_QUALIFIER_SHA256", "")
    if SHA256.fullmatch(harness_digest) is None:
        raise QualificationError("the external harness must be digest-pinned")
    harness_text = env.get("HEPTA_IB_PAPER_QUALIFIER", "")
    if not harness_text:
        raise QualificationError("the external harness is required")
    harness = Path(os.path.abspath(harness_text))
    artifact = Path(os.path.abspath(artifact))
    if artifact.is_symlink() or not artifact.is_dir():
        raise QualificationError("artifact must be a non-symlink directory")
    binary = artifact / "hepta-ib-executiond"
    _, actual_harness_digest = verify_tool(harness, "external harness")
    _, binary_digest = verify_tool(binary, "candidate binary")
    if actual_harness_digest != harness_digest:
        raise QualificationError("external harness digest mismatch")
    raw, _ = stable_regular_bytes(
        artifact / "manifest.json", label="candidate manifest", maximum=MAX_RESULT_BYTES,
        allowed_owners=frozenset({0, os.geteuid()}))
    manifest = parse_json(raw, "candidate manifest")
    if (manifest.get("schema") != "heptatrader.ib-candidate-artifact.v2"
            or manifest.get("candidate_sha") != source_sha
            or not isinstance(manifest.get("binary"), dict)
            or manifest["binary"].get("name") != binary.name
            or manifest["binary"].get("sha256") != binary_digest
            or manifest.get("isolation") != ISOLATION):
        raise QualificationError("candidate manifest identity or isolation mismatch")

    attempt = Path(os.path.abspath(attempt))
    # The trusted work root must already exist. Creating a chain of parents
    # here would require persisting each ancestor before claiming reservation.
    if attempt.parent.is_symlink() or not attempt.parent.is_dir():
        raise QualificationError("attempt parent must be a pre-existing directory")
    # Atomic reservation precedes any child process. Existing attempts, including
    # failed/interrupted ones, are never reused or silently removed.
    try:
        attempt.mkdir(mode=0o700)
    except FileExistsError:
        return 73
    identity = _private_directory(attempt)
    parent_fd = os.open(attempt.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        # Persist the newly reserved directory entry, not just attempt.json
        # inside it. Failure leaves a non-reusable reservation and no child.
        os.fsync(parent_fd)
    finally:
        os.close(parent_fd)
    record = {
        "schema": "hepta.ib-paper-attempt.v1", "state": "RESERVED",
        "source_sha": source_sha, "binary_sha256": binary_digest,
        "harness_sha256": harness_digest, "started_at_ms": _milliseconds(),
        "timeout_seconds": timeout_seconds, "returncode": None,
        "paper_authorized": False, "live_authorized": False,
    }
    atomic_private_json(attempt / "attempt.json", record)
    evidence = attempt / "evidence"
    evidence.mkdir(mode=0o700)
    private_home: Path | None = None
    private_identity: tuple[int, int] | None = None
    child: subprocess.Popen | None = None
    interrupted = [0]
    handlers: dict[int, object] = {}
    code = 70

    def remember_signal(signum: int, _frame: object) -> None:
        interrupted[0] = signum

    try:
        private_home = Path(tempfile.mkdtemp(prefix=".hepta-paper-private-", dir=attempt.parent))
        private_identity = _private_directory(private_home)
        for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            handlers[signum] = signal.signal(signum, remember_signal)
        scenarios = ",".join(REQUIRED_SCENARIOS)
        result = evidence / "qualification-result.json"
        child_env = {
            "PATH": "/usr/bin:/bin", "LC_ALL": "C", "HOME": str(private_home),
            "HEPTA_QUALIFICATION_EXPECTED_GIT_SHA": source_sha,
            "HEPTA_QUALIFICATION_EXPECTED_BINARY": str(binary),
            "HEPTA_QUALIFICATION_EXPECTED_BINARY_SHA256": binary_digest,
            "HEPTA_QUALIFICATION_EXPECTED_HARNESS_SHA256": harness_digest,
            "HEPTA_QUALIFICATION_REQUIRED_SCENARIOS": scenarios,
            "HEPTA_QUALIFICATION_RESULT_PATH": str(result),
            "HEPTA_QUALIFICATION_MUTATIONS": "1",
            "HEPTA_QUALIFICATION_EXPECTED_BROKER_HOST": broker_host,
            "HEPTA_QUALIFICATION_EXPECTED_BROKER_PORT": broker_port,
        }
        command = [str(harness), "--execution-binary", str(binary),
                   "--expected-binary-sha256", binary_digest,
                   "--expected-git-sha", source_sha,
                   "--required-scenarios", scenarios,
                   "--operation-allowlist", scenarios,
                   "--candidate-environment", "cleared",
                   "--candidate-network-policy", "broker-proxy-only",
                   "--credential-delivery", "harness-only",
                   "--broker-host", broker_host, "--broker-port", broker_port,
                   "--evidence-dir", str(evidence), "--result", str(result),
                   "--mode", "bounded-mutations"]
        record["state"] = "RUNNING"
        atomic_private_json(attempt / "attempt.json", record)
        if interrupted[0]:
            code = 128 + interrupted[0]
            record["state"] = "INTERRUPTED"
        else:
            # Never replay untrusted/possibly secret-bearing output into Actions
            # logs. The harness owns bounded, sanitized machine evidence.
            child = subprocess.Popen(command, env=child_env, cwd=private_home,
                                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL, start_new_session=True)
            deadline = time.monotonic() + timeout_seconds
            while child.poll() is None and not interrupted[0] and time.monotonic() < deadline:
                try:
                    child.wait(timeout=min(0.1, max(0.001, deadline - time.monotonic())))
                except subprocess.TimeoutExpired:
                    pass
            if interrupted[0]:
                _terminate(child, termination_grace)
                code, record["state"] = 128 + interrupted[0], "INTERRUPTED"
            elif child.poll() is None:
                _terminate(child, termination_grace)
                code, record["state"] = 124, "TIMEOUT"
            elif child.returncode:
                code = child.returncode if child.returncode > 0 else 128 - child.returncode
                record["state"] = "HARNESS_FAILED"
            else:
                # Existence/parsing is not qualification. The final verifier
                # independently checks all broker evidence and exact bindings.
                result_bytes, _ = stable_regular_bytes(
                    result, label="harness result", maximum=MAX_RESULT_BYTES,
                    allowed_owners=frozenset({os.geteuid()}))
                parse_json(result_bytes, "harness result")
                code, record["state"] = 0, "HARNESS_SUCCEEDED_AWAITING_VERIFICATION"
    except (OSError, ValueError, QualificationError):
        record["state"] = "CONTROLLER_FAILED"
        code = 70
        if child is not None and child.poll() is None:
            _terminate(child, termination_grace)
    finally:
        if child is not None:
            _terminate(child, termination_grace)
        # HOME is outside the retained attempt and every upload path.
        if private_home is not None:
            try:
                if _private_directory(private_home) != private_identity:
                    raise QualificationError("private directory identity changed")
                shutil.rmtree(private_home)
            except (OSError, QualificationError):
                record["private_cleanup_failed"] = True
                code = code or 70
        if interrupted[0]:
            code, record["state"] = 128 + interrupted[0], "INTERRUPTED"
        record.update(returncode=code, completed_at_ms=_milliseconds())
        if interrupted[0]:
            record["signal"] = interrupted[0]
        try:
            if _private_directory(attempt) != identity:
                raise QualificationError("attempt directory identity changed")
            atomic_private_json(attempt / "attempt.json", record)
        finally:
            for signum, previous in handlers.items():
                signal.signal(signum, previous)
    return code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=Path)
    parser.add_argument("source_sha")
    parser.add_argument("attempt", type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    try:
        code = run_campaign(args.artifact, args.source_sha, args.attempt)
    except (OSError, ValueError, QualificationError):
        # Fixed diagnostic avoids reflecting raw manifests, paths or secrets.
        print("PAPER campaign rejected before authorization; inspect private host evidence", file=sys.stderr)
        return 78
    print(f"PAPER attempt completed with exit={code}; no authorization issued")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
