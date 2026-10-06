import copy
import unittest
from unittest.mock import patch
from dataclasses import replace
from fixtures import *
import hepta_cost_replay as replay
from hepta_cost_replay import chronological_assessment,local_training_assessment,fit_from_tape,candidate_outcomes

# Full original 30-sample/two-UTC-date limits also apply to synthetic tests.
# Short sessions straddle midnight; no production minimum is weakened.
TEST_POLICY=DEFAULT_POLICY
DAY=NOW//86400000*86400000
START=DAY-86400000+(22*3600+40*60)*1000
CUT=START+30*430000
HOLDOUT=DAY+(21*3600+50*60)*1000
END=HOLDOUT+31*430000+1000
EPISODE_ROWS=43

def tape_fixture():
    rows=[];base=D('100')
    starts=[START+i*430000 for i in range(30)]+[HOLDOUT+i*430000 for i in range(33)]
    for start in starts:
        for sec in range(0,430,10):
            at=start+sec*1000
            mid=base*(1+D(min(sec,300))*D('.013')/D(300))
            q=quote(at,mid=str(mid))
            f=forecast(at,move='120',mid=str(mid)) if sec==0 else None
            rows.append({'at_ms':at,'quote':q,'forecast':f})
        base*=D('1.013')
    return {'schema':'heptatrader.cost-tape.v1','instrument':'BTC.USD','source_kind':'SYNTHETIC','rows':rows}


def assess(tape=None,**kw):
    # Historical 30x2 cohort tests now exercise LOCAL training readiness only.
    return local_training_assessment(tape or tape_fixture(),CUT,END,policy=TEST_POLICY,
                                     max_drawdown_usd=kw.pop('max_drawdown_usd','1000'),**kw)

