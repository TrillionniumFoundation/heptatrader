"""Read-only IB-reported historical session evidence vs original quote availability.

Only classify a 5m grid as session OPEN/CLOSED when the original,
SHA256-verified IB PAPER historical SCHEDULE response covers that timestamp.
Every other point is UNKNOWN, never silently classified as a feed failure.
No TWS API requests, trading, altered original TTLs or model fitting.
"""
from __future__ import annotations
import argparse
from bisect import bisect_right
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from historical_training.common import read, sha
from historical_training.dataset import parse_session
from .fx_original_native_dataset import (
    BAR_MS, source_closes, decision_status, write_once_private
)
from .fx_ridge_finite_campaign import verify_sealed
from .fx_ridge_source_drift_audit import (
    MAX_PRESEAL_SEGMENTS, source_segment_paths, unique_rows
)

SCHEMA = "heptatrader.fx-original-broker-session-coverage-audit.v1"

class CalendarHold(ValueError):pass
def require(condition, reason):
    if not condition: raise CalendarHold(reason)

def request_epoch(value):
    try: d=datetime.fromisoformat(value)
    except (TypeError,ValueError) as exc:
        raise CalendarHold("BROKER_REQUEST_TIMESTAMP_INVALID") from exc
    require(d.tzinfo is not None and d.utcoffset() is not None,
            "BROKER_REQUEST_TZ_MISSING")
    return int(d.timestamp()*1000)

def parse_cached_schedule(schedule_dir):
    root=Path(schedule_dir)
    request=read(root/"request.json",4096)
    receipt=read(root/"receipt.json",4096)
    response=read(root/"response.json",524288)
    require(request.get("kind")=="schedule" and
            request.get("what")=="SCHEDULE" and
            request.get("bar_size")=="1 day" and
            request.get("duration")=="30 D",
            "BROKER_SCHEDULE_SCOPE")
    require(receipt.get("state")=="RECEIVED" and
            receipt.get("request")==request and
            receipt.get("response_sha256")==sha(root/"response.json") and
            response.get("errors")==[] and response.get("bars")==[] and
            response.get("end_callback") is None,
            "BROKER_SCHEDULE_COMPLETION_OR_HASH_INVALID")
    schedule=response.get("schedule")
    require(type(schedule) is dict and
            schedule.get("timezone") in ("US/Eastern","America/New_York"),
            "BROKER_TIMEZONE_NOT_QUALIFIED")
    entries=schedule.get("sessions")
    require(type(entries) is list and 1<=len(entries)<=100,
            "BROKER_SCHEDULE_SESSION_BOUND")
    intervals=[]
    for e in entries:
        require(type(e) is dict and
                type(e.get("start")) is str and
                type(e.get("end")) is str and
                type(e.get("reference_date")) is str,
                "BROKER_SESSION_FIELDS")
        a=parse_session(e["start"],schedule["timezone"])*1000
        b=parse_session(e["end"],schedule["timezone"])*1000
        require(0 < a < b and b-a <=36*3600*1000,
                "BROKER_SESSION_DURATION_INVALID")
        intervals.append((a,b))
    intervals.sort()
    require(all(a[1]<=b[0] for a,b in zip(intervals,intervals[1:])),
            "BROKER_SESSIONS_OVERLAP")
    low=request_epoch(request["requested_start_utc"])
    high=request_epoch(request["requested_end_utc"])
    require(low<high and intervals[0][0]<=low and intervals[-1][1]>=high,
            "BROKER_SCHEDULE_NOT_REQUEST_FULL_COVERAGE")
    require(parse_session(schedule["start"],schedule["timezone"])*1000<=low and
            parse_session(schedule["end"],schedule["timezone"])*1000>=high,
            "BROKER_SCHEDULE_ENVELOPE_NOT_COMPLETE")
    return {
        "requested_low_ms":low,"requested_high_ms":high,
        "session_intervals":intervals,
        "interval_starts":[a for a,_ in intervals],
        "session_count":len(intervals),
        "request_sha256":sha(root/"request.json"),
        "receipt_sha256":sha(root/"receipt.json"),
        "response_sha256":sha(root/"response.json"),
        "source":"PREVIOUSLY_CAPTURED_READONLY_IB_PAPER_HISTORICAL_SCHEDULE",
        "broker_digitally_signed":False,
    }

def session_label(t, schedule):
    require(type(t) is int, "SESSION_GRID_EPOCH_INVALID")
    if not schedule["requested_low_ms"]<=t<=schedule["requested_high_ms"]:
        return "UNKNOWN_OUTSIDE_BROKER_SCHEDULE_REQUEST"
    starts=schedule["interval_starts"]
    i=bisect_right(starts,t)-1
    if i>=0 and t<schedule["session_intervals"][i][1]:
        return "IB_HISTORICAL_SESSION_OPEN"
    return "IB_HISTORICAL_SESSION_BREAK_OR_CLOSED"

