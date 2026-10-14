"""Standalone, stdlib-only independent audit of the NEW pocket4 12-slot cache warmup.

Works on a physically separate host with byte-exact private copies of the
frozen pocket4 receipts. DOES NOT have independent IB market-data custody.
No imports from the generating research runner or an IB broker/trader client.
"""
from __future__ import annotations
import argparse
from collections import Counter
from datetime import datetime,timezone
import hashlib,json,os,stat
from pathlib import Path

SCHEMA="heptatrader.fx-cache-cold-prewarm-independent-v1"
STUDY="heptatrader.fx-cache-prewarm-source-health.v1"
PLAN_PIN="91f2daa169e0e001f761e826f80a55db73af3daf5fee3f192a35e8632dd3b096"
CODE_PIN="c1d5f7bba41339492e362aee467e2c1c305cd3ed691e3d6f9872eed2910756bc"
N=12
RULE={"schema":"heptatrader.quote-readiness.v1","input_probes":24,
      "spacing_ms":60000,"horizon_ms":300000,
      "input_grace_ms":60000,"endpoint_tolerance_ms":5000,
      "path_gap_max_ms":30000,"minimum_usable":20,
      "maximum_missing":4,"source_ttl_ms":5000,
      "statistics_scope":"OVERLAPPING_OPERATIONAL_COVERAGE_NOT_PERFORMANCE"}
class AuditHold(ValueError):pass

def require(ok,msg):
    if not ok:raise AuditHold(msg)
def digest(b):
    return hashlib.sha256(b).hexdigest()
def canonical(o):
    return json.dumps(o,sort_keys=True,separators=(",",":"),allow_nan=False).encode()
def parse(b):
    def pairs(p):
        o={}
        for k,v in p:
            require(k not in o,"DUPLICATE_JSON_KEY")
            o[k]=v
        return o
    return json.loads(b,object_pairs_hook=pairs,
        parse_constant=lambda _:(_ for _ in ()).throw(AuditHold("NONFINITE_JSON")))
def private(path,cap=1048576):
    p=Path(path)
    require(p.is_absolute() and ".." not in p.parts and not p.is_symlink(),
            "SOURCE_RELATIVE_OR_SYMLINK")
    fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        s=os.fstat(fd)
        require(stat.S_ISREG(s.st_mode) and s.st_uid==os.geteuid()
                and s.st_nlink==1 and s.st_mode&0o077==0
                and 0<s.st_size<=cap,"PRIVATE_REGULAR_SOURCE_REQUIRED")
        b=b""
        while len(b)<s.st_size:
            buf=os.read(fd,min(256*1024,s.st_size-len(b)))
            require(bool(buf),"SHORT_SOURCE_READ")
            b+=buf
        after=os.fstat(fd)
        require((s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns)==
                (after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns),
                "SOURCE_CHANGED_DURING_READ")
        return b
    finally:os.close(fd)
def fetch(root,name,cap=1048576):
    b=private(Path(root)/name,cap)
    return parse(b),digest(b)

def check_status(s,period,started,published):
    require(s.get("schema")=="heptatrader.quote-readiness-status.v1"
            and s.get("period_sha256")==period
            and s.get("rule")==RULE,"ORIGINAL_SOURCE_POLICY_CHANGED")
    probes=s.get("probes")
    require(type(probes) is list and len(probes)==24 and
            all(type(p) is dict and type(p.get("status")) is str
                for p in probes),"ORIGINAL_24_PROBES_MISSING")
    counts=dict(Counter(p["status"] for p in probes))
    require(counts==s.get("counts")
            and s.get("usable")==counts.get("USABLE",0)
            and s.get("unusable")==24-counts.get("USABLE",0),
            "ORIGINAL_PROBE_COUNT_FALSIFIED")
    src=s.get("source_prefixes")
    require(type(src) is list and digest(canonical(src))==
            s.get("source_prefix_digest"),"ORIGINAL_PREFIX_HASH_MISMATCH")
    stamp=s.get("at_ms")
    require(type(stamp) is int and type(started) is int and
            type(published) is int and
            started<=stamp<=started+3000 and stamp<=published,
            "ORIGINAL_ASOF_TIME_INVALID")
    latest=s.get("latest_source_quote_ms")
    age=s.get("latest_quote_age_ms")
    require(type(latest) is int and type(age) is int and stamp-latest==age,
            "BROKER_QUOTE_AGE_NOT_BOUND")
    require(s.get("automatic_trial_registration") is False
            and s.get("trading_authority") is False and
            s.get("orders_sent")==0,"ORIGINAL_SOURCE_AUTHORITY_VIOLATION")
    original_ready=(s.get("state")=="READY_FOR_REVIEW_NOT_AUTHORIZATION")
    require(s.get("state") in ("READY_FOR_REVIEW_NOT_AUTHORIZATION",
                              "HOLD_NEW_FUSION_WINDOW"),
            "UNKNOWN_SOURCE_STATE")
    if original_ready:
        require(s.get("reasons")==[] and s["usable"]>=20
                and s["unusable"]<=4 and 0<=age<=5000,
                "FALSE_ORIGINAL_READY")
    return {"original_ready":original_ready,"source_asof_ms":stamp,
            "age_at_publication_ms":published-stamp}

