"""CPU-only registration crash/recovery and original-quote receipt tests."""
from concurrent.futures import ThreadPoolExecutor
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from laya_cell import resident_plan_admission as admission
from laya_cell.integrity import ContractError, canonical, digest, read_json
from laya_cell.resident import Supervisor
from laya_cell.resident_store import database, admit_job, complete_job, get_meta, put_meta, write_once
from laya_cell.shadow_source import SCHEMA as QUOTE_SCHEMA, read_source

NOW = int(datetime(2026, 10, 12, 10, tzinfo=timezone.utc).timestamp() * 1000)
SATURDAY = int(datetime(2026, 10, 17, 22, tzinfo=timezone.utc).timestamp() * 1000)


def fresh_proof(now=NOW, **changes):
    quote = {"instrument": "EUR.USD", "source": "IB", "authoritative": True,
             "stale": False, "bid": "1.1000", "ask": "1.1001",
             "observed_at_ms": now - 100, "stale_after_ms": now + 4900,
             "subscription_id": "synthetic-paper-epoch", "subscription_state": "active"}
    quote.update(changes)
    return {"schema": "heptatrader.forward-original-source-admission.v1",
            "period_sha256": "a" * 64, "instrument": "EUR.USD", "source_ttl_ms": 5000,
            "quote_known_at_ms": quote["observed_at_ms"] + 50, "quote": quote}


class RegistrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.service, self.candidate, self.job = self.fixture(self.root)
        self.addCleanup(self.service.db.close)
        self.queue = patch.object(admission, "inspect_queue",
                                  return_value={"pending_original_trials": 0})
        self.queue.start()
        self.addCleanup(self.queue.stop)
        self.source = patch.object(admission, "capture_source", return_value=fresh_proof())
        self.capture = self.source.start()
        self.addCleanup(self.source.stop)

    def fixture(self, root, now=NOW):
        root.mkdir(parents=True, exist_ok=True)
        service = object.__new__(Supervisor)
        service.root = root / "control"
        service.root.mkdir()
        service.db = database(service.root / "scheduler.sqlite")
        service.c = {"evaluation_directory": str(root / "evaluation"),
                     "control_directory": str(service.root),
                     "feed_directory": str(root / "feed"),
                     "period_sha256": "a" * 64,
                     "future_plan_admission_profile": admission.PROFILE,
                     "future_plan_admission_effective_at_ms": now - 10000,
                     "runtime_sha256": {k: "b" * 64 for k in admission.RUNTIME_PIN}}
        Path(service.c["evaluation_directory"]).mkdir()
        output = root / "candidate"
        output.mkdir()
        adapter = b"synthetic test adapter identity; not model data"
        (output / "adapter.safetensors").write_bytes(adapter)
        job = {"output_directory": str(output), "parent_directory": str(root / "parent"),
               "parent_id": "c" * 64, "dataset_sha256": "f" * 64,
               "native_binary_sha256": "9" * 64}
        candidate = {
            "created_at_ms": now - 1000, "trained_until_ms": now - 2000,
            "window": {"training_end_ms": now - 2000,
                       "validation_start_ms": now - 1999,
                       "validation_end_ms": now - 2000 + 86400000},
            "parent_id": job["parent_id"], "dataset_id": "d" * 64,
            "adapter_sha256": hashlib.sha256(adapter).hexdigest(),
            "data_fingerprint": "fingerprint",
            "dataset_file_sha256": job["dataset_sha256"],
            "native_binary_sha256": job["native_binary_sha256"],
            "plasticity": {"proposal_digest": "e" * 64},
        }
        candidate["candidate_id"] = digest(candidate)
        write_once(output / "candidate.json", candidate)
        admit_job(service.db, "job", "fingerprint", now - 2000, job, ["row"])
        complete_job(service.db, "job", candidate)
        return service, candidate, job

    def register(self, service=None, candidate=None, job=None, clock=None):
        return admission.register(service or self.service, "job",
                                  candidate or self.candidate, job or self.job,
                                  clock=clock or (lambda: NOW))

    def record(self):
        return get_meta(self.service.db, admission.KEY_PREFIX + "job")

    def plan_path(self):
        return Path(self.service.c["evaluation_directory"]) / "job" / "plan.json"

    def test_ready_commits_one_trial_and_original_rule(self):
        result = self.register()
        self.assertEqual(result["state"], "PUBLISHED")
        plan = read_json(self.plan_path())
        self.assertEqual(get_meta(self.service.db, "evaluation_trial"), 1)
        expected = self.service._evaluation_plan("job", self.candidate, self.job, 1, NOW)
        for field in ("start_ms", "outcome_end_ms", "rule"):
            self.assertEqual(plan[field], expected[field])
        self.assertFalse(plan["registration_admission"]["future_data_availability_proven"])
        self.assertFalse(plan["rule"]["trading_cutover_allowed"])
        self.assertEqual(self.service.db.execute("SELECT status FROM jobs").fetchone()[0], "COMPLETED")

    def test_fresh_looking_saturday_source_cannot_allocate(self):
        service, candidate, job = self.fixture(self.root / "weekend", SATURDAY)
        self.addCleanup(service.db.close)
        result = self.register(service, candidate, job, clock=lambda: SATURDAY)
        self.assertEqual(result["state"], "NO_FORWARD_TRIAL")
        self.capture.assert_not_called()
        self.assertEqual(get_meta(service.db, "evaluation_trial", 0), 0)
        rows = service.db.execute("SELECT * FROM jobs").fetchall()
        waiting, report = admission.waiting_candidates(service, rows)
        self.assertEqual(waiting, [])
        self.assertEqual(report["completed_candidates_without_forward_trial"], 1)
        self.assertEqual(rows[0]["status"], "COMPLETED")

    def test_temporary_source_loss_defers_without_consuming_alpha(self):
        self.capture.side_effect = ValueError("ORIGINAL_EUR_STALE")
        first = self.register()
        second = self.register(clock=lambda: NOW + 1000)
        self.assertEqual(first["state"], "DEFERRED")
        self.assertEqual(second["reason"], "FORWARD_ORIGINAL_SOURCE_UNREADY")
        self.assertEqual(get_meta(self.service.db, "evaluation_trial", 0), 0)
        self.assertFalse(self.plan_path().exists())

    def test_quote_expiry_is_rechecked_after_source_read(self):
        ticks = iter((NOW, NOW + 6000))
        result = self.register(clock=lambda: next(ticks))
        self.assertEqual(result["state"], "DEFERRED")
        self.assertEqual(get_meta(self.service.db, "evaluation_trial", 0), 0)
        self.assertFalse(self.plan_path().exists())

    def test_clock_regression_never_allocates(self):
        ticks = iter((NOW, NOW - 1))
        with self.assertRaisesRegex(ContractError, "CLOCK_REGRESSION"):
            self.register(clock=lambda: next(ticks))
        self.assertIsNone(self.record())
        self.assertEqual(get_meta(self.service.db, "evaluation_trial", 0), 0)

    def test_recovery_after_reservation_replays_original_time_without_source(self):
        with patch.object(admission, "_publish", side_effect=OSError("synthetic crash")):
            with self.assertRaises(OSError):
                self.register()
        reserved = self.record()
        self.assertEqual(reserved["state"], "RESERVED")
        self.assertFalse(self.plan_path().exists())
        self.capture.side_effect = AssertionError("must not recheck frozen trial")
        result = self.register(clock=lambda: SATURDAY)
        self.assertEqual(result["state"], "PUBLISHED")
        self.assertEqual(read_json(self.plan_path()), reserved["plan"])
        self.assertEqual(get_meta(self.service.db, "evaluation_trial"), 1)

    def test_existing_plan_before_published_metadata_finishes_original_handoff(self):
        with patch.object(self.service, "_write_evaluation_handoff",
                          side_effect=OSError("synthetic handoff crash")):
            with self.assertRaises(OSError):
                self.register()
        original = self.plan_path().read_bytes()
        self.assertEqual(self.record()["state"], "RESERVED")
        self.capture.side_effect = AssertionError("existing plan must win")
        actual_register = admission.register
        with patch.object(admission, "register",
                          side_effect=lambda *a: actual_register(*a, clock=lambda: SATURDAY)):
            self.service.register_evaluation("job", self.candidate, self.job)
        self.assertEqual(self.record()["state"], "PUBLISHED")
        self.assertEqual(self.plan_path().read_bytes(), original)
        self.assertEqual(get_meta(self.service.db, "evaluation_trial"), 1)

    def test_actual_queue_reconcile_finishes_existing_plan_reservation(self):
        with patch.object(self.service, "_write_evaluation_handoff",
                          side_effect=OSError("synthetic handoff crash")):
            with self.assertRaises(OSError):
                self.register()
        original = self.plan_path().read_bytes()
        self.assertEqual(self.record()["state"], "RESERVED")
        self.capture.side_effect = AssertionError("reserved trial must not read new source")
        with patch("laya_cell.resident.inspect_evaluation_queue",
                   return_value={"pending_original_trials": 1}):
            queue = self.service.reconcile_evaluation_queue()
        self.assertEqual(queue["deferred_training_candidates"], 0)
        self.assertEqual(self.record()["state"], "PUBLISHED")
        self.assertEqual(self.plan_path().read_bytes(), original)
        self.assertEqual(get_meta(self.service.db, "evaluation_trial"), 1)
        handoff = self.service.root / "owner-outbox" / (self.candidate["candidate_id"] + ".json")
        self.assertEqual(read_json(handoff)["candidate_id"], self.candidate["candidate_id"])

    def test_publication_cas_rejects_changed_reservation_and_rolls_back(self):
        with patch.object(admission, "_publish", side_effect=OSError("synthetic crash")):
            with self.assertRaises(OSError):
                self.register()
        original = self.record()
        changed = copy.deepcopy(original)
        changed["checked_at_ms"] += 1
        def competing_handoff(*args, **kwargs):
            with self.service.db:
                put_meta(self.service.db, admission.KEY_PREFIX + "job", changed)
        with patch.object(self.service, "_write_evaluation_handoff",
                          side_effect=competing_handoff):
            with self.assertRaisesRegex(ContractError, "RESERVATION_CHANGED"):
                admission._publish(self.service, admission.KEY_PREFIX + "job", original,
                                   self.candidate, self.job, self.plan_path().parent)
        self.assertFalse(self.service.db.in_transaction)
        self.assertEqual(self.record(), changed)
        self.assertEqual(get_meta(self.service.db, "evaluation_trial"), 1)

    def test_legacy_existing_plan_is_unchanged_during_closed_session(self):
        plan = self.service._evaluation_plan("job", self.candidate, self.job, 1, NOW)
        plan["plan_id"] = digest(plan)
        admission.publish_immutable(self.plan_path(), plan)
        with self.service.db:
            put_meta(self.service.db, "evaluation_trial", 1)
        before = self.plan_path().read_bytes()
        self.capture.side_effect = AssertionError("no new admission")
        result = self.register(clock=lambda: SATURDAY)
        self.assertEqual(result["state"], "EXISTING_IMMUTABLE_PLAN")
        self.assertIsNone(self.record())
        self.assertEqual(self.plan_path().read_bytes(), before)

    def test_unmatched_old_counter_cannot_be_called_zero_alpha(self):
        with self.service.db:
            put_meta(self.service.db, "evaluation_trial", 1)
        with self.assertRaisesRegex(ContractError, "UNMATCHED_HISTORICAL"):
            self.register()
        self.assertIsNone(self.record())

    def test_terminal_meta_alone_does_not_release_waiting_candidate(self):
        service, candidate, job = self.fixture(self.root / "weekend", SATURDAY)
        self.addCleanup(service.db.close)
        with patch.object(admission, "publish_immutable", side_effect=OSError("synthetic crash")):
            with self.assertRaises(OSError):
                self.register(service, candidate, job, clock=lambda: SATURDAY)
        first = get_meta(service.db, admission.KEY_PREFIX + "job")
        rows = service.db.execute("SELECT * FROM jobs").fetchall()
        waiting, report = admission.waiting_candidates(service, rows)
        self.assertEqual(len(waiting), 1)
        self.assertEqual(report["completed_candidates_without_forward_trial"], 0)
        result = self.register(service, candidate, job, clock=lambda: SATURDAY + 1000)
        self.assertEqual(result, first)
        waiting, report = admission.waiting_candidates(service, rows)
        self.assertEqual(waiting, [])
        self.assertEqual(report["completed_candidates_without_forward_trial"], 1)

    def test_corrupt_terminal_receipt_never_hides_candidate(self):
        service, candidate, job = self.fixture(self.root / "weekend", SATURDAY)
        self.addCleanup(service.db.close)
        self.register(service, candidate, job, clock=lambda: SATURDAY)
        path = Path(service.c["evaluation_directory"]) / "job" / "no-forward-trial.json"
        path.write_text("{}\n")
        with self.assertRaisesRegex(ContractError, "TERMINAL_RECEIPT_CHANGED"):
            admission.waiting_candidates(service, service.db.execute("SELECT * FROM jobs").fetchall())

    def test_concurrent_registration_has_one_plan_and_one_trial(self):
        def worker(_):
            service = object.__new__(Supervisor)
            service.root = self.service.root
            service.c = copy.deepcopy(self.service.c)
            service.db = database(service.root / "scheduler.sqlite")
            try:
                return self.register(service)
            finally:
                service.db.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(worker, range(2)))
        self.assertTrue(all(r["state"] in ("PUBLISHED", "EXISTING_IMMUTABLE_PLAN") for r in results))
        self.assertEqual(get_meta(self.service.db, "evaluation_trial"), 1)
        self.assertEqual(len(list(Path(self.service.c["evaluation_directory"]).glob("*/plan.json"))), 1)

    def test_calendar_is_rechecked_after_read_crosses_closing_boundary(self):
        friday = int(datetime(2026, 10, 16, 19, 45, tzinfo=timezone.utc).timestamp() * 1000)
        service, candidate, job = self.fixture(self.root / "friday", friday)
        self.addCleanup(service.db.close)
        self.capture.return_value = fresh_proof(friday)
        ticks = iter((friday, friday + 60000))
        result = self.register(service, candidate, job, clock=lambda: next(ticks))
        self.assertEqual(result["state"], "NO_FORWARD_TRIAL")
        self.capture.assert_called_once()
        self.assertEqual(get_meta(service.db, "evaluation_trial", 0), 0)

    def test_uncompleted_job_cannot_allocate_a_trial(self):
        with self.service.db:
            self.service.db.execute("UPDATE jobs SET status='RUNNING' WHERE id='job'")
        with self.assertRaisesRegex(ContractError, "COMPLETED_JOB_BINDING"):
            self.register()
        self.assertIsNone(self.record())
        self.assertEqual(get_meta(self.service.db, "evaluation_trial", 0), 0)

    def test_pending_original_trial_keeps_completed_candidate_waiting(self):
        old_path = Path(self.service.c["evaluation_directory"]) / "previous" / "plan.json"
        admission.publish_immutable(old_path, {"trial": 1})
        with self.service.db:
            put_meta(self.service.db, "evaluation_trial", 1)
        admission.inspect_queue.return_value = {"pending_original_trials": 1}
        result = self.register()
        self.assertEqual(result["state"], "DEFERRED")
        self.assertEqual(result["reason"], "PRIOR_ORIGINAL_TRIAL_PENDING")
        self.capture.assert_not_called()
        self.assertEqual(get_meta(self.service.db, "evaluation_trial"), 1)
        self.assertFalse(self.plan_path().exists())

    def test_atomic_publication_never_exposes_incomplete_final_path(self):
        path = self.root / "publication" / "record.json"
        with patch.object(admission.os, "link", side_effect=OSError("synthetic pre-publish crash")):
            with self.assertRaises(OSError):
                admission.publish_immutable(path, {"a": 1})
        self.assertFalse(path.exists())
        admission.publish_immutable(path, {"a": 1})
        before = path.read_bytes()
        with self.assertRaisesRegex(ContractError, "IMMUTABLE_FORWARD_RECORD_CONFLICT"):
            admission.publish_immutable(path, {"a": 2})
        self.assertEqual(path.read_bytes(), before)


    def pending_calendar_fixture(self, name, now=SATURDAY, count=1, futile=True):
        from laya_cell.promotion import assess
        from laya_cell.promotion_power import PROTOCOL
        self.queue.stop()
        service, candidate, job = self.fixture(self.root / name, now)
        self.addCleanup(service.db.close)
        service.c['promotion_protocol'] = PROTOCOL
        evaluation = Path(service.c['evaluation_directory'])
        adapter = (Path(job['output_directory']) / 'adapter.safetensors').read_bytes()
        last_plan = None
        for trial in range(1, count + 1):
            created = now - 60000 - (count - trial) * 86400000
            previous = copy.deepcopy(candidate)
            previous.update(created_at_ms=created - 1000,
                            trained_until_ms=created - 2000,
                            data_fingerprint='previous-' + str(trial))
            previous['window'] = {'training_end_ms': created - 2000,
                                  'validation_start_ms': created - 1999,
                                  'validation_end_ms': created - 2000 + 86400000}
            previous.pop('candidate_id')
            previous['candidate_id'] = digest(previous)
            output = self.root / name / 'previous-candidates' / str(trial)
            output.mkdir(parents=True)
            (output / 'adapter.safetensors').write_bytes(adapter)
            write_once(output / 'candidate.json', previous)
            previous_job = {**job, 'output_directory': str(output)}
            plan = service._evaluation_plan('previous-' + str(trial), previous,
                                            previous_job, trial, created)
            plan['plan_id'] = digest(plan)
            folder = evaluation / ('previous-' + str(trial))
            admission.publish_immutable(folder / 'plan.json', plan)
            if trial < count:
                admission.publish_immutable(folder / 'qualification.json',
                    assess(plan, [], plan['outcome_end_ms']))
            elif futile:
                for slot in range(plan['rule']['max_censored'] + 1):
                    admission.publish_immutable(folder / ('outcome-%03d.json' % slot), {
                        'candidate_id': plan['candidate_id'], 'plan_id': plan['plan_id'],
                        'slot': slot, 'status': 'CENSORED',
                        'reason': 'NO_PREDICTION_IN_FIXED_SLOT',
                        'evaluated_at_ms': plan['start_ms']
                            + slot * plan['rule']['slot_spacing_ms']
                            + plan['rule']['input_grace_ms'] + 1})
            last_plan = plan
        with service.db:
            put_meta(service.db, 'evaluation_trial', count)
        checked = now + 3600000 if futile else now
        record = self.register(service, candidate, job, clock=lambda: checked)
        self.assertEqual(record['state'], 'DEFERRED')
        self.assertEqual(record['reason'], 'PRIOR_ORIGINAL_TRIAL_PENDING')
        history = {p: p.read_bytes() for p in evaluation.glob('previous-*/*') if p.is_file()}
        return service, candidate, job, record, history, last_plan

    def test_reconcile_terminal_calendar_releases_wait_before_original_trial_end(self):
        created = SATURDAY + 19 * 60000
        checked = int(datetime(2026, 10, 18, 2, 30, tzinfo=timezone.utc).timestamp() * 1000)
        service, candidate, job, prior, history, frozen = self.pending_calendar_fixture(
            'terminal', created, count=41)
        self.assertLess(checked, frozen['outcome_end_ms'])
        proposal = service._evaluation_plan('job', candidate, job, 42, checked)
        self.assertGreater(proposal['rule']['slots'], 12)
        expected = admission.calendar_admission(candidate, proposal, checked)
        self.assertEqual(expected['state'], 'NO_FORWARD_TRIAL')
        self.assertEqual(expected['reason'], 'NO_CONTINUOUS_OPEN_WINDOW_FITS')
        service.c['runtime_sha256']['learn/laya_cell/resident.py'] = '1' * 64
        with patch('laya_cell.resident.time.time', return_value=checked / 1000):
            queue = service.reconcile_evaluation_queue()
        record = get_meta(service.db, admission.KEY_PREFIX + 'job')
        folder = Path(service.c['evaluation_directory']) / 'job'
        self.assertEqual(read_json(folder / 'no-forward-trial.json'), record)
        self.assertEqual(record['state'], 'NO_FORWARD_TRIAL')
        self.assertEqual(record['calendar'], expected)
        self.assertEqual(record['policy_runtime_sha256'], prior['policy_runtime_sha256'])
        self.assertEqual(record['candidate_manifest_sha256'], prior['candidate_manifest_sha256'])
        self.assertFalse(record['trial_allocated'])
        self.assertFalse(record['performance_assessment'])
        self.assertEqual(record['broker_orders_sent'], 0)
        self.assertFalse((folder / 'plan.json').exists())
        self.assertEqual(get_meta(service.db, 'evaluation_trial'), 41)
        self.assertEqual(len(list(Path(service.c['evaluation_directory']).glob('*/plan.json'))), 41)
        self.assertEqual(queue['deferred_training_candidates'], 0)
        self.assertEqual(queue['pending_original_trials'], 1)
        self.assertEqual(queue['futile_pending_trials'], 1)
        self.assertEqual(queue['viable_pending_evaluations'], 0)
        self.assertEqual(queue['completed_candidates_without_forward_trial'], 1)
        self.assertEqual({p: p.read_bytes() for p in history}, history)
        self.assertEqual(service.db.execute("SELECT status FROM jobs WHERE id='job'").fetchone()[0],
                         'COMPLETED')
        self.capture.assert_not_called()

    def test_reconcile_keeps_feasible_calendar_waiting_for_original_trial(self):
        sunday = int(datetime(2026, 10, 18, 20, 30, tzinfo=timezone.utc).timestamp() * 1000)
        for name, now, ready in (('ready', NOW, True), ('future-open', sunday, False)):
            with self.subTest(name=name):
                service, candidate, job, before, history, frozen = self.pending_calendar_fixture(
                    name, now, futile=False)
                proposal = service._evaluation_plan('job', candidate, job, 2, now)
                decision = admission.calendar_admission(candidate, proposal, now)
                self.assertEqual(decision['ready'], ready)
                self.assertNotEqual(decision['state'], 'NO_FORWARD_TRIAL')
                with patch('laya_cell.resident.time.time', return_value=now / 1000):
                    queue = service.reconcile_evaluation_queue()
                self.assertEqual(get_meta(service.db, admission.KEY_PREFIX + 'job'), before)
                self.assertEqual(get_meta(service.db, 'evaluation_trial'), 1)
                self.assertEqual(queue['deferred_training_candidates'], 1)
                self.assertEqual(queue['pending_original_trials'], 1)
                self.assertEqual(queue['forward_registration_wait_reasons'],
                                 ['PRIOR_ORIGINAL_TRIAL_PENDING'])
                folder = Path(service.c['evaluation_directory']) / 'job'
                self.assertFalse((folder / 'no-forward-trial.json').exists())
                self.assertFalse((folder / 'plan.json').exists())
                self.assertEqual({p: p.read_bytes() for p in history}, history)
        self.capture.assert_not_called()

    def test_reconcile_recovers_db_first_calendar_terminal_without_retiming(self):
        service, candidate, job, prior, history, frozen = self.pending_calendar_fixture('terminal-crash')
        checked = int(datetime(2026, 10, 18, 2, 30, tzinfo=timezone.utc).timestamp() * 1000)
        with patch('laya_cell.resident.time.time', return_value=checked / 1000):
            with patch.object(admission, '_publish', side_effect=OSError('synthetic terminal crash')):
                with self.assertRaises(OSError):
                    service.reconcile_evaluation_queue()
        first = get_meta(service.db, admission.KEY_PREFIX + 'job')
        self.assertEqual(first['state'], 'NO_FORWARD_TRIAL')
        self.assertFalse(service.db.in_transaction)
        receipt = Path(service.c['evaluation_directory']) / 'job' / 'no-forward-trial.json'
        self.assertFalse(receipt.exists())
        waiting, report = service._waiting_evaluations()
        self.assertEqual(len(waiting), 1)
        self.assertIn('TERMINAL_RECEIPT_PUBLICATION_PENDING', report['forward_registration_wait_reasons'])
        with patch('laya_cell.resident.time.time', return_value=(checked + 60000) / 1000):
            queue = service.reconcile_evaluation_queue()
        self.assertEqual(read_json(receipt), first)
        self.assertEqual(get_meta(service.db, admission.KEY_PREFIX + 'job'), first)
        self.assertEqual(get_meta(service.db, 'evaluation_trial'), 1)
        self.assertEqual(queue['deferred_training_candidates'], 0)
        self.assertEqual({p: p.read_bytes() for p in history}, history)
        self.capture.assert_not_called()

    def test_reconcile_calendar_terminal_rejects_changed_bound_evidence(self):
        checked = int(datetime(2026, 10, 18, 2, 30, tzinfo=timezone.utc).timestamp() * 1000)
        for change in ('manifest', 'receipt', 'policy-pin'):
            with self.subTest(change=change):
                service, candidate, job, before, history, frozen = self.pending_calendar_fixture(
                    'changed-' + change)
                if change == 'manifest':
                    changed = copy.deepcopy(candidate)
                    changed['created_at_ms'] += 1
                    (Path(job['output_directory']) / 'candidate.json').write_bytes(canonical(changed) + b'\n')
                elif change == 'receipt':
                    changed = {**before, 'registration_id': '0' * 64}
                    with service.db:
                        put_meta(service.db, admission.KEY_PREFIX + 'job', changed)
                else:
                    service.c['runtime_sha256'][admission.RUNTIME_PIN[0]] = 'c' * 64
                original_meta = get_meta(service.db, admission.KEY_PREFIX + 'job')
                with patch('laya_cell.resident.time.time', return_value=checked / 1000):
                    with self.assertRaises(ContractError):
                        service.reconcile_evaluation_queue()
                self.assertEqual(get_meta(service.db, admission.KEY_PREFIX + 'job'), original_meta)
                self.assertFalse(service.db.in_transaction)
                self.assertEqual(get_meta(service.db, 'evaluation_trial'), 1)
                folder = Path(service.c['evaluation_directory']) / 'job'
                self.assertFalse((folder / 'no-forward-trial.json').exists())
                self.assertFalse((folder / 'plan.json').exists())
                self.assertEqual({p: p.read_bytes() for p in history}, history)
        self.capture.assert_not_called()

    def test_reconcile_allocated_trial_never_becomes_calendar_terminal(self):
        from laya_cell.promotion_power import PROTOCOL
        self.queue.stop()
        for initial in ('RESERVED', 'PUBLISHED'):
            with self.subTest(initial=initial):
                service, candidate, job = self.fixture(self.root / ('allocated-' + initial))
                self.addCleanup(service.db.close)
                service.c['promotion_protocol'] = PROTOCOL
                self.capture.side_effect = None
                self.capture.return_value = fresh_proof()
                if initial == 'RESERVED':
                    with patch.object(admission, '_publish', side_effect=OSError('synthetic reservation crash')):
                        with self.assertRaises(OSError):
                            self.register(service, candidate, job)
                else:
                    self.register(service, candidate, job)
                before = get_meta(service.db, admission.KEY_PREFIX + 'job')
                self.assertEqual(before['state'], initial)
                original = before['plan']
                self.capture.reset_mock()
                self.capture.side_effect = AssertionError('allocated trial must not read new source')
                with patch('laya_cell.resident.time.time', return_value=SATURDAY / 1000):
                    with patch.object(service, '_finish_impossible_forward_registration',
                                      wraps=service._finish_impossible_forward_registration) as terminal:
                        queue = service.reconcile_evaluation_queue()
                folder = Path(service.c['evaluation_directory']) / 'job'
                self.assertEqual(read_json(folder / 'plan.json'), original)
                self.assertFalse((folder / 'no-forward-trial.json').exists())
                self.assertEqual(get_meta(service.db, 'evaluation_trial'), 1)
                self.assertEqual(get_meta(service.db, admission.KEY_PREFIX + 'job')['state'], 'PUBLISHED')
                self.assertEqual(queue['deferred_training_candidates'], 0)
                terminal.assert_not_called()
                self.capture.assert_not_called()

    def test_reconcile_calendar_clock_regression_preserves_deferred_record(self):
        service, candidate, job, before, history, frozen = self.pending_calendar_fixture('clock-regression')
        with patch('laya_cell.resident.time.time', return_value=(before['checked_at_ms'] - 1) / 1000):
            with self.assertRaisesRegex(ContractError, 'FORWARD_CALENDAR_CLOCK_REGRESSION'):
                service.reconcile_evaluation_queue()
        self.assertEqual(get_meta(service.db, admission.KEY_PREFIX + 'job'), before)
        self.assertEqual(get_meta(service.db, 'evaluation_trial'), 1)
        self.assertFalse(service.db.in_transaction)
        self.assertEqual({p: p.read_bytes() for p in history}, history)
        self.capture.assert_not_called()



    def closed_fixture(self, name):
        sunday = int(datetime(2026, 10, 18, 20, 30, tzinfo=timezone.utc).timestamp() * 1000)
        service, candidate, job = self.fixture(self.root / name, sunday)
        self.addCleanup(service.db.close)
        record = self.register(service, candidate, job, clock=lambda: sunday)
        self.assertEqual(record["state"], "DEFERRED")
        self.assertEqual(record["reason"], "MARKET_SESSION_CLOSED")
        return sunday, service, candidate, job, record

    def test_stable_closed_reconcile_preserves_receipt_without_registration(self):
        from laya_cell import resident
        sunday, service, candidate, job, record = self.closed_fixture("closed-fast-wait")
        path = Path(service.c["evaluation_directory"]) / "job" / "registration-status.json"
        before = path.read_bytes()
        changes = service.db.total_changes
        with (patch.object(resident.time, "time", return_value=(sunday + 1000) / 1000),
              patch.object(resident, "inspect_evaluation_queue", wraps=resident.inspect_evaluation_queue) as queue,
              patch.object(service, "_waiting_evaluations", wraps=service._waiting_evaluations) as waiting,
              patch.object(service, "candidate_done", wraps=service.candidate_done) as registration):
            result = service.reconcile_evaluation_queue()
        self.assertEqual(result["deferred_training_candidates"], 1)
        self.assertEqual(result["forward_registration_wait_reasons"], ["MARKET_SESSION_CLOSED"])
        self.assertEqual(queue.call_count, 1)
        self.assertEqual(waiting.call_count, 1)
        registration.assert_not_called()
        self.assertEqual(service.db.total_changes, changes)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(get_meta(service.db, admission.KEY_PREFIX + "job"), record)
        self.capture.assert_not_called()

    def test_closed_reconcile_takes_original_source_path_at_open(self):
        from laya_cell import resident
        sunday, service, candidate, job, record = self.closed_fixture("closed-open")
        opened = int(datetime(2026, 10, 18, 21, 15, tzinfo=timezone.utc).timestamp() * 1000)
        self.capture.return_value = fresh_proof(opened)
        with (patch.object(resident.time, "time", return_value=opened / 1000),
              patch.object(service, "candidate_done", wraps=service.candidate_done) as registration):
            result = service.reconcile_evaluation_queue()
        registration.assert_called_once()
        self.capture.assert_called_once()
        self.assertEqual(result["deferred_training_candidates"], 0)
        self.assertEqual(get_meta(service.db, "evaluation_trial"), 1)
        self.assertEqual(get_meta(service.db, admission.KEY_PREFIX + "job")["state"], "PUBLISHED")

    def test_expired_closed_wait_becomes_original_terminal_receipt(self):
        from laya_cell import resident
        sunday, service, candidate, job, record = self.closed_fixture("closed-expired")
        expired = candidate["window"]["validation_end_ms"] + 1
        with patch.object(resident.time, "time", return_value=expired / 1000):
            result = service.reconcile_evaluation_queue()
        self.assertEqual(result["deferred_training_candidates"], 0)
        self.assertEqual(get_meta(service.db, "evaluation_trial", 0), 0)
        self.assertEqual(get_meta(service.db, admission.KEY_PREFIX + "job")["state"], "NO_FORWARD_TRIAL")
        self.capture.assert_not_called()

    def test_missing_advisory_status_is_republished_by_original_registration(self):
        from laya_cell import resident
        sunday, service, candidate, job, record = self.closed_fixture("closed-missing-status")
        path = Path(service.c["evaluation_directory"]) / "job" / "registration-status.json"
        path.unlink()
        with (patch.object(resident.time, "time", return_value=(sunday + 1000) / 1000),
              patch.object(service, "candidate_done", wraps=service.candidate_done) as registration):
            result = service.reconcile_evaluation_queue()
        registration.assert_called_once()
        self.assertTrue(path.exists())
        self.assertEqual(result["deferred_training_candidates"], 1)
        self.assertEqual(get_meta(service.db, "evaluation_trial", 0), 0)
        self.capture.assert_not_called()

    def test_stable_closed_wait_still_rejects_clock_regression(self):
        from laya_cell import resident
        sunday, service, candidate, job, record = self.closed_fixture("closed-clock")
        with patch.object(resident.time, "time", return_value=(sunday - 1) / 1000):
            with self.assertRaisesRegex(ContractError, "FORWARD_CALENDAR_CLOCK_REGRESSION"):
                service.reconcile_evaluation_queue()
        self.assertEqual(get_meta(service.db, admission.KEY_PREFIX + "job"), record)


class OriginalReceiptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.feed = self.root / "feed"
        self.folder = self.feed / "segment-synthetic"
        self.folder.mkdir(parents=True)
        self.path = self.folder / "quotes.jsonl"
        self.config = {"period_sha256": "a" * 64, "feed_directory": str(self.feed)}

    def source(self, **quote_changes):
        q = fresh_proof(**quote_changes)["quote"]
        header = {"source": "IB_TOOL_GATEWAY", "execution_domain": "PAPER",
                  "instrument": "EUR.USD", "period_sha256": "a" * 64,
                  "write_operations_allowed": [], "broker_orders_sent": 0}
        rows = []
        previous = "0" * 64
        for seq, (kind, data, at) in enumerate((
                ("HEADER", header, q["observed_at_ms"] - 1),
                ("QUOTE", q, q["observed_at_ms"] + 50))):
            row = {"schema": QUOTE_SCHEMA, "sequence": seq, "previous": previous,
                   "at_ms": at, "kind": kind, "data": data}
            row["sha256"] = digest(row)
            previous = row["sha256"]
            rows.append(canonical(row) + b"\n")
        self.path.write_bytes(b"".join(rows))
        self.path.chmod(0o600)
        _, quotes, pin = read_source(self.path)
        return quotes, [pin]

    def capture(self, quotes, pins):
        selected = quotes[-1]
        sample = {"anchor_ms": selected["t"]}
        with patch.object(admission, "read_for_future_plan",
                          return_value=(quotes, pins, "ORIGINAL_FULL_HISTORY_V1")), \
             patch.object(admission, "forecast_input",
                          return_value=(sample, {"state": {"instrument": "EUR.USD"}})):
            return admission.capture_source(self.config, {}, NOW)

    def test_authoritative_original_receipt_is_bound(self):
        quotes, pins = self.source()
        proof = self.capture(quotes, pins)
        self.assertEqual(proof["quote_receipt"], quotes[-1]["receipt"])
        self.assertEqual(proof["verified_prefix"], pins[0])
        self.assertFalse(proof["future_data_availability_proven"])
        self.assertEqual(proof["broker_queries_sent"], 0)

    def test_fresh_status_cannot_refresh_an_old_original_quote(self):
        quotes, pins = self.source(observed_at_ms=NOW - 5001, stale_after_ms=NOW - 1)
        with self.assertRaisesRegex(ValueError, "QUOTE_STALE_OR_FUTURE"):
            self.capture(quotes, pins)

    def test_original_shorter_expiry_and_subscription_state_are_enforced(self):
        quotes, pins = self.source(stale_after_ms=NOW + 100)
        proof = self.capture(quotes, pins)
        with self.assertRaisesRegex(ValueError, "QUOTE_STALE_OR_FUTURE"):
            admission.check_source(self.config, proof, NOW + 100)
        quotes, pins = self.source(subscription_state="inactive")
        with self.assertRaisesRegex(ContractError, "SUBSCRIPTION_OR_CLOCK"):
            self.capture(quotes, pins)

    def test_btc_or_non_authoritative_quote_cannot_stand_in_for_eur(self):
        for changes in ({"instrument": "BTC.USD"}, {"authoritative": False}):
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, "QUOTE_AUTHORITY"):
                self.source(**changes)

    def test_rewritten_prefix_after_first_read_is_rejected(self):
        quotes, pins = self.source()
        self.path.write_bytes(self.path.read_bytes().replace(b"1.1001", b"1.1002"))
        with self.assertRaisesRegex(ContractError, "PREFIX_CHANGED"):
            self.capture(quotes, pins)


if __name__ == "__main__":
    unittest.main()
