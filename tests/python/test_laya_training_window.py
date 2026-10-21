"""Training must retain a complete original forward window before consuming rows."""
import copy
import tempfile
import unittest
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch
from historical_training.resident_resource_guard import guarded_trainer_class
from laya_cell.promotion import PROTOCOL
from laya_cell.resident import Supervisor, training_forward_window
from laya_cell.resident_plan_admission import PROFILE
from laya_cell.resident_store import database,admit_job
from laya_cell.integrity import digest
from laya_cell import resident_training_v4

RULE={'slots':94,'slot_spacing_ms':360000,'input_grace_ms':60000,'horizon_ms':300000}


def at(value):
    return int(datetime.fromisoformat(value).timestamp()*1000)


def window(now):
    return {'training_end_ms':now,'validation_start_ms':now+1,
            'validation_end_ms':now+86400000}


class TrainingWindowTests(unittest.TestCase):
    def test_open_morning_leaves_full_timeout_and_original_evaluation(self):
        now=at('2026-10-20T08:30:00+00:00')
        w=window(now);before=copy.deepcopy(w)
        result=training_forward_window(w,RULE,now)
        self.assertTrue(result['ready'])
        self.assertEqual(result['training_budget_ms'],660000)
        self.assertEqual(result['calendar']['length_ms'],33845000)
        self.assertEqual(w,before)
        self.assertFalse(result['trial_allocated'])
        self.assertFalse(result['future_data_availability_proven'])

    def test_real_32_second_rollover_failure_is_refused_before_training(self):
        now=at('2026-10-19T11:34:27.442+00:00')
        cutoff=at('2026-10-18T21:31:18.621+00:00')
        result=training_forward_window(window(cutoff),RULE,now)
        self.assertFalse(result['ready'])
        self.assertEqual(result['calendar']['reason'],'NO_CONTINUOUS_OPEN_WINDOW_FITS')
        self.assertIsNone(result['next_training_at_ms'])

    def test_current_plan_fit_is_insufficient_when_training_budget_crosses_close(self):
        now=at('2026-10-19T11:25:00+00:00')
        result=training_forward_window(window(now),RULE,now)
        self.assertTrue(result['calendar']['ready'])
        self.assertFalse(result['ready'])
        self.assertEqual(result['next_training_at_ms'],at('2026-10-19T21:15:00+00:00'))

    def test_closed_weekend_does_not_start_an_unusable_candidate(self):
        now=at('2026-10-17T22:00:00+00:00')
        result=training_forward_window(window(now),RULE,now)
        self.assertFalse(result['ready'])

    def test_expired_collected_window_cannot_be_renewed_by_preview(self):
        now=at('2026-10-20T08:30:00+00:00')
        w=window(now-86400001)
        before=copy.deepcopy(w)
        result=training_forward_window(w,RULE,now)
        self.assertFalse(result['ready'])
        self.assertEqual(result['calendar']['reason'],'CANDIDATE_WINDOW_EXPIRED')
        self.assertEqual(w,before)


