"""Explicit operator-run, two-PHYSICAL-host TEST-ONLY full-byte escrow.

No historical replay may become a new PREPARED, native LedgerWriter fact, or
product RecoveryGrant. Only fresh synthetic Ed25519 signatures in memory.
The two hosts are contacted through their already-pinned SSH aliases, with
no resident service and no remote signing private key file.
"""
from __future__ import annotations
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import time
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat

DOMAIN=b"hepta.learning-ledger.signed-evidence.v1"
ACK_DOMAIN=b"heptatrader.train-v2.test-only.physical-fullbyte-escrow.v1\x00"
HOSTS={"decision":("x230-ts","qian-ThinkPad-X230"),
       "outcome":("mac-ts","MacBook-Pro.local")}
PAYLOAD_SCHEMA="heptatrader.train-v2-complete-signed-native-escrow.v1"
INTENT_SCHEMA="heptatrader.train-v2-original-native-before-append-escrow-intent.v1"

def digest(raw):
    return hashlib.sha256(raw).hexdigest()

def canonical(v):
    return json.dumps(v,sort_keys=True,separators=(",",":"),allow_nan=False).encode()

def native_id(s):
    raw=s.encode("ascii")
    if not 1<=len(raw)<=128:
        raise ValueError("TEST_ID_TOO_LONG")
    return len(raw).to_bytes(8,"big")+raw

def new_signed_original(stage):
    """These synthetic values have no relationship to actual broker quotes."""
    if stage not in HOSTS:
        raise ValueError("TEST_STAGE_UNKNOWN")
    now=time.time_ns()//1000000
    role="generator" if stage=="decision" else "observer"
    role_tag=0 if stage=="decision" else 1
    source=b"SYNTHETIC-PHASE4Q-NATIVE-PAYLOAD-NO-IB-NO-BROKER"
    payload=source+b"-"+stage.encode()+b"-"+str(now).encode()
    key=Ed25519PrivateKey.generate()
    message=(DOMAIN+native_id("lab.phase4q."+stage+".synthetic.")
        +native_id("lab."+role)+bytes([role_tag])
        # The ORIGINAL canonical Rust SignedLearningEvidenceV1 serializer
        # signs trust_digest, scope_digest, objective_digest in this order.
        # Earlier Phase4Q synthetic physical archives used the wrong order:
        # preserve those old bytes unchanged; NEW test fixtures use canonical.
        +hashlib.sha256(b"TEST_TRUST").digest()
        +hashlib.sha256(b"TEST_SCOPE").digest()
        +hashlib.sha256(b"TEST_OBJECTIVE").digest()
        +(19).to_bytes(8,"big")
        +(now-1000).to_bytes(8,"big")
        +(now+600000).to_bytes(8,"big")
        +hashlib.sha256(payload).digest())
    signature=key.sign(message)
    public=key.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)
    intent={
        "schema":INTENT_SCHEMA,
        "stage":stage,
        "native_signer":role,
        "source_policy":"SHADOW_RESEARCH_PREACTION_TWO_BIT_NO_ORDER_V1",
        "test_only":True,"product_trading_authority":False,
        "source_time_ms":now-700,
        "outcome_due_ms":now+300000 if stage=="decision" else now-300000,
        "expected_previous_witness_sequence":0 if stage=="decision" else 1,
        "next_ledger_sequence":1 if stage=="decision" else 2,
        "source_quote_receipt":digest(b"SYNTHETIC_NO_BROKER_QUOTE"+stage.encode()),
        "test_source_packet_sha256":digest(b"SYNTHETIC_NO_PRODUCT_DATA"+stage.encode()),
        "expected_predecessor_chain_digest":(
            "0"*64 if stage=="decision" else digest(b"SYNTHETIC_PREDECESSOR")
        ),
        "original_native_payload_sha256":digest(payload),
        "original_native_evidence_message_sha256":digest(message),
        "original_detached_signature_sha256":digest(signature),
    }
    request={
        "schema":PAYLOAD_SCHEMA,"original_intent":intent,
        "signed_payload_hex":payload.hex(),
        "signed_evidence_message_hex":message.hex(),
        "detached_signature_hex":signature.hex(),
        "original_signer_public_hex":public.hex(),
        "test_only":True,"trade_authority":False,
    }
    return canonical(request)+b"\n"

