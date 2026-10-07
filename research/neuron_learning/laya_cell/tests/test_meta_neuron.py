"""Synthetic contract tests only; never performance or trading evidence."""
import math,unittest
import torch
from laya_cell.integrity import ContractError
from laya_cell.meta_neuron import PROFILE,SparseGraphMetaNeuron,canonical_nodes,tensorize

def node(name,shift=0.):
 emb=[0.]*32;emb[0]=math.sqrt(max(0.,1-shift*shift));emb[1]=shift
 probs=[.1,.1,.1,.4,.1,.1,.1]
 return {"node_id":name,"embedding":emb,"probs":probs,"uncertainty":.7,
  "effect_authority":False,"evidence":{"effect_authority":False,"input_id":"a"*64,
  "sample_id":"b"*64,"source_prefixes":[]}}

class ContractTests(unittest.TestCase):
 def test_two_existing_style_nodes_validate(self):self.assertEqual([x["node_id"] for x in canonical_nodes([node("numeric"),node("laya",.2)])],["laya","numeric"])
 def test_effect_authority_is_impossible(self):
  x=node("x");x["effect_authority"]=True
  with self.assertRaisesRegex(ContractError,"AUTHORITY"):canonical_nodes([x])
 def test_causal_input_mismatch_is_rejected(self):
  a=node("a");b=node("b");b["evidence"]["input_id"]="c"*64
  with self.assertRaisesRegex(ContractError,"CAUSAL"):canonical_nodes([a,b])
 def test_bad_simplex_rejected(self):
  x=node("x");x["probs"][0]=.9
  with self.assertRaisesRegex(ContractError,"SIMPLEX"):canonical_nodes([x])
 def test_bad_embedding_norm_rejected(self):
  x=node("x");x["embedding"]=[0.]*32
  with self.assertRaisesRegex(ContractError,"NORM"):canonical_nodes([x])
 def test_profile_has_no_trading_permission(self):self.assertFalse(PROFILE["automatic_trading"]);self.assertFalse(PROFILE["effect_authority"])

class RouterTests(unittest.TestCase):
 def run(self,n=5,k=2):
  torch.manual_seed(7);nodes=[node("n%02d"%i,min(.05*i,.5)) for i in range(n)]
  _,e,p,u=tensorize(nodes,torch.device("cpu"));m=SparseGraphMetaNeuron(k);o=m(e,p,u);return m,o
 def test_topk_is_bounded(self):
  _,o=self.run();self.assertEqual(o["selected_indices"].numel(),2);self.assertAlmostEqual(float(o["router_weights"].sum()),1.,places=6)
 def test_probability_output_is_normalized(self):
  _,o=self.run();self.assertTrue(torch.isfinite(o["probs"]).all());self.assertAlmostEqual(float(o["probs"].sum()),1.,places=6)
 def test_uncertainty_is_bounded(self):
  _,o=self.run();self.assertGreaterEqual(float(o["uncertainty"]),0);self.assertLessEqual(float(o["uncertainty"]),1)
 def test_one_node_still_uses_same_contract(self):
  _,o=self.run(1,4);self.assertEqual(o["selected_indices"].numel(),1)
 def test_gradient_lives_only_in_meta_prototype(self):
  m,o=self.run();(-torch.log(o["probs"][3])).backward();self.assertTrue(any(p.grad is not None for p in m.parameters()))
 def test_no_order_or_broker_method(self):
  m,_=self.run();names=set(dir(m));self.assertFalse({"submit","order","broker","paper_cmd"}&names)

if __name__=="__main__":unittest.main(verbosity=2)
