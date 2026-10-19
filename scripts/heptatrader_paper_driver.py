#!/usr/bin/env python3
from __future__ import annotations
import argparse,fcntl,hashlib,json,math,os,signal,stat,subprocess,tempfile,threading,time,uuid
from pathlib import Path

INSTRUMENT="EUR.USD"; QTY=25000.0; CONF=0.62; MAX_ADVERSE=0.002
FX_TARGET_NOTIONAL_USD=100000.0
FX_MAX_QUANTITY=100000.0
SAMPLE_SEC=10.0; DECISION_SEC=120.0; MAX_HISTORY=60; MODEL="gpt-6-luna"
NATIVE_ENDPOINT=None
CLOSE_RECEIPT_ROOT=None  # Set only by actual, owner-scoped Driver main().
RESIDENT_LOOP_PROGRESS_SCHEMA="heptatrader.driver-loop-progress.v1"
QUANTITY_TOLERANCE=1e-9
class DriverError(RuntimeError): pass
def now_ms(): return int(time.time()*1000)
def cid(p): return p+"-"+uuid.uuid4().hex

def strict_json(text):
    def unique(pairs):
        out={}
        for k,v in pairs:
            if k in out: raise ValueError("duplicate JSON key")
            out[k]=v
        return out
    def bad(_): raise ValueError("non-finite JSON")
    v=json.loads(text,object_pairs_hook=unique,parse_constant=bad)
    if not isinstance(v,dict): raise ValueError("JSON object required")
    return v

def run(argv,timeout=20):
    return subprocess.run([str(x) for x in argv],stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=timeout)

def maintenance_entry_hold_reason(home,uid):
    """Read existing entry holds without changing them or granting authority."""
    markers=((Path(home)/".local/state/heptatrader/operator-maintenance/maintenance-hold.json",
              "PERSISTENT_MAINTENANCE_HOLD"),
             (Path("/run/user")/str(uid)/"heptatrader/control/kill-switch",
              "NATIVE_KILL_SWITCH_PRESENT"))
    for path,reason in markers:
        try:
            path.lstat()
        except FileNotFoundError:
            continue
        except OSError:
            return "ENTRY_HOLD_STATE_UNAVAILABLE"
        return reason
    return None


def entry_inference_hold_reason(state,home,uid):
    """Cheap entry-only pause; absence of a marker never grants trade authority."""
    if state.get("halt_new_entries"):
        return "DRIVER_NEW_ENTRY_HALT"
    return maintenance_entry_hold_reason(home,uid)


def model_inference_hold_reason(state,snap,home,uid):
    """A sampling decision cannot run while its result is unusable; this grants no authority."""
    if state.get("pending") is not None:
        return "ORIGINAL_COMMAND_PENDING"
    if state.get("owner_fence_recovery_required"):
        return "OWNER_CONTROL_RECOVERY"
    if snap["active"]:
        return "ACTIVE_BROKER_ORDER"
    if abs(float(snap["position"]))<=QUANTITY_TOLERANCE:
        reason=entry_inference_hold_reason(state,home,uid)
        if reason: return reason
        if snap["owner_health"].get("mutation_ready") is not True:
            return "AUTHORITATIVE_MUTATION_NOT_READY"
    elif state.get("halt_new_entries"):
        return "HALT_REDUCE_ONLY"
    return None


def sync_model_inference(state,worker,discarded,reason):
    """Own an invalidated in-flight call until completion; never consume its old answer."""
    if reason is None:
        state.pop("model_inference_pause",None)
    else:
        state["model_inference_pause"]={"reason_code":reason,"checked_at_ms":now_ms(),
            "scope":"MODEL_INFERENCE_ONLY"}
        if worker is not None:
            discarded=True
            if worker.done(): worker=None
    return worker,discarded


def recovery_entry_hold_reason(state,home,uid):
    reason=maintenance_entry_hold_reason(home,uid)
    if reason: return reason
    if state.get("risk_halt_reason"): return "DRIVER_RISK_HALT"
    if state.get("settlement_hold_reason"): return "DRIVER_SETTLEMENT_HOLD"
    # Recovery owns its own halt. A known pre-existing independent halt stays
    # separate; legacy states without ownership metadata retain their old path.
    if (state.get("halt_new_entries") and state.get("owner_fence_owned_halt") is False
            and not state.get("lease_renewal_owned_halt")):
        return "DRIVER_NEW_ENTRY_HALT"
    return None


def resident_process_identity():
    fields=Path("/proc/self/stat").read_text().rsplit(")",1)[1].split()
    if len(fields)<=19 or not fields[19].isdigit():
        raise DriverError("resident process identity unavailable")
    return {"pid":os.getpid(),"start_ticks":fields[19]}


def record_entry_guard(state,state_path,result,decision_action,*,failure=None):
    """Persist the last actual check; this is not a current-readiness flag."""
    def bounded(value,limit):
        return " ".join(value[:8192].split())[:limit] if isinstance(value,str) else ""
    code=None if result is None else result.returncode
    stdout="" if result is None else result.stdout
    stderr="" if result is None else result.stderr
    payload={}
    if isinstance(stdout,str) and len(stdout)<=8192:
        try:payload=strict_json(stdout)
        except (ValueError,TypeError,RecursionError):pass
    def field(names,limit):
        for name in names:
            value=bounded(payload.get(name),limit)
            if value:return value
        return ""
    status=field(("status","state"),64)
    reason=field(("reason_code","reason","state","status"),256)
    if failure is not None:
        status="CALL_ERROR";reason=bounded(str(failure),256) or "guard call failed"
    else:
        status=status or ("OK" if code==0 else "ERROR")
        reason=reason or bounded(stderr,256) or bounded(stdout,256)
        if not reason and code!=0:reason="exit status "+str(code)
    observation={"checked_at_ms":now_ms(),"returncode":code,"status":status,"reason":reason,
        "decision_action":bounded(decision_action,16),"scope":"LAST_ACTUAL_ENTRY_GUARD_CHECK"}
    state["last_entry_guard"]=observation
    atomic_json(state_path,state)
    return observation

def private_dir(path):
    path.mkdir(parents=True,exist_ok=True,mode=0o700)
    st=path.lstat()
    if not stat.S_ISDIR(st.st_mode) or st.st_uid!=os.geteuid() or stat.S_IMODE(st.st_mode)!=0o700:
        raise DriverError("unsafe driver directory")

def atomic_json(path,value):
    private_dir(path.parent); raw=(json.dumps(value,sort_keys=True,separators=(",",":"))+"\n").encode()
    fd,tmp=tempfile.mkstemp(prefix=".state-",dir=path.parent)
    try:
        os.fchmod(fd,0o600); os.write(fd,raw); os.fsync(fd); os.close(fd); fd=-1
        os.replace(tmp,path); d=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
        try: os.fsync(d)
        finally: os.close(d)
    finally:
        if fd>=0: os.close(fd)
        try: os.unlink(tmp)
        except FileNotFoundError: pass

def load_state(path):
    if not path.exists():
        return {"schema":"heptatrader.paper-driver.v1","instrument":INSTRUMENT,"history":[],"pending":None,"position":0.0,
            "entry_price":None,"entry_price_basis":None,"last_decision_ms":0,"decisions":0,
            "entries":0,"exits":0,"closed_trades":0,"gross_pnl":0.0,"last_error":"",
            "halt_new_entries":False,"lease_generation":1,"lease_last_renew_ms":now_ms(),
            "lease_uncertain":False,"lease_renewal_drain":False,"lease_renewal_owned_halt":False}
    st=path.lstat()
    if not stat.S_ISREG(st.st_mode) or st.st_uid!=os.geteuid() or st.st_nlink!=1 or stat.S_IMODE(st.st_mode)!=0o600:
        raise DriverError("unsafe state file")
    v=strict_json(path.read_text())
    if v.get("schema")!="heptatrader.paper-driver.v1": raise DriverError("state schema mismatch")
    if v.get("instrument","EUR.USD")!=INSTRUMENT: raise DriverError("cross-instrument state file refused")
    return v

def native(cli,tool,*fields):
    endpoint=NATIVE_ENDPOINT if NATIVE_ENDPOINT is not None else [cli,"native"]
    p=run([*endpoint,"--io-timeout-ms","4000","--call-id",cid("driver-read"),"call",tool,*fields],15)
    if not p.stdout: raise DriverError("native transport failed: "+p.stderr[:160])
    v=strict_json(p.stdout)
    if p.returncode!=0 or v.get("status")!="ok": raise DriverError("native rejected: "+str(v.get("reason_code")))
    return v.get("payload") or {}

