#!/usr/bin/env python3
"""Fail-closed validation for the source/external HeptaTrader gap register."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import stat
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REGISTER_KEYS = {"schema", "authorization", "gaps"}
AUTHORIZATION_KEYS = {"source_state", "paper_authorized", "live_authorized"}
GAP_KEYS = {
    "id",
    "domain",
    "state",
    "blocking_authorization",
    "summary",
    "evidence",
    "issue",
}
REQUIRED_REPOSITORY_GAPS = {
    "DOC-001",
    "CI-001",
    "TEST-001",
    "RISK-001",
    "PENDING-EXPOSURE-001",
    "VENUE-001",
    "OMS-001",
    "BUILD-001",
}
REQUIRED_EXTERNAL_GAPS = {
    "G-TEAM-001": "https://github.com/TrillionniumFoundation/heptatrader/issues/8",
    "G-IB-001": "https://github.com/TrillionniumFoundation/heptatrader/issues/9",
}
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
        metadata = path.lstat()
        if path.is_symlink() or not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise GapRegisterError(f"{path}: expected a regular single-link file")
        return json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise GapRegisterError(f"cannot load {path}: {error}") from error


def canonical_path(root: Path, value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value or "\\" in value:
        raise GapRegisterError(f"{label}: invalid repository-relative path")
    relative = Path(value)
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise GapRegisterError(f"{label}: non-canonical path: {value!r}")
    path = root / relative
    try:
        metadata = path.lstat()
    except OSError as error:
        raise GapRegisterError(f"{label}: missing evidence {value}: {error}") from error
    if path.is_symlink() or not (stat.S_ISREG(metadata.st_mode) or stat.S_ISDIR(metadata.st_mode)):
        raise GapRegisterError(f"{label}: evidence is not a regular file/directory: {value}")
    return relative


def validate(root: Path | str = ROOT) -> list[str]:
    root = Path(root).resolve()
    errors: list[str] = []
    try:
        register = load_json(root / "docs/gap-register.json")
        if not isinstance(register, dict) or set(register) != REGISTER_KEYS:
            raise GapRegisterError("gap register fields are not canonical")
        if register.get("schema") != "heptatrader.gap-register.v1":
            raise GapRegisterError("unsupported gap register schema")
        authorization = register.get("authorization")
        if not isinstance(authorization, dict) or set(authorization) != AUTHORIZATION_KEYS:
            raise GapRegisterError("authorization fields are not canonical")
        if authorization.get("source_state") != "CANDIDATE":
            raise GapRegisterError("source_state must remain CANDIDATE before protected admission")
        if authorization.get("paper_authorized") is not False:
            raise GapRegisterError("PAPER cannot be source-authorized")
        if authorization.get("live_authorized") is not False:
            raise GapRegisterError("LIVE must remain unauthorized")

        gaps = register.get("gaps")
        if not isinstance(gaps, list) or not gaps:
            raise GapRegisterError("gaps must be a non-empty array")
        observed: dict[str, dict[str, Any]] = {}
        for index, gap in enumerate(gaps):
            label = f"gap[{index}]"
            if not isinstance(gap, dict) or set(gap) != GAP_KEYS:
                raise GapRegisterError(f"{label}: fields are not canonical")
            gap_id = gap.get("id")
            if not isinstance(gap_id, str) or ID_RE.fullmatch(gap_id) is None:
                raise GapRegisterError(f"{label}: invalid gap id")
            if gap_id in observed:
                raise GapRegisterError(f"{label}: duplicate gap id {gap_id}")
            observed[gap_id] = gap
            if not isinstance(gap.get("summary"), str) or not gap["summary"].strip():
                raise GapRegisterError(f"{gap_id}: non-empty summary required")
            if not isinstance(gap.get("blocking_authorization"), bool):
                raise GapRegisterError(f"{gap_id}: blocking_authorization must be boolean")
            evidence = gap.get("evidence")
            if not isinstance(evidence, list) or not evidence:
                raise GapRegisterError(f"{gap_id}: evidence must be a non-empty array")
            if len(evidence) != len(set(evidence)):
                raise GapRegisterError(f"{gap_id}: duplicate evidence path")
            for evidence_index, value in enumerate(evidence):
                canonical_path(root, value, f"{gap_id}.evidence[{evidence_index}]")

            domain = gap.get("domain")
            state = gap.get("state")
            issue = gap.get("issue")
            if domain == "REPOSITORY":
                if state != "CLOSED_SOURCE":
                    raise GapRegisterError(
                        f"{gap_id}: repository-controlled gap must be closed in this candidate"
                    )
                if issue is not None:
                    raise GapRegisterError(f"{gap_id}: closed source gap must not delegate closure")
                if gap["blocking_authorization"]:
                    raise GapRegisterError(
                        f"{gap_id}: closed source gap cannot remain an authorization blocker"
                    )
            elif domain == "EXTERNAL":
                if state != "OPEN_EXTERNAL":
                    raise GapRegisterError(
                        f"{gap_id}: source cannot mark an external control closed"
                    )
                expected_issue = REQUIRED_EXTERNAL_GAPS.get(gap_id)
                if expected_issue is None or issue != expected_issue:
                    raise GapRegisterError(f"{gap_id}: external issue binding is invalid")
                if not gap["blocking_authorization"]:
                    raise GapRegisterError(f"{gap_id}: external gap must block authorization")
            else:
                raise GapRegisterError(f"{gap_id}: invalid domain {domain!r}")

        missing_repository = sorted(REQUIRED_REPOSITORY_GAPS - set(observed))
        if missing_repository:
            raise GapRegisterError(
                "missing repository gaps: " + ", ".join(missing_repository)
            )
        missing_external = sorted(set(REQUIRED_EXTERNAL_GAPS) - set(observed))
        if missing_external:
            raise GapRegisterError(
                "missing external gaps: " + ", ".join(missing_external)
            )

        capabilities = load_json(root / "docs/capabilities.json")
        if not isinstance(capabilities, dict):
            raise GapRegisterError("capability matrix is invalid")
        if capabilities.get("live_trading_authorized") is not False:
            raise GapRegisterError("capability matrix conflicts with external blockers")
        by_id = {
            item.get("id"): item
            for item in capabilities.get("capabilities", [])
            if isinstance(item, dict)
        }
        if by_id.get("ib-paper", {}).get("status") != "QUALIFICATION_REQUIRED":
            raise GapRegisterError("IB PAPER must remain qualification-required")
        if by_id.get("live", {}).get("status") != "UNAVAILABLE":
            raise GapRegisterError("LIVE must remain unavailable")

        catalog = load_json(root / "docs/module-catalog.json")
        modules = {
            item.get("id"): item
            for item in catalog.get("modules", [])
            if isinstance(item, dict)
        } if isinstance(catalog, dict) else {}
        for module_id in ("ib-paper", "governance-qualification"):
            if modules.get(module_id, {}).get("production_authorized") is not False:
                raise GapRegisterError(
                    f"{module_id}: external blockers require production_authorized=false"
                )
    except GapRegisterError as error:
        errors.append(str(error))
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    errors = validate(args.root)
    for error in errors:
        print(f"[GAPS] {error}", file=sys.stderr)
    if errors:
        return 1
    print("[GAPS] PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
