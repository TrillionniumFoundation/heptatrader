"""CPU contracts for explicitly frozen resident price comparisons."""
import copy,json,math,subprocess,sys,tempfile,unittest
from pathlib import Path
import numpy as np
from laya_cell import resident_price_baselines as b
from laya_cell import resident_training_v2 as v2
from laya_cell.integrity import ContractError,canonical,digest,sha_file,validate_candidate_artifact
from laya_cell.resident_row_accounting import row_id
from laya_cell.resident_evaluate import measure

BASE=1800000000000

def state(i,horizon=300,anchor=None):
 bid=1.08+i*.00002;tick=.000001*(i%3+1)
 return {'instrument':'EUR.USD','anchor_ms':BASE+i*60000 if anchor is None else anchor,
  'reference_mid':str(bid+.00002),'horizon_sec':horizon,
  'columns':['offset_ms','bid','ask'],
  'quotes':[[j*10000,str(bid+j*tick),str(bid+j*tick+.00004)] for j in range(-5,1)]}

def population():
 rows=[]
 for i in range(16):
  for h in (300,900):
   s=state(i,h)
   rows.append({'sample_id':digest(['sample',i]),'horizon_sec':h,'anchor_ms':s['anchor_ms'],
    'outcome_receipt_digest':digest(['outcome',i,h]),'outcome_available_at_ms':s['anchor_ms']+h*1000+1,
    'target_class':0 if h==300 else 6,'state':s,'teacher_probs':None})
 return rows,max(r['outcome_available_at_ms'] for r in rows)+1

