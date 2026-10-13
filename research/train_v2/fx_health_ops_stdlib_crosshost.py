"""Physical-host standard-library-only 24-cycle source-cache readback.

Pins the exact files *after* the finished local research run; independent
arithmetic rechecks event clock consistency, not pre-event third-party signing
or new IB market-data. No remote orders or broker network clients.
"""
from __future__ import annotations
import argparse,hashlib,json,os,stat
from pathlib import Path
from datetime import datetime,timezone
from collections import Counter

SCHEMA="heptatrader.fx-health-24cycles-physical-readback.v1"
PINS={
 "plan.json":"45a651215410ec9ee25a6c280db3975c291459674190b5f06a61ba2677d63dd9",
 "original-journal.txt":"a2bc5f0ca656d2b3a5925bae60000dd64698514bc6812dcc823451fb60c653d5",
 "readiness.json":"49648b941f11acce4695a0f0a538a04537979ff29dcd6ca4ec48070223e2f3cb",
 "independent-operational-audit.json":"481b65c37a65b2f50b5c17452e3f6c08f865b2dd31d762c9befeedd9784ae398",
}
PERIOD="61995fcedc2993d7ac33d2ed86c9613068c5a3ecd0d1c377e612639cd5a0958f"
PLAN_SCHEMA="heptatrader.research-source-cache-24-cycle-precommit.v1"
EVENT_SCHEMA="heptatrader.source-readiness-cadence-lab.v1"
CACHED_SCHEMA="heptatrader.readiness-verified-sealed-cache-counters.v1"
SOURCE_SCHEMA="heptatrader.quote-readiness-status.v1"
PRECOMMIT_CODE={
 "source_readiness.py":"8cbedb6a5f792891f0141f7b2ae0da91a7738080cd14c45995436ed55473c309",
 "verified_sealed_readiness_cache.py":"1d1bdea5702d1fab8c9412eb2cbf0d224dd34d2736d4ab822e71bff5b9acb61c",
}
RULE={
 "schema":"heptatrader.quote-readiness.v1",
 "input_probes":24,"spacing_ms":60000,"horizon_ms":300000,
 "input_grace_ms":60000,"endpoint_tolerance_ms":5000,
 "path_gap_max_ms":30000,"minimum_usable":20,
 "maximum_missing":4,"source_ttl_ms":5000,
 "statistics_scope":"OVERLAPPING_OPERATIONAL_COVERAGE_NOT_PERFORMANCE",
}
class AuditHold(ValueError):pass
def need(value,reason):
    if not value:raise AuditHold(reason)
def digest(data):return hashlib.sha256(data).hexdigest()
def strict(data):
    def unique(pairs):
        d={}
        for k,v in pairs:
            need(k not in d,"DUPLICATE_JSON_KEY")
            d[k]=v
        return d
    return json.loads(data,object_pairs_hook=unique,
       parse_constant=lambda _:(_ for _ in ()).throw(AuditHold("NONFINITE_JSON")))
def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(",",":"),allow_nan=False).encode()
def read_bytes(dir,name,limit):
    root=Path(dir)
    need(root.is_absolute() and not root.is_symlink()
         and stat.S_IMODE(root.stat().st_mode)==0o700,"PRIVATE_ARCHIVE_DIR")
    p=root/name
    need(not p.is_symlink(),"ARCHIVE_FILE_SYMLINK")
    fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        s=os.fstat(fd)
        need(stat.S_ISREG(s.st_mode) and s.st_nlink==1
             and s.st_uid==os.geteuid() and not s.st_mode&0o077
             and 0<s.st_size<=limit,"ARCHIVE_SOURCE_UNSAFE")
        data=b""
        while len(data)<s.st_size:
            b=os.read(fd,min(256*1024,s.st_size-len(data)))
            need(bool(b),"ARCHIVE_SOURCE_TRUNCATED")
            data+=b
        now=os.fstat(fd)
        need((s.st_ino,s.st_dev,s.st_size,s.st_mtime_ns)==
             (now.st_ino,now.st_dev,now.st_size,now.st_mtime_ns),
             "ARCHIVE_SOURCE_CHANGED_DURING_READ")
        return data
    finally:os.close(fd)