def audit(root,out,*,require_final=True):
    root=Path(root);out=Path(out)
    require(root.is_absolute() and not root.is_symlink()
            and root.stat().st_uid==os.geteuid()
            and stat.S_IMODE(root.stat().st_mode)==0o700,
            "AUDIT_ROOT_NOT_PRIVATE")
    plan,ph=fetch(root,"plan.json",16384)
    require(ph==PLAN_PIN and plan.get("schema")==STUDY+".plan.v1"
            and plan.get("research_code_sha256")==CODE_PIN
            and plan.get("source_readiness_rule")==RULE
            and plan.get("count")==N
            and plan.get("spacing_ms")==15000
            and plan.get("maximum_prepublication_original_age_ms")==30000
            and plan.get("maximum_decision_start_lateness_ms")==3000
            and plan.get("production_owner_witness_admission",False) is False
            and plan.get("cutover_production_monitor") is False
            and plan.get("trade_authority") is False
            and plan.get("original_monitor_sha256")==
                "63bcef1a6d8bc22e3c2fb10d1dcebc1457559538e6a959004e61eeb88c09c320"
            and plan.get("source_cache_must_verify_all_sealed_bytes_every_cycle") is True
            and plan.get("first_original_cache480_missed_cycle_is_preserved") is True,
            "FROZEN_ORIGINAL_V2_PLAN_CHANGED")
    targets=plan.get("cycle_schedule_ms")
    require(type(targets) is list and len(targets)==N
            and all(type(x) is int for x in targets)
            and all(b-a==15000 for a,b in zip(targets,targets[1:])),
            "FROZEN_ORIGINAL_V2_TIMELINE_CHANGED")
    warm,wh=fetch(root,"prewarm-receipt.json",32768)
    warm_status,warm_sha=fetch(root,"prewarm-source.json")
    require(warm.get("schema")==STUDY+".prewarm.v1"
            and warm.get("source_bytes_sha256")==warm_sha
            and warm.get("prewarm_within_frozen_before_first_deadline") is True
            and warm.get("started_ms")<=warm.get("finished_ms") <=
                plan["prewarm_must_finish_before_first_ms"]
            and warm.get("broker_orders_sent")==0
            and warm.get("trade_authority") is False
            and warm.get("source_health_issues")==[],
            "PREWARM_NOT_DURABLY_ELIGIBLE")
    warm_review=check_status(
        warm_status,plan["original_period_sha256"],
        warm["started_ms"],warm["finished_ms"])
    require(warm_review["original_ready"] is True
            and warm["original_source_state"]=="READY_FOR_REVIEW_NOT_AUTHORIZATION",
            "WARM_SOURCE_NOT_ORIGINALLY_READY")
    retained=[]
    ready=hold=missing=0
    ages=[]
    since_previous=[]
    prev_source_asof=None
    chain="0"*64
    for i,target in enumerate(targets):
        row,r_sha=fetch(root,f"slot-{i:02d}-receipt.json",16384)
        require(row.get("schema")==STUDY+".slot.v1"
            and row.get("index")==i and row.get("target_ms")==target
            and row.get("prewarm_receipt_sha256")==wh
            and row.get("previous_receipt_sha256")==chain
            and row.get("broker_orders_sent")==0
            and row.get("trade_authority") is False
            and type(row.get("assessment_started_ms")) is int
            and type(row.get("record_sealed_at_ms")) is int
            and row["record_sealed_at_ms"]>=row["assessment_started_ms"],
            "ORIGINAL_CYCLE_CHAIN_OR_IDENTITY_CHANGED")
        sha=row.get("source_status_sha256")
        if sha is None:
            require(row["state"] in (
               "CENSORED_PREWARM_NOT_ELIGIBLE","CENSORED_FIXED_SLOT_MISSED",
               "HOLD_ORIGINAL_SOURCE_READ_FAILURE")
               and row.get("source_ready") is False
               and not (root/f"slot-{i:02d}-original-status.json").exists(),
               "UNQUALIFIED_CYCLE_FAKE_READY")
            missing+=1
        else:
            s,s_sha=fetch(root,f"slot-{i:02d}-original-status.json")
            require(s_sha==sha,"ORIGINAL_SOURCE_STATUS_HASH_CHANGED")
            require(row["assessment_started_ms"]<=row["published_ms"]
                    <=row["record_sealed_at_ms"],
                    "ORIGINAL_PUBLICATION_AFTER_RECEIPT")
            found=check_status(s,plan["original_period_sha256"],
                               row["assessment_started_ms"],row["published_ms"])
            source_age=found["age_at_publication_ms"]
            issues=[]
            if source_age>30000:
                issues.append("ORIGINAL_STATUS_STALE_AT_PUBLICATION")
            require(row.get("status_age_at_publication_ms")==source_age
                    and row.get("source_original_asof_ms")==found["source_asof_ms"],
                    "ORIGINAL_TIME_FIELDS_CHANGED")
            if found["original_ready"] and not issues:
                require(row.get("state")=="SOURCE_READY_RESEARCH_ONLY"
                        and row.get("source_ready") is True and
                        row.get("issues")==[],
                        "REAL_SOURCE_READY_NOT_CORRECTLY_CLASSIFIED")
                ready+=1
            else:
                expected_state=("SOURCE_UNQUALIFIED_RETAINED" if issues
                                else "SOURCE_HOLD_RETAINED")
                require(row.get("source_ready") is False and
                        row.get("state")==expected_state and
                        row.get("issues")==issues,
                        "REAL_SOURCE_HOLD_SILENTLY_UPGRADED")
                hold+=1
            ages.append(source_age)
            if type(prev_source_asof) is int:
                since_previous.append(row["published_ms"]-prev_source_asof)
            prev_source_asof=found["source_asof_ms"]
        chain=r_sha
        retained.append({"index":i,"state":row["state"],
                         "source_ready":row["source_ready"],
                         "receipt_sha256":r_sha})
    final,fh=fetch(root,"final.json",128*1024)
    require(not require_final or final.get("schema")==STUDY+".final.v1",
            "ORIGINAL_FINAL_MISSING")
    require(final.get("schema")==STUDY+".final.v1"
            and final.get("frozen_plan_sha256")==ph
            and final.get("prewarm_receipt_sha256")==wh
            and final.get("count")==N
            and final.get("slot_results")==retained
            and final.get("valid_original_status_count")==ready+hold
            and final.get("operational_ready_count")==ready
            and final.get("max_original_asof_to_own_publication_ms")==max(ages or [0])
            and final.get("max_original_asof_to_next_actual_publication_ms")==
                (max(since_previous) if since_previous else None)
            and final.get("production_monitor_cutover") is False
            and final.get("trade_authority") is False
            and final.get("broker_queries_or_orders_sent")==0,
            "ORIGINAL_FINAL_DOES_NOT_REPRODUCE")
    report={
       "schema":SCHEMA,"verified_at_utc":datetime.now(timezone.utc).isoformat(),
       "verifier_hostname":os.uname().nodename,
       "verifier_is_not_a_separate_IB_market_source":True,
       "pinned_plan_sha256":ph,
       "original_final_sha256":fh,
       "original_prewarm_sha256":wh,
       "cold_first_original_full_SHA_was_preheated_before_slot_zero":True,
       "slot_count":N,"source_ready_count":ready,
       "genuine_source_hold_or_unqualified_count":hold,
       "missing_source_status_count":missing,
       "max_status_age_at_publication_ms":max(ages or [0]),
       "max_previous_asof_to_next_publication_ms":(
           max(since_previous) if since_previous else None),
       "all_frozen_slots_without_source_migration":True,
       "original_480_cycle_failure_untouched":True,
       "source_checksum_chain_verified":True,
       "production_health_monitor_cutover_authorized":False,
       "independent_IB_quote_source_proven":False,
       "new_model_or_original_trading_authority":False,
       "broker_requests_or_orders_sent":0,
    }
    require(out.is_absolute() and not out.exists() and not out.is_symlink()
            and out.parent.is_dir() and out.parent.stat().st_uid==os.geteuid()
            and stat.S_IMODE(out.parent.stat().st_mode)==0o700,
            "PRIVATE_OUTPUT_MUST_BE_NEW")
    raw=canonical(report)+b"\n"
    fd=os.open(out,os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_WRONLY,0o600)
    with os.fdopen(fd,"wb") as f:
        f.write(raw);f.flush();os.fsync(f.fileno())
    d=os.open(out.parent,os.O_DIRECTORY|os.O_RDONLY|os.O_NOFOLLOW)
    try:os.fsync(d)
    finally:os.close(d)
    return report

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--root",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True)
    a=p.parse_args()
    r=audit(a.root,a.output)
    print(json.dumps({
      "state":"ORIGINAL_TWELVE_PREWARM_SLOTS_INDEPENDENTLY_RECOMPUTED",
      "ready":r["source_ready_count"],
      "missing":r["missing_source_status_count"],
      "max_next_status_age_ms":r["max_previous_asof_to_next_publication_ms"],
      "production_monitor_cutover_authorized":False,
      "broker_orders_sent":0
    },sort_keys=True))
if __name__=="__main__":main()
