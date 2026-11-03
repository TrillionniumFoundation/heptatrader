"""Rebuild unique PRICE examples from hash-pinned resident jobs and real sources."""
from __future__ import annotations

from bisect import bisect_right
from collections import Counter
from decimal import Decimal
from pathlib import Path
import math

import numpy as np

from research.neuron_learning.laya_cell.dataset import (
    BIN_EDGES_BPS, causal_state, rows_for_training,
)
from research.neuron_learning.laya_cell.integrity import (
    ContractError, digest, read_json_snapshot,
)
from research.neuron_learning.laya_cell.resident_price_baselines import FEATURES, features
from research.neuron_learning.laya_cell.resident_row_accounting import row_id
from research.neuron_learning.laya_cell.source_archive import PIN_SCHEMA
from .source_proof import checked_pages, verify_prefixes

SCHEMA = "heptatrader.model-replacement-dataset.v1"
JOB_MANIFEST_SCHEMA = "heptatrader.model-replacement-job-snapshot.v1"
GRID = {"step_ms": 10000, "method": "PAST_ONLY_LAST_OBSERVATION",
        "maximum_quote_age_ms": 30000,
        "synthetic_market_observations": False,
        "derived_features_only": True}


def need(condition, reason):
    if not condition:
        raise ContractError(reason)


def _sha(value):
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def _pin_key(pin):
    return pin["path"], pin["bytes"], pin["sha256"]


def verify_sources(source_sets, scope):
    """Validate each distinct prefix once; shared archive chains once at longest pin.

    Existing archive validation also checks its independent append-only registry
    and the original sealed files. No directory-wide scans are added here.
    """
    raw = {}
    sequences = {}
    dataset_requirements = []
    for sources in source_sets:
        need(isinstance(sources, list) and sources, "SOURCE_PREFIXES_REQUIRED")
        pages, paths, seen_raw_after_page = [], set(), False
        for pin in sources:
            need(isinstance(pin, dict), "SOURCE_PREFIX_SCHEMA")
            if pin.get("schema") == PIN_SCHEMA:
                need(not seen_raw_after_page, "SOURCE_PAGE_ORDER")
                need(pin.get("period_sha256") == scope.split(":", 1)[0], "SOURCE_PERIOD_CHANGED")
                pages.append(pin)
            else:
                if pages:
                    seen_raw_after_page = True
                paths.add(pin["path"])
                raw[_pin_key(pin)] = pin
        if pages:
            root = str(Path(pages[0]["path"]).parent)
            sequences.setdefault(root, []).append(pages)
            dataset_requirements.append((pages, paths))
    sealed_keys = set()
    expanded = dict(raw)
    page_skips = {}
    all_pages = {}
    for alternatives in sequences.values():
        longest = max(alternatives, key=len)
        for chain in alternatives:
            need(chain == longest[:len(chain)], "SOURCE_ARCHIVE_CHAIN_CONFLICT")
        documents = checked_pages(longest)
        for pin, doc in zip(longest, documents):
            all_pages[digest(pin)] = pin
            page_skips[digest(pin)] = set(doc.get("skipped_paths", []))
            for original in doc["prefixes"]:
                key = _pin_key(original)
                expanded[key] = original
                sealed_keys.add(key)
    for pages, paths in dataset_requirements:
        skipped = set().union(*(page_skips[digest(p)] for p in pages))
        need(skipped <= paths, "SOURCE_ARCHIVE_SKIPPED_RAW_PROOF_MISSING")
    read_stats = verify_prefixes(list(expanded.values()), sealed_keys)
    return {"original_prefixes": sorted(expanded.values(), key=lambda p: _pin_key(p)),
            "archive_pages": sorted(all_pages.values(), key=lambda p: (p["path"], p["index"])),
            "distinct_prefixes_verified": len(expanded), "read_stats": read_stats, "verified": True}


