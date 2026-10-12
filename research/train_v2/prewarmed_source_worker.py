"""Phase 4T: one REAL fresh PAPER quote -> TEST-only signed source receipt.

Research-only *source* experiment. Never infer a native model prediction,
actual broker order, product root or economic reward. The selection is fixed
before the first future input, and a missed deadline stays FAILED.
"""
from __future__ import annotations
import argparse,hashlib,json,os,re,stat,sys,time
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from historical_training.common import canonical,sha
from .adapters import make_spec
from .causal_ttl import sealed_original_broker_expiry
from .contracts import ContractError,compile_dataset,digest
from .orchestrator import register_plan,evidence_event,SOURCE_EVENT
from .registry import register_before_window,ingest_registered_original_event

SCHEMA="heptatrader.train-v2-prewarmed-real-source-testonly.v1"
DOMAIN=b"HEPTA.TRAIN_V2.TEST_ONLY.PREWARM.V1\x00"
POLICY="RESEARCH_FRESH_SOURCE_PREWARM_SIGNATURE_NO_ACTION_V1"
WINDOW_LEAD_MS=45000
WINDOW_DURATION_MS=120000
DECISION_DELAY_MS=550
ENDPOINT_HORIZON_MS=300000

def ms():return time.time_ns()//1000000

def private_write(path,value):
 path=Path(path)
 if path.is_symlink() or path.exists():raise ContractError("NO_REWRITING_EXPERIMENT_EVIDENCE")
 raw=canonical(value)+b"\n"
 fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
 with os.fdopen(fd,"wb") as f:f.write(raw);f.flush();os.fsync(f.fileno())
 dd=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
 try:os.fsync(dd)
 finally:os.close(dd)
 return hashlib.sha256(raw).hexdigest()

ORIGINAL_SEGMENT_NAME=re.compile(r"segment-([0-9]{19})-([0-9a-f]{8,64})")

def latest_original_segment(source_root, *, now_ns=None):
 """Use writer's numerical epoch-NANOSECOND segment, not path lexical order.

 The legacy recovery-live segment has a later lexical name but contains
 historical observations. Do NOT accidentally select that stale replay.
 """
 now=time.time_ns() if now_ns is None else now_ns
 paths=[]
 for file in source_root.glob("segment-*/quotes.jsonl"):
  match=ORIGINAL_SEGMENT_NAME.fullmatch(file.parent.name)
  if match is None:continue
  if file.is_symlink() or file.parent.is_symlink():
   raise ContractError("ORIGINAL_LIVE_SEGMENT_SYMLINK")
  if int(match.group(1))>now+3_000_000_000:
   raise ContractError("ORIGINAL_SEGMENT_START_IN_FUTURE")
  paths.append((int(match.group(1)),file))
 if not paths:return None
 paths.sort(key=lambda pair:pair[0])
 if len(paths)>1 and paths[-1][0]==paths[-2][0]:
  raise ContractError("DUPLICATE_ORIGINAL_SEGMENT_START")
 return paths[-1][1]

def candidate(spec,source_root,start_ms):
 original=latest_original_segment(source_root)
 if original is None:return None
 # Only current original pipeline bytes, never a historical/revised bar.
 try: observations,proof=sealed_original_broker_expiry(original,spec)
 except (ValueError,ContractError,OSError,KeyError):return None
 if not observations or ms()-observations[-1]["available_at_ms"]>10000:
  return None
 fresh=[q for q in observations if q["available_at_ms"]>=start_ms]
 if not fresh:return None
 row=min(fresh,key=lambda q:(q["available_at_ms"],q["event_at_ms"]))
 decision=row["available_at_ms"]+DECISION_DELAY_MS
 if (decision>row["valid_until_ms"] or ms()>=decision
      or decision<start_ms or decision>start_ms+WINDOW_DURATION_MS):
  raise ContractError("FIRST_ORIGINAL_QUOTE_TOO_LATE_NO_RESAMPLE")
 example=compile_dataset(spec,observations,[decision],decision)
 return original,proof,row,decision,example

