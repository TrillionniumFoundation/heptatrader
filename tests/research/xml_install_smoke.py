"""CMake install -> relocate -> isolated XML consumer; no source-package imports."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from xml_fixture import cli_args, fixture


def exercise(prefix: Path, work: Path, bindir: str, datadir: str) -> None:
    launcher = prefix / bindir / "hepta-research-xml"
    converter = prefix / bindir / "hepta-research-bars"
    package = prefix / datadir / "heptatrader/research/python"
    for path in (launcher, converter, package / "hepta_research/legacy_xml.py",
                 package / "hepta_research/legacy_binary.py"):
        if not path.is_file():
            raise RuntimeError("installed XML SDK is incomplete")
    env = os.environ.copy()
    for key in ("PYTHONPATH", "PYTHONHOME", "HEPTA_RESEARCH_BARS"):
        env.pop(key, None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    # Python isolated mode ignores the caller's path and environment. The
    # installed launcher must resolve its own relocated package and converter.
    probe = subprocess.run([sys.executable, "-I", "-B", "-c",
        "import sys;sys.path.insert(0,sys.argv[1]);import hepta_research.legacy_xml as m;print(m.__file__)",
        str(package)], cwd=work, env=env, capture_output=True, text=True, timeout=30, check=True)
    if Path(probe.stdout.strip()).resolve() != (package / "hepta_research/legacy_xml.py").resolve():
        raise RuntimeError("source import contamination")
    for binary, single in ((False, True), (False, False), (True, True), (True, False)):
        root = work / (str(int(binary)) + "-" + str(int(single)))
        root.mkdir()
        bundle = fixture(root, binary=binary, single=single)
        output = root / "report.json"
        command = [sys.executable, "-I", "-B", str(launcher), *cli_args(bundle, output)]
        result = subprocess.run(command, cwd=work, env=env, capture_output=True, text=True, timeout=30)
        if result.returncode:
            raise RuntimeError("installed XML consumer failed: " + result.stderr)
        report = json.loads(output.read_text())
        if ([v["delta"] for v in report["fills"]] != ["2", "-4"] or
                [v["price"] for v in report["fills"]] != ["12.5", "7.5"] or
                report["equity"][-1]["value"] != "971.50" or
                report["assumptions"]["capital"] != "1000" or
                report["assumptions"]["multiplier"] != "3" or
                report["assumptions"]["broker_authorized"] or
                report["equity"][-1]["complete"] or report["pending_target"] is not None):
            raise RuntimeError("installed replay differs from hand-computed scenario")
        # A late source-digest failure must not publish a partial replacement.
        before = output.read_bytes()
        sources = json.loads(bundle["bindings"].read_text())["sources"]
        with Path(sources[0]["path"]).open("ab") as changed:
            changed.write(b"corrupt")
        failed = subprocess.run(command, cwd=work, env=env, capture_output=True, timeout=30)
        if failed.returncode != 2 or output.read_bytes() != before:
            raise RuntimeError("installed XML failure changed existing report")
    print("XML installed consumer: CSV/BIN single/list, exact replay and failure preservation PASS")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--build-dir", type=Path, required=True)
    parser.add_argument("--config", default="")
    parser.add_argument("--bindir", default="bin")
    parser.add_argument("--datadir", default="share")
    args = parser.parse_args()
    for value in (args.bindir, args.datadir):
        if Path(value).is_absolute() or ".." in Path(value).parts:
            raise ValueError("relative install directories required")
    with tempfile.TemporaryDirectory(prefix="hepta-xml-install-") as temporary:
        work = Path(temporary)
        original, moved = work / "original", work / "relocated"
        command = ["cmake", "--install", str(args.build_dir.resolve()), "--prefix", str(original)]
        if args.config:
            command += ["--config", args.config]
        env = os.environ.copy()
        env.pop("DESTDIR", None)
        subprocess.run(command, env=env, check=True, capture_output=True, text=True, timeout=60)
        original.rename(moved)
        if original.exists():
            raise RuntimeError("old install prefix unexpectedly survived")
        exercise(moved, work, args.bindir, args.datadir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
