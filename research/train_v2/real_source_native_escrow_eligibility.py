"""Current candid truth status of Phase 4N independent Escrow readiness.

Do not silently turn locally generated TEST credentials, hostname text,
crypto validation, or synthetic native examples into a production Root /
independent administrator. An unconsumed preregistered market window
does not establish a real signed Decision. Zero keys are requested and
zero orders or product records are issued here.
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

from hepta_decision_attribution import read_private, strict
from .original_policy_audit import write_once

SCHEMA="heptatrader.train-v2-escrow-recovery-testonly-source-window-hold.v1"
HOLD="HOLD_INDEPENDENT_REMOTE_DURABLE_ESCROW_NOT_ATTESTED_OR_PRODUCT_ROOT_ENROLLED"


def assess(root:Path,*,now_ms:int|None=None)->dict:
    root=Path(root)
    if (not root.is_absolute() or root.is_symlink()
            or not root.is_dir()
            or stat.S_IMODE(root.stat().st_mode)!=0o700):
        raise ValueError("ESCROW_TRIAL_PRIVATE_SCOPE_INVALID")
    if now_ms is None:now_ms=time.time_ns()//1000000
    if type(now_ms) is not int or now_ms<=0:
        raise ValueError("ESCROW_TRIAL_CLOCK_INVALID")
    raw=read_private(root/"plan.json",16384)
    plan=strict(raw)
    spec=strict(read_private(root/"feature-spec.json",16384))
    if (plan.get("behavior_policy_id")!="SHADOW_RESEARCH_PREACTION_TWO_BIT_NO_ORDER_V1"
            or plan.get("trade_authority") is not False
            or plan.get("period_sha256")!=spec.get("period_sha256")
            or plan.get("minimum_mature_rows")!=16
            or type(plan.get("registered_at_ms")) is not int
            or type(plan.get("first_decision_ms")) is not int
            or type(plan.get("last_decision_ms")) is not int
            or plan["first_decision_ms"]-plan["registered_at_ms"]<30000
            or plan["last_decision_ms"]<=plan["first_decision_ms"]):
        raise ValueError("ESCROW_PLAN_OR_SOURCE_POLICY_CHANGED")
    # A valid preregistered plan is not sufficient. Honest readiness
    # REQUIRES source-time native original SignedEvidence + a remotely
    # attested, independently held escrow receipt BEFORE any append.
    consumed=(root/"real-shadow-decision-v1.json").exists()
    escrow_decision=(root/"native-lab-v1/original-native-decision-independent-escrow.json").exists()
    native_decision=(root/"native-lab-v1/native-source-decision-ack.json").exists()
    escrow_outcome=(root/"native-lab-v1/original-native-outcome-independent-escrow.json").exists()
    native_outcome=(root/"native-lab-v1/native-price-outcome-and-dataset-test-ack.json").exists()
    if native_decision and not escrow_decision:
        raise ValueError("NATIVE_DECISION_WITHOUT_INDEPENDENT_ESCROW_NOT_ACCEPTABLE")
    if native_outcome and not (native_decision and escrow_outcome):
        raise ValueError("NATIVE_OUTCOME_WITHOUT_INDEPENDENT_ESCROW_NOT_ACCEPTABLE")
    return {
        "schema":SCHEMA,
        "audited_at_utc":datetime.now(timezone.utc).isoformat(),
        "new_precommitted_local_IB_PAPER_plan_id":plan["plan_id"],
        "actual_source_time_plan_sealed_before_first_ms":plan["first_decision_ms"]-plan["registered_at_ms"],
        "plan_window_active_at_check":plan["first_decision_ms"]<=now_ms<=plan["last_decision_ms"],
        "plan_consumed_by_new_actual_source_decision":consumed,
        "test_only_ESCROW_program_compiled_as_original_cargo_example":True,
        "independent_X230_source_time_test_signer_enrolled":False,
        "independent_Mac_outcome_time_test_signer_enrolled":False,
        "physical_external_escrow_write_once_and_fsync_observed":False,
        "original_native_decision_signed":native_decision,
        "original_native_outcome_signed":native_outcome,
        "required_source_time_decision_escrow_exists_locally":escrow_decision,
        "required_future_outcome_escrow_exists_locally":escrow_outcome,
        "synthetic_native_escrow_crypto_positive_and_negative_tests_separate":True,
        "original_opaque_LLM_action_distribution_or_generator_root":False,
        "hardware_enrolled_external_product_escrow_recovery_authority":False,
        "recovered_historical_ACK_under_production_policy":False,
        "actual_order_fills_or_after_cost_net_PnL":False,
        "actual_broker_orders_or_model_changes":0,
        "recovery_and_release_state":HOLD,
        "next_hard_gate":"Authorized independent host custody of pinned escrow key, observed actual durable preappend archive, and product recovery owner protocol; absent those, no native append or ACK promotion",
        "plan_file_sha256":hashlib.sha256(raw).hexdigest(),
    }


def main()->None:
    p=argparse.ArgumentParser()
    p.add_argument("--root",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True)
    a=p.parse_args();os.umask(0o077)
    r=assess(a.root)
    digest=write_once(a.output,r)
    print(json.dumps({
        "research_status":r["recovery_and_release_state"],
        "plan_consumed":r["plan_consumed_by_new_actual_source_decision"],
        "external_escrow_fact_obtained":
            r["physical_external_escrow_write_once_and_fsync_observed"],
        "product_root_enrolled":False,"receipt_sha256":digest,
    },sort_keys=True))


if __name__=="__main__":main()
