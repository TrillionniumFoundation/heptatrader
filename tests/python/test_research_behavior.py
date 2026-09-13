from __future__ import annotations

import copy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import hepta_eurusd_confirmed_momentum_strategy as strategy
import hepta_market_context_builder as builder
import hepta_shadow_market_history as history
import hepta_strategy_contracts as contracts
import hepta_strategy_replay_evaluator as replay

CONFIG_PATH = ROOT / "strategies/eurusd-confirmed-momentum-shadow-v2.json"


def seal(packet):
    packet.pop("body_sha256", None)
    packet["body_sha256"] = contracts.digest_document(packet)
    return packet


def evaluator_packet(config, package_digest):
    """Minimal evaluator input, not a claim of broker/capture qualification."""
    code = ROOT / "scripts"
    digests = {key + "_sha256": contracts.digest_file(code / filename) for key, filename in {
        "evaluator": "hepta_eurusd_confirmed_momentum_strategy.py",
        "builder": "hepta_market_context_builder.py",
        "normalizer": "hepta_market_evidence_normalizer.py",
        "contracts": "hepta_strategy_contracts.py",
    }.items()}
    packet = {key: {} for key in strategy.PACKET_FIELDS}
    packet.update({
        "schema": "hepta.market-information-packet.v1", "packet_id": "fixture-packet",
        "campaign_id": "fixture-campaign", "iteration": 1, "mode": "SHADOW",
        "created_at_ms": 1_800_000_000_000, "evaluated_at_ms": 1_800_000_000_000,
        "instrument": "EUR.USD",
        "strategy": {"strategy_id": config["strategy_id"], "strategy_version": config["strategy_version"],
                     "pinned_sha256": package_digest, "config_sha256": contracts.digest_file(CONFIG_PATH),
                     "sha256_verified": True, **digests},
        "context_builder": {"schema": "hepta.market-context-builder.v3",
                            "feature_calculation_version": config["feature_calculation_version"],
                            **{key: value for key, value in digests.items() if key != "evaluator_sha256"}},
        "source_snapshot": {"mutation_attempted": False, "direct_broker_access": False},
        "authority": {**{key: True for key in strategy.AUTHORITY_KEYS},
                      "paper_authorized": False, "live_authorized": False},
        "freshness": {"quote_fresh": True, "portfolio_freshness_provable": True,
                      "bar_fresh": True, "quote_observed_at_ms": 1_800_000_000_000},
        "provenance": {"information_provenance_present": True, "calendar_provenance_present": True},
        "market": {"bid": 1.09999, "ask": 1.10001, "mid": 1.1, "spread_bps": 0.2},
        "history": {"quote_provenance_provable": True, "quote_complete": True,
                    "bar_provenance_provable": True, "bar_complete": True,
                    "raw_quote_observations": 400, "resampled_quote_observations": 7,
                    "span_seconds": 5400, "bar_observations": 40},
        "features": {"window_return_bps": 10.0, "confirmation_return_bps": 4.0,
                     "ema_separation_bps": 2.0, "ema_fast_slope_bps": 1.0,
                     "step_volatility_bps": 1.0, "atr_bps": 4.0},
        "economic_calendar": {"present": True, "high_impact_event_window_active": False},
        "information": {"conflicts": []},
        "portfolio": {"active_order_count": 0, "position_quantity": 0.0, "gross_exposure": 0.0},
        "evidence_refs": ["sha256:" + "1" * 64],
    })
    return seal(packet)


class ResearchBehaviorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = strategy.load_strategy(CONFIG_PATH)
        cls.digest = strategy.strategy_package_digest(CONFIG_PATH)

    def packet(self):
        return evaluator_packet(self.config, self.digest)

    def evaluate(self, packet):
        return strategy.evaluate(packet, self.config, self.digest)

    def test_deterministic_positive_decision_is_shadow_only(self):
        packet = self.packet()
        first = self.evaluate(packet)
        self.assertEqual(first, self.evaluate(copy.deepcopy(packet)))
        self.assertEqual(first["decision"], "TRADE")
        self.assertEqual(first["final_outcome"], "SHADOW_TRADE")
        self.assertTrue(first["trade_intent"]["paper_only"])
        self.assertEqual(first["trade_intent"]["side"], "BUY")
        self.assertEqual(first["trade_intent"]["limit_price"], packet["market"]["ask"])
        self.assertIsNone(first["campaign_open_request_id"])
        self.assertFalse(packet["authority"]["paper_authorized"])

    def test_symmetric_sell_uses_authoritative_bid(self):
        packet = self.packet()
        for field in ("window_return_bps", "confirmation_return_bps", "ema_separation_bps", "ema_fast_slope_bps"):
            packet["features"][field] *= -1
        result = self.evaluate(seal(packet))
        self.assertEqual(result["trade_intent"]["side"], "SELL")
        self.assertEqual(result["trade_intent"]["limit_price"], packet["market"]["bid"])

    def test_vetoes_produce_no_trade_and_no_intent(self):
        cases = (
            ("freshness", "quote_fresh", False, "STALE_QUOTE"),
            ("history", "quote_complete", False, "INCOMPLETE_QUOTE_HISTORY"),
            ("economic_calendar", "high_impact_event_window_active", True, "HIGH_IMPACT_EVENT_WINDOW"),
            ("portfolio", "position_quantity", 1.0, "NONZERO_POSITION"),
            ("portfolio", "active_order_count", 1, "ACTIVE_ORDER_PRESENT"),
            ("information", "conflicts", ["conflict"], "CONFLICTING_INFORMATION"),
            ("market", "spread_bps", 3.0, "SPREAD_TOO_WIDE"),
            ("features", "confirmation_return_bps", -4.0, "MOMENTUM_DIRECTION_CONFLICT"),
        )
        for section, field, value, reason in cases:
            with self.subTest(reason=reason):
                packet = self.packet()
                packet[section][field] = value
                result = self.evaluate(seal(packet))
                self.assertEqual(result["decision"], "NO_TRADE")
                self.assertIn(reason, result["reason_codes"])
                self.assertIsNone(result["trade_intent"])

    def test_packet_tampering_and_code_binding_mismatch_are_rejected(self):
        packet = self.packet()
        packet["market"]["ask"] = 1.2
        with self.assertRaisesRegex(contracts.ContractError, "DIGEST_INVALID"):
            self.evaluate(packet)
        packet = self.packet()
        packet["strategy"]["evaluator_sha256"] = "sha256:" + "0" * 64
        with self.assertRaisesRegex(contracts.ContractError, "BINDING_INVALID"):
            self.evaluate(seal(packet))

    def test_authority_claim_cannot_be_promoted_by_resealing_packet(self):
        packet = self.packet()
        packet["authority"]["paper_authorized"] = True
        with self.assertRaisesRegex(contracts.ContractError, "AUTHORITY_INVALID"):
            self.evaluate(seal(packet))

    def test_resampling_never_uses_next_observation(self):
        config = {"feature_windows": {"quote_lookback_seconds": 2, "quote_resample_seconds": 1}}
        quotes = [{"observed_at_ms": time, "mid": value} for time, value in
                  ((0, 1.0), (999, 1.1), (1001, 100.0), (2000, 1.2))]
        metadata = {"provenance_provable": True, "maximum_gap_ms": 1000}
        sampled = builder._resample_quotes(quotes, metadata, config)
        self.assertEqual([q["observed_at_ms"] for q in sampled], [0, 999, 2000])
        self.assertEqual(sampled[1]["mid"], 1.1)
        quotes[2]["mid"] = 999999.0
        self.assertEqual(builder._resample_quotes(quotes, metadata, config), sampled)

    def test_resampling_rejects_gaps_instead_of_backfilling_from_future(self):
        config = {"feature_windows": {"quote_lookback_seconds": 2, "quote_resample_seconds": 1}}
        quotes = [{"observed_at_ms": time, "mid": 1.0} for time in (0, 1001, 2000)]
        with self.assertRaisesRegex(contracts.ContractError, "RESAMPLE_GAP"):
            builder._resample_quotes(quotes, {"provenance_provable": True, "maximum_gap_ms": 15}, config)

    def records(self, start=0):
        return [{"collection_started_at_ms": start + i * 10000, "sequence": i + 1,
                 "record_sha256": "sha256:" + format(i + 1, "064x"),
                 "quote": {"bid": 1.0 + i * 0.01, "ask": 1.02 + i * 0.01}}
                for i in range(7)]

    def bar(self, records, start=0):
        return history._quote_bar(records, started_at_ms=start, interval_ms=60000,
                                  cadence_ms=10000, maximum_jitter_ms=1000)

    def test_bar_uses_half_open_interval_and_reproducible_ohlc(self):
        records = self.records()
        records[-1]["quote"] = {"bid": 1000, "ask": 1002}
        bar = self.bar(records)
        self.assertTrue(bar["complete"])
        self.assertEqual(bar["sample_count"], 6)
        self.assertAlmostEqual(bar["open"], 1.01)
        self.assertAlmostEqual(bar["close"], 1.06)
        self.assertAlmostEqual(bar["high"], 1.06)
        self.assertEqual(bar, self.bar(records))
        history._validate_materialized_bar(bar)

    def test_incomplete_bar_and_tampered_bar_cannot_be_complete_evidence(self):
        bar = self.bar(self.records()[::2])
        self.assertFalse(bar["complete"])
        self.assertIn("CAPTURE_GAP_EXCEEDED", bar["reason_codes"])
        valid = self.bar(self.records())
        valid["close"] = 8.0
        with self.assertRaises(history.HistoryError):
            history._validate_materialized_bar(valid)
        aggregate = history._five_minute_bar({0: self.bar(self.records())}, started_at_ms=0)
        self.assertFalse(aggregate["complete"])
        self.assertIsNone(aggregate["close"])

    def test_replay_costs_use_correct_side_and_reject_unfilled_limit(self):
        mark = {"bid": 100.0, "ask": 101.0}
        buy = {"side": "BUY", "limit_price": 102.0, "expected_slippage": 0.0}
        sell = {"side": "SELL", "limit_price": 99.0, "expected_slippage": 0.0}
        entry, slip = replay._entry_price(mark, buy, entry_slippage_bps=10)
        exit_price, exit_slip = replay._exit_price(mark, "BUY", exit_slippage_bps=10)
        self.assertAlmostEqual(entry, 101.101)
        self.assertAlmostEqual(slip, 0.101)
        self.assertAlmostEqual(exit_price, 99.9)
        self.assertAlmostEqual(exit_price - entry, -1.201)
        short_entry, _ = replay._entry_price(mark, sell, entry_slippage_bps=10)
        short_exit, _ = replay._exit_price(mark, "SELL", exit_slippage_bps=10)
        self.assertAlmostEqual(short_entry - short_exit, -1.201)
        buy["limit_price"] = 101.0
        self.assertIsNone(replay._entry_price(mark, buy, entry_slippage_bps=10))
        self.assertAlmostEqual(replay._maximum_drawdown([10, -3, -8, 5]), 11)

    def test_replay_rejects_overlapping_intents_and_caps_holding(self):
        intent = {"observed_at_ms": 1000, "expires_at_ms": 2000, "max_holding_ms": 10000}
        receipts = [{"decision": "TRADE", "decision_id": str(i), "trade_intent": dict(intent)} for i in range(2)]
        with self.assertRaisesRegex(contracts.ContractError, "OVERLAPPING_INTENTS"):
            replay._reject_overlapping_intents(receipts, entry_latency_ms=0, maximum_holding_seconds=None)
        self.assertEqual(replay._effective_holding_ms(intent, 2), 2000)
        self.assertEqual(replay._effective_holding_ms(intent, 20), 10000)

    def test_immutable_history_publication_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "record.json"
            original = history.canonical_bytes({"record": 1})
            history._atomic_publish(path, original, mode=0o600)
            with self.assertRaises(history.HistoryError):
                history._atomic_publish(path, b"changed", mode=0o600)
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(list(path.parent.iterdir()), [path])

    def test_interrupted_empty_history_head_can_be_recovered_without_new_record(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / history.HEAD_NAME
            with mock.patch.object(history.os, "replace", side_effect=OSError("interrupted")):
                with self.assertRaises(history.HistoryError):
                    history._atomic_replace_head(path, history.canonical_bytes({"incomplete": True}))
            self.assertTrue((path.parent / history.HEAD_PENDING_NAME).exists())
            result = history.recover_history_head(path.parent, cadence_ms=10000, minimum_free_bytes=0)
            self.assertEqual(result["status"], "empty")
            self.assertEqual(result["record_count"], 0)
            self.assertFalse((path.parent / history.HEAD_PENDING_NAME).exists())
            self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
