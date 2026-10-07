"""Bounded read-only source for forward shadow; only market.get_quote is callable."""
from pathlib import Path
import hashlib,json,os,signal,subprocess,time,uuid
h=Path.home();root=h/'.local/state/heptatrader/neuron-learning/laya-shadow-real-v1';root.mkdir(mode=0o700,parents=True,exist_ok=True)
path=root/'quotes.jsonl'
if path.exists():raise SystemExit('SHADOW_SOURCE_EXISTS_NO_OVERWRITE')
cli=h/'.local/bin/heptatrader';period=h/'.config/heptatrader/pnl-period.json';period_sha=hashlib.sha256(period.read_bytes()).hexdigest()
fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600);seq=0;previous='0'*64;stop=False
canonical=lambda x:json.dumps(x,sort_keys=True,separators=(',',':'),allow_nan=False)
def append(kind,data):
 global seq,previous
 b={'schema':'heptatrader.laya-shadow-quotes.v1','sequence':seq,'previous':previous,'at_ms':int(time.time()*1000),'kind':kind,'data':data}
 checksum=hashlib.sha256(canonical(b).encode()).hexdigest();raw=(canonical({**b,'sha256':checksum})+'\n').encode();off=0
 while off<len(raw):off+=os.write(fd,raw[off:])
 os.fsync(fd);previous=checksum;seq+=1
append('HEADER',{'source':'IB_TOOL_GATEWAY','instrument':'EUR.USD','execution_domain':'PAPER','period_sha256':period_sha,'write_operations_allowed':[],'broker_orders_sent':0})
def finish(*args):
 global stop
 stop=True
signal.signal(signal.SIGTERM,finish);signal.signal(signal.SIGINT,finish)
deadline=time.monotonic()+2400
while not stop and time.monotonic()<deadline:
 begin=time.monotonic()
 if hashlib.sha256(period.read_bytes()).hexdigest()!=period_sha:
  append('FAILURE',{'reason':'ACCOUNTING_PERIOD_CHANGED'});break
 try:
  p=subprocess.run([str(cli),'native','--io-timeout-ms','4000','--call-id','laya-shadow-read-'+uuid.uuid4().hex,'call','market.get_quote','instrument=EUR.USD'],capture_output=True,text=True,timeout=8)
  obj=json.loads(p.stdout);q=obj.get('payload') or {};now=int(time.time()*1000)
  if p.returncode or obj.get('status')!='ok':raise ValueError(obj.get('reason_code','QUOTE_UNAVAILABLE'))
  t=q.get('observed_at_ms');expiry=q.get('stale_after_ms')
  if q.get('source')!='IB' or q.get('authoritative') is not True or q.get('stale') is not False or q.get('instrument')!='EUR.USD':raise ValueError('UNAUTHORITATIVE_QUOTE')
  if type(t)is not int or type(expiry)is not int or not t<=now<=min(t+5000,expiry):raise ValueError('SOURCE_QUOTE_STALE')
  if not 0<float(q['bid'])<=float(q['ask']):raise ValueError('BAD_QUOTE')
  append('QUOTE',q)
 except Exception as exc:
  reason=str(exc)
  if len(reason)>100 or any(c not in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ_0123456789' for c in reason):reason=type(exc).__name__
  append('FAILURE',{'reason':reason,'quote_timestamp_rewritten':False})
 time.sleep(max(.1,2-(time.monotonic()-begin)))
append('END',{'broker_orders_sent':0});os.close(fd)
print(json.dumps({'path':str(path),'events':seq,'chain_head':previous,'broker_orders_sent':0}))