class ChronologicalAssessmentTests(unittest.TestCase):
    def test_synthetic_positive_paths_do_not_become_real_acceptance(self):
        result=assess()
        self.assertEqual(result['reasons'],['SYNTHETIC_IS_NOT_REAL_HOLDOUT'])
        self.assertFalse(result['research_acceptance_passed'])
        self.assertTrue(all(x['passed'] for x in result['cost_case_checks'].values()))
        self.assertFalse(result['calibration_activated']);self.assertFalse(result['trading_driver_switched'])
        self.assertEqual(result['authorization_effect'],'NONE');self.assertEqual(result['broker_orders_sent'],0)
    def test_training_uses_only_labels_known_before_cutoff(self):
        result=assess()
        cohort=next(iter(result['training']['artifact']['cohorts'].values()))
        self.assertEqual(cohort['count'],30)
        self.assertTrue(all(x['forecast']['issued_at_ms']<CUT for x in result['training']['candidate_outcomes']))
    def test_holdout_changes_never_refit_the_training_artifact(self):
        before=tape_fixture();after=copy.deepcopy(before)
        for row in after['rows']:
            if row['at_ms']>CUT:row['quote']['bid']=str(dec(row['quote']['bid'])*D('.999'))
        self.assertEqual(assess(before)['training']['artifact'],assess(after)['training']['artifact'])
    def test_future_labels_never_complete_a_training_window(self):
        result=local_training_assessment(tape_fixture(),START+29*430000+150000,END,policy=TEST_POLICY,max_drawdown_usd='1000')
        self.assertEqual(result['training']['artifact']['cohorts'],{})
        self.assertIn('NO_QUALIFIED_TRAINING_COHORT',result['reasons'])
    def test_decision_tail_is_quotes_only_not_new_orders(self):
        result=assess()
        self.assertTrue(any(x['reason']=='OUTSIDE_PREDECLARED_DECISION_WINDOW' for x in result['purged_or_outside_window_candidates']))
        for case in result['stress'].values():
            for path in case['results']:
                self.assertTrue(all(x['at_ms']<=END for x in path['trades'] if x['event']=='OPEN'))
                self.assertEqual(path['completed_round_trips'],32)
    def test_old_forecast_released_in_holdout_is_purged(self):
        tape=tape_fixture()
        f=tape['rows'][29*EPISODE_ROWS]['forecast'];tape['rows'][29*EPISODE_ROWS]['forecast']=None
        tape['rows'][30*EPISODE_ROWS]['forecast']=f;tape['rows'][30*EPISODE_ROWS]['forecast_available_at_ms']=tape['rows'][30*EPISODE_ROWS]['at_ms']
        result=assess(tape)
        self.assertTrue(any(x['reason']=='FORECAST_STRADDLES_TRAIN_HOLDOUT_BOUNDARY' for x in result['purged_or_outside_window_candidates']))
    def test_zero_trade_paths_do_not_pass(self):
        tape=tape_fixture()
        for row in tape['rows']:
            if row['at_ms']>CUT and row['forecast']:
                row['forecast']=forecast(row['at_ms'],action='HOLD',move='0',mid=row['quote']['bid'])
        result=assess(tape)
        self.assertIn('NO_DIRECTIONAL_HOLDOUT_CANDIDATES',result['reasons'])
        self.assertFalse(result['research_acceptance_passed'])
        self.assertFalse(any(x['passed'] for x in result['cost_case_checks'].values()))
    def test_missing_outcome_tail_is_not_filled(self):
        tape=tape_fixture();tape['rows']=[x for x in tape['rows'] if x['at_ms']<HOLDOUT+31*430000+150000]
        result=assess(tape)
        self.assertIn('INCOMPLETE_DIRECTIONAL_HOLDOUT_OUTCOMES',result['reasons'])
        self.assertTrue(any('OPEN_POSITION' in reason for reason in result['cost_case_checks']['BASE']['reasons']))
    def test_gap_while_holding_keeps_net_result_unknown(self):
        tape=tape_fixture();tape['rows']=[x for x in tape['rows'] if not HOLDOUT+50000<x['at_ms']<HOLDOUT+120000]
        result=assess(tape)
        self.assertTrue(any('PATH_INCOMPLETE' in x for x in result['cost_case_checks']['BASE']['reasons']))
        self.assertIsNone(result['stress']['BASE']['results'][1]['net_liquidation_pnl_usd'])
    def test_budget_is_explicit_not_invented(self):
        result=assess(max_drawdown_usd=None)
        self.assertIn('OPERATOR_DRAWDOWN_BUDGET_UNSET',result['reasons'])
        self.assertIsNone(result['operator_max_drawdown_usd'])
    def test_exceeded_drawdown_budget_is_not_a_pass(self):
        result=assess(max_drawdown_usd='.01')
        self.assertTrue(any('DRAWDOWN_BUDGET' in x for x in result['cost_case_checks']['BASE']['reasons']))
    def test_explicit_boundaries_and_types(self):
        for a,b in [(START,END),(CUT,CUT),(CUT,HOLDOUT+99999999),(True,END),(CUT,False)]:
            with self.subTest(a=a,b=b),self.assertRaises(CostError):
                local_training_assessment(tape_fixture(),a,b)
    def test_input_and_model_guesses_unchanged(self):
        tape=tape_fixture();before=canonical(tape);a=assess(tape);b=assess(tape)
        self.assertEqual(canonical(tape),before);self.assertEqual(canonical(a),canonical(b))
    def test_late_candidate_excluded_not_entire_fit_aborted(self):
        tape=tape_fixture();first=tape['rows'][0]['forecast'];tape['rows'][0]['forecast']=None
        tape['rows'][7]['forecast']=first;tape['rows'][7]['forecast_available_at_ms']=START+70000
        result=fit_from_tape(tape,CUT,policy=TEST_POLICY)
        self.assertEqual(result['sample_rows_usable_before_purging'],29)
        self.assertEqual(result['candidate_outcomes'][0]['reason'],'OBSERVED')
        self.assertTrue(any(x['reason']=='FORECAST_TOO_LATE_FOR_CALIBRATION' for x in result['calibration_sample_exclusions']))
    def test_hold_outcome_retained_but_never_directional_training(self):
        tape=tape_fixture();tape['rows'][0]['forecast']=forecast(START,action='HOLD',move='0')
        result=fit_from_tape(tape,CUT,policy=TEST_POLICY)
        self.assertEqual(result['sample_rows_usable_before_purging'],29)
        self.assertEqual(result['candidate_outcomes'][0]['reason'],'OBSERVED')
        self.assertTrue(any(x['reason'].startswith('HOLD_') for x in result['calibration_sample_exclusions']))


# Synthetic unit fixture. IB_CAPTURE here tests control flow only; no test
# result is installed or represented as real operator evidence.
REMOTE_START=NOW+5000
REMOTE_END=NOW+100000
REMOTE_TAIL=NOW+500000

def remote_oos_fixture(source_kind='IB_CAPTURE'):
    rows=[]
    for sec in range(0,501,10):
        at=NOW+sec*1000
        if sec < 20:
            mid=D('100')
        elif sec < 60:
            mid=D('99.6')
        elif sec <= 360:
            mid=D('99.6')*(D(1)+D('.05')*D(sec-60)/D(300))
        else:
            mid=D('104.58')
        f=None
        if sec==10:
            f=forecast(at,action='BUY',move='20',mid=str(mid))
        elif sec==60:
            f=forecast(at,action='BUY',move='500',mid=str(mid))
        rows.append({'at_ms':at,'quote':quote(at,mid=str(mid)),'forecast':f})
    return {'schema':'heptatrader.cost-tape.v1','instrument':'BTC.USD',
            'source_kind':source_kind,'rows':rows}

