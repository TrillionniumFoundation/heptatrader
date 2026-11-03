"""Synthetic restore contracts; no training, network, or real model loading."""
import copy
from dataclasses import asdict
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

from research.model_replacement.baselines import restore
from research.model_replacement.data import GRID
from research.model_replacement.experiment import SavedPredictor, code_identity, write_new
from research.model_replacement.forecast_models import ForecastModelConfig, MODEL_IDS
from research.neuron_learning.laya_cell.dataset import BIN_EDGES_BPS
from research.neuron_learning.laya_cell.integrity import ContractError, digest, sha_file


class RestoreIdentityTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        self.counter=0

    def receipt(self,kind):
        return {'schema':'research.forecast_model.v1','model_kind':kind,'model_id':MODEL_IDS[kind],
                'source_revision':'1'*40,'source_path':'/synthetic/original-model',
                'revision_binding':'operator_supplied',
                'files_sha256':{'config.json':'2'*64,'model.safetensors':'3'*64},
                'artifact_sha256':'4'*64,'device':'cpu','dtype':'float32','seed':20261102,
                'threads':2,'interop_threads':1,'batch_size':4,'context_length_cap':None,
                'offline_only':True,'training_mode':'frozen','trainable_parameters':0,
                'total_parameters':1000,'parameters_sha256':'5'*64,
                'versions':{'numpy':'fixture-numpy','torch':'fixture-torch','model-package':'fixture-model'},
                'implementation_sha256':{'synthetic.Backend':'6'*64,'synthetic.Model':'7'*64},
                'load_seconds':1.,'load_total_seconds':2.}

    def save_state(self,kind,receipt,*,lora=None,old_code=False):
        config=ForecastModelConfig(kind,'/synthetic/original-model','1'*40,seed=20261102,
                                   expected_files_sha256=receipt.get('files_sha256'))
        state={'schema':'heptatrader.model-replacement-state.v1',
               'model':{'kind':'LAPLACE_1_PRIOR','parameters':[1/7]*7},
               'backbone':{'config':asdict(config),'load':receipt,'lora':lora},
               'class_order':list(range(7)),'bin_edges_bps':list(BIN_EDGES_BPS),
               'grid':GRID,'code_sha256':code_identity()}
        if old_code:
            state['code_sha256']['experiment.py']='0'*64
        state['model_state_id']=digest(state)
        self.counter+=1
        return write_new(self.root/f'state-{self.counter}.json',state)

    def load(self,pin,actual):
        adapter=Mock()
        adapter.load.return_value=actual
        with patch('research.model_replacement.forecast_models.create_forecast_adapter',return_value=adapter):
            predictor=SavedPredictor(pin['path'],pin['sha256'])
        return predictor,adapter

    def test_identical_frozen_models_accept_new_load_timing_and_location(self):
        for kind in MODEL_IDS:
            with self.subTest(kind=kind):
                sealed=self.receipt(kind); actual=copy.deepcopy(sealed)
                actual.update(source_path='/synthetic/relocated-model',revision_binding='revision_directory',
                              load_seconds=3.,load_total_seconds=4.)
                pin=self.save_state(kind,sealed)
                _,adapter=self.load(pin,actual)
                adapter.load.assert_called_once_with()
                adapter.restore_lora.assert_not_called()
                self.assertEqual(sha_file(Path(pin['path'])),pin['sha256'])

    def test_same_weight_files_reject_changed_implementation(self):
        for kind in MODEL_IDS:
            with self.subTest(kind=kind):
                sealed=self.receipt(kind); actual=copy.deepcopy(sealed)
                actual['implementation_sha256']['synthetic.Model']='8'*64
                pin=self.save_state(kind,sealed)
                with self.assertRaisesRegex(ContractError,'IDENTITY_CHANGED:implementation_sha256'):
                    self.load(pin,actual)

    def test_same_weight_files_reject_changed_loaded_base_parameters(self):
        for kind in MODEL_IDS:
            with self.subTest(kind=kind):
                sealed=self.receipt(kind); actual=copy.deepcopy(sealed)
                actual['parameters_sha256']='8'*64
                pin=self.save_state(kind,sealed)
                with self.assertRaisesRegex(ContractError,'IDENTITY_CHANGED:parameters_sha256'):
                    self.load(pin,actual)

    def test_dependency_change_or_missing_package_rejected(self):
        for kind in MODEL_IDS:
            for operation in ('upgrade','missing','added'):
                with self.subTest(kind=kind,operation=operation):
                    sealed=self.receipt(kind); actual=copy.deepcopy(sealed)
                    if operation=='upgrade': actual['versions']['torch']='upgraded-fixture'
                    elif operation=='missing': del actual['versions']['model-package']
                    else: actual['versions']['extra-package']='new-fixture'
                    pin=self.save_state(kind,sealed)
                    with self.assertRaisesRegex(ContractError,'IDENTITY_CHANGED:versions'):
                        self.load(pin,actual)

    def test_missing_or_empty_identity_cannot_match_by_omission(self):
        for field in ('versions','implementation_sha256'):
            for operation in ('missing','empty'):
                with self.subTest(field=field,operation=operation):
                    sealed=self.receipt('chronos2_small')
                    if operation=='missing': del sealed[field]
                    else: sealed[field]={}
                    pin=self.save_state('chronos2_small',sealed)
                    with self.assertRaisesRegex(ContractError,'IDENTITY_REQUIRED:'+field):
                        self.load(pin,copy.deepcopy(sealed))

    def test_missing_parameter_receipt_is_rejected(self):
        sealed=self.receipt('chronos2_small'); del sealed['parameters_sha256']
        pin=self.save_state('chronos2_small',sealed)
        with self.assertRaisesRegex(ContractError,'IDENTITY_CHANGED:parameters_sha256'):
            self.load(pin,copy.deepcopy(sealed))

    def test_lora_restore_waits_for_base_identity_and_runtime_check(self):
        sealed=self.receipt('chronos2_small'); actual=copy.deepcopy(sealed)
        actual['versions']['torch']='upgraded-fixture'
        lora={'synthetic_test_receipt':True}
        pin=self.save_state('chronos2_small',sealed,lora=lora)
        adapter=Mock(); adapter.load.return_value=actual
        with patch('research.model_replacement.forecast_models.create_forecast_adapter',return_value=adapter):
            with self.assertRaisesRegex(ContractError,'IDENTITY_CHANGED:versions'):
                SavedPredictor(pin['path'],pin['sha256'])
        adapter.restore_lora.assert_not_called()
        adapter.load.return_value=sealed
        with patch('research.model_replacement.forecast_models.create_forecast_adapter',return_value=adapter):
            SavedPredictor(pin['path'],pin['sha256'])
        adapter.restore_lora.assert_called_once_with(lora)

    def test_old_code_state_stays_rejected_without_rewriting_or_loading(self):
        pin=self.save_state('chronos2_small',self.receipt('chronos2_small'),old_code=True)
        with patch('research.model_replacement.forecast_models.create_forecast_adapter') as factory:
            with self.assertRaisesRegex(ContractError,'MODEL_INFERENCE_CODE_CHANGED'):
                SavedPredictor(pin['path'],pin['sha256'])
        factory.assert_not_called()
        self.assertEqual(sha_file(Path(pin['path'])),pin['sha256'])

    def test_catboost_version_mismatch_or_missing_fails_before_native_load(self):
        path=self.root/'model.cbm'; path.write_bytes(b'explicit synthetic fixture')
        native=Mock()
        module=types.SimpleNamespace(CatBoostClassifier=native,__version__='fixture-version')
        state={'kind':'CATBOOST_CPU','version':'fixture-version','parameters':{'thread_count':2},
               'model_file':{'path':str(path),'sha256':sha_file(path)}}
        for version in ('changed-version',None,''):
            with self.subTest(version=version):
                candidate={**state,'version':version}
                with patch.dict(sys.modules,{'catboost':module}):
                    with self.assertRaisesRegex(ContractError,'CATBOOST_RUNTIME_VERSION_CHANGED'):
                        restore(candidate)
                native.assert_not_called()
        with patch.dict(sys.modules,{'catboost':module}):
            restored=restore(state)
        native.assert_called_once_with()
        native.return_value.load_model.assert_called_once_with(str(path))
        self.assertEqual(restored.threads,2)


if __name__=='__main__':
    unittest.main()
