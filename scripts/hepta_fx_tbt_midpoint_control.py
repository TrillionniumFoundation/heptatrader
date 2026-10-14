#!/usr/bin/env python3
"""MidPoint TBT positive control; MIDPOINT IS NOT BID/ASK OR ACCOUNT NAV."""
from __future__ import annotations
import json
import logging
from pathlib import Path
import sys
import threading
import time
sys.path.insert(0, str(Path(__file__).resolve().parent))
from hepta_fx_readonly_probe import CashFarmLease, ProbeViolation, read_sdk, validate_isolated_client_id

def main():
    client_id = 29757
    rid = 6
    validate_isolated_client_id(client_id, Path.home())
    EClient, EWrapper, Contract = read_sdk(
        Path.home()/".local/share/heptatrader/vendor/ibapi-reset-check")
    expected = ["97",str(rid),"0","EUR","CASH","","0.0","","",
                "IDEALPRO","","USD","","","MidPoint","0","0"]
    counts = {}
    errors = []
    ticks = []
    ready = threading.Event()
    farm = CashFarmLease()
    logging.disable(logging.CRITICAL)

    class Client(EWrapper,EClient):
        def __init__(self):
            EClient.__init__(self, self)
            self.lost = False
        def sendMsg(self, msg):
            fields = msg.rstrip("\0").split("\0") if isinstance(msg,str) and msg.endswith("\0") else []
            op = fields[0] if fields else ""
            valid = ((op=="71" and fields==["71","2",str(client_id)])
                     or (op=="59" and fields==["59","1","1"])
                     or (op=="97" and fields==expected and "97" not in counts)
                     or (op=="98" and fields==["98",str(rid)] and "98" not in counts and "97" in counts))
            if not valid:
                raise ProbeViolation("FOREIGN_OR_MUTATING_WIRE_DENIED")
            counts[op] = counts.get(op,0)+1
            return EClient.sendMsg(self,msg)
        def nextValidId(self,_):
            ready.set()
        def error(self, req, code, msg, *args):
            if req==-1:
                farm.observe(code,msg)
            if req==rid:
                errors.append(code)
        def tickByTickMidPoint(self, req, src_time, midpoint):
            if req==rid and len(ticks)<1000:
                ticks.append((int(src_time),float(midpoint),time.time_ns()//1000000))
        def connectionClosed(self):
            self.lost=True

    c=Client()
    worker=None
    status="UNKNOWN"
    try:
        c.connect("127.0.0.1",4002,client_id)
        worker=threading.Thread(target=c.run,daemon=True)
        worker.start()
        if not ready.wait(8):status="HANDSHAKE_TIMEOUT"
        elif not farm.wait_stable(.5,8):status="CASH_FARM_UNATTESTED"
        else:
            ct=Contract()
            ct.symbol,ct.secType,ct.exchange,ct.currency="EUR","CASH","IDEALPRO","USD"
            c.reqMarketDataType(1)
            c.reqTickByTickData(rid,ct,"MidPoint",0,False)
            deadline=time.monotonic()+22
            while time.monotonic()<deadline and not errors and farm.ready.is_set() and not c.lost:
                time.sleep(.1)
            status=("REQUEST_ERROR" if errors else
                    "NO_MIDPOINT_CALLBACK" if not ticks else "MIDPOINT_PRESENT_NOT_BIDASK")
    except (OSError,ValueError,ProbeViolation) as exc:
        status="PROBE_FAIL_CLOSED"
    finally:
        try:
            if c.isConnected() and counts.get("97"):
                c.cancelTickByTickData(rid)
        except Exception:
            status="CANCEL_FAILED"
        try:c.disconnect()
        except Exception:pass
        if worker:worker.join(2)
        result={"schema":"heptatrader.tbt-midpoint-positive-control.v1",
                "status":status,"source":"IB_FX_TBT_MIDPOINT_NOT_EXECUTABLE_BIDASK",
                "instrument":"EUR|CASH|IDEALPRO|USD",
                "source_timed_ticks":len(ticks),
                "broker_tick_time_available":bool(ticks),
                "source_delay_at_last_ms":ticks[-1][2]-ticks[-1][0]*1000 if ticks else None,
                "farm_id":farm.farm_id,"request_error_codes":errors[:8],
                "wire":counts,"order_send_count":0,
                "account_common_valuation_mark":False}
        print(json.dumps(result,sort_keys=True),flush=True)
    return 0 if ticks else 2

if __name__=="__main__":
    sys.exit(main())
