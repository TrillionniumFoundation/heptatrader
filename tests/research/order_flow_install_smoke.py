#!/usr/bin/env python3
"""Actual CMake install/relocation and isolated order-flow command acceptance."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from order_flow_fixture import write_fixture


def exercise(prefix: Path, root: Path, bindir: str, datadir: str) -> None:
    """Also usable against an explicitly manually staged local SDK prefix."""
    manifest, sources = write_fixture(root, split=7)
    executable = prefix/bindir/"hepta-research-order-flow"
    output = root/"order-flow-report.json"
    argv = [sys.executable, "-I", "-B", str(executable), "--manifest", str(manifest),
            "--output", str(output)]
    for ref, path in sources.items():
        argv += ["--source", ref+"="+str(path)]
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    (root/"hepta_research.py").write_text('raise RuntimeError("working-directory import")\n')
    result = subprocess.run(argv, cwd=root, env=env, text=True, capture_output=True, timeout=30)
    if result.returncode:
        raise AssertionError("isolated order-flow command failed: "+result.stderr)
    report = json.loads(output.read_text())
    if (report["final"]["equity"] != "1011.2" or report["final"]["fees"] != "0.8"
            or len(report["fills"]) != 4 or report["active_order_count"] != 1
            or report["assumptions"]["broker_authorized"] is not False
            or report["assumptions"]["exchange_settlement"] is not False):
        raise AssertionError("installed matching/FIFO fixture disagrees")
    if [entry["event_count"] for entry in report["input"]["sources"]] != [7, 8]:
        raise AssertionError("source boundaries were not preserved")
    before = output.read_bytes()
    sources["flow1"].write_bytes(b"corrupt later source\n")
    result = subprocess.run(argv, cwd=root, env=env, text=True, capture_output=True, timeout=30)
    if result.returncode != 2 or output.read_bytes() != before:
        raise AssertionError("digest rejection did not preserve existing report")
    package = prefix/datadir/"heptatrader/research/python/hepta_research"
    for name in ("model.py", "pipeline.py", "portfolio.py", "fifo.py", "matching.py", "order_flow.py"):
        if not (package/name).is_file():
            raise AssertionError("missing installed module: "+name)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", type=Path, required=True)
    parser.add_argument("--config", default="Release")
    parser.add_argument("--bindir", default="bin")
    parser.add_argument("--datadir", default="share")
    args = parser.parse_args()
    for value in (args.bindir, args.datadir):
        if Path(value).is_absolute() or ".." in Path(value).parts:
            raise ValueError("relative SDK install directories required")
    with tempfile.TemporaryDirectory(prefix="hepta-order-flow-install-") as directory:
        root = Path(directory)
        stage = root/"stage"
        command = ["cmake", "--install", str(args.build_dir.resolve()), "--prefix", str(stage)]
        if args.config:
            command += ["--config", args.config]
        subprocess.run(command, check=True, timeout=90)
        moved = root/"relocated prefix"
        shutil.move(str(stage), str(moved))
        work = root/"unrelated working directory"
        work.mkdir()
        exercise(moved, work, args.bindir, args.datadir)
        print("CMake install + relocated isolated order-flow success/rejection: PASS")


if __name__ == "__main__":
    main()
