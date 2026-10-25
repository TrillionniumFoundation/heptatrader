"""Contract tests with a fake native transport, not live Broker execution."""
import unittest, tempfile, copy, json, contextlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from fixtures import *
import hepta_cost_driver as adapter

class FakeCapture:
    def __init__(self,*a,**k): self.events=[];self.failed=False
    def append(self,event,data,**kwargs):
        if self.failed:raise OSError('SYNTHETIC DISK FAILURE')
        self.events.append((event,copy.deepcopy(data)))
    def close(self):pass

class Host:
    def __init__(self):
        self.INSTRUMENT='BTC.USD';self.QTY=1.;self.QUANTITY_TOLERANCE=1e-10;self.MAX_ADVERSE=.002;self.MODEL='gpt-6-luna'
        self.MODEL_SIGNAL_TTL_MS=60000
        self.now=NOW;self.q=quote();self.calls=[];self.writes=[];self.change_after_prepare=None;self.submit_result='ok'
        self.snap={'active':[],'position':0.,'owner_health':{'mutation_ready':True}}
        self.DriverError=type('DriverError',(RuntimeError,),{})
    def now_ms(self):return self.now
    def crypto_market_cash_budget(self,ask):return 25000.
    def atomic_json(self,p,s):self.writes.append(copy.deepcopy(s))
    def native(self,cli,tool,*a):
        self.calls.append(tool)
        if tool=='market.get_quote':return self.q
        raise AssertionError(tool)
    def snapshot(self,*a,**k):return self.snap
    def paper_cmd(self,b,s,t,o,op,extra):
        self.calls.append(op)
        if op.startswith('prepare-'):
            if self.change_after_prepare:self.change_after_prepare()
            return {'ok':True,'command_id':'original-command'}
        if op=='submit':
            return {'ok':self.submit_result=='ok','transport_ok':self.submit_result=='ok',
                    'application_status':self.submit_result,'result':{'order_id':42}}
        raise AssertionError(op)
    def prepare_send(self,state,path,binary,socket,token,outbox,kind,quote=None,side=None):
        if state.get('pending'):raise self.DriverError('original pending')
        p=self.paper_cmd(binary,socket,token,outbox,'prepare-crypto-open' if kind=='OPEN' else 'prepare-crypto-close',[])
        state['pending']={'kind':kind,'command_id':p['command_id'],'send_state':'PREPARED'}
        self.atomic_json(path,state)
        state['pending']['send_state']='POSSIBLY_SENT';self.atomic_json(path,state)
        r=self.paper_cmd(binary,socket,token,outbox,'submit',[])
        state['pending']['send_state']='ACKNOWLEDGED' if r['application_status']=='ok' else 'DEFINITELY_REJECTED' if r['application_status']=='error' else 'POSSIBLY_SENT'
        self.atomic_json(path,state)
    def entry_candidate_allowed(self,decision):
        actions={"BUY"} if self.INSTRUMENT=="BTC.USD" else {"BUY","SELL"}
        return bool(decision and decision.get("action") in actions and decision.get("confidence",-1)>=.62)
    def should_close(self,state,pos,quote,decision):
        reason=risk_exit(pos,state.get('entry_price'),quote,self.now,halt=state.get('halt_new_entries',False))
        if reason:return True,reason
        if decision and decision['confidence']>=.62 and (pos>0 and decision['action']=='SELL' or pos<0 and decision['action']=='BUY'):
            return True,'MODEL_REVERSAL'
        return False,''
    def inspect_pending(self,state,path,b,s,t,o,snap):
        cid=state['pending']['command_id'];kind=state['pending']['kind']
        state['pending']=None;state['position']=snap['position'];state['last_reconciliation']={'command_id':cid,'kind':kind}
        self.atomic_json(path,state)
    def model_decision(self,*a):raise AssertionError('legacy model must be replaced')
    def strict_json(self,s):return strict_loads(s)
    def openclaw_model_ref(self,m):return 'openai/gpt-5.6-luna'
    def run(self,args,timeout):
        self.model_args=args
        self.model_timeout=timeout
        f=forecast();raw={k:f[k] for k in adapter.CANDIDATE_SCHEMA['required']}
        return SimpleNamespace(returncode=0,stdout=json.dumps({'ok':True,'outputs':[{'text':json.dumps(raw)}]}))

