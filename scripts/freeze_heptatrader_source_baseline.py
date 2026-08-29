#!/usr/bin/env python3
"""Freeze a versioned, content-addressed Agent OS source baseline."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import pathlib
import subprocess
import sys
from datetime import datetime, timezone


def load_soak_module(root: pathlib.Path):
    path = root / "scripts" / "run_execution_gateway_soak.py"
    spec = importlib.util.spec_from_file_location("hepta_soak_manifest", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("source manifest loader is unavailable")
    module = importlib.util.module_from_spec(spec)
    # ``exec_module`` does not register a temporary module automatically.
    # Register it first so decorators that resolve ``cls.__module__`` (notably
    # dataclasses, used by the canonical soak profile) can find its namespace.
    # Remove the transient entry afterwards so loading a manifest has no
    # import side effects in the caller's interpreter.
    previous = sys.modules.get(spec.name)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        if previous is None:
            sys.modules.pop(spec.name, None)
        else:
            sys.modules[spec.name] = previous
    return module


def git(root: pathlib.Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, text=True, capture_output=True, check=True
    ).stdout.strip()


def portable_manifest(source_manifest: dict) -> dict:
    files = []
    for entry in source_manifest["files"]:
        normalized = dict(entry)
        normalized["mode"] = "0755" if int(entry["mode"], 8) & 0o100 else "0644"
        files.append(normalized)
    canonical = json.dumps(files, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode()
    return {"file_count": len(files), "sha256": "sha256:" + hashlib.sha256(canonical).hexdigest(), "files": files}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=pathlib.Path, default=pathlib.Path(__file__).resolve().parents[1])
    parser.add_argument("--version", default="")
    parser.add_argument("--output", type=pathlib.Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve(strict=True)
    version = args.version or (root / "VERSION").read_text(encoding="utf-8").strip()
    if not version or any(character.isspace() for character in version):
        raise SystemExit("VERSION must be a non-empty single token")

    source_manifest = portable_manifest(load_soak_module(root).source_manifest(root))
    status_lines = git(root, "status", "--porcelain=v1", "--untracked-files=all").splitlines()
    clean = not status_lines
    payload = {
        "schema": "hepta.versioned-source-baseline.v1",
        "version": version,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git_head": git(root, "rev-parse", "HEAD"),
        "source_manifest": source_manifest,
        "source_baseline_frozen": True,
        "clean_checkout_certified": clean,
        "release_authorized": False,
        "paper_authorized": False,
        "live_authorized": False,
        "worktree_status_entry_count": len(status_lines),
        "blocked_reason": None if clean else "VERSION_CONTROL_COMMIT_REQUIRED",
        "excluded_unsafe_tree": "compat/unsafe-direct-broker",
    }
    output = args.output
    if not output.is_absolute():
        output = root / output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    output.chmod(0o644)
    print(f"BASELINE={output}")
    print(f"SOURCE_SHA256={source_manifest['sha256']}")
    print(f"CLEAN_CHECKOUT_CERTIFIED={str(clean).lower()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
