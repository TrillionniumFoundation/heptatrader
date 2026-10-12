"""Qian-qi transport: one new real-source research event, Mac RAM-only test signer.

Signer and original Pocket4 source watcher sessions are pre-opened. Neither
test domain, account action, model prediction nor product trust is upgraded.
"""
from __future__ import annotations
import argparse,hashlib,json,os,select,shlex,subprocess,time
from pathlib import Path
from datetime import datetime,timezone
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from .prewarmed_signer_latency import DOMAIN,SIGNER,session,exchange,receive,close

SCHEMA="heptatrader.train-v2-real-source-mac-testonly-prewarm.v1"

def run(*,mac,source_host,repo,source_root,registry_root,period):
 mac_proc=source_proc=None
 source_event=None
 signer_event=None
 root_ready=None
 last_result=None
 try:
  mac_proc,mac_ready,mac_startup=session(mac,SIGNER)
  pub=mac_ready["pub"]
  if (mac_ready.get("host")!="MacBook-Pro.local"
      or mac_ready.get("memory_only") is not True):
   raise RuntimeError("EXTERNAL_MAC_TEST_ONLY_SIGNER_NOT_READY")
  signer=Ed25519PublicKey.from_public_bytes(bytes.fromhex(pub))
  cmd=("cd "+shlex.quote(str(repo))+" && env "
       "PYTHONPATH=research:research/neuron_learning:scripts "
       "python3 -B -u -m train_v2.prewarmed_source_worker"
       " --registry-root "+shlex.quote(str(registry_root))
       +" --original-source-root "+shlex.quote(str(source_root))
       +" --period "+shlex.quote(str(period))
       +" --mac-public-hex "+shlex.quote(pub))
  started=time.perf_counter_ns()
  source_proc=subprocess.Popen(
      ["ssh","-o","BatchMode=yes","-o","ConnectTimeout=5",
       "-o","StrictHostKeyChecking=yes",source_host,cmd],
       stdin=subprocess.PIPE,stdout=subprocess.PIPE,
      stderr=subprocess.PIPE,text=True,bufsize=1)
  root_ready=receive(source_proc.stdout,12)
  if (root_ready.get("kind")!="READY" or root_ready.get("test_only") is not True
      or root_ready.get("trade_authority") is not False):
   raise RuntimeError("POCKET4_ORIGINAL_SOURCE_PLAN_NOT_PRESEALED")
  source_startup=(time.perf_counter_ns()-started)/1e6
  event=receive(source_proc.stdout,185)
  if event.get("kind")=="SOURCE":
   source_event=event
   msg=bytes.fromhex(event["message_hex"])
   if not msg.startswith(DOMAIN) or len(msg)!=len(DOMAIN)+32:
    raise RuntimeError("SOURCE_NOT_TEST_DOMAIN_MESSAGE")
   t=time.perf_counter_ns()
   signed=exchange(mac_proc,{"kind":"SIGN","seq":0,"body":msg.hex()})
   signer_event=signed
   if (signed.get("kind")!="SIGNED"
       or signed["body_sha"]!=hashlib.sha256(msg).hexdigest()
       or signed["seq"]!=0):
    raise RuntimeError("MAC_SOURCE_SIGNATURE_INCORRECT")
   signer.verify(bytes.fromhex(signed["sig"]),msg)
   mac_transport_ms=(time.perf_counter_ns()-t)/1e6
   source_proc.stdin.write(json.dumps({
       "kind":"SIGNATURE","message_hex":msg.hex(),
       "signature_hex":signed["sig"],"pub":pub},sort_keys=True)+"\n")
   source_proc.stdin.flush()
   last=receive(source_proc.stdout,12)
   complete_ms=(time.perf_counter_ns()-t)/1e6
   if last.get("kind")!="RESULT":
    raise RuntimeError("POCKET4_DID_NOT_RETURN_RESEARCH_RESULT")
   last_result=last["result"]
  elif event.get("kind")=="RESULT":
   last_result=event["result"]
   mac_transport_ms=None
   complete_ms=None
  else:
   raise RuntimeError("UNKNOWN_SOURCE_PROTOCOL_EVENT")
  try:
   source_proc.stdin.close()
   code=source_proc.wait(timeout=6)
  except subprocess.TimeoutExpired:
   source_proc.kill();code=source_proc.wait(timeout=3)
  if code not in (0,2):raise RuntimeError("SOURCE_PROCESS_UNEXPECTED_ERROR")
  status=last_result["status"]
  if status.startswith("TEST_ONLY_") and code!=0:
   raise RuntimeError("RESEARCH_SUCCESS_WITH_FAILED_WORKER")
  evidence={
   "schema":SCHEMA,"observed_at_utc":datetime.now(timezone.utc).isoformat(),
   "mac_signer_host":mac_ready["host"],
   "mac_test_public_key_sha256":hashlib.sha256(bytes.fromhex(pub)).hexdigest(),
   "mac_test_private_key_only_in_process_memory":True,
   "source_host":"alex-G1628-04",
   "mac_ssh_session_created_before_new_source":True,
   "pocket4_source_watcher_created_before_new_source":True,
   "mac_ssh_startup_ms":round(mac_startup,3),
   "pocket4_source_session_startup_ms":round(source_startup,3),
   "predeclared_plan":root_ready,
   "observed_source_event":source_event,
   "mac_signer_workflow_result":({
       "message_sha256":signer_event["body_sha"],
       "crypto_us":signer_event["crypto_us"],
       "public_signing_time_ms_not_a_hardware_attestation":signer_event["signed_at"],
       "verified_test_ed25519_signature":True,
    } if signer_event else None),
   "mac_signature_roundtrip_ms":round(mac_transport_ms,3) if mac_transport_ms is not None else None,
   "mac_to_pocket4_result_roundtrip_ms":round(complete_ms,3) if complete_ms is not None else None,
   "source_outcome":last_result,
   "source_worker_exit_code":code,
   "research_source_success":status=="TEST_ONLY_ACTUAL_SOURCE_SIGNED_AND_LOCALLY_INDEXED",
   "no_resampling_or_backdating":True,
   "actual_product_root_or_owner_enrolled":False,
   "native_canonical_ledger_writer_executed":False,
   "original_opaque_llm_policy":False,
   "original_broker_order_prepared":False,
   "model_prediction_emitted":False,"economic_reward_observed":False,
   "automatic_training":False,"broker_queries_sent":0,"broker_orders_sent":0,
   "test_only_not_product_authorized":True,
  }
  return evidence
 finally:
  try:
   if source_proc:
    if source_proc.poll() is None:
     source_proc.kill()
     source_proc.wait(timeout=5)
  finally:
   if mac_proc:close(mac_proc)

