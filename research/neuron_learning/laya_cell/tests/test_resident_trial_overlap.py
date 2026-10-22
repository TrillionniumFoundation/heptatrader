"""Prospective queue overlap preserves original trials and completed receipts."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from laya_cell import resident_plan_admission as admission
from laya_cell.integrity import ContractError, canonical, digest, read_json, sha_file
from laya_cell.resident_queue import OVERLAP_PROFILE, OVERLAP_FIELD, OVERLAP_EFFECTIVE, inspect
from laya_cell.resident_store import get_meta, put_meta, write_once
from laya_cell.tests import test_resident_plan_admission as fixtures


class TrialOverlapTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.service, self.candidate, self.job = fixtures.RegistrationTests.fixture(self, self.root)
        self.addCleanup(self.service.db.close)
        source = patch.object(admission, 'capture_source', return_value=fixtures.fresh_proof())
        self.source = source.start(); self.addCleanup(source.stop)
        clock = patch.object(admission.time, 'time', return_value=fixtures.NOW / 1000)
        clock.start(); self.addCleanup(clock.stop)

    def enroll(self, *, job=True, created=None):
        policy = {OVERLAP_FIELD: OVERLAP_PROFILE, OVERLAP_EFFECTIVE: fixtures.NOW - 3000}
        self.service.c.update(policy)
        self.service.c['runtime_sha256']['learn/laya_cell/resident_queue.py'] = 'e' * 64
        if job:
            self.job.update(policy)
            with self.service.db:
                self.service.db.execute('UPDATE jobs SET payload=? WHERE id=?',
                    (canonical(self.job).decode(), 'job'))
                if created is not None:
                    self.service.db.execute('UPDATE jobs SET created_ms=? WHERE id=?', (created, 'job'))

    def old_trial(self, censored=3):
        old = self.service._evaluation_plan('old-job', self.candidate, self.job, 1,
                                            fixtures.NOW - 3600000)
        old['plan_id'] = digest(old)
        path = Path(self.service.c['evaluation_directory']) / 'old-job' / 'plan.json'
        write_once(path, old)
        for slot in range(censored):
            write_once(path.parent / f'outcome-{slot:03}.json',
                {'plan_id': old['plan_id'], 'candidate_id': old['candidate_id'],
                 'slot': slot, 'status': 'CENSORED', 'reason': 'NO_PREDICTION_IN_FIXED_SLOT'})
        with self.service.db: put_meta(self.service.db, 'evaluation_trial', 1)
        return path, old

    def register(self):
        return admission.register(self.service, 'job', self.candidate, self.job)

    def test_legacy_runtime_does_not_publish_null_retained_closeout_pin(self):
        name = 'learn/laya_cell/resident_retained_closeout.py'
        self.service.c['runtime_sha256'].pop(name, None)
        plan = self.service._evaluation_plan('job', self.candidate, self.job, 1, fixtures.NOW)
        self.assertNotIn(name, plan['evaluation_runtime_sha256'])

    def test_new_runtime_commits_retained_closeout_pin_when_available(self):
        name = 'learn/laya_cell/resident_retained_closeout.py'
        self.service.c['runtime_sha256'][name] = 'd' * 64
        plan = self.service._evaluation_plan('job', self.candidate, self.job, 1, fixtures.NOW)
        self.assertEqual(plan['evaluation_runtime_sha256'][name], 'd' * 64)

    def test_new_job_registers_without_rewriting_futile_original(self):
        self.enroll(); path, old = self.old_trial()
        before = {p.name: p.read_bytes() for p in path.parent.iterdir()}
        result = self.register()
        self.assertEqual(result['state'], 'PUBLISHED')
        self.assertEqual(result['plan']['trial'], 2)
        self.assertEqual(get_meta(self.service.db, 'evaluation_trial'), 2)
        self.assertEqual(result['plan']['registration_admission']['trial_queue']['bypassed_futile_plan_ids'],
                         [old['plan_id']])
        self.assertEqual({p.name: p.read_bytes() for p in path.parent.iterdir()}, before)
        self.assertFalse((path.parent / 'qualification.json').exists())
        self.assertEqual(inspect(self.service.c)['pending_original_trials'], 2)
        self.assertEqual(inspect(self.service.c)['viable_pending_evaluations'], 1)
        self.assertEqual(self.register()['plan'], result['plan'])
        self.assertEqual(get_meta(self.service.db, 'evaluation_trial'), 2)

    def test_old_candidate_is_not_retroactively_enrolled(self):
        self.enroll(job=False); self.old_trial()
        self.assertEqual(self.register()['reason'], 'PRIOR_ORIGINAL_TRIAL_PENDING')
        self.assertEqual(get_meta(self.service.db, 'evaluation_trial'), 1)

    def test_job_created_before_effective_time_keeps_serial_policy(self):
        self.enroll(created=fixtures.NOW - 4000); self.old_trial()
        self.assertEqual(self.register()['state'], 'DEFERRED')
        self.assertEqual(get_meta(self.service.db, 'evaluation_trial'), 1)

    def test_viable_original_still_blocks_new_job(self):
        self.enroll(); self.old_trial(censored=2)
        self.assertEqual(self.register()['state'], 'DEFERRED')
        self.assertEqual(get_meta(self.service.db, 'evaluation_trial'), 1)
        self.source.assert_not_called()

    def test_supervisor_reconcile_uses_same_queue_policy(self):
        self.enroll(); self.old_trial()
        result = self.service.reconcile_evaluation_queue()
        self.assertEqual(result['deferred_training_candidates'], 0)
        self.assertEqual(result['pending_original_trials'], 2)
        self.assertEqual(result['viable_pending_evaluations'], 1)
        self.assertEqual(get_meta(self.service.db, 'evaluation_trial'), 2)

    def retain_then_change_runtime(self):
        path = self.root / 'old-config.json'
        path.write_bytes(canonical(self.service.c))
        self.service.c['retained_registration_configs'] = [{'path': str(path), 'sha256': sha_file(path)}]
        self.service.c['runtime_sha256'][admission.RUNTIME_PIN[0]] = 'f' * 64
        return path

    def test_frozen_published_receipt_replays_only_bound_historical_config(self):
        first = self.register(); path = self.retain_then_change_runtime()
        self.source.side_effect = AssertionError('frozen receipt cannot reread future source')
        self.assertEqual(self.register(), first)
        self.assertEqual(get_meta(self.service.db, 'evaluation_trial'), 1)
        path.write_text('{}')
        with self.assertRaisesRegex(ContractError, 'RETAINED_REGISTRATION_CONFIG_CHANGED'):
            self.register()

    def test_old_deferred_receipt_requires_an_executable_original_runtime(self):
        self.old_trial(); self.assertEqual(self.register()['state'], 'DEFERRED')
        self.retain_then_change_runtime()
        with self.assertRaisesRegex(ContractError, 'RETAINED_REGISTRATION_PYTHON'):
            self.register()
        self.assertEqual(get_meta(self.service.db, 'evaluation_trial'), 1)


if __name__ == '__main__': unittest.main()
