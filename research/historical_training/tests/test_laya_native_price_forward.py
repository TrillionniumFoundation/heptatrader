"""Time-fenced frozen Native+Numeric ORIGINAL PAPER shadow-only test harness."""
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from historical_training.common import canonical,sha,read
from historical_training import laya_native_price_forward as f
from laya_cell.integrity import ContractError,digest

BASE=1900000000000

class NativeForwardTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory()
  self.addCleanup(self.temp.cleanup)
  sleeper=patch.object(f.time,'sleep',return_value=None)
  sleeper.start()
  self.addCleanup(sleeper.stop)
  self.root=Path(self.temp.name)
  self.feed=self.root/'feed';self.feed.mkdir(mode=0o700)
  self.out=self.root/'output'
  self.baseline=self.root/'baseline';self.baseline.mkdir(mode=0o700)
  self.period=self.root/'period.json';self.period.write_text('{"domain":"PAPER"}\n')
  self.binary=self.root/'native.bin';self.binary.write_bytes(b'native-TEST-ONLY-fixture')
  model={
   'model_id':'a'*64,'effect_authority':False,
   'trained_until_ms':BASE-86400000,
   'weights':[0.01]*562,
   'generation':42,
  }
  self.model_path=self.baseline/'model.json'
  self.model_path.write_bytes(canonical(model)+b'\n')
  self.freeze=self.baseline/'freeze-manifest.json'
  self.freeze.write_bytes(canonical({'inventory_sha256':{
   'model.json':sha(self.model_path)}})+b'\n')
  self.config={
   'period_path':str(self.period),'period_sha256':sha(self.period),
   'feed_directory':str(self.feed),'completed_history_sources':[],
   'baseline_directory':str(self.baseline),
   'native_binary':str(self.binary),'native_binary_sha256':sha(self.binary),
  }
  self.conf=self.root/'config.json';self.conf.write_bytes(canonical(self.config)+b'\n')
  self.slot=BASE+120000
  self.sample={
   'sample_id':'b'*64,'anchor_ms':self.slot+200,
   'known_at_ms':self.slot+400,
   'native_episode':{'rows':[[0.]*16]},
   'raw_features':[0.]*16
  }
  self.state={'time':self.sample['anchor_ms'],'event':'PAPER_FX_SOURCE'}
  self.row={'state':self.state}
  self.records=[{'t':self.sample['anchor_ms'],'known_at_ms':self.slot+300,
                 'receipt':'c'*64,'bid':1.1,'ask':1.1001}]
  self.proof=[{'path':'synthetic-local-source','sha256':'f'*64}]
  self.plan=self.register()
 def register(self):
  with patch.object(f,'now_ms',return_value=BASE):
   return f.register(self.out,self.conf,self.slot)
 def now_gen(self):
  time=[self.slot+500]
  def step():
   time[0]+=100
   return time[0]
  return step
 def capture(self,*,model_score=0.75,quotes=None,clock=None):
  if quotes is None:quotes=self.records
  with patch.object(f,'now_ms',side_effect=clock or self.now_gen()),\
       patch.object(f,'read_recent_original_quotes',
           return_value=(quotes,self.proof)),\
       patch.object(f,'forecast_input',
           return_value=(self.sample,self.row)),\
       patch.object(f,'predict_point',
           return_value=(model_score,'78fdb0c-test-native')):
   return f.capture(self.out,self.conf,wait=False)
 def verify(self,*,model_score=0.75,quotes=None):
  with patch.object(f,'verify_source_prefixes',return_value=None),\
       patch.object(f,'read_quotes',
            return_value=(quotes or self.records,self.proof)),\
       patch.object(f,'forecast_input',
            return_value=(self.sample,self.row)),\
       patch.object(f,'predict_point',
            return_value=(model_score,'78fdb0c-test-native')):
   return f.verify(self.out,self.conf)
 def test_complete_new_future_model_output_and_original_replay(self):
  ack=self.capture()
  self.assertTrue(ack['prediction_sealed_before_deadline'])
  self.assertLess(ack['post_fsync_at_ms'],self.plan['window_end_ms'])
  proof=self.verify()
  self.assertEqual(proof['true_model_prospective_observations'],1)
  self.assertTrue(proof['native_model_output_recomputed'])
  self.assertFalse(proof['laya_7bin_gpu_model_executed'])
  self.assertFalse(proof['actual_strategy_profit'])
  self.assertEqual(proof['broker_orders_sent'],0)
  prediction=read(self.out/'prediction.json')
  self.assertEqual(prediction['frozen_numeric_model_expected_mid_bps'],0.75)
  self.assertTrue(prediction['new_prospective_numeric_prediction'])
  self.assertFalse(prediction['action_head_is_trade_permission'])
 def test_second_capture_cannot_overwrite_evidence(self):
  self.capture()
  with self.assertRaisesRegex(ContractError,'CAPTURE_ALREADY_ATTEMPTED'):
   self.capture()
 def test_unchanged_frozen_model_is_required(self):
  new=read(self.model_path)
  new['weights'][0]=0.99
  self.model_path.write_bytes(canonical(new)+b'\n')
  with self.assertRaisesRegex(ContractError,'BASELINE_FREEZE_CHANGED'):
   f.capture(self.out,self.conf)
 def test_modified_source_module_refuses_after_registration(self):
  with patch.object(f,'inventory',return_value={'fake':'d'*64}):
   with self.assertRaisesRegex(ContractError,'PINNED_CODE_CHANGED'):
    f._plan(self.out)
 def test_no_prediction_before_future_slot(self):
  with patch.object(f,'now_ms',return_value=BASE):
   with self.assertRaisesRegex(ContractError,'SLOT_NOT_DUE'):
    f.capture(self.out,self.conf,wait=False)
 def test_missing_original_input_must_not_create_prediction(self):
  with patch.object(f,'now_ms',side_effect=self.now_gen()),\
       patch.object(f,'read_recent_original_quotes',
          return_value=(self.records,self.proof)),\
       patch.object(f,'forecast_input',side_effect=ContractError('SOURCE_GAP')):
   with self.assertRaisesRegex(ContractError,'SLOT_MISSED'):
    f.capture(self.out,self.conf)
  self.assertFalse((self.out/'input.json').exists())
  self.assertFalse((self.out/'prediction.json').exists())
  failure=read(self.out/'failure.json')
  self.assertEqual(failure["schema"],"heptatrader.native-price-future-missed-slot.v1")
  self.assertFalse(failure["model_prediction_fabricated"])
  with self.assertRaisesRegex(ContractError,"CAPTURE_ALREADY_ATTEMPTED"):
   self.capture()
 def test_old_quote_cannot_be_relabelled_new(self):
  fake=[{**self.records[0],'known_at_ms':BASE-1000}]
  # Need the expected registration time to precede this recorded old quote.
  with patch.object(f,'now_ms',side_effect=self.now_gen()),\
       patch.object(f,'read_recent_original_quotes',return_value=(fake,self.proof)),\
       patch.object(f,'forecast_input',return_value=(self.sample,self.row)):
   with self.assertRaisesRegex(ContractError,'SLOT_MISSED'):
    f.capture(self.out,self.conf)
  self.assertFalse((self.out/'prediction.json').exists())
 def test_rehashed_source_value_does_not_replay(self):
  self.capture()
  x=read(self.out/'input.json')
  x['sample_sha256']='e'*64
  (self.out/'input.json').chmod(0o600)
  (self.out/'input.json').write_bytes(canonical(x)+b'\n')
  with self.assertRaisesRegex(ContractError,'PREDICTION_CHAIN_NOT_VERIFIED'):
   self.verify()
 def test_predicted_score_tamper_fails_replay(self):
  self.capture()
  result=read(self.out/'prediction.json')
  result['frozen_numeric_model_expected_mid_bps']=99.
  (self.out/'prediction.json').chmod(0o600)
  (self.out/'prediction.json').write_bytes(canonical(result)+b'\n')
  with self.assertRaisesRegex(ContractError,'PREDICTION_CHAIN_NOT_VERIFIED'):
   self.verify()
 def test_native_score_mismatch_is_not_acceptance(self):
  self.capture()
  with self.assertRaisesRegex(ContractError,'MODEL_OUTPUT_MISMATCH'):
   self.verify(model_score=0.70)
 def test_source_witness_mismatch_is_not_acceptance(self):
  self.capture()
  forged=[{**self.records[0],'receipt':'d'*64}]
  with self.assertRaisesRegex(ContractError,'ORIGINAL_SOURCE_OR_FEATURES_CHANGED'):
   self.verify(quotes=forged)
 def test_finite_one_slot_and_model_frozen_required(self):
  self.assertLess(self.plan['baseline_trained_until_ms'],BASE)
  self.assertEqual(self.plan['target_horizon_sec'],300)
  self.assertEqual(self.plan['no_trade_policy'],'NO_ACTION_NO_BROKER_ORDER')
  with patch.object(f,'now_ms',return_value=BASE):
   with self.assertRaisesRegex(ContractError,'NOT_REGISTERED_BEFORE_SLOT'):
    f.register(self.root/'too_late',self.conf,BASE+10000)
