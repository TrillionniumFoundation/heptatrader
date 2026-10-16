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
            self.order_states.append({
                "state":str(getattr(orderState,"status",""))[:70],
                "echoed_whatIf":bool(getattr(order,"whatIf",False)),
                "echoed_account_is_paper":str(getattr(order,"account","")).startswith("DU"),
                "contract_id_valid":getattr(contract,"conId",None)==CONID,
                "order_type_STP":getattr(order,"orderType",None)=="STP",
                "margin_available":bool(str(getattr(orderState,"initMarginChange","")).strip())
            })
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
        app.allow_preview=True
        app.placeOrder(app.expected_order_id,c,o)
        app.preview_ready.wait(12)
        report.update(status="PREVIEW_RESPONSE_CAPTURED" if app.preview_ready.is_set()
                      else "PREVIEW_OUTCOME_UNKNOWN",
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
    if quote.get("instrument")!="EUR.USD" or quote.get("stale") is not False or quote.get("subscription_state")!="active":
        raise RuntimeError("MARKET_QUOTE_NOT_FRESH")
    now=int(time.time()*1000)
    if not isinstance(quote.get("observed_at_ms"),int) or now-quote["observed_at_ms"]>5000:
        raise RuntimeError("QUOTE_CLOCK_NOT_BOUND")
    price=synthetic_stop_price(float(quote["bid"]))
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
    receipt=one_preview(float(quote["bid"]))
    try:
        after=observe_orders()
        receipt["active_orders_before_count"]=before["count"]
        receipt["active_orders_after_count"]=after["count"]
        receipt["active_orders_equal"]=before==after
        receipt["active_broker_orders_affected"]="NONE_OBSERVED" if before==after else "DRIFT_OR_UNKNOWN"
    except Exception as exc:
        receipt["active_broker_orders_affected"]="UNVERIFIED"
        receipt["post_observer_failure"]=type(exc).__name__
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
