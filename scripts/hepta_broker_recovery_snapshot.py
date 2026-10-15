#!/usr/bin/env python3
"""Exact-account PAPER recovery evidence. No quotes, orders, binding or NAV writes.

An independent client reads open/completed orders, filtered executions,
positions and currency ledger. End callbacks are receipt barriers, not a
common valuation or exhaustive historical execution certificate.
"""
from __future__ import annotations
import argparse
from collections import Counter
import fcntl
import hashlib
import json
import logging
import os
import re
from pathlib import Path
import stat
import sys
import threading
import time


def now_ms():
    return time.time_ns() // 1_000_000


def sha(data):
    return hashlib.sha256(data).hexdigest()


def private_bytes(path, limit=32*1024*1024):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        a = os.fstat(fd)
        if (not stat.S_ISREG(a.st_mode) or a.st_uid != os.geteuid() or
                a.st_mode & 0o077 or a.st_nlink != 1 or a.st_size > limit):
            raise ValueError('PRIVATE_SOURCE_REQUIRED')
        with os.fdopen(fd, 'rb', closefd=False) as f:
            data = f.read(limit+1)
        b = os.fstat(fd)
        if len(data) > limit or (a.st_ino,a.st_size,a.st_mtime_ns) != (b.st_ino,b.st_size,b.st_mtime_ns):
            raise ValueError('SOURCE_CHANGED_DURING_CAPTURE')
        return data
    finally:
        os.close(fd)


# The native bounded OMS journal is capped at 64 MiB. Its read-only
# reconstruction needs the same bound; ordinary credentials stay 32 MiB.
MAX_EXECUTION_OMS_BYTES = 64 * 1024 * 1024


def private_execution_journal_bytes(path):
    return private_bytes(path, limit=MAX_EXECUTION_OMS_BYTES)


def save(path, value):
    data = (json.dumps(value, sort_keys=True, indent=2, allow_nan=False)+'\n').encode()
    fd = os.open(path, os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd,'wb') as f:
        f.write(data); f.flush(); os.fsync(f.fileno())
    return sha(data)


class RecoveryReadGate:
    """Pin every outbound SDK message to one exact read-only query set."""
    def __init__(self, client_id: int, account: str, owners: dict[str,int]):
        if type(client_id) is not int or not 29000 <= client_id <= 29999:
            raise ValueError('ISOLATED_NONZERO_CLIENT_REQUIRED')
        if set(owners) != {'EUR.USD','BTC.USD'} or any(type(x) is not int or x <= 0 for x in owners.values()):
            raise ValueError('EXACT_MIXED_OWNER_SCOPE_REQUIRED')
        if not isinstance(account,str) or re.fullmatch(r'DU[A-Z0-9]{1,30}',account) is None:
            raise ValueError('EXACT_PAPER_ACCOUNT_REQUIRED')
        if client_id in owners.values():
            raise ValueError('OWNER_CLIENT_COLLISION')
        self.client_id=client_id; self.account=account; self.owners=owners
        self.seen=set(); self.counts=Counter()
        self.expected={
            'start': ['71','2',str(client_id)],
            'open': ['16','1'], 'completed': ['99','1'],
            'positions': ['74','1','7301',account,''],
            'ledger': ['76','1','7302',account,'','1'],
            'aggregate': ['76','1','7303',account,'','0'],
            'cancel_positions': ['75','1','7301'],
            'cancel_ledger': ['77','1','7302'],
            'cancel_aggregate': ['77','1','7303'],
        }
        for instrument,rid in (('EUR.USD',7401),('BTC.USD',7402)):
            self.expected[instrument]=['7','3',str(rid),str(owners[instrument]),account,'',
                                       instrument.split('.')[0], 'CASH' if rid==7401 else 'CRYPTO','','']

    def check(self, msg: str):
        if not isinstance(msg,str) or not msg.endswith('\0'):
            raise ValueError('INVALID_READ_FRAME')
        fields=msg[:-1].split('\0')
        # The SDK may include one empty optionalCapabilities on StartApi.
        if fields==self.expected['start']+['']:
            fields=fields[:-1]
        name=next((k for k,v in self.expected.items() if fields==v),None)
        if name is None or name in self.seen or (name!='start' and 'start' not in self.seen):
            raise ValueError('MUTATING_FOREIGN_OR_DUPLICATE_SEND_DENIED')
        if name.startswith('cancel_') and name[7:] not in self.seen:
            raise ValueError('UNOWNED_READ_CANCEL_DENIED')
        self.seen.add(name);self.counts[fields[0]]+=1
        return name



