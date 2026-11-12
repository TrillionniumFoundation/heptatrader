"""Research-only time-disjoint selection from previously verified Laya rows.

Input rows MUST already come from a SHA-checked original PAPER dataset through
rows_for_training. This module neither authenticates that source independently
nor mutates the resident scheduler, worker, frozen plans, or any Broker route.
"""
from __future__ import annotations

import re
from collections import Counter

from .integrity import ContractError, digest
from .resident_row_accounting import row_id

SCHEMA = "heptatrader.laya-temporal-row-study.v1"
HORIZON_SECONDS = 300
MIN_ROWS = 16
MAX_ROWS = 32
MAX_INPUT_ROWS = 4096
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")


def select_recent_nonoverlap(
    rows, *, training_cutoff_ms, dataset_sha256, minimum_rows=MIN_ROWS,
    maximum_rows=MAX_ROWS
):
    """Return an auditable research-only selection and untouched row objects.

    Only five-minute horizon rows participate. Select the most recent eligible
    anchor first; enforce >= 300,000 ms separation, then restore chronological
    order. Never optimize the choice using the observed target class, training
    loss, future trial outcomes, or the current model's predicted class.

    Row uniqueness is NOT independence; even nonoverlapping windows can share
    sessions, features, volatility regimes and economically dependent paths.
    """
    if (type(training_cutoff_ms) is not int or training_cutoff_ms <= 0
            or type(dataset_sha256) is not str or
            _HEX64.fullmatch(dataset_sha256) is None
            or type(minimum_rows) is not int or
            type(maximum_rows) is not int or
            not MIN_ROWS <= minimum_rows <= maximum_rows <= MAX_ROWS
            or not isinstance(rows, list) or len(rows) > MAX_INPUT_ROWS):
        raise ContractError("TEMPORAL_STUDY_INVALID_BOUNDARY")

    eligible = []
    seen_ids = set()
    seen_anchors = set()
    for row in rows:
        if type(row) is not dict:
            raise ContractError("TEMPORAL_STUDY_ROW_SHAPE")
        horizon = row.get("horizon_sec")
        if type(horizon) is not int or horizon not in (300, 900):
            raise ContractError("TEMPORAL_STUDY_HORIZON")
        if horizon != HORIZON_SECONDS:
            continue
        anchor = row.get("anchor_ms")
        target = row.get("target_class")
        receipt = row.get("outcome_receipt_digest")
        if (type(anchor) is not int or anchor <= 0
                or anchor + HORIZON_SECONDS * 1000 > training_cutoff_ms
                or type(target) is not int or not 0 <= target <= 6
                or type(receipt) is not str or
                _HEX64.fullmatch(receipt) is None
                or type(row.get("sample_id")) is not str or
                _HEX64.fullmatch(row["sample_id"]) is None):
            raise ContractError("TEMPORAL_STUDY_UNMATURED_OR_INVALID_ROW")
        identity = row_id(row)
        if identity in seen_ids or anchor in seen_anchors:
            raise ContractError("TEMPORAL_STUDY_DUPLICATE_ROW_OR_ANCHOR")
        seen_ids.add(identity)
        seen_anchors.add(anchor)
        eligible.append((anchor, identity, row))

    eligible.sort(key=lambda t: (t[0], t[1]))
    recent_first = []
    for anchor, identity, row in reversed(eligible):
        if not recent_first or recent_first[-1][0] - anchor >= 300000:
            recent_first.append((anchor, identity, row))
        if len(recent_first) >= maximum_rows:
            break
    picked = list(reversed(recent_first))
    if any(b[0] - a[0] < 300000 for a, b in zip(picked, picked[1:])):
        raise ContractError("TEMPORAL_STUDY_INTERNAL_OVERLAP")

    selected_rows = [r for _, _, r in picked]
    classes = Counter(r["target_class"] for r in selected_rows)
    qualified_count = len(picked) >= minimum_rows
    result = {
        "schema": SCHEMA,
        "dataset_sha256_claim": dataset_sha256,
        "dataset_sha256_must_be_verified_by_caller": True,
        "training_cutoff_ms": training_cutoff_ms,
        "horizon_seconds": HORIZON_SECONDS,
        "minimum_gap_ms": 300000,
        "selection_policy": "RECENT_FIRST_CHRONOLOGICAL_TARGET_BLIND",
        "eligible_five_minute_rows": len(eligible),
        "population_row_ids_digest": digest(sorted(seen_ids)),
        "selected_row_ids": [identity for _, identity, _ in picked],
        "selected_rows": len(picked),
        "selected_target_class_counts_diagnostic_only":
            {str(k): classes[k] for k in range(7)},
        "first_anchor_ms": picked[0][0] if picked else None,
        "last_anchor_ms": picked[-1][0] if picked else None,
        "has_minimum_temporal_coverage": qualified_count,
        "state": ("RESEARCH_ONLY_ROW_SELECTION_AVAILABLE" if qualified_count
                  else "INSUFFICIENT_NONOVERLAPPING_WINDOWS_NO_TRAINING"),
        "statistical_independence_claim": False,
        "trading_authority": False,
        "model_promoted": False,
        "training_started": False,
        "resident_v1_modified": False,
    }
    result["audit_id"] = digest(result)
    return result, selected_rows
