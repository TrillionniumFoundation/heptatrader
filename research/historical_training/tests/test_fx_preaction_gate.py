import unittest
from historical_training.fx_preaction_gate import evaluate, GateError, SCHEMA, sha

def fixture():
    plan=dict(schema=SCHEMA,contract="EUR.USD",candidate_sha256="a"*64,
              history_sha256="b"*64,cost_roundtrip_bps=3.7,min_net_edge_bps=2.0,
              max_quote_age_ms=2000,max_forecast_age_ms=2000,registered_before_ms=100,
              shadow_only=True)
    quote=dict(instrument="EUR.USD",bid=1.10000,ask=1.10002,observed_at_ms=1000,
               available_at_ms=1100,expires_at_ms=3000,source="IB",authoritative=True,
               source_sha256="c"*64)
    forecast=dict(instrument="EUR.USD",direction="BUY",expected_move_bps=10.,
                  issued_at_ms=1150,available_at_ms=1200,input_quote_sha256="c"*64,
                  model_sha256="a"*64,policy_logged_propensity=True)
    return plan,quote,forecast

class GateTests(unittest.TestCase):
    def test_no_candidate_is_hold(self):
        p,q,f=fixture();d=evaluate(p,None,None,1500)
        self.assertEqual(d["state"],"HOLD")
        self.assertFalse(d["trade_authority"])
    def test_causally_qualified_is_review_not_trade(self):
        p,q,f=fixture();d=evaluate(p,q,f,1500)
        self.assertEqual(d["state"],"REVIEW_ONLY")
        self.assertEqual(d["shadow_action"],"BUY")
        self.assertFalse(d["trade_authority"])
        self.assertFalse(d["model_promotion_allowed"])
    def test_expired_quote_hold(self):
        p,q,f=fixture();q["expires_at_ms"]=1499
        self.assertEqual(evaluate(p,q,f,1500)["state"],"HOLD")
    def test_late_forecast_hold(self):
        p,q,f=fixture();f["available_at_ms"]=1501
        self.assertEqual(evaluate(p,q,f,1500)["state"],"HOLD")
    def test_quote_binding_hold(self):
        p,q,f=fixture();f["input_quote_sha256"]="d"*64
        self.assertEqual(evaluate(p,q,f,1500)["state"],"HOLD")
    def test_candidate_identity_hold(self):
        p,q,f=fixture();f["model_sha256"]="e"*64
        self.assertEqual(evaluate(p,q,f,1500)["state"],"HOLD")
    def test_propensity_hold(self):
        p,q,f=fixture();f["policy_logged_propensity"]=False
        self.assertEqual(evaluate(p,q,f,1500)["state"],"HOLD")
    def test_insufficient_edge_hold(self):
        p,q,f=fixture();f["expected_move_bps"]=1.
        self.assertEqual(evaluate(p,q,f,1500)["state"],"HOLD")
    def test_direction_mismatch_hold(self):
        p,q,f=fixture();f["direction"]="SELL"
        self.assertEqual(evaluate(p,q,f,1500)["state"],"HOLD")
    def test_uncommitted_plan_rejected(self):
        p,q,f=fixture();p["registered_before_ms"]=1500
        with self.assertRaisesRegex(GateError,"PLAN_NOT_PRECOMMITTED"):evaluate(p,q,f,1500)
    def test_invalid_crossed_quote_rejected(self):
        p,q,f=fixture();q["ask"]=1.0
        with self.assertRaisesRegex(GateError,"CROSSED_OR_ZERO_QUOTE"):evaluate(p,q,f,1500)
    def test_extra_fields_rejected(self):
        p,q,f=fixture();f["realized_pnl"]=999
        with self.assertRaisesRegex(GateError,"FORECAST_FIELDS"):evaluate(p,q,f,1500)
    def test_nan_rejected(self):
        p,q,f=fixture();f["expected_move_bps"]=float("nan")
        with self.assertRaisesRegex(GateError,"NONFINITE"):evaluate(p,q,f,1500)
    def test_unattested_source_hold(self):
        p,q,f=fixture();q["authoritative"]=False
        self.assertEqual(evaluate(p,q,f,1500)["state"],"HOLD")
if __name__=="__main__":unittest.main()
