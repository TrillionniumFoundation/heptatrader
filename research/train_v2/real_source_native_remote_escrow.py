"""TEST-ONLY independent host precommit of exact ORIGINAL native signed intent.

ONLY stage=decision on X230 and stage=outcome on Mac. Each escrow signer
has a distinct key, NOT the native Generator/Observer keys. Exact typed
intent is received before canonical Writer append; host O_EXCL persists
signed proof and fsyncs both receipt file and directory BEFORE replying.

No production trustee/root enrollment or independent organization. This
program does not read any broker account, modify a ledger, or issue orders.
"""
from __future__ import annotations

import argparse
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import socket
import stat
import sys
import time
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,PublicFormat,PrivateFormat,NoEncryption,
)

DOMAIN=b"heptatrader.test-only.independently-held-recovery-intent.v1\x00"
SCHEMA="heptatrader.train-v2-original-native-before-append-escrow-intent.v1"
RECEIPT_SCHEMA="heptatrader.train-v2-escrow-signed-original-native-intent.v1"
ROOT=Path.home()/".local/state/hepta-native-escrow-phase4n-20261012"
OWNERS={"decision":"qian-ThinkPad-X230","outcome":"MacBook-Pro.local"}


def canonical(value)->bytes:
    return json.dumps(value,sort_keys=True,separators=(",",":"),allow_nan=False).encode()


def digest(value:bytes)->str:
    return hashlib.sha256(value).hexdigest()


def directory()->Path:
    ROOT.parent.mkdir(parents=True,mode=0o700,exist_ok=True)
    ROOT.mkdir(mode=0o700,exist_ok=True)
    s=ROOT.lstat()
    if not stat.S_ISDIR(s.st_mode) or s.st_uid!=os.geteuid() or stat.S_IMODE(s.st_mode)!=0o700:
        raise ValueError("ESCROW_OWNER_PRIVATE_ROOT_REQUIRED")
    return ROOT


def allowed(stage:str)->None:
    if stage not in OWNERS or socket.gethostname()!=OWNERS[stage]:
        raise ValueError("ESCROW_STAGE_INDEPENDENT_MACHINE_MISMATCH")


def keyfile(stage:str)->Path:
    allowed(stage)
    return directory()/(stage+".escrow-test-only.key")


def load(stage:str)->Ed25519PrivateKey:
    path=keyfile(stage)
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        s=os.fstat(fd)
        if not stat.S_ISREG(s.st_mode) or s.st_uid!=os.geteuid() or stat.S_IMODE(s.st_mode)!=0o600 or s.st_size!=32:
            raise ValueError("ESCROW_PRIVATE_KEY_UNSAFE")
        raw=os.read(fd,33)
        if len(raw)!=32:raise ValueError("ESCROW_KEY_LENGTH")
    finally:
        os.close(fd)
    return Ed25519PrivateKey.from_private_bytes(raw)


def public(stage:str)->str:
    return load(stage).public_key().public_bytes(Encoding.Raw,PublicFormat.Raw).hex()


