#!/usr/bin/env python3
"""Install/relocate the binary reader, then exercise real offline computation."""
from __future__ import annotations

import argparse
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def exercise(prefix: Path, root: Path, bindir: str, datadir: str) -> dict:
    env = dict(os.environ)
    for key in ("PYTHONPATH", "PYTHONHOME", "HEPTA_RESEARCH_BARS"):
        env.pop(key, None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    command = prefix/bindir/"hepta-research-binary"
    builder = prefix/bindir/"hepta-research-bars"
    contract = prefix/datadir/"doc/hepta-research/BINARY-IMPORT.md"
    require(command.is_file() and builder.is_file() and contract.is_file(), "binary SDK files missing")
    sessions, source, output = (root/name for name in ("sessions.csv","ticks.bin","report.json"))
    sessions.write_text("begin_us,end_us,trading_day\n0,360000000,19700101\n")
    records = []
    for i,price in enumerate((10,12,13,11,9,14)):
        # Independent explicit-byte fixture, not the production decoder or its
        # private intermediate. No native struct reinterpretation in this test.
        data = bytearray(424)
        for offset,text in ((0,"TEST"),(11,"19700101"),(20,"19700101"),
                            (29,f"00:0{i}:00"),(44,"TEST")):
            data[offset:offset+len(text)] = text.encode()
        struct.pack_into("<d",data,288,price)
        struct.pack_into("<q",data,328,10+i)
        struct.pack_into("<dd",data,336,100.0,20.0)
        records.append(bytes(data))
    source.write_bytes(b"".join(records))
    args = [sys.executable,"-I",str(command),"--layout","hepta-depth82-le-a8-v1",
            "--ticks",str(source),"--sessions",str(sessions),"--output",str(output),
            "--instrument","TEST","--clock-zone","UTC","--tick-size","0.2",
            "--period-us","60000000","--first-volume","baseline","--capital","1000",
            "--quantity","2","--fast","1","--slow","2","--slippage","0.5","--fee-per-unit","0.25"]

    def run(expected):
        result = subprocess.run(args,cwd=root,env=env,stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE,text=True,timeout=120,check=False)
        require(result.returncode == expected, f"binary command exit {result.returncode}: {result.stderr}")

    run(0)
    prior = output.read_bytes()
    report = json.loads(prior)
    require([Decimal(f["delta"]) for f in report["fills"]] == [Decimal(2),Decimal(-4)], "fills")
    require([Decimal(f["price"]) for f in report["fills"]] == [Decimal("13.5"),Decimal("8.5")], "fill prices")
    require(Decimal(report["fees"]) == Decimal("1.5"), "fees")
    require([Decimal(v["value"]) for v in report["equity"]] ==
            list(map(Decimal,("1000","1000","998.5","994.5","987.5","977.5"))), "equity")
    require(report["pending_target"] is None and not report["equity"][-1]["complete"], "EOF causality")
    require(report["mode"] == "OFFLINE_HYPOTHETICAL" and not report["assumptions"]["broker_authorized"], "authority")
    require(report["input"]["legacy_ticks"]["source_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest(), "source digest")
    require(all("raw_fields" not in row for row in report["input"]["legacy_ticks"]["source_fields"]), "invented raw depth")
    with source.open("ab") as data: data.write(b"partial")
    run(2)
    require(output.read_bytes() == prior, "late failure replaced report")
    files = [command,builder,contract]
    files += [prefix/datadir/"heptatrader/research/python/hepta_research"/name
              for name in ("legacy_binary.py","legacy_ticks.py","legacy.py","pipeline.py","model.py")]
    return {"schema":"hepta.research.binary-install.v1","status":"PASS",
            "scope":"relocated_binary_command",
            "checks":["isolated_same_prefix_builder","known_fills_cost_equity",
                      "incomplete_final_bar","binary_provenance","failure_preserving_publication"],
            "installed_sha256":{str(p.relative_to(prefix)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files},
            "broker_qualification":False,"canonical_runtime_qualification":False}


def validate(build: Path, config: str, bindir: str, datadir: str) -> dict:
    for value in (bindir,datadir):
        if not value or Path(value).is_absolute() or ".." in Path(value).parts:
            raise ValueError("relative install paths required")
    with tempfile.TemporaryDirectory(prefix="hepta-binary-install-") as directory:
        root = Path(directory)
        original, prefix = root/"original", root/"relocated"
        result = subprocess.run(["cmake","--install",str(build.resolve(strict=True)),
                                 "--prefix",str(original),"--config",config],
                                cwd=root,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                                timeout=120,check=False)
        require(result.returncode == 0, "SDK install failed: "+result.stdout+result.stderr)
        original.rename(prefix)
        return exercise(prefix,root,bindir,datadir)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir",type=Path,required=True)
    parser.add_argument("--config",default="Release")
    parser.add_argument("--bindir",default="bin")
    parser.add_argument("--datadir",default="share")
    args = parser.parse_args()
    try:
        print(json.dumps(validate(args.build_dir,args.config,args.bindir,args.datadir),sort_keys=True,indent=2))
    except (OSError,ValueError,RuntimeError,KeyError,subprocess.TimeoutExpired) as error:
        print("installed binary reader FAILED: "+str(error),file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
