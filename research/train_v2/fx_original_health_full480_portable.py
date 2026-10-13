"""Strict portable FINAL-480 original PAPER source-health verifier.

Standalone Python >=3.9 standard library; does not extract untrusted archives,
connect to brokers, alter a source service, trade, train, or promote a model.
Initially fails closed until the actual naturally terminated first-party final
and independent full report digests have been pinned into this exact code.
"""
from __future__ import annotations
import argparse,hashlib,io,json,os,stat,tarfile
from collections import Counter
from datetime import datetime,timezone
from pathlib import Path

PIN={
 "plan":"490375e6cfc4cea69b0aeb8ece2bd836228d1b3d086793555ed35d920bc8f6be",
 "worker":"3b880a8bd59b515b128a5566e713767ac822862d695f1f8c4237bb018fbc0d59",
 "sandbox":"b18abaf07255b4183f96dcdad8ed5059cbbd8e2f82616c81cc5597fef513acaa",
 "first320_head":"88eb51bab98cb14911acb0eda3b649583a0db505580704a135b7c6fde915aff1",
 "first320_report":"6e420025e52c1e594f4639d48021fa01e3d16471b9bc5fbbdb4f4c28108814d9",
 "final":"eb4b0f4347c034c3d4e2ab4acb885772bc61fcdce1c54a2f8170140fc6e8cc6d",
 "full_report":"545af6fbee36436770d456a84ecd1bf813065f6b4e46a6e69e12b9e202408890",
}
SCHEMA="heptatrader.fx-original-full480-physical-source-health-audit.v1"
COHORT="heptatrader.fx-original-sustained-source-archive.v1"
RULE=dict(schema="heptatrader.quote-readiness.v1",input_probes=24,spacing_ms=60000,
 horizon_ms=300000,input_grace_ms=60000,endpoint_tolerance_ms=5000,
 path_gap_max_ms=30000,minimum_usable=20,maximum_missing=4,source_ttl_ms=5000,
 statistics_scope="OVERLAPPING_OPERATIONAL_COVERAGE_NOT_PERFORMANCE")
N=480
STEP=15000
MAX_AGE=30000

class Hold(ValueError):pass
def need(ok,why):
 if not ok:raise Hold(why)
def sha(x):return hashlib.sha256(x).hexdigest()
def canonical(x):return json.dumps(x,sort_keys=True,separators=(",",":"),allow_nan=False).encode()
def parse(raw):
 def strict_pairs(pairs):
  v={}
  for k,val in pairs:
   need(k not in v,"DUPLICATE_JSON_KEY")
   v[k]=val
  return v
 return json.loads(raw,object_pairs_hook=strict_pairs,
   parse_constant=lambda _: (_ for _ in ()).throw(Hold("NONFINITE_JSON")))
def read_private(path,max_bytes=8*1024*1024):
 p=Path(path)
 need(p.is_absolute() and not p.is_symlink() and p.is_file()
      and p.stat().st_uid==os.geteuid() and p.stat().st_nlink==1
      and p.stat().st_size<=max_bytes
      and not stat.S_IMODE(p.stat().st_mode)&0o077,"PRIVATE_SOURCE_INVALID")
 return p.read_bytes()
def archive(path):
 blob=read_private(path)
 mandatory={"plan.json","worker-start.json","sandbox.json","final.json"}|{
  f"cycle-{i:02d}-receipt.json" for i in range(N)}
 optional={f"cycle-{i:02d}-status.json" for i in range(N)}
 out={}
 with tarfile.open(fileobj=io.BytesIO(blob),mode="r:gz") as tf:
  for m in tf:
   need(m.name in mandatory|optional and m.name not in out
        and m.isfile() and not m.linkname
        and 0<m.size<=1024*1024 and m.mode&0o077==0,
        "UNTRUSTED_OR_DUPLICATE_ARCHIVE_MEMBER")
   contents=tf.extractfile(m).read()
   need(len(contents)==m.size,"ARCHIVE_SHORT_MEMBER")
   out[m.name]=contents
 need(mandatory.issubset(out),"FULL_480_RECEIPTS_OR_FINAL_MISSING")
 need(len(out)<=len(mandatory)+len(optional),"EXTRA_MEMBER")
 return out,sha(blob)