class TrainingWindowSchedulingTests(unittest.TestCase):
    def subject(self,now,*,guarded=True):
        temporary=tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        gate=MagicMock()
        gate.acquire.return_value=True
        cls=guarded_trainer_class(Supervisor,gate) if guarded else Supervisor
        service=cls.__new__(cls)
        service.root=Path(temporary.name)
        service.c={'promotion_protocol':PROTOCOL,'future_plan_admission_profile':PROFILE}
        service.storage=None
        service.db=database(service.root/'db')
        self.addCleanup(service.db.close)
        clock=[now]
        stack=ExitStack();self.addCleanup(stack.close)
        stack.enter_context(patch('laya_cell.resident.time.time',side_effect=lambda:clock[0]/1000))
        stack.enter_context(patch('laya_cell.resident.config_check'))
        stack.enter_context(patch('laya_cell.resident.bootstrap_still_off'))
        stack.enter_context(patch('laya_cell.resident.inspect_storage',return_value={'admission_ready':True}))
        stack.enter_context(patch('laya_cell.resident.rule_for_trial',return_value=RULE))
        stack.enter_context(patch.object(service,'sync_rewards',return_value={'reason':'fixture'}))
        stack.enter_context(patch.object(service,'reconcile_evaluation_queue',return_value={
            'deferred_training_candidates':0,'viable_pending_evaluations':0,'pending_original_trials':0}))
        stack.enter_context(patch.object(service,'write_status',side_effect=lambda **fields:fields))
        collect=stack.enter_context(patch('laya_cell.resident.collect',side_effect=RuntimeError('COLLECT_REACHED')))
        return service,gate,collect,clock

    def test_window_hold_precedes_heavy_lease_and_archive_collect(self):
        service,gate,collect,_=self.subject(at('2026-10-19T11:25:00+00:00'))
        result=service.step()
        self.assertEqual(result['state'],'HOLD_NEW_TRAINING_FORWARD_WINDOW')
        self.assertFalse(result['training_data_refresh_performed'])
        gate.acquire.assert_not_called()
        collect.assert_not_called()
        self.assertEqual(service.db.execute('SELECT count(*) FROM jobs').fetchone()[0],0)
        self.assertEqual(service.db.execute('SELECT count(*) FROM consumed_rows').fetchone()[0],0)

    def test_open_window_reenters_existing_guard_and_collect(self):
        service,gate,collect,_=self.subject(at('2026-10-20T08:30:00+00:00'))
        with self.assertRaisesRegex(RuntimeError,'COLLECT_REACHED'):
            service.step()
        gate.acquire.assert_called_once()
        gate.release.assert_called_once()
        collect.assert_called_once()

    def test_slow_collect_rechecks_original_window_before_row_reservation(self):
        now=at('2026-10-19T08:30:00+00:00')
        service,gate,collect,clock=self.subject(now)
        rows=[{'sample_id':str(i),'horizon_sec':300,'outcome_receipt_digest':str(i)}
              for i in range(16)]
        def delayed_collect(*args,**kwargs):
            clock[0]=at('2026-10-19T11:25:00+00:00')
            return {},rows,{'window':window(now)},None
        collect.side_effect=delayed_collect
        result=service.step()
        self.assertEqual(result['state'],'HOLD_NEW_TRAINING_FORWARD_WINDOW')
        self.assertTrue(result['training_data_refresh_performed'])
        self.assertFalse(result['rows_reserved'])
        gate.release.assert_called_once()
        self.assertEqual(service.db.execute('SELECT count(*) FROM jobs').fetchone()[0],0)
        self.assertEqual(service.db.execute('SELECT count(*) FROM consumed_rows').fetchone()[0],0)

    def test_job_records_actual_unreserved_pool_before_original_selection(self):
        now=at('2026-10-20T08:30:00+00:00')
        service,gate,collect,_=self.subject(now)
        parent=service.root/'parent';parent.mkdir()
        (parent/'candidate.json').write_text('{"candidate_id":"fixed-parent","generation":2}')
        service.c.update(objective=resident_training_v4.OBJECTIVE,profile={'fixture':True},
                         initial_parent_directory=str(parent),candidate_directory=str(service.root/'candidates'))
        rows=[{'sample_id':str(i),'horizon_sec':300,'outcome_receipt_digest':str(i),'anchor_ms':now-1000000+i}
              for i in range(64)]
        ids=[digest([r['sample_id'],r['horizon_sec'],r['outcome_receipt_digest']]) for r in rows]
        self.assertTrue(admit_job(service.db,'old','old-fingerprint',now-1800000,{},[ids[0]]))
        collect.side_effect=None;collect.return_value=({},rows,{'window':window(now)},None)
        with patch('laya_cell.resident.admit_job',return_value=False) as admission:
            result=service.step()
        self.assertEqual(result['state'],'JOB_ALREADY_ADMITTED')
        job=admission.call_args.args[4]
        self.assertEqual(job['selection_context'],{'pool_count':63,'pool_row_ids_digest':digest(sorted(ids[1:]))})
        self.assertEqual(len(job['training_row_ids']),32)
        self.assertNotIn(ids[0],job['training_row_ids'])
        self.assertEqual(collect.call_args.kwargs['reserved_row_ids'],{ids[0]})
        self.assertEqual(service.db.execute('SELECT count(*) FROM consumed_rows').fetchone()[0],1)
        gate.release.assert_called_once()

    def test_unenrolled_legacy_scheduler_does_not_apply_new_window_policy(self):
        service,_,_,_=self.subject(at('2026-10-19T11:25:00+00:00'))
        service.c.pop('future_plan_admission_profile')
        with patch('laya_cell.resident.training_forward_window',side_effect=AssertionError('UNENROLLED')):
            self.assertIsNone(service._training_window_wait(at('2026-10-19T11:25:00+00:00'),{}))


if __name__=='__main__':unittest.main()
