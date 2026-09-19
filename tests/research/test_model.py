from decimal import Decimal as D
import unittest
from hepta_research.model import (number, TargetPolicy, PositionObservation,
    ResearchLedger, HypotheticalFill, ReplayBar, replay, performance)

class TargetTests(unittest.TestCase):
    def setUp(self):
        self.policy=TargetPolicy(D(100),D(10),D(1))
    def obs(self,q=0,**kw):
        args=dict(quantity=D(q),observed_at_ms=1000,complete=True,has_active_orders=False)
        args.update(kw)
        return PositionObservation(**args)
    def test_positive_and_negative_cap(self):
        self.assertEqual(self.policy.delta(50,self.obs(),1001),10)
        self.assertEqual(self.policy.delta(-50,self.obs(),1001),-10)
    def test_reversal_closes_first_without_assuming_fill(self):
        self.assertEqual(self.policy.delta(-5,self.obs(3),1001),-3)
        self.assertEqual(self.policy.delta(-5,self.obs(0),1001),-5)
    def test_no_change(self):
        self.assertEqual(self.policy.delta(3,self.obs(3),1001),0)
    def test_invalid_projection(self):
        for kw in ({"complete":False},{"has_active_orders":True},{"observed_at_ms":0},
                   {"observed_at_ms":2000},{"observed_at_ms":True}):
            with self.subTest(kw=kw),self.assertRaises(ValueError): self.policy.delta(5,self.obs(**kw),1001)
    def test_staleness(self):
        with self.assertRaises(ValueError): self.policy.delta(5,self.obs(),2001)
    def test_lot_and_target_bounds(self):
        for q in (101,-101,"0.5"):
            with self.subTest(q=q),self.assertRaises(ValueError):self.policy.delta(q,self.obs(),1001)
    def test_long_only(self):
        with self.assertRaises(ValueError): TargetPolicy(D(100),D(10),D(1),long_only=True).delta(-1,self.obs(),1001)
    def test_nonfinite_and_large_decimals(self):
        for n in ("nan","Infinity",True,"1e100","1e-100","bad"):
            with self.subTest(n=n),self.assertRaises(ValueError):number(n)

class ReplayTests(unittest.TestCase):
    def bars(self):
        return [ReplayBar(0,10,D(10),D(11)),ReplayBar(10,20,D(12),D(13)),ReplayBar(20,30,D(14),D(15))]
    def test_next_bar_not_same_bar(self):
        r=replay(self.bars(),lambda b:2,capital=100,max_abs_target=10)
        self.assertEqual(r["fills"],[HypotheticalFill(10,D(2),D(12),D(0))])
        self.assertEqual(r["equity"],[100,102,106]);self.assertEqual(r["mode"],"OFFLINE_HYPOTHETICAL")
    def test_cost_and_multiplier(self):
        r=replay(self.bars(),lambda b:2,capital=100,max_abs_target=10,multiplier=10,slippage=1,fee_per_unit="0.5")
        self.assertEqual(r["fills"][0].price,13);self.assertEqual(r["fees"],1)
        self.assertEqual(r["equity"], [100,99,139])
    def test_unfilled_final_target(self):
        r=replay(self.bars()[:1],lambda b:2,capital=100,max_abs_target=10)
        self.assertEqual(r["fills"],[]);self.assertEqual(r["pending_target"],2)
    def test_incomplete_final_bar_never_signals(self):
        seen=[]
        bars=self.bars()[:2]+[ReplayBar(20,30,D(14),D(15),False)]
        r=replay(bars,lambda b:seen.append(b) or 2,capital=100,max_abs_target=10)
        self.assertEqual(len(seen),2);self.assertIsNone(r["pending_target"])
    def test_overlapping_and_post_incomplete_rejected(self):
        for bars in ([ReplayBar(0,10,D(1),D(1)),ReplayBar(9,20,D(1),D(1))],
                     [ReplayBar(0,10,D(1),D(1),False),ReplayBar(10,20,D(1),D(1))]):
            with self.subTest(bars=bars),self.assertRaises(ValueError):replay(bars,lambda b:1,capital=100,max_abs_target=10)
    def test_target_cap(self):
        with self.assertRaises(ValueError):replay(self.bars(),lambda b:11,capital=100,max_abs_target=10)
    def test_ledger_short_and_reversal(self):
        l=ResearchLedger(100,2);l.fill(HypotheticalFill(1,D(-2),D(10),D(1)))
        self.assertEqual(l.equity(9),103)
        l.fill(HypotheticalFill(2,D(3),D(8),D(1)));self.assertEqual(l.quantity,1);self.assertEqual(l.equity(8),106)
    def test_ledger_rejected_fill_does_not_mutate(self):
        l=ResearchLedger(100); l.fill(HypotheticalFill(2,D(1),D(10),D(0)))
        before=(l.cash,l.quantity,l.fees,l.last_fill_us)
        with self.assertRaises(ValueError):l.fill(HypotheticalFill(1,D(1),D(10),D(0)))
        self.assertEqual(before,(l.cash,l.quantity,l.fees,l.last_fill_us))
    def test_negative_historical_price(self):
        l=ResearchLedger(100);l.fill(HypotheticalFill(0,D(1),D(-10),D(0)))
        self.assertEqual(l.equity(-5),105)
    def test_metrics_defined_and_undefined(self):
        r=performance([100,120,90],252); self.assertAlmostEqual(r["max_drawdown"],.25)
        self.assertAlmostEqual(r["total_return"],-.1);self.assertIsNotNone(r["sharpe"])
        for values in ([100],[100,100,100]):
            r=performance(values,252);self.assertIsNone(r["sharpe"]);self.assertIsNone(r["sortino"])
    def test_no_synthetic_funding_or_invalid_equity(self):
        for values in ([],[0],[100,-1]):
            with self.subTest(values=values),self.assertRaises(ValueError):performance(values,252)


class DecimalBoundaryTests(unittest.TestCase):
    def test_absolute_limit_is_not_rounded_by_decimal_context(self):
        from hepta_research.model import number
        with self.assertRaises(ValueError):number("1000000000000000000.000000000000000001")
        with self.assertRaises(ValueError):number("-1000000000000000000.000000000000000001")

    def test_small_lots_at_large_position_remain_exact(self):
        from hepta_research.model import TargetPolicy, PositionObservation
        policy=TargetPolicy(D("1e18"),D("0.000000000000000001"),D("0.000000000000000001"))
        observation=PositionObservation(D("999999999999999999.999999999999999999"),1000,True,False)
        self.assertEqual(policy.delta(D("1e18"),observation,1000),D("0.000000000000000001"))
