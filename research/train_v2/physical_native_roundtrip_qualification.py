"""Physical original signed byte readback -> real canonical Rust typed parser.

TEST ONLY. Old archives stay unchanged and are intentionally rejected by the
new semantic check. Only NEW synthetic actor messages are issued in memory.
No product root, LedgerWriter, Witness ACK, broker request, model or trade.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess

from . import physical_full_escrow_orchestrate as flow
from .physical_full_escrow_receiver import SCHEMA as REQUEST_SCHEMA

REPORT_SCHEMA="heptatrader.train-v2-two-physical-original-native-rust-roundtrip.v1"
BIN_SCHEMA="heptatrader.train-v2-original-native-reparse-request.v1"
EXPECTED_OLD_FAILURE="ORIGINAL_TEST_DIGEST_SEMANTICS_DRIFT"

def sha(raw):return hashlib.sha256(raw).hexdigest()
def strict_private(file,limit):
    p=Path(file)
    if p.is_symlink() or not p.is_file():
        raise ValueError("NATIVE_PHYSICAL_INPUT_NOT_PRIVATE")
    st=p.stat()
    if (st.st_uid!=os.geteuid() or stat.S_IMODE(st.st_mode)!=0o600
            or st.st_size>limit or st.st_size<2):
        raise ValueError("NATIVE_PHYSICAL_INPUT_MODE")
    raw=p.read_bytes()
    if len(raw)!=st.st_size:return (_ for _ in ()).throw(ValueError("NATIVE_INPUT_CHANGED"))
    return raw

def rust_verify(binary,stage,host,original,receipt,*,want_success):
    packet={
        "schema":BIN_SCHEMA,
        "stage":stage,"host":host,
        "original_hex":original.hex(),
        "receipt_hex":receipt.hex(),
        "expected_original_sha256":sha(original),
        "expected_receipt_sha256":sha(receipt),
    }
    result=subprocess.run([str(binary)],input=json.dumps(packet,sort_keys=True),
             capture_output=True,text=True,timeout=15,check=False)
    if want_success:
        if result.returncode or not result.stdout:
            raise ValueError("ORIGINAL_NATIVE_RUST_REPARSE_REJECTED:"+result.stderr[-280:])
        doc=json.loads(result.stdout)
        if (doc.get("schema")!="heptatrader.train-v2-original-rust-typed-physical-reparse.v1"
                or doc.get("original_archive_sha256")!=sha(original)
                or doc.get("host_receipt_sha256")!=sha(receipt)
                or doc.get("physical_host")!=host
                or doc.get("stage")!=stage
                or doc.get("status")!="TEST_ONLY_ORIGINAL_RUST_TYPED_BYTES_AND_TWO_SIGNATURES_VERIFIED"
                or any(doc.get(k) is not True for k in (
                    "native_source_role_and_trust_scope_objective_order_verified",
                    "original_signed_evidence_rust_roundtrip_exact",
                    "original_actor_ed25519_verified",
                    "separate_physical_host_receipt_ed25519_verified"))
                or any(doc.get(k) is not False for k in (
                    "native_ledger_writer_append_performed",
                    "native_witness_acknowledged","product_root_trust_enrolled",
                    "product_actor_or_model_data_present",
                    "independent_product_recovery_grant_present",
                    "model_promoted"))
                or doc.get("broker_orders_sent")!=0):
            raise ValueError("ORIGINAL_NATIVE_RUST_REPARSE_FALSE_SUCCESS")
        return doc
    if result.returncode == 0:
        raise ValueError("NATIVE_REPARSE_ACCEPTED_FORGED_OR_OLD_BYTES")
    if not result.stderr.startswith("REJECTED:"):
        raise ValueError("NATIVE_REPARSE_UNCONTROLLED_FAILURE")
    return result.stderr.strip()[:170]

def mutation_checks(binary,stage,host,original,receipt):
    request=json.loads(original)
    remote_receipt=json.loads(receipt)
    cases=[]
    # No remote writes or signatures are created in these negative trials.
    def must_reject(label,raw,ack,actual_stage=stage,actual_host=host):
        result=rust_verify(binary,actual_stage,actual_host,raw,ack,want_success=False)
        cases.append({"case":label,"rejection":result.splitlines()[0]})
    must_reject("wrong_host",original,receipt,stage,
                "MacBook-Pro.local" if stage=="decision" else "qian-ThinkPad-X230")
    must_reject("cross_stage",original,receipt,
                "outcome" if stage=="decision" else "decision")
    corrupt_request=dict(request)
    corrupt_request["trade_authority"]=True
    must_reject("forged_trade_authority",
                flow.canonical(corrupt_request)+b"\n",receipt)
    altered=dict(request)
    val=bytearray.fromhex(altered["detached_signature_hex"])
    val[0]^=1
    altered["detached_signature_hex"]=val.hex()
    altered["original_intent"]=dict(altered["original_intent"])
    altered["original_intent"]["original_detached_signature_sha256"]=sha(bytes(val))
    must_reject("rehashed_actor_signature_tamper",
                flow.canonical(altered)+b"\n",receipt)
    altered=dict(remote_receipt)
    sig=bytearray.fromhex(altered["receipt_signature_hex"])
    sig[-1]^=1
    altered["receipt_signature_hex"]=sig.hex()
    must_reject("host_receipt_signature_tamper",
                original,flow.canonical(altered)+b"\n")
    altered=dict(remote_receipt)
    altered["native_original_message_sha256"]="0"*64
    must_reject("host_receipt_wrong_original_message",
                original,flow.canonical(altered)+b"\n")
    altered=dict(request)
    altered["signed_payload_hex"]="01"
    must_reject("payload_changed_without_matching_signing",
                flow.canonical(altered)+b"\n",receipt)
    must_reject("noncanonical_json",
                json.dumps(request,sort_keys=True,indent=2).encode()+b"\n",receipt)
    return cases

def run(repo,receiver_file,old_report_file,native_binary,native_receipt_file):
    repo,receiver_file,old_report_file,native_binary,native_receipt_file=map(
        Path,(repo,receiver_file,old_report_file,native_binary,native_receipt_file))
    if native_binary.is_symlink() or not native_binary.is_file():
        raise ValueError("NATIVE_ORIGINAL_RUST_BINARY_MISSING")
    native_receipt=json.loads(strict_private(native_receipt_file,150000))
    if (native_receipt.get("schema")!="heptatrader.train-v2-native-rust-roundtrip-build.v1"
            or native_receipt.get("canonical_source_commit")!=
                 "78fdb0cf8537e3a84fc6e0a849707559c80881e8"
            or native_receipt.get("native_bridge_binary_sha256")!=sha(native_binary.read_bytes())
            or native_receipt.get("bridge_source_sha256") != sha(
                (repo/"research/train_v2/physical_native_reparse.rs").read_bytes())
            or native_receipt.get("canonical_ledger_writer_called") is not False):
        raise ValueError("NATIVE_RUST_BINARY_NOT_SOURCE_PINNED")
    old=json.loads(strict_private(old_report_file,16384))
    if (old.get("schema")!="heptatrader.train-v2-two-physical-host-fullbytes-test-only.v1"
            or old.get("future_product_native_ledger_writer_executed") is not False
            or len(old.get("hosts",[]))!=2):
        raise ValueError("PRIOR_PHYSICAL_REPORT_SCOPE")
    expected={h["stage"]:h for h in old["hosts"]}
    if set(expected)!={"decision","outcome"}:raise ValueError("PRIOR_PHYSICAL_HOST_POPULATION")
    receiver=strict_private(receiver_file,50000) if False else receiver_file.read_bytes()
    if len(receiver)>128000:raise ValueError("PHYSICAL_RECEIVER_SOURCE_BOUND")
    if receiver != (repo/"research/train_v2/physical_full_escrow_receiver.py").read_bytes():
        raise ValueError("PHYSICAL_RECEIVER_CHANGED_BETWEEN_READS")
    results=[]
    for stage,(alias,host) in flow.HOSTS.items():
        pinned=expected[stage]
        if pinned["host"]!=host:raise ValueError("PRIOR_SOURCE_HOST_IDENTITY")
        # Fetch the EXACT previous physical archive without changing it.
        old_copy=flow.remote(alias,receiver,"read",stage,
                             pinned["original_complete_bytes_sha256"])
        old_raw=bytes.fromhex(old_copy["original_hex"])
        old_ack=bytes.fromhex(old_copy["receipt_hex"])
        # Verification of existing physical host receipt in Python is separate
        # from the native typed check; both are mandatory.
        flow.verify_host_readback(stage,old_raw,old_copy,host)
        if (sha(old_raw)!=pinned["original_complete_bytes_sha256"]
                or sha(old_ack)!=pinned["receipt_sha256"]):
            raise ValueError("EXISTING_PHYSICAL_ARCHIVE_DRIFT")
        rejected=rust_verify(native_binary,stage,host,old_raw,old_ack,want_success=False)
        if EXPECTED_OLD_FAILURE not in rejected:
            raise ValueError("OLDER_NATIVE_SEMANTIC_DRIFT_NOT_DETECTED:"+rejected)
        # NEW SYNTHETIC stage bytes with corrected canonical TRUST/SCOPE/OBJECTIVE.
        fresh=flow.new_signed_original(stage)
        if json.loads(fresh).get("schema")!=REQUEST_SCHEMA:
            raise ValueError("FRESH_TEST_ONLY_PROFILE_CHANGED")
        key=sha(fresh)
        ack=flow.remote(alias,receiver,"seal",stage,key,fresh)
        if (ack.get("status")!="NEW_COMPLETE_BYTES_SEALED_AND_READBACK"
                or ack.get("remote_fsync") is not True or ack.get("original_sha256")!=key):
            raise ValueError("NEW_PHYSICAL_SAME_ID_ALREADY_USED_OR_NOT_FSYNCED")
        a=flow.remote(alias,receiver,"read",stage,key)
        verified=flow.verify_host_readback(stage,fresh,a,host)
        current_original=bytes.fromhex(a["original_hex"])
        current_receipt=bytes.fromhex(a["receipt_hex"])
        if verified["receipt_sha256"]!=ack["receipt_sha256"]:
            raise ValueError("NEW_REMOTE_ORIGINAL_RECEIPT_MISMATCH")
        typed=rust_verify(native_binary,stage,host,current_original,
                           current_receipt,want_success=True)
        # A dropped ACK must not create another signed original/receipt.
        retry=flow.remote(alias,receiver,"seal",stage,key,fresh)
        if (retry.get("status")!="IDEMPOTENT_EXACT_REMOTE_READBACK"
                or retry.get("receipt_sha256")!=ack.get("receipt_sha256")):
            raise ValueError("LOST_ACK_RETRY_NOT_IDEMPOTENT")
        back=flow.remote(alias,receiver,"read",stage,key)
        if (back.get("original_hex")!=a.get("original_hex")
                or back.get("receipt_hex")!=a.get("receipt_hex")):
            raise ValueError("PHYSICAL_ARCHIVE_CHANGED_AFTER_RETRY")
        attacks=mutation_checks(native_binary,stage,host,
                                  current_original,current_receipt)
        results.append({
            "stage":stage,"physical_host":host,
            "previous_test_archive_sha256":sha(old_raw),
            "previous_native_rejection":rejected,
            "new_archive_sha256":sha(current_original),
            "new_host_receipt_sha256":sha(current_receipt),
            "remote_store_fsync_and_separate_ssh_readback":True,
            "native_original_rust_typed_roundtrip":True,
            "native_actor_and_remote_host_crypto_signatures_verified":True,
            "idempotent_exact_retry_no_new_source":True,
            "negative_native_check_count":len(attacks),
            "native_attack_refusals":attacks,
            "new_native_signature_is_product_authority":False,
            "new_ledger_writer_record_created":False,
            "native":typed,
        })
    if results[0]["new_host_receipt_sha256"]==results[1]["new_host_receipt_sha256"]:
        raise ValueError("UNEXPECTED_EQUAL_PHYSICAL_ESCROW_SIGNED_ACK")
    return {
        "schema":REPORT_SCHEMA,"canonical_source_sha":"78fdb0cf8537e3a84fc6e0a849707559c80881e8",
        "native_binary_sha256":sha(native_binary.read_bytes()),
        "native_source_build_receipt_sha256":sha(native_receipt_file.read_bytes()),
        "receiver_source_sha256":sha(receiver),"source_python_sha256":{
          x:sha((repo/"research/train_v2"/x).read_bytes())
          for x in ("physical_full_escrow_orchestrate.py",
                    "physical_full_escrow_receiver.py",
                    "physical_native_reparse.rs",
                    "physical_native_roundtrip_qualification.py")},
        "host_results":results,
        "prior_three_physical_escrows_unchanged":True,
        "actual_original_broker_market_data_or_order_used":False,
        "physical_signers_generated_in_memory_only":True,
        "synthetic_payload_is_not_canonical_ProductionDecisionV2":True,
        "canonical_root_and_owner_enrolled":False,
        "real_LedgerWriter_append_executed":False,
        "real_Witness_ACK_performed":False,
        "actual_BROKER_request_or_order_created":False,
        "automatic_model_training":False,
        "model_promotion":False,
        "financial_alpha_established":False,
        "product_recovery_authority_qualified":False,
        "hardware_power_loss_qualified":False,
        "product_qualification":"HOLD_TEST_ONLY_NATIVE_TYPED_TWO_HOST_ESCROW_NO_PRODUCT_TRUST",
    }

def main():
    p=argparse.ArgumentParser()
    for key in ("repo","receiver","old-report","native-binary",
                "native-build-receipt","output"):
        p.add_argument("--"+key,required=True,type=Path)
    a=p.parse_args()
    os.umask(0o077)
    report=run(a.repo,a.receiver,a.old_report,a.native_binary,a.native_build_receipt)
    path=a.output
    if path.exists() or path.is_symlink():raise ValueError("REPORT_CREATE_ONLY")
    if path.parent.stat().st_uid!=os.geteuid() or path.parent.stat().st_mode&0o077:
        raise ValueError("PRIVATE_REPORT_DIRECTORY_REQUIRED")
    raw=flow.canonical(report)+b"\n"
    f=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(f,"wb") as stream:
        stream.write(raw);stream.flush();os.fsync(stream.fileno())
    dirfd=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:os.fsync(dirfd)
    finally:os.close(dirfd)
    print(json.dumps({
        "qualification":report["product_qualification"],
        "physical_hosts":[x["physical_host"] for x in report["host_results"]],
        "rust_typed_verification":True,
        "prior_wrong_native_semantic_archives_rejected":True,
        "test_only_source_originals_created":2,
        "native_negative_challenges":sum(x["negative_native_check_count"]
                                          for x in report["host_results"]),
        "native_ledger_records_appended":0,
        "broker_orders_sent":0,
        "report_sha256":sha(raw),
    },sort_keys=True))

if __name__=="__main__":main()
