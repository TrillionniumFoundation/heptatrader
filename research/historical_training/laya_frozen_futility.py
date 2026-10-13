"""Read-only proof of irreversible mathematical failure of a frozen Laya trial.

Never writes original plan/slot files or overwrites original evaluator verdict.
A PENDING evaluator state can coexist with already-impossible qualification.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
from historical_training.common import read, sha, EvidenceError
from laya_cell.integrity import ContractError, digest
from laya_cell.promotion import validate_plan_rule

SCHEMA = "heptatrader.laya-frozen-future-futility-audit.v1"

def assess_original_futility(plan_file):
    file=Path(plan_file)
    if file.is_symlink() or not file.is_file() or file.name!="plan.json":
        raise ContractError("FUTILITY_FROZEN_PLAN_REQUIRED")
    plan=read(file,131072)
    if (plan.get("schema")!="heptatrader.laya-resident-future-plan.v1"
            or plan.get("plan_id")!=digest({
                k:v for k,v in plan.items() if k!="plan_id"})):
        raise ContractError("FUTILITY_PLAN_MUTATED")
    rule=validate_plan_rule(plan)
    slots=rule["slots"]
    if (type(slots) is not int or not 1<=slots<=256
            or type(rule["max_censored"]) is not int
            or type(rule["min_observed"]) is not int
            or rule["min_observed"]>slots):
        raise ContractError("FUTILITY_RULE_BOUND")
    observed=censored=0
    outcomes=[]
    for path in sorted(file.parent.glob("outcome-*.json")):
        if path.is_symlink():
            raise ContractError("FUTILITY_OUTCOME_SYMLINK")
        number=path.stem.removeprefix("outcome-")
        if not (number.isdigit() and len(number)==3):
            raise ContractError("FUTILITY_OUTCOME_FILENAME_CHANGED")
        slot=int(number)
        if not 0<=slot<slots:
            raise ContractError("FUTILITY_OUTCOME_OUTSIDE_FROZEN_ROSTER")
        result=read(path,131072)
        if (result.get("plan_id")!=plan["plan_id"]
                or result.get("candidate_id")!=plan["candidate_id"]
                or type(result.get("slot")) is not int
                or result["slot"]!=slot):
            raise ContractError("FUTILITY_ORIGINAL_OUTCOME_IDENTITY")
        state=result.get("status")
        if state not in ("OBSERVED","CENSORED"):
            raise ContractError("FUTILITY_UNRECOGNIZED_ORIGINAL_OUTCOME")
        observed+=state=="OBSERVED"
        censored+=state=="CENSORED"
        outcomes.append({"slot":slot,"state":state,
                         "sha256":sha(path)})
    if len(outcomes)!=observed+censored:
        raise ContractError("FUTILITY_OUTCOME_DUPLICATE")
    max_possible=slots-censored
    impossible=(censored>rule["max_censored"] or
                max_possible<rule["min_observed"])
    payload={
        "schema":SCHEMA,
        "frozen_plan_id":plan["plan_id"],
        "plan_sha256":sha(file),
        "candidate_id":plan["candidate_id"],
        "precommitted_slots":slots,
        "precommitted_min_observed":rule["min_observed"],
        "precommitted_max_censored":rule["max_censored"],
        "verified_terminal_observed":observed,
        "verified_terminal_censored":censored,
        "missing_or_future_slots_not_mislabeled_as_zero":slots-observed-censored,
        "maximum_possible_observed_after_current_censoring":max_possible,
        "terminal_slot_sha256":outcomes,
        "statistical_qualification_already_mathematically_impossible":impossible,
        "audit_status":("FROZEN_PROMOTION_IMPOSSIBLE_NO_RETROACTIVE_REPAIR"
                       if impossible else "NOT_IMPOSSIBLE_FROM_COUNTS_ALONE"),
        "original_evaluator_qualification_rewritten":False,
        "alpha_budget_reset":False,
        "old_failed_or_pending_source_restored":False,
        "economic_alpha_qualified":False,
        "automatic_model_promotion":False,
        "broker_queries_sent":0,
        "broker_orders_sent":0,
        "trade_authority":False,
    }
    return {**payload,"audit_id":digest(payload)}

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--plan",type=Path,required=True)
    ap.add_argument("--output",type=Path,required=True)
    a=ap.parse_args();os.umask(0o077)
    result=assess_original_futility(a.plan)
    path=a.output
    if path.exists() or path.is_symlink():
        raise ContractError("FUTILITY_IMMUTABLE_NEW_OUTPUT_REQUIRED")
    path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    raw=(json.dumps(result,sort_keys=True,indent=2,allow_nan=False)+"\n").encode()
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,"wb") as f:
        f.write(raw);f.flush();os.fsync(f.fileno())
    directory=os.open(path.parent,os.O_DIRECTORY|os.O_RDONLY)
    try:os.fsync(directory)
    finally:os.close(directory)
    print(json.dumps({
        "status":result["audit_status"],
        "verified_censored":result["verified_terminal_censored"],
        "max_censored_original":result["precommitted_max_censored"],
        "max_possible_observed":result["maximum_possible_observed_after_current_censoring"],
        "original_qualification_changed":False,
        "orders":0,
        "sha256":hashlib.sha256(raw).hexdigest(),
    },sort_keys=True))

if __name__=="__main__":
    main()
