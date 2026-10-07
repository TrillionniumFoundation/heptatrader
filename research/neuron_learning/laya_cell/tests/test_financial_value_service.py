"""Recovery and behavior-scope tests for the separate financial learner."""
import copy,json,sqlite3,tempfile,unittest
from pathlib import Path
from laya_cell.financial_value_service import database,behavior_datasets,Service
from laya_cell.integrity import ContractError

class ValueServiceTests(unittest.TestCase):
 def data(self):
  return {'rows':[{'row_id':'a','instrument':'EUR.USD','behavior_model_id':'remote','input_started_at_ms':1,'reward':{'input_attribution':{'producer_manifest_sha256':'old'}},'net_usd':'-11.50'},
                  {'row_id':'b','instrument':'EUR.USD','behavior_model_id':'remote','input_started_at_ms':2,'reward':{'input_attribution':{'producer_manifest_sha256':'old'}},'net_usd':'12.50'}]}
 def test_same_behavior_losses_and_gains_kept_together(self):
  groups=behavior_datasets(self.data());self.assertEqual(len(groups),1);self.assertEqual([r['net_usd'] for r in groups[0]['rows']],['-11.50','12.50'])
 def test_changed_fx_policy_does_not_mix_into_old_behavior_value(self):
  data=self.data();data['rows'][1]['reward']['input_attribution']['producer_manifest_sha256']='new'
  self.assertEqual(len(behavior_datasets(data)),2)
 def test_changed_remote_model_separate_head_scope(self):
  data=self.data();data['rows'][1]['behavior_model_id']='other'
  self.assertEqual(len(behavior_datasets(data)),2)
 def test_wallclock_status_does_not_retrigger_training(self):
  a=self.data();b=copy.deepcopy(a);a['observed_at_ms']=1;b['observed_at_ms']=999
  self.assertEqual(behavior_datasets(a)[0]['fingerprint'],behavior_datasets(b)[0]['fingerprint'])
 def test_new_corrected_reward_identity_changes_dataset(self):
  a=self.data();b=copy.deepcopy(a);b['rows'][0]['row_id']='corrected'
  self.assertNotEqual(behavior_datasets(a)[0]['fingerprint'],behavior_datasets(b)[0]['fingerprint'])
 def test_same_dataset_cannot_be_admitted_twice(self):
  with tempfile.TemporaryDirectory() as td:
   d=database(Path(td)/'db');d.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?)',('j','fingerprint','RUNNING',1,'{}',None));d.commit()
   with self.assertRaises(sqlite3.IntegrityError):d.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?)',('j2','fingerprint','RUNNING',2,'{}',None))
   d.close()
 def test_interrupted_job_not_blindly_restarted(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);s=Service.__new__(Service);s.db=database(root/'db')
   s.db.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?)',('j','f','RUNNING',1,json.dumps({'job_output':str(root/'absent')}),None));s.db.commit();s.recover()
   self.assertEqual(s.db.execute('SELECT status FROM jobs').fetchone()[0],'INTERRUPTED');s.db.close()
 def test_committed_cadence_survives_connection_restart(self):
  with tempfile.TemporaryDirectory() as td:
   p=Path(td)/'db';d=database(p);d.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?)',('j','f','COMPLETED',1234,'{}','candidate'));d.commit();d.close()
   d=database(p);self.assertEqual(d.execute('SELECT MAX(created_ms) FROM jobs').fetchone()[0],1234);d.close()

if __name__=='__main__':unittest.main(verbosity=2)
