"""Public-only, exact native Ed25519 signature and true timeline verification.

This is test-only ROOT, Mac Generator, X230 Observer, qian Evaluator,
not production-independent host enrollment, actor policy provenance or
economic execution PnL.
"""
from __future__ import annotations

from datetime import datetime, timezone
import argparse
import hashlib
import json
import os
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.exceptions import InvalidSignature

from hepta_decision_attribution import read_private,strict
from .crosshost_test_signer import _scope
from .original_policy_audit import write_once

SCHEMA="heptatrader.train-v2-public-only-true-source-time-native-crosshost-test-signatures.v1"
ROLE_ORDER={"init":("root","generator"),"finish":("observer","evaluator")}
PHYSICAL_HOSTS={"root":"qian-qi","generator":"MacBook-Pro.local",
                "observer":"qian-ThinkPad-X230","evaluator":"qian-qi"}
MAX_SIGNED_MESSAGE=8192


def sha(b:bytes)->str:return hashlib.sha256(b).hexdigest()


def _read(root:Path,name:str)->tuple[dict,bytes]:
    b=read_private(root/name,262144)
    return strict(b),b


def _utc_ms(s:str)->int:
    d=datetime.fromisoformat(s)
    if d.tzinfo is None or d.utcoffset() is None:
        raise ValueError("SIGNED_NATIVE_REMOTE_SIGNER_UNTRUSTED_TIMEZONE")
    return int(d.astimezone(timezone.utc).timestamp()*1000)


def verify(root:Path)->dict:
    root=Path(root)
    if not root.is_absolute() or root.is_symlink():
        raise ValueError("PUBLIC_NATIVE_SIGNATURE_EVIDENCE_DIRECTORY_UNSAFE")
    manifest,_=_read(root,"public_roles.json")
    if (manifest.get("schema")!="hepta-train-v2-crosshost-native-signing-pubs.v1"
            or manifest.get("purpose")!="test-only-no-production"):
        raise ValueError("PUBLIC_NATIVE_TEST_ROOT_NOT_PINNED")
    pins=manifest.get("roles")
    for role,host in PHYSICAL_HOSTS.items():
        key=pins[role]
        if (key["actual_signing_host"]!=host or key["test_only"] is not True
                or key["private_key_exported"] is not False):
            raise ValueError("NATIVE_ORIGINAL_TEST_ROLE_HOST_CHANGED")
    decision,_=_read(root,"real-shadow-decision-v1.json")
    packet,packet_bytes=_read(root,"native-real-quote-packet.json")
    observed,_=_read(root,"real-five-minute-midprice-outcome.json")
    native,raw_native=_read(root,"native-real-price-observer-packet.json")
    initial,_=_read(root/"native-lab-v1","native-source-decision-ack.json")
    finished,_=_read(root/"native-lab-v1","native-price-outcome-and-dataset-test-ack.json")
    if (initial["native_witness_ack_sequence"]!=1 or
            finished["native_durable_witness_sequence"]!=2 or
            not initial["native_acknowledged_at_ms"]<observed["outcome_due_ms"] or
            packet["selected_propensity_q32_raw"]!=decision["selected_propensity_q32_raw"] or
            packet["shadow_decision_sha256"]!=sha(
                (root/"real-shadow-decision-v1.json").read_bytes()) or
            observed["actual_endpoint_or_censoring_status"]!="OBSERVED" or
            native["market_midprice_change_bps"]!=observed["actual_market_mid_price_return_bps"] or
            native["source_shadow_packet_sha256"]!=sha(packet_bytes) or
            native["research_only"] is not True or
            finished["native_V3_dataset_is_product_market_eligible"] is not False or
            finished["actual_order_execution_cost_and_PnL"] is not False):
        raise ValueError("NATIVE_SOURCE_ACTUAL_LEDGER_TIME_OR_SCOPE_WRONG")
    due=observed["outcome_due_ms"]
    signatures=[]
    for phase in ("init","finish"):
        attest,_=_read(root,f"native-{phase}-crosshost-public-signatures.json")
        expected=ROLE_ORDER[phase]
        entries=attest.get("signatures")
        if (attest.get("schema")!="heptatrader.real-source-native-multihost-test-attestations.v1"
                or attest.get("stage")!=phase
                or not isinstance(entries,list)
                or tuple(x.get("role") for x in entries)!=expected
                or attest.get("no_private_signer_export") is not True
                or attest.get("production_authorization") is not False
                or attest["native"]["product_authority"] is not False):
            raise ValueError("NATIVE_SIGNED_STAGE_CONTEXT_CHANGED")
        for entry in entries:
            role=entry["role"]
            if entry["signing_host"]!=PHYSICAL_HOSTS[role]:
                raise ValueError("NATIVE_SIGNING_MACHINE_CHANGED")
            msg=bytes.fromhex(entry["public_message_hex"])
            sig=bytes.fromhex(entry["signature_hex"])
            if (not 1<len(msg)<=MAX_SIGNED_MESSAGE
                    or len(sig)!=64
                    or sha(msg)!=entry["message_sha256"]):
                raise ValueError("NATIVE_SIGNED_MESSAGE_LENGTH_OR_HASH")
            _scope(role,msg)
            try:
                Ed25519PublicKey.from_public_bytes(
                    bytes.fromhex(pins[role]["public_key_hex"])).verify(sig,msg)
            except (InvalidSignature,ValueError) as exc:
                raise ValueError("NATIVE_ORIGINAL_CROSSHOST_ED25519_MISMATCH") from exc
            signed_at=_utc_ms(entry["signed_at_utc"])
            if phase=="init":
                if not decision["decision_at_ms"]<=signed_at<due:
                    raise ValueError("NATIVE_GENERATOR_SIGNED_AFTER_OUTCOME")
            else:
                if not due<=signed_at:
                    raise ValueError("NATIVE_OBSERVER_SIGNED_BEFORE_OUTCOME")
            signatures.append({
                "role":role,"signing_host":entry["signing_host"],
                "message_sha256":sha(msg),"signature_sha256":sha(sig),
                "signed_at_ms":signed_at,
            })
    return {
        "schema":SCHEMA,
        "public_only_ed25519_messages_verified":len(signatures),
        "actual_separate_host_roles":PHYSICAL_HOSTS,
        "exact_original_native_signing_domain_and_test_role_scope":True,
        "native_Mac_generator_decision_actual_ack_before_horizon":True,
        "native_X230_observer_price_result_signed_after_horizon":True,
        "native_witness_ack_sequence":2,
        "verified_signature_events":signatures,
        "precommitted_shadow_source_real_original_IB_PAPER":True,
        "market_price_observed_bps":observed["actual_market_mid_price_return_bps"],
        "original_model_actual_action_probabilities_established":False,
        "x230_observer_independent_broker_subscription":False,
        "product_root_organization_independence_authenticated":False,
        "native_V3_test_data_product_eligible":False,
        "actual_order_fill_or_net_trading_PnL":False,
        "new_broker_orders_or_model_promotions":0,
        "trading_authority":False,
    }


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--root",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True)
    args=p.parse_args();os.umask(0o077)
    result=verify(args.root)
    seal=write_once(args.output,result)
    print(json.dumps({
        "signed_original_native_test_events_verified":
            result["public_only_ed25519_messages_verified"],
        "native_witness_ack_sequence":result["native_witness_ack_sequence"],
        "product_authority":False,"sealed_sha256":seal,
    },sort_keys=True))


if __name__=="__main__":
    main()
