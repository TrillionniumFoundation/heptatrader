"""Explain original Laya source HOLD without creating missing quotes or labels.

This is a read-only ROOT-CAUSE diagnostic, never a train/source admission.
It uses the already pinned original minute grid, original original-source
hash-chain reader, and original status snapshot, rather than trying to
repair historical INPUT_UNAVAILABLE windows.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from collections import Counter
from pathlib import Path

from historical_training.common import EvidenceError, read, sha
from laya_cell.source_readiness import RULE as ORIGINAL_RULE
from laya_cell.integrity import digest as chain_digest

SCHEMA="heptatrader.train-v2-original-source-gap-forensics.v1"
RULE={k:ORIGINAL_RULE[k] for k in
      ("input_probes","spacing_ms","horizon_ms","input_grace_ms",
       "endpoint_tolerance_ms","path_gap_max_ms","minimum_usable","source_ttl_ms")}

def diagnose(quotes,status,*,observation_now_ms):
    if not isinstance(status,dict) or status.get("schema")!="heptatrader.quote-readiness-status.v1":
        raise EvidenceError("ORIGINAL_SOURCE_STATUS_SCHEMA")
    observed=status.get("at_ms")
    if (type(observed) is not int or type(observation_now_ms) is not int
            or observed<=0 or observed>observation_now_ms
            or observation_now_ms-observed>120000):
        raise EvidenceError("ORIGINAL_SOURCE_STATUS_NOT_RECENT")
    if status.get("rule")!=ORIGINAL_RULE:
        raise EvidenceError("ORIGINAL_SOURCE_POLICY_CHANGED")
    probes=status.get("probes")
    if not isinstance(probes,list) or len(probes)!=RULE["input_probes"]:
        raise EvidenceError("ORIGINAL_SOURCE_PROBE_COUNT")
    starts=[p.get("start_ms") for p in probes if isinstance(p,dict)]
    last=((observed-RULE["horizon_ms"]-RULE["input_grace_ms"]-
           RULE["endpoint_tolerance_ms"])//RULE["spacing_ms"])*RULE["spacing_ms"]
    expected=[last-i*RULE["spacing_ms"] for i in reversed(range(RULE["input_probes"]))]
    if starts!=expected:
        raise EvidenceError("ORIGINAL_FIXED_GRID_MISMATCH")
    if (not isinstance(quotes,list) or len(quotes)>1000000
            or any(not isinstance(q,dict)
                   or type(q.get("t")) is not int
                   or type(q.get("known_at_ms")) is not int
                   or q["known_at_ms"]>observed
                   or q["t"]>q["known_at_ms"]
                   for q in quotes)):
        raise EvidenceError("ORIGINAL_QUOTE_TIMES_INVALID")
    if any(a["t"]>=b["t"] for a,b in zip(quotes,quotes[1:])):
        raise EvidenceError("ORIGINAL_QUOTE_NOT_ORDERED")
    period=status.get("period_sha256")
    if not isinstance(period,str) or len(period)!=64:
        raise EvidenceError("ORIGINAL_PERIOD_IDENTITY_MISSING")
    origin={"input_empty":0,"input_rejected_after_source":0,
            "observed_original_window":0}
    details=[]
    for p,start in zip(probes,starts):
        if not isinstance(p,dict) or p.get("status") not in (
            "INPUT_UNAVAILABLE","USABLE","OUTCOME_UNAVAILABLE",
            "ENDPOINT_GAP","PATH_GAP"):
            raise EvidenceError("ORIGINAL_PROBE_UNEXPECTED_STATUS")
        anchors=[q for q in quotes
                 if start<=q["known_at_ms"]<=start+RULE["input_grace_ms"]
                 and q["t"]>=start]
        if not anchors:
            if p["status"]!="INPUT_UNAVAILABLE":
                raise EvidenceError("ORIGINAL_USABLE_PROBE_WITHOUT_SOURCE")
            reason="NO_ORIGINAL_QUOTE_IN_FIXED_MINUTE"
            origin["input_empty"]+=1
        elif p["status"]=="INPUT_UNAVAILABLE":
            reason="SOURCE_PRESENT_BUT_CAUSAL_SELECTOR_NOT_ADMITTED"
            origin["input_rejected_after_source"]+=1
        else:
            reason="SOURCE_PRESENT_ORIGINAL_STATUS_"+p["status"]
            origin["observed_original_window"]+=1
        details.append({
            "start_ms":start,
            "original_probe_status":p["status"],
            "original_witnessed_anchor_count":len(anchors),
            "root_cause_class":reason,
        })
    earliest=quotes[0]["t"] if quotes else None
    latest=quotes[-1]["t"] if quotes else None
    end=latest
    first_contiguous=end
    if quotes:
        for before,after in zip(reversed(quotes[:-1]),reversed(quotes[1:])):
            if after["t"]-before["t"]>RULE["path_gap_max_ms"]:
                break
            first_contiguous=before["t"]
    observed_gaps=[
        (b["t"]-a["t"]) for a,b in zip(quotes,quotes[1:])
        if b["t"]-a["t"]>RULE["path_gap_max_ms"]
    ]
    reasons=status.get("reasons")
    if not isinstance(reasons,list) or any(not isinstance(r,str) for r in reasons):
        raise EvidenceError("ORIGINAL_HOLD_REASONS_INVALID")
    if status.get("state") not in (
            "HOLD_NEW_FUSION_WINDOW","READY_FOR_REVIEW_NOT_AUTHORIZATION"):
        raise EvidenceError("ORIGINAL_SOURCE_STATE_INVALID")
    if ((status["state"]=="HOLD_NEW_FUSION_WINDOW" and not reasons)
            or (status["state"]=="READY_FOR_REVIEW_NOT_AUTHORIZATION" and reasons)):
        raise EvidenceError("ORIGINAL_SOURCE_STATE_REASON_CONFLICT")
    if (status["state"]=="READY_FOR_REVIEW_NOT_AUTHORIZATION"
            and sum(p["status"]=="USABLE" for p in probes)<RULE["minimum_usable"]):
        raise EvidenceError("ORIGINAL_READY_WITHOUT_USABLE_PROBES")
    if (status.get("latest_source_quote_ms") not in (None,latest)
            or status.get("latest_quote_age_ms") not in (
                None,observed-latest if latest is not None else None)):
        raise EvidenceError("ORIGINAL_SOURCE_LATEST_QUOTE_MISMATCH")
    result={
        "schema":SCHEMA,
        "original_source_status_at_ms":observed,
        "original_period_sha256":period,
        "original_source_status":status["state"],
        "original_hold_reasons":reasons,
        "original_policy":RULE,
        "original_grid_probes":details,
        "source_anchor_diagnosis":origin,
        "original_quote_count_before_status":len(quotes),
        "first_original_quote_event_ms":earliest,
        "latest_original_quote_event_ms":latest,
        "latest_quote_age_ms_at_original_status":(
            observed-latest if latest is not None else None),
        "last_contiguous_quote_suffix_ms":(
            latest-first_contiguous if latest is not None else 0),
        "largest_actual_quote_gap_ms":max(observed_gaps,default=0),
        "large_actual_quote_gap_count":len(observed_gaps),
        "original_source_reasons_preserved":True,
        "past_missing_windows_repaired":False,
        "synthetic_quotes_or_labels_created":False,
        "historical_revised_bars_substituted":False,
        "production_guard_overridden":False,
        "source_operational_admission":(
            "UNCHANGED_ORIGINAL_HOLD" if
            status["state"]=="HOLD_NEW_FUSION_WINDOW" else
            "UNCHANGED_REVIEW_ONLY_NO_TRAINING"),
        "future_readiness_claim":False,
        "automatic_trial_registration":False,
        "gpu_training_admitted":False,
        "broker_queries_sent":0,
        "broker_orders_sent":0,
    }
    return result

def run(config_path,status_path):
    from laya_cell.resident_data import read_quotes
    from laya_cell.integrity import read_json
    config_hash_before=sha(config_path)
    config=read_json(Path(config_path),1024*1024)
    config_hash_after=sha(config_path)
    status_hash_before=sha(status_path)
    status=read(status_path,8*1024*1024)
    status_hash_after=sha(status_path)
    if (config_hash_before!=config_hash_after or
            status_hash_before!=status_hash_after):
        raise EvidenceError("ORIGINAL_CONFIG_OR_STATUS_CHANGED_DURING_READ")
    if config["period_sha256"]!=status.get("period_sha256"):
        raise EvidenceError("ORIGINAL_CONFIG_PERIOD_CHANGED")
    stamp=status["at_ms"]
    quotes,prefixes=read_quotes(config,stamp)
    result=diagnose(quotes,status,observation_now_ms=int(time.time()*1000))
    # The periodic status may legitimately advance while original quote pages
    # are verified. Bind the exact initial bytes, never the later path content.
    result["original_status_sha256"]=status_hash_after
    result["original_config_sha256"]=config_hash_after
    result["verified_original_source_page_count"]=len(prefixes)
    result["independently_verified_prefixes"]=True
    result["diagnostic_code_sha256"]=sha(Path(__file__))
    return result

def main():
    parser=argparse.ArgumentParser()
    for name in ("config","status","output"):
        parser.add_argument("--"+name,required=True,type=Path)
    args=parser.parse_args()
    os.umask(0o077)
    report=run(args.config,args.status)
    dest=args.output
    if dest.is_symlink() or dest.exists():
        raise EvidenceError("GAP_DIAGNOSTIC_CREATE_ONLY")
    dest.parent.mkdir(parents=True,mode=0o700,exist_ok=True)
    if dest.parent.is_symlink() or dest.parent.stat().st_mode & 0o077:
        raise EvidenceError("GAP_DIAGNOSTIC_PRIVATE_DIRECTORY")
    raw=(json.dumps(report,sort_keys=True,indent=2)+"\n").encode()
    fd=os.open(dest,os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_WRONLY,0o600)
    with os.fdopen(fd,"wb") as f:
        f.write(raw);f.flush();os.fsync(f.fileno())
    fd=os.open(dest.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:os.fsync(fd)
    finally:os.close(fd)
    print(json.dumps({
        "status":report["source_operational_admission"],
        "original_hold":report["original_hold_reasons"],
        "no_quote_minutes":report["source_anchor_diagnosis"]["input_empty"],
        "causal_input_rejected_with_quotes":report["source_anchor_diagnosis"]["input_rejected_after_source"],
        "last_contiguous_quote_suffix_ms":report["last_contiguous_quote_suffix_ms"],
        "future_readiness_claim":False,
        "broker_orders_sent":0,
        "receipt_sha256":hashlib.sha256(raw).hexdigest(),
    },sort_keys=True))
    return 2
if __name__=="__main__":
    raise SystemExit(main())