def _checked_quotes(sample):
    quotes = sample["input_snapshot"]["quotes"]
    known = sample["known_at_ms"]
    anchor = sample["anchor_ms"]
    need(type(known) is int and anchor <= known <= anchor + 60000, "INPUT_AVAILABILITY")
    previous = None
    result = []
    for q in quotes:
        t, available = q["t"], q["known_at_ms"]
        need(type(t) is int and type(available) is int and t <= available <= t + 5000
             and available <= known and t <= anchor, "NONCAUSAL_QUOTE")
        need(previous is None or 0 < t - previous <= 30000, "QUOTE_PATH_GAP")
        bid, ask = Decimal(str(q["bid"])), Decimal(str(q["ask"]))
        need(bid.is_finite() and ask.is_finite() and 0 < bid <= ask, "QUOTE_PRICE")
        mid = float((bid + ask) / 2)
        need(math.isclose(mid, float(q["mid"]), rel_tol=0, abs_tol=1e-12), "QUOTE_MID_CHANGED")
        need(_sha(q["receipt"]), "QUOTE_RECEIPT_REQUIRED")
        result.append({"t": t, "known_at_ms": available, "mid": mid,
                       "bid": str(q["bid"]), "ask": str(q["ask"]), "receipt": q["receipt"]})
        previous = t
    return result


