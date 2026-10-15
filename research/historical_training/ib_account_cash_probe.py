"""PAPER-only one-subscription IB account cash callback diagnostic.

Independent IB client ID, explicit finite subscribe/end/cancel, no orders,
no risk or account authority changes. Only salted hashes and absolute
differences are retained; raw financial balances are never written.
"""
from __future__ import annotations
import argparse,collections,hashlib,json,os,threading,time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from .common import EvidenceError,read,sha,write_once
from .ib_history import global_history_lock

PROBE_CLIENT=8985
REQ_ID=98501
MSG_CODES={71,76,77}
NONFATAL={2104,2106,2107,2108,2158}

def nonfatal_account_transport_notice(req_id,code,message):
    # Historical-market-data farms can be unavailable while an independent
    # IB account-updates subscription still operates. Do not turn a -1
    # cashhmds 2105 into an account-stream terminal error. Scoped request
    # errors, unknown service failures and actual account errors remain fatal.
    if req_id==-1 and code in NONFATAL:return True
    return (req_id==-1 and code==2105 and isinstance(message,str)
            and 'hmds' in message.casefold())


class CashState:
    def __init__(self,account):
        if not isinstance(account,str) or not account.startswith('DU') or len(account)>30:
            raise EvidenceError('PAPER_ACCOUNT_ONLY')
        self.account=account
        self._salt=os.urandom(24)  # not serialized; hashes cannot be dictionary-guessed from artifacts
        self.initial={}
        self.head={}
        self.after=[]
        self.end_ms=None
        self.discrepancies=[]
    def private_hash(self,value):
        return hashlib.sha256(self._salt+str(value).encode()).hexdigest()
    @staticmethod
    def parse(value):
        try: v=Decimal(value)
        except (InvalidOperation,TypeError,ValueError):raise EvidenceError('INVALID_CASH_VALUE')
        if not v.is_finite():raise EvidenceError('NONFINITE_CASH_VALUE')
        return v
    def ingest(self,account,key,currency,value,at_ms):
        if account!=self.account:raise EvidenceError('CROSS_ACCOUNT_CALLBACK')
        if key not in ('CashBalance','AccountReady'):return
        if key=='AccountReady':
            if value.lower()=='false':self.discrepancies.append('ACCOUNT_NOT_READY')
            return
        if currency!='EUR':return
        v=self.parse(value)
        if self.end_ms is None:
            prev=self.initial.get(currency)
            if prev is not None and prev!=v:self.discrepancies.append('INITIAL_DUPLICATE_VALUE_CONFLICT')
            self.initial[currency]=v
        else:
            diff=v-self.head[currency] if currency in self.head else None
            self.after.append({'elapsed_after_end_ms':at_ms-self.end_ms,
              'identical_to_initial':diff==0 if diff is not None else False,
              'absolute_difference':str(abs(diff)) if diff is not None else None,
              'balance_value_sha256':self.private_hash(v)})
    def end(self,at_ms):
        if self.end_ms is not None:raise EvidenceError('MULTIPLE_INITIAL_END')
        self.end_ms=at_ms;self.head=dict(self.initial)
        if 'EUR' not in self.head:raise EvidenceError('NO_EUR_CASH_IN_INITIAL_END')
    def snapshot(self):
        return {'end_observed':self.end_ms is not None,
         'initial_currency_types':sorted(self.head),
         'initial_hashed_eur_balance':self.private_hash(self.head.get('EUR','MISSING')),
         'post_end_updates':self.after[-100:],
         'post_end_distinct_value_hashes':len(set(x['balance_value_sha256'] for x in self.after)),
         'post_end_changed_count':sum(not x['identical_to_initial'] for x in self.after),
         'last_post_end_delta_abs':self.after[-1]['absolute_difference'] if self.after else None,
         'discrepancies':list(dict.fromkeys(self.discrepancies)),
         'raw_balances_redacted':True}

