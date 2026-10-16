"""No-GPU adversarial regression for isolated Action V2 true-original GPU runner."""
import copy
import json
import pathlib
import unittest
from unittest.mock import patch

from laya_cell.integrity import ContractError
from scripts import research_laya_action_v2_gpu_isolated as task


def real_data():
    source=json.loads(task.source_bytes(),object_pairs_hook=task.strict_pairs)
    return source


class RealGpuIsolationTest(unittest.TestCase):
    def test_sha_exact_private_original(self):
        b=task.source_bytes()
        self.assertEqual(len(b),703031)
        self.assertEqual(task.SOURCE_SHA,"3d701e3795cb438b5ba38f3bb7c44bed83003e040108d1378d1084635157e4a9")

    def test_train_only_178_and_actual_holdout(self):
        m,train,hold=task.partition(real_data())
        self.assertEqual(m["manifest_id"],"a6457e18d362989fda2cacb110550538ac985508106439b20d1f4608c631fab3")
        self.assertEqual((len(train),sum(r["parent_correct"] for r in train)),(178,9))
        self.assertEqual((len(hold),sum(r["parent_correct"] for r in hold)),(120,5))
        self.assertTrue(all(r["future_outcome_available_ms"]<=task.TRAIN_CUTOFF_MS for r in train))
        self.assertTrue(all(r["anchor_ms"]>=task.TRAIN_CUTOFF_MS for r in hold))
        self.assertTrue(all(r["anchor_ms"]<task.HOLDOUT_END_MS for r in hold))

    def test_new_late_training_outcome_rejected(self):
        j=real_data()
        j["rows"][0]["future_outcome_available_ms"]=task.TRAIN_CUTOFF_MS+1
        with self.assertRaises(ContractError):
            task.partition(j)

    def test_duplicate_source_json_keys_rejected(self):
        with self.assertRaises(RuntimeError):
            json.loads('{"manifest":1,"manifest":2}',object_pairs_hook=task.strict_pairs)

    def test_tampered_row_refused_by_manifest(self):
        j=real_data()
        j["rows"][0]["parent_correct"]=not j["rows"][0]["parent_correct"]
        with self.assertRaises(ContractError):
            task.partition(j)

    def test_wrong_future_row_refused_by_split(self):
        j=real_data()
        j["rows"][-1]["anchor_ms"]=task.HOLDOUT_END_MS+1000
        with self.assertRaises(ContractError):
            task.partition(j)

    def test_score_denominators_and_incomplete_ood(self):
        v=task.score([.05,.12,.25],[0,0,1])
        self.assertEqual(v["total"],3)
        self.assertEqual(v["act_at_5_of_6"],0)
        self.assertAlmostEqual(v["binary_brier"],2*(.05**2+.12**2+(.25-1)**2)/3)
        missing=task.score([None,.3],[1,0])
        self.assertEqual(missing["total"],2)
        self.assertEqual(missing["graded"],0)
        self.assertEqual(missing["unscorable_ood"],1)
        self.assertIsNone(missing["binary_brier"])
        with self.assertRaises(RuntimeError):task.score([float("nan")],[0])
        with self.assertRaises(RuntimeError):task.score([],[0])

    def test_no_future_hyperparameters_or_broker_api(self):
        code=pathlib.Path(task.__file__).read_text()
        self.assertIn("Gate(c,\"trainer\")",code)
        self.assertIn("gate.acquire(",code)
        self.assertIn("gate.release()",code)
        self.assertIn("ACTION_LR=.0001",code)
        for banned in ("placeOrder(", "cancelOrder(", "reqExecutions(", "order_send(", "model_promoted=True", "subprocess.run("):
            self.assertNotIn(banned,code)
        self.assertEqual(task.MAX_PAIR_STEPS,8)
        self.assertLess(task.MAX_GUARD_SECONDS,620)

    def test_rejects_unbounded_source_file(self):
        with patch.object(task,"SOURCE_SHA","0"*64):
            with self.assertRaises(RuntimeError):task.source_bytes()

    def test_original_parent_price_vector_exact_accept(self):
        r=real_data()["rows"][0]
        self.assertEqual(task.check_sealed_parent_prediction(
            r,list(r["frozen_parent_probs"])),0.0)

    def test_real_scale_parent_drift_refused_without_training(self):
        r=real_data()["rows"][0]
        wrong=list(r["frozen_parent_probs"])
        wrong[0]+=0.11;wrong[5]-=0.11
        self.assertAlmostEqual(sum(wrong),1.0,places=6)
        with self.assertRaisesRegex(RuntimeError,
                "ORIGINAL_SEALED_PRICE_PROBABILITY_PARITY_FAILED"):
            task.check_sealed_parent_prediction(r,wrong)

    def test_parent_small_numerical_rounding_is_not_label_repair(self):
        r=real_data()["rows"][0]
        trial=list(r["frozen_parent_probs"])
        trial[0]+=0.00004;trial[1]-=0.00004
        self.assertGreater(task.check_sealed_parent_prediction(r,trial),0)
        altered=copy.deepcopy(r);altered["parent_correct"]=not r["parent_correct"]
        with self.assertRaisesRegex(RuntimeError,"ORIGINAL_PARENT_CORRECTNESS"):
            task.check_sealed_parent_prediction(altered,list(r["frozen_parent_probs"]))

    def test_parent_nonfinite_vector_rejected(self):
        r=real_data()["rows"][0];wrong=list(r["frozen_parent_probs"])
        wrong[0]=float("nan")
        with self.assertRaisesRegex(RuntimeError,"ORIGINAL_REPLAY_INVALID"):
            task.check_sealed_parent_prediction(r,wrong)

    def test_canonical_output_does_not_overwrite(self):
        # Only a new, disposable private temp directory is used; no production paths.
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p=pathlib.Path(td)/"receipt.json"
            task.write_once(p,{"orders":0,"authority":False})
            with self.assertRaises(FileExistsError):
                task.write_once(p,{"orders":0,"authority":False})


if __name__=="__main__":
    unittest.main()