def verify_bytes(files):
    need(set(files)==set(PINS),"REQUIRED_FILES_MISSING")
    for name,body in files.items():
        need(type(body) is bytes and digest(body)==PINS[name],
             "FROZEN_SOURCE_FILE_HASH_MISMATCH")
    plan=strict(files["plan.json"])
    prior=strict(files["independent-operational-audit.json"])
    status=strict(files["readiness.json"])
    need(plan["schema"]==PLAN_SCHEMA and plan["planned_cycles"]==24
         and plan["requested_cadence_seconds"]==15
         and plan["cpu_quota_percent"]==25
         and plan["source_sha256"]==PRECOMMIT_CODE
         and plan["sandbox_only"] is True
         and plan["trade_authority"] is False,
         "ORIGINAL_PRECOMMIT_CHANGED")
    need(prior["schema"]=="heptatrader.fx-source-health-real-24cycle-qualification.v1"
         and prior["result"]=="PASS_RESEARCH_ONLY_CACHE_FRESHNESS_NOT_PRODUCTION"
         and prior["precommitted_plan_sha256"]==PINS["plan.json"]
         and prior["actual_journal_original_bytes_sha256"]==PINS["original-journal.txt"]
         and prior["live_source_readiness_bytes_sha256"]==PINS["readiness.json"]
         and prior["ready_for_unattended_production_cutover"] is False
         and prior["broker_orders_sent"]==0
         and prior["trade_authority"] is False,
         "PRIOR_SOURCE_REPLAY_NOT_BINDING")
    events=[];counters=[]
    for raw in files["original-journal.txt"].splitlines():
        try:
            p=strict(raw)
        except (ValueError,UnicodeError):
            if EVENT_SCHEMA.encode() in raw or CACHED_SCHEMA.encode() in raw:
                raise AuditHold("MALFORMED_EVENT_OR_COUNTER")
            continue
        if type(p) is not dict:continue
        if p.get("schema")==EVENT_SCHEMA:events.append(p)
        elif p.get("schema")==CACHED_SCHEMA:counters.append(p)
    need(len(events)==24 and len(counters)==1,"NOT_24_CYCLES_OR_ONE_TERMINAL")
    prev=None;older_before_next=0
    for i,event in enumerate(events):
        need(event.get("schema")==EVENT_SCHEMA
             and type(event.get("cycle")) is int
             and event["cycle"]==i+1
             and event.get("state")=="READY_FOR_REVIEW_NOT_AUTHORIZATION"
             and event.get("usable")==24
             and event.get("trade_authority") is False
             and event.get("orders_sent")==0,
             "CYCLE_TAMPER_OR_NOT_READY")
        t=event.get("source_asof_ms")
        pub=event.get("published_ms")
        age=event.get("published_source_age_ms")
        assess=event.get("assess_ms")
        need(all(type(x) is int for x in (t,pub,age,assess))
             and 0<=age<=30000
             and 0<=assess<=30000
             and pub-t==age and abs(age-assess)<=2,
             "ORIGINAL_ASOF_PUBLISH_TIME_INVALID")
        if prev is None:
            need(t>=plan["frozen_at_ms"],"CYCLE_PRECEDED_PLAN")
        else:
            duration=t-prev["source_asof_ms"]
            expected=15000 if i>1 else max(15000,prev["assess_ms"]+1000)
            need(14000<=duration<=22000
                 and pub>prev["published_ms"],
                 "SOURCE_CADENCE_INVALID")
            older_before_next=max(older_before_next,
                  pub-prev["source_asof_ms"])
        prev=event
    cache=counters[0]
    need(cache["archive_inventories_full_sha256_reverified"]==24
         and cache["sealed_decoding_strictly_parsed"]>=256
         and cache["sealed_decoding_reused"]>=256*23
         and cache["sealed_hits_bytes_reverified_at_use"]==
               cache["sealed_decoding_reused"]
         and cache["unsealed_or_extra_strictly_reparsed"]>=24
         and cache["cached_original_quote_records"]<=180000
         and cache["cache_is_not_independent_broker_signature"] is True
         and cache["trade_authority"] is False
         and cache["broker_orders_sent"]==0,
         "ARCHIVE_SHA_CACHE_REVALIDATION_INVALID")
    need(older_before_next<=30000
         and older_before_next==prior["worst_age_before_next_publish_ms"]
         and prior["cache_full_archive_rechecks"]==24
         and prior["cached_hit_bytes_sha_rechecks"]==cache["sealed_decoding_reused"],
         "INDEPENDENT_FRESHNESS_SLA_NOT_REPRODUCED")
    need(status["schema"]==SOURCE_SCHEMA
         and status["period_sha256"]==PERIOD
         and status["rule"]==RULE
         and status["at_ms"]==events[-1]["source_asof_ms"]
         and status["state"]=="READY_FOR_REVIEW_NOT_AUTHORIZATION"
         and status["usable"]==24 and status["unusable"]==0
         and status["automatic_trial_registration"] is False
         and status["trading_authority"] is False and status["orders_sent"]==0
         and status["counts"]==dict(Counter(x["status"] for x in status["probes"]))
         and len(status["probes"])==24,
         "ORIGINAL_24PROBE_PERIOD_OR_GUARD_CHANGED")
    need(status["source_prefix_digest"]==digest(canonical(status["source_prefixes"]))
         and status["latest_quote_age_ms"]==
                status["at_ms"]-status["latest_source_quote_ms"]
         and 0<=status["latest_quote_age_ms"]<=5000,
         "ORIGINAL_SOURCE_PREFIX_OR_QUOTE_FRESHNESS_INVALID")
    return {
      "schema":SCHEMA,
      "result":"CROSSHOST_STD_LIB_SOURCE_CACHE_FRESHNESS_REPRODUCED_RESEARCH_ONLY",
      "host_role":"SEPARATE_PHYSICAL_EVIDENCE_REVIEW_NOT_IB_SOURCE",
      "source_sha256":dict(PINS),
      "original_precommit_before_first_cycle":True,
      "actual_verified_original_cycles":24,
      "source_readiness_usable_probes_at_last_cycle":24,
      "actual_max_asof_age_pre_next_pub_ms":older_before_next,
      "original_archive_complete_rehashes":24,
      "cache_hit_original_rehashes":cache["sealed_decoding_reused"],
      "unsealed_source_reparses":cache["unsealed_or_extra_strictly_reparsed"],
      "product_rollout_qualified":False,
      "upstream_ib_market_source_signed":False,
      "hardware_custody_timestamp_or_native_root_proven":False,
      "original_four_censored_decisions_unchanged":True,
      "trade_authority":False,"broker_orders_sent":0,
    }

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--archive",type=Path,required=True)
    p.add_argument("--out",type=Path,required=True)
    a=p.parse_args()
    contents={name:read_bytes(a.archive,name,256*1024) for name in PINS}
    report=verify_bytes(contents)
    report["audited_at_utc"]=datetime.now(timezone.utc).isoformat()
    path=a.out
    need(path.is_absolute() and not path.is_symlink()
         and path.parent==a.archive and not path.exists(),"OUTPUT_PATH_NOT_PRIVATE_NEW")
    raw=canonical(report)+b"\n"
    f=os.open(path,os.O_EXCL|os.O_CREAT|os.O_NOFOLLOW|os.O_WRONLY,0o600)
    with os.fdopen(f,"wb") as h:
        h.write(raw);h.flush();os.fsync(h.fileno())
    dfd=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:os.fsync(dfd)
    finally:os.close(dfd)
    print(json.dumps({
        "result":report["result"],
        "original_cycles":report["actual_verified_original_cycles"],
        "max_asof_age_ms":report["actual_max_asof_age_pre_next_pub_ms"],
        "production_authority":False
    },sort_keys=True))

if __name__=="__main__":main()
