import copy,unittest
from historical_training.neuron256_market_trial import validate_populations,historical_policy,SIZES,PROFILE

class MarketArchiveTests(unittest.TestCase):
 def fixture(self):
  groups={};i=0
  for name,count in SIZES.items():
   rows=[]
   for _ in range(count):
    at=1700000000+i*7200;i+=1
    rows.append({'time':at,'history_start':at-3600,'entry_at':at+300,'exit_at':at+1200,'outcome_end':at+1500,'real_execution_reward':False,'features':[.1]*25,'target_bps':1.5,'long_after_spread_bps':1.,'short_after_spread_bps':-2.})
   groups[name]=rows
  return {'schema':'heptatrader.matched-lead1-input.v1','profile':PROFILE,'old_test_rows_exported':0,'trade_authority':False,'groups':groups}
 def test_declared_roles(self):
  self.assertEqual({k:len(v) for k,v in validate_populations(self.fixture()).items()},SIZES)
 def test_old_test_refused(self):
  d=self.fixture();d['old_test_rows_exported']=1
  with self.assertRaises(ValueError):validate_populations(d)
 def test_smaller_role_refused(self):
  d=self.fixture();d['groups']['fit'].pop()
  with self.assertRaises(ValueError):validate_populations(d)
 def test_overlapping_full_spans_refused(self):
  d=self.fixture();d['groups']['tune'][0]['history_start']=d['groups']['fit'][-1]['time']
  with self.assertRaises(ValueError):validate_populations(d)
 def test_duplicate_time_refused(self):
  d=self.fixture();d['groups']['fit'][1]=copy.deepcopy(d['groups']['fit'][0])
  with self.assertRaises(ValueError):validate_populations(d)
 def test_wrong_lead_refused(self):
  d=self.fixture();d['groups']['fit'][0]['entry_at']+=1
  with self.assertRaises(ValueError):validate_populations(d)
 def test_real_execution_not_mixed(self):
  d=self.fixture();d['groups']['fit'][0]['real_execution_reward']=True
  with self.assertRaises(ValueError):validate_populations(d)
 def test_nonfinite_input_refused(self):
  d=self.fixture();d['groups']['fit'][0]['features'][0]=float('nan')
  with self.assertRaises(ValueError):validate_populations(d)
 def test_nonfinite_outcome_refused(self):
  d=self.fixture();d['groups']['fit'][0]['target_bps']=float('nan')
  with self.assertRaises(ValueError):validate_populations(d)
 def policy_fixture(self):
  return self.fixture()['groups']['fit'][:2],[[0,0,0,0,0,0,1]]*2,[[1,1]]*2,[-15,-6,-2,0,2,6,15]
 def test_cost_and_fixed_trade_stress(self):
  v=historical_policy(*self.policy_fixture());self.assertEqual(v['count'],2)
  self.assertAlmostEqual(v['net_usd'],2.);self.assertAlmostEqual(v['fixed_trade_extra_1bp_net_usd'],-18.)
 def test_no_overlapping_positions(self):
  r,p,e,m=self.policy_fixture();r[1].update(entry_at=r[0]['entry_at']+1,exit_at=r[0]['exit_at']+1)
  self.assertEqual(historical_policy(r,p,e,m)['count'],1)
 def test_negative_edge_abstains(self):
  r,p,e,m=self.policy_fixture();self.assertEqual(historical_policy(r,p,[[-1,-1]]*2,m)['count'],0)
 def test_mismatched_rows_refused(self):
  r,p,e,m=self.policy_fixture()
  with self.assertRaises(ValueError):historical_policy(r,p[:-1],e,m)
 def test_bad_probability_refused(self):
  r,p,e,m=self.policy_fixture();p[0]=[1]*7
  with self.assertRaises(ValueError):historical_policy(r,p,e,m)
 def test_initial_zero_drawdown(self):
  r,p,e,m=self.policy_fixture();r[0]['long_after_spread_bps']=-1.1;r[1]['long_after_spread_bps']=.9
  self.assertAlmostEqual(historical_policy(r,p,e,m)['max_drawdown_usd'],20.)
