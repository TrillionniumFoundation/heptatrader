"""Final actual original signed-event native idempotency/uncertain ACK lab receipt.

Isolated COPIES of prior real IB PAPER shadow-native source, never production
LedgerWriter. Historical-timestamp retries in these copies are experiments,
not evidence of current-time authorization or a safe production recovery API.
"""
from __future__ import annotations

import argparse
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess

from .original_policy_audit import write_once
from .real_source_native_recovery_gate import (
    ACK,HOLD,assess as read_current_clock)
from hepta_decision_attribution import read_private, strict

SCHEMA="heptatrader.train-v2-real-original-source-native-witness-uncertain-retry-v1"
COMMIT="7ed0029320e2f850ad9187c250d8198779bfab5b"
CANONICAL="78fdb0cf8537e3a84fc6e0a849707559c80881e8"
REPLAY_BINARY=Path.home()/(
    ".local/state/heptatrader/operator-evidence/"
    "train-v2-full-canonical-20261011/native-test-target/"
    "debug/examples/real_source_native_replay"
)
CASES={
    "acknowledged":(2,2,ACK),
    "expired":(2,2,ACK),
    "lag-wrong":(1,1,HOLD),
    "lag-exact":(1,2,ACK),
    "lag-torn":(1,2,ACK),
    "lag-expired":(1,1,HOLD),
}


def sha(raw:bytes)->str:return hashlib.sha256(raw).hexdigest()


def commit(repo:Path)->str:
    return subprocess.check_output(["git","-C",str(repo),"rev-parse","HEAD"],text=True,timeout=10).strip()


def inspected_file(path:Path,*,readlimit=2*1024*1024)->bytes:
    if path.is_symlink() or not path.is_file() or path.stat().st_uid!=os.geteuid():
        raise ValueError("PRIVATE_SIGNED_LAB_COPY_ESCAPE")
    if path.name.endswith(".stdout"):
        # A separately bounded owner-written test command transcript is
        # not a private write_once signed source. Treat it only as a log.
        if path.stat().st_size>readlimit:
            raise ValueError("NATIVE_TEST_LOG_TOO_LARGE")
        return path.read_bytes()
    return read_private(path,readlimit)


