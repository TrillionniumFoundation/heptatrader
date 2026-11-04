"""Resident pre-open research; exact original identity, no order authority."""
from __future__ import annotations
import copy,fcntl,hashlib,json,os,re,stat,time,uuid
from pathlib import Path
from hepta_decision_attribution import VerifiedSegmentCache,read_private,strict
from .financial_forward_source import _ingress,ingress_identity
from .integrity import ContractError,canonical,digest

def wall_ms():return time.time_ns()//1000000
def require(ok,reason):
 if not ok:raise ContractError(reason)
def private_directory(path):
 p=Path(path);p.mkdir(mode=0o700,parents=True,exist_ok=True);s=p.lstat()
 require(stat.S_ISDIR(s.st_mode) and s.st_uid==os.geteuid() and not s.st_mode&0o077,"PREOPEN_PRIVATE_DIRECTORY")
 return p
def load(path):return strict(read_private(path))
def publish_once(path,value):
 """Publish complete create-only bytes, never a partial JSON target."""
 p=Path(path);private_directory(p.parent);raw=canonical(value)+b"\n";sha=hashlib.sha256(raw).hexdigest()
 if p.exists():
  require(read_private(p)==raw,"PREOPEN_IMMUTABLE_RECORD_CONFLICT");return sha
 tmp=p.with_name("."+p.name+"."+uuid.uuid4().hex)
 fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
 try:
  with os.fdopen(fd,"wb") as f:f.write(raw);f.flush();os.fsync(f.fileno())
  try:os.link(tmp,p,follow_symlinks=False)
  except FileExistsError:require(read_private(p)==raw,"PREOPEN_IMMUTABLE_RECORD_CONFLICT")
  fd=os.open(p.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_CLOEXEC)
  try:os.fsync(fd)
  finally:os.close(fd)
 finally:tmp.unlink(missing_ok=True)
 return sha

class ResidentIngress:
 """Source bytes/health are revalidated, only decoded prefixes are reused."""
 def __init__(self,registration,period_sha256):
  self.registration=copy.deepcopy(registration);self.period=period_sha256
  self.cache=VerifiedSegmentCache();self.identity=None;self.value=None;self.full_scans=0;self.reuses=0
 def read(self):
  before=ingress_identity(self.registration,self.period)
  if before==self.identity:self.reuses+=1;return copy.deepcopy(self.value)
  value=_ingress(self.registration,self.period,segment_cache=self.cache)
  require(before==ingress_identity(self.registration,self.period),"PREOPEN_SOURCE_CHANGED_DURING_CAPTURE")
  self.identity,self.value=before,value;self.full_scans+=1;return copy.deepcopy(value)
 def statistics(self):
  return {"source_captures":self.full_scans,"source_snapshot_reuses":self.reuses,
   "segment_bytes_read":self.cache.bytes_read,"new_records_decoded":self.cache.records_parsed,"segment_cache_reuses":self.cache.reuses}

