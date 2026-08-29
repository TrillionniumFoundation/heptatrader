#!/usr/bin/env python3
"""Verify an exact versioned source baseline without trusting Git cleanliness."""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import importlib.util
import json
import pathlib
import re
import subprocess
import sys
from typing import Any

import build_heptatrader_delivery_closure as delivery_closure


MAX_BASELINE_BYTES = 16 * 1024 * 1024
# A baseline can be re-sealed while a release branch is being converged.  We
# permit a bounded, linear chain of commits that touches baseline manifests
# only; source/code commits must still be represented by a new baseline and
# are never smuggled through this exception.
MAX_BASELINE_SEAL_COMMITS = 64
VERSION = re.compile(
    r"^[0-9A-Za-z][0-9A-Za-z.+-]{0,126}-round([1-9][0-9]*)$")
GIT_HEAD = re.compile(r"^[0-9a-f]{40}$")


class SourceBaselineError(RuntimeError):
    """The baseline is malformed, untrusted, or does not match its source."""


def load_source_manifest(root: pathlib.Path) -> dict[str, Any]:
    path = root / "scripts" / "run_execution_gateway_soak.py"
    spec = importlib.util.spec_from_file_location("hepta_soak_verify", path)
    if spec is None or spec.loader is None:
        raise SourceBaselineError(
            "source manifest loader is unavailable")
    module = importlib.util.module_from_spec(spec)
    # ``exec_module`` does not register the temporary module automatically.
    # Register it first so decorators that resolve ``cls.__module__`` (notably
    # dataclasses, used by the canonical soak profile) can find its namespace.
    # Always remove the transient entry afterwards; the manifest loader must
    # not leave an import side effect in the caller's interpreter.
    previous = sys.modules.get(spec.name)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        if previous is None:
            sys.modules.pop(spec.name, None)
        else:
            sys.modules[spec.name] = previous
    source_manifest = module.source_manifest(root)
    files = []
    for entry in source_manifest["files"]:
        normalized = dict(entry)
        normalized["mode"] = (
            "0755" if int(entry["mode"], 8) & 0o100 else "0644")
        files.append(normalized)
    canonical = json.dumps(
        files, ensure_ascii=True, separators=(",", ":"),
        sort_keys=True).encode()
    return {
        "file_count": len(files),
        "sha256": "sha256:" + hashlib.sha256(canonical).hexdigest(),
        "files": files,
    }


def _round_from_version(expected_version: str) -> int:
    if not isinstance(expected_version, str):
        raise SourceBaselineError("expected version must be a string")
    match = VERSION.fullmatch(expected_version)
    if match is None:
        raise SourceBaselineError(
            "expected version must be a safe token ending in a positive round")
    return int(match.group(1))


def _require_timestamp(value: Any) -> None:
    if not isinstance(value, str) or not value or not value.isascii():
        raise SourceBaselineError(
            "baseline generated_at must be an ASCII RFC3339 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise SourceBaselineError(
            "baseline generated_at must be an RFC3339 timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SourceBaselineError(
            "baseline generated_at must include a timezone")


def validate_baseline_document(
    baseline: Any,
    expected_version: str,
) -> dict[str, Any]:
    """Validate all static baseline fields and the expected release identity."""
    round_number = _round_from_version(expected_version)
    try:
        validated = delivery_closure._validate_baseline(
            baseline,
            round_number=round_number,
            release_version=expected_version,
        )
    except delivery_closure.DeliveryClosureError as error:
        raise SourceBaselineError(str(error)) from error
    _require_timestamp(baseline["generated_at"])
    return validated


def _strict_document(path: pathlib.Path, label: str) -> dict[str, Any]:
    try:
        captured = delivery_closure.stable_read(
            path, limit=MAX_BASELINE_BYTES, capture=True)
        document = delivery_closure.strict_json(
            captured.data or b"", label)
    except delivery_closure.DeliveryClosureError as error:
        raise SourceBaselineError(str(error)) from error
    if not isinstance(document, dict):
        raise SourceBaselineError(f"{label} must be a JSON object")
    return document


