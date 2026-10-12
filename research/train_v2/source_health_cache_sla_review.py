"""Independent read-only journal review for finite SHA-verified cache research lane.

Original per-cycle as-of/publish timestamps are retained, not synthesized.
This does NOT attest the underlying IB quote signature, model, or systemd
CPU resource allocation cryptographically.
"""
from __future__ import annotations
import argparse
from datetime import datetime,timezone
import hashlib,json,os,stat,subprocess
from pathlib import Path

from .source_health_cadence_sla_audit import (
    AuditHold, audit_cycles, canonical, extract_from_journal, strict,
    write_private
)

UNIT = "heptatrader-health-cache25-r2-20261013.service"
SCHEMA = "heptatrader.readiness-verified-sealed-cache-counters.v1"
REPORT_SCHEMA = "heptatrader.readiness-verified-cache-publish-sla.v2"
EXPECTED_CYCLES = 24
FROZEN_QUOTA_LABEL = 25
FROZEN_CADENCE = 15
MIN_SEALED_SEGMENTS = 256
MAX_RECORDS = 180000
FULL_FIELDS = frozenset((
    "schema", "archive_inventories_full_sha256_reverified",
    "sealed_decoding_reused","sealed_decoding_strictly_parsed",
    "sealed_hits_bytes_reverified_at_use",
    "unsealed_or_extra_strictly_reparsed",
    "cached_sealed_segments","cached_original_quote_records",
    "cache_is_not_independent_broker_signature",
    "trade_authority","broker_orders_sent"
))
INT_FIELDS = FULL_FIELDS-{
    "schema","cache_is_not_independent_broker_signature",
    "trade_authority"
}

def need(ok,why):
    if not ok:
        raise AuditHold(why)

def identify_terminal(raw_lines):
    found=[]
    for raw in raw_lines:
        value=raw.strip()
        if isinstance(value,bytes):
            value=value.decode("utf8","strict")
        if not value or not value.startswith("{"):
            continue
        try: item=strict(value)
        except (ValueError, UnicodeError, AuditHold) as e:
            if SCHEMA in value:
                raise AuditHold("CORRUPT_CACHED_COUNTER_JOURNAL") from e
            continue
        if type(item) is dict and item.get("schema")==SCHEMA:
            found.append(item)
    need(len(found)==1,"MISSING_OR_DUPLICATE_CACHED_COUNTER_JOURNAL")
    obj=found[0]
    need(set(obj)==FULL_FIELDS, "CACHED_COUNTER_FIELDS_CHANGED")
    need(all(type(obj[k]) is int and 0<=obj[k]<=1_000_000
             for k in INT_FIELDS), "CACHED_COUNTER_TYPE_OR_RANGE")
    need(obj["cache_is_not_independent_broker_signature"] is True
         and obj["trade_authority"] is False
         and obj["broker_orders_sent"]==0,
         "CACHED_MONITOR_CLAIMS_UNAUTHORIZED_TRUST_OR_ORDER")
    need(obj["archive_inventories_full_sha256_reverified"]==EXPECTED_CYCLES,
         "NOT_EVERY_CYCLE_REVERIFIED_FULL_SOURCE_ARCHIVE")
    need(obj["sealed_decoding_strictly_parsed"]>=MIN_SEALED_SEGMENTS
         and obj["sealed_decoding_reused"]>=MIN_SEALED_SEGMENTS*(EXPECTED_CYCLES-1),
         "SEALED_CACHED_DECODER_NOT_ACTIVE_FOR_ALL_CYCLES")
    need(obj["sealed_hits_bytes_reverified_at_use"]==obj["sealed_decoding_reused"],
         "CACHED_HIT_BYTES_NOT_REVERIFIED_BEFORE_USE")
    need(obj["unsealed_or_extra_strictly_reparsed"]>=EXPECTED_CYCLES,
         "UNSEALED_SOURCE_WAS_NOT_REPARSED")
    need(0<obj["cached_sealed_segments"]<=320 and
         0<obj["cached_original_quote_records"]<=MAX_RECORDS,
         "CACHE_MEMORY_BOUNDS_CHANGED")
    return obj

