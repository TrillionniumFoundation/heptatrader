import copy
import unittest
from laya_cell.integrity import ContractError, digest
from laya_cell import resident_training_v2 as v2
from laya_cell.resident_row_accounting import row_id, select_rows
from laya_cell.resident_evaluate import measure

BASE=1800000000000


def row(i,h=300,step=60000):
    return {'sample_id':digest(['sample',i]),'horizon_sec':h,
            'anchor_ms':BASE+i*step,'target_class':i%7,
            'outcome_receipt_digest':digest(['outcome',i,h]),
            'outcome_available_at_ms':BASE+i*step+h*1000+1}


def population(n=120):
    return [(row_id(r),r) for i in range(n) for h in (300,900) for r in [row(i,h)]]


class SamplingTests(unittest.TestCase):
    def test_legacy_latest32_semantics_unchanged(self):
        rows=population()
        self.assertEqual(select_rows(rows),rows[-32:])

    def test_new_selection_covers_full_available_time_and_horizons(self):
        pool=population();chosen=v2.select_covered_rows(pool)
        self.assertEqual(len(chosen),32)
        self.assertEqual(len({k for k,_ in chosen}),32)
        for h in (300,900):
            selected=[r for _,r in chosen if r['horizon_sec']==h]
            self.assertEqual(len(selected),16)
            self.assertEqual(min(r['anchor_ms'] for r in selected),BASE)
            self.assertEqual(max(r['anchor_ms'] for r in selected),BASE+119*60000)
        self.assertEqual(chosen,v2.select_covered_rows(list(reversed(pool))))

    def test_labels_and_scores_do_not_select_favorable_rows(self):
        pool=population();first=[k for k,_ in v2.select_covered_rows(pool)]
        changed=[(k,{**r,'target_class':6-r['target_class'],'price_loss':999}) for k,r in pool]
        self.assertEqual(first,[k for k,_ in v2.select_covered_rows(changed)])

    def test_sparse_horizon_does_not_waste_batch_or_duplicate_rows(self):
        pool=[(row_id(r),r) for i in range(60) for r in [row(i)]]
        r=row(1,900);pool.append((row_id(r),r))
        chosen=v2.select_covered_rows(pool)
        self.assertEqual(len(chosen),32)
        self.assertEqual(sum(r['horizon_sec']==900 for _,r in chosen),1)

    def test_short_source_does_not_fabricate_longer_coverage(self):
        pool=[(row_id(r),r) for i in range(16) for h in (300,900) for r in [row(i,h,10000)]]
        chosen=v2.select_covered_rows(pool)
        self.assertEqual({k for k,_ in chosen},{k for k,_ in pool})
        report=v2.coverage([r for _,r in chosen],BASE+200000000)
        self.assertEqual(report['anchor_span_ms'],150000)
        self.assertEqual(report['maximum_nonoverlapping_target_intervals'],1)
        self.assertEqual(report['horizon_counts'],{'300':16,'900':16})
        self.assertTrue(report['nonoverlap_is_not_statistical_independence'])

    def test_duplicate_or_replaced_identity_rejected(self):
        rows=population()
        with self.assertRaises(ContractError):v2.select_covered_rows(rows+[rows[0]])
        with self.assertRaises(ContractError):v2.select_covered_rows([('a'*64,rows[0][1])])

    def test_stats_report_actual_old_label_age(self):
        r=row(0)
        cutoff=r['outcome_available_at_ms']+39*3600000
        result=v2.coverage([r],cutoff)
        self.assertEqual(result['newest_label_age_at_cutoff_ms'],39*3600000)
        self.assertEqual(result['last_outcome_available_ms'],r['outcome_available_at_ms'])
        with self.assertRaises(ContractError):v2.coverage([r],r['outcome_available_at_ms']-1)

    def test_original_outcome_clock_attached_without_mutation(self):
        outcome={'observed_at_ms':BASE+300000,'available_at_ms':BASE+300123}
        source={'samples':[{'sample_id':'a'*64,'outcome_receipts':[outcome,None]}]}
        r={**row(0),'sample_id':'a'*64,'outcome_receipt_digest':digest(outcome)}
        before=copy.deepcopy(source)
        result=v2.timed_rows([r],source)
        self.assertEqual(result[0]['outcome_available_at_ms'],BASE+300123)
        self.assertEqual(source,before)
        with self.assertRaises(ContractError):v2.timed_rows([{**r,'outcome_receipt_digest':'b'*64}],source)


