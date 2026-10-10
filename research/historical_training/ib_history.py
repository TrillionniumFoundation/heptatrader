"""Bounded historical market-data download. No order or account mutations.

Existing PAPER Gateway only. Independent client. Resumable immutable chunks.
An API end callback is transport completion, not a claim of full coverage.
"""
from __future__ import annotations
import argparse,collections,fcntl,json,os,sys,threading,time
from contextlib import contextmanager
from datetime import datetime,timedelta,timezone
from pathlib import Path
from .common import EvidenceError,read,sha,digest,atomic,write_once
from .egress import HistoryGuard

ALLOWED=frozenset((71,20,25,87,90))
CONTRACT={'conId':12087792,'symbol':'EUR','secType':'CASH','exchange':'IDEALPRO','currency':'USD'}

def validate(c):
    if (c.get('host')!='127.0.0.1' or c.get('port')!=4002 or c.get('client_id')!=8984
        or c.get('trade_authority') is not False or c.get('contract')!=CONTRACT):
        raise EvidenceError('READ_ONLY_PAPER_HISTORY_SCOPE')
    if not 1<=c.get('chunks',0)<=7 or not 12<=c.get('spacing_sec',0)<=60:
        raise EvidenceError('BOUNDED_REQUEST_PACING_REQUIRED')
    if set(c.get('sdk_sha256',{}))!={'client.py','wrapper.py','message.py','contract.py'}:
        raise EvidenceError('PINNED_SDK_REQUIRED')
    for name,wanted in c['sdk_sha256'].items():
        if sha(Path(c['sdk_root'])/'ibapi'/name)!=wanted:raise EvidenceError('SDK_CHANGED')
    end=datetime.strptime(c['end_utc'],'%Y%m%d %H:%M:%S UTC').replace(tzinfo=timezone.utc)
    if end>=datetime.now(timezone.utc)-timedelta(days=1):raise EvidenceError('HISTORICAL_ONLY_CUTOFF')
    return end

def make_app(c):
    sys.path.insert(0,c['sdk_root'])
    from ibapi.client import EClient
    from ibapi.wrapper import EWrapper
    from ibapi.contract import Contract
    class Reader(EWrapper,EClient):
        def __init__(self):
            EWrapper.__init__(self);EClient.__init__(self,self)
            self.ready=threading.Event();self.account_event=threading.Event();self.paper=False
            self.events={};self.rows={};self.ends={};self.errors=[];self.heads={};self.schedules={}
            self.sent=collections.Counter();self.own=set();self.active=set();self.dead=False
            self.guard=HistoryGuard(c['client_id'],c['spacing_sec'])
        def sendMsg(self,msg):
            fields=msg.split('\0');code=int(fields[0])
            if code not in ALLOWED:raise EvidenceError('NON_HISTORY_MESSAGE_REFUSED:'+str(code))
            if code!=71 and not self.paper:raise EvidenceError('PAPER_NOT_VERIFIED')
            if sum(self.sent.values())>=80:raise EvidenceError('OUTBOUND_MESSAGE_CAP')
            self.guard.check(msg,self.paper,self.serverVersion())
            self.sent[code]+=1
            return EClient.sendMsg(self,msg)
        def nextValidId(self,orderId):self.ready.set()
        def managedAccounts(self,accountsList):
            accounts=[x.strip() for x in accountsList.split(',') if x.strip()]
            self.paper=bool(accounts) and all(x.startswith('DU') for x in accounts)
            self.account_event.set()
        def error(self,reqId,errorCode,errorString,advancedOrderRejectJson=''):
            self.errors.append({'request_id':reqId,'code':errorCode,'message':errorString})
            if reqId in self.events:self.events[reqId].set()
            if errorCode in (1100,1300,326,502,504):self.dead=True
        def historicalData(self,reqId,bar):
            self.guard.check_response(reqId,'bars')
            if reqId not in self.active:raise EvidenceError('UNREGISTERED_RESPONSE')
            if len(self.rows[reqId])>=20000:raise EvidenceError('BAR_CAPACITY')
            self.rows[reqId].append({'time':str(bar.date),'open':str(bar.open),'high':str(bar.high),
                'low':str(bar.low),'close':str(bar.close),'volume':str(bar.volume),
                'wap':str(bar.wap),'bar_count':bar.barCount})
        def historicalDataEnd(self,reqId,start,end):
            self.guard.check_response(reqId,'bars')
            self.ends[reqId]={'start':start,'end':end};self.events[reqId].set()
        def headTimestamp(self,reqId,headTimestamp):
            self.guard.check_response(reqId,'head')
            self.heads[reqId]=headTimestamp;self.events[reqId].set()
        def historicalSchedule(self,reqId,startDateTime,endDateTime,timeZone,sessions):
            self.guard.check_response(reqId,'schedule')
            self.schedules[reqId]={'start':startDateTime,'end':endDateTime,'timezone':timeZone,
                'sessions':[{'start':x.startDateTime,'end':x.endDateTime,'reference_date':x.refDate} for x in sessions]}
            self.events[reqId].set()
        def cancel_own(self,reqId,head=False):
            if reqId not in self.own:raise EvidenceError('FOREIGN_CANCELLATION_REFUSED')
            if head:self.cancelHeadTimeStamp(reqId)
            else:self.cancelHistoricalData(reqId)
    contract=Contract()
    for k,v in CONTRACT.items():setattr(contract,k,v)
    return Reader(),contract