def physical_crypto_settlement_snapshot(h,orders,portfolio):
    owner=h.get("execution_authoritative_health") or {}
    physical=portfolio.get("settlement_observation") or {}
    epoch=physical.get("connection_epoch"); sampled=physical.get("sampled_at_ms")
    positive=lambda x:type(x) is int and x>0
    valid=(INSTRUMENT=="BTC.USD" and h.get("execution_mode")=="PAPER" and
        h.get("gateway_ready") is True and h.get("remote_execution_ready") is True and
        owner.get("source")=="IB" and owner.get("authoritative") is True and
        owner.get("startup_recovery_read_only") is True and owner.get("mutation_blocked") is True and
        owner.get("mutation_ready") is False and
        owner.get("mutation_reason")=="IB_FX_CASH_ATTESTED_BALANCE_MISMATCH" and
        orders.get("source")=="IB" and orders.get("authoritative") is True and
        orders.get("global_active_orders_complete") is True and orders.get("owner_projection_complete") is True and
        orders.get("active_order_ids")==[] and portfolio.get("source")=="IB" and
        portfolio.get("authoritative") is False and
        physical.get("source")=="IB_REQ_POSITIONS" and physical.get("authoritative") is True and
        physical.get("scope")=="PHYSICAL_CRYPTO_ONLY" and physical.get("mutation_authorized") is False and
        isinstance(physical.get("account"),str) and bool(physical.get("account")) and
        physical.get("account")==orders.get("owner_scope",{}).get("account") and
        physical.get("execution_service_epoch")==h.get("execution_service_epoch") and
        isinstance(h.get("execution_service_epoch"),str) and bool(h.get("execution_service_epoch")) and
        positive(h.get("execution_service_fencing_generation")) and
        positive(epoch) and epoch==orders.get("active_orders_connection_epoch") and
        positive(physical.get("position_generation")) and positive(physical.get("terminal_generation")) and
        positive(physical.get("active_orders_generation")) and
        physical.get("active_orders_generation")==orders.get("active_orders_generation") and
        positive(sampled) and 0<=now_ms()-sampled<=10000)
    positions=physical.get("positions")
    if (not valid or not isinstance(positions,list) or len(positions)!=1 or
            not isinstance(positions[0],dict) or positions[0].get("instrument")!="BTC.USD" or
            type(positions[0].get("quantity")) not in (int,float) or
            not math.isfinite(positions[0]["quantity"])):
        raise DriverError("physical crypto settlement observation incomplete; trading remains fenced")
    quantity=float(positions[0]["quantity"])
    return {"orders":orders,"quote":None,"position":quantity,"gross_position":abs(quantity),
        "active":[],"owner_health":owner,"service_epoch":h["execution_service_epoch"],
        "service_generation":h.get("execution_service_fencing_generation"),
        "connection_epoch":epoch,"position_generation":physical["position_generation"],
        "position_scope":"PHYSICAL_CRYPTO_ONLY","settlement_only":True}


def snapshot(cli,state=None,path=None,require_quote=True,allow_settlement=False):
    h=native(cli,"system.get_health")
    owner=h.get("execution_authoritative_health")
    if (not isinstance(owner,dict) or owner.get("source")!="IB" or owner.get("authoritative") is not True or
            any(type(owner.get(k)) is not bool for k in ("owner_fenced","owner_recovery_only","mutation_ready"))):
        raise DriverError("authoritative owner mutation readiness unavailable")
    if state is not None:
        state["active_lease_continuation_supported"]=owner.get("active_lease_continuation_supported") is True
    if state is not None and (owner["owner_fenced"] or owner["owner_recovery_only"]):
        latch_owner_fence(state,path,owner.get("mutation_reason") or "SESSION_OWNER_FENCED")
    o=native(cli,"orders.list"); p=native(cli,"portfolio.list_positions")
    if allow_settlement and not require_quote and p.get("authoritative") is False:
        return physical_crypto_settlement_snapshot(h,o,p)
    q=native(cli,"market.get_quote","instrument="+INSTRUMENT) if require_quote else None
    l=native(cli,"risk.get_limits") if require_quote else None
    invalid=(h.get("execution_mode")!="PAPER" or not h.get("gateway_ready") or not h.get("remote_execution_ready")
        or o.get("source")!="IB" or not o.get("authoritative") or not o.get("global_active_orders_complete")
        or not o.get("owner_projection_complete") or p.get("source")!="IB" or not p.get("authoritative")
        or not isinstance(p.get("positions"),list) or not isinstance(o.get("active_order_ids"),list))
    if require_quote:
        invalid=(invalid or q.get("source")!="IB" or not q.get("authoritative") or q.get("stale") is not False
            or l.get("source")!="IB" or not l.get("authoritative"))
    if invalid: raise DriverError("authoritative PAPER snapshot incomplete")
    quantities=[float(x["quantity"]) for x in p["positions"]]
    if not all(math.isfinite(x) for x in quantities): raise DriverError("nonfinite authoritative position")
    pos=sum(float(x["quantity"]) for x in p["positions"] if x.get("instrument")==INSTRUMENT)
    return {"orders":o,"quote":q,"position":pos,"gross_position":sum(abs(x) for x in quantities),
        "active":[int(x) for x in o["active_order_ids"]],"owner_health":owner,
        "service_epoch":h.get("execution_service_epoch"),"service_generation":h.get("execution_service_fencing_generation")}

def matching_command_rows(orders,pending,expected_side):
    # The current snapshot epoch proves observation freshness, not which old
    # order owned an integer. Bind to the original durable command/correlation.
    rows=[]
    for row in orders.get("recent_orders",[]):
        if row.get("command_id")!=pending.get("command_id"): continue
        if row.get("instrument",INSTRUMENT)!=INSTRUMENT: continue
        correlation=row.get("venue_correlation_id")
        if (row.get("order_id")!=pending.get("broker_order_id") or
            not isinstance(correlation,str) or not correlation.startswith("hepta-v1-sha256:") or
            len(correlation)!=len("hepta-v1-sha256:")+64 or any(c not in "0123456789abcdef" for c in correlation[len("hepta-v1-sha256:"):]) or
            row.get("side")!=expected_side): continue
        if pending.get("venue_correlation_id") not in (None,correlation): continue
        rows.append(row)
    return rows


def recent_fill(orders,pending,expected_side):
    rows=[r for r in matching_command_rows(orders,pending,expected_side)
        if r.get("economic_fill") is True]
    if len(rows)!=1: return None
    row=rows[0]
    if row.get("broker_execution_ambiguous") is not False or not row.get("broker_execution_id"): return None
    if row.get("economic_evidence_version") == 2 and row.get("economic_evidence_complete") is not True:
        return None
    execution_ids=row.get("broker_execution_ids",[row["broker_execution_id"]])
    if (not isinstance(execution_ids,list) or not execution_ids or len(execution_ids)>128 or
            any(not isinstance(x,str) or not x or len(x)>128 for x in execution_ids) or
            len(set(execution_ids))!=len(execution_ids) or row["broker_execution_id"] not in execution_ids):
        return None
    price=float(row.get("average_fill_price",0));qty=float(row.get("filled_quantity",0))
    if price<=0 or qty<=0 or not math.isfinite(price) or not math.isfinite(qty):return None
    return {"price":price,"quantity":qty,"terminal":row.get("terminal") is True,
        "side":expected_side,"venue_correlation_id":row["venue_correlation_id"],
        "broker_execution_id":row["broker_execution_id"],"broker_execution_ids":list(execution_ids),
        "observed_at_ms":row.get("broker_execution_observed_at_ms"),"broker_status":row.get("status","unknown")}


def terminal_nonfill(orders,pending,expected_side):
    rows=matching_command_rows(orders,pending,expected_side)
    if len(rows)!=1: return False
    row=rows[0]
    return (row.get("terminal") is True and row.get("economic_fill") is False and
        row.get("broker_execution_ambiguous") is False and not row.get("broker_execution_id") and
        row.get("filled_quantity")==0 and row.get("average_fill_price")==0 and
        row.get("status") in {"Cancelled","ApiCancelled","Rejected","Inactive"})

def copy_exit_evidence(exits):
    return [dict(item) for item in exits]

def record_crypto_exit(state,pending,fill):
    parent=state.get("entry_command_id")
    if not parent or pending.get("parent_entry_command_id")!=parent:
        return  # Economic settlement is retained, but cannot qualify a driver.
    rows=state.setdefault("crypto_exit_evidence",[])
    if any(x["command_id"]==pending["command_id"] for x in rows): return
    rows.append({"command_id":pending["command_id"],"execution_id":fill["broker_execution_id"],
        "execution_ids":list(fill.get("broker_execution_ids",[fill["broker_execution_id"]])),
        "quantity":float(fill["quantity"]),"price":float(fill["price"])})

def crypto_roundtrip_qualified(state):
    proof=state.get("crypto_roundtrip_proof")
    if not isinstance(proof,dict) or not proof.get("entry_command_id") or not proof.get("entry_execution_id"):
        return False
    rows=proof.get("exits");qty=proof.get("entry_quantity")
    if type(qty) not in (int,float) or not math.isfinite(qty) or qty<=0 or not isinstance(rows,list) or not rows:
        return False
    seen=set();total=0.0
    for row in rows:
        if (not isinstance(row,dict) or not row.get("command_id") or not row.get("execution_id") or
            row["command_id"] in seen or type(row.get("quantity")) not in (int,float) or
            not math.isfinite(row["quantity"]) or row["quantity"]<=0): return False
        seen.add(row["command_id"]);total+=row["quantity"]
    return math.isfinite(total) and abs(total-qty)<=QUANTITY_TOLERANCE

def paper_cmd(binary,socket,token,outbox,op,extra):
    p=run([binary,op,"--socket",socket,"--token-file",token,"--directory",outbox,*extra],20)
    if not p.stdout: return {"ok":False,"reason":"PROCESS_NO_RESPONSE"}
    v=strict_json(p.stdout); v["process_returncode"]=p.returncode; return v

DEFINITE_APPLICATION_REJECTIONS={"error","rejected","permission_denied","invalid_tool"}


def response_transport_ok(value):
    if "transport_ok" in value: return value.get("transport_ok") is True
    return bool(value.get("ok"))


def response_application_status(value):
    status=value.get("application_status")
    if isinstance(status,str) and status: return status
    result=value.get("result")
    if isinstance(result,dict) and isinstance(result.get("status"),str): return result["status"]
    return "ok" if value.get("ok") else ""


def response_application_reason(value):
    reason=value.get("application_reason")
    if isinstance(reason,str) and reason: return reason
    result=value.get("result")
    if isinstance(result,dict) and isinstance(result.get("reason_code"),str): return result["reason_code"]
    return str(value.get("reason", ""))


def latch_owner_fence(state,path,reason):
    if not state.get("owner_fence_recovery_required"):
        state["owner_fence_owned_halt"]=not state.get("halt_new_entries",False)
    state["owner_fence_recovery_required"]=True
    state["owner_fence_block_reason"]=reason
    state["owner_control_recovery"]={"state":"AUDIT_REQUIRED","reason_code":reason,"checked_at_ms":now_ms()}
    state["owner_recovery_wait_reason"]="OWNER_AUDIT_REQUIRED"
    state["halt_new_entries"]=True
    state["last_error"]="owner fence recovery required: "+reason
    atomic_json(path,state)

