"""Price-only cost-feasibility ceiling for the frozen, original-source FX cohort.

Research diagnostics only. An omniscient direction is a *hypothetical* ceiling
on midpoint direction, not a prediction, executable IB fill, positive alpha,
license to train, or strategy/promotion authority. No broker sockets/commands.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
import os
from pathlib import Path
import stat

from historical_training.common import read, sha

SCHEMA = "heptatrader.fx-original-midpoint-oracle-cost-ceiling.v1"
MODEL_SCHEMA = "heptatrader.fx-ridge-historical-model.v1"
DATA_SCHEMA = "heptatrader.fx-original-causal-features-and-maturity.v1.private-examples.v1"
CONTRACT_SCHEMA = "heptatrader.fx-original-causal-features-and-maturity.v1"
MIN_NONOVERLAP_MS = 80 * 60 * 1000
HORIZON_MS = 900000
DECISION_DELAY_MS = 1000

class CeilingError(ValueError):
    pass

def require(value, reason):
    if not value:
        raise CeilingError(reason)

def private_json(path, limit=2*1024*1024):
    path = Path(path)
    require(path.is_absolute() and not path.is_symlink(),
            "PRIVATE_INPUT_PATH")
    st = path.stat()
    require(stat.S_ISREG(st.st_mode) and st.st_uid == os.geteuid()
            and st.st_nlink == 1 and stat.S_IMODE(st.st_mode)&0o077 == 0
            and 0 < st.st_size <= limit, "PRIVATE_INPUT_NOT_ATTESTED")
    # Read once, then verify that the same original bytes remain.
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        before = os.fstat(fd)
        payload = bytearray()
        while len(payload) < before.st_size:
            data = os.read(fd, min(1024*1024, before.st_size-len(payload)))
            require(bool(data), "PRIVATE_INPUT_TRUNCATED")
            payload.extend(data)
        after = os.fstat(fd)
        require((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns),
                "PRIVATE_INPUT_CHANGED")
    finally:
        os.close(fd)
    def unique(pairs):
        obj = {}
        for k,v in pairs:
            require(k not in obj, "DUPLICATE_INPUT_KEY")
            obj[k]=v
        return obj
    try:
        parsed = json.loads(payload, object_pairs_hook=unique,
            parse_constant=lambda x: (_ for _ in ()).throw(CeilingError("NONFINITE_JSON")))
    except (UnicodeError, json.JSONDecodeError) as ex:
        raise CeilingError("INPUT_JSON_INVALID") from ex
    return parsed, hashlib.sha256(payload).hexdigest()

def decimal(x):
    require(type(x) is str, "DECIMAL_STRING_REQUIRED")
    try:
        v=Decimal(x)
    except (InvalidOperation, ValueError) as exc:
        raise CeilingError("DECIMAL_PARSE") from exc
    require(v.is_finite() and abs(v) < 1_000_000, "DECIMAL_RANGE")
    return v

def percentile(v, p):
    require(v and 0 <= p <= 100, "PERCENTILE_DATA")
    q=sorted(v)
    idx=(len(q)-1)*Decimal(p)/100
    lo=int(idx)
    hi=min(lo+1, len(q)-1)
    return str(q[lo]+(q[hi]-q[lo])*(idx-lo))

def frozen_predict(model, features):
    mu,sd,w=[model[k] for k in ("mean","stddev","weights")]
    require(len(mu)==len(sd)==len(features)==8 and len(w)==9,
            "FROZEN_MODEL_DIMENSION")
    require(all(type(x) in (float,int) and math.isfinite(x) for x in
                (*mu,*sd,*w,*features)) and all(x>0 for x in sd),
            "NONFINITE_OR_ZERO_FROZEN_MODEL")
    pred = w[0] + sum(w[i+1]*max(-5.,min(5.,(features[i]-mu[i])/sd[i]))
                       for i in range(8))
    require(math.isfinite(pred), "NONFINITE_PREDICTION")
    return pred

def original_rows(examples, contract, model):
    require(examples.get("schema")==DATA_SCHEMA
            and examples.get("research_only") is True
            and examples.get("retrospective_not_an_oos_test") is True
            and examples.get("model_fit_authorized") is False,
            "ORIGINAL_DATASET_NOT_RESEARCH_ONLY")
    require(contract.get("schema")==CONTRACT_SCHEMA
            and contract.get("research_model_fit_admission") is False
            and contract.get("trade_authority") is False
            and contract.get("model_promotion_allowed") is False,
            "ORIGINAL_CONTRACT_NOT_HELD")
    require(examples.get("original_preseal_source_prefixes_sha256")
            == contract.get("original_segment_prefixes_sha256"),
            "ORIGINAL_QUOTE_PREFIX_IDENTITY")
    rows=examples.get("examples")
    require(type(rows) is list and 0<len(rows)<=2048
            and len(rows)==contract["native_mature_price_development_counts"]["mature_price_only_rows"],
            "PRICE_ONLY_SAMPLE_COUNT")
    require(model.get("schema")==MODEL_SCHEMA
            and model.get("historical_validation_selected_action")=="NO_TRADE"
            and model.get("candidate_has_positive_validation_edge") is False
            and model.get("trade_authority") is False
            and model.get("promotion_allowed") is False,
            "HISTORICAL_MODEL_NOT_HELD")
    require(contract["required_nonoverlap_for_any_new_research_model_fit"] >= 1024,
            "MODEL_FIT_THRESHOLD_CHANGED")
    seen, last = set(), -1
    result=[]
    cutoff=contract["preseal_cutoff_ms"]
    for row in rows:
        require(row.get("schema")==CONTRACT_SCHEMA+".example.v1"
                and row.get("retrospectively_observed") is True
                and row.get("same_original_source_domain") is True
                and row.get("actual_executable_fill") is False
                and row.get("monetary_reward") is False
                and row.get("independent_prospective_prediction") is False,
                "EXECUTION_OR_PROSPECTIVE_LABEL_MISATTRIBUTED")
        anchor=row.get("anchor_ms")
        due=row.get("mature_due_ms")
        event=row.get("mature_original_quote_event_ms")
        avail=row.get("mature_original_quote_available_ms")
        quote_id=row.get("mature_original_quote_id")
        ids=row.get("original_input_quote_ids")
        require(type(anchor) is int and anchor%300000==0
                and anchor>last and anchor not in seen and due == anchor+HORIZON_MS,
                "FROZEN_ORIGINAL_PRICE_ANCHOR_CHANGED")
        seen.add(anchor);last=anchor
        require(row.get("decision_delay_ms")==DECISION_DELAY_MS
                and type(row.get("input_last_quote_available_at_ms")) is int
                and type(row.get("input_last_quote_valid_until_ms")) is int,
                "ORIGINAL_DECISION_SCOPE_CHANGED")
        decision=anchor+DECISION_DELAY_MS
        require(0<=decision-row["input_last_quote_available_at_ms"]<=4500
                and decision<row["input_last_quote_valid_until_ms"],
                "FROZEN_ORIGINAL_INPUT_QUOTE_EXPIRED")
        require(type(event) is int and type(avail) is int
                and due <= event <= due+5000
                and event <= avail <= cutoff
                and due < cutoff,
                "MATURE_PRICE_WAS_NOT_PRESEAL_ORIGINAL")
        require(type(quote_id) is str and len(quote_id)==64
                and type(ids) is list and len(ids)==13
                and len(set(ids))==13
                and all(type(x) is str and len(x)==64 for x in ids),
                "ORIGINAL_QUOTE_IDENTITIES_INVALID")
        feat=row.get("features")
        require(type(feat) is list, "CAUSAL_FEATURES_REQUIRED")
        pred=frozen_predict(model,feat)
        target=decimal(row["price_only_midpoint_return_bps"])
        result.append({"anchor":anchor,"prediction":Decimal(str(pred)),
                       "price_move":target})
    return result

def analyze(rows, fee_p90, spread_p99, *, min_rows, pinned_nonoverlap):
    require(rows and 0 < len(rows)<=2048, "SAMPLE_POPULATION_INVALID")
    fee,spread=decimal(fee_p90),decimal(spread_p99)
    require(fee>0 and spread>0, "COST_NOT_POSITIVE")
    # Frozen, pre-outcome assumptions from the old Ridge:
    # stressed full spread once, both one-way commissions, 2 bps
    # hypothetical slippage per side, and extra 2 bps safety margin.
    base_cost=spread+2*fee+Decimal("4")
    hurdle=base_cost+Decimal("2")
    last=-10**30
    nonoverlap=[]
    for row in rows:
        if row["anchor"]>=last+MIN_NONOVERLAP_MS:
            nonoverlap.append(row)
            last=row["anchor"]
    require(len(nonoverlap)==pinned_nonoverlap,
            "NONOVERLAP_POPULATION_CHANGED")
    def group(records):
        abs_move=[abs(x["price_move"]) for x in records]
        abs_prediction=[abs(x["prediction"]) for x in records]
        errors=[abs(x["price_move"]-x["prediction"]) for x in records]
        return {
            "count":len(records),
            "zero_change_reference_mae_bps":str(sum(abs_move)/len(records)),
            "frozen_ridge_mae_bps":str(sum(errors)/len(records)),
            "frozen_ridge_direction_correct":
                sum((x["prediction"]>0)==(x["price_move"]>0)
                    for x in records if x["prediction"]!=0 and x["price_move"]!=0),
            "frozen_ridge_nonzero_direction_evaluated":
                sum(x["prediction"]!=0 and x["price_move"]!=0 for x in records),
            "oracle_perfect_direction_price_move_clears_assumed_cost_count":
                sum(a>base_cost for a in abs_move),
            "oracle_perfect_direction_price_move_clears_assumed_cost_plus_margin_count":
                sum(a>hurdle for a in abs_move),
            "frozen_ridge_prediction_magnitude_clears_original_hurdle_count":
                sum(a>hurdle for a in abs_prediction),
            "observed_absolute_midpoint_move_bps": {
                "p50":percentile(abs_move,50),
                "p90":percentile(abs_move,90),
                "p95":percentile(abs_move,95),
                "p99":percentile(abs_move,99),
                "max":str(max(abs_move)),
            },
            "observed_absolute_frozen_ridge_forecast_bps":{
                "p50":percentile(abs_prediction,50),
                "p95":percentile(abs_prediction,95),
                "max":str(max(abs_prediction)),
            },
        }
    require(len(nonoverlap)<min_rows,
            "EXPLORATORY_CEILING_CANNOT_OVERRIDE_ADMISSION")
    return {
        "all_overlapping_original_research_price_examples":group(rows),
        "independent_window_population_80min_label_blind_greedy":group(nonoverlap),
        "frozen_cost_assumptions_bps":{
            "historical_spread_p99_full_bps":str(spread),
            "historical_commission_p90_oneway_bps":str(fee),
            "hypothetical_slippage_two_sides_bps":"4",
            "extra_safety_margin_bps":"2",
            "assumed_broker_cost_roundtrip_bps":str(base_cost),
            "original_frozen_required_absolute_prediction_bps":str(hurdle),
        },
        "training_nonoverlap_min_required":min_rows,
        "model_fit_admission":False,
        "upper_bound_scope":"RETROSPECTIVE_PERFECT_DIRECTION_MIDPOINT_WITH_FIXED_COST_PROXY_ONLY",
        "oracle_is_predictable_or_executable":False,
        "current_model_action":"NO_TRADE",
        "source_selection_bias_remains":True,
        "no_profit_or_statistical_power_claim":True,
    }

def run(*,examples_path,contract_path,model_path,cost_path,
        campaign_seal_path,drift_path,output_path):
    paths=[Path(x) for x in (examples_path,contract_path,model_path,
                             cost_path,campaign_seal_path,drift_path)]
    (examples,ex_sha),(contract,contract_sha),(model,model_sha),(
        cost,cost_sha),(_,campaign_sha),(_,drift_sha)=[
            private_json(p,limit=(2*1024*1024 if i==0 else 256*1024))
            for i,p in enumerate(paths)]
    require(ex_sha==contract.get("private_examples_sha256")
            and model_sha==contract.get("frozen_original_ridge_model_sha256")
            and campaign_sha==contract.get("frozen_campaign_seal_sha256")
            and drift_sha==contract.get("source_drift_audit_sha256"),
            "ORIGINAL_RESEARCH_ROOT_HASH_MISMATCH")
    require(cost.get("schema")=="heptatrader.ib-cost-evidence.offline.v1"
            and cost.get("trade_authority") is False
            and cost.get("orders_sent")==0
            and cost.get("fx_fee_missing_or_unproved_executions")==0
            and cost.get("actual_slippage_verified") is False,
            "ORIGINAL_COST_SOURCE_NOT_RESEARCH_ONLY")
    require(contract.get("schema") == CONTRACT_SCHEMA, "ORIGINAL_CAUSAL_CONTRACT_SCHEMA")
    require(model.get("cost_receipt_sha256") == cost_sha
            and model.get("period_sha256") == cost.get("period_source_sha256"),
            "COST_NOT_PINNED_TO_ORIGINAL_MODEL_AND_PAPER_PERIOD")
    rows=original_rows(examples,contract,model)
    values=analyze(rows,
        str(cost["fx_commission_one_way_bps"]["p90"]),
        str(cost["quote_spread_full_bps"]["p99"]),
        min_rows=contract["required_nonoverlap_for_any_new_research_model_fit"],
        pinned_nonoverlap=contract["nonoverlap_80min_candidate_anchor_count"])
    report={
        "schema":SCHEMA,"observed_at_utc":datetime.now(timezone.utc).isoformat(),
        "research_code_sha256":sha(Path(__file__)),
        "source_historical_original_quote_not_broker_signed":True,
        "source_input_sha256":{
            "original_price_examples_sha256":ex_sha,
            "original_causal_contract_sha256":contract_sha,
            "frozen_ridge_file_sha256":model_sha,
            "original_cost_receipt_sha256":cost_sha,
            "old_precommit_campaign_seal_sha256":campaign_sha,
            "old_source_drift_receipt_sha256":drift_sha,
        },
        "source_price_rows_before_original_campaign_seal":len(rows),
        "source_model_trained_on_same_original_domain":False,
        "no_new_broker_queries_or_orders":True,
        "no_model_retraining_or_threshold_optimization":True,
        "no_live_strategy_promotion_or_mutation":True,
        "broker_signed_source_proven":False,
        "native_product_owner_or_witness_admitted":False,
        "trade_authority":False,
        **values,
    }
    output_path=Path(output_path)
    require(output_path.is_absolute() and not output_path.exists()
            and not output_path.is_symlink(),"OUTPUT_ALREADY_EXISTS")
    root=output_path.parent
    root.mkdir(mode=0o700,parents=True,exist_ok=True)
    st=root.stat()
    require(not root.is_symlink() and stat.S_IMODE(st.st_mode)==0o700
            and st.st_uid==os.geteuid(),"OUTPUT_ROOT_NOT_PRIVATE")
    raw=(json.dumps(report,ensure_ascii=False,sort_keys=True,
        indent=2,allow_nan=False)+"\n").encode()
    fd=os.open(output_path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    try:
        with os.fdopen(fd,"wb") as f:
            f.write(raw);f.flush();os.fsync(f.fileno())
        dirfd=os.open(root,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(dirfd)
        finally:os.close(dirfd)
    except Exception:
        output_path.unlink(missing_ok=True)
        raise
    return report

def main():
    p=argparse.ArgumentParser()
    for key in ("examples","contract","model","cost","campaign-seal","drift","output"):
        p.add_argument("--"+key,required=True,type=Path)
    a=p.parse_args()
    r=run(examples_path=a.examples,contract_path=a.contract,
          model_path=a.model,cost_path=a.cost,
          campaign_seal_path=a.campaign_seal,drift_path=a.drift,
          output_path=a.output)
    print(json.dumps({"state":"READONLY_RETROSPECTIVE_ORACLE_COST_CEILING",
        "overlap_rows":r["all_overlapping_original_research_price_examples"]["count"],
        "nonoverlap_rows":r["independent_window_population_80min_label_blind_greedy"]["count"],
        "frozen_required_bps":r["frozen_cost_assumptions_bps"]["original_frozen_required_absolute_prediction_bps"],
        "model_fit_authorized":False,"broker_orders_sent":0},sort_keys=True))
if __name__=="__main__":
    main()