def build_dataset(manifest_path: Path, horizon_sec=300):
    """The population is the union of *selected* sealed job rows, not all feed rows."""
    need(horizon_sec in (300, 900), "UNSUPPORTED_HORIZON")
    manifest, manifest_sha = read_json_snapshot(manifest_path, 4 * 1024**2)
    need(manifest.get("schema") == JOB_MANIFEST_SCHEMA, "JOB_MANIFEST_SCHEMA")
    pins = manifest.get("jobs")
    need(isinstance(pins, list) and 0 < len(pins) <= 4096, "JOB_COUNT_BOUND")
    selected, jobs, dataset_pins, datasets = {}, [], {}, {}
    sources, scopes, instruments = [], set(), set()
    occurrences = 0
    exclusions = Counter()
    input_outcomes = {}
    for pin in pins:
        need(isinstance(pin, dict) and _sha(pin.get("sha256")), "JOB_HASH_REQUIRED")
        path = Path(pin["path"])
        job, job_sha = read_json_snapshot(path, 8 * 1024**2)
        need(job_sha == pin["sha256"], "JOB_FILE_CHANGED")
        data_path, expected = Path(job["dataset"]), job["dataset_sha256"]
        key = str(data_path), expected
        if key not in datasets:
            dataset, actual = read_json_snapshot(data_path, 64 * 1024**2)
            need(actual == expected, "DATASET_FILE_CHANGED")
            # Jobs normally have distinct snapshots. Do not retain 99 parsed
            # 6MB datasets when only their selected rows are needed afterward.
            datasets.clear()
            datasets[key] = dataset
            sources.append(dataset["source_prefixes"])
            dataset_pins[key] = {"path": str(data_path), "sha256": actual}
        dataset = datasets[key]
        scopes.add(dataset["scope"])
        instruments.add(dataset["instrument"])
        need(len(scopes) == len(instruments) == 1, "MIXED_SCOPE_OR_INSTRUMENT")
        need(type(dataset.get("frozen_at_ms")) is int
             and dataset["frozen_at_ms"] <= job["window"]["training_end_ms"], "DATASET_FREEZE_AFTER_JOB_CUTOFF")
        rows, invalid = rows_for_training(dataset, job["window"])
        exclusions.update(item["reason"] for item in invalid)
        wanted = job.get("training_row_ids")
        need(isinstance(wanted, list) and wanted and all(_sha(x) for x in wanted)
             and len(set(wanted)) == len(wanted), "JOB_ROW_IDS")
        wanted_set = set(wanted)
        rows = [r for r in rows if row_id(r) in wanted_set]
        need({row_id(r) for r in rows} == wanted_set, "PINNED_JOB_ROW_MISSING")
        samples = {}
        for sample in dataset["samples"]:
            sid = sample["sample_id"]
            if sid in samples:
                need(samples[sid] == sample, "DUPLICATE_SAMPLE_CONFLICT")
            samples[sid] = sample
        jobs.append({"path": str(path), "sha256": job_sha,
                     "dataset_sha256": expected, "training_row_ids": wanted,
                     "training_end_ms": job["window"]["training_end_ms"]})
        for row in rows:
            if row["horizon_sec"] != horizon_sec:
                exclusions["OTHER_HORIZON"] += 1
                continue
            sample = samples[row["sample_id"]]
            state = causal_state(sample, dataset["instrument"], horizon_sec)
            quotes = _checked_quotes(sample)
            index = (300, 900).index(horizon_sec)
            outcome = sample["outcome_receipts"][index]
            need(outcome["horizon_sec"] == horizon_sec and _sha(outcome["source_receipt"]),
                 "OUTCOME_RECEIPT_SCHEMA")
            need(outcome["available_at_ms"] <= dataset["frozen_at_ms"], "OUTCOME_AFTER_FREEZE")
            value_bps = float(sample["targets"][index]) * 100
            need(bisect_right(BIN_EDGES_BPS, value_bps) == row["target_class"], "TARGET_BIN_CHANGED")
            identity = row_id(row)
            record = {"row_id": identity, "sample_id": row["sample_id"],
                      "scope": dataset["scope"], "horizon_sec": horizon_sec,
                      "anchor_ms": row["anchor_ms"], "input_available_at_ms": sample["known_at_ms"],
                      "input_snapshot_sha256": digest(sample["input_snapshot"]),
                      "history_start_ms": quotes[0]["t"], "quotes": quotes,
                      "state": state, "state_sha256": digest(state),
                      "numeric_features": features(state),
                      "target_class": row["target_class"], "return_bps": value_bps,
                      "outcome_observed_at_ms": outcome["observed_at_ms"],
                      "outcome_available_at_ms": outcome["available_at_ms"],
                      "outcome_receipt": outcome,
                      "outcome_receipt_digest": row["outcome_receipt_digest"]}
            input_key = (row["sample_id"], horizon_sec)
            need(input_key not in input_outcomes or input_outcomes[input_key] == identity,
                 "SAME_INPUT_CONFLICTING_OUTCOME")
            input_outcomes[input_key] = identity
            if identity in selected:
                old = {k: v for k, v in selected[identity].items() if k != "job_sha256"}
                need(old == record, "REPEATED_ROW_CONTENT_CHANGED")
                selected[identity]["job_sha256"].append(job_sha)
            else:
                selected[identity] = {**record, "job_sha256": [job_sha]}
            occurrences += 1
    need(selected, "NO_SELECTED_PRICE_ROWS")
    scope = next(iter(scopes))
    if manifest.get('period_sha256') is not None:
        need(scope.split(':',1)[0]==manifest['period_sha256'],'JOB_SNAPSHOT_PERIOD_CHANGED')
    verified_sources = verify_sources(sources, scope)
    rows = sorted(selected.values(), key=lambda r: (r["anchor_ms"], r["row_id"]))
    result = {"schema": SCHEMA, "evaluation_kind": "HISTORICAL_OFFLINE_REPLAY",
              "prospective_predictions": False, "broker_orders_sent": 0,
              "population": "UNION_OF_PINNED_RESIDENT_JOB_SELECTED_ROWS",
              "population_selection_bias": "Historical resident job selection; not an unbiased full-market holdout",
              "scope": scope, "instrument": next(iter(instruments)), "horizon_sec": horizon_sec,
              "bin_edges_bps": list(BIN_EDGES_BPS), "numeric_feature_names": list(FEATURES),
              "sequence_grid": GRID, "rows": rows,
              "sources": {"job_manifest": {"path": str(manifest_path), "sha256": manifest_sha},
                          "jobs": jobs, "datasets": list(dataset_pins.values()), **verified_sources},
              "accounting": {"job_row_occurrences": occurrences, "unique_rows": len(rows),
                             "duplicate_occurrences": occurrences - len(rows),
                             "unique_inputs": len({r["sample_id"] for r in rows}),
                             "unique_causal_states": len({r['state_sha256'] for r in rows}),
                             "included_jobs": len(jobs),
                             "exclusion_counts": dict(sorted(exclusions.items()))}}
    result["dataset_id"] = digest(result)
    return result


