"""Conservative source-health monitor as-of age envelope audit, RESEARCH ONLY.

Replays the actual user journal's bounded sandbox source-readiness loop.
No TWS/IB client, no original study mutations. Full 30s freshness coverage
needs both rapid calculation AND rapid publication; nominal cadence alone
is insufficient. Nothing here admits a product model or trade.
"""
from __future__ import annotations
import argparse
from datetime import datetime,timezone
import hashlib,json,os,stat,subprocess
from pathlib import Path

SCHEMA="heptatrader.original-quote-monitor-publish-sla.v1"
EVENT_SCHEMA="heptatrader.source-readiness-cadence-lab.v1"
FRESHNESS_MS=30000
KNOWN_UNITS={
 "heptatrader-health-cadence-25pct-20261013.service":(25,8,15),
 "heptatrader-health-cadence-50pct-20261013.service":(50,12,15),
}
FIELDS=frozenset(("schema","cycle","assess_ms","source_asof_ms","published_ms",
                   "published_source_age_ms","state","usable","trade_authority",
                   "orders_sent"))
class AuditHold(ValueError): pass

def ensure(ok,reason):
    if not ok:raise AuditHold(reason)
def canonical(d):
    return json.dumps(d,sort_keys=True,separators=(",",":"),allow_nan=False).encode()
def sha(data):
    return hashlib.sha256(data).hexdigest()
def strict(raw):
    def unique(items):
        o={}
        for k,v in items:
            ensure(k not in o,"DUPLICATE_JOURNAL_EVENT_KEY");o[k]=v
        return o
    return json.loads(raw,object_pairs_hook=unique,
                      parse_constant=lambda _:(_ for _ in ()).throw(AuditHold("NONFINITE_JOURNAL")))
def normalized_record(event):
    ensure(type(event) is dict and set(event)==FIELDS,
           "JOURNAL_EVENT_FIELDS_UNEXPECTED")
    ensure(event["schema"]==EVENT_SCHEMA
           and event["trade_authority"] is False
           and event["orders_sent"]==0
           and event["state"] in ("READY_FOR_REVIEW_NOT_AUTHORIZATION",
                                 "HOLD_NEW_FUSION_WINDOW"),"JOURNAL_NOT_A_RESEARCH_SOURCE")
    for k in ("cycle","source_asof_ms","published_ms","published_source_age_ms",
              "usable"):
        ensure(type(event[k]) is int,"INVALID_JOURNAL_NUMERIC_TYPE_"+k)
    ensure(type(event["assess_ms"]) in (int,float) and
           0 < event["assess_ms"] < 120000 and
           0 <= event["usable"] <= 24 and
           event["source_asof_ms"]>0 and event["published_ms"]>=event["source_asof_ms"],
           "NONFINITE_OR_OUT_OF_RANGE_MONITOR_EVENT")
    ensure(0<=event["published_source_age_ms"]<=120000
           and abs((event["published_ms"]-event["source_asof_ms"])-
                   event["published_source_age_ms"])<=2
           and abs(event["assess_ms"]-event["published_source_age_ms"])<=100,
           "ASOF_AGE_NOT_REPRODUCIBLE")
    return event

def extract_from_journal(raw_lines):
    out=[]
    for line in raw_lines:
        line=line.strip()
        if not line or not line.startswith("{"):continue
        try: parsed=strict(line)
        except (AuditHold,json.JSONDecodeError,UnicodeError):
            # Any candidate malformed line with the expected schema must NOT
            # be silently skipped; otherwise an adversary could hide a cycle.
            if EVENT_SCHEMA in str(line):
                raise AuditHold("CORRUPT_EXPECTED_JOURNAL_EVENT")
            continue
        if type(parsed) is dict and parsed.get("schema")==EVENT_SCHEMA:
            out.append(normalized_record(parsed))
    return out

