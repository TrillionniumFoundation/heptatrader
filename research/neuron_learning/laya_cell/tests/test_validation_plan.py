"""Synthetic tests for prospective evaluation identity; no model/venue calls."""
import copy,json,tempfile,unittest
from pathlib import Path
import laya_cell.forward_shadow as fwd
from laya_cell.integrity import canonical,digest,sha_file,ContractError

class ValidationPlanTests(unittest.TestCase):
    def setUp(self):
        self.t=tempfile.TemporaryDirectory();self.addCleanup(self.t.cleanup);self.root=Path(self.t.name)
        self.candidate={'candidate_id':'candidate-identity','created_at_ms':100}
        self.baseline={'model_id':'baseline-identity'}
        self.m={'schema':'heptatrader.laya-independent-validation.v1','candidate_id':'candidate-identity',
                'bootstrap_model_id':'baseline-identity','registered_at_ms':200,'validation_start_ms':300,
                'validation_end_ms':400,'horizon_sec':300,'automatic_selection':False,
                'source_file':str(self.root/'quotes.jsonl'),'evaluator_source_sha256':sha_file(Path(fwd.__file__))}
        self.path=self.root/'plan.json'
    def config(self,m=None):
        m=copy.deepcopy(m or self.m);m['manifest_sha256']=digest(m);self.path.write_bytes(canonical(m))
        return {'validation_plan':str(self.path),'validation_plan_sha256':sha_file(self.path),'shadow_directory':str(self.root)}
    def check(self,c):return fwd.validation_window(c,self.candidate,self.baseline)
    def test_new_window_registered_after_candidate(self):
        self.assertEqual(self.check(self.config())['validation_start_ms'],300)
    def test_changed_manifest_bytes_refused(self):
        c=self.config();self.path.write_text('{}')
        with self.assertRaisesRegex(ContractError,'FILE_CHANGED'):self.check(c)
    def test_rehashed_file_with_wrong_inner_digest_refused(self):
        c=self.config();m=json.loads(self.path.read_text());m['validation_end_ms']=500;self.path.write_bytes(canonical(m));c['validation_plan_sha256']=sha_file(self.path)
        with self.assertRaisesRegex(ContractError,'PLAN_DIGEST'):self.check(c)
    def test_candidate_cannot_be_substituted(self):
        m={**self.m,'candidate_id':'other'}
        with self.assertRaisesRegex(ContractError,'SCOPE'):self.check(self.config(m))
    def test_bootstrap_cannot_be_substituted(self):
        m={**self.m,'bootstrap_model_id':'other'}
        with self.assertRaisesRegex(ContractError,'SCOPE'):self.check(self.config(m))
    def test_backdated_registration_rejected(self):
        m={**self.m,'registered_at_ms':99}
        with self.assertRaisesRegex(ContractError,'PROSPECTIVE'):self.check(self.config(m))
    def test_registration_during_window_rejected(self):
        m={**self.m,'registered_at_ms':300}
        with self.assertRaisesRegex(ContractError,'PROSPECTIVE'):self.check(self.config(m))
    def test_reversed_window_rejected(self):
        m={**self.m,'validation_end_ms':299}
        with self.assertRaisesRegex(ContractError,'PROSPECTIVE'):self.check(self.config(m))
    def test_execution_selection_cannot_be_granted(self):
        m={**self.m,'automatic_selection':True}
        with self.assertRaisesRegex(ContractError,'SCOPE'):self.check(self.config(m))
    def test_wrong_source_path_rejected(self):
        m={**self.m,'source_file':'/unrelated/quotes.jsonl'}
        with self.assertRaisesRegex(ContractError,'SCOPE'):self.check(self.config(m))
    def test_evaluator_cannot_change_midwindow(self):
        m={**self.m,'evaluator_source_sha256':'0'*64}
        with self.assertRaisesRegex(ContractError,'EVALUATOR_CHANGED'):self.check(self.config(m))

if __name__=='__main__':unittest.main(verbosity=2)
