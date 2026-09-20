#!/usr/bin/env python3
"""Actual CMake install/relocation and isolated portfolio command acceptance."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from portfolio_fixture import check_report, fixture


def exercise(prefix: Path, root: Path, bindir: str, datadir: str) -> None:
    """Also usable against an explicitly manually staged local prefix."""
    manifest, sources, _ = fixture(root)
    executable = prefix/bindir/"hepta-research-portfolio"
    output = root/"portfolio-report.json"
    argv = [sys.executable,"-I","-B",str(executable),"--manifest",str(manifest),"--output",str(output)]
    for ref,path in sources.items():argv += ["--source",ref+"="+str(path)]
    env = os.environ.copy()
    env.pop("PYTHONPATH",None)
    env.pop("PYTHONHOME",None)
    # -I must ignore a malicious module in the working directory.
    (root/"hepta_research.py").write_text('raise RuntimeError("working-directory import")\n')
    result = subprocess.run(argv,cwd=root,env=env,text=True,capture_output=True,timeout=30)
    if result.returncode:
        raise AssertionError("isolated portfolio command failed: "+result.stderr)
    check_report(json.loads(output.read_text()))
    before = output.read_bytes()
    sources["a"].write_bytes(b"corrupt source\n")
    result = subprocess.run(argv,cwd=root,env=env,text=True,capture_output=True,timeout=30)
    if result.returncode != 2 or output.read_bytes() != before:
        raise AssertionError("digest rejection did not preserve existing report")
    package = prefix/datadir/"heptatrader/research/python/hepta_research"
    for name in ("model.py","pipeline.py","portfolio.py"):
        if not (package/name).is_file():raise AssertionError("missing installed module: "+name)


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir",type=Path,required=True)
    parser.add_argument("--config",default="Release")
    parser.add_argument("--bindir",default="bin")
    parser.add_argument("--datadir",default="share")
    args=parser.parse_args()
    for value in (args.bindir,args.datadir):
        if Path(value).is_absolute() or ".." in Path(value).parts:
            raise ValueError("relative SDK install directories required")
    with tempfile.TemporaryDirectory(prefix="hepta-portfolio-install-") as directory:
        root=Path(directory)
        stage=root/"stage"
        command=["cmake","--install",str(args.build_dir.resolve()),"--prefix",str(stage)]
        if args.config:command += ["--config",args.config]
        subprocess.run(command,check=True,timeout=90)
        moved=root/"relocated prefix"
        shutil.move(str(stage),str(moved))
        work=root/"unrelated working directory"
        work.mkdir()
        exercise(moved,work,args.bindir,args.datadir)
        print("CMake install + relocated isolated portfolio success/rejection: PASS")


if __name__ == "__main__":main()
