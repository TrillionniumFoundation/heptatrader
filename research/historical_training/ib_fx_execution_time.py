"""Bounded one-shot IB PAPER FX execution server-time read. No order method.

The callback time in OMS is local receipt time, not Execution.time. This
research collector asks IB once for recent executions using one immutable
account-scoped request, keeping query failure as failure (never inventing data).
No writes to native OMS/PnL, original feed or model state.
"""
from __future__ import annotations
import argparse
import bisect
import collections
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import logging
import math
import os
from pathlib import Path
import re
import sys
import threading
import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .common import EvidenceError, read, sha, write_once

CLIENT_ID = 8986
REQUEST_ID = 8890
INFORMATIONAL = frozenset((2104, 2105, 2106, 2107, 2108, 2158, 2172))
CONID = 12087792
TIME_FORMAT = re.compile(r"^(\d{8})[ ](\d{2}:\d{2}:\d{2})[ ]([A-Za-z][A-Za-z0-9_/+\-]{0,39})$")
UTC_FORMAT = re.compile(r"^(\d{8})-(\d{2}:\d{2}:\d{2})$")


def parse_server_time(raw):
    """IB server second precision; ambiguous/nonexistent local DST is unsafe."""
    if not isinstance(raw, str) or len(raw) > 70:
        raise EvidenceError("IB_EXECUTION_TIME_FORMAT")
    z = UTC_FORMAT.fullmatch(raw)
    m = TIME_FORMAT.fullmatch(raw)
    if z:
        stamp = datetime.strptime(z.group(1) + z.group(2), "%Y%m%d%H:%M:%S")
        return int(stamp.replace(tzinfo=timezone.utc).timestamp() * 1000)
    if not m:
        raise EvidenceError("IB_EXECUTION_TIME_FORMAT")
    stamp = datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H:%M:%S")
    # Some slim Linux tzdb packages omit old US/* aliases while retaining
    # canonical IANA rules. Map the exact documented IB alias, NEVER assume
    # a constant -04/-05 offset (DST varies by execution date).
    zone_name = {"US/Eastern":"America/New_York"}.get(m.group(3),m.group(3))
    try:
        zone = ZoneInfo(zone_name)
    except ZoneInfoNotFoundError as err:
        raise EvidenceError("IB_EXECUTION_TIME_ZONE_UNKNOWN") from err
    first = stamp.replace(tzinfo=zone, fold=0)
    second = stamp.replace(tzinfo=zone, fold=1)
    if first.utcoffset() != second.utcoffset():
        raise EvidenceError("IB_EXECUTION_TIME_DST_AMBIGUOUS")
    if first.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) != stamp:
        raise EvidenceError("IB_EXECUTION_TIME_NONEXISTENT")
    return int(first.timestamp() * 1000)


class Egress:
    """Only START_API and one exact EUR.CASH, exact-account REQ_EXECUTIONS."""
    def __init__(self, account, since):
        self.account = account
        self.since = since
        self.counts = collections.Counter()
        self.started = False
    def check(self, msg, paper, server_version):
        if not isinstance(msg, str) or not msg.endswith("\0"):
            raise EvidenceError("EXEC_TIME_WIRE_FORMAT")
        parts = msg[:-1].split("\0")
        if parts == ["71", "2", str(CLIENT_ID), ""] and not self.started:
            self.started = True
            self.counts[71] += 1
            return
        required = ["7", "3", str(REQUEST_ID), "0", self.account,
                    self.since, "EUR", "CASH", "", ""]
        if (not self.started or not paper or server_version < 180
            or parts != required or self.counts[7] != 0):
            raise EvidenceError("EXEC_TIME_READONLY_WIRE_SCOPE")
        self.counts[7] += 1