def run(registry_root,source_root,period_file,pubkey_hex):
 reg=Path(registry_root);root=Path(source_root)
 if reg.exists() or reg.is_symlink():raise ContractError("FRESH_TEST_REGISTRY_REQUIRED")
 if root.is_symlink() or not root.is_dir():raise ContractError("ORIGINAL_SOURCE_ROOT_INVALID")
 pk=bytes.fromhex(pubkey_hex)
 if len(pk)!=32:raise ContractError("EXPECTED_MAC_TEST_PUBLIC_KEY")
 verifier=Ed25519PublicKey.from_public_bytes(pk)
 spec=make_spec(sha(period_file),POLICY,broker_reported_expiry=True)
 now=ms()
 start=now+WINDOW_LEAD_MS
 end=start+WINDOW_DURATION_MS
 plan=register_plan(spec=spec,registered_at_ms=now,
  first_decision_ms=start,last_decision_ms=end,minimum_mature_rows=16)
 seal=register_before_window(reg,plan,spec,root)
 print(json.dumps({"kind":"READY","plan_id":plan["plan_id"],
  "first_window_ms":start,"last_window_ms":end,"sealed_at_ms":seal["sealed_at_ms"],
  "trade_authority":False,"test_only":True}),flush=True)
 attempted=False
 while ms()<end:
  if ms()<start-100:time.sleep(.08);continue
  if ms()<start:time.sleep(.02);continue
  if attempted:break
  try:c=candidate(spec,root,start)
  except ContractError as e:
   return {"status":"HOLD_ORIGINAL_SOURCE_EXPIRED_NO_RETRY",
           "reason":str(e),"attempts":1,"broker_orders_sent":0}
  if c is None:
   time.sleep(.035)
   continue
  attempted=True
  original,proof,row,decision,dataset=c
  item=dataset["examples"][0]
  if (item["label_status"]!="PENDING_OUTCOME"
      or item["candidate_training_eligible"] is not False):
   raise ContractError("SOURCE_FEATURE_CAUSALITY_FAILED")
  event=evidence_event(plan,item,kind=SOURCE_EVENT,at_ms=row["available_at_ms"],
                       evidence_sha256=row["source_sha256"])
  message_body={
   "schema":SCHEMA,
   "plan_id":plan["plan_id"],"event_id":event["event_id"],
   "feature_id":item["feature_id"],"dataset_id":dataset["dataset_id"],
   "original_source_path":str(original),
   "original_source_prefix_sha256":proof["verified_prefix_sha256"],
   "original_quote_receipt":row["source_id"],
   "original_event_at_ms":row["event_at_ms"],
   "original_available_at_ms":row["available_at_ms"],
   "original_expires_at_ms":row["valid_until_ms"],
   "decision_at_ms":decision,
   "source_event_at_ms":event["at_ms"],
   "source_worker_source_sha256":hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
   "no_action_or_model_inference":True,
   "not_original_llm_policy":True,
   "test_signature_only":True,"broker_orders_sent":0,
  }
  message=DOMAIN+hashlib.sha256(canonical(message_body)).digest()
  print(json.dumps({"kind":"SOURCE","message_hex":message.hex(),
    "packet_digest":digest(message_body),"decision_at_ms":decision,
    "expires_at_ms":row["valid_until_ms"],"source_available_at_ms":row["available_at_ms"],
    "original_quote_receipt":row["source_id"],"test_only":True}),flush=True)
  if ms()>decision:
   return {"status":"HOLD_SOURCE_BECAME_LATE_BEFORE_SIGNATURE",
    "attempts":1,"broker_orders_sent":0}
  raw=sys.stdin.readline(16000)
  if not raw:raise ContractError("SOURCE_SIGNATURE_NOT_DELIVERED")
  response=json.loads(raw)
  if response.get("kind")!="SIGNATURE" or response.get("message_hex")!=message.hex():
   raise ContractError("SOURCE_SIGNED_MESSAGE_SUBSTITUTED")
  signature=bytes.fromhex(response["signature_hex"])
  verifier.verify(signature,message)
  verified_at_ms=ms()
  if verified_at_ms>decision or verified_at_ms>row["valid_until_ms"]:
   return {"status":"HOLD_SIGNED_SOURCE_AFTER_ORIGINAL_DEADLINE",
    "verified_at_ms":verified_at_ms,"decision_at_ms":decision,
    "original_quote_expires_at_ms":row["valid_until_ms"],
    "broker_orders_sent":0}
  signed_proof={
   **message_body,
   "signed_test_message_sha256":hashlib.sha256(message).hexdigest(),
   "signed_message_hex":message.hex(),
   "test_only_signature_hex":signature.hex(),
   "test_only_mac_public_key_hex":pk.hex(),
   "signature_sha256":hashlib.sha256(signature).hexdigest(),
   "public_key_sha256":hashlib.sha256(pk).hexdigest(),
   "test_signature_independently_replayable_without_private_key":True,
   "verified_at_ms":verified_at_ms,
   "signed_source_receipt_fsynced_before_decision":True,
   "native_ledger_writer_ack":False,"original_broker_prepared":False,
   "canonical_product_root_enrolled":False,
   "behavior_action_propensity_known":False,
   "model_inference_completed":False,
   "trade_authority":False
  }
  proof_sha=private_write(reg/"original-quote-signed-proof.json",signed_proof)
  after_seal_ms=ms()
  if after_seal_ms>decision or after_seal_ms>row["valid_until_ms"]:
   return {"status":"HOLD_FSYNC_AFTER_SOURCE_DEADLINE",
    "attempts":1,"signed_proof_sha256":proof_sha,"broker_orders_sent":0}
  try:
   indexed=ingest_registered_original_event(
    reg,reg/"registered-events.sqlite",plan,dataset,event,original)
  except (ValueError,ContractError,OSError) as e:
   return {"status":"HOLD_RESEARCH_LEDGER_NOT_ADMITTED",
    "attempts":1,"reason":str(e),"signed_proof_sha256":proof_sha,
    "broker_orders_sent":0}
  if indexed["status"]!="INPUT_RECORDED_SHADOW_ONLY":
   raise ContractError("NOT_ONE_NEW_SOURCE_EVENT")
  result={
   "status":"TEST_ONLY_ACTUAL_SOURCE_SIGNED_AND_LOCALLY_INDEXED",
   "real_original_quote_at_ms":row["event_at_ms"],
   "real_original_quote_available_ms":row["available_at_ms"],
   "real_original_quote_expiry_ms":row["valid_until_ms"],
   "true_new_source_decision_cutoff_ms":decision,
   "mac_test_signature_verified_at_ms":verified_at_ms,
   "signed_proof_durable_at_ms":after_seal_ms,
   "signed_proof_sha256":proof_sha,
   "source_event_id":event["event_id"],
   "first_input_after_predeclared_future_start":True,
   "original_source_prefix_sha256":proof["verified_prefix_sha256"],
   "actual_model_prediction_emitted":False,
   "native_product_ledger_ack":False,"native_root_enrolled":False,
   "original_llm_behavior_propensity_known":False,
   "original_broker_order_prepared":False,
   "automatic_training":False,"trade_authority":False,
   "broker_queries_sent":0,"broker_orders_sent":0
  }
  private_write(reg/"source-readiness-outcome.json",result)
  return result
 return {"status":"HOLD_NEW_SOURCE_NOT_OBSERVED_WITHIN_FIXED_WINDOW",
         "attempts":0,"broker_orders_sent":0}

def main():
 p=argparse.ArgumentParser()
 for name in ("registry-root","original-source-root","period"):
  p.add_argument("--"+name,type=Path,required=True)
 p.add_argument("--mac-public-hex",required=True)
 args=p.parse_args();os.umask(0o077)
 try:
  result=run(args.registry_root,args.original_source_root,args.period,
             args.mac_public_hex)
 except BaseException as exc:
  result={"status":"HOLD_ISOLATED_SOURCE_WORKER_ERROR",
          "error_type":type(exc).__name__,"reason":str(exc)[:160],
          "trade_authority":False,"broker_orders_sent":0}
 print(json.dumps({"kind":"RESULT","result":result}),flush=True)
 if result["status"]!="TEST_ONLY_ACTUAL_SOURCE_SIGNED_AND_LOCALLY_INDEXED":
  raise SystemExit(2)

if __name__=="__main__":main()
