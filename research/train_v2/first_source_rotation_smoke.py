"""Read-only comparison of preexisting Phase4T signed source and new cohort fence.

Does not upgrade that V1 event to new V2 signed producer or native owner.
No new future window, quote request, model inference or order.
"""
from __future__ import annotations
import argparse,hashlib,json,os
from pathlib import Path
from historical_training.common import read,sha
from .contracts import ContractError
from .first_source_rotation import POLICY,pin_first_segment
from .prewarmed_source_verify import verify as verify_old_signed_v1

def audit(original_registry,source_root):
    oldroot=Path(original_registry)
    original=verify_old_signed_v1(oldroot)
    record=read(oldroot/"original-quote-signed-proof.json",65536)
    sealed=read(oldroot/"sealed-plans"/(record["plan_id"]+".json"),16384)
    plan=sealed["plan"]
    candidate=pin_first_segment(
       source_root,sealed["sealed_at_ms"],
       asof_ms=record["decision_at_ms"],historical_asof=True)
    if candidate["selected_segment_path"]!=record["original_source_path"]:
        raise ContractError("OLD_FIRST_ORIGINAL_SOURCE_COHORT_NOT_CONSISTENT")
    if (original["actual_quote_available_at_ms"]!=record["original_available_at_ms"]
        or original["original_ib_stale_after_ms"]!=record["original_expires_at_ms"]
        or not original["public_mac_ed25519_signature_reverified"]):
        raise ContractError("OLD_VERIFIED_SIGNATURE_OR_TIME_NOT_REPRODUCIBLE")
    code_root=Path(__file__).parent
    recorded_worker_sha=record["source_worker_source_sha256"]
    if sha(code_root/"prewarmed_source_worker.py")!=recorded_worker_sha:
        raise ContractError("PHASE4T_LEGACY_SOURCE_CODE_CHANGED")
    return {
      "schema":"heptatrader.train-v2-first-source-rotation-readonly-v1",
      "old_phase4t_registered_test_source_still_reverifiable":True,
      "old_phase4t_v1_source_event_not_reclassified_as_v2":True,
      "old_phase4t_verified_report_id":original["report_id"],
      "old_phase4t_original_proof_sha256":sha(oldroot/"original-quote-signed-proof.json"),
      "old_phase4t_original_workflow_sha256":recorded_worker_sha,
      "new_first_source_policy":POLICY,
      "new_first_source_cohort_id":candidate["cohort_id"],
      "original_selected_segment_start_ns":candidate["selected_segment_start_ns"],
      "legacy_recovery_lexical_alias_selected":False,
      "first_source_claim_scope":"LOCAL_NUMERIC_WRITER_UNROTATED_FROM_PRESEAL",
      "original_source_original_quote_availability_ms":record["original_available_at_ms"],
      "original_ib_quote_expiry_ms":record["original_expires_at_ms"],
      "predeclared_decision_at_ms":record["decision_at_ms"],
      "original_test_only_ed25519_verified":True,
      "new_source_event_signed":False,
      "native_canonical_writer_executed":False,
      "original_buy_sell_policy_qualification":False,
      "original_prepared_broker_order_proven":False,
      "canonical_owner_enrolled":False,
      "effect_authority":False,
      "broker_requests_sent":0,"broker_orders_sent":0,
      "new_source_code_sha256":{
          x:sha(code_root/x) for x in (
             "first_source_rotation.py",
             "prewarmed_source_worker_v2.py",
             "prewarmed_source_verify_v2.py",
             "prewarmed_source_orchestrate_v2.py")
      },
    }

def main():
  parser=argparse.ArgumentParser()
  for n in ("old-registry","source-root","output"):
      parser.add_argument("--"+n,required=True,type=Path)
  args=parser.parse_args()
  report=audit(args.old_registry,args.source_root)
  dest=args.output
  if dest.is_symlink() or dest.exists():
      raise ContractError("COHORT_IMMUTABLE_NEW_AUDIT_REQUIRED")
  dest.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
  raw=(json.dumps(report,sort_keys=True,indent=2)+"\n").encode()
  fd=os.open(dest,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
  with os.fdopen(fd,"wb") as out:out.write(raw);out.flush();os.fsync(out.fileno())
  fd=os.open(dest.parent,os.O_DIRECTORY|os.O_RDONLY|os.O_NOFOLLOW)
  try:os.fsync(fd)
  finally:os.close(fd)
  print(json.dumps({
      "status":"ORIGINAL_V1_PRESERVED_V2_COHORT_READONLY_SCOPED",
      "cohort_id":report["new_first_source_cohort_id"],
      "original_source_reverified":True,
      "new_signed_event":False,"broker_orders_sent":0,
      "receipt_sha256":hashlib.sha256(raw).hexdigest()},sort_keys=True))

if __name__=="__main__":
  main()
