#!/usr/bin/env python3
"""Bounded PAPER-only IB TWS API whatIf preview; NEVER a working order.

Safety: one DU account, localhost:4002, one dedicated client ID, one
Order.whatIf=True request, verified non-transmit preview semantics from
the pinned ibapi source. No cancel/other placeOrder, no strategy/driver ops.
This probes account-level STP margin validation, NOT bracket/stop protection.
"""
from __future__ import annotations
import argparse
from decimal import Decimal
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

from ibapi.client import EClient
from ibapi.contract import Contract
from ibapi.order import Order
from ibapi.wrapper import EWrapper

PORT=4002
CONID=12087792
CLIENT_ID=8994
QTY=Decimal("1000")
MARKET_TICK=0.00005
ERRORS_INFO={2104,2106,2107,2158}

def synthetic_stop_price(bid:float)->float:
    if not math.isfinite(bid) or not 0.8 < bid < 1.6:
        raise ValueError("UNQUALIFIED_EURUSD_QUOTE")
    result=math.floor((bid*.98)/MARKET_TICK+1e-9)*MARKET_TICK
    if not (0.1 < result < bid-10*MARKET_TICK):
        raise ValueError("STOP_WOULD_TRIGGER_IMMEDIATELY")
    return round(result,5)

def exact_whatif_order(account:str, bid:float, order_id:int):
    if not isinstance(account,str) or not account.startswith("DU") or len(account)>32:
        raise ValueError("PAPER_ACCOUNT_UNQUALIFIED")
    if not 1<=order_id<2147483640:
        raise ValueError("BROKER_NATIVE_ORDER_ID_UNQUALIFIED")
    contract=Contract()
    contract.conId=CONID
    contract.secType="CASH";contract.symbol="EUR"
    contract.currency="USD";contract.exchange="IDEALPRO"
    order=Order()
    order.action="SELL";order.orderType="STP"
    order.totalQuantity=QTY;order.auxPrice=synthetic_stop_price(bid)
    order.tif="DAY";order.account=account
    order.whatIf=True
    order.transmit=True
    order.orderRef="HEPTA-WHATIF-20261016-ONE-SHOT"
    order.clientId=CLIENT_ID
    order.orderId=order_id
    if not order.whatIf or order.parentId not in (0,None):
        raise ValueError("WHATIF_ORDER_FLAG_REQUIRED")
    if order.action!="SELL" or order.orderType!="STP" or order.totalQuantity!=QTY:
        raise ValueError("PREVIEW_SCOPE_INVALID")
    return contract,order

def native_read(tool:str)->dict:
    if tool not in ("market.get_quote","orders.list"):
        raise ValueError("READONLY_TOOL_SCOPE")
    argv=["/home/alex/.local/bin/heptatrader","native","--io-timeout-ms","4000",
          "--call-id","p0-ib-whatif-"+tool.replace(".","-")+"-"+str(time.monotonic_ns()),
          "call",tool]
    if tool=="market.get_quote":argv.append("instrument=EUR.USD")
    run=subprocess.run(argv,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE,text=True,timeout=10,check=False)
    if run.returncode or not run.stdout.strip():
        raise ValueError("NATIVE_READONLY_OBSERVER_UNAVAILABLE")
    response=json.loads(run.stdout)
    if response.get("status")!="ok":
        raise ValueError("NATIVE_READONLY_OBSERVER_NOT_AUTHORITATIVE")
    payload=response.get("payload")
    if not isinstance(payload,dict) or payload.get("source")!="IB" or payload.get("authoritative") is not True:
        raise ValueError("BROKER_READONLY_SCOPE_NOT_ATTESTED")
    return payload

def observe_orders():
    p=native_read("orders.list")
    if p.get("global_active_orders_complete") is not True:
        raise ValueError("GLOBAL_BROKER_ACTIVE_ORDER_INCOMPLETE")
    ids=p.get("active_order_ids")
    if not isinstance(ids,list):
        raise ValueError("BROKER_ORDER_IDS_UNKNOWN")
    # Never write original Broker order IDs to a public report.
    return {"count":len(ids),"fingerprint":hashlib.sha256(
        json.dumps(sorted(str(v) for v in ids),separators=(",",":")).encode()).hexdigest()}

