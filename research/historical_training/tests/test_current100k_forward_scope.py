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


class ForwardScopeFixture:
 def scope_identifiers(self):return '1'*64,'2'*64
 def setUp(self):
  import hashlib
  from historical_training.current100k import to_row
  from laya_cell.financial_forward_protocol import RULE
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.root=Path(self.tmp.name);self.source=self.root/'requests.jsonl'
  self.period,self.manifest=self.scope_identifiers();requests=[];lines=[]
  id_prefix=getattr(self,'request_prefix','');model=getattr(self,'request_model','test-model')
  for i in range(2):
   prompt='synthetic fixed input '+str(i)
   request={'request_id':id_prefix+'request-'+str(i),'model_id':model,'instrument':'EUR.USD',
    'period_sha256':self.period,'position_at_request':0,'started_at_ms':1000+i,
    'prompt':prompt,'prompt_sha256':hashlib.sha256(prompt.encode()).hexdigest()}
   requests.append(request);lines.append((json.dumps({'kind':'REQUEST','data':request},sort_keys=True,separators=(',',':'))+'\n').encode())
  self.source.write_bytes(b''.join(lines));self.source.chmod(0o600);self.rows=[]
  for i,request in enumerate(requests):
   prefix=b''.join(lines[:i+1]);witness={'path':str(self.source),'bytes':len(prefix),'sha256':hashlib.sha256(prefix).hexdigest()}
   command=id_prefix+'open-'+str(i)
   item={'request':request,'request_sha256':digest(request),'producer_manifest_sha256':self.manifest,
    'source_record_sha256':hashlib.sha256(lines[i]).hexdigest(),'source_prefix':witness}
   binding={'request_id':request['request_id'],'request_sha256':digest(request),'decision_sha256':'decision-'+str(i),
    'command':{'command_id':command,'kind':'OPEN','instrument':'EUR.USD','position_before_send':0,
     'quantity_mode':'BASE_NOTIONAL_TARGET','target_notional_usd':100000,'side':'BUY','order_quantity':'100000'}}
   executions=[{'execution_id':id_prefix+'entry-'+str(i),'command_id':command,'side':'BUY','quantity':'100000','price':'1','commission':'1','commission_currency':'USD'},
    {'execution_id':id_prefix+'exit-'+str(i),'command_id':id_prefix+'close-'+str(i),'side':'SELL','quantity':'100000','price':'1.00001','commission':'1','commission_currency':'USD'}]
   reward={'episode_id':id_prefix+'episode-'+str(i),'instrument':'EUR.USD','currency':'USD','learning_eligible':True,
    'exact_input_bound':True,'fees_complete':True,'settlement_complete':True,'policy_gradient_allowed':False,
    'laya_on_policy':False,'learning_exclusion_reasons':[],'behavior_model_id':model,'open_command_id':command,
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
    'producer_manifest_sha256':self.manifest,'instrument':'EUR.USD','currency':'USD','behavior_model_id':model}}
  self.rebind_dataset()
 def rebind_dataset(self):
  self.dataset.write_text(json.dumps({'period_sha256':self.period,'producer_manifest_sha256':self.manifest,'rows':self.rows}));self.dataset.chmod(0o600)
  self.candidate={'candidate_id':'candidate','created_at_ms':1,'head_sha256':'3'*64,'dataset_sha256':sha_file(self.dataset),'row_ids':[r['row_id'] for r in self.rows]}
  self.plan.update(training_dataset_sha256=self.candidate['dataset_sha256'],training_reward_ids=[r['reward_id'] for r in self.rows],
   training_input_ids=[r['input_id'] for r in self.rows],training_mean_usd=sum(float(r['net_usd']) for r in self.rows)/len(self.rows))
  self.plan['plan_id']=digest({k:v for k,v in self.plan.items() if k!='plan_id'})

class BoundPlanEvidenceReuseTests(ForwardScopeFixture,unittest.TestCase):
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


