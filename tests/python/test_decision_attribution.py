"""Synthetic provenance fixtures only. No native, model-provider or order calls."""
from __future__ import annotations
import copy,hashlib,json,os,sys,tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))
sys.path.insert(0,str(Path(__file__).resolve().parent))
import hepta_decision_attribution as a

class Host:
 INSTRUMENT='EUR.USD';MODEL='test-model'
 def __init__(self):self.calls=[];self.writes=[];self.respond={'action':'BUY','confidence':.62,'rationale':'unit fixture'}
 def run(self,argv,*args,**kw):
  self.calls.append(copy.deepcopy(argv));return SimpleNamespace(returncode=0,stdout=json.dumps({'ok':True,'outputs':[{'text':json.dumps(self.respond)}]}))
 def model_decision(self,openclaw,state_dir,history,position):
  prompt='EXACT TEST INPUT '+json.dumps(history,separators=(',',':'))
  out=self.run([openclaw,'infer','model','run','--local','--model',self.MODEL,'--thinking','low','--prompt',prompt,'--json'],150)
  return json.loads(json.loads(out.stdout)['outputs'][0]['text'])
 def atomic_json(self,path,state):self.writes.append(copy.deepcopy(state))
 def prepare_send(self,state,path,kind='OPEN'):
  if state.get('pending') is not None:raise ValueError('ORIGINAL_PENDING')
  state['pending']={'command_id':'original-'+str(len(self.writes)),'kind':kind,'instrument':self.INSTRUMENT,
     'side':state['last_decision']['action'] if kind=='OPEN' else 'SELL','created_at_ms':int(time.time()*1000),'send_state':'PREPARED','position_before_send':0}
  self.atomic_json(path,state);state['pending']['send_state']='POSSIBLY_SENT';self.atomic_json(path,state)
  self.calls.append('submit');state['pending']['send_state']='ACKNOWLEDGED';self.atomic_json(path,state)

class ProvenanceTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
  self.manifest={'schema':'heptatrader.attribution-producer.v1','period_sha256':'1'*64,'instrument':'EUR.USD',
      'execution_domain':'PAPER','journal_directory':str(self.root/'journal')}
  self.path=self.root/'manifest.json';self.path.write_text(a.canonical(self.manifest)+'\n');self.path.chmod(0o600)
  self.reg={'manifest':str(self.path),'sha256':hashlib.sha256(self.path.read_bytes()).hexdigest()}
  self.host=Host();self.journal=a.Journal(self.root/'journal',self.manifest,self.reg['sha256']);self.cap=a.Capture(self.journal,self.host,self.manifest)
  self.close=self.cap.install();self.addCleanup(self.close)
  self.history=[{'observed_at_ms':100,'bid':1.1,'ask':1.1001,'mid':1.10005}]
 def decision(self):return self.host.model_decision('model',self.root,self.history,0)
 def trade(self,decision=None):
  state={'position':0,'pending':None,'last_decision':self.decision() if decision is None else decision}
  self.host.prepare_send(state,self.root/'state');return state
 def read(self):
  self.journal.close();return a.verified_bindings(self.reg,'EUR.USD','1'*64)
 def records(self):
  self.journal.close();return [a.strict(line) for p in self.root.joinpath('journal').glob('*.jsonl') for line in p.read_bytes().splitlines()]
 def test_real_object_exact_chain_returns_one_binding(self):
  s=self.trade();b,e=self.read();self.assertEqual(e,[]);self.assertEqual(list(b),[s['pending']['command_id']]);self.assertEqual(b[s['pending']['command_id']]['model_id'],'test-model')
 def test_plaintext_input_prompt_and_response_actually_retained(self):
  self.trade();rs=self.records();request=next(r['data'] for r in rs if r['kind']=='REQUEST')
  self.assertEqual(request['history_snapshot'],self.history)
  self.assertEqual(request['prompt'],self.host.calls[0][self.host.calls[0].index('--prompt')+1])
  self.assertTrue(any(r['kind']=='RESPONSE' and 'outputs' in r['data']['stdout'] for r in rs))
 def test_decision_not_augmented_or_replaced(self):
  d=self.decision();self.assertEqual(d,self.host.respond);self.assertEqual(set(d),{'action','confidence','rationale'})
 def test_original_history_and_model_call_unchanged(self):
  before=copy.deepcopy(self.history);self.trade();self.assertEqual(self.history,before);self.assertEqual(self.host.calls[-1],'submit')
 def test_equal_clone_never_claims_same_input(self):
  d=self.decision();self.trade(copy.deepcopy(d));self.assertEqual(self.read()[0],{})
 def test_changed_decision_never_claims_original_input(self):
  d=self.decision();d['confidence']=.99;self.trade(d);self.assertEqual(self.read()[0],{})
 def test_history_time_proximity_cannot_create_binding(self):
  self.trade(self.host.respond.copy());self.assertEqual(self.read()[0],{})
 def test_pending_command_still_not_replaced(self):
  state={'pending':{'command_id':'old'},'last_decision':self.decision()};old=copy.deepcopy(state)
  with self.assertRaisesRegex(ValueError,'ORIGINAL_PENDING'):self.host.prepare_send(state,self.root)
  self.assertEqual(state,old)
 def test_writer_exception_does_not_block_trade_or_risk_close(self):
  with patch.object(self.journal,'emit',side_effect=OSError('full')):
   s=self.trade();self.assertEqual(s['pending']['send_state'],'ACKNOWLEDGED')
   state={'pending':None,'last_decision':self.host.respond};self.host.prepare_send(state,self.root,'CLOSE')
  self.assertEqual(self.host.calls.count('submit'),2)
 def test_close_has_no_new_open_binding(self):
  d=self.decision();s={'pending':None,'last_decision':d};self.host.prepare_send(s,self.root,'CLOSE');self.assertEqual(self.read()[0],{})
 def test_dropped_record_disqualifies_producer(self):
  self.trade();self.journal.dropped=1;b,e=self.read();self.assertEqual(b,{});self.assertIn('DROPPED',e[0]['reason'])
 def test_manifest_replacement_refused(self):
  self.trade();self.path.write_text('{}');self.journal.close()
  with self.assertRaisesRegex(ValueError,'MANIFEST_CHANGED'):a.verified_bindings(self.reg,'EUR.USD','1'*64)
 def test_cross_period_refused(self):
  self.trade();self.journal.close()
  with self.assertRaisesRegex(ValueError,'PRODUCER_SCOPE'):a.verified_bindings(self.reg,'EUR.USD','2'*64)
 def test_cross_instrument_refused(self):
  self.trade();self.journal.close()
  with self.assertRaisesRegex(ValueError,'PRODUCER_SCOPE'):a.verified_bindings(self.reg,'BTC.USD','1'*64)
 def test_hash_tampering_refused(self):
  self.trade();self.journal.close();p=next((self.root/'journal').glob('*.jsonl'));p.write_bytes(p.read_bytes().replace(b'test-model',b'evil-model'))
  with self.assertRaisesRegex(ValueError,'ATTRIBUTION_HASH'):self.read()
 def test_partial_last_row_never_parsed_as_fact(self):
  s=self.trade();self.journal.close();p=next((self.root/'journal').glob('*.jsonl'))
  with p.open('ab') as f:f.write(b'{"broken')
  self.assertIn(s['pending']['command_id'],self.read()[0])
 def test_private_permissions_required(self):
  self.trade();self.journal.close();p=next((self.root/'journal').glob('*.jsonl'));p.chmod(0o644)
  with self.assertRaisesRegex(ValueError,'FILE_SCOPE'):self.read()
 def test_symlink_manifest_refused(self):
  self.trade();self.journal.close();p=self.root/'link';p.symlink_to(self.path)
  with self.assertRaises(OSError):a.verified_bindings({**self.reg,'manifest':str(p)},'EUR.USD','1'*64)
 def test_raw_tokens_or_environment_not_captured(self):
  with patch.dict(os.environ,{'MODEL_API_KEY':'SECRET_TEST_VALUE'}):self.trade()
  self.assertNotIn('SECRET_TEST_VALUE',a.canonical(self.records()))
 def test_two_equal_model_responses_keep_distinct_input_ids(self):
  one=self.decision();s=self.trade(one);two=self.decision();t=self.trade(two);b,e=self.read()
  self.assertEqual(e,[]);self.assertNotEqual(b[s['pending']['command_id']]['input_id'],b[t['pending']['command_id']]['input_id'])
 def test_two_inference_calls_are_not_misrepresented_as_single(self):
  self.close();host=Host();original=host.model_decision
  def twice(*x):original(*x);return original(*x)
  host.model_decision=twice;j=a.Journal(self.root/'second',self.manifest,self.reg['sha256']);cap=a.Capture(j,host,self.manifest);close=cap.install();self.addCleanup(close)
  d=host.model_decision('model',self.root,self.history,0);s={'pending':None,'last_decision':d};host.prepare_send(s,self.root)
  j.close();rs=[json.loads(line) for p in (self.root/'second').glob('*.jsonl') for line in p.read_bytes().splitlines()]
  self.assertFalse(any(r['kind']=='COMMAND_BINDING' for r in rs))
 def test_original_state_writes_unchanged(self):
  self.trade();self.assertEqual([s['pending']['send_state'] for s in self.host.writes],['PREPARED','POSSIBLY_SENT','ACKNOWLEDGED'])
  self.assertTrue(all('input_id' not in s['pending'] for s in self.host.writes))
 def test_negative_or_weak_confidence_not_changed(self):
  self.host.respond={'action':'HOLD','confidence':.01,'rationale':'weak'};self.assertEqual(self.decision(),self.host.respond)

