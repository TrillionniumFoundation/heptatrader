"""Value shares heavy admission and source checks without blocking itself."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from historical_training import resident_resource_guard as guard
from historical_training.common import EvidenceError, digest, sha

NOW = 1792494000000

class ValuePredictorResourceGateTests(unittest.TestCase):
 def setUp(self):
  temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name)
  original=self.root/'original.json';original.write_text(json.dumps({'automatic_trading':False}))
  period=self.root/'period.json';period.write_text(json.dumps({'execution_domain':'PAPER'}))
  self.health=self.root/'source-health.json'
  self.config={'state_root':str(self.root/'guard'),'original_config':str(original),
   'original_config_sha256':sha(original),'period_path':str(period),'period_sha256':sha(period),
   'source_health_path':str(self.health),'manual_hold_path':str(self.root/'manual-hold'),
   'cache_maintenance':None}
  prefixes=[{'path':str(self.root/'source.jsonl'),'sha256':'0'*64,'bytes':1}]
  self.source={'schema':'heptatrader.quote-readiness-status.v1','period_sha256':sha(period),
   'at_ms':NOW-1000,'latest_source_quote_ms':NOW-1500,'usable':24,'unusable':0,
   'state':'READY_FOR_REVIEW_NOT_AUTHORIZATION','rule':copy.deepcopy(guard.SOURCE_RULE),
   'source_prefixes':prefixes,'source_prefix_digest':digest(prefixes)}
  self.health.write_text(json.dumps(self.source))
  for p in (original,period,self.health):p.chmod(0o600)
  self.available=10*guard.GIB;self.pressure='full avg10=0.00 avg60=0.00 avg300=0.00 total=0\n'
  original_read=Path.read_text
  def read_text(path,*args,**kwargs):
   if str(path)=='/proc/meminfo':return ('MemTotal: 25165824 kB\n'+f'MemAvailable: {self.available//1024} kB\n'+'SwapTotal: 8388608 kB\nSwapFree: 0 kB\n')
   if str(path)=='/proc/pressure/memory':return self.pressure
   return original_read(path,*args,**kwargs)
  for patcher in (mock.patch.object(guard.time,'time',return_value=NOW/1000),mock.patch.object(Path,'read_text',new=read_text)):
   patcher.start();self.addCleanup(patcher.stop)
  patcher=mock.patch('historical_training.resident_live_source.refresh',side_effect=lambda *a:self.fresh())
  self.refresh=patcher.start();self.addCleanup(patcher.stop)
 def fresh(self,source=None):
  return (copy.deepcopy(self.source if source is None else source),{'fresh_original_live_source_proven':True,'source_ttl_ms':5000,'broker_queries_sent':0})
 def gate(self,role='value-predictor',**changes):
  result=guard.Gate({**self.config,**changes},role);self.addCleanup(result.release);return result
 def test_two_fresh_checks_and_heavy_reserve(self):
  g=self.gate();self.assertTrue(g.acquire({'requests':['r1']}));self.assertEqual(self.refresh.call_count,2)
  self.assertIsNotNone(g.lease.fd);self.assertEqual(g.last_admission['resource_role'],'value-predictor')
  self.assertEqual(g.last_admission['minimum_available_bytes'],8*guard.GIB);self.assertFalse(g.last_admission['automatic_trading'])
 def test_no_self_priority(self):
  with mock.patch('historical_training.value_priority_admission.verify_policy') as verify, mock.patch('historical_training.value_priority_admission.decision',side_effect=AssertionError('SELF_PRIORITY')) as decision:
   self.assertTrue(self.gate(value_priority={'not_background':True}).acquire({'request':'r1'}))
  verify.assert_not_called();decision.assert_not_called();self.assertEqual(self.refresh.call_count,2)
 def test_background_roles_still_obey_priority(self):
  with mock.patch('historical_training.value_priority_admission.verify_policy') as verify, mock.patch('historical_training.value_priority_admission.decision',return_value=(True,'VALUE_PRIORITY_FRESH_FLAT_ORIGINAL_REQUEST',{})) as decision:
   for role in ('trainer','evaluator'):
    g=self.gate(role,value_priority={'pinned':True});self.assertFalse(g.acquire({'role':role}))
    self.assertIn('VALUE_PRIORITY_FRESH_FLAT_ORIGINAL_REQUEST',g.last_admission['reasons'])
  self.assertEqual(verify.call_count,2);self.assertEqual(decision.call_count,2);self.refresh.assert_not_called()
 def test_all_heavy_roles_keep_eight_gib(self):
  m={'MemTotal':24*guard.GIB,'MemAvailable':8*guard.GIB-1,'SwapTotal':8*guard.GIB,'SwapFree':0}
  for role in ('trainer','evaluator','value-predictor'):
   r=guard.assess_resources(m,{'avg10':0.,'avg60':0.},NOW,False,role=role)
   self.assertFalse(r['admit_new_worker']);self.assertIn('HOST_AVAILABLE_MEMORY_BELOW_8GIB',r['reasons'])
  m['MemAvailable']=2*guard.GIB
  self.assertTrue(guard.assess_resources(m,{'avg10':0.,'avg60':0.},NOW,False,role='forward_registration')['admit_new_worker'])
 def test_pressure_denies_before_refresh(self):
  self.pressure='full avg10=1.01 avg60=0.00 avg300=0.00 total=0\n';g=self.gate()
  self.assertFalse(g.acquire({'request':'r1'}));self.assertIn('HOST_FULL_MEMORY_PSI_PRESSURE',g.last_admission['reasons'])
  self.refresh.assert_not_called();self.assertIsNone(g.lease.fd)
 def test_manual_hold(self):
  Path(self.config['manual_hold_path']).write_text('hold');g=self.gate()
  self.assertFalse(g.acquire({'request':'r1'}));self.assertIn('OPERATOR_RESOURCE_HOLD',g.last_admission['reasons']);self.refresh.assert_not_called()
 def test_fresh_status_does_not_refresh_old_quote(self):
  self.source.update(at_ms=NOW,latest_source_quote_ms=NOW-5001);g=self.gate()
  self.assertFalse(g.acquire({'request':'r1'}));self.assertIn('LATEST_SOURCE_QUOTE_NOT_FRESH',g.last_admission['reasons'])
 def test_wrong_period(self):
  self.source['period_sha256']='other';g=self.gate();self.assertFalse(g.acquire({'request':'r1'}))
  self.assertIn('SOURCE_PERIOD_OR_SCHEMA_UNBOUND',g.last_admission['reasons'])
 def test_source_lost_after_lock_releases_lease(self):
  self.refresh.side_effect=[self.fresh(),self.fresh({**self.source,'latest_source_quote_ms':NOW-5001})]
  g=self.gate();self.assertFalse(g.acquire({'request':'r1'}));self.assertEqual(self.refresh.call_count,2);self.assertIsNone(g.lease.fd)
  other=guard.Lease(Path(self.config['state_root'])/'heavy-worker.lock');self.addCleanup(other.release);self.assertTrue(other.acquire())
 def test_existing_worker_never_preempted(self):
  owner=guard.Lease(Path(self.config['state_root'])/'heavy-worker.lock');self.addCleanup(owner.release);self.assertTrue(owner.acquire())
  g=self.gate();self.assertFalse(g.acquire({'request':'r1'}));self.assertEqual(g.last_admission['reasons'],['OTHER_RESEARCH_WORKER_ACTIVE'])
  self.assertIsNotNone(owner.fd);g.release();other=guard.Lease(owner.path);self.addCleanup(other.release);self.assertFalse(other.acquire())
  owner.release();self.assertTrue(g.acquire({'request':'r2'}))
 def test_no_light_registration_escape(self):
  with self.assertRaisesRegex(EvidenceError,'FORWARD_PREFLIGHT_SUBJECT_INVALID'):
   self.gate().check(forward_registration={'job_id':'j','candidate_id':'c'})
  self.refresh.assert_not_called()
 def test_unknown_roles_rejected(self):
  for role in ('predictor','forward_registration','',None):
   with self.assertRaisesRegex(EvidenceError,'RESEARCH_ROLE_REQUIRED'):self.gate(role)
  with self.assertRaisesRegex(EvidenceError,'RESOURCE_ADMISSION_ROLE_REQUIRED'):
   guard.assess_resources({},{},NOW,False,role='predictor')
 def test_no_training_cache_cleanup(self):
  with mock.patch('historical_training.resident_comgr_cache.sweep_terminal',side_effect=AssertionError('NO_CACHE_DUTY')) as sweep:
   g=self.gate(cache_maintenance='TERMINAL_TASK_COMGR_ONLY_V1');self.assertTrue(g.acquire({'request':'r1'}));g.release()
   self.assertIsNone(g.lease.fd);self.assertFalse((g.root/'value-predictor-cache-maintenance.json').exists())
  sweep.assert_not_called()

if __name__=='__main__':unittest.main()
