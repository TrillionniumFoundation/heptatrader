#!/usr/bin/env python3
from __future__ import annotations
import argparse,fcntl,json,math,os,signal,stat,subprocess,tempfile,threading,time,uuid
from pathlib import Path

INSTRUMENT="EUR.USD"; QTY=25000.0; CONF=0.62; MAX_ADVERSE=0.002
SAMPLE_SEC=10.0; DECISION_SEC=120.0; MAX_HISTORY=60; MODEL="gpt-6-luna"
NATIVE_ENDPOINT=None
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

def snapshot(cli,state=None,path=None,require_quote=True):
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
    price=float(row.get("average_fill_price",0));qty=float(row.get("filled_quantity",0))
    if price<=0 or qty<=0 or not math.isfinite(price) or not math.isfinite(qty):return None
    return {"price":price,"quantity":qty,"terminal":row.get("terminal") is True,
        "side":expected_side,"venue_correlation_id":row["venue_correlation_id"],
        "broker_execution_id":row["broker_execution_id"]}


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
    state["halt_new_entries"]=True
    state["last_error"]="owner fence recovery required: "+reason
    atomic_json(path,state)

def crypto_market_cash_budget(ask):
    ask=float(ask)
    if not math.isfinite(ask) or ask<=0:
        raise DriverError("invalid crypto ask")
    # Cash limits requested spend, not the eventual base quantity. Round DOWN
    # to cents and let Execution recheck against its own fresh Broker quote.
    cash=math.floor(min(150.0,ask*QTY)*100.0)/100.0
    if cash<=0 or cash>150.0 or cash/ask>QTY:
        raise DriverError("crypto market cash budget unavailable")
    return cash

def prepare_send(state,path,binary,socket,token,outbox,kind,quote=None,side=None):
    if state.get("pending") is not None:
        raise DriverError("original command pending; replacement preparation forbidden")
    preview=cid("driver-preview")
    cash=None
    if kind=="OPEN":
        ref=float(quote["ask"] if side=="BUY" else quote["bid"])
        if INSTRUMENT=="BTC.USD":
            if side!="BUY": raise DriverError("crypto short entries are not supported")
            cash=crypto_market_cash_budget(ref)
            prepared=paper_cmd(binary,socket,token,outbox,"prepare-crypto-open",
                ["--call-id",preview,"--price",format(ref,".17g"),"--cash-quantity",format(cash,".17g"),"--expiry",str(now_ms()+45000)])
        else:
            prepared=paper_cmd(binary,socket,token,outbox,"prepare-open",
                ["--call-id",preview,"--side",side,"--price",format(ref,".17g"),"--expiry",str(now_ms()+45000)])
    else:
        ref=None; prepared=paper_cmd(binary,socket,token,outbox,"prepare-crypto-close" if INSTRUMENT=="BTC.USD" else "prepare-close",["--call-id",preview])
    if not prepared.get("ok") or not prepared.get("command_id"):
        reason=response_application_reason(prepared) or str(prepared.get("reason", ""))
        if reason=="SESSION_OWNER_FENCED": latch_owner_fence(state,path,reason)
        raise DriverError("prepare failed: "+reason)
    pending={"kind":kind,"command_id":prepared["command_id"],"send_state":"PREPARED",
        "created_at_ms":now_ms(),"instrument":INSTRUMENT,"broker_order_id":None,"side":side,"reference_price":ref,
        "position_before_send":float(state.get("position",0.0))}
    if cash is not None:
        pending.update(quantity_mode="CASH",cash_quantity=cash,order_type="MKT",tif="IOC")
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
    open_position_matches=(p["kind"]=="OPEN" and not active and abs(pos)>QUANTITY_TOLERANCE and
        (abs(pos)<=QTY+QUANTITY_TOLERANCE or (INSTRUMENT=="BTC.USD" and p.get("quantity_mode")=="CASH")) and
        expected_sign*pos>0 and fill is not None and
        payload.get("command_status")=="accepted")
    if open_position_matches:
        if INSTRUMENT=="BTC.USD":
            state.update(entry_command_id=p["command_id"],entry_quantity=float(fill["quantity"]),
                entry_broker_execution_id=fill["broker_execution_id"],entry_venue_correlation_id=fill["venue_correlation_id"],
                entry_cash_quantity=p.get("cash_quantity"),crypto_exit_evidence=[])
        state["position"]=pos; state["entries"]+=1
        state["entry_price"]=fill_price
        state["entry_price_basis"]="broker_average_fill_price" if fill else "unavailable"
        state["entry_at_ms"]=now_ms(); state["pending"]=None
        cash_sized=INSTRUMENT=="BTC.USD" and p.get("quantity_mode")=="CASH"
        if (abs(pos)<QTY-QUANTITY_TOLERANCE and not cash_sized) or not fill or abs(pos)>QTY+QUANTITY_TOLERANCE:
            if INSTRUMENT=="BTC.USD" and not state.get("halt_new_entries"):
                state["crypto_partial_owned_halt"]=True
            state["halt_new_entries"]=True
            state["risk_halt_reason"]="PARTIAL_OR_UNPROVEN_ENTRY"
            state["last_error"]="entry outside nominal quantity or without full proof; exact reduce-only exit required"
        else: state["last_error"]=""
    elif (p["kind"]=="CLOSE" and abs(pos)<QUANTITY_TOLERANCE and not active and fill is not None and
            payload.get("command_status")=="accepted"):
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
    atomic_json(path,state)

