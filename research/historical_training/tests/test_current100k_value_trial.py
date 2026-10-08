import copy,tempfile,unittest,hashlib
from pathlib import Path
from unittest.mock import patch
from historical_training.current100k_value_trial import selected_rows,validate_trial
from historical_training.current100k import BEHAVIOR
from historical_training.common import digest
class ValueTrialTests(unittest.TestCase):
 def fixture(self):
  row={'row_id':'r','net_usd':'-25.5','reward':{},'source_request':{},'source_binding':{}}
  data={'schema':'heptatrader.current100k-value-dataset.v1','behavior_profile':BEHAVIOR,'synthetic_rewards':0,'old25k_rewards':0,'trade_authority':False,'period_sha256':'p','producer_manifest_sha256':'m','rows':[row]}
  data['fingerprint']=digest(data);return data,copy.deepcopy(data)
 def test_exact_negative_real_reward_kept(self):
  a,b=self.fixture()
  with patch('historical_training.current100k_value_trial.to_row',return_value=a['rows'][0]):self.assertEqual(selected_rows(a,b),a['rows'])
 def test_additional_current_rows_not_silently_imported(self):
  a,b=self.fixture();b['rows'].append({'row_id':'new'})
  with patch('historical_training.current100k_value_trial.to_row',return_value=a['rows'][0]):self.assertEqual(len(selected_rows(a,b)),1)
 def test_withdrawn_reward_fails(self):
  a,b=self.fixture();b['rows']=[]
  with self.assertRaises(ValueError):selected_rows(a,b)
 def test_corrected_reward_fails(self):
  a,b=self.fixture();b['rows'][0]['net_usd']='0'
  with self.assertRaises(ValueError):selected_rows(a,b)
 def test_mismatched_period_fails(self):
  a,b=self.fixture();b['period_sha256']='other'
  with self.assertRaises(ValueError):selected_rows(a,b)
 def test_synthetic_scope_fails(self):
  a,b=self.fixture();a['synthetic_rewards']=1
  with self.assertRaises(ValueError):selected_rows(a,b)
 def test_hash_tamper_fails(self):
  a,b=self.fixture();a['fingerprint']='bad'
  with self.assertRaises(ValueError):selected_rows(a,b)
 def test_wrong_original_binding_fails(self):
  a,b=self.fixture()
  with patch('historical_training.current100k_value_trial.to_row',return_value={}):
   with self.assertRaises(ValueError):selected_rows(a,b)
 def test_target_bound_no_clipping(self):
  a,b=self.fixture();a['rows'][0]['net_usd']='801';a['fingerprint']=digest({k:v for k,v in a.items() if k!='fingerprint'});b=copy.deepcopy(a)
  with patch('historical_training.current100k_value_trial.to_row',return_value=a['rows'][0]):
   with self.assertRaises(ValueError):selected_rows(a,b)
 def test_actor_update_flag_refused(self):
  with self.assertRaises(ValueError):validate_trial({'schema':'heptatrader.current100k-value-trial-config.v1','actor_update':True,'automatic_selection':False,'trade_authority':False})
