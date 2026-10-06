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
        f=forecast();raw={k:f[k] for k in adapter.CANDIDATE_SCHEMA['required']}
        return SimpleNamespace(returncode=0,stdout=json.dumps({'ok':True,'outputs':[{'text':json.dumps(raw)}]}))

class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.host=Host();self.capture=FakeCapture();self.close=adapter.attach(self.host,Path('/fixture-not-real'),capture_factory=lambda *a,**k:self.capture)
        self.state={'pending':None,'position':0.,'last_decision':forecast(),'entry_price':None,'halt_new_entries':False}
    def tearDown(self):self.close()
    def open(self):self.host.prepare_send(self.state,Path('/not-written'),'b','s','t','o','OPEN',self.host.q,'BUY')
    def trusted_fixture(self):return contextlib.nullcontext()
    def test_cost_aware_direction_bypasses_legacy_confidence_only(self):
        low=forecast();low["confidence"]=0.01;validate_forecast(low)
        self.assertTrue(self.host.entry_candidate_allowed(low))
        hold=forecast(action="HOLD",move="0");hold["confidence"]=1.0;validate_forecast(hold)
        self.assertFalse(self.host.entry_candidate_allowed(hold))

    def test_low_confidence_direction_still_reaches_cost_gate(self):
        low=forecast();low["confidence"]=0.01;validate_forecast(low)
        self.state["last_decision"]=low
        self.assertTrue(self.host.entry_candidate_allowed(low))
        self.open()
        self.assertTrue(self.state["cost_last_admission"]["allowed"])
        self.assertIn("submit",self.host.calls)

    def test_missing_local_calibration_does_not_block_remote_cost_gate(self):
        # attach() receives a nonexistent compatibility path in setUp. Resident
        # remote-model admission must never read or require it.
        self.open()
        self.assertEqual(self.host.calls,['prepare-crypto-open','market.get_quote','submit'])
        self.assertTrue(self.state['cost_last_admission']['allowed'])
        self.assertFalse(self.state['cost_last_admission']['local_training_artifact_used'])
    def test_small_edge_no_prepare(self):
        self.state['last_decision']=forecast(move='20')
        with self.trusted_fixture(),self.assertRaises(self.host.DriverError):self.open()
        self.assertFalse(self.host.calls)
    def test_positive_fixture_rechecks_before_submit(self):
        with self.trusted_fixture():self.open()
        self.assertEqual(self.host.calls,['prepare-crypto-open','market.get_quote','submit'])
        self.assertEqual(self.state['pending']['send_state'],'ACKNOWLEDGED')
        self.assertIn('cost_forecast',self.host.writes[1]['pending'])
        self.assertEqual(self.host.writes[2]['pending']['send_state'],'POSSIBLY_SENT')
    def test_price_move_after_prepare_blocks_submit(self):
        self.host.change_after_prepare=lambda:setattr(self.host,'q',quote(mid='100.7'))
        with self.trusted_fixture():self.open()
        self.assertNotIn('submit',self.host.calls)
        self.assertEqual(self.state['pending']['command_id'],'original-command')
        self.assertEqual(self.state['pending']['send_state'],'DEFINITELY_REJECTED')
    def test_stale_after_prepare_no_submit(self):
        self.host.change_after_prepare=lambda:setattr(self.host,'now',NOW+60001)
        with self.trusted_fixture():self.open()
        self.assertNotIn('submit',self.host.calls)
    def test_final_position_changed_no_submit(self):
        self.host.change_after_prepare=lambda:self.host.snap.update(position=1)
        with self.trusted_fixture():self.open()
        self.assertNotIn('submit',self.host.calls)
    def test_final_active_order_no_submit(self):
        self.host.change_after_prepare=lambda:self.host.snap.update(active=[99])
        with self.trusted_fixture():self.open()
        self.assertNotIn('submit',self.host.calls)
    def test_final_native_readiness_no_submit(self):
        self.host.change_after_prepare=lambda:self.host.snap['owner_health'].update(mutation_ready=False)
        with self.trusted_fixture():self.open()
        self.assertNotIn('submit',self.host.calls)
    def test_local_training_artifact_cannot_authorize_or_block_resident(self):
        # There is intentionally no load_calibration symbol in the resident adapter.
        self.assertFalse(hasattr(adapter,'load_calibration'))
        self.open()
        self.assertTrue(self.state['cost_last_admission']['allowed'])
        self.assertIsNone(self.state['cost_last_admission']['calibration_id'])
    def test_evidence_failure_blocks_entry(self):
        self.capture.failed=True
        with self.trusted_fixture(),self.assertRaises(self.host.DriverError):self.open()
        self.assertFalse(self.host.calls)
    def test_failed_evidence_does_not_block_close(self):
        self.capture.failed=True;self.state['position']=1
        self.host.prepare_send(self.state,Path('/not-written'),'b','s','t','o','CLOSE')
        self.assertEqual(self.host.calls,['prepare-crypto-close','submit'])
    def test_unknown_ack_keeps_original_command(self):
        self.host.submit_result='uncertain'
        with self.trusted_fixture():self.open()
        self.assertEqual(self.state['pending']['send_state'],'POSSIBLY_SENT')
        before=copy.deepcopy(self.state['pending'])
        with self.trusted_fixture(),self.assertRaises(self.host.DriverError):self.open()
        self.assertEqual(self.state['pending'],before);self.assertEqual(self.host.calls.count('submit'),1)
    def test_old_pending_never_replaced(self):
        self.state['pending']={'command_id':'order242-original','kind':'OPEN','send_state':'ACKNOWLEDGED'}
        old=copy.deepcopy(self.state)
        with self.assertRaises(self.host.DriverError):self.open()
        self.assertEqual(self.state,old)
    def test_halt_cannot_be_overridden_by_remote_cost_edge(self):
        self.state['halt_new_entries']=True
        with self.trusted_fixture(),self.assertRaises(self.host.DriverError):self.open()
    def test_cross_instrument_forecast_refused(self):
        self.state['last_decision']=forecast(instrument='EUR.USD')
        with self.trusted_fixture(),self.assertRaises(self.host.DriverError):self.open()
        self.assertFalse(self.host.calls)
    def test_legacy_exit_confidence_semantics_are_not_relaxed(self):
        self.state["position"]=1;self.state["entry_price"]=100
        low=forecast(action="SELL",move="-20");low["confidence"]=0.01;validate_forecast(low)
        self.assertEqual(self.host.should_close(self.state,1,quote(),low),(False,''))

    def test_inherited_position_retains_original_exit(self):
        self.state['position']=1;self.state['entry_price']=100
        self.assertEqual(self.host.should_close(self.state,1,quote(),forecast(action='SELL',move='-20')),(True,'MODEL_REVERSAL'))
    def test_risk_exit_independent_of_calibration_and_recorder(self):
        self.state['entry_price']=100;self.state['cost_entry_forecast']=forecast();self.capture.failed=True
        self.assertEqual(self.host.should_close(self.state,1,quote(mid='99'),None),(True,'MAX_ADVERSE_MOVE'))
    def test_managed_horizon_exit_without_calibration(self):
        self.host.now=NOW+300000;self.state.update(entry_price=100,cost_entry_forecast=forecast())
        self.assertEqual(self.host.should_close(self.state,1,quote(NOW+300000),None),(True,'FORECAST_HORIZON_EXPIRED'))
    def test_remote_forecast_entry_metadata_survives_settlement(self):
        self.state['pending']={'kind':'OPEN','command_id':'same','cost_forecast':forecast()}
        self.host.inspect_pending(self.state,Path('/not-written'),'b','s','t','o',{'position':1})
        self.assertEqual(self.state['cost_entry_forecast'],forecast())
    def test_close_settlement_starts_cooldown(self):
        self.state.update(position=1,cost_entry_forecast=forecast(),pending={'kind':'CLOSE','command_id':'same'})
        self.host.inspect_pending(self.state,Path('/not-written'),'b','s','t','o',{'position':0})
        self.assertEqual(self.state['cost_last_exit_ms'],NOW);self.assertNotIn('cost_entry_forecast',self.state)
    def test_position_neutral_model_schema(self):
        with tempfile.TemporaryDirectory() as td:
            history=[{'observed_at_ms':NOW,'bid':100,'ask':100,'mid':100}]
            f=self.host.model_decision('model',Path(td),history,987654321)
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