def qualify(root:Path,original:Path,repo:Path,canonical:Path)->dict:
    root,original,repo,canonical=map(Path,(root,original,repo,canonical))
    if (commit(repo)!=COMMIT or commit(canonical)!=CANONICAL
            or subprocess.check_output(
               ["git","-C",str(canonical),"status","--porcelain"],text=True).strip()):
        raise ValueError("ORIGINAL_SOURCE_PROVENANCE_CHANGED")
    if (not root.is_absolute() or root.is_symlink() or not root.is_dir()
            or root.parent!=original.parent
            or any(stat.S_IMODE(x.stat().st_mode)!=0o700
                   for x in (root,original))):
        raise ValueError("ISOLATED_REAL_NATIVE_LAB_DIRECTORY_INVALID")
    src=original/"native-lab-v1"
    actual_ledger=inspected_file(src/"ledger")
    actual_witness=inspected_file(src/"witness")
    trust=inspected_file(src/"original-test-trust.json")
    if len(actual_ledger)!=1224 or len(actual_witness)!=248:
        raise ValueError("REAL_SIGNED_SOURCE_CLOSED_LEDGER_NOT_TWO_RECORDS")
    original_qualification=strict(inspected_file(
        original/"actual-real-original-market-crosshost-native-qualified-final-v1.json"))
    if (original_qualification["schema"]!=
            "heptatrader.train-v2-real-original-market-native-twostage-crosshost-qualification.v1"
            or original_qualification["native_original_signed_Decision_Outcome_Witness_count"]!=2
            or original_qualification["private_evidence_sha256"]["native-lab-v1/ledger"]
               !=sha(actual_ledger)
            or original_qualification["private_evidence_sha256"]["native-lab-v1/witness"]
               !=sha(actual_witness)
            or original_qualification["product_root_enrolled_or_administrators_independent"] is not False):
        raise ValueError("ORIGINAL_REAL_PRICE_SOURCE_WAS_NOT_TEST_ONLY")
    tested={}
    ack=0
    holds=0
    for mode,(before,after,decision) in CASES.items():
        folder=root/(mode+"-final")
        if (not folder.is_dir() or folder.is_symlink()
                or stat.S_IMODE(folder.stat().st_mode)!=0o700):
            raise ValueError("TEST_COPY_NOT_PRIVATE")
        raw=inspected_file(folder/"native-replay.stdout",readlimit=131072)
        lines=raw.decode().splitlines()
        if len(lines)!=1 or not lines[0].startswith("RESULT_JSON "):
            raise ValueError("EXACT_NATIVE_REPLAY_OUTPUT_NOT_ONE_RECORD")
        native=json.loads(lines[0].split(" ",1)[1])
        if (native["schema"]!="heptatrader.train-v2-native-source-signed-historical-replay-lab.v1"
            or native["replay_mode"]!=mode or native["before_witness"]!=before
            or native["after_witness"]!=after
            or native["new_signer_requests"]!=0
            or native["records_still_exactly_two"] is not True
            or native["native_generator_public_signer_expired_now"] is not True
            or native["production_product_authority"] is not False
            or native["test_private_signers_present"] is not False
            or native["real_paper_broker_order_sent"] is not False
            or native["old_signed_evidence_historical_timestamp_used_for_replay"]
               !=(mode not in ("expired","lag-expired"))):
            raise ValueError("NATIVE_LAST_ACK_REPLAY_QUALIFICATION_INCONSISTENT")
        ledger=inspected_file(folder/"ledger")
        witness=inspected_file(folder/"witness")
        recovered=read_current_clock(original,folder)
        if (ledger!=actual_ledger
                or witness!=(actual_witness if after==2 else actual_witness[:160])
                or recovered["recovery_admission"]!=decision
                or recovered["native_witness_count"]!=after
                or recovered["independently_public_signed_Observer_epoch"]["native_expires_at_ms"]
                   >=recovered["real_wall_clock_ms"]
                or recovered["automatic_write_or_witness_ack_performed"] is not False):
            raise ValueError("CURRENT_TIME_REPLAY_ADMISSION_OR_COPY_HASH_CHANGED")
        tested[mode]={
            "native_before_witness_ack":before,
            "native_after_witness_ack":after,
            "actual_native_signed_copy_ledger_has_two_records":True,
            "current_clock_recovery_admission":decision,
            "native_complete_ledger_sha256":sha(ledger),
            "native_witness_prefix_or_complete_sha256":sha(witness),
            "actual_original_signer_credential_expired":True,
            "native_stdout_sha256":sha(raw),
            "current_clock_receipt_sha256":
                sha(inspected_file(folder/"current-clock-recovery-admission.json")),
        }
        if decision==ACK:ack+=1
        else:holds+=1
    for f in ("ledger","witness","original-test-trust.json"):
        current=inspected_file(src/f)
        expected=actual_ledger if f=="ledger" else (
            actual_witness if f=="witness" else trust)
        if current!=expected:
            raise ValueError("ORIGINAL_RETAINED_NATIVE_SOURCE_MUTATED")
    py_source=repo/"research/train_v2"
    sources={name:sha((py_source/name).read_bytes())
             for name in ("real_source_native_lab.rs","real_source_native_replay.rs",
                          "real_source_native_recovery_gate.py")}
    sources["test_train_v2_native_recovery_gate.py"]=sha(
        (repo/"tests/python/test_train_v2_native_recovery_gate.py").read_bytes())
    full_py=(root/"full-python-core-historical-final.log").read_text()
    full_rust=(root/"full-original-canonical-ledger-artifacts-libraries.log").read_text()
    if ((root/"full-python-core-historical-final.exit").read_text().strip()!="0"
            or (root/"full-original-canonical-ledger-artifacts-libraries.exit").read_text().strip()!="0"
            or "1736 passed, 21 skipped, 750 subtests passed" not in full_py
            or "test result: ok. 92 passed; 0 failed; 1 ignored" not in full_rust
            or "test result: ok. 118 passed; 0 failed; 1 ignored" not in full_rust):
        raise ValueError("ORIGINAL_CANONICAL_AND_TRAIN_FULL_REGRESSION_NOT_CLOSED")
    services={}
    for unit in ("heptatrader-execution.service","heptatrader-paper-driver.service",
        "heptatrader-crypto-driver.service","heptatrader-laya-feed.service",
        "heptatrader-neuron-shadow-eval.service","heptatrader-learning-source-health.service"):
        services[unit]=subprocess.check_output(["systemctl","--user","is-active",unit],
                                             text=True,timeout=12).strip()
    if any(v!="active" for v in services.values()):
        raise ValueError("RUNNING_BROKER_SERVICE_CHANGED_DURING_REPLAY_TEST")
    return {
        "schema":SCHEMA,
        "qualified_at_utc":datetime.now(timezone.utc).isoformat(),
        "original_historical_real_IB_PAPER_shadow_test_source":True,
        "test_source_exact_phase4L_commit":COMMIT,
        "original_full_canonical_source_commit":CANONICAL,
        "original_canonical_workspace_clean":True,
        "historical_native_root_and_independently_sealed_test_signatures_verified":True,
        "new_keys_created_or_test_keys_reloaded":False,
        "after_expiry_signed_message_current_time_checked_not_backdated":True,
        "replaying_old_signature_at_issuer_time_is_TEST_ONLY_not_current_authority":True,
        "six_real_sourced_native_replay_copy_cases":tested,
        "already_acked_or_repaired_signed_copy_count":ack,
        "current_clock_expired_lagged_witness_HOLD_cases":holds,
        "original_native_ledger_witness_preserved_byte_for_byte":True,
        "actual_real_POCKET4_source_signer_institutional_independence":False,
        "official_production_recovery_authority_and_independent_escrow_enrolled":False,
        "actual_sudden_hardware_power_loss_executed":False,
        "full_heptatrader_python_core_historical_passed":1736,
        "full_heptatrader_python_core_historical_skipped":21,
        "full_python_subtests_passed":750,
        "full_original_canonical_ledger_and_artifact_libs_passed":210,
        "full_original_canonical_library_tests_ignored":2,
        "original_canonical_cargo_lock_sha256":sha(
            (canonical/"codex-rs/Cargo.lock").read_bytes()),
        "full_python_test_log_sha256":sha(full_py.encode()),
        "full_original_canonical_rust_test_log_sha256":sha(full_rust.encode()),
        "real_original_LLM_policy_action_probabilities_available":False,
        "actual_order_fills_cost_complete_returns_attested":False,
        "full_native_product_trainable_DatasetSnapshotV3_signed":False,
        "native_ArtifactOwner_CURRENT_or_model_promotion_authorized":False,
        "broker_requests_or_orders_added":0,
        "live_services_verified":services,
        "final_native_test_only_replay_binary_sha256":sha(REPLAY_BINARY.read_bytes()),
        "source_code_sha256":sources,
        "actual_original_native_source_hashes":{
           "ledger":sha(actual_ledger),"witness":sha(actual_witness),
           "local_test_root":sha(trust),
        },
        "release_gate":"HOLD_AUTHORIZED_POST_EXPIRY_NATIVE_ACK_RECONCILIATION_AND_ORIGINAL_MODEL_PRODUCTION_TRUST",
    }


def main()->None:
    p=argparse.ArgumentParser()
    p.add_argument("--root",type=Path,required=True)
    p.add_argument("--original",type=Path,required=True)
    p.add_argument("--repo",type=Path,required=True)
    p.add_argument("--canonical",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True)
    a=p.parse_args();os.umask(0o077)
    report=qualify(a.root,a.original,a.repo,a.canonical)
    digest=write_once(a.output,report)
    print(json.dumps({
        "native_original_signed_copy_cases":len(report["six_real_sourced_native_replay_copy_cases"]),
        "acknowledged_or_historically_repaired_copies":
            report["already_acked_or_repaired_signed_copy_count"],
        "current_time_unwitnessed_expired_signature_HOLD_cases":
            report["current_clock_expired_lagged_witness_HOLD_cases"],
        "product_acceptance":report["release_gate"],
        "receipt_sha256":digest,
    },sort_keys=True))


if __name__=="__main__":main()
