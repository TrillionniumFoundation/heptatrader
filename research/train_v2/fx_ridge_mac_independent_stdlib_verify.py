"""macOS stdlib-only verifier: frozen Ridge, quote chain, and first mature label.

No broker, SSH, account or local trading access is implemented. All input is
one exported immutable bounded research ZIP. Not an independent broker oracle.
"""
from __future__ import annotations
import argparse, hashlib, json, math, os, stat, time, zipfile
from decimal import Decimal
from pathlib import Path

def abort(r): raise ValueError(r)
def sha(b):return hashlib.sha256(b).hexdigest()
def canonical(obj):return json.dumps(obj,sort_keys=True,separators=(",",":"),allow_nan=False).encode()
def strict(raw):
    def uniq(items):
        v={}
        for k,x in items:
            if k in v:abort("DUPLICATE_JSON_KEY")
            v[k]=x
        return v
    return json.loads(raw,object_pairs_hook=uniq,
                      parse_constant=lambda _:abort("NONFINITE_JSON"))
def require(v,r):
    if not v:abort(r)
def qdec(x):
    d=Decimal(str(x))
    require(d.is_finite(),"NONFINITE_QUOTE")
    return d

def source_prefix(data,period_sha):
    require(data.endswith(b"\n"),"SOURCE_PREFIX_INCOMPLETE")
    old="0"*64;previous_at=0
    rows=[]
    for idx,line in enumerate(data.splitlines()):
        require(len(line)<=65536,"OVERSIZE_ORIGINAL_SOURCE_EVENT")
        event=strict(line)
        require(type(event) is dict and
                set(event)=={"schema","sequence","previous","at_ms","kind","data","sha256"}
                and event["schema"]=="heptatrader.laya-shadow-quotes.v1"
                and event["sequence"]==idx and event["previous"]==old
                and event["sha256"]==sha(canonical({k:v for k,v in event.items() if k!="sha256"})),
                "ORIGINAL_CHAIN_HASH_OR_SEQUENCE_CHANGED")
        at=event["at_ms"]
        require(type(at) is int and previous_at<=at,"ORIGINAL_EVENT_CLOCK_INVALID")
        previous_at=at
        old=event["sha256"]
        kind=event["kind"]
        body=event["data"]
        if idx==0:
            require(kind=="HEADER" and body["source"]=="IB_TOOL_GATEWAY"
                    and body["execution_domain"]=="PAPER"
                    and body["instrument"]=="EUR.USD"
                    and body["period_sha256"]==period_sha
                    and body["write_operations_allowed"]==[]
                    and body["broker_orders_sent"]==0,"NOT_ORIGINAL_PAPER_SOURCE")
        elif kind=="QUOTE":
            bid,ask=qdec(body["bid"]),qdec(body["ask"])
            event_ms,expiry=body["observed_at_ms"],body["stale_after_ms"]
            require(body["source"]=="IB" and body["authoritative"] is True
                    and body["stale"] is False and body["instrument"]=="EUR.USD"
                    and 0<bid<=ask and type(event_ms) is int
                    and type(expiry) is int and
                    0<event_ms<=at<=min(expiry,event_ms+5000),
                    "UNTRUSTED_OR_EXPIRED_PAPER_QUOTE")
            rows.append({"id":event["sha256"],"event_ms":event_ms,
                         "available_ms":at,"expiry_ms":min(expiry,event_ms+5000),
                         "bid":bid,"ask":ask})
        elif kind=="END":
            require(body["broker_orders_sent"]==0,"SOURCE_END_HAS_ORDERS")
            require(idx==len(data.splitlines())-1,"SOURCE_AFTER_END")
        elif kind!="FAILURE":
            abort("INVALID_ORIGINAL_SOURCE_KIND")
    return rows

