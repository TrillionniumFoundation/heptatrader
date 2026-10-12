"""Fail-closed real-clock admission for expired cross-host signed test evidence.

Read only the original real-source TEST ledger and independently copied
Witness; never reauthorize historical evidence by backdating a caller clock.
No broker, signer, native Writer mutations or production root enrollment.
"""
from __future__ import annotations

import argparse
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import time

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from hepta_decision_attribution import read_private,strict
from .original_policy_audit import write_once
from .real_source_native_signature_verify import verify as verify_public

SCHEMA="heptatrader.train-v2-real-source-native-copy-recovery-admission.v1"
DOMAIN=b"hepta.learning-ledger.signed-evidence.v1"
ACK="ACKNOWLEDGED_NO_REAPPEND_REQUIRED"
REVIEW="REQUIRES_EXACT_VALID_SIGNATURE_OWNER_REVIEW_NO_AUTO_REPLAY"
HOLD="HOLD_EXPIRED_SIGNATURE_REQUIRES_INDEPENDENT_AUTHORIZED_RECOVERY_PROOF"


def sha(raw:bytes)->str:
    return hashlib.sha256(raw).hexdigest()


def own(path:Path,max_size:int=2*1024*1024)->bytes:
    if not path.is_absolute() or path.is_symlink() or path.parent.is_symlink():
        raise ValueError("REPLAY_FILE_PATH_INVALID")
    return read_private(path,max_size)


def document(path:Path)->dict:
    return strict(own(path,262144))


def parsed_id(buf:bytes,pos:int)->tuple[str,int]:
    if len(buf)-pos<8:
        raise ValueError("SIGNED_ID_TRUNCATED")
    length=int.from_bytes(buf[pos:pos+8],"big")
    pos+=8
    if not 1<=length<=128 or pos+length>len(buf):
        raise ValueError("SIGNED_ID_OUT_OF_RANGE")
    return buf[pos:pos+length].decode("ascii"),pos+length


def signed_native_times(entry:dict,public_hex:str,role:str)->dict:
    if role not in ("generator","observer"):
        raise ValueError("PRODUCER_ROLE_INVALID")
    raw=bytes.fromhex(entry["public_message_hex"])
    signature=bytes.fromhex(entry["signature_hex"])
    if not raw.startswith(DOMAIN) or len(raw)>8192 or len(signature)!=64:
        raise ValueError("NONCANONICAL_SIGNED_EVIDENCE")
    pos=len(DOMAIN)
    evidence,pos=parsed_id(raw,pos)
    principal,pos=parsed_id(raw,pos)
    if pos>=len(raw):
        raise ValueError("SIGNED_ORIGINAL_ROLE_TAG_MISSING")
    if (not evidence.startswith("lab.originalmarket.")
            or principal!="lab."+role
            or raw[pos]!=(0 if role=="generator" else 1)):
        raise ValueError("NATIVE_SIGNER_IDENTITY_CHANGED")
    pos+=1
    if len(raw)!=pos+96+24+32:
        raise ValueError("SIGNED_EVIDENCE_TRAILER_LENGTH")
    trust=raw[pos:pos+32].hex();pos+=32
    scope=raw[pos:pos+32].hex();pos+=32
    objective=raw[pos:pos+32].hex();pos+=32
    epoch=int.from_bytes(raw[pos:pos+8],"big");pos+=8
    issued=int.from_bytes(raw[pos:pos+8],"big");pos+=8
    expires=int.from_bytes(raw[pos:pos+8],"big");pos+=8
    payload=raw[pos:pos+32].hex();pos+=32
    if pos!=len(raw) or epoch!=19 or not 0<issued<expires:
        raise ValueError("SIGNING_EPOCH_OR_TIME_INVALID")
    try:
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_hex)).verify(signature,raw)
    except Exception as exc:
        raise ValueError("SIGNED_ORIGINAL_PUBLIC_SIGNATURE_INVALID") from exc
    return {
        "test_role":role,"signed_evidence_id":evidence,
        "native_authority_epoch":epoch,
        "native_issued_at_ms":issued,"native_expires_at_ms":expires,
        "original_signed_trust_digest":trust,
        "original_signed_scope_digest":scope,
        "original_signed_objective_digest":objective,
        "original_signed_payload_digest":payload,
        "original_message_sha256":sha(raw),
        "independent_ed25519_signature_verified":True,
    }


def classify(witness_count:int,real_now:int,observer_expiry:int)->str:
    if (type(witness_count) is not int or witness_count not in (1,2)
            or type(real_now) is not int or real_now<=0
            or type(observer_expiry) is not int or observer_expiry<=0):
        raise ValueError("REPLAY_CLOCK_AND_FRONTIER_TYPE")
    if witness_count==2:
        return ACK
    if real_now>observer_expiry:
        return HOLD
    return REVIEW