def verify(files,prefix_pair,full_pair,pins=PIN):
 # pin fields cannot come from the untrusted tar or peer-supplied report
 for key in ("plan","worker","sandbox","first320_head",
             "first320_report","final","full_report"):
  need(isinstance(pins.get(key),str) and len(pins[key])==64,
       "FULL_480_INDEPENDENT_ANCHORS_NOT_YET_PINNED")
 for key,name in (("plan","plan.json"),("worker","worker-start.json"),
                  ("sandbox","sandbox.json"),("final","final.json")):
  need(sha(files[name])==pins[key],"ORIGINAL_FULL_ROOT_HASH_CHANGED")
 need(sha(prefix_pair[0])==pins["first320_report"] and
      sha(full_pair[0])==pins["full_report"],
      "INDEPENDENT_PREEXISTING_AUDIT_BYTES_CHANGED")
 plan,worker,sandbox,final=[parse(files[k]) for k in
   ("plan.json","worker-start.json","sandbox.json","final.json")]
 prior,complete=prefix_pair[1],full_pair[1]
 need(plan.get("schema")==COHORT+".plan.v1"
      and plan.get("full_snapshots_count")==N
      and plan.get("candidate_assessment_cadence_ms")==STEP
      and plan.get("original_monitor_rules")==RULE
      and plan.get("maximum_status_age_ms")==MAX_AGE
      and plan.get("maximum_schedule_delay_ms")==3000
      and plan.get("cycle_schedule_ms")==[
        plan["first_cycle_start_ms"]+i*STEP for i in range(N)]
      and plan.get("no_training_or_trading") is True
      and plan.get("no_production_monitor_cutover") is True
      and plan.get("trade_authority") is False
      and plan.get("broker_requests_or_orders_sent")==0,
      "ORIGINAL_PRESEALED_PRODUCT_SCOPE_CHANGED")
 need(worker.get("schema")==COHORT+".worker.v1"
      and worker.get("plan_sha256")==pins["plan"]
      and worker.get("no_restart") is True
      and worker.get("production_monitor_not_modified") is True
      and worker.get("broker_orders_sent")==0
      and worker.get("start_ms")<plan["first_cycle_start_ms"]
      and all(sandbox.get(k) is True for k in (
         "landlock_enforced","seccomp_enforced","socket_creation_denied",
         "bearer_read_denied","only_output_writable","output_write_verified")),
      "UNTRUSTED_MONITOR_WORKER_OR_SANDBOX")
 need(final.get("schema")==COHORT+".final.v1"
      and final.get("plan_sha256")==pins["plan"]
      and final.get("no_prod_monitor_cutover") is True
      and final.get("production_trading_grant") is False
      and final.get("broker_orders_sent")==0
      and type(final.get("cycles")) is list
      and len(final["cycles"])==N,
      "FROZEN_ORIGINAL_FINAL_NOT_COMPLETE")
 need(prior.get("contiguous_prefix_cycles_checked")==320
      and prior.get("future_cycles_NOT_verified")==160
      and prior.get("prefix_original_receipt_hash_chain_head")==pins["first320_head"]
      and prior.get("cohort_complete") is False
      and prior.get("production_monitor_cadence_qualified") is False
      and len(prior.get("per_cycle",[]))==320,
      "FIRST_320_CORROBORATION_CHANGED")
 need(complete.get("schema")==
      "heptatrader.fx-original-sustained-health-independent-audit.v1"
      and complete.get("plan_sha256")==pins["plan"]
      and complete.get("final_sha256")==pins["final"]
      and complete.get("cycle_count_expected")==N
      and complete.get("trade_authority") is False
      and complete.get("prod_monitor_cadence_switch_approved") is False,
      "FULL_POCKET4_REPORT_SCOPE_CHANGED")
 previous="0"*64
 rows=[]
 ready=hold=missing=0
 for i in range(N):
  raw=files[f"cycle-{i:02d}-receipt.json"];receipt=parse(raw);h=sha(raw)
  target=plan["cycle_schedule_ms"][i]
  need(receipt.get("schema")==COHORT+".cycle.v1"
       and receipt.get("index")==i and receipt.get("frozen_target_ms")==target
       and receipt.get("previous_receipt_sha256")==previous
       and receipt.get("original_period_sha256")==plan["period_sha256"]
       and receipt.get("orders_sent")==0 and receipt.get("trade_authority") is False
       and type(receipt.get("assessment_started_ms")) is int
       and type(receipt.get("record_sealed_at_ms")) is int
       and receipt["assessment_started_ms"]>=target
       and receipt["record_sealed_at_ms"]>=receipt["assessment_started_ms"],
       "BROKEN_FULL_RECEIPT_CHAIN_OR_TIMING")
  previous=h
  need(final["cycles"][i]=={"index":i,"status":receipt["state"],
        "receipt_sha256":h,"source_ready":receipt["source_ready"]},
        "ORIGINAL_TERMINAL_NOT_BOUND_TO_CYCLE")
  if i==319:
   need(h==pins["first320_head"],"PINNED_FIRST320_RECEIPT_HEAD_CHANGED")
  raw_status=files.get(f"cycle-{i:02d}-status.json")
  if raw_status is None:
   need(receipt.get("status_sha256") is None
        and receipt.get("publisher_eligible") is False
        and receipt.get("source_ready") is False
        and receipt.get("state") in (
          "CENSORED_MISSED_FROZEN_CYCLE","HOLD_SOURCE_ASSESSMENT_FAILURE"),
        "MISSING_SOURCE_STATUS_RELABELED_AS_SUCCESS")
   missing+=1
   rows.append({"index":i,"state":receipt["state"],"receipt_sha256":h,
                "missing_original_health":True})
   continue
  status=parse(raw_status);sh=sha(raw_status)
  need(sh==receipt.get("status_sha256"),"ORIGINAL_HEALTH_BYTES_CHANGED")
  at=status.get("at_ms");pub=receipt.get("published_at_ms")
  start=receipt["assessment_started_ms"];seal=receipt["record_sealed_at_ms"]
  need(type(at) is int and type(pub) is int and
       target<=start<=target+3000 and start<=at<=start+3000 and
       at<=pub<=seal and pub-at>=0,
       "ORIGINAL_EVALUATION_CLOCK_OR_SCHEDULE")
  need(status.get("schema")=="heptatrader.quote-readiness-status.v1"
       and status.get("period_sha256")==plan["period_sha256"]
       and status.get("rule")==RULE
       and type(status.get("probes")) is list
       and len(status["probes"])==24
       and all(type(q) is dict and type(q.get("status")) is str
               for q in status["probes"]),
       "ORIGINAL_PAPER_PERIOD_OR_24_PROBES_CHANGED")
  counts=dict(Counter(q["status"] for q in status["probes"]))
  usable=counts.get("USABLE",0)
  need(counts==status.get("counts") and status.get("usable")==usable
       and status.get("unusable")==24-usable
       and type(status.get("source_prefixes")) is list
       and sha(canonical(status["source_prefixes"]))==
             status.get("source_prefix_digest"),
       "ORIGINAL_MARKET_SOURCE_PREFIX_OR_PROBE_CHANGED")
  last=status.get("latest_source_quote_ms");age=status.get("latest_quote_age_ms")
  need(type(last) is int and type(age) is int and at-last==age
       and status.get("automatic_trial_registration") is False
       and status.get("trading_authority") is False
       and status.get("orders_sent")==0,
       "ORIGINAL_QUOTE_SOURCE_CLOCK_OR_AUTHORITY_CHANGED")
  source_ready=status.get("state")=="READY_FOR_REVIEW_NOT_AUTHORIZATION"
  need(status.get("state") in (
        "READY_FOR_REVIEW_NOT_AUTHORIZATION","HOLD_NEW_FUSION_WINDOW"),
        "NONORIGINAL_SOURCE_STATE")
  if source_ready:
   need(status.get("reasons")==[] and 0<=age<=5000
        and usable>=20 and 24-usable<=4,
        "FALSE_READY_FOR_UNQUALIFIED_MARKET_SOURCE")
   ready+=1
  else:
   hold+=1
  need(receipt.get("source_ready")==source_ready
       and receipt.get("source_usable_probes")==usable
       and receipt.get("original_asof_ms")==at
       and receipt.get("source_prefix_digest")==status.get("source_prefix_digest"),
       "HEALTH_STATUS_AND_RECEIPT_DISAGREE")
  eligible=receipt.get("publisher_eligible")
  if eligible:
   need(receipt.get("issues")==[] and
        receipt.get("state")=="CAPTURED_ORIGINAL_HEALTH" and
        pub-at<=MAX_AGE,"FALSE_PUBLICATION_SLA_PASS")
  else:
   need(receipt.get("issues") and
        receipt.get("state")=="HOLD_UNQUALIFIED_SNAPSHOT",
        "PRODUCER_HOLD_NOT_RETAINED")
  if i<320:
   ref=prior["per_cycle"][i]
   need(ref.get("receipt_sha256")==h and
        ref.get("status_sha256")==sh and
        ref.get("asof_ms")==at,
        "PREEXISTING_PINNED_320_REPORT_DISAGREES")
  rows.append({"index":i,"state":status["state"],"at_ms":at,
       "published_ms":pub,"age_at_publish_ms":pub-at,
       "started_late_ms":start-target,"status_sha256":sh,
       "receipt_sha256":h,"publisher_eligible":eligible})
 need(previous==final["receipt_chain_head"]
      and previous==complete.get("original_cycle_record_chain_head")
      and complete.get("final_sha256")==pins["final"]
      and final["full_snapshots_written"]==N-missing
      and complete.get("complete_original_health_json_count")==N-missing
      and complete.get("source_ready_cycle_count")==ready
      and complete.get("source_hold_cycle_count")==hold
      and complete.get("missing_or_censored_original_health_count")==missing,
      "FULL_ORIGINAL_TERMINAL_OR_OFFLINE_REPORT_DISAGREEMENT")
 all_have=missing==0
 max_publish=max((x["age_at_publish_ms"] for x in rows if
                  "age_at_publish_ms" in x),default=None)
 pairs=[(a,b) for a,b in zip(rows,rows[1:]) if
        "at_ms" in a and "at_ms" in b]
 max_before=max((b["published_ms"]-a["at_ms"] for a,b in pairs),
                default=None) if all_have else None
 delay=max((r["started_late_ms"] for r in rows if
            "started_late_ms" in r),default=None)
 timing=bool(all_have and max_publish is not None and
             max_publish<=MAX_AGE and max_before is not None and
             max_before<=MAX_AGE and delay is not None and delay<=3000)
 need(complete.get("worst_local_published_source_age_ms")==max_publish
      and complete.get("worst_source_age_just_before_next_publication_ms")==max_before
      and complete.get("all_decision_time_snapshot_ages_within_30s")==timing,
      "DIFFERENT_FULL_COHORT_SLA_RECOMPUTATION")
 return {
  "schema":SCHEMA,"verified_at_utc":datetime.now(timezone.utc).isoformat(),
  "original_plan_sha256":pins["plan"],"original_final_sha256":pins["final"],
  "original_full_report_sha256":pins["full_report"],
  "original_first320_audit_sha256":pins["first320_report"],
  "original_first320_chain_head":pins["first320_head"],
  "original_full480_chain_head":previous,"verified_480_records":N,
  "complete_original_source_snapshots":N-missing,
  "censored_or_missing_snapshots":missing,
  "source_ready_count":ready,"source_hold_count":hold,
  "max_original_source_age_at_publication_ms":max_publish,
  "max_original_source_age_before_next_publication_ms":max_before,
  "full_source_snapshot_SLA_met":timing,
  "original_full480_source_all_ready":(ready==N and missing==0),
  "full_original_source_integrity_verified":True,
  "actual_broker_IB_quote_signature_verified":False,
  "independent_product_clock_or_witness_enrolled":False,
  "automatic_training_or_model_promotion_authorized":False,
  "production_monitor_cutover_allowed":False,
  "actual_filled_trading_net_profit_attested":False,
  "broker_requests_or_orders_sent":0,"trade_authority":False,
 }

