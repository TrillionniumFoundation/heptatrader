"""Offline net-target & fixed-decision cohort adversarial unit checks."""
import unittest
import numpy as np
from historical_training.cost_net_train_research import (
    action_target, fixed_cohort, predict_two_sides, COMMISSION_BPS,
    SPREAD_FLOOR_BPS)


class CostNetTrainTests(unittest.TestCase):
    def test_future_net_labels_distinguish_no_trade_and_direction(self):
        a=action_target(np.array([0.,4.,-5.,2.]),
                        np.array([0.,-4.,5.,1.]),2.)
        self.assertEqual(a.tolist(),[0,1,2,0])

    def test_identical_trade_cohort_cost_stress_monotonic(self):
        x=fixed_cohort(np.array([4.,-2.,3.,-3.]),
                       np.array([-2.,8.,1.,-4.]),
                       np.array([5.,1.,3.,7.]),
                       np.array([-5.,10.,-3.,-7.]),0,(0,.5,2))
        self.assertEqual([s['trades'] for s in x],[3,3,3])
        self.assertGreater(x[0]['net_usd'],x[1]['net_usd'])
        self.assertGreater(x[1]['net_usd'],x[2]['net_usd'])
        self.assertAlmostEqual(x[0]['net_usd']-x[2]['net_usd'],3*2*100000/10000)

    def test_no_trade_when_predictions_below_cost(self):
        x=fixed_cohort(np.array([.5,-2.]),np.array([.1,-3.]),
                       np.array([100,100]),np.array([100,100]),2)
        self.assertTrue(all(a['trades']==0 and a['net_usd']==0 for a in x))

    def test_no_positive_validation_forces_no_trade(self):
        x=np.linspace(0,1,240).reshape(-1,1)
        realized_long=np.full(240,-8.)
        realized_short=np.full(240,-8.)
        result=predict_two_sides(x,realized_long,realized_short,
                                 np.arange(0,100),np.arange(100,150),np.arange(150,200))
        self.assertFalse(result['validation_passed'])
        self.assertEqual(result['test_scenarios'][0]['trades'],0)


if __name__=='__main__':
    unittest.main()