def audit_cycles(records, *,quota_percent, expected_cycles, requested_period_seconds):
    ensure(type(expected_cycles) is int and 3<=expected_cycles<=24,
           "CYCLE_BOUND_INVALID")
    ensure(type(quota_percent) is int and quota_percent in (25,50),
           "CPU_BUDGET_NOT_TESTED")
    ensure(type(requested_period_seconds) is int and requested_period_seconds==15,
           "RELEASE_CADENCE_CHANGED")
    ensure(len(records)==expected_cycles,
           "LIVE_LAB_INCOMPLETE_OR_REPEATED")
    # Shape/type validation must precede ordinal comparison: bool is not
    # accepted as a numeric journal cycle even though bool == 1 in Python.
    for r in records: normalized_record(r)
    ids=[r["cycle"] for r in records]
    ensure(ids==list(range(1,expected_cycles+1)),
           "JOURNAL_CYCLE_MISSING_OR_DUPLICATE")
    peaks=[]
    exposures=[]
    intervals=[]
    for before,after in zip(records,records[1:]):
        ensure(before["source_asof_ms"] < after["source_asof_ms"] and
               before["published_ms"] < after["published_ms"] and
               before["published_ms"] < after["source_asof_ms"],
               "MONITOR_CLOCK_OR_PUBLICATION_NOT_MONOTONIC")
        stale_at_next_publish=after["published_ms"]-before["source_asof_ms"]
        duration=after["published_ms"]-before["published_ms"]
        ensure(0<duration<150000,"PUBLISH_INTERVAL_RANGE")
        # Status(i) is the last available snapshot on [pub_i, pub_i+1).
        # At the next publication the status is replaced; this is a
        # conservative upper-limit independent of any particular decision.
        lost=max(0,after["published_ms"] -
                 max(before["published_ms"],before["source_asof_ms"]+FRESHNESS_MS))
        exposures.append(lost)
        peaks.append(stale_at_next_publish)
        intervals.append({
            "cycle":before["cycle"],
            "original_asof_ms":before["source_asof_ms"],
            "published_at_ms":before["published_ms"],
            "next_publication_ms":after["published_ms"],
            "last_status_age_before_next_publish_ms":stale_at_next_publish,
            "status_stale_exposure_before_next_publish_ms":lost,
            "publish_to_publish_ms":duration,
            "continuous_30s_freshness_observed":lost==0,
        })
    total=sum(r["publish_to_publish_ms"] for r in intervals)
    bad=sum(exposures)
    max_publish_age=max(r["published_source_age_ms"] for r in records)
    result={
        "schema":SCHEMA,
        "requested_cpu_quota_percent_label_not_independent_kernel_attestation":quota_percent,
        "requested_refresh_seconds":requested_period_seconds,
        "frozen_source_freshness_budget_ms":FRESHNESS_MS,
        "observed_cycle_count":len(records),
        "all_cycle_states":[r["state"] for r in records],
        "observed_minimum_usable":min(r["usable"] for r in records),
        "observed_maximum_usable":max(r["usable"] for r in records),
        "max_frozen_source_age_at_publish_ms":max_publish_age,
        "max_stale_age_immediately_before_next_publish_ms":max(peaks),
        "observed_stale_intervals":sum(x>0 for x in exposures),
        "observed_stale_exposure_total_ms":bad,
        "observed_publication_span_ms":total,
        "observed_stale_exposure_fraction":
            (round(bad/total,6) if total else None),
        "all_observed_intervals_continuously_fresh":
            not any(exposures),
        "per_publish_interval":intervals,
        "current_product_source_monitor_unchanged":True,
        "sandbox_original_quote_monitor_not_broker_signed":True,
        "short_research_duration_cannot_certify_production_durability":True,
        "automatic_model_training_or_promotion_allowed":False,
        "trade_authority":False,
        "broker_queries_or_orders_sent_by_audit":0,
    }
    return result

def write_private(path,content):
    path=Path(path)
    ensure(path.is_absolute() and not path.exists() and not path.is_symlink(),
           "OUTPUT_MUST_BE_NEW_ABSOLUTE")
    parent=path.parent
    parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    s=parent.stat()
    ensure(not parent.is_symlink() and s.st_uid==os.geteuid()
           and stat.S_IMODE(s.st_mode)==0o700,
           "OUTPUT_DIRECTORY_NOT_PRIVATE")
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,"wb") as f:
        f.write(content);f.flush();os.fsync(f.fileno())
    d=os.open(parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:os.fsync(d)
    finally:os.close(d)

def run(*,unit,output_dir):
    ensure(unit in KNOWN_UNITS,"UNREGISTERED_SANDBOX_UNIT")
    quota,n,period=KNOWN_UNITS[unit]
    output_dir=Path(output_dir)
    raw=subprocess.check_output(["journalctl","--user","-u",unit,
                                 "--no-pager","-o","cat"],timeout=15)
    lines=raw.decode("utf8","strict").splitlines()
    events=extract_from_journal(lines)
    result=audit_cycles(events,quota_percent=quota,
                        expected_cycles=n,requested_period_seconds=period)
    raw_canonical=b"".join(canonical(x)+b"\n" for x in events)
    result.update({
        "journal_unit":unit,
        "normalized_original_journal_cycle_sha256":sha(raw_canonical),
        "observed_at_utc":datetime.now(timezone.utc).isoformat(),
        "source_code_sha256":sha(Path(__file__).read_bytes()),
        "immutable_raw_event_count":len(events),
        "not_original_00_20_health_custody":True,
    })
    write_private(output_dir/(unit+".events.v1.jsonl"),raw_canonical)
    write_private(output_dir/(unit+".review.v1.json"),
                  json.dumps(result,sort_keys=True,indent=2,allow_nan=False).encode()+b"\n")
    print(json.dumps({
        "unit":unit,
        "sample_count":len(events),
        "max_publish_age_ms":result["max_frozen_source_age_at_publish_ms"],
        "max_before_next_publish_ms":result["max_stale_age_immediately_before_next_publish_ms"],
        "stale_intervals":result["observed_stale_intervals"],
        "stale_exposure_ms":result["observed_stale_exposure_total_ms"],
        "all_observed_intervals_continuously_fresh":
            result["all_observed_intervals_continuously_fresh"],
        "broker_orders_sent":0,
    },sort_keys=True),flush=True)

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--unit",choices=tuple(KNOWN_UNITS),required=True)
    p.add_argument("--output-dir",type=Path,required=True)
    a=p.parse_args()
    os.umask(0o077)
    run(unit=a.unit,output_dir=a.output_dir)
if __name__=="__main__":main()
