"""Independently replay a real 24-cycle SHA-verified source-health canary.

No live monitor replacement; no broker APIs, no order submission or model
training. The fixed precommit, source program bytes and observed original
journal must all match before reporting *research-only* operational freshness.
"""
from __future__ import annotations
import argparse
from collections import Counter
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess

from .source_health_cache_sla_review import review, AuditHold

SCHEMA="heptatrader.fx-source-health-real-24cycle-qualification.v1"
PLAN_SCHEMA="heptatrader.research-source-cache-24-cycle-precommit.v1"
UNIT="heptatrader-health-cache25-ops-20261013.service"
EXPECTED_CODE_SHA={
    "source_readiness.py":"8cbedb6a5f792891f0141f7b2ae0da91a7738080cd14c45995436ed55473c309",
    "verified_sealed_readiness_cache.py":"1d1bdea5702d1fab8c9412eb2cbf0d224dd34d2736d4ab822e71bff5b9acb61c",
}
MIN_RESEARCH_SEALED_CYCLES=24
MAX_SOURCE_ASOF_AGE_MS=30000
EXPECTED_PAPER_PERIOD_SHA="61995fcedc2993d7ac33d2ed86c9613068c5a3ecd0d1c377e612639cd5a0958f"
EXPECTED_SOURCE_RULE={
    "schema":"heptatrader.quote-readiness.v1", "input_probes":24,
    "spacing_ms":60000,"horizon_ms":300000,"input_grace_ms":60000,
    "endpoint_tolerance_ms":5000,"path_gap_max_ms":30000,
    "minimum_usable":20,"maximum_missing":4,"source_ttl_ms":5000,
    "statistics_scope":"OVERLAPPING_OPERATIONAL_COVERAGE_NOT_PERFORMANCE",
}

class GateHold(ValueError):
    pass
def need(ok,reason):
    if not ok:raise GateHold(reason)
def sha(data):
    return hashlib.sha256(data).hexdigest()
def canonical(obj):
    return json.dumps(obj,sort_keys=True,separators=(",",":"),allow_nan=False).encode()
def read_strict(data):
    def unique(pairs):
        ans={}
        for key,val in pairs:
            need(key not in ans,"DUPLICATE_JSON_KEY")
            ans[key]=val
        return ans
    return json.loads(data,object_pairs_hook=unique,
                      parse_constant=lambda _:(_ for _ in ()).throw(GateHold("NONFINITE_JSON")))
def exact_private_bytes(file, bound=1024*1024):
    file=Path(file)
    need(file.is_absolute() and not file.is_symlink(),"PRIVATE_SOURCE_PATH")
    fd=os.open(file,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        a=os.fstat(fd)
        need(stat.S_ISREG(a.st_mode) and a.st_uid==os.geteuid()
             and a.st_nlink==1 and stat.S_IMODE(a.st_mode)&0o077==0
             and 0<a.st_size<=bound,"ORIGINAL_PRIVATE_SOURCE_SCOPE")
        parts=[]
        while sum(map(len,parts))<a.st_size:
            b=os.read(fd,min(128*1024,a.st_size-sum(map(len,parts))))
            need(bool(b),"ORIGINAL_PRIVATE_SOURCE_SHORT_READ")
            parts.append(b)
        b=os.fstat(fd)
        need((a.st_dev,a.st_ino,a.st_size,a.st_mtime_ns)==
             (b.st_dev,b.st_ino,b.st_size,b.st_mtime_ns),
             "ORIGINAL_PRIVATE_SOURCE_CHANGED_DURING_READ")
        return b"".join(parts)
    finally:
        os.close(fd)
def output_dir(root):
    r=Path(root)
    need(r.is_absolute() and not r.is_symlink() and r.is_dir()
         and r.stat().st_uid==os.geteuid() and
         stat.S_IMODE(r.stat().st_mode)==0o700,
         "OUTPUT_DIRECTORY_NOT_PRIVATE")
    return r
def write_once(path,bytes_):
    path=Path(path)
    output_dir(path.parent)
    fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,"wb") as f:
        f.write(bytes_);f.flush();os.fsync(f.fileno())
    dirfd=os.open(path.parent,os.O_DIRECTORY|os.O_RDONLY|os.O_NOFOLLOW)
    try:os.fsync(dirfd)
    finally:os.close(dirfd)

