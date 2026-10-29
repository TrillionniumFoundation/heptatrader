"""CPU-only forward admission tests through the frozen registration transaction."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from historical_training.common import EvidenceError
from historical_training.resident_resource_guard import guarded_trainer_class
from laya_cell import resident_plan_admission as admission
from laya_cell import resident as resident_module
from laya_cell.resident import Supervisor
from laya_cell.resident_store import get_meta
from laya_cell.integrity import canonical
from laya_cell.resident_queue import OVERLAP_PROFILE, OVERLAP_FIELD, OVERLAP_EFFECTIVE
from laya_cell.tests import test_resident_plan_admission as fixtures


class RegistrationGate:
    def __init__(self, owner, decisions):
        self.owner = owner
        self.decisions = iter(decisions)
        self.calls = []
        self.prediction_readiness = []
        self.acquire = Mock(side_effect=AssertionError("NO_GPU_LEASE_FOR_REGISTRATION"))
        self.release = Mock(side_effect=AssertionError("NO_LEASE_TO_RELEASE"))

    def check(self, *, forward_registration, prediction_ready=False):
        self.owner.assertTrue(self.service.db.in_transaction)
        self.calls.append(dict(forward_registration))
        self.prediction_readiness.append(prediction_ready)
        decision = next(self.decisions)
        if isinstance(decision, BaseException):
            raise decision
        return {"admit_new_worker": decision,
                "at_ms": fixtures.NOW,
                "subject": dict(forward_registration),
                "admission_scope": "FORWARD_REGISTRATION_PREFLIGHT",
                "reasons": [] if decision else ["SOURCE_CONTINUITY_NOT_READY"],
                "trial_allocated": False,
                "worker_launch_authority": False}


class ForwardRegistrationTests(unittest.TestCase):
    def setUp(self):
        box = tempfile.TemporaryDirectory()
        self.addCleanup(box.cleanup)
        self.root = Path(box.name)
        self.service, self.candidate, self.job = self.make_service(self.root)
        self.queue = patch.object(admission, "inspect_queue",
                                  return_value={"pending_original_trials": 0})
        self.inspect = self.queue.start()
        self.addCleanup(self.queue.stop)
        source = patch.object(admission, "capture_source",
                              return_value=fixtures.fresh_proof())
        self.capture = source.start()
        self.addCleanup(source.stop)
        clock = patch.object(admission.time, "time", return_value=fixtures.NOW / 1000)
        self.clock = clock.start()
        self.addCleanup(clock.stop)

    def make_service(self, root, now=fixtures.NOW):
        # Reuse the original fixture without inheriting and rerunning its tests.
        service, candidate, job = fixtures.RegistrationTests.fixture(self, root, now)
        self.addCleanup(service.db.close)
        return service, candidate, job

    def wrap(self, decisions, service=None):
        service = service or self.service
        gate = RegistrationGate(self, decisions)
        gate.service = service
        service.__class__ = guarded_trainer_class(Supervisor, gate)
        return gate

    def register(self):
        return self.service.register_evaluation("job", self.candidate, self.job)

    def plan_path(self):
        return Path(self.service.c["evaluation_directory"]) / "job" / "plan.json"

    def assert_unallocated(self):
        self.assertFalse(self.service.db.in_transaction)
        self.assertEqual(get_meta(self.service.db, "evaluation_trial", 0), 0)
        self.assertFalse(self.plan_path().exists())
        self.assertEqual(self.service.db.execute("SELECT status FROM jobs").fetchone()[0],
                         "COMPLETED")

    def test_hold_then_ready_keeps_candidate_waiting_and_publishes_once(self):
        gate = self.wrap([False, True, True])
        consumed = self.service.db.execute("SELECT * FROM consumed_rows").fetchall()
        report = self.register()
        self.assertEqual(report["state"], "FORWARD_REGISTRATION_GUARD_HOLD")
        self.assert_unallocated()
        self.assertIsNone(get_meta(self.service.db, admission.KEY_PREFIX + "job"))
        self.assertIsNone(self.service._forward_registration_context)
        waiting, _ = self.service._waiting_evaluations()
        self.assertEqual([row["id"] for row in waiting], ["job"])
        self.assertEqual(self.service.db.execute("SELECT * FROM consumed_rows").fetchall(),
                         consumed)
        self.capture.assert_not_called()
        self.assertEqual(self.register()["state"], "PUBLISHED")
        original = self.plan_path().read_bytes()
        self.assertEqual(get_meta(self.service.db, "evaluation_trial"), 1)
        self.assertIsNone(self.service._forward_registration_preflight)
        self.register()
        self.assertEqual(self.plan_path().read_bytes(), original)
        self.assertEqual(len(gate.calls), 3)
        gate.acquire.assert_not_called()
        gate.release.assert_not_called()

    def test_source_changes_after_first_preflight_roll_back_before_reservation(self):
        gate = self.wrap([True, False])
        self.assertEqual(self.register()["state"], "FORWARD_REGISTRATION_GUARD_HOLD")
        self.assert_unallocated()
        self.assertIsNone(get_meta(self.service.db, admission.KEY_PREFIX + "job"))
        self.capture.assert_called_once()
        self.assertEqual(len(gate.calls), 2)
        self.assertIsNone(self.service._forward_registration_context)

    def test_pending_trial_retry_cannot_bypass_preflight(self):
        gate = self.wrap([False])
        self.inspect.return_value = {"pending_original_trials": 1}
        self.assertEqual(self.register()["state"], "DEFERRED")
        before = get_meta(self.service.db, admission.KEY_PREFIX + "job")
        self.assertEqual(gate.calls, [])
        self.inspect.return_value = {"pending_original_trials": 0}
        self.assertEqual(self.register()["state"], "FORWARD_REGISTRATION_GUARD_HOLD")
        self.assertEqual(get_meta(self.service.db, admission.KEY_PREFIX + "job"), before)
        self.assert_unallocated()
        self.assertEqual(len(gate.calls), 1)

    def test_reserved_recovery_preserves_plan_without_new_gate_or_source(self):
        gate = self.wrap([True, True])
        with patch.object(admission, "_publish", side_effect=OSError("synthetic crash")):
            with self.assertRaises(OSError):
                self.register()
        reserved = get_meta(self.service.db, admission.KEY_PREFIX + "job")
        self.assertEqual(reserved["state"], "RESERVED")
        self.capture.side_effect = AssertionError("FROZEN_RESERVATION_NEEDS_NO_NEW_SOURCE")
        self.clock.return_value = fixtures.SATURDAY / 1000
        self.assertEqual(self.register()["state"], "PUBLISHED")
        from laya_cell.integrity import read_json
        self.assertEqual(read_json(self.plan_path()), reserved["plan"])
        self.assertEqual(get_meta(self.service.db, "evaluation_trial"), 1)
        self.assertEqual(len(gate.calls), 2)

    def test_closed_calendar_and_terminal_recovery_do_not_request_gate(self):
        service, candidate, job = self.make_service(self.root / "weekend", fixtures.SATURDAY)
        gate = self.wrap([], service)
        self.clock.return_value = fixtures.SATURDAY / 1000
        first = service.register_evaluation("job", candidate, job)
        self.assertEqual(first["state"], "NO_FORWARD_TRIAL")
        self.assertEqual(service.register_evaluation("job", candidate, job), first)
        self.assertEqual(get_meta(service.db, "evaluation_trial", 0), 0)
        self.capture.assert_not_called()
        self.assertEqual(gate.calls, [])

    def test_calendar_probe_is_unchanged_and_wrong_context_fails(self):
        gate = self.wrap([])
        proposal = self.service._evaluation_plan("job", self.candidate, self.job, 1, fixtures.NOW)
        self.assertEqual(proposal["candidate_id"], self.candidate["candidate_id"])
        self.assertEqual(gate.calls, [])
        self.service._forward_registration_context = ("another-job", self.candidate["candidate_id"])
        with self.assertRaisesRegex(EvidenceError, "FORWARD_PREFLIGHT_CONTEXT_CHANGED"):
            self.service._evaluation_plan("job", self.candidate, self.job, 1, fixtures.NOW)
        self.assertEqual(gate.calls, [])

    def test_new_overlap_job_checks_first_prediction_resources_without_gpu_lease(self):
        policy = {OVERLAP_FIELD: OVERLAP_PROFILE, OVERLAP_EFFECTIVE: fixtures.NOW - 3000}
        self.service.c.update(policy)
        self.service.c['runtime_sha256']['learn/laya_cell/resident_queue.py'] = 'e' * 64
        self.job.update(policy)
        with self.service.db:
            self.service.db.execute('UPDATE jobs SET payload=? WHERE id=?',
                                   (canonical(self.job).decode(), 'job'))
        self.inspect.return_value = {'pending_original_trials': 0,
                                     'viable_pending_evaluations': 0, 'futile_plan_ids': []}
        gate = self.wrap([True, True])
        self.assertEqual(self.register()['state'], 'PUBLISHED')
        self.assertEqual(gate.prediction_readiness, [True, True])
        gate.acquire.assert_not_called()
        gate.release.assert_not_called()

    def test_unexpected_error_rolls_back_and_restores_outer_context(self):
        self.wrap([RuntimeError("synthetic preflight failure")])
        outer = ("outer-job", "outer-candidate")
        self.service._forward_registration_context = outer
        with self.assertRaisesRegex(RuntimeError, "synthetic preflight failure"):
            self.register()
        self.assert_unallocated()
        self.assertEqual(self.service._forward_registration_context, outer)
        self.assertIsNone(get_meta(self.service.db, admission.KEY_PREFIX + "job"))

    def test_real_queue_preflight_retries_hold_and_stops_new_training(self):
        gate = self.wrap([False, False])
        self.service.sync_rewards = Mock()
        queue = {"pending_original_trials": 0, "viable_pending_evaluations": 0}
        with patch.object(resident_module, "config_check"), \
             patch.object(resident_module, "inspect_storage",
                          return_value={"admission_ready": True}), \
             patch.object(Supervisor, "write_status", lambda self, **extra: extra), \
             patch.object(resident_module, "inspect_evaluation_queue", return_value=queue):
            for _ in range(2):
                status = self.service.step()
                self.assertEqual(status["state"], "CANDIDATE_READY_AWAITING_FORWARD_ADMISSION")
                self.assertEqual(status["deferred_training_candidates"], 1)
                self.assertEqual(status["next_action"], "ORIGINAL_TRAINER_RECHECKS_SAME_CANDIDATE_FORWARD_ADMISSION")
                resume = status["forward_registration_resume_conditions"]
                self.assertEqual(resume["last_preflight"]["subject"], {"job_id": "job", "candidate_id": self.candidate["candidate_id"]})
                self.assertEqual(resume["waiting_reasons"], ["SOURCE_CONTINUITY_NOT_READY"])
                self.assertFalse(resume["current_admission_granted"])
                self.assertFalse(resume["worker_launch_authority"])
        self.assertEqual(len(gate.calls), 2)
        self.assert_unallocated()
        gate.acquire.assert_not_called()
        gate.release.assert_not_called()


if __name__ == "__main__":
    unittest.main()
