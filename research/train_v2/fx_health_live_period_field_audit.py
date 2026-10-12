"""Independently recompute original PAPER monitor freshness from field bytes.

The sampling report is research evidence, not a signed independent broker
quote or a production monitor health authorization. No broker/driver imports.
"""
from __future__ import annotations
import argparse,hashlib,json,os,stat
from pathlib import Path

SCHEMA="heptatrader.period-derived-original-source-readiness-field.v2"
AUDIT="heptatrader.original-monitor-freshness-field-audit.v1"
PRECOMMIT_PLAN="02aa4a2662b743203cc925d09d3250bcc0e02155cf5d00bb319b0526448aaa04"
VALID_STATE={"READY_FOR_REVIEW_NOT_AUTHORIZATION","HOLD_NEW_FUSION_WINDOW"}

class FieldHold(ValueError):pass
def require(test,why):
    if not test:raise FieldHold(why)
def sha(b):return hashlib.sha256(b).hexdigest()
def unique(pairs):
    out={}
    for key,val in pairs:
        require(key not in out,"DUPLICATE_JSON_KEY")
        out[key]=val
    return out
def parse(b):
    return json.loads(b,object_pairs_hook=unique,
        parse_constant=lambda _:(_ for _ in ()).throw(FieldHold("NONFINITE_JSON")))
def read_private(path,bound=1024*1024):
    p=Path(path)
    require(p.is_absolute() and not p.is_symlink(),"UNTRUSTED_SOURCE_PATH")
    s=p.stat()
    require(stat.S_ISREG(s.st_mode) and s.st_uid==os.geteuid()
            and s.st_nlink==1 and not s.st_mode&0o077
            and 0<s.st_size<=bound,"ORIGINAL_RESEARCH_FILE_NOT_PRIVATE")
    return p.read_bytes()
def validate(log,summary,period_sha):
    require(type(period_sha) is str and len(period_sha)==64 and
            all(c in "0123456789abcdef" for c in period_sha),
            "PAPER_PERIOD_NOT_SHA256")
    require(summary.get("schema")==SCHEMA and
            summary.get("original_paper_period_sha256")==period_sha and
            summary.get("sample_count")==360 and
            summary.get("sampling_interval_target_ms")==250 and
            summary.get("trading_or_original_trial_modified") is False and
            summary.get("broker_requests_or_orders")==0,
            "FIELD_PROVENANCE_OR_RELEASE_INVALID")
    lines=log.splitlines()
    require(len(lines)==360 and all(line for line in lines),
            "FROZEN_360_SAMPLES_REQUIRED")
    require(sha(log)==summary.get("sample_log_sha256"),
            "SOURCE_SAMPLE_BYTES_CHANGED")
    count={"readable":0,"missing":0,"age_le_30s":0,"age_over_30s":0,
           "ready":0,"hold":0,"incorrect_or_invalid":0}
    oldest=None;latest=None;timestamps=[];different=set()
    for line in lines:
        x=parse(line)
        require(type(x) is dict and x.get("period_sha256")==period_sha
                and type(x.get("at_ms")) is int and x["at_ms"]>0,
                "SAMPLE_SCOPE_OR_CLOCK_INVALID")
        if timestamps:
            require(125<=x["at_ms"]-timestamps[-1]<=2000,
                    "SAMPLE_POLL_INTERVAL_CHANGED_OR_GAPPED")
        timestamps.append(x["at_ms"])
        if "error" in x:
            require(x["error"] in ("ENOENT","WRONG_OR_UNDECODABLE"),
                    "UNKNOWN_MONITOR_ERROR")
            count["missing" if x["error"]=="ENOENT" else "incorrect_or_invalid"]+=1
            require(set(x)=={"at_ms","period_sha256","error"},
                    "SAMPLE_ERROR_NOT_HONEST")
            continue
        require(set(x)=={"at_ms","period_sha256","producer_at_ms","age_ms",
                         "producer_state","producer_usable","producer_sha256"},
                "MONITOR_SAMPLE_FIELDS_CHANGED")
        stamp=x["producer_at_ms"];age=x["age_ms"]
        require(type(stamp) is int and type(age) is int and
                age==x["at_ms"]-stamp and 0<=age<6*3600000,
                "MONITOR_ASOF_OR_CLOCK_DISCREPANCY")
        require(x["producer_state"] in VALID_STATE
                and type(x["producer_usable"]) is int and
                0<=x["producer_usable"]<=24,
                "MONITOR_PROBE_STATUS_UNTRUSTED")
        dig=x["producer_sha256"]
        require(type(dig) is str and len(dig)==64 and
                all(c in "0123456789abcdef" for c in dig),
                "MONITOR_SOURCE_DIGEST_NOT_SHA")
        count["readable"]+=1
        count["age_le_30s" if age<=30000 else "age_over_30s"]+=1
        count["ready" if x["producer_state"]== "READY_FOR_REVIEW_NOT_AUTHORIZATION"
              else "hold"]+=1
        oldest=age if oldest is None else min(oldest,age)
        latest=age if latest is None else max(latest,age)
        different.add(dig)
    require(timestamps[-1]-timestamps[0]>=88000,
            "INSUFFICIENT_OBSERVATION_SPAN")
    require(count==summary.get("counts") and
            len(different)==summary.get("unique_producer_status_digests") and
            oldest==summary.get("age_min_ms") and
            latest==summary.get("age_max_ms"),
            "PUBLISHED_SOURCE_FRESHNESS_COUNTERS_INCORRECT")
    require(count["readable"]+count["missing"]+count["incorrect_or_invalid"]==360,
            "SAMPLE_DENOMINATOR_MISMATCH")
    return {
        "sample_count":360,
        "observed_span_ms":timestamps[-1]-timestamps[0],
        "source_status_distinct_sha256_count":len(different),
        "readable":count["readable"],"file_missing":count["missing"],
        "ready":count["ready"],"hold":count["hold"],
        "fresh_at_most_30s":count["age_le_30s"],
        "older_than_30s":count["age_over_30s"],
        "shortest_original_monitor_age_ms":oldest,
        "longest_original_monitor_age_ms":latest,
        "snapshot_30s_requirement_met_at_all_sampled_times":
            count["age_over_30s"]==0 and count["readable"]==360,
        "sampling_is_not_random_or_a_full_prod_durability_test":True,
        "original_quote_source_or_broker_signature_verified":False,
    }
