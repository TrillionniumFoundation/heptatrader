"""Finite immutable PRICE-ONLY original cohort audit; no model/effect authority.

Audits all frozen future slots and horizons without modifying a single input
or label. Existing immutable price labels are independently rederived from
sealed original PAPER segments; missing/late inputs cannot become zero PnL.
"""
from __future__ import annotations

import argparse
from collections import Counter
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import time

from hepta_decision_attribution import read_private, strict
from historical_training.common import sha
from .contracts import ContractError, digest
from .original_multihorizon import HORIZONS_MIN, ordered_quote_index
from .postmodel_requote import load_original_quote_archive
from .prospective_multihorizon import (
    SLOT_COUNT, LAST_CAPTURE_DELAY_MS, COST_AND_MARGIN_BPS, _load,
)
from .prospective_horizon_observe import MIN_OUTCOME_GRACE_MS
from .scheduled_horizon import load_roster
from .sealed_outcome_reconcile import (
    verify_input_and_timer, verify_immutable_outcome,
)

SCHEMA = "heptatrader.train-v2-original-price-cohort-audit.v1"
HURDLE = Decimal(COST_AND_MARGIN_BPS)


def _require(ok, code):
    if not ok:
        raise ContractError(code)


def audit(root, *, now_ms=None, archive_loader=load_original_quote_archive):
    """Bounded 3-slot, 4-horizon source/price audit; never write outcomes."""
    root = Path(root)
    campaign = _load(root)
    roster_file = root / "timers-roster.json"
    roster = load_roster(roster_file)
    _require(roster["campaign_id"] == campaign["campaign_id"],
             "COHORT_AUDIT_ROSTER_SCOPE")
    now = time.time_ns() // 1000000 if now_ms is None else now_ms
    _require(type(now) is int and now > 0 and now >= campaign["created_at_ms"],
             "COHORT_AUDIT_CLOCK")
    archive = None
    index = None
    slots = []
    counter = Counter()
    for slot in range(SLOT_COUNT):
        anchor = campaign["anchors_ms"][slot]
        input_path = root / ("slot-%02d.input.json" % slot)
        timer_path = root / ("slot-%02d.timer-result.json" % slot)
        _require(not input_path.is_symlink() and not timer_path.is_symlink(),
                 "COHORT_AUDIT_SYMLINK_INPUT")
        input_exists, timer_exists = input_path.is_file(), timer_path.is_file()
        _require(input_exists == timer_exists, "COHORT_AUDIT_PARTIAL_CAPTURE")
        row = {
            "slot": slot, "anchor_ms": anchor,
            "frozen_input_deadline_ms": anchor + LAST_CAPTURE_DELAY_MS,
            "original_input_sealed": input_exists,
            "outcomes": [],
        }
        if not input_exists:
            state = ("FUTURE_OR_OPEN_SOURCE_SLOT" if
                     now <= anchor + LAST_CAPTURE_DELAY_MS
                     else "MISSED_NO_CAPTURE_NOT_A_PRICE_OR_ZERO_PNL")
            row["slot_state"] = state
            for horizon in HORIZONS_MIN:
                key = "NO_SOURCE_INPUT_NO_PRICE_OUTCOME"
                row["outcomes"].append({
                    "horizon_min": horizon, "state": key,
                    "price_bps": None, "economically_qualified": False})
                counter[key] += 1
            slots.append(row)
            continue
        frozen, _ = verify_input_and_timer(root, slot, campaign, roster)
        row["slot_state"] = "ORIGINAL_PREOUTCOME_INPUT_SEALED"
        row["original_input_sha256"] = sha(input_path)
        row["timer_result_sha256"] = sha(timer_path)
        row["source_decision_at_ms"] = frozen["decision_at_ms"]
        if archive is None:
            archive = archive_loader(campaign["source_root"],
                                     campaign["period_sha256"])
            index = ordered_quote_index(archive["rows"])
        matching = [q for q in index["known"]
                    if q[3] == frozen["original_source_id"]]
        for horizon in HORIZONS_MIN:
            due = frozen["outcome_maturity_ms"].get(str(horizon))
            _require(type(due) is int
                     and due == frozen["decision_at_ms"] + horizon * 60000,
                     "COHORT_AUDIT_HORIZON_MATURITY_CHANGED")
            output = root / ("slot-%02d-h%02d.outcome.json" % (slot,horizon))
            _require(not output.is_symlink(), "COHORT_AUDIT_OUTCOME_SYMLINK")
            result = {
                "horizon_min": horizon, "due_ms": due,
                "price_bps": None, "observed_price_below_stress_hurdle": None,
                "economically_qualified": False,
            }
            if now < due + MIN_OUTCOME_GRACE_MS:
                _require(not output.exists(),
                         "COHORT_AUDIT_OUTCOME_BEFORE_MATURITY")
                result["state"] = "PENDING_NOT_MATURE"
            elif not matching:
                _require(not output.exists(), "COHORT_AUDIT_SOURCE_SEAL_MISSING")
                result["state"] = "WAIT_ORIGINAL_INPUT_SEGMENT_SEAL"
            elif index["known_times"][-1] < due + MIN_OUTCOME_GRACE_MS:
                _require(not output.exists(), "COHORT_AUDIT_FUTURE_UNSEALED")
                result["state"] = "WAIT_ORIGINAL_SEALED_ENDPOINT_COVERAGE"
            else:
                _require(len(matching) == 1,
                         "COHORT_AUDIT_DUPLICATE_ORIGINAL_SOURCE")
                from .prospective_horizon_observe import make_result
                expected = make_result(campaign, frozen, matching[0],
                                       horizon, index)
                if not output.exists():
                    result["state"] = "READY_ONLY_NOT_YET_SEALED"
                    result["ready_original_price_state"] = expected["outcome_state"]
                else:
                    receipt = strict(read_private(output, 128 * 1024))
                    _require(type(receipt.get("observed_at_ms")) is int
                             and receipt["observed_at_ms"] <= now,
                             "COHORT_AUDIT_OUTCOME_OBSERVED_IN_FUTURE")
                    status = verify_immutable_outcome(
                        receipt, expected, due=due, archive=archive)
                    result["state"] = status
                    result["immutable_outcome_sha256"] = sha(output)
                    if (status == "VERIFIED_OBSERVED_ORIGINAL_PRICE_ONLY"):
                        number = Decimal(expected["original_midprice_change_bps"])
                        _require(number.is_finite(), "COHORT_AUDIT_NONFINITE_PRICE")
                        result["price_bps"] = str(number)
                        result["observed_price_below_stress_hurdle"] = abs(number) < HURDLE
            counter[result["state"]] += 1
            row["outcomes"].append(result)
        slots.append(row)
    _require(len(slots) == 3 and sum(len(x["outcomes"]) for x in slots) == 12,
             "COHORT_AUDIT_DECLARED_DENOMINATOR_CHANGED")
    body = {
        "schema": SCHEMA,
        "campaign_id": campaign["campaign_id"],
        "roster_id": roster["roster_id"],
        "actual_read_at_ms": now,
        "campaign_sha256": sha(root/"campaign.json"),
        "roster_sha256": sha(roster_file),
        "review_code_sha256": {
            name: sha(Path(__file__).with_name(name))
            for name in (
                "price_only_cohort_audit.py",
                "sealed_outcome_reconcile.py",
                "prospective_horizon_observe.py",
                "original_multihorizon.py",
                "scheduled_horizon.py",
            )
        },
        "horizons_min": list(HORIZONS_MIN),
        "declared_source_slots": SLOT_COUNT,
        "declared_source_horizon_cells": 12,
        "fixed_frozen_stressed_cost_plus_margin_bps": str(HURDLE),
        "all_slots": slots,
        "state_counts": dict(sorted(counter.items())),
        "original_sealed_archive_fingerprint_sha256": (
            archive["source_fingerprint_sha256"] if archive else None),
        "original_sealed_segment_count": (
            archive["sealed_segments"] if archive else 0),
        "original_model_forecast_count": 0,
        "signed_independent_observer_count": 0,
        "executed_strategy_trades_count": 0,
        "no_trade_reference_net_pnl_usd": "0",
        "true_behavior_propensity_attested": False,
        "broker_fills_and_slippage_attested": False,
        "financial_alpha_qualified": False,
        "automatic_model_selection": False,
        "broker_queries_sent": 0,
        "broker_orders_sent": 0,
        "trade_authority": False,
    }
    return {**body, "audit_id": digest(body)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    receipt = audit(args.root)
    path = args.output
    _require(not path.exists() and not path.is_symlink(),
             "COHORT_AUDIT_NEW_IMMUTABLE_OUTPUT_REQUIRED")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    raw = (json.dumps(receipt, sort_keys=True, indent=2) + "\n").encode()
    fd = os.open(path, os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,"wb") as sink:
        sink.write(raw);sink.flush();os.fsync(sink.fileno())
    parent = os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(parent)
    finally:os.close(parent)
    print(json.dumps({
        "schema": receipt["schema"], "state_counts":receipt["state_counts"],
        "declared_cells":12, "financial_alpha_qualified":False,
        "model_predictions":0, "broker_orders_sent":0,
        "receipt_sha256":hashlib.sha256(raw).hexdigest()},sort_keys=True))


if __name__=="__main__":
    main()
