import copy,unittest
import numpy as np
from historical_training.value_net_cpu_development import vector,fit,predict,score,FEATURES

def request():
 return {'started_at_ms':25000,'history_snapshot':[{'bid':1+i*.00001,'ask':1.00002+i*.00001,'mid':1.00001+i*.00001,'observed_at_ms':(i+1)*1000} for i in range(24)]}
class ValueNetTest(unittest.TestCase):
 def mutate(self,fn):
  r=request();fn(r)
  with self.assertRaises(ValueError):vector(r,'BUY')
 def test_valid(self):self.assertEqual(vector(request(),'BUY').shape,(len(FEATURES),))
 def test_unknown_side(self):
  with self.assertRaises(ValueError):vector(request(),'HOLD')
 def test_future_quote(self):self.mutate(lambda r:r['history_snapshot'][-1].update(observed_at_ms=26000))
 def test_duplicate_time(self):self.mutate(lambda r:r['history_snapshot'][-1].update(observed_at_ms=23000))
 def test_crossed_quote(self):self.mutate(lambda r:r['history_snapshot'][-1].update(bid=2))
 def test_nonfinite(self):self.mutate(lambda r:r['history_snapshot'][-1].update(mid=float('nan')))
 def test_wrong_mid(self):self.mutate(lambda r:r['history_snapshot'][-1].update(mid=1.5))
 def test_wrong_count(self):self.mutate(lambda r:r['history_snapshot'].pop())
 def test_boolean_quote(self):self.mutate(lambda r:r['history_snapshot'][-1].update(bid=True))
 def test_future_label_fields_never_features(self):
  r=request();a=vector(r,'BUY');r.update(net_usd=1e20,actual_commissions_usd=1e15,reward={'outcome':123});np.testing.assert_array_equal(a,vector(r,'BUY'))
 def test_side_condition_not_opposite_reward(self):
  a=vector(request(),'BUY');b=vector(request(),'SELL');np.testing.assert_array_equal(a[1:14],b[1:14]);np.testing.assert_array_equal(a[-5:],-b[-5:])
 def test_training_deterministic(self):
  x=np.arange(1000).reshape(50,20)/100.;y=np.arange(50.)-25;np.testing.assert_array_equal(predict(fit(x,y),x),predict(fit(x,y),x))
 def test_small_training_refused(self):
  with self.assertRaises(ValueError):fit(np.ones((3,19)),np.zeros(3))
 def test_zero_forecast_not_zero_realized_pnl(self):
  r=score(np.array([-4.,-6.]),np.array([0.,0.]));self.assertEqual(r['mae_usd'],5);self.assertEqual(r['mean_actual_net_usd'],-5)
if __name__=='__main__':unittest.main()
