import unittest
import numpy as np
from historical_training.nonlinear_fx_cost_study import matrices,threshold_on_validation,NOISE_COST

class NonlinearTests(unittest.TestCase):
 def test_no_label_data_in_features(self):
  n=200;times=np.arange(n,dtype=np.int64)*300
  price=1+np.arange(n)*.00001
  kwargs=(times,price.copy(),price.copy(),price.copy()*.99999,
          price.copy()*1.00001,np.ones(n)*.00002,np.ones(n)*.0001,3)
  idx,x,y,target,l,s,sp=matrices(*kwargs)
  before=x.copy(),y.copy()
  # Changing ONLY a future entry cannot alter features for earlier anchor.
  modified=list(kwargs);modified[2]=price.copy()
  modified[2][100:]+=0.1
  idx2,x2,y2,target2,l2,s2,sp2=matrices(*modified)
  self.assertTrue(np.array_equal(idx,idx2))
  self.assertTrue(np.array_equal(x,x2) and np.array_equal(y,y2))
  self.assertFalse(np.array_equal(target,target2))
 def test_no_trade_threshold_fallback(self):
  n=60;pred=np.ones(n)*2;spread=np.zeros(n)
  self.assertIsNone(threshold_on_validation(pred,spread,np.ones(n)*-1,np.ones(n)*-1))
if __name__=='__main__':unittest.main()
