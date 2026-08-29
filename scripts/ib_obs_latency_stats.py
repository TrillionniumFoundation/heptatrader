#!/usr/bin/env python3
import argparse
import json
from collections import defaultdict
from pathlib import Path


def percentile(sorted_vals, p):
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return float(sorted_vals[0])
    rank = (len(sorted_vals) - 1) * p
    lo = int(rank)
    hi = min(lo + 1, len(sorted_vals) - 1)
    w = rank - lo
    return float(sorted_vals[lo] * (1 - w) + sorted_vals[hi] * w)


def main():
    ap = argparse.ArgumentParser(description="Summarize Hepta IB observability JSONL latency metrics")
    ap.add_argument("--input", default="runtime-logs/ib_observability.jsonl", help="JSONL file path")
    ap.add_argument("--output", default="", help="Optional output markdown path")
    ap.add_argument("--only-ok", action="store_true", help="Only include rows where ok=true")
    args = ap.parse_args()

    in_path = Path(args.input)
    if not in_path.exists():
        raise SystemExit(f"input not found: {in_path}")

    groups = defaultdict(list)
    bad_lines = 0
    total = 0

    with in_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            total += 1
            try:
                row = json.loads(line)
            except Exception:
                bad_lines += 1
                continue

            if row.get("event") != "latency":
                continue
            if "latency_ms" not in row:
                continue
            if args.only_ok and not row.get("ok", False):
                continue

            path = str(row.get("path", "unknown"))
            stage = str(row.get("stage", "unknown"))
            try:
                ms = float(row["latency_ms"])
            except Exception:
                continue
            groups[(path, stage)].append(ms)

    lines = []
    lines.append(f"# IB Observability Latency Summary")
    lines.append(f"- input: `{in_path}`")
    lines.append(f"- total lines: {total}")
    lines.append(f"- bad json lines: {bad_lines}")
    lines.append("")
    lines.append("| path | stage | count | p50_ms | p95_ms | min_ms | max_ms |")
    lines.append("|---|---|---:|---:|---:|---:|---:|")

    for (path, stage) in sorted(groups.keys()):
        vals = sorted(groups[(path, stage)])
        p50 = percentile(vals, 0.50)
        p95 = percentile(vals, 0.95)
        lines.append(
            f"| {path} | {stage} | {len(vals)} | {p50:.2f} | {p95:.2f} | {vals[0]:.2f} | {vals[-1]:.2f} |"
        )

    report = "\n".join(lines) + "\n"

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(report, encoding="utf-8")
        print(f"written: {out_path}")
    else:
        print(report)


if __name__ == "__main__":
    main()