def validate_plan(history, period, since, now_ms):
    if (history.get("host") != "127.0.0.1"
        or history.get("port") != 4002
        or history.get("trade_authority") is not False
        or history.get("client_id") != 8984
        or period.get("execution_domain") != "PAPER"
        or period.get("kind") != "USER_RESET"
        or not isinstance(period.get("account"), str)
        or not period["account"].startswith("DU")
        or period.get("currency") != "USD"):
        raise EvidenceError("EXEC_TIME_EXACT_PAPER_SCOPE")
    if not isinstance(since, str) or not re.fullmatch(r"\d{8}-\d{2}:\d{2}:\d{2}", since):
        raise EvidenceError("EXEC_TIME_FILTER")
    since_ms = parse_server_time(since)
    if not 0 <= now_ms - since_ms <= 7*86400000:
        raise EvidenceError("EXEC_TIME_SEVEN_DAY_BOUND")
    if (not isinstance(period.get("started_at_ms"), int)
        or since_ms > period["started_at_ms"]):
        raise EvidenceError("EXEC_TIME_RESET_COVERAGE")
    expected = history.get("sdk_sha256")
    if not isinstance(expected, dict) or set(expected) != {"client.py", "wrapper.py", "message.py", "contract.py"}:
        raise EvidenceError("EXEC_TIME_PINNED_SDK")
    sdk = Path(history["sdk_root"])
    if not sdk.is_absolute() or sdk.resolve() != sdk:
        raise EvidenceError("EXEC_TIME_SDK_ROOT")
    for name, wanted in expected.items():
        if sha(sdk/"ibapi"/name) != wanted:
            raise EvidenceError("EXEC_TIME_SDK_CHANGED")
    return sdk, since_ms


def bounded_execution(req_id, contract, execution, account, since_ms, now_ms):
    if (req_id != REQUEST_ID or execution.acctNumber != account
        or contract.conId != CONID or contract.symbol != "EUR"
        or contract.secType != "CASH" or contract.currency != "USD"):
        raise EvidenceError("EXEC_TIME_FOREIGN_ACCOUNT_CONTRACT_REQUEST")
    ident = execution.execId
    if not isinstance(ident, str) or not 1 <= len(ident) <= 128:
        raise EvidenceError("EXEC_TIME_ID_INVALID")
    try:
        price = Decimal(str(execution.price))
        qty = Decimal(str(execution.shares))
    except InvalidOperation as err:
        raise EvidenceError("EXEC_TIME_ECONOMICS_INVALID") from err
    if not price.is_finite() or not qty.is_finite() or price <= 0 or qty <= 0:
        raise EvidenceError("EXEC_TIME_ECONOMICS_INVALID")
    at_ms = parse_server_time(execution.time)
    if not since_ms <= at_ms <= now_ms + 60_000:
        raise EvidenceError("EXEC_TIME_OUTSIDE_REQUEST")
    if execution.side not in ("BOT", "SLD") or int(execution.permId) < 0:
        raise EvidenceError("EXEC_TIME_SIDE_PERMID")
    return {
        "execution_id": ident, "server_time_original": execution.time,
        "server_time_second_ms": at_ms, "server_time_precision_ms": 1000,
        "side": execution.side, "shares": str(qty), "price": str(price),
        "permanent_order_id": int(execution.permId),
        "order_id": int(execution.orderId),
        "price_revision_pending": bool(execution.pendingPriceRevision),
    }