class ModelWorker:
    def __init__(self,codex,state_dir,history,position):
        self.position=float(position); self.observed_at_ms=int(history[-1]["observed_at_ms"])
        self._decision=None; self._error=None; self._done=threading.Event()
        snapshot=[dict(x) for x in history]
        def target():
            try: self._decision=model_decision(codex,state_dir,snapshot,self.position)
            except BaseException as error: self._error=error
            finally: self._done.set()
        self._thread=threading.Thread(target=target,name="heptatrader-paper-model",daemon=True)
        self._thread.start()
    def done(self): return self._done.is_set()
    def result(self):
        if not self.done(): raise DriverError("model result requested before completion")
        if self._error is not None: raise self._error
        return self._decision

def model_decision(codex,state_dir,history,position):
    schema=state_dir/"decision-schema.json"
    schema_value={"type":"object","required":["action","confidence","rationale"],
        "properties":{"action":{"enum":["BUY","SELL","HOLD"]},
        "confidence":{"type":"number","minimum":0,"maximum":1},"rationale":{"type":"string"}},"additionalProperties":False}
    raw=json.dumps(schema_value,separators=(",",":"))
    if not schema.exists(): schema.write_text(raw); schema.chmod(0o600)
    elif schema.read_text()!=raw: raise DriverError("decision schema drift")
    output=state_dir/("decision-"+uuid.uuid4().hex+".json")
    compact=[{"t":x["observed_at_ms"],"bid":x["bid"],"ask":x["ask"],"mid":x["mid"]} for x in history[-24:]]
    prompt=(f"Direction-only decision for an IB PAPER {INSTRUMENT} test. You cannot trade or use tools. "
        "Return required JSON only. Prefer HOLD unless direction is coherent. BUY means bullish and SELL bearish. "
        f"Existing position={position:.17g}. Recent authoritative quotes="+json.dumps(compact,separators=(",",":")))
    p=run([codex,"-a","never","exec","--ignore-user-config","--ephemeral","--skip-git-repo-check","--sandbox","read-only",
        "--disable","shell_tool","--disable","unified_exec","--disable","apps","--disable","skill_mcp_dependency_install",
        "--model",MODEL,"-c",'model_reasoning_effort="low"',"-c",'web_search="disabled"',
        "--output-schema",schema,"-o",output,prompt],150)
    if p.returncode!=0 or not output.exists(): raise DriverError("model decision failed: "+p.stderr[-200:])
    d=strict_json(output.read_text()); output.unlink(missing_ok=True); conf=float(d.get("confidence",-1))
    if d.get("action") not in {"BUY","SELL","HOLD"} or not math.isfinite(conf) or not 0<=conf<=1:
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


