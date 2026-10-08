"""No trading; rejects low-coverage and retrospective re-registration."""
from __future__ import annotations
from collections import Counter
from copy import deepcopy
import tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
from laya_cell.integrity import ContractError,digest
from paired_price.source_gate import verify_quality,require_unused_candidate,require_live_quality
from laya_cell.source_readiness import RULE as SOURCE_RULE

PERIOD='1'*64
NOW=1791439000000

def quality(now=NOW,usable=24):
 last=((now-365000)//SOURCE_RULE['spacing_ms'])*SOURCE_RULE['spacing_ms']
 starts=[last-i*SOURCE_RULE['spacing_ms'] for i in reversed(range(24))]
 probes=[{'start_ms':t,'status':'USABLE' if i<usable else 'ENDPOINT_GAP'} for i,t in enumerate(starts)]
 source=[{'path':'/retained/immutable-prefix','bytes':1024,'sha256':'a'*64}]
 return {'schema':'heptatrader.quote-readiness-status.v1','at_ms':now,'period_sha256':PERIOD,
  'rule':SOURCE_RULE,'state':'READY_FOR_REVIEW_NOT_AUTHORIZATION','reasons':[],
  'probes':probes,'counts':dict(Counter(p['status'] for p in probes)),'usable':usable,'unusable':24-usable,
  'latest_quote_age_ms':2500,'source_prefixes':source,'source_prefix_digest':digest(source),
  'automatic_trial_registration':False,'trading_authority':False,'orders_sent':0,
  'past_probe_labels_are_not_independent_efficacy_samples':True,'old_failed_windows_changed':False}

class SourceGateTests(unittest.TestCase):
 def setUp(self):self.config={'period_sha256':PERIOD,'automatic_trading':False}
 def test_exact20of24_allows_only_review(self):
  r=verify_quality(self.config,quality(usable=20),NOW+1000)
  self.assertEqual((r['usable'],r['missing']),(20,4))
  self.assertFalse(r['trading_authority']);self.assertFalse(r['automatic_trial_registration'])
  self.assertTrue(r['evidence_is_prior_operational_quality_not_future_efficacy'])
 def test_19of24_never_accepted_even_if_status_forged_ready(self):
  with self.assertRaisesRegex(ContractError,'COVERAGE'):verify_quality(self.config,quality(usable=19),NOW)
 def test_status_hold_denied(self):
  v=quality();v['state']='HOLD_NEW_FUSION_WINDOW'
  with self.assertRaises(ContractError):verify_quality(self.config,v,NOW)
 def test_frozen_old_period_and_current_source_scope(self):
  with self.assertRaisesRegex(ContractError,'PERIOD'):verify_quality({**self.config,'period_sha256':'2'*64},quality(),NOW)
 def test_no_frozen_or_edited_experiment_conditions(self):
  for change in ({'trading_authority':True},{'automatic_trial_registration':True},{'orders_sent':1},{'old_failed_windows_changed':True},
     {'past_probe_labels_are_not_independent_efficacy_samples':False}):
   v=quality();v.update(change)
   with self.subTest(change=change),self.assertRaises(ContractError):verify_quality(self.config,v,NOW)
 def test_stale_or_future_health_cannot_register(self):
  with self.assertRaisesRegex(ContractError,'STALE'):verify_quality(self.config,quality(),NOW+15001)
  with self.assertRaisesRegex(ContractError,'STALE'):verify_quality(self.config,quality(),NOW-1)
 def test_latest_stale_quote_not_retimed(self):
  v=quality();v['latest_quote_age_ms']=5001
  with self.assertRaisesRegex(ContractError,'QUOTE_STALE'):verify_quality(self.config,v,NOW)
 def test_original_five_second_target_and_30second_path_immutable(self):
  v=quality();v['rule']={**SOURCE_RULE,'endpoint_tolerance_ms':10000}
  with self.assertRaisesRegex(ContractError,'RULE'):verify_quality(self.config,v,NOW)
 def test_matured_fixed_start_times_required(self):
  v=quality();v['probes'][-1]['start_ms']+=60000
  with self.assertRaisesRegex(ContractError,'WINDOW_NOT_FIXED'):verify_quality(self.config,v,NOW)
  v=quality();v['probes'][-1]['start_ms']+=1
  with self.assertRaisesRegex(ContractError,'WINDOW_NOT_FIXED'):verify_quality(self.config,v,NOW)
 def test_no_future_outcomes_before_report(self):
  v=quality();v['probes']=[{'start_ms':NOW+i*60000,'status':'USABLE'} for i in range(24)]
  with self.assertRaisesRegex(ContractError,'UNMATURED'):verify_quality(self.config,v,NOW)
 def test_changed_count_or_missing_probe_refused(self):
  v=quality();v['counts']['USABLE']=23
  with self.assertRaisesRegex(ContractError,'COUNT_CONFLICT'):verify_quality(self.config,v,NOW)
  v=quality();v['probes'].pop()
  with self.assertRaisesRegex(ContractError,'PROBE_COUNT'):verify_quality(self.config,v,NOW)
 def test_bad_prefix_hash_refused(self):
  v=quality();v['source_prefixes'][0]['sha256']='0'*64
  with self.assertRaisesRegex(ContractError,'PREFIX_IDENTITY'):verify_quality(self.config,v,NOW)
 def test_report_from_live_assessor_is_never_user_provided(self):
  with patch('paired_price.source_gate.assess',return_value=quality()):
   with patch('paired_price.source_gate.time.time',return_value=NOW/1000):
    self.assertEqual(require_live_quality(self.config)['usable'],24)
 def test_cannot_repeat_old_candidate(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t)/'.local/state/heptatrader/neuron-learning/paired-price'/PERIOD/('2'*64)/'old-release'
   root.mkdir(parents=True);(root/'plan.json').write_text('retained and rejected')
   with self.assertRaisesRegex(ContractError,'NO_REPEAT'):require_unused_candidate(t,PERIOD,'2'*64)
   self.assertEqual((root/'plan.json').read_text(),'retained and rejected')
 def test_new_candidate_identity_and_old_period_isolation(self):
  with tempfile.TemporaryDirectory() as t:
   self.assertTrue(require_unused_candidate(t,PERIOD,'2'*64))
   with self.assertRaisesRegex(ContractError,'FORMAT'):require_unused_candidate(t,PERIOD,'../escaped')
 def test_unknown_provider_data_cannot_be_passed_as_price_efficacy(self):
  v=quality();v['past_probe_labels_are_not_independent_efficacy_samples']=False
  with self.assertRaises(ContractError):verify_quality(self.config,v,NOW)

if __name__=='__main__':unittest.main(verbosity=2)
