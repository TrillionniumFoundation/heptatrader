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
from runner import Native,OBJECTIVE,target_arrays,independent_counts,write_once,connect,Learner
import evaluate
import data as learning_data

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
    def freeze_quotes(self,count,step=1000,*,prediction_samples=()):
        records=[]
        for i in range(count):
            row=quote_record(T+i*step);row['kind']='QUOTE';records.append(row)
        c={'instrument':'EUR.USD','period_start_ms':T,'period_sha256':PERIOD,
           'export_dir':'unused','legacy_capture':'unused'}
        with patch('data.exports',return_value=(records,[],[])),\
             patch('data.legacy_quotes',return_value=([],None,0)):
            return freeze(c,T+(count-1)*step,prediction_samples=prediction_samples)

    def test_dense_source_retains_both_mature_horizons_and_fresh_predictions(self):
        dataset=self.freeze_quotes(1800)
        samples=dataset['samples'];mature=[s for s in samples if any(v is not None for v in s['targets'])]
        self.assertLessEqual(len(samples),512);self.assertLessEqual(len(mature),256)
        self.assertGreaterEqual(sum(s['targets'][0] is not None for s in mature),128)
        self.assertGreaterEqual(sum(s['targets'][1] is not None for s in mature),128)
        self.assertEqual(max(s['anchor_ms'] for s in samples),T+1799000)
        self.assertTrue(all(T+1799000-s['anchor_ms']<=60000 for s in samples if s not in mature))
        self.assertEqual(len({s['sample_id'] for s in samples}),len(samples))
        self.assertIn('sample_selection_profile',dataset)

    def test_feature_construction_is_bounded_before_full_history_materialization(self):
        with patch.object(learning_data,'make_sample',wraps=make_sample) as made:
            dataset=self.freeze_quotes(1800)
        self.assertEqual(made.call_count,len(dataset['samples']))
        self.assertLessEqual(made.call_count,512)

    def test_gap_prefix_preserves_original_path_censoring(self):
        qs=[q for q in quotes(200) if not T+300000<q['t']<T+380000]
        times=[q['t'] for q in qs];gaps=[0]*len(times)
        for i in range(1,len(times)):gaps[i]=gaps[i-1]+int(times[i]-times[i-1]>30000)
        original=label(sample(),qs,times,T+2100000)
        accelerated=label(sample(),qs,times,T+2100000,gap_prefix=gaps)
        self.assertEqual(accelerated,original)
        self.assertEqual(accelerated['label_reasons'],['PATH_GAP','PATH_GAP'])

    def test_sampler_never_manufactures_mature_labels_for_short_history(self):
        dataset=self.freeze_quotes(100)
        self.assertTrue(dataset['samples'])
        self.assertTrue(all(s['targets']==[None,None] for s in dataset['samples']))
        self.assertTrue(all(T+99000-s['anchor_ms']<=60000 for s in dataset['samples']))

    def test_persisted_prediction_rejects_outer_fields_that_disagree_with_snapshot(self):
        original=sample()
        mutations={'anchor_ms':original['anchor_ms']+1,'reference_mid':original['reference_mid']*2,
            'known_at_ms':original['known_at_ms']-1,'input_gap':True,
            'input_kind':'EXACT_REMOTE_INPUT','source_input_id':'b'*64}
        for key,value in mutations.items():
            with self.subTest(field=key):
                changed=copy.deepcopy(original);changed[key]=value
                with self.assertRaisesRegex(ValueError,'PREDICTION_INPUT_IDENTITY'):
                    self.freeze_quotes(1800,prediction_samples=[changed])

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