class PriceBaselineTests(unittest.TestCase):
 def test_same_rows_deterministic_frozen_fit_and_no_input_mutation(self):
  rows,cutoff=population();before=copy.deepcopy(rows)
  a=b.freeze(rows,cutoff);other=b.freeze(rows,cutoff)
  self.assertEqual(a,other);self.assertEqual(rows,before)
  self.assertEqual(a['training_row_ids_digest'],digest(sorted(row_id(r) for r in rows)))
  self.assertEqual(a['training_rows'],32)
  self.assertEqual(a['simple_model_spec']['steps'],128)
  self.assertEqual(len(a['weights'])*len(a['bias'])+len(a['bias']),77)
  self.assertEqual(a['train_prior_by_horizon']['300']['class_counts'],[16,0,0,0,0,0,0])
  self.assertEqual(a['train_prior_by_horizon']['900']['class_counts'],[0,0,0,0,0,0,16])
  self.assertFalse(a['model_or_trade_permission'])
 def test_only_training_features_define_standardization(self):
  rows,cutoff=population();f=b.freeze(rows,cutoff)
  x=np.asarray([b.features(r['state']) for r in rows],dtype=np.float64)
  np.testing.assert_array_equal(f['mean'],x.mean(axis=0))
  expected=np.where(x.std(axis=0)>=1e-6,x.std(axis=0),1.)
  np.testing.assert_array_equal(f['scale'],expected)
  changed=copy.deepcopy(rows)
  for r in changed:r['target_class']=3
  g=b.freeze(changed,cutoff)
  self.assertEqual(f['mean'],g['mean']);self.assertEqual(f['scale'],g['scale'])
  self.assertNotEqual(f['weights'],g['weights'])
 def test_linear_fit_learns_true_horizon_labels_with_fixed_steps(self):
  rows,cutoff=population();f=b.freeze(rows,cutoff)
  x=np.asarray([b.features(r['state']) for r in rows],dtype=np.float64)
  z=np.clip((x-np.asarray(f['mean']))/np.asarray(f['scale']),-6,6)
  probs=b._softmax(z@np.asarray(f['weights'])+np.asarray(f['bias']))
  targets=np.asarray([r['target_class'] for r in rows])
  self.assertTrue(np.all(np.argmax(probs,axis=-1)==targets))
  prior=(np.bincount(targets,minlength=7)+1)/(len(rows)+7)
  self.assertLess(float(-np.log(probs[np.arange(len(rows)),targets]).mean()),
                  float(-np.log(prior[targets]).mean()))
 def test_future_or_duplicate_training_row_rejected(self):
  rows,cutoff=population()
  with self.assertRaises(ContractError):b.freeze(rows,rows[0]['outcome_available_at_ms']-1)
  with self.assertRaises(ContractError):b.freeze(rows[:-1]+[rows[0]],cutoff)
  rows[0]['state']['quotes'][-1][0]=1
  with self.assertRaises(ContractError):b.freeze(rows,cutoff)
 def test_no_horizon_rows_is_explicit_uniform_laplace_prior(self):
  rows,cutoff=population();rows=[r for r in rows if r['horizon_sec']==900]
  f=b.freeze(rows,cutoff);missing=f['train_prior_by_horizon']['300']
  self.assertEqual(missing['rows'],0)
  self.assertEqual(missing['class_counts'],[0]*7)
  self.assertEqual(missing['probs'],[1/7]*7)
 def test_prediction_reads_only_causal_numeric_state(self):
  rows,cutoff=population();f=b.freeze(rows,cutoff);s=state(20,300,cutoff+60000)
  before=b.forecast(f,s)
  enriched={**s,'target_class':6,'future_outcome':{'target_class':0,'return_bps':999}}
  after=b.forecast(f,enriched)
  for key in ('uniform_probs','train_prior_probs','linear_softmax_probs'):
   self.assertEqual(before[key],after[key])
  self.assertNotEqual(before['input_id'],after['input_id'])
  self.assertEqual(before['horizon_sec'],300)
  self.assertTrue(all(math.isfinite(p) for p in before['linear_softmax_probs']))
  self.assertAlmostEqual(sum(before['linear_softmax_probs']),1.)
 def test_frozen_parameters_instrument_and_cutoff_bound(self):
  rows,cutoff=population();f=b.freeze(rows,cutoff);s=state(20,300,cutoff+60000)
  altered=copy.deepcopy(f);altered['weights'][0][0]+=.1
  with self.assertRaises(ContractError):b.forecast(altered,s)
  with self.assertRaises(ContractError):b.forecast(f,{**s,'instrument':'BTC.USD'})
  with self.assertRaises(ContractError):b.forecast(f,{**s,'anchor_ms':cutoff-1})
 def test_same_future_outcome_scored_for_every_model(self):
  rows,cutoff=population();f=b.freeze(rows,cutoff);s=state(20,300,cutoff+60000)
  plan={'candidate_id':'c','plan_id':'p','price_baselines':f,
   'action_output_profile':v2.ACTION_OUTPUT,'action_training_profile_id':None,'action_optimizer_admitted':False}
  pred={'candidate_id':'c','plan_id':'p','slot':0,'anchor_ms':s['anchor_ms'],
   'created_at_ms':s['anchor_ms']+1,'trained_until_ms':cutoff,
   'input':s,'input_id':digest(s),'reference_mid':float(s['reference_mid']),
   'candidate_probs':[1/7]*7,'parent_probs':[1/7]*7,'candidate_act_escalate_probs':None,
   'bootstrap_point_bps':0.,'action_output_profile':v2.ACTION_OUTPUT,
   'action_inference':{'frozen_profile_id':None,'action_correct_probs':None,'model_or_trade_permission':False},
   'price_baseline_forecast':b.forecast(f,s)}
  quotes=[{'t':s['anchor_ms']+i*10000,'mid':pred['reference_mid']*(1+i*.000001),'receipt':str(i)} for i in range(31)]
  result=measure(plan,0,pred,quotes,s['anchor_ms']+300001)
  self.assertEqual(result['status'],'OBSERVED')
  self.assertEqual(set(result['same_window_price_scores']),set(b.MODELS))
  self.assertEqual(result['target_class'],3)
  self.assertAlmostEqual(result['return_bps'],(quotes[-1]['mid']/quotes[0]['mid']-1)*10000)
  for name in ('uniform','laya_candidate','frozen_parent'):
   self.assertAlmostEqual(result['same_window_price_scores'][name]['brier'],6/7)
   self.assertAlmostEqual(result['same_window_price_scores'][name]['nll'],math.log(7))
  self.assertAlmostEqual(result['candidate_brier'],result['same_window_price_scores']['laya_candidate']['brier'])
  pred['price_baseline_forecast']['linear_softmax_probs'][0]+=.01
  with self.assertRaises(ContractError):measure(plan,0,pred,quotes,s['anchor_ms']+300001)
 def test_zero_probability_is_infinite_nll_and_never_dropped_from_mean(self):
  normal=b.probability_metrics([1/7]*7,3)
  impossible=b.probability_metrics([1.,0.,0.,0.,0.,0.,0.],3)
  self.assertIsNone(impossible['nll']);self.assertTrue(impossible['nll_is_infinite'])
  scores={name:copy.deepcopy(normal) for name in b.MODELS};scores['laya_candidate']=impossible
  results=[{'status':'OBSERVED','same_window_price_scores':scores},
   {'status':'OBSERVED','same_window_price_scores':{name:normal for name in b.MODELS}},
   {'status':'CENSORED','reason':'PATH_GAP'}]
  report=b.summarize(results)
  self.assertEqual(report['observed'],2);self.assertEqual(report['censored'],1)
  self.assertIsNone(report['models']['laya_candidate']['mean_nll'])
  self.assertTrue(report['models']['laya_candidate']['nll_is_infinite'])
  self.assertAlmostEqual(report['models']['uniform']['mean_nll'],math.log(7))
  self.assertAlmostEqual(report['models']['laya_candidate']['mean_brier'],(2.+6/7)/2)

