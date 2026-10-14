"""Causal original-quote-native FX feature/label contract for isolated research.

This is a retrospective, pre-frozen PAPER quote population audit, NOT a fit or
unseen efficacy sample. Every feature is built from original causal quote
pseudo-closes and every prospective *price-only* label from the first original
post-horizon quote. Revised historical OHLC may never replace missing inputs.
"""
from __future__ import annotations

import argparse
from bisect import bisect_left
from datetime import datetime, timezone
from decimal import Decimal, localcontext
import hashlib
import json
import math
import os
from pathlib import Path
import stat

import numpy as np

from historical_training.common import read, sha
from .fx_ridge_original import (
    HORIZON_MS, LOOKBACK, MAX_DECISION_LATENCY_MS, bar_close_features,
)
from .fx_ridge_finite_campaign import verify_sealed
from .fx_ridge_source_drift_audit import (
    BAR_MS, MAX_BAR_LOOKBACK_MS, MAX_PRESEAL_SEGMENTS,
    eligible_bucket_closes, source_segment_paths, unique_rows,
)

SCHEMA = "heptatrader.fx-original-causal-features-and-maturity.v1"
MIN_UNSEEN_NATIVE_BLOCKS_FOR_RESEARCH_TRAIN = 1024
BLOCK_LENGTH_MS = (LOOKBACK - 1) * BAR_MS + HORIZON_MS + BAR_MS  # 80 minutes; one-grid purge
DECISION_OFFSETS_MS = (250, 500, 1000)
REQUIRED_MAX_QUOTE_AGE_MS = MAX_DECISION_LATENCY_MS
MAX_MATURE_QUOTE_LAG_MS = 5000

class NativeHold(ValueError):
    pass

def demand(cond, msg):
    if not cond: raise NativeHold(msg)

