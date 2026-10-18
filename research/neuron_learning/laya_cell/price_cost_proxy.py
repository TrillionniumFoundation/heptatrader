"""Fixed historical next-bar mark cost proxy; no broker, files, or fitting.

The action API accepts only frozen probabilities and current known marks.
Fees 4/8 USD are hypotheses, not an IB tariff. Future OHLC is not execution.
"""
from __future__ import annotations
from bisect import bisect_right
from collections import Counter
from datetime import datetime,timezone
from math import fsum,isfinite
from .integrity import ContractError

BINS=(-10,-3,-1,1,3,10)
BAR_MS=300000
FEES=(0,4,8)

def number(value):
    if isinstance(value,bool):raise ContractError("COST_BOOLEAN")
    x=float(value)
    if not isfinite(x):raise ContractError("COST_NONFINITE")
    return x

def positive(value):
    x=number(value)
    if x<=0:raise ContractError("COST_NONPOSITIVE")
    return x

def known_input_features(bytime,anchor_ms):
    from .price_original_live_features import features_at_closed_bar
    history=[bytime.get(anchor_ms//1000-k*300) for k in range(13,0,-1)]
    if any(b is None for b in history):raise ContractError("KNOWN_INPUT_BAR_MISSING")
    return features_at_closed_bar(history),history[-1]

def train_mapping(returns,labels):
    if not returns or len(returns)!=len(labels):raise ContractError("COST_TRAIN_LENGTH")
    groups=[[] for _ in range(7)]
    for value,label in zip(returns,labels):
        x=number(value)
        if type(label) is not int or not 0<=label<7 or bisect_right(BINS,x)!=label:
            raise ContractError("COST_TRAIN_LABEL_MISMATCH")
        groups[label].append(x)
    if any(not g for g in groups):raise ContractError("COST_TRAIN_EMPTY_CLASS")
    return [{"class":k,"count":len(g),"mean_return_bps":fsum(g)/len(g),
        "min_return_bps":min(g),"max_return_bps":max(g)} for k,g in enumerate(groups)]

def decide(anchor_ms,probs,mapping,known_bar,quantity=100000,reference_fee_usd=4):
    if type(anchor_ms) is not int or anchor_ms%BAR_MS:raise ContractError("COST_ANCHOR")
    if known_bar["time"]*1000!=anchor_ms-BAR_MS:raise ContractError("COST_KNOWN_BAR_TIME")
    p=[number(x) for x in probs]
    if len(p)!=7 or any(not 0<=x<=1 for x in p) or abs(fsum(p)-1)>1e-5:
        raise ContractError("COST_PROBS")
    if len(mapping)!=7 or [m["class"] for m in mapping]!=list(range(7)):
        raise ContractError("COST_MAPPING")
    q=positive(quantity);fee=number(reference_fee_usd)
    mid=positive(known_bar["midpoint"]["close"])
    bid=positive(known_bar["bid"]["close"]);ask=positive(known_bar["ask"]["close"])
    if ask<bid or fee<0:raise ContractError("COST_KNOWN_MARKS")
    expected=fsum(p[k]*number(mapping[k]["mean_return_bps"]) for k in range(7))
    spread=(ask-bid)/mid*10000;fee_bps=fee/(q*mid)*10000
    gate=spread+fee_bps
    side="LONG" if expected>gate else "SHORT" if expected<-gate else "FLAT"
    return {"anchor_ms":anchor_ms,"side":side,"predicted_return_bps":expected,
        "known_mid":mid,"known_bid":bid,"known_ask":ask,"known_spread_bps":spread,
        "fee_bps":fee_bps,"quantity":q,"reference_fee_usd":fee}

def mark(action,target_bar):
    out={**action,"status":"UNSCORABLE","gross_usd":None,"reason":None}
    if action["side"]=="FLAT":return {**out,"status":"NO_TRADE","gross_usd":0.0}
    if action["side"] not in ("LONG","SHORT"):raise ContractError("COST_SIDE")
    if target_bar is None:return {**out,"reason":"MISSING_TARGET_BAR"}
    try:
        if type(target_bar["time"]) is not int or target_bar["time"]*1000!=action["anchor_ms"]:
            raise ContractError("TARGET_TIME_MISMATCH")
        marks={}
        for leg in ("midpoint","bid","ask"):
            v={k:positive(target_bar[leg][k]) for k in ("open","close","high","low")}
            if not v["low"]<=min(v["open"],v["close"])<=max(v["open"],v["close"])<=v["high"]:
                raise ContractError("TARGET_OHLC_INVALID")
            marks[leg]=v
        b,a,m=(marks[k] for k in ("bid","ask","midpoint"))
        if b["open"]>a["open"] or b["close"]>a["close"]:
            raise ContractError("TARGET_CROSSED")
        q=positive(action["quantity"])
        gross=q*(b["close"]-a["open"] if action["side"]=="LONG" else b["open"]-a["close"])
        mid_gross=q*(m["close"]-m["open"])*(1 if action["side"]=="LONG" else -1)
        return {**out,"status":"SCORED","gross_usd":gross,
            "midpoint_open_to_close_gross_usd":mid_gross,
            "bidask_mark_cost_vs_midpoint_usd":mid_gross-gross,
            "boundary_jump_bps":(m["open"]/action["known_mid"]-1)*10000}
    except (KeyError,TypeError,ValueError,ContractError) as exc:
        return {**out,"reason":type(exc).__name__+":"+str(exc)[:140]}

def distribution(values):
    x=sorted(number(v) for v in values)
    if not x:return {"count":0}
    return {"count":len(x),"min":x[0],"median":x[len(x)//2],
        "p95":x[min(len(x)-1,int(len(x)*.95))],"max":x[-1],"mean":fsum(x)/len(x)}

def _metrics(rows,fee):
    trades=[r for r in rows if r["side"]!="FLAT"]
    observed=[r for r in trades if r["status"]=="SCORED"]
    missing=len(trades)-len(observed);complete=missing==0
    gross=fsum(r["gross_usd"] for r in observed)
    net=[r["gross_usd"]-fee for r in observed];subtotal=fsum(net)
    equity=peak=dd=0.
    for v in net:equity+=v;peak=max(peak,equity);dd=max(dd,peak-equity)
    largest=None
    if observed:
        i=max(range(len(net)),key=lambda i:abs(net[i]))
        largest={"anchor_ms":observed[i]["anchor_ms"],"net_usd":net[i],
            "share_of_absolute_net":abs(net[i])/fsum(abs(x) for x in net) if any(net) else None}
    return {"state":"EMPTY" if not rows else "COMPLETE" if complete else "INCOMPLETE",
        "input_eligible_opportunities":len(rows),"trades":len(trades),
        "scored_trades":len(observed),"unscorable_trades":missing,
        "gross_usd":gross if complete else None,"fees_usd":fee*len(trades) if complete else None,
        "net_usd":subtotal if complete else None,
        "net_per_input_eligible_opportunity_usd":subtotal/len(rows) if complete and rows else None,
        "net_per_trade_usd":subtotal/len(trades) if complete and trades else None,
        "max_drawdown_usd":dd if complete else None,
        "largest_absolute_trade":largest if complete else None,
        "observed_subset_gross_usd":gross,"observed_subset_net_usd":subtotal}

def summarize(rows):
    previous=None;days={};quantities=set();references=set()
    for row in rows:
        anchor=row["anchor_ms"]
        if type(anchor) is not int or anchor%BAR_MS or (previous is not None and anchor-previous<BAR_MS):
            raise ContractError("COST_OVERLAPPING_OR_UNORDERED_ACTIONS")
        previous=anchor;side=row["side"]
        gate=row["known_spread_bps"]+row["fee_bps"];mu=row["predicted_return_bps"]
        wanted="LONG" if mu>gate else "SHORT" if mu<-gate else "FLAT"
        if side!=wanted:raise ContractError("COST_ACTION_CHANGED")
        if side=="FLAT":
            if row["status"]!="NO_TRADE" or row["gross_usd"]!=0:raise ContractError("COST_FLAT_MARK")
        elif row["status"]=="SCORED":number(row["gross_usd"])
        elif row["status"]!="UNSCORABLE" or row["gross_usd"] is not None:
            raise ContractError("COST_INVALID_UNSCORABLE")
        quantities.add(row["quantity"]);references.add(row["reference_fee_usd"])
        day=datetime.fromtimestamp((anchor+BAR_MS)/1000,timezone.utc).date().isoformat()
        days.setdefault(day,[]).append(row)
    if len(quantities)>1 or len(references)>1:raise ContractError("COST_MIXED_PROTOCOL")
    return {"scope":"HISTORICAL_ZERO_LATENCY_NEXT_BAR_BIDASK_MARK_PROXY",
        "counts":dict(Counter(r["side"] for r in rows)),
        "known_spread_bps":distribution([r["known_spread_bps"] for r in rows]),
        "known_zero_spread_opportunities":sum(r["known_spread_bps"]==0 for r in rows),
        "boundary_jump_bps":distribution([r["boundary_jump_bps"] for r in rows if r["status"]=="SCORED"]),
        "mark_cost_vs_midpoint_usd":distribution([r["bidask_mark_cost_vs_midpoint_usd"] for r in rows if r["status"]=="SCORED"]),
        "scenarios":{str(fee):{"overall":_metrics(rows,fee),
            "daily":{d:_metrics(v,fee) for d,v in sorted(days.items())}} for fee in FEES},
        "economic_alpha_proven":False,"not_execution_backtest":True}