def verify_plan(plan_bytes, source_files):
    plan=read_strict(plan_bytes)
    need(plan.get("schema")==PLAN_SCHEMA
         and plan.get("planned_cycles")==MIN_RESEARCH_SEALED_CYCLES
         and plan.get("cpu_quota_percent")==25
         and plan.get("requested_cadence_seconds")==15
         and plan.get("sandbox_only") is True
         and plan.get("trade_authority") is False
         and plan.get("broker_orders_sent")==0,
         "SLA_PRECOMMIT_CHANGED")
    need(set(plan.get("source_sha256",{}))==set(EXPECTED_CODE_SHA)
         and plan["source_sha256"]==EXPECTED_CODE_SHA,
         "FROZEN_SOURCE_SET_CHANGED")
    for name,digest in EXPECTED_CODE_SHA.items():
        file=Path(source_files[name])
        need(file.is_absolute() and file.is_file() and not file.is_symlink()
             and sha(file.read_bytes())==digest,
             "CANDIDATE_MONITOR_CODE_CHANGED")
    require_time=plan.get("frozen_at_ms")
    need(type(require_time) is int and require_time>0,"FROZEN_PRECOMMIT_CLOCK")
    return plan

def qualify(plan_bytes,source_files,journal_bytes,last_status_bytes):
    plan=verify_plan(plan_bytes,source_files)
    lines=journal_bytes.splitlines()
    # Separate, already tested review: verifies 24 actual as-of/publish
    # timestamps, 30s SLA, complete archived source checksum counts and
    # every cache-hit rehash before reuse.
    result,events,cache=review(lines)
    need(len(events)==24 and events[0]["source_asof_ms"]>=plan["frozen_at_ms"],
         "PRECOMMIT_NOT_BEFORE_COLD_SOURCE_ASOF")
    need(result["true_frozen_30s_asof_interval_SLA_met"] is True
         and result["all_24_cycle_health_states_are_ready"] is True
         and result["max_stale_age_immediately_before_next_publish_ms"]
                <=MAX_SOURCE_ASOF_AGE_MS,
         "OPERATIONAL_SOURCE_AGE_OR_PROBE_RULE_FAILED")
    final=read_strict(last_status_bytes)
    need(final.get("schema")=="heptatrader.quote-readiness-status.v1"
         and final.get("state")=="READY_FOR_REVIEW_NOT_AUTHORIZATION"
         and final.get("usable",0)>=20 and final.get("unusable",100)<=4
         and final.get("at_ms")==events[-1]["source_asof_ms"]
         and final.get("automatic_trial_registration") is False
         and final.get("trading_authority") is False
         and final.get("orders_sent")==0,
         "FINAL_ORIGINAL_SOURCE_SNAPSHOT_NOT_CAUSAL_OR_READONLY")
    # Reconstruct the *exact same* original 24-probe eligibility rule,
    # including source freshness, rather than trusting a READY string.
    probes=final.get("probes")
    counts=final.get("counts")
    need(final.get("period_sha256")==EXPECTED_PAPER_PERIOD_SHA
         and final.get("rule")==EXPECTED_SOURCE_RULE
         and type(probes) is list and len(probes)==24
         and all(type(p) is dict and type(p.get("status")) is str for p in probes)
         and type(counts) is dict and
             counts==dict(Counter(p["status"] for p in probes))
         and type(final.get("unusable")) is int
         and final["usable"]==counts.get("USABLE",0)
         and final["unusable"]==24-final["usable"]
         and final.get("reasons")==[],
         "FINAL_MONITOR_PROBES_OR_PERIOD_NOT_ATTESTABLE")
    at=final["at_ms"]
    quote_at=final.get("latest_source_quote_ms")
    age=final.get("latest_quote_age_ms")
    need(type(at) is int and type(quote_at) is int and type(age) is int
         and age==at-quote_at and 0<=age<=EXPECTED_SOURCE_RULE["source_ttl_ms"],
         "FINAL_ORIGINAL_QUOTE_EXPIRED_OR_AGE_FORGED")
    # A proper source hash is *local* custody and cannot attest IB/exchange.
    need(type(final.get("source_prefixes")) is list
         and type(final.get("source_prefix_digest")) is str
         and final["source_prefix_digest"]==
             sha(canonical(final["source_prefixes"])),
         "SOURCE_PROVENANCE_DIGEST_CHANGED")
    return {
        "schema":SCHEMA,"result":"PASS_RESEARCH_ONLY_CACHE_FRESHNESS_NOT_PRODUCTION",
        "unit":UNIT,"precommitted_plan_sha256":sha(plan_bytes),
        "live_source_readiness_bytes_sha256":sha(last_status_bytes),
        "actual_journal_original_bytes_sha256":sha(journal_bytes),
        "candidate_program_sha256":EXPECTED_CODE_SHA,
        "original_new_readiness_identity":final.get("period_sha256"),
        "observed_cycles":len(events),
        "original_asof_time_of_final_cycle_ms":events[-1]["source_asof_ms"],
        "first_cold_assessment_ms":result["first_cold_assessment_ms"],
        "worst_warm_assessment_ms":result["warm_assessment_max_ms"],
        "worst_age_before_next_publish_ms":
            result["max_stale_age_immediately_before_next_publish_ms"],
        "continuous_snapshot_freshness_pass":True,
        "all_24_usable_source_probation_pass":True,
        "cache_full_archive_rechecks":cache["archive_inventories_full_sha256_reverified"],
        "cached_segment_reuse_count":cache["sealed_decoding_reused"],
        "cached_hit_bytes_sha_rechecks":cache["sealed_hits_bytes_reverified_at_use"],
        "unsealed_source_full_reparse_count":cache["unsealed_or_extra_strictly_reparsed"],
        "timing_and_cache_validation_not_an_ib_quote_signature":True,
        "actual_IB_execution_PnL_attested":False,
        "product_health_monitor_changed":False,
        "original_four_window_censored_decisions_changed":False,
        "ready_for_unattended_production_cutover":False,
        "native_training_or_model_promotion_authorized":False,
        "trade_authority":False,"broker_orders_sent":0,
    }