def unscoped_historical_farm_disconnect(request_id: int, code: int) -> bool:
    """Historical-data farm 2105 is a warning for this order/account reader.

    Never treat a scoped error or missing request End as successful recovery.
    """
    return type(request_id) is int and request_id == -1 and type(code) is int and code == 2105


def contract_row(c):
    return {k:getattr(c,k,None) for k in ('conId','symbol','secType','exchange','currency','localSymbol')}


def capture(sdk: Path, client_id: int, period: dict, budget_sec: float=15) -> dict:
    from hepta_fx_readonly_probe import read_sdk
    EClient,EWrapper,_=read_sdk(sdk)
    from ibapi.execution import ExecutionFilter
    account=period['account']; owners={k:v['broker_client_id'] for k,v in period['instruments'].items()}
    gate=RecoveryReadGate(client_id,account,owners);ready=threading.Event();lock=threading.RLock()
    expected={'open','completed','positions','ledger','aggregate','EUR.USD','BTC.USD'}
    ends={};events=[];late=[];errors=[];notifications=[];foreign=Counter();lost=threading.Event()
    request_names={7301:'positions',7302:'ledger',7303:'aggregate',7401:'EUR.USD',7402:'BTC.USD'}
    def record(kind, data, group=None):
        with lock:
            target=late if group in ends else events
            if len(events)+len(late)>=20000:
                lost.set();return
            target.append(dict(kind=kind,received_at_ms=now_ms(),**data))
    def end(group):
        with lock:
            if group in ends:errors.append({'code':'DUPLICATE_END','request':group})
            else:ends[group]=now_ms()
    def order(c,o,st,kind):
        if o.account!=account:
            foreign[kind]+=1;return
        record(kind, {'account':o.account,'client_id':o.clientId,'order_id':o.orderId,
            'permanent_order_id':o.permId,'order_ref':o.orderRef,'contract':contract_row(c),
            'action':o.action,'total_quantity':str(o.totalQuantity),'cash_quantity':str(o.cashQty),
            'order_type':o.orderType,'status':st.status,
            'completed_time':getattr(st,'completedTime',None)},'open' if kind=='open_order' else 'completed')
    class Reader(EWrapper,EClient):
        def __init__(self):EClient.__init__(self,self)
        def sendMsg(self,msg):
            gate.check(msg);return EClient.sendMsg(self,msg)
        def nextValidId(self,_):ready.set()
        def error(self,rid,code,message,*args):
            if unscoped_historical_farm_disconnect(rid,code):
                # Preserve the negative HMDS connection signal separately.
                # This does not establish any order, position or account End.
                with lock:
                    if len(notifications)<32:notifications.append({'request_id':rid,'code':code})
            elif code not in (2104,2106,2107,2108,2158,2172):
                with lock:errors.append({'request_id':rid,'code':code})
        def connectionClosed(self):lost.set()
        def openOrder(self,oid,c,o,s):
            o.orderId=oid;order(c,o,s,'open_order')
        def openOrderEnd(self):end('open')
        def completedOrder(self,c,o,s):order(c,o,s,'completed_order')
        def completedOrdersEnd(self):end('completed')
        def execDetails(self,rid,c,x):
            group=request_names.get(rid)
            if x.acctNumber!=account or group not in owners or x.clientId!=owners[group]:
                foreign['execution']+=1;return
            record('execution',{'request_id':rid,'account':x.acctNumber,'client_id':x.clientId,
                'order_id':x.orderId,'permanent_order_id':x.permId,'execution_id':x.execId,
                'order_ref':x.orderRef,'contract':contract_row(c),'side':x.side,
                'shares':str(x.shares),'price':str(x.price),'cumulative_quantity':str(x.cumQty),
                'average_price':str(x.avgPrice),'broker_execution_time':x.time,
                'execution_exchange':x.exchange},group)
        def execDetailsEnd(self,rid):
            if rid in (7401,7402):end(request_names[rid])
        def commissionReport(self,x):
            record('commission',{'execution_id':x.execId,'commission':str(x.commission),'currency':x.currency})
        def positionMulti(self,rid,acct,model,c,pos,cost):
            if (rid,acct,model)!=(7301,account,''):
                foreign['position']+=1;return
            record('position',{'request_id':rid,'account':acct,'model':model,'contract':contract_row(c),
                               'quantity':str(pos),'average_cost':str(cost)},'positions')
        def positionMultiEnd(self,rid):
            if rid==7301:end('positions')
        def accountUpdateMulti(self,rid,acct,model,key,value,currency):
            if acct!=account or model or rid not in (7302,7303):
                foreign['account']+=1;return
            record('account_value',{'request_id':rid,'account':acct,'model':model,'key':key,
                                    'value':value,'currency':currency},request_names[rid])
        def accountUpdateMultiEnd(self,rid):
            if rid in (7302,7303):end(request_names[rid])
    client=Reader();worker=None;started=now_ms();failure=None;all_ends=False
    logging.disable(logging.CRITICAL)
    try:
        client.connect('127.0.0.1',4002,client_id)
        worker=threading.Thread(target=client.run,daemon=True);worker.start()
        if not ready.wait(8):raise ValueError('BROKER_HANDSHAKE_TIMEOUT')
        client.reqAllOpenOrders();client.reqCompletedOrders(True)
        for instrument,rid in (('EUR.USD',7401),('BTC.USD',7402)):
            f=ExecutionFilter();f.acctCode=account;f.clientId=owners[instrument]
            f.symbol=instrument.split('.')[0];f.secType='CASH' if rid==7401 else 'CRYPTO'
            client.reqExecutions(rid,f)
        client.reqPositionsMulti(7301,account,'')
        client.reqAccountUpdatesMulti(7302,account,'',True)
        client.reqAccountUpdatesMulti(7303,account,'',False)
        deadline=time.monotonic()+budget_sec;settle=None
        while time.monotonic()<deadline and not lost.is_set() and not errors:
            with lock:all_ends=set(ends)==expected
            if all_ends:
                if settle is None:settle=time.monotonic()+1
                if time.monotonic()>=settle:break
            time.sleep(.02)
        if lost.is_set():failure='BROKER_CONNECTION_LOST'
        elif errors:failure='BROKER_READ_ERROR'
        elif not all_ends:failure='BROKER_RECOVERY_ENDS_INCOMPLETE'
    except (OSError,ValueError) as exc:
        failure=str(exc) if isinstance(exc,ValueError) else type(exc).__name__
    finally:
        for name,fn,rid in (('positions',client.cancelPositionsMulti,7301),
                             ('ledger',client.cancelAccountUpdatesMulti,7302),
                             ('aggregate',client.cancelAccountUpdatesMulti,7303)):
            if client.isConnected() and name in gate.seen:
                try:fn(rid)
                except Exception:failure=failure or 'READ_CANCEL_FAILED'
        client.disconnect()
        if worker:worker.join(2)
    return dict(schema='heptatrader.broker-recovery-download.v1',started_at_ms=started,
        observed_at_ms=now_ms(),account=account,observer_client_id=client_id,owner_clients=owners,
        transport_complete=all_ends and failure is None,ends=ends,events=events,late_events=late,
        foreign_callback_counts=dict(foreign),errors=errors,
        broker_status_notifications=notifications,failure=failure,wire_counts=dict(gate.counts),
        requested_groups=sorted(gate.seen),order_sends=0,quote_subscriptions=0,
        full_execution_started=False,broker_history_complete=False,strict_nav_eligible=False,
        broker_valuation_time_attested=False,execution_history_scope='API_AVAILABLE_CURRENT_DAY_NOT_ALL_HISTORY')


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--sdk',type=Path,required=True);p.add_argument('--client-id',type=int,required=True)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args(argv)
    if os.geteuid()==0:raise ValueError('USER_PAPER_ONLY')
    os.umask(0o077);h=Path.home()
    period_path=h/'.config/heptatrader/pnl-period.json';observer_path=h/'.config/heptatrader/account-observer.json'
    pb=private_bytes(period_path);ob=private_bytes(observer_path);period=json.loads(pb);observer=json.loads(ob)
    if period.get('execution_domain')!='PAPER' or not str(period.get('account','')).startswith('DU'):
        raise ValueError('EXACT_PAPER_ACCOUNT_REQUIRED')
    if a.client_id==observer['client_id']:raise ValueError('OBSERVER_CLIENT_COLLISION')
    RecoveryReadGate(a.client_id,period['account'],{k:v['broker_client_id'] for k,v in period['instruments'].items()})
    lock=h/'.local/state/heptatrader/operator-evidence/full-recovery-download.lock'
    fd=os.open(lock,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
    st=os.fstat(fd)
    if not stat.S_ISREG(st.st_mode) or st.st_uid!=os.geteuid() or st.st_mode&0o077 or st.st_nlink!=1:
        os.close(fd);raise ValueError('PRIVATE_RECOVERY_LOCK_REQUIRED')
    fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
    a.output.mkdir(mode=0o700,parents=False,exist_ok=False)
    if a.output.resolve()!=a.output.absolute():raise ValueError('NO_OUTPUT_SYMLINKS')
    journal=h/'.local/state/heptatrader/execution/oms-journal.jsonl';before=private_execution_journal_bytes(journal)
    checkpoint=h/'.local/state/heptatrader/execution/ib-fx-cash-restart-attestation'
    checkpoint_before=private_bytes(checkpoint,16384) if checkpoint.exists() else None
    source=Path(__file__).resolve();root=source.parent.parent
    plan=dict(schema='heptatrader.broker-recovery-plan.v1',created_at_ms=now_ms(),
        source_sha256=sha(source.read_bytes()),period_sha256=sha(pb),observer_config_sha256=sha(ob),
        journal_before_sha256=sha(before),observer_client_id=a.client_id,mutations=False,
        sdk_sha256={n:sha((a.sdk/'ibapi'/n).read_bytes()) for n in ('client.py','wrapper.py','execution.py','message.py')})
    plan['fx_checkpoint_before_sha256']=sha(checkpoint_before) if checkpoint_before is not None else None
    save(a.output/'plan.json',plan)
    raw=capture(a.sdk,a.client_id,period)
    after=private_execution_journal_bytes(journal)
    checkpoint_after=private_bytes(checkpoint,16384) if checkpoint.exists() else None
    raw.update(period_sha256=sha(pb),period_unchanged=private_bytes(period_path)==pb,
               journal_before_sha256=sha(before),journal_after_sha256=sha(after),journal_stable=before==after)
    raw.update(fx_checkpoint_before_sha256=sha(checkpoint_before) if checkpoint_before is not None else None,
               fx_checkpoint_after_sha256=sha(checkpoint_after) if checkpoint_after is not None else None,
               fx_checkpoint_stable=checkpoint_before is not None and checkpoint_before==checkpoint_after)
    files=[('journal-before.jsonl',before),('journal-after.jsonl',after)]
    if checkpoint_before is not None:files.append(('fx-checkpoint-before.txt',checkpoint_before))
    if checkpoint_after is not None:files.append(('fx-checkpoint-after.txt',checkpoint_after))
    for name,data in files:
        with (a.output/name).open('xb') as f:f.write(data);f.flush();os.fsync(f.fileno())
    digest=save(a.output/'broker-raw.json',raw)
    print(json.dumps({'schema':raw['schema'],'transport_complete':raw['transport_complete'],
        'ends':sorted(raw['ends']),'event_counts':dict(Counter(e['kind'] for e in raw['events'])),
        'journal_stable':raw['journal_stable'],'wire_counts':raw['wire_counts'],'failure':raw['failure'],
        'raw_sha256':digest,'order_sends':0,'strict_nav_eligible':False},sort_keys=True))
    os.close(fd)
    return 0 if raw['transport_complete'] and raw['period_unchanged'] else 2


if __name__=='__main__':raise SystemExit(main())
