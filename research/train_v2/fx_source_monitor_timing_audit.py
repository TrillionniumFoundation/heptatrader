"""Independent, read-only SLA versus market-quote-health audit.

The testable question is whether a sandbox original-PAPER monitor publishes
fresh-enough full 24-probe results, separately from whether current IB quotes
are fresh. Genuine HOLD never upgrades to READY or justifies a trade.
Local journald/host timestamps are NOT independent broker signatures.
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

SCHEMA="heptatrader.fx-source-monitor-timing-vs-quote-gap-audit.v1"
CYCLE_SCHEMA="heptatrader.source-readiness-cadence-lab.v1"
STATUS_SCHEMA="heptatrader.quote-readiness-status.v1"
READY="READY_FOR_REVIEW_NOT_AUTHORIZATION"
HOLD="HOLD_NEW_FUSION_WINDOW"
MONITOR_AGE_MAX_MS=30000
IB_QUOTE_TTL_MS=5000
EXPECTED_CANDIDATE_SHA="0172c3470e7e0b821c39aac00eb077d18d436b1711c32d46f4a99bfd32199f12"
EXPECTED_INSTALLED_SHA="63bcef1a6d8bc22e3c2fb10d1dcebc1457559538e6a959004e61eeb88c09c320"
EXPECTED_PAPER_PERIOD="61995fcedc2993d7ac33d2ed86c9613068c5a3ecd0d1c377e612639cd5a0958f"
RULE={
    "schema":"heptatrader.quote-readiness.v1",
    "input_probes":24,"spacing_ms":60000,"horizon_ms":300000,
    "input_grace_ms":60000,"endpoint_tolerance_ms":5000,
    "path_gap_max_ms":30000,"minimum_usable":20,"maximum_missing":4,
    "source_ttl_ms":5000,
    "statistics_scope":"OVERLAPPING_OPERATIONAL_COVERAGE_NOT_PERFORMANCE"
}
class AuditHold(ValueError): pass

def require(ok,msg):
    if not ok: raise AuditHold(msg)

def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(",",":"),allow_nan=False).encode()

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def strict_json(raw):
    def pairs(items):
        d={}
        for key,val in items:
            require(key not in d,"DUPLICATE_JSON_KEY")
            d[key]=val
        return d
    try:
        return json.loads(raw,object_pairs_hook=pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(AuditHold("NONFINITE_JSON")))
    except (UnicodeError,json.JSONDecodeError) as ex:
        raise AuditHold("INVALID_JSON") from ex

def private_file(path,limit=8*1024*1024):
    path=Path(path)
    require(path.is_absolute() and not path.is_symlink(),"UNSAFE_INPUT_PATH")
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        a=os.fstat(fd)
        require(stat.S_ISREG(a.st_mode) and a.st_uid==os.geteuid()
                and a.st_nlink==1 and not a.st_mode&0o077 and 0<a.st_size<=limit,
                "INPUT_NOT_OWNER_PRIVATE_REGULAR_FILE")
        raw=b""
        while len(raw)<a.st_size:
            b=os.read(fd,min(1024*1024,a.st_size-len(raw)))
            require(bool(b),"INPUT_SHORT_READ")
            raw+=b
        z=os.fstat(fd)
        require((a.st_dev,a.st_ino,a.st_size,a.st_mtime_ns)==
                (z.st_dev,z.st_ino,z.st_size,z.st_mtime_ns),"INPUT_MODIFIED_DURING_AUDIT")
        return raw
    finally:os.close(fd)

def expected_cycle_events(journal):
    events=[]
    for raw in journal.splitlines():
        raw=raw.strip()
        if not raw.startswith(b"{"):continue
        item=strict_json(raw)
        if isinstance(item,dict) and item.get("schema")==CYCLE_SCHEMA:
            events.append(item)
    return events

def validate_cycle_stream(events,expected_count=24,max_age_ms=MONITOR_AGE_MAX_MS):
    require(type(events) is list and len(events)==expected_count,
            "SOURCE_CYCLE_MISSING_OR_DUPLICATED")
    statuses=[]
    intervals=[]
    for i,e in enumerate(events,1):
        require(type(e) is dict and e.get("schema")==CYCLE_SCHEMA
                and type(e.get("cycle")) is int and e["cycle"]==i
                and e.get("trade_authority") is False and e.get("orders_sent")==0
                and type(e.get("source_asof_ms")) is int
                and type(e.get("published_ms")) is int
                and type(e.get("published_source_age_ms")) is int
                and type(e.get("assess_ms")) in (int,float)
                and e.get("state") in (READY,HOLD),
                "UNTRUSTED_SOURCE_CYCLE_RECORD")
        source_age=e["published_ms"]-e["source_asof_ms"]
        require(source_age==e["published_source_age_ms"] and
                0<=source_age<=600000 and
                0<=e["assess_ms"]<=600000,
                "SOURCE_CYCLE_CLOCK_OR_ASOF_FORGED")
        usable=e.get("usable")
        if e["state"]==READY:
            require(type(usable) is int and 20<=usable<=24,
                    "PUBLISHED_READY_WITH_INVALID_PROBES")
        else:
            # HOLD can occur with 24 usable probes if the original IB
            # quote age exceeded the unmodified 5000 ms TTL.
            require(usable is None or type(usable) is int and 0<=usable<=24,
                    "PUBLISHED_HOLD_PROBE_COUNT_BAD")
        statuses.append({"cycle":i,"source_asof_ms":e["source_asof_ms"],
            "published_ms":e["published_ms"],"source_age_at_publish_ms":source_age,
            "source_state":e["state"],"usable":usable,
            "original_quote_health_uncertainty_not_reclassified":e["state"]==HOLD})
        if i>1:
            prior=events[i-2]
            require(e["source_asof_ms"]>prior["source_asof_ms"] and
                    e["published_ms"]>prior["published_ms"],
                    "MONITOR_CYCLE_CLOCK_NONMONOTONIC")
            interval_age=e["published_ms"]-prior["source_asof_ms"]
            require(interval_age>=0,"NEGATIVE_STATUS_AGE_INTERVAL")
            intervals.append(interval_age)
    max_at_publish=max(s["source_age_at_publish_ms"] for s in statuses)
    max_before_next=max(intervals) if intervals else None
    timing_pass=max_at_publish<=max_age_ms and (
        max_before_next is not None and max_before_next<=max_age_ms)
    return {
        "cycle_count":len(events),
        "source_ready_cycle_count":sum(x["source_state"]==READY for x in statuses),
        "source_hold_cycle_count":sum(x["source_state"]==HOLD for x in statuses),
        "monitor_snapshot_age_at_publish_max_ms":max_at_publish,
        "monitor_snapshot_age_before_next_publish_max_ms":max_before_next,
        "monitor_intervals_exceeding_declared_30s_age":sum(x>max_age_ms for x in intervals),
        "monitor_published_samples_exceeding_declared_30s_age":sum(
            x["source_age_at_publish_ms"]>max_age_ms for x in statuses),
        "all_observed_monitor_intervals_within_30s":timing_pass,
        "this_is_not_proof_of_future_reliability":True,
        "quote_health_HOLD_cycles_still_HOLD":True,
        "quote_health_reason_per_cycle_not_present_in_summary":True,
    }

def validate_last_status(status,last_cycle,period):
    require(type(status) is dict and status.get("schema")==STATUS_SCHEMA
            and status.get("period_sha256")==period
            and status.get("rule")==RULE
            and status.get("automatic_trial_registration") is False
            and status.get("trading_authority") is False
            and status.get("orders_sent")==0,
            "FINAL_ORIGINAL_MONITOR_SCOPE_CHANGED")
    require(status.get("at_ms")==last_cycle["source_asof_ms"]
            and status.get("state")==last_cycle["state"]
            and status.get("usable")==last_cycle["usable"],
            "FINAL_STATUS_NOT_LAST_JOURNAL_SOURCE")
    probes=status.get("probes")
    counts=status.get("counts")
    require(type(probes) is list and len(probes)==24
            and type(counts) is dict and
            all(type(p) is dict and type(p.get("status")) is str for p in probes)
            and dict(Counter(p["status"] for p in probes))==counts
            and counts.get("USABLE",0)==status["usable"]
            and status.get("unusable")==24-status["usable"],
            "LAST_STATUS_24_PROBES_COUNT_MISMATCH")
    fingerprint=status.get("source_prefix_digest")
    require(type(fingerprint) is str and
            fingerprint==sha(canonical(status.get("source_prefixes"))),
            "FINAL_SOURCE_PREFIX_DIGEST_CHANGED")
    asof,latest,age=(status.get(k) for k in (
        "at_ms","latest_source_quote_ms","latest_quote_age_ms"))
    if type(asof) is int and type(latest) is int and type(age) is int:
        require(asof-latest==age,"LAST_ORIGINAL_QUOTE_AGE_INCONSISTENT")
        if status["state"]==READY:
            require(0<=age<=IB_QUOTE_TTL_MS,"READY_EXCEEDED_ORIGINAL_QUOTE_TTL")
    return True

def audit(*,plan_file,journal_file,final_status_file,candidate_code,
          installed_code,output):
    plan_bytes=private_file(plan_file,32768)
    plan=strict_json(plan_bytes)
    require(plan.get("schema")=="heptatrader.fx-readonly-cadence-revalidation-plan.v1"
            and plan.get("sandbox") is True
            and plan.get("refresh_seconds")==15
            and plan.get("max_cycles")==24
            and plan.get("cpu_quota_pct")==50
            and plan.get("memory_max_mib")==384
            and plan.get("period_sha256")==EXPECTED_PAPER_PERIOD
            and plan.get("candidate_code_sha256")==EXPECTED_CANDIDATE_SHA
            and plan.get("installed_code_sha256")==EXPECTED_INSTALLED_SHA
            and plan.get("source_readiness_24_probe_rule_unchanged") is True
            and plan.get("original_quote_5000ms_ttl_unchanged") is True
            and plan.get("broker_orders_sent")==0 and
            plan.get("trade_authority") is False
            and plan.get("not_a_production_monitor_cutover") is True,
            "STUDY_PLAN_WRONG_SCOPE")
    require(sha(private_file(candidate_code))==EXPECTED_CANDIDATE_SHA
            and sha(private_file(installed_code))==EXPECTED_INSTALLED_SHA,
            "SOURCE_CODE_SWITCHED_DURING_RUN")
    journal=private_file(journal_file,1024*1024)
    events=expected_cycle_events(journal)
    measure=validate_cycle_stream(events,24)
    status_bytes=private_file(final_status_file,131072)
    status=strict_json(status_bytes)
    validate_last_status(status,events[-1],EXPECTED_PAPER_PERIOD)
    report={
       "schema":SCHEMA,
       "audited_at_utc":datetime.now(timezone.utc).isoformat(),
       "original_source_monitor_rule":RULE,
       "exact_precommit_plan_sha256":sha(plan_bytes),
       "candidate_source_sha256":EXPECTED_CANDIDATE_SHA,
       "installed_product_monitor_sha256":EXPECTED_INSTALLED_SHA,
       "sandbox_original_journal_sha256":sha(journal),
       "sandbox_last_full_original_status_sha256":sha(status_bytes),
       "observed_original_source_period_sha256":EXPECTED_PAPER_PERIOD,
       "source_monitor_active_production_replaced":False,
       "original_four_window_censored_cohort_changed":False,
       "prod_training_or_model_promotion_authorized":False,
       "broker_queries_or_orders_sent":0,
       "final_cycle_status_is_verified_full_24probe_and_quote_scope":True,
       "individual_prior_cycle_full_health_snapshots_attested":False,
       "journal_clock_not_independent_hardware_witness":True,
       "IB_quote_signature_or_fill_price_attested":False,
       **measure,
       "monitor_timing_research_status":(
           "PASS_SHORT_SANDBOX_TIMING_NOT_PRODUCTION_RELEASE"
           if measure["all_observed_monitor_intervals_within_30s"]
           else "HOLD_MONITOR_PUBLISH_STALENESS"),
       "underlying_market_source_health_100pct_READY_proven":(
           measure["source_hold_cycle_count"]==0),
       "unattended_product_cutover_approved":False,
       "actual_broker_execution_rewards_observed":0,
       "trade_authority":False,
    }
    output=Path(output)
    require(output.is_absolute() and not output.exists() and not output.is_symlink(),
            "OUTPUT_EXISTING_OR_UNSAFE")
    output.parent.mkdir(parents=True,mode=0o700,exist_ok=True)
    parent=output.parent.stat()
    require(parent.st_uid==os.geteuid() and stat.S_IMODE(parent.st_mode)==0o700,
            "OUTPUT_DIRECTORY_NOT_PRIVATE")
    raw=canonical(report)+b"\n"
    fd=os.open(output,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,"wb") as stream:
        stream.write(raw);stream.flush();os.fsync(stream.fileno())
    d=os.open(output.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:os.fsync(d)
    finally:os.close(d)
    return report

def main():
    p=argparse.ArgumentParser()
    for key in ("plan","journal","last-status","candidate-code","installed-code","output"):
        p.add_argument("--"+key,type=Path,required=True)
    a=p.parse_args()
    r=audit(plan_file=a.plan,journal_file=a.journal,
            final_status_file=a.last_status,candidate_code=a.candidate_code,
            installed_code=a.installed_code,output=a.output)
    print(json.dumps({key:r[key] for key in (
        "cycle_count","source_ready_cycle_count","source_hold_cycle_count",
        "monitor_snapshot_age_at_publish_max_ms",
        "monitor_snapshot_age_before_next_publish_max_ms",
        "monitor_timing_research_status","unattended_product_cutover_approved",
        "broker_queries_or_orders_sent")},sort_keys=True))
if __name__=="__main__":main()
