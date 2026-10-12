"""Physical X230/Mac one-shot TEST-ONLY full signed byte escrow receiver.

No service, credentials, root authority, original broker data or LedgerWriter
write. For a new synthetic request: exact original actor signed bytes are
verified, persisted (file + directory fsync), and read back on a separate SSH.
Each receipt is independently signed by a NEW in-memory host key; private
escrow keys are never saved or transferred. This is NOT product custody.
Python >=3.9.
"""
from __future__ import annotations
import base64
import hashlib
import json
import os
from pathlib import Path
import socket
import stat
import sys
import time

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey, Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat

SCHEMA="heptatrader.train-v2-complete-signed-native-escrow.v1"
INTENT="heptatrader.train-v2-original-native-before-append-escrow-intent.v1"
RECEIPT="heptatrader.train-v2-physical-test-fullbytes-receipt.v1"
ROOT=Path.home()/".local/state/heptatrader-train-v2-phase4q-20261012"
HOSTS={"decision":"qian-ThinkPad-X230","outcome":"MacBook-Pro.local"}
ROLE={"decision":("generator","lab.generator",0),
      "outcome":("observer","lab.observer",1)}
DOMAIN=b"hepta.learning-ledger.signed-evidence.v1"
ACK_DOMAIN=b"heptatrader.train-v2.test-only.physical-fullbyte-escrow.v1\x00"
LIMIT=262144

def canonical(x):
    return json.dumps(x,sort_keys=True,separators=(",",":"),allow_nan=False).encode()

def digest(raw):
    return hashlib.sha256(raw).hexdigest()

def strict(raw):
    def unique(pairs):
        out={}
        for k,v in pairs:
            if k in out:raise ValueError("DUPLICATE_JSON_KEY")
            out[k]=v
        return out
    return json.loads(raw,object_pairs_hook=unique,
        parse_constant=lambda _:(_ for _ in ()).throw(ValueError("JSON_NONFINITE")))

def decode(value,name,max_len):
    if not isinstance(value,str) or len(value)%2 or len(value)<2 or len(value)>max_len*2:
        raise ValueError("INVALID_HEX_BOUND:"+name)
    try: return bytes.fromhex(value)
    except ValueError as exc:raise ValueError("INVALID_HEX:"+name) from exc

def native_id(data,pos):
    if pos+8>len(data):raise ValueError("NATIVE_ID_LENGTH")
    size=int.from_bytes(data[pos:pos+8],"big");pos+=8
    if size<1 or size>128 or pos+size>len(data):
        raise ValueError("NATIVE_ID_BOUND")
    raw=data[pos:pos+size]
    if any(c<33 or c>126 for c in raw):
        raise ValueError("NATIVE_ID_ASCII")
    return raw.decode("ascii"),pos+size

def parse_native_message(data,stage,payload):
    if not data.startswith(DOMAIN):
        raise ValueError("NATIVE_EVIDENCE_DOMAIN")
    pos=len(DOMAIN)
    evidence_id,pos=native_id(data,pos)
    principal,pos=native_id(data,pos)
    if pos+1+3*32+3*8+32!=len(data):
        raise ValueError("NATIVE_EXACT_WIRE_LENGTH")
    tag=data[pos];pos+=1
    for _ in range(3):
        if data[pos:pos+32]==bytes(32):
            raise ValueError("NATIVE_ZERO_BINDING_DIGEST")
        pos+=32
    epoch=int.from_bytes(data[pos:pos+8],"big");pos+=8
    issued=int.from_bytes(data[pos:pos+8],"big");pos+=8
    expires=int.from_bytes(data[pos:pos+8],"big");pos+=8
    content=data[pos:pos+32]
    role,expected,tag_want=ROLE[stage]
    if (tag!=tag_want or principal!=expected
            or not evidence_id.startswith("lab.")
            or epoch<=0 or issued<=0 or not issued<expires
            or content!=hashlib.sha256(payload).digest()):
        raise ValueError("NATIVE_SIGNED_EVIDENCE_FIELDS")
    return {"principal":principal,"role":role,"issued_at_ms":issued,
            "expires_at_ms":expires,"authority_epoch":epoch}

