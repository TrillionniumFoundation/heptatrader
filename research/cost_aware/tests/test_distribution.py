import unittest
from dataclasses import replace
from fixtures import *

class DistributionCosts(unittest.TestCase):
    def policy(self):
        return replace(DEFAULT_POLICY, btc_slippage_bps_per_side='0', fx_slippage_bps_per_side='0', holding_bps_per_hour='0')
    def test_fee_minimum_is_applied_before_scenario_weight(self):
        f = forecast(move='0');f['scenarios'] = [{'probability': '.5', 'move_bps': '-1000'}, {'probability': '.5', 'move_bps': '1000'}]
        expected = expected_round_trip(f, quote(), '972.2222222222222222222222222', policy=self.policy())
        plug_in = estimate_round_trip('BTC.USD', 'BUY', '972.2222222222222222222222222', quote(), 0, 300, policy=self.policy())
        self.assertGreater(dec(expected['exit_commission_usd']), dec(plug_in['exit_commission_usd']))
        self.assertLess(dec(expected['net_edge_usd']), dec(plug_in['net_edge_usd']))
    def test_expected_net_is_sum_of_scenario_nets(self):
        result = expected_round_trip(forecast(), quote(), '25000')
        value = sum((dec(x['probability']) * dec(x['net_edge_usd']) for x in result['scenario_net_edges']), D(0))
        self.assertEqual(dec(result['net_edge_usd']), value)
    def test_haircut_reduces_net(self):
        ordinary = expected_round_trip(forecast(), quote(), '25000')
        cautious = expected_round_trip(forecast(), quote(), '25000', overprediction_haircut_bps='5')
        self.assertLess(dec(cautious['net_edge_usd']), dec(ordinary['net_edge_usd']))
    def test_fx_short_scenario_direction(self):
        f = forecast(instrument='EUR.USD',action='SELL',move='-80',mid='1.12')
        r = expected_round_trip(f, quote(instrument='EUR.USD',mid='1.12'), '25000')
        self.assertGreater(dec(r['net_edge_usd']), 0)
    def test_haircut_must_not_be_negative(self):
        with self.assertRaisesRegex(CostError, 'NEGATIVE_HAIRCUT'):
            expected_round_trip(forecast(), quote(), '25000', overprediction_haircut_bps='-1')
    def test_distribution_is_not_recentered_after_quote_rally(self):
        r = expected_round_trip(forecast(), quote(mid='100.7'), '25000')
        self.assertLess(dec(r['net_edge_usd']), 0)
    def test_demo_generator_is_deterministic(self):
        from hepta_cost_demo import inputs
        self.assertEqual(canonical(inputs('BTC.USD')), canonical(inputs('BTC.USD')))
    def test_demo_calibration_cannot_authorize_resident(self):
        from hepta_cost_demo import inputs
        tape,a = inputs('BTC.USD');row=tape['rows'][4]
        decision = entry_admission(row['forecast'],row['quote'],'25000',row['at_ms'],a)
        self.assertFalse(decision['allowed'])
        self.assertIn('SYNTHETIC_CALIBRATION_NOT_LIVE', decision['reasons'])

if __name__=='__main__':unittest.main(verbosity=2)
