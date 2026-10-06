"""Synthetic control fixtures and actual GPU/native conformance; not strategy proof."""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from data import canonical,digest,strict,prefix,exports,valid_quote,features,make_sample,label,freeze
from gpu import GpuHead,initial_weights,project_candidate,numerical_self_test
from runner import Native,OBJECTIVE,target_arrays,independent_counts,write_once
import evaluate

T=1791297075000
PERIOD='a'*64

def quotes(n=110):
    return [{'t':T+i*10000,'bid':1.0+i*.00001,'ask':1.00002+i*.00001,
             'mid':1.00001+i*.00001,'known_at_ms':T+i*10000,
             'receipt':digest({'tick':i})} for i in range(n)]

def sample():return make_sample(quotes()[:24],T+230000,PERIOD+':EUR.USD','CAUSAL_QUOTE_WINDOW')

def quote_record(t=T):
    return {'at_ms':t,'data':{'source':'IB','instrument':'EUR.USD','authoritative':True,'stale':False,
            'observed_at_ms':t,'stale_after_ms':t+5000,'bid':1.0,'ask':1.00002},'sha256':'b'*64}

def export_file(root,source='REAL_PAPER_RUNTIME_EXPORT',period=PERIOD,dropped=0,broken=False):
    context={'source_kind':source,'execution_domain':'PAPER','instrument':'EUR.USD',
             'broker_mutation_authorized':False,'period_file_sha256':period}
    previous='0'*64;rows=[]
    for i,(kind,data) in enumerate([('HEADER',context),('QUOTE',quote_record()['data'])]):
        body={'schema':'heptatrader.learning-export.v1','producer_id':'unitfixture','segment':0,
              'sequence':i,'previous':previous,'dropped_total':dropped,'kind':kind,'at_ms':T,'data':data}
        row={**body,'sha256':digest(body)};previous=row['sha256'];rows.append(row)
    if broken:rows[1]['data']['bid']=123
    p=Path(root)/'unitfixture-0000.jsonl';p.write_text('\n'.join(canonical(x) for x in rows)+'\n');p.chmod(0o600)
    return p


