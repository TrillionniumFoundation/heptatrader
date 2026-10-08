import copy,unittest
from historical_training.common import EvidenceError,digest
from historical_training.matched_completion_audit import trace,audit_policies

class PolicyCompletionTests(unittest.TestCase):
    def fixture(self):
        def rows(n,start):
            return [{'time':start+i*1800,'entry_at':start+i*1800+300,'exit_at':start+i*1800+1200,
                     'outcome_end':start+i*1800+1500,'long_after_spread_bps':2.,'short_after_spread_bps':-2.} for i in range(n)]
        ca=rows(35,10000);ch=rows(40,100000);cv=[[1.2,-1] for _ in ca];hv=[[.8,-1] for _ in ch];grid=[0.,.5,1.,2.]
        policies=[trace(ca,cv,b) for b in grid];best=policies[0];expected=trace(ch,hv,0.)
        identity=digest([(r['time'],r['side']) for r in expected['details']])
        record={'calibration_policies':policies,'admitted_policy':True,'selected_buffer':0.,
            'check_selected_or_diagnostic_zero_buffer':expected,'economic_qualification':False,'no_trade_net_usd':0.,
            'fixed_trade_stress':[{'extra_bps':e,'trades':40,'net_usd':expected['net_usd']-40*10*e,
                'same_time_side_sha256':identity} for e in grid]}
        return record,ca,cv,ch,hv,grid
    def test_complete_original_selection_reconstructs(self):
        r=audit_policies(*self.fixture());self.assertTrue(r['admitted_policy']);self.assertEqual(r['check_trades'],40)
    def test_omitted_losing_trade_cannot_hide(self):
        r,*args=self.fixture();r['check_selected_or_diagnostic_zero_buffer']['details'].pop()
        with self.assertRaises(EvidenceError):audit_policies(r,*args)
    def test_changed_side_rejected(self):
        r,*args=self.fixture();r['check_selected_or_diagnostic_zero_buffer']['details'][0]['side']='SELL'
        with self.assertRaises(EvidenceError):audit_policies(r,*args)
    def test_changed_entry_time_rejected(self):
        r,*args=self.fixture();r['check_selected_or_diagnostic_zero_buffer']['details'][0]['entry_at']+=1
        with self.assertRaises(EvidenceError):audit_policies(r,*args)
    def test_admission_boolean_cannot_be_forged(self):
        r,*args=self.fixture();r['admitted_policy']=False
        with self.assertRaises(EvidenceError):audit_policies(r,*args)
    def test_check_selected_threshold_cannot_change(self):
        r,*args=self.fixture();r['selected_buffer']=.5
        with self.assertRaises(EvidenceError):audit_policies(r,*args)
    def test_original_calibration_option_must_be_present(self):
        r,*args=self.fixture();r['calibration_policies'].pop()
        with self.assertRaises(EvidenceError):audit_policies(r,*args)
    def test_no_cost_reselection_in_fixed_trade_stress(self):
        r,*args=self.fixture();r['fixed_trade_stress'][1]['trades']=10
        with self.assertRaises(EvidenceError):audit_policies(r,*args)
    def test_nan_cost_is_never_pass(self):
        r,*args=self.fixture();r['check_selected_or_diagnostic_zero_buffer']['net_usd']=float('nan')
        with self.assertRaises(EvidenceError):audit_policies(r,*args)
    def test_uniform_tie_rule_and_negative_abstention(self):
        r,ca,cv,ch,hv,g=self.fixture()
        self.assertTrue(all(x['side']=='BUY' for x in trace(ch,[[1,1] for _ in ch],0)['details']))
        self.assertEqual(trace(ch,[[-1,-2] for _ in ch],0)['trades'],0)
    def test_new_buffer_grid_rejected(self):
        r,*args=self.fixture();args[-1]=[0.,.1,.2,.3]
        with self.assertRaises(EvidenceError):audit_policies(r,*args)
    def test_qualifier_cannot_enable_trading(self):
        r,*args=self.fixture();r['economic_qualification']=True
        with self.assertRaises(EvidenceError):audit_policies(r,*args)
if __name__=='__main__':unittest.main()
