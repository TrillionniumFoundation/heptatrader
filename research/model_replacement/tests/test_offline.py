"""Synthetic contract tests only; no fixtures are reported as market evidence."""
import copy, hashlib, json, os, tempfile, time, unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
from research.neuron_learning import data as original
from research.neuron_learning.laya_cell.integrity import canonical, digest, sha_file, ContractError
from research.neuron_learning.laya_cell.dataset import rows_for_training
from research.neuron_learning.laya_cell.resident_row_accounting import row_id
from research.model_replacement.data import build_dataset, JOB_MANIFEST_SCHEMA, regular_context, training_series
from research.model_replacement.splits import build_split, validate_split
from research.model_replacement.baselines import Prior, LinearSoftmax, restore
from research.model_replacement.metrics import score, fit_temperature, apply_temperature
from research.model_replacement.experiment import run_experiment, ExperimentConfig, SavedPredictor, prediction_row, write_new
from research.model_replacement.source_proof import verify_prefixes

BASE=1780000000000
SCOPE='a'*64+':EUR.USD'

def sample(i, spacing=600000, anchor=None):
    anchor=BASE+i*spacing if anchor is None else anchor
    qs=[]
    for j in range(6):
        t=anchor-(5-j)*10000; mid=1.1+(i%7)*.00001+j*.000001
        qs.append({'t':t,'known_at_ms':t+100,'bid':mid-.00002,'ask':mid+.00002,
                   'mid':mid,'receipt':digest(['synthetic',t])})
    s=original.make_sample(qs,anchor+100,SCOPE,'CAUSAL_QUOTE_WINDOW')
    target=(-.2,-.05,-.02,0.,.02,.05,.2)[i%7]
    s['targets']=[target,None]; s['label_reasons']=['OBSERVED','PENDING']
    s['outcome_receipts']=[{'horizon_sec':300,'observed_at_ms':anchor+300000,
        'available_at_ms':anchor+300100,'target_scaled':target,'source_receipt':digest(['synthetic-outcome',i])},None]
    return s

def fixture(root, n=112, spacing=600000):
    samples=[sample(i,spacing) for i in range(n)]; cutoff=samples[-1]['anchor_ms']+300101
    source=root/'quotes.jsonl'; source.write_bytes(b'{"synthetic_test_only":true}\n'); source.chmod(0o600)
    dataset={'schema':'heptatrader.neuron-dataset.v1','source_kind':'REAL_PAPER_RUNTIME',
        'scope':SCOPE,'instrument':'EUR.USD','frozen_at_ms':cutoff,'samples':samples,
        'source_prefixes':[{'path':str(source),'bytes':source.stat().st_size,'sha256':sha_file(source)}]}
    dp=root/'dataset.json'; write_new(dp,dataset)
    window={'training_end_ms':cutoff,'validation_start_ms':cutoff+1,'validation_end_ms':cutoff+2}
    rows,_=rows_for_training(dataset,window); pins=[]
    for i in range(0,len(rows),32):
        jp=root/('job-'+str(i)+'.json'); write_new(jp,{'dataset':str(dp),'dataset_sha256':sha_file(dp),
            'window':window,'training_row_ids':[row_id(r) for r in rows[i:i+32]]})
        pins.append({'path':str(jp),'sha256':sha_file(jp)})
    manifest=root/'jobs.json'; write_new(manifest,{'schema':JOB_MANIFEST_SCHEMA,'jobs':pins})
    return manifest,dp,dataset

