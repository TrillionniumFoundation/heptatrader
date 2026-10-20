import sys,unittest
from pathlib import Path
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/"research/neuron_learning"))
from laya_cell import price_readout_diagnostics as d

class PriceReadoutDiagnosticsTests(unittest.TestCase):
    def biased(self,n,strength):
        y=np.tile(np.arange(7),n);z=np.zeros((len(y),7));z[np.arange(len(y)),y]=strength;z[:,0]+=4
        return np.exp(d.log_softmax(z)),y,z

    def test_known_bias_improves_separate_holdout_without_mutation(self):
        p,y,_=self.biased(20,2.);q,t,_=self.biased(5,1.7);original=p.copy()
        f=d.fit_bias(p,y);b=np.array(f["bias"]);saved=b.copy()
        before=d.metrics(q,t);after=d.metrics(q,t,bias=b)
        self.assertLess(before["accuracy"],.2);self.assertGreater(after["accuracy"],.9)
        self.assertLess(after["nll"],before["nll"]-.5)
        d.metrics(q,(t+1)%7,bias=b)
        np.testing.assert_array_equal(p,original);np.testing.assert_array_equal(b,saved)
        self.assertEqual(f["lambda"],1./len(y))

    def test_gradient_identity_and_constant_invariance(self):
        p,y,z=self.biased(3,2.);lp,y,_=d.validated(p,y)
        b=np.array([.2,-.3,.1,.4,-.1,.05,-.35]);lam=1./len(y);g=d.bias_eval(lp,y,b,lam)[1]
        numeric=[]
        for j in range(7):
            e=np.zeros(7);e[j]=1e-6
            numeric.append((d.bias_eval(lp,y,b+e,lam)[0]-d.bias_eval(lp,y,b-e,lam)[0])/2e-6)
        np.testing.assert_allclose(g,numeric,rtol=1e-5,atol=1e-7)
        self.assertAlmostEqual(d.metrics(p,y)["nll"],d.metrics(p,y,bias=np.zeros(7))["nll"],12)
        self.assertAlmostEqual(d.metrics(p,y)["nll"],d.metrics(p,y,inverse_temperature=1)["nll"],12)
        self.assertEqual(d.metrics(p,y)["predicted_class_counts"],d.metrics(p,y,inverse_temperature=.4)["predicted_class_counts"])
        np.testing.assert_allclose(d.log_softmax(z),d.log_softmax(z+np.linspace(-1000,1000,len(z))[:,None]),atol=1e-12,rtol=0)

    def test_missing_classes_finite_and_temperature_uniform_boundary(self):
        p=np.full((35,7),1/7);y=np.full(35,2,dtype=np.int64);f=d.fit_bias(p,y)
        self.assertTrue(np.isfinite(f["bias"]).all());self.assertLess(max(abs(x) for x in f["bias"]),10)
        self.assertAlmostEqual(sum(f["bias"]),0,8)
        self.assertAlmostEqual(d.fit_temperature(p,y)["inverse_temperature"],1,8)
        z=np.zeros((35,7));z[:,0]=4;wrong=np.exp(d.log_softmax(z));t=np.full(35,6,dtype=np.int64)
        f=d.fit_temperature(wrong,t);self.assertTrue(f["uniform_degenerate"])
        self.assertEqual(f["train_metrics"]["argmax_tied_rows"],35)

    def test_bad_probability_and_label_inputs_rejected(self):
        for p,y in [(np.ones((2,7)),[0,1]),(np.full((2,7),1/7),[0.,1.]),(np.full((2,7),1/7),[False,True])]:
            with self.assertRaises(ValueError):d.fit_bias(p,y)


class PriceReadoutSplitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
        import research_laya_price_readout_diagnostics
        cls.runner = research_laya_price_readout_diagnostics

    def row(self, row_id, anchor, available):
        return {"row_id": row_id, "anchor_ms": anchor,
                "future_outcome_available_ms": available}

    def test_late_outcomes_and_embargo_are_excluded(self):
        rows = [self.row("fit", 80, 100), self.row("late", 90, 101),
                self.row("gap", 105, 110), self.row("hold", 120, 125),
                self.row("end", 130, 135)]
        train, hold = self.runner.split_rows(rows, 100, 110, 130)
        self.assertEqual([r["row_id"] for r in train], ["fit"])
        self.assertEqual([r["row_id"] for r in hold], ["hold"])

    def test_invalid_split_and_repeated_identity_are_rejected(self):
        rows = [self.row("same", 80, 90), self.row("same", 120, 125)]
        with self.assertRaisesRegex(ValueError, "Duplicate or overlapping"):
            self.runner.split_rows(rows, 100, 110, 130)
        with self.assertRaisesRegex(ValueError, "chronological"):
            self.runner.split_rows(rows, 100, 90, 130)
        with self.assertRaisesRegex(ValueError, "nonempty"):
            self.runner.split_rows(rows, 50, 60, 70)

    def test_action_labels_use_original_parent_not_calibrated_student(self):
        rows = [{"frozen_parent_probs": [.4, .1, .1, .1, .1, .1, .1],
                 "target_class": 1}]
        self.assertEqual(self.runner.action_counts(rows)["ACT_correct"], 0)
        self.assertEqual(self.runner.action_counts(rows)["ESCALATE_wrong"], 1)

if __name__=="__main__":unittest.main()
