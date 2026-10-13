"""Standalone offline cross-host audit of exact first 240 frozen PAPER health snapshots.
Reads an untrusted tar archive WITHOUT extracting or opening broker sockets.
This first-240 receipt is not proof that the planned 480-cycle trial passed.
"""
import argparse,hashlib,io,json,os,stat,tarfile
from pathlib import Path
from collections import Counter
from datetime import datetime,timezone
PIN={
 "plan":"490375e6cfc4cea69b0aeb8ece2bd836228d1b3d086793555ed35d920bc8f6be",
 "worker":"3b880a8bd59b515b128a5566e713767ac822862d695f1f8c4237bb018fbc0d59",
 "sandbox":"b18abaf07255b4183f96dcdad8ed5059cbbd8e2f82616c81cc5597fef513acaa",
 "head":"a73ee675430a2e78b1c09236d11046d322aec80dd7c5d32e4a1c74ed576a3b00",
 "report":"41dadee62965ca2f648297a9d2e9222eee1c3b40158ac8dc75a2dc42ba9a4978"}
COHORT="heptatrader.fx-original-sustained-source-archive.v1"
RULE=dict(schema="heptatrader.quote-readiness.v1",input_probes=24,spacing_ms=60000,
 horizon_ms=300000,input_grace_ms=60000,endpoint_tolerance_ms=5000,
 path_gap_max_ms=30000,minimum_usable=20,maximum_missing=4,
 source_ttl_ms=5000,statistics_scope="OVERLAPPING_OPERATIONAL_COVERAGE_NOT_PERFORMANCE")
class Fail(ValueError):pass
def need(ok,why):
 if not ok:raise Fail(why)
def sha(b):return hashlib.sha256(b).hexdigest()
def pack(o):return json.dumps(o,sort_keys=True,separators=(",",":"),allow_nan=False).encode()
def parse(b):
 def unique(pairs):
  o={}
  for k,v in pairs:
   need(k not in o,"DUPLICATE_JSON_KEY")
   o[k]=v
  return o
 return json.loads(b,object_pairs_hook=unique,parse_constant=lambda _: (_ for _ in ()).throw(Fail("NONFINITE_JSON")))
def read_private(path,limit=4000000):
 p=Path(path)
 need(p.is_absolute() and p.is_file() and not p.is_symlink()
      and p.stat().st_uid==os.geteuid() and p.stat().st_size<limit
      and not (stat.S_IMODE(p.stat().st_mode)&0o077),"UNSAFE_PRIVATE_FILE")
 return p.read_bytes()
def archive(path):
 expected={"plan.json","worker-start.json","sandbox.json"}|{
  f"cycle-{i:02d}-{suffix}.json" for i in range(240) for suffix in ("receipt","status")}
 b=read_private(path)
 d={}
 with tarfile.open(fileobj=io.BytesIO(b),mode="r:gz") as tf:
  for member in tf:
   need(member.name in expected and member.name not in d and member.isfile()
        and member.linkname=="" and 0<member.size<300000
        and not member.mode&0o077,"ARCHIVE_UNSAFE_OR_DUPLICATE")
   d[member.name]=tf.extractfile(member).read()
   need(len(d[member.name])==member.size,"ARCHIVE_TRUNCATION")
 need(set(d)==expected,"ARCHIVE_MISSING_OR_EXTRA_SOURCE")
 return d,sha(b)
