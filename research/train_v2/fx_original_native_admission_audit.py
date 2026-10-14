"""Independent read-only admission audit for pre-frozen original EUR/USD source research.

Checks the immutable native-source price examples, disjoint IB historical
session coverage, and original Broker SCHEDULE receipt bytes. Does NOT
re-fit a model, change historical labels, send requests or orders, or
turn the original locally chained quotes into broker-signed evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import time
from decimal import Decimal, InvalidOperation

SCHEMA = "heptatrader.fx-native-original-development-admission.v1"
NATIVE = "heptatrader.fx-original-causal-features-and-maturity.v1"
CALENDAR = "heptatrader.fx-original-broker-session-coverage-audit.v1"
CATEGORIES = ("IB_HISTORICAL_SESSION_OPEN", "IB_HISTORICAL_SESSION_BREAK_OR_CLOSED",
              "UNKNOWN_OUTSIDE_BROKER_SCHEDULE_REQUEST")
INTERVAL_MS = 4800000
REGISTRATION_MINIMUM = 1024
BAR_MS = 300000
HORIZON_MS = 900000

class AdmissionHold(ValueError):
    pass

def require(x, reason):
    if not x:
        raise AdmissionHold(reason)

def raw_sha(raw):
    return hashlib.sha256(raw).hexdigest()

def safe_read(path, max_size=2097152):
    path = Path(path)
    require(path.is_absolute() and not path.is_symlink(), "PRIVATE_PATH_REQUIRED")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        st = os.fstat(fd)
        require(stat.S_ISREG(st.st_mode) and st.st_uid == os.geteuid()
                and st.st_nlink == 1 and not st.st_mode & 0o077
                and 0 < st.st_size <= max_size, "PRIVATE_SOURCE_SIZE_OR_PERMISSIONS")
        chunks = []
        left = st.st_size
        while left:
            part = os.read(fd, min(1048576, left))
            require(bool(part), "SHORT_PRIVATE_READ")
            chunks.append(part)
            left -= len(part)
        after = os.fstat(fd)
        require((st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns) ==
                (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns),
                "READ_CONCURRENT_SOURCE_MUTATION")
        return b"".join(chunks)
    finally:
        os.close(fd)

def strict_json(raw):
    def unique(pairs):
        obj = {}
        for key,value in pairs:
            require(key not in obj, "DUPLICATE_JSON_KEY")
            obj[key] = value
        return obj
    def invalid(_):
        raise AdmissionHold("JSON_NONFINITE")
    obj = json.loads(raw, object_pairs_hook=unique, parse_constant=invalid)
    require(type(obj) is dict, "TOP_LEVEL_JSON_OBJECT_REQUIRED")
    return obj

def num(x):
    require(type(x) in (str,float,int) and type(x) is not bool, "NUMERIC_TYPE")
    try: n = Decimal(str(x))
    except (ValueError,InvalidOperation):
        raise AdmissionHold("BAD_DECIMAL") from None
    require(n.is_finite(), "NUMERIC_NONFINITE")
    return n

def unique_id(x):
    return type(x) is str and len(x)==64 and all(t in "0123456789abcdef" for t in x)

def nonoverlap_count(anchors):
    count,last = 0,None
    for t in anchors:
        if last is None or t >= last+INTERVAL_MS:
            last=t
            count+=1
    return count

def audit(native, private_examples, calendar, *, broker_chunk, campaign_seal,
          previous_drift):
    nraw = safe_read(native)
    eraw = safe_read(private_examples)
    craw = safe_read(calendar)
    native = strict_json(nraw)
    examples = strict_json(eraw)
    calendar = strict_json(craw)
    require(native.get("schema")==NATIVE and
            examples.get("schema")==NATIVE+".private-examples.v1" and
            calendar.get("schema")==CALENDAR, "SCHEMA_SCOPE_INVALID")
    require(native.get("private_examples_sha256")==raw_sha(eraw) and
            calendar.get("source_native_contract_sha256")==raw_sha(nraw),
            "MISMATCHED_NATIVE_DATASET_OR_CALENDAR_BYTES")
    require(raw_sha(safe_read(campaign_seal, 8192)) ==
            native.get("frozen_campaign_seal_sha256") ==
            calendar.get("frozen_campaign_seal_sha256"), "CAMPAIGN_SEAL_NOT_PINNED")
    require(raw_sha(safe_read(previous_drift, 131072))==
            native.get("source_drift_audit_sha256"), "ORIGINAL_DRIFT_RECEIPT_NOT_PINNED")
    require(native.get("original_segment_prefixes_sha256") ==
            examples.get("original_preseal_source_prefixes_sha256") ==
            calendar.get("original_preseal_source_prefixes_sha256"),
            "ORIGINAL_SOURCE_POPULATION_CHANGED")
    for f in ("trade_authority","automatic_training","automatic_trading",
              "model_promotion_allowed","price_only_alpha_claim",
              "research_model_fit_admission"):
        require(native.get(f) is False, "NATIVE_TRAINING_SCOPE_NOT_HOLD:"+f)
    for f in ("trade_authority","model_promotion_allowed",
              "original_trader_behavior_propensity_attested","canonical_owner_or_witness_enrolled"):
        require(calendar.get(f) is False, "CALENDAR_SCOPE_NOT_HOLD:"+f)
    require(examples.get("model_fit_authorized") is False
            and examples.get("research_only") is True
            and examples.get("retrospective_not_an_oos_test") is True
            and native.get("market_session_not_independently_attested") is True
            and native.get("availability_selection_bias_remains") is True
            and calendar.get("cached_historical_session_is_not_current_live_authorization") is True
            and calendar.get("no_session_claim_outside_broker_request") is True
            and calendar.get("no_backward_ohlc_fill_or_training") is True,
            "RESEARCH_EVIDENCE_PROMOTED_OR_LABEL_BACKFILL")
    for doc in (native,calendar):
        require(doc.get("broker_requests_sent")==doc.get("broker_orders_sent")==0,
                "BROKER_ACTION_CLAIM_NOT_ZERO")
    root = Path(broker_chunk)
    require(root.is_absolute() and not root.is_symlink()
            and root.is_dir(), "BROKER_CHUNK_DIR_INVALID")
    broker_hashes = {}
    for key, filename in (("broker_schedule_request_sha256","request.json"),
                          ("broker_schedule_receipt_sha256","receipt.json"),
                          ("broker_schedule_response_sha256","response.json")):
        broker_hashes[key] = raw_sha(safe_read(root/filename, 2097152))
        require(calendar.get(key)==broker_hashes[key],
                "BROKER_SCHEDULE_BYTES_NOT_PINNED:"+filename)
    request = strict_json(safe_read(root/"request.json",8192))
    receipt = strict_json(safe_read(root/"receipt.json",8192))
    response = strict_json(safe_read(root/"response.json",2097152))
    require(request.get("kind")=="schedule"
            and request.get("what")=="SCHEDULE"
            and receipt.get("state")=="RECEIVED"
            and receipt.get("request")==request
            and receipt.get("response_sha256")==broker_hashes["broker_schedule_response_sha256"]
            and response.get("errors")==[]
            and response.get("bars")==[]
            and type(response.get("schedule")) is dict
            and len(response["schedule"].get("sessions", []))==
                  calendar.get("historical_schedule_session_count"),
            "BROKER_HISTORICAL_SCHEDULE_SCOPE_INCOMPLETE")
    coverage = native.get("preseal_grid_quote_eligibility")
    counts = native.get("native_mature_price_development_counts")
    timing = native.get("counterfactual_decision_delays_ms")
    require(type(coverage) is dict and type(counts) is dict and type(timing) is dict,
            "MISSING_CAUSAL_COVERAGE")
    def nonnegative_int(v):
        return type(v) is int and v>=0
    require(all(nonnegative_int(v) for v in coverage.values())
            and all(nonnegative_int(v) for v in counts.values()),
            "BAD_COVERAGE_COUNT")
    total = coverage["all_endpoints"]
    closed = coverage["new_contract_valid_closes"]
    require(total == coverage["missing_preclose"]+coverage["expired_before_grid"]+closed
            and counts["anchors_with_close"]==closed
            and closed == sum(v for k,v in counts.items() if k!="anchors_with_close"),
            "INPUT_COVERAGE_PARTITION_NOT_CONSERVED")
    calendar_groups = calendar.get("session_coverage")
    require(type(calendar_groups) is dict and set(calendar_groups)==set(CATEGORIES),
            "BROKER_UNKNOWN_SESSION_STRATUM_MISSING")
    for label,group in calendar_groups.items():
        require(type(group) is dict and all(nonnegative_int(v) for v in group.values())
                and group["grid_count"] == group["missing_quote_close"]+group["causal_quote_close"]
                and group["causal_quote_close"] ==
                        group["original_valid_for_fixed_plus_1000_ms"]+
                        group["quote_age_or_expiry_rejected"],
                "SESSION_COVERAGE_PARTITION_INVALID:"+label)
    require(sum(x["grid_count"] for x in calendar_groups.values())==total
            and sum(x["missing_quote_close"] for x in calendar_groups.values())==
            coverage["missing_preclose"]
            and sum(x["causal_quote_close"] for x in calendar_groups.values())==closed,
            "BROKER_SCOPE_TOTALS_NOT_NATIVE_POPULATION")
    all_examples=examples.get("examples")
    require(type(all_examples) is list
            and len(all_examples)==native.get("native_price_development_samples")==
            counts["mature_price_only_rows"], "SOURCE_EXAMPLE_COUNT_MISMATCH")
    cutoff=native.get("preseal_cutoff_ms")
    require(type(cutoff) is int and cutoff>0, "PRESEAL_CUTOFF_NOT_FIXED")
    anchor_times=[]
    unique_labels=set()
    for row in all_examples:
        require(type(row) is dict and row.get("schema")==NATIVE+".example.v1",
                "NATIVE_SAMPLE_SCHEMA")
        t=row.get("anchor_ms")
        due=row.get("mature_due_ms")
        event=row.get("mature_original_quote_event_ms")
        availability=row.get("mature_original_quote_available_ms")
        quote_available=row.get("input_last_quote_available_at_ms")
        valid_until=row.get("input_last_quote_valid_until_ms")
        require(all(type(v) is int for v in
                    (t,due,event,availability,quote_available,valid_until))
                and t%BAR_MS==0 and
                (not anchor_times or t>anchor_times[-1])
                and due==t+HORIZON_MS and due<=event<=availability<=due+5000
                and quote_available<=t and
                t+1000-quote_available<=4500 and t+1000<valid_until
                and availability<cutoff and event<cutoff and t<cutoff,
                "NONCAUSAL_OR_UNSORTED_NATIVE_PRICE_ROW")
        ids = row.get("original_input_quote_ids")
        require(type(ids) is list and len(ids)==13
                and len(set(ids))==13 and all(unique_id(x) for x in ids)
                and unique_id(row.get("mature_original_quote_id"))
                and row["mature_original_quote_id"] not in ids,
                "ORIGINAL_QUOTE_IDENTITY_REUSED_OR_FORGED")
        feature=row.get("features")
        require(type(feature) is list and len(feature)==8
                and all(type(v) in (float,int) and math.isfinite(v) for v in feature)
                and all(abs(float(v))<=1.00001 for v in feature[-2:]),
                "NONFINITE_OR_MISMATCHED_NATIVE_FEATURES")
        num(row.get("price_only_midpoint_return_bps"))
        require(row.get("same_original_source_domain") is True
                and row.get("retrospectively_observed") is True
                and row.get("independent_prospective_prediction") is False
                and row.get("actual_executable_fill") is False
                and row.get("monetary_reward") is False,
                "PRIVATE_PRICE_ROW_RELABELLED_AS_TRADING_OR_UNSEEN")
        anchor_times.append(t)
        require(row["mature_original_quote_id"] not in unique_labels,
                "FUTURE_QUOTE_LABEL_REUSED_ACROSS_ROWS")
        unique_labels.add(row["mature_original_quote_id"])
    independent=nonoverlap_count(anchor_times)
    required=native.get("required_nonoverlap_for_any_new_research_model_fit")
    require(independent==native.get("nonoverlap_80min_candidate_anchor_count")
            and type(required) is int and required>=REGISTRATION_MINIMUM
            and independent<required,
            "NONOVERLAP_INDEPENDENCE_GATE_FALSIFIED")
    require(native.get("research_model_fit_hold_reasons")==
            ["INSUFFICIENT_NATIVE_NONOVERLAP_SAMPLE_COUNT"],
            "HOLD_REASONS_NOT_PRESERVED")
    for k in ("250","500","1000"):
        group=timing.get(k)
        require(type(group) is dict
                and group.get("anchors_considered")==total
                and sum(v for name,v in group.items() if name!="anchors_considered")==total,
                "COUNTERFACTUAL_DECISION_BUCKET_INVALID")
    require(timing["1000"]["eligible"]==
            sum(g["original_valid_for_fixed_plus_1000_ms"]
                for g in calendar_groups.values()),
            "BROKER_AND_NATIVE_ORIGINAL_TTL_COUNTS_DIFFER")
    return {
        "schema":SCHEMA,
        "checked_at_ms":time.time_ns()//1000000,
        "native_report_sha256":raw_sha(nraw),
        "private_examples_sha256":raw_sha(eraw),
        "broker_calendar_report_sha256":raw_sha(craw),
        "campaign_seal_sha256":native["frozen_campaign_seal_sha256"],
        "original_source_prefixes_sha256":native["original_segment_prefixes_sha256"],
        "historical_broker_schedule_sha256":broker_hashes["broker_schedule_response_sha256"],
        "distinct_original_quote_events":native["original_unique_timestamps"],
        "input_grid_total":total,
        "eligible_causal_original_closes":closed,
        "native_price_development_rows":len(anchor_times),
        "nonoverlap_80min_blocks":independent,
        "minimum_required_for_research_model_fit":required,
        "broker_session_coverage":calendar_groups,
        "training_and_trading_admission":"HOLD",
        "original_source_locally_hashed_not_broker_signed":True,
        "IB_historical_schedule_not_current_authorization":True,
        "no_revised_OHLC_backfill":True,
        "no_lookahead_or_model_retraining":True,
        "prior_prospective_test_sample_retained_unaltered":True,
        "automatic_training":False,"model_promotion_allowed":False,
        "broker_queries_sent":0,"broker_orders_sent":0,
        "trade_authority":False
    }

def main():
    p=argparse.ArgumentParser()
    for name in ("native-contract","private-examples","calendar",
                 "broker-chunk","campaign-seal","previous-drift","output"):
        p.add_argument("--"+name,required=True,type=Path)
    args=p.parse_args()
    result=audit(args.native_contract,args.private_examples,args.calendar,
                 broker_chunk=args.broker_chunk,
                 campaign_seal=args.campaign_seal,
                 previous_drift=args.previous_drift)
    target=args.output
    require(target.is_absolute() and not target.exists() and
            not target.is_symlink() and not target.parent.is_symlink(),
            "NEW_PRIVATE_REPORT_REQUIRED")
    st=target.parent.stat()
    require(stat.S_ISDIR(st.st_mode) and st.st_uid==os.geteuid() and
            stat.S_IMODE(st.st_mode)==0o700,
            "PRIVATE_OUTPUT_DIRECTORY_REQUIRED")
    fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    try:
        with os.fdopen(fd,"w",encoding="utf-8") as f:
            json.dump(result,f,sort_keys=True,indent=2,allow_nan=False)
            f.write("\n");f.flush();os.fsync(f.fileno())
        dfd=os.open(target.parent,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(dfd)
        finally:os.close(dfd)
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    print(json.dumps({"state":"NATIVE_ORIGINAL_PRICE_TRAINING_HOLD",
          "original_price_rows":result["native_price_development_rows"],
          "independent_blocks":result["nonoverlap_80min_blocks"],
          "requires":result["minimum_required_for_research_model_fit"],
          "broker_orders_sent":0,"automatic_training":False},
          sort_keys=True))
if __name__=="__main__":
    main()
