"""CPU contracts for explicitly frozen resident price comparisons."""
import copy,math,tempfile,unittest
from pathlib import Path
import numpy as np
from laya_cell import resident_price_baselines as b
from laya_cell import resident_training_v2 as v2
from laya_cell.integrity import ContractError,digest,sha_file
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
