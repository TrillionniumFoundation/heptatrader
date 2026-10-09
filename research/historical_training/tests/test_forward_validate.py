import copy,unittest
from historical_training.common import EvidenceError
from historical_training.forward_validate import custody

class CustodyTests(unittest.TestCase):
    def fixture(self):
        t=1791451200
        plan={'anchor_seconds':t,'entry_seconds':t+300,'exit_seconds':t+1200,'outcome_ready_seconds':t+1500,'registered_at_ms':(t-100)*1000}
        c={'created_at_ms':(t-200)*1000,'trade_authority':False}
        p={'available_at_ms':(t+30)*1000,'target_entry_ms':(t+300)*1000,'target_exit_ms':(t+1200)*1000,'action':'ABSTAIN','trade_authority':False,'probabilities':[.4,.2,.4]}
        r={'outcome_count':1,'state':'OBSERVED_ONE_FORWARD_PRICE_OUTCOME_NOT_EFFICACY','ended_at_ms':(t+1505)*1000,'automatic_promotion':False,'outbound_message_counts':{'20':4,'71':1},'broker_orders_sent':0}
        return plan,c,p,r
    def test_positive_fixed_custody(self):
        r=custody(*self.fixture());self.assertTrue(r['forecast_before_entry']);self.assertFalse(r['independent_statistical_efficacy'])
    def test_no_retroactive_plan(self):
        plan,c,p,r=self.fixture();plan['registered_at_ms']=(plan['anchor_seconds']+1)*1000
        with self.assertRaises(EvidenceError):custody(plan,c,p,r)
    def test_no_late_prediction(self):
        plan,c,p,r=self.fixture();p['available_at_ms']=p['target_entry_ms']
        with self.assertRaises(EvidenceError):custody(plan,c,p,r)
    def test_no_early_label(self):
        plan,c,p,r=self.fixture();r['ended_at_ms']=(plan['outcome_ready_seconds']-1)*1000
        with self.assertRaises(EvidenceError):custody(plan,c,p,r)
    def test_extra_query_denied(self):
        plan,c,p,r=self.fixture();r['outbound_message_counts']['20']=5
        with self.assertRaises(EvidenceError):custody(plan,c,p,r)
    def test_false_promotion_claim_denied(self):
        plan,c,p,r=self.fixture();r['automatic_promotion']=True
        with self.assertRaises(EvidenceError):custody(plan,c,p,r)
    def test_missing_outcome_not_success(self):
        plan,c,p,r=self.fixture();r['outcome_count']=0
        with self.assertRaises(EvidenceError):custody(plan,c,p,r)
    def test_target_cannot_shift_after_forecast(self):
        plan,c,p,r=self.fixture();p['target_exit_ms']+=300000
        with self.assertRaises(EvidenceError):custody(plan,c,p,r)
    def test_trade_action_cannot_hide_in_readonly_proof(self):
        plan,c,p,r=self.fixture();p['action']='BUY'
        with self.assertRaises(EvidenceError):custody(plan,c,p,r)
if __name__=='__main__':unittest.main()
