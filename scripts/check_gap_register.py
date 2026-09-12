#!/usr/bin/env python3
"""Validate an honest issue register; optionally evaluate one release profile.

A normal source check allows open/new/deferred issues. It validates declared
facts, not C++ spellings, issue closure, or broker authorization. Behavioral
regressions run in Core Runtime CI; package admission is a separate caller.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path, PurePosixPath
import re
import stat
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "heptatrader.gap-register.v2"
STATES = {"OPEN", "ACCEPTED", "DEFERRED", "CLOSED"}
DOMAINS = {"REPOSITORY", "EXTERNAL"}
RELEASE_PROFILES = {"core", "ib-paper"}
GAP_KEYS = {"id", "domain", "state", "summary", "evidence", "issue",
            "blocking_releases", "disposition"}
ID_RE = re.compile(r"^[A-Z][A-Z0-9-]{2,63}$")


class GapRegisterError(ValueError):
    pass


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise GapRegisterError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def reject_constant(value: str) -> None:
    raise GapRegisterError(f"non-finite JSON number: {value}")


def load_json(path: Path) -> Any:
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise GapRegisterError(f"{path}: expected a regular single-link file")
        return json.loads(path.read_text(encoding="utf-8"),
                          object_pairs_hook=unique_object, parse_constant=reject_constant)
    except (OSError, UnicodeError, ValueError, RecursionError) as error:
        raise GapRegisterError(f"cannot load {path}: {error}") from error


def canonical_evidence(root: Path, value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise GapRegisterError(f"{label}: invalid evidence path")
    relative = PurePosixPath(value)
    if (not relative.parts or relative.is_absolute() or
            relative.as_posix() != value or ".." in relative.parts):
        raise GapRegisterError(f"{label}: non-canonical evidence path: {value!r}")
    path = root
    try:
        for part in relative.parts:
            path = path / part
            if path.is_symlink():
                raise GapRegisterError(f"{label}: symlink evidence is not accepted: {value}")
        info = path.lstat()
        if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
            raise GapRegisterError(f"{label}: unsupported evidence type: {value}")
    except OSError as error:
        raise GapRegisterError(f"{label}: missing evidence {value}: {error}") from error
    return value


def release_blockers(register: dict[str, Any], profile: str) -> list[str]:
    if profile not in RELEASE_PROFILES:
        raise GapRegisterError(f"unsupported release profile: {profile}")
    # ACCEPTED/DEFERRED are dispositions, not implicit release waivers.
    return [gap["id"] for gap in register["gaps"]
            if gap["state"] != "CLOSED" and profile in gap["blocking_releases"]]


def validate(root: Path | str = ROOT, release_profile: str | None = None) -> list[str]:
    root = Path(root).resolve()
    try:
        register = load_json(root / "docs/gap-register.json")
        if not isinstance(register, dict) or set(register) != {"schema", "authorization", "gaps"}:
            raise GapRegisterError("gap register fields are not canonical")
        if register["schema"] != SCHEMA:
            raise GapRegisterError("unsupported gap register schema; migrate to v2 explicitly")
        authorization = register["authorization"]
        if (not isinstance(authorization, dict) or
                set(authorization) != {"paper_authorized", "live_authorized"} or
                any(value is not False for value in authorization.values())):
            raise GapRegisterError("source issue state cannot authorize PAPER or LIVE")
        gaps = register["gaps"]
        if not isinstance(gaps, list):
            raise GapRegisterError("gaps must be an array")
        ids: set[str] = set()
        for gap in gaps:
            if not isinstance(gap, dict) or set(gap) != GAP_KEYS:
                raise GapRegisterError("gap entry fields are not canonical")
            gap_id = gap["id"]
            if not isinstance(gap_id, str) or ID_RE.fullmatch(gap_id) is None:
                raise GapRegisterError("invalid gap id")
            if gap_id in ids:
                raise GapRegisterError(f"duplicate gap id: {gap_id}")
            ids.add(gap_id)
            if gap["domain"] not in DOMAINS or gap["state"] not in STATES:
                raise GapRegisterError(f"{gap_id}: unsupported domain or state")
            if not isinstance(gap["summary"], str) or not gap["summary"].strip():
                raise GapRegisterError(f"{gap_id}: non-empty summary required")
            disposition = gap["disposition"]
            if not isinstance(disposition, str) or (gap["state"] != "OPEN" and not disposition.strip()):
                raise GapRegisterError(f"{gap_id}: accepted/deferred/closed work needs a disposition")
            issue = gap["issue"]
            if issue is not None and (type(issue) is not int or issue < 1):
                raise GapRegisterError(f"{gap_id}: issue must be a positive repository issue number or null")
            profiles = gap["blocking_releases"]
            if (not isinstance(profiles, list) or any(profile not in RELEASE_PROFILES for profile in profiles)
                    or len(profiles) != len(set(profiles))):
                raise GapRegisterError(f"{gap_id}: invalid or duplicate blocking release profile")
            evidence = gap["evidence"]
            if not isinstance(evidence, list) or (gap["state"] == "CLOSED" and not evidence):
                raise GapRegisterError(f"{gap_id}: closed work requires evidence")
            paths = [canonical_evidence(root, item, gap_id) for item in evidence]
            if len(paths) != len(set(paths)):
                raise GapRegisterError(f"{gap_id}: duplicate evidence path")
        if release_profile is not None:
            blockers = release_blockers(register, release_profile)
            if blockers:
                raise GapRegisterError(f"{release_profile}: unresolved release blockers: {', '.join(blockers)}")
        return []
    except (GapRegisterError, KeyError, TypeError, ValueError) as error:
        return [str(error)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--release-profile", choices=sorted(RELEASE_PROFILES),
                        help="fail only on declared unresolved blockers for this profile")
    args = parser.parse_args(argv)
    errors = validate(args.root, args.release_profile)
    for error in errors:
        print(f"[GAPS] {error}", file=sys.stderr)
    if errors:
        return 1
    print("[GAPS] PASS register integrity; open work is allowed; no trading authority")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
