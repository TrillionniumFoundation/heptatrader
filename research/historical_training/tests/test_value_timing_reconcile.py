import base64, copy, hashlib, unittest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
from historical_training.value_timing_reconcile import verify_signature, reconcile, quantiles
from historical_training.value_export_reconcile import canonical, digest


def fixture():
 keys={r:Ed25519PrivateKey.generate() for r in ('registrar','predictor','evaluator','selector')}
 trust={'scope':'period','canonical_agentd_issuer':False,'roles':{r:k.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw).hex() for r,k in keys.items()}}
 td=digest(trust)
 def sign(record,role):
  p={**record,'role_witness':{'role':role,'public_key':trust['roles'][role],'scope':'period','trust_digest':td,'canonical_agentd_admission':False,'signed_at_ms':4000 if role=='predictor' else 10000}}
  p['signature']=base64.b64encode(keys[role].sign(canonical(p))).decode();return p
 pred={'request_id':'r','candidate_id':'candidate','plan_id':'plan','request_started_at_ms':1000,'request_source_written_at_ms':1100,'computed_at_ms':3900,'inference_seconds':1.}
 pb=canonical(pred)+b'\n';ph=hashlib.sha256(pb).hexdigest()
 commit=sign({'request_id':'r','candidate_id':'candidate','plan_id':'plan','committed_at_ms':3999,'prediction_sha256':ph},'predictor')
 cb=canonical(commit)+b'\n';ch=hashlib.sha256(cb).hexdigest()
 result={'command_id':'command','request_id':'r','candidate_id':'candidate','plan_id':'plan','observed_at_ms':9000,'status':'OBSERVED','reason':None,'prediction_sha256':ph,'commit_sha256':ch,'opened_at_ms':5000,'prediction_committed_at_ms':4000}
 sem={k:v for k,v in result.items() if k!='observed_at_ms'}
 o={**result,'source_ledger_prefix':{},'semantic_id':digest(sem)};o['record_id']=digest(o)
 assess={'plan_id':'plan','candidate_id':'candidate','observed_trades':1,'total_original_trades':1,'outcome_set_digest':digest([sem])}
 item={'outcome':o,'prediction':pred,'commit':commit,'prediction_file_sha256':ph,'commit_file_sha256':ch,'prediction_raw_b64':base64.b64encode(pb).decode(),'commit_raw_b64':base64.b64encode(cb).decode()}
 return {'trust':trust,'public_pins':{'witness_trust_digest':td,'period_sha256':'period'},'assessment':assess,'assessment_signed':sign(assess,'evaluator'),'captured_at_ms':11000,'items':[item]}

class TimingTest(unittest.TestCase):
 def test_signed_cohort(self):
  r=reconcile(fixture());self.assertEqual(r['predictor_signatures_verified'],1);self.assertEqual(r['groups']['preopen']['request_to_seal_ms']['p50'],3000)
 def mutate(self,fn):
  c=fixture();fn(c)
  with self.assertRaises(Exception):reconcile(c)
 def test_signature_tamper(self):self.mutate(lambda c:c['items'][0]['commit'].update(signature=base64.b64encode(b'0'*64).decode()))
 def test_role_substitution(self):self.mutate(lambda c:c['items'][0]['commit']['role_witness'].update(role='evaluator'))
 def test_wrong_trust(self):self.mutate(lambda c:c['trust']['roles'].update(predictor='00'*32))
 def test_future_witness(self):self.mutate(lambda c:c.update(captured_at_ms=1))
 def test_changed_prediction_bytes(self):self.mutate(lambda c:c['items'][0].update(prediction_raw_b64=base64.b64encode(b'{}').decode()))
 def test_changed_commit_bytes(self):self.mutate(lambda c:c['items'][0].update(commit_raw_b64=base64.b64encode(b'{}').decode()))
 def test_outcome_hash(self):self.mutate(lambda c:c['items'][0]['outcome'].update(opened_at_ms=3000))
 def test_missing_outcome(self):self.mutate(lambda c:c.update(items=[]))
 def test_duplicate_outcome(self):self.mutate(lambda c:c['items'].append(copy.deepcopy(c['items'][0])))
 def test_no_commit_not_observed(self):self.mutate(lambda c:c['items'][0].pop('commit'))
 def test_unsigned_assessment_changed(self):self.mutate(lambda c:c['assessment'].update(observed_trades=0))
 def test_empty_quantile(self):self.assertEqual(quantiles([]),{'count':0})
if __name__=='__main__':unittest.main()
