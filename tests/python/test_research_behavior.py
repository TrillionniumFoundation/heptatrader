from __future__ import annotations
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts'))
import hepta_shadow_market_history as history
import hepta_strategy_replay_evaluator as replay
import hepta_eurusd_confirmed_momentum_strategy as strategy
from hepta_strategy_contracts import ContractError


class ResearchBehaviorTests(unittest.TestCase):
    def records(self,start=0):
        return [dict(collection_started_at_ms=start+i*1000,quote_read_finished_at_ms=start+i*1000+10,
                     quote=dict(bid=1+i/1000,ask=1.002+i/1000),sequence=start//1000+i+1,
                     record_sha256=hashlib.sha256(str(start+i).encode()).hexdigest()) for i in range(60)]
    def bar(self,rows,start=0):
        return history._quote_bar(rows,started_at_ms=start,interval_ms=60000,cadence_ms=1000,maximum_jitter_ms=100)
    def test_closed_bar_excludes_next_interval(self):
        rows=self.records();first=self.bar(rows)
        future=self.records(60000);future[0]['quote']={'bid':10000,'ask':10001}
        self.assertEqual(first,self.bar(rows+future))
        self.assertTrue(first['complete'])
    def test_quote_read_after_bar_close_cannot_leak_backwards(self):
        rows=self.records();rows[-1]['quote_read_finished_at_ms']=60001
        rows[-1]['quote']={'bid':999,'ask':1000}
        result=self.bar(rows)
        self.assertLess(result['high'],2)
        self.assertFalse(result['complete'])
        self.assertEqual(result['sample_count'],59)
    def test_capture_gap_cannot_be_filled_by_lookahead(self):
        result=self.bar(self.records()[:20]+self.records()[30:])
        self.assertFalse(result['complete'])
        self.assertIn('CAPTURE_GAP_EXCEEDED',result['reason_codes'])
    def test_five_minute_requires_all_complete_minute_bars(self):
        bars={start:self.bar(self.records(start),start) for start in range(0,300000,60000)}
        self.assertTrue(history._five_minute_bar(bars,started_at_ms=0)['complete'])
        del bars[120000]
        result=history._five_minute_bar(bars,started_at_ms=0)
        self.assertFalse(result['complete']);self.assertIsNone(result['close'])
    def test_costs_charge_touch_and_slippage_on_both_sides(self):
        mark={'bid':99,'ask':101}
        for side,limit in [('BUY',103),('SELL',97)]:
            price,slip=replay._entry_price(mark,dict(side=side,expected_slippage=0.5,limit_price=limit),entry_slippage_bps=10)
            self.assertEqual(slip,0.5)
            exit_price,_=replay._exit_price(mark,side,exit_slippage_bps=10)
            self.assertLess(exit_price-price if side=='BUY' else price-exit_price,0)
        self.assertIsNone(replay._entry_price(mark,dict(side='BUY',expected_slippage=1,limit_price=101),entry_slippage_bps=0))
    def test_overlapping_trade_intents_are_not_counted_as_independent(self):
        def receipt(name,stamp):
            return dict(decision='TRADE',decision_id=name,trade_intent=dict(observed_at_ms=stamp,expires_at_ms=stamp+100,max_holding_ms=1000))
        with self.assertRaises(ContractError):replay._reject_overlapping_intents([receipt('a',1000),receipt('b',1500)],entry_latency_ms=10,maximum_holding_seconds=None)
        replay._reject_overlapping_intents([receipt('a',1000),receipt('b',3000)],entry_latency_ms=10,maximum_holding_seconds=None)
    def test_drawdown_and_holding_are_conservative(self):
        self.assertEqual(replay._maximum_drawdown([10,-5,-10,3]),15)
        self.assertEqual(replay._effective_holding_ms({'max_holding_ms':10000},3),3000)
    def test_cost_veto_cannot_be_overridden_by_high_confidence(self):
        setup=dict(maximum_spread_bps=10,minimum_step_volatility_bps=0.1,maximum_step_volatility_bps=100,
                   estimated_slippage_bps=1,minimum_cost_multiple=2)
        packet=dict(market={'spread_bps':2},features=dict(step_volatility_bps=1,confirmation_return_bps=1,atr_bps=1),confidence=1)
        self.assertIn('EXPECTED_MOVE_INSUFFICIENT_FOR_COST',strategy._cost_reasons(packet,{'setup':setup}))
    def test_history_capacity_protects_future_head_commit(self):
        with tempfile.TemporaryDirectory() as d:
            args=dict(current_record_bytes=90,incoming_record_bytes=8,new_head_bytes=3,maximum_history_bytes=100,minimum_free_bytes=0)
            with self.assertRaises(history.HistoryError):history._guard_history_capacity(Path(d),**args)
            args['maximum_history_bytes']=1000;args['minimum_free_bytes']=100
            with patch.object(history.os,'statvfs',return_value=SimpleNamespace(f_bavail=110,f_frsize=1)):
                with self.assertRaises(history.HistoryError):history._guard_history_capacity(Path(d),**args)

if __name__=='__main__':unittest.main()