def verify(d,report,pins=PIN):
 need(all(sha(d[name])==pins[key] for key,name in (
      ("plan","plan.json"),("worker","worker-start.json"),
      ("sandbox","sandbox.json"))),
      "PRESEALED_OR_WORKER_BYTES_CHANGED")
 p,w,s=[parse(d[x]) for x in (
  "plan.json","worker-start.json","sandbox.json")]
 need(p.get("schema")==COHORT+".plan.v1" and p.get("full_snapshots_count")==480
      and p.get("original_monitor_rules")==RULE
      and p.get("candidate_assessment_cadence_ms")==15000
      and p.get("maximum_status_age_ms")==30000
      and p.get("no_production_monitor_cutover") is True
      and p.get("no_training_or_trading") is True
      and p.get("trade_authority") is False
      and p.get("broker_requests_or_orders_sent")==0
      and p.get("cycle_schedule_ms")==[p["first_cycle_start_ms"]+i*15000 for i in range(480)],
      "FROZEN_ORIGINAL_HEALTH_SCOPE_CHANGED")
 need(w.get("plan_sha256")==pins["plan"] and w.get("no_restart") is True
      and w.get("production_monitor_not_modified") is True and w.get("broker_orders_sent")==0
      and w.get("start_ms")<p["first_cycle_start_ms"],
      "FROZEN_WORKER_SCOPE_CHANGED")
 need(all(s.get(k) is True for k in (
   "landlock_enforced","seccomp_enforced","socket_creation_denied",
   "bearer_read_denied","only_output_writable","output_write_verified")),
   "SOURCE_SANDBOX_ATTESTATION_CHANGED")
 need(sha(report[0])==pins["report"],"OLD_AUDIT_CONTENT_CHANGED")
 old=report[1]
 need(old.get("contiguous_prefix_cycles_checked")==240 and old.get("future_cycles_NOT_verified")==240,
      "OLD_AUDIT_FALSE_COMPLETION")
 previous="0"*64; rows=[]
 for i in range(240):
  b=d[f"cycle-{i:02d}-receipt.json"];r=parse(b)
  sb=d[f"cycle-{i:02d}-status.json"];q=parse(sb)
  target=p["first_cycle_start_ms"]+15000*i
  need(r.get("schema")==COHORT+".cycle.v1" and r.get("index")==i
       and r.get("frozen_target_ms")==target and r.get("previous_receipt_sha256")==previous
       and r.get("original_period_sha256")==p["period_sha256"] and r.get("orders_sent")==0
       and r.get("trade_authority") is False,"ORIGINAL_RECEIPT_CHAIN_CHANGED")
  previous=sha(b)
  start=r.get("assessment_started_ms");sealed=r.get("record_sealed_at_ms")
  at=q.get("at_ms");published=r.get("published_at_ms")
  need(all(type(v) is int for v in (start,sealed,at,published))
       and target<=start<=target+3000 and start<=at<=start+3000
       and at<=published<=sealed and published-at<=30000,
       "ORIGINAL_ASOF_PUBLICATION_OR_SCHEDULE_FAILED")
  need(r.get("status_sha256")==sha(sb) and r.get("original_asof_ms")==at
       and r.get("source_usable_probes")==q.get("usable")
       and r.get("source_prefix_digest")==q.get("source_prefix_digest"),
       "ORIGINAL_RAW_SOURCE_NOT_BOUND_TO_RECEIPT")
  pr=q.get("probes")
  need(q.get("schema")=="heptatrader.quote-readiness-status.v1"
       and q.get("period_sha256")==p["period_sha256"]
       and q.get("rule")==RULE and type(pr) is list and len(pr)==24,
       "BROKER_PERIOD_OR_PROBE_RULES_CHANGED")
  counts=dict(Counter(v.get("status") for v in pr))
  usable=counts.get("USABLE",0)
  need(counts==q.get("counts") and q.get("usable")==usable
       and q.get("unusable")==24-usable and type(q.get("source_prefixes")) is list
       and sha(pack(q["source_prefixes"]))==q.get("source_prefix_digest"),
       "INDEPENDENT_24_PROBES_OR_SOURCE_DIGEST_CHANGED")
  age=q.get("latest_quote_age_ms")
  need(type(age) is int and type(q.get("latest_source_quote_ms")) is int
       and at-q["latest_source_quote_ms"]==age
       and q.get("automatic_trial_registration") is False
       and q.get("trading_authority") is False and q.get("orders_sent")==0,
       "BROKER_SOURCE_AGE_OR_AUTHORITY_CHANGED")
  ready=q.get("state")=="READY_FOR_REVIEW_NOT_AUTHORIZATION"
  need(ready and q.get("reasons")==[] and 0<=age<=5000 and usable>=20
       and r.get("source_ready") is True and r.get("publisher_eligible") is True
       and r.get("state")=="CAPTURED_ORIGINAL_HEALTH" and r.get("issues")==[],
       "ORIGINAL_HEALTH_READY_CLAIM_FALSE")
  reference=old["per_cycle"][i]
  need(reference["receipt_sha256"]==previous and reference["status_sha256"]==sha(sb)
       and reference["asof_ms"]==at,
       "POCKET4_QIAN_INDEPENDENT_RECOMPUTATION_DISAGREES")
  rows.append({"at_ms":at,"published_ms":published,"usable":usable,"status_sha256":sha(sb)})
 need(previous==pins["head"],"FIRST240_ORIGINAL_CHAIN_HEAD_CHANGED")
 max_age=max(x["published_ms"]-x["at_ms"] for x in rows)
 next_age=max(b["published_ms"]-a["at_ms"] for a,b in zip(rows,rows[1:]))
 need(max_age==old["worst_published_original_source_age_ms"]
      and next_age==old["worst_status_age_at_next_publication_ms"],
      "CROSSHOST_TIMING_RECOMPUTATION_MISMATCH")
 return dict(schema="heptatrader.fx-sustained-physical-crosshost-first240.v1",
   inspected_at_utc=datetime.now(timezone.utc).isoformat(),
   plan_sha256=pins["plan"],original_report_sha256=pins["report"],
   original_first240_chain_head=pins["head"],verified_count=240,
   frozen_target_count=480,unverified_count=240,
   ready_count=240,hold_count=0,max_publish_age_ms=max_age,
   max_age_before_next_publication_ms=next_age,
   physical_host_original_bytes_reparsed=True,
   independently_signed_IB_quote=False,original_source_ib_signed=False,
   quote_source_independent_from_pocket4=False,
   production_approval=False,model_or_strategy_qualified=False,
   model_training_or_promotion_admitted=False,
   final_480_accepted=False,product_witness_enrolled=False,
   broker_orders_sent=0,trade_authority=False)
def main():
 a=argparse.ArgumentParser()
 for k in ("bundle","original-report","output"):a.add_argument("--"+k,required=True,type=Path)
 args=a.parse_args()
 d,bundle_sha=archive(args.bundle)
 raw=read_private(args.original_report)
 r=verify(d,(raw,parse(raw)))
 r["read_only_archive_sha256"]=bundle_sha
 r["standalone_verifier_source_sha256"]=sha(Path(__file__).read_bytes())
 dest=args.output
 need(dest.is_absolute() and not dest.exists() and not dest.is_symlink()
      and dest.parent.is_dir() and stat.S_IMODE(dest.parent.stat().st_mode)==0o700,
      "PRIVATE_NEW_OUTPUT_REQUIRED")
 b=pack(r)+b"\n"
 fd=os.open(dest,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
 with os.fdopen(fd,"wb") as f:f.write(b);f.flush();os.fsync(f.fileno())
 dr=os.open(dest.parent,os.O_RDONLY|os.O_DIRECTORY)
 try:os.fsync(dr)
 finally:os.close(dr)
 print(json.dumps({"status":"PHYSICAL_FIRST240_INDEPENDENTLY_RECOMPUTED_NOT_480",
  "ready_count":240,"max_next_age_ms":r["max_age_before_next_publication_ms"],
  "receipt_sha256":sha(b),"orders":0},sort_keys=True))
if __name__=="__main__":main()
