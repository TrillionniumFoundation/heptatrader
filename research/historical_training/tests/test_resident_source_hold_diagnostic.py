"""Prospective evaluator source HOLD tests; historical training has its own admission."""
from __future__ import annotations
import copy
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from historical_training.common import digest,sha
from historical_training.resident_resource_guard import (GIB,SOURCE_RULE,Gate,preflight_source_health_hold)

MEM={'MemTotal':24*GIB,'MemAvailable':11*GIB,'SwapTotal':8*GIB,'SwapFree':3*GIB}
PSI={'avg10':0.0,'avg60':0.0}

class ClosedMarketHoldTests(unittest.TestCase):
    def setUp(self):
        # Synthetic policy inputs must not require host PSI support merely to
        # reach the mocked parsers (containers may omit /proc/pressure).
        original_read_text=Path.read_text
        def telemetry(path,*args,**kwargs):
            if str(path)=='/proc/meminfo':
                return ''.join(f'{key}: {value//1024} kB\n' for key,value in MEM.items())
            if str(path)=='/proc/pressure/memory':
                return 'full avg10=0 avg60=0 avg300=0 total=0\n'
            return original_read_text(path,*args,**kwargs)
        reading=patch.object(Path,'read_text',new=telemetry)
        reading.start();self.addCleanup(reading.stop)
        attribution=patch('historical_training.resident_resource_guard.collect_resource_attribution',
            return_value={'diagnostic_only':True,'at_ms':0,'partial':True})
        attribution.start();self.addCleanup(attribution.stop)
        box=tempfile.TemporaryDirectory();self.addCleanup(box.cleanup)
        self.root=Path(box.name)
        self.original=self.root/'original.json'
        self.period=self.root/'period.json'
        self.health=self.root/'health.json'
        original={'automatic_trading':False,'runtime_sha256':{
            'learn/laya_cell/resident_plan_admission.py':'b'*64}}
        for path,raw in ((self.original,json.dumps(original,sort_keys=True).encode()+b'\n'),
                         (self.period,b'{"kind":"USER_RESET"}\n')):
            path.write_bytes(raw)
        current=int(time.time()*1000)
        pins=[{'path':str(self.root/'segment-1'/'quotes.jsonl'),'bytes':100,'sha256':'a'*64,'ended':False}]
        self.source={'schema':'heptatrader.quote-readiness-status.v1',
          'period_sha256':sha(self.period),'at_ms':current-1000,
          'latest_source_quote_ms':current-24000000,'usable':0,'unusable':24,
          'state':'HOLD_NEW_FUSION_WINDOW','rule':copy.deepcopy(SOURCE_RULE),
          'source_prefixes':pins,'source_prefix_digest':digest(pins),
          'reasons':['INSUFFICIENT_COMPLETE_HISTORY_SPAN',
                     'LATEST_AUTHORITATIVE_QUOTE_NOT_FRESH',
                     'INSUFFICIENT_ENDPOINT_AND_PATH_COVERAGE']}
        self.config={'original_config':str(self.original),'original_config_sha256':sha(self.original),
          'period_path':str(self.period),'period_sha256':sha(self.period),
          'source_health_path':str(self.health),
          'manual_hold_path':str(self.root/'manual-hold.json'),
          'state_root':str(self.root/'admission')}
        self.persist()
    def persist(self):
        self.health.write_text(json.dumps(self.source,sort_keys=True)+'\n')
    def check(self,role='evaluator',**kwargs):
        with patch('historical_training.resident_resource_guard.parse_meminfo',return_value=MEM),\
             patch('historical_training.resident_resource_guard.parse_full_psi',return_value=PSI):
            return Gate(self.config,role).check(**kwargs)

    def test_closed_market_hold_does_not_call_live_refresh_or_start_worker(self):
        with patch('historical_training.resident_live_source.refresh',
                   side_effect=AssertionError('NO_MARKET_REFRESH_ON_HELD_PROBES')) as refresh:
            r=self.check()
        refresh.assert_not_called()
        self.assertFalse(r['admit_new_worker'])
        self.assertIn('SOURCE_QUALITY_HOLD_NO_REFRESH_AUTHORITY',r['reasons'])
        self.assertIn('LATEST_SOURCE_QUOTE_NOT_FRESH',r['reasons'])
        self.assertIn('SOURCE_CONTINUITY_NOT_READY',r['reasons'])
        self.assertEqual(r['cached_probe_diagnostic']['cached_probe_usable'],0)
        self.assertEqual(r['cached_probe_diagnostic']['reported_source_reasons'],self.source['reasons'])
        self.assertFalse(r['original_live_source_evidence']['fresh_original_live_source_proven'])
        self.assertEqual(r['original_live_source_evidence']['broker_queries_sent'],0)
        self.assertEqual(r['role'],'evaluator')
        self.assertFalse(json.loads((Path(self.config['state_root'])/'evaluator-admission.json').read_text())['admit_new_worker'])

    def test_evaluator_hold_is_independently_denying(self):
        with patch('historical_training.resident_live_source.refresh',
                   side_effect=AssertionError('PROBE_NOT_NEEDED')):
            report=self.check('evaluator')
        self.assertFalse(report['admit_new_worker'])
        self.assertEqual(report['role'],'evaluator')

    def test_original_cached_prefix_digest_is_still_required_during_hold(self):
        self.source['source_prefixes'][0]['sha256']='b'*64
        self.persist()
        with patch('historical_training.resident_live_source.refresh',
                   side_effect=AssertionError('NO_VERIFICATION_BYPASS')):
            r=self.check()
        self.assertFalse(r['admit_new_worker'])
        self.assertIn('CONTINUITY_PREFIX_DIGEST_CHANGED',';'.join(r['reasons']))
        self.assertNotIn('cached_probe_diagnostic',r)

    def test_malformed_source_reason_is_denied_without_reporting_fake_quote(self):
        self.source['reasons']=['\nforged']
        self.persist()
        r=self.check()
        self.assertFalse(r['admit_new_worker'])
        self.assertIn('SOURCE_HEALTH_HOLD_REASON_FORMAT',';'.join(r['reasons']))

    def test_wrong_period_is_still_denied(self):
        self.source['period_sha256']='b'*64
        self.persist()
        r=self.check()
        self.assertFalse(r['admit_new_worker'])
        self.assertIn('SOURCE_PERIOD_OR_SCHEMA_UNBOUND',r['reasons'])

    def test_stale_status_is_still_denied(self):
        self.source['at_ms']-=1200000
        self.persist()
        r=self.check()
        self.assertFalse(r['admit_new_worker'])
        self.assertIn('SOURCE_STATUS_STALE_OR_FUTURE',r['reasons'])

    def test_changed_original_rule_is_not_accepted(self):
        self.source['rule']['source_ttl_ms']=10000
        self.persist()
        r=self.check()
        self.assertFalse(r['admit_new_worker'])
        self.assertIn('SOURCE_RULE_CHANGED',r['reasons'])

    def test_ready_status_keeps_original_verified_live_refresh_requirement(self):
        self.source.update(state='READY_FOR_REVIEW_NOT_AUTHORIZATION',
                           usable=24,unusable=0,reasons=[])
        self.persist()
        updated={**self.source,'latest_source_quote_ms':int(time.time()*1000)-500}
        proof={'schema':'synthetic-only-not-a-broker-result','broker_queries_sent':0}
        with patch('historical_training.resident_live_source.refresh',
                   return_value=(updated,proof)) as refresh,\
             patch('historical_training.resident_resource_guard.parse_meminfo',return_value=MEM),\
             patch('historical_training.resident_resource_guard.parse_full_psi',return_value=PSI):
            report=Gate(self.config,'evaluator').check()
        refresh.assert_called_once()
        self.assertTrue(report['admit_new_worker'])
        self.assertEqual(report['original_live_source_evidence'],proof)
        self.assertNotIn('cached_probe_diagnostic',report)

    def test_ready_status_without_original_live_evidence_stays_closed(self):
        self.source.update(state='READY_FOR_REVIEW_NOT_AUTHORIZATION',
                           usable=24,unusable=0,reasons=[])
        self.persist()
        with patch('historical_training.resident_live_source.refresh',
                   side_effect=ValueError('NO_CAUSAL_ORIGINAL_QUOTE')) as refresh:
            report=self.check()
        refresh.assert_called_once()
        self.assertFalse(report['admit_new_worker'])
        self.assertIn('NO_CAUSAL_ORIGINAL_QUOTE',';'.join(report['reasons']))

    def test_manual_hold_still_blocks(self):
        Path(self.config['manual_hold_path']).write_text('{}')
        r=self.check()
        self.assertFalse(r['admit_new_worker'])
        self.assertIn('OPERATOR_RESOURCE_HOLD',r['reasons'])


    def test_forward_registration_uses_source_gate_without_changing_training_state(self):
        subject={'job_id':'job','candidate_id':'c'*64}
        gate=Gate(self.config,'trainer')
        sentinel={'reason':'previous worker admission'}
        gate.last_admission=sentinel
        with patch('historical_training.resident_resource_guard.parse_meminfo',return_value=MEM),\
             patch('historical_training.resident_resource_guard.parse_full_psi',return_value=PSI),\
             patch('historical_training.resident_live_source.refresh',
                   side_effect=AssertionError('NO_REFRESH_FOR_HELD_PROBES')) as refresh,\
             patch.object(gate.lease,'acquire',side_effect=AssertionError('NO_PREFLIGHT_LEASE')) as lease:
            self.assertTrue(gate.check()['admit_new_worker'])
            normal=(gate.root/'trainer-admission.json').read_bytes()
            report=gate.check(forward_registration=subject)
        self.assertFalse(report['admit_new_worker'])
        self.assertIn('SOURCE_CONTINUITY_NOT_READY',report['reasons'])
        self.assertEqual(report['subject'],subject)
        self.assertFalse(report['trial_allocated'])
        self.assertFalse(report['worker_launch_authority'])
        self.assertTrue(report['actual_launch_guard_still_required'])
        self.assertEqual((gate.root/'trainer-admission.json').read_bytes(),normal)
        self.assertIs(gate.last_admission,sentinel)
        self.assertEqual(json.loads((gate.root/'trainer-forward-admission.json').read_text()),report)
        refresh.assert_not_called()
        lease.assert_not_called()

    def test_ready_forward_registration_still_requires_original_live_refresh(self):
        subject={'job_id':'job','candidate_id':'c'*64}
        self.source.update(state='READY_FOR_REVIEW_NOT_AUTHORIZATION',
                           usable=24,unusable=0,reasons=[])
        self.persist()
        proof={'schema':'synthetic-only-not-a-broker-result','broker_queries_sent':0}
        started=int(time.time()*1000);gate=Gate(self.config,'trainer')
        def refreshed(original,source,now):
            return {**source,'at_ms':now,'latest_source_quote_ms':now-500},proof
        with patch('historical_training.resident_resource_guard.parse_meminfo',return_value=MEM),\
             patch('historical_training.resident_resource_guard.parse_full_psi',return_value=PSI),\
             patch('historical_training.resident_resource_guard.time.time') as wall,\
             patch('historical_training.resident_resource_guard.time.monotonic') as tick:
            with patch('historical_training.resident_live_source.refresh',side_effect=refreshed) as refresh:
                for sample,offset in enumerate((0,30000,60000),1):
                    wall.return_value=(started+offset)/1000;tick.return_value=offset/1000
                    report=gate.check(forward_registration=subject)
                    self.assertEqual(report['registration_stability']['healthy_samples'],sample)
                    self.assertEqual(report['admit_new_worker'],sample==3)
                    self.assertEqual(report['original_live_source_evidence'],proof)
                self.assertEqual(refresh.call_count,3)
            wall.return_value=(started+90000)/1000;tick.return_value=90.
            with patch('historical_training.resident_live_source.refresh',
                       side_effect=ValueError('NO_CAUSAL_ORIGINAL_QUOTE')) as refresh:
                denied=gate.check(forward_registration=subject)
            self.assertFalse(denied['admit_new_worker'])
            self.assertIn('NO_CAUSAL_ORIGINAL_QUOTE',';'.join(denied['reasons']))
            self.assertEqual(denied['registration_stability']['healthy_samples'],0)
            self.assertIsNone(gate._registration_stability)
            refresh.assert_called_once()
            wall.return_value=(started+120000)/1000;tick.return_value=120.
            with patch('historical_training.resident_live_source.refresh',side_effect=refreshed):
                recovered=gate.check(forward_registration=subject)
            self.assertFalse(recovered['admit_new_worker'])
            self.assertEqual(recovered['registration_stability']['healthy_samples'],1)


    def test_resource_denial_never_opens_source_status_or_refreshes(self):
        self.health.unlink()
        for role, kwargs, reason in (
                ('evaluator', {}, 'HOST_AVAILABLE_MEMORY_BELOW_8GIB'),
                ('trainer', {'forward_registration': {'job_id':'job','candidate_id':'c'*64}},
                 'HOST_AVAILABLE_MEMORY_BELOW_REGISTRATION_RESERVE')):
            with self.subTest(role=role),\
                 patch('historical_training.resident_resource_guard.parse_meminfo',return_value={**MEM,'MemAvailable':GIB}),\
                 patch('historical_training.resident_resource_guard.parse_full_psi',return_value=PSI),\
                 patch('historical_training.resident_live_source.refresh',side_effect=AssertionError('NO_SOURCE_IO')) as refresh:
                report=Gate(self.config,role).check(**kwargs)
            self.assertEqual(report['reasons'],[reason])
            self.assertFalse(report['admit_new_worker'])
            self.assertTrue(report['source_refresh_skipped_for_resource_denial'])
            self.assertFalse(report['original_live_source_evidence']['fresh_original_live_source_proven'])
            refresh.assert_not_called()

    def test_recovered_resources_require_fresh_source_after_lease_again(self):
        self.source.update(state='READY_FOR_REVIEW_NOT_AUTHORIZATION',usable=24,unusable=0,reasons=[],latest_source_quote_ms=int(time.time()*1000)-100)
        self.persist()
        gate=Gate(self.config,'evaluator')
        with patch('historical_training.resident_resource_guard.parse_meminfo',return_value=MEM),\
             patch('historical_training.resident_resource_guard.parse_full_psi',return_value=PSI),\
             patch('historical_training.resident_live_source.refresh',return_value=(self.source,{'fixture':True})) as refresh,\
             patch.object(gate.lease,'acquire',return_value=True) as lease:
            self.assertTrue(gate.acquire({'plan':'fixture','slot':0}))
        self.assertEqual(refresh.call_count,2)
        lease.assert_called_once()



if __name__=='__main__':
    unittest.main()