class RemoteModelOosTests(unittest.TestCase):
    def assess(self,tape=None,**kwargs):
        return chronological_assessment(
            tape or remote_oos_fixture(),REMOTE_START,REMOTE_END,
            max_drawdown_usd=kwargs.pop('max_drawdown_usd','1000'),**kwargs)

    def test_remote_oos_never_refits_or_reads_training_cohorts(self):
        with patch.object(replay,'fit_from_tape',side_effect=AssertionError('remote OOS must not fit')):
            result=self.assess()
        self.assertTrue(result['strategy_accepted'])
        self.assertFalse(result['remote_model_refit_performed'])
        self.assertFalse(result['calibration_artifact_used'])
        self.assertFalse(result['local_training_required_for_acceptance'])
        self.assertIsNone(result['local_training_ready'])
        self.assertNotIn('NO_QUALIFIED_TRAINING_COHORT',result['reasons'])
        self.assertNotIn('NO_TRAINED_COHORT',canonical(result))

    def test_remote_oos_pass_requires_positive_net_advantage_under_every_stress(self):
        result=self.assess()
        self.assertTrue(result['strategy_accepted'])
        self.assertEqual(result['oos_evidence']['directional_candidates'],2)
        self.assertEqual(result['oos_evidence']['observed_directional_candidates'],2)
        for case in result['cost_case_checks'].values():
            self.assertTrue(case['passed'],case)
            for path in case['paths']:
                self.assertGreater(path['completed_round_trips'],0)
                self.assertGreater(dec(path['net_liquidation_pnl_usd']),0)
                self.assertGreater(dec(path['net_advantage_vs_direction_baseline_usd']),0)

    def test_remote_cost_gate_rejects_small_raw_edge_but_keeps_larger_candidate(self):
        result=self.assess()
        base=result['stress']['BASE']
        gate=base['results'][1]
        decisions=[d for d in gate['decisions'] if d.get('evidence')]
        self.assertEqual(decisions[0]['action'],'REJECT')
        self.assertIn('NET_EDGE_BELOW_COST_HURDLE',decisions[0]['reason'])
        self.assertEqual(decisions[1]['action'],'OPEN')
        self.assertEqual(decisions[1]['evidence']['forecast_basis'],
                         'REMOTE_MODEL_RAW_POSITION_NEUTRAL_SCENARIOS')
        self.assertFalse(decisions[1]['evidence']['local_training_artifact_used'])

    def test_remote_oos_is_fixed_time_and_outcome_tail_cannot_start_new_entry(self):
        tape=remote_oos_fixture()
        late=forecast(NOW+200000,action='BUY',move='500',mid='102')
        tape['rows'][20]['forecast']=late
        result=self.assess(tape)
        self.assertTrue(any(x['reason']=='OUTSIDE_PREDECLARED_DECISION_WINDOW'
                            for x in result['purged_or_outside_window_candidates']))
        for case in result['stress'].values():
            for path in case['results']:
                self.assertTrue(all(t['at_ms']<=REMOTE_END for t in path['trades']
                                    if t['event']=='OPEN'))

    def test_synthetic_marker_never_becomes_strategy_acceptance(self):
        result=self.assess(remote_oos_fixture('SYNTHETIC'))
        self.assertFalse(result['strategy_accepted'])
        self.assertIn('SYNTHETIC_IS_NOT_REAL_OOS',result['reasons'])

    def test_missing_directional_outcome_blocks_strategy_not_local_training(self):
        tape=remote_oos_fixture();tape['rows']=tape['rows'][:25]
        result=chronological_assessment(
            tape,REMOTE_START,REMOTE_END,max_drawdown_usd='1000')
        self.assertFalse(result['strategy_accepted'])
        self.assertIn('INCOMPLETE_DIRECTIONAL_OOS_OUTCOMES',result['reasons'])
        self.assertNotIn('NO_QUALIFIED_TRAINING_COHORT',result['reasons'])

    def test_no_trade_cannot_pass_by_avoiding_fees(self):
        tape=remote_oos_fixture()
        for row in tape['rows']:
            if row['forecast'] and row['at_ms']>REMOTE_START:
                row['forecast']=forecast(row['at_ms'],action='HOLD',move='0',
                                         mid=row['quote']['bid'])
        result=self.assess(tape)
        self.assertFalse(result['strategy_accepted'])
        self.assertIn('NO_DIRECTIONAL_OOS_CANDIDATES',result['reasons'])
        self.assertTrue(any('NO_COMPLETED_OOS_ROUNDTRIP' in reason
                            for case in result['cost_case_checks'].values()
                            for reason in case['reasons']))

    def test_explicit_drawdown_budget_is_still_required(self):
        result=self.assess(max_drawdown_usd=None)
        self.assertFalse(result['strategy_accepted'])
        self.assertIn('OPERATOR_DRAWDOWN_BUDGET_UNSET',result['reasons'])

    def test_remote_oos_input_is_not_mutated(self):
        tape=remote_oos_fixture();before=canonical(tape)
        a=self.assess(tape);b=self.assess(tape)
        self.assertEqual(canonical(tape),before)
        self.assertEqual(canonical(a),canonical(b))
