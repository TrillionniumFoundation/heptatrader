"""Independent 3x4 frozen PRICE verification on copied PAPER source, stdlib only.

Not original Pocket4 Train V2 code, no model, broker, financial action or host
trust. The prior source-custody parser is independently source-hashed.
"""
from __future__ import annotations
import argparse
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import time

from paged_crosshost_custody import (
    copied_original_inventory, CustodyError, require,
    canonical, strict, sha, private_file,
)

PINNED_STDLIB_CUSTODY_SHA = "b220ceaa873abbbd02cb77e0b45256c3e6407b92ff7966e8f896da8d85913f93"
SOURCE_AUDIT_SHA = "5da69b7245d5c2e29af45b3f49d2ebf56e0463e103ebb048757b55e1e5f6329a"
SOURCE_CLOSE_SHA = "62786642876b7aaae6463f5ff728b6add9c11d65ca17934ed995f85fe5104156"
FROZEN_PREFIX_269 = "cd8540b689d4ae75d76ec34fe0e6f72a06a0df729b8dd8d4a12cd13f5a5658db"
SCRIPT_SCHEMA = "heptatrader.train-v2-qian-crosshost-frozen-3x4.v1"
HORIZONS = (5, 15, 30, 60)
EXPENSE_STRESS_BPS = Decimal("10.9133557")


def load_pinned(path, want_sha, schema):
    raw = private_file(path, 128 * 1024)
    require(sha(raw) == want_sha, "FROZEN_ORIGINAL_RECEIPT_HASH_CHANGED")
    doc = strict(raw)
    require(doc.get("schema") == schema, "FROZEN_ORIGINAL_RECEIPT_SCHEMA")
    identity = "audit_id" if "original-price-cohort-audit" in schema else "closeout_id"
    require(doc.get(identity) == sha(canonical({
        k: v for k, v in doc.items() if k != identity})), "FROZEN_ORIGINAL_RECEIPT_DIGEST")
    return doc


