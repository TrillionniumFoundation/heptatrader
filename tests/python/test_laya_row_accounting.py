"""CPU-only row admission/accounting regression; never touches a live ledger."""
import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch,MagicMock
from laya_cell import resident
from laya_cell.integrity import ContractError,digest
from laya_cell.resident_store import database,admit_job,complete_job

class AdmissionRegression(unittest.TestCase):
 def test_scheduler_reserves_only_rows_the_sixteen_steps_can_train(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);s=object.__new__(resident.Supervisor)
   s.root=root/'control';s.root.mkdir();s.db=database(s.root/'scheduler.sqlite')
   self.addCleanup(s.db.close);s.child=None;s.stop=False;s.storage=None
   s.c={'evaluation_directory':str(root/'evaluation'),'initial_parent_directory':str(root/'parent'),
        'candidate_directory':str(root/'candidates'),'profile':{},'objective':{},'python':'never-executed',
        'native_binary_sha256':'e'*64,'period_sha256':'a'*64}
   (root/'evaluation').mkdir()
   rows=[{'sample_id':str(i),'horizon_sec':300,'outcome_receipt_digest':str(i)} for i in range(64)]
   fake=MagicMock();fake.wait.return_value=1
   rewards={'schema':'heptatrader.laya-reward-status.v2','at_ms':1,'rewards':[],
            'execution_rewards':[],'reason':'TEST_ONLY'}
   with patch.object(resident,'config_check'),patch.object(resident,'bootstrap_still_off'),        patch.object(resident,'inspect_storage',return_value={'admission_ready':True}),        patch.object(resident,'rewards_current',return_value=rewards),        patch.object(resident,'collect',return_value=({},rows,{'window':{}},None)),        patch.object(resident,'read_json',return_value={'candidate_id':'b'*64,'generation':2}),        patch.object(resident,'sha_file',return_value='c'*64),        patch.object(resident.subprocess,'Popen',return_value=fake),        patch.object(s,'candidate_done',return_value=False),patch.object(s,'write_status',side_effect=lambda **k:k):
    s.step()
   job=json.loads(s.db.execute('SELECT payload FROM jobs').fetchone()[0])
   self.assertEqual(len(job['training_row_ids']),32)
   self.assertEqual(s.db.execute('SELECT count(*) FROM consumed_rows').fetchone()[0],32)
   self.assertEqual(job['training_row_ids'],[digest([r['sample_id'],300,r['outcome_receipt_digest']]) for r in rows[-32:]])

from laya_cell.resident_row_accounting import SCHEMA,receipt,batches,verify_candidate

class ReceiptRegression(unittest.TestCase):
 def ids(self,n=32):return [digest(['synthetic-row',i]) for i in range(n)]
 def candidate(self,n=32):
  ids=self.ids(n);steps=[{'training_row_ids':x,'applied':True} for x in batches(ids)]
  return {'candidate_id':'c'*64,'steps':steps,'measurement':{'rows':n},'row_accounting':receipt(ids,steps,ids)}
 def job(self,n=32):return {'training_row_accounting_schema':SCHEMA,'training_row_ids':self.ids(n)}
 def test_exact_32_row_receipt(self):
  c=self.candidate();verify_candidate(self.job(),c)
  self.assertEqual(c['row_accounting']['gradient_attempted_count'],32)
 def test_odd_batch_remainder_is_not_dropped(self):
  c=self.candidate(21);verify_candidate(self.job(21),c)
  self.assertEqual(len(c['steps']),11);self.assertEqual(len(c['steps'][-1]['training_row_ids']),1)
 def test_64_row_reservation_is_rejected(self):
  with self.assertRaises(ContractError):self.candidate(64)
 def test_untrained_reserved_row_is_rejected(self):
  c=self.candidate();c['steps']=c['steps'][:-1]
  with self.assertRaisesRegex(ContractError,'MISMATCH'):verify_candidate(self.job(),c)
 def test_duplicate_step_cannot_inflate_samples(self):
  c=self.candidate();c['steps'][-1]=copy.deepcopy(c['steps'][0])
  with self.assertRaises(ContractError):verify_candidate(self.job(),c)
 def test_measurement_only_row_cannot_claim_gradient(self):
  c=self.candidate();c['row_accounting']['gradient_attempted_row_ids'].pop()
  with self.assertRaises(ContractError):verify_candidate(self.job(),c)
 def test_norm_rollback_is_not_an_applied_update(self):
  ids=self.ids();steps=[{'training_row_ids':x,'applied':False} for x in batches(ids)]
  r=receipt(ids,steps,ids);self.assertEqual(r['applied_count'],0);self.assertEqual(r['rolled_back_count'],32)
 def test_boolean_applied_flag_is_required(self):
  c=self.candidate();c['steps'][0]['applied']=1
  with self.assertRaises(ContractError):verify_candidate(self.job(),c)
 def test_unknown_schema_is_not_treated_as_legacy(self):
  j=self.job();j['training_row_accounting_schema']='unknown'
  with self.assertRaises(ContractError):verify_candidate(j,self.candidate())
 def test_legacy_receipt_not_rewritten_or_reclassified(self):
  c={'candidate_id':'legacy'};before=copy.deepcopy(c);verify_candidate({},c);self.assertEqual(c,before)
 def test_missing_receipt_rejected_for_new_job(self):
  c=self.candidate();del c['row_accounting']
  with self.assertRaises(ContractError):verify_candidate(self.job(),c)
 def test_completion_and_restart_preserve_exactly_once(self):
  with tempfile.TemporaryDirectory() as td:
   path=Path(td)/'state.sqlite';db=database(path);ids=self.ids();job=self.job()
   self.assertTrue(admit_job(db,'j',digest(ids),1,job,ids));db.close();db=database(path)
   try:
    self.assertTrue(complete_job(db,'j',self.candidate()))
    self.assertFalse(complete_job(db,'j',self.candidate()))
    with self.assertRaises(ContractError):admit_job(db,'another','new-fingerprint',2,job,ids)
    self.assertEqual(db.execute('SELECT count(*) FROM consumed_rows').fetchone()[0],32)
   finally:db.close()
 def test_failed_completion_does_not_mark_job_completed(self):
  with tempfile.TemporaryDirectory() as td:
   db=database(Path(td)/'state.sqlite');self.addCleanup(db.close);ids=self.ids()
   admit_job(db,'j',digest(ids),1,self.job(),ids);bad=self.candidate();bad['row_accounting']['applied_count']=999
   with self.assertRaises(ContractError):complete_job(db,'j',bad)
   self.assertEqual(db.execute('SELECT status FROM jobs').fetchone()[0],'ADMITTED')
 def test_invalid_reservation_writes_nothing(self):
  with tempfile.TemporaryDirectory() as td:
   db=database(Path(td)/'state.sqlite');self.addCleanup(db.close)
   with self.assertRaises(ContractError):admit_job(db,'j','bad',1,self.job(64),self.ids(64))
   self.assertEqual(db.execute('SELECT count(*) FROM consumed_rows').fetchone()[0],0)

if __name__=='__main__':unittest.main()