def recover_owner(state,path,cli,sessionctl,supervisor,token,uid):
    if not state.get("owner_fence_recovery_required"): return False
    if state.get("pending") is not None: raise DriverError("recovery requires original pending command reconciliation")
    snap=snapshot(cli,state,path)
    if snap["active"] or snap["gross_position"]!=0.0:
        raise DriverError("recovery requires authoritative zero orders and zero total strategy position")
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
    snap=snapshot(cli,state,path); owner=snap["owner_health"]
    if owner["owner_fenced"]:
        state["owner_recovery_phase"]="AUDIT_REQUIRED"; atomic_json(path,state)
        raise DriverError("owner fenced again before verification")
    if (owner["owner_recovery_only"] or owner["mutation_ready"] is not True or
            snap["active"] or snap["gross_position"]!=0.0):
        raise DriverError("post-resume authoritative mutation gate is not ready")
    guard=run([cli,"paper-on"],10)
    if guard.returncode!=0: raise DriverError("post-audit local PAPER guard not ready")
    # Capability verification only: this permit is never submitted or persisted.
    fields=["instrument="+INSTRUMENT,"side=BUY","order_type=MKT",
        "tif="+("IOC" if INSTRUMENT=="BTC.USD" else "DAY"),
        "reference_price="+format(float(snap["quote"]["ask"]),".17g"),"expires_at_ms="+str(now_ms()+4000)]
    if INSTRUMENT=="BTC.USD":
        fields.extend(["symbol=BTC","sec_type=CRYPTO","exchange=PAXOS","currency=USD",
            "cash_quantity="+format(crypto_market_cash_budget(snap["quote"]["ask"]),".17g")])
    else: fields.append("quantity="+format(QTY,".17g"))
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
    if clear_owned_halt and not state.get("risk_halt_reason"): state["halt_new_entries"]=False
    atomic_json(path,state)
    return True


def rearm_session(state,path,cli,generation):
    raise DriverError("session flags cannot be rearmed directly; use audited recovery")

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
    if (state.get("pending") is not None or state.get("position")!=0 or snap["position"]!=0 or
            snap["active"] or state.get("halt_new_entries") or state.get("owner_fence_recovery_required")):
        raise DriverError("canary requires its own authoritative flat/order-free unblocked state")
    guard=run([cli,"paper-on"],10)
    if guard.returncode:raise DriverError("local PAPER guard not ready")
    prepare_send(state,path,binary,socket,token,outbox,"OPEN",snap["quote"],"BUY")
    deadline=time.monotonic()+65
    while state.get("pending") is not None and time.monotonic()<deadline:
        inspect_pending(state,path,binary,socket,token,outbox,snapshot(cli,state,path,False))
        if state.get("pending") is not None:time.sleep(1)
    if state.get("pending") is not None:raise DriverError("canary pending preserved; no resubmission")
    if state.get("position",0)<=0:raise DriverError("canary entry did not produce a proved crypto fill")
    snap=snapshot(cli,state,path)
    prepare_send(state,path,binary,socket,token,outbox,"CLOSE")
    deadline=time.monotonic()+65
    while state.get("pending") is not None and time.monotonic()<deadline:
        inspect_pending(state,path,binary,socket,token,outbox,snapshot(cli,state,path,False))
        if state.get("pending") is not None:time.sleep(1)
    if state.get("pending") is not None or state.get("position")!=0:
        raise DriverError("canary exit incomplete; original state retained")
    return 0