def fixed_originals(folder, prior, closeout):
    campaign = strict(private_file(folder / "campaign.json", 32768))
    roster = strict(private_file(folder / "timers-roster.json", 32768))
    require(
        sha(private_file(folder / "campaign.json", 32768)) == prior["campaign_sha256"]
        and sha(private_file(folder / "timers-roster.json", 32768)) == prior["roster_sha256"]
        and campaign["campaign_id"] == prior["campaign_id"] == closeout["frozen_campaign_id"]
        and roster["roster_id"] == prior["roster_id"] == closeout["frozen_roster_id"]
        and roster["campaign_id"] == campaign["campaign_id"]
        and roster["registered_before_any_slot"] is True
        and campaign["actual_model_predictor"] is False
        and campaign["selection"] == "NO_TRADE_ONLY_PRICE_OBSERVATION"
        and campaign["broker_orders_sent"] == 0
        and campaign["trade_authority"] is False,
        "FROZEN_CAMPAIGN_OR_ROSTER_SCOPE_CHANGED")
    require(
        prior["sealed_original_price_horizon_labels"] == 12
        and prior["captured_original_decision_slots"] == 3
        and prior["declared_source_horizon_cells"] == 12
        and prior["actual_trading_reward_samples"] == 0
        and prior["original_model_forecast_count"] == 0
        and prior["true_behavior_propensity_attested"] is False
        and closeout["terminal_state"] == "ALL_ORIGINAL_PRICES_OBSERVED"
        and closeout["verified_distinct_original_source_decisions"] == 3
        and closeout["verified_original_horizon_labels"] == 12
        and closeout["hindsight_midprice_comparison_is_executable_pnl"] is False
        and closeout["actual_broker_fill_profit_samples"] == 0,
        "FROZEN_3X4_ECONOMIC_SCOPE_CHANGED")
    files = []
    for slot in range(3):
        audit = prior["all_slots"][slot]
        src = strict(private_file(folder / f"slot-{slot:02d}.input.json", 16384))
        timer = strict(private_file(folder / f"slot-{slot:02d}.timer-result.json", 16384))
        require(
            audit["slot"] == src["slot"] == timer["slot"] == slot
            and audit["original_input_sealed"] is True
            and sha(private_file(folder / f"slot-{slot:02d}.input.json", 16384)) ==
                audit["original_input_sha256"]
            and sha(private_file(folder / f"slot-{slot:02d}.timer-result.json", 16384)) ==
                audit["timer_result_sha256"]
            and audit["source_decision_at_ms"] == src["decision_at_ms"]
            and audit["original_source_quote_id"] == src["original_source_id"]
            and audit["original_input_id"] == src["input_id"]
            and src["original_source_available_after_registration"] is True
            and src["decision_at_ms"] <= src["source_valid_until_ms"]
            and src["source_available_at_ms"] <= src["decision_at_ms"]
            and src["source_event_at_ms"] <= src["source_available_at_ms"]
            and src["all_future_labels_unobserved"] is True
            and src["forecast_model_run"] is False
            and src["native_ledgerwriter_ack"] is False
            and src["selected_original_trading_action"] is None
            and src["broker_orders_sent"] == timer["broker_orders_sent"] == 0
            and timer["source_input_sha256"] == audit["original_input_sha256"]
            and timer["state"] == "SOURCE_INPUT_SEALED_NO_LABEL"
            and timer["worker_finished_at_ms"] <=
                timer["original_last_allowed_ms"],
            "FROZEN_ORIGINAL_INPUT_OR_PREDECISION_TIMER_CHANGED")
        outcomes = []
        for h, expected in zip(HORIZONS, audit["outcomes"]):
            path = folder / f"slot-{slot:02d}-h{h:02d}.outcome.json"
            result = strict(private_file(path, 16384))
            require(
                expected["horizon_min"] == result["horizon_min"] == h
                and sha(private_file(path, 16384)) == expected["immutable_outcome_sha256"]
                and expected["state"] == "VERIFIED_OBSERVED_ORIGINAL_PRICE_ONLY"
                and result["outcome_state"] == "OBSERVED_NEW_ORIGINAL_MARKET_PRICE_ONLY"
                and result["original_midprice_change_bps"] == expected["price_bps"]
                and result["decision_at_ms"] == src["decision_at_ms"]
                and result["due_at_ms"] == src["decision_at_ms"] + h * 60000
                and result["original_source_quote_id"] == src["original_source_id"]
                and result["preoutcome_input_id"] == src["input_id"]
                and result["campaign_id"] == campaign["campaign_id"]
                and result["observed_at_ms"] >= result["due_at_ms"]
                and result["not_a_model_prediction"] is True
                and result["price_outcome_is_not_real_execution_pnl"] is True
                and result["actual_ib_fills_or_slippage_attested"] is False
                and result["independent_observer_signed"] is False
                and result["canonical_owner_enrolled"] is False
                and result["model_selection_permitted"] is False
                and result["broker_orders_sent"] == 0
                and result["trading_authority"] is False,
                "FROZEN_PRICE_OUTCOME_PROVENANCE_OR_AUTHORITY_CHANGED")
            outcomes.append(result)
        files.append((src, timer, outcomes))
    require(
        len({x["original_source_id"] for x, _, _ in files}) == 3
        and len({x["input_id"] for x, _, _ in files}) == 3
        and all(files[i][0]["source_event_at_ms"] + 60 * 60000 + 65000 <
                files[i + 1][0]["source_event_at_ms"] - 60000 for i in (0, 1)),
        "FROZEN_DECISION_REUSE_OR_WINDOW_OVERLAP")
    return campaign, roster, files


