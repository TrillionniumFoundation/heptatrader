import unittest
import numpy as np
from historical_training.cost_horizon_study import fold_bounds,split_indices,score,select
class StudyTests(unittest.TestCase):
 def test_embargo_and_nonoverlap(self):
  i=np.arange(24,1000)
  for h in (3,6,12):
   tr,va,te=split_indices(i,1000,h,1)
   self.assertLess(max(tr)+h+1,min(va))
   self.assertLess(max(va)+h+1,min(te))
   self.assertTrue(np.all(np.diff(va)>h))
   self.assertTrue(np.all(np.diff(te)>h))
 def test_spread_and_fee_discourage_trades(self):
  sig=np.array([0.,1.,-1.,3.]);sp=np.ones(4)*1.5
  self.assertIsNone(select(sig,sp,np.zeros(4),np.zeros(4),.9))
  self.assertEqual(score(sig,sp,np.zeros(4),np.zeros(4),0,.9)['trades'],1)
 def test_no_trade_zero_and_no_negative_promotion(self):
  n=80;z=np.ones(n)*4.;sp=np.zeros(n)
  self.assertIsNone(select(z,sp,np.ones(n)*-1,np.ones(n)*-1,.5))
  self.assertEqual(score(z,sp,np.zeros(n),np.zeros(n),100,.5)['net_usd'],0)
if __name__=='__main__':unittest.main()
