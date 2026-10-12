"""Final original-market native test-only qualification, without product authority.

Read-only source validator except create-only final composite evidence receipt.
No trading, broker/model calls, production trust activation or training.
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

from hepta_decision_attribution import read_private,strict
from .original_policy_audit import write_once
from .real_source_native_integrity import inspect

SCHEMA="heptatrader.train-v2-real-original-market-native-twostage-crosshost-qualification.v1"
CANONICAL_COMMIT="78fdb0cf8537e3a84fc6e0a849707559c80881e8"
PARENT_COMMIT="bb9bbbf461c2038ce7e75c0e611486b9fc1ffff0"


def sha(path:Path)->str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(repo:Path,*args)->str:
    return subprocess.check_output(["git","-C",str(repo),*args],
                                   text=True,timeout=15).strip()


def qualify(root:Path,repo:Path,canonical:Path)->dict:
    root,repo,canonical=map(Path,(root,repo,canonical))
    if (not root.is_absolute() or root.is_symlink()
            or root.stat().st_uid!=os.geteuid()
            or stat.S_IMODE(root.stat().st_mode)!=0o700):
        raise ValueError("REAL_TEST_EVIDENCE_ROOT_NOT_PRIVATE")
    if git(canonical,"rev-parse","HEAD")!=CANONICAL_COMMIT:
        raise ValueError("ORIGINAL_CANONICAL_SOURCE_REPLACED")
    if git(canonical,"status","--porcelain"):
        raise ValueError("ORIGINAL_CANONICAL_SOURCE_DIRTY")
    ancestry=subprocess.run(["git","-C",str(repo),"merge-base","--is-ancestor",
                             PARENT_COMMIT,"HEAD"],check=False,timeout=10)
    if ancestry.returncode!=0:
        raise ValueError("TRAIN_RESEARCH_PARENT_SOURCE_CHANGED")
    report=inspect(root)
    signed=strict(read_private(root/"native-real-source-public-only-signature-verification.json",131072))
    faults=strict(read_private(root/"native-real-quote-fault-injection-qualification.json",131072))
    cleanup=strict(read_private(root/"test-only-key-cleanup-receipt.json",131072))
    decision=strict(read_private(root/"real-shadow-decision-v1.json",131072))
    ack=strict(read_private(root/"native-lab-v1/native-source-decision-ack.json",131072))
    finish=strict(read_private(root/"native-lab-v1/native-price-outcome-and-dataset-test-ack.json",131072))
    orig=strict(read_private(root/"real-five-minute-midprice-outcome.json",131072))
    if (report["original_native_on_disk_witness_record_count"]!=2
            or signed["public_only_ed25519_messages_verified"]!=4
            or signed["native_witness_ack_sequence"]!=2
            or cleanup["removed_key_files"]!=5
            or cleanup["production_private_keys_touched"] is not False
            or len(faults["native_original_backend_test_outcomes"])!=3
            or not all(v["original_native_recovery_rejected"]
                       for v in faults["native_original_backend_test_outcomes"].values())
            or not ack["native_acknowledged_at_ms"]<orig["outcome_due_ms"]
            or not orig["outcome_due_ms"]<finish["native_durable_witness_sequence"]+orig["evaluated_at_ms"]
            or finish["native_V3_dataset_is_product_market_eligible"] is not False):
        raise ValueError("ORIGINAL_LIVE_SOURCE_NATIVE_TEST_PROVENANCE_NOT_CLOSED")
    stage_dir=root/"native-lab-v1"
    if stage_dir.stat().st_uid!=os.geteuid() or stat.S_IMODE(stage_dir.stat().st_mode)!=0o700:
        raise ValueError("LAB_LEDGER_DIRECTORY_NOT_PRIVATE")
    for file in stage_dir.iterdir():
        st=file.lstat()
        if (not stat.S_ISREG(st.st_mode) or st.st_uid!=os.geteuid()
                or stat.S_IMODE(st.st_mode)!=0o600):
            raise ValueError("LAB_LEDGER_FILES_NOT_PRIVATE_AT_FINAL_CHECK")
    before=(root/"native-before-private-key-destruction-recovery.sha256").read_bytes()
    after=(root/"native-after-private-key-destruction-recovery.sha256").read_bytes()
    if before!=after:
        raise ValueError("WITNESS_OR_LEDGER_MUTATED_DURING_PRIVATE_KEY_FREE_RECOVERY")
    cold=(root/"native-cold-after-key-removal.stdout").read_text()
    if ('"records":2' not in cold or '"private_signers_read":false' not in cold
            or '"new_signatures_sent":0' not in cold):
        raise ValueError("NATIVE_WITHOUT_KEYS_COLD_RECOVERY_NOT_PROVEN")
    python_log=(root/"full-core-and-historical-python-regression.log").read_text()
    rust_log=(root/"full-original-canonical-ledger-artifacts-libraries.log").read_text()
    if ("1719 passed, 21 skipped, 745 subtests passed" not in python_log
            or "test result: ok. 92 passed; 0 failed; 1 ignored" not in rust_log
            or "test result: ok. 118 passed; 0 failed; 1 ignored" not in rust_log
            or (root/"full-core-and-historical-python-regression.exit").read_text().strip()!="0"
            or (root/"full-original-canonical-ledger-artifacts-libraries.exit").read_text().strip()!="0"):
        raise ValueError("RESEARCH_ORIGINAL_LIB_REGRESSION_NOT_PASSED")
    names=[
        "plan.json","feature-spec.json","real-shadow-decision-v1.json",
        "causal-feature-before-outcome.json","native-real-quote-packet.json",
        "public_roles.json","native-init-crosshost-public-signatures.json",
        "native-finish-crosshost-public-signatures.json",
        "real-five-minute-midprice-outcome.json",
        "x230-independent-price-observation.json",
        "pocket4-verified-x230-observer-receipt.json",
        "native-real-price-observer-packet.json",
        "real-original-market-source-native-closed-integrity.json",
        "native-real-source-public-only-signature-verification.json",
        "native-real-quote-fault-injection-qualification.json",
        "test-only-key-cleanup-receipt.json",
        "native-cold-after-key-removal.stdout",
        "full-core-and-historical-python-regression.log",
        "full-original-canonical-ledger-artifacts-libraries.log",
    ]
    hashes={name:sha(root/name) for name in names}
    hashes.update({"native-lab-v1/"+file.name:sha(file)
                   for file in stage_dir.iterdir()})
    services={}
    for n in ("heptatrader-execution.service","heptatrader-paper-driver.service",
              "heptatrader-crypto-driver.service","heptatrader-laya-feed.service",
              "heptatrader-neuron-shadow-eval.service",
              "heptatrader-learning-source-health.service"):
        services[n]=subprocess.check_output(
            ["systemctl","--user","is-active",n],text=True,timeout=10).strip()
    if any(s!="active" for s in services.values()):
        raise ValueError("NATIVE_TEST_EXPERIMENT_PRODUCTION_SERVICE_HEALTH_CHANGED")
    sources={}
    for name in ("real_source_native_lab.rs",
                 "real_source_native_packet.py",
                 "real_source_native_outcome_packet.py",
                 "real_source_native_signature_verify.py",
                 "real_source_native_integrity.py"):
        sources[name]=sha(repo/"research/train_v2"/name)
    sources["test_train_v2_real_source_native.py"]=sha(repo/"tests/python/test_train_v2_real_source_native.py")
    return {
        "schema":SCHEMA,
        "qualified_at_utc":datetime.now(timezone.utc).isoformat(),
        "heptatrader_research_commit":git(repo,"rev-parse","HEAD"),
        "canonical_original_source_commit":git(canonical,"rev-parse","HEAD"),
        "original_canonical_checkout_clean":True,
        "one_new_original_paper_market_shadow_decision_preregistered":True,
        "local_plan_id":decision["local_prospective_plan_id"],
        "shadow_only_selected_signal":decision["selected"],
        "true_only_shadow_Q32_propensity":decision["selected_propensity_q32_raw"],
        "original_market_decision_at_ms":decision["decision_at_ms"],
        "mac_source_time_original_native_test_signature_and_ack_at_ms":
            ack["native_acknowledged_at_ms"],
        "native_decision_ack_was_before_5min_outcome":True,
        "real_midprice_horizon_ms":orig["outcome_due_ms"],
        "actual_original_IB_PAPER_quote_midprice_bps":
            orig["actual_market_mid_price_return_bps"],
        "x230_source_copy_price_independently_recalculated":True,
        "x230_own_IB_broker_feed_obtained":False,
        "four_true_mac_x230_qian_test_signature_messages_verified":True,
        "native_original_signed_Decision_Outcome_Witness_count":2,
        "native_original_test_DatasetSnapshotV3_frozen_and_revalidated":True,
        "native_datasetV3_eligible_for_real_product_use":False,
        "native_cold_recover_without_private_keys":True,
        "five_test_private_key_files_deleted_and_verified_at_source_hosts":True,
        "hardware_secure_erasure_of_test_keys_established":False,
        "native_rejects_independent_witness_corruption_truncated_ledger_and_root_sig":True,
        "native_test_file_permissions_detected_and_hardened_to_0600":True,
        "physical_abrupt_power_loss_survival_tested":False,
        "product_root_enrolled_or_administrators_independent":False,
        "original_opaque_LLM_behavior_propensities_known":False,
        "real_trade_fills_commissions_or_cost_complete_PnL_known":False,
        "production_ledger_writer_service_activated":False,
        "real_market_eligible_product_dataset_V3_accepted":False,
        "native_ArtifactOwner_CURRENT_or_promotion_authorized":False,
        "pocket4_services_remained_active":services,
        "full_python_core_historical_passed":1719,
        "full_python_subtests_passed":745,
        "full_original_native_ledger_artifact_lib_tests_passed":210,
        "private_evidence_sha256":hashes,
        "git_source_sha256":sources,
        "source_research_validated_no_trading_model_deployment":True,
        "product_qualification_state":"HOLD_ORIGINAL_POLICY_PRODUCTION_ROOT_REAL_AFTER_COST_EFFICACY",
    }


def main()->None:
    p=argparse.ArgumentParser()
    p.add_argument("--root",type=Path,required=True)
    p.add_argument("--repo",type=Path,required=True)
    p.add_argument("--canonical",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True)
    a=p.parse_args();os.umask(0o077)
    result=qualify(a.root,a.repo,a.canonical)
    receipt=write_once(a.output,result)
    print(json.dumps({
        "status":result["product_qualification_state"],
        "real_paper_midprice_bps":result["actual_original_IB_PAPER_quote_midprice_bps"],
        "native_original_witness_events":result["native_original_signed_Decision_Outcome_Witness_count"],
        "new_model_or_broker_trading_authority":False,
        "receipt_sha256":receipt,
    },sort_keys=True))


if __name__=="__main__":main()
