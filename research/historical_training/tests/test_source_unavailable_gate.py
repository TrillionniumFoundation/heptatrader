"""Complete Gate.check boundaries for a failed source assessment.

Private status/config/admission files are real. Host telemetry is synthetic;
the original parsers, resource policy, period checks and Gate.check are used.
"""
from contextlib import ExitStack
import copy
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from historical_training import resident_resource_guard as guard
from historical_training.common import sha


class UnavailableSourceGateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='source-unavailable-gate-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.original = self.root / 'original.json'
        self.period = self.root / 'period.json'
        self.health = self.root / 'source-status.json'
        self.original.write_text(json.dumps({'automatic_trading': False,
            'runtime_sha256': {'learn/laya_cell/resident_plan_admission.py': 'b' * 64}}))
        self.period.write_text('{"kind":"USER_RESET"}\n')
        self.now = int(time.time() * 1000)
        self.source = {'schema': 'heptatrader.quote-readiness-status.v1',
            'period_sha256': sha(self.period), 'at_ms': self.now, 'source_asof_ms': None,
            'state': 'HOLD_NEW_FUSION_WINDOW', 'reasons': ['SOURCE_UNAVAILABLE'],
            'reason': 'SOURCE_UNAVAILABLE:archive changed during read',
            'cached_result_may_grant_ready': False, 'trading_authority': False, 'orders_sent': 0}
        self.config = {'original_config': str(self.original), 'original_config_sha256': sha(self.original),
            'period_path': str(self.period), 'period_sha256': sha(self.period),
            'source_health_path': str(self.health), 'manual_hold_path': str(self.root / 'manual-hold.json'),
            'state_root': str(self.root / 'admission')}
        self.memory = {'MemTotal': 24 * guard.GIB, 'MemAvailable': 12 * guard.GIB,
                       'SwapTotal': 8 * guard.GIB, 'SwapFree': 8 * guard.GIB}
        self.psi = {'avg10': 0.0, 'avg60': 0.0}
        original_read = Path.read_text
        def telemetry(path, *args, **kwargs):
            if str(path) == '/proc/meminfo':
                return ''.join(f'{key}: {value // 1024} kB\n' for key, value in self.memory.items())
            if str(path) == '/proc/pressure/memory':
                return f'full avg10={self.psi["avg10"]} avg60={self.psi["avg60"]} avg300=0 total=0\n'
            return original_read(path, *args, **kwargs)
        stack = ExitStack(); self.addCleanup(stack.close)
        stack.enter_context(patch.object(Path, 'read_text', new=telemetry))
        stack.enter_context(patch.object(guard, 'collect_resource_attribution',
            return_value={'diagnostic_only': True, 'partial': True}))
        self.refresh = stack.enter_context(patch('historical_training.resident_live_source.refresh',
            side_effect=AssertionError('UNAVAILABLE_OR_INVALID_SOURCE_MUST_NOT_BE_REFRESHED')))
        self.read = stack.enter_context(patch.object(guard, 'read', wraps=guard.read))

    def check(self, *, role='evaluator', **kwargs):
        self.health.write_text(json.dumps(self.source))
        return guard.Gate(self.config, role).check(**kwargs)

    def assert_no_source_proof(self, report):
        self.assertFalse(report['admit_new_worker'])
        self.refresh.assert_not_called()
        self.assertFalse(report['original_live_source_evidence']['fresh_original_live_source_proven'])
        self.assertEqual(report['original_live_source_evidence']['broker_queries_sent'], 0)

    def test_source_unavailable_reaches_negative_diagnostic_without_prefix_proof(self):
        report = self.check()
        self.assert_no_source_proof(report)
        self.assertIn('SOURCE_QUALITY_HOLD_NO_REFRESH_AUTHORITY', report['reasons'])
        self.assertNotIn('CONTINUITY_SOURCE_PREFIXES_MISSING', ';'.join(report['reasons']))
        diagnostic = report['cached_probe_diagnostic']
        self.assertEqual(diagnostic['reported_source_reasons'], ['SOURCE_UNAVAILABLE'])
        self.assertFalse(diagnostic['fresh_original_live_source_proven'])
        self.assertIsNone(diagnostic['cached_probe_usable'])
        self.assertEqual(json.loads((self.root / 'admission/evaluator-admission.json').read_text()), report)

    def test_ready_missing_or_changed_prefix_digest_keeps_original_rejection(self):
        self.source.update(state='READY_FOR_REVIEW_NOT_AUTHORIZATION', reasons=[],
                           latest_source_quote_ms=self.now - 1, usable=24, unusable=0,
                           rule=copy.deepcopy(guard.SOURCE_RULE))
        cases = ({}, {'source_prefixes': []}, {'source_prefixes': [{'path': 'fixture'}],
                                              'source_prefix_digest': 'incorrect'})
        for fields in cases:
            with self.subTest(fields=fields):
                self.source.update(fields)
                report = self.check()
                self.assertFalse(report['admit_new_worker'])
                expected = 'CONTINUITY_PREFIX_DIGEST_CHANGED' if fields.get('source_prefixes') else 'CONTINUITY_SOURCE_PREFIXES_MISSING'
                self.assertIn(expected, ';'.join(report['reasons']))
                self.assertNotIn('cached_probe_diagnostic', report)
        self.refresh.assert_not_called()

    def test_malformed_hold_reason_still_refuses_without_claimed_diagnostic(self):
        for reasons in (None, 'SOURCE_UNAVAILABLE', [123], ['bad\nREASON'], ['A' * 91], ['非规范']):
            with self.subTest(reasons=reasons):
                self.source['reasons'] = reasons
                report = self.check()
                self.assertFalse(report['admit_new_worker'])
                self.assertIn('SOURCE_HEALTH_HOLD_REASON_FORMAT', ';'.join(report['reasons']))
                self.assertNotIn('cached_probe_diagnostic', report)
        self.refresh.assert_not_called()

    def test_changed_period_or_original_config_is_refused_before_source_status(self):
        for path, reason in ((self.period, 'ORIGINAL_PERIOD_CHANGED'),
                             (self.original, 'ORIGINAL_CONFIG_CHANGED')):
            with self.subTest(path=path):
                original = path.read_bytes(); path.write_bytes(b'{}\n')
                self.read.reset_mock()
                report = self.check()
                self.assertFalse(report['admit_new_worker'])
                self.assertIn(reason, ';'.join(report['reasons']))
                self.assertFalse(any(str(call.args[0]) == str(self.health) for call in self.read.call_args_list))
                path.write_bytes(original)
        self.refresh.assert_not_called()

    def test_source_period_and_rule_are_still_rejected_for_typed_held_status(self):
        self.source.update(latest_source_quote_ms=self.now - 6000, usable=0, unusable=24,
                           rule=copy.deepcopy(guard.SOURCE_RULE))
        for field, bad, reason in (('period_sha256', 'b' * 64, 'SOURCE_PERIOD_OR_SCHEMA_UNBOUND'),
                                  ('schema', 'not-a-source-schema', 'SOURCE_PERIOD_OR_SCHEMA_UNBOUND'),
                                  ('rule', {}, 'SOURCE_RULE_CHANGED')):
            with self.subTest(field=field):
                original = self.source[field]; self.source[field] = bad
                report = self.check()
                self.assert_no_source_proof(report)
                self.assertIn(reason, report['reasons'])
                self.source[field] = original

    def test_memory_pressure_and_manual_hold_keep_precedence_over_source_read(self):
        hold = Path(self.config['manual_hold_path'])
        for kind, reason in (('memory', 'HOST_AVAILABLE_MEMORY_BELOW_8GIB'),
                             ('pressure', 'HOST_FULL_MEMORY_PSI_PRESSURE'),
                             ('manual', 'OPERATOR_RESOURCE_HOLD')):
            with self.subTest(kind=kind):
                self.memory['MemAvailable'] = guard.GIB if kind == 'memory' else 12 * guard.GIB
                self.psi['avg60'] = 0.2 if kind == 'pressure' else 0.0
                if kind == 'manual': hold.write_text('{}')
                self.read.reset_mock()
                report = self.check()
                self.assert_no_source_proof(report)
                self.assertIn(reason, report['reasons'])
                self.assertTrue(report['source_refresh_skipped_for_resource_denial'])
                self.assertFalse(any(str(call.args[0]) == str(self.health) for call in self.read.call_args_list))
                if hold.exists(): hold.unlink()

    def test_forward_registration_hold_cannot_gain_worker_or_trial_authority(self):
        self.health.write_text(json.dumps(self.source))
        gate = guard.Gate(self.config, 'trainer')
        ordinary = gate.check()
        self.assertTrue(ordinary['admit_new_worker'])
        old = (gate.root / 'trainer-admission.json').read_bytes()
        gate._registration_stability = {'previous': 'healthy'}
        subject = {'job_id': 'fixture-job', 'candidate_id': 'c' * 64}
        with patch.object(gate.lease, 'acquire', side_effect=AssertionError('NO_REGISTRATION_LEASE')):
            report = gate.check(forward_registration=subject)
        self.assert_no_source_proof(report)
        self.assertEqual(report['cached_probe_diagnostic']['reported_source_reasons'], ['SOURCE_UNAVAILABLE'])
        self.assertFalse(report['trial_allocated'])
        self.assertFalse(report['worker_launched'])
        self.assertFalse(report['worker_launch_authority'])
        self.assertFalse(report['registration_stability']['ready'])
        self.assertIsNone(gate._registration_stability)
        self.assertEqual((gate.root / 'trainer-admission.json').read_bytes(), old)


if __name__ == '__main__':
    unittest.main()