class CausalDataTests(unittest.TestCase):
    def test_duplicate_fields_fail(self):
        with self.assertRaisesRegex(ValueError,'DUPLICATE'):strict('{"x":1,"x":2}')
    def test_nan_fails(self):
        with self.assertRaisesRegex(ValueError,'NONFINITE'):strict('{"x":NaN}')
    def test_only_committed_prefix_is_read(self):
        with tempfile.TemporaryDirectory() as r:
            p=Path(r)/'input';p.write_bytes(b'{"ok":1}\n{');p.chmod(0o600)
            raw,meta=prefix(p)
            self.assertEqual(raw,b'{"ok":1}\n');self.assertEqual(meta['trailing_uncommitted_bytes'],1)
    def test_symlink_source_refused(self):
        with tempfile.TemporaryDirectory() as r:
            p=Path(r)/'input';p.write_text('x\n');p.chmod(0o600);q=Path(r)/'link';q.symlink_to(p)
            with self.assertRaises(OSError):prefix(q)
    def test_real_export_verifies_chain(self):
        with tempfile.TemporaryDirectory() as r:
            export_file(r);rows,meta,ex=exports(r,PERIOD,T+1)
            self.assertEqual(len(rows),1);self.assertEqual(ex,[]);self.assertEqual(len(meta),1)
    def test_synthetic_export_refused(self):
        with tempfile.TemporaryDirectory() as r:
            export_file(r,source='SYNTHETIC')
            with self.assertRaisesRegex(ValueError,'REAL_PAPER'):exports(r,PERIOD,T+1)
    def test_tampered_export_refused(self):
        with tempfile.TemporaryDirectory() as r:
            export_file(r,broken=True)
            with self.assertRaisesRegex(ValueError,'HASH'):exports(r,PERIOD,T+1)
    def test_wrong_period_retained_as_exclusion(self):
        with tempfile.TemporaryDirectory() as r:
            export_file(r,period='b'*64);rows,_,ex=exports(r,PERIOD,T+1)
            self.assertEqual(rows,[]);self.assertEqual(ex[0]['reason'],'DIFFERENT_ACCOUNTING_PERIOD')
    def test_export_loss_disqualifies_not_silent(self):
        with tempfile.TemporaryDirectory() as r:
            export_file(r,dropped=1);rows,_,ex=exports(r,PERIOD,T+1)
            self.assertEqual(rows,[]);self.assertIn('LOSS',ex[0]['reason'])
    def test_quote_ttl_not_rewritten(self):
        q=quote_record();q['at_ms']+=6000
        with self.assertRaisesRegex(ValueError,'STALE'):valid_quote(q,'EUR.USD')
    def test_quote_authority_not_guessed(self):
        q=quote_record();q['data']['authoritative']=False
        with self.assertRaisesRegex(ValueError,'AUTHORITY'):valid_quote(q,'EUR.USD')
    def test_features_do_not_read_future(self):
        qs=quotes();a=features(qs[:24],qs[23]['t']);qs[-1]['mid']=999
        self.assertEqual(a,features(qs[:24],qs[23]['t']))
    def test_features_fixed_shape_finite(self):
        f=features(quotes()[:24],T+230000)
        self.assertEqual(len(f),16);self.assertTrue(np.isfinite(f).all())
    def test_future_input_rejected(self):
        with self.assertRaisesRegex(ValueError,'FUTURE'):make_sample(quotes()[:24],T,PERIOD,'UNIT')
    def test_known_label_appears_only_after_horizon(self):
        qs=quotes();s=label(sample(),qs,[q['t'] for q in qs],T+1100000)
        self.assertEqual(s['label_reasons'][0],'OBSERVED');self.assertIsNotNone(s['targets'][0])
        self.assertEqual(s['label_reasons'][1],'OUTCOME_NOT_MATURE');self.assertIsNone(s['targets'][1])
    def test_gap_is_not_zero_return(self):
        qs=[q for q in quotes() if not T+300000<q['t']<T+380000]
        s=label(sample(),qs,[q['t'] for q in qs],T+1100000)
        self.assertEqual(s['label_reasons'][0],'PATH_GAP');self.assertIsNone(s['targets'][0])
    def test_missing_endpoint_is_not_interpolated(self):
        qs=[q for q in quotes() if q['t']!=T+530000]
        s=label(sample(),qs,[q['t'] for q in qs],T+1100000)
        self.assertEqual(s['label_reasons'][0],'OUTCOME_MISSING');self.assertIsNone(s['targets'][0])
    def test_anchor_mismatch_refused(self):
        qs=quotes();s=sample();s['reference_mid']*=2
        s=label(s,qs,[q['t'] for q in qs],T+1100000)
        self.assertEqual(s['label_reasons'][0],'ANCHOR_UNVERIFIED')
    def test_teacher_only_never_supplies_market_label(self):
        s=sample();s['teacher']={'distillation_allowed':True,'forecast':{'horizon_sec':300,'scenarios':[{'probability':1,'move_bps':3}]}}
        _,mask,_,tm=target_arrays([s]);self.assertEqual(mask.sum(),0);self.assertEqual(tm.sum(),0)
    def test_late_teacher_retained_but_not_used(self):
        s=sample();s['targets'][0]=.02;s['teacher']={'distillation_allowed':False,'forecast':{'horizon_sec':300}}
        _,mask,_,tm=target_arrays([s]);self.assertEqual(mask.sum(),1);self.assertEqual(tm.sum(),0)
    def test_hold_teacher_does_not_delete_market_sample(self):
        s=sample();s['targets'][0]=.02;s['teacher']={'distillation_allowed':True,'forecast':{'action':'HOLD','confidence':0.99,'horizon_sec':300,'scenarios':[{'probability':.5,'move_bps':-2},{'probability':.5,'move_bps':2}]}}
        _,mask,teacher,tm=target_arrays([s]);self.assertEqual(mask.sum(),1);self.assertEqual(tm.sum(),1);self.assertEqual(teacher[0,0],0)
    def test_overlap_does_not_inflate_independent_count(self):
        ss=[]
        for i in range(10):
            s=sample();s['anchor_ms']+=i*10000;s['targets'][0]=0.01;ss.append(s)
        self.assertEqual(independent_counts(ss)['300'],1)
    def test_immutable_artifact_cannot_be_replaced(self):
        with tempfile.TemporaryDirectory() as r:
            p=Path(r)/'artifact';write_once(p,{'v':1});write_once(p,{'v':1})
            with self.assertRaisesRegex(ValueError,'IMMUTABLE'):write_once(p,{'v':2})
    def test_period_change_stops_evaluator_before_source_read(self):
        with tempfile.TemporaryDirectory() as r:
            p=Path(r)/'period';p.write_text('{}')
            with self.assertRaisesRegex(ValueError,'PERIOD_CHANGED'):evaluate.evaluate({'period_path':str(p),'period_sha256':'f'*64})
    def test_candidate_projection_uses_both_actual_norms(self):
        b=initial_weights();c=project_candidate(b,b+1)
        self.assertLessEqual(np.linalg.norm(c-b)/np.linalg.norm(b),.0025)
        for a,z in ((0,528),(528,562)):self.assertLessEqual(np.linalg.norm(c[a:z]-b[a:z])/np.linalg.norm(b[a:z]),.005)
    def test_zero_norm_layer_refused(self):
        b=initial_weights();b[528:]=0
        with self.assertRaisesRegex(ValueError,'ZERO_LAYER'):project_candidate(b,b+1)


class NativeGpuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c=json.loads(Path(os.environ['HEPTA_NEURON_TEST_CONFIG']).read_text())
        cls.native=Native(cls.c);cls.gpu=GpuHead(cls.c['gpu_library'],cls.c['gpu_library_sha256'])
    def test_real_gpu_forward_backward_oracle(self):
        self.assertTrue(numerical_self_test(self.gpu)['finite_difference_passed'])
    def test_no_cpu_fallback(self):
        self.assertEqual(self.gpu.identity['arch'],'gfx1150');self.assertFalse(self.gpu.identity['cpu_fallback'])
    def test_native_neuron_deterministic_and_isolated(self):
        s=sample();x,a=self.native.encode([s,s]);self.assertTrue(np.array_equal(x[0],x[1]))
        self.assertEqual(a['episodes'][0],a['episodes'][1]);self.assertFalse(a['episodes'][0]['effect_authority'])
    def test_native_rejects_reversed_history(self):
        s=sample();s['native_episode']['times'].reverse()
        with self.assertRaisesRegex(RuntimeError,'TIME_ORDER'):self.native.encode([s])
    def test_native_ndu_fixed_scalar_oracle(self):
        req={'op':'utility','dataset_digest':'1'*64,'objective_digest':'2'*64,'outcome_digest':'3'*64,
             'events':[{'instant':.5,'discount':.5,'event_digest':'4'*64},{'instant':.25,'discount':1,'event_digest':'5'*64}]}
        out=self.native.call(req);self.assertEqual(out['value'],.625);self.assertFalse(out['effect_authority'])
    def test_native_missing_outcome_cannot_be_zero(self):
        req={'op':'utility','dataset_digest':'1'*64,'objective_digest':'2'*64,'outcome_digest':'0'*64,
             'events':[{'instant':0,'discount':1,'event_digest':'4'*64}]}
        with self.assertRaisesRegex(RuntimeError,'ZERO_DIGEST'):self.native.call(req)
    def proposal(self,candidate):
        b=initial_weights();q=lambda x:np.rint(np.asarray(x,dtype=float)*4294967296).astype(np.int64).tolist()
        return {'op':'proposal','baseline_q32':q(b),'candidate_q32':q(candidate),'generation':1,
                **{k:'1'*64 for k in ('dataset_digest','update_rule_digest','modulator_digest','eligibility_digest','evaluation_digest')}}
    def test_native_plasticity_no_change_has_no_authority(self):
        r=self.native.call(self.proposal(initial_weights()))
        self.assertEqual(r['candidate_count'],1);self.assertFalse(r['selection_performed'])
    def test_native_plasticity_excess_delta_refused(self):
        with self.assertRaises(RuntimeError):self.native.call(self.proposal(initial_weights()+.01))
    def test_native_plasticity_bounded_candidate_verified(self):
        b=initial_weights();c=project_candidate(b,b+.01);r=self.native.call(self.proposal(c))
        self.assertEqual(r['candidate_count'],2);self.assertEqual(r['global_max_relative_ppm'],2500)
    def test_runtime_parameters_never_mutated_by_gpu_call(self):
        b=initial_weights();before=b.tobytes();x=np.zeros((16,32),np.float32);m=np.ones((16,2),np.float32)
        self.gpu.run(x,b,y=m*.1,mask=m,epochs=2)
        self.assertEqual(b.tobytes(),before)