def _check_oms(rows, oms, account, period_start_ms):
    from laya_cell.shadow_source import strict
    row_ids = {r["execution_id"]: r for r in rows}
    seen = {}
    callback_receipts = collections.Counter()
    with Path(oms).open("rb") as f:
        for i, line in enumerate(f):
            if i >= 250000 or len(line) > 65536 or not line.endswith(b"\n"):
                raise EvidenceError("EXEC_TIME_OMS_BOUND")
            event = strict(line)
            if (event.get("event") != "broker_execution"
                or event.get("execution_domain") != "PAPER"
                or event.get("account") != account
                or event.get("instrument") != "EUR.USD"
                or event.get("ts_ms", 0) < period_start_ms):
                continue
            ident = event.get("broker_execution_id")
            if not ident or not isinstance(ident, str):
                raise EvidenceError("EXEC_TIME_OMS_EXEC_ID")
            # Reconnect/replay legitimately yields a new callback receipt
            # timestamp and connection epoch. Compare ONLY the broker's
            # original economic identity, never metadata about receipt.
            sig = (event.get("broker_econ_execution_price"),
                   event.get("broker_econ_shares"),
                   event.get("broker_econ_permanent_order_id"))
            seen_at = event.get("ts_ms")
            if type(seen_at) is not int or seen_at < period_start_ms:
                raise EvidenceError("EXEC_TIME_OMS_CALLBACK_TIME")
            if ident in seen:
                old_sig, first_at = seen[ident]
                if old_sig != sig:
                    raise EvidenceError("EXEC_TIME_OMS_DUPLICATE_CONFLICT")
                seen[ident] = (old_sig, min(first_at, seen_at))
            else:
                seen[ident] = (sig, seen_at)
            callback_receipts[ident] += 1
    matched = 0
    unmatched_price = 0
    pending_price_revision = 0
    for ident, row in row_ids.items():
        if ident not in seen:
            continue
        (price, qty, perm), callback_ms = seen[ident]
        try:
            same = (Decimal(str(price)) == Decimal(row["price"])
                    and Decimal(str(qty)) == Decimal(row["shares"])
                    and int(perm) == row["permanent_order_id"])
        except (TypeError, ValueError, InvalidOperation):
            same = False
        if not same:
            unmatched_price += 1
            continue
        if row["price_revision_pending"]:
            pending_price_revision += 1
            continue
        matched += 1
        row["earliest_oms_callback_lag_ms"] = callback_ms - row["server_time_second_ms"]
        row["oms_callback_receipts"] = callback_receipts[ident]
        row["matched_exact_oms_economics"] = True
    return {"oms_distinct_execution_count": len(seen),
            "local_callback_replay_count": sum(max(0,n-1) for n in callback_receipts.values()),
            "broker_returned_execution_count": len(rows),
            "exact_oms_economics_matches": matched,
            "economics_mismatches": unmatched_price,
            "price_revision_pending": pending_price_revision,
            "broker_returned_not_in_local_oms": len(rows) - sum(i in seen for i in row_ids),
            "returned_id_in_local_oms": sum(i in seen for i in row_ids)}


