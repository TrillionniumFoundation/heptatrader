#!/usr/bin/env python3
from __future__ import annotations
import argparse,fcntl,json,math,os,signal,stat,subprocess,tempfile,threading,time,uuid
from pathlib import Path

INSTRUMENT="EUR.USD"; QTY=25000.0; CONF=0.62; MAX_ADVERSE=0.002
SAMPLE_SEC=10.0; DECISION_SEC=120.0; MAX_HISTORY=60; MODEL="gpt-6-luna"
AGENT_ID="pocket4-paper"; SESSION_ID="pocket4-user-paper-20260928"
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
        return {"schema":"heptatrader.paper-driver.v1","history":[],"pending":None,"position":0.0,
            "entry_price":None,"entry_price_basis":None,"last_decision_ms":0,"decisions":0,
            "entries":0,"exits":0,"closed_trades":0,"gross_pnl":0.0,"last_error":"",
            "halt_new_entries":False,"lease_generation":1,"lease_last_renew_ms":now_ms(),
            "lease_uncertain":False,"lease_renewal_drain":False,"lease_renewal_owned_halt":False}
    st=path.lstat()
    if not stat.S_ISREG(st.st_mode) or st.st_uid!=os.geteuid() or st.st_nlink!=1 or stat.S_IMODE(st.st_mode)!=0o600:
        raise DriverError("unsafe state file")
    v=strict_json(path.read_text())
    if v.get("schema")!="heptatrader.paper-driver.v1": raise DriverError("state schema mismatch")
    return v

def native(cli,tool,*fields):
    p=run([cli,"native","--io-timeout-ms","4000","--call-id",cid("driver-read"),"call",tool,*fields],15)
    if not p.stdout: raise DriverError("native transport failed: "+p.stderr[:160])
    v=strict_json(p.stdout)
    if p.returncode!=0 or v.get("status")!="ok": raise DriverError("native rejected: "+str(v.get("reason_code")))
    return v.get("payload") or {}

def snapshot(cli):
    h=native(cli,"system.get_health"); o=native(cli,"orders.list")
    p=native(cli,"portfolio.list_positions"); q=native(cli,"market.get_quote","instrument=EUR.USD")
    l=native(cli,"risk.get_limits")
    if (h.get("execution_mode")!="PAPER" or not h.get("gateway_ready") or not h.get("remote_execution_ready")
        or o.get("source")!="IB" or not o.get("authoritative") or not o.get("global_active_orders_complete")
        or not o.get("owner_projection_complete") or p.get("source")!="IB" or not p.get("authoritative")
        or q.get("source")!="IB" or not q.get("authoritative") or q.get("stale") is not False
        or l.get("source")!="IB" or not l.get("authoritative")):
        raise DriverError("authoritative PAPER snapshot incomplete")
    pos=sum(float(x.get("quantity",0)) for x in p.get("positions",[]) if x.get("instrument")==INSTRUMENT)
    return {"orders":o,"quote":q,"position":pos,"active":[int(x) for x in o.get("active_order_ids",[])]}

def recent_fill(orders,oid):
    for row in orders.get("recent_orders",[]):
        if row.get("order_id")==oid and row.get("economic_fill") is True:
            price=float(row.get("average_fill_price",0)); qty=float(row.get("filled_quantity",0))
            if price>0 and qty>0 and math.isfinite(price) and math.isfinite(qty):
                return {"price":price,"quantity":qty,"terminal":row.get("terminal") is True,
                    "side":row.get("side")}
    return None

def terminal_nonfill(orders,oid):
    return any(row.get("order_id")==oid and row.get("terminal") is True and
        row.get("economic_fill") is not True for row in orders.get("recent_orders",[]))

def paper_cmd(binary,socket,token,outbox,op,extra):
    p=run([binary,op,"--socket",socket,"--token-file",token,"--directory",outbox,*extra],20)
    if not p.stdout: return {"ok":False,"reason":"PROCESS_NO_RESPONSE"}
    v=strict_json(p.stdout); v["process_returncode"]=p.returncode; return v

