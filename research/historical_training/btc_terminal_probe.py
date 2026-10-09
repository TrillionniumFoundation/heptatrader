"""Read-only broker terminal evidence for three original BTC commands.
Never submits/cancels orders and never edits the native journal or PnL state.
"""
from __future__ import annotations
import argparse,collections,json,sys,threading,time
from pathlib import Path
from .common import EvidenceError,read,sha,write_once

TARGETS=('hexec-command-0dd1f7cb13c55273058ea8c002ca70f7',
 'hexec-command-5b8e2caf55ed91bb4ef9d82c12209357',
 'hexec-command-213f7bfb57709ef895e9745b2b47035f')

def main():
    p=argparse.ArgumentParser();p.add_argument('--history-config',type=Path,required=True)
    p.add_argument('--journal',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    cfg=read(a.history_config)
    for name,wanted in cfg['sdk_sha256'].items():
        if sha(Path(cfg['sdk_root'])/'ibapi'/name)!=wanted:raise EvidenceError('SDK_CHANGED')
    raw=a.journal.read_bytes();prefix=raw[:raw.rfind(b'\n')+1]
    records=[json.loads(line) for line in prefix.splitlines()]
    groups={command:[r for r in records if r.get('req_id')==command] for command in TARGETS}
    sends={command:next(r for r in rs if r['event']=='place_sent' or r['event']=='flatten_sent') for command,rs in groups.items()}
    accounts={r['account'] for r in sends.values()}
    if len(accounts)!=1 or not all(r.get('execution_domain')=='PAPER' and r.get('instrument')=='BTC.USD' for r in sends.values()):
        raise EvidenceError('ORIGINAL_PAPER_BTC_ONLY')
    account=next(iter(accounts))
    if not account.startswith('DU'):raise EvidenceError('PAPER_REQUIRED')
    sys.path.insert(0,cfg['sdk_root'])
    from ibapi.client import EClient
    from ibapi.wrapper import EWrapper
    from ibapi.execution import ExecutionFilter
    class Reader(EWrapper,EClient):
        def __init__(self):
            EWrapper.__init__(self);EClient.__init__(self,self)
            self.ready=threading.Event();self.paper=False;self.accounts=threading.Event()
            self.completed=threading.Event();self.executed=threading.Event()
            self.orders=[];self.executions=[];self.errors=[];self.outbound=collections.Counter()
        def sendMsg(self,msg):
            code=int(msg.split('\0')[0])
            if code not in (71,7,99) or (code!=71 and not self.paper):raise EvidenceError('READ_ONLY_METHOD_REQUIRED')
            if self.outbound[code]>=1:raise EvidenceError('ONE_REQUEST_PER_METHOD')
            self.outbound[code]+=1;return EClient.sendMsg(self,msg)
        def nextValidId(self,orderId):self.ready.set()
        def managedAccounts(self,accountsList):
            self.paper=account in accountsList.split(',') and all(x.startswith('DU') for x in accountsList.split(',') if x)
            self.accounts.set()
        def error(self,reqId,errorCode,errorString,advancedOrderRejectJson=''):
            self.errors.append({'request_id':reqId,'code':errorCode,'message':errorString})
        def completedOrder(self,contract,order,state):
            if contract.symbol!='BTC' or contract.secType!='CRYPTO':return
            self.orders.append({'conid':contract.conId,'symbol':contract.symbol,'sec_type':contract.secType,
                'currency':contract.currency,'account_matches':order.account==account,
                'order_id':order.orderId,'permanent_id':order.permId,'order_ref':order.orderRef,
                'side':order.action,'total_quantity':str(order.totalQuantity),
                'filled_quantity':str(getattr(order,'filledQuantity','')),
                'status':state.status,'completed_time':state.completedTime,'completed_status':state.completedStatus})
        def completedOrdersEnd(self):self.completed.set()
        def execDetails(self,reqId,contract,execution):
            if contract.symbol!='BTC' or contract.secType!='CRYPTO':return
            self.executions.append({'execution_id':execution.execId,'order_ref':execution.orderRef,
                'permanent_id':execution.permId,'order_id':execution.orderId,'account_matches':execution.acctNumber==account,
                'shares':str(execution.shares),'cumulative':str(execution.cumQty),'average_price':str(execution.avgPrice),
                'price':str(execution.price),'time':execution.time,'client_id':execution.clientId})
        def execDetailsEnd(self,reqId):self.executed.set()
    app=Reader();thread=None
    receipt={'schema':'heptatrader.btc-terminal-readonly-probe.v1','at_ms':int(time.time()*1000),
        'journal_prefix_bytes':len(prefix),'journal_prefix_sha256':__import__('hashlib').sha256(prefix).hexdigest(),
        'client_id':8985,'native_journal_modified':False,'terminal_status_inferred_from_flat':False,'commands':{}}
    try:
        app.connect('127.0.0.1',4002,8985);thread=threading.Thread(target=app.run,daemon=True);thread.start()
        if not app.ready.wait(12) or not app.accounts.wait(3) or not app.paper:raise EvidenceError('PAPER_HANDSHAKE')
        app.reqCompletedOrders(False);app.completed.wait(20)
        f=ExecutionFilter();f.acctCode=account;f.symbol='BTC';f.secType='CRYPTO';f.clientId=701
        f.time='20261007-00:00:00'
        app.reqExecutions(8911,f);app.executed.wait(20)
    except Exception as exc:receipt['exception']=type(exc).__name__+': '+str(exc)
    finally:
        app.disconnect()
        if thread:thread.join(timeout=2)
    for command,rs in groups.items():
        send=sends[command];fills=[r for r in rs if r.get('event')=='broker_execution']
        perms={r.get('broker_econ_permanent_order_id') for r in fills}
        matching=[r for r in app.orders if r['account_matches'] and r['conid']==479624278
            and r['currency']=='USD' and r['order_ref']==send['venue_correlation_id'] and r['permanent_id'] in perms]
        statuses=[r for r in rs if r.get('event')=='broker_order_status']
        receipt['commands'][command]={'original_order_id':send['order_id'],'permanent_ids':sorted(x for x in perms if x),
            'last_local_status':statuses[-1]['status'] if statuses else None,
            'last_local_remaining':statuses[-1].get('broker_remaining_quantity') if statuses else None,
            'matching_completed_orders':matching,
            'state':'BROKER_TERMINAL_CANDIDATE_REQUIRES_NATIVE_IMPORT' if matching else 'TERMINAL_NOT_RETURNED_REMAINS_UNCONFIRMED'}
    receipt.update(completed_end=app.completed.is_set(),execution_end=app.executed.is_set(),
        returned_btc_orders=app.orders,returned_btc_executions=app.executions,errors=app.errors,
        outbound_message_counts=dict(app.outbound),broker_orders_sent=0)
    write_once(a.output,receipt)
    print(json.dumps({k:v for k,v in receipt.items() if k not in ('returned_btc_orders','returned_btc_executions','errors')},indent=2))
if __name__=='__main__':main()
