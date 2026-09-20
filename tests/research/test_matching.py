from __future__ import annotations

from copy import deepcopy
from decimal import Decimal as D, localcontext
from pathlib import Path
import random
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]/"research"/"python"))
from hepta_research.matching import InstrumentSpec, OrderFlowReplay
from hepta_research.model import HypotheticalFill, ResearchLedger
from order_flow_fixture import cancel, events, mark, order


def engine(**kwargs):
    models = kwargs.pop('instruments', {'A': InstrumentSpec(1)})
    return OrderFlowReplay(models, capital=kwargs.pop('capital', 1000), currency='USD',
                           max_mark_age_us=kwargs.pop('max_mark_age_us', 100), **kwargs)


def state(e, oid):
    return next(x for x in e.report()['orders'] if x['order_id'] == oid)


class MatchingTests(unittest.TestCase):
    def test_price_priority_before_time(self):
        e = engine()
        e.apply(order(1, 'expensive', 'EXTERNAL', 'SELL', 2, 11))
        e.apply(order(2, 'cheap', 'EXTERNAL', 'SELL', 1, 10))
        result = e.apply(order(3, 'own', 'RESEARCH', 'BUY', 2, 12))
        self.assertEqual([(t['maker_id'], t['quantity'], t['price_ticks']) for t in result['trades']],
                         [('cheap', 1, 10), ('expensive', 1, 11)])

    def test_sell_walk_best_bid_first(self):
        e = engine()
        e.apply(order(1, 'low', 'EXTERNAL', 'BUY', 2, 9))
        e.apply(order(2, 'high', 'EXTERNAL', 'BUY', 2, 10))
        r = e.apply(order(3, 'sell', 'RESEARCH', 'SELL', 3, 8))
        self.assertEqual([t['price_ticks'] for t in r['trades']], [10, 9])

    def test_queue_ahead_consumed_once(self):
        e = engine()
        e.apply(order(1, 'front', 'EXTERNAL', 'BUY', 4, 10))
        e.apply(order(2, 'own', 'RESEARCH', 'BUY', 3, 10))
        r = e.apply(order(3, 's1', 'EXTERNAL', 'SELL', 5, 10, 'IOC'))
        self.assertEqual(len(r['fills']), 1)
        self.assertEqual(r['fills'][0]['delta'], 1)
        self.assertEqual(r['fills'][0]['liquidity'], 'MAKER')
        r = e.apply(order(4, 's2', 'EXTERNAL', 'SELL', 3, 10, 'IOC'))
        self.assertEqual(r['fills'][0]['delta'], 2)
        self.assertEqual(state(e, 'front')['filled'], 4)
        self.assertEqual(state(e, 's2')['canceled'], 1)

    def test_fifo_at_identical_price(self):
        e = engine()
        for i in range(1, 4):
            e.apply(order(i, str(i), 'EXTERNAL', 'SELL', 1, 10))
        r = e.apply(order(4, 'own', 'RESEARCH', 'BUY', 2, 10))
        self.assertEqual([t['maker_id'] for t in r['trades']], ['1', '2'])
        self.assertEqual(state(e, '3')['remaining'], 1)

    def test_gtc_remainder_rests(self):
        e = engine()
        e.apply(order(1, 'a', 'EXTERNAL', 'SELL', 1, 10))
        e.apply(order(2, 'own', 'RESEARCH', 'BUY', 3, 10))
        self.assertEqual((state(e, 'own')['filled'], state(e, 'own')['remaining']), (1, 2))
        e.apply(order(3, 'b', 'EXTERNAL', 'SELL', 1, 10))
        self.assertEqual((state(e, 'own')['filled'], state(e, 'own')['remaining']), (2, 1))

    def test_non_crossing_no_fill(self):
        e = engine()
        e.apply(order(1, 'a', 'EXTERNAL', 'SELL', 1, 11))
        r = e.apply(order(2, 'own', 'RESEARCH', 'BUY', 1, 10))
        self.assertEqual(r['fills'], [])
        self.assertEqual(e.report()['active_order_count'], 2)

    def test_ioc_partial_remainder_canceled(self):
        e = engine()
        e.apply(order(1, 'a', 'EXTERNAL', 'SELL', 2, 10))
        e.apply(order(2, 'own', 'RESEARCH', 'BUY', 5, 10, 'IOC'))
        self.assertEqual((state(e, 'own')['filled'], state(e, 'own')['canceled'], state(e, 'own')['remaining']), (2, 3, 0))

    def test_fak_alias_exactly_ioc(self):
        results = []
        for tif in ('FAK', 'IOC'):
            e = engine()
            e.apply(order(1, 'a', 'EXTERNAL', 'SELL', 2, 10))
            e.apply(order(2, 'own', 'RESEARCH', 'BUY', 5, 10, tif))
            results.append(e.report())
        self.assertEqual(*results)

    def test_fok_all_levels_or_none(self):
        e = engine()
        e.apply(order(1, 'a', 'EXTERNAL', 'SELL', 2, 10))
        e.apply(order(2, 'b', 'EXTERNAL', 'SELL', 2, 11))
        r = e.apply(order(3, 'fail', 'RESEARCH', 'BUY', 5, 11, 'FOK'))
        self.assertEqual(r['trades'], [])
        self.assertEqual(state(e, 'a')['remaining'], 2)
        self.assertEqual(state(e, 'b')['remaining'], 2)
        r = e.apply(order(4, 'pass', 'RESEARCH', 'BUY', 4, 11, 'FOK'))
        self.assertEqual(sum(t['quantity'] for t in r['trades']), 4)
        self.assertEqual(state(e, 'pass')['status'], 'FILLED')

    def test_fok_limit_not_just_total_depth(self):
        e = engine()
        e.apply(order(1, 'a', 'EXTERNAL', 'SELL', 2, 10))
        e.apply(order(2, 'b', 'EXTERNAL', 'SELL', 100, 11))
        e.apply(order(3, 'fail', 'RESEARCH', 'BUY', 3, 10, 'FOK'))
        self.assertEqual(state(e, 'fail')['reason'], 'FOK_INSUFFICIENT_LIQUIDITY')
        self.assertEqual(e.report()['trades'], [])

    def test_unpriced_ioc_walks_levels(self):
        e = engine()
        e.apply(order(1, 'a', 'EXTERNAL', 'SELL', 2, 10))
        e.apply(order(2, 'own', 'RESEARCH', 'BUY', 3, None, 'IOC'))
        self.assertEqual(state(e, 'own')['filled'], 2)
        self.assertEqual(state(e, 'own')['canceled'], 1)

    def test_unpriced_resting_order_rejected(self):
        e = engine()
        for tif in ['GTC', 'DAY']:
            with self.subTest(tif=tif), self.assertRaises(ValueError):
                e.apply(order(1, 'own', 'RESEARCH', 'BUY', 1, None, tif))
        self.assertEqual(e.report()['events'], [])

    def test_partial_cancel_retains_priority(self):
        e = engine()
        e.apply(order(1, 'first', 'RESEARCH', 'BUY', 3, 10))
        e.apply(order(2, 'second', 'EXTERNAL', 'BUY', 2, 10))
        e.apply(cancel(3, 'first', quantity=1))
        r = e.apply(order(4, 'sell', 'EXTERNAL', 'SELL', 3, 10, 'IOC'))
        self.assertEqual([(t['maker_id'], t['quantity']) for t in r['trades']], [('first', 2), ('second', 1)])
        self.assertEqual(state(e, 'first')['canceled'], 1)

    def test_cancel_all_and_terminal_noop(self):
        e = engine()
        e.apply(order(1, 'a', 'RESEARCH', 'BUY', 3, 10))
        e.apply(cancel(2, 'a'))
        r = e.apply(cancel(3, 'a'))
        self.assertTrue(r['event']['already_terminal'])
        self.assertEqual(e.report()['fills'], [])
        self.assertEqual(state(e, 'a')['canceled'], 3)

    def test_cancel_cannot_claim_other_actor(self):
        e = engine()
        e.apply(order(1, 'a', 'EXTERNAL', 'BUY', 1, 10))
        before = e.report()
        with self.assertRaises(ValueError):
            e.apply(cancel(2, 'a', actor='RESEARCH'))
        self.assertEqual(e.report(), before)

    def test_overcancel_and_unknown_cancel_atomic(self):
        e = engine()
        e.apply(order(1, 'a', 'RESEARCH', 'BUY', 1, 10))
        before = e.report()
        for request in [cancel(2, 'a', quantity=2), cancel(2, 'unknown')]:
            with self.assertRaises(ValueError):
                e.apply(request)
            self.assertEqual(e.report(), before)

    def test_session_end_only_expires_day(self):
        e = engine(instruments={'A': InstrumentSpec(1), 'B': InstrumentSpec(1)})
        e.apply(order(1, 'day-a', 'RESEARCH', 'BUY', 2, 10, 'DAY'))
        e.apply(order(2, 'gtc', 'RESEARCH', 'BUY', 2, 10))
        e.apply(order(3, 'day-b', 'RESEARCH', 'BUY', 2, 10, 'DAY', instrument='B'))
        e.apply({'kind': 'session_end', 'seq': 4, 'timestamp_us': 4, 'instrument': 'A'})
        self.assertEqual(state(e, 'day-a')['canceled'], 2)
        self.assertEqual(state(e, 'gtc')['remaining'], 2)
        self.assertEqual(state(e, 'day-b')['remaining'], 2)

    def test_self_trade_cancels_aggressor_not_maker(self):
        e = engine()
        e.apply(order(1, 'own-buy', 'RESEARCH', 'BUY', 3, 10))
        e.apply(order(2, 'own-sell', 'RESEARCH', 'SELL', 3, 10))
        self.assertEqual(e.report()['fills'], [])
        self.assertEqual(state(e, 'own-buy')['remaining'], 3)
        self.assertEqual(state(e, 'own-sell')['reason'], 'SELF_TRADE_PREVENTED')

    def test_partial_before_self_trade_then_cancels(self):
        e = engine()
        e.apply(order(1, 'ext', 'EXTERNAL', 'BUY', 2, 11))
        e.apply(order(2, 'own', 'RESEARCH', 'BUY', 3, 10))
        r = e.apply(order(3, 'sell', 'RESEARCH', 'SELL', 4, 10))
        self.assertEqual(len(r['fills']), 1)
        self.assertEqual(state(e, 'sell')['filled'], 2)
        self.assertEqual(state(e, 'sell')['canceled'], 2)
        self.assertEqual(state(e, 'own')['remaining'], 3)

    def test_fok_self_trade_preflight_keeps_external_liquidity(self):
        e = engine()
        e.apply(order(1, 'ext', 'EXTERNAL', 'BUY', 2, 11))
        e.apply(order(2, 'own', 'RESEARCH', 'BUY', 3, 10))
        e.apply(order(3, 'sell', 'RESEARCH', 'SELL', 4, 10, 'FOK'))
        self.assertEqual(e.report()['trades'], [])
        self.assertEqual(state(e, 'ext')['remaining'], 2)
        self.assertEqual(state(e, 'sell')['reason'], 'FOK_SELF_TRADE')

    def test_external_external_does_not_create_research_money(self):
        e = engine()
        e.apply(order(1, 'a', 'EXTERNAL', 'BUY', 2, 10))
        e.apply(order(2, 'b', 'EXTERNAL', 'SELL', 2, 10))
        self.assertEqual(len(e.report()['trades']), 1)
        self.assertEqual(e.report()['fills'], [])
        self.assertEqual(e.report()['final']['equity'], 1000)

    def test_liquidity_does_not_reappear(self):
        e = engine()
        e.apply(order(1, 'a', 'EXTERNAL', 'SELL', 2, 10))
        e.apply(order(2, 'own', 'RESEARCH', 'BUY', 2, 10))
        e.apply(order(3, 'retry', 'RESEARCH', 'BUY', 2, 10, 'IOC'))
        self.assertEqual(state(e, 'retry')['filled'], 0)

    def test_order_identity_reuse_after_terminal_rejected(self):
        e = engine()
        e.apply(order(1, 'a', 'RESEARCH', 'BUY', 1, 10, 'IOC'))
        before = e.report()
        with self.assertRaises(ValueError):
            e.apply(order(2, 'a', 'RESEARCH', 'BUY', 1, 10, 'IOC'))
        self.assertEqual(before, e.report())

    def test_same_timestamp_uses_sequence(self):
        e = engine()
        e.apply(order(1, 'own', 'RESEARCH', 'BUY', 1, 10, time=100))
        result = e.apply(order(2, 'ext', 'EXTERNAL', 'SELL', 1, 10, time=100))
        self.assertEqual(result['fills'][0]['liquidity'], 'MAKER')
        self.assertEqual(result['fills'][0]['timestamp_us'], 100)

    def test_global_sequence_and_time_cannot_regress(self):
        e = engine(instruments={'A': InstrumentSpec(1), 'B': InstrumentSpec(1)})
        e.apply(mark(3, 10, time=100))
        before = e.report()
        for bad in [mark(3, 10, instrument='B', time=100), mark(4, 10, time=99), mark(2, 10, time=101)]:
            with self.assertRaises(ValueError):
                e.apply(bad)
            self.assertEqual(before, e.report())

    def test_no_future_liquidity_retroactive_fok(self):
        e = engine()
        e.apply(order(1, 'own', 'RESEARCH', 'BUY', 2, 10, 'FOK'))
        e.apply(order(2, 'ext', 'EXTERNAL', 'SELL', 2, 10))
        self.assertEqual(e.report()['fills'], [])
        self.assertEqual(state(e, 'own')['filled'], 0)

    def test_missing_or_stale_mark_returns_null(self):
        e = engine(max_mark_age_us=1)
        e.apply(order(1, 'ext', 'EXTERNAL', 'SELL', 1, 10))
        e.apply(order(2, 'own', 'RESEARCH', 'BUY', 1, 10))
        self.assertIsNone(e.report()['final']['equity'])
        e.apply(mark(3, 11))
        self.assertEqual(e.report()['final']['equity'], 1001)
        e.apply({'kind': 'session_end', 'seq': 5, 'timestamp_us': 5, 'instrument': 'A'})
        self.assertIsNone(e.report()['final']['equity'])
        self.assertEqual(e.report()['final']['stale_instruments'], ['A'])
        e.apply(mark(6, 12))
        self.assertEqual(e.report()['final']['equity'], 1002)

    def test_flat_instrument_needs_no_mark(self):
        e = engine()
        self.assertEqual(e.report()['final']['equity'], 1000)
        self.assertTrue(e.report()['final']['valuation_complete'])

    def test_rebase_is_attribution_not_cash(self):
        e = engine()
        e.apply(order(1, 'ext', 'EXTERNAL', 'SELL', 2, 10))
        e.apply(order(2, 'own', 'RESEARCH', 'BUY', 2, 10))
        before = e.apply(mark(3, 12))['snapshot']
        after = e.apply(mark(4, 12, kind='basis_rebase'))
        self.assertEqual(after['event']['attribution_transfer'], 4)
        self.assertEqual(after['event']['cash_transfer'], 0)
        self.assertEqual(before['cash_inventory_balance'], after['snapshot']['cash_inventory_balance'])
        self.assertEqual(before['equity'], after['snapshot']['equity'])
        self.assertEqual(after['snapshot']['realized_pnl'], 4)

    def test_negative_price_fee_is_nonnegative(self):
        e = engine(instruments={'A': InstrumentSpec('.5', 2, 1, '.1', '.01')})
        e.apply(order(1, 'ext', 'EXTERNAL', 'SELL', 2, -10))
        r = e.apply(order(2, 'own', 'RESEARCH', 'BUY', 2, -10))
        self.assertEqual(r['fills'][0]['fee'], D('.4'))
        e.apply(mark(3, -8))
        self.assertEqual(e.report()['final']['equity'], D('1003.6'))

    def test_single_capital_multisymbol_fixture(self):
        e = engine(instruments={'A': InstrumentSpec('.5', 2, 1, '.1'), 'B': InstrumentSpec(1, 3, 1, '.2')})
        for event in events():
            e.apply(event)
        r = e.report()
        self.assertEqual(r['final']['equity'], D('1011.2'))
        self.assertEqual(r['final']['fees'], D('.8'))
        self.assertEqual(r['final']['gross_notional'], 230)
        self.assertEqual(r['final']['instruments']['A']['quantity'], 2)
        self.assertEqual(r['final']['instruments']['B']['quantity'], 2)
        self.assertEqual(r['final']['realized_pnl'], 6)
        self.assertEqual(r['final']['unrealized_pnl'], 6)

    def test_bad_event_variants_do_not_advance_state(self):
        base = order(1, 'a', 'RESEARCH', 'BUY', 2, 10)
        variants = [{'quantity': True}, {'quantity': 1.0}, {'quantity': -1}, {'quantity': 10**12+1},
                    {'limit_ticks': 1.0}, {'limit_ticks': True}, {'limit_ticks': 2**63},
                    {'seq': True}, {'timestamp_us': -1}, {'timestamp_us': 2**63},
                    {'actor': 'BROKER'}, {'actor': []}, {'side': 'buy'}, {'time_in_force': 'GTD'},
                    {'instrument': 'unknown'}, {'order_id': '../../secret'}, {'kind': 'snapshot'},
                    {'extra': 'not ignored'}]
        for changes in variants:
            e = engine()
            before = e.report()
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                e.apply({**base, **changes})
            self.assertEqual(before, e.report())
            e.apply(base)
            self.assertEqual(len(e.report()['orders']), 1)

    def test_lot_validation_and_cancel(self):
        e = engine(instruments={'A': InstrumentSpec(1, 1, 2)})
        with self.assertRaises(ValueError):
            e.apply(order(1, 'a', 'RESEARCH', 'BUY', 3, 10))
        e.apply(order(1, 'a', 'RESEARCH', 'BUY', 4, 10))
        with self.assertRaises(ValueError):
            e.apply(cancel(2, 'a', quantity=1))
        e.apply(cancel(2, 'a', quantity=2))
        self.assertEqual(state(e, 'a')['remaining'], 2)

    def test_event_bound(self):
        e = engine(max_events=1)
        e.apply(mark(1, 10))
        before = e.report()
        with self.assertRaises(ValueError):
            e.apply(mark(2, 11))
        self.assertEqual(before, e.report())

    def test_active_bound_is_atomic_and_consuming_frees_slot(self):
        e = engine(max_active_orders=1)
        e.apply(order(1, 'a', 'EXTERNAL', 'SELL', 1, 10))
        before = e.report()
        with self.assertRaises(ValueError):
            e.apply(order(2, 'b', 'RESEARCH', 'BUY', 1, 9))
        self.assertEqual(before, e.report())
        e.apply(order(2, 'b', 'RESEARCH', 'BUY', 2, 10))
        self.assertEqual(e.report()['active_order_count'], 1)

    def test_trade_bound_atomic_even_after_first_planned_fill(self):
        e = engine(max_trades=1)
        e.apply(order(1, 'a', 'EXTERNAL', 'SELL', 1, 10))
        e.apply(order(2, 'b', 'EXTERNAL', 'SELL', 1, 11))
        before = e.report()
        with self.assertRaises(ValueError):
            e.apply(order(3, 'own', 'RESEARCH', 'BUY', 2, 11))
        self.assertEqual(before, e.report())
        e.apply(order(3, 'own', 'RESEARCH', 'BUY', 1, 10))
        self.assertEqual(state(e, 'b')['remaining'], 1)

    def test_account_overflow_rolls_back_all_makers(self):
        e = engine(instruments={'A': InstrumentSpec(1, '1e18')}, capital=1)
        e.apply(order(1, 'a', 'EXTERNAL', 'SELL', 1, 1))
        e.apply(order(2, 'b', 'EXTERNAL', 'SELL', 1, 2))
        before = e.report()
        with self.assertRaises((ValueError, ArithmeticError)):
            e.apply(order(3, 'own', 'RESEARCH', 'BUY', 2, 2))
        self.assertEqual(before, e.report())

    def test_aggregate_overflow_rolls_back_last_instrument(self):
        e = engine(instruments={'A': InstrumentSpec(1, '1e6'), 'B': InstrumentSpec(1, '1e6')})
        e.apply(order(1, 'a', 'EXTERNAL', 'SELL', 600000000000, 1))
        e.apply(order(2, 'own-a', 'RESEARCH', 'BUY', 600000000000, 1))
        e.apply(order(3, 'b', 'EXTERNAL', 'SELL', 600000000000, 1, instrument='B'))
        before = e.report()
        with self.assertRaises(ValueError):
            e.apply(order(4, 'own-b', 'RESEARCH', 'BUY', 600000000000, 1, instrument='B'))
        self.assertEqual(before, e.report())

    def test_return_values_do_not_alias_internal_state(self):
        e = engine()
        result = e.apply(order(1, 'a', 'RESEARCH', 'BUY', 1, 10))
        result['event']['order']['remaining'] = 999
        report = e.report()
        report['orders'][0]['remaining'] = 888
        report['events'].clear()
        self.assertEqual(state(e, 'a')['remaining'], 1)
        self.assertEqual(len(e.report()['events']), 1)

    def test_low_decimal_context(self):
        def run():
            e = engine(instruments={'A': InstrumentSpec('.005', '12.345', 1, '.0123', '.0001')}, capital='1e7')
            e.apply(order(1, 'a', 'EXTERNAL', 'SELL', 123, 123456))
            e.apply(order(2, 'own', 'RESEARCH', 'BUY', 123, 123456))
            e.apply(mark(3, 123457))
            return e.report()
        normal = run()
        with localcontext() as context:
            context.prec = 4
            low = run()
        self.assertEqual(normal, low)

    def test_invalid_models_and_bounds(self):
        for kwargs in [{'instruments': {}}, {'instruments': {'A': InstrumentSpec(0)}},
                       {'instruments': {'A': InstrumentSpec(1, fee_rate=2)}},
                       {'instruments': {'A': InstrumentSpec(1, lot=True)}},
                       {'max_events': True}, {'max_trades': 0}, {'max_mark_age_us': -1},
                       {'max_active_orders': 0}]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                engine(**kwargs)

    def test_fixed_seed_independent_book_differential(self):
        for seed in range(12):
            rng, reference, trades = random.Random(seed), {}, []
            e = engine(capital=1000000)
            for seq in range(1, 151):
                request = order(seq, str(seq), rng.choice(['RESEARCH', 'EXTERNAL']), rng.choice(['BUY', 'SELL']),
                                rng.randint(1, 9), rng.choice([None, -3, -2, -1, 0, 1, 2, 3]),
                                rng.choice(['GTC', 'DAY', 'IOC', 'FAK', 'FOK']))
                if request['limit_ticks'] is None:
                    request['time_in_force'] = rng.choice(['IOC', 'FOK'])
                # Slow reference selects one best maker at a time using a list
                # of all surviving orders, rather than the engine's book plan.
                candidate = deepcopy(reference)
                row = {**request, 'remaining': request['quantity'], 'filled': 0, 'canceled': 0}
                tentative, blocked = [], False
                while row['remaining']:
                    eligible = [v for v in candidate.values() if v['remaining'] and v['side'] != row['side'] and
                        (row['limit_ticks'] is None or (v['limit_ticks'] <= row['limit_ticks'] if row['side'] == 'BUY' else v['limit_ticks'] >= row['limit_ticks']))]
                    if not eligible:
                        break
                    maker = min(eligible, key=lambda v: (v['limit_ticks'] if row['side'] == 'BUY' else -v['limit_ticks'], v['seq']))
                    if maker['actor'] == row['actor'] == 'RESEARCH':
                        blocked = True
                        break
                    qty = min(maker['remaining'], row['remaining'])
                    maker['remaining'] -= qty
                    maker['filled'] += qty
                    row['remaining'] -= qty
                    row['filled'] += qty
                    tentative.append((seq, maker['order_id'], row['order_id'], maker['limit_ticks'], qty))
                if request['time_in_force'] == 'FOK' and row['remaining']:
                    row.update(remaining=0, filled=0, canceled=row['quantity'])
                else:
                    reference = candidate
                    trades.extend(tentative)
                    if row['remaining'] and (request['time_in_force'] in ('IOC', 'FAK') or blocked):
                        row['canceled'], row['remaining'] = row['remaining'], 0
                reference[row['order_id']] = row
                e.apply(request)
            result = e.report()
            actual = [(t['event_seq'], t['maker_id'], t['taker_id'], t['price_ticks'], t['quantity']) for t in result['trades']]
            self.assertEqual(actual, trades, seed)
            for item in result['orders']:
                for key in ('filled', 'remaining', 'canceled'):
                    self.assertEqual(item[key], reference[item['order_id']][key], (seed, item['order_id'], key))
                self.assertEqual(item['quantity'], item['filled']+item['remaining']+item['canceled'])
            ledger = ResearchLedger(1000000)
            for item in result['fills']:
                ledger.fill(HypotheticalFill(item['timestamp_us'], item['delta'], item['price'], item['fee']))
            self.assertEqual(ledger.cash, result['final']['cash_inventory_balance'])
            self.assertEqual(ledger.quantity, result['final']['instruments']['A']['quantity'])


if __name__ == '__main__':
    unittest.main()