def remote(alias,receiver_raw,mode,stage,identity,input_bytes=b""):
    loader="import base64;exec(base64.b64decode("+repr(base64.b64encode(receiver_raw).decode())+"))"
    script=("python3 -c "+shlex.quote(loader)+" "
            +shlex.quote(mode)+" "+shlex.quote(stage)+" "+shlex.quote(identity))
    proc=subprocess.run(
        ["ssh","-o","BatchMode=yes","-o","StrictHostKeyChecking=yes",
         "-o","ConnectTimeout=8",alias,script],
        input=input_bytes,capture_output=True,timeout=25,check=False)
    if proc.returncode:
        # Native bytes never appear in errors or user-visible stdout.
        raise RuntimeError(
            "REMOTE_TEST_ONLY_ESCROW_REFUSED:"+stage+":"+mode+":"
            +proc.stderr.decode(errors="replace")[:220]
        )
    try: return json.loads(proc.stdout)
    except Exception as exc:
        raise RuntimeError("REMOTE_ESCROW_INVALID_JSON") from exc

def verify_host_readback(stage,request,remote_copy,host):
    if (remote_copy.get("host")!=host
            or remote_copy.get("stage")!=stage
            or remote_copy.get("model_or_trade_authority") is not False
            or remote_copy.get("private_keys_returned") is not False):
        raise RuntimeError("PHYSICAL_ESCROW_HOST_OR_EFFECT_MISMATCH")
    original=bytes.fromhex(remote_copy["original_hex"])
    receipt=bytes.fromhex(remote_copy["receipt_hex"])
    if original!=request or digest(original)!=remote_copy["original_sha256"]:
        raise RuntimeError("PHYSICAL_ESCROW_ORIGINAL_BYTES_MISMATCH")
    if digest(receipt)!=remote_copy["receipt_sha256"]:
        raise RuntimeError("PHYSICAL_ESCROW_RECEIPT_DIGEST_MISMATCH")
    body=json.loads(receipt)
    if (body["original_request_sha256"]!=digest(request)
            or body["stage"]!=stage or body["signing_host"]!=host
            or body["test_only"] is not True
            or body["remote_directory_fsynced_before_reply"] is not True
            or body["product_root_enrolled"] is not False
            or body["native_ledger_appended"] is not False):
        raise RuntimeError("PHYSICAL_ESCROW_RECEIPT_NOT_BOUND")
    preimage=(ACK_DOMAIN+digest(request).encode()+stage.encode()
             +host.encode()+body["stored_at_ms"].to_bytes(8,"big"))
    Ed25519PublicKey.from_public_bytes(
        bytes.fromhex(body["receipt_public_hex"])).verify(
            bytes.fromhex(body["receipt_signature_hex"]),preimage)
    return {
        "host":host,"stage":stage,
        "original_complete_bytes_sha256":digest(original),
        "receipt_sha256":digest(receipt),
        "original_complete_byte_count":len(original),
        "distinct_remote_receipt_public_key_sha256":
            digest(bytes.fromhex(body["receipt_public_hex"])),
        "remote_new_key_signature_verified":True,
        "remote_file_and_directory_fsync_reported":True,
        "readback_via_separate_ssh_session":True,
        "product_root_authorized":False,
        "native_ledger_writer_run":False,
    }

