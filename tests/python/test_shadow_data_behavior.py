"""Behavioral fixtures for temporal sampling, evidence and replay economics."""
from __future__ import annotations
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import hepta_strategy_contracts as contracts
import hepta_market_context_builder as context
import hepta_shadow_market_history as history
import hepta_strategy_replay_evaluator as replay


class ShadowDataBehaviorTests(unittest.TestCase):
    def test_duplicate_keys_and_nonfinite_json_fail_before_evaluation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "evidence.json"
            for body in ('{"x":1,"x":2}', '{"x":NaN}', '{"x":Infinity}',
                         '{"x":1e999}', '{"nested":{"x":-1e999}}'):
                with self.subTest(body=body):
                    path.write_text(body)
                    with self.assertRaises(contracts.ContractError):
                        contracts.load_document(path, "FIXTURE")

    def test_nonzero_underflow_is_not_silently_observed_zero(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "evidence.json"
            for number in ('1e-999', '-1e-999'):
                path.write_text('{"x":' + number + '}')
                with self.assertRaisesRegex(contracts.ContractError, "UNDERFLOW"):
                    contracts.load_document(path, "FIXTURE")
            path.write_text('{"x":0e-999,"y":1.25}')
            self.assertEqual(contracts.load_document(path, "FIXTURE"), {"x": 0, "y": 1.25})

    def test_number_contract_rejects_bool_nonfinite_and_huge_integer(self):
        for value in (True, float("nan"), float("inf"), -float("inf"), 10**1000):
            with self.subTest(kind=type(value).__name__):
                with self.assertRaises(contracts.ContractError):
                    contracts.require_number(value, "INVALID", minimum=0)
        self.assertEqual(contracts.require_number(0, "INVALID", minimum=0), 0)

    def test_evidence_hash_is_key_order_independent_but_value_sensitive(self):
        self.assertEqual(contracts.digest_document({"a": 1, "b": 2}),
                         contracts.digest_document({"b": 2, "a": 1}))
        self.assertNotEqual(contracts.digest_document({"a": 1}), contracts.digest_document({"a": 2}))

    def test_resampling_uses_last_observation_at_or_before_each_target(self):
        quotes = [{"observed_at_ms": t, "bid": index + 1} for index, t in
                  enumerate((10_000, 10_900, 12_500, 13_000))]
        config = {"feature_windows": {"quote_lookback_seconds": 3, "quote_resample_seconds": 1}}
        result = context._resample_quotes(quotes, {"provenance_provable": True, "maximum_gap_ms": 1600}, config)
        self.assertEqual([q["observed_at_ms"] for q in result], [10_000, 10_900, 10_900, 13_000])
        self.assertIs(result[-1], quotes[-1])

    def test_resampling_rejects_uncovered_history_instead_of_looking_ahead(self):
        quotes = [{"observed_at_ms": t} for t in (10_000, 13_000)]
        config = {"feature_windows": {"quote_lookback_seconds": 3, "quote_resample_seconds": 1}}
        with self.assertRaisesRegex(contracts.ContractError, "RESAMPLE_GAP"):
            context._resample_quotes(quotes, {"provenance_provable": True, "maximum_gap_ms": 1000}, config)

    def test_repeated_captures_are_not_independent_quote_updates(self):
        quotes = [{"quote_changed": True}, {"quote_changed": False}, {"quote_changed": True}]
        metadata = {"schema": "hepta.authoritative-quote-history.v3", "independent_quote_count": 2}
        self.assertEqual(context._independent_quotes(quotes, metadata), [quotes[0], quotes[2]])
        with self.assertRaisesRegex(contracts.ContractError, "CHANGE_COUNT"):
            context._independent_quotes(quotes, {**metadata, "independent_quote_count": 3})

    @staticmethod
    def records():
        return [{"collection_started_at_ms": t, "sequence": i+1,
                 "quote": {"bid": 100+i, "ask": 102+i},
                 "record_sha256": contracts.digest_document({"sequence": i+1})}
                for i, t in enumerate(range(0, 61_000, 1000))]

    def test_bar_excludes_next_interval_and_preserves_sample_source_digest(self):
        bar = history._quote_bar(self.records(), started_at_ms=0, interval_ms=60_000,
                                 cadence_ms=1000, maximum_jitter_ms=0)
        self.assertTrue(bar["complete"])
        self.assertEqual(bar["finished_at_ms"], 59_999)
        self.assertEqual(bar["sample_count"], 60)
        self.assertEqual((bar["open"], bar["close"], bar["low"], bar["high"]), (101, 160, 101, 160))
        history._validate_materialized_bar(bar)
        changed = {**bar, "close": 161}
        with self.assertRaisesRegex(history.HistoryError, "DIGEST"):
            history._validate_materialized_bar(changed)

    def test_bar_gap_is_incomplete_not_synthetic_interpolation(self):
        records = [record for record in self.records() if record["collection_started_at_ms"] != 30_000]
        bar = history._quote_bar(records, started_at_ms=0, interval_ms=60_000,
                                 cadence_ms=1000, maximum_jitter_ms=0)
        self.assertFalse(bar["complete"])
        self.assertIn("CAPTURE_GAP_EXCEEDED", bar["reason_codes"])

    def test_five_minute_bar_without_all_minutes_has_no_usable_prices(self):
        bar = history._quote_bar(self.records(), started_at_ms=0, interval_ms=60_000,
                                 cadence_ms=1000, maximum_jitter_ms=0)
        result = history._five_minute_bar({0: bar}, started_at_ms=0)
        self.assertFalse(result["complete"])
        self.assertIn("MISSING_ONE_MINUTE_BAR", result["reason_codes"])
        self.assertIsNone(result["close"])

    def test_replay_crosses_spread_and_charges_adverse_slippage(self):
        mark = {"bid": 100, "ask": 102}
        buy = {"side": "BUY", "limit_price": 103, "expected_slippage": 0.1}
        entry, _ = replay._entry_price(mark, buy, entry_slippage_bps=10)
        exit_price, _ = replay._exit_price(mark, "BUY", exit_slippage_bps=10)
        self.assertAlmostEqual(entry, 102.102)
        self.assertAlmostEqual(exit_price, 99.9)
        self.assertLess(exit_price - entry, -2)
        self.assertIsNone(replay._entry_price(mark, {**buy, "limit_price": 102}, entry_slippage_bps=10))

    def test_holding_bound_and_overlapping_intents_are_conservative(self):
        intent = {"observed_at_ms": 1000, "expires_at_ms": 2000, "max_holding_ms": 5000}
        self.assertEqual(replay._effective_holding_ms(intent, 2), 2000)
        receipt = {"decision": "TRADE", "decision_id": "one", "trade_intent": intent}
        with self.assertRaisesRegex(contracts.ContractError, "OVERLAPPING"):
            replay._reject_overlapping_intents([receipt, {**receipt, "decision_id": "two"}],
                                                entry_latency_ms=10, maximum_holding_seconds=None)
        self.assertEqual(replay._maximum_drawdown([10, -4, -8, 3]), 12)


if __name__ == "__main__":
    unittest.main()
