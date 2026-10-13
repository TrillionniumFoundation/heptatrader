"""Off-host, stdlib-only replay of the FIRST THREE frozen FX research losses.

Only verifies research provenance and contemporaneous captured health. No broker
credentials/market-data client, no model fitting, no order or native Owner grant.
The fourth window (10:05) remains future at initial verification.
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

SCHEMA="heptatrader.fx-first-three-original-crosshost-censorship.v1"
TRIAL="heptatrader.fx-original-future-price-cost-calendar.v1"
CUSTODY="heptatrader.fx-forward-original-health-custody.v1"
PLAN_SHA="02aa4a2662b743203cc925d09d3250bcc0e02155cf5d00bb319b0526448aaa04"
SEAL_SHA="1f13ed5c7a8fcdc29cc0881bf5c7854c263876f5f89bb6bc87633d5b2b33860b"
HORIZONS=(15,30,60,120)
KNOWN_STATUSES={"USABLE","PATH_GAP","ENDPOINT_GAP","INPUT_UNAVAILABLE","OUTCOME_UNAVAILABLE"}
PERIOD="61995fcedc2993d7ac33d2ed86c9613068c5a3ecd0d1c377e612639cd5a0958f"
DECISION_REJECT="EXCEPTION_Hold_ORIGINAL_READINESS_HOLD_OR_STALE"
MATURITY_REJECT="NO_ELIGIBLE_ORIGINAL_PREACTION"
STALE_REASON="READINESS_SNAPSHOT_STALE_OR_FUTURE"
RULE={"schema":"heptatrader.quote-readiness.v1",
      "input_probes":24,"spacing_ms":60000,"horizon_ms":300000,
      "input_grace_ms":60000,"endpoint_tolerance_ms":5000,
      "path_gap_max_ms":30000,"minimum_usable":20,"maximum_missing":4,
      "source_ttl_ms":5000,
      "statistics_scope":"OVERLAPPING_OPERATIONAL_COVERAGE_NOT_PERFORMANCE"}

class CrosshostHold(ValueError):pass
def demand(condition,code):
    if not condition:raise CrosshostHold(code)
def sha(raw):return hashlib.sha256(raw).hexdigest()
def canonical(o):return json.dumps(o,sort_keys=True,separators=(",",":"),allow_nan=False).encode()
def no_duplicate(pairs):
    d={}
    for k,v in pairs:
        demand(k not in d,"DUPLICATE_JSON_KEY")
        d[k]=v
    return d
def strict(raw):
    try:
        return json.loads(raw,object_pairs_hook=no_duplicate,
             parse_constant=lambda x:(_ for _ in ()).throw(CrosshostHold("NONFINITE_JSON")))
    except (ValueError,UnicodeError,TypeError) as e:
        if isinstance(e,CrosshostHold):raise
        raise CrosshostHold("INVALID_JSON") from e
def private_dir(path):
    path=Path(path)
    demand(path.is_absolute() and ".." not in path.parts and not path.is_symlink(),
           "UNSAFE_DIR_PATH")
    s=path.stat()
    demand(stat.S_ISDIR(s.st_mode) and s.st_uid==os.geteuid()
           and s.st_mode&0o077==0,"EVIDENCE_DIRECTORY_NOT_PRIVATE")
    return path
def read_file(path,size=65536):
    path=Path(path)
    demand(path.is_absolute() and not path.is_symlink(),"RELATIVE_OR_SYMLINK_SOURCE")
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        s=os.fstat(fd)
        demand(stat.S_ISREG(s.st_mode) and s.st_uid==os.geteuid()
               and s.st_nlink==1 and s.st_mode&0o077==0
               and 0<s.st_size<=size,"UNSAFE_OR_OVERSIZE_SOURCE")
        chunks=[];pos=0
        while pos<s.st_size:
            c=os.read(fd,min(65536,s.st_size-pos))
            demand(bool(c),"SOURCE_SHORT_READ")
            chunks.append(c);pos+=len(c)
        e=os.fstat(fd)
        demand((s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns)==
               (e.st_dev,e.st_ino,e.st_size,e.st_mtime_ns),
               "SOURCE_CHANGED_DURING_READ")
        return b"".join(chunks)
    finally:os.close(fd)

def evidence(path, size=65536):
    raw=read_file(path,size)
    return strict(raw),sha(raw)

def validate_anchor_schedule(plan,seal,pretrial):
    demand(plan.get("schema")==TRIAL and
           seal.get("schema")==TRIAL+".sealed.v1" and
           seal.get("plan_sha256")==PLAN_SHA and
           plan.get("period_sha256")==PERIOD and
           plan.get("behavior_policy")=="NO_TRADE" and
           plan.get("trade_authority") is False and
           plan.get("broker_orders_sent")==0 and
           plan.get("automatic_training") is False and
           plan.get("model_promotion_allowed") is False and
           plan.get("not_native_train_v2_120min_spec") is True and
           plan.get("independent_horizons_ms")==[h*60000 for h in HORIZONS] and
           plan.get("source_minimum_usable")==20 and
           plan.get("source_maximum_missing")==4 and
           plan.get("source_freshness_age_max_ms")==30000,
           "ORIGINAL_RESEARCH_PLAN_SCOPE_CHANGED")
    anchors=plan.get("anchors_ms")
    demand(type(anchors) is list and len(anchors)==4 and
           all(type(a) is int and a%300000==0 for a in anchors) and
           all(b-a==195*60000 for a,b in zip(anchors,anchors[1:])),
           "ORIGINAL_ANCHORS_CHANGED")
    demand(len(seal.get("windows",[]))==4 and
           all(w.get("index")==i and w.get("anchor_ms")==anchors[i] and
               w.get("entry_first_ms")==anchors[i]+1000 and
               w.get("entry_last_ms")==anchors[i]+4500 and
               w.get("mature_at_ms")==[anchors[i]+h*60000 for h in HORIZONS]
               for i,w in enumerate(seal["windows"])),
           "ORIGINAL_SEALED_WINDOWS_CHANGED")
    checked=pretrial.get("checked_at_utc")
    demand(pretrial.get("schema")=="heptatrader.fx-research-crosshost-preseal-copy.v1"
           and pretrial.get("plan_sha256")==PLAN_SHA
           and pretrial.get("seal_sha256")==SEAL_SHA
           and pretrial.get("all_windows_future_at_copy") is True
           and pretrial.get("production_witness_enrolled") is False
           and pretrial.get("trade_authority") is False and
           type(checked) is str,"PRETRIAL_CROSSHOST_RECORD_CHANGED")
    date=datetime.fromisoformat(checked)
    demand(date.tzinfo is not None and date.timestamp()*1000 < anchors[0],
           "PRETRIAL_CUSTODY_NOT_BEFORE_FIRST_WINDOW")
    return anchors

def inspect_health(index,anchor,custody,health):
    start=custody.get("attempt_started_at_ms")
    demand(custody.get("schema")==CUSTODY+".snapshot-attempt.v1" and
           custody.get("window")==index and custody.get("anchor_ms")==anchor and
           type(start) is int and anchor+1000<=start<=anchor+4500 and
           custody.get("state")=="HOLD" and
           custody.get("reasons")==[STALE_REASON] and
           custody.get("broker_orders_sent")==0 and
           custody.get("trade_authority") is False and
           custody.get("no_retroactive_relabel_of_original_trial") is True,
           "FROZEN_CUSTODY_ATTEMPT_CHANGED")
    demand(custody.get("original_study_entry_readiness_proven") is False and
           custody.get("source_health_status_sha256"),"CUSTODY_CONFERS_NO_AUTHORITY")
    demand(health.get("schema")=="heptatrader.quote-readiness-status.v1" and
           health.get("period_sha256")==PERIOD and
           health.get("rule")==RULE and
           health.get("state")=="READY_FOR_REVIEW_NOT_AUTHORIZATION" and
           health.get("reasons")==[] and
           health.get("automatic_trial_registration") is False and
           health.get("trading_authority") is False and
           health.get("orders_sent")==0,
           "CAPTURED_SOURCE_MONITOR_CHANGED")
    probes=health.get("probes")
    counts=health.get("counts")
    demand(type(probes) is list and len(probes)==24 and
           type(counts) is dict and
           all(type(x) is dict and x.get("status") in KNOWN_STATUSES for x in probes),
           "HEALTH_PROBES_NOT_AUTHENTICATED")
    expected=dict(Counter(p["status"] for p in probes))
    demand(expected==counts and
           health.get("usable")==expected.get("USABLE",0) and
           health.get("unusable")==24-expected.get("USABLE",0) and
           health.get("usable")>=20,
           "HEALTH_COUNTS_NOT_REPRODUCIBLE")
    original_prefixes=health.get("source_prefixes")
    demand(type(original_prefixes) is list and
           sha(canonical(original_prefixes))==health.get("source_prefix_digest"),
           "HEALTH_ORIGINAL_PREFIX_DIGEST_CHANGED")
    at=health.get("at_ms")
    source=health.get("latest_source_quote_ms")
    latest_age=health.get("latest_quote_age_ms")
    demand(type(at) is int and type(source) is int and
           type(latest_age) is int and at-source==latest_age
           and 0<=latest_age<=5000 and
           custody.get("readiness_health_at_ms")==at and
           custody.get("recomputed_source_usable_probes")==health["usable"],
           "CAPTURED_ORIGINAL_QUOTE_CLOCK_CHANGED")
    exact_age=start-at
    demand(exact_age>30000 and exact_age<=120000,
           "HEALTH_SNAPSHOT_NOT_ACTUALLY_STALE")
    return {"original_readiness_status":"READY_BUT_STALE_FOR_30S_DECISION",
            "asof_age_at_frozen_decision_ms":exact_age,
            "original_usable_probes":health["usable"],
            "original_missing_probes":health["unusable"],
            "original_quote_age_at_status_ms":latest_age,
            "original_status_sha256":custody["source_health_status_sha256"],
            "readiness_custody_sealed_broker_authority":False}

def verify(root, *,require_three_closed=True):
    root=private_dir(root)
    plan,p_sha=evidence(root/"plan.json")
    seal,s_sha=evidence(root/"sealed.json")
    demand((p_sha,s_sha)==(PLAN_SHA,SEAL_SHA),"CROSSHOST_FROZEN_PLAN_SHA_CHANGED")
    pretrial,_=evidence(root/"pretrial-readback.json")
    anchors=validate_anchor_schedule(plan,seal,pretrial)
    final=[]
    for i in (0,1,2):
        cell=private_dir(root/("window-%02d"%i))
        decision,decision_sha=evidence(cell/"decision-attempt.json")
        a=anchors[i]
        dtime=decision.get("attempt_recorded_at_ms")
        demand(decision.get("schema")==TRIAL+".attempt.v1" and
               decision.get("stage")=="decision" and
               decision.get("state")=="CENSORED" and
               decision.get("reason_code")==DECISION_REJECT and
               type(dtime) is int and a+1000<=dtime<=a+4500 and
               decision.get("broker_orders_sent")==0 and
               decision.get("trade_authority") is False and
               decision.get("automatic_retry_allowed") is False and
               decision.get("model_promotion_allowed") is False and
               decision.get("historical_price_or_future_quote_backfilled") is False and
               not (cell/"preaction.json").exists(),
               "ORIGINAL_CENSORED_DECISION_RELABELED")
        outcomes=[]
        for h in HORIZONS:
            due=a+h*60000
            event,phash=evidence(cell/("maturity-%03d-attempt.json"%h))
            at=event.get("attempt_recorded_at_ms")
            demand(event.get("schema")==TRIAL+".attempt.v1" and
                   event.get("stage")=="maturity-%03d"%h and
                   event.get("state")=="CENSORED" and
                   event.get("reason_code")==MATURITY_REJECT and
                   type(at) is int and due+6500<=at<=due+15000 and
                   event.get("automatic_retry_allowed") is False and
                   event.get("historical_price_or_future_quote_backfilled") is False and
                   event.get("broker_orders_sent")==0 and
                   event.get("trade_authority") is False and
                   event.get("model_promotion_allowed") is False and
                   not (cell/("maturity-%03d.json"%h)).exists(),
                   "MATURITY_CENSORED_RELABEL_OR_BACKFILL")
            outcomes.append({"horizon_minutes":h,
                             "original_maturity_attempt_sha256":phash,
                             "state":"CENSORED","label_created":False})
        health=None
        if i==0:
            demand(not (root/"window-00-health-original.json").exists()
                   and not (root/"window-00-health-attempt.json").exists(),
                   "OLD_UNATTESTED_WINDOW_ZERO_CANNOT_BE_BACKFILLED")
        else:
            custody,raw_sha=evidence(root/("window-%02d-health-attempt.json"%i))
            health_data,health_sha=evidence(
                root/("window-%02d-health-original.json"%i),131072)
            demand(custody.get("source_health_status_sha256")==health_sha,
                   "ORIGINAL_HEALTH_BYTES_REPLACED")
            health=inspect_health(i,a,custody,health_data)
            health["health_attempt_sha256"]=raw_sha
        final.append({
            "window":i, "anchor_ms":a,
            "original_preaction_state":"CENSORED",
            "original_decision_sha256":decision_sha,
            "original_health":health,
            "original_health_missing_for_window_zero":i==0,
            "horizon_outcomes":outcomes})
    report={
        "schema":SCHEMA,"verified_at_utc":datetime.now(timezone.utc).isoformat(),
        "audit_scope":"OFFHOST_ORIGINAL_ARCHIVED_RECEIPTS_NOT_IB_SIGNED",
        "original_plan_sha256":p_sha,
        "original_seal_sha256":s_sha,
        "pretrial_physical_host_copy_was_before_first_decision":True,
        "pretrial_custody_is_not_hardware_signed":True,
        "original_first_three_windows_complete":True,
        "original_decisions_censored":3,
        "original_maturity_attempts_censored":12,
        "price_only_matured_labels":0,
        "window_zero_full_health_snapshot_unattested":True,
        "later_ready_health_snapshots_were_stale_at_original_decisions":True,
        "original_source_proven_ib_signed":False,
        "production_owner_or_witness_qualified":False,
        "new_trading_or_model_promotion":False,
        "broker_requests_or_orders_sent_by_auditor":0,
        "fourth_window_retains_original_future_schedule":anchors[3],
        "fourth_window_outcomes_not_in_this_audit":True,
        "first_three":final,
    }
    return report

def write_report(path,report):
    path=Path(path)
    demand(path.is_absolute() and not path.is_symlink() and
           not path.exists(),"OUTPUT_MUST_BE_NEW")
    private_dir(path.parent)
    raw=json.dumps(report,sort_keys=True,indent=2,allow_nan=False).encode()+b"\n"
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,"wb") as f:f.write(raw);f.flush();os.fsync(f.fileno())
    d=os.open(path.parent,os.O_DIRECTORY|os.O_RDONLY|os.O_NOFOLLOW)
    try:os.fsync(d)
    finally:os.close(d)

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--root",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True)
    args=p.parse_args()
    result=verify(args.root)
    write_report(args.output,result)
    print(json.dumps({"state":"THREE_FROZEN_WINDOWS_INDEPENDENTLY_CENSORED",
        "windows":3,"censored_maturities":12,
        "stale_status_age_ms":[r["original_health"]["asof_age_at_frozen_decision_ms"]
           for r in result["first_three"] if r["original_health"]],
        "fourth_window_retained":True,
        "production_admission":False},sort_keys=True))
if __name__=="__main__":main()