def collect(history, period, since, oms):
    now_ms = int(time.time()*1000)
    sdk, since_ms = validate_plan(history, period, since, now_ms)
    before = sha(oms)
    sys.path.insert(0, str(sdk))
    from ibapi.client import EClient
    from ibapi.wrapper import EWrapper
    from ibapi.execution import ExecutionFilter
    account = period["account"]
    boundary = Egress(account, since)

    class Reader(EWrapper, EClient):
        def __init__(self):
            EWrapper.__init__(self)
            EClient.__init__(self,self)
            self.ready = threading.Event()
            self.accounts_ready = threading.Event()
            self.done = threading.Event()
            self.paper = False
            self.end_received = False
            self.lost = False
            self.intentional_disconnect = False
            self.errors = []
            self.info = []
            self.rows = {}
            self.collect_limit = 2048
        def sendMsg(self, msg):
            boundary.check(msg, self.paper, self.serverVersion())
            return EClient.sendMsg(self, msg)
        def placeOrder(self,*a,**k):
            raise EvidenceError("EXEC_TIME_ORDER_FORBIDDEN")
        def cancelOrder(self,*a,**k):
            raise EvidenceError("EXEC_TIME_CANCEL_FORBIDDEN")
        def reqGlobalCancel(self,*a,**k):
            raise EvidenceError("EXEC_TIME_GLOBAL_CANCEL_FORBIDDEN")
        def nextValidId(self, order_id):
            self.ready.set()
        def managedAccounts(self, accounts):
            returned = [x.strip() for x in accounts.split(",") if x.strip()]
            self.paper = account in returned and bool(returned) and all(x.startswith("DU") for x in returned)
            self.accounts_ready.set()
        def error(self, request, code, message, *args):
            event = {"request_id":request,"code":code}
            if request == -1 and code in INFORMATIONAL:
                self.info.append(event)
            else:
                self.errors.append(event)
                if request == REQUEST_ID:
                    self.done.set()
        def connectionClosed(self):
            if not self.intentional_disconnect:
                self.lost = True
                self.done.set()
        def execDetails(self, request, contract, execution):
            try:
                row = bounded_execution(request,contract,execution,account,since_ms,int(time.time()*1000))
                ident = row["execution_id"]
                if ident in self.rows and self.rows[ident] != row:
                    raise EvidenceError("EXEC_TIME_BROKER_DUPLICATE_CONFLICT")
                if len(self.rows) >= self.collect_limit and ident not in self.rows:
                    raise EvidenceError("EXEC_TIME_BROKER_RESULT_CAP")
                self.rows[ident] = row
            except (ValueError,TypeError,KeyError) as err:
                self.errors.append({"request_id":request,"code":str(err)[:90]})
                self.done.set()
        def execDetailsEnd(self, request):
            if request != REQUEST_ID:
                self.errors.append({"request_id":request,"code":"FOREIGN_EXEC_END"})
            else:
                if self.end_received:
                    self.errors.append({"request_id":request,"code":"DUPLICATE_EXEC_END"})
                self.end_received = True
            self.done.set()
    app = Reader()
    worker = None
    completed = False
    receipt = {"schema":"heptatrader.ib-paper-fx-execution-server-time.v1",
               "observed_at_ms":now_ms, "period_sha256":sha(period["_source_path"]),
               "started_at_ms":period["started_at_ms"],
               "query_start_utc":since,"request_id":REQUEST_ID, "broker_client_id":CLIENT_ID,
               "read_only":True, "trade_authority":False,"broker_orders_sent":0,
               "no_historical_OHLC_execution_assumption":True,
               "time_basis":"IB_SERVER_TIME_SECOND_PRECISION_NOT_EXCHANGE_TIME",
               "oms_sha256":before}
    try:
        logging.disable(logging.CRITICAL)
        app.connect("127.0.0.1",4002,CLIENT_ID)
        worker = threading.Thread(target=app.run,daemon=True)
        worker.start()
        if not app.ready.wait(10) or not app.accounts_ready.wait(3) or not app.paper:
            raise EvidenceError("EXEC_TIME_PAPER_HANDSHAKE")
        f = ExecutionFilter()
        f.clientId = 0
        f.acctCode = account
        f.time = since
        f.symbol = "EUR"
        f.secType = "CASH"
        f.exchange = ""
        f.side = ""
        app.reqExecutions(REQUEST_ID,f)
        if not app.done.wait(20):
            raise EvidenceError("EXEC_TIME_END_TIMEOUT")
        completed = app.end_received and not app.errors and not app.lost
    except (ValueError,OSError,RuntimeError) as err:
        receipt["failure"] = type(err).__name__ + ":" + str(err)[:130]
    finally:
        app.intentional_disconnect = True
        app.disconnect()
        if worker is not None:
            worker.join(2)
    rows = sorted(app.rows.values(),key=lambda x:x["execution_id"])
    if sha(oms) != before:
        completed = False
        receipt["failure"] = "EXEC_TIME_OMS_CHANGED_DURING_READ"
    if sha(period["_source_path"]) != receipt["period_sha256"]:
        completed = False
        receipt["failure"] = "EXEC_TIME_PERIOD_CHANGED_DURING_READ"
    if completed:
        try:
            receipt["reconciliation"] = _check_oms(rows,oms,account,period["started_at_ms"])
        except (ValueError,OSError,KeyError,TypeError) as err:
            completed = False
            receipt["failure"] = type(err).__name__ + ":" + str(err)[:130]
    receipt.update(complete=completed, broker_end_received=app.end_received,
                   source_code_sha256=sha(Path(__file__)), returned_executions=rows,
                   transport_errors=app.errors, informational_events=app.info,
                   outbound_counts=dict(boundary.counts),
                   broker_returned_rows=len(rows),
                   period_account_not_published=True)
    return receipt


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--history-config",type=Path,required=True)
    parser.add_argument("--period",type=Path,required=True)
    parser.add_argument("--oms",type=Path,required=True)
    parser.add_argument("--since-utc",required=True)
    parser.add_argument("--output",type=Path,required=True)
    a=parser.parse_args()
    if a.output.exists() or a.output.is_symlink():
        raise SystemExit("OUTPUT_ALREADY_EXISTS_NO_REPEAT_REQUEST")
    history=read(a.history_config)
    period=read(a.period)
    period["_source_path"]=str(a.period)
    result=collect(history,period,a.since_utc,a.oms)
    write_once(a.output,result)
    print(json.dumps({"complete":result["complete"],
                     "broker_returned_rows":result["broker_returned_rows"],
                     "matched_oms":result.get("reconciliation",{}).get("exact_oms_economics_matches"),
                     "error_codes":result["transport_errors"],
                     "failure":result.get("failure"),
                     "broker_orders_sent":0},sort_keys=True))
    if not result["complete"]:
        raise SystemExit(2)

if __name__=="__main__":
    main()
