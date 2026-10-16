"""Build a frozen, ORIGINAL closed-Laya-only train population for future v2.

Real saved prediction/outcome pairs only; no active future plan may enter this
training population. All frozen parent IDs must match the requested parent.
The resulting training rows are NEVER qualification or production evidence.
"""
from __future__ import annotations
from bisect import bisect_right
from decimal import Decimal,InvalidOperation
import math
from pathlib import Path

from .action_quality_research import probabilities
from .dataset import BIN_EDGES_BPS,question
from .integrity import ContractError,digest,read_json,sha_file

SCHEMA="heptatrader.laya-v2-closed-parent-training-source.v1"
MAX_CLOSED_PLANS=512
MAX_OBS=4096


def collect_parent_closed_train(root,parent_id,cutoff_ms):
    root=Path(root)
    if (type(parent_id) is not str or len(parent_id)!=64 or
            any(c not in "0123456789abcdef" for c in parent_id) or
            type(cutoff_ms) is not int or cutoff_ms<=0):
        raise ContractError("V2_CLOSED_SOURCE_REQUEST")
    paths=sorted(root.glob("*/plan.json"))
    if len(paths)>MAX_CLOSED_PLANS:
        raise ContractError("V2_CLOSED_SOURCE_PLAN_CAP")
    pending_excluded=0;closed_plans=0;rows=[];seen=set()
    for pf in paths:
        directory=pf.parent
        qf=directory/"qualification.json"
        if not qf.is_file():
            pending_excluded+=1
            continue
        plan=read_json(pf)
        q=read_json(qf)
        if (plan.get("plan_id")!=digest({k:v for k,v in plan.items() if k!="plan_id"}) or
                q.get("plan_id")!=plan["plan_id"] or
                q.get("candidate_id")!=plan.get("candidate_id") or
                q.get("state") not in ("REJECTED","QUALIFIED_AWAITING_CANONICAL_OWNER")):
            raise ContractError("V2_CLOSED_SOURCE_PLAN_IDENTITY")
        # A trial closed later than the pinned training cutoff CANNOT become
        # training data merely because it is closed at audit time.
        if (type(plan.get("outcome_end_ms")) is not int or
                plan["outcome_end_ms"]>cutoff_ms):
            continue
        closed_plans+=1
        spacing=plan["rule"]["slot_spacing_ms"]
        horizon=plan["rule"]["horizon_ms"]
        for of in sorted(directory.glob("outcome-*.json")):
            outcome=read_json(of,64*1024)
            if (outcome.get("plan_id")!=plan["plan_id"] or
                    outcome.get("candidate_id")!=plan["candidate_id"] or
                    type(outcome.get("slot")) is not int):
                raise ContractError("V2_CLOSED_SOURCE_OUTCOME_IDENTITY")
            if outcome.get("status")!="OBSERVED":
                continue
            i=outcome["slot"]
            if not 0<=i<plan["rule"]["slots"]:
                raise ContractError("V2_CLOSED_SOURCE_SLOT_RANGE")
            pp=directory/("prediction-%03d.json"%i)
            if (not pp.is_file() or
                    outcome.get("prediction_file_sha256")!=sha_file(pp)):
                raise ContractError("V2_CLOSED_SOURCE_PREDICTION_CHANGED")
            pred=read_json(pp,32*1024)
            anchor=pred.get("anchor_ms")
            start=plan["start_ms"]+i*spacing
            if (pred.get("parent_id")!=parent_id or
                    pred.get("plan_id")!=plan["plan_id"] or
                    pred.get("candidate_id")!=plan["candidate_id"] or
                    pred.get("slot")!=i or type(anchor) is not int or
                    not start<=anchor<=start+plan["rule"]["input_grace_ms"] or
                    type(pred.get("created_at_ms")) is not int or
                    not anchor<=pred["created_at_ms"]<anchor+horizon or
                    pred.get("trained_until_ms",anchor+1)>anchor or
                    type(outcome.get("evaluated_at_ms")) is not int or
                    type(outcome.get("outcome_ms")) is not int or
                    not anchor+horizon<=outcome["outcome_ms"]<=outcome["evaluated_at_ms"]<=cutoff_ms):
                raise ContractError("V2_CLOSED_SOURCE_PREDICTION_NONCAUSAL")
            state=pred.get("input")
            if (not isinstance(state,dict) or state.get("anchor_ms")!=anchor or
                    state.get("horizon_sec")!=horizon//1000 or
                    state.get("instrument")!="EUR.USD" or
                    pred.get("input_id")!=digest(state) or
                    state.get("quantity")!="underlying MID return; not execution profit"):
                raise ContractError("V2_CLOSED_SOURCE_INPUT_CHANGED")
            quotes=state.get("quotes")
            if (not isinstance(quotes,list) or not 6<=len(quotes)<=24 or
                    state.get("columns")!=["offset_ms","bid","ask"] or
                    quotes[-1][0]!=0 or
                    any(type(q) is not list or len(q)!=3 or
                        type(q[0]) is not int or
                        not all(isinstance(v,str) for v in q[1:])
                        for q in quotes)):
                raise ContractError("V2_CLOSED_SOURCE_UNSUPPORTED_QUOTE_INPUT")
            offsets=[q[0] for q in quotes]
            if (offsets!=sorted(set(offsets)) or offsets[-1]!=0 or
                    offsets[0]<-600000 or
                    any(x>0 for x in offsets)):
                raise ContractError("V2_CLOSED_SOURCE_NONCAUSAL_QUOTE_HISTORY")
            try:
                values=[(Decimal(q[1]),Decimal(q[2])) for q in quotes]
                reference=Decimal(str(state["reference_mid"]))
                last=(values[-1][0]+values[-1][1])/2
            except (KeyError,TypeError,ValueError,InvalidOperation) as exc:
                raise ContractError("V2_CLOSED_SOURCE_BAD_QUOTE_DECIMAL") from exc
            if (not all(bid.is_finite() and ask.is_finite() and
                        0<bid<=ask for bid,ask in values) or
                    not reference.is_finite() or
                    abs(reference-last)>Decimal("0.000000000001") or
                    abs(reference-Decimal(str(pred["reference_mid"])))>
                        Decimal("0.000000000001")):
                raise ContractError("V2_CLOSED_SOURCE_QUOTE_PRICE_CHANGED")
            receipt=outcome.get("outcome_receipt")
            if (type(receipt) is not str or len(receipt)!=64 or
                    any(c not in "0123456789abcdef" for c in receipt)):
                raise ContractError("V2_CLOSED_SOURCE_OUTCOME_RECEIPT_CHANGED")
            probs=probabilities(pred["parent_probs"],7)
            act=probabilities(pred["candidate_act_escalate_probs"],2)
            target=outcome.get("target_class")
            ret=outcome.get("return_bps")
            if (type(target) is not int or not 0<=target<7 or
                    type(ret) not in (int,float) or not math.isfinite(ret) or
                    target!=bisect_right(BIN_EDGES_BPS,ret)):
                raise ContractError("V2_CLOSED_SOURCE_PRICE_LABEL_CHANGED")
            expected_brier=sum((a-float(k==target))**2 for k,a in enumerate(probs))
            parent_correct=max(range(7),key=lambda k:probs[k])==target
            expected_action_brier=2*(act[0]-float(parent_correct))**2
            if (abs(expected_brier-outcome["parent_brier"])>1e-5 or
                    abs(expected_action_brier-outcome["action_head_brier"])>1e-5):
                raise ContractError("V2_CLOSED_SOURCE_SCORING_CHANGED")
            rowkey=(anchor,horizon)
            if rowkey in seen:
                raise ContractError("V2_CLOSED_SOURCE_DUPLICATE_TRAINING_ANCHOR")
            seen.add(rowkey)
            if len(rows)>=MAX_OBS:
                raise ContractError("V2_CLOSED_SOURCE_ROW_CAP")
            row={
                "schema":"heptatrader.laya-v2-closed-parent-row.v1",
                "parent_id":parent_id,
                "plan_id":plan["plan_id"],"slot":i,
                "row_id":digest([plan["plan_id"],i,sha_file(pp),sha_file(of)]),
                "anchor_ms":anchor,"future_outcome_available_ms":outcome["evaluated_at_ms"],
                "state":state,"question":question(horizon//1000),
                "target_class":target,
                "parent_correct":parent_correct,
                "frozen_parent_probs":list(probs),
                "input_id":pred["input_id"],"prediction_sha256":sha_file(pp),
                "outcome_sha256":sha_file(of),
                "original_outcome_receipt":outcome["outcome_receipt"],
                "broker_orders_sent":0}
            rows.append(row)
    rows.sort(key=lambda row:(row["anchor_ms"],row["row_id"]))
    good=sum(r["parent_correct"] for r in rows)
    n=len(rows)
    manifest={"schema":SCHEMA,"frozen_parent_id":parent_id,
       "training_cutoff_ms":cutoff_ms,
       "closed_plans_in_scope":closed_plans,
       "pending_plans_excluded":pending_excluded,
       "training_rows":n,
       "frozen_parent_correct_ACT":good,
       "frozen_parent_wrong_ESCALATE":n-good,
       "action_training_two_class_supported":(n>=16 and good>=4 and n-good>=4),
       "chronological_train_only":True,
       "no_pending_trial_outcomes_included":True,
       "not_independent_future_evaluation":True,
       "original_predictions_and_quotes_not_modified":True,
       "model_or_trading_authority":False,
       "rows_digest":digest(rows)}
    manifest["manifest_id"]=digest(manifest)
    return {"manifest":manifest,"rows":rows}


def check_train_source(artifact):
    if (not isinstance(artifact,dict) or
            not isinstance(artifact.get("manifest"),dict) or
            not isinstance(artifact.get("rows"),list)):
        raise ContractError("V2_CLOSED_TRAIN_SOURCE_FORMAT")
    m=artifact["manifest"]
    if (m.get("schema")!=SCHEMA or
            m.get("manifest_id")!=digest({k:v for k,v in m.items() if k!="manifest_id"}) or
            m.get("rows_digest")!=digest(artifact["rows"]) or
            m.get("training_rows")!=len(artifact["rows"]) or
            m.get("model_or_trading_authority") is not False or
            m.get("no_pending_trial_outcomes_included") is not True or
            m.get("not_independent_future_evaluation") is not True):
        raise ContractError("V2_CLOSED_TRAIN_SOURCE_CHANGED")
    ids=set()
    for r in artifact["rows"]:
        if (r.get("parent_id")!=m["frozen_parent_id"] or
                type(r.get("row_id")) is not str or r["row_id"] in ids or
                r.get("future_outcome_available_ms",m["training_cutoff_ms"]+1)>
                     m["training_cutoff_ms"]):
            raise ContractError("V2_CLOSED_TRAIN_ROW_TAMPERED")
        ids.add(r["row_id"])
    return m