def validate(body:dict,stage:str,*,require_current_freshness:bool=True)->None:
    if not isinstance(body,dict) or body.get("schema")!=SCHEMA or body.get("stage")!=stage:
        raise ValueError("ESCROW_ORIGINAL_NATIVE_INTENT_SCHEMA_SCOPE")
    if body.get("test_only") is not True or body.get("product_trading_authority") is not False:
        raise ValueError("ESCROW_IS_NOT_PRODUCT_AUTHORITY")
    if body.get("native_signer")!=("generator" if stage=="decision" else "observer"):
        raise ValueError("ESCROW_SIGNER_BINDING_WRONG")
    if body.get("source_policy")!="SHADOW_RESEARCH_PREACTION_TWO_BIT_NO_ORDER_V1":
        raise ValueError("ESCROW_ORIGINAL_LLM_POLICY_NOT_ACCEPTABLE")
    sequence=1 if stage=="decision" else 2
    if (type(body.get("expected_previous_witness_sequence")) is not int or
            type(body.get("next_ledger_sequence")) is not int or
            body.get("expected_previous_witness_sequence")!=sequence-1 or
            body.get("next_ledger_sequence")!=sequence):
        raise ValueError("ESCROW_SOURCE_WITNESS_FRONTIER_INVALID")
    for k in ("original_native_payload_sha256","original_detached_signature_sha256",
              "original_native_evidence_message_sha256","test_source_packet_sha256",
              "expected_predecessor_chain_digest","source_quote_receipt"):
        value=body.get(k)
        if not isinstance(value,str) or len(value)!=64 or any(c not in '0123456789abcdef' for c in value):
            raise ValueError("ESCROW_NATIVE_BINDING_HASH_INVALID")
    if (body.get("expected_predecessor_chain_digest")==("0"*64)) != (stage=="decision"):
        raise ValueError("ESCROW_WRONG_PREDECESSOR_SCOPE")
    if type(body.get("source_time_ms")) is not int:
        raise ValueError("ESCROW_SOURCE_TIME_INVALID")
    if type(body.get("outcome_due_ms")) is not int:
        raise ValueError("ESCROW_DUE_TIME_INVALID")
    if stage=="decision" and body["outcome_due_ms"]<=body["source_time_ms"]:
        raise ValueError("ESCROW_DECISION_MATURITY_NOT_LATER")
    if stage=="outcome" and body["source_time_ms"]<body["outcome_due_ms"]:
        raise ValueError("ESCROW_OUTCOME_EARLIER_THAN_ACTUAL_MATURITY")
    if require_current_freshness:
        now=time.time_ns()//1000000
        if stage=="decision" and not 0<=now-body["source_time_ms"]<=180000:
            raise ValueError("ESCROW_REAL_NATIVE_GENERATOR_SIGNED_TOO_LATE")
        if stage=="outcome" and now<body["source_time_ms"]:
            raise ValueError("ESCROW_OBSERVER_BEFORE_HORIZON")


