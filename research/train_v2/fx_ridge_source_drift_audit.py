"""Source-freshness and training/live-input drift audit of a FINISHED FX Ridge cohort.

Read-only, no future-label re-selection. The old censored window remains
censored. Retained local IB PAPER quote chains are not broker-signed.
"""
from __future__ import annotations

import argparse
from bisect import bisect_left
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import time

import numpy as np
from historical_training.common import read, sha
from historical_training.eurusd_walkforward_cost import features, predict
from .causal_ttl import sealed_original_broker_expiry
from .fx_ridge_finite_campaign import verify_sealed
from .fx_ridge_original import (
    MAX_DECISION_LATENCY_MS, bar_close_features, LOOKBACK, HORIZON_MS
)

SCHEMA = "heptatrader.fx-ridge-causal-source-drift.v1"
MAX_PRESEAL_SEGMENTS = 256
BAR_MS = 300000
MAX_BAR_LOOKBACK_MS = 5000
MAX_DIAGNOSTIC_SEGMENTS = 3
SEGMENT = re.compile(r"^segment-(\d{18,20})-[a-z0-9]+$")
def require(ok, reason):
    if not ok:
        raise ValueError(reason)

def source_segment_paths(source_root):
    paths = []
    for path in Path(source_root).glob("segment-*/quotes.jsonl"):
        m = SEGMENT.fullmatch(path.parent.name)
        if m is None:
            continue
        require(not path.is_symlink() and not path.parent.is_symlink(),
                "QUOTE_PATH_IS_SYMLINK")
        st = path.stat()
        require(stat.S_ISREG(st.st_mode) and st.st_uid == os.geteuid()
                and st.st_nlink == 1 and st.st_mode & 0o077 == 0,
                "QUOTE_SOURCE_NOT_PRIVATE")
        paths.append((int(m.group(1)), st.st_mtime_ns, path))
    return sorted(paths, key=lambda x: x[0])

def load_segment(path, spec):
    quotes, proof = sealed_original_broker_expiry(path, spec)
    from laya_cell.shadow_source import read_source
    _, _, terminal = read_source(path)
    require(terminal["ended"] is True and
            terminal["sha256"] == proof["verified_prefix_sha256"],
            "SOURCE_SEGMENT_NOT_TERMINATED_OR_CHANGED")
    require(proof["original_period_verified"] is True
            and proof["historical_revisions_used"] is False
            and proof["broker_orders_sent"] == 0
            and proof["broker_requests_sent"] == 0,
            "SOURCE_IS_NOT_ORIGINAL_READONLY_PAPER")
    return quotes, proof

def unique_rows(paths, spec, cutoff_ms):
    seen = {}
    prefix_proofs = []
    exact_duplicate_quotes = 0
    for _, _, path in paths:
        rows, proof = load_segment(path, spec)
        prefix_proofs.append({
            "segment":path.parent.name,
            "sha256":proof["verified_prefix_sha256"],
            "bytes":proof["original_complete_bytes"],
            "number_of_unique_timestamps":len(rows),
        })
        for row in rows:
            if row["available_at_ms"] > cutoff_ms or row["event_at_ms"] > cutoff_ms:
                continue
            t=row["event_at_ms"]
            prior=seen.get(t)
            if prior:
                require(Decimal(prior["bid"]) == Decimal(row["bid"]) and
                        Decimal(prior["ask"]) == Decimal(row["ask"]),
                        "CROSS_SEGMENT_SAME_EVENT_CONFLICT")
                exact_duplicate_quotes+=1
            else:
                seen[t]=row
    return sorted(seen.values(),key=lambda q:q["event_at_ms"]),prefix_proofs,exact_duplicate_quotes

