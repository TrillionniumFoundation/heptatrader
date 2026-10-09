import json,unittest
from historical_training.common import EvidenceError
from historical_training.matched_audit import verify_role_order
class MatchedAuditRoleTests(unittest.TestCase):
    def fixture(self):
        return {name:[{'history_start':i*100,'outcome_end':i*100+50}]
                for i,name in enumerate(('fit','tune','calibration','check'))}
    def test_canonical_json_does_not_reorder_time_roles(self):
        value=json.loads(json.dumps(self.fixture(),sort_keys=True))
        self.assertEqual(list(value),['calibration','check','fit','tune'])
        verify_role_order(value)
    def test_overlapping_roles_fail(self):
        value=self.fixture();value['tune'][0]['history_start']=49
        with self.assertRaisesRegex(EvidenceError,'OVERLAP'):verify_role_order(value)
    def test_missing_role_fails(self):
        value=self.fixture();del value['calibration']
        with self.assertRaisesRegex(EvidenceError,'ROLE_SET'):verify_role_order(value)
    def test_test_group_never_added(self):
        value=self.fixture();value['test']=value['check']
        with self.assertRaisesRegex(EvidenceError,'ROLE_SET'):verify_role_order(value)
if __name__=='__main__':unittest.main()
