"""Fixed-horizon price envelope over ORIGINAL IB PAPER quote-only preseal sources.

Retrospective research; NOT a prospectively registered trading experiment.
No broker sessions inferred outside cached request; no quote backfill,
no Ridge retraining, no order side effects, no financial alpha claims.
Horizon list is immutable within this research release.
"""
from __future__ import annotations

import argparse
from bisect import bisect_left, bisect_right
from datetime import datetime, timezone
from decimal import Decimal, localcontext
import hashlib
import json
import os
from pathlib import Path
import stat

from historical_training.common import read, sha
from .fx_original_native_dataset import (
    BAR_MS, LOOKBACK, MAX_MATURE_QUOTE_LAG_MS, NativeHold, causal_price_rows,
    decision_status, first_mature_original, midpoint, source_closes
)
from .fx_original_broker_calendar_audit import (
    parse_cached_schedule, session_label,
)
from .fx_ridge_finite_campaign import verify_sealed
from .fx_ridge_source_drift_audit import (
    MAX_PRESEAL_SEGMENTS, source_segment_paths, unique_rows,
)

SCHEMA = "heptatrader.fx-original-multi-horizon-price-envelope.v1"
HORIZON_MINUTES = (15, 30, 60, 120)
HORIZON_MS = tuple(x*60*1000 for x in HORIZON_MINUTES)
DECISION_DELAY_MS = 1000
EXTRA_PURGE_MS = BAR_MS
MIN_PRICE_SAMPLE = 1

class EnvelopeHold(ValueError): pass

def require(ok, reason):
    if not ok: raise EnvelopeHold(reason)

def canonical(o):
    return json.dumps(o, sort_keys=True, separators=(",",":"), allow_nan=False).encode()

def quantile(seq, probability):
    require(bool(seq), "EMPTY_QUANTILE_POPULATION")
    v = sorted(seq)
    position=(Decimal(len(v)-1)*Decimal(str(probability)))
    lo=int(position)
    hi=min(lo+1,len(v)-1)
    return str(v[lo]+(v[hi]-v[lo])*(position-lo))

def code_sha():
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()

def enforce_inputs(campaign_root,source_contract,session_evidence_root,
                   cached_schedule_chunk,cost_path):
    root=Path(campaign_root)
    intent,sealed=verify_sealed(root)
    require(intent["shadow_only"] is True and
            intent["trade_authority"] is False and
            sealed["all_registered_before_first_decision"] is True,
            "PRESEALED_CAMPAIGN_IDENTITY")
    original=read(source_contract,65536)
    require(original.get("schema")=="heptatrader.fx-original-causal-features-and-maturity.v1"
            and original["frozen_campaign_seal_sha256"]==sha(root/"campaign-sealed.json")
            and original["research_model_fit_admission"] is False
            and original["trade_authority"] is False,
            "ORIGINAL_NATIVE_CONTRACT_NOT_FROZEN")
    session=read(session_evidence_root,65536)
    schedule=parse_cached_schedule(cached_schedule_chunk)
    require(session.get("schema")=="heptatrader.fx-original-broker-session-coverage-audit.v1"
            and session["source_native_contract_sha256"]==sha(source_contract)
            and session["broker_schedule_request_sha256"]==schedule["request_sha256"]
            and session["broker_schedule_response_sha256"]==schedule["response_sha256"]
            and session["broker_schedule_receipt_sha256"]==schedule["receipt_sha256"]
            and session["frozen_campaign_seal_sha256"]==sha(root/"campaign-sealed.json")
            and session["model_promotion_allowed"] is False,
            "CACHED_IB_SESSION_PROVENANCE_CHANGED")
    cost=read(cost_path,2*1024*1024)
    model=read(root/"window-00"/"model.json",1024*1024)
    require(cost.get("schema")=="heptatrader.ib-cost-evidence.offline.v1"
            and cost.get("orders_sent")==0
            and cost.get("trade_authority") is False
            and cost.get("actual_slippage_verified") is False
            and cost.get("fx_fee_missing_or_unproved_executions")==0
            and model.get("cost_receipt_sha256")==sha(cost_path)
            and model.get("period_sha256")==cost.get("period_source_sha256")
            and model.get("candidate_has_positive_validation_edge") is False
            and model.get("historical_validation_selected_action")=="NO_TRADE",
            "ORIGINAL_15MIN_RIDGE_OR_COST_CHANGED")
    spread=Decimal(str(cost["quote_spread_full_bps"]["p99"]))
    fee=Decimal(str(cost["fx_commission_one_way_bps"]["p90"]))
    require(spread.is_finite() and spread>0 and fee.is_finite() and fee>0,
            "COST_STRESS_NOT_FINITE_POSITIVE")
    assumption=spread+fee*2+Decimal("4")
    hurdle=assumption+Decimal("2")
    return intent,sealed,original,session,schedule,model,spread,fee,assumption,hurdle

