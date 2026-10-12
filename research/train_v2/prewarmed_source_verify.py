"""Independent READ-ONLY verifier of one pre-registered, fresh PAPER test source.

Verifies public Mac Ed25519 signature, exact committed source prefix, source
hash chain, before-cutoff proof, and one local research SQLite event. This
does NOT verify native LedgerWriter, real action, product principal or PnL.
"""
from __future__ import annotations
import argparse,hashlib,json,os,sqlite3,stat,tempfile
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.exceptions import InvalidSignature
from historical_training.common import canonical,read,sha
from .contracts import ContractError,compile_dataset,digest,feature_spec_id
from .causal_ttl import sealed_original_broker_expiry
from .orchestrator import SOURCE_EVENT,evidence_event,verify_plan
from .registry import verify_registration
from .prewarmed_source_worker import DOMAIN,DECISION_DELAY_MS,SCHEMA

SIGNED_FIELDS=(
 "schema","plan_id","event_id","feature_id","dataset_id",
 "original_source_path","original_source_prefix_sha256",
 "original_quote_receipt","original_event_at_ms","original_available_at_ms",
 "original_expires_at_ms","decision_at_ms","source_event_at_ms",
 "source_worker_source_sha256","no_action_or_model_inference",
 "not_original_llm_policy","test_signature_only","broker_orders_sent",
)

def source_prefix(data,sha_wanted):
 if not isinstance(data,bytes) or len(data)>64*1024*1024:
  raise ContractError("PROVENANCE_FILE_TOO_LARGE")
 h=hashlib.sha256()
 end=0
 for line in data.splitlines(keepends=True):
  if not line.endswith(b"\n"):break
  h.update(line);end+=len(line)
  if h.hexdigest()==sha_wanted:
   return data[:end]
 raise ContractError("SIGNED_SOURCE_PREFIX_NOT_IN_ORIGINAL_FILE")

def verify_signed_packet(proof):
 if proof.get("schema")!=SCHEMA or any(k not in proof for k in SIGNED_FIELDS):
  raise ContractError("SIGNED_SOURCE_PROOF_SCHEMA")
 if (proof.get("trade_authority") is not False
     or proof.get("native_ledger_writer_ack") is not False
     or proof.get("test_signature_independently_replayable_without_private_key") is not True):
  raise ContractError("SIGNED_SOURCE_FALSE_PRODUCT_AUTHORITY")
 packet={k:proof[k] for k in SIGNED_FIELDS}
 msg=DOMAIN+hashlib.sha256(canonical(packet)).digest()
 try:
  raw=bytes.fromhex(proof["signed_message_hex"])
  key=bytes.fromhex(proof["test_only_mac_public_key_hex"])
  signature=bytes.fromhex(proof["test_only_signature_hex"])
  if (raw!=msg or len(key)!=32 or len(signature)!=64
      or hashlib.sha256(signature).hexdigest()!=proof["signature_sha256"]
      or hashlib.sha256(key).hexdigest()!=proof["public_key_sha256"]):
   raise ContractError("SIGNED_SOURCE_MESSAGE_DIGEST_CHANGED")
  Ed25519PublicKey.from_public_bytes(key).verify(signature,msg)
 except ContractError:
  raise  # Keep scope/message identity errors distinct from crypto errors.
 except (ValueError,TypeError,InvalidSignature) as exc:
  raise ContractError("SIGNED_SOURCE_PUBLIC_SIGNATURE_INVALID") from exc
 return packet

def read_original_prefix(path,expected_sha,private_parent):
 path=Path(path)
 if not path.is_absolute() or path.is_symlink() or not path.is_file():
  raise ContractError("ORIGINAL_SOURCE_PATH_NOT_PRIVATE")
 fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
 try:
  st=os.fstat(fd)
  if (not stat.S_ISREG(st.st_mode) or st.st_uid!=os.geteuid()
      or st.st_mode & 0o077 or st.st_size>64*1024*1024):
   raise ContractError("ORIGINAL_SOURCE_FILE_UNSAFE")
  chunks=[];remaining=st.st_size
  while remaining:
   data=os.read(fd,min(1024*1024,remaining))
   if not data:raise ContractError("ORIGINAL_SOURCE_SHORT_READ")
   chunks.append(data);remaining-=len(data)
 finally:os.close(fd)
 return source_prefix(b"".join(chunks),expected_sha)