class RequestLedger:
 """Durable original requests form the restart cursor; uncertain attempts never rerun."""
 def __init__(self,root,protocol,*,clock=wall_ms,monotonic=time.monotonic_ns):
  self.root=private_directory(root);self.protocol=copy.deepcopy(protocol);self.clock=clock;self.monotonic=monotonic
  require(all(k in protocol for k in ("plan_id","input_start_ms","input_end_ms","period_sha256","producer_registration")),"PREOPEN_PROTOCOL_FIELDS")
  require(re.fullmatch("[0-9a-f]{64}",protocol["plan_id"]) is not None,"PREOPEN_PLAN_ID")
  require(type(protocol["input_start_ms"])is int and type(protocol["input_end_ms"])is int and 0<protocol["input_start_ms"]<protocol["input_end_ms"],"PREOPEN_PROTOCOL_TIME")
  self.max_age_ms=protocol.get("max_request_age_ms",60000);self.max_requests=protocol.get("max_requests",10000)
  require(type(self.max_age_ms)is int and 1<=self.max_age_ms<=60000,"PREOPEN_NONRENEWABLE_DEADLINE")
  require(type(self.max_requests)is int and 1<=self.max_requests<=100000,"PREOPEN_REQUEST_BOUND")
  self.requests=private_directory(self.root/"requests");self.bindings=private_directory(self.root/"bindings");self.incidents=private_directory(self.root/"incidents")
  self.lock=os.open(self.root/"owner.lock",os.O_WRONLY|os.O_CREAT|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
  try:
   fcntl.flock(self.lock,fcntl.LOCK_EX|fcntl.LOCK_NB);publish_once(self.root/"protocol.json",protocol)
   self.known={p.name for p in self.requests.iterdir() if p.is_dir()};self.input_cache={};self.terminal_cache={}
   require(len(self.known)<=self.max_requests,"PREOPEN_REQUEST_BOUND")
  except BaseException:self.close();raise
 def close(self):
  if self.lock is not None:os.close(self.lock);self.lock=None
 def folder(self,rid):
  require(isinstance(rid,str) and re.fullmatch("[0-9a-f]{32}",rid) is not None,"PREOPEN_REQUEST_ID")
  return self.requests/rid
 def eligible(self,item):
  r=item["request"];return self.protocol["input_start_ms"]<=r["started_at_ms"]<self.protocol["input_end_ms"] and r["position_at_request"]==0
 def observe(self,item):
  r=item["request"]
  if not self.eligible(item):return None
  rid=r["request_id"];folder=self.folder(rid)
  if rid in self.input_cache:
   require(self.input_cache[rid]==item,"PREOPEN_REQUEST_CHANGED");return folder
  require(item["request_sha256"]==digest(r),"PREOPEN_REQUEST_HASH")
  require(item["producer_manifest_sha256"]==self.protocol["producer_registration"]["sha256"],"PREOPEN_PRODUCER_CHANGED")
  require(r["period_sha256"]==self.protocol["period_sha256"] and r["instrument"]=="EUR.USD","PREOPEN_REQUEST_SCOPE")
  require(hashlib.sha256(r["prompt"].encode()).hexdigest()==r["prompt_sha256"],"PREOPEN_PROMPT_HASH")
  require(rid in self.known or len(self.known)<self.max_requests,"PREOPEN_REQUEST_BOUND")
  publish_once(folder/"input.json",{"plan_id":self.protocol["plan_id"],"request_item":item});self.known.add(rid);self.input_cache[rid]=copy.deepcopy(item)
  if not (folder/"observed.json").exists():
   publish_once(folder/"observed.json",{"plan_id":self.protocol["plan_id"],"request_id":rid,"request_sha256":item["request_sha256"],"observed_at_ms":self.clock(),"broker_orders_sent":0})
  return folder
 def terminal(self,rid):
  if rid not in self.terminal_cache:
   p=self.folder(rid)/"terminal.json";self.terminal_cache[rid]=load(p) if p.exists() else None
  return self.terminal_cache[rid]
 def finish(self,rid,status,**details):
  prior=self.terminal(rid)
  if prior is not None:return prior
  item=load(self.folder(rid)/"input.json")["request_item"]
  value={"schema":"heptatrader.preopen-request-terminal.v1","plan_id":self.protocol["plan_id"],
   "request_id":rid,"request_sha256":item["request_sha256"],"producer_manifest_sha256":item["producer_manifest_sha256"],
   "status":status,"observed_at_ms":self.clock(),"broker_orders_sent":0,"old_trial_modified":False,**details}
  publish_once(self.folder(rid)/"terminal.json",value);self.terminal_cache[rid]=value;return value
 def recover(self):
  recovered=[]
  for rid in sorted(self.known):
   if (self.folder(rid)/"attempt.json").exists() and self.terminal(rid) is None:
    recovered.append(self.finish(rid,"INTERRUPTED_UNKNOWN_NO_REPLAY",reason="Attempt existed without final durable receipt"))
  return recovered
 def wait(self,rid,reason):
  p=self.folder(rid)/("wait-"+hashlib.sha256(reason.encode()).hexdigest()[:16]+".json")
  if not p.exists():publish_once(p,{"request_id":rid,"reason":reason,"observed_at_ms":self.clock(),"broker_orders_sent":0})
 def deadline(self,r):
  return min(r["started_at_ms"]+self.max_age_ms,self.protocol["input_end_ms"])
 def pending(self,requests):
  result=[]
  for item in requests.values():
   if self.observe(item) is None:continue
   r=item["request"];rid=r["request_id"]
   if self.terminal(rid) is not None:continue
   now=self.clock()
   if now<r["started_at_ms"]:self.finish(rid,"CLOCK_REVERSED_NO_PREDICTION")
   elif now>=self.deadline(r):self.finish(rid,"REQUEST_EXPIRED_NO_BACKDATING",deadline_ms=self.deadline(r))
   else:result.append(item)
  return sorted(result,key=lambda x:(self.deadline(x["request"]),x["request"]["request_id"]))
 def attempt(self,item,predict_and_seal):
  r=item["request"];rid=r["request_id"];folder=self.observe(item)
  if folder is None:return None
  if self.terminal(rid) is not None:return self.terminal(rid)
  if (folder/"attempt.json").exists():return self.finish(rid,"INTERRUPTED_UNKNOWN_NO_REPLAY")
  now,tick=self.clock(),self.monotonic();deadline=self.deadline(r)
  if now<r["started_at_ms"] or now>=deadline:return self.finish(rid,"REQUEST_EXPIRED_NO_BACKDATING",deadline_ms=deadline)
  publish_once(folder/"attempt.json",{"plan_id":self.protocol["plan_id"],"request_id":rid,"request_sha256":item["request_sha256"],
   "started_at_ms":now,"deadline_ms":deadline,"started_monotonic_ns":tick,"attempt_id":uuid.uuid4().hex,"broker_orders_sent":0})
  try:
   receipt=predict_and_seal(copy.deepcopy(item));end,end_tick=self.clock(),self.monotonic()
   require(now<=end and tick<=end_tick,"PREOPEN_CLOCK_REVERSED")
   require(end<deadline and end_tick-tick<(deadline-now)*1000000,"PREOPEN_EXPIRED_DURING_COMPUTATION_OR_SEAL")
   require(isinstance(receipt,dict) and receipt.get("plan_id")==self.protocol["plan_id"] and receipt.get("request_id")==rid
    and receipt.get("request_sha256")==item["request_sha256"],"PREOPEN_PUBLICATION_IDENTITY")
   published=receipt.get("seal_durable_completed_at_ms")
   require(type(published)is int and now<=published<=end,"PREOPEN_POST_FSYNC_TIME_REQUIRED")
   raw=read_private(Path(receipt["publication_path"]))
   require(hashlib.sha256(raw).hexdigest()==receipt["publication_sha256"],"PREOPEN_PUBLICATION_FILE_HASH")
   body=strict(raw)
   require(body.get("request_id")==rid and body.get("request_sha256")==item["request_sha256"] and body.get("plan_id")==self.protocol["plan_id"]
    and type(body.get("seal_durable_completed_at_ms"))is int and now<=body["seal_durable_completed_at_ms"]<=published,"PREOPEN_PUBLICATION_FILE_IDENTITY")
   observed_raw=read_private(receipt["observation_path"])
   require(hashlib.sha256(observed_raw).hexdigest()==receipt["observation_sha256"],"PREOPEN_OBSERVATION_HASH")
   observed=strict(observed_raw)
   require(observed.get("publication_sha256")==receipt["publication_sha256"] and observed.get("publication_durability_observed_at_ms")==published,"PREOPEN_OBSERVATION_IDENTITY")
   return self.finish(rid,"SEALED_AWAITING_ORIGINAL_COMMAND",publication=receipt,publication_durability_observed_at_ms=end,elapsed_ms=(end_tick-tick)/1e6,before_command_confirmed=False)
  except Exception as exc:
   return self.finish(rid,"PREDICTION_FAILED_OR_LATE",reason=type(exc).__name__+":"+str(exc)[:350],deadline_ms=deadline)
 def reconcile(self,binding):
  rid=binding["request_id"];folder=self.folder(rid)
  if not (folder/"input.json").exists():return None
  item=load(folder/"input.json")["request_item"]
  require(binding["request_sha256"]==item["request_sha256"] and binding["producer_manifest_sha256"]==item["producer_manifest_sha256"]
   and binding["request_started_at_ms"]==item["request"]["started_at_ms"],"PREOPEN_COMMAND_REQUEST_IDENTITY")
  cmd=binding["command"];cid=cmd["command_id"];created=cmd["created_at_ms"]
  require(isinstance(cid,str) and re.fullmatch("[A-Za-z0-9_.-]{1,128}",cid) is not None,"PREOPEN_COMMAND_ID")
  require(type(created)is int and item["request"]["started_at_ms"]<=created<=self.clock() and cmd.get("kind")=="OPEN"
   and cmd.get("instrument")=="EUR.USD" and cmd.get("side") in ("BUY","SELL"),"PREOPEN_ORIGINAL_COMMAND_SCOPE")
  request_binding=folder/"original-command.json"
  identity={"request_id":rid,"request_sha256":item["request_sha256"],"command_id":cid}
  if request_binding.exists():require(load(request_binding)==identity,"PREOPEN_MULTIPLE_ORIGINAL_COMMANDS")
  else:publish_once(request_binding,identity)
  p=self.bindings/(cid+".json")
  if p.exists():
   old=load(p);require(old["binding"]==binding,"PREOPEN_COMMAND_REBIND");return old
  terminal=self.terminal(rid)
  if terminal is None:terminal=self.finish(rid,"NO_PREDICTION_BEFORE_ORIGINAL_COMMAND")
  publication=terminal.get("publication",{})
  before=(terminal["status"]=="SEALED_AWAITING_ORIGINAL_COMMAND" and type(publication.get("seal_durable_completed_at_ms"))is int
   and type(terminal.get("publication_durability_observed_at_ms"))is int
   and publication["seal_durable_completed_at_ms"]<=terminal["publication_durability_observed_at_ms"]<created)
  value={"schema":"heptatrader.preopen-research-command-binding.v1","plan_id":self.protocol["plan_id"],"request_id":rid,"request_sha256":item["request_sha256"],
   "command_id":cid,"binding":binding,"prediction_terminal":terminal["status"],"publication":publication or None,"observed_at_ms":self.clock(),
   "status":"PREOPEN_BOUND_RESEARCH" if before else "MISSING_OR_LATE_NO_CREDIT","before_original_command":before,"value_coverage_credit":False,
   "production_eligible":False,"broker_orders_sent":0}
  publish_once(p,value);return value
 def restart_not_before(self):
  receipts=[p for p in self.root.glob("lease-*.json") if re.fullmatch(r"lease-[0-9a-f]{32}\.json",p.name)]
  require(len(receipts)<=20000,"PREOPEN_LEASE_RECEIPT_BOUND");deadlines=[]
  for path in receipts:
   reservation=load(path);lease_id=path.stem[6:];closed=self.root/("lease-close-"+lease_id+".json")
   if closed.exists():
    receipt=load(closed)
    require(receipt.get("lease_id")==lease_id and receipt.get("plan_id")==self.protocol["plan_id"]
     and receipt.get("reservation_sha256")==hashlib.sha256(read_private(path)).hexdigest(),"PREOPEN_LEASE_CLOSE_IDENTITY")
    require(type(receipt.get("released_at_ms"))is int and reservation["acquired_at_ms"]<=receipt["released_at_ms"],"PREOPEN_LEASE_CLOSE_TIME")
    deadlines.append(receipt["released_at_ms"]+int(reservation["cooldown_seconds"]*1000))
   else:deadlines.append(reservation["restart_not_before_ms"])
  return max(deadlines,default=0)
 def reserve_lease(self,max_seconds,cooldown_seconds):
  now=self.clock();lease_id=uuid.uuid4().hex
  value={"plan_id":self.protocol["plan_id"],"lease_id":lease_id,"acquired_at_ms":now,
   "maximum_resident_seconds":max_seconds,"restart_not_before_ms":now+int((max_seconds+cooldown_seconds)*1000),
   "cooldown_seconds":cooldown_seconds,"clock_is_not_backdated":True}
  publish_once(self.root/("lease-"+lease_id+".json"),value);return value
 def close_lease(self,reservation,cleanup_evidence=None):
  now=self.clock();require(now>=reservation["acquired_at_ms"],"PREOPEN_LEASE_CLOSE_CLOCK_REVERSED")
  path=self.root/("lease-"+reservation["lease_id"]+".json")
  require(load(path)==reservation,"PREOPEN_LEASE_RESERVATION_CHANGED")
  value={"plan_id":self.protocol["plan_id"],"lease_id":reservation["lease_id"],
   "reservation_sha256":hashlib.sha256(read_private(path)).hexdigest(),"released_at_ms":now,
   "tensor_cleanup_completed":True,"original_lease_release_completed":True,"model_cleanup":cleanup_evidence}
  publish_once(self.root/("lease-close-"+reservation["lease_id"]+".json"),value);return value
 def incident(self,reason):
  p=self.incidents/(hashlib.sha256(reason.encode()).hexdigest()+".json")
  if not p.exists():publish_once(p,{"reason":reason,"first_observed_at_ms":self.clock(),"plan_id":self.protocol["plan_id"],"broker_orders_sent":0})

def run_resident(source,ledger,warm,predict_and_seal,close_model,gate,*,max_seconds=120,poll_seconds=.25,
                 guard_check_seconds=5,cooldown_seconds=60,yield_requested=lambda:False,
                 stop=lambda:False,sleep=time.sleep,monotonic=time.monotonic):
 """One prewarmed model in the original lease, with bounded fair handoff.

 Collection precedes admission and continues during cooldown. A crash cannot
 trigger immediate reacquisition: a durable reservation sets the next start.
 """
 require(1<=max_seconds<=1800 and .05<=poll_seconds<=2,"PREOPEN_RESIDENT_BOUNDS")
 require(1<=guard_check_seconds<=5 and 60<=cooldown_seconds<=900,"PREOPEN_HANDOFF_BOUNDS")
 start=monotonic();admitted=ready=False;warm_count=attempts=source_errors=0
 next_guard=start;reason="BOUNDED_SESSION_COMPLETE";restart_not_before=ledger.restart_not_before()
 reservation=None;cleanup_started=False
 def cleanup():
  nonlocal cleanup_started,admitted
  if cleanup_started:return
  cleanup_started=True
  # A failure keeps the original lease until process exit, as the GPU owner does.
  cleanup_evidence=close_model()
  if admitted:
   gate.release();admitted=False
   if reservation is not None:ledger.close_lease(reservation,cleanup_evidence)
 ledger.recover()
 def capture():
  _,requests,bindings,issues=source.read();pending=ledger.pending(requests)
  if not issues:
   for binding in bindings:ledger.reconcile(binding)
  return pending,issues
 try:
  while not stop() and monotonic()-start<max_seconds:
   pending=[]
   try:
    pending,issues=capture()
    if ledger.clock()>=ledger.protocol["input_end_ms"]:reason="FIXED_INPUT_WINDOW_CLOSED";break
    if issues:
     why="SOURCE_PROVENANCE:"+json.dumps(issues,sort_keys=True)[:600];ledger.incident(why)
     for item in pending:ledger.wait(item["request"]["request_id"],why)
     if admitted:reason="SOURCE_CHANGED_HANDOFF";break
     sleep(poll_seconds);continue
    if admitted and yield_requested():reason="OTHER_REGISTERED_WORKER_WAITING";break
    if monotonic()>=next_guard:
     next_guard=monotonic()+guard_check_seconds
     if admitted:
      check=gate.check()
      if check.get("admit_new_worker")is not True:
       reason="GUARD_WITHDREW:"+",".join(check.get("reasons",[]))[:350]
       for item in pending:ledger.wait(item["request"]["request_id"],reason)
       break
     elif ledger.clock()>=restart_not_before:
      admitted=gate.acquire({"plan_id":ledger.protocol["plan_id"],"purpose":"FROZEN_PREOPEN_RESEARCH_RESIDENT"})
      if admitted:
       reservation=ledger.reserve_lease(max_seconds-(monotonic()-start),cooldown_seconds)
       warm();warm_count+=1;ready=True
       pending,after_warm_issues=capture()
       require(not after_warm_issues,"PREOPEN_SOURCE_CHANGED_DURING_WARMUP")
    if not ready:
     why="RESEARCH_LEASE_COOLDOWN" if ledger.clock()<restart_not_before else "SHARED_RESOURCE_OR_SOURCE"
     for item in pending:ledger.wait(item["request"]["request_id"],why)
    if ready:
     for item in pending:
      if stop() or monotonic()-start>=max_seconds:break
      if yield_requested():reason="OTHER_REGISTERED_WORKER_WAITING";break
      if ledger.terminal(item["request"]["request_id"]) is None:
       ledger.attempt(item,predict_and_seal);attempts+=1
       # A completed original command always disqualifies late computation.
       _,after_forward_issues=capture()
       require(not after_forward_issues,"PREOPEN_SOURCE_CHANGED_DURING_FORWARD")
     if reason=="OTHER_REGISTERED_WORKER_WAITING":break
    sleep(poll_seconds)
   except Exception as exc:
    source_errors+=1;ledger.incident(type(exc).__name__+":"+str(exc)[:600])
    if admitted:
     reason="SOURCE_OR_RESIDENT_FAILURE_HANDOFF"
     if not ready:raise
     break
    sleep(poll_seconds)
  if stop():reason="SUPERVISOR_STOP"
  # Record requests crossing the final poll/window boundary without inference.
  try:capture()
  except Exception as exc:
   source_errors+=1;ledger.incident("FINAL_CAPTURE:"+type(exc).__name__+":"+str(exc)[:600])
  cleanup()
  result={"schema":"heptatrader.preopen-resident-session.v1","plan_id":ledger.protocol["plan_id"],"ended_at_ms":ledger.clock(),
   "reason":reason,"model_loads":warm_count,"request_attempts":attempts,"source_error_observations":source_errors,
   "resident_seconds":monotonic()-start,"source":source.statistics(),"broker_orders_sent":0,"training_steps":0,
   "old_trial_modified":False,"restart_not_before_ms":ledger.restart_not_before()}
  publish_once(ledger.root/("session-"+uuid.uuid4().hex+".json"),result);return result
 finally:cleanup()
