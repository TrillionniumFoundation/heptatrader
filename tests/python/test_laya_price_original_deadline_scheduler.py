"""Five-minute Tiny MLP research deadlines, with NO sleeps or live broker data."""
import unittest
from laya_cell.integrity import ContractError
from laya_cell.price_original_campaign import (
    create_plan,expected_anchor,pending_actions,
    HORIZON_MS,INPUT_GRACE_MS,SLOT_SPACING_MS,SLOTS)

PINS={k:"a"*64 for k in (
    "revised_bars","frozen_folds","model_weights","frozen_selection",
    "frozen_protocol","feature_module","original_bridge","contract_module",
    "runner","source_config")}
PRIOR=[.05,.05,.1,.65,.1,.03,.02]


class OriginalTinyMlpDeadlineScheduleTests(unittest.TestCase):
    def setUp(self):
        self.plan=create_plan(1800000000000,PINS,PRIOR,"b"*64)
        self.t0=expected_anchor(self.plan,0)
        self.t1=expected_anchor(self.plan,1)
        self.assertEqual(self.t1,self.t0+HORIZON_MS)

    def due(self,now,predicted=None,recorded=None):
        return pending_actions(self.plan,now,set() if predicted is None else set(predicted),
                               set() if recorded is None else set(recorded))

    def test_last_prediction_future_outcome_does_not_block_next_precommit(self):
        # At t1, slot0's outcome opens, but slot1's INPUT MUST come first.
        self.assertEqual(self.due(self.t1+3000,{0}),[(1,"TRY_PREDICT"),(0,"TRY_GRADE")])

    def test_prior_outcome_missing_inside_grace_still_keeps_next_input_eligible(self):
        self.assertEqual(self.due(self.t1+INPUT_GRACE_MS-1,{0}),
                         [(1,"TRY_PREDICT"),(0,"TRY_GRADE")])

    def test_end_of_full_grace_requires_both_attempts_no_premature_censor(self):
        self.assertEqual(self.due(self.t1+INPUT_GRACE_MS,{0}),
                         [(1,"TRY_PREDICT"),(0,"TRY_GRADE")])

    def test_one_millisecond_late_censors_both_not_hindsight_success(self):
        self.assertEqual(self.due(self.t1+INPUT_GRACE_MS+1,{0}),
                         [(1,"CENSOR_INPUT"),(0,"CENSOR_OUTCOME")])

    def test_existing_first_prediction_can_settle_after_second_prediction_precommit(self):
        self.assertEqual(self.due(self.t1+5000,{0,1}),[(0,"TRY_GRADE")])

    def test_already_observed_first_slot_does_not_create_more_work(self):
        self.assertEqual(self.due(self.t1+3000,{0},{0}),[(1,"TRY_PREDICT")])

    def test_after_restart_pending_forecast_graded_or_censored_never_repredicted(self):
        self.assertEqual(self.due(self.t0+HORIZON_MS+1000,{0}),
                         [(1,"TRY_PREDICT"),(0,"TRY_GRADE")])
        self.assertEqual(self.due(self.t0+HORIZON_MS+INPUT_GRACE_MS+1,{0}),
                         [(1,"CENSOR_INPUT"),(0,"CENSOR_OUTCOME")])

    def test_clean_restart_without_forecast_marks_expired_input(self):
        self.assertEqual(self.due(self.t1+INPUT_GRACE_MS+1),
                         [(0,"CENSOR_INPUT"),(1,"CENSOR_INPUT")])

    def test_before_all_future_anchors_does_not_predeclare_success(self):
        self.assertEqual(self.due(self.t0-1),[])

    def test_last_window_does_not_schedule_91st_input(self):
        last=expected_anchor(self.plan,SLOTS-1)
        completed=set(range(SLOTS-1))
        self.assertEqual(self.due(last,{SLOTS-1},completed),[])
        self.assertEqual(self.due(last+HORIZON_MS,{SLOTS-1},completed),
                         [(SLOTS-1,"TRY_GRADE")])
        self.assertEqual(self.due(last+HORIZON_MS+INPUT_GRACE_MS+1,
                                  {SLOTS-1},completed),[(SLOTS-1,"CENSOR_OUTCOME")])

    def test_valid_scoring_window_never_earlier_than_actual_horizon(self):
        self.assertEqual(self.due(self.t0+HORIZON_MS-1,{0}),[])

    def test_completed_slot_cannot_be_rescored_on_retry(self):
        actions=self.due(self.t0+HORIZON_MS+INPUT_GRACE_MS+9000,{0},{0})
        self.assertNotIn((0,"TRY_GRADE"),actions)
        self.assertNotIn((0,"CENSOR_OUTCOME"),actions)
        self.assertEqual(actions,[(1,"CENSOR_INPUT")])

    def test_invalid_boolean_and_foreign_slots_fail_closed(self):
        for pred,done in [({True},set()),({-1},set()),({SLOTS},set()),
                          (set(),{"0"}),({0.1},set())]:
            with self.assertRaisesRegex(ContractError,"SCHEDULER_IDENTITY"):
                self.due(self.t1,pred,done)

    def test_none_and_nonstrict_clocks_fail_closed(self):
        for clock in (None,True,1.0,"1800000000000"):
            with self.assertRaisesRegex(ContractError,"SCHEDULER_IDENTITY"):
                self.due(clock)

    def test_action_priority_holds_during_backlog_of_expired_outcomes(self):
        pending={0,1,2}
        t=expected_anchor(self.plan,3)+1000
        actions=self.due(t,pending)
        self.assertEqual(actions[0],(3,"TRY_PREDICT"))
        self.assertEqual(actions[1:],
                         [(2,"TRY_GRADE"),(0,"CENSOR_OUTCOME"),
                          (1,"CENSOR_OUTCOME")])

    def test_active_input_survives_large_expired_input_backlog_after_restart(self):
        current=expected_anchor(self.plan,12)+3500
        actions=self.due(current)
        self.assertEqual(actions[0],(12,"TRY_PREDICT"))
        self.assertEqual(len(actions),13)
        self.assertTrue(all(a=="CENSOR_INPUT" for _,a in actions[1:]))

    def test_active_maturing_outcome_beats_old_censorship_bookkeeping(self):
        current=expected_anchor(self.plan,8)+3500
        actions=self.due(current,{7})
        self.assertEqual(actions[0],(8,"TRY_PREDICT"))
        self.assertEqual(actions[1],(7,"TRY_GRADE"))
        self.assertTrue(all(a=="CENSOR_INPUT" for _,a in actions[2:]))

    def test_exact_slot_spacing_not_arbitrary_tick_time(self):
        for i in range(SLOTS-1):
            self.assertEqual(expected_anchor(self.plan,i+1)-expected_anchor(self.plan,i),
                             SLOT_SPACING_MS)