def check(zip_path):
    data=Path(zip_path).read_bytes()
    bundle_sha=sha(data)
    with zipfile.ZipFile(zip_path) as bundle:
        names=bundle.namelist()
        require(len(names)==len(set(names)) and len(names)<=40
                and "index.json" in names, "BUNDLE_MEMBER_COUNT")
        index=strict(bundle.read("index.json"))
        require(index["schema"]=="heptatrader.fx-ridge-portable-original-bundle.v1"
                and index["broker_orders_sent"]==0
                and index["model_promotion_authorized"] is False
                and index["original_broker_signatures_attested"] is False,
                "BUNDLE_CLAIM_INVALID")
        def get(name):
            require(name in index["file_sha256"],"FILE_NOT_PINNED")
            b=bundle.read(name)
            require(sha(b)==index["file_sha256"][name],"BUNDLE_FILE_SHA256_CHANGED")
            return b
        require(set(names)==set(index["file_sha256"])|{"index.json"},
                "UNMANIFESTED_BUNDLE_MEMBER")
        model_bytes=get("artifacts/model.json")
        manifest_bytes=get("artifacts/manifest.json")
        decision_bytes=get("artifacts/preoutcome-decision.json")
        q_bytes=get("artifacts/qualification.json")
        model=strict(model_bytes);manifest=strict(manifest_bytes)
        plan=strict(get("artifacts/plan.json"))
        receipt=strict(get("artifacts/local-registration.json"))
        decision=strict(decision_bytes)
        qualification=strict(q_bytes)
        outcome=strict(get("artifacts/mature-outcome.json"))
        cost_bytes=get("artifacts/cost.json")
        cost=strict(cost_bytes)
        period=plan["period_sha256"]
        require(manifest["historical_model_sha256"]==sha(canonical(model))
                and decision["model_sha256"]==sha(canonical(model))
                and decision["model_file_sha256"]==sha(model_bytes)
                and decision["manifest_sha256"]==sha(manifest_bytes)
                and qualification["decision_sha256"]==sha(decision_bytes)
                and sha(cost_bytes)==manifest["cost_receipt_sha256"]==
                    model["cost_receipt_sha256"],"MODEL_OR_COST_RECEIPT_MUTATED")
        require(plan["plan_id"]==receipt["plan_id"]==decision["plan_id"]
                and receipt["plan"]==plan
                and receipt["plan_sha256"]==sha(canonical(plan))
                and receipt["first_decision_ms"]==plan["first_decision_ms"]
                and receipt["last_decision_ms"]==plan["last_decision_ms"]
                and receipt["original_source_root"]==manifest["source_root"]
                and receipt["broker_market_data_signed"] is False
                and receipt["independent_owner_signature"] is False
                and receipt["can_relabel_previously_inspected_data_as_unseen"] is False
                and receipt["canonical_agentd_owner_enrolled"] is False
                and plan["registered_at_ms"]<=receipt["sealed_at_ms"]
                and plan["period_sha256"]==manifest["original_period_sha256"]==
                   model["period_sha256"]
                and receipt["sealed_at_ms"]<=plan["first_decision_ms"]-30000
                and plan["first_decision_ms"]<=decision["decision_at_ms"]<=plan["last_decision_ms"]
                and qualification["seal_at_ms"]<qualification["last_original_quote_expiry_ms"]
                and qualification["sealed_before_broker_reported_quote_expiry"] is True
                and model["historical_validation_selected_action"]=="NO_TRADE"
                and model["candidate_has_positive_validation_edge"] is False
                and model["historical_holdout_previously_seen"] is True
                and decision["selected_trading_action"]=="NO_TRADE",
                "REGISTRATION_OR_PREACTION_TIME_CHANGED")
        require(manifest["broker_signed_quote"] is False
                and manifest["external_production_root_enrolled"] is False
                and manifest["trading_behavior_propensity_known"] is False
                and decision["original_trader_behavior_propensity_attested"] is False
                and outcome["observed_trading_pnl_attested"] is False
                and outcome["model_efficacy_proven"] is False,
                "FALSE_PRODUCT_OR_BROKER_ATTESTATION")
        source={}
        for original_path,entry in index["source_map"].items():
            s=get(entry["archive"])
            require(len(s)==entry["max_bytes"] and sha(s)==entry["max_sha256"],
                    "SOURCE_BUNDLE_PREFIX_INVALID")
            for r in entry["receipts"]:
                require(sha(s[:r["bytes"]])==r["sha256"],"FROZEN_PREFIX_HASH_CHANGED")
                # Reparse separately for predecision and maturity, so no quote
                # from the later, longer prefix can leak into the old decision.
                source.setdefault(r["role"],[]).extend(source_prefix(s[:r["bytes"]],period))
        for role in ("preaction","outcome"):
            require(len(source.get(role,[]))>10,"MISSING_ORIGINAL_QUOTE_RECORDS")
        def unique_role(role):
            known={}
            for q in source[role]:
                t=q["event_ms"]
                if t in known:
                    require(q["bid"]==known[t]["bid"] and
                            q["ask"]==known[t]["ask"],"DUPLICATE_ORIGINAL_TICK_CONFLICT")
                else:known[t]=q
            return list(known.values())
        prior=unique_role("preaction")
        after=unique_role("outcome")
        by_id={q["id"]:q for q in prior}
        samples=decision["history_quote_samples"]
        anchor=manifest["anchor_ms"]
        require(len(samples)==13,"HISTORICAL_FEATURE_WINDOW_CHANGED")
        closes=[]
        for i,s in enumerate(samples):
            end=anchor-(12-i)*300000
            r=by_id.get(s["source_id"])
            require(r is not None and
                    end-5000<=r["event_ms"]<end and r["available_ms"]<=end and
                    r["event_ms"]==s["event_at_ms"] and
                    r["available_ms"]==s["available_at_ms"] and
                    str(r["bid"])==s["bid"] and str(r["ask"])==s["ask"],
                    "PREACTION_QUOTE_NOT_FOUND_IN_PINNED_SOURCE")
            closes.append(float((r["bid"]+r["ask"])/2))
        lag=[(closes[-1]/closes[-1-i]-1)*10000 for i in (1,3,6,12)]
        rets=[(y-x)/x for x,y in zip(closes[:-1],closes[1:])]
        avg=sum(rets)/len(rets)
        stdev=math.sqrt(sum((r-avg)**2 for r in rets)/len(rets))
        hour=((anchor//1000-300)//3600)%24
        vec=lag+[stdev*10000,sum(map(abs,rets))/len(rets)*10000,
                 math.sin(2*math.pi*hour/24),math.cos(2*math.pi*hour/24)]
        require(all(math.isclose(a,b,abs_tol=1e-8) for a,b in zip(vec,decision["feature_vector"])),
                "MODEL_INPUT_LOOKAHEAD_OR_CHANGE")
        require(len(vec)==len(model["mean"])==len(model["stddev"])==8
                and len(model["weights"])==9,"MODEL_WEIGHT_GEOMETRY")
        scaled=[min(5,max(-5,(v-m)/s)) for v,m,s in zip(vec,model["mean"],model["stddev"])]
        forecast=model["weights"][0]+sum(v*w for v,w in zip(scaled,model["weights"][1:]))
        require(math.isclose(forecast,decision["forecast_midpoint_bps"],abs_tol=1e-9),
                "MAC_INDEPENDENT_RIDGE_PREDICTION_MISMATCH")
        input_bid,input_ask=by_id[samples[-1]["source_id"]]["bid"],by_id[samples[-1]["source_id"]]["ask"]
        spread=float((input_ask-input_bid)/((input_ask+input_bid)/2)*10000)
        stressed=max(spread,cost["quote_spread_full_bps"]["p99"])
        required=stressed+2*cost["fx_commission_one_way_bps"]["p90"]+4+2
        require(math.isclose(required,decision["cost_hurdle"]["required_absolute_prediction_bps"],abs_tol=1e-8)
                and abs(forecast)<required and decision["forecast_magnitude_clears_cost"] is False,
                "ORIGINAL_COST_VIOLATED")
        due=manifest["outcome_due_ms"]
        require(due==anchor+900000 and outcome["outcome_due_ms"]==due,
                "FUTURE_HORIZON_DRIFT")
        eligible=[q for q in after if due<=q["event_ms"]<=due+5000
                  and due<=q["available_ms"]]
        require(eligible,"MISSING_FUTURE_ORIGINAL_QUOTE")
        q=min(eligible,key=lambda z:(z["event_ms"],z["available_ms"]))
        require(q["id"]==outcome["outcome_quote_sha256"]
                and q["event_ms"]==outcome["price_event_ms"]
                and q["available_ms"]==outcome["price_available_ms"]
                and outcome["input_quote_sha256"]==samples[-1]["source_id"],
                "FUTURE_PRICE_LABEL_CHERRY_PICKED")
        input_mid=(input_bid+input_ask)/2
        future=(q["bid"]+q["ask"])/2
        actual=(future/input_mid-1)*10000
        err=Decimal(str(forecast))-actual
        require(abs(actual-Decimal(outcome["observed_original_midpoint_return_bps"]))<=Decimal("1e-17")
                and abs(err-Decimal(outcome["forecast_error_bps"]))<=Decimal("1e-12"),
                "FUTURE_RETURN_OR_ERROR_MISMATCH")
    return {"schema":"heptatrader.fx-ridge-mac-independent-chain-and-price.v1",
            "source":"FROZEN_BUNDLE_PREACTION_AND_MATURITY_EXACT_PREFIXES",
            "bundle_sha256":bundle_sha,
            "verified_at_ms":int(time.time()*1000),
            "verifier_host":os.uname().nodename,
            "preaction_source_rows":len(prior),"mature_source_rows":len(after),
            "original_quote_prefix_count":len(index["source_map"]),
            "frozen_model_prediction_bps":forecast,
            "observed_original_midpoint_return_bps":str(actual),
            "forecast_error_bps":str(err),
            "cost_required_bps":required,
            "selected_action":"NO_TRADE",
            "direction_correct_research_only":(forecast>0 and actual>0) or (forecast<0 and actual<0),
            "broker_signed_source":False,"production_witness_admitted":False,
            "model_economic_alpha_proven":False,"trade_authority":False,
            "broker_requests_sent":0,"broker_orders_sent":0,
            "state":"MAC_INDEPENDENT_FROZEN_SOURCE_AND_PRICE_RECOMPUTED"}

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--bundle",type=Path,required=True)
    ap.add_argument("--output",type=Path,required=True)
    args=ap.parse_args()
    report=check(args.bundle)
    args.output.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    fd=os.open(args.output,os.O_EXCL|os.O_CREAT|os.O_WRONLY,0o600)
    with os.fdopen(fd,"w") as f:
        json.dump(report,f,sort_keys=True,indent=2);f.write("\n");f.flush();os.fsync(f.fileno())
    print(json.dumps({"state":report["state"],"host":report["verifier_host"],
                      "forecast_bps":report["frozen_model_prediction_bps"],
                      "observed_bps":report["observed_original_midpoint_return_bps"],
                      "source_files":report["original_quote_prefix_count"],
                      "broker_orders_sent":0},sort_keys=True))
if __name__=="__main__":main()