def independent_window_price(source, src, originals, audit_outcomes):
    """Use only independently decoded original, naturally sealed quote bytes."""
    rows = source["window_rows"]
    ids = {row[3]: row for row in rows}
    start = ids.get(src["original_source_id"])
    require(start is not None, "INDEPENDENT_INPUT_QUOTE_ABSENT")
    require(
        start[0] == src["source_available_at_ms"]
        and start[1] == src["source_event_at_ms"]
        and start[2] == src["source_valid_until_ms"]
        and start[4] == Decimal(src["input_quote_midpoint"]),
        "INDEPENDENT_INPUT_ID_TIME_PRICE_CHANGED")
    for witness in src["original_feature_input_receipts"]:
        quote = ids.get(witness["source_id"])
        require(
            quote is not None
            and quote[0] == witness["available_at_ms"]
            and quote[1] == witness["event_at_ms"]
            and quote[3] == witness["source_sha256"],
            "INDEPENDENT_PREDECISION_FEATURE_LINEAGE_CHANGED")
        require(
            quote[0] <= src["decision_at_ms"],
            "INDEPENDENT_FEATURE_KNOWN_AFTER_DECISION")
    require(
        src["source_valid_until_ms"] >= src["decision_at_ms"]
        and src["source_event_at_ms"] <= src["source_available_at_ms"]
        and src["source_available_at_ms"] <= src["decision_at_ms"],
        "INDEPENDENT_INPUT_BROKER_EXPIRY_OR_TIME")
    by_event = {}
    for row in sorted(rows, key=lambda q: (q[1], q[0], q[3])):
        old = by_event.get(row[1])
        if old is not None:
            require(old[4] == row[4], "INDEPENDENT_DUPLICATE_EVENT_PRICE_CHANGED")
        else:
            by_event[row[1]] = row
    times = sorted(by_event)
    results = []
    for horizon, original, expected in zip(HORIZONS, originals, audit_outcomes):
        due = src["decision_at_ms"] + horizon * 60000
        require(
            original["due_at_ms"] == due
            and original["horizon_min"] == horizon,
            "INDEPENDENT_FROZEN_HORIZON_CHANGED")
        target_time = next((t for t in times if t >= due), None)
        require(
            target_time is not None and target_time <= due + 5000,
            "INDEPENDENT_ORIGINAL_ENDPOINT_MISSING")
        route = [t for t in times if src["source_event_at_ms"] <= t <= target_time]
        require(
            route and route[0] == src["source_event_at_ms"]
            and route[-1] == target_time
            and all(b-a <= 30000 for a, b in zip(route, route[1:])),
            "INDEPENDENT_ORIGINAL_PATH_GAP")
        actual = (by_event[target_time][4] / start[4] - Decimal(1)) * Decimal(10000)
        require(
            actual == Decimal(original["original_midprice_change_bps"]),
            "INDEPENDENT_HINDSIGHT_PRICE_MISMATCH")
        results.append({
            "horizon_min": horizon,
            "due_ms": due,
            "source_event_at_ms": src["source_event_at_ms"],
            "original_source_id": src["original_source_id"],
            "first_qualifying_outcome_event_ms": target_time,
            "exact_original_mid_return_bps": str(actual),
            "original_outcome_sha256": expected["immutable_outcome_sha256"],
            "outcome_matches_original_bytes": True,
            "path_gap_max_ms": max((b-a for a,b in zip(route,route[1:])), default=0),
            "economically_executable_return_attested": False,
        })
    return results


