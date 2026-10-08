"""Isolated, bounded EUR.USD PAPER IBKR top-of-book/tick-by-tick comparison.

This probe has no order, account, venue, risk, cancel-order or model interface.
Outbound bytes are explicitly checked by SendBoundary before reaching IB API.
Neither native quote snapshot nor old experimental decisions are modified.
"""
from __future__ import annotations
import argparse,hashlib,json,os,queue,signal,stat,sys,threading,time
from collections import Counter
from pathlib import Path
from .wire import SendBoundary,RejectWire,valid_pair

SCHEMA='heptatrader.ib-direct-bidask-probe.v1'
MAX_FILE_BYTES=64*1024*1024
MAX_SECONDS=2400

def now_ms():return int(time.time()*1000)
def canonical(v):return json.dumps(v,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def digest(v):return hashlib.sha256(canonical(v)).hexdigest()

class EvidenceLog:
 def __init__(self,path):
  self.path=Path(path);self.path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
  if self.path.parent.is_symlink() or self.path.parent.stat().st_mode & 0o077:
   raise ValueError('PRIVATE_SOURCE_ROOT_REQUIRED')
  self.fd=os.open(self.path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_CLOEXEC|os.O_NOFOLLOW,0o600)
  self.seq=0;self.previous='0'*64;self.size=0;self.count=Counter()
 def add(self,kind,data):
  body={'schema':SCHEMA,'seq':self.seq,'prev':self.previous,'kind':kind,'at_ms':now_ms(),'payload':data}
  record={**body,'sha256':digest(body)}
  raw=canonical(record)+b'\n'
  if len(raw)>65536 or self.size+len(raw)>MAX_FILE_BYTES:raise ValueError('SOURCE_STORAGE_BOUND')
  self.write(raw);self.size+=len(raw);self.previous=record['sha256'];self.seq+=1;self.count[kind]+=1
 def write(self,raw):
  while raw:
   n=os.write(self.fd,raw);raw=raw[n:]
  os.fsync(self.fd)
 def close(self):
  if self.fd is not None:os.close(self.fd);self.fd=None

def load_config(path):
 c=json.loads(Path(path).read_bytes());period=Path(c['period_path'])
 if hashlib.sha256(period.read_bytes()).hexdigest()!=c['period_sha256']:raise ValueError('ACCOUNTING_PERIOD_CHANGED')
 p=json.loads(period.read_bytes())
 if p.get('execution_domain')!='PAPER':raise ValueError('NOT_PAPER')
 if c.get('scope')!='EUR.USD_CASH_IDEALPRO_READ_ONLY' or c.get('port')!=4002 or c.get('host')!='127.0.0.1':
  raise ValueError('HOST_OR_CONTRACT_SCOPE')
 cid=c['client_id']
 if type(cid)is not int or not 8000<=cid<=8999 or cid==8952 or cid in {
  x.get('broker_client_id') for x in p.get('instruments',{}).values()}:
  raise ValueError('TRADING_OR_ACCOUNT_CLIENT_ID_COLLISION')
 if c.get('subscription_mode','TOP_AND_TBT') not in ('TOP_AND_TBT','TOP_ONLY'):raise ValueError('SUBSCRIPTION_MODE')
 if type(c.get('seconds'))is not int or not 15<=c['seconds']<=MAX_SECONDS:raise ValueError('DURATION_BUDGET')
 sdk=Path(c['sdk_root']).resolve(strict=True)
 if str(sdk)!='/home/alex/.local/share/heptatrader/vendor/ibapi-reset-check':
  raise ValueError('UNPINNED_OFFICIAL_IB_SDK')
 for name,expected in c['sdk_sha256'].items():
  pth=sdk/'ibapi'/name
  if name not in ('client.py','wrapper.py','message.py','contract.py') or hashlib.sha256(pth.read_bytes()).hexdigest()!=expected:
   raise ValueError('IBAPI_SDK_INTEGRITY_CHANGED')
 if set(c['sdk_sha256'])!={'client.py','wrapper.py','message.py','contract.py'}:raise ValueError('INCOMPLETE_SDK_PINS')
 if c.get('effect_authority') is not False or c.get('allow_order_methods') is not False:raise ValueError('TRADING_AUTHORITY_FORBIDDEN')
 return c

def sdk_load(c):
 root=Path(c['sdk_root']).resolve(strict=True);sys.path.insert(0,str(root))
 from ibapi.client import EClient
 from ibapi.wrapper import EWrapper
 from ibapi.contract import Contract
 return EClient,EWrapper,Contract

def make_reader(c,boundary,events):
 EClient,EWrapper,Contract=sdk_load(c)
 class Reader(EWrapper,EClient):
  def __init__(self):
   EClient.__init__(self,self)
   self.ready=threading.Event();self.cash_ready=threading.Event()
   self.lost=False;self.market_type=1;self.closed_by_operator=False
   self.last_market_type=0
  def sendMsg(self,msg):
   boundary.check(msg) # only evidence-side request IDs and outbound codes
   return EClient.sendMsg(self,msg)
  def placeOrder(self,*a,**k):raise RejectWire('ALL_IB_ORDERS_FORBIDDEN')
  def cancelOrder(self,*a,**k):raise RejectWire('ORDER_CANCEL_FORBIDDEN')
  def reqGlobalCancel(self,*a,**k):raise RejectWire('GLOBAL_CANCEL_FORBIDDEN')
  def reqAccountUpdates(self,*a,**k):raise RejectWire('ACCOUNT_SUBSCRIPTION_FORBIDDEN')
  def reqAccountUpdatesMulti(self,*a,**k):raise RejectWire('ACCOUNT_SUBSCRIPTION_FORBIDDEN')
  def reqPositions(self,*a,**k):raise RejectWire('POSITION_SUBSCRIPTION_FORBIDDEN')
  def reqPositionsMulti(self,*a,**k):raise RejectWire('POSITION_SUBSCRIPTION_FORBIDDEN')
  def emit(self,kind,**kwargs):
   item={'kind':kind,'received_ms':now_ms(),**kwargs}
   try:events.put_nowait(item)
   except queue.Full:self.lost=True
  def nextValidId(self,_):self.ready.set();self.emit('NEXT_VALID_ID_IGNORED')
  def managedAccounts(self,_):self.emit('MANAGED_ACCOUNTS_UNREAD')
  def connectionClosed(self):
   if not self.closed_by_operator:self.lost=True;self.emit('CONNECTION_CLOSED')
  def error(self,request_id,code,message,*args):
   code=int(code);lower=str(message).lower();cash='cash' in lower
   if code in (1100,1101,2103) and (code!=2103 or cash):
    self.cash_ready.clear()
   if code==1101:self.emit('MARKET_DATA_LOST_RENEW_REQUIRED',code=code)
   if code==1102:self.emit('MARKET_DATA_MAINTAINED',code=code)
   if code==2104 and cash:self.cash_ready.set()
   if code in (101,102,100,10089,10090,10197):self.lost=True
   self.emit('CONTROL',code=code,request_id=request_id,cash_farm=cash,
    fatal=code in (101,102,100,10089,10090,10197))
  def marketDataType(self,reqId,marketDataType):
   self.last_market_type=int(marketDataType)
   if self.last_market_type!=1:self.lost=True
   self.emit('MARKET_TYPE',request_id=reqId,market_type=int(marketDataType))
  def tickPrice(self,reqId,tickType,price,attrib):
   if reqId!=boundary.data_ids[0] or int(tickType) not in (1,2):return
   self.emit('TOP_BID' if int(tickType)==1 else 'TOP_ASK',price=float(price),request_id=reqId)
  def tickByTickBidAsk(self,reqId,timestamp,bidPrice,askPrice,bidSize,askSize,attrib):
   if reqId!=boundary.data_ids[1]:return
   self.emit('TBT_BIDASK',broker_second=int(timestamp),bid=float(bidPrice),ask=float(askPrice),request_id=reqId)
 return Reader(),Contract

def contract_for_fx(Contract):
 c=Contract();c.symbol='EUR';c.secType='CASH';c.currency='USD';c.exchange='IDEALPRO'
 return c

def safe_atomic(path,value):
 p=Path(path);tmp=p.with_name(p.name+'.next')
 with tmp.open('xb') as f:f.write(canonical(value)+b'\n');f.flush();os.fsync(f.fileno())
 tmp.chmod(0o600);os.replace(tmp,p)

def run(config_path,out_root):
 os.umask(0o077);c=load_config(config_path);root=Path(out_root)
 if root.exists():raise ValueError('NONEMPTY_OR_EXISTING_PROBE_ROOT')
 root.mkdir(parents=True,mode=0o700,exist_ok=False)
 events=queue.Queue(maxsize=32000);boundary=SendBoundary(c['client_id'])
 client,Contract=make_reader(c,boundary,events)
 journal=EvidenceLog(root/'events.jsonl')
 running={'value':True};count=Counter()
 def stop(*_):running['value']=False
 signal.signal(signal.SIGINT,stop);signal.signal(signal.SIGTERM,stop)
 cfg_sha=hashlib.sha256(Path(config_path).read_bytes()).hexdigest()
 journal.add('HEADER',{'scope':c['scope'],'config_sha256':cfg_sha,
  'period_sha256':c['period_sha256'],'client_id':c['client_id'],'live_market_data_type_only':True,
  'broker_host_is_loopback_only':True,'streaming_snapshot':False,
  'tick_by_tick_type':'BidAsk' if c.get('subscription_mode')!='TOP_ONLY' else 'DISABLED','trading_methods_available':False})
 client_thread=None;start=time.monotonic();started_at=now_ms()
 last_bid=last_ask=None;last_bid_at=last_ask_at=0
 seen_tbt=set();last_summary=0;healthy=0;drop=0;sent_at=0;life='INITIALIZING';renewal_required=False
 try:
  client.connect(c['host'],c['port'],c['client_id'])
  client_thread=threading.Thread(target=client.run,daemon=True);client_thread.start()
  life='WAITING_NEXT_VALID_AND_CASH_FARM'
  deadline=time.monotonic()+15
  while running['value'] and time.monotonic()<deadline and not(client.ready.is_set() and client.cash_ready.is_set()):
   if client.lost or not client.isConnected():break
   time.sleep(.1)
  if not client.ready.is_set() or not client.cash_ready.is_set() or client.lost:
   raise ValueError('BROKER_READONLY_HANDSHAKE_OR_CASH_FARM_NOT_READY')
  client.reqMarketDataType(1)
  fx=contract_for_fx(Contract)
  client.reqMktData(boundary.data_ids[0],fx,'',False,False,[])
  if c.get('subscription_mode')!='TOP_ONLY':client.reqTickByTickData(boundary.data_ids[1],fx,'BidAsk',0,False)
  life='SUBSCRIBED';sent_at=now_ms()
  journal.add('SUBSCRIBED',{'ids':list(boundary.data_ids),'type':'LIVE_BIDASK_TOP_PLUS_TBT','at_ms':sent_at})
  while running['value'] and time.monotonic()-start<c['seconds']:
   try:item=events.get(timeout=.25)
   except queue.Empty:item=None
   if item:
    kind=item['kind'];count[kind]+=1
    if kind=='MARKET_DATA_LOST_RENEW_REQUIRED':renewal_required=True;life='WAITING_RESUBSCRIPTION_FARM'
    if kind=='CONTROL' and item['code']==2103 and item['cash_farm']:
     renewal_required=True;life='WAITING_RESUBSCRIPTION_FARM'
    if kind=='CONTROL' and item['fatal']:client.lost=True
    if kind=='TBT_BIDASK':
     ts=item['broker_second']*1000;age=item['received_ms']-ts
     item['broker_age_ms']=age
     item['valid_pair']=valid_pair(item['bid'],item['ask']) and -2000<=age<=5000 and client.last_market_type in (0,1)
     item['source_clock']='BROKER_SECOND_AND_LOCAL_CALLBACK_RECEIPT'
     # A callback with the same broker second and quote is not a new
     # independent update, even when received repeatedly.
     key=(item['broker_second'],item['bid'],item['ask'])
     item['independent_update']=item['valid_pair'] and key not in seen_tbt
     seen_tbt.add(key)
     if len(seen_tbt)>25000:raise ValueError('TBT_DEDUP_BUDGET')
    if kind in ('TOP_BID','TOP_ASK'):
     if kind=='TOP_BID':last_bid=item['price'];last_bid_at=item['received_ms']
     else:last_ask=item['price'];last_ask_at=item['received_ms']
     item['bid_and_ask_recent']=valid_pair(last_bid,last_ask) and 0<=item['received_ms']-min(last_bid_at,last_ask_at)<=5000
    journal.add(kind,{k:v for k,v in item.items() if k!='kind'})
   if client.lost or not client.isConnected():life='DISCONNECTED_NO_FALLBACK';break
   if renewal_required and client.cash_ready.is_set() and boundary.active:
    # Only this client's own market-data requests are cancelled. New request
    # identities on renewal prevent late old callbacks becoming fresh ticks.
    client.cancelMktData(boundary.data_ids[0])
    if boundary.data_ids[1] in boundary.active:client.cancelTickByTickData(boundary.data_ids[1])
    boundary.advance_cycle()
    client.reqMktData(boundary.data_ids[0],fx,'',False,False,[])
    if c.get('subscription_mode')!='TOP_ONLY':client.reqTickByTickData(boundary.data_ids[1],fx,'BidAsk',0,False)
    renewal_required=False;life='SUBSCRIBED_AFTER_FARM_RECOVERY'
    journal.add('RESUBSCRIBED_AFTER_CONTROL_2104',{'ids':list(boundary.data_ids)})
   if now_ms()-last_summary>=5000:
    last_summary=now_ms()
    safe_atomic(root/'status.json',{'schema':SCHEMA,'at_ms':last_summary,'state':life,
      'events':sum(count.values()),'counts':dict(count),'last_callback_ms':item['received_ms'] if item else None,
      'duration_sec':c['seconds'],'seconds_remaining':max(0,int(c['seconds']-(time.monotonic()-start))),
      'broker_orders_sent':0,'account_requests_sent':0,'trading_authority':False,
      'wire':boundary.signed_receipt()})
  if time.monotonic()-start>=c['seconds']:life='DURATION_ENDED'
 finally:
  try:
   if client.isConnected():
    for rid in list(boundary.active):
     if rid%2:client.cancelMktData(rid)
     else:client.cancelTickByTickData(rid)
  except Exception:life='CANCELLATION_FAILED_NO_TRADE_EFFECT'
  client.closed_by_operator=True
  client.disconnect()
  if client_thread:client_thread.join(timeout=3)
  # Drain post-disconnect callback receipt without presenting it as a quote.
  journal.add('END',{'state':life,'event_counts':dict(count),
    'wire_counts':boundary.signed_receipt(),'censored_on_disconnect':life!='DURATION_ENDED',
    'broker_orders_sent':0,'account_requests_sent':0})
  journal.close()
  status={'schema':SCHEMA,'at_ms':now_ms(),'state':life,'start_ms':started_at,
    'stop_ms':now_ms(),'counts':dict(count),'source_file_sha256':hashlib.sha256((root/'events.jsonl').read_bytes()).hexdigest(),
    'chain_head':journal.previous,'wire':boundary.signed_receipt(),
    'trading_authority':False,'broker_orders_sent':0,'account_requests_sent':0}
  safe_atomic(root/'status.json',status)
 print(json.dumps(status,indent=2),flush=True)
 return status

def main():
 p=argparse.ArgumentParser();p.add_argument('--config',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
 a=p.parse_args();run(a.config,a.output)
if __name__=='__main__':main()
