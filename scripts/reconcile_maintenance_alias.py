#!/usr/bin/env python3
"""Read-only determination of the pending Pocket4 maintenance CLI switch.

This reconciles effective symlink identity, not the result of a lost SSH/MCP
response. It never repeats the old mutation or calls an order/session endpoint.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess

SOURCE_COMMIT = "ad916725c953f0dcbe855f68588944d21d5feafc"
JOB_ID = "1a144b6e0f1-1123a8-1612"
OLD_RELEASE = "pnl-releases/523bbcf89cc67fa17ef1f0d305bd0dc4c2c418f3/heptatrader-user"
NEW_RELEASE = "operator-cli/releases/maintenance-latch-ad916725c953f0dc/heptatrader-user"
HELPER_RELEASE = "operator-cli/releases/maintenance-latch-ad916725c953f0dc/hepta_maintenance_latch.py"
RECEIPT = ".local/state/heptatrader/operator-evidence/maintenance-latch-20261016/cli-deploy.json"


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def expected_git_file(checkout: Path, relpath: str, commit=SOURCE_COMMIT) -> bytes:
    # A previously fetched, exact commit only; never downloads or mutates Git.
    return subprocess.check_output(
        ["git", "-C", str(checkout), "show", f"{commit}:{relpath}"], timeout=5
    )


def _regular(path: Path, uid: int, mode: int, expected: bytes) -> str | None:
    try:
        info = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != uid or
                info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != mode):
            return "FILE_IDENTITY_UNSAFE"
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            now = os.fstat(fd)
            if (now.st_dev, now.st_ino) != (info.st_dev, info.st_ino):
                return "FILE_CHANGED_DURING_READ"
            chunks = []
            while True:
                part = os.read(fd, 65536)
                if not part:
                    break
                chunks.append(part)
            if b"".join(chunks) != expected:
                return "FILE_BYTES_DO_NOT_MATCH_PINNED_COMMIT"
            return None
        finally:
            os.close(fd)
    except OSError:
        return "FILE_INACCESSIBLE"


def reconcile(home: Path, checkout: Path, *, uid: int, expected_wrapper: bytes,
              expected_helper: bytes) -> dict:
    """Return a truthful observation; never approve exposed-driver stops."""
    result = {
        "schema": "heptatrader.maintenance-alias-reconciliation.v1",
        "original_job_id": JOB_ID,
        "source_commit": SOURCE_COMMIT,
        "may_stop_exposed_driver": False,
        "broker_orders_requested": 0,
        "service_restarts_requested": 0,
        "prior_job_result_recovered": False,
    }
    alias = home / ".local/bin/heptatrader"
    old = home / ".local/share/heptatrader" / OLD_RELEASE
    new = home / ".local/share/heptatrader" / NEW_RELEASE
    helper = home / ".local/share/heptatrader" / HELPER_RELEASE
    receipt_path = home / RECEIPT
    try:
        meta = alias.lstat()
    except OSError:
        return {**result, "state": "ALIAS_MISSING_OR_INACCESSIBLE"}
    if not stat.S_ISLNK(meta.st_mode) or meta.st_uid != uid:
        return {**result, "state": "ALIAS_NOT_OWNER_SYMLINK"}
    try:
        actual = alias.resolve(strict=True)
    except (OSError, RuntimeError):
        return {**result, "state": "ALIAS_DANGLING_OR_CYCLIC"}
    result["effective_target"] = str(actual)
    if actual == old:
        return {**result, "state": "ORIGINAL_CLI_STILL_ACTIVE",
                "interpretation": "Do not retry original job; retrieve its journal first."}
    if actual != new:
        return {**result, "state": "UNEXPECTED_CLI_TARGET"}
    invalid = _regular(new, uid, 0o555, expected_wrapper)
    if invalid is None:
        invalid = _regular(helper, uid, 0o444, expected_helper)
    if invalid:
        return {**result, "state": "CANDIDATE_RELEASE_UNSAFE", "reason": invalid}
    if not receipt_path.exists():
        return {**result, "state": "NEW_CLI_EFFECTIVE_RECEIPT_MISSING",
                "interpretation": "Effective bytes match pinned source, but original job success is unproven."}
    try:
        record = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        return {**result, "state": "NEW_CLI_EFFECTIVE_RECEIPT_UNREADABLE"}
    expected_sha = {
        "heptatrader-user": sha256(expected_wrapper),
        "hepta_maintenance_latch.py": sha256(expected_helper),
    }
    if (record.get("source_commit") != SOURCE_COMMIT or
            record.get("new_pointer") != str(new) or
            record.get("new_source_sha256") != expected_sha or
            record.get("service_identities_unchanged") is not True or
            record.get("broker_orders_requested") != 0 or
            record.get("service_restarts_requested") != 0):
        return {**result, "state": "NEW_CLI_EFFECTIVE_RECEIPT_CONFLICT"}
    return {**result, "state": "NEW_CLI_EFFECTIVE_PRIOR_RECEIPT_MATCHES",
            "prior_job_result_recovered": False,
            "interpretation": "Source and receipt agree; check original job log to establish its final exit."}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkout", type=Path, default=Path.home() / "work/heptatrader-maintenance-latch-20261016")
    args = parser.parse_args()
    try:
        report = reconcile(
            Path.home(), args.checkout, uid=os.geteuid(),
            expected_wrapper=expected_git_file(args.checkout, "scripts/heptatrader-user"),
            expected_helper=expected_git_file(args.checkout, "scripts/hepta_maintenance_latch.py"),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        report = {"schema": "heptatrader.maintenance-alias-reconciliation.v1",
                  "state": "PINNED_SOURCE_UNAVAILABLE", "reason": type(exc).__name__,
                  "may_stop_exposed_driver": False, "broker_orders_requested": 0,
                  "service_restarts_requested": 0}
    print(json.dumps(report, sort_keys=True))
    return 0 if report["state"] == "NEW_CLI_EFFECTIVE_PRIOR_RECEIPT_MATCHES" else 2


if __name__ == "__main__":
    raise SystemExit(main())