def require_fresh_quote_observation(quote:dict,now_ms:int)->float:
    if not isinstance(quote,dict) or quote.get("instrument")!="EUR.USD" or (
        quote.get("stale") is not False or
        quote.get("subscription_state")!="active"):
        raise RuntimeError("MARKET_QUOTE_NOT_FRESH")
    observed=quote.get("observed_at_ms")
    if type(observed) is not int or not 0 <= now_ms-observed <= 5000:
        raise RuntimeError("QUOTE_CLOCK_NOT_BOUND")
    bid=float(quote["bid"])
    synthetic_stop_price(bid)
    return bid

class PreviewClient(EWrapper,EClient):
    def __init__(self):
        EWrapper.__init__(self);EClient.__init__(self,self)
        self.account_ready=threading.Event()
        self.next_ready=threading.Event()
        self.preview_ready=threading.Event()
        self.paper_account=""
        self.next_id=0
        self.expected_order_id=0
        self.allow_preview=False
        self.sent_preview=0
        self.message_codes=[]
        self.broker_error_codes=[]
        self.order_states=[]
        self.executions=0
        self.order_status_callbacks=[]
        self.expected_account=""
        self.expected_stop_price=None
        self.exact_preview_echoes=0
        self.invalid_preview_echoes=0
    def sendMsg(self,msg):
        code=int(msg.split("\0")[0])
        if code not in (71,3):
            raise RuntimeError("UNEXPECTED_OUTBOUND_API_MESSAGE")
        if code==3:
            if not self.allow_preview or self.sent_preview!=0:
                raise RuntimeError("DUPLICATE_OR_UNAUTHORIZED_PLACE_ORDER")
            self.sent_preview+=1
        self.message_codes.append(code)
        return EClient.sendMsg(self,msg)
    def managedAccounts(self,accountsList):
        names=[s.strip() for s in accountsList.split(",") if s.strip()]
        if len(names)==1 and names[0].startswith("DU"):
            self.paper_account=names[0]
        self.account_ready.set()
    def nextValidId(self,orderId):
        self.next_id=orderId
        self.next_ready.set()
    def openOrder(self,orderId,contract,order,orderState):
        if orderId==self.expected_order_id and self.allow_preview:
            state=str(getattr(orderState,"status",""))[:70]
            try:
                exact_qty=Decimal(str(getattr(order,"totalQuantity",None)))==QTY
            except (ValueError,TypeError,ArithmeticError):
                exact_qty=False
            echo={
                "state":state,
                "echoed_whatIf":getattr(order,"whatIf",None) is True,
                "echoed_account_is_paper":str(getattr(order,"account","")).startswith("DU"),
                "echoed_exact_account":bool(self.expected_account) and getattr(order,"account",None)==self.expected_account,
                "contract_id_valid":getattr(contract,"conId",None)==CONID,
                "contract_scope_valid":(getattr(contract,"secType",None)=="CASH" and
                    getattr(contract,"symbol",None)=="EUR" and
                    getattr(contract,"currency",None)=="USD" and
                    getattr(contract,"exchange",None)=="IDEALPRO"),
                "order_type_STP":getattr(order,"orderType",None)=="STP",
                "action_SELL":getattr(order,"action",None)=="SELL",
                "quantity_exact":exact_qty,
                "tif_DAY":getattr(order,"tif",None)=="DAY",
                "no_parent":getattr(order,"parentId",None) in (0,None),
                "transmit_flag":getattr(order,"transmit",None) is True,
                "client_id_exact":getattr(order,"clientId",None)==CLIENT_ID,
                "order_ref_exact":getattr(order,"orderRef",None)=="HEPTA-WHATIF-20261016-ONE-SHOT",
                "stop_price_exact":(self.expected_stop_price is not None and
                    getattr(order,"auxPrice",None)==self.expected_stop_price),
                "margin_available":bool(str(getattr(orderState,"initMarginChange","")).strip())
            }
            required=("echoed_whatIf","echoed_exact_account","contract_id_valid",
                      "contract_scope_valid","order_type_STP","action_SELL",
                      "quantity_exact","tif_DAY","no_parent","transmit_flag",
                      "client_id_exact","order_ref_exact","stop_price_exact")
            valid=state=="PreSubmitted" and all(echo[key] for key in required)
            echo["exact_nonworking_whatif_echo"]=valid
            self.order_states.append(echo)
            if valid:
                self.exact_preview_echoes+=1
            else:
                self.invalid_preview_echoes+=1
            self.preview_ready.set()
    def orderStatus(self,orderId,status,filled,remaining,*args):
        if orderId==self.expected_order_id:
            self.order_status_callbacks.append(str(status)[:65])
            if filled and float(filled)>0:
                self.executions+=1
            self.preview_ready.set()
    def execDetails(self,reqId,contract,execution):
        if execution.orderId==self.expected_order_id:self.executions+=1
    def error(self,reqId,errorCode,errorString,advancedOrderRejectJson=""):
        if len(self.broker_error_codes)<16:
            self.broker_error_codes.append({"request_id_match":reqId==self.expected_order_id,
                "code":int(errorCode)})
        if reqId==self.expected_order_id and int(errorCode) not in ERRORS_INFO:
            self.preview_ready.set()

