import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

import audit_current100k_request_timing as audit


class RequestAuditTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.folder = self.root / 'plans' / 'plan'
        self.put(self.folder / 'seen-commands' / 'command.json', {
            'request_id': 'request', 'request_started_at_ms': 1000,
            'producer_manifest_sha256': 'producer', 'command': {'command_id': 'command'}})
        self.put(self.folder / 'outcomes' / 'command' / 'outcome.json', {
            'record_id': 'record', 'opened_at_ms': 6000, 'status': 'CENSORED',
            'reason': 'NO_PREDICTION_FOR_ORIGINAL_REQUEST'})
        self.put(self.folder / 'outcomes' / 'command' / 'current.json', {
            'record_id': 'record', 'path': str(self.folder / 'outcomes' / 'command' / 'outcome.json')})
        self.plans = [{'plan_id': 'plan'}]

    def put(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))

    def inspect(self, **kwargs):
        return audit.inspect(self.root, self.plans, {'plan': {'request': {
            'request': {'started_at_ms': 1000}, 'source_written_at_ms': 1001}}}, **kwargs)

    def test_missing_prediction_does_not_invent_cause_or_write_trial(self):
        before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in self.root.rglob('*.json')}
        result = self.inspect()
        self.assertEqual(result['command_count'], 1)
        case = result['cases'][0]
        self.assertEqual(case['root_cause_status'], 'UNRECORDED_BEFORE_PREDICTOR_NO_CAUSE_INVENTED')
        self.assertEqual(case['request_to_open_ms'], 5000)
        self.assertIsNone(case['request_to_commit_ms'])
        after = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in self.root.rglob('*.json')}
        self.assertEqual(before, after)

    def test_signed_commit_time_keeps_actual_lateness(self):
        self.put(self.folder / 'predictions' / 'request' / 'prediction.json', {'computed_at_ms': 9000})
        self.put(self.folder / 'predictions' / 'request' / 'commit.json', {
            'committed_at_ms': 9004, 'role_witness': {'signed_at_ms': 9008}})
        case = self.inspect()['cases'][0]
        self.assertEqual(case['computed_to_commit_ms'], 8)
        self.assertEqual(case['commit_after_open_ms'], 3008)
        self.assertEqual(case['outcome_status'], 'CENSORED')

    def test_failure_reason_is_kept_without_becoming_a_prediction(self):
        self.put(self.folder / 'predictions' / 'request' / 'failure.json', {
            'reason': 'SOURCE_CHANGED', 'observed_at_ms': 12000})
        case = self.inspect()['cases'][0]
        self.assertEqual(case['root_cause_status'], 'RECORDED_REQUEST_FAILURE_NOT_AUTOMATIC_CAUSAL_PROOF')
        self.assertFalse(case['prediction_exists'])

    def test_late_stale_failure_does_not_claim_original_missing_cause(self):
        self.put(self.folder / 'predictions' / 'request' / 'failure.json', {
            'reason': 'REQUEST_ALREADY_OLD_NO_BACKDATING', 'observed_at_ms': 90000000})
        case = self.inspect()['cases'][0]
        self.assertEqual(case['root_cause_status'], 'POSTHOC_STALE_FAILURE_NOT_CAUSAL_PROOF')
        self.assertEqual(case['failure_observed_after_open_ms'], 89994000)

    def test_outcome_pointer_cannot_read_another_path(self):
        self.put(self.folder / 'outcomes' / 'command' / 'current.json', {
            'record_id': 'record', 'path': str(self.root / 'other.json')})
        with self.assertRaisesRegex(ValueError, 'OUTSIDE_COMMAND'):
            self.inspect()

    def test_journal_nearby_status_is_not_claimed_as_request_cause(self):
        lines = [json.dumps({'MESSAGE': json.dumps(x)}) for x in (
            {'state': 'HOLD_SHARED_RESOURCE_OR_SOURCE', 'resource_admission': {'reasons': ['BUSY']}},
            {'stage': 'RESOURCE_DENIED', 'request_id': 'request', 'resource_admission': {'reasons': ['MEMORY']}})]
        def run(argv, **kwargs):
            self.assertEqual(argv[0], 'journalctl')
            return SimpleNamespace(returncode=0, stdout='\n'.join(lines))
        result = audit.journal_near(100000, 'request', 'unit', runner=run)
        self.assertEqual(result['events'][0]['scope'], 'NEARBY_SERVICE_EVENT_NOT_CAUSAL_PROOF')
        self.assertEqual(result['events'][1]['scope'], 'EXACT_REQUEST_ID')


if __name__ == '__main__':
    unittest.main()