def validate_dataset(value):
    need(value.get("schema") == SCHEMA and value.get("dataset_id") == digest(
        {k: v for k, v in value.items() if k != "dataset_id"}), "DATASET_CONTENT_ID")
    need(value.get("bin_edges_bps") == list(BIN_EDGES_BPS)
         and value.get("sequence_grid") == GRID and value["sources"].get("verified") is True,
         "DATASET_POLICY_CHANGED")
    rows = value["rows"]
    need(rows and len({r["row_id"] for r in rows}) == len(rows), "DATASET_DUPLICATE_ROW")
    need(rows == sorted(rows, key=lambda r: (r["anchor_ms"], r["row_id"])), "DATASET_ORDER")
    for row in rows:
        need(row["row_id"] == digest([row["sample_id"], row["horizon_sec"], row["outcome_receipt_digest"]]),
             "ROW_ID_CHANGED")
        need(row["outcome_receipt_digest"] == digest(row["outcome_receipt"])
             and row["state_sha256"] == digest(row["state"]), "ROW_CONTENT_CHANGED")
        need(row["target_class"] == bisect_right(BIN_EDGES_BPS, row["return_bps"]), "TARGET_BIN_CHANGED")
    return value


def regular_context(row):
    """Explicit derived 10s grid. Repeated values remain features, not new quotes."""
    quotes = row["quotes"]
    stop = row["anchor_ms"]
    step = GRID["step_ms"]
    start = stop - ((stop - quotes[0]["t"]) // step) * step
    points = list(range(start, stop + 1, step))
    times = [q["t"] for q in quotes]
    values, receipts = [], []
    for stamp in points:
        i = bisect_right(times, stamp) - 1
        need(i >= 0 and stamp - quotes[i]["t"] <= GRID["maximum_quote_age_ms"], "GRID_UNSUPPORTED_TIME")
        need(quotes[i]["known_at_ms"] <= row["input_available_at_ms"], "GRID_INPUT_FROM_FUTURE")
        values.append(quotes[i]["mid"])
        receipts.append(quotes[i]["receipt"])
    need(len(values) >= 6, "GRID_CONTEXT_TOO_SHORT")
    return np.asarray(values, dtype=np.float32), {
        "grid_start_ms": start, "grid_end_ms": stop, "grid_points": len(values),
        "distinct_source_quotes": len(set(receipts)), "source_receipts_sha256": digest(receipts)}


def training_series(rows, *, min_length):
    """Continuous train-only observed histories; never fabricate the future path."""
    by_time = {}
    for row in rows:
        for q in row["quotes"]:
            if q["t"] in by_time:
                old = by_time[q["t"]]
                need(Decimal(old["bid"]) == Decimal(q["bid"]) and Decimal(old["ask"]) == Decimal(q["ask"]),
                     "TRAIN_SERIES_QUOTE_CONFLICT")
                if q["known_at_ms"] < old["known_at_ms"]:
                    by_time[q["t"]] = q
            else:
                by_time[q["t"]] = q
    groups = []
    for q in sorted(by_time.values(), key=lambda q: q["t"]):
        if not groups or q["t"] - groups[-1][-1]["t"] > GRID["maximum_quote_age_ms"]:
            groups.append([])
        groups[-1].append(q)
    series, pins, rejected = [], [], []
    for group in groups:
        temp = {"quotes": group, "anchor_ms": group[-1]["t"],
                "input_available_at_ms": max(q["known_at_ms"] for q in group)}
        length = (group[-1]["t"] - group[0]["t"]) // GRID["step_ms"] + 1
        if length < min_length:
            rejected.append({"start_ms": group[0]["t"], "end_ms": group[-1]["t"], "grid_points": length})
            continue
        values, pin = regular_context(temp)
        series.append(values)
        pins.append({**pin, "real_quote_count": len(group), "real_quotes_sha256": digest(group)})
    need(series, "NO_CONTIGUOUS_TRAIN_ONLY_SERIES_LONG_ENOUGH")
    return series, {"source_row_ids_sha256": digest(sorted(r["row_id"] for r in rows)),
                    "unique_real_quotes": len(by_time), "series": pins,
                    "excluded_short_series": rejected, "outcomes_used_as_synthetic_path": False}