def plans(c,end):
    out=[{'kind':'head','what':what,'end':''} for what in ('MIDPOINT','BID','ASK')]
    for i in range(c['chunks']):
        until=end-timedelta(days=30*i)
        for what in ('MIDPOINT','BID','ASK','SCHEDULE'):
            out.append({'kind':'bars' if what!='SCHEDULE' else 'schedule','what':what,
                'end':until.strftime('%Y%m%d %H:%M:%S UTC'),'duration':'30 D',
                'bar_size':'1 day' if what=='SCHEDULE' else '5 mins',
                'requested_start_utc':(until-timedelta(days=30)).isoformat(),
                'requested_end_utc':until.isoformat()})
    return out

def completion_state(signalled, errors, complete):
    """Fail closed on every request-scoped IB error, including HMDS error 162."""
    if any('pacing' in str(e.get('message', '')).lower() for e in errors):
        return 'ERROR', 'PACING_STOP'
    if errors:
        return 'ERROR', 'REQUEST_ERROR_NO_AUTOMATIC_RETRY'
    if not signalled or not complete:
        return 'TIMEOUT', 'REQUEST_TIMEOUT_NO_AUTOMATIC_RETRY'
    return 'RECEIVED', None


def verified_receipt(directory, plan):
    if directory.is_symlink():
        raise EvidenceError('RESUME_CHUNK_CHANGED')
    receipt_path = directory / 'receipt.json'
    if not receipt_path.exists():
        # The request is durably sealed before network dispatch. After a
        # crash, any unfinalized chunk may have reached the broker already.
        if directory.exists() and (not directory.is_dir() or any(directory.iterdir())):
            raise EvidenceError('UNFINALIZED_CHUNK_UNKNOWN_OUTCOME_USE_NEW_OUTPUT_ROOT')
        return None
    receipt = read(receipt_path)
    if (receipt.get('request') != plan or
            not (directory / 'request.json').exists() or
            read(directory / 'request.json') != plan or
            not (directory / 'response.json').exists() or
            sha(directory / 'response.json') != receipt.get('response_sha256')):
        raise EvidenceError('RESUME_CHUNK_CHANGED')
    if receipt.get('state') != 'RECEIVED':
        raise EvidenceError('FAILED_CHUNK_IMMUTABLE_USE_NEW_OUTPUT_ROOT')
    response = read(directory / 'response.json')
    if (not isinstance(response, dict) or response.get('errors') != []
        or not isinstance(response.get('bars'), list)
        or receipt.get('rows') != len(response['bars'])):
        raise EvidenceError('RECEIVED_CHUNK_PAYLOAD_INVALID')
    kind = plan['kind']
    if ((kind == 'head' and not response.get('head'))
        or (kind == 'bars' and not response.get('end_callback'))
        or (kind == 'schedule' and not response.get('schedule'))
        or kind not in ('head', 'bars', 'schedule')):
        raise EvidenceError('RECEIVED_CHUNK_COMPLETION_UNPROVEN')
    return receipt


def preflight_existing(root, config, end):
    """Reject frozen failures and tampering before any new network request."""
    for plan in plans(config, end):
        key = digest({'contract': CONTRACT, **plan})
        verified_receipt(root / 'chunks' / key, plan)