class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.host=Host();self.capture=FakeCapture();self.close=adapter.attach(self.host,Path('/fixture-not-real'),capture_factory=lambda *a,**k:self.capture)
        self.state={'pending':None,'position':0.,'last_decision':forecast(),'entry_price':None,'halt_new_entries':False}
    def tearDown(self):self.close()
    def open(self):self.host.prepare_send(self.state,Path('/not-written'),'b','s','t','o','OPEN',self.host.q,'BUY')
    def trusted_fixture(self):return contextlib.nullcontext()
    def test_historical_confidence_threshold_controls_entry_candidate(self):
        low=forecast();low["confidence"]=0.61;validate_forecast(low)
        self.assertFalse(self.host.entry_candidate_allowed(low))
        edge=forecast();edge["confidence"]=0.62;validate_forecast(edge)
        self.assertTrue(self.host.entry_candidate_allowed(edge))
        hold=forecast(action="HOLD",move="0");hold["confidence"]=1.0;validate_forecast(hold)
        self.assertFalse(self.host.entry_candidate_allowed(hold))

    def test_cost_policy_no_longer_blocks_open(self):
        self.state["last_decision"]=forecast(move="20")
        self.open()
        self.assertEqual(self.host.calls,["prepare-crypto-open","submit"])
        self.assertEqual(self.state["pending"]["send_state"],"ACKNOWLEDGED")
        self.assertNotIn("cost_last_admission",self.state)

    def test_capture_failure_cannot_block_open(self):
        self.capture.failed=True
        self.open()
        self.assertEqual(self.host.calls,["prepare-crypto-open","submit"])

    def test_close_still_bypasses_all_cost_logic(self):
        self.capture.failed=True;self.state["position"]=1
        self.host.prepare_send(self.state,Path("/not-written"),"b","s","t","o","CLOSE")
        self.assertEqual(self.host.calls,["prepare-crypto-close","submit"])

    def test_old_pending_never_replaced(self):
        self.state["pending"]={"command_id":"order242-original","kind":"OPEN","send_state":"ACKNOWLEDGED"}
        old=copy.deepcopy(self.state)
        with self.assertRaises(self.host.DriverError):self.open()
        self.assertEqual(self.state,old);self.assertEqual(self.host.calls,[])

    def test_base_entry_protections_remain_authoritative(self):
        self.state["pending"]={"command_id":"existing","kind":"OPEN"}
        with self.assertRaises(self.host.DriverError):self.open()
        self.assertNotIn("submit",self.host.calls)

    def test_legacy_exit_confidence_semantics_are_not_relaxed(self):
        self.state["position"]=1;self.state["entry_price"]=100
        low=forecast(action="SELL",move="-20");low["confidence"]=0.01;validate_forecast(low)
        self.assertEqual(self.host.should_close(self.state,1,quote(),low),(False,''))

    def test_inherited_position_retains_original_exit(self):
        self.state['position']=1;self.state['entry_price']=100
        self.assertEqual(self.host.should_close(self.state,1,quote(),forecast(action='SELL',move='-20')),(True,'MODEL_REVERSAL'))
    def test_position_neutral_model_schema(self):
        with tempfile.TemporaryDirectory() as td:
            history=[{'observed_at_ms':NOW,'bid':100,'ask':100,'mid':100}]
            f=self.host.model_decision('model',Path(td),history,987654321)
            self.assertEqual(self.host.model_timeout,60)
            prompt=self.host.model_args[self.host.model_args.index('--prompt')+1]
            self.assertNotIn('987654321',prompt);self.assertTrue(f['position_neutral']);self.assertEqual(f['model_id'],MODEL_ID)
            self.assertTrue(any(e[0]=='CANDIDATE' for e in self.capture.events))
            self.assertTrue((Path(td)/'decision-schema-v2.json').exists())
            self.assertFalse((Path(td)/'decision-schema.json').exists())
    def test_hash_mismatch_fails_before_import(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'base.py';p.write_text('raise RuntimeError("should never execute")');p.chmod(0o600)
            with self.assertRaisesRegex(CostError,'HASH_MISMATCH'):adapter.load_pinned_driver(p)

if __name__=='__main__':unittest.main(verbosity=2)