class OriginalSourceReadDeadlineTests(unittest.TestCase):
    def setUp(self):
        from laya_cell.price_original_campaign import prediction
        self.plan=create_plan(1800000000000,PINS,PRIOR,"b"*64)
        self.anchor=expected_anchor(self.plan,0)
        feature={"anchor_ms":self.anchor,
                 "source_kind":"ORIGINAL_VERIFIED_PAPER_QUOTES",
                 "original_event_ticks_not_revised_ib_bars":True,
                 "trade_authority":False,
                 "snapshot_id":"c"*64,"asof_ms":self.anchor+2000,
                 "last_closed_bar_age_ms":2000,
                 "future_outcome_was_not_required":True,
                 "features":[0.0]*24}
        self.pred=prediction(self.plan,0,feature,[.1,.1,.1,.4,.1,.1,.1],
                             self.anchor+3000,1.2345,"d"*64,0)
        self.bars=[{"time":self.anchor//1000,"midpoint":{"close":1.2348}}]
        self.receipts=[{"bar_start_ms":self.anchor,
                        "bar_end_ms":self.anchor+HORIZON_MS,
                        "last_quote_receipt":"e"*64}]
        self.due=self.anchor+HORIZON_MS

    def poll(self,source_asof,graded_at):
        from unittest.mock import patch
        from research_laya_price_original_shadow90 import poll_due_outcome_once
        clock=[source_asof/1000,graded_at/1000]
        with patch("research_laya_price_original_shadow90.time.time",side_effect=clock),\
             patch("research_laya_price_original_shadow90.get_original_quotes",
                   return_value=(["ORIGINAL_SEALED_SOURCE"],[{"pin":"f"*64}])) as read,\
             patch("research_laya_price_original_shadow90.original_closed_bars",
                   return_value=(self.bars,self.receipts)):
            result=poll_due_outcome_once({},self.plan,0,self.pred)
            read.assert_called_once()
            self.assertEqual(read.call_args.args[1],source_asof)
            self.assertEqual(read.call_args.args[2],self.anchor)
        return result

    def test_fresh_original_source_read_within_grace_accepts(self):
        r=self.poll(self.due+1500,self.due+2500)
        self.assertEqual(r["status"],"OBSERVED")
        self.assertEqual(r["at_ms"],self.due+2500)

    def test_source_read_crossing_60_second_deadline_never_backfills(self):
        r=self.poll(self.due+INPUT_GRACE_MS-900,self.due+INPUT_GRACE_MS+1200)
        self.assertEqual(r["status"],"CENSORED")
        self.assertEqual(r["reason"],"OUTCOME_COMMITTED_DEADLINE_MISSED")

    def test_exact_endpoint_allowed_before_cutoff(self):
        r=self.poll(self.due+INPUT_GRACE_MS-1600,self.due+INPUT_GRACE_MS-600)
        self.assertEqual(r["status"],"OBSERVED")

    def test_no_fake_original_archive_quote_return_is_accepted_after_deadline(self):
        # The source reader may report a completed original bar, but its
        # timing alone cannot make a late result qualify retrospectively.
        r=self.poll(self.due+INPUT_GRACE_MS-1,self.due+INPUT_GRACE_MS+1)
        self.assertEqual(r["status"],"CENSORED")

if __name__=="__main__":unittest.main()
