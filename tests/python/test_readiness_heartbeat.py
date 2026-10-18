"""A stale heartbeat can only preserve HOLD; positive states need full bytes."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

from laya_cell.readiness_heartbeat import StaleReadinessHeartbeat
from laya_cell.integrity import ContractError

NOW = 1790000000000


class HeartbeatTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.period = self.root / 'period.json'
        self.period.write_bytes(b'{}\n')
        self.period.chmod(0o600)
        self.feed = self.root / 'feed'
        self.segment = self.feed / 'segment-0001' / 'quotes.jsonl'
        self.segment.parent.mkdir(parents=True)
        self.segment.write_bytes(b'{"kind":"FAILURE"}\n')
        self.segment.chmod(0o600)
        self.config = {'period_path':str(self.period), 'period_sha256':hashlib.sha256(self.period.read_bytes()).hexdigest(), 'feed_directory':str(self.feed)}
        self.calls = 0
        self.latest = NOW - 90000
        self.state = 'HOLD_NEW_FUSION_WINDOW'
        self.cache = StaleReadinessHeartbeat(300)

    def full(self, config, now):
        self.calls += 1
        return {'at_ms':now, 'state':self.state, 'latest_source_quote_ms':self.latest,
                'latest_quote_age_ms':now-self.latest, 'reasons':['LATEST_AUTHORITATIVE_QUOTE_NOT_FRESH'],
                'probes':[{'start_ms':now-600000, 'status':'INPUT_UNAVAILABLE'}],
                'source_prefixes':[], 'orders_sent':0, 'trading_authority':False}

    def run_at(self, now):
        return self.cache.assess(self.config, self.full, now)

    def append(self, event):
        with self.segment.open('ab') as out:
            out.write(json.dumps(event).encode()+b'\n')

    def test_stale_unchanged_hold_updates_only_evaluation_and_age(self):
        first = self.run_at(NOW)
        later = self.run_at(NOW+15000)
        self.assertEqual(self.calls, 1)
        self.assertEqual(later['state'], 'HOLD_NEW_FUSION_WINDOW')
        self.assertEqual(later['at_ms'], NOW+15000)
        self.assertEqual(later['source_asof_ms'], NOW)
        self.assertEqual(later['latest_source_quote_ms'], self.latest)
        self.assertEqual(later['latest_quote_age_ms'], 105000)
        self.assertEqual(later['probes'], first['probes'])
        self.assertEqual(later['probe_results_at_ms'], NOW)
        self.assertFalse(later['cached_result_may_grant_ready'])

    def test_failure_and_partial_tail_append_do_not_rescan_whole_history(self):
        self.run_at(NOW)
        self.append({'kind':'FAILURE','data':{'reason':'10197'}})
        self.assertEqual(self.run_at(NOW+15000)['assessment_kind'], 'STALE_HOLD_HEARTBEAT')
        with self.segment.open('ab') as out:out.write(b'{"kind":"QUO')
        partial = self.run_at(NOW+30000)
        self.assertEqual(partial['state'], 'HOLD_NEW_FUSION_WINDOW')
        self.assertTrue(partial['source_revalidation_pending'])
        self.assertTrue(self.run_at(NOW+45000)['source_revalidation_pending'])
        self.assertEqual(self.calls, 1)

    def test_new_quote_hint_forces_full_validation_before_ready(self):
        self.run_at(NOW)
        self.append({'kind':'QUOTE','data':{'observed_at_ms':NOW+10000}})
        self.latest = NOW+10000
        self.state = 'READY_FOR_REVIEW_NOT_AUTHORIZATION'
        result = self.run_at(NOW+15000)
        self.assertEqual(self.calls, 2)
        self.assertEqual(result['assessment_kind'], 'FULL_SOURCE_ASSESSMENT')
        self.run_at(NOW+30000)
        self.assertEqual(self.calls, 3)

    def test_new_quote_cannot_hide_behind_large_failure_tail(self):
        self.run_at(NOW)
        self.append({'kind':'QUOTE','data':{'observed_at_ms':NOW}})
        self.append({'kind':'FAILURE','data':{'detail':'x'*70000}})
        self.run_at(NOW+15000)
        self.assertEqual(self.calls, 2)

    def test_oversize_append_forces_complete_assessment(self):
        self.run_at(NOW)
        self.append({'kind':'FAILURE','data':{'detail':'x'*(1024*1024)}})
        self.run_at(NOW+15000)
        self.assertEqual(self.calls, 2)

    def test_revalidate_deadline_retains_bounded_full_validation(self):
        self.run_at(NOW)
        self.run_at(NOW+299999)
        self.assertEqual(self.calls, 1)
        self.run_at(NOW+300000)
        self.assertEqual(self.calls, 2)

    def test_replace_truncate_and_same_size_mtime_restored_force_full(self):
        self.run_at(NOW)
        original = self.segment.stat()
        raw = self.segment.read_bytes()
        self.segment.write_bytes(raw.replace(b'FAILURE',b'UNKNOWN'))
        os.utime(self.segment, ns=(original.st_atime_ns, original.st_mtime_ns))
        self.run_at(NOW+15000)
        self.assertEqual(self.calls, 2)
        replacement = self.segment.with_suffix('.replacement')
        replacement.write_bytes(raw)
        replacement.chmod(0o600)
        os.replace(replacement,self.segment)
        self.run_at(NOW+30000)
        self.assertEqual(self.calls, 3)
        self.segment.write_bytes(b'')
        self.run_at(NOW+45000)
        self.assertEqual(self.calls, 4)

    def test_period_config_and_clock_change_cannot_reuse(self):
        self.run_at(NOW)
        with self.assertRaisesRegex(ContractError,'CLOCK_REGRESSED'):
            self.run_at(NOW-1)
        self.run_at(NOW)
        self.period.write_bytes(b'{"new":1}\n')
        with self.assertRaisesRegex(ContractError,'RESIDENT_PERIOD_CHANGED'):
            self.run_at(NOW+15000)
        self.period.write_bytes(b'{}\n')
        changed = {**self.config,'source_epoch':'changed'}
        with self.assertRaisesRegex(ContractError,'CONFIG_CHANGED'):
            self.cache.assess(changed,self.full,NOW+30000)

    def test_failed_complete_validation_cannot_poison_later_heartbeat(self):
        self.run_at(NOW)
        self.append({'kind':'QUOTE','data':{'observed_at_ms':NOW}})
        def failed(config,now):raise ContractError('BAD_SOURCE_CHAIN')
        with self.assertRaisesRegex(ContractError,'BAD_SOURCE_CHAIN'):
            self.cache.assess(self.config,failed,NOW+15000)
        self.run_at(NOW+30000)
        self.assertEqual(self.calls,2)

    def test_append_during_full_assess_is_rechecked_next_cycle(self):
        def growing(config,now):
            result=self.full(config,now)
            self.append({'kind':'QUOTE','data':{'observed_at_ms':NOW}})
            return result
        self.cache.assess(self.config,growing,NOW)
        self.run_at(NOW+15000)
        self.assertEqual(self.calls,2)


if __name__=='__main__':unittest.main()