class TrainingDiagnosticTests(unittest.TestCase):
 def candidate(self):
  rows,cutoff=population()
  candidate={'schema':'heptatrader.laya-resident-candidate.v1',
   'objective':copy.deepcopy(v2.OBJECTIVE),'price_baselines':b.freeze(rows,cutoff),
   'action_update_steps':0,'action_training_profile':None,
   'measurement':{'training_only':True,'rows':32,'unique_action_labels':0,
    'parent_ce':2.,'candidate_ce':1.99,'candidate_brier':.97},
   'steps':[{'applied':True,'training_row_ids':[row_id(r) for r in rows[i:i+2]],
    'teacher_rows':0,'gradient_norm_before_clip':2. if i==0 else .5}
    for i in range(0,32,2)]}
  from laya_cell.resident_row_accounting import receipt
  ids=[row_id(r) for r in rows]
  candidate['row_accounting']=receipt(ids,candidate['steps'],ids)
  candidate['data_fingerprint']=digest(ids)
  candidate['measurement']['action_target_counts']={'0':0,'1':32,'None':0}
  return candidate,rows
 def test_selected_action_support_does_not_label_unobserved_pool(self):
  cases=[('partial_zero',512,0,0,None),('full_zero',32,0,0,False),
         ('partial_positive',512,3,0,True),('full_unknown',32,0,1,None),
         ('missing_context_positive',512,3,0,None),('missing_counts',32,0,0,None),
         ('different_complete_pool',32,3,0,None),('different_measurement',32,3,0,None)]
  for name,pool_count,act,unknown,want in cases:
   with self.subTest(name=name):
    candidate,rows=self.candidate();ids=[row_id(r) for r in rows]
    pool_ids=sorted(ids) if pool_count==32 else sorted(ids+[digest(['unselected',i]) for i in range(480)])
    candidate['selection_context']={'pool_count':pool_count,'pool_row_ids_digest':digest(pool_ids)}
    candidate['measurement']['action_target_counts']={'0':act,'1':32-act-unknown,'None':unknown}
    if name=='missing_context_positive':del candidate['selection_context']
    elif name=='missing_counts':del candidate['measurement']['action_target_counts']
    elif name=='different_complete_pool':candidate['selection_context']['pool_row_ids_digest']='a'*64
    elif name=='different_measurement':candidate['row_accounting']['measurement_row_ids'][0]='b'*64
    before=copy.deepcopy(candidate)
    result=b.candidate_training_diagnostics(candidate)['action_selection_diagnostic']
    self.assertEqual(candidate,before);self.assertIs(result['pool_has_act'],want)
    self.assertIsNone(result['unselected_target_counts'])
    if name=='partial_zero':
     self.assertEqual(result['unselected_rows'],480)
     self.assertEqual(result['selected_target_counts'],{'ACT_correct':0,'ESCALATE_wrong':32,'unknown':0})
    if name=='missing_context_positive':self.assertIsNone(result['pool_count'])
    if name in ('missing_context_positive','different_complete_pool','different_measurement'):
     self.assertIsNone(result['unselected_rows'])
    else:self.assertEqual(result['unselected_rows'],pool_count-32)

 def test_repeated_epoch_presentations_and_rollbacks_do_not_inflate_selected_rows(self):
  candidate,rows=self.candidate();ids=[row_id(r) for r in rows]
  candidate['selection_context']={'pool_count':32,'pool_row_ids_digest':digest(sorted(ids))}
  candidate['steps']=copy.deepcopy(candidate['steps']*4)
  candidate['steps'][-1]['applied']=False
  report=b.candidate_training_diagnostics(candidate)
  self.assertEqual(report['training_examples']['attempted'],128)
  result=report['action_selection_diagnostic']
  self.assertEqual(result['selected_rows'],32);self.assertTrue(result['full_pool_covered'])
  self.assertFalse(result['pool_has_act'])

 def artifact(self,root):
  candidate,_=self.candidate()
  (root/'adapter.safetensors').write_bytes(b'SYNTHETIC_HASH_FIXTURE_NOT_A_MODEL')
  candidate['adapter_sha256']=sha_file(root/'adapter.safetensors')
  candidate['candidate_id']=digest(candidate)
  (root/'candidate.json').write_bytes(canonical(candidate)+b'\n')
  return candidate
 def test_steps_examples_and_training_only_references_use_original_population(self):
  candidate,rows=self.candidate();before=copy.deepcopy(candidate)
  report=b.candidate_training_diagnostics(candidate)
  self.assertEqual(candidate,before)
  self.assertEqual(report['optimizer_steps'],{'attempted':16,'applied':16,'rolled_back':0})
  self.assertEqual(report['training_examples'],{'attempted':32,'applied':32,'unique_attempted':32,'unique_applied':32})
  self.assertEqual(report['action_optimizer_steps_applied'],0)
  self.assertEqual(report['gradient_norm_before_clip']['steps_exceeding_cap'],1)
  self.assertAlmostEqual(report['training_metrics']['parent_minus_candidate_ce'],.01)
  references=report['same_training_rows_baselines']
  self.assertTrue(references['same_row_ids_as_recorded_steps'])
  self.assertTrue(references['prior_fit_reuses_scored_labels'])
  self.assertFalse(references['held_out']);self.assertFalse(report['future_value_proven'])
  self.assertAlmostEqual(references['models']['uniform']['brier'],6/7)
  self.assertAlmostEqual(references['models']['uniform']['nll'],math.log(7))
  profile=candidate['price_baselines']
  expected=[b.probability_metrics(profile['train_prior_by_horizon'][str(r['horizon_sec'])]['probs'],r['target_class']) for r in rows]
  self.assertAlmostEqual(references['models']['train_prior']['brier'],sum(x['brier'] for x in expected)/32)
  self.assertAlmostEqual(references['models']['train_prior']['nll'],sum(x['nll'] for x in expected)/32)
 def test_rollback_and_old_missing_gradient_receipts_are_not_counted_as_updates(self):
  candidate,_=self.candidate();candidate['steps'][-1]['applied']=False
  for step in candidate['steps']:
   del step['gradient_norm_before_clip'];del step['teacher_rows']
  report=b.candidate_training_diagnostics(candidate)
  self.assertEqual(report['optimizer_steps'],{'attempted':16,'applied':15,'rolled_back':1})
  self.assertEqual(report['training_examples']['applied'],30)
  self.assertEqual(report['training_examples']['unique_applied'],30)
  self.assertEqual(report['gradient_norm_before_clip']['recorded_steps'],0)
  self.assertIsNone(report['gradient_norm_before_clip']['mean'])
  self.assertIsNone(report['teacher_rows_in_recorded_steps'])
 def test_shared_artifact_validator_and_audit_preserve_original_hash_contract(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);candidate=self.artifact(root);raw=(root/'candidate.json').read_bytes()
   self.assertEqual(validate_candidate_artifact(root),candidate)
   report=b.audit_candidate_training(root)
   self.assertEqual(report['candidate_id'],candidate['candidate_id'])
   self.assertEqual((root/'candidate.json').read_bytes(),raw)
   candidate['measurement']['rows']=31
   (root/'candidate.json').write_bytes(canonical(candidate))
   with self.assertRaisesRegex(ContractError,'PARENT_MANIFEST_HASH'):b.audit_candidate_training(root)
   (root/'candidate.json').write_bytes(raw)
   (root/'adapter.safetensors').write_bytes(b'SYNTHETIC_CORRUPTION')
   with self.assertRaisesRegex(ContractError,'PARENT_ADAPTER_HASH'):b.audit_candidate_training(root)
 def test_cpu_audit_does_not_import_training_or_model_libraries(self):
  code='''
import importlib.abc,json,sys
from pathlib import Path
sys.path[:0]=json.loads(sys.argv[2])
class NoHeavyImports(importlib.abc.MetaPathFinder):
 def find_spec(self,fullname,path=None,target=None):
  if fullname.split('.')[0] in {'torch','transformers','safetensors'}:
   raise AssertionError('heavy import: '+fullname)
sys.meta_path.insert(0,NoHeavyImports())
from laya_cell.resident_price_baselines import audit_candidate_training
report=audit_candidate_training(Path(sys.argv[1]))
assert report['diagnostics']['optimizer_steps']['attempted']==16
assert report['diagnostics']['action_support_diagnostic']['reason']=='INSUFFICIENT_CAUSAL_PARENT_CORRECTNESS_ROWS'
assert 'laya_cell.resident_train' not in sys.modules
assert not any(name.split('.')[0] in {'torch','transformers','safetensors'} for name in sys.modules)
'''
  with tempfile.TemporaryDirectory() as td:
   self.artifact(Path(td))
   result=subprocess.run([sys.executable,'-c',code,td,json.dumps(sys.path)],capture_output=True,text=True,timeout=30)
   self.assertEqual(result.returncode,0,result.stderr)


