import copy,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
from historical_training.common import EvidenceError,digest
from historical_training.multiwindow import anchors,validate_plan,slot_action,final_assessment,CANDIDATE,CANDIDATE_SHA
FIRST=1791458100 # UTC 2026-10-08 11:15, full-span spacing retained.

def plan():
    p={'anchors':anchors(FIRST),'registered_at_ms':(FIRST-300)*1000,'candidate_id':CANDIDATE,'candidate_sha256':CANDIDATE_SHA,
       'trade_authority':False,'automatic_promotion':False,'action_policy':'ABSTAIN','reuse_previous_smoke':False}
    p['campaign_id']=digest(p);return p

def seal(p):p['campaign_id']=digest({k:v for k,v in p.items() if k!='campaign_id'});return p

class MultiwindowTests(unittest.TestCase):
    def test_six_weekdays_fixed_slots(self):
        p=plan();self.assertEqual(len(validate_plan(p)),24)
        self.assertTrue(all(a+1500<b-3600 for a,b in zip(p['anchors'],p['anchors'][1:])))
    def test_unaligned_anchor_refused(self):
        with self.assertRaises(EvidenceError):anchors(FIRST+1)
    def test_retrospective_plan_refused(self):
        p=plan();p['registered_at_ms']=FIRST*1000
        with self.assertRaises(EvidenceError):validate_plan(seal(p))
    def test_mutated_candidate_refused(self):
        p=plan();p['candidate_sha256']='0'*64
        with self.assertRaises(EvidenceError):validate_plan(seal(p))
    def test_reusing_old_smoke_refused(self):
        p=plan();p['reuse_previous_smoke']=True
        with self.assertRaises(EvidenceError):validate_plan(seal(p))
    def test_slot_shift_refused(self):
        p=plan();p['anchors'][1]+=300
        with self.assertRaises(EvidenceError):validate_plan(seal(p))
    def test_no_interim_statistics(self):
        with self.assertRaises(EvidenceError):final_assessment([])
    def test_missing_data_cannot_be_zero_loss(self):
        p=plan();items=[{'anchor':a,'state':'CENSORED'} for a in p['anchors']]
        r=final_assessment(items);self.assertEqual(r['censored'],24);self.assertFalse(r['price_review_eligible'])
    def test_low_count_never_passes(self):
        items=[{'anchor':a,'state':'CENSORED'} for a in plan()['anchors']]
        for i in range(5):items[i].update(state='OBSERVED',brier=.1,train_prior_brier=.6,uniform_brier=2/3,label=i%3)
        self.assertFalse(final_assessment(items)['price_review_eligible'])
    def test_positive_full_synthetic_data_review_only(self):
        items=[{'anchor':a,'state':'OBSERVED','brier':.1,'train_prior_brier':.6,'uniform_brier':2/3,'label':i%3} for i,a in enumerate(plan()['anchors'])]
        r=final_assessment(items);self.assertTrue(r['price_review_eligible']);self.assertFalse(r['automatic_promotion']);self.assertFalse(r['economic_qualification'])
    def test_future_does_not_start(self):self.assertEqual(slot_action(FIRST,FIRST-91,False,False),'FUTURE')
    def test_due_starts_once(self):self.assertEqual(slot_action(FIRST,FIRST-60,False,False),'RUN_ONCE')
    def test_restart_does_not_replay_claimed_slot(self):self.assertEqual(slot_action(FIRST,FIRST-60,True,False),'CENSOR_INTERRUPTED_NO_REPLAY')
    def test_late_wakeup_is_not_rescheduled(self):self.assertEqual(slot_action(FIRST,FIRST,False,False),'CENSOR_MISSED_WAKE_NO_REPLAY')
    def test_final_not_repeated(self):self.assertEqual(slot_action(FIRST,FIRST+2000,True,True),'FINAL')

if __name__=='__main__':unittest.main()
