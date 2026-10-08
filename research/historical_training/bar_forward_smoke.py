"""Single, bounded PAPER history-based forward proof; never a trading service.
One frozen candidate, one future outcome, four finite historical queries.
Inputs are stored before the forecast; the forecast before the target interval.
"""
from __future__ import annotations
import argparse,collections,json,math,sys,threading,time
from datetime import datetime,timezone
from pathlib import Path
from decimal import Decimal
from .common import EvidenceError,read,sha,digest,write_once
from .dataset import checked_bar

CLIENT=8991

def utc_request(seconds):return datetime.fromtimestamp(seconds,timezone.utc).strftime('%Y%m%d %H:%M:%S UTC')


def bar_input(series,anchor):
    expected=list(range(anchor-3600,anchor,300));joined=[]
    if type(anchor) is not int or anchor%300:raise EvidenceError('ANCHOR_GRID')
    if set(series)!={'MIDPOINT','BID','ASK'}:raise EvidenceError('THREE_SOURCE_SERIES_REQUIRED')
    for t in expected:
        try:r={k:series[k][t] for k in series}
        except KeyError as exc:raise EvidenceError('CONTIGUOUS_12_BAR_INPUT_REQUIRED') from exc
        if any(Decimal(r['BID'][k])>Decimal(r['ASK'][k]) for k in ('open','close')):raise EvidenceError('CROSSED_INPUT')
        joined.append(r)
    mid=[float(r['MIDPOINT']['close']) for r in joined]
    spread=[(float(r['ASK']['close'])-float(r['BID']['close']))/m*10000 for r,m in zip(joined,mid)]
    hour=datetime.fromtimestamp(anchor,timezone.utc).hour
    values=[(m/mid[-1]-1)*10000 for m in mid[:-1]]+spread+[math.sin(hour*math.pi/12),math.cos(hour*math.pi/12)]
    return {'time':anchor,'history_start':anchor-3600,'features':values}


def check_frozen(candidate,now_ms,anchor):
    if candidate.get('candidate_id')!=digest({k:v for k,v in candidate.items() if k!='candidate_id'}):raise EvidenceError('CANDIDATE_HASH')
    if (candidate.get('profile')!='EURUSD_5MIN_LOOKBACK12_LEAD1_HOLD3_V1' or candidate.get('price_shadow_eligible') is not True
        or candidate.get('trade_authority') is not False or candidate.get('automatic_promotion') is not False):raise EvidenceError('FROZEN_PRICE_ONLY_SCOPE')
    if candidate['created_at_ms']>=anchor*1000 or now_ms>= (anchor+300)*1000:raise EvidenceError('LATE_OR_NONPROSPECTIVE_PREDICTION')


class WindowGuard:
    def __init__(self,anchor):
        self.anchor=anchor;self.started=False;self.issued=set();self.cancelled=set();self.last=None;self.prediction_published=False
    def expected(self,rid):
        kinds={9101:'MIDPOINT',9102:'BID',9103:'ASK',9104:'MIDPOINT'}
        if rid not in kinds:raise EvidenceError('FOREIGN_WINDOW_REQUEST')
        end=self.anchor if rid!=9104 else self.anchor+1500;duration='7200 S' if rid!=9104 else '1800 S'
        return ['20',str(rid),'12087792','EUR','CASH','','0.0','','','IDEALPRO','','USD','','','0',utc_request(end),'5 mins',duration,'0',kinds[rid],'2','0','']
    def check(self,msg,paper,now,monotonic):
        if not isinstance(msg,str) or not msg.endswith('\0'):raise EvidenceError('WIRE_FORMAT')
        fields=msg[:-1].split('\0')
        if fields==['71','2',str(CLIENT),'']:
            if self.started:raise EvidenceError('REPEAT_START')
            self.started=True;return
        if not self.started or not paper:raise EvidenceError('PAPER_NOT_VERIFIED')
        if not fields or fields[0] not in ('20','25'):raise EvidenceError('NON_HISTORY_WIRE_DENIED')
        if fields[0]=='25':
            if len(fields)!=3 or fields[1]!='1' or not fields[2].isdigit():raise EvidenceError('CANCEL_FORMAT')
            rid=int(fields[2])
            if rid not in self.issued or rid in self.cancelled:raise EvidenceError('FOREIGN_OR_REPEATED_CANCEL')
            self.cancelled.add(rid);return
        try:rid=int(fields[1])
        except (ValueError,IndexError):raise EvidenceError('WIRE_REQUEST_ID')
        if fields!=self.expected(rid) or rid in self.issued:raise EvidenceError('WINDOW_PAYLOAD_OR_REPEAT')
        if rid==9104:
            if not self.prediction_published or now<self.anchor+1502:raise EvidenceError('OUTCOME_BEFORE_PREDICTION_OR_MATURITY')
        elif not self.anchor+2<=now<self.anchor+240:raise EvidenceError('INPUT_COLLECTION_DEADLINE')
        if self.last is not None and monotonic-self.last<12:raise EvidenceError('WINDOW_PACING')
        self.issued.add(rid);self.last=monotonic


