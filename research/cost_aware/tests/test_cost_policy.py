import unittest, tempfile, os, copy
from pathlib import Path
from dataclasses import replace
from fixtures import *

class CostTests(unittest.TestCase):
    def test_btc_25000_single_order(self):
        self.assertEqual(commission('BTC.USD','25000'),D('45.00'))
    def test_two_fills_do_not_double_order_fee(self):
        fills=[D('10000'),D('15000')]
        self.assertEqual(commission('BTC.USD',sum(fills)),D('45.00'))
        self.assertNotEqual(commission('BTC.USD',sum(fills)),D('90'))
    def test_fx_minimum_per_order_not_per_fill(self):
        self.assertEqual(commission('EUR.USD',D(500)+D(500)),D(2))
        self.assertEqual(commission('EUR.USD',500)*2,D(4))
    def test_fx_020_basis_points(self):
        self.assertEqual(commission('EUR.USD','1000000'),D(20))
    def test_zero_notional_zero_estimate(self):
        self.assertEqual(commission('BTC.USD',0),0)
    def test_btc_unconfirmed_rebate_not_assumed(self):
        self.assertEqual(commission('BTC.USD',50),D('1.75'))
    def test_bad_commissions(self):
        for value in [-1,float('nan'),float('inf'),True,'bad']:
            with self.subTest(value=value),self.assertRaises(CostError): commission('BTC.USD',value)
    def test_unsupported_instrument(self):
        with self.assertRaises(CostError):commission('IBOPT:123',500)
    def test_break_even_requires_more_than_36bps(self):
        p=replace(DEFAULT_POLICY,btc_slippage_bps_per_side='0',holding_bps_per_hour='0',btc_min_net_edge_bps='0')
        low=estimate_round_trip('BTC.USD','BUY',25000,quote(),36,300,policy=p)
        high=estimate_round_trip('BTC.USD','BUY',25000,quote(),37,300,policy=p)
        self.assertLess(dec(low['net_edge_usd']),0)
        self.assertGreater(dec(high['net_edge_usd']),0)
    def test_exit_fee_uses_changed_notional(self):
        p=replace(DEFAULT_POLICY,btc_slippage_bps_per_side='0',holding_bps_per_hour='0')
        c=estimate_round_trip('BTC.USD','BUY',25000,quote(),100,300,policy=p)
        self.assertEqual(dec(c['entry_commission_usd']),D(45))
        self.assertEqual(dec(c['exit_commission_usd']),D('45.45'))
    def test_fx_spread_is_counted_once(self):
        p=replace(DEFAULT_POLICY,fx_slippage_bps_per_side='0',holding_bps_per_hour='0')
        c=estimate_round_trip('EUR.USD','BUY',25000,quote(instrument='EUR.USD',mid='1.12',spread='.00002'),0,300,policy=p)
        self.assertEqual(dec(c['spread_usd']),D('.5'))
        self.assertEqual(dec(c['net_edge_usd']),D('-4.5'))
    def test_execution_price_cashflow_equals_mid_minus_costs(self):
        for instrument,side,mid in [('BTC.USD','BUY','100'),('EUR.USD','BUY','1.12'),('EUR.USD','SELL','1.12')]:
            c=estimate_round_trip(instrument,side,25000,quote(instrument=instrument,mid=mid,spread=str(dec(mid)*D('.0002'))),80,3600)
            qty=dec(c['quantity_estimate']); sign=1 if side=='BUY' else -1
            actual=qty*(dec(c['estimated_exit_price'])-dec(c['estimated_entry_price']))*sign
            expected=dec(c['gross_edge_usd'])-dec(c['spread_usd'])-dec(c['slippage_usd'])
            self.assertLess(abs(actual-expected),D('.0000000001'))
    def test_cost_stress_cannot_discount(self):
        with self.assertRaises(CostError):replace(DEFAULT_POLICY,fee_stress='.5').validate()
    def test_cost_stress_increases_estimate(self):
        a=estimate_round_trip('BTC.USD','BUY',25000,quote(),80,300)
        b=estimate_round_trip('BTC.USD','BUY',25000,quote(),80,300,policy=replace(DEFAULT_POLICY,fee_stress='1.5',slippage_stress='5'))
        self.assertLess(dec(b['net_edge_usd']),dec(a['net_edge_usd']))
    def test_btc_short_estimate_refused(self):
        with self.assertRaises(CostError):estimate_round_trip('BTC.USD','SELL',25000,quote(),80,300)
    def test_fresh_quote(self):self.assertEqual(quote_values(quote(),'BTC.USD',NOW)[2],100)
    def test_quote_boundary(self):
        quote_values(quote(),'BTC.USD',NOW+5000)
        with self.assertRaises(CostError):quote_values(quote(),'BTC.USD',NOW+5001)
    def test_quote_future_and_frozen(self):
        for field,value in [('observed_at_ms',NOW+1),('stale',True),('authoritative',False),('source','APP'),('instrument','EUR.USD'),('ask','90')]:
            q=quote();q[field]=value
            with self.subTest(field=field),self.assertRaises(CostError):quote_values(q,'BTC.USD',NOW)