def review(raw_lines):
    decoded=[s.decode("utf8","strict") if isinstance(s,bytes) else s
             for s in raw_lines]
    events=extract_from_journal(decoded)
    health=audit_cycles(events,quota_percent=FROZEN_QUOTA_LABEL,
                        expected_cycles=EXPECTED_CYCLES,
                        requested_period_seconds=FROZEN_CADENCE)
    cache=identify_terminal(raw_lines)
    health.update({
        "schema":REPORT_SCHEMA,
        "source_of_events":"ACTUAL_ORIGINAL_USER_SYSTEMD_JOURNAL_NOT_SYNTHETIC",
        "source_unit":UNIT,
        "sealed_cache_journal_terminal":cache,
        "first_cold_assessment_ms":events[0]["assess_ms"],
        "warm_assessment_max_ms":max(x["assess_ms"] for x in events[1:]),
        "warm_assessment_mean_ms":round(
            sum(x["assess_ms"] for x in events[1:])/(len(events)-1),3),
        "all_24_cycle_health_states_are_ready":
            all(x["state"]=="READY_FOR_REVIEW_NOT_AUTHORIZATION"
                and x["usable"]>=20 for x in events),
        "true_frozen_30s_asof_interval_SLA_met":
            health["all_observed_intervals_continuously_fresh"],
        "production_monitor_cadence_or_CPU_quota_may_not_be_changed":True,
        "future_03_35_06_50_10_05_original_study_not_relabelled":True,
        "same_host_cache_counter_is_not_an_independent_cryptographic_witness":True,
        "research_only_no_live_trading_or_auto_promotion":True,
    })
    return health,events,cache

def run(output_dir):
    lines=subprocess.check_output(
        ["journalctl","--user","-u",UNIT,"--no-pager","-o","cat"],
        timeout=20)
    raw=[line for line in lines.splitlines() if line.strip()]
    result,events,cache=review(raw)
    output_dir=Path(output_dir)
    normalized=b"".join(canonical(event)+b"\n" for event in events)
    terminal=canonical(cache)+b"\n"
    result.update({
        "audited_at_utc":datetime.now(timezone.utc).isoformat(),
        "canonical_24_cycle_events_sha256":hashlib.sha256(normalized).hexdigest(),
        "canonical_cache_final_counters_sha256":hashlib.sha256(terminal).hexdigest(),
        "auditor_code_sha256":hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "broker_orders_sent":0,
        "native_product_model_or_witness_promoted":False,
        "short_duration_is_not_production_durability_proof":True,
    })
    write_private(output_dir/"cached-original-24-cycles.jsonl",normalized)
    write_private(output_dir/"cached-terminal-counters.json",terminal)
    write_private(output_dir/"cached-sla-review.json",
                  json.dumps(result,indent=2,sort_keys=True,allow_nan=False).encode()+b"\n")
    print(json.dumps({
        "state":"25PCT_VERIFIED_CACHE_RESEARCH_SLA_REPLAY",
        "sample_count":len(events),
        "intervals_continuously_fresh":
             result["true_frozen_30s_asof_interval_SLA_met"],
        "max_before_next_publish_age_ms":
             result["max_stale_age_immediately_before_next_publish_ms"],
        "warm_assessment_max_ms":result["warm_assessment_max_ms"],
        "full_original_archive_verification_count":
             cache["archive_inventories_full_sha256_reverified"],
        "broker_orders_sent":0},sort_keys=True),flush=True)
    return result

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--output-dir",required=True,type=Path)
    args=p.parse_args()
    os.umask(0o077)
    run(args.output_dir)

if __name__=="__main__":
    main()
