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
        box=tempfile.TemporaryDirectory();self.addCleanup(box.cleanup)
        self.root=Path(box.name)
        self.original=self.root/'original.json'
        self.period=self.root/'period.json'
        self.health=self.root/'health.json'
        for path,raw in ((self.original,b'{"automatic_trading":false}\n'),(self.period,b'{"kind":"USER_RESET"}\n')):
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
    def check(self,role='evaluator'):
        with patch('historical_training.resident_resource_guard.parse_meminfo',return_value=MEM),\
             patch('historical_training.resident_resource_guard.parse_full_psi',return_value=PSI):
            return Gate(self.config,role).check()

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

if __name__=='__main__':
    unittest.main()
