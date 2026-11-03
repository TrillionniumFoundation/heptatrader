"""Small numerical/identity tests; no pretrained model or market data required."""
import json
from pathlib import Path
import tempfile
import unittest
import current100k_study as study

torch=study.configure_cpu(1)

class ReadoutStudyTests(unittest.TestCase):
    def setUp(self):
        self.x=torch.zeros(12,768);self.x[:,0]=1
        self.y=torch.linspace(-.3,.1,12,dtype=torch.float64)
        self.zero={"value.weight":torch.zeros(1,768),"value.bias":torch.zeros(1)}

    def test_sgd_common_direction_matches_exact_recurrence(self):
        arms,_=study.fit_recipes(self.x,self.y,self.zero)
        got=study.head_forward(arms["sgd16_reference"],self.x)
        want=float(self.y.mean())*(1-.9**16)*100
        self.assertLess(float((got-want).abs().max()),3e-6)

    def test_bias_changes_one_coordinate_and_real_forward(self):
        self.zero["value.weight"][0,0]=.02
        arms,_=study.fit_recipes(self.x,self.y,self.zero)
        head=arms["bias_shift_mean"]
        self.assertTrue(torch.equal(head["value.weight"],self.zero["value.weight"]))
        from safetensors.torch import load_file
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"head.safetensors";study.tensors_write(p,head)
            loaded=load_file(str(p))
            got=study.head_forward(loaded,self.x)
            self.assertLess(float((got-float(self.y.mean())*100).abs().max()),3e-6)

    def test_constrained_solve_norm_and_baseline_classification(self):
        self.y.fill_(-.2)
        arms,_=study.fit_recipes(self.x,self.y,self.zero)
        ridge=arms["ridge_normcap_0p1"]
        norm=torch.cat((ridge["value.weight"].flatten(),ridge["value.bias"])).norm()
        self.assertLessEqual(float(norm),.100001)
        self.assertGreater(float(arms["constant_mean"]["value.bias"].abs()),.1)

    def test_reject_bad_shape_and_nonfinite_head(self):
        with self.assertRaises(RuntimeError):study.head_forward({"value.weight":torch.zeros(1,767),"value.bias":torch.zeros(1)},self.x)
        self.zero["value.bias"][0]=float("nan")
        with self.assertRaises(RuntimeError):study.head_forward(self.zero,self.x)

    def test_pinned_module_does_not_reuse_another_package(self):
        with tempfile.TemporaryDirectory() as d:
            a=Path(d)/"a";b=Path(d)/"b";a.mkdir();b.mkdir()
            for p,v in ((a,"one"),(b,"two")):
                (p/"__init__.py").write_text("")
                (p/"core.py").write_text("value="+repr(v))
            self.assertEqual(study.pinned_module(a,"core").value,"one")
            self.assertEqual(study.pinned_module(b,"core").value,"two")

    def test_time_budget_training_and_covariance_patch_are_bounded(self):
        from weight_recipes import augment_recipes
        arms,info=augment_recipes(self.x,self.y,self.zero)
        for head in arms.values():
            norm=torch.cat((head["value.weight"].flatten(),head["value.bias"])).norm()
            self.assertLessEqual(float(norm),.100001)
        cost=info["additional_compute"]["sgd_head_budget_2s"]
        self.assertGreater(cost["steps"],16)
        self.assertLessEqual(cost["steps"],8192)
        self.assertLessEqual(cost["wall_seconds"],2.05)

    def test_frozen_output_refuses_conflicting_rewrite(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"result.json";study.json_write(p,{"identity":"a"});study.json_write(p,{"identity":"a"})
            with self.assertRaises(RuntimeError):study.json_write(p,{"identity":"b"})
            self.assertEqual(json.loads(p.read_text()),{"identity":"a"})

if __name__=="__main__":unittest.main()
