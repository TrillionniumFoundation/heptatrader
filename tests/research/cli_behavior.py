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
                "orders": 5, "fills": 3, "position": 1, "broker_authorized": False,
                "finalized": True, "active_orders": 0}
    for key, value in expected.items():
        require(summary.get(key) == value, f"unexpected {key}: {summary}")
    require(math.isclose(summary["fees"], .05, abs_tol=1e-10), "fees drift")
    require(math.isclose(summary["equity"], 99996.95, rel_tol=0, abs_tol=1e-8), "equity drift")
    trades = list(csv.DictReader(io.StringIO("\n".join(lines[:-1]))))
    require([int(t["timestamp_us"]) for t in trades] == [22, 52, 82], "causal fill times drift")
    require([int(t["quantity"]) for t in trades] == [1, -2, 2], "fill volume drift")
    repeated = subprocess.run(args, capture_output=True, text=True, timeout=10)
    require(repeated.returncode == 0 and repeated.stdout == result.stdout, "replay is not deterministic")
    require(summary["cost_basis"] == "average", "default cost basis changed")
    for mode in ("average", "fifo"):
        selected = subprocess.run([*args, mode], capture_output=True, text=True, timeout=10)
        require(selected.returncode == 0, selected.stderr)
        selected_lines = selected.stdout.splitlines()
        selected_summary = json.loads(selected_lines[-1])
        require(selected_summary["cost_basis"] == mode, "ignored cost selection")
        require(selected_lines[:-1] == lines[:-1], "accounting changed execution path")
        require(math.isclose(selected_summary["equity"], summary["equity"], rel_tol=0, abs_tol=1e-8),
                "cost allocation changed total equity")
        require(math.isclose(selected_summary["equity"], 100000 + selected_summary["realized_gross"] +
                            selected_summary["unrealized"] - selected_summary["fees"], rel_tol=0, abs_tol=1e-8),
                "accounting breakdown inconsistent")
    for mode in ("", "FIFO", "broker", "fifo,live"):
        rejected = subprocess.run([*args, mode], capture_output=True, text=True, timeout=10)
        require(rejected.returncode != 0 and "RESEARCH_REPLAY_FAILED" in rejected.stderr,
                "unknown cost mode accepted")
        require(not rejected.stdout, "invalid cost mode emitted output")
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "truncated.csv"
        path.write_text("\n".join((examples / "ticks.csv").read_text().splitlines()[:4]) + "\n", encoding="utf-8")
        truncated = args.copy()
        truncated[1] = str(path)
        end = subprocess.run(truncated, capture_output=True, text=True, timeout=10)
        require(end.returncode == 0, end.stderr)
        terminal = json.loads(end.stdout.splitlines()[-1])
        require(terminal["orders"] == 1 and terminal["fills"] == 0 and terminal["position"] == 0,
                "EOF must not fabricate a fill or position")
        require(terminal["finalized"] and terminal["active_orders"] == 0 and terminal["eof_terminal_events"] == 1,
                "EOF did not terminalize the pending order")
        duplicate = Path(directory) / "duplicate.csv"
        source_lines = (examples / "ticks.csv").read_text().splitlines()
        duplicate.write_text("\n".join([source_lines[0], *[line for row in source_lines[1:]
                                                         for line in (row, row)]]) + "\n", encoding="utf-8")
        duplicate_args = args.copy()
        duplicate_args[1] = str(duplicate)
        duplicated = subprocess.run(duplicate_args, capture_output=True, text=True, timeout=10)
        require(duplicated.returncode == 0 and duplicated.stdout == result.stdout,
                "streamed duplicate ticks changed fills, forecasts or liquidity")
        late = Path(directory) / "late-error.csv"
        late.write_text((examples / "ticks.csv").read_text().rstrip() +
                        "\nTEST.FUT,99,999,nan,1\n", encoding="utf-8")
        late_args = args.copy()
        late_args[1] = str(late)
        late_failure = subprocess.run(late_args, capture_output=True, text=True, timeout=10)
        require(late_failure.returncode != 0 and "RESEARCH_REPLAY_FAILED" in late_failure.stderr,
                "late malformed row was ignored")
        require('"equity"' not in late_failure.stdout and '"finalized"' not in late_failure.stdout,
                "streaming failure emitted a successful EOF summary")
        # Actual executable with a large constant stream; no retained input
        # vector, fictional fills, or unbounded stdout is needed to finish it.
        large = Path(directory) / "large.csv"
        with large.open("w", encoding="utf-8") as stream:
            stream.write("instrument,timestamp_us,sequence,price,volume\n")
            for index in range(25000):
                stream.write(f"TEST.FUT,{index},{index + 1},100,1\n")
        sessions = Path(directory) / "large-sessions.csv"
        sessions.write_text("open_us,close_us,trading_day\n0,25001,20260921\n", encoding="utf-8")
        large_args = [binary, str(large), str(sessions), "TEST.FUT", "10", "1", "2", "1"]
        large_result = subprocess.run(large_args, capture_output=True, text=True, timeout=10)
        require(large_result.returncode == 0, large_result.stderr)
        large_lines = large_result.stdout.splitlines()
        require(len(large_lines) == 2, "constant history invented fills or per-tick output")
        large_summary = json.loads(large_lines[-1])
        require(large_summary["forecasts"] == large_summary["orders"] == large_summary["fills"] == 0 and
                large_summary["position"] == 0 and large_summary["equity"] == 100000 and
                large_summary["finalized"] and large_summary["active_orders"] == 0,
                "constant streamed history changed the research state")
        bad = Path(directory) / "bad.csv"
        bad.write_text("instrument,timestamp_us,sequence,price,volume\nTEST.FUT,1,1,nan,3\n", encoding="utf-8")
        invalid = args.copy()
        invalid[1] = str(bad)
        failure = subprocess.run(invalid, capture_output=True, text=True, timeout=10)
        require(failure.returncode != 0 and "RESEARCH_REPLAY_FAILED" in failure.stderr, "bad input accepted")
        require('\"equity\"' not in failure.stdout, "failed replay emitted a success summary")
    invalid = args.copy()
    invalid[-1] = "0"
    failure = subprocess.run(invalid, capture_output=True, text=True, timeout=10)
    require(failure.returncode != 0, "zero quantity accepted")
    print("PASS: deterministic streamed output, duplicates, large input, EOF and late-error rejection")


if __name__ == "__main__":
    main()
