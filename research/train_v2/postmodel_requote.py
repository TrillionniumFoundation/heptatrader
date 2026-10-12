"""Bounded, read-only feasibility of a NEW two-clock quote-at-decision design.

NOT original opaque LLM policy replay: a post-inference quote is new evidence,
not a historical input to the original model. Never grants trading/alpha.
"""
from __future__ import annotations
from bisect import bisect_right
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
import hashlib
import os
from pathlib import Path
import re
import stat

from hepta_decision_attribution import read_private, strict, digest as original_digest
from .original_decision_population import _scan_original, _graph_decision, _request_id, MAX_MANIFESTS
from .original_policy_audit import verify_release
from .adapters import make_spec
from .causal_ttl import sealed_original_broker_expiry
from .contracts import ContractError

SCHEMA="heptatrader.train-v2-postmodel-requote-feasibility.v2"
# Hypothetical ALL-IN local source sign/witness/risk/PREPARED overhead.
# Not a measured future host SLA or a confidence that an old LLM output trades.
POST_RETURN_BUDGETS_MS=(0,50,100,250,500,1000,2000)
DIR=re.compile(r"^segment-([0-9]{19})-([0-9a-f]{8})$")
MAX_SEGMENTS=320
MAX_SOURCE_BYTES=256*1024*1024
MAX_MODEL_GRAPH=20000

def _private_directory(root):
 root=Path(root)
 if not root.is_absolute() or ".." in root.parts or root.is_symlink():
  raise ContractError("REQUOTING_ORIGINAL_ROOT_UNSAFE")
 st=root.stat()
 if not stat.S_ISDIR(st.st_mode) or st.st_uid!=os.geteuid() or st.st_mode & 0o077:
  raise ContractError("REQUOTING_ORIGINAL_ROOT_NOT_PRIVATE")
 return root

def _price(value):
 if type(value) is bool:
  raise ContractError("REQUOTING_INVALID_PRICE")
 try:p=Decimal(str(value))
 except (InvalidOperation,TypeError,ValueError) as exc:
  raise ContractError("REQUOTING_INVALID_PRICE") from exc
 if not p.is_finite() or not Decimal(0)<p<Decimal(100000000):
  raise ContractError("REQUOTING_INVALID_PRICE")
 return p

def _mid(quote):
 bid,ask=_price(quote["bid"]),_price(quote["ask"])
 if ask<bid:
  raise ContractError("REQUOTING_CROSSED_MARKET")
 return (bid+ask)/2

