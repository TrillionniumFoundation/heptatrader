import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import observe_current100k_requests as observation


class ObserverTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.trial = self.root / 'trial'; self.trial.mkdir()
        self.plan = {'plan_id': 'plan', 'candidate_id': 'candidate', 'input_start_ms': 1000, 'input_end_ms': 100000}
        self.events = []; self.observer = observation.Observer(self.trial, self.plan, self.events.append, started_ms=1000)
        self.requests = {'request': {'request': {'started_at_ms': 1100, 'position_at_request': 0},
            'source_written_at_ms': 1101, 'request_sha256': 'request-sha', 'producer_manifest_sha256': 'producer'}}

    def put(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True); path.write_text(json.dumps(value)); return path

    def test_observation_never_changes_original_trial(self):
        self.observer.source(self.requests, [], 1200)
        self.observer.source(self.requests, [], 1300)
        self.observer.results(61100, lambda rid: self.fail('No commit exists'))
        self.assertEqual([x['stage'] for x in self.events], [
            'ORIGINAL_REQUEST_OBSERVED', 'REQUEST_FRESHNESS_EXPIRED_WITHOUT_SIGNED_PREDICTION'])
        self.assertEqual(list(self.trial.iterdir()), [])

    def test_exact_denial_keeps_request_and_timestamp_not_nearby_state(self):
        self.observer.source(self.requests, [], 1200)
        path = self.put(self.root / 'denial.json', {'subject': {'plan_id': 'plan', 'requests': ['request']},
            'decision': {'at_ms': 1400, 'admit_new_worker': False, 'reasons': ['OTHER_RESEARCH_WORKER_ACTIVE']}})
        self.observer.denial(path); self.observer.denial(path)
        self.assertEqual(self.events[-1]['stage'], 'EXACT_REQUEST_ADMISSION_DENIAL')
        self.assertEqual(self.events[-1]['request_id'], 'request')
        self.assertEqual(len(self.events), 2)
        self.put(path, {'subject': {'plan_id': 'wrong', 'requests': ['request']},
            'decision': {'at_ms': 1401, 'admit_new_worker': False, 'reasons': ['MEMORY']}})
        self.observer.denial(path); self.assertEqual(len(self.events), 2)

    def test_commit_reader_is_used_and_existing_bytes_preserved(self):
        self.observer.source(self.requests, [], 1200)
        path = self.put(self.trial / 'plans/plan/predictions/request/commit.json', {'fixture': True})
        original = path.read_bytes()
        self.observer.results(1600, lambda rid: {'computed_at_ms': 1500, 'committed_at_ms': 1505})
        self.assertEqual(self.events[-1]['stage'], 'SIGNED_PREDICTION_OBSERVED')
        self.assertEqual(path.read_bytes(), original)
        self.observer.results(1700, lambda rid: self.fail('Already observed'))

    def test_new_producer_has_no_old_plan_candidate_or_prediction_lookup(self):
        plan = {**self.plan, 'producer_registration': {'manifest': '/old/manifest.json', 'sha256': 'old'}}
        observer = observation.Observer(self.trial, plan, self.events.append, started_ms=1000,
            producer_registration={'manifest': '/new/manifest.json', 'sha256': 'producer'})
        observer.source(self.requests, [], 1200)
        observer.results(1300, lambda rid: self.fail('New producer must never use old plan'))
        self.assertEqual(len(self.events), 2)
        for event in self.events:
            self.assertIsNone(event['plan_id']); self.assertIsNone(event['candidate_id'])
            self.assertEqual(event['reference_prior_plan_id'], 'plan')
            self.assertEqual(event['registration_scope_state'], 'NO_SAME_PRODUCER_PLAN_IN_OBSERVED_CONFIG')
            self.assertEqual(event['producer_manifest_sha256'], 'producer')
        self.assertEqual(list(self.trial.iterdir()), [])

    def test_explicit_producer_scope_rejects_mixed_source_identity(self):
        observer = observation.Observer(self.trial, self.plan, self.events.append, started_ms=1000,
            producer_registration={'manifest': '/new/manifest.json', 'sha256': 'different'})
        with self.assertRaisesRegex(ValueError, 'REQUEST_PRODUCER_CHANGED'):
            observer.source(self.requests, [], 1200)

    def test_directory_events_report_new_files_without_tree_scan(self):
        directory = self.root / 'denials'; directory.mkdir()
        events = observation.DirectoryEvents([directory]); self.addCleanup(events.close)
        path = self.put(directory / 'one.json', {'value': 1})
        changed, overflow = events.wait(.1)
        self.assertIn(path, changed); self.assertFalse(overflow)

    def test_diagnostic_log_batches_sync_and_preserves_json_lines(self):
        output = self.root / 'diagnostics'; output.mkdir()
        with patch.object(observation.os, 'fdatasync') as sync:
            log = observation.Log(output)
            log.emit({'event': 1}); log.emit({'event': 2})
            sync.assert_not_called()
            log.close(); sync.assert_called_once()
        self.assertEqual([json.loads(x) for x in (output / 'events.jsonl').read_text().splitlines()], [{'event': 1}, {'event': 2}])


if __name__ == '__main__':
    unittest.main()