class ExactRewardTests(unittest.TestCase):
 setUp=ProvenanceTests.setUp
 decision=ProvenanceTests.decision
 trade=ProvenanceTests.trade
 read=ProvenanceTests.read
 def economic_report(self,command):
  return {'fees_complete':True,'settlement_pending_command_ids':[],
   'executions':[{'execution_id':'e1','command_id':command,'side':'BUY','quantity':'1','price':'100','commission':'1','commission_currency':'USD'},
    {'execution_id':'e2','command_id':'close','side':'SELL','quantity':'1','price':'105','commission':'1','commission_currency':'USD'}]}
 def test_exact_graph_joins_actual_fee_value_target_without_actor_credit(self):
  sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'research/neuron_learning'))
  from laya_cell.resident_rewards import source_bindings,financial_episodes
  state=self.trade();self.journal.close()
  config={'period_sha256':'1'*64,'decision_attribution_registrations':{'EUR.USD':[self.reg]}}
  bindings,errors=source_bindings(config,'EUR.USD',int(time.time()*1000))
  self.assertEqual(errors,[])
  rs,_=financial_episodes(self.economic_report(state['pending']['command_id']),bindings,'period','EUR.USD')
  self.assertTrue(rs[0]['learning_eligible']);self.assertEqual(rs[0]['realized_net_usd'],'3')
  self.assertFalse(rs[0]['policy_gradient_allowed']);self.assertFalse(rs[0]['laya_on_policy'])
  self.assertEqual(rs[0]['input_attribution']['request_id'],next(iter(bindings.values()))['request_id'])
 def test_input_for_other_direction_never_receives_this_command_credit(self):
  sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'research/neuron_learning'))
  from laya_cell.resident_rewards import financial_episodes
  state=self.trade();bindings,_=self.read();r=self.economic_report(state['pending']['command_id'])
  r['executions'][0]['side']='SELL';r['executions'][1]['side']='BUY'
  rs,_=financial_episodes(r,bindings,'period','EUR.USD');self.assertFalse(rs[0]['learning_eligible'])
  self.assertIn('ORIGINAL_COMMAND_INPUT_SUBJECT_CONFLICT',rs[0]['learning_exclusion_reasons'])

# Re-run the real pinned-base BTC suite with provenance layered OUTSIDE the
# existing economics adapter. This tests unchanged quote/risk/fee/exit contracts.
import test_btc_entry_economics as btc_tests
class ObservedBtcInvariantTests(btc_tests.DriverIntegrationTests):
 def setUp(self):
  super().setUp()
  self.observer_temp=tempfile.TemporaryDirectory();self.addCleanup(self.observer_temp.cleanup)
  manifest={'instrument':'BTC.USD','period_sha256':'1'*64}
  journal=a.Journal(Path(self.observer_temp.name)/'journal',manifest,'2'*64)
  close=a.Capture(journal,self.m,manifest).install();self.addCleanup(close)
 def tearDown(self):
  # Cleanup runs after tearDown; restore provenance first to avoid reattaching
  # underlying economics on an already torn-down fixture.
  self.doCleanups();super().tearDown()

if __name__=='__main__':unittest.main(verbosity=2)
