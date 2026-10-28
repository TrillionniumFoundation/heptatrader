import copy,math,unittest
import torch
from laya_cell.integrity import ContractError,digest
from laya_cell.temporal_fusion_model import PROFILE,TemporalLayaFusion,padded_sequences,training_loss,brier,entropy
from laya_cell.temporal_fusion_shadow import RULE,assess,prob_score

class MetadataCompatibilityTests(unittest.TestCase):
 def test_legacy_exports_share_the_metadata_objects(self):
  from laya_cell import install_temporal_fusion as installer,temporal_fusion_metadata as metadata
  from laya_cell import temporal_fusion_model as model,temporal_fusion_shadow as shadow
  for exported in (PROFILE,model.PROFILE,shadow.PROFILE,installer.PROFILE):self.assertIs(exported,metadata.PROFILE)
  for exported in (RULE,shadow.RULE,installer.RULE):self.assertIs(exported,metadata.RULE)
  self.assertEqual(model.profile_id(),digest(metadata.PROFILE))


def sample(n=6):
 return {"native_episode":{"rows":[[0.01*(i+j) for j in range(16)] for i in range(n)]}}
class ModelTests(unittest.TestCase):
 def setUp(self):torch.manual_seed(1);self.m=TemporalLayaFusion()
 def test_shapes_and_normalization(self):
  x,l=padded_sequences([sample(6),sample(9)]);p=torch.tensor([[.1,.1,.1,.2,.2,.2,.1],[.05,.1,.15,.2,.2,.2,.1]])
  o=self.m(x,l,p);self.assertEqual(o["fusion_probs"].shape,(2,7));torch.testing.assert_close(o["fusion_probs"].sum(-1),torch.ones(2))
  self.assertEqual(o["fusion_embedding"].shape,(2,32));self.assertTrue(((o["gate_numeric_weight"]>0)&(o["gate_numeric_weight"]<1)).all())
 def test_fusion_is_exact_convex_mixture(self):
  x,l=padded_sequences([sample()]);p=torch.tensor([[.1,.1,.1,.2,.2,.2,.1]]);o=self.m(x,l,p);g=o["gate_numeric_weight"][:,None]
  torch.testing.assert_close(o["fusion_probs"],g*o["numeric_probs"]+(1-g)*p)
 def test_uncertainty_bounded(self):
  p=torch.tensor([[1/7]*7,[1.,0,0,0,0,0,0]]);u=entropy(p);self.assertLessEqual(float(u.max()),1.000001);self.assertGreaterEqual(float(u.min()),-1e-6)
 def test_variable_length_causal_sequence(self):
  x,l=padded_sequences([sample(2),sample(24)]);self.assertEqual(tuple(x.shape),(2,24,16));self.assertEqual(l.tolist(),[2,24])
 def test_sequence_over_bound_rejected(self):
  with self.assertRaises(ContractError):padded_sequences([sample(25)])
 def test_target_loss_finite(self):
  x,l=padded_sequences([sample(),sample()]);p=torch.full((2,7),1/7);o=self.m(x,l,p);loss,_=training_loss(o,torch.tensor([1,5]));self.assertTrue(torch.isfinite(loss))
 def test_no_authority_in_profile(self):self.assertFalse(PROFILE["automatic_trading"]);self.assertFalse(PROFILE["actor_update"])

class AssessmentTests(unittest.TestCase):
 def plan(self):
  p={"registered_at_ms":1,"start_ms":2,"end_ms":100,"candidate_id":"c","laya_candidate_id":"l","period_sha256":"p","rule":copy.deepcopy(RULE),"automatic_selection":False,"trade_permission":False}
  p["plan_id"]=digest(p);return p
 def rows(self,n=12,fusion=.2,laya=.8,numeric=.7):
  return [{"status":"OBSERVED","slot":i,"target_class":i%3,"fusion_brier":fusion,"laya_brier":laya,"numeric_brier":numeric,
           "gate_numeric_weight":.5,"fusion_uncertainty":.4} for i in range(n)]
 def test_clear_future_win_is_shadow_only(self):
  a=assess(self.plan(),self.rows(),100);self.assertEqual(a["state"],"EXPERIMENTAL_FUSION_WIN_SHADOW_ONLY");self.assertFalse(a["automatic_selection"])
 def test_small_sample_never_qualifies(self):
  a=assess(self.plan(),self.rows(5),100);self.assertEqual(a["state"],"REJECTED");self.assertIn("INSUFFICIENT_OBSERVED",a["reasons"])
 def test_fusion_worse_than_laya_rejected(self):
  a=assess(self.plan(),self.rows(fusion=.9,laya=.8),100);self.assertEqual(a["state"],"REJECTED")
 def test_incomplete_window_is_pending(self):
  a=assess(self.plan(),self.rows(8),50);self.assertEqual(a["state"],"PENDING")
 def test_censor_budget_enforced(self):
  rows=self.rows(10)+[{"status":"CENSORED","slot":10,"reason":"GAP"},{"status":"CENSORED","slot":11,"reason":"GAP"},{"status":"CENSORED","slot":12,"reason":"GAP"}]
  self.assertIn("TOO_MANY_CENSORED",assess(self.plan(),rows,100)["reasons"])
 def test_probability_validation(self):
  with self.assertRaises(ContractError):prob_score([.5,.5],0)

if __name__=="__main__":unittest.main(verbosity=2)
