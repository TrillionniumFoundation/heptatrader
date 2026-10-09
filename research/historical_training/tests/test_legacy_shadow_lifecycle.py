import copy,json,os,subprocess,sys,tempfile,unittest
from pathlib import Path
from historical_training.common import EvidenceError,digest,sha,write_once
from historical_training.legacy_shadow_lifecycle import assess_plan,inspect,retirement_evidence,KINDS


def plan(kind='paired'):
    schema,rule_schema,slots,permission=KINDS[kind]
    start=1000000
    p={'schema':schema,'registered_at_ms':start-10000,'start_ms':start,
       'end_ms':start+(slots-1)*360000+60000+300000+10000,
       'rule':{'schema':rule_schema,'slots':slots,'slot_spacing_ms':360000,'horizon_ms':300000,
               'input_grace_ms':60000,permission:False,'automatic_selection':False}}
    p['plan_id']=digest(p);return p

def seal(p):p['plan_id']=digest({k:v for k,v in p.items() if k!='plan_id'});return p

class LegacyLifecycleTests(unittest.TestCase):
    def test_live_window_preserved(self):
        p=plan();self.assertTrue(assess_plan(p,'paired',p['start_ms'])['admit_model_start'])
    def test_expired_window_no_model_load(self):
        p=plan();r=assess_plan(p,'paired',p['end_ms']+1)
        self.assertFalse(r['admit_model_start']);self.assertFalse(r['active_worker_termination_authorized'])
    def test_temporal_expiry_same_contract(self):
        p=plan('temporal');self.assertFalse(assess_plan(p,'temporal',p['end_ms']+1)['admit_model_start'])
    def test_boundary_not_premature(self):
        p=plan();self.assertTrue(assess_plan(p,'paired',p['end_ms'])['admit_model_start'])
    def test_clock_rollback_denied(self):
        p=plan()
        with self.assertRaises(EvidenceError):assess_plan(p,'paired',p['registered_at_ms']-1)
    def test_bool_time_denied(self):
        with self.assertRaises(EvidenceError):assess_plan(plan(),'paired',True)
    def test_resealed_authority_denied(self):
        p=plan();p['rule']['effects_authorized']=True
        with self.assertRaises(EvidenceError):assess_plan(seal(p),'paired',p['start_ms'])
    def test_resealed_auto_selection_denied(self):
        p=plan();p['rule']['automatic_selection']=True
        with self.assertRaises(EvidenceError):assess_plan(seal(p),'paired',p['start_ms'])
    def test_changed_horizon_denied(self):
        p=plan();p['rule']['horizon_ms']=1
        with self.assertRaises(EvidenceError):assess_plan(seal(p),'paired',p['start_ms'])
    def test_early_end_cannot_allow_retirement(self):
        p=plan();p['end_ms']=p['start_ms']+100
        with self.assertRaises(EvidenceError):assess_plan(seal(p),'paired',p['start_ms']+200)
    def test_bad_plan_digest_denied(self):
        p=plan();p['start_ms']+=1
        with self.assertRaises(EvidenceError):assess_plan(p,'paired',p['end_ms']+1)
    def test_other_schema_denied(self):
        p=plan();p['schema']='heptatrader.frozen-multiwindow-plan.v1'
        with self.assertRaises(EvidenceError):assess_plan(seal(p),'paired',p['end_ms']+1)
    def test_active_training_kind_not_accepted(self):
        with self.assertRaises(EvidenceError):assess_plan(plan(),'trainer',0)
    def test_bool_slot_count_denied(self):
        p=plan();p['rule']['slots']=True
        with self.assertRaises(EvidenceError):assess_plan(seal(p),'paired',p['start_ms'])
    def test_float_plan_time_denied(self):
        p=plan();p['end_ms']=float(p['end_ms'])
        with self.assertRaises(EvidenceError):assess_plan(seal(p),'paired',1000000)
    def fixture(self,root):
        p=plan();path=root/'plan.json';write_once(path,p)
        for i in range(24):write_once(root/f'failure-{i:03}.json',{'slot':i,'reason':'ORIGINAL_FIXED_INPUT_MISSING'})
        return p,path,sha(path)
    def test_sealed_retirement_preserves_files(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);p,path,h=self.fixture(root);before={f.name:sha(f) for f in root.glob('*.json')}
            r=retirement_evidence(path,h,'paired',p['end_ms']+60001)
            self.assertTrue(r['all_original_input_slots_sealed']);self.assertEqual(before,{f.name:sha(f) for f in root.glob('*.json')})
    def test_missing_slot_refuses_stop(self):
        with tempfile.TemporaryDirectory() as d:
            p,path,h=self.fixture(Path(d));(Path(d)/'failure-003.json').unlink()
            with self.assertRaisesRegex(EvidenceError,'NOT_YET_SEALED'):retirement_evidence(path,h,'paired',p['end_ms']+60001)
    def test_shifted_slot_refuses_stop(self):
        with tempfile.TemporaryDirectory() as d:
            p,path,h=self.fixture(Path(d));f=Path(d)/'failure-003.json';f.chmod(0o600);f.write_text('{"slot":4}')
            with self.assertRaises(EvidenceError):retirement_evidence(path,h,'paired',p['end_ms']+60001)
    def test_inflight_grace_refuses_stop(self):
        with tempfile.TemporaryDirectory() as d:
            p,path,h=self.fixture(Path(d))
            with self.assertRaises(EvidenceError):retirement_evidence(path,h,'paired',p['end_ms']+1)
    def test_changed_plan_bytes_refuses_inspection(self):
        with tempfile.TemporaryDirectory() as d:
            p,path,h=self.fixture(Path(d));path.chmod(0o600);path.write_text('{}')
            with self.assertRaises(EvidenceError):inspect(path,h,'paired')
    def test_cli_expiry_has_no_torch_import(self):
        with tempfile.TemporaryDirectory() as d:
            p,path,h=self.fixture(Path(d))
            code='import sys; from historical_training.legacy_shadow_lifecycle import inspect; r=inspect(sys.argv[1],sys.argv[2],"paired"); assert not r["admit_model_start"]; assert "torch" not in sys.modules; print("NO_MODEL_IMPORT")'
            research=Path(__file__).resolve().parents[2]
            env={**os.environ,'PYTHONPATH':str(research)}
            run=subprocess.run([sys.executable,'-B','-c',code,str(path),h],capture_output=True,text=True,env=env)
            self.assertEqual(run.returncode,0,run.stderr);self.assertIn('NO_MODEL_IMPORT',run.stdout)
if __name__=='__main__':unittest.main()