def frozen_ledger_rows(dbfile):
 """Read a SEALED SQLite ledger without modifying WAL or shared-memory files.

 SQLite mode=ro by itself can acquire locks/create or alter the -shm sidecar.
 A completed original event is allowed here only after its WAL has been
 checkpointed, then immutable=1 prevents creating filesystem sidecars.
 This never grants an authoritative native LedgerWriter acknowledgement.
 """
 dbfile=Path(dbfile)
 wal=Path(str(dbfile)+"-wal")
 if dbfile.is_symlink() or not dbfile.is_file() or wal.is_symlink():
  raise ContractError("FROZEN_RESEARCH_DB_UNSAFE")
 if wal.exists() and wal.stat().st_size:
  raise ContractError("RESEARCH_DB_WAL_NOT_CHECKPOINTED")
 if dbfile.stat().st_uid!=os.getuid() or dbfile.stat().st_mode & 0o077:
  raise ContractError("FROZEN_RESEARCH_DB_NOT_PRIVATE")
 from urllib.parse import quote
 uri="file:"+quote(str(dbfile))+"?mode=ro&immutable=1"
 db=sqlite3.connect(uri,uri=True)
 try:
  events=db.execute("SELECT event_id,body FROM events").fetchall()
  samples=db.execute("SELECT feature_id,state,source_event_id FROM samples").fetchall()
  batches=db.execute("SELECT count(*) FROM review_batches").fetchone()[0]
  return events,samples,batches
 finally:
  db.close()


