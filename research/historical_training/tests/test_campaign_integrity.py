"""Adversarial checks for comparator identity, fixed subjects and claim order."""
import copy,unittest
from historical_training.common import EvidenceError
from historical_training.campaign_integrity import verify_baseline_metrics,verify_claim,validate_final_items,finite
from historical_training.multiwindow import anchors,final_assessment

class ComparatorIntegrityTests(unittest.TestCase):
    def fixture(self):
        c={'model':{'fit_prior':[.3,.3,.4]}}
        r={'label':2,'outcome_count':1,'train_prior_brier':.54,'uniform_brier':2/3,'brier':.5,'target_bps':2.}
        return c,r
    def test_recompute_frozen_prior_and_uniform(self):
        c,r=self.fixture();self.assertAlmostEqual(verify_baseline_metrics(c,r)['train_prior_brier'],.54)
    def test_favorable_prior_tamper_rejected(self):
        c,r=self.fixture();r['train_prior_brier']=1.99
        with self.assertRaisesRegex(EvidenceError,'BASELINE_SCORE'):verify_baseline_metrics(c,r)
    def test_favorable_uniform_tamper_rejected(self):
        c,r=self.fixture();r['uniform_brier']=1.98
        with self.assertRaisesRegex(EvidenceError,'BASELINE_SCORE'):verify_baseline_metrics(c,r)
    def test_nan_score_cannot_pass_abs_comparison(self):
        c,r=self.fixture();r['brier']=float('nan')
        with self.assertRaises(EvidenceError):verify_baseline_metrics(c,r)
    def test_infinite_target_rejected(self):
        c,r=self.fixture();r['target_bps']=float('inf')
        with self.assertRaises(EvidenceError):verify_baseline_metrics(c,r)
    def test_bool_label_is_not_integer_class(self):
        c,r=self.fixture();r['label']=True
        with self.assertRaises(EvidenceError):verify_baseline_metrics(c,r)
    def test_float_label_rejected(self):
        c,r=self.fixture();r['label']=2.
        with self.assertRaises(EvidenceError):verify_baseline_metrics(c,r)
    def test_bool_outcome_count_rejected(self):
        c,r=self.fixture();r['outcome_count']=True
        with self.assertRaises(EvidenceError):verify_baseline_metrics(c,r)
    def test_string_score_rejected(self):
        with self.assertRaises(EvidenceError):finite('0.4','SCORE')
    def test_wrong_prior_dimension_rejected(self):
        c,r=self.fixture();c['model']['fit_prior']=[.5,.5]
        with self.assertRaises(EvidenceError):verify_baseline_metrics(c,r)
    def test_nonunit_prior_rejected(self):
        c,r=self.fixture();c['model']['fit_prior']=[.3,.3,.3]
        with self.assertRaises(EvidenceError):verify_baseline_metrics(c,r)
    def test_negative_prior_rejected(self):
        c,r=self.fixture();c['model']['fit_prior']=[-.1,.7,.4]
        with self.assertRaises(EvidenceError):verify_baseline_metrics(c,r)
    def test_all_three_labels_have_distinct_recomputations(self):
        c,r=self.fixture()
        for k in (0,1,2):
            r['label']=k;r['train_prior_brier']=sum((p-float(i==k))**2 for i,p in enumerate(c['model']['fit_prior']))
            verify_baseline_metrics(c,r)

class ClaimIntegrityTests(unittest.TestCase):
    def fixture(self):
        p={'campaign_id':'parent','candidate_sha256':'frozen','registered_at_ms':1000}
        child={'registered_at_ms':3000};claim={'campaign_id':'parent','candidate_sha256':'frozen','anchor':10,'claimed_at_ms':2000,'resend_or_replay_permitted':False}
        return p,10,child,claim
    def test_claim_order_passes(self):verify_claim(*self.fixture())
    def test_claim_after_input_denied(self):
        p,a,c,k=self.fixture();k['claimed_at_ms']=4000
        with self.assertRaises(EvidenceError):verify_claim(p,a,c,k)
    def test_wrong_slot_denied(self):
        p,a,c,k=self.fixture();k['anchor']=11
        with self.assertRaises(EvidenceError):verify_claim(p,a,c,k)
    def test_parent_substitution_denied(self):
        p,a,c,k=self.fixture();k['campaign_id']='other'
        with self.assertRaises(EvidenceError):verify_claim(p,a,c,k)
    def test_replay_permission_denied(self):
        p,a,c,k=self.fixture();k['resend_or_replay_permitted']=True
        with self.assertRaises(EvidenceError):verify_claim(p,a,c,k)

class FinalPopulationTests(unittest.TestCase):
    def items(self):
        return [{'anchor':a,'state':'OBSERVED','label':i%3,'brier':.1,'train_prior_brier':.6,'uniform_brier':2/3} for i,a in enumerate(anchors(1791457800))]
    def test_original_24_pass(self):validate_final_items(self.items())
    def test_all_censored_remain_failed_coverage(self):
        xs=[{'anchor':x['anchor'],'state':'CENSORED'} for x in self.items()]
        self.assertFalse(final_assessment(xs)['price_review_eligible'])
    def test_duplicate_subject_cannot_increase_evidence(self):
        xs=self.items();xs[-1]=copy.deepcopy(xs[0])
        with self.assertRaisesRegex(EvidenceError,'DUPLICATE'):final_assessment(xs)
    def test_reorder_final_calendar_denied(self):
        xs=self.items();xs[0],xs[1]=xs[1],xs[0]
        with self.assertRaises(EvidenceError):final_assessment(xs)
    def test_nonnumeric_aggregate_denied(self):
        xs=self.items();xs[0]['train_prior_brier']='1'
        with self.assertRaises(EvidenceError):final_assessment(xs)
    def test_nan_aggregate_denied(self):
        xs=self.items();xs[0]['brier']=float('nan')
        with self.assertRaises(EvidenceError):final_assessment(xs)
    def test_no_interim_efficacy(self):
        with self.assertRaisesRegex(EvidenceError,'INTERIM'):final_assessment(self.items()[:2])
    def test_unchanged_positive_synthetic_gate(self):
        r=final_assessment(self.items());self.assertTrue(r['price_review_eligible']);self.assertFalse(r['economic_qualification']);self.assertFalse(r['automatic_promotion'])
if __name__=='__main__':unittest.main()