@contextmanager
def global_history_lock(root):
    """Reserve pinned client 8984 across distinct output roots on Pocket4."""
    root.parent.mkdir(parents=True,mode=0o700,exist_ok=True)
    fd=os.open(root.parent/'ib-history-client-8984.lock',
               os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def sync_dir(path):
    fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:os.fsync(fd)
    finally:os.close(fd)


def run(c):
    end=validate(c);root=Path(c['output_root'])
    with global_history_lock(root):
        return _run_locked(c,end,root)


def _run_locked(c,end,root):
    root.mkdir(parents=True,mode=0o700,exist_ok=True)
    lock=open(root/'download.lock','a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    write_once(root/'plan.json',{'schema':'heptatrader.historical-download-plan.v1','config':c,'requests':plans(c,end)})
    preflight_existing(root,c,end)
    app,contract=make_app(c);thread=None;results=[];sent_now=0;last=0
    session={'schema':'heptatrader.historical-download-session.v1','started_ms':int(time.time()*1000),
        'config_sha256':digest(c),'trade_authority':False,'results':results}
    try:
        app.connect(c['host'],c['port'],c['client_id'])
        thread=threading.Thread(target=app.run,daemon=True);thread.start()
        if not app.ready.wait(12) or not app.account_event.wait(3) or not app.paper:
            raise EvidenceError('PAPER_HANDSHAKE_FAILED')
        session['server_version']=app.serverVersion()
        for index,plan in enumerate(plans(c,end)):
            key=digest({'contract':CONTRACT,**plan});directory=root/'chunks'/key
            r=verified_receipt(directory,plan)
            if r is not None:
                results.append({'request_key':key,'state':'RECEIVED','resumed':True,'rows':r.get('rows',0)})
                continue
            if app.dead:raise EvidenceError('BROKER_DISCONNECTED_NO_RECONNECT_LOOP')
            elapsed=time.monotonic()-(app.guard.last if app.guard.last is not None else last)
            if elapsed<c['spacing_sec']:time.sleep(c['spacing_sec']-elapsed)
            rid=7800+index;app.own.add(rid);app.active.add(rid)
            app.guard.register(rid,plan)
            app.events[rid]=threading.Event();app.rows[rid]=[]
            directory.mkdir(mode=0o700,parents=True,exist_ok=True)
            write_once(directory/'request.json',plan)
            # Prevent a power loss from erasing evidence of a dispatched request.
            for path in (directory,directory.parent,root,root.parent):
                sync_dir(path)
            last=time.monotonic();sent_now+=1
            if plan['kind']=='head':app.reqHeadTimeStamp(rid,contract,plan['what'],0,2)
            else:app.reqHistoricalData(rid,contract,plan['end'],plan['duration'],plan['bar_size'],plan['what'],0,2,False,[])
            signalled=app.events[rid].wait(45)
            errors=[x for x in app.errors if x['request_id']==rid]
            complete=rid in app.heads or rid in app.ends or rid in app.schedules
            if plan['kind']=='head':app.cancel_own(rid,head=True)
            elif not complete:app.cancel_own(rid)
            response={'bars':app.rows[rid],'head':app.heads.get(rid),'schedule':app.schedules.get(rid),
                'end_callback':app.ends.get(rid),'errors':errors}
            write_once(directory/'response.json',response)
            state,stop_reason=completion_state(signalled,errors,complete)
            receipt={'schema':'heptatrader.historical-chunk.v1','request':plan,'request_id':rid,
                'received_at_ms':int(time.time()*1000),'state':state,'rows':len(app.rows[rid]),
                'response_sha256':sha(directory/'response.json'),'full_range_coverage_claimed':False,
                'error_codes':[x['code'] for x in errors]}
            # Terminal receipt is required for safe replay; a missing one is UNKNOWN_OUTCOME.
            write_once(directory / 'receipt.json', receipt)
            results.append({'request_key':key,'state':state,'resumed':False,'rows':receipt['rows']})
            atomic(root/'progress.json',session)
            print(json.dumps({'kind':plan['what'],'end':plan['end'],'state':state,'rows':receipt['rows']}),flush=True)
            if stop_reason:
                raise EvidenceError(stop_reason)
    except Exception as exc:
        session['exception']=type(exc).__name__+': '+str(exc)
    finally:
        app.disconnect()
        if thread:thread.join(timeout=2)
        session.update(ended_ms=int(time.time()*1000),paper_verified=app.paper,
            requests_sent_this_run=sent_now,outbound_message_counts=dict(app.sent),
            allowed_outbound_codes=sorted(ALLOWED),broker_orders_sent=0,errors=app.errors)
        atomic(root/'session.json',session)
        write_once(root/'sessions'/(str(session['started_ms'])+'.json'),session)
        lock.close()
    return session

def main():
    p=argparse.ArgumentParser();p.add_argument('--config',type=Path,required=True);a=p.parse_args()
    os.umask(0o077);r=run(read(a.config));print(json.dumps({k:v for k,v in r.items() if k not in ('results','errors')}))
    if r.get('exception') or not r.get('results') or any(x['state']!='RECEIVED' for x in r['results']):raise SystemExit(1)
if __name__=='__main__':main()