class CandidateRecoveryTests(unittest.TestCase):
    def config(self,root):
        c=json.loads(Path(os.environ['HEPTA_NEURON_TEST_CONFIG']).read_text())
        c['output_dir']=str(Path(root)/'student')
        return c
    def test_restart_restores_exact_candidate_without_training(self):
        from runner import Learner
        with tempfile.TemporaryDirectory() as root:
            c=self.config(root);a=Learner(c);before=canonical(a.model);a.db.close()
            b=Learner(c);self.assertEqual(canonical(b.model),before)
            self.assertEqual(b.db.execute('SELECT count(*) FROM trains').fetchone()[0],0);b.db.close()
    def test_committed_database_wins_over_stale_json_pointer(self):
        from runner import Learner
        with tempfile.TemporaryDirectory() as root:
            c=self.config(root);a=Learner(c);model=copy.deepcopy(a.model)
            model.pop('model_id');model['generation']=2;model['weights'][0]+=.00001;model['model_id']=digest(model)
            with a.db:
                a.db.execute('INSERT INTO models VALUES (?,?)',(model['model_id'],canonical(model)))
                a.db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)',('current_model_id',model['model_id']))
                a.db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)',('training_fingerprint','committed-input'))
            # Deliberately leave the human-readable pointer at the predecessor.
            a.db.close();b=Learner(c)
            self.assertEqual(b.model,model);self.assertEqual(b.last_fingerprint,'committed-input')
            self.assertEqual(json.loads(b.selected_pointer.read_text()),model);b.db.close()
    def test_orphan_candidate_pointer_cannot_advance_committed_model(self):
        from runner import Learner
        with tempfile.TemporaryDirectory() as root:
            c=self.config(root);a=Learner(c);old=copy.deepcopy(a.model)
            orphan=copy.deepcopy(old);orphan.pop('model_id');orphan['generation']=99;orphan['model_id']=digest(orphan)
            a.selected_pointer.write_text(canonical(orphan));a.db.close()
            b=Learner(c);self.assertEqual(b.model,old);b.db.close()


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--config',required=True);ap.add_argument('--report',required=True);a=ap.parse_args()
    os.environ['HEPTA_NEURON_TEST_CONFIG']=a.config
    suite=unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    payload={'schema':'heptatrader.neuron-test-receipt.v1','test_cases':result.testsRun,'failures':len(result.failures),
             'errors':len(result.errors),'skipped':len(result.skipped),'fixture_kind':'SYNTHETIC_CONTROL_PLUS_REAL_GPU_CONFORMANCE',
             'result':'PASS' if result.wasSuccessful() and not result.skipped else 'FAIL','broker_orders_sent':0,
             'source_sha256':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(ROOT.rglob('*')) if p.suffix in ('.py','.rs','.cpp')},
             'real_strategy_acceptance':False}
    Path(a.report).write_text(json.dumps(payload,indent=2)+'\n');raise SystemExit(0 if payload['result']=='PASS' else 1)