def run(config,output,wait_after_end_sec=25):
    c=read(config)
    if (c.get('host')!='127.0.0.1' or c.get('port')!=4002
        or c.get('trade_authority') is not False):
        raise EvidenceError('ACCOUNT_PROBE_SCOPE')
    if not 5<=wait_after_end_sec<=35:raise EvidenceError('TIME_WINDOW_BOUND')
    for k,expected in c['sdk_sha256'].items():
        if sha(Path(c['sdk_root'])/'ibapi'/k)!=expected:raise EvidenceError('IB_SDK_HASH_CHANGED')
    import sys
    sys.path.insert(0,c['sdk_root'])
    from ibapi.client import EClient
    from ibapi.wrapper import EWrapper
    root=Path(output);root.mkdir(parents=True,mode=0o700,exist_ok=False)
    lock=root/'observer.lock'
    class Probe(EWrapper,EClient):
        def __init__(self):
            EWrapper.__init__(self);EClient.__init__(self,self)
            self.connected=threading.Event();self.managed=threading.Event()
            self.finished=threading.Event();self.guard=threading.RLock()
            self.account=None;self.state=None;self.sent=collections.Counter()
            self.errors=[];self.terminal_error=False;self.accepted=False
        def sendMsg(self,msg):
            parts=msg.split('\0')
            code=int(parts[0]);req=int(parts[2]) if code in (76,77) else 0
            with self.guard:
                if code not in MSG_CODES or sum(self.sent.values())>=3:
                    raise EvidenceError('NON_CASH_SUBSCRIPTION_MESSAGE_REFUSED')
                if code in (76,77) and (not self.accepted or req!=REQ_ID):
                    raise EvidenceError('PROBE_ACCOUNT_REQUEST_SCOPE')
                if code==71 and self.sent[71]:raise EvidenceError('DUPLICATE_START_API')
                if code==76 and (self.sent[76] or not self.sent[71]):raise EvidenceError('DUPLICATE_SUBSCRIPTION')
                if code==77 and (self.sent[77] or self.sent[76]!=1):raise EvidenceError('CANCEL_SCOPE')
                self.sent[code]+=1
            return EClient.sendMsg(self,msg)
        def nextValidId(self,orderId):self.connected.set()
        def managedAccounts(self,accountsList):
            accounts=[s.strip() for s in accountsList.split(',') if s.strip()]
            papers=[a for a in accounts if a.startswith('DU')]
            with self.guard:
                if len(papers)==1:
                    self.account=papers[0];self.state=CashState(self.account)
                    self.accepted=True
                else:self.terminal_error=True
            self.managed.set()
        def error(self,reqId,errorCode,errorString,advancedOrderRejectJson=''):
            with self.guard:
                self.errors.append({'code':errorCode,'request_id':reqId})
                if not nonfatal_account_transport_notice(reqId,errorCode,errorString):
                    self.terminal_error=True
                if self.terminal_error:self.finished.set()
        def accountUpdateMulti(self,reqId,account,modelCode,key,value,currency):
            if reqId!=REQ_ID:return
            with self.guard:
                if self.terminal_error or self.state is None:return
                try:self.state.ingest(account,key,currency,value,int(time.time()*1000))
                except EvidenceError as exc:
                    self.errors.append({'code':'LOCAL_EVIDENCE_ERROR','reason':str(exc)})
                    self.terminal_error=True;self.finished.set()
        def accountUpdateMultiEnd(self,reqId):
            if reqId!=REQ_ID:return
            with self.guard:
                if self.state is None or self.terminal_error:return
                try:self.state.end(int(time.time()*1000))
                except EvidenceError as exc:
                    self.errors.append({'code':'LOCAL_END_ERROR','reason':str(exc)})
                    self.terminal_error=True
                self.finished.set()
    with global_history_lock(root):
        app=Probe();thread=None
        started=int(time.time()*1000);note={'schema':'heptatrader.paper-account-cash-probe.v1',
         'at_ms':started,'broker_orders_sent':0,'client_id':PROBE_CLIENT,
         'read_only':True,'paper_verified':False,'authoritative_account_snapshot':False}
        try:
            app.connect(c['host'],c['port'],PROBE_CLIENT)
            thread=threading.Thread(target=app.run,daemon=True);thread.start()
            if not app.connected.wait(10) or not app.managed.wait(10):
                raise EvidenceError('PAPER_ACCOUNTS_HANDSHAKE_TIMEOUT')
            if not app.accepted:raise EvidenceError('UNIQUE_PAPER_ACCOUNT_NOT_PROVEN')
            note['paper_verified']=True
            app.reqAccountUpdatesMulti(REQ_ID,app.account,'',True)
            if not app.finished.wait(20):raise EvidenceError('ACCOUNT_INITIAL_END_TIMEOUT')
            if app.terminal_error:raise EvidenceError('ACCOUNT_STREAM_ERROR')
            time.sleep(wait_after_end_sec)
            if app.terminal_error:raise EvidenceError('ACCOUNT_STREAM_ERROR_AFTER_END')
            note['result']=app.state.snapshot()
            note['state']='OBSERVER_CLOSED'
        except Exception as exc:
            note['state']='OBSERVATION_FAILED'
            note['reason']=type(exc).__name__+': '+str(exc)
        finally:
            if app.sent[76] and app.isConnected() and not app.sent[77]:
                try:app.cancelAccountUpdatesMulti(REQ_ID)
                except Exception as exc:note['cancel_error']=type(exc).__name__
            app.disconnect()
            if thread:thread.join(timeout=2)
            note.update(ended_at_ms=int(time.time()*1000),sent_codes=dict(app.sent),
                        errors=app.errors[-30:],
                        account_id_hash=app.state.private_hash(app.account) if app.account and app.state else None)
            write_once(root/'session.json',note)
        return note

def main():
    p=argparse.ArgumentParser();p.add_argument('--config',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--after-end-seconds',type=int,default=25)
    opt=p.parse_args()
    out=run(opt.config,opt.output,opt.after_end_seconds)
    print(json.dumps({k:out.get(k) for k in ('state','paper_verified','sent_codes','result','reason','errors')},ensure_ascii=False))
    if out['state']!='OBSERVER_CLOSED':raise SystemExit(2)
if __name__=='__main__':main()