def verify_copied_three_by_four(copy_root, original_root, period, evidence_root):
    evidence_root = Path(evidence_root)
    parser_path = Path(__file__).with_name("paged_crosshost_custody.py")
    require(sha(parser_path.read_bytes()) == PINNED_STDLIB_CUSTODY_SHA,
            "INDEPENDENT_ORIGINAL_PARSER_CHANGED")
    prior = load_pinned(
        evidence_root / "prior-full-audit.json", SOURCE_AUDIT_SHA,
        "heptatrader.train-v2-original-price-cohort-audit.v2")
    closeout = load_pinned(
        evidence_root / "prior-closeout.json", SOURCE_CLOSE_SHA,
        "heptatrader.train-v2-price-cohort-closeout.v1")
    campaign, roster, inputs = fixed_originals(
        evidence_root / "original-files", prior, closeout)
    require(
        campaign["period_sha256"] == period
        and campaign["source_root"] == original_root
        and prior["original_sealed_segment_count"] == 269
        and prior["original_sealed_archive_fingerprint_sha256"] == FROZEN_PREFIX_269
        and closeout["original_sealed_source_fingerprint_sha256"] == FROZEN_PREFIX_269
        and Decimal(prior["fixed_frozen_stressed_cost_plus_margin_bps"]) == EXPENSE_STRESS_BPS
        and Decimal(closeout["fixed_original_stress_cost_plus_margin_bps"]) == EXPENSE_STRESS_BPS,
        "INDEPENDENT_ORIGINAL_PERIOD_OR_FROZEN_HURDLE_CHANGED")
    checked_windows = []
    complete = []
    source_metadata = None
    for slot, (src, timer, outcomes) in enumerate(inputs):
        begin = src["source_event_at_ms"] - 60000
        end = src["decision_at_ms"] + 60 * 60000 + 65000
        require(end-begin < 7200000, "INDEPENDENT_PRICE_WINDOW_TOO_WIDE")
        source = copied_original_inventory(
            copy_root, original_root, period, begin, end)
        require(
            source["numeric_segments"] >= 269
            and source["frozen_prefix_sha256"](269) == FROZEN_PREFIX_269
            and source["archive_pages"] >= 1,
            "INDEPENDENT_FROZEN_FIRST269_PREFIX_NOT_PRESERVED")
        view = (
            source["numeric_segments"], source["archive_pages"],
            source["archived_segments"], source["source_fingerprint_sha256"],
            source["original_total_unique_quote_receipts"],
            source["original_source_bytes"],
            source["original_archive_checkpoint_chain_head"],
        )
        if source_metadata is not None:
            require(
                view == source_metadata,
                "INDEPENDENT_COPIED_SOURCE_ROTATED_DURING_THREE_READS")
        source_metadata = view
        labels = independent_window_price(source, src, outcomes, prior["all_slots"][slot]["outcomes"])
        require(len(labels) == 4, "INDEPENDENT_HORIZONS_INCOMPLETE")
        checked_windows.append({
            "slot": slot,
            "source_input_sha256": sha(
                private_file(
                    evidence_root / "original-files" /
                    f"slot-{slot:02d}.input.json", 16384)),
            "original_source_id": src["original_source_id"],
            "original_event_ms": src["source_event_at_ms"],
            "decision_ms": src["decision_at_ms"],
            "from_ms": begin, "until_ms": end,
            "selected_quote_count": source["original_selected_quote_receipts"],
            "horizons": labels,
        })
        complete.extend(labels)
    values = [abs(Decimal(row["exact_original_mid_return_bps"]))
              for row in complete]
    require(
        len(checked_windows) == 3
        and len(complete) == 12
        and len({row["original_source_id"] for row in complete}) == 3
        and max(values) == Decimal(closeout["largest_abs_hindsight_midprice_move_bps"])
        and all(v < EXPENSE_STRESS_BPS for v in values)
        and closeout["hindsight_midprice_cells_at_or_above_stress_hurdle"] == 0
        and closeout["economic_alpha_qualified"] is False
        and prior["economic_generalization_qualified"] is False,
        "INDEPENDENT_COHORT_ECONOMICS_OR_DENOMINATOR_CHANGED")
    require(
        source_metadata is not None and source_metadata[0] > 269,
        "INDEPENDENT_SEALED_CUSTODY_NOT_FOLLOWTHROUGH")
    report = {
        "schema": SCRIPT_SCHEMA,
        "state": "INDEPENDENT_COPIED_ORIGINAL_3x4_PRICE_ONLY_MATCH_NO_TRADE",
        "original_custody_parser_sha256": sha(
            Path(__file__).with_name("paged_crosshost_custody.py").read_bytes()),
        "independent_review_code_sha256": sha(Path(__file__).read_bytes()),
        "transport": "OPERATOR_SEQUENTIAL_SSH_COPY_PAPER_ORIGINAL_NOT_BROKER_SIGNATURE",
        "original_source_absolute_identity": original_root,
        "original_source_period_sha256": period,
        "first269_frozen_original_segment_fingerprint": FROZEN_PREFIX_269,
        "current_copied_sealed_segments": source_metadata[0],
        "archive_page_count": source_metadata[1],
        "archived_segments": source_metadata[2],
        "complete_snapshot_fingerprint": source_metadata[3],
        "original_verified_quote_ids": source_metadata[4],
        "total_complete_original_bytes": source_metadata[5],
        "archive_checkpoint_chain_head": source_metadata[6],
        "original_prior_full_audit_sha256": SOURCE_AUDIT_SHA,
        "original_prior_final_closeout_sha256": SOURCE_CLOSE_SHA,
        "original_campaign_id": campaign["campaign_id"],
        "original_roster_id": roster["roster_id"],
        "distinct_original_source_decisions": 3,
        "correlated_price_horizon_observations": 12,
        "actually_independent_broker_trade_samples": 0,
        "original_model_predictions": 0,
        "source_windows": checked_windows,
        "max_absolute_hindsight_midprice_bps": str(max(values)),
        "original_hypothetical_cost_plus_margin_bps": str(EXPENSE_STRESS_BPS),
        "cells_beating_hypothetical_cost_hurdle": 0,
        "no_trade_reference_net_usd": "0",
        "broker_fills_attested": False,
        "actual_slippage_attested": False,
        "after_cost_trading_alpha_attested": False,
        "canonical_owner_enrolled": False,
        "new_forecasts_created": 0,
        "model_promoted": False,
        "broker_queries_sent": 0,
        "broker_orders_sent": 0,
        "trade_authority": False,
    }
    return report


