#!/usr/bin/env python3
"""Fail closed unless a checkout exactly matches the stage-zero Git tree at HEAD."""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from typing import Iterable

OID = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
REGULAR = {"100644", "100755"}
SYMLINK = "120000"
GITLINK = "160000"
CRITICAL_PATHS = (
    ".github/required-check-contexts-v1.json",
    ".github/workflows/ib-paper-qualification.yml",
    ".github/workflows/qualification-source-audit.yml",
    "docs/capabilities.json",
    "docs/gap-register.json",
    "docs/ib-paper-profile-policy-v1.json",
    "scripts/build_ib_candidate_artifact.sh",
    "scripts/check_qualification_trust_boundary.py",
    "scripts/run_ib_paper_artifact_qualification.sh",
    "scripts/verify_exact_git_index.py",
    "scripts/verify_ib_candidate_artifact.py",
    "scripts/verify_ib_paper_qualification.py",
    "tests/python/test_gap_register.py",
    "tests/python/test_git_index_authority.py",
    "tests/python/test_ib_paper_qualification.py",
    "tests/python/test_qualification_trust_boundary.py",
)


def _env() -> dict[str, str]:
    result = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    result.update(
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_NO_REPLACE_OBJECTS="1",
        GIT_OPTIONAL_LOCKS="0",
        GIT_TERMINAL_PROMPT="0",
    )
    return result


