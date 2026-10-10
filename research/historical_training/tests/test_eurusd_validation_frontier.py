"""Validation-only research metrics never upgrade previously reviewed history."""
import unittest

import numpy as np

from historical_training.eurusd_validation_frontier import (
    MIN_VALIDATION_TRADES, threshold_frontier,
)
from historical_training.eurusd_walkforward_cost import THRESHOLDS


class ValidationFrontierTests(unittest.TestCase):
    def test_positive_validation_requires_at_least_thirty_real_decisions(self):
        at=np.arange(45,dtype=np.int64)*900 + 1000
        prediction=np.full(45,10.,dtype=float)
        actual=np.full(45,12.,dtype=float)
        values=threshold_frontier(at,prediction,actual,3.0)
        zero=values[0]
        self.assertEqual(zero['threshold_bps'],THRESHOLDS[0])
        self.assertEqual(zero['trades'],45)
        self.assertEqual(zero['gross_usd_hypothetical'],5400.)
        self.assertEqual(zero['net_usd_hypothetical'],4050.)
        self.assertEqual(zero['cost_usd_hypothetical'],1350.)
        self.assertTrue(zero['qualifies_positive_validation'])
        self.assertEqual(MIN_VALIDATION_TRADES,30)

    def test_tiny_profitable_subset_cannot_override_no_trade(self):
        at=np.arange(45,dtype=np.int64)*900 + 1000
        prediction=np.zeros(45)
        prediction[:3]=10.
        actual=np.zeros(45)
        actual[:3]=12.
        values=threshold_frontier(at,prediction,actual,3.)
        one=next(v for v in values if v['threshold_bps']==1.)
        self.assertEqual(one['trades'],3)
        self.assertGreater(one['net_usd_hypothetical'],0)
        self.assertTrue(one['small_sample_positive_not_qualified'])
        self.assertFalse(one['qualifies_positive_validation'])
        self.assertFalse(any(v['qualifies_positive_validation'] for v in values))

    def test_unprofitable_trading_explains_no_trade(self):
        at=np.arange(60,dtype=np.int64)*900 + 1000
        prediction=np.full(60,0.1)
        actual=np.full(60,0.5)
        row=threshold_frontier(at,prediction,actual,3.69709)[0]
        self.assertEqual(row['trades'],60)
        self.assertGreater(row['gross_usd_hypothetical'],0)
        self.assertLess(row['net_usd_hypothetical'],0)
        self.assertFalse(row['qualifies_positive_validation'])

    def test_nonoverlap_costs_match_original_simulator(self):
        at=np.arange(60,dtype=np.int64)*300+1000
        p=np.full(60,10.)
        y=np.full(60,6.)
        row=threshold_frontier(at,p,y,2.5)[0]
        self.assertEqual(row['trades'],20)
        self.assertAlmostEqual(
            row['gross_usd_hypothetical']-row['net_usd_hypothetical'],
            20*2.5*10,places=2
        )


if __name__=='__main__':
    unittest.main()
