import unittest
import torch
from laya_cell.resident_objective_v2_research import bounded_auxiliary_term


class AuxiliaryLossResearchTests(unittest.TestCase):
    def test_imbalanced_training_labels_disable_auxiliary(self):
        price=torch.tensor(2.65,requires_grad=True)
        logits=torch.tensor([[-2703.,0.],[0.,2703.]],requires_grad=True)
        all_labels=torch.tensor([0]+[1]*63)
        result,meta=bounded_auxiliary_term(price,logits,torch.tensor([0,1]),all_labels)
        self.assertFalse(meta['admitted'])
        self.assertEqual(float(result.detach()),0.)
        (price+result).backward()
        self.assertTrue(torch.isfinite(price.grad))
        self.assertIsNone(logits.grad)

    def test_saturated_balanced_logits_are_finite_and_capped(self):
        price=torch.tensor(2.65,requires_grad=True)
        logits=torch.tensor([[-2703.,0.],[0.,2703.]],requires_grad=True)
        labels=torch.tensor([0,0])
        all_labels=torch.tensor([0]*6+[1]*6)
        term,meta=bounded_auxiliary_term(price,logits,labels,all_labels)
        self.assertTrue(meta['admitted'])
        self.assertLessEqual(meta['auxiliary_fraction'],.2500001)
        self.assertGreater(float(term.detach()),0.)
        (price+term).backward()
        self.assertTrue(torch.isfinite(logits.grad).all())
        self.assertTrue(torch.isfinite(price.grad))


if __name__=='__main__':
    unittest.main()
