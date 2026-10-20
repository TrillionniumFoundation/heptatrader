import copy,json,tempfile,time,unittest
from pathlib import Path
from laya_cell.integrity import ContractError,digest,sha_file
from laya_cell.current100k_forward import validate_value_candidate,feature_rows,PLAN_SCHEMA,CANDIDATE_SCHEMA
from laya_cell.current100k_forward_worker import validate_runtime
from historical_training.current100k import BEHAVIOR

class ValueScopeTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
  head=self.root/'value-head.safetensors';head.write_bytes(b'synthetic-test-only')
  self.c={'schema':CANDIDATE_SCHEMA,'training_scope':BEHAVIOR,'pnl_reward_used':True,
   'reward_revalidated_before_and_after':True,'pretrained_backbone_unchanged':True,
   'scorer_or_actor_updated':False,'on_policy_policy_gradient':False,'automatic_selection':False,
   'canonical_owner_accepted':False,'broker_orders_sent':0,'scale_usd':100,
   'status':'UNVALIDATED_BEHAVIOR_VALUE_ONLY','rows':2,'row_ids':['a','b'],'head_sha256':sha_file(head)}
 def write(self,c=None):
  c=copy.deepcopy(self.c if c is None else c);c['candidate_id']=digest({k:v for k,v in c.items() if k!='candidate_id'})
  (self.root/'candidate.json').write_text(json.dumps(c));return c
 def test_valid_current_scope_has_no_actor_selection(self):
  self.write();c=validate_value_candidate(self.root);self.assertFalse(c['scorer_or_actor_updated'])
 def test_every_actor_or_authority_claim_refused(self):
  for k in ['scorer_or_actor_updated','on_policy_policy_gradient','automatic_selection','canonical_owner_accepted']:
   c=copy.deepcopy(self.c);c[k]=True;self.write(c)
   with self.assertRaises(ContractError):validate_value_candidate(self.root)
 def test_wrong_old_behavior_refused(self):
  c=copy.deepcopy(self.c);c['training_scope']='INDEPENDENT_BEHAVIOR_BOUND_VALUE_HEAD';self.write(c)
  with self.assertRaises(ContractError):validate_value_candidate(self.root)
 def test_tensor_substitution_refused(self):
  self.write();(self.root/'value-head.safetensors').write_bytes(b'changed')
  with self.assertRaises(ContractError):validate_value_candidate(self.root)
 def test_unrevalidated_or_changed_backbone_refused(self):
  for k in ['reward_revalidated_before_and_after','pretrained_backbone_unchanged','pnl_reward_used']:
   c=copy.deepcopy(self.c);c[k]=False;self.write(c)
   with self.assertRaises(ContractError):validate_value_candidate(self.root)
 def test_duplicate_rows_and_fake_count_refused(self):
  for update in [{'row_ids':['a','a']},{'rows':3},{'rows':True},{'rows':0},{'broker_orders_sent':1}]:
   self.write({**self.c,**update})
   with self.assertRaises(ContractError):validate_value_candidate(self.root)
 def test_feature_text_is_current100k_not_fixed_25k(self):
  plan={'schema':PLAN_SCHEMA,'behavior_scope':{'behavior_profile':BEHAVIOR,'reference_notional_usd':100000,'behavior_model_id':'original'}}
  request={'instrument':'EUR.USD','model_id':'original','prompt':'original input','position_at_request':0}
  rows=feature_rows(plan,request)
  self.assertEqual(len(rows),2)
  for row in rows:
   self.assertIn('100000 USD',row['feature_text']);self.assertNotIn('25000 EUR',row['feature_text'])
  self.assertIn('BUY',rows[0]['feature_text']);self.assertIn('SELL',rows[1]['feature_text'])
 def test_different_model_or_plan_cannot_supply_features(self):
  plan={'schema':PLAN_SCHEMA,'behavior_scope':{'behavior_profile':BEHAVIOR,'reference_notional_usd':100000,'behavior_model_id':'original'}}
  for request in [{'model_id':'other'},{'model_id':None}]:
   with self.assertRaises(ContractError):feature_rows(plan,request)
  plan['schema']='old25k'
  with self.assertRaises(ContractError):feature_rows(plan,{'model_id':'original'})
 def test_old_schema_cannot_reinterpret_new_head(self):
  from laya_cell.financial_forward_predict import pin
  from laya_cell.financial_forward_protocol import RULE
  c=self.write();now=int(time.time()*1000)
  p={'candidate_id':c['candidate_id'],'candidate_directory':str(self.root),'candidate_manifest_sha256':sha_file(self.root/'candidate.json'),
     'candidate_created_at_ms':1,'registered_at_ms':2,'input_start_ms':3,'input_end_ms':3+RULE['window_ms'],
     'outcome_end_ms':3+RULE['window_ms']+RULE['closure_grace_ms'],'rule':dict(RULE),'trial':1,'synthetic':False,
     'training_mean_usd':0.,'period_sha256':'p','runtime_sha256':{}}
  p['plan_id']=digest(p)
  with self.assertRaisesRegex(ContractError,'BEHAVIOR_MISMATCH'):pin({'period_sha256':'p'},p)

