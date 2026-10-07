"""Actual local Ed25519 witnesses and fail-closed native/selector contracts.

All examples in this suite are synthetic; passing a test is never OOS evidence.
"""
import copy,json,os,subprocess,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
from laya_cell.integrity import canonical,digest,sha_file,ContractError
from laya_cell.financial_forward_witness import sign_record,verify_record,verify_trust
from laya_cell.financial_forward_protocol import RULE
from laya_cell.financial_forward_evaluate import read_prediction
from laya_cell.financial_forward_select import evaluate_gate,native

class TrustFixture(unittest.TestCase):
 def setUp(self):
  self.t=tempfile.TemporaryDirectory();self.addCleanup(self.t.cleanup);self.root=Path(self.t.name)
  script='''from pathlib import Path
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization as s
import json,sys
root=Path(sys.argv[1]);keys={}
for role in ('registrar','predictor','evaluator','selector'):
 k=Ed25519PrivateKey.generate();(root/(role+'.key')).write_bytes(k.private_bytes(s.Encoding.Raw,s.PrivateFormat.Raw,s.NoEncryption()))
 keys[role]=k.public_key().public_bytes(s.Encoding.Raw,s.PublicFormat.Raw).hex()
print(json.dumps(keys))'''
  keys=json.loads(subprocess.check_output(['/usr/bin/python3','-c',script,str(self.root)],text=True))
  trust={'scope':'1'*64,'roles':keys,'canonical_agentd_issuer':False};p=self.root/'trust.json';p.write_bytes(canonical(trust))
  self.c={'period_sha256':'1'*64,'witness_trust_path':str(p),'witness_trust_sha256':sha_file(p),'witness_trust_digest':digest(trust)}
 def role(self,name):return {**self.c,'signing_role':name,'role_key_path':str(self.root/(name+'.key'))}

class RoleWitnessTests(TrustFixture):
 def test_real_ed25519_sign_verify(self):
  r=sign_record(self.role('predictor'),'predictor',{'value':1});self.assertEqual(verify_record(self.c,'predictor',r),r)
 def test_wrong_role_cannot_sign(self):
  with self.assertRaisesRegex(ContractError,'WRONG_PROCESS'):sign_record(self.role('predictor'),'evaluator',{})
 def test_model_writer_cannot_forge_evaluation_signature(self):
  c=self.role('evaluator');c['role_key_path']=str(self.root/'predictor.key')
  with self.assertRaises(ValueError):sign_record(c,'evaluator',{'state':'PASS'})
 def test_tampered_signed_payload_rejected(self):
  r=sign_record(self.role('evaluator'),'evaluator',{'state':'PENDING'});r['state']='PASS'
  with self.assertRaises(ValueError):verify_record(self.c,'evaluator',r)
 def test_relabelled_role_rejected(self):
  r=sign_record(self.role('predictor'),'predictor',{});r['role_witness']['role']='evaluator'
  with self.assertRaises((ValueError,ContractError)):verify_record(self.c,'evaluator',r)
 def test_public_trust_changes_fail(self):
  r=sign_record(self.role('evaluator'),'evaluator',{});Path(self.c['witness_trust_path']).write_text('{}')
  with self.assertRaisesRegex(ContractError,'TRUST_CHANGED'):verify_record(self.c,'evaluator',r)
 def test_role_keys_must_be_distinct(self):
  p=Path(self.c['witness_trust_path']);t=json.loads(p.read_bytes());t['roles']['evaluator']=t['roles']['predictor'];p.write_bytes(canonical(t))
  c={**self.c,'witness_trust_sha256':sha_file(p),'witness_trust_digest':digest(t)}
  with self.assertRaisesRegex(ContractError,'NOT_DISTINCT'):verify_trust(c)
 def test_no_false_canonical_role_assertion(self):
  r=sign_record(self.role('evaluator'),'evaluator',{});self.assertFalse(r['role_witness']['canonical_agentd_admission'])
 def test_unsigned_json_pass_is_rejected(self):
  with self.assertRaisesRegex(ContractError,'ROLE_OR_SCOPE'):verify_record(self.c,'evaluator',{'state':'PASS'})
 def test_cross_period_signature_not_reused(self):
  r=sign_record(self.role('evaluator'),'evaluator',{})
  with self.assertRaisesRegex(ContractError,'SCOPE'):verify_record({**self.c,'period_sha256':'2'*64},'evaluator',r)

