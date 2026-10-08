import copy,unittest
from historical_training.common import EvidenceError
from historical_training.multiwindow import verify_child_slot
class CampaignBindingTests(unittest.TestCase):
    def fixture(self):
        a=1791457800
        parent={'anchors':[a,a+7200],'candidate_id':'fixed','candidate_sha256':'abc','registered_at_ms':(a-900)*1000}
        child={'anchor_seconds':a,'entry_seconds':a+300,'exit_seconds':a+1200,'outcome_ready_seconds':a+1500,
            'candidate_id':'fixed','candidate_sha256':'abc','registered_at_ms':(a-60)*1000}
        return parent,a,child
    def test_original_slot(self):verify_child_slot(*self.fixture())
    def test_positive_result_transplant_not_accepted(self):
        p,a,c=self.fixture()
        with self.assertRaises(EvidenceError):verify_child_slot(p,a+7200,c)
    def test_old_smoke_not_counted(self):
        p,a,c=self.fixture();c['registered_at_ms']=p['registered_at_ms']-1
        with self.assertRaises(EvidenceError):verify_child_slot(p,a,c)
    def test_target_shift_not_accepted(self):
        p,a,c=self.fixture();c['exit_seconds']+=300
        with self.assertRaises(EvidenceError):verify_child_slot(p,a,c)
    def test_candidate_substitution_not_accepted(self):
        p,a,c=self.fixture();c['candidate_sha256']='different'
        with self.assertRaises(EvidenceError):verify_child_slot(p,a,c)
    def test_hindsight_registration_not_accepted(self):
        p,a,c=self.fixture();c['registered_at_ms']=a*1000
        with self.assertRaises(EvidenceError):verify_child_slot(p,a,c)
if __name__=='__main__':unittest.main()