def main():
 p=argparse.ArgumentParser()
 for name in ("bundle","prefix-report","full-report","output"):
  p.add_argument("--"+name,type=Path,required=True)
 a=p.parse_args()
 docs,archive_sha=archive(a.bundle)
 prefix_raw=read_private(a.prefix_report);full_raw=read_private(a.full_report)
 report=verify(docs,(prefix_raw,parse(prefix_raw)),(full_raw,parse(full_raw)))
 report["original_archive_sha256"]=archive_sha
 report["portable_verifier_code_sha256"]=sha(Path(__file__).read_bytes())
 target=a.output
 need(target.is_absolute() and not target.exists() and
      not target.is_symlink() and target.parent.is_dir() and
      target.parent.stat().st_uid==os.geteuid() and
      stat.S_IMODE(target.parent.stat().st_mode)==0o700,
      "NEW_PRIVATE_OUTPUT_REQUIRED")
 raw=canonical(report)+b"\n"
 fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
 with os.fdopen(fd,"wb") as f:f.write(raw);f.flush();os.fsync(f.fileno())
 d=os.open(target.parent,os.O_RDONLY|os.O_DIRECTORY)
 try:os.fsync(d)
 finally:os.close(d)
 print(json.dumps({"state":"FULL_480_ORIGINAL_ARCHIVE_INDEPENDENTLY_REPARSED",
    "records":report["complete_original_source_snapshots"],
    "ready":report["source_ready_count"],"hold":report["source_hold_count"],
    "max_before_next_ms":report["max_original_source_age_before_next_publication_ms"],
    "status_SLA_only":report["full_source_snapshot_SLA_met"],
    "production_cutover_allowed":False,"orders_sent":0,
    "receipt_sha256":sha(raw)},sort_keys=True))
if __name__=="__main__":main()
