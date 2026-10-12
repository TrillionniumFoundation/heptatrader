"""On-demand, post-maturity prospective PRICE-only 4-horizon observation.

No model, order, broker API or independent Owner. A missing latest unsealed
source remains pending; an absent observed endpoint never becomes zero PnL.
"""
from __future__ import annotations

import argparse
from decimal import Decimal
import json
from pathlib import Path
import time

from hepta_decision_attribution import read_private, strict
from .contracts import ContractError, digest
from .original_multihorizon import (
    HORIZONS_MIN, ordered_quote_index, label_after,
)
from .postmodel_requote import load_original_quote_archive
from .prospective_multihorizon import (
    INPUT_SCHEMA, SLOT_COUNT, _load, _persist,
)

OUTCOME_SCHEMA = "heptatrader.train-v2-prospective-original-price-outcome-v1"
MIN_OUTCOME_GRACE_MS = 5000

def _check(ok, code):
    if not ok:
        raise ContractError(code)

def pending_reason(*, original_covered, quote_in_archive):
    if not quote_in_archive:
        return "WAIT_INPUT_ORIGINAL_SEGMENT_SEAL"
    if not original_covered:
        return "WAIT_END_OF_ORIGINAL_SEALED_COVERAGE"
    return "OUTCOME_READY_TO_SCORE"

def make_result(campaign, frozen, quote, horizon_min, index):
    """Pure deterministic outcome calculation from *validated* original archive."""
    _check(horizon_min in HORIZONS_MIN
           and frozen["slot"] in range(SLOT_COUNT)
           and frozen["campaign_id"] == campaign["campaign_id"],
           "PROSPECTIVE_OUTCOME_IDENTITY")
    decision=frozen["decision_at_ms"]
    due=decision+horizon_min*60000
    _check(frozen["outcome_maturity_ms"].get(str(horizon_min))==due,
           "PROSPECTIVE_OUTCOME_DUE_CHANGED")
    _check(quote[3]==frozen["original_source_id"]
           and quote[0]==frozen["source_available_at_ms"]
           and quote[1]==frozen["source_event_at_ms"]
           and quote[2]==frozen["source_valid_until_ms"]
           and str(quote[4])==frozen["input_quote_midpoint"],
           "PROSPECTIVE_OUTCOME_ORIGINAL_INPUT_CHANGED")
    state,price_change=label_after(index,quote,decision,horizon_min*60000)
    if state=="OBSERVED_RETROSPECTIVE_PRICE_ONLY":
        status="OBSERVED_NEW_ORIGINAL_MARKET_PRICE_ONLY"
        value=str(price_change)
    else:
        _check(state.startswith("CENSORED_"),
               "PROSPECTIVE_OUTCOME_INVALID_STATE")
        status=state
        value=None
    outcome={
        "schema":OUTCOME_SCHEMA,
        "campaign_id":campaign["campaign_id"],
        "slot":frozen["slot"],
        "horizon_min":horizon_min,
        "preoutcome_input_id":frozen["input_id"],
        "original_source_quote_id":quote[3],
        "decision_at_ms":decision,
        "due_at_ms":due,
        "outcome_state":status,
        "original_midprice_change_bps":value,
        "price_outcome_is_not_real_execution_pnl":True,
        "not_a_model_prediction":True,
        "not_an_independent_broker_signature":True,
        "no_trade_reference_net_pnl_usd":"0",
        "actual_ib_fills_or_slippage_attested":False,
        "independent_observer_signed":False,
        "canonical_owner_enrolled":False,
        "model_selection_permitted":False,
        "trading_authority":False,
        "broker_orders_sent":0,
    }
    return {**outcome,"outcome_id":digest(outcome)}

def observe(root, slot, horizon_min, *, now_ms=None):
    campaign=_load(root)
    _check(type(slot) is int and 0<=slot<SLOT_COUNT
           and type(horizon_min) is int and horizon_min in HORIZONS_MIN,
           "PROSPECTIVE_OUTCOME_SLOT_OR_HORIZON")
    input_path=Path(root)/("slot-%02d.input.json"%slot)
    _check(not input_path.is_symlink() and input_path.is_file(),
           "PROSPECTIVE_OUTCOME_NO_ORIGINAL_INPUT")
    frozen=strict(read_private(input_path,128*1024))
    _check(frozen.get("schema")==INPUT_SCHEMA
           and frozen.get("input_id")==digest({
               k:v for k,v in frozen.items() if k!="input_id"})
           and frozen.get("campaign_id")==campaign["campaign_id"]
           and frozen.get("slot")==slot
           and frozen.get("all_future_labels_unobserved") is True,
           "PROSPECTIVE_ORIGINAL_PREOUTCOME_INPUT_CHANGED")
    now=time.time_ns()//1_000_000 if now_ms is None else now_ms
    due=frozen["outcome_maturity_ms"].get(str(horizon_min))
    _check(type(now) is int and type(due) is int
           and now>=due+MIN_OUTCOME_GRACE_MS,
           "PROSPECTIVE_OUTCOME_NOT_MATURE")
    output=Path(root)/("slot-%02d-h%02d.outcome.json"%(slot,horizon_min))
    _check(not output.is_symlink() and not output.exists(),
           "PROSPECTIVE_OUTCOME_ALREADY_FINAL")
    # Only fully sealed NUMERIC original source segments are consumed.
    # The latest active, unsealed file is explicitly excluded by the old
    # verified original source reader; results are never labeled using it.
    archive=load_original_quote_archive(
        campaign["source_root"],campaign["period_sha256"])
    index=ordered_quote_index(archive["rows"])
    originals=[r for r in index["known"]
               if r[3]==frozen["original_source_id"]]
    if len(originals)==0:
        return {"state":"WAIT_INPUT_ORIGINAL_SEGMENT_SEAL",
                "outcome_written":False,"broker_orders_sent":0}
    _check(len(originals)==1,
           "PROSPECTIVE_OUTCOME_DUPLICATE_ORIGINAL_QUOTE")
    if index["known_times"][-1]<due+MIN_OUTCOME_GRACE_MS:
        return {"state":"WAIT_END_OF_ORIGINAL_SEALED_COVERAGE",
                "outcome_written":False,"broker_orders_sent":0}
    result=make_result(campaign,frozen,originals[0],horizon_min,index)
    result["observed_at_ms"]=now
    result["original_scanned_source_fingerprint_sha256"]=archive["source_fingerprint_sha256"]
    result["sealed_source_segment_count"]=archive["sealed_segments"]
    result["result_id"]=digest(result)
    _persist(output,result)
    return result

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--root",type=Path,required=True)
    ap.add_argument("--slot",type=int,required=True)
    ap.add_argument("--horizon-min",type=int,required=True)
    opts=ap.parse_args()
    value=observe(opts.root,opts.slot,opts.horizon_min)
    print(json.dumps({
        "state":value.get("outcome_state",value.get("state")),
        "outcome_written":value.get("outcome_written", "outcome_state" in value),
        "is_price_only":True,"broker_orders_sent":0,
        "trading_authority":False
    },sort_keys=True))

if __name__=="__main__":
    main()
