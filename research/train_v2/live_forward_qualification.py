"""Final fail-closed EXACT-PROVENANCE qualification for one actual FX shadow trial.

Actual original-market research source and X230 OBSERVATION only; no trade,
no product trust, no native Writer append, no broker API or model promotion.
"""
from __future__ import annotations

import argparse
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess

from hepta_decision_attribution import read_private,strict
from .live_forward_x230_verify import verify
from .original_policy_audit import write_once


def sha(path:Path)->str:
    # Legacy pytest stdout is an owner-written plain log, not one of
    # the write_once/fsynced private research event receipts. It may
    # have mode 0644; hash it safely without upgrading its authority.
    if path.name.endswith(".log"):
        st=path.stat()
        if (path.is_symlink() or not path.is_file()
                or st.st_uid!=os.geteuid() or st.st_size>1024*1024):
            raise ValueError("TEST_LOG_NOT_AN_OWNER_REGULAR_FILE")
        return hashlib.sha256(path.read_bytes()).hexdigest()
    return hashlib.sha256(read_private(path,1024*1024)).hexdigest()


def commit(path:Path)->str:
    return subprocess.check_output(
        ["git","-C",str(path),"rev-parse","HEAD"],text=True,timeout=10).strip()


def qualify(root:Path,canonical_checkout:Path,repo:Path)->dict:
    root=Path(root)
    canonical_checkout=Path(canonical_checkout)
    repo=Path(repo)
    expected_canonical="78fdb0cf8537e3a84fc6e0a849707559c80881e8"
    if commit(canonical_checkout)!=expected_canonical:
        raise ValueError("CANONICAL_ORIGINAL_COMMIT_DRIFT")
    if subprocess.check_output(["git","-C",str(canonical_checkout),
                                "status","--porcelain"],text=True).strip():
        raise ValueError("CANONICAL_ORIGINAL_DIRTY")
    if commit(repo)!="8be9371ef4e49c98acc7127d8343baa2de5d412c":
        # This is the exact parent of Phase 4J. After initial development,
        # current branch HEAD will be its own Phase4J commit; it must instead
        # be an ancestor, never rewrite old canonical policy.
        ancestor=subprocess.run(
            ["git","-C",str(repo),"merge-base","--is-ancestor",
             "8be9371ef4e49c98acc7127d8343baa2de5d412c","HEAD"],
            timeout=10,check=False)
        if ancestor.returncode!=0:
            raise ValueError("HEPTATRADER_RESEARCH_BASE_CHANGED")
    names=[
        "plan.json","feature-spec.json","real-shadow-decision-v1.json",
        "causal-feature-before-outcome.json",
        "real-five-minute-midprice-outcome.json",
        "x230-independent-price-observation.json",
        "pocket4-verified-x230-observer-receipt.json",
        "canonical-admission-real-source-hold.json",
        "full-core-and-historical-regression.log",
    ]
    artifacts={name:sha(root/name) for name in names}
    def doc(name):return strict(read_private(root/name,1024*1024))
    plan=doc("plan.json")
    pre=doc("causal-feature-before-outcome.json")
    decision=doc("real-shadow-decision-v1.json")
    observed=doc("real-five-minute-midprice-outcome.json")
    signed=doc("x230-independent-price-observation.json")
    canonical=doc("canonical-admission-real-source-hold.json")
    x230=verify(root,signed)
    if (plan["plan_id"]!=pre["registered_plan_id"]
            or plan["plan_id"]!=decision["local_prospective_plan_id"]
            or plan["plan_id"]!=observed["exact_plan_id"]
            or pre["full_feature_id"]!=observed["feature_id_revalidated_exactly"]
            or pre["full_feature_id"]!=x230["frozen_feature_id"]
            or decision["local_plan_sealed_at_ms"]+30000>decision["decision_at_ms"]
            or not plan["first_decision_ms"]<=decision["decision_at_ms"]<=plan["last_decision_ms"]
            or not decision["decision_at_ms"]<=pre["captured_at_ms"]<observed["outcome_due_ms"]
            or observed["outcome_due_ms"]-decision["decision_at_ms"]!=300000
            or not observed["outcome_due_ms"]<=observed["actual_original_endpoint_quote_receipt"]["event_at_ms"]<=observed["outcome_due_ms"]+5000
            or observed["actual_endpoint_or_censoring_status"]!="OBSERVED"
            or observed["actual_market_mid_price_return_bps"]!="-0.98103481"
            or x230["x230_independently_recomputed_midprice_return_bps"]!=observed["actual_market_mid_price_return_bps"]
            or signed["signed_observation"]["verified_quote_path_points"]!=148
            or canonical["original_canonical_source_and_policy_admission_audit"]["assessment"]!="HOLD_MISSING_NATIVE_LEDGER_WRITER_AND_OWNER_FACTS"
            or len(canonical["original_canonical_source_and_policy_admission_audit"]["missing_native_requirements"])!=12):
        raise ValueError("FORWARD_REAL_PROSPECTIVE_PROVENANCE_INCOMPLETE")
    for f in ("real-shadow-episode-run.exit",
              "real-outcome-cli.exit","full-core-and-historical-regression.exit"):
        if (root/f).read_text().strip()!="0":
            raise ValueError("REAL_PROSPECTIVE_NATIVE_EXECUTION_FAILED")
    if "1663 passed, 21 skipped, 728 subtests passed" not in (
            root/"full-core-and-historical-regression.log").read_text():
        raise ValueError("FULL_PYTHON_REGRESSION_NOT_PASSED")
    services=("heptatrader-execution.service","heptatrader-paper-driver.service",
              "heptatrader-crypto-driver.service","heptatrader-laya-feed.service",
              "heptatrader-neuron-shadow-eval.service",
              "heptatrader-learning-source-health.service")
    states={
        s:subprocess.check_output(["systemctl","--user","is-active",s],
                                 text=True).strip()
        for s in services
    }
    if any(status!="active" for status in states.values()):
        raise ValueError("PRODUCTION_SERVICE_DEGRADED")
    return {
        "schema":"heptatrader.train-v2-actual-new-original-market-5min-shadow-prospective.v1",
        "sealed_at_utc":datetime.now(timezone.utc).isoformat(),
        "heptatrader_research_source_commit":commit(repo),
        "original_canonical_source_commit":commit(canonical_checkout),
        "real_original_market_paper_source_reverified":True,
        "one_original_market_shadow_decision_precommitted":True,
        "original_research_plan_id":plan["plan_id"],
        "precommit_lead_before_first_decision_ms":
            plan["first_decision_ms"]-decision["local_plan_sealed_at_ms"],
        "shadow_policy_only":decision["policy_id"],
        "shadow_candidate_set":decision["candidates"],
        "shadow_selected_action":decision["selected"],
        "shadow_true_choice_probability_q32_raw":decision["selected_propensity_q32_raw"],
        "shadow_decision_at_ms":decision["decision_at_ms"],
        "original_input_quote_receipts":decision["source_history_quote_receipts"],
        "frozen_preoutcome_feature_id":pre["full_feature_id"],
        "preoutcome_feature_not_changed":True,
        "outcome_maturity_ms":observed["outcome_due_ms"],
        "real_original_market_quote_event_at_ms":
            observed["actual_original_endpoint_quote_receipt"]["event_at_ms"],
        "original_endpoint_quote_receipt":
            observed["actual_original_endpoint_quote_receipt"]["source_id"],
        "real_original_market_five_minute_midprice_return_bps":
            observed["actual_market_mid_price_return_bps"],
        "x230_physical_separate_host_independently_recomputed_label":True,
        "x230_price_path_quote_points":
            signed["signed_observation"]["verified_quote_path_points"],
        "x230_ephemeral_test_only_ed25519_signature_verified":True,
        "x230_is_same_upstream_local_broker_source_copy":True,
        "x230_machine_identity_hardware_attested":False,
        "original_opaque_llm_behavior_policy_true_propensity":False,
        "independently_administered_production_root_and_roles":False,
        "actual_ib_fills_commissions_slippage_net_pnl_attested":False,
        "native_production_ledgerwriter_acks_for_these_events":False,
        "real_native_product_dataset_snapshot_v3_admitted":False,
        "artifact_owner_current_or_model_promotion_authorized":False,
        "broker_queries_or_orders_by_this_experiment":0,
        "all_six_existing_services_active":states,
        "regression_passed":1663,
        "regression_skipped":21,
        "regression_subtests_passed":728,
        "canonical_source_based_product_gate_fails_closed":True,
        "real_economic_execution_efficacy_still_hold":True,
        "evidence_sha256":artifacts,
        "source_code_sha256":{
            name:hashlib.sha256((repo/"research/train_v2"/name).read_bytes()).hexdigest()
            for name in ("preaction_shadow_policy.py",
                         "live_forward_outcome.py",
                         "live_forward_x230_witness.py",
                         "live_forward_x230_verify.py")
        },
    }


def main()->None:
    p=argparse.ArgumentParser()
    p.add_argument("--root",type=Path,required=True)
    p.add_argument("--canonical-checkout",type=Path,required=True)
    p.add_argument("--repo",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True)
    a=p.parse_args()
    os.umask(0o077)
    body=qualify(a.root,a.canonical_checkout,a.repo)
    digest=write_once(a.output,body)
    print(json.dumps({"schema":body["schema"],
        "actual_midprice_observed_bps":
            body["real_original_market_five_minute_midprice_return_bps"],
        "x230_signature_verified":True,
        "precommit_source_real":True,
        "trade_or_model_promotion_authorized":False,
        "qualification_sha256":digest},sort_keys=True))


if __name__=="__main__":
    main()
