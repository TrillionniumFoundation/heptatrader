"""Synthetic unit fixtures only; never operator OOS or vendor evidence."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from hepta_cost_capture import Capture, read_capture
from hepta_cost_policy import CostError, digest, canonical, dec
import hepta_cost_replay as replay
from hepta_cost_oos import make_plan, evaluate_plan, validate_plan, write_evidence
from test_chronological_assessment import (remote_oos_fixture,
    REMOTE_START, REMOTE_END, REMOTE_TAIL, NOW)


class OosBoundTests(unittest.TestCase):
    def assess(self, tape=None, **kwargs):
        from test_chronological_assessment import RemoteModelOosTests
        return RemoteModelOosTests().assess(tape, **kwargs)

    def test_arbitrary_ib_label_without_watch_provenance_fails(self):
        tape=remote_oos_fixture(); tape.pop('observation_context')
        result=self.assess(tape)
        self.assertFalse(result['strategy_accepted'])
        self.assertIn('REAL_WATCH_CAPTURE_PROVENANCE_REQUIRED', result['reasons'])

    def test_missing_explicit_limits_is_not_an_implicit_small_sample_pass(self):
        result=replay.chronological_assessment(remote_oos_fixture(),REMOTE_START,REMOTE_END,
                                               max_drawdown_usd='1000')
        self.assertFalse(result['strategy_accepted'])
        for reason in ('OOS_TRAINING_END_UNSET','OOS_FIXED_OUTCOME_END_UNSET',
                       'OOS_SAMPLE_BOUNDS_UNSET','OOS_TURNOVER_REENTRY_BOUNDS_UNSET'):
            self.assertIn(reason,result['reasons'])

    def test_directional_minimum_is_explicit_and_not_local_cohort_readiness(self):
        result=self.assess(min_directional_candidates=3)
        self.assertFalse(result['strategy_accepted'])
        self.assertIn('INSUFFICIENT_TIMELY_DIRECTIONAL_OOS_CANDIDATES',result['reasons'])
        self.assertNotIn('COHORT',canonical(result['reasons']))

    def test_roundtrip_minimum_is_not_one_by_default(self):
        result=self.assess(min_completed_round_trips=2)
        self.assertFalse(result['strategy_accepted'])
        self.assertTrue(any('INSUFFICIENT_COMPLETED' in x for x in result['cost_case_checks']['BASE']['reasons']))

    def test_turnover_bound_is_checked_under_every_cost_stress(self):
        result=self.assess(max_turnover_usd='1')
        self.assertFalse(result['strategy_accepted'])
        for case in result['cost_case_checks'].values():
            self.assertTrue(any('TURNOVER_BOUND_EXCEEDED' in x for x in case['reasons']))

    def test_late_directional_outcome_is_retained_not_counted_as_timely(self):
        tape=remote_oos_fixture();f=tape['rows'][1]['forecast'];tape['rows'][1]['forecast']=None
        tape['rows'][8]['forecast']=f;tape['rows'][8]['forecast_available_at_ms']=NOW+80000
        result=self.assess(tape)
        self.assertEqual(result['oos_evidence']['directional_candidates'],2)
        self.assertEqual(result['oos_evidence']['timely_observed_directional_candidates'],1)
        self.assertFalse(result['strategy_accepted'])

    def test_outcome_end_is_fixed_even_when_later_data_arrives(self):
        tape=remote_oos_fixture();a=self.assess(tape)
        tail=copy.deepcopy(tape['rows'][-1]);tail['at_ms']+=10000
        tail['quote']['observed_at_ms']+=10000;tail['quote']['stale_after_ms']+=10000
        tape['rows'].append(tail);b=self.assess(tape)
        self.assertEqual(a['outcome_end_ms'],b['outcome_end_ms'])
        self.assertEqual(canonical(a['stress']),canonical(b['stress']))

    def test_incomplete_explicit_outcome_window_cannot_pass(self):
        result=self.assess(outcome_end_ms=REMOTE_TAIL+10000)
        self.assertFalse(result['strategy_accepted'])
        self.assertIn('OUTCOME_WINDOW_NOT_COMPLETE',result['reasons'])

    def test_equal_positive_baseline_is_not_worse_and_can_pass(self):
        original=replay.remote_stress_compare
        def equal(*args,**kwargs):
            cases=original(*args,**kwargs)
            for case in cases.values():
                case['results'][0]['net_liquidation_pnl_usd']=case['results'][1]['net_liquidation_pnl_usd']
            return cases
        with patch.object(replay,'remote_stress_compare',side_effect=equal):
            result=self.assess()
        self.assertTrue(result['strategy_accepted'],result['reasons'])
        self.assertEqual(dec(result['cost_case_checks']['BASE']['paths'][0]['net_advantage_vs_direction_baseline_usd']),0)

    def test_reentry_limit_rejects_path_even_when_profitable(self):
        original=replay.remote_stress_compare
        def over(*args,**kwargs):
            cases=original(*args,**kwargs)
            for case in cases.values():
                for path in case['results'][1:]:path['open_events']=3
            return cases
        with patch.object(replay,'remote_stress_compare',side_effect=over):
            result=self.assess(max_reentries=0)
        self.assertFalse(result['strategy_accepted'])
        self.assertTrue(any('REENTRY_BOUND_EXCEEDED' in x for x in result['cost_case_checks']['BASE']['reasons']))

    def test_local_training_true_does_not_rescue_bad_remote_oos(self):
        tape=remote_oos_fixture();tape['local_training_ready']=True
        result=self.assess(tape,max_drawdown_usd='.01')
        self.assertFalse(result['strategy_accepted'])

    def test_gap_outcome_retained_and_never_bridged_as_profit(self):
        tape=remote_oos_fixture();tape['rows']=[r for r in tape['rows'] if not NOW+120000<r['at_ms']<NOW+220000]
        result=self.assess(tape)
        self.assertFalse(result['strategy_accepted'])
        self.assertTrue(any(x['reason']=='DATA_GAP_IN_FORECAST_HORIZON' for x in result['stress']['BASE']['candidate_outcomes']))


class OosPlanTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'unit-fixture.jsonl'
        self.ctx={'mode':'WATCH','broker_mutation_authorized':False,
                  'cycle_id':'obs-unitfixture-20261006','reset_sha256':'2'*64}
        self.writer=Capture(self.path,'BTC.USD',observation_context=self.ctx,min_free_bytes=0)
        self.addCleanup(self.writer.close)
        self.tape=remote_oos_fixture()
        self.writer.append('QUOTE',self.tape['rows'][0]['quote'],at_ms=NOW)
        self.prefix=read_capture(self.path)
        self.spec={'training_end_ms':NOW,'decision_start_ms':REMOTE_START,
                   'decision_end_ms':REMOTE_END,'outcome_end_ms':REMOTE_TAIL,
                   'min_directional_candidates':2,'min_completed_round_trips':1,
                   'max_drawdown_usd':'1000','max_turnover_usd':'1000000',
                   'max_reentries':10,'size':'25000','capital':'100000'}
        self.plan=make_plan(self.prefix,self.spec,cycle_id=self.ctx['cycle_id'],
                            reset_sha256=self.ctx['reset_sha256'],now_ms=NOW+1)

    def completed(self):
        for row in self.tape['rows'][1:]:
            self.writer.append('QUOTE',row['quote'],at_ms=row['at_ms'])
            if row.get('forecast'):
                self.writer.append('CANDIDATE',{'forecast':row['forecast']},at_ms=row['at_ms'])
        return read_capture(self.path)

    def test_zero_local_cohorts_valid_oos_control_flow_passes_without_fit(self):
        capture=self.completed()
        with patch.object(replay,'fit_from_tape',side_effect=AssertionError('no local fitting')):
            result=evaluate_plan(self.plan,capture)
        self.assertTrue(result['strategy_accepted'],result['reasons'])
        self.assertEqual(result['status'],'PASS')
        self.assertFalse(result['local_training_required_for_acceptance'])
        self.assertEqual(result['broker_orders_sent'],0)
        self.assertFalse(result['trading_driver_switched'])
        self.assertEqual(result['authorization_effect'],'NONE')

    def test_pending_window_is_not_pass_or_zero_trade_success(self):
        result=evaluate_plan(self.plan,self.prefix)
        self.assertEqual(result['status'],'PENDING')
        self.assertFalse(result['strategy_accepted'])

    def test_no_backdated_preregistration(self):
        with self.assertRaisesRegex(CostError,'NO_BACKDATING'):
            make_plan(self.prefix,self.spec,cycle_id=self.ctx['cycle_id'],
                      reset_sha256=self.ctx['reset_sha256'],now_ms=REMOTE_START)

    def test_future_seen_outcomes_cannot_be_registered_as_unseen_oos(self):
        with self.assertRaisesRegex(CostError,'PREFIX_AFTER_TRAINING'):
            make_plan(self.completed(),self.spec,cycle_id=self.ctx['cycle_id'],
                      reset_sha256=self.ctx['reset_sha256'],now_ms=NOW+1)

    def test_plan_tuning_breaks_manifest(self):
        plan=copy.deepcopy(self.plan);plan['spec']['max_drawdown_usd']='99999'
        with self.assertRaisesRegex(CostError,'HASH_MISMATCH'):validate_plan(plan)

    def test_rehashed_backdating_still_fails(self):
        plan=copy.deepcopy(self.plan);plan['registered_at_ms']=REMOTE_END
        plan['manifest_sha256']=digest({k:v for k,v in plan.items() if k!='manifest_sha256'})
        with self.assertRaisesRegex(CostError,'NO_BACKDATING'):validate_plan(plan)

    def test_implementation_change_cannot_reuse_original_window(self):
        plan=copy.deepcopy(self.plan);plan['implementation_sha256']['hepta_cost_replay.py']='0'*64
        plan['manifest_sha256']=digest({k:v for k,v in plan.items() if k!='manifest_sha256'})
        with self.assertRaisesRegex(CostError,'IMPLEMENTATION_OR_POLICY_CHANGED'):validate_plan(plan)

    def test_changed_watch_identity_fails(self):
        capture=self.completed();capture['events'][0]['data']['observation_context']['session_id']='other'
        with self.assertRaisesRegex(CostError,'IDENTITY_CHANGED'):evaluate_plan(self.plan,capture)

    def test_changed_registered_prefix_fails(self):
        capture=self.completed();capture['events'][1]['sha256']='0'*64
        with self.assertRaisesRegex(CostError,'PREFIX_OR_WATCH_IDENTITY_CHANGED'):evaluate_plan(self.plan,capture)

    def test_profitable_synthetic_capture_never_passes(self):
        capture=self.completed();capture['source_kind']='SYNTHETIC'
        with self.assertRaisesRegex(CostError,'REAL_WATCH_SCOPE_REQUIRED'):evaluate_plan(self.plan,capture)

    def test_wrong_cycle_and_reset_cannot_be_used(self):
        for field,value in [('cycle_id','obs-different-cycle'),('reset_sha256','3'*64)]:
            args={'cycle_id':self.ctx['cycle_id'],'reset_sha256':self.ctx['reset_sha256']}
            args[field]=value
            with self.assertRaisesRegex(CostError,'REAL_WATCH_SCOPE_REQUIRED'):
                make_plan(self.prefix,self.spec,now_ms=NOW+1,**args)

    def test_plan_and_evidence_files_are_never_overwritten(self):
        path=Path(self.tmp.name)/'plan.json';write_evidence(path,self.plan)
        before=path.read_bytes()
        with self.assertRaises(FileExistsError):write_evidence(path,{'replacement':True})
        self.assertEqual(before,path.read_bytes())
        self.assertEqual(path.stat().st_mode&0o777,0o600)