def verify(root):
 root=Path(root)
 if (root.is_symlink() or not root.is_dir() or root.stat().st_uid!=os.getuid()
     or root.stat().st_mode & 0o077):
  raise ContractError("REGISTERED_RESEARCH_DIRECTORY_NOT_PRIVATE")
 proof=read(root/"original-quote-signed-proof.json",65536)
 packet=verify_signed_packet(proof)
 plan_id=packet["plan_id"]
 sealed=read(root/"sealed-plans"/(plan_id+".json"),16384)
 plan=sealed["plan"];spec=sealed["feature_spec"]
 verify_plan(plan)
 if plan["plan_id"]!=plan_id or feature_spec_id(spec)!=plan["feature_spec_id"]:
  raise ContractError("REGISTERED_PLAN_IDENTITY_CHANGED")
 source=Path(packet["original_source_path"])
 receipt=verify_registration(root,plan,spec,source)
 if (receipt!=sealed or receipt["sealed_at_ms"]>=plan["first_decision_ms"]
     or packet["source_worker_source_sha256"]!=sha(
         Path(__file__).with_name("prewarmed_source_worker.py"))):
  raise ContractError("REGISTERED_SOURCE_OR_CODE_CHANGED")
 raw=read_original_prefix(source,packet["original_source_prefix_sha256"],root)
 with tempfile.TemporaryDirectory(prefix=".read-only-verify-",dir=root) as temp:
  private=Path(temp)/"quotes.jsonl"
  fd=os.open(private,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
  with os.fdopen(fd,"wb") as f:f.write(raw);f.flush();os.fsync(f.fileno())
  original,prefix_proof=sealed_original_broker_expiry(private,spec)
 if prefix_proof["verified_prefix_sha256"]!=packet["original_source_prefix_sha256"]:
  raise ContractError("ORIGINAL_SHA_CHAIN_PREFIX_NOT_MATCHED")
 potential=[q for q in original if q["available_at_ms"]>=plan["first_decision_ms"]]
 if not potential:
  raise ContractError("NO_FIRST_PROSPECTIVE_QUOTE_IN_REGISTERED_SOURCE")
 first=min(potential,key=lambda q:(q["available_at_ms"],q["event_at_ms"]))
 if (first["source_id"]!=packet["original_quote_receipt"]
     or packet["decision_at_ms"]!=first["available_at_ms"]+DECISION_DELAY_MS
     or packet["original_event_at_ms"]!=first["event_at_ms"]
     or packet["original_available_at_ms"]!=first["available_at_ms"]
     or packet["original_expires_at_ms"]!=first["valid_until_ms"]):
  raise ContractError("FIRST_NEW_SOURCE_OR_DEADLINE_CHANGED")
 cutoff=packet["decision_at_ms"]
 sealed_at=proof.get("verified_at_ms")
 if (type(sealed_at)is not int
     or not plan["first_decision_ms"]<=cutoff<=plan["last_decision_ms"]
     or not first["available_at_ms"]<=sealed_at<=cutoff<=first["valid_until_ms"]):
  raise ContractError("SOURCE_SIGNATURE_AFTER_EXACT_CUTOFF")
 dataset=compile_dataset(spec,original,[cutoff],cutoff)
 example=dataset["examples"][0]
 if (dataset["dataset_id"]!=packet["dataset_id"]
     or example["feature_id"]!=packet["feature_id"]
     or example["label_status"]!="PENDING_OUTCOME"):
  raise ContractError("SOURCE_FEATURE_OR_LABEL_CHANGED")
 event=evidence_event(plan,example,kind=SOURCE_EVENT,
   at_ms=first["available_at_ms"],evidence_sha256=first["source_sha256"])
 if event["event_id"]!=packet["event_id"]:
  raise ContractError("SOURCE_EVENT_ID_CHANGED")
 dbfile=root/"registered-events.sqlite"
 if dbfile.is_symlink() or not dbfile.is_file():
  raise ContractError("REGISTERED_EVENT_DB_MISSING")
 events,samples,batchcount=frozen_ledger_rows(dbfile)
 outcome=read(root/"source-readiness-outcome.json",65536)
 if (outcome.get("status")!="TEST_ONLY_ACTUAL_SOURCE_SIGNED_AND_LOCALLY_INDEXED"
     or outcome.get("source_event_id")!=event["event_id"]
     or outcome.get("signed_proof_sha256")!=sha(root/"original-quote-signed-proof.json")
     or type(outcome.get("signed_proof_durable_at_ms")) is not int
     or not sealed_at<=outcome["signed_proof_durable_at_ms"]<=cutoff):
  raise ContractError("SOURCE_SIGNED_BYTES_NOT_DURABLE_BEFORE_DECISION")
 if (len(events)!=1 or events[0][0]!=event["event_id"]
     or json.loads(events[0][1])!=event
     or samples!=[(example["feature_id"],"AWAITING_OUTCOME",event["event_id"])]
     or batchcount!=0):
  raise ContractError("REGISTERED_SOURCE_LEDGER_NOT_EXACTLY_ONE")
 report={
  "schema":"heptatrader.train-v2-original-source-test-signer-independent-reinspection.v1",
  "original_source_quote_record_id":first["source_id"],
  "original_source_prefix_sha256":packet["original_source_prefix_sha256"],
  "original_source_original_chain_verified":True,
  "public_mac_ed25519_signature_reverified":True,
  "signed_source_message_sha256":hashlib.sha256(
      bytes.fromhex(proof["signed_message_hex"])).hexdigest(),
  "signed_proof_source_sha256":sha(root/"original-quote-signed-proof.json"),
  "registered_plan_id":plan_id,
  "actual_quote_available_at_ms":first["available_at_ms"],
  "proof_verified_at_ms":sealed_at,
  "decision_at_ms":cutoff,
  "original_ib_stale_after_ms":first["valid_until_ms"],
  "signature_and_durable_proof_before_decision":True,
  "precisely_one_causal_research_source_event":True,
  "no_outcome_or_model_prediction":True,
  "native_canonical_ledger_writer_witness_acked":False,
  "independent_product_controller_enrolled":False,
  "real_trading_or_model_promotion_authority":False,
  "host_wall_clock_independent_tpm_attestation":False,
  "broker_queries_sent":0,"broker_orders_sent":0,
 }
 return {**report,"report_id":digest(report)}

def main():
 p=argparse.ArgumentParser()
 p.add_argument("--root",type=Path,required=True)
 p.add_argument("--output",type=Path,required=True)
 a=p.parse_args();os.umask(0o077)
 result=verify(a.root)
 out=a.output
 if out.exists() or out.is_symlink():raise ContractError("IMMUTABLE_READONLY_VERIFICATION_RECEIPT")
 raw=(json.dumps(result,sort_keys=True,indent=2)+"\n").encode()
 fd=os.open(out,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
 with os.fdopen(fd,"wb") as f:f.write(raw);f.flush();os.fsync(f.fileno())
 print(json.dumps({"status":"TEST_ONLY_FIRST_FRESH_SOURCE_PUBLICLY_VERIFIED",
  "mac_test_signature":True,"source_before_decision":True,
  "native_ledger_writer":False,"broker_orders_sent":0,
  "sha256":hashlib.sha256(raw).hexdigest()},sort_keys=True))

if __name__=="__main__":main()
