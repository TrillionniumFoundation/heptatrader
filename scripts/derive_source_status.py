#!/usr/bin/env python3
"""Derive source-gap status from validators executed at the exact Git HEAD.

`docs/gap-register.json` is a policy/projection source, not self-authenticating
proof.  This command runs the repository-controlled predicates, binds their
results to one clean commit and writes a machine-generated receipt.  External
GitHub, runner and broker gaps are copied as open requirements; this command
cannot close them.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "heptatrader.source-status.v1"
CHECKS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("documentation-identity", (sys.executable, "scripts/check_documentation.py")),
    ("documentation-depth", (sys.executable, "scripts/check_module_documentation_depth.py")),
    ("build-ownership", (sys.executable, "scripts/verify_build_ownership.py")),
    ("gap-policy", (sys.executable, "scripts/check_gap_register.py")),
    ("ib-paper-profile", (sys.executable, "scripts/verify_canonical_ib_paper_profile.py")),
    ("source-gap-closures", (sys.executable, "scripts/verify_source_gap_closures.py")),
)


class SourceStatusError(RuntimeError):
    pass


def _run(root: Path, command: Sequence[str], timeout: int = 300) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            list(command),
            cwd=root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise SourceStatusError(f"cannot execute {' '.join(command)}: {error}") from error


def _git(root: Path, *arguments: str) -> str:
    result = _run(root, ("git", *arguments), timeout=60)
    if result.returncode:
        raise SourceStatusError(
            f"git {' '.join(arguments)} failed ({result.returncode}): {result.stdout[-2000:]}"
        )
    return result.stdout.strip()


def _load_json(path: Path) -> Any:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise SourceStatusError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    try:
        return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise SourceStatusError(f"cannot load {path}: {error}") from error


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _digest(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _gap_projection(register: Any, source_correct: bool) -> list[dict[str, Any]]:
    if not isinstance(register, dict) or not isinstance(register.get("gaps"), list):
        raise SourceStatusError("gap register has no gaps array")
    projection: list[dict[str, Any]] = []
    for item in register["gaps"]:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            raise SourceStatusError("gap register contains an invalid entry")
        domain = item.get("domain")
        if domain == "REPOSITORY":
            state = "VERIFIED_CLOSED" if source_correct else "OPEN_SOURCE"
        elif domain == "EXTERNAL":
            state = "OPEN_EXTERNAL"
        else:
            raise SourceStatusError(f"{item['id']}: invalid domain {domain!r}")
        projection.append({
            "id": item["id"],
            "domain": domain,
            "derived_state": state,
            "blocking_authorization": bool(
                item.get("blocking_authorization") or state != "VERIFIED_CLOSED"
            ),
        })
    return projection


def derive(root: Path | str = ROOT) -> tuple[dict[str, Any], bool]:
    root = Path(root).resolve()
    source_sha = _git(root, "rev-parse", "HEAD")
    if len(source_sha) != 40 or any(character not in "0123456789abcdef" for character in source_sha):
        raise SourceStatusError(f"invalid Git HEAD: {source_sha!r}")
    before = _git(root, "status", "--porcelain=v1", "--untracked-files=all")
    if before:
        raise SourceStatusError("source-status derivation requires a clean worktree")

    check_results: list[dict[str, Any]] = []
    all_passed = True
    for check_id, command in CHECKS:
        completed = _run(root, command)
        output = completed.stdout
        passed = completed.returncode == 0
        all_passed = all_passed and passed
        check_results.append({
            "id": check_id,
            "command": list(command),
            "passed": passed,
            "exit_code": completed.returncode,
            "output_sha256": _digest(output.encode("utf-8")),
            "output_tail": output[-4000:],
        })

    after_sha = _git(root, "rev-parse", "HEAD")
    after = _git(root, "status", "--porcelain=v1", "--untracked-files=all")
    if after_sha != source_sha:
        raise SourceStatusError("Git HEAD changed while deriving source status")
    if after:
        raise SourceStatusError("a source validator modified the worktree")

    register = _load_json(root / "docs/gap-register.json")
    receipt: dict[str, Any] = {
        "schema": SCHEMA,
        "source_sha": source_sha,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_correct": all_passed,
        "checks": check_results,
        "gaps": _gap_projection(register, all_passed),
        "authorization": {
            "paper_authorized": False,
            "live_authorized": False,
            "external_receipt_required": True,
        },
    }
    receipt["receipt_sha256"] = _digest(_canonical_json(receipt))
    return receipt, all_passed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        receipt, passed = derive(args.root)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    except SourceStatusError as error:
        print(f"[SOURCE-STATUS] {error}", file=sys.stderr)
        return 1
    print(
        f"[SOURCE-STATUS] {'PASS' if passed else 'FAIL'} "
        f"sha={receipt['source_sha']} receipt={receipt['receipt_sha256']}"
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