def retrospective_source_failure(campaign, paths, spec):
    sealed=campaign["sealed"]
    final=campaign["final"]
    root=campaign["root"]
    require(final["window_results"][0]["decision_state"]=="CENSORED",
            "FIRST_WINDOW_NOT_ORIGINAL_CENSORED")
    first=sealed["windows"][0]
    anchor=first["anchor_ms"]
    attempt=read(root/"window-00"/"decision-attempt.json",4096)
    require("LAST_ORIGINAL_QUOTE_TOO_OLD" in attempt["reason_code"] and
            attempt["nonretryable"] is True and
            first["first_decision_ms"]<=attempt["attempt_recorded_at_ms"]<=
            first["last_decision_ms"]+5000,
            "CENSORED_ORIGINAL_RECEIPT_CHANGED")
    # This is a later reconstruction from source files closed after the
    # decision. It must NOT replace or re-admit the originally censored event.
    relevant=[row for row in paths if row[0]//1000000<=anchor][-MAX_DIAGNOSTIC_SEGMENTS:]
    require(len(relevant)==MAX_DIAGNOSTIC_SEGMENTS, "NO_CENSORED_SOURCE_CONTEXT")
    quotes,provenance,_=unique_rows(relevant,spec,anchor+10000)
    usable=[q for q in quotes if anchor-BAR_MS//60<=q["event_at_ms"]<anchor
            and q["available_at_ms"]<=anchor]
    require(bool(usable), "NO_RETAINED_PREANCHOR_QUOTE_DIAGNOSTIC")
    previous=max(usable,key=lambda q:(q["event_at_ms"],q["available_at_ms"]))
    after=[q for q in quotes if anchor<=q["event_at_ms"]<=anchor+10000
           and q["available_at_ms"]<=anchor+10000]
    require(bool(after),"NO_RETAINED_POSTANCHOR_QUOTE_DIAGNOSTIC")
    following=min(after,key=lambda q:(q["event_at_ms"],q["available_at_ms"]))
    earliest=first["first_decision_ms"]
    last=attempt["attempt_recorded_at_ms"]
    earliest_age=earliest-previous["available_at_ms"]
    latest_age=last-previous["available_at_ms"]
    require(previous["event_at_ms"]<anchor<=following["event_at_ms"],
            "SOURCE_STRADDLE_ANCHOR_CHANGED")
    # The error can follow from the lower bound alone even though the quote
    # remained IB-valid for a few more milliseconds.
    require(earliest_age>MAX_DECISION_LATENCY_MS and
            earliest<previous["valid_until_ms"],
            "FROZEN_OLD_QUOTE_NOT_DIAGNOSTICALLY_EXPLAINED")
    return {
        "window":0,
        "schema":"heptatrader.fx-ridge-censored-source-timing-reconstruction.v1",
        "state":"RETROSPECTIVE_FRESHNESS_CAUSE_NOT_A_REPLAY",
        "earliest_allowed_decision_ms":earliest,
        "actual_censor_receipt_ms":last,
        "original_quote_event_ms":previous["event_at_ms"],
        "quote_first_available_ms":previous["available_at_ms"],
        "quote_ib_reported_expiry_ms":previous["valid_until_ms"],
        "next_distinct_quote_event_ms":following["event_at_ms"],
        "next_distinct_quote_available_ms":following["available_at_ms"],
        "first_allowed_decision_quote_age_ms":earliest_age,
        "at_censor_receipt_quote_age_ms":latest_age,
        "first_allowed_decision_remaining_validity_ms":
            previous["valid_until_ms"]-earliest,
        "gap_between_distinct_quote_events_ms":
            following["event_at_ms"]-previous["event_at_ms"],
        "source_chain_verified_retrospectively":True,
        "can_prove_source_was_unmodified_at_original_decision":False,
        "at_least_one_source_file_closed_after_decision":True,
        "claim_live_market_gateway_failure":False,
        "explanation":"QUOTE_EVENT_SPARSITY_ACROSS_FIXED_BAR_GRID_NOT_COLLECTOR_RESTART",
        "frozen_censored_state_retained":True,
        "original_ttl_or_window_changed":False,
        "quote_input_backfilled":False,
        "source_hashes":[p["sha256"] for p in provenance],
    }

def eligible_bucket_closes(rows, until_ms):
    """Use the exact frozen selection contract: a causal 5-second pseudo-close."""
    timestamps=[r["event_at_ms"] for r in rows]
    if not timestamps:
        return {},{}
    low=((timestamps[0]+BAR_MS-1)//BAR_MS)*BAR_MS
    high=(min(until_ms,timestamps[-1]+BAR_MS)//BAR_MS)*BAR_MS
    close={}
    stats={"grid_endpoints_considered":0,
           "missing_original_close_endpoints":0,
           "missing_due_to_no_recent_market_event":0,
           "missing_due_to_after_boundary_arrival":0,
           "first_allowed_decision_eligible":0,
           "first_allowed_decision_too_old":0,
           "first_allowed_decision_quote_expired":0,
           "pseudo_close_already_expired_at_grid_end":0,
           "sat_sun_utc_boundaries":0,
           "sat_sun_utc_missing_causal_quote":0,
           "mon_fri_utc_boundaries":0,
           "mon_fri_utc_missing_causal_quote":0}
    for end in range(low,high+1,BAR_MS):
        stats["grid_endpoints_considered"]+=1
        calendar_prefix=("sat_sun_utc" if
                         datetime.fromtimestamp(end/1000,timezone.utc).weekday()>=5
                         else "mon_fri_utc")
        stats[calendar_prefix+"_boundaries"]+=1
        pos=bisect_left(timestamps,end)-1
        has_recent_event=(pos>=0 and timestamps[pos]>=end-MAX_BAR_LOOKBACK_MS)
        chosen=None
        while pos>=0 and timestamps[pos]>=end-MAX_BAR_LOOKBACK_MS:
            q=rows[pos]
            if q["available_at_ms"]<=end:
                chosen=q
                break
            pos-=1
        if chosen is None:
            stats["missing_original_close_endpoints"]+=1
            stats[calendar_prefix+"_missing_causal_quote"]+=1
            key=("missing_due_to_after_boundary_arrival" if has_recent_event
                 else "missing_due_to_no_recent_market_event")
            stats[key]+=1
        else:
            close[end]=chosen
            if chosen["valid_until_ms"]<end:
                stats["pseudo_close_already_expired_at_grid_end"]+=1
            first_decision=end+1000
            if first_decision-chosen["available_at_ms"] > MAX_DECISION_LATENCY_MS:
                stats["first_allowed_decision_too_old"]+=1
            elif first_decision >= chosen["valid_until_ms"]:
                stats["first_allowed_decision_quote_expired"]+=1
            else:
                stats["first_allowed_decision_eligible"]+=1
    return close,stats

def source_feature_matrix(close, limit_end):
    ends=sorted(close)
    if not ends:
        return np.zeros((0,8)),[],{"reference_windows":0,"noncontiguous_or_missing_history":0}
    possible=range(ends[0]+(LOOKBACK-1)*BAR_MS, min(ends[-1],limit_end)+1,BAR_MS)
    x=[]
    used_anchors=[]
    missing=0
    for anchor in possible:
        samples=[close.get(anchor-(LOOKBACK-1-i)*BAR_MS)
                 for i in range(LOOKBACK)]
        if any(q is None for q in samples):
            missing+=1
            continue
        if len(set(q["source_id"] for q in samples))!=LOOKBACK:
            missing+=1
            continue
        # Reuse the frozen feature formula, without collecting labels.
        _,feat=bar_close_features(samples,anchor)
        require(len(feat)==8 and bool(np.isfinite(feat).all()),
                "NONFINITE_ORIGINAL_RESEARCH_FEATURE")
        x.append(feat)
        used_anchors.append(anchor)
    return np.asarray(x,dtype=float).reshape(-1,8),used_anchors,{
        "reference_windows":len(x),
        "noncontiguous_or_missing_history":missing,
    }

def q(v,ps=(0.01,0.05,0.5,0.95,0.99)):
    return [round(float(z),8) for z in np.quantile(v,ps)]

def matched_timestamp_feature_comparison(hist_x, hist_at, live_x,
                                         live_anchors, model):
    """Compare only identical UTC grid anchors, never future performance labels.

    IB historical OHLC is revised; causal original quotes are sparse. This is
    a retrospective representation check on the same timestamp, not a model
    selection or evidence of executable broker prices.
    """
    require(len(hist_x)==len(hist_at) and len(live_x)==len(live_anchors),
            "MATCHED_INPUT_LENGTH_INVALID")
    index={int(t)*1000:i for i,t in enumerate(hist_at)}
    require(len(index)==len(hist_at),"HISTORICAL_FEATURE_ANCHOR_DUPLICATE")
    matched=[(i,index[anchor]) for i,anchor in enumerate(live_anchors)
             if anchor in index]
    result={
        "source":"MATCHED_IDENTICAL_GRID_TIMESTAMPS_AFTER_HISTORICAL_MATURITY",
        "historical_ohlc_revised_not_contemporaneous":True,
        "original_quote_pseudoclose_not_ib_historical_bar":True,
        "shared_anchor_count":len(matched),
        "common_date_price_outcomes_used":False,
        "future_cohort_outcomes_reused":False,
        "new_trade_or_model_efficacy_claim":False,
    }
    if len(matched)<30:
        result["state"]="HOLD_INSUFFICIENT_SAME_TIME_QUOTE_FEATURES"
        return result
    x=np.asarray([live_x[i] for i,_ in matched],float)
    y=np.asarray([hist_x[j] for _,j in matched],float)
    diff=x-y
    require(np.isfinite(diff).all() and
            np.max(np.abs(diff[:,6:]))<1e-8,
            "SAME_GRID_CLOCK_FEATURE_INCONSISTENT")
    mean=np.asarray(model["mean"],float)
    sd=np.asarray(model["stddev"],float)
    weights=np.asarray(model["weights"],float)
    historical_pred=predict((mean,sd,weights),y)
    live_pred=predict((mean,sd,weights),x)
    result.update({
        "state":"MATCHED_RETROSPECTIVE_REPRESENTATION_DIFFERENCE_MEASURED",
        "first_shared_anchor_utc":datetime.fromtimestamp(
            live_anchors[matched[0][0]]/1000,timezone.utc).isoformat(),
        "last_shared_anchor_utc":datetime.fromtimestamp(
            live_anchors[matched[-1][0]]/1000,timezone.utc).isoformat(),
        "numeric_feature_names":(
            "ret_1bar_bps","ret_3bar_bps","ret_6bar_bps","ret_12bar_bps",
            "std_12bar_bps","mean_abs_ret_12bar_bps"),
        "per_numeric_feature_absolute_pair_diff_bps_p50_p95_p99":[
            q(np.abs(diff[:,j]),(0.5,0.95,0.99)) for j in range(6)],
        "per_numeric_feature_signed_mean_pair_diff_bps":[
            round(float(diff[:,j].mean()),8) for j in range(6)],
        "per_numeric_feature_difference_mean_abs_training_sd":[
            round(float(np.mean(np.abs(diff[:,j]))/sd[j]),7)
            for j in range(6)],
        "same_time_predicted_price_return_absolute_difference_bps_p50_p95_p99":
            q(np.abs(live_pred-historical_pred),(0.5,0.95,0.99)),
        "same_time_predicted_price_return_signed_difference_bps_mean":
            round(float((live_pred-historical_pred).mean()),8),
        "matched_hours_equal":True,
        "source_selection_bias_remains":True,
        "not_an_executable_quote_or_broker_fill":True,
    })
    return result

def historical_bar_present_overlap(historical_bars, original_rows, closes, cutoff_ms):
    """Revised IB bar availability is only a SAME-DATE reference, not a
    forward official session calendar or executable trading receipt."""
    require(type(historical_bars) is list and original_rows,
            "OVERLAP_REFERENCE_REQUIRED")
    first=original_rows[0]["event_at_ms"]+MAX_BAR_LOOKBACK_MS
    last=min(cutoff_ms,original_rows[-1]["event_at_ms"])
    endpoints=sorted(set((int(bar["time"])+300)*1000
                         for bar in historical_bars
                         if first <= (int(bar["time"])+300)*1000 <=last))
    count=len(endpoints)
    return {
        "schema":"heptatrader.historical-bar-present-causal-quote-overlap.v1",
        "historical_revised_five_minute_bar_endpoints":count,
        "with_causally_available_original_quote":
            sum(t in closes for t in endpoints),
        "missing_causally_available_original_quote":
            sum(t not in closes for t in endpoints),
        "historical_ohlc_may_be_revised":True,
        "historical_bar_availability_is_not_current_broker_session_schedule":True,
        "does_not_authorize_backfilling_missing_original_quotes":True,
        "no_trade_or_model_efficacy_claim":True,
    }

def drift_metrics(hist_x,ref_x,model,root,fin):
    mean=np.asarray(model["mean"],dtype=float)
    sd=np.asarray(model["stddev"],dtype=float)
    w=np.asarray(model["weights"],dtype=float)
    require(hist_x.shape[1]==ref_x.shape[1]==len(mean)==len(sd)==8
            and len(w)==9 and (sd>0).all()
            and np.isfinite(hist_x).all() and np.isfinite(ref_x).all(),
            "DRIFT_MATRIX_INVALID")
    require(np.allclose(hist_x.mean(axis=0),mean,rtol=0,atol=1e-8)
            and np.allclose(np.maximum(hist_x.std(axis=0),1e-8),sd,rtol=0,atol=1e-8),
            "FROZEN_MODEL_NOT_TRAINED_ON_PINNED_HISTORICAL_X")
    require(len(ref_x)>=50,"TOO_FEW_COMPLETE_CAUSAL_REFERENCE_WINDOWS")
    hist_pred=predict((mean,sd,w),hist_x)
    ref_pred=predict((mean,sd,w),ref_x)
    labels=("ret_1bar_bps","ret_3bar_bps","ret_6bar_bps",
            "ret_12bar_bps","std_12bar_bps","mean_abs_ret_12bar_bps",
            "utc_hour_sin","utc_hour_cos")
    result=[]
    for i,name in enumerate(labels):
        h=hist_x[:,i]; v=ref_x[:,i]
        p01,p99=np.quantile(h,[0.01,0.99])
        result.append({
            "feature":name,
            "historic_p01_p05_p50_p95_p99":q(h),
            "causal_original_p01_p05_p50_p95_p99":q(v),
            "mean_shift_in_frozen_training_sd":round(float((v.mean()-mean[i])/sd[i]),7),
            "outside_hist_train_p01_p99_fraction":
                round(float(((v<p01)|(v>p99)).mean()),7),
            "original_clipped_z_gt_5_fraction":
                round(float((abs((v-mean[i])/sd[i])>5).mean()),7),
        })
    forward=[]
    for n in (1,2,3):
        row=read(root/("window-%02d"%n)/"preoutcome-decision.json",131072)
        v=np.asarray(row["feature_vector"],dtype=float)
        require(v.shape==(8,) and np.isfinite(v).all()
                and row["selected_trading_action"]=="NO_TRADE",
                "FROZEN_FORWARD_INPUT_INVALID")
        pct=[float((hist_x[:,i]<v[i]).mean()) for i in range(8)]
        z=(v-mean)/sd
        forward.append({
            "window":n,"anchor_ms":row["anchor_ms"],
            "pinned_original_forecast_bps":round(float(row["forecast_midpoint_bps"]),8),
            "preaction_original_feature_percentile_against_historical_train":
                [round(t,5) for t in pct],
            "max_abs_z_using_historical_fit_stats":round(float(np.max(np.abs(z))),7),
            "historical_fit_input_clipping_hits":int((abs(z)>5).sum()),
            "training_historical_validation_action":"NO_TRADE",
        })
    return {
        "hist_trained_examples":len(hist_x),
        "preseal_original_complete_input_examples":len(ref_x),
        "per_feature":result,
        "historical_fitted_price_prediction_bps_p01_p05_p50_p95_p99":q(hist_pred),
        "preseal_original_pseudoclose_price_prediction_bps_p01_p05_p50_p95_p99":q(ref_pred),
        "forward_preaction_feature_percentiles":forward,
        "training_uses_revised_historical_ohlc":True,
        "live_uses_original_causal_quote_pseudocloses":True,
        "different_market_dates_and_intraday_regime":True,
        "prediction_distribution_is_not_predictive_efficacy":True,
        "drift_on_3_valid_forward_rows_is_not_statistically_powered":True,
        "no_live_test_result_used_for_model_fitting_or_threshold_selection":True,
    }

def audit(*,campaign_root,historical_bars,output):
    campaign_root=Path(campaign_root)
    intent,sealed=verify_sealed(campaign_root)
    final=read(campaign_root/"campaign-final.json",8192)
    require(final["window_count"]==4 and final["censored_count"]==1
            and final["successful_matured_count"]==3,
            "ORIGINAL_TERMINAL_COHORT_CHANGED")
    registry_window=campaign_root/"window-00"
    spec=read(registry_window/"feature-spec.json")
    model=read(registry_window/"model.json",1024*1024)
    manifest=read(registry_window/"manifest.json",8192)
    require(model["historical_bars_sha256"]==sha(historical_bars)
            and model["candidate_has_positive_validation_edge"] is False
            and model["historical_validation_selected_action"]=="NO_TRADE"
            and model["trade_authority"] is False,
            "FROZEN_HISTORICAL_MODEL_CHANGED")
    source_root=Path(manifest["source_root"])
    require(source_root.is_absolute() and not source_root.is_symlink() and
            source_root.is_dir() and stat.S_IMODE(source_root.stat().st_mode)==0o700,
            "UNEXPECTED_ORIGINAL_SOURCE_ROOT")
    paths=source_segment_paths(source_root)
    diag=retrospective_source_failure(
        {"root":campaign_root,"sealed":sealed,"final":final},paths,spec)
    # This pool was physically completed before the first fixed cohort seal;
    # no future campaign inputs, model labels or outcomes used for selection.
    frozen_ms=sealed["sealed_at_ms"]
    completed=[p for p in paths if p[0]//1000000 < frozen_ms and
               p[1] < frozen_ms*1000000]
    selected=completed[-MAX_PRESEAL_SEGMENTS:]
    require(len(selected)>=48,"NOT_ENOUGH_PRESEALED_ORIGINAL_SOURCE_SEGMENTS")
    source,original_prefixes,duplicate_count=unique_rows(selected,spec,frozen_ms)
    end=max(q["event_at_ms"] for q in source)
    closes,grid_stats=eligible_bucket_closes(source,min(end,frozen_ms))
    live_x,original_anchors,windows_stats=source_feature_matrix(closes,min(end,frozen_ms))
    historical=read(Path(historical_bars),64*1024*1024)
    hist=features(historical["bars"])
    hist_x=np.asarray(hist["x"],dtype=float)
    metrics=drift_metrics(hist_x,live_x,model,campaign_root,final)
    metrics["same_timestamp_original_vs_revised_historical"] = (
        matched_timestamp_feature_comparison(
            hist_x,hist["at"],live_x,original_anchors,model))
    report={
        "schema":SCHEMA,
        "generated_utc":datetime.now(timezone.utc).isoformat(),
        "scope":"READONLY_RETAINED_LOCAL_PAPER_SOURCE_AND_HISTORICAL_FEATURES",
        "source_baseline_cutoff_before_first_campaign_ms":frozen_ms,
        "historical_bars_sha256":sha(historical_bars),
        "model_sha256":sha(registry_window/"model.json"),
        "original_campaign_seal_sha256":sha(campaign_root/"campaign-sealed.json"),
        "research_code_sha256":hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "window00_retrospective_source_diagnosis":diag,
        "completed_source_segments_considered":len(completed),
        "completed_source_segments_selected":len(selected),
        "original_source_unique_timestamps_preseal":len(source),
        "original_source_exact_duplicates_deduplicated":duplicate_count,
        "complete_source_total_prefix_bytes":sum(p["bytes"] for p in original_prefixes),
        "complete_source_prefixes_sha256":hashlib.sha256(
            json.dumps(original_prefixes,sort_keys=True,
                       separators=(",",":")).encode()).hexdigest(),
        "earliest_source_timestamp_ms":source[0]["event_at_ms"],
        "latest_source_timestamp_ms":source[-1]["event_at_ms"],
        "preseal_quote_bar_close_coverage":grid_stats,
        "sat_sun_utc_calendar_is_not_ib_trading_schedule":True,
        "overlap_with_existing_revised_historical_bar_endpoints":
            historical_bar_present_overlap(historical["bars"],source,closes,frozen_ms),
        "preseal_contiguous_feature_coverage":windows_stats,
        "historical_to_original_domain_shift":metrics,
        "original_first_window_still_censored":True,
        "experiment_window_calendar_or_ttl_unchanged":True,
        "statistical_effectiveness_qualified":False,
        "broker_signed_source":False,
        "independent_product_owner_or_witness_signed":False,
        "no_broker_queries_or_orders":True,
        "trade_authority":False,
        "model_promotion_allowed":False,
        "automatic_training":False,
    }
    output=Path(output)
    require(not output.exists() and not output.is_symlink(),
            "OUTPUT_MUST_BE_NEW")
    output.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    require(stat.S_IMODE(output.parent.stat().st_mode)==0o700,
            "PRIVATE_OUTPUT_ROOT_REQUIRED")
    fd=os.open(output,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,"w",encoding="utf8") as f:
        json.dump(report,f,sort_keys=True,indent=2,allow_nan=False)
        f.write("\n");f.flush();os.fsync(f.fileno())
    return report

def main():
    p=argparse.ArgumentParser()
    for name in ("campaign-root","historical-bars","output"):
        p.add_argument("--"+name,type=Path,required=True)
    args=p.parse_args()
    report=audit(campaign_root=args.campaign_root,
                 historical_bars=args.historical_bars,output=args.output)
    summary={
        "state":"RETROSPECTIVE_SOURCE_DIAGNOSIS_AND_PRESEAL_DRIFT_RESEARCH_ONLY",
        "window00_quote_age_at_first_allowed_ms":report[
            "window00_retrospective_source_diagnosis"][
                "first_allowed_decision_quote_age_ms"],
        "window00_distinct_quote_gap_ms":report[
            "window00_retrospective_source_diagnosis"][
                "gap_between_distinct_quote_events_ms"],
        "preseal_original_feature_rows":report[
            "historical_to_original_domain_shift"][
                "preseal_original_complete_input_examples"],
        "original_vs_revised_same_timestamp_rows":report[
            "historical_to_original_domain_shift"][
                "same_timestamp_original_vs_revised_historical"][
                    "shared_anchor_count"],
        "historic_bar_present_causal_quote_misses":report[
            "overlap_with_existing_revised_historical_bar_endpoints"][
                "missing_causally_available_original_quote"],
        "original_first_window_still_censored":True,
        "trade_authority":False,"model_promotion_allowed":False,
    }
    print(json.dumps(summary,sort_keys=True),flush=True)
if __name__=="__main__":
    main()