def validate_request(request,stage,*,require_current_freshness=True):
    if (not isinstance(request,dict) or set(request)!={
            "schema","original_intent","signed_payload_hex",
            "signed_evidence_message_hex","detached_signature_hex",
            "original_signer_public_hex","test_only","trade_authority"}
            or request["schema"]!=SCHEMA or request["test_only"] is not True
            or request["trade_authority"] is not False):
        raise ValueError("PHYSICAL_REQUEST_SCOPE")
    intent=request["original_intent"]
    expected_role=ROLE[stage][0]
    if (not isinstance(intent,dict) or intent.get("schema")!=INTENT
            or intent.get("stage")!=stage
            or intent.get("native_signer")!=expected_role
            or intent.get("source_policy")!="SHADOW_RESEARCH_PREACTION_TWO_BIT_NO_ORDER_V1"
            or intent.get("test_only") is not True
            or intent.get("product_trading_authority") is not False):
        raise ValueError("PHYSICAL_ORIGINAL_INTENT")
    previous=0 if stage=="decision" else 1
    nxt=previous+1
    if (type(intent.get("expected_previous_witness_sequence")) is not int
            or type(intent.get("next_ledger_sequence")) is not int
            or intent["expected_previous_witness_sequence"]!=previous
            or intent["next_ledger_sequence"]!=nxt):
        raise ValueError("PHYSICAL_FRONTIER")
    timestamp=intent.get("source_time_ms")
    due=intent.get("outcome_due_ms")
    now=time.time_ns()//1000000
    if (type(timestamp)is not int or type(due)is not int
            or (require_current_freshness and not 0<=now-timestamp<180000)
            or (stage=="decision" and due<=timestamp)
            or (stage=="outcome" and due>timestamp)):
        raise ValueError("PHYSICAL_TEST_SOURCE_WINDOW")
    if (stage=="decision" and intent.get("expected_predecessor_chain_digest")!="0"*64):
        raise ValueError("PHYSICAL_DECISION_PREDECESSOR")
    if (stage=="outcome" and intent.get("expected_predecessor_chain_digest")=="0"*64):
        raise ValueError("PHYSICAL_OUTCOME_PREDECESSOR")
    payload=decode(request["signed_payload_hex"],"payload",65536)
    message=decode(request["signed_evidence_message_hex"],"message",65536)
    signature=decode(request["detached_signature_hex"],"signature",64)
    public=decode(request["original_signer_public_hex"],"public",32)
    if len(signature)!=64 or len(public)!=32:
        raise ValueError("PHYSICAL_SIGNATURE_SIZE")
    for key,content in (("original_native_payload_sha256",payload),
                        ("original_native_evidence_message_sha256",message),
                        ("original_detached_signature_sha256",signature)):
        if intent.get(key)!=digest(content):
            raise ValueError("PHYSICAL_DIGEST_MISMATCH:"+key)
    for key in ("source_quote_receipt","test_source_packet_sha256",
                "expected_predecessor_chain_digest"):
        v=intent.get(key)
        if not isinstance(v,str) or len(v)!=64 or any(x not in "0123456789abcdef" for x in v):
            raise ValueError("PHYSICAL_INVALID_INTENT_DIGEST")
    native=parse_native_message(message,stage,payload)
    # Archived old signatures are revalidated at their ORIGINAL signed
    # source time, not promoted by pretending the present clock is earlier.
    signed_at=native["issued_at_ms"]
    if not timestamp-3000<=signed_at<=timestamp+3000:
        raise ValueError("PHYSICAL_NATIVE_SIGNATURE_TIME")
    if not signed_at<native["expires_at_ms"] or (
        require_current_freshness and not now<native["expires_at_ms"]
    ):
        raise ValueError("PHYSICAL_NATIVE_SIGNATURE_EXPIRED")
    Ed25519PublicKey.from_public_bytes(public).verify(signature,message)
    return {"source_time_ms":timestamp,"stage":stage,**native}