class ForwardEvaluationRecoveryTests(unittest.TestCase):
    def test_saved_prediction_is_scored_after_input_leaves_latest_sample_window(self):
        with tempfile.TemporaryDirectory() as root:
            root=Path(root);period=root/'period';period.write_text('{}')
            period_sha=hashlib.sha256(period.read_bytes()).hexdigest()
            export=root/'export';export.mkdir()
            qs=quotes(600)
            for i,q in enumerate(qs):q.update(t=T+i*2000,known_at_ms=T+i*2000)
            context={'source_kind':'REAL_PAPER_RUNTIME_EXPORT','execution_domain':'PAPER',
                'instrument':'EUR.USD','broker_mutation_authorized':False,'period_file_sha256':period_sha}
            entries=[('HEADER',T,context)]+[('QUOTE',q['t'],{
                **quote_record(q['t'])['data'],'bid':q['bid'],'ask':q['ask']}) for q in qs]
            previous='0'*64;lines=[]
            for i,(kind,at,data) in enumerate(entries):
                body={'schema':'heptatrader.learning-export.v1','producer_id':'unitfixture','segment':0,
                    'sequence':i,'previous':previous,'dropped_total':0,'kind':kind,'at_ms':at,'data':data}
                row={**body,'sha256':digest(body)};previous=row['sha256'];lines.append(canonical(row))
            source=export/'unitfixture-0000.jsonl';source.write_text('\n'.join(lines[:7])+'\n');source.chmod(0o600)
            output=root/'output';output.mkdir()
            c={'period_path':str(period),'period_sha256':period_sha,'period_start_ms':T,
               'output_dir':str(output),'export_dir':str(export),'legacy_capture':str(root/'absent'),
               'instrument':'EUR.USD'}
            s=freeze(c,qs[5]['t']+1)['samples'][-1]
            db=connect(output/'bridge.sqlite')
            p={'sample':s,'model_id':'model','model_trained_until_ms':0,
               'created_at_ms':s['anchor_ms']+1,'prediction_scaled':[0,0]}
            db.execute('INSERT INTO predictions VALUES (?,?,?,?)',('prediction',s['sample_id'],p['created_at_ms'],canonical(p)))
            db.commit();db.close()
            with source.open('a') as f:f.write('\n'.join(lines[7:])+'\n')
            now=T+1200000
            # Prove the ordinary bounded training view no longer contains this input.
            self.assertNotIn(s['sample_id'],[x['sample_id'] for x in freeze(c,now)['samples']])
            with patch.object(evaluate.time,'time',return_value=now/1000):result=evaluate.evaluate(c)
            self.assertEqual(result['outcomes'].get('OBSERVED'),2)
            self.assertEqual(result['pending'],0)
            with patch.object(evaluate,'freeze',side_effect=AssertionError('completed inputs rescanned')):
                self.assertEqual(evaluate.evaluate(c)['outcomes'].get('OBSERVED'),2)

    def test_old_unfinished_prediction_is_not_displaced_by_completed_recent_rows(self):
        with tempfile.TemporaryDirectory() as root:
            root=Path(root);db=connect(root/'bridge.sqlite')
            for i in range(3):
                db.execute('INSERT INTO predictions VALUES (?,?,?,?)',(str(i),str(i),T+i,canonical({'sample':i})))
            db.commit();db.close()
            db=sqlite3.connect(root/'shadow-evaluation.sqlite')
            db.execute('CREATE TABLE outcomes(key TEXT PRIMARY KEY)')
            for i in (1,2):
                for h in (300,900):db.execute('INSERT INTO outcomes VALUES (?)',(str(i)+':'+str(h),))
            db.commit();db.close()
            with patch.object(evaluate,'MAX_PREDICTION_BATCH',1):rows=evaluate.pending_predictions(root)
            self.assertEqual(rows,[('0',{'sample':0})])

    def test_label_refresh_clears_previously_observed_target_on_missing_source(self):
        qs=quotes();s=label(sample(),qs,[q['t'] for q in qs],T+2000000)
        self.assertIsNotNone(s['targets'][0])
        s=label(s,[],[],T+2000000)
        self.assertIsNone(s['targets'][0]);self.assertIsNone(s['outcome_receipts'][0])


