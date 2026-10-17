#!/usr/bin/env python3
"""Pocket4 original-source Tiny-MLP 90-slot CLOSEOUT with two-source outage attribution.

Read-only to all original experimental, model, Broker and native runtime state.
Validates each original prediction/outcome pair with the frozen campaign's
own exact verifier; independently validates bounded original quote-chain
segments and diagnoses missing 5m bars. NEVER repairs a censored result.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime,timezone
from decimal import Decimal
import hashlib
import json
import math
import os
from pathlib import Path
import stat

ROOT=Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0,str(ROOT/"research/neuron_learning"))
from laya_cell.integrity import ContractError,read_json,sha_file,digest
from laya_cell.shadow_source import read_source,strict
from laya_cell.price_original_campaign import check_plan,verify_saved_outcome
from laya_cell.original90_gap_audit import (
    BIN_MS,source_bar_groups,impacted_anchors,failure_code
)

EVIDENCE=Path("/home/alex/.local/state/heptatrader/operator-evidence")
FROZEN=EVIDENCE/"laya-year-original-shadow90-20261016-v1"
SOURCE=Path("/home/alex/.local/state/heptatrader/neuron-learning/laya-resident-reset-1791349390533/input")
EXPECTED_PLAN_ID="cd5a68152b3f15ec059cf7b36527750d2bb40c81f62910417853b3e1cdf592ed"
# Data window also includes the 13 preceding bars necessary for causal features.
START=datetime(2026,10,16,10,30,tzinfo=timezone.utc)
END=datetime(2026,10,16,14,10,tzinfo=timezone.utc)
START_MS=int(START.timestamp()*1000)
END_MS=int(END.timestamp()*1000)
FIRST_MISSING=1792153200000 # Fri 12:20 UTC
SECOND_MISSING=1792155600000 # Fri 13:00 UTC
SCHEMA="heptatrader.original90-fixed-gap-forensic.v1"


def load_campaign():
    plan=check_plan(read_json(FROZEN/"plan.json"))
    if plan["plan_id"]!=EXPECTED_PLAN_ID or plan["slots"]!=90:
        raise ContractError("GAP_AUDIT_FROZEN_PLAN_CHANGED")
    status=read_json(FROZEN/"status.json")
    if (status.get("observed")!=75 or status.get("censored")!=15
            or status.get("recorded")!=90
            or status.get("state")!="ORIGINAL_SOURCE_90_COVERAGE_FAILED"
            or status.get("model_promotion") is not False
            or status.get("trade_authority") is not False):
        raise ContractError("GAP_AUDIT_ORIGINAL_FINAL_STATUS_CHANGED")
    outcome=[];reasons=Counter()
    metric={"model":{"brier":0.,"nll":0.,"hit":0},
            "train_frequency_prior":{"brier":0.,"nll":0.,"hit":0}}
    observed=0
    for slot in range(90):
        p=FROZEN/f"outcome-{slot:03}.json"
        if not p.is_file() or p.is_symlink():
            raise ContractError("GAP_AUDIT_MISSING_OR_SYMLINK_OUTCOME")
        j=read_json(p)
        predpath=FROZEN/f"prediction-{slot:03}.json"
        if predpath.exists():
            if predpath.is_symlink():raise ContractError("GAP_AUDIT_FOREIGN_PREDICTION")
            pred=read_json(predpath)
            verify_saved_outcome(plan,slot,j,pred,sha_file(predpath))
        else:
            verify_saved_outcome(plan,slot,j)
        if j["status"]=="OBSERVED":
            observed+=1
            for name in metric:
                for k in ("brier","nll"):metric[name][k]+=j[name][k]
                metric[name]["hit"]+=int(j[name]["hit"])
        else:
            reasons[j["reason"]]+=1
        outcome.append(j)
    if observed!=75 or len(outcome)-observed!=15:
        raise ContractError("GAP_AUDIT_FROZEN_OBSERVED_DENOMINATOR_CHANGED")
    for name in metric:
        metric[name]["brier"]/=observed
        metric[name]["nll"]/=observed
        score=status["scores_on_observed_only"][name]
        if (abs(score["mean_brier"]-metric[name]["brier"])>1e-10
                or abs(score["mean_nll"]-metric[name]["nll"])>1e-10
                or score["top1_hits"]!=metric[name]["hit"]):
            raise ContractError("GAP_AUDIT_SCORES_CHANGED")
    return plan,status,outcome,dict(reasons),metric


def chosen_original_source():
    if not SOURCE.is_dir() or SOURCE.is_symlink():
        raise ContractError("GAP_AUDIT_INVALID_ORIGINAL_SOURCE_DIRECTORY")
    paths=[]
    for path in SOURCE.glob("segment-*/quotes.jsonl"):
        try:
            start_ns=int(path.parent.name.split("-")[1])
        except (ValueError,IndexError):
            continue
        # All historical original segments are at this point complete and
        # chain-sealed. No live same-day tail used and no future regrading.
        start_ms=start_ns//1000000
        if START_MS-2*3600000<=start_ms<=END_MS:
            paths.append((start_ms,path))
    paths.sort()
    if not 4<=len(paths)<=24:
        raise ContractError("GAP_AUDIT_SOURCE_SEGMENT_COVERAGE_CHANGED")
    by_time={};pins=[];failure_counts=Counter()
    for start_ms,path in paths:
        head,accepted,prefix=read_source(path)  # original full per-file hash-chain check
        if prefix["ended"] is not True or prefix["uncommitted_tail_bytes"]!=0:
            raise ContractError("GAP_AUDIT_ORIGINAL_SOURCE_NOT_SEALED")
        pins.append({"segment":path.parent.name,"sha256":prefix["sha256"],
                     "chain_head":prefix["chain_head"],"bytes":prefix["bytes"]})
        for q in accepted:
            if not START_MS<=q["t"]<END_MS:continue
            previous=by_time.get(q["t"])
            if previous is not None:
                if (Decimal(str(previous["bid"]))!=Decimal(str(q["bid"])) or
                        Decimal(str(previous["ask"]))!=Decimal(str(q["ask"]))):
                    raise ContractError("GAP_AUDIT_SOURCE_PRICE_CONFLICT")
            if previous is None or q["known_at_ms"]<previous["known_at_ms"]:
                by_time[q["t"]]=q
        # Read original FAILURE reason categories ONLY AFTER read_source()
        # validated the full same segment chain above.
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
        with os.fdopen(fd,"rb") as f:raw=f.read(64*1024*1024+1)
        if len(raw)!=prefix["bytes"]:
            raise ContractError("GAP_AUDIT_SEGMENT_BYTES_CHANGED")
        for line in raw.splitlines():
            obj=strict(line)
            if obj.get("kind")!="FAILURE":continue
            at=obj.get("at_ms")
            if type(at) is not int or not START_MS<=at<END_MS:continue
            d=obj.get("data")
            raw_reason=None
            if isinstance(d,dict):
                raw_reason=d.get("reason_code") or d.get("reason") or d.get("error")
            if raw_reason=="JSONDecodeError":raw_reason="JSON_DECODE_ERROR"
            failure_counts[(at//BIN_MS*BIN_MS,failure_code(raw_reason))]+=1
    return list(by_time.values()),pins,failure_counts


def forensic_report():
    plan,status,records,censor,metrics=load_campaign()
    quotes,pins,failure_counts=chosen_original_source()
    bars=source_bar_groups(quotes,START_MS,END_MS)
    bad=[int(k) for k,v in bars.items() if v["complete"] is False]
    if bad != [FIRST_MISSING,SECOND_MISSING]:
        raise ContractError("GAP_AUDIT_TWO_ORIGINAL_MISSING_BARS_CHANGED")
    affected=impacted_anchors(bad,[plan["start_ms"]+i*BIN_MS for i in range(90)])
    explanation={}
    for i in range(75,90):
        anchor=plan["start_ms"]+i*BIN_MS
        explanation[str(i)]={"censored_reason":records[i]["reason"],
                             "anchor_ms":anchor,
                             "incomplete_required_prior_bars":affected[anchor],
                             "historical_outcome_untouched":True}
    if (records[75]["reason"]!="OUTCOME_COMMITTED_DEADLINE_MISSED" or
        "ORIGINAL_FEATURE_INPUT_LATE_OR_UNCOMMITTED" not in records[76]["reason"] or
        any("ORIGINAL_FEATURE_NONCONTIGUOUS_BARS" not in records[i]["reason"]
            for i in range(77,90))):
        raise ContractError("GAP_AUDIT_ORIGINAL_CENSOR_REASONS_CHANGED")
    # Slot 75 already sealed a prediction. Its 12:20-12:25 *outcome bar*
    # is incomplete; this is supplementary source diagnosis, NOT a new
    # outcome reason or a retroactively scored result.
    if (FIRST_MISSING not in bad or
            any(not explanation[str(i)]["incomplete_required_prior_bars"] for i in range(76,90))):
        raise ContractError("GAP_AUDIT_CASCADE_NOT_PROVEN")
    failures={f"{datetime.fromtimestamp(start/1000,timezone.utc).strftime('%H:%M')}/{why}":count
              for (start,why),count in sorted(failure_counts.items())}
    result={"schema":SCHEMA,"frozen_plan_id":plan["plan_id"],
            "original_plan_sha256":sha_file(FROZEN/"plan.json"),
            "original_status_sha256":sha_file(FROZEN/"status.json"),
            "evidence_scope":"POSTHOC_SOURCE_DIAGNOSTIC_NOT_PREACTION_BACKFILL",
            "source_segments":pins,"source_quote_count":len(quotes),
            "total_registered":90,"observed":75,"censored":15,
            "original_censor_reason_counts":censor,
            "original_observed_only_scores":metrics,
            "invalid_original_bars":{str(start):bars[str(start)] for start in bad},
            "source_failure_counts_by_5m":failures,
            "slot75_89_original_censor_with_13bar_propagation":explanation,
            "identified_bad_original_bar_starts_ms":bad,
            "original_predictions_and_outcomes_mutated":False,
            "broker_orders_sent":0,"model_promoted":False,"trading_authority":False,
            "historical_quality_not_prospective_model_alpha":True,
            "source_not_an_independent_broker_end_to_end_audit":True}
    result["report_id"]=digest(result)
    return result


def main():
    a=argparse.ArgumentParser(description=__doc__)
    a.add_argument("--output",type=Path,required=True)
    opt=a.parse_args()
    output=opt.output
    if (not output.is_absolute() or output.parent.resolve()!=
            EVIDENCE.joinpath("laya-original-price-two-gap-20261016").resolve()
            or not output.parent.is_dir() or output.parent.is_symlink()
            or output.exists() or output.is_symlink()):
        a.error("pre-existing private Pocket4-only audit directory and new receipt required")
    result=forensic_report()
    result["program_sha256"]=sha_file(Path(__file__))
    result["receipt_digest"]=digest(result)
    os.umask(0o077)
    fd=os.open(output,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o400)
    data=(json.dumps(result,sort_keys=True,allow_nan=False,separators=(",",":"))+"\n").encode()
    with os.fdopen(fd,"wb") as f:
        f.write(data);f.flush();os.fsync(f.fileno())
    parent=os.open(output.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:os.fsync(parent)
    finally:os.close(parent)
    summary={"source_quote_count":result["source_quote_count"],
             "observed":result["observed"],"censored":result["censored"],
             "model":result["original_observed_only_scores"]["model"],
             "prior":result["original_observed_only_scores"]["train_frequency_prior"],
             "bad_bars":result["invalid_original_bars"],
             "failure_counts":result["source_failure_counts_by_5m"],
             "receipt_sha256":hashlib.sha256(data).hexdigest(),
             "receipt_id":result["report_id"]}
    print(json.dumps(summary,sort_keys=True,ensure_ascii=False))


if __name__=="__main__":
    main()
