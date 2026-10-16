"""Actual CPU autograd through run_job, with synthetic model/data only."""
import json,tempfile,unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import torch
from safetensors.torch import save_file
from laya_cell import resident_train as t
from laya_cell.resident_model import ResidentDecisionCell,PROFILE
from laya_cell.resident_row_accounting import SCHEMA,row_id,verify_candidate
from laya_cell.integrity import digest,sha_file
from laya_cell.tests.test_resident import TinyPretrained

class NativeFixture:
 def __init__(self,*args):pass
 def predictive_utility(self,*args):return {'receipt_digest':'e'*64}
 def propose(self,*args):return {'proposal_digest':'f'*64}

class WorkerRowIntegration(unittest.TestCase):
 def run_fixture(self,n,rollback=False):
  torch.set_num_threads(1)
  with tempfile.TemporaryDirectory() as td,ExitStack() as stack:
   root=Path(td);base=root/'base';base.mkdir();(base/'base-manifest.json').write_text('{}')
   parent=root/'parent';parent.mkdir();cell=ResidentDecisionCell(TinyPretrained(),'a'*64)
   save_file(cell.trainable_state(),str(parent/'adapter.safetensors'))
   manifest={'base_id':'a'*64,'base_revision':'cpu-synthetic-only','window':{'training_end_ms':0},
             'adapter_sha256':sha_file(parent/'adapter.safetensors')}
   manifest['candidate_id']=digest(manifest);(parent/'candidate.json').write_text(json.dumps(manifest))
   data=root/'dataset.json';data.write_text('{}')
   rows=[{'sample_id':str(i),'horizon_sec':300,'outcome_receipt_digest':str(i),'anchor_ms':100+i,
          'target_class':i%7,'teacher_probs':None} for i in range(n)]
   ids=[row_id(r) for r in rows]
   cfg={'objective':t.OBJECTIVE,'profile':PROFILE,'base_directory':str(base),'output_directory':str(root/'out'),
        'baseline_directory':str(root/'baseline'),'parent_directory':str(parent),'parent_id':manifest['candidate_id'],
        'dataset':str(data),'dataset_sha256':sha_file(data),'window':{'training_end_ms':999},
        'training_row_ids':ids,'training_row_accounting_schema':SCHEMA,'native_binary':'fixture-only',
        'native_binary_sha256':'b'*64,'data_fingerprint':digest(ids),'generation':2}
   def encode(tok,selected):return {'x':torch.tensor([[int(r['sample_id'])/100,0.1,0.2] for r in selected])}
   replacements={'verify_baseline':lambda p:'d'*64,'verify_source_prefixes':lambda d:None,
                 'rows_for_training':lambda d,w:(rows,{}),'require_pocket4_gpu':lambda:torch.device('cpu'),
                 'load_verified_laya':lambda b,m:(TinyPretrained(),None,'a'*64),'encode_rows':encode,'ResidentNative':NativeFixture}
   for name,value in replacements.items():stack.enter_context(patch.object(t,name,value))
   stack.enter_context(patch.object(torch.cuda,'reset_peak_memory_stats'))
   stack.enter_context(patch.object(torch.cuda,'get_device_properties',return_value=SimpleNamespace(gcnArchName='CPU_SYNTHETIC_FIXTURE')))
   stack.enter_context(patch.object(torch.cuda,'max_memory_allocated',return_value=0))
   if rollback:
    original=ResidentDecisionCell.norm_report
    def reject(cell):return {**original(cell),'within_profile':False}
    stack.enter_context(patch.object(ResidentDecisionCell,'norm_report',reject))
   candidate=t.run_job(cfg,old_learner_verified=True);verify_candidate(cfg,candidate)
   loaded=json.loads((root/'out/candidate.json').read_text());verify_candidate(cfg,loaded)
   self.assertEqual(loaded['row_accounting']['gradient_attempted_count'],n)
   self.assertEqual(len(loaded['steps']),(n+1)//2)
   self.assertEqual(loaded['row_accounting']['applied_count'],0 if rollback else n)
   self.assertEqual(loaded['changed_parameters']==0,rollback)
 def test_32_reserved_rows_have_32_real_backward_pass_inputs(self):self.run_fixture(32)
 def test_odd_last_batch_trains_all_21_rows(self):self.run_fixture(21)
 def test_rejected_steps_are_logged_as_rollback_not_applied(self):self.run_fixture(16,True)

if __name__=='__main__':unittest.main()
