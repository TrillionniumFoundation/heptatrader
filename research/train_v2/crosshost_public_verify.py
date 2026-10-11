"""Independent offline Ed25519 verification of TEST-ONLY crosshost native facts.

Reads public signatures after private keys are destroyed. Verifies exact native
signed bytes and physical-host metadata from the isolated SSH ceremony.
Neither a host hardware attestation nor an authorized product trust root.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.exceptions import InvalidSignature

from hepta_decision_attribution import read_private, strict
from .crosshost_test_signer import _scope
from .original_policy_audit import write_once

SCHEMA="heptatrader.train-v2-crosshost-test-only-public-verification.v1"
EXPECTED=("root","generator","generator","observer","evaluator","privacy")
TEST_HOSTS={"root":"qian-qi",
            "generator":"MacBook-Pro.local",
            "observer":"qian-ThinkPad-X230",
            "evaluator":"qian-qi",
            "privacy":"qian-qi"}


def verify(manifest:dict,manifest_raw:bytes,proof:dict)->dict:
    if (manifest.get("schema")!="hepta-train-v2-crosshost-native-signing-pubs.v1"
            or manifest.get("purpose")!="test-only-no-production"
            or proof.get("schema")!="heptatrader.train-v2-true-crosshost-test-only-native-signature-lab.v1"
            or proof.get("public_manifest_sha256")!=hashlib.sha256(manifest_raw).hexdigest()
            or proof.get("all_test_only") is not True
            or proof.get("signer_private_keys_ever_exported") is not False):
        raise ValueError("PUBLIC_SIGNED_TRUST_SOURCE_INVALID")
    roles=manifest.get("roles")
    if not isinstance(roles,dict) or set(roles)!=set(TEST_HOSTS):
        raise ValueError("ROLE_KEYS_NOT_COMPLETE")
    keys={}
    hosts={}
    for role,host in TEST_HOSTS.items():
        r=roles[role]
        if (r.get("role")!=role or not r.get("test_only")
                or r.get("private_key_exported") is not False
                or r.get("actual_signing_host")!=host):
            raise ValueError("PUBLIC_ROLE_HOST_SCOPE_INVALID")
        raw=bytes.fromhex(r["public_key_hex"])
        if len(raw)!=32 or raw==bytes(32):
            raise ValueError("PUBLIC_KEY_BYTES_INVALID")
        keys[role]=raw
        hosts[role]=r["actual_signing_host"]
    if len(set(keys.values()))!=len(keys):
        raise ValueError("PUBLIC_ROLE_KEY_COLLISION")
    if len({hosts["root"],hosts["generator"],hosts["observer"]})!=3:
        raise ValueError("CROSS_MACHINE_TEST_CONTROL_NOT_SEPARATED")
    evidence=proof.get("signature_requests")
    if not isinstance(evidence,list) or tuple(x.get("role") for x in evidence)!=EXPECTED:
        raise ValueError("NATIVE_ROLE_SEQUENCE_UNVERIFIED")
    verified=[]
    for entry in evidence:
        role=entry["role"]
        if (entry.get("purpose")!="synthetic_native_lab_only"
                or entry.get("signing_key_transferred") is not False
                or entry.get("actual_signing_host")!=hosts[role]
                or entry.get("signature_cryptographically_verified_against_host_public_key")
                   is not True):
            raise ValueError("SIGNED_ROLE_SCOPE_UNTRUSTED")
        msg=bytes.fromhex(entry["native_signing_message_hex"])
        sig=bytes.fromhex(entry["signature_hex"])
        if (len(msg)>4096 or not msg or len(sig)!=64
                or hashlib.sha256(msg).hexdigest()!=entry["message_sha256"]
                or hashlib.sha256(sig).hexdigest()!=entry["signature_sha256"]):
            raise ValueError("CANONICAL_NATIVE_SIGNATURE_BINDING_CHANGED")
        _scope(role,msg)
        try:
            Ed25519PublicKey.from_public_bytes(keys[role]).verify(sig,msg)
        except (InvalidSignature,ValueError) as exc:
            raise ValueError("INDEPENDENT_OFFLINE_NATIVE_SIGNATURE_INVALID") from exc
        verified.append({"role":role,"signing_host":hosts[role],
                         "message_sha256":entry["message_sha256"],
                         "signature_sha256":entry["signature_sha256"]})
    n=proof.get("native_result")
    if (not isinstance(n,dict) or n.get("result")!="TEST_ONLY_NATIVE_PASSED"
            or n.get("native_durable_witness_frontier_records")!=3
            or n.get("native_signed_decisions")!=1
            or n.get("native_signed_terminal_outcomes")!=1
            or not n.get("native_v3_dataset_frozen")
            or not n.get("native_v3_revalidation_rejected_after_withdrawal")
            or not n.get("native_generator_evidence_corruption_rejected")
            or not n.get("native_wrong_role_outcome_rejected")
            or n.get("product_root_authority_enrolled") is not False
            or n.get("real_broker_evidence") is not False):
        raise ValueError("NATIVE_RESULTS_MISCLASSIFIED")
    return {
        "schema":SCHEMA,
        "test_only":True,
        "independent_offline_signature_verification_passed":True,
        "original_native_domain_and_test_role_scope_checked":True,
        "verified_public_ed25519_signatures":len(verified),
        "public_manifest_sha256":hashlib.sha256(manifest_raw).hexdigest(),
        "evidence":verified,
        "physical_hosts_declared_by_test_ssh_ceremony":hosts,
        "original_native_ledger_and_witness_exists_as_separate_artifacts":True,
        "machine_identity_cryptographically_attested":False,
        "production_controller_keys_enrolled":False,
        "true_original_trading_behavior_policy_probabilities":False,
        "real_quote_and_after_cost_pnl":False,
        "production_training_or_trading_authorized":False,
    }


def main()->None:
    p=argparse.ArgumentParser()
    p.add_argument("--public",type=Path,required=True)
    p.add_argument("--attestations",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True)
    a=p.parse_args()
    os.umask(0o077)
    pub=read_private(a.public,128*1024)
    attest=read_private(a.attestations,128*1024)
    result=verify(strict(pub),pub,strict(attest))
    sha=write_once(a.output,result)
    print(json.dumps({"schema":result["schema"],
        "verified_test_only_ed25519_signatures":
            result["verified_public_ed25519_signatures"],
        "product_authority":False,
        "receipt_sha256":sha},sort_keys=True))


if __name__=="__main__":
    main()
