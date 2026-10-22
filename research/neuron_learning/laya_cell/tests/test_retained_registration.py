"""Real CPU subprocesses preserve an old registration policy and one ledger."""
import copy
import json
from pathlib import Path
import shutil
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import laya_cell
from laya_cell import resident, resident_plan_admission as admission
from laya_cell.integrity import ContractError, canonical, digest, read_json, sha_file
from laya_cell.resident_queue import OVERLAP_FIELD, OVERLAP_PROFILE, OVERLAP_EFFECTIVE
from laya_cell.resident_store import admit_job, complete_job, get_meta
from laya_cell.tests import test_resident_plan_admission as registration_fixture
from laya_cell.tests import test_resident_trial_overlap as overlap_fixture


class RetainedRegistrationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.now = registration_fixture.NOW
        clock = patch.object(resident.time, 'time', return_value=self.now / 1000)
        clock.start(); self.addCleanup(clock.stop)
        self.service, self.candidate, self.job = registration_fixture.RegistrationTests.fixture(
            self, self.root, self.now)
        self.addCleanup(self.service.db.close)
        runtime = self.root / 'old-runtime'
        source = Path(laya_cell.__file__).parent
        package = runtime / 'learn/laya_cell'
        shutil.copytree(source, package, ignore=shutil.ignore_patterns('__pycache__', 'tests'))
        shutil.copy2(source.parent / 'data.py', runtime / 'learn/data.py')
        # Deliberately distinguish the pinned code from the importing process.
        original = package / 'resident_plan_admission.py'
        original.write_text(original.read_text().replace(
            'record = {**identity, "state": state,',
            'record = {**identity, "original_runtime_executed": True, "state": state,').replace(
            'time.time()', f'({self.now} / 1000)'))
        scheduler = package / 'resident.py'
        scheduler.write_text(scheduler.read_text().replace('time.time()', f'({self.now} / 1000)'))
        self.original_source = original
        period = self.root / 'period.json'; period.write_text('{"execution_domain":"PAPER"}')
        native = self.root / 'native'; native.write_bytes(b'fixture native; never executed')
        self.service.c.update(schema='heptatrader.laya-resident-config.v1',
            automatic_trading=False, python=sys.executable,
            training_cadence_ms=resident.MIN_CADENCE_MS,
            period_path=str(period), period_sha256=sha_file(period),
            native_binary=str(native), native_binary_sha256=sha_file(native),
            runtime_directory=str(runtime), state_root=str(self.root),
            state_max_bytes=1024 ** 3, objective={'schema':'heptatrader.laya-resident-objective.v1'},
            runtime_sha256={str(p.relative_to(runtime)):sha_file(p) for p in runtime.rglob('*.py')})
        self.old_plan_path, self.old_plan = overlap_fixture.TrialOverlapTests.old_trial(self)
        self.assertEqual(admission.register(self.service, 'job', self.candidate, self.job)['state'], 'DEFERRED')
        self.old_record = get_meta(self.service.db, admission.KEY_PREFIX + 'job')
        self.config_path = self.root / 'old.json'
        self.config_path.write_bytes(canonical(self.service.c))
        self.service.c = copy.deepcopy(self.service.c)
        self.service.c['retained_registration_configs'] = [
            {'path':str(self.config_path), 'sha256':sha_file(self.config_path)}]
        self.service.c['runtime_sha256'][admission.RUNTIME_PIN[0]] = 'f' * 64
        self.policy = {OVERLAP_FIELD:OVERLAP_PROFILE, OVERLAP_EFFECTIVE:self.now - 3000}
        self.service.c.update(self.policy)
        self.history = {p:p.read_bytes() for p in self.old_plan_path.parent.iterdir()}
        self.rows_before = [tuple(r) for r in self.service.db.execute('SELECT * FROM consumed_rows')]

    def record(self, job_id='job'):
        return get_meta(self.service.db, admission.KEY_PREFIX + job_id)

    def assert_original_history(self):
        self.assertEqual({p:p.read_bytes() for p in self.old_plan_path.parent.iterdir()}, self.history)
        self.assertEqual([tuple(r) for r in self.service.db.execute('SELECT * FROM consumed_rows')], self.rows_before)

    def add_future(self):
        job = copy.deepcopy(self.job)
        job.update(self.policy)
        output = self.root / 'future'; output.mkdir()
        shutil.copy2(Path(self.job['output_directory']) / 'adapter.safetensors', output)
        job['output_directory'] = str(output)
        candidate = copy.deepcopy(self.candidate)
        candidate.update(data_fingerprint='future-fingerprint', created_at_ms=self.now)
        candidate.pop('candidate_id')
        candidate['candidate_id'] = digest(candidate)
        (output / 'candidate.json').write_bytes(canonical(candidate))
        admit_job(self.service.db, 'future', 'future-fingerprint', self.now, job, ['future-row'])
        complete_job(self.service.db, 'future', candidate)
        self.rows_before = [tuple(r) for r in self.service.db.execute('SELECT * FROM consumed_rows')]
        return candidate, job

    def fresh_source(self):
        proof = registration_fixture.fresh_proof(int(time.time() * 1000))
        proof['period_sha256'] = self.service.c['period_sha256']
        return proof

    def test_original_serial_futile_wait_preserves_receipt_and_no_new_policy_decides(self):
        status = Path(self.service.c['evaluation_directory']) / 'job/registration-status.json'
        before = status.stat()
        raw = status.read_bytes()
        changes = self.service.db.total_changes
        with patch.object(admission, 'capture_source', side_effect=AssertionError('new code cannot decide old job')):
            result = admission.register(self.service, 'job', self.candidate, self.job)
        self.assertEqual(result, self.old_record)
        self.assertEqual(self.service.db.total_changes, changes)
        self.assertEqual(status.read_bytes(), raw)
        self.assertEqual((status.stat().st_ino, status.stat().st_mtime_ns),
                         (before.st_ino, before.st_mtime_ns))
        self.assertEqual(result['state'], 'DEFERRED')
        self.assertEqual(result['reason'], 'PRIOR_ORIGINAL_TRIAL_PENDING')
        self.assertEqual(result['policy_runtime_sha256'], self.old_record['policy_runtime_sha256'])
        self.assertEqual(get_meta(self.service.db, 'evaluation_trial'), 1)
        self.assertFalse((Path(self.service.c['evaluation_directory']) / 'job/plan.json').exists())
        self.assert_original_history()

    def test_original_calendar_can_terminate_expired_unallocated_candidate(self):
        candidate = copy.deepcopy(self.candidate)
        candidate['window']['validation_end_ms'] = self.now - 1
        candidate.pop('candidate_id'); candidate['candidate_id'] = digest(candidate)
        path = Path(self.job['output_directory']) / 'candidate.json'
        path.write_bytes(canonical(candidate))
        with self.service.db:
            self.service.db.execute("UPDATE jobs SET candidate_id=? WHERE id='job'", (candidate['candidate_id'],))
            self.service.db.execute('DELETE FROM meta WHERE key=?', (admission.KEY_PREFIX + 'job',))
        # Bind a new synthetic DEFERRED fixture to the expired candidate under
        # its original config, then let the real old calendar close it.
        old = read_json(self.config_path)
        current = self.service.c; self.service.c = old
        try: admission.register(self.service, 'job', candidate, self.job)
        finally: self.service.c = current
        before = self.record()
        qualification = self.old_plan_path.parent / 'qualification.json'
        qualification.write_bytes(canonical({'state':'REJECTED', 'plan_id':self.old_plan['plan_id'],
                                             'candidate_id':self.old_plan['candidate_id']}))
        self.history[qualification] = qualification.read_bytes()
        queue = self.service.reconcile_evaluation_queue()
        self.assertEqual(queue['pending_original_trials'], 0)
        self.assertEqual(queue['deferred_training_candidates'], 0)
        result = self.record()
        self.assertEqual(result['state'], 'NO_FORWARD_TRIAL')
        self.assertTrue(result['original_runtime_executed'])
        self.assertEqual(result['policy_runtime_sha256'], before['policy_runtime_sha256'])
        self.assertEqual(get_meta(self.service.db, 'evaluation_trial'), 1)
        receipt = Path(self.service.c['evaluation_directory']) / 'job/no-forward-trial.json'
        self.assertEqual(read_json(receipt), result)
        self.assert_original_history()

    def test_modified_original_runtime_fails_before_any_ledger_change(self):
        self.original_source.write_text(self.original_source.read_text() + '\n# changed after freeze\n')
        with self.assertRaisesRegex(ContractError, 'RUNTIME_CHANGED'):
            admission.register(self.service, 'job', self.candidate, self.job)
        self.assertEqual(self.record(), self.old_record)
        self.assertEqual(get_meta(self.service.db, 'evaluation_trial'), 1)
        self.assert_original_history()

    def test_changed_config_and_scope_are_not_adopted(self):
        old = read_json(self.config_path)
        old['control_directory'] = str(self.root / 'other-ledger')
        self.config_path.write_bytes(canonical(old))
        with self.assertRaisesRegex(ContractError, 'CONFIG_CHANGED'):
            admission.register(self.service, 'job', self.candidate, self.job)
        self.service.c['retained_registration_configs'][0]['sha256'] = sha_file(self.config_path)
        with self.assertRaisesRegex(ContractError, 'SCOPE_CHANGED'):
            admission.register(self.service, 'job', self.candidate, self.job)
        self.assertEqual(self.record(), self.old_record)
        self.assert_original_history()

    def test_existing_write_transaction_never_launches_original_process(self):
        self.service.db.execute('BEGIN IMMEDIATE')
        try:
            with self.assertRaisesRegex(ContractError, 'TRANSACTION_SCOPE'):
                admission.register(self.service, 'job', self.candidate, self.job)
        finally: self.service.db.rollback()
        self.assertEqual(self.record(), self.old_record)

    def test_original_positive_registration_cannot_bypass_operational_resource_guard(self):
        qualification = self.old_plan_path.parent / 'qualification.json'
        qualification.write_bytes(canonical({'state':'REJECTED', 'plan_id':self.old_plan['plan_id'],
                                             'candidate_id':self.old_plan['candidate_id']}))
        self.history[qualification] = qualification.read_bytes()
        with patch.object(admission, 'capture_source', side_effect=AssertionError('bare registration forbidden')):
            result = admission.register(self.service, 'job', self.candidate, self.job)
        self.assertEqual(result, self.old_record)
        self.assertEqual(self.service._retained_registration_wait_reasons,
                         {'job':'ORIGINAL_FORWARD_RESOURCE_GUARD_REQUIRED'})
        self.assertEqual(get_meta(self.service.db, 'evaluation_trial'), 1)
        self.assert_original_history()

    def test_old_wait_stays_visible_without_occupying_new_opted_in_slot(self):
        result = self.service.reconcile_evaluation_queue()
        self.assertEqual(result['deferred_training_candidates'], 1)
        self.assertEqual(result['retained_deferred_training_candidates'], 1)
        self.assertEqual(result['blocking_deferred_training_candidates'], 0)
        self.assertEqual(result['forward_registration_blocking_trials'], 1)
        self.assertEqual(self.record(), self.old_record)
        self.assertEqual(get_meta(self.service.db, 'evaluation_trial'), 1)
        self.assert_original_history()
        with (patch.object(resident, 'config_check'),
              patch.object(resident, 'inspect_storage', return_value={'admission_ready':True}),
              patch.object(self.service, 'sync_rewards'),
              patch.object(self.service, 'reconcile_evaluation_queue', return_value=result),
              patch.object(self.service, '_training_wait_preflight', return_value={'next':'real training gates'}) as gates):
            self.assertEqual(self.service.queue_preflight(), {'next':'real training gates'})
            gates.assert_called_once()

    def test_new_job_registers_behind_original_serial_wait_without_changing_it(self):
        self.add_future()
        with patch.object(admission, 'capture_source', side_effect=lambda *_:self.fresh_source()):
            result = self.service.reconcile_evaluation_queue()
        self.assertEqual(self.record('future')['state'], 'PUBLISHED')
        self.assertEqual(self.record('future')['plan']['trial'], 2)
        self.assertEqual(self.record(), self.old_record)
        self.assertEqual(result['deferred_training_candidates'], 1)
        self.assertEqual(result['blocking_deferred_training_candidates'], 0)
        self.assertEqual(result['viable_pending_evaluations'], 1)
        self.assert_original_history()

    def test_retained_wait_only_releases_slot_after_explicit_prospective_enrollment(self):
        for effective in (None, self.now + 1):
            with self.subTest(effective=effective):
                if effective is None:
                    self.service.c.pop(OVERLAP_FIELD, None)
                else:
                    self.service.c[OVERLAP_FIELD] = OVERLAP_PROFILE
                    self.service.c[OVERLAP_EFFECTIVE] = effective
                result = self.service.reconcile_evaluation_queue()
                self.assertEqual(result['deferred_training_candidates'], 1)
                self.assertEqual(result['blocking_deferred_training_candidates'], 1)
                self.assertEqual(self.record(), self.old_record)

    def test_new_deferred_candidate_still_occupies_the_one_future_slot(self):
        self.add_future()
        with patch.object(admission, 'capture_source', side_effect=ValueError('original EUR source unavailable')):
            result = self.service.reconcile_evaluation_queue()
        self.assertEqual(self.record('future')['state'], 'DEFERRED')
        self.assertEqual(result['deferred_training_candidates'], 2)
        self.assertEqual(result['blocking_deferred_training_candidates'], 1)
        self.assertEqual(result['forward_registration_blocking_trials'], 0)
        self.assertEqual(get_meta(self.service.db, 'evaluation_trial'), 1)
        self.assertEqual(self.record(), self.old_record)
        self.assert_original_history()
        with (patch.object(resident, 'config_check'),
              patch.object(resident, 'inspect_storage', return_value={'admission_ready':True}),
              patch.object(self.service, 'sync_rewards'),
              patch.object(self.service, 'reconcile_evaluation_queue', return_value=result),
              patch.object(self.service, 'write_status', side_effect=lambda **kw:kw),
              patch.object(self.service, '_training_wait_preflight', side_effect=AssertionError('future slot is occupied'))):
            self.assertEqual(self.service.queue_preflight()['state'], 'CANDIDATE_READY_AWAITING_FORWARD_ADMISSION')


if __name__ == '__main__': unittest.main()
