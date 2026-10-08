import copy,unittest
from historical_training.common import EvidenceError,digest
from historical_training.bar_forward_smoke import WindowGuard,bar_input,check_frozen,CLIENT

ANCHOR=1791453600

def started():
    g=WindowGuard(ANCHOR);g.check('71\x002\x008991\x00\x00',False,ANCHOR-10,0);return g

class WindowTests(unittest.TestCase):
    def wire(self,g,rid=9101):return '\x00'.join(g.expected(rid))+'\x00'
    def test_only_fixed_input_can_be_requested(self):
        g=started();g.check(self.wire(g),True,ANCHOR+2,1);self.assertEqual(g.issued,{9101})
    def test_financial_message_is_denied(self):
        with self.assertRaises(EvidenceError):started().check('3\x001\x00',True,ANCHOR,1)
    def test_late_input_request_is_denied(self):
        g=started()
        with self.assertRaises(EvidenceError):g.check(self.wire(g),True,ANCHOR+241,1)
    def test_future_outcome_before_prediction_is_denied(self):
        g=started()
        with self.assertRaises(EvidenceError):g.check(self.wire(g,9104),True,ANCHOR+1502,1)
    def test_future_outcome_before_maturity_is_denied(self):
        g=started();g.prediction_published=True
        with self.assertRaises(EvidenceError):g.check(self.wire(g,9104),True,ANCHOR+1499,1)
    def test_mature_label_request_allowed_after_publication(self):
        g=started();g.prediction_published=True;g.check(self.wire(g,9104),True,ANCHOR+1502,1)
        self.assertEqual(g.issued,{9104})
    def test_repeated_input_is_denied(self):
        g=started();g.check(self.wire(g),True,ANCHOR+2,1)
        with self.assertRaises(EvidenceError):g.check(self.wire(g),True,ANCHOR+20,20)
    def test_foreign_cancel_is_denied(self):
        with self.assertRaises(EvidenceError):started().check('25\x001\x009999\x00',True,ANCHOR,1)
    def test_changed_contract_is_denied(self):
        g=started();fields=g.expected(9101);fields[3]='GBP'
        with self.assertRaises(EvidenceError):g.check('\x00'.join(fields)+'\x00',True,ANCHOR+2,1)
    def test_input_finite_and_exactly_twelve_closed_bars(self):
        series={k:{t:{x:str(value) for x in ('open','high','low','close')} for t in range(ANCHOR-3600,ANCHOR,300)} for k,value in [('MIDPOINT',1.1),('BID',1.09999),('ASK',1.10001)]}
        r=bar_input(series,ANCHOR);self.assertEqual(len(r['features']),25);self.assertEqual(r['features'][:11],[0.]*11)
        del series['BID'][ANCHOR-900]
        with self.assertRaises(EvidenceError):bar_input(series,ANCHOR)
    def test_hindsight_candidate_rejected(self):
        c={'profile':'EURUSD_5MIN_LOOKBACK12_LEAD1_HOLD3_V1','price_shadow_eligible':True,'trade_authority':False,'automatic_promotion':False,'created_at_ms':ANCHOR*1000}
        c['candidate_id']=digest(c)
        with self.assertRaises(EvidenceError):check_frozen(c,(ANCHOR+1)*1000,ANCHOR)
    def test_late_prediction_rejected(self):
        c={'profile':'EURUSD_5MIN_LOOKBACK12_LEAD1_HOLD3_V1','price_shadow_eligible':True,'trade_authority':False,'automatic_promotion':False,'created_at_ms':(ANCHOR-1)*1000}
        c['candidate_id']=digest(c)
        with self.assertRaises(EvidenceError):check_frozen(c,(ANCHOR+300)*1000,ANCHOR)
    def test_forecast_must_be_frozen_before_anchor(self):
        c={'profile':'EURUSD_5MIN_LOOKBACK12_LEAD1_HOLD3_V1','price_shadow_eligible':True,'trade_authority':False,'automatic_promotion':False,'created_at_ms':(ANCHOR-1)*1000}
        c['candidate_id']=digest(c);check_frozen(c,(ANCHOR+3)*1000,ANCHOR)
        c['trade_authority']=True;c['candidate_id']=digest({k:v for k,v in c.items() if k!='candidate_id'})
        with self.assertRaises(EvidenceError):check_frozen(c,(ANCHOR+3)*1000,ANCHOR)
if __name__=='__main__':unittest.main()