class CalibrationTests(unittest.TestCase):
    def test_valid_cohort_but_synthetic_blocked_for_resident(self):
        a=artifact()
        with self.assertRaisesRegex(CostError,'SYNTHETIC'):calibrated_move(forecast(),a,NOW)
        raw,lower=calibrated_move(forecast(),a,NOW,synthetic_ok=True)
        self.assertEqual((raw,lower),(D(80),D(75)))
    def test_no_calibration(self):
        r=entry_admission(forecast(),quote(),25000,NOW,None)
        self.assertFalse(r['allowed']);self.assertIn('CALIBRATION_REQUIRED',r['reasons'])
        self.assertIn('uncalibrated_mean_estimate',r)
    def test_high_confidence_cannot_replace_calibration(self):
        f=forecast();f['confidence']=1
        self.assertFalse(entry_admission(f,quote(),25000,NOW)['allowed'])
    def test_future_source_labels(self):
        rows=samples(); rows[-1]['label_observed_at_ms']=NOW+1000
        with self.assertRaisesRegex(CostError,'FUTURE'):build_calibration(rows,NOW)
    def test_future_knowledge_labels(self):
        rows=samples(); rows[0]['label_available_at_ms']=NOW+1
        with self.assertRaisesRegex(CostError,'FUTURE'):build_calibration(rows,NOW)
    def test_retrospective_forecast_rejected(self):
        rows=samples();rows[0]['forecast_available_at_ms']=rows[0]['label_observed_at_ms']+1
        with self.assertRaisesRegex(CostError,'FUTURE'):build_calibration(rows,NOW)
    def test_missing_knowledge_timestamp_rejected(self):
        rows=samples();del rows[0]['label_available_at_ms']
        with self.assertRaises(CostError):build_calibration(rows,NOW)
    def test_minimum_independent_samples(self):
        a=build_calibration(samples(count=20),NOW-1000)
        self.assertEqual(a['cohorts'],{})
    def test_overlapping_samples_do_not_count_as_independent(self):
        rows=samples()
        for i,row in enumerate(rows):
            t=NOW-1000000+i*1000;row['forecast']['issued_at_ms']=t
            row.update(forecast_available_at_ms=t,label_observed_at_ms=t+300000,label_available_at_ms=t+300001)
        a=build_calibration(rows,NOW-1000)
        self.assertFalse(a['cohorts'])
    def test_duplicate_samples_rejected(self):
        rows=samples();rows.append(copy.deepcopy(rows[0]))
        with self.assertRaisesRegex(CostError,'DUPLICATE'):build_calibration(rows,NOW-1000)
    def test_future_artifact(self):
        a=artifact();a['completed_at_ms']=NOW;a['valid_until_ms']=NOW+86400000;reseal(a)
        with self.assertRaisesRegex(CostError,'FUTURE'):calibrated_move(forecast(),a,NOW,synthetic_ok=True)
    def test_expired_artifact(self):
        a=artifact();a['valid_until_ms']=NOW;reseal(a)
        with self.assertRaisesRegex(CostError,'EXPIRED'):calibrated_move(forecast(),a,NOW,synthetic_ok=True)
    def test_artifact_hash_changed(self):
        a=artifact();a['cohorts'][calibration_key(forecast())]['count']=500
        with self.assertRaisesRegex(CostError,'IDENTITY'):calibrated_move(forecast(),a,NOW,synthetic_ok=True)
    def test_no_extrapolation(self):
        with self.assertRaisesRegex(CostError,'SUPPORT'):calibrated_move(forecast(move='101'),artifact(),NOW,synthetic_ok=True)
    def test_no_cross_instrument_calibration(self):
        f=forecast(instrument='EUR.USD')
        with self.assertRaisesRegex(CostError,'COHORT'):calibrated_move(f,artifact(),NOW,synthetic_ok=True)
    def test_short_direction_calibration(self):
        f=forecast(instrument='EUR.USD',action='SELL',move='-80')
        self.assertEqual(calibrated_move(f,artifact('EUR.USD','SELL'),NOW,synthetic_ok=True),(D(80),D(75)))
    def test_no_model_or_horizon_substitution(self):
        for field,value in [('model_id','other'),('horizon_sec',900)]:
            f=forecast();f[field]=value
            with self.assertRaises(CostError):calibrated_move(f,artifact(),NOW,synthetic_ok=True)
    def test_private_artifact_read(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'c.json';p.write_text(canonical(artifact()));p.chmod(0o600)
            self.assertEqual(load_calibration(p)['calibration_id'],artifact()['calibration_id'])
    def test_artifact_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'c.json';p.write_text('{}');p.chmod(0o600); link=Path(td)/'alias';link.symlink_to(p)
            with self.assertRaises(OSError):load_calibration(link)
    def test_artifact_world_readable_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'c.json';p.write_text('{}');p.chmod(0o644)
            with self.assertRaises(CostError):load_calibration(p)
    def test_json_duplicate_and_nonfinite(self):
        for raw in ['{"x":1,"x":2}','{"x":NaN}']:
            with self.assertRaises(CostError):strict_loads(raw)

class RemoteRuntimeGateTests(unittest.TestCase):
    def test_remote_raw_positive_edge_requires_no_local_training_artifact(self):
        r=remote_entry_admission(forecast(),quote(),25000,NOW)
        self.assertTrue(r['allowed']);self.assertIsNone(r['calibration_id'])
        self.assertFalse(r['local_training_artifact_used'])
        self.assertEqual(r['forecast_basis'],'REMOTE_MODEL_RAW_POSITION_NEUTRAL_SCENARIOS')
    def test_remote_raw_small_edge_rejected_by_deterministic_costs(self):
        r=remote_entry_admission(forecast(move='20'),quote(),25000,NOW)
        self.assertFalse(r['allowed']);self.assertIn('NET_EDGE_BELOW_COST_HURDLE',r['reasons'])
        self.assertGreater(dec(r['cost_components']['entry_commission_usd']),0)
        self.assertGreater(dec(r['cost_components']['exit_commission_usd']),0)
        self.assertGreater(dec(r['cost_components']['minimum_net_edge_usd']),0)
    def test_remote_raw_reentry_and_quote_freshness_still_fail_closed(self):
        r=remote_entry_admission(forecast(),quote(),25000,NOW,last_exit_ms=NOW-1)
        self.assertIn('REENTRY_COOLDOWN',r['reasons'])
        q=quote();q['stale']=True
        self.assertFalse(remote_entry_admission(forecast(),q,25000,NOW)['allowed'])
    def test_remote_reversal_uses_exit_cost_not_sunk_entry_fee(self):
        close,reason=remote_discretionary_exit(
            forecast(instrument='EUR.USD',action='SELL',move='-80',mid='1.12'),
            quote(instrument='EUR.USD',mid='1.12'),25000,NOW)
        self.assertTrue(close);self.assertEqual(reason,'REMOTE_RAW_REVERSAL')
    def test_remote_hold_never_becomes_entry(self):
        self.assertFalse(remote_entry_admission(
            forecast(action='HOLD',move='0'),quote(),25000,NOW)['allowed'])


class GateTests(unittest.TestCase):
    def test_calibrated_positive_net(self):
        self.assertTrue(entry_admission(forecast(),quote(),25000,NOW,artifact(),synthetic_ok=True)['allowed'])
    def test_small_directional_edge_rejected(self):
        r=entry_admission(forecast(move='20'),quote(),25000,NOW,artifact(),synthetic_ok=True)
        self.assertFalse(r['allowed']);self.assertIn('NET_EDGE_BELOW_COST_HURDLE',r['reasons'])
    def test_target_is_not_recentered_after_price_rise(self):
        r=entry_admission(forecast(),quote(mid='100.7'),25000,NOW,artifact(),synthetic_ok=True)
        self.assertFalse(r['allowed'])
    def test_reentry_cooldown(self):
        r=entry_admission(forecast(),quote(),25000,NOW,artifact(),last_exit_ms=NOW-10000,synthetic_ok=True)
        self.assertIn('REENTRY_COOLDOWN',r['reasons'])
        self.assertTrue(entry_admission(forecast(),quote(),25000,NOW,artifact(),last_exit_ms=NOW-120000,synthetic_ok=True)['allowed'])
    def test_disabling_band_only_in_offline_comparison(self):
        self.assertTrue(entry_admission(forecast(),quote(),25000,NOW,artifact(),last_exit_ms=NOW-1,no_trade_band=False,synthetic_ok=True)['allowed'])
    def test_future_last_exit_refused(self):
        self.assertFalse(entry_admission(forecast(),quote(),25000,NOW,artifact(),last_exit_ms=NOW+1,synthetic_ok=True)['allowed'])
    def test_expired_forecast_with_fresh_quote(self):
        r=entry_admission(forecast(),quote(NOW+60001),25000,NOW+60001,artifact(),synthetic_ok=True)
        self.assertIn('FORECAST_STALE',r['reasons'])
    def test_forecast_invalidation(self):
        r=entry_admission(forecast(),quote(mid='99.8'),25000,NOW,artifact(),synthetic_ok=True)
        self.assertIn('FORECAST_ALREADY_INVALIDATED',r['reasons'])
    def test_distribution_required(self):
        for f in [{'action':'BUY','confidence':1,'rationale':'legacy'},None]:
            self.assertFalse(entry_admission(f,quote(),25000,NOW,artifact())['allowed'])
    def test_bad_distribution(self):
        f=forecast();f['scenarios'][0]['probability']='.7'
        self.assertFalse(entry_admission(f,quote(),25000,NOW,artifact())['allowed'])
    def test_nonfinite_candidate_fails_closed(self):
        f=forecast();f['scenarios'][0]['move_bps']=float('nan')
        self.assertFalse(entry_admission(f,quote(),25000,NOW,artifact())['allowed'])
    def test_invalidation_side(self):
        f=forecast();f['invalidation_price']='101'
        with self.assertRaises(CostError):validate_forecast(f)
    def test_risk_close_no_calibration_needed(self):
        self.assertEqual(risk_exit(1,100,quote(mid='99'),NOW),'MAX_ADVERSE_MOVE')
    def test_halt_close_no_quote_needed(self):
        self.assertEqual(risk_exit(1,100,{},NOW,halt=True),'HALT_REDUCE_ONLY')
    def test_horizon_close(self):
        self.assertEqual(risk_exit(1,100,quote(NOW+300000),NOW+300000,entry_forecast=forecast()),'FORECAST_HORIZON_EXPIRED')
    def test_invalidation_close(self):
        self.assertEqual(risk_exit(1,100,quote(mid='99.89'),NOW,entry_forecast=forecast()),'FORECAST_INVALIDATION')
    def test_calibrated_reversal(self):
        f=forecast(action='SELL',move='-80')
        self.assertEqual(discretionary_exit(f,quote(),250,NOW,artifact(action='SELL'),synthetic_ok=True),(True,'CALIBRATED_REVERSAL'))
    def test_no_adjustment_band(self):
        f=forecast(action='SELL',move='-20')
        self.assertEqual(discretionary_exit(f,quote(),250,NOW,artifact(action='SELL'),synthetic_ok=True),(False,'NO_ADJUSTMENT_BAND'))
    def test_missing_calibration_does_not_authorize_discretionary_reverse(self):
        self.assertFalse(discretionary_exit(forecast(action='SELL',move='-80'),quote(),250,NOW,None)[0])
    def test_entry_fee_not_an_exit_argument(self):
        import inspect
        self.assertNotIn('entry_commission',inspect.signature(discretionary_exit).parameters)

if __name__=='__main__': unittest.main(verbosity=2)