def assess(original:Path,copy:Path)->dict:
    original,copy=Path(original),Path(copy)
    copies=original.parent/"train-v2-native-replay-20261012"
    source=original/"native-lab-v1"
    if (not original.is_absolute() or original.is_symlink()
            or not copy.is_absolute() or copy.is_symlink()
            or copy.parent!=copies or not copy.is_dir()
            or not copies.is_dir() or copies.is_symlink()
            or not source.is_dir() or source.is_symlink()
            or any(x.stat().st_uid!=os.geteuid() or
                   stat.S_IMODE(x.stat().st_mode)!=0o700
                   for x in (original,copies,copy,source))):
        raise ValueError("ORIGINAL_TEST_ONLY_REPLAY_SCOPE_INVALID")
    signed=verify_public(original)
    if signed["public_only_ed25519_messages_verified"]!=4:
        raise ValueError("SIGNED_ORIGINAL_FOUR_ROLES_NOT_VERIFIED")
    roles=document(original/"public_roles.json")["roles"]
    init=document(original/"native-init-crosshost-public-signatures.json")
    end=document(original/"native-finish-crosshost-public-signatures.json")
    generator=next(x for x in init["signatures"] if x["role"]=="generator")
    observer=next(x for x in end["signatures"] if x["role"]=="observer")
    actual_gen=signed_native_times(generator,roles["generator"]["public_key_hex"],"generator")
    actual_obs=signed_native_times(observer,roles["observer"]["public_key_hex"],"observer")
    if actual_gen["native_issued_at_ms"]>=actual_obs["native_issued_at_ms"]:
        raise ValueError("GENERATOR_OBSERVER_ORIGINAL_ORDER_INVALID")
    for key in ("original_signed_trust_digest", "original_signed_scope_digest",
                "original_signed_objective_digest", "native_authority_epoch"):
        if actual_gen[key] != actual_obs[key]:
            raise ValueError("SOURCE_TIME_NATIVE_SIGNATURES_DO_NOT_SHARE_TRUST_CONTEXT")
    expected_ledger=own(source/"ledger")
    expected_witness=own(source/"witness")
    ledger=own(copy/"ledger")
    witness=own(copy/"witness")
    if (len(expected_ledger)!=1224 or len(expected_witness)!=248
            or ledger!=expected_ledger
            or witness not in (expected_witness,expected_witness[:160])):
        raise ValueError("TEST_REPLAY_COPY_OR_ORIGINAL_WITNESS_NOT_SAME_SOURCE")
    witness_count=2 if witness==expected_witness else 1
    now_ms=time.time_ns()//1000000
    decision=classify(witness_count,now_ms,actual_obs["native_expires_at_ms"])
    return {
        "schema":SCHEMA,
        "validated_at_utc":datetime.now(timezone.utc).isoformat(),
        "actual_original_IB_PAPER_source_lab_only":True,
        "original_real_native_ledger_copy_sha256":sha(ledger),
        "actual_copy_matches_original_read_only_native_ledger":True,
        "original_witness_prefix_or_full_exact_match":True,
        "independently_public_signed_Generator_epoch":actual_gen,
        "independently_public_signed_Observer_epoch":actual_obs,
        "real_wall_clock_ms":now_ms,
        "native_witness_count":witness_count,
        "recovery_admission":decision,
        "timestamp_for_admission_is_real_current_wall_clock":True,
        "wall_clock_is_independently_secure_hardware_attested":False,
        "historical_backdating_as_production_authority_allowed":False,
        "production_history_repair_escalation_required":decision==HOLD,
        "product_controller_root_and_signature_enrolled":False,
        "automatic_write_or_witness_ack_performed":False,
        "original_trade_after_cost_PnL_proven":False,
        "original_opaque_behavior_policy_full_support_and_propensity":False,
        "model_training_or_trading_authorized":False,
    }


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--original",type=Path,required=True)
    p.add_argument("--copy",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True)
    a=p.parse_args();os.umask(0o077)
    r=assess(a.original,a.copy)
    digest=write_once(a.output,r)
    print(json.dumps({
        "decision":r["recovery_admission"],
        "ack_sequence":r["native_witness_count"],
        "signer_expired_at_actual_clock":
            r["real_wall_clock_ms"]>
            r["independently_public_signed_Observer_epoch"]["native_expires_at_ms"],
        "no_automatic_replay":True,"product_authority":False,
        "receipt_sha256":digest,
    },sort_keys=True))


if __name__=="__main__":main()