def _bundle_git_head(
    root: pathlib.Path,
    expected_version: str,
) -> str | None:
    strict_marker = root / ".hepta" / "source-bundle-manifest.json"
    agent_marker = root / ".hepta" / "agent-os-source-manifest.json"
    markers = [
        path for path in (strict_marker, agent_marker) if path.exists()]
    if len(markers) > 1:
        raise SourceBaselineError("source bundle identity is ambiguous")
    if not markers:
        return None
    marker = markers[0]
    document = _strict_document(marker, "source bundle manifest")
    if marker == strict_marker:
        head = document.get("git_head")
        valid = (
            document.get("schema") == "hepta.clean-source-bundle.v2" and
            document.get("version") == expected_version)
    else:
        parent = document.get("parent_strict_source")
        head = parent.get("git_head") if isinstance(parent, dict) else None
        valid = (
            document.get("schema") == "hepta.agent-os-source-bundle.v1" and
            document.get("release_version") == expected_version and
            isinstance(parent, dict) and
            parent.get("schema") == "hepta.clean-source-bundle.v2")
    if not valid or not isinstance(head, str) or GIT_HEAD.fullmatch(head) is None:
        raise SourceBaselineError(
            "source bundle manifest has no trusted Git identity")
    return head


def _git_output(root: pathlib.Path, *arguments: str) -> str:
    try:
        result = subprocess.run(
            ["git", *arguments],
            cwd=root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise SourceBaselineError("Git identity verification failed") from error
    return result.stdout.strip()


def _is_baseline_manifest_path(path: str) -> bool:
    """Return whether a Git path is a release/source baseline manifest.

    Only canonical manifest locations are admitted.  In particular, a
    generic file under ``release-manifests`` (or a bundle marker under
    ``.hepta``) is not a seal and cannot extend the identity exception.
    """
    if path == "source-baseline.json":
        return True
    if not path.startswith("release-manifests/") or not path.endswith(
            "/manifest.json"):
        return False
    components = path.split("/")
    # Require at least release-manifests/<version>/manifest.json and reject
    # aliases/traversal components before comparing the path.
    return (len(components) == 3 and
            all(component not in {"", ".", ".."} for component in components))


def _commit_changed_paths(root: pathlib.Path, commit: str) -> list[str]:
    """Read one commit's changed paths without following a worktree path."""
    output = _git_output(
        root, "diff-tree", "--no-commit-id", "--name-only", "--diff-filter=ACMR",
        "-r", commit)
    return [line for line in output.splitlines() if line]


def _verify_baseline_seal_chain(
    root: pathlib.Path,
    expected_head: str,
    current_head: str,
    allowed_paths: frozenset[str],
) -> None:
    """Accept a bounded chain that edits only this release's manifests.

    A release may have both ``source-baseline.json`` and a versioned manifest;
    sealing either one can therefore leave the other as a metadata-only commit
    in the ancestry.  The allow-list is computed by the caller for the
    specific release, rather than accepting every file that merely resembles a
    manifest.  This prevents an unrelated release manifest from extending the
    Git identity exception.
    """
    if (GIT_HEAD.fullmatch(expected_head) is None or
            GIT_HEAD.fullmatch(current_head) is None):
        raise SourceBaselineError(
            "source baseline Git identity drift: invalid seal-chain identity")
    if (not allowed_paths or
            any(not _is_baseline_manifest_path(path)
                for path in allowed_paths)):
        raise SourceBaselineError(
            "source baseline Git identity drift: invalid seal-chain paths")
    cursor = current_head
    traversed = 0
    while cursor != expected_head:
        traversed += 1
        if traversed > MAX_BASELINE_SEAL_COMMITS:
            raise SourceBaselineError(
                "source baseline Git identity drift: baseline seal chain is too long")
        parents = _git_output(root, "show", "-s", "--format=%P", cursor).split()
        if len(parents) != 1 or GIT_HEAD.fullmatch(parents[0]) is None:
            raise SourceBaselineError(
                "source baseline Git identity drift: seal chain must be linear")
        changed = _commit_changed_paths(root, cursor)
        if not changed or any(path not in allowed_paths for path in changed):
            raise SourceBaselineError(
                "source baseline Git identity drift: non-baseline commit in seal chain")
        cursor = parents[0]


def _verify_git_identity(
    root: pathlib.Path,
    baseline_path: pathlib.Path,
    expected_head: str,
    expected_version: str,
) -> None:
    bundle_head = _bundle_git_head(root, expected_version)
    if bundle_head is not None:
        if bundle_head != expected_head:
            raise SourceBaselineError("source baseline Git identity drift")
        return

    current_head = _git_output(root, "rev-parse", "--verify", "HEAD")
    if GIT_HEAD.fullmatch(current_head) is None:
        raise SourceBaselineError("Git returned an invalid HEAD identity")
    try:
        relative = baseline_path.resolve(strict=True).relative_to(root)
    except ValueError as error:
        raise SourceBaselineError(
            "source baseline must be inside the repository") from error
    relative_text = relative.as_posix()
    if _git_output(
            root, "status", "--porcelain=v1", "--untracked-files=all",
            "--", relative_text):
        raise SourceBaselineError("source baseline Git identity drift")
    if current_head == expected_head:
        return
    # A baseline refresh itself is a tiny metadata-only commit. During
    # convergence there may be several such refreshes in a row, so walk the
    # exact parent chain and admit only baseline-manifest paths. This keeps
    # the useful one-commit behavior while preventing a source/code commit
    # from being hidden behind a later baseline update.
    if not _is_baseline_manifest_path(relative_text):
        raise SourceBaselineError(
            "source baseline Git identity drift: baseline path is not canonical")
    allowed_paths = {relative_text, "source-baseline.json"}
    # The versioned manifest is the other half of the normal Round-N seal
    # pair.  Add only the exact path for this release, never an arbitrary
    # ``release-manifests/*/manifest.json`` sibling.
    if VERSION.fullmatch(expected_version):
        allowed_paths.add(
            f"release-manifests/heptatrader-agent-os-v{expected_version}/manifest.json")
    _verify_baseline_seal_chain(
        root, expected_head, current_head, frozenset(allowed_paths))


def verify(
    root: pathlib.Path,
    baseline_path: pathlib.Path,
    expected_version: str,
) -> dict[str, Any]:
    root = root.resolve(strict=True)
    baseline_path = (
        baseline_path if baseline_path.is_absolute()
        else root / baseline_path)
    baseline = _strict_document(baseline_path, "source baseline")
    validated = validate_baseline_document(baseline, expected_version)
    _verify_git_identity(
        root, baseline_path, baseline["git_head"], expected_version)
    current = load_source_manifest(root)
    if baseline["source_manifest"] != current:
        raise SourceBaselineError("source manifest drift")
    return {
        "version": expected_version,
        "git_head": baseline["git_head"],
        "source_manifest": current,
        "clean_checkout_certified":
            validated["clean_checkout_certified"],
        "release_authorized": validated["release_authorized"],
        "blocked_reason": validated["blocked_reason"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root", type=pathlib.Path,
        default=pathlib.Path(__file__).resolve().parents[1])
    parser.add_argument("--baseline", type=pathlib.Path, required=True)
    parser.add_argument("--expected-version", required=True)
    args = parser.parse_args()
    try:
        report = verify(args.root, args.baseline, args.expected_version)
    except (SourceBaselineError, OSError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    source_manifest = report["source_manifest"]
    print(
        f"PASS: {report['version']} {source_manifest['sha256']} "
        f"({source_manifest['file_count']} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