class TrainingCadenceRecoveryTests(unittest.TestCase):
    def config(self,root):
        period=Path(root)/'period';period.write_text('{}')
        return {'output_dir':str(Path(root)/'student'),'period_path':str(period),
            'period_sha256':hashlib.sha256(period.read_bytes()).hexdigest(),
            'instrument':'EUR.USD','gpu_library':'unused','gpu_library_sha256':'unused'}

    def test_restart_restores_committed_training_clock(self):
        with tempfile.TemporaryDirectory() as root,patch('runner.GpuHead'):
            c=self.config(root);a=Learner(c)
            a.db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)',('last_training_ms',str(T)))
            a.db.commit();a.db.close();b=Learner(c)
            b.gpu.identity={'backend':'SYNTHETIC_CONTROL_FIXTURE'}
            self.assertEqual(b.last_train_ms,T);b.db.close()

    def test_existing_receipt_recovers_cadence_before_metadata_upgrade(self):
        with tempfile.TemporaryDirectory() as root,patch('runner.GpuHead'):
            c=self.config(root);a=Learner(c)
            a.db.execute('INSERT INTO trains VALUES (?,?)',('receipt',canonical({'at_ms':T})))
            a.db.commit();a.db.close();b=Learner(c)
            self.assertEqual(b.last_train_ms,T);b.db.close()

    def test_changed_data_after_restart_still_obeys_five_minute_cadence(self):
        with tempfile.TemporaryDirectory() as root,patch('runner.GpuHead'):
            c=self.config(root);a=Learner(c)
            a.db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)',('last_training_ms',str(T)))
            a.db.commit();a.db.close();b=Learner(c)
            b.gpu.identity={'backend':'SYNTHETIC_CONTROL_FIXTURE'}
            samples=[]
            for i in range(16):
                s=sample();s['sample_id']=str(i);s['targets'][0]=.02;samples.append(s)
            dataset={'samples':samples,'source_kind':'SYNTHETIC_CONTROL_FIXTURE',
                'label_reason_counts':{},'exact_remote_inputs':0,'teacher_actions':{},
                'legacy_teachers_without_exact_input_not_distilled':0,'settlements':[]}
            with patch('runner.freeze',return_value=dataset),patch('runner.replay_economics',return_value={}),\
                 patch.object(b,'predict_before_learning',return_value=0),\
                 patch.object(b.native,'encode',side_effect=AssertionError('unexpected training')),\
                 patch('runner.time.time',return_value=(T+1000)/1000):
                self.assertEqual(b.step()['state'],'SHADOW_COLLECTING')
            self.assertEqual(b.db.execute('SELECT count(*) FROM trains').fetchone()[0],0);b.db.close()

    def test_training_clock_is_committed_with_candidate_and_fingerprint(self):
        with tempfile.TemporaryDirectory() as root,patch('runner.GpuHead'):
            c=self.config(root);a=Learner(c);a.gpu.identity={'backend':'SYNTHETIC_CONTROL_FIXTURE'}
            a.gpu.sha256='0'*64;weights=np.asarray(a.model['weights'],dtype=np.float32)
            common={'prediction':np.zeros((16,2),np.float32),'loss':.01,'gpu_kernel_ms':0}
            a.gpu.run.side_effect=[{**common,'weights':weights},{**common,'weights':weights+.000001},
                {**common,'weights':weights+.000001}]
            samples=[]
            for i in range(16):
                s=sample();s['sample_id']=str(i);s['targets'][0]=.02
                s['outcome_receipts'][0]={'available_at_ms':T};samples.append(s)
            dataset={'samples':samples,'source_kind':'SYNTHETIC_CONTROL_FIXTURE',
                'scope':c['period_sha256']+':EUR.USD','label_reason_counts':{},'exact_remote_inputs':0,
                'teacher_actions':{},'legacy_teachers_without_exact_input_not_distilled':0,'settlements':[],
                'sample_selection_profile':copy.deepcopy(learning_data.SAMPLE_SELECTION_PROFILE)}
            with patch('runner.freeze',return_value=dataset),patch('runner.replay_economics',return_value={}),\
                 patch.object(a,'predict_before_learning',return_value=0),\
                 patch.object(a.native,'encode',return_value=(np.zeros((16,32)),{'episodes':[]})),\
                 patch.object(a.native,'utility',return_value={'value':0,'receipt_digest':'1'*64}),\
                 patch.object(a.native,'call',return_value={'proposal_digest':'2'*64}),\
                 patch('runner.time.time',return_value=T/1000):
                self.assertEqual(a.step()['state'],'GPU_CANDIDATE_TRAINED')
            self.assertEqual(a.db.execute("SELECT value FROM meta WHERE key='last_training_ms'").fetchone()[0],str(T))
            self.assertEqual(a.db.execute('SELECT count(*) FROM trains').fetchone()[0],1)
            record=json.loads(a.db.execute('SELECT payload FROM trains').fetchone()[0])
            self.assertEqual(record['sample_selection_profile'],learning_data.SAMPLE_SELECTION_PROFILE)
            model=a.model;a.db.close();b=Learner(c)
            self.assertEqual(b.last_train_ms,T);self.assertEqual(b.model,model);b.db.close()


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
    def test_restart_retains_training_cadence(self):
        from runner import Learner
        with tempfile.TemporaryDirectory() as root:
            c=self.config(root);a=Learner(c)
            with a.db:a.db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)',('last_training_ms',str(T)))
            a.db.close();b=Learner(c);self.assertEqual(b.last_train_ms,T);b.db.close()
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