def fx_order_quantity(reference):
    reference=float(reference)
    if not math.isfinite(reference) or reference<=0:
        raise DriverError("invalid FX reference price")
    quantity=min(FX_MAX_QUANTITY,float(math.floor(FX_TARGET_NOTIONAL_USD/reference)))
    if quantity<=0 or quantity>FX_MAX_QUANTITY or quantity*reference>FX_TARGET_NOTIONAL_USD+1e-9:
        raise DriverError("FX notional budget unavailable")
    return quantity

def crypto_market_cash_budget(ask, *, cash_limit=25000.0, base_limit=1.0, allow_smaller=False):
    ask=float(ask)
    if not math.isfinite(ask) or ask<=0:
        raise DriverError("invalid crypto ask")
    # Cash limits requested spend, not the eventual base quantity. Round DOWN
    # to cents and let Execution recheck against its own fresh Broker quote.
    cash_limit=float(cash_limit);base_limit=float(base_limit)
    if (not math.isfinite(cash_limit) or not math.isfinite(base_limit) or
            cash_limit<=0 or base_limit<=0 or base_limit>QTY):
        raise DriverError("crypto market cash limit invalid")
    # The resident path keeps its exact $25k policy through defaults. Canary
    # callers may select a strictly smaller cash/base envelope without changing
    # the production crypto profile. Round DOWN to cents so neither cap grows.
    cash=(math.floor(min(cash_limit, ask*base_limit)*100.0)/100.0 if allow_smaller
          else cash_limit)
    if cash<=0 or cash>cash_limit or cash/ask>base_limit+1e-15:
        raise DriverError("crypto market cash budget unavailable")
    return cash

# Diagnostic provenance, NEVER an Execution order authorization or verified
# counterfactual stop price. These are the ONLY close trigger labels emitted by
# this driver. Old close commands without a label remain explicitly unknown.
EXIT_TRIGGER_CODES=frozenset({
    "HALT_REDUCE_ONLY", "MAX_ADVERSE_MOVE", "MODEL_REVERSAL",
    "OWNER_RECOVERY_REDUCE_ONLY", "CRYPTO_CANARY_EXIT",
})

def freeze_exit_trigger(command_id,reason,position,quote=None):
    if reason is not None and reason not in EXIT_TRIGGER_CODES:
        raise DriverError("close trigger reason is not an original driver predicate")
    when=now_ms()
    record={"schema":"heptatrader.driver-close-trigger.v1",
        "original_command_id":command_id,
        "reason_code":reason if reason is not None else "LEGACY_UNATTRIBUTED",
        "reason_source":"DRIVER_EVALUATED" if reason in
            {"MAX_ADVERSE_MOVE","MODEL_REVERSAL","HALT_REDUCE_ONLY"} else
            "DRIVER_RECOVERY_OR_CANARY" if reason is not None else "UNATTRIBUTED",
        "trigger_at_ms":when,"position_at_trigger":float(position)}
    if quote is not None:
        # This is OPTIONAL diagnostic evidence. An invalid/stale quote here
        # must never prevent the original reduce-only CLOSE preparation and
        # original-command reconciliation. The trading decision was already
        # made by should_close() against the authoritative snapshot.
        try:
            if (not isinstance(quote,dict) or
                    type(quote.get("observed_at_ms")) is not int or
                    not 0 < quote["observed_at_ms"] <= when):
                raise ValueError("unverified quote timestamp")
            bid=float(quote["bid"]);ask=float(quote["ask"])
            if (not math.isfinite(bid) or not math.isfinite(ask) or
                    bid<=0 or ask<bid):
                raise ValueError("unverified quote values")
        except (KeyError,ValueError,TypeError,OverflowError):
            record["quote_evidence_status"]="UNVERIFIED_DIAGNOSTIC_OMITTED"
        else:
            record.update(quote_observed_at_ms=quote["observed_at_ms"],
                quote_bid=bid,quote_ask=ask,
                quote_evidence_status="VALIDATED")
    return record

def record_correlated_close(state,pending,fill,position_after,*,partial=False):
    """Driver-original reason correlated to an *observed* broker execution.

    Not a signed OMS proof of stop trigger, and no retroactive attribution from
    mutable last_exit_reason. Persisted only at the exact accepted terminal
    order/position branch and kept bounded for operator attribution.
    """
    command_id=pending.get("command_id")
    execution_id=fill.get("broker_execution_id")
    venue_id=fill.get("venue_correlation_id")
    if not all(isinstance(value,str) and value for value in
               (command_id,execution_id,venue_id)):
        raise DriverError("original close command/execution attribution missing")
    trigger=pending.get("exit_trigger")
    if (not isinstance(trigger,dict) or trigger.get("original_command_id")!=command_id or
            trigger.get("schema")!="heptatrader.driver-close-trigger.v1"):
        trigger={"schema":"heptatrader.driver-close-trigger.v1",
            "original_command_id":command_id,
            "reason_code":"LEGACY_UNATTRIBUTED","reason_source":"LEGACY_MISSING"}
    receipt={"schema":"heptatrader.driver-close-command-attribution.v1",
        "instrument":INSTRUMENT,"original_command_id":command_id,
        "broker_order_id":pending.get("broker_order_id"),
        "broker_execution_id":execution_id,
        "broker_execution_ids":list(fill.get("broker_execution_ids",[execution_id])),
        "venue_correlation_id":venue_id,
        "broker_fill_quantity":float(fill.get("quantity",0.0)),
        "broker_average_fill_price":float(fill.get("price",0.0)),
        "exit_trigger":dict(trigger),
        "position_before_send":pending.get("position_before_send"),
        "position_after_terminal_fill":float(position_after),
        "terminal_partial_exit":bool(partial),
        "broker_terminal_observed":bool(fill.get("terminal")),
        "recorded_at_ms":now_ms(),
        "evidence_scope":"DRIVER_REPORTED_REASON_WITH_EXACT_BROKER_FILL_NOT_BROKER_CAUSAL_PROOF"}
    history=state.setdefault("close_command_attribution",[])
    if not isinstance(history,list):
        raise DriverError("close attribution history corrupted")
    for prior in history:
        if isinstance(prior,dict) and prior.get("original_command_id")==command_id:
            if (prior.get("broker_execution_id")!=execution_id or
                    prior.get("broker_execution_ids")!=receipt["broker_execution_ids"] or
                    prior.get("venue_correlation_id")!=venue_id or
                    prior.get("exit_trigger")!=receipt["exit_trigger"]):
                raise DriverError("original close command attribution mismatch")
            return prior
    history.append(receipt)
    # Limit historical state growth without altering original Execution ledger.
    if len(history)>64: del history[:-64]
    return receipt

def persist_close_receipt(folder, receipt):
    """Owner-private create-only CLOSE intent evidence; not Broker authority.

    The accepted original CLOSE remains reconciled even when this optional
    diagnostic cannot be written. A crash after fsync and before the normal
    Driver state write is handled by exact content comparison on replay.
    """
    cmd=receipt.get("original_command_id") if isinstance(receipt,dict) else None
    if (receipt.get("schema")!="heptatrader.driver-close-command-attribution.v1"
            or not isinstance(cmd,str) or not 1<=len(cmd)<=128
            or receipt.get("instrument")!=INSTRUMENT):
        raise DriverError("durable close receipt scope invalid")
    folder=Path(folder)
    private_dir(folder)
    name=hashlib.sha256(cmd.encode("utf-8")).hexdigest()+".json"
    payload=(json.dumps(receipt,sort_keys=True,separators=(",",":"),
                        ensure_ascii=False,allow_nan=False)+"\n").encode("utf-8")
    if not 0<len(payload)<=65536:
        raise DriverError("durable close receipt bounds")
    dirfd=os.open(folder,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        try:
            fd=os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,
                       0o600,dir_fd=dirfd)
        except FileExistsError:
            fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW,dir_fd=dirfd)
            try:
                st=os.fstat(fd)
                if (not stat.S_ISREG(st.st_mode) or st.st_nlink!=1 or
                        st.st_uid!=os.geteuid() or
                        stat.S_IMODE(st.st_mode)!=0o600 or not 0<st.st_size<=65536):
                    raise DriverError("unsafe existing close receipt")
                old=b""
                while len(old)<st.st_size:
                    piece=os.read(fd,st.st_size-len(old))
                    if not piece:raise DriverError("short existing close receipt")
                    old+=piece
            finally:
                os.close(fd)
            existing=strict_json(old.decode("utf-8"))
            def stable(value):
                return {k:v for k,v in value.items() if k!="recorded_at_ms"}
            if stable(existing)!=stable(receipt):
                raise DriverError("conflicting immutable close receipt")
            return "EXACT_EXISTING"
        try:
            view=memoryview(payload)
            while view:
                n=os.write(fd,view)
                if n<=0:raise DriverError("short durable close receipt write")
                view=view[n:]
            os.fsync(fd)
        finally:
            os.close(fd)
        os.fsync(dirfd)
        return "FSYNCED_CREATE_ONLY"
    finally:
        os.close(dirfd)


def record_close_diagnostic(state,pending,fill,position_after,*,partial=False):
    """Never turn nonauthoritative attribution into a broker settlement gate.

    Called only AFTER original command, execution and position reconciliation
    succeeded. The strict recorder still rejects false/conflicting evidence;
    its failure is reported without inventing a receipt or blocking the already
    confirmed close. Order, owner and risk validation exceptions are NOT caught.
    """
    try:
        receipt=record_correlated_close(state,pending,fill,position_after,partial=partial)
        if CLOSE_RECEIPT_ROOT is not None:
            persist_close_receipt(CLOSE_RECEIPT_ROOT,receipt)
    except Exception as exc:
        state['last_close_attribution_error']={
            'schema':'heptatrader.close-attribution-diagnostic-error.v1',
            'original_command_id':pending.get('command_id'),
            'exception_type':type(exc).__name__,
            'reason':str(exc)[:256],
            'observed_at_ms':now_ms(),
            'evidence_scope':'DIAGNOSTIC_FAILURE_NOT_BROKER_STATE'}

