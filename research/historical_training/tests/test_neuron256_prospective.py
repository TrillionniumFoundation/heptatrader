import copy,datetime,json,math,tempfile,unittest
from pathlib import Path
from historical_training.neuron256_prospective import EDGES,BASELINES,FAMILY,valid_probs,choose_rho,validate_plan,assess,original_input,outcome
from historical_training.common import EvidenceError,digest

class ResidualDesignTests(unittest.TestCase):
 def plan(self):
  start=1791558600;anchors=[start,start+7200]+[1791803400+d*86400+k*7200 for d in range(4) for k in range(4)]
  p={'schema':'heptatrader.neuron256-seven-bin-future-plan.v1','family':FAMILY,'trial':1,'alpha':.025,'baselines':list(BASELINES),'anchors':anchors,
   'registered_at_ms':start*1000-86400000,'laya29_end_ms':start*1000-3600000,'min_observed':16,'max_censored':2,'min_days':4,'min_mean_brier_gain':.001,
   'cpu_public_key_hex':'ab'*32,'cpu_backend':'2.11.0+cpu','cpu_emitter_sha256':'ab'*32,
   'runtime_sha256':{'research/historical_training/neuron256_cpu_custody.py':'11'*32},
   'trade_authority':False,'automatic_selection':False,'replay_missed_windows':False}
  p['plan_id']=digest(p);return p
 def records(self,p):
  return [{'anchor':a,'status':'OBSERVED','target_class':i%2,'brier':{'candidate':.1,**{b:.2 for b in BASELINES}}} for i,a in enumerate(p['anchors'])]
 def resign(self,p):p['plan_id']=digest({k:v for k,v in p.items() if k!='plan_id'});return p
 def test_seven_bins_not_three(self):
  for values in [[.3,.3,.4],[1/7]*6,[float('nan')]+[1/6]*6,[-.1]+[1.1/6]*6]:
   with self.assertRaises(EvidenceError):valid_probs(values)
  self.assertEqual(valid_probs([1/7]*7),[1/7]*7)
 def test_invalid_boolean_probability(self):
  with self.assertRaises(EvidenceError):valid_probs([True,0.,0.,0.,0.,0.,0.])
 def test_development_selection_not_unseen_holdout(self):
  p=[[.5,.5,0.,0.,0.,0.,0.]]*2;g=[[1.,0.,0.,0.,0.,0.,0.]]*2
  selected,grid=choose_rho(p,g,[0,0]);self.assertEqual(selected['rho'],1.);self.assertEqual(len(grid),5)
 def test_zero_rho_remains_allowed_control(self):
  p=[[1.,0.,0.,0.,0.,0.,0.]]*2;g=[[0.,1.,0.,0.,0.,0.,0.]]*2
  self.assertEqual(choose_rho(p,g,[0,0])[0]['rho'],0.)
 def test_foreign_or_duplicate_subject_rejected(self):
  p=self.plan();rs=self.records(p)
  with self.assertRaises(EvidenceError):assess(p,rs+[rs[0]],10**15)
  rs[0]['anchor']+=300
  with self.assertRaises(EvidenceError):assess(p,rs,10**15)
 def test_no_interim_qualification(self):
  p=self.plan();a=assess(p,self.records(p),p['anchors'][0]*1000);self.assertEqual(a['state'],'PENDING')
 def test_all_win_reachable_only_at_original_end(self):
  p=self.plan();a=assess(p,self.records(p),(p['anchors'][-1]+1560)*1000)
  self.assertEqual(a['state'],'PRICE_SCREEN_PASSED_RESEARCH_ONLY');self.assertFalse(a['economic_qualification']);self.assertFalse(a['trade_authority'])
 def test_censored_cases_not_deleted(self):
  p=self.plan();rs=self.records(p)
  for x in rs[:3]:x['status']='CENSORED'
  a=assess(p,rs,10**15);self.assertEqual(a['state'],'REJECTED');self.assertEqual(a['censored'],3)
 def test_missing_final_cases_remain_pending(self):
  p=self.plan();self.assertEqual(assess(p,self.records(p)[:-1],10**15)['state'],'PENDING')
 def test_each_baseline_required(self):
  p=self.plan();rs=self.records(p)
  for x in rs:x['brier']['prior']=.05
  a=assess(p,rs,10**15);self.assertEqual(a['state'],'REJECTED');self.assertIn('NO_MATERIAL_GAIN:prior',a['reasons'])
 def test_ties_do_not_gain_sign_evidence(self):
  p=self.plan();rs=self.records(p)
  for x in rs:x['brier']['graph']=.1
  a=assess(p,rs,10**15);self.assertEqual(a['comparisons']['graph']['non_ties'],0);self.assertEqual(a['state'],'REJECTED')
 def test_boolean_trial_cannot_reset_family(self):
  p=self.plan();p['trial']=True
  with self.assertRaises(EvidenceError):validate_plan(self.resign(p))
 def test_changed_alpha_or_shape_refused(self):
  for key,value in [('alpha',.05),('min_observed',1),('max_censored',18),('min_mean_brier_gain',0.),('replay_missed_windows',True),('trade_authority',True)]:
   p=self.plan();p[key]=value
   with self.assertRaises(EvidenceError):validate_plan(self.resign(p))
 def test_before_laya_end_refused(self):
  p=self.plan();p['laya29_end_ms']=p['anchors'][0]*1000
  with self.assertRaises(EvidenceError):validate_plan(self.resign(p))
 def test_nonfinite_aggregate_refused(self):
  p=self.plan();rs=self.records(p);rs[0]['brier']['candidate']=float('nan')
  with self.assertRaises(EvidenceError):assess(p,rs,10**15)

 def test_no_canonical_feature_returns_without_gpu_or_claim(self):
  from unittest.mock import patch
  from historical_training.neuron256_prospective import predict_one
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);parent=root/'parent';parent.mkdir()
   anchor=1791558600
   (parent/'plan.json').write_text(json.dumps({'anchors':[anchor]}))
   config={'root':str(root/'research'),'parent_root':str(parent),
           'canonical_cpu_root':str(root/'research'/'canonical-cpu')}
   plan=self.plan()
   candidate={'candidate_id':'candidate', 'encoder_asset':{}}
   with patch('historical_training.neuron256_prospective.time.time',return_value=anchor+10):
    with patch('historical_training.neuron256_prospective.original_input',
               return_value=({'time':anchor,'features':[0.]*25},{})):
     self.assertIsNone(predict_one(config,plan,candidate,anchor))
   self.assertFalse((root/'research'/'slots'/str(anchor)/'claim.json').exists())
 def test_future_plan_requires_canonical_cpu_pins(self):
  for key in ('cpu_public_key_hex','cpu_backend','cpu_emitter_sha256'):
   p=self.plan();p.pop(key)
   with self.assertRaises(EvidenceError):validate_plan(self.resign(p))
  p=self.plan();p['cpu_emitter_sha256']='not-hex'.ljust(64,'x')
  with self.assertRaises(EvidenceError):validate_plan(self.resign(p))
 def test_incomplete_or_late_raw_input_refused(self):
  with tempfile.TemporaryDirectory() as d:
   with self.assertRaises(EvidenceError):original_input(d,{'anchors':[0]},300,1)
