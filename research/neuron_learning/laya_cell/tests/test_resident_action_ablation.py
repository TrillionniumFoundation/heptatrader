import math
import unittest
import torch
from laya_cell.resident_action_ablation import branch_gradient_diagnostics, freeze_weight


class BranchGradientTests(unittest.TestCase):
    def test_disjoint_preserves_grad_and_backward(self):
        p=torch.nn.Parameter(torch.tensor(2.,dtype=torch.float64))
        a=torch.nn.Parameter(torch.tensor(3.,dtype=torch.float64))
        p.grad=torch.full_like(p,13);a.grad=torch.full_like(a,17)
        pl,al=p.square()/2,a.square()/2
        d=branch_gradient_diagnostics(pl,al,[("p",p),("a",a)],.5)
        self.assertEqual(d["graph_shared_tensors"],0);self.assertEqual(d["overlap_numel"],0)
        self.assertIsNone(d["shared_cosine"]);self.assertEqual(d["global_cosine"],0)
        self.assertEqual(d["price_l2"],2);self.assertEqual(d["action_l2"],3)
        self.assertEqual(d["combined_l2"],2.5)
        self.assertAlmostEqual(d["clip_factor"],1/(2.5+1e-6))
        self.assertEqual(p.grad.item(),13);self.assertEqual(a.grad.item(),17)
        p.grad=a.grad=None;(pl+.5*al).backward()
        self.assertEqual(p.grad.item(),2);self.assertEqual(a.grad.item(),1.5)

    def test_shared_conflict_unused_frozen(self):
        p=torch.nn.Parameter(torch.tensor(2.,dtype=torch.float64))
        unused=torch.nn.Parameter(torch.tensor(7.,dtype=torch.float64))
        frozen=torch.nn.Parameter(torch.tensor(9.),requires_grad=False)
        d=branch_gradient_diagnostics(p.square()/2,(p-4).square()/2,
                                     [("p",p),("unused",unused),("frozen",frozen)],1)
        self.assertEqual(d["trainable_tensors"],2);self.assertEqual(d["graph_shared_tensors"],1)
        self.assertEqual(d["shared_cosine"],-1);self.assertEqual(d["overlap_numel"],1)
        self.assertEqual(d["combined_l2"],0);self.assertEqual(d["clip_factor"],1)
        self.assertIsNone(p.grad);self.assertIsNone(unused.grad)

    def test_weight_train_boundary_and_nonfinite_guards(self):
        p=torch.nn.Parameter(torch.tensor(2.));a=torch.nn.Parameter(torch.tensor(50.))
        named=[("p",p),("a",a)]
        records=[branch_gradient_diagnostics(k*p.square()/2,k*a.square()/2,named,.01) for k in (1,2)]
        result=freeze_weight(records,provenance={"split":"train"})
        self.assertEqual(result["basis"],"global_clip_budget_heuristic")
        self.assertAlmostEqual(result["action_weight"],.04)
        with self.assertRaises(ValueError):freeze_weight(records,provenance={"split":"holdout"})
        with self.assertRaises(ValueError):
            freeze_weight([branch_gradient_diagnostics(p.square(),None,named,1)],provenance={"split":"train"})
        z=torch.nn.Parameter(torch.tensor(0.))
        with self.assertRaises(FloatingPointError):
            branch_gradient_diagnostics(z.sqrt(),None,[("z",z)],0)
        d=branch_gradient_diagnostics(p*1e20,None,[("p",p)],0)
        self.assertTrue(math.isfinite(d["price_l2"]))
        self.assertTrue(math.isclose(d["price_l2"],1e20,rel_tol=1e-6))