def prepare_send(state,path,binary,socket,token,outbox,kind,quote=None,side=None,*,crypto_cash_limit=25000.0,crypto_base_limit=1.0,exit_reason=None):
    if state.get("pending") is not None:
        raise DriverError("original command pending; replacement preparation forbidden")
    if kind!="CLOSE" and exit_reason is not None:
        raise DriverError("exit trigger cannot be assigned to OPEN")
    if kind=="CLOSE" and exit_reason is not None and exit_reason not in EXIT_TRIGGER_CODES:
        raise DriverError("unknown close trigger code")
    # Validate and timestamp the original observed trigger BEFORE preparing the
    # signed HSR1 command: a bad quote must not mint an orphan Broker command.
    close_trigger=(freeze_exit_trigger("ORIGINAL_COMMAND_NOT_YET_ASSIGNED",
        exit_reason,state.get("position",0.0),quote) if kind=="CLOSE" else None)
    preview=cid("driver-preview")
    cash=None
    if kind=="OPEN":
        ref=float(quote["ask"] if side=="BUY" else quote["bid"])
        if INSTRUMENT=="BTC.USD":
            if side!="BUY": raise DriverError("crypto short entries are not supported")
            canary_scope=(crypto_cash_limit!=25000.0 or crypto_base_limit!=1.0)
            cash=crypto_market_cash_budget(ref,cash_limit=crypto_cash_limit,base_limit=crypto_base_limit,
                allow_smaller=canary_scope)
            prepared=paper_cmd(binary,socket,token,outbox,
                "prepare-crypto-canary-open" if canary_scope else "prepare-crypto-open",
                ["--call-id",preview,"--price",format(ref,".17g"),"--cash-quantity",format(cash,".17g"),"--expiry",str(now_ms()+45000)])
        else:
            quantity=fx_order_quantity(ref)
            prepared=paper_cmd(binary,socket,token,outbox,"prepare-open",
                ["--call-id",preview,"--side",side,"--quantity",format(quantity,".17g"),
                 "--price",format(ref,".17g"),"--expiry",str(now_ms()+45000)])
    else:
        ref=None; prepared=paper_cmd(binary,socket,token,outbox,"prepare-crypto-close" if INSTRUMENT=="BTC.USD" else "prepare-close",["--call-id",preview])
    if not prepared.get("ok") or not prepared.get("command_id"):
        reason=response_application_reason(prepared) or str(prepared.get("reason", ""))
        if reason=="SESSION_OWNER_FENCED": latch_owner_fence(state,path,reason)
        raise DriverError("prepare failed: "+reason)
    pending={"kind":kind,"command_id":prepared["command_id"],"send_state":"PREPARED",
        "created_at_ms":now_ms(),"instrument":INSTRUMENT,"broker_order_id":None,"side":side,"reference_price":ref,
        "position_before_send":float(state.get("position",0.0))}
    if kind=="CLOSE":
        close_trigger["original_command_id"]=prepared["command_id"]
        pending["exit_trigger"]=close_trigger
    if cash is not None:
        pending.update(quantity_mode="CASH",cash_quantity=cash,order_type="MKT",tif="IOC")
    elif kind=="OPEN":
        pending.update(quantity_mode="BASE_NOTIONAL_TARGET",order_quantity=quantity,
            target_notional_usd=FX_TARGET_NOTIONAL_USD,order_type="MKT",tif="DAY")
    if INSTRUMENT=="BTC.USD" and kind=="CLOSE":
        pending["parent_entry_command_id"]=state.get("entry_command_id")
    state["pending"]=pending; atomic_json(path,state)
    # This state precedes the one and only transport attempt. A crash here must
    # inspect the original HSR1 command and can never mint a replacement ID.
    pending["send_state"]="POSSIBLY_SENT"; atomic_json(path,state)
    sent=paper_cmd(binary,socket,token,outbox,"submit",["--command-id",pending["command_id"]])
    transport_ok=response_transport_ok(sent)
    application_status=response_application_status(sent)
    application_reason=response_application_reason(sent)
    pending["last_submit_transport_ok"]=transport_ok
    pending["last_submit_reason"]=str(sent.get("reason", ""))
    pending["application_status"]=application_status
    pending["application_reason"]=application_reason
    pending["application_observed_at_ms"]=now_ms()
    result=sent.get("result") if isinstance(sent.get("result"),dict) else {}
    if isinstance(result.get("order_id"),int) and result["order_id"]>=0:
        pending["broker_order_id"]=result["order_id"]
    if not transport_ok or not application_status or application_status=="uncertain":
        pending["send_state"]="POSSIBLY_SENT"
    elif application_status in {"ok","duplicate"}:
        pending["send_state"]="ACKNOWLEDGED"
    elif application_status in DEFINITE_APPLICATION_REJECTIONS:
        pending["send_state"]="DEFINITELY_REJECTED"
        state["last_error"]="definite non-send: "+application_reason
        if application_reason=="SESSION_OWNER_FENCED":
            latch_owner_fence(state,path,application_reason)
    else:
        pending["send_state"]="POSSIBLY_SENT"
    atomic_json(path,state)