def select_nonoverlap_anchors(eligible_preaction_anchors, horizon_ms):
    """Select on preaction eligibility + clock only, BEFORE inspecting labels."""
    require(type(horizon_ms) is int and horizon_ms>0, "HORIZON_INVALID")
    last=None
    selected=[]
    for anchor in eligible_preaction_anchors:
        require(type(anchor) is int and anchor%BAR_MS==0, "ANCHOR_NOT_GRID")
        if last is not None:
            require(anchor>last, "ELIGIBILITY_ANCHORS_NOT_ORDERED")
        if not selected or anchor>=selected[-1]+(LOOKBACK-1)*BAR_MS+horizon_ms+EXTRA_PURGE_MS:
            selected.append(anchor)
        last=anchor
    return selected

def full_broker_session(anchor, due, schedule):
    """Only an interval entirely within ONE cached IB session is classed open.

    The historical SCHEDULE is a retrospective market-hours reference, not
    a broker signature or authorization for a new live trading session.
    """
    if not schedule["requested_low_ms"] <= anchor or (
            due+MAX_MATURE_QUOTE_LAG_MS > schedule["requested_high_ms"]):
        return "UNKNOWN_OUTSIDE_CACHED_BROKER_SESSION_SCOPE"
    starts=schedule["interval_starts"]
    i=bisect_right(starts,anchor)-1
    if i>=0 and starts[i]<=anchor and (
        due+MAX_MATURE_QUOTE_LAG_MS <=schedule["session_intervals"][i][1]):
        return "IB_HISTORICAL_SESSION_FULL_HORIZON_OPEN"
    return "IB_HISTORICAL_SESSION_BREAK_OR_CLOSED"

