#!/usr/bin/env python3
"""Pocket4 CLOSED-source Action V2 weight/temporal-balance audit (no optimizer).

The input is a pre-existing original-source SHA-bound training population.
Emit ONLY small aggregate diagnostics into a new private O_EXCL receipt.
No model weights, inference, active trials, Broker or trade operation.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"research/neuron_learning"))

from laya_cell.action_v2_temporal_pairing import source_time_matched_action_pairs
from laya_cell.integrity import ContractError, digest
from laya_cell.resident_action_v2_closed_source import check_train_source


def no_duplicates(pairs):
    obj={}
    for key,value in pairs:
        if key in obj:
            raise ContractError("V2_REAL_SOURCE_DUPLICATE_JSON_KEY")
        obj[key]=value
    return obj


def source_bytes(path):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    with os.fdopen(fd,"rb") as file:
        st=os.fstat(file.fileno())
        if not stat.S_ISREG(st.st_mode) or not 0<st.st_size<=4*1024*1024:
            raise ContractError("V2_REAL_SOURCE_FILE_SCOPE")
        body=file.read(4*1024*1024+1)
    if len(body)!=st.st_size:
        raise ContractError("V2_REAL_SOURCE_CHANGED_DURING_READ")
    return body


def day(ts):
    return datetime.fromtimestamp(ts/1000,timezone.utc).strftime("%Y-%m-%d")


def analyze(artifact,source_sha256):
    m=check_train_source(artifact)
    rows=artifact["rows"]
    n=len(rows)
    if n!=m["training_rows"]:
        raise ContractError("V2_REAL_SOURCE_COUNT")
    positives=[(i,r) for i,r in enumerate(rows) if r["parent_correct"] is True]
    negatives=[(i,r) for i,r in enumerate(rows) if r["parent_correct"] is False]
    count=len(positives)
    if len(negatives)!=n-count or n<=0 or count==0:
        raise ContractError("V2_REAL_SOURCE_TWO_CLASS_COUNT")
    old_pairs=list(zip([i for i,_ in positives],[i for i,_ in negatives]))
    new=source_time_matched_action_pairs(artifact)
    pairs=new["pairs"]
    old_gaps=[abs(rows[i]["anchor_ms"]-rows[j]["anchor_ms"]) for i,j in old_pairs]
    by_day={}
    for row in rows:
        dt=day(row["anchor_ms"])
        r=by_day.setdefault(dt,{"total":0,"ACT":0,"ESCALATE":0})
        r["total"]+=1
        r["ACT" if row["parent_correct"] else "ESCALATE"]+=1
    days=sorted(by_day)
    train_day=days[0] if days else None
    frozen_train_positives=by_day[train_day]["ACT"] if train_day else 0
    old_neg_dates=dict(Counter(day(rows[j]["anchor_ms"]) for _,j in old_pairs))
    new_neg_dates=dict(Counter(day(rows[j]["anchor_ms"]) for _,j in pairs))
    full_class_weight_act=n/(2*count)
    full_class_weight_escalate=n/(2*(n-count))
    report={
        "schema":"heptatrader.laya-v2-original-closed-prior-probe.v1",
        "source_sha256":source_sha256,
        "source_manifest_id":m["manifest_id"],
        "source_rows_digest":m["rows_digest"],
        "frozen_parent_id":m["frozen_parent_id"],
        "training_cutoff_ms":m["training_cutoff_ms"],
        "sample_total":n,
        "ACT_correct":count,
        "ESCALATE_wrong":n-count,
        "original_training_day_counts":by_day,
        "earliest_day_act_count":frozen_train_positives,
        "earliest_day_satisfies_v2_four_ACT_minimum":frozen_train_positives>=4,
        "old_first_negatives_day_counts":old_neg_dates,
        "old_first_negative_max_gap_ms":max(old_gaps) if old_gaps else None,
        "new_matched_negative_day_counts":new_neg_dates,
        "new_matched_pair_count":len(pairs),
        "new_matched_skipped_positives":new["skipped_act_rows"],
        "new_matched_max_gap_ms":new["max_pair_gap_ms"],
        "new_matched_min_spacing_ms":new.get("min_spacing_ms"),
        "new_matched_max_allowed_lag_ms":new.get("max_lag_ms"),
        "new_pairing_eligible":new["eligible"],
        "original_full_class_weight_ACT":full_class_weight_act,
        "original_full_class_weight_ESCALATE":full_class_weight_escalate,
        "double_balanced_ACT_effective_mass_on_equal_pairs":
            full_class_weight_act/(full_class_weight_act+full_class_weight_escalate),
        "correct_equal_pair_ACT_training_mass":0.5,
        "correct_train_prevalence_at_prior_corrected_inference":count/n,
        "new_v2_profile_requires_true_parent_logits_before_training":True,
        "actual_model_training_jobs":0,
        "actual_optimizer_steps":0,
        "historical_cohort_is_previously_seen_not_prospective":True,
        "no_live_trial_or_existing_source_modified":True,
        "model_promoted":False,
        "broker_orders_sent":0,
    }
    report["report_id"]=digest(report)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source",type=Path,required=True)
    parser.add_argument("--expected-source-sha256",required=True)
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    if (not args.source.is_absolute() or not args.output.is_absolute() or
            not args.output.parent.is_dir() or args.output.exists() or
            args.output.is_symlink() or
            args.output.parent.resolve()==args.source.parent.resolve() or
            len(args.expected_source_sha256)!=64):
        parser.error("existing absolute pinned source and new isolated output required")
    body=source_bytes(args.source)
    sha=hashlib.sha256(body).hexdigest()
    if sha!=args.expected_source_sha256:
        raise ContractError("V2_REAL_SOURCE_SHA_CHANGED")
    artifact=json.loads(body,object_pairs_hook=no_duplicates)
    report=analyze(artifact,sha)
    os.umask(0o077)
    fd=os.open(args.output,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o400)
    with os.fdopen(fd,"w") as file:
        json.dump(report,file,sort_keys=True,separators=(",",":"),allow_nan=False)
        file.write("\n")
        file.flush()
        os.fsync(file.fileno())
    pfd=os.open(args.output.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:os.fsync(pfd)
    finally:os.close(pfd)
    print(json.dumps(report,sort_keys=True),flush=True)


if __name__=="__main__":
    main()