def run(output_root,plan_path,source_files,status_file,*,journal_data=None):
    root=output_dir(output_root)
    need(not any((root/name).exists() for name in
             ("original-journal.txt","independent-operational-audit.json")),
         "SLA_AUDIT_ALREADY_EXISTS_NO_OVERWRITE")
    pb=exact_private_bytes(plan_path,32*1024)
    sb=exact_private_bytes(status_file,256*1024)
    jb=(subprocess.check_output(["journalctl","--user","-u",UNIT,
             "--no-pager","-o","cat"],timeout=30)
        if journal_data is None else journal_data)
    need(len(jb)<512*1024 and jb, "ACTUAL_JOURNAL_MISSING_OR_OVERLONG")
    report=qualify(pb,source_files,jb,sb)
    report["audited_at_utc"]=datetime.now(timezone.utc).isoformat()
    report["auditor_source_sha256"]=sha(Path(__file__).read_bytes())
    write_once(root/"original-journal.txt",jb)
    write_once(root/"independent-operational-audit.json",canonical(report)+b"\n")
    return report
def main():
    p=argparse.ArgumentParser()
    for name in ("root","plan","monitor-code","cache-code","status"):
        p.add_argument("--"+name,type=Path,required=True)
    a=p.parse_args()
    r=run(a.root,a.plan,{
       "source_readiness.py":a.monitor_code,
       "verified_sealed_readiness_cache.py":a.cache_code},a.status)
    print(json.dumps({k:r[k] for k in (
        "result","observed_cycles","worst_age_before_next_publish_ms",
        "cache_full_archive_rechecks","trade_authority")},
        sort_keys=True))
if __name__=="__main__":main()
