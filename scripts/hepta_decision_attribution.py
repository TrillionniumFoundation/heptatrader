"""Exact local decision provenance; observational only, never trading authority.

Records the literal infer CLI prompt/options, original local input snapshot,
returned envelope, decoded decision and original PREPARED command. The identity
link uses the actual returned Python object, not time proximity or equal text.
Background I/O and bounded queues cannot veto a risk exit or submit. Lost evidence
is explicitly unusable for model credit; it never changes a trading outcome.
"""
from __future__ import annotations
from collections import OrderedDict
import copy,hashlib,json,os,queue,stat,threading,time,uuid
from pathlib import Path

SCHEMA='heptatrader.exact-decision-attribution.v1'
MAX_RECORD=262144
MAX_STREAM=16*1024*1024
MAX_TOTAL=256*1024*1024

def canonical(v):return json.dumps(v,sort_keys=True,separators=(',',':'),allow_nan=False)
def digest(v):return hashlib.sha256(canonical(v).encode()).hexdigest()
def strict(raw):
 def pairs(items):
  out={}
  for k,v in items:
   if k in out:raise ValueError('DUPLICATE_JSON_KEY')
   out[k]=v
  return out
 return json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda _:(_ for _ in ()).throw(ValueError('NONFINITE_JSON')))
def read_private(path,limit=MAX_STREAM):
 fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
 try:
  st=os.fstat(fd)
  if not stat.S_ISREG(st.st_mode) or st.st_uid!=os.geteuid() or st.st_mode&0o077 or st.st_size>limit:
   raise ValueError('ATTRIBUTION_FILE_SCOPE')
  chunks=[];left=st.st_size
  while left:
   data=os.read(fd,min(left,1048576))
   if not data:raise ValueError('SHORT_READ')
   chunks.append(data);left-=len(data)
  return b''.join(chunks)
 finally:os.close(fd)

class Journal:
 def __init__(self,directory,manifest,manifest_sha256,capacity=128):
  self.root=Path(directory);self.root.mkdir(mode=0o700,parents=True,exist_ok=True)
  s=self.root.lstat()
  if not stat.S_ISDIR(s.st_mode) or s.st_uid!=os.geteuid() or s.st_mode&0o077:raise ValueError('PRIVATE_DIRECTORY_REQUIRED')
  self.manifest=manifest;self.manifest_sha=manifest_sha256;self.id=uuid.uuid4().hex
  self.q=queue.Queue(capacity);self.stop=threading.Event();self.dropped=0;self.error=None;self.count=0
  self.worker=threading.Thread(target=self._worker,name='decision-attribution-writer',daemon=True);self.worker.start()
 def emit(self,kind,data):
  try:
   if self.error or self.stop.is_set():raise ValueError('WRITER_UNAVAILABLE')
   value={'kind':kind,'captured_at_ms':int(time.time()*1000),'data':copy.deepcopy(data)}
   if len(canonical(value).encode())>MAX_RECORD-4096:raise ValueError('RECORD_TOO_LARGE')
   self.q.put_nowait(value);return True
  except Exception:self.dropped+=1;return False
 def _worker(self):
  fd=None;seq=0;prev='0'*64;segment=0;size=0
  try:
   def append(item):
    nonlocal seq,prev,size
    body={'schema':SCHEMA,'producer_id':self.id,'sequence':seq,'previous':prev,
          'dropped_total':self.dropped,'written_at_ms':int(time.time()*1000),**item}
    sha=digest(body);raw=(canonical({**body,'sha256':sha})+'\n').encode();offset=0
    while offset<len(raw):
     n=os.write(fd,raw[offset:])
     if n<=0:raise OSError('SHORT_WRITE')
     offset+=n
    os.fsync(fd);seq+=1;prev=sha;size+=len(raw);self.count+=1
   def open_segment():
    nonlocal fd,size
    if sum(p.stat().st_size for p in self.root.glob('*.jsonl'))>=MAX_TOTAL:raise ValueError('STORAGE_CAP_NO_PRUNING')
    if fd is not None:os.close(fd)
    path=self.root/(self.id+'-'+str(segment).zfill(4)+'.jsonl')
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600);size=0
    append({'kind':'HEADER','captured_at_ms':int(time.time()*1000),'data':{'manifest_sha256':self.manifest_sha,
            'instrument':self.manifest['instrument'],'period_sha256':self.manifest['period_sha256'],
            'execution_domain':'PAPER','source_kind':'PINNED_DRIVER_CALLSITE','trading_authority':False}})
   open_segment()
   while not self.stop.is_set() or not self.q.empty():
    try:item=self.q.get(timeout=.5)
    except queue.Empty:self._health();continue
    try:
     if size>=MAX_STREAM-MAX_RECORD:segment+=1;open_segment()
     append(item)
    finally:self.q.task_done()
   self._health()
  except Exception as exc:
   self.error=type(exc).__name__+':'+str(exc)[:80]
   try:self._health()
   except Exception:pass
  finally:
   if fd is not None:os.close(fd)
 def _health(self):
  p=self.root/(self.id+'.health.json');tmp=p.with_suffix('.next')
  body={'producer_id':self.id,'written_records':self.count,'dropped':self.dropped,'error':self.error,
        'at_ms':int(time.time()*1000),'order_operations':0}
  fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_TRUNC|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
  with os.fdopen(fd,'w') as f:f.write(canonical(body)+'\n')
  os.replace(tmp,p)
 def close(self):self.stop.set();self.worker.join(timeout=3)