def load_original_quote_archive(source_root,period_sha):
 """Only sealed original NUMERIC writer segments, never recovery-live alias."""
 root=_private_directory(source_root)
 entries=sorted(root.iterdir(),key=lambda p:p.name)
 numeric=[]
 seen_starts=set()
 for entry in entries:
  name=entry.name
  if not name.startswith("segment-") or name=="segment-recovery-live":
   continue
  match=DIR.fullmatch(name)
  if match is None:
   raise ContractError("REQUOTING_MALFORMED_NUMERIC_SOURCE")
  if entry.is_symlink() or not entry.is_dir():
   raise ContractError("REQUOTING_SYMLINKED_SEGMENT")
  start=int(match.group(1))
  if start in seen_starts:
   raise ContractError("REQUOTING_DUPLICATE_SEGMENT_START")
  seen_starts.add(start)
  file=entry/"quotes.jsonl"
  if file.is_symlink() or not file.is_file():
   raise ContractError("REQUOTING_SEGMENT_WITHOUT_SOURCE")
  numeric.append((start,file))
 if not 1<=len(numeric)<=MAX_SEGMENTS:
  raise ContractError("REQUOTING_NUMERIC_SEGMENT_BUDGET")
 numeric.sort()
 from laya_cell.shadow_source import read_source
 total=0;quotes=[];sealed=0;empty=0;source_proofs=[]
 last_numeric=numeric[-1][0]
 for n,file in numeric:
  total+=file.stat().st_size
  if total>MAX_SOURCE_BYTES:
   raise ContractError("REQUOTING_SOURCE_BYTE_BUDGET")
  header,items,prefix=read_source(file)
  if header.get("period_sha256")!=period_sha or header.get("instrument")!="EUR.USD":
   raise ContractError("REQUOTING_SOURCE_PERIOD_OR_INSTRUMENT")
  if prefix["uncommitted_tail_bytes"]!=0:
   raise ContractError("REQUOTING_UNCOMMITTED_SOURCE_TAIL")
  if prefix["ended"] is not True:
   if n!=last_numeric:
    raise ContractError("REQUOTING_UNSEALED_EARLIER_SEGMENT")
   continue
  spec=make_spec(period_sha,"DIAGNOSTIC_ONLY_NOT_ORIGINAL_POLICY",
                 broker_reported_expiry=True)
  rows,witness=sealed_original_broker_expiry(file,spec)
  if len(rows)!=len(items):
   raise ContractError("REQUOTING_SOURCE_DOUBLE_READ_MISMATCH")
  sealed+=1;empty+=not bool(rows)
  for row in rows:
   quotes.append((row["available_at_ms"],row["event_at_ms"],
                  row["valid_until_ms"],row["source_id"],_mid(row)))
  source_proofs.append({"segment_start_ns":n,
    "original_sha256":prefix["sha256"],"quote_count":len(rows)})
 if not quotes or sealed==0:
  raise ContractError("REQUOTING_NO_SEALED_QUOTE_SOURCE")
 quotes.sort(key=lambda q:(q[0],q[1],q[3]))
 seen_id=set()
 for idx,(available,event,expires,qid,price) in enumerate(quotes):
  if qid in seen_id:
   raise ContractError("REQUOTING_DUPLICATE_CHAIN_QUOTE")
  seen_id.add(qid)
  if not event<=available<=expires<=event+5000:
   raise ContractError("REQUOTING_QUOTE_TIME_INVALID")
 return {
  "rows":quotes,"known_times":[q[0] for q in quotes],
  "sealed_segments":sealed,"empty_segments":empty,
  "unsealed_newest_excluded":sealed<len(numeric),
  "numeric_segments_total":len(numeric),
  "verified_original_bytes":total,
  "source_fingerprint_sha256":hashlib.sha256(
   __import__("json").dumps(source_proofs,sort_keys=True,separators=(",",":")).encode()).hexdigest(),
  "first_available_ms":quotes[0][0],
  "last_available_ms":quotes[-1][0],
 }

def asof_requote(known_times,rows,model_decision_ms):
 """Most recently AVAILABLE quote; future events are never eligible."""
 if type(model_decision_ms) is not int or model_decision_ms<1:
  raise ContractError("REQUOTING_DECISION_TIME_INVALID")
 i=bisect_right(known_times,model_decision_ms)-1
 if i<0:
  return "BEFORE_RECORDED_SOURCE",None
 last=rows[i]
 if model_decision_ms>known_times[-1]:
  return "AFTER_LAST_SEALED_QUOTE",None
 if not last[1]<=last[0]<=model_decision_ms<=last[2]:
  return "NO_FRESH_QUOTE_AT_MODEL_RETURN",None
 return "NEW_QUOTE_AVAILABLE_FOR_HYPOTHETICAL_NEW_POLICY",last