def run(receiver_file):
    raw=Path(receiver_file).read_bytes()
    if not raw or len(raw)>128000:
        raise ValueError("RECEIVER_SCRIPT_BOUND")
    results=[]
    for stage,(alias,host) in HOSTS.items():
        request=new_signed_original(stage)
        request_id=digest(request)
        ack=remote(alias,raw,"seal",stage,request_id,request)
        if (ack.get("host")!=host or ack.get("stage")!=stage
                or ack.get("original_sha256")!=request_id
                or ack.get("status")!="NEW_COMPLETE_BYTES_SEALED_AND_READBACK"
                or ack.get("remote_fsync") is not True):
            raise RuntimeError("REMOTE_ESCROW_NEW_SEAL_NOT_ATTESTED")
        old=remote(alias,raw,"read",stage,request_id)
        result=verify_host_readback(stage,request,old,host)
        if ack["receipt_sha256"]!=result["receipt_sha256"]:
            raise RuntimeError("REMOTE_ESCROW_ACK_NOT_MATCHING_DISK")
        identical=remote(alias,raw,"seal",stage,request_id,request)
        if (identical.get("status")!="IDEMPOTENT_EXACT_REMOTE_READBACK"
                or identical.get("receipt_sha256")!=result["receipt_sha256"]):
            raise RuntimeError("REMOTE_ESCROW_LOST_ACK_RETRY_NOT_IDEMPOTENT")
        third=remote(alias,raw,"read",stage,request_id)
        if third["original_hex"]!=old["original_hex"] or third["receipt_hex"]!=old["receipt_hex"]:
            raise RuntimeError("REMOTE_ESCROW_CHANGED_AFTER_DUPLICATE")
        result["idempotent_exact_retry_verified"]=True
        result["sha256_reverified_after_duplicate"]=True
        results.append(result)
    if results[0]["distinct_remote_receipt_public_key_sha256"]==results[1]["distinct_remote_receipt_public_key_sha256"]:
        raise RuntimeError("REMOTE_ESCROW_IDENTICAL_HOST_TEST_SIGNER_KEY")
    return {
      "schema":"heptatrader.train-v2-two-physical-host-fullbytes-test-only.v1",
      "observed_at_ms":time.time_ns()//1000000,
      "receiver_source_sha256":digest(raw),
      "original_actor_signed_message_created_in_memory":True,
      "native_shaped_original_signed_bytes":True,
      "original_broker_market_data_used":False,
      "same_user_or_operator_can_administer_both_hosts":True,
      "independent_organization_or_hardware_root_qualified":False,
      "product_recovery_authority_enrolled":False,
      "preappend_real_broker_event_qualified":False,
      "future_product_native_ledger_writer_executed":False,
      "original_old_phase4o_data_recovered":False,
      "hosts":results,
      "new_trading_order_count":0,
      "automatic_model_training":False,
      "model_promotion_allowed":False,
      "account_or_service_changed":False,
    }

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--receiver",type=Path,required=True)
    ap.add_argument("--output",type=Path,required=True)
    args=ap.parse_args()
    report=run(args.receiver)
    path=args.output
    if path.exists() or path.is_symlink():
        raise ValueError("PHYSICAL_ESCROW_IMMUTABLE_REPORT_EXISTS")
    path.parent.mkdir(parents=True,mode=0o700,exist_ok=True)
    raw=canonical(report)+b"\n"
    fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_WRONLY,0o600)
    with os.fdopen(fd,"wb") as f:
        f.write(raw);f.flush();os.fsync(f.fileno())
    dirfd=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(dirfd)
    finally:os.close(dirfd)
    print(json.dumps({
       "status":"TWO_REAL_HOSTS_SYNTHETIC_FULL_BYTES_ESCROW_AND_RETRIEVAL",
       "hosts":[item["host"] for item in report["hosts"]],
       "full_byte_readback_sha256":[item["original_complete_bytes_sha256"]
                                   for item in report["hosts"]],
       "idempotent_exact_retries":True,
       "root_authority_qualified":False,
       "native_ledger_executed":False,
       "new_broker_orders":0,
       "report_sha256":digest(raw),
    },sort_keys=True))

if __name__=="__main__":
    main()
