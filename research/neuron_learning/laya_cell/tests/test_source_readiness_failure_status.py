"""Exercise emitted source failures through the real admission consumer."""
from contextlib import ExitStack, redirect_stdout
import io
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

from historical_training import resident_resource_guard as guard
from laya_cell import source_readiness as readiness


class SourceReadinessFailureStatusTests(unittest.TestCase):
    def run_monitor(self, assessments, *, once):
        statuses = []
        output = io.StringIO()
        config = {'period_sha256': 'a' * 64, 'period_path': '/fixture/period.json',
                  'seed_dataset': '/fixture/seed.json', 'oms_path': '/fixture/oms.json',
                  'export_directory': '/fixture/export', 'feed_directory': '/fixture/feed',
                  'runtime_directory': '/fixture/runtime'}
        argv = ['source_readiness.py', '--config', '/fixture/config.json',
                '--output', '/fixture/status.json']
        if once:
            argv += ['--once']
        else:
            argv += ['--sandbox', '--refresh-seconds', '15', '--max-cycles', '2']
        sandbox = types.ModuleType('sandbox')
        sandbox.enter = lambda _: {'fixture': True}
        with ExitStack() as stack:
            stack.enter_context(patch.object(sys, 'argv', argv))
            stack.enter_context(patch.object(readiness, 'read_json', return_value=config))
            stack.enter_context(patch.object(readiness, 'assess', side_effect=assessments))
            stack.enter_context(patch.object(readiness, 'atomic', side_effect=lambda path, value:
                statuses.append(value.copy()) if path.name == 'status.json' else None))
            stack.enter_context(patch.object(readiness.os, 'umask'))
            stack.enter_context(patch.object(readiness.signal, 'signal'))
            stack.enter_context(patch.object(Path, 'mkdir'))
            stack.enter_context(patch.object(readiness, 'next_monitor_deadline', return_value=0))
            stack.enter_context(patch.dict(sys.modules, {'sandbox': sandbox}))
            stack.enter_context(redirect_stdout(output))
            readiness.main()
        return statuses, [json.loads(line) for line in output.getvalue().splitlines()]

    def test_reader_errors_publish_a_valid_hold_without_source_freshness(self):
        for error in (OSError('archive changed during read'),
                      TimeoutError('timeout:\n非标准字段;' + 'x' * 400)):
            with self.subTest(error=type(error).__name__):
                statuses, _ = self.run_monitor([error], once=True)
                status = statuses[0]
                held = guard.preflight_source_health_hold(status)
                self.assertEqual(held['reported_source_reasons'], ['SOURCE_UNAVAILABLE'])
                self.assertFalse(held['fresh_original_live_source_proven'])
                self.assertIsNone(status['source_asof_ms'])
                self.assertIsNone(status['source_assessed_at_ms'])
                self.assertIsNone(status['latest_quote_age_at_publish_ms'])
                self.assertFalse(status['cached_result_may_grant_ready'])
                self.assertEqual(status['orders_sent'], 0)
                self.assertLessEqual(len(status['reason']), len('SOURCE_UNAVAILABLE:') + 180)
                self.assertEqual(status['period_sha256'], 'a' * 64)

    def test_failed_cadence_is_diagnosable_and_next_real_assessment_can_recover(self):
        healthy = {'schema': 'heptatrader.quote-readiness-status.v1',
                   'state': 'READY_FOR_REVIEW_NOT_AUTHORIZATION', 'reasons': [],
                   'at_ms': 123456, 'source_asof_ms': 123455, 'usable': 24,
                   'latest_source_quote_ms': 123450, 'orders_sent': 0,
                   'trading_authority': False}
        statuses, logs = self.run_monitor([OSError('archive publication changed'), healthy],
                                         once=False)
        self.assertEqual(guard.preflight_source_health_hold(statuses[0])[
            'reported_source_reasons'], ['SOURCE_UNAVAILABLE'])
        self.assertEqual(logs[0]['reasons'], ['SOURCE_UNAVAILABLE'])
        self.assertEqual(logs[0]['reason'], 'SOURCE_UNAVAILABLE:archive publication changed')
        self.assertIsNone(logs[0]['published_source_age_ms'])
        self.assertIsNone(guard.preflight_source_health_hold(statuses[1]))
        self.assertEqual(statuses[1]['source_asof_ms'], 123455)
        self.assertEqual(statuses[1]['latest_source_quote_ms'], 123450)
        self.assertNotIn('reason', logs[1])
        self.assertFalse(statuses[1]['trading_authority'])


if __name__ == '__main__':
    unittest.main()