def replay_saved_exact_receipt(receipt:Path,stage:str,raw_intent:bytes)->bytes:
    """Return only byte-exact *already durable* Escrow intent, never overwrite.

    The stage's same original private test key remains required to check
    signer continuity during this experimental signer-lifecycle window.
    A lost network reply can be redelivered without minting a new signed
    intent. This is not a production RecoveryGrant or product identity.
    """
    fd=os.open(receipt,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        st=os.fstat(fd)
        if (not stat.S_ISREG(st.st_mode) or st.st_uid!=os.geteuid()
                or stat.S_IMODE(st.st_mode)!=0o600
                or st.st_size<100 or st.st_size>16384):
            raise ValueError("ESCROW_PRIOR_ARCHIVE_NOT_OWNER_PRIVATE")
        prior=os.read(fd,16385)
    finally:
        os.close(fd)
    if not prior.endswith(b"\n"):
        raise ValueError("ESCROW_PRIOR_RECEIPT_INCOMPLETE")
    doc=json.loads(prior)
    if canonical(doc)+b"\n" != prior:
        raise ValueError("ESCROW_PRIOR_RECEIPT_NOT_CANONICAL")
    original=json.loads(raw_intent)
    pinned=public(stage)
    if (doc.get("schema")!=RECEIPT_SCHEMA
            or doc.get("body")!=original
            or doc.get("body_sha256")!=digest(raw_intent)
            or doc.get("signing_host")!=socket.gethostname()
            or doc.get("escrow_test_role")!="escrow_"+stage
            or doc.get("public_key_hex")!=pinned
            or doc.get("message_domain_hex")!=DOMAIN.hex()
            or doc.get("test_only") is not True
            or doc.get("production_root_authority") is not False
            or doc.get("durably_persisted_before_reply") is not True):
        raise ValueError("ESCROW_PRIOR_RECEIPT_IDENTITY_CONFLICT")
    issued=doc.get("intent_held_at_ms")
    if (type(issued) is not int or issued<original["source_time_ms"]
            or issued>time.time_ns()//1000000):
        raise ValueError("ESCROW_PRIOR_RECEIPT_TIME_CONFLICT")
    if (stage=="decision" and
            not 0<=issued-original["source_time_ms"]<=180000):
        raise ValueError("ESCROW_PRIOR_SOURCE_TIME_TOO_LATE")
    if stage=="decision" and issued>=original["outcome_due_ms"]:
        raise ValueError("ESCROW_PRIOR_DECISION_AFTER_MARKET_OUTCOME")
    if stage=="outcome" and original["source_time_ms"]<original["outcome_due_ms"]:
        raise ValueError("ESCROW_PRIOR_OBSERVER_BEFORE_MARKET_OUTCOME")
    try:
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(pinned)).verify(
            bytes.fromhex(doc["signature_hex"]),
            DOMAIN+raw_intent+issued.to_bytes(8,"big"))
    except Exception as error:
        raise ValueError("ESCROW_PRIOR_RECEIPT_BAD_ED25519_SIGNATURE") from error
    return prior


def action(mode:str,stage:str)->None:
    allowed(stage)
    p=keyfile(stage)
    if mode=="init":
        if p.exists() or p.is_symlink():raise FileExistsError("ESCROW_PRIVATE_KEY_EXISTS")
        key=Ed25519PrivateKey.generate()
        raw=key.private_bytes(Encoding.Raw,PrivateFormat.Raw,NoEncryption())
        fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,"wb") as f:f.write(raw);f.flush();os.fsync(f.fileno())
        fd=os.open(ROOT,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(fd)
        finally:os.close(fd)
        print(json.dumps({"role":"escrow_"+stage,"host":socket.gethostname(),
                          "public_key_hex":public(stage),"test_only":True},sort_keys=True))
    elif mode=="pub":
        print(json.dumps({"role":"escrow_"+stage,"host":socket.gethostname(),
                          "public_key_hex":public(stage),"test_only":True},sort_keys=True))
    elif mode=="destroy":
        load(stage)
        p.unlink()
        fd=os.open(ROOT,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(fd)
        finally:os.close(fd)
        print(json.dumps({"stage":stage,"only_test_key_unlinked":True,"host":socket.gethostname()},sort_keys=True))
    elif mode=="seal":
        line=sys.stdin.buffer.readline(16*1024+1).strip()
        if len(line)>16*1024:raise ValueError("ESCROW_INTENT_TOO_LARGE")
        raw=bytes.fromhex(line.decode("ascii"))
        body=json.loads(raw)
        if canonical(body)!=raw:raise ValueError("ESCROW_NONCANONICAL_INTENT_BYTES")
        # An EXACT existing prior durable receipt may be read back after
        # source-time expiration without re-signing or creating any new
        # permission. Only NEW receipts require a current source-time window.
        validate(body,stage,require_current_freshness=False)
        receipt=directory()/("receipt-"+stage+"-"+digest(raw)+".json")
        if receipt.exists() or receipt.is_symlink():
            prior=replay_saved_exact_receipt(receipt,stage,raw)
            print(json.dumps({"receipt_hex":prior.hex(),"receipt_sha256":digest(prior),
                              "role":"escrow_"+stage,"host":socket.gethostname(),
                              "durable_receipt_path":str(receipt),
                              "idempotent_prior_receipt_readback":True,
                              "test_only":True},sort_keys=True))
            return
        validate(body,stage,require_current_freshness=True)
        now=time.time_ns()//1000000
        key=load(stage)
        message=DOMAIN+raw+now.to_bytes(8,"big")
        signature=key.sign(message)
        record={
            "schema":RECEIPT_SCHEMA,
            "body":body,
            "body_sha256":digest(raw),
            "intent_held_at_ms":now,
            "signing_host":socket.gethostname(),
            "escrow_test_role":"escrow_"+stage,
            "public_key_hex":public(stage),
            "signature_hex":signature.hex(),
            "message_domain_hex":DOMAIN.hex(),
            "test_only":True,
            "production_root_authority":False,
            "durably_persisted_before_reply":True,
        }
        blob=canonical(record)+b"\n"
        try:
            fd=os.open(receipt,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        except FileExistsError:
            # Concurrent identical caller: only a COMPLETE durable,
            # cryptographically identical original receipt may be returned.
            # Incomplete writers cause HOLD, never fabricate an ACK.
            blob=replay_saved_exact_receipt(receipt,stage,raw)
            replay=True
        else:
            with os.fdopen(fd,"wb") as f:
                f.write(blob);f.flush();os.fsync(f.fileno())
            d=os.open(ROOT,os.O_RDONLY|os.O_DIRECTORY)
            try:os.fsync(d)
            finally:os.close(d)
            replay=False
        print(json.dumps({"receipt_hex":blob.hex(),"receipt_sha256":digest(blob),
                          "role":"escrow_"+stage,"host":socket.gethostname(),
                          "durable_receipt_path":str(receipt),
                          "idempotent_prior_receipt_readback":replay,
                          "test_only":True},sort_keys=True))
    else:
        raise ValueError("ESCROW_MODE_UNKNOWN")


if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("mode",choices=("init","pub","seal","destroy"))
    p.add_argument("stage",choices=("decision","outcome"))
    a=p.parse_args()
    action(a.mode,a.stage)