class DataTests(unittest.TestCase):
    def test_real_schema_roundtrip_and_unique_accounting(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); manifest,_,_=fixture(root)
            initial=build_dataset(manifest); doc=json.loads(manifest.read_text())
            extra=root/'repeated-job.json'; extra.write_bytes(Path(doc['jobs'][0]['path']).read_bytes())
            doc['jobs'].append({'path':str(extra),'sha256':sha_file(extra)})
            manifest.write_bytes(canonical(doc))
            result=build_dataset(manifest)
            self.assertEqual(result['accounting']['unique_rows'],112)
            self.assertEqual(result['accounting']['duplicate_occurrences'],32)
            self.assertEqual(result['sources']['read_stats']['original_files_read'],1)
            self.assertEqual(result['rows'][0]['target_class'],0)
            self.assertEqual(result['rows'][3]['return_bps'],0)
            self.assertEqual(len(initial['rows']),len(result['rows']))
    def test_job_bytes_changed_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            manifest,_,_=fixture(Path(d)); pin=json.loads(manifest.read_text())['jobs'][0]
            with Path(pin['path']).open('ab') as f: f.write(b'\n')
            with self.assertRaisesRegex(ContractError,'JOB_FILE_CHANGED'): build_dataset(manifest)
    def test_purge_uses_actual_maturity_and_split_is_frozen(self):
        with tempfile.TemporaryDirectory() as d:
            manifest,_,_=fixture(Path(d),spacing=30000); data=build_dataset(manifest)
            split=build_split(data,minimum_rows=2); self.assertTrue(split['purged'])
            parts=validate_split(split,data)
            for left,right in zip(('train','validation','calibration'),('validation','calibration','test')):
                self.assertLess(max(r['outcome_available_at_ms'] for r in parts[left]),
                                min(r['anchor_ms'] for r in parts[right]))
            split['partitions']['train'].append(split['partitions']['test'].pop())
            split['split_id']=digest({k:v for k,v in split.items() if k!='split_id'})
            with self.assertRaisesRegex(ContractError,'SPLIT_RULE'): validate_split(split,data)
    def test_multiple_prefixes_read_longest_once_and_check_shorter_hash(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'source.jsonl'; raw=b'a\nbb\nccc\n'; path.write_bytes(raw); path.chmod(0o600)
            pins=[{'path':str(path),'bytes':n,'sha256':hashlib.sha256(raw[:n]).hexdigest()} for n in (2,5,9)]
            result=verify_prefixes(pins,set()); self.assertEqual(result['original_bytes_read'],9)
            self.assertEqual(result['original_files_read'],1)
            pins[0]['sha256']='f'*64
            with self.assertRaisesRegex(ContractError,'SOURCE_PREFIX_HASH_CHANGED'): verify_prefixes(pins,set())

class FitTests(unittest.TestCase):
    def test_linear_updates_parameters_train_only_scaler_and_restores(self):
        x=np.linspace(-2,2,120)[:,None]; y=(x[:,0]>0).astype(np.int64)+2
        model=LinearSoftmax(steps=200).fit(x,y,x,y)
        self.assertTrue(model.receipt['parameters_changed'])
        self.assertLess(score(model.predict_proba(x),y)['mean_nll'],.5)
        np.testing.assert_array_equal(model.mean,x.mean(0))
        loaded=restore(model.save(None)); np.testing.assert_array_equal(loaded.predict_proba(x),model.predict_proba(x))
    def test_calibration_only_temperature_and_zero_nll_explicit(self):
        p=np.tile([.99,.005,.001,.001,.001,.001,.001],(40,1)); y=np.arange(40)%2
        before=p.copy(); fitted=fit_temperature(p,y)
        np.testing.assert_array_equal(p,before)
        self.assertLessEqual(score(apply_temperature(p,fitted['temperature']),y)['mean_nll'],score(p,y)['mean_nll'])
        impossible=score([[1.,0,0,0,0,0,0]],[1])
        self.assertTrue(impossible['nll_is_infinite']); self.assertIsNone(impossible['mean_nll'])
    def test_endpoint_not_invented_as_continuous_path(self):
        row=prediction_row(sample(0)); row['row_id']=digest(['row',0])
        values,_=regular_context(row); self.assertEqual(len(values),6)
        with self.assertRaisesRegex(ContractError,'NO_CONTIGUOUS_TRAIN_ONLY_SERIES'):
            training_series([row],min_length=36)
    def test_persisted_models_warm_predictions_require_new_inputs(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); manifest,_,_=fixture(root); data=build_dataset(manifest); split=build_split(data)
            dp=root/'compiled.json'; sp=root/'split.json'; write_new(dp,data); write_new(sp,split)
            for kind in ('prior','linear'):
                with patch('research.model_replacement.experiment.time.time',return_value=time.time()-10):
                    result=run_experiment(dp,sp,root/kind,ExperimentConfig(model=kind,linear_steps=20))
                pin=result['model_state']; loaded=SavedPredictor(pin['path'],pin['sha256'])
                with self.assertRaisesRegex(ContractError,'FRESH_INPUT_AFTER_MODEL_SEAL'):
                    loaded.predict([data['rows'][0]])
                row=prediction_row(sample(0,anchor=int(time.time()*1000)-500))
                first=loaded.predict([row]); again=loaded.predict([row])
                np.testing.assert_array_equal(first['probabilities'],again['probabilities'])
                self.assertAlmostEqual(sum(first['probabilities'][0]),1.)
                self.assertEqual(result['evaluation_kind'],'HISTORICAL_OFFLINE_REPLAY')
                self.assertFalse(result['promoted'])