class ExplicitConfigurationTests(unittest.TestCase):
 def fixture(self,root):
  old=root/'old';new=root/'new'
  for folder in (old,new):
   (folder/'bin').mkdir(parents=True);(folder/'bin/native').write_bytes(b'unchanged-native')
  for name in v2.RUNTIME_PINS:
   p=new/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('new code\n')
  config={'schema':'heptatrader.laya-resident-config.v1','automatic_trading':False,
   'runtime_directory':str(old),'runtime_sha256':{'bin/native':sha_file(old/'bin/native')},
   'native_binary':str(old/'bin/native'),'native_binary_sha256':sha_file(old/'bin/native'),
   'control_directory':'same/control','evaluation_directory':'same/evaluation',
   'candidate_directory':'same/candidates','initial_parent_directory':'same/parent',
   'promotion_protocol':'SAME_EXISTING_PROTOCOL','promotion_legacy_trial_floor':41,
   'future_plan_admission_profile':{'keep':'identical'},'objective':{'schema':'old'},'profile':{'keep':'same tensor mask'}}
  return config,new
 def test_new_objective_and_pins_keep_existing_state_and_trial_budget(self):
  with tempfile.TemporaryDirectory() as td:
   original,runtime=self.fixture(Path(td));before=copy.deepcopy(original)
   new=v2.configuration_for_release(original,runtime)
   self.assertEqual(original,before);self.assertEqual(new['objective'],v2.OBJECTIVE)
   self.assertEqual(new['runtime_directory'],str(runtime))
   for key in ('control_directory','evaluation_directory','candidate_directory','initial_parent_directory',
               'promotion_protocol','promotion_legacy_trial_floor','future_plan_admission_profile','profile'):
    self.assertEqual(new[key],original[key])
   for name in v2.RUNTIME_PINS:self.assertEqual(new['runtime_sha256'][name],sha_file(runtime/name))
   self.assertEqual(new['native_binary'],str(runtime/'bin/native'))
 def test_different_native_binary_is_not_silently_adopted(self):
  with tempfile.TemporaryDirectory() as td:
   config,runtime=self.fixture(Path(td));(runtime/'bin/native').write_bytes(b'changed')
   with self.assertRaises(ContractError):v2.configuration_for_release(config,runtime)
 def test_guard_wrapper_cannot_be_mistaken_for_original_configuration(self):
  with tempfile.TemporaryDirectory() as td:
   config,runtime=self.fixture(Path(td));config['schema']='guard-wrapper'
   with self.assertRaises(ContractError):v2.configuration_for_release(config,runtime)

if __name__=='__main__':unittest.main()