def build_preaction(root,original):
    _,sealed=verify_sealed(root)
    cutoff=sealed["sealed_at_ms"]
    spec=read(root/"window-00"/"feature-spec.json",8192)
    manifest=read(root/"window-00"/"manifest.json",8192)
    source_root=Path(manifest["source_root"])
    require(source_root.is_absolute() and source_root.is_dir()
            and not source_root.is_symlink()
            and stat.S_IMODE(source_root.stat().st_mode)==0o700,
            "ORIGINAL_FEED_SCOPE_CHANGED")
    # Do not import quotes closed after the old preregistration. Historical
    # local mtimes are not an independently attested market timestamp.
    paths=[p for p in source_segment_paths(source_root)
           if p[0]//1000000<cutoff and p[1]<cutoff*1000000]
    paths=paths[-MAX_PRESEAL_SEGMENTS:]
    require(len(paths)==original["original_segments"],
            "ORIGINAL_SEGMENT_SET_REVISED")
    rows,proofs,duplicates=unique_rows(paths,spec,cutoff)
    require(hashlib.sha256(canonical(proofs)).hexdigest()
            ==original["original_segment_prefixes_sha256"]
            and len(rows)==original["original_unique_timestamps"]
            and sum(p["bytes"] for p in proofs)==original["verified_original_segment_bytes"],
            "ORIGINAL_SOURCE_BYTES_CHANGED")
    closes,stats=source_closes(rows,cutoff)
    require(stats==original["preseal_grid_quote_eligibility"],
            "ORIGINAL_GRID_ELIGIBILITY_REVISED")
    anchors=[]
    missing_history=0
    held_freshness=0
    for anchor in sorted(closes):
        # Use exactly the already-pinned causal original 13 pseudo-close inputs.
        samples=[closes.get(anchor-(LOOKBACK-1-i)*BAR_MS) for i in range(LOOKBACK)]
        if any(s is None for s in samples) or len({s["source_id"] for s in samples})!=LOOKBACK:
            missing_history+=1
            continue
        if decision_status(samples[-1],anchor,DECISION_DELAY_MS)!="ELIGIBLE_PRICE_ONLY_SOURCE":
            held_freshness+=1
            continue
        anchors.append((anchor, samples))
    require(len(anchors)+missing_history+held_freshness==len(closes),
            "ORIGINAL_PREACTION_DENOMINATOR")
    return rows,closes,anchors,{
        "original_segments":len(paths),
        "distinct_quote_event_timestamps":len(rows),
        "causal_quote_close_count":len(closes),
        "preaction_eligible_13close_count":len(anchors),
        "missing_13close_count":missing_history,
        "preaction_quote_freshness_rejected":held_freshness,
        "original_prefixes_sha256":original["original_segment_prefixes_sha256"],
        "original_preseal_cutoff_ms":cutoff,
        "mtime_is_not_a_signed_preseal_proof":True,
    }

def price_outcome(anchor, source, times, horizon_ms, cutoff):
    due=anchor+horizon_ms
    if due+MAX_MATURE_QUOTE_LAG_MS>cutoff:
        return None,"CENSORED_OUTCOME_NOT_PRESEAL"
    q,reason=first_mature_original(source,times,due)
    if q is None:
        return None,"CENSORED_"+reason
    return q,"MATURE_PRICE_ONLY"

def evaluate_horizon(rows,preaction_anchors,schedule,horizon_ms,cutoff,cost,hurdle):
    timestamps=[r["event_at_ms"] for r in rows]
    require(timestamps==sorted(set(timestamps)), "ORIGINAL_TICKS_NOT_SORTED")
    labels={}
    eligible=[]
    maturity_reasons={}
    for anchor,samples in preaction_anchors:
        future,reason=price_outcome(anchor,rows,timestamps,horizon_ms,cutoff)
        maturity_reasons[reason]=maturity_reasons.get(reason,0)+1
        if future is None:
            continue
        with localcontext() as ctx:
            ctx.prec=40
            value=(midpoint(future)/midpoint(samples[-1])-1)*Decimal(10000)
        labels[anchor]=value
        eligible.append(anchor)
    # Anchor sequence is fixed BEFORE outcome inspection.
    anchor_times=[a for a,_ in preaction_anchors]
    selected=select_nonoverlap_anchors(anchor_times,horizon_ms)
    nonoverlap=[labels[a] for a in selected if a in labels]
    # Two disjoint source classes to keep unknown sessions distinct.
    classes=(
        "IB_HISTORICAL_SESSION_FULL_HORIZON_OPEN",
        "IB_HISTORICAL_SESSION_BREAK_OR_CLOSED",
        "UNKNOWN_OUTSIDE_CACHED_BROKER_SESSION_SCOPE",
    )
    strata={c:{"preaction_anchors":0,"mature_price_labels":0,
               "nonoverlap_preaction_selected":0,
               "nonoverlap_mature_price_labels":0}
            for c in classes}
    labels_by_class={c:[] for c in classes}
    for anchor in anchor_times:
        kind=full_broker_session(anchor,anchor+horizon_ms,schedule)
        strata[kind]["preaction_anchors"]+=1
        if anchor in labels:
            strata[kind]["mature_price_labels"]+=1
            labels_by_class[kind].append(labels[anchor])
    for anchor in selected:
        kind=full_broker_session(anchor,anchor+horizon_ms,schedule)
        strata[kind]["nonoverlap_preaction_selected"]+=1
        if anchor in labels:
            strata[kind]["nonoverlap_mature_price_labels"]+=1
    def summarize(seq):
        if not seq:
            return {"count":0,"price_magnitude_covers_assumed_cost":0,
                    "price_magnitude_covers_frozen_margin":0}
        absolute=[abs(x) for x in seq]
        return {
            "count":len(seq),
            "price_magnitude_covers_assumed_cost":sum(x>cost for x in absolute),
            "price_magnitude_covers_frozen_margin":sum(x>hurdle for x in absolute),
            "absolute_original_midpoint_return_bps":{
                "p50":quantile(absolute,"0.5"),
                "p90":quantile(absolute,"0.9"),
                "p95":quantile(absolute,"0.95"),
                "p99":quantile(absolute,"0.99"),
                "maximum":str(max(absolute)),
            },
        }
    return {
        "horizon_minutes":horizon_ms//60000,
        "preaction_denominator":len(preaction_anchors),
        "maturity_state_counts":maturity_reasons,
        "mature_price_label_count":len(eligible),
        "nonoverlap_selected_before_outcome":len(selected),
        "nonoverlap_mature_label_count":len(nonoverlap),
        "nonoverlap_purge_ms":(LOOKBACK-1)*BAR_MS+horizon_ms+EXTRA_PURGE_MS,
        "overall_retro_price_only":summarize(list(labels.values())),
        "label_blind_nonoverlap_preaction_price_only":summarize(nonoverlap),
        "ib_schedule_class_counts":strata,
        "ib_full_horizon_open_only_retro_price":summarize(
            labels_by_class["IB_HISTORICAL_SESSION_FULL_HORIZON_OPEN"]),
        "future_label_omission_is_not_a_zero_return":True,
        "nonoverlap_anchor_choice_does_not_read_future_price":True,
        "selected_cohort_has_source_availability_bias":True,
        "cost_assumes_same_roundtrip_structure_for_every_horizon":True,
    }

def common_anchor_comparison(rows,preaction,schedule,cutoff,cost,hurdle):
    """Compare fixed horizons on *identical* causal inputs and initial anchors.

    The common-mature population still depends on future source availability
    and is therefore NOT a prospective OOS validation sample.
    """
    timestamps=[x["event_at_ms"] for x in rows]
    require(timestamps==sorted(set(timestamps)),"ORIGINAL_TICKS_NOT_SORTED")
    selected=set(select_nonoverlap_anchors([a for a,_ in preaction],
                                            HORIZON_MS[-1]))
    available=[]
    complete_nonoverlap=[]
    all_open=[]
    for anchor,samples in preaction:
        origin=midpoint(samples[-1])
        outcomes=[]
        for horizon in HORIZON_MS:
            q,why=price_outcome(anchor,rows,timestamps,horizon,cutoff)
            if q is None:
                break
            with localcontext() as ctx:
                ctx.prec=40
                outcomes.append((midpoint(q)/origin-1)*Decimal(10000))
        if len(outcomes)!=len(HORIZON_MS):
            continue
        available.append(outcomes)
        if anchor in selected:
            complete_nonoverlap.append(outcomes)
        if full_broker_session(anchor,anchor+HORIZON_MS[-1],schedule
                               )=="IB_HISTORICAL_SESSION_FULL_HORIZON_OPEN":
            all_open.append(outcomes)
    def summarize(matrix):
        answer={"common_label_count":len(matrix),
                "same_source_anchor_and_initial_midpoint_across_horizons":True}
        by_horizon={}
        for i,minutes in enumerate(HORIZON_MINUTES):
            abs_moves=[abs(row[i]) for row in matrix]
            by_horizon[str(minutes)]={
                "count":len(abs_moves),
                "oracle_price_magnitude_above_assumed_cost":sum(
                    x>cost for x in abs_moves),
                "oracle_price_magnitude_above_frozen_cost_and_margin":sum(
                    x>hurdle for x in abs_moves),
                "absolute_midpoint_return_p50_bps":(
                    quantile(abs_moves,"0.5") if abs_moves else None),
            }
        answer["fixed_horizons"]=by_horizon
        return answer
    return {
        "original_preaction_eligible_anchor_denominator":len(preaction),
        "all_four_mature_at_same_anchor":summarize(available),
        "common_longest_horizon_label_blind_nonoverlap_preselected":len(selected),
        "common_longest_horizon_nonoverlap_all_four_mature":
            summarize(complete_nonoverlap),
        "common_all_four_mature_inside_same_cached_ib_session":
            summarize(all_open),
        "conditioning_on_four_mature_prices_has_future_source_selection_bias":True,
        "different_horizons_were_not_ranked_or_selected_for_trading":True,
    }

def run(*,campaign_root,source_contract,session_evidence,cached_schedule_chunk,
        cost_path,original_15min_examples,output):
    root=Path(campaign_root)
    intent,sealed,original,session,schedule,model,spread,fee,cost,hurdle=(
        enforce_inputs(root,source_contract,session_evidence,
                       cached_schedule_chunk,cost_path))
    rows,closes,preaction,coverage=build_preaction(root,original)
    # Exact 15-minute old cohort parity, without training or model fitting.
    stored=read(original_15min_examples,2*1024*1024)
    require(stored.get("model_fit_authorized") is False and
            stored.get("retrospective_not_an_oos_test") is True and
            sha(original_15min_examples)==original["private_examples_sha256"],
            "EXISTING_15MIN_LABELS_NOT_PINNED")
    original_labels={
        x["anchor_ms"]:x for x in stored["examples"]}
    tmp_15=evaluate_horizon(rows,preaction,schedule,HORIZON_MS[0],
                            sealed["sealed_at_ms"],cost,hurdle)
    recomputed={}
    ts=[r["event_at_ms"] for r in rows]
    for anchor,samples in preaction:
        q,reason=price_outcome(anchor,rows,ts,HORIZON_MS[0],sealed["sealed_at_ms"])
        if q is not None:
            with localcontext() as ctx:
                ctx.prec=40
                value=(midpoint(q)/midpoint(samples[-1])-1)*Decimal(10000)
            recomputed[anchor]=(value,q["source_id"])
    require(set(recomputed)==set(original_labels),
            "NEW_15MIN_LABEL_POPULATION_DIFFERS_FROM_FROZEN_ORIGINAL")
    for anchor,(value,eid) in recomputed.items():
        row=original_labels[anchor]
        require(eid==row["mature_original_quote_id"]
                and value==Decimal(row["price_only_midpoint_return_bps"]),
                "ORIGINAL_15MIN_PRICE_LABEL_REWRITTEN")
    report={
        "schema":SCHEMA,
        "generated_utc":datetime.now(timezone.utc).isoformat(),
        "research_source_code_sha256":code_sha(),
        "fixed_horizons_minutes":list(HORIZON_MINUTES),
        "frozen_cost_receipt_sha256":sha(cost_path),
        "frozen_original_model_sha256":sha(root/"window-00"/"model.json"),
        "original_causal_contract_sha256":sha(source_contract),
        "cached_ib_schedule_evidence_sha256":sha(session_evidence),
        "cached_broker_schedule_chunk_sha256":schedule["response_sha256"],
        "frozen_campaign_seal_sha256":sha(root/"campaign-sealed.json"),
        "old_15min_price_labels_exactly_recomputed":True,
        "old_original_censored_window_unchanged":True,
        "data_collected_before_old_research_campaign_seal":True,
        "source_capture_not_independently_ib_signed":True,
        "source_clock_or_filesystem_not_hardware_attested":True,
        "preaction_coverage":coverage,
        "fixed_original_cost_scenario_bps":{
            "historical_full_spread_p99":str(spread),
            "oneway_ib_callback_commission_p90":str(fee),
            "hypothetical_roundtrip_slippage":"4",
            "historical_assumed_roundtrip_cost":str(cost),
            "additional_margin":"2",
            "frozen_cost_plus_margin":str(hurdle),
            "unknown_financing_liquidity_gap_and_execution_costs":True,
        },
        "horizon_results":[
            evaluate_horizon(rows,preaction,schedule,h,sealed["sealed_at_ms"],cost,hurdle)
            for h in HORIZON_MS
        ],
        "common_anchor_comparison":common_anchor_comparison(
            rows,preaction,schedule,sealed["sealed_at_ms"],cost,hurdle),
        "horizons_not_selected_or_ranked_for_deployment":True,
        "retrospective_hindsight_not_predictive_information":True,
        "no_new_future_trial_or_promotion":True,
        "automatic_training":False,"model_fit_admission":False,
        "original_model_trading_action":"NO_TRADE",
        "broker_queries_or_orders":0,"trade_authority":False,
    }
    output=Path(output)
    require(output.is_absolute() and not output.exists() and not output.is_symlink(),
            "RESEARCH_OUTPUT_ALREADY_EXISTS")
    output.parent.mkdir(parents=True,mode=0o700,exist_ok=True)
    require(output.parent.stat().st_uid==os.geteuid() and
            stat.S_IMODE(output.parent.stat().st_mode)==0o700,
            "OUTPUT_DIRECTORY_NOT_PRIVATE")
    fd=os.open(output,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    try:
        raw=json.dumps(report,ensure_ascii=False,sort_keys=True,indent=2,allow_nan=False).encode()+b"\n"
        with os.fdopen(fd,"wb") as f:
            f.write(raw);f.flush();os.fsync(f.fileno())
        d=os.open(output.parent,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(d)
        finally:os.close(d)
    except BaseException:
        output.unlink(missing_ok=True)
        raise
    return report

def main():
    p=argparse.ArgumentParser()
    for key in ("campaign-root","source-contract","session-evidence",
                "cached-schedule-chunk","cost","original-15min-examples","output"):
        p.add_argument("--"+key,required=True,type=Path)
    a=p.parse_args()
    result=run(campaign_root=a.campaign_root,
       source_contract=a.source_contract,
       session_evidence=a.session_evidence,
       cached_schedule_chunk=a.cached_schedule_chunk,
       cost_path=a.cost,
       original_15min_examples=a.original_15min_examples,
       output=a.output)
    print(json.dumps({
        "state":"ORIGINAL_SOURCE_MULTI_HORIZON_RETROSPECTIVE_NO_TRADE",
        "horizon_minutes":list(HORIZON_MINUTES),
        "results":[{"minutes":x["horizon_minutes"],
                    "mature_labels":x["mature_price_label_count"],
                    "nonoverlap_price_labels":x["nonoverlap_mature_label_count"],
                    "ib_full_horizon_open_price_labels":
                       x["ib_full_horizon_open_only_retro_price"]["count"]}
                   for x in result["horizon_results"]],
        "broker_orders_sent":0,"model_fit_admission":False
    },sort_keys=True),flush=True)
if __name__=="__main__":main()
