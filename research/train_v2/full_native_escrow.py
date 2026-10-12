"""TEST-ONLY complete original native signed-byte escrow contract.

Never accepts a digest-only escrow as recoverable original V2 evidence.
Does not enroll canonical RecoveryAuthority, issue new signatures, change
LedgerWriter, or authorize an ACK. Only bounded private immutable bytes.
"""
from __future__ import annotations
import hashlib
import json
import os
import stat
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.exceptions import InvalidSignature

from .real_source_native_remote_escrow import SCHEMA as INTENT_SCHEMA, canonical

SCHEMA="heptatrader.train-v2-complete-signed-native-escrow.v1"
LIMIT=262144


def sha(data:bytes)->str:
    return hashlib.sha256(data).hexdigest()


def decode_hex(value,name,max_bytes):
    if (not isinstance(value,str) or len(value)%2 or
        not 2<=len(value)<=max_bytes*2):
        raise ValueError("NATIVE_ESCROW_HEX_BOUND:"+name)
    try:
        return bytes.fromhex(value)
    except ValueError as err:
        raise ValueError("NATIVE_ESCROW_HEX_INVALID:"+name) from err


def validate_complete(request):
    """Verify exact message, signature, payload and original intent binding.

    The original typed intent's hashes refer to the canonical Rust signing
    payload and SignedLearningEvidenceV1 signing bytes, not to a future
    substitute reconstruction. No signature verification is skipped.
    """
    if not isinstance(request,dict) or set(request)!={
        "schema","original_intent","signed_payload_hex",
        "signed_evidence_message_hex","detached_signature_hex",
        "original_signer_public_hex","test_only","trade_authority"}:
        raise ValueError("NATIVE_ESCROW_COMPLETE_SCHEMA")
    intent=request["original_intent"]
    if (request["schema"]!=SCHEMA or
        not isinstance(intent,dict) or intent.get("schema")!=INTENT_SCHEMA or
        intent.get("stage") not in ("decision","outcome") or
        intent.get("test_only") is not True or
        request.get("test_only") is not True or
        request.get("trade_authority") is not False or
        intent.get("product_trading_authority") is not False):
        raise ValueError("NATIVE_ESCROW_TEST_ONLY_SCOPE")
    expected_role="generator" if intent["stage"]=="decision" else "observer"
    if intent.get("native_signer")!=expected_role:
        raise ValueError("NATIVE_ESCROW_NATIVE_SIGNER_ROLE")
    payload=decode_hex(request["signed_payload_hex"],"payload",LIMIT)
    message=decode_hex(request["signed_evidence_message_hex"],"message",LIMIT)
    signature=decode_hex(request["detached_signature_hex"],"signature",64)
    public=decode_hex(request["original_signer_public_hex"],"public",32)
    if len(signature)!=64 or len(public)!=32:
        raise ValueError("NATIVE_ESCROW_NATIVE_SIGNATURE_LENGTH")
    for name,raw in (
        ("original_native_payload_sha256",payload),
        ("original_native_evidence_message_sha256",message),
        ("original_detached_signature_sha256",signature)):
        if intent.get(name)!=sha(raw):
            raise ValueError("NATIVE_ESCROW_ORIGINAL_BYTES_DIGEST:"+name)
    domain=b"hepta.learning-ledger.signed-evidence.v1"
    if not message.startswith(domain) or len(message)<=len(domain)+32:
        raise ValueError("NATIVE_ESCROW_WRONG_NATIVE_DOMAIN")
    if message[-32:] != hashlib.sha256(payload).digest():
        raise ValueError("NATIVE_ESCROW_SIGNED_PAYLOAD_BINDING")
    try:
        Ed25519PublicKey.from_public_bytes(public).verify(signature,message)
    except (InvalidSignature,ValueError) as err:
        raise ValueError("NATIVE_ESCROW_DETACHED_SIGNATURE_INVALID") from err
    summary={
        "schema":SCHEMA,
        "stage":intent["stage"],
        "native_signer":expected_role,
        "original_intent_digest":sha(canonical(intent)),
        "signed_payload_sha256":sha(payload),
        "signed_message_sha256":sha(message),
        "signed_signature_sha256":sha(signature),
        "original_signer_public_hex":public.hex(),
        "recoverable_complete_original_test_bytes":True,
        "independent_remote_preappend_fsync_proven":False,
        "native_root_recovery_authority_enrolled":False,
        "native_witness_advance_authorized":False,
        "product_trading_authority":False,
    }
    return summary


def seal_local_test_only(directory, request):
    """Local test-only create-once bytes, no remote host identity claim."""
    result=validate_complete(request)
    root=Path(directory)
    if root.is_symlink():
        raise ValueError("NATIVE_ESCROW_SYMLINK")
    root.mkdir(mode=0o700,parents=True,exist_ok=True)
    st=root.stat()
    if not stat.S_ISDIR(st.st_mode) or st.st_uid!=os.getuid() or stat.S_IMODE(st.st_mode)!=0o700:
        raise ValueError("NATIVE_ESCROW_PRIVATE_ROOT_REQUIRED")
    content=canonical(request)+b"\n"
    if len(content)>LIMIT:
        raise ValueError("NATIVE_ESCROW_RECORD_TOO_BIG")
    name="original-"+result["stage"]+"-"+result["original_intent_digest"]+".json"
    target=root/name
    fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,"wb") as f:
        f.write(content);f.flush();os.fsync(f.fileno())
    directory_fd=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:os.fsync(directory_fd)
    finally:os.close(directory_fd)
    return {**result,"archive_file_sha256":sha(content),"archive_basename":name}


def verify_saved(directory, stage, intent_digest):
    root=Path(directory)
    if root.is_symlink():
        raise ValueError("NATIVE_ESCROW_SYMLINK")
    if stage not in ("decision","outcome") or len(intent_digest)!=64 or any(
        c not in "0123456789abcdef" for c in intent_digest):
        raise ValueError("NATIVE_ESCROW_PATH_ID")
    path=root/("original-"+stage+"-"+intent_digest+".json")
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        st=os.fstat(fd)
        if (not stat.S_ISREG(st.st_mode) or st.st_uid!=os.getuid()
                or stat.S_IMODE(st.st_mode)!=0o600 or st.st_size>LIMIT):
            raise ValueError("NATIVE_ESCROW_ARCHIVE_NOT_PRIVATE")
        raw=os.read(fd,LIMIT+1)
    finally:os.close(fd)
    if not raw.endswith(b"\n"):
        raise ValueError("NATIVE_ESCROW_ARCHIVE_INCOMPLETE")
    try:request=json.loads(raw)
    except (TypeError,ValueError) as err:
        raise ValueError("NATIVE_ESCROW_ARCHIVE_JSON") from err
    if canonical(request)+b"\n"!=raw:
        raise ValueError("NATIVE_ESCROW_ARCHIVE_NOT_CANONICAL")
    result=validate_complete(request)
    if result["stage"]!=stage or result["original_intent_digest"]!=intent_digest:
        raise ValueError("NATIVE_ESCROW_ARCHIVE_IDENTITY_CHANGED")
    return {**result,"archive_file_sha256":sha(raw),"archive_basename":path.name}