def inspect_pending(state,path,binary,socket,token,outbox,snap):
    p=state.get("pending")
    if p is None:return  # A reconciled command is never counted or queried twice.
    if p.get("send_state")=="DEFINITELY_REJECTED":
        before=p.get("position_before_send");current=float(snap["position"])
        if (type(before) not in (int,float) or not math.isfinite(float(before)) or
                snap["active"] or abs(current-float(before))>QUANTITY_TOLERANCE or p.get("broker_order_id") is not None):
            state["last_error"]="definite rejection awaits authoritative unchanged-state proof"
            atomic_json(path,state); return
        state["position"]=current;state["pending"]=None
        state["last_error"]="definite non-send reconciled: "+str(p.get("application_reason", ""))
        atomic_json(path,state);return
    inspected=paper_cmd(binary,socket,token,outbox,"inspect",
        ["--command-id",p["command_id"],"--call-id",cid("driver-status")])
    if not response_transport_ok(inspected):
        p["last_status"]={"at_ms":now_ms(),"reason_code":str(inspected.get("reason","INSPECTION_TRANSPORT_FAILED"))}
        atomic_json(path,state);return
    inspect_status=response_application_status(inspected)
    if inspect_status not in {"ok","duplicate"} or not isinstance(inspected.get("result"),dict):
        p["last_status"]={"at_ms":now_ms(),"reason_code":response_application_reason(inspected) or "ORIGINAL_COMMAND_STATUS_UNAVAILABLE"}
        atomic_json(path,state);return
    payload=inspected["result"].get("payload") or {}
    if (payload.get("authoritative") is not True or payload.get("command_id")!=p["command_id"]):
        p["last_status"]={"at_ms":now_ms(),"reason_code":"ORIGINAL_COMMAND_STATUS_UNAVAILABLE"}
        atomic_json(path,state); return
    # Original-command status is another IPC round trip. A restart between
    # snapshot and inspect must not splice two service authorities into a fill.
    if snap.get("settlement_only") and (
            not isinstance(snap.get("service_epoch"),str) or not snap["service_epoch"] or
            payload.get("execution_service_epoch")!=snap["service_epoch"] or
            type(snap.get("service_generation")) is not int or snap["service_generation"]<=0 or
            type(payload.get("execution_service_fencing_generation")) is not int or
            payload["execution_service_fencing_generation"]!=snap["service_generation"]):
        p["last_status"]={"at_ms":now_ms(),"reason_code":"ORIGINAL_COMMAND_SNAPSHOT_MISMATCH"}
        state["last_error"]="original command snapshot changed; settlement remains pending"
        atomic_json(path,state); return
    if type(payload.get("order_id")) is int and payload["order_id"]>=0:
        p["broker_order_id"]=payload["order_id"]
    oid=p.get("broker_order_id");pos=float(snap["position"]);active=snap["active"]
    expected_side=p.get("side") if p["kind"]=="OPEN" else ("SELL" if float(state.get("position",0))>0 else "BUY")
    fill=recent_fill(snap["orders"],p,expected_side)
    if fill:
        p["venue_correlation_id"]=fill["venue_correlation_id"]
        p["broker_execution_id"]=fill["broker_execution_id"]
    fill_price=fill.get("price") if fill else None
    if INSTRUMENT=="BTC.USD" and fill:
        before=float(p.get("position_before_send",state.get("position",0)))
        expected=pos-before if p["kind"]=="OPEN" else before-pos
        if (not fill.get("terminal") or expected<=0 or
                abs(expected-float(fill["quantity"]))>QUANTITY_TOLERANCE):
            state["last_error"]="crypto fill awaits exact terminal position-delta reconciliation"
            atomic_json(path,state);return
    p["last_status"]={"at_ms":now_ms(),"command_status":payload.get("command_status"),"reason_code":payload.get("reason_code","")}
    expected_sign=1.0 if p.get("side")=="BUY" else -1.0 if p.get("side")=="SELL" else 0.0
    expected_open_quantity=(float(p.get("order_quantity",QTY))
        if p["kind"]=="OPEN" and INSTRUMENT!="BTC.USD" else QTY)
    open_position_matches=(p["kind"]=="OPEN" and not active and abs(pos)>QUANTITY_TOLERANCE and
        (abs(pos)<=expected_open_quantity+QUANTITY_TOLERANCE or (INSTRUMENT=="BTC.USD" and p.get("quantity_mode")=="CASH")) and
        expected_sign*pos>0 and fill is not None and
        payload.get("command_status")=="accepted")
    if open_position_matches:
        if INSTRUMENT=="BTC.USD":
            state.update(entry_command_id=p["command_id"],entry_quantity=float(fill["quantity"]),
                entry_broker_execution_id=fill["broker_execution_id"],entry_venue_correlation_id=fill["venue_correlation_id"],
                entry_broker_execution_ids=list(fill.get("broker_execution_ids",[fill["broker_execution_id"]])),
                entry_cash_quantity=p.get("cash_quantity"),crypto_exit_evidence=[])
        state["position"]=pos; state["entries"]+=1
        state["entry_price"]=fill_price
        state["entry_price_basis"]="broker_average_fill_price" if fill else "unavailable"
        observed=fill.get("observed_at_ms") if fill else None
        state["entry_at_ms"]=observed if type(observed) is int and 0<observed<=now_ms() else now_ms()
        state["pending"]=None
        cash_sized=INSTRUMENT=="BTC.USD" and p.get("quantity_mode")=="CASH"
        expected_entry_quantity=(float(p.get("order_quantity",QTY)) if not cash_sized else QTY)
        if (abs(pos)<expected_entry_quantity-QUANTITY_TOLERANCE and not cash_sized) or not fill or abs(pos)>expected_entry_quantity+QUANTITY_TOLERANCE:
            if INSTRUMENT=="BTC.USD" and not state.get("halt_new_entries"):
                state["crypto_partial_owned_halt"]=True
            state["halt_new_entries"]=True
            state["risk_halt_reason"]="PARTIAL_OR_UNPROVEN_ENTRY"
            state["last_error"]="entry outside nominal quantity or without full proof; exact reduce-only exit required"
        else: state["last_error"]=""
    elif (p["kind"]=="CLOSE" and abs(pos)<QUANTITY_TOLERANCE and not active and fill is not None and
            payload.get("command_status")=="accepted"):
        record_close_diagnostic(state,p,fill,pos)
        entry=state.get("entry_price"); old=float(state.get("position",0))
        if fill_price and entry and old:
            closed_qty=min(abs(old),float(fill.get("quantity",QTY)))
            state["gross_pnl"]+=((fill_price-entry)*closed_qty if old>0 else (entry-fill_price)*closed_qty)
        if INSTRUMENT=="BTC.USD":
            record_crypto_exit(state,p,fill)
            exits=state.get("crypto_exit_evidence",[])
            if (state.get("entry_command_id") and state.get("entry_broker_execution_id") and exits and
                abs(sum(x["quantity"] for x in exits)-float(state.get("entry_quantity",0)))<=QUANTITY_TOLERANCE):
                state["crypto_roundtrip_proof"]={"entry_command_id":state["entry_command_id"],
                    "entry_execution_id":state["entry_broker_execution_id"],"entry_quantity":state["entry_quantity"],
                    "exits":copy_exit_evidence(exits),"verified_at_ms":now_ms()}
        state.update(position=0.0,entry_price=None,entry_price_basis=None,entry_at_ms=None,pending=None,last_error="")
        state["exits"]+=1; state["closed_trades"]+=1
        if (INSTRUMENT=="BTC.USD" and state.get("crypto_partial_owned_halt") and
                state.get("risk_halt_reason") in {"PARTIAL_OR_UNPROVEN_ENTRY","PARTIAL_FLATTEN"} and
                not state.get("owner_fence_recovery_required") and not state.get("lease_uncertain")):
            state["crypto_partial_owned_halt"]=False
            state["risk_halt_reason"]=""
            state["halt_new_entries"]=False
    elif (p["kind"]=="CLOSE" and not active and fill and fill.get("terminal") and
          abs(pos)>QUANTITY_TOLERANCE and abs(pos)<abs(float(state.get("position",0)))-QUANTITY_TOLERANCE):
        old=float(state.get("position",0));entry=state.get("entry_price")
        reduced=abs(old)-abs(pos)
        if not entry or abs(reduced-float(fill["quantity"]))>QUANTITY_TOLERANCE:
            state["last_error"]="partial exit quantity/economic evidence mismatch"
            atomic_json(path,state);return
        record_close_diagnostic(state,p,fill,pos,partial=True)
        state["gross_pnl"]+=((fill_price-entry)*reduced if old>0 else (entry-fill_price)*reduced)
        if INSTRUMENT=="BTC.USD": record_crypto_exit(state,p,fill)
        state["partial_exit_fills"]=int(state.get("partial_exit_fills",0))+1
        if INSTRUMENT=="BTC.USD" and not state.get("halt_new_entries"):
            state["crypto_partial_owned_halt"]=True
        state["position"]=pos; state["pending"]=None; state["halt_new_entries"]=True
        state["risk_halt_reason"]="PARTIAL_FLATTEN"
        state["last_error"]="partial flatten terminal; remaining exposure requires next exact reduce-only flatten"
    # A status-only rejection after a possible send is not zero-fill proof.
    # Definite pre-send refusals were handled separately above with no order ID.
    elif (terminal_nonfill(snap["orders"],p,expected_side) and
          not active and math.isfinite(pos) and
          abs(pos-float(state.get("position",0.0)))<QUANTITY_TOLERANCE):
        state["pending"]=None; state["last_error"]="terminal non-fill: "+str(payload.get("reason_code",""))
    else:
        state["last_error"]="original command awaiting correlation-bound Broker reconciliation"
    if state.get("pending") is None and fill is not None:
        state["last_reconciliation"]={"command_id":p["command_id"],"kind":p["kind"],
            "broker_order_id":p.get("broker_order_id"),"venue_correlation_id":fill["venue_correlation_id"],
            "execution_ids":list(fill.get("broker_execution_ids",[fill["broker_execution_id"]])),
            "cumulative_filled_quantity":fill["quantity"],"average_fill_price":fill["price"],
            "broker_status":fill.get("broker_status","unknown"),"position_after":state["position"],
            "position_scope":snap.get("position_scope","PAPER_BASELINE_DELTA"),
            "risk_authoritative":not snap.get("settlement_only",False),
            "service_epoch":snap.get("service_epoch"),"connection_epoch":snap.get("connection_epoch"),
            "position_generation":snap.get("position_generation"),"reconciled_at_ms":now_ms()}
    atomic_json(path,state)

def openclaw_model_ref(model):
    model=str(model).strip()
    if not model: raise DriverError("model id is empty")
    # Preserve the historical HeptaTrader Luna preference while using the
    # canonical model id advertised by the current OpenClaw catalog.
    if model=="gpt-6-luna": return "openai/gpt-5.6-luna"
    if "/" in model: return model
    return "openai/"+model

class ModelWorker:
    def __init__(self,openclaw,state_dir,history,position):
        self.position=float(position); self.observed_at_ms=int(history[-1]["observed_at_ms"])
        self._decision=None; self._error=None; self._done=threading.Event()
        snapshot=[dict(x) for x in history]
        def target():
            try: self._decision=model_decision(openclaw,state_dir,snapshot,self.position)
            except BaseException as error: self._error=error
            finally: self._done.set()
        self._thread=threading.Thread(target=target,name="heptatrader-paper-model",daemon=True)
        self._thread.start()
    def done(self): return self._done.is_set()
    def result(self):
        if not self.done(): raise DriverError("model result requested before completion")
        if self._error is not None: raise self._error
        return self._decision

def model_decision(openclaw,state_dir,history,position):
    schema=state_dir/"decision-schema.json"
    schema_value={"type":"object","required":["action","confidence","rationale"],
        "properties":{"action":{"enum":["BUY","SELL","HOLD"]},
        "confidence":{"type":"number","minimum":0,"maximum":1},"rationale":{"type":"string"}},"additionalProperties":False}
    raw=json.dumps(schema_value,separators=(",",":"))
    if not schema.exists(): schema.write_text(raw); schema.chmod(0o600)
    elif schema.read_text()!=raw: raise DriverError("decision schema drift")
    compact=[{"t":x["observed_at_ms"],"bid":x["bid"],"ask":x["ask"],"mid":x["mid"]} for x in history[-24:]]
    prompt=(f"Direction-only decision for an IB PAPER {INSTRUMENT} test. You cannot trade or use tools. "
        "Return exactly one JSON object and nothing else matching this schema: "+raw+". "
        "Prefer HOLD unless direction is coherent. BUY means bullish and SELL bearish. "
        f"Existing position={position:.17g}. Recent authoritative quotes="+json.dumps(compact,separators=(",",":")))
    p=run([openclaw,"infer","model","run","--local","--model",openclaw_model_ref(MODEL),
        "--thinking","low","--prompt",prompt,"--json"],150)
    if p.returncode!=0: raise DriverError("model decision failed: "+p.stderr[-200:])
    try:
        envelope=strict_json(p.stdout)
        outputs=envelope.get("outputs")
        if envelope.get("ok") is not True or not isinstance(outputs,list) or len(outputs)!=1:
            raise DriverError("model decision envelope invalid")
        text=outputs[0].get("text") if isinstance(outputs[0],dict) else None
        if not isinstance(text,str) or not text.strip():
            raise DriverError("model decision output missing")
        d=strict_json(text)
    except DriverError:
        raise
    except Exception as error:
        raise DriverError("model decision envelope invalid") from error
    conf=float(d.get("confidence",-1))
    if (set(d.keys())!={"action","confidence","rationale"} or
            d.get("action") not in {"BUY","SELL","HOLD"} or
            not isinstance(d.get("rationale"),str) or
            not math.isfinite(conf) or not 0<=conf<=1):
        raise DriverError("invalid model decision")
    d["confidence"]=conf; return d

def _parse_command_json(result):
    try:
        return strict_json(result.stdout) if result.stdout else {}
    except Exception:
        return {}


def _renew_command(sessionctl, supervisor, token, uid, generation):
    return run([sessionctl,"--socket",supervisor,"--io-timeout-ms","4000","renew",
        "--token-file",token,"--generation",str(generation),"--ttl-sec","86400",
        "--token-owner-uid",str(uid)],10)


