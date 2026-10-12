"""One-time cache-vs-original source-health comparison at a PRE-FROZEN FX time.

Only read-only evidence: this instrument cannot alter the old trade decision,
monitor, quote TTL, source provenance, trial cohort or production identity.
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
import time

SCHEMA="heptatrader.fx-original-health-cache-fixedslot-shadow.v1"
PERIOD_SHA="61995fcedc2993d7ac33d2ed86c9613068c5a3ecd0d1c377e612639cd5a0958f"
ORIGINAL_PLAN_SHA="02aa4a2662b743203cc925d09d3250bcc0e02155cf5d00bb319b0526448aaa04"
ORIGINAL_SEAL_SHA="1f13ed5c7a8fcdc29cc0881bf5c7854c263876f5f89bb6bc87633d5b2b33860b"
ANCHOR_MS=1791857100000 # 2026-10-13 10:05:00 pocket4 UTC+8
CANARY_START_MS=ANCHOR_MS-320000 # 09:59:40
CAPTURE_MS=ANCHOR_MS+2000 # 10:05:02
WINDOW_START_MS=ANCHOR_MS+1000
WINDOW_END_MS=ANCHOR_MS+4500
MIN_PRECOMMIT_MS=30*60000
MAX_ASOF_AGE_MS=30000
KNOWN_STATUSES=frozenset(("USABLE","PATH_GAP","ENDPOINT_GAP","INPUT_UNAVAILABLE","OUTCOME_UNAVAILABLE"))
RULE={
  "schema":"heptatrader.quote-readiness.v1",
  "input_probes":24,"spacing_ms":60000,"horizon_ms":300000,
  "input_grace_ms":60000,"endpoint_tolerance_ms":5000,
  "path_gap_max_ms":30000,"minimum_usable":20,"maximum_missing":4,
  "source_ttl_ms":5000,
  "statistics_scope":"OVERLAPPING_OPERATIONAL_COVERAGE_NOT_PERFORMANCE",
}

class Hold(ValueError):
    pass

def require(test,reason):
    if not test:raise Hold(reason)

def now_ms():
    return time.time_ns()//1_000_000

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def canonical(obj):
    return json.dumps(obj,sort_keys=True,separators=(",",":"),allow_nan=False).encode()

def strict(raw):
    def once(pairs):
        d={}
        for k,v in pairs:
            require(k not in d,"DUPLICATE_JSON_KEY")
            d[k]=v
        return d
    return json.loads(raw,object_pairs_hook=once,
        parse_constant=lambda _:(_ for _ in ()).throw(Hold("NONFINITE_JSON")))

def private_dir(path,create=False):
    p=Path(path)
    require(p.is_absolute() and not p.is_symlink()
            and ".." not in p.parts,"UNTRUSTED_DIRECTORY")
    if create:
        require(not p.exists(),"OUTPUT_EXISTS")
        p.mkdir(mode=0o700,parents=True)
    s=p.stat()
    require(stat.S_ISDIR(s.st_mode) and s.st_uid==os.geteuid()
            and stat.S_IMODE(s.st_mode)==0o700,"DIRECTORY_NOT_PRIVATE")
    return p

def read_private(path,limit=256*1024):
    p=Path(path)
    require(p.is_absolute() and not p.is_symlink(),"SOURCE_LINK_OR_RELATIVE")
    fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        before=os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_uid==os.geteuid()
                and before.st_nlink==1 and not before.st_mode&0o077
                and 0<before.st_size<=limit,"SOURCE_OWNERSHIP_OR_SIZE")
        raw=b""
        while len(raw)<before.st_size:
            chunk=os.read(fd,min(65536,before.st_size-len(raw)))
            require(bool(chunk),"SOURCE_TRUNCATED")
            raw+=chunk
        after=os.fstat(fd)
        require((before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns)==
                (after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns),
                "SOURCE_CHANGED_DURING_READ")
        return raw
    finally:os.close(fd)

def read_pinned_research_code(path):
    """Developer source files are not private 0600; pin exact bytes instead."""
    p=Path(path)
    require(p.is_absolute() and not p.is_symlink(),"SOURCE_CODE_PATH_INVALID")
    fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        st=os.fstat(fd)
        require(stat.S_ISREG(st.st_mode) and st.st_uid==os.geteuid()
                and st.st_nlink==1 and 0<st.st_size<2*1024*1024,
                "SOURCE_CODE_IDENTITY_INVALID")
        raw=b""
        while len(raw)<st.st_size:
            b=os.read(fd,min(65536,st.st_size-len(raw)))
            require(bool(b),"SOURCE_CODE_TRUNCATED")
            raw+=b
        after=os.fstat(fd)
        require((st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns)==
                (after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns),
                "SOURCE_CODE_CHANGED_DURING_READ")
        return raw
    finally:os.close(fd)

def write_once(path,obj_or_raw):
    p=Path(path)
    private_dir(p.parent)
    raw=obj_or_raw if isinstance(obj_or_raw,bytes) else canonical(obj_or_raw)+b"\n"
    fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,"wb") as f:
        f.write(raw);f.flush();os.fsync(f.fileno())
    d=os.open(p.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:os.fsync(d)
    finally:os.close(d)

def frozen_prior(root):
    p=private_dir(root)
    plan_bytes=read_private(p/"plan.json")
    seal_bytes=read_private(p/"sealed.json")
    require((sha(plan_bytes),sha(seal_bytes))==(ORIGINAL_PLAN_SHA,ORIGINAL_SEAL_SHA),
            "ORIGINAL_PRECOMMIT_CHANGED")
    plan,seal=strict(plan_bytes),strict(seal_bytes)
    require(seal.get("plan_sha256")==ORIGINAL_PLAN_SHA
            and plan.get("period_sha256")==PERIOD_SHA
            and plan.get("trade_authority") is False
            and plan.get("automatic_training") is False
            and plan.get("model_promotion_allowed") is False
            and plan.get("not_native_train_v2_120min_spec") is True
            and plan.get("behavior_policy")=="NO_TRADE"
            and len(plan.get("anchors_ms",[]))==4
            and len(seal.get("windows",[]))==4
            and plan["anchors_ms"][3]==ANCHOR_MS
            and seal["windows"][3]["entry_first_ms"]==WINDOW_START_MS
            and seal["windows"][3]["entry_last_ms"]==WINDOW_END_MS,
            "ORIGINAL_WINDOW_OR_AUTHORITY_CHANGED")
    return plan

def health_quality(health,capture):
    reasons=[]
    if health.get("schema")!="heptatrader.quote-readiness-status.v1":
        reasons.append("WRONG_STATUS_SCHEMA")
    if health.get("period_sha256")!=PERIOD_SHA:
        reasons.append("WRONG_PAPER_PERIOD")
    if health.get("rule")!=RULE:
        reasons.append("ORIGINAL_24_PROBE_RULE_CHANGED")
    actual=health.get("probes")
    counts=health.get("counts")
    if (type(actual) is not list or len(actual)!=24
        or type(counts) is not dict or
        any(type(p) is not dict or p.get("status") not in KNOWN_STATUSES
            for p in actual)):
        reasons.append("PROBE_POPULATION_NOT_24")
    else:
        c=dict(Counter(p["status"] for p in actual))
        if (c!=counts or health.get("usable")!=c.get("USABLE",0)
            or health.get("unusable")!=24-c.get("USABLE",0)):
            reasons.append("SOURCE_PROBE_TOTAL_NOT_REPRODUCIBLE")
    original_prefixes=health.get("source_prefixes")
    if (type(original_prefixes) is not list
        or health.get("source_prefix_digest")!=sha(canonical(original_prefixes))):
        reasons.append("SOURCE_PREFIX_FINGERPRINT_CHANGED")
    stamp=health.get("at_ms")
    age=capture-stamp if type(stamp) is int else None
    if age is None or not 0<=age<=MAX_ASOF_AGE_MS:
        reasons.append("STATUS_SNAPSHOT_NOT_CAUSALLY_FRESH")
    quote=health.get("latest_source_quote_ms")
    lag=health.get("latest_quote_age_ms")
    if (type(quote) is not int or type(lag) is not int or type(stamp) is not int
        or stamp-quote!=lag or not 0<=lag<=RULE["source_ttl_ms"]):
        reasons.append("RECORDED_QUOTE_AGE_INVALID")
    if (health.get("state")!="READY_FOR_REVIEW_NOT_AUTHORIZATION"
        or health.get("reasons")!=[]
        or health.get("usable",0)<20 or health.get("unusable",24)>4):
        reasons.append("SOURCE_MONITOR_NOT_READY")
    if (health.get("automatic_trial_registration") is not False
        or health.get("trading_authority") is not False
        or health.get("orders_sent")!=0):
        reasons.append("RESEARCH_SOURCE_AUTHORITY_CHANGED")
    return sorted(set(reasons)),age

def freeze(*,original_root,output_root,baseline_path,cache_path,
           source_monitor_code,cache_code,config_path,at=None):
    current=now_ms() if at is None else at
    prior=frozen_prior(original_root)
    require(current<CANARY_START_MS-MIN_PRECOMMIT_MS,
            "CANARY_NOT_REGISTERED_BEFORE_FUTURE_WINDOW")
    require(str(baseline_path)==prior["readiness_file"]
            and str(config_path)==prior["config_file"]
            and str(cache_path).startswith(str(output_root)+"/"),
            "STATUS_SOURCE_PATH_NOT_ISOLATED")
    read_private(config_path,2*1024*1024)
    read_pinned_research_code(source_monitor_code)
    read_pinned_research_code(cache_code)
    root=private_dir(output_root,create=True)
    manifest={
        "schema":SCHEMA+".plan.v1",
        "registered_at_ms":current,
        "original_plan_sha256":ORIGINAL_PLAN_SHA,
        "original_seal_sha256":ORIGINAL_SEAL_SHA,
        "original_root":str(original_root),
        "original_health_file":str(baseline_path),
        "canary_health_file":str(cache_path),
        "fixed_anchor_ms":ANCHOR_MS,
        "canary_start_ms":CANARY_START_MS,
        "capture_exact_target_ms":CAPTURE_MS,
        "capture_permitted_window_ms":[WINDOW_START_MS,WINDOW_END_MS],
        "source_config_file":str(config_path),
        "source_config_sha256":sha(read_private(config_path)),
        "source_monitor_code_sha256":sha(read_pinned_research_code(source_monitor_code)),
        "verified_cache_code_sha256":sha(read_pinned_research_code(cache_code)),
        "source_monitor_code_file":str(source_monitor_code),
        "verified_cache_code_file":str(cache_code),
        "research_comparator_code_sha256":sha(Path(__file__).read_bytes()),
        "exact_refresh_seconds":15,
        "exact_max_cycles":24,
        "cpu_quota_percent":25,
        "cache_enabled_under_sandbox_only":True,
        "source_rules_not_relaxed":True,
        "original_trial_not_changed":True,
        "source_signature_or_native_owner_proven":False,
        "only_compare_source_health_not_execute":True,
        "broker_queries_sent":0,"broker_orders_sent":0,
        "trade_authority":False,"model_promotion_allowed":False,
    }
    write_once(root/"plan.json",manifest)
    return {"state":"FIXED_1005_CACHE_CANARY_COMPARISON_PRESEALED",
            "registered_at_ms":current,
            "future_anchor_utc":datetime.fromtimestamp(ANCHOR_MS/1000,timezone.utc).isoformat(),
            "plan_sha256":sha(read_private(root/"plan.json")),
            "no_original_trial_mutation":True,"broker_orders_sent":0}

def verify_plan(root):
    root=private_dir(root)
    plan=strict(read_private(root/"plan.json"))
    require(plan.get("schema")==SCHEMA+".plan.v1"
            and plan.get("original_plan_sha256")==ORIGINAL_PLAN_SHA
            and plan.get("original_seal_sha256")==ORIGINAL_SEAL_SHA
            and plan.get("fixed_anchor_ms")==ANCHOR_MS
            and plan.get("canary_start_ms")==CANARY_START_MS
            and plan.get("capture_exact_target_ms")==CAPTURE_MS
            and plan.get("capture_permitted_window_ms")==[WINDOW_START_MS,WINDOW_END_MS]
            and plan.get("exact_refresh_seconds")==15
            and plan.get("exact_max_cycles")==24
            and plan.get("cpu_quota_percent")==25
            and plan.get("cache_enabled_under_sandbox_only") is True
            and plan.get("trade_authority") is False
            and plan.get("model_promotion_allowed") is False
            and plan.get("broker_orders_sent")==0
            and plan.get("original_trial_not_changed") is True
            and plan.get("research_comparator_code_sha256")==sha(Path(__file__).read_bytes()),
            "FIXED_CANARY_OR_AUTHORITY_CHANGED")
    prior=frozen_prior(Path(plan["original_root"]))
    require(plan["original_health_file"]==prior["readiness_file"]
            and plan["source_config_file"]==prior["config_file"]
            and sha(read_private(Path(plan["source_config_file"])))==plan["source_config_sha256"]
            and sha(read_pinned_research_code(Path(plan["source_monitor_code_file"])))==
                plan["source_monitor_code_sha256"]
            and sha(read_pinned_research_code(Path(plan["verified_cache_code_file"])))==
                plan["verified_cache_code_sha256"]
            and plan["canary_health_file"].startswith(str(root)+"/"),
            "CACHED_MONITOR_SOURCE_OR_CONFIG_CHANGED")
    return plan

def compare_once(root,*,captured_at=None):
    root=private_dir(root)
    plan=verify_plan(root)
    at=now_ms() if captured_at is None else captured_at
    require(not (root/"comparison.json").exists(),"COMPARISON_ALREADY_ATTEMPTED_NO_RETRY")
    report={
        "schema":SCHEMA+".comparison.v1",
        "original_plan_sha256":ORIGINAL_PLAN_SHA,
        "original_seal_sha256":ORIGINAL_SEAL_SHA,
        "canary_precommit_sha256":sha(read_private(root/"plan.json")),
        "original_anchor_ms":ANCHOR_MS,
        "attempt_at_ms":at,
        "canary_is_shadow_not_original_study_input":True,
        "source_ib_signed":False,
        "native_owner_or_witness_enrolled":False,
        "original_trade_entry_eligibility_not_reclassified":True,
        "trade_authority":False,"broker_queries_sent":0,"broker_orders_sent":0,
    }
    if not WINDOW_START_MS<=at<=WINDOW_END_MS:
        report.update(state="MISSED_NO_REPLAY",reason="OUTSIDE_FROZEN_DECISION_WINDOW")
        write_once(root/"comparison.json",report)
        return report
    for kind,key in (("baseline","original_health_file"),("canary","canary_health_file")):
        try:
            raw=read_private(Path(plan[key]))
            status=strict(raw)
            reasons,age=health_quality(status,at)
            # Preserve BOTH raw snapshots even if status is HOLD/stale.
            write_once(root/(kind+"-original-status.json"),raw)
            report[kind]={
                "captured_sha256":sha(raw),
                "source_producer_at_ms":status.get("at_ms"),
                "age_at_comparison_ms":age,
                "source_probes_usable":status.get("usable"),
                "source_state":status.get("state"),
                "healthy_at_exact_time":not bool(reasons),
                "reasons":reasons,
            }
        except Exception as err:
            report[kind]={
                "healthy_at_exact_time":False,
                "reasons":["MISSING_OR_UNTRUSTED_"+type(err).__name__],
            }
    durable=now_ms() if captured_at is None else captured_at+100
    report["comparison_durable_at_ms"]=durable
    if durable>WINDOW_END_MS:
        report["state"]="LATE_CUSTODY_NOT_AN_AT_DECISION_WITNESS"
    else:
        report["state"]="TWO_READONLY_SOURCE_HEALTH_SNAPSHOTS_PRESERVED"
    write_once(root/"comparison.json",report)
    return report

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--mode",required=True,choices=("preseal","verify","capture"))
    p.add_argument("--original-root",type=Path)
    p.add_argument("--root",required=True,type=Path)
    for n in ("baseline","cache","source-monitor-code","cache-code","config"):
        p.add_argument("--"+n,type=Path)
    a=p.parse_args()
    os.umask(0o077)
    if a.mode=="preseal":
        require(all((a.original_root,a.baseline,a.cache,a.source_monitor_code,
                     a.cache_code,a.config)),"CANARY_PRESEAL_PARAMETERS_REQUIRED")
        result=freeze(original_root=a.original_root,output_root=a.root,
                      baseline_path=a.baseline,cache_path=a.cache,
                      source_monitor_code=a.source_monitor_code,
                      cache_code=a.cache_code,config_path=a.config)
    elif a.mode=="verify":
        plan=verify_plan(a.root)
        result={"state":"EXACT_PRECOMMITTED_READONLY_COMPARISON_VALID",
                "future_anchor_ms":ANCHOR_MS,
                "original_trial_changed":False,"broker_orders_sent":0}
    else:
        result=compare_once(a.root)
    print(json.dumps(result,sort_keys=True),flush=True)

if __name__=="__main__":
    main()
