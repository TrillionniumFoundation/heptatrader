"""Synthetic custody fixtures only; real 1e-6 ROCm proof is a separate receipt."""
import base64,copy,hashlib,json,os,tempfile,unittest
from pathlib import Path
import numpy as np
from safetensors.numpy import save_file
from historical_training.common import EvidenceError,digest,sha
from historical_training.neuron256_cpu_custody import (
 SCHEMA,SOURCE,BACKEND,verify_feature,signed_record,verify_signature,public_key)
from historical_training.matched_data import text

class SignedCanonicalCpuFeatureTests(unittest.TestCase):
 def setUp(self):
  tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
  self.root=Path(tmp.name);self.anchor=1791558600
  self.row={'time':self.anchor,'history_start':self.anchor-3600,'features':[.01]*25}
  self.inp=self.root/'input.json';self.inp.write_text(json.dumps(self.row))
  # RFC 8032 public, synthetic test vector. Not a production signing secret.
  priv=self.root/'producer-ed25519.key'
  priv.write_bytes(bytes.fromhex('9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60'))
  priv.chmod(0o600);self.keyfile=priv
  pub='d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a'
  self.plan={'anchors':[self.anchor],'plan_id':'signed-plan-1','cpu_public_key_hex':pub,
             'cpu_emitter_sha256':'a'*64}
  self.candidate={'candidate_id':'frozen-candidate',
                 'encoder_asset':{'model_file_sha256':'b'*64,'tokenizer_file_sha256':'c'*64}}
  self.folder=self.root/'features'
  self.folder.mkdir()
  raw=np.zeros((1,768),dtype=np.float32);raw[0,0]=1.
  save_file({'features':raw},str(self.folder/'raw.safetensors'))
  self.payload={'schema':SCHEMA,'source':SOURCE,'plan_id':self.plan['plan_id'],
    'candidate_id':self.candidate['candidate_id'],'anchor':self.anchor,
    'generated_at_ms':self.anchor*1000+50000,'source_input_sha256':sha(self.inp),
    'source_row_digest':digest(self.row),
    'source_text_sha256':hashlib.sha256(text(self.row).encode()).hexdigest(),
    'encoder_model_sha256':self.candidate['encoder_asset']['model_file_sha256'],
    'encoder_tokenizer_sha256':self.candidate['encoder_asset']['tokenizer_file_sha256'],
    'encoder_runtime_sha256':self.plan['cpu_emitter_sha256'],
    'torch_version':BACKEND,'cpu_only':True,'raw_sha256':sha(self.folder/'raw.safetensors'),
    'rows':1,'width':768,'new_training_steps':0,'broker_orders_sent':0,'trade_authority':False}
  self.store_signed(self.payload)

 def store_signed(self,payload):
  rec=signed_record(payload,self.keyfile,self.plan['cpu_public_key_hex'])
  (self.folder/'receipt.json').write_text(json.dumps(rec,sort_keys=True))
  return rec

 def verify(self,**other):
  return verify_feature(self.folder,self.plan,self.candidate,self.row,self.anchor,
      other.get('now_ms',self.anchor*1000+80000),source_input_file=other.get('input_file',self.inp))

 def test_exact_frozen_signed_vector_accepted(self):
  vec,rec=self.verify()
  self.assertEqual(vec.shape,(1,768));self.assertEqual(float(vec[0,0]),1)
  self.assertEqual(rec['torch_version'],'2.11.0+cpu')
  self.assertFalse(rec['trade_authority'])
 def test_wrong_signer_fails(self):
  p=dict(self.plan);p['cpu_public_key_hex']='11'*32
  with self.assertRaisesRegex(EvidenceError,'SIGNATURE'):
   verify_feature(self.folder,p,self.candidate,self.row,self.anchor,self.anchor*1000+80000,source_input_file=self.inp)
 def test_modified_receipt_without_resigning_fails(self):
  rec=json.loads((self.folder/'receipt.json').read_text());rec['generated_at_ms']+=1
  (self.folder/'receipt.json').write_text(json.dumps(rec))
  with self.assertRaisesRegex(EvidenceError,'SIGNATURE'):self.verify()
 def test_modified_tensor_fails_even_with_original_receipt(self):
  raw=np.zeros((1,768),dtype=np.float32);raw[0,1]=1.
  save_file({'features':raw},str(self.folder/'raw.safetensors'))
  with self.assertRaisesRegex(EvidenceError,'RAW_CHANGED'):self.verify()
 def test_replacement_original_input_fails(self):
  self.inp.write_text(json.dumps({**self.row,'features':[.02]*25}))
  with self.assertRaisesRegex(EvidenceError,'INPUT_OR_ENV'):self.verify()
 def test_cannot_sign_different_private_key(self):
  path=self.root/'other.key'
  path.write_bytes(b'\x01'*32);path.chmod(0o600)
  with self.assertRaisesRegex(EvidenceError,'SIGNER_SUBSTITUTION'):
   signed_record(self.payload,path,self.plan['cpu_public_key_hex'])
 def test_world_readable_private_key_rejected(self):
  self.keyfile.chmod(0o644)
  with self.assertRaisesRegex(EvidenceError,'KEY_PERMISSIONS'):
   signed_record(self.payload,self.keyfile,self.plan['cpu_public_key_hex'])
 def test_late_source_feature_not_backdated(self):
  with self.assertRaisesRegex(EvidenceError,'TOO_EARLY_OR_LATE'):self.verify(now_ms=self.anchor*1000+49999)
 def test_feature_after_fixed_four_minute_cutoff_fails(self):
  self.store_signed({**self.payload,'generated_at_ms':(self.anchor+240)*1000})
  with self.assertRaisesRegex(EvidenceError,'TOO_EARLY_OR_LATE'):
   self.verify(now_ms=(self.anchor+260)*1000)
 def test_rocm_wheel_cpu_cannot_impersonate_canonical_source(self):
  self.store_signed({**self.payload,'torch_version':'2.11.0+rocm7.13.0'})
  with self.assertRaisesRegex(EvidenceError,'RECEIPT_SCOPE'):self.verify()
 def test_wrong_input_text_refused(self):
  self.store_signed({**self.payload,'source_text_sha256':'1'*64})
  with self.assertRaisesRegex(EvidenceError,'INPUT_OR_ENV'):self.verify()
 def test_wrong_model_hash_refused(self):
  self.store_signed({**self.payload,'encoder_model_sha256':'1'*64})
  with self.assertRaisesRegex(EvidenceError,'INPUT_OR_ENV'):self.verify()
 def test_wrong_future_candidate_refused(self):
  p=dict(self.candidate);p['candidate_id']='unregistered'
  with self.assertRaisesRegex(EvidenceError,'RECEIPT_SCOPE'):
   verify_feature(self.folder,self.plan,p,self.row,self.anchor,self.anchor*1000+80000,source_input_file=self.inp)
 def test_live_model_authority_refused(self):
  self.store_signed({**self.payload,'trade_authority':True})
  with self.assertRaisesRegex(EvidenceError,'RECEIPT_SCOPE'):self.verify()
 def test_optimizer_and_broker_claims_refused(self):
  for key in ('new_training_steps','broker_orders_sent'):
   self.store_signed({**self.payload,key:1})
   with self.assertRaisesRegex(EvidenceError,'RECEIPT_SCOPE'):self.verify()
 def test_unknown_receipt_key_refused_even_when_signed(self):
  self.store_signed({**self.payload,'unreviewed_source_override':'yes'})
  with self.assertRaisesRegex(EvidenceError,'RECEIPT_FIELDS'):self.verify()
 def test_original_cpu_encoder_cannot_run_on_rocm_cpu_backend(self):
  import torch
  from unittest.mock import patch
  from historical_training.neuron256_frozen_readout import encode_cpu
  with patch.object(torch,'__version__','2.11.0+rocm7.13.0'):
   with self.assertRaisesRegex(EvidenceError,'CANONICAL_CPU_RUNTIME_REQUIRED'):
    encode_cpu(self.root,[],{'model_file_sha256':'missing','tokenizer_file_sha256':'missing'})
