"""Multi-horizon ORIGINAL PAPER quote economic capacity, retrospective only.

No model fit, no broker connection, no strategy selection. Outcome/price labels
are never consulted when choosing the fixed input-time cohorts.
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import Counter
from decimal import Decimal
from hashlib import sha256
import argparse
import json
import os
from pathlib import Path

from hepta_decision_attribution import read_private, strict
from historical_training.common import sha
from .contracts import ContractError
from .economics import verify_cost_receipt
from .postmodel_requote import load_original_quote_archive

SCHEMA = "heptatrader.fx-original-multihorizon-price-capacity.v1"
HORIZONS_MIN = (5, 15, 30, 60)
GRID_MS = 300_000
DECISION_LAG_MS = 1000
MAX_ENDPOINT_DELAY_MS = 5000
MAX_PATH_GAP_MS = 30_000
MIN_DISJOINT_SPACING_MS = 80 * 60_000
DISJOINT_EMBARGO_MS = 300_000
STRESS_SLIPPAGE_ROUNDTRIP_BPS = Decimal("4")
SAFETY_MARGIN_BPS = Decimal("2")
PINNED_COST_SHA256 = "12b251c4b6226abe501ff2a044e0618b59e6ab88170caed0db80387442a79e8c"
# Original sealed-source fingerprint is pinned AFTER exploratory discovery.
# This remains an inspected development population, NOT an independent OOS.
PINNED_ORIGINAL_SEALED_SOURCE = "f2a878bd6c80397c9d39af233d44482f3a5c861b90c6bb1681911d9e8864cf23"
RESEARCH_SAMPLE_MINIMUM = 1024
MAX_QUOTE_ROWS = 200_000
MAX_GRID_WINDOWS = 4096


def fail(condition, reason):
    if not condition:
        raise ContractError(reason)


def price(value):
    if isinstance(value, bool):
        raise ContractError("MULTIHORIZON_PRICE_BOOLEAN")
    try:
        d = Decimal(str(value))
    except Exception as exc:
        raise ContractError("MULTIHORIZON_PRICE_INVALID") from exc
    fail(d.is_finite() and Decimal(0) < d < Decimal(1000000),
         "MULTIHORIZON_PRICE_NOT_FINITE_OR_POSITIVE")
    return d


def ordered_quote_index(rows):
    """Strict as-known-at source and a separately chronological outcome index."""
    fail(isinstance(rows, list) and 6 <= len(rows) <= MAX_QUOTE_ROWS,
         "MULTIHORIZON_SOURCE_ROW_BOUND")
    available_index = []
    by_event = {}
    seen = set()
    for row in rows:
        fail(isinstance(row, (tuple, list)) and len(row) == 5,
             "MULTIHORIZON_SOURCE_SCHEMA")
        avail, event, expiry, original_id, mid = row
        fail(all(type(x) is int for x in (avail, event, expiry))
             and 0 < event <= avail <= expiry <= event + 5000,
             "MULTIHORIZON_SOURCE_TIME_OR_TTL")
        fail(isinstance(original_id, str) and len(original_id) == 64
             and all(c in "0123456789abcdef" for c in original_id)
             and original_id not in seen,
             "MULTIHORIZON_SOURCE_IDENTITY")
        seen.add(original_id)
        mid = price(mid)
        if event in by_event:
            fail(by_event[event][4] == mid,
                 "MULTIHORIZON_DUPLICATE_EVENT_PRICE_CONFLICT")
            # Same event with unchanged MID may recur locally; preserve the
            # earliest locally knowable endpoint (not a later backfill).
            if avail < by_event[event][0]:
                by_event[event] = (avail, event, expiry, original_id, mid)
        else:
            by_event[event] = (avail, event, expiry, original_id, mid)
        available_index.append((avail, event, expiry, original_id, mid))
    fail(all(available_index[i][0] <= available_index[i+1][0]
             for i in range(len(available_index)-1)),
         "MULTIHORIZON_SOURCE_AVAILABILITY_UNSORTED")
    events = sorted(by_event.values(), key=lambda x: x[1])
    event_times = [x[1] for x in events]
    gap_count = [0]
    for left, right in zip(events, events[1:]):
        gap_count.append(gap_count[-1] +
                         (right[1]-left[1] > MAX_PATH_GAP_MS))
    return {
        "known": available_index,
        "known_times": [x[0] for x in available_index],
        "events": events,
        "event_times": event_times,
        "gap_prefix": gap_count,
    }


def grid_schedule(index):
    known = index["known_times"]
    first = (known[0] // GRID_MS + 1) * GRID_MS
    last = (known[-1] // GRID_MS) * GRID_MS
    times = list(range(first, last + 1, GRID_MS))
    fail(0 < len(times) <= MAX_GRID_WINDOWS,
         "MULTIHORIZON_GRID_POPULATION_BOUND")
    return times


def input_asof(index, anchor):
    decision = anchor + DECISION_LAG_MS
    i = bisect_right(index["known_times"], decision) - 1
    if i < 0:
        return "INPUT_NOT_AVAILABLE", None
    observation = index["known"][i]
    known, event, expiry, _, _ = observation
    if not event <= known <= decision:
        return "INPUT_FUTURE_OR_INVALID", None
    if decision > expiry:
        return "INPUT_QUOTE_EXPIRED", None
    return "INPUT_CAUSAL", observation


def label_after(index, input_quote, anchor, horizon_ms):
    due = anchor + horizon_ms
    events, times = index["events"], index["event_times"]
    if due > times[-1]:
        return "CENSORED_BEYOND_SEALED_SOURCE", None
    right = bisect_left(times, due)
    if right >= len(events) or events[right][1] > due + MAX_ENDPOINT_DELAY_MS:
        return "CENSORED_ENDPOINT_NOT_OBSERVED", None
    observed = events[right]
    if observed[0] > index["known_times"][-1]:
        return "CENSORED_OUTCOME_NOT_KNOWABLE", None
    left = bisect_left(times, input_quote[1])
    if (left >= len(events) or events[left][1] != input_quote[1]
            or right < left):
        raise ContractError("MULTIHORIZON_SOURCE_CROSS_INDEX_MISSING")
    if index["gap_prefix"][right] != index["gap_prefix"][left]:
        return "CENSORED_INTERVENING_SOURCE_GAP", None
    return "OBSERVED_RETROSPECTIVE_PRICE_ONLY", (
        (observed[4]/input_quote[4] - Decimal(1)) * Decimal(10000)
    )


def percentile(values, pct):
    fail(values and 0 <= pct <= 100, "MULTIHORIZON_PERCENTILE_EMPTY")
    sorted_values = sorted(values)
    # Nearest rank is deterministic and is not a statistical confidence bound.
    ix = max(0, (len(values)*pct + 99)//100 - 1)
    return str(sorted_values[ix])


def price_stats(values, hurdle):
    if not values:
        return {"observed_rows": 0, "hindsight_move_clears_cost_count": 0,
                "hindsight_move_clears_cost_plus_margin_count": 0,
                "upper_bound_proves_executable_edge": False}
    abs_moves = [abs(v) for v in values]
    return {
        "observed_rows": len(values),
        "abs_original_midpoint_move_bps": {
            "p50": percentile(abs_moves, 50),
            "p90": percentile(abs_moves, 90),
            "p95": percentile(abs_moves, 95),
            "p99": percentile(abs_moves, 99),
            "max": str(max(abs_moves)),
        },
        "hindsight_move_clears_cost_count":
            sum(x > hurdle-SAFETY_MARGIN_BPS for x in abs_moves),
        "hindsight_move_clears_cost_plus_margin_count":
            sum(x > hurdle for x in abs_moves),
        "hindsight_oracle_is_trade_policy": False,
        "upper_bound_proves_executable_edge": False,
    }


def assess_horizon(index, anchors, horizon_ms, stressed_cost_plus_margin):
    fail(type(horizon_ms) is int and horizon_ms in
         [minutes*60_000 for minutes in HORIZONS_MIN],
         "MULTIHORIZON_UNREGISTERED_HORIZON")
    eligibility = []
    denied = Counter()
    for anchor in anchors:
        status, quote = input_asof(index, anchor)
        if quote is None:
            denied[status] += 1
        else:
            eligibility.append((anchor, quote))
    # The input-only selector is frozen BEFORE reading *any* outcome prices.
    # All source/endpoint censoring remains inside the selected denominator.
    spacing = max(MIN_DISJOINT_SPACING_MS, horizon_ms+DISJOINT_EMBARGO_MS)
    selected = set()
    last = -10**30
    for anchor, quote in eligibility:
        if anchor >= last+spacing:
            selected.add(anchor)
            last = anchor
    all_status, selected_status = Counter(), Counter()
    all_returns, selected_returns = [], []
    for anchor, quote in eligibility:
        state, realized = label_after(index, quote, anchor, horizon_ms)
        all_status[state] += 1
        if realized is not None:
            all_returns.append(realized)
        if anchor in selected:
            selected_status[state] += 1
            if realized is not None:
                selected_returns.append(realized)
    fail(sum(all_status.values()) == len(eligibility)
         and sum(selected_status.values()) == len(selected),
         "MULTIHORIZON_COHORT_DENOMINATOR_LOST")
    return {
        "horizon_minutes": horizon_ms//60_000,
        "fixed_grid_anchor_count": len(anchors),
        "input_at_decision_eligible": len(eligibility),
        "input_rejection_reasons": dict(sorted(denied.items())),
        "input_only_time_disjoint_spacing_ms": spacing,
        "prelabel_time_disjoint_selected": len(selected),
        "all_eligible_outcome_status": dict(sorted(all_status.items())),
        "time_disjoint_outcome_status": dict(sorted(selected_status.items())),
        "all_eligible_covered_price_stats":
            price_stats(all_returns, stressed_cost_plus_margin),
        "time_disjoint_covered_price_stats":
            price_stats(selected_returns, stressed_cost_plus_margin),
        "price_labels_used_to_select_windows": False,
        "outcome_censoring_imputed_as_zero": False,
        "independent_prospective_test": False,
        "qualified_research_fit_rows": 0,
        "price_only_model_training_permitted": False,
    }


def original_multi_horizon_summary(archive, cost, cost_sha, period):
    fail(archive.get("sealed_segments", 0) > 0
         and archive.get("verified_original_bytes", 0) > 0
         and archive.get("numeric_segments_total", 0) <= 320,
         "MULTIHORIZON_UNQUALIFIED_SOURCE_ARCHIVE")
    fail(archive.get("source_fingerprint_sha256") ==
         PINNED_ORIGINAL_SEALED_SOURCE,
         "MULTIHORIZON_FROZEN_SEALED_SOURCE_CHANGED")
    fail(cost_sha == PINNED_COST_SHA256,
         "MULTIHORIZON_FROZEN_ORIGINAL_COST_CHANGED")
    spreads, commissions = verify_cost_receipt(cost, period)
    # Do not tune this stress against observed outcomes. It is EXACTLY the
    # predeclared threshold used by the old, held 15-minute Ridge plan.
    p99_spread, p90_fee = spreads[2], commissions[1]
    base = p99_spread + 2*p90_fee + STRESS_SLIPPAGE_ROUNDTRIP_BPS
    hurdle = base + SAFETY_MARGIN_BPS
    fail(base == Decimal("8.9133557")
         and hurdle == Decimal("10.9133557"),
         "MULTIHORIZON_FROZEN_COST_HURDLE_CHANGED")
    index = ordered_quote_index(archive["rows"])
    anchors = grid_schedule(index)
    results = [assess_horizon(
        index, anchors, h*60_000, hurdle) for h in HORIZONS_MIN]
    return {
        "schema": SCHEMA,
        "original_paper_period_sha256": period,
        "original_quote_archive_fingerprint":
            archive["source_fingerprint_sha256"],
        "original_verified_quote_count": len(index["known"]),
        "original_verified_event_times": len(index["events"]),
        "original_sealed_numeric_segments": archive["sealed_segments"],
        "original_numeric_unsealed_newest_excluded":
            archive["unsealed_newest_excluded"],
        # Upstream scanner's byte budget includes the excluded, currently
        # UNSEALED newest segment. It is not an eligible source population.
        "scanned_source_bytes_including_excluded_unsealed_newest":
            archive["verified_original_bytes"],
        "pin_applies_only_to_sealed_source_fingerprint": True,
        "first_known_at_ms": index["known_times"][0],
        "last_known_at_ms": index["known_times"][-1],
        "research_cohort_description":
            "FIXED_UTC_5MIN_GRID_1S_DECISION_ASOF_FIRST_OBSERVED_ENDPOINT",
        "horizon_minutes_frozen_not_optimized": list(HORIZONS_MIN),
        "endpoint_event_time_tolerance_ms": MAX_ENDPOINT_DELAY_MS,
        "path_max_gap_ms": MAX_PATH_GAP_MS,
        "historical_selected_nonoverlap_80min_floor_ms":
            MIN_DISJOINT_SPACING_MS,
        "frozen_cost_receipt_sha256": cost_sha,
        "historical_p99_full_spread_bps": str(p99_spread),
        "historical_p90_oneway_commission_bps": str(p90_fee),
        "hypothetical_slippage_roundtrip_bps":
            str(STRESS_SLIPPAGE_ROUNDTRIP_BPS),
        "extra_safety_margin_bps": str(SAFETY_MARGIN_BPS),
        "roundtrip_cost_proxy_bps": str(base),
        "fixed_cost_plus_margin_bps": str(hurdle),
        "horizons": results,
        "one_original_15min_model_retrained_or_reused": False,
        "forecast_model_evaluated": False,
        "price_direction_forecast_attested": False,
        "same_source_live_train_inference_parity_proven": False,
        "actual_execution_slippage_attested": False,
        "actual_broker_fill_at_midpoint_attested": False,
        "independent_forward_sample_attested": False,
        "all_account_net_pnl_attested": False,
        "new_model_fit_allowed": False,
        "original_trader_or_source_modified": False,
        "broker_requests_sent": 0,
        "broker_orders_sent": 0,
        "automatic_promotion": False,
        "trade_authority": False,
        "interpretation":
            "RETROSPECTIVE_PRICE_MOVEMENT_HINDSIGHT_DIRECTIONAL_CAPACITY_ONLY",
    }


def run(*, source_root, period_file, cost_file, output_file):
    period = sha(period_file)
    cost_raw = read_private(cost_file, 512*1024)
    cost = strict(cost_raw)
    archive = load_original_quote_archive(source_root, period)
    report = original_multi_horizon_summary(
        archive, cost, sha256(cost_raw).hexdigest(), period)
    report["source_code_sha256"] = sha(Path(__file__))
    report["source_reader_sha256"] = sha(
        Path(__file__).with_name("postmodel_requote.py"))
    report["cost_validator_sha256"] = sha(
        Path(__file__).with_name("economics.py"))
    path = Path(output_file)
    fail(path.is_absolute() and not path.is_symlink()
         and not path.exists(), "MULTIHORIZON_OUTPUT_CREATE_ONLY")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fail(path.parent.is_dir() and not path.parent.is_symlink()
         and path.parent.stat().st_uid == os.geteuid()
         and path.parent.stat().st_mode & 0o077 == 0,
         "MULTIHORIZON_PRIVATE_OUTPUT_REQUIRED")
    data = (json.dumps(report, sort_keys=True, indent=2)+"\n").encode()
    fd = os.open(path, os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    dir_fd = os.open(path.parent, os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)
    return report


def main():
    cli = argparse.ArgumentParser()
    for key in ("source-root", "period", "cost", "output"):
        cli.add_argument("--"+key, type=Path, required=True)
    args = cli.parse_args()
    result = run(
        source_root=args.source_root, period_file=args.period,
        cost_file=args.cost, output_file=args.output)
    print(json.dumps({
        "status": "RETROSPECTIVE_NO_TRAIN_OR_TRADE",
        "sampled_horizons_min": list(HORIZONS_MIN),
        "input_time_eligible": [r["input_at_decision_eligible"]
                                for r in result["horizons"]],
        "prelabel_disjoint_count": [r["prelabel_time_disjoint_selected"]
                                    for r in result["horizons"]],
        "observed_disjoint": [r["time_disjoint_covered_price_stats"]["observed_rows"]
                              for r in result["horizons"]],
        "original_verified_quote_count": result["original_verified_quote_count"],
        "model_fit_allowed": False,
        "broker_orders_sent": 0,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
