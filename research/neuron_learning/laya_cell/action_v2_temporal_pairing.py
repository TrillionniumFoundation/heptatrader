"""Research-only causal time-matched Action V2 pair selection.

Select a *subset* of already-mature, genuinely labelled frozen training rows.
No data collection, optimizer, order, future evaluation, or production writer.
Avoid pairing an ACT from Friday with the earliest ESCALATE from Wednesday.
The source artifact and profile must already be immutable and verified.
"""
from __future__ import annotations

from .integrity import ContractError
from .resident_action_v2_research import (
    MIN_EACH_LABEL, _validated_rows, stratified_action_pairs,
)
from .resident_action_v2_closed_source import check_train_source

MIN_SPACING_MS = 300000   # fixed five-minute outcome windows must not overlap
MAX_LAG_MS = 3600000      # within a local UTC-hour of the original ACT example


def _windows(min_spacing_ms, max_lag_ms):
    if (type(min_spacing_ms) is not int or min_spacing_ms < 300000 or
            type(max_lag_ms) is not int or not 300000 <= max_lag_ms <= 86400000):
        raise ContractError("V2_TEMPORAL_PAIR_WINDOW")


def _unsupported(rows):
    n=sum(r.get("parent_correct") is True for r in rows)
    return {"pairs": [], "eligible": False, "original_act_rows": n,
            "skipped_act_rows": n, "max_pair_gap_ms": None,
            "reason": "NO_GENUINE_TWO_CLASS_PROFILE",
            "training_only": True, "model_or_trade_permission": False}


def time_matched_action_pairs(rows, profile, *,
                              min_spacing_ms=MIN_SPACING_MS,
                              max_lag_ms=MAX_LAG_MS):
    """Greedy same-UTC-day 1:1 matching from an exact frozen training profile.

    Does not claim independent future efficacy; every row and label remains
    immutable under the existing action-v2 profile digest.
    """
    _windows(min_spacing_ms, max_lag_ms)
    if not stratified_action_pairs(rows, profile):
        return _unsupported(rows)
    return _match(rows, min_spacing_ms, max_lag_ms)


def source_time_matched_action_pairs(artifact, *,
                                     min_spacing_ms=MIN_SPACING_MS,
                                     max_lag_ms=MAX_LAG_MS):
    """Audit original SHA-pinned CLOSED source WITHOUT fabricating parent logits.

    This checks source custody and studies matching only; it does not
    construct a trainable profile or fit any model.
    """
    _windows(min_spacing_ms, max_lag_ms)
    m=check_train_source(artifact)
    rows=_validated_rows(artifact["rows"],cutoff_ms=m["training_cutoff_ms"])
    if not m["action_training_two_class_supported"]:
        return _unsupported(rows)
    return _match(rows, min_spacing_ms, max_lag_ms)


def _match(rows, min_spacing_ms, max_lag_ms):
    positive = sorted(
        (i for i, r in enumerate(rows) if r["parent_correct"] is True),
        key=lambda i: (rows[i]["anchor_ms"], rows[i]["row_id"])
    )
    negative = [i for i, r in enumerate(rows) if r["parent_correct"] is False]
    selected = set()
    used_times = []
    pairs = []
    for ai in positive:
        anchor = rows[ai]["anchor_ms"]
        if any(abs(anchor - t) < min_spacing_ms for t in used_times):
            continue
        day = anchor // 86400000  # UTC calendar date, never host-local TZ
        options = [
            ni for ni in negative
            if ni not in selected
            and rows[ni]["anchor_ms"] // 86400000 == day
            and min_spacing_ms <= abs(rows[ni]["anchor_ms"] - anchor) <= max_lag_ms
            and all(abs(rows[ni]["anchor_ms"] - t) >= min_spacing_ms
                    for t in used_times)
        ]
        if not options:
            continue
        ni = min(options, key=lambda j: (
            abs(rows[j]["anchor_ms"] - anchor), rows[j]["row_id"]))
        # Strictly disjoint indices and fixed-horizon anchors; no synthetic
        # oversampling or fractional learning credit.
        pairs.append((ai, ni))
        selected.add(ai)
        selected.add(ni)
        used_times.extend((anchor, rows[ni]["anchor_ms"]))
    gaps = [abs(rows[i]["anchor_ms"]-rows[j]["anchor_ms"]) for i, j in pairs]
    return {
        "pairs": pairs if len(pairs) >= MIN_EACH_LABEL else [],
        "eligible": len(pairs) >= MIN_EACH_LABEL,
        "original_act_rows": len(positive),
        "skipped_act_rows": len(positive) - len(pairs),
        "max_pair_gap_ms": max(gaps) if gaps else None,
        "min_spacing_ms": min_spacing_ms,
        "max_lag_ms": max_lag_ms,
        "reason": None if len(pairs) >= MIN_EACH_LABEL
                  else "INSUFFICIENT_NONOVERLAPPING_SAME_DAY_PAIRS",
        "training_only": True,
        "model_or_trade_permission": False,
    }