def _git(root: Path, args: list[str], errors: list[str], label: str) -> bytes | None:
    try:
        result = subprocess.run(
            [
                "git",
                "-c", "core.fsmonitor=false",
                "-c", "core.hooksPath=/dev/null",
                "-c", "core.untrackedCache=false",
                "-C", str(root), *args,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
            check=False,
            env=_env(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        errors.append(f"{label} failed: {exc}")
        return None
    if result.returncode:
        detail = result.stderr.decode("utf-8", "replace").strip()
        errors.append(f"{label} failed with {result.returncode}: {detail}")
        return None
    return result.stdout


def _records(data: bytes, label: str, errors: list[str]) -> list[str] | None:
    try:
        return [item for item in data.decode("utf-8").split("\0") if item]
    except UnicodeDecodeError as exc:
        errors.append(f"{label} contains a non-UTF-8 path: {exc}")
        return None


def _path(raw: str, label: str, errors: list[str]) -> str | None:
    parts = raw.split("/")
    if not raw or raw.startswith("/") or "\\" in raw or any(p in {"", ".", ".."} for p in parts):
        errors.append(f"{label} is not canonical: {raw!r}")
        return None
    return raw


def _index(data: bytes, errors: list[str]) -> dict[str, tuple[str, str]] | None:
    items = _records(data, "git index", errors)
    if items is None:
        return None
    output: dict[str, tuple[str, str]] = {}
    for item in items:
        try:
            meta, raw = item.split("\t", 1)
            mode, oid, stage = meta.split(" ")
        except ValueError:
            errors.append(f"malformed git index record: {item!r}")
            continue
        path = _path(raw, "git index path", errors)
        if path is None:
            continue
        if stage != "0":
            errors.append(f"unmerged git index entry is not permitted: {path} (stage {stage})")
        elif not OID.fullmatch(oid) or set(oid) == {"0"}:
            errors.append(f"invalid git index object id for {path}")
        elif mode == GITLINK:
            errors.append(f"tracked gitlink/submodule is not permitted: {path}")
        elif mode not in REGULAR | {SYMLINK}:
            errors.append(f"unsupported git index mode {mode} for {path}")
        elif path in output:
            errors.append(f"duplicate stage-zero git index path: {path}")
        else:
            output[path] = (mode, oid)
    if not output:
        errors.append("stage-zero git index must not be empty")
    return output


def _tree(data: bytes, errors: list[str]) -> dict[str, tuple[str, str]] | None:
    items = _records(data, "HEAD tree", errors)
    if items is None:
        return None
    output: dict[str, tuple[str, str]] = {}
    for item in items:
        try:
            meta, raw = item.split("\t", 1)
            mode, kind, oid = meta.split(" ")
        except ValueError:
            errors.append(f"malformed HEAD tree record: {item!r}")
            continue
        path = _path(raw, "HEAD tree path", errors)
        if path is None:
            continue
        if mode == GITLINK or kind == "commit":
            errors.append(f"tracked gitlink/submodule is not permitted: {path}")
        elif kind != "blob" or mode not in REGULAR | {SYMLINK}:
            errors.append(f"unsupported HEAD tree entry {mode} {kind} for {path}")
        elif not OID.fullmatch(oid) or set(oid) == {"0"}:
            errors.append(f"invalid HEAD tree object id for {path}")
        elif path in output:
            errors.append(f"duplicate HEAD tree path: {path}")
        else:
            output[path] = (mode, oid)
    if not output:
        errors.append("HEAD tree must not be empty")
    return output


def _blob_id(data: bytes, oid: str) -> str:
    digest = hashlib.sha1() if len(oid) == 40 else hashlib.sha256()  # noqa: S324
    digest.update(f"blob {len(data)}\0".encode("ascii"))
    digest.update(data)
    return digest.hexdigest()


def _worktree(root: Path, relative: str, mode: str, oid: str, critical: set[str], errors: list[str]) -> None:
    path = root / relative
    try:
        info = path.lstat()
    except OSError as exc:
        errors.append(f"tracked work-tree path is unavailable: {relative}: {exc}")
        return
    if mode in REGULAR:
        if path.is_symlink() or not stat.S_ISREG(info.st_mode):
            errors.append(f"tracked regular file was replaced by another type: {relative}")
            return
        if info.st_nlink != 1:
            errors.append(f"tracked regular file must have one hard link: {relative}")
            return
        if bool(info.st_mode & 0o111) != (mode == "100755"):
            errors.append(f"tracked executable mode differs from the index: {relative}")
        try:
            data = path.read_bytes()
        except OSError as exc:
            errors.append(f"tracked regular file is unreadable: {relative}: {exc}")
            return
    else:
        if relative in critical:
            errors.append(f"critical trust-boundary path must not be a symlink: {relative}")
            return
        if not stat.S_ISLNK(info.st_mode):
            errors.append(f"tracked symlink was replaced by another type: {relative}")
            return
        try:
            data = os.fsencode(os.readlink(path))
        except OSError as exc:
            errors.append(f"tracked symlink is unreadable: {relative}: {exc}")
            return
    actual = _blob_id(data, oid)
    if actual != oid:
        errors.append(f"tracked work-tree bytes differ from indexed blob: {relative} (expected {oid}, observed {actual})")


def validate(root: Path | str, *, critical_paths: Iterable[str] = CRITICAL_PATHS) -> list[str]:
    root = Path(root).resolve()
    errors: list[str] = []
    metadata = root / ".git"
    try:
        info = metadata.lstat()
    except OSError as exc:
        return [f"ordinary .git directory is required at validator root: {exc}"]
    if metadata.is_symlink() or not stat.S_ISDIR(info.st_mode):
        return ["ordinary non-symlink .git directory is required at validator root"]
    raw_top = _git(root, ["rev-parse", "--show-toplevel"], errors, "git rev-parse")
    if raw_top is None:
        return errors
    try:
        top = Path(raw_top.decode("utf-8").strip()).resolve()
    except (UnicodeDecodeError, OSError) as exc:
        return errors + [f"Git repository top level is invalid: {exc}"]
    if top != root:
        return errors + [f"validator root is not the Git repository top level: {top}"]
    commands = (
        (["ls-files", "--stage", "-z", "--"], "git index listing"),
        (["ls-tree", "-r", "-z", "--full-tree", "HEAD"], "HEAD tree listing"),
        (
            [
                "ls-files",
                "--others",
                "--directory",
                "--no-empty-directory",
                "--exclude-standard",
                "-z",
                "--",
            ],
            "untracked path listing",
        ),
        (
            [
                "ls-files",
                "--others",
                "--directory",
                "--no-empty-directory",
                "--ignored",
                "--exclude-standard",
                "-z",
                "--",
            ],
            "ignored path listing",
        ),
    )
    payloads = [_git(root, args, errors, label) for args, label in commands]
    if any(item is None for item in payloads):
        return errors
    index = _index(payloads[0] or b"", errors)
    tree = _tree(payloads[1] or b"", errors)
    if index is None or tree is None:
        return errors
    if index != tree:
        missing = sorted(set(tree) - set(index))
        added = sorted(set(index) - set(tree))
        changed = sorted(path for path in set(index) & set(tree) if index[path] != tree[path])
        if missing:
            errors.append("stage-zero index is missing HEAD paths: " + ", ".join(missing))
        if added:
            errors.append("stage-zero index has non-HEAD paths: " + ", ".join(added))
        if changed:
            errors.append("stage-zero index entries differ from HEAD: " + ", ".join(changed))
    untracked = _records(payloads[2] or b"", "untracked path listing", errors)
    if untracked:
        errors.append(
            "untracked work-tree content is not permitted: "
            + ", ".join(sorted(untracked))
        )
    ignored = _records(payloads[3] or b"", "ignored path listing", errors)
    if ignored:
        errors.append(
            "ignored work-tree content is not permitted: "
            + ", ".join(sorted(ignored))
        )
    critical = set(critical_paths)
    for relative in sorted(critical):
        if relative not in tree:
            errors.append(f"critical trust-boundary path is absent from HEAD: {relative}")
        elif tree[relative][0] not in REGULAR:
            errors.append(f"critical trust-boundary path is not a regular Git file: {relative}")
    for relative, (mode, oid) in sorted(tree.items()):
        _worktree(root, relative, mode, oid, critical, errors)
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    errors = validate(args.root)
    for error in errors:
        print(f"[EXACT-GIT-INDEX] {error}", file=sys.stderr)
    if errors:
        return 1
    print("[EXACT-GIT-INDEX] PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