def check_host(stage):
    if stage not in HOSTS or socket.gethostname()!=HOSTS[stage]:
        raise ValueError("PHYSICAL_HOST_STAGE_MISMATCH")

def private_root():
    if ROOT.is_symlink():
        raise ValueError("PHYSICAL_ROOT_SYMLINK")
    ROOT.mkdir(parents=True,exist_ok=True,mode=0o700)
    s=ROOT.lstat()
    if not stat.S_ISDIR(s.st_mode) or s.st_uid!=os.geteuid() or stat.S_IMODE(s.st_mode)!=0o700:
        raise ValueError("PHYSICAL_ROOT_NOT_PRIVATE")
    return ROOT

def read_once(path,maxsize=LIMIT):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        s=os.fstat(fd)
        if (not stat.S_ISREG(s.st_mode) or s.st_uid!=os.geteuid()
                or stat.S_IMODE(s.st_mode)!=0o600 or not 0<s.st_size<=maxsize):
            raise ValueError("PHYSICAL_ARCHIVE_MODE_OR_SIZE")
        parts=[];remaining=s.st_size
        while remaining:
            chunk=os.read(fd,min(remaining,65536))
            if not chunk:raise ValueError("PHYSICAL_TRUNCATED")
            parts.append(chunk);remaining-=len(chunk)
        return b"".join(parts)
    finally:os.close(fd)

def filename(stage,key):
    if not isinstance(key,str) or len(key)!=64 or any(c not in "0123456789abcdef" for c in key):
        raise ValueError("PHYSICAL_UNSAFE_ARCHIVE_ID")
    return "original-"+stage+"-"+key+".json"