class PredictionCommitTests(TrustFixture):
 def commit(self):
  plan={'plan_id':'plan','candidate_id':'candidate'};folder=self.root/'plans/plan/predictions/request';folder.mkdir(parents=True)
  payload={'request_id':'request','candidate_id':'candidate','plan_id':'plan','values_usd':{'BUY':1.,'SELL':-1.}}
  (folder/'prediction.json').write_bytes(canonical(payload));now=int(time.time()*1000)
  raw={'plan_id':'plan','candidate_id':'candidate','request_id':'request','prediction_sha256':sha_file(folder/'prediction.json'),'committed_at_ms':now}
  c=sign_record(self.role('predictor'),'predictor',raw);(folder/'commit.json').write_bytes(canonical(c))
  return plan,folder,c
 def test_signed_commit_loads_exact_prediction(self):
  p,d,c=self.commit();out=read_prediction(self.root,p,'request',self.c);self.assertEqual(out['values_usd']['SELL'],-1.)
 def test_prediction_mutation_after_signed_commit_fails(self):
  p,d,c=self.commit();(d/'prediction.json').write_text('{}')
  with self.assertRaises(ContractError):read_prediction(self.root,p,'request',self.c)
 def test_wrong_candidate_signed_commit_fails(self):
  p,d,c=self.commit();p['candidate_id']='other'
  with self.assertRaises(ContractError):read_prediction(self.root,p,'request',self.c)
 def test_future_claimed_committed_timestamp_fails(self):
  p,d,c=self.commit();raw={k:v for k,v in c.items() if k not in ('role_witness','signature')};raw['committed_at_ms']=int(time.time()*1000)+10000
  c=sign_record(self.role('predictor'),'predictor',raw);(d/'commit.json').write_bytes(canonical(c))
  with self.assertRaisesRegex(ContractError,'WITNESS'):read_prediction(self.root,p,'request',self.c)
 def test_signature_from_registrar_does_not_witness_prediction(self):
  p,d,c=self.commit();raw={k:v for k,v in c.items() if k not in ('role_witness','signature')};c=sign_record(self.role('registrar'),'registrar',raw);(d/'commit.json').write_bytes(canonical(c))
  with self.assertRaisesRegex(ContractError,'ROLE_OR_SCOPE'):read_prediction(self.root,p,'request',self.c)

class SelectorGateTests(TrustFixture):
 def make(self,state='STATISTICALLY_QUALIFIED_REQUIRES_NATIVE_OWNER'):
  now=int(time.time()*1000);p={'plan_id':'p','candidate_id':'c','outcome_end_ms':now-1}
  s=sign_record(self.role('evaluator'),'evaluator',{'plan_id':'p','candidate_id':'c','state':state,'reasons':[],'selection_performed':False})
  return p,s
 def test_real_signed_pass_only_reaches_gate_not_selection(self):
  p,s=self.make();self.assertTrue(evaluate_gate(self.c,p,s,int(time.time()*1000)))
 def test_pending_never_selects(self):
  p,s=self.make('PENDING');self.assertFalse(evaluate_gate(self.c,p,s,int(time.time()*1000)))
 def test_stale_assessment_not_replayed(self):
  p,s=self.make()
  with self.assertRaisesRegex(ContractError,'CURRENT_EVALUATION'):evaluate_gate(self.c,p,s,int(time.time()*1000)+60001)
 def test_window_cannot_finish_early(self):
  p,s=self.make();p['outcome_end_ms']=int(time.time()*1000)+86400000
  with self.assertRaisesRegex(ContractError,'NOT_COMPLETE'):evaluate_gate(self.c,p,s,int(time.time()*1000))
 def test_other_candidate_cannot_borrow_signature(self):
  p,s=self.make();p['candidate_id']='other'
  with self.assertRaisesRegex(ContractError,'SCOPE'):evaluate_gate(self.c,p,s,int(time.time()*1000))
 def test_native_accepted_boolean_cannot_substitute_certificates(self):
  binary=os.environ.get('FINANCIAL_NATIVE_VERIFIER')
  if not binary:self.skipTest('separate real-native suite supplies verifier path')
  c={'canonical_verifier_binary':binary,'canonical_verifier_sha256':sha_file(Path(binary))}
  with self.assertRaisesRegex(ContractError,'REJECTED'):native(c,{'operation':'verify_current_selection','owner_accepted':True,'signature':'fake'})

if __name__=='__main__':unittest.main(verbosity=2)
