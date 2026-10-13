"""Independent REAL original PAPER verified-cache health timeline/recovery audit.

Only read frozen status/receipts from an already-running fixed 480-cycle study.
Does NOT change cached source, broker subscriptions, original study, monitor,
model or trade authority. Mid-run output must never be called 480/480 proof.
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

SCHEMA = "heptatrader.fx-original-cache480-recovery-and-sla.v1"
COHORT = "heptatrader.fx-verified-cache-480-original-readonly.v1"
PLAN_PIN = "8a77da0fcdd3d251a19d53209fa7a836386848380f92e3681590b33ce2bf98d5"
PERIOD_PIN = "61995fcedc2993d7ac33d2ed86c9613068c5a3ecd0d1c377e612639cd5a0958f"
RULE = {"schema":"heptatrader.quote-readiness.v1","input_probes":24,
        "spacing_ms":60000,"horizon_ms":300000,"input_grace_ms":60000,
        "endpoint_tolerance_ms":5000,"path_gap_max_ms":30000,
        "minimum_usable":20,"maximum_missing":4,"source_ttl_ms":5000,
        "statistics_scope":"OVERLAPPING_OPERATIONAL_COVERAGE_NOT_PERFORMANCE"}
N = 480
STEP_MS = 15000
MAX_AGE_MS = 30000
class Hold(ValueError): pass

def require(ok,msg):
    if not ok:raise Hold(msg)

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(",",":"),allow_nan=False).encode()

def strict(b):
    def unique(pairs):
        d={}
        for k,v in pairs:
            require(k not in d,"DUPLICATE_JSON_FIELD")
            d[k]=v
        return d
    try:
        return json.loads(b,object_pairs_hook=unique,
            parse_constant=lambda _: (_ for _ in ()).throw(Hold("NONFINITE_JSON")))
    except (UnicodeError,json.JSONDecodeError) as exc:
        raise Hold("INVALID_JSON_DOCUMENT") from exc

def owner_dir(root,create=False):
    p=Path(root)
    require(p.is_absolute() and not p.is_symlink() and ".." not in p.parts,
            "RESEARCH_PATH_UNSAFE")
    if create:
        p.mkdir(mode=0o700,parents=True,exist_ok=True)
    st=p.stat()
    require(stat.S_ISDIR(st.st_mode) and st.st_uid==os.geteuid()
            and stat.S_IMODE(st.st_mode)==0o700,"PRIVATE_RESEARCH_DIRECTORY_REQUIRED")
    return p

def private_json(path,size=131072):
    p=Path(path)
    require(p.is_absolute() and not p.is_symlink(),"PRIVATE_FILE_SYMLINK_OR_RELATIVE")
    fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        s=os.fstat(fd)
        require(stat.S_ISREG(s.st_mode) and s.st_uid==os.geteuid()
                and s.st_nlink==1 and s.st_mode&0o077==0
                and 0<s.st_size<=size,"PRIVATE_SOURCE_INVALID")
        b=bytearray()
        while len(b)<s.st_size:
            chunk=os.read(fd,min(65536,s.st_size-len(b)))
            require(bool(chunk),"SOURCE_SHORT_READ")
            b.extend(chunk)
        e=os.fstat(fd)
        require((s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns)==
                (e.st_dev,e.st_ino,e.st_size,e.st_mtime_ns),
                "SOURCE_CHANGED_DURING_AUDIT")
    finally:
        os.close(fd)
    return strict(b),sha(b)

def pct(v,p):
    if not v:return None
    ordered=sorted(v)
    at=(len(ordered)-1)*p/100
    lo=int(at)
    hi=min(lo+1,len(ordered)-1)
    return round(ordered[lo]+(ordered[hi]-ordered[lo])*(at-lo),3)

def classification(s,rec,period):
    require(s.get("schema")=="heptatrader.quote-readiness-status.v1"
            and s.get("period_sha256")==period
            and s.get("rule")==RULE,"ORIGINAL_MONITOR_SCOPE_CHANGED")
    probes=s.get("probes"); counts=s.get("counts")
    require(type(probes) is list and len(probes)==24
            and type(counts) is dict
            and all(type(x) is dict and type(x.get("status")) is str
                    for x in probes),"ORIGINAL_PROBE_SET_UNRELIABLE")
    true_counts=dict(Counter(x["status"] for x in probes))
    require(true_counts==counts
            and type(s.get("usable")) is int
            and type(s.get("unusable")) is int
            and s["usable"]==true_counts.get("USABLE",0)
            and s["unusable"]==24-s["usable"],
            "ORIGINAL_PROBE_COUNTS_REWRITTEN")
    require(type(s.get("source_prefixes")) is list
            and sha(canonical(s["source_prefixes"]))==s.get("source_prefix_digest"),
            "ORIGINAL_SOURCE_PREFIX_DIGEST_CHANGED")
    stamp=s.get("at_ms"); age=s.get("latest_quote_age_ms")
    observed=s.get("latest_source_quote_ms")
    started=rec.get("actual_assessment_started_ms")
    published=rec.get("published_at_ms")
    require(type(started) is int and type(stamp) is int
            and type(published) is int
            and started<=stamp<=started+3000
            and stamp<=published<=rec.get("record_sealed_at_ms"),
            "SOURCE_ASOF_OR_PUBLICATION_NONCAUSAL")
    require(type(age) is int and type(observed) is int and
            age==stamp-observed,"SOURCE_QUOTE_AGE_CHANGED")
    require(s.get("automatic_trial_registration") is False
            and s.get("trading_authority") is False
            and s.get("orders_sent")==0,
            "SOURCE_MONITOR_AUTHORITY_FORGED")
    state=s.get("state")
    require(state in ("READY_FOR_REVIEW_NOT_AUTHORIZATION","HOLD_NEW_FUSION_WINDOW"),
            "ORIGINAL_SOURCE_STATE_UNEXPECTED")
    ready=state=="READY_FOR_REVIEW_NOT_AUTHORIZATION"
    if ready:
        require(s.get("reasons")==[] and s["usable"]>=20
                and s["unusable"]<=4 and 0<=age<=5000,
                "FALSE_READY_FROM_UNUSABLE_OR_STALE_ORIGINAL")
    require(rec.get("original_reported_state")==state
            and rec.get("source_usable_probes")==s["usable"]
            and rec.get("original_asof_ms")==stamp
            and rec.get("original_monitor_source_prefix_digest")==
                s["source_prefix_digest"],
            "ORIGINAL_RECEIPT_SOURCE_STATUS_CHANGED")
    rec_issues=rec.get("issues")
    require(type(rec_issues) is list,"INVALID_SOURCE_ISSUES")
    qualified=ready and rec_issues==[]
    require(rec.get("source_ready") is qualified,"SOURCE_HOLD_OR_BAD_HEALTH_ELEVATED")
    if rec_issues:
        require(rec.get("state")=="HOLD_UNQUALIFIED_SOURCE",
                "NONCOMPLIANT_SOURCE_MARKED_ACCEPTED")
    else:
        require(rec.get("state")=="CAPTURED_ORIGINAL_CACHE_HEALTH",
                "ACCEPTED_CYCLE_STATE_INCONSISTENT")
    return {
        "class":("READY" if qualified else "ORIGINAL_HOLD" if not ready
                 else "QUALIFICATION_HOLD"),
        "at_ms":stamp,
        "published_at_ms":published,
        "status_age_at_publish_ms":published-stamp,
        "source_usable_probes":s["usable"],
        "probe_counts":counts,
        "reason_codes":s.get("reasons") or [],
        "issues":rec_issues,
    }

def scan(root):
    root=owner_dir(root)
    plan,plan_hash=private_json(root/"plan.json",32768)
    require(plan_hash==PLAN_PIN
            and plan.get("schema")==COHORT+".plan.v1"
            and plan.get("count")==N and plan.get("spacing_ms")==STEP_MS
            and plan.get("maximum_original_status_age_ms")==MAX_AGE_MS
            and plan.get("period_sha256")==PERIOD_PIN
            and plan.get("exact_original_monitor_rule")==RULE
            and plan.get("trade_authority") is False
            and plan.get("broker_queries_or_orders_sent")==0
            and plan.get("production_cutover_authorized") is False,
            "FROZEN_480_PLAN_MISMATCH")
    times=plan.get("cycle_schedule_ms")
    require(type(times) is list and len(times)==N
            and times==[times[0]+STEP_MS*i for i in range(N)],
            "FROZEN_480_SCHEDULE_CHANGED")
    worker,worker_hash=private_json(root/"worker-start.json",16384)
    require(worker.get("plan_sha256")==PLAN_PIN
            and worker.get("no_restart_no_retry") is True
            and worker.get("no_production_service_mutation") is True
            and worker.get("broker_orders_sent")==0,
            "ORIGINAL_WORKER_NOT_RESEARCH_ONLY")
    # Read an immutable contiguous prefix: do not fill skipped cycles or
    # promote an in-progress test to a complete 480-cycle validation.
    found=[(root/("cycle-%03d-receipt.json"%i)).exists() for i in range(N)]
    if False in found:
        first_gap=found.index(False)
        require(not any(found[first_gap+1:]),"CANNOT_SILENTLY_FILL_INTERIOR_CYCLE_GAP")
        size=first_gap
    else:size=N
    require(size>=1,"NO_RESEARCH_CYCLES_YET")
    states=[]; evidence=[]; statuses=[]; probe_totals=Counter()
    reason_totals=Counter(); issues_totals=Counter(); pub_ages=[]
    chain="0"*64
    for i in range(size):
        rec,r_sha=private_json(root/("cycle-%03d-receipt.json"%i),16384)
        require(rec.get("schema")==COHORT+".cycle.v1"
                and rec.get("index")==i
                and rec.get("fixed_target_ms")==times[i]
                and rec.get("period_sha256")==PERIOD_PIN
                and rec.get("previous_receipt_sha256")==chain
                and rec.get("trade_authority") is False
                and rec.get("broker_orders_sent")==0
                and type(rec.get("actual_assessment_started_ms")) is int
                and rec["actual_assessment_started_ms"]>=times[i]
                and type(rec.get("record_sealed_at_ms")) is int
                and rec["record_sealed_at_ms"]>=rec["actual_assessment_started_ms"],
                "BROKEN_CYCLE_OR_RECEIPT_CHAIN")
        p=root/("cycle-%03d-status.json"%i)
        if rec.get("status_sha256") is None:
            require(not p.exists()
                    and rec.get("source_ready") is False
                    and rec.get("state") in ("CENSORED_SCHEDULE_MISSED",
                                             "HOLD_SOURCE_ASSESSMENT_FAILURE"),
                    "MISSING_SOURCE_STATUS_AS_FALSE_SUCCESS")
            row={"class":"MISSING_OR_FAILED","at_ms":None,
                 "published_at_ms":None,"status_age_at_publish_ms":None,
                 "source_usable_probes":None,"probe_counts":{},
                 "reason_codes":[],"issues":rec.get("issues") or []}
        else:
            s,s_sha=private_json(p,262144)
            require(s_sha==rec["status_sha256"],"RECEIPT_STATUS_SHA_MISMATCH")
            row=classification(s,rec,PERIOD_PIN)
            require(row["status_age_at_publish_ms"]<=MAX_AGE_MS
                    or (row["class"]!="READY"
                        and "ORIGINAL_STATUS_STALE_AT_PUBLICATION" in row["issues"]),
                    "FALSE_FRESH_ORIGINAL_HEALTH")
            pub_ages.append(row["status_age_at_publish_ms"])
        for key,value in row["probe_counts"].items():
            probe_totals[key]+=value
        reason_totals.update(row["reason_codes"])
        issues_totals.update(row["issues"])
        states.append(row["class"])
        evidence.append({"index":i,"asof_ms":row["at_ms"],
                         "published_at_ms":row["published_at_ms"],
                         "class":row["class"],
                         "source_usable_probes":row["source_usable_probes"]})
        if row["at_ms"] is not None:statuses.append((i,row))
        chain=r_sha
    occurrences=Counter(states)
    # contiguous streak boundaries, including HOLD states that persist
    # after a quote stream resumes while trailing 24 probes mature.
    episodes=[]
    first=0
    for j in range(1,size+1):
        if j==size or states[j]!=states[first]:
            episodes.append({
                "status":states[first],"first_index":first,"last_index":j-1,
                "cycle_count":j-first,
                "fixed_calendar_duration_ms":(j-first)*STEP_MS,
                "start_utc":datetime.fromtimestamp(times[first]/1000,timezone.utc).isoformat(),
                "end_cycle_utc":datetime.fromtimestamp(times[j-1]/1000,timezone.utc).isoformat(),
            })
            first=j
    transitions=[]
    for (i,prior),(j,current) in zip(statuses,statuses[1:]):
        age=current["published_at_ms"]-prior["at_ms"]
        transitions.append({"previous_cycle":i,"next_cycle":j,
                            "previous_original_status":prior["class"],
                            "next_original_status":current["class"],
                            "prior_asof_to_next_durable_publish_ms":age,
                            "violates_precommitted_30s_sla":age>MAX_AGE_MS})
    bad=[t for t in transitions if t["violates_precommitted_30s_sla"]]
    final_file=root/"final.json"
    require(not final_file.exists() or size==N,"PREMATURE_FALSE_TERMINAL_REPORT")
    # Presence of final alone is NOT independent signed approval.
    result={
        "schema":SCHEMA,
        "audited_at_utc":datetime.now(timezone.utc).isoformat(),
        "audit_source_sha256":sha(Path(__file__).read_bytes()),
        "original_frozen_plan_sha256":plan_hash,
        "original_worker_sha256":worker_hash,
        "source_period_sha256":PERIOD_PIN,
        "prefix_cycle_count":size,"expected_total":N,
        "complete_480_evidence_observed":size==N and final_file.is_file(),
        "final_report_independently_reconciled":False,
        "capture_reason_totals":dict(reason_totals),
        "original_probe_status_totals":dict(probe_totals),
        "audit_issue_totals":dict(issues_totals),
        "cycle_classes":dict(occurrences),
        "episodes":episodes,
        "longest_original_hold_episode":max(
            (x for x in episodes if x["status"]=="ORIGINAL_HOLD"),
            key=lambda x:x["cycle_count"],default=None),
        "last_observed_class":states[-1],
        "publication_age_ms":{"p50":pct(pub_ages,50),"p95":pct(pub_ages,95),
                              "p99":pct(pub_ages,99),"maximum":max(pub_ages) if pub_ages else None},
        "cross_publication_asof_age_ms":{
            "sample_pairs":len(transitions),"maximum":max(
                (x["prior_asof_to_next_durable_publish_ms"] for x in transitions),
                default=None),
            "violations_over_30s_count":len(bad),
            "violations":bad,
        },
        "release_blockers":(
            ["ACTIVE_COHORT_NOT_FULL_480" if size<N else
             "FINAL_480_REQUIRES_SEPARATE_ORIGINAL_BYTE_REPLAY"]
            + (["GENUINE_ORIGINAL_SOURCE_HOLD"] if occurrences["ORIGINAL_HOLD"] else [])
            + (["CROSS_PUBLICATION_30S_FRESHNESS_BREACH"] if bad else [])
            + ["UPSTREAM_IB_CAUSAL_ROOT_NOT_ATTESTED",
               "PRODUCT_NATIVE_OWNER_WITNESS_LEDGER_AND_FLEX_NAV_HOLD"]
        ),
        "all_bad_original_samples_preserved":True,
        "not_an_original_broker_quote_or_independent_signed_timestamp":True,
        "zero_new_broker_requests_or_orders":True,
        "model_training_or_production_cutover_allowed":False,
        "trade_authority":False
    }
    return result

def write_report(path,report):
    p=Path(path)
    require(p.is_absolute() and not p.exists() and not p.is_symlink(),
            "RESEARCH_REPORT_MUST_BE_NEW")
    owner_dir(p.parent,create=True)
    raw=json.dumps(report,sort_keys=True,indent=2,allow_nan=False).encode()+b"\n"
    fd=os.open(p,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,"wb") as out:
        out.write(raw);out.flush();os.fsync(out.fileno())
    fd=os.open(p.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:os.fsync(fd)
    finally:os.close(fd)
    return sha(raw)

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--root",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True)
    a=p.parse_args()
    r=scan(a.root)
    out_sha=write_report(a.output,r)
    print(json.dumps({"state":"ORIGINAL_SOURCE_TIMELINE_PREFIX_RESEARCH_ONLY",
        "observed_cycles":r["prefix_cycle_count"],
        "ready":r["cycle_classes"].get("READY",0),
        "original_hold":r["cycle_classes"].get("ORIGINAL_HOLD",0),
        "missing":r["cycle_classes"].get("MISSING_OR_FAILED",0),
        "maximum_cross_pub_asof_age_ms":
            r["cross_publication_asof_age_ms"]["maximum"],
        "cross_pub_sla_violations":
            r["cross_publication_asof_age_ms"]["violations_over_30s_count"],
        "output_sha256":out_sha,"trade_authority":False},sort_keys=True))
if __name__=="__main__":main()
