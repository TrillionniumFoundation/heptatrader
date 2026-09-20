#!/usr/bin/env python3
"""Numerical and failure-path acceptance of the actual offline executable."""
import csv
import io
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def main() -> None:
    binary, examples = sys.argv[1], Path(sys.argv[2])
    args = [binary, str(examples / "ticks.csv"), str(examples / "sessions.csv"),
            "TEST.FUT", "10", "1", "2", "1"]
    result = subprocess.run(args, capture_output=True, text=True, timeout=10)
    require(result.returncode == 0, result.stderr)
    lines = result.stdout.splitlines()
    summary = json.loads(lines[-1])
    expected = {"model": "offline-last-trade-liquidity-v1", "forecasts": 7,
                "orders": 5, "fills": 3, "position": 1, "broker_authorized": False}
    for key, value in expected.items():
        require(summary.get(key) == value, f"unexpected {key}: {summary}")
    require(math.isclose(summary["fees"], .05, abs_tol=1e-10), "fees drift")
    require(math.isclose(summary["equity"], 99996.95, rel_tol=0, abs_tol=1e-8), "equity drift")
    trades = list(csv.DictReader(io.StringIO("\n".join(lines[:-1]))))
    require([int(t["timestamp_us"]) for t in trades] == [22, 52, 82], "causal fill times drift")
    require([int(t["quantity"]) for t in trades] == [1, -2, 2], "fill volume drift")
    repeated = subprocess.run(args, capture_output=True, text=True, timeout=10)
    require(repeated.returncode == 0 and repeated.stdout == result.stdout, "replay is not deterministic")
    with tempfile.TemporaryDirectory() as directory:
        bad = Path(directory) / "bad.csv"
        bad.write_text("instrument,timestamp_us,sequence,price,volume\nTEST.FUT,1,1,nan,3\n", encoding="utf-8")
        invalid = args.copy()
        invalid[1] = str(bad)
        failure = subprocess.run(invalid, capture_output=True, text=True, timeout=10)
        require(failure.returncode != 0 and "RESEARCH_REPLAY_FAILED" in failure.stderr, "bad input accepted")
        require('"equity"' not in failure.stdout, "failed replay emitted a success summary")
    invalid = args.copy()
    invalid[-1] = "0"
    failure = subprocess.run(invalid, capture_output=True, text=True, timeout=10)
    require(failure.returncode != 0, "zero quantity accepted")
    print("PASS: deterministic numeric output and fail-closed CLI inputs")


if __name__ == "__main__":
    main()
