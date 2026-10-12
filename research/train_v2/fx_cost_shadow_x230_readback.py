"""Independent X230 stdlib readback of a bounded Pocket4 PAPER research bundle.

Rebuilds local SHA256 quote chain, exact decision-time prefix, chronology,
deduplicated source identity, cost arithmetic, and mature price-only label.
This is NOT broker-signed data, a product LedgerWriter ACK, or an order audit.
"""
from __future__ import annotations
from decimal import Decimal,localcontext
import hashlib,json,os
from pathlib import Path
import stat,sys
from datetime import datetime,timezone

def sha(data):return hashlib.sha256(data).hexdigest()
def canon(value):return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def need(ok,why):
    if not ok:raise ValueError("X230_INDEPENDENT_"+why)
def private(path):
    path=Path(path)
    need(path.is_file() and not path.is_symlink() and
         path.stat().st_uid==os.geteuid() and
         stat.S_IMODE(path.stat().st_mode)==0o600,"PRIVATE_EVIDENCE_BOUND")
    return path.read_bytes()
def document(root,name):
    raw=private(root/name)
    return json.loads(raw),raw

def run(root):
    root=Path(root)
    need(root.is_absolute() and not root.is_symlink()
         and root.stat().st_uid==os.geteuid()
         and stat.S_IMODE(root.stat().st_mode)==0o700,"PRIVATE_ROOT")
    decision,draw=document(root,"frozen-decision.json")
    outcome,oraw=document(root,"mature-price-only-outcome-v1.json")
    qualification,_=document(root,"qualification.json")
    manifest,_=document(root,"manifest.json")
    cost,craw=document(root,"historical-cost.json")
    plan,_=document(root,"plan.json")
    registration,_=document(root,"local-registration.json")
    src=private(root/"original-source-through-outcome.jsonl")
    source_code=private(root/"fx_cost_shadow.py")
    need(sha(source_code)==manifest["implementation_sha256"],"SOURCE_CODE_DRIFT")
    need(sha(craw)==manifest["historical_cost_receipt_sha256"]==
         decision["cost_receipt_sha256"],"HISTORICAL_COST_CHANGED")
    need(sha(draw)==qualification["decision_sha256"]==
         outcome["original_decision_sha256"],"DECISION_SHA256_CHANGED")
    need(qualification["decision_durable_before_expiry"] is True and
         qualification["durable_at_ms"]<=decision["source_quote_expiry_ms"] and
         decision["decision_at_ms"]<=qualification["durable_at_ms"],
         "DECISION_DURABILITY_EXPIRED")
    need(plan["plan_id"]==registration["plan_id"]==decision["plan_id"]==
         outcome["original_plan_id"],"REGISTERED_PLAN_MISMATCH")
    need(registration["sealed_at_ms"]+30000<=decision["decision_at_ms"] and
         plan["first_decision_ms"]<=decision["decision_at_ms"]<=
         plan["last_decision_ms"],"PREDECLARED_LEAD_NOT_PROVEN")
    need(manifest["original_period_sha256"]==
         plan["period_sha256"]==cost["period_source_sha256"]
         and cost["broker_requests_sent"]==0
         and cost["orders_sent"]==0,
         "PERIOD_OR_COST_BROKER_SCOPE")
    need(manifest["original_period_sha256"]==
         plan["period_sha256"]==cost["period_source_sha256"],
         "PERIOD_SCOPE_DRIFT")
    need(outcome["status"]=="RESEARCH_PRICE_ONLY_OUTCOME_MATURED_NO_ALPHA_CLAIM"
         and decision["selected_action"]=="NO_TRADE"
         and decision["trained_model_applied"] is False
         and outcome["actual_execution_pnl_attested"] is False
         and outcome["trained_forecaster_efficacy_tested"] is False
         and outcome["broker_orders_sent"]==0,
         "ACTION_OR_ECONOMIC_SCOPE_DRIFT")
    need(src.endswith(b"\n"),"ORIGINAL_SOURCE_PARTIAL_ROW")
    prev="0"*64;rows=[];clock=0;prefix_hash=None;prefix_count=None
    all_events=[]
    for n,line in enumerate(src.splitlines(keepends=True)):
        d=json.loads(line)
        need(d.get("schema")=="heptatrader.laya-shadow-quotes.v1" and
             d.get("sequence")==n and d.get("previous")==prev,
             "SOURCE_CHAIN_SEQUENCE")
        calculated=sha(canon({k:v for k,v in d.items() if k!="sha256"}))
        need(calculated==d.get("sha256"),"SOURCE_CHAIN_SHA256")
        t=d.get("at_ms")
        need(type(t) is int and clock<=t,"SOURCE_CALLBACK_TIME_REGRESSED")
        clock=t;prev=calculated
        all_events.append((d,line))
    need(all_events and all_events[0][0]["kind"]=="HEADER","SOURCE_HEADER_MISSING")
    header=all_events[0][0]["data"]
    need(header["execution_domain"]=="PAPER" and
         header["period_sha256"]==manifest["original_period_sha256"] and
         header["broker_orders_sent"]==0 and
         header["write_operations_allowed"]==[],
         "SOURCE_HEADER_AUTHORITY")
    prefix=hashlib.sha256()
    quotes=[];bytime={};decision_seen=False;last_source_id=None
    for index,(ev,line) in enumerate(all_events):
        prefix.update(line)
        if ev["kind"]=="QUOTE":
            q=ev["data"];t=q["observed_at_ms"]
            need(type(t) is int and t>0 and
                 type(q["stale_after_ms"]) is int and
                 t<=ev["at_ms"]<=min(t+5000,q["stale_after_ms"]),
                 "ORIGINAL_QUOTE_TIME")
            with localcontext() as ctx:
                ctx.prec=40
                bid=Decimal(str(q["bid"]));ask=Decimal(str(q["ask"]))
                need(bid>0 and ask>=bid and
                     bid.is_finite() and ask.is_finite(),
                     "INVALID_ORIGINAL_PRICE")
            if t in bytime:
                old=bytime[t][0]["data"]
                need(bid==Decimal(str(old["bid"])) and
                     ask==Decimal(str(old["ask"])),
                     "SAME_TIME_CONFLICTING_QUOTE")
            else:bytime[t]=(ev,ev["sha256"])
            quotes.append(ev["sha256"])
        if prefix.hexdigest()==decision["source_prefix_sha256"]:
            decision_seen=True
            frozen_first_by_time=dict(bytime)
            prefix_count=index+1
            last_source_id=bytime[max(bytime)][1]
    need(decision_seen and prefix_count is not None,
         "EXACT_DECISION_PREFIX_MISSING")
    need(last_source_id==decision["original_quote_source_id"] and
         [frozen_first_by_time[t][1] for t in sorted(frozen_first_by_time)[-6:]]==
         decision["input_quote_source_ids"],
         "PREDECISION_SIX_QUOTE_IDENTITY")
    original=frozen_first_by_time[max(frozen_first_by_time)][0]
    need(original["data"]["observed_at_ms"]<=
         decision["source_quote_available_at_ms"]<=
         decision["decision_at_ms"]<=
         decision["source_quote_expiry_ms"],
         "PREDECISION_QUOTE_TTL")
    need(original["at_ms"]==decision["source_quote_available_at_ms"] and
         min(original["data"]["observed_at_ms"]+5000,
             original["data"]["stale_after_ms"])==
         decision["source_quote_expiry_ms"],
         "PREDECISION_EXPIRY_IDENTITY")
    due=decision["outcome_not_available_before_ms"]
    need(due==decision["decision_at_ms"]+300000==
         outcome["original_horizon_due_ms"],"EXACT_HORIZON")
    futures=[e for e,line in all_events
             if e["kind"]=="QUOTE"
             and due<=e["data"]["observed_at_ms"]<=due+5000
             and e["at_ms"]<=outcome["mature_result_sealed_at_ms"]]
    need(bool(futures),"NO_MATURE_ORIGINAL_QUOTE")
    chosen=min(futures,key=lambda e:(e["data"]["observed_at_ms"],e["at_ms"]))
    need(chosen["sha256"]==outcome["mature_original_outcome_quote_id"]
         and chosen["at_ms"]==outcome["mature_quote_available_at_ms"]
         and chosen["data"]["observed_at_ms"]==
         outcome["mature_quote_event_at_ms"],
         "MATURE_QUOTE_IDENTITY_DRIFT")
    with localcontext() as ctx:
        ctx.prec=40
        in_bid=Decimal(str(original["data"]["bid"]))
        in_ask=Decimal(str(original["data"]["ask"]))
        out_bid=Decimal(str(chosen["data"]["bid"]))
        out_ask=Decimal(str(chosen["data"]["ask"]))
        initial=(in_bid+in_ask)/2
        ending=(out_bid+out_ask)/2
        actual=(ending/initial-1)*10000
        reported=Decimal(outcome["price_only_midpoint_return_bps"])
        need(abs(actual-reported)<Decimal("1e-22"),
             "PRICE_ONLY_RETURN_DRIFT")
        full_spread=(in_ask-in_bid)/initial*10000
        hist_p99=Decimal(str(cost["quote_spread_full_bps"]["p99"]))
        fees=2*Decimal(str(cost["fx_commission_one_way_bps"]["p90"]))
        hurdle=max(hist_p99,full_spread)+fees+Decimal(4)+Decimal(2)
        report_cost=decision["economic_cost_hurdle"]
        need(abs(hurdle-Decimal(report_cost["required_predicted_abs_mid_move_bps"]))<
             Decimal("1e-20"),"PREDECISION_COST_HURDLE_DRIFT")
    return {
      "schema":"heptatrader.fx-cost-shadow-x230-independent-readback.v1",
      "state":"READONLY_CROSSHOST_ORIGINAL_SOURCE_COST_AND_MATURE_PRICE_MATCH",
      "independent_host":os.uname().nodename,
      "original_quote_record_count":len(quotes),
      "original_timestamp_count":len(bytime),
      "predecision_source_prefix_record_count":prefix_count,
      "source_bundle_sha256":sha(src),
      "original_decision_sha256":sha(draw),
      "mature_result_sha256":sha(oraw),
      "cost_receipt_sha256":sha(craw),
      "source_code_sha256":sha(source_code),
      "price_only_midpoint_return_bps":str(reported),
      "original_cost_hurdle_bps":str(report_cost["required_predicted_abs_mid_move_bps"]),
      "same_timestamp_quote_duplicates":len(quotes)-len(bytime),
      "local_origin_is_not_broker_signed":True,
      "independent_product_root_or_witness_enrolled":False,
      "executed_broker_net_pnl_claimed":False,
      "model_edge_or_promotion_proven":False,
      "broker_requests_or_orders_by_verifier":0,
      "trade_authority":False
    }

def main():
    root=Path(sys.argv[1])
    output=root/"x230-readback.json"
    report=run(root)
    os.umask(0o077)
    fd=os.open(output,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,"wb") as fh:
        fh.write(json.dumps(report,sort_keys=True,indent=2).encode()+b"\n")
        fh.flush();os.fsync(fh.fileno())
    print(json.dumps({"state":report["state"],"original_quote_record_count":report["original_quote_record_count"],"same_timestamp_quote_duplicates":report["same_timestamp_quote_duplicates"],"price_only_midpoint_return_bps":report["price_only_midpoint_return_bps"],"broker_orders_sent":0,"receipt_sha256":sha(output.read_bytes())},sort_keys=True))

if __name__=="__main__":main()