class RuntimePinTests(unittest.TestCase):
 def test_changed_source_or_period_cannot_run_worker(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);period=root/'period';period.write_text('{}');src=root/'module.py';src.write_text('# pinned')
   inv={'module.py':sha_file(src)}
   c={'schema':'heptatrader.current100k-forward-worker-config.v1','automatic_trading':False,'actor_update':False,
      'period_path':str(period),'period_sha256':sha_file(period),'worker_source_root':str(root),
      'worker_runtime_sha256':inv,'worker_runtime_inventory_digest':digest(inv)}
   validate_runtime(c);src.write_text('# changed')
   with self.assertRaisesRegex(ContractError,'RUNTIME_CHANGED'):validate_runtime(c)


class BoundPlanEvidenceReuseTests(unittest.TestCase):
 def setUp(self):
  import hashlib
  from historical_training.current100k import to_row
  from laya_cell.financial_forward_protocol import RULE
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.root=Path(self.tmp.name);self.source=self.root/'requests.jsonl'
  self.period='1'*64;self.manifest='2'*64;requests=[];lines=[]
  for i in range(2):
   prompt='synthetic fixed input '+str(i)
   request={'request_id':'request-'+str(i),'model_id':'test-model','instrument':'EUR.USD',
    'period_sha256':self.period,'position_at_request':0,'started_at_ms':1000+i,
    'prompt':prompt,'prompt_sha256':hashlib.sha256(prompt.encode()).hexdigest()}
   requests.append(request);lines.append((json.dumps({'kind':'REQUEST','data':request},sort_keys=True,separators=(',',':'))+'\n').encode())
  self.source.write_bytes(b''.join(lines));self.source.chmod(0o600);self.rows=[]
  for i,request in enumerate(requests):
   prefix=b''.join(lines[:i+1]);witness={'path':str(self.source),'bytes':len(prefix),'sha256':hashlib.sha256(prefix).hexdigest()}
   command='open-'+str(i)
   item={'request':request,'request_sha256':digest(request),'producer_manifest_sha256':self.manifest,
    'source_record_sha256':hashlib.sha256(lines[i]).hexdigest(),'source_prefix':witness}
   binding={'request_id':request['request_id'],'request_sha256':digest(request),'decision_sha256':'decision-'+str(i),
    'command':{'command_id':command,'kind':'OPEN','instrument':'EUR.USD','position_before_send':0,
     'quantity_mode':'BASE_NOTIONAL_TARGET','target_notional_usd':100000,'side':'BUY','order_quantity':'100000'}}
   executions=[{'execution_id':'entry-'+str(i),'command_id':command,'side':'BUY','quantity':'100000','price':'1','commission':'1','commission_currency':'USD'},
    {'execution_id':'exit-'+str(i),'command_id':'close-'+str(i),'side':'SELL','quantity':'100000','price':'1.00001','commission':'1','commission_currency':'USD'}]
   reward={'episode_id':'episode-'+str(i),'instrument':'EUR.USD','currency':'USD','learning_eligible':True,
    'exact_input_bound':True,'fees_complete':True,'settlement_complete':True,'policy_gradient_allowed':False,
    'laya_on_policy':False,'learning_exclusion_reasons':[],'behavior_model_id':'test-model','open_command_id':command,
    'input_id':digest(request),'forecast_id':'decision-'+str(i),'executions':executions,'realized_gross_usd':'1',
    'actual_commissions_usd':'2','realized_net_usd':'-1','input_attribution':{'producer_manifest_sha256':self.manifest,
     'request_id':request['request_id'],'prompt_sha256':request['prompt_sha256'],'source_prefixes':[witness]}}
   reward['reward_id']=digest(reward);self.rows.append(to_row(reward,item,binding,self.manifest,self.period))
  self.dataset=self.root/'dataset.json'
  self.plan={'schema':PLAN_SCHEMA,'candidate_id':'candidate','candidate_created_at_ms':1,'registered_at_ms':2,
   'input_start_ms':3,'input_end_ms':3+RULE['window_ms'],'outcome_end_ms':3+RULE['window_ms']+RULE['closure_grace_ms'],
   'rule':copy.deepcopy(RULE),'trial':1,'synthetic':False,'period_sha256':self.period,'head_tensor_sha256':'3'*64,
   'training_dataset_path':str(self.dataset),'producer_registration':{'sha256':self.manifest},
   'behavior_scope':{'behavior_profile':BEHAVIOR,'reference_notional_usd':100000,'quantity':None,
    'producer_manifest_sha256':self.manifest,'instrument':'EUR.USD','currency':'USD','behavior_model_id':'test-model'}}
  self.rebind_dataset()
 def rebind_dataset(self):
  self.dataset.write_text(json.dumps({'period_sha256':self.period,'producer_manifest_sha256':self.manifest,'rows':self.rows}));self.dataset.chmod(0o600)
  self.candidate={'candidate_id':'candidate','created_at_ms':1,'head_sha256':'3'*64,'dataset_sha256':sha_file(self.dataset),'row_ids':[r['row_id'] for r in self.rows]}
  self.plan.update(training_dataset_sha256=self.candidate['dataset_sha256'],training_reward_ids=[r['reward_id'] for r in self.rows],
   training_input_ids=[r['input_id'] for r in self.rows],training_mean_usd=sum(float(r['net_usd']) for r in self.rows)/len(self.rows))
  self.plan['plan_id']=digest({k:v for k,v in self.plan.items() if k!='plan_id'})
 def test_shared_source_read_once(self):
  from unittest import mock
  import hepta_decision_attribution as attribution
  from laya_cell.current100k_forward import validate_bound_plan
  with mock.patch.object(attribution,'read_private',wraps=attribution.read_private) as reader:
   self.assertEqual(validate_bound_plan(self.plan,self.candidate),self.plan)
  self.assertEqual(reader.call_count,1)
 def test_source_mutation_rejected(self):
  from laya_cell.current100k_forward import validate_bound_plan
  self.source.write_bytes(self.source.read_bytes().replace(b'synthetic',b'corrupted',1))
  with self.assertRaises(ContractError):validate_bound_plan(self.plan,self.candidate)
 def test_economic_recomputation_is_preserved(self):
  from laya_cell.current100k_forward import validate_bound_plan
  self.rows[0]['net_usd']='999';self.rebind_dataset()
  with self.assertRaisesRegex(ContractError,'CURRENT100K_FORWARD_REWARD_RECOMPUTATION'):validate_bound_plan(self.plan,self.candidate)
