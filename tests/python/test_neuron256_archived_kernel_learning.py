"""Torch-backed archive parity; executed by the explicit learning lane."""
import unittest
from dataclasses import replace
import torch
from neuron_graph_v2.archive import ArchivedGraph
from neuron_graph_v2.tests.test_neuron256 import node, T

class ArchivedKernelTests(unittest.TestCase):
 def fixture(self):
  torch.manual_seed(197);m=ArchivedGraph()
  a=node('a-numeric',[.05,.05,.1,.5,.1,.1,.1]);b=replace(node('b-semantic',[.1,.1,.1,.4,.1,.1,.1]),kind='semantic')
  e=torch.tensor([[a.embedding,b.embedding]]);p=torch.tensor([[a.probabilities,b.probabilities]])
  return m,[a,b],e,p
 def test_exact_live_soft_kernel_parity(self):
  m,n,e,p=self.fixture()
  with torch.no_grad():m.router.weight.normal_(0,.1);m.weight_head.weight.normal_(0,.1)
  a=m(n,T+300);b=m.archived(e,p)
  self.assertLess(float((a['probabilities']-b['probabilities'][0]).abs().max()),1e-6)
  self.assertLess(float((a['embedding']-b['embedding'][0]).abs().max()),1e-6)
  self.assertFalse(b['live_packet_emitted']);self.assertNotIn('available_at_ms',b)
 def test_shape_refused(self):
  m,n,e,p=self.fixture()
  with self.assertRaises(ValueError):m.archived(e[:,:,:32],p)
 def test_simplex_refused(self):
  m,n,e,p=self.fixture()
  with self.assertRaises(ValueError):m.archived(e,p*2)
 def test_nonfinite_refused(self):
  m,n,e,p=self.fixture();e[0,0,0]=float('nan')
  with self.assertRaises(ValueError):m.archived(e,p)
 def test_nonunit_embedding_refused(self):
  m,n,e,p=self.fixture()
  with self.assertRaises(ValueError):m.archived(e*2,p)
 def test_autograd_path(self):
  m,n,e,p=self.fixture();out=m.archived(e,p);loss=-out['probabilities'][0,3].log();loss.backward()
  self.assertIsNotNone(m.weight_head.weight.grad);self.assertTrue(torch.isfinite(m.weight_head.weight.grad).all())

 def test_noncontiguous_checkpoint_preserves_values(self):
  from historical_training.neuron256_market_trial import checkpoint_tensors
  source=torch.arange(20.).reshape(2,10).T;self.assertFalse(source.is_contiguous())
  result=checkpoint_tensors({'cost_weights':source})['cost_weights']
  self.assertTrue(result.is_contiguous());self.assertTrue(torch.equal(source,result))


if __name__ == "__main__":
    unittest.main()
