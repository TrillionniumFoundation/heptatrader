"""Causal, fail-closed 24-dimensional research features from original PAPER quotes.

This bridges the historical 5-minute bar FEATURE CONTRACT, not source identity:
sampled original PAPER ticks do NOT establish IB revised-bar equivalence.
No model, broker, trade permission, evaluator, or training ledger is modified.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from math import cos, isfinite, pi, sin
from .integrity import ContractError, digest
from .price_year_gpu import FEATURE_NAMES, FEATURE_VERSION

BIN_MS = 300000
START_BOUND_MS = 15000
END_BOUND_MS = 15000
MAX_GAP_MS = 30000
MIN_QUOTES_PER_BIN = 8
NEEDED_CLOSED_BARS = 13
LATEST_INPUT_GRACE_MS = 60000
MAX_SOURCE_QUOTES = 100000
MAX_BINS = 512


def original_closed_bars(quotes, asof_ms):
    """As-of bar closes only; missing buckets are holes, never interpolated."""
    if type(asof_ms) is not int or asof_ms <= 0:
        raise ContractError("ORIGINAL_FEATURE_ASOF")
    if not isinstance(quotes, list) or len(quotes) > MAX_SOURCE_QUOTES:
        raise ContractError("ORIGINAL_FEATURE_SOURCE_CAPACITY")
    groups = defaultdict(list)
    seen = set()
    for q in quotes:
        if not isinstance(q, dict):
            raise ContractError("ORIGINAL_FEATURE_QUOTE_SHAPE")
        t = q.get("t")
        known = q.get("known_at_ms")
        if (type(t) is not int or type(known) is not int or t<=0 or known<=0
                or t in seen):
            raise ContractError("ORIGINAL_FEATURE_TIME_OR_ID")
        seen.add(t)
        b=q.get("bid");a=q.get("ask");m=q.get("mid")
        if (type(b) not in (int,float) or type(a) not in (int,float)
                or type(m) not in (int,float) or
                not all(isfinite(z) for z in (b,a,m)) or
                not 0 < b <= a or abs(m-(b+a)*0.5)>0.00012):
            raise ContractError("ORIGINAL_FEATURE_CROSSED_OR_INVALID")
        if t > asof_ms or known > asof_ms:
            continue
        start=(t//BIN_MS)*BIN_MS
        if start+BIN_MS > asof_ms:
            continue
        groups[start].append(q)
    if len(groups)>MAX_BINS:
        raise ContractError("ORIGINAL_FEATURE_BAR_CAPACITY")
    bars=[];evidence=[]
    for start,rs in sorted(groups.items()):
        rs.sort(key=lambda q:q["t"])
        times=[q["t"] for q in rs]
        if (len(rs)<MIN_QUOTES_PER_BIN or
                times[0]>start+START_BOUND_MS or
                times[-1]<start+BIN_MS-END_BOUND_MS or
                any(y-x>MAX_GAP_MS for x,y in zip(times,times[1:]))):
            continue
        last=rs[-1]
        mids=[q["mid"] for q in rs]
        bars.append({"time":start//1000,
          "bid":{"close":float(last["bid"])},
          "ask":{"close":float(last["ask"])},
          "midpoint":{"close":float(last["mid"]),"high":float(max(mids)),"low":float(min(mids))}})
        evidence.append({"bar_start_ms":start,"bar_end_ms":start+BIN_MS,
          "first_ms":times[0],"last_ms":times[-1],"n_quotes":len(rs),
          "last_quote_receipt":last.get("receipt"),"max_known_at_ms":max(q["known_at_ms"] for q in rs)})
    return bars,evidence


def features_at_closed_bar(bars):
    """Produce features using 13 already complete bars; future label not read."""
    if len(bars)<NEEDED_CLOSED_BARS:
        raise ContractError("ORIGINAL_FEATURE_INSUFFICIENT_CLOSED_HISTORY")
    last=bars[-NEEDED_CLOSED_BARS:]
    times=[q["time"] for q in last]
    if (any(type(t) is not int for t in times) or
            any(t-s!=300 for s,t in zip(times,times[1:]))):
        raise ContractError("ORIGINAL_FEATURE_NONCONTIGUOUS_BARS")
    mids=[]
    for bar in last:
        try:
            b=float(bar["bid"]["close"])
            a=float(bar["ask"]["close"])
            m=float(bar["midpoint"]["close"])
            hi=float(bar["midpoint"]["high"])
            lo=float(bar["midpoint"]["low"])
        except (KeyError,ValueError,TypeError) as ex:
            raise ContractError("ORIGINAL_FEATURE_BAR_SHAPE") from ex
        if (not all(isfinite(v) for v in (b,a,m,hi,lo)) or
                not 0 < b <= a or not 0 < lo <= m <= hi or
                abs(m-(a+b)*0.5)>.00012):
            raise ContractError("ORIGINAL_FEATURE_BAR_PRICE")
        mids.append(m)
    previous=[(mids[i]/mids[i-1]-1)*10000 for i in range(1,NEEDED_CLOSED_BARS)]
    cur=last[-1];mid=mids[-1]
    anchor_ms=(times[-1]+300)*1000
    dt=datetime.fromtimestamp(anchor_ms/1000,timezone.utc)
    hour=dt.hour+dt.minute/60
    x=(previous+
      [sum(previous[-k:])/k for k in (3,6,12)]+
      [sum(abs(v) for v in previous[-k:])/k for k in (3,6,12)]+
      [(float(cur["midpoint"]["high"])-float(cur["midpoint"]["low"]))/mid*10000,
       (float(cur["ask"]["close"])-float(cur["bid"]["close"]))/mid*10000,
       sin(2*pi*hour/24),cos(2*pi*hour/24),
       sin(2*pi*dt.weekday()/7),cos(2*pi*dt.weekday()/7)])
    if len(x)!=len(FEATURE_NAMES) or any(not isfinite(v) for v in x):
        raise ContractError("ORIGINAL_FEATURE_NONFINITE")
    return {"schema":"heptatrader.price-original-event-feature.v1",
       "anchor_ms":anchor_ms,"features":x,
       "feature_names":list(FEATURE_NAMES),"historical_feature_version":FEATURE_VERSION,
       "original_event_ticks_not_revised_ib_bars":True,
       "future_outcome_was_not_required":True,"trade_authority":False}


def original_feature_snapshot(quotes, prefixes, asof_ms, *, require_fresh=True):
    """Leave availability on HOLD rather than fabricate a live five-minute bar."""
    bars,evidence=original_closed_bars(quotes,asof_ms)
    result=features_at_closed_bar(bars)
    age=asof_ms-result["anchor_ms"]
    if age<0 or (require_fresh and age>LATEST_INPUT_GRACE_MS):
        raise ContractError("ORIGINAL_FEATURE_INPUT_LATE_OR_UNCOMMITTED")
    result.update({"quote_prefixes_digest":digest(prefixes),
       "asof_ms":asof_ms,"last_closed_bar_age_ms":age,
       "source_kind":"ORIGINAL_VERIFIED_PAPER_QUOTES",
       "source_event_receipts":[x["last_quote_receipt"] for x in evidence[-NEEDED_CLOSED_BARS:]],
       "source_bar_count":len(bars),"source_quote_count":len(quotes),
       "source_coverage_policy":"13_COMPLETE_5MIN_BARS_NO_IMPUTATION_V1"})
    result["snapshot_id"]=digest(result)
    return result
