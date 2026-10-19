import itertools
import unittest
import torch
from torch.nn import functional as F
from laya_cell.resident_price_readout_diagnostics import (
    option_metrics, frozen_option_offset, unpermute_slots,
    solve_shared_readout_ball, joint_price_gap_bounds, action_gradient_parts,
)


class PriceReadoutTests(unittest.TestCase):
    def test_pairing_gain_matches_exhaustive_expected_nll(self):
        z=torch.tensor([[2.,0.,-1.],[-1.,1.,.5],[.4,-.7,.2]],dtype=torch.float64)
        y=torch.tensor([0,1,0])
        m=option_metrics(z,y)
        expected=sum(float(F.cross_entropy(z,torch.tensor(p))) for p in itertools.permutations(y.tolist()))/6
        self.assertAlmostEqual(m["analytic_random_pair_nll"],expected,places=13)
        self.assertAlmostEqual(m["conditional_pairing_gain"],expected-m["nll"],places=13)
        shifted=option_metrics(z+torch.tensor([5.,-3.,2.]),y)
        self.assertAlmostEqual(m["conditional_pairing_gain"],shifted["conditional_pairing_gain"],places=13)
        self.assertLess(abs(m["pairing_identity_residual"]),1e-13)

    def test_offset_preserves_conditional_residual_and_inputs(self):
        z=torch.tensor([[2.,0.,-1.],[-1.,1.,.5],[.4,-.7,.2]],dtype=torch.float64)
        original=z.clone();prior=[.1,.3,.6]
        offset,profile=frozen_option_offset(z,prior,split="train")
        self.assertAlmostEqual(float(offset.sum()),0.,places=13)
        expected=torch.tensor(prior,dtype=torch.float64).log()
        actual=(z+offset).mean(0);actual=actual-actual.mean()
        self.assertTrue(torch.allclose(actual,expected-expected.mean(),atol=1e-13,rtol=0))
        self.assertTrue(torch.equal(z,original))
        self.assertTrue(profile["mean_probability_match_not_guaranteed"])

    def test_slot_unpermutation_and_validation(self):
        canonical=torch.tensor([[1.,2.,3.],[4.,5.,6.]])
        for order in ([1,2,0],[2,1,0],[0,1,2]):
            self.assertTrue(torch.equal(unpermute_slots(canonical[:,order],order),canonical))
        for order in ([0,0,2],[0,1],[False,1,2]):
            with self.assertRaises(ValueError):unpermute_slots(canonical,order)

    def test_one_dimensional_convex_solution_and_gap(self):
        # Both labels favor the positive side. Exact optimum is delta=radius.
        h=torch.tensor([[[1.],[-1.]],[[2.],[-2.]]],dtype=torch.float64)
        z=torch.zeros(2,2,dtype=torch.float64);y=torch.zeros(2,dtype=torch.long)
        delta,r=solve_shared_readout_ball(h,z,y,.2,gap_tolerance=1e-12)
        self.assertTrue(r["converged"])
        self.assertAlmostEqual(float(delta[0]),.2,places=13)
        exact=float(F.cross_entropy(torch.tensor([[.2,-.2],[.4,-.4]],dtype=torch.float64),y))
        self.assertAlmostEqual(r["nll"],exact,places=13)
        self.assertLessEqual(r["nll_lower_bound_from_certificate"],exact+1e-13)

    def test_constant_shared_features_have_no_learning_direction(self):
        h=torch.ones(3,2,4,dtype=torch.float64)
        z=torch.tensor([[1.,0.],[0.,1.],[.5,0.]],dtype=torch.float64)
        delta,r=solve_shared_readout_ball(h,z,torch.tensor([0,1,0]),.2)
        self.assertEqual(float(delta.norm()),0.)
        self.assertEqual(r["iterations"],0)
        self.assertEqual(r["convex_gap"],0.)

    def test_two_layer_finite_updates_obey_cross_term_bound(self):
        gen=torch.Generator().manual_seed(71)
        x=torch.randn(5,3,6,generator=gen,dtype=torch.float64)
        w1=torch.randn(6,6,generator=gen,dtype=torch.float64)
        bias=torch.randn(6,generator=gen,dtype=torch.float64)
        w2=torch.randn(6,generator=gen,dtype=torch.float64)
        h=F.gelu(F.linear(x,w1,bias));z=h@w2
        pred=z.argmax(-1);y=(pred+1)%3
        bound=joint_price_gap_bounds(z,y,x,h,w2[:2],.03,.05)
        index=torch.arange(5)
        for _ in range(3):
            d1=torch.randn(2,6,generator=gen,dtype=torch.float64)
            d1=d1/d1.norm()*.03
            d2=torch.randn(6,generator=gen,dtype=torch.float64)
            d2=d2/d2.norm()*.05
            updated=w1.clone();updated[:2]+=d1
            after=F.gelu(F.linear(x,updated,bias))@(w2+d2)
            improvement=(after[index,y]-after[index,pred])-(z[index,y]-z[index,pred])
            for i,row in enumerate(bound["records"]):
                self.assertLessEqual(float(improvement[i]),row["total_bound"]+1e-12)

    def test_action_common_feature_gradient_is_explicit(self):
        x=torch.tensor([[2.,3.],[2.,3.],[2.,3.]],dtype=torch.float64)
        z=torch.zeros(3,2,dtype=torch.float64)
        r=action_gradient_parts(x,z,[True,False,False])
        self.assertEqual(r["conditional_feature_gradient_l2"],0.)
        self.assertLess(r["decomposition_error_l2"],1e-14)
        self.assertAlmostEqual(r["residual_mean"],1/6,places=13)

    def test_invalid_inputs_fail_before_solving(self):
        z=torch.zeros(2,3,dtype=torch.float64)
        with self.assertRaises(ValueError):option_metrics(z,torch.tensor([0.,1.]))
        with self.assertRaises(ValueError):option_metrics(z,torch.tensor([0,3]))
        with self.assertRaises(ValueError):frozen_option_offset(z,[.1,.2,.7],split="holdout")
        with self.assertRaises(ValueError):frozen_option_offset(z,[0.,.3,.7],split="train")
        with self.assertRaises(ValueError):frozen_option_offset(z,[.2,.3,.7],split="train")
        with self.assertRaises(ValueError):solve_shared_readout_ball(torch.zeros(2,3,4),z,[0,1],0.)
        with self.assertRaises(ValueError):option_metrics(torch.full((2,3),float("nan")),[0,1])


if __name__=="__main__":
    unittest.main()
