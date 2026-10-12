"""READ-ONLY final Rust reparse of already escrowed physical test bytes.

Uses only SSH read mode on pinned X230/Mac. No new signatures or remote
archive writes. This binds the final rustfmt-reviewed native executable hash
to the exact previously recovered original role-signed bytes.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
from . import physical_full_escrow_orchestrate as flow
from .physical_native_roundtrip_qualification import (
    sha, strict_private, rust_verify, mutation_checks,
)

SCHEMA="heptatrader.train-v2-final-readonly-native-rust-recheck.v1"

def run(repo,receiver_file,prior_file,native_binary,native_receipt_file):
    repo,receiver_file,prior_file,native_binary,native_receipt_file=map(
        Path,(repo,receiver_file,prior_file,native_binary,native_receipt_file))
    original= strict_private(prior_file,150000)
    previous=json.loads(original)
    if (previous.get("schema")!=
            "heptatrader.train-v2-two-physical-original-native-rust-roundtrip.v1"
            or previous.get("real_LedgerWriter_append_executed") is not False
            or len(previous.get("host_results",[]))!=2):
        raise ValueError("NATIVE_FINAL_PRIOR_TEST_PROOF_MISMATCH")
    build=json.loads(strict_private(native_receipt_file,150000))
    if (build.get("canonical_source_commit")!=
             "78fdb0cf8537e3a84fc6e0a849707559c80881e8"
            or sha(native_binary.read_bytes())!=
             build.get("native_bridge_binary_sha256")
            or sha((repo/"research/train_v2/physical_native_reparse.rs").read_bytes())!=
             build.get("bridge_source_sha256")
            or build.get("canonical_ledger_writer_called") is not False):
        raise ValueError("NATIVE_FINAL_BINARY_SOURCE_DRIFT")
    raw=receiver_file.read_bytes()
    if raw!=(repo/"research/train_v2/physical_full_escrow_receiver.py").read_bytes():
        raise ValueError("ORIGINAL_PHYSICAL_RECEIVER_CHANGED")
    matched={p["stage"]:p for p in previous["host_results"]}
    if set(matched)!={"decision","outcome"}:
        raise ValueError("SOURCE_PHYSICAL_POPULATION")
    results=[]
    for stage,(alias,host) in flow.HOSTS.items():
        expected=matched[stage]
        if expected["physical_host"]!=host:
            raise ValueError("PHYSICAL_HOST_IDENTITY_CHANGED")
        # Both read calls are read-only on existing physical 0600 archives.
        old=flow.remote(alias,raw,"read",stage,
                        expected["previous_test_archive_sha256"])
        old_body=bytes.fromhex(old["original_hex"])
        old_receipt=bytes.fromhex(old["receipt_hex"])
        flow.verify_host_readback(stage,old_body,old,host)
        if sha(old_body)!=expected["previous_test_archive_sha256"]:
            raise ValueError("PREVIOUS_ARCHIVE_CHANGED")
        prior_rejection=rust_verify(native_binary,stage,host,
                                   old_body,old_receipt,want_success=False)
        if "ORIGINAL_TEST_DIGEST_SEMANTICS_DRIFT" not in prior_rejection:
            raise ValueError("ORIGINAL_ARCHIVE_NOT_REJECTED_BY_FINAL_RUST")
        new=flow.remote(alias,raw,"read",stage,
                        expected["new_archive_sha256"])
        new_body=bytes.fromhex(new["original_hex"])
        new_receipt=bytes.fromhex(new["receipt_hex"])
        flow.verify_host_readback(stage,new_body,new,host)
        if (sha(new_body)!=expected["new_archive_sha256"]
                or sha(new_receipt)!=expected["new_host_receipt_sha256"]):
            raise ValueError("NEW_NATIVE_ARCHIVE_CHANGED")
        native=rust_verify(native_binary,stage,host,
                           new_body,new_receipt,want_success=True)
        attacks=mutation_checks(native_binary,stage,host,new_body,new_receipt)
        reread=flow.remote(alias,raw,"read",stage,
                           expected["new_archive_sha256"])
        if (reread["original_hex"]!=new["original_hex"]
                or reread["receipt_hex"]!=new["receipt_hex"]):
            raise ValueError("PHYSICAL_FINAL_SEPARATE_READ_CHANGED")
        results.append({
            "physical_host":host,"original_stage":stage,
            "old_wrong_order_archive_sha256":sha(old_body),
            "old_native_semantic_rejection":prior_rejection,
            "new_archive_sha256":sha(new_body),
            "new_host_receipt_sha256":sha(new_receipt),
            "native_exact_original_rust_signing_bytes_verified":True,
            "actor_and_remote_host_ed25519_verified":True,
            "different_ssh_sessions_recovered_identical_bytes":True,
            "negative_native_challenges_refused":len(attacks),
            "native_summary":native,
        })
    return {
        "schema":SCHEMA,
        "previous_initial_physical_test_sha256":sha(original),
        "canonical_native_commit":build["canonical_source_commit"],
        "rustfmt_final_native_source_sha256":build["bridge_source_sha256"],
        "native_executable_sha256":build["native_bridge_binary_sha256"],
        "native_build_receipt_sha256":sha(native_receipt_file.read_bytes()),
        "source_code_sha256":{
           name:sha((repo/"research/train_v2"/name).read_bytes())
           for name in (
             "physical_full_escrow_orchestrate.py",
             "physical_full_escrow_receiver.py",
             "physical_native_reparse.rs",
             "physical_native_build.py",
             "physical_native_roundtrip_qualification.py",
             "physical_native_readonly_recheck.py",
           )},
        "host_results":results,
        "old_remote_archive_modified":False,
        "new_remote_archive_modified":False,
        "new_remote_signatures_requested":0,
        "new_remote_archive_writes":0,
        "existing_host_signatures_rechecked_at_original_historical_time":True,
        "new_original_market_quote_used":False,
        "product_signed_native_typed_decision_created":False,
        "real_native_LedgerWriter_append_called":False,
        "native_WitnessStore_ACK_called":False,
        "product_root_owner_or_recovery_grant_installed":False,
        "test_original_payload_is_not_ProductionDecisionV2":True,
        "broker_requests_and_orders":0,
        "model_training_or_promotion":False,
        "status":"HOLD_FINAL_TWO_PHYSICAL_RUST_TYPED_ESCROW_WITHOUT_PRODUCT_ROOT",
    }

def main():
    p=argparse.ArgumentParser()
    for n in ("repo","receiver","prior-report","native-binary",
              "native-build-receipt","output"):
        p.add_argument("--"+n,required=True,type=Path)
    a=p.parse_args()
    os.umask(0o077)
    report=run(a.repo,a.receiver,a.prior_report,a.native_binary,
               a.native_build_receipt)
    dest=a.output
    if dest.exists() or dest.is_symlink():raise ValueError("FINAL_READONLY_CREATE_ONCE")
    if dest.parent.stat().st_uid!=os.geteuid() or dest.parent.stat().st_mode&0o077:
        raise ValueError("FINAL_READONLY_PRIVATE_EVIDENCE")
    data=flow.canonical(report)+b"\n"
    fd=os.open(dest,os.O_WRONLY|os.O_EXCL|os.O_CREAT|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,"wb") as f:
        f.write(data);f.flush();os.fsync(f.fileno())
    dirfd=os.open(dest.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:os.fsync(dirfd)
    finally:os.close(dirfd)
    print(json.dumps({
        "status":report["status"],
        "hosts":[x["physical_host"] for x in report["host_results"]],
        "rust_native_source_sha256":report["rustfmt_final_native_source_sha256"],
        "old_archives_rejected_by_real_native_Rust":True,
        "correct_new_original_archives_verified":True,
        "native_negative_refusals":sum(x["negative_native_challenges_refused"]
                                      for x in report["host_results"]),
        "remote_ssh_mode":"READ_ONLY",
        "new_signatures":0,"native_ledger_appends":0,"broker_orders":0,
        "report_sha256":sha(data)
    },sort_keys=True))

if __name__=="__main__":main()