def main():
    ap = argparse.ArgumentParser()
    for item in ("source-copy", "original-root", "period", "evidence-root", "output"):
        ap.add_argument("--" + item, required=True)
    args = ap.parse_args()
    start = time.monotonic()
    result = verify_copied_three_by_four(
        args.source_copy, args.original_root, args.period, args.evidence_root)
    result["actual_review_monotonic_wall_ms"] = round(
        (time.monotonic()-start)*1000, 3)
    output = Path(args.output)
    require(not output.is_symlink() and not output.exists(),
            "INDEPENDENT_CREATE_ONLY_PRIVATE_RECEIPT")
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    require(
        output.parent.stat().st_mode & 0o077 == 0,
        "INDEPENDENT_EVIDENCE_DIRECTORY_NOT_PRIVATE")
    raw = canonical(result) + b"\n"
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as file:
        file.write(raw)
        file.flush()
        os.fsync(file.fileno())
    directory_fd = os.open(output.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    print(json.dumps({
        "state": result["state"],
        "segments": result["current_copied_sealed_segments"],
        "archived_pages": result["archive_page_count"],
        "frozen_original_decisions": result["distinct_original_source_decisions"],
        "correlated_price_labels": result["correlated_price_horizon_observations"],
        "maximum_abs_hindsight_bps": result["max_absolute_hindsight_midprice_bps"],
        "cost_hurdle_bps": result["original_hypothetical_cost_plus_margin_bps"],
        "cells_beating_hurdle": result["cells_beating_hypothetical_cost_hurdle"],
        "executed_trades": result["actually_independent_broker_trade_samples"],
        "review_wall_ms": result["actual_review_monotonic_wall_ms"],
        "sha256": sha(raw),
    }, sort_keys=True))


if __name__ == "__main__":
    main()