def prepare_send(state,path,binary,socket,token,outbox,kind,quote=None,side=None):
    preview=cid("driver-preview")
    if kind=="OPEN":
        ref=float(quote["ask"] if side=="BUY" else quote["bid"])
        prepared=paper_cmd(binary,socket,token,outbox,"prepare-open",
            ["--call-id",preview,"--side",side,"--price",format(ref,".17g"),"--expiry",str(now_ms()+45000)])
    else:
        ref=None; prepared=paper_cmd(binary,socket,token,outbox,"prepare-close",["--call-id",preview])
    if not prepared.get("ok") or not prepared.get("command_id"):
        raise DriverError("prepare failed: "+str(prepared.get("reason")))
    pending={"kind":kind,"command_id":prepared["command_id"],"send_state":"PREPARED",
        "created_at_ms":now_ms(),"broker_order_id":None,"side":side,"reference_price":ref}
    state["pending"]=pending; atomic_json(path,state)
    pending["send_state"]="POSSIBLY_SENT"; atomic_json(path,state)
    sent=paper_cmd(binary,socket,token,outbox,"submit",["--command-id",pending["command_id"]])
    pending["last_submit_transport_ok"]=bool(sent.get("ok")); pending["last_submit_reason"]=str(sent.get("reason",""))
    result=sent.get("result") if isinstance(sent.get("result"),dict) else {}
    if isinstance(result.get("order_id"),int) and result["order_id"]>=0: pending["broker_order_id"]=result["order_id"]
    atomic_json(path,state)