class Capture:
 def __init__(self,journal,driver,manifest):
  self.journal=journal;self.driver=driver;self.manifest=manifest;self.local=threading.local();self.lock=threading.Lock();self.objects=OrderedDict()
 def emit(self,*args):
  try:return self.journal.emit(*args)
  except Exception:return False
 def install(self):
  d=self.driver;original={n:getattr(d,n) for n in ('run','model_decision','prepare_send','atomic_json')}
  def run(argv,*args,**kw):
   ctx=getattr(self.local,'inference',None)
   if ctx is None or list(argv[1:4])!=['infer','model','run']:return original['run'](argv,*args,**kw)
   ctx['calls']+=1
   try:
    # Only literal public inference controls. Never persist process environment,
    # authentication flags, headers, bearer files or arbitrary native arguments.
    if self.manifest.get('period_path') and hashlib.sha256(read_private(self.manifest['period_path'],65536)).hexdigest()!=self.manifest['period_sha256']:
     raise ValueError('PERIOD_CHANGED_NO_ATTRIBUTION')
    prompt=argv[argv.index('--prompt')+1];model=argv[argv.index('--model')+1]
    if not isinstance(prompt,str) or len(prompt)>131072:raise ValueError('PROMPT_BOUND')
    params={'model':model,'thinking':argv[argv.index('--thinking')+1] if '--thinking' in argv else None,
            'local':'--local' in argv,'json':'--json' in argv}
    request={'request_id':ctx['id'],'instrument':d.INSTRUMENT,'period_sha256':self.manifest['period_sha256'],
      'model_id':d.MODEL,'model_controls':params,'prompt':prompt,'prompt_sha256':hashlib.sha256(prompt.encode()).hexdigest(),
      'history_snapshot':ctx['history'],'position_at_request':ctx['position'],'started_at_ms':int(time.time()*1000),
      'input_scope':'EXACT_LOCAL_INFER_CLI_REQUEST_NOT_UNOBSERVED_PROVIDER_INTERNAL_CONTEXT'}
    ctx['request']=request;ctx['request_sha256']=digest(request);self.emit('REQUEST',request)
   except Exception:ctx['invalid']=True
   result=original['run'](argv,*args,**kw)
   try:
    response={'request_id':ctx['id'],'request_sha256':ctx.get('request_sha256'),'returncode':result.returncode,
             'stdout':result.stdout,'available_at_ms':int(time.time()*1000)}
    ctx['response']=response;ctx['response_sha256']=digest(response);self.emit('RESPONSE',response)
   except Exception:ctx['invalid']=True
   return result
  def model_decision(openclaw,state_dir,history,position):
   ctx={'id':uuid.uuid4().hex,'history':copy.deepcopy(history[-24:]),'position':float(position),'calls':0}
   self.local.inference=ctx
   try:decision=original['model_decision'](openclaw,state_dir,history,position)
   finally:self.local.inference=None
   try:
    if ctx.get('invalid') or ctx['calls']!=1 or ctx['response']['returncode']!=0:raise ValueError('NONEXACT_MODEL_CALL')
    info={'request_id':ctx['id'],'request_sha256':ctx['request_sha256'],'response_sha256':ctx['response_sha256'],
          'decision':copy.deepcopy(decision),'decision_sha256':digest(decision),'available_at_ms':int(time.time()*1000)}
    self.emit('DECISION',info)
    with self.lock:
     self.objects[id(decision)]=(decision,info)
     while len(self.objects)>32:self.objects.popitem(last=False)
   except Exception:self.emit('UNBOUND_DECISION',{'reason':'NO_SINGLE_EXACT_REQUEST','instrument':d.INSTRUMENT})
   return decision
  def prepare_send(state,path,*args,**kw):
   previous=getattr(self.local,'preparing',None);self.local.preparing=state
   try:return original['prepare_send'](state,path,*args,**kw)
   finally:self.local.preparing=previous
  def atomic_json(path,state):
   result=original['atomic_json'](path,state)
   try:
    pending=state.get('pending') or {}
    if (getattr(self.local,'preparing',None) is not state or pending.get('kind')!='OPEN'
        or pending.get('send_state')!='PREPARED'):return result
    if self.manifest.get('period_path') and hashlib.sha256(read_private(self.manifest['period_path'],65536)).hexdigest()!=self.manifest['period_sha256']:
     raise ValueError('PERIOD_CHANGED_AT_PREPARE')
    decision=state.get('last_decision')
    with self.lock:entry=self.objects.get(id(decision))
    if not entry or entry[0] is not decision or digest(decision)!=entry[1]['decision_sha256']:
     self.emit('UNBOUND_COMMAND',{'command_id':pending.get('command_id'),'instrument':d.INSTRUMENT,
                                'reason':'NO_IDENTICAL_RETURNED_DECISION_OBJECT'});return result
    command={k:copy.deepcopy(pending[k]) for k in ('command_id','kind','created_at_ms','instrument','side','position_before_send',
             'reference_price','quantity_mode','cash_quantity','order_type','tif') if k in pending}
    self.emit('COMMAND_BINDING',{'command':command,'request_id':entry[1]['request_id'],
      'request_sha256':entry[1]['request_sha256'],'response_sha256':entry[1]['response_sha256'],
      'decision_sha256':entry[1]['decision_sha256'],'decision_available_at_ms':entry[1]['available_at_ms'],
      'basis':'IDENTICAL_RETURNED_OBJECT_AT_ORIGINAL_DURABLE_PREPARED','state_write_succeeded':True})
   except Exception:self.emit('OBSERVER_ERROR',{'phase':'PREPARED','authority_effect':'NONE'})
   return result
  for n,f in {'run':run,'model_decision':model_decision,'prepare_send':prepare_send,'atomic_json':atomic_json}.items():setattr(d,n,f)
  def close():
   for n,f in original.items():setattr(d,n,f)
   self.journal.close()
  return close

