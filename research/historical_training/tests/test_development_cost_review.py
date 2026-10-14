import unittest
import numpy as np
from historical_training.development_cost_review import chosen_indices, metrics, ridge, infer
class ReviewTest(unittest.TestCase):
 def test_nonoverlapping_positions(self):
  a=np.array([0,300,600,900]);e=a+900;p=np.ones(4)
  self.assertEqual(chosen_indices(a,e,p,0).tolist(),[0,3])
 def test_abstention(self):
  a=np.arange(3);m=metrics(a,np.ones(3),np.ones(3),chosen_indices(a,a+1,np.ones(3),None),2)
  self.assertEqual(m['trades'],0);self.assertEqual(m['net_usd'],0);self.assertIsNone(m['mean_net_usd_per_trade'])
 def test_cost_increase_same_trades(self):
  a=np.arange(3);p=np.array([1.,-1.,1.]);y=np.array([3.,-2.,1.]);ids=np.arange(3)
  l=metrics(a,p,y,ids,1.);h=metrics(a,p,y,ids,2.)
  self.assertEqual(l['trade_set_sha256'],h['trade_set_sha256']);self.assertAlmostEqual(l['net_usd']-h['net_usd'],30.)
 def test_invalid_cost(self):
  for c in (float('nan'),-1.):
   with self.assertRaises(ValueError):metrics(np.arange(1),np.ones(1),np.ones(1),np.arange(1),c)
 def test_invalid_forecast(self):
  with self.assertRaises(ValueError):chosen_indices(np.array([0]),np.array([1]),np.array([np.nan]),0)
 def test_invalid_maturity(self):
  with self.assertRaises(ValueError):chosen_indices(np.array([1]),np.array([1]),np.ones(1),0)
 def test_training_deterministic(self):
  x=np.arange(160).reshape(80,2)/100.;y=np.sin(x[:,0]);a=infer(ridge(x,y),x);b=infer(ridge(x,y),x)
  np.testing.assert_array_equal(a,b)
 def test_invalid_train(self):
  with self.assertRaises(ValueError):ridge(np.ones((4,2)),np.ones(4))
if __name__=='__main__':unittest.main()
