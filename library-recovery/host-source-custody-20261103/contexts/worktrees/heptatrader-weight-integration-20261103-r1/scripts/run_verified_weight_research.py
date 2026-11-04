#!/usr/bin/env python3
"""Start one research role from a fully hashed immutable Python release."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import runpy
import sys


def verify_release(manifest_path, expected_sha):
    manifest_path = Path(manifest_path)
    raw = manifest_path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_sha:
        raise ValueError("WEIGHT_RELEASE_MANIFEST_CHANGED")
    manifest = json.loads(raw)
    if manifest.get("schema") != "heptatrader.weight-research-release.v1":
        raise ValueError("WEIGHT_RELEASE_SCHEMA")
    root = manifest_path.parent.resolve()
    files = manifest["files"]
    if not isinstance(files, dict) or not 1 <= len(files) <= 5000:
        raise ValueError("WEIGHT_RELEASE_FILE_BOUND")
    for name, expected in files.items():
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("WEIGHT_RELEASE_PATH")
        path = root / relative
        if path.is_symlink() or path.resolve() != root / relative:
            raise ValueError("WEIGHT_RELEASE_SYMLINK")
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError("WEIGHT_RELEASE_FILE_CHANGED:" + name)
    actual_python = {str(p.relative_to(root)) for p in root.rglob("*.py")}
    if actual_python != {name for name in files if name.endswith(".py")}:
        raise ValueError("WEIGHT_RELEASE_PYTHON_INVENTORY")
    for name, expected in manifest.get("external_files", {}).items():
        path = Path(name)
        if not path.is_absolute() or path.is_symlink():
            raise ValueError("WEIGHT_EXTERNAL_PATH")
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError("WEIGHT_EXTERNAL_SOURCE_CHANGED")
    return root, manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--target", required=True, choices=("predictor", "evaluator", "supervisor"))
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    os.umask(0o077)
    root, manifest = verify_release(args.manifest, args.sha256)
    sys.dont_write_bytecode = True
    sys.path[:0] = [str(root / "research/neuron_learning"), str(root / "research"), str(root / "scripts")]
    arguments = args.arguments[1:] if args.arguments[:1] == ["--"] else args.arguments
    sys.argv = [args.target, *arguments]
    if args.target == "predictor":
        runpy.run_path(str(root / "scripts/run_current100k_preopen_research.py"), run_name="__main__")
    elif args.target == "supervisor":
        runpy.run_path(str(root / "scripts/supervise_current100k_weight_shadow.py"), run_name="__main__")
    else:
        runpy.run_module("laya_cell.current100k_weight_shadow_eval", run_name="__main__", alter_sys=True)


if __name__ == "__main__":
    main()