def main():
 p=argparse.ArgumentParser()
 p.add_argument("--mac",default="mac-ts")
 p.add_argument("--source-host",default="pocket4")
 for name in ("repo","source-root","registry-root","period","output"):
  p.add_argument("--"+name,type=Path,required=True)
 args=p.parse_args();os.umask(0o077)
 try:
  report=run(mac=args.mac,source_host=args.source_host,
             repo=args.repo,source_root=args.source_root,
             registry_root=args.registry_root,period=args.period)
 except BaseException as exc:
  report={"schema":SCHEMA,"research_source_success":False,
          "status":"HOLD_EXTERNAL_SOURCE_SIGNER_ERROR",
          "error_type":type(exc).__name__,"error":str(exc)[:200],
          "actual_product_root_or_owner_enrolled":False,
          "native_canonical_ledger_writer_executed":False,
          "automatic_training":False,"broker_orders_sent":0}
 if args.output.is_symlink() or args.output.exists():
  raise ValueError("NO_OVERWRITE_ORIGINAL_RECEIPT")
 args.output.parent.mkdir(parents=True,mode=0o700,exist_ok=True)
 raw=(json.dumps(report,sort_keys=True,indent=2)+"\n").encode()
 fd=os.open(args.output,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
 with os.fdopen(fd,"wb") as f:
  f.write(raw);f.flush();os.fsync(f.fileno())
 print(json.dumps({"result":report.get("source_outcome",{}).get(
                    "status",report.get("status","UNKNOWN")),
  "research_source_success":report["research_source_success"],
  "original_broker_orders":0,"native_product_ledger_acked":False,
  "receipt_sha256":hashlib.sha256(raw).hexdigest()},sort_keys=True))
 if not report["research_source_success"]:raise SystemExit(2)

if __name__=="__main__":main()
