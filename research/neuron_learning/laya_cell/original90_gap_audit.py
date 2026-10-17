"""Fixed five-minute ORIGINAL quote gap diagnostics; read-only research.

No interpolation, label backfill, automatic trading, price-target inference,
or claim that a posthoc complete bar was available at original decision time.
"""
from __future__ import annotations

from collections import Counter
import math

from .integrity import ContractError

BIN_MS = 300000
BARS = 13
MIN_COUNT = 8
START_GRACE_MS = 15000
END_GRACE_MS = 15000
MAX_INTERQUOTE_MS = 30000


def bar_diagnostic(records, start_ms, *, asof_ms=None):
    """Explain which ORIGINAL completeness condition failed for one 5m bar."""
    if type(start_ms) is not int or start_ms < 0 or start_ms % BIN_MS:
        raise ContractError("GAP_AUDIT_INVALID_BAR_START")
    if asof_ms is not None and (type(asof_ms) is not int or asof_ms <= 0):
        raise ContractError("GAP_AUDIT_ASOF_TIME")
    if not isinstance(records, list) or len(records) > 20000:
        raise ContractError("GAP_AUDIT_QUOTE_BOUND")
    q=[]
    seen=set()
    for r in records:
        if not isinstance(r,dict) or type(r.get("t")) is not int or type(r.get("known_at_ms")) is not int:
            raise ContractError("GAP_AUDIT_QUOTE_TIME")
        if r["t"] in seen:
            raise ContractError("GAP_AUDIT_DUPLICATE_SOURCE_QUOTE")
        seen.add(r["t"])
        if r["t"] < start_ms or r["t"] >= start_ms+BIN_MS:
            raise ContractError("GAP_AUDIT_FOREIGN_BAR")
        if asof_ms is None or (r["t"]<=asof_ms and r["known_at_ms"]<=asof_ms):
            q.append(r)
    q.sort(key=lambda x:x["t"])
    t=[x["t"] for x in q]
    gaps=[b-a for a,b in zip(t,t[1:])]
    reasons=[]
    if len(t)<MIN_COUNT:reasons.append("INSUFFICIENT_REAL_QUOTES")
    if not t:reasons.extend(("NO_FIRST_ORIGINAL_QUOTE","NO_TERMINAL_ORIGINAL_QUOTE"))
    else:
        if t[0]>start_ms+START_GRACE_MS:reasons.append("ORIGINAL_BAR_START_UNCOVERED")
        if t[-1]<start_ms+BIN_MS-END_GRACE_MS:reasons.append("ORIGINAL_BAR_END_UNCOVERED")
        if gaps and max(gaps)>MAX_INTERQUOTE_MS:reasons.append("ORIGINAL_INTERNAL_QUOTE_GAP")
    return {"bar_start_ms":start_ms,"quote_count":len(t),
            "first_lag_ms":None if not t else t[0]-start_ms,
            "end_gap_ms":None if not t else start_ms+BIN_MS-t[-1],
            "max_interquote_gap_ms":max(gaps,default=0),
            "complete":not reasons,"reasons":reasons}


def impacted_anchors(bad_starts, slot_anchors):
    """13 contiguous *prior* bars are essential for an ORIGINAL input.

    This is a diagnostic about eligibility, NEVER a retrospective
    replacement for original CENSORED/OBSERVED receipts.
    """
    if len(set(bad_starts))!=len(bad_starts):
        raise ContractError("GAP_AUDIT_DUPLICATE_FAILED_BAR")
    if any(type(t) is not int or t<0 or t%BIN_MS for t in bad_starts):
        raise ContractError("GAP_AUDIT_FAILED_BAR_TIME")
    if any(type(t) is not int or t<0 or t%BIN_MS for t in slot_anchors):
        raise ContractError("GAP_AUDIT_INPUT_ANCHOR_TIME")
    bad=set(bad_starts)
    return {int(t):[t-(i+1)*BIN_MS for i in range(BARS) if t-(i+1)*BIN_MS in bad]
            for t in slot_anchors}


def failure_code(value):
    if not isinstance(value,str) or not 1<=len(value)<=96:
        return "UNCLASSIFIED_FAILURE"
    if any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ_0123456789" for c in value):
        return "UNCLASSIFIED_FAILURE"
    return value


def source_bar_groups(quotes,start_ms,end_ms):
    if start_ms>=end_ms or start_ms%BIN_MS or end_ms%BIN_MS:
        raise ContractError("GAP_AUDIT_INTERVAL")
    by={i:[] for i in range(start_ms,end_ms,BIN_MS)}
    for q in quotes:
        if type(q.get("t")) is not int:raise ContractError("GAP_AUDIT_SOURCE_QUOTE_TIME")
        k=q["t"]//BIN_MS*BIN_MS
        if k in by:by[k].append(q)
    return {str(t):bar_diagnostic(by[t],t) for t in by}
