"""Derive a separately proven IB schedule-contained historical research subwindow.

The parent API's N-D UTC-calendar coverage failure remains FAIL. Never edit
the source receipt or infer that a historical bar was known in real time.
No IB connection, broker orders, live training, model selection or promotion.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from .common import EvidenceError, read, sha, digest, write_once
from .dataset import parse_session, examples, split
from .ib_history import verified_receipt


def utc_epoch(value):
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if stamp.utcoffset() is None or stamp.utcoffset().total_seconds() != 0:
        raise EvidenceError("DERIVED_WINDOW_EXPLICIT_UTC_REQUIRED")
    return int(stamp.timestamp())


def validate_window(bars, old_bars, schedule, start, end):
    """Validate only source/temporal facts; no labels or model scores."""
    if not 0 < start < end or end >= int(datetime.now(timezone.utc).timestamp()):
        raise EvidenceError("DERIVED_HISTORICAL_WINDOW_ONLY")
    if start % 300 or (end + 1) % 300:
        raise EvidenceError("DERIVED_COMPLETE_FIVE_MINUTE_BOUNDARIES_REQUIRED")
    zone = schedule["timezone"]
    a = parse_session(schedule["start"], zone)
    z = parse_session(schedule["end"], zone)
    if not a <= start < end <= z:
        raise EvidenceError("DERIVED_INTERVAL_OUTSIDE_RETURNED_BROKER_HEADER")
    expected = set()
    for session in schedule["sessions"]:
        lo = parse_session(session["start"], zone)
        hi = parse_session(session["end"], zone)
        if hi <= lo:
            raise EvidenceError("BROKER_SESSION_ORDER")
        expected.update(t for t in range(((lo+299)//300)*300, hi, 300)
                        if start <= t and t+300 <= end)
    selected = [bar for bar in bars if start <= bar["time"] and bar["time"]+300 <= end]
    timestamps = [bar["time"] for bar in selected]
    if len(timestamps) < 100 or timestamps != sorted(set(timestamps)):
        raise EvidenceError("DERIVED_SUBWINDOW_TIME_OR_SIZE")
    if set(timestamps) != expected:
        raise EvidenceError("DERIVED_EXPECTED_SCHEDULE_MISMATCH")
    old = {bar["time"]: bar for bar in old_bars}
    new = {bar["time"]: bar for bar in selected}
    if len(old) != len(old_bars) or len(new) != len(selected):
        raise EvidenceError("PARENT_SOURCE_TIMESTAMP_DUPLICATION")
    overlap = sorted(set(old) & set(new))
    added = sorted(set(new) - set(old))
    if (not overlap or not added or
            any(old[t] != new[t] for t in overlap) or
            min(added) <= max(old)):
        raise EvidenceError("DERIVED_OVERLAP_OR_APPEND_MISMATCH")
    return selected, expected, overlap, added, a, z


def derive(raw_root, parent_root, old_root, output_root, start_utc, end_utc):
    raw_root, parent_root, old_root, output_root = (
        Path(raw_root), Path(parent_root), Path(old_root), Path(output_root))
    if output_root.exists():
        raise EvidenceError("NEW_OUTPUT_ONLY")
    parent = read(parent_root / "paired-bars.json")
    pc = read(parent_root / "coverage.json")
    old = read(old_root / "paired-bars.json")
    oc = read(old_root / "coverage.json")
    raw_session = read(raw_root / "session.json")
    plan = read(raw_root / "plan.json")
    if (parent.get("coverage") != pc or
            parent.get("trade_authority") is not False or
            parent.get("actual_execution_rewards") != 0 or
            pc.get("full_requested_coverage_proven") is not False or
            pc.get("scheduled_missing_count") != 0 or
            pc.get("unpaired_timestamps") != 0 or
            pc.get("crossed_open_or_close") != 0):
        raise EvidenceError("PARENT_UNPROVEN_BUT_OTHERWISE_INTACT_REQUIRED")
    if (old.get("coverage") != oc or
            oc.get("full_requested_coverage_proven") is not True or
            old.get("trade_authority") is not False or
            old.get("actual_execution_rewards") != 0):
        raise EvidenceError("PREVIOUS_HISTORY_NOT_PROVEN")
    if (raw_session.get("paper_verified") is not True or
            raw_session.get("broker_orders_sent") != 0 or
            raw_session.get("exception") or
            raw_session.get("requests_sent_this_run") != 4 or
            len(raw_session.get("results", [])) != 4 or
            any(x.get("state") != "RECEIVED" for x in raw_session["results"])):
        raise EvidenceError("HISTORICAL_PAPER_SESSION_NOT_COMPLETE")
    requests = plan.get("requests", [])
    if (len(requests) != 4 or
            sorted((x["kind"], x["what"]) for x in requests) !=
            sorted((x, y) for x, y in
                   [("bars", "MIDPOINT"), ("bars", "BID"),
                    ("bars", "ASK"), ("schedule", "SCHEDULE")])):
        raise EvidenceError("EXPECTED_THREE_BARS_AND_SCHEDULE_REQUESTS")
    files = {}
    for request in requests:
        key = digest({"contract": plan["config"]["contract"], **request})
        receipt = verified_receipt(raw_root / "chunks" / key, request)
        if receipt is None or receipt.get("state") != "RECEIVED":
            raise EvidenceError("IMMUTABLE_SOURCE_RECEIPT_MISSING")
        files[request["what"]] = raw_root / "chunks" / key / "response.json"
    schedule_path = files["SCHEDULE"]
    sched = read(schedule_path).get("schedule")
    if not isinstance(sched, dict):
        raise EvidenceError("BROKER_SCHEDULE_RESPONSE_REQUIRED")
    start, end = utc_epoch(start_utc), utc_epoch(end_utc)
    selected, expected, overlap, added, head_start, head_end = validate_window(
        parent["bars"], old["bars"], sched, start, end)
    iso = lambda epoch: datetime.fromtimestamp(epoch, timezone.utc).isoformat()
    proof = {
        "schema": "heptatrader.derived-schedule-subwindow-proof.v1",
        "parent_api_interval_full_coverage_proven": False,
        "derived_scope_start_utc": iso(start),
        "derived_scope_end_utc": iso(end),
        "parent_request_start_utc": pc["requested_start_utc"],
        "parent_request_end_utc": pc["requested_end_utc"],
        "broker_schedule_header_start_utc": iso(head_start),
        "broker_schedule_header_end_utc": iso(head_end),
        "parent_bars_sha256": sha(parent_root / "paired-bars.json"),
        "parent_coverage_sha256": sha(parent_root / "coverage.json"),
        "parent_raw_session_sha256": sha(raw_root / "session.json"),
        "parent_raw_schedule_response_sha256": sha(schedule_path),
        "parent_original_request_plan_sha256": sha(raw_root / "plan.json"),
        "old_bars_sha256": sha(old_root / "paired-bars.json"),
        "expected_subwindow_bars": len(expected),
        "derived_bar_count": len(selected),
        "unpaired_or_unscheduled_bars": 0,
        "missing_schedule_bars": 0,
        "overlap_exact": len(overlap),
        "new_bars": len(added),
        "gap_fill_performed": False,
        "broker_orders_sent": 0,
        "model_promotion": False,
        "research_only_revised_not_contemporaneous": True,
    }
    coverage = {
        "schema": "heptatrader.historical-coverage.v1",
        "coverage_scope": "DERIVED_SUBWINDOW_WITHIN_RETURNED_BROKER_SCHEDULE",
        "requested_start_utc": iso(start),
        "requested_end_utc": iso(end),
        "actual_first_utc": iso(selected[0]["time"]),
        "actual_last_utc": iso(selected[-1]["time"]),
        "bar_count": len(selected),
        "scheduled_bars": len(expected),
        "scheduled_missing_count": 0,
        "unpaired_timestamps": 0,
        "crossed_open_or_close": 0,
        "calendar_response_interval_complete": True,
        "full_requested_coverage_proven": True,
        "calendar_coverage_rule": "PROVEN_DERIVED_SUBWINDOW_NOT_PARENT_API_INTERVAL",
        "parent_api_interval_full_coverage_proven": False,
        "derivation_proof_sha256": digest(proof),
        "counts_by_kind": {kind: len(selected) for kind in ("BID", "ASK", "MIDPOINT")},
        "gap_fill_performed": False,
        "revised_historical_data_not_contemporaneous": True,
        "research_only": True,
    }
    dataset = {
        "schema": "heptatrader.historical-paired-bars.v1",
        "bars": selected,
        "coverage": coverage,
        "sources": [{
            "path": str(parent_root),
            "sha256": sha(parent_root / "paired-bars.json"),
            "derivation_proof_sha256": digest(proof)
        }],
        "trade_authority": False,
        "actual_execution_rewards": 0,
        "live_evaluation_repaired": False,
        "research_only_revised_not_contemporaneous": True,
    }
    dataset["dataset_id"] = digest(dataset)
    rows, excluded = examples(selected)
    groups, bounds = split(rows)
    sample = {
        "schema": "heptatrader.offline-price-examples.v1",
        "lookback_bars": 12,
        "horizon_bars": 3,
        "bar_seconds": 300,
        "boundaries": bounds,
        "exclusions": excluded,
        "groups": groups,
        "actual_execution_rewards": 0,
        "trade_authority": False,
    }
    output_root.mkdir(mode=0o700, parents=False, exist_ok=False)
    write_once(output_root / "paired-bars.json", dataset)
    sample["paired_bars_sha256"] = sha(output_root / "paired-bars.json")
    sample["dataset_id"] = digest(sample)
    write_once(output_root / "examples.json", sample)
    write_once(output_root / "coverage.json", coverage)
    proof["output_sha256"] = {
        name: sha(output_root / name)
        for name in ("paired-bars.json", "examples.json", "coverage.json")}
    write_once(output_root / "derivation-proof.json", proof)
    return {
        "derived_bars": len(selected),
        "exact_overlap": len(overlap),
        "new_bars": len(added),
        "source_parent_api_interval_still_unproven": True,
        "derived_sha256": sha(output_root / "paired-bars.json"),
        "proof_sha256": sha(output_root / "derivation-proof.json"),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--parent", type=Path, required=True)
    parser.add_argument("--old", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--start-utc", required=True)
    parser.add_argument("--end-utc", required=True)
    args = parser.parse_args()
    print(json.dumps(derive(args.raw, args.parent, args.old, args.output,
                            args.start_utc, args.end_utc), sort_keys=True))


if __name__ == "__main__":
    main()
