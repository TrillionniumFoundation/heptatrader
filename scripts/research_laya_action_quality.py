#!/usr/bin/env python3
"""Frozen retrospective quality calibration. No trading/execution imports."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research/neuron_learning"))
from laya_cell.action_quality_research import collect_closed, experiment


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evaluation-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    if (args.output.exists() or args.output.is_symlink() or not args.output.is_absolute()
            or not args.output.parent.is_dir()):
        parser.error("output must be a new absolute file in a pre-existing directory")
    rows, provenance = collect_closed(args.evaluation_root)
    report = experiment(rows, provenance)
    report["no_execution_or_model_modification"] = True
    with args.output.open("x") as stream:
        json.dump(report, stream, sort_keys=True, separators=(",", ":"), allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    args.output.chmod(0o400)
    print(json.dumps({"report_id": report["report_id"], "observations": len(rows),
                      "selected": report["validation_selected"],
                      "test": report["test_scores_after_selection"]}, sort_keys=True))


if __name__ == "__main__":
    main()
