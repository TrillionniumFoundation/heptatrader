"""Independent read-only original PAPER quote as-of / settlement audit.

The active v1 shadow stores outcome commit time but not source-read start time.
A quote that becomes known during the read can change a later reconstructed
OHLC close. This module checks whether the saved receipt was causally
feasible, while NEVER retroactively replacing it with a later quote.
"""
from __future__ import annotations
from bisect import bisect_right
from math import isfinite

from .dataset import BIN_EDGES_BPS
from .integrity import ContractError, digest
from .price_original_live_features import original_feature_snapshot, original_closed_bars
from .price_original_campaign import HORIZON_MS, INPUT_GRACE_MS


def bars_known_asof(quotes, asof_ms):
    if type(asof_ms) is not int or asof_ms<=0:
        raise ContractError("ASOF_AUDIT_BAD_CLOCK")
    snapshot=[q for q in quotes
              if q["known_at_ms"]<=asof_ms and q["t"]<=asof_ms]
    bars,receipts=original_closed_bars(snapshot,asof_ms)
    return {bar["time"]*1000:(bar,proof)
            for bar,proof in zip(bars,receipts)}


def verify_original_input(pred, quotes, prefixes):
    """Rebuild feature math only. Later source prefix hashes are NOT as-of pins."""
    cutoff=pred.get("source_asof_ms")
    if type(cutoff) is not int:
        raise ContractError("ASOF_AUDIT_INPUT_CLOCK")
    subset=[q for q in quotes if q["known_at_ms"]<=cutoff and q["t"]<=cutoff]
    feature=original_feature_snapshot(subset,prefixes,cutoff,require_fresh=True)
    bars=bars_known_asof(quotes,cutoff)
    last=bars.get(pred["anchor_ms"]-HORIZON_MS)
    if (feature["anchor_ms"]!=pred["anchor_ms"] or last is None or
            feature["historical_feature_version"]!=pred.get(
                "historical_feature_version",feature["historical_feature_version"]) or
            digest(feature["features"])!=pred["feature_vector_digest"] or
            abs(last[0]["midpoint"]["close"]-pred["base_mid"])>1e-10):
        raise ContractError("ASOF_AUDIT_ORIGINAL_INPUT_CHANGED")
    return {"source_asof_ms":cutoff,"historical_features_match":True,
            "original_base_mid_matches":True,
            "feature_math_not_archive_prefix_proof":True}


def verify_original_outcome(pred,outcome,quotes,now_ms):
    """Check feasible *interval* for the source read; no invented exact timestamp.

    A read of the saved last quote at or after the horizon requires its
    known_at time to have passed. No later event-time quote may already be
    known at the cut. The original outcome commit may happen after the read.
    """
    if outcome.get("status")!="OBSERVED":
        raise ContractError("ASOF_AUDIT_ONLY_OBSERVED")
    anchor=pred["anchor_ms"]
    due=anchor+HORIZON_MS
    committed=outcome["at_ms"]
    if (type(committed) is not int or not due<=committed<=due+INPUT_GRACE_MS or
            type(now_ms) is not int or now_ms<committed):
        raise ContractError("ASOF_AUDIT_OUTCOME_TIME")
    population=sorted([q for q in quotes if anchor<=q["t"]<due],
                      key=lambda q:q["t"])
    identified=[q for q in population if q["receipt"]==outcome["outcome_quote_receipt"]]
    if len(identified)!=1:
        return {"status":"RECEIPT_NOT_IN_CURRENT_VERIFIED_SOURCE",
                "slot":outcome["slot"]}
    saved=identified[0]
    later=[q for q in population if q["t"]>saved["t"]]
    first_later_known=min((q["known_at_ms"] for q in later),default=None)
    lower=max(due,saved["known_at_ms"])
    upper=min(committed+1,first_later_known) if first_later_known is not None else committed+1
    feasible=lower<upper
    receipt_rebuilt=False
    if feasible:
        at=bars_known_asof(quotes,lower)
        current=at.get(anchor)
        receipt_rebuilt=bool(current and
            current[1]["last_quote_receipt"]==saved["receipt"] and
            abs(current[0]["midpoint"]["close"]-saved["mid"])<=1e-10)
    actual_bps=(saved["mid"]/pred["base_mid"]-1)*10000
    original_label=bisect_right(BIN_EDGES_BPS,actual_bps)
    price_label_match=(abs(actual_bps-outcome["return_bps"])<=1e-7 and
                      original_label==outcome["actual_class"])
    if not (feasible and receipt_rebuilt and price_label_match):
        return {"status":"INCONSISTENT_CAUSAL_SOURCE_RECEIPT",
                "slot":outcome["slot"],
                "receipt_exists":True,
                "feasible_time_window":feasible,
                "receipt_rebuilt_at_candidate_asof":receipt_rebuilt,
                "return_and_label_match":price_label_match}
    cutoff=due+INPUT_GRACE_MS
    finality={"status":"NOT_YET_MATURE","class_changed":None,
              "return_shift_bps":None,"quote_receipt_changed":None}
    if now_ms>=cutoff:
        late=bars_known_asof(quotes,cutoff)
        matured=late.get(anchor)
        if matured is not None:
            mid=matured[0]["midpoint"]["close"]
            new_bps=(mid/pred["base_mid"]-1)*10000
            new_label=bisect_right(BIN_EDGES_BPS,new_bps)
            finality={"status":"COMPLETE_AT_FIXED_60S_GRACE",
                      "class_changed":new_label!=original_label,
                      "return_shift_bps":new_bps-actual_bps,
                      "quote_receipt_changed":
                          matured[1]["last_quote_receipt"]!=saved["receipt"]}
        else:
            finality={"status":"NOT_STRICTLY_COMPLETE_AT_60S_GRACE",
                      "class_changed":None,"return_shift_bps":None,
                      "quote_receipt_changed":None}
    return {"status":"RECEIPT_MATCH_CAUSAL_ASOF_FEASIBLE",
            "slot":outcome["slot"],"read_clock_lower_bound_ms":lower,
            "read_clock_upper_bound_ms":upper-1,
            "actual_source_read_clock_was_not_logged":True,
            "saved_receipt_known_at_ms":saved["known_at_ms"],
            "first_newer_event_known_at_ms":first_later_known,
            "source_read_can_precede_result_commit":True,
            "saved_target_class":original_label,
            "matured_60s_finality":finality}