def exact_read(stage,key):
    base=private_root()
    archive=read_once(base/filename(stage,key))
    receipt=read_once(base/("receipt-"+stage+"-"+key+".json"))
    if digest(archive)!=key or not archive.endswith(b"\n") or not receipt.endswith(b"\n"):
        raise ValueError("PHYSICAL_ARCHIVE_IDENTITY_CHANGED")
    request=strict(archive)
    signed=strict(receipt)
    if canonical(request)+b"\n"!=archive or canonical(signed)+b"\n"!=receipt:
        raise ValueError("PHYSICAL_NONCANONICAL_ARCHIVE")
    native=validate_request(
        request,stage,require_current_freshness=False
    )  # historical byte/signature validation is not new authorization
    required={"schema","original_request_sha256","stored_at_ms","signing_host",
              "stage","receipt_public_hex","receipt_signature_hex",
              "native_original_message_sha256","test_only",
              "remote_directory_fsynced_before_reply","product_root_enrolled",
              "native_ledger_appended"}
    if (set(signed)!=required or signed["schema"]!=RECEIPT
            or signed["original_request_sha256"]!=key
            or signed["stage"]!=stage
            or signed["signing_host"]!=socket.gethostname()
            or signed["native_original_message_sha256"]!=digest(bytes.fromhex(request["signed_evidence_message_hex"]))
            or signed["test_only"] is not True
            or signed["remote_directory_fsynced_before_reply"] is not True
            or signed["product_root_enrolled"] is not False
            or signed["native_ledger_appended"] is not False
            or type(signed["stored_at_ms"]) is not int
            or signed["stored_at_ms"]<native["source_time_ms"]
            or signed["stored_at_ms"]>native["source_time_ms"]+180000
            or signed["stored_at_ms"]>time.time_ns()//1000000):
        raise ValueError("PHYSICAL_RECEIPT_SCOPE")
    sig=decode(signed["receipt_signature_hex"],"receipt_signature",64)
    pub=decode(signed["receipt_public_hex"],"receipt_public",32)
    if len(sig)!=64 or len(pub)!=32:
        raise ValueError("PHYSICAL_RECEIPT_SIGNATURE_SIZE")
    preimage=ACK_DOMAIN+key.encode()+stage.encode()+signed["signing_host"].encode()+signed["stored_at_ms"].to_bytes(8,"big")
    Ed25519PublicKey.from_public_bytes(pub).verify(sig,preimage)
    return archive,receipt,native

def seal(stage,raw):
    check_host(stage)
    root=private_root()
    if len(raw)>LIMIT or not raw.endswith(b"\n"):
        raise ValueError("PHYSICAL_REQUEST_SIZE")
    request=strict(raw)
    if canonical(request)+b"\n"!=raw:
        raise ValueError("PHYSICAL_REQUEST_CANONICAL")
    # Old completed archives are revalidated at their ORIGINAL signed time
    # without claiming a new event. Freshness is required only for FIRST SEAL.
    validate_request(request,stage,require_current_freshness=False)
    key=digest(raw)
    original=root/filename(stage,key)
    def exact_replay():
        prior,receipt,_=exact_read(stage,key)
        if prior!=raw:
            raise ValueError("PHYSICAL_ARCHIVED_ORIGINAL_CONFLICT")
        return {"status":"IDEMPOTENT_EXACT_REMOTE_READBACK",
                "original_sha256":digest(prior),"receipt_sha256":digest(receipt),
                "host":socket.gethostname(),"stage":stage,
                "remote_fsync":True,"new_native_event":False}
    if original.exists() or original.is_symlink():
        return exact_replay()
    validate_request(request,stage,require_current_freshness=True)
    try:
        fd=os.open(original,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    except FileExistsError:
        return exact_replay()
    with os.fdopen(fd,"wb") as f:
        f.write(raw);f.flush();os.fsync(f.fileno())
    at=time.time_ns()//1000000
    keypair=Ed25519PrivateKey.generate()
    public=keypair.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)
    message=ACK_DOMAIN+key.encode()+stage.encode()+socket.gethostname().encode()+at.to_bytes(8,"big")
    receipt={
        "schema":RECEIPT,"original_request_sha256":key,"stored_at_ms":at,
        "signing_host":socket.gethostname(),"stage":stage,
        "receipt_public_hex":public.hex(),
        "receipt_signature_hex":keypair.sign(message).hex(),
        "native_original_message_sha256":digest(bytes.fromhex(request["signed_evidence_message_hex"])),
        "test_only":True,"remote_directory_fsynced_before_reply":True,
        "product_root_enrolled":False,"native_ledger_appended":False}
    dest=root/("receipt-"+stage+"-"+key+".json")
    blob=canonical(receipt)+b"\n"
    fd=os.open(dest,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,"wb") as f:
        f.write(blob);f.flush();os.fsync(f.fileno())
    dirfd=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:os.fsync(dirfd)
    finally:os.close(dirfd)
    saved,proof,_=exact_read(stage,key)
    assert saved==raw and proof==blob
    return {"status":"NEW_COMPLETE_BYTES_SEALED_AND_READBACK",
            "original_sha256":digest(raw),"receipt_sha256":digest(blob),
            "host":socket.gethostname(),"stage":stage,
            "remote_fsync":True,"new_native_event":False}

def main():
    if len(sys.argv)!=4:
        raise ValueError("USAGE mode stage request_sha256")
    mode,stage,key=sys.argv[1:4]
    check_host(stage)
    if mode=="seal":
        raw=sys.stdin.buffer.read(LIMIT+1)
        result=seal(stage,raw)
        print(json.dumps(result,sort_keys=True))
    elif mode=="read":
        archive,receipt,_=exact_read(stage,key)
        print(json.dumps({
            "schema":"heptatrader.train-v2-physical-test-archive-readback.v1",
            "host":socket.gethostname(),"stage":stage,
            "original_hex":archive.hex(),"receipt_hex":receipt.hex(),
            "original_sha256":digest(archive),"receipt_sha256":digest(receipt),
            "private_keys_returned":False,"model_or_trade_authority":False,
        },sort_keys=True))
    else:
        raise ValueError("PHYSICAL_MODE")

if __name__=="__main__":
    try:main()
    except Exception as exc:
        # No original trading bytes or secret keys are printed in failure.
        print(json.dumps({"status":"REFUSED","type":type(exc).__name__,
            "reason":str(exc)[:120],"trade_authority":False}),file=sys.stderr)
        raise SystemExit(2)