def grid_session_quality(grid_times, closes, schedule):
    """Disjoint broker schedule strata; never treat out-of-coverage as closed."""
    counters={}
    for label in (
        "IB_HISTORICAL_SESSION_OPEN",
        "IB_HISTORICAL_SESSION_BREAK_OR_CLOSED",
        "UNKNOWN_OUTSIDE_BROKER_SCHEDULE_REQUEST",
    ):
        counters[label]={
            "grid_count":0,"causal_quote_close":0,"missing_quote_close":0,
            "original_valid_for_fixed_plus_1000_ms":0,
            "quote_age_or_expiry_rejected":0,
        }
    for t in grid_times:
        label=session_label(t,schedule)
        row=counters[label]
        row["grid_count"]+=1
        q=closes.get(t)
        if q is None:
            row["missing_quote_close"]+=1
        else:
            row["causal_quote_close"]+=1
            if decision_status(q,t,1000)=="ELIGIBLE_PRICE_ONLY_SOURCE":
                row["original_valid_for_fixed_plus_1000_ms"]+=1
            else:
                row["quote_age_or_expiry_rejected"]+=1
    require(sum(x["grid_count"] for x in counters.values())==len(grid_times),
            "SCHEDULE_GRID_COUNT_MISMATCH")
    for c in counters.values():
        require(c["causal_quote_close"]+c["missing_quote_close"]==c["grid_count"]
                and c["original_valid_for_fixed_plus_1000_ms"]+
                    c["quote_age_or_expiry_rejected"]==c["causal_quote_close"],
                "SCHEDULE_COVERAGE_PARTITION_FAILED")
    return counters

def audit(*,original_campaign,source_contract,broker_schedule_chunk):
    campaign_root=Path(original_campaign)
    _,sealed=verify_sealed(campaign_root)
    previous=read(source_contract,65536)
    require(previous.get("schema")==
                "heptatrader.fx-original-causal-features-and-maturity.v1"
            and previous["frozen_campaign_seal_sha256"]==
                sha(campaign_root/"campaign-sealed.json")
            and previous["preseal_grid_quote_eligibility"]["all_endpoints"]==1452
            and previous["original_segments"]>=48
            and previous["trade_authority"] is False
            and previous["model_promotion_allowed"] is False,
            "ORIGINAL_SOURCE_CONTRACT_CHANGED")
    spec=read(campaign_root/"window-00"/"feature-spec.json")
    manifest=read(campaign_root/"window-00"/"manifest.json")
    source_root=Path(manifest["source_root"])
    freeze=sealed["sealed_at_ms"]
    paths=[x for x in source_segment_paths(source_root)
           if x[0]//1000000 < freeze and x[1] < freeze*1000000]
    paths=paths[-MAX_PRESEAL_SEGMENTS:]
    require(len(paths)==previous["original_segments"],
            "ORIGINAL_SOURCE_SEGMENT_SET_CHANGED")
    rows,pins,duplicates=unique_rows(paths,spec,freeze)
    digest=hashlib.sha256(json.dumps(pins,sort_keys=True,
                separators=(",",":")).encode()).hexdigest()
    require(digest==previous["original_segment_prefixes_sha256"] and
            len(rows)==previous["original_unique_timestamps"],
            "ORIGINAL_SOURCE_PREFIXES_CHANGED")
    closes,baseline=source_closes(rows,freeze)
    require(baseline==previous["preseal_grid_quote_eligibility"],
            "ORIGINAL_COHORT_GRID_POPULATION_CHANGED")
    start=((rows[0]["event_at_ms"]+BAR_MS-1)//BAR_MS)*BAR_MS
    end=(min(freeze,rows[-1]["event_at_ms"])//BAR_MS)*BAR_MS
    grid=range(start,end+1,BAR_MS)
    require(len(grid)==baseline["all_endpoints"],"GRID_POPULATION_CHANGED")
    schedule=parse_cached_schedule(broker_schedule_chunk)
    groups=grid_session_quality(grid,closes,schedule)
    return {
        "schema":SCHEMA,
        "generated_utc":datetime.now(timezone.utc).isoformat(),
        "source_native_contract_sha256":sha(source_contract),
        "frozen_campaign_seal_sha256":sha(campaign_root/"campaign-sealed.json"),
        "original_preseal_quote_segments":len(paths),
        "original_preseal_source_prefixes_sha256":digest,
        "broker_schedule_response_sha256":schedule["response_sha256"],
        "broker_schedule_request_sha256":schedule["request_sha256"],
        "broker_schedule_receipt_sha256":schedule["receipt_sha256"],
        "historical_schedule_session_count":schedule["session_count"],
        "historical_schedule_request_start_utc":
            datetime.fromtimestamp(schedule["requested_low_ms"]/1000,timezone.utc).isoformat(),
        "historical_schedule_request_end_utc":
            datetime.fromtimestamp(schedule["requested_high_ms"]/1000,timezone.utc).isoformat(),
        "session_coverage":groups,
        "no_session_claim_outside_broker_request":True,
        "cached_historical_session_is_not_current_live_authorization":True,
        "cached_ib_market_schedule_is_not_cryptographic_broker_signature":True,
        "original_quote_validity_not_relaxed":True,
        "old_censored_original_cohort_not_relabelled":True,
        "no_backward_ohlc_fill_or_training":True,
        "original_trader_behavior_propensity_attested":False,
        "canonical_owner_or_witness_enrolled":False,
        "model_promotion_allowed":False,
        "trade_authority":False,
        "broker_requests_sent":0,
        "broker_orders_sent":0,
    }

def main():
    p=argparse.ArgumentParser()
    for k in ("campaign-root","source-contract","broker-schedule-chunk","output"):
        p.add_argument("--"+k,type=Path,required=True)
    a=p.parse_args()
    from .fx_original_native_dataset import write_once_private
    report=audit(original_campaign=a.campaign_root,
                 source_contract=a.source_contract,
                 broker_schedule_chunk=a.broker_schedule_chunk)
    write_once_private(a.output,report)
    print(json.dumps({"state":"CACHED_IB_SCHEDULE_SCOPED_QUOTE_COVERAGE_ONLY",
                      "schedule_session_count":report["historical_schedule_session_count"],
                      "coverage":report["session_coverage"],
                      "broker_requests_sent":0,
                      "model_promotion_allowed":False},sort_keys=True))
if __name__=="__main__":
    main()