def run(candidate_path,history_config,health_path,output,*,anchor_seconds=None):
    out=Path(output)
    if out.exists():raise EvidenceError('FORWARD_PROOF_EXISTS_NO_REPEAT')
    out.mkdir(mode=0o700)
    c=read(candidate_path);config=read(history_config);candidate_sha=sha(candidate_path)
    now=time.time()
    if anchor_seconds is not None:
        if type(anchor_seconds) is not int or anchor_seconds%300 or not 5<anchor_seconds-now<=90:
            raise EvidenceError('PREDECLARED_ANCHOR_START_DEADLINE')
        anchor=anchor_seconds
    else:anchor=math.ceil((now+90)/300)*300
    check_frozen(c,int(now*1000),anchor)
    health=read(health_path)
    if (now*1000-health['at_ms']>120000 or health.get('usable',0)<20 or health.get('unusable',25)>4):raise EvidenceError('EXISTING_SOURCE_REVIEW_GATE_NOT_READY')
    sdk=Path(config['sdk_root'])
    for name,value in config['sdk_sha256'].items():
        if sha(sdk/'ibapi'/name)!=value:raise EvidenceError('PINNED_SDK_CHANGED')
    sdkpins={str(p.relative_to(sdk)):sha(p) for p in (sdk/'ibapi').rglob('*.py')}
    plan={'schema':'heptatrader.single-future-bar-proof-plan.v1','registered_at_ms':int(now*1000),
        'candidate_sha256':candidate_sha,'candidate_id':c['candidate_id'],'anchor_seconds':anchor,
        'entry_seconds':anchor+300,'exit_seconds':anchor+1200,'outcome_ready_seconds':anchor+1500,
        'lookback_bars':12,'bar_seconds':300,'decision_lead_seconds':300,'holding_seconds':900,
        'source_review_sha256':sha(health_path),'source_usable':health['usable'],'source_unusable':health['unusable'],
        'sdk_sha256':sdkpins,'producer_sha256':sha(__file__),'input_requests':3,'outcome_requests':1,
        'scope':'ONE_PROSPECTIVE_OPERATIONAL_PROOF_NOT_STATISTICAL_EFFICACY',
        'data_basis':'IB_REVISED_HISTORICAL_BARS_WITH_ORIGINAL_INPUT_BYTES_RETAINED',
        'independent_sample_count_max':1,'automatic_promotion':False,'trade_authority':False,'action_policy':'ABSTAIN'}
    write_once(out/'plan.json',plan)
    sys.path.insert(0,str(sdk))
    from ibapi.client import EClient
    from ibapi.wrapper import EWrapper
    from ibapi.contract import Contract
    class Reader(EWrapper,EClient):
        def __init__(self):
            EWrapper.__init__(self);EClient.__init__(self,self);self.ready=threading.Event();self.accounts=threading.Event();self.paper=False
            self.guard=WindowGuard(anchor);self.events={};self.rows={};self.ends={};self.errors=[];self.counts=collections.Counter()
        def sendMsg(self,msg):
            self.guard.check(msg,self.paper,time.time(),time.monotonic());self.counts[int(msg.split('\0')[0])]+=1
            return EClient.sendMsg(self,msg)
        def nextValidId(self,orderId):self.ready.set()
        def managedAccounts(self,accountsList):
            xs=[x for x in accountsList.split(',') if x]
            expected=config.get('expected_paper_account')
            self.paper=bool(xs) and all(x.startswith('DU') for x in xs) and (expected is None or expected in xs)
            self.accounts.set()
        def error(self,reqId,errorCode,errorString,advancedOrderRejectJson=''):
            self.errors.append({'request_id':reqId,'code':errorCode,'message':errorString})
            if reqId in self.events:self.events[reqId].set()
        def historicalData(self,reqId,bar):
            if reqId not in self.guard.issued or reqId in self.guard.cancelled:raise EvidenceError('UNBOUND_BAR_CALLBACK')
            if len(self.rows[reqId])>=1000:raise EvidenceError('BAR_BUDGET')
            self.rows[reqId].append({'time':str(bar.date),**{k:str(getattr(bar,k)) for k in ('open','high','low','close')}})
        def historicalDataEnd(self,reqId,start,end):
            if reqId not in self.events:raise EvidenceError('UNBOUND_END')
            self.ends[reqId]={'start':start,'end':end};self.events[reqId].set()
    app=Reader();thread=None;receipt={'schema':'heptatrader.single-future-bar-proof-result.v1','plan_sha256':sha(out/'plan.json'),
        'candidate_id':c['candidate_id'],'outcome_count':0,'state':'STARTED','broker_orders_sent':0,'automatic_promotion':False}
    started=time.monotonic();wall_offset=time.time()-started
    def wait_until(when):
        while time.time()<when:
            if time.monotonic()-started>2100:raise EvidenceError('TOTAL_PROOF_TIME_BUDGET')
            if abs(time.time()-time.monotonic()-wall_offset)>2:raise EvidenceError('CLOCK_DISCONTINUITY')
            time.sleep(min(2,max(0.01,when-time.time())))
    contract=Contract()
    for k,v in {'conId':12087792,'symbol':'EUR','secType':'CASH','exchange':'IDEALPRO','currency':'USD'}.items():setattr(contract,k,v)
    def query(rid):
        if app.guard.last is not None:
            remaining=12.01-(time.monotonic()-app.guard.last)
            if remaining>0:time.sleep(remaining)
        fields=app.guard.expected(rid);app.events[rid]=threading.Event();app.rows[rid]=[]
        app.reqHistoricalData(rid,contract,fields[15],fields[17],fields[16],fields[19],0,2,False,[])
        app.events[rid].wait(45)
        errors=[x for x in app.errors if x['request_id']==rid]
        if rid not in app.ends:app.cancelHistoricalData(rid)
        raw={'request_id':rid,'received_at_ms':int(time.time()*1000),'fields':fields,'rows':app.rows[rid],'end':app.ends.get(rid),'errors':errors}
        write_once(out/(str(rid)+'.json'),raw)
        if rid not in app.ends or errors or not app.rows[rid]:raise EvidenceError('HISTORY_REQUEST_INCOMPLETE_NO_RETRY')
        answer={};end=anchor if rid!=9104 else anchor+1500
        for row in app.rows[rid]:
            t,value=checked_bar(row)
            if t+300>end:continue
            if t in answer:raise EvidenceError('DUPLICATE_WINDOW_BAR')
            answer[t]=value
        return answer
    try:
        app.connect('127.0.0.1',4002,CLIENT);thread=threading.Thread(target=app.run,daemon=True);thread.start()
        if not app.ready.wait(12) or not app.accounts.wait(3) or not app.paper:raise EvidenceError('PAPER_HANDSHAKE')
        if app.serverVersion()!=187:raise EvidenceError('WINDOW_SERVER_PROTOCOL_CHANGED')
        print(json.dumps({'registered':plan,'state':'AWAITING_FIXED_INPUT_ANCHOR'}),flush=True)
        wait_until(anchor+2)
        series={what:query(rid) for rid,what in ((9101,'MIDPOINT'),(9102,'BID'),(9103,'ASK'))}
        input_row=bar_input(series,anchor);write_once(out/'input.json',input_row)
        from .calibration_model import predict,calibrate_apply,predict_value,brier
        import numpy as np
        p=calibrate_apply(predict(c['model'],[input_row]),c['calibration'])[0]
        value=predict_value(c['value_model'],[input_row])[0]
        available=int(time.time()*1000);check_frozen(c,available,anchor)
        if sha(candidate_path)!=candidate_sha:raise EvidenceError('FROZEN_CANDIDATE_CHANGED')
        prediction={'candidate_id':c['candidate_id'],'candidate_sha256':candidate_sha,'input_sha256':sha(out/'input.json'),
            'available_at_ms':available,'target_entry_ms':(anchor+300)*1000,'target_exit_ms':(anchor+1200)*1000,
            'probabilities':p.tolist(),'value_net_bps':value.tolist(),'action':'ABSTAIN','trade_authority':False,
            'input_raw_sha256':{str(rid):sha(out/(str(rid)+'.json')) for rid in (9101,9102,9103)}}
        write_once(out/'prediction.json',prediction);app.guard.prediction_published=True
        receipt['prediction_sha256']=sha(out/'prediction.json');print(json.dumps({'state':'PREDICTION_DURABLE_BEFORE_TARGET','prediction':prediction}),flush=True)
        wait_until(anchor+1502)
        label_bars=query(9104)
        if anchor+300 not in label_bars or anchor+1200 not in label_bars:raise EvidenceError('OUTCOME_ENDPOINT_MISSING')
        if any(t not in label_bars for t in range(anchor+300,anchor+1500,300)):raise EvidenceError('OUTCOME_PATH_MISSING')
        entry=float(label_bars[anchor+300]['open']);exit_price=float(label_bars[anchor+1200]['open'])
        change=(exit_price/entry-1)*10000;label=0 if change< -1 else 2 if change>1 else 1
        receipt.update(state='OBSERVED_ONE_FORWARD_PRICE_OUTCOME_NOT_EFFICACY',outcome_count=1,
            target_bps=change,label=label,brier=float(brier([p],np.array([label]))[0]),
            train_prior_brier=float(brier([c['model']['fit_prior']],np.array([label]))[0]),uniform_brier=2/3,
            prediction_precedes_target=available<(anchor+300)*1000,
            input_and_forecast_unchanged=sha(out/'prediction.json')==receipt['prediction_sha256'],
            label_raw_sha256=sha(out/'9104.json'),statistical_efficacy_proven=False)
    except Exception as exc:
        receipt.update(state='CENSORED_NO_RETRY_NO_MODEL_CHANGE',reason=type(exc).__name__+': '+str(exc))
    finally:
        app.disconnect()
        if thread:thread.join(timeout=2)
        receipt.update(ended_at_ms=int(time.time()*1000),outbound_message_counts=dict(app.counts),errors=app.errors,
            paper_verified=app.paper,financial_message_codes_sent=[],candidate_unchanged=sha(candidate_path)==candidate_sha)
        write_once(out/'result.json',receipt);print(json.dumps(receipt,indent=2),flush=True)
    return receipt


def main():
    p=argparse.ArgumentParser();p.add_argument('--candidate',type=Path,required=True);p.add_argument('--history-config',type=Path,required=True)
    p.add_argument('--health',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    result=run(a.candidate,a.history_config,a.health,a.output)
    if result['outcome_count']!=1:raise SystemExit(1)
if __name__=='__main__':main()