def source_model_returns(release_root, period_sha, *, original_cache=None):
 """Verify original EUR/USD REQUEST RESPONSE DECISION chains (no account IDs output)."""
 root=Path(release_root)
 if root.is_symlink() or not root.is_absolute() or not root.is_dir():
  raise ContractError("REQUOTING_RELEASE_ROOT_UNSAFE")
 manifests=sorted(root.glob("*/*/manifest.json"))
 if not 1<=len(manifests)<=MAX_MANIFESTS:
  raise ContractError("REQUOTING_ORIGINAL_RELEASE_COUNT")
 cached={} if original_cache is None else original_cache
 unique=set();reports=[];observations=[];unknown=Counter()
 for path in manifests:
  manifest=strict(read_private(path,65536))
  if manifest.get("instrument")!="EUR.USD":
   continue
  if manifest.get("period_sha256")!=period_sha:
   raise ContractError("REQUOTING_CROSS_PERIOD_MODEL")
  provenance=verify_release(path)
  full=read_private(path,65536)
  if hashlib.sha256(full).hexdigest()!=provenance["manifest_sha256"]:
   raise ContractError("REQUOTING_RELEASE_MUTATED")
  directory=Path(manifest.get("journal_directory",""))
  if directory not in cached:
   cached[directory]=_scan_original(directory)
  producers=[p for p in cached[directory]["producers"]
     if p["manifest_sha256"]==provenance["manifest_sha256"]]
  if not producers:
   raise ContractError("REQUOTING_RELEASE_WITHOUT_ORIGINAL_PRODUCERS")
  count=0;invalid=Counter()
  for producer in producers:
   grouped=defaultdict(dict)
   for entry in producer["rows"]:
    kind=entry["kind"]
    if kind not in ("REQUEST","RESPONSE","DECISION"):
     continue
    rid=_request_id(entry["data"])
    if kind in grouped[rid]:
     raise ContractError("REQUOTING_DUPLICATE_ORIGINAL_MODEL_EVENT")
    grouped[rid][kind]=(entry["data"],entry["sequence"])
   for rid,stages in grouped.items():
    identity=(manifest["instrument"],manifest["period_sha256"],rid)
    if identity in unique:
     raise ContractError("REQUOTING_CROSS_RELEASE_MODEL_REQUEST_REUSE")
    unique.add(identity)
    if not {"REQUEST","RESPONSE","DECISION"} <= stages.keys():
     unknown["INCOMPLETE_ORIGINAL_MODEL_GRAPH"]+=1
     continue
    req,resp,dec=(stages[name][0] for name in ("REQUEST","RESPONSE","DECISION"))
    try:
     action,_=_graph_decision(req,resp,dec,
        *(stages[name][1] for name in ("REQUEST","RESPONSE","DECISION")),
        "EUR.USD",period_sha)
     start=req["started_at_ms"]; end=dec["available_at_ms"]
     hist=req["history_snapshot"]
     if (type(start) is not int or type(end) is not int
        or not isinstance(hist,list) or not 1<=len(hist)<=24):
      raise ValueError("REQUOTING_ORIGINAL_TIME_OR_HISTORY")
     earlier=hist[-1]
     original_at=earlier["observed_at_ms"]
     if (type(original_at) is not int or not
           0<original_at<=start<=end):
      raise ValueError("REQUOTING_ORIGINAL_SOURCE_TIME")
     initial_mid=_mid(earlier)
     if action not in ("BUY","SELL","HOLD"):
      raise ValueError("REQUOTING_ORIGINAL_ACTION_UNKNOWN")
    except (ValueError,KeyError,IndexError,TypeError,AttributeError,
            InvalidOperation) as exc:
     invalid[type(exc).__name__]+=1
     unknown["UNQUALIFIED_ORIGINAL_MODEL_GRAPH"]+=1
     continue
    observations.append({
      "original_model_decision_at_ms":end,
      "original_input_quote_at_ms":original_at,
      "original_request_at_ms":start,
      "original_action":action,
      "original_input_mid":initial_mid,
      "original_manifest_sha256":provenance["manifest_sha256"],
      # No prompt/rationale/original request or account IDs in output.
    })
    count+=1
    if len(observations)>MAX_MODEL_GRAPH:
     raise ContractError("REQUOTING_ORIGINAL_MODEL_ROW_BUDGET")
  reports.append({
    "original_release_sha256":provenance["manifest_sha256"],
    "source_instrument":"EUR.USD",
    "observed_model_returns":count,
    "incomplete_or_invalid":dict(invalid),
    "only_complete_original_model_graphs":True,
    "policy_opportunity_denominator_complete":False,
  })
 if not observations:
  raise ContractError("REQUOTING_NO_VERIFIED_ORIGINAL_MODEL_GRAPH")
 return observations,reports,unknown

