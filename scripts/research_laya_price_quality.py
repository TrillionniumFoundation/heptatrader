#!/usr/bin/env python3
"""Read-only closed-Laya seven-bin price baseline with fixed chronology."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT / "research/neuron_learning"))
from laya_cell.action_quality_research import collect_closed
from laya_cell.price_quality_research import experiment


def main():
    a=argparse.ArgumentParser(description=__doc__)
    a.add_argument("--evaluation-root",type=Path,required=True)
    a.add_argument("--output",type=Path,required=True)
    args=a.parse_args()
    os.umask(0o077)
    if (not args.output.is_absolute() or args.output.exists() or args.output.is_symlink()
            or not args.output.parent.is_dir()):
        a.error("output must be a new absolute path in an existing directory")
    rows,provenance=collect_closed(args.evaluation_root)
    report=experiment(rows,provenance)
    with args.output.open("x") as f:
        json.dump(report,f,sort_keys=True,separators=(",",":"),allow_nan=False)
        f.write("\n");f.flush();os.fsync(f.fileno())
    args.output.chmod(0o400)
    print(json.dumps({"report_id":report["report_id"],
                      "selected_smoothing":report["selected_smoothing"],
                      "split_counts":report["split_counts"],
                      "test_scores":report["test_scores"]},sort_keys=True))


if __name__=="__main__":
    main()
