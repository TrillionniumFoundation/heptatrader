import base64, copy, hashlib, unittest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
from historical_training.value_timing_reconcile import verify_signature, reconcile, quantiles
from historical_training.value_export_reconcile import canonical, digest


def fixture(cases=('OBSERVED',),front_timing=None):
 keys={r:Ed25519PrivateKey.generate() for r in ('registrar','predictor','evaluator','selector')}
 trust={'scope':'period','canonical_agentd_issuer':False,'roles':{r:k.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw).hex() for r,k in keys.items()}}
 td=digest(trust)
 def sign(record,role):
  p={**record,'role_witness':{'role':role,'public_key':trust['roles'][role],'scope':'period','trust_digest':td,'canonical_agentd_admission':False,'signed_at_ms':4000 if role=='predictor' else 10000}}
  p['signature']=base64.b64encode(keys[role].sign(canonical(p))).decode();return p
 items=[];semantics=[]
 for index,kind in enumerate(cases):
  rid='r' if index==0 else 'r'+str(index);cid='command' if index==0 else 'command'+str(index)
  status=kind if kind in ('OBSERVED','PENDING') else 'CENSORED'
  reason={'OBSERVED':None,'PENDING':'ORIGINAL_TRADE_NOT_YET_CLOSED_AND_SETTLED','LATE':'NOT_COMMITTED_BEFORE_ORIGINAL_OPEN','MISSING':'ACTUAL_OPEN_WITHOUT_EXACT_PLAN_BINDING'}[kind]
  result={'command_id':cid,'request_id':None if kind=='MISSING' else rid,'candidate_id':'candidate','plan_id':'plan','observed_at_ms':9000,'status':status,'reason':reason,'opened_at_ms':3500 if kind=='LATE' else 5000}
  item={}
  if kind!='MISSING':
   pred={'request_id':rid,'candidate_id':'candidate','plan_id':'plan','request_started_at_ms':1000,'request_source_written_at_ms':1100,'computed_at_ms':3900,'inference_seconds':1.}
   if front_timing is not None:pred['front_timing']=copy.deepcopy(front_timing)
   pb=canonical(pred)+b'\n';ph=hashlib.sha256(pb).hexdigest()
   commit=sign({'request_id':rid,'candidate_id':'candidate','plan_id':'plan','committed_at_ms':3999,'prediction_sha256':ph},'predictor')
   cb=canonical(commit)+b'\n';ch=hashlib.sha256(cb).hexdigest()
   result.update(prediction_sha256=ph,commit_sha256=ch)
   if status=='OBSERVED':result['prediction_committed_at_ms']=4000
   item.update(prediction=pred,commit=commit,prediction_file_sha256=ph,commit_file_sha256=ch,prediction_raw_b64=base64.b64encode(pb).decode(),commit_raw_b64=base64.b64encode(cb).decode())
  sem={k:v for k,v in result.items() if k!='observed_at_ms'};semantics.append(sem)
  o={**result,'source_ledger_prefix':{},'semantic_id':digest(sem)};o['record_id']=digest(o);item['outcome']=o;items.append(item)
 assess={'plan_id':'plan','candidate_id':'candidate','observed_trades':sum(x=='OBSERVED' for x in cases),'total_original_trades':len(items),'outcome_set_digest':digest(semantics)}
 return {'trust':trust,'public_pins':{'witness_trust_digest':td,'period_sha256':'period'},'assessment':assess,'assessment_signed':sign(assess,'evaluator'),'captured_at_ms':11000,'items':items}


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

 def test_open_coverage_keeps_pending_late_and_missing_denominator(self):
  r=reconcile(fixture(('OBSERVED','PENDING','LATE','MISSING')));c=r['open_coverage']
  self.assertEqual(c['total_original_opens'],4);self.assertEqual(c['signed_before_original_open'],2)
  self.assertEqual(c['signed_at_or_after_original_open'],1);self.assertEqual(c['no_verified_commit'],1)
  self.assertEqual(c['preopen_commit_coverage'],.5);self.assertEqual(c['observed_settled_trades'],1)
  self.assertEqual(c['request_binding_missing'],1)
  self.assertEqual(r['not_preopen_reason_counts']['late_signed_commit'],{'NOT_COMMITTED_BEFORE_ORIGINAL_OPEN':1})
  self.assertEqual(r['not_preopen_reason_counts']['no_verified_commit'],{'ACTUAL_OPEN_WITHOUT_EXACT_PLAN_BINDING':1})
 def test_tail_quantiles(self):
  q=quantiles([0,100]);self.assertEqual(q['p95'],95);self.assertEqual(q['p99'],99)
 def test_legacy_capture_does_not_invent_phase_measurements(self):
  r=reconcile(fixture());self.assertEqual(r['front_timing_commits'],0);self.assertEqual(r['phase_latency_ms'],{'preopen':{},'late':{}})
 def test_signed_front_phases_appear_in_tail_summary(self):
  front={'schema':'heptatrader.value-prediction-front-timing.v1','worker_started_at_ms':1500,'prediction_entered_at_ms':2000,'worker_phases_ms':{'ingress_validation':200.},'prediction_phases_ms':{'model_load':300.,'inference':1000.}}
  r=reconcile(fixture(front_timing=front));self.assertEqual(r['front_timing_commits'],1)
  self.assertEqual(r['phase_latency_ms']['preopen']['prediction.model_load']['p99'],300.)
  self.assertEqual(r['timings'][0]['request_to_prediction_entry_ms'],1000)
 def test_signed_negative_phase_is_still_invalid(self):
  front={'schema':'heptatrader.value-prediction-front-timing.v1','worker_phases_ms':{'ingress_validation':-1}}
  with self.assertRaises(Exception):reconcile(fixture(front_timing=front))