def run(*,sample_file,summary_file,period_config,original_plan,retraction,output):
    config=parse(Path(period_config).read_bytes())
    period=config["period_sha256"]
    require(len(period)==64 and
            sha(Path(config["period_path"]).read_bytes())==period,
            "CONFIG_ORIGINAL_PAPER_PERIOD_SHA_NOT_VALID")
    old=read_private(original_plan)
    original=parse(old)
    require(sha(old)==PRECOMMIT_PLAN and
            original.get("period_sha256")==period and
            original.get("behavior_policy")=="NO_TRADE" and
            original.get("trade_authority") is False and
            original.get("not_native_train_v2_120min_spec") is True,
            "FROZEN_PRECOMMIT_IDENTITY_CHANGED")
    wrong=parse(read_private(retraction))
    require(wrong.get("schema")=="heptatrader.source-path-diagnostic-invalidated.v1"
            and wrong.get("valid_paper_period_sha256")==period
            and wrong.get("bad_name_length")==42 and
            wrong.get("actual_name_length")==64 and
            wrong.get("previous_wrong_path_enoent_does_not_prove_filesystem_or_monitor_failure") is True,
            "INCORRECT_PATH_DIAGNOSTIC_NOT_WITHDRAWN")
    raw=read_private(sample_file,256*1024)
    summary=parse(read_private(summary_file,16384))
    result=validate(raw,summary,period)
    result.update({
        "schema":AUDIT,"original_PAPER_period":period,
        "observed_status_log_sha256":sha(raw),
        "claimed_source_summary_sha256":sha(read_private(summary_file,16384)),
        "invalidated_old_path_observations_shall_not_be_used":True,
        "full_original_censored_window_unmodified":True,
        "monitor_run_unchanged":True,
        "broker_queries_or_orders_sent":0,
        "model_training_or_promotion_allowed":False,
        "production_monitor_rollout_authorized":False,
    })
    output=Path(output)
    require(output.is_absolute() and not output.exists() and not output.is_symlink(),
            "AUDIT_REPORT_OVERWRITE_NOT_ALLOWED")
    output.parent.mkdir(parents=True,mode=0o700,exist_ok=True)
    require(stat.S_IMODE(output.parent.stat().st_mode)==0o700,
            "OUTPUT_DIRECTORY_NOT_PRIVATE")
    raw_out=json.dumps(result,sort_keys=True,indent=2,allow_nan=False).encode()+b"\n"
    fd=os.open(output,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,"wb") as f:
        f.write(raw_out);f.flush();os.fsync(f.fileno())
    d=os.open(output.parent,os.O_DIRECTORY|os.O_RDONLY)
    try:os.fsync(d)
    finally:os.close(d)
    return result
def main():
    p=argparse.ArgumentParser()
    for k in ("sample-file","summary-file","period-config","original-plan",
              "retraction","output"):
        p.add_argument("--"+k,required=True,type=Path)
    args=p.parse_args()
    r=run(sample_file=args.sample_file,
          summary_file=args.summary_file,
          period_config=args.period_config,
          original_plan=args.original_plan,
          retraction=args.retraction,output=args.output)
    print(json.dumps({
        "state":"ORIGINAL_HEALTH_MONITOR_REAL_PERIOD_BOUND_SLA_RESEARCH_ONLY",
        "sample_count":r["sample_count"],
        "unavailable":r["file_missing"],
        "source_stale_over_30s":r["older_than_30s"],
        "production_freshness_SLA_met":r["snapshot_30s_requirement_met_at_all_sampled_times"],
        "trading_authority":False,
    },sort_keys=True),flush=True)
if __name__=="__main__":main()