def exact_preview_callback_attested(app:PreviewClient)->bool:
    # orderStatus alone is not a complete broker WHATIF echo. Even when
    # openOrder matches, actual/terminal status invalidates a preview claim.
    request_errors=any(
        error["request_id_match"] and error["code"] not in ERRORS_INFO
        for error in app.broker_error_codes)
    unexpected_status=any(
        status!="PreSubmitted" for status in app.order_status_callbacks)
    return (app.preview_ready.is_set() and app.exact_preview_echoes>0 and
            app.invalid_preview_echoes==0 and
            app.sent_preview==1 and app.message_codes==[71,3] and
            app.executions==0 and not request_errors and
            not unexpected_status)

def one_preview(bid:float)->dict:
    app=PreviewClient()
    thread=None
    report={"schema":"heptatrader.ibkr-paper-single-stp-whatif.v1",
            "broker_order_preview_only":True,
            "real_bracket_submitted":False,
            "native_stop_protection_proven":False,
            "risk_owner_unchanged":True,
            "paper_client_id":CLIENT_ID,
            "what_if_true":True,
            "quantity_eur":str(QTY),
            "contract_id":CONID,
            "stop_price":synthetic_stop_price(bid),
            "active_broker_orders_affected":"UNVERIFIED"}
    try:
        app.connect("127.0.0.1",PORT,CLIENT_ID)
        thread=threading.Thread(target=app.run,daemon=True)
        thread.start()
        if not app.account_ready.wait(7) or not app.next_ready.wait(7) or not app.paper_account:
            raise RuntimeError("BROKER_PAPER_ACCOUNT_AND_NATIVE_ID_NOT_VERIFIED")
        c,o=exact_whatif_order(app.paper_account,bid,app.next_id)
        app.expected_order_id=app.next_id
        app.expected_account=app.paper_account
        app.expected_stop_price=o.auxPrice
        app.allow_preview=True
        app.placeOrder(app.expected_order_id,c,o)
        app.preview_ready.wait(12)
        broker_error_for_preview=any(
            e["request_id_match"] and e["code"] not in ERRORS_INFO
            for e in app.broker_error_codes)
        preview_exact=exact_preview_callback_attested(app)
        report.update(
            status=("PREVIEW_EXACT_ECHO_CONFIRMED_NONWORKING" if preview_exact
                    else "PREVIEW_ECHO_MISMATCH_OR_UNKNOWN"),
            exact_echo_verified=preview_exact,
            exact_echo_count=app.exact_preview_echoes,
            invalid_echo_count=app.invalid_preview_echoes,
            request_id_error=broker_error_for_preview,
            submitted_as_whatif=True,
            broker_sdk_orders_attempted=app.sent_preview,
            raw_outbound_opcodes=app.message_codes,
            callbacks=app.order_states,
            order_statuses=app.order_status_callbacks,
            broker_error_codes=app.broker_error_codes,
            execution_callbacks=app.executions,
            native_order_id_sha256=hashlib.sha256(
                str(app.expected_order_id).encode()).hexdigest())
        if app.executions:
            report["status"]="UNEXPECTED_REAL_EXECUTION_FATAL"
            report["exact_echo_verified"]=False
        # No accepted working protection conclusion is possible from whatIf.
    except Exception as exc:
        report.update(status="PREVIEW_FAILED_OR_UNKNOWN",
            exception_type=type(exc).__name__,
            exception_label=str(exc)[:110])
    finally:
        try:app.disconnect()
        except Exception:pass
        if thread:thread.join(timeout=2)
    return report

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--paper-whatif",action="store_true")
    ap.add_argument("--evidence-dir",type=Path)
    a=ap.parse_args()
    quote=native_read("market.get_quote")
    now=int(time.time()*1000)
    bid=require_fresh_quote_observation(quote,now)
    price=synthetic_stop_price(bid)
    if not a.paper_whatif:
        print(json.dumps({"dry_run":True,"whatIf":True,"price":price,
                          "contract_id":CONID,"quantity_eur":str(QTY)},sort_keys=True))
        return
    if not a.evidence_dir:
        ap.error("explicit --evidence-dir required")
    root=a.evidence_dir
    if root.is_symlink():raise RuntimeError("SYMLINKED_EVIDENCE_ROOT")
    root.mkdir(mode=0o700,parents=True,exist_ok=True)
    if root.stat().st_uid!=os.geteuid() or root.stat().st_mode & 0o077:
        raise RuntimeError("EVIDENCE_SCOPE_NOT_PRIVATE")
    # Durable ONE-SHOT guard exists BEFORE ANY API preview attempt.
    lease=root/"whatif-preview-executed-once.lock"
    fd=os.open(lease,os.O_EXCL|os.O_CREAT|os.O_WRONLY|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,"w") as f:
        f.write(json.dumps({"scope":"ONE_PAPER_WHATIF", "created_ms":now})+"\n")
        f.flush();os.fsync(f.fileno())
    before=observe_orders()
    receipt=one_preview(bid)
    try:
        after=observe_orders()
        receipt["active_orders_before_count"]=before["count"]
        receipt["active_orders_after_count"]=after["count"]
        receipt["active_orders_equal"]=before==after
        receipt["active_broker_orders_affected"]="NONE_OBSERVED" if before==after else "DRIFT_OR_UNKNOWN"
        if before!=after and receipt.get("exact_echo_verified"):
            # The resident strategy may change orders independently, so
            # never attribute a drift to WhatIf or claim clean non-effect.
            receipt["status"]="PREVIEW_ACCOUNT_ORDER_SET_CHANGED_OR_UNKNOWN"
            receipt["exact_echo_verified"]=False
    except Exception as exc:
        receipt["active_broker_orders_affected"]="UNVERIFIED"
        receipt["post_observer_failure"]=type(exc).__name__
        if receipt.get("exact_echo_verified"):
            receipt["status"]="PREVIEW_ACCOUNT_ORDER_SET_UNVERIFIED"
            receipt["exact_echo_verified"]=False
    receipt["recorded_at_ms"]=int(time.time()*1000)
    payload=(json.dumps(receipt,indent=2,sort_keys=True)+"\n").encode()
    dest=root/"whatif-preview-result.json"
    fd=os.open(dest,os.O_EXCL|os.O_CREAT|os.O_WRONLY|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,"wb") as f:f.write(payload);f.flush();os.fsync(f.fileno())
    print(json.dumps({"status":receipt.get("status"),
        "paper_preview_not_protection":True,
        "broker_active_orders_equal":receipt.get("active_orders_equal"),
        "broker_errors":receipt.get("broker_error_codes"),
        "response_received":bool(receipt.get("callbacks")),
        "execution_callbacks":receipt.get("execution_callbacks"),
        "sha256":hashlib.sha256(payload).hexdigest()},sort_keys=True))

if __name__=="__main__":
    main()