class SignedOutcomeOrderTests(unittest.TestCase):
 def fixture(self, ordered=True):
  keys={r:Ed25519PrivateKey.generate() for r in ('registrar','predictor','evaluator','selector')}
  trust={'scope':'period','canonical_agentd_issuer':False,
   'roles':{r:k.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw).hex() for r,k in keys.items()}}
  td=digest(trust);items=[];semantic=[]
  for i in range(2):
   body={'command_id':'command-'+str(i),'request_id':None,'candidate_id':'candidate','plan_id':'plan',
    'opened_at_ms':5000+i,'status':'CENSORED','reason':'NO_PREDICTION_FOR_ORIGINAL_REQUEST'}
   semantic.append(body);record={**body,'observed_at_ms':9000,'source_ledger_prefix':{},'semantic_id':digest(body)}
   record['record_id']=digest(record);items.append({'outcome':record})
  assessment={'plan_id':'plan','candidate_id':'candidate','observed_trades':0,'total_original_trades':2,'outcome_set_digest':digest(semantic)}
  if ordered:assessment['outcome_record_ids']=[x['outcome']['record_id'] for x in items]
  capture={'trust':trust,'public_pins':{'witness_trust_digest':td,'period_sha256':'period'},
   'assessment':assessment,'captured_at_ms':11000,'items':items}
  def seal():
   signed={**capture['assessment'],'role_witness':{'role':'evaluator','public_key':trust['roles']['evaluator'],
    'scope':'period','trust_digest':td,'canonical_agentd_admission':False,'signed_at_ms':10000}}
   signed['signature']=base64.b64encode(keys['evaluator'].sign(canonical(signed))).decode();capture['assessment_signed']=signed
  seal();return capture,seal
 def test_signed_order_recovers_capture_enumeration(self):
  c,_=self.fixture();c['items'].reverse();r=reconcile(c)
  self.assertEqual(r['commands'],2);self.assertTrue(r['signed_outcome_set_verified']);self.assertTrue(r['signed_outcome_record_order_verified'])
 def test_legacy_order_retains_original_digest(self):
  c,_=self.fixture(False);self.assertEqual(reconcile(c)['commands'],2);c['items'].reverse()
  with self.assertRaisesRegex(Exception,'SIGNED_OUTCOME_SET_DIGEST'):reconcile(c)
 def test_duplicate_or_null_signed_order_rejected(self):
  for invalid in (None,[],['0'*64,'0'*64]):
   c,seal=self.fixture();c['assessment']['outcome_record_ids']=invalid;seal()
   with self.assertRaisesRegex(Exception,'SIGNED_OUTCOME_RECORD_ORDER'):reconcile(c)
 def test_missing_or_substituted_record_rejected(self):
  c,_=self.fixture();c['items'][1]=copy.deepcopy(c['items'][0])
  with self.assertRaisesRegex(Exception,'SIGNED_OUTCOME_RECORD_MEMBERSHIP'):reconcile(c)
 def test_order_does_not_replace_semantic_digest_check(self):
  c,seal=self.fixture();c['assessment']['outcome_record_ids'].reverse();seal()
  with self.assertRaisesRegex(Exception,'SIGNED_OUTCOME_SET_DIGEST'):reconcile(c)

if __name__=='__main__':unittest.main()