def main():
    global INSTRUMENT,QTY,NATIVE_ENDPOINT,QUANTITY_TOLERANCE
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
    QTY=0.001 if crypto else 25000.0;QUANTITY_TOLERANCE=1e-10 if crypto else 1e-9
    home=Path.home(); state_dir=home/".local/state/heptatrader"/("crypto-driver" if crypto else "paper-driver"); private_dir(state_dir)
    outbox=state_dir/"outbox"; private_dir(outbox); state_path=state_dir/"state.json"
    lock=os.open(state_dir/"driver.lock",os.O_RDWR|os.O_CREAT|os.O_CLOEXEC,0o600); fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    cli=str(home/".local/bin/heptatrader"); binary=str(home/".local/share/heptatrader/current/bin/hepta-paper-command")
    sessionctl=str(home/".local/share/heptatrader/current/bin/hepta-sessionctl")
    codex=str(home/".local/share/heptatrader/tools/node_modules/.bin/codex")
    socket=f"/run/user/{os.geteuid()}/heptatrader/tools.sock"; supervisor=f"/run/user/{os.geteuid()}/heptatrader/supervisor.sock"
    token=str(home/".config/heptatrader"/("crypto.token" if crypto else "paper.token"))
    if crypto:
        NATIVE_ENDPOINT=[str(home/".local/share/heptatrader/current/bin/heptactl"),"--socket",socket,"--token-file",token]
    for x in (cli,binary,sessionctl,codex,token):
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
    atomic_json(state_path,state); model_worker=None
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
        snap=snapshot(cli,require_quote=False)
        if state.get("pending"):
            inspect_pending(state,state_path,binary,socket,token,str(outbox),snap)
        else:
            state["position"]=float(snap["position"]); atomic_json(state_path,state)
        os.close(lock); return 0
    if crypto and not crypto_roundtrip_qualified(state):
        raise DriverError("crypto resident driver requires a correlation-bound completed round trip")
    while not stop:
        started=time.monotonic()
        try:
            pending_mode=state.get("pending") is not None
            snap=snapshot(cli,state,state_path,require_quote=False); pos=float(snap["position"])
            if not pending_mode and state.get("active_lease_continuation_supported"):
                maybe_renew(state,state_path,sessionctl,supervisor,token,os.geteuid(),pos,len(snap["active"]),None)
            if not pending_mode:
                snap["quote"]=native(cli,"market.get_quote","instrument="+INSTRUMENT)
                q=snap["quote"]
                if q.get("authoritative") is not True or q.get("source")!="IB" or q.get("stale") is not False:
                    raise DriverError("authoritative instrument quote unavailable")
            if pending_mode:
                inspect_pending(state,state_path,binary,socket,token,str(outbox),snap)
                if args.once: break
                time.sleep(max(0.2,SAMPLE_SEC-(time.monotonic()-started)))
                continue
            q=snap["quote"]; mid=(float(q["bid"])+float(q["ask"]))/2
            if not state["history"] or state["history"][-1]["observed_at_ms"]!=q["observed_at_ms"]:
                state["history"].append({"observed_at_ms":q["observed_at_ms"],"bid":q["bid"],"ask":q["ask"],"mid":mid})
                state["history"]=state["history"][-MAX_HISTORY:]
            if state.get("owner_fence_recovery_required"):
                state["position"]=pos; state["halt_new_entries"]=True
                model_worker=None
                if not snap["active"] and abs(pos)>QUANTITY_TOLERANCE:
                    prepare_send(state,state_path,binary,socket,token,str(outbox),"CLOSE")
                elif not snap["active"] and snap["gross_position"]==0.0:
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
                model_worker=None
                if args.once: break
                time.sleep(max(0.2,SAMPLE_SEC-(time.monotonic()-started))); continue
            if abs(pos)<=QUANTITY_TOLERANCE and snap["owner_health"]["mutation_ready"] is not True:
                raise DriverError("authoritative mutation state is not ready")
            decision=None
            # Safety exits are evaluated on every sample and never wait for the model.
            if not snap["active"] and abs(pos)>QUANTITY_TOLERANCE:
                close,why=should_close(state,pos,q,None)
                if close:
                    state["last_exit_reason"]=why; atomic_json(state_path,state)
                    prepare_send(state,state_path,binary,socket,token,str(outbox),"CLOSE")
                    atomic_json(state_path,state)
                    if args.once: break
                    time.sleep(max(0.2,SAMPLE_SEC-(time.monotonic()-started))); continue
            if model_worker is not None and model_worker.done():
                try:
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
                model_worker=None
            if snap["active"]:
                state["last_error"]="active broker order present; observing only"
            elif abs(pos)>QUANTITY_TOLERANCE and decision is not None:
                close,why=should_close(state,pos,q,decision)
                if close:
                    state["last_exit_reason"]=why; atomic_json(state_path,state)
                    prepare_send(state,state_path,binary,socket,token,str(outbox),"CLOSE")
            elif abs(pos)<=QUANTITY_TOLERANCE and not state.get("halt_new_entries") and decision and decision["confidence"]>=CONF and decision["action"] in ({"BUY"} if INSTRUMENT=="BTC.USD" else {"BUY","SELL"}):
                guard=run([cli,"paper-on"],10)
                if guard.returncode!=0: raise DriverError("paper guard release failed")
                prepare_send(state,state_path,binary,socket,token,str(outbox),"OPEN",q,decision["action"])
            due=now_ms()-int(state.get("last_decision_attempt_ms",state.get("last_decision_ms",0)))>=int(DECISION_SEC*1000)
            if model_worker is None and due and len(state["history"])>=6:
                state["last_decision_attempt_ms"]=now_ms(); atomic_json(state_path,state)
                model_worker=ModelWorker(codex,state_dir,state["history"],pos)
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