def _finish_renewal(state,generation):
    owned=state.get("lease_renewal_owned_halt",False)
    state.update(lease_generation=generation,lease_last_renew_ms=now_ms(),lease_renewal_state="IDLE",
        lease_uncertain=False,lease_renewal_drain=False,lease_renewal_owned_halt=False)
    if owned and not state.get("owner_fence_recovery_required") and not state.get("risk_halt_reason"):
        state["halt_new_entries"]=False
    if not state.get("owner_fence_recovery_required"): state["last_error"]=""


def continue_active_lease(state,path,sessionctl,supervisor,token,uid,active_orders,pending):
    due=(state.get("lease_uncertain") or state.get("lease_renewal_state")=="POSSIBLY_SENT" or
        now_ms()-int(state.get("lease_last_renew_ms",0))>=4*60*60*1000)
    if not due: return
    if not state.get("halt_new_entries"):
        state["lease_renewal_owned_halt"]=True
    state["halt_new_entries"]=True
    if active_orders or pending is not None:
        state["last_error"]="lease continuation waits for original command settlement"
        atomic_json(path,state);return
    if now_ms()<int(state.get("lease_continue_retry_ms",0)):return
    state["lease_continue_retry_ms"]=now_ms()+30000
    g=int(state.get("lease_generation",1));state["lease_renewal_state"]="POSSIBLY_SENT"
    atomic_json(path,state)
    try:
        result=run([sessionctl,"--socket",supervisor,"--io-timeout-ms","4000","renew-active",
            "--token-file",token,"--generation",str(g),"--ttl-sec","86400","--token-owner-uid",str(uid)],10)
        value=_parse_command_json(result)
    except Exception:
        state["lease_uncertain"]=True;atomic_json(path,state);raise
    generation=value.get("lease_generation")
    if (result.returncode==0 and value.get("accepted") is True and value.get("reason_code")=="SESSION_LEASE_CONTINUED"
            and type(generation) is int and generation==g+1):
        _finish_renewal(state,generation);atomic_json(path,state);return
    reason=str(value.get("reason_code") or "SESSION_LEASE_CONTINUATION_UNCERTAIN")
    if reason=="SESSION_LEASE_GENERATION_MISMATCH" and type(generation) is int and generation>g:
        state["lease_generation"]=generation
    if reason in {"SESSION_OWNER_FENCE_PENDING","SESSION_OWNER_RECOVERY_REQUIRED","SESSION_LEASE_CONTINUATION_NOT_ALLOWED"}:
        latch_owner_fence(state,path,reason);return
    state["lease_uncertain"]=not bool(value)
    state["last_error"]="lease continuation pending: "+reason
    atomic_json(path,state)

def maybe_renew(state,path,sessionctl,supervisor,token,uid,position,active_orders,pending):
    if state.get("owner_fence_recovery_required"): return
    if state.get("active_lease_continuation_supported"):
        return continue_active_lease(state,path,sessionctl,supervisor,token,uid,active_orders,pending)
    if state.get("lease_uncertain") or state.get("lease_renewal_state")=="POSSIBLY_SENT":
        latch_owner_fence(state,path,"SESSION_RENEWAL_UNCERTAIN"); return
    if now_ms()-int(state.get("lease_last_renew_ms",0))<4*60*60*1000: return
    if not state.get("halt_new_entries"):
        state["lease_renewal_owned_halt"]=True
    state["halt_new_entries"]=True
    if abs(float(position))>QUANTITY_TOLERANCE or active_orders or pending is not None:
        state["lease_renewal_drain"]=True
        state["last_error"]="session renewal due; drain to exact-zero before renewal"
        atomic_json(path,state); return
    g=int(state.get("lease_generation",1)); state["lease_renewal_state"]="POSSIBLY_SENT"; atomic_json(path,state)
    try:
        result=_renew_command(sessionctl,supervisor,token,uid,g); value=_parse_command_json(result)
    except Exception:
        state["lease_uncertain"]=True; latch_owner_fence(state,path,"SESSION_RENEWAL_UNCERTAIN"); raise
    generation=value.get("lease_generation")
    if result.returncode==0 and value.get("accepted") is True and type(generation) is int and generation==g+1:
        _finish_renewal(state,generation); atomic_json(path,state); return
    state["lease_uncertain"]=not bool(value)
    latch_owner_fence(state,path,str(value.get("reason_code") or "SESSION_RENEWAL_UNCERTAIN"))


def recovery_position_flat(snap):
    # Precheck only, not resumption authority. Each isolated user PAPER owner
    # may request resume once its own instrument is flat; the native custodian
    # independently proves the exact owner binding, both BTC/FX snapshots,
    # terminal evidence, active-order zero and the unchanged token/generation.
    value=snap.get("position")
    return type(value) in (int,float) and math.isfinite(value) and value==0.0

def recover_owner(state,path,cli,sessionctl,supervisor,token,uid):
    if not state.get("owner_fence_recovery_required"): return False
    if state.get("pending") is not None: raise DriverError("recovery requires original pending command reconciliation")
    snap=snapshot(cli,state,path,False)
    if snap["active"] or not recovery_position_flat(snap):
        raise DriverError("recovery requires authoritative zero orders and zero total strategy position in the recovery scope")
    # The native custodian distinguishes an active user lease with a stale
    # recovery marker from a revoked/finalized owner. A local flag cannot do so.
    # Never retry a strategy order here. This authenticated control operation
    # re-audits Broker-owned snapshots even after a lost prior control reply.
    if state.get("owner_recovery_phase")!="VERIFY_PREVIEW":
        if now_ms()<int(state.get("owner_recovery_next_attempt_ms",0)): return False
        g=int(state.get("lease_generation",1))
        state["owner_recovery_phase"]="RESUME_SENT"
        state["owner_recovery_attempts"]=int(state.get("owner_recovery_attempts",0))+1
        state["owner_recovery_next_attempt_ms"]=now_ms()+60000
        atomic_json(path,state)
        result=run([sessionctl,"--socket",supervisor,"--io-timeout-ms","4000","paper-resume",
            "--token-file",token,"--generation",str(g),"--ttl-sec","86400","--token-owner-uid",str(uid)],10)
        value=_parse_command_json(result); generation=value.get("lease_generation")
        if (result.returncode!=0 or value.get("accepted") is not True or
                value.get("reason_code")!="SESSION_OWNER_RESUMED" or type(generation) is not int or generation!=g+1):
            if (value.get("reason_code")=="SESSION_LEASE_GENERATION_MISMATCH" and
                    type(generation) is int and generation>g):
                state["lease_generation"]=generation
            state["owner_recovery_phase"]="AUDIT_REQUIRED"
            state["owner_recovery_last_reason"]=str(value.get("reason_code") or "SESSION_RESUME_UNCERTAIN")
            atomic_json(path,state)
            raise DriverError("owner resume not confirmed: "+state["owner_recovery_last_reason"])
        state["lease_generation"]=generation
        state["owner_recovery_phase"]="VERIFY_PREVIEW"
        state["owner_resume_confirmed_ms"]=now_ms()
        atomic_json(path,state)
        control=snapshot(cli,state,path,False)
    else:
        control=snap
    owner=control["owner_health"]
    if owner["owner_fenced"]:
        state["owner_recovery_phase"]="AUDIT_REQUIRED"; atomic_json(path,state)
        raise DriverError("owner fenced again before verification")
    if owner["owner_recovery_only"] or control["active"] or not recovery_position_flat(control):
        state["owner_recovery_wait_reason"]="CONTROL_RECONCILIATION_REQUIRED"
        atomic_json(path,state)
        raise DriverError("post-resume authoritative control reconciliation is not ready")
    state["owner_control_recovery"]={"state":"RESTORED","checked_at_ms":now_ms(),
        "service_epoch":control["service_epoch"],"service_fencing_generation":control["service_generation"],
        "lease_generation":state["lease_generation"],"entry_ready":False}
    pause=recovery_entry_hold_reason(state,Path.home(),uid)
    if pause or owner["mutation_ready"] is not True:
        state["owner_recovery_wait_reason"]=pause or "ENTRY_MUTATION_GATE_NOT_READY"
        atomic_json(path,state)
        return False
    state["owner_recovery_wait_reason"]="ENTRY_SNAPSHOT_REQUIRED"
    atomic_json(path,state)
    snap=snapshot(cli,state,path); owner=snap["owner_health"]
    if (owner["owner_fenced"] or owner["owner_recovery_only"] or owner["mutation_ready"] is not True
            or snap["active"] or not recovery_position_flat(snap)
            or snap["service_epoch"]!=control["service_epoch"]
            or snap["service_generation"]!=control["service_generation"]):
        raise DriverError("post-resume authoritative mutation gate is not ready")
    pause=recovery_entry_hold_reason(state,Path.home(),uid)
    if pause:
        state["owner_recovery_wait_reason"]=pause;atomic_json(path,state);return False
    state["owner_recovery_wait_reason"]="ENTRY_PREVIEW_REQUIRED"
    guard=run([cli,"paper-on"],10)
    if guard.returncode!=0: raise DriverError("post-audit local PAPER guard not ready")
    # Capability verification only: this permit is never submitted or persisted.
    fields=["instrument="+INSTRUMENT,"side=BUY","order_type=MKT",
        "tif="+("IOC" if INSTRUMENT=="BTC.USD" else "DAY"),
        "reference_price="+format(float(snap["quote"]["ask"]),".17g"),"expires_at_ms="+str(now_ms()+4000)]
    if INSTRUMENT=="BTC.USD":
        fields.extend(["symbol=BTC","sec_type=CRYPTO","exchange=PAXOS","currency=USD",
            "cash_quantity="+format(crypto_market_cash_budget(snap["quote"]["ask"]),".17g")])
    else: fields.append("quantity="+format(fx_order_quantity(float(snap["quote"]["ask"])),".17g"))
    preview=native(cli,"risk.preview_order",*fields)
    if (preview.get("approved") is not True or not preview.get("preview_permit") or
            preview.get("service_epoch")!=snap["service_epoch"] or
            preview.get("service_fencing_generation")!=snap["service_generation"]):
        raise DriverError("post-resume preview did not prove current service readiness")
    clear_owned_halt=(state.get("owner_fence_owned_halt") or state.get("lease_renewal_owned_halt"))
    state.update(owner_fence_recovery_required=False,owner_fence_owned_halt=False,
        owner_recovery_phase="VERIFIED",owner_recovery_verified_ms=now_ms(),
        owner_fence_block_reason="",last_transient_error="",
        owner_recovery_service_epoch=snap["service_epoch"],owner_recovery_last_reason="",
        lease_uncertain=False,lease_renewal_state="IDLE",lease_renewal_drain=False,
        lease_renewal_owned_halt=False,lease_last_renew_ms=now_ms(),last_error="")
    state["owner_recovery_wait_reason"]=""
    state["owner_control_recovery"]["entry_ready"]=True
    state["owner_control_recovery"]["checked_at_ms"]=now_ms()
    if clear_owned_halt and not state.get("risk_halt_reason"): state["halt_new_entries"]=False
    atomic_json(path,state)
    return True