def describe_int(values):
 if not values:return {"count":0,"p50":None,"p90":None,"p99":None}
 values=sorted(values)
 import math
 return {"count":len(values),
   "p50":values[math.ceil(len(values)*0.5)-1],
   "p90":values[math.ceil(len(values)*0.9)-1],
   "p99":values[math.ceil(len(values)*0.99)-1]}

def analyze_join(archive,observations,release_summary,missing):
 """Aggregate only observed original returns; no trade or credit estimation."""
 rows=archive["rows"]; times=archive["known_times"]
 counts=Counter();by_release=defaultdict(Counter)
 drifts=[];orig_ages=[];fresh_ages=[];waits=[];slacks=[]
 by_budget={str(budget):Counter() for budget in POST_RETURN_BUDGETS_MS}
 for item in observations:
  at=item["original_model_decision_at_ms"]
  action=item["original_action"]
  key=item["original_manifest_sha256"]
  counts["verified_original_model_returns"]+=1
  by_release[key]["verified_model_returns"]+=1
  if at < archive["first_available_ms"] or at > archive["last_available_ms"]:
   counts["outside_attested_sealed_source_coverage_unknown"]+=1
   by_release[key]["out_of_coverage_unknown"]+=1
   continue
  counts["in_attested_sealed_source_time_bounds"]+=1
  status,q=asof_requote(times,rows,at)
  if q is None:
   counts["no_original_local_fresh_quote_known_at_model_return"]+=1
   by_release[key]["fresh_quote_not_observed"]+=1
   continue
  if status!="NEW_QUOTE_AVAILABLE_FOR_HYPOTHETICAL_NEW_POLICY":
   raise ContractError("REQUOTING_INTERNAL_SOURCE_STATUS")
  _known,quote_at,expiry,_source_id,mid=q
  if _known>at or expiry<at or quote_at>at:
   raise ContractError("REQUOTING_FUTURE_OR_EXPIRED_SOURCE_BYPASS")
  source_time=item["original_input_quote_at_ms"]
  age=at-source_time
  if age<0:
   raise ContractError("REQUOTING_ORIGINAL_MODEL_SOURCE_CLOCK")
  latest_drift=abs((mid/item["original_input_mid"]-1)*Decimal(10000))
  if not latest_drift.is_finite():
   raise ContractError("REQUOTING_NUMERIC_DRIFT_INVALID")
  counts["fresh_local_quote_asof_model_return_upper_bound"]+=1
  counts["fresh_returned_"+action]+=1
  if quote_at>source_time:
   counts["fresh_local_quote_newer_than_original_model_input"]+=1
  else:
   counts["latest_local_quote_not_newer_than_model_input"]+=1
  by_release[key]["fresh_quote_upper_bound"]+=1
  slack=expiry-at
  if slack<0 or slack>5000:
   raise ContractError("REQUOTING_BROKER_EXPIRY_SLACK_INCONSISTENT")
  slacks.append(slack)
  for hypothetical_budget in POST_RETURN_BUDGETS_MS:
   if slack>=hypothetical_budget:
    by_budget[str(hypothetical_budget)]["all_model_returns"]+=1
    by_budget[str(hypothetical_budget)]["returned_"+action]+=1
  drifts.append(int((latest_drift*Decimal(1000000)).to_integral_value()))
  orig_ages.append(age)
  fresh_ages.append(at-quote_at)
  waits.append(at-_known)
 if counts["verified_original_model_returns"]!=len(observations):
  raise ContractError("REQUOTING_INTERNAL_MODEL_COUNT")
 if (counts["fresh_local_quote_asof_model_return_upper_bound"]
     +counts["no_original_local_fresh_quote_known_at_model_return"]
     +counts["outside_attested_sealed_source_coverage_unknown"]
     !=len(observations)):
  raise ContractError("REQUOTING_DENOMINATOR_INCONSISTENT")
 report={
  "schema":SCHEMA,
  "quote_archive":{
    k:v for k,v in archive.items() if k not in ("rows","known_times")},
  "verified_original_model_release_scopes":release_summary,
  "original_unknown_graphs":dict(missing),
  "observations":dict(sorted(counts.items())),
  "within_model_return_subset_original_input_age_ms":describe_int(orig_ages),
  "within_model_return_subset_new_quote_event_age_ms":describe_int(fresh_ages),
  "within_model_return_subset_new_quote_delivery_age_ms":describe_int(waits),
  "absolute_requote_drift_microbps":describe_int(drifts),
  "remaining_original_broker_quote_ttl_at_model_return_ms":describe_int(slacks),
  "hypothetical_new_policy_after_return_budgets_ms":{
    k:dict(sorted(v.items())) for k,v in sorted(by_budget.items(),key=lambda p:int(p[0]))},
  "budget_accounting_scope":"HYPOTHETICAL_END_TO_END_COSTS_NOT_NATIVE_ACK_OR_RISK_SLA",
  "hypothetical_budgets_never_relabel_original_model_as_new_quote_policy":True,
  "per_release":{k:dict(sorted(v.items())) for k,v in sorted(by_release.items())},
  "scope":"RETROSPECTIVE_READONLY_UPPER_BOUND_NOT_ORIGINAL_LLM_POLICY",
  "claim_of_original_policy_source_rebinding":False,
  "source_attested_as_broker_signed":False,
  "future_quote_data_used_in_any_features":False,
  "original_policy_model_forward_inference_executed":False,
  "model_based_after_cost_performance_attested":False,
  "original_full_action_propensities_known":False,
  "original_prepared_order_identity_attested":False,
  "independent_canonical_owner_enrolled":False,
  "promotion_allowed":False,
  "real_model_inference_performed":False,
  "broker_requests_sent":0,
  "broker_orders_sent":0,
 }
 return report

