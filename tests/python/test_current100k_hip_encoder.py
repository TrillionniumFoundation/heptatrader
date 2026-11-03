"""GPU cleanup tests without loading Torch, a model, or a GPU context."""
import sys,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'research'),str(ROOT/'research/neuron_learning')]
from laya_cell.current100k_hip_encoder import WarmHipEncoder,environment_is_bound
from laya_cell.integrity import ContractError

class HipLifecycleTests(unittest.TestCase):
 def encoder(self,available=True,residual=0):
  e=WarmHipEncoder.__new__(WarmHipEncoder)
  e.raw=object();e.tokenizer=object();e.closed=False;e.proven=True;e.allocation_before=0
  state={'workspace':33554432,'events':[]}
  def allocated():return (1000000000 if e.raw is not None else 0)+state['workspace']+residual
  def sync():self.assertIsNone(e.raw);state['events'].append('sync')
  def clear():state['events'].append('clear');state['workspace']=0
  def empty():state['events'].append('empty')
  cuda=SimpleNamespace(memory_allocated=allocated,max_memory_allocated=lambda:1614848000,synchronize=sync,empty_cache=empty)
  e.torch=SimpleNamespace(cuda=cuda,_C=SimpleNamespace(**({'_cuda_clearCublasWorkspaces':clear} if available else {})))
  return e,state
 def test_workspace_released_after_tensors_and_sync(self):
  e,state=self.encoder();r=e.close()
  self.assertEqual(r['after_model_gc'],33554432);self.assertEqual(r['after_workspace_clear'],0)
  self.assertEqual(r['after_empty_cache'],0);self.assertTrue(r['all_model_allocations_released'])
  self.assertEqual(state['events'],['sync','clear','empty','sync']);self.assertEqual(e.close(),r)
 def test_missing_clear_api_cannot_relax_zero_allocation(self):
  e,_=self.encoder(available=False)
  with self.assertRaisesRegex(ContractError,'TENSORS_STILL_LIVE'):e.close()
  self.assertFalse(e.closed);self.assertEqual(e.cleanup_receipt['after_empty_cache'],33554432)
 def test_unexplained_residual_is_still_failure(self):
  e,_=self.encoder(residual=1)
  with self.assertRaisesRegex(ContractError,'TENSORS_STILL_LIVE:1'):e.close()
  self.assertFalse(e.closed)
 def test_unproven_runtime_cannot_claim_clean_release(self):
  e,_=self.encoder();e.proven=False
  with self.assertRaisesRegex(ContractError,'UNPROVEN_HIP'):e.close()
 def test_bound_environment_requires_python_manifest_and_library_prefix(self):
  hip=SimpleNamespace(ENV_PIN='TEST_HIP_PIN');env={'TEST_HIP_PIN':'a'*64,'LD_LIBRARY_PATH':'/accepted:/original'}
  with patch.dict('os.environ',env,clear=True):
   self.assertTrue(environment_is_bound({'python':sys.executable},hip,env))
   self.assertFalse(environment_is_bound({'python':'/wrong/python'},hip,env))
  with patch.dict('os.environ',{'TEST_HIP_PIN':'a'*64,'LD_LIBRARY_PATH':'/wrong:/accepted'},clear=True):
   self.assertFalse(environment_is_bound({'python':sys.executable},hip,env))
if __name__=='__main__':unittest.main()
