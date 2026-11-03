"""Label-maturity-aware chronological partitions, frozen before any model fit."""
from __future__ import annotations

from collections import Counter
import math

from .data import need, validate_dataset
from research.neuron_learning.laya_cell.integrity import digest

SCHEMA = "heptatrader.model-replacement-split.v1"
PARTITIONS = ("train", "validation", "calibration", "test")


def coverage(rows):
    if not rows:
        return {"rows": 0, "class_counts": [0] * 7}
    times = [r["anchor_ms"] for r in rows]
    intervals = sorted((r["anchor_ms"], r["outcome_observed_at_ms"]) for r in rows)
    total, start, end = 0, *intervals[0]
    for left, right in intervals[1:]:
        if left > end:
            total += end - start
            start, end = left, right
        else:
            end = max(end, right)
    total += end - start
    counts = Counter(r["target_class"] for r in rows)
    return {"rows": len(rows), "unique_inputs": len({r["sample_id"] for r in rows}),
            "first_anchor_ms": min(times), "last_anchor_ms": max(times),
            "latest_label_available_ms": max(r["outcome_available_at_ms"] for r in rows),
            "anchor_span_ms": max(times) - min(times), "label_interval_union_ms": total,
            "distinct_utc_hours": len({t // 3600000 for t in times}),
            "distinct_utc_days": len({t // 86400000 for t in times}),
            "class_counts": [counts[i] for i in range(7)],
            "independent_sample_count": None,
            "independence_note": "Adjacent label intervals may overlap within each partition"}


def build_split(dataset, fractions=(.55, .15, .15), embargo_ms=0, minimum_rows=8):
    validate_dataset(dataset)
    need(len(fractions) == 3 and all(math.isfinite(f) and 0 < f < 1 for f in fractions)
         and sum(fractions) < 1, "SPLIT_FRACTIONS")
    need(type(embargo_ms) is int and embargo_ms >= 0, "SPLIT_EMBARGO")
    need(type(minimum_rows) is int and minimum_rows >= 1, "SPLIT_MINIMUM")
    rows = dataset["rows"]
    anchors = sorted({r["anchor_ms"] for r in rows})
    n = len(anchors)
    cuts = [int(n * sum(fractions[:i])) for i in (1, 2, 3)]
    need(0 < cuts[0] < cuts[1] < cuts[2] < n, "NOT_ENOUGH_DISTINCT_ANCHORS")
    boundaries = [anchors[i] for i in cuts]
    assigned, purged = {name: [] for name in PARTITIONS}, []
    for row in rows:
        i = sum(row["anchor_ms"] >= boundary for boundary in boundaries)
        name = PARTITIONS[i]
        if i < 3 and max(row["outcome_observed_at_ms"], row["outcome_available_at_ms"],
                         row["input_available_at_ms"]) >= boundaries[i] - embargo_ms:
            purged.append({"row_id": row["row_id"], "partition": name,
                           "reason": "LABEL_NOT_MATURE_BEFORE_NEXT_PARTITION",
                           "next_partition_anchor_ms": boundaries[i]})
        else:
            assigned[name].append(row)
    for name, values in assigned.items():
        need(len(values) >= minimum_rows, "INSUFFICIENT_ROWS_AFTER_PURGE:" + name)
    result = {"schema": SCHEMA, "dataset_id": dataset["dataset_id"],
              "policy": "ANCHOR_GROUPED_CHRONOLOGICAL_WITH_ACTUAL_LABEL_AVAILABILITY_PURGE",
              "fractions": list(fractions), "embargo_ms": embargo_ms,
              "boundaries_ms": boundaries, "minimum_rows": minimum_rows,
              "partitions": {name: [r["row_id"] for r in assigned[name]] for name in PARTITIONS},
              "coverage": {name: coverage(assigned[name]) for name in PARTITIONS},
              "purged": purged, "test_used_for_selection": False,
              "calibration_used_for_model_fit": False, "prospective": False}
    result["split_id"] = digest(result)
    return result


def validate_split(split, dataset):
    need(split.get("schema") == SCHEMA and split.get("split_id") == digest(
        {k: v for k, v in split.items() if k != "split_id"}), "SPLIT_CONTENT_ID")
    need(split.get("dataset_id") == dataset["dataset_id"], "SPLIT_DATASET_CHANGED")
    expected = build_split(dataset, tuple(split["fractions"]), split["embargo_ms"], split["minimum_rows"])
    need(split == expected, "SPLIT_RULE_OR_PARTITIONS_CHANGED")
    by_id = {r["row_id"]: r for r in dataset["rows"]}
    return {name: [by_id[key] for key in split["partitions"][name]] for name in PARTITIONS}