def rearm_session(state,path,cli,generation):
    raise DriverError("session flags cannot be rearmed directly; use audited recovery")

def entry_candidate_allowed(decision):
    """Legacy default: direction plus historical confidence threshold.

    Cost-aware adapters may replace only this OPEN-candidate predicate. Exit,
    risk, quote, session and command semantics are deliberately independent.
    """
    if not isinstance(decision,dict):
        return False
    actions={"BUY"} if INSTRUMENT=="BTC.USD" else {"BUY","SELL"}
    return decision.get("action") in actions and decision.get("confidence",-1)>=CONF

def should_close(state,pos,quote,decision):
    if state.get("halt_new_entries"): return True,"HALT_REDUCE_ONLY"
    entry=state.get("entry_price")
    if entry:
        entry=float(entry)
        if pos>0 and float(quote["bid"])<=entry*(1-MAX_ADVERSE): return True,"MAX_ADVERSE_MOVE"
        if pos<0 and float(quote["ask"])>=entry*(1+MAX_ADVERSE): return True,"MAX_ADVERSE_MOVE"
    if decision and decision["confidence"]>=CONF:
        if pos>0 and decision["action"]=="SELL": return True,"MODEL_REVERSAL"
        if pos<0 and decision["action"]=="BUY": return True,"MODEL_REVERSAL"
    return False,""

def canary_roundtrip(state,path,cli,binary,socket,token,outbox):
    if INSTRUMENT!="BTC.USD":raise DriverError("bounded canary is crypto-only")
    snap=snapshot(cli,state,path)
    bare_prequalification_halt=(state.get("halt_new_entries") is True and
        not state.get("risk_halt_reason") and not state.get("settlement_hold_reason") and
        not state.get("owner_fence_recovery_required") and not state.get("lease_uncertain") and
        not state.get("lease_renewal_owned_halt") and not state.get("owner_fence_owned_halt"))
    if (state.get("pending") is not None or state.get("position")!=0 or snap["position"]!=0 or
            snap["active"] or state.get("owner_fence_recovery_required") or
            (state.get("halt_new_entries") and not bare_prequalification_halt)):
        raise DriverError("canary requires its own authoritative flat/order-free unblocked state")
    guard=run([cli,"paper-on"],10)
    if guard.returncode:raise DriverError("local PAPER guard not ready")
    # Qualification is deliberately tiny and independent of the resident $25k
    # crypto policy: <= $150 cash and <= 0.0005 BTC at the observed ask.  The
    # lower base cap gives substantial headroom against price movement before
    # a MKT/IOC fill while remaining below the requested 0.001 BTC ceiling.
    prepare_send(state,path,binary,socket,token,outbox,"OPEN",snap["quote"],"BUY",
        crypto_cash_limit=150.0,crypto_base_limit=0.0005)
    deadline=time.monotonic()+65
    while state.get("pending") is not None and time.monotonic()<deadline:
        inspect_pending(state,path,binary,socket,token,outbox,snapshot(cli,state,path,False))
        if state.get("pending") is not None:time.sleep(1)
    if state.get("pending") is not None:raise DriverError("canary pending preserved; no resubmission")
    if state.get("position",0)<=0:raise DriverError("canary entry did not produce a proved crypto fill")
    canary_violation=""
    filled_quantity=float(state.get("position",0))
    fill_price=float(state.get("entry_price") or 0)
    if filled_quantity>0.001+QUANTITY_TOLERANCE:
        canary_violation="canary fill exceeded 0.001 BTC"
    elif fill_price>0 and filled_quantity*fill_price>150.01:
        canary_violation="canary fill exceeded $150 notional"
    snap=snapshot(cli,state,path)
    prepare_send(state,path,binary,socket,token,outbox,"CLOSE",
        exit_reason="CRYPTO_CANARY_EXIT")
    deadline=time.monotonic()+65
    while state.get("pending") is not None and time.monotonic()<deadline:
        inspect_pending(state,path,binary,socket,token,outbox,snapshot(cli,state,path,False))
        if state.get("pending") is not None:time.sleep(1)
    if state.get("pending") is not None or state.get("position")!=0:
        raise DriverError("canary exit incomplete; original state retained")
    if canary_violation:
        raise DriverError(canary_violation+"; position was flattened")
    if bare_prequalification_halt and not state.get("risk_halt_reason"):
        state["halt_new_entries"]=False
        atomic_json(path,state)
    return 0

