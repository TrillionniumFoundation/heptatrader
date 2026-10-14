import dataclasses as dc
import unittest
from forecast_action_boundary import (Quote,Forecast,Policy,BoundaryRejected,decide,require_durable_before_deadline)


class ForecastActionBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.old=Quote('EUR.USD','epoch1','a'*64,1000,1010,6000,1.10,1.1001)
        self.policy=Policy('slow-fast-v1','b'*64,60000,30000,20,2)
        self.forecast=Forecast('slow-fast-v1','b'*64,self.old,1100,7400,7450,1.102)
        self.quote=Quote('EUR.USD','epoch1','c'*64,7500,7501,12500,1.1001,1.1002)

    def test_slow_model_may_outlive_input_ttl_but_new_quote_cannot(self):
        result=decide(self.forecast,self.quote,self.policy,7520)
        self.assertEqual(result.action,'BUY');self.assertEqual(result.authorization_effect,'NONE')
        self.assertEqual(result.expires_at_ms,12500)
        self.assertEqual(self.forecast.input_quote.expires_at_ms,6000)
        require_durable_before_deadline(result,7600)

    def test_negative_timing_and_identity_cases(self):
        for quote,policy,now,reason in [
            (self.quote,self.policy,12500,'EXPIRED'),
            (self.quote,self.policy,7500,'COMMITTED'),
            (dc.replace(self.quote,receipt_sha256='a'*64),self.policy,7520,'REUSED'),
            (dc.replace(self.quote,observed_at_ms=7449),self.policy,7520,'COMMITTED'),
            (dc.replace(self.quote,epoch='epoch2'),self.policy,7520,'SCOPE'),
            (dc.replace(self.quote,instrument='BTC.USD'),self.policy,7520,'SCOPE'),
            (self.quote,dc.replace(self.policy,model_sha256='d'*64),7520,'MISMATCH'),
            (self.quote,dc.replace(self.policy,max_input_age_ms=6500),7520,'EXPIRED'),
            (self.quote,dc.replace(self.policy,horizon_ms=6520),7520,'EXPIRED'),
            (dc.replace(self.quote,bid=1.2,ask=1.21),self.policy,7520,'REPRICING')]:
            with self.subTest(reason=reason), self.assertRaisesRegex(BoundaryRejected,reason):
                decide(self.forecast,quote,policy,now)

    def test_post_fsync_clock_not_prewrite_clock(self):
        result=decide(self.forecast,self.quote,self.policy,12499)
        for t in (12498,12500,12501,True):
            with self.subTest(t=t),self.assertRaisesRegex(BoundaryRejected,'DURABLE'):
                require_durable_before_deadline(result,t)

    def test_no_spurious_order_from_no_edge(self):
        forecast=dc.replace(self.forecast,predicted_mid=1.10015)
        self.assertEqual(decide(forecast,self.quote,self.policy,7520).action,'HOLD')
        forecast=dc.replace(self.forecast,predicted_mid=1.098)
        self.assertEqual(decide(forecast,self.quote,self.policy,7520).action,'SELL')

    def test_forecast_is_immutable_and_causal(self):
        with self.assertRaises(dc.FrozenInstanceError):self.forecast.predicted_mid=100
        for kwargs in ({'started_at_ms':6000},{'committed_at_ms':7399},
                       {'predicted_mid':float('nan')},{'started_at_ms':True}):
            with self.subTest(kwargs=kwargs),self.assertRaises(BoundaryRejected):
                dc.replace(self.forecast,**kwargs)

    def test_invalid_quotes_and_policies_refuse(self):
        for kwargs in ({'bid':float('inf')},{'ask':1.0},{'expires_at_ms':7501},
                       {'receipt_sha256':'bad'},{'received_at_ms':True}):
            with self.subTest(kwargs=kwargs),self.assertRaises(BoundaryRejected):dc.replace(self.quote,**kwargs)
        for kwargs in ({'minimum_edge_bps':-1},{'max_input_age_ms':True},{'max_repricing_bps':float('nan')}):
            with self.subTest(kwargs=kwargs),self.assertRaises(BoundaryRejected):dc.replace(self.policy,**kwargs)
