"""Read-only canonical V2 readiness probe; never sends a broker operation."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
from historical_training.common import sha
from .adapters import make_spec
from .causal_ttl import sealed_original_broker_expiry
from .canonical_native import verify_native_layout
from .canonical_admission import inspect_readiness
from .contracts import ContractError

def probe(native_repo, period_file, original_segment):
 layout=verify_native_layout(native_repo)
 period=sha(period_file)
 spec=make_spec(period,
   "REAL_PAPER_SOURCE_ONLY_NO_CANONICAL_POLICY_SELECTED",
   broker_reported_expiry=True)
 rows,source=sealed_original_broker_expiry(original_segment,spec)
 accepted=None
 for index in range(5,min(len(rows)-1,128)):
  row=rows[index]
  cutoff=row["available_at_ms"]+200
  if cutoff>row["valid_until_ms"] or cutoff>=rows[index+1]["available_at_ms"]:
   continue
  try:
   outcome=inspect_readiness(layout,spec,row,original_segment,cutoff)
  except ContractError:
   continue
  if outcome["assessment"]!="HOLD_MISSING_NATIVE_LEDGER_WRITER_AND_OWNER_FACTS":
   raise ContractError("CANONICAL_PRECHECK_UNEXPECTED_NATIVE_GRANT")
  accepted=outcome
  break
 if accepted is None:
  raise ContractError("CANONICAL_NO_ORIGINAL_PAPER_PIT_EXAMPLE")
 return {
  "schema":"heptatrader.train-v2-canonical-readiness-smoke.v1",
  "canonical_contract":layout,
  "read_only_auditor_sha256":{
   name:sha(Path(__file__).with_name(name)) for name in (
    "canonical_native.py","canonical_admission.py","canonical_smoke.py")},
  "original_price_source":{
   "quote_count":len(rows),
   "local_source_chain_sha256":source["verified_prefix_sha256"],
   "source_chain_head":source["original_chain_head"],
   "historical_original_source_only":True,
  },
  "audit":accepted,
  "fully_native_compiled":False,
  "canonical_host_enrolled":False,
  "new_forward_trial_registered":False,
  "new_broker_query":False,
  "model_inference_performed":False,
  "model_trained":False,
  "order_authority":False,
  "broker_orders_sent":0,
 }

def main():
 ap=argparse.ArgumentParser()
 for key in ("native-repo","period","original-segment","output"):
  ap.add_argument("--"+key,required=True,type=Path)
 a=ap.parse_args()
 os.umask(0o077)
 receipt=probe(a.native_repo,a.period,a.original_segment)
 path=a.output
 if path.exists() or path.is_symlink():
  raise ContractError("CANONICAL_IMMUTABLE_NEW_OUTPUT_REQUIRED")
 path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
 raw=(json.dumps(receipt,indent=2,sort_keys=True)+"\n").encode()
 fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
 with os.fdopen(fd,"wb") as f:
  f.write(raw);f.flush();os.fsync(f.fileno())
 directory_fd=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
 try:os.fsync(directory_fd)
 finally:os.close(directory_fd)
 print(json.dumps({
  "status":receipt["audit"]["assessment"],
  "native_source_commit":receipt["canonical_contract"]["exact_native_commit"],
  "original_quotes":receipt["original_price_source"]["quote_count"],
  "missing_requirements":len(receipt["audit"]["missing_native_requirements"]),
  "native_writer_executed":False,
  "broker_orders_sent":0,
  "receipt_sha256":hashlib.sha256(raw).hexdigest()},sort_keys=True))

if __name__=="__main__":
 main()