def verified_bindings(registration,instrument,period_sha256):
 """Only a registered producer and a complete exact graph grant local attribution.

 Does NOT mint canonical-owner acceptance, settlement, fees or policy gradient.
 """
 path=Path(registration['manifest']);raw=read_private(path,65536)
 if hashlib.sha256(raw).hexdigest()!=registration['sha256']:raise ValueError('PRODUCER_MANIFEST_CHANGED')
 m=strict(raw)
 if (m.get('schema')!='heptatrader.attribution-producer.v1' or m.get('instrument')!=instrument
     or m.get('period_sha256')!=period_sha256 or m.get('execution_domain')!='PAPER'):
  raise ValueError('PRODUCER_SCOPE')
 files=sorted(Path(m['journal_directory']).glob('*.jsonl'))
 if len(files)>128:raise ValueError('PRODUCER_FILE_BOUND')
 groups={};issues=[];prefixes=[]
 for p in files:
  blob=read_private(p);blob=blob[:blob.rfind(b'\n')+1]
  prefixes.append({'path':str(p),'bytes':len(blob),'sha256':hashlib.sha256(blob).hexdigest()})
  for line in blob.splitlines():
   if len(line)>MAX_RECORD:raise ValueError('ATTRIBUTION_RECORD_BOUND')
   r=strict(line)
   if r.get('schema')!=SCHEMA or r.get('sha256')!=digest({k:v for k,v in r.items() if k!='sha256'}):raise ValueError('ATTRIBUTION_HASH')
   groups.setdefault(r['producer_id'],[]).append(r)
 bindings={};invalid_commands=set()
 for producer,records in groups.items():
  records.sort(key=lambda r:r['sequence']);previous='0'*64;requests={};responses={};decisions={};commands=[];bad=False;order={'REQUEST':{},'RESPONSE':{},'DECISION':{}}
  health=Path(m['journal_directory'])/(producer+'.health.json')
  if health.exists():
   h=strict(read_private(health,65536));bad=bool(h.get('dropped') or h.get('error'))
  for i,r in enumerate(records):
   if r['sequence']!=i or r['previous']!=previous:raise ValueError('ATTRIBUTION_CHAIN_GAP')
   previous=r['sha256'];bad|=bool(r.get('dropped_total'))
   if type(r.get('captured_at_ms'))is not int or r['captured_at_ms']>int(time.time()*1000):raise ValueError('ATTRIBUTION_CLOCK')
   v=r['data'];kind=r['kind']
   if kind=='HEADER':
    if (v.get('manifest_sha256')!=registration['sha256'] or v.get('instrument')!=instrument
        or v.get('period_sha256')!=period_sha256 or v.get('execution_domain')!='PAPER'
        or v.get('source_kind')!='PINNED_DRIVER_CALLSITE' or v.get('trading_authority') is not False):
     raise ValueError('ATTRIBUTION_HEADER_SCOPE')
   elif i==0:raise ValueError('MISSING_ATTRIBUTION_HEADER')
   elif kind in ('REQUEST','RESPONSE','DECISION'):
    target={'REQUEST':requests,'RESPONSE':responses,'DECISION':decisions}[kind];key=v['request_id']
    if key in target and target[key]!=v:raise ValueError('CONFLICTING_REQUEST_RECORD')
    target[key]=v;order[kind].setdefault(key,i)
   elif kind=='COMMAND_BINDING':commands.append((v,i))
  if bad:
   issues.append({'producer_id':producer,'reason':'DROPPED_OR_FAILED_PRODUCER_NO_CREDIT'});continue
  for b,binding_sequence in commands:
   cmd=b.get('command',{});rid=b.get('request_id');request=requests.get(rid);response=responses.get(rid);decision=decisions.get(rid)
   try:
    if not all((request,response,decision)):raise ValueError('INCOMPLETE_INPUT_RESPONSE_GRAPH')
    if not order['REQUEST'][rid]<order['RESPONSE'][rid]<order['DECISION'][rid]<binding_sequence:
     raise ValueError('ATTRIBUTION_CAUSAL_RECORD_ORDER')
    if (digest(request)!=b['request_sha256'] or digest(response)!=b['response_sha256']
        or decision['request_sha256']!=digest(request) or decision['response_sha256']!=digest(response)
        or response['request_sha256']!=digest(request) or digest(decision['decision'])!=b['decision_sha256']
        or decision['decision_sha256']!=b['decision_sha256']):raise ValueError('REQUEST_DECISION_LINK_CONFLICT')
    if (request['instrument']!=instrument or request['period_sha256']!=period_sha256
        or cmd.get('instrument')!=instrument or cmd.get('kind')!='OPEN' or cmd.get('side') not in ('BUY','SELL')
        or decision['decision'].get('action')!=cmd['side'] or b.get('state_write_succeeded') is not True
        or b.get('basis')!='IDENTICAL_RETURNED_OBJECT_AT_ORIGINAL_DURABLE_PREPARED'):raise ValueError('COMMAND_INPUT_SCOPE')
    if hashlib.sha256(request['prompt'].encode()).hexdigest()!=request['prompt_sha256']:raise ValueError('PROMPT_HASH')
    if not 0<request['started_at_ms']<=response['available_at_ms']<=decision['available_at_ms']<=cmd['created_at_ms']:
     raise ValueError('REQUEST_AFTER_COMMAND')
    envelope=strict(response['stdout']);outputs=envelope.get('outputs')
    if response['returncode']!=0 or envelope.get('ok') is not True or not isinstance(outputs,list) or len(outputs)!=1:
     raise ValueError('MODEL_ENVELOPE')
    decoded=strict(outputs[0]['text'])
    if not isinstance(decoded,dict) or any(decision['decision'].get(k)!=v for k,v in decoded.items()):raise ValueError('MODEL_DECISION_NOT_FROM_RESPONSE')
    cid=cmd['command_id'];value={'input_id':digest(request),'forecast_id':b['decision_sha256'],'model_id':request['model_id'],
     'instrument':instrument,'side':cmd['side'],'binding_basis':b['basis'],'request_id':rid,
     'prompt_sha256':request['prompt_sha256'],'producer_manifest_sha256':registration['sha256'],
     'source_prefixes':prefixes,'attribution_scope':request['input_scope']}
    if cid in bindings and bindings[cid]!=value:raise ValueError('COMMAND_MULTIPLE_INPUTS')
    bindings[cid]=value
   except (ValueError,KeyError,TypeError) as exc:
    cid=cmd.get('command_id');invalid_commands.add(cid)
    issues.append({'command_id':cid,'reason':str(exc)})
 for cid in invalid_commands:bindings.pop(cid,None)
 return bindings,issues