def main():
    global INSTRUMENT,QTY,NATIVE_ENDPOINT,QUANTITY_TOLERANCE,CLOSE_RECEIPT_ROOT
    ap=argparse.ArgumentParser(); ap.add_argument("--once",action="store_true")
    ap.add_argument("--reconcile-only",action="store_true")
    ap.add_argument("--recover-owner",action="store_true")
    ap.add_argument("--instrument",choices=("EUR.USD","BTC.USD"),default="EUR.USD")
    ap.add_argument("--canary-roundtrip",action="store_true")
    ap.add_argument("--rearm-session-generation",type=int,default=0)
    args=ap.parse_args()
    if sum(bool(x) for x in (args.once,args.reconcile_only,args.rearm_session_generation,args.recover_owner,args.canary_roundtrip))>1:
        raise DriverError("choose only one one-shot operator mode")
    if os.geteuid()==0: raise DriverError("driver must be non-root")
    INSTRUMENT=args.instrument;crypto=INSTRUMENT=="BTC.USD"
    QTY=1.0 if crypto else FX_MAX_QUANTITY;QUANTITY_TOLERANCE=1e-10 if crypto else 1e-9
    home=Path.home(); state_dir=home/".local/state/heptatrader"/("crypto-driver" if crypto else "paper-driver"); private_dir(state_dir)
    outbox=state_dir/"outbox"; private_dir(outbox); state_path=state_dir/"state.json"
    CLOSE_RECEIPT_ROOT=state_dir/"close-command-receipts"
    lock=os.open(state_dir/"driver.lock",os.O_RDWR|os.O_CREAT|os.O_CLOEXEC,0o600); fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    cli=str(home/".local/bin/heptatrader"); binary=str(home/".local/share/heptatrader/current/bin/hepta-paper-command")
    sessionctl=str(home/".local/share/heptatrader/current/bin/hepta-sessionctl")
    openclaw=str(home/".npm-global/bin/openclaw")
    socket=f"/run/user/{os.geteuid()}/heptatrader/tools.sock"; supervisor=f"/run/user/{os.geteuid()}/heptatrader/supervisor.sock"
    token=str(home/".config/heptatrader"/("crypto.token" if crypto else "paper.token"))
    if crypto:
        NATIVE_ENDPOINT=[str(home/".local/share/heptatrader/current/bin/heptactl"),"--socket",socket,"--token-file",token]
    for x in (cli,binary,sessionctl,token):
        if not Path(x).exists(): raise DriverError("required runtime missing: "+x)
    stop=False
    def stopping(_sig,_frame):
        nonlocal stop; stop=True
    signal.signal(signal.SIGTERM,stopping); signal.signal(signal.SIGINT,stopping)
    state=load_state(state_path)
    if crypto:
        state.setdefault("pnl_currency","USD")
        state.setdefault("pnl_basis","BROKER_FILL_GROSS")
        state.setdefault("fees_complete",False)
        state.setdefault("net_pnl",None)
    if state.get("lease_uncertain") or state.get("lease_renewal_state")=="POSSIBLY_SENT":
        if state.get("active_lease_continuation_supported"):
            state["halt_new_entries"]=True
        else: latch_owner_fence(state,state_path,"SESSION_RENEWAL_UNCERTAIN")
    if not args.reconcile_only: atomic_json(state_path,state)
    model_worker=None; model_worker_discarded=False
    if args.canary_roundtrip:
        try:return canary_roundtrip(state,state_path,cli,binary,socket,token,str(outbox))
        finally:os.close(lock)
    if args.recover_owner:
        latch_owner_fence(state,state_path,"OPERATOR_AUDITED_RECOVERY")
        try:
            return 0 if recover_owner(state,state_path,cli,sessionctl,supervisor,token,os.geteuid()) else 1
        finally:
            os.close(lock)
    if args.rearm_session_generation:
        rearm_session(state,state_path,cli,args.rearm_session_generation)
        os.close(lock); return 0
    if args.reconcile_only:
        try:
            snap=snapshot(cli,require_quote=False,allow_settlement=crypto and state.get("pending") is not None)
            if snap.get("settlement_only"):
                if not state.get("settlement_hold_reason"):
                    state["settlement_hold_owned_halt"]=not state.get("halt_new_entries",False)
                state["settlement_hold_reason"]="IB_FX_CASH_ATTESTED_BALANCE_MISMATCH"
                state["halt_new_entries"]=True
            if state.get("pending"):
                inspect_pending(state,state_path,binary,socket,token,str(outbox),snap)
                if state.get("pending") is not None:
                    raise DriverError("original command reconciliation remains pending")
            else:
                if abs(float(state.get("position",0))-float(snap["position"]))>QUANTITY_TOLERANCE:
                    raise DriverError("position changed without an original pending command; audited evidence required")
                if state.get("settlement_hold_reason") and snap["owner_health"].get("mutation_ready") is True:
                    if (state.get("settlement_hold_owned_halt") and not state.get("risk_halt_reason") and
                            not state.get("owner_fence_recovery_required") and not state.get("lease_uncertain")):
                        state["halt_new_entries"]=False
                    state["settlement_hold_reason"]="";state["settlement_hold_owned_halt"]=False
                atomic_json(state_path,state)
            return 0
        finally:os.close(lock)
    if state.get("settlement_hold_reason"):
        raise DriverError("operator settlement hold requires full authoritative reconciliation before resident trading")
    if crypto and not crypto_roundtrip_qualified(state):
        raise DriverError("crypto resident driver requires a correlation-bound completed round trip")
    process_identity=resident_process_identity();loop_iteration=0
    while not stop:
        started=time.monotonic();loop_iteration+=1
        state["resident_loop_progress"]={"schema":RESIDENT_LOOP_PROGRESS_SCHEMA,**process_identity,
            "iteration":loop_iteration,"at_ms":now_ms()}
        try:
            pending_mode=state.get("pending") is not None
            snap=snapshot(cli,state,state_path,require_quote=False); pos=float(snap["position"])
            if not pending_mode and state.get("active_lease_continuation_supported"):
                maybe_renew(state,state_path,sessionctl,supervisor,token,os.geteuid(),pos,len(snap["active"]),None)
            inference_reason=model_inference_hold_reason(state,snap,home,os.geteuid())
            model_worker,model_worker_discarded=sync_model_inference(
                state,model_worker,model_worker_discarded,inference_reason)
            if pending_mode:
                inspect_pending(state,state_path,binary,socket,token,str(outbox),snap)
                if args.once: break
                time.sleep(max(0.2,SAMPLE_SEC-(time.monotonic()-started)))
                continue
            if state.get("owner_fence_recovery_required"):
                state["position"]=pos; state["halt_new_entries"]=True
                if not snap["active"] and abs(pos)>QUANTITY_TOLERANCE:
                    prepare_send(state,state_path,binary,socket,token,str(outbox),"CLOSE",
                        exit_reason="OWNER_RECOVERY_REDUCE_ONLY")
                elif not snap["active"] and recovery_position_flat(snap):
                    recover_owner(state,state_path,cli,sessionctl,supervisor,token,os.geteuid())
                atomic_json(state_path,state)
                if args.once: break
                time.sleep(max(0.2,SAMPLE_SEC-(time.monotonic()-started)))
                continue
            if INSTRUMENT=="BTC.USD" and abs(pos-float(state.get("position",0)))>QUANTITY_TOLERANCE:
                state["halt_new_entries"]=True;state["risk_halt_reason"]="EXTERNAL_CRYPTO_POSITION_CHANGE"
                raise DriverError("crypto position changed outside the original pending command")
            state["position"]=pos; maybe_renew(state,state_path,sessionctl,supervisor,token,os.geteuid(),
                pos,len(snap["active"]),state.get("pending"))
            if state.get("owner_fence_recovery_required"):
                model_worker,model_worker_discarded=sync_model_inference(
                    state,model_worker,model_worker_discarded,"OWNER_CONTROL_RECOVERY")
                atomic_json(state_path,state)
                if args.once: break
                time.sleep(max(0.2,SAMPLE_SEC-(time.monotonic()-started))); continue
            pause_reason=entry_inference_hold_reason(state,home,os.geteuid()) if abs(pos)<=QUANTITY_TOLERANCE else None
            if pause_reason:
                state["entry_inference_pause"]={"reason_code":pause_reason,"checked_at_ms":now_ms(),
                    "scope":"ENTRY_INFERENCE_ONLY"}
                model_worker,model_worker_discarded=sync_model_inference(
                    state,model_worker,model_worker_discarded,pause_reason)
                atomic_json(state_path,state)
                if args.once: break
                time.sleep(max(0.2,SAMPLE_SEC-(time.monotonic()-started))); continue
            state.pop("entry_inference_pause",None)
            if abs(pos)<=QUANTITY_TOLERANCE and snap["owner_health"]["mutation_ready"] is not True:
                raise DriverError("authoritative mutation state is not ready")
            # Risk halts use the existing native CLOSE path before any quote
            # request. Native execution still validates the exact reduce-only order.
            if not snap["active"] and abs(pos)>QUANTITY_TOLERANCE and state.get("halt_new_entries"):
                why="HALT_REDUCE_ONLY"
                state["last_exit_reason"]=why; atomic_json(state_path,state)
                prepare_send(state,state_path,binary,socket,token,str(outbox),"CLOSE",exit_reason=why)
                atomic_json(state_path,state)
                if args.once: break
                time.sleep(max(0.2,SAMPLE_SEC-(time.monotonic()-started))); continue
            # Price-based exits and new entries still require a fresh IB quote.
            snap["quote"]=native(cli,"market.get_quote","instrument="+INSTRUMENT)
            q=snap["quote"]
            if q.get("authoritative") is not True or q.get("source")!="IB" or q.get("stale") is not False:
                raise DriverError("authoritative instrument quote unavailable")
            mid=(float(q["bid"])+float(q["ask"]))/2
            if not state["history"] or state["history"][-1]["observed_at_ms"]!=q["observed_at_ms"]:
                state["history"].append({"observed_at_ms":q["observed_at_ms"],"bid":q["bid"],"ask":q["ask"],"mid":mid})
                state["history"]=state["history"][-MAX_HISTORY:]
            decision=None
            # Safety exits are evaluated on every sample and never wait for the model.
            if not snap["active"] and abs(pos)>QUANTITY_TOLERANCE:
                close,why=should_close(state,pos,q,None)
                if close:
                    state["last_exit_reason"]=why; atomic_json(state_path,state)
                    prepare_send(state,state_path,binary,socket,token,str(outbox),"CLOSE",
                        q,exit_reason=why)
                    atomic_json(state_path,state)
                    if args.once: break
                    time.sleep(max(0.2,SAMPLE_SEC-(time.monotonic()-started))); continue
            inference_reason=model_inference_hold_reason(state,snap,home,os.geteuid())
            model_worker,model_worker_discarded=sync_model_inference(
                state,model_worker,model_worker_discarded,inference_reason)
            if model_worker is not None and model_worker.done():
                try:
                    if model_worker_discarded:
                        raise DriverError("model decision discarded after inference hold")
                    candidate=model_worker.result()
                    age=now_ms()-model_worker.observed_at_ms
                    if abs(pos-model_worker.position)>QUANTITY_TOLERANCE:
                        raise DriverError("model decision discarded after authoritative position change")
                    if age<0 or age>60000:
                        raise DriverError("model decision discarded after market snapshot expiry")
                    decision=candidate; state["decisions"]+=1; state["last_decision"]=decision
                    state["last_decision_ms"]=now_ms(); state["last_error"]=""
                except Exception as error:
                    state["last_error"]=("model HOLD: "+str(error))[:500]
                model_worker=None; model_worker_discarded=False
            if snap["active"]:
                state["last_error"]="active broker order present; observing only"
            elif abs(pos)>QUANTITY_TOLERANCE and decision is not None:
                close,why=should_close(state,pos,q,decision)
                if close:
                    state["last_exit_reason"]=why; atomic_json(state_path,state)
                    prepare_send(state,state_path,binary,socket,token,str(outbox),"CLOSE",
                        q,exit_reason=why)
            elif abs(pos)<=QUANTITY_TOLERANCE and not state.get("halt_new_entries") and entry_candidate_allowed(decision):
                try:
                    guard=run([cli,"paper-on"],10)
                except Exception as error:
                    record_entry_guard(state,state_path,None,decision["action"],failure=error)
                    raise
                observation=record_entry_guard(state,state_path,guard,decision["action"])
                if guard.returncode!=0:
                    raise DriverError("paper guard release failed: "+observation["reason"])
                prepare_send(state,state_path,binary,socket,token,str(outbox),"OPEN",q,decision["action"])
            # A command prepared in this same iteration also blocks the next worker.
            inference_reason=model_inference_hold_reason(state,snap,home,os.geteuid())
            model_worker,model_worker_discarded=sync_model_inference(
                state,model_worker,model_worker_discarded,inference_reason)
            due=now_ms()-int(state.get("last_decision_attempt_ms",state.get("last_decision_ms",0)))>=int(DECISION_SEC*1000)
            if inference_reason is None and model_worker is None and due and len(state["history"])>=6:
                if not Path(openclaw).exists():
                    raise DriverError("model runtime missing: "+openclaw)
                state["last_decision_attempt_ms"]=now_ms(); atomic_json(state_path,state)
                model_worker_discarded=False
                model_worker=ModelWorker(openclaw,state_dir,state["history"],pos)
            atomic_json(state_path,state)
        except Exception as e:
            if state.get("owner_fence_recovery_required"):
                state["last_transient_error"]=str(e)[:500]
                state["last_error"]="owner fence recovery required: "+state.get("owner_fence_block_reason","SESSION_OWNER_FENCED")
            else:
                state["last_error"]=str(e)[:500]
            atomic_json(state_path,state)
        if args.once: break
        time.sleep(max(0.2,SAMPLE_SEC-(time.monotonic()-started)))
    os.close(lock); return 0
if __name__=="__main__": raise SystemExit(main())
