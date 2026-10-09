import unittest
from historical_training.audit_offline import observed_brier,training_prior,fixed_trade_stress,input_text
from historical_training.common import EvidenceError
class AuditTests(unittest.TestCase):
    def test_uniform_brier(self):self.assertAlmostEqual(observed_brier([1/3]*3,2),2/3)
    def test_probability_rejects_nonfinite(self):
        with self.assertRaises(EvidenceError):observed_brier([float('nan'),0,1],0)
    def test_probability_rejects_nonunit_sum(self):
        with self.assertRaises(EvidenceError):observed_brier([.1,.2,.3],0)
    def test_prior_training_only(self):
        train=[{'class':0},{'class':2},{'class':2}]
        self.assertEqual(training_prior(train),[1/3,0,2/3])
    def test_bad_class_rejected(self):
        with self.assertRaises(EvidenceError):training_prior([{'class':-1}])
    def test_fixed_trade_cost_monotone_identity(self):
        trades=[{'time':10,'side':'BUY','simulated_net_usd':5},{'time':20,'side':'SELL','simulated_net_usd':-10}]
        a=fixed_trade_stress(trades,0);b=fixed_trade_stress(trades,1)
        self.assertEqual(a['trade_identity_sha256'],b['trade_identity_sha256'])
        self.assertEqual(b['net_usd'],a['net_usd']-20)
    def test_duplicate_trade_rejected(self):
        r={'time':10,'side':'BUY','simulated_net_usd':5}
        with self.assertRaises(EvidenceError):fixed_trade_stress([r,r],1)
    def test_future_targets_not_in_features(self):
        row={'features':[.1]*25,'target_bps':5};a=input_text(row);row['target_bps']=500
        self.assertEqual(a,input_text(row))
if __name__=='__main__':unittest.main()