class ActionOutcomeTests(unittest.TestCase):
    def fixture(self,new=True):
        p={'candidate_id':'c','plan_id':'p'}
        if new:p.update(action_output_profile=v2.ACTION_OUTPUT,action_training_profile_id=None,
                        action_optimizer_admitted=False)
        inp={'original':'shared'}
        pred={'candidate_id':'c','plan_id':'p','slot':0,'anchor_ms':BASE,
              'created_at_ms':BASE+1,'trained_until_ms':BASE-1,
              'input':inp,'input_id':digest(inp),'reference_mid':1.,
              'candidate_probs':[1/7]*7,'parent_probs':[1/7]*7,
              'candidate_act_escalate_probs':None if new else [1.,0.],
              'bootstrap_point_bps':0.}
        if new:pred.update(action_output_profile=v2.ACTION_OUTPUT,action_inference={
            'frozen_profile_id':None,'action_correct_probs':None,'model_or_trade_permission':False})
        quotes=[{'t':BASE+i*10000,'mid':1.+i*0.000001,'receipt':'r'} for i in range(31)]
        return p,pred,quotes

    def test_legacy_action_scoring_unchanged(self):
        p,pred,q=self.fixture(False);out=measure(p,0,pred,q,BASE+300001)
        self.assertEqual(out['status'],'OBSERVED')
        self.assertEqual(out['action_head_brier'],2.)

    def test_unsupported_action_not_fake_perfect_or_probability(self):
        p,pred,q=self.fixture();out=measure(p,0,pred,q,BASE+300001)
        self.assertEqual(out['status'],'OBSERVED')
        self.assertIsNone(out['action_head_brier'])
        self.assertAlmostEqual(out['candidate_brier'],6/7)
        pred['candidate_act_escalate_probs']=[0.,1.]
        with self.assertRaises(ContractError):measure(p,0,pred,q,BASE+300001)

    def test_probability_cannot_claim_missing_training_support(self):
        p,pred,q=self.fixture()
        pred['action_inference']['action_correct_probs']=[.8]
        pred['candidate_act_escalate_probs']=[.8,1-.8]
        with self.assertRaisesRegex(ContractError,'UNSUPPORTED_ACTION_PROBABILITY'):
            measure(p,0,pred,q,BASE+300001)

    def test_temperature_output_id_and_probability_must_agree(self):
        p,pred,q=self.fixture();p['action_training_profile_id']='f'*64
        p['action_optimizer_admitted']=True
        pred['action_inference'].update(frozen_profile_id='f'*64,action_correct_probs=[.8])
        pred['candidate_act_escalate_probs']=[.8,1-.8]
        out=measure(p,0,pred,q,BASE+300001)
        self.assertAlmostEqual(out['action_head_brier'],1.28)
        pred['action_inference']['frozen_profile_id']='g'*64
        with self.assertRaises(ContractError):measure(p,0,pred,q,BASE+300001)



from laya_cell import resident_training_v2 as v2

class ActionObservationTests(unittest.TestCase):
    def fixture(self, admitted=False):
        profile='f'*64 if admitted else None
        plan={'action_output_profile':v2.ACTION_OUTPUT,
              'action_training_profile_id':profile,'action_optimizer_admitted':admitted}
        prediction={'action_output_profile':v2.ACTION_OUTPUT,'candidate_act_escalate_probs':None,
                    'action_inference':{'frozen_profile_id':profile,'action_correct_probs':None,
                                        'model_or_trade_permission':False}}
        return plan,prediction
    def test_legacy_missing_score_is_corruption_not_abstention(self):
        with self.assertRaises(ContractError):
            v2.action_observation({}, {'candidate_act_escalate_probs':[1.,0.]},
                parent_correct=False,outcome={'action_head_brier':None})
    def test_no_training_support_is_explicitly_missing_action_only(self):
        plan,prediction=self.fixture()
        report=v2.action_observation(plan,prediction,parent_correct=False,
                                     outcome={'action_head_brier':None})
        self.assertEqual(report['abstention_reason'],'NO_BALANCED_ACTION_TRAINING')
        self.assertIsNone(report['action_brier'])
        self.assertIsNone(report['action_correct_probability'])
        with self.assertRaises(ContractError):
            v2.action_observation(plan,prediction,parent_correct=False,outcome={'action_head_brier':0.})
    def test_missing_score_key_cannot_impersonate_explicit_null(self):
        plan,prediction=self.fixture()
        with self.assertRaises(ContractError):
            v2.action_observation(plan,prediction,parent_correct=True,outcome={})
    def test_supported_ood_abstains_but_does_not_invent_correctness(self):
        plan,prediction=self.fixture(True)
        prediction['action_inference']['action_correct_probs']=[None]
        for parent_correct in (True,False):
            report=v2.action_observation(plan,prediction,parent_correct=parent_correct,
                                         outcome={'action_head_brier':None})
            self.assertEqual(report['abstention_reason'],'ACTION_OOD_ABSTENTION')
            self.assertIsNone(report['action_brier'])
    def test_supported_frozen_probability_keeps_actual_parent_target(self):
        plan,prediction=self.fixture(True)
        prediction['action_inference']['action_correct_probs']=[.8]
        prediction['candidate_act_escalate_probs']=[.8,1-.8]
        report=v2.action_observation(plan,prediction,parent_correct=False,outcome={'action_head_brier':1.28})
        self.assertAlmostEqual(report['action_brier'],1.28)
        self.assertIsNone(report['abstention_reason'])
        with self.assertRaises(ContractError):
            v2.action_observation(plan,prediction,parent_correct=False,outcome={'action_head_brier':None})
    def test_unsupported_probability_and_substituted_profile_rejected(self):
        plan,prediction=self.fixture()
        prediction['candidate_act_escalate_probs']=[0.,1.]
        with self.assertRaises(ContractError):v2.action_observation(plan,prediction,parent_correct=False)
        plan,prediction=self.fixture(True)
        prediction['action_inference']['frozen_profile_id']='g'*64
        with self.assertRaises(ContractError):v2.action_observation(plan,prediction,parent_correct=False)

if __name__=='__main__':unittest.main()