class NewProducerScopeRegistrationTests(ForwardScopeFixture,unittest.TestCase):
 def scope_identifiers(self):
  period=self.root/'period.json';period.write_text(json.dumps({'execution_domain':'PAPER'}));period.chmod(0o600)
  manifest=self.root/'producer.json'
  manifest.write_text(json.dumps({'schema':'heptatrader.attribution-producer.v1','behavior_profile':BEHAVIOR,
   'instrument':'EUR.USD','execution_domain':'PAPER','period_sha256':sha_file(period),
   'journal_directory':str(self.root/'journal')}));manifest.chmod(0o600)
  return sha_file(period),sha_file(manifest)
 def complete_candidate(self):
  from historical_training.current100k_value_trial import selected_rows
  data={'schema':'heptatrader.current100k-value-dataset.v1','period_sha256':self.period,
   'producer_manifest_sha256':self.manifest,'behavior_profile':BEHAVIOR,'rows':self.rows,
   'synthetic_rewards':0,'old25k_rewards':0,'trade_authority':False}
  data['fingerprint']=digest(data);selected_rows(data,data)
  self.dataset.write_text(json.dumps(data));self.dataset.chmod(0o600)
  directory=self.root/'candidate';directory.mkdir()
  head=directory/'value-head.safetensors';head.write_bytes(b'synthetic-test-only');head.chmod(0o600)
  candidate={'schema':CANDIDATE_SCHEMA,'training_scope':BEHAVIOR,'pnl_reward_used':True,
   'reward_revalidated_before_and_after':True,'pretrained_backbone_unchanged':True,
   'scorer_or_actor_updated':False,'on_policy_policy_gradient':False,'automatic_selection':False,
   'canonical_owner_accepted':False,'broker_orders_sent':0,'scale_usd':100,
   'status':'UNVALIDATED_BEHAVIOR_VALUE_ONLY','rows':len(self.rows),'row_ids':[r['row_id'] for r in self.rows],
   'head_sha256':sha_file(head),'dataset_sha256':sha_file(self.dataset),'base_id':'fixture-base','created_at_ms':1}
  candidate['candidate_id']=digest(candidate)
  (directory/'candidate.json').write_text(json.dumps(candidate));(directory/'candidate.json').chmod(0o600)
  self.plan.update(candidate_id=candidate['candidate_id'],candidate_directory=str(directory),
   candidate_manifest_sha256=sha_file(directory/'candidate.json'),head_tensor_sha256=candidate['head_sha256'],
   training_dataset_sha256=candidate['dataset_sha256'])
  self.plan['plan_id']=digest({k:v for k,v in self.plan.items() if k!='plan_id'})
  return directory,candidate
 def setUp(self):
  self.request_prefix='prior-';ForwardScopeFixture.setUp(self)
  prior_directory,prior_candidate=self.complete_candidate()
  self.prior={'directory':prior_directory,'candidate':prior_candidate,'plan':copy.deepcopy(self.plan),
              'dataset':self.dataset,'manifest':self.root/'producer.json'}
  self.prepare_new()
 def prepare_new(self,*,prefix='new-',model='test-model'):
  self.request_prefix=prefix;self.request_model=model;ForwardScopeFixture.setUp(self)
  self.directory,self.candidate=self.complete_candidate()
  self.history=self.root/'history';self.history.mkdir()
  self.prior_path=self.history/'prior'/'plan.json';self.prior_path.parent.mkdir()
  self.prior_path.write_text(json.dumps(self.prior['plan']));self.prior_path.chmod(0o600)
  self.config={'automatic_trading':False,'actor_update':False,'period_path':str(self.root/'period.json'),
   'period_sha256':self.period,'producer_registration':{'manifest':str(self.root/'producer.json'),'sha256':self.manifest},
   'forward_root':str(self.root/'forward'),'witness_trust_digest':'4'*64,'worker_runtime_inventory_digest':'5'*64}
 def register(self,**changes):
  from unittest.mock import patch
  from laya_cell.current100k_forward import register,NEW_PRODUCER_SCOPE
  arguments={'registration_scope':NEW_PRODUCER_SCOPE,'prior_plan_path':self.prior_path,**changes}
  # Only the existing signer is substituted: validation, source-prefix replay,
  # exact execution/fee arithmetic and immutable plan publication are real.
  with patch('laya_cell.financial_forward_witness.sign_record',return_value={'fixture_signature':True}) as signer:
   result=register(self.config,self.directory,self.dataset,self.prior['directory']/'candidate.json',self.history,**arguments)
  signer.assert_called_once();self.assertEqual(signer.call_args.args[1],'registrar')
  return result
 def test_new_producer_uses_fresh_rows_and_preserves_trial_budget_and_old_bytes(self):
  from laya_cell.financial_forward_protocol import RULE
  old=self.prior_path.read_bytes();plan=self.register()
  self.assertEqual(plan['registration_scope'],'NEW_PRODUCER_SCOPE');self.assertEqual(plan['trial'],2)
  self.assertEqual(plan['previous_rows_reused_in_refit'],0);self.assertEqual(plan['prior_plan_id'],self.prior['plan']['plan_id'])
  self.assertEqual(plan['prior_producer_manifest_sha256'],self.prior['plan']['behavior_scope']['producer_manifest_sha256'])
  self.assertEqual(plan['old_trial_ids_retained'],[self.prior['plan']['plan_id']])
  self.assertEqual(plan['rule'],RULE);self.assertFalse(plan['automatic_effect_authority'])
  self.assertEqual(self.prior_path.read_bytes(),old)
 def test_default_path_still_requires_original_rows(self):
  with self.assertRaisesRegex(ContractError,'PRIOR_ELEVEN_ROWS_NOT_PRESERVED'):
   self.register(registration_scope='SAME_PRODUCER_REFIT')
  self.assertFalse(Path(self.config['forward_root']).exists())
 def test_same_scope_refit_path_remains_available(self):
  self.directory=self.prior['directory'];self.dataset=self.prior['dataset']
  self.config['producer_registration']={'manifest':str(self.prior['manifest']),'sha256':sha_file(self.prior['manifest'])}
  plan=self.register(registration_scope='SAME_PRODUCER_REFIT')
  self.assertEqual(plan['previous_rows_reused_in_refit'],2)
  self.assertEqual(plan['fit_relation'],'FRESH_HEAD_REFIT_NOT_INCREMENTAL_ACTOR_UPDATES')
 def test_same_producer_cannot_bypass_refit_preservation(self):
  self.directory=self.prior['directory'];self.dataset=self.prior['dataset']
  self.config['producer_registration']={'manifest':str(self.prior['manifest']),'sha256':sha_file(self.prior['manifest'])}
  with self.assertRaisesRegex(ContractError,'REQUIRES_CHANGED_REGISTERED_PRODUCER'):self.register()
 def test_changed_model_is_not_a_producer_only_transition(self):
  self.prepare_new(model='different-behavior-model')
  with self.assertRaisesRegex(ContractError,'BEHAVIOR_OR_PERIOD_CHANGED'):self.register()
 def test_period_change_requires_a_separate_scoped_protocol(self):
  self.config['period_sha256']='9'*64
  with self.assertRaisesRegex(ContractError,'BEHAVIOR_OR_PERIOD_CHANGED'):self.register()
 def test_registered_producer_manifest_bytes_are_revalidated(self):
  path=Path(self.config['producer_registration']['manifest']);path.write_text(path.read_text()+'\n')
  with self.assertRaisesRegex(ValueError,'REGISTERED_PRODUCER_CHANGED'):self.register()
 def test_same_original_inputs_cannot_be_relabelled_with_new_producer(self):
  self.prepare_new(prefix='prior-')
  with self.assertRaisesRegex(ContractError,'HISTORICAL_TRAINING_OVERLAP'):self.register()
 def test_actor_authority_is_refused_before_publication(self):
  self.config['actor_update']=True
  with self.assertRaisesRegex(ContractError,'REQUIRES_PRIOR_PLAN_NO_ACTOR'):self.register()
 def test_missing_prior_plan_is_refused(self):
  with self.assertRaisesRegex(ContractError,'REQUIRES_PRIOR_PLAN_NO_ACTOR'):self.register(prior_plan_path=None)
 def test_duplicate_and_missing_trial_numbers_are_refused(self):
  for trial in (1,3):
   with self.subTest(trial=trial):
    other=copy.deepcopy(self.prior['plan']);other['trial']=trial;other['fixture_history']='other'
    other['plan_id']=digest({k:v for k,v in other.items() if k!='plan_id'})
    path=self.history/'other'/'plan.json';path.parent.mkdir(exist_ok=True);path.write_text(json.dumps(other));path.chmod(0o600)
    with self.assertRaisesRegex(ContractError,'COMPLETE_TRIAL_HISTORY_REQUIRED'):self.register()
 def test_prior_must_be_latest_retained_trial(self):
  other=copy.deepcopy(self.prior['plan']);other['trial']=2
  other['plan_id']=digest({k:v for k,v in other.items() if k!='plan_id'})
  path=self.history/'later'/'plan.json';path.parent.mkdir();path.write_text(json.dumps(other));path.chmod(0o600)
  with self.assertRaisesRegex(ContractError,'COMPLETE_TRIAL_HISTORY_REQUIRED'):self.register()
 def test_history_can_span_retired_roots_without_resetting_trial_number(self):
  older=copy.deepcopy(self.prior['plan']);older['schema']='heptatrader.financial-forward-plan.v1'
  older['plan_id']=digest({k:v for k,v in older.items() if k!='plan_id'})
  first_root=self.root/'retired-first';path=first_root/'first'/'plan.json';path.parent.mkdir(parents=True)
  path.write_text(json.dumps(older));path.chmod(0o600)
  prior=copy.deepcopy(self.prior['plan']);prior['trial']=2
  prior['plan_id']=digest({k:v for k,v in prior.items() if k!='plan_id'})
  self.prior_path.write_text(json.dumps(prior));self.history=[first_root,self.history]
  plan=self.register()
  self.assertEqual(plan['trial'],3);self.assertEqual(plan['prior_trial'],2)
  self.assertEqual(plan['old_trial_ids_retained'],[older['plan_id'],prior['plan_id']])
 def test_overlap_with_earlier_retired_trial_is_also_refused(self):
  earlier=copy.deepcopy(self.prior['plan']);earlier['training_input_ids']=[self.rows[0]['input_id']]
  earlier['plan_id']=digest({k:v for k,v in earlier.items() if k!='plan_id'})
  path=self.history/'earlier'/'plan.json';path.parent.mkdir();path.write_text(json.dumps(earlier));path.chmod(0o600)
  prior=copy.deepcopy(self.prior['plan']);prior['trial']=2
  prior['plan_id']=digest({k:v for k,v in prior.items() if k!='plan_id'});self.prior_path.write_text(json.dumps(prior))
  with self.assertRaisesRegex(ContractError,'HISTORICAL_TRAINING_OVERLAP'):self.register()
 def test_unknown_registration_mode_is_not_an_implicit_fresh_scope(self):
  with self.assertRaisesRegex(ContractError,'REGISTRATION_SCOPE_UNKNOWN'):self.register(registration_scope=True)
 def test_changed_prior_candidate_manifest_is_refused(self):
  path=self.prior['directory']/'candidate.json';candidate=json.loads(path.read_text());candidate['fixture_change']=True
  candidate['candidate_id']=digest({k:v for k,v in candidate.items() if k!='candidate_id'});path.write_text(json.dumps(candidate))
  with self.assertRaisesRegex(ContractError,'PRIOR_CANDIDATE_BINDING'):self.register()
 def test_existing_new_scope_registry_is_never_replayed(self):
  self.register()
  with self.assertRaisesRegex(ContractError,'ALREADY_REGISTERED_NO_REPLAY'):self.register()