def main():
 import argparse,json
 from datetime import datetime,timezone
 from historical_training.common import sha
 parser=argparse.ArgumentParser(description=__doc__)
 for name in ("period","source-root","original-release-root","output"):
  parser.add_argument("--"+name,type=Path,required=True)
 args=parser.parse_args();os.umask(0o077)
 period_sha=sha(args.period)
 archive=load_original_quote_archive(args.source_root,period_sha)
 observations,release_summary,missing=source_model_returns(
     args.original_release_root,period_sha)
 result=analyze_join(archive,observations,release_summary,missing)
 result["frozen_original_period_sha256"]=period_sha
 result["audited_at_utc"]=datetime.now(timezone.utc).isoformat()
 result["source_code_sha256"]=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
 test_source=Path(__file__).resolve().parents[2]/"tests/python/test_train_v2_postmodel_requote.py"
 if not test_source.is_file() or test_source.is_symlink():
  raise ContractError("REQUOTING_ADVERSARIAL_TEST_SOURCE_MISSING")
 result["adversarial_test_source_sha256"]=hashlib.sha256(test_source.read_bytes()).hexdigest()
 dest=args.output
 if dest.exists() or dest.is_symlink():
  raise ContractError("REQUOTING_IMMUTABLE_OUTPUT_EXISTS")
 dest.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
 import json
 raw=(json.dumps(result,sort_keys=True,indent=2)+"\n").encode()
 fd=os.open(dest,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
 with os.fdopen(fd,"wb") as f:
  f.write(raw);f.flush();os.fsync(f.fileno())
 directory_fd=os.open(dest.parent,os.O_DIRECTORY|os.O_RDONLY|os.O_NOFOLLOW)
 try:os.fsync(directory_fd)
 finally:os.close(directory_fd)
 print(json.dumps({
  "status":result["scope"],
  "observed_model_returns":result["observations"]["verified_original_model_returns"],
  "source_window_covered":result["observations"].get(
        "in_attested_sealed_source_time_bounds",0),
  "has_fresh_local_quote_after_model":result["observations"].get(
        "fresh_local_quote_asof_model_return_upper_bound",0),
  "source_coverage_unknown":result["observations"].get(
        "outside_attested_sealed_source_coverage_unknown",0),
  "economic_alpha_proven":False,
  "trade_authority":False,
  "broker_orders_sent":0,
  "receipt_sha256":hashlib.sha256(raw).hexdigest(),
 },sort_keys=True))

if __name__=="__main__":
 main()