def canonical(o):
    return json.dumps(o, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()

def midpoint(q):
    bid, ask = Decimal(q["bid"]), Decimal(q["ask"])
    demand(bid.is_finite() and ask.is_finite() and 0 < bid <= ask,
           "INVALID_ORIGINAL_BID_ASK")
    return (bid+ask)/2

def source_closes(original_rows, before_ms):
    """Use pre-grid original quotes, with full IB-reported validity at grid.

    Do not change the old frozen Ridge eligibility policy. This is a NEW,
    stricter representation contract for a separate possible future model.
    """
    closes, old_stats = eligible_bucket_closes(
        original_rows, min(before_ms, original_rows[-1]["event_at_ms"]))
    counts = {"all_endpoints": old_stats["grid_endpoints_considered"],
              "missing_preclose": old_stats["missing_original_close_endpoints"],
              "expired_before_grid": 0,
              "new_contract_valid_closes": 0}
    strict = {}
    for grid, q in sorted(closes.items()):
        if (q["valid_until_ms"] <= grid or
            not grid-MAX_BAR_LOOKBACK_MS <= q["event_at_ms"] < grid or
            not q["event_at_ms"] <= q["available_at_ms"] <= grid):
            counts["expired_before_grid"] += 1
            continue
        strict[grid] = q
    counts["new_contract_valid_closes"] = len(strict)
    demand(counts["missing_preclose"] +
           counts["expired_before_grid"] +
           counts["new_contract_valid_closes"] == counts["all_endpoints"],
           "SOURCE_COVERAGE_PARTITION_ERROR")
    return strict, counts

def decision_status(q, anchor, offset):
    demand(type(offset) is int and 0 < offset <= 4500, "INVALID_DECISION_OFFSET")
    if q["event_at_ms"] >= anchor or q["available_at_ms"] > anchor:
        return "POSTGRID_QUOTE_NOT_CAUSAL"
    decision = anchor + offset
    if decision - q["available_at_ms"] > REQUIRED_MAX_QUOTE_AGE_MS:
        return "QUOTE_TOO_OLD"
    if decision >= q["valid_until_ms"]:
        return "BROKER_REPORTED_QUOTE_EXPIRED"
    return "ELIGIBLE_PRICE_ONLY_SOURCE"

def delay_grid(closes, anchors):
    report = {}
    for offset in DECISION_OFFSETS_MS:
        counters = {
            "anchors_considered": len(anchors),
            "eligible": 0, "too_old": 0,
            "broker_expired": 0,
            "missing_close": 0,
            "invalid_causal": 0,
        }
        for anchor in anchors:
            v = closes.get(anchor)
            if v is None:
                counters["missing_close"] += 1
                continue
            reason = decision_status(v, anchor, offset)
            key = {
                "ELIGIBLE_PRICE_ONLY_SOURCE": "eligible",
                "QUOTE_TOO_OLD": "too_old",
                "BROKER_REPORTED_QUOTE_EXPIRED": "broker_expired",
                "POSTGRID_QUOTE_NOT_CAUSAL": "invalid_causal",
            }[reason]
            counters[key] += 1
        demand(sum(v for k,v in counters.items() if k!="anchors_considered")
               == counters["anchors_considered"],
               "COUNTERFACTUAL_BUCKET_COUNT_MISMATCH")
        report[str(offset)] = counters
    return report

def first_mature_original(original_rows, timestamps, due):
    """Select FIRST future event; a late first observation must censor.

    A later event cannot replace a late first quote to optimize a target.
    The required availability is frozen at due + 5 seconds. No revisited
    historical OHLC or active endpoint selection.
    """
    i = bisect_left(timestamps, due)
    if i >= len(original_rows) or original_rows[i]["event_at_ms"] > due + MAX_MATURE_QUOTE_LAG_MS:
        return None, "NO_ORIGINAL_FUTURE_MARKET_EVENT"
    q = original_rows[i]
    if not due <= q["available_at_ms"] <= due + MAX_MATURE_QUOTE_LAG_MS:
        return None, "FIRST_FUTURE_QUOTE_LATE_OR_UNAVAILABLE"
    if not q["available_at_ms"] < q["valid_until_ms"]:
        return None, "FUTURE_QUOTE_EXPIRED_ON_ARRIVAL"
    return q, "MATURE_PRICE_ONLY"

def causal_price_rows(original_rows, closes, cutoff):
    """Every row: same native input distribution and original horizon label."""
    timestamps = [x["event_at_ms"] for x in original_rows]
    demand(timestamps == sorted(set(timestamps)), "ORIGINAL_EVENTS_NOT_STRICTLY_SORTED")
    anchors = sorted(closes)
    rows = []
    counts = {
        "anchors_with_close": len(anchors),
        "missing_13_contiguous_endpoints": 0,
        "input_source_duplicate": 0,
        "decision_quote_age_or_expiry_hold": 0,
        "future_quote_absent": 0,
        "future_quote_late": 0,
        "mature_price_only_rows": 0,
    }
    for anchor in anchors:
        endpoints = [anchor-(LOOKBACK-1-i)*BAR_MS for i in range(LOOKBACK)]
        samples = [closes.get(t) for t in endpoints]
        if any(x is None for x in samples):
            counts["missing_13_contiguous_endpoints"] += 1
            continue
        ids=[x["source_id"] for x in samples]
        if len(set(ids)) != LOOKBACK:
            counts["input_source_duplicate"] += 1
            continue
        if decision_status(samples[-1], anchor, 1000) != "ELIGIBLE_PRICE_ONLY_SOURCE":
            counts["decision_quote_age_or_expiry_hold"] += 1
            continue
        # Frozen historical Ridge *formula*, but inputs are ALL original
        # IB PAPER causal pseudo-closes, not historical revised bars.
        _, features=bar_close_features(samples,anchor)
        demand(np.asarray(features).shape==(8,) and
               np.isfinite(features).all(), "NATIVE_FEATURE_NONFINITE")
        due=anchor+HORIZON_MS
        if due+MAX_MATURE_QUOTE_LAG_MS > cutoff:
            counts["future_quote_absent"] += 1
            continue
        q, label_reason = first_mature_original(original_rows,timestamps,due)
        if label_reason == "NO_ORIGINAL_FUTURE_MARKET_EVENT":
            counts["future_quote_absent"] += 1
            continue
        if label_reason != "MATURE_PRICE_ONLY":
            counts["future_quote_late"] += 1
            continue
        x=midpoint(samples[-1])
        y=midpoint(q)
        with localcontext() as ctx:
            ctx.prec=40
            target=(y/x-1)*Decimal(10000)
        rows.append({
            "schema":SCHEMA+".example.v1",
            "anchor_ms":anchor,
            "features":[float(x) for x in features],
            "original_input_quote_ids":ids,
            "input_last_quote_available_at_ms":samples[-1]["available_at_ms"],
            "input_last_quote_valid_until_ms":samples[-1]["valid_until_ms"],
            "decision_delay_ms":1000,
            "mature_due_ms":due,
            "mature_original_quote_event_ms":q["event_at_ms"],
            "mature_original_quote_available_ms":q["available_at_ms"],
            "mature_original_quote_id":q["source_id"],
            "price_only_midpoint_return_bps":str(target),
            "same_original_source_domain":True,
            "retrospectively_observed":True,
            "independent_prospective_prediction":False,
            "actual_executable_fill":False,
            "monetary_reward":False,
        })
        counts["mature_price_only_rows"] += 1
    demand(sum(v for k,v in counts.items() if k != "anchors_with_close") ==
           counts["anchors_with_close"],"NATIVE_LABEL_COVERAGE_INCONSISTENT")
    return rows, counts

def frozen_nonoverlap_subsample(rows):
    """Select oldest eligible, then purge 80 min; no shared input/outcome boundary."""
    out=[]
    last=None
    for row in rows:
        a=row["anchor_ms"]
        if last is None or a >= last + BLOCK_LENGTH_MS:
            out.append(a)
            last = a
    return out

def source_native_report(campaign_root, previous_audit_root, original_bars_path):
    campaign_root=Path(campaign_root)
    _,sealed=verify_sealed(campaign_root)
    prior=read(Path(previous_audit_root),128*1024)
    demand(prior.get("schema")=="heptatrader.fx-ridge-causal-source-drift.v1"
           and prior["original_campaign_seal_sha256"] ==
               sha(campaign_root/"campaign-sealed.json")
           and prior["historical_bars_sha256"] == sha(original_bars_path),
           "PREVIOUS_CAUSAL_AUDIT_NOT_PINNED")
    first=sealed["windows"][0]
    model=read(campaign_root/"window-00"/"model.json",1024*1024)
    manifest=read(campaign_root/"window-00"/"manifest.json",8192)
    spec=read(campaign_root/"window-00"/"feature-spec.json",8192)
    demand(model["historical_bars_sha256"]==sha(original_bars_path)
           and model["historical_validation_selected_action"]=="NO_TRADE"
           and model["candidate_has_positive_validation_edge"] is False
           and model["trade_authority"] is False,
           "HISTORICAL_MODEL_OR_RELEASE_CHANGED")
    source_root=Path(manifest["source_root"])
    demand(source_root.is_absolute() and not source_root.is_symlink()
           and source_root.is_dir()
           and stat.S_IMODE(source_root.stat().st_mode)==0o700,
           "ORIGINAL_SOURCE_ROOT_UNSAFE")
    freeze=sealed["sealed_at_ms"]
    pool=[p for p in source_segment_paths(source_root)
          if p[0]//1000000 < freeze and p[1] < freeze*1000000]
    pool=pool[-MAX_PRESEAL_SEGMENTS:]
    demand(len(pool)==prior["completed_source_segments_selected"],
           "PRESEAL_ORIGINAL_SEGMENT_SET_CHANGED")
    rows,proofs,dup=unique_rows(pool,spec,freeze)
    bytes_total=sum(x["bytes"] for x in proofs)
    hash_proofs=hashlib.sha256(canonical(proofs)).hexdigest()
    demand(hash_proofs==prior["complete_source_prefixes_sha256"] and
           bytes_total==prior["complete_source_total_prefix_bytes"] and
           len(rows)==prior["original_source_unique_timestamps_preseal"] and
           dup==prior["original_source_exact_duplicates_deduplicated"],
           "ORIGINAL_PRESEAL_SOURCE_BYTES_CHANGED")
    closes,covered=source_closes(rows,freeze)
    # This is only a source-engineering counterfactual. Never represent it
    # as a newly registered prediction, fill, or improvement in the old
    # censored event.
    start=((rows[0]["event_at_ms"]+BAR_MS-1)//BAR_MS)*BAR_MS
    stop=(min(rows[-1]["event_at_ms"],freeze)//BAR_MS)*BAR_MS
    endpoints=range(start,stop+1,BAR_MS)
    demand(len(endpoints)==covered["all_endpoints"] and
           covered["all_endpoints"]==
               prior["preseal_quote_bar_close_coverage"]["grid_endpoints_considered"] and
           covered["missing_preclose"]==
               prior["preseal_quote_bar_close_coverage"]["missing_original_close_endpoints"],
           "PRESEAL_GRID_BOUNDARY_CHANGED")
    timing=delay_grid(closes,endpoints)
    examples,states=causal_price_rows(rows,closes,freeze)
    nonoverlap=frozen_nonoverlap_subsample(examples)
    eligible_count=len(nonoverlap)
    train_admissible=eligible_count>=MIN_UNSEEN_NATIVE_BLOCKS_FOR_RESEARCH_TRAIN
    # source-native processing is development-only: the prior data was already
    # inspected and physically sealed, so even enough rows would not be a
    # genuine new independent efficacy or broker execution claim.
    return {
        "schema":SCHEMA,
        "generated_at_utc":datetime.now(timezone.utc).isoformat(),
        "frozen_campaign_seal_sha256":sha(campaign_root/"campaign-sealed.json"),
        "source_drift_audit_sha256":sha(previous_audit_root),
        "historical_bars_sha256":sha(original_bars_path),
        "frozen_original_ridge_model_sha256":sha(campaign_root/"window-00"/"model.json"),
        "preseal_cutoff_ms":freeze,
        "original_segments":len(pool),
        "original_segment_prefixes_sha256":hash_proofs,
        "verified_original_segment_bytes":bytes_total,
        "original_unique_timestamps":len(rows),
        "preseal_grid_quote_eligibility":covered,
        "counterfactual_decision_delays_ms":timing,
        "timing_study_is_not_early_entry_latency_qualification":True,
        "window00_original_censored_retained":True,
        "closed_quote_to_feature_rule":{
            "grid_seconds":300,
            "lookback_closes":LOOKBACK,
            "last_event_before_grid_required":True,
            "original_quote_at_grid_not_expired_required":True,
            "latest_quote_at_grid_availability_required":True,
            "historical_midpoint_ohlc_substitution_allowed":False,
            "decision_offset_ms":1000,
            "max_quote_age_at_decision_ms":REQUIRED_MAX_QUOTE_AGE_MS,
            "mature_price_horizon_ms":HORIZON_MS,
            "future_first_quote_max_lag_ms":MAX_MATURE_QUOTE_LAG_MS,
            "label_source":"FIRST_CAUSALLY_AVAILABLE_ORIGINAL_IB_PAPER_QUOTE_RESEARCH_PRICE_ONLY",
            "broker_execution_or_slippage_certified":False
        },
        "native_mature_price_development_counts":states,
        "native_price_development_samples":len(examples),
        "nonoverlap_80min_candidate_anchor_count":eligible_count,
        "required_nonoverlap_for_any_new_research_model_fit":
            MIN_UNSEEN_NATIVE_BLOCKS_FOR_RESEARCH_TRAIN,
        "research_model_fit_admission":False,
        "research_model_fit_hold_reasons":(
            ["INSUFFICIENT_NATIVE_NONOVERLAP_SAMPLE_COUNT"]
            if not train_admissible else
            ["ALREADY_VIEWED_RETROSPECTIVE_DATA_NOT_NEW_UNSEEN_SAMPLE"]
        ),
        "examples_private_only":True,
        "training_and_live_feature_domain_has_single_original_quote_contract":True,
        "same_original_source_domain_does_not_prove_same_market_regime":True,
        "market_session_not_independently_attested":True,
        "availability_selection_bias_remains":True,
        "original_quote_source_is_locally_hashed_not_broker_signed":True,
        "broker_requests_sent":0,"broker_orders_sent":0,
        "automatic_trading":False,"automatic_training":False,
        "trade_authority":False,"model_promotion_allowed":False,
        "price_only_alpha_claim":False,
    }, examples

def write_once_private(path, payload):
    path=Path(path)
    demand(path.is_absolute() and not path.exists() and not path.is_symlink(),
           "OUTPUT_MUST_BE_NEW")
    path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    st=path.parent.stat()
    demand(st.st_uid==os.geteuid() and
           stat.S_IMODE(st.st_mode)==0o700,"PRIVATE_OUTPUT_DIR_REQUIRED")
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    try:
        with os.fdopen(fd,"wb") as f:
            f.write(json.dumps(payload,sort_keys=True,indent=2,
                               allow_nan=False).encode()+b"\n")
            f.flush();os.fsync(f.fileno())
        dfd=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(dfd)
        finally:os.close(dfd)
    except BaseException:
        path.unlink(missing_ok=True)
        raise

def main():
    p=argparse.ArgumentParser()
    for name in ("campaign-root","previous-drift-report","historical-bars",
                 "report","examples"):
        p.add_argument("--"+name,type=Path,required=True)
    args=p.parse_args()
    os.umask(0o077)
    report, examples=source_native_report(args.campaign_root,
                                          args.previous_drift_report,
                                          args.historical_bars)
    demand(args.report!=args.examples and not args.examples.exists() and
           not args.report.exists(),"OUTPUTS_MUST_BE_NEW")
    write_once_private(args.examples,{
        "schema":SCHEMA+".private-examples.v1",
        "original_preseal_source_prefixes_sha256":
            report["original_segment_prefixes_sha256"],
        "research_only":True,"model_fit_authorized":False,
        "retrospective_not_an_oos_test":True,
        "examples":examples,
    })
    report["private_examples_sha256"]=sha(args.examples)
    write_once_private(args.report,report)
    print(json.dumps({
        "state":"NATIVE_ORIGINAL_PRICE_POPULATION_RESEARCH_ONLY_HOLD",
        "original_feature_label_examples":len(examples),
        "nonoverlap_80min_blocks":
            report["nonoverlap_80min_candidate_anchor_count"],
        "training_admitted":False,"broker_orders_sent":0,
    },sort_keys=True))

if __name__=="__main__":
    main()