def inspect_pending(state,path,binary,socket,token,outbox,snap):
    p=state.get("pending")
    inspected=paper_cmd(binary,socket,token,outbox,"inspect",
        ["--command-id",p["command_id"],"--call-id",cid("driver-status")])
    payload={}
    if inspected.get("ok") and isinstance(inspected.get("result"),dict):
        payload=inspected["result"].get("payload") or {}
        if isinstance(payload.get("order_id"),int) and payload["order_id"]>=0: p["broker_order_id"]=payload["order_id"]
    oid=p.get("broker_order_id"); pos=float(snap["position"]); active=snap["active"]; fill=recent_fill(snap["orders"],oid)
    fill_price=fill.get("price") if fill else None
    p["last_status"]={"at_ms":now_ms(),"command_status":payload.get("command_status"),"reason_code":payload.get("reason_code","")}
    expected_sign=1.0 if p.get("side")=="BUY" else -1.0 if p.get("side")=="SELL" else 0.0
    open_position_matches=(p["kind"]=="OPEN" and not active and abs(pos)>1e-9 and
        abs(pos)<=QTY+1e-9 and expected_sign*pos>0)
    if open_position_matches:
        state["position"]=pos; state["entries"]+=1
        state["entry_price"]=fill_price
        state["entry_price_basis"]="broker_average_fill_price" if fill else "unavailable"
        state["entry_at_ms"]=now_ms(); state["pending"]=None
        if abs(pos)<QTY-1e-9 or not fill:
            state["halt_new_entries"]=True
            state["last_error"]="partial or price-unproven entry adopted; reduce-only exit required"
        else:
            state["last_error"]=""
    elif p["kind"]=="CLOSE" and abs(pos)<1e-9 and not active:
        entry=state.get("entry_price"); old=float(state.get("position",0))
        if fill_price and entry and old:
            closed_qty=min(abs(old),float(fill.get("quantity",QTY)))
            state["gross_pnl"]+=((fill_price-entry)*closed_qty if old>0 else (entry-fill_price)*closed_qty)
        state.update(position=0.0,entry_price=None,entry_price_basis=None,entry_at_ms=None,pending=None,last_error="")
        state["exits"]+=1; state["closed_trades"]+=1
    elif (p["kind"]=="CLOSE" and not active and fill and fill.get("terminal") and
          abs(pos)>1e-9 and abs(pos)<abs(float(state.get("position",0)))-1e-9):
        state["position"]=pos; state["pending"]=None; state["halt_new_entries"]=True
        state["last_error"]="partial flatten terminal; remaining exposure requires next exact reduce-only flatten"
    elif (payload.get("command_status")=="rejected" or terminal_nonfill(snap["orders"],oid)) and not active:
        state["pending"]=None; state["last_error"]="terminal non-fill: "+str(payload.get("reason_code",""))
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
    prompt=("Direction-only decision for an IB PAPER EUR/USD test. You cannot trade or use tools. "
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


def _provision_command(sessionctl, supervisor, token, uid):
    return run([sessionctl,"--socket",supervisor,"--io-timeout-ms","4000","provision",
        "--template","paper","--token-file",token,"--agent-id",AGENT_ID,
        "--session-id",SESSION_ID,"--peer-uid",str(uid),"--ttl-sec","86400"],10)


def _finish_renewal(state, generation):
    lease_halted_entries = (state.get("lease_renewal_owned_halt") or
        state.get("lease_uncertain") or
        state.get("lease_renewal_state")=="POSSIBLY_SENT")
    state["lease_generation"]=int(generation)
    state["lease_last_renew_ms"]=now_ms()
    state["lease_renewal_state"]="IDLE"
    state["lease_uncertain"]=False
    state["lease_renewal_drain"]=False
    if lease_halted_entries:
        state["halt_new_entries"]=False
    state["lease_renewal_owned_halt"]=False
    state["last_error"]=""


def maybe_renew(state,path,sessionctl,supervisor,token,uid,position,active_orders,pending):
    due=(state.get("lease_renewal_state")=="POSSIBLY_SENT" or
         now_ms()-int(state.get("lease_last_renew_ms",0))>=4*60*60*1000)
    if not due:
        return
    if abs(float(position))>1e-9 or active_orders or pending is not None:
        state["lease_renewal_drain"]=True
        if not state.get("halt_new_entries"):
            state["halt_new_entries"]=True
            state["lease_renewal_owned_halt"]=True
        state["last_error"]="session renewal due; drain to exact-zero before renewal"
        atomic_json(path,state)
        return
    g=int(state.get("lease_generation",1))
    state["lease_renewal_state"]="POSSIBLY_SENT"; atomic_json(path,state)
    result=_renew_command(sessionctl,supervisor,token,uid,g)
    value=_parse_command_json(result)
    if result.returncode==0 and value.get("accepted") is True:
        new_generation=int(value.get("lease_generation",0))
        if new_generation>0:
            _finish_renewal(state,new_generation); atomic_json(path,state); return
    if value.get("reason_code")=="SESSION_LEASE_GENERATION_MISMATCH":
        reported=int(value.get("lease_generation",0) or 0)
        if reported>0 and reported!=g:
            retry=_renew_command(sessionctl,supervisor,token,uid,reported)
            retry_value=_parse_command_json(retry)
            if retry.returncode==0 and retry_value.get("accepted") is True:
                new_generation=int(retry_value.get("lease_generation",0))
                if new_generation>0:
                    _finish_renewal(state,new_generation); atomic_json(path,state); return
        elif reported==0:
            provision=_provision_command(sessionctl,supervisor,token,uid)
            provision_value=_parse_command_json(provision)
            if provision.returncode==0 and provision_value.get("accepted") is True:
                new_generation=int(provision_value.get("lease_generation",0))
                if new_generation>0:
                    _finish_renewal(state,new_generation); atomic_json(path,state); return
    if value.get("reason_code") in {"RECOVERY_INGRESS_FENCE_REQUIRED", "SESSION_NOT_FOUND", "LEASE_STORE_TOKEN_NOT_FOUND"}:
        provision=_provision_command(sessionctl,supervisor,token,uid)
        provision_value=_parse_command_json(provision)
        if provision.returncode==0 and provision_value.get("accepted") is True:
            new_generation=int(provision_value.get("lease_generation",0))
            if new_generation>0:
                _finish_renewal(state,new_generation); atomic_json(path,state); return
    if value:
        state["halt_new_entries"]=True
        state["lease_renewal_owned_halt"]=False
        state["last_error"]=("session renewal rejected: "+str(value.get("reason_code")))[:500]
    else:
        state["lease_uncertain"]=True; state["halt_new_entries"]=True
        state["lease_renewal_owned_halt"]=False
        state["last_error"]=("session renewal uncertain: "+result.stderr[-160:])[:500]
    atomic_json(path,state)

def rearm_session(state,path,cli,generation):
    if not state.get("lease_uncertain"):
        raise DriverError("session rearm requires persisted lease uncertainty")
    if state.get("pending") is not None:
        raise DriverError("session rearm requires pending mutation reconciliation")
    snap=snapshot(cli)
    if snap["active"] or abs(float(snap["position"]))>1e-9:
        raise DriverError("session rearm requires authoritative flat/order-free Broker state")
    if generation<1:
        raise DriverError("session rearm generation invalid")
    state["position"]=0.0
    state["lease_generation"]=generation
    state["lease_last_renew_ms"]=now_ms()
    state["lease_renewal_state"]="IDLE"
    state["lease_uncertain"]=False
    state["lease_renewal_drain"]=False
    state["lease_renewal_owned_halt"]=False
    state["halt_new_entries"]=False
    state["last_error"]=""
    state["session_rearmed_at_ms"]=now_ms()
    atomic_json(path,state)

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

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--once",action="store_true")
    ap.add_argument("--reconcile-only",action="store_true")
    ap.add_argument("--rearm-session-generation",type=int,default=0)
    args=ap.parse_args()
    if sum(bool(x) for x in (args.once,args.reconcile_only,args.rearm_session_generation))>1:
        raise DriverError("choose only one one-shot operator mode")
    if os.geteuid()==0: raise DriverError("driver must be non-root")
    home=Path.home(); state_dir=home/".local/state/heptatrader/paper-driver"; private_dir(state_dir)
    outbox=state_dir/"outbox"; private_dir(outbox); state_path=state_dir/"state.json"
    lock=os.open(state_dir/"driver.lock",os.O_RDWR|os.O_CREAT|os.O_CLOEXEC,0o600); fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    cli=str(home/".local/bin/heptatrader"); binary=str(home/".local/share/heptatrader/current/bin/hepta-paper-command")
    sessionctl=str(home/".local/share/heptatrader/current/bin/hepta-sessionctl")
    codex=str(home/".local/share/heptatrader/tools/node_modules/.bin/codex")
    socket=f"/run/user/{os.geteuid()}/heptatrader/tools.sock"; supervisor=f"/run/user/{os.geteuid()}/heptatrader/supervisor.sock"
    token=str(home/".config/heptatrader/paper.token")
    for x in (cli,binary,sessionctl,codex,token):
        if not Path(x).exists(): raise DriverError("required runtime missing: "+x)
    stop=False
    def stopping(_sig,_frame):
        nonlocal stop; stop=True
    signal.signal(signal.SIGTERM,stopping); signal.signal(signal.SIGINT,stopping)
    state=load_state(state_path)
    if state.get("lease_renewal_state")=="POSSIBLY_SENT":
        state["halt_new_entries"]=True
        state["last_error"]="session renewal reconciliation pending after restart"
    atomic_json(state_path,state); model_worker=None
    if args.rearm_session_generation:
        rearm_session(state,state_path,cli,args.rearm_session_generation)
        os.close(lock); return 0
    if args.reconcile_only:
        snap=snapshot(cli)
        if state.get("pending"):
            inspect_pending(state,state_path,binary,socket,token,str(outbox),snap)
        else:
            state["position"]=float(snap["position"]); atomic_json(state_path,state)
        os.close(lock); return 0
    while not stop:
        started=time.monotonic()
        try:
            if ((state.get("lease_uncertain") or state.get("lease_renewal_state")=="POSSIBLY_SENT") and
                    state.get("pending") is None and abs(float(state.get("position",0.0)))<1e-9):
                maybe_renew(state,state_path,sessionctl,supervisor,token,os.geteuid(),0.0,0,None)
            snap=snapshot(cli); pos=float(snap["position"]); q=snap["quote"]; mid=(float(q["bid"])+float(q["ask"]))/2
            if not state["history"] or state["history"][-1]["observed_at_ms"]!=q["observed_at_ms"]:
                state["history"].append({"observed_at_ms":q["observed_at_ms"],"bid":q["bid"],"ask":q["ask"],"mid":mid})
                state["history"]=state["history"][-MAX_HISTORY:]
            if state.get("pending"):
                inspect_pending(state,state_path,binary,socket,token,str(outbox),snap)
            else:
                state["position"]=pos; maybe_renew(state,state_path,sessionctl,supervisor,token,os.geteuid(),
                    pos,len(snap["active"]),state.get("pending"))
                decision=None
                # Safety exits are evaluated on every sample and never wait for the model.
                if not snap["active"] and abs(pos)>1e-9:
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
                        if abs(pos-model_worker.position)>1e-9:
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
                elif abs(pos)>1e-9 and decision is not None:
                    close,why=should_close(state,pos,q,decision)
                    if close:
                        state["last_exit_reason"]=why; atomic_json(state_path,state)
                        prepare_send(state,state_path,binary,socket,token,str(outbox),"CLOSE")
                elif abs(pos)<=1e-9 and not state.get("halt_new_entries") and decision and decision["confidence"]>=CONF and decision["action"] in {"BUY","SELL"}:
                    guard=run([cli,"paper-on"],10)
                    if guard.returncode!=0: raise DriverError("paper guard release failed")
                    prepare_send(state,state_path,binary,socket,token,str(outbox),"OPEN",q,decision["action"])
                due=now_ms()-int(state.get("last_decision_attempt_ms",state.get("last_decision_ms",0)))>=int(DECISION_SEC*1000)
                if model_worker is None and due and len(state["history"])>=6:
                    state["last_decision_attempt_ms"]=now_ms(); atomic_json(state_path,state)
                    model_worker=ModelWorker(codex,state_dir,state["history"],pos)
                atomic_json(state_path,state)
        except Exception as e:
            state["last_error"]=str(e)[:500]; atomic_json(state_path,state)
        if args.once: break
        time.sleep(max(0.2,SAMPLE_SEC-(time.monotonic()-started)))
    os.close(lock); return 0
if __name__=="__main__": raise SystemExit(main())
