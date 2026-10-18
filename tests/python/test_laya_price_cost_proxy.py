import math
import tempfile
import unittest
import copy
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'research/neuron_learning'))
from laya_cell import price_cost_proxy as c
from laya_cell.integrity import ContractError

def bar(t=0, opening=1.1, closing=1.101, spread=0.0002):

    def leg(offset):
        o = opening + offset
        v = closing + offset
        return {'open': o, 'close': v, 'low': min(o, v), 'high': max(o, v)}
    return {'time': t, 'bid': leg(0), 'ask': leg(spread), 'midpoint': leg(spread / 2)}

def mapping():
    return c.train_mapping([-11.0, -5.0, -2.0, 0.0, 2.0, 5.0, 11.0], list(range(7)))

def action(side='LONG', anchor=300000):
    p = [0.0] * 7
    p[6 if side == 'LONG' else 0] = 1.0
    return c.decide(anchor, p, mapping(), bar(anchor // 1000 - 300))

class PriceCostProxyTests(unittest.TestCase):

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.tmp_path = Path(directory.name)

    def test_train_bins_right_boundary_and_invalid_train(self):
        values = [-11.0, -10.0, -3.0, -1.0, 1.0, 3.0, 10.0]
        result = c.train_mapping(values, list(range(7)))
        assert [x['count'] for x in result] == [1] * 7
        with self.assertRaises(ContractError):
            c.train_mapping(values, [0] * 7)
        with self.assertRaises(ContractError):
            c.train_mapping([0.0], [3])
        with self.assertRaises(ContractError):
            c.train_mapping([float('nan')], [3])

    def test_exact_threshold_is_flat_and_bad_probabilities_fail(self):
        b = bar(opening=1.0, closing=1.0, spread=0)
        for idx in (2, 4):
            p = [0.0] * 7
            p[3] = 0.8
            p[idx] = 0.2
            a = c.decide(300000, p, mapping(), b)
            assert a['side'] == 'FLAT'
        with self.assertRaises(ContractError):
            c.decide(300000, [1.0] * 7, mapping(), b)
        b['ask']['close'] = 0.9
        with self.assertRaises(ContractError):
            c.decide(300000, [0, 0, 0, 1, 0, 0, 0], mapping(), b)

    def test_future_marks_never_change_action_or_mapping(self):
        a = action()
        before = copy.deepcopy(a)
        mu = mapping()
        first = c.mark(a, bar(300))
        second = c.mark(a, bar(300, closing=1.2, spread=0.001))
        assert a == before and mapping() == mu
        assert first['gross_usd'] != second['gross_usd']
        assert c.mark(a, None)['status'] == 'UNSCORABLE'

    def test_manual_long_short_cashflows_and_no_trade(self):
        b = bar(300)
        assert math.isclose(c.mark(action('LONG'), b)['gross_usd'], 80, rel_tol=1e-06, abs_tol=1e-12)
        assert math.isclose(c.mark(action('SHORT'), b)['gross_usd'], -120, rel_tol=1e-06, abs_tol=1e-12)
        flat = {**action(), 'side': 'FLAT', 'predicted_return_bps': 0.0}
        assert c.mark(flat, None)['status'] == 'NO_TRADE'

    def test_bad_future_keeps_trade_in_denominator_missing(self):
        mutation = 'missing'
        b = bar(300)
        if mutation == 'missing':
            b = None
        elif mutation == 'time':
            b['time'] = 600
        elif mutation == 'crossed':
            b['bid']['close'] = 2
        else:
            b['midpoint']['low'] = 2
        result = c.summarize([c.mark(action(), b)])
        assert result['scenarios']['4']['overall']['unscorable_trades'] == 1
        assert result['scenarios']['4']['overall']['net_usd'] is None

    def test_bad_future_keeps_trade_in_denominator_time(self):
        mutation = 'time'
        b = bar(300)
        if mutation == 'missing':
            b = None
        elif mutation == 'time':
            b['time'] = 600
        elif mutation == 'crossed':
            b['bid']['close'] = 2
        else:
            b['midpoint']['low'] = 2
        result = c.summarize([c.mark(action(), b)])
        assert result['scenarios']['4']['overall']['unscorable_trades'] == 1
        assert result['scenarios']['4']['overall']['net_usd'] is None

    def test_bad_future_keeps_trade_in_denominator_crossed(self):
        mutation = 'crossed'
        b = bar(300)
        if mutation == 'missing':
            b = None
        elif mutation == 'time':
            b['time'] = 600
        elif mutation == 'crossed':
            b['bid']['close'] = 2
        else:
            b['midpoint']['low'] = 2
        result = c.summarize([c.mark(action(), b)])
        assert result['scenarios']['4']['overall']['unscorable_trades'] == 1
        assert result['scenarios']['4']['overall']['net_usd'] is None

    def test_bad_future_keeps_trade_in_denominator_range(self):
        mutation = 'range'
        b = bar(300)
        if mutation == 'missing':
            b = None
        elif mutation == 'time':
            b['time'] = 600
        elif mutation == 'crossed':
            b['bid']['close'] = 2
        else:
            b['midpoint']['low'] = 2
        result = c.summarize([c.mark(action(), b)])
        assert result['scenarios']['4']['overall']['unscorable_trades'] == 1
        assert result['scenarios']['4']['overall']['net_usd'] is None

    def test_one_book_fees_are_exact_and_adjacent_trades_do_not_overlap(self):
        rows = [c.mark(action(anchor=a), bar(a // 1000)) for a in (300000, 600000)]
        result = c.summarize(rows)['scenarios']
        assert result['0']['overall']['net_usd'] - result['4']['overall']['net_usd'] == 8
        assert result['4']['overall']['net_usd'] - result['8']['overall']['net_usd'] == 8
        assert result['4']['overall']['trades'] == 2
        with self.assertRaises(ContractError):
            c.summarize([rows[0], rows[0]])
        with self.assertRaises(ContractError):
            c.summarize(list(reversed(rows)))

    def test_flat_zero_and_loss_drawdown(self):
        a = {**action(), 'side': 'FLAT', 'predicted_return_bps': 0.0}
        result = c.summarize([c.mark(a, None)])['scenarios']['8']['overall']
        assert result['trades'] == 0 and result['net_usd'] == 0
        row = c.mark(action('SHORT'), bar(300))
        loss = c.summarize([row])['scenarios']['4']['overall']
        assert loss['net_usd'] < 0 and math.isclose(loss['max_drawdown_usd'], -loss['net_usd'], rel_tol=1e-06, abs_tol=1e-12)

    def test_incomplete_book_never_promotes_winning_subset_to_total(self):
        first = c.mark(action(), bar(300))
        missing = c.mark(action(anchor=600000), None)
        result = c.summarize([first, missing])['scenarios']['4']['overall']
        assert result['observed_subset_net_usd'] > 0
        assert result['net_usd'] is None and result['max_drawdown_usd'] is None

    def test_input_opportunity_survives_missing_future_target(self):
        known = {i * 300: bar(i * 300) for i in range(13)}
        anchor = 13 * 300000
        first, current = c.known_input_features(known, anchor)
        a = c.decide(anchor, [0, 0, 0, 0, 0, 0, 1], mapping(), current)
        assert a['side'] == 'LONG'
        missing = c.mark(a, known.get(anchor // 1000))
        assert c.summarize([missing])['scenarios']['4']['overall']['net_usd'] is None
        known[anchor // 1000] = bar(anchor // 1000)
        second, current2 = c.known_input_features(known, anchor)
        b = c.decide(anchor, [0, 0, 0, 0, 0, 0, 1], mapping(), current2)
        assert first == second and a == b
        assert c.mark(b, known[anchor // 1000])['status'] == 'SCORED'
if __name__ == '__main__':
    unittest.main()
